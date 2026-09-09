# Behavior policy

Status: implemented initial behavior layer. Part of the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

Behavior policies decide what riders and drivers do. They model personal
preferences, sensitivities, attention, learning, and willingness to accept a
second order. [Marketplace policies](marketplace-policy.md) determine what each
platform offers; the [marketplace engine](marketplace-engine.md) enforces which
requested actions are physically and contractually valid.

## Shipped implementation and population API

This file is the single behavior specification, including the initial model
choices, configuration, memory, and limitations. Executable models live in
`behavior_policy.py`: `RiderPolicy`, `DriverPolicy`, and `EvolutionPolicy`.
`behavior.py` is removed. `policy_runtime.py` translates their proposals into
engine commands and owns notification delivery, explicit state and guarded
scheduling. `scenario.py` realizes the population and exogenous sessions from
a compiled definition, and `main.py` only executes one prepared run. There is
no legacy probability model or scenario compatibility mode.

Populations are declared in the scenario: behavior defaults for each trait
family, correlated segments with weights and trait overrides, and explicit
people for deterministic fixtures. Personal values resolve as behavior defaults,
then the segment, then the explicit person. Segment traits may be distributions
sampled once per person with a seed derived from the person's stable identity,
so a trait is never resampled on a later trip. Realized `PersonProfile` values
are saved as input artifacts and are immutable. Learned scores and preferences
live in per-person runtime state; trip memories and shift/search memories are
separate. Segment `initial_scores` seed the learned scores.

```python
from scenario import Scenario, person, segment

scenario = (Scenario(preset="three-platform-week@1")
    .with_changes({
        "behavior.rider.parameters.eta_tolerance_seconds": 300,
        "behavior.evolution.parameters.learning_rate": .1,
        "population.riders.segments.rebu-first.rider.patience_seconds": {"uniform": [180, 360]},
        "evolution.checkpoint_hours": 24,
    })
    .add("population.drivers.people", person(
        "d-commuter", apps=("rebu", "blot"), preferred_app="rebu", registrations=("rebu", "blot"),
        awareness=("rebu", "blot", "flyt"), disclosed_to=("rebu",),
        driver={"no_offer_seconds": 60, "second_order_probability": .8},
        evolution={"adoption_rate_per_day": .02, "onboard_car": True})))
```

The compiler rejects an unusable preferred app, a driver whose car is not
registered for it, membership of an unlaunched platform, unknown platform
references, weights that do not sum to one and out-of-range trait values.
Access supports every nonempty subset of the configured apps independently by
role. `preferred_app` must be in installed apps and usable accounts; drivers also
require its vehicle registration. `accounts` defaults to installed apps and
`registrations` defaults to accounts. The published presets mix segments that
install all three apps with different first choices and a Rebu-only segment.
Only launched usable apps participate. `awareness` explicitly identifies apps a
person can discover before installation. A profile's latent `segment` is exposed
only to platforms in `disclosed_to`.

Engine installation, account activation, app opening and car registration are
separate commands. At adoption, the initial onboarding flow records a download
and account activation separately, optionally registers the assigned vehicle
when `onboard_car=True`, and leaves preference and open apps unchanged. Without
vehicle onboarding, a driver can download an app but cannot use it for offers.

### Initial rider model

`RiderTraits` defines price/ETA tolerance and sensitivity, loyalty, search cost,
purchase bias, outside utility and taste scale. The route reference is
`reference_base_minor + reference_per_km_minor * straight_line_km`, independent
of the platform's tariff and discount. The inspection probability is the
exponential response below using price excess above `acceptable_price_ratio`
and ETA excess above `eta_tolerance_seconds`. Missing supply forces inspection
when an unseen app remains. A fixed inspection draw is reused within an intent.

The purchase utility is:

```text
purchase_bias - price_sensitivity * net_price / route_reference
- eta_sensitivity * pickup_eta / eta_tolerance
+ loyalty * is_preferred + learned_score
- search_cost * app_visit_index + taste_scale * logit(platform_taste_draw)
```

Compare it with `outside_utility + taste_scale * logit(outside_draw)`. Both draw
identities persist for the intent. Ties choose stable platform IDs and the most
recent quote within that platform. Quotes are compared only while valid; a
repeat of an identical quote cannot grant a new independent conversion draw.
The next unseen app ranks personal preference, learned score and known announced
incentives, never a hidden quote or supply count.

