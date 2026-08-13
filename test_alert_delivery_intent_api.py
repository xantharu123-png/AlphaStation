"""End-to-end API/SMTP/tracker delivery-intent regressions.

These tests use an isolated SQLite database and the real two-phase tracker
contract.  They prove that SMTP is entered only by the compare-and-set owner,
accepted mail is activated exactly once, and an unknown DATA outcome remains
non-replayable.
"""

from __future__ import annotations

import sqlite3
from email import message_from_string

import api
from modules import signal_tracker as tracker


def _row(ticker: str = "INTENT") -> dict:
    return {
        "Ticker": ticker,
        "Signal_Direction": "LONG",
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 105.0,
        "TP2": 110.0,
        "trade_horizon": "swing",
        "strategy": "intent-regression",
    }


def _setup(monkeypatch, tmp_path, smtp_cls) -> str:
    db_path = str(tmp_path / "signal_tracker.sqlite")
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", db_path)
    monkeypatch.setattr(api.smtplib, "SMTP", smtp_cls)
    monkeypatch.setattr(
        api,
        "_SECRETS",
        {
            "GMAIL_USER": "operator@example.com",
            "GMAIL_APP_PASSWORD": "test-only-password",
            "ALERT_EMAIL": "operator@example.com",
        },
    )
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "is_telegram_configured", lambda: False)
    return db_path


class _AcceptedSMTP:
    calls = 0
    messages = []

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, sender, recipients, message):
        type(self).calls += 1
        type(self).messages.append(message)
        return {}

    def quit(self):
        pass


def test_accepted_tracking_mail_is_activated_once_and_never_replayed(
    monkeypatch, tmp_path
):
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    kwargs = {
        "bypass_startup_cooldown": True,
        "mail_class": "swing_trade",
        "trade_horizon": "swing",
        "mail_channel": "stocks_premarket",
        "tracking_scanner": "stock_strategy",
        "tracking_rows": [_row()],
        "delivery_dedupe_keys": ["intent-regression-key"],
    }

    assert api._send_email_alert("Intent E2E", "<p>x</p>", **kwargs) is True
    assert api._send_email_alert("Intent E2E", "<p>x</p>", **kwargs) is False
    assert _AcceptedSMTP.calls == 1
    wire = message_from_string(_AcceptedSMTP.messages[0])
    decoded = "\n".join(
        (part.get_payload(decode=True) or b"").decode(
            part.get_content_charset() or "utf-8", errors="replace"
        )
        for part in wire.walk()
        if part.get_content_type() in {"text/plain", "text/html"}
    )
    assert "Signal-ID:" in decoded

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, delivery_state, delivery_prepared_at, "
            "delivery_attempted_at, delivery_accepted_at, "
            "delivery_recipient_keys_json, mail_channel "
            "FROM signals"
        ).fetchone()
    assert row is not None
    assert row[0:2] == (tracker.STATUS_OPEN, "ACTIVE")
    assert all(row[index] for index in (2, 3, 4, 5))
    assert row[6] == "stocks_premarket"


class _UnknownSMTP(_AcceptedSMTP):
    calls = 0

    def sendmail(self, sender, recipients, message):
        type(self).calls += 1
        raise ConnectionResetError("unknown after DATA")


def test_unknown_data_tracking_intent_is_not_replayed(monkeypatch, tmp_path):
    _UnknownSMTP.calls = 0
    db_path = _setup(monkeypatch, tmp_path, _UnknownSMTP)
    quarantined = []

    class _Outbox:
        @staticmethod
        def record_tracker_acceptance_pending(*args, **kwargs):
            return "unused-contract-ready"

        @staticmethod
        def quarantine(*args, **kwargs):
            quarantined.append((args, kwargs))
            return 1

    monkeypatch.setattr(api, "_mail_outbox", _Outbox())
    kwargs = {
        "bypass_startup_cooldown": True,
        "mail_class": "trade",
        "mail_channel": "stocks_swing",
        "tracking_scanner": "stock_strategy",
        "tracking_rows": [_row("UNKNOWN")],
        "delivery_dedupe_keys": ["unknown-intent-key"],
    }

    assert api._send_email_alert("Unknown Intent", "<p>x</p>", **kwargs) is False
    assert api._send_email_alert("Unknown Intent", "<p>x</p>", **kwargs) is False
    assert _UnknownSMTP.calls == 1
    assert len(quarantined) == 1

    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status, delivery_state, delivery_accepted_at FROM signals"
        ).fetchone()
    assert state == (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED", None)


class _PartialAcceptedSMTP(_AcceptedSMTP):
    calls = []

    def sendmail(self, sender, recipients, message):
        type(self).calls.append(tuple(recipients))
        return {"later@example.com": (450, b"temporary refusal")}


def test_tracking_mail_does_not_merge_later_retry_into_accepted_cohort(
    monkeypatch, tmp_path
):
    _PartialAcceptedSMTP.calls = []
    db_path = _setup(monkeypatch, tmp_path, _PartialAcceptedSMTP)

    assert api._send_email_alert(
        "Partial Intent",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        recipient_emails=["first@example.com", "later@example.com"],
        mail_class="trade",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[_row("PARTIAL")],
        delivery_dedupe_keys=["partial-intent-key"],
    ) is True

    assert _PartialAcceptedSMTP.calls == [
        ("first@example.com", "later@example.com")
    ]
    with sqlite3.connect(db_path) as conn:
        recipient_keys = conn.execute(
            "SELECT delivery_recipient_keys_json FROM signals"
        ).fetchone()[0]
    assert api._recipient_delivery_key("first@example.com") in recipient_keys
    assert api._recipient_delivery_key("later@example.com") not in recipient_keys
