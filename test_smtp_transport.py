#!/usr/bin/env python3
"""SMTP-Transport-Fallback (bg_service._smtp_transport_send) — Regression-Tests.

Vorfall 2026-08-01 (KW31-Wochenreport): smtp.gmail.com:465 lief 3x in den
Timeout (~30 s/Versuch) => Mail endgueltig verloren. Fix: primaer 465/SSL,
Fallback 587/STARTTLS; Timeout via SMTP_TIMEOUT konfigurierbar (Default 15).

Beweist:
- Primaerpfad 465/SSL wird genutzt, wenn er funktioniert (587 unberuehrt)
- Bei 465-Fehler: 587/STARTTLS mit ehlo/starttls/login/sendmail
- Scheitern BEIDER Transporte => Exception propagiert (Retry-Loop entscheidet)
- _smtp_timeout_seconds: Env/Secrets-Override, defensives Clampen
- Integration: _send_email_alert liefert ueber den Fallback aus und
  protokolliert den Transport im Erfolgs-Log
"""
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bg_service


class _FakeSMTP:
    """Aufzeichnender SMTP-Ersatz mit Context-Manager-Protokoll."""

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.context = context
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.context = context
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def sendmail(self, sender, recipients, msg):
        self.calls.append(("sendmail", sender, tuple(recipients)))


def _boom(host, port, timeout=None, **kwargs):
    raise TimeoutError("timed out")


# ── _smtp_transport_send ─────────────────────────────────────────────────────

def test_primary_ssl465_used_when_working(monkeypatch):
    """465/SSL klappt => 'ssl465', STARTTLS-Pfad wird nie angefasst."""
    created = []

    def _ssl_factory(host, port, timeout=None, context=None):
        inst = _FakeSMTP(host, port, timeout, context)
        created.append(inst)
        return inst

    def _plain_factory(host, port, timeout=None, **kwargs):  # darf nie aufgerufen werden
        raise AssertionError("587/STARTTLS darf bei funktionierendem 465 nicht starten")

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _ssl_factory)
    monkeypatch.setattr(bg_service.smtplib, "SMTP", _plain_factory)

    tag = bg_service._smtp_transport_send("MSG", "u@x.de", "pw", ["a@x.de"], timeout=7)

    assert tag == "ssl465"
    assert created[0].port == 465 and created[0].timeout == 7
    assert created[0].context is not None
    assert created[0].context.check_hostname is True
    assert ("login", "u@x.de", "pw") in created[0].calls
    assert ("sendmail", "u@x.de", ("a@x.de",)) in created[0].calls


def test_fallback_starttls587_after_465_timeout(monkeypatch):
    """465 wirft => 587/STARTTLS mit korrekter Reihenfolge, Tag 'starttls587'."""
    created = []

    def _plain_factory(host, port, timeout=None):
        inst = _FakeSMTP(host, port, timeout)
        created.append(inst)
        return inst

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _boom)
    monkeypatch.setattr(bg_service.smtplib, "SMTP", _plain_factory)

    tag = bg_service._smtp_transport_send("MSG", "u@x.de", "pw", ["a@x.de"], timeout=9)

    assert tag == "starttls587"
    assert created[0].port == 587 and created[0].timeout == 9
    # Reihenfolge: ehlo -> starttls -> ehlo -> login -> sendmail
    assert created[0].calls[:3] == ["ehlo", "starttls", "ehlo"]
    assert created[0].context is not None
    assert created[0].context.check_hostname is True
    assert ("login", "u@x.de", "pw") in created[0].calls
    assert ("sendmail", "u@x.de", ("a@x.de",)) in created[0].calls


def test_both_transports_failing_propagates(monkeypatch):
    """465 UND 587 scheitern => die 587-Exception geht an den Caller
    (dessen Retry-Loop/Backlog-Logik entscheidet ueber weitere Versuche)."""
    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _boom)
    monkeypatch.setattr(bg_service.smtplib, "SMTP", _boom)

    try:
        bg_service._smtp_transport_send("MSG", "u", "p", ["a@x.de"])
        raised = None
    except TimeoutError as exc:
        raised = exc
    assert isinstance(raised, TimeoutError)


def test_partial_refusal_is_reported_without_port_fallback(monkeypatch):
    class PartialSMTP(_FakeSMTP):
        def sendmail(self, sender, recipients, msg):
            super().sendmail(sender, recipients, msg)
            return {"b@x.de": (450, b"temporary refusal")}

    created = []

    def _ssl_factory(host, port, timeout=None, context=None):
        instance = PartialSMTP(host, port, timeout, context)
        created.append(instance)
        return instance

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _ssl_factory)
    monkeypatch.setattr(
        bg_service.smtplib,
        "SMTP",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("partial DATA result must not switch ports")
        ),
    )

    result = bg_service._smtp_transport_send(
        "MSG", "u@x.de", "pw", ["a@x.de", "b@x.de"]
    )

    assert result == "ssl465"
    assert result.accepted == ("a@x.de",)
    assert result.refused == ("b@x.de",)
    assert len(created) == 1


