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


PENNY_MIN_PRICE = 0.20
PENNY_MAX_PRICE = 5.00


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
    if rvol < 1.15 and change_pct < 2.0:
        blockers.append("no_volume_or_price_ignition")
    if spread_bps > 350:
        blockers.append("spread_too_wide_for_monitoring")

    liquidity = _scale(math.log10(max(projected_dollar_volume, 1.0)), 6.0, 7.7, 20.0)
    relative_volume = _scale(rvol, 1.0, 5.0, 22.0)
    momentum = _scale(change_pct, 0.0, 15.0, 18.0)
    close_strength = _scale(close_position, 0.35, 0.90, 15.0)
    spread_quality = _scale(350.0 - spread_bps, 0.0, 300.0, 10.0)
    early_move = 10.0 if 1.0 <= change_pct <= 12.0 else 4.0 if 0.0 < change_pct <= 22.0 else 0.0
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
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Analyze only completed chronological 5-minute bars."""
    data = _valid_bars(bars)
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
    typical_volume = _median((item["volume"] for item in baseline), 1.0)
    volume_ratio = latest["volume"] / max(typical_volume, 1.0)
    previous_volume_ratio = previous["volume"] / max(typical_volume, 1.0)
    breakout_level = max(item["high"] for item in baseline)
    candle_range = max(latest["high"] - latest["low"], latest["close"] * 0.001)
    close_position = _clamp((latest["close"] - latest["low"]) / candle_range, 0.0, 1.0)
    upper_wick_pct = _clamp((latest["high"] - max(latest["open"], latest["close"])) / candle_range * 100.0)

    cumulative_pv = sum(((item["high"] + item["low"] + item["close"]) / 3.0) * item["volume"] for item in data)
    cumulative_volume = sum(item["volume"] for item in data)
    vwap = cumulative_pv / cumulative_volume if cumulative_volume > 0 else latest["close"]
    ema9 = _ema(closes, 9)
    ema20 = _ema(closes, 20)

    true_ranges: List[float] = []
    for idx in range(max(1, len(data) - 13), len(data)):
        item = data[idx]
        prev_close = data[idx - 1]["close"]
        true_ranges.append(max(item["high"] - item["low"], abs(item["high"] - prev_close), abs(item["low"] - prev_close)))
    atr5 = sum(true_ranges) / len(true_ranges) if true_ranges else latest["close"] * 0.02

    breakout_confirmed = (
        latest["close"] >= breakout_level * 1.001
        and close_position >= 0.68
        and upper_wick_pct <= 32.0
        and volume_ratio >= 1.45
    )
    previous_broke = previous["close"] >= breakout_level * 1.001 and previous_volume_ratio >= 1.35
    retest_confirmed = (
        previous_broke
        and latest["low"] <= breakout_level * 1.015
        and latest["close"] >= breakout_level
        and close_position >= 0.55
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
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    now_value = float(now_ts if now_ts is not None else time.time())
    candle_closed_at = timestamp + 300.0 if timestamp > 0 else 0.0
    age_seconds = max(0.0, now_value - candle_closed_at) if candle_closed_at else None
    fresh = bool(age_seconds is not None and age_seconds <= 620.0)

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
) -> Dict[str, Any]:
    """Build stop and targets from actual invalidation/barrier structure."""
    entry = _num(intraday.get("price"))
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

    nearest = resistances[0]
    nearest_r = (_num(nearest.get("price")) - entry) / risk
    blockers: List[str] = []
    if nearest_r < 1.10:
        blockers.append("overhead_resistance_too_close")

    tp1 = next((level for level in resistances if (_num(level.get("price")) - entry) / risk >= 1.35), None)
    tp2 = next(
        (
            level
            for level in resistances
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
            "stop_source": stop_source,
            "nearest_barrier": {
                "price": _round_price(nearest.get("price")),
                "source": nearest.get("source"),
                "distance_r": round(nearest_r, 2),
            },
        }

    tp1_price = _num(tp1.get("price"))
    tp2_price = _num(tp2.get("price"))
    rr_tp1 = (tp1_price - entry) / risk
    rr_tp2 = (tp2_price - entry) / risk
    effective_rr = rr_tp1 * 0.5 + rr_tp2 * 0.5
    return {
        "valid": True,
        "blockers": [],
        "direction": "LONG",
        "entry": _round_price(entry),
        "stop": _round_price(stop),
        "stop_loss": _round_price(stop),
        "tp1": _round_price(tp1_price),
        "tp2": _round_price(tp2_price),
        "risk": _round_price(risk),
        "rr": round(effective_rr, 2),
        "live_rr": round(effective_rr, 2),
        "rr_tp1": round(rr_tp1, 2),
        "rr_tp2": round(rr_tp2, 2),
        "stop_source": stop_source,
        "tp1_source": str(tp1.get("source") or "structural resistance"),
        "tp2_source": str(tp2.get("source") or "structural resistance"),
        "target_quality": "STRUCTURAL",
        "model": "penny_5m_trigger_multitimeframe_structure_v1",
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
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Return one transparent lifecycle row for UI, mail and persistence."""
    details = details or {}
    broad = score_broad_penny_candidate(snapshot)
    intraday = analyze_penny_intraday(bars_5m, now_ts=now_ts)
    price = _num(intraday.get("price"), _num(snapshot.get("price")))
    rvol = _num(snapshot.get("rvol"))
    change_pct = _num(snapshot.get("change_pct"))
    projected_dollar = _num(snapshot.get("projected_dollar_volume"))
    dollar_volume = _num(snapshot.get("dollar_volume"))
    spread_bps = _num(snapshot.get("spread_bps"), 9999.0)
    shares_m = _num(details.get("shares_millions"))
    market_cap_m = _num(details.get("market_cap_millions"))
    news_context = details.get("news_context") if isinstance(details.get("news_context"), dict) else {}
    positive_catalysts = list(news_context.get("positive_catalysts") or [])
    company_news_risks = list(news_context.get("risk_flags") or [])

    structure_score = 0.0
    if intraday.get("data_ok"):
        structure_score += _scale(2.8 - abs(_num(intraday.get("distance_to_breakout_pct"))), 0.0, 2.8, 8.0)
        structure_score += 5.0 if _num(intraday.get("ema9")) >= _num(intraday.get("ema20")) else 0.0
        structure_score += 5.0 if price >= _num(intraday.get("vwap")) else 0.0
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
    entry_quality += 8.0 if price >= _num(intraday.get("vwap")) > 0 else 0.0
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

    plan = build_penny_trade_plan(intraday, daily_bars, extra_resistances=extra_resistances)
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
    if spread_bps > 180:
        hard_blockers.append("spread_above_180bps")
    if pump_potential < 70:
        hard_blockers.append("pump_potential_below_70")
    if entry_quality < 75:
        hard_blockers.append("entry_quality_below_75")
    if dump_risk > 45:
        hard_blockers.append("dump_risk_above_45")
    if company_news_risks:
        hard_blockers.append("recent_dilution_reverse_split_or_company_risk_news")
    if not plan.get("valid"):
        hard_blockers.extend(plan.get("blockers") or ["invalid_structure_plan"])

    exit_now = bool(previous_active and (
        intraday.get("vwap_lost")
        or intraday.get("heavy_red_bar")
        or intraday.get("volume_no_progress")
        or dump_risk >= 65
    ))
    if exit_now:
        lifecycle = "EXIT"
        trade_action = "JETZT_VERKAUFEN"
        action_label = "JETZT VERKAUFEN"
    elif previous_active:
        lifecycle = "FORTSETZUNG"
        trade_action = "HALTEN"
        action_label = "POSITION HALTEN / STOP NACHZIEHEN"
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

    trade_setup = dict(plan)
    trade_setup.update({
        "direction": "LONG",
        "trade_action": trade_action,
        "action_label": action_label,
        "entry_status": lifecycle,
        "risk_flags": list(dict.fromkeys([*(intraday.get("warnings") or []), *hard_blockers])),
        "notes": [
            f"Pump {round(pump_potential)} | Entry {round(entry_quality)} | Dump-Risiko {round(dump_risk)}",
            f"Trigger: {intraday.get('trigger_type', 'none')}",
        ],
    })

    return {
        "ticker": str(snapshot.get("ticker") or "").upper(),
        "name": str(details.get("name") or snapshot.get("name") or ""),
        "asset_class": "penny_stock",
        "price": _round_price(price),
        "change_pct": round(change_pct, 2),
        "rvol": round(rvol, 2),
        "volume": round(_num(snapshot.get("volume"))),
        "dollar_volume": round(dollar_volume),
        "projected_dollar_volume": round(projected_dollar),
        "spread_bps": round(spread_bps, 1),
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
        },
        "pump_potential_score": round(pump_potential),
        "entry_quality_score": round(entry_quality),
        "dump_risk_score": round(dump_risk),
        "score": round(pump_potential),
        "grade": grade_for_score(pump_potential),
        "lifecycle": lifecycle,
        "trade_action": trade_action,
        "trade_signal": trade_action,
        "signal_label": action_label,
        "execution_trigger_ok": trade_action == "JETZT_KAUFEN",
        "trigger_type": intraday.get("trigger_type"),
        "trigger_timestamp": intraday.get("trigger_timestamp"),
        "breakout_level": _round_price(intraday.get("breakout_level")),
        "vwap": _round_price(intraday.get("vwap")),
        "volume_acceleration": intraday.get("volume_ratio"),
        "hard_blockers": list(dict.fromkeys(hard_blockers)),
        "warnings": list(dict.fromkeys(intraday.get("warnings") or [])),
        "trade_setup": trade_setup,
        "entry": trade_setup.get("entry"),
        "stop_loss": trade_setup.get("stop_loss"),
        "tp1": trade_setup.get("tp1"),
        "tp2": trade_setup.get("tp2"),
        "rr": trade_setup.get("rr"),
    }
