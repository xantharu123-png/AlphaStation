"""Shared trade-level normalization and geometry validation.

This module is intentionally dependency-free so API, background service and
scanner code can use the same Entry/Stop/TP math without drifting apart.
"""

from __future__ import annotations

import math
import re
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


def minimum_stop_distance(
    entry: Any,
    *,
    atr: Any = None,
    spread_pct: Any = None,
    trade_horizon: Any = None,
    scanner_name: Any = None,
    asset_class: Any = None,
) -> Dict[str, Any]:
    """Return one shared market-noise floor for stop validation.

    The floor is not a target generator. It only prevents a technically valid
    structure stop from being placed inside ordinary price/ATR/spread noise.
    Scanner construction and later trade-health checks must use this same
    function so that a plan cannot pass under two different definitions.
    """
    price = safe_float(entry, None)
    if price is None or price <= 0:
        return {
            "distance": None,
            "profile": "invalid",
            "price_floor": None,
            "atr_floor": None,
            "spread_floor": None,
            "components": {
                "price_floor": None,
                "atr_floor": None,
                "spread_floor": None,
            },
        }

    context = " ".join(
        str(value or "").strip().lower()
        for value in (trade_horizon, scanner_name, asset_class)
    )
    is_crypto = "crypto" in context or any(
        token in context
        for token in ("early_mover", "new_listing", "btc_diverg", "explosion")
    )
    if any(token in context for token in ("intraday", "daytrade", "scalp", "orb")):
        profile = "intraday"
        price_pct = 0.006 if is_crypto else 0.004
        atr_multiple = 0.35
    elif any(token in context for token in ("position", "weekly", "turtle", "long_term")):
        profile = "position"
        price_pct = 0.018 if is_crypto else 0.020
        atr_multiple = 0.60
    else:
        profile = "swing"
        price_pct = 0.012 if is_crypto else 0.015
        atr_multiple = 0.45

    atr_value = max(0.0, safe_float(atr, 0.0) or 0.0)
    spread_value = max(0.0, safe_float(spread_pct, 0.0) or 0.0)
    price_floor = price * price_pct
    atr_floor = atr_value * atr_multiple
    # A round trip through a quoted spread needs room beyond one spread.
    spread_floor = price * spread_value / 100.0 * 1.5
    distance = max(price_floor, atr_floor, spread_floor)
    return {
        "distance": distance,
        "profile": profile,
        "price_floor": price_floor,
        "atr_floor": atr_floor,
        "spread_floor": spread_floor,
        "components": {
            "price_floor": price_floor,
            "atr_floor": atr_floor,
            "spread_floor": spread_floor,
        },
        "price_pct": price_pct * 100.0,
        "atr_multiple": atr_multiple,
    }


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
    if "SHORT" in text or re.search(r"\bSELL\b", text):
        return "SHORT"
    if "LONG" in text or re.search(r"\bBUY\b", text):
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

    LONG: stop < entry < tp1 < tp2
    SHORT: stop > entry > tp1 > tp2
    """
    entry = safe_float(entry, None)
    stop = safe_float(stop, None)
    tp1 = safe_float(tp1, None)
    tp2 = safe_float(tp2, None)
    direction = str(direction or "").strip().upper() or None

    errors: List[str] = []
    warnings: List[str] = []

    if entry is None or stop is None or tp1 is None or tp2 is None:
        missing = []
        if entry is None:
            missing.append("entry")
        if stop is None:
            missing.append("stop")
        if tp1 is None:
            missing.append("tp1")
        if tp2 is None:
            missing.append("tp2")
        return {"valid": False, "rr": None, "risk": None, "direction": direction, "errors": [f"missing_{','.join(missing)}"], "warnings": warnings}

    non_positive = [
        name for name, value in (("entry", entry), ("stop", stop), ("tp1", tp1), ("tp2", tp2))
        if value <= 0
    ]
    if non_positive:
        return {
            "valid": False,
            "rr": None,
            "risk": None,
            "direction": direction,
            "errors": [f"non_positive_{','.join(non_positive)}"],
            "warnings": warnings,
        }

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
            errors.append("tp2_not_above_tp1")
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
            errors.append("tp2_not_below_tp1")
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
    rr_tp1 = None
    rr_tp2 = None
    if not errors:
        rr_tp1 = round(reward1 / risk, 2)
        rr_tp2 = round(reward2 / risk, 2)
        rr = round((0.5 * reward1 + 0.5 * reward2) / risk, 2)

    return {
        "valid": not errors,
        "rr": rr,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "reward1": reward1 if not errors else None,
        "reward2": reward2 if not errors else None,
        "risk": risk if risk and risk > 0 else None,
        "direction": direction,
        "errors": errors,
        "warnings": warnings,
    }


def trade_plan_quality(
    levels: Dict[str, Any],
    *,
    min_primary_tp_rr: float = 1.5,
    min_tp_gap_r: float = 0.5,
    min_tp_gap_pct: float = 0.006,
    runner_rr_cap: float = 5.0,
    max_runner_to_tp1_ratio: float = 3.5,
) -> Dict[str, Any]:
    """Validate target usefulness without letting a distant TP2 hide a weak TP1.

    ``trade_geometry`` answers whether the levels are ordered correctly. This
    function answers whether the ordered targets form a usable trade plan.
    API and background mail workers intentionally share this implementation.
    """
    rr = safe_float(levels.get("rr"), None)
    rr_tp1 = safe_float(levels.get("rr_tp1"), None)
    rr_tp2 = safe_float(levels.get("rr_tp2"), None)
    entry = safe_float(levels.get("entry"), None)
    risk = safe_float(levels.get("risk"), None)
    reward1 = safe_float(levels.get("reward1"), None)
    reward2 = safe_float(levels.get("reward2"), None)
    errors = {str(item) for item in (levels.get("errors") or [])}
    issues: List[str] = []

    if rr_tp1 is None or rr_tp2 is None:
        return {
            "effective_rr": rr,
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "runner_skew": False,
            "tp1_ok": False,
            "issues": ["missing_target_rr"],
        }

    if rr_tp1 < min_primary_tp_rr:
        issues.append("tp1_rr_below_primary_threshold")
    if errors.intersection({"tp2_not_above_tp1", "tp2_not_below_tp1"}):
        issues.append("tp2_not_beyond_tp1")

    if entry and risk and reward1 is not None and reward2 is not None:
        min_tp_gap = max(risk * min_tp_gap_r, entry * min_tp_gap_pct)
        if reward2 <= reward1:
            issues.append("tp2_not_beyond_tp1")
        elif (reward2 - reward1) < min_tp_gap:
            issues.append("targets_too_close")

    if rr_tp2 < max(2.0, rr_tp1 + min_tp_gap_r):
        issues.append("targets_too_close")
    if rr_tp2 > runner_rr_cap and rr_tp1 < 2.0:
        issues.append("runner_rr_overdominates_tp1")
    if rr_tp1 > 0 and rr_tp2 / rr_tp1 > max_runner_to_tp1_ratio and rr_tp1 < 2.0:
        issues.append("runner_rr_overdominates_tp1")

    capped_tp2 = min(rr_tp2, runner_rr_cap)
    effective_rr = round((rr_tp1 + capped_tp2) / 2.0, 2)
    runner_skew = rr_tp2 > runner_rr_cap and rr_tp2 >= max(rr_tp1 * 2.25, rr_tp1 + 4.0)
    return {
        "effective_rr": effective_rr,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "runner_skew": runner_skew,
        "tp1_ok": rr_tp1 >= min_primary_tp_rr,
        "issues": list(dict.fromkeys(issues)),
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

    # AUDIT M-4 (Biotech, 10.06.2026): synthetisch erzeugte Level — z.B. baut
    # _enrich_biotech_alert_trade_levels Entry/Stop/TP aus ATR/Support-Struktur
    # und schreibt sie als explizite Row-Felder — gingen bisher als native
    # durch (estimated-Sperre des Plan-Guards wirkungslos). Drittes, EHRLICHES
    # Flag: synthetic. Bewusst minimal-invasiv: native bleibt True und
    # estimated bleibt False, damit das bestehende estimated-Mail-Gate
    # Biotech-Mails weiter zulaesst (dokumentierte Ausnahme: Struktur-Level
    # sind handelbar, aber via M1-Label + synthetic-Flag gekennzeichnet).
    synthetic = bool(row.get("Trade_Setup_Synthetic") or row.get("trade_setup_synthetic"))
    if not synthetic:
        _setup_src = str(row.get("Trade_Setup_Source", row.get("trade_setup_source", "")) or "")
        synthetic = _setup_src.startswith("biotech_daily")

    return {
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "rr": geometry["rr"],
        "rr_tp1": geometry.get("rr_tp1"),
        "rr_tp2": geometry.get("rr_tp2"),
        "reward1": geometry.get("reward1"),
        "reward2": geometry.get("reward2"),
        "risk": geometry["risk"],
        "direction": geometry["direction"],
        "valid": geometry["valid"],
        "errors": geometry["errors"],
        "warnings": geometry["warnings"],
        "estimated": estimated,
        "native": native,
        "synthetic": synthetic,
        "source": source_label,
        "sources": sources,
    }