def test_quit_failure_after_data_never_resends(monkeypatch):
    class QuitFailureSMTP(_FakeSMTP):
        def quit(self):
            self.calls.append("quit")
            raise ConnectionResetError("connection lost after DATA")

        def close(self):
            self.calls.append("close")

    created = []

    def _ssl_factory(host, port, timeout=None, context=None):
        instance = QuitFailureSMTP(host, port, timeout, context)
        created.append(instance)
        return instance

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _ssl_factory)
    monkeypatch.setattr(
        bg_service.smtplib,
        "SMTP",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("QUIT failure must not trigger a duplicate DATA send")
        ),
    )

    result = bg_service._smtp_transport_send(
        "MSG", "u@x.de", "pw", ["a@x.de"]
    )

    assert result.accepted == ("a@x.de",)
    assert [call for call in created[0].calls if isinstance(call, tuple) and call[0] == "sendmail"] == [
        ("sendmail", "u@x.de", ("a@x.de",))
    ]
    assert created[0].calls[-2:] == ["quit", "close"]


def test_connection_loss_during_data_never_falls_back_or_retries(monkeypatch):
    class UnknownOutcomeSMTP(_FakeSMTP):
        def sendmail(self, sender, recipients, msg):
            super().sendmail(sender, recipients, msg)
            raise ConnectionResetError("connection lost after DATA acceptance")

        def close(self):
            self.calls.append("close")

    created = []

    def _ssl_factory(host, port, timeout=None, context=None):
        instance = UnknownOutcomeSMTP(host, port, timeout, context)
        created.append(instance)
        return instance

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _ssl_factory)
    monkeypatch.setattr(
        bg_service.smtplib,
        "SMTP",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unknown DATA outcome must not switch ports")
        ),
    )

    try:
        bg_service._smtp_transport_send(
            "MSG", "u@x.de", "pw", ["a@x.de"]
        )
        raised = None
    except bg_service._SMTPDataOutcomeUnknown as exc:
        raised = exc

    assert isinstance(raised, bg_service._SMTPDataOutcomeUnknown)
    assert [call for call in created[0].calls if isinstance(call, tuple) and call[0] == "sendmail"] == [
        ("sendmail", "u@x.de", ("a@x.de",))
    ]


# ── _smtp_timeout_seconds ────────────────────────────────────────────────────

def test_timeout_default_and_overrides(monkeypatch):
    monkeypatch.delenv("SMTP_TIMEOUT", raising=False)
    assert bg_service._smtp_timeout_seconds({}) == 15
    assert bg_service._smtp_timeout_seconds({"SMTP_TIMEOUT": "25"}) == 25
    monkeypatch.setenv("SMTP_TIMEOUT", "20")
    assert bg_service._smtp_timeout_seconds({}) == 20
    # Secrets schlagen Env
    assert bg_service._smtp_timeout_seconds({"SMTP_TIMEOUT": "30"}) == 30


def test_timeout_defensive_clamping(monkeypatch):
    monkeypatch.delenv("SMTP_TIMEOUT", raising=False)
    assert bg_service._smtp_timeout_seconds({"SMTP_TIMEOUT": "abc"}) == 15
    assert bg_service._smtp_timeout_seconds({"SMTP_TIMEOUT": "1"}) == 15    # zu klein
    assert bg_service._smtp_timeout_seconds({"SMTP_TIMEOUT": "999"}) == 15  # zu gross
    assert bg_service._smtp_timeout_seconds(None) == 15


# ── Integration: _send_email_alert ueber den Fallback ────────────────────────

