"""AST-210 reposition + idle hook + zone_return (plan section 4.G; S6 geo-fence/deadhead).

15x15 km map, one zone "core" = [0,0]-[5,5]. UrbanOnly restricts its service_area to "core";
CityWide has none. Driver d1 (both apps, driver_participation@2 idle_rule='zone_return',
max_reposition_km=15) serves a CityWide ride from inside the core out to (12,12), well outside
it. d1's evolution memory is seeded directly with zone_scores={'core': 1000} (the "an initial
zone_scores memory" ambiguity resolution) so the idle hook -- fired at the unqueued drop-off --
sees the core as worth more than staying put and issues a Reposition back to its centroid
(2.5, 2.5). No settlement, no Transfer: repositioning is service-free. Two UrbanOnly requests
inside the core bracket the reposition: one while d1 is still outside (no candidate, eta_seconds
None, no offer), one after d1 arrives (candidate found, eta_seconds set, offer and completion).
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2, EvolutionTraitsV2
from main import Simulation
from metrics import conservation, driver_distance, load_run
from policy_contracts import plain
from scenario import Scenario, compile_scenario, person, platform, shift, trip, zone

DRIVER_TRAITS = plain(DriverTraitsV2(idle_rule='zone_return', max_reposition_km=15, acceptance_bias=10,
                                     no_offer_seconds=30))
EVOLUTION_TRAITS = plain(EvolutionTraitsV2(zone_learning_rate=.5))
RIDER_TRAITS = {'taste_scale': 0, 'purchase_bias': 5}
CORE_EARLY_HOURS = 1000 / 3600   # while d1 is mid-CityWide-transport, well outside the core
CORE_LATE_HOURS = 4200 / 3600    # comfortably after the reposition's own arrival


def build():
    platforms = (platform('urbanonly', service_area='core') | platform('citywide'))
    return Scenario(preset='market-blank@1', name='fixture-p6-reposition').with_changes({
        'world.map_km': (15, 15), 'world.horizon_hours': 2, 'world.zones': zone('core', min=(0, 0), max=(5, 5)),
        'platforms': platforms,
        'activity.shifts': {'generator': 'explicit', 'items': [
            shift('d1-shift', driver='d1', at_hours=0, hours=2, location=(0, 0))]},
        'activity.trips': {'generator': 'explicit', 'items': [
            trip('citywide-ride', rider='r1', at_hours=0, origin=(2, 2), destination=(12, 12)),
            trip('urbanonly-early', rider='r2', at_hours=CORE_EARLY_HOURS, origin=(3, 3), destination=(1, 1)),
            trip('urbanonly-late', rider='r3', at_hours=CORE_LATE_HOURS, origin=(3, 3), destination=(1, 1)),
        ]},
        'behavior.driver': {'implementation': 'driver_participation@2', 'parameters': DRIVER_TRAITS},
        'behavior.evolution': {'implementation': 'personal_evolution@2', 'parameters': EVOLUTION_TRAITS},
    }).add(
        'population.drivers.people', person('d1', apps=('urbanonly', 'citywide'), preferred_app='citywide', driver={})
    ).add(
        'population.riders.people', person('r1', apps=('citywide',), preferred_app='citywide', rider=RIDER_TRAITS)
    ).add(
        'population.riders.people', person('r2', apps=('urbanonly',), preferred_app='urbanonly', rider=RIDER_TRAITS)
    ).add(
        'population.riders.people', person('r3', apps=('urbanonly',), preferred_app='urbanonly', rider=RIDER_TRAITS)
    )


def run(*, vary_competitor=False):
    scenario = build()
    if vary_competitor:
        # Hidden-state-free candidate lists: only citywide's own tariff changes; urbanonly's own
        # decisions must not move at all.
        scenario = scenario.with_changes({'platforms.citywide.policy.parameters.commission_fraction': .35})
    inputs = compile_scenario(scenario).prepare(seed=0)
    sim = Simulation(inputs)
    sim.policies.state('driver', 'd1')['evolution']['zone_scores'] = {'core': 1000.0}
    reposition_events = []

    def _capture(n):
        # Settlement/transfer counts as of THIS notification's own instant, not the run's end --
        # captured synchronously inside the listener, which fires as each notification is
        # published, never after sim.run() has already reached the horizon.
        if n.kind in ('reposition_started', 'reposition_ended'):
            reposition_events.append((n, len(sim.engine.settlements), len(sim.engine.transfers)))
    sim.engine.add_listener(_capture)
    sim.run(log_dir='logs')
    return sim, reposition_events


def main():
    sim, events = run()
    print('=== reposition events ===')
    for n, settlements, transfers in events:
        print(f"  t={n.at:.6f}s {n.kind} {dict(n.data)} (settlements so far={settlements}, transfers={transfers})")
    started_n, started_settlements, started_transfers = next(e for e in events if e[0].kind == 'reposition_started')
    ended_n, ended_settlements, ended_transfers = next(e for e in events if e[0].kind == 'reposition_ended')
    relocation = sim.engine.relocations[started_n.data['relocation_id']]
    leg = relocation.legs[0]
    print(f"relocation {relocation.id}: end_reason={relocation.end_reason} leg {leg.origin}->{leg.destination}"
          f" = {math.dist(leg.origin, leg.destination):.6f} km, duration={leg.planned_end - leg.started_at:.6f}s")
    print(f"settlements at reposition_started={started_settlements} at reposition_ended={ended_settlements}"
          f" (both must equal 1: t1 only)")
    print(f"transfers at reposition_started={started_transfers} at reposition_ended={ended_transfers}"
          f" (both must equal 0: no transfer for a reposition)")

    arrival = ended_n.at
    urbanonly_offers = [o for o in sim.engine.offers.values() if o.platform_id == 'urbanonly']
    before = [o for o in urbanonly_offers if o.created_at < arrival]
    after = [o for o in urbanonly_offers if o.created_at >= arrival]
    print(f"urbanonly offers with created_at < arrival ({arrival:.3f}s): {len(before)}")
    print(f"urbanonly offers with created_at >= arrival: {len(after)}")
    early_quote = next(q for q in sim.engine.quotes.values() if q.rider_id == 'r2')
    late_quote = next(q for q in sim.engine.quotes.values() if q.rider_id == 'r3')
    print(f"urbanonly early quote (r2, before arrival): eta_seconds={early_quote.eta_seconds}")
    print(f"urbanonly late quote (r3, after arrival): eta_seconds={late_quote.eta_seconds}")

    header, initial, final, footer = load_run(sim.log_path)
    distances = driver_distance(initial, final)
    print(f"driver_distance['d1'] = {distances['d1']}")

    cons = conservation(initial, final)
    print(f"orders_balanced={cons['orders_balanced']} settlement_residual_minor={cons['settlement_residual_minor']}"
          f" orders_created={cons['orders_created']} completed={cons['completed']} canceled={cons['canceled']}"
          f" active_at_end={cons['active_at_end']}")

    print('\n=== hidden-state-free candidate lists: vary citywide only, diff urbanonly decisions ===')
    sim_a, _ = run(vary_competitor=False)
    sim_b, _ = run(vary_competitor=True)
    decisions_a = [d for d in sim_a.decisions if d['role'] == 'platform' and d['identity'] == 'urbanonly']
    decisions_b = [d for d in sim_b.decisions if d['role'] == 'platform' and d['identity'] == 'urbanonly']
    print(f"urbanonly decision records identical across citywide commission change: {decisions_a == decisions_b}"
          f" ({len(decisions_a)} records)")


if __name__ == '__main__':
    main()
