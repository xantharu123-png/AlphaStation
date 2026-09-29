"""Independent admission review; run through the isolated offline API launcher.

Provider, SMTP and scanner work are replaced by inert admission callbacks.
The real scheduler and durable reservation functions remain under test.
"""

from copy import deepcopy
from datetime import datetime
import os
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import api
from modules import gap_scan_schedule as gap


LONG = "strat_gap_momentum_long"
SHORT = "strat_gap_momentum_short"


def swiss(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/Zurich"))


@pytest.fixture
def admission(monkeypatch, tmp_path):
    clock = {"now": swiss("2026-09-30T01:59:00").timestamp(), "calls": []}
    store = gap.GapScheduleStore(tmp_path / "gap.json")
    store.next_due(LONG, clock["now"])
    monkeypatch.setattr(api, "_GAP_SCAN_SCHEDULE_STORE", store)
    monkeypatch.setattr(api, "_scan_status", deepcopy(api._scan_status))
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_resume_restarts", {})
    monkeypatch.setattr(api, "_scan_watchdog_check", lambda *_: None)
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: clock["now"]))
    for status in api._scan_status.values():
        status.update(running=False, last_run=None, next_run=None)
    clock["now"] = swiss("2026-09-30T02:00:00").timestamp()
    clock["store"] = store
    return clock


def test_real_gap_admission_persists_receipt_before_both_worker_starts(admission, monkeypatch):
    due = gap.scheduled_slot("2026-09-30", 2)
    strategies = []

    def accept(name, function):
        job = admission["store"].snapshot(admission["now"])["jobs"][name]
        assert job["last_attempt_slot"] == due
        assert job["last_attempt_phase"] == "reserved"
        assert job["next_due"] == gap.scheduled_slot("2026-09-30", 12)
        admission["calls"].append(name)
        function()
        return True

    monkeypatch.setattr(api, "_run_scan_safe", accept)
    def scan(strategy, **kwargs):
        assert kwargs.get("publish_generic_cache") is False
        strategies.append(strategy)

    monkeypatch.setattr(api, "_strategy_scan_wrapper", scan)
    api._run_due_gap_scans(admission["now"])
    assert admission["calls"] == [LONG, SHORT]
    assert strategies == ["Gap Momentum Long", "Gap Momentum Short"]
    api._run_due_gap_scans(admission["now"])
    assert admission["calls"] == [LONG, SHORT]
    assert api._scan_status[LONG]["last_run"] is None


def test_monday_two_am_gap_jobs_run_while_other_stock_jobs_remain_on_new_york_weekend(admission, monkeypatch, tmp_path):
    clock = swiss("2026-09-28T02:00:00").timestamp()
    store = gap.GapScheduleStore(tmp_path / "monday.json")
    store.next_due(LONG, swiss("2026-09-28T01:59:00"))
    admission["now"] = clock
    monkeypatch.setattr(api, "_GAP_SCAN_SCHEDULE_STORE", store)
    monkeypatch.setattr(api, "_run_scan_safe", lambda name, _: admission["calls"].append(name) or True)
    assert api._automatic_scan_allowed(LONG, clock)
    assert not api._automatic_scan_allowed("strategy_scan", clock)
    api._run_due_gap_scans(clock)
    assert admission["calls"] == [LONG, SHORT]


@pytest.mark.parametrize("launch_failure", [False, True])
def test_real_gap_admission_releases_contention_or_launch_failure(admission, monkeypatch, launch_failure):
    due = gap.scheduled_slot("2026-09-30", 2)

    def cannot_start(name, function):
        admission["calls"].append(name)
        if launch_failure:
            raise RuntimeError("inert thread start failed")
        return False

    monkeypatch.setattr(api, "_run_scan_safe", cannot_start)
    api._run_due_gap_scans(admission["now"])
    assert admission["calls"] == [LONG, SHORT]
    assert admission["store"].pending_slot(LONG, admission["now"]) == due
    assert admission["store"].pending_slot(SHORT, admission["now"]) == due
    assert admission["store"].snapshot(admission["now"])["jobs"][LONG]["last_attempt_phase"] == "released"


def test_corrupt_ledger_stops_admission_and_status_does_not_invent_hourly_due(admission, monkeypatch):
    path = admission["store"].path
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{}")
    monkeypatch.setattr(api, "_run_scan_safe", lambda name, func: admission["calls"].append(name))
    api._run_due_gap_scans(admission["now"])
    assert admission["calls"] == []
    assert api._scan_status[LONG]["next_run"] is None
    status = {"schedule_name": LONG, "interval_min": 60, "last_run": "2026-09-30T00:00:00+00:00"}
    last, next_run = api._effective_scan_timing(status, {}, now_ts=admission["now"])
    assert last is not None
    assert next_run is None
    schedule = api._scan_schedule_for(LONG)
    assert schedule["automatic_paused"]
    assert schedule["reason"] == "gap_schedule_unavailable"


def test_gap_timing_does_not_inherit_the_hourly_stock_round(admission):
    status = {"schedule_name": LONG, "interval_min": 60, "last_run": "2026-09-29T20:00:00+00:00"}
    _, next_run = api._effective_scan_timing(status, {}, now_ts=admission["now"])
    assert next_run == "2026-09-30T00:00:00+00:00"
    admission["store"].claim(LONG, gap.scheduled_slot("2026-09-30", 2), admission["now"])
    _, next_run = api._effective_scan_timing(status, {}, now_ts=admission["now"])
    assert next_run == "2026-09-30T10:00:00+00:00"


