"""Shared policy values: immutable observations and identity-keyed randomness."""
import hashlib
import json
import math
from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType
from collections.abc import Mapping


def finite_number(value, name, minimum=0, maximum=None, strictly_positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (strictly_positive and value == 0)
            or (maximum is not None and value > maximum)):
        raise ValueError(f"Invalid {name}: {value!r}")


def positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


class Observation(Mapping):
    """Recursively immutable detached record; no engine or mutable world reference."""
    __slots__ = ('_data',)

    def __init__(self, values):
        object.__setattr__(self, '_data', MappingProxyType({k: freeze(v) for k, v in values.items()}))

    def __setattr__(self, name, value):
        raise TypeError('Observations are immutable')

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def freeze(value):
    if is_dataclass(value):
        values = {f.name: getattr(value, f.name) for f in fields(value)}
        values.update({name: getattr(value, name) for name, descriptor in vars(type(value)).items()
                       if isinstance(descriptor, property)})
        return Observation(values)
    if isinstance(value, Mapping):
        return Observation(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(v) for v in value)
    return value


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


@dataclass(frozen=True)
class RandomValues:
    seed: int
    identity: tuple

    def uniform(self, purpose):
        key = json.dumps([self.seed, self.identity, purpose], separators=(',', ':'), allow_nan=False)
        bits = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big') >> 11
        return (bits + 0.5) / (2 ** 53)


@dataclass(frozen=True)
class Declaration:
    name: str
    version: str
    parameter_schema: type
    inputs: tuple
    outputs: tuple
    memory_version: int
    hooks: tuple
    memory_schema: tuple = ()  # (field name, JSON value type); empty means stateless
    observations: tuple = ()  # platform-audience notification kinds an `observe` hook may request


@dataclass(frozen=True)
class Decision:
    action: object
    memory: dict
    explanation: str

    def __post_init__(self):
        json.dumps(self.memory, allow_nan=False)


@dataclass(frozen=True)
class Wait:
    seconds: float

    def __post_init__(self):
        finite_number(self.seconds, 'wait seconds', strictly_positive=True)


@dataclass(frozen=True)
class Stop:
    reason: str


@dataclass(frozen=True)
class Cancel:
    order_id: int
    party: str
    reason: str
    rider_fee_minor: int = 0
    driver_compensation_minor: int = 0
    driver_penalty_minor: int = 0


@dataclass(frozen=True)
class Transfer:
    """A proposal to post money outside a ride. amount_minor is signed; positive credits the person.

    The reason vocabulary is intentionally not checked here: this module
    imports nothing from marketplace_engine.py (only hashlib/json/math and
    dataclass helpers), and importing TRANSFER_REASONS would create a
    cycle. marketplace_engine.post_transfer is the single authority on
    reasons; this contract only proves the proposal's shape.
    """
    reason: str
    role: str
    person_id: object
    amount_minor: int
    platform_id: str | None = None
    counterparty: str = 'platform'
    order_id: int | None = None
    program_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError('A transfer needs a reason')
        if self.role not in ('rider', 'driver'):
            raise ValueError('A transfer names a rider or a driver')
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int) or self.amount_minor == 0:
            raise ValueError('Transfer amount must be a nonzero integer of minor units')
        if self.counterparty not in ('platform', 'external'):
            raise ValueError('Unknown transfer counterparty')
        if self.counterparty == 'external' and self.platform_id is not None:
            raise ValueError('An external transfer has no platform side')
        if self.program_id is not None and (not isinstance(self.program_id, str) or not self.program_id):
            raise ValueError('program_id must be a nonempty string')
