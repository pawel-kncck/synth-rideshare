# Scenario definition

Status: proposed design, not implemented. Preset names and API examples below
illustrate authoring contracts for the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md). This is phase 3's
final implementation step, followed by extensive scenario testing before the
[phase 4 experiment runner](../phase-4-experiment-runner.md).

Phase 3 must support compiling a definition and executing a single seeded scenario
through the existing `Simulation` orchestration. It must not depend on the phase 4
batch runner, paired variants, or comparison reports. Per-replication contracts
below define the handoff to that future runner.

A scenario describes a world and the models acting in it. It should be short
when an experiment changes little, complete after resolution, and reproducible
after defaults or implementation code evolve. It does not directly drive rides
or mutate live engine objects.

## Authoring and configuration groups

Keep Python entry points initially, with typed builders returning definitions.
JSON/YAML can later be alternative inputs to the same schema; building a new
modeling language is not a prerequisite. A custom Python policy is registered
by identity and version instead of embedded as an anonymous callback in data.

| Group | Contents |
| --- | --- |
| Identity and versions | Scenario name, schema, preset, engine model, and selected policy versions |
| World | Geography, coordinate/time/currency units, movement parameters, calendar origin, and horizon |
| Population | Rider/driver/car identities or generators, correlated segments, app sets, registrations, preferences, traits, and car bindings |
| Activity | Explicit or generated trip intents and shifts, weekly demand shape, and declared conflict behavior |
| Platforms | Rebu/Blot/Flyt launch state and marketplace policy definitions, including conditional segment rules |
| Behavior | Rider/driver policy selections, personal parameter defaults, and memory initialization |
| Evolution | Awareness, adoption, learning, update cadence, and onboarding/registration policies |
| Interventions | Time-indexed launches, policy versions, exposure, and explicit cohort updates |

Replication seeds, variants, output modes, warm-up/measurement windows, and
comparison settings belong to the [experiment definition](experiment-runner.md).
They are saved alongside the scenario and may be supplied by a convenience
single-run command. Simulation outcomes such as market share are not inputs to
be imposed on orders; initial ownership/preference shares are valid inputs.

Conceptual future authoring:

```python
baseline = Scenario(preset="three-platform-week@1")
variant = baseline.with_changes({
    "platforms.blot.pricing.parameters.per_km": 1.2,
    "behavior.driver_apps.parameters.no_offer_tolerance_seconds": 90,
})
prepared = compile_scenario(variant)
```

This is not an existing callable API. A published preset must supply every
required setting, with actual numeric defaults committed and tested under its
version. Do not publish a preset that relies on incidental constructor defaults.

## Defaults, overrides, and provenance

Use complete versioned modern presets, including a three-platform preset. Label synthetic defaults; calibration
is a separate artifact with its source and applicable model version. A future
default change creates a new preset version, not a changed meaning of `@1`.

Resolve preset values, then scenario overrides. Within a policy family, apply
its documented target hierarchy: shared platform defaults then platform override
then a matching conditional rule; personal defaults then segment then explicit
person. These are distinct hierarchies, not one global merge across unrelated
fields. Platform policy conditions use only platform-visible segments.

Key platforms and segments by stable ID. Merge maps by declared schema; replace
lists as a whole unless a dedicated keyed update operation is specified. Use
explicit add/remove operations for campaign and intervention collections. Missing
means inherit; null is accepted only where the schema defines a meaning. Reject
unknown IDs/keys and unused parameters, including parameters from a replaced
policy implementation. A policy replacement validates its own parameter schema.

Conditional policy rules have explicit unique priorities and a fallback. The
first matching rule wins; its identity is recorded. Reject duplicate priorities
and statically detectable conflicts. Arbitrary runtime predicates cannot always
be proven disjoint, so do not rely on such a proof for resolution semantics.

Export a canonical resolved definition and provenance for each resolved value.
Show a semantic difference against the baseline after resolution, so an inherited
change or a model replacement is visible alongside numeric overrides.

## Population and activity definitions

Support explicit people/cars for small deterministic scenarios and seeded
generators for scale. Person IDs and exogenous intent IDs must be stable across
paired variants; do not derive them from later order creation sequence.

Validate all seven nonempty rider/driver app combinations, conditional preferred
apps, car registration sets, and one-car-per-driver initial bindings. People and
cars each retain at least one membership. Active participants must have usable
launched access; a driver's preferred usable app must be compatible with their
car. An entrant variant starts without members of an unlaunched app and uses
explicit exposure/adoption/onboarding to grow it after launch.

Allow correlated segment generation and explicit distributions rather than
requiring every trait to be independent. Weights must sum to one. If exact counts
are requested, use deterministic rounding plus seeded assignment. Save realized
profiles as well as generator parameters. Cars do not inherit registrations
silently; a declared default generator can deliberately match each assigned
driver's access. Driver adoption can trigger a declared car-registration policy.

