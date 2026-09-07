import random
import unittest
from unittest.mock import patch

from demand import PeakPeriod, WeeklyDemandProfile
from main import SearchResult, Simulation
from metrics import aggregate_intervals, summarize_intervals
from scenarios.scenario_realistic_week import build_scenario


class DecisionTests(unittest.TestCase):
    def quote(self, price=10, eta=300, available=True):
        return SearchResult(5, 600, price, eta, available)

    def test_reference_rates_and_preference_directions(self):
        sim = Simulation()
        self.assertAlmostEqual(sim.rider_order_chance(self.quote()), .55)
        self.assertAlmostEqual(sim.driver_acceptance_chance(10, 300), .70)
        self.assertGreater(sim.rider_order_chance(self.quote(price=5)), .55)
        self.assertLess(sim.rider_order_chance(self.quote(price=20)), .55)
        self.assertLess(sim.driver_acceptance_chance(5, 300), .70)
        self.assertGreater(sim.driver_acceptance_chance(20, 300), .70)
        for eta, direction in ((0, self.assertGreater), (900, self.assertLess)):
            direction(sim.rider_order_chance(self.quote(eta=eta)), .55)
            direction(sim.driver_acceptance_chance(10, eta), .70)
        self.assertEqual(sim.rider_order_chance(self.quote(eta=None, available=False)), 0)
        for price, eta in ((1e100, 300), (10, 1e100)):
            for chance in (sim.rider_order_chance(self.quote(price, eta)),
                           sim.driver_acceptance_chance(price, eta)):
                self.assertTrue(0 <= chance <= 1)

    def test_zero_sensitivities_and_probability_endpoints(self):
        sim = Simulation(rider_price_sensitivity=0, driver_price_sensitivity=0,
                         rider_eta_sensitivity=0, driver_eta_sensitivity=0)
        self.assertAlmostEqual(sim.rider_order_chance(self.quote(1000, 9000)), .55)
        self.assertAlmostEqual(sim.driver_acceptance_chance(1000, 9000), .70)
        for rate in (0, 1):
            sim = Simulation(rider_order_probability=rate, driver_acceptance_probability=rate)
            self.assertEqual(sim.rider_order_chance(self.quote(1000, 9000)), rate)
            self.assertEqual(sim.driver_acceptance_chance(1000, 9000), rate)

    def test_invalid_parameters_fail_up_front(self):
        for name in ("rider_order_probability", "driver_acceptance_probability"):
            for value in (-.1, 1.1, True, None, "0.5", float("nan"), float("inf")):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                    Simulation(**{name: value})
        for name in ("rider_price_sensitivity", "driver_price_sensitivity",
                     "rider_eta_sensitivity", "driver_eta_sensitivity", "reference_price", "reference_eta_seconds"):
            for value in (-1, True, None, float("nan"), float("inf")):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                    Simulation(**{name: value})
        for name in ("reference_price", "reference_eta_seconds"):
            with self.assertRaisesRegex(ValueError, name):
                Simulation(**{name: 0})

    def start_trip(self, drivers=2, **kwargs):
        sim = Simulation(driver_count=drivers, rider_count=1, order_delay_seconds=0,
                         rider_order_probability=1, **kwargs)
        for index, driver_id in enumerate(sim.drivers):
            sim.schedule_driver_session(0, driver_id, (index + 1, 0))
        sim.schedule_rider_session(0, sim.riders[0], (0, 0), (3, 0))
        return sim

    def test_rider_can_leave_with_available_supply_and_without_an_order(self):
        sim = Simulation(driver_count=1, rider_count=1, rider_order_probability=0)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        sim.schedule_rider_session(0, sim.riders[0], (0, 0), (3, 0))
        sim.advance_to(30)
        self.assertEqual(sim.rider_session_history[0].exit_reason, "quote declined")
        self.assertEqual(sim.order_history, [])
        self.assertEqual(sim.active_orders, [])
        self.assertEqual(sim.active_driver_sessions[sim.drivers[0]].state, "waiting for order")
        totals = summarize_intervals(aggregate_intervals(sim.market_history, 0, 30))
        self.assertEqual(totals["declined_sessions"], 1)
        self.assertEqual(totals["session_to_order_pct"], 0)

    def test_no_supply_overrides_even_forced_conversion(self):
        sim = self.start_trip(drivers=0)
        sim.advance_to(30)
        self.assertEqual(sim.rider_session_history[0].exit_reason, "no drivers available")
        self.assertEqual(sim.order_history, [])

    def test_rejection_tries_next_driver_with_their_own_eta(self):
        sim = self.start_trip()
        with patch.object(sim._decision_rng, "random", side_effect=[.999, 0]) as draw:
            sim.advance_to(1000)
        self.assertEqual(draw.call_count, 2)  # expired callbacks never draw again
        order = sim.order_history[0]
        self.assertEqual(order.state, "completed")
        self.assertEqual([offer.state for offer in order.offers], ["rejected", "accepted"])
        self.assertEqual([offer.eta_seconds for offer in order.offers], [120, 240])
        self.assertGreater(order.offers[0].acceptance_probability, order.offers[1].acceptance_probability)
        self.assertEqual([offer.resolved_at for offer in order.offers], [3, 6])
        self.assertTrue(all(session.state == "waiting for order" for session in sim.active_driver_sessions.values()))

    def test_all_rejections_obey_offer_limit_and_release_drivers(self):
        sim = self.start_trip(drivers=6, driver_acceptance_probability=0)
        sim.advance_to(1000)
        order = sim.order_history[0]
        self.assertEqual(order.state, "canceled")
        self.assertEqual(len(order.offers), sim.max_offers_per_order)
        self.assertTrue(all(offer.state == "rejected" for offer in order.offers))
        self.assertEqual(sim.rider_session_history[0].exit_reason, "no drivers accepted")
        self.assertTrue(all(session.pending_order is None for session in sim.active_driver_sessions.values()))

    def test_timeout_and_shift_end_preempt_stale_decisions(self):
        sim = self.start_trip(accept_delay_seconds=10)
        with patch.object(sim._decision_rng, "random") as draw:
            sim.advance_to(100)
        draw.assert_not_called()
        self.assertTrue(all(offer.state == "expired" for offer in sim.order_history[0].offers))
        sim = Simulation(driver_count=2, rider_count=1, rider_order_probability=1,
                         driver_acceptance_probability=1, order_delay_seconds=0)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0), shift_seconds=1)
        sim.schedule_driver_session(0, sim.drivers[1], (1, 0))
        sim.schedule_rider_session(0, sim.riders[0], (0, 0), (3, 0))
        sim.advance_to(1000)
        self.assertEqual([offer.state for offer in sim.order_history[0].offers], ["canceled", "accepted"])

    def test_seeded_rates_at_reference_conditions(self):
        sim = Simulation(driver_count=1, rider_count=6000, speed_kmh=60, base_fare=2.5)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        for index, rider in enumerate(sim.riders):
            # $10, five-minute pickup, five-minute trip back to driver's start.
            sim.schedule_rider_session(index * 1000, rider, (5, 0), (0, 0))
        sim.advance_to(6000 * 1000)
        decisions = [e for e in sim.market_history if e["type"] == "rider_decision"]
        offers = [e for e in sim.market_history if e["type"] == "offer_resolved"]
        self.assertTrue(.53 < sum(e["ordered"] for e in decisions) / len(decisions) < .57)
        self.assertTrue(.67 < sum(e["state"] == "accepted" for e in offers) / len(offers) < .73)

    def test_scenario_reproducibility_is_independent_of_global_random(self):
        first = build_scenario(seed=17, rider_count=100)
        first.advance_to(169 * 3600)
        random.seed(987)
        random.random()
        second = build_scenario(seed=17, rider_count=100)
        second.advance_to(169 * 3600)
        self.assertEqual(first.session_schedule, second.session_schedule)
        self.assertEqual(first.market_history, second.market_history)
        self.assertFalse(first.active_orders)
        self.assertFalse(first.active_rider_sessions)
        self.assertFalse(first.active_driver_sessions)


