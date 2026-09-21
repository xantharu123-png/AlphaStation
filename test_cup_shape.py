"""Offline shape regressions: jagged/asymmetric bowls, not perfect curves."""
from copy import deepcopy
import math
import random

import pytest

from modules.cup_shape import validate_cup_shape


def _bar(close, *, low=None, high=None):
    return {"open": close * .997, "high": close * 1.012 if high is None else high,
            "low": close * .988 if low is None else low, "close": close}


def _textbook_cup():
    closes = [100 - 24 * i / 27 for i in range(28)]
    closes += [75 + 2 * abs((i - 13) / 13) for i in range(26)]
    closes += [77 + 22.5 * i / 35 for i in range(36)]
    return [_bar(close) for close in closes]


def _bowl(*, length=91, bottom_at=.5, seed=None):
    rng = random.Random(seed)
    bottom = round((length - 1) * bottom_at)
    closes = []
    for i in range(length):
        distance = (bottom - i) / bottom if i <= bottom else (i - bottom) / (length - 1 - bottom)
        close = 75 + 25 * distance ** 2
        if seed is not None and i not in (0, length - 1):
            close += rng.uniform(-1.1, 1.1)
        closes.append(close)
    return [_bar(close) for close in closes]


def _interpolate(knots, length=91):
    closes = []
    for i in range(length):
        for (start, price), (end, end_price) in zip(knots, knots[1:]):
            if start <= i <= end:
                closes.append(price + (end_price - price) * (i - start) / (end - start))
                break
    return [_bar(close) for close in closes]


def test_textbook_shape_keeps_exact_raw_anchors_and_score_count():
    bars = _textbook_cup()
    result = validate_cup_shape(bars)
    assert result is not None
    assert (result["left_rim_index"], result["bottom_index"], result["right_rim_index"]) == (0, 41, 89)
    assert result["cup_lip"] == bars[0]["high"]
    assert result["bottom"] == bars[41]["low"]
    middle = bars[int(len(bars) * .22):int(len(bars) * .78)]
    assert result["rounded_bottom_bars"] == sum(bar["low"] <= result["bottom"] + result["depth_abs"] * .18 for bar in middle)


@pytest.mark.parametrize("bottom_at", [.28, .4, .5, .65])
@pytest.mark.parametrize("seed", [None, 1, 4, 19, 72])
def test_rounded_bowl_allows_asymmetry_and_daily_zigzags(bottom_at, seed):
    bars = _bowl(bottom_at=bottom_at, seed=seed)
    assert validate_cup_shape(bars) is not None
    if seed is not None:
        bottom = min(range(len(bars)), key=lambda i: bars[i]["close"])
        # These positives really contain counter-trend daily movement.
        assert any(bars[i]["close"] > bars[i - 1]["close"] for i in range(1, bottom))
        assert any(bars[i]["close"] < bars[i - 1]["close"] for i in range(bottom + 1, len(bars)))


def test_flat_price_with_three_isolated_low_wicks_is_not_a_cup():
    bars = [_bar(99) for _ in range(91)]
    for i in (25, 45, 65):
        bars[i]["low"] = 74
    assert validate_cup_shape(bars) is None


@pytest.mark.parametrize("bottom_at", [30, 45, 60])
def test_long_linear_v_is_not_rounded_even_with_multiple_low_bars(bottom_at):
    bars = _interpolate([(0, 100), (bottom_at, 75), (90, 100)])
    assert sum(bar["low"] < 80 for bar in bars) > 3
    assert validate_cup_shape(bars) is None


def test_shelf_then_sharp_recovery_is_not_a_rounded_right_wall():
    bars = _interpolate([(0, 100), (28, 77), (67, 75), (69, 100), (90, 100)])
    assert validate_cup_shape(bars) is None


def test_sharp_drop_then_slow_recovery_is_not_a_rounded_left_wall():
    bars = _interpolate([(0, 100), (20, 100), (22, 75), (58, 76), (90, 100)])
    assert validate_cup_shape(bars) is None


def test_full_depth_w_with_broad_troughs_is_not_one_cup():
    bars = _interpolate([(0, 100), (20, 76), (30, 75), (45, 99), (60, 75), (70, 76), (90, 100)])
    assert validate_cup_shape(bars) is None


def test_late_second_basin_cannot_hide_after_the_selected_right_rim():
    # First trough is slightly deeper so raw bottom remains BEFORE the
    # highest right-rim candle. The later basin must nevertheless be checked.
    first_bowl = _bowl(length=61)
    late_basin = _interpolate([(0, 98), (12, 76), (19, 76), (29, 99)], length=30)
    bars = first_bowl + late_basin
    assert validate_cup_shape(bars) is None