Defaults are `decision_seconds=5`, `opening_seconds=2`, `retry_seconds=2`,
`patience_seconds=300`, `cancellation_after_seconds=600`, `max_app_visits=3`,
`max_quote_refreshes=1`, `max_order_attempts=3`, and `attempts_per_app=1`. Search
patience has its own deadline event; it stops search but does not erase a live
order. Order progress can request cancellation before boarding. The rider retries
only after the engine publishes actual cancellation. A canceled attempt remains
under the same trip intent, whose conversion timestamp is assigned just once.
For deterministic purchase tests use `taste_scale=0` and a sufficiently high or
low `purchase_bias`, with explicit supply and delay assumptions.

### Initial driver model and clocks

Drivers open their preferred usable app at shift start. `DriverTraits` selects
`expansion="multi_app"` or `"exclusive_switch"`, `after_service="retain"` or
`"preferred"`, and `expand_while_busy` (default false). Expansion tracks visited
channels so exclusive switching cannot cycle indefinitely. The default no-offer
threshold is 60 seconds and further opening delay is 30 seconds. Every offer
receipt resets no-offer time, even when the offer is rejected; app opening does
not. Phase, acceptance, offer, app and shift generations suppress stale events.
Apps required by accepted commitments remain open under policy-driven switches.

Responses execute after `response_seconds=3` and recheck private capacity. The
acceptance score combines `acceptance_bias`, normalized payout, normalized private
pickup delay, loyalty and the learned score. Its logistic probability is
multiplied by `second_order_probability` for a second commitment. No slot, shift
exit, or exceeding `max_private_pickup_seconds` forces rejection. The displayed
ETA and private estimate are stored separately on the proposal; the private
estimate includes the driver's own preceding service across all apps. The actual
engine response is stored in response memory. A driver never treats a requested
acceptance as a confirmed assignment.

`cancel_after_seconds` bounds commitment patience before boarding.
`shift_exit="drain"` stops acceptance and finishes accepted orders;
`"cancel_queued"` additionally requests cancellation of queued orders. The
engine remains responsible for service promotion, occupancy and final shift exit.

This is `driver_participation@1`'s behavior; every statement in this paragraph
is conditional on that implementation. Two or more platforms' offers to the
same multihoming driver are never compared jointly: each `offer_received`
independently schedules its own `policy.driver.respond` after
`response_seconds`, and each is evaluated on its own terms whenever its
event fires, in the scheduler's own (FIFO) processing order. There is no
cross-platform tie-break and no lookahead, so whichever offer happens to
reach its response instant first is answered first, regardless of payout,
even when a driver would clearly prefer the other on reflection
(`scenarios/reviews/s01_driver_supply_elasticity.py`'s check 2 demonstrates
and documents this -- and names `driver_participation@2`'s
`response_rule='best_pending'`, below, as what compares them).
"Accepting" (`_driver_accepting`, used to build every platform's candidate
list) means on shift, no exit requested, the app open and not paused, and
the assigned car registered and the platform launched -- not physically
idle: a driver mid-ride is still `accepting` on every app with a free
commitment slot. There is no repositioning (a driver never travels without
an active or queued commitment) and, at `@1`, no memory of past rejections,
cancellations, or failed pickups carried into future decisions;
`pickup_eta_revised` notifications are emitted (`revise_pickup_eta`,
[marketplace-engine.md](marketplace-engine.md)) but `rider_search@1` does
not consume them, so a rider's cancellation decision never reacts to a
revised (only the original) ETA. "Participant policies v2" below names the
`@2` traits that change each of these: `response_rule`/`hold_for_better`
compare pending offers and hold out for a better one; `availability` adds
`accepting=False` while paused; `cancel_rule='private_eta'` and
`eta_drift_cancel_seconds` add exactly the private-ETA and revised-ETA
memory this paragraph says `@1` lacks.

Personal diagnostics retain phase-specific `opportunity_exposure`,
`personal_offer`, `opportunity_wait_censored`, `commitment_wait`,
`back_to_back_ready`, and `post_dropoff_offer_wait`. Only idle or busy-with-one-slot
opportunities accrue exposure. Full-capacity offers are recorded separately.
An unqueued completion begins a post-drop-off search; a queued promotion records
readiness without creating an idle interval. Physical idle time is derived from
engine shift and physical service spans, separately from these search clocks.

### Initial evolution model and checkpoints

