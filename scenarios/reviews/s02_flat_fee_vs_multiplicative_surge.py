"""S2 -- Flat-Fee Bonus vs. Multiplicative Surge Under Distance Heterogeneity (scenario-reviews/02-*).

Market: two_platform("metro", "urban"), $3.00 base + $1.00/km at 20%
commission on both, 150 riders/30 drivers, 6h weekly demand. From t=2h to
t=4h (policy_change), Metro's multiplier rises to 1.8x; Urban's base fare
rises by a flat $5.00 (300 -> 800 minor); both revert to baseline at t=4h.

Geography (phase 2, plan section 4.F): the map is 20x20 km with five zones
partitioning it (`surge` (8,8)-(12,12), `west`/`east`/`south`/`north` filling
the rest, area-proportional weights .04/.40/.40/.08/.08) and two rider
segments -- `short-haul` (1-3 km trips) and `long-haul` (8-12 km trips),
weight .5 each -- sharing those `origin_zones` and a single `spatial_peak`
that doubles the `surge` zone's origin weight from t=2h to t=4h, the same
window as the surcharge. Trip *count* is unaffected (`trips_per_rider`
stays the control input; plan section 8 forbids endogenous demand): only
where riders originate shifts within the window.

Approximation (`notes` below): DESCRIPTION.md scopes the surge zone to
(4,4)-(6,6) on a 10x10 map. An 8-12 km long-haul trip needs a map whose
half-diagonal reaches 12 km from every origin (`hypot(20,20)/2 = 14.14 >=
12`); a 10x10 map (`hypot(10,10)/2 = 7.07 < 12`) cannot host it from the
centre, so the map here is 20x20 with the surge zone scaled 2x in place
(same relative size and position) rather than left at DESCRIPTION's box.
Both platforms keep the shipped 20% commission throughout, so Urban's
surge is not a driver-share surcharge; that part of the approximation is
unrelated to geography and predates phase 2.

Checks 1, 2 and 4 are exact quote arithmetic under MarketplaceParameters'
half-up rounding; check 2's figures are the same formula by hand, not
sampled, so they hold independent of realized demand or geography.
"""
from decimal import Decimal

from harness import cli
from marketplace_policy import rounded
from metrics import Checks
from scenario import policy_change, segment, spatial_peak, two_platform, zone

BASE, PER_KM, MULTIPLIER, FLAT_SURGE = 300, 100, Decimal("1.8"), 500
SURGE_START, SURGE_END = 2 * 3600, 4 * 3600
ORIGIN_ZONES = {"surge": .04, "west": .40, "east": .40, "south": .08, "north": .08}
NOTES = {"assumption": "Surge is map-wide for pricing (only the origin-zone doubling is spatial); both "
                      "platforms keep 20% commission throughout.",
        "approximation": "DESCRIPTION.md's (4,4)-(6,6) surge zone on a 10x10 map cannot host an 8-12km "
                         "long-haul trip from every origin (half-diagonal 7.07 < 12), so the map here is "
                         "20x20 with the zone scaled 2x in place: (8,8)-(12,12).",
        "source": "plans/scenario-readiness-plan.md section 7 (S2 row) and section 10; AST-206 (phase 2 geography)"}


