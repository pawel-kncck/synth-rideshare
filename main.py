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


class ScheduledEvent:
    """One queued callback. Cancelled events are skipped when their time comes."""

    def __init__(self, callback, owner):
        self.callback = callback
        self.owner = owner
        self.canceled = False

    def cancel(self):
        self.canceled = True
        if self.owner is not None:
            self.owner.pending_events.discard(self)


class Driver:
    """A persistent person with a stable ID and at most one active session."""

    def __init__(self, driver_id, simulation):
        self.id = driver_id
        self.simulation = simulation
        self.session = None


class Rider:
    """A persistent person with a stable ID and at most one active session."""

    def __init__(self, rider_id, simulation):
        self.id = rider_id
        self.simulation = simulation
        self.session = None


class DriverSession:
    states = (
        "waiting for order",
        "considering order",
        "driving to pickup",
        "waiting for rider",
        "driving with rider",
        "offline",
    )

    def __init__(self, driver, location, started_at):
        self.driver = driver
        self.location = location
        self.started_at = started_at
        self.ended_at = None
        self.state = "waiting for order"
        self.pending_offer = None
        self.current_order = None
        self.shift_over = False  # set when the shift ends during a ride
        self.pending_events = set()

    @property
    def active(self):
        return self.state != "offline"


class RiderSession:
    states = (
        "online",
        "waiting for driver acceptance",
        "waiting for pickup",
        "driver has arrived",
        "riding",
        "offline",
    )

    def __init__(self, rider, location, destination, started_at):
        self.rider = rider
        self.location = location
        self.destination = destination
        self.started_at = started_at
        self.ended_at = None
        self.exit_reason = None
        self.state = "online"
        self.search_result = None
        self.current_order = None
        self.pending_events = set()

    @property
    def active(self):
        return self.state != "offline"


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
        self.rider = rider_session.rider
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
        self.attempted_driver_ids = set()
        self.accepted_at = None
        self.pickup_arrived_at = None
        self.boarded_at = None
        self.completed_at = None
        self.canceled_at = None
        self.pending_events = set()

    @property
    def active(self):
        return self.state not in self.terminal_states


class Offer:
    states = ("pending", "accepted", "expired", "canceled")

    def __init__(self, offer_id, order, driver_session, created_at, expires_at):
        self.id = offer_id
        self.order = order
        self.driver_session = driver_session
        self.created_at = created_at
        self.expires_at = expires_at
        self.state = "pending"
        self.resolved_at = None
        self.pending_events = set()

    @property
    def active(self):
        return self.state == "pending"


def generate_drivers(simulation, count=10, seed=0):
    rng = random.Random(seed)
    return [
        Driver(int(f"9{id_}"), simulation)
        for id_ in rng.sample(range(1, 1_000_000_000), count)
    ]


