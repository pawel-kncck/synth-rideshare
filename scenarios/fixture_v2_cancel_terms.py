"""driver_participation@2 cancel_rule='private_eta' (AST-209, plan section 4.D; S3).

Three single-homed driver/rider pairs, one per platform, share identical geometry: a pickup
3.5 km from the driver at world.speed_kmh=30 gives a private pickup ETA of 420 s (7 minutes),
above cancel_eta_threshold_seconds=300. Each platform declares different cancellation terms, so
the same private-ETA overrun is tolerable on some and not on others (penalty_tolerance_minor=100):

  * lenient:  driver_cancellation_penalty_minor=0,  driver_lockout_seconds=0   -> exposure   0
  * strict:   driver_cancellation_penalty_minor=500, driver_lockout_seconds=900 -> exposure 2000
              (900 * reference_payout_minor(1000) / 600 = 1500, plus the 500 penalty)
  * moderate: driver_cancellation_penalty_minor=80,  driver_lockout_seconds=0   -> exposure  80

lenient and moderate cancel at the first cancel_check_seconds=30 recheck (exposure <= tolerance);
strict's exposure exceeds tolerance, so it falls through to the inherited @1 patience branch and
the ride completes normally. moderate's cancellation posts a nonzero driver_penalty Transfer, so
this fixture also exercises and verifies that money-conservation path end to end.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2
from main import run_scenario
from metrics import conservation, load_run
from policy_contracts import plain
from scenario import Scenario, person, platform, shift, trip

DRIVER_TRAITS = plain(DriverTraitsV2(
    cancel_rule="private_eta", cancel_eta_threshold_seconds=300, cancel_check_seconds=30,
    penalty_tolerance_minor=100, acceptance_bias=10))
RIDER_TRAITS = {"taste_scale": 0, "purchase_bias": 10, "cancellation_after_seconds": 3600}
PLATFORMS = {
    "lenient": {"driver_cancellation_penalty_minor": 0, "driver_lockout_seconds": 0},
    "strict": {"driver_cancellation_penalty_minor": 500, "driver_lockout_seconds": 900},
    "moderate": {"driver_cancellation_penalty_minor": 80, "driver_lockout_seconds": 0},
}


def build():
    platforms = None
    shifts, trips, people = [], [], []
    for name, terms in PLATFORMS.items():
        entry = platform(name, base_fare_minor=200, per_km_minor=150, **terms)
        platforms = entry if platforms is None else platforms | entry
        shifts.append(shift(f"{name}-shift", driver=f"d-{name}", at_hours=0, hours=1, location=(0, 0)))
        trips.append(trip(f"{name}-ride", rider=f"r-{name}", at_hours=0, origin=(3.5, 0), destination=(3.5, 5)))
        people.append(person(f"d-{name}", apps=(name,), preferred_app=name, driver={}))
        people.append(person(f"r-{name}", apps=(name,), preferred_app=name, rider=RIDER_TRAITS))
    scenario = Scenario(preset="market-blank@1", name="fixture-v2-cancel-terms").with_changes({
        "world.horizon_hours": 1,
        "platforms": platforms,
        "activity.shifts": {"generator": "explicit", "items": shifts},
        "activity.trips": {"generator": "explicit", "items": trips},
        "behavior.driver": {"implementation": "driver_participation@2", "parameters": DRIVER_TRAITS},
    })
    for item in people:
        section = "population.drivers.people" if item["id"].startswith("d-") else "population.riders.people"
        scenario = scenario.add(section, item)
    return scenario


if __name__ == "__main__":
    sim = run_scenario(build(), seed=0, log_dir="logs")
    for name in PLATFORMS:
        order = next(o for o in sim.engine.orders.values() if o.platform_id == name)
        detail = f"{order.cancellation['by']}, reason {order.cancellation['reason']!r}" if order.cancellation else "no cancellation"
        print(f"{name} order {order.id}: {order.state} ({detail})")
    penalties = [t for t in sim.engine.transfers.values() if t.reason == "driver_penalty"]
    print(f"driver_penalty transfers: {[(t.platform_id, t.amount_minor) for t in penalties]}")
    header, initial, final, footer = load_run(sim.log_path)
    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']} "
          f"transfer_party_delta_minor={cons['transfer_party_delta_minor']} "
          f"account_deltas_match_records={cons['account_deltas_match_records']}")
