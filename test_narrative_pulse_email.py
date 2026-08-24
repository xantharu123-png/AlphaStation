from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import api
import pytest


@pytest.fixture(autouse=True)
def _isolate_email_dedupe(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email-dedupe.json"))
    monkeypatch.setattr(api, "_mail_outbox", None)


def _row(name, ticker, c1, c5, c20, rvol=1.0, cmf=0.0, obv=0.0):
    return {
        "sector": name,
        "narrative": name,
        "ticker": ticker,
        "change_1d": c1,
        "change_5d": c5,
        "change_20d": c20,
        "rvol": rvol,
        "cmf": cmf,
        "obv_change": obv,
        "examples": ["AAA", "BBB", "CCC"],
    }


def test_narrative_pulse_sorts_bullish_and_bearish(monkeypatch):
    monkeypatch.setattr(api, "_narrative_representatives", lambda tickers, direction, max_items=3: [])
    payload = api._build_narrative_pulse([
        _row("Semiconductors", "SMH", 2.0, 8.0, 15.0, rvol=1.6, cmf=0.2, obv=12),
        _row("Regional Banks", "KRE", -1.5, -7.0, -12.0, rvol=1.4, cmf=-0.2, obv=-14),
        _row("Utilities", "XLU", 0.1, 0.4, 1.0, rvol=0.8),
    ])

    assert payload["bullish"][0]["sector"] == "Semiconductors"
    assert payload["bearish"][0]["sector"] == "Regional Banks"
    assert payload["bullish"][0]["bias"] == "BULLISCH"
    assert payload["bearish"][0]["bias"] == "BEARISCH"


def test_narrative_pulse_email_is_daily_and_does_not_use_etf_word(monkeypatch):
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(api, "_narrative_pulse_recipients", lambda frequency: ["narrative@example.com"] if frequency == "daily" else [])
    sent = {}
    marked = []

    def fake_send(subject, body, *args, **kwargs):
        sent["subject"] = subject
        sent["body"] = body
        sent["recipients"] = kwargs.get("recipient_emails")
        sent["delivery_dedupe_keys"] = kwargs.get("delivery_dedupe_keys")
        return True

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    monkeypatch.setattr(api, "_email_dedupe_mark", lambda key, now=None: marked.append((key, now)))
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    assert api._send_narrative_pulse_email(payload) is True
    assert "Narrative Pulse" in sent["subject"]
    assert "Semiconductors" in sent["body"]
    assert "Regional Banks" in sent["body"]
    assert "ETF" not in sent["body"].upper()
    assert sent["recipients"] == ["narrative@example.com"]
    assert sent["delivery_dedupe_keys"] == [marked[0][0]]
    assert marked[0][0].startswith("narrative_pulse_daily_")


def test_narrative_pulse_success_blocks_later_hourly_run_in_same_daily_bucket(monkeypatch, tmp_path):
    rendered_at = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    sent = []
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email-dedupe.json"))
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(
        api,
        "_narrative_pulse_recipients",
        lambda frequency: ["narrative@example.com"] if frequency == "daily" else [],
    )

    def fake_send(subject, body, **kwargs):
        sent.append((subject, kwargs))
        return True

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    first = rendered_at.timestamp()
    assert api._send_narrative_pulse_email(payload, now=first) is True
    assert api._send_narrative_pulse_email(payload, now=first + 3600) is False
    assert len(sent) == 1
    assert sent[0][1]["delivery_dedupe_keys"] == ["narrative_pulse_daily_20260824"]

    assert api._send_narrative_pulse_email(payload, now=first + 86400) is True
    assert len(sent) == 2


def test_parallel_narrative_pulse_runs_send_only_once(monkeypatch):
    rendered_at = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    sent = []
    monkeypatch.setattr(
        api,
        "_narrative_pulse_recipients",
        lambda frequency: ["narrative@example.com"] if frequency == "daily" else [],
    )
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda subject, body, **kwargs: sent.append(subject) or True,
    )
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: api._send_narrative_pulse_email(payload, now=rendered_at.timestamp()),
            range(8),
        ))

    assert results.count(True) == 1
    assert len(sent) == 1


def test_narrative_pulse_unknown_smtp_outcome_stays_fail_closed(monkeypatch):
    rendered_at = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    attempts = []
    monkeypatch.setattr(
        api,
        "_narrative_pulse_recipients",
        lambda frequency: ["narrative@example.com"] if frequency == "daily" else [],
    )

    def fake_send(subject, body, **kwargs):
        attempts.append(subject)
        api._set_last_delivery_outcome("unknown")
        return False

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    first = rendered_at.timestamp()
    assert api._send_narrative_pulse_email(payload, now=first) is False
    assert api._send_narrative_pulse_email(payload, now=first + 3600) is False
    assert len(attempts) == 1


def test_narrative_pulse_definite_pre_data_failure_releases_claim(monkeypatch):
    rendered_at = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    attempts = []
    monkeypatch.setattr(
        api,
        "_narrative_pulse_recipients",
        lambda frequency: ["narrative@example.com"] if frequency == "daily" else [],
    )

    def fake_send(subject, body, **kwargs):
        attempts.append(subject)
        api._set_last_delivery_outcome("failed")
        return False

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    first = rendered_at.timestamp()
    assert api._send_narrative_pulse_email(payload, now=first) is False
    assert api._send_narrative_pulse_email(payload, now=first + 1) is False
    assert len(attempts) == 2


