import unittest
from unittest.mock import patch

from main import RiderSession, Simulation


class OrderScenarios(unittest.TestCase):
    def setUp(self):
        self.simulation = Simulation(driver_count=2, rider_count=2)
        self.driver = self.simulation.drivers[0].go_online((0, 1))
        self.driver.wait_for_order()
        self.rider = self.simulation.riders[0].start_session((0, 0), (3, 4))

    def test_order_uses_latest_quote_and_registers_with_session(self):
        self.rider.search((0, 10))
        self.simulation.advance_to(30)

        order = self.rider.make_order()

        self.assertEqual(order.id, 1)
        self.assertIs(order.simulation, self.simulation)
        self.assertIs(order.rider_session, self.rider)
        self.assertIs(order.rider, self.rider.rider)
        self.assertIsNone(order.driver_session)
        self.assertEqual(order.pickup_location, (0, 0))
        self.assertEqual(order.destination, (0, 10))
        self.assertEqual(order.distance_km, 10)
        self.assertEqual(order.duration_seconds, 1200)
        self.assertEqual(order.price, 17)
        self.assertEqual(order.created_at, 30)
        self.assertEqual(order.state, "waiting for driver to accept")
        self.assertIsNone(order.cancellation_reason)
        self.assertIs(self.rider.current_order, order)
        self.assertEqual(self.simulation.active_orders, [order])
        self.assertEqual(self.simulation.order_history, [])

    def test_trip_snapshots_and_accepted_quote_survive_later_changes(self):
        pickup = [0, 0]
        destination = [3, 4]
        rider = self.simulation.riders[1].start_session(pickup, destination)
        quote = rider.search_result
        self.simulation.speed_kmh = 60
        self.simulation.base_fare = 20
        self.simulation.price_per_km = 15

        order = rider.make_order()
        pickup[:] = [10, 10]
        destination[:] = [20, 20]
        self.driver.location = (30, 30)

        self.assertEqual(order.pickup_location, (0, 0))
        self.assertEqual(order.destination, (3, 4))
        with self.assertRaises(TypeError):
            order.pickup_location[0] = 100
        with self.assertRaises(TypeError):
            order.destination[0] = 100
        self.assertEqual(order.distance_km, quote.distance_km)
        self.assertEqual(order.duration_seconds, quote.duration_seconds)
        self.assertEqual(order.price, quote.price)

        self.simulation.finalize_order(order, "completed")
        rider.search((0, 50))
        self.assertEqual(order.destination, (3, 4))
        self.assertEqual(order.price, 9.5)
        self.assertEqual(self.simulation.order_history, [order])

    def test_duplicate_order_and_search_are_rejected_without_changes(self):
        order = self.rider.make_order()
        quote = self.rider.search_result

        for action in (
            self.rider.make_order,
            lambda: self.simulation.create_order(self.rider),
            lambda: self.rider.search((10, 10)),
            lambda: self.simulation.search(self.rider),
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                action()

        self.assertEqual(self.rider.destination, (3, 4))
        self.assertIs(self.rider.search_result, quote)
        self.assertIs(self.rider.current_order, order)
        self.assertEqual(self.simulation.active_orders, [order])
        self.assertEqual(self.simulation.order_history, [])

    def test_unavailable_response_requires_another_search(self):
        self.driver.go_offline()
        self.rider.search((3, 4))

        with self.assertRaises(ValueError):
            self.rider.make_order()
        self.driver = self.driver.driver.go_online((0, 1))
        self.driver.wait_for_order()
        with self.assertRaises(ValueError):
            self.rider.make_order()
        self.assertIsNone(self.rider.current_order)
        self.assertEqual(self.simulation.active_orders, [])
        self.assertEqual(self.simulation.order_history, [])

        self.rider.search((3, 4))
        order = self.rider.make_order()
        self.assertEqual(order.id, 1)
        self.assertEqual(self.simulation.active_orders, [order])

    def test_active_session_without_a_search_cannot_order(self):
        rider = RiderSession(self.simulation.riders[1], (0, 0), (3, 4))
        self.simulation.register_rider_session(rider)

        with self.assertRaises(ValueError):
            rider.make_order()

        self.assertIsNone(rider.current_order)
        self.assertEqual(self.simulation.active_orders, [])
        self.assertEqual(self.simulation.order_history, [])

    def test_order_requires_the_active_session_in_the_same_simulation(self):
        unregistered = RiderSession(self.simulation.riders[1], (0, 0), (3, 4))
        unregistered.search_result = self.rider.search_result
        other_simulation = Simulation(driver_count=1, rider_count=2)
        other_rider = other_simulation.riders[0].start_session((0, 0), (3, 4))
        self.rider.go_offline()
        replacement = self.rider.rider.start_session((0, 0), (3, 4))

        for session in (unregistered, self.rider, other_rider):
            with self.subTest(session=session), self.assertRaises(ValueError):
                self.simulation.create_order(session)

        self.assertIsNone(replacement.current_order)
        self.assertEqual(self.simulation.active_orders, [])
        self.assertEqual(self.simulation.order_history, [])
        self.assertIs(self.simulation.active_rider_sessions[self.rider.rider.id], replacement)

    def test_stale_positive_quote_cancels_through_dispatch(self):
        for unavailable_state in ("offline", "driving to pickup", "online"):
            with self.subTest(unavailable_state=unavailable_state):
                simulation = Simulation(driver_count=1, rider_count=1)
                driver = simulation.drivers[0].go_online((0, 1))
                driver.wait_for_order()
                rider = simulation.riders[0].start_session((0, 0), (3, 4))
                quote = rider.search_result
                if unavailable_state == "offline":
                    driver.go_offline()
                    # An ended session's state alone cannot make it eligible.
                    driver.state = "waiting for order"
                else:
                    driver.state = unavailable_state
                simulation.advance_to(12)

                with patch.object(simulation, "dispatch_order", wraps=simulation.dispatch_order) as dispatch:
                    order = rider.make_order()

                dispatch.assert_called_once_with(order)
                self.assertEqual(order.state, "canceled")
                self.assertEqual(order.cancellation_reason, "no drivers accepted")
                self.assertEqual(order.created_at, 12)
                self.assertEqual(order.price, quote.price)
                self.assertEqual(order.duration_seconds, quote.duration_seconds)
                self.assertIsNone(order.driver_session)
                self.assertEqual(simulation.active_orders, [])
                self.assertEqual(simulation.order_history, [order])
                self.assertIsNone(rider.current_order)
                self.assertIs(simulation.active_rider_sessions[rider.rider.id], rider)
                self.assertEqual(rider.state, "online")
                self.assertFalse(rider.search((3, 4)).drivers_available)

    def test_dispatch_rechecks_availability_without_repricing(self):
        order = self.rider.make_order()
        self.driver.go_offline()
        self.simulation.price_per_km = 100

        self.simulation.dispatch_order(order)

        self.assertEqual(order.state, "canceled")
        self.assertEqual(order.cancellation_reason, "no drivers accepted")
        self.assertEqual(order.price, 9.5)
        self.assertEqual(self.simulation.order_history, [order])

    def test_terminal_cleanup_is_idempotent_and_preserves_replacement_order(self):
        for state, reason in (("completed", None), ("canceled", "no drivers accepted")):
            with self.subTest(state=state):
                order = self.rider.make_order()
                self.simulation.finalize_order(order, state, reason)
                self.assertIsNone(self.rider.current_order)
                self.assertNotIn(order, self.simulation.active_orders)

                self.rider.search((3, 4))
                replacement = self.rider.make_order()
                self.simulation.finalize_order(order, "canceled", "rider ended session")
                self.simulation.finalize_order(order, "completed")
                self.simulation.dispatch_order(order)

                self.assertEqual(order.state, state)
                self.assertEqual(order.cancellation_reason, reason)
                self.assertEqual(self.simulation.order_history.count(order), 1)
                self.assertIs(self.rider.current_order, replacement)
                self.assertEqual(self.simulation.active_orders, [replacement])
                self.simulation.finalize_order(replacement, "completed")

    def test_invalid_finalization_does_not_change_active_order(self):
        order = self.rider.make_order()
        for state, reason in (
            ("searching for a driver", None),
            ("canceled", None),
            ("completed", "no drivers accepted"),
        ):
            with self.subTest(state=state, reason=reason), self.assertRaises(ValueError):
                self.simulation.finalize_order(order, state, reason)
        other_simulation = Simulation(driver_count=0, rider_count=0)
        with self.assertRaises(ValueError):
            other_simulation.finalize_order(order, "completed")
        with self.assertRaises(ValueError):
            other_simulation.dispatch_order(order)

        self.assertEqual(order.state, "waiting for driver to accept")
        self.assertIsNone(order.cancellation_reason)
        self.assertIs(self.rider.current_order, order)
        self.assertEqual(self.simulation.active_orders, [order])
        self.assertEqual(self.simulation.order_history, [])
        self.assertEqual(other_simulation.order_history, [])

    def test_ending_session_cancels_and_archives_its_order_once(self):
        order = self.rider.make_order()

        self.rider.go_offline()
        replacement = self.rider.rider.start_session((0, 0), (3, 4))
        self.rider.go_offline()

        self.assertEqual(order.state, "canceled")
        self.assertEqual(order.cancellation_reason, "rider ended session")
        self.assertIsNone(self.rider.current_order)
        self.assertEqual(self.simulation.active_orders, [])
        self.assertEqual(self.simulation.order_history, [order])
        self.assertEqual(self.simulation.rider_session_history, [self.rider])
        self.assertIs(self.rider.rider.session, replacement)
        self.assertIs(self.simulation.active_rider_sessions[self.rider.rider.id], replacement)

    def test_order_ids_are_unique_across_sessions_and_local_to_simulation(self):
        first = self.rider.make_order()
        self.rider.go_offline()
        replacement = self.rider.rider.start_session((0, 0), (3, 4))
        second = replacement.make_order()
        self.simulation.drivers[1].go_online((0, 2)).wait_for_order()
        third = self.simulation.riders[1].start_session((0, 0), (3, 4)).make_order()
        other_simulation = Simulation(driver_count=1, rider_count=1)
        other_simulation.drivers[0].go_online((0, 1)).wait_for_order()
        other_order = other_simulation.riders[0].start_session((0, 0), (3, 4)).make_order()

        self.assertEqual([first.id, second.id, third.id], [1, 2, 3])
        self.assertEqual(other_order.id, 1)
        self.assertEqual(self.simulation.active_orders, [second, third])
        self.assertEqual(self.simulation.order_history, [first])


if __name__ == "__main__":
    unittest.main()


class AutomaticOrderScenarios(unittest.TestCase):
    def setUp(self):
        self.simulation = Simulation(driver_count=1, rider_count=1, order_delay_seconds=5)
        self.driver = self.simulation.drivers[0].go_online((0, 1))
        self.driver.wait_for_order()

    def test_order_is_created_after_the_delay_following_a_successful_search(self):
        rider = self.simulation.riders[0].start_session((0, 0), (3, 4))
        self.assertIsNotNone(rider.order_event)
        self.assertIn(rider.order_event, rider.pending_events)

        self.simulation.advance_to(4.999)
        self.assertIsNone(rider.current_order)
        self.simulation.advance_to(5)

        order = rider.current_order
        self.assertIsNotNone(order)
        self.assertEqual(order.created_at, 5)
        self.assertEqual(order.state, "waiting for driver to accept")
        self.assertIsNone(rider.order_event)
        self.assertEqual(rider.pending_events, set())

    def test_new_search_restarts_the_delay_and_no_drivers_means_no_order(self):
        rider = self.simulation.riders[0].start_session((0, 0), (3, 4))
        first_event = rider.order_event
        self.simulation.advance_to(3)
        rider.search((0, 10))
        self.assertTrue(first_event.canceled)

        self.simulation.advance_to(7.999)
        self.assertIsNone(rider.current_order)
        self.simulation.advance_to(8)
        self.assertEqual(rider.current_order.destination, (0, 10))

        self.driver.go_offline()   # cancels the pending order through dispatch
        self.assertIsNone(rider.current_order)
        rider.search((3, 4))
        self.assertIsNone(rider.order_event)
        self.simulation.advance_to(20)
        self.assertIsNone(rider.current_order)

    def test_going_offline_and_manual_orders_cancel_the_automatic_order(self):
        rider = self.simulation.riders[0].start_session((0, 0), (3, 4))
        event = rider.order_event
        manual = rider.make_order()
        self.simulation.advance_to(9)   # past the automatic order, before the offer expires
        self.assertIs(rider.current_order, manual)
        self.assertEqual(self.simulation.active_orders, [manual])
        self.assertIsNone(rider.order_event)
        self.assertNotIn(event, rider.pending_events)

        other = Simulation(driver_count=1, rider_count=1, order_delay_seconds=5)
        other.drivers[0].go_online((0, 1)).wait_for_order()
        leaving = other.riders[0].start_session((0, 0), (3, 4))
        leaving_event = leaving.order_event
        leaving.go_offline()
        other.advance_to(10)
        self.assertTrue(leaving_event.canceled)
        self.assertEqual(other.active_orders, [])
        self.assertEqual(other.order_history, [])

    def test_invalid_order_delays_are_rejected(self):
        for delay in (-1, float("inf"), float("nan")):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                Simulation(order_delay_seconds=delay)
        self.assertIsNone(Simulation().order_delay_seconds)
