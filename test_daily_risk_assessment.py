"""Pure offline contract tests. No fixture is broker evidence or a live gate."""

from copy import deepcopy
from decimal import Decimal

import pytest

from modules.daily_risk_assessment import assess_daily_risk


def exposure(intent="new", **changes):
    value = {
        "intent_id": intent, "position_key": intent,
        "entry_to_stop_risk_usd": "10", "future_costs_usd": "1",
        "gap_stress_usd": "1", "pending_cash_usd": "100",
        "gross_exposure_usd": "100", "protection_verified": True,
        "has_pending_entry": True,
    }
    value.update(changes)
    return value


def event(intent, sequence=1, kind="reserved", *, at="2026-09-08T10:00:00Z", **changes):
    value = {
        "event_id": f"{intent}:{sequence}", "intent_id": intent,
        "sequence": sequence, "kind": kind, "at": at,
        "fill_quantity": "1" if kind == "entry_fill" else "0", "reconciled": True,
    }
    value.update(changes)
    return value


def request():
    session = {
        "session_id": "USD-session-20260908", "account_id": "OFFLINE-EXAMPLE",
        "currency": "USD", "start_at": "2026-09-08T00:00:00Z",
        "end_at": "2026-09-09T00:00:00Z", "start_equity_usd": "5000",
        "confirmed_capital_usd": "5000",
    }
    return {
        "session": session, "expected_session": deepcopy(session),
        "policy": {
            "user_daily_loss_tolerance_usd": "300", "daily_loss_pct": "1",
            "per_trade_risk_pct": "0.25", "aggregate_risk_pct": "0.75",
            "existing_daily_limit_usd": "50", "existing_trade_limit_usd": "12.5",
            "existing_aggregate_limit_usd": "37.5", "max_daily_entries": 3,
            "max_positions": 3, "minimum_cash_usd": "500",
            "max_gross_exposure_pct": "20",
        },
        "snapshot": {
            "snapshot_id": "offline-snapshot-1", "session_id": session["session_id"],
            "account_id": session["account_id"], "currency": "USD",
            "observed_at": "2026-09-08T12:00:00Z", "evidence_kind": "hypothetical",
            "reconciled_complete": True, "ledger_complete": True,
            "positions_complete": True, "orders_complete": True,
            "fills_complete": True, "costs_complete": True,
            "pnl_basis": "realized_net_plus_entry_to_stop",
            "realized_gross_pnl_usd": "0",
            "booked_costs_usd": {"commissions": "0", "financing": "0", "borrow": "0", "other": "0"},
            "available_cash_after_open_before_pending_usd": "5000",
        },
        "ledger_events": [], "exposures": [], "candidate": exposure(),
        "existing_gates": {"snapshot_id": "offline-snapshot-1", "candidate_intent_id": "new", "decision": "passed"},
    }


def assess(**updates):
    values = request()
    values.update(updates)
    return assess_daily_risk(**values)


def test_confirmed_5000_and_300_never_relaxes_existing_one_percent_limit():
    result = assess()
    assert result["input_valid"] and result["within_model_limits"]
    assert result["metrics"]["effective_daily_limit_usd"] == "50"
    assert result["metrics"]["effective_per_trade_limit_usd"] == "12.5"
    assert result["metrics"]["effective_aggregate_limit_usd"] == "37.5"
    assert result["metrics"]["projected_stressed_loss_usd"] == "12"
    assert result["metrics"]["remaining_daily_budget_after_candidate_usd"] == "38"
    assert result["evidence_kind"] == "hypothetical"
    assert result["mode"] == "OFFLINE_ONLY"
    for key in ("execution_authorized", "paper_authorized", "atomic_reservation_performed", "loss_ceiling_guaranteed", "broker_evidence_independently_verified"):
        assert result[key] is False


@pytest.mark.parametrize("evidence", ["hypothetical", "caller_reconciled_broker_snapshot"])
def test_caller_evidence_label_is_not_promoted_to_verified_broker_data(evidence):
    values = request()
    values["snapshot"]["evidence_kind"] = evidence
    result = assess_daily_risk(**values)
    assert result["evidence_kind"] == evidence
    assert not result["broker_evidence_independently_verified"]
    assert not result["execution_authorized"]


