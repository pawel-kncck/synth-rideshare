import unittest
from unittest.mock import patch

from main import SearchResult, Simulation


class SearchScenarios(unittest.TestCase):
    def test_nearest_waiting_driver_determines_eta(self):
        simulation = Simulation(driver_count=4, rider_count=2)
        far = simulation.drivers[0].go_online((0, 3))
        far.wait_for_order()
        near = simulation.drivers[1].go_online((0, 1))
        near.wait_for_order()
        busy = simulation.drivers[2].go_online((0, 0))
        busy.wait_for_order()
        busy_rider = simulation.riders[1].start_session((0, 0), (3, 4))
        busy.accept_order(busy_rider.make_order().pending_offer)
        simulation.drivers[3].go_online((0, 0))

        rider = simulation.riders[0].start_session((0, 0), (3, 4))

        self.assertEqual(rider.search_result, SearchResult(5, 600, 9.5, 120, True))
        near.go_offline()
        self.assertEqual(rider.search((3, 4)).eta_seconds, 360)
        far.go_offline()
        self.assertEqual(rider.search((3, 4)), SearchResult(5, 600, 9.5, None, False))

    def test_no_drivers_still_returns_trip_quote(self):
        simulation = Simulation(driver_count=0, rider_count=1)

        rider = simulation.riders[0].start_session((0, 0), (3, 4))

        self.assertEqual(rider.search_result, SearchResult(5, 600, 9.5, None, False))
        self.assertIs(simulation.active_rider_sessions[rider.rider.id], rider)
        self.assertEqual(rider.state, "online")

    def test_searches_are_immediate_and_do_not_reserve_a_shared_driver(self):
        simulation = Simulation(driver_count=1, rider_count=2)
        driver = simulation.drivers[0].go_online((0, 1))
        driver.wait_for_order()
        simulation.advance_to(30)
        active_drivers = simulation.active_driver_sessions.copy()

        with patch.object(simulation, "schedule") as schedule, patch("main.time.sleep") as sleep:
            first = simulation.riders[0].start_session((0, 0), (3, 4))
            second = simulation.riders[1].start_session((0, 0), (3, 4))

        expected = SearchResult(5, 600, 9.5, 120, True)
        self.assertEqual(first.search_result, expected)
        self.assertEqual(second.search_result, expected)
        self.assertEqual(simulation.current_time, 30)
        self.assertEqual(simulation.active_driver_sessions, active_drivers)
        self.assertEqual(driver.state, "waiting for order")
        self.assertEqual(driver.location, (0, 1))
        self.assertEqual(simulation.active_orders, [])
        schedule.assert_not_called()
        sleep.assert_not_called()

    def test_waiting_preserves_snapshot_until_scheduled_search(self):
        simulation = Simulation(driver_count=1, rider_count=1)

        def make_driver_available():
            simulation.drivers[0].go_online((0, 1)).wait_for_order()

        with patch.object(simulation, "search", wraps=simulation.search) as search:
            rider = simulation.riders[0].start_session((0, 0), (3, 4))
            initial_result = rider.search_result
            simulation.schedule(10, make_driver_available)
            simulation.schedule(30, lambda: rider.search((3, 4)), owner=rider)

            simulation.advance_to(20)
            self.assertEqual(search.call_count, 1)
            self.assertIs(rider.search_result, initial_result)
            self.assertFalse(rider.search_result.drivers_available)

            simulation.advance_to(30)
            refreshed_result = rider.search_result
            self.assertEqual(search.call_count, 2)
            self.assertEqual(refreshed_result, SearchResult(5, 600, 9.5, 120, True))
            self.assertIsNot(refreshed_result, initial_result)
            self.assertFalse(initial_result.drivers_available)

            simulation.drivers[0].session.go_offline()
            simulation.advance_to(60)
            self.assertEqual(search.call_count, 2)
            self.assertIs(rider.search_result, refreshed_result)

            self.assertEqual(rider.search((3, 4)), SearchResult(5, 600, 9.5, None, False))

    def test_changing_destination_updates_quote_in_same_session(self):
        simulation = Simulation(driver_count=1, rider_count=1)
        simulation.drivers[0].go_online((0, 1)).wait_for_order()
        rider = simulation.riders[0].start_session((0, 0), (3, 4))
        initial_result = rider.search_result

        result = rider.search((0, 10))

        self.assertEqual(result, SearchResult(10, 1200, 17, 120, True))
        self.assertIs(result, rider.search_result)
        self.assertEqual(rider.destination, (0, 10))
        self.assertEqual(initial_result, SearchResult(5, 600, 9.5, 120, True))
        self.assertEqual(len(simulation.active_rider_sessions), 1)
        self.assertIs(simulation.active_rider_sessions[rider.rider.id], rider)

    def test_driver_at_pickup_has_zero_eta(self):
        simulation = Simulation(driver_count=1, rider_count=1)
        simulation.drivers[0].go_online((0, 0)).wait_for_order()

        rider = simulation.riders[0].start_session((0, 0), (3, 4))

        self.assertEqual(rider.search_result, SearchResult(5, 600, 9.5, 0, True))


if __name__ == "__main__":
    unittest.main()
