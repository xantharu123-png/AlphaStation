"""Mirrored synthetic v3 proof contracts, not market-quality/profit evidence."""
from copy import deepcopy
from datetime import timedelta
import time

import pytest

from modules.wyckoff import analyze_wyckoff
from modules.wyckoff_contract import validate_entry_trigger
from test_wyckoff_engine import BASE, analyze, selected, textbook_bars
from test_wyckoff_robustness import mirror_bar_update


def range_bars(direction="LONG", *, continuation=False):
    bars = textbook_bars("LONG", spring=False)
    for i, bar in enumerate(bars):
        close = (92 + i * .35 if continuation else 114 - i * .35) if i < 30 else (109. if i >= 66 else 103.)
        bar.update(open=close, high=close + 1., low=close - 1., close=close, volume=1000.)
    updates = {
        30: dict(open=101., high=102., low=100., close=101., volume=1000.),
        35: dict(open=104., high=107., low=103., close=105., volume=1300.),
        38: dict(open=103., high=104., low=102., close=103.),
        41: dict(open=104., high=106., low=103., close=105.),
        46: dict(open=101., high=102., low=100.5, close=101., volume=600.),
        50: dict(open=103., high=105.5, low=102., close=104.),
        55: dict(open=101., high=102., low=100.8, close=101.5, volume=600.),
        65: dict(open=105., high=110., low=104.5, close=109., volume=3000.),
        70: dict(open=108., high=108.3, low=106.8, close=107.8, volume=550.),
    }
    for index, update in updates.items():
        bars[index].update(update)
    if direction == "SHORT":
        for bar in bars:
            bar["open"], bar["high"], bar["low"], bar["close"] = (
                200 - bar["open"], 200 - bar["low"], 200 - bar["high"], 200 - bar["close"])
    return bars


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_v3_textbook_trigger_validates_shared_contract(direction):
    row = selected(analyze(textbook_bars(direction), direction), direction)
    assert row["entry_state"] == "ready" and row["structure_state"] == "confirmed"
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=100), model="causal_wyckoff_v3")
    assert row["swings"] and row["provisional_swing"]["status"] == "unconfirmed"
    assert all(s["volume_per_bar"] == s["cumulative_volume"] / s["duration_bars"] for s in row["swings"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_late_target_keeps_confirmed_historic_phases(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 99, direction, open=130., high=131., low=129., close=130.)
    row = selected(analyze(bars, direction), direction)
    assert row["entry_state"] == "target_passed" and not row["trade_ready"]
    assert row["structure_state"] == "confirmed" and row["phase"] == "D"
    assert all(p["status"] == "inferred" for p in row["phase_evidence"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_distinct_secondary_tests_not_adjacent_low_cluster(direction):
    bars = textbook_bars(direction, spring=False)
    for index in (50, 51, 52):
        mirror_bar_update(bars, index, direction, open=98., high=99., low=96.2, close=98., volume=600.)
    row = selected(analyze(bars, direction), direction)
    tests = [e for e in row["events"] if e["name"] == "ST"]
    assert len(tests) == 2 and [e["occurrence"] for e in tests] == [1, 2]
    assert tests[-1]["index"] == 50 and tests[-1]["confirmation_time"] == int(bars[53]["open_time"].timestamp())


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_stopped_trigger_stays_stopped_but_new_distinct_retest_can_qualify(direction):
    bars = textbook_bars(direction)
    before = selected(analyze(bars, direction), direction)
    mirror_bar_update(bars, 90, direction, open=107., high=108., low=105., close=107.5, volume=1000.)
    stopped = selected(analyze(bars[:92], direction, count=92), direction)
    assert stopped["entry_state"] == "stopped" and stopped["structure_state"] == "confirmed"
    # New retest is delayed until a new separated directional counter-swing.
    mirror_bar_update(bars, 93, direction, open=109., high=111., low=108., close=110., volume=1300.)
    mirror_bar_update(bars, 96, direction, open=106.8, high=107., low=105.7, close=106.5, volume=550.)
    after = selected(analyze(bars, direction), direction)
    assert after["trade_ready"] and after["entry_state"] == "ready"
    assert after["entry_trigger"]["trigger_id"] != before["entry_trigger"]["trigger_id"]
    assert after["entry_triggers"][0]["state"] == "stopped"
    assert len([e for e in after["events"] if e["name"] in {"LPS", "LPSY"}]) >= 2


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_phase_e_needs_completed_fresh_progress_after_retest(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 92, direction, open=110., high=112., low=109., close=111., volume=1300.)
    mirror_bar_update(bars, 93, direction, open=111., high=113., low=110., close=112., volume=1200.)
    before = selected(analyze(bars[:93], direction, count=93), direction)
    after = selected(analyze(bars[:94], direction, count=94), direction)
    assert before["phase"] == "D"
    assert after["phase"] == "E" and after["structure_state"] == "continuation"
    assert after["entry_trigger"]["trigger_id"] == before["entry_trigger"]["trigger_id"]
    assert after["trade_ready"] and validate_entry_trigger(after, as_of=BASE + timedelta(days=94), model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("continuation", [False, True])
def test_nonclimactic_reversal_and_continuation_have_own_prior_trend_range_proof(direction, continuation):
    row = selected(analyze(range_bars(direction, continuation=continuation), direction), direction)
    assert row["trade_ready"]
    assert row["origin_kind"] == ("continuation" if continuation else "nonclimactic")
    assert row["structure_type"] == ({"LONG": "Reaccumulation", "SHORT": "Redistribution"}[direction] if continuation else
                                        {"LONG": "Accumulation", "SHORT": "Distribution"}[direction])
    assert row["events"][0]["name"] == "RangeOrigin"
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=100), model="causal_wyckoff_v3")
    assert row["prior_trend"]["end_at"] < row["events"][0]["observed_at"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_c_without_spring_and_in_range_d_do_not_bypass_breakout_gate(direction):
    bars = textbook_bars(direction, spring=False)
    mirror_bar_update(bars, 47, direction, open=101., high=102., low=100., close=101.)
    mirror_bar_update(bars, 50, direction, open=99., high=100., low=98.5, close=99., volume=500.)
    mirror_bar_update(bars, 52, direction, open=102., high=104., low=101., close=103., volume=1200.)
    mirror_bar_update(bars, 53, direction, open=103., high=104.5, low=102., close=104.)
    mirror_bar_update(bars, 54, direction, open=102., high=103., low=101., close=102., volume=600.)
    mirror_bar_update(bars, 55, direction, open=103., high=105., low=102., close=104.)
    row = selected(analyze(bars[:70], direction, count=70), direction)
    assert row["variant"] == "without_spring" and row["phase"] == "D"
    assert row["entry_state"] == "no_trigger" and not row["trade_ready"]
    assert any(e["name"] == "CTest" for e in row["events"])
    assert any(e["name"] in {"InRangeSOS", "InRangeSOW"} for e in row["events"])
    control = selected(analyze(textbook_bars(direction, spring=False)[:70], direction, count=70), direction)
    assert control["phase"] == "B" and not control["trade_ready"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_rolling_window_retains_timestamp_id_when_only_irrelevant_bars_drop(direction):
    bars = range_bars(direction)
    before = selected(analyze(bars, direction), direction)
    after = selected(analyze(bars[5:], direction), direction)
    assert before["structure_id"] == after["structure_id"]
    assert before["entry_trigger"]["trigger_id"] == after["entry_trigger"]["trigger_id"]
    assert [e["event_id"] for e in before["events"]] == [e["event_id"] for e in after["events"]]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("continuation", [False, True])
def test_every_fixed_cutoff_is_identical_and_event_confirmation_is_observable(direction, continuation):
    bars = range_bars(direction, continuation=continuation)
    for count in range(60, 101):
        result = analyze(bars, direction, count=count)
        assert result == analyze(bars[:count], direction, count=count)
        for row in result["patterns"]:
            for event in row["events"]:
                assert event["observed_at"] <= event["confirmed_at"] <= result["as_of"]
            for swing in row["swings"]:
                assert swing["end_at"] <= swing["confirmed_at"] <= result["as_of"]


def test_outside_candle_ambiguous_pivots_cannot_supply_entry_trigger():
    bars = textbook_bars()
    bars[85].update(high=112., low=105.8)
    result = analyze(bars)
    row = selected(result, "LONG")
    assert not row["trade_ready"]
    assert (BASE + timedelta(days=86)).isoformat().replace("+00:00", "Z") in result["ambiguous_pivot_times"]


def test_work_is_bounded_and_reported_not_silently_cropped():
    bars = textbook_bars()
    for index in range(100, 721):
        bar = deepcopy(bars[-1])
        bar.update(open_time=BASE + timedelta(days=index), close_time=BASE + timedelta(days=index + 1))
        bars.append(bar)
    result = analyze(bars, count=721)
    assert result["reason"] == "analysis_window_exceeds_bounded_model" and not result["patterns"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_target_consumed_then_returned_quote_never_revives_same_trigger(direction):
    bars = textbook_bars(direction)
    before = selected(analyze(bars, direction), direction)
    mirror_bar_update(bars, 95, direction, open=114., high=116., low=113., close=115.)
    after = selected(analyze(bars, direction), direction)
    assert after["entry_state"] == "target_passed" and not after["trade_ready"]
    assert after["entry_trigger"]["trigger_id"] == before["entry_trigger"]["trigger_id"]
    assert after["structure_state"] != "failed"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_parent_e_and_smaller_child_continuation_coexist_without_reidentification(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 92, direction, open=110., high=112., low=109., close=111., volume=1300.)
    mirror_bar_update(bars, 93, direction, open=111., high=113., low=110., close=112., volume=1200.)
    original = selected(analyze(bars, direction), direction)
    child = range_bars(direction, continuation=True)
    for bar in child:
        for key in ("open", "high", "low", "close"):
            bar[key] += 20. if direction == "LONG" else -20.
        bar["open_time"] += timedelta(days=100)
        bar["close_time"] += timedelta(days=100)
    result = analyze(bars + child, direction, count=200)
    parent = next(p for p in result["patterns"] if p["structure_id"] == original["structure_id"])
    descendants = [p for p in result["patterns"] if p["parent_structure_id"] == parent["structure_id"]]
    assert parent["phase"] == "E" and parent["structure_state"] == "continuation"
    assert descendants and any(p["trade_ready"] for p in descendants)
    assert parent["events"][:len(original["events"])] == original["events"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_missing_origin_cannot_reconstruct_old_structure_identity(direction):
    bars = range_bars(direction)
    complete = selected(analyze(bars, direction), direction)
    clipped = analyze(bars[31:], direction)
    assert not any(p["structure_id"] == complete["structure_id"] for p in clipped["patterns"])
    assert clipped["history_context"]["prior_window_context"] == "incomplete_history"
    assert clipped["history_context"]["missing_anchor_policy"] == "no_reconstructed_structure_or_trigger"


def test_retained_swing_anchors_keep_ids_when_old_initial_pivots_roll_out():
    bars = range_bars()
    whole, clipped = analyze(bars), analyze(bars[10:])
    cutoff = int(bars[30]["open_time"].timestamp())
    before = [s for s in whole["swings"] if s["start_time"] >= cutoff]
    after = [s for s in clipped["swings"] if s["start_time"] >= cutoff]
    assert before == after and before


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_plateau_secondary_test_does_not_backdate_trigger_anchors(direction):
    bars = textbook_bars(direction, spring=False)
    for index in (50, 51, 52):
        mirror_bar_update(bars, index, direction, open=98., high=99., low=96.2, close=98., volume=600.)
    for count in range(60, 101):
        result = analyze(bars, direction, count=count)
        assert result == analyze(bars[:count], direction, count=count)
        for row in result["patterns"]:
            if row["trade_ready"]:
                assert validate_entry_trigger(row, as_of=BASE + timedelta(days=count), model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_every_event_price_is_on_its_observed_bar(direction):
    bars = range_bars(direction, continuation=True)
    for row in analyze(bars, direction)["patterns"]:
        for event in row["events"]:
            observed = bars[event["index"]]
            assert observed["low"] <= event["price"] <= observed["high"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_rolling_window_freezes_stop_and_state_not_only_trigger_ids(direction):
    bars = range_bars(direction)
    for index in range(5):
        mirror_bar_update(bars, index, direction, open=100., high=180., low=20., close=100.)
    before = selected(analyze(bars, direction), direction)
    after = selected(analyze(bars[5:], direction), direction)
    assert before["entry_trigger"] == after["entry_trigger"]
    assert before["trade"] == after["trade"]
    assert before["entry_state"] == after["entry_state"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_missing_anchor_relative_volume_blocks_entry_not_historic_phases(direction):
    bars = textbook_bars(direction)
    for index in (19, 21, 22, 23, 24):
        bars[index]["volume"] = 0.
    row = selected(analyze(bars, direction), direction)
    assert row["entry_state"] == "data_missing" and not row["trade_ready"]
    assert row["structure_state"] == "confirmed"
    assert row["invalidation_reason"] == "trigger_volume_evidence_missing"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_failed_old_e_cannot_be_parent_of_a_later_range(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 92, direction, open=110., high=112., low=109., close=111., volume=1300.)
    mirror_bar_update(bars, 93, direction, open=111., high=113., low=110., close=112., volume=1200.)
    old_id = selected(analyze(bars, direction), direction)["structure_id"]
    child = range_bars(direction, continuation=True)
    for bar in child:
        bar["open_time"] += timedelta(days=100)
        bar["close_time"] += timedelta(days=100)
    result = analyze(bars + child, direction, count=200)
    parent = next(p for p in result["patterns"] if p["structure_id"] == old_id)
    assert parent["structure_state"] == "failed" and parent["structure_failed_at"]
    later = [p for p in result["patterns"] if p["range_start_time"] > int((BASE + timedelta(days=100)).timestamp())]
    assert later and all(p["parent_structure_id"] != old_id for p in later)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_preliminary_and_range_side_tests_are_evidence_not_filled_schema(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 10, direction, open=106., high=107.5, low=104., close=107., volume=1600.)
    mirror_bar_update(bars, 48, direction, open=103., high=105., low=101., close=104., volume=1000.)
    mirror_bar_update(bars, 65, direction, open=97., high=99., low=96., close=98., volume=500.)
    row = selected(analyze(bars, direction), direction)
    assert row["trade_ready"]
    names = {e["name"] for e in row["events"]}
    assert {"PS" if direction == "LONG" else "PSY", "UpperTest" if direction == "LONG" else "LowerTest",
            "SpringTest" if direction == "LONG" else "UTADTest"}.issubset(names)
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=100), model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_first_secondary_plateau_uses_actual_departure_confirmation(direction):
    bars = textbook_bars(direction)
    for index in (40, 41, 42):
        mirror_bar_update(bars, index, direction, open=98., high=99., low=96., close=98., volume=700.)
    row = selected(analyze(bars, direction), direction)
    first = next(e for e in row["events"] if e["name"] == "ST")
    assert first["index"] == 40
    assert first["confirmation_time"] == int(bars[43]["open_time"].timestamp())
    assert first["confirmed_at"] == bars[43]["close_time"].isoformat().replace("+00:00", "Z")
    assert row["trade_ready"] and validate_entry_trigger(row, as_of=BASE + timedelta(days=100), model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_terminal_target_cause_cannot_be_replaced_by_later_stop(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 90, direction, open=109., high=120., low=108., close=109.)
    mirror_bar_update(bars, 96, direction, open=108., high=109., low=105., close=108., volume=1000.)
    before = selected(analyze(bars[:94], direction, count=94), direction)
    after = selected(analyze(bars, direction), direction)
    assert before["entry_state"] == after["entry_state"] == "target_passed"
    assert before["entry_trigger"] == after["entry_trigger"]
    assert after["entry_trigger"]["terminal_at"] == bars[90]["close_time"].isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_same_bar_stop_and_target_are_ambiguous_not_an_invented_outcome(direction):
    bars = textbook_bars(direction)
    mirror_bar_update(bars, 90, direction, open=108., high=120., low=105., close=108.)
    row = selected(analyze(bars, direction), direction)
    assert row["entry_state"] == "ambiguous" and not row["trade_ready"]
    assert row["invalidation_reason"] == "ambiguous_no_intrabar_order"
    assert row["entry_trigger"]["terminal_at"] == bars[90]["close_time"].isoformat().replace("+00:00", "Z")
    assert row["structure_state"] != "failed"
