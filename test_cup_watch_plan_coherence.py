"""Cup watch persistence must not turn a rejected final plan into a trade.

Use scripts/run_offline_tests.py: only the clock, session and external 4H
provider are substituted. Cup detection, causal zones, final planning, JSON
projection, promotion and the mail-plan guard remain real.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email import message_from_string
from email.header import decode_header, make_header
import json
import sqlite3

import pytest

import api
from test_cup_final_plan_coherence import (
    NOW, SESSION, _FixedDatetime, _causal_cup_inputs,
)


def _real_watch(monkeypatch, *, with_overhead=True, far_targets=False,
                missing_snapshot=False):
    candidate, snapshot = _causal_cup_inputs(
        monkeypatch, with_overhead=with_overhead, far_targets=far_targets,
    )
    monkeypatch.setattr(api, "datetime", _FixedDatetime)
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {
        "allowed": False, "session": "CLOSED", "reason": "unit-test closed",
    })
    monkeypatch.setattr(api, "_current_us_market_date_str", lambda: SESSION)
    # This suite owns no queue file. The row projection/promotion below are
    # real; the external persistence effect of producing the watch is not.
    monkeypatch.setattr(api, "_upsert_cup_handle_watch", lambda *a, **k: True)
    row = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000},
        structure_snapshot=None if missing_snapshot else snapshot,
    )
    assert row is not None
    assert row["entry_status"] == "DAILY_CLOSE_CONFIRMED_WATCH_ONLY"
    assert row["trade_signal"] == "BEOBACHTEN"
    assert row["alertable_long"] is False
    assert api._cup_signal_contract_valid(row, strategy_name="Cup and Handle Breakout")
    return row


def _json_watch(row):
    return json.loads(json.dumps(api._cup_handle_watch_row(row), allow_nan=False))


def _trigger():
    return {
        "confirmed": True,
        "trigger_type": "fresh_5m_cross",
        "reason": "cup_next_session_5m_trigger_confirmed",
        "trigger_observed_ts": NOW.timestamp(),
    }


@pytest.mark.parametrize("case", ["projection", "missing_snapshot", "near_barrier"])
def test_rejected_final_cup_plan_stays_rejected_after_watch_json_and_trigger(monkeypatch, case):
    """Dropping final-plan metadata at the queue boundary used to release these."""
    row = _real_watch(
        monkeypatch, with_overhead=case != "projection",
        missing_snapshot=case == "missing_snapshot",
    )
    expected_reasons = (
        {"trade_first_barrier_below_minimum_reward", "trade_breakout_not_confirmed"}
        if case == "near_barrier" else {"trade_target_not_structural"}
    )
    assert api._alert_trade_plan_rejection_reason(row) in expected_reasons
    if case == "near_barrier":
        assert (row["nearest_barrier"]["zone_low"], row["nearest_barrier"]["zone_high"]) == (
            110.75299153057763, 111.24700846942237,
        )
        assert row["TP1"] == 110.75
        assert row["structure_status"] == "WAIT_BREAK_RECLAIM"
        assert row["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"
    else:
        assert row["target_quality"].startswith("PROJECTION_ONLY")
        assert row["tp1_is_projection"] is True

    stored = _json_watch(row)
    assert api._alert_trade_plan_rejection_reason(stored) in expected_reasons
    assert stored["structure_status"] == row["structure_status"]
    assert stored["target_quality"] == row["target_quality"]
    assert stored["trade_setup"]["tp1_is_projection"] == row["trade_setup"]["tp1_is_projection"]
    promoted = api._promote_cup_handle_watch_row(stored, _trigger())
    # Refusing promotion altogether is safe. A trigger can otherwise change
    # execution state, but cannot supply missing structural targets or erase a
    # real first opposing zone from the unchanged Cup plan.
    if promoted is not None:
        assert api._alert_trade_plan_rejection_reason(promoted) in expected_reasons
        assert promoted["Entry"] == 101.2
        assert promoted["StopLoss"] == 92.38
        assert promoted["trade_setup"]["structure_status"] == row["structure_status"]


def test_legitimate_structural_watch_keeps_its_final_plan_and_can_promote(monkeypatch):
    """Fail-closed persistence must not silence a fully evidenced valid watch."""
    row = _real_watch(monkeypatch, far_targets=True)
    assert (row["Entry"], row["StopLoss"], row["TP1"], row["TP2"]) == (
        101.2, 92.38, 117.75, 134.75,
    )
    assert row["structure_status"] == "ACCEPT"
    assert api._alert_trade_plan_rejection_reason(row) is None
    stored = _json_watch(row)
    assert api._alert_trade_plan_rejection_reason(stored) is None
    promoted = api._promote_cup_handle_watch_row(stored, _trigger())
    assert promoted is not None
    assert promoted["entry_status"] == "NEXT_SESSION_TRIGGER_CONFIRMED"
    assert promoted["trade_signal"] == "JETZT_TRADEN"
    assert api._alert_trade_plan_rejection_reason(promoted) is None
    setup = promoted["trade_setup"]
    decision = setup["structure_decision"]
    assert (decision["entry"], decision["stop"], decision["target1"], decision["target2"]) == (
        101.2, 92.38, 117.75, 134.75,
    )
    assert setup["tp1_is_projection"] is False
    assert setup["tp2_is_projection"] is False
    assert setup["tp1_zone_id"] != setup["tp2_zone_id"]
    assert setup["stop_source_family"] == "cup_handle"
    assert decision["stop_evidence"]["source_family"] == "cup_handle"


def test_shape_valid_legacy_watch_without_final_coherence_cannot_promote(monkeypatch):
    """Old scalar-only watches have no authority for a later trade release."""
    row = _json_watch(_real_watch(monkeypatch, far_targets=True))
    final_fields = {
        "structure_status", "structure_reason", "structure_decision",
        "target_quality", "nearest_barrier", "overhead_resistance",
        "barrier_gate", "barrier_gate_active", "entry_eligible", "level_quality",
        "tp1_is_projection", "tp2_is_projection", "stop_is_projection",
        "tp1_source", "tp2_source", "tp1_zone_id", "tp2_zone_id",
        "tp1_source_family", "tp2_source_family", "stop_source_family",
        "stop_confirmed_at", "stop_data_cutoff_at", "stop_pattern_evidence",
    }
    for payload in (row, row["trade_setup"]):
        for key in list(payload):
            if key in final_fields or key.startswith(("cup_plan_", "cup_final_plan_")):
                payload.pop(key)
    assert api._cup_signal_contract_valid(row, strategy_name="Cup and Handle Breakout")
    assert api._promote_cup_handle_watch_row(row, _trigger()) is None


def test_final_plan_projection_is_json_safe_and_drops_arbitrary_nested_payload(monkeypatch):
    """Preserving a safety verdict does not authorize arbitrary persisted data."""
    row = _real_watch(monkeypatch, far_targets=True)
    before = deepcopy(row)
    marker = "DO_NOT_PERSIST_ARBITRARY_PAYLOAD"
    row["arbitrary"] = {"note": marker}
    row["trade_setup"]["arbitrary"] = marker
    for decision in (row["structure_decision"], row["trade_setup"]["structure_decision"]):
        decision["arbitrary"] = {"note": marker}
        decision["stop_evidence"]["arbitrary"] = [marker]
    if isinstance(row.get("nearest_barrier"), dict):
        row["nearest_barrier"]["arbitrary"] = marker
    stored = _json_watch(row)
    assert marker not in json.dumps(stored, allow_nan=False)
    assert "_daily_bars" not in stored
    assert api._alert_trade_plan_rejection_reason(stored) is None
    decision = stored["trade_setup"]["structure_decision"]
    assert decision["entry"] == before["Entry"] == 101.2
    assert decision["stop_evidence"]["price"] == before["StopLoss"] == 92.38


def test_structural_cup_queue_promotes_through_real_sender_and_delivery_ledger(monkeypatch, tmp_path):
    """One real queue lease and trade wire; no plan/admission helper is mocked.

    Settings and every durable mail store are isolated. Polygon responses and
    the external 4H/fundamental provider boundaries supply fixture data;
    SMTP accepts only reserved .invalid test recipients without opening a
    connection. The sender, classification, final quote/path validation,
    dedupe ownership, intent preparation and acceptance tracker remain real.
    """
    from modules import auth, cup_handle_watch_queue, mail_outbox
    from modules import regime_filter, signal_tracker, suppression_telemetry

    real_session_status = api._stock_trade_email_status
    real_current_date = api._current_us_market_date_str
    real_upsert_watch = api._upsert_cup_handle_watch
    # Reuse the actual completed source history before changing the session
    # clock. Later 4H evidence must not contradict these Cup/Handle daily bars.
    source_candidate, _ = _causal_cup_inputs(monkeypatch, far_targets=True)
    completed_daily = deepcopy(source_candidate["_daily_bars"])
    row = _real_watch(monkeypatch, far_targets=True)
    monkeypatch.setattr(api, "_upsert_cup_handle_watch", real_upsert_watch)
    queue_path = tmp_path / "cup_watch.json"
    monkeypatch.setattr(api, "_CUP_HANDLE_WATCH_QUEUE_PATH", queue_path)
    assert api._queue_cup_handle_next_session_watch(
        row, confirmation_date=SESSION, target_session_date="2026-08-31",
        breakout_level=row["cup_rim_level"],
    )
    queued = cup_handle_watch_queue.queue_snapshot(path=queue_path)
    assert len(queued["items"]) == 1
    persisted_row = next(iter(queued["items"].values()))["row"]
    assert api._alert_trade_plan_rejection_reason(persisted_row) is None
    assert persisted_row["cup_plan_version"] == "cup_causal_structure_v1"
    assert persisted_row["trade_setup"]["cup_plan_version"] == "cup_causal_structure_v1"

    # Friday's confirmed daily watch may act only on the next regular session.
    now = datetime(2026, 8, 31, 14, 0, 10, tzinfo=timezone.utc)
    trigger_at = now.replace(minute=55, second=0) - timedelta(hours=1)
    quote_at = now - timedelta(seconds=1)
    last_trade_at = now.replace(second=0) - timedelta(seconds=1)

    class MondayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", MondayClock)
    monkeypatch.setattr(signal_tracker, "datetime", MondayClock)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())
    monkeypatch.setattr(api, "_stock_trade_email_status", real_session_status)
    monkeypatch.setattr(api, "_current_us_market_date_str", real_current_date)
    assert api._stock_trade_email_status(now)["allowed"] is True

    for module, attribute, name in (
        (signal_tracker, "SIGNAL_DB_PATH", "tracker.sqlite"),
        (signal_tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", "acceptance.sqlite"),
        (mail_outbox, "MAIL_OUTBOX_DB_PATH", "outbox.sqlite"),
        (suppression_telemetry, "SUPPRESSION_TELEMETRY_DB_PATH", "suppression.sqlite"),
        (auth, "AUTH_DB_PATH", "auth.sqlite"),
        (auth, "AUTH_DB_LEGACY_JSON_PATH", "absent_legacy_users.json"),
        (api, "_EMAIL_DEDUPE_FILE", "dedupe.json"),
        (api, "COMMON_STOCK_UNIVERSE_CACHE", "stock_reference.json"),
    ):
        monkeypatch.setattr(module, attribute, str(tmp_path / name))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(regime_filter, "DEFAULT_STATE_PATH", tmp_path / "regime.json")
    monkeypatch.setenv("REGIME_FILTER_ENABLED", "0")
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "sender@example.invalid",
        "GMAIL_APP_PASSWORD": "offline-cup-test-only",
        "ALERT_EMAIL": "recipient@example.invalid",
    })
    monkeypatch.setattr(api, "POLYGON_KEY", "offline-cup-fixture")
    monkeypatch.setattr(api, "_EMAIL_STARTUP_TIME", now.timestamp() - 600)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_STOCK_QUOTE_CAPABILITY", deepcopy(api._STOCK_QUOTE_CAPABILITY))
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {
        "loaded_at": now.timestamp(), "tickers": ["CUPX"], "source": "fixture",
        "adr_tickers": [], "names": {"CUPX": "Offline Cup Fixture Corp"},
        "names_refresh_attempted_at": now.timestamp(),
    })
    # A code-read market cache is genuine data, not a replaced health helper.
    market_path = tmp_path / "market_context.json"
    market_path.write_text(json.dumps({
        "cached_at": now.isoformat(), "results": [{
            "source": {"market_internals_observed_at": now.isoformat()},
            "summary": {"regime": "NEUTRAL", "trade_mode": "NORMAL"},
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(api, "MARKET_CONTEXT_CACHE", str(market_path))
    four_hour = []
    for daily in completed_daily[-12:]:
        session = datetime.fromisoformat(daily["date"]).date()
        assert api.stock_swing.session_close(session.isoformat()) is not None
        # The day's full observed OHLC path occurs in the first four hours;
        # the remaining completed 2.5-hour segment holds its real daily close.
        # Combined OHLCV reproduces the exact source day without extending
        # its high/low or manufacturing an unrelated historical 101.4 price.
        segments = [
            {key: daily[key] for key in ("open", "high", "low", "close")},
            {key: daily["close"] for key in ("open", "high", "low", "close")},
        ]
        for segment, hour, count, close_hour, close_minute in zip(
            segments, (9, 13), (8, 5), (13, 16), (30, 0),
        ):
            closed = datetime(session.year, session.month, session.day,
                              close_hour, close_minute, tzinfo=api.stock_swing.NY)
            four_hour.append({
                **segment,
                "timestamp": int(datetime(session.year, session.month, session.day,
                                          hour, 30, tzinfo=api.stock_swing.NY).timestamp() * 1000),
                "volume": daily["volume"] * count / 13,
                "partial_source_bar": False, "source_bar_count": count,
                "expected_source_bar_count": count, "missing_source_bar_count": 0,
                "is_closed": True, "closed_at": closed.astimezone(timezone.utc).isoformat(),
            })
        first, last = four_hour[-2:]
        assert first["open"] == daily["open"]
        assert max(first["high"], last["high"]) == daily["high"]
        assert min(first["low"], last["low"]) == daily["low"]
        assert last["close"] == daily["close"]
        assert first["volume"] + last["volume"] == pytest.approx(daily["volume"])
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", lambda *a, **k: four_hour)
    monkeypatch.setattr(api, "fetch_business_quality", lambda *a, **k: {})

    provider_calls = []

    class FixtureResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return deepcopy(self.payload)

    def provider(url, **kwargs):
        provider_calls.append(url)
        if url == "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/CUPX":
            return FixtureResponse({"ticker": {
                "lastQuote": {"p": 101.29, "P": 101.31, "t": int(quote_at.timestamp() * 1000)},
                "lastTrade": {"p": 101.3, "t": int(last_trade_at.timestamp() * 1000)},
            }})
        if url == "https://api.polygon.io/v2/aggs/ticker/CUPX/range/1/minute/2026-08-31/2026-08-31":
            return FixtureResponse({"status": "OK", "results": [{
                "t": int((trigger_at + timedelta(minutes=index)).timestamp() * 1000),
                "o": 101.25, "h": 101.4, "l": 101.2, "c": 101.3, "v": 1000,
            } for index in range(5)]})
        raise AssertionError("Unexpected external provider request in offline Cup test")

    def forbidden(*args, **kwargs):
        raise AssertionError("External network or fallback SMTP is forbidden")

    monkeypatch.setattr(api, "rate_limited_get", provider)
    monkeypatch.setattr(api.req.sessions.Session, "request", forbidden)
    smtp_messages = []

    class OfflineSMTP:
        def __init__(self, host, port, **kwargs):
            assert (host, port) == ("smtp.gmail.com", 587)

        def ehlo(self):
            return 250, b"offline"

        def starttls(self, **kwargs):
            return 220, b"offline"

        def login(self, username, password):
            assert (username, password) == ("sender@example.invalid", "offline-cup-test-only")

        def sendmail(self, sender, recipients, message):
            assert sender == "sender@example.invalid"
            assert recipients == ["recipient@example.invalid"]
            smtp_messages.append(message)
            return {}

        def quit(self):
            return 221, b"offline"

        def close(self):
            return None

    monkeypatch.setattr(api.smtplib, "SMTP", OfflineSMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", forbidden)
    claims = api._claim_cup_handle_watches(
        "2026-08-31", now_ts=now.timestamp(), path=queue_path,
    )
    assert len(claims) == 1
    claim = claims[0]
    promoted = api._promote_cup_handle_watch_row(claim["row"], {
        "confirmed": True, "trigger_type": "fresh_5m_cross",
        "reason": "cup_next_session_5m_trigger_confirmed",
        "trigger_observed_ts": trigger_at.timestamp(),
    })
    assert promoted is not None
    assert api._alert_trade_plan_rejection_reason(promoted) is None
    api._send_strategy_scan_alerts("Cup and Handle Breakout", [promoted], "stocks")

    assert len(smtp_messages) == 1, api._EMAIL_SEND_LOG
    wire = message_from_string(smtp_messages[0])
    subject = str(make_header(decode_header(wire["Subject"])))
    assert "SWING:" in subject
    assert "WATCH:" not in subject
    assert "CUPX" in subject
    assert api._last_delivery_outcome() == "accepted"
    assert sum("/snapshot/" in url for url in provider_calls) >= 2
    assert sum("/range/1/minute/" in url for url in provider_calls) == 1
    connection = sqlite3.connect((tmp_path / "tracker.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        deliveries = connection.execute(
            "SELECT ticker,mail_class,channel,mail_channel,delivery_state,"
            "fill_evidence_verified,entry,stop,tp1,tp2 FROM signals ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    assert deliveries == [(
        "CUPX", "trade", "email", "stocks_swing", "ACTIVE", 0,
        101.2, 92.38, 117.75, 134.75,
    )]
    state = api._classify_alert_candidate("stock_strategy", promoted, now.timestamp())
    assert api._load_email_dedupe()[state["cooldown_key"]] == now.timestamp()
    assert api._finish_cup_handle_watch_claim(
        claim["id"], claim["lease_owner"], remove=True,
        generation=claim["generation"], path=queue_path,
    )
    assert cup_handle_watch_queue.queue_snapshot(path=queue_path)["items"] == {}
