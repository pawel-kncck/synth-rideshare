# Phase 2: autonomous behavior and larger populations

## Objective and dependency

Implement original plan step 8: make drivers and riders act autonomously through scheduled events, then exercise larger populations and report useful outcomes.

This is a standalone implementation handoff for a new session. It depends on completion of [Phase 1](phase-1-orders-and-rides.md). Read the current implementation, that plan's completion notes, and the tests before editing. Verify the Phase 1 behavior first; do not assume proposed method names in its plan are the final APIs. If Phase 1 is missing or incomplete, report the concrete dependency instead of layering autonomous behavior over it.

Work through the milestones below in small, explainable increments. Retain the deterministic Phase 1 scenarios as regression tests.

## Required foundation and invariants

- Persistent `Driver` and `Rider` objects represent the population and hold long-term attributes. Driver/rider sessions hold transient activity and participate in interactions.
- `Simulation` owns populations, active session/order registries, histories, a clock in elapsed simulated seconds, and a heap-based scheduler with cancellation support after Phase 1.
- Session creation registers the session. Termination removes it from active processing, cancels its pending actions, and retains history.
- Search returns an immediate immutable snapshot of distance, duration, price, driver availability, and ETA. Waiting does not refresh it. Only another explicit search does that; search never reserves supply.
- Driver matching considers active sessions waiting for an order. Offers reserve one driver, expire after 10 seconds, and try at most five distinct drivers in total. Exhaustion cancels the order with `"no drivers accepted"`.
- Accepted rides synchronize the rider session, order, and driver session. On successful completion, the rider session and order end; the driver session can continue.
- Preserve straight-line kilometre coordinates, durations in seconds, configurable constant speed, and simple fare rules. New behavioral defaults are synthetic assumptions, not estimates of a real market.
- Keep the core compatible with the current Python environment (the initial baseline uses Python 3.9.6) and the standard library. `archive/` is older work and is outside this phase.

## Milestone 1 — configure a reproducible scenario

1. Add explicit scenario settings for population sizes, a simulation horizon, map bounds, activation timing, driver shifts, rider decisions, response delays, retry limits, and the random seed. Keep speed and fare settings supported.
2. Put long-term behavioral traits on drivers/riders when the traits belong to a person, such as a driver's acceptance probability or a rider's patience/retry limit. Put run-wide settings and random-number generators on the simulation or scenario configuration.
3. Use seeded `random.Random` instances. Keep behavior randomness separate from the existing population-ID generation so adding a behavioral draw does not change the population IDs. Use deterministic iteration/tie ordering; avoid global randomness and wall-clock-based seeds.
4. Start with simple bounded distributions and clearly document chosen defaults. Validate probabilities, nonnegative durations, nonoverlapping shift rules, and retry limits. Retry/decision loops need positive delays and finite bounds so they cannot create infinite same-time events.
5. Keep setup separate from the generic event-processing loop. Provide an explicit scenario setup operation that schedules initial activity; calling `run()` alone must not recreate the old fixed driver-event demonstration or schedule duplicate activations.

## Milestone 2 — schedule population activation

1. At scenario setup, generate each driver's shift start, starting location, and planned shift end, then schedule activation callbacks. A population pass during setup is acceptable.
2. A driver activation creates a session and puts it into `"waiting for order"` when ready. Later driver updates operate through the active session and scheduled callbacks.
3. Schedule rider demand events with a starting location and destination. The first scenario can use one demand event per rider; repeated trips must create fresh sessions only after earlier sessions have ended.
4. A demand event starts the rider session, whose first search happens immediately. Schedule the rider's subsequent decision after a configurable thinking delay.
5. Never scan all inactive drivers or riders on each tick to decide who should wake up. Future activation callbacks provide that mechanism. Prevent overlapping sessions for a person.
6. Handle shift endings through the Phase 1 cleanup rules. An idle driver can leave immediately; a pending offer must be released. If a driver is already serving an accepted ride, record a request to end after that ride and then go offline through coordinated cleanup.

Verify scheduled activation at exact times, duplicate prevention, valid reactivation after a prior session, and the absence of further effective actions from ended sessions.

## Milestone 3 — add rider decisions

1. Define a small decision policy invoked after each search response. Keep that policy separate from `Simulation.search()` so search itself stays immediate and observational.
2. When the response has no drivers, choose among waiting and searching again, changing destination and searching again, or ending the session. A "wait" decision schedules a later search; it does not poll or silently update the existing response.
3. When drivers are available, let the rider choose to order, search again, or leave. Initially use configurable probabilities; price/ETA sensitivity can be a later refinement within this phase if it remains easy to explain.
4. Bound retries and waiting time. Ensure there is an eventual exit path. Distinguish rider search retries from the existing five-offer dispatch limit.
5. If the order is canceled because no driver accepted, return control to the same active rider session's decision policy. Do not create overlapping retry timers or a second active order.
6. Once the rider orders, cancel obsolete pre-order decisions. Accepted rides proceed through the Phase 1 lifecycle; ride completion ends the session and its pending actions.

