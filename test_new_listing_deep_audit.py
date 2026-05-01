import time

from modules.new_listing_scanner import (
    _is_tradeable_short_signal,
    _monitor_key,
    check_safety,
    generate_short_signal,
)


def _fresh_ticker():
    return {
        "volume_usd_24h": 1_500_000,
        "bid": 96.9,
        "ask": 97.1,
        "timestamp": int(time.time()),
    }


def _fresh_candles():
    now = int(time.time())
    return [
        {"timestamp": now - 3600, "open": 95, "close": 96, "volume_usd": 50_000},
        {"timestamp": now, "open": 96, "close": 97, "volume_usd": 60_000},
    ]


def _deep_book():
    return {
        "bids": [(96.9, 200), (96.8, 200)],
        "asks": [(97.1, 200), (97.2, 200)],
    }


def test_monitor_key_keeps_same_symbol_separate_by_exchange():
    assert _monitor_key("ABCUSDT", "mexc") != _monitor_key("ABCUSDT", "binance")


def test_safety_requires_fresh_ticker_candles_and_orderbook():
    safe, warnings = check_safety(_fresh_ticker(), _deep_book(), _fresh_candles())
    assert safe is True
    assert any("OK" in warning for warning in warnings)

    safe, warnings = check_safety(_fresh_ticker(), None, _fresh_candles())
    assert safe is False
    assert any("Orderbook" in warning for warning in warnings)

    stale = _fresh_ticker()
    stale["timestamp"] = int(time.time()) - 3600
    safe, warnings = check_safety(stale, _deep_book(), _fresh_candles())
    assert safe is False
    assert any("Ticker stale" in warning for warning in warnings)


def test_pump_still_running_is_watch_not_tradeable_short():
    signal = generate_short_signal(
        "MOONUSDT",
        {
            "ath": 100,
            "current_price": 99,
            "pump_pct": 80,
            "from_ath_pct": 1,
            "momentum_recent": 2.0,
            "current_red_streak": 0,
            "avg_upper_wick_pct": 5,
        },
        exh_score=90,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["continuation_risk"] is True
    assert signal["confirmation_ok"] is False
    assert signal["timing_quality"] < 4
    assert _is_tradeable_short_signal(signal) is False


def test_confirmed_first_crack_with_rr_is_tradeable_short():
    signal = generate_short_signal(
        "CRACKUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["confirmation_ok"] is True
    assert signal["continuation_risk"] is False
    assert signal["rr_effective"] >= 1.5
    assert signal["grade"] in ("S", "A")
    assert signal["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(signal) is True


def test_low_rr_confirmed_crack_stays_watchlist_only():
    signal = generate_short_signal(
        "NORRUSDT",
        {
            "ath": 100,
            "current_price": 90,
            "pump_pct": 80,
            "from_ath_pct": 10,
            "momentum_recent": -1.5,
            "current_red_streak": 2,
            "avg_upper_wick_pct": 30,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["rr_effective"] < 1.5
    assert "rr_too_low" in signal["risk_flags"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False
