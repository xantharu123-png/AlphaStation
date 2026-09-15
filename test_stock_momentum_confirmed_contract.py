"""True stock-breakout selection and pre-publication proof, no live I/O."""
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules.stock_momentum_contract import (
    MOMENTUM_CONTRACT_VERSION, cap_momentum_score, evaluate_momentum_breakout,
)


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
NAME = "Momentum Breakout Long"


def _metrics(**changes):
    return {"history_ok": True, "completed_bars": 70, "ema20": 95., "ema50": 94.,
            "rsi14": 60., "high_10d": 99.5, "high_20d": 100., "low_20d": 85.,
            "breakout_10d_pct": 2.5, "breakout_20d_pct": 2., "range_pos": 95.,
            "change_5d": 3., "atr14": 3., "atr_pct": 3., "rvol20": 3.,
            "rvol_source": "20D_completed_session", "avg_vol20": 1_000_000,
            "median_dollar_vol20": 100_000_000, **changes}


def _bar(index, close, *, open_=None, direction="LONG", **changes):
    open_ = close - .2 if open_ is None else open_
    high, low = max(open_, close) + .1, min(open_, close) - .1
    if direction == "SHORT":
        open_, high, low, close = 200-open_, 200-low, 200-high, 200-close
    return {"timestamp": int((NOW-timedelta(minutes=15)+timedelta(minutes=5*index)).timestamp()*1000),
            "open": open_, "high": high, "low": low, "close": close, "volume": 1000,
            **changes}


@pytest.mark.parametrize("range_pos", [70., 95.])
def test_range_strength_or_ema_reclaim_is_not_a_real_breakout(range_pos):
    metrics = _metrics(high_10d=120., high_20d=125., range_pos=range_pos)
    result = evaluate_momentum_breakout(metrics, price=100., change_pct=5.5, rvol=3., close_pos=1.)
    assert result["eligible"] is False
    assert result["breakout_type"] is None
    assert "no_momentum_breakout_structure" in result["reasons"]


@pytest.mark.parametrize("price,allowed", [(99.9, False), (100., False), (100.099, False), (100.1, True)])
def test_price_must_cross_actual_prior_high_and_existing_confirmation_buffer(price, allowed):
    # Stale/precomputed percentages are not proof of an actual price crossing.
    result = evaluate_momentum_breakout(_metrics(high_10d=100.), price=price,
                                       change_pct=3., rvol=3., close_pos=.95)
    assert result["eligible"] is allowed
    assert result["requires_intraday_confirmation"] is True


def test_10d_break_remains_valid_when_20d_resistance_is_higher():
    result = evaluate_momentum_breakout(_metrics(high_10d=100., high_20d=110.),
                                       price=100.2, change_pct=3., rvol=1.5, close_pos=.8)
    assert result["eligible"]
    assert result["breakout_type"] == "10D_HIGH_BREAKOUT"
    assert result["breakout_level"] == 100.


@pytest.mark.parametrize("changes", [{"price": True}, {"rvol": -1}, {"rvol": True},
                                     {"close_pos": 1.5}, {"close_pos": -0.1}, {"close_pos": True}])
