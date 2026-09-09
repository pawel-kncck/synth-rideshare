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
INTERVENTION_ORDER = {'launch': 0, 'policy': 1, 'preference': 2}


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


def segment_fields(role, *, explicit):
    fields_ = {
        'apps': APPS, 'preferred_app': Scalar('string'), 'accounts': Seq(Scalar('string'), nullable=True),
        'awareness': APPS, 'disclosed_to': APPS,
        'initial_scores': Table(Scalar('number', minimum=-2, maximum=2)),
        role: Params(TRAITS[role], sampled=not explicit, partial=True),
        'evolution': Params(EvolutionTraits, sampled=not explicit, partial=True),
    }
    if role == 'driver':
        fields_['registrations'] = Seq(Scalar('string'), nullable=True)
    if explicit:
        return {'id': Scalar('string'), 'segment': Scalar('string', nullable=True), **fields_}
    return {'weight': Scalar('number', minimum=0, maximum=1), **fields_}


def population_spec(role):
    return Map({'count': Scalar('integer', minimum=0),
                'segments': Table(Map(segment_fields(role, explicit=False))),
                'people': Keyed(Map(segment_fields(role, explicit=True), partial=True))})


CAMPAIGN = Map({
    'id': Scalar('string'), 'start_hours': NONNEGATIVE_HOURS, 'end_hours': POSITIVE_HOURS,
    'discount_minor': Scalar('integer', minimum=0), 'discount_fraction': Scalar('number', minimum=0, maximum=1),
    'discount_cap_minor': Scalar('integer', minimum=0, nullable=True), 'bonus_minor': Scalar('integer', minimum=0),
    'segment': Scalar('string', nullable=True), 'new_user_only': Scalar('boolean'),
    'awareness': Scalar('string', choices=('announced', 'in_app')),
})
RULE = Map({'id': Scalar('string'), 'priority': Scalar('integer'), 'when': Table(Scalar('value')),
            'parameters': Params(MarketplaceParameters, partial=True)})
POLICY_EXTRA = {'version': Scalar('string'), 'rules': Keyed(RULE), 'campaigns': Keyed(CAMPAIGN), 'fallback': Scalar('string')}
PLATFORM = Map({
    'launched': Scalar('boolean'),
    'policy': Implementation('marketplace', POLICY_EXTRA),
    'controller': Map({'interval_hours': POSITIVE_HOURS, 'until_hours': POSITIVE_HOURS}, nullable=True),
})
PEAK = Map({'id': Scalar('string'), 'weekdays': Seq(Scalar('integer', minimum=0, maximum=6)),
            'start_hour': Scalar('integer', minimum=0, maximum=23), 'end_hour': Scalar('integer', minimum=0, maximum=24),
            'multiplier': Scalar('number', minimum=1)})
SHIFTS = Choice('generator', {
    'rotation': Map({'crews': Scalar('integer', minimum=1), 'shift_hours': POSITIVE_HOURS,
                     'days': Scalar('integer', minimum=1, nullable=True), 'first_start_hours': NONNEGATIVE_HOURS}),
    'explicit': Map({'items': Keyed(Map({'id': Scalar('string'), 'driver': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS,
                                         'hours': Scalar('number', positive=True, nullable=True), 'location': Point()}))}),
    'none': Map({}),
})
TRIPS = Choice('generator', {
    'weekly': Map({'trips_per_rider': Scalar('number', positive=True), 'duration_hours': Scalar('number', positive=True, nullable=True),
                   'peaks': Keyed(PEAK), 'off_peak_weight': Scalar('number', positive=True),
                   'assignment': Scalar('string', choices=('round_robin', 'random'))}),
    'explicit': Map({'items': Keyed(Map({'id': Scalar('string'), 'rider': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS,
                                         'origin': Point(), 'destination': Point()}))}),
    'none': Map({}),
})
INTERVENTION = Choice('kind', {
    'launch': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS, 'platform': Scalar('string')}),
    'policy': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS, 'platform': Scalar('string'),
                   'policy': Implementation('marketplace', POLICY_EXTRA, partial=True)}),
    'preference': Map({'id': Scalar('string'), 'at_hours': NONNEGATIVE_HOURS, 'role': Scalar('string', choices=ROLES),
                       'segment': Scalar('string', nullable=True), 'people': APPS, 'preferred_app': Scalar('string')}),
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
             bonus_minor=0, segment=None, new_user_only=False, awareness='announced'):
    return _build(CAMPAIGN, {'id': id, 'start_hours': start_hours, 'end_hours': end_hours, 'discount_minor': discount_minor,
                             'discount_fraction': discount_fraction, 'discount_cap_minor': discount_cap_minor,
                             'bonus_minor': bonus_minor, 'segment': segment, 'new_user_only': new_user_only,
                             'awareness': awareness}, f'campaign {id}')


