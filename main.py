import heapq
import itertools
import math
import random
import time
from dataclasses import dataclass, field
from typing import Optional


def _point(value, name):
    """Return a finite (x, y) tuple of kilometres or raise a clear error."""
    try:
        x, y = value
        point = (float(x), float(y))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an (x, y) pair of kilometres") from None
    if not all(math.isfinite(coordinate) for coordinate in point):
        raise ValueError(f"{name} must be finite")
    return point


@dataclass(frozen=True)
class SearchResult:
    """Trip details and driver availability at the instant of a search."""

    distance_km: float
    duration_seconds: float
    price: float
    eta_seconds: Optional[float]
    drivers_available: bool


@dataclass(eq=False)
class DriverSession:
    """A driver's time online. Its state follows the offer or order it holds."""

    ride_states = {
        "driver driving to pickup": "driving to pickup",
        "driver waiting for rider": "waiting for rider",
        "driving with rider": "driving with rider",
    }

    driver_id: int
    location: tuple
    started_at: float
    ended_at: Optional[float] = None
    pending_order: Optional["Order"] = None  # order whose offer the driver is considering
    current_order: Optional["Order"] = None
    shift_over: bool = False  # set when the shift ends during a ride

    @property
    def state(self):
        if self.ended_at is not None:
            return "offline"
        if self.pending_order is not None:
            return "considering order"
        if self.current_order is None:
            return "waiting for order"
        return self.ride_states[self.current_order.state]


@dataclass(eq=False)
class RiderSession:
    """A rider's request for one trip. Its state follows the order it holds."""

    order_states = {
        "searching for a driver": "waiting for driver acceptance",
        "waiting for driver to accept": "waiting for driver acceptance",
        "driver driving to pickup": "waiting for pickup",
        "driver waiting for rider": "driver has arrived",
        "driving with rider": "riding",
    }

    rider_id: int
    location: tuple
    destination: tuple
    started_at: float
    ended_at: Optional[float] = None
    exit_reason: Optional[str] = None
    search_result: Optional[SearchResult] = None
    current_order: Optional["Order"] = None

    @property
    def state(self):
        if self.ended_at is not None:
            return "offline"
        if self.current_order is None:
            return "online"
        return self.order_states[self.current_order.state]


@dataclass(eq=False)
class Order:
    """One trip request, from the accepted quote to completion or cancellation.

    States: searching for a driver, waiting for driver to accept, driver
    driving to pickup, driver waiting for rider, driving with rider, then
    completed or canceled.
    """

    terminal_states = ("completed", "canceled")

    id: int
    rider_session: RiderSession
    pickup_location: tuple
    destination: tuple
    quote: SearchResult
    driver_session: Optional[DriverSession] = None
    state: Optional[str] = None
    timeline: dict = field(default_factory=dict)  # first time each state was entered
    cancellation_reason: Optional[str] = None
    pending_offer: Optional["Offer"] = None
    offers: list = field(default_factory=list)


@dataclass
class Offer:
    """One attempt to hand an order to a driver."""

    id: int
    driver_session: DriverSession
    created_at: float
    expires_at: float
    state: str = "pending"  # then accepted, expired, or canceled
    resolved_at: Optional[float] = None


def generate_ids(prefix, count, upper, seed):
    """Stable person IDs: a role prefix digit followed by a seeded random number."""
    rng = random.Random(seed)
    return [int(f"{prefix}{id_}") for id_ in rng.sample(range(1, upper), count)]


