# Phase 3: Rebu, Blot, and Flyt on one physical market

Status: design and implementation plan only. Proposed APIs, modules, presets,
and capabilities are not implemented by this documentation change.

The simulator's purpose is to compare experiments. Scenarios should require
little configuration, inherit versioned defaults, and replace models without
rewriting lifecycle mechanics. Establish those boundaries before introducing
three competing marketplaces: **Rebu**, **Blot**, and **Flyt**.

One market owns riders, drivers, cars, and physical locations. Each platform is
a marketplace with independent policies and knowledge. Market share emerges
from participants' decisions and completed rides.

This revision replaces the earlier global one-pending-offer-or-ride reservation.
Competing offers can coexist. A driver can hold two accepted, unfinished orders
market-wide while physically serving only one. Same-platform back-to-back orders
and cross-platform queued orders are requirements of this phase.

## Architecture and document map

| Document | Responsibility |
| --- | --- |
| [Event engine](architecture/event-engine.md) | Generic clock, deterministic scheduling, event records, cancellation, and continuation; no ride-hailing dependencies. |
| [Marketplace engine](architecture/marketplace-engine.md) | Shared entities, physical movement, commitments, legal transitions, scoped observations, and accounting. |
| [Marketplace policy](architecture/marketplace-policy.md) | Per-platform matching, dispatch, pricing, distance/ETA estimates, incentives, expiry, and cancellation, including segment conditions. |
| [Behavior policy](architecture/behavior-policy.md) | Participant decisions, private memory, app use, acceptance, patience, adoption, and learning. |
| [Scenario definition](architecture/scenario-definition.md) | Minimal authoring, versioned presets, typed overrides, interventions, validation, and compilation to an execution plan. |
| [Experiment runner](architecture/experiment-runner.md) | Variants, replications, controlled inputs, reproducibility, measurements, comparison, and performance. |

These are responsibility boundaries, not independent simulation clocks. The
marketplace engine supplies domain handlers to the event engine and validates
policy commands. Platform policies receive local views; participant policies
receive personal views. Compilation prepares an immutable plan, and the runner
creates fresh mutable state per replication. Reporting cannot affect decisions.

The layer documents own detailed contracts; this roadmap owns implementation
order and release scope. Change both when a shared contract changes.

## Engine invariants and configurable assumptions

Every modern scenario must obey these marketplace-engine constraints:

- Every physical ride binds one rider, one driver, and one car. Each entity can
  participate in only one physical ride at a time.
- A driver/car has one actual trajectory, shared by an onboard rider, regardless
  of what individual platforms report about their orders.
- A driver holds at most two accepted, nonterminal orders across all platforms.
  Offers alone do not consume slots; acceptance acquires a slot atomically.
- A queued order cannot physically arrive at pickup until the preceding ride
  ends and its own pickup travel finishes. An expired ETA cannot cause arrival.
- Going offline cannot erase a ride or silently discard accepted commitments.
  Cancellation and shutdown must reach valid physical and contractual endings.
- People own at least one installed app; cars have at least one platform
  registration. Service requires compatible participant and vehicle access.

The two-order ceiling is a fixed model constraint, even though it is not a
physical law. Platforms can impose stricter rules but cannot raise it. World
parameters such as speed are configurable; a single actual trajectory is not.

Nearest-driver matching, five distinct dispatch attempts, ten-second offer
expiry, rider search strategy, second-order willingness, cancellation rules,
app retention, tariffs, and learning models are replaceable policy defaults.
Do not encode these as engine laws.

## Entities, platform knowledge, and commitments

Introduce persistent rider, driver, and car records. Keep installed/enabled
apps, preferred app, open apps, accepted orders, and physical activity separate.
A car's membership means registration; it has no human app preference. Initially
assign one explicit car per driver. Shared-fleet reassignment is a later model.

Support all seven nonempty app subsets independently for riders and drivers,
plus explicit car registrations. Validate preferred-app usability and compatible
driver/car pairs. Support explicit small populations and seeded correlated
segments. Downloads and preference changes are different transitions. If driver
adoption triggers car registration, declare that policy and log both changes.

Each platform knows its own offers, orders, app sessions, visible segments, and
shared location observations. It cannot access competitor destinations, orders,
or the global commitment queue. Match using platform-local eligibility; a
secret global-idle filter would leak competitor activity. Participants can know
their own commitments across apps. The experiment observer may see the whole
market, but that privilege never becomes a policy input.

An accepted Blot order can be labeled "driving to pickup" by Blot while its
driver still carries Rebu's rider. Store Blot's order state, the market's physical
activity, and the driver's service sequence separately. Multiple offers do not
reserve a driver globally. Competing acceptances for one order choose one driver;
competing acceptances for the driver's last slot admit one winner atomically.

