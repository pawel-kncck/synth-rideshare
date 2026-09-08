import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make main.py importable

from main import Simulation

sim = Simulation(driver_count=2, rider_count=5, seed=0)

# A scenario only decides who shows up when. Everything after that (searching,
# ordering, offers, driving, boarding, drop-off) plays out inside the simulation.
sim.schedule_driver_session(0,    sim.drivers[0], (7, 2))
sim.schedule_rider_session(1000,  sim.riders[0], (0, 1), (2, 8))
sim.schedule_rider_session(3000,  sim.riders[0], (0, 1), (2, 8))
sim.schedule_rider_session(5000,  sim.riders[0], (8, 2), (0, 1))
sim.schedule_rider_session(7500,  sim.riders[0], (1, 1), (15, 11))

sim.run()
