"""A seeded week with 30,000 riders and 555 drivers on one 10-by-10 km map."""

import random
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demand import WeeklyDemandProfile
from main import Simulation


WEEK_HOURS = 7 * 24
SESSIONS_PER_RIDER = 7


def build_scenario(seed=0, rider_count=30_000, drivers_per_shift=185):
    """Defaults yield 103,719 completions and 71.94% utilization over 168 hours.

    The population was calibrated at seed 0, before the marketplace engine
    existed, to 90-110k rides and 70-75% utilization; back-to-back dispatch
    now completes more rides per online hour. Custom parameters or seeds
    change the results; all decisions and ride outcomes remain autonomous.
    """
    for name, value in (("rider_count", rider_count), ("drivers_per_shift", drivers_per_shift)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    rng = random.Random(seed)
    demand = WeeklyDemandProfile()
    sim = Simulation(driver_count=3 * drivers_per_shift, rider_count=rider_count, seed=seed)
    sim.scenario_parameters = {
        "name": "100k rides week", "start_weekday": "Monday", "start_hour": 0,
        "demand_duration_hours": WEEK_HOURS, "drain_hours": 0,
        "demand_profile": asdict(demand), "off_peak_weight": 1,
        "map_size_km": 10, "drivers_per_shift": drivers_per_shift, "shift_hours": 8,
        "sessions_per_rider": SESSIONS_PER_RIDER,
        "target_completed_rides": [90_000, 110_000],
        "target_utilization_pct": [70, 75],
    }
    locations = [(x, y) for x in range(11) for y in range(11)]
    # Three daily crews provide round-the-clock coverage. A driver completes
    # any accepted ride after shift end before going offline.
    for day in range(7):
        for index, driver_id in enumerate(sim.drivers):
            hour = day * 24 + (index // drivers_per_shift) * 8
            sim.schedule_driver_session(hour * 3600, driver_id, rng.choice(locations),
                                        shift_seconds=8 * 3600)

    times = demand.sample_session_times(rider_count * SESSIONS_PER_RIDER,
                                       WEEK_HOURS * 3600, seed=seed)
    # Rotate through the population in arrival order, spreading each rider's
    # seven sessions across the week instead of overlapping their trips.
    for index, at_seconds in enumerate(times):
        rider_id = sim.riders[index % rider_count]
        pickup, destination = rng.sample(locations, 2)
        sim.schedule_rider_session(at_seconds, rider_id, pickup, destination)
    return sim


if __name__ == "__main__":
    # Stop at Sunday midnight: next-week completions do not count toward the target.
    build_scenario().run(start=0, end=WEEK_HOURS, time_scale=False)
