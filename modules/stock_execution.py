"""Shared stock swing execution checks used by API and background mailers."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional


def _finite_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _median(values: List[Any], default: float = 0.0) -> float:
    numbers = sorted(
        value for value in (_finite_float(item) for item in values) if value is not None
    )
    if not numbers:
        return default
    middle = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[middle]
    return (numbers[middle - 1] + numbers[middle]) / 2.0


def aggregate_regular_session_4h_bars(
    raw_bars: List[Dict[str, Any]],
    timezone_et: Any,
    *,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """Aggregate Polygon 30-minute bars into regular-session execution bars.

    The US cash session is 6.5 hours, so it is represented as one full 4-hour
    bar (09:30-13:30 ET) and one 2.5-hour closing bar. The closing bar is still
    relevant for execution because it captures late-session rejection risk.
    """
    buckets: Dict[Any, List[Dict[str, Any]]] = {}
    for bar in raw_bars or []:
        if not isinstance(bar, dict):
            continue
        timestamp_ms = _finite_float(bar.get("t"))
        if timestamp_ms is None:
            continue
        local_dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone_et)
        minutes = local_dt.hour * 60 + local_dt.minute
        if local_dt.weekday() >= 5 or minutes < 570 or minutes >= 960:
            continue
        bucket_index = 0 if minutes < 810 else 1
        buckets.setdefault((local_dt.date(), bucket_index), []).append(bar)

    aggregated: List[Dict[str, Any]] = []
    for (_session_date, bucket_index), chunk in sorted(buckets.items(), key=lambda item: item[0]):
        chunk = sorted(chunk, key=lambda item: _finite_float(item.get("t"), 0.0) or 0.0)
        if not chunk:
            continue
        open_price = _finite_float(chunk[0].get("o"))
        close = _finite_float(chunk[-1].get("c"))
        highs = [_finite_float(item.get("h")) for item in chunk]
        lows = [_finite_float(item.get("l")) for item in chunk]
        if open_price is None or close is None or any(value is None for value in highs + lows):
            continue
        high = max(float(value) for value in highs if value is not None)
        low = min(float(value) for value in lows if value is not None)
        if open_price <= 0 or close <= 0 or high < max(open_price, close) or low > min(open_price, close):
            continue
        expected_count = 8 if bucket_index == 0 else 5
        aggregated.append({
            "timestamp": chunk[0].get("t"),
            "open": float(open_price),
            "high": high,
            "low": low,
            "close": float(close),
            "volume": sum(_finite_float(item.get("v"), 0.0) or 0.0 for item in chunk),
            "source_bar_count": len(chunk),
            "partial_source_bar": len(chunk) < expected_count,
        })
    return aggregated[-max(1, int(limit or 24)):]


def stock_swing_4h_execution_state(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify whether a new long must wait after a violent 4H rejection.

    A broad setup score measures opportunity; this function measures current
    execution timing. Ordinary red pullbacks remain clear. A rejection needs a
    weak close plus statistically expanded range or volume, or an exceptionally
    large bearish body. An unreclaimed rejection blocks a fresh long entry.
    """
    base_state: Dict[str, Any] = {
        "Swing_4H_Execution_Checked": True,
        "Swing_4H_Execution_Status": "CLEAR",
        "Swing_4H_Execution_Reason": "no_material_4h_rejection",
    }
    cleaned: List[Dict[str, Any]] = []
    for raw in bars or []:
        if not isinstance(raw, dict):
            continue
        open_price = _finite_float(raw.get("open", raw.get("o")))
        high = _finite_float(raw.get("high", raw.get("h")))
        low = _finite_float(raw.get("low", raw.get("l")))
        close = _finite_float(raw.get("close", raw.get("c")))
        volume = _finite_float(raw.get("volume", raw.get("v")), 0.0) or 0.0
        if None in (open_price, high, low, close):
            continue
        if open_price <= 0 or close <= 0 or high < max(open_price, close) or low > min(open_price, close):
            continue
        cleaned.append({
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
            "timestamp": raw.get("timestamp", raw.get("time", raw.get("t"))),
        })

    if len(cleaned) < 8:
        return {
            **base_state,
            "Swing_4H_Execution_Status": "DATA_UNAVAILABLE",
            "Swing_4H_Execution_Reason": "insufficient_4h_history",
        }

    rejection: Optional[Dict[str, Any]] = None
    first_candidate = max(6, len(cleaned) - 3)
    for index in range(first_candidate, len(cleaned)):
        candle = cleaned[index]
        prior = cleaned[max(0, index - 12):index]
        if len(prior) < 6:
            continue
        prior_ranges = []
        for position, item in enumerate(prior):
            previous_close = prior[position - 1]["close"] if position else item["open"]
            prior_ranges.append(max(
                item["high"] - item["low"],
                abs(item["high"] - previous_close),
                abs(item["low"] - previous_close),
            ))
        median_range = _median(prior_ranges)
        median_volume = _median([item["volume"] for item in prior if item["volume"] > 0])
        previous_close = prior[-1]["close"]
        true_range = max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        )
        candle_range = max(candle["high"] - candle["low"], 1e-9)
        body_change_pct = ((candle["close"] - candle["open"]) / candle["open"]) * 100.0
        close_position = (candle["close"] - candle["low"]) / candle_range
        high_rejection_pct = ((candle["high"] - candle["close"]) / candle["high"]) * 100.0
        range_ratio = true_range / median_range if median_range > 0 else 0.0
        volume_ratio = candle["volume"] / median_volume if median_volume > 0 and candle["volume"] > 0 else 0.0
        prior_resistance = max(item["high"] for item in prior[-8:])
        failed_breakout = (
            candle["high"] >= prior_resistance * 1.003
            and candle["close"] <= prior_resistance * 0.995
        )
        expanded = range_ratio >= 1.5 or volume_ratio >= 1.8
        severe_rejection = (
            body_change_pct <= -2.0
            and close_position <= 0.32
            and high_rejection_pct >= 2.0
            and expanded
        ) or (
            body_change_pct <= -4.0
            and close_position <= 0.40
            and range_ratio >= 1.25
        )
        if not severe_rejection:
            continue
        midpoint = (candle["high"] + candle["low"]) / 2.0
        rejection = {
            "index": index,
            "body_change_pct": body_change_pct,
            "close_position": close_position,
            "range_ratio": range_ratio,
            "volume_ratio": volume_ratio,
            "failed_breakout": failed_breakout,
            "reclaim_level": max(midpoint, prior_resistance if failed_breakout else midpoint),
            "rejection_low": candle["low"],
        }

    if rejection is None:
        return base_state

    reclaim_level = float(rejection["reclaim_level"])
    state = {
        **base_state,
        "Swing_4H_Rejection_Change_Pct": round(float(rejection["body_change_pct"]), 2),
        "Swing_4H_Rejection_Close_Pos": round(float(rejection["close_position"]), 3),
        "Swing_4H_Rejection_Range_Ratio": round(float(rejection["range_ratio"]), 2),
        "Swing_4H_Rejection_Volume_Ratio": round(float(rejection["volume_ratio"]), 2),
        "Swing_4H_Reclaim_Level": round(reclaim_level, 6),
        "Swing_4H_Rejection_Low": round(float(rejection["rejection_low"]), 6),
        "Swing_4H_Rejection_Bars_Ago": len(cleaned) - 1 - int(rejection["index"]),
        "Swing_4H_Failed_Breakout": bool(rejection["failed_breakout"]),
    }
    if cleaned[-1]["close"] < reclaim_level * 0.997:
        state["Swing_4H_Execution_Status"] = "WAIT_RECLAIM"
        state["Swing_4H_Execution_Reason"] = (
            "failed_4h_breakout_not_reclaimed"
            if rejection["failed_breakout"]
            else "bearish_4h_rejection_not_reclaimed"
        )
    else:
        state["Swing_4H_Execution_Status"] = "RECLAIMED"
        state["Swing_4H_Execution_Reason"] = "4h_rejection_reclaimed"
    return state
