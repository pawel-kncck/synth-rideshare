"""One seeded week from the published week preset: rotating crews, weekly peaks, daily checkpoints."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import run_scenario
from scenario import Scenario

if __name__ == "__main__":
    run_scenario(Scenario(preset="three-platform-week@1"), seed=0)
