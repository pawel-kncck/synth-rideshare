"""Scenario validation (no unit-test framework), shared by local gates and CI.

Compare seed-0 @1 notification traces with a Git baseline, check snapshot/restore
equivalence, reconcile order notifications and verify frozen settlement terms.
Account/transfer conservation is not covered: those records arrive in phase 3.
Phase-specific checks for
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
from collections import Counter
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
        events = {kind: Counter() for kind in ("order_created", "order_completed", "order_canceled")}

        def record(notification):
            trace.update(json.dumps(plain(notification), sort_keys=True).encode())
            trace.update(b"\n")
            if notification.audience == "platform" and notification.kind in events:
                events[notification.kind][notification.data["order_id"]] += 1

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
        active = sum(order.state in ("open", "assigned") for order in orders)
        if events["order_created"] != Counter(order.id for order in orders):
            raise RuntimeError(f"{preset}: created notifications differ from stored orders")
        for state, kind in (("completed", "order_completed"), ("canceled", "order_canceled")):
            if events[kind] != Counter(order.id for order in orders if order.state == state):
                raise RuntimeError(f"{preset}: {kind} notifications differ from terminal records")
        if sum(events["order_created"].values()) != finished + canceled + active:
            raise RuntimeError(f"{preset}: created events do not reconcile with order states")
        linked = Counter(order_id for intent in complete.engine.intents.values()
                         for order_id in intent.order_ids)
        if linked != events["order_created"]:
            raise RuntimeError(f"{preset}: intent order links differ from created notifications")
        settled = Counter()
        for settlement in complete.engine.settlements.values():
            if settlement.reason != "completed_ride":
                continue
            order = complete.engine.orders[settlement.order_id]
            settled[order.id] += 1
            if (settlement.rider_payment_minor != order.fare.rider_payment_minor
                    or settlement.driver_payout_minor != order.assignment.payout.driver_payout_minor
                    or settlement.platform_contribution_minor != (
                        order.fare.rider_payment_minor - order.assignment.payout.driver_payout_minor)):
                raise RuntimeError(f"{preset}: settlement differs from frozen quote/offer terms")
        if settled != events["order_completed"]:
            raise RuntimeError(f"{preset}: missing or duplicate completed-ride settlements")
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
        if args.output is None:
            parser.error("--capture-source requires --output")
        capture(args.capture_source.resolve(), args.output)
        return
    source = Path(__file__).resolve().parents[1]
    base_ref = args.base_ref
    if not base_ref or set(base_ref) == {"0"}:
        base_ref = "HEAD^"
        print("No previous push revision; comparing against the current commit's first parent")
    resolved = subprocess.run(["git", "rev-parse", "--verify", base_ref + "^{commit}"],
                              cwd=source, text=True, capture_output=True)
    if resolved.returncode:
        parser.error(f"comparison baseline {base_ref!r} is unavailable; fetch main or provide --base-ref")
    base_sha = resolved.stdout.strip()
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
                           cwd=checkout, check=True, timeout=240,
                           env={**os.environ, "PYTHONHASHSEED": "0"})
        before = json.loads((root / "before.json").read_text())
        after = json.loads((root / "after.json").read_text())
        if before != after:
            raise RuntimeError("Seed-0 @1 behavior changed:\n" + json.dumps(
                {"before": before, "after": after}, indent=2))
        print("PASS: day/week @1 seed-0 traces unchanged; snapshot/restore, order lifecycle and frozen settlement terms agree")
        print("Account/transfer conservation and new mechanisms require phase-specific acceptance evidence")


if __name__ == "__main__":
    main()
