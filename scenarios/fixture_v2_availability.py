"""driver_participation@2 availability (AST-209, plan section 4.D/5; S9/S5).

The participant-side answer to the competitor-occupancy boundary: a platform never learns another
platform's commitment count, but a driver can disclose "I'm busy" through pause_app/resume_app.

Default mode (pause_while_serving_other): one driver (d1, both apps), a Flash ride accepted first
(fixture_cross_platform_queue.py's geometry, renamed) running from t=75s to drop-off at t=705s.
Two Steady requests land while Flash is mid-ride (t=200s, t=450s): d1 shows accepting=False to
Steady for the whole ride (driver_availability), so quote()'s own candidates() finds nobody --
each sees a no-supply quote (eta_seconds is None), never places an order, and no Steady offer is
ever created for them. A third Steady request after drop-off (t=750s) sees d1 accepting=True
again, sees real supply, and is offered and accepted normally.

--full mode (pause_when_full): once Flash is active and a Steady ride is also accepted (queued,
both slots full), a third same-driver Flash request is paused on *both* apps -- the full-capacity
variant -- until one of the two commitments frees a slot.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2
from main import Simulation
from metrics import conservation, load_run
from policy_contracts import plain
from scenario import Scenario, compile_scenario, person, shift, trip, two_platform

RIDER_TRAITS = {"taste_scale": 0, "purchase_bias": 5}


def build(*, availability, third_flash_trip=False):
    driver_traits = plain(DriverTraitsV2(availability=availability, acceptance_bias=10, no_offer_seconds=30))
    trips = [
        trip("flash-ride", rider="r1", at_hours=60 / 3600, origin=(0, 0), destination=(0, 5)),
        trip("steady-early", rider="r2", at_hours=200 / 3600, origin=(5, 5), destination=(5, 10)),
        trip("steady-mid", rider="r3", at_hours=450 / 3600, origin=(5, 5), destination=(5, 10)),
        trip("steady-late", rider="r4", at_hours=750 / 3600, origin=(5, 5), destination=(5, 10)),
    ]
    riders = ["r2", "r3", "r4"]
    if third_flash_trip:
        trips.append(trip("flash-ride-2", rider="r5", at_hours=200 / 3600, origin=(0, 5), destination=(0, 9)))
        riders.append("r5")
    scenario = two_platform("flash", "steady", riders=0, drivers=0, horizon_hours=1).with_changes({
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d1-shift", driver="d1", at_hours=0, hours=1, location=(0, 0))]},
        "activity.trips": {"generator": "explicit", "items": trips},
        "behavior.driver": {"implementation": "driver_participation@2", "parameters": driver_traits},
    }).add(
        "population.riders.people", person("r1", apps=("flash",), preferred_app="flash", rider=RIDER_TRAITS)
    ).add(
        "population.drivers.people", person("d1", apps=("flash", "steady"), preferred_app="flash", driver={})
    )
    for rider_id in riders:
        platform_id = "flash" if rider_id == "r5" else "steady"
        scenario = scenario.add(
            "population.riders.people",
            person(rider_id, apps=(platform_id,), preferred_app=platform_id, rider=RIDER_TRAITS))
    return scenario


def run(availability, *, third_flash_trip=False):
    events = []
    scenario = build(availability=availability, third_flash_trip=third_flash_trip)
    inputs = compile_scenario(scenario).prepare(seed=0)
    sim = Simulation(inputs)
    sim.engine.add_listener(lambda n: events.append(n) if n.kind == "driver_availability" else None)
    sim.run(log_dir="logs")

    print(f"=== availability={availability!r} third_flash_trip={third_flash_trip} ===")
    for n in events:
        print(f"  t={n.at:.0f}s driver_availability platform={n.audience_id} accepting={n.data['accepting']}")

    flash_completed_at = _completed_at(sim, "flash")
    print(f"flash drop-off at t={flash_completed_at:.0f}s")

    for quote in sorted((q for q in sim.engine.quotes.values() if q.platform_id == "steady"), key=lambda q: q.at):
        print(f"  t={quote.at:.0f}s steady quote {quote.id} for rider {quote.rider_id}: eta_seconds={quote.eta_seconds}")

    steady_offers_while_serving_flash = sum(
        1 for o in sim.engine.offers.values() if o.platform_id == "steady" and o.created_at < flash_completed_at)
    print(f"steady offers to d1 while serving flash = {steady_offers_while_serving_flash}")

    steady_offers_after_dropoff = sum(
        1 for o in sim.engine.offers.values() if o.platform_id == "steady" and o.created_at >= flash_completed_at)
    print(f"steady offers after drop-off >= 1: {steady_offers_after_dropoff >= 1} (count={steady_offers_after_dropoff})")

    header, initial, final, footer = load_run(sim.log_path)
    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']}")
    return sim


def _completed_at(sim, platform_id):
    """The first completion time on this platform, or the horizon if it never completes."""
    times = [order.timeline["completed"] for order in sim.engine.orders.values()
            if order.platform_id == platform_id and "completed" in order.timeline]
    return min(times) if times else sim.horizon_seconds


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="pause_when_full with a second, queued Flash request")
    args = parser.parse_args()
    if args.full:
        run("pause_when_full", third_flash_trip=True)
    else:
        run("pause_while_serving_other")
