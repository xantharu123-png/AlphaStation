import time
from datetime import datetime, timedelta, timezone

from modules.new_listing_scanner import (
    _clean_listing_base_symbol,
    _extract_listing_symbols_from_title,
    _is_tradeable_short_signal,
    _monitor_key,
    calculate_micro_crack_trigger,
    check_safety,
    cleanup_monitoring,
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


def _micro_crack_candles():
    now = int(time.time())
    rows = []
    price = 80.0
    for i in range(18):
        rows.append({
            "timestamp": now + i * 300,
            "open": price,
            "high": price * 1.018,
            "low": price * 0.995,
            "close": price * 1.015,
            "volume_usd": 100_000 + i * 2_000,
        })
        price *= 1.015
    high = 130.0
    rows.extend([
        {"timestamp": now + 18 * 300, "open": price, "high": high, "low": 99, "close": 118, "volume_usd": 420_000},
        {"timestamp": now + 19 * 300, "open": 118, "high": 119, "low": 115, "close": 116, "volume_usd": 360_000},
        {"timestamp": now + 20 * 300, "open": 116, "high": 117, "low": 113, "close": 114, "volume_usd": 340_000},
        {"timestamp": now + 21 * 300, "open": 114, "high": 116.5, "low": 112, "close": 115, "volume_usd": 280_000},
        {"timestamp": now + 22 * 300, "open": 115, "high": 117, "low": 113, "close": 114.5, "volume_usd": 520_000},
    ])
    return rows


def test_monitor_key_keeps_same_symbol_separate_by_exchange():
    assert _monitor_key("ABCUSDT", "mexc") != _monitor_key("ABCUSDT", "binance")


def test_listing_announcement_symbol_parser_keeps_crypto_and_blocks_stock_titles():
    assert _clean_listing_base_symbol("STARUSDT") == "STAR"
    assert _extract_listing_symbols_from_title("Binance Futures Will Launch STARUSDT Perpetual Contracts") == ["STAR"]
    assert _extract_listing_symbols_from_title("[Initial listing] Bitget to list Hooli (HOOLI) in the GameFi zone") == ["HOOLI"]
    assert _extract_listing_symbols_from_title("Bitget Stock Futures will list NVDAUSDT shares") == []


def test_cleanup_expires_new_listing_by_exchange_listing_time_not_detection_time():
    now = datetime.now(timezone.utc)
    monitoring = {
        "binance:OLDUSDT": {
            "symbol": "OLDUSDT",
            "exchange": "binance",
            "source": "new_listing",
            "status": "monitoring",
            "detected_at": now.isoformat(),
            "listing_time": (now - timedelta(hours=96)).isoformat(),
        }
    }

    cleaned = cleanup_monitoring(monitoring)

    assert cleaned["binance:OLDUSDT"]["status"] == "expired"


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
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
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


def test_missing_listing_context_is_watch_only_even_if_crack_is_confirmed():
    signal = generate_short_signal(
        "UNKNOWNAGEUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["trade_category"] == "LISTING_INFO_MISSING"
    assert signal["listing_trade_ok"] is False
    assert "listing_info_missing" in signal["risk_flags"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_active_pump_detection_is_watch_only_even_with_crack():
    signal = generate_short_signal(
        "OLDPUMPUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "pump_detection",
            "listing_age_hours": None,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["trade_category"] == "ACTIVE_PUMP_WATCH"
    assert signal["listing_trade_ok"] is False
    assert "active_pump_watch_only" in signal["risk_flags"]
    assert "ACTIVE PUMP BEOBACHTEN" in signal["timing"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_btc_risk_on_waits_for_deeper_crack_before_short():
    signal = generate_short_signal(
        "BTCRISKONUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
            "btc_tailwind_risk": True,
            "btc_change_pct": 3.2,
            "coin_change_pct": 1.0,
            "btc_divergence": -2.2,
            "btc_short_context": "BTC_RISK_ON_WAIT_FOR_DEEPER_CRACK",
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["btc_context_ok"] is False
    assert "btc_risk_on_wait_for_deeper_crack" in signal["risk_flags"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_new_listing_age_window_required_for_short_mail_quality():
    too_early = generate_short_signal(
        "BABYUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 0.3,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )
    valid = generate_short_signal(
        "NEWUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert too_early["trade_category"] == "NEW_LISTING_TOO_EARLY"
    assert too_early["listing_trade_ok"] is False
    assert "listing_too_early" in too_early["risk_flags"]
    assert _is_tradeable_short_signal(too_early) is False
    assert valid["trade_category"] == "NEW_LISTING_DUMP"
    assert valid["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(valid) is True


def test_early_crack_uses_local_rejection_stop_and_can_trade_below_old_score_gate():
    signal = generate_short_signal(
        "EARLYUSDT",
        {
            "ath": 100,
            "current_price": 96,
            "pump_pct": 70,
            "from_ath_pct": 4.0,
            "momentum_recent": -0.2,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 30,
            "recent_rejection_high": 100,
            "recent_crack_depth_pct": 4.0,
            "prior_3_low_broken": True,
            "lower_high_confirmed": True,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 100,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        },
        exh_score=50,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["setup_type"] == "early_crack"
    assert signal["stop_model"] == "micro_crack_stop"
    assert signal["stop_loss"] < signal["hard_stop_loss"]
    assert signal["grade"] == "A"
    assert signal["timing_quality"] == 4
    assert signal["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(signal) is True


def test_early_crack_without_micro_trigger_stays_watchlist_only():
    signal = generate_short_signal(
        "GENIUSUSDT",
        {
            "ath": 0.5226,
            "current_price": 0.5006,
            "pump_pct": 20,
            "from_ath_pct": 4.2,
            "momentum_recent": -0.2,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "recent_rejection_high": 0.5226,
            "recent_crack_depth_pct": 4.2,
            "prior_3_low_broken": True,
            "lower_high_confirmed": True,
            "micro_trigger_ok": False,
            "micro_score": 20,
        },
        exh_score=47,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert "micro_trigger_missing" in signal["risk_flags"]
    assert signal["timing_quality"] < 4
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_micro_crack_trigger_can_create_tradeable_signal():
    pump_data = {
        "ath": 130,
        "current_price": _micro_crack_candles()[-1]["close"],
        "pump_pct": 80,
        "from_ath_pct": 9.0,
        "momentum_recent": 0.2,
        "current_red_streak": 0,
        "avg_upper_wick_pct": 5,
        "listing_source": "new_listing",
        "listing_age_hours": 24,
    }
    micro = calculate_micro_crack_trigger(_micro_crack_candles(), pump_data)
    pump_data.update(micro)
    signal = generate_short_signal(
        "MICROUSDT",
        pump_data,
        exh_score=35,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert micro["micro_trigger_ok"] is True
    assert signal["setup_type"] == "early_crack"
    assert signal["stop_model"] == "micro_crack_stop"
    assert signal["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(signal) is True


def test_ultra_early_1m_crack_is_disabled():
    candles = _micro_crack_candles()[-12:]
    pump_data = {
        "ath": 130,
        "current_price": candles[-1]["close"],
        "pump_pct": 80,
        "from_ath_pct": 9.0,
        "listing_source": "new_listing",
        "listing_age_hours": 2,
    }

    micro = calculate_micro_crack_trigger(candles, pump_data, timeframe="1m")

    assert micro["micro_timeframe"] == "1m"
    assert micro["micro_trigger_ok"] is False
    assert "one_minute_execution_disabled" in micro["micro_warnings"]


def test_micro_crack_blocks_green_squeeze_without_first_crack():
    candles = _micro_crack_candles()
    last = candles[-1]
    last["open"] = last["close"] * 0.98
    last["high"] = last["close"] * 1.01
    last["low"] = last["open"] * 0.995
    last["close"] = last["high"] * 0.995

    micro = calculate_micro_crack_trigger(candles, {"ath": 135})

    assert micro["micro_trigger_ok"] is False
    assert "micro_still_squeezing" in micro["micro_warnings"] or "micro_too_early_no_crack" in micro["micro_warnings"]


def test_early_crack_blocks_after_tp1_or_without_structure():
    too_late = generate_short_signal(
        "LATEUSDT",
        {
            "ath": 100,
            "current_price": 79,
            "pump_pct": 70,
            "from_ath_pct": 21.0,
            "momentum_recent": -1.0,
            "current_red_streak": 2,
            "avg_upper_wick_pct": 35,
            "recent_rejection_high": 92,
            "recent_crack_depth_pct": 14.0,
            "prior_3_low_broken": True,
            "lower_high_confirmed": True,
        },
        exh_score=55,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )
    no_structure = generate_short_signal(
        "NOSTRUCTUSDT",
        {
            "ath": 100,
            "current_price": 96,
            "pump_pct": 70,
            "from_ath_pct": 4.0,
            "momentum_recent": 0.8,
            "current_red_streak": 0,
            "avg_upper_wick_pct": 5,
            "recent_rejection_high": 96.5,
            "recent_crack_depth_pct": 0.5,
            "prior_3_low_broken": False,
            "lower_high_confirmed": False,
        },
        exh_score=55,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert too_late["signal_quality"] == "watch_or_blocked"
    assert too_late["tp1_missed"] is True
    assert no_structure["signal_quality"] == "watch_or_blocked"
    assert "crack_structure_weak" in no_structure["risk_flags"]


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
