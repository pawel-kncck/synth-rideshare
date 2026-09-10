# Marketplace policy

Status: platform-side state and pricing structures landed in phase 4 (this
document); phases 1-3 (observability, geography, ledger/transfers) are
merged. Part of the [phase 3 roadmap](../phase-3-multi-platform-marketplace.md)
and the [scenario readiness plan](../scenario-readiness-plan.md).

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
programs=..., version="modern-v1", fallback="default")` provides strict Python
binding. Unknown parameters, invalid amounts, unsupported model names,
duplicate rule priorities/IDs, duplicate campaign or program IDs and a local
commitment ceiling over two are errors. Each rule has `id`, `priority`, `when`,
`parameters` and an optional `start`/`end` window (seconds; both or neither).
Conditions use exact equality on `role`, `segment`, `new_user`,
`completed_rides`, `origin_zone` or `destination_zone`. Segment is present only
for platforms named in the person's `disclosed_to`; completion history is
maintained separately by platform and role; zone labels are present only when
the scenario declares `world.zones` and resolve on the *ride* side only (a
rider/order's `visible`), never the driver's -- see "Contract and segment
resolution" below. The explicit fallback is the fully resolved default
parameter set. This binder is not the full scenario compiler; scenarios bind
the same values through `platforms.<id>.policy`.

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
    programs=[],  # every PlatformPolicy.compile call site must pass this, even when empty
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

`revise_interval_seconds` (default `0`, off; AST-209, plan section 4.E) adds a
third, *time-driven* trigger alongside assignment and preceding-completion: while
positive, `PolicyRuntime` re-revises an assigned, not-yet-arrived order every
`revise_interval_seconds` seconds (started at `order_assigned`, rescheduling
itself until the order arrives or ends) using the platform's own `revise()`
hook and estimator, unchanged. This is what lets a rider's
`eta_drift_cancel_seconds` (behavior-policy.md) see drift on a ride the
platform is not otherwise re-estimating -- without it, nothing would ever
*produce* a revision for that check to react to. Like
`guarantee_window_seconds`, the runtime reads only the platform's **base**
parameter, never a rule-resolved value, so the cadence cannot silently vary
by segment; it is nonetheless a `MarketplaceParameters` field like any
other, so `platform()`/`policy_change` author it identically.

`rider_cancellation_fee_minor` and
`driver_cancellation_compensation_minor` configure separate cancellation
settlements for rider requests before boarding; defaults are zero. Driver and
platform cancellation requests have zero fees, except `driver_cancellation_penalty_minor`
(phase 3, default zero), which `MarketplacePolicy.cancel` fills into
`Cancel.driver_penalty_minor` only when the requesting party is the driver;
`cancel_order` posts it as a `driver_penalty` `Transfer` (marketplace-engine.md
"Money accounts"), never a `Settlement` -- it is not a ride-bound amount. A
zero penalty (the default, and always the case for a rider or platform
cancellation) posts no record at all. The current cancellation policy
is consulted at request time; accepted completion payouts are never changed.
Canceled attempts do not advance completed-ride eligibility.

The initial controller is fixed: `controller` produces no tariff change --
`marketplace@1` always returns `Stop`. A platform's scenario `controller`
setting (`interval_hours`, `until_hours`) schedules a bounded cadence with
explicit serializable memory. As of phase 3 the `controller` hook may also
return a `Transfer` proposal (`policy_contracts.Transfer`, declared in
`MarketplacePolicy.declaration`'s outputs); `PolicyRuntime.apply_transfer`
applies it with the calling platform bound as the funder (a platform may only
post its own transfers, mirroring cancellation's own-order-and-party check),
then posts the returned memory exactly as a `Stop` would. Phase 4 adds a
second call site, `observe()` (below), which the built-in `@1` policy *does*
use, to post `guarantee_topup` transfers -- see "Platform state and the
observe hook". Timed `policy_change` interventions in the
scenario select new compiled versions; scenario campaigns declare
`start_hours`/`end_hours`, which the compiler
converts to simulated seconds. Automatic surge, broadcast dispatch,
road-network routing, campaign budgets, stacking variants and quests remain
extensions rather than advertised implementations. Built-in declarations expose
parameter types, permitted observations, typed outputs, lifecycle hooks and
versioned JSON memory fields. Snapshots preserve the selected built-in versions
and platform-local completion memory.

**Not in this release**: automatic/algorithmic surge (a scenario still sets
`multiplier`/`surcharge_minor` explicitly through rules or `policy_change`,
never a self-adjusting controller); broadcast (multi-driver) dispatch; road-
network routing; multi-ride quests and stacking-discount variants; and a
policy that computes its own tariff from observed market conditions rather
than authored parameters and rules. Plan section 4.D (phase 5) and later name
the phases that add richer participant/controller models. (Driver cancellation
penalties and platform/driver cash balances with ledger `Transfer`s landed in
phase 3, section 4.A; zone/window rules, surcharges, first-N and budgeted
campaigns, driver earnings guarantees, dispatch lockouts, service areas and
market-wide regulation landed in phase 4, section 4.B-4.C -- see the two new
sections below.)

The fare binds at quote time and the commission at offer time by default
(`commission_binding="offer"`); setting `commission_binding="quote"` binds the
commission fraction into the quote itself, so an order that straddles a
`policy_change` (or a `regulation`) pays the contract in force when it was
*quoted*, not when it was dispatched -- see "Pricing, incentives, and
commitments" below. Both bindings still freeze into the order/assignment;
nothing later reprices either. A policy *can* refuse to quote: `quote()` may
return `Stop(reason)` (today only for a request outside the platform's
declared `service_area`); `PolicyRuntime.quote` records a `quote_refused`
observation and re-queues the rider (treating a refusal like missing supply)
rather than raising or silently stalling the search.

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

A rule's `when` may also match `origin_zone`/`destination_zone`, resolved from
the request/order's pickup and destination against `world.zones` (the first
declared zone id, sorted, whose closed box contains the point; `None` outside
every zone). These labels are computed only when the scenario declares zones
and are merged into the *rider-side* `visible` dict `PlatformPolicy.resolve`
already reads for `quote`/`dispatch`/`cancel` -- **never** into a driver's own
`visible`, so a zone-conditioned rule can select fare/campaign/service-area
terms for a ride but never a driver-side parameter (commission binding,
`offer_seconds`, `max_local_commitments`, `driver_lockout_seconds`,
`service_area`) by the ride's geography; a platform that wants zone-
conditional commission sets `commission_binding="quote"` so the quote-time
resolution (which does see zones) decides it. A rule may also declare a
half-open `[start, end)` time window (seconds; both `start`/`end` or
neither); a windowed rule never matches when `resolve` is called without a
clock (only `dispatch`/`quote`/`revise`/`cancel`/`candidates` pass one).
Overlapping zones are legal; a point on a shared edge belongs to whichever
zone sorts first.

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
    + surcharge_minor
S = round_half_up(surcharge_minor * surcharge_driver_share)   # driver's flat, commission-exempt share
D = eligible fixed or percentage rider discount, subject to caps and campaign budget room
c = commission_fraction bound at quote time if commission_binding="quote", else at offer time
base driver payout = round_half_up((G - S) * (1 - c)) + S
driver payout = base driver payout + eligible completion bonus B
rider payment = G - D
platform contribution = rider payment - driver payout
```

