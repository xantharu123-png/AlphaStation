"""Causal completed-daily adapter for the shared live Momentum selection core.

This is selection parity only. Daily bars do not supply the live intraday
confirmation, context or structural execution plan; exits remain a named proxy.
"""

import math
from datetime import date

from modules.indicators import calculate_ema_series, calculate_rsi_from_bars
from modules.stock_momentum_contract import evaluate_momentum_breakout
from modules.volume_metrics import completed_bar_rvol


def daily_bar_is_explicitly_incomplete(row):
    """Any explicit negative completion flag wins over conflicting positives."""
    return any(str(row.get(key, "true")).strip().lower() in {"false", "0", "open", "no", "n"}
               for key in ("is_closed", "complete", "completed", "final"))


def evaluate_daily_momentum(bars, signal_idx):
    # Imported here so the shared module remains the sole profile owner.
    from modules.stock_momentum_contract import MOMENTUM_SCAN_FILTERS, MOMENTUM_MIN_DOLLAR_VOLUME

    if signal_idx < 20 or signal_idx >= len(bars):
        return None
    prefix = bars[:signal_idx + 1]
    try:
        for row in prefix:
            if any(isinstance(row[key], bool) for key in ("open", "high", "low", "close", "volume")):
                return None
            values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
            open_, high, low, close, volume = values
            if (not all(math.isfinite(value) for value in values)
                    or min(open_, high, low, close) <= 0 or volume <= 0
                    or high < max(open_, close, low) or low > min(open_, close, high)):
                return None
            if daily_bar_is_explicitly_incomplete(row):
                return None
        dates = [str(row.get("date") or "") for row in prefix]
        for value in dates:
            date.fromisoformat(value)
        if any(not value for value in dates) or any(a >= b for a, b in zip(dates, dates[1:])):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    prior, today = prefix[:-1], prefix[-1]
    price = float(today["close"])
    change = (price / float(prior[-1]["close"]) - 1) * 100
    day_range = float(today["high"]) - float(today["low"])
    close_pos = (price - float(today["low"])) / day_range if day_range else 0.5
    raw_rvol = completed_bar_rvol(today["volume"], (row["volume"] for row in prior[-20:]),
                                  lookback=20, minimum_periods=10)
    if raw_rvol is None:
        return None
    rvol = min(round(raw_rvol, 2), 50.0)
    profile_values = {"Preis": price, "Change %": change, "RVOL": rvol, "Close Position": close_pos}
    if any(not bounds[0] <= profile_values[key] <= bounds[1]
           for key, bounds in MOMENTUM_SCAN_FILTERS.items()):
        return None
    if price * float(today["volume"]) < MOMENTUM_MIN_DOLLAR_VOLUME:
        return None
    closes = [float(row["close"]) for row in prefix]
    def ema(period):
        return next((value for value in reversed(calculate_ema_series(closes, period)) if value is not None), None)
    history = {
        "history_ok": len(prior) >= 20,
        "high_10d": max(float(row["high"]) for row in prior[-10:]),
        "high_20d": max(float(row["high"]) for row in prior[-20:]),
        "ema20": ema(20), "ema50": ema(50),
        "rsi14": calculate_rsi_from_bars(prefix[-40:], 14),
        # At a completed daily close, the live metric's completed array already
        # includes today; it must not append a duplicate active bar.
        "change_5d": (price / float(prefix[-5]["close"]) - 1) * 100,
    }
    selected = evaluate_momentum_breakout(history, price=price, change_pct=change,
                                         rvol=rvol, close_pos=close_pos)
    if not selected["eligible"]:
        return None
    return {"price": price, "change_pct": change, "rvol": rvol, "close_pos": close_pos,
            "history_metrics": history, "momentum_selection": selected,
            "selection_scope": "completed_daily_inputs_not_live_execution",
            "live_equivalent": False}
