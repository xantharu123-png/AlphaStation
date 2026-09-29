"""Offline fixed-clock Gap integration: fake workers, no provider or SMTP.

Run only through tmp/offline_mail_fix_tests_20260925.py, whose disposable state
and external-network fence protect the operator's real configuration.
"""
from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import api
from modules import gap_scan_schedule as gap


LONG = "strat_gap_momentum_long"
SHORT = "strat_gap_momentum_short"


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.fixture
def fixed(monkeypatch, tmp_path):
    state = SimpleNamespace(now=utc("2026-09-30T00:00:00Z").timestamp(), calls=[],
                            allow=True, registered={}, workers=[], timeline=[])
    statuses = deepcopy(api._scan_status)
    for value in statuses.values():
        value.update(running=False, last_run=None, next_run=None)
        for field in ("last_run_id", "_run_id", "_started_at", "last_error"):
            value.pop(field, None)
    monkeypatch.setattr(api, "_scan_status", statuses)
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_resume_restarts", {})
    monkeypatch.setattr(api, "_GAP_SCAN_SCHEDULE_STORE", gap.GapScheduleStore(tmp_path / "schedule.json"))
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {key: str(tmp_path / (key + ".json")) for key in statuses})
    monkeypatch.setattr(api, "_scan_watchdog_check", lambda *args: None)
    monkeypatch.setattr(api, "_scan_control_data_token", lambda *args: ("daily", "fixture"))
    monkeypatch.setattr(api.scan_control, "register", lambda name, run_id, **kwargs: state.registered.update({name: kwargs}))

    def sleep(seconds):
        if seconds == 30:
            if state.timeline:
                state.now = state.timeline.pop(0)
            else:
                api._scheduler_running = False

    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: state.now,
                        monotonic=lambda: state.now, sleep=sleep))
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: pytest.fail("No provider I/O"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **kw: pytest.fail("No mail I/O"))

    def launch(name, func, **kwargs):
        # Assert durable reservation exists BEFORE worker admission.
        if gap.is_gap_scan(name):
            job = api._GAP_SCAN_SCHEDULE_STORE.snapshot(state.now)["jobs"][name]
            assert job["last_attempt_phase"] == "reserved"
            assert job["last_attempt_slot"] <= state.now
        state.calls.append(name)
        return state.allow

    state.original_start = api._run_scan_safe
    monkeypatch.setattr(api, "_run_scan_safe", launch)
    return state


def job(name, state):
    return api._GAP_SCAN_SCHEDULE_STORE.snapshot(state.now)["jobs"][name]


def test_gap_is_not_in_hourly_stock_sweep():
    assert "Gap Momentum Long" not in api._AUTO_STOCK_ALERT_STRATEGIES
    assert "Gap Momentum Short" not in api._AUTO_STOCK_ALERT_STRATEGIES
    assert "Momentum Breakout Long" in api._AUTO_STOCK_ALERT_STRATEGIES
    assert "Cup and Handle Breakout" in api._AUTO_STOCK_ALERT_STRATEGIES


@pytest.mark.parametrize("name,strategy", [(LONG, "Gap Momentum Long"), (SHORT, "Gap Momentum Short")])
def test_fixed_jobs_have_exact_leaf_identity_cache_control_and_budget(name, strategy):
    assert api._strategy_scan_status_key(strategy) == name
    assert api.SCAN_CACHE_MAP[name] == api._strategy_cache_path(strategy)
    assert api._scan_status[name]["schedule_name"] == name
    assert api._scan_status[name]["interval_min"] == 0
    assert api._scan_control_supported(name)
    assert api._SCAN_TIMEOUTS[name] == 35


def test_exact_slot_reserves_both_once_and_repeated_ticks_do_not_rescan(fixed):
    api._run_due_gap_scans()
    assert fixed.calls == [LONG, SHORT]
    assert job(LONG, fixed)["next_due"] == utc("2026-09-30T10:00:00Z").timestamp()
    assert api._scan_status[LONG]["next_run"] == "2026-09-30T10:00:00+00:00"
    for _ in range(3):
        api._run_due_gap_scans()
    assert fixed.calls == [LONG, SHORT]
    fixed.now = utc("2026-09-30T10:00:00Z").timestamp()
    api._run_due_gap_scans()
    assert fixed.calls == [LONG, SHORT, LONG, SHORT]
    assert job(SHORT, fixed)["next_due"] == utc("2026-10-01T00:00:00Z").timestamp()


