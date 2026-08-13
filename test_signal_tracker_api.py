"""Signal-Tracking + Telegram-Integration in api.py — Tests.

Die Kontrakt-Module (modules/signal_tracker, modules/notify_telegram) baut ein
Parallel-Team; api.py bindet sie defensiv (try/except ImportError, Fallback
None). Diese Tests mocken die Kontrakt-Funktionen deshalb via monkeypatch auf
den api-Globals und laufen identisch MIT und OHNE fertige Module.

Abgedeckt:
- record_alert_signals wird nach erfolgreichem Trade-Mail-Versand mit
  scanner_name + Rows (inkl. Entry/Stop/TP-Feldern) gerufen; bei sent=False nicht.
- Early-Mover-Digest: record nur fuer die 🚨-Trade-Rows, nie fuer die
  Watch-Mail (mail_class="watch").
- Telegram-Spiegel in _send_email_alert: trade-Mail => 1 Call mit finalem
  Betreff (inkl. "🚨 JETZT: "-Praefix); watch-Mail => 0; SMTP-Fehler => 0.
- /api/signal-performance: ohne Admin 403, mit Admin 200 + Struktur,
  ohne Modul 503.
- commercial-readiness meldet signal_tracker-Status.
- api bleibt importierbar, wenn die Modul-Imports fehlschlagen.

Mock-/Fixture-Muster folgt test_email_alert_audit.py / test_mail_class_api.py
(FakeSMTP fuer den echten _send_email_alert-Pfad).
"""

import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import api


@pytest.fixture(autouse=True)
def _isolate_email_state(monkeypatch, tmp_path):
    """Gleiches Isolations-Muster wie test_email_alert_audit.py."""
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(
        api,
        "_has_open_equivalent_trade_safe",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {
        "allowed": True,
        "session": "US_REGULAR",
        "reason": "unit-test market open",
    })
    monkeypatch.setattr(
        api,
        "_fetch_stock_swing_execution_state",
        lambda *a, **k: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "CLEAR",
            "Swing_4H_Execution_Reason": "unit_test_clear",
        },
    )


def _record_recorder(monkeypatch):
    """Recorder fuer den Kontrakt-Hook record_alert_signals (api-Global)."""
    calls = []

    def _recorder(
        scanner_name, rows, mail_class="trade", channel="email", **kwargs
    ):
        calls.append({
            "scanner": scanner_name,
            "rows": rows,
            "mail_class": mail_class,
            "channel": channel,
            "delivery_recipient_keys": kwargs.get("delivery_recipient_keys"),
        })
        return len(rows)

    monkeypatch.setattr(api, "record_alert_signals", _recorder)
    return calls


def _biotech_cache(tmp_path):
    """Alertbare Biotech-Row (Spiegel test_email_alert_audit.py, PFE-Fixture)."""
    row = {
        "Ticker": "PFE",
        "Grade": "A",
        "Score": 94,
        "RVOL": 1.31,
        "Preis": 26.48,
        "Signal_Direction": "LONG",
        "Entry": 26.48,
        "StopLoss": 25.78,
        "TP1": 27.71,
        "TP2": 29.49,
        "latest_bar_change_pct": 0.2,
        "latest_bar_close_pos": 0.76,
    }
    cache_file = tmp_path / "biotech.json"
    cache_file.write_text(json.dumps({"cached_at": datetime.now().isoformat(), "results": [row]}))
    return cache_file


def _early_mover_row(**overrides):
    """Handelbares Crypto-Swing-Setup (Spiegel test_mail_class_api.py)."""
    row = {
        "Symbol": "EMO",
        "Name": "Early Mover",
        "grade": "A",
        "score": 86,
        "entry_score": 85,
        "Price": 1.25,
        "Change24h": 4.2,
        "VolMCapRatio": 8.5,
        "direction": "LONG",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "CONDITIONAL_LONG",
        "entry_quality": "GOOD",
        "execution_trigger_ok": True,
        "signal_quality": "conditional_long_setup",
        "entry": 1.25,
        "stop_loss": 1.15,
        "tp1": 1.43,
        "tp2": 1.57,
        "live_rr_ratio": 2.4,
        "distance_to_entry_r": 0,
        "late_to_tp1": False,
        "btc_context": {"btc_24h": 1.2, "alpha_24h": 3.0, "tailwind": True},
        "risk_flags": [],
        "trade_setup": {
            "trade_action": "LONG_TRIGGER",
            "entry": 1.25,
            "stop_loss": 1.15,
            "tp1": 1.43,
            "tp2": 1.57,
            "live_rr": 2.4,
            "distance_to_entry_r": 0,
            "btc_context": {"btc_24h": 1.2, "alpha_24h": 3.0, "tailwind": True},
        },
    }
    row.update(overrides)
    return row


