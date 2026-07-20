"""Deterministic penny-stock pump lifecycle and execution model.

The scanner deliberately separates three questions:

* pump_potential_score: is unusual capital entering a liquid low-priced stock?
* entry_quality_score: is there a fresh, closed 5-minute execution trigger now?
* dump_risk_score: is the move already extended or distributing?

A high setup score is never sufficient for a buy signal.  ``JETZT_KAUFEN``
requires all execution, liquidity and structural target gates to pass.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from modules.trade_levels import trade_geometry
from modules.vrvp_levels import calculate_wilder_atr
from modules.volume_metrics import historical_volume_baseline


PENNY_MIN_PRICE = 0.20
PENNY_MAX_PRICE = 5.00
PENNY_EXECUTION_MAX_SPREAD_BPS = 120.0
PENNY_TRIGGER_MAX_AGE_SECONDS = 360.0
PENNY_MIN_TRADE_SCORE = 80.0
PENNY_NEAREST_BARRIER_MIN_R = 1.35
PENNY_MAX_ENTRY_DRIFT_R = 0.35
PENNY_DEFAULT_SLIPPAGE_BPS = 15.0
PENNY_MIN_NET_TP1_RR = 1.0
PENNY_MIN_NET_EFFECTIVE_RR = 1.5


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _scale(value: float, low: float, high: float, points: float) -> float:
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low), 0.0, 1.0) * points


def _median(values: Iterable[float], default: float = 0.0) -> float:
    cleaned = [float(value) for value in values if _num(value, 0.0) > 0]
    return float(statistics.median(cleaned)) if cleaned else default


def _round_price(value: Any) -> float:
    price = _num(value)
    if price >= 1:
        return round(price, 3)
    if price >= 0.01:
        return round(price, 5)
    return round(price, 8)


def _ema(values: Sequence[float], period: int) -> float:
    cleaned = [_num(value) for value in values if _num(value) > 0]
    if not cleaned:
        return 0.0
    alpha = 2.0 / (max(1, period) + 1.0)
    result = cleaned[0]
    for value in cleaned[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _bar(bar: Dict[str, Any]) -> Dict[str, float]:
    close = _num(bar.get("close", bar.get("c")))
    return {
        "open": _num(bar.get("open", bar.get("o")), close),
        "high": _num(bar.get("high", bar.get("h")), close),
        "low": _num(bar.get("low", bar.get("l")), close),
        "close": close,
        "volume": _num(bar.get("volume", bar.get("v"))),
        "timestamp": _num(bar.get("timestamp", bar.get("t"))),
    }


def _valid_bars(bars: Sequence[Dict[str, Any]]) -> List[Dict[str, float]]:
    result: List[Dict[str, float]] = []
    for raw in bars or []:
        if not isinstance(raw, dict):
            continue
        item = _bar(raw)
        if item["close"] <= 0 or item["high"] < item["low"] or item["volume"] < 0:
            continue
        result.append(item)
    return result


def _timestamp_seconds(value: Any) -> float:
    timestamp = _num(value)
    while timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return timestamp


def _completed_bars(
    bars: Sequence[Dict[str, Any]],
    *,
    timeframe_seconds: float,
    now_ts: float,
) -> List[Dict[str, float]]:
    """Return sorted, de-duplicated bars whose interval has fully closed."""
    completed: Dict[float, Dict[str, float]] = {}
    for item in _valid_bars(bars):
        timestamp = _timestamp_seconds(item.get("timestamp"))
        if timestamp <= 0 or timestamp + timeframe_seconds > now_ts:
            continue
        normalized = dict(item)
        normalized["timestamp"] = timestamp
        completed[timestamp] = normalized
    return [completed[key] for key in sorted(completed)]


def grade_for_score(score: Any) -> str:
    value = _num(score)
    if value >= 88:
        return "S"
    if value >= 76:
        return "A"
    if value >= 64:
        return "B"
    if value >= 52:
        return "C"
    return "D"


def score_broad_penny_candidate(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Cheap full-universe score used before per-ticker 5-minute calls."""
    price = _num(snapshot.get("price"))
    change_pct = _num(snapshot.get("change_pct"))
    rvol = _num(snapshot.get("rvol"))
    dollar_volume = _num(snapshot.get("dollar_volume"))
    projected_dollar_volume = _num(snapshot.get("projected_dollar_volume"))
    close_position = _clamp(_num(snapshot.get("close_position"), 0.5), 0.0, 1.0)
    spread_bps = _num(snapshot.get("spread_bps"), 9999.0)

    blockers: List[str] = []
    if not (PENNY_MIN_PRICE <= price <= PENNY_MAX_PRICE):
        blockers.append("price_outside_penny_universe")
    if dollar_volume < 150_000:
        blockers.append("current_dollar_volume_too_low")
    if projected_dollar_volume < 1_000_000:
        blockers.append("projected_dollar_volume_too_low")
    if spread_bps > 350:
        blockers.append("spread_too_wide_for_monitoring")

    liquidity = _scale(math.log10(max(projected_dollar_volume, 1.0)), 6.0, 7.7, 20.0)
    relative_volume = _scale(rvol, 1.0, 5.0, 22.0)
    momentum = _scale(change_pct, 0.0, 12.0, 18.0)
    close_strength = _scale(close_position, 0.35, 0.90, 15.0)
    spread_quality = _scale(350.0 - spread_bps, 0.0, 300.0, 10.0)
    # Smooth triangular preference: reward an emerging move, not an already
    # vertical pump. Avoid hard score cliffs at arbitrary percentage borders.
    if 0.0 < change_pct <= 8.0:
        early_move = change_pct / 8.0 * 10.0
    elif 8.0 < change_pct < 24.0:
        early_move = (24.0 - change_pct) / 16.0 * 10.0
    else:
        early_move = 0.0
    raw_score = liquidity + relative_volume + momentum + close_strength + spread_quality + early_move
    if change_pct > 35:
        raw_score -= min(20.0, (change_pct - 35.0) * 0.7)
    if change_pct < 0:
        raw_score -= min(20.0, abs(change_pct) * 1.2)

    return {
        "eligible": not blockers,
        "blockers": blockers,
        "broad_score": round(_clamp(raw_score), 1),
        "components": {
            "liquidity": round(liquidity, 1),
            "relative_volume": round(relative_volume, 1),
            "momentum": round(momentum, 1),
            "close_strength": round(close_strength, 1),
            "spread_quality": round(spread_quality, 1),
            "early_move": round(early_move, 1),
        },
    }


