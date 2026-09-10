# Simulator realism and research utility roadmap

Status: proposed roadmap for reference, recorded on 2026-09-10. This document
describes future work; it does not assert that the acceptance criteria have
already been met.

## Purpose and phase numbering

Keep Phase 3 finalization separate from improving the model itself: first
finish and verify the capabilities already promised, then build the research
infrastructure and progressively improve realism.

This roadmap uses the top-level numbering in
[Phase 3: multi-platform marketplace](phase-3-multi-platform-marketplace.md)
and [Phase 4: experiment runner](phase-4-experiment-runner.md). The separately
numbered implementation steps in the
[scenario-readiness plan](scenario-readiness-plan.md) are not new top-level
phases. Outstanding work from that current readiness effort belongs in the
Phase 3 finalization bucket below.

The intended research focus is platform competition, pricing, and participant
behavior. Full traffic microsimulation is not a prerequisite.

## Phase 3 finalization — Complete the existing simulator

**Objective:** make the current implementation, scenarios, checks, and
documentation agree.

### Scope

- Audit existing requirements against implementation and executable evidence.
- Finish genuinely missing pieces within that agreed scope.
- Update review scenarios to use existing capabilities: guarantees,
  geofencing, cancellation penalties, V2 behavior, shutdown, debt, dividends,
  repositioning, and delays where required.
- Replace stale `NOT-EVALUABLE` explanations with working checks or precise
  descriptions of remaining limitations.
- Verify interactions between mechanisms, not just isolated fixtures.
- Check accounting, physical occupancy, information boundaries, expiry races,
  and checkpoint restoration.
- Resolve reproducibility-contract gaps, including random identities based
  on global counters. Full paired-experiment execution remains Phase 4 work.
- Fix setup and documentation inconsistencies, including Python-version
  support.

Do not add new labor-supply models, realistic routing, or adaptive pricing to
this bucket merely because they would improve realism.

**Exit criterion:** every in-scope requirement has executable evidence;
remaining exclusions are explicitly documented. A scenario passing its checks
means its mechanism works, not that its economic hypothesis is true.

## Phase 4 — Reliable experimentation

**Objective:** turn individual simulations into reproducible comparative
experiments.

This phase largely follows the existing
[Phase 4 plan](phase-4-experiment-runner.md) and its
[detailed experiment-runner design](architecture/experiment-runner.md).

### Scope

- Baselines, named treatments, parameter sweeps, and multiple replication
  seeds.
- Shared populations and schedules where scientifically appropriate.
- Controlled random pairing and fresh state for every run.
- Warm-up, treatment, measurement, and drain windows.
- Paired treatment effects and uncertainty across replications, rather than
  treating interacting rides within one run as independent replications.
- Explicit handling of failed runs, censored outcomes, and undefined metrics.
- Experiment manifests, resumability, parallel execution, and metrics-only
  output.
- Decision diagnostics and performance measurements sufficient to investigate
  surprising results.
- Parameter sensitivity and comparisons between existing policy versions.

The experiment layer should reveal when a conclusion depends heavily on an
assumption before more mechanisms are added. Reporting and analysis must not
change simulation decisions or expose hidden information to policies.

**Exit criterion:** one experiment definition produces a reproducible
baseline-versus-treatment report, including uncertainty and failures.

**Research unlocked:** under these synthetic assumptions, what changes when
commission falls?

## Phase 5 — Empirical foundations and a credible baseline market

**Objective:** make the environment and population defensible before making
behavior more sophisticated.

### Scope

- Data-import interfaces for demand, travel, and available behavioral
  observations.
- Time-dependent origin–destination demand and travel-time matrices.
- Coherent participant segments, including correlated traits and geographic
  patterns.
- Operating costs tied to actual activity: passenger travel, pickup, and
  repositioning.
- Clear separation of actual conditions from platform estimates and
  participant beliefs.
- Calibration tooling, parameter provenance, and held-out evaluation datasets.
- Distributional metrics: earnings per online hour, waiting-time tails,
  unserved demand, and geographic differences.

