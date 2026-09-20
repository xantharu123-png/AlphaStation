"""Cooperative in-process scan parking at explicitly safe owner boundaries.

No thread is killed and no stack is serialized. Provider, cache and delivery
code must never call safe_point while holding their own locks.
"""
from contextlib import contextmanager
import math
import re
import threading
import time


_CONDITION = threading.Condition(threading.RLock())
_CONTROLS = {}
_LOCAL = threading.local()
_MAX_FINISHED = 128
STATES = frozenset({"running", "pause_requested", "paused", "finishing", "finished", "restart_required"})


class ScanRestartRequired(BaseException):
    """Control flow, deliberately not swallowed by provider Exception handlers."""
    def __init__(self, *, automatic=False, paused_seconds=0., reason="data_epoch_changed"):
        super().__init__("scan_restart_required")
        self.automatic = bool(automatic)
        self.paused_seconds = max(0., float(paused_seconds))
        self.reason = reason


def _supported(key):
    return (isinstance(key, str) and len(key) <= 96
            and bool(re.fullmatch(r"[a-z0-9_]+", key))
            and (key in {"strategy_scan", "bi_long", "bi_short"} or key.startswith("strat_")))


def _epoch(value):
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError("invalid_resume_at")
    try:
        if math.isfinite(value) and value > 0:
            return float(value)
    except OverflowError:
        pass
    raise ValueError("invalid_resume_at")


def _valid_token(value):
    if type(value) is not tuple or not value:
        return False
    for part in value:
        if type(part) is tuple:
            if not _valid_token(part):
                return False
        elif type(part) is str:
            if not part:
                return False
        elif type(part) in (int, float, bool):
            try:
                if not math.isfinite(part):
                    return False
            except OverflowError:
                return False
        else:
            return False
    return True


def _entry(key, run_id):
    if not _supported(key):
        raise ValueError("scan_control_unsupported")
    entry = _CONTROLS.get(key)
    if entry is None or entry["run_id"] != run_id:
        raise ValueError("scan_control_stale_run")
    return entry


def _snapshot(entry):
    active = max(0., time.monotonic() - entry["pause_started"]) if entry["pause_started"] is not None else 0.
    return {"owner_scan_key": entry["key"], "run_id": entry["run_id"], "state": entry["state"],
            "paused_seconds": entry["paused_seconds"] + active, "paused_at": entry["paused_at"],
            "resume_at": entry["resume_at"], "auto_resume": entry["auto_resume"]}


def register(key, run_id, *, resume_at=None, current_data_token, data_token, auto_allowed):
    if not _supported(key):
        raise ValueError("scan_control_unsupported")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", run_id)
            or not callable(current_data_token) or not callable(auto_allowed)):
        raise ValueError("scan_control_invalid_registration")
    resume_at = _epoch(resume_at)
    with _CONDITION:
        old = _CONTROLS.get(key)
        if old is not None and not old["ended"]:
            raise ValueError("scan_control_busy")
        _CONTROLS[key] = {"key": key, "run_id": run_id, "state": "running", "resume_at": resume_at,
            "auto_resume": False, "paused_at": None, "pause_started": None, "paused_seconds": 0.,
            "current_data_token": current_data_token, "data_token": data_token,
            "auto_allowed": auto_allowed, "resume_automatic": False, "ended": False}
        finished = [name for name, item in _CONTROLS.items() if item["state"] == "finished" and name != key]
        for name in finished[:-_MAX_FINISHED]:
            _CONTROLS.pop(name, None)
        _CONDITION.notify_all()
        return _snapshot(_CONTROLS[key])


@contextmanager
def bind(key, run_id):
    with _CONDITION:
        entry = _entry(key, run_id)
        if entry["ended"]:
            raise ValueError("scan_control_finished")
    previous = getattr(_LOCAL, "owner", None)
    _LOCAL.owner = (key, run_id)
    try:
        yield
    finally:
        _LOCAL.owner = previous


def snapshot(key):
    with _CONDITION:
        entry = _CONTROLS.get(key) if _supported(key) else None
        return _snapshot(entry) if entry is not None else {}


