import heapq
import itertools
import math
import random
import time
from dataclasses import dataclass
from typing import Optional


def _require(condition, message, exception=ValueError):
    """Raise `exception(message)` unless `condition` holds."""
    if not condition:
        raise exception(message)


class ScheduledEvent:
    """Handle for one queued callback; cancellation is safe to repeat."""

    def __init__(self, callback, owner=None):
        self.callback = callback
        self.owner = owner
        self.canceled = False

    def cancel(self):
        self.canceled = True
        if self.owner is not None:
            self.owner.pending_events.discard(self)


class Session:
    """State and lifecycle shared by driver and rider sessions."""

    registry = None

    def __init__(self, actor, location):
        self.actor = actor
        self.simulation = actor.simulation
        self.location = location
        self.state = "online"
        self.pending_events = set()
        self.current_order = None

    def go_offline(self):
        self.simulation.end_session(self)


class DriverSession(Session):
    states = ("online", "waiting for order", "considering order", "driving to pickup",
              "waiting for rider", "driving with rider", "offline")
    registry = "active_driver_sessions"

    def __init__(self, driver, location):
        super().__init__(driver, location)
        self.pending_offer = None

    driver = property(lambda self: self.actor)

    def wait_for_order(self):
        _require(self.simulation._is_active_event_owner(self),
                 "Driver session must be active in this simulation")
        _require(self.pending_offer is None and self.current_order is None,
                 "Driver cannot wait while considering or serving an order")
        self.state = "waiting for order"

    def accept_order(self, offer):
        return self.simulation.accept_offer(self, offer)

    def reject_order(self, offer):
        return self.simulation.reject_offer(self, offer)

    def arrive_at_pickup(self, order):
        return self.simulation.arrive_at_pickup(self, order)

    def pick_up_rider(self, order):
        return self.simulation.pick_up_rider(self, order)

    def end_ride(self, order):
        return self.simulation.end_ride(self, order)


class RiderSession(Session):
    states = ("online", "waiting for driver acceptance", "waiting for pickup",
              "driver has arrived", "riding", "offline")
    registry = "active_rider_sessions"

    def __init__(self, rider, location, destination):
        super().__init__(rider, location)
        self.destination = destination
        self.search_result = None
        self.order_event = None

    rider = property(lambda self: self.actor)

    def search(self, destination):
        """Search immediately, replace the stored snapshot, and queue the automatic order."""
        _require(self.state != "offline", "Cannot search after the rider session has ended")
        _require(self.current_order is None,
                 "Cannot search while the rider session has an active order")
        self.destination = destination
        self.search_result = self.simulation.search(self)
        self._schedule_order()
        return self.search_result

    def _schedule_order(self):
        """Order after the configured delay; a newer search replaces the pending order."""
        if self.order_event is not None:
            self.order_event.cancel()
            self.order_event = None
        delay = self.simulation.order_delay_seconds
        if delay is None or not self.search_result.drivers_available:
            return
        self.order_event = self.simulation.schedule(delay, self._order_automatically, owner=self)

    def _order_automatically(self):
        self.order_event = None
        if self.current_order is not None or self.search_result is None:
            return None
        if not self.search_result.drivers_available:
            return None
        return self.make_order()

    def make_order(self):
        """Order using this session's latest available search response."""
        return self.simulation.create_order(self)


class Actor:
    """A person with a stable id who opens transient sessions."""

    id_prefix = ""
    id_range = 0
    session_class = None

    def __init__(self, actor_id, simulation):
        self.id = actor_id
        self.simulation = simulation
        self.session = None

    @classmethod
    def generate(cls, simulation, count, seed):
        rng = random.Random(seed)
        return [
            cls(int(f"{cls.id_prefix}{id_}"), simulation)
            for id_ in rng.sample(range(1, cls.id_range), count)
        ]

    def _open_session(self, *args):
        session = self.session_class(self, *args)
        self.simulation.register_session(session)
        self.session = session
        return session


class Driver(Actor):
    id_prefix = "9"
    id_range = 1_000_000_000
    session_class = DriverSession

    def go_online(self, location):
        session = self._open_session(location)
        self.simulation._log(f"Driver {self.id} went online at {tuple(location)}")
        return session


