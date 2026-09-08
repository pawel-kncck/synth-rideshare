"""The shared physical market and its contractual mechanics.

One market owns riders, drivers, cars, and positions. Platforms are
marketplace instances inside it: each has its own app sessions, quotes,
orders, offers, and observations, but none owns a copy of a person, a car,
or a location. The engine owns the authoritative records, actual movement,
accepted commitments, legal transitions, scoped observation delivery, and
settlement records. It decides what is physically and contractually
possible; platform and participant policies decide what to ask for.

Policies act through commands (`start_shift`, `open_app`, `begin_intent`,
`issue_quote`, `place_order`, `create_offer`, `respond_to_offer`,
`cancel_order`, `end_shift`, ...). A command validates, applies one coherent
transition, and only then publishes scoped notifications. Physical progress
(pickup arrival, boarding, drop-off) and offer expiry are domain events on
the generic scheduler in `event_engine.py`; their payloads name records by
id and generation, so a stale event is ignored rather than acted upon.
Design notes live in plans/architecture/marketplace-engine.md.
"""

import copy
import itertools
import math
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Any, Mapping, NamedTuple, Optional

from policy_contracts import freeze

MAX_COMMITMENTS = 2  # accepted, unfinished orders per driver across all platforms
SNAPSHOT_SCHEMA_VERSION = 2
ROLES = ("rider", "driver")
CANCELLATION_PARTIES = ("rider", "driver", "platform")
OFFER_DISPOSITIONS = ("accepted", "rejected", "expired", "canceled", "acceptance_failed")


class CommandRejected(ValueError):
    """A command violated access, lifecycle, or value rules. Nothing changed."""


# ----------------------------------------------------------------------
# Values
# ----------------------------------------------------------------------

def point(value, name="point"):
    """A finite (x, y) pair of kilometres."""
    try:
        x, y = value
        result = (float(x), float(y))
    except (TypeError, ValueError):
        raise CommandRejected(f"{name} must be an (x, y) pair of kilometres") from None
    if not all(math.isfinite(coordinate) for coordinate in result):
        raise CommandRejected(f"{name} must be finite")
    return result


def minor_units(value, name, minimum=0):
    """An integer amount of minor currency units (cents), never a float."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandRejected(f"{name} must be an integer number of minor currency units")
    if value < minimum:
        raise CommandRejected(f"{name} must be at least {minimum}")
    return value


def finite(value, name, minimum=None, strictly_positive=False, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CommandRejected(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise CommandRejected(f"{name} must be at least {minimum:g}")
    if strictly_positive and value <= 0:
        raise CommandRejected(f"{name} must be positive")
    return value


def to_minor_units(amount, units_per_major=100):
    """Round a major-unit amount to integer minor units, half up."""
    scaled = Decimal(str(amount)) * units_per_major
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class World:
    """Configurable physical inputs. Legality of occupancy does not depend on them."""

    speed_kmh: float = 30.0
    boarding_seconds: float = 30.0
    minor_units_per_major: int = 100

    def __post_init__(self):
        finite(self.speed_kmh, "speed_kmh", strictly_positive=True)
        finite(self.boarding_seconds, "boarding_seconds", minimum=0)
        if (isinstance(self.minor_units_per_major, bool) or not isinstance(self.minor_units_per_major, int)
                or self.minor_units_per_major < 1):
            raise CommandRejected("minor_units_per_major must be a positive integer")

    def travel_seconds(self, distance_km):
        return distance_km / self.speed_kmh * 3600

    def to_minor(self, amount):
        return to_minor_units(amount, self.minor_units_per_major)


@dataclass(frozen=True)
class Motion:
    """One straight-line trajectory. Position is interpolated at query time."""

    origin: tuple
    destination: tuple
    started_at: float
    arrives_at: float

    @classmethod
    def idle(cls, location, at):
        return cls(location, location, at, at)

    @property
    def moving(self):
        return self.arrives_at > self.started_at

    def position_at(self, at_seconds):
        if at_seconds >= self.arrives_at:
            return self.destination
        if at_seconds <= self.started_at:
            return self.origin
        share = (at_seconds - self.started_at) / (self.arrives_at - self.started_at)
        return (
            self.origin[0] + (self.destination[0] - self.origin[0]) * share,
            self.origin[1] + (self.destination[1] - self.origin[1]) * share,
        )


@dataclass(frozen=True)
class FareTerms:
    """Quoted rider terms, frozen at quote time and bound by the order."""

    gross_minor: int
    discount_minor: int = 0

    def __post_init__(self):
        minor_units(self.gross_minor, "gross_minor")
        minor_units(self.discount_minor, "discount_minor")
        if self.discount_minor > self.gross_minor:
            raise CommandRejected("discount_minor cannot exceed gross_minor")

    @property
    def rider_payment_minor(self):
        return self.gross_minor - self.discount_minor


@dataclass(frozen=True)
class PayoutTerms:
    """Offered driver terms, frozen at offer time and bound by acceptance."""

    payout_minor: int
    bonus_minor: int = 0

    def __post_init__(self):
        minor_units(self.payout_minor, "payout_minor")
        minor_units(self.bonus_minor, "bonus_minor")

    @property
    def driver_payout_minor(self):
        return self.payout_minor + self.bonus_minor


# ----------------------------------------------------------------------
# Authoritative records. They refer to each other by id, never by object.
# ----------------------------------------------------------------------

@dataclass
class Platform:
    id: str
    name: str
    launched: bool = True
    open_driver_ids: set = field(default_factory=set)
    open_rider_ids: set = field(default_factory=set)


@dataclass
class Car:
    id: Any
    registrations: set
    driver_id: Any = None  # the one controlling driver
    motion: Optional[Motion] = None  # None until it has ever been placed


@dataclass
class Shift:
    """A driver's physical presence in the market, independent of any app."""

    id: int
    driver_id: Any
    started_at: float
    location: tuple
    exit_requested_at: Optional[float] = None
    ended_at: Optional[float] = None


@dataclass
class Driver:
    id: Any
    apps: set  # installed platforms
    car_id: Any
    open_apps: set = field(default_factory=set)
    shift_id: Optional[int] = None
    commitments: list = field(default_factory=list)  # accepted order ids in service order
    service_id: Optional[int] = None  # the physical service currently performed
    accounts: set = field(default_factory=set)


@dataclass
class Rider:
    id: Any
    apps: set
    location: Optional[tuple] = None  # where they are when not onboard
    open_apps: set = field(default_factory=set)
    intent_id: Optional[int] = None  # the live trip intent
    service_id: Optional[int] = None  # the ride they are onboard
    accounts: set = field(default_factory=set)


