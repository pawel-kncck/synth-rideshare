"""rider_search@2 fatigue/sticky + personal_evolution@2 sticky (AST-209, plan section 4.E; S7).

One multi-homing rider (r1, preferred budget) makes two trips that Budget's driver cancels on
elapsed patience (a pickup far enough that cancel_after_seconds=60 elapses before arrival) --
Reliable has no driver yet, so r1's inspection of it is a no-op (no viable quote, no order, no
side effect). fatigue_threshold=2 means the *third* trip's decide() sees two recorded failures on
Budget and switches preferred_app to Reliable (fatigue_target='best_other') before even trying
Budget again; Reliable now has a driver and the trip completes there.

r1's initial_scores seed Budget far above Reliable, so from the next checkpoint onward
personal_evolution@2's own learning-driven comparison would want to switch preferred_app *back*
to Budget (learned[budget] > learned[reliable] + margin) -- representing "Budget improved" (a
policy_change intervention on Budget's tariff runs at the same time, for the same reason) without
depending on hard-to-control post-switch experience. sticky=True (recorded on the fatigue switch)
suppresses exactly that learning-driven reversion; sticky=False does not, and this fixture runs
both to show the flag is what makes the difference.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import EvolutionTraitsV2, RiderTraitsV2
from main import run_scenario
from metrics import conservation, load_run
from policy_contracts import plain
from scenario import Scenario, person, platform, policy_change, shift, trip

EVOLUTION_TRAITS = plain(EvolutionTraitsV2(learning_rate=.5))


def build(*, sticky):
    rider_traits = plain(RiderTraitsV2(fatigue_threshold=2, fatigue_target="best_other", sticky=sticky,
                                       taste_scale=0, purchase_bias=10))
    platforms = (platform("budget", base_fare_minor=200, per_km_minor=150)
                | platform("reliable", base_fare_minor=200, per_km_minor=150))
    return Scenario(preset="market-blank@1", name="fixture-v2-hysteresis").with_changes({
        "world.horizon_hours": 4,
        "platforms": platforms,
        "evolution": {"checkpoint_hours": 1, "first_checkpoint_hours": 1},
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d-budget-1-shift", driver="d-budget-1", at_hours=0, hours=4, location=(0, 0)),
            shift("d-budget-2-shift", driver="d-budget-2", at_hours=0, hours=4, location=(10, 0)),
            shift("d-reliable-shift", driver="d-reliable", at_hours=500 / 3600, hours=4, location=(20, 0)),
        ]},
        "activity.trips": {"generator": "explicit", "items": [
            trip("budget-ride-1", rider="r1", at_hours=0, origin=(3, 0), destination=(3, 5)),
            trip("budget-ride-2", rider="r1", at_hours=300 / 3600, origin=(13, 0), destination=(13, 5)),
            trip("switch-ride", rider="r1", at_hours=600 / 3600, origin=(20, 0), destination=(20, 5)),
        ]},
        "behavior.rider": {"implementation": "rider_search@2", "parameters": rider_traits},
        "behavior.evolution": {"implementation": "personal_evolution@2", "parameters": EVOLUTION_TRAITS},
    }).add(
        "population.riders.people", person("r1", apps=("budget", "reliable"), preferred_app="budget",
                                           rider={}, initial_scores={"budget": 2.0, "reliable": -2.0})
    ).add(
        "population.drivers.people", person("d-budget-1", apps=("budget",), preferred_app="budget",
                                            driver={"acceptance_bias": 10, "cancel_after_seconds": 60})
    ).add(
        "population.drivers.people", person("d-budget-2", apps=("budget",), preferred_app="budget",
                                            driver={"acceptance_bias": 10, "cancel_after_seconds": 60})
    ).add(
        "population.drivers.people", person("d-reliable", apps=("reliable",), preferred_app="reliable",
                                            driver={"acceptance_bias": 10})
    ).add(
        "interventions", policy_change("budget-improves", at_hours=1, platform="budget", version="budget-v2",
                                       parameters={"base_fare_minor": 50, "per_km_minor": 50})
    )


def run(*, sticky):
    sim = run_scenario(build(sticky=sticky), seed=0, log_dir="logs")
    s = sim.policies.state("rider", "r1")
    print(f"=== sticky={sticky} ===")
    print(f"failures = {s.get('failures')}")
    switches = [obs for obs in sim.policies.observations if obs["type"] == "preference_switched"]
    print(f"preference_switched observations = {switches}")
    checkpoints = [d for d in sim.policies.decisions
                  if d["role"] == "rider" and d["identity"] == "r1" and d["hook"] == "checkpoint"]
    print(f"checkpoint preferred_app after each of {len(checkpoints)} checkpoints: "
          f"{[d['proposal']['preferred_app'] for d in checkpoints]}")
    print(f"final preferred_app = {s['preferred_app']}")
    print(f"sticky_preference flag = {s.get('sticky_preference')}")
    header, initial, final, footer = load_run(sim.log_path)
    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']}")
    return sim


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-sticky", action="store_true", help="Run with sticky=False instead")
    args = parser.parse_args()
    run(sticky=not args.no_sticky)
