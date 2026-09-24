"""Read-only, privacy-bounded export of BI's explicitly reduced data scope."""
import json

import pytest

from test_collect_server_evidence import collector


COUNTERS = {
    "excluded_data_symbols": 1,
    "valid_data_symbols": 3,
    "data_retry_attempts": 2,
    "data_retry_recovered": 1,
    "data_retry_failed": 1,
    "data_retry_budget_exhausted": 0,
    "data_retry_observation_mismatches": 0,
}


@pytest.mark.parametrize("filename", ["bi_cache_short.json", "bi_scan_progress_short.json"])
@pytest.mark.parametrize("result_count", [0, 2])
def test_finished_exclusion_scope_and_all_seven_counters_survive_private_export(tmp_path, filename, result_count):
    path = tmp_path / filename
    path.write_text(json.dumps({
        "status": "done", "partial": False,
        "results": [{"ticker": "PRIVATE_TICKER", "email": "PRIVATE_RECIPIENT"}] * result_count,
        "detail": "PRIVATE_PROVIDER_BODY",
        "diagnostics": {
            "coverage": "complete_with_exclusions", **COUNTERS,
            "private_count": 123, "error": "PRIVATE_PROVIDER_KEY",
        },
    }), encoding="utf8")
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns

    result = collector.safe_cache_summary(path)

    assert result["available"] is True
    assert result["coverage"] == "complete_with_exclusions"
    assert result["numeric_diagnostics"] == COUNTERS
    assert result["raw_rows"] == result_count
    assert "PRIVATE" not in json.dumps(result)
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "PRIVATE", None])
def test_exclusion_counters_reject_booleans_and_other_non_counts_without_rewriting_source(tmp_path, value):
    path = tmp_path / "bi_cache_long.json"
    path.write_text(json.dumps({
        "results": [],
        "diagnostics": {"coverage": "complete_with_exclusions", **dict.fromkeys(COUNTERS, value),
                        "PRIVATE_UNKNOWN_COUNTER": 1},
    }), encoding="utf8")
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns

    result = collector.safe_cache_summary(path)

    assert result["coverage"] == "complete_with_exclusions"
    assert result["numeric_diagnostics"] == {}
    assert "PRIVATE" not in json.dumps(result)
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime
