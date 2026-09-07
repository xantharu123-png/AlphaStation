"""The shared Fibonacci selector retires origin-breached swing projections."""

from datetime import datetime, timedelta, timezone

import pytest

from modules.fibonacci_levels import select_confirmed_swing_leg
from modules.level_zones import normalize_completed_bars


BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _bars(ranges, direction="LONG"):
    """Mirror LONG fixtures around 100 for exact, symmetric SHORT cases."""
    rows = []
    for index, (high, low) in enumerate(ranges):
        if direction == "SHORT":
            high, low = 200 - low, 200 - high
        close = (high + low) / 2
        rows.append({
            "timestamp": BASE + timedelta(hours=index),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
        })
    return rows


def _initial_leg(direction):
    # Low at index 1 -> high at index 3, confirmed by completed index 4.
    return _bars([(12, 10), (11, 8), (13, 10), (17, 13), (15, 11)], direction)


def _select(rows, direction, *, as_of=None, **kwargs):
    return select_confirmed_swing_leg(
        rows,
        direction=direction,
        timeframe="1H",
        as_of=as_of or BASE + timedelta(hours=20),
        pivot_left=1,
        pivot_right=1,
        **kwargs,
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_completed_wick_breach_retires_leg_even_when_close_recovers(direction):
    rows = _initial_leg(direction)
    intact = _select(rows, direction)
    assert intact is not None

    breach = _bars([(14, 7)], direction)[0]
    breach["timestamp"] = BASE + timedelta(hours=5)
    assert (
        breach["close"] > intact.start_price
        if direction == "LONG"
        else breach["close"] < intact.start_price
    )
    assert _select(rows + [breach], direction) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_origin_touch_is_not_a_breach(direction):
    rows = _initial_leg(direction)
    intact = _select(rows, direction)
    touch = _bars([(14, 8)], direction)[0]
    touch["timestamp"] = BASE + timedelta(hours=5)

    leg = _select(rows + [touch], direction)

    assert leg is not None
    assert leg.leg_id == intact.leg_id
    assert leg.provenance["origin_breach_policy"] == "strict_completed_bar_wick_after_end"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_origin_breach_in_endpoint_confirmation_bar_is_rejected(direction):
    rows = _bars([(12, 10), (11, 8), (13, 10), (17, 13), (15, 7)], direction)

    assert _select(rows, direction) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("minimum_move_atr", [0, 1])
def test_broken_selected_leg_cannot_fall_back_to_older_start_or_end(
    direction, minimum_move_atr
):
    rows = _bars([
        (10, 8), (9, 5), (15, 9), (20, 13), (16, 12),
        (15, 10), (17, 12), (18, 13), (16, 12),
    ], direction)
    intact = _select(rows, direction, minimum_move_atr=minimum_move_atr, atr=8)
    assert intact is not None
    assert (intact.start_pivot_index, intact.end_pivot_index) == (5, 7)
    # The newer origin (10) breaks, while older origin (5) remains untouched.
    breach = _bars([(14, 9)], direction)[0]
    breach["timestamp"] = BASE + timedelta(hours=9)

    assert _select(
        rows + [breach], direction, minimum_move_atr=minimum_move_atr, atr=8
    ) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_existing_minimum_atr_candidate_selection_is_preserved(direction):
    rows = _bars([
        (10, 8), (9, 5), (15, 9), (20, 13), (16, 12),
        (15, 10), (17, 12), (18, 13), (16, 12), (14, 9),
    ], direction)
    # 10 -> 18 is below the existing 9-point minimum. The eligible 5 -> 18
    # leg remains intact; lifecycle validation does not alter ATR eligibility.
    leg = _select(rows, direction, minimum_move_atr=1, atr=9)

    assert leg is not None
    assert (leg.start_pivot_index, leg.end_pivot_index) == (1, 7)
    assert leg.magnitude == 13
    assert _select(rows, direction, minimum_move_atr=1, atr=16) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("excluded_kind", ["open", "future", "not_yet_closed"])
def test_uncompleted_or_future_origin_breach_cannot_change_snapshot(direction, excluded_kind):
    rows = _initial_leg(direction)
    cutoff = BASE + timedelta(hours=6)
    breach = _bars([(14, 7)], direction)[0]
    breach["timestamp"] = BASE + timedelta(hours=5)
    if excluded_kind == "open":
        breach["is_closed"] = False
    elif excluded_kind == "future":
        breach["timestamp"] = BASE + timedelta(hours=9)
    else:
        breach["timestamp"] = cutoff - timedelta(minutes=30)

    expected = _select(rows, direction, as_of=cutoff)
    actual = _select(rows + [breach], direction, as_of=cutoff)

    assert expected is not None
    assert actual is not None
    assert actual.to_dict() == expected.to_dict()


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_breach_takes_effect_exactly_when_bar_closes(direction):
    rows = _initial_leg(direction)
    breach = _bars([(14, 7)], direction)[0]
    breach["timestamp"] = BASE + timedelta(hours=5)
    rows.append(breach)

    assert _select(rows, direction, as_of=BASE + timedelta(hours=6, microseconds=-1)) is not None
    assert _select(rows, direction, as_of=BASE + timedelta(hours=6)) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_new_confirmed_leg_can_replace_retired_leg_but_not_before_confirmation(direction):
    rows = _bars([
        (12, 10), (11, 8), (13, 10), (17, 13), (15, 11),
        (14, 7), (15, 10), (16, 12), (13, 10),
    ], direction)
    assert _select(rows, direction, as_of=BASE + timedelta(hours=8)) is None

    leg = _select(rows, direction, as_of=BASE + timedelta(hours=9))

    assert leg is not None
    assert (leg.start_pivot_index, leg.end_pivot_index) == (5, 7)
    assert leg.start_at < leg.end_at < leg.confirmed_at
    assert leg.confirmed_at == BASE + timedelta(hours=9)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_lifecycle_uses_completed_normalized_indices_not_raw_input_indices(direction):
    rows = _initial_leg(direction)
    breach = _bars([(14, 7)], direction)[0]
    breach["timestamp"] = BASE + timedelta(hours=5)
    rows.append(breach)
    # Raw slicing after end_pivot_index would omit the breach at position 0.
    shuffled = [rows[5], rows[2], rows[4], rows[0], rows[3], rows[1], dict(rows[2])]
    ignored = dict(breach, is_closed=False, timestamp=BASE - timedelta(hours=1))
    shuffled.insert(2, ignored)

    assert _select(shuffled, direction) is None
    completed = normalize_completed_bars(shuffled, timeframe="1H", as_of=BASE + timedelta(hours=20))
    assert len(completed) == 6
    assert _select(completed, direction) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_pre_origin_extreme_cannot_invalidate_a_later_leg(direction):
    rows = _bars([(12, 1), (13, 10), (11, 8), (14, 10), (17, 13), (15, 11)], direction)

    leg = _select(list(reversed(rows)), direction)

    assert leg is not None
    assert (leg.start_pivot_index, leg.end_pivot_index) == (2, 4)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_explicit_close_timestamps_follow_the_same_lifecycle(direction):
    rows = _initial_leg(direction)
    breach = _bars([(14, 7)], direction)[0]
    breach["timestamp"] = BASE + timedelta(hours=5)
    rows.append(breach)
    for row in rows:
        row["close_time"] = row["timestamp"] + timedelta(hours=1)

    assert _select(rows, direction, as_of=BASE + timedelta(hours=6)) is None
