# Marketplace policy

Status: implemented initial policy layer. Part of the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

Marketplace policies describe how Rebu, Blot, and Flyt each operate. They choose
commercial and operational rules, including rules conditional on known rider or
driver segments. The [marketplace engine](marketplace-engine.md) validates and
executes their commands; [behavior policies](behavior-policy.md) decide how people
respond. These are separate decisions with different information available.

## Shipped implementation and configuration

Executable platform logic lives in `marketplace_policy.py`; `policy_runtime.py`
builds detached immutable contexts and applies the typed proposals through
`marketplace_engine.py`. `main.py` contains no pricing, matching or dispatch
model. `behavior.py` has been removed; participant model details are owned by
[Behavior policy](behavior-policy.md).

`PlatformPolicy.compile(defaults=..., overrides=..., rules=..., campaigns=...,
version="modern-v1", fallback="default")` provides strict Python binding.
Unknown parameters, invalid amounts, unsupported model names, duplicate rule
priorities/IDs, duplicate campaign IDs and a local commitment ceiling over two
are errors. Each rule has `id`, `priority`, `when` and `parameters`. Conditions
use exact equality on `role`, `segment`, `new_user` or `completed_rides`. Segment
is present only for platforms named in the person's `disclosed_to`; completion
history is maintained separately by platform and role. The explicit fallback is
the fully resolved default parameter set. This binder is not the full scenario
compiler; scenarios bind the same values through `platforms.<id>.policy`.

```python
from marketplace_policy import PlatformPolicy

rebu = PlatformPolicy.compile(
    defaults={"per_km_minor": 150, "commission_fraction": .2},
    overrides={"base_fare_minor": 200},
    version="launch-v1",
    rules=[{
        "id": "new-rider", "priority": 10,
        "when": {"role": "rider", "new_user": True},
        "parameters": {"multiplier": .9},
    }],
    campaigns=[{
        "id": "launch", "start": 0, "end": 86400,
        "discount_fraction": .2, "discount_cap_minor": 500,
        "bonus_minor": 100, "awareness": "announced",
    }],
)
```

Amounts ending in `_minor` are integer minor currency units. The physical world
sets the currency subdivision once. Tariff inputs are `base_fare_minor`,
`per_km_minor`, `per_minute_minor`, `minimum_fare_minor`, `multiplier` and
`commission_fraction`. Rounding uses decimal half-up on gross, discount and base
payout; fixed integer bonuses are already normalized. Campaigns choose either
`discount_minor` or `discount_fraction`, optionally `discount_cap_minor`, and
`bonus_minor`. They support `segment`, `new_user_only`, and `awareness` equal to
`announced` or `in_app`. Public announcements affect discovery; committed terms
are always visible in the quote or offer itself.

Named alternatives are `matching="nearest"` or `"pickup_eta"`, and
`estimator="own_service"` or `"current_position"`. Both use straight-line
kilometres with independently configured `estimated_speed_kmh` and
`estimated_boarding_seconds`; neither controls physical travel. A strict local
policy selects `max_local_commitments=1`; modern back-to-back selects two with
`back_to_back_within_seconds` (default 1800). The nearest matcher breaks ties by
stable driver ID, numerically for numeric IDs. It never filters on private free
slots. Quote ETA is the minimum estimated pickup time over local candidates.

Dispatch uses `max_attempts=5`, `retry_drivers=False`, `retry_seconds=1`,
`order_patience_seconds=60`, `offer_seconds=10`, and `quote_seconds=30` by default.
No-candidate decisions wait within the order deadline; they do not consume an
offer attempt. Offers are clipped to the order's remaining dispatch patience.
There is one unresolved offer per local order and per local driver. All retry
scheduling carries order identities and generations.

Quotes and offers store their policy version, selected rule, campaign ID and
prediction time. Orders keep `eta_predictions` with target `pickup_arrival` and
source quote, offer or revision. Assignment and completion of preceding own
service trigger local revisions. A revision excludes the target ride's boarding
and transport. It cannot trigger arrival or change committed payments. Physical
leg origins are chosen by the engine when service actually starts. Exports keep
estimated trip distance and actual completed transport distance separately.

`rider_cancellation_fee_minor` and
`driver_cancellation_compensation_minor` configure separate cancellation
settlements for rider requests before boarding; defaults are zero. Driver and
platform cancellation requests have zero fees. The current cancellation policy
is consulted at request time; accepted completion payouts are never changed.
Canceled attempts do not advance completed-ride eligibility.

The initial controller is fixed: `controller` produces no tariff change. A
platform's scenario `controller` setting (`interval_hours`, `until_hours`)
schedules a bounded cadence with explicit serializable memory. Timed
`policy_change` interventions in the scenario select new compiled versions;
scenario campaigns declare `start_hours`/`end_hours`, which the compiler
converts to simulated seconds. Automatic surge, broadcast dispatch,
road-network routing, campaign budgets, stacking variants and quests remain
extensions rather than advertised implementations. Built-in declarations expose
parameter types, permitted observations, typed outputs, lifecycle hooks and
versioned JSON memory fields. Snapshots preserve the selected built-in versions
and platform-local completion memory.

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
output types, memory schema, and supported lifecycle hooks. The strict Python configuration binding validates parameter schemas and binds
the built-in implementations. The broader [scenario compiler](scenario-definition.md)
remains a separate layer. Runtime checks still validate outputs.

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
are not supported. All quotes use the modern commitment and rounding rules.

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
