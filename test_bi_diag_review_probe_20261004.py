"""Independent red/green probe of full producer rows versus BI reporting counters."""
from copy import deepcopy

import pytest
import api

from test_bi_backtest_diagnostics_api import diagnostics, normalize
from test_bi_backtest_diagnostic_funnel import study, _indicator
from modules import backtests as bt
from modules.backtest_methodology import describe_cached_backtest


@pytest.mark.parametrize("rows,accepted,filled,no_fill", [
    (None, 0, 0, 0),
    (None, 1, 0, 0),
    ([], 1, 0, 0),
    ([{"outcome": "NO_FILL", "entry_filled": False}], 2, 0, 2),
    ([{"outcome": "NO_FILL", "entry_filled": False}], 1, 1, 0),
])
def test_full_producer_rows_cannot_disagree_with_accepted_or_fill_diagnostics(rows, accepted, filled, no_fill):
    raw = {"trades": rows, "summary": {
        "total_signals": accepted, "n_tickers": 2,
        "diagnostics": diagnostics(qualified=accepted, accepted=accepted, filled=filled, no_fill=no_fill),
    }}
    try:
        result = api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 3)
    except (ValueError, TypeError):
        return  # Rejecting the malformed producer payload is also safe.
    assert result.get("diagnostics_status") == "invalid", result
    assert result["verdict"]["status"] == "data_incomplete"


def test_empty_real_row_list_with_zero_accepted_remains_a_legitimate_zero_cohort():
    raw = {"trades": [], "summary": {"total_signals": 0, "n_tickers": 2, "diagnostics": diagnostics()}}
    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 3)
    assert result["diagnostics_status"] == "available"
    assert result["diagnostics"]["zero_result_stage"] == "indicator_rejected"


def test_real_final_only_pending_row_survives_api_generic_unresolved_quality_guard():
    diag = diagnostics(qualified=1, accepted=1)
    diag["funnel"]["unresolved"] = 1
    raw = {"trades": [{"outcome": "UNRESOLVED", "exit_reason": "UNRESOLVED", "entry_filled": False,
                       "evaluation_status": "INCOMPLETE_ENTRY_WINDOW", "pnl_pct": None,
                       "r_multiple": None, "signal_date": "2026-10-02"}],
           "summary": {"total_signals": 1, "n_tickers": 2, "unresolved": 1,
                       "data_quality": {"status": "NO_KNOWN_FETCH_OR_SESSION_GAP"}, "diagnostics": diag}}
    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 3)
    assert result["diagnostics_status"] == "available"
    assert result["diagnostics"]["zero_result_stage"] == "entry_pending", result
    assert result["verdict"]["status"] == "entry_pending"


@pytest.mark.parametrize("mode", ["indicator_rejected", "plan_rejected", "entry_pending"])
def test_actual_producer_diagnostics_survive_api_and_cached_description(study, monkeypatch, mode):
    if mode == "plan_rejected":
        monkeypatch.setattr(bt, "analyze_breakout_imminent", lambda *a, **k: _indicator(True, green=17))
    elif mode == "entry_pending":
        monkeypatch.setattr(bt, "analyze_breakout_imminent", lambda bars, **k:
            _indicator(True, green=17) if bars[-1]["date"] == "2026-10-02" else _indicator())
        monkeypatch.setattr(bt, "build_bi_trade_plan", lambda *a, **k: {
            "accepted": True, "reason": None, "Entry": 102, "StopLoss": 98,
            "TP1": 104, "TP2": 106, "entry_method": "stop_breakout", "plan_version": "synthetic",
            "geometry": {"rr": 3.0},
        })
    raw = study()
    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 3)
    assert result["diagnostics_status"] == "available"
    assert result["diagnostics"]["zero_result_stage"] == mode
    assert result["diagnostics"]["funnel"]["accepted_plans"] == len(raw["trades"])
    cached = describe_cached_backtest(result, "scanner_bi_long")
    assert cached["diagnostics_status"] == "available"
    assert cached["diagnostics"]["zero_result_stage"] == mode
    if mode == "entry_pending":
        assert cached["data_quality"]["followup_pending_only"] is True
        assert cached["verdict"]["status"] == "entry_pending"
        assert cached["avg_pnl"] is None


