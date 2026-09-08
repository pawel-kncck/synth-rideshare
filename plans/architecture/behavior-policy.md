# Behavior policy

Status: proposed design, not implemented. Part of the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

Behavior policies decide what riders and drivers do. They model personal
preferences, sensitivities, attention, learning, and willingness to accept a
second order. [Marketplace policies](marketplace-policy.md) determine what each
platform offers; the [marketplace engine](marketplace-engine.md) enforces which
requested actions are physically and contractually valid.

## Decision contract and information

Each decision receives a read-only personal context, explicit policy memory,
and controlled random values. It returns a typed action or action proposal,
updated memory, and an explanation suitable for diagnostics. The engine applies
the action, rechecking legality at execution time.

Examples include open/close an app, request a quote, order a valid quote, accept
or reject an offer, wait until a bounded future decision, request cancellation,
request offline, download an app, or update preference. Outputs cannot mutate
another person's profile, move a car, finalize a ride, or create money directly.

Participants may know their own commitments across apps. Drivers know which
ride they are actually serving and whether they have a queued order. Riders
know quotes they have observed and their own order's progress. Neither receives
unrestricted knowledge of other people's preferences, competitor supply, or
future events. A driver may make an informed choice about a hidden-to-Blot Rebu
ride without disclosing that ride to Blot automatically.

Keep stable traits, learned memory, and per-trip/per-shift state separate. Memory
is serializable and owned per person/policy; it is not hidden in shared mutable
plugin globals. Pure calculations such as the existing logistic helper remain
reusable implementation components rather than the sole supported choice model.

## Population traits and app access

Support all seven nonempty app subsets independently for riders and drivers.
Choose preferred apps conditionally on access. Separate installed apps, usable
accounts, current open apps, preferred app, and learned preference scores.
Downloading an app does not automatically make it preferred or open.

Stable traits can include acceptable price ratio, ETA tolerance, no-offer
tolerance, sensitivities, loyalty, search friction, patience, response delay,
second-order willingness, adoption propensity, and learning rate. Explicit
profiles support deterministic tests; seeded segments can correlate these traits.
Sample traits once rather than rerolling a different person on every trip.

Cars have registration sets rather than human preferences. A driver's usable
platforms depend on their access and their assigned car's registration. A declared
onboarding policy may register the car when the driver adopts an app; these are
separate recorded changes. Do not let a download silently bypass vehicle access.

## Rider search and purchase

Default sequence:

```text
trip intent -> preferred usable app -> quote
  -> evaluate observed price, ETA, availability, preference, and outside option
  -> order, leave, or open another installed app
  -> on failed/canceled attempt, retry within the same intent if permitted
  -> physically complete at most one ride
```

Price dissatisfaction is relative to a route reference fare independent of
the platform's current discount. ETA dissatisfaction is relative to personal
tolerance in seconds. A possible default opening response is
`1 - exp(-sensitivity * normalized_excess)`, combining price/ETA excesses on
declared scales. No supply or failed dispatch can trigger an alternative even
without a positive price excess.

Keep the trigger to inspect another app separate from the eventual purchase
choice. Compare only observed, valid quotes on a common utility scale using
net price, pickup ETA, preference, and search cost, with an outside option.
Choose the next unseen app using known preference and announced incentives,
without consulting its hidden quote or supply in advance.

Draw the outside option once per trip and reuse per-platform taste draws across
retries. Merely asking for another quote must not grant a new independent chance
to convert. New observations can change utility, but an identical repeated quote
with the same context cannot manufacture extra demand.

Default app opening and retries have positive delays. Bound distinct app visits,
quote refreshes, order attempts, and total patience separately. The initial
preset permits one order attempt per installed platform, up to three. A policy
can choose different finite budgets. Once budget/patience is exhausted, terminate
the intent explicitly. Use identities/generations on delayed decisions.

The default rider holds one live platform attempt for an intent. Cancellation
before boarding can allow another attempt only after the first is actually
closed. No additional order is created merely because a request to cancel was
sent. Once onboard, switching apps cannot transfer the rider into a second ride.
Conversion means the first order under the intent, not each order attempt.

## Driver app participation and waiting

Default drivers open their preferred usable app at shift start. App expansion
responds to time without an offer; offer acceptance is a separate decision.
Support retaining opened apps after service and resetting optional offer channels
to the preferred app as alternative policies. Apps with unresolved commitments
retain the participation needed to fulfill or explicitly cancel those orders.

Maintain distinct clocks and observations:

| Measure | Meaning |
| --- | --- |
| Physical idle time | Time with no pickup, boarding, or passenger service; considering offers alone is idle. |
| No-offer time | Elapsed time in the current offer-seeking episode since it began or any relevant offer arrived. |
| Commitment wait | Acceptance until that order begins physical pickup service. |
| First post-drop-off offer wait | Drop-off to first subsequent offer when entering an offer-seeking idle episode; may be censored. |
| Back-to-back readiness | A next order was already accepted at drop-off, so no idle offer wait was required. |

