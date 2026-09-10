"""S7 -- Service Reliability Hysteresis and Price vs. ETA Trade-offs (scenario-reviews/07-*).

Market: two_platform("budget", "reliable") on market-blank@1, Budget $1.00
base + $1.00/km, Reliable $3.00 base + $1.80/km, both 20% commission,
100 riders/20 drivers, 5-day weekly demand.

Approximations (recorded in `notes` below too): this script keeps
rider_search@1, which carries no cross-trip failure memory
(behavior-policy.md); switching here is only ever a *stated preference*
mechanism (`preference_change`/learned scores), never a failure-triggered
state machine. The per-rider consecutive-failure counter and the
fatigue-triggered sticky switch now exist as rider_search@2's
`fatigue_threshold`/`fatigue_target`/`sticky` traits and the runtime's
outcome memory (AST-209, plan section 4.E) -- see
scenarios/fixture_v2_hysteresis.py for a direct demonstration -- but
checks 1-3 stay NOT-EVALUABLE here because this script does not select
`@2`, and reliably forcing two consecutive Budget failures in this
100-rider, calibration-free population needs supply tuning that stays out
of this PR. What is measurable today is first-choice query share against
each day's installed base -- check 4, using
`first_choice_periods`/`--first-choice-days 1`.
"""
from harness import cli
from metrics import Checks, first_choice_periods
from scenario import two_platform

NOTES = {
    "assumption": "'unit' is one kilometre; both platforms keep 20% commission (DESCRIPTION.md does not state one).",
    "approximation": "This script keeps rider_search@1 (no cross-trip failure memory); rider_search@2's "
                      "failure counter and sticky fatigue switch now exist (AST-209, plan section 4.E, "
                      "demonstrated in scenarios/fixture_v2_hysteresis.py) but need calibrated supply to force "
                      "failures reliably in this population, which stays out of this PR -- only first-choice "
                      "share vs. installed base is measurable here.",
    "source": "plans/scenario-readiness-plan.md section 7 (S7 row) and section 10",
}


def build():
    return two_platform(
        "budget", "reliable", riders=100, drivers=20, horizon_hours=120,
        first_parameters={"base_fare_minor": 100, "per_km_minor": 100, "commission_fraction": .2},
        second_parameters={"base_fare_minor": 300, "per_km_minor": 180, "commission_fraction": .2},
    ).with_changes({
        "activity.trips": {"generator": "weekly", "trips_per_rider": 10, "duration_hours": 120, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 2, "shift_hours": 12, "days": 5, "first_start_hours": 0},
        "notes": NOTES,
    })


def evaluate(header, initial, final, footer):
    checks = Checks("s07-service-reliability-hysteresis")
    checks.not_evaluable("s7.1 individual failure counters on Budget",
                         "rider_search@1 (selected here) keeps no cross-trip failure memory; "
                         "rider_search@2's consecutive_failures (AST-209, plan section 4.E), demonstrated in "
                         "scenarios/fixture_v2_hysteresis.py, is not exercised by this @1 script")
    checks.not_evaluable("s7.2 two consecutive failures switch a rider's default to Reliable",
                         "rider_search@1 (selected here) has no failure-triggered switch; "
                         "rider_search@2's fatigue_threshold/fatigue_target (AST-209), demonstrated in "
                         "scenarios/fixture_v2_hysteresis.py, is not exercised by this @1 script")
    checks.not_evaluable("s7.3 Phase-2 converts stay on Reliable despite Budget's faster ETA",
                         "rider_search@1 (selected here) has no sticky-preference state; rider_search@2's "
                         "sticky flag (AST-209), demonstrated in scenarios/fixture_v2_hysteresis.py, is not "
                         "exercised by this @1 script")

    periods = first_choice_periods(header, initial, final, 1)
    total_queries = sum(row["queries"] for row in periods)
    both_installed = all(row["installed_riders"]["budget"] > 0 and row["installed_riders"]["reliable"] > 0
                         for row in periods)
    checks.verdict("s7.4 daily first-choice share vs. installed base is tracked", both_installed and total_queries > 0,
                   f"{len(periods)} daily rows; day 1 first_choice={periods[0]['first_choice_counts']} of "
                   f"installed={periods[0]['installed_riders']}; {total_queries} total first-choice queries")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
