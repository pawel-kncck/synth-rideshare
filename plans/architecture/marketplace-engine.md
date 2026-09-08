# Marketplace engine

Status: implemented in `marketplace_engine.py`. `main.py` composes a multi-platform
simulation through `policy_runtime.py`. This document defines the physical and contractual
mechanics used by the [phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

There is one physical market and one marketplace engine. Rebu, Blot, and Flyt
are marketplace instances in it, with separate policy state and observations.
They do not own independent copies of a driver, rider, car, or location.

## Responsibilities and invariants

The engine owns authoritative entities, physical movement, commitments, legal
transitions, scoped observation delivery, and monetary settlement records. It
registers two domain handlers, `offer.expire` and `service.advance`, on the
generic [event engine](event-engine.md). Platform decisions belong to
[marketplace policies](marketplace-policy.md), and personal decisions belong to
[behavior policies](behavior-policy.md); today `main.py` plays both roles for
one platform.

Fixed constraints, enforced by the engine regardless of policy:

1. Every physical ride requires one rider, one driver, and one car.
2. Each entity occupies at most one physical ride at a time. Pickup travel,
   boarding, and passenger transport form a single driver/car service.
3. Each car has one controlling driver. An onboard rider shares the car's
   position until the ride actually ends.
4. A driver has at most two accepted, nonterminal orders across all platforms
   (`MAX_COMMITMENTS`). An order has at most one accepted driver/car assignment.
5. A queued acceptance initiates no motion and cannot arrive at its pickup
   during the preceding ride, even if that route passes it.
6. Going offline, cancellation, and closing apps cannot erase occupied
   entities, release them prematurely, or silently discard commitments.
7. A person has at least one installed app and a car at least one platform
   registration. Service requires usable access on the owning platform.

The two-order cap is a module constant, not a parameter: platforms and
participants can choose fewer commitments but cannot raise it. World speed,
boarding time, and currency granularity are configurable `World` inputs;
occupancy legality does not change with them.

## Authoritative records

| Record | Contents |
| --- | --- |
| `Platform` | Identity, launch state, and the sets of driver/rider ids with its app open. |
| `Rider` | Installed apps, open apps, location when not onboard, live trip intent, and the service they are onboard. |
| `Driver` | Installed apps, open apps, car binding, current shift, ordered accepted commitments, and current physical service. |
| `Car` | Registrations, the controlling driver, and one `Motion` (origin, destination, start, arrival) resolving position at any time. |
| `Shift` | A driver's physical presence: start, exit request, and actual end. |
| `TripIntent` | One rider's desired trip across platform attempts: quotes, orders, once-only conversion time, and terminal outcome. |
| `Quote` | A platform's frozen `FareTerms` (gross, discount, in minor units), estimated distance/duration, and its pickup ETA estimate or `None`. |
| `Order` | Owning platform, intent, bound fare terms, `open`/`assigned`/`completed`/`canceled` state, `Assignment`, offer ids, service id, timeline, cancellation, settlements. |
| `Offer` | One proposal to one driver: frozen `PayoutTerms`, displayed ETA, deadline, and exactly one disposition. |
| `Service` | Actual pickup, boarding, and transport legs for one order, with arrival, boarding, and end times and the actual end position. |
| `Settlement` | Rider payment, driver payout, and the platform contribution residual, with a reason (`completed_ride` or `cancellation_fee`). |

Records refer to each other by id, never by object, so the whole market
serializes: `snapshot()` and `MarketplaceEngine.restore(snapshot, registry)`
rebuild every table and counter. Taken with the scheduler's own snapshot at
the same event boundary, a restored market continues identically, including
an occupied ride with a queued order and outstanding competitor offers.

One car per driver is bound at `add_driver`; rebinding is not supported.
Platform status stays separate from physical status: `Order.phase` reports
`accepted`, `arrived`, `onboard`, or a terminal state, which is what the owning
platform legitimately learns, while `Service.phase` and the driver's
`commitments` record where the car really is and what is queued.

## Commands and execution boundary

Policies never mutate records. They call commands, each of which resolves
ids, validates access, lifecycle, and values (`CommandRejected` names the
rule), applies one coherent transition, and only then publishes scoped
notifications. Listeners may issue further commands while notifications are
being delivered; they always observe a completed transition.

| Command | Effect |
| --- | --- |
| `add_platform`, `add_car`, `add_driver`, `add_rider`, `install_app`, `register_car`, `launch_platform` | Population and access. Memberships must be nonempty. |
| `start_shift(driver, location)`, `end_shift(driver)` | Physical presence. Ending is a request: new acceptance stops now, pending offers close as `canceled`, and the shift ends once commitments are drained. |
| `open_app(role, person, platform)`, `close_app(...)` | App participation. Drivers need a shift and a registered car. Closing cancels that platform's pending offers; accepted orders survive. |
| `begin_intent(rider, origin, destination)`, `end_intent(intent, reason)` | A rider's trip across attempts. An intent with a live order cannot be ended. |
| `issue_quote(platform, intent, gross, discount, distance_km=, duration_seconds=, eta_seconds=)` | Frozen rider terms for an open app session. |
| `place_order(intent, quote)` | Creates the platform order and converts the intent once. One live order per intent. |
| `create_offer(platform, order, driver, payout, bonus, expires_at=, eta_seconds=)` | Proposes an open order to an accepting driver; schedules `offer.expire`. |
| `respond_to_offer(offer, accept)` | Returns the disposition, or `stale` when already resolved. |
| `cancel_order(order, by, reason, rider_fee_minor=, driver_compensation_minor=)` | Ends an order before boarding, freeing only that commitment. |

Runtime legality is mandatory even for a compiled scenario: acceptance
re-checks everything at execution time because an offer may expire, an order
may be assigned, or a slot may fill between policy evaluation and application.

## Offers and atomic acceptance

An offer reserves nothing globally. Several platforms may hold offers to the
same driver, and a platform may hold several offers for one order; the engine
only refuses a second pending offer from the same driver for the same order.

Acceptance validates in order: pending and unexpired (`now < expires_at`,
half-open), order still `open`, driver accepting on that platform (on shift,
no exit requested, app open, car registered, platform launched), and fewer
than two commitments. It then resolves the offer as `accepted`, binds the
`Assignment` with the offered terms, appends the commitment, and closes the
order's other pending offers as `canceled` (`order_assigned`), all in one
transition. A first commitment starts pickup service at once; a second is
queued without changing motion. Only cancellation removes a queued order.

Each offer gets exactly one disposition: `accepted`, `rejected`, `expired`,
`canceled` (order assigned or canceled, driver unavailable), or
`acceptance_failed` (`order_unavailable`, `driver_unavailable`,
`no_free_slot`). The owning platform's `offer_resolved` notification carries
the offer, order, driver, and state, never competitor ids, destinations, or a
slot count. A repeated response to a resolved offer returns `stale`.

## Actual motion and queue promotion

A car's position is resolved from its one `Motion` by straight-line
interpolation at world speed; idle cars have a fixed position. A waiting
rider stays at their own location; an onboard rider follows the car.
`position_of(role, id)` gives every observer the same coordinates.

Service legs are scheduled from actual origins: pickup travel from the car's
current position, boarding at the pickup for `World.boarding_seconds`, then
transport to the destination. Each `service.advance` event names the service
and leg index, so a leg that was stopped or superseded is ignored. Boarding
verifies the rider is not in another ride and the driver still performs this
service. Drop-off settles once, closes the intent as `completed`, releases the
commitment once, and promotes the next commitment from the actual drop-off
position. A pickup colocated with the previous destination takes zero travel
but still arrives after the earlier ride's completion through event order.
ETA estimates never schedule an arrival.

## Offline and cancellation

Shift presence and app participation are distinct. `end_shift` stops
acceptance immediately, cancels the driver's pending offers, tells each open
platform the driver is no longer accepting, and finalizes the shift when the
last commitment ends, closing the apps then. `close_app` withdraws from new
offers on one platform only; an order accepted there must still be served or
explicitly canceled.

`cancel_order` is permitted before boarding and rejected once the rider is
onboard. A queued order frees its commitment without touching current motion;
the order in pickup service stops at the car's actual position, and the queue
promotes from there. The platform decides permission and fees and passes them
in; a fee produces a separate `cancellation_fee` settlement. The intent stays
live so a rider policy can retry under the same intent identity without
counting conversion again.

## Money and observation boundaries

Amounts are integer minor units; `World.to_minor` rounds half up from major
units. Settlements satisfy `rider_payment = driver_payout +
platform_contribution` exactly, contribution being the residual and possibly
negative. A completed ride settles once; cancellation fees use a distinct
reason and record.

Notifications are scoped to an audience: `platform` (own app sessions,
orders, offers, arrivals, boardings, completions, cancellations, driver
availability), `driver` (offers, commitments, service progress, shift
changes), and `rider` (quotes, order progress, intent end). `PlatformView`
exposes own orders, offers, quotes, rider requests, and `DriverPresence`
observations (position, accepting, own accepted orders, own pending offers)
for drivers with that app open. `DriverView` shows a driver's cross-app
commitments and their physical phase; `RiderView` shows the intent, quotes,
and live order. The engine's tables are the privileged experiment observer.
Views are API contracts for trusted extensions, not a sandbox.

## Validation

There is no unit test suite, by project policy. The behaviors below were
checked with throwaway scripts against three platforms and with the bundled
scenarios whenever the engine changed:

- The roadmap's ETA fixture: a Blot estimate of 3 minutes from position alone,
  an informed Rebu estimate of 10 minutes, and an actual pickup wait of 10.
- A platform's view of a driver busy elsewhere equals its view of an idle
  driver at the same position.
- A third commitment fails on every platform mix with `no_free_slot`; two
  last-slot acceptances admit one; two drivers accepting one order yield one
  assignment and one `canceled` competitor offer.
- Expiry is half-open at the deadline; acceptance just before it succeeds.
- Queue cancellation leaves motion untouched; canceling the order in pickup
  stops at the actual position and promotes the queue; the stale arrival event
  does nothing; onboard cancellation is rejected.
- Shift end with two commitments drains both, refuses new offers, then ends.
- A colocated queued pickup arrives at zero travel after the earlier drop-off.
- Settlements conserve money, including a negative contribution.
- A snapshot mid-ride with a queued order and competitor offers restores and
  continues to identical records and identical scheduler state.
- On a simulated week: orders created = completed + canceled + active, each
  intent completes at most once, one settlement per completion, services
  never overlap per driver or rider and lie inside shifts, position is
  continuous across promoted services, and physical service hours equal the
  report's active hours.

Measure physical active time from service intervals, never from overlapping
accepted-order timelines; queued waiting is a separate quantity.