def rule(id, *, priority, when, parameters):
    """A conditional policy override, validated against today's VISIBLE_FIELDS at authoring time.

    `ConditionalRule.__post_init__` enforces the same two conditions at
    `compile_scenario` time; checking them here surfaces the offending
    field names immediately instead of after a full scenario resolves. The
    visible-field set is `marketplace_policy.VISIBLE_FIELDS`; plan section
    4.H adds zone and window keys to it in a later phase, so `rule()` reads
    the set rather than duplicating it -- today it is exactly VISIBLE_FIELDS.
    """
    checked = _build(RULE, {'id': id, 'priority': priority, 'when': dict(when), 'parameters': dict(parameters)}, f'rule {id}')
    unknown = sorted(set(checked['when']) - VISIBLE_FIELDS)
    if not checked['when'] or unknown:
        raise ScenarioError(f'rule {id}.when: conditions must use declared platform-visible fields '
                            f'{sorted(VISIBLE_FIELDS)}' + (f'; got {unknown}' if unknown else '; got none'))
    unknown = sorted(set(checked['parameters']) - {f.name for f in fields(MarketplaceParameters)})
    if unknown:
        raise ScenarioError(f'rule {id}.parameters: unknown policy parameter(s) {unknown}')
    return checked


def peak(id, *, weekdays, start_hour, end_hour, multiplier):
    return _build(PEAK, {'id': id, 'weekdays': list(weekdays), 'start_hour': start_hour, 'end_hour': end_hour,
                         'multiplier': multiplier}, f'peak {id}')


def segment(*, weight, apps, preferred_app, accounts=None, registrations=None, awareness=None, disclosed_to=(),
            initial_scores=None, rider=None, driver=None, evolution=None):
    """A population segment: correlated access and trait bundle. Omit `registrations` for riders."""
    value = {'weight': weight, 'apps': list(apps), 'preferred_app': preferred_app,
             'accounts': None if accounts is None else list(accounts),
             'awareness': list(apps if awareness is None else awareness), 'disclosed_to': list(disclosed_to),
             'initial_scores': dict(initial_scores or {}), 'evolution': dict(evolution or {})}
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
}
ALL_APPS = ['rebu', 'blot', 'flyt']


def _platform_v1():
    return {'launched': True, 'controller': None,
            'policy': {'implementation': 'marketplace@1', 'version': 'modern-v1',
                       'parameters': dict(MARKETPLACE_DEFAULTS_V1), 'rules': [], 'campaigns': [], 'fallback': 'default'}}


def _segments_v1(role):
    """Mixed ownership: multi-app majorities with different first choices plus single-app users."""
    trait = 'rider' if role == 'rider' else 'driver'
    mix = [('rebu-first', .4, ALL_APPS, 'rebu'), ('blot-first', .25, ALL_APPS, 'blot'),
           ('flyt-first', .15, ALL_APPS, 'flyt'), ('rebu-only', .2, ['rebu'], 'rebu')]
    result = {}
    for name, weight, apps, preferred in mix:
        item = {'weight': weight, 'apps': list(apps), 'preferred_app': preferred, 'accounts': None,
                'awareness': list(ALL_APPS), 'disclosed_to': [], 'initial_scores': {}, trait: {}, 'evolution': {}}
        if role == 'driver':
            item['registrations'] = None
        result[name] = item
    return result


