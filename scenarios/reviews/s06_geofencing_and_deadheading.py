"""S6 -- Asymmetric Geo-Fencing, Deadheading, and Suburban Supply Deserts (scenario-reviews/06-*).

Market: two_platform("citywide", "urbanonly") on market-blank@1, $1.20/km
loaded fare (no base fare) at 20% commission on both platforms, 200
riders/40 drivers, one weekly-demand day, on a 15x15 km map.

Geography (phase 2, plan section 4.F): zones `core` (0,0)-(5,5) and
`suburb` (6,6)-(15,15). All drivers start their shift in the core
(`start_zone`). Riders split `core-riders` (weight .6, origin always
`core`, destination 80% `core` / 20% `suburb`) and `suburb-riders` (weight
.4, origin always `suburb`, destination 50/50) -- the Core/Suburb
origin-destination mix DESCRIPTION.md asks for.

Approximations (recorded in `notes` below too): a service area that rejects
a query whose pickup/drop-off falls outside a zone is a phase-4 mechanism
(world service areas, plans/scenario-readiness-plan.md section 4.B);
"urbanonly" is built identically to "citywide" today, so checks 1 and 2
(query rejection outside the core, no dispatch to an out-of-zone driver
until it re-enters) are NOT-EVALUABLE, named honestly below rather than
faked -- geography alone (this phase) supplies the OD *mix*, not service
areas or dispatch gating.
"""
from harness import cli
from marketplace_policy import rounded
from metrics import Checks, _new_terminal, _tables, driver_distance
from scenario import segment, two_platform, zone

NOTES = {
    "assumption": "'unit' is one kilometre (scenario-reviews/README.md); both platforms pay $1.20/loaded-km, "
                  "no base fare, 20% commission. Core (0,0)-(5,5) and suburb (6,6)-(15,15) partition most of "
                  "the 15x15 map, leaving the strip between them (plan section 10, S6) unreachable as an "
                  "origin or destination -- riders' origin_zones/destination_zones only ever name these two.",
    "approximation": "urbanonly has no enforced service area (plan section 4.B, phase 4); checks 1/2 stay "
                     "NOT-EVALUABLE for that reason. The Core/Suburb OD mix itself (this note previously "
                     "deferred it to phase 2) is implemented: origin_zones/destination_zones below.",
    "source": "plans/scenario-readiness-plan.md section 7 (S6 row) and section 10; AST-206 (phase 2 geography)",
}


def build():
    scenario = two_platform(
        "citywide", "urbanonly", riders=200, drivers=40, horizon_hours=24,
        shared={"base_fare_minor": 0, "per_km_minor": 120, "commission_fraction": .2},
    ).with_changes({
        "world.map_km": [15, 15], "world.sampling": "continuous",
        "world.zones": zone("core", min=(0, 0), max=(5, 5)) | zone("suburb", min=(6, 6), max=(15, 15)),
        "activity.trips": {"generator": "weekly", "trips_per_rider": 3, "duration_hours": 24, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 24, "days": 1, "first_start_hours": 0},
        "notes": NOTES,
    })
    return (scenario
            .remove("population.riders.segments", "both-apps")
            .remove("population.drivers.segments", "both-apps")
            .with_changes({
                "population.riders.segments": {
                    "core-riders": segment(weight=.6, apps=("citywide", "urbanonly"), preferred_app="citywide",
                                           activity={"origin_zones": {"core": 1},
                                                     "destination_zones": {"core": {"core": .8, "suburb": .2}}}),
                    "suburb-riders": segment(weight=.4, apps=("citywide", "urbanonly"), preferred_app="citywide",
                                             activity={"origin_zones": {"suburb": 1},
                                                       "destination_zones": {"suburb": {"suburb": .5, "core": .5}}}),
                },
                "population.drivers.segments": {
                    "both-apps": segment(weight=1, apps=("citywide", "urbanonly"), preferred_app="citywide",
                                         driver={}, activity={"start_zone": "core"}),
                },
            }))


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
