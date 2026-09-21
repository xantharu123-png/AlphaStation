"""Causal, close-supported Cup shape checks for an already chosen cup window.

This is an operational pattern filter, not a probability of trading success.
Only the supplied, completed cup bars are read; handle/breakout/future prices
must not be included. Raw extrema keep the existing geometry evidence's exact
ranges and first-tie ordering. Smoothing is solely for noisy shape checks and
never replaces a chart anchor or a trading level.
"""
from collections.abc import Mapping
import math
from statistics import median


VERSION = "cup_shape_v1"

# Fractions below are deliberately coarse, scale-free operational safeguards,
# not claimed textbook constants or a fitted success model. Ordinary daily
# reversals are allowed; only substantial alternate structures are excluded.
MIN_CLOSE_DEPTH_SHARE = 0.60  # A wick cannot supply most of the apparent cup.
BOTTOM_ZONE = 0.20
MID_ZONE = 0.50
MIN_BOTTOM_CONCENTRATION = 0.50  # Linear V: .20/.50=.40; quadratic U: ~.63.
MIN_BOTTOM_DURATION = 0.08
MIN_SIDE_TRANSITION_DURATION = 0.06
RETEST_LOW_ZONE = 0.30
DEEP_REBOUND_ZONE = 0.65  # A near-rim rally separating deep lows is a W.


def _number(bar, name):
    value = bar.get(name, bar.get(name[0]))
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _trailing_median3(values):
    # Every value uses its own and at most two older bars. No centered window,
    # padding from a later handle, resampling, or future-bar read is involved.
    return [median(values[max(0, i - 2):i + 1]) for i in range(len(values))]


def validate_cup_shape(cup):
    """Return bounded scalar evidence, or None for unsupported/non-cup shape.

    ``cup`` is a list/tuple of 45..170 chronological OHLC mappings, excluding
    the handle. Long and short numeric OHLC aliases are supported. Open is
    optional (the shape uses high/low/close); if supplied it must be valid.
    Returned indices are zero-based positions in the ORIGINAL input, even
    when a median filters an isolated daily zigzag in the shape calculation.

    Existing depth (10..45%) and rim-balance (.86..1.16) bounds are retained.
    Shape additionally requires a close-supported, broad low region, gradual
    passage through both side walls, and no sustained near-rim rally between
    deep troughs. It does not require symmetry or monotonic daily movement.
    """
    if not isinstance(cup, (list, tuple)) or not 45 <= len(cup) <= 170:
        return None
    prices = []
    for bar in cup:
        if not isinstance(bar, Mapping):
            return None
        high, low, close = (_number(bar, field) for field in ("high", "low", "close"))
        if high is None or low is None or close is None or not low <= close <= high:
            return None
        if "open" in bar or "o" in bar:
            opening = _number(bar, "open")
            if opening is None or not low <= opening <= high:
                return None
        prices.append((high, low, close))

    length = len(cup)
    # Exactly mirrors modules.cup_pattern_evidence: never select a nicer raw
    # extremum or chronology than the one the user sees drawn on their chart.
    left_range = range(max(8, int(length * .32)))
    middle_range = range(int(length * .22), int(length * .78))
    right_range = range(int(length * .62), length)
    left_index = max(left_range, key=lambda i: prices[i][0])
    bottom_index = min(middle_range, key=lambda i: prices[i][1])
    right_index = max(right_range, key=lambda i: prices[i][0])
    if not left_index < bottom_index < right_index:
        return None

    left_lip, right_lip = prices[left_index][0], prices[right_index][0]
    lip = max(left_lip, right_lip)
    bottom = prices[bottom_index][1]
    depth = lip - bottom
    depth_pct = depth / lip * 100
    lip_ratio = right_lip / left_lip
    if not 10 <= depth_pct <= 45 or not .86 <= lip_ratio <= 1.16:
        return None

    closes = [row[2] for row in prices[left_index:right_index + 1]]
    smooth = _trailing_median3(closes)
    trough = min(smooth)
    close_rim = min(closes[0], closes[-1])
    close_depth = close_rim - trough
    if close_depth <= 0 or close_depth / depth < MIN_CLOSE_DEPTH_SHARE:
        return None
    heights = [(value - trough) / close_depth for value in smooth]
    span = len(heights)
    # A rim represented only by an isolated high/close spike is not a
    # recovered right wall. Two older closes must support most of the rise.
    if heights[-1] < .75:
        return None
    # The selected right rim may occur before the final cup bar. Do not hide
    # a second deep basin by validating only the history up to that rim; the
    # remaining cup must still hand off in the upper half to the handle.
    tail_closes = [row[2] for row in prices[right_index:]]
    if any(value < trough + close_depth * .50 for value in _trailing_median3(tail_closes)):
        return None

    bottom_points = [i for i, value in enumerate(heights) if value <= BOTTOM_ZONE]
    mid_points = [i for i, value in enumerate(heights) if value <= MID_ZONE]
    bottom_count = len(bottom_points)
    if bottom_count < max(3, math.ceil(span * MIN_BOTTOM_DURATION)):
        return None
    concentration = bottom_count / len(mid_points)
    if concentration < MIN_BOTTOM_CONCENTRATION:
        return None

    first_bottom, last_bottom = bottom_points[0], bottom_points[-1]
    # Count actual intermediate-price sessions, not elapsed time on an upper
    # shelf: a crash or one/two-session snapback is not a rounded side wall.
    left_transition = sum(.25 <= value <= .75 for value in heights[:first_bottom])
    right_transition = sum(.25 <= value <= .75 for value in heights[last_bottom + 1:])
    minimum_transition = max(3, math.ceil(span * MIN_SIDE_TRANSITION_DURATION))
    if min(left_transition, right_transition) < minimum_transition:
        return None

    deep_points = [i for i, value in enumerate(heights) if value <= RETEST_LOW_ZONE]
    between_troughs = heights[deep_points[0]:deep_points[-1] + 1]
    rebound_run = longest_rebound_run = 0
    for value in between_troughs:
        rebound_run = rebound_run + 1 if value >= DEEP_REBOUND_ZONE else 0
        longest_rebound_run = max(longest_rebound_run, rebound_run)
    if longest_rebound_run >= 2:
        return None

    # Preserve the pre-existing raw-low score input; close support is a
    # separate acceptance metric, not a silent change to score calibration.
    raw_bottom_zone = bottom + depth * .18
    rounded_bottom_bars = sum(prices[i][1] <= raw_bottom_zone for i in middle_range)
    if rounded_bottom_bars < 3:
        return None
    return {
        "version": VERSION,
        "left_rim_index": left_index,
        "bottom_index": bottom_index,
        "right_rim_index": right_index,
        "left_lip": left_lip,
        "right_lip": right_lip,
        "cup_lip": lip,
        "bottom": bottom,
        "depth_abs": depth,
        "depth_pct": depth_pct,
        "lip_ratio": lip_ratio,
        "rounded_bottom_bars": rounded_bottom_bars,
        "close_supported_bottom_bars": bottom_count,
        "close_depth_share": round(close_depth / depth, 6),
        "bottom_concentration": round(concentration, 6),
        "left_transition_bars": left_transition,
        "right_transition_bars": right_transition,
        "max_internal_rebound_share": round(max(between_troughs), 6),
    }
