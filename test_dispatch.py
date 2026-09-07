import unittest

from main import Simulation


class DispatchScenarios(unittest.TestCase):
    def scenario(self, locations=((0, 1), (0, 2)), rider_count=1):
        simulation = Simulation(driver_count=len(locations), rider_count=rider_count)
        drivers = [
            driver.go_online(location)
            for driver, location in zip(simulation.drivers, locations)
        ]
        for driver in drivers:
            driver.wait_for_order()
        riders = [rider.start_session((0, 0), (3, 4)) for rider in simulation.riders]
        return simulation, drivers, riders

    def assert_consistent(self, simulation):
        for driver in simulation.active_driver_sessions.values():
            self.assertFalse(driver.pending_offer is not None and driver.current_order is not None)
            if driver.pending_offer is not None:
                self.assertEqual(driver.state, "considering order")
                self.assertIs(driver.pending_offer.driver_session, driver)
                self.assertIs(driver.pending_offer.order.pending_offer, driver.pending_offer)
            if driver.current_order is not None:
                self.assertEqual(driver.state, "driving to pickup")
                self.assertIs(driver.current_order.driver_session, driver)
                self.assertIn(driver.current_order, simulation.active_orders)
        for order in simulation.active_orders + simulation.order_history:
            attempted = [offer.driver_session.driver.id for offer in order.offers]
            self.assertEqual(len(attempted), len(set(attempted)))
            self.assertEqual(set(attempted), order.attempted_driver_ids)
            self.assertLessEqual(len(attempted), 5)
            if order in simulation.active_orders:
                self.assertIs(order.rider_session.current_order, order)
            else:
                self.assertIsNone(order.pending_offer)
                self.assertEqual(order.pending_events, set())
            for offer in order.offers:
                if offer.state == "pending":
                    self.assertIs(order.pending_offer, offer)
                    self.assertIs(offer.driver_session.pending_offer, offer)
                else:
                    self.assertEqual(offer.pending_events, set())
                    self.assertIsNotNone(offer.resolved_at)

    def test_nearest_active_driver_is_reserved_with_id_tie_breaking(self):
        simulation, drivers, riders = self.scenario(((0, 3), (0, 1), (1, 0), (0, 0)))
        drivers[3].go_offline()
        # A historical session is excluded even if its state is misleading.
        drivers[3].state = "waiting for order"
        simulation.advance_to(7)

        order = riders[0].make_order()
        offer = order.pending_offer
        expected = min(drivers[1:3], key=lambda driver: driver.driver.id)

        self.assertIs(offer.driver_session, expected)
        self.assertEqual(offer.id, 1)
        self.assertIs(offer.order, order)
        self.assertEqual(offer.created_at, 7)
        self.assertEqual(offer.expires_at, 17)
        self.assertEqual(offer.state, "pending")
        self.assertIsNone(offer.resolved_at)
        self.assertIs(offer.timeout_event.owner, offer)
        self.assertEqual(offer.pending_events, {offer.timeout_event})
        self.assertEqual(order.state, "waiting for driver to accept")
        self.assertEqual(riders[0].state, "waiting for driver acceptance")
        self.assertIsNone(order.driver_session)
        self.assertIsNone(expected.current_order)
        self.assert_consistent(simulation)

    def test_acceptance_binds_sessions_and_survives_former_timeout(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        offer = order.pending_offer
        results = []
        simulation.schedule(2, lambda: results.append(drivers[0].accept_order(offer)), owner=offer)

        simulation.advance_to(2)

        self.assertEqual(results, [True])
        self.assertEqual(offer.state, "accepted")
        self.assertEqual(offer.resolved_at, 2)
        self.assertTrue(offer.timeout_event.canceled)
        self.assertEqual(offer.pending_events, set())
        self.assertIsNone(order.pending_offer)
        self.assertIsNone(drivers[0].pending_offer)
        self.assertIs(order.driver_session, drivers[0])
        self.assertIs(drivers[0].current_order, order)
        self.assertEqual(order.accepted_at, 2)
        self.assertEqual(order.state, "driver driving to pickup")
        self.assertEqual(drivers[0].state, "driving to pickup")
        self.assertEqual(riders[0].state, "waiting for pickup")
        simulation.advance_to(20)
        self.assertFalse(offer.timeout_event.callback())
        self.assertEqual(order.state, "driver driving to pickup")
        self.assertEqual(drivers[0].location, (0, 1))
        self.assertEqual(len(order.offers), 1)
        self.assert_consistent(simulation)

    def test_rejection_then_acceptance_preserves_quote(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        first = order.pending_offer
        simulation.price_per_km = 100
        simulation.speed_kmh = 100
        simulation.schedule(2, lambda: drivers[0].reject_order(first), owner=first)
        simulation.advance_to(2)
        second = order.pending_offer

        self.assertEqual(first.state, "rejected")
        self.assertTrue(first.timeout_event.canceled)
        self.assertEqual(first.resolved_at, 2)
        self.assertEqual(drivers[0].state, "waiting for order")
        self.assertIs(second.driver_session, drivers[1])
        self.assertEqual(second.id, 2)
        self.assertEqual((second.created_at, second.expires_at), (2, 12))
        simulation.schedule(1, lambda: drivers[1].accept_order(second), owner=second)
        simulation.advance_to(20)

        self.assertEqual(order.accepted_at, 3)
        self.assertEqual(order.price, 9.5)
        self.assertEqual(order.duration_seconds, 600)
        self.assertFalse(drivers[0].accept_order(first))
        self.assertFalse(drivers[0].reject_order(first))
        self.assertFalse(first.timeout_event.callback())
        self.assert_consistent(simulation)

    def test_expiration_dispatches_next_offer_at_the_exact_deadline(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        first = order.pending_offer
        simulation.advance_to(9)
        self.assertFalse(first.timeout_event.callback())
        self.assertIs(order.pending_offer, first)

        simulation.advance_to(10)
        second = order.pending_offer

        self.assertEqual(first.state, "expired")
        self.assertEqual(first.resolved_at, 10)
        self.assertEqual(first.pending_events, set())
        self.assertEqual(drivers[0].state, "waiting for order")
        self.assertIs(second.driver_session, drivers[1])
        self.assertEqual((second.created_at, second.expires_at), (10, 20))
        simulation.schedule(1, lambda: drivers[1].accept_order(second), owner=second)
        simulation.advance_to(20)
        self.assertEqual(order.accepted_at, 11)
        self.assert_consistent(simulation)

    def test_exhaustion_with_fewer_than_five_drivers_allows_a_fresh_search(self):
        for outcome in ("reject", "expire"):
            with self.subTest(outcome=outcome):
                simulation, drivers, riders = self.scenario(((0, 1), (0, 2), (0, 3)))
                rider = riders[0]
                quote = rider.search_result
                order = rider.make_order()
                for driver in drivers:
                    offer = order.pending_offer
                    self.assertIs(offer.driver_session, driver)
                    if outcome == "reject":
                        simulation.schedule(1, lambda offer=offer: offer.driver_session.reject_order(offer))
                        simulation.advance_to(simulation.current_time + 1)
                    else:
                        simulation.advance_to(offer.expires_at)

                self.assertEqual(order.state, "canceled")
                self.assertEqual(order.cancellation_reason, "no drivers accepted")
                self.assertEqual(len(order.offers), 3)
                self.assertEqual(simulation.active_orders, [])
                self.assertEqual(simulation.order_history, [order])
                self.assertIsNone(rider.current_order)
                self.assertEqual(rider.state, "online")
                self.assertIs(rider.search_result, quote)
                self.assertTrue(rider.search((3, 4)).drivers_available)
                self.assert_consistent(simulation)

    def test_five_total_offers_is_a_strict_limit_with_more_supply(self):
        for outcome in ("reject", "expire", "mixed"):
            with self.subTest(outcome=outcome):
                simulation, drivers, riders = self.scenario(tuple((0, i) for i in range(1, 8)))
                order = riders[0].make_order()
                for index in range(5):
                    offer = order.pending_offer
                    self.assertIs(offer.driver_session, drivers[index])
                    if outcome == "reject" or (outcome == "mixed" and index % 2 == 0):
                        self.assertTrue(drivers[index].reject_order(offer))
                    else:
                        simulation.advance_to(offer.expires_at)

                self.assertEqual(order.state, "canceled")
                self.assertEqual(order.cancellation_reason, "no drivers accepted")
                self.assertEqual(len(order.offers), 5)
                self.assertEqual(order.attempted_driver_ids, {driver.driver.id for driver in drivers[:5]})
                self.assertEqual([driver.state for driver in drivers], ["waiting for order"] * 7)
                simulation.advance_to(100)
                self.assertEqual(simulation.order_history, [order])
                self.assert_consistent(simulation)

    def test_fifth_offer_can_still_be_accepted(self):
        simulation, drivers, riders = self.scenario(tuple((0, i) for i in range(1, 7)))
        order = riders[0].make_order()
        for driver in drivers[:4]:
            self.assertTrue(driver.reject_order(order.pending_offer))

        self.assertTrue(drivers[4].accept_order(order.pending_offer))
        simulation.advance_to(20)

        self.assertEqual(len(order.offers), 5)
        self.assertIs(order.driver_session, drivers[4])
        self.assertEqual(drivers[5].state, "waiting for order")
        self.assert_consistent(simulation)

    def test_simultaneous_orders_cannot_reserve_the_same_driver(self):
        simulation, drivers, riders = self.scenario(((0, 1),), rider_count=2)
        orders = []
        for rider in riders:
            simulation.schedule(0, lambda rider=rider: orders.append(rider.make_order()))

        simulation.advance_to(0)
        first, second = orders

        self.assertEqual(first.state, "waiting for driver to accept")
        self.assertEqual(second.state, "canceled")
        self.assertEqual(second.cancellation_reason, "no drivers accepted")
        self.assertEqual(second.offers, [])
        self.assertIs(drivers[0].pending_offer, first.pending_offer)
        self.assertFalse(riders[1].search((3, 4)).drivers_available)
        drivers[0].reject_order(first.pending_offer)
        self.assertTrue(riders[1].search((3, 4)).drivers_available)
        replacement = riders[1].make_order()
        self.assertIs(replacement.pending_offer.driver_session, drivers[0])
        self.assert_consistent(simulation)

    def test_acceptance_at_or_after_deadline_is_invalid_in_both_insertion_orders(self):
        for delay in (9.999, 10, 10.001, 20):
            for response_first in (True, False):
                with self.subTest(delay=delay, response_first=response_first):
                    simulation, drivers, riders = self.scenario(((0, 1),))
                    results = []

                    def respond():
                        results.append(drivers[0].accept_order(offer))

                    if response_first:
                        simulation.schedule(delay, respond)
                    order = riders[0].make_order()
                    offer = order.pending_offer
                    if not response_first:
                        simulation.schedule(delay, respond)
                    simulation.advance_to(20)

                    accepted = delay < 10
                    self.assertEqual(results, [accepted])
                    self.assertEqual(offer.state, "accepted" if accepted else "expired")
                    self.assertEqual(order.state, "driver driving to pickup" if accepted else "canceled")
                    self.assertEqual(order.accepted_at, delay if accepted else None)
                    self.assert_consistent(simulation)

    def test_wrong_driver_foreign_and_duplicate_responses_have_no_effect(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        offer = order.pending_offer
        foreign = Simulation(driver_count=0, rider_count=0)

        self.assertFalse(drivers[1].accept_order(offer))
        self.assertFalse(drivers[1].reject_order(offer))
        self.assertFalse(foreign.accept_offer(drivers[0], offer))
        self.assertFalse(foreign.reject_offer(drivers[0], offer))
        self.assertFalse(drivers[0].accept_order(None))
        self.assertFalse(drivers[0].reject_order(None))
        self.assertIs(order.pending_offer, offer)
        self.assertFalse(offer.timeout_event.canceled)
        self.assertTrue(drivers[0].accept_order(offer))
        self.assertFalse(drivers[0].accept_order(offer))
        self.assertFalse(drivers[0].reject_order(offer))
        self.assertFalse(offer.timeout_event.callback())
        self.assert_consistent(simulation)

    def test_rejection_at_deadline_cannot_preempt_expiration(self):
        for response_first in (True, False):
            with self.subTest(response_first=response_first):
                simulation, drivers, riders = self.scenario()
                results = []

                def reject():
                    results.append(drivers[0].reject_order(offer))

                if response_first:
                    simulation.schedule(10, reject)
                order = riders[0].make_order()
                offer = order.pending_offer
                if not response_first:
                    simulation.schedule(10, reject)

                simulation.advance_to(10)

                self.assertEqual(results, [False])
                self.assertEqual(offer.state, "expired")
                self.assertEqual(offer.resolved_at, 10)
                self.assertIs(order.pending_offer.driver_session, drivers[1])
                self.assert_consistent(simulation)

    def test_eligibility_requires_no_offer_or_accepted_order_even_if_state_says_waiting(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                simulation, drivers, riders = self.scenario(rider_count=2)
                first = riders[0].make_order()
                if accepted:
                    drivers[0].accept_order(first.pending_offer)
                original_state = drivers[0].state
                # Exercise the reference checks independently of the state check.
                drivers[0].state = "waiting for order"

                quote = riders[1].search((3, 4))
                second = riders[1].make_order()

                self.assertEqual(quote.eta_seconds, 240)
                self.assertIs(second.pending_offer.driver_session, drivers[1])
                drivers[0].state = original_state
                self.assert_consistent(simulation)

    def test_dispatch_reevaluates_new_sessions_and_current_distances(self):
        simulation, drivers, riders = self.scenario(((0, 1), (0, 2), (0, 3)))
        drivers[1].go_offline()
        drivers[2].go_offline()
        order = riders[0].make_order()
        farther = drivers[1].driver.go_online((0, 4))
        nearer = drivers[2].driver.go_online((0, 0.5))
        farther.wait_for_order()
        nearer.wait_for_order()

        drivers[0].reject_order(order.pending_offer)

        self.assertIs(order.pending_offer.driver_session, nearer)
        nearer.reject_order(order.pending_offer)
        self.assertIs(order.pending_offer.driver_session, farther)
        self.assert_consistent(simulation)

    def test_driver_leaving_releases_offer_and_reopened_actor_is_not_retried(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        first = order.pending_offer
        simulation.schedule(2, drivers[0].go_offline, owner=drivers[0])
        simulation.advance_to(2)
        second = order.pending_offer
        replacement = drivers[0].driver.go_online((0, 0))
        replacement.wait_for_order()

        self.assertEqual(first.state, "canceled")
        self.assertEqual(first.resolved_at, 2)
        self.assertTrue(first.timeout_event.canceled)
        self.assertIsNone(drivers[0].pending_offer)
        self.assertEqual(drivers[0].state, "offline")
        self.assertEqual((second.created_at, second.expires_at), (2, 12))
        self.assertIs(second.driver_session, drivers[1])
        self.assertFalse(drivers[0].accept_order(first))
        self.assertFalse(replacement.accept_order(first))
        drivers[1].reject_order(second)

        self.assertEqual(order.state, "canceled")
        self.assertEqual(order.cancellation_reason, "no drivers accepted")
        self.assertEqual(len(order.offers), 2)
        self.assertEqual(replacement.state, "waiting for order")
        simulation.advance_to(20)
        self.assertEqual(simulation.order_history, [order])
        self.assert_consistent(simulation)

    def test_old_offer_cannot_affect_the_same_drivers_new_offer_for_another_order(self):
        simulation, drivers, riders = self.scenario(rider_count=2)
        first_order = riders[0].make_order()
        old = first_order.pending_offer
        drivers[0].reject_order(old)
        drivers[1].accept_order(first_order.pending_offer)
        simulation.advance_to(2)
        second_order = riders[1].make_order()
        current = second_order.pending_offer
        results = []
        simulation.schedule(8, lambda: results.append(drivers[0].accept_order(old)))
        simulation.schedule(8, lambda: results.append(drivers[0].reject_order(old)))
        simulation.schedule(8, lambda: results.append(old.timeout_event.callback()))

        simulation.advance_to(10)

        self.assertEqual(results, [False, False, False])
        self.assertIs(drivers[0].pending_offer, current)
        self.assertIs(second_order.pending_offer, current)
        self.assertEqual(current.state, "pending")
        self.assertEqual(current.expires_at, 12)
        self.assertTrue(drivers[0].accept_order(current))
        self.assert_consistent(simulation)

    def test_rider_ending_session_releases_offer_and_invalidates_late_work(self):
        simulation, drivers, riders = self.scenario(((0, 1),))
        rider = riders[0]
        order = rider.make_order()
        offer = order.pending_offer
        response = simulation.schedule(5, lambda: drivers[0].accept_order(offer), owner=offer)
        simulation.advance_to(2)

        rider.go_offline()
        replacement = rider.rider.start_session((0, 0), (3, 4))
        replacement_order = replacement.make_order()
        self.assertTrue(drivers[0].accept_order(replacement_order.pending_offer))
        rider.go_offline()
        simulation.advance_to(20)

        self.assertTrue(response.canceled)
        self.assertTrue(offer.timeout_event.canceled)
        self.assertEqual(offer.state, "canceled")
        self.assertEqual(order.cancellation_reason, "rider ended session")
        self.assertFalse(drivers[0].accept_order(offer))
        self.assertFalse(offer.timeout_event.callback())
        self.assertIs(drivers[0].current_order, replacement_order)
        self.assertEqual(simulation.rider_session_history, [rider])
        self.assert_consistent(simulation)

    def test_accepted_ride_cannot_be_abandoned_or_reset_through_session_actions(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        offer = order.pending_offer
        with self.assertRaises(ValueError):
            drivers[0].wait_for_order()
        drivers[0].accept_order(offer)

        for action in (drivers[0].go_offline, riders[0].go_offline, drivers[0].wait_for_order):
            with self.subTest(action=action), self.assertRaises(ValueError):
                action()
        for action in (drivers[0].arrive_at_pickup, drivers[0].pick_up_rider, drivers[0].end_ride):
            with self.subTest(action=action), self.assertRaises(NotImplementedError):
                action()

        self.assertIs(drivers[0].current_order, order)
        self.assertIs(riders[0].current_order, order)
        self.assert_consistent(simulation)

    def test_terminal_cleanup_releases_driver_without_altering_a_replacement_order(self):
        for state, reason in (("completed", None), ("canceled", "scripted cancellation")):
            with self.subTest(state=state):
                simulation, drivers, riders = self.scenario(((0, 1),), rider_count=2)
                first = riders[0].make_order()
                drivers[0].accept_order(first.pending_offer)

                simulation.finalize_order(first, state, reason)
                self.assertIsNone(drivers[0].current_order)
                self.assertEqual(drivers[0].state, "waiting for order")
                second = riders[1].make_order()
                drivers[0].accept_order(second.pending_offer)
                simulation.finalize_order(first, state, reason)
                simulation.advance_to(20)

                self.assertIs(first.driver_session, drivers[0])
                self.assertIs(drivers[0].current_order, second)
                self.assertEqual(simulation.order_history, [first])
                self.assert_consistent(simulation)

    def test_repeated_dispatch_does_not_create_duplicate_offers(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        offer = order.pending_offer

        simulation.dispatch_order(order)
        simulation.dispatch_order(order)
        self.assertIs(order.pending_offer, offer)
        self.assertEqual(order.offers, [offer])
        drivers[0].accept_order(offer)
        simulation.dispatch_order(order)

        self.assertEqual(order.offers, [offer])
        self.assert_consistent(simulation)

    def test_offer_owned_actions_reject_foreign_or_resolved_owners(self):
        simulation, drivers, riders = self.scenario()
        order = riders[0].make_order()
        offer = order.pending_offer
        other = Simulation(driver_count=0, rider_count=0)
        with self.assertRaises(ValueError):
            other.schedule(0, self.fail, owner=offer)

        drivers[0].reject_order(offer)

        with self.assertRaises(ValueError):
            simulation.schedule(0, self.fail, owner=offer)
        self.assertEqual(offer.pending_events, set())
        self.assert_consistent(simulation)


if __name__ == "__main__":
    unittest.main()
