"""Starter swing regression contract. All provider/SMTP evidence is synthetic."""
from copy import deepcopy
from datetime import datetime, timezone
from datetime import timedelta
import sqlite3

import pytest
import api
from modules import stock_swing_contract as swing
from test_stock_momentum_confirmed_contract import _wrapper_fixture, NOW, NAME
from test_stock_strategy_final_revalidation import _row


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize("now,session", [
    ("2026-09-15T20:14:59Z", "2026-09-14"),
    ("2026-09-15T20:15:00Z", "2026-09-15"),
    ("2026-09-07T15:00:00Z", "2026-09-04"),  # Labor Day
    ("2026-11-27T18:14:59Z", "2026-11-25"),  # Thanksgiving, early close
    ("2026-11-27T18:15:00Z", "2026-11-27"),
    ("2026-03-09T20:15:00Z", "2026-03-09"),  # DST
])
def test_exchange_calendar_and_provider_delay(now, session):
    assert swing.completed_sessions(utc(now), 1) == [session]


def test_default_is_authorized_starter_not_silent_live_downgrade(monkeypatch):
    monkeypatch.delenv("STOCK_SWING_DATA_MODE", raising=False)
    assert swing.enabled()
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "realtime")
    assert not swing.enabled()


def plan(direction="LONG", **changes):
    row = _row(direction)
    setup = row["trade_setup"]
    row.update(entry=setup["entry"], stop_loss=setup["stop"], tp1=setup["tp1"], tp2=setup["tp2"])
    row.update(swing.metadata("2026-09-14", 100))
    row.update(changes)
    return row


@pytest.mark.parametrize("field,value", [
    ("stock_swing_contract_version", True), ("stock_swing_contract_version", 0),
    ("scan_price_source", "polygon_snapshot"), ("scan_price_observed_at", "2026-09-15T18:00:00Z"),
    ("fill_evidence_verified", True), ("swing_reference_close", float("nan")),
    ("swing_timeframe", "5m"), ("swing_data_delay_seconds", 0),
])
def test_invented_or_mixed_source_contract_rejected(field, value):
    assert not swing.validate(plan(**{field: value}), utc("2026-09-15T18:00:00Z"))


def payload(session, close=100):
    timestamp = int(datetime.fromisoformat(session).replace(tzinfo=swing.NY).timestamp()*1000)
    return {"status": "OK", "adjusted": True, "results": [
        {"T": "TEST", "t": timestamp, "o": close-1, "h": close+1, "l": close-2, "c": close, "v": 3_000_000}
    ]}


def test_bulk_feed_has_real_dated_close_no_last_trade(monkeypatch):
    monkeypatch.delenv("STOCK_SWING_DATA_MODE", raising=False)
    monkeypatch.setattr(swing, "completed_sessions", lambda *a: ["2026-09-14", "2026-09-11"])
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return type("Response", (), {"status_code": 200, "json": lambda self: payload(url[-10:])})()
    monkeypatch.setattr(api, "rate_limited_get", request)
    rows = api._fetch_strategy_snapshot_universe(NAME)
    assert len(calls) == 2 and len(rows) == 1
    assert all("/aggs/grouped/" in call[0] for call in calls)
    assert rows[0]["price_source"] == swing.SOURCE
    assert rows[0]["scan_price_observed_at"] == "2026-09-14T20:00:00Z"
    assert "lastTrade" not in rows[0] and "lastQuote" not in rows[0]


@pytest.mark.parametrize("changes", [{"o": 0}, {"c": True}, {"h": 99}, {"v": -1}, {"t": 0}])
def test_bad_bar_cannot_become_swing_signal(changes):
    data = payload("2026-09-14")
    data["results"][0].update(changes)
    with pytest.raises(ValueError):
        swing.parse_grouped(data, "2026-09-14")


def test_wrong_session_duplicate_and_unadjusted_rejected():
    with pytest.raises(ValueError):
        swing.parse_grouped(payload("2026-09-11"), "2026-09-14")
    data = payload("2026-09-14")
    data["results"] *= 2
    with pytest.raises(ValueError):
        swing.parse_grouped(data, "2026-09-14")
    data = payload("2026-09-14")
    data["adjusted"] = False
    with pytest.raises(ValueError):
        swing.parse_grouped(data, "2026-09-14")