`surcharge_minor` (default 0) is added to the multiplicative fare after
rounding-eligible terms are combined, before the final gross rounding;
`surcharge_driver_share` (default 0, range `[0,1]`) is the fraction of that
surcharge paid straight to the driver, exempt from commission -- with
`surcharge_driver_share=1` the driver receives the flat surcharge dollar-for-
dollar on top of their commissioned base fare. At the defaults
(`surcharge_minor=0`, `surcharge_driver_share=0`, `commission_binding="offer"`)
every term above reduces character-for-character to today's expression:
`G` is unchanged (`+0`), `S=0`, and `c` is always the offer-time
`commission_fraction`.

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

`Campaign.max_completed_rides` caps first-N redemption: a campaign is eligible
for a person only while `visible.completed_rides < max_completed_rides` (the
same per-platform completion count "Contract and segment resolution" already
tracks), so a rider's fourth ride quotes at the campaign's absence rather than
its discount. `Campaign.budget_minor` caps aggregate spend: `quote()` reserves
the chosen discount against the campaign's budget *before* the quote is
issued (never after, and never by cutting a promised payment at settlement),
keyed by trip intent so a refreshed quote for the same intent releases and
replaces its own prior reservation; `observe()` (below) converts a
reservation to spend on `order_created`/`order_completed` and releases it on
`order_canceled`. A quote whose campaign has no budget room left is priced
without the discount, exactly as if no campaign matched. Multi-ride quests
and stacking-discount variants remain extensions. Contribution is not profit
and excludes operating costs/taxes.

