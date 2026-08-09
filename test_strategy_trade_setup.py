from datetime import datetime, timezone

from api import (
    _apply_trade_health_final_signal,
    _build_structured_trade_setup,
    _early_mover_long_rule_reasons,
    _strategy_daily_history_metrics,
)
from modules.backtests import simulate_trade


def test_momentum_sidebar_targets_do_not_use_tiny_one_r_targets():
    setup = _build_structured_trade_setup(
        direction="LONG",
        entry=9.27,
        atr=0.28,
        support_1=8.97,
        resistance_1=9.57,
        high_20d=9.60,
        low_20d=8.35,
        range_pos=73,
    )

    assert setup is not None
    assert setup["tp1"] >= 9.85
    assert setup["tp2"] >= 10.05
    assert setup["rr_tp1"] >= 1.5
    assert setup["rr_tp2"] >= 2.5
    assert setup["rr"] >= 2.0
    assert any("Resistance" in warning for warning in setup["warnings"])
    assert any("20D-Range" in note for note in setup["notes"])


def test_short_sidebar_targets_respect_minimum_r_and_support_barriers():
    setup = _build_structured_trade_setup(
        direction="SHORT",
        entry=50.0,
        atr=1.2,
        support_1=49.3,
        resistance_1=51.1,
        high_20d=55.0,
        low_20d=48.0,
        range_pos=25,
    )

    assert setup is not None
    assert setup["tp1"] <= 48.0
    assert setup["tp2"] <= 46.9
    assert setup["rr_tp1"] >= 1.5
    assert setup["rr_tp2"] >= 2.5
    assert any("Support" in warning for warning in setup["warnings"])


def test_long_setup_can_add_starter_entry_before_breakout():
    setup = _build_structured_trade_setup(
        direction="LONG",
        entry=33.44,
        atr=1.36,
        support_1=29.99,
        resistance_1=33.44,
        high_20d=33.44,
        low_20d=26.20,
        range_pos=62,
        current_price=30.68,
        vwap=30.02,
        ema20=29.73,
        vah=30.43,
    )

    assert setup is not None
    assert setup["entry"] == 33.44
    assert setup["main_entry"] == 33.44
    assert setup["starter_plan"]["status"] == "ANTICIPATION"
    assert setup["starter_entry"] == 30.68
    assert setup["starter_tp1"] == 33.44
    assert setup["starter_stop"] < 30.68
    assert setup["starter_plan"]["rr_tp1"] >= 1.15
    assert setup["entry_plan_type"] == "starter_plus_breakout"
    assert any("Starter Entry" in note for note in setup["notes"])


def test_long_setup_does_not_add_starter_when_breakout_is_already_close():
    setup = _build_structured_trade_setup(
        direction="LONG",
        entry=33.44,
        atr=1.36,
        support_1=31.20,
        resistance_1=33.44,
        high_20d=33.44,
        low_20d=28.10,
        range_pos=82,
        current_price=33.10,
        vwap=32.80,
        ema20=32.40,
        vah=32.95,
    )

    assert setup is not None
    assert "starter_plan" not in setup
    assert setup["entry"] == 33.44


def test_long_setup_does_not_add_starter_when_structure_is_only_overhead():
    setup = _build_structured_trade_setup(
        direction="LONG",
        entry=33.44,
        atr=1.36,
        support_1=0,
        resistance_1=33.44,
        high_20d=33.44,
        low_20d=26.20,
        range_pos=62,
        current_price=30.68,
        vwap=0,
        ema20=0,
        vah=30.85,
    )

    assert setup is not None
    assert "starter_plan" not in setup


def test_long_setup_does_not_add_starter_when_main_breakout_is_too_far():
    setup = _build_structured_trade_setup(
        direction="LONG",
        entry=42.00,
        atr=1.20,
        support_1=30.10,
        resistance_1=42.00,
        high_20d=42.00,
        low_20d=25.50,
        range_pos=45,
        current_price=30.50,
        vwap=30.20,
        ema20=29.90,
        vah=30.25,
    )

    assert setup is not None
    assert "starter_plan" not in setup


def test_strategy_rvol_uses_completed_20d_average_not_previous_day_only():
    bars = []
    for idx in range(20):
        bars.append({
            "date": f"2026-04-{idx + 1:02d}",
            "open": 10 + idx * 0.02,
            "high": 10.6 + idx * 0.02,
            "low": 9.7 + idx * 0.02,
            "close": 10.2 + idx * 0.02,
            "volume": 100_000,
        })
    bars[-1]["volume"] = 1_000_000

    metrics = _strategy_daily_history_metrics(
        bars,
        price=11.0,
        day_open=10.8,
        day_high=11.2,
        day_low=10.6,
        day_volume=200_000,
        now_utc=datetime(2026, 4, 20, 21, 0, tzinfo=timezone.utc),
    )

    assert metrics["history_ok"] is True
    assert metrics["avg_vol20"] == 145_000
    assert metrics["rvol20"] == 1.38
    assert metrics["support_1"] < 11.0
    assert metrics["resistance_1"] > 11.0


