"""Adversarial offline checks for historical execution, not market performance."""
from copy import deepcopy

import pytest

from modules import backtests as bt


def _bar(day="2026-09-01", **values):
    return {"date": day, "open": 100.0, "high": 102.0, "low": 98.0,
            "close": 100.0, "volume": 100_000, **values}


def _exit(bars, direction="LONG", **values):
    sign = 1 if direction == "LONG" else -1
    return bt.simulate_50_50_daily_exit(
        bars=bars, start_idx=values.pop("start_idx", 0), max_hold=values.pop("max_hold", 1),
        direction=direction, entry_price=100, stop_price=100 - sign * 5,
        tp1_price=100 + sign * 10, tp2_price=100 + sign * 20, fee_pct=0, **values)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("net_pct", [-0.00004, 0.00004])
def test_daily_path_preserves_sign_and_precision_below_four_decimals(direction, net_pct):
    sign = 1 if direction == "LONG" else -1
    close = 100 * (1 + sign * net_pct / 100)
    result = _exit([_bar(close=close)], direction)
    assert result["pnl_pct"] == pytest.approx(net_pct, abs=1e-12)
    assert result["r_multiple"] == pytest.approx(net_pct / 5, abs=1e-12)
    assert result["exit_price"] == pytest.approx(close, abs=1e-12)
    assert result["is_winner"] is (net_pct > 0)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("net_pct", [-0.004, 0.004])
def test_rule_engine_does_not_round_net_pnl_or_r_before_aggregation(direction, net_pct):
    sign = 1 if direction == "long" else -1
    entry = 100 * (1 + sign * .0005)
    close = entry * (1 + sign * (.2 + net_pct) / 100) / (1 - sign * .0005)
    bars = [_bar(), _bar("2026-09-02", close=close)]
    original = deepcopy(bars)
    strategy = {"direction": direction, "entry": "next_open", "stop_pct": .05,
                "tp1_rr": 1.5, "tp2_rr": 2.5, "max_hold_days": 1}
    result = bt.simulate_trade(bars, 0, strategy)
    assert result["pnl_pct"] == pytest.approx(net_pct, abs=1e-10)
    assert result["r_multiple"] == pytest.approx(net_pct / 5, abs=1e-10)
    assert result["is_winner"] is (net_pct > 0)
    assert bars == original


def test_rule_engine_preserves_sub_cent_entry_stop_and_targets():
    bars = [_bar(open=.12823, high=.13, low=.126, close=.12823),
            _bar("2026-09-02", open=.12823, high=.13, low=.126, close=.129)]
    strategy = {"direction": "long", "entry": "next_open", "stop_pct": .05,
                "tp1_rr": 1.5, "tp2_rr": 2.5, "max_hold_days": 1}
    result = bt.simulate_trade(bars, 0, strategy)
    entry = .12823 * 1.0005
    assert result["entry_price"] == pytest.approx(entry)
    assert result["stop_price"] == pytest.approx(entry * .95)
    assert result["tp1_price"] == pytest.approx(entry * 1.075)
    assert result["tp2_price"] == pytest.approx(entry * 1.125)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("key", ["open", "high", "low", "close"])
def test_boolean_daily_ohlc_is_unknown_not_a_price(direction, key):
    # A 1-dollar price is coherent; a boolean that coerces to 1 is not a price.
    sign = 1 if direction == "LONG" else -1
    bar = _bar(open=1, high=1, low=1, close=1)
    bar[key] = True
    result = bt.simulate_50_50_daily_exit([bar], 0, 1, direction,
                                       1, 1 - sign * .05, 1 + sign * .1, 1 + sign * .2,
                                       fee_pct=0)
    assert result["outcome"] == "UNRESOLVED"
    assert result["evaluation_status"] == "INVALID_OHLC"
    assert result["pnl_pct"] is None


@pytest.mark.parametrize("field", ["is_closed", "complete", "completed", "final"])
def test_pending_bi_entry_does_not_claim_a_fill_from_an_explicitly_open_bar(field):
    plan = {"Entry": 100, "StopLoss": 95, "TP1": 110, "TP2": 120,
            "entry_method": "stop_breakout", "plan_version": "test"}
    result = bt._simulate_bi_plan_daily([_bar(**{field: False})], 0, plan, "LONG", horizon_bars=1)
    assert result["outcome"] == "UNRESOLVED"
    assert result["entry_filled"] is False
    assert result["evaluation_status"] == "INCOMPLETE_DAILY_BAR"