# ── 1) Signal-Logging nach erfolgreichem 🚨-Versand (BI/Biotech-Pfad) ──


def test_bi_alert_send_success_records_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PFE"}, "unit"))
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    deliveries = []
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda subject, body, **kwargs: deliveries.append(kwargs) or True,
    )

    api._check_and_alert("biotech", str(_biotech_cache(tmp_path)))

    assert len(deliveries) == 1
    assert deliveries[0]["tracking_scanner"] == "biotech"
    assert deliveries[0]["mail_class"] == "trade"
    rows = deliveries[0]["tracking_rows"]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "PFE"
    # Rows muessen die Entry/Stop/TP-Felder tragen (Tracker extrahiert tolerant)
    assert rows[0]["Entry"] == 26.48
    assert rows[0]["StopLoss"] == 25.78
    assert rows[0]["TP1"] == 27.71
    assert rows[0]["TP2"] == 29.49


def test_bi_alert_send_failure_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PFE"}, "unit"))
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: False)
    calls = _record_recorder(monkeypatch)

    api._check_and_alert("biotech", str(_biotech_cache(tmp_path)))

    assert calls == []


# ── 2) Early-Mover-Digest: record nur fuer 🚨-Rows, Watch-Mail loggt nie ──


def test_early_mover_digest_records_only_trade_rows(monkeypatch):
    sent = []
    monkeypatch.setattr(
        api,
        "_revalidate_early_mover_mail_candidate",
        lambda candidate, now_ts=None: {"ok": True, "candidate": candidate},
    )
    monkeypatch.setattr(
        api, "_send_email_alert",
        lambda subject, body, **kwargs: sent.append(kwargs) or True,
    )
    payload = {"coins": [
        _early_mover_row(Symbol="TRADENOW"),
        _early_mover_row(
            Symbol="RETESTZONE",
            trade_action="WAIT_FOR_RETEST",
            entry_status="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
        ),
    ]}

    api._send_early_mover_long_alerts(payload)

    # Trade-Digest UND Watch-Mail gingen raus ...
    assert {item.get("mail_class") for item in sent} == {"trade", "watch"}
    # ... aber record genau 1x und nur mit den 🚨-Trade-Rows
    tracked = [item for item in sent if item.get("tracking_scanner")]
    assert len(tracked) == 1
    assert tracked[0]["tracking_scanner"] == "early_movers"
    symbols = [r.get("symbol") for r in tracked[0]["tracking_rows"]]
    assert symbols == ["TRADENOW"]
    assert not any(
        r.get("symbol") == "RETESTZONE" for r in tracked[0]["tracking_rows"]
    )


def test_early_mover_watch_mail_alone_records_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(
        api, "_send_email_alert",
        lambda subject, body, **kwargs: sent.append(kwargs.get("mail_class")) or True,
    )
    calls = _record_recorder(monkeypatch)
    payload = {"coins": [
        _early_mover_row(
            Symbol="RETESTZONE",
            trade_action="WAIT_FOR_RETEST",
            entry_status="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
        ),
    ]}

    api._send_early_mover_long_alerts(payload)

    assert sent == ["watch"]  # Watch-Mail wurde versendet ...
    assert calls == []        # ... aber kein Signal-Logging dafuer


# ── 3) Telegram-Spiegel in _send_email_alert ──


def test_safe_telegram_formatter_returns_formatted_body(monkeypatch):
    monkeypatch.setattr(
        api,
        "format_alert_rows_for_telegram",
        lambda rows: f"formatted:{len(rows)}",
    )

    assert api._safe_format_telegram_rows([{"ticker": "AAA"}]) == "formatted:1"


class _FakeSMTP:
    """FakeSMTP-Muster aus test_mail_class_api.py."""

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, sender, recipients, message):
        pass

    def quit(self):
        pass


def _setup_real_send(monkeypatch):
    monkeypatch.setattr(api.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "op@x.com",
        "GMAIL_APP_PASSWORD": "pw",
        "ALERT_EMAIL": "op@x.com",
        "ALERT_OPERATOR_WATCH_OPTIN": "1",
        "SMTP_PORT": "587",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)


