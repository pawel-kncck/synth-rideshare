"""A synthetic 30-day market: Flyt earns share through adoption and a tapered campaign.

Run with --control for the same people, trips and shifts without the commercial
intervention. See flyt_growth_month.md for assumptions and analysis commands.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import run_scenario
from scenario import Scenario, campaign, policy_change, segment, shift


ALL_APPS = ("rebu", "blot", "flyt")
INCUMBENTS = ("rebu", "blot")
DAYS = 30


def build(*, control=False):
    scenario = Scenario(preset="three-platform-week@1", name="flyt-growth-month").with_changes({
        "world.horizon_hours": DAYS * 24 + 2,
        "world.speed_kmh": 25,
        "population.riders.count": 1000,
        "population.drivers.count": 50,
        "activity.trips.trips_per_rider": 16,
        "activity.trips.duration_hours": DAYS * 24,
        "behavior.rider.parameters.purchase_bias": 2.2,
        "behavior.rider.parameters.eta_tolerance_seconds": 420,
        "behavior.rider.parameters.cancellation_after_seconds": 900,
        "behavior.driver.parameters.second_order_probability": .6,
        "behavior.driver.parameters.max_private_pickup_seconds": 900,
        "behavior.evolution.parameters.learning_rate": .12,
        "behavior.evolution.parameters.preference_margin": .12,
        "behavior.evolution.parameters.preference_cooldown_seconds": 3 * 86400,
        "behavior.evolution.parameters.adoption_friction": .3,
        "behavior.evolution.parameters.onboard_car": True,
    })
    for app in ALL_APPS:
        scenario = scenario.with_changes({f"platforms.{app}.policy.parameters.estimated_speed_kmh": 25})

    # Keep the cohort size fixed. Access and first choices are initial conditions,
    # never target completion shares or forced preference-change interventions.
    mix = (
        ("rebu-first", .45, INCUMBENTS, "rebu"),
        ("blot-first", .25, INCUMBENTS, "blot"),
        ("flyt-first", .10, ALL_APPS, "flyt"),
        ("comparison", .10, ALL_APPS, "rebu"),
        ("rebu-only", .10, ("rebu",), "rebu"),
    )
    for role in ("riders", "drivers"):
        for name in ("rebu-first", "blot-first", "flyt-first", "rebu-only"):
            scenario = scenario.remove(f"population.{role}.segments", name)
        segments = {}
        for name, weight, apps, preferred in mix:
            traits = ({"driver": {"no_offer_seconds": {"uniform": [45, 120]}}}
                      if role == "drivers" else
                      {"rider": {"loyalty": {"uniform": [.15, .45]},
                                 "price_sensitivity": {"uniform": [.8, 1.4]}}})
            segments[name] = segment(
                weight=weight, apps=apps, preferred_app=preferred, awareness=ALL_APPS,
                evolution={"adoption_rate_per_day": .06 if role == "drivers" else .035},
                **traits)
        scenario = scenario.with_changes({f"population.{role}.segments": segments})

    # Three staggered eight-hour crews, with two consecutive days off per week
    # for each driver. Stagger rest days within crews to maintain 24-hour supply.
    shifts = [shift(
        f"shift-{index}-{day + 1}", driver=f"driver-{index}",
        at_hours=day * 24 + ((index - 1) % 3) * 8, hours=8,
        location=(1 + (index * 3) % 9, 1 + (index * 7) % 9))
        for day in range(DAYS) for index in range(1, 51)
        if (day + (index - 1) // 3) % 7 < 5]
    scenario = scenario.with_changes({"activity.shifts": {"generator": "explicit", "items": shifts}})
    if control:
        return scenario.renamed("flyt-growth-month-control")

    scenario = scenario.add("interventions", policy_change(
        "flyt-lower-commission", at_hours=7 * 24, platform="flyt", version="growth-v1",
        parameters={"commission_fraction": .15}))
    for name, start, end, discount, cap, bonus in (
        ("trial", 7, 14, .25, 400, 150),
        ("repeat", 14, 21, .20, 300, 100),
        ("taper", 21, 30, .10, 200, 50),
    ):
        scenario = scenario.add("platforms.flyt.policy.campaigns", campaign(
            name, start_hours=start * 24, end_hours=end * 24,
            discount_fraction=discount, discount_cap_minor=cap, bonus_minor=bonus))
    return scenario


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--control", action="store_true")
    args = parser.parse_args()
    run_scenario(build(control=args.control), seed=args.seed)