## Cancellation and policy changes

Cancellation policy decides when a rider/driver/platform can request ending an
order, which reason applies, and any fee/payout. Default permits cancellation
before boarding with zero fees. A delay-sensitive rider decides whether to
request it in their behavior policy. The engine enforces a valid physical ending
and clears only the relevant commitment.

`driver_lockout_seconds` (default 0, off) adds a consequence to a *driver-
initiated* cancellation: `observe()` (below) records
`locked_until[driver] = now + driver_lockout_seconds` in the canceling
platform's own memory, and `candidates()` skips a locked driver until that
time strictly passes. The lockout is per platform (each platform's memory is
separate) and reads the canceling driver's id from the platform's own order
record, never from a widened notification payload.

Timed interventions select new policy versions at safe decision points. Existing
quotes, offers, and accepted terms remain committed. A policy update can change
future dispatch or estimates but cannot erase a current order or reorder occupied
service. Store platform controller memory explicitly for resume and replications.
A `policy_change` naming platform `"*"` expands at compile time to one entry
per platform already launched by that intervention's time (each merging
against its *own* base policy, so two platforms can move from different
starting points to a shared new term); an update, wildcard or not, may never
change `guarantee_window_seconds` -- that cadence is fixed once, from the
platform's launch-time policy, in `Simulation.__init__`.

## Platform state and the observe hook

