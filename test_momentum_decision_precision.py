"""Decision arithmetic uses exact synthetic OHLC values; external I/O is forbidden."""
from copy import deepcopy
from datetime import timedelta
import math

import pytest

import api
from modules import stock_swing_contract as swing
from test_gap_cache_session_freshness import cache_view


def scorer(*, strategy="Momentum Breakout Long", extension=2.496,
           atr=2., rvol=3., day_open=99., day_high=101.5, day_low=99.):
    direction = "SHORT" if "Short" in strategy else "LONG"
    price = 101.
    change = extension * atr
    close_pos = (price - day_low) / (day_high - day_low)
    if direction == "SHORT":
        price, day_open, day_high, day_low = (
            200-price, 200-day_open, 200-day_low, 200-day_high)
        change, close_pos = -change, 1-close_pos
    score, meta = api._score_strategy_candidate(
        strategy_name=strategy, filters={}, change_pct=change, rvol=rvol,
        close_pos=close_pos, dollar_vol=100_000_000., gap_pct=0., vortag_pct=0.,
        price=price, day_open=day_open, day_high=day_high, day_low=day_low,
        prev_atr_pct=atr,
    )
    return score, meta, dict(price=price, change_pct=change, rvol=rvol,
                            close_pos=close_pos, day_high=day_high, day_open=day_open)


def quality(meta, values):
    return api._stock_momentum_breakout_continuation_quality(
        "Momentum Breakout Long", {"high_20d": 100.}, meta,
        breakout_type="20D_HIGH_BREAKOUT", completed_daily=True,
        **{key: values[key] for key in ("price", "change_pct", "rvol", "close_pos")},
    )


def mail_quality(meta, values, result):
    row = dict(Strategy="Momentum Breakout Long", direction="LONG",
        Preis=values["price"], RVOL=values["rvol"], Close_Position=values["close_pos"],
        Change_Pct=values["change_pct"], Day_High=values["day_high"],
        Day_Open=values["day_open"], TP1=110., History_OK=True,
        MedianDollarVol20=100_000_000., Upper_Wick_Pct=meta["upper_wick_pct"],
        Breakout_Freshness_Checked=True, Breakout_Freshness_Status="DAILY_CONFIRMED",
        Momentum_Breakout_Type="20D_HIGH_BREAKOUT",
        Breakout_Continuation_Score=result["score"],
        Breakout_Continuation_Status=result["status"], Breakout_Fakeout_Risk=result["risk"])
    row.update(swing.metadata("2026-09-23", values["price"]))
    before = deepcopy(row)
    outcome = api._stock_strategy_mail_quality_state(row, daily_close_confirmed_mode=True)
    assert row == before
    return outcome


def test_extension_below_existing_boundary_cannot_be_rounded_into_false_mail_rejection():
    setup_score, meta, values = scorer()
    result = quality(meta, values)
    assert setup_score == 84
    assert meta["extension_atr"] == pytest.approx(2.496)
    assert result["components"]["timing"] == 10.
    assert result["score"] == 81 and result["status"] == "CONTINUATION_OK"
    assert mail_quality(meta, values, result) == (True, "")
    # Reproduce the old loss of four points without changing the threshold.
    old_meta = dict(meta, extension_atr=round(meta["extension_atr"], 2))
    old_result = quality(old_meta, values)
    assert old_result["score"] == 77
    assert mail_quality(old_meta, values, old_result) == (
        False, "momentum_mail_blocked_daily_quality_below_threshold")


@pytest.mark.parametrize("extension,timing", [(2.496, 10.), (2.4999, 10.), (2.5, 6.), (2.504, 6.)])
def test_daily_quality_keeps_exact_existing_2_5_atr_boundary(extension, timing):
    _, meta, values = scorer(extension=extension)
    result = quality(meta, values)
    assert result["components"]["timing"] == timing
    assert mail_quality(meta, values, result)[0] is (extension < 2.5)


@pytest.mark.parametrize("strategy", ["Momentum Breakout Long", "Gap Momentum Short"])
@pytest.mark.parametrize("extension", [2.496, 2.4999, 2.5, 2.504])
def test_shared_scorer_preserves_long_and_short_decision_extension(strategy, extension):
    _, meta, _ = scorer(strategy=strategy, extension=extension, atr=2.004)
    assert meta["extension_atr"] == pytest.approx(extension)
    assert meta["atr_pct"] == pytest.approx(2.004)
    if "Short" in strategy:
        assert meta["direction"] == "short"
        # Momentum's continuation/mail quality is LONG only; do not invent a
        # short Momentum contract merely because the shared scorer handles it.
        assert api._stock_momentum_breakout_continuation_quality(
            strategy, {}, meta, breakout_type="10D_HIGH_BREAKOUT", price=99.,
            change_pct=-4.992, rvol=3., close_pos=.2, completed_daily=True) == {}


