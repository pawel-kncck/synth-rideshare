"""Blot runs a mid-week rider discount and driver bonus, then raises its tariff afterwards.

The variant shares the baseline's population and schedules for the same seed,
so only Blot's commercial policy differs. The resolved difference is printed
before the runs so an inherited change is as visible as a numeric override.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import Simulation
from scenario import Scenario, campaign, compile_scenario, diff_plans, policy_change

baseline = Scenario(preset="three-platform-week@1")
variant = (
    baseline.renamed("blot-discount-week")
    .add("platforms.blot.policy.campaigns", campaign(
        "midweek", start_hours=24, end_hours=96, discount_fraction=.2, discount_cap_minor=500,
        bonus_minor=100, awareness="announced"))
    .add("interventions", policy_change(
        "post-campaign-tariff", at_hours=96, platform="blot", version="tariff-v2",
        parameters={"per_km_minor": 165}))
)

if __name__ == "__main__":
    base_plan, variant_plan = compile_scenario(baseline), compile_scenario(variant)
    print(json.dumps(diff_plans(base_plan, variant_plan), indent=2))
    shared = base_plan.prepare(seed=0)
    for plan in (base_plan, variant_plan):
        Simulation(shared.reuse_for(plan)).run()
