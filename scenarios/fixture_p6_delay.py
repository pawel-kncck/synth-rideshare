"""AST-210 world.speed_zones + delay intervention, paired with phase 5's ETA-drift cancel (plan
section 4.G; S5 "active trip experiences a delay").

Driver d1 at (0,0), rider r1's pickup 6 km away at (6,0) along the x axis, inside zone
'corridor' = [(-1,-1), (7,1)]. A `delay('slow1', at_hours=0, zone='corridor', multiplier=0.25,
duration_hours=1)` is in force before the pickup leg begins, slowing the corridor to 30*0.25 =
7.5 km/h: the pickup leg's planned duration is 6/7.5*3600 = 2880.0s, versus the undelayed
6/30*3600 = 720.0s. The platform's own ETA estimator (`estimated_speed_kmh`, deliberately never
delay-aware -- marketplace-engine.md "Delays") still promises around the undelayed 720s; each
`revise_interval_seconds=60` periodic re-estimate reads the car's true (slowed) position and
recomputes remaining distance at the SAME undelayed 30 km/h, so every successive predicted
arrival instant drifts further past the original promise. `rider_search@2`'s
`eta_drift_cancel_seconds=120` cancels once that drift exceeds the threshold.

A second, independent check confirms the "legs already in progress keep their planned end" rule:
a leg that begins BEFORE a delay is imposed on its box is unaffected, checked directly through
Engine.travel_seconds/_begin_leg without involving any rider/driver policy.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2, RiderTraitsV2
from main import Simulation
from policy_contracts import plain
from scenario import Scenario, compile_scenario, delay, person, platform, shift, trip, zone

RIDER_TRAITS = plain(RiderTraitsV2(taste_scale=0, purchase_bias=5, eta_drift_cancel_seconds=120))
DRIVER_TRAITS = plain(DriverTraitsV2(acceptance_bias=10, no_offer_seconds=30))


def build():
    plat = platform('city', revise_interval_seconds=60)
    return Scenario(preset='market-blank@1', name='fixture-p6-delay').with_changes({
        'world.map_km': (20, 20), 'world.horizon_hours': 2,
        'world.zones': zone('corridor', min=(-1, -1), max=(7, 1)),
        'platforms': plat,
        'behavior.rider': {'implementation': 'rider_search@2', 'parameters': RIDER_TRAITS},
        'behavior.driver': {'implementation': 'driver_participation@2', 'parameters': DRIVER_TRAITS},
        'activity.shifts': {'generator': 'explicit', 'items': [
            shift('d1-shift', driver='d1', at_hours=0, hours=2, location=(0, 0))]},
        'activity.trips': {'generator': 'explicit', 'items': [
            trip('t1', rider='r1', at_hours=0, origin=(6, 0), destination=(6, 5))]},
    }).add(
        'interventions', delay('slow1', at_hours=0, zone='corridor', multiplier=0.25, duration_hours=1)
    ).add(
        'population.drivers.people', person('d1', apps=('city',), preferred_app='city', driver={})
    ).add(
        'population.riders.people', person('r1', apps=('city',), preferred_app='city', rider={})
    )


def run():
    inputs = compile_scenario(build()).prepare(seed=0)
    sim = Simulation(inputs)
    eta_revisions = []

    def _capture(n):
        if n.kind == 'pickup_eta_revised':
            eta_revisions.append((n.at, n.data['eta_seconds']))
    sim.engine.add_listener(_capture)
    sim.run(log_dir='logs')
    return sim, eta_revisions


def main():
    sim, eta_revisions = run()
    e = sim.engine
    order = next(iter(e.orders.values()))
    service = e.services[order.service_id]
    pickup_leg = service.legs[0]
    promise = next(p for p in order.eta_predictions if p['source'] == 'offer')
    promised_arrival = promise['at'] + promise['eta_seconds']

    print(f"pickup leg: {pickup_leg.origin}->{pickup_leg.destination}"
         f" planned_duration={pickup_leg.planned_end - pickup_leg.started_at:.1f}s (undelayed would be 720.0s)")
    print(f"promise: at={promise['at']:.1f} eta_seconds={promise['eta_seconds']:.3f}"
         f" -> promised_arrival={promised_arrival:.3f}")

    print(f"\n{len(eta_revisions)} pickup_eta_revised notifications:")
    predicted_arrivals = []
    for at, eta_seconds in eta_revisions:
        arrival = at + eta_seconds
        predicted_arrivals.append(arrival)
        print(f"  t={at:.1f}s eta_seconds={eta_seconds:.3f} -> predicted_arrival={arrival:.3f}"
             f" (drift past promise={arrival - promised_arrival:+.3f}s)")
    monotonic = all(b >= a for a, b in zip(predicted_arrivals, predicted_arrivals[1:]))
    print(f"predicted arrivals monotonically increasing: {monotonic}")
    last_drift = (predicted_arrivals[-1] - promised_arrival) if predicted_arrivals else None
    print(f"last drift exceeds eta_drift_cancel_seconds=120: {last_drift is not None and last_drift > 120}")

    print(f"\norder: state={order.state} cancellation={order.cancellation}")
    cancel_ok = (order.state == 'canceled' and order.cancellation is not None
                and order.cancellation['by'] == 'rider' and order.cancellation['reason'] == 'pickup eta drift')

    # Second, independent check: a leg already in progress when a delay is imposed keeps its
    # planned end; a fresh leg begun inside the same window is slowed. Pure engine, no policy.
    print('\n=== legs-in-progress-keep-their-planned-end (direct engine check) ===')
    check_scenario = Scenario(preset='market-blank@1', name='fixture-p6-delay-check').with_changes({
        'world.map_km': (20, 20), 'world.horizon_hours': 1,
    }).add(
        'population.drivers.people', person('d2', apps=('solo',), preferred_app='solo', driver={})
    ).with_changes({'platforms': platform('solo')})
    check_inputs = compile_scenario(check_scenario).prepare(seed=0)
    check_sim = Simulation(check_inputs)
    ce = check_sim.engine
    ce.start_shift('d2', (0.0, 0.0))
    reloc_a = ce.reposition('d2', (6.0, 0.0))  # leg begins BEFORE any delay exists
    leg_a_planned_end_before = reloc_a.legs[0].planned_end
    ce.impose_delay((-1, -1), (7, 1), 0.25, duration_seconds=3600)  # imposed mid-leg
    print(f"reloc_a leg (already in progress when the delay was imposed):"
         f" planned_end unchanged = {reloc_a.legs[0].planned_end == leg_a_planned_end_before}"
         f" ({reloc_a.legs[0].planned_end})")
    check_sim.advance_to(reloc_a.legs[0].planned_end)
    reloc_b = ce.reposition('d2', (0.0, 0.0))  # a fresh leg begun INSIDE the same delay window
    undelayed_b = math.dist((6.0, 0.0), (0.0, 0.0)) / 30 * 3600
    delayed_b = reloc_b.legs[0].planned_end - reloc_b.legs[0].started_at
    print(f"reloc_b leg (begun inside the active delay): duration={delayed_b:.1f}s"
         f" vs undelayed {undelayed_b:.1f}s -- slowed: {delayed_b > undelayed_b * 1.5}")
    legs_check_ok = (reloc_a.legs[0].planned_end == leg_a_planned_end_before and delayed_b > undelayed_b * 1.5)

    ok = monotonic and (last_drift is not None and last_drift > 120) and cancel_ok and legs_check_ok
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
