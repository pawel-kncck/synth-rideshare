"""Scenario definitions: versioned presets, typed overrides, resolution, compilation and prepared inputs.

A scenario describes a world and the models acting in it. It never drives rides
or mutates live engine objects. Authoring produces a definition; `compile_scenario`
resolves the preset and overrides against the declared schema, validates every
reference and range, binds policy implementations, and returns an immutable
`Plan`. `Plan.prepare(seed)` realizes the population and exogenous activity for
one replication as explicit input artifacts; `main.Simulation` instantiates fresh
mutable state from them. The design lives in
plans/architecture/scenario-definition.md.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import random
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

from behavior_policy import DriverTraits, EvolutionTraits, PersonProfile, RiderTraits
from marketplace_engine import World
from marketplace_policy import MarketplaceParameters, PlatformPolicy, VISIBLE_FIELDS
from policy_contracts import plain
from policy_runtime import BUILTIN_IMPLEMENTATIONS, POLICY_REGISTRY, policy_class

SCHEMA_VERSION = 1
COMPILER_VERSION = 1
SEED_DERIVATION_VERSION = 1
HOUR = 3600
SOURCE_FILES = ('main.py', 'scenario.py', 'marketplace_engine.py', 'event_engine.py', 'behavior_policy.py',
                'marketplace_policy.py', 'policy_runtime.py', 'policy_contracts.py')
ROLES = ('rider', 'driver')
TRAITS = {'rider': RiderTraits, 'driver': DriverTraits, 'evolution': EvolutionTraits}
DISTRIBUTIONS = ('uniform', 'normal', 'choice')
INTERVENTION_ORDER = {'launch': 0, 'regulation': 1, 'policy': 2, 'preference': 3}


class ScenarioError(ValueError):
    """One or more configuration errors, each naming the exact field."""


class Diagnostics:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, path, message):
        self.errors.append(f'{path}: {message}')

    def warn(self, path, message):
        self.warnings.append(f'{path}: {message}')

    def raise_errors(self):
        if self.errors:
            raise ScenarioError('\n'.join(self.errors))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def derive_seed(seed, purpose):
    """Deterministic purpose-specific seed, independent of Python's randomized hash()."""
    key = canonical([SEED_DERIVATION_VERSION, seed, purpose])
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big')


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def join(path, key):
    return f'{path}.{key}' if path else str(key)


# ----------------------------------------------------------------------
# Registered generators: trips, shifts and population, identified and versioned like policies
# (register_policy/policy_class in policy_runtime.py) but registered here -- generators are
# compile/prepare-time artifacts with no engine access, so POLICY_FAMILIES' hook/parameter-schema
# contract does not apply to them. Call contracts (`definition`/`people`/`parameters`/`random` in,
# a session-dict list or a {person_id: declaration} mapping out) are documented in
# plans/architecture/scenario-definition.md and re-validated at prepare time by _check_session
# and the population-generator path in _realize_population; compile time only proves the selected
# identifier is registered (mirroring Implementation's policy-identifier check).
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratorDeclaration:
    """A registered generator's identity (`name@version`) and family; `inputs`/`outputs` are free-form
    documentation, not validated against anything."""
    name: str
    version: str
    family: str
    inputs: tuple = ()
    outputs: tuple = ()


GENERATOR_FAMILIES = ('trips', 'shifts', 'population')
GENERATOR_REGISTRY = {family: {} for family in GENERATOR_FAMILIES}
GENERATOR_SOURCES = {family: {} for family in GENERATOR_FAMILIES}


def generator_id(implementation):
    declaration = implementation.declaration
    return f'{declaration.name}@{declaration.version}'


def register_generator(family, implementation):
    """Register a trusted generator under its declared name and version, mirroring `register_policy`.

    The source is hashed with `inspect.getsource` right here, at registration
    time, not invented later when a manifest is written -- so the recorded
    hash always reflects what actually ran. A generator whose source cannot
    be retrieved (`inspect.getsource` raising `OSError`, e.g. one defined at
    an interactive prompt) cannot be registered, because there would be
    nothing to hash for provenance. The hash detects source drift, not
    impurity: a generator that reads the clock, a global, or an unseeded
    `random` module call can still silently break `Inputs.reuse_for` and
    paired variants without changing its source at all.
    """
    if family not in GENERATOR_FAMILIES:
        raise ValueError(f'Unknown generator family {family!r}')
    declaration = getattr(implementation, 'declaration', None)
    if (declaration is None or not isinstance(declaration.name, str) or not declaration.name
            or not isinstance(declaration.version, str) or not declaration.version):
        raise ValueError(f'{family} generator needs a GeneratorDeclaration with a name and version')
    if declaration.family != family:
        raise ValueError(f'{family} generator declaration must declare family {family!r}, got {declaration.family!r}')
    if not callable(getattr(implementation, 'generate', None)):
        raise ValueError(f'{family} generator must implement a callable generate(...)')
    identifier = generator_id(implementation)
    existing = GENERATOR_REGISTRY[family].get(identifier)
    if existing is not None and existing is not implementation:
        raise ValueError(f'{family} generator {identifier} is already registered')
    try:
        source = inspect.getsource(implementation)
    except OSError:
        raise ValueError(f'{family} generator {identifier} needs retrievable source for provenance') from None
    GENERATOR_REGISTRY[family][identifier] = implementation
    GENERATOR_SOURCES[family][identifier] = hashlib.sha256(source.encode()).hexdigest()
    return identifier


def generator_class(family, identifier):
    try:
        return GENERATOR_REGISTRY[family][identifier]
    except KeyError:
        known = ', '.join(sorted(GENERATOR_REGISTRY.get(family, {}))) or 'none'
        raise ValueError(f'Unknown {family} generator {identifier!r}; registered: {known}') from None


# ----------------------------------------------------------------------
# Schema: every definition value has a declared kind and merge rule
# ----------------------------------------------------------------------

class Spec:
    """A schema node. Values are JSON-shaped; specs validate, merge and enumerate leaves."""
    nullable = False

    def check(self, value, path, diag, complete):
        raise NotImplementedError

    def merge(self, base, change):
        return change  # replace by default

    def leaves(self, value, path):
        yield path, value

    def descend(self, value, key, path):
        raise ScenarioError(f'{path}: cannot descend into {key!r}')


class Scalar(Spec):
    def __init__(self, kind, *, minimum=None, maximum=None, positive=False, choices=None, nullable=False):
        self.kind, self.minimum, self.maximum, self.positive = kind, minimum, maximum, positive
        self.choices, self.nullable = choices, nullable

    def check(self, value, path, diag, complete):
        if value is None:
            if not self.nullable:
                diag.error(path, 'null is not accepted here')
            return None
        ok = {'number': _is_number, 'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
              'string': lambda v: isinstance(v, str) and bool(v), 'boolean': lambda v: isinstance(v, bool),
              'value': lambda v: isinstance(v, (str, bool, int)) and (not isinstance(v, str) or v),
              'any': lambda v: isinstance(v, (str, bool, dict)) or _is_number(v)}[self.kind](value)
        if not ok:
            diag.error(path, f'expected {self.kind}, got {value!r}')
            return value
        if self.choices is not None and value not in self.choices:
            diag.error(path, f'expected one of {list(self.choices)}, got {value!r}')
        if self.kind in ('number', 'integer'):
            if self.minimum is not None and value < self.minimum:
                diag.error(path, f'must be at least {self.minimum}, got {value!r}')
            if self.maximum is not None and value > self.maximum:
                diag.error(path, f'must be at most {self.maximum}, got {value!r}')
            if self.positive and value <= 0:
                diag.error(path, f'must be positive, got {value!r}')
        return value


class Point(Spec):
    def __init__(self, positive=False):
        self.positive = positive

    def check(self, value, path, diag, complete):
        if (not isinstance(value, (list, tuple)) or len(value) != 2 or not all(_is_number(v) for v in value)
                or (self.positive and any(v <= 0 for v in value))):
            diag.error(path, f'expected an (x, y) pair of finite{" positive" if self.positive else ""} kilometres, got {value!r}')
            return value
        return [float(value[0]), float(value[1])]


class Seq(Spec):
    """A list replaced as a whole on override."""

    def __init__(self, item, nullable=False):
        self.item, self.nullable = item, nullable

    def check(self, value, path, diag, complete):
        if value is None:
            if not self.nullable:
                diag.error(path, 'null is not accepted here')
            return None
        if not isinstance(value, (list, tuple)):
            diag.error(path, f'expected a list, got {value!r}')
            return value
        return [self.item.check(v, f'{path}[{i}]', diag, complete) for i, v in enumerate(value)]

    def leaves(self, value, path):
        yield path, list(value) if isinstance(value, (list, tuple)) else value


class Map(Spec):
    """Fixed keys merged by schema. A complete map (presets, resolved definitions) has every key."""

    def __init__(self, fields, *, nullable=False, partial=False):
        self.fields, self.nullable, self.partial = fields, nullable, partial

    def check(self, value, path, diag, complete):
        if value is None:
            if not self.nullable:
                diag.error(path, 'null is not accepted here')
            return None
        if not isinstance(value, dict):
            diag.error(path, f'expected a mapping, got {value!r}')
            return value
        for key in value:
            if key not in self.fields:
                diag.error(join(path, key), f'unknown key; expected one of {sorted(self.fields)}')
        result = {}
        for key, spec in self.fields.items():
            if key in value:
                result[key] = spec.check(value[key], join(path, key), diag, complete and not self.partial)
            elif complete and not self.partial:
                diag.error(join(path, key), 'missing required setting')
        return result

    def merge(self, base, change):
        if base is None or change is None or not isinstance(base, dict) or not isinstance(change, dict):
            return change
        merged = dict(base)
        for key, value in change.items():
            merged[key] = self.fields[key].merge(base.get(key), value) if key in self.fields else value
        return merged

    def leaves(self, value, path):
        if not isinstance(value, dict):
            yield path, value
            return
        for key, spec in self.fields.items():
            if key in value:
                yield from spec.leaves(value[key], join(path, key))

    def descend(self, value, key, path):
        if key not in self.fields:
            raise ScenarioError(f'{join(path, key)}: unknown key; expected one of {sorted(self.fields)}')
        return self.fields[key], (value or {}).get(key)


class Table(Spec):
    """A mapping keyed by user-declared stable IDs; entries merge by ID."""

    def __init__(self, value):
        self.value = value

    def check(self, value, path, diag, complete):
        if not isinstance(value, dict):
            diag.error(path, f'expected a mapping keyed by ID, got {value!r}')
            return value
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or '.' in key:
                diag.error(join(path, key), 'IDs must be nonempty strings without dots')
            result[key] = self.value.check(item, join(path, key), diag, complete)
        return result

    def merge(self, base, change):
        if not isinstance(base, dict) or not isinstance(change, dict):
            return change
        merged = dict(base)
        for key, value in change.items():
            merged[key] = self.value.merge(base.get(key), value)
        return merged

    def leaves(self, value, path):
        for key, item in value.items():
            yield from self.value.leaves(item, join(path, key))

    def descend(self, value, key, path):
        return self.value, (value or {}).get(key)


class Keyed(Spec):
    """A list of items with unique `id`. Overrides replace it; `add`/`remove` edit by ID."""

    def __init__(self, item):
        self.item = item

    def check(self, value, path, diag, complete):
        if not isinstance(value, (list, tuple)):
            diag.error(path, f'expected a list of items with IDs, got {value!r}')
            return value
        seen, result = set(), []
        for index, item in enumerate(value):
            identity = item.get('id') if isinstance(item, dict) else None
            label = join(path, identity if isinstance(identity, str) and identity else f'[{index}]')
            if not isinstance(identity, str) or not identity or '.' in identity:
                diag.error(label, 'items need a nonempty string id without dots')
            elif identity in seen:
                diag.error(label, 'duplicate id')
            seen.add(identity)
            result.append(self.item.check(item, label, diag, complete))
        return result

    def leaves(self, value, path):
        for index, item in enumerate(value):
            identity = item.get('id') if isinstance(item, dict) else None
            yield from self.item.leaves(item, join(path, identity or f'[{index}]'))

    def descend(self, value, key, path):
        for item in value or ():
            if isinstance(item, dict) and item.get('id') == key:
                return self.item, item
        raise ScenarioError(f'{join(path, key)}: no item with this id; use add() to insert one')

    def replace(self, value, key, item):
        return [item if isinstance(existing, dict) and existing.get('id') == key else existing for existing in value]


class Choice(Spec):
    """A map whose allowed keys depend on a discriminator. Changing it replaces the whole value."""

    def __init__(self, field, options, partial=False):
        self.field, self.options, self.partial = field, options, partial
        for name, option in options.items():
            option.fields[field] = Scalar('string', choices=(name,))
            option.partial = partial

    def option(self, value, path, diag):
        if not isinstance(value, dict):
            diag.error(path, f'expected a mapping with {self.field!r}, got {value!r}')
            return None
        name = value.get(self.field)
        if name not in self.options:
            diag.error(join(path, self.field), f'expected one of {sorted(self.options)}, got {name!r}')
            return None
        return self.options[name]

    def check(self, value, path, diag, complete):
        option = self.option(value, path, diag)
        return value if option is None else option.check(value, path, diag, complete)

    def merge(self, base, change):
        if (not isinstance(base, dict) or not isinstance(change, dict)
                or change.get(self.field, base.get(self.field)) != base.get(self.field)):
            return change
        return self.options[base[self.field]].merge(base, change)

    def leaves(self, value, path):
        option = self.options.get(value.get(self.field)) if isinstance(value, dict) else None
        if option is None:
            yield path, value
        else:
            yield from option.leaves(value, path)

    def descend(self, value, key, path):
        option = self.options.get((value or {}).get(self.field))
        if option is None:
            raise ScenarioError(f'{path}: set {self.field!r} before changing its settings')
        return option.descend(value, key, path)


class Params(Spec):
    """Parameters of a dataclass schema. Sampled maps may hold distribution specs per trait."""

    def __init__(self, schema, *, sampled=False, partial=False):
        self.schema, self.sampled, self.partial = schema, sampled, partial
        self.names = tuple(f.name for f in fields(schema))

    def check(self, value, path, diag, complete):
        if not isinstance(value, dict):
            diag.error(path, f'expected a mapping of {self.schema.__name__} parameters, got {value!r}')
            return value
        for key in value:
            if key not in self.names:
                diag.error(join(path, key), f'unknown {self.schema.__name__} parameter')
        complete = complete and not self.partial
        if complete:
            for key in self.names:
                if key not in value:
                    diag.error(join(path, key), f'missing required {self.schema.__name__} parameter')
        scalars = {}
        for key, item in value.items():
            if key not in self.names:
                continue
            if isinstance(item, dict):
                if not self.sampled:
                    diag.error(join(path, key), 'distributions are only accepted in population segments')
                else:
                    self.check_distribution(item, join(path, key), diag)
            else:
                scalars[key] = item
        # Validate scalar values under the schema's own rules; a partial map is
        # validated after merging with its defaults, see the compiler.
        if complete and len(scalars) == len(self.names):
            try:
                self.schema(**scalars)
            except (TypeError, ValueError) as error:
                diag.error(path, str(error))
        return dict(value)

    @staticmethod
    def check_distribution(item, path, diag):
        if len(item) != 1 or next(iter(item)) not in DISTRIBUTIONS:
            diag.error(path, f'a distribution is one of {list(DISTRIBUTIONS)} with its arguments')
            return
        name, args = next(iter(item.items()))
        if name == 'uniform' and not (isinstance(args, (list, tuple)) and len(args) == 2
                                      and all(_is_number(v) for v in args) and args[0] <= args[1]):
            diag.error(path, 'uniform needs [low, high] with low <= high')
        elif name == 'normal' and not (isinstance(args, (list, tuple)) and len(args) == 4
                                       and all(_is_number(v) for v in args) and args[1] >= 0 and args[2] <= args[3]):
            diag.error(path, 'normal needs [mean, sd, low, high] with sd >= 0 and low <= high')
        elif name == 'choice' and not (isinstance(args, (list, tuple)) and args
                                       and all(isinstance(pair, (list, tuple)) and len(pair) == 2
                                               and _is_number(pair[1]) and pair[1] >= 0 for pair in args)
                                       and sum(pair[1] for pair in args) > 0):
            diag.error(path, 'choice needs [[value, weight], ...] with nonnegative weights')

    def merge(self, base, change):
        return {**base, **change} if isinstance(base, dict) and isinstance(change, dict) else change

    def leaves(self, value, path):
        for key in self.names:
            if key in value:
                yield join(path, key), value[key]

    def descend(self, value, key, path):
        if key not in self.names:
            raise ScenarioError(f'{join(path, key)}: unknown {self.schema.__name__} parameter')
        return Scalar('any'), (value or {}).get(key)


