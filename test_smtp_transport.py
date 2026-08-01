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

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def sendmail(self, sender, recipients, msg):
        self.calls.append(("sendmail", sender, tuple(recipients)))


def _boom(host, port, timeout=None):
    raise TimeoutError("timed out")


# ── _smtp_transport_send ─────────────────────────────────────────────────────

def test_primary_ssl465_used_when_working(monkeypatch):
    """465/SSL klappt => 'ssl465', STARTTLS-Pfad wird nie angefasst."""
    created = []

    def _ssl_factory(host, port, timeout=None):
        inst = _FakeSMTP(host, port, timeout)
        created.append(inst)
        return inst

    def _plain_factory(host, port, timeout=None):  # darf nie aufgerufen werden
        raise AssertionError("587/STARTTLS darf bei funktionierendem 465 nicht starten")

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _ssl_factory)
    monkeypatch.setattr(bg_service.smtplib, "SMTP", _plain_factory)

    tag = bg_service._smtp_transport_send("MSG", "u@x.de", "pw", ["a@x.de"], timeout=7)

    assert tag == "ssl465"
    assert created[0].port == 465 and created[0].timeout == 7
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