@pytest.mark.parametrize("clock", ["2026-09-30T00:00:01Z", "2026-09-30T07:00:00Z",
                                    "2026-09-30T10:00:01Z", "2026-09-30T20:00:00Z"])
def test_first_adoption_or_missing_cache_does_not_create_startup_catchup(fixed, clock):
    fixed.now = utc(clock).timestamp()
    api._run_due_gap_scans()
    assert fixed.calls == []
    assert job(LONG, fixed)["next_due"] > fixed.now


def test_actual_busy_owner_releases_claim_and_preserves_slot(fixed, monkeypatch):
    api._scan_status["biotech"]["running"] = True
    monkeypatch.setattr(api, "_run_scan_safe", fixed.original_start)
    api._run_due_gap_scans()
    assert job(LONG, fixed)["next_due"] == fixed.now
    assert job(SHORT, fixed)["next_due"] == fixed.now
    assert job(LONG, fixed)["last_attempt_phase"] == "released"
    assert not api._scan_status[LONG]["running"]
    assert api._scan_status[LONG]["last_run"] is None


def test_false_admission_does_not_consume_slot(fixed):
    fixed.allow = False
    api._run_due_gap_scans()
    assert job(LONG, fixed)["next_due"] == fixed.now
    fixed.allow = True
    api._run_due_gap_scans()
    assert fixed.calls == [LONG, SHORT, LONG, SHORT]
    assert job(LONG, fixed)["next_due"] > fixed.now


def test_restart_retains_consumed_slot_and_missing_cache_does_not_rescan(fixed):
    api._run_due_gap_scans()
    path = api._GAP_SCAN_SCHEDULE_STORE.path
    api._GAP_SCAN_SCHEDULE_STORE = gap.GapScheduleStore(path)
    fixed.now += 60
    api._run_due_gap_scans()
    assert fixed.calls == [LONG, SHORT]
    assert job(LONG, fixed)["next_due"] == utc("2026-09-30T10:00:00Z").timestamp()


def test_monday_two_am_uses_swiss_weekday_even_when_new_york_is_sunday(fixed):
    fixed.now = utc("2026-09-28T00:00:00Z").timestamp()
    assert not api.scan_schedule.automatic_scan_allowed(LONG, fixed.now)
    assert api._automatic_scan_allowed(LONG)
    assert not api._automatic_scan_allowed("bi_long", fixed.now)
    api._run_due_gap_scans()
    assert fixed.calls == [LONG, SHORT]


@pytest.mark.parametrize("clock", ["2026-09-26T00:00:00Z", "2026-09-27T10:00:00Z"])
def test_swiss_weekend_waits_for_monday_two_am(fixed, clock):
    fixed.now = utc(clock).timestamp()
    api._run_due_gap_scans()
    assert fixed.calls == []
    schedule = api._scan_schedule_for(LONG)
    assert schedule["timezone"] == "Europe/Zurich"
    assert schedule["automatic_paused"] and schedule["reason"] == "weekend"
    assert schedule["next_eligible_at"] == "2026-09-28T00:00:00+00:00"


@pytest.mark.parametrize("now,expected", [
    ("2026-03-27T11:00:01Z", "2026-03-30T00:00:00Z"),
    ("2026-10-23T10:00:01Z", "2026-10-26T01:00:00Z"),
    ("2026-09-30T00:05:00Z", "2026-09-30T10:00:00Z"),
    ("2026-09-30T10:01:00Z", "2026-10-01T00:00:00Z"),
])
def test_auto_resume_at_next_fixed_slot_not_hourly_or_us_monday(now, expected):
    assert api._scan_resume_at(LONG, {"next_run": None}, utc(now).timestamp()) == utc(expected).timestamp()