Generate intended trips and shifts as exogenous inputs in the initial preset.
Prepare those schedules per replication and reuse them across variants when
they are controlled inputs. Repeated people retain memory and memberships.
Endogenous demand or shift extension requires an explicitly selected generator
or behavior policy and becomes a different experimental mechanism.

Detect static schedule conflicts where possible. A future overlap can depend
on realized ride duration, search, or shift overtime. The default is a clear run
failure for an impossible new session, preserving current behavior. A later
supported queue/defer policy must preserve and record the original intent/time;
it cannot silently drop demand in the treatment scenario. Static validation
must not claim to prove that all future sessions are nonoverlapping.

## Interventions and effective time

Interventions specify simulated time, target, operation, and policy/cohort
identity. Validate windows and conflicting explicit writes to the same property
at the same time. Use half-open tariff/campaign windows, with effective policy
lookup at the decision time rather than depending on event insertion order.

At a learning checkpoint, apply due explicit interventions first, take a common
population snapshot, compute behavioral updates, then apply them together. This
domain ordering is implemented above the generic event scheduler. Keep fixed
checkpoint origins when comparing continuous and split execution.

Changes affect the next permitted decision. They do not reprice accepted terms,
teleport entities, clear a queue, or interrupt a passenger ride. A driver app
download and car registration remain separately validated and logged even when
one onboarding policy coordinates them.

## Compiler pipeline and output

The initial compiler prepares execution; it does not emit machine code or a
precomputed future marketplace. Its stages are:

1. Parse/build the typed definition and resolve versioned presets and overrides.
2. Normalize declared units and monetary representations; keep canonical values
   and user-facing source paths for diagnostics.
3. Resolve policy IDs, entity/platform references, segment visibility, and schemas.
4. Validate ranges, combinations, required observations, allowed command types,
   and known lifecycle compatibility. Reject attempts to configure three accepted
   orders or to bypass the engine's other fixed constraints.
5. Prepare immutable policy bindings, time-window lookup tables, static rule data,
   and population/activity generator plans. Resolve conditional runtime values
   only when their declared inputs are available.
6. Produce the execution plan, resolved manifest, provenance, warnings, and
   reproducible fingerprints for definitions and selected implementations.

The runner instantiates a fresh world, scheduler, personal/platform memory, and
randomness for each replication. Generated profiles/schedules are explicit input
artifacts, not mutable state shared by runs. Cache prepared plans only when the
relevant definition, compiler, implementation, and environment versions match.

Do not precompute future matching, evolving preferences, dynamic surge, or
endogenous arrivals as though they were independent of prior decisions. A plan
is immutable; the world it constructs remains dynamic.

## Extensibility and limits of validation

Policies declare input/output and memory contracts. Prefer bounded named hooks
such as quote decision, offer response, no-offer threshold, trip ending, and
evolution checkpoint. Avoid an unrestricted hook that receives the entire
simulation before/after every event. Observation hooks cannot mutate the world.

Compilation rejects wrong types, unknown references, invalid probabilities,
incompatible policies, unsupported observation access, and statically invalid
limits. Runtime validates finite outputs, valid identities, command permissions,
offer freshness, available capacity, and legal transitions. Scenario runs and
throwaway scripts assess behavioral logic, information leakage, calibration,
and bias.

Arbitrary Python cannot generally be proven pure, terminating, or correct by
this compiler. It remains trusted extension code with runtime checks and
scenario validation.
A restricted formula language may later allow stronger inspection/optimization
for selected policy families. Introduce it after concrete experimental needs,
not as a requirement to compile every user-written Python function.

Native compilation is optional later work for measured numerical bottlenecks.
Preparation can remove repeated configuration lookups and bind policies once;
it does not establish a runtime speedup without measurement. An optimized path
must preserve semantics and identify unsupported policies explicitly.

## Diagnostics, migration, and acceptance

Errors name the exact field, policy/rule, expected contract, and conflicting
value/reference. Report independent configuration errors together where possible.
Warnings explain unresolved runtime risks without silently rewriting a scenario.

The shipped Python API uses explicit world, platform policy, and population
profile values. Old scalar `Simulation(...)` pricing and probability arguments
are removed. The future compiler should bind this modern contract directly;
legacy scenario compatibility is not a requirement.

Test minimal preset resolution; immutable version pinning; unknown-key errors;
policy replacement; segment-rule precedence; invalid app/car access; conflicting
interventions; invalid units/probabilities; forbidden third-order configuration;
resolved diffs; identical per-replication generated inputs; and fresh isolated
state from a shared prepared plan. Reconstruct a scenario from saved artifacts
without reading today's defaults or relying on plugin globals.
