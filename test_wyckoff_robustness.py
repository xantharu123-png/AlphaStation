"""Causal range/phase regressions, not calibrated trading-performance claims."""
from copy import deepcopy
from datetime import timedelta

import pytest

from modules.wyckoff import MODEL
from test_wyckoff_engine import BASE, analyze, selected, textbook_bars


def mirror_bar_update(bars, index, direction, **prices):
    """Define mirrored test disturbances without asymmetric SHORT rules."""
    if direction == "SHORT":
        mirrored = {key: value for key, value in prices.items() if key not in {"open", "high", "low", "close"}}
        mirrored.update(open=200 - prices["open"], high=200 - prices["low"],
                        low=200 - prices["high"], close=200 - prices["close"])
        prices = mirrored
    bars[index].update(prices)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("small_high", [102.5, 104.0])
def test_small_first_recovery_peak_does_not_steal_structural_ar(direction, small_high):
    bars = textbook_bars(direction)
    before = selected(analyze(bars, direction), direction)
    mirror_bar_update(bars, 22, direction, open=101., high=small_high, low=100., close=101.5)
    after = selected(analyze(bars, direction), direction)
    assert after["trade_ready"]
    assert after["events"] == before["events"]
    # Changed historic OHLC legitimately changes confirmation ATR slightly;
    # the structural projection must not be replaced by the tiny first peak.
    for field in ("entry", "tp1", "tp2"):
        assert after["trade"][field] == before["trade"][field]
    at_confirmation = selected(analyze(bars[:87], direction, count=87), direction)
    assert after["trade"]["stop"] == at_confirmation["trade"]["stop"]
    assert after["range_confirmed_at"] == before["range_confirmed_at"]
    assert after["range_confirmed_time"] == int((BASE + timedelta(days=26)).timestamp())


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_ar_is_frozen_at_first_st_not_a_later_stronger_pivot(direction):
    bars = textbook_bars(direction, spring=False)
    mirror_bar_update(bars, 25, direction, open=101., high=102., low=100., close=101.)
    mirror_bar_update(bars, 22, direction, open=102., high=106., low=100., close=105., volume=2000.)
    mirror_bar_update(bars, 26, direction, open=98., high=99., low=96., close=98., volume=700.)
    mirror_bar_update(bars, 30, direction, open=102., high=110., low=100., close=105., volume=1000.)
    row = selected(analyze(bars, direction), direction)
    assert row["trade_ready"]
    assert next(item for item in row["events"] if item["name"] == "AR")["index"] == 22
    assert next(item for item in row["events"] if item["name"] == "ST")["index"] == 26
    assert row["range_high" if direction == "LONG" else "range_low"] == (106. if direction == "LONG" else 94.)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("failure_index", [21, 50])
def test_historically_failed_phase_a_cannot_revive_without_secondary_test(direction, failure_index):
    bars = textbook_bars(direction, spring=False)
    mirror_bar_update(bars, 40, direction, open=101., high=102., low=100., close=101., volume=1000.)
    mirror_bar_update(bars, failure_index, direction, open=90., high=91., low=89., close=90., volume=1000.)
    row = selected(analyze(bars, direction), direction)
    assert row["phase"] == "A"
    assert row["signal_state"] == "invalidated"
    assert row["invalidation_reason"] == "range_failed_before_secondary_test"
    assert not row["trade_ready"] and row["trade"] is None
    assert [item["name"] for item in row["events"]] == (["SC", "AR"] if direction == "LONG" else ["BC", "AR"])
    assert row["phase_evidence"][0]["status"] == "invalidated"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_failed_confirmed_breakout_keeps_original_evidence_and_cannot_revive(direction):
    bars = textbook_bars(direction)
    before = selected(analyze(bars, direction), direction)
    mirror_bar_update(bars, 90, direction, open=105., high=106., low=104., close=105., volume=1000.)
    mirror_bar_update(bars, 93, direction, open=106., high=110., low=105.8, close=109., volume=3500.)
    mirror_bar_update(bars, 95, direction, open=106.8, high=107., low=105.8, close=106.6, volume=600.)
    after = selected(analyze(bars, direction), direction)
    assert not after["trade_ready"] and after["trade"] is None
    assert after["invalidation_reason"] == "breakout_failed"
    assert after["events"] == before["events"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("spring", [False, True])
def test_phase_intervals_and_ordinals_are_real_evidence_not_numbered_subwaves(direction, spring):
    bars = textbook_bars(direction, spring=spring)
    result = analyze(bars, direction)
    row = selected(result, direction)
    assert result["model"] == MODEL == "causal_wyckoff_v3"
    assert row["sequence_basis"] == "chronological_display_ordinal_not_canonical_subwave"
    assert row["phase_basis"] == "inferred_event_intervals"
    assert row["unmodelled_phases"] == []
    assert [item["sequence"] for item in row["events"]] == list(range(1, len(row["events"]) + 1))
    assert all(item["occurrence"] == 1 for item in row["events"])
    st = next(item for item in row["events"] if item["name"] == "ST")
    assert st["phase"] == "A"
    phases = row["phase_evidence"]
    assert [item["phase"] for item in phases] == (["A", "B", "C", "D"] if spring else ["A", "B", "D"])
    phase_b = phases[1]
    assert phase_b["start_time"] == phase_b["confirmed_time"] == st["confirmation_time"]
    assert phase_b["basis"] == "range_after_first_secondary_test"
    times = {int(bar["open_time"].timestamp()) for bar in bars}
    for item in phases:
        assert item["start_time"] in times and item["confirmed_time"] in times and item["end_time"] in times
        assert item["start_time"] <= item["confirmed_time"] <= item["end_time"]
        assert item["observed_at"] <= item["confirmed_at"] <= result["as_of"]
        assert item["status"] == "inferred"
    assert all(current["end_time"] == following["start_time"] for current, following in zip(phases, phases[1:]))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_repeated_springs_have_occurrences_without_duplicate_phase_blocks(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 60, direction, open=96., high=98., low=94.5, close=97., volume=500.)
    mirror_bar_update(bars, 61, direction, open=98., high=100., low=97.5, close=99., volume=1000.)
    row = selected(analyze(bars, direction), direction)
    name = "Spring" if direction == "LONG" else "UTAD"
    assert row["trade_ready"]
    repeated = [item for item in row["events"] if item["name"] == name]
    assert [item["occurrence"] for item in repeated] == [1, 2]
    assert [item["sequence"] for item in repeated] == [4, 5]
    assert sum(item["phase"] == "C" for item in row["phase_evidence"]) == 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_full_history_at_each_fixed_prefix_is_identical_to_available_history(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 22, direction, open=101., high=102.5, low=100., close=101.5)
    for count in range(60, 101):
        assert analyze(bars, direction, count=count) == analyze(bars[:count], direction, count=count)
    confirmed = selected(analyze(bars[:87], direction, count=87), direction)
    current = selected(analyze(bars, direction), direction)
    assert confirmed["trade_ready"]
    assert current["events"] == confirmed["events"]
    assert current["trade"]["stop"] == confirmed["trade"]["stop"]
    assert analyze(list(reversed(bars)) + [deepcopy(bars[22])], direction) == analyze(bars, direction)
