"""BI runtime warnings tolerate measured runs without relaxing the hard limit."""

import time

import pytest

import api
from modules import watchdog_log


@pytest.fixture(params=["bi_long", "bi_short"])
def bi_scan(request, monkeypatch, tmp_path):
    """Use production budgets; isolate scanner state, SMTP and persistent logs."""
    name = request.param
    started_at = time.time() - 3 * 3600
    state = {"running": True, "_started_at": started_at, "interval_min": 180}
    worker = object()
    monkeypatch.setattr(api, "_scan_status", {name: state})
    monkeypatch.setattr(api, "_scan_threads", {name: worker})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(watchdog_log, "WATCHDOG_EVENTS_PATH", str(tmp_path / "events.jsonl"))
    sent = []

    def record_mail(subject, body_html, **kwargs):
        sent.append({"subject": subject, "body": body_html, **kwargs})
        return True

    monkeypatch.setattr(api, "_send_email_alert", record_mail)
    return name, state, worker, sent


@pytest.mark.parametrize("elapsed", [2812.1, 3599, 3600])
def test_bi_measured_runtime_and_soft_boundary_do_not_warn(bi_scan, elapsed):
    """The measured 46m52s run and the 60m boundary are not stuck episodes."""
    name, state, worker, sent = bi_scan
    now = state["_started_at"] + elapsed

    assert api._scan_watchdog_check(name, now=now) is None
    runtime = api._scan_runtime_state(name, state, now_ts=now)
    assert runtime["runtime_health"] == "running"
    assert runtime["timeout_exceeded"] is False
    assert sent == []
    assert "_episode_started_at" not in state
    assert "last_error" not in state
    assert api._scan_threads[name] is worker
    assert watchdog_log.load_watchdog_events(days=7) == []


def test_bi_warns_once_after_one_hour_with_matching_public_budget(bi_scan):
    """Moving the soft limit must not disable warnings or leave UI at 45m."""
    name, state, worker, sent = bi_scan
    now = state["_started_at"] + 3601

    assert api._scan_watchdog_check(name, now=now) == "stuck"
    assert api._scan_watchdog_check(name, now=now + 30) is None
    runtime = api._scan_runtime_state(name, state, now_ts=now)
    assert runtime["runtime_health"] == "stuck"
    assert runtime["timeout_exceeded"] is True
    assert runtime["timeout_minutes"] == 60
    assert len(sent) == 1
    assert "Budget 60 Min" in sent[0]["body"]
    assert sent[0]["mail_class"] == "info"
    assert state["running"] is True
    assert api._scan_threads[name] is worker


def test_bi_hard_alarm_still_fires_after_135_minutes_despite_soft_throttle(bi_scan):
    """A 60m soft budget must not silently turn the 135m hard cap into 180m."""
    name, state, worker, sent = bi_scan
    started_at = state["_started_at"]

    assert api._scan_watchdog_check(name, now=started_at + 3601) == "stuck"
    assert api._scan_watchdog_check(name, now=started_at + 8100) is None
    assert len(sent) == 1
    assert api._scan_watchdog_check(name, now=started_at + 8101) == "stuck_hard"
    assert api._scan_watchdog_check(name, now=started_at + 8160) is None
    assert len(sent) == 2
    assert "kontrollierten Neustart" in sent[1]["subject"]
    assert "kein paralleler Ersatzlauf" in sent[1]["body"]
    assert state["running"] is True
    assert api._scan_threads[name] is worker
    assert [event["kind"] for event in watchdog_log.load_watchdog_events(days=7)] == [
        "warn", "hard_timeout",
    ]