class Simulation:
    """A ride-hailing market driven by a simulated clock.

    A scenario only schedules when driver and rider sessions begin, via
    schedule_driver_session() and schedule_rider_session(). Every later event
    (searches, orders, offers, acceptance, travel, boarding, drop-off, and
    session ends) is triggered from inside the simulation.
    """

    offer_timeout_seconds = 10
    max_offers_per_order = 5

    def __init__(
        self,
        driver_count=10,
        rider_count=100,
        seed=0,
        speed_kmh=30,
        base_fare=2.0,
        price_per_km=1.5,
        boarding_delay_seconds=30,
        order_delay_seconds=5,
        accept_delay_seconds=3,
    ):
        if speed_kmh <= 0:
            raise ValueError("Driving speed must be positive")
        if base_fare < 0 or price_per_km < 0:
            raise ValueError("Fare values cannot be negative")
        for name, value in (
            ("Boarding delay", boarding_delay_seconds),
            ("Order delay", order_delay_seconds),
            ("Accept delay", accept_delay_seconds),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")

        self.speed_kmh = speed_kmh
        self.base_fare = base_fare
        self.price_per_km = price_per_km
        self.boarding_delay_seconds = boarding_delay_seconds
        # How long a rider thinks about a quote before ordering or leaving.
        self.order_delay_seconds = order_delay_seconds
        # How long a driver takes to accept an offer. At or past the offer
        # timeout the offer expires first and the next driver is tried.
        self.accept_delay_seconds = accept_delay_seconds
        self.drivers = generate_ids(9, driver_count, 1_000_000_000, seed)
        self.riders = generate_ids(8, rider_count, 10_000_000_000, seed)
        self.active_driver_sessions = {}
        self.active_rider_sessions = {}
        self.active_orders = []
        self.order_history = []
        self.driver_session_history = []
        self.rider_session_history = []
        self.current_time = 0
        self._events = []
        self._event_sequence = itertools.count()
        self._order_sequence = itertools.count(1)
        self._offer_sequence = itertools.count(1)

    # ----------------------------------------------------------------------
    # External interface: scheduling sessions and moving the clock
    # ----------------------------------------------------------------------

    def schedule_driver_session(self, at_seconds, driver_id, location, shift_seconds=None):
        """Bring a driver online at a simulated time.

        The driver waits for orders until the shift length elapses (or until
        the run ends when no shift length is given). A shift that ends during
        a ride finishes that ride first.
        """
        if driver_id not in self.drivers:
            raise ValueError(f"Unknown driver {driver_id!r}")
        location = _point(location, "Location")
        if shift_seconds is not None and (
            not math.isfinite(shift_seconds) or shift_seconds <= 0
        ):
            raise ValueError("Shift length must be None or a positive number of seconds")
        self._schedule_at(
            at_seconds, lambda: self._start_driver_session(driver_id, location, shift_seconds)
        )

    def schedule_rider_session(self, at_seconds, rider_id, location, destination):
        """Have a rider appear at a simulated time wanting to travel.

        The rider searches immediately, then orders after the order delay if a
        driver was available or leaves if none was. The session ends at
        drop-off or when no driver accepts.
        """
        if rider_id not in self.riders:
            raise ValueError(f"Unknown rider {rider_id!r}")
        location = _point(location, "Location")
        destination = _point(destination, "Destination")
        self._schedule_at(
            at_seconds, lambda: self._start_rider_session(rider_id, location, destination)
        )

    def advance_to(self, target_time):
        """Process every event due up to the target time, then set the clock there."""
        if target_time < self.current_time:
            raise ValueError("Cannot move time backwards")

        while self._events and self._events[0][0] <= target_time:
            self.current_time, _, callback = heapq.heappop(self._events)
            callback()

        self.current_time = target_time

    def run(self, start=6, end=23, seconds_per_hour=1):
        """Play the clock from the start hour to the end hour."""
        end_time = (end - start) * 3600
        if end_time < self.current_time:
            raise ValueError("Run end time is before the current simulation time")
        if seconds_per_hour < 0:
            raise ValueError("Playback speed cannot be negative")

        self.advance_to(self.current_time)
        while self.current_time < end_time:
            next_time = min(self.current_time + 60, end_time)
            time.sleep((next_time - self.current_time) * seconds_per_hour / 3600)
            self.advance_to(next_time)

    # ----------------------------------------------------------------------
    # Scheduler
    # ----------------------------------------------------------------------

    def _schedule_at(self, at_seconds, callback):
        if not math.isfinite(at_seconds):
            raise ValueError("Scheduled time must be finite")
        if at_seconds < self.current_time:
            raise ValueError("Cannot schedule a session in the past")
        self._schedule(at_seconds - self.current_time, callback)

    def _schedule(self, delay_seconds, callback):
        """Queue a callback.

        Nothing is ever removed from the queue, so every callback must first
        check that what it is about is still in the state it expects and
        return quietly otherwise.
        """
        if delay_seconds < 0:
            raise ValueError("Cannot schedule an event in the past")
        heapq.heappush(
            self._events,
            (self.current_time + delay_seconds, next(self._event_sequence), callback),
        )

    def _log(self, message):
        print(f"[{self.current_time:>6.0f}s] {message}")

    # ----------------------------------------------------------------------
    # Pricing
    # ----------------------------------------------------------------------

    def calculate_duration(self, distance_km):
        """Return travel duration in simulated seconds."""
        if distance_km < 0:
            raise ValueError("Distance cannot be negative")
        return distance_km / self.speed_kmh * 3600

    def calculate_price(self, distance_km):
        """Return the base fare plus the distance charge."""
        if distance_km < 0:
            raise ValueError("Distance cannot be negative")
        return self.base_fare + distance_km * self.price_per_km

    # ----------------------------------------------------------------------
    # Driver sessions
    # ----------------------------------------------------------------------

    def _start_driver_session(self, driver_id, location, shift_seconds):
        if driver_id in self.active_driver_sessions:
            raise ValueError(f"Driver {driver_id} already has an active session")
        session = DriverSession(driver_id, location, self.current_time)
        self.active_driver_sessions[driver_id] = session
        self._log(f"Driver {driver_id} went online at {location}")
        if shift_seconds is not None:
            self._schedule(shift_seconds, lambda: self._end_shift(session))

    def _end_shift(self, session):
        if session.state == "offline":
            return
        if session.current_order is not None:
            session.shift_over = True
            self._log(
                f"Driver {session.driver_id} finishes the shift after order "
                f"{session.current_order.id}"
            )
            return
        self._end_driver_session(session)

    def _end_driver_session(self, session):
        """Take the driver offline, handing any pending offer to the next driver."""
        if session.state == "offline":
            return
        order = session.pending_order
        session.ended_at = self.current_time
        del self.active_driver_sessions[session.driver_id]
        self.driver_session_history.append(session)
        self._log(f"Driver {session.driver_id} went offline at {session.location}")
        if order is not None:
            self._resolve_offer(order, "canceled")
            self._dispatch_order(order)

    def _driver_is_eligible(self, session):
        return session.state == "waiting for order"

    # ----------------------------------------------------------------------
    # Rider sessions and search
    # ----------------------------------------------------------------------

    def _start_rider_session(self, rider_id, location, destination):
        if rider_id in self.active_rider_sessions:
            raise ValueError(f"Rider {rider_id} already has an active session")
        session = RiderSession(rider_id, location, destination, self.current_time)
        self.active_rider_sessions[rider_id] = session
        self._log(f"Rider {rider_id} started a session at {location} heading to {destination}")
        session.search_result = quote = self._search(session)
        if quote.drivers_available:
            self._log(
                f"Rider {rider_id} quoted {quote.distance_km:.1f} km for {quote.price:.2f}, "
                f"nearest driver {quote.eta_seconds:.0f}s away"
            )
        else:
            self._log(f"Rider {rider_id} quoted {quote.distance_km:.1f} km, no drivers available")
        self._schedule(
            self.order_delay_seconds, lambda: self._decide_on_quote(session)
        )

    def _search(self, session):
        """Quote the trip and the nearest waiting driver without reserving anyone."""
        distance_km = math.dist(session.location, session.destination)
        pickup_distance_km = min(
            (
                math.dist(session.location, driver_session.location)
                for driver_session in self.active_driver_sessions.values()
                if self._driver_is_eligible(driver_session)
            ),
            default=None,
        )
        return SearchResult(
            distance_km=distance_km,
            duration_seconds=self.calculate_duration(distance_km),
            price=self.calculate_price(distance_km),
            eta_seconds=(
                self.calculate_duration(pickup_distance_km)
                if pickup_distance_km is not None
                else None
            ),
            drivers_available=pickup_distance_km is not None,
        )

    def _decide_on_quote(self, session):
        if session.state != "online" or session.current_order is not None:
            return
        if session.search_result.drivers_available:
            self._create_order(session)
        else:
            self._log(f"Rider {session.rider_id} left: no drivers available")
            self._end_rider_session(session, "no drivers available")

    def _end_rider_session(self, session, reason):
        if session.state == "offline":
            return
        session.ended_at = self.current_time
        session.exit_reason = reason
        del self.active_rider_sessions[session.rider_id]
        self.rider_session_history.append(session)

    # ----------------------------------------------------------------------
    # Orders and offers
    # ----------------------------------------------------------------------

    def _set_state(self, order, state):
        order.state = state
        order.timeline.setdefault(state, self.current_time)

    def _create_order(self, session):
        order = Order(
            next(self._order_sequence), session,
            session.location, session.destination, session.search_result,
        )
        self._set_state(order, "searching for a driver")
        session.current_order = order
        self.active_orders.append(order)
        self._log(
            f"Order {order.id} created by rider {session.rider_id}: "
            f"{order.pickup_location} to {order.destination}, "
            f"{order.quote.distance_km:.1f} km, price {order.quote.price:.2f}"
        )
        self._dispatch_order(order)

    def _dispatch_order(self, order):
        """Offer to the nearest untried waiting driver, up to the offer limit."""
        if (
            order.state != "searching for a driver"
            or order.pending_offer is not None
            or order.driver_session is not None
        ):
            return
        if len(order.offers) >= self.max_offers_per_order:
            self._abandon_order(order)
            return

        tried = {offer.driver_session.driver_id for offer in order.offers}
        driver_session = min(
            (
                session
                for session in self.active_driver_sessions.values()
                if self._driver_is_eligible(session) and session.driver_id not in tried
            ),
            key=lambda session: (
                math.dist(order.pickup_location, session.location), session.driver_id
            ),
            default=None,
        )
        if driver_session is None:
            self._abandon_order(order)
            return

        offer = Offer(
            next(self._offer_sequence), driver_session,
            self.current_time, self.current_time + self.offer_timeout_seconds,
        )
        order.offers.append(offer)
        order.pending_offer = offer
        self._set_state(order, "waiting for driver to accept")
        driver_session.pending_order = order
        # The timeout is queued first so a response due at the same instant
        # as the deadline finds the offer already expired.
        self._schedule(self.offer_timeout_seconds, lambda: self._expire_offer(order, offer))
        self._schedule(self.accept_delay_seconds, lambda: self._accept_offer(order, offer))

    def _abandon_order(self, order):
        """Cancel an order no driver accepted; the rider gives up and leaves."""
        self._finalize_order(order, "canceled", "no drivers accepted")
        self._log(
            f"Order {order.id} canceled: no drivers accepted; "
            f"rider {order.rider_session.rider_id} left"
        )
        self._end_rider_session(order.rider_session, "no drivers accepted")

    def _resolve_offer(self, order, state):
        """Close the order's pending offer and release the driver it reserved."""
        offer = order.pending_offer
        offer.state = state
        offer.resolved_at = self.current_time
        offer.driver_session.pending_order = None
        order.pending_offer = None
        if order.state == "waiting for driver to accept":
            self._set_state(order, "searching for a driver")

    def _accept_offer(self, order, offer):
        if order.pending_offer is not offer or self.current_time >= offer.expires_at:
            return
        self._resolve_offer(order, "accepted")
        driver_session = offer.driver_session
        order.driver_session = driver_session
        self._set_state(order, "driver driving to pickup")
        driver_session.current_order = order
        pickup_duration = self.calculate_duration(
            math.dist(driver_session.location, order.pickup_location)
        )
        self._log(
            f"Driver {driver_session.driver_id} accepted order {order.id} "
            f"from {driver_session.location}, pickup in {pickup_duration:.0f}s"
        )
        self._schedule(
            pickup_duration, lambda: self._advance_ride(order, "driver driving to pickup")
        )

    def _expire_offer(self, order, offer):
        if order.pending_offer is not offer:
            return
        self._log(
            f"Driver {offer.driver_session.driver_id} did not answer offer {offer.id} "
            f"for order {order.id} in time"
        )
        self._resolve_offer(order, "expired")
        self._dispatch_order(order)

    # ----------------------------------------------------------------------
    # Rides
    # ----------------------------------------------------------------------

    def _advance_ride(self, order, expected_state):
        """Run one ride step (arrival, boarding, or drop-off) if the ride is still there."""
        if order.state != expected_state or order.driver_session is None:
            return
        driver, rider = order.driver_session, order.rider_session
        if expected_state == "driver driving to pickup":
            driver.location = order.pickup_location
            self._set_state(order, "driver waiting for rider")
            self._log(
                f"Driver {driver.driver_id} arrived at pickup {order.pickup_location} "
                f"for order {order.id}, waiting {self.boarding_delay_seconds:.0f}s"
            )
            delay = self.boarding_delay_seconds
        elif expected_state == "driver waiting for rider":
            self._set_state(order, "driving with rider")
            self._log(
                f"Rider {rider.rider_id} boarded with driver {driver.driver_id} "
                f"for order {order.id}, trip to {order.destination} "
                f"takes {order.quote.duration_seconds:.0f}s"
            )
            delay = order.quote.duration_seconds
        else:
            driver.location = rider.location = order.destination
            self._log(
                f"Order {order.id} completed: rider {rider.rider_id} dropped off at "
                f"{order.destination} by driver {driver.driver_id}, "
                f"{self.current_time - order.timeline['searching for a driver']:.0f}s after ordering, "
                f"fare {order.quote.price:.2f}"
            )
            self._finalize_order(order, "completed")
            self._end_rider_session(rider, "trip completed")
            if driver.shift_over:
                self._end_driver_session(driver)
            return
        next_state = order.state
        self._schedule(delay, lambda: self._advance_ride(order, next_state))

    def _finalize_order(self, order, state, cancellation_reason=None):
        """Archive the order once and release the rider and driver it held."""
        if order.state in Order.terminal_states:
            return
        if order.pending_offer is not None:
            self._resolve_offer(order, "canceled")
        self._set_state(order, state)
        order.cancellation_reason = cancellation_reason
        self.active_orders.remove(order)
        self.order_history.append(order)
        order.rider_session.current_order = None
        if order.driver_session is not None:
            order.driver_session.current_order = None