def _base_v1(name, *, horizon_hours, calendar):
    return {
        'name': name, 'schema_version': SCHEMA_VERSION, 'preset': None, 'calibration': 'synthetic', 'notes': {},
        'world': {'speed_kmh': 30, 'boarding_seconds': 30, 'minor_units_per_major': 100, 'map_km': [10, 10],
                  'sampling': 'grid', 'grid_step_km': 1, 'calendar': dict(calendar), 'horizon_hours': horizon_hours},
        'platforms': {app: _platform_v1() for app in ALL_APPS},
        'behavior': {'rider': {'implementation': 'rider_search@1', 'parameters': dict(RIDER_DEFAULTS_V1)},
                     'driver': {'implementation': 'driver_participation@1', 'parameters': dict(DRIVER_DEFAULTS_V1)},
                     'evolution': {'implementation': 'personal_evolution@1', 'parameters': dict(EVOLUTION_DEFAULTS_V1)}},
        'population': {'riders': {'count': 0, 'segments': _segments_v1('rider'), 'people': []},
                       'drivers': {'count': 0, 'segments': _segments_v1('driver'), 'people': []}},
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


def platform(id, *, launched=True, version='modern-v1', rules=(), campaigns=(), fallback='default',
            controller=None, **parameters):
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
    `compile_scenario` still re-validates cross-field combinations one
    platform cannot see alone, such as `max_local_commitments > 2`.
    """
    diag = Diagnostics()
    if not isinstance(id, str) or not id or '.' in id:
        diag.error('platform', 'IDs must be nonempty strings without dots')
    entry = {'launched': launched, 'controller': controller,
             'policy': {'implementation': BUILTIN_IMPLEMENTATIONS['marketplace'], 'version': version,
                        'parameters': {**MARKETPLACE_DEFAULTS_V1, **parameters},
                        'rules': list(rules), 'campaigns': list(campaigns), 'fallback': fallback}}
    checked = PLATFORM.check(entry, f'platform {id}', diag, complete=True)
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

    def manifest(self):
        """Everything needed to reconstruct and identify this plan without today's defaults."""
        return {'name': self.name, 'preset': self.resolved['preset'], 'calibration': self.resolved['calibration'],
                'notes': copy.deepcopy(self.resolved['notes']),
                'schema_version': SCHEMA_VERSION, 'compiler_version': COMPILER_VERSION,
                'seed_derivation_version': SEED_DERIVATION_VERSION, 'horizon_seconds': self.horizon_seconds,
                'fingerprints': dict(self.fingerprints), 'implementations': copy.deepcopy(self.implementations),
                'resolved': copy.deepcopy(self.resolved), 'provenance': dict(self.provenance),
                'interventions': copy.deepcopy(list(self.interventions)), 'checkpoints': list(self.checkpoints),
                'warnings': list(self.warnings)}

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
    horizon = world_def['horizon_hours'] * HOUR
    platforms = {}
    launched_at = {}
    for pid, item in resolved['platforms'].items():
        launched_at[pid] = 0 if item['launched'] else None
        policy = _compile_policy(item['policy'], f'platforms.{pid}.policy', diag)
        controller = None
        if item['controller'] is not None:
            controller = (item['controller']['interval_hours'] * HOUR, item['controller']['until_hours'] * HOUR)
            if controller[1] > horizon:
                diag.warn(f'platforms.{pid}.controller.until_hours', 'extends beyond the horizon')
        platforms[pid] = PlatformPlan(item['launched'], item['policy']['implementation'], policy, controller)
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
    interventions = []
    for item in resolved['interventions']:
        at = item['at_hours'] * HOUR
        path = f"interventions.{item['id']}"
        entry = {'id': item['id'], 'kind': item['kind'], 'at_seconds': at}
        if at > horizon:
            diag.warn(path, 'scheduled after the horizon; it will never apply')
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
                base = resolved['platforms'][pid]['policy']
                if item['policy'].get('implementation', base['implementation']) != base['implementation']:
                    diag.error(f'{path}.policy.implementation', 'an intervention selects a policy version, not a different implementation')
                merged = Implementation('marketplace', POLICY_EXTRA).merge(base, item['policy'])
                entry['policy'] = plain(_compile_policy(merged, f'{path}.policy', diag))
                if merged.get('version') == base['version'] and merged != base:
                    diag.warn(f'{path}.policy.version', 'changed settings should carry a new policy version label')
        else:
            entry.update({'role': item['role'], 'segment': item['segment'], 'people': list(item['people']),
                          'preferred_app': item['preferred_app']})
            if (item['segment'] is None) == (not item['people']):
                diag.error(path, 'a preference change targets either one segment or an explicit people list')
        interventions.append(entry)
    interventions.sort(key=lambda e: (e['at_seconds'], INTERVENTION_ORDER[e['kind']], e['id']))
    seen = {}
    for entry in interventions:
        target = ((entry['at_seconds'], entry['kind'], entry['platform']) if entry['kind'] != 'preference'
                  else (entry['at_seconds'], 'preference', entry['role'], entry['segment'], tuple(entry['people'])))
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
            merged = {key: value for key, value in base.items() if key not in ('weight', role, 'evolution', 'initial_scores')}
            merged.update({key: value for key, value in item.items() if key not in ('id', 'segment', role, 'evolution', 'initial_scores')})
            for key in ('apps', 'preferred_app'):
                if key not in merged:
                    diag.error(f'{label}.{key}', 'required for an explicit person without a segment')
                    merged[key] = [] if key == 'apps' else ''
            merged.setdefault('awareness', list(merged['apps']))
            merged.setdefault('disclosed_to', [])
            merged.setdefault('accounts', None)
            if role == 'driver':
                merged.setdefault('registrations', None)
            merged[role] = {**base.get(role, {}), **item.get(role, {})}
            merged['evolution'] = {**base.get('evolution', {}), **item.get('evolution', {})}
            merged['initial_scores'] = {**base.get('initial_scores', {}), **item.get('initial_scores', {})}
            access = _check_access(role, merged, label, platforms, launched_at, behavior, diag, sampled=False)
            explicit_people[role].append({**access, 'id': item['id'], 'segment': item['segment'],
                                          'initial_scores': merged['initial_scores'],
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
    diag.raise_errors()
    # Controlled inputs: identities, segment assignment, sampled trait draws and
    # schedules depend only on these sections and the seed, never on treatments.
    controls = {'world': resolved['world'], 'population': resolved['population'], 'activity': resolved['activity'],
                'seed_derivation_version': SEED_DERIVATION_VERSION}
    fingerprints = {'definition': fingerprint(resolved), 'controls': fingerprint(controls),
                    'implementation': implementation_fingerprint(implementations)}
    fingerprints['plan'] = fingerprint([fingerprints['definition'], fingerprints['implementation'], COMPILER_VERSION])
    return Plan(resolved, provenance, tuple(diag.warnings), world, horizon, platforms, implementations, behavior,
                segments, explicit_people, person_ids, checkpoints, tuple(interventions), fingerprints)


def implementation_fingerprint(implementations):
    source_dir = Path(__file__).resolve().parent
    sources = {name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    return fingerprint({'sources': sources, 'implementations': implementations, 'compiler': COMPILER_VERSION})


def _compile_policy(definition, path, diag):
    campaigns = []
    for item in definition['campaigns']:
        campaigns.append({key: value for key, value in item.items() if key not in ('start_hours', 'end_hours')}
                         | {'start': item['start_hours'] * HOUR, 'end': item['end_hours'] * HOUR})
    try:
        return PlatformPolicy.compile(overrides=definition['parameters'], rules=definition['rules'], campaigns=campaigns,
                                      version=definition['version'], fallback=definition['fallback'])
    except (TypeError, ValueError) as error:
        diag.error(path, str(error))
        return None


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


def prepare_inputs(plan, seed=0):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScenarioError('seed: must be an integer')
    people = tuple(_realize_population(plan, seed))
    sessions = tuple(_realize_activity(plan, seed, people))
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


def _realize_population(plan, seed):
    resolved = plan.resolved
    for role in ROLES:
        section = resolved['population'][f'{role}s']
        rng = random.Random(derive_seed(seed, ['population', role]))
        weights = {sid: item['weight'] for sid, item in plan.segments[role].items()}
        assignment = _allocate(section['count'], weights, rng) if section['count'] else []
        members = [{**plan.segments[role][sid], 'id': f'{role}-{index + 1}', 'segment': sid}
                   for index, sid in enumerate(assignment)] + list(plan.explicit_people[role])
        for member in members:
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
            yield record


def _profile(values):
    return PersonProfile(**(values | {'rider': RiderTraits(**values['rider']), 'driver': DriverTraits(**values['driver']),
                                      'evolution': EvolutionTraits(**values['evolution'])}))


def load_profile(values):
    """Rebuild a typed profile from a realized (JSON) population record."""
    return _profile(values)


def _sample_point(world, rng):
    width, height = world['map_km']
    if world['sampling'] == 'grid':
        step = world['grid_step_km']
        return [rng.randint(0, math.floor(width / step + 1e-9)) * step, rng.randint(0, math.floor(height / step + 1e-9)) * step]
    return [rng.uniform(0, width), rng.uniform(0, height)]


def _realize_activity(plan, seed, people):
    world, activity = plan.resolved['world'], plan.resolved['activity']
    drivers = [p['id'] for p in people if p['role'] == 'driver']
    riders = [p['id'] for p in people if p['role'] == 'rider']
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
                sessions.append({'kind': 'shift', 'id': f'shift-{driver_id}-{day + 1}', 'driver': driver_id,
                                 'at_seconds': start, 'shift_seconds': shifts['shift_hours'] * HOUR,
                                 'location': _sample_point(world, rng)})
    elif shifts['generator'] == 'explicit':
        for item in shifts['items']:
            sessions.append({'kind': 'shift', 'id': item['id'], 'driver': item['driver'], 'at_seconds': item['at_hours'] * HOUR,
                             'shift_seconds': None if item['hours'] is None else item['hours'] * HOUR,
                             'location': list(item['location'])})
    trips = activity['trips']
    rng = random.Random(derive_seed(seed, ['activity', 'trips']))
    if trips['generator'] == 'weekly' and riders:
        duration = (trips['duration_hours'] * HOUR if trips['duration_hours'] is not None else plan.horizon_seconds)
        count = round(trips['trips_per_rider'] * len(riders))
        times = _sample_times(count, duration, trips, world['calendar'], rng)
        for index, at in enumerate(times):
            rider_id = riders[index % len(riders)] if trips['assignment'] == 'round_robin' else rng.choice(riders)
            origin = _sample_point(world, rng)
            destination = _sample_point(world, rng)
            while destination == origin:
                destination = _sample_point(world, rng)
            sessions.append({'kind': 'trip', 'id': f'trip-{index + 1}', 'rider': rider_id, 'at_seconds': at,
                             'origin': origin, 'destination': destination})
    elif trips['generator'] == 'explicit':
        for item in trips['items']:
            sessions.append({'kind': 'trip', 'id': item['id'], 'rider': item['rider'], 'at_seconds': item['at_hours'] * HOUR,
                             'origin': list(item['origin']), 'destination': list(item['destination'])})
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