class Distribution(Spec):
    """One sampled distribution (`uniform`/`normal`/`choice`) outside a `Params` map, e.g. `activity.*.distance_km`.

    Reuses `Params.check_distribution` so there is exactly one implementation
    of the distribution-shape rule. The default (replace) `Spec.merge` is
    correct here -- a variant that names a new `distance_km` means exactly
    that distribution, not a merge with the base's.
    """
    nullable = True

    def check(self, value, path, diag, complete):
        if value is None:
            return None
        if not isinstance(value, dict):
            diag.error(path, f'expected a distribution, got {value!r}')
            return value
        Params.check_distribution(value, path, diag)
        return dict(value)


class Json(Spec):
    """Any JSON-serializable value; its contents are a registered generator's own contract, not this schema's."""

    def check(self, value, path, diag, complete):
        try:
            canonical(value)
        except (TypeError, ValueError):
            diag.error(path, 'must be a JSON-serializable value')
        return copy.deepcopy(value)


class Implementation(Spec):
    """A registered policy selection. Its parameter schema comes from the implementation's declaration."""

    def __init__(self, family, extra=None, partial=False):
        self.family, self.extra, self.partial = family, dict(extra or {}), partial

    def schema_for(self, identifier):
        schema = policy_class(self.family, identifier).declaration.parameter_schema
        if self.family == 'marketplace':
            # The marketplace declaration binds the whole PlatformPolicy; its tariff
            # and dispatch parameters are the MarketplaceParameters component.
            schema = MarketplaceParameters
        return Params(schema)

    def option(self, value, path, diag):
        if not isinstance(value, dict):
            diag.error(path, f'expected a mapping with "implementation", got {value!r}')
            return None
        identifier = value.get('implementation')
        if identifier not in POLICY_REGISTRY[self.family]:
            diag.error(join(path, 'implementation'),
                       f'unknown {self.family} policy {identifier!r}; registered: {sorted(POLICY_REGISTRY[self.family])}')
            return None
        return Map({'implementation': Scalar('string'), 'parameters': self.schema_for(identifier), **self.extra},
                   partial=self.partial)

    def check(self, value, path, diag, complete):
        option = self.option(value, path, diag)
        return value if option is None else option.check(value, path, diag, complete)

    def merge(self, base, change):
        if (not isinstance(base, dict) or not isinstance(change, dict)
                or change.get('implementation', base.get('implementation')) != base.get('implementation')):
            return change  # a replaced implementation validates its own parameters from scratch
        return self.option(base, '', Diagnostics()).merge(base, change)

    def leaves(self, value, path):
        option = self.option(value, path, Diagnostics()) if isinstance(value, dict) else None
        if option is None:
            yield path, value
        else:
            yield from option.leaves(value, path)

    def descend(self, value, key, path):
        option = self.option(value, path, Diagnostics()) if isinstance(value, dict) else None
        if option is None:
            raise ScenarioError(f'{path}: select a registered implementation before changing its settings')
        return option.descend(value, key, path)


APPS = Seq(Scalar('string'))
NONNEGATIVE_HOURS = Scalar('number', minimum=0)
POSITIVE_HOURS = Scalar('number', positive=True)

# Per-segment geometry for the weekly/rotation generators (plan section 4.F). Both are
# `partial=True`: an absent key is the default-off state, so a segment that names none of
# this keeps its `activity: {}` and realization takes the unchanged `_sample_point` path.
SPATIAL_PEAK = Map({'id': Scalar('string'), 'zones': Seq(Scalar('string')),
                    'weekdays': Seq(Scalar('integer', minimum=0, maximum=6)),
                    'start_hour': Scalar('integer', minimum=0, maximum=23),
                    'end_hour': Scalar('integer', minimum=0, maximum=24),
                    'multiplier': Scalar('number', minimum=0)})
RIDER_ACTIVITY = Map({'origin_zones': Table(Scalar('number', minimum=0)),
                      'destination_zones': Table(Table(Scalar('number', minimum=0))),
                      'distance_km': Distribution(),
                      'spatial_peaks': Keyed(SPATIAL_PEAK)}, partial=True)
DRIVER_ACTIVITY = Map({'start_zone': Scalar('string', nullable=True)}, partial=True)


def segment_fields(role, *, explicit):
    fields_ = {
        'apps': APPS, 'preferred_app': Scalar('string'), 'accounts': Seq(Scalar('string'), nullable=True),
        'awareness': APPS, 'disclosed_to': APPS,
        'initial_scores': Table(Scalar('number', minimum=-2, maximum=2)),
        role: Params(TRAITS[role], sampled=not explicit, partial=True),
        'evolution': Params(EvolutionTraits, sampled=not explicit, partial=True),
        'activity': RIDER_ACTIVITY if role == 'rider' else DRIVER_ACTIVITY,
    }
    if role == 'driver':
        fields_['registrations'] = Seq(Scalar('string'), nullable=True)
    if explicit:
        return {'id': Scalar('string'), 'segment': Scalar('string', nullable=True), **fields_}
    return {'weight': Scalar('number', minimum=0, maximum=1), **fields_}


def population_spec(role):
    return Map({'count': Scalar('integer', minimum=0),
                'segments': Table(Map(segment_fields(role, explicit=False))),
                'people': Keyed(Map(segment_fields(role, explicit=True), partial=True)),
                'generator': Choice('kind', {'segments': Map({}), 'registered': registered_generator()})})


CAMPAIGN = Map({
    'id': Scalar('string'), 'start_hours': NONNEGATIVE_HOURS, 'end_hours': POSITIVE_HOURS,
    'discount_minor': Scalar('integer', minimum=0), 'discount_fraction': Scalar('number', minimum=0, maximum=1),
    'discount_cap_minor': Scalar('integer', minimum=0, nullable=True), 'bonus_minor': Scalar('integer', minimum=0),
    'segment': Scalar('string', nullable=True), 'new_user_only': Scalar('boolean'),
    'awareness': Scalar('string', choices=('announced', 'in_app')),
    'budget_minor': Scalar('integer', minimum=0, nullable=True),
    'max_completed_rides': Scalar('integer', minimum=1, nullable=True),
})
RULE = Map({'id': Scalar('string'), 'priority': Scalar('integer'), 'when': Table(Scalar('value')),
            'parameters': Params(MarketplaceParameters, partial=True),
            'start_hours': Scalar('number', minimum=0, nullable=True),
            'end_hours': Scalar('number', positive=True, nullable=True)})
PROGRAM = Map({'id': Scalar('string'), 'kind': Scalar('string', choices=('hourly_guarantee',)),
              'floor_minor': Scalar('integer', minimum=0), 'window_hours': POSITIVE_HOURS,
              'min_acceptance_rate': Scalar('number', minimum=0, maximum=1),
              'min_online_hours': Scalar('number', minimum=0), 'zero_dispatch_qualifies': Scalar('boolean')})
POLICY_EXTRA = {'version': Scalar('string'), 'rules': Keyed(RULE), 'campaigns': Keyed(CAMPAIGN),
                'fallback': Scalar('string'), 'programs': Keyed(PROGRAM)}
PLATFORM = Map({
    'launched': Scalar('boolean'),
    'policy': Implementation('marketplace', POLICY_EXTRA),
    'controller': Map({'interval_hours': POSITIVE_HOURS, 'until_hours': POSITIVE_HOURS}, nullable=True),
    # Seeds the platform's tracked cash account (marketplace_engine.Account); null (every
    # preset's default) means untracked -- flows still post, there is just no balance to report.
    'starting_cash_minor': Scalar('integer', minimum=0, nullable=True),
})
PEAK = Map({'id': Scalar('string'), 'weekdays': Seq(Scalar('integer', minimum=0, maximum=6)),
            'start_hour': Scalar('integer', minimum=0, maximum=23), 'end_hour': Scalar('integer', minimum=0, maximum=24),
            'multiplier': Scalar('number', minimum=1)})
ZONE = Map({'min': Point(), 'max': Point()})  # closed axis-aligned box in km; map_km stays a sampling extent, not a fence


def registered_generator():
    """A `{'implementation': 'name@version', 'parameters': {...}}` option, as a FACTORY.

    `Choice.__init__` mutates each option `Map` it is given (writing the
    discriminator field and `partial` flag onto it in place), so one shared
    instance reused across `SHIFTS`, `TRIPS` and the two population
    `Choice`s would alias all four -- this must be called fresh every time.
    `parameters` is `Table(Json())`, an open bag: unlike a policy's
    parameter schema, a generator's own contract is not known until it is
    selected, so it is validated by the generator itself at prepare time,
    not by this schema.
    """
    return Map({'implementation': Scalar('string'), 'parameters': Table(Json())})


SHIFTS = Choice('generator', {
    'rotation': Map({'crews': Scalar('integer', minimum=1), 'shift_hours': POSITIVE_HOURS,
                     'days': Scalar('integer', minimum=1, nullable=True), 'first_start_hours': NONNEGATIVE_HOURS}),
    'explicit': Map({'items': Keyed(Map({'id': Scalar('string'), 'driver': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS,
                                         'hours': Scalar('number', positive=True, nullable=True), 'location': Point()}))}),
    'registered': registered_generator(),
    'none': Map({}),
})
TRIPS = Choice('generator', {
    'weekly': Map({'trips_per_rider': Scalar('number', positive=True), 'duration_hours': Scalar('number', positive=True, nullable=True),
                   'peaks': Keyed(PEAK), 'off_peak_weight': Scalar('number', positive=True),
                   'assignment': Scalar('string', choices=('round_robin', 'random'))}),
    'explicit': Map({'items': Keyed(Map({'id': Scalar('string'), 'rider': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS,
                                         'origin': Point(), 'destination': Point()}))}),
    'registered': registered_generator(),
    'none': Map({}),
})
INTERVENTION = Choice('kind', {
    'launch': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS, 'platform': Scalar('string')}),
    'policy': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS, 'platform': Scalar('string'),
                   'policy': Implementation('marketplace', POLICY_EXTRA, partial=True)}),
    'preference': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS, 'role': Scalar('string', choices=ROLES),
                       'segment': Scalar('string', nullable=True), 'people': APPS, 'preferred_app': Scalar('string')}),
    'regulation': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS,
                       'max_base_fare_minor': Scalar('integer', minimum=0, nullable=True),
                       'max_per_km_minor': Scalar('integer', minimum=0, nullable=True),
                       'max_commission_fraction': Scalar('number', minimum=0, maximum=1, nullable=True)}),
})
SCENARIO = Map({
    'name': Scalar('string'),
    'schema_version': Scalar('integer', choices=(SCHEMA_VERSION,)),
    'preset': Scalar('string', nullable=True),
    'calibration': Scalar('string'),
    'notes': Table(Scalar('string')),
    'world': Map({
        'speed_kmh': Scalar('number', positive=True), 'boarding_seconds': Scalar('number', minimum=0),
        'minor_units_per_major': Scalar('integer', minimum=1), 'map_km': Point(positive=True),
        'sampling': Scalar('string', choices=('grid', 'continuous')), 'grid_step_km': Scalar('number', positive=True),
        'zones': Table(ZONE),
        'calendar': Map({'weekday': Scalar('integer', minimum=0, maximum=6), 'hour': Scalar('number', minimum=0, maximum=24)}),
        'horizon_hours': POSITIVE_HOURS,
    }),
    'platforms': Table(PLATFORM),
    'behavior': Map({'rider': Implementation('rider'), 'driver': Implementation('driver'),
                     'evolution': Implementation('evolution')}),
    'population': Map({'riders': population_spec('rider'), 'drivers': population_spec('driver')}),
    'activity': Map({'shifts': SHIFTS, 'trips': TRIPS, 'conflicts': Scalar('string', choices=('fail',))}),
    'evolution': Map({'checkpoint_hours': Scalar('number', positive=True, nullable=True),
                      'first_checkpoint_hours': Scalar('number', positive=True, nullable=True)}),
    'interventions': Keyed(INTERVENTION),
})


# ----------------------------------------------------------------------
# Typed builders: complete items for keyed collections
# ----------------------------------------------------------------------

def _build(spec, value, label):
    diag = Diagnostics()
    checked = spec.check(value, label, diag, complete=True)
    diag.raise_errors()
    return checked


def platform_policy(*, version='modern-v1', parameters=None, rules=(), campaigns=(), fallback='default',
                    implementation=BUILTIN_IMPLEMENTATIONS['marketplace']):
    """A partial policy definition; unspecified parameters inherit from the platform or preset."""
    return {'implementation': implementation, 'version': version, 'parameters': dict(parameters or {}),
            'rules': list(rules), 'campaigns': list(campaigns), 'fallback': fallback}


def campaign(id, *, start_hours, end_hours, discount_minor=0, discount_fraction=0, discount_cap_minor=None,
             bonus_minor=0, segment=None, new_user_only=False, awareness='announced', budget_minor=None,
             max_completed_rides=None):
    return _build(CAMPAIGN, {'id': id, 'start_hours': start_hours, 'end_hours': end_hours, 'discount_minor': discount_minor,
                             'discount_fraction': discount_fraction, 'discount_cap_minor': discount_cap_minor,
                             'bonus_minor': bonus_minor, 'segment': segment, 'new_user_only': new_user_only,
                             'awareness': awareness, 'budget_minor': budget_minor,
                             'max_completed_rides': max_completed_rides}, f'campaign {id}')


