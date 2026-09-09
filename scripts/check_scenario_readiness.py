"""Scenario validation (no unit-test framework), shared by local gates and CI.

Compare seed-0 @1 notification traces with a Git baseline, check snapshot/restore
equivalence, and validate settlement/order invariants. Phase-specific checks for
new mechanisms remain part of each issue's acceptance evidence and review.
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def capture(source, output):
    sys.path.insert(0, str(source))
    from main import Simulation
    from policy_contracts import plain
    from scenario import Scenario, compile_scenario

    results = {}
    for preset in ("three-platform-day@1", "three-platform-week@1"):
        inputs = compile_scenario(Scenario(preset=preset)).prepare(seed=0)
        complete = Simulation(inputs)
        trace = hashlib.sha256()

        def record(notification):
            trace.update(json.dumps(plain(notification), sort_keys=True).encode())
            trace.update(b"\n")

        complete.engine.add_listener(record)
        with tempfile.TemporaryDirectory(prefix="scenario-check-") as logs:
            complete.run(log_dir=logs)
        partial = Simulation(inputs)
        partial.advance_to(partial.horizon_seconds / 2)
        restored = Simulation.restore(json.loads(json.dumps(partial.snapshot())))
        restored.advance_to(restored.horizon_seconds)
        if restored.snapshot() != complete.snapshot():
            raise RuntimeError(f"{preset}: snapshot/restore differs from continuous run")
        orders = list(complete.engine.orders.values())
        finished = sum(order.state == "completed" for order in orders)
        canceled = sum(order.state == "canceled" for order in orders)
        active = sum(not order.terminal for order in orders)
        if len(orders) != finished + canceled + active:
            raise RuntimeError(f"{preset}: order conservation failed")
        for settlement in complete.engine.settlements.values():
            if settlement.rider_payment_minor != (
                settlement.driver_payout_minor + settlement.platform_contribution_minor
            ):
                raise RuntimeError(f"{preset}: settlement money conservation failed")
        results[preset] = {"notifications_sha256": trace.hexdigest(), "orders": len(orders),
                           "completed": finished, "canceled": canceled, "active": active}
    output.write_text(json.dumps(results, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--capture-source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.capture_source is not None:
        capture(args.capture_source.resolve(), args.output)
        return
    source = Path(__file__).resolve().parents[1]
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.base_ref + "^{commit}"], cwd=source, text=True
    ).strip()
    with tempfile.TemporaryDirectory(prefix="scenario-baseline-") as directory:
        root = Path(directory)
        baseline = root / "base"
        baseline.mkdir()
        archive = subprocess.check_output(["git", "archive", base_sha], cwd=source)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(baseline, filter="data")
        for label, checkout in (("before", baseline), ("after", source)):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--capture-source",
                            str(checkout), "--output", str(root / (label + ".json"))],
                           cwd=checkout, check=True, timeout=900,
                           env={**os.environ, "PYTHONHASHSEED": "0"})
        before = json.loads((root / "before.json").read_text())
        after = json.loads((root / "after.json").read_text())
        if before != after:
            raise RuntimeError("Seed-0 @1 behavior changed:\n" + json.dumps(
                {"before": before, "after": after}, indent=2))
        print("PASS: day/week @1 seed-0 traces unchanged; snapshot/restore and conservation agree")


if __name__ == "__main__":
    main()
