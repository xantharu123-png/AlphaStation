"""Starter stock swing plans: completed daily evidence, never an execution quote.

This contract is separate from the live/intraday contract. The reference close
is not a fill; activation and price-path evaluation start after mail acceptance.
"""
from datetime import date, datetime, timedelta, timezone
import math
import os
from zoneinfo import ZoneInfo

VERSION = 1
MODE = "completed_daily_swing"
SOURCE = "polygon_completed_1d_swing"
DELAY_SECONDS = 15 * 60
NY = ZoneInfo("America/New_York")


def enabled():
    return os.environ.get("STOCK_SWING_DATA_MODE", "starter_swing").lower() == "starter_swing"


def session_close(session):
    # Reuse the app's exchange calendar (holidays, exceptional closes, DST).
    # Lazy import avoids a circular dependency with tracker consumers.
    from modules.signal_tracker import _us_equity_session_close
    return _us_equity_session_close(date.fromisoformat(str(session)[:10]))


def completed_sessions(as_of=None, count=2):
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("swing_as_of_requires_timezone")
    available = now - timedelta(seconds=DELAY_SECONDS)
    day = available.astimezone(NY).date()
    sessions = []
    for _ in range(40):
        close = session_close(day.isoformat())
        if close is not None and close <= available:
            sessions.append(day.isoformat())
            if len(sessions) == count:
                return sessions
        day -= timedelta(days=1)
    raise ValueError("swing_calendar_unavailable")


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def metadata(session, close):
    timestamp = session_close(session)
    price = number(close)
    if timestamp is None or price is None or price <= 0:
        raise ValueError("invalid_swing_reference")
    observed = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "stock_swing_contract_version": VERSION,
        "stock_swing_mode": MODE,
        "swing_analysis_session": str(session)[:10],
        "swing_reference_close": price,
        "swing_data_delay_seconds": DELAY_SECONDS,
        "swing_timeframe": "1D",
        "trade_horizon": "swing",
        "scan_price_observed_at": observed,
        "scan_price_source": SOURCE,
        "price_observed_at": observed,
        "price_source": SOURCE,
        "price_mode": "swing_reference_close",
        "price_session": "COMPLETED_US_SESSION",
        "fill_evidence_verified": False,
        "entry_quality": "SWING_PLAN",
    }


def is_swing(row):
    return isinstance(row, dict) and row.get("stock_swing_mode") == MODE


def validate(row, as_of=None):
    """Exact source/session validation; weekend age is not a 90-second failure."""
    if not is_swing(row) or type(row.get("stock_swing_contract_version")) is not int:
        return False
    if row["stock_swing_contract_version"] != VERSION:
        return False
    try:
        session = completed_sessions(as_of, 1)[0]
        expected = metadata(session, row.get("swing_reference_close"))
    except (ValueError, TypeError, OverflowError):
        return False
    return all(row.get(key) == expected[key] for key in (
        "swing_analysis_session", "scan_price_observed_at", "scan_price_source",
        "swing_timeframe", "trade_horizon", "swing_data_delay_seconds",
    )) and row.get("fill_evidence_verified") is False


def parse_grouped(payload, session):
    """Validate a dated bulk OHLC response; never invent trades or timestamps."""
    if (not isinstance(payload, dict) or payload.get("status") not in {"OK", "DELAYED"}
            or payload.get("adjusted") is not True
            or not isinstance(payload.get("results"), list) or not payload["results"]):
        raise ValueError("swing_daily_feed_unavailable")
    result = {}
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            raise ValueError("swing_daily_feed_invalid")
        ticker = raw.get("T")
        values = {key: number(raw.get(key)) for key in ("o", "h", "l", "c", "v", "t")}
        if (not isinstance(ticker, str) or not ticker or ticker in result
                or any(value is None for value in values.values())):
            raise ValueError("swing_daily_feed_invalid")
        if values["v"] == 0:
            continue  # No trading observation, not a zero-price signal.
        if (min(values[key] for key in ("o", "h", "l", "c", "t")) <= 0
                or values["v"] < 0 or values["l"] > min(values["o"], values["c"])
                or values["h"] < max(values["o"], values["c"])):
            raise ValueError("swing_daily_feed_invalid")
        try:
            observed_day = datetime.fromtimestamp(values["t"] / 1000, timezone.utc).astimezone(NY).date().isoformat()
        except (ValueError, OverflowError, OSError):
            raise ValueError("swing_daily_feed_invalid") from None
        if observed_day != session:
            raise ValueError("swing_daily_feed_wrong_session")
        result[ticker] = dict(raw, date=session)
    if not result:
        raise ValueError("swing_daily_feed_empty")
    return result


