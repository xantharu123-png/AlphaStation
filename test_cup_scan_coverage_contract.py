"""Cup special-filter admission coverage stays bounded and separate from universe coverage."""
import json

import pytest

from test_collect_server_evidence import _attempt_payload, collector
from test_frontend_scanner_lifecycle import SOURCE, evaluate


COUNTS = {"special_filter_input_count": 539, "special_filter_checked_count": 180,
          "special_filter_unexamined_count": 359, "special_filter_limit": 180}


@pytest.mark.parametrize("kind", ["cache", "attempt"])
def test_only_fixed_special_filter_counts_are_exported(tmp_path, kind):
    payload = {"results": [], "diagnostics": {}} if kind == "cache" else _attempt_payload("cup_and_handle_breakout")
    payload["diagnostics"].update(COUNTS, private_pattern_tickers=["PRIVATE_FIXTURE"])
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_cache_summary(path) if kind == "cache" else collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout")
    assert all(result["numeric_diagnostics"][key] == value for key, value in COUNTS.items())
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("bad", [True, -1, 1.5, None, "180", []])
def test_invalid_coverage_is_not_exported_as_a_known_count(tmp_path, bad):
    payload = _attempt_payload("cup_and_handle_breakout")
    payload["diagnostics"].update({key: bad for key in COUNTS})
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout")
    assert not (set(COUNTS) & set(result["numeric_diagnostics"]))


@pytest.mark.parametrize("attempt", [True, False])
def test_frontend_distinguishes_special_filter_from_full_universe_coverage(attempt):
    data = {"universe_count": 12588, "checked": 12588, **COUNTS}
    payload = {"attempt_diagnostics": data} if attempt else data
    result = evaluate(f"scannerDiagnosticSummary({json.dumps(payload)})")
    assert result["attempt"] is attempt
    assert result["specialCoverage"] == {"input": 539, "checked": 180, "unexamined": 359, "limit": 180}
    assert result["checked"] == 12588


@pytest.mark.parametrize("change", [
    {"special_filter_checked_count": 181}, {"special_filter_unexamined_count": 0},
    {"special_filter_input_count": "539"}, {"special_filter_limit": None},
])
def test_frontend_does_not_present_inconsistent_special_coverage(change):
    assert evaluate(f"scannerDiagnosticSummary({json.dumps({**COUNTS, **change})})")["specialCoverage"] is None


def test_legacy_missing_coverage_stays_unknown_and_shipped_component_labels_limit():
    assert evaluate("scannerDiagnosticSummary({universe_count:12588, checked:12588})")["specialCoverage"] is None
    assert 'Spezialpruefung Ergebnisstand:' in SOURCE
    assert 'Abgearbeitet umfasst auch Ablehnungen wegen fehlender Historie.' in SOURCE
    assert 'Kein Nachweis einer Musterpruefung des gesamten Aktienuniversums.' in SOURCE
