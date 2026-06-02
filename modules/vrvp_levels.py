"""
Shared VRVP/volume-profile trade level helpers.

The local volume profile is built from OHLCV bars, so it is an approximation
of TradingView's tick-based VRVP. We use it as structural confluence for
support/resistance and targets, not as a standalone signal generator.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from modules.volume_analysis import calculate_volume_profile


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return val


def round_trade_price(price: Any) -> float:
    val = _safe_float(price, 0.0) or 0.0
    aval = abs(val)
    if aval >= 100:
        return round(val, 2)
    if aval >= 10:
        return round(val, 2)
    if aval >= 1:
        return round(val, 3)
    if aval >= 0.01:
        return round(val, 5)
    return round(val, 8)


def normalize_ohlcv_bars(bars: List[Dict[str, Any]], lookback: Optional[int] = None) -> List[Dict[str, float]]:
    """Normalize mixed API bar shapes to volume-profile OHLCV input."""
    parsed: List[Dict[str, float]] = []
    source = bars[-lookback:] if lookback and lookback > 0 else bars
    for bar in source or []:
        if not isinstance(bar, dict):
            continue
        close = _safe_float(bar.get("close", bar.get("c")))
        high = _safe_float(bar.get("high", bar.get("h", close)))
        low = _safe_float(bar.get("low", bar.get("l", close)))
        open_ = _safe_float(bar.get("open", bar.get("o", close)))
        volume = _safe_float(bar.get("volume", bar.get("v", 0)), 0.0) or 0.0
        if close is None or high is None or low is None or open_ is None:
            continue
        if close <= 0 or high <= 0 or low <= 0 or high < low or volume <= 0:
            continue
        parsed.append({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return parsed


def _dedupe_levels(levels: List[Dict[str, Any]], entry: float) -> List[Dict[str, Any]]:
    """Merge almost-identical profile levels without losing the strongest source."""
    if not levels or entry <= 0:
        return []
    # Treat levels inside 0.12% as the same zone. That keeps penny crypto and
    # larger stocks both stable without a fixed cent threshold.
    tolerance = max(entry * 0.0012, 1e-9)
    merged: List[Dict[str, Any]] = []
    for level in sorted(levels, key=lambda x: _safe_float(x.get("price"), 0.0) or 0.0):
        price = _safe_float(level.get("price"))
        if price is None or price <= 0:
            continue
        if merged and abs(price - (_safe_float(merged[-1].get("price"), price) or price)) <= tolerance:
            if (_safe_float(level.get("weight"), 0.0) or 0.0) > (_safe_float(merged[-1].get("weight"), 0.0) or 0.0):
                merged[-1] = level
            else:
                merged[-1].setdefault("merged_sources", []).append(level.get("source", "VRVP"))
            continue
        merged.append(level)
    return merged


def _make_level(price: Any, source: str, kind: str, weight: float) -> Optional[Dict[str, Any]]:
    val = _safe_float(price)
    if val is None or val <= 0:
        return None
    return {
        "price": val,
        "rounded": round_trade_price(val),
        "source": source,
        "kind": kind,
        "weight": round(float(weight or 0.0), 3),
    }


def build_vrvp_structure(
    bars: List[Dict[str, Any]],
    current_price: Any,
    direction: str = "LONG",
    *,
    timeframe: str = "1D",
    num_bins: int = 24,
    min_bars: int = 20,
    lookback: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Build support/resistance candidates from a local volume profile."""
    current = _safe_float(current_price)
    if current is None or current <= 0:
        return None
    ohlcv = normalize_ohlcv_bars(bars or [], lookback=lookback)
    if len(ohlcv) < min_bars:
        return None
    profile = calculate_volume_profile(ohlcv, num_bins=max(12, int(num_bins or 24)))
    if not profile:
        return None

    avg_vol = _safe_float(profile.get("avg_volume"), 0.0) or 0.0
    levels: List[Dict[str, Any]] = []
    for source, key, weight in (
        ("VRVP POC", "poc", 2.2),
        ("VRVP VAH", "vah", 1.7),
        ("VRVP VAL", "val", 1.7),
    ):
        level = _make_level(profile.get(key), source, key.upper(), weight)
        if level:
            levels.append(level)

    for hvn in profile.get("hvns") or []:
        vol = _safe_float(hvn.get("volume"), 0.0) or 0.0
        weight = 1.45 + (vol / avg_vol if avg_vol > 0 else 0.0) * 0.15
        for price, suffix in ((hvn.get("mid"), "HVN mid"), (hvn.get("low"), "HVN low"), (hvn.get("high"), "HVN high")):
            level = _make_level(price, f"VRVP {suffix}", "HVN", weight)
            if level:
                levels.append(level)

    for lvn in profile.get("lvns") or []:
        # LVN edges are often better targets than the low-volume center.
        for price, suffix in ((lvn.get("low"), "LVN lower edge"), (lvn.get("high"), "LVN upper edge")):
            level = _make_level(price, f"VRVP {suffix}", "LVN_EDGE", 1.05)
            if level:
                levels.append(level)

    levels = _dedupe_levels(levels, current)
    supports = sorted([lvl for lvl in levels if (_safe_float(lvl.get("price"), 0) or 0) < current], key=lambda x: x["price"], reverse=True)
    resistances = sorted([lvl for lvl in levels if (_safe_float(lvl.get("price"), 0) or 0) > current], key=lambda x: x["price"])

    return {
        "timeframe": timeframe,
        "direction": str(direction or "").upper(),
        "bars": len(ohlcv),
        "poc": round_trade_price(profile.get("poc")),
        "vah": round_trade_price(profile.get("vah")),
        "val": round_trade_price(profile.get("val")),
        "range_high": round_trade_price(profile.get("range_high")),
        "range_low": round_trade_price(profile.get("range_low")),
        "supports": supports[:8],
        "resistances": resistances[:8],
        "levels": levels[:24],
        "profile_quality": "ok" if len(ohlcv) >= max(min_bars, 30) else "thin",
        "source": "ohlcv_volume_profile",
    }