@dataclass
class TripIntent:
    """One rider's wish to travel, retained across platform attempts."""

    id: int
    rider_id: Any
    origin: tuple
    destination: tuple
    created_at: float
    quote_ids: list = field(default_factory=list)
    order_ids: list = field(default_factory=list)
    live_order_id: Optional[int] = None
    converted_at: Optional[float] = None  # first order, counted once
    ended_at: Optional[float] = None
    outcome: Optional[str] = None  # completed or abandoned
    reason: Optional[str] = None

    @property
    def live(self):
        return self.ended_at is None


@dataclass
class Quote:
    id: int
    platform_id: str
    intent_id: int
    rider_id: Any
    at: float
    fare: FareTerms
    distance_km: float
    duration_seconds: float
    eta_seconds: Optional[float]  # None means the platform offered no supply
    expires_at: float
    policy_version: str
    selected_rule: str
    campaign_id: Optional[str] = None

    @property
    def drivers_available(self):
        return self.eta_seconds is not None


@dataclass(frozen=True)
class Assignment:
    driver_id: Any
    car_id: Any
    offer_id: int
    accepted_at: float
    payout: PayoutTerms


@dataclass
class Order:
    """A platform's attempt to serve an intent, from quote acceptance to its end."""

    id: int
    platform_id: str
    intent_id: int
    rider_id: Any
    pickup: tuple
    destination: tuple
    quote_id: int
    fare: FareTerms
    created_at: float
    state: str = "open"  # open, assigned, completed, canceled
    assignment: Optional[Assignment] = None
    offer_ids: list = field(default_factory=list)
    service_id: Optional[int] = None
    timeline: dict = field(default_factory=dict)
    cancellation: Optional[dict] = None
    settlement_ids: list = field(default_factory=list)
    eta_predictions: list = field(default_factory=list)

    @property
    def terminal(self):
        return self.state in ("completed", "canceled")

    @property
    def phase(self):
        """What the owning platform legitimately knows about progress."""
        if self.terminal:
            return self.state
        if self.assignment is None:
            return "open"
        if "boarded" in self.timeline:
            return "onboard"
        if "arrived" in self.timeline:
            return "arrived"
        return "accepted"


@dataclass
class Offer:
    id: int
    platform_id: str
    order_id: int
    driver_id: Any
    created_at: float
    expires_at: float
    payout: PayoutTerms
    eta_seconds: Optional[float]  # the platform's displayed estimate
    state: str = "pending"
    resolved_at: Optional[float] = None
    reason: Optional[str] = None
    policy_version: str = 'direct-v1'
    selected_rule: str = 'default'
    campaign_id: Optional[str] = None


@dataclass(frozen=True)
class Leg:
    kind: str  # pickup, boarding, transport
    origin: tuple
    destination: tuple
    started_at: float
    planned_end: float


@dataclass
class Service:
    """Actual pickup travel, boarding, and transport for one order."""

    id: int
    order_id: int
    platform_id: str
    driver_id: Any
    car_id: Any
    rider_id: Any
    started_at: float
    phase: str = "pickup"  # pickup, boarding, transport, ended
    legs: list = field(default_factory=list)
    arrived_at: Optional[float] = None
    boarded_at: Optional[float] = None
    ended_at: Optional[float] = None
    end_reason: Optional[str] = None
    end_position: Optional[tuple] = None


@dataclass(frozen=True)
class Settlement:
    id: int
    at: float
    order_id: int
    platform_id: str
    rider_id: Any
    driver_id: Any
    reason: str  # completed_ride or cancellation_fee
    rider_payment_minor: int
    driver_payout_minor: int
    platform_contribution_minor: int  # the residual, so conservation is exact


@dataclass(frozen=True)
class Notification:
    """A scoped observation. Its data holds only what the audience may know."""

    at: float
    audience: str  # platform, driver, or rider
    audience_id: Any
    kind: str
    data: Mapping[str, Any]


# ----------------------------------------------------------------------
# Scoped views
# ----------------------------------------------------------------------

class DriverPresence(NamedTuple):
    """A driver as one platform sees them: position, availability, own work.

    A lightweight tuple: platforms scan every open driver on each quote and
    dispatch, so this is the hottest object in a large run.
    """

    driver_id: Any
    position: tuple
    accepting: bool
    own_order_ids: tuple  # this platform's accepted, unfinished orders in acceptance order
    pending_offer_ids: tuple  # this platform's unresolved offers to the driver


@dataclass(frozen=True)
class RiderRequest:
    rider_id: Any
    intent_id: int
    origin: tuple
    destination: tuple


class PlatformView:
    """Read-only access to one platform's own records and authorized observations.

    It never reveals which platform a driver is physically serving, another
    platform's destinations, the hidden commitment count, or when a driver
    actually becomes free. Position observations are the same physical
    coordinates every platform sees at the same time.
    """

    def __init__(self, engine, platform_id):
        self._engine = engine
        self.platform_id = platform_id
        self._platform = engine.platforms[platform_id]

    @property
    def now(self):
        return self._engine.now

    def drivers(self):
        """Drivers with this app open on shift, with their observed position."""
        engine, platform_id, now = self._engine, self.platform_id, self._engine.now
        drivers, cars, shifts = engine.drivers, engine.cars, engine.shifts
        orders, offers, pending_by_driver = engine.orders, engine.offers, engine._pending_by_driver
        for driver_id in self._platform.open_driver_ids:
            driver = drivers[driver_id]
            commitments = driver.commitments
            pending = pending_by_driver.get(driver_id)
            yield DriverPresence(
                driver_id,
                cars[driver.car_id].motion.position_at(now),
                # An open app already implies a shift, a registered car, and a
                # launched platform; only an exit request withdraws the driver.
                shifts[driver.shift_id].exit_requested_at is None,
                tuple(order_id for order_id in commitments
                      if orders[order_id].platform_id == platform_id) if commitments else (),
                tuple(offer_id for offer_id in pending
                      if offers[offer_id].platform_id == platform_id) if pending else (),
            )

    def rider_requests(self):
        """Riders with this app open and a live trip intent."""
        for rider_id in self._platform.open_rider_ids:
            request = self.rider_request(rider_id)
            if request is not None:
                yield request

    def rider_request(self, rider_id):
        rider = self._engine.riders[rider_id]
        if self.platform_id not in rider.open_apps or rider.intent_id is None:
            return None
        intent = self._engine.intents[rider.intent_id]
        return RiderRequest(rider_id, intent.id, intent.origin, intent.destination)

    def order(self, order_id):
        order = self._engine.orders[order_id]
        if order.platform_id != self.platform_id:
            raise CommandRejected(f"Order {order_id} belongs to another platform")
        return freeze(order)

    def offer(self, offer_id):
        offer = self._engine.offers[offer_id]
        if offer.platform_id != self.platform_id:
            raise CommandRejected(f"Offer {offer_id} belongs to another platform")
        return freeze(offer)

    def quote(self, quote_id):
        quote = self._engine.quotes[quote_id]
        if quote.platform_id != self.platform_id:
            raise CommandRejected(f"Quote {quote_id} belongs to another platform")
        return freeze(quote)

    def own_orders(self):
        return [freeze(order) for order in self._engine.orders.values() if order.platform_id == self.platform_id]

    def own_offers(self):
        return [freeze(offer) for offer in self._engine.offers.values() if offer.platform_id == self.platform_id]


