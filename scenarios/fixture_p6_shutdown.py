"""AST-210 shutdown_platform + insolvency rule (plan section 4.G; S10 platform failure).

Every order/offer is built directly through engine commands (begin_intent/issue_quote/
place_order/create_offer/respond_to_offer) rather than through rider/driver behavior policy, so
the fixture controls exactly which of three simultaneous states exist at the shutdown instant:

  * order1 is accepted, boarded and mid-transport (frozen terms: a completed ride's settlement
    must match order1.fare / order1.assignment.payout exactly, whether or not the platform is
    still launched when it later completes).
  * order2 is open, with no offer at all.
  * order3 is open with one still-pending offer to d1.

The only driver, d1, has response_seconds set absurdly high so it never auto-responds to
anything within this fixture's short horizon -- every acceptance here is this script's own
explicit respond_to_offer(True) call for order1, never an autonomous decision. 'burn's own
max_local_commitments=1 keeps the still-live PolicyRuntime's automatic dispatch (which fires at
zero delay on every order_created, regardless of who created the order) from ever finding d1
eligible for order2/order3 once it already holds order1 -- it retries forever and finds nobody,
so order2 truly never receives an offer and order3 receives only the one this script creates by
hand. Both of PolicyRuntime's autonomous loops are real and still running; they are steered around,
not disabled.

Platform 'burn' starts with a small tracked cash balance; while order1 is mid-transport (so it is
NOT eligible for cancellation), a manual `grant` transfer -- an ordinary engine mechanism, chosen
here only to control exactly when the balance crosses zero -- drives its balance to <= 0.

Two independent triggers reach the identical end state:
  --insolvency  : platforms.burn.insolvency='shutdown'; the transfer's own `_check_solvency`
                  call schedules the shutdown.
  (no flag)     : insolvency=None; an authored `shutdown` intervention fires at the same
                  simulated instant instead, exercising the scenario/PolicyRuntime wiring
                  independently of the cash rule.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import Simulation
from metrics import conservation
from scenario import Scenario, compile_scenario, person, platform, shutdown

BOARD_CHECK_SECONDS = 90.0  # order1 confirmed boarded/mid-transport (boarding 0-30s, transport 30-150s)
SHUTDOWN_SECONDS = 100.0    # order2/order3/offer3 exist by BOARD_CHECK_SECONDS; the shutdown fires
                            # here for both modes, so both land at the identical simulated instant
STARTING_CASH_MINOR = 50_000
GRANT_MINOR = 60_000  # drives balance to 50000 - 60000 = -10000 <= 0
NEVER = 1_000_000_000.0  # traits set to this never fire within this fixture's short horizon


def build(*, use_insolvency):
    plat = platform('burn', starting_cash_minor=STARTING_CASH_MINOR, max_local_commitments=1,
                    insolvency='shutdown' if use_insolvency else None)
    scenario = Scenario(preset='market-blank@1', name='fixture-p6-shutdown').with_changes({
        'world.horizon_hours': 1,
        'platforms': plat,
    }).add(
        # response_seconds=NEVER: this fixture always accepts/leaves-pending offers itself: an
        # autonomous response racing ahead of the script would undermine the exact states it builds.
        'population.drivers.people', person('d1', apps=('burn',), preferred_app='burn',
                                            driver={'response_seconds': NEVER})
    ).add(
        'population.riders.people', person('r1', apps=('burn',), preferred_app='burn',
                                           rider={'decision_seconds': NEVER, 'patience_seconds': NEVER})
    ).add(
        'population.riders.people', person('r2', apps=('burn',), preferred_app='burn',
                                           rider={'decision_seconds': NEVER, 'patience_seconds': NEVER})
    ).add(
        'population.riders.people', person('r3', apps=('burn',), preferred_app='burn',
                                           rider={'decision_seconds': NEVER, 'patience_seconds': NEVER})
    )
    if not use_insolvency:
        scenario = scenario.add('interventions', shutdown('shut1', at_hours=SHUTDOWN_SECONDS / 3600, platform='burn'))
    return scenario


def run(*, use_insolvency):
    inputs = compile_scenario(build(use_insolvency=use_insolvency)).prepare(seed=0)
    sim = Simulation(inputs)
    initial = sim.snapshot()
    e = sim.engine

    e.start_shift('d1', (0.0, 0.0))
    e.open_app('driver', 'd1', 'burn')
    for rider_id in ('r1', 'r2', 'r3'):
        e.open_app('rider', rider_id, 'burn')

    # order1: accepted, boarded, mid-transport by the crossing instant. Zero pickup distance (d1
    # starts exactly at the pickup) keeps the timeline simple: boarding 0-30s, transport 30-150s.
    # Done synchronously (no advance_to yet), so the scheduler has not yet run PolicyRuntime's own
    # zero-delay dispatch attempt for this order when it resolves -- see module docstring.
    intent1 = e.begin_intent('r1', (0.0, 0.0), (0.0, 1.0))
    quote1 = e.issue_quote('burn', intent1.id, 2000, 0, distance_km=1.0, duration_seconds=120,
                           eta_seconds=120, expires_at=3600.0)
    order1 = e.place_order(intent1.id, quote1.id)
    offer1 = e.create_offer('burn', order1.id, 'd1', 1500, expires_at=3600.0)
    e.respond_to_offer(offer1.id, True)

    sim.advance_to(BOARD_CHECK_SECONDS)
    service1 = e.services[order1.service_id]
    print(f"order1 at t={BOARD_CHECK_SECONDS}: state={order1.state} service.phase={service1.phase}"
         f" (must be assigned/transport)")
    assert order1.state == 'assigned' and service1.phase == 'transport'

    # order2: open, never offered -- automatic dispatch (still live) retries forever and never
    # finds d1 eligible (max_local_commitments=1, already used by order1).
    intent2 = e.begin_intent('r2', (0.0, 0.0), (0.0, 1.0))
    quote2 = e.issue_quote('burn', intent2.id, 500, 0, distance_km=1.0, duration_seconds=120,
                           eta_seconds=120, expires_at=3600.0)
    order2 = e.place_order(intent2.id, quote2.id)

    # order3: open, one pending offer to d1 -- created directly (bypassing the same
    # max_local_commitments filter that keeps automatic dispatch away), never responded.
    intent3 = e.begin_intent('r3', (0.0, 0.0), (0.0, 1.0))
    quote3 = e.issue_quote('burn', intent3.id, 700, 0, distance_km=1.0, duration_seconds=120,
                           eta_seconds=120, expires_at=3600.0)
    order3 = e.place_order(intent3.id, quote3.id)
    offer3 = e.create_offer('burn', order3.id, 'd1', 600, expires_at=3600.0)

    balance_before = e.account('platform', 'burn').balance_minor
    if use_insolvency:
        e.post_transfer('grant', 'driver', 'd1', GRANT_MINOR, counterparty='platform', platform_id='burn')
    # Either the just-posted transfer's own zero-delay platform.insolvency event, or the authored
    # 'shutdown' intervention scheduled for this same instant, fires during this advance.
    sim.advance_to(SHUTDOWN_SECONDS)
    balance_after = e.account('platform', 'burn').balance_minor

    burn = e.platforms['burn']
    print(f"\nmode={'insolvency' if use_insolvency else 'intervention'}: balance {balance_before} -> {balance_after}"
         f" burn.launched={burn.launched}")
    print(f"order1 (boarded): state={order1.state} service.phase={e.services[order1.service_id].phase}"
         f" (must be untouched: still assigned/transport)")
    print(f"order2 (open, no offer): state={order2.state} cancellation={order2.cancellation}"
         f" offer_ids={order2.offer_ids}")
    print(f"order3 (open, pending offer): state={order3.state} cancellation={order3.cancellation}")
    print(f"offer3: state={offer3.state} reason={offer3.reason}")
    print(f"burn.open_driver_ids={burn.open_driver_ids!r} burn.open_rider_ids={burn.open_rider_ids!r}")
    # Captured right here, immediately after the shutdown fires -- order1 completes naturally
    # later in this same function, so checking order1.state this late would no longer reflect
    # what the shutdown itself did to it.
    order1_untouched_by_shutdown = (order1.state == 'assigned'
                                    and e.services[order1.service_id].phase == 'transport')

    quote_rejected = False
    try:
        e.issue_quote('burn', intent2.id, 100, 0, distance_km=1.0, duration_seconds=60, eta_seconds=60,
                      expires_at=3600.0)
    except Exception as error:
        quote_rejected = True
        quote_reason = repr(error)
    print(f"a further quote on burn after shutdown is rejected: {quote_rejected} ({quote_reason if quote_rejected else ''})")

    # Let order1 finish naturally; frozen terms means its settlement must match its OWN
    # order.fare/assignment.payout, unaffected by burn.launched being False.
    sim.advance_to(sim.horizon_seconds)
    settlement1 = e.settlements[order1.settlement_ids[0]]
    print(f"\norder1 completed: state={order1.state} settlement.rider_payment_minor={settlement1.rider_payment_minor}"
         f" (order1.fare.rider_payment_minor={order1.fare.rider_payment_minor})"
         f" settlement.driver_payout_minor={settlement1.driver_payout_minor}"
         f" (assignment.payout.driver_payout_minor={order1.assignment.payout.driver_payout_minor})")
    frozen_terms_ok = (settlement1.rider_payment_minor == order1.fare.rider_payment_minor
                      and settlement1.driver_payout_minor == order1.assignment.payout.driver_payout_minor)
    print(f"frozen terms preserved: {frozen_terms_ok}")

    final = sim.snapshot()
    cons = conservation(initial, final)
    print(f"conservation: orders_created={cons['orders_created']} completed={cons['completed']}"
         f" canceled={cons['canceled']} active_at_end={cons['active_at_end']}"
         f" orders_balanced={cons['orders_balanced']}")

    # The cash rule names its own, more specific reason ("insolvency", marketplace_engine
    # ._on_platform_insolvency) rather than shutdown_platform's generic default
    # ("platform_shutdown", used verbatim by the scenario `shutdown` intervention below) -- both
    # are the SAME mechanism (shutdown_platform's cancellation cascade), just invoked with a
    # cause-specific reason string, so both are checked for internal consistency (order2/order3/
    # offer3 must all agree on whichever reason fired) rather than one hardcoded literal.
    expected_reason = 'insolvency' if use_insolvency else 'platform_shutdown'
    ok = (not burn.launched and order1_untouched_by_shutdown and order1.state == 'completed'
         and order2.state == 'canceled' and not order2.offer_ids
         and order2.cancellation == {'by': 'platform', 'reason': expected_reason}
         and order3.state == 'canceled' and order3.cancellation == {'by': 'platform', 'reason': expected_reason}
         and offer3.state == 'canceled' and offer3.reason == expected_reason
         and not burn.open_driver_ids and not burn.open_rider_ids and quote_rejected
         and frozen_terms_ok and cons['orders_balanced'])
    return sim, ok


def main():
    # --insolvency: the cash rule triggers the shutdown. No flag: an authored `shutdown`
    # intervention triggers it instead, at the same simulated instant -- proving the intervention
    # path independently of the cash rule (plan section 4.G evidence).
    use_insolvency = '--insolvency' in sys.argv[1:]
    print(f"=== mode: {'--insolvency (cash rule)' if use_insolvency else 'shutdown intervention'} ===")
    _, ok = run(use_insolvency=use_insolvency)
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
