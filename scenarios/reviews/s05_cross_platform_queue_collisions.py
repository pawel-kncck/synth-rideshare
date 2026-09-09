"""S5 -- Cross-Platform Chained Dispatching and Queue Collisions (scenario-reviews/05-*).

Market: a deterministic explicit fixture modelled on
scenarios/fixture_cross_platform_queue.py, renamed Blue/Green: one driver
(d1, both apps), three explicit trips (r1 Blue, r2 Green while d1 is
mid-Blue-ride, r3 Blue while d1 is mid-Green-ride), decision randomness
removed the same way (taste_scale=0, saturating purchase/acceptance bias).
This reliably realizes the queue-promotion collision, unlike S1 check 2's
emergent (and often vacuous) co-pending pairs.

Approximations (`notes` below): drivers disclosing "queue full" across
platforms (`pause_when_full`) does not exist yet -- today a platform simply
cannot see a competitor's commitments at all, a *stronger* boundary than
the review assumes (plan section 5), so the check as literally asked
(Blue observing Green's queue slot) is NOT-EVALUABLE; check 1 (the capacity
cap itself) is fully answerable instead. `pickup_eta_revised` is emitted
(marketplace-engine.md) but rider_search@1 does not consume it.
"""
from harness import cli
from metrics import Checks
from scenario import person, shift, trip, two_platform

MAX_COMMITMENTS = 2
NOTES = {
    "assumption": "Blue and Green are policy-identical two_platform() defaults; only the fixture's explicit "
                  "trips create the collision.",
    "approximation": "No platform ever sees a competitor's queue state (a stronger boundary than "
                      "'pause_when_full' disclosure, which does not exist yet -- plan section 5, phase 5); "
                      "pickup_eta_revised is logged but not consumed by riders (behavior-policy.md, phase 5).",
    "source": "plans/scenario-readiness-plan.md section 7 (S5 row) and section 10",
}


def build():
    return two_platform("blue", "green", riders=0, drivers=0, horizon_hours=1).with_changes({
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d1-shift", driver="d1", at_hours=0, hours=1, location=(0, 0))]},
        "activity.trips": {"generator": "explicit", "items": [
            trip("blue-ride", rider="r1", at_hours=60 / 3600, origin=(0, 0), destination=(0, 5)),
            trip("green-ride", rider="r2", at_hours=340 / 3600, origin=(.889, 3.208), destination=(5, 3.2)),
            trip("blue-ride-2", rider="r3", at_hours=800 / 3600, origin=(0, 5), destination=(0, 9)),
        ]},
        "notes": NOTES,
    }).add(
        "population.riders.people", person("r1", apps=("blue",), preferred_app="blue",
                                           rider={"taste_scale": 0, "purchase_bias": 5})
    ).add(
        "population.riders.people", person("r2", apps=("green",), preferred_app="green",
                                           rider={"taste_scale": 0, "purchase_bias": 5, "cancellation_after_seconds": 900})
    ).add(
        "population.riders.people", person("r3", apps=("blue",), preferred_app="blue",
                                           rider={"taste_scale": 0, "purchase_bias": 5, "cancellation_after_seconds": 900})
    ).add(
        "population.drivers.people", person("d1", apps=("blue", "green"), preferred_app="blue",
                                            driver={"acceptance_bias": 10, "no_offer_seconds": 30})
    )


def evaluate(header, initial, final, footer):
    checks = Checks("s05-cross-platform-queue-collisions")
    orders = list(final["engine"]["orders"])
    events = []
    for order in orders:
        if order["assignment"] is None:
            continue
        end = order["timeline"].get("completed", order["timeline"].get("canceled"))
        events.append((order["assignment"]["accepted_at"], 1, order["assignment"]["driver_id"]))
        events.append((end if end is not None else float("inf"), -1, order["assignment"]["driver_id"]))
    events.sort(key=lambda e: (e[0], -e[1]))
    held = {}
    peak = 0
    for _, delta, driver_id in events:
        held[driver_id] = held.get(driver_id, 0) + delta
        peak = max(peak, held[driver_id])
    checks.verdict("s5.1 never more than 1 active + 1 queued commitment", peak <= MAX_COMMITMENTS,
                   f"peak simultaneous accepted-but-unfinished orders for any driver = {peak} "
                   f"(MAX_COMMITMENTS={MAX_COMMITMENTS}); {len(orders)} orders total")
    checks.not_evaluable("s5.2 both platforms register a driver's queue slot as locked",
                         "a platform never observes a competitor's commitments at all -- a stronger boundary "
                         "than the disclosure mechanism this asks for (plan section 5; pause_when_full is phase 5)")

    services = sorted((s for s in final["engine"]["services"] if s["driver_id"] == "d1"), key=lambda s: s["started_at"])
    mismatches = [(p["id"], n["id"], p["end_position"], n["legs"][0]["origin"]) for p, n in zip(services, services[1:])
                 if p["end_position"] is not None and list(p["end_position"]) != list(n["legs"][0]["origin"])]
    checks.verdict("s5.3 next pickup leg starts at the previous drop-off position", not mismatches,
                   f"0 of {max(0, len(services) - 1)} consecutive d1 service pairs mismatch "
                   + (f"; mismatches {mismatches}" if mismatches else "(each queued pickup originates exactly "
                                                                       "where the prior ride actually ended)"))
    checks.not_evaluable("s5.4 Green's cancellation logic re-evaluates live projected ETA",
                         "rider_search@1 does not consume pickup_eta_revised notifications yet "
                         "(behavior-policy.md; plan section 4.D/4.E, phase 5)")
    return checks


if __name__ == "__main__":
    cli(__doc__, build, evaluate)