Use forced probabilities and fixed delays in tests to exercise every decision branch before trying mixed random behavior.

## Milestone 4 — add driver decisions

1. When a driver session receives an offer, draw a response delay and an outcome from a configurable policy: acceptance, rejection, or no response.
2. Schedule accept/reject actions against the specific offer identity. No response leaves expiration to the existing 10-second timeout. Late responses must be ignored, including a response scheduled exactly at the deadline.
3. Driver policy makes a decision; the simulation remains responsible for reservation, dispatch retries, and coordinated state changes. Do not duplicate dispatch logic inside the policy.
4. After a ride, the driver can continue waiting or leave when its shift-ending request applies. Add other breaks only if needed after the basic shift lifecycle works.

Verify that seeded decisions reproduce the same outcomes, each driver has at most one pending offer or accepted order, and driver decision callbacks cannot act on an expired/replaced offer.

## Milestone 5 — integrate a small autonomous market

1. Start with a small configured scenario, such as 10 drivers and 100 riders, before increasing population sizes. All behavior should flow from activation, decision, timeout, and travel events.
2. Run supply-rich, supply-poor, and zero-driver scenarios. Every path should progress through completed/canceled orders or bounded rider-session exits without an infinite retry loop.
3. Check the Phase 1 invariants under concurrent activity: exclusive driver reservations, one active order per rider, no duplicate sessions, consistent coupling, and exactly-once terminal cleanup.
4. Define the horizon explicitly: stop processing at the configured end time and retain/report pending events and active sessions/orders. Do not label unfinished rides completed or canceled just because the observation window ended. Stop scheduling new demand beyond the horizon.
5. Preserve events beyond the horizon so the clock can be advanced further if requested. Treat a later run as continuation rather than an opportunity to duplicate scenario setup.

## Milestone 6 — measure outcomes and scale

1. Add a compact structured summary: sessions started/ended, searches, orders created/completed/canceled/still active, cancellation reasons, and offer acceptance/rejection/timeout counts.
2. Use lifecycle timestamps to report observed pickup waiting times and completed trip durations. Distinguish actual pickup wait from the ETA quoted during search, and distinguish incomplete observations at the horizon.
3. If reporting driver time in each state, accumulate elapsed time when states change and account for the final interval at the reporting horizon. Avoid per-second updates to every driver.
4. Support disabling or reducing terminal output for larger runs. Set playback delay to zero for correctness/performance runs; simulation results must not depend on display speed.
5. Scale progressively, for example from 10/100 to 100/1,000 and then 1,000/10,000 drivers/riders if the prior run is healthy. Record seed, settings, event counts, wall time, and peak active counts. Report observations instead of inventing a performance target.
6. Profile only after obtaining a working baseline. Candidate lookup may scan active driver sessions initially; consider a spatial index only if measurements justify it. Continue to keep inactive populations out of routine event processing.
7. Check canceled-event and history retention at larger sizes. Add bounded retention or heap cleanup only when measurements demonstrate a need, while preserving accurate summary counts.

## Acceptance scenarios

| Scenario | Required outcome |
| --- | --- |
| Same seed and configuration, repeated runs | Identical event/outcome records or summaries, excluding wall-clock performance measurements. |
| Forced acceptance with sufficient supply | Complete rides, clean rider/order termination, and continuing driver sessions. |
| Forced rejection or no response | Existing retry limit and timeout rules hold; no stuck reservations. |
| No drivers | Unavailable search responses and bounded rider retries/exits; no infinite event chain. |
| Two riders competing for one driver | The driver is reserved for at most one offer/order at a time. |
| Shift ends during a ride | The accepted ride completes, then the driver leaves through session cleanup. |
| Session ends before a planned action | That callback cannot mutate the ended session or a replacement session. |
| Horizon reached with a ride in progress | Order/session remain active and are reported as incomplete; later continuation remains possible. |
| Larger configured population | Active registries remain consistent; event processing avoids scanning inactive populations. |

Check count conservation: orders created equal completed plus canceled plus active, and sessions started equal ended plus active. Keep these checks independent of the particular random outcome.

## Completion and handoff

- Keep real routing, external services, calibrated economic models, dynamic pricing, and a UI outside this phase unless separately requested.
- Preserve the search and Phase 1 tests, adapting only setup code where public APIs have deliberately evolved. Add focused behavior, reproducibility, cleanup, horizon, and concurrency scenarios.
- Run the relevant current test modules explicitly, avoiding unrelated historical tests in `archive/`, and run `git diff --check`.
- Document the runnable scenario entry point, configuration/defaults, a reproducible example, summary definitions, and measured scale results. Include any justified deviations from this plan.
- When implementing this phase, commit the completed, verified phase and report its commit hash and any remaining limitations. Do not create another session automatically.
