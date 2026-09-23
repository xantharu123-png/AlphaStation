"""Offline API integration of cooperative parking; no scans/providers or SMTP.

Actual bounded workers exercise ownership, admission, control-flow unwinding,
and the administrator route. A thread tail barrier proves that a queued fresh
run waits for actual thread death, not just the running=False status update.
"""
from datetime import datetime, timedelta
import json
import threading
import time
from types import SimpleNamespace

import pytest

import api
from modules import scan_control as control


CUP = "Cup and Handle Breakout"
KEY = "strat_cup_and_handle_breakout"
OLD_SUCCESS = "2026-09-18T10:00:00"
ADMIN = "Bearer offline-admin"


@pytest.fixture
def isolated_api(monkeypatch, tmp_path):
    """Every worker has explicit release/cleanup and all delivery is recorded."""
    workers, gates, notices, cache_checks = [], [], [], []
    context = SimpleNamespace(token=("daily", "2026-09-18", "same-config"),
                              allowed=True, tail_hook=None, workers=workers,
                              notices=notices, cache_checks=cache_checks)

    class TrackedThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.test_index = len(workers)
            workers.append(self)

        def run(self):
            try:
                super().run()
            finally:
                if context.tail_hook is not None:
                    context.tail_hook(self)

    def event():
        result = threading.Event()
        gates.append(result)
        return result

    context.event = event
    monkeypatch.setattr(api, "threading", SimpleNamespace(
        Thread=TrackedThread, current_thread=threading.current_thread))
    monkeypatch.setattr(api, "_scan_lock", threading.Lock())
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_resume_restarts", {})
    monkeypatch.setattr(api, "_scan_status", {})
    monkeypatch.setattr(control, "_CONTROLS", {})
    monkeypatch.setattr(control, "_LOCAL", threading.local())
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {})
    monkeypatch.setattr(api, "_scan_cache_revision", lambda path: None)
    monkeypatch.setattr(api, "_require_fresh_scan_cache",
                        lambda name, revision: cache_checks.append(name))
    monkeypatch.setattr(api, "_scan_control_data_token", lambda name: context.token)
    monkeypatch.setattr(api.scan_schedule, "automatic_scan_allowed", lambda name: context.allowed)
    monkeypatch.setattr(api, "_effective_scan_interval_min", lambda name: 5)
    monkeypatch.setattr(api, "_print_sanitized_traceback", lambda: None)
    monkeypatch.setattr(api, "_send_stuck_scan_mail",
                        lambda *args, **kwargs: notices.append(("warning", args, kwargs)) or True)
    monkeypatch.setattr(api, "_send_stuck_recovery_mail",
                        lambda *args, **kwargs: notices.append(("recovery", args, kwargs)) or "unannounced")
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: pytest.fail("No provider requests"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **kw: pytest.fail("No signal mail"))
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.test"})
    monkeypatch.setattr(api, "verify_token", lambda token: {
        "offline-admin": {"email": "admin@example.test"},
        "offline-member": {"email": "member@example.test"},
    }.get(token))
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(tmp_path / "absent.json"))
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *a: rows)
    monkeypatch.setattr(api, "_apply_signal_only_policy", lambda scanner, rows: rows)
    monkeypatch.setattr(api, "_scan_quality_payload", lambda *a: {
        "warnings": [], "data_source": "offline", "exclusion_policy": []})
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **kw: (
        [], None, {"strategy": CUP, "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
                   "checked": 1, "total": 2, "diagnostics": {"coverage": "incomplete"}}, True))
    yield context
    # Release bounded test waits and wake parked workers without starting new
    # scans. Cleanup completes before monkeypatch restores shared registries.
    context.tail_hook = None
    for gate in gates:
        gate.set()
    for key, entry in list(control._CONTROLS.items()):
        control.end(key, entry["run_id"])
    for worker in workers:
        worker.join(3)
    assert not any(worker.is_alive() for worker in workers), "Test leaked a parked worker"


def _add_state(key):
    api._scan_status[key] = {"running": False, "last_run": OLD_SUCCESS,
                             "next_run": None, "interval_min": 5}


def _request(key, run_id, action, *, auto_resume=False, authorization=ADMIN):
    return api.control_scan(api.ScanControlRequest(scanner=key, run_id=run_id,
                             action=action, auto_resume=auto_resume), authorization)


