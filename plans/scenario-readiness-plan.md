# Scenario readiness: consolidated review findings and implementation plan

Status: proposal, not implemented. It consolidates the ten independent reviews
under `scenario-reviews/*/ANALYSIS-*.md` (all written against revision
`28e839e`; the code they cite is unchanged at `main` `aeddf3e`), checks their
claims against the current modules, compares the pre-PR-4 implementation
(`27d7f3c`), and proposes an ordered plan. Nothing here changes a shipped
contract until its phase is implemented and the owning architecture document
is updated with it.

## 1. Summary

- All ten reviews reach the same shape of conclusion. The market skeleton is
  configurable today: any number of named platforms, per-platform tariffs and
  take-rates, windowed campaigns, populations and segments, horizons, all app
  subsets, launch timing, timed `policy_change` and `preference_change`, frozen
  quote and offer terms, the two-order cap, and a raw log that retains every
  record. The scenario-specific mechanisms are not: nothing in the shipped
  code keeps money outside a completed ride, remembers platform-side driver
  state, prices by zone or time inside one policy version, compares offers
  across apps, keeps rider failure memory, moves a car without an order, or
  removes a participant mid-run.
- The gaps are not ten unrelated feature lists. They cluster into eight
  capability areas (section 4) that recur across scenarios. Building each area
  once, with a small typed surface, serves all ten and the phase 4 experiments.
- The pre-PR-4 implementation is not more suitable (section 3). Every blocking
  gap is in `marketplace_engine.py`, `marketplace_policy.py`,
  `behavior_policy.py` or `policy_runtime.py`, which PR 4 did not change in the
  relevant places. Reverting would lose provenance, validation, paired inputs
  and the policy registry that scenarios 4, 7, 8 and 10 rely on. What the old
  code did better, arbitrary Python for activity and per-person profiles,
  should come back as registered generators inside the compiled plan.
- Recommended order: observability and authoring (cheap, unblocks writing all
  ten scenario scripts with their checks), then the ledger, then platform-side
  state and pricing structures, then participant policies v2, then physical
  engine extensions. Sections 6 and 7 give the phases and the per-scenario
  readiness after each.

## 2. What the reviews found

### 2.1 Requirement coverage by scenario

Legend: **yes** configurable now; **approx** an approximation exists and the
review names what it loses; **no** requires code; blank means not required.

| Requirement | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 | S9 | S10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Platforms, tariffs, take-rates, horizon, counts | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Multihoming access and preferred app | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Timed commission/tariff change, launch | yes | yes | | yes | | | | yes | | |
| Frozen contracts across a policy change | | | | | | | | yes | | |
| Platform-funded rider discount, negative contribution | | | | yes | | | | | | yes |
| Zone or time-boxed surge inside one policy | | no | | | | | | | no | |
| Rider surcharge (flat fee) with driver pass-through | | approx | | | | | | | | |
| First-N-rides voucher | | | | no | | | | | | |
| Driver penalty on driver cancel; running driver balance | | | no | | | | | | | no |
| Platform-scoped timed dispatch lockout | | | no | | | | | | | |
| Hourly guarantee with acceptance-rate condition; top-up ledger line | | | | | | | | | no | |
| Regulatory cap as a market-wide constraint | | | | | | | | approx | | |
| Platform cash, insolvency shutdown, dividends | | | | | | | | | | no |
| Driver lease, bankruptcy, permanent removal | | | | | | | | | | no |
| Service area (geo-fence) quote rejection and dispatch gating | | | | | | no | | | | |
| Both apps open at shift start | approx | approx | | | approx | | | | approx | |
| Cross-offer comparison, hold-for-better, coin flip | no | no | | | | | | | no | |
| Driver cancel by ETA threshold, penalty-aware | | | no | | | | | | | |
| Driver availability pausing (busy elsewhere) | approx | | | | no | | | | no | |
| Rider lexicographic choice with a deadband | approx | no | | | | | | | | approx |
| Rider failure counters and sticky switching | | | | | | | no | | | |
| Rider cancel on revised-ETA drift; continuous revise | | | | | no | | | | | |
| Event-driven install (ETA), peer cascade, idle-hour driver adoption | | | | no | | | | | | |
| Segment-specific distance, OD zones, corridors, spatial demand shocks | | no | | | | no | | no | no | |
| Repositioning without an order (deadheading) | | | | | | no | | | no | |
| Mid-trip delay or traffic | | | | | no | | | | | |
| Endogenous shift extension under debt | | | | | | | | | | no |
| Per-platform, per-window money and party-split cancels in metrics | no | | no | no | | | | | no | no |
| Empty versus loaded km per driver | | | | | | no | | | | |
| Installs versus daily first-choice query volume | | | | no | | | no | | | |

Cells marked **yes** for S2 and S9 "timed surge" refer to `policy_change` on
`multiplier`, which surges the whole map; both reviews class the zonal version
as missing.

### 2.2 Claims verified against the code

The reviews were inspection-based and mostly did not run scenarios. The
following statements they rely on were re-checked and hold at `aeddf3e`:

- `PolicyRuntime.quote` raises unless the hook returns a `QuoteProposal`, so no
  policy can refuse a quote (`policy_runtime.py`, `quote`).
- `MarketplaceEngine._settle` requires an order and `Settlement.reason` is only
  `completed_ride` or `cancellation_fee`; `bonus_minor` is folded into
  `driver_payout_minor` at settlement (`marketplace_engine.py`, `_drop_off`,
  `_settle`, `PayoutTerms.driver_payout_minor`).
- `MarketplacePolicy.cancel` charges fees only when `party == "rider"`; the
  `Cancel` contract has no driver penalty field (`marketplace_policy.py`,
  `policy_contracts.py`).
