"""Recipient retries through the real stdlib SMTP transaction implementation.

RCPT acceptance alone does not deliver a message. A 421 transaction abort
must not drop earlier accepted or not-yet-considered envelope recipients.
"""

import importlib.util
import json
import sqlite3

import pytest

import api
from modules import mail_outbox, signal_tracker as tracker
from test_alert_delivery_intent_api import _row, _setup


ADDRESSES = ("a@example.invalid", "b@example.invalid", "c@example.invalid")


@pytest.fixture
def scripted_sender(monkeypatch, tmp_path):
    # Keep the actual installed sendmail control flow, even when the offline
    # runner replaces the public SMTP constructors. No real socket is created.
    spec = importlib.util.spec_from_file_location("rcpt_test_stdlib_smtp", api.smtplib.__file__)
    stdlib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stdlib)
    for name in ("SMTPRecipientsRefused", "SMTPDataError", "SMTPSenderRefused"):
        setattr(stdlib, name, getattr(api.smtplib, name))

    class ScriptedSMTP(stdlib.SMTP):
        created = 0
        replies = {}
        injected_errors = {}
        data_acceptances = []
        envelope_attempts = []

        def __init__(self, *args, **kwargs):
            type(self).created += 1
            self.number = type(self).created
            self.does_esmtp = False
            self.accepted_rcpts = []

        def ehlo(self):
            return 250, b"hello"

        def ehlo_or_helo_if_needed(self):
            return None

        def starttls(self, **kwargs):
            return 220, b"tls fixture"

        def login(self, *args):
            return 235, b"login fixture"

        def mail(self, *args):
            return 250, b"sender accepted"

        def rcpt(self, address, options=()):
            code = type(self).replies.get(self.number, {}).get(address, 250)
            if code in (250, 251):
                self.accepted_rcpts.append(address)
            return code, b"controlled RCPT reply"

        def data(self, message):
            type(self).data_acceptances.append((self.number, tuple(self.accepted_rcpts)))
            return 250, b"message accepted"

        def sendmail(self, sender, recipients, message, *args, **kwargs):
            type(self).envelope_attempts.append(tuple(recipients))
            if self.number in type(self).injected_errors:
                raise type(self).injected_errors[self.number]
            return super().sendmail(sender, recipients, message, *args, **kwargs)

        def _rset(self):
            return 250, b"reset fixture"

        def quit(self):
            return 221, b"bye"

        def close(self):
            return None

    db_path = _setup(monkeypatch, tmp_path, ScriptedSMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", ScriptedSMTP)
    monkeypatch.setitem(api._SECRETS, "ALERT_EMAIL", ",".join(ADDRESSES))
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)
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
        "tracking_rows": [_row("RCPT421")],
        "delivery_dedupe_keys": ["stock_strategy_RCPT421"],
    }
    return ScriptedSMTP, db_path, kwargs


@pytest.mark.parametrize(("first_replies", "wanted"), [
    ({ADDRESSES[1]: 421}, ADDRESSES),
    ({ADDRESSES[0]: 550, ADDRESSES[1]: 421}, ADDRESSES[1:]),
    ({ADDRESSES[0]: 550, ADDRESSES[1]: 450, ADDRESSES[2]: 451}, ADDRESSES[1:]),
])
def test_pre_data_abort_retries_all_unmailed_nonpermanent_recipients(
    scripted_sender, first_replies, wanted
):
    smtp, db_path, kwargs = scripted_sender
    smtp.replies = {1: first_replies}
    assert api._send_email_alert("RCPT retry", "<p>Signal</p>", **kwargs) is True
    assert smtp.envelope_attempts == [ADDRESSES, wanted]
    assert smtp.data_acceptances == [(2, wanted)]  # First transaction had no DATA.
    with sqlite3.connect(db_path) as conn:
        status, state, recipients = conn.execute(
            "SELECT status, delivery_state, delivery_recipient_keys_json FROM signals"
        ).fetchone()
    assert (status, state) == (tracker.STATUS_OPEN, "ACTIVE")
    assert set(json.loads(recipients)) == {api._recipient_delivery_key(value) for value in wanted}
    assert not mail_outbox.has_uncertain_delivery_key("stock_strategy_RCPT421")


