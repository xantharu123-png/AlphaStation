from pathlib import Path

import api


ROOT = Path(__file__).resolve().parent


def test_early_mover_signal_only_shows_scored_candidates_and_confirmed_trades():
    shared_trade_plan = {
        "direction": "LONG",
        "risk_level": "LOW",
        "risk_flags": [],
        "live_rr_ratio": 2.2,
        "distance_to_entry_r": 0.1,
        "btc_context": {"tailwind": True},
    }
    payload = {
        "coins": [
            {
                **shared_trade_plan,
                "Symbol": "WAIT",
                "trade_signal": "WARTEN",
                "trade_action": "WAIT_FOR_RETEST",
                "grade": "S",
                "score": 98,
                "setup_score": 98,
                "entry_score": 74,
            },
            {
                **shared_trade_plan,
                "Symbol": "GO",
                "trade_signal": "JETZT_TRADEN",
                "trade_action": "LONG_TRIGGER",
                "grade": "S",
                "score": 88,
                "setup_score": 88,
                "entry_score": 88,
                "execution_quality_score": 92,
                "alertable_crypto": True,
            },
        ],
        "stats": {"unified_count": 2},
    }

    filtered = api._apply_signal_only_policy("early_movers", [payload])

    assert [row["Symbol"] for row in filtered[0]["coins"]] == ["GO", "WAIT"]
    assert filtered[0]["stats"]["suppressed_watch_rows"] == 0
    assert filtered[0]["stats"]["visible_candidates"] == 2
    assert filtered[0]["stats"]["trade_now_count"] == 1


def test_stock_signal_only_requires_tradeable_and_top_grade():
    rows = [
        {"ticker": "LOWGRADE", "trade_decision": "TRADEABLE", "trade_health": {"decision": "TRADEABLE"}, "grade": "C", "score": 95},
        {"ticker": "SIGNAL", "trade_decision": "TRADEABLE", "trade_health": {"decision": "TRADEABLE"}, "grade": "A", "score": 84},
        {"ticker": "WAIT", "trade_decision": "WAIT_FOR_RETEST", "trade_health": {"decision": "WAIT_FOR_RETEST"}, "grade": "S", "score": 91},
    ]

    filtered = api._apply_signal_only_policy("strategy_scan", rows)

    assert [row["ticker"] for row in filtered] == ["SIGNAL"]


def test_stock_strategy_swing_results_use_daily_state_not_5m(monkeypatch, tmp_path):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"LATE", "RUNR"}, "unit"))

    base_row = {
        "ticker": "LATE",
        "grade": "A",
        "score": 96,
        "rvol": 2.8,
        "price": 24.5,
        "current_price": 24.5,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "change_pct": 18.0,
        "close_pos": 0.91,
        "open_to_current_pct": 8.5,
        "Extension_ATR": 4.5,
        "DayHigh": 25.0,
        "DayLow": 22.5,
        "Entry": 24.5,
        "StopLoss": 23.6,
        "TP1": 25.9,
        "TP2": 26.8,
        "dollar_volume": 12_000_000,
    }
    confirmed_row = {
        **base_row,
        "ticker": "RUNR",
        "change_pct": 5.2,
        "Change_Pct": 5.2,
        "open_to_current_pct": 2.1,
        "Extension_ATR": 1.6,
        "vol_confirmed": True,
        "vwap_aligned": True,
    }

    decorated = api._decorate_scan_results([base_row, confirmed_row], "stock_strategy", 10)
    rows_by_ticker = {row["ticker"]: row for row in decorated}

    assert rows_by_ticker["LATE"]["raw_score"] == 96
    assert rows_by_ticker["LATE"]["score"] < 80
    assert rows_by_ticker["LATE"]["trade_signal"] == "WARTEN"
    assert "swing_extended_wait_retest" in rows_by_ticker["LATE"]["scanner_suppression_reasons"]
    assert "fresh_5m_state_missing_wait_trigger" not in rows_by_ticker["LATE"]["scanner_suppression_reasons"]
    assert rows_by_ticker["RUNR"]["raw_score"] == 96
    assert rows_by_ticker["RUNR"]["score"] >= 80
    assert rows_by_ticker["RUNR"]["trade_signal"] == "JETZT_TRADEN"
    assert "fresh_5m_state_missing_wait_trigger" not in rows_by_ticker["RUNR"]["scanner_suppression_reasons"]

    filtered = api._apply_signal_only_policy("stock_strategy", decorated)

    assert [row["ticker"] for row in filtered] == ["RUNR"]


