from datetime import datetime, timedelta, timezone

from api import (
    _alert_trade_plan_ok,
    _attach_starter_entry_plan,
    _apply_trade_health_final_signal,
    _build_structured_trade_setup,
    _completed_stock_daily_atr,
    _early_mover_long_rule_reasons,
    _extract_trade_barrier,
    _strategy_daily_history_metrics,
)
from modules.backtests import simulate_trade
from modules.level_zones import LevelEvidence, StructureSnapshot, build_structure_snapshot


def test_momentum_sidebar_keeps_first_resistance_and_waits_instead_of_skipping_it():
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
    assert setup["tp1"] == 9.57
    assert setup["tp2"] > setup["tp1"]
    assert setup["rr_tp1"] < 1.35
    assert setup["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert setup["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"
    assert setup["nearest_barrier"]["price"] == 9.57
    assert setup["trade_action"] == "WAIT_FOR_BREAK_RECLAIM"
    assert any("Break/Reclaim" in warning for warning in setup["warnings"])
    assert any("20D-Range" in note for note in setup["notes"])


def test_short_sidebar_keeps_first_support_and_waits_instead_of_skipping_it():
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
    assert setup["tp1"] == 49.3
    assert setup["tp2"] < setup["tp1"]
    assert setup["rr_tp1"] < 1.35
    assert setup["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert setup["barrier_gate"] == "BREAK_SUPPORT_REQUIRED"
    assert setup["nearest_barrier"]["price"] == 49.3
    assert any("Break/Reclaim" in warning for warning in setup["warnings"])


def test_alert_gate_rejects_a_plan_waiting_for_first_barrier_reclaim():
    row = {
        "direction": "LONG",
        "entry": 10.0,
        "stop_loss": 9.0,
        "tp1": 10.5,
        "tp2": 12.0,
        "trade_setup": {
            "structure_status": "WAIT_BREAK_RECLAIM",
            "barrier_gate": "BREAK_RECLAIM_REQUIRED",
        },
    }

    assert _alert_trade_plan_ok(row, require_native_levels=False) is False


def test_alert_gate_rejects_explicit_projection_only_tp1_plan():
    row = {
        "direction": "LONG",
        "entry": 100.0,
        "stop_loss": 97.0,
        "tp1": 104.5,
        "tp2": 108.0,
        "trade_setup": {
            "entry": 100.0,
            "stop": 97.0,
            "tp1": 104.5,
            "tp2": 108.0,
            "target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
            "tp1_is_projection": True,
            "tp2_is_projection": True,
            "structure_status": "ACCEPT",
        },
    }

    assert _alert_trade_plan_ok(row, require_native_levels=False) is False


def test_alert_gate_uses_final_row_quality_before_stale_nested_setup():
    row = {
        "direction": "LONG",
        "entry": 100.0,
        "stop_loss": 95.0,
        "tp1": 108.0,
        "tp2": 115.0,
        "target_quality": "WEAK_STRUCTURAL_TARGETS",
        "trade_setup": {
            "target_quality": "STRUCTURAL_VRVP",
            "structure_status": "ACCEPT",
        },
    }

    assert _alert_trade_plan_ok(row, require_native_levels=False) is False


def test_alert_gate_final_acceptance_can_clear_stale_nested_wait_state():
    row = {
        "direction": "LONG",
        "entry": 100.0,
        "stop_loss": 95.0,
        "tp1": 108.0,
        "tp2": 115.0,
        "target_quality": "STRUCTURAL_FIRST_BARRIER",
        "barrier_gate": None,
        "structure_status": "ACCEPT",
        "trade_setup": {
            "target_quality": "STRUCTURAL_FIRST_BARRIER",
            "barrier_gate": "BREAK_RECLAIM_REQUIRED",
            "structure_status": "WAIT_BREAK_RECLAIM",
            "structure_decision": {"status": "REJECT"},
        },
    }

    assert _alert_trade_plan_ok(row, require_native_levels=False) is True


def test_final_row_barrier_wins_over_stale_nested_barrier():
    row = {
        "nearest_barrier": {
            "zone_id": "final-zone",
            "price": 110.0,
            "side": "resistance",
        },
        "trade_setup": {
            "nearest_barrier": {
                "zone_id": "stale-zone",
                "price": 105.0,
                "side": "resistance",
            }
        },
    }

    assert _extract_trade_barrier(row)["zone_id"] == "final-zone"


def test_explicit_final_row_barrier_clear_does_not_revive_stale_nested_barrier():
    row = {
        "nearest_barrier": None,
        "trade_setup": {
            "nearest_barrier": {
                "zone_id": "stale-zone",
                "price": 105.0,
                "side": "resistance",
            }
        },
    }

    assert _extract_trade_barrier(row) is None


def test_invalid_final_row_barrier_cannot_revive_stale_nested_barrier():
    row = {
        "nearest_barrier": {"zone_id": "invalid-final", "price": -1.0},
        "trade_setup": {
            "nearest_barrier": {
                "zone_id": "stale-zone",
                "price": 105.0,
                "side": "resistance",
            }
        },
    }

    assert _extract_trade_barrier(row) is None


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


def test_daily_hlc3_proxy_alone_cannot_authorize_a_starter_entry():
    setup = {
        "direction": "LONG",
        "entry": 105.0,
        "stop": 95.0,
        "tp1": 112.0,
        "tp2": 120.0,
    }

    enriched = _attach_starter_entry_plan(
        setup,
        current_price=100.0,
        atr=2.0,
        vwap=99.0,
        # No true-session/tick evidence: this value may only be Daily HLC3.
        vwap_evidence_type="daily_typical_price_proxy",
    )

    assert "starter_plan" not in enriched


def test_empty_causal_snapshot_never_falls_back_to_actionable_legacy_levels():
    unavailable = StructureSnapshot(
        symbol="EMPTY",
        asset_class="stock",
        horizon="swing",
        as_of=datetime(2026, 4, 20, 21, 0, tzinfo=timezone.utc),
        current_price=100.0,
        zones=(),
        atr_by_timeframe={},
        completed_bar_counts={},
        quality_flags=("no_completed_bars", "no_confirmed_levels"),
    )

    for legacy in (
        {"support_1": None, "resistance_1": None, "high_20d": None, "low_20d": None},
        {"support_1": 95.0, "resistance_1": 105.0, "high_20d": 110.0, "low_20d": 90.0},
    ):
        setup = _build_structured_trade_setup(
            direction="LONG",
            entry=100.0,
            atr=2.0,
            structure_snapshot=unavailable,
            **legacy,
        )
        assert setup is None


def test_crossed_resistance_requires_real_completed_reclaim_before_new_long_setup():
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    resistance = LevelEvidence(
        source_family="horizontal_swing",
        source_name="confirmed_swing_high",
        timeframe="1D",
        lower=101.0,
        upper=101.0,
        observed_at=base,
        confirmed_at=base,
        data_cutoff_at=base,
        provenance={"role_hint": "resistance"},
    )

    def bar(day, *, high, low, close):
        closed_at = base + timedelta(days=day)
        return {
            "open_time": closed_at - timedelta(days=1),
            "close_time": closed_at,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        }

    def snapshot(rows, day):
        return build_structure_snapshot(
            {"1D": rows},
            symbol="ABC",
            asset_class="stock",
            horizon="swing",
            as_of=base + timedelta(days=day),
            current_price=102.0,
            tick_size=0.05,
            external_evidence=[resistance],
            include_session_levels=False,
            pivot_left=1,
            pivot_right=1,
        )

    break_only = [bar(1, high=101.5, low=101.15, close=101.3)]
    pending = snapshot(break_only, 1)
    assert _build_structured_trade_setup(
        "LONG", 102.0, 2.0, 0, 0, 0, 0,
        structure_snapshot=pending,
        require_causal_structure=True,
    ) is None

    reclaimed = snapshot(
        break_only + [bar(2, high=101.4, low=101.08, close=101.25)],
        2,
    )
    setup = _build_structured_trade_setup(
        "LONG", 102.0, 2.0, 0, 0, 0, 0,
        structure_snapshot=reclaimed,
        require_causal_structure=True,
    )

    assert setup is not None
    transitions = setup["level_structure_summary"]["zone_transitions"]
    assert len(transitions) == 1
    assert transitions[0]["state"] == "RECLAIMED"


def test_strategy_rvol_uses_completed_20d_average_not_previous_day_only():
    bars = []
    for idx in range(20):
        bars.append({
            "date": f"2026-04-{idx + 1:02d}",
            "open": 95.0,
            "high": 100.0,
            "low": 90.0,
            "close": 95.0,
            "volume": 100_000,
        })
    bars.append({
        "date": "2026-04-21",
        "open": 101.0,
        "high": 120.0,
        "low": 100.5,
        "close": 119.0,
        "volume": 1_000_000,
    })

    metrics = _strategy_daily_history_metrics(
        bars,
        price=119.0,
        day_open=101.0,
        day_high=120.0,
        day_low=100.5,
        day_volume=1_000_000,
        now_utc=datetime(2026, 4, 21, 21, 0, tzinfo=timezone.utc),
    )

    assert metrics["history_ok"] is True
    assert metrics["avg_vol20"] == 100_000
    assert metrics["rvol20_raw"] == 10.0
    assert metrics["rvol20"] == 10.0
    assert metrics["high_20d"] == 100.0
    assert metrics["breakout_20d_pct"] == 19.0
    assert metrics["baseline_bars"] == 20
    assert metrics["completed_bars"] == 21
    assert metrics["support_1"] < 119.0


def test_strategy_levels_exclude_the_running_daily_session_before_close():
    bars = []
    for idx in range(20):
        bars.append({
            "date": f"2026-04-{idx + 1:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 100_000,
        })
    bars.append({
        "date": "2026-04-21",
        "open": 10.0,
        "high": 99.0,
        "low": 1.0,
        "close": 50.0,
        "volume": 900_000,
    })

    metrics = _strategy_daily_history_metrics(
        bars,
        price=10.5,
        day_open=10.0,
        day_high=99.0,
        day_low=1.0,
        day_volume=900_000,
        now_utc=datetime(2026, 4, 21, 19, 0, tzinfo=timezone.utc),
    )

    assert metrics["completed_bars"] == 20
    assert metrics["high_20d"] == 11.0
    assert metrics["low_20d"] == 9.0
    assert metrics["level_structure"]["completed_bar_counts"]["1D"] == 20


def test_strategy_daily_metrics_ignore_future_open_unordered_and_exact_duplicate_bars():
    completed = [
        {
            "date": f"2026-04-{index + 1:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 100_000,
        }
        for index in range(20)
    ]
    cutoff = datetime(2026, 4, 21, 19, 0, tzinfo=timezone.utc)

    def metrics(rows):
        return _strategy_daily_history_metrics(
            rows,
            price=10.5,
            day_open=10.0,
            day_high=10.8,
            day_low=9.8,
            day_volume=50_000,
            now_utc=cutoff,
        )

    baseline = metrics(completed)
    running = {
        "date": "2026-04-21",
        "open": 10.0,
        "high": 999.0,
        "low": 1.0,
        "close": 900.0,
        "volume": 9_000_000,
    }
    future = {**running, "date": "2026-04-22"}
    unordered = list(reversed([*completed, completed[6].copy(), running, future]))
    augmented = metrics(unordered)

    for key in (
        "high_20d", "low_20d", "avg_vol20", "atr14",
        "completed_bars", "baseline_bars", "breakout_20d_pct",
    ):
        assert augmented[key] == baseline[key]


def test_strategy_daily_metrics_drop_both_sides_of_conflicting_duplicate_session():
    completed = [
        {
            "date": f"2026-04-{index + 1:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 100_000,
        }
        for index in range(20)
    ]
    conflicting = {
        **completed[-1],
        "high": 500.0,
        "close": 400.0,
    }
    metrics = _strategy_daily_history_metrics(
        [*completed, conflicting],
        price=10.5,
        day_open=10.0,
        day_high=10.8,
        day_low=9.8,
        day_volume=50_000,
        now_utc=datetime(2026, 4, 21, 19, 0, tzinfo=timezone.utc),
    )

    assert metrics["completed_bars"] == 19
    assert metrics["baseline_bars"] == 19
    assert metrics["high_20d"] == 11.0
    assert metrics["atr14"] == 2.0


def test_stock_daily_atr_is_prefix_invariant_to_open_and_future_sessions():
    completed = [
        {
            "date": f"2026-04-{index + 1:02d}",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100_000,
        }
        for index in range(20)
    ]
    cutoff = datetime(2026, 4, 21, 19, 0, tzinfo=timezone.utc)
    baseline = _completed_stock_daily_atr(completed, as_of=cutoff)
    open_session = {
        "date": "2026-04-21",
        "open": 100.0,
        "high": 1_000.0,
        "low": 1.0,
        "close": 900.0,
        "volume": 9_000_000,
    }
    future_session = {**open_session, "date": "2026-04-22"}

    assert baseline == 2.0
    assert _completed_stock_daily_atr(
        [*completed, open_session, future_session],
        as_of=cutoff,
    ) == baseline


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


def test_early_mover_requires_fresh_5m_execution_for_every_holding_horizon(monkeypatch):
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

    assert "early_mover_wait_entry_confirmation" in _early_mover_long_rule_reasons(row)

    row["_alert_horizon"] = "intraday"
    assert "early_mover_wait_entry_confirmation" in _early_mover_long_rule_reasons(row)

    row["_alert_horizon"] = "swing"
    row["risk_flags"] = []

    row["execution_trigger_ok"] = "false"
    assert "early_mover_wait_entry_confirmation" in _early_mover_long_rule_reasons(row)

    row["execution_trigger_ok"] = False
    row["trade_setup"] = {"execution_trigger_ok": True}
    row["intraday_trigger"] = {
        "ok": True,
        "timeframe": "5m",
        "checked_at": 1_000_000,
        "last_candle_closed_at": 999_940,
        "execution_data_age_seconds": 60,
    }
    assert "early_mover_wait_entry_confirmation" in _early_mover_long_rule_reasons(row)

    row["execution_trigger_ok"] = True
    row.pop("intraday_trigger")
    assert "early_mover_wait_entry_confirmation" in _early_mover_long_rule_reasons(row)

    row["intraday_trigger"] = {
        "ok": True,
        "timeframe": "5m",
        "checked_at": 1_000_000,
        "last_candle_closed_at": 999_940,
        "execution_data_age_seconds": 60,
    }
    assert "early_mover_wait_entry_confirmation" not in _early_mover_long_rule_reasons(row)


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
