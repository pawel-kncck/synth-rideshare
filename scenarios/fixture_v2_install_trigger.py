"""rider_search@2 install_trigger_eta_seconds (event-driven Download) (AST-209, plan section 4.E; S4).

One rider (r1) has only Dominant installed but is aware of both Dominant and Challenger (both
launched). Dominant's only driver is 5 km from pickup (ETA 600 s), above
install_trigger_eta_seconds=480, so decide() downloads Challenger mid-search instead of ordering
Dominant's slow quote. checkpoint_hours=24 with a 1-hour horizon means no scheduled checkpoint can
run first, so the install is unambiguously the event-driven Download path, not the checkpoint's
own hazard download. Challenger's driver is adjacent to pickup, so the re-queued decision orders
Challenger once it is usable.

--peers adds five same-role neighbours within vicinity_km=2, four of whom already have Challenger
installed (neighbor_install_share['challenger']=0.8, at or above install_trigger_peer_share=0.5)
and switches to personal_evolution@2 with hourly checkpoints, so the peer cascade -- a second,
independent, deterministic install trigger -- fires at the first checkpoint even for a rider who
never sees a slow quote (Dominant's driver sits right at pickup in this mode, and
install_trigger_eta_seconds is off). --vicinity-km 0.5 puts every neighbour outside vicinity, so
the share is 0 and nothing installs.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import EvolutionTraitsV2, RiderTraitsV2
from main import run_scenario
from policy_contracts import plain
from scenario import Scenario, person, platform, shift, trip


def build(*, peers=False, vicinity_km=2):
    platforms = platform("dominant", base_fare_minor=200, per_km_minor=150) | platform(
        "challenger", base_fare_minor=200, per_km_minor=150)
    # peers mode isolates the peer cascade from the event-driven trigger: Dominant's driver sits
    # right at pickup (no slow-quote install trigger), and install_trigger_eta_seconds is off.
    rider_traits = plain(RiderTraitsV2(install_trigger_eta_seconds=0 if peers else 480,
                                       taste_scale=0, purchase_bias=10))
    dominant_location = (0, 0) if peers else (5, 0)
    scenario = Scenario(preset="market-blank@1", name="fixture-v2-install-trigger").with_changes({
        "world.horizon_hours": 1,
        "platforms": platforms,
        "evolution": {"checkpoint_hours": 24, "first_checkpoint_hours": None},
        "activity.shifts": {"generator": "explicit", "items": [
            shift("d-dominant-shift", driver="d-dominant", at_hours=0, hours=1, location=dominant_location),
            shift("d-challenger-shift", driver="d-challenger", at_hours=0, hours=1, location=(0, 0)),
        ]},
        "activity.trips": {"generator": "explicit", "items": [
            trip("r1-ride", rider="r1", at_hours=0, origin=(0, 0), destination=(4, 0))]},
        "behavior.rider": {"implementation": "rider_search@2", "parameters": rider_traits},
    }).add(
        "population.riders.people", person("r1", apps=("dominant",), preferred_app="dominant",
                                           awareness=("dominant", "challenger"), rider={})
    ).add(
        "population.drivers.people", person("d-dominant", apps=("dominant",), preferred_app="dominant",
                                            driver={"acceptance_bias": 10})
    ).add(
        "population.drivers.people", person("d-challenger", apps=("challenger",), preferred_app="challenger",
                                            driver={"acceptance_bias": 10})
    )
    if peers:
        evolution_traits = plain(EvolutionTraitsV2(install_trigger_peer_share=.5, vicinity_km=vicinity_km))
        scenario = scenario.with_changes({
            "evolution": {"checkpoint_hours": 1, "first_checkpoint_hours": 1},
            "behavior.evolution": {"implementation": "personal_evolution@2", "parameters": evolution_traits},
        })
        # Five neighbours 1.0-1.4 km from r1's origin (0, 0) -- within vicinity_km=2, outside
        # vicinity_km=0.5 -- four already holding Challenger and one not, so
        # neighbor_install_share['challenger'] == 4/5 == 0.8. begin_intent sets Rider.location
        # from a trip's origin, which is what position_of('rider', ...) reads for a rider not
        # onboard.
        for index in range(5):
            offset = 1.0 + index * 0.1
            apps = ("dominant", "challenger") if index < 4 else ("dominant",)
            scenario = scenario.add(
                "population.riders.people",
                person(f"n{index}", apps=apps, preferred_app="dominant",
                      awareness=("dominant", "challenger"), rider={})
            ).add(
                "activity.trips.items",
                trip(f"n{index}-ride", rider=f"n{index}", at_hours=0, origin=(offset, 0), destination=(offset, .01)))
    return scenario


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peers", action="store_true")
    parser.add_argument("--vicinity-km", type=float, default=2)
    args = parser.parse_args()
    scenario = build(peers=args.peers, vicinity_km=args.vicinity_km)
    if args.peers:
        from main import Simulation
        from scenario import compile_scenario
        inputs = compile_scenario(scenario).prepare(seed=0)
        sim = Simulation(inputs)
        sim.advance_to(3599)  # just before the first (t=3600) checkpoint
        shares = sim.policies.neighbor_install_shares("rider")
        print(f"neighbor_install_share (t=3599s, vicinity_km={args.vicinity_km}) for r1 = {shares.get('r1', {})}")
        sim.run(log_dir="logs")
    else:
        sim = run_scenario(scenario, seed=0, log_dir="logs")
    installs = [obs for obs in sim.policies.observations if obs["type"] in ("app_installed", "account_activated")
               and obs["person_id"] == "r1"]
    for obs in installs:
        print(f"t={obs['at_seconds']:.0f}s {obs['type']} role={obs['role']} person_id={obs['person_id']} "
              f"platform_id={obs['platform_id']}")
    checkpoint_decisions = [d for d in sim.policies.decisions if d["hook"] == "checkpoint"]
    print(f"checkpoint decisions = {len(checkpoint_decisions)}")
    r1_orders = [o for o in sim.engine.orders.values() if o.rider_id == "r1"]
    for order in sorted(r1_orders, key=lambda o: o.id):
        print(f"r1 order {order.id} on {order.platform_id}: {order.state}")
