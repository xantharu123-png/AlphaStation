import pytest

from modules import backtests as bt


def _simulate(bars, direction="LONG", **kwargs):
    sign = 1 if direction == "LONG" else -1
    args = dict(bars=bars, start_idx=0, max_hold=1, direction=direction, entry_price=100,
                stop_price=100-sign*5, tp1_price=100+sign*10, tp2_price=100+sign*20, fee_pct=0)
    args.update(kwargs)
    return bt.simulate_50_50_daily_exit(**args)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_open_target_executes_before_later_stop(direction):
    bar = dict(date="2026-08-25", open=125, high=127, low=94, close=105)
    if direction == "SHORT":
        bar.update(open=75, high=106, low=73, close=95)
    result = _simulate([bar], direction)
    assert result["exit_reason"] == "BLENDED_TP"
    assert result["r_multiple"] == result["r_multiple_upper"] == 3
    assert result["intrabar_ambiguous"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_open_tp1_then_reversal_credits_the_completed_partial(direction):
    bar = dict(date="2026-08-25", open=112, high=113, low=94, close=105)
    if direction == "SHORT":
        bar.update(open=88, high=106, low=87, close=95)
    result = _simulate([bar], direction)
    assert result["exit_reason"] == result["exit_reason_upper"] == "TP1_STOP"
    assert result["r_multiple"] == result["r_multiple_upper"] == 1
    assert result["intrabar_ambiguous"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_close_after_tp1_forces_runner_stop_even_in_favorable_bound(direction):
    bars = [dict(date="2026-08-25", open=100, high=112, low=98, close=99),
            dict(date="2026-08-26", open=121, high=125, low=120, close=123)]
    if direction == "SHORT":
        bars = [dict(date="2026-08-25", open=100, high=102, low=88, close=101),
                dict(date="2026-08-26", open=79, high=80, low=75, close=77)]
    result = _simulate(bars, direction, max_hold=2)
    assert result["exit_reason"] == result["exit_reason_upper"] == "TP1_STOP"
    assert result["r_multiple"] == result["r_multiple_upper"] == 1
    assert result["exit_date"] == result["exit_date_upper"] == "2026-08-25"


def test_data_end_does_not_liquidate_unfinished_trade():
    result = _simulate([dict(date="2026-08-25", open=100, high=102, low=99, close=101)], max_hold=20)
    assert result["outcome"] == "UNRESOLVED"
    assert result["r_multiple"] is None
    assert result["pnl_pct"] is None
    stats = bt.compute_backtest_stats([result])
    assert stats["total_decided"] == 0
    assert stats["unresolved"] == 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_eod_runner_pays_the_same_exit_slippage_as_stop_target(direction):
    result = _simulate([dict(date="2026-08-25", open=100, high=102, low=98, close=101)],
                       direction, exit_slippage=.001)
    exit_price = 101 * (.999 if direction == "LONG" else 1.001)
    expected = (exit_price-100)/5 * (1 if direction == "LONG" else -1)
    assert result["r_multiple"] == pytest.approx(expected, abs=.00005)
    assert result["evaluation_model_version"] == "daily-causal-exits-v2"


@pytest.mark.parametrize("method,direction,opening", [("stop_breakout","LONG",100),
    ("market_at_signal","SHORT",100), ("limit_pullback","SHORT",100)])
def test_daily_bi_plan_preserves_canonical_levels_and_records_execution_variant(method,direction,opening):
    sign = 1 if direction == "LONG" else -1
    plan = dict(Entry=100,StopLoss=100-sign*5,TP1=100+sign*10,TP2=100+sign*20,
                entry_method=method,plan_version="shared-test")
    bar = dict(date="2026-08-25",open=opening,high=102,low=98,close=100)
    result = bt._simulate_bi_plan_daily([bar],0,plan,direction,horizon_bars=1)
    assert result["entry_target"] == plan["Entry"]
    assert result["stop_target"] == plan["StopLoss"]
    assert result["tp1_target"] == plan["TP1"]
    assert result["tp2_target"] == plan["TP2"]
    assert result["entry_filled"]
    assert result["live_delivery_equivalent"] is False
    assert result["execution_model"] == "daily_next_session_50_50_be_after_tp1_v2"


def test_pending_bi_entry_at_data_end_is_not_a_filled_trade():
    plan = dict(Entry=100,StopLoss=95,TP1=110,TP2=120,entry_method="stop_breakout",plan_version="shared-test")
    result = bt._simulate_bi_plan_daily([dict(date="2026-08-25",open=98,high=99,low=97,close=98)],0,plan,"LONG")
    assert result["outcome"] == "UNRESOLVED"
    assert result["entry_filled"] is False
    stats = bt.compute_backtest_stats([result])
    assert stats["total_filled"] == stats["total_decided"] == 0


@pytest.mark.parametrize("bad", [{"high": None}, {"open": float("nan")}, {"low": 101}])
def test_bi_entry_path_rejects_invalid_future_ohlc_without_inventing_fill(bad):
    plan = dict(Entry=100, StopLoss=95, TP1=110, TP2=120, entry_method="stop_breakout", plan_version="test")
    bar = dict(date="2026-08-25", open=100, high=102, low=98, close=100)
    bar.update(bad)
    result = bt._simulate_bi_plan_daily([bar], 0, plan, "LONG")
    assert result["outcome"] == "UNRESOLVED"
    assert result["evaluation_status"] == "INVALID_OHLC"
    assert result["entry_filled"] is False


@pytest.mark.parametrize("bad", [{"close": None}, {"open": float("nan")}, {"high": 98}, {"low": 101}])
def test_malformed_ohlc_cannot_create_a_decided_backtest_result(bad):
    bar = dict(date="2026-08-25",open=100,high=102,low=98,close=100)
    bar.update(bad)
    result = _simulate([bar])
    assert result["outcome"] == "UNRESOLVED"
    assert result["evaluation_status"] == "INVALID_OHLC"
    assert result["r_multiple"] is None


@pytest.mark.parametrize("cost", [{"fee_pct": -1}, {"exit_slippage": 1}, {"fee_pct": float("nan")}])
def test_invalid_cost_assumptions_do_not_invent_profit(cost):
    assert _simulate([dict(date="2026-08-25",open=100,high=102,low=98,close=100)], **cost) is None
