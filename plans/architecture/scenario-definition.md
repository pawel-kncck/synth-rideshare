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

Supported activity generators are `rotation`/`explicit`/`registered`/`none`
shifts and `weekly`/`explicit`/`registered`/`none` trips; the only conflict
policy is `fail`. Static checks reject crews whose shifts overlap, overlapping
explicit shift windows and same-second trips for one rider, and warn about
trips within a rider's search patience. Endogenous demand, queue/defer
policies, unit conversion of money, JSON/YAML front ends beyond
`Scenario.load`, and restricted formula languages are not implemented.

## Geography: zones and per-segment trip demand

`world.zones` is a `Table` of named, closed, axis-aligned boxes in kilometres
(`{id: {"min": [x0, y0], "max": [x1, y1]}}`, built with `zone(id, min=..., max=...)`
mirroring `platform()`). It is required (`"zones": {}` is the off state every
preset ships) but, like `map_km`, it never reaches `World` or
`marketplace_engine.py` -- the engine still does not fence or clip a
coordinate, so declaring a zone changes only what the *generators* below can
reference, never what a coordinate means at runtime. `compile_scenario`
(`_check_zones`) rejects a box whose `max` is not strictly greater than `min`
on both axes, warns when a box extends beyond `map_km` (a sampled point could
then fall outside the sampling extent), and, when `world.sampling` is `grid`,
requires a multiple of `grid_step_km` inside the box on *each* axis
independently (sampling draws each axis separately). Overlapping zones are
legal and undiagnosed: a point is sampled by picking a zone first, so overlap
never double-counts, and a point on a shared edge belongs to both boxes under
this closed-box test.

`population.<role>.segments.<id>.activity` (and the same key on an explicit
`person()`) is a `partial` map, absent by default, that gives the `weekly`
trip generator and the `rotation` shift generator geography instead of the
map-wide uniform draw every segment had before this mechanism existed. A
rider's `activity` may declare `origin_zones` (weights over declared zones),
`destination_zones` (weights *conditional on the drawn origin zone* --
requires `origin_zones`, and every origin zone with positive weight needs a
row), `distance_km` (a distribution; mutually exclusive with
`destination_zones`), and `spatial_peaks` (a zone list, clock window and
multiplier that boosts that zone's `origin_zones` weight inside the window --
overlapping peaks combine with `max`, the same aggregation `_peak_weight`
already uses for time-of-day surge; origins only, never destinations or
arrival times). A driver's `activity` may declare `start_zone`. Declaring
rider geometry while `activity.trips` is not `weekly`, or `start_zone` while
`activity.shifts` is not `rotation`, compiles with a warning, not an error --
the fields are simply unused by the active generator. `_check_segment_activity`
validates every zone reference, weight sum, and (against `world.map_km`) the
feasibility of `distance_km`'s upper bound: it must fit from the map's centre
(`distance <= hypot(*map_km) / 2`, the worst-case origin) or compilation
fails outright, and a warning follows if it exceeds `min(map_km)` (rejection
sampling then discards most directions) or if `world.sampling` is `grid`
(the drawn distance is perturbed by snapping the destination to the grid
afterward). Trip count is never changed by any of this -- `trips_per_rider`
remains the sole control on demand volume; geometry only changes *where* a
trip starts and ends and, through `spatial_peaks`, *which zone it is more
likely to start in* during a window.

Realizing a trip with declared geometry draws, in order: an origin zone
(weighted by `origin_zones`, adjusted for any matching `spatial_peaks` at
that instant, then a point inside it); then a destination, by whichever one
of `destination_zones` / `distance_km` / (neither) applies, redrawn while it
equals the origin, up to 100 attempts total -- a distance-based destination
draws its distance once and only resamples the direction on each attempt.
Exhausting the budget is a deterministic run failure (`ScenarioError`), never
an infinite loop; the compile-time feasibility check above exists precisely
to make that rare. When a trip's rider (or a shift's driver) declares nothing,
realization takes the identical, byte-for-byte unchanged map-wide
`_sample_point` branch every scenario used before this mechanism existed --
this is why the published `@1` presets, which declare no zones and no
segment `activity`, produce identical seed-0 people, sessions and
notification traces to before phase 2.

## Registered generators

`activity.shifts`/`activity.trips` accept a third generator kind,
`registered`, and `population.riders`/`population.drivers` gain a
`generator` `Choice` (`{"kind": "segments"}`, the default, or
`{"kind": "registered", "implementation": "name@version", "parameters": {...}}`)
alongside `count`/`segments`/`people`. Both select a trusted implementation
by identity through a registry in `scenario.py` --
`register_generator(family, implementation)` / `generator_class(family, id)`
-- that mirrors `policy_runtime.register_policy`/`policy_class`'s shape,
identity (`name@version`) and duplicate-registration rule, but lives in
`scenario.py` rather than `policy_runtime.py`: generators are compile/prepare-time
artifacts with no engine access, so `POLICY_FAMILIES`' hook/parameter-schema
contract does not apply to them, and their own `parameters` are an open
`Table(Json())` bag validated by the generator itself, not by this schema.
Registering computes `hashlib.sha256(inspect.getsource(implementation))` once,
at registration time; a generator whose source `inspect.getsource` cannot
retrieve (e.g. one defined at an interactive prompt) cannot be registered,
because there would then be nothing to hash for provenance.

Call contracts, invoked with a seed-derived `random.Random` distinct per
family and identity (`derive_seed(seed, ['activity', family, identifier])`
for trips/shifts, `derive_seed(seed, ['population', role, identifier])` for
population) so swapping a generator cannot silently reuse another's stream:

