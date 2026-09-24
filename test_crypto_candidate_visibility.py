"""Display-only crypto candidates never reopen execution/mail permission."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

import api


def _early(**changes):
    return {
        "Symbol": "CAND", "Price": 10.0, "direction": "LONG",
        "setup_score": 88, "score": 88, "explosion_score": 86,
        "entry_score": 78, "grade": "S", "risk_level": "LOW",
        "trade_action": "NO_TRADE", "trade_signal": "NICHT_TRADEN",
        "trade_decision": "NO_TRADE", "signal_quality": "no_trade",
        "execution_trigger_ok": False, "alertable_crypto": False,
        "live_rr_ratio": 2.2, "distance_to_entry_r": 0.1,
        "btc_context": {"tailwind": True, "btc_24h": 0.2, "btc_7d": 1.0},
        "native_plan_status": "unavailable", "native_plan_reason": "no_structural_invalidation",
        "risk_flags": ["trade_health_no_trade"], **changes,
    }


@pytest.mark.parametrize("changes", [
    {}, {"live_rr_ratio": 0.4},
    {"barrier_gate_active": True, "native_plan_reason": "near_structural_barrier"},
    {"risk_level": "HIGH", "risk_flags": ["thin_orderbook", "market_impact_risk"]},
    {"distance_to_entry_r": 1.8, "risk_flags": ["chased_from_entry"]},
    {"trade_action": "WAIT_FOR_TRIGGER", "trade_signal": "WARTEN", "trade_decision": "WAIT_FOR_TRIGGER"},
])
def test_early_execution_warning_retained_without_reopening_canonical_gate(changes):
    source = _early(**changes)
    before = deepcopy(source)
    assert not api._early_mover_visible_candidate(source)
    visible = api._apply_scanner_visibility_policy("early_movers", [source])
    assert len(visible) == 1
    assert visible[0]["visibility_status"] == "candidate_warning"
    assert visible[0]["visibility_is_trade_signal"] is False
    assert visible[0]["visibility_warnings"]
    for key, value in before.items():
        assert visible[0][key] == value
    assert source == before
    assert not api._classify_alert_candidate("early_movers", source, 1_000_000)["alertable_now"]


@pytest.mark.parametrize("changes", [
    {"score": 30, "setup_score": 30, "explosion_score": 30, "grade": "C"},
    {"partial_data": True}, {"data_valid": False},
    {"risk_flags": ["partial_crypto_data"]}, {"btc_context": {}},
    {"trade_action": "BEOBACHTEN", "trade_signal": "BEOBACHTEN"},
    {"Price": float("nan")}, {"Symbol": ""},
])
def test_early_primary_quality_and_data_guards_remain(changes):
    assert api._apply_scanner_visibility_policy("early_movers", [_early(**changes)]) == []


def test_early_final_decoration_does_not_hide_blocked_candidate():
    source = _early(trade_action="LONG_NOW", trade_signal="JETZT_TRADEN",
                    trade_health={"decision": "NO_TRADE", "decision_label": "Kein Plan"})
    api._apply_trade_health_final_signal(source, "early_movers")
    assert source["trade_action"] == "NO_TRADE"
    visible = api._apply_scanner_visibility_policy("early_movers", [{"coins": [source], "stats": {}}])
    assert len(visible[0]["coins"]) == 1
    assert visible[0]["stats"]["visibility_counts"]["candidate_warning"] == 1
    assert api._apply_signal_only_policy("early_movers", [{"coins": [source]}])[0]["coins"] == []


@pytest.mark.parametrize("data_patch, expected_count", [
    ({}, 1), ({"partial_data": True}, 0), ({"data_valid": False}, 0),
    ({"data_warning": "missing_candles"}, 0),
    ({"risk_flags": ["partial_crypto_data"]}, 0),
])
def test_early_existing_confirmed_execution_alternative_remains_visible(data_patch, expected_count):
    source = _early(
        trade_action="LONG_NOW", trade_signal="JETZT_TRADEN", trade_decision="TRADEABLE",
        signal_quality="tradeable", entry_score=24, explosion_score=34,
        execution_quality_score=92, execution_trigger_ok=True, alertable_crypto=True,
        native_plan_status="valid", native_plan_reason="native_structure_plan", risk_flags=[],
    )
    assert api._early_mover_visible_candidate(source)
    source.update(data_patch)
    before = deepcopy(source)
    visible = api._apply_scanner_visibility_policy("early_movers", [source])
    assert len(visible) == expected_count
    if visible:
        assert visible[0]["visibility_status"] == "released"
        assert visible[0]["visibility_is_trade_signal"] is True
    assert source == before


def _cache_sources(monkeypatch, rows, *, age=30):
    cached_at = (datetime.now() - timedelta(seconds=age)).isoformat()
    monkeypatch.setattr(api, "load_cache_file", lambda path: (
        deepcopy(rows) if path == api.CRYPTO_EXPLOSION_CACHE else [], cached_at))
    monkeypatch.setattr(api, "_scan_cache_payload", lambda _path: {})
    monkeypatch.setattr(api, "_decorate_scan_results", lambda values, *_a: deepcopy(values))
    monkeypatch.setattr(api, "_decorate_new_listing_display_results", lambda values, *_a: (values, {}))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    return cached_at


@pytest.mark.parametrize("state,signal", [("NO_TRADE", "NICHT_TRADEN"), ("WAIT_FOR_TRIGGER", "WARTEN")])
def test_combined_get_retains_warning_cache_candidate_but_default_builder_does_not(monkeypatch, state, signal):
    source = _early(trade_action=state, trade_signal=signal, trade_decision=state,
                    exchange="bybit", contract="CANDUSDT", execution_data_age_seconds=120,
                    source_market_data_at="2026-09-24T11:00:00Z")
    before = deepcopy(source)
    cached_at = _cache_sources(monkeypatch, [source])
    assert api._build_crypto_trade_signals_from_caches()[0] == []
    assert api._normalize_crypto_long_signal(source) is None
    response = api.get_crypto_trade_signals_results()
    assert response["cached_at"] == cached_at
    assert response["stats"]["trade_now_count"] == 0
    assert response["data_quality"]["visibility_counts"]["candidate_warning"] == 1
    actual = response["data"][0]
    for key in ("trade_action", "trade_signal", "trade_decision", "alertable_crypto",
                "execution_trigger_ok", "native_plan_reason", "source_market_data_at",
                "execution_data_age_seconds", "exchange", "contract"):
        assert actual[key] == source[key]
    assert actual["visibility_status"] == "candidate_warning"
    assert actual["scanner_source"] == "crypto_explosion"
    assert actual["visibility_warnings"]
    assert not actual["visibility_is_trade_signal"]
    assert source == before


@pytest.mark.parametrize("changes", [
    {"explosion_score": 10}, {"explosion_score": float("nan")},
    {"Price": 0}, {"Price": float("inf")}, {"partial_data": True},
    {"data_status": "stale"}, {"risk_flags": ["invalid_ohlcv"]},
])
def test_combined_display_rejects_unusable_or_weak_source(changes):
    assert api._normalize_crypto_long_signal(_early(**changes), display_only=True) is None


def test_combined_display_never_fabricates_permission_fields():
    source = _early()
    source.pop("alertable_crypto")
    source.pop("execution_trigger_ok")
    actual = api._normalize_crypto_long_signal(source, display_only=True)
    assert "alertable_crypto" not in actual
    assert "execution_trigger_ok" not in actual
    assert "decision" not in actual
    assert actual["trade_action"] == "NO_TRADE"


def test_combined_display_preserves_dedupe_and_separate_venue_plans():
    rows = [
        _early(exchange="bybit", contract="CANDUSDT", entry=10, stop=9),
        _early(exchange="binance", contract="CANDUSDT", entry=11, stop=8, risk_level="HIGH"),
    ]
    before = deepcopy(rows)
    actual = api._merge_crypto_trade_signals(rows, [], display_only=True)
    assert len(actual) == 1
    assert actual[0]["exchange"] == "bybit"
    assert actual[0]["entry"] == 10
    assert actual[0]["stop"] == 9
    assert actual[0]["venue_alternatives"][0]["entry"] == 11
    assert actual[0]["venue_alternatives"][0]["stop"] == 8
    assert rows == before


def test_combined_display_keeps_existing_expired_trigger_downgrade(monkeypatch):
    source = _early(trade_action="LONG_NOW", trade_signal="JETZT_TRADEN", trade_decision="TRADEABLE",
                    execution_trigger_ok=True, alertable_crypto=True, execution_data_age_seconds=60,
                    native_plan_status="valid", native_plan_reason="native_structure_plan")
    _cache_sources(monkeypatch, [source], age=7200)
    response = api.get_crypto_trade_signals_results()
    actual = response["data"][0]
    assert actual["trigger_expired"] is True
    assert actual["alertable_crypto"] is False
    assert actual["execution_trigger_ok"] is False
    assert actual["trade_action"] not in {"LONG_NOW", "JETZT_LONG"}
    assert actual["visibility_status"] == "candidate_warning"
    assert response["stats"]["trade_now_count"] == 0


def test_combined_cache_wrapper_does_not_enable_display_candidates(monkeypatch):
    _cache_sources(monkeypatch, [_early()])
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.append(deepcopy(rows)))
    api._crypto_trade_signals_wrapper(refresh_sources=False)
    assert saved == [[]]
