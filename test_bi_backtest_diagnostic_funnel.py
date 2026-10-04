"""Offline counterexamples for honest BI cohort/window/plan diagnostics.

Synthetic data exercises accounting and causality, not a market hit rate.
No actual grouped-provider, signal sending or tracker state is used.
"""
from datetime import datetime

import pytest

from modules import backtests as bt
from modules.patterns import BreakoutAnalysisResult


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 4, 12, tzinfo=tz)


def _row(**updates):
    return {"o": 100, "h": 101, "l": 99, "c": 100, "v": 1_000_000, **updates}


def _indicator(valid=False, *, green=15, available=20, hard=(), contract=True):
    checks = tuple({"id": i, "key": f"factor_{i}", "available": i <= available,
                    "passed": i <= green} for i in range(1, 21))
    return BreakoutAnalysisResult((valid, 100, 173, [], 85, "A", 3, 3),
        indicator_checks=checks, green_count=green, available_count=available,
        indicator_contract_ok=contract, hard_gate_failures=hard)


@pytest.fixture
def study(monkeypatch):
    monkeypatch.setattr(bt, "datetime", _Clock)
    monkeypatch.setattr(bt, "fetch_grouped_daily", lambda key, day: {"TEST": _row()})
    monkeypatch.setattr(bt, "analyze_breakout_imminent", lambda *a, **k: _indicator())
    return lambda **kwargs: bt.run_bi_v2_backtest("offline", months=3, max_tickers=5, **kwargs)


def _assert_funnel(report):
    s = report["summary"]
    d = s["diagnostics"]
    assert d["schema_version"] == 1
    c, f = d["coverage"], d["funnel"]
    assert f["windows_considered"] == sum(f[k] for k in (
        "occupied_windows", "invalid_or_incomplete_windows", "price_filtered_windows",
        "volume_filtered_windows", "indicator_evaluated_windows"))
    assert f["indicator_evaluated_windows"] == sum(f[k] for k in (
        "indicator_contract_unavailable_windows", "indicator_rejected_windows", "indicator_qualified_candidates"))
    assert f["indicator_qualified_candidates"] == f["plan_rejected_candidates"] + f["accepted_plans"]
    assert sum(f["confluence_histogram"].values()) == f["indicator_evaluated_windows"]
    assert sum(f["plan_rejection_counts"].values()) == f["plan_rejected_candidates"]
    assert f["accepted_plans"] == s["total_signals"] == len(report["trades"])
    assert f["filled_trades"] == s["total_filled"]
    assert f["no_fill"] == s["no_fill"]
    assert f["unresolved"] == s["unresolved"]
    assert c["selected_tickers"] == s["n_tickers"]
    assert c["selected_expected_sessions"] >= c["selected_observed_sessions"]
    assert c["selected_expected_sessions"] - c["selected_observed_sessions"] == sum(
        len(days) for days in c["missing_sessions_by_ticker"].values())
    assert 0 <= c["tickers_with_usable_windows"] <= c["tickers_with_test_period_data"] <= c["selected_tickers"]
    return d


def test_real_structural_rejections_are_counted_before_zero_not_disguised_as_no_setups(study, monkeypatch):
    monkeypatch.setattr(bt, "analyze_breakout_imminent", lambda *a, **k: _indicator(True, green=17))
    result = study()
    d = _assert_funnel(result)
    assert d["coverage"]["selected_tickers"] == d["coverage"]["tickers_with_usable_windows"] == 1
    assert d["funnel"]["indicator_qualified_candidates"] > 0
    assert d["funnel"]["plan_rejection_counts"] == {
        "structural_barrier_blocked": d["funnel"]["indicator_qualified_candidates"]}
    assert d["zero_result_stage"] == "plan_rejected"
    assert result["summary"]["total_signals"] == result["summary"]["no_fill"] == 0