def test_auto_resume_claims_slot_and_prevents_fresh_duplicate_after_completion(fixed):
    api._run_due_gap_scans()
    api._scan_status[LONG]["running"] = True
    fixed.now = utc("2026-09-30T10:00:00Z").timestamp()
    assert api._scan_automatic_resume_allowed(LONG)
    assert not api._scan_automatic_resume_allowed(LONG)
    assert job(LONG, fixed)["next_due"] == utc("2026-10-01T00:00:00Z").timestamp()
    api._scan_status[LONG]["running"] = False
    api._run_due_gap_scans()
    assert fixed.calls.count(LONG) == 1
    assert fixed.calls.count(SHORT) == 2


def test_active_owner_and_epoch_restart_are_not_a_second_due_admission(fixed):
    api._GAP_SCAN_SCHEDULE_STORE.next_due(LONG, fixed.now)
    api._scan_status[LONG]["running"] = True
    api._scan_resume_restarts[SHORT] = {"dummy": True}
    api._run_due_gap_scans()
    assert fixed.calls == []
    assert job(LONG, fixed)["last_attempt_slot"] is None


def test_native_worker_registration_uses_fixed_resume_callback(fixed, monkeypatch):
    class InertThread:
        def __init__(self, **kwargs):
            self.alive = False
        def start(self):
            self.alive = True
        def is_alive(self):
            return self.alive
    monkeypatch.setattr(api, "threading", SimpleNamespace(Thread=InertThread))
    assert fixed.original_start(LONG, lambda: None)
    callback = fixed.registered[LONG]["auto_allowed"]
    assert callback()
    assert not callback()
    assert api._scan_status[LONG]["next_run"] == "2026-09-30T10:00:00+00:00"


def test_calendar_next_run_not_cache_mtime_or_one_minute_interval(fixed):
    api._GAP_SCAN_SCHEDULE_STORE.next_due(LONG, fixed.now)
    fixed.now += 3600
    status = {"last_run": "2026-09-29T20:00:00+00:00", "next_run": None,
              "interval_min": 0, "schedule_name": LONG}
    _, due = api._effective_scan_timing(status, {}, now_ts=fixed.now)
    assert due == "2026-09-30T00:00:00+00:00"  # Pending admission, not last+60s.
    assert api._scan_schedule_for(LONG)["scheduled_at"] == due


def test_cache_health_follows_actual_fixed_admission_not_minutes(fixed):
    api._run_due_gap_scans()
    path = Path(api.SCAN_CACHE_MAP[LONG])
    path.write_text('{"results":[]}', encoding="utf-8")
    os.utime(path, (fixed.now + 30, fixed.now + 30))
    fixed.now += 8 * 3600
    assert api._scan_cache_health(LONG, api._scan_status[LONG])["cache_health"] == "ok"
    fixed.now = utc("2026-09-30T10:00:00Z").timestamp()
    api._run_due_gap_scans()
    assert api._scan_cache_health(LONG, api._scan_status[LONG])["cache_health"] == "stale"


def test_corrupt_ledger_blocks_dispatch_and_public_timing_does_not_crash(fixed):
    path = Path(api._GAP_SCAN_SCHEDULE_STORE.path)
    path.write_text('{"bad":null}', encoding="utf-8")
    api._run_due_gap_scans()
    assert fixed.calls == []
    public = api._scan_schedule_for(LONG)
    assert public["reason"] == "gap_schedule_unavailable"
    assert public["automatic_paused"] and public["next_eligible_at"] is None
    assert api._effective_scan_timing(api._scan_status[LONG], {}, now_ts=fixed.now)[1] is None
    assert json.loads(path.read_text()) == {"bad": None}


def test_scheduler_prioritizes_fixed_gap_before_hourly_and_never_treats_it_as_startup_cache(fixed, monkeypatch):
    monkeypatch.setattr(api, "_scheduler_running", True)
    monkeypatch.setattr(api, "_drain_scan_resume_restarts", lambda: None)
    monkeypatch.setattr(api, "_scan_is_parked", lambda *args: False)
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda *args: False)
    # Fake interval calls never acquire an actual owner, so completion is immediate.
    api._scheduler_loop()
    assert fixed.calls[:2] == [LONG, SHORT]
    assert fixed.calls.count(LONG) == fixed.calls.count(SHORT) == 1
    source = inspect.getsource(api._scheduler_loop)
    assert '"strat_gap_momentum_long"' not in source
    assert '"strat_gap_momentum_short"' not in source


