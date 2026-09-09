"""S2 -- Flat-Fee Bonus vs. Multiplicative Surge Under Distance Heterogeneity (scenario-reviews/02-*).

Market: two_platform("metro", "urban"), $3.00 base + $1.00/km at 20%
commission on both, 150 riders/30 drivers, 6h weekly demand. From t=2h to
t=4h (policy_change), Metro's multiplier rises to 1.8x; Urban's base fare
rises by a flat $5.00 (300 -> 800 minor); both revert to baseline at t=4h.

Approximation (`notes` below): DESCRIPTION.md scopes surge to a (4,4)-(6,6)
zone; world.zones is phase 2 (section 4.F), so surge here is map-wide for
the window -- the multiplicative-vs-flat *rate structure* question (checks
1/2/4) does not depend on which trips are in scope. Both platforms keep the
shipped 20% commission, so Urban's surge is not a driver-share surcharge.

Checks 1, 2 and 4 are exact quote arithmetic under MarketplaceParameters'
half-up rounding; check 2's figures are the same formula by hand, not
sampled, so they hold independent of realized demand.
"""
from decimal import Decimal

from harness import cli
from marketplace_policy import rounded
from metrics import Checks
from scenario import policy_change, two_platform

BASE, PER_KM, MULTIPLIER, FLAT_SURGE = 300, 100, Decimal("1.8"), 500
SURGE_START, SURGE_END = 2 * 3600, 4 * 3600
NOTES = {"assumption": "Surge is map-wide for [2h,4h); both platforms keep 20% commission throughout.",
        "approximation": "world.zones does not exist yet (plan section 4.F, phase 2).",
        "source": "plans/scenario-readiness-plan.md section 7 (S2 row) and section 10"}


def build():
    surge = [policy_change(f"{p}-{edge}", at_hours=hour, platform=p, version=v, parameters=params)
            for p, on, off in (("metro", {"multiplier": float(MULTIPLIER)}, {"multiplier": 1}),
                               ("urban", {"base_fare_minor": BASE + FLAT_SURGE}, {"base_fare_minor": BASE}))
            for edge, hour, v, params in (("on", 2, "surge-v1", on), ("off", 4, "modern-v1", off))]
    scenario = two_platform(
        "metro", "urban", riders=150, drivers=30, horizon_hours=6,
        shared={"base_fare_minor": BASE, "per_km_minor": PER_KM, "commission_fraction": .2},
    ).with_changes({
        "activity.trips": {"generator": "weekly", "trips_per_rider": 4, "duration_hours": 6, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 6, "days": 1, "first_start_hours": 0},
        "notes": NOTES,
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
                       "(the reject-short-for-long comparison itself needs driver_participation@2, phase 5)")
    post = [q for q in quotes if q["at"] >= SURGE_END]
    bad_post = [q["id"] for q in post if q["fare"]["gross_minor"] != _gross(q["platform_id"], q["distance_km"], surging=False)]
    checks.verdict("s2.4 surge deactivates instantaneously at t=4h", not bad_post,
                   f"0 of {len(post)} quotes at/after t={SURGE_END}s deviate from the baseline formula"
                   + (f"; bad quote ids {bad_post[:5]}" if bad_post else ""))
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
