from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules.backtests import evaluate_rule_signal, simulate_trade, compute_backtest_stats, _BacktestTradeList, _attach_backtest_data_quality
from modules.backtest_methodology import attach_backtest_methodology, describe_cached_backtest
from modules.momentum_daily_backtest import evaluate_daily_momentum
from modules.stock_momentum_contract import evaluate_momentum_breakout
from modules.strategies import BACKTEST_STRATEGY_RULES


def _daily_bars(count=65):
    day = datetime(2026, 1, 5, tzinfo=timezone.utc)
    rows = []
    while len(rows) < count:
        if day.weekday() < 5:
            rows.append({"date": day.date().isoformat(), "open": 100.0,
                         "high": 101.0, "low": 98.0, "close": 100.0, "volume": 1_000_000.0})
        day += timedelta(days=1)
    rows[30].update(high=106.0, low=99.0, close=105.0, volume=2_000_000.0)
    for row in rows[31:]:
        row.update(open=105.0, high=110.0, low=103.0, close=106.0)
    return rows


def test_daily_adapter_calls_identical_momentum_core_and_is_prefix_invariant():
    rows = _daily_bars()
    selected = evaluate_daily_momentum(rows, 30)
    assert selected is not None
    assert selected == evaluate_daily_momentum(rows[:31], 30)
    direct = evaluate_momentum_breakout(selected["history_metrics"], price=selected["price"],
                                       change_pct=selected["change_pct"], rvol=selected["rvol"],
                                       close_pos=selected["close_pos"])
    assert selected["momentum_selection"] == direct
    assert direct["breakout_type"] == "20D_HIGH_BREAKOUT"
    assert selected["live_equivalent"] is False
    assert evaluate_rule_signal(rows, 30, BACKTEST_STRATEGY_RULES["Breakout Long"]) == selected


def test_completed_daily_adapter_metrics_match_live_on_identical_input_prefix():
    rows = _daily_bars()
    # Unequal trailing closes expose an off-by-one change_5d baseline.
    rows[26].update(close=99.0)
    rows[25].update(close=98.5)
    selected = evaluate_daily_momentum(rows, 30)
    today = rows[30]
    live = api._strategy_daily_history_metrics(
        rows[:31], price=today["close"], day_open=today["open"], day_high=today["high"],
        day_low=today["low"], day_volume=today["volume"], symbol="TEST",
        now_utc=datetime.fromisoformat(today["date"]).replace(hour=23, tzinfo=timezone.utc),
    )
    assert selected is not None
    for key in ("history_ok", "high_10d", "high_20d", "ema20", "ema50", "rsi14", "change_5d"):
        expected = live[key]
        if expected is None:
            assert selected["history_metrics"][key] is None
        else:
            assert selected["history_metrics"][key] == pytest.approx(expected)
    assert selected["rvol"] == live["rvol20"]


def test_incomplete_holding_bar_cannot_manufacture_daily_exit():
    rows = _daily_bars()
    rows[33]["is_closed"] = False
    trade = simulate_trade(rows, 30, BACKTEST_STRATEGY_RULES["Breakout Long"])
    assert trade["outcome"] == "UNRESOLVED"
    assert trade["evaluation_status"] == "INCOMPLETE_DAILY_BAR"
    assert trade["r_multiple"] is None
    assert trade["roundtrip_fee_pct"] == 0.2
    assert trade["exit_slippage_fraction"] == 0.0005
    assert trade["model_provenance"]["cost_policy"]["round_trip_fee_bps"] == 20


@pytest.mark.parametrize("mutation", ["under_high", "low_rvol", "small_day", "open_bar", "bad_order",
                                      "missing_volume", "boolean_volume", "boolean_open", "conflicting_flags"])
def test_daily_momentum_does_not_invent_eligibility(mutation):
    rows = _daily_bars()
    if mutation == "under_high":
        rows[29]["close"] = 96
        rows[29]["low"] = 95
        rows[30].update(close=100.9)
    elif mutation == "low_rvol":
        rows[30]["volume"] = 1_400_000
    elif mutation == "small_day":
        rows[30]["close"] = 101.5
    elif mutation == "open_bar":
        rows[30]["is_closed"] = False
    elif mutation == "bad_order":
        rows[20], rows[21] = rows[21], rows[20]
    elif mutation == "boolean_volume":
        rows[29]["volume"] = True
    elif mutation == "boolean_open":
        rows[29]["open"] = True
    elif mutation == "conflicting_flags":
        rows[29].update(is_closed=True, final=" n ")
    else:
        rows[29].pop("volume")
    assert evaluate_daily_momentum(rows, 30) is None


def test_real_single_ticker_rule_engine_receives_rule_not_strategy_name(monkeypatch):
    rows = _daily_bars()
    provider = [{"t": int(datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc).timestamp() * 1000),
                 "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]}
                for row in rows]
    class Response:
        status_code = 200
        def json(self):
            return {"results": list(reversed(provider))}
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    result = api._run_backtest("TEST", "Momentum Breakout Long", 3)
    assert "error" not in result
    assert result["total_input_trades"] >= 1
    assert result["selection_model"] == "stock_momentum_daily_selection_v1"
    assert result["model_provenance"]["execution_rule"]["stop_pct"] == 0.05
    assert result["model_provenance"]["cost_policy"]["entry_slippage_bps"] == 5
    assert result["live_validation_eligible"] is False
    assert result["paper_autotrade_release_eligible"] is False