@pytest.mark.parametrize("direction,method,opening,high,low,close", [
    ("SHORT", "stop_breakout", 105, 107, 104, 105),
    ("LONG", "limit_pullback", 105, 107, 104, 105),
])
def test_directional_bi_pending_order_does_not_fill_without_its_trigger(
        direction, method, opening, high, low, close):
    sign = 1 if direction == "LONG" else -1
    plan = {"Entry": 100, "StopLoss": 100 - sign * 5, "TP1": 100 + sign * 10,
            "TP2": 100 + sign * 20, "entry_method": method, "plan_version": "test"}
    # Keep the short open below its stop so the independent invalidation guard
    # does not decide the test instead of the untriggered order.
    if direction == "SHORT":
        plan["StopLoss"] = 110
    result = bt._simulate_bi_plan_daily([_bar(open=opening, high=high, low=low, close=close)],
                                      0, plan, direction, horizon_bars=1)
    assert result["entry_filled"] is False
    assert result["outcome"] == "NO_FILL"
    assert result["evaluation_status"] == "ENTRY_NOT_REACHED"


@pytest.mark.parametrize("direction,method,expected_fill", [
    ("SHORT", "stop_breakout", 99.9), ("LONG", "limit_pullback", 100),
])
def test_directional_bi_intrabar_order_fills_at_the_trigger_not_the_prior_open(direction, method, expected_fill):
    sign = 1 if direction == "LONG" else -1
    plan = {"Entry": 100, "StopLoss": 100 - sign * 5, "TP1": 100 + sign * 10,
            "TP2": 100 + sign * 20, "entry_method": method, "plan_version": "test"}
    if direction == "SHORT":
        plan.update(StopLoss=108, TP1=85, TP2=75)
    result = bt._simulate_bi_plan_daily([_bar(open=104, high=106, low=99, close=101)],
                                      0, plan, direction, horizon_bars=1)
    assert result["entry_filled"] is True
    assert result["actual_entry"] == pytest.approx(expected_fill)


def test_negative_start_index_cannot_replay_the_last_future_bar_as_first_bar():
    assert _exit([_bar(close=101)], start_idx=-1) is None


def test_summary_recomputes_winner_from_net_result_not_a_contradictory_flag():
    row = {"outcome": "EOD", "exit_reason": "EOD", "entry_filled": True,
           "pnl_pct": -.004, "r_multiple": -.0008, "is_winner": True, "bars_held": 1}
    stats = bt.compute_backtest_stats([row])
    assert stats["win_rate"] == 0
    assert stats["winners"] == 0
    assert stats["losers"] == 1
    assert stats["profit_factor"] == 0


@pytest.mark.parametrize("pnl", [None, float("nan"), float("inf"), True])
def test_summary_does_not_publish_unknown_net_result_as_a_decided_trade(pnl):
    row = {"outcome": "EOD", "exit_reason": "EOD", "entry_filled": True,
           "pnl_pct": pnl, "r_multiple": 1, "is_winner": True, "bars_held": 1}
    stats = bt.compute_backtest_stats([row])
    assert stats["total_input_trades"] == 1
    assert stats["total_decided"] == 0
    assert stats["unresolved"] == 1
    assert stats["performance_available"] is False
    assert stats["data_quality"]["status"] == "PARTIAL"


def test_known_net_pnl_without_r_keeps_pnl_but_does_not_invent_r():
    row = {"outcome": "EOD", "exit_reason": "EOD", "entry_filled": True,
           "pnl_pct": 2, "r_multiple": None, "is_winner": True, "bars_held": 1}
    stats = bt.compute_backtest_stats([row])
    assert stats["total_decided"] == 1
    assert stats["win_rate"] == 100
    assert stats["avg_pnl"] == 2
    assert stats["avg_r"] is None
    assert stats["total_r"] is None
    assert stats["profit_factor"] is None
    assert stats["avg_r_upper"] is None