def build():
    surge = [policy_change(f"{p}-{edge}", at_hours=hour, platform=p, version=v, parameters=params)
            for p, on, off in (("metro", {"multiplier": float(MULTIPLIER)}, {"multiplier": 1}),
                               ("urban", {"base_fare_minor": BASE + FLAT_SURGE}, {"base_fare_minor": BASE}))
            for edge, hour, v, params in (("on", 2, "surge-v1", on), ("off", 4, "modern-v1", off))]
    surge_window = spatial_peak("surge-window", zones=["surge"], weekdays=[0], start_hour=2, end_hour=4, multiplier=2)
    scenario = two_platform(
        "metro", "urban", riders=150, drivers=30, horizon_hours=6,
        shared={"base_fare_minor": BASE, "per_km_minor": PER_KM, "commission_fraction": .2},
    ).with_changes({
        "world.map_km": [20, 20], "world.sampling": "continuous",
        "world.zones": (zone("surge", min=(8, 8), max=(12, 12)) | zone("west", min=(0, 0), max=(8, 20))
                        | zone("east", min=(12, 0), max=(20, 20)) | zone("south", min=(8, 0), max=(12, 8))
                        | zone("north", min=(8, 12), max=(12, 20))),
        "activity.trips": {"generator": "weekly", "trips_per_rider": 4, "duration_hours": 6, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 6, "days": 1, "first_start_hours": 0},
        "notes": NOTES,
    }).remove("population.riders.segments", "both-apps").with_changes({
        "population.riders.segments": {
            "short-haul": segment(weight=.5, apps=("metro", "urban"), preferred_app="metro",
                                  activity={"origin_zones": ORIGIN_ZONES, "distance_km": {"uniform": [1, 3]},
                                            "spatial_peaks": [surge_window]}),
            "long-haul": segment(weight=.5, apps=("metro", "urban"), preferred_app="metro",
                                 activity={"origin_zones": ORIGIN_ZONES, "distance_km": {"uniform": [8, 12]},
                                           "spatial_peaks": [surge_window]}),
        },
    })
    for item in surge:
        scenario = scenario.add("interventions", item)
    return scenario


def _gross(platform_id, distance_km, *, surging):
    if platform_id == "metro":
        return rounded((MULTIPLIER if surging else 1) * (Decimal(BASE) + Decimal(PER_KM) * Decimal(str(distance_km))))
    base = BASE + FLAT_SURGE if surging else BASE
    return rounded(Decimal(base) + Decimal(PER_KM) * Decimal(str(distance_km)))


def evaluate(header, initial, final, footer):
    checks = Checks("s02-flat-fee-vs-multiplicative-surge")
    quotes = list(final["engine"]["quotes"])
    surging = [q for q in quotes if SURGE_START <= q["at"] < SURGE_END]
    bad = [q["id"] for q in surging if q["fare"]["gross_minor"] != _gross(q["platform_id"], q["distance_km"], surging=True)]
    checks.verdict("s2.1 Metro scales with distance, Urban adds a flat $5.00", not bad,
                   f"0 of {len(surging)} in-window quotes deviate from the multiplicative/flat formula"
                   + (f"; bad quote ids {bad[:5]}" if bad else ""))
    m1, u1, m10, u10 = (_gross("metro", 1, surging=True), _gross("urban", 1, surging=True),
                        _gross("metro", 10, surging=True), _gross("urban", 10, surging=True))
    checks.verdict("s2.2 Metro cheaper short-haul, Urban cheaper long-haul",
                   (m1, u1, m10, u10) == (720, 900, 2340, 1800) and m1 < u1 and m10 > u10,
                   f"1km: metro {m1} < urban {u1} (720/900); 10km: metro {m10} > urban {u10} (2340/1800)")
    by_driver = {}
    for offer in final["engine"]["offers"]:
        if offer["platform_id"] == "urban":
            by_driver.setdefault(offer["driver_id"], []).append(offer)
    found = None
    for driver_id, offers in by_driver.items():
        offers.sort(key=lambda o: o["created_at"])
        found = next(((driver_id, a["id"], b["id"]) for a, b in zip(offers, offers[1:])
                     if a["state"] == "rejected" and b["state"] == "accepted"), None)
        if found:
            break
    if found is None:
        checks.not_evaluable("s2.3 no phantom lock on Urban after a rejection",
                             "no driver rejected an Urban offer and was later offered/accepted another at seed 0")
    else:
        checks.verdict("s2.3 no phantom lock on Urban after a rejection", True,
                       f"driver {found[0]} rejected Urban offer {found[1]}, later accepted Urban offer {found[2]} "
                       "(this script deliberately keeps driver_participation@1; @2's response_rule="
                       "'best_pending' (AST-209, plan section 4.D) is what compares pending offers before "
                       "rejecting the shorter one)")
    post = [q for q in quotes if q["at"] >= SURGE_END]
    bad_post = [q["id"] for q in post if q["fare"]["gross_minor"] != _gross(q["platform_id"], q["distance_km"], surging=False)]
    checks.verdict("s2.4 surge deactivates instantaneously at t=4h", not bad_post,
                   f"0 of {len(post)} quotes at/after t={SURGE_END}s deviate from the baseline formula"
                   + (f"; bad quote ids {bad_post[:5]}" if bad_post else ""))
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
