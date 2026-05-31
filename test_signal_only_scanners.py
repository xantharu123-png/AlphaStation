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


def test_stock_scanner_results_use_effective_trade_state(monkeypatch, tmp_path):
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
        "latest_bar_change_pct": 0.35,
        "latest_bar_close_pos": 0.82,
        "vol_confirmed": True,
        "vwap_aligned": True,
    }

    decorated = api._decorate_scan_results([base_row, confirmed_row], "stock_strategy", 10)
    rows_by_ticker = {row["ticker"]: row for row in decorated}

    assert rows_by_ticker["LATE"]["raw_score"] == 96
    assert rows_by_ticker["LATE"]["score"] < 80
    assert rows_by_ticker["LATE"]["trade_signal"] == "WARTEN"
    assert "fresh_5m_state_missing_wait_trigger" in rows_by_ticker["LATE"]["scanner_suppression_reasons"]
    assert rows_by_ticker["RUNR"]["raw_score"] == 96
    assert rows_by_ticker["RUNR"]["score"] >= 80
    assert rows_by_ticker["RUNR"]["trade_signal"] == "JETZT_TRADEN"

    filtered = api._apply_signal_only_policy("stock_strategy", decorated)

    assert [row["ticker"] for row in filtered] == ["RUNR"]


def test_all_stock_signal_scanners_cap_raw_score_without_fresh_trigger(monkeypatch, tmp_path):
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
        "change_pct": 14.0,
        "Change_Pct": 14.0,
        "close_pos": 0.88,
        "open_to_current_pct": 5.0,
        "DayHigh": 20.4,
        "DayLow": 18.5,
        "Entry": 20.0,
        "StopLoss": 19.25,
        "TP1": 21.2,
        "TP2": 22.0,
        "dollar_volume": 10_000_000,
    }

    for scanner_name in ("stock_strategy", "strategy_scan", "turtle", "volume_spikes", "bi_long", "biotech"):
        decorated = api._decorate_scan_results([row], scanner_name, 10)

        assert decorated[0]["raw_score"] == 98
        assert decorated[0]["score"] < 80
        assert decorated[0]["trade_signal"] != "JETZT_TRADEN"
        assert "fresh_5m_state_missing_wait_trigger" in decorated[0]["scanner_suppression_reasons"]
        assert api._apply_signal_only_policy(scanner_name, decorated) == []


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
