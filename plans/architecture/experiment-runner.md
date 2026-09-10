# Experiment runner

Status: proposed design, not implemented. This document defines the comparison
and execution layer of the [phase 4 roadmap](../phase-4-experiment-runner.md).
Implementation follows phase 3 scenario definitions and extensive scenario testing.

The runner evaluates a baseline and scenario variants under declared controls.
The current execution path writes a structured `simulation.log`; `metrics.py`
reconstructs summaries and optional intervals separately after execution. Any
future runner must preserve that separation and keep metric calculations in
`metrics.py`. Rendering and comparison outputs described below are future work.

Its output is reproducible evidence about model behavior, including uncertainty
and failures. It does not decide which app someone uses or how a platform prices.

## Experiment definition and execution

An experiment specifies a base [scenario definition](scenario-definition.md),
named variant changes, replication seeds, controlled/generated inputs, warm-up
and measurement windows, observation cutoff/drain behavior, requested metrics,
output detail, and resource limits. Distinguish parameter experiments from
replacing a policy or changing a structural model version.

For each experiment:

1. Resolve and compile all scenario variants before scheduling expensive runs.
   Save semantic diffs, implementation versions, and any validation diagnostics.
2. For each replication, generate or load the declared common population and
   exogenous activity inputs, preserving stable identities where applicable.
3. Instantiate independent marketplace state, platform/participant memory,
   scheduler, and randomness for each variant. Never reuse mutated run objects.
4. Execute to the declared boundaries and collect observations through sinks.
5. Save completed/failed status, realized inputs, outcomes, and performance data.
6. Compare variants within replication, then summarize across replications using
   declared metric definitions and uncertainty methods.

A single scenario run is a one-variant, one-replication experiment. Batch runs
should support metrics-only output and optional detailed traces; generating an
interactive report for every replication is not required. Rendering, logging,
and wall-clock playback are runner concerns and must not change simulation.

## Randomness and controlled inputs

Separate generators for identities, population traits, exogenous activity,
marketplace stochastic policies, rider behavior, driver behavior, and evolution.
Use deterministic seed derivation independent of Python's randomized `hash()`.
Version the derivation algorithm and record its inputs.

Where decisions should be paired, identify random variables by replication,
stable person/intent, decision purpose, and meaningful attempt/app identity.
Do not use global order/event counters as the identity for otherwise equivalent
decisions; treatments can change those counters. Reuse trip-level outside-option
and platform taste draws according to the behavior contract. Reporting uses no
behavioral draws. Purpose-specific sequential streams remain valid where stable
event pairing is not meaningful; document their weaker correspondence.

Changing app search or creating extra offers must not shift unrelated riders'
entire subsequent random sequence. Common random inputs improve comparison but
cannot keep interactions or market trajectories identical after treatments
diverge. Do not claim that equal seeds guarantee equal realized outcomes.

Sharing population and intent/shift schedules is appropriate when those are
controlled inputs. If a variant changes demand generation, population size, or
shift response, declare that changed mechanism and share only compatible inputs.
Keep initial market share outcomes emergent; do not relabel orders to enforce a
target share.

## Warm-up, windows, and continuation

Record calendar origin, simulated origin, warm-up duration, treatment start,
measurement windows, and final observation cutoff. Distinguish events occurring
inside a window from outcomes of a cohort that started inside it.

Support two explicit ways to handle the measurement horizon:

- Stop observation at the boundary, retain unfinished state, and mark outcomes
  unresolved/censored at that cutoff.
- End new exogenous arrivals as declared, drain existing work for a declared
  duration, and observe later outcomes of selected cohorts without adding those
  later events to an earlier completion-time market share.

Do not average or compare these modes as though they used identical denominators.
Record late completions separately and cap drain/resource use explicitly.
Unfinished work is not automatically canceled at the horizon.

Run warm-up independently per variant when their policies differ during warm-up.
If variants are identical until treatment, an event-boundary checkpoint may
initialize each treatment, with independent copies and the same future controlled
random inputs. Never reuse a baseline warm-up that already applied a different
policy and call it an equivalent treatment starting state.

The scheduler retains inclusive end-boundary execution for compatibility. Use
event identities/cursors, not timestamp filtering alone, to avoid processing or
exporting a boundary event twice across continued runs. Time spans clip exactly;
metric windows document half-open intervals and the final inclusive event boundary.

A full checkpoint includes clock/queue/sequences, domain entities and trajectories,
accepted queues, pending offers, monetary commitments, access, policy memory,
generator/RNG state, intervention progress, and observer cursors. Restore only
compatible versions. Historical reports remain immutable; a new report records
its starting snapshot and newly observed events.

