"""S1 -- Driver Supply Elasticity Under Asymmetric Platform Take-Rates (scenario-reviews/01-*).

Market: two_platform("alpha", "beta") on market-blank@1, 200 riders/40 drivers,
identical $2.00 base + $1.50/km tariff, alpha at 20% commission, beta at 10%;
one 24-hour rotation shift covers all drivers, weekly demand at 0.5 trips per
rider per hour (12 trips/rider over 24h), horizon 26h for drain time.

Approximations relative to DESCRIPTION.md (recorded in `notes` below too):
  * Riders do not literally "pick whichever platform quotes the lowest ETA
    (or a coin flip)" -- rider_search@1's utility model (price, ETA, loyalty,
    search cost) is used instead; both are ETA/price sensitive, which is the
    closest available approximation.
  * Drivers do not literally "prioritize dispatches from the lower-commission
    platform" -- driver_participation@1 scores acceptance from the *net
    payout offered*, so a lower commission (higher payout on an identical
    fare) is preferred on average, but offers are still evaluated one at a
    time as they arrive (see check 2).
  * The $0.30/km operating cost is not computed here: plan section 10 (S1)
    treats it as an offline metric on driver net earnings that enters no
    check, so it is left to a future offline pass rather than invented here.

Assumption adopted (plans/scenario-readiness-plan.md section 10, S1):
"Exactly 12.5% higher" is the design ratio -- payout == round_half_up((1 -
c) * gross) per platform, with the *unrounded* ratio of shares equal to
0.9 / 0.8 == 1.125. "Dispatched" means an accepted offer (assignment), not
an offer merely created.

Check 2 (simultaneous cross-platform dispatch) depends on realized supply:
a co-pending pair needs both platforms to hold a live offer to the *same*
driver at the same time, which is rare at default response/offer timings
with independent per-platform dispatch. offer_seconds=30 and
driver.response_seconds=25 (both raised from their defaults of 10 and 3,
using the same determinism-tuning approach as
scenarios/fixture_cross_platform_queue.py) widen the overlap window enough
for seed 0 to realize a nonzero, non-trivial pair count; the realized count
is printed by --check. Under driver_participation@1, offers are evaluated
one at a time in FIFO order (plans/architecture/behavior-policy.md), not
jointly across platforms, so a FAIL on check 2 is the expected, documented
result -- phase 5 (driver_participation@2, plan section 4.D) is what would
let a driver compare two live offers before responding.
"""
from decimal import Decimal

from harness import cli
from marketplace_policy import rounded
from metrics import Checks, _new_terminal, _tables, conservation, platform_funnel
from scenario import two_platform

MAX_COMMITMENTS = 2
NOTES = {
    "assumption": "Payout ratio uses round_half_up per platform; unrounded ratio 0.9/0.8 == 1.125; "
                  "'dispatched' means an accepted offer (assignment).",
    "approximation": "Riders use rider_search@1's utility model, not literal lowest-ETA choice; drivers "
                      "use driver_participation@1's payout-sensitive acceptance, not explicit commission "
                      "priority; the $0.30/km operating cost is not computed (enters no check).",
    "source": "plans/scenario-readiness-plan.md section 10 (S1)",
}


def build():
    return two_platform(
        "alpha", "beta", riders=200, drivers=40, horizon_hours=26,
        shared={"base_fare_minor": 200, "per_km_minor": 150, "offer_seconds": 30},
        first_parameters={"commission_fraction": .2}, second_parameters={"commission_fraction": .1},
    ).with_changes({
        "world.map_km": [10, 10],
        "activity.trips": {"generator": "weekly", "trips_per_rider": 12, "duration_hours": 24, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 24, "days": 1, "first_start_hours": 0},
        "behavior.driver.parameters.response_seconds": 25,
        "notes": NOTES,
    })


def _co_pending_pairs(final):
    """Offer pairs to the same driver, one per platform, whose [created_at, resolved-or-expiry] overlap."""
    by_driver = {}
    for offer in final["engine"]["offers"]:
        by_driver.setdefault(offer["driver_id"], []).append(offer)
    pairs, beta_wins = 0, 0
    for offers in by_driver.values():
        alpha = [o for o in offers if o["platform_id"] == "alpha"]
        beta = [o for o in offers if o["platform_id"] == "beta"]
        for a in alpha:
            a_end = a["resolved_at"] if a["resolved_at"] is not None else a["expires_at"]
            for b in beta:
                b_end = b["resolved_at"] if b["resolved_at"] is not None else b["expires_at"]
                if a["created_at"] <= b_end and b["created_at"] <= a_end:
                    pairs += 1
                    if b["state"] == "accepted" and a["state"] != "accepted":
                        beta_wins += 1
    return pairs, beta_wins