def _telegram_recorder(monkeypatch, configured=True):
    tg_calls = []
    monkeypatch.setattr(api, "is_telegram_configured", lambda: configured)
    monkeypatch.setattr(
        api, "send_telegram_alert",
        lambda subject, body_text="": tg_calls.append((subject, body_text)) or True,
    )
    return tg_calls


def test_telegram_mirror_sends_trade_mail_with_final_subject(monkeypatch):
    _setup_real_send(monkeypatch)
    tg_calls = _telegram_recorder(monkeypatch)

    ok = api._send_email_alert(
        "3 Top-Setups — BI Scanner LONG",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        telegram_text="TG-BODY",
    )

    assert ok is True
    assert len(tg_calls) == 1
    subject, body_text = tg_calls[0]
    # Finaler Betreff inkl. Klassen-Praefix "🚨 JETZT: "
    assert subject.startswith(api._MAIL_CLASS_SUBJECT_PREFIXES["trade"])
    assert "3 Top-Setups" in subject
    assert body_text == "TG-BODY"


def test_telegram_mirror_skips_watch_mail(monkeypatch):
    _setup_real_send(monkeypatch)
    tg_calls = _telegram_recorder(monkeypatch)

    ok = api._send_email_alert(
        "Crypto Retest-Zonen (1 Kandidaten)",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_class="watch",
        telegram_text="darf nie ankommen",
    )

    assert ok is True
    assert tg_calls == []


def test_telegram_mirror_skips_on_smtp_failure(monkeypatch):
    class _BoomSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("smtp down (unit)")

    monkeypatch.setattr(api.smtplib, "SMTP", _BoomSMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", _BoomSMTP)
    monkeypatch.setattr(api.time, "sleep", lambda *_a, **_k: None)  # Retry-Backoff skippen
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "op@x.com",
        "GMAIL_APP_PASSWORD": "pw",
        "ALERT_EMAIL": "op@x.com",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    tg_calls = _telegram_recorder(monkeypatch)

    ok = api._send_email_alert(
        "3 Top-Setups — BI Scanner LONG",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        telegram_text="TG-BODY",
    )

    assert ok is False
    assert tg_calls == []


def test_smtp_acceptance_is_never_retried_when_quit_fails(monkeypatch):
    calls = {"sendmail": 0, "ssl": 0}

    class _AcceptedThenQuitFails(_FakeSMTP):
        def sendmail(self, sender, recipients, message):
            calls["sendmail"] += 1
            return {}

        def quit(self):
            raise OSError("connection dropped after DATA acceptance")

    def _ssl_must_not_run(*args, **kwargs):
        calls["ssl"] += 1
        raise AssertionError("must not resend after DATA acceptance")

    monkeypatch.setattr(api.smtplib, "SMTP", _AcceptedThenQuitFails)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", _ssl_must_not_run)
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "op@x.com",
        "GMAIL_APP_PASSWORD": "pw",
        "ALERT_EMAIL": "op@x.com",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)

    ok = api._send_email_alert(
        "One irreversible delivery",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
    )

    assert ok is True
    assert calls == {"sendmail": 1, "ssl": 0}


def test_unknown_data_outcome_is_not_retried_or_queued(monkeypatch):
    calls = {"sendmail": 0, "ssl": 0, "enqueue": 0, "quarantine": 0}

    class _UnknownOutcome(_FakeSMTP):
        def sendmail(self, sender, recipients, message):
            calls["sendmail"] += 1
            raise ConnectionResetError("outcome unknown after DATA")

    def _ssl_must_not_run(*args, **kwargs):
        calls["ssl"] += 1
        raise AssertionError("must not resend unknown DATA outcome")

    class _Outbox:
        @staticmethod
        def enqueue(*args, **kwargs):
            calls["enqueue"] += 1
            return 1

        @staticmethod
        def quarantine(*args, **kwargs):
            calls["quarantine"] += 1
            return 1

    monkeypatch.setattr(api.smtplib, "SMTP", _UnknownOutcome)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", _ssl_must_not_run)
    monkeypatch.setattr(api, "_mail_outbox", _Outbox())
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "op@x.com",
        "GMAIL_APP_PASSWORD": "pw",
        "ALERT_EMAIL": "op@x.com",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)

    ok = api._send_email_alert(
        "Unknown outcome",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_class="info",
    )

    assert ok is False
    assert api._last_delivery_outcome() == "unknown"
    assert calls == {"sendmail": 1, "ssl": 0, "enqueue": 0, "quarantine": 1}


