from datetime import date, timedelta
import inspect

import api
import modules.analysis as analysis_module
import modules.backtests as backtests
import modules.patterns as patterns_module
import modules.scanners as scanner_module
from modules.scanners import _biotech_readout_sort_key, _biotech_readout_timing_weight
from modules.analysis import (
    _historical_relative_volume,
    calculate_breakout_timing,
    calculate_gap_timing,
    calculate_insider_timing,
    calculate_ma_bounce_timing,
    calculate_reversal_timing,
)
from modules.patterns import _covered_volume_baseline, _relative_volume_state, analyze_candles
from modules.performance_metrics import profit_factor_metrics
from modules.trade_health import calculate_trade_health
from modules.volume_analysis import find_volume_voids_for_chart
from modules.volume_metrics import completed_bar_rvol, historical_volume_baseline


def _bar(day, *, open_, high, low, close, volume=100_000):
    return {
        "date": day,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _prev_high_strategy():
    return {
        "direction": "long",
        "entry": "prev_high",
        "stop_pct": 0.05,
        "tp1_rr": 1.0,
        "tp2_rr": 3.0,
        "max_hold_days": 1,
    }


def test_volume_baseline_does_not_reach_past_the_requested_time_window():
    baseline = historical_volume_baseline(
        [100, 200, 0, None],
        lookback=2,
        minimum_periods=1,
    )

    assert baseline is None


def test_strategy_rvol_requires_the_same_minimum_history_as_its_baseline():
    rvol = completed_bar_rvol(
        500,
        [100, 0, None],
        lookback=3,
        minimum_periods=2,
    )

    assert rvol == 0.0


def test_all_api_atr_helpers_use_wilder_smoothing_without_partial_warmup():
    import api
    from modules.vrvp_levels import calculate_wilder_atr

    bars = []
    close = 100.0
    for index in range(30):
        close += 0.4 if index % 3 else -0.2
        bars.append({
            "open": close - 0.1,
            "high": close + 1.0 + index * 0.03,
            "low": close - 0.8,
            "close": close,
            "volume": 1_000 + index,
        })

    expected14 = calculate_wilder_atr(bars, period=14)
    expected18 = calculate_wilder_atr(bars, period=18)
    assert api._calc_recent_atr(bars, 14) == expected14
    assert api._ce_atr(bars, 18) == expected18
    assert api._bt_atr(bars, len(bars) - 1, 14) == expected14
    assert api._bt_atr(bars[:14], 13, 14) == 0.0


def test_bi_and_biotech_backtests_use_canonical_wilder_atr():
    bi_source = inspect.getsource(backtests.run_bi_v2_backtest)
    biotech_source = inspect.getsource(backtests.run_biotech_backtest)

    assert "calculate_wilder_atr(window, period=5)" in bi_source
    assert "calculate_wilder_atr(window, period=10)" in biotech_source
    assert "tr_values" not in bi_source
    assert "tr_values" not in biotech_source


def test_live_scanner_and_chart_paths_share_canonical_wilder_atr():
    autotrader_source = inspect.getsource(scanner_module.autotrader_scan_once)
    bi_source = inspect.getsource(scanner_module._bi_background_scan)
    wyckoff_source = inspect.getsource(analysis_module.find_wyckoff_for_chart)
    detail_source = inspect.getsource(api.get_ticker_detail)
    short_bonus_source = inspect.getsource(analysis_module.calculate_short_bonus_signals)
    breakout_source = inspect.getsource(patterns_module.analyze_breakout_imminent)

    assert "calculate_wilder_atr(analysis_window, period=5)" in autotrader_source
    assert "_tr_vals" not in autotrader_source
    assert "calculate_wilder_atr(bars, period=5)" in bi_source
    assert "or (bars[-1]" not in bi_source
    assert "calculate_atr_14(ohlcv_data)" in wyckoff_source
    assert "atr_vals" not in wyckoff_source
    assert "calculate_wilder_atr(chronological_bars, period=14)" in detail_source
    assert "tr_values" not in detail_source
    assert "calculate_atr_14(bars[:idx])" in short_bonus_source
    assert "bars[k][\"high\"] - bars[k][\"low\"]" not in short_bonus_source
    assert "calculate_atr_14(bars)" in breakout_source
    assert "atr_ob = sum" not in breakout_source
    assert "calculate_wilder_atr(_session_bars, period=10)" in bi_source


def test_pattern_volume_baseline_requires_sixty_percent_real_coverage():
    sparse = [100.0] * 5 + [0.0] * 5
    covered = [100.0] * 6 + [0.0] * 4

    assert _covered_volume_baseline(sparse) == 0.0
    assert _covered_volume_baseline(covered) == 100.0


def test_prev_high_exact_touch_fills_before_slippage_is_applied():
    bars = [
        _bar("2026-01-01", open_=98, high=100, low=97, close=99),
        _bar("2026-01-02", open_=99, high=99.5, low=98, close=99),
        _bar("2026-01-03", open_=99, high=100, low=99, close=100),
    ]

    trade = backtests.simulate_trade(bars, 1, _prev_high_strategy())

    assert trade is not None
    assert trade["entry_trigger"] == 100.0
    assert trade["entry_fill_basis"] == "trigger_touch"
    assert trade["entry_price"] == 100.05
    assert trade["entry_date"] == "2026-01-03"


def test_prev_high_gap_fills_at_open_not_at_stale_trigger():
    bars = [
        _bar("2026-01-01", open_=98, high=100, low=97, close=99),
        _bar("2026-01-02", open_=99, high=99.5, low=98, close=99),
        _bar("2026-01-03", open_=105, high=106, low=104, close=105),
    ]

    trade = backtests.simulate_trade(bars, 1, _prev_high_strategy())

    assert trade is not None
    assert trade["entry_fill_basis"] == "gap_open_above_trigger"
    assert trade["entry_price"] == 105.05
    assert trade["entry_price"] > trade["entry_trigger"]


def test_at_close_entry_date_is_signal_date_not_next_bar():
    strategy = {
        "direction": "long",
        "entry": "at_close",
        "stop_pct": 0.05,
        "tp1_rr": 1.0,
        "tp2_rr": 3.0,
        "max_hold_days": 1,
    }
    bars = [
        _bar("2026-01-01", open_=99, high=101, low=98, close=100),
        _bar("2026-01-02", open_=100, high=102, low=99, close=101),
    ]

    trade = backtests.simulate_trade(bars, 0, strategy)

    assert trade is not None
    assert trade["entry_date"] == "2026-01-01"
    assert trade["entry_fill_basis"] == "at_close"


def _stats_trade(reason, r_multiple, *, winner, tp1_hit=False):
    return {
        "exit_reason": reason,
        "r_multiple": r_multiple,
        "pnl_pct": r_multiple * 5,
        "is_winner": winner,
        "bars_held": 2,
        "tp1_hit": tp1_hit,
    }


def test_backtest_stats_maps_current_blended_exit_reasons():
    trades = [
        _stats_trade("BLENDED_TP", 2.0, winner=True, tp1_hit=True),
        _stats_trade("TP1_STOP", 0.5, winner=True, tp1_hit=True),
        _stats_trade("TP1+EOD", 0.7, winner=True, tp1_hit=True),
        _stats_trade("STOP", -1.0, winner=False),
    ]

    stats = backtests.compute_backtest_stats(trades)

    assert stats["tp1_rate"] == 75.0
    assert stats["tp2_rate"] == 25.0
    assert stats["stop_rate"] == 50.0
    assert stats["full_stop_rate"] == 25.0
    assert stats["post_tp1_stop_rate"] == 25.0
    assert stats["eod_rate"] == 25.0


def test_profit_factor_without_losses_is_unbounded_not_magic_number():
    summary = profit_factor_metrics(12.5, 0)
    stats = backtests.compute_backtest_stats(
        [_stats_trade("BLENDED_TP", 2.0, winner=True, tp1_hit=True)]
    )

    assert summary["value"] is None
    assert summary["display"] == "INF"
    assert summary["unbounded"] is True
    assert stats["profit_factor"] is None
    assert stats["profit_factor_display"] == "INF"
    assert stats["avg_loss"] == 0


def test_classic_backtest_keeps_same_day_signals_from_different_strategies(monkeypatch):
    start = date(2026, 1, 1)
    bars = [
        _bar(
            (start + timedelta(days=idx)).isoformat(),
            open_=10,
            high=11,
            low=9,
            close=10,
        )
        for idx in range(32)
    ]
    rules = {
        "Strategy A": {"signal": "a", "min_price": 1},
        "Strategy B": {"signal": "b", "min_price": 1},
    }

    monkeypatch.setattr(backtests, "BACKTEST_STRATEGY_RULES", rules)
    monkeypatch.setattr(backtests, "fetch_backtest_daily_data", lambda *args: bars)
    monkeypatch.setattr(
        backtests,
        "compute_daily_metrics",
        lambda _bars, _idx: {"price": 10, "change_pct": 2, "rvol": 2},
    )
    monkeypatch.setattr(backtests, "check_signal", lambda _metrics, _signal: True)
    monkeypatch.setattr(
        backtests,
        "simulate_trade",
        lambda _bars, idx, _strategy: {
            "signal_date": _bars[idx]["date"],
            "entry_date": _bars[idx]["date"],
            "exit_date": _bars[idx]["date"],
            "pnl_pct": 1,
            "r_multiple": 0.2,
            "is_winner": True,
            "bars_held": 1,
            "exit_reason": "EOD",
        },
    )

    result = backtests.run_full_backtest(
        "key",
        strategies=list(rules),
        tickers=["TEST"],
        months=24,
    )

    assert result["Strategy A"]
    assert result["Strategy B"]
    assert len(result["Strategy A"]) == len(result["Strategy B"])
    assert result["Strategy A"][0]["signal_date"] == result["Strategy B"][0]["signal_date"]


def test_relative_volume_excludes_current_bar_and_fails_closed():
    history = [100.0] * 19 + [200.0]

    assert historical_volume_baseline(history, lookback=20) == 105.0
    assert completed_bar_rvol(210, history, lookback=20) == 2.0
    assert completed_bar_rvol(210, [0, None, float("nan")], lookback=20) == 0.0


def test_relative_volume_requires_minimum_history_and_never_uses_future_bars():
    volumes = [100.0] * 5 + [250.0, 10_000.0]

    assert historical_volume_baseline(volumes[:4], minimum_periods=5) is None
    assert completed_bar_rvol(250, volumes[:4], minimum_periods=5) == 0.0
    assert _relative_volume_state(volumes, 5, minimum_periods=5) == 2.5
    assert _relative_volume_state([0, None, float("nan"), 200], 3) is None


def test_backtest_universe_liquidity_uses_only_information_at_test_start():
    bars = [_bar(f"2026-01-{day:02d}", open_=10, high=11, low=9, close=10, volume=100)
            for day in range(1, 21)]
    bars.extend(
        _bar(f"2026-02-{day:02d}", open_=10, high=11, low=9, close=10, volume=10_000_000)
        for day in range(1, 6)
    )

    average = backtests._initial_universe_average_volume(bars, window_size=20)

    assert average == 100.0


def test_backtest_universe_liquidity_rejects_too_short_history():
    bars = [
        _bar(f"2026-01-{day:02d}", open_=10, high=11, low=9, close=10, volume=100)
        for day in range(1, 10)
    ]

    assert backtests._initial_universe_average_volume(bars, window_size=20) is None


def test_biotech_readouts_sort_future_before_unconfirmed_past_dates():
    readouts = [
        {"readout_category": "UPCOMING", "days_until_readout": 45},
        {"readout_category": "OVERDUE_STALE", "days_until_readout": -220},
        {"readout_category": "IMMINENT", "days_until_readout": 1},
        {"readout_category": "OVERDUE", "days_until_readout": -10},
    ]

    ordered = sorted(readouts, key=_biotech_readout_sort_key)

    assert [item["days_until_readout"] for item in ordered] == [1, 45, -10, -220]
    assert _biotech_readout_timing_weight("IMMINENT") == 3.0
    assert _biotech_readout_timing_weight("UPCOMING") == 1.5
    assert _biotech_readout_timing_weight("OVERDUE") == 0.0
    assert _biotech_readout_timing_weight("OVERDUE_STALE") == 0.0


def test_new_listing_age_sort_keeps_zero_hour_listing_and_rejects_invalid_age():
    assert api._new_listing_age_sort_value(0) == 0.0
    assert api._new_listing_age_sort_value("2.5") == 2.5
    assert api._new_listing_age_sort_value(None) == float("inf")
    assert api._new_listing_age_sort_value(-1) == float("inf")


def test_orb_volume_score_fails_closed_without_complete_positive_volume():
    assert api._orb_volume_score(True, 0, 100, True) == (0, "Vol N/A")
    assert api._orb_volume_score(True, 200, 0, True) == (0, "Vol N/A")
    assert api._orb_volume_score(True, 200, 100, False) == (0, "Vol N/A")
    assert api._orb_volume_score(True, 200, 100, True) == (25, "Vol 2x+")
    assert api._orb_volume_score(False, 100, 100, True) == (5, "Vol unconfirmed")


def test_wyckoff_relative_volume_requires_real_historical_baseline():
    assert _historical_relative_volume([0.0] * 10, 9) is None
    assert _historical_relative_volume([100.0] * 5 + [200.0], 5) == 2.0


def test_candle_analysis_does_not_treat_missing_prior_volume_as_accumulation():
    candles = []
    for idx in range(10):
        close = 10.0 + idx * 0.1
        candles.append({
            "o": close - 0.05,
            "h": close + 0.1,
            "l": close - 0.1,
            "c": close,
            "v": 0 if idx < 5 else 100_000,
        })

    result = analyze_candles(candles)

    assert result["volume_trend"] == "neutral"
    assert result["breakout_ready"] is False


def test_volume_voids_require_positive_volume_and_price_range():
    zero_volume = [
        {"high": 11 + idx * 0.01, "low": 10 + idx * 0.01, "volume": 0}
        for idx in range(10)
    ]
    flat_prices = [
        {"high": 10, "low": 10, "volume": 100_000}
        for _ in range(10)
    ]

    assert find_volume_voids_for_chart(zero_volume) == []
    assert find_volume_voids_for_chart(flat_prices) == []
    assert find_volume_voids_for_chart(zero_volume, num_bins=0) == []


def test_backtest_drawdown_is_chronological_not_input_order():
    trades = [
        {"ticker": "SECOND", "entry_date": "2026-01-02", "pnl_pct": 100, "r_multiple": 2},
        {"ticker": "FIRST", "entry_date": "2026-01-01", "pnl_pct": -20, "r_multiple": -1},
        {"ticker": "THIRD", "entry_date": "2026-01-03", "pnl_pct": -20, "r_multiple": -1},
    ]

    result = api._backtest_stats(trades, "TEST", "Strategy", 6)

    assert result["max_drawdown"] == 20.0
    assert [trade["ticker"] for trade in result["trades"]] == ["FIRST", "SECOND", "THIRD"]


def test_oos_split_keeps_all_same_day_trades_on_one_side():
    start = date(2026, 1, 1)
    trades = []
    for day_index in range(20):
        trade_date = (start + timedelta(days=day_index)).isoformat()
        for suffix in ("A", "B"):
            trades.append({
                "ticker": f"{day_index}{suffix}",
                "entry_date": trade_date,
                "pnl_pct": 1.0,
                "r_multiple": 0.2,
            })

    summary = api._bt_out_of_sample_summary(trades)

    assert summary["status"] == "pass"
    assert summary["same_date_leakage"] is False
    assert summary["in_sample"]["total_trades"] == 32
    assert summary["holdout"]["total_trades"] == 8


def test_oos_failure_and_missing_holdout_block_an_otherwise_approved_strategy():
    approved = {
        "status": "approved",
        "label": "FREIGEGEBEN",
        "tradable": True,
        "reasons": [],
    }
    failed = api._bt_apply_oos_verdict(
        approved,
        {
            "status": "fail",
            "robust": False,
            "holdout": {
                "avg_pnl": -0.2,
                "profit_factor_display": "0.80",
                "max_drawdown": 12,
            },
        },
    )
    open_validation = api._bt_apply_oos_verdict(
        approved,
        {"status": "insufficient_sample", "total_trades": 18},
    )

    assert failed["status"] == "validation_failed"
    assert failed["tradable"] is False
    assert open_validation["status"] == "validation_open"
    assert open_validation["tradable"] is False


def test_oos_pass_keeps_approved_and_nonapproved_verdicts_are_not_upgraded():
    approved = {"status": "approved", "tradable": True, "reasons": []}
    blocked = {"status": "blocked", "tradable": False, "reasons": ["weak"]}

    confirmed = api._bt_apply_oos_verdict(approved, {"status": "pass", "robust": True})
    still_blocked = api._bt_apply_oos_verdict(blocked, {"status": "pass", "robust": True})

    assert confirmed["status"] == "approved"
    assert confirmed["tradable"] is True
    assert "Holdout" in confirmed["reasons"][-1]
    assert still_blocked == blocked


def test_timing_models_do_not_award_missing_evidence():
    breakout = calculate_breakout_timing({})
    gap = calculate_gap_timing({"Chg%": 8.0}, is_gap_up=True)
    ma_bounce = calculate_ma_bounce_timing({})
    reversal = calculate_reversal_timing({"Chg%": 2.0}, is_long=True)

    assert breakout["score"] == 0
    assert breakout["evidence_complete"] is False
    assert gap["gap_pct"] is None
    assert gap["gap_direction_matches"] is False
    assert gap["rating"] != "GO"
    assert ma_bounce["score"] == 0
    assert ma_bounce["evidence_complete"] is False
    assert reversal["score"] <= 1
    assert reversal["evidence_complete"] is False


def test_missing_insider_data_awards_no_free_timing_points():
    result = calculate_insider_timing({})

    assert result["score"] == 0
    assert result["max_score"] == 5
    assert all(not factor["ok"] for factor in result["factors"])


def test_trade_health_uses_net_rr_when_execution_costs_are_available():
    row = {
        "ticker": "COST",
        "direction": "LONG",
        "current_price": 100,
        "entry": 100,
        "stop": 99,
        "tp1": 102,
        "tp2": 104,
        "execution_cost_pct": 1.2,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.85,
        "dollar_volume": 20_000_000,
    }

    health = calculate_trade_health(row, "crypto_early_mover")

    assert health["metrics"]["live_rr_gross"] == 3.0
    assert health["metrics"]["live_rr_net"] == 0.82
    assert health["metrics"]["live_rr"] == 0.82
    assert health["metrics"]["rr_cost_basis"] == "net"
    # Costs make the current entry untradeable. An intact high-volume setup may
    # remain a wait-for-better-entry candidate instead of being discarded.
    assert health["decision"] == "WAIT_FOR_CONTINUATION"
    assert health["decision"] != "TRADEABLE"


def test_stop_breach_zeroes_gross_and_net_live_rr():
    health = calculate_trade_health(
        {
            "ticker": "BROKEN",
            "direction": "LONG",
            "current_price": 98.5,
            "entry": 100,
            "stop": 99,
            "tp1": 102,
            "tp2": 104,
            "execution_cost_pct": 0.3,
            "rvol": 2,
            "dollar_volume": 20_000_000,
        },
        "stock_strategy",
    )

    assert health["decision"] == "NO_TRADE"
    assert health["metrics"]["live_rr"] == 0.0
    assert health["metrics"]["live_rr_gross"] == 0.0
    assert health["metrics"]["live_rr_net"] == 0.0
