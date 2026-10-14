"""Pure completed-Daily ticker detail indicator adapter; no market I/O."""
from datetime import date
import math

from modules.indicators import calculate_ema, calculate_macd, calculate_rsi_from_bars
from modules.volume_metrics import completed_bar_rvol, historical_volume_baseline


MODEL_VERSION = "ticker-detail-completed-daily-v1"
RECURSIVE_WARMUP_BARS = 150
LONG_EMA_HISTORY_BARS = 200
_FIELDS = ("rsi", "ema9", "ema20", "ema50", "ema100", "ema200",
           "macd", "macd_signal", "macd_histogram", "rvol", "avg_volume")


def build_ticker_detail_indicators(completed_bars):
    """Raw native values on the full causal completed-Daily history supplied.

    Caller owns exchange-calendar/as-of normalization before invoking this pure
    adapter. A partial quote/day must never be appended or substitute the last
    completed session's volume. No precision is lost before presentation.
    """
    bars = list(completed_bars or ())
    result = {key: None for key in _FIELDS}
    meta = {
        "model": MODEL_VERSION, "timeframe": "1D", "history_valid": False,
        "completed_history_bars": 0, "indicator_session": None,
        "recursive_warmup_required": RECURSIVE_WARMUP_BARS,
        "long_ema_history_required": LONG_EMA_HISTORY_BARS,
        "precision": "raw_native_until_presentation",
        "evidence_scope": "completed_daily_history_not_intraday_quote",
        "rvol_mode": "completed_session_vs_previous_20_completed",
        "available": {key: False for key in _FIELDS},
        "unavailable_reasons": {}, "status": "unavailable",
    }
    result["indicators_meta"] = meta
    try:
        if not bars:
            raise ValueError("insufficient_completed_history")
        previous = None
        for bar in bars:
            day = date.fromisoformat(str(bar["date"]))
            if previous is not None and day <= previous:
                raise ValueError("nonchronological_daily_history")
            previous = day
            if any(str(bar.get(key, "true")).strip().lower() in {"false", "0", "open", "no", "n"}
                   for key in ("is_closed", "complete", "completed", "final")):
                raise ValueError("uncompleted_daily_history")
            values = [bar[key] for key in ("open", "high", "low", "close", "volume")]
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                raise ValueError("invalid_completed_ohlcv")
            opening, high, low, close, volume = values
            if (min(opening, high, low, close) <= 0 or volume < 0
                    or not low <= min(opening, close) <= max(opening, close) <= high):
                raise ValueError("invalid_completed_ohlcv")
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        reason = str(error) if str(error) in {
            "insufficient_completed_history", "nonchronological_daily_history",
            "uncompleted_daily_history", "invalid_completed_ohlcv"} else "invalid_completed_history"
        meta["unavailable_reasons"] = {key: reason for key in _FIELDS}
        return result

    meta.update(history_valid=True, completed_history_bars=len(bars), indicator_session=previous.isoformat())
    closes = [bar["close"] for bar in bars]
    for period in (9, 20, 50, 100, 200):
        key = f"ema{period}"
        minimum = LONG_EMA_HISTORY_BARS if period >= 100 else period
        if len(bars) >= minimum:
            result[key] = calculate_ema(closes, period)
        else:
            meta["unavailable_reasons"][key] = "insufficient_long_ema_history" if period >= 100 else "insufficient_ema_history"
    if len(bars) >= RECURSIVE_WARMUP_BARS:
        result["rsi"] = calculate_rsi_from_bars(bars, raw=True)
        result["macd"], result["macd_signal"], result["macd_histogram"] = calculate_macd(bars)
    else:
        for key in ("rsi", "macd", "macd_signal", "macd_histogram"):
            meta["unavailable_reasons"][key] = "insufficient_recursive_warmup"
    baseline = historical_volume_baseline(
        [bar["volume"] for bar in bars[:-1]], lookback=20, minimum_periods=20,
    )
    if baseline is not None and bars[-1]["volume"] > 0:
        result["avg_volume"] = baseline
        result["rvol"] = completed_bar_rvol(
            bars[-1]["volume"], [bar["volume"] for bar in bars[:-1]], lookback=20, minimum_periods=20,
        )
    else:
        reason = "insufficient_completed_volume_baseline" if baseline is None else "unusable_completed_signal_volume"
        meta["unavailable_reasons"].update(rvol=reason, avg_volume=reason)
    meta["available"] = {key: result[key] is not None for key in _FIELDS}
    meta["status"] = "complete" if all(meta["available"].values()) else "partial"
    return result