class Rider(Actor):
    id_prefix = "8"
    id_range = 10_000_000_000
    session_class = RiderSession

    def start_session(self, location, destination):
        session = self._open_session(location, destination)
        self.simulation._log(
            f"Rider {self.id} started a session "
            f"at {tuple(location)} heading to {tuple(destination)}"
        )
        session.search(destination)
        return session


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
    states = ("searching for a driver", "waiting for driver to accept",
              "driver driving to pickup", "driver waiting for rider",
              "driving with rider") + terminal_states

    def __init__(self, order_id, rider_session):
        self.id = order_id
        self.simulation = rider_session.simulation
        self.rider_session = rider_session
        self.rider = rider_session.rider
        self.driver_session = None
        self.pickup_location = tuple(rider_session.location)
        self.destination = tuple(rider_session.destination)
        quote = rider_session.search_result
        self.distance_km = quote.distance_km
        self.duration_seconds = quote.duration_seconds
        self.price = quote.price
        self.created_at = self.simulation.current_time
        self.state = "searching for a driver"
        self.pending_events = set()
        self.offers = []
        self.attempted_driver_ids = set()
        self.cancellation_reason = None
        self.pending_offer = None
        self.accepted_at = None
        self.pickup_arrived_at = None
        self.boarded_at = None
        self.completed_at = None
        self.canceled_at = None
        self.ride_event = None
        self.ride_event_at = None


class Offer:
    def __init__(self, offer_id, order, driver_session, created_at, expires_at):
        self.id = offer_id
        self.order = order
        self.simulation = order.simulation
        self.driver_session = driver_session
        self.created_at = created_at
        self.expires_at = expires_at
        self.state = "pending"
        self.resolved_at = None
        self.timeout_event = None
        self.response_event = None
        self.pending_events = set()