def test_impossible_or_boolean_quote_inputs_fail_closed(changes):
    inputs = {"price": 102., "change_pct": 3., "rvol": 3., "close_pos": .9, **changes}
    result = evaluate_momentum_breakout(_metrics(), **inputs)
    assert result["eligible"] is False
    assert "invalid_momentum_inputs" in result["reasons"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("daily_close_mode", [False, True])
def test_previous_cross_cannot_confirm_latest_close_back_inside_level(direction, daily_close_mode):
    bars = [_bar(0, 99.5, direction=direction), _bar(1, 100.3, direction=direction),
            _bar(2, 99.9, direction=direction)]
    result = api._stock_breakout_freshness_state(
        {"ticker": "TEST", "direction": direction, "Breakout_Level": 100.},
        bars=bars, as_of=NOW, daily_close_confirmed_mode=daily_close_mode,
    )
    assert result["Breakout_Freshness_Status"] == "NOT_CONFIRMED"


def test_unclosed_future_and_conflicting_candles_never_confirm_breakout():
    before = [_bar(0, 99.5), _bar(1, 99.6), _bar(2, 99.7)]
    fake = [_bar(3, 102.), _bar(2, 103., is_closed=False), _bar(4, 104.)]
    row = {"ticker": "TEST", "direction": "LONG", "Breakout_Level": 100.}
    result = api._stock_breakout_freshness_state(row, bars=before+fake, as_of=NOW)
    assert result["Breakout_Freshness_Status"] == "NOT_CONFIRMED"
    assert result["Breakout_Confirmation_Close"] == 99.7
    conflict = api._stock_breakout_freshness_state(row, bars=before+[_bar(2, 104.)], as_of=NOW)
    assert conflict["Breakout_Freshness_Status"] == "DATA_UNAVAILABLE"


def test_freshness_accepts_seconds_milliseconds_and_naive_utc_cutoff():
    bars = [_bar(0, 99.5), _bar(1, 100.3), _bar(2, 100.5)]
    bars[1]["timestamp"] /= 1000
    result = api._stock_breakout_freshness_state(
        {"ticker": "TEST", "direction": "LONG", "Breakout_Level": 100.},
        bars=bars, as_of=NOW.replace(tzinfo=None),
    )
    assert result["Breakout_Freshness_Status"] == "FRESH_CROSS"
    assert result["Breakout_Age_Minutes"] == 5.
    assert result["Breakout_Confirmation_Age_Seconds"] == 0.


@pytest.mark.parametrize("legacy_type", ["TREND_RECLAIM", "RANGE_BREAKOUT", "MOMENTUM_BREAKOUT", ""])
def test_score_semantic_cap_runs_after_all_mdr_bonuses(legacy_type):
    assert cap_momentum_score(79+15, breakout_type=legacy_type, continuation_status="CONTINUATION_OK") == 79
    assert cap_momentum_score(95, breakout_type=legacy_type, continuation_status="FAKEOUT_RISK") == 64


def _current_row(**changes):
    return {"ticker": "TEST", "Strategy": NAME, "direction": "LONG", "price": 102.,
            "score": 90, "grade": "S", "trade_signal": "JETZT_TRADEN", "trade_action": "LONG_NOW",
            "Momentum_Contract_Version": MOMENTUM_CONTRACT_VERSION, "Momentum_Execution_Confirmed": True,
            "Momentum_Breakout_Type": "20D_HIGH_BREAKOUT", "Breakout_Level": 100.,
            "Breakout_Confirmation_Close": 101., "Breakout_Confirmation_Closed_At": NOW.isoformat(),
            "Breakout_Confirmation_Timeframe": "5m", "Breakout_Freshness_Status": "FRESH_CROSS",
            **changes}


def _freeze(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)


@pytest.mark.parametrize("change", [
    {"Momentum_Contract_Version": None}, {"Momentum_Execution_Confirmed": False},
    {"Momentum_Breakout_Type": "TREND_RECLAIM"}, {"Momentum_Breakout_Type": "RANGE_BREAKOUT"},
    {"Breakout_Confirmation_Close": 99.9}, {"price": 99.9}, {"direction": "SHORT"},
    {"Breakout_Confirmation_Closed_At": (NOW-timedelta(minutes=16)).isoformat()},
    {"Breakout_Confirmation_Closed_At": (NOW+timedelta(seconds=1)).isoformat()},
])
def test_signal_only_policy_rejects_invalid_cached_momentum_even_with_jetzt_label(monkeypatch, change):
    _freeze(monkeypatch)
    assert api._apply_signal_only_policy("strategy_scan", [_current_row(**change)]) == []
    assert api._apply_signal_only_policy("strategy_scan", [_current_row()])
    assert api._apply_signal_only_policy("strategy_scan", [_current_row(Strategy="Gap Momentum Long", **change)])


def _wrapper_fixture(monkeypatch, *, price=102., metrics=None, bars=None):
    _freeze(monkeypatch)
    written = []
    snapshot = {"ticker": "TEST", "day": {"o": 98., "h": price, "l": 96., "c": price, "v": 3_000_000},
                "prevDay": {"c": 98., "v": 1_000_000},
                "lastTrade": {"p": price, "t": int(NOW.timestamp()*1_000_000_000)},
                "lastQuote": {"p": price-.01, "P": price+.01}}
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: pytest.fail("unexpected live provider"))
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: [snapshot])
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"TEST"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    monkeypatch.setattr(api, "get_current_trading_session", lambda: ("Regular", "Regular"))
    monkeypatch.setattr(api, "_us_equity_expected_volume_fraction", lambda *a: 1.)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: [])
    monkeypatch.setattr(api, "_stock_previous_session_change", lambda *a, **kw: 11.)
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", lambda *a, **kw: metrics or _metrics())
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **kw: bars if bars is not None else [
        _bar(0, 99.5), _bar(1, 100.3), _bar(2, 101.)])
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", lambda *a, **kw: [])
    monkeypatch.setattr(api, "_build_stock_level_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", lambda rows, *a: rows)
    monkeypatch.setattr(api, "_enrich_stock_business_quality_rows", lambda rows: rows)
    monkeypatch.setattr(api, "_remove_partial_cache", lambda *a: None)
    monkeypatch.setattr(api, "save_partial_cache_file", lambda *a, **kw: None)
    monkeypatch.setattr(api, "finalize_cache_file", lambda path, rows, **kw: written.append((rows, kw)))
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", lambda *a, **kw: pytest.fail("mail disabled"))
    return written