def test_ohlc_wick_rounding_cannot_reduce_passing_daily_quality_to_77():
    # The open is above the close, so the exact upper wick is 15.051 percent.
    rvol = .7 * math.exp((14.521 / 20.) * math.log(3. / .7))
    _, meta, values = scorer(extension=2./3., atr=3., rvol=rvol,
                             day_open=101.5 - 2.5 * .15051)
    assert meta["upper_wick_pct"] == pytest.approx(15.051)
    result = quality(meta, values)
    assert result["score"] == 78
    assert mail_quality(meta, values, result) == (True, "")
    old_meta = dict(meta, upper_wick_pct=round(meta["upper_wick_pct"], 1))
    old_result = quality(old_meta, values)
    assert old_result["score"] == 77
    assert mail_quality(old_meta, values, old_result) == (
        False, "momentum_mail_blocked_daily_quality_below_threshold")


def test_setup_score_is_not_a_circular_daily_quality_input():
    _, meta, values = scorer()
    assert quality(dict(meta, setup_score=0.), values) == quality(dict(meta, setup_score=100.), values)


def test_raw_relative_volume_stays_below_existing_1_5_mail_floor():
    _, meta, values = scorer(extension=1., rvol=1.496, day_high=101.1)
    result = quality(meta, values)
    assert result["score"] >= 78
    assert mail_quality(meta, values, result) == (
        False, "momentum_mail_blocked_rvol_below_breakout_floor")


def test_liquidity_baseline_excludes_reference_and_future_sessions():
    cutoff = swing.session_close("2026-09-23")
    day = cutoff.date() - timedelta(days=1)
    history = []
    while len(history) < 20:
        if swing.session_close(day.isoformat()) is not None:
            history.append(dict(date=day.isoformat(), open=99., high=100., low=98.,
                                close=99., volume=100_000.))
        day -= timedelta(days=1)
    history.reverse()
    current = dict(date="2026-09-23", open=99., high=101.5, low=99., close=101., volume=900_000.)
    future = dict(current, date="2026-09-24", close=100_000., high=100_000., volume=9_000_000.)
    args = dict(price=101., day_open=99., day_high=101.5, day_low=99., day_volume=900_000.,
                now_utc=cutoff, include_structure=False, signal_session="2026-09-23")
    expected = api._strategy_daily_history_metrics([*history, current], **args)
    actual = api._strategy_daily_history_metrics([future, current, *reversed(history)], **args)
    assert actual == expected
    assert actual["baseline_bars"] == 20 and actual["completed_bars"] == 21
    assert actual["median_dollar_vol20"] == 9_900_000.
    assert actual["high_20d"] == 100. and actual["rvol20_raw"] == 9.


def test_reference_high_cent_rounding_is_a_quantified_gate_boundary_not_target_self_reference():
    _, meta, values = scorer(extension=1., rvol=3.)
    result = quality(meta, values)
    # Existing 0.5% room requirement remains unchanged. Adjusted provider OHLC
    # can be sub-cent even when the native target is rounded to cents.
    values = dict(values, day_high=101.486)
    row = dict(Strategy="Momentum Breakout Long", Preis=101., RVOL=3.,
        Close_Position=.8, Upper_Wick_Pct=20., Change_Pct=2., Day_Open=99.,
        Day_High=values["day_high"], TP1=102., History_OK=True, MedianDollarVol20=100_000_000.,
        Momentum_Breakout_Type="20D_HIGH_BREAKOUT", Breakout_Continuation_Score=result["score"],
        Breakout_Continuation_Status=result["status"], Breakout_Fakeout_Risk=result["risk"])
    row.update(swing.metadata("2026-09-23", 101.))
    assert values["day_high"] < row["TP1"] * .995
    assert api._stock_strategy_mail_quality_state(row, daily_close_confirmed_mode=True) == (True, "")
    rounded = dict(row, Day_High=round(values["day_high"], 2))
    assert api._stock_strategy_mail_quality_state(rounded, daily_close_confirmed_mode=True) == (
        False, "momentum_mail_blocked_daily_target_previously_touched")


