"""Isolated reporting tests: no provider, SMTP, or real cached study."""

from copy import deepcopy

import pytest
import api
from modules.backtest_diagnostics import apply_bi_backtest_diagnosis, validate_bi_diagnostics
from modules.backtest_methodology import attach_backtest_methodology, describe_cached_backtest


def diagnostics(*, qualified=0, rejected=0, accepted=0, filled=0, no_fill=0):
    return {
        "schema_version": 1,
        "coverage": {"selected_tickers": 2, "tickers_with_test_period_data": 2,
                     "tickers_with_usable_windows": 2, "expected_fetch_sessions": 60,
                     "loaded_fetch_sessions": 60, "empty_fetch_dates": [], "failed_fetch_dates": [],
                     "selected_expected_sessions": 120, "selected_observed_sessions": 120,
                     "missing_sessions_by_ticker": {}, "invalid_bars_by_ticker": {}},
        "funnel": {"windows_considered": 64, "occupied_windows": 0, "invalid_or_incomplete_windows": 0,
                   "price_filtered_windows": 0, "volume_filtered_windows": 0, "indicator_evaluated_windows": 64,
                   "indicator_qualified_candidates": qualified, "indicator_contract_unavailable_windows": 0,
                   "indicator_rejected_windows": 64-qualified, "confluence_histogram": {"17": qualified, "15": 64-qualified},
                   "hard_gate_counts": {}, "unavailable_factor_counts": {}, "plan_rejected_candidates": rejected,
                   "plan_rejection_counts": {"structural_barrier_blocked": rejected}, "accepted_plans": accepted,
                   "filled_trades": filled, "no_fill": no_fill, "unresolved": 0},
        "zero_result_stage": "producer_value_not_authoritative",
    }


def normalize(diag=None, *, accepted=0, trades=None, quality=None):
    summary = {"total_signals": accepted, "n_tickers": 2, "plan_version": "shared-plan-fixture",
               "parity_scope": "plan_not_full_live_universe", "execution_model": "daily_next_session_50_50_be_after_tp1_v2",
               "live_delivery_equivalent": False, "selection_model": "stock-bi-20-v8", "input_timeframe": "1D"}
    if diag is not None:
        summary["diagnostics"] = diag
    if quality is not None:
        summary["data_quality"] = quality
    raw = {"summary": summary, "trades": trades or []}
    return api._normalize_scanner_backtest(raw, "scanner_bi_long", {"name": "BI Long"}, 3)


def test_qualified_candidates_are_not_reported_as_no_patterns():
    diag = diagnostics(qualified=64, rejected=64)
    original = deepcopy(diag)
    result = normalize(diag)
    assert result["total_signals"] == 0  # Backward-compatible accepted-plan count.
    assert result["diagnostics"]["funnel"]["indicator_qualified_candidates"] == 64
    assert result["diagnostics"]["zero_result_stage"] == "plan_rejected"
    assert result["verdict"]["status"] == "plans_rejected"
    assert result["verdict"]["tradable"] is False
    assert diag == original


def test_zero_qualified_is_distinct_from_zero_accepted():
    result = normalize(diagnostics())
    assert result["verdict"]["status"] == "no_bi_setups"
    assert result["diagnostics"]["zero_result_stage"] == "indicator_rejected"


@pytest.mark.parametrize("unavailable", [1, 64])
def test_unavailable_indicator_contract_is_not_complete_negative_result(unavailable):
    diag = diagnostics()
    diag["funnel"]["indicator_contract_unavailable_windows"] = unavailable
    diag["funnel"]["indicator_rejected_windows"] = 64 - unavailable
    result = normalize(diag)
    assert result["diagnostics"]["zero_result_stage"] == "indicator_unavailable"
    assert result["verdict"]["status"] == "indicator_unavailable"


def test_last_accepted_signal_is_pending_not_final_no_fill():
    diag = diagnostics(qualified=1, accepted=1)
    diag["funnel"]["unresolved"] = 1
    result = normalize(diag, accepted=1,
                       trades=[{"outcome": "UNRESOLVED", "entry_filled": False, "evaluation_status": "INCOMPLETE_ENTRY_WINDOW"}],
                       quality={"status": "NO_KNOWN_FETCH_OR_SESSION_GAP"})
    assert result["diagnostics"]["zero_result_stage"] == "entry_pending"
    assert result["verdict"]["status"] == "entry_pending"
    assert result["data_quality"]["status"] == "PARTIAL"
    assert result["data_quality"]["followup_pending_only"] is True
    reopened = describe_cached_backtest(result)
    assert reopened["diagnostics"]["zero_result_stage"] == "entry_pending"


def test_pending_followup_cannot_hide_real_historical_gap():
    diag = diagnostics(qualified=1, accepted=1)
    diag["funnel"]["unresolved"] = 1
    diag["coverage"]["missing_sessions_by_ticker"] = {"AAA": ["2026-09-10"]}
    result = normalize(diag, accepted=1,
                       trades=[{"outcome": "UNRESOLVED", "entry_filled": False, "evaluation_status": "INCOMPLETE_ENTRY_WINDOW"}],
                       quality={"status": "PARTIAL"})
    assert result["diagnostics"]["zero_result_stage"] == "data_incomplete"
    assert result["data_quality"].get("followup_pending_only") is not True