## Movement, estimates, and back-to-back service

Platforms calculate distance, propose routes, and estimate ETA independently.
The market executes one actual route and supplies position at simulated time
`t`. Start with straight-line movement at world speed, interpolated between
movement boundaries without per-second population polling.

ETA never schedules physical arrival. On completion of a preceding ride, begin
queued pickup travel from its actual drop-off position. Validate route origins
and world constraints. Initial service order follows acceptance order; a queued
cancellation can remove a commitment without interrupting the occupied ride.

Use this deterministic fixture with otherwise exact travel estimates:

| Quantity | Minutes |
| --- | --- |
| Remaining Rebu ride | 6 |
| Current position to Blot pickup | 3 |
| Rebu destination to Blot pickup | 4 |
| Blot's initial ETA without Rebu knowledge | 3 |
| Actual pickup wait after Blot acceptance | 10 |
| Informed same-platform back-to-back estimate for identical geometry | 10 |

The seven-minute error must emerge from missing information, without an
artificial cross-platform penalty. Same-platform knowledge permits a correct
estimate under an exact estimator; other estimation models can still err.
Reject a third commitment, including another Rebu order while Rebu and Blot
already occupy the two slots.

## Behavior, economics, and evolution

Riders normally open their preferred usable app. Personal price/ETA tolerances,
sensitivity, search friction, and patience govern further app searches. Bound
app openings, quote refreshes, and orders separately. Keep one trip intent across
failed attempts and count conversion once. Reuse trip taste/outside-option draws
so repeated searches do not manufacture conversion. A permitted pre-boarding
cancellation can allow a retry after the prior order closes; an onboard rider
cannot switch into another simultaneous ride.

Drivers choose apps and offers using private workload, preference, payout, and
waiting experience. Track physical idle time, no-offer time, and commitment wait
separately. A queued order may eliminate post-drop-off idle waiting. Busy time
can provide another app's offers, so the previous blanket exclusion of busy
time from offer opportunity is removed. Separate idle and busy-with-a-free-slot
exposure and retain censored waiting observations.

Platforms own independent versioned tariffs, commissions, discounts, and driver
bonuses. Freeze quoted rider terms and accepted driver terms. The default
completed-ride accounting is:

```text
gross fare G; rider discount D; commission fraction c; driver bonus B
rider payment = G - D
driver payout = (1 - c) * G + B
platform contribution = c * G - D - B
```

Contribution excludes operating costs and taxes. Default cancellation charges
no fee and earns no completion bonus. Other supported cancellation policies
must produce separate explicit settlements, not invented completed rides.
Campaign windows are half-open, and committed terms survive policy changes.
The policy and engine documents define rounding and settlement contracts.

Initially hold total people, exogenous trip intents, and shifts fixed. Preserve
ownership and personal memory across weeks. Support scheduled launch, marketing,
policy/cohort changes, and endogenous adoption and learning. Zero rates freeze
those mechanisms independently. Use observed experience, priors for unused apps,
bounded learning, and preference hysteresis. Updates never interrupt occupied
rides, erase queued commitments, or rewrite monetary terms.

## Scenario compiler and experiment controls

New Python scenario builders produce typed definitions rather than mutating a
live simulation. A named, versioned preset supplies complete synthetic defaults.
Scenarios specify differences. Export resolved values and override provenance.
Unknown keys, unused parameters, and incompatible policies fail before execution.

Compile definitions into prepared plans: resolve policy versions and references,
check interfaces and observation permissions, normalize units, bind functions,
and prepare lookups. Instantiate reproducible populations and external inputs
per replication. Dynamic legality still needs runtime checks. Do not precompute
market-dependent futures or promise native speedups for arbitrary Python hooks.

Compare a baseline and explicit variants using a seed set and shared initial
people/schedules where those inputs are controlled. Use stable intent/person
and decision-purpose random identities, not global order counters or Python's
process-randomized hash. Record model, preset, policy, compiler, metric, and
environment versions. Define warm-up, measurement, drain, and unfinished-outcome
treatment before comparing results.

## Metrics and reports

Replace single-decision-per-session assumptions before rider retries. Emit
exactly-once trip conversion and terminal outcomes alongside repeated quotes.
Preserve offer-created/resolved cohorts and distinguish failed acceptance from
rejection, expiry, and cancellation.

Primary market share is platform completions divided by all completions in the
selected window. Also report absolute completions, unserved demand, first-app
and order shares, downloads, preferences, and ownership. Undefined denominators
produce gaps. Installed-app penetrations can sum above 100%.