def generate_riders(simulation, count=100, seed=0):
    rng = random.Random(seed)
    return [
        Rider(int(f"8{id_}"), simulation)
        for id_ in rng.sample(range(1, 10_000_000_000), count)
    ]


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
        self.drivers = generate_drivers(self, driver_count, seed)
        self.riders = generate_riders(self, rider_count, seed)
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

    def schedule_driver_session(self, at_seconds, driver, location, shift_seconds=None):
        """Bring a driver online at a simulated time.

        The driver waits for orders until the shift length elapses (or until
        the run ends when no shift length is given). A shift that ends during
        a ride finishes that ride first.
        """
        self._check_person(driver, Driver, self.drivers, "Driver")
        location = _point(location, "Location")
        if shift_seconds is not None and (
            not math.isfinite(shift_seconds) or shift_seconds <= 0
        ):
            raise ValueError("Shift length must be None or a positive number of seconds")
        self._schedule_at(
            at_seconds, lambda: self._start_driver_session(driver, location, shift_seconds)
        )

    def schedule_rider_session(self, at_seconds, rider, location, destination):
        """Have a rider appear at a simulated time wanting to travel.

        The rider searches immediately, then orders after the order delay if a
        driver was available or leaves if none was. The session ends at
        drop-off or when no driver accepts.
        """
        self._check_person(rider, Rider, self.riders, "Rider")
        location = _point(location, "Location")
        destination = _point(destination, "Destination")
        self._schedule_at(
            at_seconds, lambda: self._start_rider_session(rider, location, destination)
        )

    def advance_to(self, target_time):
        """Process every event due up to the target time, then set the clock there."""
        if target_time < self.current_time:
            raise ValueError("Cannot move time backwards")

        while self._events and self._events[0][0] <= target_time:
            execution_time, _, event = heapq.heappop(self._events)
            if event.canceled:
                continue
            if event.owner is not None:
                if not event.owner.active:
                    event.cancel()
                    continue
                event.owner.pending_events.discard(event)
            self.current_time = execution_time
            event.callback()

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

    def _check_person(self, person, kind, population, label):
        if not isinstance(person, kind) or person.simulation is not self:
            raise ValueError(f"{label} must belong to this simulation")
        if person not in population:
            raise ValueError(f"{label} is not part of this simulation's population")

    def _schedule_at(self, at_seconds, callback):
        if not math.isfinite(at_seconds):
            raise ValueError("Scheduled time must be finite")
        if at_seconds < self.current_time:
            raise ValueError("Cannot schedule a session in the past")
        self._schedule(at_seconds - self.current_time, callback)

    def _schedule(self, delay_seconds, callback, owner=None):
        """Queue a callback; one tied to an owner dies with that owner."""
        if delay_seconds < 0:
            raise ValueError("Cannot schedule an event in the past")
        if owner is not None and not owner.active:
            raise ValueError("Event owner must be active")

        event = ScheduledEvent(callback, owner)
        if owner is not None:
            owner.pending_events.add(event)
        heapq.heappush(
            self._events,
            (self.current_time + delay_seconds, next(self._event_sequence), event),
        )
        return event

    def _cancel_pending_events(self, owner):
        for event in tuple(owner.pending_events):
            event.cancel()

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

    def _start_driver_session(self, driver, location, shift_seconds):
        if driver.session is not None:
            raise ValueError(f"Driver {driver.id} already has an active session")
        session = DriverSession(driver, location, self.current_time)
        driver.session = session
        self.active_driver_sessions[driver.id] = session
        self._log(f"Driver {driver.id} went online at {location}")
        if shift_seconds is not None:
            self._schedule(shift_seconds, lambda: self._end_shift(session), owner=session)

    def _end_shift(self, session):
        if session.current_order is not None:
            session.shift_over = True
            self._log(
                f"Driver {session.driver.id} finishes the shift after order "
                f"{session.current_order.id}"
            )
            return
        self._end_driver_session(session)

    def _end_driver_session(self, session):
        """Take the driver offline, handing any pending offer to the next driver."""
        if not session.active:
            return
        offer = session.pending_offer
        self._cancel_pending_events(session)
        session.state = "offline"
        session.ended_at = self.current_time
        del self.active_driver_sessions[session.driver.id]
        session.driver.session = None
        self.driver_session_history.append(session)
        self._log(f"Driver {session.driver.id} went offline at {session.location}")
        if offer is not None:
            self._resolve_offer(offer, "canceled")
            self._dispatch_order(offer.order)

    def _driver_is_eligible(self, session):
        return (
            session.state == "waiting for order"
            and session.pending_offer is None
            and session.current_order is None
        )

    # ----------------------------------------------------------------------
    # Rider sessions and search
    # ----------------------------------------------------------------------

    def _start_rider_session(self, rider, location, destination):
        if rider.session is not None:
            raise ValueError(f"Rider {rider.id} already has an active session")
        session = RiderSession(rider, location, destination, self.current_time)
        rider.session = session
        self.active_rider_sessions[rider.id] = session
        self._log(f"Rider {rider.id} started a session at {location} heading to {destination}")
        session.search_result = quote = self._search(session)
        if quote.drivers_available:
            self._log(
                f"Rider {rider.id} quoted {quote.distance_km:.1f} km for {quote.price:.2f}, "
                f"nearest driver {quote.eta_seconds:.0f}s away"
            )
        else:
            self._log(f"Rider {rider.id} quoted {quote.distance_km:.1f} km, no drivers available")
        self._schedule(
            self.order_delay_seconds, lambda: self._decide_on_quote(session), owner=session
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
        if session.search_result.drivers_available:
            self._create_order(session)
        else:
            self._log(f"Rider {session.rider.id} left: no drivers available")
            self._end_rider_session(session, "no drivers available")

    def _end_rider_session(self, session, reason):
        if not session.active:
            return
        self._cancel_pending_events(session)
        session.state = "offline"
        session.ended_at = self.current_time
        session.exit_reason = reason
        del self.active_rider_sessions[session.rider.id]
        session.rider.session = None
        self.rider_session_history.append(session)

    # ----------------------------------------------------------------------
    # Orders and offers
    # ----------------------------------------------------------------------

    def _create_order(self, session):
        order = Order(next(self._order_sequence), session, self.current_time)
        session.current_order = order
        self.active_orders.append(order)
        self._log(
            f"Order {order.id} created by rider {order.rider.id}: "
            f"{order.pickup_location} to {order.destination}, "
            f"{order.distance_km:.1f} km, price {order.price:.2f}"
        )
        self._dispatch_order(order)

    def _dispatch_order(self, order):
        """Offer to the nearest untried waiting driver, up to the offer limit."""
        if (
            not order.active
            or order.pending_offer is not None
            or order.driver_session is not None
        ):
            return
        if len(order.attempted_driver_ids) >= self.max_offers_per_order:
            self._abandon_order(order)
            return

        driver_session = min(
            (
                session
                for session in self.active_driver_sessions.values()
                if self._driver_is_eligible(session)
                and session.driver.id not in order.attempted_driver_ids
            ),
            key=lambda session: (
                math.dist(order.pickup_location, session.location), session.driver.id
            ),
            default=None,
        )
        if driver_session is None:
            self._abandon_order(order)
            return

        offer = Offer(
            next(self._offer_sequence), order, driver_session,
            self.current_time, self.current_time + self.offer_timeout_seconds,
        )
        order.attempted_driver_ids.add(driver_session.driver.id)
        order.offers.append(offer)
        order.pending_offer = offer
        order.state = "waiting for driver to accept"
        order.rider_session.state = "waiting for driver acceptance"
        driver_session.pending_offer = offer
        driver_session.state = "considering order"
        # The timeout is queued first so a response due at the same instant
        # as the deadline finds the offer already expired.
        self._schedule(
            self.offer_timeout_seconds, lambda: self._expire_offer(offer), owner=offer
        )
        self._schedule(
            self.accept_delay_seconds, lambda: self._accept_offer(offer), owner=offer
        )

    def _abandon_order(self, order):
        """Cancel an order no driver accepted; the rider gives up and leaves."""
        self._finalize_order(order, "canceled", "no drivers accepted")
        self._log(f"Order {order.id} canceled: no drivers accepted; rider {order.rider.id} left")
        self._end_rider_session(order.rider_session, "no drivers accepted")

    def _resolve_offer(self, offer, state):
        """Close the offer and release only the references it still holds."""
        if not offer.active:
            return
        offer.state = state
        offer.resolved_at = self.current_time
        self._cancel_pending_events(offer)
        if offer.order.pending_offer is offer:
            offer.order.pending_offer = None
            if offer.order.state == "waiting for driver to accept":
                offer.order.state = "searching for a driver"
        driver = offer.driver_session
        if driver.pending_offer is offer:
            driver.pending_offer = None
            if driver.active and driver.current_order is None:
                driver.state = "waiting for order"

    def _accept_offer(self, offer):
        if not offer.active or self.current_time >= offer.expires_at:
            return
        self._resolve_offer(offer, "accepted")
        order = offer.order
        driver_session = offer.driver_session
        order.driver_session = driver_session
        order.accepted_at = self.current_time
        order.state = "driver driving to pickup"
        order.rider_session.state = "waiting for pickup"
        driver_session.current_order = order
        driver_session.state = "driving to pickup"
        pickup_duration = self.calculate_duration(
            math.dist(driver_session.location, order.pickup_location)
        )
        self._log(
            f"Driver {driver_session.driver.id} accepted order {order.id} "
            f"from {driver_session.location}, pickup in {pickup_duration:.0f}s"
        )
        self._schedule(pickup_duration, lambda: self._arrive_at_pickup(order), owner=order)

    def _expire_offer(self, offer):
        if not offer.active:
            return
        self._log(
            f"Driver {offer.driver_session.driver.id} did not answer offer {offer.id} "
            f"for order {offer.order.id} in time"
        )
        self._resolve_offer(offer, "expired")
        self._dispatch_order(offer.order)

    # ----------------------------------------------------------------------
    # Rides
    # ----------------------------------------------------------------------

    def _require_state(self, order, order_state):
        if order.state != order_state or order.driver_session is None:
            raise RuntimeError(
                f"Order {order.id} is {order.state!r}; expected {order_state!r}"
            )

    def _arrive_at_pickup(self, order):
        self._require_state(order, "driver driving to pickup")
        driver_session = order.driver_session
        driver_session.location = order.pickup_location
        order.pickup_arrived_at = self.current_time
        order.state = "driver waiting for rider"
        order.rider_session.state = "driver has arrived"
        driver_session.state = "waiting for rider"
        self._log(
            f"Driver {driver_session.driver.id} arrived at pickup {order.pickup_location} "
            f"for order {order.id}, waiting {self.boarding_delay_seconds:.0f}s"
        )
        self._schedule(
            self.boarding_delay_seconds, lambda: self._pick_up_rider(order), owner=order
        )

    def _pick_up_rider(self, order):
        self._require_state(order, "driver waiting for rider")
        driver_session = order.driver_session
        order.boarded_at = self.current_time
        order.state = "driving with rider"
        order.rider_session.state = "riding"
        driver_session.state = "driving with rider"
        self._log(
            f"Rider {order.rider.id} boarded with driver {driver_session.driver.id} "
            f"for order {order.id}, trip to {order.destination} takes {order.duration_seconds:.0f}s"
        )
        self._schedule(order.duration_seconds, lambda: self._end_ride(order), owner=order)

    def _end_ride(self, order):
        """Complete the trip, end the rider session, and free or release the driver."""
        self._require_state(order, "driving with rider")
        driver_session = order.driver_session
        driver_session.location = order.destination
        order.rider_session.location = order.destination
        self._log(
            f"Order {order.id} completed: rider {order.rider.id} dropped off at "
            f"{order.destination} by driver {driver_session.driver.id}, "
            f"{self.current_time - order.created_at:.0f}s after ordering, fare {order.price:.2f}"
        )
        self._finalize_order(order, "completed")
        self._end_rider_session(order.rider_session, "trip completed")
        if driver_session.shift_over:
            self._end_driver_session(driver_session)

    def _finalize_order(self, order, state, cancellation_reason=None):
        """Archive the order once and release the rider and driver it held."""
        if not order.active:
            return
        if order.pending_offer is not None:
            self._resolve_offer(order.pending_offer, "canceled")
        order.state = state
        order.cancellation_reason = cancellation_reason
        if state == "completed":
            order.completed_at = self.current_time
        else:
            order.canceled_at = self.current_time
        self._cancel_pending_events(order)
        self.active_orders.remove(order)
        self.order_history.append(order)
        rider_session = order.rider_session
        if rider_session.current_order is order:
            rider_session.current_order = None
            if rider_session.active:
                rider_session.state = "online"
        driver_session = order.driver_session
        if driver_session is not None and driver_session.current_order is order:
            driver_session.current_order = None
            if driver_session.active:
                driver_session.state = "waiting for order"
