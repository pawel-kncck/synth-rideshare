# Synthetic rideshare marketplace

A seeded Python simulation of Rebu, Blot, and Flyt sharing one physical market.
Drivers, riders, cars, travel and settlements exist once. Each platform receives
its own observations and chooses its own commercial rules. Participants act on
personal observations across the apps they can use.

## Run

Python 3.9 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scenarios/scenario_500_riders.py
.venv/bin/python -m unittest discover -s tests -v
```

The scenario scripts write an offline HTML report and machine-readable records
under a unique directory in `logs/`. Use `time_scale=False` to run without sleeping.

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
| `main.py` | Population assembly, exogenous session scheduling, checkpoints and run/report orchestration. |
| `event_engine.py` | Generic deterministic scheduler, serializable events, cancellation and event budgets. |
| `marketplace_engine.py` | Physical truth, access, quotes, orders, offers, commitments, service, legal transitions and exact settlement. |
| `policy_contracts.py` | Immutable detached observations, typed decisions, validation and identity-keyed randomness. |
| `marketplace_policy.py` | Versioned platform policy implementations and strict configuration binding. |
| `behavior_policy.py` | Participant policy implementations; the behavior specification lives in the linked architecture document. |
| `policy_runtime.py` | Scoped context construction, notification routing, guarded delayed decisions, command application and explicit memory persistence. |
| `demand.py` | Exogenous weekly demand sampling, independent of policies. |
| `metrics.py`, `reporting.py` | Cohort metrics and report exports. |
| `report_template.html`, `report_dashboard.js` | Offline dashboard and interactive time filtering. |
| `tests/test_policies.py` | Policy contracts, lifecycle boundaries and deterministic continuation. |

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
end=23, time_scale=3600)` plays `(end - start)` simulated hours, creates a report,
and prints a summary. `time_scale` is simulated seconds per real second; `False`
disables sleeping. Reports support 5, 15, 30 or 60 minute aggregation.

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
run. Logs and wall-clock report directories are runner outputs and are not part
of deterministic state.

Use `sim.policies.schedule_intervention(at_seconds, platform_id="rebu",
config=PlatformPolicy(version="tariff-v2", ...))` to select a new commercial
policy at a simulated decision boundary. Existing quote and accepted offer terms
remain committed. Population checkpoints and preference interventions are
specified in [Behavior policy](plans/architecture/behavior-policy.md).

## Reports

Every successful `run()` writes:

- `config.json`: world, compiled policy parameters, population profiles, seed,
  scheduled sessions, run status and source hashes.
- `events.jsonl`: engine observations, policy proposals and explanations,
  campaign/rule/version provenance, personal opportunity and reward observations,
  ETA predictions, actual completed distance and settlements.
- `summary.json` and interval CSV files: aggregate metrics.
- `report.html`: a self-contained Plotly dashboard, usable offline.
- `simulation.log`: scoped lifecycle notifications.

Conversion counts the first order per intent. Search coverage counts individual
quotes with locally eligible supply; it is not a guarantee that acceptance can
succeed. Driver utilization uses actual physical service, so a queued order does
not add a second stream of working time. Money is integer minor units, and
platform contribution is the exact residual of settled rider and driver payments.
Contribution excludes operating costs and taxes.

The existing dashboard shows aggregate cohorts across platforms. Per-platform
analysis, policy diagnostics, ETA histories and opportunity observations are
available in the exported event records; new experiment-comparison panels are
part of the experiment-runner roadmap.

See [the phase 3 roadmap](plans/phase-3-multi-platform-marketplace.md) for the
remaining scenario-definition and experiment-runner work.
