# Phase 1: orders, driver offers, and the shared ride lifecycle

## Objective and working approach

Implement original plan steps 5, 6, and 7. An active rider session should be able to order, receive a driver through sequential offers, and complete a ride. A completed ride ends the rider session and order while the driver session remains available for another order.

This is a standalone implementation handoff for a new session. Read the current `main.py` and `test_search.py` before editing. Work through the milestones below in order, explain each meaningful increment, and verify it before proceeding. Keep the implementation small and understandable. Use scripted decisions in this phase; autonomous decisions and population activation belong to [Phase 2](phase-2-autonomous-simulation.md).

## Starting point

- `Driver` and `Rider` own identity and a reference to their simulation. They create sessions through `Driver.go_online(location)` and `Rider.start_session(location, destination)`.
- Session objects hold changing state and locations. Starting a session registers it; going offline removes it, clears the owner's current-session reference, and archives it.
- `Simulation` owns both populations, active driver/rider session dictionaries keyed by actor ID, `active_orders` (currently an empty list), and session history lists.
- `Simulation.schedule(delay_seconds, callback)` queues callbacks using `heapq` and an insertion sequence. It currently returns no handle and has no cancellation mechanism.
- `advance_to(target_time)` processes events at their exact times. Time is elapsed simulated seconds. `run()` controls the displayed clock and playback speed; it contains no scripted actor events.
- Search is implemented. Starting a rider session performs a search; subsequent `RiderSession.search(destination)` calls replace its stored immutable `SearchResult`.
- Search is immediate and observational: no time advance, polling, orders, or driver reservations. Only active driver sessions in `"waiting for order"` contribute to ETA. Several riders can see the same driver. Preserve these properties.
- Coordinates represent kilometres on a flat map. Travel uses straight-line distance and a configurable constant speed. Defaults: 30 km/h, base fare 2, price per kilometre 1.5. Durations and ETA are seconds; prices are simulation currency units.
- `Order` currently only holds a rider and a state; it has no lifecycle or registry integration.
- The local `python3` is Python 3.9.6. Use the standard library and compatible syntax, including `Optional[...]` rather than runtime `float | None` annotations.
- Baseline verification: `python3 -B -m unittest -v test_search`. There are six search scenarios. `archive/` contains older work and is outside this implementation's scope.

## Milestone 1 — create an order from a rider session (step 5)

1. Add a session action such as `RiderSession.make_order()` that delegates coordinated creation to `Simulation.create_order(rider_session)` and returns the order.
2. Require an active rider session, its latest search response showing driver availability, and no existing active order for that session. An unavailable response requires another search before ordering. Reject a second order or a new search while an order is active.
3. Extend `Order` with a simulation-scoped ID, its rider session, an initially unassigned driver session, pickup and destination snapshots, the quoted distance/duration/price, creation time, state, and cancellation reason. Preserve `order.rider` as a convenience if useful. Copy coordinates into immutable snapshots so later actor movement cannot alter the order.
4. Keep the accepted quote for this order. Recheck current driver availability when dispatching; a positive search response is not a reservation. Do not silently change the quoted fare at dispatch time.
5. Add a current-order reference to the rider session and register the order in `Simulation.active_orders`. Keep the existing collection simple; introduce an `order_history` collection for terminal orders.
6. Add terminal order states `"completed"` and `"canceled"`. Centralize finalization so it removes an order from active processing and archives it exactly once.

Verify order creation, immutable trip details, duplicate-order prevention, and the case where the last available driver becomes unavailable after search. For that stale-positive case, create the order and finalize it through the dispatch failure path described below.

## Milestone 2 — make scheduled actions cancelable

Implement this prerequisite before connecting offers to the scheduler.

1. Introduce a small scheduled-event handle with a cancellation flag and a `cancel()` operation. Have `schedule()` return it while retaining time-plus-sequence ordering in the heap.
2. Have `advance_to()` discard canceled events without running callbacks. Existing callers that ignore the returned handle must continue to work.
3. Keep handles for pending offer timeouts and ride actions with the objects that own those actions. Cancel them when their purpose ends. Session termination must invalidate its pending activity.
4. Also check order, offer, and session identity/status inside lifecycle callbacks. A late response or an old callback must never affect a replacement offer or a later session belonging to the same person.

Verify that canceling a timeout prevents its callback, repeated cancellation is harmless, and equal-time callbacks retain deterministic ordering. Do not use `time.sleep()` to implement decisions or deadlines.

## Milestone 3 — sequential offers, rejection, and timeout (step 6)

