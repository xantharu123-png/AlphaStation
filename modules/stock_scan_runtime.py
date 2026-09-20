"""Opt-in cooperative budgets and bounded, run-local stock data reuse.

No worker is killed or replaced. Checkpoints unwind a slow leaf normally;
the sweep can then guard completed siblings in its existing single mail batch.
An in-flight OS/network call still needs its own timeout and the watchdog.
"""
from collections import OrderedDict
from contextlib import contextmanager
from functools import wraps
import json
import math
import re
import threading
import time
import zlib
from modules import scan_control


LEAF_WORK_SECONDS = 20 * 60
SWEEP_WORK_SECONDS = 30 * 60
CACHE_BYTES = 32 * 1024 * 1024
_LOCAL = threading.local()
_PROGRESS_LOCK = threading.Lock()
_PROGRESS = {}
PHASES = frozenset({"starting", "universe", "history", "analyzing", "special_filter",
                    "enrichment", "publish", "mail_guard", "work_timeout", "error", "complete"})
STAGE_TIMING_LABELS = frozenset({"history", "structure", "execution_history", "plan",
                                 "cache_publish", "special_filter"})
STAGE_TIMING_SEMANTICS = "per_leaf_inclusive_elapsed_ms_not_additive"


def diagnostics():
    state = current()
    if state is None:
        return {}
    root = state["root"]
    return {"runtime_phase": state["phase"], "provider_requests": root["requests"],
            "history_cache_hits": root["cache_hits"],
            "rate_wait_seconds": int(root["rate_wait_seconds"]),
            "stage_elapsed_ms": {
                label: min(2**63 - 1, max(0, int(seconds * 1000)))
                for label, seconds in state["stage_elapsed_seconds"].items()
                if label in STAGE_TIMING_LABELS
            },
            "stage_timing_semantics": STAGE_TIMING_SEMANTICS,
            "leaf_elapsed_seconds": max(0, int(time.monotonic() - state["started"])),
            "elapsed_seconds": max(0, int(time.monotonic() - root["started"]))}


@contextmanager
def measure(label):
    """Measure completed code blocks without changing budget/error behavior.

    Values are cumulative for the current leaf, including failed blocks. A
    nested block also contributes to its outer stage, so timings are explicitly
    not additive. Unfinished blocks are absent until they return or unwind.
    Labels are code-owned; outside an opted-in scan this helper is a no-op.
    """
    state = current()
    if state is None or type(label) is not str or label not in STAGE_TIMING_LABELS:
        yield
        return
    started = time.monotonic()
    paused_before = state["root"].get("excluded_pause_seconds", 0.)
    try:
        yield
    finally:
        excluded = state["root"].get("excluded_pause_seconds", 0.) - paused_before
        elapsed = max(0.0, time.monotonic() - started - excluded)
        totals = state["stage_elapsed_seconds"]
        totals[label] = min((2**63 - 1) / 1000, totals.get(label, 0.0) + elapsed)


class ScanWorkTimeout(RuntimeError):
    def __init__(self):
        super().__init__("scan_timeout")


def current():
    return getattr(_LOCAL, "state", None)


def exclude_pause(seconds):
    """Remove an actual safe-point park from every active nested work budget."""
    state = current()
    if state is None or type(seconds) not in (int, float) or seconds <= 0:
        return
    try:
        if not math.isfinite(seconds):
            return
    except OverflowError:
        return
    root = state["root"]
    root["excluded_pause_seconds"] = root.get("excluded_pause_seconds", 0.) + seconds
    root["started"] += seconds
    root["work_deadline"] += seconds
    for key in ("last_progress_at", "published", "logged"):
        if key in root:
            root[key] += seconds
    active = state
    while active is not None:
        active["started"] += seconds
        active["phase_started_at"] += seconds
        if active.get("deadline") is not None:
            active["deadline"] += seconds
        active = active.get("parent")


def safe_point():
    """Opt-in parking, never called implicitly from provider checkpoints."""
    try:
        paused = scan_control.safe_point()
    except scan_control.ScanRestartRequired as exc:
        exclude_pause(exc.paused_seconds)
        raise
    exclude_pause(paused)
    return paused


def seal():
    try:
        paused = scan_control.seal()
    except scan_control.ScanRestartRequired as exc:
        exclude_pause(exc.paused_seconds)
        raise
    exclude_pause(paused)
    return paused


def _key(name):
    return re.sub(r"[^a-z0-9_]+", "_", str(name).lower()).strip("_")[:96]


def _publish(state, *, force=False):
    now = time.monotonic()
    root = state["root"]
    if not force and now - root.get("published", 0) < 1:
        return
    root["published"] = now
    payload = {
        "running": root["running"], "strategy": state.get("strategy", ""),
        "phase": state.get("phase", "starting"),
        "checked": state.get("checked", 0), "total": state.get("total", 0),
        "requests": root["requests"], "history_cache_hits": root["cache_hits"],
        "cache_bytes": root["cache_bytes"], "rate_wait_seconds": round(root["rate_wait_seconds"], 1),
        "elapsed_seconds": round(now - root["started"], 1),
        "last_progress_at": root.get("last_progress_at", now),
        "phase_started_at": state.get("phase_started_at", now),
    }
    with _PROGRESS_LOCK:
        _PROGRESS[root["key"]] = payload
    if force or now - root.get("logged", 0) >= 30:
        root["logged"] = now
        print(f"[Stock Runtime] {root['key']} strategy={payload['strategy']} "
              f"phase={payload['phase']} checked={payload['checked']}/{payload['total']} "
              f"requests={payload['requests']} cache_hits={payload['history_cache_hits']} "
              f"rate_wait={payload['rate_wait_seconds']}s elapsed={payload['elapsed_seconds']}s", flush=True)


