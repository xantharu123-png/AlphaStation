"""Real watchdog/worker/dedupe lifecycle; fake clock and SMTP, no market calls.

Soft-warning episodes run for 65 minutes, beyond the calibrated BI 60m budget.
"""

import threading
import time
from types import SimpleNamespace

import pytest

import api
from modules import watchdog_log


@pytest.fixture
def watchdog(monkeypatch, tmp_path):
    clock = [time.time()]
    sent, workers, delivery_results = [], [], []
    real_thread = threading.Thread
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    monkeypatch.setattr(api, "_scan_status", {
        "bi_short": {"running": False, "last_run": None, "interval_min": 180},
    })
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setitem(api.SCAN_CACHE_MAP, "bi_short", None)
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(watchdog_log, "WATCHDOG_EVENTS_PATH", str(tmp_path / "events.jsonl"))

    def smtp(subject, body_html, **kwargs):
        sent.append({"subject": subject, "body": body_html})
        result = delivery_results.pop(0) if delivery_results else True
        if isinstance(result, Exception):
            raise result
        return result

    def thread_factory(*args, **kwargs):
        worker = real_thread(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(api, "_send_email_alert", smtp)
    monkeypatch.setattr(api.threading, "Thread", thread_factory)

    def run(minutes=0, *, check=False, fail=False):
        def scan():
            clock[0] += minutes * 60
            if check:
                api._scan_watchdog_check("bi_short", now=clock[0])
            if fail:
                raise RuntimeError("simulated scan failure")

        assert api._run_scan_safe("bi_short", scan)
        workers[-1].join(timeout=5)
        assert not workers[-1].is_alive()

    yield SimpleNamespace(
        clock=clock, sent=sent, results=delivery_results, run=run,
        state=api._scan_status["bi_short"], workers=workers,
    )
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_throttled_warning_cannot_turn_into_recovery_after_six_hours(watchdog):
    api._email_dedupe_mark("stuck_throttle_bi_short")
    watchdog.run(65, check=True)
    assert watchdog.sent == []
    watchdog.clock[0] += 7 * 3600
    watchdog.run()
    assert watchdog.sent == []
    assert "_episode_started_at" not in watchdog.state
    assert not watchdog.state.get("_pending_stuck_recoveries")


def test_no_orphan_recovery_without_a_delivered_warning(watchdog):
    watchdog.state["_episode_started_at"] = watchdog.clock[0] - 65 * 60
    watchdog.run()
    assert watchdog.sent == []
    assert "_episode_started_at" not in watchdog.state


def test_hard_only_warning_gets_its_recovery_despite_other_episode_throttle(watchdog):
    api._email_dedupe_mark("stuck_throttle_bi_short")
    watchdog.run(136, check=True)
    assert len(watchdog.sent) == 2
    assert "kontrollierten Neustart" in watchdog.sent[0]["subject"]
    assert "laeuft wieder" in watchdog.sent[1]["subject"]
    assert "_episode_started_at" not in watchdog.state


def test_already_delivered_recovery_never_rearms_after_dedupe_expiry(watchdog):
    started = watchdog.clock[0] - 65 * 60
    watchdog.state["_episode_started_at"] = started
    api._email_dedupe_mark(f"stuck_scan_bi_short_{int(started)}")
    api._email_dedupe_mark(f"stuck_recovery_bi_short_{int(started)}")
    watchdog.run()
    watchdog.clock[0] += 8 * 86400
    watchdog.run()
    assert watchdog.sent == []
    assert "_episode_started_at" not in watchdog.state


@pytest.mark.parametrize("failure", [False, OSError("SMTP unavailable")], ids=["false", "exception"])
@pytest.mark.parametrize("retry_hours", [10, 192], ids=["ten-hours", "eight-days"])
def test_delivery_retry_preserves_actual_recovery_time(watchdog, failure, retry_hours):
    watchdog.results[:] = [True, failure, True]
    watchdog.run(65, check=True)
    assert len(watchdog.sent) == 2
    assert "65 Min" in watchdog.sent[-1]["body"]
    watchdog.clock[0] += retry_hours * 3600
    watchdog.run(1)
    assert len(watchdog.sent) == 3
    assert "65 Min" in watchdog.sent[-1]["body"]
    assert "666 Min" not in watchdog.sent[-1]["body"]
    assert "_episode_started_at" not in watchdog.state
    assert not watchdog.state.get("_pending_stuck_recoveries")


def test_delivery_failure_does_not_merge_a_new_timeout_into_recovered_episode(watchdog):
    watchdog.results[:] = [True, False, True, True, True]
    watchdog.run(65, check=True)
    watchdog.clock[0] += 7 * 3600
    watchdog.run(65, check=True)
    warnings = [mail for mail in watchdog.sent if "haengt" in mail["subject"]]
    recoveries = [mail for mail in watchdog.sent if "laeuft wieder" in mail["subject"]]
    assert len(warnings) == 2
    assert len(recoveries) == 3  # Failed attempt, its retry, then new episode.
    assert all("65 Min" in mail["body"] for mail in recoveries)
    assert not watchdog.state.get("_pending_stuck_recoveries")


def test_failed_followup_run_keeps_same_incident_warning_identity(watchdog):
    watchdog.run(65, check=True, fail=True)
    watchdog.clock[0] += 7 * 3600
    watchdog.run(65, check=True, fail=True)
    assert len(watchdog.sent) == 1
    assert "_episode_started_at" in watchdog.state
    watchdog.run(1)
    assert len(watchdog.sent) == 2
    assert "laeuft wieder" in watchdog.sent[-1]["subject"]


@pytest.mark.parametrize("warning_delivered", [True, False])
def test_completion_while_warning_is_in_flight_waits_for_its_outcome(
    watchdog, monkeypatch, warning_delivered,
):
    scan_release = threading.Event()
    warning_entered = threading.Event()
    warning_release = threading.Event()

    def smtp(subject, body_html, **kwargs):
        watchdog.sent.append({"subject": subject, "body": body_html})
        if "haengt" in subject:
            warning_entered.set()
            assert warning_release.wait(5)
            return warning_delivered
        return True

    monkeypatch.setattr(api, "_send_email_alert", smtp)
    assert api._run_scan_safe("bi_short", lambda: scan_release.wait(5))
    worker = watchdog.workers[0]
    watchdog.clock[0] += 65 * 60
    watcher = threading.Thread(target=api._scan_watchdog_check, args=("bi_short",))
    watcher.start()
    try:
        assert warning_entered.wait(5)
        scan_release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(watchdog.sent) == 1
        assert watchdog.state.get("_pending_stuck_recoveries")
    finally:
        scan_release.set()
        warning_release.set()
        watcher.join(timeout=5)
        worker.join(timeout=5)
    watchdog.run(1)
    assert len(watchdog.sent) == (2 if warning_delivered else 1)
    if warning_delivered:
        assert "65 Min" in watchdog.sent[-1]["body"]
    assert not watchdog.state.get("_pending_stuck_recoveries")