class DemandTests(unittest.TestCase):
    def test_weekday_commutes_and_weekend_nights_have_exact_boundaries(self):
        demand = WeeklyDemandProfile()
        for day in range(5):
            for hour, weight in ((6.99, 1), (7, 2.5), (8.99, 2.5), (9, 1),
                                 (15.99, 1), (16, 2.8), (18.99, 2.8), (19, 1)):
                self.assertEqual(demand.weight_at(day, hour), weight)
        for day in (5, 6):
            self.assertEqual(demand.weight_at(day, 8), 1)
            self.assertEqual(demand.weight_at(day, 17), 1)
        for day, hour, weight in ((4, 2, 1), (4, 20.99, 1), (4, 21, 3), (5, 0, 3),
                                  (5, 2.99, 3), (5, 3, 1), (5, 21, 3), (6, 2, 3),
                                  (6, 3, 1), (6, 21, 1), (0, 2, 1)):
            self.assertEqual(demand.weight_at(day, hour), weight)

    def test_custom_cross_week_boundary_and_overlapping_peaks(self):
        demand = WeeklyDemandProfile((
            PeakPeriod("Sunday night", (6,), 23, 2, 4),
            PeakPeriod("Monday early", (0,), 1, 3, 2),
        ))
        self.assertEqual(demand.weight_at(0, 1), 4)
        self.assertEqual(demand.weight_at(0, 2), 2)
        self.assertEqual(demand.weight_at(0, 3), 1)

    def test_sampling_reproduces_peak_intensity_and_partial_hours(self):
        demand = WeeklyDemandProfile()
        times = demand.sample_session_times(30000, 3 * 3600, start_hour=6)
        counts = [sum(hour * 3600 <= t < (hour + 1) * 3600 for t in times) for hour in range(3)]
        self.assertTrue(2.35 < counts[1] / counts[0] < 2.65)
        self.assertTrue(2.35 < counts[2] / counts[0] < 2.65)
        times = demand.sample_session_times(10000, 3600, start_hour=6.5)
        before = sum(t < 1800 for t in times)
        self.assertTrue(2.3 < (len(times) - before) / before < 2.7)
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 <= t < 3600 for t in times))
        self.assertEqual(times, demand.sample_session_times(10000, 3600, start_hour=6.5))
        self.assertNotEqual(times, demand.sample_session_times(10000, 3600, start_hour=6.5, seed=1))
        for start in (.123456789, 6.01, 23.99):
            times = demand.sample_session_times(10, 7 * 86400, start_hour=start)
            self.assertTrue(all(0 <= t < 7 * 86400 for t in times))

    def test_invalid_demand_parameters(self):
        for days, start, end, multiplier in (((7,), 7, 9, 2), ((0,), 9, 9, 2),
                                            ((0,), 7.5, 9, 2), ((0,), 7, 9, .5)):
            with self.assertRaises(ValueError):
                PeakPeriod("Invalid", days, start, end, multiplier)
        for kwargs in ({"session_count": -1}, {"duration_seconds": 0},
                       {"start_hour": 24}, {"start_weekday": 7}):
            options = {"session_count": 1, "duration_seconds": 3600, **kwargs}
            with self.assertRaises(ValueError):
                WeeklyDemandProfile().sample_session_times(**options)


if __name__ == "__main__":
    unittest.main()
