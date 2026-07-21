"""
Shared VRVP/volume-profile trade level helpers.

The local volume profile is built from OHLCV bars, so it is an approximation
of TradingView's tick-based VRVP. We use it as structural confluence for
support/resistance and targets, not as a standalone signal generator.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from modules.volume_analysis import calculate_volume_profile, merge_lvn_bins
from modules.trade_levels import trade_geometry


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
    if aval > 0:
        # Preserve six significant digits for micro-priced crypto. Fixed
        # decimal rounding can otherwise collapse a valid level to 0.0.
        return float(f"{val:.6g}")
    return 0.0


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


def calculate_wilder_atr(
    bars: List[Dict[str, Any]],
    period: int = 14,
    lookback: Optional[int] = None,
) -> float:
    """Return a canonical Wilder ATR for mixed API bar shapes.

    ATR is an absolute price distance, calculated on the same timeframe as
    ``bars``. Volume is intentionally not required because true range only
    depends on high, low, and the previous close. Returning ``0.0`` for fewer
    than ``period + 1`` valid bars keeps callers explicit about their fallback.
    """
    try:
        period = max(1, int(period))
    except (TypeError, ValueError):
        period = 14

    # NACHAUDIT N1 (defensiv): Wilder-ATR setzt chronologische Bars voraus.
    # APIs liefern teils sort=desc (neueste zuerst) — dann laeuft die
    # Glaettung rueckwaerts und previous_close ist der Folgetag. Wenn
    # Timestamps vorhanden sind, wird deshalb VOR dem Lookback-Slice
    # aufsteigend sortiert.
    raw_bars = [bar for bar in (bars or []) if isinstance(bar, dict)]

    def _bar_sort_ts(bar: Dict[str, Any]) -> Optional[float]:
        for key in ("t", "timestamp", "time", "ts"):
            value = bar.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
        return None

    ts_values = [_bar_sort_ts(bar) for bar in raw_bars]
    if len(raw_bars) >= 2 and all(ts is not None for ts in ts_values):
        raw_bars = [bar for _, bar in sorted(zip(ts_values, raw_bars), key=lambda pair: pair[0])]

    source = raw_bars[-lookback:] if lookback and lookback > 0 else raw_bars
    parsed: List[Dict[str, float]] = []
    for bar in source:
        if not isinstance(bar, dict):
            continue
        close = _safe_float(bar.get("close", bar.get("c")))
        high = _safe_float(bar.get("high", bar.get("h")))
        low = _safe_float(bar.get("low", bar.get("l")))
        if close is None or high is None or low is None:
            continue
        if close <= 0 or high <= 0 or low <= 0 or high < low:
            continue
        parsed.append({"high": high, "low": low, "close": close})

    if len(parsed) < period + 1:
        return 0.0

    true_ranges: List[float] = []
    for index in range(1, len(parsed)):
        bar = parsed[index]
        previous_close = parsed[index - 1]["close"]
        true_ranges.append(max(
            bar["high"] - bar["low"],
            abs(bar["high"] - previous_close),
            abs(bar["low"] - previous_close),
        ))

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((period - 1) * atr + true_range) / period
    return float(atr) if math.isfinite(atr) and atr > 0 else 0.0


def _dedupe_levels(levels: List[Dict[str, Any]], entry: float) -> List[Dict[str, Any]]:
    """Merge almost-identical profile levels without losing the strongest source."""
    if not levels or entry <= 0:
        return []
    # Treat levels inside 0.12% as the same zone. That keeps penny crypto and
    # larger stocks both stable without a fixed cent threshold.
    # NACHAUDIT N10: Floor relativ statt absolut — 1e-9 war bei Sub-Nano-
    # Coins ~45% des Preises und kollabierte alle Level zu einem.
    # A fixed absolute floor can collapse distinct levels for ultra-low-priced
    # tokens. math.ulp keeps the numerical floor relative to the actual value.
    tolerance = max(entry * 0.0012, math.ulp(entry) * 16)
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

    lvn_zones = profile.get("lvn_zones") or merge_lvn_bins(profile.get("lvns") or [])
    for lvn in lvn_zones:
        # A continuous void has only two meaningful boundaries. Internal raw
        # bin edges are not support/resistance and must never become targets.
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
        "volume_voids": lvn_zones,
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


def _level_kind(level: Dict[str, Any]) -> str:
    return str(level.get("kind") or "").strip().upper()


def _level_source(level: Dict[str, Any]) -> str:
    return str(level.get("source") or "VRVP level").strip()


def _is_lvn_edge(level: Dict[str, Any]) -> bool:
    return _level_kind(level) == "LVN_EDGE" or "LVN" in _level_source(level).upper()


def _is_structural_barrier(level: Dict[str, Any]) -> bool:
    """True for traded-volume zones, never for an empty-volume gap edge."""
    if _is_lvn_edge(level):
        return False
    kind = _level_kind(level)
    source = _level_source(level).upper()
    return kind in {"POC", "VAH", "VAL", "HVN"} or any(
        token in source for token in ("VRVP POC", "VRVP VAH", "VRVP VAL", "VRVP HVN")
    )


def _is_stop_anchor(level: Dict[str, Any], side: str) -> bool:
    """Use only defensible volume acceptance zones as stop invalidation."""
    if not _is_structural_barrier(level):
        return False
    kind = _level_kind(level)
    source = _level_source(level).upper()
    if kind == "POC" or "VRVP POC" in source:
        return True
    if side == "LONG":
        return kind == "VAL" or "VRVP VAL" in source or "HVN LOW" in source
    return kind == "VAH" or "VRVP VAH" in source or "HVN HIGH" in source


def _candidate_prices(vrvp: Dict[str, Any], side: str, target: bool) -> List[Tuple[float, str]]:
    key = "resistances" if (side == "LONG") == target else "supports"
    candidates: List[Tuple[float, str]] = []
    for level in vrvp.get(key) or []:
        if not target and not _is_stop_anchor(level, side):
            continue
        price = _safe_float(level.get("price"))
        if price and price > 0:
            candidates.append((price, _level_source(level)))
    return candidates


def _asset_profile(asset_type: str) -> Dict[str, float]:
    text = str(asset_type or "").lower()
    if "crypto" in text:
        return {"min_tp_pct": 0.045, "max_stop_mult": 1.45, "stop_buffer_pct": 0.004}
    if "intraday" in text or "orb" in text:
        return {"min_tp_pct": 0.006, "max_stop_mult": 1.25, "stop_buffer_pct": 0.0015}
    return {"min_tp_pct": 0.025, "max_stop_mult": 1.35, "stop_buffer_pct": 0.0025}


def _barrier_profile(asset_type: str) -> Dict[str, float]:
    """Distance thresholds where the next opposite VRVP zone becomes a gate.

    A nearby resistance/support is not automatically a target. If it is too
    close relative to risk, the setup first needs a break/reclaim instead of a
    blind entry.
    """
    text = str(asset_type or "").lower()
    if "crypto" in text:
        return {"max_r": 1.25, "max_pct": 1.8, "max_pct_r": 1.8}
    if "intraday" in text or "orb" in text:
        return {"max_r": 1.10, "max_pct": 0.9, "max_pct_r": 1.6}
    return {"max_r": 1.25, "max_pct": 2.5, "max_pct_r": 1.8}


def _nearest_target_level(vrvp: Dict[str, Any], side: str, entry: float) -> Optional[Dict[str, Any]]:
    key = "resistances" if side == "LONG" else "supports"
    levels = []
    for level in vrvp.get(key) or []:
        if not _is_structural_barrier(level):
            continue
        price = _safe_float(level.get("price"))
        if price is None or price <= 0:
            continue
        if side == "LONG" and price <= entry:
            continue
        if side == "SHORT" and price >= entry:
            continue
        levels.append(level)
    if not levels:
        return None
    return sorted(levels, key=lambda x: abs((_safe_float(x.get("price"), entry) or entry) - entry))[0]


def _near_trade_barrier(
    vrvp: Dict[str, Any],
    side: str,
    entry: float,
    risk: float,
    asset_type: str,
) -> Optional[Dict[str, Any]]:
    if not vrvp or side not in ("LONG", "SHORT") or entry <= 0 or risk <= 0:
        return None
    level = _nearest_target_level(vrvp, side, entry)
    if not level:
        return None
    price = _safe_float(level.get("price"))
    if price is None or price <= 0:
        return None
    distance = abs(price - entry)
    distance_pct = (distance / entry) * 100.0
    distance_r = distance / risk
    profile = _barrier_profile(asset_type)
    is_close = (
        distance_r <= profile["max_r"]
        or (distance_pct <= profile["max_pct"] and distance_r <= profile["max_pct_r"])
    )
    if not is_close:
        return None
    side_label = "resistance" if side == "LONG" else "support"
    return {
        "side": side_label,
        "price": round_trade_price(price),
        "source": str(level.get("source") or "VRVP level"),
        "timeframe": vrvp.get("timeframe"),
        "distance_pct": round(distance_pct, 2),
        "distance_r": round(distance_r, 2),
        "strength": round(float(_safe_float(level.get("weight"), 1.0) or 1.0), 2),
        "action": "BREAK_RECLAIM_REQUIRED" if side == "LONG" else "BREAK_SUPPORT_REQUIRED",
    }


def _attach_barrier_gate(enriched: Dict[str, Any], barrier: Optional[Dict[str, Any]], side: str) -> None:
    if not barrier:
        return
    key = "overhead_resistance" if side == "LONG" else "underlying_support"
    flag = "near_overhead_resistance" if side == "LONG" else "near_underlying_support"
    label = "Resistance erst brechen/reclaimen" if side == "LONG" else "Support erst brechen/reclaimen"
    enriched["nearest_barrier"] = barrier
    enriched[key] = barrier
    enriched["barrier_gate"] = barrier.get("action")
    enriched["barrier_gate_reason"] = label
    flags = list(enriched.get("risk_flags") or [])
    flags.append(flag)
    enriched["risk_flags"] = list(dict.fromkeys(flags))
    notes = list(enriched.get("notes") or [])
    notes.append(
        f"Nahe {barrier.get('side')} {barrier.get('price')} ({barrier.get('timeframe') or 'VRVP'}, "
        f"{barrier.get('distance_r')}R) - {label}"
    )
    enriched["notes"] = list(dict.fromkeys(notes))


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
    if atr_value < 0 or atr_value > entry * 0.50:
        enriched["vrvp_atr_warning"] = "implausible_atr_ignored"
        atr_value = 0.0
    min_tp_reward = max(risk * 1.5, entry * profile["min_tp_pct"], atr_value * 0.70)
    min_tp2_reward = max(risk * 2.4, min_tp_reward * 1.5, entry * profile["min_tp_pct"] * 1.8)
    used: List[str] = []
    # Stop only moves to a nearby VRVP invalidation zone when it does not widen
    # risk too aggressively. Otherwise we keep the existing structure stop.
    stop_candidates = _candidate_prices(vrvp, side, target=False)
    if stop_candidates:
        if side == "LONG":
            valid_stops = [(p, s) for p, s in stop_candidates if p < entry]
            valid_stops.sort(key=lambda x: x[0], reverse=True)
            for support, source in valid_stops:
                proposed = support - max(entry * profile["stop_buffer_pct"], atr_value * 0.35)
                new_risk = entry - proposed
                if new_risk >= risk * 0.80 and new_risk <= risk * profile["max_stop_mult"]:
                    stop = proposed
                    enriched["stop_source"] = f"{source} invalidation"
                    used.append("stop")
                    break
        else:
            valid_stops = [(p, s) for p, s in stop_candidates if p > entry]
            valid_stops.sort(key=lambda x: x[0])
            for resistance, source in valid_stops:
                proposed = resistance + max(entry * profile["stop_buffer_pct"], atr_value * 0.35)
                new_risk = proposed - entry
                if new_risk >= risk * 0.80 and new_risk <= risk * profile["max_stop_mult"]:
                    stop = proposed
                    enriched["stop_source"] = f"{source} invalidation"
                    used.append("stop")
                    break
        risk = (entry - stop) if side == "LONG" else (stop - entry)
    _attach_barrier_gate(enriched, _near_trade_barrier(vrvp, side, entry, risk, asset_type), side)

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
            if abs(price - selected_tp1[0]) >= max(risk * 0.55, entry * 0.008):
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
    if tp1 is None or not _distance_ok(tp1, entry, max(risk * 1.5, entry * 0.006), side):
        tp1 = entry + risk * 1.6 if side == "LONG" else max(0.00000001, entry - risk * 1.6)
        enriched["tp1_source"] = "risk fallback after VRVP validation"
    if tp2 is None or not _distance_ok(tp2, entry, max(abs(tp1 - entry) + risk * 0.55, risk * 2.4, entry * 0.012), side):
        tp2 = entry + max(risk * 2.45, abs(tp1 - entry) * 1.35) if side == "LONG" else max(0.00000001, entry - max(risk * 2.45, abs(tp1 - entry) * 1.35))
        enriched["tp2_source"] = "risk fallback after VRVP validation"

    geometry = trade_geometry(entry, stop, tp1, tp2, side)
    if not geometry["valid"]:
        enriched["vrvp_applied"] = False
        enriched["vrvp_geometry_errors"] = list(geometry.get("errors") or [])
        return enriched

    _set_level_aliases(enriched, "entry", entry)
    _set_level_aliases(enriched, "stop", stop)
    _set_level_aliases(enriched, "tp1", tp1)
    _set_level_aliases(enriched, "tp2", tp2)

    risk = float(geometry["risk"])
    rr_tp1 = float(geometry["rr_tp1"])
    rr_tp2 = float(geometry["rr_tp2"])
    rr = float(geometry["rr"])

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
