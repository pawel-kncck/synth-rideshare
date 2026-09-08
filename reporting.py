"""Persist run records and build a self-contained, offline Plotly report."""

import csv
import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from plotly import __version__ as plotly_version
from plotly.offline import get_plotlyjs

from metrics import INTERVAL_MINUTES, OFFER_OUTCOME_KEYS, aggregate_intervals, summarize_intervals


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run_configuration(sim, start, end, time_scale, initial_time, interval_minutes):
    source_dir = Path(__file__).resolve().parent
    return {
        "schema_version": 2,
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
            "platform_id": sim.platform_id,
            "driver_count": len(sim.drivers),
            "rider_count": len(sim.riders),
            "minor_units_per_major": sim.world.minor_units_per_major,
            **{key: getattr(sim, key) for key in (
                "speed_kmh", "base_fare", "price_per_km", "commission_fraction",
                "boarding_delay_seconds", "order_delay_seconds", "accept_delay_seconds",
                "offer_timeout_seconds", "max_offers_per_order",
                "rider_order_probability", "driver_acceptance_probability",
                "rider_price_sensitivity", "driver_price_sensitivity",
                "rider_eta_sensitivity", "driver_eta_sensitivity",
                "reference_price", "reference_eta_seconds",
            )},
        },
        "scenario": sim.scenario_parameters,
        "scheduled_sessions": sim.session_schedule,
        "report": {
            "default_interval_minutes": interval_minutes,
            "available_interval_minutes": list(INTERVAL_MINUTES),
            "plotly_version": plotly_version,
            "coverage_definition": "A quote finding at least one eligible driver with a free order slot anywhere on the map.",
            "utilization_definition": "Physical service seconds (pickup travel, boarding, transport) divided by online driver seconds; pending offers and queued commitments are idle.",
            "interval_definition": "Intervals start at the run's initial time; only the final interval includes its right endpoint.",
            "session_to_order_definition": "Sessions that placed an order / sessions started in this run, grouped by session start; outcomes observed through run end, including undecided sessions in denominator.",
            "offer_acceptance_definition": "Accepted offers / offers created in this run, grouped by offer creation; outcomes observed through run end, including pending, rejected, expired, canceled and failed-acceptance offers in denominator.",
        },
        "source_sha256": {
            name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
            for name in ("main.py", "marketplace_engine.py", "event_engine.py", "behavior.py", "demand.py",
                         "metrics.py", "reporting.py", "report_template.html", "report_dashboard.js")
        },
    }


