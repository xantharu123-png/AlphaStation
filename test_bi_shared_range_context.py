"""Offline proofs for one BI range boundary; no live market or app I/O."""
import copy
import random

import pytest

import modules.patterns as patterns
from modules.bi_trade_plan import BI_PLAN_VERSION, bi_consolidation_days, bi_range_context, build_bi_trade_plan
from modules.fibonacci_levels import project_fibonacci


def _bar(price=100.0, high=None, low=None):
    return dict(open=price, high=price + 1 if high is None else high,
                low=price - 1 if low is None else low, close=price, volume=1_000_000)


def _mirror(bars):
    return [{**bar, "open": 200 - bar["open"], "high": 200 - bar["low"],
             "low": 200 - bar["high"], "close": 200 - bar["close"]} for bar in bars]


def _confirmed_fib_fixture(direction, boundary=104.832):
    """Real 90->114 swing, then six-day range whose high is the38.2% Fib.

    The old15-day high is114, not the actual adaptive-range high104.832.
    Strictly decreasing post-peak highs cannot invent a newer swing high.
    Mirroring gives the same causal proof for the short direction.
    """
    bars = [_bar() for _ in range(50)]
    bars[23] = _bar(low=90)
    for i in range(24, 37):
        bars[i] = _bar(101 + (i - 24) * 0.8)
    bars[37] = _bar(112, high=114, low=111)
    for i, high in zip(range(38, 44), (113.5, 113, 112.5, 112, 111, 109.1)):
        bars[i] = _bar(high - 1, high=high, low=high - 2)
    for i in range(44, 50):
        bars[i] = _bar(102.8, high=boundary - (i - 44) * 0.004, low=102 + (i - 44) * 0.02)
    return bars if direction == "long" else _mirror(bars)


def test_empty_range_has_unknown_prices_not_zero():
    assert bi_range_context([]) == {"range_days": 0, "effective_days": 0,
                                    "range_high": None, "range_low": None}


@pytest.mark.parametrize("length", [1, 3, 4, 5, 10, 15, 49, 50, 80])
def test_range_is_bounded_by_actual_history_and_last_fifty(length):
    bars = [_bar() for _ in range(length)]
    before = copy.deepcopy(bars)
    context = bi_range_context(bars)
    assert context == {"range_days": min(length, 50), "effective_days": min(length, 50),
                       "range_high": 101.0, "range_low": 99.0}
    assert bars == before


@pytest.mark.parametrize("high,expected_days", [(105.999999, 6), (106, 5), (107, 5), (115, 5)])
def test_original_strict_six_percent_limit_is_not_relaxed(high, expected_days):
    bars = [_bar(100, high=high, low=100)] + [_bar(100, high=101, low=100) for _ in range(5)]
    context = bi_range_context(bars)
    assert context["range_days"] == bi_consolidation_days(bars) == expected_days
    assert context["effective_days"] == expected_days
    assert context["range_high"] == (high if expected_days == 6 else 101)


@pytest.mark.parametrize("length", [5, 12, 15, 35, 50, 80])
def test_short_consolidation_keeps_existing_up_to_fifteen_bar_fallback(length):
    bars = [_bar(90) for _ in range(length - 4)] + [_bar(100) for _ in range(4)]
    context = bi_range_context(bars)
    assert context == {"range_days": 4, "effective_days": min(length, 15),
                       "range_high": 101, "range_low": 89}


@pytest.mark.parametrize("days", [None, 0, 1, 4, 5, 15, 36, 50])
def test_valid_explicit_range_days_keep_legacy_window_boundaries(days):
    rng = random.Random(83)
    # The original plan used its full structure history for the lastdays
    # slice, but ordinary range_days never exceeds50. Both must stay equal.
    bars = [_bar(100 + rng.uniform(-4, 4)) for _ in range(90)]
    actual_days = bi_consolidation_days(bars[-50:]) if days is None else days
    legacy_window = bars[-actual_days:] if actual_days >= 5 else bars[-15:]
    before = copy.deepcopy(bars)
    context = bi_range_context(bars, range_days=days)
    assert context == {"range_days": actual_days, "effective_days": len(legacy_window),
                       "range_high": max(b["high"] for b in legacy_window),
                       "range_low": min(b["low"] for b in legacy_window)}
    assert bars == before