@dataclass(frozen=True)
class CommitmentView:
    order_id: int
    platform_id: str
    phase: str  # queued, pickup, boarding, transport
    pickup: tuple
    destination: tuple
    payout_minor: int


class DriverView:
    """What a driver knows: their own position, shift, apps, and cross-app work."""

    def __init__(self, engine, driver_id):
        self._engine = engine
        self.driver_id = driver_id
        self._driver = engine.drivers[driver_id]

    @property
    def now(self):
        return self._engine.now

    @property
    def position(self):
        return self._engine.position_of("driver", self.driver_id)

    @property
    def on_shift(self):
        return self._driver.shift_id is not None

    @property
    def exit_requested(self):
        shift = self._engine.shifts.get(self._driver.shift_id)
        return shift is not None and shift.exit_requested_at is not None

    @property
    def open_apps(self):
        return frozenset(self._driver.open_apps)

    @property
    def free_slots(self):
        return MAX_COMMITMENTS - len(self._driver.commitments)

    def commitments(self):
        result = []
        for order_id in self._driver.commitments:
            order = self._engine.orders[order_id]
            service = self._engine.services.get(order.service_id)
            phase = "queued" if service is None or service.ended_at is not None else service.phase
            result.append(CommitmentView(order.id, order.platform_id, phase, order.pickup,
                                         order.destination, order.assignment.payout.driver_payout_minor))
        return result

    def pending_offers(self):
        return [freeze(self._engine.offers[offer_id])
                for offer_id in self._engine._pending_offers_to_driver(self.driver_id)]


class RiderView:
    """What a rider knows: their intent, the quotes they saw, and their order."""

    def __init__(self, engine, rider_id):
        self._engine = engine
        self.rider_id = rider_id
        self._rider = engine.riders[rider_id]

    @property
    def now(self):
        return self._engine.now

    @property
    def open_apps(self):
        return frozenset(self._rider.open_apps)

    @property
    def intent(self):
        return freeze(self._engine.intents.get(self._rider.intent_id))

    def quotes(self):
        intent = self.intent
        return [] if intent is None else [freeze(self._engine.quotes[quote_id]) for quote_id in intent.quote_ids]

    @property
    def order(self):
        intent = self.intent
        return None if intent is None or intent.live_order_id is None else freeze(self._engine.orders[intent.live_order_id])


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------

