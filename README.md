# Synthetic rideshare marketplace

A seeded Python simulation of Rebu, Blot, and Flyt sharing one physical market.
Drivers, riders, cars, travel and settlements exist once. Each platform receives
its own observations and chooses its own commercial rules. Participants act on
personal observations across the apps they can use.

## Run

Python 3.9 or later:

```sh
python3 -m venv .venv
.venv/bin/python scenarios/scenario_500_riders.py
```

The simulator and offline metrics use only the Python standard library. Each
scenario writes one structured `simulation.log` under a unique directory in
`logs/`. Use `time_scale=False` to run without sleeping.
Validation uses scenario runs and throwaway scripts; there is no unit test suite.

```python
from main import Simulation
from marketplace_engine import World
from marketplace_policy import PlatformPolicy

sim = Simulation(
    driver_count=10,
    rider_count=100,
    seed=7,
    world=World(speed_kmh=30, boarding_seconds=30),
    platforms={
        "rebu": PlatformPolicy.compile(overrides={"base_fare_minor": 200}),
        "blot": PlatformPolicy.compile(overrides={"base_fare_minor": 180}),
        "flyt": PlatformPolicy.compile(overrides={"commission_fraction": .15}),
    },
)
sim.schedule_driver_session(0, sim.drivers[0], (2, 2), shift_seconds=8 * 3600)
sim.schedule_rider_session(100, sim.riders[0], (2, 2), (5, 8))
sim.run(start=0, end=9, time_scale=False)
```

`Simulation` accepts explicit `world`, `platforms`, `rider_profiles`, and
`driver_profiles`. With no platform mapping, it creates all three platforms.
The old scalar pricing and decision-probability constructor arguments have been
removed. Profile configuration, model equations, clocks, adoption and learning
are documented in [Behavior policy](plans/architecture/behavior-policy.md).
Commercial configuration is documented in
[Marketplace policy](plans/architecture/marketplace-policy.md).

## Architecture

| File | Responsibility |
| --- | --- |
| `main.py` | Population assembly, exogenous session scheduling, checkpoints, execution and raw logging. |
| `event_engine.py` | Generic deterministic scheduler, serializable events, cancellation and event budgets. |
| `marketplace_engine.py` | Physical truth, access, quotes, orders, offers, commitments, service, legal transitions and exact settlement. |
| `policy_contracts.py` | Immutable detached observations, typed decisions, validation and identity-keyed randomness. |
| `marketplace_policy.py` | Versioned platform policy implementations and strict configuration binding. |
| `behavior_policy.py` | Participant policy implementations; the behavior specification lives in the linked architecture document. |
| `policy_runtime.py` | Scoped context construction, notification routing, guarded delayed decisions, command application and explicit memory persistence. |
| `demand.py` | Exogenous weekly demand sampling, independent of policies. |
| `metrics.py` | All post-run calculations: reconstruct metrics from a saved log, summarize outcomes, and optionally aggregate intervals. |

The engine checks legality again when proposals execute. Each driver can hold at
most two unfinished accepted orders and physically serves only one at a time.
Offers from multiple platforms can coexist. Platforms cannot query a driver's
hidden competitor commitments to build candidate lists. Quote expiry and offer
expiry are half-open: acceptance at the deadline fails.

Policy code gets frozen snapshots, serializable private memory, and named random
values. It gets no scheduler, mutable engine record, global RNG, or wall clock.
The runtime is the only adapter between policy decisions and engine commands.
Scoped engine views also return detached, immutable records. Direct engine records
remain authoritative and are intended for orchestration and analysis.

These are trusted Python contracts, not a sandbox for hostile plugins. The
shipped checkpoint loader binds the built-in policy implementations and rejects
unsupported snapshot versions. A general scenario compiler, arbitrary plugin
registry and multi-replication experiment runner remain separate roadmap work.

## Sessions, time and checkpoints

`schedule_driver_session(at_seconds, driver_id, location, shift_seconds=None)`
schedules a shift. `schedule_rider_session(at_seconds, rider_id, origin,
destination)` schedules a trip intent. Coordinates are Cartesian kilometres;
all engine and policy durations use simulated seconds. The scenario controls
exogenous schedules; policy events handle the subsequent lifecycle.

`advance_to(t)` processes events through `t` without playback delay. `run(start=6,
end=23, time_scale=3600)` advances to simulated time `(end - start) * 3600`
seconds, writes the log, and prints its path. `time_scale` is simulated seconds
per real second; `False` disables sleeping. `run()` performs no metric calculations
and does not import `metrics.py`. Analyze the log separately after it returns.