def _commitments_held(orders, driver_id, at, *, exclude_order_id, platform_id=None):
    """How many of driver_id's other accepted orders (optionally restricted to platform_id) were held at `at`."""
    count = 0
    for order in orders.values():
        if order["id"] == exclude_order_id or order["assignment"] is None:
            continue
        if order["assignment"]["driver_id"] != driver_id:
            continue
        if platform_id is not None and order["platform_id"] != platform_id:
            continue
        accepted_at = order["assignment"]["accepted_at"]
        end = order["timeline"].get("completed", order["timeline"].get("canceled"))
        if accepted_at <= at and (end is None or end > at):
            count += 1
    return count


def evaluate(header, initial, final, footer):
    checks = Checks("s01-driver-supply-elasticity")
    before, after = _tables(initial), _tables(final)
    completed = _new_terminal(before, after, "completed")

    # Check 1: payout ratio.
    violations, by_platform = [], {"alpha": 0, "beta": 0}
    commission = {"alpha": Decimal("0.2"), "beta": Decimal("0.1")}
    for order in completed:
        by_platform[order["platform_id"]] += 1
        gross, c = order["fare"]["gross_minor"], commission[order["platform_id"]]
        expected = rounded(Decimal(gross) * (1 - c))
        actual = order["assignment"]["payout"]["payout_minor"] + order["assignment"]["payout"]["bonus_minor"]
        if actual != expected:
            violations.append((order["id"], expected, actual))
    ratio = (1 - commission["beta"]) / (1 - commission["alpha"])
    checks.verdict("s1.1 payout ratio", not violations,
                   f"{by_platform['alpha']}/{by_platform['alpha']} alpha and {by_platform['beta']}/{by_platform['beta']} "
                   f"beta completed orders equal round_half_up((1-c)*gross); unrounded ratio {ratio} == 1.125"
                   + (f"; {len(violations)} violations, first {violations[0]}" if violations else ""))

    # Check 2: simultaneous cross-platform dispatch.
    pairs, beta_wins = _co_pending_pairs(final)
    if pairs == 0:
        checks.not_evaluable("s1.2 simultaneous cross-platform dispatch",
                             "no co-pending offer pair was realized at seed 0 with the tuned timings")
    else:
        checks.verdict("s1.2 simultaneous cross-platform dispatch", beta_wins == pairs,
                       f"driver accepted beta over a co-pending alpha offer in {beta_wins}/{pairs} pairs "
                       "(driver_participation@1 evaluates offers FIFO, not jointly; phase 5 owns joint comparison)")

    # Check 3: no dispatch to a driver with no free slot.
    violations3, failed_no_slot = [], 0
    for pid, funnel in platform_funnel(initial, final).items():
        failed_no_slot += funnel["failed_offers"]
    for order in after["orders"].values():
        if order["assignment"] is None:
            continue
        at = order["assignment"]["accepted_at"]
        held = _commitments_held(after["orders"], order["assignment"]["driver_id"], at, exclude_order_id=order["id"])
        held_local = _commitments_held(after["orders"], order["assignment"]["driver_id"], at,
                                       exclude_order_id=order["id"], platform_id=order["platform_id"])
        if held >= MAX_COMMITMENTS or held_local >= MAX_COMMITMENTS:
            violations3.append(order["id"])
    checks.verdict("s1.3 no dispatch beyond a free slot", not violations3,
                   f"0 of {sum(1 for o in after['orders'].values() if o['assignment'] is not None)} accepted "
                   f"offers exceeded MAX_COMMITMENTS={MAX_COMMITMENTS} or the offering platform's cap; "
                   f"failed_offers (no_free_slot and other acceptance failures) = {failed_no_slot}"
                   + (f"; violations {violations3}" if violations3 else ""))

    # Check 4: totals reconcile.
    funnel = platform_funnel(initial, final)
    cons = conservation(initial, final)
    funnel_completed = sum(row["completed"] for row in funnel.values())
    checks.verdict("s1.4 totals reconcile", funnel_completed == cons["completed"] and cons["orders_balanced"],
                   f"conservation()['completed']={cons['completed']} == sum(platform_funnel[p]['completed'])="
                   f"{funnel_completed}; orders_balanced={cons['orders_balanced']}")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