class MarketplaceEngine:
    """Authoritative state and legal transitions for one physical market.

    Construct it with a `HandlerRegistry`; it registers `offer.expire` and
    `service.advance` there. Then `bind()` a `Scheduler` built (or restored)
    from that registry. Every command validates before mutating, applies one
    coherent transition, and publishes notifications afterwards, so
    listeners reacting with further commands always see a consistent market.
    """

    EVENT_KINDS = ("offer.expire", "service.advance")

    def __init__(self, world, registry):
        self.world = world
        self.registry = registry
        self.scheduler = None
        registry.register("offer.expire", self._on_offer_expiry)
        registry.register("service.advance", self._on_service_advance)
        self.platforms = {}
        self.riders = {}
        self.drivers = {}
        self.cars = {}
        self.shifts = {}
        self.intents = {}
        self.quotes = {}
        self.orders = {}
        self.offers = {}
        self.services = {}
        self.settlements = {}
        self._sequences = {
            name: itertools.count(1)
            for name in ("shift", "intent", "quote", "order", "offer", "service", "settlement")
        }
        self._pending_by_order = {}  # order id -> set of pending offer ids
        self._pending_by_driver = {}  # driver id -> set of pending offer ids
        self._listeners = []
        self._outbox = deque()
        self._depth = 0

    def bind(self, scheduler):
        """Attach the scheduler that carries this market's clock and events."""
        if scheduler.registry is not self.registry:
            raise CommandRejected("The scheduler must use the registry the engine registered on")
        self.scheduler = scheduler
        return self

    @property
    def now(self):
        return self.scheduler.now

    def add_listener(self, callback):
        """callback(notification) receives every scoped notification, in order."""
        self._listeners.append(callback)

    # ----------------------------------------------------------------------
    # Transitions and notifications
    # ----------------------------------------------------------------------

    @contextmanager
    def _transition(self):
        """Publish notifications only after the outermost transition completes."""
        self._depth += 1
        try:
            yield
        except BaseException:
            if self._depth == 1:
                self._outbox.clear()
            raise
        finally:
            self._depth -= 1
        if self._depth == 0:
            while self._outbox:
                notification = self._outbox.popleft()
                for listener in self._listeners:
                    listener(notification)

    def _notify(self, audience, audience_id, kind, **data):
        self._outbox.append(Notification(self.now, audience, audience_id, kind, MappingProxyType(data)))

    def _next_id(self, name):
        return next(self._sequences[name])

    # ----------------------------------------------------------------------
    # Population and access
    # ----------------------------------------------------------------------

    def add_platform(self, platform_id, name=None, launched=True):
        if platform_id in self.platforms:
            raise CommandRejected(f"Platform {platform_id!r} already exists")
        self.platforms[platform_id] = Platform(platform_id, name or platform_id, launched)
        return self.platforms[platform_id]

    def launch_platform(self, platform_id):
        self._platform(platform_id).launched = True

    def add_car(self, car_id, registrations):
        if car_id in self.cars:
            raise CommandRejected(f"Car {car_id!r} already exists")
        self.cars[car_id] = Car(car_id, self._membership(registrations, "Car registrations"))
        return self.cars[car_id]

    def add_driver(self, driver_id, apps, car_id, *, accounts=None):
        if driver_id in self.drivers:
            raise CommandRejected(f"Driver {driver_id!r} already exists")
        car = self._car(car_id)
        if car.driver_id is not None:
            raise CommandRejected(f"Car {car_id!r} already has driver {car.driver_id!r}")
        driver = Driver(driver_id, self._membership(apps, "Driver apps"), car_id)
        driver.accounts = set(driver.apps if accounts is None else accounts)
        if not driver.accounts <= driver.apps:
            raise CommandRejected('Accounts require installed apps')
        car.driver_id = driver_id
        self.drivers[driver_id] = driver
        return driver

    def add_rider(self, rider_id, apps, location=None, *, accounts=None):
        if rider_id in self.riders:
            raise CommandRejected(f"Rider {rider_id!r} already exists")
        rider = Rider(rider_id, self._membership(apps, "Rider apps"),
                      None if location is None else point(location, "Location"))
        rider.accounts = set(rider.apps if accounts is None else accounts)
        if not rider.accounts <= rider.apps:
            raise CommandRejected('Accounts require installed apps')
        self.riders[rider_id] = rider
        return rider

    def install_app(self, role, person_id, platform_id):
        """A download. It does not open the app or change preferences."""
        self._platform(platform_id)
        self._person(role, person_id).apps.add(platform_id)

    def activate_account(self, role, person_id, platform_id):
        person = self._person(role, person_id)
        if platform_id not in person.apps:
            raise CommandRejected('An account requires an installed app')
        person.accounts.add(platform_id)

    def register_car(self, car_id, platform_id):
        self._platform(platform_id)
        self._car(car_id).registrations.add(platform_id)

    def _membership(self, platforms, name):
        members = set(platforms)
        if not members:
            raise CommandRejected(f"{name} must include at least one platform")
        for platform_id in members:
            self._platform(platform_id)
        return members

    # ----------------------------------------------------------------------
    # Presence and app participation
    # ----------------------------------------------------------------------

    def start_shift(self, driver_id, location):
        driver = self._driver(driver_id)
        location = point(location, "Location")
        if driver.shift_id is not None:
            raise CommandRejected(f"Driver {driver_id!r} is already on shift")
        with self._transition():
            shift = Shift(self._next_id("shift"), driver_id, self.now, location)
            self.shifts[shift.id] = shift
            driver.shift_id = shift.id
            self.cars[driver.car_id].motion = Motion.idle(location, self.now)
            self._notify("driver", driver_id, "shift_started", shift_id=shift.id, location=location)
        return shift

    def end_shift(self, driver_id):
        """Stop accepting new work now; go offline once accepted work is done."""
        driver = self._driver(driver_id)
        shift = self.shifts.get(driver.shift_id)
        if shift is None:
            raise CommandRejected(f"Driver {driver_id!r} is not on shift")
        if shift.exit_requested_at is not None:
            return shift
        with self._transition():
            shift.exit_requested_at = self.now
            for offer_id in list(self._pending_offers_to_driver(driver_id)):
                self._resolve_offer(self.offers[offer_id], "canceled", "driver_unavailable")
            for platform_id in driver.open_apps:
                self._notify("platform", platform_id, "driver_availability",
                             driver_id=driver_id, accepting=False)
            self._notify("driver", driver_id, "exit_requested", shift_id=shift.id,
                         remaining_commitments=len(driver.commitments))
            if not driver.commitments:
                self._finish_shift(driver, shift)
        return shift

    def _finish_shift(self, driver, shift):
        assert driver.service_id is None and not driver.commitments
        shift.ended_at = self.now
        driver.shift_id = None
        for platform_id in sorted(driver.open_apps):
            self.platforms[platform_id].open_driver_ids.discard(driver.id)
            self._notify("platform", platform_id, "driver_app_closed", driver_id=driver.id)
        driver.open_apps.clear()
        self._notify("driver", driver.id, "shift_ended", shift_id=shift.id,
                     location=self.position_of("driver", driver.id))

    def open_app(self, role, person_id, platform_id):
        person = self._person(role, person_id)
        platform = self._platform(platform_id)
        if platform_id not in person.apps:
            raise CommandRejected(f"{role.capitalize()} {person_id!r} has not installed {platform_id!r}")
        if platform_id not in person.accounts:
            raise CommandRejected('App requires a usable account')
        if not platform.launched:
            raise CommandRejected(f"Platform {platform_id!r} has not launched")
        if platform_id in person.open_apps:
            return
        if role == "driver":
            if person.shift_id is None:
                raise CommandRejected(f"Driver {person_id!r} must be on shift to open an app")
            if platform_id not in self.cars[person.car_id].registrations:
                raise CommandRejected(f"Car {person.car_id!r} is not registered with {platform_id!r}")
        with self._transition():
            person.open_apps.add(platform_id)
            if role == "driver":
                platform.open_driver_ids.add(person_id)
                self._notify("platform", platform_id, "driver_app_opened", driver_id=person_id,
                             accepting=self._driver_accepting(person, platform_id))
            else:
                platform.open_rider_ids.add(person_id)
                request = self.platform_view(platform_id).rider_request(person_id)
                self._notify("platform", platform_id, "rider_app_opened", rider_id=person_id,
                             request=request)

    def close_app(self, role, person_id, platform_id):
        """Stop using an app. Accepted orders on it survive; its pending offers do not."""
        person = self._person(role, person_id)
        platform = self._platform(platform_id)
        if platform_id not in person.open_apps:
            return
        with self._transition():
            person.open_apps.discard(platform_id)
            if role == "driver":
                platform.open_driver_ids.discard(person_id)
                for offer_id in list(self._pending_offers_to_driver(person_id)):
                    offer = self.offers[offer_id]
                    if offer.platform_id == platform_id:
                        self._resolve_offer(offer, "canceled", "driver_unavailable")
                self._notify("platform", platform_id, "driver_app_closed", driver_id=person_id)
            else:
                platform.open_rider_ids.discard(person_id)
                self._notify("platform", platform_id, "rider_app_closed", rider_id=person_id)

    def _driver_accepting(self, driver, platform_id):
        shift = self.shifts.get(driver.shift_id)
        return (
            shift is not None and shift.exit_requested_at is None
            and platform_id in driver.open_apps and platform_id in driver.accounts
            and platform_id in self.cars[driver.car_id].registrations
            and self.platforms[platform_id].launched
        )

    # ----------------------------------------------------------------------
    # Trip intents, quotes, and orders
    # ----------------------------------------------------------------------

    def begin_intent(self, rider_id, origin, destination):
        rider = self._rider(rider_id)
        origin, destination = point(origin, "Origin"), point(destination, "Destination")
        if rider.intent_id is not None:
            raise CommandRejected(f"Rider {rider_id!r} already has a live trip intent")
        if rider.service_id is not None:
            raise CommandRejected(f"Rider {rider_id!r} is onboard a ride")
        with self._transition():
            intent = TripIntent(self._next_id("intent"), rider_id, origin, destination, self.now)
            self.intents[intent.id] = intent
            rider.intent_id = intent.id
            rider.location = origin
            self._notify("rider", rider_id, "intent_started", intent_id=intent.id)
        return intent

    def end_intent(self, intent_id, reason):
        """The rider gives up. Its live order, if any, must be canceled first."""
        intent = self._intent(intent_id)
        if not intent.live:
            raise CommandRejected(f"Trip intent {intent_id} has already ended")
        if intent.live_order_id is not None:
            raise CommandRejected(f"Trip intent {intent_id} still has order {intent.live_order_id}")
        with self._transition():
            self._close_intent(intent, "abandoned", reason)
        return intent

    def _close_intent(self, intent, outcome, reason):
        intent.ended_at = self.now
        intent.outcome = outcome
        intent.reason = reason
        self.riders[intent.rider_id].intent_id = None
        self._notify("rider", intent.rider_id, "intent_ended", intent_id=intent.id,
                     outcome=outcome, reason=reason)

    def issue_quote(self, platform_id, intent_id, gross_minor, discount_minor=0, *,
                    distance_km, duration_seconds, eta_seconds, expires_at,
                    policy_version='direct-v1', selected_rule='default', campaign_id=None):
        """A platform's frozen price and supply estimate for a rider's request."""
        platform = self._platform(platform_id)
        intent = self._intent(intent_id)
        rider = self.riders[intent.rider_id]
        fare = FareTerms(gross_minor, discount_minor)
        finite(expires_at, 'expires_at')
        if expires_at <= self.now:
            raise CommandRejected('Quote expiry must be after the current time')
        finite(distance_km, "distance_km", minimum=0)
        finite(duration_seconds, "duration_seconds", minimum=0)
        finite(eta_seconds, "eta_seconds", minimum=0, allow_none=True)
        if not platform.launched:
            raise CommandRejected(f"Platform {platform_id!r} has not launched")
        if platform_id not in rider.open_apps:
            raise CommandRejected(f"Rider {rider.id!r} does not have {platform_id!r} open")
        if not intent.live:
            raise CommandRejected(f"Trip intent {intent_id} has ended")
        with self._transition():
            quote = Quote(self._next_id("quote"), platform_id, intent_id, rider.id, self.now,
                          fare, distance_km, duration_seconds, eta_seconds, expires_at,
                          policy_version, selected_rule, campaign_id)
            self.quotes[quote.id] = quote
            intent.quote_ids.append(quote.id)
            self._notify("rider", rider.id, "quote_received", quote_id=quote.id,
                         platform_id=platform_id, intent_id=intent_id)
        return quote

    def place_order(self, intent_id, quote_id):
        """The rider orders a quote. The first order converts the intent, once."""
        intent = self._intent(intent_id)
        quote = self._quote(quote_id)
        rider = self.riders[intent.rider_id]
        if self.now >= quote.expires_at:
            raise CommandRejected(f'Quote {quote_id} has expired')
        if quote.intent_id != intent_id:
            raise CommandRejected(f"Quote {quote_id} was not issued for trip intent {intent_id}")
        if not intent.live:
            raise CommandRejected(f"Trip intent {intent_id} has ended")
        if intent.live_order_id is not None:
            raise CommandRejected(f"Trip intent {intent_id} already has order {intent.live_order_id}")
        if quote.platform_id not in rider.open_apps:
            raise CommandRejected(f"Rider {rider.id!r} no longer has {quote.platform_id!r} open")
        if not self.platforms[quote.platform_id].launched:
            raise CommandRejected(f"Platform {quote.platform_id!r} has not launched")
        with self._transition():
            order = Order(self._next_id("order"), quote.platform_id, intent_id, rider.id,
                          intent.origin, intent.destination, quote.id, quote.fare, self.now)
            order.timeline["created"] = self.now
            order.eta_predictions.append({'at': quote.at, 'target': 'pickup_arrival',
                'eta_seconds': quote.eta_seconds, 'policy_version': quote.policy_version,
                'selected_rule': quote.selected_rule, 'source': 'quote', 'source_id': quote.id})
            self.orders[order.id] = order
            intent.order_ids.append(order.id)
            intent.live_order_id = order.id
            if intent.converted_at is None:
                intent.converted_at = self.now
            self._notify("rider", rider.id, "order_created", order_id=order.id,
                         platform_id=order.platform_id)
            self._notify("platform", order.platform_id, "order_created", order_id=order.id,
                         rider_id=rider.id, intent_id=intent_id, quote_id=quote.id)
        return order

    # ----------------------------------------------------------------------
    # Offers and atomic acceptance
    # ----------------------------------------------------------------------

    def create_offer(self, platform_id, order_id, driver_id, payout_minor, bonus_minor=0, *,
                     expires_at, eta_seconds=None, policy_version='direct-v1',
                     selected_rule='default', campaign_id=None):
        """Propose an order to a driver. Offers reserve nothing globally."""
        self._platform(platform_id)
        order = self._order(order_id)
        driver = self._driver(driver_id)
        payout = PayoutTerms(payout_minor, bonus_minor)
        finite(expires_at, "expires_at")
        finite(eta_seconds, "eta_seconds", minimum=0, allow_none=True)
        if order.platform_id != platform_id:
            raise CommandRejected(f"Order {order_id} belongs to {order.platform_id!r}, not {platform_id!r}")
        if order.state != "open":
            raise CommandRejected(f"Order {order_id} is {order.state}, not open")
        if expires_at <= self.now:
            raise CommandRejected("An offer must expire after the current time")
        if not self._driver_accepting(driver, platform_id):
            raise CommandRejected(f"Driver {driver_id!r} is not accepting work on {platform_id!r}")
        for offer_id in self._pending_by_order.get(order_id, ()):
            if self.offers[offer_id].driver_id == driver_id:
                raise CommandRejected(f"Driver {driver_id!r} already has a pending offer for order {order_id}")
        with self._transition():
            offer = Offer(self._next_id("offer"), platform_id, order_id, driver_id, self.now,
                          expires_at, payout, eta_seconds, policy_version=policy_version,
                          selected_rule=selected_rule, campaign_id=campaign_id)
            self.offers[offer.id] = offer
            order.offer_ids.append(offer.id)
            self._pending_by_order.setdefault(order_id, set()).add(offer.id)
            self._pending_by_driver.setdefault(driver_id, set()).add(offer.id)
            self.scheduler.schedule_at(expires_at, "offer.expire", {"offer_id": offer.id})
            self._notify("driver", driver_id, "offer_received", offer_id=offer.id,
                         platform_id=platform_id, order_id=order_id, pickup=order.pickup,
                         destination=order.destination, payout_minor=payout.driver_payout_minor,
                         eta_seconds=eta_seconds, expires_at=expires_at)
        return offer

    def revise_pickup_eta(self, platform_id, order_id, eta_seconds, policy_version, selected_rule):
        """A local prediction revision; never schedule physical arrival from an estimate."""
        order = self._order(order_id)
        finite(eta_seconds, 'eta_seconds', minimum=0)
        if order.platform_id != platform_id or order.terminal or 'arrived' in order.timeline:
            raise CommandRejected('ETA revisions require an own order awaiting pickup')
        with self._transition():
            order.eta_predictions.append({'at': self.now, 'target': 'pickup_arrival',
                'eta_seconds': eta_seconds, 'policy_version': policy_version,
                'selected_rule': selected_rule, 'source': 'revision', 'source_id': order.id})
            self._notify('rider', order.rider_id, 'pickup_eta_revised', order_id=order.id,
                         platform_id=platform_id, eta_seconds=eta_seconds)

    def respond_to_offer(self, offer_id, accept):
        """Resolve a pending offer. Returns its disposition, or 'stale' if already resolved.

        Acceptance checks the offer, the order, the driver's access, and the
        commitment cap, then acquires the slot and binds the assignment in
        one transition. A first commitment starts pickup travel; a second is
        queued behind the current service without changing actual motion.
        """
        offer = self._offer(offer_id)
        if offer.state != "pending":
            return "stale"
        order = self.orders[offer.order_id]
        driver = self.drivers[offer.driver_id]
        with self._transition():
            if not accept:
                self._resolve_offer(offer, "rejected")
            elif self.now >= offer.expires_at:
                self._resolve_offer(offer, "expired")
            elif order.state != "open":
                self._resolve_offer(offer, "acceptance_failed", "order_unavailable")
            elif not self._driver_accepting(driver, offer.platform_id):
                self._resolve_offer(offer, "acceptance_failed", "driver_unavailable")
            elif len(driver.commitments) >= MAX_COMMITMENTS:
                self._resolve_offer(offer, "acceptance_failed", "no_free_slot")
            else:
                self._accept(offer, order, driver)
        return offer.state

    def _accept(self, offer, order, driver):
        self._resolve_offer(offer, "accepted")
        order.state = "assigned"
        order.assignment = Assignment(driver.id, driver.car_id, offer.id, self.now, offer.payout)
        order.timeline["assigned"] = self.now
        order.eta_predictions.append({'at': offer.created_at, 'target': 'pickup_arrival',
            'eta_seconds': offer.eta_seconds, 'policy_version': offer.policy_version,
            'selected_rule': offer.selected_rule, 'source': 'offer', 'source_id': offer.id})
        driver.commitments.append(order.id)
        for other_id in list(self._pending_by_order.get(order.id, ())):
            self._resolve_offer(self.offers[other_id], "canceled", "order_assigned")
        self._notify("platform", order.platform_id, "order_assigned", order_id=order.id,
                     driver_id=driver.id, offer_id=offer.id)
        self._notify("rider", order.rider_id, "order_assigned", order_id=order.id,
                     platform_id=order.platform_id, driver_id=driver.id)
        self._notify("driver", driver.id, "commitment_added", order_id=order.id,
                     platform_id=order.platform_id, position=len(driver.commitments))
        if driver.service_id is None:
            self._start_service(order)

    def _resolve_offer(self, offer, state, reason=None):
        assert offer.state == "pending" and state in OFFER_DISPOSITIONS
        offer.state = state
        offer.resolved_at = self.now
        offer.reason = reason
        self._pending_by_order[offer.order_id].discard(offer.id)
        self._pending_by_driver[offer.driver_id].discard(offer.id)
        self._notify("platform", offer.platform_id, "offer_resolved", offer_id=offer.id,
                     order_id=offer.order_id, driver_id=offer.driver_id, state=state)
        self._notify("driver", offer.driver_id, "offer_closed", offer_id=offer.id,
                     platform_id=offer.platform_id, state=state)

    def _pending_offers_to_driver(self, driver_id):
        return self._pending_by_driver.get(driver_id, ())

    def _on_offer_expiry(self, event):
        offer = self.offers.get(event.payload["offer_id"])
        if offer is None or offer.state != "pending" or self.now < offer.expires_at:
            return
        with self._transition():
            self._resolve_offer(offer, "expired")

    # ----------------------------------------------------------------------
    # Actual motion and physical service
    # ----------------------------------------------------------------------

    def position_of(self, role, person_id, at_seconds=None):
        """Physical position from the one active trajectory. Riders onboard follow the car."""
        at_seconds = self.now if at_seconds is None else at_seconds
        person = self._person(role, person_id)
        if role == "driver":
            car = self.cars[person.car_id]
        elif person.service_id is not None:
            car = self.cars[self.services[person.service_id].car_id]
        else:
            return person.location
        if car.motion is None:
            return None
        return car.motion.position_at(at_seconds)

    def _start_service(self, order):
        """Begin pickup travel for the driver's next commitment from the actual position."""
        driver = self.drivers[order.assignment.driver_id]
        car = self.cars[driver.car_id]
        assert driver.service_id is None and driver.commitments[0] == order.id
        origin = car.motion.position_at(self.now)
        service = Service(self._next_id("service"), order.id, order.platform_id, driver.id,
                          car.id, order.rider_id, self.now)
        self.services[service.id] = service
        driver.service_id = service.id
        order.service_id = service.id
        order.timeline["service_started"] = self.now
        self._notify("driver", driver.id, "service_started", order_id=order.id,
                     platform_id=order.platform_id)
        self._begin_leg(service, "pickup", origin, order.pickup)

    def _begin_leg(self, service, kind, origin, destination):
        if kind == "boarding":
            duration = self.world.boarding_seconds
        else:
            duration = self.world.travel_seconds(math.dist(origin, destination))
        leg = Leg(kind, origin, destination, self.now, self.now + duration)
        service.legs.append(leg)
        self.cars[service.car_id].motion = Motion(origin, destination, self.now, self.now + duration)
        self.scheduler.schedule_after(duration, "service.advance",
                                      {"service_id": service.id, "leg": len(service.legs) - 1})

    def _on_service_advance(self, event):
        service = self.services.get(event.payload["service_id"])
        if service is None or service.ended_at is not None or event.payload["leg"] != len(service.legs) - 1:
            return
        leg = service.legs[-1]
        if self.now < leg.planned_end:
            return
        with self._transition():
            if leg.kind == "pickup":
                self._arrive(service, leg)
            elif leg.kind == "boarding":
                self._board(service, leg)
            else:
                self._drop_off(service, leg)

    def _arrive(self, service, leg):
        order = self.orders[service.order_id]
        service.phase = "boarding"
        service.arrived_at = self.now
        order.timeline["arrived"] = self.now
        self._notify("platform", order.platform_id, "order_arrived", order_id=order.id)
        self._notify("rider", order.rider_id, "order_arrived", order_id=order.id)
        self._notify("driver", service.driver_id, "arrived_at_pickup", order_id=order.id)
        self._begin_leg(service, "boarding", leg.destination, leg.destination)

    def _board(self, service, leg):
        order = self.orders[service.order_id]
        rider = self.riders[service.rider_id]
        driver = self.drivers[service.driver_id]
        if rider.service_id is not None:
            raise RuntimeError(f"Rider {rider.id!r} is already onboard service {rider.service_id}")
        if rider.intent_id != order.intent_id or driver.service_id != service.id:
            raise RuntimeError(f"Service {service.id} lost its rider or driver binding")
        rider.service_id = service.id
        service.phase = "transport"
        service.boarded_at = self.now
        order.timeline["boarded"] = self.now
        self._notify("platform", order.platform_id, "order_boarded", order_id=order.id)
        self._notify("rider", order.rider_id, "order_boarded", order_id=order.id)
        self._notify("driver", service.driver_id, "rider_boarded", order_id=order.id)
        self._begin_leg(service, "transport", leg.destination, order.destination)

    def _drop_off(self, service, leg):
        order = self.orders[service.order_id]
        rider = self.riders[service.rider_id]
        driver = self.drivers[service.driver_id]
        self.cars[service.car_id].motion = Motion.idle(leg.destination, self.now)
        rider.service_id = None
        rider.location = leg.destination
        self._end_service(service, "completed", leg.destination)
        order.state = "completed"
        order.timeline["completed"] = self.now
        intent = self.intents[order.intent_id]
        intent.live_order_id = None
        settlement = self._settle(order, "completed_ride", order.fare.rider_payment_minor,
                                  order.assignment.payout.driver_payout_minor)
        self._notify("platform", order.platform_id, "order_completed", order_id=order.id,
                     settlement_id=settlement.id)
        self._notify("rider", order.rider_id, "order_completed", order_id=order.id,
                     platform_id=order.platform_id, rider_payment_minor=settlement.rider_payment_minor)
        self._close_intent(intent, "completed", "trip completed")
        self._release_commitment(driver, order, "completed")

    def _end_service(self, service, reason, position):
        service.phase = "ended"
        service.ended_at = self.now
        service.end_reason = reason
        service.end_position = position
        self.drivers[service.driver_id].service_id = None

    def _release_commitment(self, driver, order, outcome):
        """Free the commitment once, then promote the next order or finish the shift."""
        driver.commitments.remove(order.id)
        self._notify("driver", driver.id, "commitment_released", order_id=order.id,
                     platform_id=order.platform_id, outcome=outcome,
                     driver_payout_minor=order.assignment.payout.driver_payout_minor if outcome == "completed" else 0)
        if driver.service_id is None:
            if driver.commitments:
                self._start_service(self.orders[driver.commitments[0]])
            else:
                shift = self.shifts.get(driver.shift_id)
                if shift is not None and shift.exit_requested_at is not None:
                    self._finish_shift(driver, shift)

    # ----------------------------------------------------------------------
    # Cancellation and money
    # ----------------------------------------------------------------------

    def cancel_order(self, order_id, by, reason, *, rider_fee_minor=0, driver_compensation_minor=0):
        """End an order before boarding. The platform decides permission and fees;
        the engine performs the physical ending and frees only this commitment."""
        order = self._order(order_id)
        if by not in CANCELLATION_PARTIES:
            raise CommandRejected(f"Cancellation party must be one of {CANCELLATION_PARTIES}")
        minor_units(rider_fee_minor, "rider_fee_minor")
        minor_units(driver_compensation_minor, "driver_compensation_minor")
        if order.terminal:
            raise CommandRejected(f"Order {order_id} is already {order.state}")
        service = self.services.get(order.service_id)
        if service is not None and service.ended_at is None and service.phase == "transport":
            raise CommandRejected(f"Order {order_id} cannot be canceled once the rider is onboard")
        if by == "driver" and order.assignment is None:
            raise CommandRejected(f"Order {order_id} has no driver to cancel it")
        with self._transition():
            for offer_id in list(self._pending_by_order.get(order.id, ())):
                self._resolve_offer(self.offers[offer_id], "canceled", "order_canceled")
            order.state = "canceled"
            order.timeline["canceled"] = self.now
            order.cancellation = {"by": by, "reason": reason}
            self.intents[order.intent_id].live_order_id = None
            driver = None
            if order.assignment is not None:
                driver = self.drivers[order.assignment.driver_id]
                if service is not None and service.ended_at is None:
                    car = self.cars[service.car_id]
                    position = car.motion.position_at(self.now)
                    car.motion = Motion.idle(position, self.now)
                    self._end_service(service, "canceled", position)
            if rider_fee_minor or driver_compensation_minor:
                self._settle(order, "cancellation_fee", rider_fee_minor, driver_compensation_minor)
            self._notify("platform", order.platform_id, "order_canceled", order_id=order.id,
                         by=by, reason=reason)
            self._notify("rider", order.rider_id, "order_canceled", order_id=order.id,
                         platform_id=order.platform_id, by=by, reason=reason)
            if driver is not None:
                self._notify("driver", driver.id, "order_canceled", order_id=order.id,
                             platform_id=order.platform_id, by=by, reason=reason)
                self._release_commitment(driver, order, "canceled")
        return order

    def _settle(self, order, reason, rider_payment_minor, driver_payout_minor):
        settlement = Settlement(
            self._next_id("settlement"), self.now, order.id, order.platform_id, order.rider_id,
            order.assignment.driver_id if order.assignment else None, reason,
            rider_payment_minor, driver_payout_minor, rider_payment_minor - driver_payout_minor,
        )
        self.settlements[settlement.id] = settlement
        order.settlement_ids.append(settlement.id)
        return settlement

    # ----------------------------------------------------------------------
    # Serialization. A market snapshot plus the scheduler's snapshot, taken
    # at the same event boundary, restore a run; policy memory and random
    # state are the runner's to capture alongside them.
    # ----------------------------------------------------------------------

    def snapshot(self):
        tables = {
            name: [_jsonable(asdict(record)) for record in getattr(self, name).values()]
            for name in _TABLES
        }
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "world": asdict(self.world),
            "sequences": {name: next(copy.copy(counter)) for name, counter in self._sequences.items()},
            **tables,
        }

    @classmethod
    def restore(cls, snapshot, registry):
        """Rebuild the market records. Bind a scheduler restored from the same boundary."""
        if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise CommandRejected(f"Unsupported market snapshot schema {snapshot.get('schema_version')!r}")
        engine = cls(World(**snapshot["world"]), registry)
        for name, factory in _TABLES.items():
            table = getattr(engine, name)
            for item in snapshot[name]:
                record = factory(item)
                table[record.id] = record
        for name, value in snapshot["sequences"].items():
            engine._sequences[name] = itertools.count(int(value))
        for offer in engine.offers.values():
            if offer.state == "pending":
                engine._pending_by_order.setdefault(offer.order_id, set()).add(offer.id)
                engine._pending_by_driver.setdefault(offer.driver_id, set()).add(offer.id)
        return engine

    # ----------------------------------------------------------------------
    # Views and lookups
    # ----------------------------------------------------------------------

    def platform_view(self, platform_id):
        self._platform(platform_id)
        return PlatformView(self, platform_id)

    def driver_view(self, driver_id):
        self._driver(driver_id)
        return DriverView(self, driver_id)

    def rider_view(self, rider_id):
        self._rider(rider_id)
        return RiderView(self, rider_id)

    def _lookup(self, table, key, label):
        try:
            return table[key]
        except KeyError:
            raise CommandRejected(f"Unknown {label} {key!r}") from None

    def _platform(self, platform_id):
        return self._lookup(self.platforms, platform_id, "platform")

    def _car(self, car_id):
        return self._lookup(self.cars, car_id, "car")

    def _driver(self, driver_id):
        return self._lookup(self.drivers, driver_id, "driver")

    def _rider(self, rider_id):
        return self._lookup(self.riders, rider_id, "rider")

    def _person(self, role, person_id):
        if role not in ROLES:
            raise CommandRejected(f"Role must be one of {ROLES}")
        return self._driver(person_id) if role == "driver" else self._rider(person_id)

    def _intent(self, intent_id):
        return self._lookup(self.intents, intent_id, "trip intent")

    def _quote(self, quote_id):
        return self._lookup(self.quotes, quote_id, "quote")

    def _order(self, order_id):
        return self._lookup(self.orders, order_id, "order")

    def _offer(self, offer_id):
        return self._lookup(self.offers, offer_id, "offer")


