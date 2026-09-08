# Marketplace engine

Status: proposed design, not implemented. This document defines the physical and
contractual mechanics used by the [phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

There is one physical market and one marketplace engine. Rebu, Blot, and Flyt
are marketplace instances in it, with separate policy state and observations.
They do not own independent copies of a driver, rider, car, or location.

## Responsibilities and invariants

The engine owns authoritative entities, physical movement, commitments, legal
transitions, scoped observation delivery, and monetary commitment/settlement
records. It uses the generic [event engine](event-engine.md). Platform decisions
belong to [marketplace policies](marketplace-policy.md), and personal decisions
belong to [behavior policies](behavior-policy.md).

Fixed constraints for the modern model:

1. Every physical ride requires one rider, one driver, and one car.
2. Each entity occupies at most one physical ride at a time. Pickup travel,
   boarding, and passenger transport use a single driver/car service sequence.
3. Each car has at most one active controlling driver. An onboard rider shares
   the driver/car position until an actual ride ending.
4. A driver has at most two accepted, nonterminal orders across all platforms.
   An order has at most one current accepted driver/car assignment.
5. Queued acceptance does not initiate competing physical movement or permit
   arrival at pickup during the preceding ride, even if that route passes it.
6. Going offline, cancellation, app changes, and shift end cannot erase occupied
   entities, release them prematurely, or silently discard commitments.
7. A person has at least one installed app and a car has at least one platform
   registration. Service requires usable access on the owning platform.

The two-order cap is fixed for this model version. It is intentionally stricter
than physical possibility. Platforms and participants can choose fewer
commitments, but cannot configure a higher cap or bypass it with a hook.

World geometry, speed, and traffic are configurable inputs. Enforcement of one
trajectory and legal occupancy does not change with those parameter values.

## Authoritative records

| Record | Meaning |
| --- | --- |
| Rider | Stable identity, app access, location when unoccupied, physical ride reference, and private profile/memory references. |
| Driver | Stable identity, app access, current car binding, shift/exit state, physical service reference, and ordered accepted commitments. |
| Car | Stable identity, registrations, controlling driver, physical position/trajectory, and occupancy. |
| Platform | Identity, launch state, local app sessions, local orders/offers, observations, and policy-memory references. |
| Trip intent | One rider's desired trip, retained across platform attempts, with once-only conversion and terminal outcome. |
| Platform order | Owning platform, intent, quoted terms, accepted assignment, platform lifecycle, and terminal outcome. |
| Offer | A proposal to one driver for one order, with response deadline and immutable offered terms. |
| Commitment | Accepted order reference and its place in a driver's service sequence. |
| Physical service | Current pickup leg, boarding, or occupied ride, with actual origin, route, times, and entity bindings. |

Initially assign one car per driver. Model explicit registration sets rather
than inferring that every car supports every app. Driver/car rebinding is not
part of the first release; a future implementation must reject it while either
has active service or an unresolved vehicle-bound commitment.

Keep platform status separate from physical status. A platform can report
`driving_to_pickup` immediately after acceptance while the engine records that
order as queued behind a different platform's passenger transport. Neither
status overwrites the other. Platform pending offers are not physical occupancy.

## Commands and execution boundary

Policies return typed commands, never mutate these records directly. Commands
include creating a quote/order/offer, resolving an offer, attempting acceptance,
requesting cancellation, changing app participation, requesting offline, and
applying a permitted membership or preference update.

For each command, resolve IDs; validate access, lifecycle, finite values, and
command permissions; apply one coherent transition; update affected records and
indexes; then publish scoped observations and schedule subsequent domain work.
Rejected commands have explicit outcomes. Internal traces retain the full cause;
a platform receives only the information appropriate to its own interaction.

Runtime legality is mandatory even for a compiled, validated scenario. An offer
may expire, an order may be assigned, or a slot may fill between policy evaluation
and command application. Check event generation and attempt identity as well.

## Offers and atomic acceptance

An offer does not acquire a global driver slot or hide the driver from other
platforms. A platform can reserve its own local offer opportunity according to
its policy. Multiple platforms may have outstanding offers to the same driver;
a dispatch policy may also propose multiple offers for an order if its declared
mode supports that, while the engine still allows only one accepted assignment.

Acceptance validates that the offer is pending and unexpired, the order is
unassigned and live, participant/car access is valid, the driver is accepting
new work, and fewer than two commitments exist. Acquire the slot and bind the
assignment in the same event transition. Record offered terms as accepted terms.

If this is the first commitment, initiate its physical pickup service. If it is
the second, append it without changing actual motion. Initial service order is
acceptance order. Only explicit cancellation removes a queued order; the default
model does not reorder accepted service opportunistically.

Close competing offers for the now-assigned order. Do not cancel unrelated
platform offers merely because a hidden global slot filled. Those platforms
cannot learn the hidden queue through proactive engine notifications. The
participant can reject them, or a subsequent acceptance can fail normally.

For one remaining slot and simultaneous Blot/Flyt acceptance commands, sequence
ordering selects one winner. For two drivers accepting one order, one assignment
wins. The engine never temporarily grants three commitments or two assignments.
Expose a generic inability to accept to the platform, not competitor IDs,
destinations, or a global slot count. Participant views may show their own slots.

Give each offer exactly one terminal disposition. A still-pending offer whose
acceptance cannot acquire a valid assignment/slot ends as `acceptance_failed`;
at or after its deadline it ends as `expired`. A repeated response to an already
terminal offer is a stale diagnostic, not another terminal outcome. This keeps
offer cohorts reconcilable while dispatch reacts to the actual attempt result.

## Actual motion and queue promotion

Resolve physical position from one active trajectory at time `t`. Start with
straight-line interpolation at scenario world speed. Idle entities have fixed
positions. An onboard rider follows the same trajectory; a waiting rider stays
at their own location. All platforms observing at the same time get the same
physical coordinates, subject to app access, without competitor service metadata.

Platform route and distance calculations are estimates or proposals. Validate
any route used for movement against actual origin and world constraints. A
platform estimate does not directly change position, speed, or arrival time.
An obsolete route origin requires a new valid route, never teleportation.

Only physical service events can report pickup arrival, boarding, or drop-off.
Boarding binds the waiting rider to the driver/car and checks that all three
are free of a conflicting physical ride. Drop-off updates actual position and
ends occupancy. Finalization releases the completed commitment once and promotes
the next live commitment; pickup starts from this actual drop-off location.

Do not schedule a queued arrival for `accepted_at + quoted_eta`. An ETA revision
also cannot trigger an arrival. A pickup colocated with the previous destination
can have zero travel duration, but arrival still occurs after the earlier ride
has ended through normal event ordering.

## Offline and cancellation

Distinguish market shift/physical presence from app participation. A request to
end a shift immediately stops acceptance of new work. Actual offline finalization
waits until active service and accepted commitments are resolved. An app with an
unfinished order cannot erase that order by being closed; an unrelated app can
stop accepting new offers without affecting the occupied ride elsewhere.

Default shift-end policy finishes already accepted service, including the queued
order, then goes offline; it accepts no replacements during that drain. A stricter
platform/participant policy may request cancellation of the queued order, subject
to its cancellation terms. This is an explicit resolved transition, not silent
removal. Offline behavior must be recorded in scenario defaults and comparisons.

Default cancellation permits ending an order before boarding and charges no fee.
Canceling a queued order frees only that commitment, leaves current movement
unchanged, and invalidates its future events. Canceling the order currently in
pickup service stops that leg at its actual position before promoting the queue.
If a future selected policy permits early termination after boarding, it must
end the physical ride at an actual valid stop before releasing entities. Initial
built-in cancellation does not allow an onboard rider simply to disappear.

The platform decides permission and financial consequences. The engine decides
how the permitted transition can occur physically. Completion and cancellation
are mutually exclusive terminal outcomes. Retries create a new platform attempt
under the same trip intent; no stale accepted assignment survives that retry.

## Money and observation boundaries

Persist immutable quote terms, offer terms, accepted commitments, and settlement
identity. Validate and apply the amounts returned by supported financial policies.
Modern monetary records use integer minor units with a declared rounding rule.
Each settlement conserves rider payment = driver payout + platform contribution;
contribution may be negative. A completed-ride reward settles only once, while a
cancellation fee, if configured, uses a distinct reason and settlement record.

Publish a platform view of its own records, authorized attributes, and location
observations. Do not expose physical service owner, other-platform destination,
hidden commitment count, or true hidden release time. Local candidate indexes
must not silently remove drivers based on competitor activity. A platform may
infer behavior from its own observations, but cannot read the experiment trace.

Publish each participant's own cross-app commitments and memory to their behavior
policy. Restrict knowledge of other people. The privileged experiment observer
can record physical truth for analysis; observer records are not policy inputs.

Typed views are API contracts for trusted extensions, not a Python security
sandbox. Use read-only snapshots and explicit owned memory rather than passing
the mutable simulation object to plugins.

## Tests and implementation implications

Replace single driver `pending_order`/`current_order` fields and state derived
from one order. Split current dispatch selection from offer/acceptance mechanics.
Stop using `_driver_is_eligible` as a universal global-idle candidate filter.
Separate the current `_accept_offer` scheduling of pickup from acceptance itself.

Required tests include the roadmap's six-plus-four versus three minute ETA
fixture; same-platform informed estimates; a third order on any platform mix;
two last-slot accepts; two drivers accepting one order; passing a queued pickup
during another ride; queue cancellation; shift end with two commitments; stale
arrival after cancellation; and checkpoint restoration during an occupied ride
with a queued order and outstanding competitor offers.

Check physical occupancy, assignment/slot conservation, one settlement, and
position continuity after every tested transition. Verify that changing only
hidden competitor metadata cannot change a platform policy's supplied view.
Measure physical active time from service intervals, not overlapping accepted
order timelines; queued waiting is a separate metric.