def _get_level(setup: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key in setup:
            val = _safe_float(setup.get(key))
            if val is not None:
                return val
    return None


def _set_level_aliases(setup: Dict[str, Any], key: str, value: float) -> None:
    rounded = round_trade_price(value)
    if key == "entry":
        for alias in ("entry", "Entry"):
            setup[alias] = rounded
    elif key == "stop":
        for alias in ("stop", "StopLoss", "stop_loss"):
            setup[alias] = rounded
    elif key == "tp1":
        for alias in ("tp1", "TP1", "target1"):
            if alias in setup or key == "tp1":
                setup[alias] = rounded
    elif key == "tp2":
        for alias in ("tp2", "TP2", "target2"):
            if alias in setup or key == "tp2":
                setup[alias] = rounded


def _distance_ok(price: float, entry: float, min_reward: float, direction: str) -> bool:
    if direction == "LONG":
        return price > entry and (price - entry) >= min_reward
    return 0 < price < entry and (entry - price) >= min_reward


def _candidate_prices(vrvp: Dict[str, Any], side: str, target: bool) -> List[Tuple[float, str]]:
    key = "resistances" if (side == "LONG") == target else "supports"
    candidates: List[Tuple[float, str]] = []
    for level in vrvp.get(key) or []:
        price = _safe_float(level.get("price"))
        if price and price > 0:
            candidates.append((price, str(level.get("source") or "VRVP level")))
    return candidates


def _asset_profile(asset_type: str) -> Dict[str, float]:
    text = str(asset_type or "").lower()
    if "crypto" in text:
        return {"min_tp_pct": 0.045, "max_stop_mult": 1.45, "stop_buffer_pct": 0.004}
    if "intraday" in text or "orb" in text:
        return {"min_tp_pct": 0.006, "max_stop_mult": 1.25, "stop_buffer_pct": 0.0015}
    return {"min_tp_pct": 0.025, "max_stop_mult": 1.35, "stop_buffer_pct": 0.0025}


def apply_vrvp_to_trade_setup(
    setup: Dict[str, Any],
    vrvp: Optional[Dict[str, Any]],
    *,
    direction: Optional[str] = None,
    asset_type: str = "stock",
    atr: Optional[float] = None,
) -> Dict[str, Any]:
    """Return setup enriched with VRVP support/resistance where it improves structure."""
    if not isinstance(setup, dict):
        return setup
    enriched = dict(setup)
    if not vrvp:
        enriched["vrvp_applied"] = False
        return enriched

    side = str(direction or enriched.get("direction") or "").upper()
    entry = _get_level(enriched, "entry", "Entry")
    stop = _get_level(enriched, "stop", "StopLoss", "stop_loss")
    tp1 = _get_level(enriched, "tp1", "TP1", "target1")
    tp2 = _get_level(enriched, "tp2", "TP2", "target2")
    if side not in ("LONG", "SHORT") and entry is not None and stop is not None:
        side = "LONG" if stop < entry else "SHORT"
    if side not in ("LONG", "SHORT") or entry is None or stop is None or entry <= 0 or stop <= 0:
        enriched["vrvp_applied"] = False
        return enriched

    risk = (entry - stop) if side == "LONG" else (stop - entry)
    if risk <= 0:
        enriched["vrvp_applied"] = False
        return enriched

    profile = _asset_profile(asset_type)
    atr_value = _safe_float(atr, 0.0) or 0.0
    min_tp_reward = max(risk * 1.35, entry * profile["min_tp_pct"], atr_value * 0.65)
    min_tp2_reward = max(risk * 2.15, min_tp_reward * 1.45, entry * profile["min_tp_pct"] * 1.65)

    used: List[str] = []
    # Stop only moves to a nearby VRVP invalidation zone when it does not widen
    # risk too aggressively. Otherwise we keep the existing structure stop.
    stop_candidates = _candidate_prices(vrvp, side, target=False)
    if stop_candidates:
        if side == "LONG":
            valid_stops = [(p, s) for p, s in stop_candidates if p < entry]
            valid_stops.sort(key=lambda x: x[0], reverse=True)
            for support, source in valid_stops:
                proposed = support - max(entry * profile["stop_buffer_pct"], atr_value * 0.05)
                new_risk = entry - proposed
                if new_risk >= risk * 0.55 and new_risk <= risk * profile["max_stop_mult"]:
                    stop = proposed
                    enriched["stop_source"] = f"{source} invalidation"
                    used.append("stop")
                    break
        else:
            valid_stops = [(p, s) for p, s in stop_candidates if p > entry]
            valid_stops.sort(key=lambda x: x[0])
            for resistance, source in valid_stops:
                proposed = resistance + max(entry * profile["stop_buffer_pct"], atr_value * 0.05)
                new_risk = proposed - entry
                if new_risk >= risk * 0.55 and new_risk <= risk * profile["max_stop_mult"]:
                    stop = proposed
                    enriched["stop_source"] = f"{source} invalidation"
                    used.append("stop")
                    break
        risk = (entry - stop) if side == "LONG" else (stop - entry)

    target_candidates = _candidate_prices(vrvp, side, target=True)
    if side == "LONG":
        target_candidates.sort(key=lambda x: x[0])
    else:
        target_candidates.sort(key=lambda x: x[0], reverse=True)

    selected_tp1: Optional[Tuple[float, str]] = None
    selected_tp2: Optional[Tuple[float, str]] = None
    for price, source in target_candidates:
        if selected_tp1 is None and _distance_ok(price, entry, min_tp_reward, side):
            selected_tp1 = (price, source)
            continue
        if selected_tp1 is not None and _distance_ok(price, entry, min_tp2_reward, side):
            if abs(price - selected_tp1[0]) >= max(risk * 0.35, entry * 0.006):
                selected_tp2 = (price, source)
                break

    if selected_tp1:
        tp1 = selected_tp1[0]
        enriched["tp1_source"] = selected_tp1[1]
        used.append("tp1")
    if selected_tp2:
        tp2 = selected_tp2[0]
        enriched["tp2_source"] = selected_tp2[1]
        used.append("tp2")

    # Preserve valid existing targets if VRVP has no cleaner level, but never
    # allow duplicate/invalid TP1 and TP2 after enrichment.
    if tp1 is None or not _distance_ok(tp1, entry, max(risk * 1.05, entry * 0.004), side):
        tp1 = entry + risk * 1.6 if side == "LONG" else max(0.00000001, entry - risk * 1.6)
        enriched["tp1_source"] = "risk fallback after VRVP validation"
    if tp2 is None or not _distance_ok(tp2, entry, max(abs(tp1 - entry) * 1.18, risk * 1.8), side):
        tp2 = entry + max(risk * 2.45, abs(tp1 - entry) * 1.35) if side == "LONG" else max(0.00000001, entry - max(risk * 2.45, abs(tp1 - entry) * 1.35))
        enriched["tp2_source"] = "risk fallback after VRVP validation"

    _set_level_aliases(enriched, "entry", entry)
    _set_level_aliases(enriched, "stop", stop)
    _set_level_aliases(enriched, "tp1", tp1)
    _set_level_aliases(enriched, "tp2", tp2)

    reward1 = abs(tp1 - entry)
    reward2 = abs(tp2 - entry)
    rr_tp1 = reward1 / risk if risk > 0 else 0.0
    rr_tp2 = reward2 / risk if risk > 0 else 0.0
    rr = (rr_tp1 + rr_tp2) / 2 if rr_tp2 > 0 else rr_tp1

    enriched["risk"] = round_trade_price(risk)
    enriched["rr"] = round(rr, 2)
    enriched["rr_tp1"] = round(rr_tp1, 2)
    enriched["rr_tp2"] = round(rr_tp2, 2)
    enriched["direction"] = side
    enriched["vrvp_applied"] = bool(used)
    enriched["vrvp_timeframe"] = vrvp.get("timeframe")
    enriched["vrvp_poc"] = vrvp.get("poc")
    enriched["vrvp_vah"] = vrvp.get("vah")
    enriched["vrvp_val"] = vrvp.get("val")
    enriched["vrvp_levels"] = {
        "supports": [lvl.get("rounded") for lvl in (vrvp.get("supports") or [])[:4]],
        "resistances": [lvl.get("rounded") for lvl in (vrvp.get("resistances") or [])[:4]],
    }
    old_model = str(enriched.get("level_model") or "structure_first_v2")
    if "vrvp" not in old_model.lower():
        enriched["level_model"] = f"{old_model}+vrvp"
    if used:
        enriched["target_quality"] = "STRUCTURAL_VRVP"
        notes = list(enriched.get("notes") or [])
        notes.append(f"VRVP {vrvp.get('timeframe')} als Support/Resistance-Konfluenz genutzt")
        enriched["notes"] = notes
    return enriched