`EvolutionTraits` defaults both `adoption_rate_per_day` and `learning_rate` to
zero. `adoption_friction` attenuates the rate exponentially; dissatisfaction with
the preferred platform increases it. Only known, launched, missing apps enter the
combined hazard. At most one target is drawn per explicitly scheduled checkpoint.
This is a coarse-time approximation at high rates; use finer checkpoints when
multiple downloads per period would matter.

Rider rewards combine route-normalized net price, pickup wait and ETA error.
Driver rewards combine actual payout and physical service duration; overlapping
queued commitment waits never count as additional working hours. Cancellations
produce negative observations. Rewards and learned scores are bounded to
`[-2, 2]` and smoothed at the configured learning rate. There is no second reward
for discounts or bonuses already represented in money experienced.

For drivers, offer rates use `prior_offer_rate_per_second` and
`prior_exposure_seconds`, separately by platform and idle/busy phase. The inverse
is the estimated phase wait. A time-weighted bounded wait penalty combines with
service reward scores. Platforms without exposure retain their priors. Raw
observations remain exported so this estimator can be replaced. Learning rate
zero leaves score values and preferences unchanged, even with new observations.

Preference changes require `preference_margin` and
`preference_cooldown_seconds`. A scenario `preference_change` intervention
targeting a segment or an explicit people list sets preferences independently
of learning; the compiler checks static access and that the app is launched
by then. Checkpoints come from the scenario's `evolution.checkpoint_hours`.
At a checkpoint, all due interventions are applied first, all decisions use the
same population snapshot, and only then are adoption and learning results
applied. Exogenous trip/shift schedules and population counts never change.

The built-in declarations specify parameter types, permitted observations,
typed proposals, hooks and JSON memory fields/version. `Simulation.snapshot()`
and `Simulation.restore()` preserve profiles, preferences, private memories,
exposure, pending decisions, interventions and random identities. A general
custom-policy registry and scenario compiler remain separate roadmap work;
initial restore binds the shipped implementations. Validate the behavior contracts
with scenario runs and throwaway scripts; there is no unit test suite.

### Participant policies v2

`driver_participation@2`, `rider_search@2` and `personal_evolution@2`
(AST-209, plan section 4.D/4.E) are additive registered implementations,
each a Python subclass of its `@1` counterpart with a trait dataclass that
subclasses the `@1` traits (`register_policy`'s `issubclass` check requires
this). Every new trait defaults to today's `@1` value or behavior, so a `@2`
selection with default traits reproduces `@1` byte-for-byte, including the
random draw order -- shipped presets keep selecting `@1` through
`BUILTIN_IMPLEMENTATIONS`, so nothing changes without an explicit selection.

**`DriverTraitsV2`** (subclasses `DriverTraits`):

| Trait | Default (= `@1`) | Meaning |
| --- | --- | --- |
| `open_apps_at_start` | `'preferred'` | `'all'` opens every usable app at shift start instead of just the preferred one |
| `response_rule` | `'independent'` | `'best_pending'` compares this offer's value against every other currently-pending offer to the same driver before the `@1` logistic result stands |
| `response_objective` | `'total_payout'` | `'payout_per_minute'` divides by `private_eta_seconds` plus the destination leg at `speed_kmh` |
| `hold_for_better` | `False` | while idle with a free slot, decline an offer below a reservation value |
| `reservation_payout_minor` | `0` | explicit reservation, always an absolute total payout in minor units regardless of `response_objective` (below); `0` selects the learned-wait form (below) |
| `hold_max_wait_seconds` | `0` | learned-wait form only: hold only if some other open platform's learned idle wait is at most this |
| `cancel_rule` | `'patience'` | `'private_eta'` adds an ETA/penalty-exposure cancel ahead of the inherited elapsed-patience branch |
| `cancel_eta_threshold_seconds` | `3600` | private-ETA form: cancel only once the private pickup ETA exceeds this |
| `cancel_check_seconds` | `0` | `0` = off; else the runtime's re-check cadence for the private-ETA rule |
| `penalty_tolerance_minor` | `0` | private-ETA form: cancel only when declared exposure is at or below this |
| `availability` | `'always_open'` | `'pause_when_full'` / `'pause_while_serving_other'` (below) |
| `tie_break` | `'stable'` | tie-break for `response_rule='best_pending'`: `'stable'` (smallest `(platform_id, offer_id)`) or `'random'` |

