"""AST-210 shift_end hook + Extend (plan section 4.G; S10 driver day-target extension).

One driver, two authored 1-hour shifts separated by a multi-hour gap, `shift_end_rule=
'extend_to_target'` with an unreachable `daily_net_target_minor=1_000_000` (the driver earns
nothing -- no trips exist in this fixture), `max_extension_seconds=3600`,
`extension_step_seconds=1800`. The policy proposes extending by 1800s every time the shift would
otherwise end and the target is still unmet; the runtime clamps the total granted to the cap, so
each shift ends exactly 3600s (1h) later than authored, never more.

The second shift is a regression check on its own: an Opus review of round 0 found that
`PolicyRuntime.shift_end` tracked `used_seconds` on the driver's state without scoping it to the
shift, so a later shift silently inherited an earlier shift's exhausted budget and could never
extend at all -- only the very first shift a driver ever worked could use the mechanism, which is
exactly the shape of every multi-day preset and every S10 scenario (`daily_net_target_minor` is
named for a *day*, not a driver's lifetime). This fixture's shift `s2` reproduces the multi-shift
case and demonstrates the fix: `s2` extends by the same 3600s cap as `s1`, independently, with its
own `shift_extended` observations starting from 0 rather than continuing from `s1`'s leftover
1800/3600 total.

A control run with `daily_net_target_minor=0` (the target is trivially already met) shows the
mechanism is default-off: both shifts end exactly on schedule, with no `Extend` decision at all.
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
SECOND_SHIFT_AT_HOURS = 6.0  # well clear of s1's extended end (2h authored+extended)
SECOND_SHIFT_START_SECONDS = SECOND_SHIFT_AT_HOURS * 3600.0


def build(*, daily_net_target_minor):
    traits = plain(DriverTraitsV2(shift_end_rule='extend_to_target', daily_net_target_minor=daily_net_target_minor,
                                  max_extension_seconds=MAX_EXTENSION_SECONDS,
                                  extension_step_seconds=EXTENSION_STEP_SECONDS))
    return Scenario(preset='market-blank@1', name='fixture-p6-shift-extension').with_changes({
        'world.horizon_hours': 9,
        'platforms': platform('solo'),
        'behavior.driver': {'implementation': 'driver_participation@2', 'parameters': traits},
        'activity.shifts': {'generator': 'explicit', 'items': [
            shift('s1', driver='d1', at_hours=0, hours=AUTHORED_SHIFT_SECONDS / 3600, location=(0.0, 0.0)),
            shift('s2', driver='d1', at_hours=SECOND_SHIFT_AT_HOURS, hours=AUTHORED_SHIFT_SECONDS / 3600,
                  location=(0.0, 0.0)),
        ]},
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
    shift2 = sim.engine.shifts[2]
    expected_ended_at = AUTHORED_SHIFT_SECONDS + MAX_EXTENSION_SECONDS
    expected_ended_at2 = SECOND_SHIFT_START_SECONDS + AUTHORED_SHIFT_SECONDS + MAX_EXTENSION_SECONDS
    print(f"shift1: started_at={shift1.started_at} ended_at={shift1.ended_at} (expect {expected_ended_at})")
    print(f"shift2: started_at={shift2.started_at} ended_at={shift2.ended_at} (expect {expected_ended_at2})")
    total_extended = sum(o['seconds'] for o in extended_obs)
    print(f"total extended seconds across both shifts: {total_extended} (expect {2 * MAX_EXTENSION_SECONDS})")

    extend_decisions = [d for d in shift_end_decisions if d['action'] == 'Extend']
    stop_decisions = [d for d in shift_end_decisions if d['action'] == 'Stop']
    ok_a = (shift1.ended_at == expected_ended_at and shift2.ended_at == expected_ended_at2
           and total_extended == 2 * MAX_EXTENSION_SECONDS
           and len(extend_decisions) == 4 and len(stop_decisions) == 2
           and stop_decisions[0]['at_seconds'] == expected_ended_at
           and stop_decisions[1]['at_seconds'] == expected_ended_at2)
    print(f"OVERALL (both shifts extend to their own cap): {'PASS' if ok_a else 'FAIL'}")

    # Regression check (Opus review, round 0): shift2's own extension budget must start from 0,
    # not inherit shift1's already-exhausted used_seconds. Each shift contributes exactly two
    # 1800s grants (its own extension_step_seconds), and the *per-shift* total tops out at the
    # cap (3600s) independently -- never a cumulative-across-shifts total like 5400 or 7200.
    obs_by_shift = ([o for o in extended_obs if o['at_seconds'] < SECOND_SHIFT_START_SECONDS],
                    [o for o in extended_obs if o['at_seconds'] >= SECOND_SHIFT_START_SECONDS])
    ok_regression = all(
        [o['seconds'] for o in shift_obs] == [EXTENSION_STEP_SECONDS, EXTENSION_STEP_SECONDS]
        and [o['total_seconds'] for o in shift_obs] == [EXTENSION_STEP_SECONDS, MAX_EXTENSION_SECONDS]
        for shift_obs in obs_by_shift)
    print(f"shift_extended by shift: s1={obs_by_shift[0]}")
    print(f"                          s2={obs_by_shift[1]}")
    print(f"OVERALL (shift2's budget is independent of shift1's, not cumulative): "
         f"{'PASS' if ok_regression else 'FAIL'}")

    print('\n=== control: daily_net_target_minor=0 (already met; default-off proof) ===')
    sim2 = run(daily_net_target_minor=0)
    shift_end_decisions2 = [d for d in sim2.decisions if d['role'] == 'driver' and d['hook'] == 'shift_end']
    for d in shift_end_decisions2:
        print(f"  t={d['at_seconds']:.1f}s action={d['action']}")
    extended_obs2 = [o for o in sim2.policies.observations if o['type'] == 'shift_extended']
    shift1b, shift2b = sim2.engine.shifts[1], sim2.engine.shifts[2]
    print(f"shift1: ended_at={shift1b.ended_at} (expect exactly {AUTHORED_SHIFT_SECONDS}, no extension)")
    print(f"shift2: ended_at={shift2b.ended_at} "
         f"(expect exactly {SECOND_SHIFT_START_SECONDS + AUTHORED_SHIFT_SECONDS}, no extension)")
    print(f"shift_extended observations: {extended_obs2} (expect none)")
    extend_decisions2 = [d for d in shift_end_decisions2 if d['action'] == 'Extend']
    ok_b = (shift1b.ended_at == AUTHORED_SHIFT_SECONDS
           and shift2b.ended_at == SECOND_SHIFT_START_SECONDS + AUTHORED_SHIFT_SECONDS
           and not extended_obs2 and not extend_decisions2
           and len(shift_end_decisions2) == 2
           and all(d['action'] == 'Stop' for d in shift_end_decisions2))
    print(f"OVERALL (control): {'PASS' if ok_b else 'FAIL'}")

    ok = ok_a and ok_regression and ok_b
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
