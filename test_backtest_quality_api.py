"""Offline API regressions: incomplete observations never confirm an edge."""
from copy import deepcopy
from datetime import date, timedelta
import socket

import pytest

import api


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("This reporting test must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def _positive_cohort():
    # 40 dated observations, including losses in both chronological splits.
    # This would qualify under the existing report verdict absent data gaps.
    result = []
    for index in range(40):
        pnl = -0.5 if index % 4 == 0 else 2.0
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        result.append({
            "ticker": "OFFLINE", "entry_date": day, "exit_date": day,
            "entry_price": 100.0, "exit_price": 100.0 + pnl,
            "pnl_pct": pnl, "r_multiple": pnl / 2.0,
            "grade": "A", "outcome": "STOP" if pnl < 0 else "TP2",
        })
    return result


def _scanner(trades, **kwargs):
    return api._build_backtest_result("scanner_bi_long", "BI Long", "long", 6, trades, **kwargs)


def _assert_incomplete(result):
    assert result["data_quality"]["status"] == "PARTIAL"
    assert result["data_quality"]["limitations"]
    assert result["verdict"]["status"] == "data_incomplete"
    assert result["verdict"]["tradable"] is False
    assert result["out_of_sample"]["status"] == "data_incomplete"
    assert result["out_of_sample"]["robust"] is False
    assert result["out_of_sample"]["diagnostic_only"] is True


def test_complete_positive_control_retains_existing_report_verdict():
    result = _scanner(_positive_cohort())
    assert result["verdict"]["status"] == "approved"
    assert result["verdict"]["tradable"] is True
    assert result["out_of_sample"]["status"] == "pass"
    assert result["data_quality"]["status"] == "NO_KNOWN_FETCH_OR_SESSION_GAP"
    assert result["avg_r"] == 0.69


def test_scanner_normalization_preserves_partial_source_and_removes_edge_claim():
    raw = {
        "summary": {
            "total_signals": 40,
            "data_quality": {"status": "PARTIAL", "failed_fetch_days": 1,
                             "failed_fetch_dates": ["2026-01-20"],
                             "statistics_scope": "observed_decided_paths_only_not_complete_market_cohort"},
        },
        "trades": _positive_cohort(),
    }
    original = deepcopy(raw)
    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 6)
    _assert_incomplete(result)
    assert result["data_quality"]["failed_fetch_dates"] == ["2026-01-20"]
    assert result["total_decided"] == 40
    assert result["avg_pnl"] == 1.38  # Descriptive observed-subset result remains.
    assert result["avg_r"] == 0.69
    assert raw == original


@pytest.mark.parametrize("use_indicator", [False, True])
def test_unresolved_path_prevents_positive_verdict_for_observed_winners(use_indicator):
    trades = _positive_cohort() + [{
        "ticker": "GAP", "entry_date": "2026-02-15", "outcome": "UNRESOLVED",
        "pnl_pct": None, "r_multiple": None, "entry_filled": False,
        "missing_expected_sessions": ["2026-02-17"],
    }]
    result = api._backtest_stats(trades, "OFFLINE", "test", 6) if use_indicator else _scanner(trades)
    _assert_incomplete(result)
    assert result["total_decided"] == 40
    assert result["total_filled"] == 40  # Unknown pending entry is not an actual fill.
    assert result["unresolved"] == 1
    assert result["avg_r"] == 0.69
    assert result["data_quality"]["missing_expected_sessions"] == ["2026-02-17"]


def test_summary_unresolved_count_is_not_lost_when_only_decided_rows_are_retained():
    result = _scanner(_positive_cohort(), unresolved=3)
    _assert_incomplete(result)
    assert result["data_quality"]["unresolved_trades"] == 3


@pytest.mark.parametrize("use_indicator", [False, True])
def test_missing_r_does_not_become_zero_or_an_average_of_the_known_subset(use_indicator):
    trades = _positive_cohort()
    del trades[-1]["r_multiple"]
    original = deepcopy(trades)
    result = api._backtest_stats(trades, "OFFLINE", "test", 6) if use_indicator else _scanner(trades)
    _assert_incomplete(result)
    assert result["avg_r"] is None
    assert result["avg_r_upper"] is None
    assert result["total_r_upper"] is None
    assert result["data_quality"]["missing_r_decided_trades"] == 1
    assert result["total_decided"] == 40
    assert result["win_rate"] == 75.0  # PnL is known; R is not.
    if not use_indicator:
        assert result["stats_by_grade"]["A"]["avg_r"] is None
        assert result["trades"][-1]["r_multiple"] is None
    assert trades == original