@pytest.mark.parametrize("key", ["loaded_fetch_sessions", "selected_observed_sessions"])
def test_missing_observations_are_data_gaps_even_without_date_details(key):
    diag = diagnostics()
    diag["coverage"][key] -= 1
    result = normalize(diag)
    assert result["diagnostics"]["zero_result_stage"] == "data_incomplete"


@pytest.mark.parametrize("raw_count", [True, 1.5, "1"])
def test_raw_summary_count_is_not_silently_coerced(raw_count):
    diag = diagnostics(qualified=1, accepted=1, no_fill=1)
    result = normalize(diag, accepted=raw_count, trades=[{"outcome": "NO_FILL", "entry_filled": False}])
    assert result["diagnostics_status"] == "invalid"


def test_pending_flag_cannot_override_unavailable_source():
    result = normalize(diagnostics())
    result["data_quality"] = {"status": "UNAVAILABLE", "followup_pending_only": True}
    result = apply_bi_backtest_diagnosis(result)
    assert result["diagnostics"]["zero_result_stage"] == "data_incomplete"


@pytest.mark.parametrize("gap", ["failed_fetch_dates", "empty_fetch_dates", "missing_sessions_by_ticker", "invalid_bars_by_ticker"])
def test_data_gaps_before_any_trade_override_zero_pattern_claim(gap):
    diag = diagnostics()
    diag["coverage"][gap] = {"AAA": ["2026-09-10"]} if gap == "missing_sessions_by_ticker" else {"AAA": 1} if gap == "invalid_bars_by_ticker" else ["2026-09-10"]
    result = normalize(diag, quality={"status": "PARTIAL"})
    assert result["diagnostics"]["zero_result_stage"] == "data_incomplete"
    assert result["verdict"]["status"] == "data_incomplete"


def test_no_entry_is_not_no_setup():
    diag = diagnostics(qualified=1, accepted=1, no_fill=1)
    result = normalize(diag, accepted=1, trades=[{"outcome": "NO_FILL", "entry_filled": False}])
    assert result["diagnostics"]["zero_result_stage"] == "no_fills"
    assert result["verdict"]["status"] == "entry_not_reached"


@pytest.mark.parametrize("section,key,value", [
    ("coverage", "selected_tickers", True), ("coverage", "loaded_fetch_sessions", 61),
    ("funnel", "indicator_evaluated_windows", "64"), ("funnel", "plan_rejected_candidates", -1),
    ("funnel", "indicator_qualified_candidates", 1), ("funnel", "confluence_histogram", {"15": 63}),
    ("funnel", "plan_rejection_counts", {"blocked": 1}), ("coverage", "invalid_bars_by_ticker", {"AAA": True}),
    ("coverage", "missing_sessions_by_ticker", {"AAA": "missing"}),
])
def test_inconsistent_or_coerced_diagnosis_never_claims_zero_setups(section, key, value):
    diag = diagnostics()
    diag[section][key] = value
    assert validate_bi_diagnostics(diag) is None
    result = normalize(diag)
    assert result["diagnostics_status"] == "invalid"
    assert "diagnostics" not in result
    assert result["data_quality"]["status"] == "PARTIAL"
    assert result["verdict"]["status"] == "data_incomplete"


def test_old_zero_cache_remains_unexplained_not_relabelled():
    cached = {"strategy": "scanner_bi_long", "total_trades": 0, "total_signals": 0, "n_tickers": 200,
              "trades": [], "verdict": {"status": "no_signal", "label": "KEIN SIGNAL"}}
    original = deepcopy(cached)
    result = describe_cached_backtest(cached)
    assert result["diagnostics_status"] == "missing"
    assert result["verdict"]["status"] == "diagnosis_unavailable"
    assert result["win_rate"] is None
    assert cached == original


def test_provenance_survives_normalization_metadata_and_reopen():
    result = normalize(diagnostics())
    annotated = attach_backtest_methodology(result, "scanner_bi_long")
    cached = describe_cached_backtest(annotated)
    assert cached["execution_model"] == result["execution_model"]
    assert cached["model_provenance"]["plan_version"] == "shared-plan-fixture"
    assert cached["model_provenance"]["live_delivery_equivalent"] is False
    assert cached["model_provenance"]["selection_model"] == "stock-bi-20-v8"
    assert cached["model_provenance"]["input_timeframe"] == "1D"
    assert cached["diagnostics"] == annotated["diagnostics"]


def test_bi_model_change_does_not_invalidate_other_models(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "BACKTEST_CACHE", str(tmp_path / "backtest.json"))
    bi = api._backtest_request_identity(api._normalized_backtest_request(api.BacktestRequest(strategy="scanner_bi_long")))
    generic = api._backtest_request_identity(api.BacktestRequest(strategy="sma_crossover"))
    bi_new, generic_old = api._backtest_v2_cache_path(bi), api._backtest_v2_cache_path(generic)
    monkeypatch.setattr(api, "_BACKTEST_MODEL_VERSION", "previous_generic_version")
    assert api._backtest_v2_cache_path(bi) == bi_new
    assert api._backtest_v2_cache_path(generic) != generic_old
