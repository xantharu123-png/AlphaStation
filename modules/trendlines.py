"""Causal diagonal S/R evidence, independent of chart or scanner I/O.

A line becomes evidence only after three independent strict pivots have been
confirmed by closed right-hand candles. Its geometry and ATR band are frozen
when the second anchor becomes knowable. Later volatility cannot fit old
touches retroactively, and a later close break is terminal for that geometry.

The x-axis is the completed-candle index, not elapsed wall-clock time. That
matches a logical exchange-bar chart across nights/weekends. ``price_at_as_of``
is a line projection at the last *completed* bar, never an observed quote or
an assertion that an entry was executed. Session adaptation belongs to the
caller; normalization and completion checks are shared with ``level_zones``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .level_zones import (
    CompletedBar,
    _coerce_datetime,
    _normalized_timeframe,
    normalize_completed_bars,
)


MODEL = "causal_trendline_v1"
VERSION = 1
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
MIN_TOUCH_SEPARATION = PIVOT_LEFT + PIVOT_RIGHT + 1
MIN_ANCHOR_SPAN = 10
ATR_PERIOD = 14
ATR_TOUCH_FACTOR = 0.4
BREAK_BAND_FACTOR = 2.0


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _series_time(bar: CompletedBar, timestamp_mode: str) -> int:
    value = bar.closed_at if timestamp_mode == "close" else bar.opened_at
    return int(value.timestamp())


@dataclass(frozen=True)
class _Pivot:
    index: int
    confirmed_index: int
    price: float


def _strict_pivots(bars: Tuple[CompletedBar, ...], side: str) -> List[_Pivot]:
    pivots: List[_Pivot] = []
    for index in range(PIVOT_LEFT, len(bars) - PIVOT_RIGHT):
        pivot = bars[index]
        neighbours = (
            bars[index-PIVOT_LEFT:index]
            + bars[index+1:index+PIVOT_RIGHT+1]
        )
        if side == "support":
            valid = all(pivot.low < other.low for other in neighbours)
            price = pivot.low
        else:
            valid = all(pivot.high > other.high for other in neighbours)
            price = pivot.high
        if valid:
            pivots.append(_Pivot(index, index+PIVOT_RIGHT, price))
    return pivots


def _causal_atr(bars: Tuple[CompletedBar, ...], scale: str) -> List[float]:
    """Wilder ATR of each prefix; log mode uses dimensionless log ranges."""
    values: List[float] = []
    running = 0.0
    previous_close = None
    transform = math.log if scale == "log" else float
    for index, bar in enumerate(bars):
        high, low, close = transform(bar.high), transform(bar.low), transform(bar.close)
        true_range = high-low
        if previous_close is not None:
            true_range = max(true_range, abs(high-previous_close), abs(low-previous_close))
        previous_close = close
        if index < ATR_PERIOD:
            running += true_range
            value = running/(index+1)
        else:
            value = (values[-1]*(ATR_PERIOD-1)+true_range)/ATR_PERIOD
        values.append(value)
    return values


def _pivot_payload(pivot: _Pivot, bars: Tuple[CompletedBar, ...], mode: str) -> Dict[str, Any]:
    bar = bars[pivot.index]
    return {
        "bar_index": pivot.index,
        "confirmation_bar_index": pivot.confirmed_index,
        "time": _series_time(bar, mode),
        "price": pivot.price,
        "observed_at": _iso(bar.closed_at),
        "confirmed_at": _iso(bars[pivot.confirmed_index].closed_at),
    }


def _geometry_key(intercept: float, slope: float) -> Tuple[str, str]:
    # Co-linear later anchor pairs must not resurrect an already broken line.
    # Rounding is only for a deterministic equivalence key, never chart prices.
    return (format(intercept, ".12g"), format(slope, ".12g"))


def _candidate(
    bars: Tuple[CompletedBar, ...], pivots: List[_Pivot], first_pos: int,
    second_pos: int, side: str, atr: List[float], close_values: List[float], *, timeframe: str,
    cutoff: datetime, timestamp_mode: str, scale: str,
) -> Dict[str, Any] | None:
    first, second = pivots[first_pos], pivots[second_pos]
    if second.index-first.index < MIN_ANCHOR_SPAN:
        return None
    transform = math.log if scale == "log" else float
    inverse = math.exp if scale == "log" else float
    anchor_value = transform(first.price)
    slope = (transform(second.price)-anchor_value)/(second.index-first.index)
    intercept = anchor_value-slope*first.index
    tolerance = atr[second.confirmed_index]*ATR_TOUCH_FACTOR
    if not all(math.isfinite(value) for value in (intercept, slope, tolerance)) or tolerance <= 0:
        return None
    break_band = tolerance*BREAK_BAND_FACTOR
    epsilon = max(abs(anchor_value), abs(transform(second.price)), 1e-300)*1e-12

    def value_at(index: int) -> float:
        return intercept+slope*index

    def close_break(index: int) -> bool:
        diff = close_values[index]-value_at(index)
        # The frozen noise band, including the comparison epsilon, is based
        # on the anchor prefix, never later prices/volatility.
        return diff < -break_band-epsilon if side == "support" else diff > break_band+epsilon

    # A fit with no possible third confirmed independent contact need not
    # inspect every close in a large lookback. This only prunes impossible
    # candidates; it never advances a contact's actual confirmation time.
    if not any(
        pivot.index-second.index >= MIN_TOUCH_SEPARATION
        and abs(transform(pivot.price)-value_at(pivot.index)) <= tolerance+epsilon
        for pivot in pivots[second_pos+1:]
    ):
        return None

    # Validate the anchor prefix itself before accepting a third touch. A
    # hypothetical fit already broken while forming is not confirmed evidence.
    if any(close_break(index) for index in range(first.index, second.confirmed_index+1)):
        return None

    touches = [first, second]
    confirmed_index = None
    broken_index = None
    next_pivot_pos = second_pos+1
    for index in range(second.confirmed_index+1, len(bars)):
        if close_break(index):
            if confirmed_index is None:
                return None
            broken_index = index
            break
        # Only the pivot whose complete right-hand window is now available
        # can add evidence. Broken lines never collect recovery touches.
        while next_pivot_pos < len(pivots) and pivots[next_pivot_pos].confirmed_index <= index:
            pivot = pivots[next_pivot_pos]
            next_pivot_pos += 1
            if pivot.index-touches[-1].index < MIN_TOUCH_SEPARATION:
                continue
            difference = abs(transform(pivot.price)-value_at(pivot.index))
            if difference <= tolerance+epsilon:
                touches.append(pivot)
                if confirmed_index is None:
                    confirmed_index = index

    if confirmed_index is None:
        return None

    last_index = len(bars)-1
    drawing_end = broken_index if broken_index is not None else last_index
    try:
        last_value = inverse(value_at(last_index))
        end_value = inverse(value_at(drawing_end))
        confirmation_value = inverse(value_at(confirmed_index))
    except OverflowError:
        return None
    if not all(math.isfinite(value) and value > 0 for value in (end_value, confirmation_value)):
        return None
    last_value = last_value if math.isfinite(last_value) and last_value > 0 else None
    anchors = [_pivot_payload(pivot, bars, timestamp_mode) for pivot in (first, second)]
    identity = "|".join((
        MODEL, side, timeframe, scale, anchors[0]["observed_at"],
        format(first.price, ".17g"), anchors[1]["observed_at"], format(second.price, ".17g"),
    ))
    line_id = "tl_"+hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return {
        "id": line_id,
        "model": MODEL,
        "version": VERSION,
        "source_family": "trendline",
        "type": side,
        "status": "broken" if broken_index is not None else "active",
        "timeframe": timeframe,
        "scale": scale,
        "axis_basis": "completed_bar_index",
        "slope_per_bar": slope,
        "slope_unit": "log_price_per_bar" if scale == "log" else "price_per_bar",
        "observed_at": _iso(bars[first.index].closed_at),
        "confirmed_at": _iso(bars[confirmed_index].closed_at),
        "data_cutoff_at": _iso(bars[-1].closed_at),
        "requested_as_of": _iso(cutoff),
        "anchors": anchors,
        "touches": len(touches),
        "touch_evidence": [_pivot_payload(pivot, bars, timestamp_mode) for pivot in touches],
        "pivot_left": PIVOT_LEFT,
        "pivot_right": PIVOT_RIGHT,
        "minimum_touch_separation": MIN_TOUCH_SEPARATION,
        "tolerance": tolerance,
        "tolerance_unit": "log_price" if scale == "log" else "price",
        "tolerance_confirmed_at": _iso(bars[second.confirmed_index].closed_at),
        "atr_period": ATR_PERIOD,
        "break_band": break_band,
        "break_rule": "completed_close_beyond_frozen_band",
        "broken_at": _iso(bars[broken_index].closed_at) if broken_index is not None else None,
        "break_close": bars[broken_index].close if broken_index is not None else None,
        "price_at_as_of": last_value,
        "projection_only": True,
        "projection_basis": "last_completed_bar_index",
        "projection_at": _iso(bars[-1].closed_at),
        "price_at_confirmation": confirmation_value,
        # Render each verified bar, not only endpoints. A chart may retain an
        # explicitly unfinished historical candle between these bars. Its
        # extra logical slot must not shift our confirmed anchors/touches.
        "points": [
            {"time": _series_time(bars[index], timestamp_mode),
             "price": inverse(value_at(index))}
            for index in range(first.index, drawing_end + 1)
        ],
        "_geometry": _geometry_key(intercept, slope),
        "_intercept": intercept,
        "_last_index": last_index,
        "_span": touches[-1].index-first.index,
    }


def _same_line(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    """Collapse duplicate fits, not independent nearby parallel structures."""
    if first["_geometry"] == second["_geometry"]:
        return True
    first_contacts = {item["bar_index"] for item in first["touch_evidence"]}
    second_contacts = {item["bar_index"] for item in second["touch_evidence"]}
    shared = first_contacts & second_contacts
    if len(shared) < 3:
        return False
    band = min(first["tolerance"], second["tolerance"])
    # Linear/log affine geometry means endpoint comparisons bound the whole
    # shared contact interval; no noisy refit may rewrite a historic break.
    return all(
        abs((first["_intercept"]+first["slope_per_bar"]*index)
            -(second["_intercept"]+second["slope_per_bar"]*index)) <= band
        for index in (min(shared), max(shared), min(first["_last_index"], second["_last_index"]))
    )


def build_causal_trendlines(
    bars: Sequence[Mapping[str, Any]], timeframe: str, as_of: Any,
    timestamp_mode: str = "open", scale: str = "linear", max_per_side: int = 3,
) -> List[Dict[str, Any]]:
    """Return up to ``max_per_side`` dated, confirmed diagonal lines per side.

    Active lines are ranked before historical broken context. No current/open
    bar is allowed to change anchors, pivot confirmation, tolerance or state.
    ``scale='log'`` fits log-price per logical bar and returns actual prices;
    the caller must render it on a logarithmic price axis. Default is linear.

    ``points.time`` uses the normalized candle open (or close when explicitly
    requested). A session-adapting chart caller must map that canonical time
    back to its original chart timestamp rather than create extra x-axis bars.
    """
    mode = str(timestamp_mode or "open").strip().lower()
    scale = str(scale or "linear").strip().lower()
    if mode not in {"open", "close"}:
        raise ValueError("timestamp_mode must be 'open' or 'close'")
    if scale not in {"linear", "log"}:
        raise ValueError("scale must be 'linear' or 'log'")
    if isinstance(max_per_side, bool) or not isinstance(max_per_side, int) or max_per_side < 0:
        raise ValueError("max_per_side must be a non-negative integer")
    cutoff = _coerce_datetime(as_of)
    if max_per_side == 0:
        return []
    completed = normalize_completed_bars(
        bars, timeframe=timeframe, as_of=cutoff, timestamp_mode=mode,
    )
    if len(completed) < PIVOT_LEFT+PIVOT_RIGHT+1:
        return []
    timeframe = _normalized_timeframe(timeframe)
    atr = _causal_atr(completed, scale)
    transform = math.log if scale == "log" else float
    close_values = [transform(bar.close) for bar in completed]
    result: List[Dict[str, Any]] = []
    for side in ("support", "resistance"):
        pivots = _strict_pivots(completed, side)
        candidates: List[Dict[str, Any]] = []
        for first_pos in range(len(pivots)-2):
            for second_pos in range(first_pos+1, len(pivots)-1):
                line = _candidate(
                    completed, pivots, first_pos, second_pos, side, atr, close_values,
                    timeframe=timeframe, cutoff=cutoff, timestamp_mode=mode, scale=scale,
                )
                if line is not None:
                    candidates.append(line)
        # Keep the first known version of identical geometry, even if later
        # anchors would avoid its historic break and fit a recovery as active.
        unique = []
        for line in sorted(candidates, key=lambda item: (item["confirmed_at"], item["observed_at"], item["id"])):
            if not any(_same_line(known, line) for known in unique):
                unique.append(line)
        unique.sort(key=lambda item: (
            item["status"] != "active", -item["touches"], -item["_span"],
            item["confirmed_at"], item["id"],
        ))
        for line in unique[:max_per_side]:
            del line["_geometry"]
            del line["_intercept"]
            del line["_last_index"]
            del line["_span"]
            result.append(line)
    return result