def test_scanner_publishes_daily_plan_without_any_live_quote_or_5m(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    previous_as_of = []
    monkeypatch.setattr(api, "_stock_previous_session_change", lambda *a, **k: previous_as_of.append(k["as_of"]) or 11.)
    session = swing.completed_sessions(NOW, 1)[0]
    data = payload(session, 102)
    bar = data["results"][0]
    bar.update(o=98, h=102, l=96)
    feed = swing.universe(swing.parse_grouped(data, session), {"TEST": {"c": 98, "v": 1_000_000}}, session)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: feed)
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **k: pytest.fail("no 5m in swing"))
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert len(rows) == 1
    row = rows[0]
    assert row["Momentum_Execution_Confirmed"] is False
    assert row["Breakout_Confirmation_Timeframe"] == "1D"
    assert row["Breakout_Freshness_Status"] == "DAILY_CONFIRMED"
    assert swing.validate(row, NOW)
    assert api._stock_momentum_row_contract_valid(row, as_of=NOW)
    assert previous_as_of == [swing.session_close(session)]
    assert written[0][1]["metadata"]["diagnostics"]["data_mode"] == swing.MODE
    corrupt = dict(row, Breakout_Confirmation_Close=99)
    assert not api._stock_momentum_row_contract_valid(corrupt, as_of=NOW)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_final_mail_plan_keeps_price_and_not_a_fill_without_live_entitlement(monkeypatch, direction):
    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", lambda *a, **k: pytest.fail("no live quote"))
    monkeypatch.setattr(api, "_stock_swing_delayed_observation", lambda *a: {"price": 100., "observed_at": "2026-09-15T17:45:00+00:00"})
    row = plan(direction)
    before = deepcopy(row)
    result = api._revalidate_stock_strategy_mail_candidate(row, now_ts=utc("2026-09-15T18:00:00Z").timestamp())
    assert result["ok"]
    candidate = result["candidate"]
    assert candidate["price_mode"] == "swing_delayed_close"
    assert candidate["fill_evidence_verified"] is False
    assert candidate["price_observed_at"] == "2026-09-15T17:45:00+00:00"
    assert candidate["trade_setup"] == row["trade_setup"] and row == before
    assert "kein Live" in api._stock_swing_notice_html(candidate).replace("Kein Live", "kein Live")


@pytest.mark.parametrize("scanner", ["orb", "penny_stocks", "crypto_strategy"])
def test_swing_cannot_bypass_intraday_or_crypto(scanner):
    result = api._revalidate_stock_strategy_mail_candidate(plan(), scanner_name=scanner,
        now_ts=utc("2026-09-15T18:00:00Z").timestamp())
    assert result["ok"] is False


def test_stale_plan_and_invalid_geometry_stay_blocked():
    assert not api._revalidate_stock_strategy_mail_candidate(plan(),
        now_ts=utc("2026-09-15T20:15:00Z").timestamp())["ok"]
    row = plan(stop_loss=110, trade_setup={"direction": "LONG", "entry": 100, "stop": 110, "tp1": 90, "tp2": 120})
    assert not api._revalidate_stock_strategy_mail_candidate(row,
        now_ts=utc("2026-09-15T18:00:00Z").timestamp())["ok"]


