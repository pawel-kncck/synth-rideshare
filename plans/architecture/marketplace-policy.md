# Marketplace policy

Status: proposed design, not implemented. Part of the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

Marketplace policies describe how Rebu, Blot, and Flyt each operate. They choose
commercial and operational rules, including rules conditional on known rider or
driver segments. The [marketplace engine](marketplace-engine.md) validates and
executes their commands; [behavior policies](behavior-policy.md) decide how people
respond. These are separate decisions with different information available.

## Policy responsibilities

| Policy family | Inputs available to the platform | Result |
| --- | --- | --- |
| Candidate eligibility and matching | Local app participation, registrations, own commitments/offers, authorized location/segment observations | Eligible local candidates and ranking or assignment proposals |
| Dispatch | Own unassigned order, attempt history, local candidates | Which offers to issue, when to retry, or when to end the attempt |
| Distance, routing, and ETA | Observed coordinates, own trip details and known ongoing service, configured map/estimator | Estimated distances/times or a route proposal |
| Pricing | Own quote request, estimated distance/duration, visible segments, own market observations | Versioned gross fare terms |
| Incentives and payout | Campaign windows, eligibility, own rider/driver history and awareness rules | Discount, commission, bonus, and their commitment conditions |
| Offer terms | Candidate/order context and visible segments | Offer deadline and any supported local offer restrictions |
| Cancellation | Own lifecycle, requesting party, observed delay, declared terms | Permission, reason, and any cancellation financial terms |
| Controllers | Own accumulated observations and scheduled decision cadence | Proposed tariff or other permitted local policy-state updates |

Initially provide simple implementations and named alternatives. Interfaces can
support future automatic surge or richer matching without claiming these models
exist in the first release. A platform cannot change physical limits, rewrite
another platform's policy, move an entity directly, or inspect the full market.

## Contract and segment resolution

A policy receives a read-only context, its explicit platform-owned memory, and
controlled randomness when needed. It returns a typed proposal and updated
memory. Context includes simulated time and stable IDs. Commands go through the
engine; no direct queue, mutable world object, wall-clock dependency, or ambient
global RNG is part of the supported interface.

Each implementation declares its version, parameter schema, permitted inputs,
output types, memory schema, and supported lifecycle hooks. The
[scenario compiler](scenario-definition.md) validates those declarations and
binds selected implementations. Runtime checks still validate outputs.

Resolve shared platform defaults, then platform-specific overrides, then the
highest-priority matching conditional rule. Require explicit unique priorities
and a fallback for conditional tables. The first matching rule produces one
resolved parameter set; do not accidentally stack every matching rule. Record
the selected rule and version on the decision. Unused or wrong-model parameters
are errors, not silently ignored configuration.

Conditions can use only fields visible to that platform. A scenario-wide latent
trait is not automatically a visible segment. A rider can be a returning customer
on Rebu and a new user on Blot. Segment definitions and disclosure belong in the
scenario. Dynamic conditions are evaluated at runtime; they are not all resolved
once at compilation.

## Matching, dispatch, and offer lifecycle

Start with nearest locally eligible driver and stable driver-ID tie resolution.
Default maximum attempts is five distinct drivers per platform order, and default
offer expiry is ten seconds. Both are policy values and can vary by platform or
segment. Offer response delay belongs to the participant policy.

The initial dispatch policy sends one unresolved offer per order at a time and
allows one unresolved offer to a driver within that platform. These local rules
do not prevent competing platforms from sending offers simultaneously. Contracts
can support an explicitly selected broadcast mode later, while engine acceptance
still assigns only one driver to an order.

A platform may offer to a driver who is carrying a competitor's rider. It cannot
query global physical idleness, hidden destinations, or global free order slots
to construct its candidate list. Use open-app participation, registration, own
offers/accepted orders, and observed location. Own back-to-back eligibility can
depend on the estimated remaining duration of the platform's current ride.

The modern preset permits locally eligible back-to-back offers with at most two
own accepted orders per driver; the engine's market-wide ceiling is also two.
A platform can select a stricter policy that does not offer during its own known
service. That does not grant knowledge of service elsewhere.

Rejection, expiry, or an unsuccessful acceptance can trigger the next bounded
attempt. Define retry delay, whether a driver can be retried, and overall order
patience explicitly. Defaults use distinct drivers and positive retry delays.
Legacy compatibility retains its existing immediate bounded redispatch behavior.
When no attempt remains, end the platform order and notify the rider policy;
do not automatically end the underlying trip intent inside dispatch logic.

Expiry is a half-open validity rule: acceptance at or after the deadline fails.
An offer can still fail before its deadline if its order or assignment context
changed. The owning platform gets the outcome of its own offer, without another
platform's IDs or a global capacity count.