def analyze_penny_intraday(
    bars: Sequence[Dict[str, Any]],
    *,
    spread_bps: float = 0.0,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Analyze only completed chronological 5-minute bars."""
    now_value = float(now_ts if now_ts is not None else time.time())
    data = _completed_bars(bars, timeframe_seconds=300.0, now_ts=now_value)
    result: Dict[str, Any] = {
        "data_ok": False,
        "fresh": False,
        "trigger_confirmed": False,
        "breakout_confirmed": False,
        "retest_confirmed": False,
        "ignition": False,
        "warnings": [],
    }
    if len(data) < 15:
        result["warnings"] = ["insufficient_closed_5m_history"]
        return result

    latest = data[-1]
    previous = data[-2]
    baseline = data[-14:-2]
    if len(baseline) < 10:
        result["warnings"] = ["insufficient_5m_baseline"]
        return result

    closes = [item["close"] for item in data]
    typical_volume = historical_volume_baseline(
        [item["volume"] for item in baseline],
        lookback=len(baseline),
        method="median",
        minimum_periods=10,
    )
    if typical_volume is None:
        result["warnings"] = ["insufficient_5m_volume_baseline"]
        return result
    volume_ratio = latest["volume"] / typical_volume
    previous_volume_ratio = previous["volume"] / typical_volume
    breakout_level = max(item["high"] for item in baseline)
    candle_range = max(latest["high"] - latest["low"], latest["close"] * 0.001)
    close_position = _clamp((latest["close"] - latest["low"]) / candle_range, 0.0, 1.0)
    upper_wick_pct = _clamp((latest["high"] - max(latest["open"], latest["close"])) / candle_range * 100.0)

    cumulative_pv = sum(((item["high"] + item["low"] + item["close"]) / 3.0) * item["volume"] for item in data)
    cumulative_volume = sum(item["volume"] for item in data)
    vwap = cumulative_pv / cumulative_volume if cumulative_volume > 0 else latest["close"]
    ema9 = _ema(closes, 9)
    ema20 = _ema(closes, 20)

    atr5 = calculate_wilder_atr(data, period=14) or latest["close"] * 0.02

    atr_pct = atr5 / latest["close"] * 100.0 if latest["close"] > 0 else 0.0
    spread_pct = max(0.0, _num(spread_bps)) / 100.0
    breakout_clearance_pct = max(
        0.25,
        min(1.50, atr_pct * 0.15),
        min(1.50, spread_pct * 0.50),
    )
    breakout_confirmation_level = breakout_level * (1.0 + breakout_clearance_pct / 100.0)
    retest_tolerance_pct = max(0.45, min(1.50, atr_pct * 0.22 + spread_pct * 0.20))
    retest_floor = breakout_level * (1.0 - max(0.15, spread_pct * 0.20) / 100.0)

    breakout_confirmed = (
        latest["close"] >= breakout_confirmation_level
        and close_position >= 0.68
        and upper_wick_pct <= 32.0
        and volume_ratio >= 1.45
    )
    previous_broke = previous["close"] >= breakout_confirmation_level and previous_volume_ratio >= 1.35
    retest_confirmed = (
        previous_broke
        and latest["low"] <= breakout_level * (1.0 + retest_tolerance_pct / 100.0)
        and latest["close"] >= retest_floor
        and close_position >= 0.60
        and volume_ratio >= 0.80
        and latest["close"] >= latest["open"] * 0.995
    )
    distance_to_breakout_pct = (breakout_level - latest["close"]) / latest["close"] * 100.0
    ignition = (
        -0.5 <= distance_to_breakout_pct <= 2.5
        and latest["close"] >= vwap
        and ema9 >= ema20
        and volume_ratio >= 1.10
    )

    timestamp = latest["timestamp"]
    candle_closed_at = timestamp + 300.0 if timestamp > 0 else 0.0
    age_seconds = max(0.0, now_value - candle_closed_at) if candle_closed_at else None
    fresh = bool(age_seconds is not None and age_seconds <= PENNY_TRIGGER_MAX_AGE_SECONDS)

    recent = data[-6:]
    green_streak = 0
    for item in reversed(data):
        if item["close"] > item["open"]:
            green_streak += 1
        else:
            break
    extension_vwap_pct = (latest["close"] - vwap) / vwap * 100.0 if vwap > 0 else 0.0
    failed_highs = sum(
        1
        for item in recent
        if item["high"] >= breakout_level and item["close"] < breakout_level and item["high"] > item["low"]
    )
    recent_volume = sum(item["volume"] for item in recent[-3:])
    prior_volume = sum(item["volume"] for item in recent[:3])
    recent_price_change = (recent[-1]["close"] - recent[-4]["close"]) / recent[-4]["close"] * 100.0 if recent[-4]["close"] > 0 else 0.0
    volume_no_progress = prior_volume > 0 and recent_volume >= prior_volume * 1.5 and recent_price_change <= 0.5
    vwap_lost = latest["close"] < vwap and previous["close"] < vwap
    heavy_red_bar = latest["close"] < latest["open"] and volume_ratio >= 1.8 and close_position <= 0.35

    warnings: List[str] = []
    if not fresh:
        warnings.append("stale_5m_candle")
    if green_streak >= 5:
        warnings.append("extended_green_streak")
    if extension_vwap_pct >= 10.0:
        warnings.append("far_above_vwap")
    if failed_highs >= 2:
        warnings.append("repeated_failed_highs")
    if volume_no_progress:
        warnings.append("record_volume_without_price_progress")
    if upper_wick_pct >= 45.0:
        warnings.append("large_upper_wick")
    if heavy_red_bar:
        warnings.append("high_volume_red_reversal")

    result.update({
        "data_ok": True,
        "fresh": fresh,
        "age_seconds": int(age_seconds) if age_seconds is not None else None,
        "trigger_timestamp": int(timestamp) if timestamp else None,
        "price": latest["close"],
        "breakout_level": breakout_level,
        "breakout_confirmation_level": breakout_confirmation_level,
        "breakout_clearance_pct": round(breakout_clearance_pct, 3),
        "retest_tolerance_pct": round(retest_tolerance_pct, 3),
        "breakout_confirmed": breakout_confirmed,
        "retest_confirmed": retest_confirmed,
        "trigger_confirmed": bool(fresh and (breakout_confirmed or retest_confirmed)),
        "trigger_type": "5m_breakout" if breakout_confirmed else "5m_retest_hold" if retest_confirmed else "none",
        "ignition": ignition,
        "distance_to_breakout_pct": round(distance_to_breakout_pct, 2),
        "volume_ratio": round(volume_ratio, 2),
        "close_position": round(close_position, 3),
        "upper_wick_pct": round(upper_wick_pct, 1),
        "vwap": vwap,
        "ema9": ema9,
        "ema20": ema20,
        "atr5": atr5,
        "swing_low": min(item["low"] for item in data[-5:]),
        "retest_low": min(latest["low"], previous["low"]),
        "green_streak": green_streak,
        "extension_vwap_pct": round(extension_vwap_pct, 2),
        "failed_highs": failed_highs,
        "volume_no_progress": volume_no_progress,
        "vwap_lost": vwap_lost,
        "heavy_red_bar": heavy_red_bar,
        "warnings": warnings,
    })
    return result


def _daily_resistance_levels(daily_bars: Sequence[Dict[str, Any]], entry: float) -> List[Dict[str, Any]]:
    data = _valid_bars(daily_bars)
    if not data or entry <= 0:
        return []
    # Exclude the live/current day from historical resistance pivots.
    history = data[:-1] if len(data) > 1 else data
    levels: List[Dict[str, Any]] = []
    for lookback, label, weight in ((20, "20D High", 1.6), (60, "60D High", 1.9), (120, "120D High", 2.1)):
        subset = history[-lookback:]
        if subset:
            levels.append({"price": max(item["high"] for item in subset), "source": label, "weight": weight})
    for idx in range(2, max(2, len(history) - 2)):
        price = history[idx]["high"]
        if price > max(history[idx - 1]["high"], history[idx - 2]["high"]) and price >= max(history[idx + 1]["high"], history[idx + 2]["high"]):
            levels.append({"price": price, "source": "Daily swing high", "weight": 1.5})
    return [level for level in levels if _num(level.get("price")) > entry]


def _dedupe_structure_levels(levels: Sequence[Dict[str, Any]], entry: float) -> List[Dict[str, Any]]:
    tolerance = max(entry * 0.004, 0.000001)
    result: List[Dict[str, Any]] = []
    for level in sorted(levels, key=lambda item: _num(item.get("price"))):
        price = _num(level.get("price"))
        if price <= entry:
            continue
        if result and abs(price - _num(result[-1].get("price"))) <= tolerance:
            if _num(level.get("weight"), 1.0) > _num(result[-1].get("weight"), 1.0):
                result[-1] = dict(level)
            continue
        result.append(dict(level))
    return result


def build_penny_trade_plan(
    intraday: Dict[str, Any],
    daily_bars: Sequence[Dict[str, Any]],
    *,
    extra_resistances: Optional[Sequence[Dict[str, Any]]] = None,
    entry_price: Optional[float] = None,
    spread_bps: float = 0.0,
    slippage_bps: float = PENNY_DEFAULT_SLIPPAGE_BPS,
) -> Dict[str, Any]:
    """Build stop and targets from actual invalidation/barrier structure."""
    entry = _num(entry_price, _num(intraday.get("price")))
    atr5 = max(_num(intraday.get("atr5")), entry * 0.005)
    breakout = _num(intraday.get("breakout_level"))
    if entry <= 0 or breakout <= 0:
        return {"valid": False, "blockers": ["missing_entry_or_breakout_structure"]}

    buffer = max(entry * 0.003, atr5 * 0.12)
    min_risk = max(entry * 0.018, atr5 * 0.70)
    max_risk = entry * 0.12
    stop_candidates = [
        (breakout - buffer, "5m breakout/retest invalidation"),
        (_num(intraday.get("retest_low")) - buffer, "5m retest low invalidation"),
        (_num(intraday.get("vwap")) - buffer, "5m VWAP loss invalidation"),
        (_num(intraday.get("ema20")) - buffer, "5m EMA20 structure invalidation"),
        (_num(intraday.get("swing_low")) - buffer, "5m swing-low invalidation"),
    ]
    for level in extra_resistances or []:
        if not isinstance(level, dict):
            continue
        level_price = _num(level.get("price"))
        level_weight = _num(level.get("weight"), 1.0)
        if 0 < level_price < entry and level_weight >= 1.40:
            stop_candidates.append((level_price - buffer, f"{level.get('source') or 'profile support'} invalidation"))
    valid_stops = sorted(
        [(price, source) for price, source in stop_candidates if 0 < price < entry and min_risk <= entry - price <= max_risk],
        key=lambda item: item[0],
        reverse=True,
    )
    if not valid_stops:
        return {"valid": False, "blockers": ["no_structural_stop_in_valid_risk_band"]}
    stop, stop_source = valid_stops[0]
    risk = entry - stop

    resistances = _daily_resistance_levels(daily_bars, entry)
    resistances.extend(dict(level) for level in (extra_resistances or []) if isinstance(level, dict))
    resistances = _dedupe_structure_levels(resistances, entry)
    if not resistances:
        return {"valid": False, "blockers": ["no_verified_overhead_structure_targets"]}

    strong_resistances = [
        level
        for level in resistances
        if _num(level.get("weight"), 1.0) >= 1.40
        or any(token in str(level.get("source") or "").lower() for token in ("daily", "20d", "60d", "120d", "swing high"))
    ]
    if not strong_resistances:
        return {"valid": False, "blockers": ["no_high_confidence_overhead_structure_targets"]}
    nearest = strong_resistances[0]
    nearest_r = (_num(nearest.get("price")) - entry) / risk
    blockers: List[str] = []
    if nearest_r < PENNY_NEAREST_BARRIER_MIN_R:
        blockers.append("overhead_resistance_too_close")
    tp1 = next(
        (level for level in strong_resistances if (_num(level.get("price")) - entry) / risk >= PENNY_NEAREST_BARRIER_MIN_R),
        None,
    )
    tp2 = next(
        (
            level
            for level in strong_resistances
            if tp1 is not None
            and _num(level.get("price")) > _num(tp1.get("price")) + risk * 0.35
            and (_num(level.get("price")) - entry) / risk >= 2.25
        ),
        None,
    )
    if tp1 is None:
        blockers.append("no_structural_tp1_at_acceptable_reward")
    if tp2 is None:
        blockers.append("no_distinct_structural_tp2_at_acceptable_reward")
    if blockers:
        return {
            "valid": False,
            "blockers": blockers,
            "entry": _round_price(entry),
            "stop_loss": _round_price(stop),
            "risk": _round_price(risk),
            "stop_source": stop_source,
            "nearest_barrier": {
                "price": _round_price(nearest.get("price")),
                "source": nearest.get("source"),
                "distance_r": round(nearest_r, 2),
            },
        }

    entry = _round_price(entry)
    stop = _round_price(stop)
    tp1_price = _round_price(tp1.get("price"))
    tp2_price = _round_price(tp2.get("price"))
    geometry = trade_geometry(entry, stop, tp1_price, tp2_price, "LONG")
    if not geometry.get("valid"):
        return {
            "valid": False,
            "blockers": ["invalid_trade_geometry"],
            "geometry_errors": geometry.get("errors", []),
        }
    risk = float(geometry["risk"])
    rr_tp1 = float(geometry["rr_tp1"])
    rr_tp2 = float(geometry["rr_tp2"])
    gross_effective_rr = float(geometry["rr"])
    round_trip_cost = entry * (
        max(0.0, _num(spread_bps)) + 2.0 * max(0.0, _num(slippage_bps))
    ) / 10_000.0
    net_rr_tp1 = (tp1_price - entry - round_trip_cost) / risk
    net_rr_tp2 = (tp2_price - entry - round_trip_cost) / risk
    net_effective_rr = net_rr_tp1 * 0.5 + net_rr_tp2 * 0.5
    cost_blockers: List[str] = []
    if net_rr_tp1 < PENNY_MIN_NET_TP1_RR:
        cost_blockers.append("net_tp1_reward_below_cost_adjusted_minimum")
    if net_effective_rr < PENNY_MIN_NET_EFFECTIVE_RR:
        cost_blockers.append("net_effective_rr_below_cost_adjusted_minimum")
    if cost_blockers:
        return {
            "valid": False,
            "blockers": cost_blockers,
            "entry": entry,
            "stop_loss": stop,
            "tp1": tp1_price,
            "tp2": tp2_price,
            "risk": _round_price(risk),
            "gross_rr": round(gross_effective_rr, 2),
            "net_rr": round(net_effective_rr, 2),
            "gross_rr_tp1": round(rr_tp1, 2),
            "gross_rr_tp2": round(rr_tp2, 2),
            "net_rr_tp1": round(net_rr_tp1, 2),
            "net_rr_tp2": round(net_rr_tp2, 2),
            "round_trip_cost": _round_price(round_trip_cost),
            "cost_model": "spread plus two-sided slippage",
        }
    return {
        "valid": True,
        "blockers": [],
        "direction": "LONG",
        "entry": entry,
        "stop": stop,
        "stop_loss": stop,
        "tp1": tp1_price,
        "tp2": tp2_price,
        "risk": _round_price(risk),
        "rr": round(net_effective_rr, 2),
        "live_rr": round(net_effective_rr, 2),
        "rr_tp1": round(net_rr_tp1, 2),
        "rr_tp2": round(net_rr_tp2, 2),
        "gross_rr": round(gross_effective_rr, 2),
        "gross_rr_tp1": round(rr_tp1, 2),
        "gross_rr_tp2": round(rr_tp2, 2),
        "round_trip_cost": _round_price(round_trip_cost),
        "spread_bps": round(max(0.0, _num(spread_bps)), 1),
        "slippage_bps": round(max(0.0, _num(slippage_bps)), 1),
        "cost_model": "spread plus two-sided slippage",
        "stop_source": stop_source,
        "tp1_source": str(tp1.get("source") or "structural resistance"),
        "tp2_source": str(tp2.get("source") or "structural resistance"),
        "target_quality": "STRUCTURAL",
        "structure_confidence": "HIGH" if _num(tp1.get("weight"), 1.0) >= 1.7 and _num(tp2.get("weight"), 1.0) >= 1.7 else "MEDIUM",
        "model": "penny_5m_execution_multitimeframe_structure_v2",
        "nearest_barrier": {
            "price": _round_price(nearest.get("price")),
            "source": nearest.get("source"),
            "distance_r": round(nearest_r, 2),
        },
    }


def evaluate_penny_candidate(
    snapshot: Dict[str, Any],
    bars_5m: Sequence[Dict[str, Any]],
    daily_bars: Sequence[Dict[str, Any]],
    *,
    details: Optional[Dict[str, Any]] = None,
    extra_resistances: Optional[Sequence[Dict[str, Any]]] = None,
    previous_active: bool = False,
    previous_position: Optional[Dict[str, Any]] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Return an internal lifecycle decision for signal gating and persistence."""
    details = details or {}
    previous_position = previous_position or {}
    broad = score_broad_penny_candidate(snapshot)
    spread_bps = _num(snapshot.get("spread_bps"), 9999.0)
    intraday = analyze_penny_intraday(bars_5m, spread_bps=spread_bps, now_ts=now_ts)
    trigger_price = _num(intraday.get("price"), _num(snapshot.get("price")))
    live_bid = _num(snapshot.get("bid"), _num(snapshot.get("price"), trigger_price))
    live_ask = _num(snapshot.get("ask"), _num(snapshot.get("price"), trigger_price))
    execution_price = live_ask if snapshot.get("spread_known") and live_ask > 0 else trigger_price
    mark_price = live_bid if snapshot.get("spread_known") and live_bid > 0 else _num(snapshot.get("price"), trigger_price)
    rvol = _num(snapshot.get("rvol"))
    change_pct = _num(snapshot.get("change_pct"))
    projected_dollar = _num(snapshot.get("projected_dollar_volume"))
    dollar_volume = _num(snapshot.get("dollar_volume"))
    shares_m = _num(details.get("shares_millions"))
    market_cap_m = _num(details.get("market_cap_millions"))
    news_context = details.get("news_context") if isinstance(details.get("news_context"), dict) else {}
    sec_context = details.get("sec_filing_context") if isinstance(details.get("sec_filing_context"), dict) else {}
    positive_catalysts = list(news_context.get("positive_catalysts") or [])
    company_news_risks = list(dict.fromkeys([
        *(news_context.get("risk_flags") or []),
        *(sec_context.get("risk_flags") or []),
    ]))

    structure_score = 0.0
    if intraday.get("data_ok"):
        structure_score += _scale(2.8 - abs(_num(intraday.get("distance_to_breakout_pct"))), 0.0, 2.8, 8.0)
        structure_score += 5.0 if _num(intraday.get("ema9")) >= _num(intraday.get("ema20")) else 0.0
        structure_score += 5.0 if trigger_price >= _num(intraday.get("vwap")) else 0.0
        structure_score += _scale(_num(intraday.get("volume_ratio")), 1.0, 3.0, 7.0)
    float_score = 3.0
    if 0 < shares_m <= 10:
        float_score = 10.0
    elif shares_m <= 25 and shares_m > 0:
        float_score = 8.0
    elif shares_m <= 50 and shares_m > 0:
        float_score = 6.0
    elif shares_m > 100:
        float_score = 1.0
    if 20 <= market_cap_m <= 350:
        float_score = min(10.0, float_score + 2.0)

    catalyst_score = 6.0 if positive_catalysts else 0.0
    pump_potential = _clamp(_num(broad.get("broad_score")) * 0.72 + structure_score + float_score + catalyst_score)

    entry_quality = 0.0
    if intraday.get("fresh"):
        entry_quality += 15.0
    if intraday.get("breakout_confirmed"):
        entry_quality += 30.0
    elif intraday.get("retest_confirmed"):
        entry_quality += 27.0
    elif intraday.get("ignition"):
        entry_quality += 12.0
    entry_quality += _scale(_num(intraday.get("close_position")), 0.45, 0.90, 12.0)
    entry_quality += _scale(_num(intraday.get("volume_ratio")), 1.0, 3.0, 13.0)
    entry_quality += 8.0 if trigger_price >= _num(intraday.get("vwap")) > 0 else 0.0
    entry_quality += 7.0 if _num(intraday.get("ema9")) >= _num(intraday.get("ema20")) > 0 else 0.0
    entry_quality += _scale(180.0 - spread_bps, 0.0, 150.0, 15.0)
    entry_quality = _clamp(entry_quality)

    dump_risk = 0.0
    dump_risk += _scale(change_pct, 12.0, 45.0, 20.0)
    dump_risk += _scale(_num(intraday.get("extension_vwap_pct")), 5.0, 18.0, 18.0)
    dump_risk += _scale(_num(intraday.get("upper_wick_pct")), 25.0, 65.0, 18.0)
    dump_risk += min(14.0, _num(intraday.get("failed_highs")) * 7.0)
    dump_risk += 14.0 if intraday.get("volume_no_progress") else 0.0
    dump_risk += 15.0 if intraday.get("heavy_red_bar") else 0.0
    dump_risk += 12.0 if intraday.get("vwap_lost") else 0.0
    dump_risk += _scale(spread_bps, 120.0, 350.0, 12.0)
    if _num(intraday.get("green_streak")) >= 5:
        dump_risk += 10.0
    if 0 < shares_m <= 10:
        dump_risk += 5.0
    if company_news_risks:
        dump_risk += 25.0
    dump_risk = _clamp(dump_risk)

    plan = build_penny_trade_plan(
        intraday,
        daily_bars,
        extra_resistances=extra_resistances,
        entry_price=execution_price,
        spread_bps=spread_bps,
    )
    trade_score = _clamp(pump_potential * 0.35 + entry_quality * 0.45 + (100.0 - dump_risk) * 0.20)
    hard_blockers: List[str] = []
    if not intraday.get("data_ok"):
        hard_blockers.append("closed_5m_data_missing")
    if not intraday.get("fresh"):
        hard_blockers.append("closed_5m_trigger_stale")
    if not intraday.get("trigger_confirmed"):
        hard_blockers.append("fresh_5m_breakout_or_retest_missing")
    if dollar_volume < 500_000:
        hard_blockers.append("current_dollar_volume_below_500k")
    if projected_dollar < 3_000_000:
        hard_blockers.append("projected_dollar_volume_below_3m")
    if rvol < 1.5:
        hard_blockers.append("rvol_below_1_5")
    if not snapshot.get("spread_known"):
        hard_blockers.append("live_spread_unknown")
    if spread_bps > PENNY_EXECUTION_MAX_SPREAD_BPS:
        hard_blockers.append("spread_above_execution_limit")
    if pump_potential < 70:
        hard_blockers.append("pump_potential_below_70")
    if entry_quality < 75:
        hard_blockers.append("entry_quality_below_75")
    if trade_score < PENNY_MIN_TRADE_SCORE:
        hard_blockers.append("trade_score_below_80")
    if dump_risk > 45:
        hard_blockers.append("dump_risk_above_45")
    if company_news_risks:
        hard_blockers.append("recent_dilution_reverse_split_or_company_risk_filing")
    news_ok = str(news_context.get("status") or "").lower() == "ok"
    sec_ok = str(sec_context.get("status") or "").lower() == "ok"
    context_warnings: List[str] = []
    if not news_ok:
        context_warnings.append("recent_news_context_unavailable")
    if not sec_ok:
        hard_blockers.append("sec_filing_risk_data_unavailable")
    if not plan.get("valid"):
        hard_blockers.extend(plan.get("blockers") or ["invalid_structure_plan"])

    risk = _num(plan.get("risk"))
    entry_drift_r = (execution_price - trigger_price) / risk if risk > 0 else None
    if entry_drift_r is not None and entry_drift_r > PENNY_MAX_ENTRY_DRIFT_R:
        hard_blockers.append("live_ask_too_far_above_trigger")
    trigger_type = str(intraday.get("trigger_type") or "none")
    if trigger_type == "5m_breakout" and execution_price < _num(intraday.get("breakout_confirmation_level")):
        hard_blockers.append("live_price_lost_breakout_confirmation")
    if trigger_type == "5m_retest_hold" and execution_price < _num(intraday.get("breakout_level")) * 0.998:
        hard_blockers.append("live_price_lost_retest_structure")

    model_active = bool(previous_active or previous_position.get("active"))
    persisted_setup = previous_position.get("trade_setup") if isinstance(previous_position.get("trade_setup"), dict) else {}
    if not persisted_setup and model_active:
        persisted_setup = {
            "entry": previous_position.get("buy_entry"),
            "stop_loss": previous_position.get("stop_loss"),
            "tp1": previous_position.get("tp1"),
            "tp2": previous_position.get("tp2"),
            "direction": "LONG",
        }
    original_stop = _num(persisted_setup.get("stop_loss", persisted_setup.get("stop")))
    original_tp1 = _num(persisted_setup.get("tp1"))
    original_tp2 = _num(persisted_setup.get("tp2"))
    position_levels_ok = bool(original_stop > 0 and original_tp1 > 0 and original_tp2 > original_tp1)
    data_reliable = bool(
        intraday.get("data_ok")
        and intraday.get("fresh")
        and snapshot.get("spread_known")
        and mark_price > 0
        and (not model_active or position_levels_ok)
    )
    stop_hit = bool(model_active and original_stop > 0 and mark_price <= original_stop)
    tp1_hit = bool(model_active and original_tp1 > 0 and mark_price >= original_tp1)
    tp2_hit = bool(model_active and original_tp2 > 0 and mark_price >= original_tp2)

    exit_now = bool(model_active and data_reliable and (
        stop_hit
        or tp2_hit
        or intraday.get("vwap_lost")
        or intraday.get("heavy_red_bar")
        or intraday.get("volume_no_progress")
        or dump_risk >= 65
        or bool(company_news_risks)
    ))
    if exit_now:
        lifecycle = "EXIT"
        trade_action = "JETZT_VERKAUFEN"
        action_label = "JETZT VERKAUFEN - MODELLPOSITION INVALIDIERT"
    elif model_active and not data_reliable:
        lifecycle = "DATENLUECKE"
        trade_action = "TRIGGER_WARTEN"
        action_label = "MODELLPOSITION: DATEN FEHLEN - KEIN BLINDES HALTEN"
    elif model_active:
        lifecycle = "FORTSETZUNG"
        trade_action = "HALTEN"
        action_label = "MODELLPOSITION: TP1 ERREICHT / STOP NACHZIEHEN" if tp1_hit else "MODELLPOSITION HALTEN"
    elif not hard_blockers:
        lifecycle = "ENTRY"
        trade_action = "JETZT_KAUFEN"
        action_label = "JETZT KAUFEN"
    elif dump_risk >= 65:
        lifecycle = "DISTRIBUTION"
        trade_action = "NICHT_KAUFEN"
        action_label = "DISTRIBUTION / NICHT KAUFEN"
    elif intraday.get("ignition") and pump_potential >= 62:
        lifecycle = "IGNITION"
        trade_action = "TRIGGER_WARTEN"
        action_label = "IGNITION - 5M TRIGGER WARTEN"
    elif pump_potential >= 50:
        lifecycle = "AUFBAU"
        trade_action = "BEOBACHTEN"
        action_label = "AUFBAU BEOBACHTEN"
    else:
        lifecycle = "SCHWACH"
        trade_action = "NICHT_KAUFEN"
        action_label = "KEIN SETUP"

    trade_setup = dict(persisted_setup if model_active and persisted_setup else plan)
    trade_setup.update({
        "direction": "LONG",
        "trade_action": trade_action,
        "action_label": action_label,
        "entry_status": lifecycle,
        "risk_flags": list(dict.fromkeys([*(intraday.get("warnings") or []), *context_warnings, *hard_blockers])),
        "notes": [
            f"Trade {round(trade_score)} | Pump {round(pump_potential)} | Entry {round(entry_quality)} | Dump-Risiko {round(dump_risk)}",
            f"Trigger: {intraday.get('trigger_type', 'none')}",
        ],
    })

    return {
        "ticker": str(snapshot.get("ticker") or "").upper(),
        "name": str(details.get("name") or snapshot.get("name") or ""),
        "asset_class": "penny_stock",
        "price": _round_price(mark_price),
        "change_pct": round(change_pct, 2),
        "rvol": round(rvol, 2),
        "rvol_source": snapshot.get("rvol_source", "previous_day_volume_fallback"),
        "rvol_history_days": snapshot.get("rvol_history_days"),
        "rvol_baseline_volume": snapshot.get("rvol_baseline_volume"),
        "volume": round(_num(snapshot.get("volume"))),
        "dollar_volume": round(dollar_volume),
        "projected_dollar_volume": round(projected_dollar),
        "spread_bps": round(spread_bps, 1),
        "spread_known": bool(snapshot.get("spread_known")),
        "quote_age_seconds": snapshot.get("quote_age_seconds"),
        "shares_outstanding_m": round(shares_m, 1) if shares_m else None,
        "float_data_quality": "shares_outstanding_proxy" if shares_m else "unknown",
        "market_cap_m": round(market_cap_m, 1) if market_cap_m else None,
        "catalyst_context": {
            "positive": positive_catalysts,
            "risk_flags": company_news_risks,
            "headline": news_context.get("headline"),
            "data_status": news_context.get("status", "unavailable_or_empty"),
            "source": news_context.get("source", "recent_market_news_headlines"),
            "disclaimer": news_context.get("disclaimer", "Headline context; not a filing database."),
            "sec_status": sec_context.get("status", "unavailable"),
            "sec_source": sec_context.get("source", "SEC EDGAR submissions"),
        },
        "pump_potential_score": round(pump_potential),
        "entry_quality_score": round(entry_quality),
        "dump_risk_score": round(dump_risk),
        "trade_score": round(trade_score),
        "score": round(trade_score),
        "grade": grade_for_score(trade_score),
        "score_semantics": "trade_score=35% setup + 45% execution + 20% inverse dump risk; not a win probability",
        "lifecycle": lifecycle,
        "trade_action": trade_action,
        "trade_signal": trade_action,
        "signal_label": action_label,
        "execution_trigger_ok": trade_action == "JETZT_KAUFEN",
        "position_state_source": "scanner_model_not_broker" if model_active else "new_scan",
        "model_position_active": model_active,
        "trigger_type": intraday.get("trigger_type"),
        "trigger_timestamp": intraday.get("trigger_timestamp"),
        "signal_age_seconds": intraday.get("age_seconds"),
        "breakout_level": _round_price(intraday.get("breakout_level")),
        "breakout_confirmation_level": _round_price(intraday.get("breakout_confirmation_level")),
        "breakout_clearance_pct": intraday.get("breakout_clearance_pct"),
        "trigger_price": _round_price(trigger_price),
        "live_entry_price": _round_price(execution_price),
        "entry_price_source": "live_ask" if snapshot.get("spread_known") else "closed_5m_close_untradeable",
        "entry_drift_r": round(entry_drift_r, 2) if entry_drift_r is not None and math.isfinite(entry_drift_r) else None,
        "vwap": _round_price(intraday.get("vwap")),
        "volume_acceleration": intraday.get("volume_ratio"),
        "hard_blockers": list(dict.fromkeys(hard_blockers)),
        "warnings": list(dict.fromkeys([*(intraday.get("warnings") or []), *context_warnings])),
        "trade_setup": trade_setup,
        "entry": trade_setup.get("entry"),
        "stop_loss": trade_setup.get("stop_loss"),
        "tp1": trade_setup.get("tp1"),
        "tp2": trade_setup.get("tp2"),
        "rr": trade_setup.get("rr"),
    }


def evaluate_penny_signal_outcome(
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    future_bars: Sequence[Dict[str, Any]],
    *,
    spread_bps: float = 0.0,
    slippage_bps: float = 15.0,
) -> Dict[str, Any]:
    """Conservative deterministic replay for calibration and regression tests.

    If stop and target are touched in the same OHLC bar, stop wins because the
    intrabar path is unknown. Costs are charged on entry and exit.
    """
    entry = _num(entry)
    stop = _num(stop)
    tp1 = _num(tp1)
    tp2 = _num(tp2)
    geometry = trade_geometry(entry, stop, tp1, tp2, "LONG")
    data = _valid_bars(future_bars)
    if not geometry.get("valid") or not data:
        return {"valid": False, "outcome": "INVALID", "net_r": None}
    risk = float(geometry["risk"])

    round_trip_cost = entry * (max(0.0, spread_bps) + 2.0 * max(0.0, slippage_bps)) / 10_000.0
    tp1_seen = False
    exit_price = data[-1]["close"]
    outcome = "MARK_TO_MARKET"
    bars_held = len(data)
    for index, bar in enumerate(data, start=1):
        if bar["low"] <= stop:
            exit_price = stop
            outcome = "STOP"
            bars_held = index
            break
        if bar["high"] >= tp2:
            exit_price = tp2
            outcome = "TP2"
            bars_held = index
            break
        if bar["high"] >= tp1:
            tp1_seen = True

    gross_r = (exit_price - entry) / risk
    net_r = (exit_price - entry - round_trip_cost) / risk
    return {
        "valid": True,
        "outcome": outcome,
        "tp1_seen": tp1_seen,
        "bars_held": bars_held,
        "exit_price": _round_price(exit_price),
        "gross_r": round(gross_r, 3),
        "net_r": round(net_r, 3),
        "round_trip_cost": _round_price(round_trip_cost),
        "assumption": "same-bar ambiguity resolves stop-first; OHLC replay is not tick execution",
    }
