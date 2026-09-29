"""Wall-clock schedule for automatic Gap Momentum stock discovery.

The clock belongs to the user's Swiss timezone, independently of the exchange
timezone. Monday 02:00 can therefore review Friday's completed US session even
though New York is still on Sunday. The scanner validates the latest completed
exchange session separately; this module does not invent a market-data age.

Clock functions are pure; GapScheduleStore retains admission slots durably.
A first adoption starts at the next configured slot; an existing pending slot
can be retried while an engine is occupied, without creating interval scans.
"""

from datetime import date, datetime, time, timedelta, timezone
from contextlib import contextmanager
import copy
import json
import math
import os
import stat
import tempfile
import threading
from zoneinfo import ZoneInfo


TIMEZONE = "Europe/Zurich"
LOCAL_TIMES = ("02:00", "12:00")
HOURS = (2, 12)
SCAN_NAMES = frozenset({"strat_gap_momentum_long", "strat_gap_momentum_short"})
_ZONE = ZoneInfo(TIMEZONE)


def _utc(value):
    try:
        if isinstance(value, datetime):
            parsed = value
        elif type(value) in (int, float) and math.isfinite(value):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        elif type(value) is str and 0 < len(value) <= 64:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _now(value):
    return _utc(datetime.now(timezone.utc) if value is None else value)