def test_whole_wrapper_writes_confirmed_contract_before_cache(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert len(rows) == 1
    assert rows[0]["Momentum_Breakout_Type"] == "20D_HIGH_BREAKOUT"
    assert rows[0]["Momentum_Execution_Confirmed"] is True
    assert rows[0]["Momentum_Contract_Version"] == MOMENTUM_CONTRACT_VERSION
    assert rows[0]["Breakout_Freshness_Status"] == "FRESH_CROSS"
    assert api._stock_momentum_row_contract_valid(rows[0], as_of=NOW)
    assert written[0][0] == rows
    assert written[0][1]["metadata"]["cache_version"] == api.STOCK_STRATEGY_CACHE_VERSION


@pytest.mark.parametrize("bars", [[], [_bar(0, 99.5), _bar(1, 100.3), _bar(2, 99.9)]])
def test_whole_wrapper_never_caches_missing_or_lost_confirmation(monkeypatch, bars):
    written = _wrapper_fixture(monkeypatch, bars=bars)
    if not bars:
        with pytest.raises(api.ScannerDataError, match="scan_data_unavailable"):
            api._strategy_scan_wrapper(NAME, send_email=False)
        assert written == []  # Missing feed does not replace the last final cache.
        return
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert written[0][0] == []
    rejects = written[0][1]["metadata"]["diagnostics"]["rejected"]
    assert any(key.startswith("momentum:intraday_") for key in rejects)


def test_whole_wrapper_rejects_reclaim_before_fetching_execution(monkeypatch):
    written = _wrapper_fixture(monkeypatch, price=100., metrics=_metrics(high_10d=120., high_20d=125.))
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **kw: pytest.fail("not a breakout"))
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert written[0][1]["metadata"]["diagnostics"]["rejected"]["momentum_breakout_gate"] == 1


def test_whole_wrapper_exposes_scoped_provider_rejection(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    def unavailable(*a, **kw):
        raise api.req.ConnectionError("fixture provider unavailable")
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", unavailable)
    with pytest.raises(api.ScannerDataError, match="scan_data_unavailable") as caught:
        api._strategy_scan_wrapper(NAME, send_email=False)
    assert written == []
    rejects = caught.value.diagnostics["rejected"]
    assert rejects["scan_data_unavailable"] == 1
    assert "exception" not in rejects


def test_proof_that_expires_during_scan_is_not_published_as_new(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            later = NOW+timedelta(minutes=16)
            return later.astimezone(tz) if tz else later.replace(tzinfo=None)
    def delayed_levels(*args, **kwargs):
        monkeypatch.setattr(api, "datetime", LaterClock)
        return []
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", delayed_levels)
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert written[0][0] == []
    rejects = written[0][1]["metadata"]["diagnostics"]["rejected"]
    assert rejects["momentum:confirmation_expired_before_publication"] == 1


def test_real_decorator_then_signal_policy_cannot_reanimate_legacy_momentum(monkeypatch):
    _freeze(monkeypatch)
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: pytest.fail("unexpected provider"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {"regime": "NEUTRAL"}, "indices": {}})
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"TEST"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **kw: row)
    # Genuine geometry allows the real health/scanner decoration to yield
    # JETZT_TRADEN. The independent contract filter must still reject old rows.
    row = _current_row(
        RVOL=3., Close_Position=.95, Change_Pct=4., ATR14=3., Entry=102., StopLoss=98., TP1=110., TP2=115.,
        Trade_Setup_Source="stock_strategy_20d_structure", target_quality="STRUCTURAL_FIRST_BARRIER",
        structure_status="ACCEPT", Breakout_Continuation_Status="CONTINUATION_OK",
        Breakout_Continuation_Score=90, Upper_Wick_Pct=5., Swing_4H_Execution_Status="CLEAR",
        trade_setup={"direction": "LONG", "entry": 102., "stop": 98., "tp1": 110., "tp2": 115.,
                     "target_quality": "STRUCTURAL_FIRST_BARRIER", "structure_status": "ACCEPT",
                     "tp1_is_projection": False, "tp2_is_projection": False},
    )
    valid = api._decorate_scan_results([row], "strategy_scan", cache_age_seconds=10)
    legacy = api._decorate_scan_results([dict(row, Momentum_Contract_Version=None)], "strategy_scan", cache_age_seconds=10)
    assert valid[0]["trade_signal"] == legacy[0]["trade_signal"] == "JETZT_TRADEN"
    assert api._apply_signal_only_policy("strategy_scan", valid)
    assert api._apply_signal_only_policy("strategy_scan", legacy) == []