def test_native_factor_confluence_and_hard_gate_counts_do_not_infer_from_score(study, monkeypatch):
    monkeypatch.setattr(bt, "analyze_breakout_imminent",
                        lambda *a, **k: _indicator(green=18, hard=("range_breakdown",)))
    d = _assert_funnel(study())
    f = d["funnel"]
    assert f["confluence_histogram"] == {"18": f["indicator_evaluated_windows"]}
    assert f["hard_gate_counts"] == {"range_breakdown": f["indicator_evaluated_windows"]}
    assert d["zero_result_stage"] == "indicator_rejected"


def test_unavailable_factors_remain_unknown_not_failed_confirmations(study, monkeypatch):
    monkeypatch.setattr(bt, "analyze_breakout_imminent",
                        lambda *a, **k: _indicator(green=14, available=19, contract=False))
    d = _assert_funnel(study())
    f = d["funnel"]
    assert f["indicator_contract_unavailable_windows"] == f["indicator_evaluated_windows"]
    assert f["indicator_rejected_windows"] == 0
    assert f["unavailable_factor_counts"] == {"factor_20": f["indicator_evaluated_windows"]}
    assert d["zero_result_stage"] == "indicator_unavailable"


def test_legacy_tuple_metadata_cannot_be_reported_as_verified_confluence(study, monkeypatch):
    monkeypatch.setattr(bt, "analyze_breakout_imminent",
                        lambda *a, **k: (False, 170, 173, [], 98, "S", 5, 5))
    d = _assert_funnel(study())
    f = d["funnel"]
    assert f["confluence_histogram"] == {"unknown": f["indicator_evaluated_windows"]}
    assert f["indicator_metadata_unknown_windows"] == f["indicator_evaluated_windows"]
    assert f["unavailable_factor_counts"] == {}


@pytest.mark.parametrize("fault", [None, {}, []])
def test_empty_failed_and_malformed_expected_sessions_are_data_gaps_without_trades(study, monkeypatch, fault):
    monkeypatch.setattr(bt, "fetch_grouped_daily",
                        lambda key, day: fault if day == "2026-09-25" else {"TEST": _row()})
    d = _assert_funnel(result := study())
    c, f = d["coverage"], d["funnel"]
    assert result["summary"]["data_quality"]["status"] == "PARTIAL"
    assert c["missing_sessions_by_ticker"]["TEST"] == ["2026-09-25"]
    assert f["invalid_or_incomplete_windows"] > 0
    assert d["zero_result_stage"] == "data_incomplete"
    assert c["empty_fetch_dates"] == (["2026-09-25"] if fault == {} else [])
    assert c["failed_fetch_dates"] == ([] if fault == {} else ["2026-09-25"])


@pytest.mark.parametrize("bad,reason", [
    ({"c": True}, "INVALID_OHLC"), ({"c": "100"}, "INVALID_OHLC"),
    ({"h": float("nan")}, "INVALID_OHLC"), ({"h": 98}, "INVALID_OHLC"),
    ({"o": None}, "INVALID_OHLC"), ({"v": False}, "INVALID_VOLUME"),
    ({"v": 0}, "INVALID_VOLUME"), ({"v": "1000000"}, "INVALID_VOLUME"),
    ({"is_closed": False}, "INCOMPLETE_DAILY_BAR"),
    ({"complete": False, "final": True}, "INCOMPLETE_DAILY_BAR"),
])
def test_invalid_raw_ohlcv_cannot_enter_scores_or_be_called_complete(study, monkeypatch, bad, reason):
    observed = []
    def analyze(window, **kwargs):
        observed.extend(bar["date"] for bar in window)
        return _indicator()
    monkeypatch.setattr(bt, "analyze_breakout_imminent", analyze)
    monkeypatch.setattr(bt, "fetch_grouped_daily",
                        lambda key, day: {"TEST": _row(**bad)} if day == "2026-09-25" else {"TEST": _row()})
    d = _assert_funnel(result := study())
    c = d["coverage"]
    assert "2026-09-25" not in observed
    assert c["invalid_bars_by_ticker"] == {"TEST": 1}
    assert c["invalid_bar_reason_counts"] == {reason: 1}
    assert result["summary"]["data_quality"]["excluded_invalid_bars"] == 1
    assert d["zero_result_stage"] == "data_incomplete"


