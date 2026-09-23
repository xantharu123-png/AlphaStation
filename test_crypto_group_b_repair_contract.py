import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks

import api


@pytest.mark.parametrize("market_type", ["futures", "forex", "international"])
def test_unsupported_nonstock_markets_never_fall_back_to_stock_scanner(monkeypatch, market_type):
    called = []
    monkeypatch.setattr(api, "_run_scan_safe", lambda *_args, **_kwargs: called.append(True))

    with pytest.raises(api.HTTPException) as exc:
        api.run_scan(api.ScanRequest(strategy="anything", market_type=market_type), BackgroundTasks())

    assert exc.value.status_code == 501
    assert exc.value.detail == {
        "code": "market_scanner_not_implemented",
        "market_type": market_type,
        "scan_supported": False,
        "message": (
            f"Der {market_type}-Scanner ist noch nicht implementiert. "
            "Es wurde kein Aktien-Scanner als Ersatz gestartet."
        ),
    }
    assert called == []


def test_nonstock_catalog_and_result_route_publish_unsupported_capability():
    response = api.list_strategies("futures")
    assert response.count > 0
    assert all(row["scan_supported"] is False for row in response.strategies.values())
    assert all(
        row["unsupported_reason"] == "market_scanner_not_implemented"
        for row in response.strategies.values()
    )
    with pytest.raises(api.HTTPException) as exc:
        api.get_scan_results(strategy="Futures Momentum ", market_type="futures")
    assert exc.value.status_code == 501


def _valid_short_payload():
    micro_closed_at = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    setup = {
        "direction": "SHORT",
        "entry": 1.0,
        "stop": 1.08,
        "tp1": 0.82,
        "tp2": 0.72,
        "target_quality": "STRUCTURAL_TP1_PROJECTION_TP2",
        "tp1_is_projection": False,
        "tp2_is_projection": True,
        "structure_status": "ACCEPT",
    }
    pump = {
        "current_price": 1.0,
        "pump_pct": 45.0,
        "from_ath_pct": 12.0,
        "micro_trigger_ok": True,
        "micro_score": 88,
        "micro_data_age_seconds": 45,
        "micro_candle_closed_at": micro_closed_at,
    }
    signal = {
        **setup,
        "symbol": "TESTUSDT",
        "stop_loss": 1.08,
        "rr_effective": 2.25,
        "risk_pct": 8.0,
        "exh_score": 90,
        "timing_quality": 90,
        "grade": "S",
        "safety_ok": True,
        "confirmation_ok": True,
        "btc_context_ok": True,
        "continuation_risk": False,
        "tp1_missed": False,
        "tp2_missed": False,
        "micro_required": True,
        "micro_trigger_ok": True,
        "micro_data_age_seconds": 45,
        "micro_candle_closed_at": micro_closed_at,
        "listing_trade_ok": True,
        "trade_category": "NEW_LISTING_DUMP",
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "SHORT_NOW",
        "trade_setup": setup,
        "pump_data": pump,
    }
    return {
        "signals": [{"symbol": "TESTUSDT", "exchange": "bybit", "signal": signal}],
        "watchlist": [],
        "monitoring": [],
    }


def test_new_listing_versioned_cache_is_fresh_now_and_stale_watch(monkeypatch):
    flat = api._flatten_new_listing_pipeline_results(_valid_short_payload())
    assert flat[0]["new_listing_short_cache_version"] == api._NEW_LISTING_SHORT_CACHE_VERSION
    assert flat[0]["source_trade_contract_validated"] is True
    assert flat[0]["micro_data_age_seconds"] == 45
    assert flat[0]["micro_candle_closed_at"]

    fresh = api._downgrade_expired_new_listing_triggers(flat, 30)
    assert api._normalize_crypto_short_signal(fresh[0])["trade_action"] == "JETZT_SHORT"
    stale = api._downgrade_expired_new_listing_triggers(flat, 7200)
    assert stale[0]["trade_action"] == "SHORT_WATCH"
    assert stale[0]["trigger_expiry_reason"] == "new_listing_micro_trigger_expired"

    cached_at = datetime.now().isoformat()
    monkeypatch.setattr(
        api,
        "load_cache_file",
        lambda path, *_a, **_k: (flat, cached_at) if path == api.NEW_LISTING_CACHE else ([], cached_at),
    )
    monkeypatch.setattr(api, "_scan_cache_payload", lambda _path: {})
    rows, stats, *_rest = api._build_crypto_trade_signals_from_caches()
    assert rows[0]["trade_action"] == "JETZT_SHORT"
    assert stats["short_trade_now_count"] == 1