## Required measures and denominators

| Measure | Definition and interpretation |
| --- | --- |
| Completed-ride share | Platform completions / all completions by completion time in the selected window; undefined if none complete |
| Intent conversion | Intents creating at least one order / intents in the start cohort, counted once and observed to the declared cutoff |
| Fulfillment | Completed intents / intents in the declared cohort; separate unresolved from ended-unserved intents |
| Platform funnel | Visits, quotes, order attempts, offers, assignments, and completions; label visit/attempt counts versus unique intents |
| Offer acceptance | Accepted offers / created offers in the cohort; show rejected, expired, canceled, failed-acceptance, and pending outcomes |
| Physical service | Pickup travel + boarding + passenger transport, attributed to the actually served order |
| Commitment wait | Accepted order time before its physical pickup service begins; may overlap another platform's service |
| ETA error | Actual pickup time minus prediction time minus predicted remaining ETA; retain original and revised predictions distinctly |
| Membership/preference | Installed/registered penetration, seven personal app subsets, preferred shares, downloads, and transitions |
| Economics | Gross fare, net rider payment, discount, driver payout/bonus, cancellation settlements, and contribution |

Each created offer is pending or has one terminal disposition. The sum of
accepted, rejected, expired, canceled, acceptance-failed, and pending offers
equals offers created in the cohort. Repeated stale responses are diagnostic
events and do not add another disposition.

Count physical online time once per driver and car. Define physical idle as
online time outside service, including consideration of offers when no service
is active. A queued Blot order during Rebu transport has zero Blot physical active
time until its pickup service begins. Report platform-labeled en-route duration
separately; summing accepted-order durations is not physical utilization.

Platform offer-opportunity intervals can overlap and include service elsewhere.
Separate locally apparent candidates from personally usable opportunity with
one free commitment slot. These privileged distinctions are analyst measurements,
not new inputs to platform policies. Report idle and busy opportunity separately;
do not sum platform exposure into global hours or count censored waits as zero.

For ETA analysis, retain driver/rider context, prediction origin, and observation
time. An order canceled before pickup has no observed pickup error; report its
cancellation and censoring separately. Completed-only ETA statistics can select
away the worst delays, so present completion/cancellation rates with them.

Preferred-app shares sum to 100% within a role with valid preferences. App
penetrations and app-open counts can sum above 100%. Unique riders/drivers/cars
can appear on several platforms in a window. Avoid an undefined per-platform
utilization allocation of shared idle time; report global utilization plus
platform service, commitment, and exposure measures.

Aggregate ratios from counts/time, not averages of interval percentages. Preserve
exact clipped spans, start-cohort definitions, and snapshots needed to reconstruct
ownership at arbitrary window starts. Keep calculations in `metrics.py`; future
exports or visualizations must consume its results instead of reimplementing
metric formulas.

### Phase-1 metric families (`metrics.py`)

These offline, opt-in families (`--windows`/`--platform-detail`/
`--first-choice-days`/`--ledger`/`--ledger-drivers`, the last two added
phase 3) define the semantics any later exporter or report must reuse rather
than recompute:

- **Window boundaries** are hour offsets from the run's own start (not
  calendar hours), producing consecutive half-open windows plus a final
  inclusive one, matching `aggregate_intervals`'s own boundary rule; each
  window also carries a `cumulative` total in the same shape, from run start
  through that window's right edge.
- **Leg-start km attribution**: a service leg (`pickup` = empty km,
  `transport` = loaded km) is attributed whole to the run segment containing
  its own `started_at`. This is the rule that makes a continuous run and a
  restored continuation agree byte-for-byte on driver distance -- a leg is
  never split across a snapshot boundary. Because a snapshot is an
  event-boundary checkpoint, a leg's own `started_at` can land exactly on it
  (the segment that produced the leg ends there, and a restored continuation
  begins there); `driver_distance`/`eta_drift` implement the rule by
  cohorting legs and ETA revisions by identity -- new in `final` versus
  `initial`, the same rule every other function in this module uses -- not by
  testing each one's own timestamp against the segment's clock bounds, so
  that boundary instant is never double counted.
- **First choice** is the platform of a rider's *earliest* quote (ordered by
  `(at, id)`) among intents created in the run; drivers never query, so this
  is rider-side only. Each quote is assigned to exactly one daily (or
  `period_days`-wide) period -- the same half-open, last-inclusive bucket
  rule as window boundaries above and `aggregate_market_share`'s completion
  periods -- so a quote landing exactly on a period boundary is counted once,
  not in both the period it closes and the one it opens.
