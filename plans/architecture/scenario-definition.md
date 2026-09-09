# Scenario definition

Status: implemented in `scenario.py`, executed by `main.py`. This was phase 3's
final implementation step in the
[phase 3 roadmap](../phase-3-multi-platform-marketplace.md); extensive scenario
testing follows before the [phase 4 experiment runner](../phase-4-experiment-runner.md).

## Shipped implementation

`Scenario(preset="three-platform-week@1")` selects a published preset;
`Scenario.from_definition(dict)` and `Scenario.load(path)` accept a complete
definition or a saved plan manifest. `with_changes({path: value})` sets values
by dotted path (maps merge by schema, lists are replaced), `add(path, item)`
and `remove(path, id)` edit keyed collections and ID tables, and `renamed`
labels the variant. Typed builders (`campaign`, `rule`, `peak`, `segment`,
`person`, `shift`, `trip`, `launch`, `policy_change`, `preference_change`,
`platform_policy`) return complete items. Times are authored in hours after
the calendar origin and normalized to seconds; money stays in declared minor units.

`compile_scenario(scenario)` returns an immutable `Plan`: the canonical resolved
definition, per-value provenance (`preset:<id>`, `definition`, or
`change[i]:<op> <path>`), warnings, compiled `PlatformPolicy` values, selected
implementations, behavior defaults, segment plans, explicit people, intervention
entries ordered by time then launch/policy/preference, checkpoint times and
fingerprints for the definition, controls, implementations and plan.
`diff_plans(baseline, variant)` lists leaf-level semantic differences including
inherited values and implementation replacements. `Plan.save(path)` writes the
manifest; loading it reproduces the same definition fingerprint.

`Plan.prepare(seed)` realizes `Inputs`: population records (stable `rider-n`,
`driver-n`, `car-driver-n` identities, segment assignment by largest-remainder
counts and seeded order, traits sampled once per person from declared
distributions) and sessions (`trip-n` in arrival order, rotation or explicit
shifts). `Inputs.reuse_for(variant_plan)` shares identities and schedules with
a variant whose world, population and activity sections are unchanged.
`main.Simulation(inputs)` builds fresh state and `run()` executes to the horizon.

Implementations are selected by `name@version` from `policy_runtime.py`'s
registry (`register_policy(family, cls)`), one for riders, drivers, evolution
and each platform's marketplace. A replacement must accept the family's
parameter schema and hooks; the replaced implementation's parameters are not
carried over. The compiler cannot prove custom Python correct.

Published presets: `three-platform-day@1` (one Monday, 10 drivers, 100 riders,
one trip each) and `three-platform-week@1` (Monday through Sunday plus a drain
hour, 30 drivers in three crews, 3,500 riders, weekday and weekend peaks, daily
checkpoints). Both are labeled `calibration: synthetic` and write every default
out under their version. A `scenario.ScenarioError` reports every independent
configuration error together, each naming its path.

A top-level `notes` table (`{id: text string}`, e.g. `{"assumption": "...",
"approximation": "...", "source": "..."}`) holds free-form authoring notes,
set with `.with_changes({"notes": {...}})` like any other `Table` (a merge,
so a script can add keys without restating existing ones). It is part of the
resolved definition -- hence of `fingerprints['definition']` and of
`manifest()['resolved']['notes']`, and surfaced again directly at
`manifest()['notes']` and the `Plan.notes` property for grepping -- but it is
deliberately **not** part of `controls` (`compile_scenario` builds `controls`
from `world`/`population`/`activity` only), so two variants that differ only
in their notes still share prepared inputs through `Inputs.reuse_for`. A
required key means a JSON definition or saved manifest from before this field
existed fails `Scenario.load` with `notes: missing required setting`; add
`"notes": {}` to migrate it. That failure is accepted, not patched with a
silent default -- a hidden default would break the "complete definition"
invariant and provenance the rest of this document describes.

`PRESETS['market-blank@1']` is `_base_v1` with empty `platforms` and empty
`population.<role>.segments`: world, behavior defaults, `activity` generators
set to `none`, evolution off. `compile_scenario` on it alone fails with
`platforms: at least one platform is required`, which is the intended
authoring-time signal that a script must add its own market.
`platform(id, **parameters)` builds one complete `{id: <PLATFORM entry>}`
table entry over `MARKETPLACE_DEFAULTS_V1` (rejecting an unknown parameter
name with its exact path, at authoring time, before `compile_scenario` would);
`two_platform(first, second, ...)` composes two `platform()` calls and one
`both-apps` population segment per role into a ready `Scenario` on
`market-blank@1`. Because `platforms` and `population.<role>.segments` are
`Table`s (merge by ID into whatever the base already has), starting from
`market-blank@1` is what lets a script hand these builders a whole market in
one `with_changes`, instead of first removing a preset's existing named
segments. `rule()` validates `when` against `marketplace_policy.VISIBLE_FIELDS`
at authoring time (`ConditionalRule.__post_init__` enforces the same
condition again at `compile_scenario` time); the visible-field set is read
from `marketplace_policy`, not duplicated, so it grows automatically when a
later phase adds zone or window keys to it.

Supported activity generators are `rotation`/`explicit`/`none` shifts and
`weekly`/`explicit`/`none` trips; the only conflict policy is `fail`. Static
checks reject crews whose shifts overlap, overlapping explicit shift windows and
same-second trips for one rider, and warn about trips within a rider's search
patience. Endogenous demand, queue/defer policies, unit conversion of money,
JSON/YAML front ends beyond `Scenario.load`, and restricted formula languages
are not implemented.

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

`world.map_km` is a sampling extent for the generators (`_sample_point`'s grid
or continuous draw), not a physical fence: the engine never rejects or clips a
coordinate outside it, so an explicit trip or shift can name a point beyond
`map_km` on purpose. `population.<role>.segments`/`people` never carry trip
geometry -- origins and destinations come only from `activity.trips`, so a
segment shapes who travels and on what terms, never where. Replacing
`platforms.<id>` (a full `Table` entry, via `with_changes` or `platform()`)
requires every policy parameter, the same as any other complete preset value;
a `policy_change` intervention instead **merges** onto the platform's current
policy, so it can move just the fields it names. A custom (non-`@1`) rider,
driver or evolution policy implementation must still accept exactly the
shipped `RiderTraits`/`DriverTraits`/`EvolutionTraits` dataclasses: `scenario.py`'s
`TRAITS` mapping that binds segment/person trait dicts is fixed to those three
schemas regardless of which implementation a scenario selects, so a custom
policy cannot introduce its own trait fields through population segments yet
(deriving `TRAITS` from each implementation's own declaration is future work,
plan section 4.D/4.E).

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
