"""S8 -- Mid-Trip Regulatory Price Cap and Contract Immutability (scenario-reviews/08-*).

Market: two_platform("apex", "zenith") on market-blank@1, 250 background riders
(rider-1..250) plus three dedicated boundary riders, 60 background drivers
(driver-1..60) plus three dedicated boundary drivers, identical pre-shock
tariff ($2.00 base + $4.00/km, 25% commission) on both platforms. At t=12h a
municipal order takes effect on both platforms via policy_change: base
$2.50, $2.00/km, 10% commission (plan section 10, S8's reading of the
DESCRIPTION.md cap).

Boundary trips A, B and C are explicit trips at the decimal hours as written
in DESCRIPTION.md (assumption, plan section 10 S8): 11.45h = 11:27:00,
11.58h = 11:34:48, 12.01h = 12:00:36, all on the same 8.0 km corridor
(1,1)->(9,1) so the fare arithmetic below is exact. "Dispatched" means an
accepted offer (assignment), matching S1's reading.

The `explicit` trip generator cannot be combined with `weekly` in one
definition (TRIPS is a Choice), so all background demand for the remaining
riders is also explicit: a deterministic list comprehension (no RNG in this
script) gives each background rider one trip spread across the day.
Background driver shifts are two-part (00:00-11:00 and 12:30-24:00),
deliberately leaving background supply offline for the 11:00-12:30 window
so it cannot substitute for the boundary trips' own dedicated drivers.

Approximations relative to DESCRIPTION.md (recorded in `notes` below too):
  * Corridor demand ("high demand across an airport-to-downtown transit
    corridor") is approximated by explicit point-to-point trips rather than
    a zone or corridor generator -- world.zones is phase 2 (plan section
    4.F). Only the three boundary trips model the corridor explicitly;
    background demand is scattered across the map.
  * world.speed_kmh is lowered from its 30 km/h default to 15 km/h, and the
    three boundary drivers get a large acceptance_bias (15, vs the default
    1) and the boundary riders a large purchase_bias (8) with taste_scale=0
    and a longer cancellation_after_seconds (3600s) -- the same determinism
    levers scenarios/fixture_cross_platform_queue.py uses to remove decision
    randomness, needed here so a specific pickup reliably straddles the
    t=12h boundary at seed 0 instead of leaving it to chance. The realized
    assignment/pickup times are printed by --check (see s8.1/s8.2 detail).

Boundary driver placement (world.map_km stays the default 10x10, so a
pickup this slow needs the lower speed above): boundary-driver-a starts at
(1,9), 8.0 km from the corridor's pickup at (1,1); boundary-driver-b at
(9,9), 11.31 km away, for a longer pickup that survives past t=12h;
boundary-driver-c starts at (1,1) itself (colocated) so trip C, entirely
inside the new regime, dispatches immediately after t=12h.
"""
import math
from decimal import Decimal

from harness import cli
from marketplace_policy import rounded
from metrics import Checks, _new_settlements, _tables, settlement_breakdown
from scenario import person, policy_change, shift, trip, two_platform

RIDERS, DRIVERS = 250, 60
NOON = 12 * 3600
PRE_SHOCK = {"base_fare_minor": 200, "per_km_minor": 400, "commission_fraction": Decimal("0.25")}
POST_SHOCK = {"base_fare_minor": 250, "per_km_minor": 200, "commission_fraction": Decimal("0.10")}
NOTES = {
    "assumption": "Decimal hours as written (11.45h=11:27:00, 12.01h=12:00:36); the order is a regulation "
                  "(base 250, per_km 200, commission 10%) both platforms match by policy_change at t=12h; "
                  "'dispatched' means an accepted offer (assignment).",
    "approximation": "Corridor demand is explicit point-to-point trips, not a zone/corridor generator "
                      "(phase 2); world.speed_kmh, driver acceptance_bias and rider purchase_bias/"
                      "taste_scale/cancellation_after_seconds are tuned (fixture_cross_platform_queue.py's "
                      "determinism levers) so seed 0 reliably straddles t=12h.",
    "source": "plans/scenario-readiness-plan.md section 10 (S8)",
}


