"""Narrative source-window regressions; no real requests, mail or cache writes."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import api


NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)  # 10:00 New York


def _bars(count=25, *, latest_session="2026-09-03", latest_volume=1_000_000):
    eastern = ZoneInfo("America/New_York")
    session = datetime.fromisoformat(latest_session).replace(tzinfo=eastern)
    dates = []
    while len(dates) < count:
        if session.weekday() < 5:
            dates.append(session)
        session -= timedelta(days=1)
    rows = [{"t": int(day.timestamp() * 1000), "o": 100, "h": 101, "l": 99,
             "c": 100, "v": 1_000_000} for day in dates]
    rows[0]["v"] = latest_volume
    return rows


def _request(monkeypatch, rows):
    class Response:
        status_code = 200
        def json(self):
            return {"results": rows}
    monkeypatch.setattr(api, "POLYGON_KEY", "offline")
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **k: Response())


def test_narrative_completed_prior_day_volume_is_not_projected_by_today_clock(monkeypatch):
    _request(monkeypatch, _bars())
    result = api._fetch_daily_proxy_perf("TEST", now_utc=NOW)
    assert result["rvol"] == pytest.approx(1.0)
    assert result["bar_state"] == "completed_daily"
    assert result["rvol_basis"] == "completed_session"
    assert api._narrative_score(result) == pytest.approx(0.0)


def test_narrative_current_forming_volume_is_explicit_and_projected_only_for_its_session(monkeypatch):
    _request(monkeypatch, _bars(latest_session="2026-09-04", latest_volume=220_000))
    result = api._fetch_daily_proxy_perf("TEST", now_utc=NOW)
    assert result["rvol"] == pytest.approx(1.0)
    assert result["bar_state"] == "in_progress_daily_aggregate"
    assert result["rvol_basis"] == "current_session_intraday_pace"


def test_narrative_short_history_does_not_fabricate_five_or_twenty_day_returns(monkeypatch):
    rows = _bars(count=2)
    rows[0].update(o=100, h=111, c=110)
    _request(monkeypatch, rows)
    result = api._fetch_daily_proxy_perf("TEST", now_utc=NOW)
    assert result["change_1d"] == pytest.approx(10.0)
    assert result["change_5d"] is None
    assert result["change_20d"] is None
    assert result["cmf"] is None
    assert result["rvol"] is None
    assert result["obv_change"] is None


def test_narrative_future_or_conflicting_session_does_not_define_latest_price(monkeypatch):
    rows = _bars()
    future = dict(rows[0], t=int(datetime(2026, 9, 5, 4, tzinfo=timezone.utc).timestamp() * 1000), c=100.5)
    _request(monkeypatch, [future, rows[0], dict(rows[0], c=100.5), *rows[1:]])
    result = api._fetch_daily_proxy_perf("TEST", now_utc=NOW)
    assert result["price"] == pytest.approx(100)
    assert result["source_session"] == "2026-09-02"


def test_narrative_consumers_keep_missing_horizons_unknown(monkeypatch):
    row = {"ticker": "TEST", "price": 100, "change_1d": 1, "change_5d": None,
           "change_20d": None, "volume": 1_000_000, "rvol": None, "obv_change": None,
           "cmf": None, "bar_state": "completed_daily", "rvol_basis": "unavailable",
           "source_session": "2026-09-03"}
    saved = {}
    monkeypatch.setattr(api, "SECTOR_ETFS", {"TEST": "Test"})
    monkeypatch.setattr(api, "NARRATIVE_PROXIES", {})
    monkeypatch.setattr(api, "_fetch_daily_proxy_perf", lambda *a, **k: row)
    monkeypatch.setattr(api, "save_cache_file", lambda path, value: saved.update({path: value}))
    monkeypatch.setattr(api, "_send_narrative_pulse_email", lambda *_: False)
    api._money_flow_wrapper()
    assert len(saved[api.MONEY_FLOW_CACHE]) == 1
    displayed = saved[api.MONEY_FLOW_CACHE][0]
    assert displayed["change_5d"] is None
    assert displayed["change_20d"] is None
    assert displayed["cmf_signal"] == "UNBEKANNT"
    assert displayed["obv_signal"] == "UNBEKANNT"
    assert displayed["flow_signal"] == "UNBEKANNT"
    assert "—" in api._format_narrative_row(displayed)
