import heapq
import itertools
import math
import random
import time
from dataclasses import dataclass
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


class DriverSession:
    """A driver's time online. Its state follows the offer or order it holds."""

    ride_states = {
        "driver driving to pickup": "driving to pickup",
        "driver waiting for rider": "waiting for rider",
        "driving with rider": "driving with rider",
    }

    def __init__(self, driver_id, location, started_at):
        self.driver_id = driver_id
        self.location = location
        self.started_at = started_at
        self.ended_at = None
        self.pending_order = None  # order whose offer the driver is considering
        self.current_order = None
        self.shift_over = False  # set when the shift ends during a ride

    @property
    def state(self):
        if self.ended_at is not None:
            return "offline"
        if self.pending_order is not None:
            return "considering order"
        if self.current_order is None:
            return "waiting for order"
        return self.ride_states[self.current_order.state]


class RiderSession:
    """A rider's request for one trip. Its state follows the order it holds."""

    order_states = {
        "searching for a driver": "waiting for driver acceptance",
        "waiting for driver to accept": "waiting for driver acceptance",
        "driver driving to pickup": "waiting for pickup",
        "driver waiting for rider": "driver has arrived",
        "driving with rider": "riding",
    }

    def __init__(self, rider_id, location, destination, started_at):
        self.rider_id = rider_id
        self.location = location
        self.destination = destination
        self.started_at = started_at
        self.ended_at = None
        self.exit_reason = None
        self.search_result = None
        self.current_order = None

    @property
    def state(self):
        if self.ended_at is not None:
            return "offline"
        if self.current_order is None:
            return "online"
        return self.order_states[self.current_order.state]


@dataclass(frozen=True)
class SearchResult:
    """Trip details and driver availability at the instant of a search."""

    distance_km: float
    duration_seconds: float
    price: float
    eta_seconds: Optional[float]
    drivers_available: bool


class Order:
    terminal_states = ("completed", "canceled")
    states = (
        "searching for a driver",
        "waiting for driver to accept",
        "driver driving to pickup",
        "driver waiting for rider",
        "driving with rider",
    ) + terminal_states

    def __init__(self, order_id, rider_session, created_at):
        self.id = order_id
        self.rider_session = rider_session
        self.rider_id = rider_session.rider_id
        self.driver_session = None
        self.pickup_location = rider_session.location
        self.destination = rider_session.destination
        quote = rider_session.search_result
        self.distance_km = quote.distance_km
        self.duration_seconds = quote.duration_seconds
        self.price = quote.price
        self.created_at = created_at
        self.state = "searching for a driver"
        self.cancellation_reason = None
        self.pending_offer = None
        self.offers = []
        self.accepted_at = None
        self.pickup_arrived_at = None
        self.boarded_at = None
        self.completed_at = None
        self.canceled_at = None


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

    def _create_order(self, session):
        order = Order(next(self._order_sequence), session, self.current_time)
        session.current_order = order
        self.active_orders.append(order)
        self._log(
            f"Order {order.id} created by rider {order.rider_id}: "
            f"{order.pickup_location} to {order.destination}, "
            f"{order.distance_km:.1f} km, price {order.price:.2f}"
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
        order.state = "waiting for driver to accept"
        driver_session.pending_order = order
        # The timeout is queued first so a response due at the same instant
        # as the deadline finds the offer already expired.
        self._schedule(self.offer_timeout_seconds, lambda: self._expire_offer(order, offer))
        self._schedule(self.accept_delay_seconds, lambda: self._accept_offer(order, offer))

    def _abandon_order(self, order):
        """Cancel an order no driver accepted; the rider gives up and leaves."""
        self._finalize_order(order, "canceled", "no drivers accepted")
        self._log(f"Order {order.id} canceled: no drivers accepted; rider {order.rider_id} left")
        self._end_rider_session(order.rider_session, "no drivers accepted")

    def _resolve_offer(self, order, state):
        """Close the order's pending offer and release the driver it reserved."""
        offer = order.pending_offer
        offer.state = state
        offer.resolved_at = self.current_time
        offer.driver_session.pending_order = None
        order.pending_offer = None
        if order.state == "waiting for driver to accept":
            order.state = "searching for a driver"

    def _accept_offer(self, order, offer):
        if order.pending_offer is not offer or self.current_time >= offer.expires_at:
            return
        self._resolve_offer(order, "accepted")
        driver_session = offer.driver_session
        order.driver_session = driver_session
        order.accepted_at = self.current_time
        order.state = "driver driving to pickup"
        driver_session.current_order = order
        pickup_duration = self.calculate_duration(
            math.dist(driver_session.location, order.pickup_location)
        )
        self._log(
            f"Driver {driver_session.driver_id} accepted order {order.id} "
            f"from {driver_session.location}, pickup in {pickup_duration:.0f}s"
        )
        self._schedule(pickup_duration, lambda: self._arrive_at_pickup(order))

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

    def _ride_is_at(self, order, order_state):
        """True while the accepted ride is still at the step a callback expects."""
        return order.state == order_state and order.driver_session is not None

    def _arrive_at_pickup(self, order):
        if not self._ride_is_at(order, "driver driving to pickup"):
            return
        driver_session = order.driver_session
        driver_session.location = order.pickup_location
        order.pickup_arrived_at = self.current_time
        order.state = "driver waiting for rider"
        self._log(
            f"Driver {driver_session.driver_id} arrived at pickup {order.pickup_location} "
            f"for order {order.id}, waiting {self.boarding_delay_seconds:.0f}s"
        )
        self._schedule(
            self.boarding_delay_seconds, lambda: self._pick_up_rider(order)
        )

    def _pick_up_rider(self, order):
        if not self._ride_is_at(order, "driver waiting for rider"):
            return
        driver_session = order.driver_session
        order.boarded_at = self.current_time
        order.state = "driving with rider"
        self._log(
            f"Rider {order.rider_id} boarded with driver {driver_session.driver_id} "
            f"for order {order.id}, trip to {order.destination} takes {order.duration_seconds:.0f}s"
        )
        self._schedule(order.duration_seconds, lambda: self._end_ride(order))

    def _end_ride(self, order):
        """Complete the trip, end the rider session, and free or release the driver."""
        if not self._ride_is_at(order, "driving with rider"):
            return
        driver_session = order.driver_session
        driver_session.location = order.destination
        order.rider_session.location = order.destination
        self._log(
            f"Order {order.id} completed: rider {order.rider_id} dropped off at "
            f"{order.destination} by driver {driver_session.driver_id}, "
            f"{self.current_time - order.created_at:.0f}s after ordering, fare {order.price:.2f}"
        )
        self._finalize_order(order, "completed")
        self._end_rider_session(order.rider_session, "trip completed")
        if driver_session.shift_over:
            self._end_driver_session(driver_session)

    def _finalize_order(self, order, state, cancellation_reason=None):
        """Archive the order once and release the rider and driver it held."""
        if order.state in Order.terminal_states:
            return
        if order.pending_offer is not None:
            self._resolve_offer(order, "canceled")
        order.state = state
        order.cancellation_reason = cancellation_reason
        if state == "completed":
            order.completed_at = self.current_time
        else:
            order.canceled_at = self.current_time
        self.active_orders.remove(order)
        self.order_history.append(order)
        order.rider_session.current_order = None
        if order.driver_session is not None:
            order.driver_session.current_order = None