- `driver_context` omits `DriverView.pending_offers()`; `DriverPolicy.respond`
  scores one offer in isolation (`policy_runtime.py`, `behavior_policy.py`).
- Riders receive `pickup_eta_revised` but `PolicyRuntime.on_notification` has no
  branch for it; `RiderPolicy.progress` cancels only on elapsed patience.
- `ConditionalRule.when` accepts only `segment`, `new_user`, `completed_rides`,
  `role`, while the `rule()` builder accepts any key (`VISIBLE_FIELDS` versus
  `RULE` in `scenario.py`).
- `Campaign.eligible` gates on window, segment and `new_user_only` only.
- Interventions are `launch`, `policy`, `preference`; each `policy_change`
  targets one platform.
- `_realize_activity` samples origins and destinations independently and
  uniformly over `map_km`; peaks weight clock hours only; segments do not
  affect trip geometry.
- Motion begins only in `start_shift` (teleport) and `_begin_leg`; there is no
  reposition command. `World` has one speed.
- `TRAITS` in `scenario.py` binds the builtin trait classes, so a custom policy
  with a subclassed trait schema cannot be authored through segments even
  though `register_policy` accepts the subclass.
- Two same-second offers from two platforms to one idle driver both resolve
  after `response_seconds`; the first response executes while the second offer
  is still pending, so a comparative response can be atomic without a new
  scheduler concept (FIFO at equal time in `event_engine.py`).

Two review statements need correction:

- S1 and S2 propose changing the runtime's `shift_started` handler so every
  driver opens all usable apps. Make it a driver trait instead, so the `@1`
  presets keep their behavior and the change stays out of the runtime.
- S5 proposes that the engine cancel other platforms' pending offers when a
  driver's last slot fills. That would leak the global commitment count to
  those platforms, which `marketplace-engine.md` and phase 3 forbid. Section 5
  proposes the participant-side alternative.

### 2.3 Recurring documentation findings

Nine of ten reviews report the same friction, independent of features:

1. Scenario names (Alpha/Beta, Metro/Urban, Strict/Lenient, Dominant/Challenger,
   Blue/Green, UrbanOnly/CityWide, Budget/Reliable, Apex/Zenith, Steady/Flash,
   Corp/Burn/Coop) must be mapped mentally to `rebu`/`blot`/`flyt`; prose says
   the platforms are those three although IDs are free strings.
2. Setting up a two-platform market means removing `flyt` and four preset
   segments and re-adding segments; replacing the `platforms` table drops the
   inherited parameter set and fails with a long missing-key list.
3. "Multiplier" and "surge" each mean three things (tariff `multiplier`, demand
   `peaks.multiplier`, the unimplemented automatic surge).
4. There is no single "not in this release" list for money, geography,
   incentives and behavior; absence has to be inferred by search.
5. `map_km` reads as a world bound but only bounds sampling.
6. `quote_received` notifications carry no money; fares live on quote records.
7. Metrics are global: no per-platform money, no windows, no per-driver split.
8. "Multihoming" in a brief is over-read as both apps open; the shipped driver
   opens the preferred app and expands after `no_offer_seconds`.
9. `notes.md` is stale relative to the README.

## 3. The pre-PR-4 implementation

### 3.1 What it was

At `27d7f3c`, `main.Simulation(driver_count, rider_count, seed, world=,
platforms=, rider_profiles=, driver_profiles=)` created people with random
numeric IDs, took compiled `PlatformPolicy` values per platform, and accepted
profiles as a single `PersonProfile`, a list, or a callable
`(person_id, RandomValues) -> PersonProfile`. Scripts scheduled activity
imperatively with `schedule_driver_session(at, driver, location, shift_seconds)`
and `schedule_rider_session(at, rider, origin, destination)`, sampled arrival
times with `demand.WeeklyDemandProfile`, wrote free-form assumptions into
`sim.scenario_parameters`, and called `run(start, end)`. There was no schema,
no preset, no provenance, no manifest, no paired-input reuse and no policy
registry (`PolicyRuntime` bound the builtin classes).

PR 4 replaced this with `scenario.py` (schema, presets, typed changes,
compiler, `Plan.prepare`), reduced `main.py` to execution over `Inputs`, added
`register_policy`, and deleted `demand.py` and the old scripts. It did not
change the engine, the marketplace policy, the behavior policy or the runtime's
decision paths.

### 3.2 Would it be more suitable for these scenarios?

| Aspect | Pre-PR-4 | Current | Verdict |
| --- | --- | --- | --- |
| Spatial demand: zones, corridors, segment-specific distances (S2, S6, S8, S9) | Trivial: any Python in the script | `weekly` is uniform; `explicit` lists scale badly | Old is better today. Section 4.F brings this back as registered generators. |
| Deterministic fixtures with exact timing (S5, S8) | Direct `schedule_*` calls | `explicit` trips and shifts through builders (`fixture_cross_platform_queue.py`) | Equivalent; current is more verbose but validated. |
| Per-person profiles (S9 strategic/honest, S7 exclusive drivers) | Callable per person | Segments with distributions plus `person(...)` | Equivalent; current records realized profiles. |
| Two-platform market | Pass a two-key `platforms` dict | Remove preset platform and segments | Old is less verbose. Fix with a blank preset and builders (4.H), not a revert. |
| Ledgers, lockouts, guarantees, zones in pricing, repositioning, hysteresis, drift cancellation | Absent | Absent | No difference; these are the blocking gaps. |
| Paired control/treatment with shared people and schedules (S4, S7, S10, phase 4) | Re-run the script with the same seed and hope nothing else consumed the RNG | `Plan.prepare` plus `Inputs.reuse_for` | Current is required. |
| Reproduction from artifacts, provenance, unknown-key rejection | None | Manifest, fingerprints, `ScenarioError` | Current is required by `scenario-definition.md`. |
| Custom policies | Not selectable | `register_policy`, selected by `name@version` | Current is required for every "custom policy" route the reviews propose. |
| Recording assumptions | `scenario_parameters` free dict in the log | Only the resolved definition | Bring back a free `notes` mapping in the definition (4.H). |

