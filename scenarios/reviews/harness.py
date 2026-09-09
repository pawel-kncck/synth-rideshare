"""Shared plumbing for the review scenario scripts: path bootstrap, run/reuse of a log, --check CLI.

Every scenario script under scenarios/reviews/ mirrors one folder under
scenario-reviews/sNN-*/ (see that directory's README.md for the name and
unit mapping) and exposes build() -> Scenario and evaluate(header, initial,
final, footer) -> metrics.Checks, so a future experiment runner can import
either half independently of the --check CLI here.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from main import run_scenario  # noqa: E402
from metrics import load_run  # noqa: E402


def run_and_log(scenario, *, seed=0, log_dir="logs"):
    """Compile, run once at the given seed, and return (simulation, its log path)."""
    sim = run_scenario(scenario, seed=seed, log_dir=log_dir)
    return sim, sim.log_path


def load(path):
    """metrics.load_run passthrough: (header, initial, final, footer) from a saved log."""
    return load_run(path)


def cli(description, build, evaluate):
    """Without --check: run and print the log path. With --check: run (or reuse --log) and evaluate.

    build() takes no arguments and returns a Scenario; evaluate(header,
    initial, final, footer) returns a metrics.Checks whose report() this
    prints, exiting with its return code. --log PATH with --check evaluates
    an already-saved log without re-running the scenario, so PR evidence
    stays reproducible from a saved artifact.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--seed", type=int, default=0, help="Replication seed for a fresh run (default: 0)")
    parser.add_argument("--check", action="store_true", help="Evaluate the scenario's observable checks")
    parser.add_argument("--log", type=Path, default=None, help="Evaluate this existing log instead of running")
    parser.add_argument("--log-dir", default="logs", help="Directory for a fresh run's log (default: logs)")
    args = parser.parse_args()
    if args.log is not None and not args.check:
        parser.error("--log only applies with --check")
    path = args.log if args.log is not None else run_and_log(build(), seed=args.seed, log_dir=args.log_dir)[1]
    if not args.check:
        return
    header, initial, final, footer = load(path)
    checks = evaluate(header, initial, final, footer)
    raise SystemExit(checks.report())