def test_loss_current_pending_candidate_cost_and_gap_all_use_same_budget():
    values = request()
    values["snapshot"]["realized_gross_pnl_usd"] = "-28"
    values["snapshot"]["booked_costs_usd"]["commissions"] = "2"
    values["ledger_events"] = [event("old")]
    values["exposures"] = [exposure("old")]
    result = assess_daily_risk(**values)
    assert result["input_valid"] and not result["within_model_limits"]
    assert result["reasons"] == ["prospective_daily_loss_limit_exceeded"]
    assert result["metrics"]["realized_net_pnl_usd"] == "-30"
    assert result["metrics"]["current_stressed_risk_usd"] == "12"
    assert result["metrics"]["remaining_daily_budget_before_candidate_usd"] == "8"
    assert result["metrics"]["projected_stressed_loss_usd"] == "54"


def test_all_booked_cost_components_debited_once():
    values = request()
    values["snapshot"]["realized_gross_pnl_usd"] = "10"
    values["snapshot"]["booked_costs_usd"] = {"commissions": "4", "financing": "3", "borrow": "2", "other": "2"}
    result = assess_daily_risk(**values)
    assert result["metrics"]["realized_net_pnl_usd"] == "-1"
    assert result["metrics"]["projected_stressed_loss_usd"] == "13"


def test_realized_profit_cannot_expand_budget_above_original_cap():
    values = request()
    values["snapshot"]["realized_gross_pnl_usd"] = "150"
    result = assess_daily_risk(**values)
    assert result["metrics"]["realized_loss_consumed_usd"] == "0"
    assert result["metrics"]["remaining_daily_budget_before_candidate_usd"] == "50"


def test_all_minimum_caps_preserve_stricter_limits():
    values = request()
    values["policy"].update(existing_daily_limit_usd="40", existing_trade_limit_usd="11", existing_aggregate_limit_usd="9")
    result = assess_daily_risk(**values)
    assert result["metrics"]["effective_daily_limit_usd"] == "40"
    assert "per_trade_stressed_risk_exceeded" in result["reasons"]
    assert "aggregate_stressed_risk_exceeded" in result["reasons"]
    values["policy"]["user_daily_loss_tolerance_usd"] = "7"
    result = assess_daily_risk(**values)
    assert result["metrics"]["effective_daily_limit_usd"] == "7"


def test_higher_next_session_equity_cannot_raise_confirmed_capital_basis():
    values = request()
    for key in ("session", "expected_session"):
        values[key]["start_equity_usd"] = "6000"
    values["policy"].update(existing_daily_limit_usd="60", existing_trade_limit_usd="15", existing_aggregate_limit_usd="45")
    result = assess_daily_risk(**values)
    assert result["metrics"]["risk_basis_usd"] == "5000"
    assert result["metrics"]["effective_daily_limit_usd"] == "50"


def test_lower_session_equity_tightens_basis():
    values = request()
    for key in ("session", "expected_session"):
        values[key]["start_equity_usd"] = "4000"
    result = assess_daily_risk(**values)
    assert result["metrics"]["effective_daily_limit_usd"] == "40"
    assert result["metrics"]["effective_per_trade_limit_usd"] == "10"
    assert not result["within_model_limits"]


def test_exact_decimal_equality_is_allowed_but_one_real_unit_excess_is_not():
    values = request()
    values["snapshot"]["realized_gross_pnl_usd"] = "-37.5"
    values["candidate"]["entry_to_stop_risk_usd"] = "10.5"
    assert assess_daily_risk(**values)["within_model_limits"]
    values["candidate"]["entry_to_stop_risk_usd"] = "10.50000001"
    result = assess_daily_risk(**values)
    assert not result["within_model_limits"]
    assert "prospective_daily_loss_limit_exceeded" in result["reasons"]
    assert "per_trade_stressed_risk_exceeded" in result["reasons"]


def test_limit_exhaustion_and_existing_overrun_are_visible_not_negative_capacity():
    values = request()
    values["snapshot"]["realized_gross_pnl_usd"] = "-51"
    result = assess_daily_risk(**values)
    assert "daily_loss_budget_exhausted" in result["reasons"]
    assert result["metrics"]["remaining_daily_budget_before_candidate_usd"] == "0"
    assert result["metrics"]["existing_daily_budget_overrun_usd"] == "1"


def test_closed_filled_intents_still_consume_three_daily_slots():
    values = request()
    for intent in ("one", "two", "three"):
        values["ledger_events"] += [event(intent), event(intent, 2, "entry_fill"), event(intent, 3, "closed")]
    result = assess_daily_risk(**values)
    assert result["input_valid"]
    assert result["metrics"]["projected_position_slots"] == 1
    assert result["metrics"]["projected_entry_slots"] == 4
    assert result["reasons"] == ["daily_entry_slots_exceeded"]


