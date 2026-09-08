# Phase 4: Experiment runner and comparison

Status: deferred until [phase 3](phase-3-multi-platform-marketplace.md) completes
[scenario definition](architecture/scenario-definition.md) and the subsequent
period of extensive scenario testing.

The detailed design lives in [Experiment runner](architecture/experiment-runner.md).
This phase builds on compiled scenario definitions and the existing marketplace,
policy, checkpoint, and report contracts.

## Scope and implementation sequence

| Milestone | Deliverable and acceptance gate |
| --- | --- |
| 1. Experiment definition and execution | Baseline and named variants, replication seeds, semantic diffs, saved manifests, and fresh state per variant and replication. Reproduce a saved small experiment with all defaults explicit. |
| 2. Controlled inputs and reproducibility | Shared populations and exogenous schedules where compatible, stable random identities, implementation provenance, and explicit failure records. Validate repeatability and independence from logging/report detail. |
| 3. Windows and continuation | Declared warm-up, treatment, measurement, cutoff/drain behavior, and checkpoint integration. Continuous and resumed runs agree without duplicated boundary records or invented terminal outcomes. |
| 4. Comparison and reporting | Platform/time reports, paired differences, replicate uncertainty, raw numerators/denominators, and Python/dashboard metric parity. Keep unfinished outcomes and undefined shares visible. |
| 5. Scale and performance | Small, weekly, and multiweek measurements; metrics-only batch output; resource limits; serial/parallel equality where parallel execution is supported. Profile runtime, peak memory, candidate scans, event counts, logs, and reports before optimizing. |

## Validation and handoff

Use scenario runs and throwaway scripts; there is no unit test suite. Extend the
scenarios exercised after phase 3 into the reference experiments specified by
the runner design. Check controlled inputs, entity and money conservation,
information boundaries, exact window accounting, failure visibility, and
repeatable comparisons. Preserve platform knowledge boundaries when optimizing.

Earlier single-platform calibration figures are historical references, not
targets for the three-platform model. Document observed outcomes, performance,
and remaining model limitations alongside each experiment.