def test_stock_signal_scanners_default_to_swing_without_fresh_5m_gate(monkeypatch, tmp_path):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"RAW"}, "unit"))
    row = {
        "ticker": "RAW",
        "grade": "S",
        "score": 98,
        "rvol": 2.4,
        "price": 20.0,
        "current_price": 20.0,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "change_pct": 5.0,
        "Change_Pct": 5.0,
        "close_pos": 0.88,
        "open_to_current_pct": 2.0,
        "Extension_ATR": 1.5,
        "DayHigh": 20.4,
        "DayLow": 18.5,
        "Entry": 20.0,
        "StopLoss": 19.25,
        "TP1": 21.2,
        "TP2": 22.0,
        "dollar_volume": 10_000_000,
    }

    for scanner_name in ("turtle", "bi_long", "biotech"):
        decorated = api._decorate_scan_results([row], scanner_name, 10)

        assert decorated[0]["raw_score"] == 98
        assert decorated[0]["score"] >= 80
        assert decorated[0]["trade_signal"] == "JETZT_TRADEN"
        assert "fresh_5m_state_missing_wait_trigger" not in decorated[0]["scanner_suppression_reasons"]
        assert [r["ticker"] for r in api._apply_signal_only_policy(scanner_name, decorated)] == ["RAW"]


def test_biotech_watchlist_rows_do_not_survive_signal_filter(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"CRSP"}, "unit"))
    row = {
        "ticker": "CRSP",
        "grade": "S",
        "score": 81,
        "price": 57.67,
        "current_price": 57.67,
        "rvol": 1.2,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "Entry": 57.67,
        "StopLoss": 55.90,
        "TP1": 61.20,
        "TP2": 64.10,
        "bio_trade_mode": "WATCHLIST",
        "bio_risk_flags": [
            "news_catalyst_without_calendar_confirmation",
            "sell_the_news_risk_extended_chart",
        ],
    }

    decorated = api._decorate_scan_results([row], "biotech", 10)

    assert decorated[0]["score"] >= 80
    assert api._apply_signal_only_policy("biotech", decorated) == []


def test_volume_spikes_are_display_radar_not_signal_only(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"VOLX"}, "unit"))
    row = {
        "ticker": "VOLX",
        "price": 12.34,
        "change_pct": 1.2,
        "volume": 1_200_000,
        "rvol": 4.2,
        "dollar_volume": 14_800_000,
        "signal_type": "ABSORPTION",
        "asset_class": "stock",
        "trade_signal": "BEOBACHTEN",
        "trade_action": "BEOBACHTEN",
        "execution_trigger_ok": False,
    }

    decorated = api._decorate_scan_results([row], "volume_spikes", 10)
    visible = api._apply_signal_only_policy("volume_spikes", decorated)

    assert "volume_spikes" not in api._SIGNAL_ONLY_SCANNERS
    assert "volume_spikes" not in api._ALERT_TRADE_PLAN_GUARD_SCANNERS
    assert "volume_spikes" not in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS
    assert "volume_spikes" not in api._LONG_ENTRY_ALERT_SCANNERS
    assert "volume_spikes" not in api._STOCK_ALERT_SCANNERS
    assert "volume_spikes" not in api._STOCK_RESULT_TRADE_STATE_SCANNERS
    assert "volume_spikes" in api.STOCK_SCANNER_ASSET_GUARD_NAMES
    assert visible == decorated
    assert visible[0]["trade_signal"] == "BEOBACHTEN"
    assert visible[0]["trade_action"] == "BEOBACHTEN"
    assert visible[0]["execution_trigger_ok"] is False
    assert "scanner_decision" not in visible[0]


def test_scanner_signal_vs_radar_contract():
    """Scanner categories must not silently turn radar/context rows into trades."""
    trade_signal_scanners = {
        "bear", "bi_short", "bi_long", "biotech", "orb", "turtle",
        "stock_strategy", "strategy_scan",
        "early_movers", "crypto_trade_signals", "crypto_explosion",
        "new_listing", "btc_divergenz", "crypto_strategy",
    }
    radar_scanners = {"volume_spikes", "money_flow", "crash_monitor"}

    assert trade_signal_scanners <= api._SIGNAL_ONLY_SCANNERS
    assert radar_scanners.isdisjoint(api._SIGNAL_ONLY_SCANNERS)
    assert radar_scanners.isdisjoint(api._STOCK_RESULT_TRADE_STATE_SCANNERS)
    assert radar_scanners.isdisjoint(api._STOCK_ALERT_SCANNERS)


