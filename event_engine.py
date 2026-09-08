"""A generic discrete-event scheduler.

The scheduler owns the simulated clock, event ordering, event identities,
cancellation, execution budgets, and serialization of pending work. It knows
nothing about the domain using it: a domain registers a handler per event
kind, and payloads are opaque serializable values the scheduler never reads.
Design notes live in plans/architecture/event-engine.md.
"""

import heapq
import math
from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

SNAPSHOT_SCHEMA_VERSION = 1


class SchedulerError(Exception):
    """A failure that stops the scheduler; the queue stays inspectable."""


class HandlerFailure(SchedulerError):
    """A handler raised. The original exception is chained as the cause."""

    def __init__(self, record, at_seconds, recent, cause):
        self.record = record
        self.at_seconds = at_seconds
        self.recent = tuple(recent)  # the events processed just before this one
        super().__init__(
            f"Handler for {record.kind} (event {record.event_id}) failed at "
            f"{at_seconds:g}s: {cause!r}"
        )


class BudgetExceeded(SchedulerError):
    """More events were processed than the configured budget allows."""

    def __init__(self, message, record, at_seconds, recent):
        self.record = record
        self.at_seconds = at_seconds
        self.recent = tuple(recent)
        tail = ", ".join(f"{item.kind}#{item.event_id}" for item in self.recent[-8:])
        super().__init__(f"{message}; next event {record.kind}#{record.event_id}; last processed: {tail}")


@dataclass(frozen=True)
class EventRecord:
    """One scheduled unit of work. Payload fields are opaque to the scheduler."""

    event_id: int
    at_seconds: float
    sequence: int
    kind: str
    version: int
    payload: Mapping[str, Any]

    def to_dict(self):
        return {
            "event_id": self.event_id, "at_seconds": self.at_seconds, "sequence": self.sequence,
            "kind": self.kind, "version": self.version, "payload": dict(self.payload),
        }


class ScheduledEvent:
    """Cancellation handle for a queued event.

    Status is pending, running, processed, canceled, or failed. Only a pending
    event can be canceled: canceling one that is running or processed does
    not undo it and returns False.
    """

    __slots__ = ("record", "status", "_scheduler")

    def __init__(self, record, scheduler):
        self.record = record
        self.status = "pending"
        self._scheduler = scheduler

    @property
    def event_id(self):
        return self.record.event_id

    def cancel(self):
        return self._scheduler.cancel(self)

    def __repr__(self):
        return f"ScheduledEvent({self.record.kind}#{self.record.event_id} at {self.record.at_seconds:g}s, {self.status})"


class HandlerRegistry:
    """Immutable configuration: event kinds mapped to versioned callables.

    One registry can construct many independent schedulers. Register every
    kind before scheduling; unknown kinds are rejected at scheduling time.
    """

    def __init__(self):
        self._handlers = {}

    def register(self, kind, handler, version=1):
        if not isinstance(kind, str) or not kind:
            raise ValueError("Event kind must be a nonempty string")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"Version of {kind} must be a positive integer")
        if not callable(handler):
            raise TypeError(f"Handler for {kind} must be callable")
        if kind in self._handlers:
            raise ValueError(f"Event kind {kind!r} is already registered")
        self._handlers[kind] = (version, handler)
        return self

    def __contains__(self, kind):
        return kind in self._handlers

    def kinds(self):
        return {kind: version for kind, (version, _) in self._handlers.items()}

    def version_of(self, kind):
        return self._lookup(kind)[0]

    def handler_for(self, kind):
        return self._lookup(kind)[1]

    def _lookup(self, kind):
        try:
            return self._handlers[kind]
        except KeyError:
            raise ValueError(f"Unknown event kind {kind!r}") from None


