"""An explicit admin test checks transport, not trading-signal eligibility."""
from email import message_from_string
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api
from modules import mail_outbox


@pytest.fixture
def technical_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_require_admin", lambda authorization: ({}, "owner@example.invalid"))
    monkeypatch.setattr(api, "_email_alert_status", lambda: {"configured": True})
    monkeypatch.setattr(api, "_SECRETS", {"ALERT_EMAIL": "other@example.invalid,third@example.invalid"})
    def send(subject, body, **kwargs):
        sent.append((subject, body, kwargs))
        api._set_last_delivery_outcome("accepted")
        return True
    monkeypatch.setattr(api, "_send_email_alert", send)
    return sent


def test_one_test_to_authenticated_admin_not_configured_recipient_list(technical_mail):
    result = api.test_email_alert(authorization="Bearer fixture")
    assert len(technical_mail) == 1
    subject, body, kwargs = technical_mail[0]
    assert kwargs["recipient_emails"] == ["owner@example.invalid"]
    assert kwargs["mail_class"] == "info"
    assert kwargs["bypass_startup_cooldown"] is True
    assert kwargs["queue_on_failure"] is False
    assert "Technische Testmail" in subject
    assert "Kein Handelssignal" in body
    assert "ab jetzt automatisch" not in body
    assert result["delivery_status"] == "accepted"
    assert "Mailserver" in result["message"]


@pytest.mark.parametrize("outcome,status", [
    ("failed", "not_sent"), ("refused", "not_sent"), ("not_attempted", "not_sent"),
    ("unknown", "unknown"), ("outbox_queued", "unknown"),
    ("accepted_unjournaled", "unknown"), ("future_unresolved_outcome", "unknown"),
])
def test_failure_does_not_invent_password_error_or_retry(monkeypatch, technical_mail, outcome, status):
    attempts = []
    def send(*args, **kwargs):
        attempts.append(kwargs)
        api._set_last_delivery_outcome(outcome)
        return False
    monkeypatch.setattr(api, "_send_email_alert", send)
    with pytest.raises(HTTPException) as error:
        api.test_email_alert(authorization="Bearer fixture")
    assert len(attempts) == 1
    assert error.value.detail["delivery_status"] == status
    assert error.value.status_code == (409 if status == "unknown" else 502)
    if status == "unknown":
        assert "Nicht erneut senden" in error.value.detail["message"]
    assert "GMAIL_APP_PASSWORD" not in str(error.value.detail)


def test_not_configured_does_not_send(monkeypatch, technical_mail):
    monkeypatch.setattr(api, "_email_alert_status", lambda: {"configured": False})
    with pytest.raises(HTTPException) as error:
        api.test_email_alert(authorization="Bearer fixture")
    assert not technical_mail
    assert error.value.detail["delivery_status"] == "not_sent"


def test_admin_auth_checked_before_transport(monkeypatch, technical_mail):
    def denied(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Admin access required")
    monkeypatch.setattr(api, "_require_admin", denied)
    with pytest.raises(HTTPException) as error:
        api.test_email_alert(authorization=None)
    assert error.value.status_code == 403
    assert not technical_mail


@pytest.fixture
def real_technical_sender(monkeypatch, tmp_path):
    """Only replace the external SMTP boundary; retain sender/outbox behavior."""
    state = SimpleNamespace(error=None, envelopes=[], forbidden_calls=[])

    class ScriptedSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def sendmail(self, sender, recipients, message):
            state.envelopes.append((sender, list(recipients), message))
            if state.error is not None:
                raise state.error
            return {}

        def quit(self):
            pass

        def close(self):
            pass

    def forbidden(*args, **kwargs):
        state.forbidden_calls.append((args, kwargs))
        raise AssertionError("Technical info mail must not activate trades or resolve a subscriber cohort")

    monkeypatch.setattr(api, "_require_admin", lambda authorization: ({}, "owner@example.invalid"))
    monkeypatch.setattr(api, "_email_alert_status", lambda: {"configured": True})
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "sender@example.invalid", "GMAIL_APP_PASSWORD": "offline-fixture",
        "ALERT_EMAIL": "other@example.invalid,third@example.invalid",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "get_email_alert_recipients", forbidden)
    monkeypatch.setattr(api, "is_telegram_configured", forbidden)
    monkeypatch.setattr(api, "send_telegram_alert", forbidden)
    for name in ("prepare_alert_delivery_intent", "mark_alert_delivery_attempted",
                 "journal_alert_delivery_acceptance", "finalize_alert_delivery"):
        monkeypatch.setattr(api, name, forbidden)
    monkeypatch.setattr(api.smtplib, "SMTP", ScriptedSMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", ScriptedSMTP)
    monkeypatch.setattr(api.time, "sleep", lambda *args: None)
    monkeypatch.setattr(api, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(tmp_path / "outbox.sqlite"))
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args, **kwargs: None)
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    return state


