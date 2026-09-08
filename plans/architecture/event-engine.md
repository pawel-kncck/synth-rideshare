# Event engine

Status: proposed design, not an implemented API. See the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md) for delivery order.

The event engine advances a simulated clock and executes scheduled work. It
must be usable by another simulation domain without importing ride-hailing
entities, platform rules, behavioral models, or reporting packages.

## Responsibilities and boundary

Own the clock, event ordering, scheduling, event identities, cancellation,
execution status, and serialization of scheduling state. The
[marketplace engine](marketplace-engine.md) registers domain handlers; it owns
what a handler means and whether a resulting domain transition is legal.

The scheduler must not know about drivers, cars, orders, pricing, offer expiry,
physical occupancy, or the two-order limit. It does not generate demand or make
random decisions. Wall-clock playback, output directories, replication, and
reports belong to the [experiment runner](experiment-runner.md).

## Proposed interface and event representation

Conceptual operations, with final Python names to be chosen during extraction:

| Operation | Contract |
| --- | --- |
| Register handler | Associate a versioned event kind with a callable during runtime construction. |
| Schedule at time | Validate time and payload, assign event identity and stable sequence, return a cancellation handle. |
| Schedule after delay | Resolve relative to the current simulated time using the same validation. |
| Cancel handle | Idempotently mark an event invalid; cancellation of a processed event does not undo its effects. |
| Step | Process the next valid event, or report that the queue is empty. |
| Advance to time | Process events through an explicit inclusive boundary, then move the clock to that boundary. |
| Snapshot/restore | Preserve clock, pending records, sequence counters, and cancellation state with schema versions. |

Use structured records containing `event_id`, `at_seconds`, `sequence`,
`kind`, and a serializable payload. Domain payloads normally reference stable
entity IDs and a generation/attempt identity instead of closing over mutable
Python objects. The scheduler treats those fields as opaque values.

The queue key is initially `(at_seconds, sequence)`. Keep FIFO ordering for
equal times, matching the current heap mechanism. Do not add scheduler priorities
as an incidental optimization. If future modeling needs explicit priorities,
version and test the resulting semantics before exposing them.

## Time and ordering

Use finite simulated seconds relative to one run origin. Keep the existing
floating-point time representation for the initial extraction; calendar labels
and time zones are runner/scenario concerns. Reject NaN, infinity, booleans as
numeric times, negative delays, and attempts to move or schedule into the past.

Zero-delay events are legal. A handler scheduling work for the current time
places it after already queued events at that time. Run handlers to completion
without interleaving another event. This allows the marketplace engine to make
an atomic acceptance transition without a second acceptance executing midway.
It does not make arbitrary handler code transactional or thread-safe.

Do not execute newly scheduled events recursively inside `schedule`. Return to
the queue so event order remains inspectable. Provide a configurable execution
budget for excessive events at one timestamp and overall event count. Exceeding
a budget fails the run with a diagnostic trace; it must not silently discard
work, shift timestamps, or alter outcomes to make progress.

Domain rules own boundary semantics. For example, the marketplace engine rejects
acceptance when `now >= offer.expires_at`, regardless of callback insertion order.
Tariff selection uses half-open effective windows. A domain checkpoint handler
applies due explicit interventions before taking its learning snapshot. The
scheduler itself does not assign special meanings to those events.

Preserve the current inclusive `advance_to(end)` behavior. A continuous run and
a split run must execute a boundary event once, never twice. Running to a horizon
leaves later events queued and does not imply an empty market or a completed ride.

## Cancellation, stale work, and failure

Lazy cancellation is sufficient initially: retain invalid queue entries until
they reach the head. Domain handlers must also check generation, session, and
attempt identities. Canceling a timer cannot substitute for checking whether
the quoted offer or trip still exists when related work executes.

Use queue compaction only if invalid entries materially increase memory. Rebuild
the heap without changing the ordering keys or resampling anything.

Unknown handler kinds, invalid payloads, or handler failures stop execution and
identify time, event ID/kind, and the original exception. The runner records the
failure and reproduction metadata. Do not automatically retry a partly executed
domain handler. Domain command implementations must validate before mutation
and publish their outputs after a coherent transition.

## Continuation and observation

A scheduler snapshot alone is not a full simulation checkpoint. The runner
coordinates snapshots with marketplace state, policy memory, randomness, and
output cursors at an event boundary. Restore the handler registry from the
recorded model/policy versions and reject incompatible versions.

Trace sinks may observe scheduled, canceled, skipped, and processed event
records. They cannot mutate domain state or supply random draws. Detailed tracing
may be disabled while retaining failure diagnostics and required domain metrics.
Enabling tracing must not change event ordering or decisions.

Keep immutable configuration separate from runtime state. The same prepared plan
can construct many independent schedulers, each with its own clock, queue, and
sequence counters. A restored scheduler continues counters rather than resetting
them and changing same-time tie resolution.

## Initial implementation and validation

Extract `Simulation._schedule`, `_schedule_at`, and `advance_to` first, with
behavioral equivalence tests. A private callable adapter can preserve legacy
execution during migration; arbitrary closures are not a supported portable
checkpoint format. Move modern domain events to registered structured records
before claiming persisted resume support.

Keep heap-based discrete-event execution. Neither a tick loop nor a native-code
compiler is required for this boundary. Measure event throughput, queue size,
invalid-event share, and memory before changing the data structure.

Required acceptance cases:

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
