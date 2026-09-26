"""Signal-bar confirmation and prior-session references use different prefixes."""
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules.level_zones import (
    build_structure_snapshot, classify_for_trade, select_trade_structure,
)

UTC = timezone.utc
CLOSE = datetime(2026, 9, 25, 20, tzinfo=UTC)
OPEN = CLOSE.replace(hour=13, minute=30)


def candle(close_at, *, high, low=98., close=100., open_=100., span=timedelta(hours=6, minutes=30)):
    return dict(open_time=close_at-span, close_time=close_at, open=open_, high=high,
                low=low, close=close, volume=1_000_000., is_closed=True)


def mirror(bars, direction):
    if direction == "LONG":
        return bars
    return [dict(bar, open=200-bar["open"], high=200-bar["low"],
                 low=200-bar["high"], close=200-bar["close"]) for bar in bars]


def snapshot(direction, *, historical_high=101., bound=True, bar_closed=True):
    daily = [candle(CLOSE-timedelta(days=1), high=historical_high),
             candle(CLOSE, high=104.02, close=104., open_=102.)]
    daily[-1]["is_closed"] = bar_closed
    return build_structure_snapshot({"1D": mirror(daily, direction)},
        symbol="TEST", asset_class="stock", horizon="swing", as_of=CLOSE,
        current_price=104. if direction == "LONG" else 96.,
        tick_size=.01, atr_by_timeframe={"1D": 2.},
        session_reference_before=OPEN if bound else None)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_prior_session_level_can_be_confirmed_by_signal_close_without_retest(direction):
    state = snapshot(direction)
    side = classify_for_trade(state, entry=state.current_price, direction=direction)
    level = next(zone for zone in state.zones
                 if ("PDH" if direction == "LONG" else "PDL") in zone.source_names)
    assert level.confirmed_at == CLOSE-timedelta(days=1)
    assert level.break_state == "break_confirmed"
    assert level.break_reclaim_evidence.break_closed_at == CLOSE
    assert side.opposing_barriers == ()
    assert state.completed_bar_counts["1D"] == 2
    assert "session_levels_precede_signal_session" in state.to_dict()["quality_flags"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_live_default_still_uses_latest_completed_extreme(direction):
    state = snapshot(direction, bound=False)
    side = classify_for_trade(state, entry=state.current_price, direction=direction)
    assert len(side.opposing_barriers) == 1
    assert side.opposing_barriers[0].confirmed_at == CLOSE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_actual_previous_session_nearby_barrier_still_blocks(direction):
    state = snapshot(direction, historical_high=104.3)
    side = classify_for_trade(state, entry=state.current_price, direction=direction)
    assert len(side.opposing_barriers) == 1
    decision = select_trade_structure(side, stop=99. if direction == "LONG" else 101.)
    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.barrier_r < 1.35
    assert decision.nearest_barrier.confirmed_at < OPEN


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_current_wick_and_open_bar_do_not_confirm_crossed_previous_level(direction):
    state = snapshot(direction, bar_closed=False)
    level = next(zone for zone in state.zones
                 if ("PDH" if direction == "LONG" else "PDL") in zone.source_names)
    assert level.break_state == "intact"
    assert state.completed_bar_counts["1D"] == 1


def test_week_reference_precedes_signal_but_all_completed_week_bars_remain():
    bars = [candle(CLOSE-timedelta(days=7), high=101., span=timedelta(days=4, hours=6, minutes=30)),
            candle(CLOSE, high=104.02, close=104., span=timedelta(days=4, hours=6, minutes=30))]
    state = build_structure_snapshot({"1W": bars}, symbol="TEST", asset_class="stock",
        horizon="swing", as_of=CLOSE, current_price=104., tick_size=.01,
        session_reference_before=OPEN)
    assert state.completed_bar_counts["1W"] == 2
    pwh = next(z for z in state.zones if "PWH" in z.source_names)
    assert pwh.confirmed_at == CLOSE-timedelta(days=7)
    assert pwh.upper < 102.


def test_future_reference_boundary_is_rejected():
    with pytest.raises(ValueError, match="crosses snapshot"):
        build_structure_snapshot({}, symbol="TEST", asset_class="stock", horizon="swing",
            as_of=CLOSE, current_price=100., session_reference_before=CLOSE+timedelta(days=1))


@pytest.mark.parametrize("session,price,cutoff", [
    ("2026-09-24", 104., CLOSE),
    ("2026-09-25", 104.1, CLOSE),
    ("2026-09-25", 104., CLOSE+timedelta(seconds=1)),
    ("2026-09-25", 104., CLOSE-timedelta(seconds=1)),
    ("bad-session", 104., CLOSE),
])
def test_stock_signal_binding_fails_closed_for_wrong_day_price_or_clock(session, price, cutoff):
    daily = [dict(date="2026-09-24", open=100., high=101., low=98., close=100., volume=1e6),
             dict(date="2026-09-25", open=102., high=104.02, low=101., close=104., volume=2e6)]
    assert api._build_stock_level_snapshot(daily, symbol="TEST", current_price=price,
        direction="LONG", atr14=2., as_of=cutoff, signal_session=session) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_new_signal_retains_yesterdays_extreme_as_genuine_barrier(direction):
    daily = [candle(CLOSE-timedelta(days=1), high=104.3, close=103.),
             candle(CLOSE, high=104.02, close=104.)]
    # Yesterday may itself have been a breakout. For today's distinct signal
    # its known high is now prior evidence; it must not be exempted forever.
    state = build_structure_snapshot({"1D": mirror(daily, direction)},
        symbol="TEST", asset_class="stock", horizon="swing", as_of=CLOSE,
        current_price=104. if direction == "LONG" else 96., tick_size=.01,
        atr_by_timeframe={"1D": 2.}, session_reference_before=OPEN)
    barriers = classify_for_trade(state, entry=state.current_price, direction=direction).opposing_barriers
    assert len(barriers) == 1
    assert barriers[0].confirmed_at == CLOSE-timedelta(days=1)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("flag,value", [
    ("is_closed", False), ("complete", "false"), ("completed", 0), ("final", "open"),
])
def test_stock_date_adapter_does_not_promote_explicitly_open_signal_bar(direction, flag, value):
    bars = mirror([candle(CLOSE-timedelta(days=1), high=101.),
                   candle(CLOSE, high=104.02, close=104.)], direction)
    daily = [dict(bar, date=bar["close_time"].date().isoformat()) for bar in bars]
    daily[-1][flag] = value  # Even a conflicting is_closed=True cannot overrule this.
    state = api._build_stock_level_snapshot(daily, symbol="TEST",
        current_price=104. if direction == "LONG" else 96., direction=direction,
        atr14=2., as_of=CLOSE, signal_session="2026-09-25")
    assert state is None
    # The diagnostic Wyckoff adapter must retain the row and its clock, but
    # never let a positive alias overrule the explicitly unfinished state.
    diagnostic = api._stock_wyckoff_daily_input([
        {key: value for key, value in bar.items() if key not in {"open_time", "close_time"}}
        for bar in daily
    ])
    assert len(diagnostic) == len(daily)
    assert diagnostic[-1]["close_time"] == CLOSE
    assert diagnostic[-1]["is_closed"] is False
