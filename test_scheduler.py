import unittest
from unittest.mock import patch

from main import ScheduledEvent, Simulation


class SchedulerScenarios(unittest.TestCase):
    def setUp(self):
        self.simulation = Simulation(driver_count=0, rider_count=0)
        self.calls = []

    def test_canceling_timeout_prevents_callback_and_is_repeatable(self):
        timeout = self.simulation.schedule(10, lambda: self.calls.append("expired"))
        self.assertIsInstance(timeout, ScheduledEvent)
        self.assertFalse(timeout.canceled)

        timeout.cancel()
        timeout.cancel()
        with patch("main.time.sleep") as sleep:
            self.simulation.advance_to(10)
            self.simulation.advance_to(20)

        self.assertTrue(timeout.canceled)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.simulation.current_time, 20)
        sleep.assert_not_called()

    def test_earlier_callback_can_cancel_a_pending_timeout(self):
        timeout = self.simulation.schedule(10, lambda: self.calls.append("expired"))
        self.simulation.schedule(2, timeout.cancel)
        self.simulation.schedule(12, lambda: self.calls.append(self.simulation.current_time))

        self.simulation.advance_to(20)

        self.assertEqual(self.calls, [12])
        self.assertTrue(timeout.canceled)
        self.assertEqual(self.simulation.current_time, 20)

    def test_equal_time_order_survives_cancellation_and_new_zero_delay_events(self):
        def first():
            self.calls.append(("first", self.simulation.current_time))
            self.simulation.schedule(0, lambda: self.calls.append(("nested", self.simulation.current_time)))

        self.simulation.schedule(10, first)
        canceled = self.simulation.schedule(10, lambda: self.calls.append("canceled"))
        self.simulation.schedule(10, lambda: self.calls.append(("second", self.simulation.current_time)))
        canceled.cancel()

        self.simulation.advance_to(10)

        self.assertEqual(self.calls, [("first", 10), ("second", 10), ("nested", 10)])

    def test_cancellation_at_the_same_time_respects_insertion_order(self):
        for cancel_first in (True, False):
            with self.subTest(cancel_first=cancel_first):
                simulation = Simulation(driver_count=0, rider_count=0)
                calls = []
                if cancel_first:
                    simulation.schedule(10, lambda: timeout.cancel())
                timeout = simulation.schedule(10, lambda: calls.append("fired"))
                if not cancel_first:
                    simulation.schedule(10, timeout.cancel)

                simulation.advance_to(10)

                self.assertEqual(calls, [] if cancel_first else ["fired"])

    def test_exact_times_and_events_beyond_the_horizon_are_preserved(self):
        self.simulation.advance_to(5)
        self.simulation.schedule(0, lambda: self.calls.append(self.simulation.current_time))
        self.simulation.schedule(1.5, lambda: self.calls.append(self.simulation.current_time))
        future = self.simulation.schedule(10, lambda: self.calls.append(self.simulation.current_time))
        self.assertEqual(self.calls, [])

        self.simulation.advance_to(7)
        self.assertEqual(self.calls, [5, 6.5])
        self.assertEqual(self.simulation.current_time, 7)
        self.assertFalse(future.canceled)
        self.simulation.advance_to(15)
        self.assertEqual(self.calls, [5, 6.5, 15])

    def test_canceling_an_executed_event_does_not_replay_or_undo_it(self):
        event = self.simulation.schedule(0, lambda: self.calls.append("fired"))
        self.simulation.advance_to(0)

        event.cancel()
        event.cancel()
        self.simulation.advance_to(10)

        self.assertEqual(self.calls, ["fired"])

    def test_invalid_times_leave_existing_events_intact(self):
        self.simulation.schedule(1, lambda: self.calls.append(self.simulation.current_time))
        with self.assertRaises(ValueError):
            self.simulation.schedule(-1, lambda: self.calls.append("invalid"))
        with self.assertRaises(ValueError):
            self.simulation.advance_to(-1)

        self.simulation.advance_to(1)

        self.assertEqual(self.calls, [1])