def rule(id, *, priority, when, parameters, start_hours=None, end_hours=None):
    """A conditional policy override, validated against today's VISIBLE_FIELDS at authoring time.

    `ConditionalRule.__post_init__` enforces the same conditions at
    `compile_scenario` time; checking them here surfaces the offending
    field names immediately instead of after a full scenario resolves. The
    visible-field set is `marketplace_policy.VISIBLE_FIELDS`, which phase 4
    extended with `origin_zone`/`destination_zone`; `rule()` reads the set
    by reference rather than duplicating it, so it always tracks that set.
    `start_hours`/`end_hours` (both or neither) restrict the rule to a
    half-open `[start_hours, end_hours)` window, converted to seconds by
    `_compile_policy` exactly like a campaign's window.
    """
    checked = _build(RULE, {'id': id, 'priority': priority, 'when': dict(when), 'parameters': dict(parameters),
                            'start_hours': start_hours, 'end_hours': end_hours}, f'rule {id}')
    unknown = sorted(set(checked['when']) - VISIBLE_FIELDS)
    if not checked['when'] or unknown:
        raise ScenarioError(f'rule {id}.when: conditions must use declared platform-visible fields '
                            f'{sorted(VISIBLE_FIELDS)}' + (f'; got {unknown}' if unknown else '; got none'))
    unknown = sorted(set(checked['parameters']) - {f.name for f in fields(MarketplaceParameters)})
    if unknown:
        raise ScenarioError(f'rule {id}.parameters: unknown policy parameter(s) {unknown}')
    if (start_hours is None) != (end_hours is None):
        raise ScenarioError(f'rule {id}: start_hours and end_hours must be given together')
    return checked


def program(id, *, floor_minor, window_hours, min_acceptance_rate=0, min_online_hours=0,
           zero_dispatch_qualifies=True, kind='hourly_guarantee'):
    """One `hourly_guarantee` program: at window close, a qualifying driver is topped up to
    `floor_minor` minus their own completed payout in the window. `window_hours` must equal the
    platform's own `guarantee_window_seconds` (in hours) -- `PlatformPolicy.__post_init__` enforces it."""
    return _build(PROGRAM, {'id': id, 'kind': kind, 'floor_minor': floor_minor, 'window_hours': window_hours,
                            'min_acceptance_rate': min_acceptance_rate, 'min_online_hours': min_online_hours,
                            'zero_dispatch_qualifies': zero_dispatch_qualifies}, f'program {id}')


def regulation(id, *, at_hours, max_base_fare_minor=None, max_per_km_minor=None, max_commission_fraction=None):
    """A market-wide regulation intervention: engine-enforced and visible to every platform equally.
    `compile_scenario` requires at least one cap, with `max_base_fare_minor`/`max_per_km_minor` jointly
    set or absent."""
    return _build(INTERVENTION.options['regulation'], {'id': id, 'kind': 'regulation', 'at_hours': at_hours,
        'max_base_fare_minor': max_base_fare_minor, 'max_per_km_minor': max_per_km_minor,
        'max_commission_fraction': max_commission_fraction}, f'regulation {id}')


def peak(id, *, weekdays, start_hour, end_hour, multiplier):
    return _build(PEAK, {'id': id, 'weekdays': list(weekdays), 'start_hour': start_hour, 'end_hour': end_hour,
                         'multiplier': multiplier}, f'peak {id}')


def spatial_peak(id, *, zones, weekdays, start_hour, end_hour, multiplier):
    """One `activity.<role>.segments.<sid>.activity.spatial_peaks` item: a clock window that multiplies
    the listed zones' `origin_zones` weight for a rider whose segment declares one (origins only)."""
    return _build(SPATIAL_PEAK, {'id': id, 'zones': list(zones), 'weekdays': list(weekdays), 'start_hour': start_hour,
                                 'end_hour': end_hour, 'multiplier': multiplier}, f'spatial_peak {id}')


def segment(*, weight, apps, preferred_app, accounts=None, registrations=None, awareness=None, disclosed_to=(),
            initial_scores=None, rider=None, driver=None, evolution=None, activity=None):
    """A population segment: correlated access and trait bundle. Omit `registrations` for riders.

    `activity` is the segment's geometry declaration (`origin_zones`,
    `destination_zones`, `distance_km`, `spatial_peaks` for a rider segment;
    `start_zone` for a driver segment) -- omit it (the default) for the
    map-wide `_sample_point` behavior every segment had before phase 2.
    """
    value = {'weight': weight, 'apps': list(apps), 'preferred_app': preferred_app,
             'accounts': None if accounts is None else list(accounts),
             'awareness': list(apps if awareness is None else awareness), 'disclosed_to': list(disclosed_to),
             'initial_scores': dict(initial_scores or {}), 'evolution': dict(evolution or {}),
             'activity': dict(activity or {})}
    if driver is not None or registrations is not None:
        value.update({'driver': dict(driver or {}), 'registrations': None if registrations is None else list(registrations)})
    else:
        value['rider'] = dict(rider or {})
    return value


def person(id, *, segment=None, **overrides):
    """An explicit person. Values inherit from the named segment, then behavior defaults."""
    return {'id': id, 'segment': segment, **{key: (list(value) if isinstance(value, tuple) else value)
                                             for key, value in overrides.items()}}


def shift(id, *, driver, at_hours, hours, location):
    return {'id': id, 'driver': driver, 'at_hours': at_hours, 'hours': hours, 'location': list(location)}


def trip(id, *, rider, at_hours, origin, destination):
    return {'id': id, 'rider': rider, 'at_hours': at_hours, 'origin': list(origin), 'destination': list(destination)}


def launch(id, *, at_hours, platform):
    return {'id': id, 'kind': 'launch', 'at_hours': at_hours, 'platform': platform}


def policy_change(id, *, at_hours, platform, **policy):
    """Select a new commercial policy version at a simulated time; omitted settings stay as before."""
    return {'id': id, 'kind': 'policy', 'at_hours': at_hours, 'platform': platform,
            'policy': {'implementation': BUILTIN_IMPLEMENTATIONS['marketplace'], **policy}}


def preference_change(id, *, at_hours, role, preferred_app, segment=None, people=()):
    return {'id': id, 'kind': 'preference', 'at_hours': at_hours, 'role': role, 'segment': segment,
            'people': list(people), 'preferred_app': preferred_app}


# ----------------------------------------------------------------------
# Presets: complete, versioned, synthetic defaults
# ----------------------------------------------------------------------

# Every value is written out under its version. A later default change is a
# new preset version, never a changed meaning of an existing one.
RIDER_DEFAULTS_V1 = {
    'acceptable_price_ratio': 1.2, 'eta_tolerance_seconds': 300, 'price_sensitivity': 1, 'eta_sensitivity': .5,
    'loyalty': .3, 'search_cost': .1, 'outside_utility': 0, 'purchase_bias': 1, 'taste_scale': .25,
    'decision_seconds': 5, 'opening_seconds': 2, 'retry_seconds': 2, 'patience_seconds': 300,
    'cancellation_after_seconds': 600, 'max_app_visits': 3, 'max_quote_refreshes': 1, 'max_order_attempts': 3,
    'attempts_per_app': 1, 'reference_base_minor': 200, 'reference_per_km_minor': 150,
}
DRIVER_DEFAULTS_V1 = {
    'response_seconds': 3, 'no_offer_seconds': 60, 'further_opening_seconds': 30, 'expansion': 'multi_app',
    'after_service': 'retain', 'expand_while_busy': False, 'second_order_probability': 1, 'acceptance_bias': 1,
    'payout_sensitivity': 1, 'delay_sensitivity': .5, 'reference_payout_minor': 1000, 'reference_delay_seconds': 300,
    'loyalty': .2, 'max_private_pickup_seconds': 3600, 'cancel_after_seconds': 7200, 'shift_exit': 'drain',
}
EVOLUTION_DEFAULTS_V1 = {
    'adoption_rate_per_day': 0, 'adoption_friction': 0, 'learning_rate': 0, 'preference_margin': .1,
    'preference_cooldown_seconds': 86400, 'prior_offer_rate_per_second': 1 / 300, 'prior_exposure_seconds': 300,
    'onboard_car': False,
}
MARKETPLACE_DEFAULTS_V1 = {
    'base_fare_minor': 200, 'per_km_minor': 150, 'per_minute_minor': 0, 'minimum_fare_minor': 0, 'multiplier': 1,
    'commission_fraction': .2, 'estimated_speed_kmh': 30, 'estimated_boarding_seconds': 30, 'estimator': 'own_service',
    'matching': 'nearest', 'max_local_commitments': 2, 'back_to_back_within_seconds': 1800, 'max_attempts': 5,
    'retry_drivers': False, 'retry_seconds': 1, 'order_patience_seconds': 60, 'offer_seconds': 10, 'quote_seconds': 30,
    'rider_cancellation_fee_minor': 0, 'driver_cancellation_compensation_minor': 0,
    'driver_cancellation_penalty_minor': 0,
    'surcharge_minor': 0, 'surcharge_driver_share': 0, 'commission_binding': 'offer',
    'driver_lockout_seconds': 0, 'guarantee_window_seconds': 0, 'announce_terms': False, 'service_area': None,
}
ALL_APPS = ['rebu', 'blot', 'flyt']


def _platform_v1():
    return {'launched': True, 'controller': None, 'starting_cash_minor': None,
            'policy': {'implementation': 'marketplace@1', 'version': 'modern-v1',
                       'parameters': dict(MARKETPLACE_DEFAULTS_V1), 'rules': [], 'campaigns': [], 'programs': [],
                       'fallback': 'default'}}


def _segments_v1(role):
    """Mixed ownership: multi-app majorities with different first choices plus single-app users."""
    trait = 'rider' if role == 'rider' else 'driver'
    mix = [('rebu-first', .4, ALL_APPS, 'rebu'), ('blot-first', .25, ALL_APPS, 'blot'),
           ('flyt-first', .15, ALL_APPS, 'flyt'), ('rebu-only', .2, ['rebu'], 'rebu')]
    result = {}
    for name, weight, apps, preferred in mix:
        item = {'weight': weight, 'apps': list(apps), 'preferred_app': preferred, 'accounts': None,
                'awareness': list(ALL_APPS), 'disclosed_to': [], 'initial_scores': {}, trait: {}, 'evolution': {},
                'activity': {}}
        if role == 'driver':
            item['registrations'] = None
        result[name] = item
    return result


def _base_v1(name, *, horizon_hours, calendar):
    return {
        'name': name, 'schema_version': SCHEMA_VERSION, 'preset': None, 'calibration': 'synthetic', 'notes': {},
        'world': {'speed_kmh': 30, 'boarding_seconds': 30, 'minor_units_per_major': 100, 'map_km': [10, 10],
                  'sampling': 'grid', 'grid_step_km': 1, 'zones': {}, 'calendar': dict(calendar), 'horizon_hours': horizon_hours},
        'platforms': {app: _platform_v1() for app in ALL_APPS},
        'behavior': {'rider': {'implementation': 'rider_search@1', 'parameters': dict(RIDER_DEFAULTS_V1)},
                     'driver': {'implementation': 'driver_participation@1', 'parameters': dict(DRIVER_DEFAULTS_V1)},
                     'evolution': {'implementation': 'personal_evolution@1', 'parameters': dict(EVOLUTION_DEFAULTS_V1)}},
        'population': {'riders': {'count': 0, 'segments': _segments_v1('rider'), 'people': [], 'generator': {'kind': 'segments'}},
                       'drivers': {'count': 0, 'segments': _segments_v1('driver'), 'people': [], 'generator': {'kind': 'segments'}}},
        'activity': {'shifts': {'generator': 'none'}, 'trips': {'generator': 'none'}, 'conflicts': 'fail'},
        'evolution': {'checkpoint_hours': None, 'first_checkpoint_hours': None},
        'interventions': [],
    }


def _three_platform_day_v1():
    """One Monday from 06:00 to 23:00: ten all-day drivers, one trip per rider before 22:00."""
    base = _base_v1('three-platform-day', horizon_hours=17, calendar={'weekday': 0, 'hour': 6})
    base['population']['riders']['count'] = 100
    base['population']['drivers']['count'] = 10
    base['activity']['shifts'] = {'generator': 'rotation', 'crews': 1, 'shift_hours': 17, 'days': 1, 'first_start_hours': 0}
    base['activity']['trips'] = {'generator': 'weekly', 'trips_per_rider': 1, 'duration_hours': 16, 'peaks': [],
                                 'off_peak_weight': 1, 'assignment': 'round_robin'}
    return base


def _three_platform_week_v1():
    """Monday 00:00 through Sunday plus one drain hour: three eight-hour crews and weekly peaks."""
    base = _base_v1('three-platform-week', horizon_hours=169, calendar={'weekday': 0, 'hour': 0})
    base['population']['riders']['count'] = 3500
    base['population']['drivers']['count'] = 30
    base['activity']['shifts'] = {'generator': 'rotation', 'crews': 3, 'shift_hours': 8, 'days': 7, 'first_start_hours': 0}
    base['activity']['trips'] = {
        'generator': 'weekly', 'trips_per_rider': 1, 'duration_hours': 168, 'off_peak_weight': 1, 'assignment': 'round_robin',
        'peaks': [{'id': 'morning-commute', 'weekdays': [0, 1, 2, 3, 4], 'start_hour': 7, 'end_hour': 9, 'multiplier': 2.5},
                  {'id': 'afternoon-commute', 'weekdays': [0, 1, 2, 3, 4], 'start_hour': 16, 'end_hour': 19, 'multiplier': 2.8},
                  {'id': 'weekend-night', 'weekdays': [4, 5], 'start_hour': 21, 'end_hour': 3, 'multiplier': 3}]}
    base['evolution'] = {'checkpoint_hours': 24, 'first_checkpoint_hours': 24}
    return base


def _market_blank_v1():
    """World and behavior defaults with no platforms and no segments: the base for authored markets.

    Everything in `_base_v1` besides platforms/segments is inherited
    unchanged: world, behavior implementations and defaults, `activity` off
    (`shifts`/`trips` generators are `none`), evolution off, no
    interventions, empty notes. `compile_scenario` on the bare preset raises
    `platforms: at least one platform is required` -- that is the intended
    authoring-time error (see README.md and scenario-definition.md), and the
    empty segments tables are exactly what let `platform()`/`two_platform()`
    and a `with_changes({"platforms": ...})` merge in a whole market without
    first removing the three-platform preset's four named segments per role.
    """
    base = _base_v1('market-blank', horizon_hours=24, calendar={'weekday': 0, 'hour': 0})
    base['platforms'] = {}
    for role in ('riders', 'drivers'):
        base['population'][role]['segments'] = {}
    return base


PRESETS = {
    'three-platform-day@1': _three_platform_day_v1,
    'three-platform-week@1': _three_platform_week_v1,
    'market-blank@1': _market_blank_v1,
}


def preset_definition(identifier):
    if identifier not in PRESETS:
        raise ScenarioError(f'preset: unknown preset {identifier!r}; published: {sorted(PRESETS)}')
    definition = PRESETS[identifier]()
    definition['preset'] = identifier
    return definition


