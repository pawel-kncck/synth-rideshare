"""driver_participation@2 response_rule='best_pending' (AST-209, plan section 4.D; S1).

One driver (d1, both apps open at shift start via open_apps_at_start='all'), two single-homing
riders (r1 alpha-only, r2 beta-only) whose trips start at the same instant, so both platforms'
dispatch lands two co-pending offers on d1 at the same response tick. Default mode: alpha and beta
quote the same fare but alpha's commission is higher (so its driver payout is lower), so
best_pending strictly prefers beta -- alpha's offer is rejected, beta's is accepted, and (with
retry_drivers=False, the shipped default) alpha's order finds no other driver and is canceled once
dispatch is exhausted on order_patience_seconds.

--tie-random sweeps seeds with equal payout on both platforms: tie_break='random' should split
roughly evenly, while tie_break='stable' (the default trait value) always favors the smaller
(platform_id, offer_id) -- alpha, because r1's trip is listed first and so its order/offer
consistently gets the lower id. The tie-sweep pre-fills the driver's other commitment slot with an
unrelated, fast-resolving "gamma" ride so that whichever of alpha/beta loses the coin flip is
rejected outright (free_slots=0) rather than also being accepted as an independent second order --
without this, whichever platform responds first and wins the tie leaves the other, now the sole
still-pending offer, to win its own trivial one-way tie a moment later.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2
from main import Simulation, run_scenario
from metrics import conservation, load_run
from policy_contracts import plain
from scenario import Scenario, compile_scenario, person, platform, shift, trip

RIDER_TRAITS = {"taste_scale": 0, "purchase_bias": 10}
FILLER_TRAITS = {"taste_scale": 0, "purchase_bias": 10, "decision_seconds": 0.1, "opening_seconds": 0.1}


def build(*, tie_break="stable", equal_payout=False):
    driver_traits = plain(DriverTraitsV2(
        open_apps_at_start="all", response_rule="best_pending", response_objective="total_payout",
        tie_break=tie_break, acceptance_bias=10))
    commission = {"alpha": 0.2, "beta": 0.2} if equal_payout else {"alpha": 0.5, "beta": 0.0}
    platforms = (platform("alpha", base_fare_minor=200, per_km_minor=150, commission_fraction=commission["alpha"])
                | platform("beta", base_fare_minor=200, per_km_minor=150, commission_fraction=commission["beta"]))
    driver_apps = ["alpha", "beta"]
    trips = [
        trip("alpha-ride", rider="r1", at_hours=0, origin=(0, 0), destination=(0, 5)),
        trip("beta-ride", rider="r2", at_hours=0, origin=(0, 0), destination=(0, 5)),
    ]
    people = [
        person("r1", apps=("alpha",), preferred_app="alpha", rider=RIDER_TRAITS),
        person("r2", apps=("beta",), preferred_app="beta", rider=RIDER_TRAITS),
    ]
    if equal_payout:
        # A same-instant, fast-resolving third ride occupies the driver's other slot well before
        # alpha/beta's offers are even created, so the tie's loser is rejected on free_slots==0
        # rather than also being independently accepted as a second order a moment later.
        platforms |= platform("gamma", base_fare_minor=200, per_km_minor=150, commission_fraction=0.2)
        driver_apps.append("gamma")
        trips.append(trip("gamma-ride", rider="r0", at_hours=0, origin=(0, 0), destination=(0, 10)))
        people.append(person("r0", apps=("gamma",), preferred_app="gamma", rider=FILLER_TRAITS))
    scenario = Scenario(preset="market-blank@1", name="fixture-v2-two-offers").with_changes({
        "world.horizon_hours": 1,
        "platforms": platforms,
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d1-shift", driver="d1", at_hours=0, hours=1, location=(0, 0))]},
        "activity.trips": {"generator": "explicit", "items": trips},
        "behavior.driver": {"implementation": "driver_participation@2", "parameters": driver_traits},
    }).add("population.drivers.people", person("d1", apps=tuple(driver_apps), preferred_app="alpha", driver={}))
    for item in people:
        scenario = scenario.add("population.riders.people", item)
    return scenario


def _offer_summary(sim):
    """{platform_id: (offer_id, state)} for d1's offers to alpha/beta, in creation order."""
    offers = sorted((o for o in sim.engine.offers.values() if o.driver_id == "d1" and o.platform_id != "gamma"),
                    key=lambda o: o.id)
    return {o.platform_id: (o.id, o.state) for o in offers}


def run_default(seed):
    scenario = build()
    sim = run_scenario(scenario, seed=seed, log_dir="logs")
    summary = _offer_summary(sim)
    for platform_id in ("alpha", "beta"):
        offer_id, state = summary[platform_id]
        print(f"{platform_id} offer {offer_id}: {state}")
    accepted = sum(1 for order in sim.engine.orders.values()
                   if order.assignment is not None and order.assignment.driver_id == "d1")
    print(f"commitments accepted for d1 = {accepted}")
    completed = sum(1 for order in sim.engine.orders.values() if order.state == "completed")
    canceled = sum(1 for order in sim.engine.orders.values() if order.state == "canceled")
    print(f"orders completed = {completed}, canceled = {canceled}")
    header, initial, final, footer = load_run(sim.log_path)
    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']}")
    return sim


def run_tie_sweep(seeds, tie_break):
    tally = {"alpha": 0, "beta": 0}
    scenario = build(tie_break=tie_break, equal_payout=True)
    for seed in range(seeds):
        inputs = compile_scenario(scenario).prepare(seed=seed)
        sim = Simulation(inputs)
        sim.advance_to(sim.horizon_seconds)
        summary = _offer_summary(sim)
        winners = [p for p, (_, state) in summary.items() if state == "accepted"]
        assert len(winners) == 1, f"seed {seed}: expected exactly one winner, got {summary}"
        winner = winners[0]
        tally[winner] += 1
        print(f"seed {seed}: winner={winner} offers={summary}")
    print(f"tally over {seeds} seeds under tie_break={tie_break!r}: {tally}")
    return tally


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0, help="Seed for the default (asymmetric-payout) run")
    parser.add_argument("--tie-random", action="store_true",
                         help="Equal-payout tie sweep under tie_break='random' (default: 'stable')")
    parser.add_argument("--seeds", type=int, default=None, help="Seed count for the tie sweep (implies a sweep)")
    args = parser.parse_args()
    if args.tie_random or args.seeds is not None:
        run_tie_sweep(args.seeds or 20, "random" if args.tie_random else "stable")
    else:
        run_default(args.seed)
