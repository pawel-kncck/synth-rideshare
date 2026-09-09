"""S4 -- Cold-Start Market Entry via Subsidized Rider Vouchers (scenario-reviews/04-*).

Market: market-blank@1 with "dominant" (launched) and "challenger"
(unlaunched, launches at t=24h), $3.00 base + $1.50/km at 20% commission on
both, 250 riders/50 drivers over 7 days. Nobody starts with Challenger
installed (apps=("dominant",)) but everyone is aware of it (awareness
includes both), so daily evolution checkpoints can draw downloads once it
launches (mirrors scenarios/flyt_launch_week.py's pattern). A new_user_only
campaign on Challenger from t=24h to t=96h discounts up to $10.00/ride
(discount_minor=discount_cap_minor=1000 minor, so the cap is redundant with
the flat amount -- the plan's literal reading, section 10 S4).

Approximations (recorded in `notes` below too): "first 3 rides free" as a
per-rider counted budget, an announced 0%-then-15% driver commission
schedule, and peer-adoption/ETA-triggered installs are first-N/announced-
terms/budget mechanics (plan section 4.B/4.C) and peer cascade or
ETA-triggered adoption (section 4.E) -- all phase 4/5, so check 3 is
NOT-EVALUABLE. Adoption here is the shipped rate-based evolution model
instead of the review's specific triggers.
"""
from harness import cli
from metrics import Checks, first_choice_periods, window_rows
from scenario import Scenario, campaign, launch, platform, segment

HOUR = 3600
NOTES = {
    "assumption": "Challenger's $10 voucher is both the discount and its cap (plan section 10, S4); "
                  "vicinity/peer-cascade adoption is approximated by the shipped rate-based evolution model "
                  "with onboard_car=True (behavior-policy.md: without it a driver downloads Challenger but "
                  "the car is never registered, so it can never actually serve a ride on it).",
    "approximation": "First-N voucher budgeting, announced commission terms and peer-cascade/ETA-triggered "
                      "installs are phase 4/5 mechanics (plan sections 4.B/4.C/4.E); not modelled here. "
                      "adoption_rate_per_day and checkpoint cadence are raised well above the shipped "
                      "defaults so seed 0 realizes Challenger installs and rides inside the run at all.",
    "source": "plans/scenario-readiness-plan.md section 7 (S4 row) and section 10",
}


def build():
    fares = {"base_fare_minor": 300, "per_km_minor": 150, "commission_fraction": .2}
    return Scenario(preset="market-blank@1", name="s04-cold-start-rider-vouchers").with_changes({
        "world.horizon_hours": 168,
        "population.riders.count": 80, "population.drivers.count": 20,
        "platforms": platform("dominant", **fares) | platform("challenger", launched=False, **fares),
        "population.riders.segments": {"cohort": segment(weight=1, apps=("dominant",), preferred_app="dominant",
                                                          awareness=("dominant", "challenger"))},
        "population.drivers.segments": {"cohort": segment(weight=1, apps=("dominant",), preferred_app="dominant",
                                                           awareness=("dominant", "challenger"), driver={})},
        "activity.trips": {"generator": "weekly", "trips_per_rider": 20, "duration_hours": 168, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 3, "shift_hours": 8, "days": 7, "first_start_hours": 0},
        # onboard_car=True: without it a driver can download Challenger but the car is never
        # registered for it, so it can never actually accept a Challenger offer (behavior-policy.md).
        "behavior.evolution.parameters.adoption_rate_per_day": 1.5,
        "behavior.evolution.parameters.onboard_car": True,
        "evolution": {"checkpoint_hours": 8, "first_checkpoint_hours": 8},
        "notes": NOTES,
    }).add(
        "interventions", launch("challenger-launch", at_hours=24, platform="challenger")
    ).add(
        "platforms.challenger.policy.campaigns",
        campaign("first-riders", start_hours=24, end_hours=96, discount_minor=1000, discount_cap_minor=1000,
                new_user_only=True)
    )


def evaluate(header, initial, final, footer):
    checks = Checks("s04-cold-start-rider-vouchers")
    before_launch = [r for r in final["engine"]["quotes"] if r["platform_id"] == "challenger" and r["at"] < 24 * HOUR]
    offers_early = [r for r in final["engine"]["offers"] if r["platform_id"] == "challenger" and r["created_at"] < 24 * HOUR]
    completed_early = [o for o in final["engine"]["orders"]
                       if o["platform_id"] == "challenger" and o["state"] == "completed"
                       and o["timeline"]["completed"] < 24 * HOUR]
    checks.verdict("s4.1 zero Challenger activity before t=24h",
                   not before_launch and not offers_early and not completed_early,
                   f"{len(before_launch)} quotes, {len(offers_early)} offers, {len(completed_early)} completions "
                   "on Challenger before launch")

    rows = window_rows(header, initial, final, [24, 96])
    contribution = rows[1]["platforms"]["challenger"]["money"]["totals"]["platform_contribution_minor"]
    checks.verdict("s4.2 Challenger runs a negative contribution in [24h,96h)", contribution < 0,
                   f"--windows 24,96 window [24h,96h): platform_contribution_minor = {contribution}")

    checks.not_evaluable("s4.3 each rider's first three Challenger rides are individually voucher-tracked",
                         "first-N redemption budgeting does not exist yet (plan section 4.B/4.C, phase 4); "
                         "new_user_only discounts every eligible ride in the campaign window, not just the first 3")

    periods = first_choice_periods(header, initial, final, 1)
    installed_grows = periods[-1]["installed_riders"]["challenger"] > periods[0]["installed_riders"]["challenger"]
    checks.verdict("s4.4 installs and first-choice usage are tracked separately", installed_grows,
                   f"day 1 installed={periods[0]['installed_riders']}, day 7 installed={periods[-1]['installed_riders']}, "
                   f"day 7 first_choice_counts={periods[-1]['first_choice_counts']} "
                   "(an install need not convert to a first-choice query that day)")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
