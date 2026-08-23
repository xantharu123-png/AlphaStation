from datetime import datetime, timedelta, timezone

import api
import pytest

from api import (
    _calculate_directional_fib_levels,
    _decorate_scan_results,
    _strategy_score_to_grade,
    _turtle_score_cap,
)
from modules.fibonacci_levels import (
    ConfirmedSwingLeg,
    fibonacci_payload_adapter,
    project_fibonacci,
    select_confirmed_swing_leg,
)


FIB_BASE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _causal_fib_bar(index, high, low, close, *, closed=True):
    close_time = FIB_BASE + timedelta(hours=4 * index)
    return {
        "open_time": close_time - timedelta(hours=4),
        "close_time": close_time,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "is_closed": closed,
    }


def test_directional_fibonacci_long_extensions_are_above_swing_high():
    payload = _calculate_directional_fib_levels(
        highs=[10, 11, 12, 13],
        lows=[8, 9, 10, 11],
        closes=[9, 10, 12, 12.5],
        timeframe="4H",
        direction="LONG",
        lookback=4,
    )

    levels = payload["levels"]
    meta = payload["meta"]
    assert meta["direction"] == "long"
    assert meta["timeframe"] == "4H"
    assert levels["0%"] == 13
    assert levels["100%"] == 8
    assert levels["127%"] > levels["0%"]


def test_directional_fibonacci_short_extensions_are_below_swing_low():
    payload = _calculate_directional_fib_levels(
        highs=[13, 12, 11, 10],
        lows=[11, 10, 9, 8],
        closes=[12, 10, 9, 8.5],
        timeframe="1H",
        direction="SHORT",
        lookback=4,
    )

    levels = payload["levels"]
    meta = payload["meta"]
    assert meta["direction"] == "short"
    assert meta["timeframe"] == "1H"
    assert levels["0%"] == 8
    assert levels["100%"] == 13
    assert levels["127%"] < levels["0%"]


def test_canonical_fibonacci_uses_confirmed_chronological_long_leg():
    bars = [
        _causal_fib_bar(1, 11, 9, 10),
        _causal_fib_bar(2, 10, 7, 9),   # confirmed swing low after next bar
        _causal_fib_bar(3, 12, 9, 11),
        _causal_fib_bar(4, 15, 10, 14), # confirmed swing high after next bar
        _causal_fib_bar(5, 13, 9, 11),
    ]
    as_of = FIB_BASE + timedelta(hours=20)

    leg = select_confirmed_swing_leg(
        bars,
        as_of=as_of,
        direction="LONG",
        timeframe="4H",
        pivot_left=1,
        pivot_right=1,
    )

    assert leg is not None
    assert leg.start_price == 7
    assert leg.end_price == 15
    assert leg.start_at < leg.end_at < leg.confirmed_at
    evidence = project_fibonacci(leg)
    assert evidence
    assert all(item.projection_only for item in evidence)
    assert len({item.independence_key for item in evidence}) == 1
    payload = fibonacci_payload_adapter(leg, lookback_bars=len(bars))
    assert payload["levels"]["0%"] == 15
    assert payload["levels"]["100%"] == 7
    assert payload["levels"]["127%"] > 15
    assert payload["meta"]["projection_only"] is True


def test_canonical_fibonacci_rejects_wrong_extrema_order_for_long():
    bars = [
        _causal_fib_bar(1, 10, 9, 9.5),
        _causal_fib_bar(2, 15, 10, 14), # high occurs first
        _causal_fib_bar(3, 11, 8, 9),
        _causal_fib_bar(4, 10, 5, 6),   # low occurs later
        _causal_fib_bar(5, 11, 7, 10),
    ]
    as_of = FIB_BASE + timedelta(hours=20)

    assert select_confirmed_swing_leg(
        bars,
        as_of=as_of,
        direction="LONG",
        timeframe="4H",
        pivot_left=1,
        pivot_right=1,
    ) is None
    short_leg = select_confirmed_swing_leg(
        bars,
        as_of=as_of,
        direction="SHORT",
        timeframe="4H",
        pivot_left=1,
        pivot_right=1,
    )
    assert short_leg is not None
    assert short_leg.start_price == 15
    assert short_leg.end_price == 5


def test_canonical_fibonacci_running_bar_cannot_confirm_swing_end():
    bars = [
        _causal_fib_bar(1, 11, 9, 10),
        _causal_fib_bar(2, 10, 7, 9),
        _causal_fib_bar(3, 12, 9, 11),
        _causal_fib_bar(4, 15, 10, 14),
        _causal_fib_bar(5, 13, 9, 11, closed=False),
    ]
    as_of = FIB_BASE + timedelta(hours=20)

    assert select_confirmed_swing_leg(
        bars,
        as_of=as_of,
        direction="LONG",
        timeframe="4H",
        pivot_left=1,
        pivot_right=1,
    ) is None

    bars[-1]["is_closed"] = True
    assert select_confirmed_swing_leg(
        bars,
        as_of=as_of,
        direction="LONG",
        timeframe="4H",
        pivot_left=1,
        pivot_right=1,
    ) is not None


