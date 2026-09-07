"""Persist run records and build a self-contained, offline Plotly report."""

import csv
import hashlib
import itertools
import json
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from plotly import __version__ as plotly_version
from plotly.offline import get_plotlyjs

from metrics import INTERVAL_MINUTES, aggregate_intervals, summarize_intervals


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run_configuration(sim, start, end, time_scale, initial_time, interval_minutes):
    source_dir = Path(__file__).resolve().parent
    return {
        "schema_version": 1,
        "run": {
            "id": sim.run_directory.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "start_hour": start,
            "end_hour": end,
            "initial_time_seconds": initial_time,
            "end_time_seconds": (end - start) * 3600,
            "time_scale": time_scale,
            "status": "running",
        },
        "simulation": {
            "seed": sim.seed,
            "driver_count": len(sim.drivers),
            "rider_count": len(sim.riders),
            **{key: getattr(sim, key) for key in (
                "speed_kmh", "base_fare", "price_per_km", "boarding_delay_seconds",
                "order_delay_seconds", "accept_delay_seconds", "offer_timeout_seconds",
                "max_offers_per_order",
            )},
        },
        "scheduled_sessions": sim.session_schedule,
        "report": {
            "default_interval_minutes": interval_minutes,
            "available_interval_minutes": list(INTERVAL_MINUTES),
            "plotly_version": plotly_version,
            "coverage_definition": "A search finding at least one eligible idle driver anywhere on the map.",
            "utilization_definition": "Active driver seconds divided by online driver seconds; pending offers are idle.",
            "interval_definition": "Intervals start at the run's initial time; only the final interval includes its right endpoint.",
        },
        "source_sha256": {
            name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
            for name in ("main.py", "metrics.py", "reporting.py", "report_template.html")
        },
    }


def collect_records(sim, initial_totals, initial_time):
    """Copy the run's observations so subsequent simulation changes cannot alter them."""
    records = [
        {"type": "search", **asdict(search)}
        for search in sim.search_history[initial_totals["searches"]:]
    ]
    for session in itertools.chain(sim.driver_session_history, sim.active_driver_sessions.values()):
        left = max(initial_time, session.started_at)
        right = min(sim.current_time, session.ended_at if session.ended_at is not None else sim.current_time)
        if right >= left:
            records.append({
                "type": "driver_online", "driver_id": session.driver_id,
                "start_seconds": left, "end_seconds": right,
                "session_started_at": session.started_at, "session_ended_at": session.ended_at,
            })
    for order in itertools.chain(sim.order_history, sim.active_orders):
        accepted_at = order.timeline.get("driver driving to pickup")
        if accepted_at is None:
            continue
        ended_at = order.timeline[order.state] if order.state in order.terminal_states else None
        left = max(initial_time, accepted_at)
        right = min(sim.current_time, ended_at if ended_at is not None else sim.current_time)
        if right >= left:
            records.append({
                "type": "driver_active", "driver_id": order.driver_session.driver_id,
                "order_id": order.id, "start_seconds": left, "end_seconds": right,
                "accepted_at": accepted_at, "ended_at": ended_at,
            })
    for order in sim.order_history[initial_totals["finished_orders"]:]:
        if order.state == "completed":
            records.append({
                "type": "order_completed", "at_seconds": order.timeline["completed"],
                "order_id": order.id, "driver_id": order.driver_session.driver_id,
                "rider_id": order.rider_session.rider_id,
                "distance_km": order.quote.distance_km, "fare": order.quote.price,
            })
    return records


@lru_cache(maxsize=1)
def _plotly_bundle():
    return get_plotlyjs()


def write_report(directory, configuration, records):
    directory = Path(directory)
    run = configuration["run"]
    series = {
        str(minutes): aggregate_intervals(
            records, run["initial_time_seconds"], run["end_time_seconds"], minutes, run["start_hour"]
        )
        for minutes in INTERVAL_MINUTES
    }
    selected = series[str(configuration["report"]["default_interval_minutes"])]
    payload = {
        "configuration": configuration,
        "summary": summarize_intervals(selected),
        "series": series,
    }
    write_json(directory / "config.json", configuration)
    write_json(directory / "summary.json", payload["summary"])
    with (directory / "events.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
    with (directory / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)

    template = Path(__file__).with_name("report_template.html").read_text(encoding="utf-8")
    # Escape HTML-sensitive JSON characters so strings cannot terminate the
    # data script. User-supplied values are rendered using textContent in JS.
    data = json.dumps(payload, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    html = template.replace("__PLOTLY_BUNDLE__", _plotly_bundle()).replace("__REPORT_DATA__", data)
    report_path = directory / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