def _new_listing_routes(monkeypatch, *, close_age_seconds, cached_at):
    payload = _valid_short_payload()
    signal = payload["signals"][0]["signal"]
    closed_at = (datetime.now(timezone.utc) - timedelta(seconds=close_age_seconds)).isoformat()
    signal["micro_candle_closed_at"] = closed_at
    signal["pump_data"]["micro_candle_closed_at"] = closed_at
    flat = api._flatten_new_listing_pipeline_results(payload)
    monkeypatch.setattr(
        api,
        "load_cache_file",
        lambda path, *_args, **_kwargs: (
            (copy.deepcopy(flat), cached_at)
            if path == api.NEW_LISTING_CACHE
            else ([], None)
        ),
    )
    monkeypatch.setattr(api, "_scan_cache_payload", lambda _path: {})
    return api.get_new_listing_results(), api._build_crypto_trade_signals_from_caches()


@pytest.mark.parametrize(
    "cached_at",
    [None, "invalid", (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()],
)
def test_real_micro_close_age_blocks_old_short_with_missing_invalid_or_aware_cache_time(
    monkeypatch, cached_at
):
    direct, combined = _new_listing_routes(
        monkeypatch,
        close_age_seconds=24 * 3600,
        cached_at=cached_at,
    )
    assert direct["data"][0]["trade_action"] == "WAIT_FOR_TRIGGER"
    assert direct["data"][0]["alertable_crypto"] is False
    assert combined[0][0]["trade_action"] == "SHORT_WATCH"


def test_fresh_aware_cache_and_real_close_time_remain_tradeable(monkeypatch):
    direct, combined = _new_listing_routes(
        monkeypatch,
        close_age_seconds=45,
        cached_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    assert direct["data"][0]["trade_action"] == "SHORT_NOW"
    assert combined[0][0]["trade_action"] == "JETZT_SHORT"


def test_exact_zero_numeric_age_is_not_replaced_by_default():
    assert api._crypto_trade_to_float(0, -1) == 0
    row = api._flatten_new_listing_pipeline_results(_valid_short_payload())[0]
    row["micro_data_age_seconds"] = 0
    row.pop("micro_candle_closed_at", None)
    fresh = api._downgrade_expired_new_listing_triggers([row], 0)[0]
    assert fresh["effective_micro_data_age_seconds"] == 0
    assert api._normalize_crypto_short_signal(fresh)["trade_action"] == "JETZT_SHORT"


def test_legacy_or_corrupt_short_cache_fails_closed():
    row = {
        "symbol": "LEGACY",
        "direction": "SHORT",
        "trade_action": "SHORT_NOW",
        "trade_category": "NEW_LISTING_DUMP",
        "micro_trigger_ok": True,
        "micro_data_age_seconds": 30,
        "safety_ok": True,
        "listing_trade_ok": True,
        "rr_effective": 0.2,
        "risk_pct": 80.0,
        "confirmation_ok": False,
        "continuation_risk": True,
        "tp1_missed": True,
        "price": 1.0,
    }
    assert api._normalize_crypto_short_signal(row)["trade_action"] == "SHORT_WATCH"


def _long_row(**overrides):
    row = {
        "Symbol": "LONG",
        "trade_signal": "JETZT_TRADEN",
        "execution_trigger_ok": True,
        "execution_data_age_seconds": 30,
        "alertable_crypto": True,
        "risk_level": "LOW",
        "spread_execution_ok": True,
        "funding_rate": 0.01,
        "funding_rate_unit": "percent",
        "funding_interval_hours": 8,
        "explosion_score": 85,
        "entry_score": 85,
        "entry": 10.0,
        "stop": 9.5,
        "tp1": 11.0,
        "tp2": 12.0,
        "risk_reward": 2.0,
        "target_quality": "STRUCTURAL_TP1_PROJECTION_TP2",
        "tp1_is_projection": False,
        "structure_status": "ACCEPT",
    }
    row.update(overrides)
    return row


def test_high_risk_or_crowded_funding_cannot_normalize_to_trade_now():
    safe = api._normalize_crypto_long_signal(_long_row())
    crowded = api._normalize_crypto_long_signal(
        _long_row(alertable_crypto=False, risk_level="HIGH", funding_rate=0.09)
    )
    assert safe["trade_action"] == "JETZT_LONG"
    assert crowded["trade_action"] == "LONG_ARMED"


def test_venue_selection_is_risk_first_and_preserves_complete_alternative_plan():
    risky = _long_row(
        Symbol="VENUE", exchange="binance", contract="VENUEUSDT",
        entry=10.0, stop=9.0, tp1=13.0, tp2=15.0,
        explosion_score=99, entry_score=99, risk_reward=5.0,
        alertable_crypto=False, risk_level="HIGH", funding_rate=0.09,
    )
    safe = _long_row(
        Symbol="VENUE", exchange="bybit", contract="VENUEUSDT",
        entry=20.0, stop=19.0, tp1=22.0, tp2=23.0,
        explosion_score=80, entry_score=80, risk_reward=2.0,
    )
    selected = api._merge_crypto_trade_signals([risky, safe], [])[0]
    assert (selected["exchange"], selected["entry"], selected["stop"]) == ("bybit", 20.0, 19.0)
    alternative = selected["venue_alternatives"][0]
    assert (alternative["exchange"], alternative["entry"], alternative["stop"]) == ("binance", 10.0, 9.0)
    assert selected["venue_selection"]["policy"] == "risk_then_action_then_score_rr"


def test_unknown_risk_watch_cannot_displace_valid_medium_risk_trade_now():
    payload = _valid_short_payload()
    entry = payload["signals"].pop()
    entry["signal"].update(safety_ok=False, risk_pct=50.0, continuation_risk=True)
    payload["watchlist"].append(entry)
    short = api._flatten_new_listing_pipeline_results(payload)
    long = _long_row(
        Symbol="TEST",
        exchange="binance",
        contract="TESTUSDT",
        risk_level="MEDIUM",
    )

    selected = api._merge_crypto_trade_signals([long], short)[0]

    assert selected["trade_action"] == "JETZT_LONG"
    assert selected["venue_selection"]["selected_risk_rank"] == 1
    assert selected["venue_alternatives"][0]["trade_action"] == "SHORT_WATCH"
    assert selected["venue_alternatives"][0]["risk_rank"] == 3


def test_missing_risk_without_explicit_safety_failure_stays_unknown_rank_two():
    row = api._flatten_new_listing_pipeline_results(_valid_short_payload())[0]
    normalized = api._normalize_crypto_short_signal(row)
    assert normalized.get("risk_level") is None
    assert api._crypto_trade_execution_risk_rank(normalized) == 2


def test_explicitly_unsafe_low_risk_short_cannot_displace_valid_medium_long():
    payload = _valid_short_payload()
    entry = payload["signals"].pop()
    entry["signal"].update(
        safety_ok=False,
        continuation_risk=True,
        risk_level="LOW",
    )
    payload["watchlist"].append(entry)
    short = api._flatten_new_listing_pipeline_results(payload)
    short[0]["risk_level"] = "LOW"
    long = _long_row(
        Symbol="TEST",
        exchange="binance",
        contract="TESTUSDT",
        risk_level="MEDIUM",
    )

    selected = api._merge_crypto_trade_signals([long], short)[0]

    assert selected["trade_action"] == "JETZT_LONG"
    assert selected["venue_selection"]["selected_risk_rank"] == 1
    assert selected["venue_alternatives"][0]["risk_level"] == "LOW"
    assert selected["venue_alternatives"][0]["risk_rank"] == 3


def test_btc_divergence_preserves_partial_source_provenance(monkeypatch):
    coins = [
        {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 100000,
         "market_cap": 2_000_000_000_000, "total_volume": 40_000_000_000,
         "price_change_percentage_24h": 0.0, "price_change_percentage_7d_in_currency": 0.0},
        {"id": "test", "symbol": "test", "name": "Test", "current_price": 10,
         "market_cap": 100_000_000, "total_volume": 10_000_000,
         "price_change_percentage_24h": 15.0, "price_change_percentage_7d_in_currency": 30.0},
    ]

    monkeypatch.setattr(
        api,
        "_CG_MARKETS_STATUS",
        {"source": "stale_cache", "partial": True, "warning": "partial-source"},
    )
    def fetch(**_kwargs):
        return coins

    monkeypatch.setattr(api, "_fetch_coingecko_markets", fetch)
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})
    row = api._build_crypto_btc_divergence_results()[0]
    assert row["source_status"] == {
        "source": "stale_cache",
        "partial": True,
        "warning": "partial-source",
    }
    assert row["partial_data"] is True