@pytest.mark.parametrize("terminal", ["cancel_confirmed", "reject_confirmed"])
def test_entirely_unfilled_reconciled_terminal_refunds_slot(terminal):
    values = request()
    values["ledger_events"] = [event("old"), event("old", 2, terminal)]
    result = assess_daily_risk(**values)
    assert result["within_model_limits"]
    assert result["metrics"]["current_entry_slots"] == 0


def test_pending_cancel_does_not_refund_slot_or_risk():
    values = request()
    values["ledger_events"] = [event("old"), event("old", 2, "cancel_requested")]
    values["exposures"] = [exposure("old")]
    result = assess_daily_risk(**values)
    assert result["metrics"]["current_entry_slots"] == 1
    assert result["metrics"]["current_stressed_risk_usd"] == "12"


def test_partial_fill_then_cancel_never_refunds_and_keeps_remaining_open_risk():
    values = request()
    values["ledger_events"] = [event("old"), event("old", 2, "entry_fill", fill_quantity="0.01"), event("old", 3, "cancel_confirmed")]
    values["exposures"] = [exposure("old", pending_cash_usd="0", has_pending_entry=False)]
    result = assess_daily_risk(**values)
    assert result["input_valid"]
    assert result["metrics"]["current_entry_slots"] == 1
    assert result["metrics"]["current_stressed_risk_usd"] == "12"
    values["ledger_events"].append(event("old", 4, "closed"))
    values["exposures"] = []
    result = assess_daily_risk(**values)
    assert result["metrics"]["current_entry_slots"] == 1


def test_exact_event_retry_and_candidate_reservation_retry_are_idempotent():
    values = request()
    reserved = event("new")
    values["ledger_events"] = [reserved, deepcopy(reserved)]
    values["exposures"] = [deepcopy(values["candidate"])]
    result = assess_daily_risk(**values)
    assert result["within_model_limits"]
    assert result["metrics"]["candidate_already_reserved"]
    assert result["metrics"]["projected_entry_slots"] == 1
    assert result["metrics"]["incremental_candidate_risk_usd"] == "0"
    assert result["metrics"]["projected_stressed_loss_usd"] == "12"
    assert result["metrics"]["projected_pending_cash_usd"] == "100"


def test_idempotent_candidate_cannot_change_reserved_risk():
    values = request()
    values["ledger_events"] = [event("new")]
    values["exposures"] = [exposure("new", future_costs_usd="2")]
    assert assess_daily_risk(**values)["reasons"] == ["candidate_reservation_conflict"]


def test_carryover_pending_reserves_today_and_open_risk_carries_without_new_fill():
    values = request()
    yesterday = "2026-09-07T22:00:00Z"
    values["ledger_events"] = [event("pending", at=yesterday), event("held", at=yesterday), event("held", 2, "entry_fill", at=yesterday)]
    values["exposures"] = [exposure("pending"), exposure("held", pending_cash_usd="0", has_pending_entry=False)]
    result = assess_daily_risk(**values)
    assert result["within_model_limits"]
    assert result["metrics"]["current_entry_slots"] == 1
    assert result["metrics"]["current_stressed_risk_usd"] == "24"
    assert result["metrics"]["projected_position_slots"] == 3


def test_overnight_pending_fill_counts_on_actual_fill_session():
    values = request()
    values["ledger_events"] = [event("old", at="2026-09-07T22:00:00Z"), event("old", 2, "entry_fill"), event("old", 3, "closed")]
    assert assess_daily_risk(**values)["metrics"]["current_entry_slots"] == 1


def test_overnight_partial_fill_remaining_order_reserves_today_before_next_fill():
    values = request()
    yesterday = "2026-09-07T22:00:00Z"
    values["ledger_events"] = [event("old", at=yesterday), event("old", 2, "entry_fill", at=yesterday)]
    values["exposures"] = [exposure("old")]
    result = assess_daily_risk(**values)
    assert result["input_valid"]
    assert result["metrics"]["current_entry_slots"] == 1
    assert result["metrics"]["projected_entry_slots"] == 2


def test_future_fees_also_reserve_cash_not_only_loss_budget():
    values = request()
    values["snapshot"]["available_cash_after_open_before_pending_usd"] = "600"
    result = assess_daily_risk(**values)
    assert result["input_valid"]
    assert result["metrics"]["cash_after_pending_and_future_costs_usd"] == "499"
    assert result["reasons"] == ["minimum_cash_reserve_exceeded"]


def test_pending_entry_must_have_explicit_positive_cash_reservation():
    values = request()
    values["candidate"]["pending_cash_usd"] = "0"
    assert assess_daily_risk(**values)["reasons"] == ["pending_cash_state_mismatch"]