def test_real_sender_accepts_one_admin_info_mail_without_trade_or_telegram(real_technical_sender):
    result = api.test_email_alert(authorization="Bearer fixture")
    assert result["delivery_status"] == "accepted"
    assert len(real_technical_sender.envelopes) == 1
    sender, recipients, wire = real_technical_sender.envelopes[0]
    assert sender == "sender@example.invalid"
    assert recipients == ["owner@example.invalid"]
    message = message_from_string(wire)
    assert message["To"] == "owner@example.invalid"
    assert "other@example.invalid" not in wire
    assert "third@example.invalid" not in wire
    assert not real_technical_sender.forbidden_calls
    stats = mail_outbox.stats()
    assert stats["total"] == stats["queued"] == stats["tracker_acceptance_pending_count"] == 0
    assert api._last_delivery_outcome() == "accepted"


@pytest.mark.parametrize("error", [
    api.smtplib.SMTPDataError(451, b"temporary DATA rejection"),
    api.smtplib.SMTPDataError(550, b"permanent DATA rejection"),
    api.smtplib.SMTPSenderRefused(550, b"sender rejected", "sender@example.invalid"),
])
def test_real_admin_failure_never_creates_delayed_mail(monkeypatch, real_technical_sender, error):
    real_technical_sender.error = error

    def forbidden_enqueue(*args, **kwargs):
        pytest.fail("A one-off personal test must not enqueue a later mail")

    monkeypatch.setattr(mail_outbox, "enqueue", forbidden_enqueue)
    with pytest.raises(HTTPException) as result:
        api.test_email_alert(authorization="Bearer fixture")
    assert result.value.status_code == 502
    assert result.value.detail["delivery_status"] == "not_sent"
    assert len(real_technical_sender.envelopes) == 1
    assert api._last_delivery_outcome() == "failed"
    assert mail_outbox.stats()["total"] == 0
    attempts = []
    assert mail_outbox.process_outbox(attempts.append)["sent"] == 0
    assert attempts == []


@pytest.mark.parametrize("error", [
    TimeoutError("DATA reply missing"),
    api.smtplib.SMTPServerDisconnected("DATA connection dropped"),
])
def test_real_admin_unknown_data_is_quarantined_not_queued_or_retried(
    monkeypatch, real_technical_sender, error
):
    real_technical_sender.error = error

    def forbidden_enqueue(*args, **kwargs):
        pytest.fail("Unknown DATA acceptance must never enter automatic retry")

    monkeypatch.setattr(mail_outbox, "enqueue", forbidden_enqueue)
    with pytest.raises(HTTPException) as result:
        api.test_email_alert(authorization="Bearer fixture")
    assert result.value.status_code == 409
    assert result.value.detail["delivery_status"] == "unknown"
    assert len(real_technical_sender.envelopes) == 1
    assert api._last_delivery_outcome() == "unknown"
    stats = mail_outbox.stats()
    assert stats["queued"] == 0
    assert stats["uncertain"] == 1
    assert mail_outbox.due_items() == []
    attempts = []
    assert mail_outbox.process_outbox(attempts.append)["sent"] == 0
    assert attempts == []
    assert len(real_technical_sender.envelopes) == 1


def test_default_info_sender_keeps_existing_outbox_contract(real_technical_sender):
    real_technical_sender.error = api.smtplib.SMTPDataError(550, b"DATA rejected")
    assert api._send_email_alert(
        "Ordinary information", "<p>Information</p>", mail_class="info",
        recipient_emails=["owner@example.invalid"], bypass_startup_cooldown=True,
    ) is False
    assert api._last_delivery_outcome() == "outbox_queued"
    assert mail_outbox.stats()["queued"] == 1
    pending = mail_outbox.due_items()
    assert len(pending) == 1
    assert pending[0]["mail_class"] == "info"
    assert pending[0]["recipients"] == ["owner@example.invalid"]


@pytest.mark.parametrize("queue_on_failure", [False, True])
@pytest.mark.parametrize("mail_class", ["trade", "swing_trade"])
def test_new_queue_option_cannot_delay_time_sensitive_stock_mail(
    real_technical_sender, queue_on_failure, mail_class
):
    real_technical_sender.error = api.smtplib.SMTPDataError(451, b"DATA temporarily rejected")
    assert api._send_email_alert(
        "Stock signal", "<p>Signal</p>", mail_class=mail_class,
        recipient_emails=["owner@example.invalid"], bypass_startup_cooldown=True,
        queue_on_failure=queue_on_failure,
    ) is False
    assert api._last_delivery_outcome() == "failed"
    assert len(real_technical_sender.envelopes) == 1
    assert mail_outbox.stats()["queued"] == mail_outbox.stats()["total"] == 0