def test_api_fibonacci_wrapper_uses_confirmed_completed_chronological_leg_when_timestamps_exist():
    bars = [
        _causal_fib_bar(1, 12, 9, 10),
        _causal_fib_bar(2, 11, 8, 9),
        _causal_fib_bar(3, 10, 7, 8),
        _causal_fib_bar(4, 12, 9, 11),
        _causal_fib_bar(5, 14, 10, 13),
        _causal_fib_bar(6, 16, 12, 15),
        _causal_fib_bar(7, 14, 10, 11),
        _causal_fib_bar(8, 13, 9, 10),
        _causal_fib_bar(9, 12, 8, 9),
    ]
    payload = _calculate_directional_fib_levels(
        [bar["high"] for bar in bars],
        [bar["low"] for bar in bars],
        [bar["close"] for bar in bars],
        timeframe="4H",
        direction="LONG",
        times=[bar["open_time"] for bar in bars],
        as_of=FIB_BASE + timedelta(hours=36),
    )

    assert payload["levels"]["0%"] == 16
    assert payload["levels"]["100%"] == 7
    assert payload["meta"]["model"] == "confirmed_directional_retracement_v3"
    assert payload["meta"]["causal_timestamps_available"] is True
    assert payload["meta"]["structural_barrier"] is False


def test_api_fibonacci_wrapper_is_prefix_invariant_to_post_cutoff_extreme_bar():
    bars = [
        _causal_fib_bar(1, 12, 9, 10),
        _causal_fib_bar(2, 11, 8, 9),
        _causal_fib_bar(3, 10, 7, 8),
        _causal_fib_bar(4, 12, 9, 11),
        _causal_fib_bar(5, 14, 10, 13),
        _causal_fib_bar(6, 16, 12, 15),
        _causal_fib_bar(7, 14, 10, 11),
        _causal_fib_bar(8, 13, 9, 10),
        _causal_fib_bar(9, 12, 8, 9),
    ]
    cutoff = FIB_BASE + timedelta(hours=36)

    def payload(rows):
        return _calculate_directional_fib_levels(
            [bar["high"] for bar in rows],
            [bar["low"] for bar in rows],
                [bar["close"] for bar in rows],
                timeframe="4H",
                direction="LONG",
                times=[bar["open_time"] for bar in rows],
            as_of=cutoff,
        )

    prefix = payload(bars)
    future_extreme = _causal_fib_bar(10, 110, 90, 100)

    assert prefix
    assert payload(bars + [future_extreme]) == prefix


def test_api_fibonacci_explicit_timestamp_path_fails_closed_on_any_length_gap():
    highs = [12, 11, 10, 12, 14, 16, 14, 13, 12]
    lows = [9, 8, 7, 9, 10, 12, 10, 9, 8]
    closes = [10, 9, 8, 11, 13, 15, 11, 10, 9]
    times = [FIB_BASE + timedelta(hours=4 * index) for index in range(9)]
    cutoff = FIB_BASE + timedelta(hours=36)

    assert _calculate_directional_fib_levels(
        highs, lows, closes, timeframe="4H", times=times[:-1], as_of=cutoff
    ) == {}
    assert _calculate_directional_fib_levels(
        highs, lows, closes, timeframe="4H", times=[], as_of=cutoff
    ) == {}


def test_fibonacci_timeframe_ratio_labels_and_provenance_are_unambiguous():
    kwargs = {
        "leg_id": "leg-contract",
        "direction": "LONG",
        "start_price": 100.0,
        "start_at": FIB_BASE,
        "end_price": 110.0,
        "end_at": FIB_BASE + timedelta(days=1),
        "confirmed_at": FIB_BASE + timedelta(days=2),
        "data_cutoff_at": FIB_BASE + timedelta(days=3),
        "timeframe": "daily",
        "start_pivot_index": 1,
        "end_pivot_index": 2,
    }
    leg = ConfirmedSwingLeg(**kwargs, provenance={"source": "unit"})
    levels = project_fibonacci(
        leg,
        retracements=(0.381, 0.382),
        extensions=(),
    )

    assert leg.timeframe == "1D"
    assert {level.timeframe for level in levels} == {"1D"}
    assert {level.source_name for level in levels} == {"FIB 38.1%", "FIB 38%"}
    assert len({level.source_name for level in levels}) == 2

    with pytest.raises(ValueError, match="JSON-serialisable"):
        ConfirmedSwingLeg(**kwargs, provenance={"not_json": {1, 2}})


def test_turtle_score_is_capped_when_live_confirmation_is_weak():
    capped, flags = _turtle_score_cap(95, change_pct=0.51, rvol=1.29, breakout_pct=0.4)

    assert capped == 79
    assert _strategy_score_to_grade(capped) == "A"
    assert "Tagesmomentum noch schwach" in flags


def test_turtle_decoration_syncs_stale_score_and_grade(monkeypatch):
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda *args, **kwargs: ({"BANX"}, "unit"),
    )
    row = {
        "Ticker": "BANX",
        "Score": 95,
        "Grade": "C",
        "Change_Pct": 0.51,
        "RVOL": 1.29,
        "Breakout_Pct": 0.4,
    }

    decorated = _decorate_scan_results([row], "turtle", cache_age_seconds=30)[0]

    assert decorated["setup_score"] == 79
    assert decorated["score"] == 45
    assert decorated["trade_signal"] == "NICHT_TRADEN"
    assert "invalid_trade_plan" in decorated["scanner_suppression_reasons"]
    assert decorated["raw_score"] == 95
    assert any("Turtle-Score gedeckelt" in warning for warning in decorated["_quality"]["warnings"])