# ----------------------------------------------------------------------
# Snapshot encoding: JSON-shaped dicts in, records out
# ----------------------------------------------------------------------

def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def _opt_point(value):
    return None if value is None else tuple(value)


def _motion(value):
    return None if value is None else Motion(tuple(value["origin"]), tuple(value["destination"]),
                                             value["started_at"], value["arrives_at"])


def _platform(item):
    return Platform(item["id"], item["name"], item["launched"],
                    set(item["open_driver_ids"]), set(item["open_rider_ids"]))


def _car(item):
    return Car(item["id"], set(item["registrations"]), item["driver_id"], _motion(item["motion"]))


def _driver(item):
    return Driver(item["id"], set(item["apps"]), item["car_id"], set(item["open_apps"]),
                  item["shift_id"], list(item["commitments"]), item["service_id"], set(item["accounts"]))


def _rider(item):
    return Rider(item["id"], set(item["apps"]), _opt_point(item["location"]), set(item["open_apps"]),
                 item["intent_id"], item["service_id"], set(item["accounts"]))


def _shift(item):
    return Shift(item["id"], item["driver_id"], item["started_at"], tuple(item["location"]),
                 item["exit_requested_at"], item["ended_at"])


def _intent(item):
    return TripIntent(item["id"], item["rider_id"], tuple(item["origin"]), tuple(item["destination"]),
                      item["created_at"], list(item["quote_ids"]), list(item["order_ids"]),
                      item["live_order_id"], item["converted_at"], item["ended_at"],
                      item["outcome"], item["reason"])


