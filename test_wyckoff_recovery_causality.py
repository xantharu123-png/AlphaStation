"""Offline regressions for skipped recovery evidence and plateau confirmation."""
import pytest

from test_wyckoff_engine import analyze, textbook_bars
from test_wyckoff_robustness import mirror_bar_update


def original_range(bars, direction, count):
    result = analyze(bars, direction, count=count)
    assert result == analyze(bars[:count], direction, count=count)
    return next(row for row in result["patterns"]
                if row["range_start_time"] == int(bars[20]["open_time"].timestamp()))


def recovery_bars(direction):
    bars = textbook_bars(direction, spring=False)
    mirror_bar_update(bars, 65, direction, open=96., high=98., low=94.5, close=95., volume=500.)
    mirror_bar_update(bars, 66, direction, open=95., high=96., low=94.8, close=95.5, volume=500.)
    mirror_bar_update(bars, 67, direction, open=97., high=102., low=96., close=100., volume=1000.)
    return bars


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("failure", ["deep_breach", "heavy_volume"])
def test_intervening_recovery_breach_fails_when_observed_and_never_revives(direction, failure):
    bars = recovery_bars(direction)
    if failure == "deep_breach":
        mirror_bar_update(bars, 66, direction, open=94., high=95., low=80., close=90., volume=500.)
    else:
        mirror_bar_update(bars, 66, direction, open=95., high=96., low=94.8, close=95.5, volume=4000.)
    pending = original_range(bars, direction, 66)
    assert pending["structure_state"] == "unclear"
    assert pending["structure_failed_at"] is None
    for count in (67, 68, 100):
        row = original_range(bars, direction, count)
        assert row["structure_state"] == "failed"
        assert row["structure_failed_at"] == bars[66]["close_time"].isoformat().replace("+00:00", "Z")
        assert row["structure_failure_reason"] == "range_failed"
        assert row["trade_ready"] is False and row["trade"] is None
        assert not any(e["name"] in {"Spring", "UTAD"} for e in row["events"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_valid_multibar_recovery_stays_pending_until_actual_recovery(direction):
    bars = recovery_bars(direction)
    for count in (66, 67):
        row = original_range(bars, direction, count)
        assert row["structure_state"] == "unclear" and not row["trade_ready"]
        assert not any(e["name"] in {"Spring", "UTAD"} for e in row["events"])
    row = original_range(bars, direction, 68)
    spring = next(e for e in row["events"] if e["name"] in {"Spring", "UTAD"})
    assert spring["index"] == 65
    assert spring["confirmed_at"] == bars[67]["close_time"].isoformat().replace("+00:00", "Z")
    assert original_range(bars, direction, 100)["trade_ready"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_recovery_timeout_is_dated_when_deadline_expires_not_at_first_breach(direction):
    bars = recovery_bars(direction)
    for index in (67, 68):
        mirror_bar_update(bars, index, direction, open=95., high=96., low=94.8, close=95.5, volume=500.)
    pending = original_range(bars, direction, 68)
    assert pending["structure_state"] == "unclear"
    assert pending["structure_failed_at"] is None
    for count in (69, 100):
        row = original_range(bars, direction, count)
        assert row["structure_state"] == "failed"
        assert row["structure_failed_at"] == bars[68]["close_time"].isoformat().replace("+00:00", "Z")
        assert row["trade_ready"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_post_breakout_range_breach_keeps_first_failure_before_later_collapse(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 90, direction, open=96., high=98., low=94.5, close=95., volume=500.)
    mirror_bar_update(bars, 91, direction, open=94., high=95., low=80., close=90., volume=500.)
    mirror_bar_update(bars, 92, direction, open=97., high=102., low=96., close=100., volume=1000.)
    for count in (91, 92, 93, 100):
        row = original_range(bars, direction, count)
        assert row["structure_state"] == "failed"
        assert row["structure_failed_at"] == bars[90]["close_time"].isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_in_range_progress_waits_for_plateau_departure_without_backdating(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 69, direction, open=103., high=104., low=102., close=103.)
    mirror_bar_update(bars, 70, direction, open=102., high=103., low=101., close=102., volume=600.)
    for index in (71, 72):
        mirror_bar_update(bars, index, direction, open=103., high=104., low=101., close=103., volume=600.)
    mirror_bar_update(bars, 73, direction, open=104., high=105., low=102., close=104.)
    for count in (72, 73):
        row = original_range(bars, direction, count)
        assert row["phase"] == "C"
        assert not any(e["name"] in {"InRangeSOS", "InRangeSOW"} for e in row["events"])
    row = original_range(bars, direction, 74)
    proof = next(e for e in row["events"] if e["name"] in {"InRangeSOS", "InRangeSOW"})
    assert proof["index"] == 70
    assert proof["confirmed_at"] == bars[73]["close_time"].isoformat().replace("+00:00", "Z")
    assert proof["confirmation_time"] == int(bars[73]["open_time"].timestamp())
    phase = next(p for p in row["phase_evidence"] if p["phase"] == "D")
    assert phase["confirmed_at"] == proof["confirmed_at"]
    assert row["phase"] == "D" and row["trade_ready"] is False