def universe(current, previous, session):
    return [dict(ticker=ticker, day=bar, prevDay=previous.get(ticker, {}),
                 _sources=["completed_daily"], **metadata(session, bar["c"]))
            for ticker, bar in current.items()]


def delayed_market_watermark(as_of):
    """Latest complete regular-session minute available with Starter delay."""
    available = (as_of - timedelta(seconds=DELAY_SECONDS)).replace(second=0, microsecond=0)
    day = available.astimezone(NY).date()
    for _ in range(40):
        close = session_close(day.isoformat())
        opening = datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(hour=9, minute=30)
        if close is not None and available >= opening + timedelta(minutes=1):
            return min(available, close).astimezone(timezone.utc)
        day -= timedelta(days=1)
    raise ValueError("swing_calendar_unavailable")


def validate_minute_path(payload, *, reference_at, available_at, stop, tp1, direction):
    """No stop/target breach in the observable path; missing intervals block.

    The last 15 minutes are explicitly unobserved, never declared safe. This
    is a delayed plan check, not an immediate-entry or execution guarantee.
    """
    if (not isinstance(payload, dict) or payload.get("status") not in {"OK", "DELAYED"}
            or not isinstance(payload.get("results"), list)):
        raise ValueError("swing_delayed_path_unavailable")
    reference = reference_at.astimezone(timezone.utc)
    available = available_at.astimezone(timezone.utc)
    if not reference < available or available - reference > timedelta(days=14):
        raise ValueError("swing_delayed_path_bounds_invalid")
    closes = {}
    for bar in payload["results"]:
        if not isinstance(bar, dict):
            raise ValueError("swing_delayed_bar_invalid")
        values = {key: number(bar.get(key)) for key in ("t", "o", "h", "l", "c")}
        if any(value is None or value <= 0 for value in values.values()):
            raise ValueError("swing_delayed_bar_invalid")
        try:
            start = datetime.fromtimestamp(values["t"]/1000, timezone.utc)
        except (ValueError, OSError, OverflowError):
            raise ValueError("swing_delayed_bar_invalid") from None
        closed = start + timedelta(minutes=1)
        if closed <= reference or closed > available:
            continue
        day = start.astimezone(NY).date()
        close = session_close(day.isoformat())
        opening = datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(hour=9, minute=30)
        if close is None or start < opening or closed > close:
            continue
        if (start.second or start.microsecond or closed in closes
                or values["l"] > min(values["o"], values["c"])
                or values["h"] < max(values["o"], values["c"])):
            raise ValueError("swing_delayed_bar_invalid")
        if (direction == "LONG" and (values["l"] <= stop or values["h"] >= tp1)
                or direction == "SHORT" and (values["h"] >= stop or values["l"] <= tp1)):
            raise ValueError("swing_delayed_path_stop_or_target_touched")
        closes[closed] = values["c"]
    day = reference.astimezone(NY).date()
    while day <= available.astimezone(NY).date():
        close = session_close(day.isoformat())
        if close is not None:
            first = datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(hour=9, minute=31)
            cursor = max(first.astimezone(timezone.utc), reference + timedelta(minutes=1))
            end = min(close.astimezone(timezone.utc), available)
            while cursor <= end:
                if cursor not in closes:
                    raise ValueError("swing_delayed_path_incomplete")
                cursor += timedelta(minutes=1)
        day += timedelta(days=1)
    if available not in closes:
        raise ValueError("swing_delayed_price_missing")
    return closes[available]
