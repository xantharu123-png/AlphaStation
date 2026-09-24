"""Candidate display must not change trading or mail admission."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import api
from modules import scanner_visibility, stock_swing_contract


def row(**extra):
    return {"ticker": "TEST", "price": 100.0, "score": 91, "grade": "S",
            "trade_decision": "WAIT_FOR_TRIGGER", "trade_action": "WAIT_FOR_TRIGGER",
            "trade_signal": "WARTEN", **extra}


@pytest.mark.parametrize("scanner", ["strategy_scan", "stock_strategy", "turtle", "biotech", "bear", "orb", "crypto_strategy", "btc_divergenz", "new_listing"])
def test_waiting_scanner_candidate_visible_without_promoting_execution(scanner):
    source = row(native_plan_status="unavailable", native_plan_reason="crossed_resistance_unconfirmed")
    before = deepcopy(source)
    visible = api._apply_scanner_visibility_policy(scanner, [source])
    assert len(visible) == 1
    assert visible[0]["visibility_status"] == "candidate_warning"
    assert not visible[0]["visibility_is_trade_signal"]
    assert any(w["code"] == "crossed_resistance_unconfirmed" for w in visible[0]["visibility_warnings"])
    for key in ("trade_decision", "trade_action", "trade_signal", "score", "grade"):
        assert visible[0][key] == source[key]
    assert source == before


def test_old_signal_filter_stays_strict_and_separate():
    source = row(native_plan_reason="first_opposing_barrier_before_minimum_rr")
    assert api._apply_scanner_visibility_policy("stock_strategy", [source])
    assert api._apply_signal_only_policy("stock_strategy", [source]) == []
    assert not api._scanner_row_is_trade_signal(source, "stock_strategy")


def test_barrier_warning_reports_real_price_distance_and_r():
    source = row(native_plan_reason="first_opposing_barrier_before_minimum_rr", nearest_barrier={
        "price": 101.0, "distance_r": 0.4, "timeframe": "1D", "side": "resistance"})
    presented = api._apply_scanner_visibility_policy("stock_strategy", [source])[0]
    warning = presented["visibility_warnings"][0]
    assert warning["price"] == 101
    assert warning["distance_pct"] == 1
    assert warning["distance_r"] == 0.4
    assert warning["timeframe"] == "1D"


def test_native_plan_failure_has_warning_even_when_old_decorator_says_tradeable():
    source = row(trade_decision="TRADEABLE", trade_signal="JETZT_TRADEN", trade_action="LONG_NOW",
                 native_plan_status="unavailable", native_plan_reason="invalid_trade_geometry")
    actual = api._apply_scanner_visibility_policy("stock_strategy", [source])[0]
    assert actual["visibility_status"] == "candidate_warning"
    assert actual["trade_signal"] == "JETZT_TRADEN"  # presentation never rewrites execution evidence


@pytest.mark.parametrize("patch", [
    {"price": 0}, {"price": float("nan")}, {"price": -1},
    {"data_valid": False}, {"data_error_code": "invalid_bar_value"},
    {"scanner_suppression_reasons": ["scan_data_incomplete"]},
])
def test_unusable_data_does_not_become_warning_candidate(patch):
    assert api._apply_scanner_visibility_policy("stock_strategy", [row(**patch)]) == []


def test_bi_keeps_real_twenty_indicator_contract():
    from test_bi_signal_contract_downstream import _bi_row
    valid = {**_bi_row("YES"), **row(), "ticker": "YES"}
    invalid = {**_bi_row("NO", green=16), **row(), "ticker": "NO"}
    actual = api._apply_scanner_visibility_policy("bi_long", [valid, invalid])
    assert [item["ticker"] for item in actual] == ["YES"]
    assert actual[0]["visibility_status"] == "candidate_warning"


@pytest.mark.parametrize("strategy", ["Cup and Handle Breakout", "Wyckoff Spring", "Momentum Breakout Long"])
def test_legacy_or_unproven_special_pattern_not_resurrected(strategy):
    actual = api._apply_scanner_visibility_policy("stock_strategy", [row(Strategy=strategy)])
    assert actual == []


def test_stale_daily_reference_excluded_calendar_not_900_seconds():
    source = row(**stock_swing_contract.metadata("2026-09-15", 100))
    assert api._apply_scanner_visibility_policy("stock_strategy", [source]) == []


def test_nested_payload_counts_do_not_count_orb_duplicate_views():
    waiting = row()
    released = row(ticker="GOOD", trade_decision="TRADEABLE", trade_signal="JETZT_TRADEN", trade_action="LONG_NOW")
    source = {"breakouts": [released], "candidates": [waiting], "failed_breakouts": [],
              "actionable_breakouts": [released], "rejected_range_breaks": []}
    actual = api._apply_scanner_visibility_policy("orb", [source])
    counts = scanner_visibility.counts(api._visibility_instrument_rows(actual))
    assert counts == {"total": 2, "released": 1, "candidate_warning": 1, "context": 0, "warning_count": 1}


def test_confirmed_breakout_without_retest_keeps_warning_but_not_false_pending():
    source = row(trade_decision="TRADEABLE", trade_action="LONG_NOW", trade_signal="JETZT_TRADEN",
                 breakout_confirmation="confirmed_close", retest_status="not_confirmed")
    actual = api._apply_scanner_visibility_policy("stock_strategy", [source])[0]
    assert actual["visibility_status"] == "released"
    assert any(w["code"] == "breakout_confirmed_retest_pending" for w in actual["visibility_warnings"])


def test_generic_response_returns_warning_candidate_and_distinct_counts(monkeypatch, tmp_path):
    path = tmp_path / "strategy.json"
    candidates = [row(native_plan_reason="crossed_resistance_unconfirmed")]
    api.save_cache_file(str(path), candidates, metadata={
        "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
        "diagnostics": {"strategy": "Gap Momentum Long", "final_results": 1},
    })
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *_a: str(path))
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *_a: rows)
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    result = api.get_scan_results(strategy="Gap Momentum Long", market_type="stocks")
    assert result.count == 1
    assert result.data_quality["visibility_counts"]["candidate_warning"] == 1
    assert result.data_quality["visibility_counts"]["released"] == 0
    assert result.diagnostics["visible_results_after_signal_policy"] == 1
