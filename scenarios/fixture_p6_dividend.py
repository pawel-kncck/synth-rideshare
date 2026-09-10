"""AST-210 evolution.ledger's sibling, platforms.<id>.dividend (plan section 4.G; S10 Coop payout).

Two drivers on platform 'coop' (starting_cash_minor=200000, commission_fraction=0.5). Every ride
is built directly through engine commands (as in fixture_p6_shutdown.py) so this script controls
exactly how many rides each driver completes before the period closes: d1 completes 2 (qualifies
under min_completed_rides=2), d2 completes 1 (does not). Each ride nets 'coop' 400 minor units
(commission on an 800 gross fare), so by the dividend's first period close (period_hours=24) cash
is 200000 + 3*400 = 201200: excess over the 100000 reserve is 101200, paid entirely to the one
qualifying driver (d1) -- d2's own ride still funded the excess, but only completions actually
qualify for a share.

A control run with min_completed_rides=99 (unreachable by either driver) shows the same excess
cash pays no dividend at all: the qualification gate, not just the reserve check, gates payment.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import Simulation
from scenario import Scenario, compile_scenario, person, platform

RESERVE_MINOR = 100_000
STARTING_CASH_MINOR = 200_000
GROSS_PER_RIDE_MINOR = 800
DRIVER_PAYOUT_PER_RIDE_MINOR = 400  # commission_fraction=0.5 on an 800 gross fare


def build(*, min_completed_rides):
    plat = platform('coop', starting_cash_minor=STARTING_CASH_MINOR, commission_fraction=0.5,
                    dividend={'reserve_minor': RESERVE_MINOR, 'period_hours': 24,
                             'min_completed_rides': min_completed_rides})
    return Scenario(preset='market-blank@1', name='fixture-p6-dividend').with_changes({
        'world.horizon_hours': 30,
        'platforms': plat,
    }).add(
        'population.drivers.people', person('d1', apps=('coop',), preferred_app='coop', driver={})
    ).add(
        'population.drivers.people', person('d2', apps=('coop',), preferred_app='coop', driver={})
    ).add(
        'population.riders.people', person('r1', apps=('coop',), preferred_app='coop', rider={})
    ).add(
        'population.riders.people', person('r2', apps=('coop',), preferred_app='coop', rider={})
    ).add(
        'population.riders.people', person('r3', apps=('coop',), preferred_app='coop', rider={})
    )


def _complete_one_ride(e, sim, *, driver_id, rider_id, pickup, destination):
    """Build and run one ride to completion through direct engine commands, synchronously.

    `pickup` must be wherever the driver's car actually is (zero pickup travel), which the
    caller tracks itself, since each ride moves the car to its own destination."""
    intent = e.begin_intent(rider_id, pickup, destination)
    quote = e.issue_quote('coop', intent.id, GROSS_PER_RIDE_MINOR, 0, distance_km=1.0, duration_seconds=120,
                          eta_seconds=120, expires_at=sim.horizon_seconds)
    order = e.place_order(intent.id, quote.id)
    offer = e.create_offer('coop', order.id, driver_id, DRIVER_PAYOUT_PER_RIDE_MINOR, expires_at=sim.horizon_seconds)
    e.respond_to_offer(offer.id, True)
    sim.advance_to(sim.current_time + 150.0)  # 0 pickup (car already there) + 30 boarding + 120 transport
    assert order.state == 'completed', f"setup: ride for {driver_id!r} did not complete ({order.state!r})"
    return order


def run(*, min_completed_rides):
    inputs = compile_scenario(build(min_completed_rides=min_completed_rides)).prepare(seed=0)
    sim = Simulation(inputs)
    e = sim.engine
    e.start_shift('d1', (0.0, 0.0))
    e.start_shift('d2', (0.0, 0.0))
    e.open_app('driver', 'd1', 'coop')
    e.open_app('driver', 'd2', 'coop')
    for rider_id in ('r1', 'r2', 'r3'):
        e.open_app('rider', rider_id, 'coop')

    # Each ride's pickup matches wherever that driver's car actually ended up, so every pickup
    # leg is zero-distance and the fixed 150s advance above always lands exactly at completion.
    _complete_one_ride(e, sim, driver_id='d1', rider_id='r1', pickup=(0.0, 0.0), destination=(0.0, 1.0))
    _complete_one_ride(e, sim, driver_id='d1', rider_id='r2', pickup=(0.0, 1.0), destination=(0.0, 2.0))
    _complete_one_ride(e, sim, driver_id='d2', rider_id='r3', pickup=(0.0, 0.0), destination=(0.0, 1.0))

    balance_before_period = e.account('platform', 'coop').balance_minor
    sim.advance_to(24 * 3600.0)  # the dividend's first scheduled period close
    balance_after_period = e.account('platform', 'coop').balance_minor
    return sim, balance_before_period, balance_after_period


def main():
    print('=== primary: min_completed_rides=2 (only d1 qualifies) ===')
    sim, before, after = run(min_completed_rides=2)
    e = sim.engine
    print(f"d1 completed rides so far: 2, d2 completed rides so far: 1")
    print(f"coop.balance_minor before period close={before} after={after}")
    dividend_transfers = sorted((t for t in e.transfers.values() if t.reason == 'dividend'), key=lambda t: t.id)
    for t in dividend_transfers:
        print(f"  dividend transfer: t={t.at} driver={t.person_id} amount_minor={t.amount_minor}"
             f" platform_id={t.platform_id}")
    paid_obs = [o for o in sim.policies.observations if o['type'] == 'dividend_paid']
    print(f"dividend_paid observations: {paid_obs}")

    expected_excess = (before - RESERVE_MINOR)
    ok_a = (len(dividend_transfers) == 1 and dividend_transfers[0].person_id == 'd1'
           and dividend_transfers[0].amount_minor == expected_excess
           and len(paid_obs) == 1 and paid_obs[0]['qualifying_drivers'] == 1
           and after == before - expected_excess)
    print(f"OVERALL (primary): {'PASS' if ok_a else 'FAIL'}")

    print('\n=== control: min_completed_rides=99 (unreachable; qualification gate, not just cash) ===')
    sim2, before2, after2 = run(min_completed_rides=99)
    dividend_transfers2 = [t for t in sim2.engine.transfers.values() if t.reason == 'dividend']
    paid_obs2 = [o for o in sim2.policies.observations if o['type'] == 'dividend_paid']
    print(f"coop.balance_minor before={before2} after={after2} (must be UNCHANGED: no dividend)")
    print(f"dividend transfers: {dividend_transfers2} (expect none)")
    print(f"dividend_paid observations: {paid_obs2} (expect none)")
    ok_b = (not dividend_transfers2 and not paid_obs2 and after2 == before2)
    print(f"OVERALL (control): {'PASS' if ok_b else 'FAIL'}")

    ok = ok_a and ok_b
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