def test_send_email_alert_uses_fallback_and_logs_transport(monkeypatch, tmp_path):
    """465 dauerhaft down, 587 ok => Versand True, Erfolgs-Log traegt den
    Transport-Tag, kein einziger Retry-Sleep noetig (Fallback im 1. Versuch)."""
    import time as _time

    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", _time.time() - 3600)
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dd.json"))
    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _boom)
    created = []

    def _plain_factory(host, port, timeout=None):
        inst = _FakeSMTP(host, port, timeout)
        created.append(inst)
        return inst

    monkeypatch.setattr(bg_service.smtplib, "SMTP", _plain_factory)
    monkeypatch.setattr(bg_service, "_send_telegram_companion", lambda *a, **k: None)
    logs = []
    monkeypatch.setattr(bg_service.log, "info", lambda *a, **k: logs.append(a))
    sleeps = []
    monkeypatch.setattr(bg_service.time, "sleep", lambda s: sleeps.append(s))

    ok = bg_service._send_email_alert(
        "Transport-Test",
        "<p>Body</p>",
        {"GMAIL_USER": "u@x.de", "GMAIL_APP_PASSWORD": "pw",
         "ALERT_EMAIL": "u@x.de", "ALERT_SEND_TO_SUBSCRIBERS": "0"},
    )

    assert ok is True
    assert created and created[0].port == 587
    assert sleeps == []  # Fallback greift innerhalb von Versuch 1
    success = [a for a in logs if a and "E-Mail Alert gesendet" in str(a[0])]
    assert success and "starttls587" in str(success[0])


def test_partial_delivery_tracks_only_accepted_initial_recipients(
    monkeypatch, tmp_path
):
    import time as _time

    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", _time.time() - 3600)
    monkeypatch.setattr(
        bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dd.json")
    )
    monkeypatch.setattr(bg_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bg_service, "_send_telegram_companion", lambda *a, **k: None)
    calls = []

    def partial_transport(message, _user, _password, recipients, timeout=15):
        calls.append((message, tuple(recipients)))
        refused = {"b@x.de": (450, b"retry")}
        return bg_service._SMTPDeliveryResult(
            "ssl465", recipients, refused
        )

    monkeypatch.setattr(bg_service, "_smtp_transport_send", partial_transport)

    ok = bg_service._send_email_alert(
        "Partial",
        "<p>Body</p>",
        {
            "GMAIL_USER": "u@x.de",
            "GMAIL_APP_PASSWORD": "pw",
            "ALERT_EMAIL": "a@x.de,b@x.de",
            "ALERT_SEND_TO_SUBSCRIBERS": "0",
        },
        mail_class="trade",
    )

    assert ok is True
    assert [recipients for _message, recipients in calls] == [
        ("a@x.de", "b@x.de"),
        ("b@x.de",),
        ("b@x.de",),
    ]
    assert len({message for message, _recipients in calls}) == 1
    assert bg_service._last_email_delivery() == {
        "intended": ("a@x.de", "b@x.de"),
        "accepted": ("a@x.de",),
        "pending": ("b@x.de",),
        "queued": False,
        "outcome_unknown": False,
    }

    recorded = []

    def fake_record(_scanner, _rows, **kwargs):
        recorded.append(kwargs)
        return 1

    monkeypatch.setattr(bg_service, "record_alert_signals", fake_record)
    assert bg_service._record_alert_signals_safe(
        "stock_strategy", [{"Ticker": "ABC"}], mail_class="trade"
    ) == 1
    assert recorded[0]["delivery_recipient_keys"] == (
        bg_service._recipient_delivery_key("a@x.de"),
    )


def test_unknown_data_outcome_is_not_retried_or_queued(monkeypatch):
    import time as _time

    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", _time.time() - 3600)
    calls = []

    def unknown_transport(*args, **kwargs):
        calls.append((args, kwargs))
        raise bg_service._SMTPDataOutcomeUnknown("after DATA")

    queued = []
    quarantined = []

    class FakeOutbox:
        @staticmethod
        def enqueue(*args, **kwargs):
            queued.append((args, kwargs))
            return 1

        @staticmethod
        def quarantine(*args, **kwargs):
            quarantined.append((args, kwargs))
            return 2

    monkeypatch.setattr(bg_service, "_smtp_transport_send", unknown_transport)
    monkeypatch.setattr(bg_service, "_mail_outbox", FakeOutbox())
    monkeypatch.setattr(
        bg_service.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("unknown DATA must not retry")
        ),
    )

    ok = bg_service._send_email_alert(
        "Unknown",
        "<p>Body</p>",
        {
            "GMAIL_USER": "u@x.de",
            "GMAIL_APP_PASSWORD": "pw",
            "ALERT_EMAIL": "a@x.de",
            "ALERT_SEND_TO_SUBSCRIBERS": "0",
        },
        mail_class="signal_update",
        enqueue_on_failure=False,
        outbox_dedupe_keys=["signal_update_event_recipient"],
    )

    assert ok is False
    assert len(calls) == 1
    assert queued == []
    assert len(quarantined) == 1
    assert quarantined[0][1]["mail_class"] == "signal_update"
    assert set(quarantined[0][1]["delivery_dedupe_keys"]) == {
        "signal_update_event_recipient",
        bg_service._followup_recipient_dedupe_key(
            "signal_update_event_recipient", "a@x.de"
        ),
    }
    assert bg_service._last_email_delivery()["outcome_unknown"] is True