@pytest.mark.parametrize("value", [True, False, 6.0, "6", -1, 51, 90, [], {}])
def test_invalid_explicit_days_fail_without_silent_clamping(value):
    bars = [_bar() for _ in range(50)]
    with pytest.raises(ValueError, match="range_days"):
        bi_range_context(bars, range_days=value)
    plan = build_bi_trade_plan(bars, direction="long", range_days=value, apply_structure=False)
    assert plan["accepted"] is False and plan["reason"] == "invalid_range_context"


def test_explicit_days_cannot_claim_more_history_than_supplied():
    with pytest.raises(ValueError, match="range_days"):
        bi_range_context([_bar() for _ in range(36)], range_days=50)


def test_old_prefix_does_not_change_shared_fifty_bar_context():
    tail = [_bar() for _ in range(50)]
    full = [_bar(30), _bar(180)] + tail
    assert bi_range_context(full) == bi_range_context(tail)
    assert patterns.analyze_breakout_imminent(full).consolidation_days == 50


@pytest.mark.parametrize("direction,prices,rr,method", [
    ("long", (101.2, 99.2, 103.7, 105.5), 1.7, "stop_breakout"),
    ("short", (100.5, 102.0, 98.46, 97.76), 1.6, "limit_pullback"),
])
@pytest.mark.parametrize("days", [None, 0, 4, 5, 15, 50])
def test_prechange_plan_prices_and_version_stay_identical(direction, prices, rr, method, days):
    # Values were obtained from the unmodified plan before this refactor.
    plan = build_bi_trade_plan([_bar() for _ in range(50)], direction=direction,
                               range_days=days, apply_structure=False)
    assert plan["accepted"] is True
    assert tuple(plan[k] for k in ("Entry", "StopLoss", "TP1", "TP2")) == prices
    assert plan["RiskReward"] == rr and plan["entry_method"] == method
    assert plan["RangeHigh"] == 101 and plan["RangeLow"] == 99 and plan["atr5"] == 2
    assert plan["range_days"] == (50 if days is None else days)
    assert plan["plan_version"] == plan["level_model"] == BI_PLAN_VERSION == "bi_shared_structure_v3"


@pytest.mark.parametrize("direction", ["long", "short"])
def test_real_confirmed_fib_checks_the_same_adaptive_boundary_as_plan(monkeypatch, direction):
    bars = _confirmed_fib_fixture(direction)
    before = copy.deepcopy(bars)
    real_select = patterns.select_confirmed_swing_leg
    observations = []

    def capture(input_bars, **kwargs):
        leg = real_select(input_bars, **kwargs)
        observations.append((len(input_bars), kwargs, leg))
        return leg

    monkeypatch.setattr(patterns, "select_confirmed_swing_leg", capture)
    result = patterns.analyze_breakout_imminent(bars, direction=direction)
    context = bi_range_context(bars)
    plan = build_bi_trade_plan(bars, direction=direction, range_days=result.consolidation_days,
                               apply_structure=False)
    check = result.indicator_checks[17]
    assert result.consolidation_days == context["range_days"] == plan["range_days"] == 6
    assert context["effective_days"] == 6 and plan["accepted"] is True
    assert plan["RangeHigh"] == round(context["range_high"], 2)
    assert plan["RangeLow"] == round(context["range_low"], 2)
    assert check["available"] and check["passed"] and check["points"] == 5
    assert "Range-Boundary" in check["reason"] and "38.2%" in check["reason"]

    length, call, leg = observations[0]
    assert len(observations) == 1 and length == 30
    assert call["timeframe"] == "1D" and call["timestamp_mode"] == "close"
    assert leg.direction == direction.upper()
    assert leg.start_pivot_index < leg.end_pivot_index <= 27
    assert leg.end_at <= leg.confirmed_at <= call["as_of"]
    assert leg.provenance["pivot_left"] == leg.provenance["pivot_right"] == 2
    assert leg.provenance["minimum_move_atr"] == 0

    levels = project_fibonacci(leg, retracements=(0.236, 0.382, 0.5, 0.618, 0.786), extensions=())
    tolerance = leg.magnitude * 0.03
    old_boundary = max(b["high"] for b in bars[-15:]) if direction == "long" else min(b["low"] for b in bars[-15:])
    new_boundary = context["range_high" if direction == "long" else "range_low"]
    assert old_boundary != new_boundary
    assert all(abs(old_boundary - level.midpoint) >= tolerance for level in levels)
    assert any(abs(new_boundary - level.midpoint) < tolerance for level in levels)
    assert all(abs(bars[-1]["close"] - level.midpoint) >= tolerance for level in levels
               if float(level.provenance["ratio"]) in (0.236, 0.382, 0.5))
    assert bars == before