def test_trade_health_final_state_overrides_stock_scanner_now_signal():
    row = {
        "Ticker": "TEST",
        "trade_signal": "JETZT_TRADEN",
        "entry_status": "JETZT_TRADEN",
        "trade_action": "LONG_NOW",
        "risk_flags": [],
        "risk_reasons": [],
        "trade_decision": "NO_TRADE",
        "trade_decision_label": "No Trade",
        "trade_health": {"decision": "NO_TRADE", "decision_label": "No Trade"},
        "trade_setup": {"trade_action": "LONG_NOW", "entry_status": "JETZT_TRADEN"},
    }

    _apply_trade_health_final_signal(row, "stock_strategy")

    assert row["trade_signal"] == "NICHT_TRADEN"
    assert row["entry_status"] == "NO_TRADE"
    assert row["trade_action"] == "NO_TRADE"
    assert row["trade_setup"]["trade_action"] == "NO_TRADE"
    assert row["trade_setup"]["trade_decision"] == "NO_TRADE"


def test_early_mover_swing_does_not_require_5m_but_intraday_does(monkeypatch):
    row = {
        "_alert_horizon": "swing",
        "direction": "LONG",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "LONG_TRIGGER",
        "signal_quality": "tradeable",
        "score": 88,
        "grade": "S",
        "entry": 10.0,
        "stop_loss": 9.4,
        "tp1": 11.4,
        "tp2": 12.2,
        "live_rr_ratio": 2.4,
        "distance_to_entry_r": 0,
        "risk_flags": ["no_intraday_execution_trigger"],
        "btc_context": {"tailwind": True, "btc_24h": 0.4, "btc_7d": 1.1, "alpha_24h": 2.0},
        "target_quality": "STRUCTURAL",
    }

    monkeypatch.setattr("api._scanner_uses_intraday_horizon", lambda scanner_name: True)

    assert "early_mover_wait_entry_confirmation" not in _early_mover_long_rule_reasons(row)

    row["_alert_horizon"] = "intraday"
    assert "early_mover_wait_entry_confirmation" in _early_mover_long_rule_reasons(row)


def _two_target_strategy(direction="long"):
    return {
        "direction": direction,
        "entry": "at_close",
        "stop_pct": 0.05,
        "tp1_rr": 1.0,
        "tp2_rr": 3.0,
        "max_hold_days": 3,
    }


def test_backtest_does_not_exit_at_untraded_average_target():
    bars = [
        {"date": "2026-01-01", "open": 99, "high": 101, "low": 98, "close": 100},
        {"date": "2026-01-02", "open": 100.2, "high": 106, "low": 99, "close": 105},
        {"date": "2026-01-03", "open": 106, "high": 111, "low": 104, "close": 110},
    ]

    trade = simulate_trade(bars, 0, _two_target_strategy())

    assert trade is not None
    assert trade["target_model"] == "50_50_tp1_tp2"
    assert trade["exit_reason"] == "TP1_STOP"
    assert trade["tp1_hit"] is True
    assert trade["r_multiple"] == 0.45
    assert trade["exit_reason_upper"] == "TP1+EOD"
    assert trade["r_multiple_upper"] == 1.45
    assert trade["intrabar_ambiguous"] is True


def test_backtest_reports_tp1_stop_to_tp2_band_when_daily_order_is_unknown():
    bars = [
        {"date": "2026-01-01", "open": 99, "high": 101, "low": 98, "close": 100},
        {"date": "2026-01-02", "open": 100.2, "high": 106, "low": 99, "close": 105},
        {"date": "2026-01-03", "open": 106, "high": 116, "low": 104, "close": 115},
    ]

    trade = simulate_trade(bars, 0, _two_target_strategy())

    assert trade is not None
    assert trade["exit_reason"] == "TP1_STOP"
    assert trade["r_multiple"] == 0.45
    assert trade["exit_reason_upper"] == "BLENDED_TP"
    assert trade["r_multiple_upper"] == 1.95
    assert "same_bar_tp1_and_trailed_stop" in trade["ambiguity_reason"]


def test_backtest_tp1_then_breakeven_stop_keeps_only_partial_profit():
    bars = [
        {"date": "2026-01-01", "open": 99, "high": 101, "low": 98, "close": 100},
        {"date": "2026-01-02", "open": 100.2, "high": 106, "low": 99, "close": 105},
        {"date": "2026-01-03", "open": 104, "high": 106, "low": 99, "close": 100},
    ]

    trade = simulate_trade(bars, 0, _two_target_strategy())

    assert trade is not None
    assert trade["exit_reason"] == "TP1_STOP"
    assert trade["r_multiple"] == 0.45


def test_backtest_short_partial_exit_is_directionally_symmetric():
    bars = [
        {"date": "2026-01-01", "open": 101, "high": 102, "low": 99, "close": 100},
        {"date": "2026-01-02", "open": 99.8, "high": 101, "low": 94, "close": 95},
        {"date": "2026-01-03", "open": 94, "high": 96, "low": 84, "close": 85},
    ]

    trade = simulate_trade(bars, 0, _two_target_strategy(direction="short"))

    assert trade is not None
    assert trade["exit_reason"] == "TP1_STOP"
    assert trade["r_multiple"] == 0.45
    assert trade["exit_reason_upper"] == "BLENDED_TP"
    assert trade["r_multiple_upper"] == 1.95


def test_backtest_clean_same_bar_tp2_is_not_delayed_to_next_day():
    bars = [
        {"date": "2026-01-01", "open": 99, "high": 101, "low": 98, "close": 100},
        {"date": "2026-01-02", "open": 101, "high": 116, "low": 101, "close": 115},
    ]

    trade = simulate_trade(bars, 0, _two_target_strategy())

    assert trade is not None
    assert trade["exit_reason"] == "BLENDED_TP"
    assert trade["exit_reason_upper"] == "BLENDED_TP"
    assert trade["r_multiple"] == 1.95
    assert trade["intrabar_ambiguous"] is False
