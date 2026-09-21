"""Offline causal Wyckoff contracts; synthetic patterns are not profitability proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from modules.wyckoff import analyze_wyckoff
from modules.level_zones import CompletedBar


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def textbook_bars(direction="LONG", *, spring=True):
    bars = []
    for i in range(100):
        close = 110 - i * 0.35 if i < 20 else (108.5 if i > 79 else 101.0)
        bars.append({"open_time": BASE + timedelta(days=i),
                     "close_time": BASE + timedelta(days=i + 1),
                     "open": close, "high": close + 1, "low": close - 1,
                     "close": close, "volume": 1000.0})
    bars[20].update(open=102.0, high=103.0, low=95.0, close=100.0, volume=6000.0)
    bars[25].update(open=102.0, high=106.0, low=100.0, close=105.0, volume=2000.0)
    bars[26].update(open=103.0, high=105.0, low=101.0, close=102.5)
    bars[40].update(open=98.0, high=99.0, low=96.0, close=98.0, volume=700.0)
    if spring:
        bars[55].update(open=96.0, high=98.0, low=94.5, close=97.0, volume=500.0)
        bars[56].update(open=98.0, high=100.0, low=97.5, close=99.0)
    bars[79].update(open=104.0, high=109.0, low=103.8, close=108.0, volume=3000.0)
    bars[85].update(open=106.8, high=107.0, low=105.8, close=106.6, volume=600.0)
    if direction == "SHORT":
        for bar in bars:
            bar["open"], bar["high"], bar["low"], bar["close"] = (
                200 - bar["open"], 200 - bar["low"], 200 - bar["high"], 200 - bar["close"])
    return bars


def analyze(bars, direction="ALL", *, count=None):
    return analyze_wyckoff(bars, as_of=BASE + timedelta(days=count or 100),
                           timeframe="1D", direction=direction)


def selected(result, direction):
    return next(item for item in result["patterns"] if item["direction"] == direction)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("spring", [False, True])
def test_real_event_chain_with_optional_spring_is_trade_ready(direction, spring):
    result = analyze(textbook_bars(direction, spring=spring), direction)
    row = selected(result, direction)
    assert result["status"] == "ok"
    assert row["trade_ready"] is True
    assert row["signal_state"] == "confirmed"
    assert row["phase"] == "D"
    assert row["score_kind"] == "quality_not_probability"
    assert row["variant"] == ("spring" if spring else "no_spring")
    names = [event["name"] for event in row["events"]]
    chain = ["SC", "AR", "ST", "SOS", "LPS"] if direction == "LONG" else ["BC", "AR", "ST", "SOW", "LPSY"]
    assert all(name in names for name in chain)
    assert ("Spring" if direction == "LONG" else "UTAD") in names if spring else True
    proof = [next(event for event in row["events"] if event["name"] == name) for name in chain]
    assert [event["confirmed_at"] for event in proof] == sorted(event["confirmed_at"] for event in proof)
    assert all(event["observed_at"] <= event["confirmed_at"] <= result["as_of"] for event in row["events"])
    assert all(event["time"] <= event["confirmation_time"] for event in row["events"])
    assert next(event for event in row["events"] if event["name"] == "AR")["confirmation_time"] == int((BASE + timedelta(days=26)).timestamp())
    trade = row["trade"]
    if direction == "LONG":
        assert 0 < trade["stop"] < trade["entry"] < trade["tp1"] < trade["tp2"]
    else:
        assert 0 < trade["tp2"] < trade["tp1"] < trade["entry"] < trade["stop"]
    assert trade["fill_evidence_verified"] is False
    assert trade["target_basis"] == "measured_range_projection"
    json.dumps(result)


def test_flat_price_and_falling_volume_cannot_create_either_direction():
    bars = textbook_bars()
    for index, bar in enumerate(bars):
        bar.update(open=100., high=101., low=99., close=100., volume=1000. if index < 50 else 400.)
    assert analyze(bars)["patterns"] == []


@pytest.mark.parametrize("field,value", [("volume", None), ("volume", float("nan")), ("volume", -1), ("high", 0), ("close", float("inf"))])
def test_invalid_completed_data_is_explicit_not_fabricated(field, value):
    bars = textbook_bars()
    bars[55][field] = value
    result = analyze(bars)
    assert result["status"] == "invalid_data"
    assert result["patterns"] == []


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_zero_volume_is_not_low_volume_spring_evidence(direction):
    bars = textbook_bars(direction)
    bars[55]["volume"] = 0
    row = selected(analyze(bars, direction), direction)
    assert row["trade_ready"] is False
    assert not any(event["name"] in {"Spring", "UTAD"} for event in row["events"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_future_or_explicitly_open_candle_cannot_change_a_fixed_cutoff(direction):
    bars = textbook_bars(direction)
    expected = analyze(bars, direction)
    future = deepcopy(bars[-1])
    future.update(open_time=BASE + timedelta(days=100), close_time=BASE + timedelta(days=101),
                  open=50., high=150., low=1., close=2., volume=100000.)
    assert analyze(bars + [future], direction) == expected
    future.update(open_time=BASE + timedelta(days=99, hours=1),
                  close_time=BASE + timedelta(days=99, hours=2), is_closed=False)
    assert analyze(bars + [future], direction) == expected


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_later_failed_break_cannot_remain_an_entry_signal(direction):
    bars = textbook_bars(direction)
    bar = bars[-1]
    close = 104. if direction == "LONG" else 96.
    bar.update(open=close, high=close + .5, low=close - .5, close=close)
    row = selected(analyze(bars, direction), direction)
    assert row["signal_state"] == "invalidated"
    assert row["trade_ready"] is False
    assert row["trade"] is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_unfinished_lps_confirmation_cannot_be_trade_ready(direction):
    bars = textbook_bars(direction)
    result = analyze(bars[:86], direction, count=86)
    row = selected(result, direction)
    assert row["trade_ready"] is False
    assert row["signal_confirmed_at"] is None
    assert not any(event["name"] in {"LPS", "LPSY"} for event in row["events"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_phase_b_context_has_no_trade_or_entry_signal(direction):
    row = selected(analyze(textbook_bars(direction, spring=False)[:70], direction, count=70), direction)
    assert row["phase"] == "B"
    assert row["trade_ready"] is False
    assert row["trade"] is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_expired_projected_target_is_not_reported_as_positive_reward(direction):
    bars = textbook_bars(direction)
    close = 130. if direction == "LONG" else 70.
    bars[-1].update(open=close, high=close + .5, low=close - .5, close=close)
    row = selected(analyze(bars, direction), direction)
    assert row["trade_ready"] is False
    assert row["trade"] is None
    assert row["invalidation_reason"] == "projected_target_not_beyond_entry"


def test_missing_explicit_context_cannot_guess_daily_or_current_time():
    with pytest.raises(TypeError):
        analyze_wyckoff(textbook_bars())
    with pytest.raises(ValueError):
        analyze_wyckoff(textbook_bars(), as_of=BASE, timeframe="nonsense")


def test_minimum_history_is_reported_separately_from_no_patterns():
    result = analyze(textbook_bars()[:30], count=30)
    assert result["status"] == "insufficient_data"
    assert result["patterns"] == []


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("failure_index", [21, 35])
def test_failed_range_before_secondary_test_cannot_be_resurrected(direction, failure_index):
    bars = textbook_bars(direction)
    close = 90. if direction == "LONG" else 110.
    bars[failure_index].update(open=close, high=close + .5, low=close - .5, close=close)
    row = selected(analyze(bars, direction), direction)
    assert row["trade_ready"] is False
    assert row["signal_state"] == "invalidated"
    assert row["invalidation_reason"] == "range_failed_before_secondary_test"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_latest_zero_volume_bar_cannot_supply_current_entry_evidence(direction):
    bars = textbook_bars(direction)
    bars[-1]["volume"] = 0.
    row = selected(analyze(bars, direction), direction)
    assert row["trade_ready"] is False
    assert row["trade"] is None
    assert row["invalidation_reason"] == "latest_volume_evidence_missing"


def test_malformed_completed_bar_clock_is_explicitly_invalid():
    bars = textbook_bars()
    raw = bars[50]
    bars[50] = CompletedBar(raw["close_time"] + timedelta(days=1), raw["close_time"],
                            raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])
    result = analyze(bars)
    assert result["status"] == "invalid_data"
    assert result["patterns"] == []


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_post_confirmation_stop_breach_does_not_reuse_old_signal(direction):
    bars = textbook_bars(direction)
    row = selected(analyze(bars, direction), direction)
    stop = row["trade"]["stop"]
    if direction == "LONG":
        bars[90]["low"] = stop - .1
    else:
        bars[90]["high"] = stop + .1
    # The close still holds above/below the breakout boundary.
    failed = selected(analyze(bars, direction), direction)
    assert failed["trade_ready"] is False
    assert failed["trade"] is None
    assert failed["invalidation_reason"] == "post_confirmation_stop_breached"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_new_completed_hold_does_not_rewrite_confirmed_events_or_stop(direction):
    bars = textbook_bars(direction)
    before = selected(analyze(bars, direction), direction)
    extra = deepcopy(bars[-1])
    extra.update(open_time=BASE + timedelta(days=100), close_time=BASE + timedelta(days=101))
    if direction == "LONG":
        extra.update(open=109., high=110., low=108., close=109.5)
    else:
        extra.update(open=91., high=92., low=90., close=90.5)
    after = selected(analyze(bars + [extra], direction, count=101), direction)
    assert after["trade_ready"]
    assert after["events"] == before["events"]
    assert after["range_confirmed_at"] == before["range_confirmed_at"]
    assert after["trade"]["stop"] == before["trade"]["stop"]


@pytest.mark.parametrize("requested", ["ALL", "LONG", "SHORT"])
def test_consumed_old_long_context_does_not_block_a_new_short_trigger(requested):
    bars = textbook_bars("LONG")
    for bar in textbook_bars("SHORT"):
        for name in ("open", "high", "low", "close"):
            bar[name] += 17.
        bar["open_time"] += timedelta(days=100)
        bar["close_time"] += timedelta(days=100)
        bars.append(bar)
    result = analyze(bars, requested, count=200)
    assert result["patterns"]
    for row in result["patterns"]:
        if row["direction"] == "LONG":
            assert not row["trade_ready"] and row["entry_state"] == "target_passed"
        else:
            assert row["trade_ready"] and row["entry_state"] == "ready"
        assert row["structure_state"] != "failed"


def test_mirror_sequences_have_equal_quality_and_opposite_geometry():
    long = selected(analyze(textbook_bars("LONG"), "LONG"), "LONG")
    short = selected(analyze(textbook_bars("SHORT"), "SHORT"), "SHORT")
    assert long["score"] == short["score"]
    assert long["signal_confirmed_at"] == short["signal_confirmed_at"]
    for key in ("entry", "stop", "tp1", "tp2"):
        assert long["trade"][key] == pytest.approx(200 - short["trade"][key])


def test_conflicting_completed_candles_cannot_be_silently_dropped():
    bars = textbook_bars()
    duplicate = deepcopy(bars[50])
    duplicate["volume"] += 1
    result = analyze(bars + [duplicate])
    assert result["status"] == "invalid_data"
    assert result["reason"] == "conflicting_completed_bars"


def test_order_and_identical_duplicates_do_not_rewrite_event_sequence():
    bars = textbook_bars()
    expected = analyze(bars)
    assert analyze(list(reversed(bars)) + [deepcopy(bars[50])]) == expected


def test_zero_volume_outside_confirmation_events_is_not_fake_low_volume_evidence():
    bars = textbook_bars()
    bars[72]["volume"] = 0.
    row = selected(analyze(bars, "LONG"), "LONG")
    assert row["trade_ready"]
    assert all(event["index"] != 72 for event in row["events"])


def test_canonical_completed_bar_clock_is_normalized_before_serialization():
    bars = textbook_bars()
    raw = bars[50]
    bars[50] = CompletedBar(raw["open_time"].isoformat(), raw["close_time"].isoformat(),
                            raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])
    assert analyze(bars) == analyze(textbook_bars())
