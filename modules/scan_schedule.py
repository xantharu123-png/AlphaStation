"""Calendar policy for automatic US-stock discovery, never manual execution.

Call this at automatic dispatch, not inside scanner implementations: an operator
can still request a weekend scan. Existing workers, position management, crypto,
risk context and delivery workers are deliberately outside this policy. This is
a weekend rule, not a market-open or exchange-holiday calendar.
"""
from datetime import datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo


TIMEZONE = "America/New_York"
_MARKET_ZONE = ZoneInfo(TIMEZONE)
_DATETIME_TYPE = datetime
STOCK_SCAN_NAMES = frozenset({
    "bi_long", "bi_short", "biotech", "bear", "volume_spikes", "penny_stocks",
    "orb", "turtle", "strategy_scan", "money_flow", "cup_handle_watch",
    "quote_capability",
    # Legacy BG aliases remain protected even with explicit ownership overrides.
    "strategies", "bear_scan",
})


def is_stock_scan(name):
    """Explicit ownership; crypto_strat_* and unknown jobs remain unaffected."""
    return type(name) is str and (
        name in STOCK_SCAN_NAMES or name.startswith("strat_")
    )


def _utc(value):
    """Parse aware timestamps without guessing server-local or user timezones."""
    try:
        if isinstance(value, _DATETIME_TYPE):
            parsed = value
        elif type(value) in (int, float):
            if not math.isfinite(value):
                return None
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


def _now(now_utc):
    return _utc(datetime.now(timezone.utc) if now_utc is None else now_utc)


def _market_time(value):
    try:
        return value.astimezone(_MARKET_ZONE) if value is not None else None
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def automatic_scan_allowed(name, now_utc=None):
    """Block only fresh automatic stock starts on Saturday/Sunday in New York."""
    if not is_stock_scan(name):
        return True
    local = _market_time(_now(now_utc))
    return local is not None and local.weekday() < 5


def next_allowed_at(timestamp):
    """Return a UTC epoch, rolling weekends to Monday 00:00 ET; invalid -> None.

    Calendar arithmetic, rather than adding 24-hour durations, preserves the
    Monday midnight boundary across both US daylight-saving transitions.
    """
    parsed = _utc(timestamp)
    local = _market_time(parsed)
    if local is None:
        return None
    try:
        if local.weekday() >= 5:
            monday = local.date() + timedelta(days=7 - local.weekday())
            parsed = datetime.combine(monday, time.min, _MARKET_ZONE).astimezone(timezone.utc)
        return parsed.timestamp()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def schedule_snapshot(name, next_run=None, now_utc=None):
    """Bounded public status, without mutating runtime state or inventing success.

    A future due time is retained, except stock weekends are rolled forward.
    No supplied due time on a weekday means no next-run promise. Pause here is
    calendar-only; a user's independent manual pause must be composed by callers.
    """
    stock = is_stock_scan(name)
    now = _now(now_utc)
    local = _market_time(now)
    invalid_clock = stock and local is None
    weekend = stock and local is not None and local.weekday() >= 5
    result = {
        "automatic_paused": bool(invalid_clock or weekend),
        "reason": "invalid_schedule_time" if invalid_clock else "weekend" if weekend else None,
        "timezone": TIMEZONE,
        "next_eligible_at": None,
    }
    if invalid_clock:
        return result
    due = _utc(next_run)
    if due is not None and now is not None:
        due = max(due, now)
    elif weekend:
        due = now
    if due is not None:
        next_epoch = next_allowed_at(due) if stock else due.timestamp()
        normalized = _utc(next_epoch)
        if normalized is not None:
            result["next_eligible_at"] = normalized.isoformat()
    return result