def test_prior_session_closed_trade_does_not_use_current_entry_slot():
    values = request()
    yesterday = "2026-09-07T22:00:00Z"
    values["ledger_events"] = [event("old", at=yesterday), event("old", 2, "entry_fill", at=yesterday), event("old", 3, "closed", at=yesterday)]
    assert assess_daily_risk(**values)["metrics"]["current_entry_slots"] == 0


@pytest.mark.parametrize("change,reason", [
    ({"max_positions": 1}, "position_slots_exceeded"),
    ({"max_daily_entries": 1}, "daily_entry_slots_exceeded"),
    ({"minimum_cash_usd": "4900"}, "minimum_cash_reserve_exceeded"),
    ({"max_gross_exposure_pct": "2"}, "gross_exposure_limit_exceeded"),
])
def test_position_cash_exposure_and_tighter_daily_slot_limits(change, reason):
    values = request()
    values["ledger_events"] = [event("old")]
    values["exposures"] = [exposure("old")]
    values["policy"].update(change)
    assert reason in assess_daily_risk(**values)["reasons"]


@pytest.mark.parametrize("decision", ["blocked", "unresolved"])
def test_existing_gates_are_never_overridden_by_positive_arithmetic(decision):
    values = request()
    values["existing_gates"]["decision"] = decision
    result = assess_daily_risk(**values)
    assert result["input_valid"] and not result["within_model_limits"]
    assert result["reasons"] == ["existing_gates_" + decision]


@pytest.mark.parametrize("flag", ["reconciled_complete", "ledger_complete", "positions_complete", "orders_complete", "fills_complete", "costs_complete"])
@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_completeness_must_be_literal_true(flag, value):
    values = request()
    values["snapshot"][flag] = value
    result = assess_daily_risk(**values)
    assert not result["input_valid"] and result["metrics"] is None


@pytest.mark.parametrize("bad", [None, True, False, "NaN", float("inf"), "-Infinity", "-0.01", "garbage", [], {}, "1e20", "0.000000001"])
@pytest.mark.parametrize("location,key", [("candidate", "future_costs_usd"), ("policy", "existing_daily_limit_usd"), ("session", "start_equity_usd")])
def test_invalid_numeric_inputs_fail_closed(bad, location, key):
    values = request()
    values[location][key] = bad
    if location == "session":
        values["expected_session"][key] = bad
    result = assess_daily_risk(**values)
    assert not result["input_valid"] and not result["within_model_limits"]
    assert result["metrics"] is None


@pytest.mark.parametrize("bad", [None, True, "3", 3.0, 0, -1, 4])
def test_daily_entries_requires_integer_one_to_three(bad):
    values = request()
    values["policy"]["max_daily_entries"] = bad
    assert not assess_daily_risk(**values)["input_valid"]


@pytest.mark.parametrize("location", ["session", "policy", "snapshot", "candidate", "existing_gates"])
def test_unknown_fields_are_not_silently_discarded(location):
    values = request()
    values[location]["unrealized_pnl_usd"] = "-20"
    result = assess_daily_risk(**values)
    assert not result["input_valid"] and result["metrics"] is None


def test_mark_to_market_basis_cannot_double_count_unrealized_losses():
    values = request()
    values["snapshot"]["pnl_basis"] = "mark_to_market"
    assert assess_daily_risk(**values)["reasons"] == ["pnl_basis_invalid"]


def test_unknown_booked_costs_are_not_zero():
    values = request()
    del values["snapshot"]["booked_costs_usd"]["borrow"]
    assert assess_daily_risk(**values)["reasons"] == ["booked_costs_schema_invalid"]
    values = request()
    values["snapshot"]["booked_costs_usd"]["borrow"] = None
    assert not assess_daily_risk(**values)["input_valid"]


def test_unprotected_and_missing_exposure_fail_closed():
    values = request()
    values["candidate"]["protection_verified"] = False
    assert assess_daily_risk(**values)["reasons"] == ["unprotected_or_unverified_exposure"]
    values = request()
    values["ledger_events"] = [event("old")]
    assert assess_daily_risk(**values)["reasons"] == ["ledger_exposure_coverage_mismatch"]


@pytest.mark.parametrize("key,value", [("session_id", "other-day"), ("start_equity_usd", "5001"), ("account_id", "other-account"), ("start_at", "2026-09-08T01:00:00Z")])
def test_restart_cannot_replace_immutable_session_anchor(key, value):
    values = request()
    values["session"][key] = value
    assert assess_daily_risk(**values)["reasons"] == ["session_anchor_mismatch"]


