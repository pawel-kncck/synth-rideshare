"""S10 -- Three-Way Platform War with Endogenous Solvency and Driver Vehicle Debt (scenario-reviews/10-*).

Market: market-blank@1 with three platform() platforms -- "burn" (0%
commission, a 50%-off-every-ride campaign, so it pays full driver payout
while collecting half fare), "corp" (20% commission, baseline) and "coop"
(5% commission) -- $2.50 base + $1.20/km on all three, one segment with all
three apps installed (riders "prioritize price above all else", so
preferred_app="burn"). 200 riders/40 drivers, 2-day weekly demand.

Approximations (recorded in `notes` below too): starting cash reserves,
the $0/-$100 driver debt/bankruptcy threshold, automatic shutdown at
balance <= 0, daily lease deductions and the Coop weekly dividend all need
a ledger, `Transfer` records and shift-extension hooks (plan sections
4.A/4.G, phases 3 and 6) that do not exist yet, so checks 2, 3 and 4 are
NOT-EVALUABLE. What is measurable today is the per-ride burn identity
already guaranteed by Settlement's own fields, plus the resulting
cumulative burn series -- check 1.
"""
from harness import cli
from metrics import Checks, _new_settlements, _tables
from scenario import Scenario, campaign, platform, segment

INSOLVENCY_FLOOR = -200000  # minor units ($2,000), plan section 10 (S10)
NOTES = {
    "assumption": "'unit' is one kilometre; Burn/Corp/Coop all charge $2.50 base + $1.20/km before their own "
                  "commission/subsidy; every rider and driver already has all three apps (plan section 10, S10).",
    "approximation": "Starting cash, the driver debt/bankruptcy threshold, automatic shutdown and the Coop "
                      "dividend all need a ledger and Transfer records (plan sections 4.A/4.G, phases 3/6); "
                      "not modelled here. Only the per-ride burn identity is checked.",
    "source": "plans/scenario-readiness-plan.md section 7 (S10 row) and section 10",
}


def build():
    fares = {"base_fare_minor": 250, "per_km_minor": 120}
    platforms = (platform("burn", commission_fraction=0, **fares)
                | platform("corp", commission_fraction=.2, **fares)
                | platform("coop", commission_fraction=.05, **fares))
    apps = ("burn", "corp", "coop")
    return Scenario(preset="market-blank@1", name="s10-platform-solvency-and-driver-debt").with_changes({
        "world.horizon_hours": 48,
        "population.riders.count": 200, "population.drivers.count": 40,
        "platforms": platforms,
        "population.riders.segments": {"all-three": segment(weight=1, apps=apps, preferred_app="burn")},
        "population.drivers.segments": {"all-three": segment(weight=1, apps=apps, preferred_app="burn", driver={})},
        "activity.trips": {"generator": "weekly", "trips_per_rider": 6, "duration_hours": 48, "peaks": [],
                           "off_peak_weight": 1, "assignment": "round_robin"},
        "activity.shifts": {"generator": "rotation", "crews": 1, "shift_hours": 20, "days": 2, "first_start_hours": 0},
        "notes": NOTES,
    }).add(
        "platforms.burn.policy.campaigns", campaign("burn-subsidy", start_hours=0, end_hours=48, discount_fraction=.5)
    )


def evaluate(header, initial, final, footer):
    checks = Checks("s10-platform-solvency-and-driver-debt")
    before, after = _tables(initial), _tables(final)
    burn = sorted((s for s in _new_settlements(before, after) if s["platform_id"] == "burn"), key=lambda s: s["at"])
    bad = [s["id"] for s in burn
          if s["platform_contribution_minor"] != s["rider_payment_minor"] - s["driver_payout_minor"]]
    cumulative, running = [], 0
    for s in burn:
        running += s["platform_contribution_minor"]
        cumulative.append((s["at"], running))
    end_seconds = final["scheduler"]["clock_seconds"] - initial["scheduler"]["clock_seconds"]
    rate_per_hour = running / (end_seconds / 3600) if end_seconds and running < 0 else None
    projected_hours = INSOLVENCY_FLOOR / rate_per_hour if rate_per_hour else None
    checks.verdict("s10.1 per-ride burn identity: contribution == rider_payment - driver_payout", not bad,
                   f"0 of {len(burn)} Burn settlements violate the identity; cumulative contribution ends at "
                   f"{running} minor over {end_seconds / 3600:.1f}h"
                   + (f"; at this rate it would cross {INSOLVENCY_FLOOR} around hour {projected_hours:.1f}"
                      if projected_hours else " (never negative at this seed/duration)")
                   + (f"; violations {bad[:3]}" if bad else ""))

    checks.not_evaluable("s10.2 Burn shuts down instantly and gracefully at balance <= $0",
                         "no cash balance or shutdown mechanism exists yet (plan sections 4.A/4.G, phases 3/6)")
    checks.not_evaluable("s10.3 drivers below -$100 are permanently deactivated",
                         "no driver ledger or bankruptcy/repossession mechanism exists yet (plan section 4.A, phase 3)")
    checks.not_evaluable("s10.4 Coop's weekly dividend moves cash without touching trip-level pricing",
                         "no ledger or Transfer/dividend mechanism exists yet (plan section 4.A, phase 3)")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