def _wait_state(key, state):
    with control._CONDITION:
        assert control._CONDITION.wait_for(
            lambda: control.snapshot(key).get("state") == state, timeout=3), control.snapshot(key)


def _park(context, key=KEY):
    entered, boundary, continued, finish = [context.event() for _ in range(4)]
    steps = []

    def work():
        steps.append("before")
        entered.set()
        assert boundary.wait(3)
        api._scan_control_point()
        steps.append("after")
        continued.set()
        assert finish.wait(3)
        api._scan_control_point(finishing=True)

    _add_state(key)
    assert api._run_scan_safe(key, work)
    assert entered.wait(3)
    worker = api._scan_threads[key]
    run_id = api._scan_status[key]["last_run_id"]
    reply = _request(key, run_id, "pause")
    assert reply["status"] == "pause_requested"
    assert control.snapshot(key)["state"] == "pause_requested"
    assert steps == ["before"] and not continued.is_set()
    boundary.set()
    _wait_state(key, "paused")
    return SimpleNamespace(key=key, run_id=run_id, worker=worker, steps=steps,
                           continued=continued, finish=finish)


def _resume_finish(parked):
    assert _request(parked.key, parked.run_id, "resume")["status"] == "resume_requested"
    assert parked.continued.wait(3)
    parked.finish.set()
    parked.worker.join(3)
    assert not parked.worker.is_alive()


def test_admin_pause_resumes_same_worker_stack_and_run_id_once(isolated_api):
    context = isolated_api
    parked = _park(context)
    snapshot = api._scan_control_snapshot(KEY)
    assert snapshot["state"] == "paused" and snapshot["worker_alive"]
    assert snapshot["owner_scan_key"] == KEY and snapshot["run_id"] == parked.run_id
    assert snapshot["scope"] == "scanner"
    assert datetime.fromisoformat(snapshot["paused_at"]).tzinfo is not None
    assert "same-config" not in json.dumps(snapshot)
    assert parked.worker.is_alive() and parked.steps == ["before"]
    assert api._scan_status[KEY]["last_run"] == OLD_SUCCESS
    assert context.cache_checks == []
    _resume_finish(parked)
    assert parked.steps == ["before", "after"]
    assert len(context.workers) == 1
    assert context.cache_checks == [KEY] and context.notices == []
    assert api._scan_status[KEY]["last_run_id"] == parked.run_id
    assert api._scan_status[KEY]["last_run"] != OLD_SUCCESS
    assert api._scan_control_snapshot(KEY)["state"] == "finished"


def test_completed_real_pause_schedules_next_interval_without_immediate_rerun(isolated_api):
    parked = _park(isolated_api)
    # Windows monotonic may report exactly zero for an immediate test resume.
    # Represent one elapsed second deterministically; the real worker remains
    # parked and must still pass the unchanged resume/finish API path.
    with control._CONDITION:
        control._CONTROLS[KEY]["paused_seconds"] = 1.0
    _resume_finish(parked)
    state = api._scan_status[KEY]
    assert state["_resume_completed_at"] > 0
    assert datetime.fromisoformat(state["next_run"]).timestamp() > state["_resume_completed_at"]
    assert state["last_run_id"] == parked.run_id


@pytest.mark.parametrize("other", [KEY, "strategy_scan", "bi_long", "bi_short",
                                    "biotech", "strat_gap_momentum_long"])
def test_parked_worker_retains_all_heavy_stock_admission(isolated_api, other):
    parked = _park(isolated_api)
    if other != KEY:
        _add_state(other)
    calls = []
    assert api._run_scan_safe(other, lambda: calls.append(other)) is False
    assert calls == [] and len(isolated_api.workers) == 1
    _resume_finish(parked)