def test_missing_upper_bound_is_not_replaced_by_a_zero_return():
    row = {"outcome": "EOD", "pnl_pct": 2, "r_multiple": .4,
           "pnl_pct_upper": None, "r_multiple_upper": None, "intrabar_ambiguous": True}
    stats = bt.backtest_uncertainty_metrics([row])
    assert stats["avg_pnl_upper"] is None
    assert stats["total_pnl_upper"] is None
    assert stats["win_rate_upper"] is None
    assert stats["avg_r_upper"] is None
    assert stats["total_r_upper"] is None


def test_summary_preserves_small_net_returns_until_display():
    row = {"outcome": "EOD", "exit_reason": "EOD", "entry_filled": True,
           "pnl_pct": .004, "r_multiple": .0008, "is_winner": True, "bars_held": 1}
    stats = bt.compute_backtest_stats([row])
    assert stats["avg_pnl"] == pytest.approx(.004)
    assert stats["avg_r"] == pytest.approx(.0008)
    assert stats["total_r"] == pytest.approx(.0008)
    assert stats["avg_pnl_upper"] == pytest.approx(.004)
    assert stats["total_r_upper"] == pytest.approx(.0008)


def test_empty_cohort_keeps_partial_provider_quality_without_fabricated_trades():
    trades = bt._BacktestTradeList()
    trades.data_quality = {"status": "PARTIAL", "excluded_invalid_bars": 1,
                           "invalid_dates": ["2026-09-01"], "unavailable_tickers": []}
    stats = bt.compute_backtest_stats(trades)
    assert stats["total_input_trades"] == 0
    assert stats["total_decided"] == 0
    assert stats["data_quality"]["status"] == "PARTIAL"
    assert stats["data_quality"]["excluded_invalid_bars"] == 1
    assert stats["data_quality"]["invalid_dates"] == ["2026-09-01"]


def test_full_universe_loader_carries_provider_quality_into_empty_strategy_reports(monkeypatch):
    # The historical request can yield no matching signals while a rejected
    # provider row still makes coverage partial. Neither fact invents a trade.
    bars = bt._BacktestTradeList([_bar()])
    bars.data_quality = {"status": "PARTIAL", "excluded_invalid_bars": 1,
                         "invalid_dates": ["2026-09-02"], "unavailable_tickers": []}
    monkeypatch.setattr(bt, "fetch_backtest_daily_data", lambda *_args: bars)
    results = bt.run_full_backtest("offline", strategies=["Breakout Long"], months=3, tickers=["QA"])
    stats = bt.compute_backtest_stats(results["Breakout Long"])
    assert stats["total_input_trades"] == 0
    assert stats["data_quality"]["status"] == "PARTIAL"
    assert stats["data_quality"]["excluded_invalid_bars"] == 1
    assert stats["data_quality"]["invalid_dates"] == ["2026-09-02"]
    assert stats["data_quality"]["unavailable_tickers"] == ["QA"]


def test_plain_trade_list_keeps_repeated_cohort_quality_without_multiplying_exclusions():
    quality = {"status": "PARTIAL", "excluded_invalid_bars": 1,
               "invalid_dates": ["2026-09-02"]}
    rows = [{"outcome": "EOD", "exit_reason": "EOD", "entry_filled": True,
             "pnl_pct": 2, "r_multiple": .4, "is_winner": True, "bars_held": 1,
             "data_quality": deepcopy(quality)} for _ in range(2)]
    stats = bt.compute_backtest_stats(rows)
    assert stats["total_decided"] == 2
    assert stats["data_quality"]["status"] == "PARTIAL"
    assert stats["data_quality"]["excluded_invalid_bars"] == 1
    assert stats["data_quality"]["invalid_dates"] == ["2026-09-02"]


@pytest.mark.parametrize("scope", [
    {"total_trades": 0},
    {"total_trades": 2, "total_decided": 0},
    {"total_trades": 2, "error": "invalid_history"},
])
def test_unavailable_performance_nulls_all_model_return_and_drawdown_aliases(scope):
    result = bt.limit_backtest_report({"compounded_return": 3.0,
        "trade_sequence_compounded_return_pct": 3.0, "trade_sequence_max_drawdown_pct": 0.0,
        "max_drawdown": 0.0, **scope})
    assert result["performance_available"] is False
    for key in ("compounded_return", "trade_sequence_compounded_return_pct",
                "trade_sequence_max_drawdown_pct", "max_drawdown"):
        assert result[key] is None
