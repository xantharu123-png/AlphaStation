"""Adversarial repair acceptance for F06/F13/F20/F21; all external I/O mocked."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import api
from modules import scanners


NOW = datetime(2026, 9, 23, 14, 30, tzinfo=timezone.utc)


class Response:
    def __init__(self, payload=None, status=200):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


def clock(monkeypatch, now=NOW):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)


def context_dependencies(monkeypatch):
    clock(monkeypatch)
    monkeypatch.setattr(api, "_fetch_polygon_market_headlines", lambda: ([], None))
    monkeypatch.setattr(api, "_calendar_event_risk_snapshot", lambda: {"score": 0, "level": "LOW", "data_status": "ok"})
    monkeypatch.setattr(api, "_fetch_treasury_rates_block", lambda: {"status": "missing"})


def optimistic_crash():
    return {"fear_score": 95, "vix": {"price": 11, "ticker": "I:VIX"},
            "breadth": {"ad_ratio": 3., "advancing_pct": 80}, "data_status": "ok"}


@pytest.mark.parametrize("source_time,fresh", [
    (NOW.replace(tzinfo=None).isoformat(), True),
    (NOW.isoformat(), True),
    ((NOW - timedelta(seconds=2700)).replace(tzinfo=None).isoformat(), True),
    ((NOW - timedelta(seconds=2701)).replace(tzinfo=None).isoformat(), False),
    ("2026-01-01T12:00:00", False),
    ((NOW + timedelta(days=1)).replace(tzinfo=None).isoformat(), False),
    (None, False), ("invalid-clock", False),
])
def test_market_context_never_renews_old_or_unverified_crash_evidence(monkeypatch, source_time, fresh):
    context_dependencies(monkeypatch)
    monkeypatch.setattr(api, "load_cache_file", lambda *a, **kw: ([optimistic_crash()], source_time))
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(rows))
    api._market_context_wrapper()
    context = saved[0]
    assert context["source"]["market_internals_observed_at"] == source_time
    if fresh:
        assert context["market_risk"]["data_status"] == "ok"
        assert context["market_risk"]["fear_score"] == 95
        assert context["regime"] == "RISK_ON"
    else:
        assert context["market_risk"]["data_status"] != "ok"
        assert context["regime"] != "RISK_ON"
        assert context["warnings"]


def test_fresh_derived_cache_cannot_hide_expired_source_on_read(monkeypatch):
    context_dependencies(monkeypatch)
    old = (NOW - timedelta(seconds=2701)).replace(tzinfo=None).isoformat()
    derived = {"regime": "RISK_ON", "source": {"market_internals_observed_at": old}}
    def load(path, **kw):
        return ([derived], NOW.replace(tzinfo=None).isoformat()) if path == api.MARKET_CONTEXT_CACHE else ([optimistic_crash()], old)
    monkeypatch.setattr(api, "load_cache_file", load)
    context = api._get_market_context_snapshot()
    assert context["cache_status"] == "stale"
    assert context["regime"] != "RISK_ON"


@pytest.mark.parametrize("field", ["change_1d", "change_5d", "change_20d"])
@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), True])
def test_narrative_requires_all_observed_return_horizons(field, missing):
    row = dict(ticker="PARTIAL", change_1d=25., change_5d=5., change_20d=5., rvol=2., cmf=.2, obv_change=10.)
    row[field] = missing
    payload = api._build_narrative_pulse([row])
    assert api._narrative_score(row) is None
    assert payload["all"][0]["bias"] == "UNBEKANNT"
    assert payload["bullish"] == payload["bearish"] == []
    assert payload["status"] == "partial"


@pytest.mark.parametrize("change,bias", [(0., "NEUTRAL"), (10., "BULLISCH"), (-10., "BEARISCH")])
def test_narrative_real_zero_and_signed_returns_keep_their_meaning(change, bias):
    row = dict(ticker="OBSERVED", change_1d=change, change_5d=change, change_20d=change,
               rvol=1., cmf=0., obv_change=0.)
    payload = api._build_narrative_pulse([row])
    assert payload["status"] == "success"
    assert payload["all"][0]["narrative_score"] == change
    assert payload["all"][0]["bias"] == bias


def proxy_perf(**updates):
    return dict(price=100, change_1d=0., change_5d=0., change_20d=0., volume=1000000,
                rvol=1., cmf=0., obv_change=0., **updates)


@pytest.mark.parametrize("mode", ["total", "partial", "exception"])
def test_moneyflow_required_proxy_outage_never_writes_or_dispatches(monkeypatch, mode):
    monkeypatch.setattr(api, "SECTOR_ETFS", {"ONE": "One", "TWO": "Two"})
    monkeypatch.setattr(api, "NARRATIVE_PROXIES", {})
    def fetch(ticker, **kw):
        if mode == "exception":
            raise TimeoutError("fixture")
        return proxy_perf() if mode == "partial" and ticker == "ONE" else None
    monkeypatch.setattr(api, "_fetch_daily_proxy_perf", fetch)
    writes, sent = [], []
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **kw: writes.append(a))
    monkeypatch.setattr(api, "_send_narrative_pulse_email", lambda *a: sent.append(a))
    with pytest.raises(api.ScannerDataError) as error:
        api._money_flow_wrapper()
    assert error.value.code == ("scan_data_incomplete" if mode == "partial" else "scan_data_unavailable")
    assert not writes and not sent


@pytest.mark.parametrize("partial", [True, False])
def test_moneyflow_valid_observations_stay_context_and_partial_never_dispatches(monkeypatch, partial):
    monkeypatch.setattr(api, "SECTOR_ETFS", {"ONE": "One"})
    monkeypatch.setattr(api, "NARRATIVE_PROXIES", {})
    perf = proxy_perf()
    if partial:
        perf.update(change_5d=None, change_20d=None, rvol=None, cmf=None, obv_change=None)
    monkeypatch.setattr(api, "_fetch_daily_proxy_perf", lambda *a, **kw: perf)
    saved, sent = {}, []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.update({path: rows}))
    monkeypatch.setattr(api, "_send_narrative_pulse_email", lambda payload: sent.append(payload))
    api._money_flow_wrapper()
    assert saved[api.MONEY_FLOW_CACHE][0]["execution_trigger_ok"] is False
    assert saved[api.MONEY_FLOW_CACHE][0]["trade_action"] == "BEOBACHTEN"
    assert saved[api.NARRATIVE_PULSE_CACHE]["status"] == ("partial" if partial else "success")
    assert len(sent) == (0 if partial else 1)


@pytest.mark.parametrize("quick", [False, True])
@pytest.mark.parametrize("failure", [503, 403, "error_body", "malformed", "exception", "partial"])
def test_biotech_news_outage_keeps_final_cache_and_reports_error(monkeypatch, quick, failure):
    universe = [{"ticker": f"BIO{i}", "name": f"Biotechnology{i}"} for i in range(5)]
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kw: universe)
    monkeypatch.setattr(scanners, "_biotech_cache_load", lambda **kw: [{"Ticker": "BIO0", "Score": 90}])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kw: set())
    calls, saved, progress = [], [], []
    def fetch(url, **kw):
        calls.append(kw.get("params", {}).get("ticker"))
        if failure == "exception":
            raise TimeoutError("fixture")
        if failure == "partial":
            return Response({"results": []}) if kw.get("params", {}).get("ticker") != "BIO0" else Response(status=503)
        if isinstance(failure, int):
            return Response(status=failure)
        return Response({"status": "ERROR", "results": []} if failure == "error_body" else {"results": None})
    monkeypatch.setattr(scanners, "rate_limited_get", fetch)
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda *a, **kw: saved.append((a, kw)))
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda *a, **kw: progress.append((a, kw)))
    runner = scanners._biotech_quick_scan if quick else scanners._biotech_background_scan
    with pytest.raises(scanners.ScannerDataError):
        runner("offline")
    assert len(calls) == 5
    assert not saved
    assert progress[-1][0] == ("error",)
    assert not any(event[0] == ("done",) for event in progress)


@pytest.mark.parametrize("quick", [False, True])
def test_biotech_successful_empty_news_is_legitimate_zero(monkeypatch, quick):
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kw: [{"ticker": "BIO", "name": "Biotechnology"}])
    monkeypatch.setattr(scanners, "_biotech_cache_load", lambda **kw: [{"Ticker": "BIO", "Score": 90}])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kw: set())
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({"results": []}))
    saved, progress = [], []
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda rows, **kw: saved.append(rows))
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda *a, **kw: progress.append(a))
    (scanners._biotech_quick_scan if quick else scanners._biotech_background_scan)("offline")
    assert saved == [[]] and progress[-1] == ("done",)


def bear_dependencies(monkeypatch):
    monkeypatch.setattr(api, "get_current_trading_session", lambda: ("Regular", "fixture"))
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": False, "reason": "offline"})
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **kw: None)


def test_inverse_etf_unknowns_sort_without_crashing_or_inventing_zero(monkeypatch):
    bear_dependencies(monkeypatch)
    monkeypatch.setattr(api, "INVERSE_ETFS", {"SHORT": ("Short index", "SPY"), "KNOWN": ("3x Short index", "QQQ"), "OTHER": ("Short index", "SPY")})
    def get(url, **kw):
        if "/aggs/" not in url:
            return Response(status=503)
        count = 6 if "/KNOWN/" in url else 2
        return Response({"results": [{"c": 12 if index == 0 else 10, "v": 1000000} for index in range(count)]})
    monkeypatch.setattr(api, "rate_limited_get", get)
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(rows))
    api._bear_scan_wrapper()
    rows = saved[0]["inverse_etfs"]
    assert rows[0]["ticker"] == "KNOWN" and rows[0]["change_5d"] == 20
    assert len(rows) == 3
    for row in rows[1:]:
        assert row["change_1d"] == 20
        assert row["change_5d"] is row["change_20d"] is row["rvol"] is None
        assert row["signal"] == "UNBEKANNT"
        assert row["decay_warning"] is True
    assert saved[0]["breakdown_stocks"] == []


def test_bear_total_outage_preserves_cache_but_is_not_success(monkeypatch):
    bear_dependencies(monkeypatch)
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: Response(status=503))
    saved = []
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **kw: saved.append(a))
    with pytest.raises(api.ScannerDataError):
        api._bear_scan_wrapper()
    assert saved == []


def test_proxy_completed_halfday_uses_real_session_close(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 27, 19, tzinfo=timezone.utc))
    dates = ["2026-11-27", "2026-11-25", "2026-11-24", "2026-11-23", "2026-11-20", "2026-11-19"]
    bars = [{"t": int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
             "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000000} for day in dates]
    monkeypatch.setattr(api, "POLYGON_KEY", "offline")
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: Response({"results": bars}))
    perf = api._fetch_daily_proxy_perf("TEST")
    assert perf["bar_state"] == "completed_daily"
    assert perf["rvol_basis"] == "completed_session"
    assert perf["rvol"] == 1.0


def technical_bars(count=25):
    day = datetime(2026, 1, 2, tzinfo=ZoneInfo("America/New_York"))
    result = []
    while len(result) < count:
        if api.stock_swing.session_close(day.date().isoformat()) is not None:
            value = 100 + len(result) * .2
            result.append(dict(t=day.timestamp() * 1000, o=value, h=value + 1, l=value - 1,
                               c=value, v=1000000))
        day += timedelta(days=1)
    return result


@pytest.mark.parametrize("failure", [503, 403, "exception", "error_body", "malformed", "invalid_bar"])
def test_biotech_required_technical_provider_failure_is_not_zero_score(monkeypatch, failure):
    def fetch(*a, **kw):
        if failure == "exception":
            raise TimeoutError("fixture")
        if isinstance(failure, int):
            return Response(status=failure)
        return Response({"error_body": {"status": "ERROR", "results": technical_bars()},
                         "malformed": {"results": None},
                         "invalid_bar": {"results": [{**technical_bars(1)[0], "c": float("nan")}]}}[failure])
    monkeypatch.setattr(scanners, "rate_limited_get", fetch)
    with pytest.raises(scanners.ScannerDataError):
        scanners._biotech_technical_score("offline", "BIO")


@pytest.mark.parametrize("count", [0, 20, 21, 25])
def test_biotech_valid_technical_history_keeps_existing_score_contract(monkeypatch, count):
    bars = technical_bars(count)
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({"results": bars}))
    result = scanners._biotech_technical_score("offline", "BIO")
    if count < 21:
        assert result["technical_score"] == 0 and result["details"] == {}
    else:
        assert result == scanners._compute_biotech_technical_from_bars(bars)


def test_biotech_technical_error_propagates_through_actual_full_scan_owner(monkeypatch):
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kw: [{"ticker": "BIO", "name": "Biotechnology"}])
    monkeypatch.setattr(scanners, "_biotech_cache_load", lambda **kw: [{"Ticker": "OLD", "Score": 90}])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kw: set())
    monkeypatch.setattr(scanners, "get_ticker_details", lambda *a: {"sic_code": "2836", "market_cap_millions": 1000, "shares_millions": 20})
    monkeypatch.setattr(scanners, "_get_bpiq_catalysts", lambda *a: {"bpiq_available": False})
    def news(key, ticker, **kw):
        return {"catalyst_score": 20 if ticker == "BIO" else 0, "catalysts": [], "news": [], "negative_flags": []}
    monkeypatch.setattr(scanners, "_scan_biotech_news", news)
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response(status=503))
    saved, progress = [], []
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda *a, **kw: saved.append(a))
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda *a, **kw: progress.append(a))
    with pytest.raises(scanners.ScannerDataError):
        scanners._biotech_background_scan("offline")
    assert not saved and progress[-1] == ("error",)


@pytest.mark.parametrize("day,close", [("2026-11-27", "2026-11-27T18:00:00+00:00"),
    ("2026-07-02", "2026-07-02T20:00:00+00:00"),
    ("2026-03-06", "2026-03-06T21:00:00+00:00"), ("2026-03-09", "2026-03-09T20:00:00+00:00")])
@pytest.mark.parametrize("explicit", [True, False])
def test_bi_biotech_completed_session_clock_matches_shared_calendar(monkeypatch, day, close, explicit):
    closed = datetime.fromisoformat(close)
    rows = [{"date": "2026-01-02"}, {"date": day}, {"date": "2099-01-02"},
            {"date": "2026-07-03"}, {"date": "2026-09-26"}]
    def run(now):
        if explicit:
            return scanners._bi_strip_partial_bar(rows, as_of=now)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        monkeypatch.setattr(scanners, "datetime", Clock)
        return scanners._bi_strip_partial_bar(rows)
    assert run(closed - timedelta(microseconds=1)) == rows[:1]
    assert run(closed) == rows[:2]
