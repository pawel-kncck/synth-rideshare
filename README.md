# Synthetic rideshare marketplace

A seeded Python simulation of Rebu, Blot, and Flyt sharing one physical market.
Drivers, riders, cars, travel and settlements exist once. Each platform receives
its own observations and chooses its own commercial rules. Participants act on
personal observations across the apps they can use.

## Run

Python 3.9 or later:

```sh
python3 -m venv .venv
.venv/bin/python scenarios/baseline_day.py
.venv/bin/python main.py three-platform-week@1 --seed 3
```

The simulator and offline metrics use only the Python standard library. Each
run writes one structured `simulation.log` under a unique directory in `logs/`.
Validation uses scenario runs and throwaway scripts; there is no unit test suite.

A scenario is a published versioned preset plus typed changes. Presets are
complete: every world, platform, behavior, population, activity and evolution
setting is written out under its version, labeled synthetic. A scenario changes
only what an experiment needs, by dotted path or with keyed `add`/`remove`
operations on campaigns, rules, people and interventions:

```python
from main import Simulation, run_scenario
from scenario import Scenario, campaign, compile_scenario, diff_plans, policy_change

baseline = Scenario(preset="three-platform-week@1")
variant = (baseline.renamed("blot-discount")
    .with_changes({"platforms.blot.policy.parameters.per_km_minor": 120,
                   "behavior.driver.parameters.no_offer_seconds": 90})
    .add("platforms.blot.policy.campaigns",
         campaign("midweek", start_hours=24, end_hours=96, discount_fraction=.2, bonus_minor=100))
    .add("interventions", policy_change("tariff", at_hours=96, platform="blot",
                                        version="tariff-v2", parameters={"per_km_minor": 165})))

plan = compile_scenario(variant)          # immutable: resolved values, provenance, fingerprints
print(diff_plans(compile_scenario(baseline), plan))
inputs = plan.prepare(seed=0)             # realized people, cars, shifts and trips for one replication
Simulation(inputs).run()                  # fresh engine, scheduler, memory and randomness
run_scenario(baseline, seed=0)            # the same three steps in one call
```

`compile_scenario` rejects unknown keys, unused generator parameters, invalid
ranges and probabilities, unknown platform/segment/person references, app or car
access that cannot participate, conflicting interventions, and a request for
more than two accepted commitments. Independent errors are reported together,
each naming its field. `Plan.manifest()` and `Plan.save(path)` export the
canonical resolved definition with per-value provenance; `Scenario.load(path)`
rebuilds the scenario from that artifact alone. The authoring contract is
documented in [Scenario definition](plans/architecture/scenario-definition.md);
profiles, model equations, adoption and learning in
[Behavior policy](plans/architecture/behavior-policy.md); commercial
configuration in [Marketplace policy](plans/architecture/marketplace-policy.md).

Platform IDs are free strings: nothing requires `rebu`/`blot`/`flyt`, and
`market-blank@1` starts with no platforms and no segments so a script can
build any market it names. `platform(id, **parameters)` returns one complete
`{id: entry}` platform, overriding `MARKETPLACE_DEFAULTS_V1`; `two_platform(...)`
returns a ready two-platform `Scenario` on `market-blank@1` in one call:

```python
from scenario import two_platform

scenario = two_platform("alpha", "beta", riders=200, drivers=40, horizon_hours=24,
                        shared={"base_fare_minor": 200, "per_km_minor": 150},
                        first_parameters={"commission_fraction": .2},
                        second_parameters={"commission_fraction": .1})
```

`.with_changes(...)`/`.add(...)` chain from either builder's result exactly as
from any other `Scenario`. `platform()`/`rule()` validate parameter and
condition names at authoring time, before `compile_scenario` re-validates
cross-field combinations a single platform or rule cannot see alone.

"Multiplier" means three different things depending on where it appears:
`platforms.<id>.policy.parameters.multiplier` scales a platform's raw quoted
tariff (`MarketplaceParameters.multiplier`); a demand `peak`'s `multiplier` is
an *arrival-rate* weight in `_sample_times`, not a price; a campaign's
`discount_fraction` is a fare reduction, never called a multiplier despite
also being a ratio. Keep them straight when reading or writing a scenario.

## Architecture

| File | Responsibility |
| --- | --- |
| `scenario.py` | Definition schema, versioned presets, typed overrides with provenance, semantic diffs, the compiler producing immutable plans, and per-seed population/activity generation. |
| `main.py` | Single-run execution over prepared inputs: fresh state, exogenous session handlers, checkpoints and raw logging. `run_scenario` is the convenience entry point. |
| `event_engine.py` | Generic deterministic scheduler, serializable events, cancellation and event budgets. |
| `marketplace_engine.py` | Physical truth, access, quotes, orders, offers, commitments, service, legal transitions and exact settlement. |
| `policy_contracts.py` | Immutable detached observations, typed decisions, validation and identity-keyed randomness. |
| `marketplace_policy.py` | Versioned platform policy implementations and strict configuration binding. |
| `behavior_policy.py` | Participant policy implementations; the behavior specification lives in the linked architecture document. |
| `policy_runtime.py` | Policy registry by identity and version, scoped context construction, notification routing, guarded delayed decisions, command application and explicit memory persistence. |
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