def test_scan_status_gap_next_run_uses_durable_slot_after_old_manual_status(admission, monkeypatch):
    statuses = {name: deepcopy(api._scan_status[name]) for name in (LONG, SHORT)}
    for status in statuses.values():
        status["next_run"] = "2026-09-30T00:15:00+00:00"  # Stale interval/manual clock.
    monkeypatch.setattr(api, "_scan_status", statuses)
    monkeypatch.setattr(api, "_scan_cache_health", lambda *_: {"cache_health": "not_tracked"})
    monkeypatch.setattr(api, "_scan_control_snapshot", lambda *_: {})
    monkeypatch.setattr(api.stock_scan_runtime, "progress", lambda _: None)
    monkeypatch.setattr(api, "_ce_progress_snapshot", lambda: None)
    monkeypatch.setattr(api, "_bi_progress_read", lambda _: None)
    monkeypatch.setattr(api, "_biotech_progress_read", lambda: None)
    result = api.get_scan_status()
    for name in (LONG, SHORT):
        assert result["scans"][name]["next_run"] == "2026-09-30T00:00:00+00:00"
        assert result["scans"][name]["schedule"]["scheduled_at"] == result["scans"][name]["next_run"]


def test_gap_short_wins_admission_before_hourly_heavy_round_after_long_finishes(admission, monkeypatch):
    owner = {"name": None}
    accepted = []

    def dispatch(name, function):
        if api._is_heavy_stock_worker(name):
            if owner["name"] is not None:
                return False
            accepted.append(name)
            if gap.is_gap_scan(name):
                owner["name"] = name
                api._scan_status[name]["running"] = True
        return True

    def sleep(seconds):
        if owner["name"] is not None:
            api._scan_status[owner["name"]]["running"] = False
            owner["name"] = None
        if seconds == 30:
            api._scheduler_running = False

    monkeypatch.setattr(api, "_run_scan_safe", dispatch)
    monkeypatch.setattr(api, "_scheduler_running", True)
    monkeypatch.setattr(api, "_drain_scan_resume_restarts", lambda: None)
    monkeypatch.setattr(api, "_scan_is_parked", lambda _: False)
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {})
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: admission["now"], sleep=sleep))
    api._scheduler_loop()
    assert accepted[:3] == [LONG, SHORT, "strategy_scan"]
    assert accepted.count(LONG) == 1
    assert accepted.count(SHORT) == 1


def test_released_contention_slot_is_not_an_executed_failed_cache_attempt(admission, monkeypatch, tmp_path):
    cache = tmp_path / "gap_cache.json"
    cache.write_text('{"results":[]}', encoding="utf-8")
    old = admission["now"] - 3600
    os.utime(cache, (old, old))
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {LONG: str(cache), SHORT: str(cache)})
    monkeypatch.setattr(api, "_run_scan_safe", lambda *_: False)
    api._run_due_gap_scans(admission["now"])
    assert admission["store"].snapshot(admission["now"])["jobs"][LONG]["last_attempt_phase"] == "released"
    health = api._scan_cache_health(LONG, api._scan_status[LONG])
    assert health["cache_health"] == "ok"
    assert health["cache_stale"] is False
    assert health["scan_error"] is None


def test_gap_short_is_not_starved_when_long_exits_between_priority_check_and_hourly_admission(admission, monkeypatch, tmp_path):
    # Begin before noon with no earlier unconsumed slot. At the last startup
    # light task, model a Long worker holding the 12:00 slot. It exits exactly
    # after Short has been refused once, before the hourly heavy check.
    clock = admission
    clock["now"] = swiss("2026-09-30T11:59:00").timestamp()
    store = gap.GapScheduleStore(tmp_path / "noon.json")
    store.next_due(LONG, clock["now"])
    monkeypatch.setattr(api, "_GAP_SCAN_SCHEDULE_STORE", store)
    scenario = {"interval": False, "long_exited": False}
    accepted = []

    def dispatch(name, function):
        if not scenario["interval"]:
            if name == "crypto_trade_signals":
                clock["now"] = swiss("2026-09-30T12:00:00").timestamp()
                store.claim(LONG, gap.scheduled_slot("2026-09-30", 12), clock["now"])
                api._scan_status[LONG]["running"] = True
                scenario["interval"] = True
            return False
        if name == SHORT and not scenario["long_exited"]:
            api._scan_status[LONG]["running"] = False
            scenario["long_exited"] = True
            return False
        if api._is_heavy_stock_worker(name):
            if api._scan_status[SHORT]["running"]:
                return False
            accepted.append(name)
            if name == SHORT:
                api._scan_status[SHORT]["running"] = True
        return False if name != SHORT else True

    def sleep(seconds):
        if seconds == 30:
            api._scheduler_running = False

    monkeypatch.setattr(api, "_run_scan_safe", dispatch)
    monkeypatch.setattr(api, "_scheduler_running", True)
    monkeypatch.setattr(api, "_drain_scan_resume_restarts", lambda: None)
    monkeypatch.setattr(api, "_scan_is_parked", lambda _: False)
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {})
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: clock["now"], sleep=sleep))
    api._scheduler_loop()
    assert scenario["long_exited"]
    assert accepted == [SHORT]