1. Dispatch to the nearest currently eligible driver session, checking only `active_driver_sessions`. Eligibility means `"waiting for order"`, no pending offer, and no accepted order. Break equal-distance ties by driver ID.
2. Record each offer's identity, order, driver session, creation time, and expiry time. Track attempted driver IDs per order so reopening a driver's session does not allow a repeat offer to that driver.
3. Reserve the chosen driver immediately with a state such as `"considering order"` and a pending-offer reference. Set the order to its existing `"waiting for driver to accept"` state. A driver can have only one outstanding offer, and an order can have only one outstanding offer.
4. Schedule expiration exactly 10 simulated seconds after sending the offer. Add session actions to accept or reject a specific offer; coordinate their effects in `Simulation`.
5. Acceptance is valid only for the current pending offer, with an active driver session and `current_time < expires_at`. A response at the exact deadline is expired regardless of insertion order. Invalid late/duplicate responses must have no effect.
6. On rejection or timeout, cancel/invalidate that offer's timeout, release the driver, and try the next nearest eligible driver who has not been tried. Reevaluate the active registry at each attempt.
7. Use **five total offers per order**, including the first. This resolves the earlier "max 5 times" wording as five distinct drivers, not five retries after an initial offer.
8. If five offers fail, or there are no untried eligible drivers, finalize the order as canceled with the exact reason `"no drivers accepted"`. This includes a stale-positive search followed by zero eligible drivers. Clear the rider session's order reference and keep the session active so it can search again.
9. Keep responses deterministic and explicitly scheduled in tests/examples. Do not introduce acceptance probabilities yet.

Verify acceptance, rejection followed by acceptance, expiration followed by another offer, exhaustion with fewer than five drivers, a strict five-offer limit with more drivers available, simultaneous orders competing for one driver, and acceptance at/after the deadline. Advancing past a canceled timeout must not undo acceptance.

## Milestone 4 — coordinate the shared ride (step 7)

Make `Simulation` the single coordinator of transitions involving multiple objects. Session methods represent actor actions and delegate these transitions; they must no longer independently put an unmatched driver into an active ride.

| Event | Rider session | Order | Driver session |
| --- | --- | --- | --- |
| Offer pending | Waiting for driver acceptance | Waiting for driver to accept | Considering order |
| Driver accepts | Waiting for pickup | Driver driving to pickup | Driving to pickup |
| Driver arrives | Driver has arrived | Driver waiting for rider | Waiting for rider |
| Rider boards | Riding | Driving with rider | Driving with rider |
| Ride ends | Offline and archived | Completed and archived | Waiting for order |

The existing order/driver strings should be preserved where they already match these meanings. Add the rider states and considering-offer state explicitly.

1. On acceptance, connect the order and both sessions, clear the pending offer, and schedule pickup arrival using the driver's current location and the order's pickup location. This actual travel estimate may differ from the earlier search ETA.
2. On pickup arrival, update the driver's location to the pickup and coordinate the three waiting states.
3. Schedule boarding after a configurable deterministic delay. Use 30 seconds as an initial simulation assumption, keeping the rider-waiting stage observable. Handle a zero delay correctly as well.
4. On boarding, coordinate the riding states and schedule completion using the order's travel duration.
5. On completion, update both session locations to the destination, finalize the order, clear live order references, and end/archive the rider session. Return the same driver session to `"waiting for order"`; it can serve another ride without being recreated.
6. Record the timestamps needed to verify the lifecycle and support later metrics: creation, acceptance, pickup arrival, boarding, and completion/cancellation. Do not implement continuous movement yet; location changes occur at arrival events.

Initial session-ending policy: a rider ending a session while an order is awaiting acceptance cancels that order with `"rider ended session"` and releases any pending driver offer. A driver leaving while considering an offer releases it and lets dispatch continue. Reject ordinary offline requests during an accepted ride in this phase; normal ride completion performs coordinated cleanup. Broader mid-ride cancellation policies can be added separately.

Update existing search-test setup if methods such as `accept_order()` now require a real offer. Preserve the search assertions rather than keeping an unsupported no-order acceptance path solely for a fixture.

## Concrete acceptance scenario

Use a rider at `(0, 0)`, destination `(3, 4)`, and a waiting driver at `(0, 1)`, with the existing default speed and fares and a 30-second boarding delay.

| Simulated time | Expected event |
| --- | --- |
| 0 | Search returns distance 5 km, duration 600 seconds, price 9.5, ETA 120 seconds; rider orders and driver receives offer. |
| 2 | Driver accepts; offer timeout is canceled. |
| 10 | Former timeout has no effect. |
| 122 | Driver arrives at pickup. |
| 152 | Rider boards. |
| 752 | Ride completes; rider session and order are archived; driver remains active at `(3, 4)` and waits for another order. |

Also verify that a second rider can use that same driver session, ended sessions cannot receive effective late actions, terminal cleanup is idempotent, and historical order details remain intact.

## Completion and handoff

- Keep all processing based on active registries and scheduled events. Do not add population scans to `run()` or restore its old scripted event tuple.
- Keep this phase free of autonomous populations, stochastic decisions, geographic services, dynamic pricing, and a UI.
- Add focused standard-library scenario tests for dispatch, timeout handling, cancellation, and ride completion. Run the search suite and the new suites explicitly; avoid collecting the older `archive/test.py` by accident.
- Run `git diff --check`, review the final changes, and document the actual public APIs, selected state names, and any justified deviations at the end of this file.
- When implementing this phase, commit the completed, verified phase and report the commit hash and remaining Phase 2 work. Do not create a new session automatically.
