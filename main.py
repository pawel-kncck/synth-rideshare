import heapq
import itertools
import math
import random
import time
from dataclasses import dataclass
from typing import Optional


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


class DriverSession:
    states = (
        "online",
        "waiting for order",
        "considering order",
        "driving to pickup",
        "waiting for rider",
        "driving with rider",
        "offline",
    )

    def __init__(self, driver, location):
        self.driver = driver
        self.location = location
        self.state = "online"
        self.pending_events = set()
        self.pending_offer = None
        self.current_order = None

    def wait_for_order(self):
        simulation = self.driver.simulation
        if not simulation._is_active_event_owner(self):
            raise ValueError("Driver session must be active in this simulation")
        if self.pending_offer is not None or self.current_order is not None:
            raise ValueError("Driver cannot wait while considering or serving an order")
        self.state = "waiting for order"

    def accept_order(self, offer):
        return self.driver.simulation.accept_offer(self, offer)

    def reject_order(self, offer):
        return self.driver.simulation.reject_offer(self, offer)

    def arrive_at_pickup(self, order):
        return self.driver.simulation.arrive_at_pickup(self, order)

    def pick_up_rider(self, order):
        return self.driver.simulation.pick_up_rider(self, order)

    def end_ride(self, order):
        return self.driver.simulation.end_ride(self, order)

    def go_offline(self):
        self.driver.simulation.end_driver_session(self)


class Driver:
    def __init__(self, driver_id, simulation):
        self.id = driver_id
        self.simulation = simulation
        self.session = None

    def go_online(self, location):
        session = DriverSession(self, location)
        self.simulation.register_driver_session(session)
        self.session = session
        return self.session


class RiderSession:
    states = (
        "online",
        "waiting for driver acceptance",
        "waiting for pickup",
        "driver has arrived",
        "riding",
        "offline",
    )

    def __init__(self, rider, location, destination):
        self.rider = rider
        self.location = location
        self.destination = destination
        self.state = "online"
        self.search_result = None
        self.current_order = None
        self.pending_events = set()

    def search(self, destination):
        """Search immediately and replace the stored response snapshot."""
        if self.state == "offline":
            raise ValueError("Cannot search after the rider session has ended")
        if self.current_order is not None:
            raise ValueError("Cannot search while the rider session has an active order")
        self.destination = destination
        self.search_result = self.rider.simulation.search(self)
        return self.search_result

    def make_order(self):
        """Order using this session's latest available search response."""
        return self.rider.simulation.create_order(self)

    def go_offline(self):
        self.rider.simulation.end_rider_session(self)


class Rider:
    def __init__(self, rider_id, simulation):
        self.id = rider_id
        self.simulation = simulation
        self.session = None

    def start_session(self, location, destination):
        session = RiderSession(self, location, destination)
        self.simulation.register_rider_session(session)
        self.session = session
        session.search(destination)
        return self.session


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

    def __init__(self, order_id, rider_session):
        self.id = order_id
        self.simulation = rider_session.rider.simulation
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
        self.cancellation_reason = None
        self.pending_events = set()
        self.pending_offer = None
        self.offers = []
        self.attempted_driver_ids = set()
        self.accepted_at = None
        self.pickup_arrived_at = None
        self.boarded_at = None
        self.completed_at = None
        self.canceled_at = None
        self.ride_event = None
        self.ride_event_at = None


