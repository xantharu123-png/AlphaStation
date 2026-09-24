"""Visibility does not manufacture permission, data, or canonical trade state."""
from copy import deepcopy

import pytest

import api
from modules import scanner_visibility as view


def _row(**changes):
    return {"ticker": "TEST", "price": 100, "score": 90, "grade": "A",
            "trade_decision": "TRADEABLE", "trade_action": "LONG_NOW",
            "trade_signal": "JETZT_TRADEN", **changes}


@pytest.mark.parametrize("key", ["trade_setup", "trade_health", "_quality", "native_plan_diagnostics"])
@pytest.mark.parametrize("value", ["bad", [1, 2], 7, True])
def test_malformed_optional_metadata_is_warning_never_exception_or_release(key, value):
    row = _row(**{key: value})
    before = deepcopy(row)
    presented = view.present(row, released=True)
    assert row == before
    assert presented["visibility_status"] == "candidate_warning"
    assert "display_metadata_invalid" in {warning["code"] for warning in presented["visibility_warnings"]}
    for field in ("trade_action", "trade_signal", "trade_decision"):
        assert presented[field] == before[field]


@pytest.mark.parametrize("changes", [
    {"entry_status": "WAIT_FOR_RETEST"}, {"scanner_decision": "NO_TRADE"},
    {"signal_quality": "watch_or_blocked"}, {"native_plan_status": "unavailable"},
    {"native_plan_reason": "invalid_trade_geometry"}, {"barrier_gate_active": True},
    {"native_plan_diagnostics": {"reason": "crossed_resistance_unconfirmed"}},
])
def test_canonical_blocker_or_invalid_plan_cannot_get_released_label(changes):
    presented = view.present(_row(**changes), released=True)
    assert presented["visibility_status"] == "candidate_warning"
    assert presented["visibility_is_trade_signal"] is False


@pytest.mark.parametrize("reason", [
    "trigger_stale_for_mail", "new_listing_micro_trigger_expired",
    "closed_5m_trigger_stale", "fresh_closed_5m_trigger_missing",
    "closed_5m_data_stale", "stale_5m_candle",
])
def test_expired_trigger_is_visible_as_warning_if_price_setup_data_remain_valid(reason):
    row = _row(scanner_suppression_reasons=[reason])
    assert view.unusable_reason(row) is None
    presented = view.present(row, released=True)
    assert presented["visibility_status"] == "candidate_warning"
    assert "trigger_not_current" in {warning["code"] for warning in presented["visibility_warnings"]}


@pytest.mark.parametrize("changes", [
    {"data_status": "invalid_ohlcv"}, {"market_data_valid": False},
    {"ohlcv_valid": False}, {"_quality": {"data_valid": False}},
    {"data_error_code": "invalid_bar_value"}, {"risk_flags": ["partial_crypto_data"]},
    {"scanner_suppression_reasons": ["stale_market_data"]},
])
def test_known_bad_market_data_remain_excluded(changes):
    assert api._apply_scanner_visibility_policy("crypto_trade_signals", [_row(**changes)]) == []


@pytest.mark.parametrize("price", [None, 0, -1, True, float("nan"), float("inf")])
def test_nonpositive_nonfinite_or_boolean_prices_are_not_candidates(price):
    assert api._apply_scanner_visibility_policy("crypto_trade_signals", [_row(price=price)]) == []


def test_missing_instrument_identity_is_not_a_candidate():
    assert api._apply_scanner_visibility_policy("crypto_trade_signals", [_row(ticker="")]) == []


@pytest.mark.parametrize("scanner,action,score", [
    ("crypto_trade_signals", "JETZT_LONG", 10),
    ("crypto_trade_signals", "JETZT_SHORT", 10),
    ("penny_stocks", "JETZT_KAUFEN", 10),
])
def test_now_action_does_not_bypass_existing_score_policy(scanner, action, score):
    rows = api._apply_scanner_visibility_policy(scanner, [_row(trade_action=action, trade_score=score, score=score)])
    assert len(rows) == 1
    assert rows[0]["visibility_status"] == "candidate_warning"


@pytest.mark.parametrize("scanner,action", [
    ("crypto_trade_signals", "JETZT_LONG"), ("crypto_trade_signals", "JETZT_SHORT"),
    ("penny_stocks", "JETZT_KAUFEN"),
])
def test_valid_now_action_preserves_existing_release(scanner, action):
    rows = api._apply_scanner_visibility_policy(scanner, [_row(trade_action=action, trade_score=90)])
    assert rows[0]["visibility_status"] == "released"


@pytest.mark.parametrize("action", ["HALTEN", "AKTIV_HALTEN", "JETZT_VERKAUFEN"])
def test_position_management_is_retained_as_context_even_when_quote_is_stale(action):
    row = _row(trade_action=action, data_status="stale", price=None)
    rows = api._apply_scanner_visibility_policy("penny_stocks", [row])
    assert len(rows) == 1
    assert rows[0]["trade_action"] == action
    assert rows[0]["visibility_status"] == "context"
    assert rows[0]["visibility_is_trade_signal"] is False


def test_stale_context_placeholder_survives_without_becoming_trade_candidate():
    rows = api._apply_scanner_visibility_policy("market_context", [{"data_status": "stale"}])
    assert len(rows) == 1
    assert rows[0]["visibility_status"] == "context"
    assert rows[0]["visibility_warnings"]


def test_nested_orb_aliases_are_processed_but_not_double_counted():
    row = _row(native_plan_reason="first_opposing_barrier_before_minimum_rr")
    payload = {"breakouts": [row], "failed_breakouts": [], "candidates": [],
               "actionable_breakouts": [], "rejected_range_breaks": [row], "stats": "bad"}
    rows = api._apply_scanner_visibility_policy("orb", [payload])
    counts = rows[0]["stats"]["visibility_counts"]
    assert counts["total"] == counts["candidate_warning"] == 1
    assert rows[0]["rejected_range_breaks"][0]["visibility_status"] == "candidate_warning"
    assert payload["stats"] == "bad"
    assert "visibility_status" not in row


def test_orb_alias_only_container_is_retained_and_counted_once():
    rows = api._apply_scanner_visibility_policy("orb", [{"actionable_breakouts": [_row()], "rejected_range_breaks": []}])
    assert rows[0]["stats"]["visibility_counts"]["total"] == 1


def test_barrier_warning_reports_real_german_price_field_and_finite_distances():
    row = _row(price=None, Preis=100, native_plan_reason="first_opposing_barrier_before_minimum_rr",
               native_plan_diagnostics={"barrier": {"price": 101, "distance_r": 0.8, "side": "resistance", "timeframe": "1D"}})
    warnings = view.present(row)["visibility_warnings"]
    assert warnings[0]["price"] == 101
    assert warnings[0]["distance_pct"] == 1
    assert warnings[0]["distance_r"] == 0.8
    assert warnings[0]["timeframe"] == "1D"


def test_string_warning_and_bad_label_container_are_safe():
    presented = view.present(_row(_quality={"warnings": "Example warning"}), labels=["bad"])
    assert {warning["label"] for warning in presented["visibility_warnings"]} >= {"Example warning"}