def progress(key):
    with _PROGRESS_LOCK:
        value = dict(_PROGRESS.get(key) or {})
    if value:
        now = time.monotonic()
        value["seconds_since_progress"] = max(0, int(now - value.pop("last_progress_at")))
        value["phase_seconds"] = max(0, int(now - value.pop("phase_started_at")))
    return value


def checkpoint(phase=None, *, checked=None, total=None):
    state = current()
    if state is None:
        return
    now = time.monotonic()
    if phase is not None and phase != state.get("phase"):
        state.update(phase=phase, phase_started_at=now)
    if checked is not None:
        if checked != state.get("checked"):
            state["root"]["last_progress_at"] = now
        state["checked"] = checked
    if total is not None:
        state["total"] = total
    _publish(state)
    deadline = state.get("deadline")
    if deadline is not None and now >= deadline:
        state["phase"] = "work_timeout"
        _publish(state, force=True)
        raise ScanWorkTimeout()


@contextmanager
def scope(name, *, sweep=False):
    previous = current()
    now = time.monotonic()
    owner = previous is None
    root = previous["root"] if previous else {
        "key": "strategy_scan" if sweep else "strat_" + _key(name),
        "started": now, "running": True, "requests": 0, "cache_hits": 0,
        "rate_wait_seconds": 0.0, "cache": OrderedDict(), "cache_bytes": 0,
        "work_deadline": now + (SWEEP_WORK_SECONDS if sweep else LEAF_WORK_SECONDS),
    }
    deadline = None if sweep else min(now + LEAF_WORK_SECONDS, root["work_deadline"])
    remaining_leaves = previous.get("remaining_leaves", 0) if previous else 0
    if deadline is not None and remaining_leaves > 0:
        # Reserve a fair opportunity for every unattempted sibling. Fast
        # finishes leave their unused time available to subsequent strategies.
        share = max(0.0, root["work_deadline"] - now) / remaining_leaves
        deadline = min(deadline, now + share)
    state = {"root": root, "strategy": _key(name), "phase": "starting", "phase_started_at": now,
             "started": now, "deadline": deadline, "stage_elapsed_seconds": {}, "parent": previous}
    _LOCAL.state = state
    _publish(state, force=True)
    try:
        yield state
    except Exception:
        state["phase"] = "work_timeout" if state.get("phase") == "work_timeout" else "error"
        raise
    else:
        state["phase"] = "complete"
    finally:
        if owner:
            root["running"] = False
        _publish(state, force=True)
        _LOCAL.state = previous


def bounded_leaf(function):
    @wraps(function)
    def run(*args, **kwargs):
        name = args[0] if args else kwargs.get("strategy_name", "stock_strategy")
        with scope(name):
            return function(*args, **kwargs)
    return run


def bounded_sweep(function):
    @wraps(function)
    def run(*args, **kwargs):
        with scope("auto_sweep", sweep=True):
            return function(*args, **kwargs)
    return run


def cache_get(key):
    state = current()
    if state is None:
        return None
    root = state["root"]
    entry = root["cache"].get(key)
    if entry is None:
        return None
    root["cache"].move_to_end(key)
    root["cache_hits"] += 1
    # Decode a fresh tree: strategy enrichment cannot mutate another leaf.
    return json.loads(zlib.decompress(entry))


def cache_put(key, value):
    state = current()
    if state is None:
        return
    try:
        entry = zlib.compress(json.dumps(value, separators=(",", ":"), allow_nan=False).encode(), 1)
    except (TypeError, ValueError):
        return  # A cache must not change validation or provider behaviour.
    if len(entry) > CACHE_BYTES:
        return
    root = state["root"]
    old = root["cache"].pop(key, b"")
    root["cache_bytes"] -= len(old)
    while root["cache"] and root["cache_bytes"] + len(entry) > CACHE_BYTES:
        _, removed = root["cache"].popitem(last=False)
        root["cache_bytes"] -= len(removed)
    root["cache"][key] = entry
    root["cache_bytes"] += len(entry)


def budgeted_wait(seconds, sleeper=time.sleep):
    checkpoint()
    state = current()
    deadline = state.get("deadline") if state else None
    wait = max(0.0, seconds)
    if deadline is not None:
        wait = min(wait, max(0.0, deadline - time.monotonic()))
    if state:
        state["root"]["rate_wait_seconds"] += wait
    sleeper(wait)
    checkpoint()


def request_timeout(timeout):
    checkpoint()
    state = current()
    if state is None:
        return timeout
    state["root"]["requests"] += 1
    deadline = state.get("deadline")
    if deadline is None:
        return timeout
    left = max(0.001, deadline - time.monotonic())
    def bounded(value):
        return min(float(value), left) if value is not None else left
    return tuple(bounded(value) for value in timeout) if isinstance(timeout, tuple) else bounded(timeout)
