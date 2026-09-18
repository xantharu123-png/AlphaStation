"""Private cache diagnostics must distinguish absence from zero actionable rows."""
import importlib.util
import json
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "scan_evidence", Path(__file__).parent / "scripts" / "collect_server_evidence.py")
collector = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collector)


def test_crypto_scan_stats_export_only_known_counts_and_boolean_flags(tmp_path):
    path = tmp_path / "crypto_explosion_cache.json"
    path.write_text(json.dumps({"results": [], "scan_stats": {
        "universe_count": 1300, "chart_checked": 1000, "max_chart_checks": 1000,
        "venue_workers": 4, "result_count": 80, "trade_now_count": 0, "armed_count": 80,
        "source_degraded": True, "incomplete": False,
        "by_exchange": {"bybit": 200, "binance": 300, "PRIVATE_VENUE": 4},
        "request_failures": {"PRIVATE_VENUE": "PRIVATE_API_KEY"},
        "PRIVATE_COUNT": 5, "scanner_note": "PRIVATE_NOTE",
    }}), encoding="utf8")

    result = collector.safe_cache_summary(path)

    assert result["scan_stats"] == {
        "numeric_counts": {"universe_count": 1300, "chart_checked": 1000,
                           "max_chart_checks": 1000, "venue_workers": 4,
                           "result_count": 80, "trade_now_count": 0, "armed_count": 80},
        "source_degraded": True, "incomplete": False,
        "by_exchange": {"bybit": 200, "binance": 300, "_omitted_categories": 1},
    }
    assert result["numeric_diagnostics"] == {}
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("value", [True, -1, 1.5, "7", None, {}, [], float("nan")])
def test_crypto_scan_counts_do_not_coerce_invalid_values(tmp_path, value):
    path = tmp_path / "crypto_explosion_cache.json"
    path.write_text(json.dumps({"scan_stats": {
        "chart_checked": value, "trade_now_count": value,
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["scan_stats"]["numeric_counts"] == {}
    json.dumps(result, allow_nan=False)


def test_crypto_cache_rows_count_actions_without_claiming_live_confirmation(tmp_path):
    path = tmp_path / "crypto_trade_signals_cache.json"
    rows = [
        {"trade_action": "JETZT_LONG", "trade_signal": "JETZT_TRADEN",
         "execution_trigger_ok": True, "Symbol": "PRIVATE_SYMBOL", "entry": 123},
        {"trade_action": "JETZT_SHORT", "trade_signal": "JETZT_TRADEN", "micro_trigger_ok": True},
        {"trade_action": "LONG_ARMED", "trade_signal": "WARTEN", "execution_trigger_ok": False},
        {"trade_action": "SHORT_WATCH", "trade_signal": "WARTEN", "micro_trigger_ok": False},
        {"trade_action": "LONG_ARMED", "trade_signal": "EXPLOSION_ARMED"},
        {"trade_action": "PRIVATE_ACTION", "trade_signal": {"PRIVATE": "SECRET"},
         "execution_trigger_ok": 1, "micro_trigger_ok": "true"},
        {}, "PRIVATE_ROW",
    ]
    path.write_text(json.dumps({"results": rows}), encoding="utf8")
    before = path.read_bytes()

    result = collector.safe_cache_summary(path)

    assert result["raw_rows"] == 8
    assert result["row_state_counts"] == {
        "semantics": "cached_rows_not_live_confirmation_or_delivery",
        "invalid_rows": 1,
        "trade_action": {"JETZT_LONG": 1, "JETZT_SHORT": 1, "LONG_ARMED": 2,
                         "SHORT_WATCH": 1, "_unrecognized": 1, "_missing": 1},
        "trade_signal": {"JETZT_TRADEN": 2, "WARTEN": 2, "EXPLOSION_ARMED": 1,
                         "_unrecognized": 1, "_missing": 1},
        "execution_trigger_ok": {"true": 1, "false": 1, "_missing": 4, "_unrecognized": 1},
        "micro_trigger_ok": {"true": 1, "false": 1, "_missing": 4, "_unrecognized": 1},
    }
    assert "PRIVATE" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)
    assert path.read_bytes() == before


def test_crypto_row_states_are_scoped_to_known_cache_files(tmp_path):
    path = tmp_path / "other_cache.json"
    path.write_text('{"results":[{"trade_action":"JETZT_LONG"}]}', encoding="utf8")
    assert "row_state_counts" not in collector.safe_cache_summary(path)


@pytest.mark.parametrize("payload,reason", [
    (b"not-json PRIVATE", "invalid_json"), (b"\xff", "invalid_json"),
    (b"[]", "invalid_payload"), (b"null", "invalid_payload"),
])
def test_cache_unavailable_has_bounded_reason_not_exception_content(tmp_path, payload, reason):
    path = tmp_path / "PRIVATE_cache.json"
    path.write_bytes(payload)
    assert collector.safe_cache_summary(path) == {"available": False, "reason": reason}


def test_missing_cache_is_not_reported_as_empty_or_invalid(tmp_path):
    path = tmp_path / "missing.json"
    assert collector.safe_cache_summary(path) == {"available": False, "reason": "missing"}
    assert not path.exists()


def test_oversized_cache_is_not_reported_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_bytes(b" " * 17)
    monkeypatch.setattr(collector, "CACHE_MAX_BYTES", 16)
    assert collector.safe_cache_summary(path) == {"available": False, "reason": "too_large"}


def test_unreadable_cache_reason_never_contains_private_path_or_os_error(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text("{}", encoding="utf8")
    def denied(*args):
        raise PermissionError("PRIVATE_PATH PRIVATE_KEY")
    monkeypatch.setattr(collector.os, "open", denied)
    assert collector.safe_cache_summary(path) == {"available": False, "reason": "unreadable"}


@pytest.mark.parametrize("source", ["cache", "attempt"])
def test_stock_plan_builder_outcomes_export_only_known_numeric_reasons(tmp_path, source):
    counts = {
        "invalid_entry_or_direction": 1, "causal_structure_missing": 2,
        "causal_structure_unavailable": 3, "crossed_resistance_unconfirmed": 4,
        "crossed_support_unconfirmed": 5, "no_structural_invalidation": 6,
        "invalid_stop_risk": 7, "invalid_trade_geometry": 8,
        "native_structure_plan": 9, "first_opposing_barrier_before_minimum_rr": 10,
        "direction_missing": 11, "plan_unavailable": 12,
    }
    payload = {
        "schema_version": 1, "attempt_kind": "stock_strategy",
        "strategy_slug": "gap_momentum_long", "run_id": "ab" * 16,
        "code_revision": "012345abcdef", "started_at": "2026-09-18T12:00:00Z",
        "updated_at": "2026-09-18T12:01:00Z", "results": [],
        "status": "complete", "result_count": 0, "error_code": None,
        "diagnostics": {"plan_build_counts": {**counts, "PRIVATE_REASON": 99}},
    }
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(payload), encoding="utf8")

    result = (collector.safe_cache_summary(path) if source == "cache" else
              collector.safe_strategy_attempt_summary(path, "gap_momentum_long"))

    assert result["plan_build_counts"] == {**counts, "_omitted_categories": 1}
    assert result["plan_build_count_semantics"] == "builder_outcomes_not_final_eligibility_or_delivery"
    assert "PRIVATE" not in json.dumps(result)