def platform(id, *, launched=True, version='modern-v1', rules=(), campaigns=(), programs=(), fallback='default',
            controller=None, starting_cash_minor=None, **parameters):
    """One complete platform entry keyed by its ID: ``{id: <PLATFORM entry>}``.

    Unnamed keyword parameters override `MARKETPLACE_DEFAULTS_V1`; every
    other tariff/dispatch parameter keeps its published default. Combine
    entries with the ``|`` dict operator and set them in one change, e.g.
    ``.with_changes({"platforms": platform("alpha", commission_fraction=.2)
    | platform("beta", commission_fraction=.1)})`` -- ``platforms`` is a
    `Table`, so this merges into (or replaces named entries in) whatever
    platforms the base scenario already has; starting from `market-blank@1`
    (no platforms) the result is exactly the platforms named here.

    Validation is immediate and at authoring time: an empty or dotted `id`
    is rejected the same way a `Table` key would be; an unknown or
    misspelled parameter name is rejected with the exact path (for example
    ``platform alpha.policy.parameters.comission_fraction``); `controller`
    must be `None` or `{"interval_hours": ..., "until_hours": ...}`.
    `starting_cash_minor` (an explicit keyword, not one of `**parameters`:
    it seeds the platform's cash `Account`, not a `MarketplaceParameters`
    field) defaults to `None`, meaning untracked. `compile_scenario` still
    re-validates cross-field combinations one platform cannot see alone,
    such as `max_local_commitments > 2`.
    """
    diag = Diagnostics()
    if not isinstance(id, str) or not id or '.' in id:
        diag.error('platform', 'IDs must be nonempty strings without dots')
    entry = {'launched': launched, 'controller': controller, 'starting_cash_minor': starting_cash_minor,
             'policy': {'implementation': BUILTIN_IMPLEMENTATIONS['marketplace'], 'version': version,
                        'parameters': {**MARKETPLACE_DEFAULTS_V1, **parameters},
                        'rules': list(rules), 'campaigns': list(campaigns), 'programs': list(programs),
                        'fallback': fallback}}
    checked = PLATFORM.check(entry, f'platform {id}', diag, complete=True)
    diag.raise_errors()
    return {id: checked}


def zone(id, *, min, max):
    """One complete `{id: {'min': [x0, y0], 'max': [x1, y1]}}` table entry, validated at authoring time.

    Mirrors `platform()`: `id` is validated as a `Table` key would be, and
    the box itself is checked against `ZONE` immediately, before
    `compile_scenario` would. Combine entries with `|` and set them with
    `.with_changes({"world.zones": zone(...) | zone(...)})` -- `world.zones`
    is a `Table`, so this merges by ID into whatever zones the base scenario
    already has. `min`/`max` are plain `(x, y)` pairs in kilometres; `Point()`
    (not `Point(positive=True)`) accepts `[0, 0]` and negative corners
    because `map_km` is a sampling extent, not a fence -- see `_check_zones`
    for the box/map/grid checks `compile_scenario` runs afterwards.
    """
    diag = Diagnostics()
    if not isinstance(id, str) or not id or '.' in id:
        diag.error('zone', 'IDs must be nonempty strings without dots')
    checked = ZONE.check({'min': list(min), 'max': list(max)}, f'zone {id}', diag, complete=True)
    diag.raise_errors()
    return {id: checked}


# ----------------------------------------------------------------------
# Scenario: a base definition plus an ordered list of typed changes
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    """An immutable authoring record. Every method returns a new scenario."""
    preset: str | None = None
    definition: dict | None = None
    changes: tuple = ()
    name: str | None = None

    def __post_init__(self):
        if (self.preset is None) == (self.definition is None):
            raise ScenarioError('scenario: give exactly one of preset or a complete definition')
        object.__setattr__(self, 'changes', tuple(self.changes))
        if self.definition is not None:
            object.__setattr__(self, 'definition', copy.deepcopy(self.definition))

    @classmethod
    def from_definition(cls, definition, name=None):
        """A complete definition, for example a saved resolved manifest or a JSON file."""
        return cls(definition=definition, name=name)

    @classmethod
    def load(cls, path):
        """A JSON definition, or a saved plan manifest (its resolved definition is used)."""
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(data, dict) and 'resolved' in data and 'fingerprints' in data:
            data = data['resolved']
        return cls.from_definition(data)

    def with_changes(self, changes):
        """Set values by dotted path. Maps merge by schema; lists are replaced whole."""
        ops = tuple(('set', path, copy.deepcopy(value)) for path, value in dict(changes).items())
        return Scenario(self.preset, self.definition, self.changes + ops, self.name)

    def add(self, path, item):
        """Insert one item into a keyed collection such as campaigns, rules, people or interventions."""
        return Scenario(self.preset, self.definition, self.changes + (('add', path, copy.deepcopy(item)),), self.name)

    def remove(self, path, identity):
        """Remove a keyed item or table entry by ID."""
        return Scenario(self.preset, self.definition, self.changes + (('remove', path, identity),), self.name)

    def renamed(self, name):
        return Scenario(self.preset, self.definition, self.changes, name)

    def base(self):
        if self.preset is not None:
            return preset_definition(self.preset)
        return copy.deepcopy(self.definition)

    def resolve(self):
        """Apply preset then overrides; return (resolved definition, provenance, diagnostics)."""
        diag = Diagnostics()
        base = self.base()
        source = f'preset:{self.preset}' if self.preset is not None else 'definition'
        resolved = SCENARIO.check(base, '', diag, complete=True)
        if diag.errors:
            diag.errors.insert(0, f'{source}: base definition is incomplete or invalid')
            return resolved, {}, diag
        if self.name is not None:
            resolved['name'] = self.name
        prefixes = []
        for index, (op, path, value) in enumerate(self.changes):
            try:
                prefix = _apply(resolved, op, path, value, index, diag)
            except ScenarioError as error:
                diag.errors.append(str(error))
                continue
            if prefix is not None:
                prefixes.append((prefix, f'change[{index}]:{op} {path}'))
        if diag.errors:
            return resolved, {}, diag
        resolved = SCENARIO.check(resolved, '', diag, complete=True)
        if resolved.get('preset') != base.get('preset'):
            diag.error('preset', 'the preset identity cannot be overridden; start from another preset instead')
        provenance = {}
        for leaf, _ in SCENARIO.leaves(resolved, ''):
            origin = source
            for prefix, label in prefixes:
                if leaf == prefix or leaf.startswith(prefix + '.'):
                    origin = label
            provenance[leaf] = origin
        if self.name is not None:
            provenance['name'] = 'scenario:name'
        return resolved, provenance, diag

    def to_dict(self):
        return {'preset': self.preset, 'definition': self.definition, 'name': self.name,
                'changes': [{'op': op, 'path': path, 'value': value} for op, path, value in self.changes]}


def two_platform(first, second, *, name=None, riders=0, drivers=0, horizon_hours=24, shared=None,
                 first_parameters=None, second_parameters=None):
    """A ready duopoly on `market-blank@1`: two launched platforms and one multi-homing segment per role.

    ``shared`` parameters (a plain dict) apply to both platforms;
    ``first_parameters``/``second_parameters`` further override just that
    one platform on top of ``shared``. Every generated rider and driver
    installs both apps with active accounts and prefers ``first`` (the
    ``both-apps`` segment, weight 1) -- a script that needs lopsided access
    or several segments still replaces or extends
    ``population.<role>.segments`` itself; this only removes the setup that
    is identical across every two-platform script. Returns a `Scenario`
    (not yet compiled), so callers keep chaining `.with_changes(...)` /
    `.add(...)`. Replaces, per script, roughly the preset choice, the
    eight-line segment-removal loop and two platform/segment blocks that
    `scenarios/*.py` still writes by hand.
    """
    if first == second:
        raise ScenarioError('two_platform: platform ids must be distinct')
    shared = shared or {}
    platforms = (platform(first, **{**shared, **(first_parameters or {})})
                | platform(second, **{**shared, **(second_parameters or {})}))
    return Scenario(preset='market-blank@1', name=name).with_changes({
        'world.horizon_hours': horizon_hours,
        'population.riders.count': riders, 'population.drivers.count': drivers,
        'platforms': platforms,
        'population.riders.segments': {
            'both-apps': segment(weight=1, apps=(first, second), preferred_app=first)},
        'population.drivers.segments': {
            'both-apps': segment(weight=1, apps=(first, second), preferred_app=first, driver={})},
    })


def _navigate(root, path):
    """Walk specs and values along a dotted path; return (parent spec, parent value, last key, spec, value)."""
    segments = path.split('.') if path else []
    if not segments or any(not segment for segment in segments):
        raise ScenarioError(f'{path!r}: paths are nonempty dotted keys')
    spec, value, walked = SCENARIO, root, ''
    parents = []
    for segment_ in segments:
        parents.append((spec, value, segment_))
        spec, value = spec.descend(value, segment_, walked)
        walked = join(walked, segment_)
    return parents, spec, value


def _apply(root, op, path, value, index, diag):
    parents, spec, current = _navigate(root, path)
    if op == 'set':
        checked = spec.check(value, path, diag, complete=False)
        _store(parents, spec.merge(current, checked))
        return path
    if op == 'add':
        if not isinstance(spec, Keyed):
            raise ScenarioError(f'{path}: add() applies to keyed collections only')
        checked = spec.item.check(value, join(path, value.get('id', '?') if isinstance(value, dict) else '?'), diag, complete=False)
        identity = checked.get('id') if isinstance(checked, dict) else None
        if any(item.get('id') == identity for item in current or ()):
            raise ScenarioError(f'{join(path, identity)}: an item with this id already exists; set its fields by path instead')
        _store(parents, list(current or ()) + [checked])
        return join(path, identity)
    if op == 'remove':
        if isinstance(spec, Keyed):
            if not any(item.get('id') == value for item in current or ()):
                raise ScenarioError(f'{join(path, value)}: no item with this id to remove')
            _store(parents, [item for item in current if item.get('id') != value])
        elif isinstance(spec, Table):
            if value not in (current or {}):
                raise ScenarioError(f'{join(path, value)}: no entry with this id to remove')
            _store(parents, {key: item for key, item in current.items() if key != value})
        else:
            raise ScenarioError(f'{path}: remove() applies to keyed collections and ID tables only')
        return join(path, value)
    raise ScenarioError(f'change[{index}]: unknown operation {op!r}')


def _store(parents, replacement):
    """Write a value back through the walked containers, rebuilding keyed lists by ID."""
    for spec, container, key in reversed(parents):
        if isinstance(spec, Keyed):
            replacement = spec.replace(container, key, replacement)
        else:
            container = dict(container or {})
            container[key] = replacement
            replacement = container
    root = parents[0][1]
    root.clear()
    root.update(replacement)


# ----------------------------------------------------------------------
# Compilation: references, ranges, bindings, plan
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class PlatformPlan:
    launched: bool
    implementation: str
    policy: PlatformPolicy
    controller: tuple | None  # (interval_seconds, until_seconds)
    starting_cash_minor: int | None = None


@dataclass(frozen=True)
class Plan:
    """An immutable prepared scenario. `prepare(seed)` realizes inputs; nothing here is run state."""
    resolved: dict
    provenance: dict
    warnings: tuple
    world: World
    horizon_seconds: float
    platforms: dict
    implementations: dict
    behavior: dict
    segments: dict
    explicit_people: dict
    person_ids: dict
    checkpoints: tuple
    interventions: tuple
    fingerprints: dict
    generators: dict  # {'trips'|'shifts'|'population.rider'|'population.driver': {'implementation', 'source_sha256'}}

    @property
    def name(self):
        return self.resolved['name']

    @property
    def calendar(self):
        return dict(self.resolved['world']['calendar'])

    @property
    def notes(self):
        """The free-form authoring notes: {id: text}, never part of the controls fingerprint."""
        return copy.deepcopy(self.resolved['notes'])

    @property
    def zones(self):
        """`{id: {'min': [x0, y0], 'max': [x1, y1]}}`. Never reaches `World`; the engine still does not fence coordinates."""
        return copy.deepcopy(self.resolved['world']['zones'])

    def manifest(self):
        """Everything needed to reconstruct and identify this plan without today's defaults."""
        return {'name': self.name, 'preset': self.resolved['preset'], 'calibration': self.resolved['calibration'],
                'notes': copy.deepcopy(self.resolved['notes']),
                'schema_version': SCHEMA_VERSION, 'compiler_version': COMPILER_VERSION,
                'seed_derivation_version': SEED_DERIVATION_VERSION, 'horizon_seconds': self.horizon_seconds,
                'fingerprints': dict(self.fingerprints), 'implementations': copy.deepcopy(self.implementations),
                'resolved': copy.deepcopy(self.resolved), 'provenance': dict(self.provenance),
                'interventions': copy.deepcopy(list(self.interventions)), 'checkpoints': list(self.checkpoints),
                'warnings': list(self.warnings), 'generators': copy.deepcopy(self.generators)}

    def prepare(self, seed=0):
        return prepare_inputs(self, seed)

    def save(self, path):
        Path(path).write_text(json.dumps(self.manifest(), indent=2, sort_keys=True, allow_nan=False), encoding='utf-8')


