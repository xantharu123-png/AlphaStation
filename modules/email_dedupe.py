"""Cross-process email deduplication backed by an atomic JSON store."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import threading
import time
import uuid
from typing import Dict, Iterator, Optional


_PROCESS_LOCK = threading.RLock()
_DEFAULT_MAX_KEEP_SECONDS = 7 * 86400


def _timestamp(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _ensure_lock_byte(lock_file) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)


def _acquire_file_lock(lock_file) -> None:
    if os.name == "nt":
        import msvcrt

        _ensure_lock_byte(lock_file)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_file_lock(lock_file) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_store(path: str) -> Iterator[None]:
    lock_path = f"{path}.lock"
    parent = os.path.dirname(os.path.abspath(lock_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    # The in-process lock also protects threads on platforms where file-lock
    # semantics are process-scoped rather than file-descriptor-scoped.
    with _PROCESS_LOCK:
        with open(lock_path, "a+b") as lock_file:
            _acquire_file_lock(lock_file)
            try:
                yield
            finally:
                _release_file_lock(lock_file)


def _load_unlocked(path: str, now: float, max_keep_seconds: int) -> Dict[str, float]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    dedupe: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if now - timestamp <= max_keep_seconds:
            dedupe[str(key)] = timestamp
    return dedupe


def _write_unlocked(path: str, dedupe: Dict[str, float]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(dedupe, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def load_email_dedupe(
    path: str,
    now: Optional[float] = None,
    max_keep_seconds: int = _DEFAULT_MAX_KEEP_SECONDS,
) -> Dict[str, float]:
    timestamp = _timestamp(now)
    with _locked_store(path):
        return _load_unlocked(path, timestamp, max_keep_seconds)


def save_email_dedupe(
    path: str,
    dedupe: Dict[str, float],
    now: Optional[float] = None,
    max_keep_seconds: int = _DEFAULT_MAX_KEEP_SECONDS,
) -> None:
    timestamp = _timestamp(now)
    cleaned: Dict[str, float] = {}
    for key, value in (dedupe or {}).items():
        try:
            value_float = float(value)
        except (TypeError, ValueError):
            continue
        if timestamp - value_float <= max_keep_seconds:
            cleaned[str(key)] = value_float
    with _locked_store(path):
        _write_unlocked(path, cleaned)


def email_dedupe_active(
    path: str,
    key: str,
    ttl_seconds: int,
    now: Optional[float] = None,
) -> bool:
    timestamp = _timestamp(now)
    with _locked_store(path):
        dedupe = _load_unlocked(path, timestamp, _DEFAULT_MAX_KEEP_SECONDS)
        previous = dedupe.get(str(key))
        return previous is not None and timestamp - previous < int(ttl_seconds)


def email_dedupe_remaining(
    path: str,
    key: str,
    ttl_seconds: int,
    now: Optional[float] = None,
) -> int:
    timestamp = _timestamp(now)
    with _locked_store(path):
        dedupe = _load_unlocked(path, timestamp, _DEFAULT_MAX_KEEP_SECONDS)
        previous = dedupe.get(str(key))
        if previous is None:
            return 0
        return int(max(0, int(ttl_seconds) - (timestamp - previous)))


def email_dedupe_mark(path: str, key: str, now: Optional[float] = None) -> float:
    timestamp = _timestamp(now)
    with _locked_store(path):
        dedupe = _load_unlocked(path, timestamp, _DEFAULT_MAX_KEEP_SECONDS)
        dedupe[str(key)] = timestamp
        _write_unlocked(path, dedupe)
    return timestamp


def email_dedupe_claim(
    path: str,
    key: str,
    ttl_seconds: int,
    now: Optional[float] = None,
) -> bool:
    """Atomically reserve a key if no active reservation exists."""
    timestamp = _timestamp(now)
    with _locked_store(path):
        dedupe = _load_unlocked(path, timestamp, _DEFAULT_MAX_KEEP_SECONDS)
        previous = dedupe.get(str(key))
        if previous is not None and timestamp - previous < int(ttl_seconds):
            return False
        dedupe[str(key)] = timestamp
        _write_unlocked(path, dedupe)
    return True


def email_dedupe_release(
    path: str,
    key: str,
    claimed_at: Optional[float] = None,
) -> bool:
    """Release only the reservation owned by the caller.

    ``claimed_at`` prevents a failed sender from deleting a newer reservation
    created by another process after the original claim expired.
    """
    expected = None if claimed_at is None else float(claimed_at)
    timestamp = time.time() if expected is None else expected
    with _locked_store(path):
        dedupe = _load_unlocked(path, timestamp, _DEFAULT_MAX_KEEP_SECONDS)
        current = dedupe.get(str(key))
        if current is None:
            return False
        if expected is not None and current != expected:
            return False
        dedupe.pop(str(key), None)
        _write_unlocked(path, dedupe)
    return True