def request_pause(key, run_id, auto_resume=True, resume_at=None):
    resume_at = _epoch(resume_at)
    with _CONDITION:
        entry = _entry(key, run_id)
        if entry["state"] in {"finishing", "finished", "restart_required"}:
            raise ValueError("scan_control_not_pausable")
        if entry["state"] != "paused":
            entry["state"] = "pause_requested"
        entry["auto_resume"] = bool(auto_resume)
        if resume_at is not None:
            entry["resume_at"] = resume_at
        _CONDITION.notify_all()
        return _snapshot(entry)


def request_resume(key, run_id):
    with _CONDITION:
        entry = _entry(key, run_id)
        if entry["state"] in {"finishing", "finished", "restart_required"}:
            raise ValueError("scan_control_not_resumable")
        entry["state"] = "running"
        entry["resume_automatic"] = False
        _CONDITION.notify_all()
        return _snapshot(entry)


def _finish_pause(entry):
    if entry["pause_started"] is None:
        return 0.
    duration = max(0., time.monotonic() - entry["pause_started"])
    entry["paused_seconds"] += duration
    entry["pause_started"] = None
    entry["paused_at"] = None
    return duration


def safe_point():
    owner = getattr(_LOCAL, "owner", None)
    if owner is None:
        return 0.
    total_paused = 0.
    was_parked = False
    while True:
        with _CONDITION:
            entry = _entry(*owner)
            if entry["state"] in {"finished", "restart_required"}:
                raise ScanRestartRequired(paused_seconds=total_paused, reason="owner_finished")
            if entry["state"] == "pause_requested":
                entry["state"] = "paused"
                if entry["pause_started"] is None:
                    entry["pause_started"] = time.monotonic()
                    entry["paused_at"] = time.time()
                _CONDITION.notify_all()
            if entry["state"] == "paused":
                was_parked = True
                due = (entry["auto_resume"] and entry["resume_at"] is not None
                       and time.time() >= entry["resume_at"])
                if not due:
                    delay = max(.01, min(30., entry["resume_at"] - time.time())) if entry["auto_resume"] and entry["resume_at"] else None
                    _CONDITION.wait(timeout=delay)
                    continue
                allowed_callback = entry["auto_allowed"]
            else:
                total_paused += _finish_pause(entry)
                if not was_parked:
                    return total_paused
                token_callback, data_token = entry["current_data_token"], entry["data_token"]
                automatic = entry["resume_automatic"]
                allowed_callback = None
        # Application callbacks are never invoked while the controller lock is
        # held. In particular, they may consult API/calendar state safely.
        if allowed_callback is not None:
            try:
                allowed = bool(allowed_callback())
            except Exception:
                allowed = False
            with _CONDITION:
                entry = _entry(*owner)
                if entry["state"] == "paused":
                    if allowed and entry["auto_resume"] and entry["resume_at"] is not None and time.time() >= entry["resume_at"]:
                        entry["state"] = "running"
                        entry["resume_automatic"] = True
                        _CONDITION.notify_all()
                    else:
                        _CONDITION.wait(timeout=30.)
            continue
        try:
            current_token = token_callback()
            valid = _valid_token(data_token) and _valid_token(current_token) and current_token == data_token
        except Exception:
            valid = False
        with _CONDITION:
            entry = _entry(*owner)
            if not valid:
                entry["state"] = "restart_required"
                _CONDITION.notify_all()
                raise ScanRestartRequired(automatic=automatic, paused_seconds=total_paused)
            if entry["state"] == "pause_requested":
                continue  # A second request won the race before work resumed.
            return total_paused


def seal():
    """Honor a pending pause, then atomically reject any later pause request."""
    owner = getattr(_LOCAL, "owner", None)
    if owner is None:
        return 0.
    paused = 0.
    while True:
        try:
            paused += safe_point()
        except ScanRestartRequired as exc:
            exc.paused_seconds += paused
            raise
        with _CONDITION:
            entry = _entry(*owner)
            if entry["state"] == "pause_requested":
                continue
            entry["state"] = "finishing"
            _CONDITION.notify_all()
            return paused


def end(key, run_id):
    with _CONDITION:
        entry = _CONTROLS.get(key)
        if entry is None or entry["run_id"] != run_id:
            return False
        _finish_pause(entry)
        entry["ended"] = True
        if entry["state"] != "restart_required":
            entry["state"] = "finished"
        _CONDITION.notify_all()
        return True