def test_ticker_selected_before_study_without_future_history_is_not_called_analyzed(study, monkeypatch):
    def grouped(key, day):
        return {"OLD": _row(v=2_000_000), "TEST": _row()} if day < "2026-07-05" else {"TEST": _row()}
    monkeypatch.setattr(bt, "fetch_grouped_daily", grouped)
    d = _assert_funnel(study())
    c = d["coverage"]
    assert c["selected_tickers"] == 2
    assert c["tickers_with_test_period_data"] == c["tickers_with_usable_windows"] == 1
    assert len(c["missing_sessions_by_ticker"]["OLD"]) == c["expected_test_sessions"]
    assert c["boundary_coverage"].endswith("not_listing_verification")
    assert d["zero_result_stage"] == "data_incomplete"


def test_price_and_volume_prefilters_partition_windows_and_do_not_mean_no_data(study, monkeypatch):
    def grouped(key, day):
        if day < "2026-07-05":
            return {"TEST": _row()}
        if day < "2026-08-15":
            return {"TEST": _row(o=4, h=5, l=3, c=4)}
        return {"TEST": _row(v=100_000)}
    monkeypatch.setattr(bt, "fetch_grouped_daily", grouped)
    d = _assert_funnel(study())
    f = d["funnel"]
    assert d["coverage"]["tickers_with_usable_windows"] == 1
    assert f["price_filtered_windows"] > 0 and f["volume_filtered_windows"] > 0
    assert f["indicator_evaluated_windows"] == 0
    assert d["zero_result_stage"] == "prefilter_rejected"


def test_requested_200k_threshold_does_not_silently_exclude_300k_prior_volume(study, monkeypatch):
    monkeypatch.setattr(bt, "fetch_grouped_daily", lambda key, day: {"TEST": _row(v=300_000)})
    d = _assert_funnel(study(min_volume=200_000))
    assert d["coverage"]["selected_tickers"] == 1
    assert d["funnel"]["indicator_evaluated_windows"] > 0
    assert d["zero_result_stage"] == "indicator_rejected"


def test_latest_prior_volume_not_oldest_warmup_or_future_volume_selects_ticker(study, monkeypatch):
    selected = []
    def grouped(key, day):
        vol = 800_000 if day < "2026-06-05" else (2_000_000 if day < "2026-07-05" else 500_000)
        future = 50_000_000 if day >= "2026-07-05" else 1_000_000
        return {"OLD": _row(v=future), "NEW": _row(v=vol)}
    monkeypatch.setattr(bt, "fetch_grouped_daily", grouped)
    report = bt.run_bi_v2_backtest("offline", months=3, max_tickers=1,
        progress_callback=lambda pct, text: selected.append(text) if "Analysiere " in text else None)
    _assert_funnel(report)
    assert any("Analysiere NEW " in text for text in selected)
    assert not any("Analysiere OLD " in text for text in selected)


def test_signal_date_uses_last_known_bar_includes_final_day_and_not_pre_study(study, monkeypatch):
    days = []
    monkeypatch.setattr(bt, "analyze_breakout_imminent",
        lambda bars, **kw: days.append(bars[-1]["date"]) or _indicator())
    _assert_funnel(study())
    assert days[0] == "2026-07-06"  # first actual session on/after study start July5
    assert days[-1] == "2026-10-02"  # Friday, Oct3 is not an expected session
    assert all(day >= "2026-07-05" for day in days)