def test_tracker_stores_reference_but_never_backdates_fill(monkeypatch, tmp_path):
    from modules import signal_tracker as tracker
    path = str(tmp_path / "signals.sqlite")
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", path)
    monkeypatch.setattr(tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", str(tmp_path / "journal.sqlite"))
    assert tracker.record_alert_signals("stock_strategy", [plan()], mail_channel="stocks_swing") == 1
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT price_mode, price_observed_at, entry_filled_at, created_at, fill_evidence_verified FROM signals").fetchone()
    assert row[0] == "swing_reference_close"
    assert row[1].startswith("2026-09-14T20:00:00")
    assert row[2] is None and not row[4]


@pytest.mark.parametrize("green,expected", [(16, 0), (17, 1)])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_real_bi_path_keeps_17_contract_and_labels_daily_plan(monkeypatch, green, expected, direction):
    import modules.scanners as scanners
    from test_bi_deep_fixes_scan import _patch_scan_io, _flat_bars
    from test_bi_diagnostics_integration import _result
    bars = _flat_bars()
    dates = []
    day = datetime(2026, 9, 4).date()
    while len(dates) < len(bars):
        if swing.session_close(day.isoformat()) is not None:
            dates.append(day)
        day -= timedelta(days=1)
    for bar, day in zip(bars, reversed(dates)):
        bar["t"] = int(datetime.combine(day, datetime.min.time(), tzinfo=swing.NY).timestamp()*1000)
    written = _patch_scan_io(monkeypatch, {"TEST": bars})
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(scanners, "datetime", Clock)
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *a, **k: _result(green))
    monkeypatch.delenv("STOCK_SWING_DATA_MODE", raising=False)
    scanners._bi_background_scan("fixture", direction=direction, candidates=["TEST"])
    assert len(written["results"]) == expected
    if expected:
        row = written["results"][0]
        assert swing.validate(row, NOW)
        assert row["BI_IndicatorsGreen"] == 17
        assert row["Preis"] == round(bars[-1]["close"], 2)
        assert row["fill_evidence_verified"] is False


def minutes():
    opening = utc("2026-09-15T13:30:00Z")
    return {"status": "DELAYED", "results": [
        {"t": int((opening+timedelta(minutes=i)).timestamp()*1000),
         "o": 100, "h": 101, "l": 99, "c": 100} for i in range(3)]}


def check_path(data):
    return swing.validate_minute_path(data, reference_at=utc("2026-09-14T20:00:00Z"),
        available_at=utc("2026-09-15T13:33:00Z"), stop=95, tp1=110, direction="LONG")


def test_delayed_path_does_not_need_realtime_but_must_cover_observable_minutes():
    assert check_path(minutes()) == 100
    assert swing.delayed_market_watermark(utc("2026-09-15T13:48:20Z")) == utc("2026-09-15T13:33:00Z")
    assert swing.delayed_market_watermark(utc("2026-09-15T13:40:00Z")) == utc("2026-09-14T20:00:00Z")


@pytest.mark.parametrize("mutation", ["stop", "target", "gap", "duplicate", "invalid"])
def test_known_breach_missing_minute_or_bad_bar_blocks_swing_mail(mutation):
    data = minutes()
    if mutation == "stop": data["results"][0]["l"] = 94
    if mutation == "target": data["results"][0]["h"] = 111
    if mutation == "gap": data["results"].pop(1)
    if mutation == "duplicate": data["results"].append(dict(data["results"][1]))
    if mutation == "invalid": data["results"][0]["o"] = True
    with pytest.raises(ValueError):
        check_path(data)


def test_future_bar_cannot_supply_a_delayed_price():
    data = minutes()
    future = dict(data["results"][-1], t=int(utc("2026-09-15T13:40:00Z").timestamp()*1000), c=150)
    data["results"].append(future)
    assert check_path(data) == 100


def test_daily_level_adapter_uses_actual_early_close():
    bars = api._daily_level_bars([{"date": "2026-11-27", "open": 99, "high": 101,
                                   "low": 98, "close": 100, "volume": 10000}])
    assert bars[0]["close_time"] == utc("2026-11-27T18:00:00Z")


def test_plan_mail_reaches_sender_off_session_and_keeps_pending_fill(monkeypatch, tmp_path):
    from test_stock_strategy_final_revalidation import _patch_strategy_mail_pipeline
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    now = utc("2026-09-15T00:00:00Z")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": False, "session": "CLOSED"})
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_ensure_stock_business_quality", lambda r: r)
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **k: pytest.fail("no live quote/provider needed after completed close"))
    api._send_strategy_scan_alerts(NAME, [plan()], "stocks")
    assert len(sent) == 1
    assert "kein Live" in sent[0]["body"].replace("Kein Live", "kein Live")
    assert "Finaler ausfuehrbarer Bid/Ask" not in sent[0]["body"]
    row = sent[0]["tracking_rows"][0]
    assert row["price_mode"] == "swing_delayed_close" and row["fill_evidence_verified"] is False
    assert tracked == []  # No pre-SMTP recording by the scanner helper.