def test_square_bottom_with_vertical_walls_is_not_a_rounded_cup():
    bars = [_bar(close) for close in [100] * 25 + [75] * 40 + [99.5] * 25]
    assert validate_cup_shape(bars) is None


def test_multiple_sinusoidal_basins_are_not_one_cup():
    bars = [_bar(100 - 25 * abs(math.sin(math.pi * i / 44.5))) for i in range(90)]
    assert validate_cup_shape(bars) is None


def test_original_fixture_with_alternating_daily_noise_stays_valid():
    bars = _textbook_cup()
    for i, bar in enumerate(bars[:-1]):
        offset = .65 * math.sin(i * 2.3)
        for field in ("open", "high", "low", "close"):
            bar[field] += offset
    assert validate_cup_shape(bars) is not None


def test_moderate_bottom_retest_is_allowed_without_near_rim_rally():
    bars = _textbook_cup()
    for i in range(31, 51):
        bars[i] = _bar(76 + 4 * math.sin((i - 31) * math.pi / 19))
    assert validate_cup_shape(bars) is not None


def test_scale_invariant_and_supports_short_aliases():
    baseline = validate_cup_shape(_bowl(seed=19))
    assert baseline is not None
    for scale in (.001, .1, 10, 10000):
        bars = [{key[0]: value * scale for key, value in bar.items()} for bar in _bowl(seed=19)]
        result = validate_cup_shape(bars)
        assert result is not None
        for key in ("left_rim_index", "bottom_index", "right_rim_index", "rounded_bottom_bars",
                    "close_supported_bottom_bars", "left_transition_bars", "right_transition_bars"):
            assert result[key] == baseline[key]
        assert result["depth_pct"] == pytest.approx(baseline["depth_pct"])


@pytest.mark.parametrize("bad", [None, True, False, float("nan"), float("inf"), -float("inf"), 0, -1, "NaN", "invalid"])
@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_invalid_prices_fail_closed_without_mutation(field, bad):
    bars = _textbook_cup()
    bars[40][field] = bad
    assert validate_cup_shape(bars) is None


def test_invalid_ohlc_order_fails_closed():
    bars = _textbook_cup()
    bars[40]["high"] = bars[40]["close"] - 1
    assert validate_cup_shape(bars) is None


def test_input_size_is_bounded_and_bars_are_not_removed_or_reordered():
    assert validate_cup_shape(_bowl(length=44)) is None
    assert validate_cup_shape(_bowl(length=171)) is None
    bars = _bowl(seed=4)
    before = deepcopy(bars)
    assert validate_cup_shape(bars) is not None
    assert bars == before
    bars[41] = None
    assert validate_cup_shape(bars) is None


def test_first_tie_anchor_order_matches_descriptive_geometry():
    bars = _textbook_cup()
    bars[1]["high"] = bars[0]["high"]
    bars[42]["low"] = bars[41]["low"]
    result = validate_cup_shape(bars)
    assert result is not None
    assert result["left_rim_index"] == 0
    assert result["bottom_index"] == 41


def test_inverted_raw_rim_bottom_chronology_is_not_repaired():
    bars = _textbook_cup()
    bars[61] = _bar(100, high=101.2, low=99)
    bars[65] = _bar(71, high=72, low=70)
    assert validate_cup_shape(bars) is None


def test_shape_anchors_match_the_separate_descriptive_evidence_builder():
    from datetime import date, timedelta
    from modules.cup_pattern_evidence import build_cup_geometry_evidence

    cup = _bowl(bottom_at=.4, seed=72)
    shape = validate_cup_shape(cup)
    assert shape is not None
    segment = cup + [_bar(close) for close in (99, 96, 97, 98, 103)]
    for i, bar in enumerate(segment):
        bar["date"] = (date(2025, 1, 1) + timedelta(days=i)).isoformat()
    evidence = build_cup_geometry_evidence(
        segment, cup_length=len(cup), handle_length=5,
        session_getter=lambda bar: bar["date"],
        number_getter=lambda bar, long_key, short_key: bar.get(long_key, bar.get(short_key)),
    )
    assert evidence["status"] == "available"
    for name, index_key, price_key in (("left_rim", "left_rim_index", "left_lip"),
                                       ("bottom", "bottom_index", "bottom"),
                                       ("right_rim", "right_rim_index", "right_lip")):
        assert evidence["anchors"][name]["index"] == shape[index_key]
        assert evidence["anchors"][name]["price"] == shape[price_key]


def test_validator_reads_only_supplied_cup_and_evidence_is_bounded():
    cup = _textbook_cup()
    history = cup + [_bar(1000000)] * 40
    result = validate_cup_shape(history[:len(cup)])
    assert result == validate_cup_shape(cup)
    assert len(result) <= 20
    assert all(isinstance(value, (str, int, float)) for value in result.values())
