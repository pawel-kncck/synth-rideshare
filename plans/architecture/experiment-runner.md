# Experiment runner

Status: proposed design, not implemented. This document defines the comparison
and execution layer of the [phase 3 roadmap](../phase-3-multi-platform-marketplace.md).

The runner evaluates a baseline and scenario variants under declared controls.
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
ownership at arbitrary window starts. Ensure Python, JSON/CSV exports, and the
dashboard's platform/time filters agree.

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
entity conservation, Python/JavaScript metric parity, undefined shares, and
failure visibility. Use the old weekly calibration only for legacy regression;
do not force new-model outcomes to match its ride count or utilization.