def _quote(item):
    return Quote(item["id"], item["platform_id"], item["intent_id"], item["rider_id"], item["at"],
                 FareTerms(**item["fare"]), item["distance_km"], item["duration_seconds"], item["eta_seconds"],
                 item["expires_at"], item["policy_version"], item["selected_rule"], item["campaign_id"])


def _order(item):
    assignment = item["assignment"]
    if assignment is not None:
        assignment = Assignment(assignment["driver_id"], assignment["car_id"], assignment["offer_id"],
                                assignment["accepted_at"], PayoutTerms(**assignment["payout"]))
    return Order(item["id"], item["platform_id"], item["intent_id"], item["rider_id"], tuple(item["pickup"]),
                 tuple(item["destination"]), item["quote_id"], FareTerms(**item["fare"]), item["created_at"],
                 item["state"], assignment, list(item["offer_ids"]), item["service_id"], dict(item["timeline"]),
                 item["cancellation"], list(item["settlement_ids"]), list(item["eta_predictions"]))


def _offer(item):
    return Offer(item["id"], item["platform_id"], item["order_id"], item["driver_id"], item["created_at"],
                 item["expires_at"], PayoutTerms(**item["payout"]), item["eta_seconds"], item["state"],
                 item["resolved_at"], item["reason"], item["policy_version"], item["selected_rule"], item["campaign_id"])


def _service(item):
    legs = [Leg(leg["kind"], tuple(leg["origin"]), tuple(leg["destination"]), leg["started_at"], leg["planned_end"])
            for leg in item["legs"]]
    return Service(item["id"], item["order_id"], item["platform_id"], item["driver_id"], item["car_id"],
                   item["rider_id"], item["started_at"], item["phase"], legs, item["arrived_at"],
                   item["boarded_at"], item["ended_at"], item["end_reason"], _opt_point(item["end_position"]))


def _settlement(item):
    return Settlement(**item)


_TABLES = {
    "platforms": _platform, "cars": _car, "drivers": _driver, "riders": _rider, "shifts": _shift,
    "intents": _intent, "quotes": _quote, "orders": _order, "offers": _offer, "services": _service,
    "settlements": _settlement,
}