**`respond`** layers three stages on the unchanged `@1` logistic (same
score, same `RandomValues(('response', driver_id, platform_id, offer_id))`
draw, drawn exactly once at the end), so a default-trait `@2` driver decides
byte-for-byte like `@1`. Each stage may force `probability=0`
deterministically -- the `Respond` proposal's `probability` field, not a
parsed explanation string, is what a fixture checks:

1. The `@1` score, second-order scaling and hard zeros (no free slot, exit
   requested, private ETA past `max_private_pickup_seconds`).
2. `response_rule='best_pending'`: build `value(o)` (per `response_objective`)
   over `context.pending_offers` (an id-sorted, enriched view of this
   driver's own unresolved offers, each with a runtime-computed
   `private_eta_seconds` -- sorted because the underlying engine set's
   iteration order can change across a snapshot/restore cycle, and a policy
   comparing offers must see the same order either way). Reject when this
   offer's value is below the best pending value; when several tie exactly,
   `tie_break` picks the winner from the tied offers sorted by
   `(platform_id, offer_id)` -- `'stable'` takes the first, `'random'` takes
   `tied[int(context.tie_draw * len(tied))]`. `tie_draw` is a *separate*,
   runtime-supplied symmetric draw keyed to `('tie_break', 'driver',
   driver_id)` over the sorted pending offer ids -- deliberately not derived
   from the per-offer acceptance draw above, whose identity contains the
   offer id and therefore cannot agree between two co-pending offers, and
   must never change (moving it would move every `@1` acceptance draw and
   fail the seed-0 gate).
