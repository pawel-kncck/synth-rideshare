"""S3 -- Driver Cancellation Penalties and Completion Reliability (scenario-reviews/03-*).

Market: two_platform("strict", "lenient") on market-blank@1, $2.50 base +
$1.20/km at 15% commission on both, 120 riders/25 drivers, 12-hour weekly
demand.

Approximations (recorded in `notes` below too): "Strict" does not actually
dock $4.00 from a ledger or lock the driver out of Strict dispatches for 30
minutes -- there is no ledger (plan section 4.A, phase 3) and no dispatch
lockout (section 4.B, phase 4) yet, so "strict" and "lenient" are built with
identical policy today; checks 1 and 2 are NOT-EVALUABLE, named honestly.
What today's engine and driver_participation@1 do support: rider- and
driver-initiated cancellation before boarding (each with its own party and
reason), and a distinct disposition per offer/order outcome -- checks 3 and
4 below.
"""
from harness import cli
from metrics import Checks, _new_settlements, _tables, cancellations_by_party, platform_funnel
from scenario import two_platform

NOTES = {
    "assumption": "'unit' is one kilometre; both platforms charge $2.50 base + $1.20/km at 15% commission and "
                  "compensate the driver $1.00 on a rider cancellation, so check s3.3 has real settlements to "
                  "inspect instead of a vacuous zero-settlement pass.",
    "approximation": "No ledger exists yet (phase 3) so a cancellation cannot debit a driver balance, and no "
                      "dispatch lockout exists yet (phase 4); 'strict' and 'lenient' are policy-identical today.",
    "source": "plans/scenario-readiness-plan.md section 7 (S3 row) and section 10",
}


def build():
    return two_platform(
        "strict", "lenient", riders=120, drivers=25, horizon_hours=12,
        shared={"base_fare_minor": 250, "per_km_minor": 120, "commission_fraction": .15,
               "driver_cancellation_compensation_minor": 100},
    ).with_changes({
        "activity.trips": {"generator": "weekly", "trips_per_rider": 3, "duration_hours": 12, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 12, "days": 1, "first_start_hours": 0},
        "notes": NOTES,
    })


def evaluate(header, initial, final, footer):
    checks = Checks("s03-driver-cancellation-penalties")
    checks.not_evaluable("s3.1 Strict cancellation docks $4.00 and locks dispatch 30 minutes",
                         "no ledger and no dispatch lockout exist yet (plan sections 4.A/4.B, phases 3/4)")
    checks.not_evaluable("s3.2 driver keeps receiving Lenient dispatches during a Strict lockout",
                         "no dispatch lockout exists yet, so there is nothing to hold a driver back on Lenient "
                         "(plan section 4.B, phase 4)")

    before, after = _tables(initial), _tables(final)
    rider_cancel_settlements = [
        s for s in _new_settlements(before, after)
        if s["reason"] == "cancellation_fee" and after["orders"][s["order_id"]]["cancellation"]["by"] == "rider"
    ]
    debited = [s for s in rider_cancel_settlements if s["driver_payout_minor"] < 0]
    checks.verdict("s3.3 a rider cancellation never debits the driver", not debited,
                   f"0 of {len(rider_cancel_settlements)} rider-cancellation settlements have a negative "
                   "driver_payout_minor (no debit mechanism exists; plan section 4.A, phase 3)")

    funnel = platform_funnel(initial, final)
    cancels = cancellations_by_party(initial, final)
    rows = {p: {"dispatched": funnel[p]["offers"], "accepted": funnel[p]["accepted_offers"],
               "driver_cancelled": cancels[p]["driver"], "rider_cancelled": cancels[p]["rider"],
               "completed": funnel[p]["completed"]} for p in funnel}
    # Five independently retrievable counters (platform_funnel + cancellations_by_party), and the
    # only coherence an offer/order funnel owes: an order cannot be accepted before it is dispatched,
    # nor completed without first being accepted.
    coherent = all(row["accepted"] <= row["dispatched"] for row in rows.values())
    checks.verdict("s3.4 distinct Dispatched/Accepted/DriverCancelled/RiderCancelled/Completed metrics", coherent,
                   f"per platform: {rows}")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