def test_classic_indicator_without_initial_risk_does_not_report_zero_r():
    trades = _positive_cohort()
    for trade in trades:
        del trade["r_multiple"]
        del trade["outcome"]  # Actual classic-indicator row shape.
    result = api._backtest_stats(trades, "OFFLINE", "ema_crossover", 6)
    _assert_incomplete(result)
    assert result["avg_r"] is None
    assert result["data_quality"]["missing_r_decided_trades"] == 40


@pytest.mark.parametrize("value", [None, "", False, float("nan"), float("inf"), -float("inf")])
def test_normalization_preserves_unknown_metrics_as_null(value):
    row = api._normalize_backtest_trades([{
        "pnl_pct": value, "r_multiple": value, "pnl_pct_upper": value, "r_multiple_upper": value,
    }], "long")[0]
    assert row["pnl_pct"] is row["r_multiple"] is row["pnl_pct_upper"] is row["r_multiple_upper"] is None


@pytest.mark.parametrize("value", [0, -1.234, 1.234])
def test_normalization_keeps_real_zero_positive_and_negative_measurements(value):
    row = api._normalize_backtest_trades([{"pnl_pct": value, "r_multiple": value}], "long")[0]
    assert row["pnl_pct"] == row["r_multiple"] == round(value, 2)
    assert row["pnl_pct_upper"] == row["r_multiple_upper"] == round(value, 2)


@pytest.mark.parametrize("use_indicator", [False, True])
def test_nominally_closed_row_without_pnl_is_not_counted_as_zero_loss(use_indicator):
    trades = _positive_cohort()
    trades[-1]["pnl_pct"] = None
    result = api._backtest_stats(trades, "OFFLINE", "test", 6) if use_indicator else _scanner(trades)
    _assert_incomplete(result)
    assert result["total_decided"] == 39
    assert result["unresolved"] == 1
    assert result["data_quality"]["missing_pnl_trades"] == 1
    assert result["win_rate"] == 74.4


@pytest.mark.parametrize("use_indicator", [False, True])
def test_empty_trade_list_preserves_fetch_failure_metadata(use_indicator):
    class TradesWithQuality(list):
        data_quality = {"status": "PARTIAL", "failed_fetch_days": 1,
                        "failed_fetch_dates": ["2026-09-01"]}

    trades = TradesWithQuality()
    result = api._backtest_stats(trades, "OFFLINE", "test", 6) if use_indicator else _scanner(trades)
    _assert_incomplete(result)
    assert result["total_decided"] == 0
    assert result["data_quality"]["failed_fetch_dates"] == ["2026-09-01"]


def test_partial_source_without_specific_dates_is_still_not_complete():
    result = _scanner(_positive_cohort(), data_quality={"status": "PARTIAL"})
    _assert_incomplete(result)


def test_unresolved_indicator_has_no_fabricated_pnl_or_r():
    row = api._indicator_unresolved_trade({"entry_date": "2026-09-01", "entry_price": 100.0}, ["2026-09-01"])
    assert row["pnl_pct"] is row["r_multiple"] is None


def test_optional_null_quality_lists_are_normalized_without_crashing():
    quality = {"status": "PARTIAL", "failed_fetch_dates": None,
               "unavailable_tickers": None, "missing_expected_sessions": None}
    trades = _positive_cohort()
    trades[0]["missing_expected_sessions"] = None
    result = _scanner(trades, data_quality=quality)
    _assert_incomplete(result)
    for key in ("failed_fetch_dates", "unavailable_tickers", "missing_expected_sessions"):
        assert result["data_quality"][key] == []


@pytest.mark.parametrize("quality", [
    {"failed_fetch_dates": "2026-09-01"},
    {"missing_expected_sessions": 17},
    {"unavailable_tickers": [None]},
    {"failed_fetch_days": float("nan")},
    {"failed_fetch_days": -1},
    {"failed_fetch_days": 1.5},
    "invalid",
])
def test_malformed_quality_metadata_cannot_confirm_an_edge(quality):
    _assert_incomplete(_scanner(_positive_cohort(), data_quality=quality))
