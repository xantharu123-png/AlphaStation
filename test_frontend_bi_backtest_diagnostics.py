"""Execute shipped BI diagnosis presentation with offline, synthetic counter payloads."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent


def presentation(payload):
    node = shutil.which("node")
    assert node, "Node is required to verify shipped BI diagnostics"
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    start = source.index("function backtestBIDiagnosticsPresentation(")
    end = source.index("function loadBacktestPreferences()", start)
    script = source[start:end] + "\nprocess.stdout.write(JSON.stringify(backtestBIDiagnosticsPresentation(" + json.dumps(payload) + ")));"
    completed = subprocess.run([node, "-e", script], encoding="utf-8", capture_output=True, check=True, timeout=10)
    return json.loads(completed.stdout)


def report(stage="plan_rejected"):
    return {"request": {"strategy": "scanner_bi_long"}, "total_trades": 0,
            "n_tickers": 200, "diagnostics": {"schema_version": 1,
                "coverage": {"selected_tickers": 200, "tickers_with_test_period_data": 180,
                             "tickers_with_usable_windows": 170,
                             "selected_expected_sessions": 10000, "selected_observed_sessions": 10000},
                "funnel": {"windows_considered": 5000, "indicator_evaluated_windows": 4000,
                           "indicator_qualified_candidates": 7, "indicator_rejected_windows": 3993,
                           "indicator_contract_unavailable_windows": 0,
                           "plan_rejected_candidates": 7, "accepted_plans": 0, "filled_trades": 0,
                           "no_fill": 0, "unresolved": 0,
                           "plan_rejection_counts": {"WAIT_BREAK_RECLAIM": 7}},
                "zero_result_stage": stage},
            "plan_version": "bi-plan-fixture-v1", "execution_model": "daily-fixture",
            "parity_scope": "plan-only-fixture", "methodology": "shared-production-bi-fixture"}


def test_selected_tickers_are_not_claimed_to_have_usable_histories():
    result = presentation(report())
    counters = dict(result["counters"])
    assert counters["Aktien ausgewählt"] == 200
    assert counters["Aktien mit auswertbaren Fenstern"] == 170
    assert counters["BI-Setups"] == 7
    assert counters["Handelspläne"] == 0
    assert "7 BI-Setups" in result["title"]
    assert "Handelsplanprüfung" in result["detail"]


def test_no_indicator_candidates_is_separate_from_rejected_plans():
    payload = report("indicator_rejected")
    payload["diagnostics"]["funnel"].update(indicator_qualified_candidates=0,
            plan_rejected_candidates=0, plan_rejection_counts={})
    result = presentation(payload)
    assert result["title"] == "Keine BI-Setups nach 17/20-Prüfung"
    assert dict(result["counters"])["BI-Setups"] == 0


@pytest.mark.parametrize("stage,unavailable", [("indicator_unavailable", 4000), ("indicator_unavailable", 1000), ("indicator_rejected", 1000)])
def test_noncalculable_contract_is_not_called_no_17_of_20_setups(stage, unavailable):
    payload = report(stage)
    payload["diagnostics"]["funnel"].update(indicator_qualified_candidates=0,
        indicator_contract_unavailable_windows=unavailable, indicator_rejected_windows=4000-unavailable,
        plan_rejected_candidates=0, plan_rejection_counts={})
    result = presentation(payload)
    assert result["title"] == "BI-Prüfung unvollständig"
    assert "nicht alle" in result["detail"].lower()
    assert "Keine BI-Setups" not in result["title"]


def test_prefilter_rejections_are_not_absent_histories():
    payload = report("prefilter_rejected")
    payload["diagnostics"]["funnel"].update(indicator_evaluated_windows=0,
            indicator_qualified_candidates=0, plan_rejected_candidates=0, plan_rejection_counts={})
    result = presentation(payload)
    assert "Preis-/Volumenfilter" in result["title"]
    assert "Historien waren vorhanden" in result["detail"]


@pytest.mark.parametrize("stage,coverage,title", [
    ("no_selected_tickers", {"selected_tickers": 0}, "Keine Aktien für die Stichprobe"),
    ("no_usable_windows", {"tickers_with_usable_windows": 0}, "Keine auswertbaren BI-Zeitfenster"),
])
def test_empty_selection_and_empty_windows_have_distinct_causes(stage, coverage, title):
    payload = report(stage)
    payload["diagnostics"]["coverage"].update(coverage)
    assert title in presentation(payload)["title"]


def test_accepted_plans_without_fills_are_not_no_setups():
    payload = report("no_fills")
    payload["diagnostics"]["funnel"].update(accepted_plans=7, plan_rejected_candidates=0,
            no_fill=7, plan_rejection_counts={})
    result = presentation(payload)
    assert "kein Einstieg ausgeführt" in result["title"]
    assert dict(result["counters"])["Handelspläne"] == 7


@pytest.mark.parametrize("stage,no_fill", [("entry_pending", 0), ("entry_pending", 6), ("no_fills", 6)])
def test_unfinished_entry_window_is_not_final_no_fill(stage, no_fill):
    payload = report(stage)
    payload["diagnostics"]["funnel"].update(accepted_plans=7, plan_rejected_candidates=0,
        unresolved=7-no_fill, no_fill=no_fill, plan_rejection_counts={})
    result = presentation(payload)
    assert result["title"] == "Einstiegsprüfung noch offen"
    assert "nicht abgeschlossen" in result["detail"]
    assert "kein Einstieg ausgeführt" not in result["title"]


@pytest.mark.parametrize("stage,filled,title", [("entry_pending", 0, "Einstiegsprüfung noch offen"),
    ("outcomes_pending", 1, "Trade-Ergebnis noch offen")])
def test_explicit_followup_only_flag_does_not_call_future_tail_a_history_gap(stage, filled, title):
    payload = report(stage)
    payload["data_quality"] = {"status": "PARTIAL", "followup_pending_only": True}
    payload["verdict"] = {"status": "data_incomplete"}
    payload["diagnostics"]["funnel"].update(accepted_plans=1, indicator_qualified_candidates=1,
        filled_trades=filled, unresolved=1, plan_rejected_candidates=0, plan_rejection_counts={})
    result = presentation(payload)
    assert result["title"] == title
    assert result["followupPendingOnly"] is True


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_missing_or_coerced_followup_flag_never_overrides_partial_data(flag):
    payload = report("entry_pending")
    payload["data_quality"] = {"status": "PARTIAL", "followup_pending_only": flag}
    payload["diagnostics"]["funnel"].update(accepted_plans=1, indicator_qualified_candidates=1,
        unresolved=1, plan_rejected_candidates=0, plan_rejection_counts={})
    assert presentation(payload)["title"] == "Datenabdeckung unvollständig"


def test_real_coverage_gap_overrides_a_false_followup_only_flag():
    payload = report("entry_pending")
    payload["data_quality"] = {"status": "PARTIAL", "followup_pending_only": True}
    payload["diagnostics"]["funnel"].update(accepted_plans=1, indicator_qualified_candidates=1,
        unresolved=1, plan_rejected_candidates=0, plan_rejection_counts={})
    payload["diagnostics"]["coverage"]["missing_sessions_by_ticker"] = {"QA": ["2026-08-03"]}
    result = presentation(payload)
    assert result["title"] == "Datenabdeckung unvollständig"
    assert result["followupPendingOnly"] is False


@pytest.mark.parametrize("completed,title", [(0, "Trade-Ergebnis noch offen"),
    (1, "BI-Backtest ausgewertet · Ergebnisse noch offen")])
def test_real_api_null_stage_filled_followup_only_does_not_become_a_history_gap(completed, title):
    payload = report(None)
    payload["total_trades"] = completed
    payload["data_quality"] = {"status": "PARTIAL", "followup_pending_only": True}
    payload["diagnostics"]["funnel"].update(accepted_plans=2, indicator_qualified_candidates=2,
        filled_trades=2, unresolved=2-completed, plan_rejected_candidates=0, plan_rejection_counts={})
    result = presentation(payload)
    assert result["title"] == title
    assert result["followupPendingOnly"] is True


def test_missing_fetch_session_overrides_false_future_tail_flag_with_null_stage():
    payload = report(None)
    payload["data_quality"] = {"status": "PARTIAL", "followup_pending_only": True}
    payload["diagnostics"]["funnel"].update(accepted_plans=1, indicator_qualified_candidates=1,
        filled_trades=1, unresolved=1, plan_rejected_candidates=0, plan_rejection_counts={})
    payload["diagnostics"]["coverage"].update(expected_fetch_sessions=100, loaded_fetch_sessions=99)
    result = presentation(payload)
    assert result["title"] == "Datenabdeckung unvollständig"
    assert result["followupPendingOnly"] is False


@pytest.mark.parametrize("stage,partial", [("data_incomplete", False), ("plan_rejected", True)])
def test_data_gaps_have_precedence_over_no_setups(stage, partial):
    payload = report(stage)
    if partial:
        payload["data_quality"] = {"status": "PARTIAL"}
    result = presentation(payload)
    assert result["title"] == "Datenabdeckung unvollständig"
    assert "kein vollständiges Marktergebnis" in result["detail"]


def test_legacy_diagnosis_does_not_hide_known_data_incompleteness():
    payload = report()
    payload.pop("diagnostics")
    payload["data_quality"] = {"status": "PARTIAL"}
    result = presentation(payload)
    assert result["title"] == "Datenabdeckung unvollständig"
    assert "Prüfzähler fehlen" in result["detail"]
    assert result["available"] is False


@pytest.mark.parametrize("diagnostics", [None, {}, {"schema_version": 2, "coverage": {}, "funnel": {}},
    {"schema_version": 1, "coverage": [], "funnel": {}},
    {"schema_version": 1, "coverage": {}, "funnel": []}])
def test_old_or_bad_diagnostics_never_invent_zero_candidates(diagnostics):
    payload = {"request": {"strategy": "scanner_bi_long"}, "total_trades": 0,
               "n_tickers": 200, "diagnostics": diagnostics}
    result = presentation(payload)
    assert result["title"] == "Ursache der Nullanzeige nicht gespeichert"
    assert "neuer Lauf" in result["detail"]
    assert result["available"] is False
    assert result["counters"] == [["Aktien ausgewählt", 200]]


@pytest.mark.parametrize("bad_count", [None, False, True, "0", "7", -1, 2.5])
def test_missing_or_invalid_counts_stay_unknown_not_zero(bad_count):
    payload = report()
    payload["diagnostics"]["funnel"]["indicator_qualified_candidates"] = bad_count
    result = presentation(payload)
    assert result["title"] == "Ursache der Nullanzeige nicht gespeichert"
    assert "BI-Setups" not in dict(result["counters"])


def test_inconsistent_stage_does_not_claim_all_candidates_rejected():
    payload = report()
    payload["diagnostics"]["funnel"]["plan_rejected_candidates"] = 3
    assert presentation(payload)["title"] == "Ursache der Nullanzeige nicht gespeichert"


def test_valid_filled_study_and_source_provenance_are_preserved():
    payload = report(None)
    payload["total_trades"] = 4
    payload["diagnostics"]["funnel"].update(accepted_plans=7, filled_trades=4,
            plan_rejected_candidates=0, plan_rejection_counts={})
    result = presentation(payload)
    assert result["title"] == "BI-Backtest ausgewertet"
    assert result["zeroResult"] is False
    assert dict(result["provenance"])["Planversion"] == payload["plan_version"]
    assert dict(result["provenance"])["Ausführungsmodell"] == payload["execution_model"]
    assert dict(result["provenance"])["Vergleichsumfang"] == payload["parity_scope"]


def test_diagnosis_belongs_to_saved_result_not_current_dropdown():
    payload = report()
    payload["request"]["strategy"] = "sma_crossover"
    assert presentation(payload) is None
    payload["request"]["strategy"] = "scanner_bi_short"
    assert presentation(payload) is not None


def test_primary_ui_is_compact_and_all_diagnostic_explanations_are_collapsed():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert 'data-testid="backtest-bi-diagnosis"' in html
    assert 'data-testid="backtest-bi-funnel"' in html
    assert "{verdict && !biDiagnosis?.zeroResult && !biDiagnosis?.followupPendingOnly && (" in html
    assert '<span className="font-semibold">Ticker:</span>' not in html
    for marker in ('data-testid="backtest-methodology"', 'data-testid="backtest-bi-diagnostic-details"'):
        tag = html[html.rfind("<details", 0, html.index(marker)):html.index(">", html.index(marker)) + 1]
        assert " open" not in tag
        assert "defaultOpen" not in tag


def test_reason_counters_do_not_turn_boolean_or_missing_into_observed_counts():
    payload = report()
    payload["diagnostics"]["funnel"]["plan_rejection_counts"] = {
        "observed": 7, "missing": None, "boolean": False, "string": "0"}
    assert presentation(payload)["reasonCounts"] == [["observed", 7]]


def test_bi_metric_names_evaluated_trades_without_relabeling_other_strategies():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    label = "{biDiagnosis ? 'Ausgewertete Trades' : 'Trades'}"
    assert label in html
    metric_block = html[html.index(label):html.index(label) + 240]
    assert "formatBacktestMetric(results.total_trades, 0)" in metric_block
    assert "filled_trades" not in metric_block
    for strategy in ("scanner_bi_long", "scanner_bi_short", "bi_long", "bi_short"):
        assert presentation({"strategy": strategy, "total_trades": 0}) is not None
    for strategy in ("sma_crossover", "scanner_momentum_long", "crypto_long"):
        assert presentation({"strategy": strategy, "total_trades": 0}) is None
