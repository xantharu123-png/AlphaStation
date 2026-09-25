"""Background SMTP receipt regressions at the real stdlib transaction boundary."""

import atexit
import importlib.util
import io
import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

_path_exists = Path.exists
with patch.object(logging, "FileHandler", return_value=logging.NullHandler()), \
        patch.object(atexit, "register"), \
        patch.object(Path, "exists", lambda path: False if path.name in {"secrets.toml", ".env"} else _path_exists(path)):
    import bg_service as bg

from modules import mail_outbox, signal_tracker as tracker
from test_alert_delivery_intent_api import _row


ADDRESSES = ("a@example.invalid", "b@example.invalid", "c@example.invalid")
SECRETS = {"GMAIL_USER": "sender@example.invalid", "GMAIL_APP_PASSWORD": "offline-only",
           "ALERT_EMAIL": ",".join(ADDRESSES), "ALERT_SEND_TO_SUBSCRIBERS": "0"}


@pytest.fixture(params=[465, 587])
def bg_transport(request, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("bg_test_stdlib_smtp", bg.smtplib.__file__)
    stdlib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stdlib)
    for name in ("SMTPRecipientsRefused", "SMTPDataError", "SMTPSenderRefused", "SMTPResponseException"):
        setattr(stdlib, name, getattr(bg.smtplib, name))

    class ScriptedSMTP(stdlib.SMTP):
        constructor_ports = []
        envelopes = []
        data_attempts = []
        data_acceptances = []
        rcpt_replies = {}
        sender_code = 250
        data_code = 250
        data_error = None
        parser_failure = False
        quit_error = None

        def __init__(self, host, port, **kwargs):
            type(self).constructor_ports.append(port)
            if port == 465 and request.param == 587:
                raise ConnectionRefusedError("controlled pre-DATA SSL failure")
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
            return type(self).sender_code, b"controlled sender reply"

        def rcpt(self, address, options=()):
            code = type(self).rcpt_replies.get(address, 250)
            if code in (250, 251):
                self.accepted_rcpts.append(address)
            return code, b"controlled RCPT reply"

        def data(self, message):
            type(self).data_attempts.append(tuple(self.accepted_rcpts))
            if type(self).parser_failure:
                # Exercise the real getreply parser: its synthetic 500 is not
                # evidence of a negative server DATA acknowledgement.
                self.file = io.BytesIO(b"x" * (stdlib._MAXLINE + 1) + b"\r\n")
                return self.getreply()
            if type(self).data_error is not None:
                raise type(self).data_error
            if type(self).data_code == 250:
                type(self).data_acceptances.append(tuple(self.accepted_rcpts))
            return type(self).data_code, b"controlled DATA reply"

        def sendmail(self, sender, recipients, message, *args, **kwargs):
            type(self).envelopes.append(tuple(recipients))
            return super().sendmail(sender, recipients, message, *args, **kwargs)

        def _rset(self):
            return 250, b"reset fixture"

        def quit(self):
            if type(self).quit_error is not None:
                raise type(self).quit_error
            return 221, b"bye"

        def close(self):
            return None

    monkeypatch.setattr(bg.smtplib, "SMTP_SSL", ScriptedSMTP)
    monkeypatch.setattr(bg.smtplib, "SMTP", ScriptedSMTP)
    monkeypatch.setattr(bg.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bg, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(bg, "_send_telegram_companion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bg, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(tmp_path / "outbox.sqlite"))
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", str(tmp_path / "tracker.sqlite"))
    monkeypatch.setattr(tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", "")
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    return ScriptedSMTP, request.param


def _transport():
    return bg._smtp_transport_send("MSG", SECRETS["GMAIL_USER"], SECRETS["GMAIL_APP_PASSWORD"], ADDRESSES)


def _sender(mail_class="info"):
    return bg._send_email_alert("Background receipt", "<p>Signal</p>", SECRETS,
                               mail_class=mail_class, bypass_startup_delay=True,
                               recipient_emails=list(ADDRESSES), outbox_dedupe_keys=["bg-receipt-key"])


def test_rcpt421_reports_entire_unaccepted_envelope_without_port_replay(bg_transport):
    smtp, port = bg_transport
    smtp.rcpt_replies = {ADDRESSES[1]: 421}
    result = _transport()
    assert result.accepted == ()
    assert result.refused == ADDRESSES
    assert smtp.envelopes == [ADDRESSES]
    assert smtp.data_attempts == []
    assert smtp.constructor_ports == ([465] if port == 465 else [465, 587])


def test_actual_partial_data_acceptance_keeps_only_accepted_cohort(bg_transport):
    smtp, _port = bg_transport
    smtp.rcpt_replies = {ADDRESSES[1]: 450}
    result = _transport()
    assert result.accepted == (ADDRESSES[0], ADDRESSES[2])
    assert result.refused == (ADDRESSES[1],)
    assert smtp.data_acceptances == [(ADDRESSES[0], ADDRESSES[2])]
    assert smtp.envelopes == [ADDRESSES]


@pytest.mark.parametrize("failure", ["parser500", "disconnect", "data250", "data354"])
def test_unknown_data_response_never_becomes_definitive_failure(bg_transport, failure):
    smtp, port = bg_transport
    if failure == "parser500":
        smtp.parser_failure = True
    elif failure == "disconnect":
        smtp.data_error = ConnectionResetError("DATA acknowledgement lost")
    else:
        smtp.data_error = bg.smtplib.SMTPDataError(int(failure[4:]), b"unexpected reply")
    with pytest.raises(bg._SMTPDataOutcomeUnknown):
        _transport()
    assert smtp.envelopes == [ADDRESSES]
    assert smtp.constructor_ports == ([465] if port == 465 else [465, 587])


@pytest.mark.parametrize(("stage", "code"), [("sender", 450), ("sender", 550), ("data", 451), ("data", 550)])
def test_real_negative_mail_or_data_reply_remains_definitive(bg_transport, stage, code):
    smtp, port = bg_transport
    if stage == "sender":
        smtp.sender_code = code
    else:
        smtp.data_code = code
    with pytest.raises(bg._SMTPDefinitiveDeliveryFailure):
        _transport()
    assert smtp.data_acceptances == []
    assert smtp.envelopes == [ADDRESSES]
    assert smtp.constructor_ports == ([465] if port == 465 else [465, 587])


def test_negative_quit_reply_after_data_acceptance_cannot_retry(bg_transport):
    smtp, _port = bg_transport
    smtp.quit_error = bg.smtplib.SMTPResponseException(500, b"QUIT failed")
    assert _transport().accepted == ADDRESSES
    assert smtp.data_acceptances == [ADDRESSES]
    assert smtp.envelopes == [ADDRESSES]


def test_sender_rcpt421_never_fabricates_tracker_acceptance_and_queues_all(bg_transport):
    smtp, _port = bg_transport
    smtp.rcpt_replies = {ADDRESSES[1]: 421}
    assert _sender() is False
    assert smtp.envelopes == [ADDRESSES] * 3
    assert smtp.data_attempts == []
    delivery = bg._last_email_delivery()
    assert delivery["accepted"] == ()
    assert delivery["pending"] == ADDRESSES
    assert delivery["queued"] is True
    assert delivery["outcome_unknown"] is False
    assert bg._record_alert_signals_safe("stock_strategy", [_row("BGRECEIPT")]) == 0
    with sqlite3.connect(mail_outbox.MAIL_OUTBOX_DB_PATH) as conn:
        rows = conn.execute("SELECT status, recipients_json FROM mail_outbox").fetchall()
    assert [(status, tuple(json.loads(recipients))) for status, recipients in rows] == [("pending", ADDRESSES)]


def test_sender_parser500_is_quarantined_once_without_retry_or_normal_queue(bg_transport):
    smtp, _port = bg_transport
    smtp.parser_failure = True
    assert _sender() is False
    assert smtp.envelopes == [ADDRESSES]
    delivery = bg._last_email_delivery()
    assert delivery["accepted"] == ()
    assert delivery["pending"] == ADDRESSES
    assert delivery["outcome_unknown"] is True
    assert delivery["queued"] is False
    assert mail_outbox.has_uncertain_delivery_key("bg-receipt-key")
    with sqlite3.connect(mail_outbox.MAIL_OUTBOX_DB_PATH) as conn:
        assert conn.execute("SELECT DISTINCT status FROM mail_outbox").fetchall() == [("uncertain",)]


def test_outbox_worker_preserves_whole_cohort_after_rcpt421(bg_transport):
    smtp, _port = bg_transport
    smtp.rcpt_replies = {ADDRESSES[1]: 421}
    item_id = mail_outbox.enqueue("Queued receipt", "<p>Info</p>", list(ADDRESSES), mail_class="info")
    assert item_id
    result = bg._run_mail_outbox_job(SECRETS)
    assert result["sent"] == 0
    assert result["failed"] == 1
    with sqlite3.connect(mail_outbox.MAIL_OUTBOX_DB_PATH) as conn:
        status, recipients = conn.execute("SELECT status, recipients_json FROM mail_outbox WHERE id=?", (item_id,)).fetchone()
    assert status == "pending"
    assert tuple(json.loads(recipients)) == ADDRESSES
    assert smtp.data_attempts == []
