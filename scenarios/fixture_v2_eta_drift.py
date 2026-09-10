"""rider_search@2 eta_drift_cancel_seconds + revise_interval_seconds (AST-209, plan section 4.E; S5).

fixture_cross_platform_queue.py's geometry: one driver serves a Rebu ride while Blot's request
(from a point near the Rebu route) arrives mid-ride, so Blot's initial pickup ETA is estimated
from the driver's current position with no knowledge of the still-running Rebu ride -- an
optimistic promise. Blot's platform re-revises that ETA every revise_interval_seconds=60s as the
driver continues away from Blot's pickup toward its Rebu drop-off; the Blot rider's
eta_drift_cancel_seconds=120 compares the latest revision's *predicted arrival instant*
(at + eta_seconds) against the original offer's, and cancels once the drift exceeds 120s.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import RiderTraitsV2
from main import run_scenario
from metrics import conservation, load_run
from policy_contracts import plain
from scenario import Scenario, person, shift, trip

RIDER_TRAITS = plain(RiderTraitsV2(
    eta_drift_cancel_seconds=120, taste_scale=0, purchase_bias=5, cancellation_after_seconds=900))


def build():
    return Scenario(preset="three-platform-day@1", name="fixture-v2-eta-drift").with_changes({
        "world.horizon_hours": 1,
        "population.riders.count": 0,
        "population.drivers.count": 0,
        "evolution.checkpoint_hours": None,
        "platforms.blot.policy.parameters.revise_interval_seconds": 60,
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d1-shift", driver="d1", at_hours=0, hours=1, location=(0, 0))]},
        "activity.trips": {"generator": "explicit", "items": [
            trip("rebu-ride", rider="r1", at_hours=60 / 3600, origin=(0, 0), destination=(0, 5)),
            trip("blot-ride", rider="r2", at_hours=340 / 3600, origin=(.889, 3.208), destination=(5, 3.2)),
        ]},
        "behavior.rider": {"implementation": "rider_search@2", "parameters": RIDER_TRAITS},
    }).remove("population.riders.segments", "rebu-first").remove(
        "population.riders.segments", "blot-first").remove(
        "population.riders.segments", "flyt-first").remove(
        "population.riders.segments", "rebu-only").remove(
        "population.drivers.segments", "rebu-first").remove(
        "population.drivers.segments", "blot-first").remove(
        "population.drivers.segments", "flyt-first").remove(
        "population.drivers.segments", "rebu-only"
    ).add(
        "population.riders.people", person("r1", apps=("rebu",), preferred_app="rebu",
                                           rider={"taste_scale": 0, "purchase_bias": 5})
    ).add(
        "population.riders.people", person("r2", apps=("blot",), preferred_app="blot", rider={})
    ).add(
        "population.drivers.people", person("d1", apps=("rebu", "blot"), preferred_app="rebu",
                                            driver={"acceptance_bias": 10, "no_offer_seconds": 30})
    )


if __name__ == "__main__":
    sim = run_scenario(build(), seed=0, log_dir="logs")
    blot_order = next(o for o in sim.engine.orders.values() if o.platform_id == "blot")
    print(f"blot order {blot_order.id}: state={blot_order.state} cancellation={blot_order.cancellation}")
    predictions = [(p["source"], round(p["at"]), round(p["eta_seconds"]) if p["eta_seconds"] is not None else None)
                   for p in blot_order.eta_predictions]
    print(f"eta_predictions = {predictions}")
    promise = next(p for p in blot_order.eta_predictions if p["source"] == "offer")
    revisions = [p for p in blot_order.eta_predictions if p["source"] == "revision"]
    if revisions:
        latest = revisions[-1]
        drift = (latest["at"] + latest["eta_seconds"]) - (promise["at"] + promise["eta_seconds"])
        print(f"drift at last revision before cancel = {drift:.0f}s (threshold 120s)")
    header, initial, final, footer = load_run(sim.log_path)
    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']}")
