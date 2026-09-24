"""No network: isolated per-run reasons and actual transport-boundary events."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import api
from modules import scan_mail_audit as audit
from modules.suppression_telemetry import ALLOWED_SUPPRESSION_REASONS
from test_server_evidence_scan_diagnostics import collector
from test_stock_strategy_sweep_isolation import _mock_sweep, _row, STRATEGIES


def test_scope_restored_after_nested_exception_and_thread_isolated():
    with audit.capture(5) as outer:
        audit.transport("swing_trade", "accepted")
        with pytest.raises(ValueError):
            with audit.capture(1) as inner:
                audit.transport("watch", "queued")
                raise ValueError("private")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(audit.transport, "trade", "accepted").result()
        audit.transport("trade", "sender_called")
    audit.transport("trade", "accepted")
    assert outer["transport_events"] == {"trade_accepted": 1, "trade_sender_called": 1}
    assert inner["transport_events"] == {"other_queued": 1}


@pytest.mark.parametrize("value", [None, {}, True, -1, 1.5, "12", float("nan"), 10**9+1])
def test_counts_do_not_coerce_and_private_keys_never_survive(value):
    raw = {"schema_version": 1, "candidate_rows": value, "private": "owner@example.invalid",
           "reason_occurrences": {"score_below_alert_threshold": value, "PRIVATE_SYMBOL": 4},
           "transport_events": {"trade_accepted": value, "PRIVATE_SUBJECT": 3}}
    result = audit.project(raw, ALLOWED_SUPPRESSION_REASONS)
    assert result == collector._scan_mail_audit_projection(raw)
    assert "candidate_rows" not in result
    assert result["reason_occurrences"] == result["transport_events"] == {}
    assert "PRIVATE" not in json.dumps(result)


def test_reason_counts_exist_even_if_durable_telemetry_write_fails(monkeypatch):
    monkeypatch.setattr(api, "record_suppressions", lambda *a, **k: (_ for _ in ()).throw(OSError("private")))
    with audit.capture(2) as evidence:
        assert api._record_suppression_counts("stock_strategy", {"score_below_alert_threshold": 2, "grade_below_alert_threshold": 1}) == 0
    assert evidence["reason_occurrences"] == {"score_below_alert_threshold": 2, "grade_below_alert_threshold": 1}
    assert evidence["transport_events"] == {}  # 3 reasons are not 3 mails.


def test_sender_guard_is_not_smtp_acceptance(monkeypatch):
    monkeypatch.setattr(api, "_SECRETS", {})
    monkeypatch.setattr(api, "record_suppressions", lambda *a, **k: 1)
    with audit.capture(1) as evidence:
        assert api._send_email_alert("Private", "<p>x</p>", bypass_startup_cooldown=True) is False
    assert evidence["transport_events"] == {"trade_sender_called": 1}
    assert evidence["reason_occurrences"] == {"missing_gmail_config": 1}


@pytest.mark.parametrize("outcome,expected", [("accept", "trade_accepted"), ("partial", "trade_partial"), ("unknown", "trade_unknown")])
def test_real_sender_boundary_counts_without_real_smtp(monkeypatch, outcome, expected):
    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def quit(self): pass
        def sendmail(self, sender, recipients, body):
            if outcome == "unknown":
                raise TimeoutError("PRIVATE_TRANSPORT")
            return {"b@example.invalid": (550, b"no")} if outcome == "partial" else {}
    monkeypatch.setattr(api.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "fake@example.invalid", "GMAIL_APP_PASSWORD": "fake"})
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **k: ["a@example.invalid", "b@example.invalid"])
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(api, "record_suppressions", lambda *a, **k: 1)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    with audit.capture(1) as evidence:
        api._send_email_alert("Fake", "<p>x</p>", bypass_startup_cooldown=True, mail_class="swing_trade")
    assert evidence["transport_events"] == {"trade_sender_called": 1, expected: 1}


@pytest.mark.parametrize("fail", [False, True])
def test_sweep_persists_scoped_evidence_even_on_guard_exception(monkeypatch, tmp_path, fail):
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    monkeypatch.setattr(api, "_AUTO_STOCK_ALERT_STRATEGIES", list(STRATEGIES))
    cache, _, _ = _mock_sweep(monkeypatch, tmp_path, {STRATEGIES[0]: [_row("PRIVATE_SYMBOL")]})
    def mail(*args):
        audit.suppressions({"score_below_alert_threshold": 1}, ALLOWED_SUPPRESSION_REASONS)
        if fail:
            raise RuntimeError("PRIVATE_ERROR")
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", mail)
    if fail:
        with pytest.raises(Exception):
            api._stock_strategy_alert_sweep_wrapper()
    else:
        api._stock_strategy_alert_sweep_wrapper()
    path = tmp_path / "stock_strategy_sweep_attempt.json"
    saved = json.loads(path.read_text())
    evidence = saved["diagnostics"]["mail_audit"]
    assert evidence["candidate_rows"] == 1
    assert evidence["reason_occurrences"] == {"score_below_alert_threshold": 1}
    assert evidence["transport_events"] == {}
    projected = collector.safe_strategy_attempt_summary(path, "stock_strategy_sweep")
    assert projected["mail_audit"] == evidence
    assert "PRIVATE" not in json.dumps(projected)


def test_bi_new_error_categories_export_privately_without_payload(tmp_path):
    path = tmp_path / "bi_scan_progress_short.json"
    path.write_text(json.dumps({"status": "error", "diagnostics": {
        "excluded_uncompleted_bars": 3,
        "data_error_value_classes": {"zero_price": 1, "PRIVATE_OHLC": 1},
        "data_error_positions": {"last": 1, "PRIVATE_TIME": 1},
    }}))
    result = collector.safe_cache_summary(path)
    assert result["numeric_diagnostics"]["excluded_uncompleted_bars"] == 3
    assert result["data_error_value_classes"] == {"zero_price": 1, "_omitted_categories": 1}
    assert result["data_error_positions"] == {"last": 1, "_omitted_categories": 1}
    assert "PRIVATE" not in json.dumps(result)
