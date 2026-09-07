"""A seeded day with 500 riders and 10 drivers, played without sleeping."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make main.py importable

from main import Simulation

seed = 0
rng = random.Random(seed)
sim = Simulation(driver_count=10, rider_count=500, seed=seed)

# Locations are kilometre coordinates on a 10-by-10 km map.
locations = [(x, y) for x in range(11) for y in range(11)]

# All ten drivers work from 06:00 to 23:00.
for driver_id in sim.drivers:
    sim.schedule_driver_session(0, driver_id, rng.choice(locations), shift_seconds=17 * 3600)

# Each rider searches once between 06:00 and 22:00, leaving the last
# hour for remaining trips to finish. Pickup and destination are distinct.
for rider_id in sim.riders:
    at_seconds = rng.randint(0, 16 * 3600)
    pickup, destination = rng.sample(locations, 2)
    sim.schedule_rider_session(at_seconds, rider_id, pickup, destination)

sim.run(start=6, end=23, time_scale=False)