Verdict: do not revert. Restore the two things the old code did better as
first-class, reproducible features of the plan:

1. **Registered activity and population generators.** `activity.trips.generator:
   "registered"` with `name@version`, resolved through the same registry
   mechanism as policies, receiving the resolved definition, the realized
   people and a seed-derived `random.Random`, returning session dicts that pass
   `_check_realized_conflicts`. The manifest records the identity and a source
   hash so a plan is still reproducible. The same for
   `population.<role>.generator`, replacing the segment allocation with a
   per-person callable. This is the pre-PR-4 flexibility with provenance.
2. **Direct `Inputs` construction** for throwaway fixtures:
   `Inputs.explicit(plan, seed, people=..., sessions=...)`, validated the same
   way, so a short script can schedule five trips without authoring a
   definition. It is the old imperative style on top of a compiled plan.

## 4. Capability areas

Each area lists the current state, the smallest change that stands on its
own, the files it touches, how to verify it with a scenario run or throwaway
script, and the scenarios it unlocks. Sizes are rough: S under a day, M a few
days, L a week or more.

### 4.A Money outside the ride: ledgers and transfers

Current: money exists only as `Settlement` rows created by `_drop_off` and
`cancel_order`. There is no platform cash, no driver balance, no transfer that
is not bound to an order, and `bonus_minor` is lost at settlement.

Proposed:

- Add a `Transfer` record: `id, at, reason, platform_id | None, role, person_id,
  amount_minor` (signed, positive credits the person), `order_id | None`,
  `program_id | None`, `counterparty` in `platform` or `external`. Conservation
  holds per entry: a platform transfer debits the platform's account by the
  same amount; an external transfer (lease, operating cost) has no platform
  side. Reasons start as `driver_penalty`, `guarantee_topup`, `lease`,
  `dividend`, `operating_cost`, `grant`.
- Add `Account` balances kept by the engine: per platform (`cash_minor`,
  seeded from a new `platforms.<id>.starting_cash_minor`, default `None` for
  "not tracked"), per driver and per rider. Settlements and transfers both post
  to accounts. Snapshots include accounts; restore rebuilds them from the
  tables, not from stored totals, so conservation can be re-checked.
- Keep `Settlement` for order-bound money and add `bonus_minor` to it so the
  base/bonus split survives without joining assignments.
- Engine command `post_transfer(...)` with the same validation style as other
  commands. A `Transfer` proposal type in `policy_contracts.py`; the runtime
  applies it for marketplace hooks (section 4.B) and for scenario-scheduled
  postings (section 4.G lease events).
- `Cancel` gains `driver_penalty_minor`; `MarketplacePolicy.cancel` fills it
  from a new `driver_cancellation_penalty_minor` parameter when the requesting
  party is the driver; `cancel_order` posts the penalty as a `Transfer`.
- Operating cost per km stays an offline metric (section 4.H) unless a behavior
  policy needs it; then a driver trait `operating_cost_per_km_minor` posts an
  external transfer per completed service leg.

Files: `marketplace_engine.py` (records, `_TABLES`, `post_transfer`, accounts,
snapshot), `policy_contracts.py` (`Transfer`, `Cancel`), `marketplace_policy.py`
(parameter, cancel), `policy_runtime.py` (apply `Transfer`), `scenario.py`
(`starting_cash_minor`, parameter schema), `metrics.py` (section 4.H),
`plans/architecture/marketplace-engine.md` and `marketplace-policy.md`.

Verify: a fixture with one subsidized ride and one driver cancel shows platform
cash falling by `payout - rider_payment`, a `driver_penalty` transfer of the
configured amount, and a snapshot-restore that reproduces balances.

Unlocks: S3 penalties, S9 top-ups, S10 cash, leases, dividends, S4 and S10
per-platform burn.

Size: M.

### 4.B Platform-side state: observe hook, lockouts, windows, budgets

Current: `platform_memory` changes only inside the five marketplace hooks. A
platform is notified of `offer_resolved`, `order_canceled`, `driver_app_*` and
`driver_availability`, but those notifications never reach the policy, so a
policy cannot remember that a driver canceled, expired an offer, or was online.

Proposed:

- A sixth marketplace hook, `observe(context, memory, random)`, invoked by the
  runtime for platform-audience notifications the declaration lists. It returns
  a `Decision` whose action is `Stop` or `Transfer` and whose memory is the
  updated platform memory. Declared memory fields are versioned like today.
- Pass `memory` into `candidates` (through `quote` and `dispatch`, which already
  receive it) so eligibility can read platform state.
- Built-in uses of the hook, all parameterized and off by default:
  - `driver_lockout_seconds`: on a driver-initiated cancel, record
    `locked_until[driver_id]`; `candidates` skips locked drivers. Other
    platforms are unaffected because memory is per platform. (S3)
  - Windowed driver records: dispatches sent, accepted, rejected, expired,
    app-open seconds and completed own payout per driver per window
    (`guarantee_window_seconds`). (S9)
  - Campaign `budget_minor`: reserve the discount at quote, release on quote
    expiry without an order or on cancellation, consume at completion; stop
    offering the discount when the reserved plus consumed total reaches the
    budget. This is the reservation rule `marketplace-policy.md` requires
    before advertising a budgeted promotion. (S4, S10)