def test_accepted_unfilled_plans_are_different_from_rejected_plans_and_unknown_future(study, monkeypatch):
    monkeypatch.setattr(bt, "analyze_breakout_imminent", lambda *a, **k: _indicator(True, green=17))
    monkeypatch.setattr(bt, "build_bi_trade_plan", lambda *a, **k: {
        "accepted": True, "reason": None, "Entry": 102, "StopLoss": 98,
        "TP1": 104, "TP2": 106, "entry_method": "stop_breakout", "plan_version": "synthetic",
        "geometry": {"rr": 3.0},
    })
    result = study()
    d = _assert_funnel(result)
    f = d["funnel"]
    assert f["accepted_plans"] > 0
    assert f["plan_rejected_candidates"] == f["filled_trades"] == 0
    assert f["occupied_windows"] > 0
    assert f["no_fill"] > 0
    assert f["unresolved"] == 1  # not a second final-day plan while first pending
    assert d["zero_result_stage"] == "entry_pending"


def test_final_only_signal_is_unknown_future_entry_not_definitive_no_fill(study, monkeypatch):
    monkeypatch.setattr(bt, "analyze_breakout_imminent", lambda bars, **k:
        _indicator(True, green=17) if bars[-1]["date"] == "2026-10-02" else _indicator())
    monkeypatch.setattr(bt, "build_bi_trade_plan", lambda *a, **k: {
        "accepted": True, "reason": None, "Entry": 102, "StopLoss": 98,
        "TP1": 104, "TP2": 106, "entry_method": "stop_breakout", "plan_version": "synthetic",
        "geometry": {"rr": 3.0},
    })
    result = study()
    d = _assert_funnel(result)
    f = d["funnel"]
    assert (f["indicator_qualified_candidates"], f["accepted_plans"],
            f["filled_trades"], f["no_fill"], f["unresolved"]) == (1, 1, 0, 0, 1)
    assert result["trades"][0]["evaluation_status"] == "INCOMPLETE_ENTRY_WINDOW"
    assert result["trades"][0]["signal_date"] == "2026-10-02"
    assert result["summary"]["data_quality"]["status"] == "NO_KNOWN_FETCH_OR_SESSION_GAP"
    assert d["zero_result_stage"] == "entry_pending"


def test_no_selected_or_no_expected_sessions_still_returns_full_zero_contract(study, monkeypatch):
    monkeypatch.setattr(bt, "fetch_grouped_daily", lambda key, day: {"TEST": _row(v=100_000)})
    d = _assert_funnel(study(min_volume=200_000))
    assert d["zero_result_stage"] == "no_selected_tickers"
    from modules import signal_tracker
    monkeypatch.setattr(signal_tracker, "_is_us_equity_session", lambda day: False)
    d = _assert_funnel(study())
    assert d["coverage"]["expected_fetch_sessions"] == 0
    assert d["coverage"]["loaded_fetch_sessions"] == 0
    assert d["zero_result_stage"] == "no_selected_tickers"


def test_unselected_dormant_invalid_asset_does_not_poison_selected_study(study, monkeypatch):
    monkeypatch.setattr(bt, "fetch_grouped_daily",
                        lambda key, day: {"TEST": _row(), "BAD": _row(v=0)})
    d = _assert_funnel(result := study())
    c = d["coverage"]
    assert c["source_excluded_invalid_bars"] == c["expected_fetch_sessions"]
    assert c["excluded_invalid_bars"] == 0
    assert c["invalid_bars_by_ticker"] == {}
    assert result["summary"]["data_quality"]["status"] == "NO_KNOWN_FETCH_OR_SESSION_GAP"
    assert d["zero_result_stage"] == "indicator_rejected"


def test_all_invalid_source_rows_do_not_claim_no_bi_setups_or_complete_study(study, monkeypatch):
    monkeypatch.setattr(bt, "fetch_grouped_daily", lambda key, day: {"BAD": _row(v=0)})
    d = _assert_funnel(result := study())
    assert d["coverage"]["selected_tickers"] == 0
    assert d["coverage"]["source_excluded_invalid_bars"] > 0
    assert result["summary"]["data_quality"]["status"] == "PARTIAL"
    assert d["zero_result_stage"] == "data_incomplete"
