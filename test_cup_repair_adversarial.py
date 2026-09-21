"""Independent, offline detector controls for the Cup close/shape repair.

These cases exercise the complete window search, not just the shape helper:
an invalid 90-session formation cannot escape through a shorter candidate.
"""
from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

import api
from modules.cup_shape import validate_cup_shape
from modules.patterns import detect_chart_patterns


def _bar(close, *, volume=1_000_000):
    return {"open": close * .997, "high": close * 1.012,
            "low": close * .988, "close": close, "volume": volume}


def _rounded_closes():
    return ([100 - 24 * i / 27 for i in range(28)]
            + [75 + 2 * abs((i - 13) / 13) for i in range(26)]
            + [77 + 22.5 * i / 35 for i in range(36)])


def _formation(kind="rounded", *, noisy=False, scale=1.0):
    if kind == "square":
        closes = [100.] * 25 + [75.] * 40 + [99.5] * 25
    elif kind == "linear_v":
        closes = ([100 - 25 * i / 44 for i in range(45)]
                  + [75 + 24.5 * i / 44 for i in range(45)])
    elif kind == "two_basins":
        closes = [100 - 25 * abs(math.sin(math.pi * i / 44.5)) for i in range(90)]
    elif kind == "wick_floor":
        closes = [99.5] * 90
    else:
        assert kind == "rounded"
        closes = _rounded_closes()
    bars = [_bar(close) for close in closes]
    if kind == "wick_floor":
        for index in (32, 41, 48):
            bars[index]["low"] = 74.1
    if noisy:
        for index, bar in enumerate(bars[:-1]):
            offset = .65 * math.sin(index * 2.3)
            for field in ("open", "high", "low", "close"):
                bar[field] += offset
    bars += [_bar(close, volume=650_000) for close in
             (98.8, 97.2, 95.5, 94.0, 94.8, 95.6, 96.7, 97.5, 98.3)]
    bars.append({"open": 101.3, "high": 102.717, "low": 99.8,
                 "close": 101.7, "volume": 2_400_000})
    session = date(2025, 9, 1)
    for bar in bars:
        while session.weekday() >= 5:
            session += timedelta(days=1)
        bar["date"] = session.isoformat()
        session += timedelta(days=1)
        for field in ("open", "high", "low", "close"):
            bar[field] *= scale
    return bars


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Cup geometry regressions must never invoke provider or SMTP I/O")

    monkeypatch.setattr(api, "rate_limited_get", forbidden)
    monkeypatch.setattr(api, "_send_email_alert", forbidden)
    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", forbidden)


@pytest.mark.parametrize("kind", ["square", "linear_v", "wick_floor"])
def test_false_cup_cannot_escape_through_shorter_detector_window(kind):
    bars = _formation(kind)
    assert validate_cup_shape(bars[:90]) is None
    assert api._detect_cup_handle_breakout(bars, current_price=101.7) is None


def test_two_basins_are_not_one_cup_but_a_complete_later_bowl_can_stand_alone():
    bars = _formation("two_basins")
    assert validate_cup_shape(bars[:90]) is None
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert setup is not None
    anchors = setup["cup_pattern_evidence"]["anchors"]
    # Older basins cannot invalidate a coherent later bowl. The actual
    # selected anchors must describe the second bowl, never both as one U.
    assert anchors["left_rim"]["session"] >= bars[44]["date"]
    assert anchors["bottom"]["session"] > bars[44]["date"]
    assert anchors["right_rim"]["session"] == bars[89]["date"]
    assert setup["cup_confirmation_level"] >= bars[0]["high"]


@pytest.mark.parametrize("scale", [.1, 1., 100.])
@pytest.mark.parametrize("noisy", [False, True])
def test_real_bowl_with_ordinary_zigzags_keeps_original_observed_anchors(scale, noisy):
    bars = _formation(noisy=noisy, scale=scale)
    before = deepcopy(bars)
    assert validate_cup_shape(bars[:90]) is not None
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7 * scale)
    assert setup is not None
    assert bars == before
    evidence = setup["cup_pattern_evidence"]
    assert evidence["status"] == "available"
    anchors = evidence["anchors"]
    assert anchors["left_rim"]["index"] < anchors["bottom"]["index"] < anchors["right_rim"]["index"]
    source = {bar["date"]: bar for bar in bars}
    for anchor in anchors.values():
        assert anchor["price"] == source[anchor["session"]][anchor["price_field"]]
    assert anchors["breakout"]["price"] == bars[-1]["close"]


@pytest.mark.parametrize("close", [100., 100.8, 101., 101.2, 101.4])
def test_marginal_close_cannot_lower_the_original_structural_resistance(close):
    bars = _formation()
    bars[-1].update(open=100., high=102., low=98., close=close)
    # The original rim is 101.2 and confirmation requires the existing .2%
    # buffer. A live quote over it cannot repair yesterday's failed close.
    assert api._detect_cup_handle_breakout(bars, current_price=102.) is None


def test_exact_existing_close_buffer_is_accepted_without_rounding_the_rim():
    bars = _formation()
    threshold = bars[0]["high"] * 1.002
    bars[-1].update(open=100.5, high=102., low=99.8, close=threshold)
    setup = api._detect_cup_handle_breakout(bars, current_price=threshold)
    assert setup is not None
    assert setup["cup_pattern_evidence"]["anchors"]["left_rim"]["price"] == bars[0]["high"]


def test_inverted_right_rim_before_deepest_bottom_cannot_be_a_valid_cup():
    bars = _formation()
    bars[61].update(open=99.7, close=100., high=101.2, low=99.)
    bars[65].update(open=70.787, close=71., high=72., low=70.)
    assert api._detect_cup_handle_breakout(bars, current_price=101.7) is None


@pytest.mark.parametrize("index", [-1, -5])
def test_invalid_breakout_or_handle_ohlc_cannot_attest_close_confirmation(index):
    bars = _formation()
    bars[index]["high"] = bars[index]["close"] - .01
    assert api._detect_cup_handle_breakout(bars, current_price=101.7) is None


@pytest.mark.parametrize("kind", ["square", "linear_v", "wick_floor"])
def test_generic_chart_does_not_call_false_bowls_cup_breakouts(kind):
    bars = _formation(kind)
    patterns = detect_chart_patterns(bars, lookback=len(bars))
    assert not any(pattern["pattern"] == "Cup & Handle" for pattern in patterns)


def test_generic_chart_allows_noisy_bowl_without_claiming_scanner_confirmation():
    bars = _formation(noisy=True)
    cups = [pattern for pattern in detect_chart_patterns(bars, lookback=len(bars))
            if pattern["pattern"] == "Cup & Handle"]
    assert len(cups) == 1
    assert cups[0]["signal_scope"] == "chart_structure_only"
    assert cups[0]["cup_shape_version"] == "cup_shape_v1"
    assert "kein Scannersignal" in cups[0]["description"]


def test_generic_chart_wick_and_sub_buffer_close_remain_unconfirmed():
    bars = _formation()
    bars[-1].update(open=100.5, close=101.4, high=102., low=99.8)
    cups = [pattern for pattern in detect_chart_patterns(bars, lookback=len(bars))
            if pattern["pattern"] == "Cup & Handle"]
    assert len(cups) == 1
    assert cups[0]["confidence"] == "Medium"
    assert cups[0]["signal_scope"] == "chart_structure_only"
    assert "noch nicht per Schluss bestaetigt" in cups[0]["description"]
