"""Pure daily selection contract for real stock momentum breakouts.

Daily eligibility is deliberately not an execution signal. Live callers must
also provide completed intraday confirmation; daily backtests must not pretend
that this unavailable execution evidence was observed.
"""

import math


MOMENTUM_CONTRACT_VERSION = 1
CONFIRMATION_BUFFER = 0.001  # Existing stock 5m close-confirmation boundary.
MOMENTUM_BREAKOUT_TYPES = frozenset({"20D_HIGH_BREAKOUT", "10D_HIGH_BREAKOUT"})
MOMENTUM_SCAN_FILTERS = {
    "Change %": (2.0, 200.0), "RVOL": (1.5, 100.0),
    "Close Position": (0.50, 1.0), "Preis": (5.0, 100000.0),
}
MOMENTUM_MIN_DOLLAR_VOLUME = 750000


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def evaluate_momentum_breakout(history_metrics, *, price, change_pct, rvol, close_pos):
    """Select a real prior 20D/10D high break using existing quality floors."""
    metrics = history_metrics or {}
    price, change_pct, rvol, close_pos = map(_number, (price, change_pct, rvol, close_pos))
    reasons = []
    if not metrics.get("history_ok"):
        reasons.append("not_enough_daily_history")
    if (
        price is None or price <= 0 or any(value is None for value in (change_pct, rvol, close_pos))
        or (rvol is not None and rvol < 0)
        or (close_pos is not None and not 0 <= close_pos <= 1)
    ):
        reasons.append("invalid_momentum_inputs")
        return {"eligible": False, "reasons": reasons, "breakout_type": None,
                "breakout_level": None, "contract_version": MOMENTUM_CONTRACT_VERSION,
                "requires_intraday_confirmation": True}

    high20, high10 = (_number(metrics.get(key)) for key in ("high_20d", "high_10d"))
    breaks20 = bool(high20 and high20 > 0 and price >= high20 * (1 + CONFIRMATION_BUFFER))
    breaks10 = bool(high10 and high10 > 0 and price >= high10 * (1 + CONFIRMATION_BUFFER))
    holds20 = breaks20 and change_pct >= -0.2 and rvol >= 1.5 and close_pos >= 0.50
    holds10 = breaks10 and change_pct >= 0.4 and rvol >= 1.5 and close_pos >= 0.52
    breakout_type = "20D_HIGH_BREAKOUT" if holds20 else "10D_HIGH_BREAKOUT" if holds10 else None
    level = high20 if holds20 else high10 if holds10 else None

    if not (holds20 or holds10):
        if change_pct < -0.2:
            reasons.append("daily_momentum_too_small")
        if rvol < 1.5:
            reasons.append("rvol_below_breakout_threshold")
        if close_pos < 0.50:
            reasons.append("daily_close_not_near_high")
        reasons.append("no_momentum_breakout_structure")

    ema20, ema50, rsi14, change5d = (
        _number(metrics.get(key)) for key in ("ema20", "ema50", "rsi14", "change_5d")
    )
    if ema20 and price <= ema20 and not (holds10 or holds20):
        reasons.append("price_below_ema20")
    if ema20 and ema50 and price <= ema50 and ema20 < ema50 and not holds20:
        reasons.append("no_ema20_50_trend_reclaim")
    if rsi14 is not None and rsi14 < 45:
        reasons.append("rsi_too_weak_for_momentum")
    if rsi14 is not None and rsi14 > 90 and not holds20:
        reasons.append("rsi_overheated")
    if change5d is not None and change5d < -8 and not (holds10 or holds20):
        reasons.append("bounce_after_recent_selloff")
    return {"eligible": not reasons, "reasons": reasons, "breakout_type": breakout_type,
            "breakout_level": level, "contract_version": MOMENTUM_CONTRACT_VERSION,
            "requires_intraday_confirmation": True}


def cap_momentum_score(score, *, breakout_type, continuation_status):
    """Reapply semantic caps after every bonus, including legacy candidates."""
    value = max(0, min(100, int(score)))
    if breakout_type not in MOMENTUM_BREAKOUT_TYPES:
        value = min(value, 79)
    if continuation_status == "FAKEOUT_RISK":
        value = min(value, 64)
    elif continuation_status == "WICK_WATCH":
        value = min(value, 74)
    return value