These are trusted Python contracts, not a sandbox for hostile plugins. A custom
policy is registered with `policy_runtime.register_policy(family, cls)` under
its declared `name@version`, must accept the family's parameter schema and
implement its hooks, and is then selectable by scenarios. The compiler cannot
prove such code correct; runtime checks and scenario runs validate it. The
multi-replication experiment runner remains phase 4 work.

## Scenarios

| Scenario | What it exercises |
| --- | --- |
| `scenarios/baseline_day.py` | The `three-platform-day@1` preset: one Monday, ten all-day drivers, one trip per rider. |
| `scenarios/baseline_week.py` | The `three-platform-week@1` preset: three eight-hour crews, weekday commute and weekend night peaks, daily checkpoints. |
| `scenarios/blot_discount_week.py` | A campaign plus a later tariff intervention on Blot, run against the baseline with shared population and schedules; prints the resolved diff. |
| `scenarios/flyt_launch_week.py` | Flyt starts unlaunched and without members; awareness, adoption, learning and car onboarding grow it after a Wednesday launch. |
| `scenarios/flyt_growth_month.py` | 1,000 riders, 50 drivers and 30 days: Flyt runs a tapered rider/driver campaign after a baseline week. Includes a `--control` variant; [assumptions and analysis](scenarios/flyt_growth_month.md). |
| `scenarios/fixture_cross_platform_queue.py` | An explicit one-driver, two-rider market where Blot accepts an order during a Rebu ride and its ETA lacks the remaining Rebu service. |
| `scenarios/scale_week.py` | 30,000 riders with seven trips each and 555 drivers: the profiling workload. Expect hours at full size; platform context construction dominates. |
| `scenarios/reviews/sNN_*.py` | Ten scenario-review markets built with `two_platform`/`platform`/`market-blank@1` (`scenario-reviews/NN-*/DESCRIPTION.md`); each documents its approximations and exposes `build()`/`evaluate()` plus a `--check` CLI (see below). |

Every `scenarios/reviews/sNN_*.py` script accepts `--check`: it runs (or,
with `--log PATH`, re-evaluates an existing log without running again) and
prints one `PASS`/`FAIL`/`NOT-EVALUABLE` line per observable check from its
`scenario-reviews/NN-*/DESCRIPTION.md`, through `metrics.Checks` and the
`metrics.py` helpers below, then exits nonzero on any `FAIL`:

```sh
python3 scenarios/reviews/s01_driver_supply_elasticity.py --check
python3 scenarios/reviews/s08_regulatory_price_cap.py --check --log logs/simulation-.../simulation.log
```

