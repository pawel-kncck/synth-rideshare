import unittest
from unittest.mock import patch

from main import DriverSession, Order, RiderSession, SearchResult, Simulation


class RideScenarios(unittest.TestCase):
    def scenario(self, driver_location=(0, 1), pickup=(0, 0), destination=(3, 4), **settings):
        simulation = Simulation(driver_count=2, rider_count=2, **settings)
        driver = simulation.drivers[0].go_online(driver_location)
        driver.wait_for_order()
        rider = simulation.riders[0].start_session(pickup, destination)
        return simulation, driver, rider, rider.make_order()

    def assert_stage(self, simulation, driver, rider, order, states):
        self.assertEqual((rider.state, order.state, driver.state), states)
        self.assertIn(rider.state, RiderSession.states)
        self.assertIn(order.state, Order.states)
        self.assertIn(driver.state, DriverSession.states)
        self.assertIs(simulation.active_rider_sessions[rider.rider.id], rider)
        self.assertIs(simulation.active_driver_sessions[driver.driver.id], driver)
        self.assertIs(rider.current_order, order)
        self.assertIs(driver.current_order, order)
        self.assertIs(order.driver_session, driver)
        self.assertEqual(simulation.active_orders, [order])
        self.assertIsNone(order.pending_offer)
        self.assertIsNone(driver.pending_offer)
        self.assertEqual(order.pending_events, {order.ride_event})
        self.assertIs(order.ride_event.owner, order)

    def test_concrete_acceptance_timeline_and_coordinated_cleanup(self):
        simulation, driver, rider, order = self.scenario()
        self.assertEqual(rider.search_result, SearchResult(5, 600, 9.5, 120, True))
        self.assertEqual(order.created_at, 0)
        self.assertEqual(
            (rider.state, order.state, driver.state),
            ("waiting for driver acceptance", "waiting for driver to accept", "considering order"),
        )
        offer = order.pending_offer
        simulation.schedule(2, lambda: driver.accept_order(offer), owner=offer)
        with patch("main.time.sleep") as sleep:
            simulation.advance_to(2)
            pickup_event = order.ride_event
            self.assertEqual(order.accepted_at, 2)
            self.assertTrue(offer.timeout_event.canceled)
            for moment in (2, 10, 121.999):
                simulation.advance_to(moment)
                self.assert_stage(simulation, driver, rider, order, (
                    "waiting for pickup", "driver driving to pickup", "driving to pickup",
                ))
                self.assertEqual(driver.location, (0, 1))
                self.assertIsNone(order.pickup_arrived_at)

            simulation.advance_to(122)
            boarding_event = order.ride_event
            self.assertTrue(pickup_event.canceled)
            self.assertEqual(order.pickup_arrived_at, 122)
            self.assertEqual(driver.location, (0, 0))
            for moment in (122, 151.999):
                simulation.advance_to(moment)
                self.assert_stage(simulation, driver, rider, order, (
                    "driver has arrived", "driver waiting for rider", "waiting for rider",
                ))
                self.assertIsNone(order.boarded_at)

            simulation.advance_to(152)
            completion_event = order.ride_event
            self.assertTrue(boarding_event.canceled)
            self.assertEqual(order.boarded_at, 152)
            for moment in (152, 751.999):
                simulation.advance_to(moment)
                self.assert_stage(simulation, driver, rider, order, (
                    "riding", "driving with rider", "driving with rider",
                ))
                self.assertEqual(driver.location, (0, 0))
                self.assertEqual(rider.location, (0, 0))
                self.assertIsNone(order.completed_at)

            simulation.advance_to(752)
            sleep.assert_not_called()

        self.assertEqual((rider.state, order.state, driver.state), (
            "offline", "completed", "waiting for order",
        ))
        self.assertEqual(order.completed_at, 752)
        self.assertIsNone(order.canceled_at)
        self.assertIsNone(order.cancellation_reason)
        self.assertEqual(driver.location, (3, 4))
        self.assertEqual(rider.location, (3, 4))
        self.assertIsNone(rider.current_order)
        self.assertIsNone(driver.current_order)
        self.assertIsNone(rider.rider.session)
        self.assertIs(driver.driver.session, driver)
        self.assertIs(order.driver_session, driver)
        self.assertEqual(simulation.active_rider_sessions, {})
        self.assertEqual(simulation.active_driver_sessions, {driver.driver.id: driver})
        self.assertEqual(simulation.active_orders, [])
        self.assertEqual(simulation.order_history, [order])
        self.assertEqual(simulation.rider_session_history, [rider])
        self.assertEqual(simulation.driver_session_history, [])
        self.assertEqual(order.pending_events, set())
        self.assertTrue(completion_event.canceled)
        self.assertIsNone(order.ride_event)
        self.assertIsNone(order.ride_event_at)

    def test_same_driver_session_serves_a_second_rider_and_preserves_history(self):
        simulation, driver, rider, first = self.scenario()
        offer = first.pending_offer
        simulation.schedule(2, lambda: driver.accept_order(offer), owner=offer)
        simulation.advance_to(752)
        second_rider = simulation.riders[1].start_session((3, 4), (0, 0))
        self.assertEqual(second_rider.search_result.eta_seconds, 0)
        second = second_rider.make_order()
        offer = second.pending_offer
        simulation.schedule(1, lambda: driver.accept_order(offer), owner=offer)

        simulation.advance_to(1383)

        self.assertEqual(second.completed_at, 1383)
        self.assertEqual(simulation.order_history, [first, second])
        self.assertEqual(simulation.rider_session_history, [rider, second_rider])
        self.assertEqual(simulation.driver_session_history, [])
        self.assertEqual(simulation.active_driver_sessions, {driver.driver.id: driver})
        self.assertIs(driver.driver.session, driver)
        self.assertIs(second.driver_session, first.driver_session)
        self.assertEqual(driver.state, "waiting for order")
        self.assertEqual(driver.location, (0, 0))
        self.assertEqual(rider.location, (3, 4))
        self.assertEqual(first.pickup_location, (0, 0))
        self.assertEqual(first.destination, (3, 4))
        self.assertEqual((first.distance_km, first.duration_seconds, first.price), (5, 600, 9.5))
        self.assertEqual((first.created_at, first.accepted_at, first.pickup_arrived_at,
                          first.boarded_at, first.completed_at), (0, 2, 122, 152, 752))
        self.assertEqual(first.offers[0].state, "accepted")

    def test_actual_pickup_uses_acceptance_location_and_preserves_trip_quote(self):
        pickup, destination = [0, 0], [3, 4]
        simulation, driver, rider, order = self.scenario(pickup=pickup, destination=destination)
        quote = rider.search_result
        pickup[:] = [20, 20]
        destination[:] = [30, 30]
        driver.location = (0, 3)
        simulation.speed_kmh = 60
        simulation.price_per_km = 100
        simulation.advance_to(2)
        driver.accept_order(order.pending_offer)
        simulation.speed_kmh = 5

        simulation.advance_to(181.999)
        self.assertEqual(driver.location, (0, 3))
        self.assertIsNone(order.pickup_arrived_at)
        simulation.advance_to(182)
        self.assertEqual(driver.location, (0, 0))
        self.assertEqual(order.pickup_arrived_at, 182)
        simulation.advance_to(812)

        self.assertEqual(order.boarded_at, 212)
        self.assertEqual(order.completed_at, 812)
        self.assertEqual((order.duration_seconds, order.price), (600, 9.5))
        self.assertEqual(driver.location, (3, 4))
        self.assertEqual(rider.location, (3, 4))
        self.assertIs(rider.search_result, quote)
        self.assertEqual(quote.eta_seconds, 120)

    def test_zero_pickup_zero_trip_and_configurable_boarding_delays(self):
        for delay in (0, 7.5, 30):
            for destination, duration in (((0, 0), 0), ((3, 4), 600)):
                with self.subTest(delay=delay, destination=destination):
                    simulation, driver, rider, order = self.scenario(
                        driver_location=(0, 0), destination=destination,
                        boarding_delay_seconds=delay,
                    )
                    offer = order.pending_offer
                    simulation.schedule(2, lambda: driver.accept_order(offer), owner=offer)
                    simulation.advance_to(2)
                    self.assertEqual(order.pickup_arrived_at, 2)
                    if delay > 0:
                        self.assertEqual(rider.state, "driver has arrived")
                        self.assertIsNone(order.boarded_at)
                    simulation.advance_to(2 + delay + duration)

                    self.assertEqual(order.boarded_at, 2 + delay)
                    self.assertEqual(order.completed_at, 2 + delay + duration)
                    self.assertEqual(order.state, "completed")
                    self.assertEqual(driver.location, destination)
                    self.assertEqual(rider.location, destination)
                    self.assertEqual(simulation.order_history, [order])
                    self.assertEqual(order.pending_events, set())

    def test_invalid_boarding_delays_are_rejected(self):
        for delay in (-1, float("inf"), float("-inf"), float("nan")):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                Simulation(boarding_delay_seconds=delay)

    def test_busy_driver_is_excluded_from_search_through_every_ride_stage(self):
        simulation, driver, rider, order = self.scenario()
        observer = simulation.riders[1].start_session((0, 0), (3, 4))
        self.assertFalse(observer.search_result.drivers_available)
        driver.accept_order(order.pending_offer)

        for moment in (0, 120, 150, 749.999):
            simulation.advance_to(moment)
            self.assertFalse(observer.search((3, 4)).drivers_available)
        simulation.advance_to(750)
        self.assertEqual(observer.search((3, 4)).eta_seconds, 600)

    def test_offline_requests_are_rejected_through_every_accepted_stage(self):
        simulation, driver, rider, order = self.scenario()
        driver.accept_order(order.pending_offer)
        for moment in (0, 120, 150):
            simulation.advance_to(moment)
            event = order.ride_event
            for action in (driver.go_offline, rider.go_offline, driver.wait_for_order):
                with self.subTest(moment=moment, action=action), self.assertRaises(ValueError):
                    action()
                self.assertFalse(event.canceled)
                self.assertIs(rider.current_order, order)
                self.assertIs(driver.current_order, order)
        simulation.advance_to(750)
        driver.go_offline()
        rider.go_offline()
        self.assertEqual(simulation.driver_session_history, [driver])
        self.assertEqual(simulation.rider_session_history, [rider])

    def test_early_wrong_driver_foreign_and_duplicate_ride_actions_have_no_effect(self):
        simulation, driver, rider, order = self.scenario()
        other_driver = simulation.drivers[1].go_online((0, 1))
        other_driver.wait_for_order()
        foreign = Simulation(driver_count=0, rider_count=0)
        for action in (driver.arrive_at_pickup, driver.pick_up_rider, driver.end_ride):
            self.assertFalse(action(order))
            self.assertFalse(action(None))
        driver.accept_order(order.pending_offer)

        for moment in (0, 120, 150):
            simulation.advance_to(moment)
            state = (rider.state, order.state, driver.state)
            event = order.ride_event
            for actor in (driver, other_driver):
                for action in (actor.arrive_at_pickup, actor.pick_up_rider, actor.end_ride):
                    self.assertFalse(action(order))
            for action in (foreign.arrive_at_pickup, foreign.pick_up_rider, foreign.end_ride):
                self.assertFalse(action(driver, order))
            self.assertEqual((rider.state, order.state, driver.state), state)
            self.assertIs(order.ride_event, event)
            self.assertFalse(event.canceled)
        simulation.advance_to(750)
        for action in (driver.arrive_at_pickup, driver.pick_up_rider, driver.end_ride):
            self.assertFalse(action(order))
        self.assertEqual(simulation.order_history, [order])

    def test_direct_actions_at_due_times_cancel_the_automatic_duplicates(self):
        simulation, driver, rider, order = self.scenario()
        results = []
        for delay, action in ((120, driver.arrive_at_pickup),
                              (150, driver.pick_up_rider), (750, driver.end_ride)):
            simulation.schedule(delay, lambda action=action: results.append(action(order)))
        driver.accept_order(order.pending_offer)

        for moment in (120, 150, 750):
            event = order.ride_event
            simulation.advance_to(moment)
            self.assertTrue(event.canceled)
            self.assertFalse(event.callback())

        self.assertEqual(results, [True, True, True])
        self.assertEqual((order.pickup_arrived_at, order.boarded_at, order.completed_at), (120, 150, 750))
        self.assertEqual(simulation.order_history, [order])

    def test_callbacks_recheck_both_session_bindings_and_all_three_states(self):
        for stage_time, due_time in ((0, 120), (120, 150), (150, 750)):
            for invalidation in ("driver order", "rider order", "driver registry", "rider registry",
                                 "driver state", "rider state", "order state", "order registry"):
                with self.subTest(stage=stage_time, invalidation=invalidation):
                    simulation, driver, rider, order = self.scenario()
                    driver.accept_order(order.pending_offer)
                    simulation.advance_to(stage_time)
                    event = order.ride_event
                    locations = (driver.location, rider.location)
                    timestamps = (order.pickup_arrived_at, order.boarded_at, order.completed_at)
                    # Bypass cleanup to exercise the callback's own guards as well as ownership.
                    if invalidation == "driver order":
                        driver.current_order = object()
                    elif invalidation == "rider order":
                        rider.current_order = object()
                    elif invalidation == "driver registry":
                        simulation.active_driver_sessions[driver.driver.id] = object()
                    elif invalidation == "rider registry":
                        simulation.active_rider_sessions[rider.rider.id] = object()
                    elif invalidation == "driver state":
                        driver.state = "waiting for order"
                    elif invalidation == "rider state":
                        rider.state = "online"
                    elif invalidation == "order state":
                        order.state = "searching for a driver"
                    else:
                        simulation.active_orders.remove(order)

                    simulation.advance_to(due_time)

                    self.assertFalse(event.callback())
                    self.assertEqual((driver.location, rider.location), locations)
                    self.assertEqual((order.pickup_arrived_at, order.boarded_at, order.completed_at), timestamps)
                    self.assertEqual(simulation.order_history, [])
                    self.assertEqual(simulation.rider_session_history, [])

    def test_finalization_cancels_each_ride_stage_and_protects_replacement_sessions(self):
        for stage_time in (0, 120, 150):
            with self.subTest(stage=stage_time):
                simulation, driver, rider, order = self.scenario()
                driver.accept_order(order.pending_offer)
                simulation.advance_to(stage_time)
                event = order.ride_event
                # Low-level cleanup remains available; ordinary mid-ride offline is prohibited.
                simulation.finalize_order(order, "canceled", "scripted cleanup")
                rider.go_offline()
                driver.go_offline()
                new_driver = driver.driver.go_online((0, 1))
                new_driver.wait_for_order()
                new_rider = rider.rider.start_session((0, 0), (3, 4))
                replacement = new_rider.make_order()
                new_driver.accept_order(replacement.pending_offer)
                old_results = []
                simulation.schedule(1, lambda: old_results.append(event.callback()))

                simulation.advance_to(stage_time + 1)
                simulation.finalize_order(order, "completed")
                rider.go_offline()
                driver.go_offline()

                self.assertEqual(old_results, [False])
                self.assertTrue(event.canceled)
                self.assertEqual(order.pending_events, set())
                self.assertEqual(order.canceled_at, stage_time)
                self.assertIsNone(order.completed_at)
                self.assertEqual(order.cancellation_reason, "scripted cleanup")
                self.assertIs(new_driver.current_order, replacement)
                self.assertIs(new_rider.current_order, replacement)
                self.assertIs(driver.driver.session, new_driver)
                self.assertIs(rider.rider.session, new_rider)
                simulation.advance_to(stage_time + 750)
                self.assertEqual(simulation.order_history, [order, replacement])
                self.assertEqual(simulation.rider_session_history, [rider, new_rider])

    def test_completion_invalidates_rider_work_and_old_callbacks_during_a_later_ride(self):
        simulation, driver, rider, order = self.scenario()
        calls = []
        rider_event = simulation.schedule(751, lambda: calls.append("old rider"), owner=rider)
        driver_event = simulation.schedule(751, lambda: calls.append("driver"), owner=driver)
        driver.accept_order(order.pending_offer)
        events = [order.ride_event]
        simulation.advance_to(120)
        events.append(order.ride_event)
        simulation.advance_to(150)
        events.append(order.ride_event)
        simulation.advance_to(750)
        self.assertTrue(rider_event.canceled)
        self.assertFalse(driver_event.canceled)
        new_rider = rider.rider.start_session((3, 4), (0, 0))
        replacement = new_rider.make_order()
        driver.accept_order(replacement.pending_offer)

        simulation.advance_to(751)
        for event in events:
            self.assertFalse(event.callback())
        simulation.finalize_order(order, "canceled", "stale cancellation")
        simulation.finalize_order(order, "completed")
        rider.go_offline()

        self.assertEqual(calls, ["driver"])
        self.assertEqual(rider.pending_events, set())
        self.assertEqual(order.pending_events, set())
        self.assertEqual(order.completed_at, 750)
        self.assertIsNone(order.canceled_at)
        self.assertIsNone(order.cancellation_reason)
        self.assertEqual(simulation.order_history, [order])
        self.assertEqual(simulation.rider_session_history, [rider])
        self.assertIs(driver.current_order, replacement)
        self.assertIs(rider.rider.session, new_rider)
        self.assertEqual(new_rider.state, "driver has arrived")
        simulation.advance_to(1380)
        self.assertEqual(simulation.order_history, [order, replacement])

    def test_cancellation_timestamps_cover_expiry_stale_search_and_session_end(self):
        for cause, canceled_at in (("expired", 10), ("unavailable", 3), ("rider ended", 3)):
            with self.subTest(cause=cause):
                simulation = Simulation(driver_count=1, rider_count=1)
                driver = simulation.drivers[0].go_online((0, 1))
                driver.wait_for_order()
                rider = simulation.riders[0].start_session((0, 0), (3, 4))
                if cause == "unavailable":
                    driver.go_offline()
                    simulation.advance_to(3)
                order = rider.make_order()
                if cause == "expired":
                    simulation.advance_to(10)
                elif cause == "rider ended":
                    simulation.advance_to(3)
                    rider.go_offline()
                simulation.advance_to(20)
                simulation.finalize_order(order, "completed")

                self.assertEqual(order.state, "canceled")
                self.assertEqual(order.canceled_at, canceled_at)
                self.assertIsNone(order.accepted_at)
                self.assertIsNone(order.pickup_arrived_at)
                self.assertIsNone(order.boarded_at)
                self.assertIsNone(order.completed_at)
                self.assertIsNone(order.ride_event)
                self.assertEqual(simulation.order_history, [order])


if __name__ == "__main__":
    unittest.main()
