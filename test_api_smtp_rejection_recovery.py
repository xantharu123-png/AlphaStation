"""Explicit SMTP rejection must not become a permanent unknown-delivery lock.

The real API sender, tracker intent, outbox and dedupe are exercised with only
the external SMTP boundary replaced. A timeout/disconnect still cannot replay.
"""

import sqlite3

import pytest

import api
from modules import mail_outbox, signal_tracker as tracker
from test_alert_delivery_intent_api import _AcceptedSMTP, _row, _setup


def _isolated_sender(monkeypatch, tmp_path, error):
    class ReplySMTP(_AcceptedSMTP):
        calls = 0
        failure = error

        def sendmail(self, sender, recipients, message):
            type(self).calls += 1
            if type(self).failure is not None:
                raise type(self).failure
            return {}

    db_path = _setup(monkeypatch, tmp_path, ReplySMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", ReplySMTP)
    monkeypatch.setattr(api, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(tmp_path / "outbox.sqlite"))
    monkeypatch.setattr(tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", "")
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    kwargs = {
        "bypass_startup_cooldown": True,
        "mail_class": "trade",
        "mail_channel": "stocks_swing",
        "tracking_scanner": "stock_strategy",
        "tracking_rows": [_row("REPLY")],
        "delivery_dedupe_keys": ["stock_strategy_REPLY"],
    }
    return ReplySMTP, db_path, kwargs


@pytest.mark.parametrize("error", [
    api.smtplib.SMTPDataError(451, b"temporary rejection"),
    api.smtplib.SMTPDataError(550, b"permanent rejection"),
    api.smtplib.SMTPSenderRefused(450, b"sender temporarily rejected", "operator@example.com"),
    api.smtplib.SMTPSenderRefused(550, b"sender rejected", "operator@example.com"),
])
def test_explicit_rejection_releases_intent_for_a_later_fresh_signal(
    monkeypatch, tmp_path, error
):
    smtp, db_path, kwargs = _isolated_sender(monkeypatch, tmp_path, error)
    claimed_at = api.time.time()
    assert api._email_dedupe_claim("stock_strategy_REPLY", 3600, now=claimed_at)

    assert api._send_email_alert("SMTP reply", "<p>Signal</p>", **kwargs) is False
    assert api._last_delivery_outcome() == "failed"
    assert smtp.calls == 1  # No blind retry of a time-sensitive entry mail.
    assert not mail_outbox.has_uncertain_delivery_key("stock_strategy_REPLY")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
    assert api._email_dedupe_release_after_send("stock_strategy_REPLY", claimed_at=claimed_at)

    # A new scanner decision can prepare again; no false ATTEMPTED row remains.
    smtp.failure = None
    assert api._send_email_alert("SMTP reply", "<p>Signal</p>", **kwargs) is True
    assert smtp.calls == 2
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status, delivery_state FROM signals").fetchall() == [
            (tracker.STATUS_OPEN, "ACTIVE")
        ]


@pytest.mark.parametrize("error", [
    TimeoutError("DATA reply missing"),
    ConnectionResetError("DATA connection reset"),
    api.smtplib.SMTPServerDisconnected("DATA connection disconnected"),
    # smtplib.getreply synthesizes this locally for an oversized reply. It is
    # not evidence of rejection, including after the server accepted DATA.
    api.smtplib.SMTPResponseException(500, "Line too long."),
    api.smtplib.SMTPDataError(250, b"unexpected non-rejection reply"),
    api.smtplib.SMTPResponseException(354, b"unexpected intermediate reply"),
])
def test_unknown_or_unexpected_data_outcome_stays_quarantined_without_replay(
    monkeypatch, tmp_path, error
):
    smtp, db_path, kwargs = _isolated_sender(monkeypatch, tmp_path, error)
    claimed_at = api.time.time()
    assert api._email_dedupe_claim("stock_strategy_REPLY", 3600, now=claimed_at)

    assert api._send_email_alert("SMTP reply", "<p>Signal</p>", **kwargs) is False
    assert api._last_delivery_outcome() == "unknown"
    assert mail_outbox.has_uncertain_delivery_key("stock_strategy_REPLY")
    assert not api._email_dedupe_release_after_send("stock_strategy_REPLY", claimed_at=claimed_at)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status, delivery_state FROM signals").fetchall() == [
            (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED")
        ]

    smtp.failure = None
    assert api._send_email_alert("SMTP reply", "<p>Signal</p>", **kwargs) is False
    assert smtp.calls == 1


def test_negative_quit_reply_after_acceptance_cannot_cancel_or_repeat_delivery(
    monkeypatch, tmp_path
):
    smtp, db_path, kwargs = _isolated_sender(monkeypatch, tmp_path, None)

    def fail_quit(self):
        raise api.smtplib.SMTPResponseException(550, b"cleanup rejected")

    monkeypatch.setattr(smtp, "quit", fail_quit)
    assert api._send_email_alert("SMTP reply", "<p>Signal</p>", **kwargs) is True
    assert api._last_delivery_outcome() == "accepted"
    assert not mail_outbox.has_uncertain_delivery_key("stock_strategy_REPLY")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status, delivery_state FROM signals").fetchall() == [
            (tracker.STATUS_OPEN, "ACTIVE")
        ]
    assert api._send_email_alert("SMTP reply", "<p>Signal</p>", **kwargs) is False
    assert smtp.calls == 1