Receipt of an offer resets the default no-offer clock even if rejected. Opening
another app does not reset it. After an unqueued ride ends, a new idle search
episode starts. If a next order is already queued, promote it without inventing
a post-drop-off waiting interval. Report the existing commitment separately
rather than treating it as a new offer arriving at time zero.

Schedule app expansion at a personal threshold; after opening one alternative,
use a positive further delay if another remains. Default expansion applies to
idle offer-seeking episodes. A separate configurable policy can expand apps while
serving a ride with one free commitment slot. Open apps may continue delivering
offers during a ride even when busy expansion is disabled.

Multi-app mode adds an app; exclusive-switch mode changes which optional app is
seeking new offers. Neither changes physical position or silently closes an app
needed for an accepted order. No expansion loop remains when all usable apps are
open. When both slots are occupied, suspend searches for additional commitments;
platforms may still send offers based on their local knowledge.

Use generation guards for idle/busy phase changes, offer receipt, app changes,
shift end, and acceptance. Explicit response/attention policies determine how
overlapping offers are handled; default response decisions are scheduled after
the configured delay and re-evaluate personal capacity when they execute.

## Driver acceptance, cancellation, and shift exit

Acceptance can consider offered payout and bonus, displayed pickup ETA, private
remaining workload, expected actual pickup delay, preference, and risk of rider
cancellation. Store displayed and personally estimated delay separately. A driver
can knowingly accept Blot while still serving Rebu, even if Blot's estimate is
optimistic. Per-segment willingness to take a second order is configurable.

Default modern behavior considers a second order when one slot remains. It does
not request a third; the engine cap remains mandatory for custom policies and
races. A delayed acceptance can still fail, and policy memory must handle the
actual result rather than assuming that returning `Accept` succeeded.

Drivers and riders may request cancellation based on their observed circumstances.
The platform decides contractual permission and terms, and the engine performs
the legal ending. Default driver shift exit stops new acceptances and drains
already accepted orders. Alternative exit behavior may request queued-order
cancellation but cannot simply release the active car or passenger.

## Adoption and learned preference

Keep total population and exogenous trip/shift schedules fixed in the initial
experiment preset. Participants retain access and experience over multiple weeks.
Downloads and preferences evolve independently, with scheduled interventions
handled by the scenario and engine at safe boundaries.

At a configurable checkpoint, compute each missing launched app's adoption rate
from personal segment, awareness, friction, and dissatisfaction. Convert a rate
per simulated day with `p = 1 - exp(-rate * elapsed_days)`. For several apps,
use the combined hazard for whether to download and proportional rates for the
target. Initially permit at most one download per checkpoint; document the
coarse-time approximation at high rates. Zero rates freeze adoption. Exposure
must allow a one-app person to discover alternatives without global knowledge.

Initial evolution adds apps without uninstalling, preserving at least one app.
Allow an explicit onboarding policy to coordinate driver adoption and assigned
car registration. No occupied ride or existing contract changes on adoption.

Learn from personal observations: rider net prices, prediction errors, waits,
and failures; driver offers, waiting, service time, payout, and cancellations.
Normalize for route/time differences, smooth and bound rewards, and retain priors
for unused platforms. Do not add a second preference reward for a promotion
already represented in experienced net price or payout.

Busy drivers can receive new offers. Track idle opportunity and opportunity
while busy with one free slot separately, together with which apps are open.
The personal learning estimator may use the driver's own workload. It cannot supply those
private exposure labels to a platform automatically. Stop accepting-opportunity
exposure when both slots are filled; unrelated platform offers may still arrive
and should be recorded separately from usable opportunities.

A first estimator can use offers per opportunity-time, with prior rate and prior
exposure, separately for idle and busy phases. Its inverse estimates waiting in
that phase. Combine with normalized payout/service observations; do not treat
overlapping queued waits as extra physical working hours. Retain raw observations
to replace this approximation. A wait ended by accepting another app's offer is
censored for the losing app, not a completed wait or a zero-wait success.

Use a preference margin and cooldown before switching the preferred app. Learning
rate zero freezes learned changes; explicit interventions remain separate. At a
checkpoint, apply due interventions, snapshot the population, calculate all
learning/adoption decisions against that snapshot, then apply results together.
New preferences affect the next permitted decision and do not rewrite commitments.

## Acceptance and reproducibility

Test one/two/three-app people; isolated price/ETA/no-supply triggers; bounded
search; identical repeated-quote decisions; cancellation then retry; no-offer
reset on rejected offers; app-opening without clock reset; queued drop-off with
no new idle episode; busy offer acceptance; two competing delayed responses;
zero-rate adoption/learning; conditional preference validity; and checkpoint
resume with private memory and unchanged random identities.

Policies declare observations and memory versions for the
[scenario compiler](scenario-definition.md). The
[experiment runner](experiment-runner.md) supplies stable randomness by person,
intent, and purpose. A custom Python policy is trusted code with tested contracts,
not something a compiler can generally prove correct or guaranteed to terminate.
