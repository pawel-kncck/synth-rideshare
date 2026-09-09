"""driver_participation@2 hold_for_better (AST-209, plan section 4.D).

Five single-driver/single-rider platforms share identical geometry (a 5 km ride, base_fare_minor
200 + per_km_minor 150 => 950 gross, 20% default commission => 760 payout minor) so every
platform's driver sees the exact same offer; only the driver's hold_for_better trait combination
differs, isolating what the reservation stage alone decides:

  * nohold:      hold_for_better=False (baseline: accepts on the unchanged @1 logistic alone)
  * below:       hold_for_better=True, response_objective='total_payout' (the default), reservation
                 100 minor (well below the 760 payout) -> accepts
  * above:       hold_for_better=True, response_objective='total_payout', reservation 10000 minor
                 (above the payout) -> holds out; the order cancels once no other driver takes it
  * rate_below:  hold_for_better=True, response_objective='payout_per_minute', reservation 100 minor
                 -- the regression this fixture exists for. The reservation stage must compare
                 against the offer's *total* payout regardless of response_objective (which governs
                 only best_pending ranking, a different stage): 100 is far below 760, so this must
                 also accept. Before the fix, offer_value(mine, 'payout_per_minute', ...) (a
                 per-minute rate, an order of magnitude smaller than 760) was compared directly
                 against the absolute 100 reservation, so rate_below incorrectly held out here
                 every time and the order canceled.
  * rate_above:  hold_for_better=True, response_objective='payout_per_minute', reservation 10000
                 minor -- same objective as rate_below but a reservation genuinely above the
                 payout, so this must still hold out; proves the fix compares against the true
                 payout rather than simply never holding out under payout_per_minute.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2
from main import run_scenario
from metrics import conservation, load_run
from policy_contracts import plain
from scenario import Scenario, person, platform, shift, trip

RIDER_TRAITS = {"taste_scale": 0, "purchase_bias": 10}
BASE_DRIVER_TRAITS = plain(DriverTraitsV2(acceptance_bias=10))
DRIVER_MODES = {
    "nohold": {},
    "below": {"hold_for_better": True, "reservation_payout_minor": 100},
    "above": {"hold_for_better": True, "reservation_payout_minor": 10000},
    "rate_below": {"hold_for_better": True, "reservation_payout_minor": 100,
                   "response_objective": "payout_per_minute"},
    "rate_above": {"hold_for_better": True, "reservation_payout_minor": 10000,
                   "response_objective": "payout_per_minute"},
}


def build():
    platforms = None
    shifts, trips, people = [], [], []
    for name in DRIVER_MODES:
        entry = platform(name, base_fare_minor=200, per_km_minor=150)
        platforms = entry if platforms is None else platforms | entry
        shifts.append(shift(f"{name}-shift", driver=f"d-{name}", at_hours=0, hours=1, location=(0, 0)))
        trips.append(trip(f"{name}-ride", rider=f"r-{name}", at_hours=0, origin=(0, 0), destination=(0, 5)))
        people.append(person(f"d-{name}", apps=(name,), preferred_app=name, driver=DRIVER_MODES[name]))
        people.append(person(f"r-{name}", apps=(name,), preferred_app=name, rider=RIDER_TRAITS))
    scenario = Scenario(preset="market-blank@1", name="fixture-v2-hold-for-better").with_changes({
        "world.horizon_hours": 1,
        "platforms": platforms,
        "activity.shifts": {"generator": "explicit", "items": shifts},
        "activity.trips": {"generator": "explicit", "items": trips},
        "behavior.driver": {"implementation": "driver_participation@2", "parameters": BASE_DRIVER_TRAITS},
    })
    for item in people:
        section = "population.drivers.people" if item["id"].startswith("d-") else "population.riders.people"
        scenario = scenario.add(section, item)
    return scenario


if __name__ == "__main__":
    sim = run_scenario(build(), seed=0, log_dir="logs")
    for name in DRIVER_MODES:
        order = next(o for o in sim.engine.orders.values() if o.platform_id == name)
        responses = [d for d in sim.policies.decisions
                    if d["role"] == "driver" and d["identity"] == f"d-{name}" and d["hook"] == "respond"]
        resp = responses[0]
        print(f"{name}: order {order.id} state={order.state} "
              f"respond p={resp['proposal']['probability']:.3f} accept={resp['proposal']['accept']} "
              f"why={resp['explanation']!r}")
    header, initial, final, footer = load_run(sim.log_path)
    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']}")
