"""Deterministic OFFLINE daily-risk arithmetic; NEVER an execution gate.

No I/O, persistence, clock, broker or configuration imports. All completeness
claims belong to the caller; this module cannot verify a broker or serialize
concurrent orders. Money is returned as exact decimal strings, not float PnL.
See docs/DAILY_RISK_MODEL.md for the deliberately narrow accounting contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


_ZERO = Decimal(0)
_ONE_HUNDRED = Decimal(100)
_MAX_VALUE = Decimal("1000000000000")
_BASIS = "realized_net_plus_entry_to_stop"
_EVIDENCE = {"hypothetical", "caller_reconciled_broker_snapshot"}
_SESSION_KEYS = {
    "session_id", "account_id", "currency", "start_at", "end_at",
    "start_equity_usd", "confirmed_capital_usd",
}
_POLICY_KEYS = {
    "user_daily_loss_tolerance_usd", "daily_loss_pct", "per_trade_risk_pct",
    "aggregate_risk_pct", "existing_daily_limit_usd", "existing_trade_limit_usd",
    "existing_aggregate_limit_usd", "max_daily_entries", "max_positions",
    "minimum_cash_usd", "max_gross_exposure_pct",
}
_SNAPSHOT_KEYS = {
    "snapshot_id", "session_id", "account_id", "currency", "observed_at",
    "evidence_kind", "reconciled_complete", "ledger_complete", "positions_complete",
    "orders_complete", "fills_complete", "costs_complete", "pnl_basis",
    "realized_gross_pnl_usd", "booked_costs_usd",
    "available_cash_after_open_before_pending_usd",
}
_COST_KEYS = {"commissions", "financing", "borrow", "other"}
_EVENT_KEYS = {
    "event_id", "intent_id", "sequence", "kind", "at", "fill_quantity", "reconciled",
}
_EXPOSURE_KEYS = {
    "intent_id", "position_key", "entry_to_stop_risk_usd", "future_costs_usd",
    "gap_stress_usd", "pending_cash_usd", "gross_exposure_usd", "protection_verified",
    "has_pending_entry",
}
_GATE_KEYS = {"snapshot_id", "candidate_intent_id", "decision"}


class _InvalidInput(ValueError):
    pass


def _record(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _InvalidInput(label + "_schema_invalid")
    return dict(value)


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _InvalidInput(label + "_invalid")
    return value


def _number(value: Any, label: str, *, signed: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise _InvalidInput(label + "_invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise _InvalidInput(label + "_invalid") from exc
    # Bounded precision makes comparisons exact at the documented input scale.
    if (
        not result.is_finite() or abs(result) > _MAX_VALUE
        or result.as_tuple().exponent < -8
        or (not signed and result < 0)
    ):
        raise _InvalidInput(label + "_invalid")
    return result


def _integer(value: Any, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _InvalidInput(label + "_invalid")
    return value


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise _InvalidInput(label + "_invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _InvalidInput(label + "_invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise _InvalidInput(label + "_timezone_missing")
    return result.astimezone(timezone.utc)


def _money(value: Decimal) -> str:
    # No cent rounding down of risk, no conversion of unknown money to zero.
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _base_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model_version": "daily-risk-offline-v1",
        "mode": "OFFLINE_ONLY",
        "execution_authorized": False,
        "paper_authorized": False,
        "broker_evidence_independently_verified": False,
        "atomic_reservation_performed": False,
        "frozen_snapshot_only": True,
        "loss_ceiling_guaranteed": False,
        "input_valid": False,
        "within_model_limits": False,
        "pnl_basis": _BASIS,
        "reasons": [],
        "metrics": None,
    }


def _ledger(events: Any, start: datetime, end: datetime, observed: datetime) -> dict[str, Any]:
    if not isinstance(events, (list, tuple)):
        raise _InvalidInput("ledger_not_a_sequence")
    event_ids: dict[str, dict[str, Any]] = {}
    by_intent: dict[str, list[dict[str, Any]]] = {}
    for raw in events:
        event = _record(raw, _EVENT_KEYS, "event")
        event_id = _name(event["event_id"], "event_id")
        intent_id = _name(event["intent_id"], "intent_id")
        _integer(event["sequence"], "event_sequence", 1000000000)
        event["at"] = _time(event["at"], "event_time")
        event["fill_quantity"] = _number(event["fill_quantity"], "fill_quantity")
        if event["at"] > observed:
            raise _InvalidInput("event_after_snapshot")
        if event["reconciled"] is not True:
            raise _InvalidInput("event_not_reconciled")
        kind = event["kind"]
        if not isinstance(kind, str) or kind not in {"reserved", "entry_fill", "cancel_requested", "cancel_confirmed", "reject_confirmed", "closed"}:
            raise _InvalidInput("event_kind_invalid")
        if (kind == "entry_fill") != (event["fill_quantity"] > 0):
            raise _InvalidInput("event_fill_quantity_mismatch")
        if event_id in event_ids:
            if event_ids[event_id] != event:
                raise _InvalidInput("event_id_conflict")
            continue  # Exact replay is idempotent, never another trade.
        event_ids[event_id] = event
        by_intent.setdefault(intent_id, []).append(event)

    states: dict[str, dict[str, Any]] = {}
    for intent_id, intent_events in by_intent.items():
        state = {"status": "absent", "ever_filled": False, "filled_this_session": False}
        previous_time = None
        for index, event in enumerate(sorted(intent_events, key=lambda row: row["sequence"]), 1):
            if event["sequence"] != index:
                raise _InvalidInput("event_sequence_gap_or_conflict")
            if previous_time is not None and event["at"] < previous_time:
                raise _InvalidInput("event_time_regression")
            previous_time = event["at"]
            kind, status = event["kind"], state["status"]
            if kind == "reserved":
                if status != "absent":
                    raise _InvalidInput("intent_reserved_twice")
                state["status"] = "pending"
            elif kind == "entry_fill":
                if status not in {"pending", "open", "cancel_requested"}:
                    raise _InvalidInput("fill_without_live_intent")
                state["status"] = "open"
                state["ever_filled"] = True
                state["filled_this_session"] |= start <= event["at"] < end
            elif kind == "cancel_requested":
                if status not in {"pending", "open", "cancel_requested"}:
                    raise _InvalidInput("cancel_without_live_intent")
                state["status"] = "cancel_requested"
            elif kind in {"cancel_confirmed", "reject_confirmed"}:
                if status not in {"pending", "open", "cancel_requested"}:
                    raise _InvalidInput("terminal_without_live_intent")
                # A partially filled cancellation keeps both risk and its slot.
                state["status"] = "open_terminal" if state["ever_filled"] else "unfilled_terminal"
            else:
                if status not in {"open", "open_terminal", "cancel_requested"} or not state["ever_filled"]:
                    raise _InvalidInput("close_without_open_fill")
                state["status"] = "closed"
        states[intent_id] = state

    slot_ids = {
        key for key, state in states.items()
        if state["filled_this_session"]
        or (not state["ever_filled"] and state["status"] in {"pending", "cancel_requested"})
    }
    active_ids = {
        key for key, state in states.items()
        if state["status"] not in {"closed", "unfilled_terminal"}
    }
    return {"states": states, "slot_ids": slot_ids, "active_ids": active_ids}


def _exposure(raw: Any) -> dict[str, Any]:
    result = _record(raw, _EXPOSURE_KEYS, "exposure")
    _name(result["intent_id"], "exposure_intent_id")
    _name(result["position_key"], "position_key")
    if result["protection_verified"] is not True:
        raise _InvalidInput("unprotected_or_unverified_exposure")
    if type(result["has_pending_entry"]) is not bool:
        raise _InvalidInput("pending_entry_state_unknown")
    for key in _EXPOSURE_KEYS - {"intent_id", "position_key", "protection_verified", "has_pending_entry"}:
        result[key] = _number(result[key], key)
    if result["has_pending_entry"] != (result["pending_cash_usd"] > 0):
        raise _InvalidInput("pending_cash_state_mismatch")
    return result


def assess_daily_risk(
    *, session: Any, expected_session: Any, policy: Any, snapshot: Any,
    ledger_events: Any, exposures: Any, candidate: Any, existing_gates: Any,
) -> dict[str, Any]:
    """Assess one candidate against a complete caller-supplied frozen snapshot.

    ``expected_session`` must be an externally retained immutable session anchor,
    not a copy generated from the current broker balance on each call. Exact
    schema validation rejects unknown fields (including mark-to-market PnL).
    Even a valid hypothetical PASS gives NO authority to place an order.
    """
    report = _base_report()
    try:
        with localcontext() as context:
            context.prec = 50
            values = _assess(
                session, expected_session, policy, snapshot, ledger_events,
                exposures, candidate, existing_gates,
            )
        report.update(values)
    except _InvalidInput as exc:
        report["reasons"] = [str(exc)]
    return report


def _assess(session: Any, expected_session: Any, policy: Any, snapshot: Any,
            ledger_events: Any, exposures: Any, candidate: Any, existing_gates: Any) -> dict[str, Any]:
    session = _record(session, _SESSION_KEYS, "session")
    anchor = _record(expected_session, _SESSION_KEYS, "expected_session")
    if session != anchor:
        raise _InvalidInput("session_anchor_mismatch")
    for key in ("session_id", "account_id"):
        _name(session[key], key)
    if session["currency"] != "USD":
        raise _InvalidInput("session_currency_not_usd")
    start, end = _time(session["start_at"], "session_start"), _time(session["end_at"], "session_end")
    if end <= start:
        raise _InvalidInput("session_interval_invalid")
    equity = _number(session["start_equity_usd"], "start_equity")
    confirmed_capital = _number(session["confirmed_capital_usd"], "confirmed_capital")
    if equity <= 0 or confirmed_capital <= 0:
        raise _InvalidInput("session_capital_not_positive")
    # Profit/new-day balance growth alone cannot raise the confirmed risk basis.
    basis = min(equity, confirmed_capital)

    policy = _record(policy, _POLICY_KEYS, "policy")
    max_entries = _integer(policy["max_daily_entries"], "max_daily_entries", 3)
    max_positions = _integer(policy["max_positions"], "max_positions", 20)
    for key in _POLICY_KEYS - {"max_daily_entries", "max_positions"}:
        policy[key] = _number(policy[key], key)
    for key in ("daily_loss_pct", "per_trade_risk_pct", "aggregate_risk_pct", "max_gross_exposure_pct"):
        if policy[key] > 100:
            raise _InvalidInput(key + "_above_100")

    snapshot = _record(snapshot, _SNAPSHOT_KEYS, "snapshot")
    snapshot_id = _name(snapshot["snapshot_id"], "snapshot_id")
    for key in ("session_id", "account_id", "currency"):
        if snapshot[key] != session[key]:
            raise _InvalidInput("snapshot_" + key + "_mismatch")
    observed = _time(snapshot["observed_at"], "snapshot_time")
    if not start <= observed < end:
        raise _InvalidInput("snapshot_outside_session")
    if not isinstance(snapshot["evidence_kind"], str) or snapshot["evidence_kind"] not in _EVIDENCE:
        raise _InvalidInput("evidence_kind_invalid")
    for key in ("reconciled_complete", "ledger_complete", "positions_complete", "orders_complete", "fills_complete", "costs_complete"):
        if snapshot[key] is not True:
            raise _InvalidInput(key + "_not_true")
    if snapshot["pnl_basis"] != _BASIS:
        raise _InvalidInput("pnl_basis_invalid")
    gross = _number(snapshot["realized_gross_pnl_usd"], "realized_gross_pnl", signed=True)
    costs = _record(snapshot["booked_costs_usd"], _COST_KEYS, "booked_costs")
    booked_costs = sum((_number(value, "booked_" + key) for key, value in costs.items()), _ZERO)
    net = gross - booked_costs
    loss_consumed = max(_ZERO, -net)  # Realized profits cannot add risk capacity.
    available_cash = _number(snapshot["available_cash_after_open_before_pending_usd"], "available_cash")

    ledger = _ledger(ledger_events, start, end, observed)
    if not isinstance(exposures, (list, tuple)):
        raise _InvalidInput("exposures_not_a_sequence")
    current: dict[str, dict[str, Any]] = {}
    for raw in exposures:
        item = _exposure(raw)
        intent_id = item["intent_id"]
        if intent_id in current:
            raise _InvalidInput("duplicate_exposure_intent")
        current[intent_id] = item
    if set(current) != ledger["active_ids"]:
        raise _InvalidInput("ledger_exposure_coverage_mismatch")
    for intent_id, item in current.items():
        state = ledger["states"][intent_id]
        if not state["ever_filled"] and any(item[key] <= 0 for key in ("entry_to_stop_risk_usd", "pending_cash_usd", "gross_exposure_usd")):
            raise _InvalidInput("pending_exposure_risk_or_cash_missing")
        if state["status"] == "open_terminal" and item["has_pending_entry"]:
            raise _InvalidInput("terminal_intent_has_pending_cash")
        # An overnight partial fill can still have an unfilled entry remainder.
        # That remainder reserves today's slot even before another fill arrives.
        if item["has_pending_entry"]:
            ledger["slot_ids"].add(intent_id)
    candidate = _exposure(candidate)
    candidate_id = candidate["intent_id"]
    if candidate["entry_to_stop_risk_usd"] <= 0:
        raise _InvalidInput("candidate_stop_risk_not_positive")
    if candidate["gross_exposure_usd"] <= 0 or not candidate["has_pending_entry"]:
        raise _InvalidInput("candidate_cash_or_exposure_not_positive")
    if candidate_id in ledger["states"]:
        state = ledger["states"][candidate_id]
        if state["ever_filled"] or state["status"] != "pending":
            raise _InvalidInput("candidate_intent_not_new_or_pending")
        if current.get(candidate_id) != candidate:
            raise _InvalidInput("candidate_reservation_conflict")
        candidate_already_reserved = True
    else:
        candidate_already_reserved = False

    gates = _record(existing_gates, _GATE_KEYS, "existing_gates")
    if gates["snapshot_id"] != snapshot_id or gates["candidate_intent_id"] != candidate_id:
        raise _InvalidInput("existing_gates_identity_mismatch")
    if not isinstance(gates["decision"], str) or gates["decision"] not in {"passed", "blocked", "unresolved"}:
        raise _InvalidInput("existing_gates_decision_invalid")
    reasons = []
    if gates["decision"] != "passed":
        reasons.append("existing_gates_" + gates["decision"])

    def stressed(item: dict[str, Any]) -> Decimal:
        return item["entry_to_stop_risk_usd"] + item["future_costs_usd"] + item["gap_stress_usd"]

    daily_limit = min(policy["user_daily_loss_tolerance_usd"], policy["existing_daily_limit_usd"], basis * policy["daily_loss_pct"] / _ONE_HUNDRED)
    trade_limit = min(policy["existing_trade_limit_usd"], basis * policy["per_trade_risk_pct"] / _ONE_HUNDRED)
    aggregate_limit = min(policy["existing_aggregate_limit_usd"], basis * policy["aggregate_risk_pct"] / _ONE_HUNDRED)
    current_risk = sum((stressed(item) for item in current.values()), _ZERO)
    candidate_risk = stressed(candidate)
    incremental_risk = _ZERO if candidate_already_reserved else candidate_risk
    projected_risk = current_risk + incremental_risk
    raw_remaining = daily_limit - loss_consumed - current_risk
    projected_loss = loss_consumed + projected_risk
    projected = list(current.values()) + ([] if candidate_already_reserved else [candidate])
    projected_slots = len(ledger["slot_ids"] | {candidate_id})
    position_slots = len({item["position_key"] for item in projected})
    pending_cash = sum((item["pending_cash_usd"] for item in projected), _ZERO)
    future_costs = sum((item["future_costs_usd"] for item in projected), _ZERO)
    gross_exposure = sum((item["gross_exposure_usd"] for item in projected), _ZERO)
    cash_after = available_cash - pending_cash - future_costs

    if loss_consumed >= daily_limit:
        reasons.append("daily_loss_budget_exhausted")
    if projected_loss > daily_limit:
        reasons.append("prospective_daily_loss_limit_exceeded")
    if candidate_risk > trade_limit:
        reasons.append("per_trade_stressed_risk_exceeded")
    if projected_risk > aggregate_limit:
        reasons.append("aggregate_stressed_risk_exceeded")
    if projected_slots > max_entries:
        reasons.append("daily_entry_slots_exceeded")
    if position_slots > max_positions:
        reasons.append("position_slots_exceeded")
    if cash_after < policy["minimum_cash_usd"]:
        reasons.append("minimum_cash_reserve_exceeded")
    if gross_exposure > basis * policy["max_gross_exposure_pct"] / _ONE_HUNDRED:
        reasons.append("gross_exposure_limit_exceeded")
    return {
        "input_valid": True,
        "within_model_limits": not reasons,
        "evidence_kind": snapshot["evidence_kind"],
        "snapshot_observed_at": observed.isoformat(),
        "session_start_at": start.isoformat(),
        "session_end_at": end.isoformat(),
        "reasons": reasons,
        "metrics": {
            "risk_basis_usd": _money(basis),
            "effective_daily_limit_usd": _money(daily_limit),
            "effective_per_trade_limit_usd": _money(trade_limit),
            "effective_aggregate_limit_usd": _money(aggregate_limit),
            "realized_net_pnl_usd": _money(net),
            "realized_loss_consumed_usd": _money(loss_consumed),
            "current_stressed_risk_usd": _money(current_risk),
            "candidate_stressed_risk_usd": _money(candidate_risk),
            "incremental_candidate_risk_usd": _money(incremental_risk),
            "remaining_daily_budget_before_candidate_usd": _money(max(_ZERO, raw_remaining)),
            "existing_daily_budget_overrun_usd": _money(max(_ZERO, -raw_remaining)),
            "projected_stressed_loss_usd": _money(projected_loss),
            "remaining_daily_budget_after_candidate_usd": _money(max(_ZERO, daily_limit - projected_loss)),
            "projected_pending_cash_usd": _money(pending_cash),
            "projected_future_costs_usd": _money(future_costs),
            "cash_after_pending_and_future_costs_usd": _money(cash_after),
            "projected_gross_exposure_usd": _money(gross_exposure),
            "current_entry_slots": len(ledger["slot_ids"]),
            "projected_entry_slots": projected_slots,
            "projected_position_slots": position_slots,
            "candidate_already_reserved": candidate_already_reserved,
        },
    }


__all__ = ["assess_daily_risk"]
