from datetime import datetime, timezone

import api


def _daily_bars(volume=100_000):
    return [
        {
            "date": f"2026-06-{idx + 1:02d}",
            "open": 9.8,
            "high": 10.4,
            "low": 9.6,
            "close": 10.0,
            "volume": volume,
        }
        for idx in range(20)
    ]


def _bar(minute, open_, high, low, close, volume=10_000):
    base = datetime(2026, 7, 10, 13, 30, tzinfo=timezone.utc).timestamp() * 1000
    return {
        "timestamp": base + minute * 60_000,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_intraday_rvol_is_normalized_against_expected_session_volume():
    # 10:00 ET is 30 minutes after open; the shared U-curve expects 22%.
    now_utc = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    metrics = api._strategy_daily_history_metrics(
        _daily_bars(),
        price=10.2,
        day_open=10.0,
        day_high=10.3,
        day_low=9.9,
        day_volume=22_000,
        now_utc=now_utc,
    )

    assert metrics["expected_volume_fraction"] == 0.22
    assert metrics["rvol20_raw"] == 0.22
    assert metrics["rvol20"] == 1.0
    assert metrics["rvol_source"] == "20D_intraday_time_adjusted"
    assert metrics["median_dollar_vol20"] == 1_000_000


def test_mail_classifier_waits_below_unreclaimed_resistance(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda: ({"TEST"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda row: [])
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda row, scanner: [])
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *args, **kwargs: 0)
    row = {
        "ticker": "TEST",
        "Strategy": "Momentum Breakout Long",
        "grade": "S",
        "score": 95,
        "rvol": 2.2,
        "price": 100.0,
        "direction": "LONG",
        "Entry": 100.0,
        "StopLoss": 99.0,
        "TP1": 102.0,
        "TP2": 104.0,
        "trade_setup": {
            "direction": "LONG",
            "entry": 100.0,
            "stop": 99.0,
            "tp1": 102.0,
            "tp2": 104.0,
            "nearest_barrier": {
                "side": "resistance",
                "price": 100.8,
                "distance_r": 0.8,
                "timeframe": "1D",
            },
        },
    }

    state = api._classify_alert_candidate("stock_strategy", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "near_structural_barrier_wait_trigger" in state["suppression_reasons"]
    assert state["decision"] == "WAIT_TRIGGER"


def test_momentum_mail_gate_fails_closed_when_breakout_metadata_is_missing():
    ok, reason = api._stock_strategy_mail_quality_state({"Strategy": "Momentum Breakout Long"})

    assert ok is False
    assert reason == "momentum_mail_blocked_missing_breakout_type"


def _clean_momentum_mail_row():
    return {
        "Strategy": "Momentum Breakout Long",
        "History_OK": True,
        "MedianDollarVol20": 5_000_000,
        "Momentum_Breakout_Type": "20D_HIGH_BREAKOUT",
        "Breakout_Continuation_Status": "CONTINUATION_OK",
        "Breakout_Continuation_Score": 92,
        "Breakout_Fakeout_Risk": "LOW",
        "Upper_Wick_Pct": 8,
        "Close_Position": 0.92,
        "RVOL": 2.2,
        "Preis": 100,
        "Day_High": 101,
        "Day_Open": 98,
        "Change_Pct": 2.5,
        "TP1": 105,
    }


def test_momentum_mail_blocks_thin_historical_dollar_liquidity():
    row = _clean_momentum_mail_row()
    row["MedianDollarVol20"] = 750_000

    ok, reason = api._stock_strategy_mail_quality_state(row)

    assert ok is False
    assert reason == "momentum_mail_blocked_thin_baseline_liquidity"


def test_momentum_mail_blocks_missing_historical_liquidity():
    row = _clean_momentum_mail_row()
    row["History_OK"] = False

    ok, reason = api._stock_strategy_mail_quality_state(row)

    assert ok is False
    assert reason == "momentum_mail_blocked_missing_liquidity_history"


def test_momentum_mail_allows_sufficient_baseline_liquidity():
    ok, reason = api._stock_strategy_mail_quality_state(_clean_momentum_mail_row())

    assert ok is True
    assert reason == ""


def test_stock_4h_execution_blocks_multi_candle_chase():
    bars = [
        _bar(0, 10.0, 10.2, 9.9, 10.1),
        _bar(240, 10.1, 10.3, 10.0, 10.2),
        _bar(480, 10.2, 10.4, 10.0, 10.1),
        _bar(720, 10.1, 10.5, 10.0, 10.4),
        _bar(960, 10.4, 10.7, 10.2, 10.6),
        _bar(1200, 10.6, 10.8, 10.4, 10.7),
        _bar(1440, 10.7, 11.2, 10.6, 11.1),
        _bar(1680, 11.1, 12.0, 11.0, 11.9),
        _bar(1920, 11.9, 13.2, 11.8, 13.0),
        _bar(2160, 13.0, 14.4, 12.9, 14.2),
        _bar(2400, 14.2, 15.6, 14.1, 15.4),
        _bar(2640, 15.4, 15.8, 15.1, 15.3),
    ]

    state = api.stock_swing_4h_execution_state(bars)

    assert state["Swing_4H_Execution_Status"] == "WAIT_RETEST"
    assert state["Swing_4H_Recent_Move_Pct"] >= 12


def test_stock_4h_execution_allows_fresh_non_extended_breakout():
    bars = [
        _bar(0, 10.0, 10.2, 9.9, 10.1),
        _bar(240, 10.1, 10.3, 10.0, 10.2),
        _bar(480, 10.2, 10.25, 10.0, 10.1),
        _bar(720, 10.1, 10.35, 10.0, 10.3),
        _bar(960, 10.3, 10.4, 10.2, 10.25),
        _bar(1200, 10.25, 10.45, 10.2, 10.35),
        _bar(1440, 10.35, 10.5, 10.3, 10.4),
        _bar(1680, 10.4, 10.85, 10.35, 10.75),
    ]

    state = api.stock_swing_4h_execution_state(bars)

    assert state["Swing_4H_Execution_Status"] == "CLEAR"


def test_gap_momentum_mail_blocks_extended_4h_run():
    row = {
        "Strategy": "Gap Momentum Long",
        "direction": "LONG",
        "Swing_4H_Execution_Checked": True,
        "Swing_4H_Execution_Status": "WAIT_RETEST",
    }

    ok, reason = api._stock_strategy_mail_quality_state(row)

    assert ok is False
    assert reason == "stock_swing_mail_blocked_4h_extended_run"


def test_breakout_freshness_detects_recent_cross():
    bars = [
        _bar(0, 99.3, 99.8, 99.1, 99.5),
        _bar(5, 99.5, 100.4, 99.4, 100.2),
        _bar(10, 100.2, 100.8, 100.1, 100.6),
    ]
    row = {"ticker": "TEST", "direction": "LONG", "Breakout_Level": 100.0}

    result = api._stock_breakout_freshness_state(row, bars=bars)

    assert result["Breakout_Freshness_Status"] == "FRESH_CROSS"
    assert result["Breakout_Age_Minutes"] == 5.0


def test_breakout_freshness_blocks_lost_level():
    bars = [
        _bar(0, 99.3, 99.8, 99.1, 99.5),
        _bar(5, 99.5, 100.5, 99.4, 100.3),
        _bar(10, 100.2, 100.3, 98.8, 99.4),
    ]
    row = {"ticker": "TEST", "direction": "LONG", "Breakout_Level": 100.0}

    result = api._stock_breakout_freshness_state(row, bars=bars)

    assert result["Breakout_Freshness_Status"] == "FAILED_BREAKOUT"


def test_breakout_freshness_does_not_treat_nearby_price_as_confirmed_cross():
    bars = [
        _bar(0, 99.2, 99.7, 99.1, 99.5),
        _bar(5, 99.5, 100.05, 99.4, 99.85),
        _bar(10, 99.85, 100.02, 99.7, 99.9),
    ]
    row = {"ticker": "TEST", "direction": "LONG", "Breakout_Level": 100.0}

    result = api._stock_breakout_freshness_state(row, bars=bars)

    assert result["Breakout_Freshness_Status"] == "NOT_CONFIRMED"
