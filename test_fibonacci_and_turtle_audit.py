from api import (
    _calculate_directional_fib_levels,
    _decorate_scan_results,
    _strategy_score_to_grade,
    _turtle_score_cap,
)


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


def test_turtle_score_is_capped_when_live_confirmation_is_weak():
    capped, flags = _turtle_score_cap(95, change_pct=0.51, rvol=1.29, breakout_pct=0.4)

    assert capped == 79
    assert _strategy_score_to_grade(capped) == "A"
    assert "Tagesmomentum noch schwach" in flags


def test_turtle_decoration_syncs_stale_score_and_grade():
    row = {
        "Ticker": "BANX",
        "Score": 95,
        "Grade": "C",
        "Change_Pct": 0.51,
        "RVOL": 1.29,
        "Breakout_Pct": 0.4,
    }

    decorated = _decorate_scan_results([row], "turtle", cache_age_seconds=30)[0]

    assert decorated["score"] == 79
    assert decorated["grade"] == "A"
    assert decorated["raw_score"] == 95
    assert any("Turtle-Score gedeckelt" in warning for warning in decorated["_quality"]["warnings"])
