"""Shared, dependency-free helpers for backtest performance reporting."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple


def chronological_trade_key(trade: Dict[str, Any]) -> Tuple[str, str, str]:
    """Sort trades by economic occurrence, independent of ticker loop order."""
    primary_date = str(
        trade.get("entry_date")
        or trade.get("signal_date")
        or trade.get("exit_date")
        or ""
    )
    exit_date = str(trade.get("exit_date") or primary_date)
    ticker = str(trade.get("ticker") or trade.get("symbol") or "")
    return (primary_date, exit_date, ticker)


def profit_factor_metrics(
    gross_profit: Any,
    gross_loss: Any,
    *,
    precision: int = 2,
) -> Dict[str, Any]:
    """Return an honest Profit Factor representation.

    A profitable sample without losses has an unbounded Profit Factor. It is
    not 99 or 999. ``comparison_value`` is intentionally separate and exists
    only for internal threshold comparisons; it may be positive infinity and
    must never be serialized as the public metric.
    """
    try:
        profit = max(0.0, float(gross_profit or 0.0))
    except (TypeError, ValueError, OverflowError):
        profit = 0.0
    try:
        loss = max(0.0, float(gross_loss or 0.0))
    except (TypeError, ValueError, OverflowError):
        loss = 0.0
    if not math.isfinite(profit):
        profit = 0.0
    if not math.isfinite(loss):
        loss = 0.0

    if loss > 0:
        value = round(profit / loss, precision)
        return {
            "value": value,
            "display": f"{value:.{precision}f}",
            "unbounded": False,
            "comparison_value": value,
        }
    if profit > 0:
        return {
            "value": None,
            "display": "INF",
            "unbounded": True,
            "comparison_value": float("inf"),
        }
    return {
        "value": 0.0,
        "display": f"{0.0:.{precision}f}",
        "unbounded": False,
        "comparison_value": 0.0,
    }


def profit_factor_value(gross_profit: Any, gross_loss: Any) -> Optional[float]:
    """Convenience accessor for the public numeric Profit Factor value."""
    return profit_factor_metrics(gross_profit, gross_loss)["value"]
