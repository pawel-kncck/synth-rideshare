"""Offline metric reconstruction, aggregation and summary from a single raw run log.

The default output's `summary` (and `summary.settlements` in particular) is
global: totals across every platform, with bonus already folded into
`driver_payout_minor` rather than broken out separately. Per-platform money,
cancellations by party, driver km splits, ETA-drift cancellations and a
platform funnel are opt-in through `--platform-detail` (see
platform_funnel, money_by_platform, cancellations_by_party, driver_distance
and eta_drift below), not reconstructed by hand from the snapshot.
"""

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
    return {name: {record["id"]: record for record in snapshot["engine"].get(name, [])}
            for name in ("shifts", "intents", "quotes", "orders", "offers", "services", "settlements", "transfers")}


def _accounts(snapshot, role):
    """{id: account} for one role from a boundary snapshot's engine.accounts (absent in pre-phase-3 logs)."""
    return {a["id"]: a for a in snapshot["engine"].get("accounts", []) if a["role"] == role}


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


def aggregate_market_share(completed, platforms, start_seconds, end_seconds, period_days):
    """Completion share in periods relative to run start, with a partial last row.

    Use completion time, not quote/order time or installation counts. Periods
    are half-open; only the last includes its right boundary. Empty markets
    have undefined shares, and platforms with no rides remain in every row.
    ``completed`` must contain only transitions observed during this run.
    """
    if isinstance(period_days, bool) or not isinstance(period_days, int) or period_days <= 0:
        raise ValueError("market_share_days must be a positive integer")
    if not all(math.isfinite(t) for t in (start_seconds, end_seconds)) or end_seconds < start_seconds:
        raise ValueError("Invalid market-share time boundaries")
    width = period_days * 86400
    rows = [{"start_seconds": start_seconds + index * width,
             "end_seconds": min(end_seconds, start_seconds + (index + 1) * width),
             "platform_completions": {p: 0 for p in platforms}}
            for index in range(max(1, math.ceil((end_seconds - start_seconds) / width)))]
    for order in completed:
        at = order["timeline"]["completed"]
        if start_seconds <= at <= end_seconds:
            index = min(len(rows) - 1, int((at - start_seconds) // width))
            rows[index]["platform_completions"][order["platform_id"]] += 1
    for row in rows:
        total = sum(row["platform_completions"].values())
        row["completed_orders"] = total
        row["platform_completion_share_pct"] = {
            p: 100 * count / total if total else None for p, count in row["platform_completions"].items()}
    return rows


# ----------------------------------------------------------------------
# Phase 1: opt-in per-platform, per-window and per-check metrics. Every
# function below is additive: none is called unless a caller asks for it,
# so calculate_metrics(path) with no extra arguments is unaffected. Each
# reuses the "new in this run" / _new_terminal cohort rules above so a
# continuation and a fresh run agree; see plans/architecture/experiment-
# runner.md for the shared definitions (window boundaries, leg-start
# attribution, first-choice, installed base, ETA drift).
# ----------------------------------------------------------------------

def _settlement_row(orders, settlements):
    """One money row: gross/discount, base/bonus and the settlement's own three fields, summed.

    Only a completed_ride settlement's own rider_payment_minor/
    driver_payout_minor are themselves derived from the order's frozen quote
    fare and the accepted offer's frozen payout terms
    (marketplace_engine._drop_off calls _settle with order.fare.
    rider_payment_minor and order.assignment.payout.driver_payout_minor), so
    only there does rider_gross_minor/rider_discount_minor decompose those
    same frozen fare terms -- it is exactly what the rider paid, not an
    estimate of it. Any other reason (today: cancellation_fee) settles an
    amount the frozen fare does not describe at all: a canceled order's fare
    was never charged. That row instead reports what actually moved:
    rider_gross_minor is the settlement's own rider_payment_minor (discount
    0), the same fallback an order with no assignment at all always used.

    driver_base_minor/driver_bonus_minor (phase 3) split the settlement's OWN
    driver_payout_minor using its own bonus_minor field, not a join against
    the assignment: bonus = s.get("bonus_minor", <the accepted offer's
    bonus_minor> if this is a completed_ride settlement with an assignment,
    else 0). The fallback exists only for a settlement dict from a log
    written before Settlement.bonus_minor existed (no such key at all); it
    reproduces the exact same integer, because marketplace_engine._drop_off
    has always settled a completed_ride with bonus_minor equal to
    order.assignment.payout.bonus_minor, so the two sources agree bit for
    bit -- this is a source change for the SAME numbers, not a new estimate.
    driver_base_minor is then driver_payout_minor - bonus for every
    settlement, completed_ride or not (a cancellation has no bonus concept:
    bonus 0, so driver_base_minor is just driver_payout_minor there, as
    before this field existed).

    rider_payment_minor, driver_payout_minor and platform_contribution_minor
    are always the settlement's own three fields (never re-derived), so
    residual_minor (rider_payment - driver_payout - platform_contribution) is
    exactly 0 by construction of Settlement; it is reported here, not
    assumed. This split is a per-settlement identity -- driver_base_minor +
    driver_bonus_minor always equals driver_payout_minor and
    rider_gross_minor - rider_discount_minor always equals
    rider_payment_minor, for every settlement and therefore for any sum of
    rows -- and conservation() checks it.
    """
    rider_gross = rider_discount = driver_base = driver_bonus = 0
    rider_payment = driver_payout = platform_contribution = 0
    for s in settlements:
        order = orders[s["order_id"]]
        assignment = order["assignment"]
        completed_with_assignment = s["reason"] == "completed_ride" and assignment is not None
        if completed_with_assignment:
            rider_gross += order["fare"]["gross_minor"]
            rider_discount += order["fare"]["discount_minor"]
        else:
            rider_gross += s["rider_payment_minor"]
        bonus = s.get("bonus_minor", assignment["payout"]["bonus_minor"] if completed_with_assignment else 0)
        driver_bonus += bonus
        driver_base += s["driver_payout_minor"] - bonus
        rider_payment += s["rider_payment_minor"]
        driver_payout += s["driver_payout_minor"]
        platform_contribution += s["platform_contribution_minor"]
    return {"count": len(settlements), "rider_gross_minor": rider_gross, "rider_discount_minor": rider_discount,
            "rider_payment_minor": rider_payment, "driver_base_minor": driver_base, "driver_bonus_minor": driver_bonus,
            "driver_payout_minor": driver_payout, "platform_contribution_minor": platform_contribution,
            "residual_minor": rider_payment - driver_payout - platform_contribution}


def _transfer_rows(transfers):
    """count/by_reason/party_delta_minor for one transfer list -- the phase-3 half of _settlement_rows.

    by_reason[r]["platform_delta_minor"] sums -amount_minor over that
    reason's platform-counterparty transfers only (the debit posted to the
    funding platform); an external transfer has no platform side and
    contributes nothing to any platform_delta_minor. party_delta_minor sums
    amount_minor over every EXTERNAL transfer in the list: a platform
    counterparty's party deltas net to zero by construction (person
    +amount, platform -amount), so only the external total survives the sum
    -- 0 whenever the list holds no external transfer.
    """
    reasons = sorted({t["reason"] for t in transfers})
    by_reason = {}
    for reason in reasons:
        rows = [t for t in transfers if t["reason"] == reason]
        by_reason[reason] = {"count": len(rows), "amount_minor": sum(t["amount_minor"] for t in rows),
                             "platform_delta_minor": -sum(t["amount_minor"] for t in rows
                                                          if t["counterparty"] == "platform")}
    return {"count": len(transfers), "by_reason": by_reason,
            "party_delta_minor": sum(t["amount_minor"] for t in transfers if t["counterparty"] == "external")}


def _settlement_rows(orders, settlements, transfers=()):
    """settlement_breakdown's shape (by_reason/totals/transfers) for one settlement/transfer cohort.

    transfers defaults to () so every pre-phase-3 caller (and any caller that
    only has settlements to report) keeps the exact placeholder shape phase 1
    published: count 0, by_reason {}, party_delta_minor 0.
    """
    reasons = sorted({s["reason"] for s in settlements})
    by_reason = {reason: _settlement_row(orders, [s for s in settlements if s["reason"] == reason]) for reason in reasons}
    return {"by_reason": by_reason, "totals": _settlement_row(orders, settlements),
            "transfers": _transfer_rows(transfers)}


def _new_settlements(before, after):
    return [s for identity, s in after["settlements"].items() if identity not in before["settlements"]]


def _new_transfers(before, after):
    return [t for identity, t in after["transfers"].items() if identity not in before["transfers"]]


def settlement_breakdown(initial, final):
    """Settlement money split into base/bonus/discount by reason, plus the phase-3 transfers section.

    Uses settlements AND transfers new in this run (_new_settlements/
    _new_transfers, the same "new in this run" cohort rule as everywhere
    else). This is the global row: every Transfer with a platform
    counterparty debits that platform, but an external transfer (lease,
    operating cost) has none, so a per-platform sum of transfers
    (money_by_platform) will not reproduce this row's transfers section
    whenever an external transfer exists in the run -- documented there, not
    a bug here (there is no platform to attribute an external transfer to).
    """
    before, after = _tables(initial), _tables(final)
    return _settlement_rows(after["orders"], _new_settlements(before, after), _new_transfers(before, after))


def money_by_platform(initial, final):
    """settlement_breakdown's structure, keyed by platform_id.

    Every platform in the final snapshot is present, even with zero
    settlements in this run, matching aggregate_market_share's convention
    that platforms with no rides remain in every row. Summing any of the
    three settlement totals over every platform reproduces the same global
    sum computed directly from all new settlements (conservation() checks
    this: platform_money_matches_totals). Transfers bucket by their own
    platform_id the same way, but an external transfer's platform_id is
    always None, so it lands in no platform's bucket at all -- summing every
    platform's transfers section here therefore does NOT reproduce
    settlement_breakdown's global transfers section when the run has any
    external transfer; that gap is the external total itself
    (conservation()['transfer_party_delta_minor']), not an inconsistency to
    fix by inventing a platform attribution.
    """
    before, after = _tables(initial), _tables(final)
    by_platform = {p["id"]: [] for p in final["engine"]["platforms"]}
    transfers_by_platform = {p["id"]: [] for p in final["engine"]["platforms"]}
    for s in _new_settlements(before, after):
        by_platform.setdefault(s["platform_id"], []).append(s)
    for t in _new_transfers(before, after):
        if t["platform_id"] is not None:
            transfers_by_platform.setdefault(t["platform_id"], []).append(t)
    return {platform_id: _settlement_rows(after["orders"], rows, transfers_by_platform.get(platform_id, ()))
            for platform_id, rows in by_platform.items()}


def _blank_funnel_counts():
    return {"quotes": 0, "quotes_with_supply": 0, "orders": 0, "offers": 0,
            "accepted_offers": 0, "rejected_offers": 0, "expired_offers": 0, "canceled_offers": 0,
            "failed_offers": 0, "pending_offers": 0, "completed": 0, "canceled": 0}


def _funnel_rows(platforms, quotes, orders, offers, completed, canceled):
    """Flow counts per platform from already-cohorted record lists, plus their ratios.

    Each list is pre-filtered by its caller (the whole run, or one window);
    offers and their disposition are counted together by the offer's own
    creation, so a later acceptance is attributed back to the offer's own
    row and offer_acceptance_pct never drifts past 100% the way it would if
    disposition followed its own resolution time instead.
    """
    rows = {pid: _blank_funnel_counts() for pid in platforms}
    for quote in quotes:
        row = rows.setdefault(quote["platform_id"], _blank_funnel_counts())
        row["quotes"] += 1
        row["quotes_with_supply"] += int(quote["eta_seconds"] is not None)
    for order in orders:
        rows.setdefault(order["platform_id"], _blank_funnel_counts())["orders"] += 1
    for offer in offers:
        row = rows.setdefault(offer["platform_id"], _blank_funnel_counts())
        row["offers"] += 1
        row[OFFER_OUTCOME_KEYS[offer["state"]]] += 1
    for order in completed:
        rows.setdefault(order["platform_id"], _blank_funnel_counts())["completed"] += 1
    for order in canceled:
        rows.setdefault(order["platform_id"], _blank_funnel_counts())["canceled"] += 1
    for row in rows.values():
        row["offer_acceptance_pct"] = 100 * row["accepted_offers"] / row["offers"] if row["offers"] else None
        row["order_completion_pct"] = 100 * row["completed"] / row["orders"] if row["orders"] else None
    return rows


def platform_funnel(initial, final):
    """Per-platform funnel from quotes through completion or cancellation.

    Quotes, orders and offers use the "new in this run" cohort; completed
    and canceled use _new_terminal, so an order carried in from a previous
    run segment still counts its transition here. active_at_end counts
    every order of this platform not yet terminal at the final boundary,
    including carry-in orders -- unlike the flow counts above it is a
    snapshot, not an event count, so it cannot be reconstructed per window
    (window_rows omits it). Ratios are null when their denominator is zero.
    """
    before, after = _tables(initial), _tables(final)
    platforms = [p["id"] for p in final["engine"]["platforms"]]
    new_quotes = [q for identity, q in after["quotes"].items() if identity not in before["quotes"]]
    new_orders = [o for identity, o in after["orders"].items() if identity not in before["orders"]]
    new_offers = [of for identity, of in after["offers"].items() if identity not in before["offers"]]
    rows = _funnel_rows(platforms, new_quotes, new_orders, new_offers,
                        _new_terminal(before, after, "completed"), _new_terminal(before, after, "canceled"))
    active = {pid: 0 for pid in platforms}
    for order in after["orders"].values():
        if order["state"] not in ("completed", "canceled"):
            active[order["platform_id"]] = active.get(order["platform_id"], 0) + 1
    for pid, row in rows.items():
        row["active_at_end"] = active.get(pid, 0)
    return rows


def cancellations_by_party(initial, final):
    """Canceled orders in this run, by platform and the party who canceled, with reasons.

    Uses _new_terminal(..., "canceled"), the same cohort as the global
    cancelled_orders count, so a carried-in order that cancels during this
    run counts here. by_reason further splits each platform's cancellations
    by the cancellation policy's stated reason string.
    """
    before, after = _tables(initial), _tables(final)
    rows = {p["id"]: {"rider": 0, "driver": 0, "platform": 0, "by_reason": {}} for p in final["engine"]["platforms"]}
    for order in _new_terminal(before, after, "canceled"):
        row = rows.setdefault(order["platform_id"], {"rider": 0, "driver": 0, "platform": 0, "by_reason": {}})
        by, reason = order["cancellation"]["by"], order["cancellation"]["reason"]
        row[by] += 1
        row["by_reason"][reason] = row["by_reason"].get(reason, 0) + 1
    return rows


def driver_distance(initial, final):
    """Empty (pickup) and loaded (transport) kilometres per driver, from service legs.

    A leg is attributed to the run segment where it first appears -- cohorted
    by identity (service_id, its index in that service's own legs list), the
    same "new in this run" rule collect_metric_records and every other
    function in this module use, not by testing its started_at against the
    segment's clock bounds. A service's legs list is append-only
    (marketplace_engine.py never pops or reorders it) and a leg's started_at
    is always the clock at the instant it was appended, so a snapshot
    boundary can land exactly on a leg's own started_at (Snapshot() is an
    event-boundary checkpoint, so this is an ordinary case, not a contrived
    one): a filter inclusive on both ends would then count that one leg
    twice, once in the segment that produced it and again in the next,
    restored one. Cohorting by identity instead means a leg is counted
    exactly once no matter where a snapshot falls, which is what makes a
    continuous run and a restored continuation agree byte-for-byte on driver
    distance (see plans/architecture/experiment-runner.md). Reposition legs
    do not exist yet (plan phase 6, marketplace-engine.md) and would land in
    other_km; boarding legs have zero length and add nothing to any bucket.
    loaded_km equals summary.completed_distance_km whenever every transport
    leg in the run belongs to a completed order (true exactly when
    orders_active_at_end is 0). loaded_share_pct is null for a driver (or the
    fleet) with no logged distance at all.
    """
    end = final["scheduler"]["clock_seconds"]
    before_services = {s["id"]: s for s in initial["engine"]["services"]}
    by_driver = {d["id"]: {"empty": [], "loaded": [], "other": []} for d in final["engine"]["drivers"]}
    for service in final["engine"]["services"]:
        bucket = by_driver.setdefault(service["driver_id"], {"empty": [], "loaded": [], "other": []})
        already = len(before_services[service["id"]]["legs"]) if service["id"] in before_services else 0
        for index, leg in enumerate(service["legs"]):
            if index < already or leg["started_at"] > end:
                continue
            distance = math.dist(leg["origin"], leg["destination"])
            key = "empty" if leg["kind"] == "pickup" else "loaded" if leg["kind"] == "transport" else "other"
            bucket[key].append(distance)

    def _row(bucket):
        empty_km, loaded_km, other_km = math.fsum(bucket["empty"]), math.fsum(bucket["loaded"]), math.fsum(bucket["other"])
        total_km = empty_km + loaded_km + other_km
        return {"empty_km": empty_km, "loaded_km": loaded_km, "other_km": other_km, "total_km": total_km,
                "loaded_share_pct": 100 * loaded_km / total_km if total_km else None}

    result = {driver_id: _row(bucket) for driver_id, bucket in by_driver.items()}
    result["totals"] = _row({"empty": [v for b in by_driver.values() for v in b["empty"]],
                             "loaded": [v for b in by_driver.values() for v in b["loaded"]],
                             "other": [v for b in by_driver.values() for v in b["other"]]})
    return result


def _blank_eta_totals():
    return {"canceled": 0, "drift_defined": 0, "drift_positive": 0, "drift_seconds_total": 0.0,
            "drift_seconds_min": None, "drift_seconds_max": None, "drift_seconds_avg": None}


def _blank_eta_row():
    return {**_blank_eta_totals(), "revisions": 0, "by_party": {}}


def eta_drift(initial, final):
    """ETA-drift cancellations: how long past the first promised pickup a cancellation landed.

    promised_arrival is the quote-time prediction: eta_predictions[0] (always
    present and always the quote's own estimate, appended in place_order) is
    p0, and promised_arrival = p0["at"] + p0["eta_seconds"]. An ETA-drift
    cancellation is one whose drift (the cancellation instant minus
    promised_arrival) is positive: the platform ran later than first quoted.
    drift stays undefined (excluded from the drift_* statistics, but still
    counted in "canceled") when the platform had no supply at quote time
    (eta_seconds is None) or, defensively, eta_predictions is empty.
    revisions counts eta_predictions entries appended by revise_pickup_eta
    (source == "revision") that are new in this run, for every order of the
    platform, whether or not it was later canceled; it is reported only by
    platform, not split by party. Cohorted the same way driver_distance
    cohorts legs: eta_predictions is append-only, so comparing each order's
    own list length in `initial` against `final` and counting only the
    entries beyond what was already there identifies exactly the entries new
    to this run, without testing each entry's own timestamp against the
    run's clock bounds -- a boundary inclusive on both ends would double
    count a revision recorded in the same instant a snapshot was taken, the
    same way it would double count a leg in driver_distance.
    """
    before, after = _tables(initial), _tables(final)
    end = final["scheduler"]["clock_seconds"]
    rows = {p["id"]: _blank_eta_row() for p in final["engine"]["platforms"]}
    for identity, order in after["orders"].items():
        row = rows.setdefault(order["platform_id"], _blank_eta_row())
        already = len(before["orders"][identity]["eta_predictions"]) if identity in before["orders"] else 0
        row["revisions"] += sum(1 for p in order["eta_predictions"][already:]
                                if p["source"] == "revision" and p["at"] <= end)
    for order in _new_terminal(before, after, "canceled"):
        row = rows.setdefault(order["platform_id"], _blank_eta_row())
        party = order["cancellation"]["by"]
        party_row = row["by_party"].setdefault(party, _blank_eta_totals())
        row["canceled"] += 1
        party_row["canceled"] += 1
        predictions = order["eta_predictions"]
        p0 = predictions[0] if predictions else None
        if p0 is not None and p0["eta_seconds"] is not None:
            drift = order["timeline"]["canceled"] - (p0["at"] + p0["eta_seconds"])
            for target in (row, party_row):
                target["drift_defined"] += 1
                target["drift_positive"] += int(drift > 0)
                target["drift_seconds_total"] += drift
                target["drift_seconds_min"] = drift if target["drift_seconds_min"] is None else min(target["drift_seconds_min"], drift)
                target["drift_seconds_max"] = drift if target["drift_seconds_max"] is None else max(target["drift_seconds_max"], drift)
    for row in rows.values():
        row["drift_seconds_avg"] = row["drift_seconds_total"] / row["drift_defined"] if row["drift_defined"] else None
        for party_row in row["by_party"].values():
            party_row["drift_seconds_avg"] = (party_row["drift_seconds_total"] / party_row["drift_defined"]
                                              if party_row["drift_defined"] else None)
    return rows


def _window_bounds(start, end, boundary_hours):
    if (boundary_hours is None or isinstance(boundary_hours, (str, bytes)) or not hasattr(boundary_hours, "__iter__")):
        raise ValueError("window boundaries must be a nonempty sequence of hour offsets")
    boundary_hours = list(boundary_hours)
    if not boundary_hours or any(isinstance(h, bool) or not isinstance(h, (int, float))
                                 or not math.isfinite(h) or h <= 0 for h in boundary_hours):
        raise ValueError("window boundaries must be finite positive numbers")
    if any(a >= b for a, b in zip(boundary_hours, boundary_hours[1:])):
        raise ValueError("window boundaries must be strictly increasing")
    edges = [min(end, start + hours * 3600) for hours in boundary_hours]
    return [start] + edges + [end]


def _window_detail(orders, platforms, quotes, orders_list, offers, completed, canceled, settlements, transfers,
                   left, right, *, inclusive_right):
    def _select(records, key):
        return [r for r in records if left <= key(r) <= right] if inclusive_right \
            else [r for r in records if left <= key(r) < right]

    funnel = _funnel_rows(platforms, _select(quotes, lambda q: q["at"]), _select(orders_list, lambda o: o["created_at"]),
                          _select(offers, lambda of: of["created_at"]),
                          _select(completed, lambda o: o["timeline"]["completed"]),
                          _select(canceled, lambda o: o["timeline"]["canceled"]))
    by_platform = {pid: [] for pid in platforms}
    for s in _select(settlements, lambda s: s["at"]):
        by_platform.setdefault(s["platform_id"], []).append(s)
    transfers_by_platform = {pid: [] for pid in platforms}
    for t in _select(transfers, lambda t: t["at"]):
        if t["platform_id"] is not None:
            transfers_by_platform.setdefault(t["platform_id"], []).append(t)
    return {pid: {"money": _settlement_rows(orders, rows, transfers_by_platform.get(pid, ())), "funnel": funnel[pid]}
            for pid, rows in by_platform.items()}


def window_rows(header, initial, final, boundary_hours):
    """Consecutive windows of the run split at hour offsets from its own start, plus running totals.

    boundary_hours are finite, strictly increasing hour offsets from this
    run's own start (not calendar hours); N boundaries make N+1 windows, all
    half-open except the last, which includes the run's end, matching
    aggregate_intervals. A boundary past the run's end clips to the end, so
    asking for a wider window than a short run safely reports an empty
    trailing window instead of failing. Each row's "platforms" holds that
    window's own money (settlement_breakdown's shape, transfers included)
    and funnel per platform; settlements and transfers bucket by their own
    at, completions by timeline.completed and cancellations by
    timeline.canceled (their own event time), while quotes/orders/offers --
    and offer dispositions -- bucket by their own creation time (see
    _funnel_rows). "cumulative" is the same shape computed from the run's
    start through this window's right edge, so the last window's cumulative
    equals the whole run's totals (window boundaries are hours after start,
    see calculate_metrics and the README).
    """
    before, after = _tables(initial), _tables(final)
    start, end = initial["scheduler"]["clock_seconds"], final["scheduler"]["clock_seconds"]
    bounds = _window_bounds(start, end, boundary_hours)
    platforms = [p["id"] for p in final["engine"]["platforms"]]
    quotes = [q for identity, q in after["quotes"].items() if identity not in before["quotes"]]
    orders_list = [o for identity, o in after["orders"].items() if identity not in before["orders"]]
    offers = [of for identity, of in after["offers"].items() if identity not in before["offers"]]
    completed = _new_terminal(before, after, "completed")
    canceled = _new_terminal(before, after, "canceled")
    settlements = _new_settlements(before, after)
    transfers = _new_transfers(before, after)
    rows = []
    for index in range(len(bounds) - 1):
        left, right = bounds[index], bounds[index + 1]
        last = index == len(bounds) - 2
        rows.append({
            "start_seconds": left, "end_seconds": right,
            "start_hours": (left - start) / 3600, "end_hours": (right - start) / 3600,
            "clock_start": clock_label(header["start_hour"] * 3600 + left),
            "clock_end": clock_label(header["start_hour"] * 3600 + right),
            "platforms": _window_detail(after["orders"], platforms, quotes, orders_list, offers, completed,
                                        canceled, settlements, transfers, left, right, inclusive_right=last),
            "cumulative": _window_detail(after["orders"], platforms, quotes, orders_list, offers, completed,
                                         canceled, settlements, transfers, start, right, inclusive_right=True),
        })
    return rows


def first_choice_periods(header, initial, final, period_days):
    """Daily (or period_days-wide) first-choice query share against each period's installed base.

    "First choice" is the platform of a rider's earliest quote (ordered by
    (at, id)) among intents created in this run; drivers never query, so
    this is rider-side only. An intent with no quote at all (no supply
    observation was even attempted) contributes to no period. Each quote
    lands in exactly one period -- the half-open interval containing its
    `at`, except the last period, which also includes the run's own end --
    the same single-assignment bucket rule aggregate_market_share uses for
    completions, so a quote landing exactly on a period boundary is never
    counted in both the period it closes and the one it opens; queries and
    sum(first_choice_counts.values()) always agree. "Installed
    base" at a period's start is the initial snapshot's engine.riders[*].apps,
    replayed forward with every policies.observations app_installed record
    (role == "rider") at or before that instant -- app installs are
    cumulative and idempotent per rider (a set), so replaying an install
    from before this run segment began is harmless. A rider can multi-home,
    so installed_share_pct is a share of total installs and can sum above
    100% within a period; that is expected, not an error (see
    plans/architecture/experiment-runner.md).
    """
    if isinstance(period_days, bool) or not isinstance(period_days, int) or period_days <= 0:
        raise ValueError("first_choice_days must be a positive integer")
    before, after = _tables(initial), _tables(final)
    start, end = initial["scheduler"]["clock_seconds"], final["scheduler"]["clock_seconds"]
    width = period_days * 86400
    platforms = [p["id"] for p in final["engine"]["platforms"]]
    apps = {r["id"]: set(r["apps"]) for r in initial["engine"]["riders"]}
    installs = sorted((o for o in final["policies"]["observations"]
                       if o["type"] == "app_installed" and o["role"] == "rider"), key=lambda o: o["at_seconds"])
    first_choice = []
    for identity, intent in after["intents"].items():
        if identity in before["intents"] or not intent["quote_ids"]:
            continue
        earliest = min(intent["quote_ids"], key=lambda q: (after["quotes"][q]["at"], q))
        quote = after["quotes"][earliest]
        first_choice.append((quote["at"], quote["platform_id"]))
    rows, cursor = [], 0
    for index in range(max(1, math.ceil((end - start) / width))):
        left = start + index * width
        right = min(end, start + (index + 1) * width)
        while cursor < len(installs) and installs[cursor]["at_seconds"] <= left:
            apps.setdefault(installs[cursor]["person_id"], set()).add(installs[cursor]["platform_id"])
            cursor += 1
        installed_riders = {p: sum(p in a for a in apps.values()) for p in platforms}
        total_installed = sum(installed_riders.values())
        rows.append({
            "start_seconds": left, "end_seconds": right,
            "clock_start": clock_label(header["start_hour"] * 3600 + left),
            "clock_end": clock_label(header["start_hour"] * 3600 + right),
            "queries": 0, "first_choice_counts": {p: 0 for p in platforms},
            "first_choice_share_pct": {p: None for p in platforms},
            "installed_riders": installed_riders,
            "installed_share_pct": {p: 100 * n / total_installed if total_installed else None
                                    for p, n in installed_riders.items()},
        })
    # Single-assignment bucketing (aggregate_market_share's rule): each quote
    # lands in exactly one row, so a boundary instant is never double counted.
    for at, platform_id in first_choice:
        if start <= at <= end:
            index = min(len(rows) - 1, int((at - start) // width))
            rows[index]["first_choice_counts"][platform_id] += 1
            rows[index]["queries"] += 1
    for row in rows:
        queries = row["queries"]
        row["first_choice_share_pct"] = {p: 100 * n / queries if queries else None
                                         for p, n in row["first_choice_counts"].items()}
    return rows


def _driver_ledger_row(driver_id, settlements, transfers, before_accounts, after_accounts):
    before_account, after_account = before_accounts.get(driver_id), after_accounts.get(driver_id)
    return {
        "payout_minor": sum(s["driver_payout_minor"] for s in settlements),
        "transfers_minor": sum(t["amount_minor"] for t in transfers),
        "balance_start_minor": before_account["balance_minor"] if before_account else None,
        "balance_end_minor": after_account["balance_minor"] if after_account else None,
        "transfers_by_reason": {reason: {"count": row["count"], "amount_minor": row["amount_minor"]}
                                for reason, row in _transfer_rows(transfers)["by_reason"].items()},
    }


def ledger_section(initial, final, *, per_driver=False):
    """Cash-flow reconciliation from account balances plus the new-record cohorts (the ``--ledger`` section).

    Each platform's cash_start_minor/cash_end_minor read that platform's OWN
    account.balance_minor from the initial/final snapshot (None when
    starting_cash_minor is None -- untracked, though its flows still post
    and are reported); ride_contribution_minor sums platform_contribution_minor
    over settlements new in this run, and transfers_minor sums the debit
    (-amount_minor) of this run's new platform-counterparty transfers funded
    by that platform. These are two INDEPENDENTLY derived numbers -- one
    from the boundary balances, one from replaying new records -- so
    cash_end_minor - cash_start_minor == net_minor
    (ride_contribution_minor + transfers_minor) is a real offline
    cross-check, the same conservation property marketplace_engine.restore's
    _rebuild_accounts proves inside the engine, not a tautology.

    The top-level "transfers" section is global (settlement_breakdown's
    transfers shape plus an external_minor per reason): external_minor at
    the top level and party_delta_minor are the same number by construction
    (a platform-counterparty transfer's party deltas net to zero, so only
    the external total survives either sum) -- both names are kept because
    each reads naturally in a different sentence.

    unattributed_driver_payout_minor names settlements whose
    driver_payout_minor has no driver_id to post to at all (reachable today:
    a rider cancels an unassigned order under
    driver_cancellation_compensation_minor > 0). It is 0 in every run where
    that combination never occurs, and is reported rather than silenced by
    an engine-side rejection, which would change legality for scenarios that
    already authored that parameter. drivers is filled only when per_driver
    is requested (it is one row per driver, the "optional" half of --ledger).
    """
    before, after = _tables(initial), _tables(final)
    new_settlements, new_transfers = _new_settlements(before, after), _new_transfers(before, after)
    platform_accounts_before, platform_accounts_after = _accounts(initial, "platform"), _accounts(final, "platform")
    platforms = {}
    for p in final["engine"]["platforms"]:
        pid = p["id"]
        own_settlements = [s for s in new_settlements if s["platform_id"] == pid]
        own_transfers = [t for t in new_transfers if t["platform_id"] == pid]  # always counterparty == platform
        contribution = sum(s["platform_contribution_minor"] for s in own_settlements)
        transfers_minor = -sum(t["amount_minor"] for t in own_transfers)
        before_account, after_account = platform_accounts_before.get(pid), platform_accounts_after.get(pid)
        platforms[pid] = {
            "starting_cash_minor": p.get("starting_cash_minor"),  # .get: absent in a pre-phase-3 log
            "cash_start_minor": before_account["balance_minor"] if before_account else None,
            "cash_end_minor": after_account["balance_minor"] if after_account else None,
            "ride_contribution_minor": contribution, "transfers_minor": transfers_minor,
            "net_minor": contribution + transfers_minor,
            "transfers_by_reason": _transfer_rows(own_transfers)["by_reason"],
        }
    global_rows = _transfer_rows(new_transfers)
    by_reason = {reason: {**row, "external_minor": sum(t["amount_minor"] for t in new_transfers
                                                        if t["reason"] == reason and t["counterparty"] == "external")}
                for reason, row in global_rows["by_reason"].items()}
    result = {
        "platforms": platforms,
        "transfers": {"count": global_rows["count"], "external_minor": global_rows["party_delta_minor"],
                      "party_delta_minor": global_rows["party_delta_minor"], "by_reason": by_reason},
        "unattributed_driver_payout_minor": sum(s["driver_payout_minor"] for s in new_settlements
                                                if s["driver_id"] is None),
    }
    if per_driver:
        driver_accounts_before, driver_accounts_after = _accounts(initial, "driver"), _accounts(final, "driver")
        result["drivers"] = {
            d["id"]: _driver_ledger_row(
                d["id"], [s for s in new_settlements if s["driver_id"] == d["id"]],
                [t for t in new_transfers if t["role"] == "driver" and t["person_id"] == d["id"]],
                driver_accounts_before, driver_accounts_after)
            for d in final["engine"]["drivers"]
        }
    return result


def _account_deltas_match(initial, final, new_settlements, new_transfers):
    """conservation()'s account_deltas_match_records: the offline twin of the engine's own rebuild check.

    For every (role, id) account present in both boundary snapshots, the
    settlement_minor delta between them must equal that party's own leg
    summed over settlements new in this run, and likewise transfer_minor
    against transfers new in this run -- exactly what
    marketplace_engine._post_settlement/_post_transfer_record post, replayed
    here from the raw log instead of from engine.accounts.
    """
    before_accounts = {(a["role"], a["id"]): a for a in initial["engine"].get("accounts", [])}
    after_accounts = {(a["role"], a["id"]): a for a in final["engine"].get("accounts", [])}
    settlement_leg, transfer_leg = {}, {}
    for s in new_settlements:
        settlement_leg[("platform", s["platform_id"])] = (
            settlement_leg.get(("platform", s["platform_id"]), 0) + s["platform_contribution_minor"])
        settlement_leg[("rider", s["rider_id"])] = settlement_leg.get(("rider", s["rider_id"]), 0) - s["rider_payment_minor"]
        if s["driver_id"] is not None:
            settlement_leg[("driver", s["driver_id"])] = (
                settlement_leg.get(("driver", s["driver_id"]), 0) + s["driver_payout_minor"])
    for t in new_transfers:
        key = (t["role"], t["person_id"])
        transfer_leg[key] = transfer_leg.get(key, 0) + t["amount_minor"]
        if t["counterparty"] == "platform":
            pkey = ("platform", t["platform_id"])
            transfer_leg[pkey] = transfer_leg.get(pkey, 0) - t["amount_minor"]
    for key in set(before_accounts) & set(after_accounts):
        before_account, after_account = before_accounts[key], after_accounts[key]
        if after_account["settlement_minor"] - before_account["settlement_minor"] != settlement_leg.get(key, 0):
            return False
        if after_account["transfer_minor"] - before_account["transfer_minor"] != transfer_leg.get(key, 0):
            return False
    return True


def conservation(initial, final):
    """Order and money conservation identities: the residuals every --check script starts from.

    orders_balanced is (orders active at this run's start + orders_created)
    == (completed + canceled + active_at_end); completed/canceled use
    _new_terminal so a carried-in order's transition during this run still
    counts. settlement_residual_minor sums rider_payment - driver_payout -
    platform_contribution over every settlement new in this run; it is
    exactly 0 by construction of Settlement (reported, not assumed).
    platform_money_matches_totals confirms that summing money_by_platform's
    per-platform totals reproduces the same three sums computed directly
    from every new settlement, so a bucketing bug cannot hide inside a
    per-platform split. settlement_split_reconciles checks the per-row
    identity _settlement_row's docstring promises -- base+bonus equals that
    row's own driver_payout_minor, and gross-discount equals its own
    rider_payment_minor -- on every settlement_breakdown row (each reason and
    the total), so a future change to the base/bonus/gross/discount split
    cannot silently stop reconciling with the money the settlements actually
    moved.

    Three additive (phase 3) keys: transfer_party_delta_minor sums
    amount_minor over every EXTERNAL transfer new in this run (0 per
    platform-counterparty transfer, by construction of post_transfer, so
    only external transfers can move it off zero).
    unattributed_driver_payout_minor sums driver_payout_minor over new
    settlements with no driver_id (see ledger_section); it is 0 in every run
    that never exercises a rider cancellation's driver compensation on an
    unassigned order. account_deltas_match_records is the offline twin of
    marketplace_engine.restore's own _rebuild_accounts conservation check
    (_account_deltas_match): every account's settlement_minor/transfer_minor
    delta between the boundary snapshots must equal that party's own leg
    summed over the records new in this run.
    """
    before, after = _tables(initial), _tables(final)
    new_orders = [o for identity, o in after["orders"].items() if identity not in before["orders"]]
    completed = _new_terminal(before, after, "completed")
    canceled = _new_terminal(before, after, "canceled")
    active_at_start = sum(1 for o in before["orders"].values() if o["state"] not in ("completed", "canceled"))
    active_at_end = sum(1 for o in after["orders"].values() if o["state"] not in ("completed", "canceled"))
    new_settlements = _new_settlements(before, after)
    new_transfers = _new_transfers(before, after)
    money_keys = ("rider_payment_minor", "driver_payout_minor", "platform_contribution_minor")
    global_totals = {key: sum(s[key] for s in new_settlements) for key in money_keys}
    platform_totals = {key: sum(row["totals"][key] for row in money_by_platform(initial, final).values())
                       for key in money_keys}
    breakdown = settlement_breakdown(initial, final)
    split_rows = list(breakdown["by_reason"].values()) + [breakdown["totals"]]
    return {
        "orders_created": len(new_orders), "completed": len(completed), "canceled": len(canceled),
        "active_at_end": active_at_end,
        "orders_balanced": active_at_start + len(new_orders) == len(completed) + len(canceled) + active_at_end,
        "settlement_residual_minor": sum(s["rider_payment_minor"] - s["driver_payout_minor"] - s["platform_contribution_minor"]
                                         for s in new_settlements),
        "platform_money_matches_totals": platform_totals == global_totals,
        "settlement_split_reconciles": all(
            row["driver_base_minor"] + row["driver_bonus_minor"] == row["driver_payout_minor"]
            and row["rider_gross_minor"] - row["rider_discount_minor"] == row["rider_payment_minor"]
            for row in split_rows),
        "transfer_party_delta_minor": sum(t["amount_minor"] for t in new_transfers if t["counterparty"] == "external"),
        "unattributed_driver_payout_minor": sum(s["driver_payout_minor"] for s in new_settlements
                                                if s["driver_id"] is None),
        "account_deltas_match_records": _account_deltas_match(initial, final, new_settlements, new_transfers),
    }


CHECK_STATES = ("PASS", "FAIL", "NOT-EVALUABLE")


class Checks:
    """Observable-check verdicts for one scenario script: pass, fail, or not evaluable with a reason.

    Every scenario script under scenarios/reviews/ builds one from its own
    evaluate(), records one verdict per check in DESCRIPTION.md's Observable
    Checks list, prints them with report(), and exits with its return code.
    NOT-EVALUABLE is never a failure -- it names the reason and the owning
    phase, so a script is honest about today's limits instead of fabricating
    a verdict. The line format is fixed so PR evidence stays diffable:
    "PASS  s1.1 payout ratio — 42/42 completed orders equal round_half_up((1-c)*gross)".
    """

    def __init__(self, scenario):
        self.scenario = scenario
        self._rows = []

    def verdict(self, name, passed, detail=""):
        self._rows.append((name, "PASS" if passed else "FAIL", detail))

    def not_evaluable(self, name, reason):
        self._rows.append((name, "NOT-EVALUABLE", reason))

    def report(self):
        for name, state, detail in self._rows:
            print(f"{state}  {name}" + (f" — {detail}" if detail else ""))
        return 1 if any(state == "FAIL" for _, state, _ in self._rows) else 0


def calculate_metrics(path, interval_minutes=None, *, market_share_days=None,
                      windows=None, platform_detail=False, first_choice_days=None,
                      ledger=False, ledger_drivers=False):
    """Calculate the full run summary and optional intervals from one saved log.

    Uses only the standard library and raw values in the log. No current model
    defaults, engine objects, reporting files, or other run artifacts are needed.
    The input is never modified. All timestamps are simulated seconds; exact
    settlement totals are retained in integer minor units.

    With no extra arguments the return value is exactly what it always was.
    Independent opt-in sections add offline detail without touching it:
    ``windows`` (an hour-boundary sequence, see window_rows) adds "windows"
    and "window_boundary_hours"; ``platform_detail=True`` adds
    "platform_detail" with per-platform money, funnel, cancellations, driver
    km, ETA drift, the settlement breakdown and the conservation identities;
    ``first_choice_days`` adds "first_choice_days" and "first_choice_periods";
    ``ledger=True`` (or ``ledger_drivers=True``, which implies it) adds
    "ledger" (see ledger_section) -- cash start/end per platform, ride
    contribution, transfers by reason, and, only with ledger_drivers, one
    row per driver.
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
    if market_share_days is not None:
        result["market_share_days"] = market_share_days
        result["market_share_periods"] = aggregate_market_share(
            completed, platform_completions, start, end, market_share_days)
    if windows is not None:
        result["window_boundary_hours"] = list(windows)
        result["windows"] = window_rows(header, initial, final, windows)
    if platform_detail:
        result["platform_detail"] = {
            "money": money_by_platform(initial, final),
            "funnel": platform_funnel(initial, final),
            "cancellations": cancellations_by_party(initial, final),
            "driver_distance": driver_distance(initial, final),
            "eta_drift": eta_drift(initial, final),
            "settlement_breakdown": settlement_breakdown(initial, final),
            "conservation": conservation(initial, final),
        }
    if first_choice_days is not None:
        result["first_choice_days"] = first_choice_days
        result["first_choice_periods"] = first_choice_periods(header, initial, final, first_choice_days)
    if ledger or ledger_drivers:
        result["ledger"] = ledger_section(initial, final, per_driver=ledger_drivers)
    return result


def _hours_list(text):
    """argparse type= for --windows: a comma-separated, strictly increasing list of positive hours."""
    try:
        values = [float(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid window boundaries: {text!r}; expected comma-separated hours") from None
    if not values or any(not math.isfinite(v) or v <= 0 for v in values):
        raise argparse.ArgumentTypeError("window boundaries must be finite positive numbers")
    if any(a >= b for a, b in zip(values, values[1:])):
        raise argparse.ArgumentTypeError("window boundaries must be strictly increasing")
    return values


def main():
    parser = argparse.ArgumentParser(description="Calculate metrics after a run from its structured simulation.log.")
    parser.add_argument("log", type=Path, help="Path to simulation.log (JSON Lines schema 2)")
    parser.add_argument("--interval-minutes", type=int, choices=INTERVAL_MINUTES,
                        help="Include interval metrics; omitted by default")
    parser.add_argument("--market-share-days", type=int,
                        help="Include platform completion shares in periods of this many days")
    parser.add_argument("--windows", type=_hours_list, metavar="H1,H2,...",
                        help="Include money/funnel windows split at these hour offsets from run start; omitted by default")
    parser.add_argument("--platform-detail", action="store_true",
                        help="Include per-platform money, funnel, cancellations, driver km, ETA drift and conservation")
    parser.add_argument("--first-choice-days", type=int,
                        help="Include first-choice query share against installed base in periods of this many days")
    parser.add_argument("--ledger", action="store_true",
                        help="Include per-platform cash start/end, ride contribution and transfers by reason")
    parser.add_argument("--ledger-drivers", action="store_true",
                        help="Like --ledger, plus one row per driver (implies --ledger)")
    args = parser.parse_args()
    try:
        result = calculate_metrics(args.log, args.interval_minutes, market_share_days=args.market_share_days,
                                   windows=args.windows, platform_detail=args.platform_detail,
                                   first_choice_days=args.first_choice_days,
                                   ledger=args.ledger, ledger_drivers=args.ledger_drivers)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Cannot analyze log: {error}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
