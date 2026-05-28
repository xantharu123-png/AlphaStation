from pathlib import Path

import api


ROOT = Path(__file__).resolve().parent


def test_early_mover_signal_only_hides_wait_rows():
    payload = {
        "coins": [
            {
                "Symbol": "WAIT",
                "trade_signal": "WARTEN",
                "trade_action": "WAIT_FOR_RETEST",
                "grade": "S",
                "score": 98,
            },
            {
                "Symbol": "GO",
                "trade_signal": "JETZT_TRADEN",
                "trade_action": "LONG_TRIGGER",
                "grade": "S",
                "score": 88,
                "alertable_crypto": True,
            },
        ],
        "stats": {"unified_count": 2},
    }

    filtered = api._apply_signal_only_policy("early_movers", [payload])

    assert [row["Symbol"] for row in filtered[0]["coins"]] == ["GO"]
    assert filtered[0]["stats"]["suppressed_watch_rows"] == 1
    assert filtered[0]["stats"]["visible_trade_signals"] == 1


def test_stock_signal_only_requires_tradeable_and_top_grade():
    rows = [
        {"ticker": "LOWGRADE", "trade_decision": "TRADEABLE", "trade_health": {"decision": "TRADEABLE"}, "grade": "C", "score": 95},
        {"ticker": "SIGNAL", "trade_decision": "TRADEABLE", "trade_health": {"decision": "TRADEABLE"}, "grade": "A", "score": 84},
        {"ticker": "WAIT", "trade_decision": "WAIT_FOR_RETEST", "trade_health": {"decision": "WAIT_FOR_RETEST"}, "grade": "S", "score": 91},
    ]

    filtered = api._apply_signal_only_policy("strategy_scan", rows)

    assert [row["ticker"] for row in filtered] == ["SIGNAL"]


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
