"""Offline calendar coverage contracts for stock daily backtest execution."""
from datetime import datetime

import pytest

from modules import backtests as bt


def _bar(day, **kwargs):
    return dict(date=day, **dict(dict(open=100, high=102, low=99, close=101), **kwargs))


def _simulate(bars, **kwargs):
    args = dict(bars=bars, start_idx=0, max_hold=2, direction="LONG", entry_price=100,
                stop_price=95, tp1_price=110, tp2_price=120, fee_pct=0)
    args.update(kwargs)
    return bt.simulate_50_50_daily_exit(**args)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_missing_regular_session_cannot_produce_a_decided_profit(direction):
    args = {} if direction == "LONG" else dict(direction="SHORT", stop_price=105, tp1_price=90, tp2_price=80)
    result = _simulate([_bar("2026-09-01"), _bar("2026-09-03")], **args)
    assert result["outcome"] == result["exit_reason"] == "UNRESOLVED"
    assert result["evaluation_status"] == "MISSING_EXPECTED_SESSION"
    assert result["missing_expected_sessions"] == ["2026-09-02"]
    assert result["pnl_pct"] is result["r_multiple"] is result["r_multiple_upper"] is None
    stats = bt.compute_backtest_stats([result])
    assert stats["total_decided"] == 0
    assert stats["data_quality"]["status"] == "PARTIAL"
    assert stats["data_quality"]["coverage_unresolved_trades"] == 1


@pytest.mark.parametrize("previous,current", [
    ("2026-08-28", "2026-08-31"),  # Weekend
    ("2026-09-04", "2026-09-08"),  # Labor Day
    ("2026-04-02", "2026-04-06"),  # Good Friday
    ("2025-01-08", "2025-01-10"),  # Official special NYSE closure
    ("2026-11-25", "2026-11-27"),  # Thanksgiving (Friday is still a session)
])
def test_exchange_closures_do_not_invent_missing_stock_bars(previous, current):
    result = _simulate([_bar(previous), _bar(current)])
    assert result["exit_reason"] == "EOD"
    assert result["r_multiple"] == .2
    assert result["missing_expected_sessions"] == []
    assert result["session_coverage"] == "expected_session_gaps_checked"


def test_explicit_247_calendar_requires_weekend_observations():
    result = _simulate([_bar("2026-08-28"), _bar("2026-08-31")], session_calendar="24_7")
    assert result["outcome"] == "UNRESOLVED"
    assert result["missing_expected_sessions"] == ["2026-08-29", "2026-08-30"]


def test_api_crypto_daily_replay_requires_saturday_and_sunday():
    # Pure replay only; no scanner, provider, HTTP or database function runs.
    import api

    result = api._simulate_crypto_trade(
        [_bar("2026-08-28"), _bar("2026-08-31")], 0, "long", 100, 95, 110, 120, 2, fee_pct=0,
    )
    assert result["outcome"] == "UNRESOLVED"
    assert result["session_calendar"] == "24_7"
    assert result["evaluation_status"] == "MISSING_EXPECTED_SESSION"
    assert result["missing_expected_sessions"] == ["2026-08-29", "2026-08-30"]
    assert result["pnl_pct"] is result["r_multiple"] is None


def test_exit_proven_before_later_missing_session_remains_decided():
    result = _simulate([_bar("2026-09-01", high=121, low=99, close=120), _bar("2026-09-03")])
    assert result["exit_reason"] == "BLENDED_TP"
    assert result["r_multiple"] == 3


def test_one_closed_path_does_not_resolve_an_alternative_blocked_by_missing_session():
    result = _simulate([_bar("2026-09-01", high=112, close=111),
                        _bar("2026-09-03", open=112, high=115, low=111, close=113)])
    assert result["outcome"] == "UNRESOLVED"
    assert result["missing_expected_sessions"] == ["2026-09-02"]
    assert result["session_coverage"] == "missing_expected_sessions"
    assert result["r_multiple"] is result["r_multiple_upper"] is None


def test_missing_session_between_signal_and_first_future_bar_is_not_a_fill():
    plan = dict(Entry=100, StopLoss=95, TP1=110, TP2=120, entry_method="stop_breakout", plan_version="test")
    result = bt._simulate_bi_plan_daily([_bar("2026-09-01"), _bar("2026-09-03")], 1, plan, "LONG")
    assert result["outcome"] == "UNRESOLVED"
    assert result["evaluation_status"] == "MISSING_EXPECTED_SESSION"
    assert result["entry_filled"] is False
    assert result["missing_expected_sessions"] == ["2026-09-02"]


def test_entry_wait_cannot_skip_a_session_and_revive_later():
    plan = dict(Entry=100, StopLoss=95, TP1=110, TP2=120, entry_method="stop_breakout", plan_version="test")
    result = bt._simulate_bi_plan_daily([
        _bar("2026-09-01", open=98, high=99, low=97, close=98), _bar("2026-09-03")], 0, plan, "LONG")
    assert result["outcome"] == "UNRESOLVED"
    assert result["entry_filled"] is False
    assert result["evaluation_status"] == "MISSING_EXPECTED_SESSION"


def test_unresolved_data_gap_cannot_release_ticker_occupancy():
    assert bt.conservative_trade_exit_index(
        {"outcome": "UNRESOLVED", "exit_date": "2026-09-03"},
        {"2026-09-01": 0, "2026-09-03": 1, "2026-09-04": 2}, 0,
    ) == 2


@pytest.mark.parametrize("second", ["2026-09-01", "2026-08-31"])
def test_duplicate_or_reverse_daily_dates_remain_unresolved(second):
    result = _simulate([_bar("2026-09-01"), _bar(second)])
    assert result["outcome"] == "UNRESOLVED"
    assert result["evaluation_status"] == "NON_INCREASING_DAILY_DATES"


def test_undated_legacy_bar_fixture_is_not_claimed_as_verified_data():
    result = _simulate([_bar("synthetic-1"), _bar("synthetic-2")])
    assert result["exit_reason"] == "EOD"
    assert result["session_coverage"] == "legacy_bar_sequence_unverified"


@pytest.mark.parametrize("study", ["bi", "biotech", "grouped"])
def test_grouped_fetch_failure_is_visible_even_in_an_empty_report(monkeypatch, study):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 5, tzinfo=tz)

    monkeypatch.setattr(bt, "datetime", FrozenDatetime)
    calls = []

    def fetch(_key, day):
        calls.append(day)
        return None if day == "2026-09-02" else {}

    monkeypatch.setattr(bt, "fetch_grouped_daily", fetch)
    if study == "bi":
        result = bt.run_bi_v2_backtest("offline", months=1)
        quality = result["summary"]["data_quality"]
    elif study == "biotech":
        result = bt.run_biotech_backtest("offline", months=1)
        quality = result["summary"]["data_quality"]
    else:
        result, _ = bt.run_full_backtest_grouped("offline", strategies=["test"], months=1)
        assert isinstance(result["test"], list)
        quality = bt.compute_backtest_stats(result["test"])["data_quality"]
    assert "2026-09-02" in calls
    assert "2026-07-03" not in calls  # Observed Independence Day is not a required fetch.
    assert quality["status"] == "PARTIAL"
    assert quality["failed_fetch_days"] == 1
    assert quality["failed_fetch_dates"] == ["2026-09-02"]
    assert quality["statistics_scope"] == "observed_decided_paths_only_not_complete_market_cohort"
