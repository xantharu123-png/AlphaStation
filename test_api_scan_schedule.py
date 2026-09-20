"""Independent integration tests of the real API scheduler, entirely offline.

Dispatch functions never run scanners. The heavy-ownership test uses the actual
worker admission function with inert threads, so no provider/mail work occurs.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import api
from modules import scan_schedule as calendar


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.fixture
def scheduler(monkeypatch, tmp_path):
    state = {"now": utc("2026-09-20T16:00:00Z"), "calls": [], "watchdogs": [],
             "sleeps": [], "timeline": [], "phase": "startup"}
    original_start = api._run_scan_safe
    original_drain = api._drain_scan_resume_restarts
    statuses = deepcopy(api._scan_status)
    for status in statuses.values():
        status.update(running=False, last_run=None, next_run=None)
        for field in ("_run_id", "_started_at", "last_run_id", "last_error"):
            status.pop(field, None)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz) if tz else state["now"].replace(tzinfo=None)

        @classmethod
        def fromtimestamp(cls, stamp, tz=None):
            parsed = datetime.fromtimestamp(stamp, timezone.utc)
            return parsed.astimezone(tz) if tz else parsed.replace(tzinfo=None)

    def sleep(seconds):
        state["sleeps"].append(seconds)
        if seconds == 30:
            if state["timeline"]:
                state["now"] = state["timeline"].pop(0)
            else:
                api._scheduler_running = False

    def dispatch(name, func, **kwargs):
        state["calls"].append((name, state["now"], state["phase"]))
        return True

    def watchdog(name, now):
        state["phase"] = "interval"
        state["watchdogs"].append((name, now))

    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(calendar, "datetime", Clock)
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: state["now"].timestamp(),
                                                   monotonic=lambda: state["now"].timestamp(), sleep=sleep))
    monkeypatch.setattr(api, "_scan_status", statuses)
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_resume_restarts", {})
    monkeypatch.setattr(api, "_scheduler_running", True)
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: False)
    monkeypatch.setattr(api, "_run_scan_safe", dispatch)
    monkeypatch.setattr(api, "_scan_watchdog_check", watchdog)
    monkeypatch.setattr(api, "_drain_scan_resume_restarts", lambda: None)
    monkeypatch.setattr(api, "_scan_is_parked", lambda name: False)
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {name: str(tmp_path / (name + ".json")) for name in statuses})
    state["original_start"] = original_start
    state["original_drain"] = original_drain
    state["dispatch"] = dispatch
    return state


def test_weekend_startup_and_interval_skip_only_explicit_stock_jobs(scheduler):
    api._scheduler_loop()
    names = {name for name, _, _ in scheduler["calls"]}
    assert not any(calendar.is_stock_scan(name) for name in names)
    assert names == {"crypto_explosion", "early_movers", "crash_monitor", "market_context",
                     "btc_divergenz", "penny_positions", "new_listing", "crypto_trade_signals"}
    for name in api._scan_status:
        if calendar.is_stock_scan(name):
            assert api._scan_status[name]["last_run"] is None
            assert api._scan_status[name]["next_run"] == "2026-09-21T04:00:00+00:00"


def test_weekend_skips_never_refresh_or_replace_previous_successful_cache(scheduler):
    path = Path(api.SCAN_CACHE_MAP["bi_long"])
    original = '{"timestamp":"2026-09-18T15:00:00Z","data":[{"ticker":"KEPT"}]}'
    path.write_text(original, encoding="utf-8")
    stamp = utc("2026-09-18T15:00:00Z").timestamp()
    os.utime(path, (stamp, stamp))
    api._scan_status["bi_long"]["last_run"] = "2026-09-18T15:00:00"
    api._scheduler_loop()
    assert api._scan_status["bi_long"]["last_run"] == "2026-09-18T15:00:00"
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime == stamp
    assert not Path(str(path) + ".partial").exists()


def test_friday_saturday_monday_interval_transitions_use_new_york_not_utc_day(scheduler):
    friday = utc("2026-09-19T03:59:00Z")  # Already Saturday UTC, still Friday NY.
    saturday = utc("2026-09-19T16:00:00Z")
    sunday = utc("2026-09-20T16:00:00Z")
    monday = utc("2026-09-21T04:00:00Z")
    scheduler.update(now=friday, timeline=[saturday, sunday, monday])
    api._scheduler_loop()
    for name in ("bi_long", "bi_short", "biotech", "strategy_scan", "money_flow", "orb", "cup_handle_watch"):
        stamps = [stamp for called, stamp, _ in scheduler["calls"] if called == name]
        assert stamps == [friday, monday]
    for name in ("early_movers", "crypto_explosion", "penny_positions", "market_context"):
        stamps = [stamp for called, stamp, _ in scheduler["calls"] if called == name]
        assert stamps == [friday, saturday, sunday, monday]


def test_weekday_fresh_cache_uses_original_mtime_not_a_faked_scan_success(scheduler):
    scheduler["now"] = utc("2026-09-21T16:00:00Z")
    path = Path(api.SCAN_CACHE_MAP["bi_long"])
    path.write_text("{}", encoding="utf-8")
    stamp = scheduler["now"].timestamp() - 30
    os.utime(path, (stamp, stamp))
    api._scheduler_loop()
    assert not any(name == "bi_long" for name, _, _ in scheduler["calls"])
    assert api._scan_status["bi_long"]["last_run"] == "2026-09-21T15:59:30"
    assert path.stat().st_mtime == stamp


@pytest.mark.parametrize("name", ["strategy_scan", "bi_long", "bi_short"])
def test_epoch_restart_success_waits_full_interval_before_scheduler_rerun(scheduler, monkeypatch, name):
    """A real fresh worker can finish before its drain returns on Monday."""
    monday = utc("2026-09-21T16:00:00Z")
    scheduler["timeline"] = [monday, monday + timedelta(seconds=299), monday + timedelta(seconds=300)]
    workers, completions = [], []

    class TrackedThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            workers.append(self)

    def fresh_work():
        api._scan_control_point(finishing=True)
        completions.append(scheduler["now"])

    def launch(scan_name, func, **kwargs):
        if kwargs.get("expected_previous_run_id") is not None:
            return scheduler["original_start"](scan_name, func, **kwargs)
        return scheduler["dispatch"](scan_name, func, **kwargs)

    def drain_and_finish():
        scheduler["original_drain"]()
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive(), "Restart worker did not finish before the scheduler tick"

    monkeypatch.setattr(api, "threading", SimpleNamespace(Thread=TrackedThread, current_thread=threading.current_thread))
    monkeypatch.setattr(api.scan_control, "_CONTROLS", {})
    monkeypatch.setattr(api.scan_control, "_LOCAL", threading.local())
    monkeypatch.setattr(api, "_scan_control_data_token", lambda key: ("daily", "2026-09-21"))
    monkeypatch.setattr(api, "_effective_scan_interval_min", lambda key: 5)
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda key: key != name)
    monkeypatch.setattr(api, "_scan_cache_revision", lambda path: None)
    monkeypatch.setattr(api, "_require_fresh_scan_cache", lambda *args: None)
    monkeypatch.setattr(api, "_run_scan_safe", launch)
    monkeypatch.setattr(api, "_drain_scan_resume_restarts", drain_and_finish)
    api._scan_status[name]["last_run_id"] = "parked-prior-epoch"
    api._scan_resume_restarts[name] = {
        "run_id": "parked-prior-epoch", "automatic": True,
        "thread": SimpleNamespace(is_alive=lambda: False), "func": fresh_work,
    }

    api._scheduler_loop()

    assert completions == [monday] and len(workers) == 1
    assert api._scan_status[name]["_resume_completed_at"] == monday.timestamp()
    assert name not in api._scan_resume_restarts
    assert [(key, stamp) for key, stamp, _ in scheduler["calls"]] == [
        (name, monday + timedelta(seconds=300))]


@pytest.mark.parametrize("route", ["bi_long", "bi_short", "generic"])
def test_manual_api_routes_remain_available_on_weekends(scheduler, monkeypatch, route):
    monkeypatch.setattr(api, "POLYGON_KEY", "offline-key")
    assert not calendar.automatic_scan_allowed("bi_long")
    if route == "generic":
        strategy = "Momentum Breakout Long"
        monkeypatch.setattr(api, "resolve_strategy_name", lambda value, market: value)
        monkeypatch.setattr(api, "get_strategies_for_market", lambda market: {strategy: {}})
        result = api.run_scan(api.ScanRequest(strategy=strategy), api.BackgroundTasks())
        expected = "strat_momentum_breakout_long"
    else:
        result = api.trigger_bi_scan(api.BIScanRequest(direction=route.split("_")[1]))
        expected = route
    assert result["accepted"] and result["status"] == "started"
    assert [name for name, _, _ in scheduler["calls"]] == [expected]


def test_parked_heavy_worker_releases_startup_wait_but_not_exclusive_ownership(scheduler, monkeypatch):
    scheduler["now"] = utc("2026-09-21T16:00:00Z")
    started = []
    attempts = []
    constructing = {"name": None}

    class InertThread:
        def __init__(self, **kwargs):
            self.name = constructing["name"]
            self.alive = False

        def start(self):
            self.alive = True
            started.append(self.name)

        def is_alive(self):
            return self.alive

    def launch(name, func, **kwargs):
        if api._is_heavy_stock_worker(name):
            attempts.append(name)
            constructing["name"] = name
            return scheduler["original_start"](name, func, **kwargs)
        return scheduler["dispatch"](name, func, **kwargs)

    monkeypatch.setattr(api, "threading", SimpleNamespace(Thread=InertThread))
    monkeypatch.setattr(api, "_scan_control_data_token", lambda name: ("daily", "2026-09-21"))
    monkeypatch.setattr(api.scan_control, "register", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_scan_is_parked", lambda name: name == "strategy_scan")
    monkeypatch.setattr(api, "_run_scan_safe", launch)
    api._scheduler_loop()
    assert started == ["strategy_scan"]
    assert {"bi_long", "bi_short", "biotech"} <= set(attempts)
    assert scheduler["sleeps"].count(10) == 1  # No one-hour startup stall.
    assert {name for name, _, _ in scheduler["calls"]} >= {"new_listing", "crypto_trade_signals"}
    assert api._scan_status["strategy_scan"]["running"]
    assert api._scan_status["strategy_scan"]["last_run"] is None
    assert api._scan_threads["strategy_scan"].is_alive()
    assert not any(api._scan_status[name]["running"] for name in ("bi_long", "bi_short", "biotech"))
