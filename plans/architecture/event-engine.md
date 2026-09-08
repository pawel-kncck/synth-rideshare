# Event engine

Status: implemented in `event_engine.py`. The marketplace engine and
`main.py` run on it. See the [phase 3 roadmap](../phase-3-multi-platform-marketplace.md)
for what comes next.

The event engine advances a simulated clock and executes scheduled work. It is
usable by another simulation domain without importing ride-hailing entities,
platform rules, behavioral models, or reporting packages: `event_engine.py`
imports only the standard library.

## Responsibilities and boundary

The engine owns the clock, event ordering, scheduling, event identities,
cancellation, execution status, execution budgets, and serialization of
scheduling state. The [marketplace engine](marketplace-engine.md) registers
domain handlers; it owns what a handler means and whether a resulting domain
transition is legal.

The scheduler does not know about drivers, cars, orders, pricing, offer expiry,
physical occupancy, or the two-order limit. It does not generate demand or make
random decisions. Wall-clock playback, output directories, replication, and
reports belong to the [experiment runner](experiment-runner.md); today
`Simulation.run()` in `main.py` plays that role.

## Interface

| Operation | Python | Contract |
| --- | --- | --- |
| Register handler | `HandlerRegistry.register(kind, handler, version=1)` | Associate a versioned event kind with a callable. A registry is configuration: build it once, construct any number of schedulers from it. Re-registering a kind fails. |
| Schedule at time | `Scheduler.schedule_at(at_seconds, kind, payload=None)` | Validate time and payload, assign an event id and a sequence number, return a `ScheduledEvent` handle. |
| Schedule after delay | `Scheduler.schedule_after(delay_seconds, kind, payload=None)` | Resolve relative to the current simulated time with the same validation. |
| Cancel | `handle.cancel()` or `Scheduler.cancel(handle)` | Idempotent. Returns `True` only when the event was pending. Canceling a running or processed event does not undo its effects and returns `False`. |
| Step | `Scheduler.step()` | Process the next valid event and return its record, or return `None` when the queue is empty. |
| Advance to time | `Scheduler.advance_to(target_seconds)` | Process events through the inclusive boundary, then move the clock there. Returns the number of events processed. |
| Snapshot/restore | `Scheduler.snapshot()`, `Scheduler.restore(snapshot, registry, **options)` | Preserve clock, valid pending records, counters, and registered kind versions under `schema_version` 1. |
| Inspect | `now`, `pending()`, `pending_count`, `processed_count`, `next_time()`, `recent`, `failure` | Read-only views for runners, tracing, and diagnostics. |
| Compact | `Scheduler.compact()` | Rebuild the heap without canceled entries. Ordering keys are unchanged; nothing is resampled. |

Handlers are called as `handler(record)` with an `EventRecord` containing
`event_id`, `at_seconds`, `sequence`, `kind`, `version`, and a read-only
`payload` mapping. The scheduler treats payload fields as opaque values; it only
requires them to be JSON-shaped (`None`, `bool`, `int`, finite `float`, `str`,
lists, tuples, and string-keyed dicts) so records can be persisted and traced.
Domain payloads reference stable entity ids and a generation identity (a
shift id, an offer id, a service leg index) instead of closing over mutable
Python objects. The marketplace engine registers two kinds; the Rebu
simulation in `main.py` adds its scenario and decision timers:

| Kind | Owner | Payload | Handler checks before acting |
| --- | --- | --- | --- |
| `offer.expire` | engine | `offer_id` | The offer is still pending and its deadline has passed. |
| `service.advance` | engine | `service_id`, `leg` | The service has not ended and that leg is still its current one. |
| `driver_session.start` | `main.py` | `driver_id`, `location`, `shift_seconds` | The engine rejects a second shift for a driver already on one. |
| `driver_session.end_shift` | `main.py` | `driver_id`, `shift_id` | That shift is still the driver's current one. |
| `rider_session.start` | `main.py` | `rider_id`, `location`, `destination` | The engine rejects a second live intent for the rider. |
| `rider_session.decide` | `main.py` | `intent_id`, `quote_id` | The intent is live and has no order yet. |
| `offer.respond` | `main.py` | `offer_id` | The offer is still pending. |

The queue key is `(at_seconds, sequence)`: by time, then first-in first-out.
There are no scheduler priorities. If future modeling needs them, version the
resulting semantics before exposing them.

## Time and ordering