Start with an achievable reference market. A calibrated travel-time matrix may
be sufficient; a complete road-network simulator is not a prerequisite.
Calibration begins here and continues whenever later phases change the model.
Where evidence is unavailable, retain explicitly uncertain assumptions rather
than presenting synthetic coefficients as empirical estimates.

**Exit criterion:** a versioned baseline reproduces selected observed patterns
within declared tolerances, with unsupported assumptions clearly identified.

**Research unlocked:** does the simulated market resemble the particular market
we intend to study?

## Phase 6 — Economic participant behavior

**Objective:** make drivers and riders respond to the economic consequences of
their choices.

### Milestone 6A — Decisions within a trip or shift

- Driver acceptance based on expected incremental net earnings, total time,
  destination, and alternative opportunities.
- Heterogeneous response times and uncertainty in expectations.
- Consistent reasoning across acceptance, cancellation, and repositioning.
- Rider choice and cancellation informed by trip purpose, urgency, price, and
  reliability.
- Competing behavioral models retained as explicit, versioned alternatives.

### Milestone 6B — Participation across time

- Drivers decide whether to start working, take breaks, stop, extend, and
  return.
- Different participation models, including reservation-wage and
  income-targeting behavior.
- Future rider usage responds to previous experiences.
- Endogenous demand and supply operate alongside, rather than replace,
  fixed-input experimental controls.

Researchers sometimes need fixed shifts to isolate acceptance behavior and
sometimes need endogenous shifts to measure labor supply. Both modes must
remain available, with the controlled and endogenous inputs stated explicitly.

**Exit criterion:** earnings and service conditions influence participation
through explicit, testable mechanisms; alternative behavioral assumptions can
be compared.

**Research unlocked:** does lower commission attract more working hours, merely
redirect existing drivers, or both?

## Phase 7 — Adaptive platform competition

**Objective:** allow platforms to react to the evolving market.

### Scope

- Supply–demand-responsive pricing.
- Geographic driver incentives and budget allocation.
- Matching strategies incorporating pickup reliability and acceptance
  likelihood.
- Sequential versus batched dispatch.
- Campaign adaptation and responses to financial constraints.
- Experiments on disclosure, multi-apping, and queueing rules.

Begin with transparent rule-based controllers. More complex optimization
should earn its place by improving a specific research application. Retain
fixed-policy platforms as controls so participant responses can be
distinguished from platform reactions.

**Exit criterion:** competing platforms adapt using only permitted
observations, and experiments can separate the effects of each adaptive
mechanism.

**Research unlocked:** what happens after competitors respond, not just
immediately after one platform changes its policy?

## Phase 8 — Integrated validation and a research release

**Objective:** establish which conclusions the complete model can support.

This is not the first testing phase. Verification and validation belong in
every preceding phase; here, the focus is independent evaluation of the
integrated system.

### Scope

- Held-out validation against observed periods or interventions.
- Robustness to parameter uncertainty and alternative model structures.
- Tests for conclusions driven by initialization, tie-breaking, queue limits,
  or measurement boundaries.
- Published benchmark experiments and reproducible reference results.
- Documentation of equations, assumptions, evidence, and applicability limits.
- Clear distinctions between illustrative findings, robust model findings,
  and empirically supported predictions.

**Exit criterion:** another researcher can reproduce a result and understand
both its evidential support and its limitations.

## Sequencing and scope boundaries

The progression is:

1. Finish the current model.
2. Compare it reliably.
3. Ground it in data.
4. Improve participant economics.
5. Add platform adaptation.
6. Validate the integrated research instrument.

Each phase should produce a usable increment. Existing simpler policies and
fixed-input controls remain available as reference models; later phases do not
silently redefine earlier published experiments.

Full traffic microsimulation, pooling, shared fleets, and sophisticated
learning algorithms remain research-specific extensions rather than mandatory
parts of this roadmap.