def test_scanner_cache_and_scheduler_contracts():
    """Health/cache wiring must point at the scanner that actually writes data."""
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    bg_source = (ROOT / "bg_service.py").read_text(encoding="utf-8")
    scanner_names = (
        set(api._scan_status)
        | set(api.SCAN_CACHE_MAP)
        | set(api._SIGNAL_ONLY_SCANNERS)
        | set(api._STOCK_RESULT_TRADE_STATE_SCANNERS)
        | set(api._ALERT_TRADE_HEALTH_GUARD_SCANNERS)
        | set(api.STOCK_SCANNER_ASSET_GUARD_NAMES)
    )

    assert api.SCAN_CACHE_MAP["crypto_trade_signals"] == api.CRYPTO_TRADE_SIGNALS_CACHE
    assert api.SCAN_CACHE_MAP["crypto_trade_signals"] != api.CRYPTO_EXPLOSION_CACHE
    assert scanner_names <= set(api.SCAN_DATA_SOURCES)
    assert set(api._scan_status) <= set(api.SCAN_CACHE_MAP)
    assert '("crypto_trade_signals", lambda: _crypto_trade_signals_wrapper(refresh_sources=False))' in api_source
    assert "/tmp/alpha_biotech_cache.json" in bg_source
    assert "/tmp/strategy_scan_cache.json" in bg_source
    assert "/tmp/biotech_scan_results.json" not in bg_source
    assert "/tmp/strategy_scan_results.json" not in bg_source


def test_crypto_trade_signals_scheduler_merge_does_not_rerun_source_scans(monkeypatch):
    calls = []
    saved = {}
    monkeypatch.setattr(api, "_crypto_explosion_wrapper", lambda: calls.append("long"))
    monkeypatch.setattr(api, "_new_listing_wrapper", lambda: calls.append("short"))
    monkeypatch.setattr(
        api,
        "_build_crypto_trade_signals_from_caches",
        lambda: ([{"Symbol": "MERGE"}], {"result_count": 1}, "cached", 0, []),
    )
    monkeypatch.setattr(
        api,
        "save_cache_file",
        lambda path, rows, metadata=None: saved.update({"path": path, "rows": rows}),
    )

    api._crypto_trade_signals_wrapper(refresh_sources=False)

    assert calls == []
    assert saved == {"path": api.CRYPTO_TRADE_SIGNALS_CACHE, "rows": [{"Symbol": "MERGE"}]}


def test_generic_crypto_strategy_results_use_crypto_strategy_policy(monkeypatch, tmp_path):
    cache_path = tmp_path / "crypto_breakout_cache.json"
    api.save_cache_file(str(cache_path), [{
        "Ticker": "COINX",
        "Symbol": "COINX",
        "grade": "A",
        "score": 88,
        "Price": 1.23,
        "current_price": 1.23,
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "LONG_TRIGGER",
        "execution_trigger_ok": True,
        "signal_quality": "tradeable",
        "entry": 1.23,
        "stop_loss": 1.15,
        "tp1": 1.42,
        "tp2": 1.55,
    }])
    monkeypatch.setattr(api, "_strategy_cache_path", lambda strategy, market_type="stocks": str(cache_path))

    response = api.get_scan_results(strategy="Breakout Long", market_type="crypto")

    assert response.data_quality["scanner"] == "crypto_strategy"
    assert response.data_source == api.SCAN_DATA_SOURCES["crypto_strategy"]


def test_stock_swing_strategy_scanners_do_not_cap_for_missing_5m(monkeypatch, tmp_path):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"RAW"}, "unit"))
    row = {
        "ticker": "RAW",
        "grade": "S",
        "score": 92,
        "rvol": 2.0,
        "price": 20.0,
        "current_price": 20.0,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "change_pct": 5.0,
        "Change_Pct": 5.0,
        "close_pos": 0.74,
        "open_to_current_pct": 2.0,
        "Extension_ATR": 1.5,
        "DayHigh": 20.4,
        "DayLow": 18.5,
        "Entry": 20.0,
        "StopLoss": 19.25,
        "TP1": 21.2,
        "TP2": 22.0,
        "dollar_volume": 10_000_000,
    }

    for scanner_name in ("stock_strategy", "strategy_scan"):
        decorated = api._decorate_scan_results([row], scanner_name, 10)

        assert decorated[0]["raw_score"] == 92
        assert decorated[0]["score"] >= 80
        assert decorated[0]["trade_signal"] == "JETZT_TRADEN"
        assert "fresh_5m_state_missing_wait_trigger" not in decorated[0]["scanner_suppression_reasons"]
        assert [r["ticker"] for r in api._apply_signal_only_policy(scanner_name, decorated)] == ["RAW"]


def test_new_listing_watch_email_is_disabled(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: sent.append(args) or True)

    result = api._send_new_listing_watch_email({
        "watchlist": [
            {
                "symbol": "WATCHUSDT",
                "exchange": "mexc",
                "signal": {"pump_data": {"pump_pct": 40, "from_ath_pct": 10}, "grade": "S", "rr_effective": 2.0},
            }
        ]
    })

    assert result is False
    assert sent == []


def test_manual_watchlist_is_not_exposed_in_navigation():
    frontend = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "id: 'watchlist'" not in frontend
    assert "activeTab === 'watchlist'" not in frontend
