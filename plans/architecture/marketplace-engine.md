# Marketplace engine

Status: implemented in `marketplace_engine.py`. `main.py` composes a multi-platform
simulation from compiled scenario inputs through `policy_runtime.py`. This document defines the physical and contractual
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
[behavior policies](behavior-policy.md); `policy_runtime.py` adapts both to
engine commands.

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
| `Platform` | Identity, launch state, the sets of driver/rider ids with its app open, `starting_cash_minor` (seeds its cash `Account`; `None` = untracked), and (AST-210) `insolvency` (`None` or `"shutdown"`). |
| `Rider` | Installed apps, open apps, location when not onboard, live trip intent, and the service they are onboard. |
| `Driver` | Installed apps, open apps, car binding, current shift, ordered accepted commitments, current physical service, (AST-209) `paused_apps` -- a subset of open apps that stays open (for commitments) but is not accepting -- and (AST-210) `relocation_id` (the live `Relocation`, if any), `deactivated_at`/`deactivation_reason` (set once; refuses any later `start_shift`). |
| `Car` | Registrations, the controlling driver, and one `Motion` (origin, destination, start, arrival) resolving position at any time. |
| `Shift` | A driver's physical presence: start, exit request, and actual end. |
| `TripIntent` | One rider's desired trip across platform attempts: quotes, orders, once-only conversion time, terminal outcome, and the originating scenario session id (`source_id`, optional). |
| `Quote` | A platform's frozen `FareTerms` (gross, discount, in minor units), estimated distance/duration, its pickup ETA estimate or `None`, and (phase 4) `commission_fraction` (binds the offer-time commission when not `None`) and `driver_surcharge_minor` (a commission-exempt flat share of a surcharge already folded into `gross_minor`) -- both default-off and appended last, restored with `.get` tolerance. |
| `Order` | Owning platform, intent, bound fare terms, `open`/`assigned`/`completed`/`canceled` state, `Assignment`, offer ids, service id, timeline, cancellation, settlements. |
| `Offer` | One proposal to one driver: frozen `PayoutTerms`, displayed ETA, deadline, and exactly one disposition. |
| `Service` | Actual pickup, boarding, and transport legs for one order, with arrival, boarding, and end times and the actual end position. |
| `Relocation` (AST-210) | A service-free repositioning move: `driver_id`/`car_id`, `started_at`, exactly one `Leg` of kind `"reposition"` in `legs` (mirrors `Service.legs`, so `metrics.driver_distance`'s identity-based cohorting rule applies unchanged), `ended_at`/`end_reason` (`"arrived"`, `"interrupted"`, `"shift_ended"`, or `"deactivated"`) and `end_position`. No order, no settlement, no platform. |
| `SpeedZone` (AST-210) | A closed box (`min`, `max`) whose `multiplier` applies to the effective speed of any leg *beginning* inside it while active (`at <= now`, and `ends_at is None or now < ends_at`); a static `world.speed_zones` entry has `ends_at=None` (until the run ends), a `delay` intervention sets one. |
| `Settlement` | Rider payment, driver payout, the platform contribution residual, a reason (`completed_ride` or `cancellation_fee`), and `bonus_minor` (the accepted offer's bonus on `completed_ride`, `0` on `cancellation_fee`, so the base/bonus split survives without joining the assignment). |
| `Transfer` | Money moved outside a ride: signed `amount_minor` (positive credits the person), `reason` (one of `TRANSFER_REASONS`, which already included `lease` and `dividend` ahead of AST-210 actually posting them), `role`/`person_id`, `platform_id` (funding platform, or `None`), `counterparty` (`platform` debits that platform by the same amount; `external` -- lease, operating cost -- has no platform side), and optional `order_id`/`program_id`. |
| `Regulation` (phase 4) | A market-wide price/commission cap: `at` (imposed time) and optional `max_base_fare_minor`, `max_per_km_minor`, `max_commission_fraction`. Records accumulate in `MarketplaceEngine.regulations`; the greatest id is the one in force (`engine.regulation`), so restore needs only the table, no cached pointer. Engine-enforced and visible to every platform equally -- no notification (a platform's compliance is its own scheduled `policy_change`; the audit trail is `command_result: regulation_rejected`). |

Records refer to each other by id, never by object, so the whole market
serializes: `snapshot()` and `MarketplaceEngine.restore(snapshot, registry)`
rebuild every table and counter. Taken with the scheduler's own snapshot at
the same event boundary, a restored market continues identically, including
an occupied ride with a queued order and outstanding competitor offers.

`TripIntent.source_id` is the id of the scenario session (`main.Simulation
._on_trip_start` passes the exogenous `trip.start` payload's own `id`) that
started the intent, or `None` when one is begun some other way. It is
scenario metadata for offline lookup (e.g. finding "the order for trip-42"
in a saved log without event-replay), never mutated after construction, and
deliberately absent from the `intent_started` notification -- it identifies
authored demand, not something a rider observes. It is appended last in the
dataclass and `_intent`'s positional restore reads it with `.get`, so a
snapshot taken before this field existed still restores (with `source_id`
`None`); `SNAPSHOT_SCHEMA_VERSION` stays unchanged because the field is
additive and optional.

### Money accounts

`Account` (`role`, `id`, `opening_minor`, `settlement_minor`, `transfer_minor`,
derived `balance_minor`) is a money balance the engine keeps in
`MarketplaceEngine.accounts`, keyed `(role, id)` for every platform, driver
and rider -- unrelated to a person's platform *app* accounts
(`Driver.accounts`/`Rider.accounts`, `activate_account`), which are
membership, not money. `account(role, id)` reads one; callers never write it
directly. `opening_minor` is `Platform.starting_cash_minor` for a platform
(`None` = untracked: `settlement_minor`/`transfer_minor` still accumulate, so
a ledger can report contribution and transfers, but `balance_minor` is `None`
too) and `0` for a driver or rider. Both `add_platform` and `_settle`/
`post_transfer` open and post accounts directly; **no account posting emits a
notification** -- `_post`, `_post_settlement` and `_post_transfer_record` are
silent, so a zero-effect posting (or none at all) never perturbs a
notification trace.

`Account` is deliberately **not** one of `_TABLES`: `snapshot()` emits
`"accounts"` (sorted by `(role, type(id).__name__, str(id))` so insertion
order never affects the emitted bytes) purely for offline reading (`metrics.py`
reads cash start/end from it), but `restore()` ignores that list entirely and
calls `_rebuild_accounts()`, which derives every balance from scratch by
replaying `settlements` and `transfers` in id order. A restored engine's own
`snapshot()` therefore re-derives the same `"accounts"` list a continuous run
would have produced only if replaying the tables truly reproduces every
posted balance -- the strongest conservation check in the repository, not a
tautology one gets by decoding stored totals.

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
| `open_app(role, person, platform)`, `close_app(...)` | App participation. Drivers need a shift and a registered car. Closing cancels that platform's pending offers; accepted orders survive; closing also clears any pause on that app, so a later re-open is never silently paused. |
| `pause_app(driver, platform)`, `resume_app(...)` (AST-209) | A driver-initiated, per-platform "I'm busy": stops accepting on an *open* app without closing it, so accepted commitments and the obligation to serve them survive. Pausing cancels that platform's pending offers to the driver (exactly like `close_app`) and publishes the existing `driver_availability` notification with `accepting=False`; resuming publishes it with the *computed* `accepting` value, so resuming while an exit is requested (or another reason the driver would not actually be accepting) does not announce the driver as accepting. Raises if the app is not open; a no-op if already in the requested pause state. |
| `begin_intent(rider, origin, destination, source_id=None)`, `end_intent(intent, reason)` | A rider's trip across attempts. An intent with a live order cannot be ended. |
| `issue_quote(platform, intent, gross, discount, distance_km=, duration_seconds=, eta_seconds=)` | Frozen rider terms for an open app session. |
| `place_order(intent, quote)` | Creates the platform order and converts the intent once. One live order per intent. |
| `create_offer(platform, order, driver, payout, bonus, expires_at=, eta_seconds=)` | Proposes an open order to an accepting driver; schedules `offer.expire`. |
| `respond_to_offer(offer, accept)` | Returns the disposition, or `stale` when already resolved. |
| `cancel_order(order, by, reason, rider_fee_minor=, driver_compensation_minor=, driver_penalty_minor=)` | Ends an order before boarding, freeing only that commitment. A nonzero `driver_penalty_minor` is only legal for `by="driver"`; when nonzero it posts a `driver_penalty` `Transfer` between the existing driver `order_canceled` notification and releasing the commitment. |
| `post_transfer(reason, role, person, amount_minor, platform_id=, counterparty=, order_id=, program_id=)` | Posts a `Transfer` outside a ride: `counterparty="platform"` requires and debits `platform_id`; `"external"` forbids it. Notifies the credited person and, for a platform counterparty, the funding platform; never the account posting itself. |
| `impose_regulation(max_base_fare_minor=, max_per_km_minor=, max_commission_fraction=)` (phase 4) | Records a new `Regulation` effective immediately (`at=now`), superseding any prior one. Requires at least one cap; the two fare caps are jointly set or jointly absent. No notification. |
| `reposition(driver, destination)` (AST-210) | Starts a service-free `Relocation` for an on-shift, uncommitted, not-already-repositioning, not-deactivated driver: a `Motion` toward `destination` and one `reposition` `Leg`. No settlement, no platform side. Driver-only `reposition_started` notification. |
| `deactivate_driver(driver, reason)` (AST-210) | Permanently retires a driver: ends a live relocation, ends the shift (draining as `end_shift` already does), cancels every not-yet-boarded commitment (`by="driver"`, `reason="driver_deactivated"`), and refuses any later `start_shift`. A boarded (`transport`-phase) commitment is left alone to finish on frozen terms. Idempotent; driver-only `driver_deactivated` notification. |
| `shutdown_platform(platform, reason="platform_shutdown")` (AST-210) | Retires a platform: `launched=False`, every pending offer of this platform resolved `canceled` (its own `reason`) *before* its not-yet-boarded orders are canceled with the *same* reason (so `cancel_order`'s own offer-resolution loop finds nothing left to overwrite with a different one), then every open app session closed. A boarded order is skipped and finishes on frozen terms. Idempotent; platform-only `platform_shutdown` notification. |
| `impose_delay(box_min, box_max, multiplier, duration_seconds=None)` (AST-210) | Records a `SpeedZone` effective immediately (`at=now`) for `duration_seconds` (`None` = until the run ends). No notification -- physical and public by construction, like `impose_regulation`. |

Runtime legality is mandatory even for a compiled scenario: acceptance
re-checks everything at execution time because an offer may expire, an order
may be assigned, or a slot may fill between policy evaluation and application.

### Regulation (phase 4)

`RegulationRejected` is a `CommandRejected` subclass, so every existing
`except CommandRejected` still catches it -- a caller that wants to
distinguish a regulatory rejection (to log `command_result:
regulation_rejected` instead of crashing or reporting a generic
`offer_unavailable`) catches `RegulationRejected` specifically, before any
broader `CommandRejected` handler. `issue_quote` checks the in-force
`engine.regulation`'s fare cap, when set, against `gross_minor`:
`cap = round_half_up(max_base_fare_minor + max_per_km_minor * distance_km)`,
rounded *up* deliberately, so a compliant platform whose own rounding lands
one cent over an unrounded cap is never wrongly rejected. `create_offer`
checks the commission cap, when set, against `payout_minor` (the base
payout, never `driver_payout_minor` -- a bonus is not commission relief):
`floor = round_half_up((1 - max_commission_fraction) * order.fare.gross_minor)`;
`payout_minor < floor` is rejected. Both checks run after every other
validation and before the transition, so a rejected command changes nothing.

### Repositioning (AST-210)

`reposition(driver_id, destination)` starts a `Motion` and a service-free
`reposition` leg on a new `Relocation`, exactly mirroring how a `Service`
leg is built (`_begin_leg`'s speed-zone-aware `travel_seconds`, the same
`Leg` shape) so `metrics.driver_distance`'s identity-based cohorting rule
(never a time filter) applies unchanged -- a `reposition_km` bucket sums a
relocation's *planned* `origin -> destination` distance, attributed to
whichever run first observes that relocation id, byte-identical to how a
canceled service's own pickup leg is already treated; `end_position` records
where the car actually ended up, for offline reconciliation, but the
reported `reposition_km` is always the planned distance. `total_km` grows to
include it, so `loaded_share_pct`'s denominator (now empty + loaded + other +
reposition) matches plan section 10 S6's "net earnings per total km
including empty and reposition km".

A driver may only start repositioning with zero commitments and no live
service -- reject otherwise ("cannot reposition while committed to work") --
so a live `Relocation` and any accepted commitment are mutually exclusive by
construction. An accepted order interrupts a live relocation: `_start_service`
ends it (`reason="interrupted"`) as its first statement, *before* reading the
car's current position, so pickup automatically starts from wherever the car
actually was -- no extra code, because `_begin_leg` immediately overwrites
`car.motion` anyway. `_finish_shift` and `deactivate_driver` also end a live
relocation (`"shift_ended"`, `"deactivated"`) before their own asserts/
transitions run, so a driver never goes offline or gets retired mid-move
without the car settling to an idle `Motion` at its actual position.

A repositioning driver stays `accepting` (unless separately paused) and is
exactly as visible to every platform as a driver mid-service:
`PlatformView.drivers()` reads `car.motion.position_at(now)` either way, and
`_driver_accepting` never consults `relocation_id`. No platform is ever told
a driver is repositioning -- the only notifications are driver-audience
`reposition_started`/`reposition_ended` -- so this leaks nothing into a
hidden-state-free candidate list: arriving inside a platform's
`service_area` re-enables its offers through the existing position filter in
`candidates()`, with no new wiring at all.

### Deactivation and shutdown (AST-210)

`deactivate_driver(driver_id, reason)` permanently retires a driver. It
reuses `end_shift` for the presence side (stop new acceptance, cancel
pending offers, tell every open platform `driver_availability
accepting=False`, and finalize the shift once nothing remains queued) so a
boarded ride's own drop-off closes the driver's apps exactly as an ordinary
shift end would. Every commitment that is **not** currently boarded --
including one still in its pickup leg -- is canceled (`by="driver"`,
`reason="driver_deactivated"`); a commitment whose service has reached
`transport` is left alone and completes on frozen terms, and its own
drop-off's `_release_commitment` is what eventually re-enters `_finish_shift`
and closes the remaining apps. `start_shift` refuses any driver with
`deactivated_at` set (`"was deactivated (<reason>)"`), so deactivation is
permanent for the life of the run. Idempotent: deactivating an already-
deactivated driver changes nothing and notifies nothing. The runtime posts a
`driver_deactivated` observation (`policy_runtime.py`) from the notification,
and a scenario-authored shift whose driver has since been deactivated is
skipped by `main.Simulation._on_shift_start` with a logged `session_skipped`
record and matching observation -- an explicit `deactivated_at is not None`
test, never a `try/except CommandRejected` around `start_shift`, because that
would also swallow the unrelated, pre-existing "driver already on shift" run
failure an authored extension overlapping the next scheduled shift must still
produce.

`shutdown_platform(platform_id, reason="platform_shutdown")` retires a
platform: `launched=False` immediately blocks `issue_quote`, `place_order`,
and (through `_driver_accepting`) `create_offer`/`respond_to_offer` -- no
further code is needed to stop new business. Every still-pending offer of
this platform is resolved `canceled` with the given reason *first*, then
every not-yet-boarded order is canceled with the *same* reason; that order
matters, not just style: `cancel_order` already resolves an order's own
remaining pending offers itself (with reason `"order_canceled"`), so
resolving them here first means that inner loop finds nothing left to
resolve, and every canceled offer ends up carrying `shutdown_platform`'s own
reason rather than a generic cancellation one. A commitment whose service has
reached `transport` is skipped -- a boarded ride finishes on frozen terms,
exactly like deactivation's own boarded-ride rule -- and every open app
session (both roles) is then closed. Idempotent; a `platform_shutdown`
notification is published once, to the platform only.

### Insolvency (AST-210)

`Platform.insolvency == "shutdown"` (requires a tracked `starting_cash_minor`;
enforced at compile time, not here) makes a zero-or-negative cash balance
call `shutdown_platform` automatically. `_check_solvency(platform_id)` --
called at the end of `_settle` (after the settlement has fully posted) and at
the end of `post_transfer` when its `counterparty == "platform"` -- reads the
already-updated balance and, when it is tracked and `<= 0`, *schedules* a
zero-delay `platform.insolvency` event rather than shutting down inline.
Scheduling instead of executing is deliberate: `_settle`/`post_transfer` are
themselves inside an open `_transition()`, and cancelling orders synchronously
from there would re-enter a half-applied transition (`_drop_off`'s own
in-progress mutation, for the settlement path). The zero-delay event runs
`shutdown_platform(pid, "insolvency")` at the exact same clock instant but
outside that transition, satisfying plan section 10 S10's "takes effect at
that event time." A duplicate crossing is a no-op: the handler re-checks
`launched` and the balance before acting. `_rebuild_accounts` (restore) never
goes through `_settle`/`post_transfer`, so restoring a snapshot never
re-triggers a shutdown that already happened -- and one that had not yet
happened stays exactly as pending as the restored scheduler snapshot says.
Default-off: with `insolvency is None` (every preset, every scenario without
the rule) `_check_solvency` returns on its first line, schedules nothing, and
notifies nothing -- one extra call at the end of every settlement/platform
transfer, never a behavior change.

### Delays (AST-210)

Every leg's travel time -- pickup, transport, and a `reposition` leg alike --
goes through `MarketplaceEngine.travel_seconds(distance_km, origin)`, which
early-returns the *identical* expression `world.travel_seconds(distance_km)`
whenever `engine.speed_zones` is empty (not an equivalent one -- the literal
byte-identical call), so every scenario without a delay is untouched.
Otherwise it divides by `speed_kmh_at(origin)`: `world.speed_kmh` times the
`multiplier` of every `SpeedZone` whose window contains `now` (`at <= now`,
and `ends_at is None or now < ends_at`) and whose closed box contains
`origin`, multiplied in ascending `id` order for float determinism across a
snapshot/restore cycle. A static `world.speed_zones` entry has `ends_at=None`
(permanent); a `delay` intervention's `duration_hours` sets one. Because
`_begin_leg` calls `travel_seconds` once, at the instant the leg begins, and
computes `planned_end` from that single call, **a leg already in progress
keeps its originally planned end** when a delay begins or ends partway
through it -- only a leg that *begins* inside an active window is slowed.

Platform ETA estimates are deliberately *not* delay-aware:
`MarketplaceParameters.estimated_speed_kmh` (marketplace-policy.md) stays the
app's ordinary map speed, on purpose. A delayed pickup leg's actual duration
is longer than the promise, so each periodic `revise()` re-reads the car's
true (slowed) position and recomputes a *remaining* distance that shrank less
than the undelayed estimator assumed -- the predicted arrival instant drifts
further past the original promise on every re-estimate, which is exactly what
lets `rider_search@2`'s `eta_drift_cancel_seconds` (behavior-policy.md) react
to a delay with no delay-specific code of its own: it already reacts to any
growing gap between the promised and the currently predicted arrival instant.

## Offers and atomic acceptance

An offer reserves nothing globally. Several platforms may hold offers to the
same driver, and a platform may hold several offers for one order; the engine
only refuses a second pending offer from the same driver for the same order.

Acceptance validates in order: pending and unexpired (`now < expires_at`,
half-open), order still `open`, driver accepting on that platform (on shift,
no exit requested, app open and not paused, car registered, platform
launched), and fewer than two commitments. It then resolves the offer as `accepted`, binds the
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

`pause_app`/`resume_app` (AST-209) are a third, narrower level between these
two: `close_app` withdraws membership from a platform entirely (no longer in
`open_apps`, no longer counted anywhere as participating); `end_shift`
withdraws from every platform at once and eventually ends the shift itself;
`pause_app` withdraws only *new acceptance* on one already-open platform --
the app stays in `open_apps`, still counts toward that platform's observed
participation, and a `queue_expansion` for a different, not-yet-opened app
is unaffected -- while existing and future commitments on it are served
exactly as if it were never paused. All three cancel that platform's
pending offers to the driver identically (`driver_unavailable`). A paused
app resumes automatically only when a policy (via `PolicyRuntime
.apply_availability`, behavior-policy.md) decides the pausing condition no
longer holds; the engine itself never un-pauses on a timer.

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
reason and record. A settlement's driver leg posts only when
`driver_id is not None`; a rider cancelling an *unassigned* order under a
nonzero `driver_cancellation_compensation_minor` is the one case where a
settlement's `driver_payout_minor` has nowhere to post (there is no driver to
credit) -- the engine does not reject it (that would change legality for
already-authored scenarios), it is simply excluded from every account, and
`metrics.py` names the gap explicitly as `unattributed_driver_payout_minor`
in `ledger_section`/`conservation`.

Every `Transfer` conserves money at the record level: a `platform`
counterparty credits the named person by `amount_minor` and debits that
platform's account by the same amount, so the party deltas sum to zero; an
`external` counterparty (a lease, an operating cost) has no platform side at
all, so the person's delta alone IS the declared external amount --
`metrics.py` reports this as `party_delta_minor`/`transfer_party_delta_minor`
rather than pretending it nets to zero. A platform's per-transfer rows
therefore do not sum to the global transfers row whenever any external
transfer exists in the run; that gap is the external total itself, not
something to attribute to a platform.

Notifications are scoped to an audience: `platform` (own app sessions,
orders, offers, arrivals, boardings, completions, cancellations, driver
availability, `transfer_posted` for transfers it funds, and, AST-210,
`platform_shutdown`), `driver` (offers, commitments, service progress, shift
changes, `transfer_posted` when credited or debited, and, AST-210,
`reposition_started`/`reposition_ended` and `driver_deactivated`), and
`rider` (quotes, order progress, intent end, and `transfer_posted` when
credited or debited) -- an external `lease` is private to the driver; the
platform is never told about it, and repositioning/deactivation are private
to the driver in exactly the same way (no platform ever learns a driver is
repositioning or has been deactivated, except by that driver simply
disappearing from its own candidate list). `PlatformView`
exposes own orders, offers, quotes, rider requests, and `DriverPresence`
observations (position, accepting, own accepted orders, own pending offers)
for drivers with that app open. `DriverPresence.accepting` is `shift not
exit-requested` **and** (AST-209) `not paused on this platform` -- the same
two conditions `_driver_accepting` checks, so a platform's own candidate
list and the engine's own acceptance validation never disagree about who is
accepting. `DriverView` shows a driver's cross-app commitments and their
physical phase, and (AST-209) `pending_offers()`: this driver's own
unresolved offers as an id-sorted list of `PendingOfferView`s (offer,
platform, order, terms, ETA, pickup/destination) -- sorted because the
engine's internal `_pending_by_driver` is a set whose iteration order a
snapshot/restore cycle can change, and a policy comparing pending offers
must see the same order continuously or restored. `RiderView` shows the
intent, quotes, and live order. The engine's tables are the privileged
experiment observer. Views are API contracts for trusted extensions, not a
sandbox.

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
- (Phase 3) A subsidized ride (`payout_minor > rider_payment_minor`) reduces
  the funding platform's cash account by exactly `payout - rider_payment`,
  credits the driver the full payout and debits the rider the full payment;
  `Settlement.bonus_minor` matches the accepted offer's bonus.
  `driver_cancellation_penalty_minor` posts one `driver_penalty` `Transfer`
  of the configured (negated) amount on a driver-initiated cancellation, and
  posts none at all (not a zero-valued record) when the parameter is zero; a
  rider-initiated cancellation with a nonzero `driver_penalty_minor` is
  rejected before any mutation, and no `driver_penalty` transfer is ever
  found attached to a rider cancellation.
- (Phase 3) An external transfer (`counterparty="external"`) has no platform
  leg and its declared `amount_minor` is exactly the credited/debited
  person's delta; a platform transfer's person and platform deltas sum to
  zero. Every value/shape rejection (zero amount, unknown reason, a platform
  counterparty with no `platform_id`, an external counterparty with one, an
  unknown person, a float `amount_minor`, an `order_id` belonging to another
  platform, a nonzero `driver_penalty_minor` for a non-driver party) raises
  `CommandRejected` and leaves the market unchanged.
- (Phase 3) `engine.snapshot()["accounts"]`, JSON round-tripped and restored,
  reproduces every account's `balance_minor` exactly, and
  `restored.snapshot() == engine.snapshot()` -- because `restore()` derives
  accounts by replaying `settlements`/`transfers` (`_rebuild_accounts`),
  never by decoding the emitted list, this equality is itself the
  conservation proof. Replaying every settlement and transfer into an
  independent `{party: delta}` accumulator reproduces each account's own
  `settlement_minor + transfer_minor` exactly.
- (Phase 3) A full scenario run (platforms built through
  `scenario.platform(starting_cash_minor=...)`, `driver_cancellation_penalty_minor`
  set through `platforms.<id>.policy.parameters`) reproduces the same
  per-cancellation penalty transfers end to end through
  `MarketplacePolicy.cancel` -> `PolicyRuntime.cancel` ->
  `cancel_order`; a custom marketplace policy returning a `Transfer` from its
  `controller` hook (the only reachable phase-3 application site, since
  `marketplace@1`'s `controller` always returns `Stop`) is applied by
  `PolicyRuntime.apply_transfer` with the calling platform bound as funder;
  `main.Simulation.snapshot()`/`restore()` reproduce every account balance
  the same way the bare engine does. Both seed-0 `@1` presets are unaffected
  byte-for-byte (`scripts/check_scenario_readiness.py`), including a
  1000+-order scenario-review run compared field-by-field against pristine
  `origin/main`.
- (Phase 4) An unmodified platform's quote/offer exceeding an imposed
  regulation's caps is rejected with `RegulationRejected` and changes
  nothing; a platform whose parameters comply is unaffected. `SNAPSHOT_SCHEMA_VERSION`
  stays 2: `regulations` joins `_TABLES` exactly like phase 3's `transfers`
  did (`.get(name, [])` at restore, so a pre-phase-4 snapshot restores with
  an empty table and a fresh `regulation` sequence), and `Quote`'s two new
  fields restore through `_quote`'s existing `.get`-tolerant factory. A
  snapshot taken mid-run with a pending `policy.observe_window` scheduler
  event (see marketplace-policy.md) restores and continues to identical
  records once `policy_runtime.PolicyRuntime` (which owns that handler) is
  constructed before `Scheduler.restore`.
- (AST-209) `pause_app` on an open app makes `_driver_accepting` and
  `DriverPresence.accepting` both `False` for that platform only, cancels
  its pending offers to the driver as `driver_unavailable`, and leaves
  accepted commitments (and the driver's other, unpaused apps) untouched;
  `resume_app` restores `accepting` to whatever `_driver_accepting` would
  otherwise compute (so resuming during an exit request does not announce
  acceptance). `close_app` and `_finish_shift` both clear `paused_apps`, so
  a later re-open is never silently paused. `SNAPSHOT_SCHEMA_VERSION` stays
  2: `Driver.paused_apps` is additive and restores with `.get`-tolerance
  (`set()` for a pre-AST-209 snapshot). `DriverView.pending_offers()`'s
  explicit id sort makes a continuous run and one that snapshots mid-run and
  restores agree on `driver_participation@2`'s `response_rule='best_pending'`
  tie-break, across every fixture in `scenarios/fixture_v2_*.py`. Changing
  only one platform's own pricing/campaign never changes another platform's
  own `policy_decision` sequence, including while a driver is paused on a
  third platform (candidate lists stay hidden-state-free under pausing,
  exactly as without it).
- (AST-210) `scenarios/fixture_p6_reposition.py`: a driver dropped off outside
  a zone-restricted platform's `service_area` repositions back to it; the
  reposition consumes travel time (`Relocation.legs[0].planned_end -
  started_at` matches `math.dist(...)/speed_kmh*3600` exactly) but creates
  **no** settlement and **no** `Transfer` (checked at both
  `reposition_started` and `reposition_ended`); the restricted platform's
  offers to that driver are `eta_seconds is None` before arrival and a real,
  ETA-bearing offer after; `metrics.driver_distance` separates `empty_km`,
  `loaded_km`, and `reposition_km` into disjoint totals. Varying only a
  competing platform's own commission and diffing the zone-restricted
  platform's own `policy_decision` records shows them byte-identical --
  repositioning leaks nothing into a hidden-state-free candidate list.
  `scenarios/fixture_p6_bankruptcy.py`: a driver with no income is
  deactivated for `"bankruptcy"` at the first lease posting that drops its
  account below the threshold, disappears from every platform's own
  candidate list -- checked specifically at the midpoint of what would have
  been a *later*, still-scheduled shift, not merely at the run's end, where
  an ordinary (non-deactivated) driver would equally be absent for unrelated
  reasons -- and that later shift is skipped with a logged reason and no
  `shift_started` notification; the run still finishes `status="completed"`.
  A second, earning driver crosses the same lease debit without being
  deactivated. `scenarios/fixture_p6_shutdown.py` (both `--insolvency` and an
  authored `shutdown` intervention at the same simulated instant): a
  platform whose tracked cash crosses zero cancels every one of its open
  orders (one with no offer at all, one with a still-pending offer) with a
  consistent cancellation reason across the order and its offer, closes
  every open app session, and accepts no further quotes -- while a
  concurrently boarded order is left alone and later settles on exactly its
  own frozen `Quote`/`Assignment` terms, unaffected by the platform no
  longer being launched. `scenarios/fixture_p6_delay.py`: a zone delay
  slows a 6 km pickup leg from an undelayed 720.0s to 2880.0s (30 km/h times
  a 0.25 multiplier); each periodic `revise()` under the unchanged (delay-
  unaware) platform ETA estimator produces a monotonically later predicted
  arrival instant, and once the drift exceeds `rider_search@2`'s
  `eta_drift_cancel_seconds` the rider cancels with reason `"pickup eta
  drift"` -- the phase 5 mechanism this phase's delay pairs with, unmodified.
  A leg already in progress when a delay is imposed on its box keeps its
  original `planned_end`; a fresh leg begun inside the same active window is
  slowed. `scenarios/fixture_p6_shift_extension.py`: a driver whose shift
  net payout stays below an authored `daily_net_target_minor` extends twice
  by `extension_step_seconds`, then stops exactly at `max_extension_seconds`
  -- `Shift.ended_at` lands exactly at the authored duration plus the cap,
  never beyond it -- while a control with the target already met (`0`) ends
  the shift on schedule with no `Extend` decision recorded at all.
  `scenarios/fixture_p6_dividend.py`: at a dividend's first period close, the
  excess of tracked cash over `reserve_minor` is paid entirely to the driver
  meeting `min_completed_rides` in that period and withheld from one that
  does not, even though both funded the excess; a control with an
  unreachable threshold shows the same excess cash pays nothing at all --
  the qualification gate, not just the reserve check, gates payment. A
  combined snapshot/restore probe (relocation in flight, a regulation in
  force, tracked nonzero account balances, and a deactivated driver, all
  four present at one restore boundary) reproduces an identical final
  snapshot continuous or restored, with `metrics.conservation` holding on
  both. `SNAPSHOT_SCHEMA_VERSION` stays 2 throughout: `relocations` and
  `speed_zones` join `_TABLES` exactly like phase 3's `transfers` and phase
  4's `regulations` did (`.get(name, [])` at restore, so a pre-AST-210
  snapshot restores with both tables empty and fresh sequence counters), and
  `Driver.relocation_id`/`deactivated_at`/`deactivation_reason` and
  `Platform.insolvency` restore through the existing `.get`-tolerant
  factories. `scripts/check_scenario_readiness.py` (both published presets,
  seed 0) is unaffected byte-for-byte, because every one of these mechanisms
  is off by construction wherever a scenario does not explicitly select it.

Measure physical active time from service intervals, never from overlapping
accepted-order timelines; queued waiting is a separate quantity.
