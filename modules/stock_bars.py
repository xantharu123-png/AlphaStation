"""Causal Polygon aggregate adapter for stock pattern producers (no I/O)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from modules.level_zones import normalize_completed_bars


def completed_polygon_bars(raw_bars, *, span="day", multiplier=1, as_of):
    """Only valid, uniquely determined, completed source bars.

    Daily stock aggregates are session-dated at midnight ET, not UTC dates.
    The shared exchange calendar defines holidays, early closes and DST.
    Intraday source intervals retain their actual opening timestamp and duration.
    Returned aliases support legacy pattern code and canonical level engines.
    """
    if span not in {"day", "hour", "minute"} or int(multiplier) < 1:
        raise ValueError("unsupported stock aggregate timeframe")
    if span == "day" and int(multiplier) != 1:
        raise ValueError("only single-session daily aggregates are supported")
    prepared = []
    eastern = ZoneInfo("America/New_York")
    for raw in raw_bars or ():
        try:
            opened = datetime.fromtimestamp(float(raw["t"]) / 1000, tz=timezone.utc)
            candidate = dict(raw)
            candidate["open_time"] = opened
            if span == "day":
                from modules.stock_swing_contract import session_close
                session_date = opened.astimezone(eastern).date()
                closed = session_close(session_date.isoformat())
                if closed is None:
                    continue
                candidate["close_time"] = closed
            prepared.append(candidate)
        except (TypeError, ValueError, KeyError, OverflowError, OSError):
            continue
    timeframe = f"{int(multiplier)}{ {'day': 'D', 'hour': 'H', 'minute': 'M'}[span]}"
    bars = normalize_completed_bars(prepared, timeframe=timeframe, as_of=as_of)
    return [{**bar.to_dict(), "open_time": bar.opened_at.isoformat(), "close_time": bar.closed_at.isoformat(),
             "t": int(bar.opened_at.timestamp() * 1000),
             "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close, "v": bar.volume,
             "date": bar.opened_at.astimezone(eastern).strftime("%Y-%m-%d" if span == "day" else "%Y-%m-%d %H:%M")}
            for bar in bars]
