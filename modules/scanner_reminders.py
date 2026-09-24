"""Personal, causal daily structure reminders; not trade admission or execution.

The anchor must be obtained from the server's scanner cache, never a browser's
claimed prices. All decisions below are pure and consume completed daily bars.
"""
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile

from modules import stock_swing_contract as swing

MODE = "structure_1d"


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def instant(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(value, timezone.utc)
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("reminder_timestamp_requires_timezone")
    return result.astimezone(timezone.utc)


def anchor_from_row(row, *, ticker, direction, condition, now, zone_id=None):
    """Freeze one exact zone from a trusted, latest completed scanner snapshot."""
    now = instant(now)
    snapshot = row.get("level_structure") or row.get("Level_Structure") or {}
    if (not isinstance(snapshot, dict) or snapshot.get("model") != "causal_level_zones_v1"
            or str(snapshot.get("symbol") or "").upper() != ticker
            or snapshot.get("asset_class") != "stock" or snapshot.get("horizon") != "swing"
            or int((snapshot.get("completed_bar_counts") or {}).get("1D", 0)) < 1):
        raise ValueError("server_daily_structure_missing")
    as_of = instant(snapshot.get("as_of"))
    latest_close = swing.session_close(swing.completed_sessions(now, 1)[0])
    if not latest_close <= as_of <= now:
        raise ValueError("server_daily_structure_stale")
    price = number(snapshot.get("current_price"))
    if price is None or price <= 0 or direction not in {"LONG", "SHORT"}:
        raise ValueError("server_structure_direction_or_price_invalid")
    role = "resistance" if direction == "LONG" else "support"
    candidates = []
    for zone in snapshot.get("zones") or []:
        if not isinstance(zone, dict) or role not in (zone.get("origin_roles") or []):
            continue
        if zone_id and zone.get("zone_id") != zone_id:
            continue
        lower, upper = number(zone.get("lower")), number(zone.get("upper"))
        try:
            confirmed = instant(zone.get("confirmed_at"))
        except (ValueError, TypeError, OverflowError):
            continue
        if (lower is None or upper is None or not 0 < lower <= upper
                or not zone.get("zone_id") or zone.get("projection_only") is True
                or confirmed > as_of or not zone.get("evidence")):
            continue
        boundary = upper if direction == "LONG" else lower
        crossed = price > upper if direction == "LONG" else price < lower
        # Retest monitors start with the closest already-crossed zone when one
        # exists; trigger monitors prefer a still-opposing boundary.
        preferred = crossed if condition == "retest" else not crossed
        candidates.append((not preferred, abs(price - boundary), str(zone["zone_id"]), {
            "zone_id": str(zone["zone_id"]), "lower": lower, "upper": upper,
            "direction": direction, "confirmed_at": confirmed.isoformat(),
            "snapshot_as_of": as_of.isoformat(), "timeframe": "1D",
        }))
    if not candidates:
        raise ValueError("server_structure_zone_missing")
    return min(candidates, key=lambda item: item[:3])[3]


def completed_daily_bars(raw_bars, *, now):
    """Use exchange closes including half-days; Starter delay is intentional."""
    cutoff = instant(now) - timedelta(seconds=swing.DELAY_SECONDS)
    bars = []
    seen = set()
    for raw in raw_bars or []:
        if not isinstance(raw, dict):
            raise ValueError("invalid_daily_bar")
        if raw.get("date"):
            session = str(raw["date"])[:10]
        else:
            epoch = number(raw.get("t", raw.get("timestamp", raw.get("time"))))
            if epoch is None:
                raise ValueError("daily_timestamp_missing")
            if epoch > 1e11:
                epoch /= 1000
            session = datetime.fromtimestamp(epoch, timezone.utc).astimezone(swing.NY).date().isoformat()
        closed = swing.session_close(session)
        if closed is None or closed > cutoff:
            continue
        if raw.get("is_closed") is False or raw.get("closed") is False:
            raise ValueError("daily_candle_explicitly_unclosed")
        values = {name: number(raw.get(name, raw.get(short))) for name, short in (
            ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}
        if (any(value is None for value in values.values()) or min(values[k] for k in ("open", "high", "low", "close")) <= 0
                or values["volume"] < 0 or values["low"] > min(values["open"], values["close"])
                or values["high"] < max(values["open"], values["close"]) or session in seen):
            raise ValueError("invalid_daily_bar")
        seen.add(session)
        bars.append(dict(values, closed_at=closed.astimezone(timezone.utc), session=session))
    return sorted(bars, key=lambda bar: bar["closed_at"])


def evaluate(reminder, raw_bars, *, now):
    """Break on an earlier close, later zone touch + close holding its far edge.

    A touch and the closing hold may share a daily candle (close follows its
    low/high), but a breakout and retest may not share a candle. No proximity
    test, client evidence flag, or current quote can substitute for this path.
    """
    now = instant(now)
    zone = reminder.get("zone") or {}
    direction = zone.get("direction")
    lower, upper = number(zone.get("lower")), number(zone.get("upper"))
    if direction not in {"LONG", "SHORT"} or lower is None or upper is None or not 0 < lower <= upper:
        return {"triggered": False, "reason": "reminder_zone_invalid"}
    try:
        confirmed = instant(zone["confirmed_at"])
        created = instant(reminder["created_at_epoch"])
        bars = completed_daily_bars(raw_bars, now=now)
        latest_session = swing.completed_sessions(now, 1)[0]
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return {"triggered": False, "reason": "invalid_daily_reminder_data"}
    if not bars or bars[-1]["session"] != latest_session:
        return {"triggered": False, "reason": "daily_reminder_data_not_current"}
    # A missing intervening session could hide an invalidating close. Never
    # join a historic break to a later retest across a provider coverage gap.
    first_session = max(bars[0]["session"], confirmed.astimezone(swing.NY).date().isoformat())
    day = datetime.fromisoformat(first_session).date()
    last_day = datetime.fromisoformat(latest_session).date()
    sessions = {bar["session"] for bar in bars}
    if (last_day - day).days > 400:
        return {"triggered": False, "reason": "daily_reminder_history_invalid"}
    while day <= last_day:
        name = day.isoformat()
        if swing.session_close(name) is not None and name not in sessions:
            return {"triggered": False, "reason": "daily_reminder_history_incomplete"}
        day += timedelta(days=1)
    boundary = upper if direction == "LONG" else lower
    buffer = max(boundary * 0.0001, 0.000001)
    broken_at = None
    previous = None
    pending_result = None
    for bar in bars:
        closed = bar["closed_at"]
        if closed <= confirmed:
            previous = bar
            continue
        close = bar["close"]
        beyond = close > upper + buffer if direction == "LONG" else close < lower - buffer
        opposite = close < lower - buffer if direction == "LONG" else close > upper + buffer
        if broken_at is not None and opposite:
            if closed > created:
                return {"triggered": False, "invalidated": True, "reason": "daily_structure_invalidated",
                        "candle_closed_at": closed.isoformat(), "last_close": close}
            broken_at = None
        if broken_at is None and beyond and previous is not None:
            previously_inside = previous["close"] <= upper + buffer if direction == "LONG" else previous["close"] >= lower - buffer
            if previously_inside:
                broken_at = closed
                if reminder.get("condition") == "trigger" and closed > created:
                    pending_result = pending_result or _result("daily_breakout_confirmed", bar, zone, broken_at)
        elif (broken_at is not None and closed > broken_at and closed > created
              and reminder.get("condition") == "retest" and beyond
              and bar["low"] <= upper and bar["high"] >= lower):
            pending_result = pending_result or _result("daily_retest_confirmed", bar, zone, broken_at)
        previous = bar
    if pending_result:
        return pending_result
    return {"triggered": False, "reason": "waiting_for_daily_retest" if reminder.get("condition") == "retest" else "waiting_for_daily_breakout",
            "candle_timeframe": "1D", "last_completed_at": bars[-1]["closed_at"].isoformat()}


def _result(reason, bar, zone, broken_at):
    return {"triggered": True, "reason": reason, "notification_kind": "personal_structure_update",
            "is_trade_signal": False, "price_evidence": "completed_daily_candle", "candle_timeframe": "1D",
            "candle_closed_at": bar["closed_at"].isoformat(), "break_closed_at": broken_at.isoformat(),
            "last_close": bar["close"], "zone": dict(zone), "data_delay_seconds": swing.DELAY_SECONDS}


def load_records(path, *, legacy_path=None):
    """One-time migration preserves legacy records; malformed data fails closed."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("reminder_store_symlink")
    source = path
    if not path.exists():
        if not legacy_path or not Path(legacy_path).exists():
            return []
        source = Path(legacy_path)
        if source.is_symlink() or not source.is_file():
            raise ValueError("legacy_reminder_store_unsafe")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("reminder_store_invalid")
    if source != path:
        save_records(path, raw)
    return raw


def _sync_parent_directory(path, os_api=os):
    """Persist the renamed directory entry on POSIX before acknowledging save.

    Python does not expose this directory-fsync contract on Windows; there the
    flushed file plus replace protects process-crash recovery, not a claimed
    equivalent NTFS power-loss guarantee.
    """
    if os_api.name != "posix":
        return
    descriptor = os_api.open(str(path), os_api.O_RDONLY | os_api.O_DIRECTORY)
    try:
        os_api.fsync(descriptor)
    finally:
        os_api.close(descriptor)


def save_records(path, records):
    """Atomic save, with POSIX rename durability; propagate all sync failures."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("reminder_store_symlink")
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=True, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_parent_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
