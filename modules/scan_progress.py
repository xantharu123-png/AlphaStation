"""Run-bound file progress for BI and Biotech; never signal/result authority."""
import math
import re

from modules import scan_control


KEYS = frozenset({"bi_long", "bi_short", "biotech"})
ACTIVE_STATES = frozenset({"running", "pause_requested", "paused", "finishing"})
PROGRESS_STATES = frozenset({"scanning", "running", "done"})


def identity(key):
    """Only the executing bound worker can label its own progress."""
    owner = scan_control.bound_owner()
    if key not in KEYS or not owner or owner[0] != key:
        return {}
    current = scan_control.snapshot(key)
    if current.get("run_id") != owner[1] or current.get("state") not in ACTIVE_STATES:
        return {}
    return {"progress_version": 1, "owner_scan_key": key, "run_id": owner[1]}


def _epoch(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _count(value):
    return type(value) is int and 0 <= value <= 1_000_000_000


def current_progress(raw, key, state, *, started_at, now):
    """Project a file only while the same scheduler/control owner is alive.

    Old counts can be a useful last-known checkpoint during a pause or slow
    provider request. Retain their actual age; never invent a fresh timestamp.
    A new process/run must not adopt them, even if its result cache is unchanged.
    """
    if key not in KEYS or not isinstance(raw, dict) or not isinstance(state, dict):
        return None
    control = state.get("control")
    run_id = state.get("run_id")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", run_id)
            or state.get("running") is not True or not isinstance(control, dict)
            or control.get("worker_alive") is not True or control.get("supported") is not True
            or control.get("state") not in ACTIVE_STATES
            or control.get("run_id") != run_id or control.get("owner_scan_key") != key
            or type(raw.get("progress_version")) is not int or raw["progress_version"] != 1
            or raw.get("run_id") != run_id or raw.get("owner_scan_key") != key
            or raw.get("status") not in PROGRESS_STATES):
        return None
    stamp = raw.get("timestamp")
    if not all(_epoch(value) for value in (stamp, started_at, now)) or not started_at <= stamp <= now + 5:
        return None
    checked, total, hits = (raw.get(field) for field in ("checked", "total", "hits"))
    if not all(_count(value) for value in (checked, total, hits)) or not hits <= checked <= total:
        return None
    return {"progress_version": 1, "owner_scan_key": key, "run_id": run_id,
            "running": True, "status": raw["status"], "checked": checked, "total": total, "hits": hits,
            "detail": raw.get("detail", "")[:400] if isinstance(raw.get("detail", ""), str) else "",
            "timestamp": stamp, "run_started_at": started_at, "observed_at": now,
            "seconds_since_progress": max(0, int(now - stamp))}
