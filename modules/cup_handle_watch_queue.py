"""Durable, bounded queue for next-session Cup & Handle trigger watches.

The queue stores only code-owned market/setup fields supplied by ``api.py``.
Persistence reuses the repository's locked, atomic JSON state utility so an
API restart or overlapping scheduler tick cannot lose or double-claim work.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from modules.regime_filter import load_state, update_state


DEFAULT_QUEUE_PATH = (
    Path(__file__).resolve().parent.parent
    / "data_cache"
    / "cup_handle_watch_queue.json"
)
QUEUE_VERSION = 1
DEFAULT_LEASE_SECONDS = 240.0
MAX_ITEMS = 100
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")


def _clean_state(raw: Any) -> Dict[str, Any]:
    state = raw if isinstance(raw, dict) else {}
    items = state.get("items")
    return {
        "version": QUEUE_VERSION,
        "items": dict(items) if isinstance(items, dict) else {},
    }


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    ticker = str(entry.get("ticker") or "").strip().upper()
    row = entry.get("row")
    try:
        lease_until = entry.get("lease_until")
        generation = entry.get("generation")
        return bool(
            _TICKER_RE.fullmatch(ticker)
            and isinstance(row, dict)
            and str(entry.get("confirmation_date") or "")
            and str(entry.get("target_session_date") or "")
            and float(entry.get("breakout_level") or 0) > 0
            and float(entry.get("expires_at") or 0) > 0
            and (
                lease_until in (None, "")
                or float(lease_until) >= 0
            )
            and (
                generation in (None, "")
                or int(generation) >= 0
            )
        )
    except (TypeError, ValueError, OverflowError):
        return False


def upsert_watch(
    entry: Dict[str, Any], *, path: Optional[Path | str] = None
) -> bool:
    """Atomically insert/refresh one deterministic pattern-session identity."""
    if not _valid_entry(entry):
        return False
    clean = dict(entry)
    clean["ticker"] = str(clean["ticker"]).strip().upper()
    identity = str(clean.get("id") or "").strip()
    if not identity:
        identity = "|".join(
            (
                clean["ticker"],
                str(clean["confirmation_date"]),
                str(clean["target_session_date"]),
            )
        )
    clean["id"] = identity

    def _mutate(raw: dict) -> dict:
        state = _clean_state(raw)
        items = state["items"]
        previous = items.get(identity)
        if isinstance(previous, dict):
            clean.setdefault("created_at", previous.get("created_at"))
        try:
            prior_generation = int((previous or {}).get("generation") or 0)
        except (TypeError, ValueError, OverflowError):
            prior_generation = 0
        clean["generation"] = prior_generation + 1
        # A full sweep refreshes evidence, but must not steal an active lease.
        if isinstance(previous, dict) and float(previous.get("lease_until") or 0) > time.time():
            clean["lease_owner"] = previous.get("lease_owner")
            clean["lease_until"] = previous.get("lease_until")
        items[identity] = clean
        if len(items) > MAX_ITEMS:
            ordered = sorted(
                items.values(),
                key=lambda value: float((value or {}).get("updated_at") or 0),
                reverse=True,
            )[:MAX_ITEMS]
            state["items"] = {
                str(value.get("id")): value
                for value in ordered
                if isinstance(value, dict) and value.get("id")
            }
        return state

    return update_state(_mutate, path or DEFAULT_QUEUE_PATH)


def claim_for_session(
    session_date: str,
    *,
    now_ts: Optional[float] = None,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    limit: int = 25,
    path: Optional[Path | str] = None,
) -> list[Dict[str, Any]]:
    """Prune invalid/expired entries and lease due rows in one locked update."""
    current = float(time.time() if now_ts is None else now_ts)
    owner = uuid.uuid4().hex
    claimed: list[Dict[str, Any]] = []
    wanted_date = str(session_date or "")

    def _mutate(raw: dict) -> dict:
        state = _clean_state(raw)
        kept: Dict[str, Any] = {}
        for identity, value in state["items"].items():
            if not _valid_entry(value):
                continue
            item = dict(value)
            # This is intentionally a one-session queue.  Future/past target
            # dates are not silently rolled into another trading day.
            target_date = str(item.get("target_session_date") or "")
            if target_date < wanted_date or float(item.get("expires_at") or 0) <= current:
                continue
            if target_date > wanted_date:
                kept[str(identity)] = item
                continue
            lease_until = float(item.get("lease_until") or 0)
            if lease_until <= current and len(claimed) < max(1, int(limit)):
                item["lease_owner"] = owner
                item["lease_until"] = current + max(1.0, float(lease_seconds))
                claimed.append(dict(item))
            kept[str(identity)] = item
        state["items"] = kept
        return state

    if not update_state(_mutate, path or DEFAULT_QUEUE_PATH):
        return []
    return claimed


def prune_for_session(
    session_date: str,
    *,
    now_ts: Optional[float] = None,
    path: Optional[Path | str] = None,
) -> bool:
    """Remove corrupt, expired and non-target-session rows without leasing."""
    current = float(time.time() if now_ts is None else now_ts)
    wanted_date = str(session_date or "")

    def _mutate(raw: dict) -> dict:
        state = _clean_state(raw)
        state["items"] = {
            str(identity): dict(value)
            for identity, value in state["items"].items()
            if _valid_entry(value)
            and str(value.get("target_session_date") or "") >= wanted_date
            and float(value.get("expires_at") or 0) > current
        }
        return state

    return update_state(_mutate, path or DEFAULT_QUEUE_PATH)


def finish_claim(
    identity: str,
    owner: str,
    *,
    remove: bool,
    generation: Optional[int] = None,
    path: Optional[Path | str] = None,
) -> bool:
    """Conditionally remove or release only the exact queue lease owner."""
    key = str(identity or "")
    lease_owner = str(owner or "")
    if not key or not lease_owner:
        return False
    try:
        expected_generation = (
            None if generation is None else int(generation)
        )
    except (TypeError, ValueError, OverflowError):
        return False
    changed = False

    def _mutate(raw: dict) -> dict:
        nonlocal changed
        state = _clean_state(raw)
        item = state["items"].get(key)
        if not isinstance(item, dict) or str(item.get("lease_owner") or "") != lease_owner:
            return state
        changed = True
        try:
            current_generation = int(item.get("generation") or 0)
        except (TypeError, ValueError, OverflowError):
            state["items"].pop(key, None)
            return state
        if expected_generation is not None and current_generation != expected_generation:
            # A full sweep refreshed this identity while the monitor worked on
            # its old claimed copy. Preserve the newer row and merely release
            # the inherited lease so the next monitor can evaluate it.
            refreshed = dict(item)
            refreshed.pop("lease_owner", None)
            refreshed.pop("lease_until", None)
            state["items"][key] = refreshed
            return state
        if remove:
            state["items"].pop(key, None)
        else:
            released = dict(item)
            released.pop("lease_owner", None)
            released.pop("lease_until", None)
            state["items"][key] = released
        return state

    return bool(update_state(_mutate, path or DEFAULT_QUEUE_PATH) and changed)


def queue_snapshot(*, path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Internal/test-only snapshot; never expose rows through public health."""
    return _clean_state(load_state(path or DEFAULT_QUEUE_PATH))
