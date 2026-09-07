import heapq
import itertools
import math
import random
import time
from dataclasses import dataclass
from typing import Optional


class DriverSession:
    states = (
        "online",
        "waiting for order",
        "driving to pickup",
        "waiting for rider",
        "driving with rider",
        "offline",
    )

    def __init__(self, driver, location):
        self.driver = driver
        self.location = location
        self.state = "online"

    def wait_for_order(self):
        self.state = "waiting for order"

    def accept_order(self):
        self.state = "driving to pickup"

    def arrive_at_pickup(self):
        self.state = "waiting for rider"

    def pick_up_rider(self):
        self.state = "driving with rider"

    def end_ride(self):
        self.state = "waiting for order"

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
        "offline",
    )

    def __init__(self, rider, location, destination):
        self.rider = rider
        self.location = location
        self.destination = destination
        self.state = "online"
        self.search_result = None

    def search(self, destination):
        """Search immediately and replace the stored response snapshot."""
        if self.state == "offline":
            raise ValueError("Cannot search after the rider session has ended")
        self.destination = destination
        self.search_result = self.rider.simulation.search(self)
        return self.search_result

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
    states = (
        "searching for a driver",
        "waiting for driver to accept",
        "driver driving to pickup",
        "driver waiting for rider",
        "driving with rider",
    )

    def __init__(self, rider):
        self.rider = rider
        self.state = "searching for a driver"


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
    def __init__(
        self,
        driver_count=10,
        rider_count=100,
        seed=0,
        speed_kmh=30,
        base_fare=2.0,
        price_per_km=1.5,
    ):
        if speed_kmh <= 0:
            raise ValueError("Driving speed must be positive")
        if base_fare < 0 or price_per_km < 0:
            raise ValueError("Fare values cannot be negative")

        self.speed_kmh = speed_kmh
        self.base_fare = base_fare
        self.price_per_km = price_per_km
        self.drivers = generate_drivers(self, driver_count, seed)
        self.riders = generate_riders(self, rider_count, seed)
        self.active_driver_sessions = {}
        self.active_rider_sessions = {}
        self.active_orders = []
        self.driver_session_history = []
        self.rider_session_history = []
        self.current_time = 0
        self._events = []
        self._event_sequence = itertools.count()

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

        distance_km = math.dist(rider_session.location, rider_session.destination)
        pickup_distance_km = min(
            (
                math.dist(rider_session.location, session.location)
                for session in self.active_driver_sessions.values()
                if session.state == "waiting for order"
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

    def schedule(self, delay_seconds, callback):
        if delay_seconds < 0:
            raise ValueError("Cannot schedule an event in the past")

        execution_time = self.current_time + delay_seconds
        sequence = next(self._event_sequence)
        heapq.heappush(self._events, (execution_time, sequence, callback))

    def advance_to(self, target_time):
        if target_time < self.current_time:
            raise ValueError("Cannot move time backwards")

        while self._events and self._events[0][0] <= target_time:
            execution_time, _, callback = heapq.heappop(self._events)
            self.current_time = execution_time
            callback()

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
        session.state = "offline"
        del self.active_driver_sessions[driver.id]
        driver.session = None
        self.driver_session_history.append(session)

    def end_rider_session(self, session):
        rider = session.rider
        if self.active_rider_sessions.get(rider.id) is not session:
            return
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
