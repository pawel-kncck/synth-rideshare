"""Time-based aggregation of immutable run records, independent of playback."""

import math


INTERVAL_MINUTES = (5, 15, 30, 60)


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
    this prevents counting boundary events again in a continuation.
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
                    row[record["state"] + "_offers"] += 1
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
        "canceled_offers", "pending_offers",
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