class OwnedEventScenarios(unittest.TestCase):
    def setUp(self):
        self.simulation = Simulation(driver_count=1, rider_count=1)
        self.driver = self.simulation.drivers[0].go_online((0, 1))
        self.driver.wait_for_order()
        self.rider = self.simulation.riders[0].start_session((0, 0), (3, 4))

    def test_ending_driver_session_cancels_its_work_without_affecting_other_owners(self):
        event = self.simulation.schedule(
            10, lambda: self.driver.driver.session.wait_for_order(), owner=self.driver
        )
        self.simulation.schedule(10, lambda: self.rider.search((0, 10)), owner=self.rider)

        self.driver.go_offline()
        replacement = self.driver.driver.go_online((1, 1))
        self.driver.go_offline()
        self.simulation.advance_to(10)

        self.assertTrue(event.canceled)
        self.assertEqual(self.driver.pending_events, set())
        self.assertEqual(self.driver.state, "offline")
        self.assertEqual(replacement.state, "online")
        self.assertEqual(self.rider.destination, (0, 10))
        self.assertEqual(self.rider.pending_events, set())

    def test_ending_rider_session_protects_its_replacement_from_old_actions(self):
        event = self.simulation.schedule(
            10, lambda: self.rider.rider.session.search((0, 10)), owner=self.rider
        )

        self.rider.go_offline()
        replacement = self.rider.rider.start_session((0, 0), (3, 4))
        self.rider.go_offline()
        self.simulation.advance_to(10)

        self.assertTrue(event.canceled)
        self.assertEqual(self.rider.pending_events, set())
        self.assertIs(self.rider.rider.session, replacement)
        self.assertEqual(replacement.destination, (3, 4))

    def test_finalizing_order_cancels_its_work_and_preserves_a_later_order(self):
        for state, reason in (("completed", None), ("canceled", "no drivers accepted")):
            with self.subTest(state=state):
                order = self.rider.make_order()
                event = self.simulation.schedule(
                    10,
                    lambda: self.simulation.finalize_order(
                        self.rider.current_order, "canceled", "stale action"
                    ),
                    owner=order,
                )

                self.simulation.finalize_order(order, state, reason)
                replacement = self.rider.make_order()
                self.driver.accept_order(replacement.pending_offer)
                self.simulation.finalize_order(order, state, reason)
                self.simulation.advance_to(self.simulation.current_time + 10)

                self.assertTrue(event.canceled)
                self.assertEqual(order.pending_events, set())
                self.assertEqual(self.simulation.active_orders, [replacement])
                self.assertIs(self.rider.current_order, replacement)
                self.simulation.finalize_order(replacement, "completed")

    def test_ending_rider_session_cancels_both_session_and_order_activity(self):
        order = self.rider.make_order()
        calls = []
        events = [
            self.simulation.schedule(10, lambda: calls.append("fired"), owner=owner)
            for owner in (self.rider, order)
        ]

        self.rider.go_offline()
        self.simulation.advance_to(10)

        self.assertEqual(calls, [])
        self.assertTrue(all(event.canceled for event in events))
        self.assertEqual(self.rider.pending_events, set())
        self.assertEqual(order.pending_events, set())
        self.assertEqual(order.cancellation_reason, "rider ended session")

    def test_session_termination_callback_cancels_equal_time_and_later_actions(self):
        ending = self.simulation.schedule(5, self.driver.go_offline, owner=self.driver)
        pending = [
            self.simulation.schedule(delay, self.fail, owner=self.driver)
            for delay in (5, 10)
        ]

        self.simulation.advance_to(20)

        self.assertFalse(ending.canceled)
        self.assertTrue(all(event.canceled for event in pending))
        self.assertEqual(self.driver.pending_events, set())
        self.assertEqual(self.driver.state, "offline")
        self.assertEqual(self.simulation.current_time, 20)

    def test_canceling_one_action_preserves_other_actions_and_releases_finished_handles(self):
        calls = []
        canceled = self.simulation.schedule(10, lambda: calls.append("canceled"), owner=self.rider)

        def follow_up():
            self.assertEqual(self.rider.pending_events, set())
            calls.append(self.simulation.current_time)
            self.simulation.schedule(2, lambda: calls.append(self.simulation.current_time), owner=self.rider)

        self.simulation.schedule(10, follow_up, owner=self.rider)
        canceled.cancel()
        canceled.cancel()
        self.simulation.advance_to(12)

        self.assertEqual(calls, [10, 12])
        self.assertEqual(self.rider.pending_events, set())

    def test_session_identity_and_status_are_rechecked_before_callback(self):
        for kind in ("driver", "rider"):
            for invalidation in ("replacement", "offline"):
                with self.subTest(kind=kind, invalidation=invalidation):
                    simulation = Simulation(driver_count=1, rider_count=1)
                    if kind == "driver":
                        session = simulation.drivers[0].go_online((0, 1))
                        registry = simulation.active_driver_sessions
                        actor_id = session.driver.id
                    else:
                        session = simulation.riders[0].start_session((0, 0), (3, 4))
                        registry = simulation.active_rider_sessions
                        actor_id = session.rider.id
                    calls = []
                    event = simulation.schedule(10, lambda: calls.append("stale"), owner=session)
                    # Bypass ordinary cleanup to exercise the execution-time guard itself.
                    if invalidation == "replacement":
                        registry[actor_id] = object()
                    else:
                        session.state = "offline"

                    simulation.advance_to(10)

                    self.assertEqual(calls, [])
                    self.assertTrue(event.canceled)
                    self.assertEqual(session.pending_events, set())

    def test_order_identity_and_status_are_rechecked_before_callback(self):
        for invalidation in ("replacement", "terminal", "unregistered", "rider ended", "driver ended"):
            with self.subTest(invalidation=invalidation):
                simulation = Simulation(driver_count=1, rider_count=1)
                simulation.drivers[0].go_online((0, 1)).wait_for_order()
                rider = simulation.riders[0].start_session((0, 0), (3, 4))
                order = rider.make_order()
                calls = []
                event = simulation.schedule(10, lambda: calls.append("stale"), owner=order)
                # Bypass ordinary cleanup to exercise the execution-time guard itself.
                if invalidation == "replacement":
                    rider.current_order = object()
                elif invalidation == "terminal":
                    order.state = "canceled"
                elif invalidation == "unregistered":
                    simulation.active_orders.remove(order)
                elif invalidation == "rider ended":
                    rider.state = "offline"
                else:
                    order.driver_session = simulation.drivers[0].session
                    order.driver_session.state = "offline"

                simulation.advance_to(10)

                self.assertEqual(calls, [])
                self.assertTrue(event.canceled)
                self.assertEqual(order.pending_events, set())

    def test_inactive_and_foreign_owners_cannot_schedule_activity(self):
        order = self.rider.make_order()
        other_simulation = Simulation(driver_count=1, rider_count=1)
        foreign_driver = other_simulation.drivers[0].go_online((0, 1))
        foreign_driver.wait_for_order()
        foreign_rider = other_simulation.riders[0].start_session((0, 0), (3, 4))
        foreign_order = foreign_rider.make_order()
        self.rider.go_offline()
        self.driver.go_offline()

        for owner in (self.driver, self.rider, order, foreign_driver, foreign_rider, foreign_order):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                self.simulation.schedule(0, self.fail, owner=owner)
            self.assertEqual(owner.pending_events, set())
        with self.assertRaises(TypeError):
            self.simulation.schedule(0, self.fail, owner=object())
        self.simulation.advance_to(10)

    def test_callback_failure_releases_handle_and_leaves_later_work_pending(self):
        def fail():
            raise RuntimeError("scripted failure")

        calls = []
        failed = self.simulation.schedule(2, fail, owner=self.rider)
        later = self.simulation.schedule(3, lambda: calls.append("later"), owner=self.rider)

        with self.assertRaisesRegex(RuntimeError, "scripted failure"):
            self.simulation.advance_to(10)

        self.assertEqual(self.simulation.current_time, 2)
        self.assertNotIn(failed, self.rider.pending_events)
        self.assertIn(later, self.rider.pending_events)
        self.simulation.advance_to(10)
        self.assertEqual(calls, ["later"])
        self.assertEqual(self.rider.pending_events, set())


if __name__ == "__main__":
    unittest.main()