def test_partial_refusal_retries_only_refused_recipient(monkeypatch):
    calls = []

    class _PartialSMTP(_FakeSMTP):
        def sendmail(self, sender, recipients, message):
            calls.append(tuple(recipients))
            if len(calls) == 1:
                return {"b@example.com": (450, b"temporary")}
            return {}

    monkeypatch.setattr(api.smtplib, "SMTP", _PartialSMTP)
    monkeypatch.setattr(api.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "op@x.com", "GMAIL_APP_PASSWORD": "pw", "ALERT_EMAIL": "op@x.com",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)

    assert api._send_email_alert(
        "Partial", "<p>x</p>", bypass_startup_cooldown=True,
        mail_class="trade", recipient_emails=["a@example.com", "b@example.com"],
    )

    assert calls == [("a@example.com", "b@example.com"), ("b@example.com",)]
    assert len(api._take_last_delivery_recipients()) == 2
    assert api._last_delivery_outcome() == "accepted"


# ── 4) /api/signal-performance (Admin-Gate, Struktur, 503 ohne Modul) ──


def test_signal_performance_endpoint_requires_admin():
    with pytest.raises(api.HTTPException) as exc_info:
        api.api_signal_performance(days=30, authorization=None)

    assert exc_info.value.status_code == 403


def test_signal_performance_endpoint_returns_summary_for_admin(monkeypatch):
    monkeypatch.setattr(api, "_require_admin", lambda authorization: ({"email": "admin@x.com"}, "admin@x.com"))
    monkeypatch.setattr(
        api, "load_performance_summary",
        lambda days=90, mature_only=True: {
            "days": days,
            "signals": 5,
            "hit_rate_tp1": 0.6,
            "mature_only": mature_only,
        },
    )

    result = api.api_signal_performance(days=30, authorization="Bearer admin-token")

    assert result["days"] == 30
    assert result["signals"] == 5
    assert result["hit_rate_tp1"] == 0.6
    assert result["mature_only"] is True


def test_signal_performance_endpoint_503_without_module(monkeypatch):
    monkeypatch.setattr(api, "_require_admin", lambda authorization: ({"email": "admin@x.com"}, "admin@x.com"))
    monkeypatch.setattr(api, "load_performance_summary", None)

    with pytest.raises(api.HTTPException) as exc_info:
        api.api_signal_performance(days=30, authorization="Bearer admin-token")

    assert exc_info.value.status_code == 503
    assert "signal_tracker" in str(exc_info.value.detail)


# ── 5) commercial-readiness: signal_tracker-Status ──


def test_commercial_readiness_reports_signal_tracker(monkeypatch):
    monkeypatch.setattr(api, "record_alert_signals", lambda *a, **k: 0)
    monkeypatch.setattr(api, "load_performance_summary", lambda days=90: {})
    monkeypatch.setattr(api, "get_signal_count", lambda: 7)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["signal_tracker"] == {"available": True, "signals_recorded": 7}


def test_commercial_readiness_signal_tracker_unavailable(monkeypatch):
    monkeypatch.setattr(api, "record_alert_signals", None)
    monkeypatch.setattr(api, "load_performance_summary", None)
    monkeypatch.setattr(api, "get_signal_count", None)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["signal_tracker"] == {"available": False, "signals_recorded": 0}


# ── 6) Defensive Imports: api importierbar ohne signal_tracker/notify_telegram ──


def test_api_imports_with_blocked_signal_tracker_module():
    """api muss auch bei fehlschlagendem Modul-Import starten (Fallback None)."""
    repo_root = Path(api.__file__).resolve().parent
    code = (
        "import sys\n"
        "class _BlockSignalModules:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in ('modules.signal_tracker', 'modules.notify_telegram'):\n"
        "            raise ImportError('blocked for defensive-import test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _BlockSignalModules())\n"
        "import api\n"
        "assert api.record_alert_signals is None\n"
        "assert api.load_performance_summary is None\n"
        "assert api.get_signal_count is None\n"
        "assert api.is_telegram_configured is None\n"
        "assert api.send_telegram_alert is None\n"
        "assert api.format_alert_rows_for_telegram is None\n"
        "print('DEFENSIVE_IMPORT_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=240,
    )

    assert proc.returncode == 0, f"stderr: {proc.stderr[-2000:]}"
    assert "DEFENSIVE_IMPORT_OK" in proc.stdout
