"""A deterministic explicit market: one driver serves a Rebu ride while accepting a Blot order.

Rider r1 uses Rebu only; rider r2 uses Blot only. The driver has both apps and
opens Blot after the no-offer threshold. Blot's request arrives while the Rebu
passenger is onboard, so Blot's ETA is estimated from the driver's current
position without knowledge of the remaining Rebu ride. The printout compares
Blot's predictions with the actual pickup wait. Decision randomness is removed
by fixed tastes and a saturating acceptance bias.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import run_scenario
from scenario import Scenario, person, shift, trip

scenario = Scenario(preset="three-platform-day@1", name="fixture-cross-platform-queue").with_changes({
    "world.horizon_hours": 1,
    "population.riders.count": 0,
    "population.drivers.count": 0,
    "evolution.checkpoint_hours": None,
    "activity.shifts": {"generator": "explicit", "items": [
        shift("d1-shift", driver="d1", at_hours=0, hours=1, location=(0, 0))]},
    "activity.trips": {"generator": "explicit", "items": [
        trip("rebu-ride", rider="r1", at_hours=60 / 3600, origin=(0, 0), destination=(0, 5)),
        trip("blot-ride", rider="r2", at_hours=340 / 3600, origin=(.889, 3.208), destination=(5, 3.2))]},
})
for role in ("riders", "drivers"):
    for name in ("rebu-first", "blot-first", "flyt-first", "rebu-only"):
        scenario = scenario.remove(f"population.{role}.segments", name)
scenario = (
    scenario
    .add("population.riders.people", person("r1", apps=("rebu",), preferred_app="rebu",
                                            rider={"taste_scale": 0, "purchase_bias": 5}))
    .add("population.riders.people", person("r2", apps=("blot",), preferred_app="blot",
                                            rider={"taste_scale": 0, "purchase_bias": 5, "cancellation_after_seconds": 900}))
    .add("population.drivers.people", person("d1", apps=("rebu", "blot"), preferred_app="rebu",
                                             driver={"acceptance_bias": 10, "no_offer_seconds": 30}))
)

if __name__ == "__main__":
    sim = run_scenario(scenario, seed=0)
    for order in sim.engine.orders.values():
        predictions = [(p["source"], round(p["at"]), round(p["eta_seconds"])) for p in order.eta_predictions]
        arrived = order.timeline.get("arrived")
        wait = None if arrived is None else round(arrived - order.created_at)
        print(f"{order.platform_id} order {order.id}: {order.state}, created {order.created_at:.0f}s, "
              f"predictions {predictions}, actual pickup wait {wait}s")