## Distance, routing, and ETA

Every platform selects its own distance and duration estimator. Price can use
estimated trip distance; matching can use estimated pickup distance or another
supported score. Store estimated and actual distances separately.

An estimator can read observed current position and its own unfinished service.
For a known same-platform back-to-back trip, include remaining known service
plus travel from its estimated destination to the next pickup. If the current
ride belongs to another platform, its remaining duration and destination are
absent. The default estimator uses visible current position and may underpredict.

Store ETA prediction time, prediction target, input policy version, and later
revisions. Define ETA as remaining time to pickup arrival at the prediction time,
excluding the next rider's boarding and passenger journey. A revision uses only
newly available local information. Knowing one's own ride permits an informed
prediction; it does not force every estimator to be perfect.

Route proposals do not determine physical truth by themselves. The engine
validates actual starting position and world constraints before executing a leg.
A queued order's stale origin must be replanned when service starts. Arrival is
published from actual movement, never from an estimate or its expiration.

## Pricing, incentives, and commitments

Default quote calculation uses estimated trip length and duration:

```text
G = max(minimum_fare, multiplier *
        (base_fare + per_km * estimated_km + per_minute * estimated_minutes))
D = eligible fixed or percentage rider discount, subject to caps
base driver payout = (1 - commission_fraction) * G
driver payout = base driver payout + eligible completion bonus B
rider payment = G - D
platform contribution = rider payment - driver payout
```

Use one currency initially. Normalize modern amounts to integer minor units,
with decimal round-half-up at documented commitment boundaries: rounded gross
fare first, rounded and capped discount, rounded base driver payout, and rounded
bonus. Compute contribution as the residual of settled payments so conservation
is exact even when rounding means it differs slightly from `c * G - D - B`.
Validate finite nonnegative fares/bonuses, discount no greater than gross fare,
and commission fractions in `[0, 1]`. Negative contribution is allowed.

Each platform can set its own tariffs, commissions, and campaigns. Discounting
the rider does not reduce base driver payout; the platform funds the discount.
A driver bonus does not increase rider payment. Currency changes within a run
are not supported initially. Legacy quotes retain existing arithmetic through
their explicit compatibility policy, without silently imposing modern rounding.

Supported initial campaign settings include start/end, fixed or capped percentage
rider discount, fixed completion bonus, visible segment/new-user eligibility,
and how participants learn about the offer. Default overlap behavior is nonstacking:
choose the greatest eligible discount and greatest eligible bonus independently,
breaking equal values by stable campaign ID. Other stacking rules need explicit
supported implementations, not an ambiguous list merge.

Freeze gross fare and rider discount when issuing a quote, until its explicit
expiry. Order creation binds those terms. Freeze driver payout/bonus terms when
issuing an offer, and bind them on acceptance. A queue delay, campaign ending,
tariff update, or later segment change cannot silently reduce an accepted bonus
or reprice an existing order. Expired quotes require a new quote and rider choice.

Use half-open campaign windows `[start, end)` and version lookup at decision
time. Previously committed terms survive boundaries. Default settlement occurs
once on completion, and canceled orders earn no completion rewards. Configured
cancellation fees produce a separate settlement; cancellations still do not
consume a completed-ride entitlement.

Aggregate budgets, first-N redemptions, and multi-ride quests are extensions.
They require commitment reservation/release rules before being advertised in a
quote or offer. A budget cannot be implemented by reducing a promised payment
at settlement. Contribution is not profit and excludes operating costs/taxes.

## Cancellation and policy changes

Cancellation policy decides when a rider/driver/platform can request ending an
order, which reason applies, and any fee/payout. Default permits cancellation
before boarding with zero fees. A delay-sensitive rider decides whether to
request it in their behavior policy. The engine enforces a valid physical ending
and clears only the relevant commitment.

Timed interventions select new policy versions at safe decision points. Existing
quotes, offers, and accepted terms remain committed. A policy update can change
future dispatch or estimates but cannot erase a current order or reorder occupied
service. Store platform controller memory explicitly for resume and replications.

## Validation and acceptance

Test independent platform tariffs and segment overrides; hidden-state-free
candidate lists; back-to-back versus cross-platform ETA inputs; distinct retry
limits; exact expiry boundaries; immutable accepted bonuses; overlap/tie rules;
rounding and negative contribution; queued cancellation; and rejection of a
policy requesting more than two local accepted commitments.

For the same platform-visible context and controlled randomness, changing only
hidden competitor metadata must not change the policy result. Runtime command
success may differ because the engine enforces physical truth. Record that
difference without passing privileged reasons back into platform policy state.