def test_partial_acceptance_quarantines_only_unknown_recipient(monkeypatch):
    import time as _time

    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", _time.time() - 3600)
    monkeypatch.setattr(bg_service.time, "sleep", lambda _seconds: None)
    calls = []

    def partial_then_unknown(_message, _user, _password, recipients, timeout=15):
        calls.append(tuple(recipients))
        if len(calls) == 1:
            return bg_service._SMTPDeliveryResult(
                "ssl465", recipients, {"b@x.de": (450, b"retry")}
            )
        raise bg_service._SMTPDataOutcomeUnknown("after DATA")

    quarantined = []

    class FakeOutbox:
        @staticmethod
        def quarantine(*args, **kwargs):
            quarantined.append((args, kwargs))
            return 17

    monkeypatch.setattr(bg_service, "_smtp_transport_send", partial_then_unknown)
    monkeypatch.setattr(bg_service, "_mail_outbox", FakeOutbox())
    monkeypatch.setattr(bg_service, "_send_telegram_companion", lambda *a, **k: None)

    ok = bg_service._send_email_alert(
        "Partial unknown",
        "<p>Body</p>",
        {
            "GMAIL_USER": "u@x.de",
            "GMAIL_APP_PASSWORD": "pw",
            "ALERT_EMAIL": "a@x.de,b@x.de",
            "ALERT_SEND_TO_SUBSCRIBERS": "0",
        },
        mail_class="trade",
        outbox_dedupe_keys=["trade-event"],
    )

    assert ok is True
    assert calls == [("a@x.de", "b@x.de"), ("b@x.de",)]
    assert len(quarantined) == 1
    assert quarantined[0][0][2] == ["b@x.de"]
    assert quarantined[0][1]["delivery_dedupe_keys"] == [
        bg_service._followup_recipient_dedupe_key(
            "trade-event", "b@x.de"
        )
    ]
    assert bg_service._last_email_delivery()["accepted"] == ("a@x.de",)
    assert bg_service._last_email_delivery()["pending"] == ("b@x.de",)
    assert bg_service._last_email_delivery()["outcome_unknown"] is True


def test_unknown_quarantine_requires_fallback_receipt(monkeypatch):
    import time as _time

    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", _time.time() - 3600)
    monkeypatch.setattr(
        bg_service,
        "_smtp_transport_send",
        lambda *_a, **_k: (_ for _ in ()).throw(
            bg_service._SMTPDataOutcomeUnknown("after DATA")
        ),
    )
    fallback = []

    class FakeOutbox:
        @staticmethod
        def quarantine(*_args, **_kwargs):
            return None

        @staticmethod
        def register_uncertain_delivery_keys(keys, **kwargs):
            fallback.append((keys, kwargs))
            return -9

    monkeypatch.setattr(bg_service, "_mail_outbox", FakeOutbox())

    assert bg_service._send_email_alert(
        "Trade unknown",
        "<p>Body</p>",
        {
            "GMAIL_USER": "u@x.de",
            "GMAIL_APP_PASSWORD": "pw",
            "ALERT_EMAIL": "a@x.de",
            "ALERT_SEND_TO_SUBSCRIBERS": "0",
        },
        mail_class="trade",
        outbox_dedupe_keys=["trade-event"],
    ) is False
    assert len(fallback) == 1
    assert "trade-event" in fallback[0][0]


def test_bg_outbox_quarantines_legacy_actionable_entry_before_smtp(
    monkeypatch, tmp_path
):
    """A pre-existing trade row must not bypass quote/path/tracker contracts."""
    from modules import mail_outbox

    db_path = tmp_path / "legacy-actionable-outbox.sqlite"
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(db_path))
    item_id = mail_outbox.enqueue(
        "Old entry",
        "<p>stale setup</p>",
        ["recipient@example.com"],
        mail_class="trade",
        delivery_dedupe_keys=["legacy-entry-1"],
        now=1_000,
    )
    assert item_id is not None

    smtp_calls = []
    monkeypatch.setattr(bg_service, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(
        bg_service,
        "_send_email_alert",
        lambda *args, **kwargs: smtp_calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(mail_outbox.time, "time", lambda: 1_000)

    result = bg_service._run_mail_outbox_job({})

    assert smtp_calls == []
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert result["uncertain"] == 1
    status = mail_outbox.stats(now=1_000)
    assert status["uncertain"] == 1
    assert "manual review required" in status["last_error"]