def _finite_time(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number of seconds")
    return value


def _check_serializable(value, path="payload"):
    """Allow only JSON-shaped values so records can be persisted and traced."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_serializable(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            _check_serializable(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} must be built from None, bool, int, float, str, list, tuple, or dict")


class Scheduler:
    """Heap-ordered execution of registered event kinds on a simulated clock.

    Events run in `(at_seconds, sequence)` order: by time, then first-in
    first-out. Handlers run to completion; work they schedule returns to the
    queue instead of running recursively. Cancellation is lazy: canceled
    entries stay in the heap until they reach the head and are skipped.
    """

    def __init__(self, registry, *, max_events_per_time=100_000, max_events=None,
                 trace=None, diagnostic_tail=32):
        for name, value in (("max_events_per_time", max_events_per_time), ("max_events", max_events)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer or None")
        self.registry = registry
        self.max_events_per_time = max_events_per_time
        self.max_events = max_events
        self._trace = trace  # trace(action, record): observation only
        self._now = 0
        self._queue = []  # heap of (at_seconds, sequence, ScheduledEvent)
        self._invalid = 0  # canceled entries still in the heap
        self._next_event_id = 1
        self._next_sequence = 0
        self._processed = 0
        self._processed_at_now = 0
        self._recent = deque(maxlen=diagnostic_tail)
        self.failure = None

    # ----------------------------------------------------------------------
    # Inspection
    # ----------------------------------------------------------------------

    @property
    def now(self):
        return self._now

    @property
    def processed_count(self):
        return self._processed

    @property
    def pending_count(self):
        return len(self._queue) - self._invalid

    @property
    def recent(self):
        """The most recently processed records, oldest first."""
        return tuple(self._recent)

    def pending(self):
        """Valid queued records in execution order."""
        return [entry.record for _, _, entry in sorted(self._queue, key=lambda item: item[:2])
                if entry.status == "pending"]

    def next_time(self):
        """Time of the next valid event, or None when the queue is empty."""
        head = self._head()
        return None if head is None else head.record.at_seconds

    # ----------------------------------------------------------------------
    # Scheduling and cancellation
    # ----------------------------------------------------------------------

    def schedule_at(self, at_seconds, kind, payload=None):
        _finite_time(at_seconds, "at_seconds")
        if at_seconds < self._now:
            raise ValueError(f"Cannot schedule {kind} at {at_seconds:g}s: the clock is at {self._now:g}s")
        return self._enqueue(at_seconds, kind, payload)

    def schedule_after(self, delay_seconds, kind, payload=None):
        _finite_time(delay_seconds, "delay_seconds")
        if delay_seconds < 0:
            raise ValueError(f"Cannot schedule {kind} with a negative delay ({delay_seconds:g}s)")
        return self._enqueue(self._now + delay_seconds, kind, payload)

    def _enqueue(self, at_seconds, kind, payload):
        version = self.registry.version_of(kind)
        payload = {} if payload is None else payload
        _check_serializable(payload)
        record = EventRecord(
            self._next_event_id, at_seconds, self._next_sequence, kind, version,
            MappingProxyType(dict(payload)),
        )
        self._next_event_id += 1
        self._next_sequence += 1
        entry = ScheduledEvent(record, self)
        heapq.heappush(self._queue, (at_seconds, record.sequence, entry))
        self._emit("scheduled", record)
        return entry

    def cancel(self, event):
        """Mark a queued event invalid. Returns False if it was not pending."""
        if event.status != "pending":
            return False
        event.status = "canceled"
        self._invalid += 1
        self._emit("canceled", event.record)
        return True

    def compact(self):
        """Drop canceled entries from the heap without changing execution order."""
        self._queue = [item for item in self._queue if item[2].status == "pending"]
        heapq.heapify(self._queue)
        self._invalid = 0

    # ----------------------------------------------------------------------
    # Execution
    # ----------------------------------------------------------------------

    def step(self):
        """Process the next valid event. Returns its record, or None when idle."""
        if self.failure is not None:
            raise SchedulerError(f"Scheduler stopped at {self._now:g}s: {self.failure}")
        entry = self._head()
        if entry is None:
            return None
        heapq.heappop(self._queue)
        record = entry.record
        if record.at_seconds > self._now:
            self._now = record.at_seconds
            self._processed_at_now = 0
        self._execute(entry)
        return record

    def advance_to(self, target_seconds):
        """Process every event due through the inclusive target, then move the clock there."""
        _finite_time(target_seconds, "target_seconds")
        if target_seconds < self._now:
            raise ValueError(f"Cannot move time backwards from {self._now:g}s to {target_seconds:g}s")
        processed = 0
        while True:
            head = self._head()
            if head is None or head.record.at_seconds > target_seconds:
                break
            self.step()
            processed += 1
        if target_seconds > self._now:
            self._now = target_seconds
            self._processed_at_now = 0
        return processed

    def _head(self):
        """Skip canceled entries and return the first valid one without removing it."""
        while self._queue:
            entry = self._queue[0][2]
            if entry.status == "pending":
                return entry
            heapq.heappop(self._queue)
            self._invalid -= 1
            self._emit("skipped", entry.record)
        return None

    def _execute(self, entry):
        record = entry.record
        if self.max_events is not None and self._processed >= self.max_events:
            self._stop_over_budget(entry, f"Exceeded the run budget of {self.max_events} events at {self._now:g}s")
        if self.max_events_per_time is not None and self._processed_at_now >= self.max_events_per_time:
            self._stop_over_budget(
                entry, f"Exceeded the budget of {self.max_events_per_time} events at one time ({self._now:g}s)"
            )
        handler = self.registry.handler_for(record.kind)
        entry.status = "running"
        try:
            handler(record)
        except Exception as error:
            entry.status = "failed"
            self.failure = HandlerFailure(record, self._now, self._recent, error)
            self._emit("failed", record)
            raise self.failure from error
        except BaseException:
            entry.status = "failed"
            raise
        entry.status = "processed"
        self._processed += 1
        self._processed_at_now += 1
        self._recent.append(record)
        self._emit("processed", record)

    def _stop_over_budget(self, entry, message):
        # Put the event back so the queue shows what was about to run.
        heapq.heappush(self._queue, (entry.record.at_seconds, entry.record.sequence, entry))
        self.failure = BudgetExceeded(message, entry.record, self._now, self._recent)
        raise self.failure

    def _emit(self, action, record):
        if self._trace is not None:
            self._trace(action, record)

    # ----------------------------------------------------------------------
    # Serialization
    # ----------------------------------------------------------------------

    def snapshot(self):
        """Clock, valid pending records, and counters. Not a full domain checkpoint."""
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "clock_seconds": self._now,
            "next_event_id": self._next_event_id,
            "next_sequence": self._next_sequence,
            "processed_count": self._processed,
            "kinds": self.registry.kinds(),
            "events": [record.to_dict() for record in self.pending()],
        }

    @classmethod
    def restore(cls, snapshot, registry, **options):
        """Rebuild a scheduler whose counters and ordering continue the snapshot.

        Every pending kind must be registered at the recorded version.
        """
        if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported scheduler snapshot schema {snapshot.get('schema_version')!r}")
        scheduler = cls(registry, **options)
        scheduler._now = _finite_time(snapshot["clock_seconds"], "clock_seconds")
        highest_id = highest_sequence = -1
        for item in snapshot["events"]:
            kind = item["kind"]
            if kind not in registry:
                raise ValueError(f"Snapshot needs a handler for {kind!r}, which is not registered")
            if registry.version_of(kind) != item["version"]:
                raise ValueError(
                    f"Snapshot recorded {kind!r} version {item['version']}, "
                    f"but the registry has version {registry.version_of(kind)}"
                )
            at_seconds = _finite_time(item["at_seconds"], "at_seconds")
            if at_seconds < scheduler._now:
                raise ValueError(f"Snapshot event {kind}#{item['event_id']} is before the clock")
            _check_serializable(item["payload"])
            record = EventRecord(
                int(item["event_id"]), at_seconds, int(item["sequence"]), kind, int(item["version"]),
                MappingProxyType(dict(item["payload"])),
            )
            heapq.heappush(scheduler._queue, (at_seconds, record.sequence, ScheduledEvent(record, scheduler)))
            highest_id = max(highest_id, record.event_id)
            highest_sequence = max(highest_sequence, record.sequence)
        scheduler._next_event_id = int(snapshot["next_event_id"])
        scheduler._next_sequence = int(snapshot["next_sequence"])
        if scheduler._next_event_id <= highest_id or scheduler._next_sequence <= highest_sequence:
            raise ValueError("Snapshot counters would reuse an existing event identity")
        scheduler._processed = int(snapshot.get("processed_count", 0))
        return scheduler