- **Installed base** at a period's start is the initial snapshot's
  `engine.riders[*].apps`, replayed forward with every `app_installed`
  observation (role `rider`) at or before that instant. Riders multi-home, so
  installed shares can sum past 100% within a period; that is expected, not a
  bug to normalize away.
- **ETA drift** on a canceled order is `timeline.canceled - (p0.at +
  p0.eta_seconds)`, where `p0` is the order's first (quote-time)
  `eta_predictions` entry; it is `null` when the platform had no supply at
  quote time. A positive drift means the platform ran later than first
  promised.
- **Transfers** (`_transfer_rows`, phase 3, section 4.A of the [readiness
  plan](../scenario-readiness-plan.md)): `settlement_breakdown`'s `transfers`
  block reports, for the settlement/transfer cohort it was built from,
  `by_reason[r]["platform_delta_minor"]` as `-sum(amount_minor)` over that
  reason's *platform-counterparty* transfers only (the debit posted to the
  funding platform) -- an external transfer (lease, operating cost) has no
  platform side and contributes nothing to any `platform_delta_minor`.
  `party_delta_minor` sums `amount_minor` over every *external* transfer in
  the cohort: a platform-counterparty transfer's person/platform deltas net
  to zero by construction, so only the external total ever moves this off
  `0`. Because an external transfer's `platform_id` is always `None`,
  `money_by_platform`'s per-platform transfers sections do not sum back to
  `settlement_breakdown`'s global transfers section whenever the run has any
  external transfer; that gap is the external total itself
  (`conservation()`'s `transfer_party_delta_minor`), not a bucketing bug to
  fix by inventing a platform attribution. With no transfers in the cohort
  (every pre-phase-3 caller, and any run before phase 3's mechanisms fire)
  the block is still exactly `{"count": 0, "by_reason": {},
  "party_delta_minor": 0}`, the shape phase 1 published.
- **Settlement split**: `settlement_breakdown`/`money_by_platform` decompose
  a settlement into `driver_base_minor`/`driver_bonus_minor` and
  `rider_gross_minor`/`rider_discount_minor`. The rider half still comes
  from the order's frozen quote fare (`fare.gross_minor`/`discount_minor`)
  only for a `completed_ride` settlement with an assignment, because only
  there is the frozen fare what the settlement actually charged
  (`rider_payment_minor` is itself defined from it); any other case (today:
  `cancellation_fee`, and a `completed_ride` with no assignment) reports
  `rider_gross_minor` as the settlement's own `rider_payment_minor`
  (`rider_discount_minor` 0) -- that half is unchanged by phase 3. The
  driver half (phase 3) now reads the settlement's OWN `bonus_minor` field
  directly instead of joining the assignment: `driver_base_minor` is
  `driver_payout_minor - bonus_minor` for every reason, not just
  `completed_ride` (a `cancellation_fee` settlement's `bonus_minor` is
  always `0`, so `driver_base_minor` there is just `driver_payout_minor`, as
  before this field existed). The only fallback is a settlement dict with no
  `bonus_minor` key at all -- a log written before `Settlement.bonus_minor`
  existed -- which instead reads the accepted offer's frozen payout
  `bonus_minor` for a `completed_ride` settlement with an assignment, else
  `0`; this reproduces the exact same integer, because the engine has always
  settled a completed ride with `bonus_minor` equal to that same frozen
  offer term, so the two sources agree bit for bit -- a source change for
  the same numbers on every log written by this engine, not a new estimate.
  This makes `driver_base_minor + driver_bonus_minor == driver_payout_minor`
  and `rider_gross_minor - rider_discount_minor == rider_payment_minor` a
  per-settlement identity for every reason, which `conservation()`'s
  `settlement_split_reconciles` checks on every `settlement_breakdown` row.
- **Ledger** (`ledger_section`, phase 3, `--ledger`/`--ledger-drivers`):
  per-platform `cash_start_minor`/`cash_end_minor` read that platform's own
  `Account.balance_minor` from the initial/final boundary snapshot (`None`
  when `starting_cash_minor` is `None` -- untracked, though its flows still
  post and are reported); `ride_contribution_minor` sums
  `platform_contribution_minor` over settlements new in this run, and
  `transfers_minor` sums the debit of this run's new platform-counterparty
  transfers funded by that platform, both broken out by `reason` too
  (`transfers_by_reason`). These are two independently derived numbers --
  one from boundary balances, one from replaying new records -- so
  `cash_end_minor - cash_start_minor == ride_contribution_minor +
  transfers_minor` is a real offline cross-check, not a tautology,
  mirroring `marketplace_engine.restore`'s own `_rebuild_accounts`
  conservation property (see marketplace-engine.md). The top-level
  `transfers` section is global (`_transfer_rows`'s shape plus, per reason,
  an `external_minor` -- that reason's own external-transfer total, the same
  formula `party_delta_minor` uses but scoped to one reason instead of the
  whole cohort). `unattributed_driver_payout_minor`
  names settlements whose `driver_payout_minor` has no `driver_id` to post
  to at all (reachable today: a rider cancels an unassigned order under a
  nonzero `driver_cancellation_compensation_minor`); it is `0` whenever a
  run never exercises that combination, and is reported rather than
  silenced by an engine-side rejection, which would change legality for
  scenarios that already authored that parameter. `--ledger-drivers`
  (implies `--ledger`) adds one row per driver (`payout_minor`,
  `transfers_minor`, `balance_start_minor`/`balance_end_minor`,
  `transfers_by_reason`). `conservation()` gains three phase-3 keys
  alongside `settlement_split_reconciles`: `transfer_party_delta_minor` (the
  same quantity as `settlement_breakdown`'s global `party_delta_minor`, over
  every transfer new in this run), `unattributed_driver_payout_minor` (the
  same quantity `ledger_section` reports), and `account_deltas_match_records`
  -- the offline twin of `marketplace_engine.restore`'s `_rebuild_accounts`
  check: every account's `settlement_minor`/`transfer_minor` delta between
  the boundary snapshots must equal that party's own leg summed over the
  records new in this run, replayed here from the raw log rather than from
  `engine.accounts`.

## Comparison and interpretation

For each metric, retain raw numerator/denominator or event data and the value
per replication. Compute treatment-minus-control differences within matched
replications, then estimate uncertainty across those independent replications.
An initial method can use a paired bootstrap over replication IDs; its analysis
RNG is separate and its method/seed are recorded. One replication has no empirical
between-replication uncertainty estimate. Do not treat millions of correlated
rides from one run as millions of independent replications.

Distinguish mean per-replication ratios from ratios pooled across replications;
state which quantity is reported. Preserve undefined denominators and document
how many paired values are available. Do not silently replace undefined shares
with zero or exclude failed runs as though they were missing at random.

Compare absolute rides, unserved demand, wait/ETA distributions, cancellation,
driver earnings per physical online hour, and contribution alongside share.
A discount can attract more demand than supply can serve; increased share or
fulfillment is not an invariant to enforce in a congested market.

Use frozen ownership/preference controls to separate immediate pricing and supply
effects from adaptation. Mark interventions on time series and compare post-
campaign retention. Clearly distinguish synthetic model findings from calibrated
predictions about real-world platforms.

## Artifacts, errors, and performance

Save an experiment manifest, authored and resolved definitions, semantic diffs,
selected implementation hashes/versions, compiler and metric versions, environment
information, seed derivation, realized population/activity inputs, status, summary
metrics, and selected event traces/checkpoints. Deduplicate immutable shared input
artifacts by content identity, while each run references the exact artifact used.

Record failures with scenario/replication identity, event context, diagnostics,
and any reproducible checkpoint. An interrupted or invalid run is not a valid
zero-outcome replication. Resume from a validated checkpoint or start fresh;
do not rerun a partly applied event blindly. Show failed-run counts in comparisons.

Parallelize independent replications/variants in separate processes before trying
to parallelize conflicting events within one market. Keep outputs and mutable
policy state isolated. Report wall time, preparation time, events, peak queue,
memory, log/history size, and report cost separately. For a later compiled path,
separate cold compilation from warm execution and verify output equivalence.

Profile real weekly/multiweek workloads before changing storage, indexing, or
numeric code. A faster candidate index must still use platform-visible eligibility
instead of a hidden global-idle filter. Metrics-only mode must preserve outcomes.

## Initial experiment suite and acceptance

- A deterministic three-platform fixture covering competing offers, the two-order
  cap, queue cancellation, physical positions, and the 6+4 versus 3 minute ETA case.
- A baseline and isolated per-platform tariff/dispatch/segment-policy variants.
- A multiweek baseline, Blot discount, Flyt driver bonus, and post-campaign period,
  with matched frozen and evolving controls; combined campaigns do not substitute
  for isolated treatments when attributing individual effects.
- A Flyt launch variant with initially absent access and declared awareness,
  adoption, and car onboarding.
- A back-to-back willingness comparison, keeping exogenous people/intents/shifts
  constant while allowing physical outcomes and queues to diverge.

Validate serial/parallel equality, trace/report independence, repeatable saved
definitions, continuous/resumed equality, exact boundary accounting, money and
entity conservation, consistent metrics across analysis outputs, undefined
shares, and failure visibility. Use the old weekly calibration only for legacy regression;
do not force new-model outcomes to match its ride count or utilization.