def test_scheduled_worker_uses_exact_long_short_producer(fixed, monkeypatch):
    produced = []
    monkeypatch.setattr(api, "_strategy_scan_wrapper", lambda strategy, **kwargs: produced.append((strategy, kwargs)))
    def launch(name, func, **kwargs):
        assert job(name, fixed)["last_attempt_phase"] == "reserved"
        func()
        return True
    monkeypatch.setattr(api, "_run_scan_safe", launch)
    api._run_due_gap_scans()
    assert produced == [("Gap Momentum Long", {"publish_generic_cache": False}),
                        ("Gap Momentum Short", {"publish_generic_cache": False})]


def queue_epoch_restart(state, name=LONG, automatic=True):
    api._scan_status[name]["last_run_id"] = "prior-gap-run"
    api._scan_resume_restarts[name] = {"automatic": automatic, "run_id": "prior-gap-run",
        "thread": SimpleNamespace(is_alive=lambda: False), "func": lambda: None}


def test_automatic_epoch_restart_reuses_current_reserved_slot_without_double_claim(fixed):
    api._run_due_gap_scans()
    queue_epoch_restart(fixed)
    fixed.now += 60
    api._drain_scan_resume_restarts()
    assert LONG not in api._scan_resume_restarts
    assert fixed.calls.count(LONG) == 2  # Second worker is the same admitted attempt.
    assert job(LONG, fixed)["last_attempt_slot"] == utc("2026-09-30T00:00:00Z").timestamp()
    assert job(LONG, fixed)["next_due"] == utc("2026-09-30T10:00:00Z").timestamp()


def test_friday_queued_epoch_restart_waits_for_monday_swiss_slot_and_then_claims(fixed):
    fixed.now = utc("2026-09-25T10:00:00Z").timestamp()
    api._run_due_gap_scans()
    queue_epoch_restart(fixed)
    fixed.now = utc("2026-09-27T23:59:59Z").timestamp()
    api._drain_scan_resume_restarts()
    assert LONG in api._scan_resume_restarts
    assert fixed.calls == [LONG, SHORT]
    fixed.now = utc("2026-09-28T00:00:00Z").timestamp()
    api._drain_scan_resume_restarts()
    assert LONG not in api._scan_resume_restarts
    assert fixed.calls == [LONG, SHORT, LONG]
    assert job(LONG, fixed)["last_attempt_slot"] == fixed.now
    assert job(LONG, fixed)["next_due"] == utc("2026-09-28T10:00:00Z").timestamp()


def test_new_resume_slot_is_released_when_owner_admission_fails(fixed):
    api._run_due_gap_scans()
    queue_epoch_restart(fixed)
    fixed.now = utc("2026-09-30T10:00:00Z").timestamp()
    fixed.allow = False
    api._drain_scan_resume_restarts()
    assert LONG in api._scan_resume_restarts
    assert job(LONG, fixed)["next_due"] == fixed.now
    assert job(LONG, fixed)["last_attempt_phase"] == "released"
    fixed.allow = True
    api._drain_scan_resume_restarts()
    assert LONG not in api._scan_resume_restarts
    assert job(LONG, fixed)["last_attempt_phase"] == "reserved"


def test_manual_epoch_restart_is_immediate_without_consuming_scheduled_slot(fixed, monkeypatch):
    fixed.now = utc("2026-09-27T10:00:00Z").timestamp()
    due = api._GAP_SCAN_SCHEDULE_STORE.next_due(LONG, fixed.now)
    queue_epoch_restart(fixed, automatic=False)
    monkeypatch.setattr(api, "_run_scan_safe", lambda *args, **kwargs: True)
    api._drain_scan_resume_restarts()
    assert LONG not in api._scan_resume_restarts
    assert job(LONG, fixed)["next_due"] == due
    assert job(LONG, fixed)["last_attempt_slot"] is None


def test_status_top_level_next_run_and_schedule_share_fixed_ledger(fixed):
    api._scan_status[LONG]["next_run"] = "1999-01-01T00:00:00"
    row = api.get_scan_status()["scans"][LONG]
    assert row["next_run"] == row["schedule"]["scheduled_at"] == "2026-09-30T00:00:00+00:00"