Time is finite simulated seconds relative to one run origin, kept as Python
numbers. Calendar labels and time zones are runner/scenario concerns. The
scheduler rejects NaN, infinity, booleans and other non-numbers, negative
delays, and attempts to schedule or advance into the past, all before anything
enters the queue.

Zero-delay events are legal. A handler scheduling work for the current time
places it after already queued events at that time. Handlers run to completion
without interleaving another event, which lets the marketplace engine make an
atomic acceptance transition. It does not make arbitrary handler code
transactional or thread-safe.

Newly scheduled events are never executed recursively inside `schedule_*`; they
return to the queue so event order stays inspectable through `pending()`.
Two execution budgets are configurable per scheduler: `max_events_per_time`
(default 100,000 events at one timestamp) and `max_events` (default unlimited).
Exceeding one raises `BudgetExceeded` naming the time, the event that was about
to run, and the last processed events. The offending event is put back in the
queue, the clock is not moved, and no work is discarded or reordered.

Domain rules own boundary semantics. The ride-hailing domain queues an offer's
expiry before the driver's decision and rejects acceptance when
`now >= offer.expires_at`, regardless of insertion order. The scheduler assigns
no special meaning to any kind.

`advance_to(end)` is inclusive. A continuous run and a split run execute a
boundary event once, never twice. Running to a horizon leaves later events
queued; it does not imply an empty market or a completed ride.

## Cancellation, stale work, and failure

Cancellation is lazy: a canceled entry stays in the heap until it reaches the
head, where it is skipped and reported to the trace sink. `pending_count` and
`pending()` already exclude canceled entries. Call `compact()` if invalid
entries materially increase memory.

Cancellation never substitutes for domain checks. Every ride-hailing handler
first resolves the ids in its payload against the current records and returns
quietly when they no longer match. The marketplace engine keeps no
cancellation handles at all: a resolved offer's expiry event and a stopped
service's arrival event simply find nothing to do. That keeps the market
restorable from records plus the scheduler snapshot, with no handle table
to rebuild; the stale events are few and short-lived.

Unknown kinds and invalid payloads fail at scheduling time. A handler that
raises stops the scheduler: the exception is wrapped in `HandlerFailure`, which
carries the record, the simulated time, the recently processed records, and
the original exception as its cause. The scheduler refuses further steps after
a failure so that a partly executed transition is never retried automatically.
`Simulation.run()` writes the failed event, recent events, traceback, and raw
final state into `simulation.log` with a failed status. Offline analysis in
`metrics.py` uses the actual stopping time. Domain handlers must validate before
mutating and publish outputs after a coherent transition.

## Continuation and observation

A scheduler snapshot alone is not a full simulation checkpoint. Marketplace
state, policy memory, randomness, and output cursors must be captured together
at an event boundary by the runner. `restore()` rejects unknown schema
versions, pending kinds the registry lacks, kinds registered at a different
version than the snapshot recorded, times before the restored clock, and
counters that would reuse an existing event identity. A restored scheduler
continues the event id and sequence counters, so same-time tie resolution is
unchanged.

A trace sink is a callable `trace(action, record)` passed at construction. It
observes `scheduled`, `canceled`, `skipped`, `processed`, and `failed` records.
It receives frozen records with read-only payloads, cannot mutate domain state
or supply random draws, and does not change ordering or decisions. Omitting the
sink retains failure diagnostics through `recent` and the raised exception.

Configuration is separate from runtime state: the registry and the budgets are
fixed at construction, while the clock, queue, counters, and failure belong to
each scheduler instance.

## Validation

There is no unit test suite, by project policy: at this stage every change may
be a refactor, and tests would freeze ad-hoc decisions into requirements. The
behaviors below are the contract, checked with throwaway scripts and scenario
runs whenever the engine changes:

- A toy non-market domain uses the scheduler without importing marketplace code.
- Out-of-order insertion executes by time; equal times execute by sequence.
- Zero-delay descendants execute after existing same-time records.
- Invalid times fail before entering the queue; backward advance fails clearly.
- Repeated cancellation is harmless; canceled events do not execute.
- A domain generation check neutralizes a stale event even without cancellation.
- A same-time infinite scheduling loop fails diagnostically without invented time.
- Continuous, split, and restored runs produce the same ordered domain results.
- Tracing, playback speed, and output configuration do not affect simulated work.
- A handler failure stops the run and preserves enough context for reproduction.

Measure event throughput, queue size, invalid-event share, and memory before
changing the data structure.
