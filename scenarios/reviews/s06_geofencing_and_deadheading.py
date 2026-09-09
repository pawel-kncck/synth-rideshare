"""S6 -- Asymmetric Geo-Fencing, Deadheading, and Suburban Supply Deserts (scenario-reviews/06-*).

Market: two_platform("citywide", "urbanonly") on market-blank@1, $1.20/km
loaded fare (no base fare) at 20% commission on both platforms, 100
riders/20 drivers, one weekly-demand day.

Approximations (recorded in `notes` below too): a service area that rejects
a query whose pickup/drop-off falls outside a zone is a phase-4 mechanism
(world service areas, plans/scenario-readiness-plan.md section 4.B);
"urbanonly" is built identically to "citywide" today, so checks 1 and 2
(query rejection outside the core, no dispatch to an out-of-zone driver
until it re-enters) are NOT-EVALUABLE, named honestly below rather than
faked. The Core/Suburb origin-destination mix needs geography (section 4.F,
phase 2); demand here uses the plain weekly generator over the whole map
instead of a zoned split.
"""
from harness import cli
from marketplace_policy import rounded
from metrics import Checks, _new_terminal, _tables, driver_distance
from scenario import two_platform

NOTES = {
    "assumption": "'unit' is one kilometre (scenario-reviews/README.md); both platforms pay $1.20/loaded-km, "
                  "no base fare, 20% commission.",
    "approximation": "urbanonly has no enforced service area (plan section 4.B, phase 4); Core/Suburb OD mix "
                      "needs geography (section 4.F, phase 2), so demand uses the plain weekly generator.",
    "source": "plans/scenario-readiness-plan.md section 7 (S6 row) and section 10",
}


def build():
    return two_platform(
        "citywide", "urbanonly", riders=100, drivers=20, horizon_hours=24,
        shared={"base_fare_minor": 0, "per_km_minor": 120, "commission_fraction": .2},
    ).with_changes({
        "activity.trips": {"generator": "weekly", "trips_per_rider": 3, "duration_hours": 24, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 24, "days": 1, "first_start_hours": 0},
        "notes": NOTES,
    })


def evaluate(header, initial, final, footer):
    checks = Checks("s06-geofencing-and-deadheading")
    checks.not_evaluable("s6.1 UrbanOnly rejects out-of-core queries",
                         "no service area exists yet; every platform sees every query (plan section 4.B, phase 4)")
    checks.not_evaluable("s6.2 no dispatch outside the core until re-entry",
                         "service areas do not exist yet, so location never gates candidacy (plan section 4.B, phase 4)")

    distances = driver_distance(initial, final)
    drivers = {k: v for k, v in distances.items() if k != "totals"}
    moved = sum(1 for v in drivers.values() if v["empty_km"] > 0 or v["loaded_km"] > 0)
    checks.verdict("s6.3 empty vs loaded km tracked per driver", moved > 0,
                   f"{moved}/{len(drivers)} drivers logged distance; fleet totals {distances['totals']}")

    before, after = _tables(initial), _tables(final)
    completed = _new_terminal(before, after, "completed")
    violations = []
    for order in completed:
        quote = after["quotes"][order["quote_id"]]
        expected = rounded(quote["distance_km"] * 120)  # per_km_minor only: no base fare, no pickup component
        if order["fare"]["gross_minor"] != expected:
            violations.append(order["id"])
    checks.verdict("s6.4 pickup travel consumes time but generates no fare", not violations,
                   f"0 of {len(completed)} completed orders' gross_minor depends on anything but the ride's own "
                   "(post-pickup) distance_km" + (f"; violations {violations[:3]}" if violations else ""))
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