def _wall_clock(naive):
    """First occurrence for a repeated time; first valid minute after a gap.

    Swiss changes currently occur on excluded Sundays, but the conversion is
    still defined rather than relying on zoneinfo's imaginary-time behavior.
    """
    for minute in range(181):
        target = naive + timedelta(minutes=minute)
        choices = []
        for fold in (0, 1):
            candidate = target.replace(tzinfo=_ZONE, fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(_ZONE).replace(tzinfo=None) == target:
                choices.append(candidate)
        if choices:
            return min(choices)
    raise ValueError("gap_schedule_wall_time_unavailable")


def is_gap_scan(name):
    return type(name) is str and name in SCAN_NAMES


def automatic_scan_allowed(name, now_utc=None):
    """A Gap job starts only on a Swiss weekday; other jobs are not governed."""
    if not is_gap_scan(name):
        return True
    now = _now(now_utc)
    return now is not None and now.astimezone(_ZONE).weekday() < 5


def scheduled_slot(local_date, hour):
    """UTC epoch for an exact configured weekday slot, otherwise None."""
    try:
        if type(local_date) is str:
            local_date = date.fromisoformat(local_date)
        if type(local_date) is not date or type(hour) is not int or hour not in HOURS:
            return None
        if local_date.weekday() >= 5:
            return None
        return _wall_clock(datetime.combine(local_date, time(hour))).timestamp()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _slot_search(value, *, forward, inclusive):
    clock = _utc(value)
    if clock is None:
        return None
    day = clock.astimezone(_ZONE).date()
    hours = HOURS if forward else tuple(reversed(HOURS))
    try:
        for _ in range(10):
            for hour in hours:
                epoch = scheduled_slot(day, hour)
                if epoch is None:
                    continue
                candidate = datetime.fromtimestamp(epoch, timezone.utc)
                if ((forward and (candidate > clock or inclusive and candidate == clock))
                        or (not forward and (candidate < clock or inclusive and candidate == clock))):
                    return epoch
            day += timedelta(days=1 if forward else -1)
    except (OverflowError, OSError, TypeError, ValueError):
        return None
    return None


def initial_slot(now_utc=None):
    """First future/inclusive slot; an empty ledger never creates a catch-up."""
    return _slot_search(_now(now_utc), forward=True, inclusive=True)


def next_slot(timestamp):
    """Next configured slot strictly after an aware timestamp or UTC epoch."""
    return _slot_search(timestamp, forward=True, inclusive=False)


def latest_slot(now_utc=None):
    return _slot_search(_now(now_utc), forward=False, inclusive=True)


def slot_key(timestamp):
    """Stable wall-clock identity for persistence; non-slot timestamps fail."""
    clock = _utc(timestamp)
    if clock is None:
        return None
    local = clock.astimezone(_ZONE)
    for hour in HOURS:
        epoch = scheduled_slot(local.date(), hour)
        if epoch is not None and epoch == clock.timestamp():
            return f"{TIMEZONE}:{local.date().isoformat()}:{hour:02d}:00"
    return None


def due_slot(next_due, now_utc=None):
    """Pending current-day slot, or None; old missed slots never pile up.

    If an engine is busy at 02:00, the slot remains due until 12:00. If the
    server only returns after 12:00, one 12:00 run replaces that stale backlog.
    Friday's abandoned slot is never run during the weekend or Monday before
    Monday's first configured time. No attempt is marked complete here.
    """
    now, due = _now(now_utc), _utc(next_due)
    if now is None or due is None or slot_key(due) is None or due > now:
        return None
    local = now.astimezone(_ZONE)
    if local.weekday() >= 5:
        return None
    latest = latest_slot(now)
    if latest is None:
        return None
    latest_local = datetime.fromtimestamp(latest, timezone.utc).astimezone(_ZONE)
    if latest_local.date() != local.date() or latest < due.timestamp():
        return None
    return latest


def schedule_snapshot(name, next_run=None, now_utc=None):
    """Public clock metadata, composed by the caller with pause/worker state."""
    now = _now(now_utc)
    governed = is_gap_scan(name)
    invalid = governed and now is None
    weekend = governed and now is not None and now.astimezone(_ZONE).weekday() >= 5
    due = _utc(next_run)
    candidate = None
    if governed and now is not None:
        if due is not None and slot_key(due) is not None and due > now:
            candidate = due.timestamp()
        elif due is not None:
            candidate = due_slot(due, now)
        if candidate is None:
            candidate = initial_slot(now)
    scheduled = _utc(candidate)
    eligible = max(scheduled, now) if scheduled is not None and now is not None else None
    return {
        "automatic_paused": bool(invalid or weekend),
        "reason": "invalid_schedule_time" if invalid else "weekend" if weekend else None,
        "timezone": TIMEZONE,
        "schedule_type": "fixed_local_times",
        "local_times": list(LOCAL_TIMES),
        "weekdays": [0, 1, 2, 3, 4],
        "scheduled_at": scheduled.isoformat() if scheduled is not None else None,
        "slot_id": slot_key(scheduled),
        "next_eligible_at": eligible.isoformat() if eligible is not None else None,
    }


class GapScheduleStore:
    """Durable admission receipts for the single API scheduler owner.

    Reserving a slot consumes that automatic attempt before a worker starts.
    This survives a crash between admission and completion, without pretending
    that the scanner ran successfully. Engine contention can release the same
    reservation; an actual worker error waits for the next configured slot.
    """

    MAX_BYTES = 16 * 1024
    _ROOT_KEYS = {"schema_version", "timezone", "local_times", "jobs"}
    _JOB_KEYS = {"next_due", "last_attempt_slot", "last_attempt_at", "last_attempt_phase"}

    def __init__(self, path):
        self.path = os.path.abspath(os.fspath(path))
        self._lock = threading.RLock()

    @contextmanager
    def _guard(self):
        """Serialize read-modify-replace across threads and API processes."""
        with self._lock:
            descriptor = None
            locked = False
            try:
                os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
                flags = (os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_BINARY", 0))
                descriptor = os.open(self.path + ".lock", flags, 0o600)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 1:
                    raise ValueError("gap_schedule_state_lock_invalid")
                if metadata.st_size == 0:
                    os.write(descriptor, b"\0")
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                elif os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                else:
                    raise ValueError("gap_schedule_state_lock_unsupported")
                locked = True
                yield
            except OSError:
                raise ValueError("gap_schedule_state_lock_failed") from None
            finally:
                if descriptor is not None:
                    if locked:
                        try:
                            if os.name == "posix":
                                fcntl.flock(descriptor, fcntl.LOCK_UN)
                            elif os.name == "nt":
                                os.lseek(descriptor, 0, os.SEEK_SET)
                                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    os.close(descriptor)

    @staticmethod
    def _epoch(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            return None
        clock = _utc(value)
        return clock.timestamp() if clock is not None else None

    @staticmethod
    def _unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("gap_schedule_state_invalid")
            result[key] = value
        return result

    @classmethod
    def _validate(cls, payload):
        try:
            if (type(payload) is not dict or set(payload) != cls._ROOT_KEYS
                    or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                    or payload["timezone"] != TIMEZONE
                    or payload["local_times"] != list(LOCAL_TIMES)
                    or type(payload["jobs"]) is not dict or set(payload["jobs"]) != SCAN_NAMES):
                raise ValueError
            for job in payload["jobs"].values():
                if type(job) is not dict or set(job) != cls._JOB_KEYS:
                    raise ValueError
                due = cls._epoch(job["next_due"])
                if due is None or slot_key(due) is None:
                    raise ValueError
                attempted = cls._epoch(job["last_attempt_slot"])
                phase = job["last_attempt_phase"]
                if job["last_attempt_slot"] is None:
                    if job["last_attempt_at"] is not None or phase is not None:
                        raise ValueError
                else:
                    observed = cls._epoch(job["last_attempt_at"])
                    if attempted is None or slot_key(attempted) is None or observed is None or observed < attempted:
                        raise ValueError
                    if phase == "reserved":
                        if due != next_slot(attempted):
                            raise ValueError
                    elif phase == "released":
                        if due != attempted:
                            raise ValueError
                    else:
                        raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("gap_schedule_state_invalid") from None
        return payload

    def _read(self):
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(self.path, flags), "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_size > self.MAX_BYTES):
                    raise ValueError("gap_schedule_state_invalid")
                raw = handle.read(self.MAX_BYTES + 1)
                if len(raw) > self.MAX_BYTES:
                    raise ValueError("gap_schedule_state_invalid")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=self._unique_object)
            return self._validate(payload)
        except FileNotFoundError:
            return None
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise ValueError("gap_schedule_state_invalid") from None
        except OSError:
            raise ValueError("gap_schedule_state_unavailable") from None

    def _write(self, state):
        self._validate(state)
        parent = os.path.dirname(self.path)
        temporary = None
        try:
            raw = json.dumps(state, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(raw) > self.MAX_BYTES:
                raise ValueError("gap_schedule_state_invalid")
            os.makedirs(parent, mode=0o700, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".gap-schedule-", dir=parent)
            with os.fdopen(descriptor, "wb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name == "posix":
                directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except (OSError, ValueError, TypeError):
            raise ValueError("gap_schedule_state_write_failed") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _state(self, now):
        state = self._read()
        if state is not None:
            return state
        first = initial_slot(now)
        if first is None:
            raise ValueError("gap_schedule_time_invalid")
        state = {
            "schema_version": 1,
            "timezone": TIMEZONE,
            "local_times": list(LOCAL_TIMES),
            "jobs": {name: {"next_due": first, "last_attempt_slot": None,
                            "last_attempt_at": None, "last_attempt_phase": None}
                     for name in sorted(SCAN_NAMES)},
        }
        self._write(state)
        return state

    @staticmethod
    def _clock(value):
        clock = _now(value)
        if clock is None:
            raise ValueError("gap_schedule_time_invalid")
        return clock

    @staticmethod
    def _name(name):
        if not is_gap_scan(name):
            raise ValueError("gap_schedule_unknown_scan")
        return name

    def next_due(self, name, now=None):
        clock, name = self._clock(now), self._name(name)
        with self._guard():
            return self._state(clock)["jobs"][name]["next_due"]

    def pending_slot(self, name, now=None):
        clock, name = self._clock(now), self._name(name)
        with self._guard():
            return due_slot(self._state(clock)["jobs"][name]["next_due"], clock)

    def claim(self, name, slot, now=None):
        clock, name = self._clock(now), self._name(name)
        parsed = _utc(slot)
        if parsed is None or slot_key(parsed) is None:
            return False
        epoch = parsed.timestamp()
        with self._guard():
            state = self._state(clock)
            if due_slot(state["jobs"][name]["next_due"], clock) != epoch:
                return False
            updated = copy.deepcopy(state)
            updated["jobs"][name] = {
                "next_due": next_slot(epoch),
                "last_attempt_slot": epoch,
                "last_attempt_at": clock.timestamp(),
                "last_attempt_phase": "reserved",
            }
            self._write(updated)
            return True

    def unclaim(self, name, slot, now=None):
        clock, name = self._clock(now), self._name(name)
        parsed = _utc(slot)
        if parsed is None or slot_key(parsed) is None:
            return False
        epoch = parsed.timestamp()
        with self._guard():
            state = self._state(clock)
            job = state["jobs"][name]
            if (job["last_attempt_slot"] != epoch or job["last_attempt_phase"] != "reserved"
                    or job["next_due"] != next_slot(epoch)):
                return False
            updated = copy.deepcopy(state)
            updated["jobs"][name]["next_due"] = epoch
            updated["jobs"][name]["last_attempt_phase"] = "released"
            self._write(updated)
            return True

    def snapshot(self, now=None):
        with self._guard():
            return copy.deepcopy(self._state(self._clock(now)))
