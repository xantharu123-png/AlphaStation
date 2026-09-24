"""Independent adversarial review of scan-scoped mail evidence; no network."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

import api
from modules import scan_mail_audit as audit
from modules.suppression_telemetry import ALLOWED_SUPPRESSION_REASONS
from test_server_evidence_scan_diagnostics import collector


def test_independent_reason_capture_survives_missing_optional_durable_writer(monkeypatch):
    monkeypatch.setattr(api, "record_suppressions", None)
    with audit.capture(1) as result:
        assert api._record_suppression_counts("stock_strategy", {"score_below_alert_threshold": 1}) == 0
    assert result["reason_occurrences"] == {"score_below_alert_threshold": 1}
    assert result["transport_events"] == {}


def test_concurrent_captures_do_not_merge_or_leak_into_reused_worker():
    gate = Barrier(2)
    def worker(event, count):
        with audit.capture(count) as result:
            for _ in range(count):
                audit.transport("swing_trade", event)
            gate.wait(timeout=5)
            audit.suppressions({"score_below_alert_threshold": count}, ALLOWED_SUPPRESSION_REASONS)
        audit.transport("swing_trade", "accepted")
        with audit.capture(0) as subsequent:
            pass
        return result, subsequent
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, "accepted", 2)
        second = pool.submit(worker, "failed", 3)
        one, empty_one = first.result()
        two, empty_two = second.result()
    assert one["transport_events"] == {"trade_accepted": 2}
    assert two["transport_events"] == {"trade_failed": 3}
    assert one["reason_occurrences"] == {"score_below_alert_threshold": 2}
    assert two["reason_occurrences"] == {"score_below_alert_threshold": 3}
    assert empty_one["transport_events"] == empty_two["transport_events"] == {}


def test_projection_is_fresh_bounded_and_matches_standalone_collector():
    payload = {
        "schema_version": 1, "candidate_rows": 10**9,
        "semantics": "PRIVATE_SENDER@example.invalid",
        "reason_occurrences": {key: 1 for key in ALLOWED_SUPPRESSION_REASONS},
        "transport_events": {key: 2 for key in audit.EVENTS},
    }
    payload["reason_occurrences"]["PRIVATE_SYMBOL"] = 3
    payload["transport_events"]["PRIVATE_SUBJECT"] = 4
    projected = audit.project(payload, ALLOWED_SUPPRESSION_REASONS)
    assert projected == collector._scan_mail_audit_projection(payload)
    assert len(projected["reason_occurrences"]) == len(ALLOWED_SUPPRESSION_REASONS)
    assert set(projected["transport_events"]) == set(audit.EVENTS)
    assert "PRIVATE" not in json.dumps(projected)
    payload["transport_events"]["trade_accepted"] = 200
    assert projected["transport_events"]["trade_accepted"] == 2


def _sender_fixture(monkeypatch, behavior):
    calls = []
    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def quit(self):
            raise RuntimeError("PRIVATE_QUIT")
        def close(self): pass
        def sendmail(self, sender, recipients, body):
            calls.append(tuple(recipients))
            return behavior(len(calls), recipients)
    monkeypatch.setattr(api.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "fake@example.invalid", "GMAIL_APP_PASSWORD": "fake"})
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **k: ["a@example.invalid", "b@example.invalid"])
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(api, "record_suppressions", lambda *a, **k: 1)
    monkeypatch.setattr(api, "is_telegram_configured", lambda: False)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    return calls


def test_partial_accepted_then_uncertain_retry_has_distinct_evidence(monkeypatch):
    def behavior(attempt, recipients):
        if attempt == 1:
            return {"b@example.invalid": (451, b"retry")}
        raise TimeoutError("PRIVATE_DATA_UNKNOWN")
    calls = _sender_fixture(monkeypatch, behavior)
    with audit.capture(1) as result:
        assert api._send_email_alert("Private", "<p>x</p>", bypass_startup_cooldown=True,
                                     mail_class="swing_trade") is True
    assert calls == [("a@example.invalid", "b@example.invalid"), ("b@example.invalid",)]
    assert result["transport_events"] == {"trade_sender_called": 1, "trade_partial_unknown": 1}
    assert "PRIVATE" not in json.dumps(result)
    assert audit.project(result, ALLOWED_SUPPRESSION_REASONS) == collector._scan_mail_audit_projection(result)


def test_successful_send_is_one_message_not_one_event_per_recipient_or_quit_retry(monkeypatch):
    calls = _sender_fixture(monkeypatch, lambda *a: {})
    with audit.capture(10) as result:
        assert api._send_email_alert("Private", "<p>x</p>", bypass_startup_cooldown=True,
                                     mail_class="swing_trade") is True
    assert len(calls) == 1
    assert result["transport_events"] == {"trade_sender_called": 1, "trade_accepted": 1}


@pytest.mark.parametrize("mail_class", ["trade", "swing_trade", "watch"])
def test_definite_refusal_is_never_acceptance_and_only_nontrade_can_queue(monkeypatch, mail_class):
    calls = _sender_fixture(monkeypatch, lambda *a: {
        "a@example.invalid": (550, b"refused"), "b@example.invalid": (550, b"refused"),
    })
    enqueued = []
    monkeypatch.setattr(api, "_mail_outbox", SimpleNamespace(enqueue=lambda *a, **k: enqueued.append(1) or 17))
    with audit.capture(1) as result:
        assert api._send_email_alert("Private", "<p>x</p>", bypass_startup_cooldown=True,
                                     mail_class=mail_class) is False
    kind = "other" if mail_class == "watch" else "trade"
    expected = {kind + "_sender_called": 1, kind + "_failed": 1}
    if kind == "other":
        expected["other_queued"] = 1
    assert result["transport_events"] == expected
    assert len(calls) == 1 and len(enqueued) == (1 if kind == "other" else 0)


def test_unknown_data_outcome_is_not_retried_or_queued(monkeypatch):
    def unknown(*args):
        raise TimeoutError("PRIVATE_DATA")
    calls = _sender_fixture(monkeypatch, unknown)
    enqueued = []
    monkeypatch.setattr(api, "_mail_outbox", SimpleNamespace(enqueue=lambda *a, **k: enqueued.append(1) or 17))
    monkeypatch.setattr(api, "_quarantine_unknown_email_delivery", lambda *a, **k: 1)
    with audit.capture(1) as result:
        assert api._send_email_alert("Private", "<p>x</p>", bypass_startup_cooldown=True,
                                     mail_class="watch") is False
    assert len(calls) == 1 and enqueued == []
    assert result["transport_events"] == {"other_sender_called": 1, "other_unknown": 1}