@pytest.mark.parametrize("other", ["crypto_explosion", "crypto_strat_long", "penny_positions"])
def test_parked_stock_does_not_block_crypto_or_light_position_jobs(isolated_api, other):
    parked = _park(isolated_api)
    _add_state(other)
    completed = isolated_api.event()
    assert api._run_scan_safe(other, completed.set)
    assert completed.wait(3)
    isolated_api.workers[-1].join(3)
    assert (control.snapshot(other).get("state") == "finished") if api._scan_control_supported(other) else not control.snapshot(other)
    assert parked.worker.is_alive() and parked.steps == ["before"]
    _resume_finish(parked)


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("key", [KEY, "strategy_scan", "bi_long", "bi_short"])
def test_changed_epoch_queues_fresh_worker_only_after_actual_thread_death(isolated_api, monkeypatch, automatic, key):
    context = isolated_api
    first_entered, boundary, tail_entered, tail_release, fresh_done = [context.event() for _ in range(5)]
    calls, completed = [], []

    def tail(worker):
        if worker.test_index == 0:
            tail_entered.set()
            assert tail_release.wait(3)

    context.tail_hook = tail

    def work():
        calls.append(threading.current_thread())
        if len(calls) == 1:
            first_entered.set()
            assert boundary.wait(3)
            api._scan_control_point()
            pytest.fail("Old data must unwind, not continue its unfinished work")
        completed.append("fresh")
        api._scan_control_point(finishing=True)
        fresh_done.set()

    _add_state(key)
    if key == "strategy_scan":
        _add_state(KEY)
        api._scan_status[KEY]["last_run_id"] = "older-manual-run"
    assert api._run_scan_safe(key, work)
    assert first_entered.wait(3)
    old_worker = context.workers[0]
    run_id = api._scan_status[key]["last_run_id"]
    context.allowed = False
    monkeypatch.setattr(api, "_scan_resume_at", lambda *a: time.time() - 1)
    _request(key, run_id, "pause", auto_resume=automatic)
    boundary.set()
    _wait_state(key, "paused")
    context.token = ("daily", "2026-09-21", "same-config")
    if automatic:
        context.allowed = True
        with control._CONDITION:
            control._CONDITION.notify_all()
    else:
        _request(key, run_id, "resume")
    assert tail_entered.wait(3)
    assert old_worker.is_alive()  # API cleanup is complete but the thread isn't.
    assert api._scan_status[key]["running"] is False
    assert api._scan_status[key]["last_run"] == OLD_SUCCESS
    assert "last_error" not in api._scan_status[key]
    assert api._scan_control_snapshot(key)["state"] == "restart_required"
    result = (api.get_bi_results(key.split("_")[1]) if key in {"bi_long", "bi_short"}
              else api.get_scan_results(CUP, None, "stocks"))
    assert result.scan_control["state"] == "restart_required"
    assert result.scan_control["owner_scan_key"] == key
    assert result.scan_control["run_id"] == run_id == result.scan_run_id
    pending = api._scan_resume_restarts[key]
    assert pending["thread"] is old_worker and pending["automatic"] is automatic
    assert context.cache_checks == [] and completed == []
    api._drain_scan_resume_restarts()
    assert len(context.workers) == 1 and completed == []
    tail_release.set()
    old_worker.join(3)
    assert not old_worker.is_alive()
    api._drain_scan_resume_restarts()
    assert fresh_done.wait(3)
    context.workers[-1].join(3)
    assert len(context.workers) == 2 and calls[0] is not calls[1]
    assert completed == ["fresh"] and context.cache_checks == [key]
    assert api._scan_status[key]["last_run_id"] != run_id
    assert key not in api._scan_resume_restarts and context.notices == []
    state = api._scan_status[key]
    assert state["_resume_completed_at"] > 0
    assert datetime.fromisoformat(state["next_run"]).timestamp() > state["_resume_completed_at"]


def test_drain_expected_predecessor_is_rechecked_atomically_at_admission(isolated_api, monkeypatch):
    _add_state(KEY)
    api._scan_status[KEY]["last_run_id"] = "old-run"
    calls = []
    api._scan_resume_restarts[KEY] = {"run_id": "old-run", "automatic": False,
        "thread": SimpleNamespace(is_alive=lambda: False), "func": lambda: calls.append("stale")}
    original = api._run_scan_safe
    observed = []

    def racing_start(name, func, **kwargs):
        observed.append(kwargs.get("expected_previous_run_id"))
        # A different request completed in the interval between drain's first
        # inspection and the central admission lock. Never replace that run.
        api._scan_status[KEY]["last_run_id"] = "new-manual-run"
        return original(name, func, **kwargs)

    monkeypatch.setattr(api, "_run_scan_safe", racing_start)
    api._drain_scan_resume_restarts()
    assert observed == ["old-run"]
    assert calls == [] and isolated_api.workers == []
    assert api._scan_status[KEY]["last_run_id"] == "new-manual-run"
    api._drain_scan_resume_restarts()
    assert KEY not in api._scan_resume_restarts