def compile_scenario(scenario):
    """Stages: build/resolve, normalize units, resolve references, validate, bind, fingerprint."""
    resolved, provenance, diag = scenario.resolve()
    diag.raise_errors()
    world_def = resolved['world']
    try:
        world = World(world_def['speed_kmh'], world_def['boarding_seconds'], world_def['minor_units_per_major'])
    except ValueError as error:
        diag.error('world', str(error))
    _check_zones(world_def, diag)
    horizon = world_def['horizon_hours'] * HOUR
    platforms = {}
    launched_at = {}
    for pid, item in resolved['platforms'].items():
        launched_at[pid] = 0 if item['launched'] else None
        policy = _compile_policy(item['policy'], f'platforms.{pid}.policy', diag, zones=world_def['zones'])
        controller = None
        if item['controller'] is not None:
            controller = (item['controller']['interval_hours'] * HOUR, item['controller']['until_hours'] * HOUR)
            if controller[1] > horizon:
                diag.warn(f'platforms.{pid}.controller.until_hours', 'extends beyond the horizon')
        platforms[pid] = PlatformPlan(item['launched'], item['policy']['implementation'], policy, controller,
                                      item['starting_cash_minor'])
    if not platforms:
        diag.error('platforms', 'at least one platform is required')
    implementations = {family: resolved['behavior'][family]['implementation'] for family in ('rider', 'driver', 'evolution')}
    implementations['marketplace'] = {pid: platforms[pid].implementation for pid in platforms}
    behavior = {}
    for family in ('rider', 'driver', 'evolution'):
        schema = policy_class(family, implementations[family]).declaration.parameter_schema
        try:
            behavior[family] = plain(schema(**resolved['behavior'][family]['parameters']))
        except (TypeError, ValueError) as error:
            diag.error(f'behavior.{family}.parameters', str(error))
    # Interventions: normalize times, order launches first, and detect conflicting writes.
    def _apply_policy_change(pid, policy_change, path):
        """Merge one policy intervention against platform pid's base policy, compile it, and check
        it does not try to change guarantee_window_seconds (the platform's single window-close
        cadence, fixed at launch -- ambiguity resolved in plans/scenario-readiness-plan.md)."""
        base = resolved['platforms'][pid]['policy']
        if policy_change.get('implementation', base['implementation']) != base['implementation']:
            diag.error(f'{path}.policy.implementation', 'an intervention selects a policy version, not a different implementation')
        merged = Implementation('marketplace', POLICY_EXTRA).merge(base, policy_change)
        compiled = _compile_policy(merged, f'{path}.policy', diag, zones=world_def['zones'])
        if (compiled is not None
                and compiled.parameters.guarantee_window_seconds != platforms[pid].policy.parameters.guarantee_window_seconds):
            diag.error(f'{path}.policy.parameters.guarantee_window_seconds',
                      'a policy intervention may not change guarantee_window_seconds; it is fixed at launch')
        if merged.get('version') == base['version'] and merged != base:
            diag.warn(f'{path}.policy.version', 'changed settings should carry a new policy version label')
        return compiled

    interventions = []
    wildcard_policies = []  # (item, at_seconds, path); expanded once launched_at is fully known
    for item in resolved['interventions']:
        at = item['at_hours'] * HOUR
        path = f"interventions.{item['id']}"
        entry = {'id': item['id'], 'kind': item['kind'], 'at_seconds': at}
        if at > horizon:
            diag.warn(path, 'scheduled after the horizon; it will never apply')
        if item['kind'] == 'policy' and item['platform'] == '*':
            wildcard_policies.append((item, at, path))
            continue
        if item['kind'] in ('launch', 'policy'):
            pid = item['platform']
            entry['platform'] = pid
            if pid not in platforms:
                diag.error(f'{path}.platform', f'unknown platform {pid!r}')
            elif item['kind'] == 'launch':
                if launched_at[pid] is not None:
                    diag.error(path, f'platform {pid!r} is already launched')
                else:
                    launched_at[pid] = at
            else:
                entry['policy'] = plain(_apply_policy_change(pid, item['policy'], path))
        elif item['kind'] == 'regulation':
            caps = {key: item[key] for key in ('max_base_fare_minor', 'max_per_km_minor', 'max_commission_fraction')}
            entry.update(caps)
            if all(v is None for v in caps.values()):
                diag.error(path, 'a regulation needs at least one cap')
            if (caps['max_base_fare_minor'] is None) != (caps['max_per_km_minor'] is None):
                diag.error(path, 'max_base_fare_minor and max_per_km_minor must be set together')
        else:
            entry.update({'role': item['role'], 'segment': item['segment'], 'people': list(item['people']),
                          'preferred_app': item['preferred_app']})
            if (item['segment'] is None) == (not item['people']):
                diag.error(path, 'a preference change targets either one segment or an explicit people list')
        interventions.append(entry)
    # policy(platform="*") expands to the platforms launched by this time -- launched_at now
    # reflects every 'launch' intervention regardless of authored order, since the loop above ran
    # to completion (mirrors the preference-validity pass below, which also runs only afterward).
    for item, at, path in wildcard_policies:
        selected = sorted(pid for pid in platforms if launched_at[pid] is not None and launched_at[pid] <= at)
        if not selected:
            diag.error(path, 'platform "*" selects no platform: none is launched by this time')
        for pid in selected:
            sub_path = f"interventions.{item['id']}-{pid}"
            compiled = _apply_policy_change(pid, item['policy'], sub_path)
            interventions.append({'id': f"{item['id']}-{pid}", 'kind': 'policy', 'at_seconds': at,
                                  'platform': pid, 'policy': plain(compiled)})
    interventions.sort(key=lambda e: (e['at_seconds'], INTERVENTION_ORDER[e['kind']], e['id']))
    seen = {}
    for entry in interventions:
        if entry['kind'] == 'preference':
            target = (entry['at_seconds'], 'preference', entry['role'], entry['segment'], tuple(entry['people']))
        elif entry['kind'] == 'regulation':
            target = (entry['at_seconds'], 'regulation')
        else:
            target = (entry['at_seconds'], entry['kind'], entry['platform'])
        if target in seen:
            diag.error(f"interventions.{entry['id']}", f"conflicts with {seen[target]!r}: same target and time")
        seen[target] = entry['id']
    # Population: segments and explicit people, validated against access rules.
    segments, person_ids, explicit_people = {}, {}, {}
    for role in ROLES:
        section = resolved['population'][f'{role}s']
        path = f'population.{role}s'
        weights = {sid: item['weight'] for sid, item in section['segments'].items()}
        if section['count'] and not section['segments']:
            diag.error(f'{path}.segments', 'generated people need at least one segment')
        if section['segments'] and abs(sum(weights.values()) - 1) > 1e-9:
            diag.error(f'{path}.segments', f'segment weights must sum to one, got {sum(weights.values())!r}')
        segments[role] = {}
        for sid, item in section['segments'].items():
            access = _check_access(role, item, f'{path}.segments.{sid}', platforms, launched_at, behavior, diag, sampled=True)
            segments[role][sid] = {**access, 'weight': item['weight'], 'initial_scores': dict(item['initial_scores']),
                                   'activity': dict(item['activity']),
                                   'traits': {role: dict(item[role]), 'evolution': dict(item['evolution'])}}
        ids = [f'{role}-{index}' for index in range(1, section['count'] + 1)]
        explicit = []
        explicit_people[role] = []
        for item in section['people']:
            label = f"{path}.people.{item['id']}"
            if item['id'] in ids or item['id'] in explicit:
                diag.error(label, 'duplicate person id (generated ids are role-n)')
            explicit.append(item['id'])
            base = {}
            if item['segment'] is not None:
                if item['segment'] not in section['segments']:
                    diag.error(f'{label}.segment', f'unknown segment {item["segment"]!r}')
                else:
                    base = section['segments'][item['segment']]
            merged, missing = _merge_person(role, item, base)
            for key in missing:
                diag.error(f'{label}.{key}', 'required for an explicit person without a segment')
            access = _check_access(role, merged, label, platforms, launched_at, behavior, diag, sampled=False)
            explicit_people[role].append({**access, 'id': item['id'], 'segment': item['segment'],
                                          'initial_scores': merged['initial_scores'], 'activity': merged['activity'],
                                          'traits': {role: merged[role], 'evolution': merged['evolution']}})
        person_ids[role] = tuple(ids + explicit)
    for pid, plan_ in platforms.items():
        members = [sid for role in ROLES for sid, item in segments[role].items() if pid in item['apps']]
        if not plan_.launched and launched_at[pid] is None:
            diag.warn(f'platforms.{pid}', 'never launches; awareness of it cannot lead to adoption')
        if plan_.launched and not members and not any(pid in p.get('apps', ()) for role in ROLES for p in resolved['population'][f'{role}s']['people']):
            diag.warn(f'platforms.{pid}', 'no segment or explicit person installs this app')
    # Preference interventions must target existing cohorts with static access.
    for entry in interventions:
        if entry['kind'] != 'preference':
            continue
        path = f"interventions.{entry['id']}"
        role = entry['role']
        if entry['segment'] is not None and entry['segment'] not in segments[role]:
            diag.error(f'{path}.segment', f'unknown {role} segment {entry["segment"]!r}')
        elif entry['segment'] is not None:
            item = segments[role][entry['segment']]
            if entry['preferred_app'] not in item['usable']:
                diag.error(f'{path}.preferred_app', f'{entry["preferred_app"]!r} is not usable by segment {entry["segment"]!r}')
        for person_id in entry['people']:
            if person_id not in person_ids[role]:
                diag.error(f'{path}.people', f'unknown {role} {person_id!r}')
        app = entry['preferred_app']
        if app in launched_at and (launched_at[app] is None or launched_at[app] > entry['at_seconds']):
            diag.error(f'{path}.preferred_app', f'{app!r} is not launched at that time')
    # Activity generators: references and static conflicts that do not depend on realized rides.
    activity = resolved['activity']
    shifts, trips = activity['shifts'], activity['trips']
    if shifts['generator'] == 'rotation':
        if shifts['crews'] * shifts['shift_hours'] > 24:
            diag.error('activity.shifts', 'crews * shift_hours exceeds 24 hours: a driver would start a shift before ending the previous one')
        if not person_ids['driver']:
            diag.warn('activity.shifts', 'rotation without drivers produces no shifts')
    elif shifts['generator'] == 'explicit':
        _check_explicit_windows(shifts['items'], 'driver', person_ids['driver'], 'activity.shifts.items', diag)
    if trips['generator'] == 'weekly':
        if not person_ids['rider']:
            diag.warn('activity.trips', 'weekly demand without riders produces no trips')
        duration = trips['duration_hours']
        if duration is not None and duration * HOUR > horizon:
            diag.error('activity.trips.duration_hours', 'demand continues after the horizon')
        for item in trips['peaks']:
            if item['start_hour'] == item['end_hour']:
                diag.error(f"activity.trips.peaks.{item['id']}", 'peak hours must be distinct')
            if not item['weekdays']:
                diag.error(f"activity.trips.peaks.{item['id']}.weekdays", 'a peak needs at least one weekday')
    elif trips['generator'] == 'explicit':
        _check_explicit_windows(trips['items'], 'rider', person_ids['rider'], 'activity.trips.items', diag,
                                patience=behavior.get('rider', {}).get('patience_seconds'))
    for kind in ('shifts', 'trips'):
        for item in activity[kind].get('items', ()):
            if item['at_hours'] * HOUR > horizon:
                diag.warn(f"activity.{kind}.items.{item['id']}", 'starts after the horizon')
    # Segment/explicit-person geometry: a second pass, now that shifts/trips generator kinds are
    # known, so the two generator-mismatch warnings below can compare against them.
    for role in ROLES:
        path = f'population.{role}s'
        for sid, item in segments[role].items():
            _check_segment_activity(item['activity'], f'{path}.segments.{sid}.activity', world_def['zones'], world_def,
                                    trips['generator'], shifts['generator'], diag)
        for person in explicit_people[role]:
            _check_segment_activity(person['activity'], f"{path}.people.{person['id']}.activity", world_def['zones'],
                                    world_def, trips['generator'], shifts['generator'], diag)
    checkpoints = ()
    evolution = resolved['evolution']
    if evolution['checkpoint_hours'] is not None:
        first = evolution['first_checkpoint_hours'] or evolution['checkpoint_hours']
        times, at = [], first * HOUR
        while at <= horizon:
            times.append(at)
            at += evolution['checkpoint_hours'] * HOUR
        checkpoints = tuple(times)
    elif evolution['first_checkpoint_hours'] is not None:
        diag.error('evolution.first_checkpoint_hours', 'requires checkpoint_hours')
    learning = any(segments[role][sid]['traits']['evolution'].get(key, behavior.get('evolution', {}).get(key, 0))
                   for role in ROLES for sid in segments[role] for key in ('adoption_rate_per_day', 'learning_rate'))
    if learning and not checkpoints:
        diag.warn('evolution.checkpoint_hours', 'adoption or learning rates are nonzero but no checkpoint is scheduled')
    # Registered generators: identity + source hash for the manifest and implementation fingerprint.
    # The generator itself runs at prepare time (_run_registered / _realize_population); compile time
    # only proves the selected identifier is registered, mirroring Implementation's policy-identifier check.
    generators = {}
    for family, section in (('shifts', shifts), ('trips', trips)):
        if section['generator'] != 'registered':
            continue
        identifier = section['implementation']
        if identifier not in GENERATOR_REGISTRY[family]:
            diag.error(f'activity.{family}.implementation',
                       f'unknown {family} generator {identifier!r}; registered: {sorted(GENERATOR_REGISTRY[family])}')
        else:
            generators[family] = {'implementation': identifier, 'source_sha256': GENERATOR_SOURCES[family][identifier]}
    if generators.get('trips') and not person_ids['rider']:
        diag.warn('activity.trips', 'registered demand without riders produces no trips')
    for role in ROLES:
        gen = resolved['population'][f'{role}s']['generator']
        if gen['kind'] != 'registered':
            continue
        identifier = gen['implementation']
        if identifier not in GENERATOR_REGISTRY['population']:
            diag.error(f'population.{role}s.generator.implementation',
                       f'unknown population generator {identifier!r}; registered: {sorted(GENERATOR_REGISTRY["population"])}')
        else:
            generators[f'population.{role}'] = {'implementation': identifier, 'source_sha256': GENERATOR_SOURCES['population'][identifier]}
            if resolved['population'][f'{role}s']['count'] == 0:
                diag.warn(f'population.{role}s.generator', 'produces no people')
    diag.raise_errors()
    # Controlled inputs: identities, segment assignment, sampled trait draws and
    # schedules depend only on these sections and the seed, never on treatments.
    controls = {'world': resolved['world'], 'population': resolved['population'], 'activity': resolved['activity'],
                'seed_derivation_version': SEED_DERIVATION_VERSION}
    fingerprints = {'definition': fingerprint(resolved), 'controls': fingerprint(controls),
                    'implementation': implementation_fingerprint(implementations, generators)}
    fingerprints['plan'] = fingerprint([fingerprints['definition'], fingerprints['implementation'], COMPILER_VERSION])
    return Plan(resolved, provenance, tuple(diag.warnings), world, horizon, platforms, implementations, behavior,
                segments, explicit_people, person_ids, checkpoints, tuple(interventions), fingerprints, generators)


