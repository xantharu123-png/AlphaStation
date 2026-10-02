"""Pure, session-bound daily reference metrics for the Bear stock producer."""
from datetime import datetime
import math

from modules.stock_bars import completed_polygon_bars
from modules.stock_swing_contract import NY, session_close
from modules.volume_metrics import historical_volume_baseline


def bear_reference_metrics(raw_bars, *, signal_session, as_of):
    """Use completed bars strictly before the observation's explicit session.

    Provider order is irrelevant. A missing current-session aggregate must not
    remove yesterday from the historical baseline. Invalid or conflicting bars
    are a data error, not an invitation to manufacture OHLC values or dates.
    The caller owns the price/session observation and its error isolation.
    """
    if (not isinstance(as_of, datetime) or as_of.tzinfo is None
            or not isinstance(signal_session, str)):
        raise ValueError("bear_history_time_context_invalid")
    try:
        signal_date = datetime.strptime(signal_session, "%Y-%m-%d").date()
        if signal_date.isoformat() != signal_session or session_close(signal_session) is None:
            raise ValueError("invalid session")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("bear_history_signal_session_invalid") from None
    if signal_date > as_of.astimezone(NY).date():
        raise ValueError("bear_history_signal_session_future")
    if not isinstance(raw_bars, (list, tuple)):
        raise ValueError("bear_history_payload_invalid")

    prepared, observations = [], {}
    for bar in raw_bars:
        if not isinstance(bar, dict):
            raise ValueError("bear_history_bar_invalid")
        # Determine relevance from a real timestamp before inspecting OHLCV.
        # An unfinished/future session cannot enter either reference metrics
        # or the structural prefix, even if its provisional prices are bad.
        try:
            raw_timestamp = bar.get("t")
            if isinstance(raw_timestamp, bool):
                raise ValueError("invalid timestamp")
            timestamp = float(raw_timestamp)
            if not math.isfinite(timestamp) or timestamp <= 0:
                raise ValueError("invalid timestamp")
            day = datetime.fromtimestamp(timestamp / 1000.0, NY).date().isoformat()
            closed_at = session_close(day)
            if closed_at is None:
                raise ValueError("non-session")
        except (TypeError, ValueError, OverflowError, OSError):
            raise ValueError("bear_history_bar_invalid") from None
        if day > signal_session or closed_at > as_of:
            continue
        values = {}
        for key in ("t", "o", "h", "l", "c", "v"):
            raw = bar.get(key)
            if isinstance(raw, bool):
                raise ValueError("bear_history_bar_invalid")
            try:
                values[key] = float(raw)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("bear_history_bar_invalid") from None
        if (any(not math.isfinite(value) for value in values.values())
                or min(values[key] for key in ("t", "o", "h", "l", "c")) <= 0
                or values["v"] < 0 or values["l"] > min(values["o"], values["c"])
                or values["h"] < max(values["o"], values["c"])):
            raise ValueError("bear_history_bar_invalid")
        observation = tuple(values[key] for key in ("t", "o", "h", "l", "c", "v")) + tuple(
            bar.get(key) for key in ("complete", "is_closed", "closed"))
        if day in observations:
            if observations[day] != observation:
                raise ValueError("bear_history_conflicting_session")
            continue
        observations[day] = observation
        prepared.append(dict(bar, **values))

    # Downstream structural consumers get only the observation's prefix, too.
    # A later as_of may certify completion but cannot append future sessions to
    # an older snapshot's support/resistance calculation.
    completed = [bar for bar in completed_polygon_bars(prepared, as_of=as_of)
                 if bar["date"] <= signal_session]
    prior = [bar for bar in completed if bar["date"] < signal_session]
    if len(prior) < 20:
        raise ValueError("bear_history_insufficient_completed_reference")
    last20 = prior[-20:]
    return {
        "signal_session": signal_session,
        "completed_bars": completed,
        "prior_bars": prior,
        "reference_count": len(prior),
        "latest_reference_session": prior[-1]["date"],
        "ma20": sum(bar["c"] for bar in last20) / 20.0,
        "ma50": sum(bar["c"] for bar in prior[-50:]) / 50.0 if len(prior) >= 50 else None,
        "avg_volume20": historical_volume_baseline(
            (bar["v"] for bar in last20), lookback=20, minimum_periods=10),
        "low_20d": min(bar["l"] for bar in last20),
        "low_60d": min(bar["l"] for bar in prior[-60:]) if len(prior) >= 60 else None,
    }