def _background_trips():
    return [
        trip(f"bg-trip-{i}", rider=f"rider-{i}", at_hours=(i * 24 / RIDERS) % 24,
             origin=(1 + (i * 3) % 9, 1 + (i * 7) % 9), destination=(1 + (i * 5) % 9, 1 + (i * 2) % 9))
        for i in range(1, RIDERS + 1)
    ]


def _background_shifts():
    return [
        entry for i in range(1, DRIVERS + 1)
        for entry in (
            shift(f"bg-shift-{i}-am", driver=f"driver-{i}", at_hours=0, hours=11,
                 location=(1 + (i * 4) % 9, 1 + (i * 6) % 9)),
            shift(f"bg-shift-{i}-pm", driver=f"driver-{i}", at_hours=12.5, hours=11.5,
                 location=(1 + (i * 4) % 9, 1 + (i * 6) % 9)),
        )
    ]


def build():
    boundary_trips = [
        trip("trip-a", rider="boundary-a", at_hours=11.45, origin=(1, 1), destination=(9, 1)),
        trip("trip-b", rider="boundary-b", at_hours=11.58, origin=(1, 1), destination=(9, 1)),
        trip("trip-c", rider="boundary-c", at_hours=12.01, origin=(1, 1), destination=(9, 1)),
    ]
    boundary_shifts = [
        shift("boundary-shift-a", driver="boundary-driver-a", at_hours=11, hours=2, location=(1, 9)),
        shift("boundary-shift-b", driver="boundary-driver-b", at_hours=11, hours=2, location=(9, 9)),
        shift("boundary-shift-c", driver="boundary-driver-c", at_hours=11.9, hours=1, location=(1, 1)),
    ]
    pinned_rider = {"purchase_bias": 8, "taste_scale": 0, "cancellation_after_seconds": 3600}
    pinned_driver = {"acceptance_bias": 15}
    return two_platform(
        "apex", "zenith", riders=RIDERS, drivers=DRIVERS, horizon_hours=24,
        shared={"base_fare_minor": PRE_SHOCK["base_fare_minor"], "per_km_minor": PRE_SHOCK["per_km_minor"],
               "commission_fraction": float(PRE_SHOCK["commission_fraction"])},
    ).with_changes({
        "world.speed_kmh": 15,
        "activity.trips": {"generator": "explicit", "items": _background_trips() + boundary_trips},
        "activity.shifts": {"generator": "explicit", "items": _background_shifts() + boundary_shifts},
        "notes": NOTES,
    }).add(
        "population.riders.people", person("boundary-a", segment="both-apps", rider=pinned_rider)
    ).add(
        "population.riders.people", person("boundary-b", segment="both-apps", rider=pinned_rider)
    ).add(
        "population.riders.people", person("boundary-c", segment="both-apps", rider=pinned_rider)
    ).add(
        "population.drivers.people", person("boundary-driver-a", segment="both-apps", driver=pinned_driver)
    ).add(
        "population.drivers.people", person("boundary-driver-b", segment="both-apps", driver=pinned_driver)
    ).add(
        "population.drivers.people", person("boundary-driver-c", segment="both-apps", driver=pinned_driver)
    ).add(
        "interventions", policy_change("apex-cap", at_hours=12, platform="apex", version="municipal-order-v1",
                                       parameters={k: (float(v) if k == "commission_fraction" else v)
                                                   for k, v in POST_SHOCK.items()})
    ).add(
        "interventions", policy_change("zenith-cap", at_hours=12, platform="zenith", version="municipal-order-v1",
                                       parameters={k: (float(v) if k == "commission_fraction" else v)
                                                   for k, v in POST_SHOCK.items()})
    )


def _order_for(after, source_id):
    """The completed order (or, failing that, the first attempted order) whose intent has this source_id."""
    orders = []
    for intent in after["intents"].values():
        if intent["source_id"] == source_id:
            orders = [after["orders"][order_id] for order_id in intent["order_ids"]]
            break
    completed = [o for o in orders if o["state"] == "completed"]
    return (completed[0] if completed else orders[0]) if orders else None


