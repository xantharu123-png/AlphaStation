"""Mail explanations are read-only and must not invent delivery permission."""
from copy import deepcopy
import json
import pytest

import api
from modules import scanner_visibility
from test_frontend_candidate_visibility import run
from test_frontend_candidate_visibility import SOURCE
from test_frontend_scanner_lifecycle import evaluate


def summary(**updates):
    args = dict(row={"score": 94}, state={"score": 67},
                reasons=["score_below_alert_threshold"], labels={}, minimum_score=80,
                assessed_at=1000, complete=True)
    args.update(updates)
    return scanner_visibility.mail_check_summary(**args)


def test_explanation_preserves_setup_and_computed_score_without_mutation():
    row, state = {"score": 94}, {"score": 67}
    before = deepcopy((row, state))
    result = summary(row=row, state=state)
    assert (row, state) == before
    assert result["setup_score"] == 94 and result["trade_score"] == 67
    assert result["status"] == "blocked"
    assert "67" in result["reasons"][0]["label"] and "80" in result["reasons"][0]["label"]
    assert "alertable_now" not in result


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, 101])
def test_bad_score_cannot_produce_passed_preview(value):
    assert summary(state={"score": value}, reasons=[])["status"] == "unavailable"


def test_partial_preview_remains_unavailable():
    assert summary(state={"score": 95}, reasons=[], complete=False)["status"] == "unavailable"


def test_preview_pass_is_explicitly_not_delivery_or_execution():
    result = summary(state={"score": 95}, reasons=[])
    assert result["status"] == "checks_passed"
    row = {"mail_check": result, "visibility_status": "candidate_warning", "visibility_is_trade_signal": False}
    view = run(f"scannerMailPresentation({json.dumps(row)})")
    assert view["status"] == "checks_passed" and "Versand separat" in view["label"]
    assert run(f"scannerCandidatePresentation({json.dumps(row)})")["status"] == "candidate_warning"


def test_frontend_rejects_false_pass_and_malformed_scores():
    row = {"mail_check": summary(state={"score": 10}, reasons=[])}
    assert run(f"scannerMailPresentation({json.dumps(row)})")["status"] == "unavailable"
    row["mail_check"]["trade_score"] = True
    assert run(f"scannerMailPresentation({json.dumps(row)})")["score"] is None
    assert run('scannerMailPresentation({})') is None


def test_momentum_breakdown_is_separate_from_mail_score_and_retest():
    quality = {"schema_version": 1, "timeframe": "1D", "evidence": "completed_daily",
               "score": 70, "maximum_score": 96, "mail_min_score": 78,
               "semantics": "daily_bar_quality_not_future_continuation_or_retest",
               "components": [{"label": "Volumen", "points": 14, "max_points": 22}],
               "deductions": []}
    view = run(f"scannerMomentumQualityPresentation({json.dumps({'momentum_quality': quality})})")
    assert view["deficit"] == 8
    assert view["parts"] == ["Volumen: 14/22"]
    assert "abgeschlossenen" in view["label"]
    assert "released" not in view
    quality["score"] = None
    assert run(f"scannerMomentumQualityPresentation({json.dumps({'momentum_quality': quality})})") is None


def test_quality_rejection_is_visible_but_does_not_rewrite_scanner_release(monkeypatch):
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *_a: {
        "score": 94, "grade": "S", "suppression_reasons": [], "alertable_now": True})
    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state", lambda *_a, **_k:
                        (False, "momentum_mail_blocked_daily_quality_below_threshold"))
    state = api._scanner_result_trade_state("stock_strategy", {"ticker": "QA", "score": 94})
    assert state["alertable_now"] is True
    assert state["mail_check"]["status"] == "blocked"
    assert state["mail_check"]["reasons"][0]["code"] == "momentum_mail_blocked_daily_quality_below_threshold"


@pytest.mark.parametrize("count", [0, 2])
def test_finished_scan_with_exclusions_is_not_error_or_full_coverage(count):
    diag = {"coverage": "complete_with_exclusions", "excluded_data_symbols": 1}
    data = {"cached_at": "2026-09-24T12:00:00Z", "diagnostics": diag, "data": []}
    assert evaluate(f"scannerPollOutcome({json.dumps(data)},null,true)") == "complete"
    info = {"info": data, "kind": "bi", "hasLoaded": True, "count": count}
    view = evaluate(f"scannerEvidenceState({json.dumps(info)})")
    assert view["tone"] == "warning"
    assert "1 Aktie(n)" in view["text"] and "Datenausschlüssen" in view["text"]
    assert "kein erfolgreich" not in view["text"]


def test_old_or_running_evidence_keeps_priority_over_exclusion_success():
    info = {"cached_at": "2026-09-24T12:00:00Z", "diagnostics": {"coverage": "complete_with_exclusions"}}
    view = evaluate(f"scannerEvidenceState({json.dumps({'info': info, 'hasLoaded': True, 'running': True, 'count': 0})})")
    assert view["tone"] == "running" and "Altstand" in view["text"]


def test_display_does_not_replace_legitimate_capped_score_with_raw_score(monkeypatch):
    observed = []
    def classify(_scanner, row, _now):
        observed.append(api._extract_alert_score(row))
        return {"score": 60, "suppression_reasons": ["score_below_alert_threshold"], "grade": "B"}
    monkeypatch.setattr(api, "_classify_alert_candidate", classify)
    row = {"ticker": "QA", "score": 70, "raw_score": 100, "grade": "B"}
    api._apply_scanner_result_trade_state(row, "turtle")
    api._apply_scanner_result_trade_state(row, "turtle")
    assert observed == [70, 70]
    assert row["mail_check"]["setup_score"] == 70


def test_low_score_without_reasons_is_unknown_not_a_server_pass():
    assert summary(state={"score": 10}, reasons=[])["status"] == "unavailable"


@pytest.mark.parametrize("patch", [{"evidence": "unknown"}, {"maximum_score": 0, "mail_min_score": 0, "score": 0}, {"semantics": None}])
def test_unknown_momentum_schema_cannot_invent_pass(patch):
    q = {"schema_version": 1, "timeframe": "1D", "evidence": "completed_daily",
         "semantics": "daily_bar_quality_not_future_continuation_or_retest",
         "score": 70, "maximum_score": 96, "mail_min_score": 78, **patch}
    assert run(f"scannerMomentumQualityPresentation({json.dumps({'momentum_quality': q})})") is None


def test_explanation_disclosures_do_not_open_ticker_sidebar():
    area = SOURCE[SOURCE.index("function ScannerCandidateStatus("):SOURCE.index("const BREAKOUT_WITHOUT_RETEST_CODE")]
    assert area.count("onClick={event => event.stopPropagation()}") == 2