A sixth hook, `observe(context, memory, random) -> Decision(Stop|Transfer, memory, explanation)`,
lets a platform react to its own *already-published* notifications -- the
same audience-scoped stream `PolicyRuntime.on_notification` already reacts to
-- to keep windowed records, lock out a canceling driver, or move campaign
budget between reserved and consumed, all without widening any engine
notification payload. `MarketplacePolicy.observed_kinds()` scans the compiled
config once (base parameters plus every rule's merged parameters) and returns
exactly the kinds this platform's configuration needs:

* any `driver_lockout_seconds > 0` -> `order_canceled`
* any `guarantee_window_seconds > 0` -> `offer_resolved`, `order_completed`,
  `driver_app_opened`, `driver_app_closed`, `driver_availability`, and the
  runtime-generated `window_closed`
* any campaign with `budget_minor is not None` -> `order_created`,
  `order_canceled`, `order_completed`

`PolicyRuntime.__init__` calls this once per platform to build `self.observing`
(and re-derives it after every `policy_change`); `on_notification` calls
`observe()` only when the notification's kind is in that platform's set. For
every shipped preset this set is empty, so `observe()` is never called: no
`policy_decision` rows, no memory writes, and (since `guarantee_window_seconds`
is 0) no `policy.observe_window` scheduler events exist. `observe`'s context
(`now`, `platform_id`, `kind`, `data`, `order`, `offer`, `driver_id`, `visible`)
mirrors `own_orders`/`own_offers`: `order`/`offer` are this platform's own
records, resolved the same way `own_orders`/`own_offers` are; `driver_id` is
the driver the notification is about (from the data, the offer, the order's
assignment, or the window's own subject) or `None`; `visible` is the driver's
resolved-context dict when a driver is named, else the rider's -- so
`config.resolve(context.visible, context.now)` selects the same parameters a
driver-side rule would. `window_closed` is not an engine notification: it is
raised by `PolicyRuntime._observe_window`, fired once per
`guarantee_window_seconds` per platform, once per member driver (a driver
with this platform in both `apps` and `accounts` -- the platform's own
registered base, never every engine driver), in stable id order.

Memory gains five new keys, all absent (and hence absent from every preset's
snapshot) until their owning mechanism is used: `locked_until` (lockouts, per
driver), `windows` (per window-start, per driver: `dispatches`, `accepted`,
`rejected`, `expired`, `online_seconds`, `payout_minor` -- windows are keyed by
their start and popped by `window_closed`, so a same-instant engine event is
always attributed to whichever window it actually falls in, never the one
being closed), `open` (per driver, the app-open accrual cursor for
`online_seconds`), `budgets` (per campaign: `reserved`, `consumed`) and
`reservations` (per trip intent: `campaign_id`, `amount_minor`, `expires_at`,
`order_id`). A campaign budget reservation has no engine expiry event, so it
is swept lazily (release without consuming) at the top of `quote()` and of
`observe()` whenever a budgeted campaign exists.

`programs` (a `PlatformPolicy` field, alongside `rules`/`campaigns`) declares
`hourly_guarantee` programs: `id`, `floor_minor`, `window_seconds` (must equal
the platform's own `guarantee_window_seconds`), `min_acceptance_rate`,
`min_online_seconds`, `zero_dispatch_qualifies`. At each `window_closed`,
`considered = accepted + rejected + expired` (an offer resolved `canceled` or
`acceptance_failed` -- not the driver's own choice -- counts in `dispatches`
but not `considered`); `rate = accepted/considered`, or
`1.0`/`0.0` per `zero_dispatch_qualifies` when `considered == 0`. A driver who
meets `min_online_seconds` and `min_acceptance_rate` and whose own completed
`payout_minor` (excluding bonus) in the window is below `floor_minor` receives
a `Transfer(reason="guarantee_topup", role="driver", program_id=...)` for the
shortfall, applied through the same `apply_transfer` the controller hook uses
(so the platform, never another party, funds it, and party deltas still sum
to zero). Only the first qualifying program (by id) pays a given driver in a
given window.

`service_area` (`None`, off by default) refuses a `quote()` whose request
origin or destination falls outside the platform's declared box, and skips a
`candidates()` row whose driver position falls outside it -- independently: a
platform can quote a ride it will never be able to serve if supply is
elsewhere in the area, and can decline to serve a driver currently outside its
area regardless of the ride's own geography. Authored as a zone id string
(resolved to a box by `_compile_policy` against `world.zones`) or a literal
box; `MarketplacePolicy` raises loudly, never silently, if an unresolved
string ever reaches a decision.

## Validation and acceptance

Test independent platform tariffs and segment overrides; hidden-state-free
candidate lists -- now specifically including a platform with lockouts and a
service area, proving `candidates()`/`dispatch()` still never depend on a
competitor's memory, campaigns, or commitments; back-to-back versus
cross-platform ETA inputs; distinct retry limits; exact expiry boundaries;
immutable accepted bonuses; overlap/tie rules; rounding and negative
contribution; queued cancellation; and rejection of a policy requesting more
than two local accepted commitments.

For the same platform-visible context and controlled randomness, changing only
hidden competitor metadata must not change the policy result. Runtime command
success may differ because the engine enforces physical truth. Record that
difference without passing privileged reasons back into platform policy state.

Windowed records and campaign budgets add their own conservation obligations:
a window's driver record must be attributable to exactly one window (keyed by
start, popped at close, never folded into "the current" record); a budget's
`reserved + consumed` must never exceed `budget_minor`, and nothing may ever
be cut from an already-quoted `rider_payment_minor` to enforce it -- reserve
before the quote, release on expiry or cancellation, consume on completion.
A `guarantee_topup` Transfer must show `transfer_party_delta_minor == 0` (the
funding platform's account absorbs exactly the credited amount) alongside the
existing settlement/order conservation identities.
