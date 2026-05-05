"""Shared trade-level normalization and geometry validation.

This module is intentionally dependency-free so API, background service and
scanner code can use the same Entry/Stop/TP math without drifting apart.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple


ENTRY_KEYS = ("Entry", "entry", "entry_price", "trigger_entry")
STOP_KEYS = ("StopLoss", "stop_loss", "Stop", "stop", "SL", "invalidation_stop")
TP1_KEYS = ("TP1", "tp1", "target1", "Target1", "target", "Target", "tp1_target")
TP2_KEYS = ("TP2", "tp2", "target2", "Target2", "tp2_target")


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return val


def _nested_sources(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = [row]
    for nested_key in ("trade_setup", "setup", "signal"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
            pump = nested.get("pump_data")
            if isinstance(pump, dict):
                sources.append(pump)
    return sources


def first_trade_level(row: Dict[str, Any], keys: Iterable[str]) -> Tuple[Optional[float], Optional[str]]:
    for source in _nested_sources(row):
        for key in keys:
            value = safe_float(source.get(key), None)
            if value is not None and value > 0:
                return value, key
    return None, None


def infer_trade_direction(row: Dict[str, Any]) -> Optional[str]:
    setup = row.get("trade_setup", {}) if isinstance(row.get("trade_setup"), dict) else {}
    text = " ".join(str(value or "") for value in (
        row.get("Signal_Direction"),
        row.get("BI_Direction"),
        row.get("direction"),
        row.get("_direction"),
        row.get("side"),
        row.get("trade_action"),
        setup.get("direction"),
        setup.get("trade_action"),
    )).upper()
    if "SHORT" in text or text == "SELL":
        return "SHORT"
    if "LONG" in text or "BUY" in text:
        return "LONG"
    return None


def trade_geometry(
    entry: Optional[float],
    stop: Optional[float],
    tp1: Optional[float],
    tp2: Optional[float],
    direction: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate signed trade geometry and calculate blended R:R.

    LONG: stop < entry < tp1 <= tp2
    SHORT: stop > entry > tp1 >= tp2
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not entry or not stop or not tp1 or not tp2:
        missing = []
        if not entry:
            missing.append("entry")
        if not stop:
            missing.append("stop")
        if not tp1:
            missing.append("tp1")
        if not tp2:
            missing.append("tp2")
        return {"valid": False, "rr": None, "risk": None, "direction": direction, "errors": [f"missing_{','.join(missing)}"], "warnings": warnings}

    if direction not in ("LONG", "SHORT"):
        if stop < entry:
            direction = "LONG"
        elif stop > entry:
            direction = "SHORT"
        else:
            errors.append("stop_equals_entry")

    risk = None
    reward1 = None
    reward2 = None

    if direction == "LONG":
        if stop >= entry:
            errors.append("invalid_long_stop")
        if tp1 <= entry:
            errors.append("invalid_long_tp1")
        if tp2 <= entry:
            errors.append("invalid_long_tp2")
        if tp2 <= tp1:
            warnings.append("tp2_not_above_tp1")
        risk = entry - stop
        reward1 = tp1 - entry
        reward2 = tp2 - entry
    elif direction == "SHORT":
        if stop <= entry:
            errors.append("invalid_short_stop")
        if tp1 >= entry:
            errors.append("invalid_short_tp1")
        if tp2 >= entry:
            errors.append("invalid_short_tp2")
        if tp2 >= tp1:
            warnings.append("tp2_not_below_tp1")
        risk = stop - entry
        reward1 = entry - tp1
        reward2 = entry - tp2
    else:
        errors.append("missing_direction")

    if risk is None or risk <= 0:
        errors.append("invalid_risk")
    if reward1 is None or reward2 is None or reward1 <= 0 or reward2 <= 0:
        errors.append("invalid_reward")

    rr = None
    if not errors:
        rr = round((0.5 * reward1 + 0.5 * reward2) / risk, 2)

    return {
        "valid": not errors,
        "rr": rr,
        "risk": risk if risk and risk > 0 else None,
        "direction": direction,
        "errors": errors,
        "warnings": warnings,
    }


def normalize_alert_trade_levels(
    row: Dict[str, Any],
    *,
    price_fallback: Optional[float] = None,
    allow_estimated: bool = True,
) -> Dict[str, Any]:
    """Extract, estimate when needed, then validate Entry/Stop/TP levels."""
    entry, entry_key = first_trade_level(row, ENTRY_KEYS)
    stop, stop_key = first_trade_level(row, STOP_KEYS)
    tp1, tp1_key = first_trade_level(row, TP1_KEYS)
    tp2, tp2_key = first_trade_level(row, TP2_KEYS)
    direction = infer_trade_direction(row)
    estimated = False
    sources = {
        "entry": entry_key,
        "stop": stop_key,
        "tp1": tp1_key,
        "tp2": tp2_key,
    }

    if entry is None and price_fallback is not None:
        entry = safe_float(price_fallback, None)
        if entry:
            estimated = True
            sources["entry"] = "price_fallback"

    if allow_estimated and entry and not stop:
        high = safe_float(row.get("DayHigh", row.get("day_high", row.get("High24h"))), None)
        low = safe_float(row.get("DayLow", row.get("day_low", row.get("Low24h"))), None)
        day_range = (high - low) if high and low and high > low else 0
        risk = max(entry * 0.03, day_range * 0.45 if day_range > 0 else 0)
        risk = min(risk, entry * 0.12)
        if direction == "SHORT" and risk > 0:
            stop = entry + risk
            estimated = True
            sources["stop"] = "estimated_day_range"
        elif direction == "LONG" and risk > 0:
            stop = max(0.00000001, entry - risk)
            estimated = True
            sources["stop"] = "estimated_day_range"

    if allow_estimated and entry and stop and (not tp1 or not tp2):
        if direction not in ("LONG", "SHORT"):
            direction = "LONG" if stop < entry else "SHORT" if stop > entry else direction
        if direction == "LONG" and stop < entry:
            risk = entry - stop
            if not tp1:
                tp1 = entry + risk * 1.5
                sources["tp1"] = "estimated_r_multiple"
                estimated = True
            if not tp2:
                tp2 = entry + risk * 2.5
                sources["tp2"] = "estimated_r_multiple"
                estimated = True
        elif direction == "SHORT" and stop > entry:
            risk = stop - entry
            if not tp1:
                tp1 = max(0.00000001, entry - risk * 1.5)
                sources["tp1"] = "estimated_r_multiple"
                estimated = True
            if not tp2:
                tp2 = max(0.00000001, entry - risk * 2.5)
                sources["tp2"] = "estimated_r_multiple"
                estimated = True

    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    native = all(sources.get(key) and not str(sources[key]).startswith(("estimated", "price_fallback")) for key in ("entry", "stop", "tp1", "tp2"))
    source_label = "native" if native else "estimated" if estimated else "incomplete"

    return {
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "rr": geometry["rr"],
        "risk": geometry["risk"],
        "direction": geometry["direction"],
        "valid": geometry["valid"],
        "errors": geometry["errors"],
        "warnings": geometry["warnings"],
        "estimated": estimated,
        "native": native,
        "source": source_label,
        "sources": sources,
    }
