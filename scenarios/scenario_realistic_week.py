"""Monday through Sunday: rider choices, driver rejections, and weekly peaks."""

import random
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demand import WeeklyDemandProfile
from main import Simulation


def build_scenario(seed=0, rider_count=3500):
    rng = random.Random(seed)
    demand = WeeklyDemandProfile()
    sim = Simulation(driver_count=30, rider_count=rider_count, seed=seed)
    sim.scenario_parameters = {
        "name": "Realistic week", "start_weekday": "Monday", "start_hour": 0,
        "demand_duration_hours": 168, "drain_hours": 1,
        "demand_profile": asdict(demand), "off_peak_weight": 1,
        "map_size_km": 10, "drivers_per_shift": 10, "shift_hours": 8,
    }
    locations = [(x, y) for x in range(11) for y in range(11)]
    # Three crews cover each day. Each driver works eight hours, with sixteen
    # hours before the next shift; rides in progress finish after shift end.
    for day in range(7):
        for index, driver_id in enumerate(sim.drivers):
            hour = day * 24 + (index // 10) * 8
            sim.schedule_driver_session(hour * 3600, driver_id, rng.choice(locations),
                                        shift_seconds=8 * 3600)
    # Session demand peaks before riders see a fare or decide whether to order.
    times = demand.sample_session_times(rider_count, 7 * 86400, seed=seed)
    for rider_id, at_seconds in zip(sim.riders, times):
        pickup, destination = rng.sample(locations, 2)
        sim.schedule_rider_session(at_seconds, rider_id, pickup, destination)
    return sim


if __name__ == "__main__":
    build_scenario().run(start=0, end=169, time_scale=False, interval_minutes=60)
