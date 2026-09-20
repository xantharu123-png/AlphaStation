"""Real-thread cooperative parking, owner races and active-time accounting."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from modules import scan_control as control
from modules import stock_scan_runtime as runtime


KEY = "strat_cup_and_handle_breakout"
RUN = "test-run"


@pytest.fixture(autouse=True)
def clean_controls(monkeypatch):
    monkeypatch.setattr(control, "_CONTROLS", {})
    monkeypatch.setattr(control, "_LOCAL", threading.local())
    yield
    for key, entry in list(control._CONTROLS.items()):
        control.end(key, entry["run_id"])


def register(*, key=KEY, run=RUN, token=("2026-09-18", "config-v1"), current=None, allowed=lambda: True, resume_at=None):
    return control.register(key, run, resume_at=resume_at, current_data_token=current or (lambda: token),
                            data_token=token, auto_allowed=allowed)


def wait_state(state, *, key=KEY):
    with control._CONDITION:
        assert control._CONDITION.wait_for(lambda: control.snapshot(key).get("state") == state, timeout=2), control.snapshot(key)


def start_worker(function, *, key=KEY, run=RUN):
    errors = []
    def work():
        try:
            with control.bind(key, run):
                function()
        except BaseException as exc:
            errors.append(exc)
        finally:
            control.end(key, run)
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    return worker, errors


def test_actual_worker_parks_and_resumes_same_stack_without_double_work():
    register()
    control.request_pause(KEY, RUN, auto_resume=False)
    visited = []
    def body():
        for index in range(4):
            control.safe_point()
            visited.append(index)
    worker, errors = start_worker(body)
    wait_state("paused")
    assert worker.is_alive() and visited == []
    assert control.snapshot(KEY)["paused_at"] > 0
    with pytest.raises(ValueError, match="busy"):
        register(run="replacement")
    control.request_resume(KEY, RUN)
    worker.join(timeout=2)
    assert not worker.is_alive() and not errors
    assert visited == [0, 1, 2, 3]
    state = control.snapshot(KEY)
    assert state["state"] == "finished" and state["run_id"] == RUN
    assert state["paused_seconds"] >= 0 and state["paused_at"] is None


def test_auto_resume_requires_due_time_and_allowed_market():
    allowed = [False]
    register(allowed=lambda: allowed[0], resume_at=time.time() - 1)
    control.request_pause(KEY, RUN, auto_resume=True)
    completed = []
    worker, errors = start_worker(lambda: (control.safe_point(), completed.append(True)))
    wait_state("paused")
    assert completed == [] and worker.is_alive()
    allowed[0] = True
    with control._CONDITION:
        control._CONDITION.notify_all()
    worker.join(timeout=2)
    assert completed == [True] and not errors


def test_future_auto_resume_happens_without_new_worker():
    register(resume_at=time.time() + .05)
    control.request_pause(KEY, RUN)
    visited = []
    worker, errors = start_worker(lambda: (control.safe_point(), visited.append(threading.get_ident())))
    wait_state("paused")
    worker.join(timeout=2)
    assert visited == [worker.ident] and not errors


@pytest.mark.parametrize("token,current", [(None, None), ((), ()), ((None,), (None,)),
    (("old",), ("new",)), (("old",), ["old"]), ((float("nan"),), (float("nan"),))])
@pytest.mark.parametrize("automatic", [False, True])
def test_changed_or_invalid_data_epoch_requires_fresh_restart(token, current, automatic):
    allowed = [False]
    register(token=token, current=lambda: current, allowed=lambda: allowed[0], resume_at=time.time() - 1)
    control.request_pause(KEY, RUN, auto_resume=automatic)
    visited = []
    worker, errors = start_worker(lambda: (control.safe_point(), visited.append("unsafe continuation")))
    wait_state("paused")
    if automatic:
        allowed[0] = True
        with control._CONDITION:
            control._CONDITION.notify_all()
    else:
        control.request_resume(KEY, RUN)
    worker.join(timeout=2)
    assert not worker.is_alive() and visited == []
    assert len(errors) == 1 and isinstance(errors[0], control.ScanRestartRequired)
    assert not isinstance(errors[0], Exception)
    assert errors[0].automatic is automatic
    assert control.snapshot(KEY)["state"] == "restart_required"


def test_stale_ids_crypto_and_finished_controls_cannot_mutate_owner():
    register()
    for request in (control.request_pause, control.request_resume):
        with pytest.raises(ValueError, match="stale_run"):
            request(KEY, "old-run")
        with pytest.raises(ValueError, match="unsupported"):
            request("crypto_strat_cup", RUN)
    control.end(KEY, RUN)
    register(run="new-run")
    assert control.end(KEY, RUN) is False
    assert control.snapshot(KEY)["run_id"] == "new-run"
    with pytest.raises(ValueError, match="unsupported"):
        register(key="crypto_explosion")


def test_snapshot_never_exposes_callbacks_tokens_or_arbitrary_rows():
    register(token=("PRIVATE_CONFIG_TOKEN",))
    state = control.snapshot(KEY)
    assert set(state) == {"owner_scan_key", "run_id", "state", "paused_seconds", "paused_at", "resume_at", "auto_resume"}
    assert "PRIVATE" not in json.dumps(state)
    state["state"] = "paused"
    assert control.snapshot(KEY)["state"] == "running"
    assert control.safe_point() == control.seal() == 0


def test_seal_honors_pending_pause_and_rejects_later_pause():
    register()
    control.request_pause(KEY, RUN, auto_resume=False)
    sealed, finish = threading.Event(), threading.Event()
    def body():
        control.seal()
        sealed.set()
        assert finish.wait(2)
    worker, errors = start_worker(body)
    wait_state("paused")
    assert not sealed.is_set()
    control.request_resume(KEY, RUN)
    assert sealed.wait(2)
    assert control.snapshot(KEY)["state"] == "finishing"
    with pytest.raises(ValueError, match="not_pausable"):
        control.request_pause(KEY, RUN)
    finish.set()
    worker.join(timeout=2)
    assert not errors


def test_seal_catches_pause_arriving_between_safe_point_and_finish_lock(monkeypatch):
    register()
    original = control.safe_point
    first = [True]
    def raced_point():
        seconds = original()
        if first[0]:
            first[0] = False
            control.request_pause(KEY, RUN, auto_resume=False)
        return seconds
    monkeypatch.setattr(control, "safe_point", raced_point)
    commits = []
    worker, errors = start_worker(lambda: (control.seal(), commits.append(True)))
    wait_state("paused")
    assert commits == []
    control.request_resume(KEY, RUN)
    worker.join(timeout=2)
    assert commits == [True] and not errors


def test_callbacks_do_not_hold_controller_lock():
    def outside_lock():
        values = []
        observer = threading.Thread(target=lambda: values.append(control.snapshot(KEY)))
        observer.start()
        observer.join(timeout=1)
        assert not observer.is_alive() and values
        return ("fixed",)
    register(token=("fixed",), current=outside_lock)
    control.request_pause(KEY, RUN, auto_resume=False)
    worker, errors = start_worker(control.safe_point)
    wait_state("paused")
    control.request_resume(KEY, RUN)
    worker.join(timeout=2)
    assert not errors


def test_paused_seconds_do_not_consume_nested_runtime_or_stage_budget(monkeypatch):
    clock = {"now": 100.}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(runtime, "LEAF_WORK_SECONDS", 10)
    monkeypatch.setattr(runtime, "SWEEP_WORK_SECONDS", 25)
    def parked():
        clock["now"] += 3600.
        return 3600.
    monkeypatch.setattr(control, "safe_point", parked)
    with runtime.scope("outer", sweep=True) as parent:
        with runtime.scope("inner") as leaf:
            with runtime.measure("history"):
                clock["now"] += 1.
                runtime.safe_point()
                clock["now"] += 2.
                runtime.checkpoint()
            assert runtime.diagnostics()["leaf_elapsed_seconds"] == 3
            assert runtime.diagnostics()["elapsed_seconds"] == 3
            assert runtime.diagnostics()["stage_elapsed_ms"] == {"history": 3000}
            assert leaf["deadline"] == 3710.
            assert parent["started"] == 3700.
            assert parent["root"]["work_deadline"] == 3725.
        with runtime.scope("next"):
            runtime.checkpoint()
        clock["now"] += 23.
        with pytest.raises(runtime.ScanWorkTimeout), runtime.scope("expired"):
            runtime.checkpoint()


def test_restart_unwind_also_excludes_park_time(monkeypatch):
    clock = {"now": 100.}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    def expired():
        clock["now"] += 3600.
        raise control.ScanRestartRequired(paused_seconds=3600.)
    monkeypatch.setattr(control, "safe_point", expired)
    with runtime.scope("leaf"):
        with pytest.raises(control.ScanRestartRequired):
            runtime.safe_point()
        assert runtime.diagnostics()["elapsed_seconds"] == 0


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), 2**2048, True, -1., "120"])
def test_invalid_pause_duration_cannot_corrupt_deadlines(seconds):
    with runtime.scope("leaf") as state:
        before = state["deadline"], state["root"]["work_deadline"], state["root"]["started"]
        runtime.exclude_pause(seconds)
        assert (state["deadline"], state["root"]["work_deadline"], state["root"]["started"]) == before


def test_seal_restart_after_second_park_preserves_all_pause_accounting(monkeypatch):
    register()
    calls = []
    def points():
        calls.append(True)
        if len(calls) == 1:
            control.request_pause(KEY, RUN, auto_resume=False)
            return 10.
        raise control.ScanRestartRequired(paused_seconds=20., automatic=True)
    monkeypatch.setattr(control, "safe_point", points)
    with control.bind(KEY, RUN), pytest.raises(control.ScanRestartRequired) as error:
        control.seal()
    assert error.value.paused_seconds == 30.
    assert error.value.automatic is True