def _payout_minor(order):
    payout = order["assignment"]["payout"]
    return payout["payout_minor"] + payout["bonus_minor"]


def evaluate(header, initial, final, footer):
    checks = Checks("s08-regulatory-price-cap")
    before, after = _tables(initial), _tables(final)

    order_a = _order_for(after, "trip-a")
    if order_a is None or order_a["state"] != "completed":
        checks.not_evaluable("s8.1 trip A settles on pre-shock terms",
                             "trip-a's order was never dispatched to completion at this seed")
    else:
        gross, payout = order_a["fare"]["gross_minor"], _payout_minor(order_a)
        distance = math.dist(order_a["pickup"], order_a["destination"])
        rate = (gross - PRE_SHOCK["base_fare_minor"]) / distance
        commission = 1 - Decimal(payout) / Decimal(gross)
        ok = (order_a["timeline"]["completed"] > NOON and gross == 3400 and payout == 2550
              and abs(rate - 400) < 1e-9 and commission == Decimal("0.25"))
        checks.verdict("s8.1 trip A settles on pre-shock terms", ok,
                       f"completed at {order_a['timeline']['completed']:.1f}s (> {NOON}); "
                       f"gross/payout/contribution = {gross}/{payout}/{gross - payout} (expected 3400/2550/850); "
                       f"implied rate {rate:g}/km (expected 400), commission {commission} (expected 0.25)")

    order_b = _order_for(after, "trip-b")
    if order_b is None or order_b["state"] != "completed":
        checks.not_evaluable("s8.2 trip B accepted before, picked up after",
                             "trip-b's order was never dispatched to completion at this seed")
    else:
        gross, payout = order_b["fare"]["gross_minor"], _payout_minor(order_b)
        assigned, boarded = order_b["timeline"]["assigned"], order_b["timeline"]["boarded"]
        ok = assigned < NOON < boarded and gross == 3400 and payout == 2550
        checks.verdict("s8.2 trip B accepted before, picked up after", ok,
                       f"assigned={assigned:.1f}s < {NOON} < boarded={boarded:.1f}s; "
                       f"gross/payout = {gross}/{payout} (expected 3400/2550, pre-shock terms frozen at acceptance)")

    orders_by_quote = {order["quote_id"]: order for order in after["orders"].values()}
    post_shock_quotes = [q for q in after["quotes"].values() if q["at"] >= NOON]
    violations = []
    for quote in post_shock_quotes:
        cap = rounded(Decimal(POST_SHOCK["base_fare_minor"])
                      + Decimal(POST_SHOCK["per_km_minor"]) * Decimal(str(quote["distance_km"])))
        if quote["fare"]["gross_minor"] > cap:
            violations.append(("gross-cap", quote["id"], quote["fare"]["gross_minor"], cap))
            continue
        order = orders_by_quote.get(quote["id"])
        if order is None or order["assignment"] is None:
            continue
        min_payout = rounded(Decimal(quote["fare"]["gross_minor"]) * Decimal("0.90")) - 1
        if _payout_minor(order) < min_payout:
            violations.append(("commission-floor", quote["id"], _payout_minor(order), min_payout))
    checks.verdict("s8.3 post-shock quotes respect the cap", not violations,
                   f"0 of {len(post_shock_quotes)} quotes at/after t={NOON} exceed gross<=round(250+200*km) or "
                   f"pay below round(gross*0.90)-1" + (f"; violations {violations[:3]}" if violations else ""))

    new_settlements = _new_settlements(before, after)
    negative = [s for s in new_settlements if s["platform_contribution_minor"] < 0]
    breakdown = settlement_breakdown(initial, final)
    checks.verdict("s8.4 no negative platform margin",
                   not negative and breakdown["totals"]["residual_minor"] == 0,
                   f"0 of {len(new_settlements)} settlements have platform_contribution_minor < 0; "
                   f"settlement_breakdown totals residual_minor={breakdown['totals']['residual_minor']}"
                   + (f"; negative examples {negative[:3]}" if negative else ""))
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