```python
import json
from pathlib import Path

sim.advance_to(300)
Path("checkpoint.json").write_text(json.dumps(sim.snapshot(), allow_nan=False))
continued = Simulation.restore(json.loads(Path("checkpoint.json").read_text()))
continued.advance_to(3600)
```

Capture snapshots at event boundaries, after an `advance_to` call. They include
engine records, scheduler identities and queue ordering, policy versions, pending
interventions, profiles, private memories, diagnostic records and randomness
identities. Restoring and continuing produces the same records as an uninterrupted
run. Logs and wall-clock run directories are runner outputs and are not part
of deterministic state.

Use `sim.policies.schedule_intervention(at_seconds, platform_id="rebu",
config=PlatformPolicy(version="tariff-v2", ...))` to select a new commercial
policy at a simulated decision boundary. Existing quote and accepted offer terms
remain committed. Population checkpoints and preference interventions are
specified in [Behavior policy](plans/architecture/behavior-policy.md).

## Raw log and offline metrics

`Simulation.run()` produces exactly one file: `simulation.log`, encoded as JSON
Lines (one JSON object per line), with this ordered schema:

1. `run_started`: log schema version, run identity, wall-clock creation time,
   simulated origin/target, playback settings, and simulation source hashes.
2. `state` with `boundary="initial"`: the raw simulation checkpoint before events
   are processed, including seed, world, population profiles, configured policies,
   scenario parameters, scheduled sessions, existing records, and pending events.
3. `notification`: timestamped, scoped engine lifecycle notifications during
   execution. These contain observations, not interval aggregates.
4. `state` with `boundary="final"`: the raw checkpoint at the actual stopping
   time, including order timelines, physical service legs, exact minor-unit
   settlements, policy decisions/explanations, observations, memory and pending work.
5. `run_finished`: completed/failed status and event-processing wall time. A
   handled failure also includes the exception/traceback and, for handler failures,
   the failing event and recent event records. The exception is still raised.

The initial/final state records preserve the engine's existing histories; they
are serialized at run boundaries. No metric calculations or interval series are
performed while running or while writing the log. Serialization still takes time
and storage, and these logs include full boundary checkpoints. A hard process
termination or write failure can leave an incomplete log; the offline reader
rejects it instead of presenting partial data as a complete run. A failed run's
final state is diagnostic and is not a safe checkpoint to resume automatically.

Compute metrics explicitly after execution:

```sh
python3 metrics.py logs/simulation-<run-id>/simulation.log
python3 metrics.py logs/simulation-<run-id>/simulation.log --interval-minutes 15
```

Both commands print JSON to stdout and leave the log unchanged. No HTML, CSV,
configuration, event-export, or summary files are created automatically. The
first command calculates a whole-run summary; the second adds interval rows
(5, 15, 30 or 60 minutes). The Python API is:

```python
from metrics import calculate_metrics

result = calculate_metrics(sim.log_path, interval_minutes=15)
```

`metrics.py` owns reconstruction, cohort rules, duration clipping, ratios, money
aggregation, distance, pickup wait, platform completion shares, event counts,
and summary formatting. It reads only the log; it does not import the simulator
or depend on current model defaults. Old free-form text logs are not supported.

Conversion counts the first order per intent. Conversion and offer acceptance
use cohorts created during the run, with outcomes observed through its end;
undecided intents and pending offers remain in their denominators. Completions,
cancellations, and settlements count transitions observed during the run,
including orders carried in from a previous run. Boundary snapshots distinguish
new transitions at the same timestamp from previously observed events. Order
conservation includes orders active at the beginning of a continuation.

Search coverage counts quotes with locally eligible supply, without guaranteeing
acceptance. Driver utilization clips physical service and online spans to the
run, so a queued order adds no extra physical working time. Undefined ratios
are `null`; totals use counts and durations rather than averages of percentages.
Settlement totals retain exact integer minor units; completed-ride and
cancellation money remain separate. Contribution excludes operating costs/taxes.

Raw snapshots and notifications also retain data for further offline analysis:
ETA prediction histories, access/membership changes, policy interventions,
opportunity observations and preference decisions. Behavioral exposure and
learning updates remain in the policy runtime because subsequent decisions use
them; report aggregation is entirely offline.

The final implementation step in [phase 3](plans/phase-3-multi-platform-marketplace.md)
is [scenario definition](plans/architecture/scenario-definition.md), followed by
extensive scenario testing. The experiment runner and comparison work belong to
[phase 4](plans/phase-4-experiment-runner.md).
