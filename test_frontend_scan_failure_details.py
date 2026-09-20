"""Safe, executable failure diagnostics; no browser, API or provider access."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, evaluate


@pytest.mark.parametrize("code,fragment", [
    ("scan_timeout", "Zeitlimit"),
    ("scan_cache_publish_failed", "gespeichert"),
    ("scan_partial_cache", "Zwischenstand"),
    ("scan_already_running", "Kein paralleler Ersatzlauf"),
])
def test_failure_banner_identifies_known_cause_without_claiming_zero_signals(code, fragment):
    text = evaluate(f"scannerPublicFailure(200, {json.dumps({'scan_error': code})})")
    assert fragment in text


def details(payload):
    return evaluate(f"scannerFailureDetails({json.dumps(payload)})")


def test_latest_attempt_measurements_are_separate_from_preserved_old_result():
    payload = {"scan_error": "scan_timeout", "diagnostics": {
        "leaf_elapsed_seconds": 99, "stage_elapsed_ms": {"structure": 999999},
        "attempt_diagnostics": {"leaf_elapsed_seconds": 1200, "runtime_phase": "work_timeout",
            "provider_requests": 11, "history_cache_hits": 9, "rate_wait_seconds": 0,
            "stage_timing_semantics": "per_leaf_inclusive_elapsed_ms_not_additive",
            "stage_elapsed_ms": {"history": 1234, "structure": 5000, "cache_publish": 0}},
    }}
    result = details(payload)
    assert result["code"] == "scan_timeout"
    assert result["phase"] == "Zeitlimit erreicht"
    assert result["elapsedSeconds"] == 1200
    assert result["rateWaitSeconds"] == 0
    assert result["stageSeconds"] == [["Historie", 1.2], ["Struktur", 5], ["Zwischenstand speichern", 0]]


def test_missing_attempt_measurements_do_not_become_zero_or_reuse_old_cache():
    result = details({"scan_error": "scan_timeout", "diagnostics": {"leaf_elapsed_seconds": 123}})
    assert result["elapsedSeconds"] is None
    assert result["providerRequests"] is None
    assert result["stageSeconds"] == []


def test_verified_persisted_error_can_supply_code_and_measurements():
    result = details({"diagnostics": {"latest_attempt": {"available": True, "status": "error",
        "error_code": "scan_cache_publish_failed", "diagnostics": {"leaf_elapsed_seconds": 65}}}})
    assert result["code"] == "scan_cache_publish_failed"
    assert result["elapsedSeconds"] == 65


@pytest.mark.parametrize("status,code", [
    ("complete", "scan_timeout"), ("running", "scan_timeout"),
    ("error", "scan_data_invalid"), ("error", "UNKNOWN"),
])
def test_mismatched_persisted_attempt_never_supplies_failure_measurements(status, code):
    result = details({"scan_error": "scan_timeout", "diagnostics": {"latest_attempt": {
        "available": True, "status": status, "error_code": code,
        "diagnostics": {"leaf_elapsed_seconds": 12, "runtime_phase": "complete"}}}})
    assert result["code"] == "scan_timeout"
    assert result["elapsedSeconds"] is None
    assert result["phase"] is None


def test_explicit_failed_attempt_measurements_take_precedence_over_persisted_old_success():
    result = details({"scan_error": "scan_timeout", "diagnostics": {
        "attempt_diagnostics": {"leaf_elapsed_seconds": 1200, "runtime_phase": "work_timeout"},
        "latest_attempt": {"available": True, "status": "complete", "diagnostics": {
            "leaf_elapsed_seconds": 12, "runtime_phase": "complete"}}}})
    assert result["elapsedSeconds"] == 1200
    assert result["phase"] == "Zeitlimit erreicht"


def test_unknown_top_level_error_does_not_match_normalized_persisted_error():
    result = details({"scan_error": "UNKNOWN", "diagnostics": {"latest_attempt": {
        "available": True, "status": "error", "error_code": "scan_failed",
        "diagnostics": {"leaf_elapsed_seconds": 12}}}})
    assert result["code"] == "scan_failed"
    assert result["elapsedSeconds"] is None


@pytest.mark.parametrize("latest", [{}, {"available": False, "status": "error", "error_code": "scan_timeout"},
                                  {"available": True, "status": "complete", "error_code": "scan_timeout"}])
def test_missing_or_nonfailed_persisted_attempt_does_not_invent_failure(latest):
    assert details({"diagnostics": {"latest_attempt": latest}}) is None


@pytest.mark.parametrize("bad", [-1, 1e12, "123", True, None, "SECRET"])
def test_diagnostics_reject_unbounded_or_wrong_typed_measurements(bad):
    result = details({"scan_error": "SECRET", "diagnostics": {"attempt_diagnostics": {
        "runtime_phase": "SECRET", "leaf_elapsed_seconds": bad, "provider_requests": bad,
        "stage_timing_semantics": "per_leaf_inclusive_elapsed_ms_not_additive",
        "stage_elapsed_ms": {"structure": bad, "SECRET": 100}}}})
    assert result["code"] == "scan_failed"
    assert result["phase"] is None
    assert result["elapsedSeconds"] is None
    assert result["providerRequests"] is None
    assert result["stageSeconds"] == []
    assert "SECRET" not in json.dumps(result)


def test_unknown_timing_semantics_are_not_misrepresented_as_comparable_seconds():
    result = details({"scan_error": "scan_failed", "diagnostics": {"attempt_diagnostics": {
        "stage_timing_semantics": "unknown", "stage_elapsed_ms": {"structure": 10}}}})
    assert result["stageSeconds"] == []


@pytest.mark.parametrize("phase", ["__proto__", "constructor", "toString", [], {}])
def test_unknown_phase_cannot_render_object_prototype(phase):
    result = details({"scan_error": "scan_failed", "diagnostics": {"attempt_diagnostics": {"runtime_phase": phase}}})
    assert result["phase"] is None


def test_shipped_ui_exposes_safe_details_and_retained_result_warning():
    evidence = SOURCE[SOURCE.index("function ScannerEvidence("):SOURCE.index("// Scanner Tab")]
    assert 'Technische Diagnose des letzten Versuchs' in evidence
    assert '<code>{failure.code}</code>' in evidence
    assert 'nicht zum fehlgeschlagenen Versuch' in evidence
    assert 'Zeitbloecke koennen sich ueberschneiden; nicht addieren' in evidence
    assert 'typeof summary.checked' in evidence
    assert 'onClick={feed.refresh}' in evidence
