"""Private export keeps stock exclusion reasons, never provider messages."""
import json

import pytest

from test_collect_server_evidence import collector


@pytest.mark.parametrize("source", ["cache", "attempt"])
def test_stock_history_exclusions_and_errors_export_bounded_metadata(tmp_path, source):
    counts = {"excluded_data_symbols": 3, "empty_history_symbols": 2,
              "invalid_history_symbols": 1, "data_retry_attempts": 2,
              "data_retry_recovered": 1, "data_retry_failed": 1,
              "data_retry_budget_exhausted": 0, "data_retry_observation_mismatches": 0}
    payload = {
        "schema_version": 1, "attempt_kind": "stock_strategy",
        "strategy_slug": "cup_and_handle_breakout", "run_id": "ab" * 16,
        "code_revision": "012345abcdef", "started_at": "2026-09-25T12:00:00Z",
        "updated_at": "2026-09-25T12:01:00Z", "results": [],
        "status": "complete", "result_count": 1, "error_code": None,
        "diagnostics": {
            **counts, "coverage": "complete_with_exclusions",
            "stock_history_error_counts": {"invalid_bar_value": 1,
                                            "http_rate_limited": 2, "PRIVATE_TOKEN": 6},
            "stock_history_error_fields": {"c": 1, "PRIVATE_SYMBOL": 1},
            "stock_history_error_value_classes": {"zero_price": 1, "PRIVATE_PRICE": 2},
            "stock_history_error_positions": {"interior": 1, "PRIVATE_RESPONSE": 3},
            "rejected": {"empty_daily_history": 2, "invalid_daily_history": 1},
        },
    }
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    before = path.read_bytes()
    result = (collector.safe_cache_summary(path) if source == "cache" else
              collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout"))
    assert result["coverage"] == "complete_with_exclusions"
    assert result["numeric_diagnostics"] == counts
    assert result["stock_history_error_counts"] == {
        "invalid_bar_value": 1, "http_rate_limited": 2, "_omitted_categories": 1}
    assert result["stock_history_error_fields"] == {"c": 1, "_omitted_categories": 1}
    assert result["stock_history_error_value_classes"] == {"zero_price": 1, "_omitted_categories": 1}
    assert result["stock_history_error_positions"] == {"interior": 1, "_omitted_categories": 1}
    assert result["rejected"] == {"empty_daily_history": 2, "invalid_daily_history": 1}
    assert "PRIVATE" not in json.dumps(result)
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", [True, -1, 1.5, "1", None, float("nan")])
def test_stock_history_export_does_not_coerce_invalid_counters(tmp_path, bad):
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps({"diagnostics": {
        "empty_history_symbols": bad, "invalid_history_symbols": bad,
        "stock_history_error_counts": {"invalid_bar_value": bad},
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["numeric_diagnostics"] == {}
    assert result["stock_history_error_counts"].get("invalid_bar_value") is None
    json.dumps(result, allow_nan=False)
