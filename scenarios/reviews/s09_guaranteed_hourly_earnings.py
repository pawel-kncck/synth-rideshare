"""S9 -- Cross-App Exploitation of Guaranteed Hourly Driver Earnings (scenario-reviews/09-*).

Market: two_platform("steady", "flash") on market-blank@1, $2.50 base +
$1.20/km at 20% commission on both, 150 riders/30 drivers, 16-hour weekly
demand.

Approximations (recorded in `notes` below too): the $30/hour guarantee's
top-up settlement is itself postable since phase 3 (`post_transfer(reason=
"guarantee_topup", ...)`, plan section 4.A), but the 90%-acceptance-rate
condition over a rolling 60-minute window needs the windowed observe hook
(section 4.B, phase 4) that does not exist yet, and this scenario has no
policy that computes or posts a top-up; the strategic-driver exploit
(idling on Steady in a low-demand cell while sniping Flash surge) needs the
same participant policy v2 hooks as S1/S2 (section 4.D, phase 5). Checks 1
and 4 (the guarantee condition and its top-up) are NOT-EVALUABLE. What
today's engine already enforces structurally -- one physical service per
driver at a time, and a complete, mutually exclusive offer-disposition
partition -- answers checks 2 and 3's non-strategic half.
"""
from harness import cli
from metrics import Checks, OFFER_OUTCOME_KEYS, platform_funnel
from scenario import two_platform

NOTES = {
    "assumption": "'unit' is one kilometre; both platforms keep 20% commission (DESCRIPTION.md prices the guarantee, not the split).",
    "approximation": "The $30/hr guarantee's windowed acceptance-rate condition needs the phase-4 observe hook "
                      "(plan section 4.B); its top-up settlement is postable since phase 3 (section 4.A) but no "
                      "policy here computes or posts one. The strategic idle-and-snipe exploit's mechanism now "
                      "exists as driver_participation@2's availability trait (section 4.D, AST-209), "
                      "demonstrated in scenarios/fixture_v2_availability.py; this script keeps @1.",
    "source": "plans/scenario-readiness-plan.md section 7 (S9 row) and section 10",
}


def build():
    return two_platform(
        "steady", "flash", riders=150, drivers=30, horizon_hours=16,
        shared={"base_fare_minor": 250, "per_km_minor": 120, "commission_fraction": .2},
    ).with_changes({
        "activity.trips": {"generator": "weekly", "trips_per_rider": 4, "duration_hours": 16, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 16, "days": 1, "first_start_hours": 0},
        "notes": NOTES,
    })


def evaluate(header, initial, final, footer):
    checks = Checks("s09-guaranteed-hourly-earnings")
    checks.not_evaluable("s9.1 Steady's 60-minute acceptance windows and organic-earnings top-up",
                         "the windowed observe hook (60-minute acceptance-rate condition) does not exist yet "
                         "(plan section 4.B, phase 4); the top-up settlement itself is postable since phase 3 "
                         "(section 4.A), but nothing here computes when to post one")

    overlaps = []
    for driver_id in final["engine"]["drivers"]:
        services = sorted((s for s in final["engine"]["services"] if s["driver_id"] == driver_id["id"]),
                          key=lambda s: s["started_at"])
        for prev, nxt in zip(services, services[1:]):
            end = prev["ended_at"] if prev["ended_at"] is not None else float("inf")
            if nxt["started_at"] < end:
                overlaps.append((driver_id["id"], prev["id"], nxt["id"]))
    checks.verdict("s9.2 a driver never runs two simultaneous physical services", not overlaps,
                   f"0 of {len(final['engine']['services'])} services overlap another service of the same driver "
                   "(the strategic-hiding half -- physically positioning where Steady's algorithm cannot find "
                   "them -- is driver_participation@2's availability trait, plan section 4.D, AST-209, "
                   "demonstrated in scenarios/fixture_v2_availability.py; this script keeps @1)"
                   + (f"; overlaps {overlaps[:3]}" if overlaps else ""))

    funnel = platform_funnel(initial, final)
    mismatched = [p for p, row in funnel.items() if sum(row[k] for k in OFFER_OUTCOME_KEYS.values()) != row["offers"]]
    checks.verdict("s9.3 every offer is pending or has exactly one disposition (includes expired)", not mismatched,
                   f"per platform offers vs. disposition sum: "
                   f"{ {p: (row['offers'], row['expired_offers']) for p, row in funnel.items()} } "
                   "(disqualifying a driver from that hour's top-up on a timeout needs the phase-4 windowed "
                   "observe hook, not the ledger -- the top-up Transfer itself is postable since phase 3)")

    checks.not_evaluable("s9.4 top-up subsidies are a separate ledger entry from trip fare earnings",
                         "the ledger exists and settlement_breakdown's transfers block is populated since phase 3 "
                         "(plan section 4.A), separating any top-up from trip-fare settlements already -- but "
                         "nothing in this scenario posts a guarantee_topup Transfer, because the windowed "
                         "acceptance-rate condition that would trigger one needs the phase-4 observe hook "
                         "(section 4.B)")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
