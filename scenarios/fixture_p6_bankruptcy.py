"""AST-210 evolution.ledger: lease posting and bankruptcy (plan section 4.G; S10 driver debt).

Two platforms, 30-hour horizon starting Monday midnight. driver_lease_minor=500000 posts every
lease_hours=24, landing exactly at each midnight. d1 never drives (no trips, no income): the
first posting drops its account to -500000, below driver_bankruptcy_minor=-100000, so
deactivate_driver('bankruptcy') fires -- an observation is recorded, d1 disappears from every
platform's candidates, and its second explicit shift (authored at t=25h, after the posting)
is skipped with a logged reason instead of failing the run. d2 completes one high-fare ride
first, so the same lease still leaves it solvent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import Simulation
from metrics import conservation, load_run
from scenario import compile_scenario, person, shift, trip, two_platform

LEDGER = {'driver_lease_minor': 500000, 'lease_hours': 24, 'driver_bankruptcy_minor': -100000}


def build():
    scenario = two_platform('alpha', 'beta', riders=0, drivers=0, horizon_hours=30,
                            first_parameters={'base_fare_minor': 1_000_000, 'per_km_minor': 0}
                            ).renamed('fixture-p6-bankruptcy').with_changes({
        'evolution.ledger': LEDGER,
        'activity.shifts': {'generator': 'explicit', 'items': [
            shift('d1-shift1', driver='d1', at_hours=0, hours=8, location=(0, 0)),
            shift('d1-shift2', driver='d1', at_hours=25, hours=1, location=(0, 0)),
            shift('d2-shift', driver='d2', at_hours=0, hours=10, location=(0, 0)),
        ]},
        'activity.trips': {'generator': 'explicit', 'items': [
            trip('d2-ride', rider='r1', at_hours=.1, origin=(0, 0), destination=(0, 1)),
        ]},
    }).add(
        'population.drivers.people', person('d1', apps=('alpha', 'beta'), preferred_app='alpha', driver={})
    ).add(
        'population.drivers.people', person('d2', apps=('alpha', 'beta'), preferred_app='alpha',
                                            driver={'acceptance_bias': 10, 'no_offer_seconds': 5})
    ).add(
        'population.riders.people', person('r1', apps=('alpha',), preferred_app='alpha',
                                           rider={'taste_scale': 0, 'purchase_bias': 5})
    )
    return scenario


def run():
    inputs = compile_scenario(build()).prepare(seed=0)
    sim = Simulation(inputs)
    sim.run(log_dir='logs')
    return sim


def main():
    sim = run()
    lease_transfers = sorted((t for t in sim.engine.transfers.values() if t.reason == 'lease'), key=lambda t: t.id)
    print('=== lease transfers ===')
    for t in lease_transfers:
        print(f"  t={t.at:.1f}s driver={t.person_id} amount_minor={t.amount_minor}"
              f" counterparty={t.counterparty} platform_id={t.platform_id}")
    d1, d2 = sim.engine.drivers['d1'], sim.engine.drivers['d2']
    print(f"d1.account.balance_minor={sim.engine.account('driver', 'd1').balance_minor}"
          f" deactivated_at={d1.deactivated_at} deactivation_reason={d1.deactivation_reason}")
    print(f"d2.account.balance_minor={sim.engine.account('driver', 'd2').balance_minor}"
          f" deactivated_at={d2.deactivated_at}")
    deactivated_obs = [o for o in sim.policies.observations if o['type'] == 'driver_deactivated']
    print(f"driver_deactivated observations: {deactivated_obs}")

    for pid in sim.engine.platforms:
        candidates = [d.driver_id for d in sim.engine.platform_view(pid).drivers()]
        print(f"platform {pid}: d1 in candidates = {'d1' in candidates} (candidates={candidates})")
    print(f"d1.open_apps = {d1.open_apps!r}")

    skipped = [json_line for json_line in _log_records(sim.log_path) if json_line.get('type') == 'session_skipped']
    print(f"session_skipped records: {skipped}")
    shift_started_after_25h = [n for n in _notifications(sim.log_path)
                               if n['kind'] == 'shift_started' and n['at'] >= 25 * 3600]
    print(f"shift_started notifications at/after t=25h: {shift_started_after_25h}")

    header, initial, final, footer = load_run(sim.log_path)
    print(f"run status = {footer['status']}")
    cons = conservation(initial, final)
    print(f"transfer_party_delta_minor={cons['transfer_party_delta_minor']} (expect -1000000)"
          f" orders_balanced={cons['orders_balanced']} account_deltas_match_records={cons['account_deltas_match_records']}")


def _log_records(path):
    import json
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def _notifications(path):
    return [r['notification'] for r in _log_records(path) if r['type'] == 'notification']


if __name__ == '__main__':
    main()
