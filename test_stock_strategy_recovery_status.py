"""Successful auto recovery must not display an older manual failure as latest."""
from copy import deepcopy

import pytest

import api
from test_stock_strategy_attempt_status import (
    STRATEGIES, attempt_payload, status_io, write_attempt,
)


def _old_manual_failure(strategy="Momentum Breakout Long"):
    state = {
        "running": False, "last_run_id": "old-manual-owner",
        "last_attempt_at": "2026-09-14T10:00:00+00:00",
        "last_run": "2026-09-14T09:00:00+00:00",
        "last_error": "scan_timeout",
        "last_attempt_diagnostics": {"coverage": "incomplete", "checked": 10},
    }
    api._scan_status[api._strategy_scan_status_key(strategy)] = deepcopy(state)
    return state


def _complete_attempt(root, strategy="Momentum Breakout Long"):
    payload = attempt_payload(strategy, status="complete")
    payload.update(started_at="2026-09-14T12:00:00+00:00", updated_at="2026-09-14T12:10:00+00:00")
    return write_attempt(root, payload)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_newer_complete_own_cache_and_attempt_clear_only_projected_manual_error(status_io, strategy):
    root, cache = status_io
    original = _old_manual_failure(strategy)
    path = _complete_attempt(root, strategy)
    before = path.read_bytes()
    cache["stamp"] = "2026-09-14T12:09:59+00:00"
    result = api.get_scan_results(strategy, None, "stocks")
    assert result.scan_error is None
    assert "attempt_diagnostics" not in result.diagnostics
    assert result.diagnostics["latest_attempt"]["status"] == "complete"
    assert result.diagnostics["coverage"] == "complete"
    assert not any("fehlgeschlagen" in warning for warning in result.warnings)
    assert result.cached_at == cache["stamp"] and result.count == 0
    assert result.scan_run_id == original["last_run_id"]
    assert result.scan_last_completed_at == original["last_run"]
    assert api._scan_status[api._strategy_scan_status_key(strategy)] == original
    assert path.read_bytes() == before


@pytest.mark.parametrize("stamp", ["2026-09-14T11:59:59+00:00", "2026-09-14T12:10:01+00:00",
                                    "2035-01-01T12:09:59+00:00", None])
def test_cache_outside_successful_attempt_does_not_clear_manual_failure(status_io, stamp):
    root, cache = status_io
    _old_manual_failure()
    _complete_attempt(root)
    cache["stamp"] = stamp
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_timeout"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"


@pytest.mark.parametrize("manual_start", ["2026-09-14T12:00:00+00:00", "2026-09-14T12:11:00+00:00",
                                         None, "not-a-timestamp"])
def test_equal_newer_or_unknown_manual_chronology_is_not_cleared(status_io, manual_start):
    root, cache = status_io
    _old_manual_failure()
    state = api._scan_status[api._strategy_scan_status_key("Momentum Breakout Long")]
    state["last_attempt_at"] = manual_start
    _complete_attempt(root)
    cache["stamp"] = "2026-09-14T12:09:59+00:00"
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_timeout"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"


def test_active_manual_worker_remains_authoritative_even_with_complete_attempt(status_io):
    root, cache = status_io
    _old_manual_failure()
    state = api._scan_status[api._strategy_scan_status_key("Momentum Breakout Long")]
    state["running"] = True
    _complete_attempt(root)
    cache["stamp"] = "2026-09-14T12:09:59+00:00"
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_running is True and result.scan_error == "scan_timeout"
    assert result.scan_run_id == state["last_run_id"]


@pytest.mark.parametrize("incomplete", ["partial", "coverage"])
def test_partial_or_incomplete_cache_does_not_prove_recovery(status_io, incomplete):
    root, cache = status_io
    _old_manual_failure()
    _complete_attempt(root)
    cache.update(stamp="2026-09-14T12:09:59+00:00")
    cache[incomplete] = True if incomplete == "partial" else "incomplete"
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_timeout"


@pytest.mark.parametrize("problem", ["foreign_identity", "old_version"])
def test_wrong_cache_identity_or_version_cannot_clear_old_error(status_io, monkeypatch, problem):
    root, _ = status_io
    _old_manual_failure()
    _complete_attempt(root)
    metadata = {
        "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
        "diagnostics": {"coverage": "complete", "final_results": 0, "strategy": "Momentum Breakout Long"},
    }
    if problem == "foreign_identity":
        metadata["diagnostics"]["strategy"] = "Gap Momentum Short"
    else:
        metadata["cache_version"] = "old-version"
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **kw: (
        [], "2026-09-14T12:09:59+00:00", metadata, False))
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_timeout"


def test_complete_attempt_without_matching_final_cache_is_not_recovery(status_io):
    root, _ = status_io
    _old_manual_failure()
    _complete_attempt(root)
    # Fixture retains its old Sep 8 cache. A Sep 14 complete attempt alone is insufficient.
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")
    assert result.scan_error == "scan_timeout"
    assert result.diagnostics["latest_attempt"]["status"] == "complete"