@pytest.mark.parametrize("direction", ["long", "short"])
def test_same_boundary_does_not_relax_fib_distance_or_invent_confirmation(direction):
    bars = _confirmed_fib_fixture(direction, boundary=105.9)
    check = patterns.analyze_breakout_imminent(bars, direction=direction).indicator_checks[17]
    assert check["available"] and not check["passed"] and check["points"] == 0


@pytest.mark.parametrize("direction", ["long", "short"])
def test_origin_breach_still_retires_the_real_swing(direction):
    bars = _confirmed_fib_fixture(direction)
    if direction == "long":
        bars[-1]["low"] = 89
    else:
        bars[-1]["high"] = 111
    check = patterns.analyze_breakout_imminent(bars, direction=direction).indicator_checks[17]
    assert check["available"] and not check["passed"] and check["points"] == 0
    assert "Kein intakter bestaetigter Swing" in check["reason"]


@pytest.mark.parametrize("green,expected", [(16, False), (17, True), (20, True)])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_new_semantics_version_preserves_twenty_ids_and_seventeen_gate(monkeypatch, green, expected, direction):
    from test_bi_indicator_contract import _force_checks

    _force_checks(monkeypatch, green)
    result = patterns.analyze_breakout_imminent([_bar() for _ in range(50)], direction=direction)
    assert result.contract_version == patterns.BI_STOCK_CONTRACT_VERSION == "stock-bi-20-v3"
    assert [c["id"] for c in result.indicator_checks] == list(range(1, 21))
    assert len({c["key"] for c in result.indicator_checks}) == 20
    assert result.required_green == 17 and result.available_count == 20
    assert result.green_count == green and result[0] is expected


@pytest.mark.parametrize("direction", ["long", "short"])
def test_crypto_library_mode_keeps_its_legacy_fifteen_day_fib_boundary(direction):
    bars = _confirmed_fib_fixture(direction)
    stock = patterns.analyze_breakout_imminent(bars, direction=direction)
    crypto = patterns.analyze_breakout_imminent(bars, direction=direction, crypto_mode=True)
    # Same real confirmed swing, but only stock uses the shared stock plan.
    assert stock.indicator_checks[17]["passed"] is True
    assert crypto.indicator_checks[17]["available"] is True
    assert crypto.indicator_checks[17]["passed"] is False
    assert crypto.indicator_checks[17]["points"] == 0
    assert crypto.indicator_contract_ok is False and crypto[0] is False


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("length", [36, 50, 80])
def test_crypto_library_mode_keeps_unclipped_range_input(direction, length):
    bars = [_bar() for _ in range(length)]
    result = patterns.analyze_breakout_imminent(bars, direction=direction, crypto_mode=True)
    assert result.consolidation_days == bi_consolidation_days(bars) == length
    assert result.indicator_contract_ok is False and result[0] is False
