"""Offline metric reconstruction, aggregation and summary from a single raw run log."""

import argparse
import json
import math
from pathlib import Path


INTERVAL_MINUTES = (5, 15, 30, 60)

# Every created offer is pending or has exactly one of these dispositions.
OFFER_OUTCOME_KEYS = {
    "accepted": "accepted_offers", "rejected": "rejected_offers", "expired": "expired_offers",
    "canceled": "canceled_offers", "acceptance_failed": "failed_offers", "pending": "pending_offers",
}


def clock_label(seconds):
    """Format simulated clock seconds, including day offsets after midnight."""
    day, seconds = divmod(int(seconds), 86400)
    hour, seconds = divmod(seconds, 3600)
    minute, second = divmod(seconds, 60)
    return f"{'D+' + str(day) + ' ' if day else ''}{hour:02}:{minute:02}:{second:02}"


def aggregate_intervals(records, start_seconds, end_seconds, interval_minutes=15, start_hour=6):
    """Aggregate counts and exact driver durations into consecutive intervals.

    Intervals start at this run's initial simulated time. They are half-open,
    except the last interval includes events at the run end, matching run().
    The caller supplies only searches/completions observed during this run;
    this prevents counting boundary events again in a continuation. Active
    driver time is physical service (pickup travel, boarding, transport), so
    an accepted order queued behind another ride adds nothing until it starts.
    """
    if (
        isinstance(interval_minutes, bool)
        or not isinstance(interval_minutes, (int, float))
        or not math.isfinite(interval_minutes)
        or interval_minutes <= 0
    ):
        raise ValueError("interval_minutes must be a finite positive number")
    if not all(math.isfinite(t) for t in (start_seconds, end_seconds, start_hour)):
        raise ValueError("Report times must be finite")
    if end_seconds < start_seconds:
        raise ValueError("Report end must not precede its start")

    width = interval_minutes * 60
    count = max(1, math.ceil((end_seconds - start_seconds) / width))
    rows = []
    for index in range(count):
        left = start_seconds + index * width
        right = min(left + width, end_seconds)
        rows.append({
            "start_seconds": left,
            "end_seconds": right,
            "clock_start": clock_label(start_hour * 3600 + left),
            "clock_end": clock_label(start_hour * 3600 + right),
            "searches": 0,
            "covered_searches": 0,
            "online_driver_seconds": 0.0,
            "active_driver_seconds": 0.0,
            "completed_orders": 0,
            "rider_sessions": 0,
            "converted_sessions": 0,
            "undecided_sessions": 0,
            "declined_sessions": 0,
            "unavailable_sessions": 0,
            "offers": 0,
            "accepted_offers": 0,
            "rejected_offers": 0,
            "expired_offers": 0,
            "canceled_offers": 0,
            "failed_offers": 0,
            "pending_offers": 0,
        })

    def index_at(at_seconds):
        return min(count - 1, max(0, int((at_seconds - start_seconds) // width)))

    # Ratios follow the cohort that started in this run. Assign later decisions
    # back to their session/offer's interval, so delays never produce >100%.
    session_rows, offer_rows = {}, {}
    for record in records:
        if not start_seconds <= record.get("at_seconds", -math.inf) <= end_seconds:
            continue
        row = rows[index_at(record["at_seconds"])]
        if record["type"] == "rider_session_started":
            session_rows[record["session_id"]] = row
            row["rider_sessions"] += 1
            row["undecided_sessions"] += 1
        elif record["type"] == "offer_created":
            offer_rows[record["offer_id"]] = row
            row["offers"] += 1
            row["pending_offers"] += 1

    for record in records:
        kind = record["type"]
        if kind in ("rider_decision", "offer_resolved"):
            if not start_seconds <= record["at_seconds"] <= end_seconds:
                continue
            if kind == "rider_decision":
                row = session_rows.get(record["session_id"])
                if row is not None:
                    row["undecided_sessions"] -= 1
                    key = ("converted_sessions" if record["ordered"] else
                           "unavailable_sessions" if record["eta_seconds"] is None else "declined_sessions")
                    row[key] += 1
            else:
                row = offer_rows.get(record["offer_id"])
                if row is not None:
                    row["pending_offers"] -= 1
                    row[OFFER_OUTCOME_KEYS[record["state"]]] += 1
        elif kind in ("search", "order_completed"):
            at = record["at_seconds"]
            if not start_seconds <= at <= end_seconds:
                continue
            row = rows[index_at(at)]
            if kind == "search":
                row["searches"] += 1
                row["covered_searches"] += int(record["drivers_available"])
            else:
                row["completed_orders"] += 1
        elif kind in ("driver_online", "driver_active"):
            left = max(start_seconds, record["start_seconds"])
            right = min(end_seconds, record["end_seconds"])
            if right <= left:
                continue
            key = "online_driver_seconds" if kind == "driver_online" else "active_driver_seconds"
            for index in range(index_at(left), index_at(right) + 1):
                row = rows[index]
                row[key] += max(0, min(right, row["end_seconds"]) - max(left, row["start_seconds"]))

    cumulative = 0
    for row in rows:
        searches = row["searches"]
        online, active = row["online_driver_seconds"], row["active_driver_seconds"]
        row["coverage_pct"] = 100 * row["covered_searches"] / searches if searches else None
        row["utilization_pct"] = 100 * active / online if online else None
        row["idle_driver_seconds"] = online - active
        row["session_to_order_pct"] = (100 * row["converted_sessions"] / row["rider_sessions"]
                                       if row["rider_sessions"] else None)
        row["offer_acceptance_pct"] = (100 * row["accepted_offers"] / row["offers"]
                                       if row["offers"] else None)
        cumulative += row["completed_orders"]
        row["cumulative_completed_orders"] = cumulative
    return rows


def summarize_intervals(rows):
    """Weight daily ratios by searches/driver time, rather than averaging percentages."""
    searches = sum(row["searches"] for row in rows)
    covered = sum(row["covered_searches"] for row in rows)
    online = math.fsum(row["online_driver_seconds"] for row in rows)
    active = math.fsum(row["active_driver_seconds"] for row in rows)
    market = {key: sum(row[key] for row in rows) for key in (
        "rider_sessions", "converted_sessions", "undecided_sessions", "declined_sessions",
        "unavailable_sessions", "offers", "accepted_offers", "rejected_offers", "expired_offers",
        "canceled_offers", "failed_offers", "pending_offers",
    )}
    return {
        **market,
        "session_to_order_pct": (100 * market["converted_sessions"] / market["rider_sessions"]
                                  if market["rider_sessions"] else None),
        "offer_acceptance_pct": (100 * market["accepted_offers"] / market["offers"]
                                  if market["offers"] else None),
        "searches": searches,
        "covered_searches": covered,
        "coverage_pct": 100 * covered / searches if searches else None,
        "online_driver_hours": online / 3600,
        "active_driver_hours": active / 3600,
        "idle_driver_hours": (online - active) / 3600,
        "utilization_pct": 100 * active / online if online else None,
        "completed_orders": sum(row["completed_orders"] for row in rows),
    }


def load_run(path):
    """Read a closed raw log without importing or restoring the simulation.

    Notifications are diagnostic; authoritative boundary snapshots provide the
    metric inputs. A missing terminal record is an incomplete log, not a run
    with zero outcomes. Handled failures have a final snapshot and are analyzed
    only through their actual stopping time, with failed status preserved.
    """
    header = footer = initial = final = None
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line, parse_constant=_reject_constant)
                kind = record["type"]
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"Invalid log record at line {line_number}: {error}") from error
            if footer is not None:
                raise ValueError("Records found after run_finished")
            if kind == "run_started":
                if header is not None or line_number != 1:
                    raise ValueError("Expected exactly one run_started record at the beginning")
                if record.get("schema_version") != 2:
                    raise ValueError("Unsupported run log schema")
                header = record
            elif header is None:
                raise ValueError("Missing run_started record; legacy text logs are not supported")
            elif kind == "state":
                snapshot = record["snapshot"]
                if (snapshot.get("schema_version") != 2
                        or snapshot["engine"].get("schema_version") != 2
                        or snapshot["scheduler"].get("schema_version") != 1
                        or snapshot["policies"].get("schema_version") != 2):
                    raise ValueError("Unsupported state schema in run log")
                if record["boundary"] == "initial" and initial is None and final is None:
                    initial = snapshot
                elif record["boundary"] == "final" and initial is not None and final is None:
                    final = snapshot
                else:
                    raise ValueError("Invalid or duplicate state boundary")
                if record["at_seconds"] != snapshot["scheduler"]["clock_seconds"]:
                    raise ValueError("State timestamp does not match its scheduler")
            elif kind == "run_finished":
                footer = record
            elif kind != "notification":
                raise ValueError(f"Unknown log record type: {kind!r}")
    if any(value is None for value in (header, initial, final, footer)):
        raise ValueError("Incomplete log: initial/final states and run_finished are required")
    start, end = initial["scheduler"]["clock_seconds"], final["scheduler"]["clock_seconds"]
    if (start != header["at_seconds"] or end != footer["at_seconds"]
            or end < start or end > header["target_end_seconds"]):
        raise ValueError("Inconsistent run boundaries")
    if footer["status"] not in ("completed", "failed"):
        raise ValueError("Unknown run status")
    if footer["status"] == "completed" and end != header["target_end_seconds"]:
        raise ValueError("Completed run did not reach its target")
    return header, initial, final, footer


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _tables(snapshot):
    return {name: {record["id"]: record for record in snapshot["engine"][name]}
            for name in ("shifts", "intents", "quotes", "orders", "offers", "services", "settlements")}


def _new_terminal(before, after, state):
    """Select transitions, including old orders completing after a continuation."""
    return [order for identity, order in after["orders"].items()
            if order["state"] == state and before["orders"].get(identity, {}).get("state") != state]


def collect_metric_records(initial, final):
    """Derive metric facts offline from raw state, never from scoped notifications.

    Conversion/acceptance use cohorts created in this run and observed through
    its end. Completions use transition time, including carry-in orders. Compare
    identities and states at the boundary, so an already-observed event at the
    same timestamp is not counted twice. Online/service spans include carry-in
    activity and are clipped during aggregation.
    """
    before, after = _tables(initial), _tables(final)
    end = final["scheduler"]["clock_seconds"]
    records = []
    for identity, quote in after["quotes"].items():
        if identity not in before["quotes"]:
            records.append({"type": "search", "at_seconds": quote["at"],
                            "drivers_available": quote["eta_seconds"] is not None})
    for identity, intent in after["intents"].items():
        if identity in before["intents"]:
            continue
        records.append({"type": "rider_session_started", "at_seconds": intent["created_at"],
                        "session_id": identity})
        decision_at = intent["converted_at"] if intent["converted_at"] is not None else intent["ended_at"]
        if decision_at is not None:
            etas = [after["quotes"][q]["eta_seconds"] for q in intent["quote_ids"]]
            records.append({"type": "rider_decision", "at_seconds": decision_at,
                            "session_id": identity, "ordered": intent["converted_at"] is not None,
                            "eta_seconds": next((eta for eta in etas if eta is not None), None)})
    for identity, offer in after["offers"].items():
        if identity in before["offers"]:
            continue
        records.append({"type": "offer_created", "at_seconds": offer["created_at"], "offer_id": identity})
        if offer["state"] != "pending":
            records.append({"type": "offer_resolved", "at_seconds": offer["resolved_at"],
                            "offer_id": identity, "state": offer["state"]})
    for order in _new_terminal(before, after, "completed"):
        records.append({"type": "order_completed", "at_seconds": order["timeline"]["completed"]})
    for table, kind in (("shifts", "driver_online"), ("services", "driver_active")):
        for item in after[table].values():
            records.append({"type": kind, "start_seconds": item["started_at"],
                            "end_seconds": end if item["ended_at"] is None else item["ended_at"]})
    return records


def calculate_metrics(path, interval_minutes=None):
    """Calculate the full run summary and optional intervals from one saved log.

    Uses only the standard library and raw values in the log. No current model
    defaults, engine objects, reporting files, or other run artifacts are needed.
    The input is never modified. All timestamps are simulated seconds; exact
    settlement totals are retained in integer minor units.
    """
    header, initial, final, footer = load_run(path)
    start, end = initial["scheduler"]["clock_seconds"], final["scheduler"]["clock_seconds"]
    records = collect_metric_records(initial, final)
    # A single whole-run bucket reuses the cohort/time definitions without
    # calculating interval series unless the caller asks for them.
    summary = summarize_intervals(aggregate_intervals(
        records, start, end, max(1, (end - start) / 60), header["start_hour"]))
    before, after = _tables(initial), _tables(final)
    new_orders = [order for identity, order in after["orders"].items() if identity not in before["orders"]]
    completed = _new_terminal(before, after, "completed")
    canceled = _new_terminal(before, after, "canceled")
    new_settlements = [s for identity, s in after["settlements"].items() if identity not in before["settlements"]]
    units = final["engine"]["world"]["minor_units_per_major"]
    money_keys = ("rider_payment_minor", "driver_payout_minor", "platform_contribution_minor")

    def money_totals(settlements):
        return {key: sum(s[key] for s in settlements) for key in money_keys}

    # Materialize each cohort once: all monetary fields must see the same records.
    completed_money = money_totals([s for s in new_settlements if s["reason"] == "completed_ride"])
    cancellation_money = money_totals([s for s in new_settlements if s["reason"] != "completed_ride"])
    waits = [o["timeline"]["arrived"] - o["created_at"] for o in completed]
    platform_completions = {p["id"]: 0 for p in final["engine"]["platforms"]}
    for order in completed:
        platform_completions[order["platform_id"]] += 1
    summary.update({
        "simulated_seconds": end - start,
        "driver_shifts_started": len(set(after["shifts"]) - set(before["shifts"])),
        "drivers_on_shift_at_end": sum(d["shift_id"] is not None for d in final["engine"]["drivers"]),
        "live_intents_at_end": sum(i["ended_at"] is None for i in after["intents"].values()),
        "orders_created": len(new_orders),
        "orders_active_at_start": sum(o["state"] not in ("completed", "canceled") for o in before["orders"].values()),
        "canceled_orders": len(canceled),
        "orders_active_at_end": sum(o["state"] not in ("completed", "canceled") for o in after["orders"].values()),
        "queued_orders_at_end": sum(max(0, len(d["commitments"]) - 1) for d in final["engine"]["drivers"]),
        "riders_leaving_unserved": sum(i["outcome"] == "abandoned" and
            before["intents"].get(identity, {}).get("ended_at") is None
            for identity, i in after["intents"].items()),
        "completed_distance_km": math.fsum(math.dist(leg["origin"], leg["destination"])
            for o in completed for leg in after["services"][o["service_id"]]["legs"] if leg["kind"] == "transport"),
        "average_order_to_pickup_seconds": math.fsum(waits) / len(waits) if waits else None,
        "minor_units_per_major": units,
        "settlements": money_totals(new_settlements),
        "completed_ride_settlements": completed_money,
        "cancellation_settlements": cancellation_money,
        "rider_payments": completed_money["rider_payment_minor"] / units,
        "driver_payouts": completed_money["driver_payout_minor"] / units,
        "platform_contribution": completed_money["platform_contribution_minor"] / units,
        "platform_completions": platform_completions,
        "platform_completion_share_pct": {p: 100 * n / len(completed) if completed else None
                                          for p, n in platform_completions.items()},
        "events_processed": final["scheduler"]["processed_count"] - initial["scheduler"]["processed_count"],
        "events_pending_at_end": len(final["scheduler"]["events"]),
    })
    result = {"run": {"id": header["run_id"], "status": footer["status"],
                      "initial_time_seconds": start, "end_time_seconds": end,
                      "target_end_seconds": header["target_end_seconds"], "start_hour": header["start_hour"],
                      "runtime_seconds": footer["runtime_seconds"], "time_scale": header["time_scale"]},
              "summary": summary}
    if footer["status"] == "failed":
        result["run"]["error"] = footer["error"]
    if interval_minutes is not None:
        result["interval_minutes"] = interval_minutes
        result["intervals"] = aggregate_intervals(records, start, end, interval_minutes, header["start_hour"])
    return result


def main():
    parser = argparse.ArgumentParser(description="Calculate metrics after a run from its structured simulation.log.")
    parser.add_argument("log", type=Path, help="Path to simulation.log (JSON Lines schema 1)")
    parser.add_argument("--interval-minutes", type=int, choices=INTERVAL_MINUTES,
                        help="Include interval metrics; omitted by default")
    args = parser.parse_args()
    try:
        result = calculate_metrics(args.log, args.interval_minutes)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Cannot analyze log: {error}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