Count physical driver/car service only for the order actually receiving pickup
travel, boarding, or passenger transport. A Blot queue overlapping Rebu service
is not Blot active time. Report commitment wait, platform-reported en-route time,
and original/revised/actual ETA separately. Platform offer opportunity intervals
can overlap, including service elsewhere, and cannot be summed as physical hours.

Use consistent platform/time filters, cohort definitions, and weighted ratios.
Reconstruct ownership at arbitrary window starts from snapshots and changes.
Keep Python and dashboard metrics in parity. Reports do not consume behavioral
randomness or supply hidden feedback to policies.

## Implementation sequence

| Milestone | Deliverable and acceptance gate |
| --- | --- |
| 0. Refactor and characterize | Separate scheduling, domain transitions, policies, configuration, and run/report orchestration. Preserve legacy single-platform decisions, timings, and metrics. |
| 1. Definition and experiment foundation | Versioned presets, resolver/compiler, immutable plans, fresh run state, and paired variants. Reproduce a saved small experiment with all defaults explicit. |
| 2. Shared market and platform views | Cars, trajectories, access, and scoped observations. Same location at the same time; no competitor order visibility. |
| 3. Offers and queued commitments | Competing offers, atomic two-order capacity, same/cross-platform queues, independent estimates, cancellation, and deferred offline. Pass the ETA fixture and third-order race tests. |
| 4. Economics and rider funnel | Independent tariffs, incentives, committed terms, settlements, bounded app search, and exactly-once conversion. Reconcile money and trip outcomes. |
| 5. Participation and evolution | No-offer timers, app retention, second-order willingness, exposure-aware learning, adoption, and interventions. Preserve commitments during updates. |
| 6. Comparison and scale | Platform/time reports, replicate uncertainty, continuation, and small/weekly/multiweek measurements. Pass all cross-layer gates below. |

Extract `_schedule`/`advance_to` into generic scheduling and retain `run()` as a
compatibility facade over runner orchestration. Split `_dispatch_order` into
local policy decisions and engine commands. Replace driver `pending_order` and
`current_order` with separate offer references, commitments, and physical state.
Move choice/pricing behind their contracts; do not retain all domain transitions
in `main.py`. Module names are implementation choices, not a required package API.

The legacy adapter uses Rebu, one car per driver, existing price arithmetic and
random draw order, and local rules that offer only to idle drivers. It does not
exercise second commitments. Preserve timeout-at-deadline behavior and current
numeric results; do not add rounding or extra draws to that path. Reject mixed
legacy arguments and modern policy overrides. New experiments select the modern
model explicitly.

## Cross-layer acceptance gates

- No rider, driver, or car is physically occupied twice, and one car cannot have
  two active controlling drivers.
- All app subsets, car registration, preference validity, launches, and downloads
  obey access rules without inventing platform users.
- Competitor state cannot leak through candidate filtering, ETA inputs, capacity
  counters, or rejection reasons.
- Offers coexist; an order assigns one driver; a driver holds at most two
  accepted orders; same-time races and all third-order combinations are tested.
- Queued pickup cannot occur early, even if the preceding route passes that
  pickup. Queue cancellation leaves current service unchanged; promotion and
  capacity release happen exactly once.
- Offline requests, stale events, expiry, and policy changes cannot revive orders
  or remove occupied entities.
- Rider retries preserve intent identity and conversion; repeated searches do
  not grant independent extra conversion chances.
- Discounts/bonuses affect the correct party; terms survive campaign end;
  cancellation settlements remain distinct from completion rewards.
- Learning distinguishes idle/busy opportunity, handles censored waits, respects
  actor knowledge, and freezes with zero rates.
- Continuous and resumed runs agree, including positions, queues, memory,
  randomness, future events, and monetary commitments.
- Orders created = completed + canceled + active; each intent completes at most
  once; physical online time = idle + service; platform completions sum to market
  completions; rider payments = driver payouts + platform contribution for every
  completed-ride or explicit cancellation settlement.

Run existing Python/JavaScript suites and focused new contract tests during
implementation. The 94,773-ride/72.12%-utilization legacy calibration is a
reference, not a target imposed on three competing platforms. Profile candidate
scans, event counts, logs, reports, runtime, and peak memory before optimizing;
indexes must preserve platform knowledge boundaries.

The first complete release includes the behavior above. Road-network routing,
automatic pricing optimization, campaign budgets/quests, app removal, shared
fleets, pooling, and endogenous total demand/shift generation remain extensions.
Define interfaces for such models without claiming their implementations exist.