def implementation_fingerprint(implementations, generators=None):
    """Selected policy implementations plus registered-generator identity and source hash, so a swapped
    generator (or its source drifting since registration) changes `fingerprints['implementation']`/`['plan']`
    exactly like a swapped policy does."""
    source_dir = Path(__file__).resolve().parent
    sources = {name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    return fingerprint({'sources': sources, 'implementations': implementations, 'generators': generators or {},
                        'compiler': COMPILER_VERSION})


def _resolve_service_area(parameters, zones, path, diag):
    """Resolve a string `service_area` in `parameters` against `zones` into a box, in place -- the
    one place a zone id becomes a box before it reaches `MarketplaceParameters` (plan section 4.C;
    scenario-definition.md). Shared by a policy's base parameters and every rule's parameters, since
    a rule can override service_area like any other MarketplaceParameters field; an unresolved string
    reaching MarketplaceParameters would later crash marketplace_policy.inside() at decision time."""
    area = parameters.get('service_area')
    if isinstance(area, str):
        zones = zones or {}
        if area not in zones:
            diag.error(f'{path}.service_area', f'unknown zone {area!r}; declared: {sorted(zones)}')
        else:
            box = zones[area]
            parameters['service_area'] = [list(box['min']), list(box['max'])]


def _compile_policy(definition, path, diag, zones=None):
    """Normalize hours to seconds (campaign/rule windows, program window/online hours) and resolve
    a string `service_area` against the world's declared zones -- the one place a zone id becomes a
    box before it reaches `MarketplaceParameters` (plan section 4.C; scenario-definition.md). Applied
    via `_resolve_service_area` to the base parameters and to every rule's parameters alike;
    `_apply_policy_change` routes interventions through this same function, so intervention rules
    get the same treatment."""
    campaigns = []
    for item in definition['campaigns']:
        campaigns.append({key: value for key, value in item.items() if key not in ('start_hours', 'end_hours')}
                         | {'start': item['start_hours'] * HOUR, 'end': item['end_hours'] * HOUR})
    rules = []
    for item in definition['rules']:
        entry = {key: value for key, value in item.items() if key not in ('start_hours', 'end_hours')}
        if item.get('start_hours') is not None:
            entry['start'], entry['end'] = item['start_hours'] * HOUR, item['end_hours'] * HOUR
        entry['parameters'] = dict(entry.get('parameters') or {})
        _resolve_service_area(entry['parameters'], zones, f'{path}.rules.{item["id"]}.parameters', diag)
        rules.append(entry)
    programs = []
    for item in definition.get('programs', ()):
        programs.append({key: value for key, value in item.items() if key not in ('window_hours', 'min_online_hours')}
                        | {'window_seconds': item['window_hours'] * HOUR,
                           'min_online_seconds': item['min_online_hours'] * HOUR})
    parameters = dict(definition['parameters'])
    _resolve_service_area(parameters, zones, f'{path}.parameters', diag)
    try:
        return PlatformPolicy.compile(overrides=parameters, rules=rules, campaigns=campaigns, programs=programs,
                                      version=definition['version'], fallback=definition['fallback'])
    except (TypeError, ValueError) as error:
        diag.error(path, str(error))
        return None


def _has_grid_point(lo, hi, step):
    """Whether some multiple of `step` lies in the closed interval `[lo, hi]`."""
    nearest = math.ceil(lo / step - 1e-9) * step
    return nearest <= hi + 1e-9


def _check_zones(world_def, diag):
    """`world.zones`: max greater than min on both axes, a warning when a box extends beyond `map_km`,
    and (grid sampling only) an error when either axis has no multiple of `grid_step_km` inside the box
    -- `_sample_in_zone` draws each axis independently, so a missing grid point on either one would hang.

    Overlapping zones are legal and are not diagnosed: sampling always picks
    a zone first (`_pick_zone`), so overlap never double-counts a point --
    a point on a shared edge simply belongs to both boxes under this closed-box test.
    """
    map_km, sampling, step = world_def['map_km'], world_def['sampling'], world_def['grid_step_km']
    for zid, box in world_def['zones'].items():
        path = f'world.zones.{zid}'
        lo, hi = box['min'], box['max']
        if hi[0] <= lo[0] or hi[1] <= lo[1]:
            diag.error(path, f'a zone box needs max greater than min on both axes, got min={lo!r} max={hi!r}')
            continue
        if lo[0] < 0 or lo[1] < 0 or hi[0] > map_km[0] or hi[1] > map_km[1]:
            diag.warn(path, 'extends beyond map_km; sampled points can fall outside the sampling extent')
        if sampling == 'grid' and not all(_has_grid_point(lo[axis], hi[axis], step) for axis in (0, 1)):
            diag.error(path, f'contains no grid point at grid_step_km={step}')


def _merge_person(role, item, base):
    """Merge one person declaration (an explicit person, or a registered population generator's
    returned declaration) onto its named segment's resolved fields (`base`, or `{}` if it names none).

    Access fields (apps/preferred_app/accounts/awareness/disclosed_to/registrations) are inherited
    wholesale from `item` if it names them, else from `base`; the trait/evolution/initial_scores/activity
    maps merge key-wise instead, so a person can override just one field of its segment's bundle.
    Shared by `compile_scenario`'s explicit-person loop and `_realize_registered_population`, so segment
    and generated people go through one implementation. Returns `(merged, missing)`; `missing` lists
    which of ('apps', 'preferred_app') neither `item` nor `base` supplied -- required whenever there is
    no segment (or an unresolved one) to supply them, which the caller reports with its own path.
    """
    merged = {key: value for key, value in base.items() if key not in ('weight', role, 'evolution', 'initial_scores', 'activity')}
    merged.update({key: value for key, value in item.items() if key not in ('id', 'segment', role, 'evolution', 'initial_scores', 'activity')})
    missing = [key for key in ('apps', 'preferred_app') if key not in merged]
    for key in missing:
        merged[key] = [] if key == 'apps' else ''
    merged.setdefault('awareness', list(merged['apps']))
    merged.setdefault('disclosed_to', [])
    merged.setdefault('accounts', None)
    if role == 'driver':
        merged.setdefault('registrations', None)
    merged[role] = {**base.get(role, {}), **item.get(role, {})}
    merged['evolution'] = {**base.get('evolution', {}), **item.get('evolution', {})}
    merged['initial_scores'] = {**base.get('initial_scores', {}), **item.get('initial_scores', {})}
    merged['activity'] = {**base.get('activity', {}), **item.get('activity', {})}
    return merged, missing


def _launch_times(plan):
    """Rebuild the compile-time `{platform_id: launched_at_seconds_or_None}` map from `plan` alone, for
    `_check_access` calls made after compilation (a registered population generator's declarations;
    `Inputs.explicit`'s people) -- no new `Plan` field needed."""
    launched_at = {pid: (0 if item.launched else None) for pid, item in plan.platforms.items()}
    for entry in plan.interventions:
        if entry['kind'] == 'launch':
            launched_at[entry['platform']] = entry['at_seconds']
    return launched_at


def _check_access(role, item, path, platforms, launched_at, behavior, diag, *, sampled):
    apps = list(item.get('apps') or [])
    accounts = apps if item.get('accounts') is None else list(item['accounts'])
    registrations = accounts if item.get('registrations') is None else list(item['registrations'])
    for key, value in (('apps', apps), ('accounts', accounts), ('awareness', item.get('awareness') or []),
                       ('disclosed_to', item.get('disclosed_to') or [])) + ((('registrations', registrations),) if role == 'driver' else ()):
        unknown = [p for p in value if p not in platforms]
        if unknown:
            diag.error(f'{path}.{key}', f'unknown platform(s) {unknown}')
        if len(set(value)) != len(value):
            diag.error(f'{path}.{key}', 'duplicate platform IDs')
        if key in ('apps', 'accounts', 'registrations') and not value:
            diag.error(f'{path}.{key}', 'at least one platform is required')
        if key in ('apps', 'accounts', 'registrations'):
            unlaunched = [p for p in value if p in platforms and not platforms[p].launched]
            if unlaunched:
                diag.error(f'{path}.{key}', f'{unlaunched} are not launched; grow membership through awareness and adoption')
    if not set(accounts) <= set(apps):
        diag.error(f'{path}.accounts', 'accounts require installed apps')
    usable = [p for p in apps if p in accounts and (role == 'rider' or p in registrations) and p in platforms and platforms[p].launched]
    preferred = item.get('preferred_app')
    if preferred not in usable:
        diag.error(f'{path}.preferred_app', f'{preferred!r} must be an installed, account-enabled, launched app'
                   + (' registered for the assigned car' if role == 'driver' else ''))
    if not usable:
        diag.error(f'{path}', 'no usable launched app; every person must be able to participate')
    for family in (role, 'evolution'):
        scalars = {k: v for k, v in item.get(family, {}).items() if not isinstance(v, dict)}
        if family in behavior:
            try:
                TRAITS[family](**{**behavior[family], **scalars})
            except (TypeError, ValueError) as error:
                diag.error(f'{path}.{family}', str(error))
    for pid in item.get('initial_scores', {}):
        if pid not in platforms:
            diag.error(f'{path}.initial_scores.{pid}', 'unknown platform')
    return {'apps': apps, 'preferred_app': preferred, 'accounts': accounts, 'registrations': registrations,
            'awareness': list(item.get('awareness') or []), 'disclosed_to': list(item.get('disclosed_to') or []),
            'usable': usable}


def _check_explicit_windows(items, role, known_ids, path, diag, patience=None):
    by_person = {}
    for item in items:
        if item[role] not in known_ids:
            diag.error(f"{path}.{item['id']}.{role}", f'unknown {role} {item[role]!r}')
        by_person.setdefault(item[role], []).append(item)
    for person_id, entries in by_person.items():
        entries.sort(key=lambda e: (e['at_hours'], e['id']))
        for previous, current in zip(entries, entries[1:]):
            if role == 'driver':
                end = None if previous['hours'] is None else previous['at_hours'] + previous['hours']
                if end is None or current['at_hours'] < end:
                    diag.error(f"{path}.{current['id']}", f"driver {person_id!r} would still be on shift {previous['id']!r}")
            elif current['at_hours'] == previous['at_hours']:
                diag.error(f"{path}.{current['id']}", f"rider {person_id!r} already starts trip {previous['id']!r} at that time")
            elif patience is not None and (current['at_hours'] - previous['at_hours']) * HOUR < patience:
                diag.warn(f"{path}.{current['id']}", f"within search patience of {previous['id']!r}; a live intent would fail the run")


def _check_segment_activity(activity, path, zones, world_def, trips_generator, shifts_generator, diag):
    """One segment's or explicit person's `activity` block: zone references, weight sums, `distance_km`
    feasibility against `world.map_km`/`sampling`, and the two generator-mismatch warnings.

    Called for every rider/driver segment and every explicit person (the
    role split in `segment_fields` already restricts a driver's `activity`
    to `start_zone` and a rider's to the other four keys, so most branches
    below are no-ops for a driver's block).
    """
    origin, destination = activity.get('origin_zones'), activity.get('destination_zones')
    distance, peaks = activity.get('distance_km'), activity.get('spatial_peaks')
    start_zone = activity.get('start_zone')

    def zone_ref(zid, subpath):
        if zid not in zones:
            diag.error(subpath, f'unknown zone {zid!r}; declared: {sorted(zones)}')

    if origin is not None:
        for zid in origin:
            zone_ref(zid, f'{path}.origin_zones.{zid}')
        if sum(origin.values()) <= 0:
            diag.error(f'{path}.origin_zones', 'origin_zones weights must sum to a positive number')
    if destination is not None:
        if origin is None:
            diag.error(f'{path}.destination_zones',
                       'destination_zones requires origin_zones: the destination mix is conditional on the origin zone')
        else:
            for oz, weight in origin.items():
                if weight > 0 and oz not in destination:
                    diag.error(f'{path}.destination_zones', f'missing a row for origin zone {oz!r}')
        for oz, row in destination.items():
            zone_ref(oz, f'{path}.destination_zones.{oz}')
            for dz in row:
                zone_ref(dz, f'{path}.destination_zones.{oz}.{dz}')
            if sum(row.values()) <= 0:
                diag.error(f'{path}.destination_zones.{oz}', 'weights must sum to a positive number')
        if distance is not None:
            diag.error(path, 'declare either destination_zones or distance_km, not both')
    if distance is not None:
        (name, args), = distance.items()
        if name == 'choice':
            values = [v for v, _ in args]
            numeric = [v for v in values if _is_number(v)]
            if len(numeric) != len(values):
                diag.error(f'{path}.distance_km', 'distance_km: choice values must be numbers')
            lower, upper = (min(numeric), max(numeric)) if numeric else (None, None)
        elif name == 'uniform':
            lower, upper = args[0], args[1]
        else:  # normal
            lower, upper = args[2], args[3]
        if lower is not None and lower <= 0:
            diag.error(f'{path}.distance_km', 'distance_km must be strictly positive')
        if upper is not None:
            width, height = world_def['map_km']
            half_diagonal = math.hypot(width, height) / 2
            if upper > half_diagonal:
                diag.error(f'{path}.distance_km', f'distance_km up to {upper:g} km cannot fit inside '
                           f'map_km {width:g}x{height:g} from every origin')
            elif upper > min(width, height):
                diag.warn(f'{path}.distance_km', 'rejection sampling will discard most directions near the map centre')
        if world_def['sampling'] == 'grid':
            diag.warn(f'{path}.distance_km',
                      'grid sampling snaps the destination to the grid, so the realized distance differs from the drawn one')
    if peaks is not None:
        if origin is None:
            diag.error(f'{path}.spatial_peaks', 'spatial_peaks require origin_zones')
        for item in peaks:
            label = f"{path}.spatial_peaks.{item['id']}"
            for zid in item['zones']:
                zone_ref(zid, f'{label}.zones.{zid}')
            if item['start_hour'] == item['end_hour']:
                diag.error(label, 'peak hours must be distinct')
            if not item['weekdays']:
                diag.error(f'{label}.weekdays', 'a peak needs at least one weekday')
            if not item['zones']:
                diag.error(f'{label}.zones', 'a spatial peak needs at least one zone')
    if start_zone is not None:
        zone_ref(start_zone, f'{path}.start_zone')
    if (origin is not None or destination is not None or distance is not None or peaks is not None) and trips_generator != 'weekly':
        diag.warn(path, f'segment geometry applies to the weekly trip generator; activity.trips is {trips_generator!r}')
    if start_zone is not None and shifts_generator != 'rotation':
        diag.warn(path, f'start_zone applies to the rotation shift generator; activity.shifts is {shifts_generator!r}')


def diff_plans(baseline, variant):
    """Semantic difference of two resolved definitions, leaf by leaf, keyed lists by ID."""
    before = dict(SCENARIO.leaves(baseline.resolved, ''))
    after = dict(SCENARIO.leaves(variant.resolved, ''))
    rows = []
    for path in sorted(set(before) | set(after)):
        if before.get(path, _MISSING) != after.get(path, _MISSING):
            rows.append({'path': path, 'baseline': before.get(path), 'variant': after.get(path),
                         'source': variant.provenance.get(path)})
    if baseline.implementations != variant.implementations:
        rows.append({'path': 'implementations', 'baseline': baseline.implementations,
                     'variant': variant.implementations, 'source': 'registry'})
    return rows


_MISSING = object()


# ----------------------------------------------------------------------
# Prepared inputs: realized population and exogenous activity per replication
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Inputs:
    """Explicit input artifacts for one replication. Runs never mutate them."""
    plan: Plan
    seed: int
    people: tuple
    sessions: tuple
    fingerprint: str

    def to_dict(self):
        return {'seed': self.seed, 'seed_derivation_version': SEED_DERIVATION_VERSION,
                'controls_fingerprint': self.plan.fingerprints['controls'], 'fingerprint': self.fingerprint,
                'people': copy.deepcopy(list(self.people)), 'sessions': copy.deepcopy(list(self.sessions))}

    def reuse_for(self, plan):
        """Share these controlled inputs with a variant whose controls are identical.

        Person identities, segment assignment, sampled trait draws and every
        shift/trip are reproduced from the same seed. Trait values that the
        variant deliberately changes (behavior defaults, segment overrides)
        follow the variant, because those are treatments, not controls.
        """
        if plan.fingerprints['controls'] != self.plan.fingerprints['controls']:
            raise ScenarioError('inputs: the variant changes population or activity controls; prepare its own inputs')
        shared = prepare_inputs(plan, self.seed)
        if shared.sessions != self.sessions or [p['id'] for p in shared.people] != [p['id'] for p in self.people]:
            raise ScenarioError('inputs: controlled schedules diverged despite equal controls')
        return shared

    @classmethod
    def explicit(cls, plan, seed=0, *, people=None, sessions=None):
        """Throwaway fixture inputs on a compiled plan: explicit people and/or sessions, validated the
        same way as a registered generator's output -- for a short script that wants to schedule a few
        trips without authoring a definition (plan section 3.2's "direct Inputs construction").

        `people` (default: the plan's own population) is a list of `person()`-shaped declarations, each
        needing a `role` key; realized through the same schema-check -> `_merge_person` -> `_check_access`
        -> `_check_segment_activity` -> `_realize_person` path as an explicit or registered-generator
        person, so it has the identical shape and validation, geometry (`activity`) included: a person's
        merged `activity` is honored at realization exactly like a segment's or a registered population
        generator's, through the same `registered_activity` overlay `prepare_inputs` threads through
        `_realize_activity`. `sessions` (default: the plan's own generated activity) is a list of
        trip/shift dicts, each checked with `_check_session` against `people` (not `plan.person_ids`,
        when `people` was supplied) then sorted and checked with `_check_realized_conflicts`, exactly
        like a registered generator's output.

        No new `Inputs` field: `to_dict()` and every snapshot's `inputs` blob keep their shape.
        `reuse_for` already refuses inputs like these for any OTHER plan -- re-preparing that plan
        yields ITS OWN generated people/sessions, which will not match what was supplied here, so
        `reuse_for`'s existing "controlled schedules diverged despite equal controls" error fires.
        There is no separate opt-out flag; that refusal is by design.
        """
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ScenarioError('seed: must be an integer')
        registered_activity = {}
        if people is None:
            realized_people = tuple(_realize_population(plan, seed, registered_activity))
        else:
            realized_people = tuple(_realize_explicit_people(plan, seed, people, registered_activity))
        if sessions is None:
            realized_sessions = tuple(_realize_activity(plan, seed, realized_people, registered_activity))
        else:
            if people is None:
                known_ids = {'rider': set(plan.person_ids['rider']), 'driver': set(plan.person_ids['driver'])}
            else:
                known_ids = {role: {p['id'] for p in realized_people if p['role'] == role} for role in ROLES}
            checked = []
            for index, item in enumerate(sessions):
                kind = item.get('kind') if isinstance(item, dict) else None
                if kind not in ('trip', 'shift'):
                    raise ScenarioError(f'sessions[{index}]: expected kind "trip" or "shift", got {kind!r}')
                checked.append(_check_session(item, kind, known_ids, f'sessions[{index}]'))
            checked.sort(key=lambda s: (s['at_seconds'], s['kind'] != 'shift', s['id']))
            _check_realized_conflicts(checked, plan)
            realized_sessions = tuple(checked)
        digest = fingerprint({'controls': plan.fingerprints['controls'], 'seed': seed,
                              'people': realized_people, 'sessions': realized_sessions})
        return cls(plan, seed, realized_people, realized_sessions, digest)


def prepare_inputs(plan, seed=0):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScenarioError('seed: must be an integer')
    # A registered population generator's people are not in plan.explicit_people and may have no
    # segment, so _activity_index (compile-time sources only) cannot recover their activity; this
    # dict, filled as _realize_population runs, is the third source _realize_activity overlays.
    registered_activity = {}
    people = tuple(_realize_population(plan, seed, registered_activity))
    sessions = tuple(_realize_activity(plan, seed, people, registered_activity))
    digest = fingerprint({'controls': plan.fingerprints['controls'], 'seed': seed, 'people': people, 'sessions': sessions})
    return Inputs(plan, seed, people, sessions, digest)


def _allocate(count, weights, rng):
    """Exact counts by largest remainder, then a seeded assignment order."""
    names = sorted(weights)
    raw = {name: count * weights[name] for name in names}
    counts = {name: math.floor(raw[name]) for name in names}
    for name in sorted(names, key=lambda n: (-(raw[n] - counts[n]), n))[:count - sum(counts.values())]:
        counts[name] += 1
    assignment = [name for name in names for _ in range(counts[name])]
    rng.shuffle(assignment)
    return assignment


def _sample(spec, rng):
    (name, args), = spec.items()
    if name == 'uniform':
        return rng.uniform(args[0], args[1])
    if name == 'normal':
        return min(args[3], max(args[2], rng.gauss(args[0], args[1])))
    values, weights = zip(*args)
    return rng.choices(values, weights=weights, k=1)[0]


def _realize_population(plan, seed, registered_activity=None):
    """`registered_activity`, when given, is filled with `{person_id: activity}` for every
    registered-population-generated person with a nonempty merged activity -- the only source that
    knows it, since such a person is never in `plan.explicit_people` and may declare no segment at all.

    `population.<role>s.people` (compile-time explicit people) are appended after either source of
    generated members, exactly like the segments branch already did -- a registered population
    generator only replaces segment *allocation*, not the separate explicit-people list, and
    `compile_scenario` already folds their ids into `plan.person_ids` regardless of generator kind
    (duplicate-id checks, preference interventions and `_run_registered`'s `known_ids` all validate
    against the union), so silently dropping them here would let compile-time checks pass for an
    identity `prepare()` never realizes.
    """
    resolved = plan.resolved
    for role in ROLES:
        section = resolved['population'][f'{role}s']
        if section['generator']['kind'] == 'registered':
            generated = _realize_registered_population(role, section, plan, seed)
            if registered_activity is not None:
                for member in generated:
                    if member['activity']:
                        registered_activity[member['id']] = member['activity']
            members = generated + list(plan.explicit_people[role])
        else:
            rng = random.Random(derive_seed(seed, ['population', role]))
            weights = {sid: item['weight'] for sid, item in plan.segments[role].items()}
            assignment = _allocate(section['count'], weights, rng) if section['count'] else []
            members = [{**plan.segments[role][sid], 'id': f'{role}-{index + 1}', 'segment': sid}
                       for index, sid in enumerate(assignment)] + list(plan.explicit_people[role])
        for member in members:
            yield _realize_person(plan, role, member, seed)


def _realize_person(plan, role, member, seed):
    """One realized person record from a `member` dict shaped like `plan.segments[role][sid]` (plus
    `id`/`segment`) for a segment-allocated person, or like `plan.explicit_people[role]`'s entries for
    an explicit or registered-generator one -- the same trait sampling and profile assembly either way.
    Traits are sampled once per person from declared distributions, seeded by identity so paired variants
    that share a seed and controls reproduce the same draws.
    """
    person_id = member['id']
    traits = {}
    for family in (role, 'evolution'):
        values = {**plan.behavior[family], **member['traits'][family]}
        for name, value in values.items():
            if isinstance(value, dict):
                values[name] = _sample(value, random.Random(derive_seed(seed, ['trait', role, person_id, family, name])))
        try:
            traits[family] = plain(TRAITS[family](**values))
        except (TypeError, ValueError) as error:
            raise ScenarioError(f'population.{role}s {person_id} {family}: {error}') from None
    profile = {'apps': member['apps'], 'preferred_app': member['preferred_app'], 'accounts': member['accounts'],
               'registrations': member['registrations'] if role == 'driver' else member['accounts'],
               'awareness': member['awareness'], 'segment': member['segment'] if member['segment'] is not None else 'explicit',
               'disclosed_to': member['disclosed_to'],
               'rider': traits.get('rider', plan.behavior['rider']), 'driver': traits.get('driver', plan.behavior['driver']),
               'evolution': traits['evolution']}
    profile = plain(_profile(profile))
    record = {'role': role, 'id': person_id, 'segment': member['segment'], 'profile': profile,
              'initial_scores': dict(member['initial_scores'])}
    if role == 'driver':
        record['car'] = {'id': f'car-{person_id}', 'registrations': list(profile['registrations'])}
    return record


def _realize_registered_population(role, section, plan, seed):
    """One role's people from a registered population generator: `count` still fixes the identities
    (`role-1..role-n`, plan section 3.2's "per-person callable" replacing segment allocation, not
    identity); the generator supplies each counted id's declaration. This is why every compile-time
    cross-check that already validated against `plan.person_ids` (explicit activity references,
    preference interventions, `Inputs.reuse_for` controls) keeps working: the generator cannot invent
    an identity the compiler never saw.

    Each declaration is checked with the same partial `segment_fields(role, explicit=True)` schema an
    explicit person uses, merged with its named segment (if any) through `_merge_person`, and validated
    with the same `_check_access` and `_check_segment_activity` -- so a generated person has the
    identical shape and validation (geometry included) as a segment-allocated or explicit one, ready
    for `_realize_person`.
    """
    identifier = section['generator']['implementation']
    implementation = generator_class('population', identifier)
    expected = [f'{role}-{index}' for index in range(1, section['count'] + 1)]
    rng = random.Random(derive_seed(seed, ['population', role, identifier]))
    produced = implementation.generate(definition=copy.deepcopy(plan.resolved), role=role, person_ids=list(expected),
                                       parameters=copy.deepcopy(section['generator']['parameters']), random=rng)
    if not isinstance(produced, dict):
        raise ScenarioError(f'population.{role}s.generator.{identifier}: generate() must return a '
                            f'{{person_id: declaration}} mapping, got {produced!r}')
    missing, unexpected = sorted(set(expected) - set(produced)), sorted(set(produced) - set(expected))
    if missing or unexpected:
        detail = '; '.join(filter(None, [f'missing {missing}' if missing else '', f'unexpected {unexpected}' if unexpected else '']))
        raise ScenarioError(f'population.{role}s.generator.{identifier}: {detail}')
    declaration_schema = Map(segment_fields(role, explicit=True), partial=True)
    known_segments = plan.resolved['population'][f'{role}s']['segments']
    launched_at = _launch_times(plan)
    members = []
    for person_id in expected:
        path = f'population.{role}s.generator.{identifier}.{person_id}'
        diag = Diagnostics()
        checked = declaration_schema.check(produced[person_id], path, diag, complete=False)
        segment_id = checked.get('segment') if isinstance(checked, dict) else None
        base = {}
        if segment_id is not None:
            if segment_id not in known_segments:
                diag.error(f'{path}.segment', f'unknown segment {segment_id!r}')
            else:
                base = known_segments[segment_id]
        merged, missing_keys = _merge_person(role, checked if isinstance(checked, dict) else {}, base)
        for key in missing_keys:
            diag.error(f'{path}.{key}', 'required for a generated person without a segment')
        access = _check_access(role, merged, path, plan.platforms, launched_at, plan.behavior, diag, sampled=False)
        _check_segment_activity(merged['activity'], f'{path}.activity', plan.resolved['world']['zones'], plan.resolved['world'],
                                plan.resolved['activity']['trips']['generator'], plan.resolved['activity']['shifts']['generator'], diag)
        diag.raise_errors()
        members.append({**access, 'id': person_id, 'segment': segment_id, 'initial_scores': merged['initial_scores'],
                        'activity': merged['activity'], 'traits': {role: merged[role], 'evolution': merged['evolution']}})
    return members


def _profile(values):
    return PersonProfile(**(values | {'rider': RiderTraits(**values['rider']), 'driver': DriverTraits(**values['driver']),
                                      'evolution': EvolutionTraits(**values['evolution'])}))


def load_profile(values):
    """Rebuild a typed profile from a realized (JSON) population record."""
    return _profile(values)


def _realize_explicit_people(plan, seed, people, registered_activity=None):
    """`Inputs.explicit(people=...)`: realize a list of `person()`-shaped declarations, each needing a
    `role` key (checked before the per-role schema, since it selects which schema and segment table
    apply), through exactly the same schema-check -> `_merge_person` -> `_check_access` ->
    `_check_segment_activity` -> `_realize_person` path as an explicit or registered-generator person.
    Ids must be unique within their role (matching `plan.person_ids`' own per-role uniqueness -- a
    rider and a driver may share an id).

    `registered_activity`, when given, is filled with `{person_id: activity}` for every supplied person
    with a nonempty merged activity -- the same out-of-band channel `_realize_population` fills for a
    registered population generator's people. A supplied person here is never in `plan.explicit_people`
    or `plan.segments` (it exists only in this call's `people` list), so `_activity_index` cannot recover
    its geometry by id or by segment; without this, `Inputs.explicit`'s caller-supplied `activity` would
    be validated but then silently ignored at realization, exactly like an unvalidated one would be.
    """
    role_field = Scalar('string', choices=ROLES)
    schemas = {role: Map(segment_fields(role, explicit=True), partial=True) for role in ROLES}
    launched_at = _launch_times(plan)
    world_def, activity_def = plan.resolved['world'], plan.resolved['activity']
    seen = {role: set() for role in ROLES}
    for index, item in enumerate(people):
        label = f'people[{index}]'
        if not isinstance(item, dict):
            raise ScenarioError(f'{label}: expected a person mapping, got {item!r}')
        diag = Diagnostics()
        role = item.get('role')
        role_field.check(role, f'{label}.role', diag, complete=True)
        diag.raise_errors()
        checked = schemas[role].check({key: value for key, value in item.items() if key != 'role'}, label, diag, complete=False)
        identity = checked.get('id')
        if not isinstance(identity, str) or not identity or '.' in identity:
            diag.error(f'{label}.id', 'person ids must be nonempty strings without dots')
        elif identity in seen[role]:
            diag.error(f'{label}.id', f'duplicate {role} id {identity!r}')
        base = {}
        segment_id = checked.get('segment')
        if segment_id is not None:
            if segment_id not in plan.segments[role]:
                diag.error(f'{label}.segment', f'unknown segment {segment_id!r}')
            else:
                base = plan.resolved['population'][f'{role}s']['segments'][segment_id]
        merged, missing = _merge_person(role, checked, base)
        for key in missing:
            diag.error(f'{label}.{key}', 'required for an explicit person without a segment')
        access = _check_access(role, merged, label, plan.platforms, launched_at, plan.behavior, diag, sampled=False)
        _check_segment_activity(merged['activity'], f'{label}.activity', world_def['zones'], world_def,
                                activity_def['trips']['generator'], activity_def['shifts']['generator'], diag)
        diag.raise_errors()
        seen[role].add(identity)
        if registered_activity is not None and merged['activity']:
            registered_activity[identity] = merged['activity']
        member = {**access, 'id': identity, 'segment': segment_id, 'initial_scores': merged['initial_scores'],
                 'activity': merged['activity'], 'traits': {role: merged[role], 'evolution': merged['evolution']}}
        yield _realize_person(plan, role, member, seed)


def _sample_point(world, rng):
    width, height = world['map_km']
    if world['sampling'] == 'grid':
        step = world['grid_step_km']
        return [rng.randint(0, math.floor(width / step + 1e-9)) * step, rng.randint(0, math.floor(height / step + 1e-9)) * step]
    return [rng.uniform(0, width), rng.uniform(0, height)]


# ----------------------------------------------------------------------
# Segment/registered geometry: entered only when a rider or driver declares
# `activity`; the map-wide `_sample_point` calls above are never touched by
# any of this, which is how seed-0 stays byte-identical (see `_realize_activity`).
# ----------------------------------------------------------------------

_MAX_GEOMETRY_ATTEMPTS = 100


def _activity_index(plan, people):
    """`{person_id: activity}` for every realized person with a nonempty geometry declaration, from the
    two compile-time sources: explicit people are looked up by id in `plan.explicit_people[role]`
    (already merged with their named segment's `activity`, if any, in `compile_scenario`); segment-
    allocated people are looked up by their assigned segment in `plan.segments[role]`. Built from `plan`
    + `people`, never from a new key on the person records themselves (those flow verbatim into
    snapshots and must not grow a key).

    A registered-population person is neither: it is not in `plan.explicit_people`, and may declare no
    segment at all, so `.get(record['segment'])` here safely yields nothing for one -- `_realize_activity`
    overlays the third source (`registered_activity` from `_realize_population`) on top of this index.
    """
    explicit = {person['id']: person['activity'] for role in ROLES for person in plan.explicit_people[role]}
    index = {}
    for record in people:
        pid = record['id']
        if pid in explicit:
            item = explicit[pid]
        else:
            item = (plan.segments[record['role']].get(record['segment']) or {}).get('activity')
        if item:
            index[pid] = item
    return index


def _clock_at(calendar, at_seconds):
    """`(weekday, hour)` at `at_seconds` after the calendar origin -- the same integer hour-bucket
    convention `_sample_times`/`_peak_weight` use for clock-hour peaks."""
    clock = math.floor(calendar['hour'] + at_seconds / HOUR)
    return int((calendar['weekday'] + clock // 24) % 7), clock % 24


def _zone_box(zones, zone_id):
    box = zones[zone_id]
    return tuple(box['min']), tuple(box['max'])


def _snap_to_grid(value, extent, step):
    """Nearest multiple of `step` inside `[0, extent]`, clamped the same way `_sample_point`'s grid draw is."""
    max_index = math.floor(extent / step + 1e-9)
    return min(max(round(value / step), 0), max_index) * step


def _sample_in_zone(world, box, rng):
    """A point inside the closed `box`; 2 draws, mirroring `_sample_point`'s shape exactly.

    Grid sampling draws uniformly among the multiples of `grid_step_km`
    that lie in the box on each axis independently -- `_check_zones`
    guarantees at least one exists per axis for every declared zone.
    """
    (x0, y0), (x1, y1) = box
    if world['sampling'] == 'grid':
        step = world['grid_step_km']
        lo_x, hi_x = math.ceil(x0 / step - 1e-9), math.floor(x1 / step + 1e-9)
        lo_y, hi_y = math.ceil(y0 / step - 1e-9), math.floor(y1 / step + 1e-9)
        return [rng.randint(lo_x, hi_x) * step, rng.randint(lo_y, hi_y) * step]
    return [rng.uniform(x0, x1), rng.uniform(y0, y1)]


def _pick_zone(weights, rng):
    """One zone id, drawn with `rng.choices` over ids sorted for determinism; `weights` maps id -> weight."""
    names = sorted(weights)
    return rng.choices(names, weights=[weights[name] for name in names], k=1)[0]


def _origin_weights(activity, calendar, at_seconds):
    """`origin_zones` weights at this instant: each baseline weight times the largest multiplier of every
    `spatial_peaks` entry whose zones contain it and whose window contains `(weekday, hour)` -- `max`
    mirrors `_peak_weight`'s aggregation of overlapping windows. `rng.choices` normalizes the result."""
    weekday, hour = _clock_at(calendar, at_seconds)
    weights = {}
    for zone_id, base_weight in activity['origin_zones'].items():
        multiplier = 1
        for item in activity.get('spatial_peaks') or ():
            if zone_id not in item['zones']:
                continue
            if item['start_hour'] < item['end_hour']:
                inside = weekday in item['weekdays'] and item['start_hour'] <= hour < item['end_hour']
            else:  # wraps past midnight into the next weekday, same convention as _peak_weight
                inside = ((weekday in item['weekdays'] and hour >= item['start_hour'])
                          or ((weekday - 1) % 7 in item['weekdays'] and hour < item['end_hour']))
            if inside:
                multiplier = max(multiplier, item['multiplier'])
        weights[zone_id] = base_weight * multiplier
    return weights


def _sample_geometry(world, zones, activity, calendar, at_seconds, rng, context):
    """One rider's origin/destination for a trip whose segment (or explicit person) declares `activity`.

    Fixed draw order:
    1. origin -- `origin_zones` declared: `_pick_zone(_origin_weights(...))` then `_sample_in_zone`;
       otherwise the unchanged map-wide `_sample_point`.
    2. destination, tried up to `_MAX_GEOMETRY_ATTEMPTS` times (steps 2 and 3 share this one budget):
       `destination_zones` declared -> `_pick_zone(destination_zones[origin_zone])` then `_sample_in_zone`;
       elif `distance_km` declared -> `d` is drawn once before the loop, then a fresh direction
       `theta = rng.random() * 2 * math.pi` each attempt, accepting the first `origin + d*(cos, sin)`
       inside `[0, map_km]` on both axes (grid sampling snaps each axis to the nearest grid point after
       acceptance); else the unchanged map-wide `_sample_point`.
    3. redraw (same rule, same budget) while the candidate equals the origin.
    Exhausting the budget raises `ScenarioError` -- a deterministic failure, never an infinite loop.
    """
    origin_zones = activity.get('origin_zones')
    if origin_zones is not None:
        origin_zone = _pick_zone(_origin_weights(activity, calendar, at_seconds), rng)
        origin = _sample_in_zone(world, _zone_box(zones, origin_zone), rng)
    else:
        origin_zone, origin = None, _sample_point(world, rng)
    destination_zones, distance = activity.get('destination_zones'), activity.get('distance_km')
    width, height = world['map_km']
    d = _sample(distance, rng) if distance is not None else None
    for _ in range(_MAX_GEOMETRY_ATTEMPTS):
        if destination_zones is not None:
            candidate = _sample_in_zone(world, _zone_box(zones, _pick_zone(destination_zones[origin_zone], rng)), rng)
        elif d is not None:
            theta = rng.random() * 2 * math.pi
            x, y = origin[0] + d * math.cos(theta), origin[1] + d * math.sin(theta)
            if not (0 <= x <= width and 0 <= y <= height):
                continue
            if world['sampling'] == 'grid':
                step = world['grid_step_km']
                x, y = _snap_to_grid(x, width, step), _snap_to_grid(y, height, step)
            candidate = [x, y]
        else:
            candidate = _sample_point(world, rng)
        if candidate != origin:
            return origin, candidate
    detail = f'{d:.3f} km' if d is not None else 'a destination different from the origin'
    raise ScenarioError(f'activity.trips: no destination at {detail} fits inside map_km from {origin} for {context}')


def _check_session(item, kind, known_ids, path):
    """One realized session dict (trip or shift): exact key set, known person of the right role, finite
    times, JSON-serializable coordinates. Used for every registered-generator session and every
    `Inputs.explicit(sessions=...)` item, so downstream code (the sort and `_check_realized_conflicts`
    at the end of `_realize_activity`, and `main.Simulation`'s scheduler payload) sees one uniform shape
    regardless of where a session came from.

    `known_ids` is `{'rider': set_of_ids, 'driver': set_of_ids}` -- `plan.person_ids` for a registered
    generator, or the supplied people for `Inputs.explicit`.
    """
    if not isinstance(item, dict):
        raise ScenarioError(f'{path}: expected a session mapping, got {item!r}')
    role = 'rider' if kind == 'trip' else 'driver'
    expected = ({'kind', 'id', 'rider', 'at_seconds', 'origin', 'destination'} if kind == 'trip'
               else {'kind', 'id', 'driver', 'at_seconds', 'shift_seconds', 'location'})
    if set(item) != expected:
        raise ScenarioError(f'{path}: expected exactly the keys {sorted(expected)}, got {sorted(item)}')
    if item.get('kind') != kind:
        raise ScenarioError(f"{path}.kind: expected {kind!r}, got {item.get('kind')!r}")
    identity = item['id']
    if not isinstance(identity, str) or not identity or '.' in identity:
        raise ScenarioError(f'{path}.id: session ids must be nonempty strings without dots')
    person_id = item[role]
    if person_id not in known_ids[role]:
        raise ScenarioError(f'{path}.{role}: unknown {role} {person_id!r}')
    at = item['at_seconds']
    if not _is_number(at) or at < 0:
        raise ScenarioError(f'{path}.at_seconds: must be a finite number >= 0, got {at!r}')

    def point(key):
        value = item[key]
        if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(_is_number(v) for v in value):
            raise ScenarioError(f'{path}.{key}: expected an (x, y) pair of finite numbers, got {value!r}')
        return [float(value[0]), float(value[1])]

    if kind == 'trip':
        result = {'kind': kind, 'id': identity, 'rider': person_id, 'at_seconds': at,
                  'origin': point('origin'), 'destination': point('destination')}
    else:
        shift_seconds = item['shift_seconds']
        if shift_seconds is not None and not (_is_number(shift_seconds) and shift_seconds > 0):
            raise ScenarioError(f'{path}.shift_seconds: must be None or a finite positive number, got {shift_seconds!r}')
        result = {'kind': kind, 'id': identity, 'driver': person_id, 'at_seconds': at,
                  'shift_seconds': shift_seconds, 'location': point('location')}
    try:
        canonical(result)
    except (TypeError, ValueError) as error:
        raise ScenarioError(f'{path}: session is not JSON-serializable: {error}') from None
    return result


def _run_registered(family, section, plan, seed, people):
    """Invoke a registered trips/shifts generator and validate every returned item with `_check_session`.

    `definition`/`people` are deep copies so the generator cannot mutate the plan or the realized
    population from here on; `random` is a seed-derived stream keyed by family and identity
    (`derive_seed(seed, ['activity', family, identifier])`), distinct from every other stream, so
    swapping the generator cannot silently reuse another generator's draws. Ids need only be unique
    within this generator's own batch -- the same guarantee `explicit` items get from `Keyed.check`.
    The produced sessions join the same list as the built-in generators, so the sort and
    `_check_realized_conflicts` at the end of `_realize_activity` cover them unchanged.
    """
    identifier = section['implementation']
    implementation = generator_class(family, identifier)
    rng = random.Random(derive_seed(seed, ['activity', family, identifier]))
    produced = implementation.generate(definition=copy.deepcopy(plan.resolved), people=copy.deepcopy(list(people)),
                                       parameters=copy.deepcopy(section['parameters']), random=rng)
    if not isinstance(produced, (list, tuple)):
        raise ScenarioError(f'activity.{family}.registered.{identifier}: generate() must return a list of session dicts')
    known_ids = {'rider': set(plan.person_ids['rider']), 'driver': set(plan.person_ids['driver'])}
    kind = 'trip' if family == 'trips' else 'shift'
    seen, sessions = set(), []
    for index, item in enumerate(produced):
        label = item.get('id') if isinstance(item, dict) else None
        path = f"activity.{family}.registered.{identifier}.{label if isinstance(label, str) and label else index}"
        checked = _check_session(item, kind, known_ids, path)
        if checked['id'] in seen:
            raise ScenarioError(f"{path}: duplicate session id {checked['id']!r}")
        seen.add(checked['id'])
        sessions.append(checked)
    return sessions


def _realize_activity(plan, seed, people, registered_activity=None):
    world, activity = plan.resolved['world'], plan.resolved['activity']
    drivers = [p['id'] for p in people if p['role'] == 'driver']
    riders = [p['id'] for p in people if p['role'] == 'rider']
    # {person_id: activity} for anyone who declared geometry; empty for everyone else, which is how
    # the branches below fall through to the literally unchanged _sample_point lines on the default path.
    geometry = _activity_index(plan, people)
    if registered_activity:
        geometry.update(registered_activity)
    sessions = []
    shifts = activity['shifts']
    rng = random.Random(derive_seed(seed, ['activity', 'shifts']))
    if shifts['generator'] == 'rotation':
        days = shifts['days'] if shifts['days'] is not None else math.ceil(plan.horizon_seconds / 86400)
        for day in range(days):
            for index, driver_id in enumerate(drivers):
                start = (shifts['first_start_hours'] + day * 24 + (index % shifts['crews']) * shifts['shift_hours']) * HOUR
                if start > plan.horizon_seconds:
                    continue
                start_zone = (geometry.get(driver_id) or {}).get('start_zone')
                location = (_sample_point(world, rng) if start_zone is None
                           else _sample_in_zone(world, _zone_box(world['zones'], start_zone), rng))
                sessions.append({'kind': 'shift', 'id': f'shift-{driver_id}-{day + 1}', 'driver': driver_id,
                                 'at_seconds': start, 'shift_seconds': shifts['shift_hours'] * HOUR,
                                 'location': location})
    elif shifts['generator'] == 'explicit':
        for item in shifts['items']:
            sessions.append({'kind': 'shift', 'id': item['id'], 'driver': item['driver'], 'at_seconds': item['at_hours'] * HOUR,
                             'shift_seconds': None if item['hours'] is None else item['hours'] * HOUR,
                             'location': list(item['location'])})
    elif shifts['generator'] == 'registered':
        sessions.extend(_run_registered('shifts', shifts, plan, seed, people))
    trips = activity['trips']
    rng = random.Random(derive_seed(seed, ['activity', 'trips']))
    if trips['generator'] == 'weekly' and riders:
        duration = (trips['duration_hours'] * HOUR if trips['duration_hours'] is not None else plan.horizon_seconds)
        count = round(trips['trips_per_rider'] * len(riders))
        times = _sample_times(count, duration, trips, world['calendar'], rng)
        for index, at in enumerate(times):
            rider_id = riders[index % len(riders)] if trips['assignment'] == 'round_robin' else rng.choice(riders)
            trip_id = f'trip-{index + 1}'
            activity_for_rider = geometry.get(rider_id)
            if not activity_for_rider:
                origin = _sample_point(world, rng)          # unchanged lines, unchanged draw order
                destination = _sample_point(world, rng)
                while destination == origin:
                    destination = _sample_point(world, rng)
            else:
                origin, destination = _sample_geometry(world, world['zones'], activity_for_rider, world['calendar'],
                                                        at, rng, f'{rider_id} ({trip_id})')
            sessions.append({'kind': 'trip', 'id': trip_id, 'rider': rider_id, 'at_seconds': at,
                             'origin': origin, 'destination': destination})
    elif trips['generator'] == 'explicit':
        for item in trips['items']:
            sessions.append({'kind': 'trip', 'id': item['id'], 'rider': item['rider'], 'at_seconds': item['at_hours'] * HOUR,
                             'origin': list(item['origin']), 'destination': list(item['destination'])})
    elif trips['generator'] == 'registered':
        sessions.extend(_run_registered('trips', trips, plan, seed, people))
    sessions.sort(key=lambda s: (s['at_seconds'], s['kind'] != 'shift', s['id']))
    _check_realized_conflicts(sessions, plan)
    return sessions


def _peak_weight(peaks, off_peak, weekday, hour):
    weights = []
    for item in peaks:
        if item['start_hour'] < item['end_hour']:
            inside = weekday in item['weekdays'] and item['start_hour'] <= hour < item['end_hour']
        else:  # wraps past midnight into the next weekday
            inside = ((weekday in item['weekdays'] and hour >= item['start_hour'])
                      or ((weekday - 1) % 7 in item['weekdays'] and hour < item['end_hour']))
        if inside:
            weights.append(item['multiplier'])
    return max(weights, default=off_peak)


def _sample_times(count, duration, trips, calendar, rng):
    """Arrivals weighted by clock hour; partial first/last hours keep their exact width."""
    start_hour, weekday = calendar['hour'], calendar['weekday']
    first = math.floor(start_hour)
    hours = math.ceil(((start_hour - first) * HOUR + duration) / HOUR)
    windows, weights = [], []
    for offset in range(hours):
        clock = first + offset
        left = max(0, (clock - start_hour) * HOUR)
        right = min(duration, (clock + 1 - start_hour) * HOUR)
        if right <= left:
            continue
        windows.append((left, right))
        weights.append((right - left) * _peak_weight(trips['peaks'], trips['off_peak_weight'],
                                                     (weekday + clock // 24) % 7, clock % 24))
    chosen = rng.choices(windows, weights=weights, k=count)
    return sorted(left + rng.random() * (right - left) for left, right in chosen)


def _check_realized_conflicts(sessions, plan):
    """Static conflicts only: overlapping declared shift windows and same-second trips per rider."""
    errors = []
    last_shift, last_trip = {}, {}
    for item in sessions:
        if item['kind'] == 'shift':
            previous = last_shift.get(item['driver'])
            if previous is not None and (previous[1] is None or item['at_seconds'] < previous[1]):
                errors.append(f"activity.shifts: {item['id']} starts while {previous[0]} is still scheduled for {item['driver']}")
            end = None if item['shift_seconds'] is None else item['at_seconds'] + item['shift_seconds']
            last_shift[item['driver']] = (item['id'], end)
        else:
            previous = last_trip.get(item['rider'])
            if previous is not None and previous[1] == item['at_seconds']:
                errors.append(f"activity.trips: {item['id']} and {previous[0]} start at the same time for {item['rider']}")
            last_trip[item['rider']] = (item['id'], item['at_seconds'])
    if errors:
        raise ScenarioError('\n'.join(errors))
