"""AST-210 shift_end hook + Extend (plan section 4.G; S10 driver day-target extension).

One driver, one authored 1-hour shift, `shift_end_rule='extend_to_target'` with an unreachable
`daily_net_target_minor=1_000_000` (the driver earns nothing -- no trips exist in this fixture),
`max_extension_seconds=3600`, `extension_step_seconds=1800`. The policy proposes extending by
1800s every time the shift would otherwise end and the target is still unmet; the runtime clamps
the total granted to the cap, so the shift ends exactly 3600s (1h) later than authored, never more.
A control run with `daily_net_target_minor=0` (the target is trivially already met) shows the
mechanism is default-off: the shift ends exactly on schedule, with no `Extend` decision at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavior_policy import DriverTraitsV2
from main import Simulation
from policy_contracts import plain
from scenario import Scenario, compile_scenario, person, platform, shift

AUTHORED_SHIFT_SECONDS = 3600.0
MAX_EXTENSION_SECONDS = 3600.0
EXTENSION_STEP_SECONDS = 1800.0


def build(*, daily_net_target_minor):
    traits = plain(DriverTraitsV2(shift_end_rule='extend_to_target', daily_net_target_minor=daily_net_target_minor,
                                  max_extension_seconds=MAX_EXTENSION_SECONDS,
                                  extension_step_seconds=EXTENSION_STEP_SECONDS))
    return Scenario(preset='market-blank@1', name='fixture-p6-shift-extension').with_changes({
        'world.horizon_hours': 3,
        'platforms': platform('solo'),
        'behavior.driver': {'implementation': 'driver_participation@2', 'parameters': traits},
        'activity.shifts': {'generator': 'explicit', 'items': [
            shift('s1', driver='d1', at_hours=0, hours=AUTHORED_SHIFT_SECONDS / 3600, location=(0.0, 0.0))]},
    }).add(
        'population.drivers.people', person('d1', apps=('solo',), preferred_app='solo', driver={})
    )


def run(*, daily_net_target_minor):
    inputs = compile_scenario(build(daily_net_target_minor=daily_net_target_minor)).prepare(seed=0)
    sim = Simulation(inputs)
    sim.run(log_dir='logs')
    return sim


def main():
    print('=== unreachable target: daily_net_target_minor=1,000,000 ===')
    sim = run(daily_net_target_minor=1_000_000)
    shift_end_decisions = [d for d in sim.decisions if d['role'] == 'driver' and d['hook'] == 'shift_end']
    for d in shift_end_decisions:
        print(f"  t={d['at_seconds']:.1f}s action={d['action']}")
    extended_obs = [o for o in sim.policies.observations if o['type'] == 'shift_extended']
    print(f"shift_extended observations: {extended_obs}")
    shift1 = sim.engine.shifts[1]
    print(f"shift1: started_at={shift1.started_at} ended_at={shift1.ended_at}"
         f" (expect authored {AUTHORED_SHIFT_SECONDS} + cap {MAX_EXTENSION_SECONDS} = "
         f"{AUTHORED_SHIFT_SECONDS + MAX_EXTENSION_SECONDS})")
    total_extended = sum(o['seconds'] for o in extended_obs)
    print(f"total extended seconds: {total_extended} (expect {MAX_EXTENSION_SECONDS})")

    expected_ended_at = AUTHORED_SHIFT_SECONDS + MAX_EXTENSION_SECONDS
    extend_decisions = [d for d in shift_end_decisions if d['action'] == 'Extend']
    stop_decisions = [d for d in shift_end_decisions if d['action'] == 'Stop']
    ok_a = (shift1.ended_at == expected_ended_at and total_extended == MAX_EXTENSION_SECONDS
           and len(extend_decisions) == 2 and len(stop_decisions) == 1
           and stop_decisions[0]['at_seconds'] == expected_ended_at)
    print(f"OVERALL (unreachable target): {'PASS' if ok_a else 'FAIL'}")

    print('\n=== control: daily_net_target_minor=0 (already met; default-off proof) ===')
    sim2 = run(daily_net_target_minor=0)
    shift_end_decisions2 = [d for d in sim2.decisions if d['role'] == 'driver' and d['hook'] == 'shift_end']
    for d in shift_end_decisions2:
        print(f"  t={d['at_seconds']:.1f}s action={d['action']}")
    extended_obs2 = [o for o in sim2.policies.observations if o['type'] == 'shift_extended']
    shift1b = sim2.engine.shifts[1]
    print(f"shift1: ended_at={shift1b.ended_at} (expect exactly {AUTHORED_SHIFT_SECONDS}, no extension)")
    print(f"shift_extended observations: {extended_obs2} (expect none)")
    extend_decisions2 = [d for d in shift_end_decisions2 if d['action'] == 'Extend']
    ok_b = (shift1b.ended_at == AUTHORED_SHIFT_SECONDS and not extended_obs2 and not extend_decisions2
           and len(shift_end_decisions2) == 1 and shift_end_decisions2[0]['action'] == 'Stop')
    print(f"OVERALL (control): {'PASS' if ok_b else 'FAIL'}")

    ok = ok_a and ok_b
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