@pytest.mark.parametrize("version,expected", [(18, None), (api.STOCK_STRATEGY_CACHE_VERSION, 970.)])
def test_rounded_18_cache_cannot_defer_precision_rebuild_at_startup(monkeypatch, version, expected):
    assert api.STOCK_STRATEGY_CACHE_VERSION > 18
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {"strategy_scan": "fixture-cache"})
    monkeypatch.setattr(api.os.path, "getmtime", lambda *a: 970.)
    monkeypatch.setattr(api, "_AUTO_STOCK_ALERT_STRATEGIES", ["Momentum Breakout Long"])
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: "fixture-leaf")
    monkeypatch.setattr(api, "_scan_cache_payload", lambda *a: {
        "results": [], "cache_version": version,
        "diagnostics": {"coverage": "complete"}})
    assert api._startup_scan_cache_time("strategy_scan", 1000.) == expected


def test_rounded_18_strategy_cache_cannot_be_returned_as_current_precision(cache_view):
    cache_view["rows"] = [{"ticker": "TEST", "Strategy": "Momentum Breakout Long",
                            "Extension_ATR": 2.50, "Breakout_Continuation_Score": 77}]
    cache_view["meta"] = {"cache_version": 18, "diagnostics": {
        "strategy": "Momentum Breakout Long", "coverage": "complete"}}
    response = api.get_scan_results(strategy="Momentum Breakout Long", market_type="stocks")
    assert response.data == []
    assert response.diagnostics["warning"] == "strategy_cache_version_old_scan_again"
    assert response.diagnostics["required_cache_version"] == api.STOCK_STRATEGY_CACHE_VERSION


def test_rounded_18_cache_cannot_authorize_structure_reminder(monkeypatch):
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: "fixture-leaf")
    monkeypatch.setattr(api, "load_cache_metadata", lambda *a: {"cache_version": 18})
    monkeypatch.setattr(api, "load_cache_file", lambda *a: pytest.fail("old cache must stop before rows"))
    with pytest.raises(ValueError, match="server_scanner_cache_version_old"):
        api._structure_reminder_server_row("TEST", "Momentum Breakout Long", "LONG")


def producer_row(monkeypatch, *, price=101.004, previous=98.0034, day_open=99.0034,
                 day_high=101.486, day_low=98.5034, metrics=None):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, _metrics, NOW, NAME
    from test_stock_starter_swing import payload, matching_swing_history
    written = _wrapper_fixture(monkeypatch, metrics=metrics or _metrics())
    session = swing.completed_sessions(NOW, 1)[0]
    source = payload(session, price)
    source["results"][0].update(o=day_open, h=day_high, l=day_low)
    feed = swing.universe(swing.parse_grouped(source, session),
        {"TEST": {"c": previous, "v": 1_000_000.}}, session)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: feed)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: matching_swing_history(feed[0]))
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **k: pytest.fail("daily, not a live trigger"))
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert len(rows) == 1 and written[0][0] == rows
    assert written[0][1]["metadata"]["cache_version"] == api.STOCK_STRATEGY_CACHE_VERSION
    return rows[0]


def test_real_daily_producer_preserves_adjusted_ohlc_at_tp1_room_boundary(monkeypatch):
    row = producer_row(monkeypatch)
    assert row["price"] == row["Preis"] == row["swing_reference_close"] == 101.004
    assert {key: row[key] for key in ("Day_Open", "Day_High", "Day_Low", "Prev_Close")} == {
        "Day_Open": 99.0034, "Day_High": 101.486, "Day_Low": 98.5034, "Prev_Close": 98.0034}
    # This isolates mail quality, not plan admission. The native plan builder
    # is separately covered and these assertions never claim full release.
    candidate = dict(row, TP1=102.)
    assert api._stock_strategy_mail_quality_state(candidate, daily_close_confirmed_mode=True) == (True, "")
    old = dict(candidate, Day_High=round(candidate["Day_High"], 2))
    assert api._stock_strategy_mail_quality_state(old, daily_close_confirmed_mode=True) == (
        False, "momentum_mail_blocked_daily_target_previously_touched")