def test_info_outbox_owns_delivery_dedupe_key_after_safe_smtp_failure(monkeypatch):
    queued = []

    class _RejectedSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, message):
            raise api.smtplib.SMTPRecipientsRefused({
                recipients[0]: (550, b"recipient rejected"),
            })

        def quit(self):
            pass

    class _Outbox:
        @staticmethod
        def enqueue(*args, **kwargs):
            queued.append((args, kwargs))
            return 41

    monkeypatch.setattr(api.smtplib, "SMTP", _RejectedSMTP)
    monkeypatch.setattr(api, "_mail_outbox", _Outbox())
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "sender@example.com",
        "GMAIL_APP_PASSWORD": "password",
        "ALERT_EMAIL": "owner@example.com",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)

    key = "narrative_pulse_daily_20260824"
    assert api._send_email_alert(
        "Narrative Pulse",
        "<p>context</p>",
        bypass_startup_cooldown=True,
        recipient_emails=["owner@example.com"],
        mail_class="info",
        delivery_dedupe_keys=[key],
    ) is False

    assert len(queued) == 1
    assert queued[0][1]["delivery_dedupe_keys"] == [key]
    assert api._last_delivery_outcome() == "outbox_queued"
    assert api._email_dedupe_remaining(key, api.NARRATIVE_PULSE_DEDUPE_SEC) > 0


def test_narrative_pulse_body_and_send_share_one_render_time(monkeypatch):
    rendered_at = datetime(2026, 7, 31, 14, 9, tzinfo=timezone.utc)
    sent = []
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        api,
        "_narrative_pulse_recipients",
        lambda frequency: ["narrative@example.com"] if frequency == "daily" else [],
    )
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda subject, body, **kwargs: sent.append((subject, body, kwargs)) or True,
    )
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    assert api._send_narrative_pulse_email(payload, now=rendered_at.timestamp()) is True

    assert len(sent) == 1
    _subject, body, kwargs = sent[0]
    assert api._mail_timestamp_dual(rendered_at) in body
    assert kwargs["rendered_at"] == rendered_at


def test_narrative_pulse_respects_frequency_recipients(monkeypatch):
    claimed = []
    sent = []

    def fake_claim(key, ttl, now=None):
        claimed.append((key, ttl))
        return True

    def fake_recipients(frequency):
        return ["two@example.com"] if frequency == "twice_daily" else []

    def fake_send(subject, body, *args, **kwargs):
        sent.append((subject, kwargs.get("recipient_emails")))
        return True

    monkeypatch.setattr(api, "_email_dedupe_claim", fake_claim)
    monkeypatch.setattr(api, "_narrative_pulse_recipients", fake_recipients)
    monkeypatch.setattr(api, "_send_email_alert", fake_send)

    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    assert api._send_narrative_pulse_email(payload) is True
    assert len(sent) == 1
    assert "2x taeglich" in sent[0][0]
    assert sent[0][1] == ["two@example.com"]
    assert any(key.startswith("narrative_pulse_twice_daily_") for key, _ttl in claimed)


def test_narrative_pulse_uses_global_daily_fallback_when_subscribers_empty(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "get_email_alert_recipients", lambda *args, **kwargs: [])
    monkeypatch.setitem(api._SECRETS, "GMAIL_USER", "sender@example.com")
    monkeypatch.setitem(api._SECRETS, "GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setitem(api._SECRETS, "ALERT_EMAIL", "owner@example.com")
    monkeypatch.delenv("NARRATIVE_EMAIL_FREQUENCY", raising=False)
    monkeypatch.delenv("NARRATIVE_PULSE_FREQUENCY", raising=False)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *args, **kwargs: True)
    sent = {}

    def fake_send(subject, body, *args, **kwargs):
        sent["subject"] = subject
        sent["recipients"] = kwargs.get("recipient_emails")
        return True

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    assert api._send_narrative_pulse_email(payload) is True
    assert "Taeglich" in sent["subject"]
    assert sent["recipients"] == ["owner@example.com"]


def test_narrative_pulse_status_reports_missing_recipients(monkeypatch):
    monkeypatch.setattr(api, "_narrative_pulse_recipients", lambda frequency: [])
    status = api._narrative_pulse_email_status(now=1_800_000_000)

    assert status["reason"] == "no_recipients"
    assert status["recipient_count"] == 0
    assert all(item["recipient_count"] == 0 for item in status["frequencies"].values())


def test_narrative_pulse_cache_status_reads_wrapped_payload(monkeypatch):
    monkeypatch.setattr(api.os.path, "exists", lambda path: path == api.NARRATIVE_PULSE_CACHE)
    monkeypatch.setattr(
        api,
        "load_cache_file",
        lambda path: ([{"generated_at": "2026-06-05T10:00:00", "all": [{"a": 1}, {"a": 2}]}], "2026-06-05T10:00:00"),
    )

    status = api._narrative_pulse_cache_status()

    assert status["cache_exists"] is True
    assert status["generated_at"] == "2026-06-05T10:00:00"
    assert status["item_count"] == 2