def _stored_cache_roundtrip(monkeypatch, tmp_path, result):
    monkeypatch.setattr(api, "BACKTEST_CACHE", str(tmp_path / "review-cache.json"))
    request = api._normalized_backtest_request(
        api.BacktestRequest(strategy="scanner_bi_long", months=3)
    )
    identity = api._backtest_request_identity(request)
    saved = api._complete_backtest_request(request, result, identity, "cache-partition-review")
    restored = api.get_backtest_results(
        ticker=request.ticker, strategy=request.strategy, months=request.months,
        max_tickers=request.max_tickers, min_price=request.min_price,
        min_volume=request.min_volume,
    )["data"]
    return saved, restored


@pytest.mark.parametrize("kind", ["filled_and_no_fill_overlap", "unaccounted_accepted", "no_fill_and_unresolved_overlap"])
def test_saved_cache_rejects_impossible_combined_funnel_partition(monkeypatch, tmp_path, kind):
    result = normalize(
        diagnostics(qualified=1, accepted=1, no_fill=1), accepted=1,
        trades=[{"outcome": "NO_FILL", "entry_filled": False}],
    )
    result = deepcopy(result)
    funnel = result["diagnostics"]["funnel"]
    if kind == "filled_and_no_fill_overlap":
        funnel["filled_trades"] = 1
    elif kind == "no_fill_and_unresolved_overlap":
        funnel["unresolved"] = 1
    else:
        funnel["no_fill"] = 0
    _, restored = _stored_cache_roundtrip(monkeypatch, tmp_path, result)
    assert restored["diagnostics_status"] == "invalid"
    assert restored["verdict"]["status"] == "data_incomplete"


@pytest.mark.parametrize("pending_filled", [False, True])
def test_saved_cache_preserves_legitimate_filled_unresolved_overlap(monkeypatch, tmp_path, pending_filled):
    diag = diagnostics(qualified=3, accepted=3, filled=1 + pending_filled, no_fill=1)
    diag["funnel"]["unresolved"] = 1
    rows = [
        {"outcome": "WIN", "entry_filled": True, "pnl_pct": 1, "r_multiple": 1},
        {"outcome": "NO_FILL", "entry_filled": False},
        {"outcome": "UNRESOLVED", "entry_filled": pending_filled,
         "evaluation_status": "INCOMPLETE_HOLDING_WINDOW" if pending_filled else "INCOMPLETE_ENTRY_WINDOW"},
    ]
    result = normalize(diag, accepted=3, trades=rows,
                       quality={"status": "NO_KNOWN_FETCH_OR_SESSION_GAP"})
    saved, restored = _stored_cache_roundtrip(monkeypatch, tmp_path, result)
    assert saved["diagnostics_status"] == restored["diagnostics_status"] == "available"
    assert restored["diagnostics"]["funnel"]["filled_trades"] == 1 + pending_filled
    assert restored["diagnostics"]["funnel"]["unresolved"] == 1


def test_saved_display_cap_is_not_revalidated_as_full_producer_rows(monkeypatch, tmp_path):
    total = 200
    diag = diagnostics(qualified=total, accepted=total, filled=total)
    diag["coverage"].update(selected_tickers=total, tickers_with_test_period_data=total,
                            tickers_with_usable_windows=total,
                            selected_expected_sessions=60 * total,
                            selected_observed_sessions=60 * total)
    diag["funnel"].update(windows_considered=total, indicator_evaluated_windows=total,
                         indicator_rejected_windows=0, confluence_histogram={"17": total})
    rows = [{"ticker": f"SYNTH{index}", "outcome": "WIN", "entry_filled": True,
             "pnl_pct": 1, "r_multiple": 1} for index in range(total)]
    raw = {"trades": rows, "summary": {"total_signals": total, "n_tickers": total,
           "diagnostics": diag, "data_quality": {"status": "NO_KNOWN_FETCH_OR_SESSION_GAP"}}}
    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 3)
    saved, restored = _stored_cache_roundtrip(monkeypatch, tmp_path, result)
    assert saved["diagnostics_status"] == restored["diagnostics_status"] == "available"
    assert restored["total_signals"] == restored["total_trades"] == total
    assert restored["trades_total"] == total
    assert restored["trades_returned"] == len(restored["trades"]) == 150
    assert restored["trades_truncated"] is True
    assert restored["diagnostics"]["funnel"]["accepted_plans"] == total
