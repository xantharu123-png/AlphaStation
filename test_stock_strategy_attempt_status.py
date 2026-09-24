"""Offline result-status projection; no provider, scanner, mail or order calls."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import api


STRATEGIES = {
    "Momentum Breakout Long": "momentum_breakout_long",
    "Gap Momentum Long": "gap_momentum_long",
    "Gap Momentum Short": "gap_momentum_short",
    "Cup and Handle Breakout": "cup_and_handle_breakout",
}


@pytest.fixture
def status_io(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 9, 14, 15, tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_scan_status", {})
    cache = tmp_path / "final.json"
    cache.write_text("{}", encoding="utf8")
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(cache))
    state = {"stamp": "2026-09-08T12:00:00", "coverage": "complete", "partial": False}
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **kw: (
        [], state["stamp"], {"cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
                            "diagnostics": {"coverage": state["coverage"], "final_results": 0}}, state["partial"]))
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *a: rows)
    monkeypatch.setattr(api, "_apply_scanner_visibility_policy", lambda scanner, rows: rows)
    monkeypatch.setattr(api, "_scan_quality_payload", lambda *a: {
        "warnings": [], "data_source": "synthetic", "exclusion_policy": []})
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: pytest.fail("No provider calls"))
    return tmp_path, state


def attempt_payload(strategy="Momentum Breakout Long", status="error"):
    return {"schema_version": 1, "attempt_kind": "stock_strategy", "strategy_slug": STRATEGIES[strategy],
            "run_id": "a" * 32, "code_revision": "123456abcdef", "status": status,
            "started_at": "2026-09-14T11:58:54+00:00", "updated_at": "2026-09-14T11:58:57+00:00",
            "results": [], "result_count": 0 if status == "complete" else None,
            "error_code": "scan_data_unavailable" if status == "error" else None,
            "diagnostics": {"coverage": "complete" if status == "complete" else "incomplete",
                            "final_results": 0 if status == "complete" else None,
                            "universe_count": 13171, "rejected": {"missing_price_or_prev_close": 5479}}}


def write_attempt(root, payload):
    path = root / ("stock_strategy_" + payload["strategy_slug"] + "_attempt.json")
    path.write_text(json.dumps(payload), encoding="utf8")
    return path


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_auto_leaf_failure_is_visible_after_restart_without_replacing_old_result_or_run_id(status_io, strategy):
    root, state = status_io
    path = write_attempt(root, attempt_payload(strategy))
    before = path.read_bytes()
    api._scan_status["strategy_scan"] = {"running": False, "last_error": "scan_data_incomplete"}
    result = api.get_scan_results(strategy, None, "stocks")
    assert result.scan_error == "scan_data_unavailable"
    assert result.count == 0 and result.cached_at == state["stamp"]
    assert result.diagnostics["coverage"] == "complete"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"
    assert result.diagnostics["attempt_diagnostics"]["final_results"] is None
    assert result.diagnostics["attempt_diagnostics"]["rejected"]["missing_price_or_prev_close"] == 5479
    assert result.diagnostics["latest_attempt"]["attempt_run_id"] == "a" * 32
    assert result.scan_run_id is None and not result.scan_running
    assert path.read_bytes() == before and set(api._scan_status) == {"strategy_scan"}


def test_newer_manual_ack_owns_polling_over_older_persisted_failure(status_io):
    root, _ = status_io
    write_attempt(root, attempt_payload())
    key = api._strategy_scan_status_key("Momentum Breakout Long")
    api._scan_status[key] = {"running": True, "last_run_id": "manual-ack",
                             "last_attempt_at": "2026-09-14T12:01:00+00:00"}
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error is None and result.scan_running is True
    assert result.scan_run_id == "manual-ack"
    assert "attempt_diagnostics" not in result.diagnostics


@pytest.mark.parametrize("manual_status", ["complete", "error"])
def test_newer_finished_manual_attempt_wins_over_older_file(status_io, manual_status):
    root, _ = status_io
    write_attempt(root, attempt_payload())
    key = api._strategy_scan_status_key("Momentum Breakout Long")
    state = {"running": False, "last_run_id": "manual-ack", "last_attempt_at": "2026-09-14T12:01:00+00:00"}
    if manual_status == "error":
        state.update(last_error="scan_provider_unauthorized", last_attempt_diagnostics={"coverage": "incomplete"})
    api._scan_status[key] = state
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_run_id == "manual-ack"
    assert result.scan_error == ("scan_provider_unauthorized" if manual_status == "error" else None)


def test_newer_complete_cache_clears_persisted_error_without_mutating_ram(status_io):
    root, cache = status_io
    write_attempt(root, attempt_payload())
    cache["stamp"] = "2026-09-14T12:01:00+00:00"
    key = api._strategy_scan_status_key("Momentum Breakout Long")
    state = {"running": False, "last_run_id": "old-manual",
             "last_attempt_at": "2026-09-14T10:00:00+00:00"}
    api._scan_status[key] = dict(state)
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error is None and "attempt_diagnostics" not in result.diagnostics
    assert result.diagnostics["coverage"] == "complete" and result.count == 0
    assert api._scan_status[key] == state


@pytest.mark.parametrize("with_attempt", [False, True])
def test_cache_cannot_clear_manual_failure_without_its_terminal_time(status_io, with_attempt):
    root, cache = status_io
    if with_attempt:
        payload = attempt_payload()
        payload.update(started_at="2026-09-14T10:01:00+00:00", updated_at="2026-09-14T11:00:00+00:00")
        write_attempt(root, payload)
    cache["stamp"] = "2026-09-14T10:30:00+00:00"
    key = api._strategy_scan_status_key("Momentum Breakout Long")
    state = {"running": False, "last_error": "scan_data_unavailable", "last_run_id": "manual-ack",
             "last_attempt_at": "2026-09-14T10:00:00+00:00", "last_attempt_diagnostics": {"coverage": "incomplete"}}
    api._scan_status[key] = dict(state)
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_data_unavailable"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"
    assert api._scan_status[key] == state


def test_orphaned_running_file_never_claims_live_worker_or_completed_zero(status_io):
    root, _ = status_io
    write_attempt(root, attempt_payload(status="running"))
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert not result.scan_running and result.scan_run_id is None
    assert result.diagnostics["latest_attempt"]["status"] == "running"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"
    assert result.diagnostics["attempt_diagnostics"]["final_results"] is None


@pytest.mark.parametrize("stamp", ["2026-09-14T11:58:55+00:00", "2026-09-14T11:58:57+00:00", "2035-01-01T00:00:00+00:00"])
def test_overlapping_or_future_cache_cannot_hide_newer_terminal_failure(status_io, stamp):
    root, cache = status_io
    write_attempt(root, attempt_payload())
    cache["stamp"] = stamp
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_data_unavailable"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"


def test_generic_fallback_cache_cannot_prove_recovery_of_requested_strategy(status_io, monkeypatch):
    root, cache = status_io
    write_attempt(root, attempt_payload())
    cache["stamp"] = "2026-09-14T12:01:00+00:00"
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(root / "missing-own.json"))
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_data_unavailable"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"


@pytest.mark.parametrize("identity", [None, "Gap Momentum Short", "Momentum Breakout Long"])
def test_fallback_only_exposes_rows_with_consistent_requested_strategy_identity(status_io, monkeypatch, identity):
    root, _ = status_io
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(root / "missing-own.json"))
    diagnostics = {"coverage": "complete", "final_results": 1}
    if identity is not None:
        diagnostics["strategy"] = identity
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **kw: (
        [{"ticker": "SYNTHETIC"}], "2026-09-14T12:01:00+00:00",
        {"cache_version": api.STOCK_STRATEGY_CACHE_VERSION, "diagnostics": diagnostics}, False))
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    if identity == "Momentum Breakout Long":
        assert result.count == 1 and result.data[0]["ticker"] == "SYNTHETIC"
        assert result.diagnostics["coverage"] == "complete"
    else:
        assert result.count == 0 and result.data == [] and result.cached_at is None
        assert result.diagnostics["coverage"] == "unknown"
        assert "SYNTHETIC" not in result.model_dump_json()


def test_complete_attempt_alone_does_not_publish_result_or_scheduler_completion(status_io):
    root, cache = status_io
    write_attempt(root, attempt_payload(status="complete"))
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.cached_at == cache["stamp"]
    assert result.diagnostics["latest_attempt"]["status"] == "complete"
    assert not result.scan_running and result.scan_run_id is None
    assert result.scan_error is None and "attempt_diagnostics" not in result.diagnostics


@pytest.mark.parametrize("field,bad", [
    ("schema_version", True), ("attempt_kind", "stock_strategy_sweep"), ("strategy_slug", "../PRIVATE"),
    ("run_id", "PRIVATE"), ("code_revision", "PRIVATE"), ("status", "PRIVATE"),
    ("started_at", "2026-09-14T11:58:54"), ("updated_at", "2026-09-14T11:00:00Z"),
    ("updated_at", "9999-12-31T23:59:59-23:59"), ("updated_at", "2035-01-01T00:00:00Z"),
    ("error_code", "PRIVATE_PROVIDER_MESSAGE"), ("result_count", 0), ("results", [{"ticker": "PRIVATE"}]),
])
def test_invalid_attempt_is_explicitly_unavailable_not_raw_error_or_zero(status_io, field, bad):
    root, _ = status_io
    payload = attempt_payload()
    original_slug = payload["strategy_slug"]
    payload[field] = bad
    path = root / ("stock_strategy_" + original_slug + "_attempt.json")
    path.write_text(json.dumps(payload), encoding="utf8")
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.diagnostics["latest_attempt"] == {"available": False, "reason": "invalid"}
    assert "PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize("bad_count", [True, -1, 1.5, "PRIVATE", 10**9 + 1])
def test_attempt_numeric_and_private_fields_are_projected_not_exposed(status_io, bad_count):
    root, _ = status_io
    payload = attempt_payload()
    payload["PRIVATE_EXTRA"] = "PRIVATE_BODY"
    payload["diagnostics"].update(universe_count=bad_count, rows=[{"ticker": "PRIVATE"}])
    payload["diagnostics"]["rejected"]["PRIVATE_REASON"] = 123
    write_attempt(root, payload)
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_data_unavailable"
    assert "universe_count" not in result.diagnostics["attempt_diagnostics"]
    assert "PRIVATE" not in result.model_dump_json()


def test_attempt_reader_missing_oversized_directory_and_duplicate_json_are_unavailable(status_io, monkeypatch):
    root, _ = status_io
    read = lambda: api._read_stock_strategy_attempt("Momentum Breakout Long")
    assert read() == {"available": False, "reason": "missing"}
    path = write_attempt(root, attempt_payload())
    monkeypatch.setattr(api, "_STOCK_ATTEMPT_READ_MAX_BYTES", 8)
    assert read() == {"available": False, "reason": "invalid"}
    monkeypatch.setattr(api, "_STOCK_ATTEMPT_READ_MAX_BYTES", 131072)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf8")
    assert read() == {"available": False, "reason": "invalid"}
    path.unlink()
    path.mkdir()
    assert read() == {"available": False, "reason": "invalid"}


def test_unknown_strategy_reader_never_opens_arbitrary_path(status_io, monkeypatch):
    monkeypatch.setattr(api.os, "open", lambda *a, **kw: pytest.fail("Unknown strategy must not open"))
    assert api._read_stock_strategy_attempt("../PRIVATE") == {"available": False, "reason": "not_supported"}
