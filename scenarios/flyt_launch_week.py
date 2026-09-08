"""Flyt launches on Wednesday into a two-app market and grows through awareness and adoption.

Nobody starts with Flyt installed. Every segment knows about it, so daily
checkpoints can draw downloads once it has launched; drivers who adopt also
register their car (a declared onboarding policy, logged as a separate change).
A launch campaign runs for the first two days after launch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import run_scenario
from scenario import Scenario, campaign, launch, segment

TWO_APPS = ("rebu", "blot")
ALL_APPS = ("rebu", "blot", "flyt")
scenario = (
    Scenario(preset="three-platform-week@1", name="flyt-launch-week")
    .with_changes({
        "platforms.flyt.launched": False,
        "behavior.evolution.parameters.adoption_rate_per_day": .15,
        "behavior.evolution.parameters.adoption_friction": .5,
        "behavior.evolution.parameters.learning_rate": .1,
        "behavior.evolution.parameters.onboard_car": True,
    })
)
for role in ("riders", "drivers"):
    for name in ("rebu-first", "blot-first", "flyt-first", "rebu-only"):
        scenario = scenario.remove(f"population.{role}.segments", name)
scenario = scenario.with_changes({
    "population.riders.segments": {
        "rebu-first": segment(weight=.5, apps=TWO_APPS, preferred_app="rebu", awareness=ALL_APPS),
        "blot-first": segment(weight=.3, apps=TWO_APPS, preferred_app="blot", awareness=ALL_APPS),
        "rebu-only": segment(weight=.2, apps=("rebu",), preferred_app="rebu", awareness=ALL_APPS),
    },
    "population.drivers.segments": {
        "rebu-first": segment(weight=.5, apps=TWO_APPS, preferred_app="rebu", awareness=ALL_APPS, driver={}),
        "blot-first": segment(weight=.3, apps=TWO_APPS, preferred_app="blot", awareness=ALL_APPS, driver={}),
        "rebu-only": segment(weight=.2, apps=("rebu",), preferred_app="rebu", awareness=ALL_APPS, driver={}),
    },
}).add("interventions", launch("flyt-launch", at_hours=48, platform="flyt")).add(
    "platforms.flyt.policy.campaigns",
    campaign("launch", start_hours=48, end_hours=96, discount_fraction=.25, discount_cap_minor=400, bonus_minor=150))

if __name__ == "__main__":
    sim = run_scenario(scenario, seed=0)
    installs = [o for o in sim.policies.observations if o["type"] == "app_installed" and o["platform_id"] == "flyt"]
    cars = [o for o in sim.policies.observations if o["type"] == "car_registered" and o["platform_id"] == "flyt"]
    print(f"Flyt downloads: {sum(o['role'] == 'rider' for o in installs)} riders, "
          f"{sum(o['role'] == 'driver' for o in installs)} drivers; cars registered: {len(cars)}")