* **trips/shifts**: `generate(*, definition, people, parameters, random) ->
  list[dict]`. `definition` is a deep copy of `plan.resolved`, `people` a
  deep copy of the realized population; mutating either cannot reach the
  plan. Every returned item is validated by `_check_session` (exactly the
  session's documented key set -- trips `{kind, id, rider, at_seconds,
  origin, destination}`, shifts `{kind, id, driver, at_seconds,
  shift_seconds, location}`; a known person of the right role; finite
  times; JSON-serializable coordinates) with ids required unique only
  within this generator's own batch (the same guarantee `explicit` items
  get from `Keyed.check`). The produced sessions then join the built-in
  generators' output before the existing sort and
  `_check_realized_conflicts`, so they are covered by the same static
  conflict checks unconditionally.
* **population**: `generate(*, definition, role, person_ids, parameters,
  random) -> {person_id: declaration}`. `count` still fixes the identities
  (`role-1..role-n` in `person_ids`); the generator supplies each counted
  id's declaration rather than inventing identities of its own -- this is
  what keeps every compile-time cross-check that already validated against
  `plan.person_ids` (explicit activity references, preference
  interventions, `Inputs.reuse_for`'s controls) enforceable. The returned
  mapping's keys must be exactly `person_ids`, reported as separate missing
  and unexpected lists on a mismatch. Each declaration is checked with the
  same partial `segment_fields(role, explicit=True)` schema an explicit
  person uses, merged with its named `segment` (if any) through the same
  `_merge_person` an explicit person goes through, and validated with the
  same `_check_access` -- so a generated person has the identical shape and
  downstream validation as a segment-allocated or explicit one.

The manifest records identity and provenance for whichever generators are
`registered`: `manifest()['generators']` (and `Plan.generators`) is
`{'trips': {...}, 'shifts': {...}, 'population.rider': {...},
'population.driver': {...}}`, each present entry
`{'implementation': 'name@version', 'source_sha256': '...'}`. This dict
folds into `implementation_fingerprint`, so `fingerprints['implementation']`
and `fingerprints['plan']` change with the generator's identity *and* its
recorded source hash, exactly like a swapped policy implementation.
Reproducing a saved plan's fingerprint from `Scenario.load(manifest)`
therefore requires the same generator source to be registered in the
loading process -- the recorded hash detects that the source has drifted
since registration, not that the generator is pure. A registered generator
is trusted extension code exactly like a custom policy (see "Extensibility
and limits of validation" below): one that reads the clock, a global, or an
unseeded `random` module call can silently break `Inputs.reuse_for` and
paired variants without its source, or its recorded hash, changing at all.
An unknown selected identifier is a compile error
(`activity.trips.implementation` / `activity.shifts.implementation` /
`population.<role>s.generator.implementation`); a `registered` trips
generator with no riders, or a `registered` population generator with
`count == 0`, warns exactly like the built-in generators' equivalent
no-op cases.

`Inputs.explicit(plan, seed=0, *, people=None, sessions=None)` is the direct
construction path for a throwaway fixture: a short script that wants to
schedule a few trips or people without authoring a definition (this is the
pre-PR-4 imperative style, restored on top of a compiled plan). Omitted
`people`/`sessions` fall back to the plan's own `_realize_population`/
`_realize_activity`; supplied ones are `person()`-shaped declarations (each
needing a `role` key) or trip/shift dicts, validated through the identical
paths a registered generator's output takes (`_merge_person`/`_check_access`/
`_realize_person` for people; `_check_session`, the sort, and
`_check_realized_conflicts` for sessions) so a fixture's inputs have the
same shape and the same static guarantees as a fully authored definition's.
There is no new `Inputs` field -- `to_dict()` and every snapshot's `inputs`
blob keep their shape -- and no opt-out flag on `reuse_for`: preparing any
*other* plan from these inputs' seed produces that plan's own generated
people/sessions, which will not equal what was supplied here, so
`reuse_for`'s existing "controlled schedules diverged despite equal
controls" error already refuses the mismatch.

`world.zones` and `population.<role>.generator` are required keys, so a
JSON definition or saved manifest from before phase 2 fails `Scenario.load`
with `world.zones: missing required setting` (and the same for
`population.riders.generator` / `population.drivers.generator`); add
`"zones": {}` and `"generator": {"kind": "segments"}` to migrate it, the
same precedent `notes` set in phase 1. `world`/`population`/`activity` all
remain inside `controls` (`compile_scenario` builds `controls` from exactly
those three sections), so `fingerprints['controls']`/`['definition']` change
for every scenario with this phase's schema addition -- expected, since
geometry and a generator's identity genuinely are controlled inputs: they
change realized sessions, so two variants that differ only in zones or a
generator selection must not share prepared inputs through `Inputs.reuse_for`.

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
`map_km` on purpose; the same is true of `world.zones` (see "Geography" above)
-- a declared zone shapes only what a generator can reference, never what the
engine accepts. `population.<role>.segments`/`people` never carry *trip
identity or realized coordinates* -- `origin`/`destination` themselves are
still exogenous per-trip values that only `activity.trips` (built-in or
registered) produces -- but, since phase 2, a segment or explicit person
**may** carry an `activity` geometry *preference* (`origin_zones`,
`destination_zones`, `distance_km`, `spatial_peaks`, `start_zone`) that
biases where the `weekly`/`rotation` generators draw from; a segment still
never authors a coordinate directly. Replacing `platforms.<id>` (a full
`Table` entry, via `with_changes` or `platform()`)
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
