"""Post-close discovery exercises the real scheduler, without market or mail I/O."""
import json
import os
from pathlib import Path

import pytest

import api
from test_api_scan_schedule import scheduler, utc


def _completed_leaf_caches(scheduler, monkeypatch, tmp_path, *, session, written_at):
    monkeypatch.setattr(api, "_strategy_cache_path", lambda name: str(tmp_path / (name + ".json")))
    aggregate = Path(api.SCAN_CACHE_MAP["strategy_scan"])
    aggregate.write_text(json.dumps({"results": [], "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
        "diagnostics": {"coverage": "complete"}}), encoding="utf8")
    os.utime(aggregate, (written_at.timestamp(), written_at.timestamp()))
    for strategy in api._AUTO_STOCK_ALERT_STRATEGIES:
        path = Path(api._strategy_cache_path(strategy))
        path.write_text(json.dumps({"results": [], "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
            "diagnostics": {"strategy": strategy, "coverage": "complete", "data_mode": api.stock_swing.MODE,
                "analysis_as_of": session}}), encoding="utf8")
        os.utime(path, (written_at.timestamp(), written_at.timestamp()))


def test_new_daily_data_dispatches_recent_preclose_sweep_without_waiting_an_hour(scheduler, monkeypatch, tmp_path):
    scheduler.update(now=utc("2026-10-09T20:10:00Z"), timeline=[
        utc("2026-10-09T20:14:59Z"), utc("2026-10-09T20:15:00Z"), utc("2026-10-09T20:15:30Z")])
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    _completed_leaf_caches(scheduler, monkeypatch, tmp_path, session="2026-10-08T20:00:00+00:00",
                          written_at=utc("2026-10-09T20:09:30Z"))
    api._scheduler_loop()
    assert [(name, stamp) for name, stamp, _ in scheduler["calls"]] == [
        ("strategy_scan", utc("2026-10-09T20:15:00Z"))]


def test_restart_after_close_cannot_reuse_recently_written_prior_session_empty_cache(scheduler, monkeypatch, tmp_path):
    scheduler["now"] = utc("2026-10-09T20:16:00Z")
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    _completed_leaf_caches(scheduler, monkeypatch, tmp_path, session="2026-10-08T20:00:00+00:00",
                          written_at=utc("2026-10-09T20:15:30Z"))
    api._scheduler_loop()
    assert [(name, stamp) for name, stamp, _ in scheduler["calls"]] == [
        ("strategy_scan", utc("2026-10-09T20:16:00Z"))]


def test_current_session_empty_cache_still_defers_duplicate_startup(scheduler, monkeypatch, tmp_path):
    scheduler["now"] = utc("2026-10-09T20:16:00Z")
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    _completed_leaf_caches(scheduler, monkeypatch, tmp_path, session="2026-10-09T20:00:00+00:00",
                          written_at=utc("2026-10-09T20:15:30Z"))
    api._scheduler_loop()
    assert scheduler["calls"] == []


@pytest.mark.parametrize("field,value", [
    ("strategy", "wrong-strategy"), ("coverage", "incomplete"),
    ("coverage", "unknown"), ("data_mode", "realtime"),
    ("analysis_as_of", "2026-10-09T20:00:00"), ("analysis_as_of", None),
    ("analysis_as_of", "2026-10-09T19:59:00Z"),
])
def test_current_empty_leaf_requires_complete_same_session_producer_metadata(
        scheduler, monkeypatch, tmp_path, field, value):
    scheduler["now"] = utc("2026-10-09T20:16:00Z")
    _completed_leaf_caches(scheduler, monkeypatch, tmp_path, session="2026-10-09T20:00:00+00:00",
                          written_at=utc("2026-10-09T20:15:30Z"))
    path = Path(api._strategy_cache_path(api._AUTO_STOCK_ALERT_STRATEGIES[0]))
    payload = json.loads(path.read_text(encoding="utf8"))
    payload["diagnostics"][field] = value
    path.write_text(json.dumps(payload), encoding="utf8")
    stamp = utc("2026-10-09T20:15:30Z").timestamp()
    os.utime(path, (stamp, stamp))
    assert api._startup_scan_cache_time("strategy_scan", scheduler["now"].timestamp()) is None


def test_postclose_sweep_has_priority_but_only_until_its_first_new_data_start(scheduler):
    scheduler["now"] = utc("2026-10-09T20:16:00Z")
    tasks = [(name, None) for name in ("bi_long", "market_context", "strategy_scan", "biotech")]
    previous = {"bi_long": utc("2026-10-09T12:00:00Z").timestamp(),
                "strategy_scan": utc("2026-10-09T20:10:00Z").timestamp(),
                "biotech": utc("2026-10-09T15:00:00Z").timestamp()}
    order = [name for name, _ in api._scheduler_task_order(tasks, previous)]
    assert order[0] == "strategy_scan"
    assert order[1] == "market_context"
    previous["strategy_scan"] = utc("2026-10-09T20:15:00Z").timestamp()
    order = [name for name, _ in api._scheduler_task_order(tasks, previous)]
    assert order[0] == "bi_long"
    assert order[1] == "market_context"


def test_refused_postclose_admission_remains_due_without_claiming_a_success(scheduler, monkeypatch, tmp_path):
    scheduler.update(now=utc("2026-10-09T20:10:00Z"), timeline=[
        utc("2026-10-09T20:15:00Z"), utc("2026-10-09T20:15:30Z"), utc("2026-10-09T20:16:00Z")])
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    _completed_leaf_caches(scheduler, monkeypatch, tmp_path, session="2026-10-08T20:00:00+00:00",
                          written_at=utc("2026-10-09T20:09:30Z"))
    attempts = []
    original = scheduler["dispatch"]
    def admission(name, function, **kwargs):
        attempts.append(scheduler["now"])
        return False if len(attempts) == 1 else original(name, function, **kwargs)
    monkeypatch.setattr(api, "_run_scan_safe", admission)
    api._scheduler_loop()
    assert attempts == [utc("2026-10-09T20:15:00Z"), utc("2026-10-09T20:15:30Z")]
    assert [(name, stamp) for name, stamp, _ in scheduler["calls"]] == [
        ("strategy_scan", utc("2026-10-09T20:15:30Z"))]


def test_started_close_attempt_uses_hourly_retry_not_thirty_second_launch_loop(scheduler, monkeypatch, tmp_path):
    scheduler.update(now=utc("2026-10-09T20:10:00Z"), timeline=[
        utc("2026-10-09T20:15:00Z"), utc("2026-10-09T20:15:30Z"),
        utc("2026-10-09T20:16:00Z"), utc("2026-10-09T21:14:59Z"),
        utc("2026-10-09T21:15:00Z")])
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    _completed_leaf_caches(scheduler, monkeypatch, tmp_path, session="2026-10-08T20:00:00+00:00",
                          written_at=utc("2026-10-09T20:09:30Z"))
    # Admission returns True, but leaves no running worker or successful cache.
    # A worker failure must not monopolize the engine by relaunching every tick.
    api._scheduler_loop()
    assert [(name, stamp) for name, stamp, _ in scheduler["calls"]] == [
        ("strategy_scan", utc("2026-10-09T20:15:00Z")),
        ("strategy_scan", utc("2026-10-09T21:15:00Z"))]
