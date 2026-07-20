"""Canonical volume-baseline and relative-volume primitives."""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Optional


def _valid_volume(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def historical_volume_baseline(
    volumes: Iterable[Any],
    *,
    lookback: int = 20,
    method: str = "mean",
    minimum_periods: int = 1,
) -> Optional[float]:
    """Calculate a baseline from completed historical bars only.

    Callers pass history without the signal/current bar. Missing, zero and
    non-finite volumes are excluded instead of silently becoming a baseline
    of one share.
    """
    size = max(1, int(lookback or 1))
    # The lookback is a time window, not a quota of valid observations. Slice
    # first so missing recent bars cannot be silently replaced by older data.
    raw_window = list(volumes)[-size:]
    cleaned = []
    for item in raw_window:
        value = _valid_volume(item)
        if value is not None:
            cleaned.append(value)
    if len(cleaned) < max(1, int(minimum_periods or 1)):
        return None
    if str(method).lower() == "median":
        return float(statistics.median(cleaned))
    return float(sum(cleaned) / len(cleaned))


def completed_bar_rvol(
    current_volume: Any,
    history_volumes: Iterable[Any],
    *,
    lookback: int = 20,
    method: str = "mean",
    minimum_periods: int = 1,
) -> float:
    """Return RVOL without self-contamination; invalid baselines fail closed."""
    current = _valid_volume(current_volume)
    baseline = historical_volume_baseline(
        history_volumes,
        lookback=lookback,
        method=method,
        minimum_periods=minimum_periods,
    )
    if current is None or baseline is None or baseline <= 0:
        return 0.0
    return current / baseline


def project_partial_rvol(raw_rvol: Any, expected_fraction: Any) -> float:
    """Project partial-session RVOL using an explicit expected volume share."""
    try:
        value = max(0.0, float(raw_rvol or 0.0))
        fraction = float(expected_fraction)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(value) or not math.isfinite(fraction):
        return 0.0
    if fraction >= 1.0:
        return value
    if fraction <= 0:
        return 0.0
    return value / max(fraction, 0.01)