- `MarketplacePolicy.quote` may return `Stop(reason)`; `PolicyRuntime.quote`
  records a `quote_refused` observation and issues nothing. The rider policy
  already treats a missing quote like missing supply and can inspect another
  app. (S6)
- `service_area` parameter (an axis-aligned box in km, or a zone name from
  4.F): refuse quotes whose origin or destination lies outside it, and skip
  candidates whose observed position lies outside it. (S6)

Files: `marketplace_policy.py`, `policy_runtime.py` (`on_notification` platform
branch, `quote`), `scenario.py` (parameters), `plans/architecture/marketplace-policy.md`.

Verify: three throwaway scripts: a Strict driver cancel followed by no Strict
offers for 1800 s while Lenient offers continue; a driver with zero own
completions and 100% acceptance in a window receives a `guarantee_topup`
transfer of the configured floor minus earnings, and a driver who expires an
offer in the window receives none; an out-of-area request gets no UrbanOnly
quote while CityWide quotes it.

Unlocks: S3, S6, S9, S4 and S10 budgets.

Size: M.

### 4.C Pricing and incentive structures

Current: one tariff formula per resolved rule; rules condition on four
visible fields; campaigns give a discount or a bonus; interventions target one
platform; commission is applied at offer time to a gross frozen at quote time.

Proposed:

