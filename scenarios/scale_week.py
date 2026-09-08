"""A large seeded week: 30,000 riders taking seven trips each, served by 555 drivers in three crews.

This is the scale workload for profiling. Its outcomes are synthetic; earlier
single-platform calibration figures are historical references, not targets.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import run_scenario
from scenario import Scenario


def build(rider_count=30_000, driver_count=555, trips_per_rider=7):
    return Scenario(preset="three-platform-week@1", name="scale-week").with_changes({
        "population.riders.count": rider_count,
        "population.drivers.count": driver_count,
        "activity.trips.trips_per_rider": trips_per_rider,
        "activity.trips.duration_hours": 168,
    })


if __name__ == "__main__":
    run_scenario(build(), seed=0)