@pytest.mark.parametrize("authorization", [None, "Basic invalid", "Bearer expired", "Bearer offline-member"])
def test_control_route_requires_real_admin_before_mutation(isolated_api, authorization):
    parked = _park(isolated_api)
    before = control.snapshot(KEY)
    with pytest.raises(api.HTTPException) as denied:
        _request(KEY, parked.run_id, "resume", authorization=authorization)
    assert denied.value.status_code == 403
    after = control.snapshot(KEY)
    assert after["state"] == before["state"] == "paused"
    assert after["run_id"] == before["run_id"] and not parked.continued.is_set()
    _resume_finish(parked)


@pytest.mark.parametrize("scanner,action", [
    ("new_listing", "pause"), ("penny_positions", "pause"),
    ("not_a_scanner", "pause"), ("strat_../private", "pause"),
    (KEY, "cancel"), ("strat_" + "x" * 100, "pause"),
])
def test_control_route_rejects_unsupported_controls_with_400(isolated_api, scanner, action):
    with pytest.raises(api.HTTPException) as denied:
        _request(scanner, "valid-run", action)
    assert denied.value.status_code == 400
    assert denied.value.detail == "scan_control_unsupported"
    assert control._CONTROLS == {} and isolated_api.workers == []


@pytest.mark.parametrize("run_id", ["older-run", "", "x" * 97, "../private"])
def test_control_route_rejects_stale_or_invalid_run_id_with_409(isolated_api, run_id):
    parked = _park(isolated_api)
    with pytest.raises(api.HTTPException) as denied:
        _request(KEY, run_id, "resume")
    assert denied.value.status_code == 409
    assert denied.value.detail == "scan_control_stale_run"
    assert control.snapshot(KEY)["state"] == "paused"
    _resume_finish(parked)


def test_finishing_worker_rejects_pause_without_interrupting_delivery_boundary(isolated_api):
    sealed, release = isolated_api.event(), isolated_api.event()
    completed = []

    def work():
        api._scan_control_point(finishing=True)
        sealed.set()
        assert release.wait(3)
        completed.append("committed-once")

    _add_state(KEY)
    assert api._run_scan_safe(KEY, work) and sealed.wait(3)
    worker = isolated_api.workers[0]
    run_id = api._scan_status[KEY]["last_run_id"]
    for action in ("pause", "resume"):
        with pytest.raises(api.HTTPException) as denied:
            _request(KEY, run_id, action)
        assert denied.value.status_code == 409
        assert denied.value.detail == "scan_control_state_changed"
    assert control.snapshot(KEY)["state"] == "finishing"
    release.set()
    worker.join(3)
    assert completed == ["committed-once"] and isolated_api.cache_checks == [KEY]


def test_missing_auto_resume_schedule_fails_closed_without_pausing(isolated_api, monkeypatch):
    entered, release = isolated_api.event(), isolated_api.event()
    _add_state(KEY)
    assert api._run_scan_safe(KEY, lambda: (entered.set(), release.wait(3)))
    assert entered.wait(3)
    monkeypatch.setattr(api, "_scan_resume_at", lambda *a: None)
    with pytest.raises(api.HTTPException) as denied:
        _request(KEY, api._scan_status[KEY]["last_run_id"], "pause", auto_resume=True)
    assert denied.value.status_code == 409
    assert control.snapshot(KEY)["state"] == "running"
    release.set()
    isolated_api.workers[0].join(3)


def test_paused_watchdog_never_warns_and_resumed_budget_excludes_pause(isolated_api):
    parked = _park(isolated_api)
    api._scan_status[KEY]["_started_at"] = time.time() - 1005
    with control._CONDITION:
        control._CONTROLS[KEY]["paused_seconds"] = 1000
    health = api._scan_runtime_state(KEY, api._scan_status[KEY], timeout_minutes=1)
    assert health["runtime_health"] == "paused" and not health["timeout_exceeded"]
    assert 0 <= health["runtime_seconds"] <= 10
    assert api._scan_watchdog_check(KEY) is None
    assert isolated_api.notices == [] and "last_error" not in api._scan_status[KEY]
    _request(KEY, parked.run_id, "resume")
    assert parked.continued.wait(3)
    resumed = api._scan_runtime_state(KEY, api._scan_status[KEY], timeout_minutes=1)
    assert resumed["runtime_health"] == "running" and not resumed["timeout_exceeded"]
    assert resumed["runtime_seconds"] <= 10
    parked.finish.set()
    parked.worker.join(3)