@pytest.mark.parametrize("key,value", [("session_id", "yesterday"), ("account_id", "other"), ("currency", "EUR"), ("observed_at", "2026-09-09T00:00:00Z"), ("observed_at", "2026-09-08T12:00:00")])
def test_snapshot_session_identity_time_and_timezone_fail_closed(key, value):
    values = request()
    values["snapshot"][key] = value
    assert not assess_daily_risk(**values)["input_valid"]


def test_dst_session_uses_explicit_aware_boundaries_not_utc_day_guess():
    values = request()
    for key in ("session", "expected_session"):
        values[key].update(start_at="2026-10-25T00:00:00+02:00", end_at="2026-10-26T00:00:00+01:00")
    values["snapshot"]["observed_at"] = "2026-10-25T23:30:00+01:00"
    assert assess_daily_risk(**values)["within_model_limits"]


def test_fresh_existing_gate_result_must_match_snapshot_and_candidate():
    for key, value in (("snapshot_id", "older-snapshot"), ("candidate_intent_id", "other-intent")):
        values = request()
        values["existing_gates"][key] = value
        assert assess_daily_risk(**values)["reasons"] == ["existing_gates_identity_mismatch"]


@pytest.mark.parametrize("events,reason", [
    ([event("old", 2)], "event_sequence_gap_or_conflict"),
    ([event("old"), event("old", 2)], "intent_reserved_twice"),
    ([event("old", kind="entry_fill")], "fill_without_live_intent"),
    ([event("old"), event("old", 2, "closed")], "close_without_open_fill"),
    ([event("old", at="2026-09-08T13:00:00Z")], "event_after_snapshot"),
    ([event("old"), event("old", 2, "entry_fill", at="2026-09-08T09:00:00Z")], "event_time_regression"),
    ([event("old"), event("old", 2, "cancel_confirmed", reconciled=False)], "event_not_reconciled"),
    ([event("old"), event("old", 2, "cancel_confirmed"), event("old", 3, "entry_fill")], "fill_without_live_intent"),
    ([event("old", kind=[])], "event_kind_invalid"),
])
def test_incomplete_or_contradictory_ledger_fails_closed(events, reason):
    values = request()
    values["ledger_events"] = events
    assert assess_daily_risk(**values)["reasons"] == [reason]


def test_event_id_conflict_and_duplicate_exposure_are_not_deduped_away():
    values = request()
    values["ledger_events"] = [event("old"), event("different", event_id="old:1")]
    assert assess_daily_risk(**values)["reasons"] == ["event_id_conflict"]
    values["ledger_events"] = [event("old")]
    values["exposures"] = [exposure("old"), exposure("old")]
    assert assess_daily_risk(**values)["reasons"] == ["duplicate_exposure_intent"]


def test_filled_or_terminal_intent_cannot_be_reused_as_new_entry():
    values = request()
    values["ledger_events"] = [event("new"), event("new", 2, "entry_fill")]
    values["exposures"] = [exposure("new")]
    assert assess_daily_risk(**values)["reasons"] == ["candidate_intent_not_new_or_pending"]


def test_assessment_is_immutable_deterministic_and_not_a_concurrent_reservation():
    values = request()
    original = deepcopy(values)
    result = assess_daily_risk(**values)
    assert values == original
    assert result == assess_daily_risk(**deepcopy(values))
    # Two independent callers can evaluate the same snapshot: neither acquired
    # a slot. Only the existing store's future atomic integration may do that.
    assert result["atomic_reservation_performed"] is False
    assert values["ledger_events"] == []


def test_decimal_inputs_are_supported_without_binary_float_rounding():
    values = request()
    values["candidate"]["future_costs_usd"] = Decimal("0.10")
    values["candidate"]["gap_stress_usd"] = 0.2
    result = assess_daily_risk(**values)
    assert result["metrics"]["candidate_stressed_risk_usd"] == "10.3"


def test_import_has_no_broker_store_config_filesystem_or_network_dependency():
    import ast
    from pathlib import Path

    source = Path(__file__).parent.joinpath("modules", "daily_risk_assessment.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported == {"__future__", "collections.abc", "datetime", "decimal", "typing"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"open", "eval", "exec", "__import__"}
                   for node in ast.walk(tree))


def test_unknown_boolean_pending_state_and_missing_pending_cash_fail_closed():
    values = request()
    values["candidate"]["has_pending_entry"] = 1
    assert assess_daily_risk(**values)["reasons"] == ["pending_entry_state_unknown"]
    values = request()
    values["ledger_events"] = [event("old")]
    values["exposures"] = [exposure("old", pending_cash_usd="0", has_pending_entry=False)]
    assert assess_daily_risk(**values)["reasons"] == ["pending_exposure_risk_or_cash_missing"]