- **Zone and time conditions on rules.** `visible` gains `origin_zone` and
  `destination_zone` (from `world.zones`, section 4.F; the platform already
  sees the request's coordinates) and rules gain optional `start_hours` and
  `end_hours` with the same half-open semantics as campaigns. Metro's zonal
  1.8x is one rule; Urban's flat fee is another. Deactivation at the window
  end is instantaneous for new quotes; live quotes keep their terms, which the
  scenario's 4.01 check tolerates. (S2, S9)
- **Surcharge.** `surcharge_minor` added after the multiplier, with
  `surcharge_driver_share` (0 to 1) added to the driver payout at offer time.
  This keeps the two structures from sharing a formula path, which the S2
  review flags as necessary. (S2)
- **First-N rides.** `Campaign.max_completed_rides`; eligibility uses the
  existing per-platform completion count already in `visible`. (S4)
- **Guarantee programs.** A `programs` table on a platform policy, first kind
  `hourly_guarantee` with `floor_minor`, `window_seconds`,
  `min_acceptance_rate`, `min_online_seconds`, `zero_dispatch_qualifies`.
  Evaluated at window close by the `observe` hook on a controller-cadence
  event, emitting `Transfer(reason="guarantee_topup")`. (S9)
- **Commission binding time.** `commission_binding` in `offer` (today's
  behavior) or `quote`: with `quote`, the resolved commission is stored on the
  quote and order and reused at dispatch, so a quote and an offer straddling a
  policy change carry one contract. (S8)
- **Regulation.** A new intervention kind `regulation` with `at_hours`,
  `max_base_fare_minor`, `max_per_km_minor`, `max_commission_fraction`. The
  engine stores it as a `Regulation` record and validates `issue_quote`
  (`gross <= max_base + max_per_km * distance_km`) and `create_offer`
  (`payout >= (1 - max_commission) * gross`) from its effective time. A
  violating proposal is a `CommandRejected`, recorded by the runtime as
  `command_result: regulation_rejected`, so attempted illegal quotes are
  auditable and a compliant platform must schedule its own `policy_change`. A
  `policy_change(platform="*")` convenience applies one change to every
  launched platform. (S8)
- **Announced terms.** `announce_terms: true` on a platform adds its commission
  and bonus salience to `announced`, so a driver adoption hazard can react to
  "0% commission" the way it reacts to a campaign today. (S4)

Files: `marketplace_policy.py`, `marketplace_engine.py` (regulation),
`policy_runtime.py`, `scenario.py` (schema, builders, `INTERVENTION`),
`plans/architecture/marketplace-policy.md`, `scenario-definition.md`.

Verify: quote probes for 1 km and 10 km in and out of the zone and window
(S2's exact 7.20/9.00/23.40/18.00); a straddling quote and offer under
`commission_binding=quote`; a regulation at hour 12 that rejects a still-unchanged
platform's quote and accepts the capped one; a fourth Challenger ride quoted at
full price after three discounted ones.

Unlocks: S2, S4, S8, S9.

Size: M, of which regulation is S and programs are M.

### 4.D Driver decisions v2 (`driver_participation@2`)

Current: the driver opens the preferred app, expands after a no-offer timer,
responds to each offer alone with a logistic on payout and private ETA, and
cancels only on elapsed patience.

Proposed, as a new registered implementation so `@1` behavior and its presets
stay reproducible:

- Context gains `pending_offers` (already available from `DriverView`), the
  offer's platform terms that a real app shows (cancellation penalty, lockout,
  active guarantee program) and, when the trait needs it, learned
  `estimated_wait_seconds` per platform from evolution memory.
- `open_apps_at_start`: `preferred` (today) or `all`. Applied in the runtime's
  `shift_started` branch. (S1, S2, S5, S9)
- `response_rule`: `independent` (today) or `best_pending`, which rejects the
  current offer when another pending offer scores higher on the configured
  objective (`total_payout` or `payout_per_minute` using the offer's
  destination and `world.speed_kmh`). Because same-second offers resolve FIFO
  with both pending, this is atomic for the "same matching tick" case without
  a scheduler change. (S1, S2)
- `hold_for_better`: with a positive `reservation_payout_minor` or the learned
  wait estimate, decline an inferior offer while idle when the expected value
  of waiting for a better platform exceeds it. (S1)
- `cancel_rule`: `patience` (today) or `private_eta`, with
  `cancel_eta_threshold_seconds` and `penalty_tolerance_minor`: cancel a
  commitment whose private pickup ETA exceeds the threshold when the platform's
  declared penalty plus lockout value is below tolerance. (S3)
- `availability`: `always_open` (today), `pause_when_full`, or
  `pause_while_serving_other`. Uses a new engine command `pause_app` /
  `resume_app` that keeps the app open for existing commitments, sets
  `accepting=False`, cancels that platform's pending offers to the driver, and
  publishes the existing `driver_availability` notification. This is the
  participant-side answer to S5 and S9 (section 5). (S1, S5, S9)
- `tie_break`: `stable` (today) or `random`, for equal scores. (S1)

The `TRAITS` binding in `scenario.py` must resolve the trait class from the
selected implementation's declaration rather than the builtin table, so the
`@2` traits are authorable through segments. This also removes the custom-policy
trap S9 reports.

Files: `behavior_policy.py`, `policy_runtime.py` (`driver_context`,
`shift_started`, apply pause/resume), `marketplace_engine.py`
(`pause_app`/`resume_app`, `_driver_accepting`), `scenario.py` (`TRAITS`),
`plans/architecture/behavior-policy.md`.

Verify: the S1 two-offer fixture (Alpha rejected, Beta accepted, one accepted
commitment); a Strict driver with a 7-minute private ETA cancels under Lenient
terms and completes under Strict terms; a driver on a Flash ride shows
`accepting=False` to Steady and receives no Steady offer until drop-off.

Unlocks: S1, S2, S3, S5, S9.

Size: M.

### 4.E Rider decisions v2 (`rider_search@2`) and evolution v2

Current: sequential search, one utility with one price sensitivity, cancel on
elapsed patience, preferences change only through learning or intervention,
downloads only at checkpoints from a population hazard.

Proposed:

- `choice_rule`: `utility` (today) or `lexicographic` with an ordered key list
  and tolerances, for example `[("price", 50), ("eta", 0)]` meaning cheapest
  wins, then lower ETA when prices are within 50 minor units; `tie_break`
  `stable` or `random`. (S1, S2, S10)
- `compare_all_apps`: visit every usable app before deciding, keeping the
  existing opening delays and budgets. (S2)
- **Outcome memory.** The runtime keeps, per rider and platform,
  `consecutive_failures` updated on `order_canceled` and on completions whose
  pickup wait exceeds `failure_wait_seconds`; a completed on-time ride resets
  it. Exposed in the rider context and in evolution memory. (S7)
- `fatigue_threshold` and `fatigue_target` (`best_other` or a platform ID)
  switch `preferred_app` when the threshold is reached, with a `sticky` flag
  that stops learning from switching back unless the new platform fails the
  rider or a `preference_change` intervention runs. Implemented in
  `EvolutionPolicy` for checkpoint cadence and in a rider `outcome` hook for
  immediate switching. (S7)
- **ETA drift cancellation.** The runtime re-queues `policy.rider.progress` on
  `pickup_eta_revised`; `progress` compares the latest revision to the offer's
  promise (`order.eta_predictions`) and cancels when drift exceeds
  `eta_drift_cancel_seconds`. The runtime also schedules a bounded periodic
  `revise` for assigned orders awaiting pickup (`revise_interval_seconds` on
  the platform policy) so drift appears without competitor telemetry. (S5)
- **Event-driven install.** `decide` may return `Download(platform)` for a
  known, launched, uninstalled app when `install_trigger_eta_seconds` is
  exceeded on the current quote; the runtime applies the same install and
  account activation path as checkpoints. (S4)
- **Peer cascade.** Evolution context gains `neighbor_install_share` per app,
  computed by the runtime over people within `vicinity_km` of the person's last
  known location. `install_trigger_peer_share` adds a deterministic install.
  Social knowledge of neighbors' apps is participant information, not platform
  information, so this respects the boundary. (S4)
- **Idle-hour driver adoption.** `adoption_phase`: `any` (today) or `idle`,
  with checkpoints scheduled hourly and the driver's phase in the checkpoint
  context. (S4)

Files: `behavior_policy.py`, `policy_runtime.py`, `scenario.py`,
`plans/architecture/behavior-policy.md`.

Verify: two quotes at (7.20, long ETA) and (9.00, short ETA) choose 7.20, and
at equal prices choose the shorter ETA; two forced Budget cancels switch a
rider to Reliable and a later Budget improvement does not switch them back; a
slowed preceding ride raises the revised ETA past the promise and the rider
cancels with the drift reason; a Dominant quote over 480 s installs Challenger
before the next checkpoint.

Unlocks: S1, S2, S4, S5, S7, S10.

Size: M for the rider policy, S for evolution changes, S for the runtime hooks.

### 4.F Activity and population generation with geography

Current: `weekly` trips are uniform in space with clock-hour peaks;
`explicit` lists every trip; shifts start at uniform points; segments do not
affect geometry.

Proposed:

- `world.zones`: a table of named axis-aligned boxes in km. Used by rules
  (4.C), service areas (4.B), generators (here), zone memory (4.G) and metrics
  (4.H). `map_km` remains the sampling extent; the engine still does not fence
  coordinates, and the documentation says so.
- `weekly` gains per-segment geometry under `population.<role>.segments.<id>.activity`:
  `origin_zones` (weights), `destination_zones` (weights conditional on origin
  zone), `distance_km` (a distribution; sample a destination at that distance
  in a random direction, rejecting points outside the map), and
  `spatial_peaks` (zone plus window multipliers). Shifts gain `start_zone`.
  Covers S2 short/long haul and zone doubling, S6 core/suburb mixes, S8
  corridor, S9 fringe parking.
- `registered` generators for trips, shifts and population (section 3.2), for
  anything the declarative form cannot express, with identity and source hash
  in the manifest.
- `Inputs.explicit(...)` for fixtures.
- Trip session IDs copied onto `TripIntent.source_id` by `_on_trip_start` so
  Trip A/B/C are first-class in the log. (S8)

Files: `scenario.py`, `main.py`, `marketplace_engine.py` (`TripIntent`),
`plans/architecture/scenario-definition.md`.

Verify: prepared inputs for S2 show 75 riders with 1 to 3 km trips and 75 with
8 to 12 km trips and roughly twice the zone-origin share inside the window;
S6 inputs place all drivers in the core and 80/20 versus 50/50 destination
mixes by segment.

Unlocks: S2, S6, S8, S9 setups.

Size: M.

### 4.G Physical engine extensions

Current: cars move only for service legs; the world has one speed; people and
platforms exist for the whole run; shifts are exogenous.

Proposed, in order of value:

- **Reposition.** `reposition(driver_id, destination)` starts a `Motion` and a
  service-free leg of kind `reposition` on a new `Relocation` record; an
  accepted order interrupts it and starts pickup from the current position.
  Drivers keep `accepting` while repositioning unless paused. A driver
  `idle(context, memory, random)` hook runs after an unqueued drop-off and at
  the no-offer threshold; the `@2` policy compares expected net per km of
  staying against returning to a zone using zone-level earnings memory kept by
  the evolution policy (`zone_scores`). No settlement is created; metrics
  separate reposition km from pickup km. (S6, S9)
- **Deactivate driver.** `deactivate_driver(driver_id, reason)`: end the
  shift, cancel queued commitments, finish or cancel the current pickup leg
  (an onboard ride completes first, matching cancellation rules), close apps,
  and refuse future `start_shift`. The runtime posts a `driver_deactivated`
  observation; scheduled shifts for that driver are skipped with a logged
  reason, not a run failure. (S10)
- **Shutdown platform.** `shutdown_platform(platform_id)`: `launched=False`,
  cancel open orders and pending offers with `platform_shutdown`, close every
  app session, keep boarded rides to completion on frozen terms. Triggered by
  a `shutdown` intervention or by the insolvency rule below. (S10)
- **Scheduled financial events.** Scenario `evolution.ledger` settings:
  `driver_lease_minor` and `lease_hours` post external transfers on a cadence;
  `driver_bankruptcy_minor` calls `deactivate_driver` at the posting when the
  balance is below it; `platforms.<id>.insolvency: shutdown` calls
  `shutdown_platform` when cash reaches zero at a settlement or posting;
  `platforms.<id>.dividend` (`reserve_minor`, `period_hours`,
  `min_completed_rides`) posts `dividend` transfers. (S10)
- **Shift extension.** A driver `shift_end(context, memory, random)` hook at
  the scheduled shift end may return `Extend(seconds)` up to
  `max_extension_seconds`; the `@2` policy extends while the day's net is below
  `daily_net_target_minor`. The activity conflict check treats an extension
  that overlaps the next scheduled shift as a run failure, as today. (S10)
- **Delays.** `world.speed_zones` (per-zone speed multipliers) and a `delay`
  intervention (`at_hours`, zone or box, multiplier, duration) that changes the
  speed used for legs beginning in the window; legs already in progress keep
  their planned end. Enough for S5's "active trip experiences a delay" when
  paired with periodic revise; road networks stay out of scope. (S5)

Files: `marketplace_engine.py`, `behavior_policy.py`, `policy_runtime.py`,
`main.py`, `scenario.py`, `plans/architecture/marketplace-engine.md`,
`behavior-policy.md`, `scenario-definition.md`.

Verify: a suburb drop-off followed by a reposition to the core consumes
travel time, creates no settlement and re-enables UrbanOnly offers on arrival;
a driver below the bankruptcy threshold at midnight stops appearing in any
platform's candidates; a platform reaching zero cash cancels its open orders
and issues no further quotes; a rider's revised ETA under a delay grows past
the promise.

Unlocks: S5 fully, S6 fully, S9 positioning, S10.

Size: L in total; reposition M, deactivate and shutdown S each, ledger events
S, shift extension S, delays S.

### 4.H Observability, authoring ergonomics and checks

Current: `metrics.py` reports global money, completion counts and shares by
platform, offer dispositions and utilization. Scenario scripts print ad hoc
lines.

Proposed:

- `metrics.py` additions, all offline over the existing log: money by platform
  and by declared window (`--windows 24,96`), settlements split into base,
  bonus and transfers by reason, platform cash series, per-driver ledger
  (`--ledger`), canceled orders by party and platform, a per-platform funnel
  (quotes, orders, offers, accepted, completed, canceled by party), empty
  versus loaded km per driver from service legs (pickup, transport and later
  reposition), daily first-choice query share versus installed base, and
  ETA-drift cancellations. Undefined ratios stay `null`.
- A `checks` convention: each scenario script under `scenarios/` accepts
  `--check` and evaluates its observable checks from the log through
  `metrics.py` helpers, printing pass, fail or not-evaluable with the reason.
  This is the "extensive scenario testing" phase 3 calls for, kept as scenario
  runs rather than a unit test suite.
- Authoring: a `market-blank@1` preset with world and behavior defaults but no
  platforms or segments; a `platform(id, **parameters)` builder that copies
  `MARKETPLACE_DEFAULTS_V1`; a `two_platform(...)` helper that returns a ready
  duopoly definition; a free `notes` mapping in the definition saved to the
  manifest; tighten `rule()` to `VISIBLE_FIELDS` plus the new zone and window
  keys so misuse fails at authoring time.

Files: `metrics.py`, `scenario.py`, `scenarios/*.py`, README.

Unlocks: check evaluation for every scenario; the S1, S3, S4, S6, S7, S8,
S9 and S10 observability requirements.

Size: M for metrics, S for authoring.

## 5. Information-boundary decisions

The reviews surface three places where a scenario's wording conflicts with
the documented boundary. Decide them explicitly rather than by implementation
accident.

1. **Competitor occupancy (S5, S9).** Keep the engine boundary: a platform
   never learns the global commitment count or a competitor's service. The
   scenario's "both platforms register the driver as busy" is satisfied by the
   driver disclosing it: `pause_app` under `availability=pause_when_full` or
   `pause_while_serving_other` (4.D). This mirrors real apps, keeps the S5
   ETA-blindness fixture valid under the default, and makes S9's tension
   explicit: a strategic driver who pauses Steady stops earning Steady online
   time. Reject the engine-side "cancel other platforms' offers when full"
   proposal.
2. **Regulation (S8).** Enforced by the engine, visible to everyone, not a
   platform policy. Compliance is still each platform's `policy_change`;
   attempted violations are logged.
3. **Peer adoption (S4).** Neighbors' installed apps are participant
   knowledge computed by the runtime; platforms do not receive it.

## 6. Implementation plan

Phases are ordered so each ships alone, updates its owning architecture
document, and is validated by scenario runs and throwaway scripts. Nothing
depends on phase 4 experiment tooling.

| Phase | Deliverables | Areas | Scenarios newly runnable with their checks |
| --- | --- | --- | --- |
| 0. Scenario scripts as they are | One script per scenario under `scenarios/reviews/`, using today's capabilities and documenting each approximation in its docstring; `--check` reports not-evaluable items honestly | 4.H convention | S8 (contract immutability answers its question today); S1, S5 partially |
| 1. Observability and authoring | Metrics additions, check helpers, blank preset and builders, `notes`, trip `source_id`, `rule()` validation, documentation fixes from section 9 | 4.H, part of 4.F | S1 and S8 checks fully evaluable; every other script becomes shorter |
| 2. Geography and generators | `world.zones`, segment geometry in `weekly`, registered generators, `Inputs.explicit` | 4.F | S2, S6, S8, S9 setups; S6 supply-desert approximation |
| 3. Ledger | `Transfer`, accounts, `bonus_minor` on settlement, driver penalty, `starting_cash_minor`, metrics ledger | 4.A | S3 penalty check; S4 and S10 burn check |
| 4. Platform state and pricing | `observe` hook, lockouts, service areas, quote refusal, zone and window rules, surcharge, first-N, commission binding, regulation, `policy_change("*")`, announced terms, budgets, guarantee programs | 4.B, 4.C | S2, S3, S4 vouchers, S6 fence, S8 cap, S9 guarantee |
| 5. Participant policies v2 | `driver_participation@2`, `rider_search@2`, evolution v2, `pause_app`, runtime hooks (revised ETA, outcome memory, event-driven install), `TRAITS` from declarations | 4.D, 4.E | S1, S2, S3, S5 (minus delay), S7, S4 cascade, S9 strategic |
| 6. Physical engine | Reposition and idle hook, deactivate, shutdown, ledger events, shift extension, delays | 4.G | S5, S6, S9, S10 fully |

Phases 2 and 3 are independent of each other and of phase 1; phases 4 and 5
depend on 3 (transfers) and 2 (zones) respectively; phase 6 depends on 3 and
5. Within phase 4, regulation and commission binding are independent of the
`observe` hook and can land first.

Per-phase acceptance, in addition to the verifications in section 4:

- Continuous and resumed runs still agree (`snapshot`/`restore`) with every
  new record type.
- `orders created = completed + canceled + active` and money conservation
  hold with transfers included: for every settlement and transfer, the party
  deltas sum to zero or to the declared external amount.
- `three-platform-day@1` and `three-platform-week@1` produce identical logs
  before and after each phase for seed 0, since every new mechanism defaults
  off and `@1` implementations are untouched.
- Hidden-state-free candidate lists: changing only competitor metadata does
  not change any platform decision (the existing acceptance test) also under
  `pause_app`, lockouts and service areas.

## 7. Per-scenario readiness

| Scenario | Runnable now as an approximation | Needs | Open questions for the author |
| --- | --- | --- | --- |
| S1 Take-rate elasticity | Yes: 20%/10% commissions, dual access, ETA-led riders via traits; drivers accept independently | Phase 1 (payout ratio, funnel), phase 5 (compare, hold, open all, coin flip) | Is "exactly 12.5%" per-ride after integer rounding or the design ratio? Does "dispatched" mean offered or assigned? Is the $0.30/unit cost part of the checks? |
| S2 Flat fee vs multiplier | Partly: map-wide timed multiplier and base-fare bump via `policy_change`; explicit trips for distance groups | Phase 2 (distances, zone shock), phase 4 (zone rules, surcharge), phase 5 (lexicographic riders, per-minute drivers) | Max payout per minute favors Urban's short trips, but the check expects drivers to reject them for Metro's long trips. Which rule wins? Is the doubling relative to the zone off-peak or the map? Take-rate is unspecified. |
| S3 Cancellation penalties | Partly: fares, patience, driver cancel by elapsed time | Phase 3 (penalty), phase 4 (lockout), phase 5 (ETA-threshold cancel), phase 1 (party-split metrics) | None blocking. |
| S4 Cold-start vouchers | Partly: unlaunched Challenger, launch at 24 h, commission schedule, capped `new_user_only` discount, checkpoint adoption | Phase 4 (first-N, announced terms, budget), phase 5 (ETA install, peer cascade, idle-hour adoption), phase 1 (windowed burn) | Vicinity radius is unspecified. Is the voucher window or the third ride the terminal condition? |
| S5 Queue collisions | Partly: cap, cross-platform queueing, `back_to_back_within_seconds=180`, `expand_while_busy`; the shipped fixture shows the ETA gap | Phase 5 (pausing, drift cancel, periodic revise), phase 6 (delay) | "Both platforms register the driver as locked" conflicts with the boundary; section 5 resolves it by driver disclosure. Confirm. |
| S6 Geo-fence and deadheading | Partly: CityWide is the default; core starts via explicit shifts; suburb waiting emerges; empty km reconstructible from legs | Phase 2 (zones, OD mixes), phase 4 (service area), phase 1 (km split), phase 6 (reposition, zone memory) | The zones leave uncovered strips of the map. "Retention" has no simulator meaning; earnings and utilization are the proxies. |
| S7 Reliability hysteresis | Partly: tariffs, dual installs, Reliable-exclusive drivers, Budget supply injected by explicit shifts at 48 h, learned preferences | Phase 5 (failure counters, sticky switch, long-wait failures), phase 1 (query share vs installs) | Phase 1 wait and cancellation rates are emergent, not settable; calibrate counts and geography. |
| S8 Regulatory cap | Yes for the core question: contracts are bound at quote and offer time and settled from bound terms (verified by the review's fixtures) | Phase 1 (trip IDs, implied-rate checks), phase 4 (regulation, commission binding, all-platform change), phase 2 (corridor) | `t = 11.45` decimal hours or clock time? Is $2.00/unit a cap or the new tariff? "Dispatched" equals assignment. |
| S9 Hourly guarantee | Partly: two platforms, exclusive versus dual segments, timed map-wide Flash multiplier, fringe parking via explicit shifts and trips | Phase 3 (top-up transfer), phase 4 (windows, program, zone surge), phase 5 (availability, strategic response), phase 6 (return to fringe) | Clock hours or rolling windows? Gross or payout? Does 0 of 0 dispatches qualify? Is a timeout a rejection? "No ghost double-booking" versus the two-order cap. |
| S10 Solvency and debt | Partly: take-rates, 50% subsidy with 0% commission, per-ride burn identity from settlements | Phase 3 (cash, balances), phase 6 (lease, bankruptcy, shutdown, dividends, shift extension), phase 1 (money series) | Exact hour of insolvency versus event time; do boarded Burn rides finish; are all riders and drivers already on Corp and Coop for "forced migration"; is the $2,000 reserve the starting cash. |

## 8. What not to do

- Do not revert to the pre-PR-4 API (section 3.2).
- Do not leak competitor occupancy or the global commitment count into
  `DriverPresence`, candidate lists or rejection reasons (section 5).
- Do not implement budgets by cutting promised payments at settlement;
  reserve at quote and release on expiry (4.B).
- Do not generate endogenous demand for S2's "demand doubles". The shock is
  scheduled by the scenario through zone peaks; a demand-response generator is
  a separate experimental mechanism per `scenario-definition.md`.
- Do not add road-network routing; zone speed multipliers and delay
  interventions cover the reviewed needs.
- Do not add a unit test suite; keep validation in scenario scripts with
  `--check` and throwaway scripts, per project policy.

## 9. Documentation fixes independent of code

These can land with phase 1 and address section 2.3:

1. README: state that platform IDs are free strings, show a two-platform
   snippet next to the Blot example, and name the three meanings of
   "multiplier".
2. `marketplace-policy.md`: a "Not in this release" list (zone or time surge,
   surcharge, first-N, budgets, guarantees, driver penalties and lockouts,
   service areas, platform or driver cash), the statement that fare binds at
   quote and commission at offer, and a note that a policy cannot refuse a
   quote today.
3. `behavior-policy.md`: state that offers are evaluated one at a time in
   FIFO order, that drivers open the preferred app first, that `accepting`
   means app open and not exiting rather than physically idle, that
   repositioning and consecutive-failure memory are absent, and that
   `pickup_eta_revised` is logged but not consumed by riders.
4. `scenario-definition.md`: `map_km` is a sampling extent; segments do not
   change trip geometry; replacing the `platforms` table requires complete
   parameters while `policy_change` merges; the custom-policy trait binding
   limit.
5. `metrics.py` docstring and README: money totals are global settlement
   aggregates; bonus is not a separate key; how to reconstruct per-platform
   money, cancels by party and km splits from the snapshot until phase 1.
6. `scenario-reviews/README.md`: a name-mapping and units note (platform
   labels to IDs, "unit" is a kilometre, money is minor units, `t` is hours
   after the calendar origin) for future reviewers.
7. Refresh or remove `notes.md`.

## 10. Ambiguities to return to the scenario authors

Collected from section 7, these need an answer before a check can be written
as pass or fail rather than as an assumption:

- S1: rounding of "exactly 12.5%"; "dispatched" as offer or assignment;
  whether operating cost enters any check.
- S2: per-minute objective versus the reject-short-for-long example; the
  baseline of "demand doubles"; take-rate; whether the zone box is closed.
- S4: vicinity radius; whether unused voucher rides expire at 96 h.
- S5: whether platform-visible locking is required or experimenter-visible
  capacity suffices.
- S6: coverage of the strip between the two zones; the meaning of retention.
- S8: time notation; cap versus replacement tariff.
- S9: window definition; earnings definition; 0-of-0 acceptance; timeout as
  rejection; the meaning of "ghost double-booking" given the two-order cap.
- S10: insolvency timing; boarded rides at shutdown; migration for people
  without other apps; reserve versus starting cash.