class Simulation:
    offer_timeout_seconds = 10
    max_offers_per_order = 5

    # order state -> (rider state, driver state) for each stage of an accepted ride
    ride_stages = {
        "driver driving to pickup": ("waiting for pickup", "driving to pickup"),
        "driver waiting for rider": ("driver has arrived", "waiting for rider"),
        "driving with rider": ("riding", "driving with rider"),
    }

    def __init__(self, driver_count=10, rider_count=100, seed=0, speed_kmh=30,
                 base_fare=2.0, price_per_km=1.5, boarding_delay_seconds=30,
                 order_delay_seconds=None, accept_delay_seconds=None):
        def valid_delay(value, optional):
            return (value is None and optional) or (
                value is not None and math.isfinite(value) and value >= 0
            )

        _require(speed_kmh > 0, "Driving speed must be positive")
        _require(base_fare >= 0 and price_per_km >= 0, "Fare values cannot be negative")
        _require(valid_delay(boarding_delay_seconds, False),
                 "Boarding delay must be finite and nonnegative")
        _require(valid_delay(order_delay_seconds, True),
                 "Order delay must be None or finite and nonnegative")
        _require(valid_delay(accept_delay_seconds, True),
                 "Accept delay must be None or finite and nonnegative")

        self.speed_kmh = speed_kmh
        self.base_fare = base_fare
        self.price_per_km = price_per_km
        self.boarding_delay_seconds = boarding_delay_seconds
        # None keeps ordering manual; a number orders that long after a successful search.
        self.order_delay_seconds = order_delay_seconds
        # None leaves offers to the caller; a number accepts that long after each offer.
        self.accept_delay_seconds = accept_delay_seconds
        self.drivers = Driver.generate(self, driver_count, seed)
        self.riders = Rider.generate(self, rider_count, seed)
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

    def _log(self, message):
        print(f"[{self.current_time:>6.0f}s] {message}")

    def calculate_duration(self, distance_km):
        """Return travel duration in simulated seconds."""
        _require(distance_km >= 0, "Distance cannot be negative")
        return distance_km / self.speed_kmh * 3600

    def calculate_price(self, distance_km):
        """Return the base fare plus the distance charge."""
        _require(distance_km >= 0, "Distance cannot be negative")
        return self.base_fare + distance_km * self.price_per_km

    def search(self, rider_session):
        """Return an immediate quote without reserving drivers or creating orders."""
        _require(self.active_rider_sessions.get(rider_session.rider.id) is rider_session,
                 "Rider session must be active in this simulation")
        _require(rider_session.current_order is None,
                 "Cannot search while the rider session has an active order")

        distance_km = math.dist(rider_session.location, rider_session.destination)
        pickup_distance_km = min(
            (
                math.dist(rider_session.location, session.location)
                for session in self.active_driver_sessions.values()
                if self._driver_is_eligible(session)
            ),
            default=None,
        )
        return SearchResult(
            distance_km=distance_km,
            duration_seconds=self.calculate_duration(distance_km),
            price=self.calculate_price(distance_km),
            eta_seconds=None if pickup_distance_km is None
            else self.calculate_duration(pickup_distance_km),
            drivers_available=pickup_distance_km is not None,
        )

    def create_order(self, rider_session):
        """Register the accepted quote, then check current dispatch availability."""
        _require(
            self.active_rider_sessions.get(rider_session.rider.id) is rider_session
            and rider_session.state != "offline",
            "Rider session must be active in this simulation",
        )
        _require(rider_session.current_order is None, "Rider session already has an active order")
        quote = rider_session.search_result
        _require(quote is not None and quote.drivers_available,
                 "Search with available drivers is required before ordering")

        order = Order(next(self._order_sequence), rider_session)
        rider_session.current_order = order
        self.active_orders.append(order)
        self._log(
            f"Order {order.id} created by rider {order.rider.id}: "
            f"{order.pickup_location} to {order.destination}, "
            f"{order.distance_km:.1f} km, price {order.price:.2f}"
        )
        self.dispatch_order(order)
        return order

    def dispatch_order(self, order):
        """Offer to the nearest untried eligible driver, up to five total offers."""
        _require(order.simulation is self, "Order belongs to another simulation")
        if order.state in Order.terminal_states:
            return order
        _require(order in self.active_orders, "Order must be active in this simulation")
        _require(self._is_active_event_owner(order),
                 "Order must belong to the current active rider session")
        if (
            order.state != "searching for a driver"
            or order.pending_offer is not None
            or order.driver_session is not None
        ):
            return order

        driver_session = None
        if len(order.attempted_driver_ids) < self.max_offers_per_order:
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
            self.finalize_order(order, "canceled", "no drivers accepted")
            return order

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
        offer.timeout_event = self.schedule(
            self.offer_timeout_seconds, lambda: self._expire_offer(offer), owner=offer
        )
        if self.accept_delay_seconds is not None:
            offer.response_event = self.schedule(
                self.accept_delay_seconds,
                lambda: driver_session.accept_order(offer),
                owner=offer,
            )
        return order

    def _driver_is_eligible(self, session):
        return (
            session.state == "waiting for order"
            and session.pending_offer is None
            and session.current_order is None
        )

    def _is_current_offer(self, offer):
        """Check identity and lifecycle state, leaving deadline checks to responders."""
        return (
            isinstance(offer, Offer)
            and offer.simulation is self
            and offer.state == "pending"
            and offer.order.pending_offer is offer
            and offer.order.state == "waiting for driver to accept"
            and offer.order.driver_session is None
            and offer.order.rider_session.state == "waiting for driver acceptance"
            and offer.driver_session.pending_offer is offer
            and offer.driver_session.current_order is None
            and offer.driver_session.state == "considering order"
            and self._is_active_event_owner(offer.order)
            and self._is_active_event_owner(offer.driver_session)
        )

    def _resolve_offer(self, offer, state):
        """Invalidate this offer's work and release only its own live references."""
        if offer.state != "pending":
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
            if self._is_active_event_owner(driver) and driver.current_order is None:
                driver.state = "waiting for order"

    def _respond_to_offer(self, driver_session, offer, state):
        """Resolve this exact current offer strictly before its deadline."""
        if (
            not self._is_current_offer(offer)
            or offer.driver_session is not driver_session
            or self.current_time >= offer.expires_at
        ):
            return False
        self._resolve_offer(offer, state)
        return True

    def accept_offer(self, driver_session, offer):
        """Accept this exact current offer strictly before its deadline."""
        if not self._respond_to_offer(driver_session, offer, "accepted"):
            return False
        order = offer.order
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
        self._schedule_ride_action(
            order, pickup_duration, lambda: driver_session.arrive_at_pickup(order)
        )
        return True

    def reject_offer(self, driver_session, offer):
        if not self._respond_to_offer(driver_session, offer, "rejected"):
            return False
        self.dispatch_order(offer.order)
        return True

    def _expire_offer(self, offer):
        if not self._is_current_offer(offer) or self.current_time < offer.expires_at:
            return False
        self._resolve_offer(offer, "expired")
        self.dispatch_order(offer.order)
        return True

    def _schedule_ride_action(self, order, delay_seconds, callback):
        """Keep just the next travel/boarding action, owned by this order."""
        if order.ride_event is not None:
            order.ride_event.cancel()
        order.ride_event_at = self.current_time + delay_seconds
        order.ride_event = self.schedule(delay_seconds, callback, owner=order)

    def _is_current_ride(self, driver_session, order, order_state):
        """Guard direct actions as well as callbacks against early or stale transitions."""
        rider_state, driver_state = self.ride_stages[order_state]
        return (
            isinstance(order, Order)
            and order.simulation is self
            and order.driver_session is driver_session is not None
            and driver_session.current_order is order
            and order.pending_offer is None
            and driver_session.pending_offer is None
            and order.state == order_state
            and order.rider_session.state == rider_state
            and driver_session.state == driver_state
            and self._is_active_event_owner(order)
            and order.ride_event is not None
            and not order.ride_event.canceled
            and self.current_time >= order.ride_event_at
        )

    def arrive_at_pickup(self, driver_session, order):
        """Move the assigned driver to pickup when the scheduled travel is due."""
        if not self._is_current_ride(driver_session, order, "driver driving to pickup"):
            return False
        driver_session.location = order.pickup_location
        order.pickup_arrived_at = self.current_time
        self._enter_ride_stage(order, "driver waiting for rider")
        self._log(
            f"Driver {driver_session.driver.id} arrived at pickup "
            f"{order.pickup_location} for order {order.id}, "
            f"waiting {self.boarding_delay_seconds:.0f}s"
        )
        self._schedule_ride_action(
            order, self.boarding_delay_seconds, lambda: driver_session.pick_up_rider(order)
        )
        return True

    def pick_up_rider(self, driver_session, order):
        """Board the rider after the deterministic waiting period."""
        if not self._is_current_ride(driver_session, order, "driver waiting for rider"):
            return False
        order.boarded_at = self.current_time
        self._enter_ride_stage(order, "driving with rider")
        self._log(
            f"Rider {order.rider.id} boarded with driver "
            f"{driver_session.driver.id} for order {order.id}, trip to {order.destination} "
            f"takes {order.duration_seconds:.0f}s"
        )
        self._schedule_ride_action(
            order, order.duration_seconds, lambda: driver_session.end_ride(order)
        )
        return True

    def _enter_ride_stage(self, order, order_state):
        """Move the order and both sessions into the next stage of an accepted ride."""
        order.state = order_state
        order.rider_session.state, order.driver_session.state = self.ride_stages[order_state]

    def end_ride(self, driver_session, order):
        """Complete the trip, end the rider session, and keep the driver available."""
        if not self._is_current_ride(driver_session, order, "driving with rider"):
            return False
        driver_session.location = order.destination
        order.rider_session.location = order.destination
        self.finalize_order(order, "completed")
        self._log(
            f"Order {order.id} completed: rider {order.rider.id} "
            f"dropped off at {order.destination} by driver {driver_session.driver.id}, "
            f"{self.current_time - order.created_at:.0f}s after ordering, fare {order.price:.2f}"
        )
        self.end_session(order.rider_session)
        return True

    def finalize_order(self, order, state, cancellation_reason=None):
        """Archive once; physical ride completion is coordinated by end_ride()."""
        _require(order.simulation is self, "Order belongs to another simulation")
        _require(state in Order.terminal_states,
                 "Final order state must be completed or canceled")
        if order.state in Order.terminal_states:
            return order
        _require(order in self.active_orders, "Order must be active in this simulation")
        _require(state != "canceled" or cancellation_reason,
                 "Canceled orders require a cancellation reason")
        _require(state != "completed" or cancellation_reason is None,
                 "Completed orders cannot have a cancellation reason")

        if order.pending_offer is not None:
            self._resolve_offer(order.pending_offer, "canceled")
        order.state = state
        order.cancellation_reason = cancellation_reason
        if state == "completed":
            order.completed_at = self.current_time
        else:
            order.canceled_at = self.current_time
        self._cancel_pending_events(order)
        if order.ride_event is not None:
            order.ride_event.cancel()
        order.ride_event = None
        order.ride_event_at = None
        self.active_orders.remove(order)
        self.order_history.append(order)
        for session, released_state in (
            (order.rider_session, "online"), (order.driver_session, "waiting for order"),
        ):
            if session is not None and session.current_order is order:
                session.current_order = None
                if self._is_active_event_owner(session):
                    session.state = released_state
        return order

    def _is_active_event_owner(self, owner):
        if isinstance(owner, Session):
            return (
                getattr(self, owner.registry).get(owner.actor.id) is owner
                and owner.state != "offline"
            )
        if isinstance(owner, Order):
            return (
                owner.simulation is self
                and owner in self.active_orders
                and owner.state not in Order.terminal_states
                and owner.rider_session.current_order is owner
                and self._is_active_event_owner(owner.rider_session)
                and (
                    owner.driver_session is None
                    or self._is_active_event_owner(owner.driver_session)
                )
            )
        if isinstance(owner, Offer):
            return self._is_current_offer(owner)
        raise TypeError("Event owner must be a driver session, rider session, order, or offer")

    def _cancel_pending_events(self, owner):
        for event in tuple(owner.pending_events):
            event.cancel()

    def schedule(self, delay_seconds, callback, *, owner=None):
        """Return a cancelable handle tied to an optional active session/order/offer."""
        _require(delay_seconds >= 0, "Cannot schedule an event in the past")
        _require(owner is None or self._is_active_event_owner(owner),
                 "Event owner must be active in this simulation")

        event = ScheduledEvent(callback, owner)
        if owner is not None:
            owner.pending_events.add(event)
        heapq.heappush(
            self._events,
            (self.current_time + delay_seconds, next(self._event_sequence), event),
        )
        return event

    def advance_to(self, target_time):
        _require(target_time >= self.current_time, "Cannot move time backwards")

        while self._events and self._events[0][0] <= target_time:
            execution_time, _, event = heapq.heappop(self._events)
            if event.canceled:
                continue
            if event.owner is not None:
                if not self._is_active_event_owner(event.owner):
                    event.cancel()
                    continue
                event.owner.pending_events.discard(event)
            self.current_time = execution_time
            event.callback()

        self.current_time = target_time

    def register_session(self, session):
        """Track a newly opened driver or rider session by its actor id."""
        actor = session.actor
        role = "Driver" if isinstance(session, DriverSession) else "Rider"
        _require(actor.simulation is self, f"{role} belongs to another simulation")
        active = getattr(self, session.registry)
        _require(actor.id not in active, f"{role} already has an active session")
        active[actor.id] = session

    register_driver_session = register_session
    register_rider_session = register_session

    def end_session(self, session):
        """Archive a session, canceling its work and releasing any pending offer."""
        active = getattr(self, session.registry)
        actor = session.actor
        if active.get(actor.id) is not session:
            return
        offer = None
        if isinstance(session, DriverSession):
            _require(session.current_order is None,
                     "Cannot end a driver session during an accepted ride")
            offer = session.pending_offer
        else:
            _require(
                session.current_order is None or session.current_order.driver_session is None,
                "Cannot end a rider session during an accepted ride",
            )
            if session.current_order is not None:
                self.finalize_order(session.current_order, "canceled", "rider ended session")
        self._cancel_pending_events(session)
        session.state = "offline"
        del active[actor.id]
        actor.session = None
        history = (
            self.driver_session_history
            if isinstance(session, DriverSession)
            else self.rider_session_history
        )
        history.append(session)
        if offer is not None:
            self._resolve_offer(offer, "canceled")
            self.dispatch_order(offer.order)

    end_driver_session = end_session
    end_rider_session = end_session

    def run(self, start=6, end=23, seconds_per_hour=1):
        end_time = (end - start) * 3600
        _require(end_time >= self.current_time,
                 "Run end time is before the current simulation time")
        _require(seconds_per_hour >= 0, "Playback speed cannot be negative")

        self.advance_to(self.current_time)
        while self.current_time < end_time:
            next_time = min(self.current_time + 60, end_time)
            time.sleep((next_time - self.current_time) * seconds_per_hour / 3600)
            self.advance_to(next_time)


def run_simulation(start=6, end=23, seconds_per_hour=1):
    simulation = Simulation()
    simulation.run(start, end, seconds_per_hour)
    return simulation


if __name__ == "__main__":
    run_simulation()
