"""S7 -- Service Reliability Hysteresis and Price vs. ETA Trade-offs (scenario-reviews/07-*).

Market: two_platform("budget", "reliable") on market-blank@1, Budget $1.00
base + $1.00/km, Reliable $3.00 base + $1.80/km, both 20% commission,
100 riders/20 drivers, 5-day weekly demand.

Approximations (recorded in `notes` below too): per-rider consecutive-
failure counters and a sticky preference switch after two failures are
absent -- driver_participation@1/rider_search@1 keep no cross-trip failure
memory (behavior-policy.md), and switching is a *stated preference*
mechanism (`preference_change`/learned scores), not a failure-triggered
state machine. That is phase 5 (driver/rider policy v2, plan section
4.D/4.E), so checks 1-3 are NOT-EVALUABLE. What is measurable today is
first-choice query share against each day's installed base -- check 4,
using `first_choice_periods`/`--first-choice-days 1`.
"""
from harness import cli
from metrics import Checks, first_choice_periods
from scenario import two_platform

NOTES = {
    "assumption": "'unit' is one kilometre; both platforms keep 20% commission (DESCRIPTION.md does not state one).",
    "approximation": "No per-rider failure counter or sticky preference switch exists yet (plan section 4.D/4.E, "
                      "phase 5); only first-choice share vs. installed base is measurable today.",
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
                         "no per-rider consecutive-failure memory exists yet (plan section 4.E, phase 5)")
    checks.not_evaluable("s7.2 two consecutive failures switch a rider's default to Reliable",
                         "no failure-triggered preference switch exists yet (plan section 4.E, phase 5)")
    checks.not_evaluable("s7.3 Phase-2 converts stay on Reliable despite Budget's faster ETA",
                         "no persistent brand-affinity/hysteresis state exists yet (plan section 4.E, phase 5)")

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