def collect_records(sim, cursor, initial_time):
    """Copy this run's observations so subsequent simulation changes cannot alter them.

    Engine records have sequential ids; the cursor holds the counts at run
    start, so only records created by this run are exported. Driver spans
    are clipped to the run and come from physical shifts and services.
    """
    engine, now = sim.engine, sim.current_time
    money = sim._money

    def new(table, name):
        return [record for record in table.values() if record.id > cursor[name]]

    def clipped(started_at, ended_at):
        left = max(initial_time, started_at)
        right = min(now, now if ended_at is None else ended_at)
        return (left, right) if right >= left else None

    records = []
    for quote in new(engine.quotes, "quotes"):
        intent = engine.intents[quote.intent_id]
        records.append({
            "type": "search", "at_seconds": quote.at, "quote_id": quote.id, "platform_id": quote.platform_id,
            "rider_id": quote.rider_id, "session_id": intent.id, "session_started_at": intent.created_at,
            "location": intent.origin, "destination": intent.destination,
            "drivers_available": quote.drivers_available, "eta_seconds": quote.eta_seconds,
            "distance_km": quote.distance_km, "duration_seconds": quote.duration_seconds,
            "price": money(quote.fare.rider_payment_minor),
        })
    for intent in new(engine.intents, "intents"):
        records.append({"type": "rider_session_started", "at_seconds": intent.created_at,
                        "session_id": intent.id, "rider_id": intent.rider_id})
        if intent.ended_at is not None:
            records.append({"type": "trip_ended", "at_seconds": intent.ended_at, "session_id": intent.id,
                            "rider_id": intent.rider_id, "outcome": intent.outcome, "reason": intent.reason,
                            "converted": intent.converted_at is not None})
    records.extend(dict(decision) for decision in sim.decisions[cursor["decisions"]:])
    for offer in new(engine.offers, "offers"):
        records.append({
            "type": "offer_created", "at_seconds": offer.created_at, "offer_id": offer.id,
            "order_id": offer.order_id, "platform_id": offer.platform_id, "driver_id": offer.driver_id,
            "price": money(offer.payout.driver_payout_minor), "eta_seconds": offer.eta_seconds,
            "expires_at": offer.expires_at,
        })
        if offer.state != "pending":
            records.append({"type": "offer_resolved", "at_seconds": offer.resolved_at, "offer_id": offer.id,
                            "order_id": offer.order_id, "state": offer.state, "reason": offer.reason})
    for shift in engine.shifts.values():
        span = clipped(shift.started_at, shift.ended_at)
        if span is not None:
            records.append({
                "type": "driver_online", "driver_id": shift.driver_id, "shift_id": shift.id,
                "start_seconds": span[0], "end_seconds": span[1],
                "session_started_at": shift.started_at, "session_ended_at": shift.ended_at,
            })
    for service in engine.services.values():
        span = clipped(service.started_at, service.ended_at)
        if span is not None:
            records.append({
                "type": "driver_active", "driver_id": service.driver_id, "order_id": service.order_id,
                "platform_id": service.platform_id, "start_seconds": span[0], "end_seconds": span[1],
                "service_started_at": service.started_at, "service_ended_at": service.ended_at,
                "end_reason": service.end_reason,
            })
    for order in new(engine.orders, "orders"):
        if order.state == "completed":
            records.append({
                "type": "order_completed", "at_seconds": order.timeline["completed"],
                "order_id": order.id, "platform_id": order.platform_id,
                "driver_id": order.assignment.driver_id, "rider_id": order.rider_id,
                "distance_km": engine.quotes[order.quote_id].distance_km,
                "fare": money(order.fare.rider_payment_minor),
            })
        elif order.state == "canceled":
            records.append({
                "type": "order_canceled", "at_seconds": order.timeline["canceled"],
                "order_id": order.id, "platform_id": order.platform_id, "rider_id": order.rider_id,
                "driver_id": order.assignment.driver_id if order.assignment else None,
                **order.cancellation,
            })
    for settlement in new(engine.settlements, "settlements"):
        records.append({
            "type": "settlement", "at_seconds": settlement.at, "settlement_id": settlement.id,
            "order_id": settlement.order_id, "platform_id": settlement.platform_id,
            "rider_id": settlement.rider_id, "driver_id": settlement.driver_id, "reason": settlement.reason,
            "rider_payment": money(settlement.rider_payment_minor),
            "driver_payout": money(settlement.driver_payout_minor),
            "platform_contribution": money(settlement.platform_contribution_minor),
        })
    return records


@lru_cache(maxsize=1)
def _plotly_bundle():
    return get_plotlyjs()


def dashboard_observations(records):
    """Compact observations for exact time filtering in the offline dashboard.

    Session and offer outcomes use the saved run's observation horizon, just
    like the exported interval series. Filtering selects their start cohorts.
    Driver spans remain continuous so a selection can split them exactly.
    """
    decisions = {r["session_id"]: r for r in records if r["type"] == "rider_decision"}
    resolutions = {r["offer_id"]: r["state"] for r in records if r["type"] == "offer_resolved"}
    observations = []
    for record in records:
        kind = record["type"]
        if kind in ("driver_online", "driver_active"):
            observations.append({
                "start_seconds": record["start_seconds"], "end_seconds": record["end_seconds"],
                "metric": "online_driver_seconds" if kind == "driver_online" else "active_driver_seconds",
            })
            continue
        if kind == "search":
            counts = {"searches": 1, "covered_searches": int(record["drivers_available"])}
        elif kind == "order_completed":
            counts = {"completed_orders": 1}
        elif kind == "rider_session_started":
            decision = decisions.get(record["session_id"])
            outcome = ("undecided_sessions" if decision is None else
                       "converted_sessions" if decision["ordered"] else
                       "unavailable_sessions" if decision["eta_seconds"] is None else "declined_sessions")
            counts = {"rider_sessions": 1, outcome: 1}
        elif kind == "offer_created":
            counts = {"offers": 1, OFFER_OUTCOME_KEYS[resolutions.get(record["offer_id"], "pending")]: 1}
        else:
            continue
        observations.append({"at_seconds": record["at_seconds"], "counts": counts})
    return observations


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
        "observations": dashboard_observations(records),
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
    dashboard_script = Path(__file__).with_name("report_dashboard.js").read_text(encoding="utf-8")
    html = template.replace("__PLOTLY_BUNDLE__", _plotly_bundle()).replace(
        "__DASHBOARD_SCRIPT__", dashboard_script
    ).replace("__REPORT_DATA__", data)
    report_path = directory / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