A `NOT-EVALUABLE` line is not a failure; it names the reason and the plan
phase that will make the check answerable, per
[the scenario reviews' own README](scenario-reviews/README.md).

Generated person IDs are `rider-n`/`driver-n` with cars `car-driver-n`; trip
IDs are `trip-n` in arrival order. They depend only on the population and
activity sections and the seed, so paired variants keep identities. Segment
counts use largest-remainder rounding and a seeded assignment; traits can be
scalars or `{"uniform": [low, high]}`, `{"normal": [mean, sd, low, high]}` and
`{"choice": [[value, weight], ...]}` distributions sampled once per person.

`world.zones` names axis-aligned boxes in km (`zone(id, min=..., max=...)`);
`map_km`, like before, stays a sampling extent the engine never fences. A
rider or driver segment (or explicit `person()`) may add an `activity` block
-- `origin_zones`/`destination_zones`/`distance_km`/`spatial_peaks` for
riders, `start_zone` for drivers -- that biases the `weekly`/`rotation`
generators toward those zones instead of the map-wide uniform draw; omitting
it (every segment's default) is unchanged behavior. `activity.trips`/
`activity.shifts` also accept `{"generator": "registered", "implementation":
"name@version", "parameters": {...}}`, and `population.<role>.generator`
accepts `{"kind": "registered", ...}`, both resolved through
`register_generator(family, implementation)`/`generator_class(family, id)` --
the same identity-and-version registry `register_policy` uses for policies,
with a source hash recorded at registration for provenance. `Inputs.explicit(
plan, seed=0, people=[...], sessions=[...])` schedules a short fixture's own
people/sessions directly on a compiled plan, validated the same way. See
[Scenario definition](plans/architecture/scenario-definition.md) for the full
validation rules, draw order and manifest fingerprint details.

## Sessions, time and checkpoints

Scenario times are hours after the calendar origin; the plan and log use
simulated seconds. Coordinates are Cartesian kilometres on the world's map.
The scenario controls exogenous shifts and trip intents; policy events handle
the subsequent lifecycle. A rotation generator with `crews * shift_hours > 24`,
overlapping explicit shifts, or two trips of one rider at the same second are
rejected before execution. Other overlaps depend on realized rides and fail
the run when the engine rejects the second session; nothing is dropped silently.

`Simulation.run(until_seconds=None, time_scale=False, log_dir="logs")` advances
to the scenario horizon (or an earlier time), writes the log, and prints its
path. `time_scale` is simulated seconds per real second; `False` disables
sleeping. `run()` performs no metric calculations and does not import
`metrics.py`. Analyze the log separately after it returns. `advance_to(t)`
processes events through `t` without playback delay.

```python
import json
from pathlib import Path

sim.advance_to(300)
Path("checkpoint.json").write_text(json.dumps(sim.snapshot(), allow_nan=False))
continued = Simulation.restore(json.loads(Path("checkpoint.json").read_text()))
continued.advance_to(3600)
```

Capture snapshots at event boundaries, after an `advance_to` call. They include
engine records, scheduler identities and queue ordering, selected policy
implementations and versions, pending interventions, profiles, private
memories, diagnostic records, randomness identities, the plan manifest and the
realized inputs. Restoring and continuing produces the same records as an
uninterrupted run without reading today's defaults. Logs and wall-clock run
directories are runner outputs and are not part of deterministic state.

Interventions are scenario data: `launch`, `policy_change` and
`preference_change` items with an ID and a time. Launches apply before policy
versions, which apply before cohort preference changes at the same time; at a
learning checkpoint all due interventions apply first, then adoption and
learning decisions use one population snapshot. Existing quote and accepted
offer terms remain committed.

## Raw log and offline metrics

`Simulation.run()` produces exactly one file: `simulation.log`, encoded as JSON
Lines (one JSON object per line), with this ordered schema:

1. `run_started`: log schema version, run identity, wall-clock creation time,
   calendar origin, simulated target, playback settings, scenario identity with
   definition/implementation/input fingerprints, and simulation source hashes.
2. `state` with `boundary="initial"`: the raw simulation checkpoint before events
   are processed, including seed, world, population profiles, configured policies,
   the plan manifest, realized inputs, existing records, and pending events.
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
python3 metrics.py logs/simulation-<run-id>/simulation.log --market-share-days 7
python3 metrics.py logs/simulation-<run-id>/simulation.log --windows 24,96
python3 metrics.py logs/simulation-<run-id>/simulation.log --platform-detail
python3 metrics.py logs/simulation-<run-id>/simulation.log --first-choice-days 1
python3 metrics.py logs/simulation-<run-id>/simulation.log --ledger --ledger-drivers
```

These commands print JSON to stdout and leave the log unchanged. No HTML, CSV,
configuration, event-export, or summary files are created automatically. The
first command calculates a whole-run summary; the second adds interval rows
(5, 15, 30 or 60 minutes). The third adds per-platform completed-ride counts and
shares in seven-day periods; use `--market-share-days 1` for daily shares. Periods
are relative to run start, and the last can be shorter.

Four more flags are opt-in and additive; with none of them,
`calculate_metrics(path)` is byte-identical to before they existed. `--windows
H1,H2,...` (comma-separated, strictly increasing hours after run start) splits
money and the funnel into consecutive windows plus a `cumulative` running
total in the same shape, so "money by t=24h and by t=96h" reads from one
result (`window_rows`). `--platform-detail` adds `platform_detail`: per-
platform money (`money_by_platform`), funnel (`platform_funnel`), cancellations
by party (`cancellations_by_party`), driver km (`driver_distance`), ETA-drift
cancellations (`eta_drift`), the base/bonus/discount settlement split
(`settlement_breakdown`, transfers by reason included since phase 3) and
order/money conservation identities (`conservation`, three additive phase-3
keys: `transfer_party_delta_minor`, `unattributed_driver_payout_minor`,
`account_deltas_match_records`). `--ledger` (`--ledger-drivers` implies it,
and additionally fills one row per driver) adds `ledger`
(`ledger_section`, phase 3): per-platform cash start/end, ride contribution,
and transfers by reason, cross-checked as
`cash_end_minor - cash_start_minor == ride_contribution_minor + transfers_minor`
from two independently derived sources -- boundary account balances and the
records new in this run. `--first-choice-days N` adds `first_choice_periods`: each
period's first-choice query share (the platform of a rider's earliest quote
per intent) against the installed base at that period's start; a rider can
have several apps installed, so installed shares can sum past 100% within a
period. Driver km splits a service's legs by kind: `pickup` is empty km,
`transport` is loaded km (`boarding` legs have zero length); a leg is
attributed whole to the run segment containing its own `started_at`, so a
continuous run and a restored continuation agree. Undefined ratios stay
`null` throughout, as elsewhere in this file. The Python API is:

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

[Phase 3](plans/phase-3-multi-platform-marketplace.md) implementation is
complete with [scenario definition](plans/architecture/scenario-definition.md);
what remains is extensive scenario testing. The experiment runner and comparison
work belong to [phase 4](plans/phase-4-experiment-runner.md).