def test_repeated_rcpt421_preserves_retry_cap_and_releases_unaccepted_intent(scripted_sender):
    smtp, db_path, kwargs = scripted_sender
    smtp.replies = {attempt: {ADDRESSES[1]: 421} for attempt in (1, 2, 3)}
    claim_time = api.time.time()
    assert api._email_dedupe_claim("stock_strategy_RCPT421", 3600, now=claim_time)
    assert api._send_email_alert("RCPT retry", "<p>Signal</p>", **kwargs) is False
    assert smtp.envelope_attempts == [ADDRESSES, ADDRESSES, ADDRESSES]
    assert smtp.data_acceptances == []
    assert api._last_delivery_outcome() == "refused"
    assert not mail_outbox.has_uncertain_delivery_key("stock_strategy_RCPT421")
    assert api._email_dedupe_release_after_send("stock_strategy_RCPT421", claimed_at=claim_time)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0


def test_all_permanent_rcpt_refusals_are_never_retried(scripted_sender):
    smtp, db_path, kwargs = scripted_sender
    smtp.replies = {1: {address: 550 for address in ADDRESSES}}
    assert api._send_email_alert("RCPT retry", "<p>Signal</p>", **kwargs) is False
    assert smtp.envelope_attempts == [ADDRESSES]
    assert smtp.data_acceptances == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0


def test_permanent_refusal_on_later_attempt_remains_excluded(scripted_sender):
    smtp, _db_path, kwargs = scripted_sender
    smtp.replies = {
        1: {ADDRESSES[0]: 550, ADDRESSES[1]: 450, ADDRESSES[2]: 451},
        2: {ADDRESSES[1]: 450, ADDRESSES[2]: 550},
    }
    assert api._send_email_alert("RCPT retry", "<p>Signal</p>", **kwargs) is True
    assert smtp.envelope_attempts == [ADDRESSES, ADDRESSES[1:], (ADDRESSES[1],)]
    assert smtp.data_acceptances == [(3, (ADDRESSES[1],))]


def test_info_retry_after_rcpt_abort_never_repeats_prior_data_acceptance(scripted_sender):
    smtp, _db_path, kwargs = scripted_sender
    smtp.replies = {
        1: {ADDRESSES[1]: 450, ADDRESSES[2]: 450},
        2: {ADDRESSES[2]: 421},
    }
    kwargs.update(mail_class="info", tracking_scanner="", tracking_rows=[])
    assert api._send_email_alert("RCPT retry", "<p>Information</p>", **kwargs) is True
    assert smtp.envelope_attempts == [ADDRESSES, ADDRESSES[1:], ADDRESSES[1:]]
    assert smtp.data_acceptances == [(1, (ADDRESSES[0],)), (3, ADDRESSES[1:])]
    assert api._last_delivery_outcome() == "accepted"
    assert set(api._take_last_delivery_recipients()) == {
        api._recipient_delivery_key(value) for value in ADDRESSES
    }


def test_refusal_exception_cannot_inject_an_unrequested_recipient(scripted_sender):
    smtp, _db_path, kwargs = scripted_sender
    # Defensive adapter boundary: the real stdlib cannot invent an address,
    # but a malformed exception still must not widen the authorized envelope.
    smtp.injected_errors = {
        1: api.smtplib.SMTPRecipientsRefused({"outside@example.invalid": (450, b"injected")})
    }
    assert api._send_email_alert("RCPT retry", "<p>Signal</p>", **kwargs) is True
    assert smtp.envelope_attempts == [ADDRESSES, ADDRESSES]
    assert smtp.data_acceptances == [(2, ADDRESSES)]