@pytest.mark.parametrize("mutation", ["missing_open", "null_open", "boolean_volume", "nan_high",
                                      "negative_volume", "missing_volume", "conflicting_flags"])
def test_api_does_not_fabricate_fills_from_invalid_provider_rows(monkeypatch, mutation):
    rows = _daily_bars()
    provider = [{"t": int(datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc).timestamp() * 1000),
                 "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]}
                for row in rows]
    if mutation == "missing_open":
        provider[31].pop("o")
    elif mutation == "null_open":
        provider[31]["o"] = None
    elif mutation == "boolean_volume":
        provider[31]["v"] = True
    elif mutation == "nan_high":
        provider[31]["h"] = float("nan")
    elif mutation == "negative_volume":
        provider[31]["v"] = -1
    elif mutation == "missing_volume":
        provider[31].pop("v")
    else:
        provider[31].update(is_closed=True, final=" n ")
    class Response:
        status_code = 200
        def json(self):
            return {"results": list(reversed(provider))}
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    result = api._run_backtest("TEST", "Momentum Breakout Long", 3)
    assert "error" not in result
    assert result["total_trades"] == 0
    assert result["unresolved"] == 1
    assert rows[31]["date"] in result["open_trades"][0]["missing_expected_sessions"]
    assert result["performance_available"] is False
    assert result["model_provenance"]["completed_bars"] == 64
    assert result["model_provenance"]["excluded_open_future_invalid_or_duplicate_bars"] == 1
    assert result["model_provenance"]["excluded_invalid_ohlcv_bars"] == (0 if mutation == "conflicting_flags" else 1)
    for key in ("total_return", "sum_pnl", "best_trade", "worst_trade"):
        assert result[key] is None


def test_no_data_api_response_and_empty_universe_still_disclose_model(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"results": []}
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    result = api._run_backtest("TEST", "Momentum Breakout Long", 3)
    assert result["error"]
    assert result["live_equivalent"] is False
    results = {"Breakout Long": _BacktestTradeList()}
    _attach_backtest_data_quality(results, unavailable_tickers=["TEST"])
    stats = compute_backtest_stats(results["Breakout Long"])
    assert stats["selection_model"] == "stock_momentum_daily_selection_v1"
    assert stats["model_provenance"]["selection_contract_version"] == 1
    assert stats["live_equivalent"] is False


def test_api_excludes_current_open_and_future_sessions_before_any_simulation(monkeypatch):
    rows = _daily_bars()
    cutoff = datetime.fromisoformat(rows[63]["date"]).replace(hour=17, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff if tz else cutoff.replace(tzinfo=None)
    provider = [{"t": int(datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc).timestamp() * 1000),
                 "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]}
                for row in rows]
    class Response:
        status_code = 200
        def json(self):
            return {"results": list(reversed(provider))}
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    result = api._run_backtest("TEST", "Momentum Breakout Long", 3)
    assert "error" not in result
    assert result["model_provenance"]["completed_daily_bars_only"] is True
    assert result["model_provenance"]["completed_bars"] == 63
    assert result["model_provenance"]["excluded_open_future_invalid_or_duplicate_bars"] == 2


def test_old_cache_is_not_relabelled_current_and_verdict_cannot_release_live():
    original = {"verdict": {"tradable": True, "status": "approved", "color": "green"}, "total_trades": 20}
    result = describe_cached_backtest(original, "Momentum Breakout Long")
    assert result["selection_model"] == "legacy_unversioned_proxy"
    assert result["model_provenance"]["selection_contract_version"] is None
    assert result["verdict"]["tradable"] is False
    assert result["verdict"]["status"] == "model_limited"
    assert result["verdict"]["color"] != "green"
    assert original["verdict"]["tradable"] is True
    current = attach_backtest_methodology(original, "Breakout Long", BACKTEST_STRATEGY_RULES["Breakout Long"])
    assert describe_cached_backtest(current)["model_provenance"] == current["model_provenance"]


def test_indicator_result_does_not_claim_fixed_stop_or_rule_engine_costs():
    result = attach_backtest_methodology({}, "sma_crossover")
    assert result["execution_model"] == "strategy_specific_daily_simulation"
    assert result["model_provenance"]["cost_policy"] is None


def test_unknown_engine_keeps_specialized_warnings_without_invented_timeframe():
    result = attach_backtest_methodology({
        "methodology_warnings": ["volume_spike_proxy_not_historical_catalyst", "current_static_universe_survivorship_bias"],
        "execution_model": "producer_specific_v2", "model_provenance": {"data_cutoff_at": "2026-09-01"},
    }, "biotech")
    assert "current_static_universe_survivorship_bias" in result["methodology_warning_codes"]
    assert any("Survivorship" in warning for warning in result["methodology_warnings"])
    assert result["execution_model"] == "producer_specific_v2"
    assert result["model_provenance"]["input_timeframe"] == "strategy_specific_unverified"
    assert result["model_provenance"]["data_cutoff_at"] == "2026-09-01"
    assert result["performance_available"] is False
    assert result["win_rate"] is None
