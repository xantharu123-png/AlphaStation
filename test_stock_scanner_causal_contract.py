"""Stock public-family regressions: deterministic fixtures, no live I/O."""
from datetime import datetime, timedelta, timezone
import inspect

import api


def _day(date, close, *, open_=None):
    open_ = close if open_ is None else open_
    return {"date": date, "open": open_, "high": max(open_, close) + 1,
            "low": min(open_, close) - 1, "close": close, "volume": 1_000_000}


def test_previous_day_return_includes_gap_not_just_candle_body():
    rows = [_day("2026-09-01", 100), _day("2026-09-02", 110, open_=120), _day("2026-09-03", 150)]
    # Prior candle was -8.33% open-to-close, but +10% close-to-close.
    assert round(api._stock_previous_session_change(rows, as_of=datetime(2026, 9, 3, 16, tzinfo=timezone.utc)), 8) == 10.0


def test_pattern_history_never_uses_forming_or_conflicting_daily_bar():
    as_of = datetime(2026, 9, 3, 16, tzinfo=timezone.utc)
    rows = [_day("2026-09-01", 100), _day("2026-09-02", 110), _day("2026-09-03", 150)]
    assert len(api._stock_completed_pattern_history(rows, as_of=as_of)) == 2
    assert len(api._stock_completed_pattern_history(rows + [_day("2026-09-02", 111)], as_of=as_of)) == 1


def test_ma_long_cannot_turn_close_at_low_zero_into_neutral_half(monkeypatch):
    rows = [_day("2026-01-01", 50 + i) for i in range(65)]
    monkeypatch.setattr(api, "_get_ma_profiles", lambda *_: [{"ma_type": "SMA", "ma_period": 20,
                      "ma_distance_max": 2, "ma_approach": "from_above"}])
    monkeypatch.setattr(api, "_get_max_ma_period", lambda *_: 20)
    monkeypatch.setattr(api, "_calc_sma_series", lambda closes, period: [value / 1.01 for value in closes])
    base = {"_daily_bars": rows, "price": rows[-1]["close"], "Dollar_Volume": 5_000_000,
            "Change_Pct": 1, "Close_Position": 0.0, "RVOL": 2, "score": 80}
    assert api._apply_ma_strategy_filter(base, {}) is None
    assert api._apply_ma_strategy_filter({**base, "Close_Position": 0.5}, {}) is not None


def test_orb_requires_three_distinct_aligned_opening_intervals():
    start = datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)
    opening_ms = int(start.timestamp() * 1000)
    def bar(i):
        return {"t": opening_ms + i * 300_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 500}
    cutoff = start + timedelta(minutes=20)
    assert api._orb_completed_regular_bars([bar(0), bar(0), bar(1)], market_open_ms=opening_ms, as_of=cutoff) == ([], [])
    regular, opening = api._orb_completed_regular_bars([bar(i) for i in range(5)], market_open_ms=opening_ms, as_of=cutoff)
    assert len(regular) == 4
    assert len(opening) == 3


def test_turtle_volume_is_completed_not_projected_and_bear_clock_is_explicit():
    turtle = inspect.getsource(api._turtle_scan_wrapper)
    assert "completed_polygon_bars" in turtle
    assert "_project_us_equity_rvol(rvol_raw)" not in turtle
    assert '"completed_signal_session"' in turtle
    bear = inspect.getsource(api._bear_scan_wrapper)
    assert '_is_extended_hours = _bear_session in ("Pre-Market", "After-Hours")' in bear


def test_public_turtle_menu_does_not_accept_generic_consolidation(monkeypatch):
    rows = [_day("2026-01-01", 100) for _ in range(25)]
    monkeypatch.setattr(api, "analyze_multi_day_pattern", lambda *_: (True, 95, ["consolidation"]))
    candidate = {"_daily_bars": rows, "Dollar_Volume": 5_000_000, "base_score": 80}
    assert api._apply_pattern_strategy_filter(candidate, api.STRATEGIES["Turtle Breakout"]) is None


def test_public_turtle_menu_requires_completed_donchian_break_and_valid_plan():
    rows = [_day("2026-01-01", 100) for _ in range(24)] + [_day("2026-01-02", 102)]
    candidate = {"_daily_bars": rows, "Dollar_Volume": 5_000_000, "base_score": 80}
    result = api._apply_pattern_strategy_filter(candidate, api.STRATEGIES["Turtle Breakout"])
    assert result["pattern_type"] == "turtle_donchian20"
    assert result["Entry"] == 101
    assert result["StopLoss"] < result["Entry"] < result["TP1"] < result["TP2"]
    assert result["trade_setup"]["entry"] == result["Entry"]
