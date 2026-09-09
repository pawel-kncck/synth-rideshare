"""rider_search@2 choice_rule='lexicographic' (AST-209, plan section 4.E; S2).

One multi-homing rider requests a 4 km trip. cheap (base_fare_minor=120, per_km_minor=150) quotes
gross_minor=720 with its only driver 5 km from pickup (long ETA); dear (base_fare_minor=300, same
per_km) quotes gross_minor=900 with its driver adjacent to pickup (near-zero ETA). With
choice_keys=[('price', 50), ('eta', 0)] and compare_all_apps=True, the rider visits both apps and
then chooses lexicographically: price first, with a 50-minor tolerance -- the 180-minor gap
between 720 and 900 is well outside that tolerance, so cheap wins outright despite its much longer
ETA.

--equal-prices puts dear on the same 120 base fare, so both quote gross_minor=720: the price
filter now ties (0 <= 50 tolerance) and the zero-tolerance ETA tiebreak picks whichever platform's
driver is actually closer to pickup (dear, in this fixture).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import RiderTraitsV2
from main import run_scenario
from policy_contracts import plain
from scenario import Scenario, person, platform, shift, trip

RIDER_TRAITS = plain(RiderTraitsV2(
    choice_rule="lexicographic", choice_keys=(("price", 50), ("eta", 0)), compare_all_apps=True,
    taste_scale=0, purchase_bias=10))


def build(*, equal_prices=False):
    dear_base = 120 if equal_prices else 300
    platforms = (platform("cheap", base_fare_minor=120, per_km_minor=150)
                | platform("dear", base_fare_minor=dear_base, per_km_minor=150))
    return Scenario(preset="market-blank@1", name="fixture-v2-lexicographic").with_changes({
        "world.horizon_hours": 1,
        "platforms": platforms,
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d-cheap-shift", driver="d-cheap", at_hours=0, hours=1, location=(5, 0)),
            shift("d-dear-shift", driver="d-dear", at_hours=0, hours=1, location=(0, 0)),
        ]},
        "activity.trips": {"generator": "explicit", "items": [
            trip("r1-ride", rider="r1", at_hours=0, origin=(0, 0), destination=(4, 0))]},
        "behavior.rider": {"implementation": "rider_search@2", "parameters": RIDER_TRAITS},
    }).add(
        "population.riders.people", person("r1", apps=("cheap", "dear"), preferred_app="cheap", rider={})
    ).add(
        "population.drivers.people", person("d-cheap", apps=("cheap",), preferred_app="cheap",
                                            driver={"acceptance_bias": 10})
    ).add(
        "population.drivers.people", person("d-dear", apps=("dear",), preferred_app="dear",
                                            driver={"acceptance_bias": 10})
    )


def run(*, equal_prices):
    sim = run_scenario(build(equal_prices=equal_prices), seed=0, log_dir="logs")
    intent = sim.engine.intents[1]
    order = sim.engine.orders[intent.order_ids[0]] if intent.order_ids else None
    quotes = {q.platform_id: q for q in sim.engine.quotes.values()}
    print(f"=== equal_prices={equal_prices} ===")
    for platform_id, q in sorted(quotes.items()):
        print(f"  {platform_id} quote {q.id}: gross_minor={q.fare.gross_minor} eta_seconds={q.eta_seconds}")
    if order is None:
        print("no order was placed")
    else:
        chosen = quotes[order.platform_id]
        print(f"chosen quote: platform={order.platform_id} gross_minor={chosen.fare.gross_minor} "
              f"eta_seconds={chosen.eta_seconds}")
    return sim


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equal-prices", action="store_true")
    args = parser.parse_args()
    run(equal_prices=args.equal_prices)
