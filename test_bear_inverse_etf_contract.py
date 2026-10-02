"""Actual Bear section-1 producer: fixed completed clock and strict source.

No mail, network, server state or real credentials; the offline launcher owns
disposable application state. Short genuine history stays explicitly unknown.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import api
import pytest
from modules.stock_swing_contract import NY, session_close
from scripts.scanner_history_bi import expected_sessions

UTC = timezone.utc
NOW = datetime(2026, 9, 29, 20, 15, tzinfo=UTC)


def daily(count=21):
    days = expected_sessions(datetime(2026, 7, 1).date(), datetime(2026, 9, 30).date())[-count:]
    return [{"t": int(datetime.fromisoformat(day).replace(tzinfo=NY).timestamp()*1000),
             "o": 100.0+index, "h": 101.0+index, "l": 99.0+index,
             "c": 100.0+index, "v": 1_000_000.0}
            for index, day in enumerate(days)]


def payload(rows):
    return {"status": "OK", "adjusted": True, "results": rows,
            "resultsCount": len(rows), "queryCount": len(rows)}


def run(monkeypatch, responses, *, now=NOW, starter=True):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    class Response:
        def __init__(self, data, code=200):
            self.data, self.status_code = data, code
        def json(self):
            return deepcopy(self.data)
    calls, saved = [], []
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: starter)
    monkeypatch.setattr(api, "INVERSE_ETFS", {symbol: ("3x Short index", "SPY") for symbol in responses})
    monkeypatch.setattr(api, "_scan_control_point", lambda **kw: None)
    monkeypatch.setattr(api, "get_current_trading_session", lambda: ("Regular", "fixture"))
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": False, "reason": "offline"})
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **kw: None)
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(deepcopy(rows)))
    def request(url, **kw):
        calls.append((url, kw))
        if "/aggs/" not in url:
            return Response({}, 503)
        symbol = url.split("/ticker/")[1].split("/")[0]
        return Response(responses[symbol])
    monkeypatch.setattr(api, "rate_limited_get", request)
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **kw: pytest.fail("ETF context is not a mail authorization"))
    api._bear_scan_wrapper()
    return saved[0]["inverse_etfs"], calls


def test_actual_inverse_etf_sorts_both_orders_before_computing_returns(monkeypatch):
    history = daily()
    rows, calls = run(monkeypatch, {"ASC": payload(history), "DESC": payload(list(reversed(history)))})
    left, right = [{key: value for key, value in row.items() if key != "ticker"} for row in rows]
    assert left == right
    assert rows[0]["price"] == 120.0
    assert rows[0]["change_1d"] == pytest.approx(round((120/119-1)*100, 2))
    assert rows[0]["change_5d"] == pytest.approx(round((120/115-1)*100, 2))
    assert rows[0]["change_20d"] == pytest.approx(20.0)
    assert rows[0]["rvol"] == 1.0 and rows[0]["history_bars"] == 21
    assert all(kw["params"].get("adjusted") is True for url, kw in calls if "/aggs/" in url)


@pytest.mark.parametrize("count", [2, 5, 6, 10, 11, 20, 21])
def test_genuine_short_history_keeps_unmeasurable_horizons_unknown(monkeypatch, count):
    rows, _ = run(monkeypatch, {"VALID": payload(list(reversed(daily(count))))})
    assert len(rows) == 1
    row = rows[0]
    assert row["change_1d"] is not None
    assert (row["change_5d"] is None) == (count < 6)
    assert (row["change_20d"] is None) == (count < 21)
    assert (row["rvol"] is None) == (count < 11)
    if count < 6:
        assert row["signal"] == "UNBEKANNT"


@pytest.mark.parametrize("field,value", [
    ("o", None), ("h", True), ("l", 0.0), ("c", float("nan")),
    ("v", None), ("v", True), ("v", float("inf")), ("v", -1),
    ("h", 10.0), ("l", 1000.0), ("t", True), ("t", float("nan")),
    ("t", 1_700_000_000), ("t", None),
])
def test_one_bad_closed_etf_is_excluded_without_erasing_valid_peer(monkeypatch, field, value):
    bad = daily()
    bad[3][field] = value
    rows, _ = run(monkeypatch, {"BAD": payload(bad), "GOOD": payload(daily())})
    assert [row["ticker"] for row in rows] == ["GOOD"]


@pytest.mark.parametrize("change", [
    {"adjusted": False}, {"status": "ERROR"}, {"next_url": "https://invalid.example/next"},
    {"resultsCount": 1}, {"queryCount": True}, {"results": {}},
])
def test_bad_source_envelope_is_not_a_partial_valid_inverse_etf(monkeypatch, change):
    bad = payload(daily())
    bad.update(change)
    rows, _ = run(monkeypatch, {"BAD": bad, "GOOD": payload(daily())})
    assert [row["ticker"] for row in rows] == ["GOOD"]


def test_forming_and_future_daily_ohlcv_are_not_consumed(monkeypatch):
    prior = daily(22)[:-1]
    forming = daily(22)[-1]
    future = {**forming, "t": int(datetime(2026, 9, 30, tzinfo=NY).timestamp()*1000)}
    forming.update(o=None, h=True, l=0, c=float("inf"), v=None)
    future.update(o=None, h=True, l=0, c=float("inf"), v=None)
    rows, _ = run(monkeypatch, {"BASE": payload(prior), "OPEN": payload([future, forming]+list(reversed(prior)))},
                  now=datetime(2026, 9, 29, 19, 50, tzinfo=UTC))
    left, right = [{key: value for key, value in row.items() if key != "ticker"} for row in rows]
    assert left == right and rows[0]["history_bars"] == 21


def test_starter_waits_until_completed_daily_close_is_available(monkeypatch):
    history = daily(3)
    early, _ = run(monkeypatch, {"VALID": payload(history)}, now=NOW-timedelta(seconds=1))
    ready, _ = run(monkeypatch, {"VALID": payload(history)}, now=NOW)
    assert early[0]["history_bars"] == 2 and ready[0]["history_bars"] == 3
    assert early[0]["price"] == 101 and ready[0]["price"] == 102


def test_actual_halfday_calendar_is_used_instead_of_fixed_1600(monkeypatch):
    days = ["2026-11-24", "2026-11-25", "2026-11-27"]
    history = [{"t": int(datetime.fromisoformat(day).replace(tzinfo=NY).timestamp()*1000),
                "o": 100, "h": 101, "l": 99, "c": 100, "v": 1_000_000} for day in days]
    now = session_close(days[-1])+timedelta(minutes=15)
    rows, _ = run(monkeypatch, {"VALID": payload(history)}, now=now)
    assert rows[0]["history_bars"] == 3


def test_duplicate_exact_bar_is_one_observation_conflict_invalidates_series(monkeypatch):
    valid = daily()
    exact = valid+[deepcopy(valid[-1])]
    bad = valid+[{**valid[-1], "c": valid[-1]["c"]+.5}]
    rows, _ = run(monkeypatch, {"EXACT": payload(exact), "CONFLICT": payload(bad)})
    assert [row["ticker"] for row in rows] == ["EXACT"]
    assert rows[0]["history_bars"] == 21
