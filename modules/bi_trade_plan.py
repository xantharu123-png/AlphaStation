"""Pure production BI plan shared by the scanner and historical simulations.

Inputs are chronological completed bars. Execution/fill rules are deliberately
outside this module: a plan is not proof of an executable quote or a fill.
"""
from __future__ import annotations

import math

from modules.trade_levels import trade_geometry
from modules.vrvp_levels import apply_vrvp_to_trade_setup, build_vrvp_structure, calculate_wilder_atr


BI_PLAN_VERSION = "bi_shared_structure_v3"


def bi_consolidation_days(bars):
    if not bars:
        return 0
    current = float(bars[-1]["close"])
    if current <= 0:
        return 0
    high, low, days = float(bars[-1]["high"]), float(bars[-1]["low"]), 1
    for bar in reversed(bars[:-1]):
        high = max(high, float(bar["high"]))
        low = min(low, float(bar["low"]))
        if (high - low) / current * 100 >= 6:
            break
        days += 1
    return days


def build_bi_trade_plan(completed_bars, *, direction, range_days=None, live_price=None,
                        as_of=None, apply_structure=True):
    """Return accepted/reason, canonical prices and explicit entry semantics.

LONG waits for stop_breakout; SHORT in the lower range half requests
market_at_signal, otherwise limit_pullback. A daily backtest must document
its next-bar fill approximation instead of inventing an at-signal fill.
"""
    side = str(direction).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    bars = list(completed_bars or ())
    rejected = {"accepted": False, "plan_version": BI_PLAN_VERSION}
    if len(bars) < 36:
        return {**rejected, "reason": "insufficient_history"}
    try:
        for bar in bars:
            o, h, l, c = (float(bar[key]) for key in ("open", "high", "low", "close"))
            if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) or h < max(o, l, c) or l > min(o, h, c):
                return {**rejected, "reason": "invalid_ohlc"}
        current = float(bars[-1]["close"])
        live = current if live_price is None else float(live_price)
        if not math.isfinite(live) or live <= 0:
            return {**rejected, "reason": "invalid_live_price"}
    except (ValueError, TypeError, KeyError):
        return {**rejected, "reason": "invalid_ohlc"}

    analysis_bars = bars[-50:]
    days = bi_consolidation_days(analysis_bars) if range_days is None else int(range_days)
    range_bars = bars[-days:] if days >= 5 else bars[-15:]
    high = max(b["high"] for b in range_bars)
    low = min(b["low"] for b in range_bars)
    size = high - low
    if size / low * 100 < 1.0:
        return {**rejected, "reason": "range_too_narrow"}
    recent = bars[-10:]
    adr = sum((b["high"] - b["low"]) / b["close"] * 100 for b in recent) / len(recent)
    atr = calculate_wilder_atr(analysis_bars, period=5)
    if adr < 0.3 or atr <= 0:
        return {**rejected, "reason": "atr_too_small"}

    plan = {"direction": side, "level_model": BI_PLAN_VERSION}
    if side == "LONG":
        plan["Entry"] = round(high + max(atr * 0.1, size * 0.02), 2)
        plan["StopLoss"] = round(high - max(atr * 0.9, size * 0.10), 2)
        risk = max(0.01, plan["Entry"] - plan["StopLoss"])
        plan["TP1"] = round(high + max(size * 0.75, risk * 1.35), 2)
        plan["TP2"] = round(high + max(size * 1.618, risk * 2.25), 2)
        plan.update(stop_source="range_high_retest_invalidation", tp1_source="range_extension",
                    tp2_source="range_extension", entry_method="stop_breakout")
    else:
        if live < low * (1 - max(2 * atr / live, 0.03)):
            return {**rejected, "reason": "entry_too_extended"}
        if current < (high + low) / 2:
            plan["Entry"] = round(current, 2)
            plan["StopLoss"] = round(min(high, max(low + atr * 0.75, current + atr * 1.2)), 2)
            plan.update(stop_source="breakdown_reclaim_invalidation", entry_method="market_at_signal")
        else:
            plan["Entry"] = round(high * 0.995, 2)
            plan["StopLoss"] = round(high + atr * 0.5, 2)
            plan.update(stop_source="range_high_reclaim_invalidation", entry_method="limit_pullback")
        risk = max(0.01, plan["StopLoss"] - plan["Entry"])
        if plan["Entry"] > low and plan["Entry"] - low >= risk * 1.15:
            plan["TP1"] = round(low, 2)
        else:
            plan["TP1"] = round(max(0.01, min(low - size * 0.272, plan["Entry"] - 0.5 * risk)), 2)
        plan["TP2"] = round(max(0.01, min(low - size * 0.618, plan["TP1"] - 0.25 * risk)), 2)
        plan.update(tp1_source="range_low_support_or_extension", tp2_source="range_extension")

    if apply_structure:
        if as_of is None:
            # Production must supply its actual cutoff; historical callers must
            # supply the signal cutoff rather than accidentally using today.
            return {**rejected, "reason": "structure_cutoff_missing"}
        profile = build_vrvp_structure(bars, plan["Entry"], side, timeframe="1D", num_bins=24,
                                      min_bars=30, lookback=90, as_of=as_of,
                                      date_session_context="us_equity_regular")
        structure_atr = calculate_wilder_atr(bars, period=14, lookback=90) or atr
        plan = {**plan, **apply_vrvp_to_trade_setup(plan, profile, direction=side,
                                                  asset_type="stock_swing", atr=structure_atr)}
        if (
            str(plan.get("structure_status") or "").upper() in {"REJECT", "WAIT_BREAK_RECLAIM"}
            or plan.get("barrier_gate_active") is True
            or plan.get("tp1_is_projection") is True
            or str(plan.get("target_quality") or "").startswith("PROJECTION_ONLY")
        ):
            return {**plan, **rejected, "reason": "structural_barrier_blocked"}
    if side == "LONG" and (plan["Entry"] - live) / live > max(2 * atr / live, 0.03):
        return {**rejected, "reason": "entry_too_extended"}
    geometry = trade_geometry(plan["Entry"], plan["StopLoss"], plan["TP1"], plan["TP2"], side)
    if not geometry.get("valid") or geometry.get("rr", 0) < 1.2:
        return {**rejected, "reason": "invalid_geometry_or_rr"}
    return {**plan, "accepted": True, "reason": None, "plan_version": BI_PLAN_VERSION,
            "geometry": geometry, "range_days": days, "RangeHigh": round(high, 2),
            "RangeLow": round(low, 2), "atr5": atr, "RiskReward": round(geometry["rr"], 1)}
