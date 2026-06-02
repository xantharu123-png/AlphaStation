import api


def test_crypto_trade_signals_prefers_confirmed_short_over_long_watch():
    long_rows = [{
        "Symbol": "PUMP",
        "trade_signal": "EXPLOSION_ARMED",
        "explosion_score": 88,
        "entry_score": 70,
        "Price": 1.20,
        "entry": 1.20,
        "stop": 1.10,
        "tp1": 1.35,
        "tp2": 1.50,
        "risk_reward": 2.1,
    }]
    short_rows = [{
        "symbol": "PUMP",
        "trade_action": "SHORT_NOW",
        "trade_category": "NEW_LISTING_DUMP",
        "exhaustion_score": 76,
        "timing_quality": 82,
        "price": 1.18,
        "entry": 1.18,
        "stop": 1.27,
        "tp1": 1.02,
        "tp2": 0.92,
        "rr_effective": 2.4,
        "pump_pct": 44,
        "from_ath_pct": 14,
    }]

    rows = api._merge_crypto_trade_signals(long_rows, short_rows)

    assert len(rows) == 1
    assert rows[0]["Symbol"] == "PUMP"
    assert rows[0]["direction"] == "SHORT"
    assert rows[0]["trade_action"] == "JETZT_SHORT"
    assert "opposite_crypto_engine_suppressed" in rows[0]["risk_flags"]


def test_crypto_trade_signals_prefers_confirmed_long_over_weak_short_watch():
    long_rows = [{
        "Symbol": "COIL",
        "trade_signal": "JETZT_TRADEN",
        "explosion_score": 84,
        "entry_score": 91,
        "Price": 2.0,
        "entry": 2.0,
        "stop": 1.9,
        "tp1": 2.25,
        "tp2": 2.45,
        "risk_reward": 2.8,
    }]
    short_rows = [{
        "symbol": "COIL",
        "trade_action": "BEOBACHTEN",
        "trade_category": "ACTIVE_PUMP_WATCH",
        "exhaustion_score": 52,
        "timing_quality": 30,
        "price": 2.0,
        "rr_effective": 0.8,
        "pump_pct": 11,
        "from_ath_pct": 2,
    }]

    rows = api._merge_crypto_trade_signals(long_rows, short_rows)

    assert len(rows) == 1
    assert rows[0]["direction"] == "LONG"
    assert rows[0]["trade_action"] == "JETZT_LONG"
    assert rows[0]["trade_signal"] == "JETZT_TRADEN"


def test_crypto_trade_signals_keeps_distinct_long_and_short_symbols():
    rows = api._merge_crypto_trade_signals(
        [{
            "Symbol": "LONGA",
            "trade_signal": "JETZT_TRADEN",
            "explosion_score": 82,
            "entry_score": 86,
            "Price": 3,
        }],
        [{
            "symbol": "SHORTB",
            "trade_action": "SHORT_NOW",
            "trade_category": "NEW_LISTING_DUMP",
            "exhaustion_score": 81,
            "timing_quality": 84,
            "price": 4,
            "pump_pct": 60,
            "from_ath_pct": 18,
        }],
    )

    assert [row["direction"] for row in rows] == ["LONG", "SHORT"]
    assert {row["Symbol"] for row in rows} == {"LONGA", "SHORTB"}