3. `hold_for_better`, only while idle with a free slot and no exit
   requested: reject when this offer's *total payout* (`offer_value(...,
   'total_payout', ...)`, always the absolute payout -- deliberately not
   `response_objective`, which governs only how stage 2 ranks pending
   offers against each other, not what a reservation means) is below the
   reservation.

**`progress`** adds, before the inherited `@1` elapsed-patience branch: when
`cancel_rule='private_eta'` and boarding has not happened, value a lockout
at `lockout_seconds * reference_payout_minor / 600` (the same
money-per-second scale `driver_reward` already uses), add the platform's
declared `cancellation_penalty_minor`, and cancel when the private pickup
ETA exceeds `cancel_eta_threshold_seconds` *and* that total exposure is at
or below `penalty_tolerance_minor`. `context.terms` (`{platform_id,
cancellation_penalty_minor, lockout_seconds, guarantee}`) is the offer's (or
order's) platform's terms *resolved against this driver's own `visible`
dict* -- exactly what a real app would show this driver on this platform,
never another person's terms or a competitor's.

`open_apps_at_start` and `availability` are applied by the *runtime*, not
proposed by the policy -- the same precedent `after_service='preferred'`
already set. `open_apps_at_start='all'` is applied once, in the
`shift_started` branch of `on_notification`. `availability` is applied by
`PolicyRuntime.apply_availability`, called unconditionally as the last
statement of `sync_driver` (one call site, so no branch can forget it),
using the new engine commands `pause_app`/`resume_app`
([marketplace-engine.md](marketplace-engine.md)): the app stays open (so it
still counts toward commitments and the "open apps" observed elsewhere) but
`accepting=False`, that platform's pending offers to the driver are
canceled, and the existing `driver_availability` notification is published
-- which `MarketplacePolicy.observe` already maps to guarantee-window online
time, so a paused driver correctly stops accruing that platform's online
seconds (plan section 5's S9 tension). `pause_while_serving_other` pauses
every open app except the one(s) the driver currently has an accepted
commitment on; `pause_when_full` pauses every open app once commitments
reach `MAX_COMMITMENTS`. `apply_availability` never pauses an app the driver
has not opened, and a re-entrancy guard (`_pausing`, transient, deliberately
not snapshotted) stops the `driver_availability` notification it publishes
from recursing back into itself through `sync_driver`. This is the
participant-side answer to the competitor-occupancy boundary (plan section
5): a platform still never learns another platform's commitment count or
service; a driver can only *disclose* busyness through its own pause.

**`RiderTraitsV2`** (subclasses `RiderTraits`):

| Trait | Default (= `@1`) | Meaning |
| --- | --- | --- |
| `choice_rule` | `'utility'` | `'lexicographic'` (below) |
| `choice_keys` | `(('price', 50), ('eta', 0))` | ordered `(key, tolerance)` pairs for the lexicographic rule |
| `compare_all_apps` | `False` | visit every usable app before deciding, within the existing visit/opening budgets |
| `tie_break` | `'stable'` | lexicographic survivor tie-break |
| `failure_wait_seconds` | `720` | a completion whose pickup wait exceeds this counts as a failure (below) |
| `fatigue_threshold` | `0` | `0` = off; else consecutive same-platform failures that trigger a preferred-app switch |
| `fatigue_target` | `'best_other'` | the ranked-best other usable app, or an explicit platform id |
| `sticky` | `False` | recorded on a fatigue switch; personal_evolution@2 honors it (below) |
| `eta_drift_cancel_seconds` | `0` | `0` = off; else cancel when the predicted pickup arrival instant drifts past the offer's promise by more than this |
| `install_trigger_eta_seconds` | `0` | `0` = off; else `decide` may download a known app when the current quote's ETA exceeds this |

`decide` inserts four gated blocks into the unchanged `@1` body, in this
order, so that with default traits the memory-seeding and random-draw order
(`visited`, `attempts`, `refreshes`, `outside_draw`, `taste`,
`inspection_draw`) is untouched and a default-trait `@2` rider consumes the
same draws in the same order as `@1`:

1. **Fatigue switch**, right after the patience/order-budget guards, before
   any app is opened: if the *current* preferred app's failure count (below)
   is at or above `fatigue_threshold`, propose switching to `fatigue_target`
   (falling back to the ranked-best other usable app for `'best_other'`)
   *only if* that target's own failure count is strictly lower than the
   current preferred app's. `failures` is never reset by a switch itself
   (only by an on-time completion on that platform), so two apps sitting at
   the same failure count -- or a target that is itself at or past
   `fatigue_threshold` and no better than the app just left -- must never be
   proposed, or repeated decisions on an unresolved intent (no new failure
   arrives between them) would swap `preferred_app` back and forth forever.
   Requiring strict improvement instead makes any run of consecutive
   switches a strictly decreasing sequence of failure counts bounded below
   by zero, which must terminate. If a named `fatigue_target` platform is
   not currently usable, this is not a compile-time-checkable condition
   (traits do not know the platform table); the policy simply does not
   switch, and no `SwitchPreferred` is proposed.
2. **`compare_all_apps`**: `inspect` also becomes true whenever any usable
   app remains unseen, independent of price/ETA dissatisfaction.
3. **`install_trigger_eta_seconds`**: once the latest quote's ETA exceeds
   the threshold, download the best-ranked known, launched, not-yet-*installed*
   app (`Download`, applied by the runtime like a checkpoint's own download)
   and re-queue -- a mid-intent install changes `usable_apps`, so the policy
   never acts on the new app within the same decision, only on a later one.
   The candidate filter is against `context.installed_apps` (the rider's own
   `apps`), deliberately *not* `usable_apps`: `usable_apps` also excludes an
   installed app the rider has no *account* on yet, and `accounts` may be an
   authored strict subset of `apps` (`scenario.person`/`segment` accept it),
   so filtering on `usable_apps` would offer an already-installed app as a
   "candidate" and the runtime's Download branch rejects exactly that as an
   already-installed app. A per-intent memory list caps this at one download
   per target per intent.
4. **`choice_rule='lexicographic'`**: replaces only the *selection* of
   `best` among viable quotes -- narrow to survivors within `tolerance` of
   the tightest value at each `choice_keys` entry in order (price first by
   default), then pick by `tie_break`. The outside-option purchase gate
   (`utility(best) >= outside`) is unchanged, so a lexicographic rider can
   still decline; only the ranking among viable quotes changes.

`progress` adds, before the inherited `@1` elapsed-patience branch: when
`eta_drift_cancel_seconds` is set and boarding has not happened, compare the
*predicted arrival instant* (`at + eta_seconds`) of the order's first
`source='offer'` prediction (the original promise) against its last
`source='revision'` prediction (the latest re-estimate); cancel with reason
`'pickup eta drift'` once the difference exceeds the threshold. Comparing
predicted arrival *instants*, not raw ETA values, is deliberate: pure
elapsed time must never look like an improving estimate. The runtime
re-queues `policy.rider.progress` (a zero-delay schedule, so the decision
stays at an event boundary) on every `pickup_eta_revised` notification when
this trait is set, and separately schedules a bounded periodic
`policy.revise` for an assigned, not-yet-arrived order when the owning
platform's `revise_interval_seconds` (marketplace-policy.md) is positive --
without it, nothing would ever *produce* a revision for the drift check to
react to, since a platform only revises today on its own
assignment/completion events.

**Outcome memory** (runtime-owned, in `PolicyRuntime.people`, gated on
`fatigue_threshold > 0` so an `@1` rider's state dict never gains the key):
`failures[platform_id]` increments on an `order_canceled` with `by != 'rider'`
(a rider's own cancellation is not the platform failing) and on an
`order_completed` whose pickup wait (`timeline['arrived'] - created_at`)
exceeds `failure_wait_seconds`; a completion within that bound resets it to
`0`. Each increment also appends a `service_failure` observation
(`{cause: 'canceled'|'long_wait', consecutive}`) so a fixture can assert on
the counter without reading private memory. `rider_context` exposes it as
`failures`, alongside `known_launched_apps` (aware, launched apps, whether
or not usable), `installed_apps` (this rider's own `apps` -- the
install-trigger candidate filter above, independent of `fatigue_threshold`)
and `sticky_preference`.

**`Download`** (rider `decide`) and **`SwitchPreferred`** (rider `decide`,
`platform_id` + `sticky`) are applied by the runtime in `_rider_decide`
exactly like the existing typed actions: `Download` installs the app,
activates its account, and re-queues; `SwitchPreferred` sets
`preferred_app` and `sticky_preference` in the rider's own state, records a
`preference_switched` observation, and re-queues. A `preference_change`
intervention (marketplace-policy.md's cousin, scenario-definition.md) also
clears `sticky_preference` back to `False` when it applies a new
`preferred_app` -- stickiness blocks only *learning-driven* reversion, never
an operator's explicit preference change, and a further fatigue switch
still fires normally once the *new* preferred platform accumulates its own
failures.

**`EvolutionTraitsV2`** (subclasses `EvolutionTraits`):

| Trait | Default (= `@1`) | Meaning |
| --- | --- | --- |
| `install_trigger_peer_share` | `0` | `0` = off; else the neighbor-installed-share threshold for the peer cascade (below) |
| `vicinity_km` | `2` | neighbor radius for the peer cascade and `neighbor_install_share` |
| `adoption_phase` | `'any'` | `'idle'` restricts the checkpoint's own hazard download to idle-phase drivers |

`personal_evolution@2.checkpoint` calls the inherited `@1` body unchanged
(identical random draw order -- the hazard draw is always consumed, even
when its result is about to be discarded) and applies three overrides to
the returned `Evolve`, reusing the parent's memory as-is:

1. **Sticky preference**: if `context.sticky_preference` is set, the
   returned `preferred_app` is forced back to `context.preferred_app` --
   learning-driven switching is suppressed. Fatigue switching is unaffected
   because it runs entirely in the rider policy's `decide`, not here: the
   two mechanisms own two different halves of "who changes
   `preferred_app` and when" (the rider switches on fatigue; evolution is
   the one that can be blocked from switching back).
2. **`adoption_phase='idle'`**: if the person's current phase (drivers only;
   riders are always `'off'` for this purpose) is not `'idle'`, the
   checkpoint's own download is discarded (forced to `None`) -- after the
   hazard draw already ran, so the random stream does not shift with phase.
3. **Peer cascade**: independent of the checkpoint's own hazard and
   deterministic (no draw): if `install_trigger_peer_share > 0` and no
   download is already selected, install the lowest-id known, launched,
   not-yet-installed app whose `neighbor_install_share` is at or above the
   threshold.

`neighbor_install_share` (`PolicyRuntime.neighbor_install_shares`, computed
once per checkpoint per role, and only when some person's
`install_trigger_peer_share` is actually positive -- otherwise an empty
dict, no O(n^2) work) is participant knowledge, computed entirely by the
runtime from participant positions and installed apps; no platform ever
receives it (plan section 5.3). A "neighbor" is another person of the *same
role* with a known position (`position_of`, falling back to `Rider.location`
for a rider not currently onboard) within `vicinity_km`, excluding the
person themself; the share for an app is the fraction of those neighbors
who have it installed, `0` (an empty dict, read as `0` for every app) when
there are no positioned neighbors.

`scenario.py`'s population segments/explicit people now resolve each
family's trait schema from the *selected* implementation's declaration
(`trait_schema`, scenario-definition.md), not from a fixed `@1` table, so
every `@2` trait above is authorable exactly like an `@1` one -- through
`behavior.<family>.parameters`, a population segment, or an explicit
person.

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