@pytest.mark.parametrize("owner", ["strategy_scan", KEY])
def test_generic_results_report_actual_paused_round_or_manual_owner(isolated_api, owner):
    parked = _park(isolated_api, owner)
    result = api.get_scan_results(CUP, None, "stocks")
    assert result.scan_running and result.partial
    assert result.scan_error is None and result.count == 0
    assert result.scan_control["state"] == "paused"
    assert result.scan_control["owner_scan_key"] == owner
    assert result.scan_control["run_id"] == parked.run_id == result.scan_run_id
    assert result.scan_control["scope"] == ("strategy_round" if owner == "strategy_scan" else "scanner")
    assert result.cached_at is None and result.scan_last_completed_at == OLD_SUCCESS
    _resume_finish(parked)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_bi_results_include_same_authoritative_pause_metadata(isolated_api, direction):
    key = "bi_" + direction
    parked = _park(isolated_api, key)
    result = api.get_bi_results(direction)
    generic = api.get_scan_results(key, None, "stocks")
    for payload in (result, generic):
        assert payload.scan_running and payload.partial
        assert payload.scan_control["state"] == "paused"
        assert payload.scan_control["owner_scan_key"] == key
        assert payload.scan_control["run_id"] == parked.run_id == payload.scan_run_id
        assert payload.scan_last_completed_at == OLD_SUCCESS
    assert result.diagnostics["indicator_gate"]["minimum_green"] == 17
    _resume_finish(parked)


def test_default_results_without_query_report_bi_long_owner(isolated_api):
    parked = _park(isolated_api, "bi_long")
    result = api.get_scan_results()
    assert result.scan_running and result.scan_control["state"] == "paused"
    assert result.scan_control["owner_scan_key"] == "bi_long"
    assert result.scan_run_id == parked.run_id == result.scan_control["run_id"]
    _resume_finish(parked)


@pytest.mark.parametrize("send_email", [False, True])
@pytest.mark.parametrize("later_minutes", [0, 16])
def test_real_wrapper_revalidates_momentum_after_final_park_before_cache_and_mail(
        isolated_api, monkeypatch, send_email, later_minutes):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, NAME, NOW

    context = isolated_api
    written = _wrapper_fixture(monkeypatch)
    ready, boundary = context.event(), context.event()
    enriched_counts, returned, mail_rows = [], [], []
    key = "strat_momentum_breakout_long"

    def enrichment(rows):
        # This row already passed the first final confirmation check. Pausing
        # after this stage must still protect cache publication and mail input.
        enriched_counts.append(len(rows))
        ready.set()
        assert boundary.wait(3)

    class ClockAfterPause(datetime):
        @classmethod
        def now(cls, tz=None):
            value = NOW + timedelta(minutes=later_minutes)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(api, "_enrich_stock_business_quality_rows", enrichment)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts",
                        lambda strategy, rows, market: mail_rows.append(list(rows)))
    _add_state(key)
    assert api._run_scan_safe(key, lambda: returned.append(
        api._strategy_scan_wrapper(NAME, send_email=send_email)))
    assert ready.wait(3)
    worker = context.workers[0]
    run_id = api._scan_status[key]["last_run_id"]
    _request(key, run_id, "pause")
    boundary.set()
    _wait_state(key, "paused")
    assert enriched_counts == [1] and written == [] and mail_rows == []
    monkeypatch.setattr(api, "datetime", ClockAfterPause)
    _request(key, run_id, "resume")
    worker.join(3)
    assert not worker.is_alive()
    expected = 1 if later_minutes == 0 else 0
    assert len(returned) == 1 and len(returned[0]) == expected
    assert len(written) == 1 and len(written[0][0]) == expected
    assert mail_rows == ([returned[0]] if send_email else [])
    rejected = written[0][1]["metadata"]["diagnostics"]["rejected"]
    assert rejected.get("momentum:confirmation_expired_before_publication", 0) == (1 if later_minutes else 0)
    assert api._scan_status[key]["last_run_id"] == run_id
