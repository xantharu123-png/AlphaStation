"""Offline acceptance for stock audit F01/F06/F07/F08/F09 and session clocks."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from zoneinfo import ZoneInfo

import pytest
import api
from modules.stock_bars import completed_polygon_bars


NOW = datetime(2026, 9, 23, 14, 30, tzinfo=timezone.utc)
NY = ZoneInfo("America/New_York")


class Response:
    def __init__(self, payload=None, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


def freeze(monkeypatch, now=NOW):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())


def orb_row(direction="LONG", age=0):
    long = direction == "LONG"
    return dict(ticker="AUDT", direction=direction, current_price=100.1 if long else 97.9,
                signal_price=100.1 if long else 97.9, or_high=100, or_low=98,
                signal_bar_timestamp=(NOW.timestamp() - age - 300) * 1000,
                bar_state="completed_5m", entry=100 if long else 98,
                stop=99 if long else 99, target1=102 if long else 96,
                target2=103 if long else 95, score=95, grade="S", rvol=2.4,
                vol_confirmed=True, breakout_state="active_breakout", breakout_age_bars=2,
                recent_hold_pct=1.0, dollar_volume=12_000_000, entry_quality="GOOD")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("age,reason", [(0, None), (900, None), (900.001, "orb_completed_candle_stale"),
                                       (-.001, "orb_completed_candle_unverified")])
def test_orb_completed_clock_exact_existing_15m_limit(direction, age, reason):
    reasons = api._orb_signal_gate_reasons(orb_row(direction, age), as_of=NOW)
    assert reasons == ([] if reason is None else [reason])


@pytest.mark.parametrize("direction,current", [("LONG", 100), ("LONG", 99.99), ("SHORT", 98), ("SHORT", 98.01)])
def test_orb_current_side_is_strict(direction, current):
    row = orb_row(direction)
    row["current_price"] = current
    assert "orb_current_breakout_lost" in api._orb_signal_gate_reasons(row, as_of=NOW)


@pytest.mark.parametrize("field,value", [("signal_bar_timestamp", None), ("signal_bar_timestamp", float("nan")),
                                        ("bar_state", "forming"), ("current_price", float("inf")),
                                        ("or_high", None), ("or_low", 100)])
def test_orb_missing_invalid_evidence_never_actionable(field, value):
    row = orb_row()
    row[field] = value
    assert api._orb_signal_gate_reasons(row, as_of=NOW)


@pytest.mark.parametrize("now,current,expected", [
    (NOW.replace(hour=13, minute=50), 100.05, True),
    (NOW.replace(hour=13, minute=50), 99.98, False),
    (NOW, 100.05, False), (NOW, 99.98, False),
])
def test_actual_orb_wrapper_fresh_current_side_and_stale_feed(monkeypatch, now, current, expected):
    freeze(monkeypatch, now)
    start = int(now.replace(hour=13, minute=30).timestamp() * 1000)
    bars = [{"t": start + i * 300000, "o": 99.5, "h": 100, "l": 98.6,
             "c": 99.6, "v": 100000} for i in range(3)]
    bars.append({"t": start + 900000, "o": 99.95, "h": 100.15, "l": 99.9, "c": 100.1, "v": 300000})
    snapshot = {"tickers": [{"ticker": "AUDT", "day": {"o": 98, "h": 100.15, "l": 98,
                  "c": current, "v": 2000000}}]}
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Response({"results": bars} if "/aggs/" in url else snapshot))
    monkeypatch.setattr(api, "fetch_grouped_daily", lambda *a: {"AUDT": {"c": 99, "h": 101, "l": 97, "v": 1000000}})
    monkeypatch.setattr(api, "_fetch_orb_atr_pct", lambda *a: (2, "fixture"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **kw: row)
    monkeypatch.setattr(api, "_filter_open_equivalent_trade_rows", lambda *a, **kw: ([], 0))
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a, **kw: None)
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(rows))
    api._orb_scanner_wrapper()
    assert bool(saved[0]["actionable_breakouts"]) is expected
    row = saved[0]["breakouts"][0]
    if expected:
        assert row["trade_decision"] == "TRADEABLE"
        assert row["retest_warning"]
    else:
        assert api._orb_signal_gate_reasons(row, as_of=now)
        assert row["trade_decision"] == "NO_TRADE"
        assert not api._classify_alert_candidate("orb", row, now.timestamp())["alertable_now"]


def test_orb_cached_results_recheck_clock_without_a_new_scan(monkeypatch):
    freeze(monkeypatch)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    payload = [{"breakouts": [orb_row()], "failed_breakouts": [], "candidates": []}]
    fresh = api._decorate_orb_results(payload, 0)[0]
    assert len(fresh["actionable_breakouts"]) == 1
    freeze(monkeypatch, NOW + timedelta(seconds=901))
    aged = api._decorate_orb_results([fresh], 0)[0]  # Even a wrongly refreshed cache timestamp cannot renew proof.
    assert aged["actionable_breakouts"] == []
    assert aged["rejected_range_breaks"][0]["trade_decision"] == "NO_TRADE"
    assert "orb_completed_candle_stale" in aged["rejected_range_breaks"][0]["orb_gate_reasons"]


def daily_bars(count=25, session="2026-09-22"):
    cursor = datetime.fromisoformat(session).replace(tzinfo=NY)
    dates = []
    while len(dates) < count:
        if api.stock_swing.session_close(cursor.date().isoformat()) is not None:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return [{"t": int(day.timestamp() * 1000), "o": 99.4, "h": 100, "l": 99, "c": 99.5,
             "v": 1000000} for day in reversed(dates)]


def snapshot():
    return {"tickers": [{"ticker": "AUDT", "day": {"c": 99.9, "h": 100, "l": 99.8,
                         "o": 99.85, "v": 2000000}, "prevDay": {"c": 100.1, "v": 1000000}}]}


@pytest.mark.parametrize("wrapper", [api._turtle_scan_wrapper, api._volume_spikes_wrapper])
@pytest.mark.parametrize("fault", [503, 403, "exception", "error_body", "malformed", "empty"])
def test_required_bulk_outage_preserves_last_good_cache(monkeypatch, wrapper, fault):
    freeze(monkeypatch)
    def get(*a, **kw):
        if fault == "exception":
            raise TimeoutError("offline fixture")
        if isinstance(fault, int):
            return Response(status=fault)
        return Response({"error_body": {"status": "ERROR"}, "malformed": {"tickers": None},
                         "empty": {"tickers": []}}[fault])
    monkeypatch.setattr(api, "rate_limited_get", get)
    writes = []
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **kw: writes.append(a))
    with pytest.raises(api.ScannerDataError):
        wrapper()
    assert writes == []


@pytest.mark.parametrize("wrapper", [api._turtle_scan_wrapper, api._volume_spikes_wrapper])
def test_successful_bulk_zero_is_distinct_from_optional_movers_outage(monkeypatch, wrapper):
    freeze(monkeypatch)
    small = {"tickers": [{"ticker": "AUDT", "day": {"c": 1, "v": 1}, "prevDay": {"c": 1, "v": 1}}]}
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Response(small) if url.endswith("/tickers") else Response(status=503))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    writes = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: writes.append(rows))
    wrapper()
    assert writes == [[]]


@pytest.mark.parametrize("history", ["http_error", "exception", "malformed", "stale", "invalid_bar"])
def test_turtle_required_history_failure_preserves_cache(monkeypatch, history):
    freeze(monkeypatch)
    def get(url, **kw):
        if "/aggs/" not in url:
            return Response(snapshot())
        if history == "exception":
            raise TimeoutError()
        return {"http_error": Response(status=503), "malformed": Response({}),
                "invalid_bar": Response({"results": [{**daily_bars(1)[0], "c": float("nan")}]}),
                "stale": Response({"results": daily_bars(session="2026-09-21")})}[history]
    monkeypatch.setattr(api, "rate_limited_get", get)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    writes = []
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **kw: writes.append(a))
    with pytest.raises(api.ScannerDataError) as error:
        api._turtle_scan_wrapper()
    assert error.value.code == "scan_data_incomplete"
    assert writes == []


def test_turtle_partial_symbol_outage_does_not_publish_first_survivor(monkeypatch):
    freeze(monkeypatch)
    bars = daily_bars()
    bars[-1].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3000000)
    bulk = snapshot()
    bulk["tickers"].append({**deepcopy(bulk["tickers"][0]), "ticker": "MISS"})
    def get(url, **kw):
        if "/aggs/ticker/MISS/" in url:
            return Response(status=503)
        return Response({"results": bars} if "/aggs/" in url else bulk)
    monkeypatch.setattr(api, "rate_limited_get", get)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT", "MISS"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    writes = []
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **kw: writes.append(a))
    with pytest.raises(api.ScannerDataError):
        api._turtle_scan_wrapper()
    assert writes == []


@pytest.mark.parametrize("count", [0, 21, 25])
def test_turtle_successful_insufficient_or_no_break_history_is_legitimate_zero(monkeypatch, count):
    freeze(monkeypatch)
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Response({"results": daily_bars(count)} if "/aggs/" in url else snapshot()))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.append(rows))
    api._turtle_scan_wrapper()
    assert saved == [[]]


def test_volume_spike_positive_is_still_context_not_trade(monkeypatch):
    freeze(monkeypatch)
    bulk = snapshot()
    bulk["tickers"][0]["day"].update(c=105, h=105, v=6000000)
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Response(bulk) if url.endswith("/tickers") else Response(status=503))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(rows))
    api._volume_spikes_wrapper()
    assert len(saved) == 1 and saved[0]["rvol"] > 3
    assert saved[0]["execution_trigger_ok"] is False
    assert saved[0]["trade_action"] == "BEOBACHTEN"


def test_turtle_reference_and_snapshot_are_independent_coherent_observations(monkeypatch):
    freeze(monkeypatch)
    bars = daily_bars()
    bars[-1].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3000000)
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Response({"results": bars} if "/aggs/" in url else snapshot()))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(rows))
    api._turtle_scan_wrapper()
    row = saved[0]
    assert row["Preis"] == row["swing_reference_close"] == 100.1
    assert row["snapshot_price"] == 99.9 < row["DC_High_20"]
    assert row["Change_Pct"] == round((100.1 / 99.5 - 1) * 100, 2)
    assert row["snapshot_change_pct"] == -.2
    assert row["Volume"] == 3000000 and row["snapshot_volume"] == 2000000
    assert row["Dollar_Volume"] == 300300000
    assert api.stock_swing.validate(row, NOW)
    assert row["fill_evidence_verified"] is False
    assert row["snapshot_received_at"] != row["price_observed_at"]
    assert row["snapshot_updated_at"] is None  # No invented provider timestamp.
    assert row["StopLoss"] < row["Entry"] < row["TP1"] < row["TP2"]
    assert row["retest_warning"]


@pytest.mark.parametrize("minute,expected_session,expected_price", [(14, "2026-09-22", 100.1), (15, "2026-09-23", 101.1)])
def test_turtle_respects_completed_daily_availability_boundary(monkeypatch, minute, expected_session, expected_price):
    now = NOW.replace(hour=20, minute=minute)
    freeze(monkeypatch, now)
    bars = daily_bars(26, "2026-09-23")
    bars[-2].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3000000)
    bars[-1].update(o=100.2, h=101.2, l=100.2, c=101.1, v=3000000)
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Response({"results": bars} if "/aggs/" in url else snapshot()))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: None)
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(rows))
    api._turtle_scan_wrapper()
    assert saved[0]["swing_analysis_session"] == expected_session
    assert saved[0]["swing_reference_close"] == expected_price
    assert api.stock_swing.validate(saved[0], now)


@pytest.mark.parametrize("direction,period,count", [("Long", 21, 31), ("Long", 50, 60), ("Short", 50, 60), ("Short", 200, 210)])
def test_each_ma_profile_uses_own_sufficient_history(direction, period, count):
    strat = deepcopy(api.STRATEGIES[f"MA Bounce {direction}"])
    profile = next(p for p in strat["ma_profiles"] if p["ma_period"] == period)
    long = direction == "Long"
    closes = [200 + (i * .3 if long else -i * .3) for i in range(count)]
    ma = (api.calculate_ema_series(closes, period) if profile["ma_type"] == "EMA" else api._calc_sma_series(closes, period))[-1]
    row = {"_daily_bars": [{"close": c} for c in closes], "price": ma * (1.01 if long else .99),
           "Dollar_Volume": 5000000, "Change_Pct": -.5 if long else .5,
           "Close_Position": .7 if long else .3, "RVOL": 2., "base_score": 80}
    own = {**strat, "ma_profiles": [profile]}
    assert api._apply_ma_strategy_filter(row, own) is not None
    assert api._apply_ma_strategy_filter(row, strat) is not None
    assert api._apply_ma_strategy_filter({**row, "_daily_bars": row["_daily_bars"][:-1]}, own) is None
    assert api._apply_ma_strategy_filter({**row, "price": ma * (.99 if long else 1.01)}, own) is None


def test_ma_postfilter_requests_all_profiles_without_requiring_longest(monkeypatch):
    freeze(monkeypatch)
    bars = daily_bars(65)
    for i, bar in enumerate(bars):
        value = 100 + .2 * i
        bar.update(o=value, h=value + .5, l=value - .5, c=value)
    history = [{"date": datetime.fromtimestamp(b["t"] / 1000, NY).date().isoformat(),
                "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]} for b in bars]
    ma = api.calculate_ema_series([b["close"] for b in history], 21)[-1]
    candidate = dict(ticker="AUDT", price=ma * 1.01, Dollar_Volume=5000000,
                     Change_Pct=-.5, Close_Position=.7, RVOL=2., base_score=80)
    requested = []
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda ticker, count, *a: requested.append(count) or history)
    original = deepcopy(candidate)
    result = api._apply_special_strategy_post_filter([candidate], api.STRATEGIES["MA Bounce Long"], "MA Bounce Long")
    assert requested == [212]
    assert len(result) == 1 and result[0]["ma_type"] == "EMA 21"
    assert candidate == original


@pytest.mark.parametrize("count", [5, 6, 20, 21, 25])
def test_return_anchors_are_identical_before_and_after_numerator_close(count):
    source = daily_bars(count)
    bars = [{"date": datetime.fromtimestamp(b["t"] / 1000, NY).date().isoformat(),
             "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i,
             "volume": 1000000} for i, b in enumerate(source)]
    price = 99 + count
    kwargs = dict(price=price, day_open=price, day_high=price + 1, day_low=price - 1,
                  day_volume=1000000, include_structure=False)
    before = api._strategy_daily_history_metrics(bars, now_utc=NOW.replace(day=22, hour=19, minute=59), **kwargs)
    after = api._strategy_daily_history_metrics(bars, now_utc=NOW.replace(day=22, hour=20, minute=15), **kwargs)
    for days in (5, 20):
        key = f"change_{days}d"
        if count <= days:
            assert before[key] is after[key] is None
        else:
            expected = (price / (price - days) - 1) * 100
            assert before[key] == pytest.approx(expected)
            assert after[key] == pytest.approx(expected)


@pytest.mark.parametrize("session,closed", [("2026-11-27", "2026-11-27T18:00:00+00:00"),
    ("2026-07-01", "2026-07-01T20:00:00+00:00"),
    ("2026-07-02", "2026-07-02T20:00:00+00:00"),
    ("2026-12-24", "2026-12-24T18:00:00+00:00"),
    ("2027-07-02", "2027-07-02T20:00:00+00:00"),
    ("2027-11-26", "2027-11-26T18:00:00+00:00"),
    ("2028-07-03", "2028-07-03T17:00:00+00:00"),
    ("2028-11-24", "2028-11-24T18:00:00+00:00"),
    ("2026-03-06", "2026-03-06T21:00:00+00:00"), ("2026-03-09", "2026-03-09T20:00:00+00:00")])
def test_polygon_daily_completion_uses_shared_calendar_exact_boundary(session, closed):
    bar = daily_bars(1, session)[0]
    close = datetime.fromisoformat(closed)
    assert completed_polygon_bars([bar], as_of=close - timedelta(microseconds=1)) == []
    accepted = completed_polygon_bars([bar], as_of=close)
    assert len(accepted) == 1
    assert datetime.fromisoformat(accepted[0]["close_time"]) == close


@pytest.mark.parametrize("session", ["2026-07-03", "2026-09-07", "2026-09-26"])
def test_polygon_daily_bars_on_holiday_or_weekend_are_not_sessions(session):
    bar = dict(t=int(datetime.fromisoformat(session).replace(tzinfo=NY).timestamp() * 1000),
               o=10, h=11, l=9, c=10, v=100)
    assert completed_polygon_bars([bar], as_of=datetime(2027, 1, 1, tzinfo=timezone.utc)) == []
