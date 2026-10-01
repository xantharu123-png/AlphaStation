"""Offline regressions for completed-bar, causally confirmed diagonal levels."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from modules.trendlines import build_causal_trendlines


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bars(count=80, *, pivot_prices=(100.0, 101.5, 103.0), indices=(10, 25, 40)):
    rows = []
    for index in range(count):
        low = 108.0 + index * 0.1
        rows.append({
            "time": int((BASE + timedelta(hours=index)).timestamp()),
            "open": low + 1.0,
            "high": low + 2.0,
            "low": low,
            "close": low + 1.0,
            "volume": 1_000,
            "is_closed": True,
        })
    for index, low in zip(indices, pivot_prices):
        if index < count:
            rows[index]["low"] = low
    return rows


def _as_of(rows):
    return datetime.fromtimestamp(rows[-1]["time"], tz=timezone.utc) + timedelta(hours=1)


def _run(rows, **kwargs):
    return build_causal_trendlines(rows, timeframe="1H", as_of=_as_of(rows), **kwargs)


def _side(lines, side):
    return [line for line in lines if line["type"] == side]


def _mirror(rows, ceiling=250.0):
    return [
        {**row, "open": ceiling-row["open"], "high": ceiling-row["low"],
         "low": ceiling-row["high"], "close": ceiling-row["close"]}
        for row in rows
    ]


def test_three_confirmed_touches_have_dated_causal_evidence_and_chart_points():
    rows = _bars()
    line = _side(_run(rows), "support")[0]
    assert line["status"] == "active"
    assert line["touches"] == 3
    assert line["source_family"] == "trendline"
    assert line["timeframe"] == "1H"
    assert line["model"] == "causal_trendline_v1"
    assert line["axis_basis"] == "completed_bar_index"
    assert line["scale"] == "linear"
    assert line["points"][0] == {"time": rows[10]["time"], "price": 100.0}
    assert line["points"][-1]["price"] == pytest.approx(106.9)
    assert line["price_at_as_of"] == pytest.approx(106.9)
    assert line["projection_only"] is True
    assert line["observed_at"] == "2026-01-01T11:00:00Z"
    assert line["confirmed_at"] == "2026-01-02T20:00:00Z"
    assert line["data_cutoff_at"] == "2026-01-04T08:00:00Z"
    assert [item["bar_index"] for item in line["touch_evidence"]] == [10, 25, 40]
    assert len(line["anchors"]) == 2
    assert line["tolerance_confirmed_at"] == "2026-01-02T05:00:00Z"
    json.dumps(line, allow_nan=False)


@pytest.mark.parametrize("side", ["support", "resistance"])
def test_original_regression_latest_completed_nonpivot_close_breaks_line(side):
    rows = _bars()
    rows[-1].update(open=86.0, high=87.0, low=85.0, close=86.0)
    if side == "resistance":
        rows = _mirror(rows)
    line = _side(_run(rows), side)[0]
    assert line["status"] == "broken"
    assert line["broken_at"] == "2026-01-04T08:00:00Z"
    assert line["break_close"] == rows[-1]["close"]
    assert line["break_rule"] == "completed_close_beyond_frozen_band"
    assert line["confirmed_at"] < line["broken_at"]


def test_original_regression_open_high_cannot_create_a_prior_support():
    rows = _bars(pivot_prices=(100.0, 107.0, 105.0))
    rows[-1]["is_closed"] = False
    cutoff = _as_of(rows)
    before = build_causal_trendlines(rows, timeframe="1H", as_of=cutoff)
    mutated = deepcopy(rows)
    mutated[-1]["high"] = 1_000.0
    after = build_causal_trendlines(mutated, timeframe="1H", as_of=cutoff)
    assert not _side(before, "support")
    assert after == before


@pytest.mark.parametrize("side", ["support", "resistance"])
def test_open_and_future_mutations_change_no_anchor_tolerance_or_state(side):
    rows = _bars()
    if side == "resistance":
        rows = _mirror(rows)
    cutoff = _as_of(rows)
    baseline = build_causal_trendlines(rows, timeframe="1H", as_of=cutoff)
    extra = {**rows[-1], "time": rows[-1]["time"]+3600,
             "is_closed": False, "open": 200.0, "high": 1e7,
             "low": 0.001, "close": 200.0}
    future = {**extra, "time": extra["time"]+3600*100, "is_closed": True}
    assert build_causal_trendlines(rows+[extra, future], timeframe="1H", as_of=cutoff) == baseline


def test_later_closed_volatility_cannot_rewrite_anchor_band_or_confirmation():
    rows = _bars()
    before = _side(_run(rows[:50]), "support")[0]
    rows[-1]["high"] = 1_000.0
    after = _side(_run(rows), "support")[0]
    for key in ("id", "anchors", "tolerance", "tolerance_confirmed_at", "confirmed_at"):
        assert after[key] == before[key]
    assert after["status"] == "active"


def test_later_closed_high_cannot_retroactively_fit_noncollinear_old_touches():
    rows = _bars(pivot_prices=(100.0, 107.0, 105.0))
    assert not _side(_run(rows), "support")
    rows[-1]["high"] = 1_000.0
    assert not _side(_run(rows), "support")


def test_third_touch_needs_all_right_hand_bars_to_be_closed():
    rows = _bars()
    assert not _side(_run(rows[:43]), "support")
    assert _side(_run(rows[:44]), "support")
    rows[43]["is_closed"] = False
    assert not _side(_run(rows[:44]), "support")


def test_equal_low_plateau_is_not_three_independent_strict_pivots():
    rows = _bars()
    for index in (10, 25, 40):
        rows[index+1]["low"] = rows[index]["low"]
    assert not _side(_run(rows), "support")


def test_clustered_touches_do_not_masquerade_as_independent_contacts():
    rows = _bars(pivot_prices=(100.0, 100.4, 100.8), indices=(10, 14, 18))
    assert not _side(_run(rows), "support")


def test_break_before_third_touch_confirmation_never_freely_confirms_line():
    rows = _bars()
    rows[43].update(open=95.0, high=96.0, low=94.0, close=95.0)
    assert not _side(_run(rows), "support")


@pytest.mark.parametrize("side", ["support", "resistance"])
def test_break_is_terminal_even_after_recovery_and_later_touches(side):
    rows = _bars(count=130)
    rows[60].update(open=90.0, high=92.0, low=89.0, close=91.0)
    for index in (70, 85, 100, 115):
        rows[index]["low"] = 100.0 + (index-10)*0.1
    if side == "resistance":
        rows = _mirror(rows)
    early = _side(_run(rows[:61]), side)[0]
    late = _side(_run(rows), side)
    same = next(line for line in late if line["id"] == early["id"])
    assert same["status"] == "broken"
    assert same["broken_at"] == early["broken_at"]
    assert same["touches"] == early["touches"]
    assert not any(
        line["status"] == "active" and abs(line["slope_per_bar"]-same["slope_per_bar"]) < 1e-12
        and abs(line["price_at_as_of"]-same["price_at_as_of"]) < 1e-9
        for line in late
    )


def test_wick_through_a_line_without_close_break_remains_active():
    rows = _bars()
    rows[-1]["low"] = 90.0
    line = _side(_run(rows), "support")[0]
    assert line["status"] == "active"


@pytest.mark.parametrize("scale_factor", [1e-8, 0.001, 100.0])
def test_small_prices_and_subcent_ticks_are_not_rounded_to_zero(scale_factor):
    rows = _bars()
    for row in rows:
        for key in ("open", "high", "low", "close"):
            row[key] *= scale_factor
    line = _side(_run(rows), "support")[0]
    assert line["points"][0]["price"] == pytest.approx(100.0*scale_factor, rel=1e-12)
    assert line["points"][-1]["price"] == pytest.approx(106.9*scale_factor, rel=1e-12)
    assert line["points"][-1]["price"] > 0


def test_close_timestamp_mode_keeps_drawing_timestamps_on_source_close_axis():
    rows = _bars()
    for row in rows:
        row["time"] += 3600
    line = build_causal_trendlines(rows, timeframe="1H", as_of=_as_of(rows), timestamp_mode="close")[0]
    assert line["points"][0]["time"] == rows[10]["time"]
    assert line["observed_at"] == "2026-01-01T11:00:00Z"


def test_irregular_calendar_gaps_keep_explicit_logical_bar_axis():
    rows = _bars()
    for index in range(26, len(rows)):
        rows[index]["time"] += 3600*48
    line = _side(_run(rows), "support")[0]
    assert line["axis_basis"] == "completed_bar_index"
    assert line["slope_per_bar"] == pytest.approx(0.1)
    assert line["price_at_as_of"] == pytest.approx(106.9)


def test_explicit_log_scale_uses_geometric_price_projection():
    rows = _bars()
    for index, row in enumerate(rows):
        expected = 100.0 * 1.01**(index-10)
        row.update(open=expected*1.06, high=expected*1.08, low=expected*1.04, close=expected*1.06)
    for index in (10, 25, 40):
        rows[index]["low"] = 100.0 * 1.01**(index-10)
    line = _side(_run(rows, scale="log"), "support")[0]
    assert line["scale"] == "log"
    assert line["price_at_as_of"] == pytest.approx(100.0*1.01**69)
    assert line["points"][-1]["price"] == line["price_at_as_of"]


@pytest.mark.parametrize("kwargs", [{"scale": "percent"}, {"max_per_side": -1}, {"timestamp_mode": "guess"}])
def test_invalid_configuration_is_not_silently_reinterpreted(kwargs):
    with pytest.raises(ValueError):
        _run(_bars(), **kwargs)


def test_max_per_side_zero_is_an_explicit_empty_result():
    assert _run(_bars(), max_per_side=0) == []


def test_any_explicit_negative_completion_flag_excludes_a_bar():
    rows = _bars()
    rows[-1].update(is_closed=True, complete=False, open=86.0, high=87.0, low=85.0, close=86.0)
    line = _side(_run(rows), "support")[0]
    assert line["status"] == "active"
    assert line["data_cutoff_at"] == "2026-01-04T07:00:00Z"


def test_duplicate_provider_rows_cannot_add_independent_touches():
    rows = _bars()
    cutoff = _as_of(rows)
    before = build_causal_trendlines(rows, timeframe="1H", as_of=cutoff)
    repeated = rows + [dict(rows[index]) for index in (10, 25, 40)]
    assert build_causal_trendlines(repeated, timeframe="1H", as_of=cutoff) == before


def test_noisy_refits_of_the_same_touch_sequence_are_not_duplicate_lines():
    rows = _bars(count=100, indices=(10, 25, 40, 55, 70),
                 pivot_prices=(100.0, 101.5, 103.1, 104.48, 106.05))
    supports = _side(_run(rows, max_per_side=10), "support")
    assert len(supports) == 1
    assert supports[0]["touches"] == 5


def test_results_are_deterministic_json_and_never_mutate_input():
    rows = _bars()
    untouched = deepcopy(rows)
    result = _run(rows)
    assert result == build_causal_trendlines(list(reversed(rows)), timeframe="1H", as_of=_as_of(rows))
    assert rows == untouched
    assert json.loads(json.dumps(result, allow_nan=False)) == result
