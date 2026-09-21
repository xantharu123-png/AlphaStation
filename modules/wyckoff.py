"""Pure, completed-bar Wyckoff v3 structures and separately gated entry plans.

Reversal and continuation evidence, A--E and local swings are chart context.
Only a volume-confirmed breakout and fresh retest authorize an entry trigger.
Versioned thresholds are uncalibrated assumptions, not win probabilities.
Callers retain structural, execution, risk, cost and mail-delivery gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping, Sequence

from modules.indicators import calculate_atr_14
from modules.level_zones import CompletedBar, normalize_completed_bars
from modules.wyckoff_structure import detect_structures
from modules.wyckoff_swings import PARAMETERS


MODEL = "causal_wyckoff_v3"
UTC = timezone.utc


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("as_of must have an explicit timezone")
        return value.astimezone(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("as_of must have an explicit timezone")
        return parsed.astimezone(UTC)
    raise ValueError("as_of must be an explicit timezone-aware datetime or ISO timestamp")


def _completed_input(raw_bars, *, cutoff, timeframe, timestamp_mode):
    """Use the canonical clock, but do not silently fabricate/drop bad evidence."""
    completed = []
    for raw in raw_bars:
        if isinstance(raw, CompletedBar):
            try:
                opened, closed = _utc(raw.opened_at), _utc(raw.closed_at)
            except (TypeError, ValueError, OverflowError):
                return (), "invalid_bar_timestamp"
            if closed > cutoff:
                continue
            if opened > closed:
                return (), "invalid_bar_timestamp"
            clock = CompletedBar(opened, closed, raw.open, raw.high, raw.low, raw.close, raw.volume)
            values = (raw.open, raw.high, raw.low, raw.close, raw.volume)
        elif isinstance(raw, Mapping):
            # A harmless OHLCV clock probe distinguishes unfinished/future bars
            # from malformed *completed* price/volume data before normalization.
            probe = {**raw, "open": 1., "high": 1., "low": 1., "close": 1., "volume": 1.}
            clocks = normalize_completed_bars([probe], timeframe=timeframe, as_of=cutoff,
                                             timestamp_mode=timestamp_mode)
            if not clocks:
                flag = next((raw[key] for key in ("is_closed", "complete", "completed", "final") if key in raw), None)
                if flag is not None and str(flag).lower() in {"false", "0", "no", "n", "open"}:
                    continue
                # Valid clocks beyond the requested cutoff are ignored. A row
                # without a verifiable clock cannot participate in this model.
                future = normalize_completed_bars([probe], timeframe=timeframe,
                    as_of=datetime.max.replace(tzinfo=UTC), timestamp_mode=timestamp_mode)
                if future:
                    continue
                return (), "invalid_bar_timestamp"
            clock = clocks[0]
            values = tuple(raw.get(long, raw.get(short)) for long, short in (
                ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v")))
        else:
            return (), "invalid_bar_payload"
        try:
            if any(isinstance(value, bool) for value in values):
                raise ValueError
            open_, high, low, close, volume = (float(value) for value in values)
            if not all(math.isfinite(value) for value in (open_, high, low, close, volume)):
                raise ValueError
            if volume < 0:
                return (), "invalid_bar_volume"
            if min(open_, high, low, close) <= 0 or high < max(open_, close, low) or low > min(open_, close, high):
                return (), "invalid_bar_prices"
        except (TypeError, ValueError, OverflowError):
            return (), "invalid_bar_value"
        completed.append(CompletedBar(clock.opened_at, clock.closed_at, open_, high, low, close, volume))
    by_close = {}
    for bar in completed:
        signature = (bar.opened_at, bar.open, bar.high, bar.low, bar.close, bar.volume)
        previous = by_close.get(bar.closed_at)
        if previous is not None and previous[0] != signature:
            return (), "conflicting_completed_bars"
        by_close[bar.closed_at] = (signature, bar)
    return tuple(by_close[key][1] for key in sorted(by_close)), None


def analyze_wyckoff(bars: Sequence[Mapping[str, Any]], *, as_of, timeframe: str,
                    direction: str = "ALL", timestamp_mode: str = "open", minimum_bars: int = 60):
    """Return causal chart context and separately gated Wyckoff entry evidence.

    Daily equity callers must supply actual market-session ``close_time``;
    this generic core never guesses an exchange session or reads a live clock.
    Missing/invalid volume blocks analysis, while actual zero-volume bars are
    retained as non-evidence (never interpreted as successful low-volume tests).
    """
    cutoff = _utc(as_of)
    tf = str(timeframe).strip().upper()
    if not re.fullmatch(r"[1-9][0-9]*[MHDW]", tf):
        raise ValueError("timeframe must be explicit, e.g. 1D, 4H or 15M")
    side = str(direction).strip().upper()
    if side not in {"LONG", "SHORT", "ALL"}:
        raise ValueError("direction must be LONG, SHORT or ALL")
    if timestamp_mode not in {"open", "close"}:
        raise ValueError("timestamp_mode must be open or close")
    result = {"model": MODEL, "status": "ok", "reason": None, "as_of": _iso(cutoff),
              "timeframe": tf, "bars_used": 0, "latest_completed_at": None, "patterns": []}
    completed, error = _completed_input(tuple(bars or ()), cutoff=cutoff, timeframe=tf,
                                        timestamp_mode=timestamp_mode)
    if error:
        result.update(status="invalid_data", reason=error)
        return result
    result.update(bars_used=len(completed), latest_completed_at=_iso(completed[-1].closed_at) if completed else None)
    if len(completed) < max(60, int(minimum_bars)):
        result.update(status="insufficient_data", reason="minimum_completed_bars_missing")
        return result
    if not any(bar.volume > 0 for bar in completed):
        result.update(status="invalid_data", reason="positive_volume_evidence_missing")
        return result
    atr, _ = calculate_atr_14([bar.to_dict() for bar in completed])
    if not math.isfinite(atr) or atr <= 0:
        result.update(status="invalid_data", reason="atr_unavailable")
        return result
    if len(completed) > PARAMETERS["maximum_analysis_bars"]:
        result.update(status="insufficient_data", reason="analysis_window_exceeds_bounded_model")
        return result
    all_patterns, evidence = detect_structures(completed, tf)
    result.update(parameter_version=PARAMETERS["version"], parameters=dict(PARAMETERS),
                  swings=evidence["swings"], provisional_swing=evidence["provisional_swing"],
                  ambiguous_pivot_times=evidence["ambiguous_times"],
                  history_context={"status": "available_history_only", "window_start_at": _iso(completed[0].closed_at),
                                   "available_bars": len(completed), "may_be_truncated": True,
                                   "prior_window_context": "incomplete_history",
                                   "required_anchors": "observed" if all_patterns else "not_observed",
                                   "missing_anchor_policy": "no_reconstructed_structure_or_trigger"})
    result["patterns"] = [pattern for pattern in all_patterns if side == "ALL" or pattern["direction"] == side]
    return result


__all__ = ["MODEL", "analyze_wyckoff"]