class Offer:
    states = ("pending", "accepted", "rejected", "expired", "canceled")

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
        self.pending_events = set()


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
    ):
        if speed_kmh <= 0:
            raise ValueError("Driving speed must be positive")
        if base_fare < 0 or price_per_km < 0:
            raise ValueError("Fare values cannot be negative")
        if not math.isfinite(boarding_delay_seconds) or boarding_delay_seconds < 0:
            raise ValueError("Boarding delay must be finite and nonnegative")

        self.speed_kmh = speed_kmh
        self.base_fare = base_fare
        self.price_per_km = price_per_km
        self.boarding_delay_seconds = boarding_delay_seconds
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

    def search(self, rider_session):
        """Return an immediate quote without reserving drivers or creating orders."""
        if self.active_rider_sessions.get(rider_session.rider.id) is not rider_session:
            raise ValueError("Rider session must be active in this simulation")
        if rider_session.current_order is not None:
            raise ValueError("Cannot search while the rider session has an active order")

        distance_km = math.dist(rider_session.location, rider_session.destination)
        pickup_distance_km = min(
            (
                math.dist(rider_session.location, session.location)
                for session in self.active_driver_sessions.values()
                if self._driver_is_eligible(session)
            ),
            default=None,
        )
        eta_seconds = (
            self.calculate_duration(pickup_distance_km)
            if pickup_distance_km is not None
            else None
        )

        return SearchResult(
            distance_km=distance_km,
            duration_seconds=self.calculate_duration(distance_km),
            price=self.calculate_price(distance_km),
            eta_seconds=eta_seconds,
            drivers_available=pickup_distance_km is not None,
        )

    def create_order(self, rider_session):
        """Register the accepted quote, then check current dispatch availability."""
        if (
            self.active_rider_sessions.get(rider_session.rider.id) is not rider_session
            or rider_session.state == "offline"
        ):
            raise ValueError("Rider session must be active in this simulation")
        if rider_session.current_order is not None:
            raise ValueError("Rider session already has an active order")
        quote = rider_session.search_result
        if quote is None or not quote.drivers_available:
            raise ValueError("Search with available drivers is required before ordering")

        order = Order(next(self._order_sequence), rider_session)
        rider_session.current_order = order
        self.active_orders.append(order)
        self.dispatch_order(order)
        return order

    def dispatch_order(self, order):
        """Offer to the nearest untried eligible driver, up to five total offers."""
        if order.simulation is not self:
            raise ValueError("Order belongs to another simulation")
        if order.state in Order.terminal_states:
            return order
        if order not in self.active_orders:
            raise ValueError("Order must be active in this simulation")
        if not self._is_active_event_owner(order):
            raise ValueError("Order must belong to the current active rider session")
        if (
            order.state != "searching for a driver"
            or order.pending_offer is not None
            or order.driver_session is not None
        ):
            return order

        if len(order.attempted_driver_ids) >= self.max_offers_per_order:
            self.finalize_order(order, "canceled", "no drivers accepted")
            return order

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

    def accept_offer(self, driver_session, offer):
        """Accept this exact current offer strictly before its deadline."""
        if (
            not self._is_current_offer(offer)
            or offer.driver_session is not driver_session
            or self.current_time >= offer.expires_at
        ):
            return False
        self._resolve_offer(offer, "accepted")
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
        self._schedule_ride_action(
            order, pickup_duration, lambda: driver_session.arrive_at_pickup(order)
        )
        return True

    def reject_offer(self, driver_session, offer):
        if (
            not self._is_current_offer(offer)
            or offer.driver_session is not driver_session
            or self.current_time >= offer.expires_at
        ):
            return False
        self._resolve_offer(offer, "rejected")
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

    def _is_current_ride(self, driver_session, order, order_state, rider_state, driver_state):
        """Guard direct actions as well as callbacks against early or stale transitions."""
        return (
            isinstance(order, Order)
            and order.simulation is self
            and order.driver_session is driver_session
            and driver_session is not None
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
        if not self._is_current_ride(
            driver_session, order,
            "driver driving to pickup", "waiting for pickup", "driving to pickup",
        ):
            return False
        driver_session.location = order.pickup_location
        order.pickup_arrived_at = self.current_time
        order.state = "driver waiting for rider"
        order.rider_session.state = "driver has arrived"
        driver_session.state = "waiting for rider"
        self._schedule_ride_action(
            order, self.boarding_delay_seconds, lambda: driver_session.pick_up_rider(order)
        )
        return True

    def pick_up_rider(self, driver_session, order):
        """Board the rider after the deterministic waiting period."""
        if not self._is_current_ride(
            driver_session, order,
            "driver waiting for rider", "driver has arrived", "waiting for rider",
        ):
            return False
        order.boarded_at = self.current_time
        order.state = "driving with rider"
        order.rider_session.state = "riding"
        driver_session.state = "driving with rider"
        self._schedule_ride_action(
            order, order.duration_seconds, lambda: driver_session.end_ride(order)
        )
        return True

    def end_ride(self, driver_session, order):
        """Complete the trip, end the rider session, and keep the driver available."""
        if not self._is_current_ride(
            driver_session, order, "driving with rider", "riding", "driving with rider",
        ):
            return False
        driver_session.location = order.destination
        order.rider_session.location = order.destination
        self.finalize_order(order, "completed")
        self.end_rider_session(order.rider_session)
        return True

    def finalize_order(self, order, state, cancellation_reason=None):
        """Archive once; physical ride completion is coordinated by end_ride()."""
        if order.simulation is not self:
            raise ValueError("Order belongs to another simulation")
        if state not in Order.terminal_states:
            raise ValueError("Final order state must be completed or canceled")
        if order.state in Order.terminal_states:
            return order
        if order not in self.active_orders:
            raise ValueError("Order must be active in this simulation")
        if state == "canceled" and not cancellation_reason:
            raise ValueError("Canceled orders require a cancellation reason")
        if state == "completed" and cancellation_reason is not None:
            raise ValueError("Completed orders cannot have a cancellation reason")

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
        if order.rider_session.current_order is order:
            order.rider_session.current_order = None
            if self._is_active_event_owner(order.rider_session):
                order.rider_session.state = "online"
        driver = order.driver_session
        if driver is not None and driver.current_order is order:
            driver.current_order = None
            if self._is_active_event_owner(driver):
                driver.state = "waiting for order"
        return order

    def _is_active_event_owner(self, owner):
        if isinstance(owner, DriverSession):
            return (
                self.active_driver_sessions.get(owner.driver.id) is owner
                and owner.state != "offline"
            )
        if isinstance(owner, RiderSession):
            return (
                self.active_rider_sessions.get(owner.rider.id) is owner
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
        if delay_seconds < 0:
            raise ValueError("Cannot schedule an event in the past")
        if owner is not None and not self._is_active_event_owner(owner):
            raise ValueError("Event owner must be active in this simulation")

        execution_time = self.current_time + delay_seconds
        sequence = next(self._event_sequence)
        event = ScheduledEvent(callback, owner)
        if owner is not None:
            owner.pending_events.add(event)
        heapq.heappush(self._events, (execution_time, sequence, event))
        return event

    def advance_to(self, target_time):
        if target_time < self.current_time:
            raise ValueError("Cannot move time backwards")

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

    def register_driver_session(self, session):
        driver = session.driver
        if driver.simulation is not self:
            raise ValueError("Driver belongs to another simulation")
        if driver.id in self.active_driver_sessions:
            raise ValueError("Driver already has an active session")
        self.active_driver_sessions[driver.id] = session

    def register_rider_session(self, session):
        rider = session.rider
        if rider.simulation is not self:
            raise ValueError("Rider belongs to another simulation")
        if rider.id in self.active_rider_sessions:
            raise ValueError("Rider already has an active session")
        self.active_rider_sessions[rider.id] = session

    def end_driver_session(self, session):
        driver = session.driver
        if self.active_driver_sessions.get(driver.id) is not session:
            return
        if session.current_order is not None:
            raise ValueError("Cannot end a driver session during an accepted ride")
        offer = session.pending_offer
        self._cancel_pending_events(session)
        session.state = "offline"
        del self.active_driver_sessions[driver.id]
        driver.session = None
        self.driver_session_history.append(session)
        if offer is not None:
            self._resolve_offer(offer, "canceled")
            self.dispatch_order(offer.order)

    def end_rider_session(self, session):
        rider = session.rider
        if self.active_rider_sessions.get(rider.id) is not session:
            return
        if session.current_order is not None and session.current_order.driver_session is not None:
            raise ValueError("Cannot end a rider session during an accepted ride")
        if session.current_order is not None:
            self.finalize_order(session.current_order, "canceled", "rider ended session")
        self._cancel_pending_events(session)
        session.state = "offline"
        del self.active_rider_sessions[rider.id]
        rider.session = None
        self.rider_session_history.append(session)

    def run(self, start=6, end=23, seconds_per_hour=1):
        end_time = (end - start) * 3600
        if end_time < self.current_time:
            raise ValueError("Run end time is before the current simulation time")
        if seconds_per_hour < 0:
            raise ValueError("Playback speed cannot be negative")

        self.advance_to(self.current_time)
        while True:
            # The clock shows start time plus elapsed simulated seconds.
            hour, minute = divmod(int(start * 60 + self.current_time / 60), 60)
            driver_states = " | ".join(
                f"Driver {session.driver.id}: {session.state}"
                for session in self.active_driver_sessions.values()
            ) or "No active driver sessions"
            print(
                f"\r{hour:02}:{minute:02} | {driver_states:<60}",
                end="",
                flush=True,
            )
            if self.current_time >= end_time:
                break
            next_time = min(self.current_time + 60, end_time)
            time.sleep((next_time - self.current_time) * seconds_per_hour / 3600)
            self.advance_to(next_time)
        print()


def run_simulation(start=6, end=23, seconds_per_hour=1):
    simulation = Simulation()
    simulation.run(start, end, seconds_per_hour)
    return simulation


if __name__ == "__main__":
    run_simulation()