@pytest.mark.parametrize("field", ["change_pct", "open_to_current_pct"])
def test_real_daily_producer_cannot_round_7_996_percent_into_8_percent_chase(monkeypatch, field):
    if field == "change_pct":
        previous, day_open, high, low = 101./1.07996, 99., 101.22, 98.5
    else:
        previous, day_open, high, low = 99., 101./1.07996, 103.75, 90.
    row = producer_row(monkeypatch, price=101., previous=previous, day_open=day_open,
                       day_high=high, day_low=low)
    assert row[field] == pytest.approx(7.996)
    candidate = dict(row, TP1=110.)
    assert api._stock_strategy_mail_quality_state(candidate, daily_close_confirmed_mode=True) == (True, "")
    old = dict(candidate, **{field: round(candidate[field], 2)})
    if field == "change_pct":
        old["Change_Pct"] = old[field]
    assert api._stock_strategy_mail_quality_state(old, daily_close_confirmed_mode=True) == (
        False, "momentum_mail_blocked_daily_move_extended")


@pytest.mark.parametrize("atr,price,allowed", [(2.004, 100.15, True), (1.9996, 100., False)])
def test_real_daily_producer_keeps_atr_on_correct_side_of_2_percent_floor(monkeypatch, atr, price, allowed):
    from test_stock_momentum_confirmed_contract import _metrics
    row = producer_row(monkeypatch, price=price, previous=98., day_open=98.,
        day_high=price+.05, day_low=97.,
        metrics=_metrics(atr14=atr, atr_pct=atr/price*100, high_20d=99., high_10d=98.5))
    assert row["ATR14"] == atr
    assert row["ATR_Pct"] == pytest.approx(atr/price*100)
    candidate = dict(row, TP1=105.)
    outcome = api._stock_strategy_mail_quality_state(candidate, daily_close_confirmed_mode=True)
    assert outcome[0] is allowed
    if not allowed:
        assert outcome[1] == "stock_swing_mail_blocked_low_volatility_budget"
    old_outcome = api._stock_strategy_mail_quality_state(
        dict(candidate, ATR14=round(row["ATR14"], 2)), daily_close_confirmed_mode=True)
    assert old_outcome[0] is not allowed


@pytest.mark.parametrize("atr,price,allowed", [(2.004, 100.15, True), (1.9996, 100., False)])
def test_native_plan_atr_reader_keeps_original_value_not_tick_formatted_metadata(atr, price, allowed):
    plan = api._build_structured_trade_setup("LONG", price, atr, 98., 110., 104., 95., 50.)
    assert plan is not None
    assert plan["atr"] == atr
    row = dict(Strategy="Gap Momentum Long", Preis=price, ATR14=atr, trade_setup=plan)
    assert api._alert_atr_value(row) == atr
    assert api._stock_strategy_mail_quality_state(row)[0] is allowed
    old = dict(row, trade_setup=dict(plan, atr=round(plan["atr"], 2)))
    assert api._stock_strategy_mail_quality_state(old)[0] is not allowed


def test_completed_daily_close_position_0_6496_cannot_be_promoted_to_0_65():
    _, meta, values = scorer(extension=2./3., atr=3., day_low=99.,
        day_high=102.07881773399014, day_open=102.07881773399014)
    assert values["close_pos"] == pytest.approx(.6496)
    result = quality(meta, values)
    assert result["score"] == 78
    assert mail_quality(meta, values, result) == (
        False, "momentum_mail_blocked_not_holding_upper_range")
    assert mail_quality(meta, dict(values, close_pos=round(values["close_pos"], 2)), result) == (True, "")


def test_real_daily_producer_cannot_promote_under_65_percent_close_position(monkeypatch):
    # Same close-position geometry, scaled to a legitimate sub-3% gap so the
    # producer's real gap-open deduction does not mask the holding-range gate.
    low = 100.9
    high = low + (101.-low)/.6496
    row = producer_row(monkeypatch, price=101., previous=99., day_open=high,
                       day_high=high, day_low=low)
    assert row["Close_Position"] == row["close_pos"] == pytest.approx(.6496)
    assert row["Breakout_Continuation_Score"] == 78
    candidate = dict(row, TP1=110.)
    assert api._stock_strategy_mail_quality_state(candidate, daily_close_confirmed_mode=True) == (
        False, "momentum_mail_blocked_not_holding_upper_range")
    old = dict(candidate, Close_Position=round(row["Close_Position"], 2), close_pos=round(row["close_pos"], 2))
    assert api._stock_strategy_mail_quality_state(old, daily_close_confirmed_mode=True) == (True, "")
