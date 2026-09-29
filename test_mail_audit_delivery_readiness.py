"""Operator cache diagnostics must not impersonate SMTP delivery evidence."""
import json

import pytest

import api


@pytest.fixture
def ready_status(monkeypatch):
    status = {"configured": True, "recipient_configured": True,
              "startup_cooldown_remaining_seconds": 0}
    monkeypatch.setattr(api, "_email_alert_status", lambda: status)
    # An operator inspection must never enter a transport boundary.
    monkeypatch.setattr(api, "_send_email_alert",
                        lambda *a, **k: pytest.fail("audit cannot send mail"))
    return status


def _passing_cache():
    return {"stock_strategy": {"rows_checked": 1,
                               "alertable_now_count": 1,
                               "suppression_counts": {}}}


def test_passing_cached_candidate_with_no_recipient_cannot_be_mail_ready(ready_status):
    ready_status["recipient_configured"] = False
    result = api._summarize_email_alert_audit(_passing_cache())
    assert result["overall_status"] == "NO_ELIGIBLE_RECIPIENTS"
    assert result["total_alertable_now"] == 1
    assert result["delivery_evaluated"] is False


def test_passing_cache_explicitly_stops_before_final_send_checks(ready_status):
    result = api._summarize_email_alert_audit(_passing_cache())
    assert result["overall_status"] == "PRECHECK_PASSED"
    assert result["scanner_statuses"][0]["status"] == "PRECHECK_PASSED"
    assert result["evidence_scope"] == "cache_precheck"
    assert result["delivery_evaluated"] is False
    assert "alle Gates" not in result["next_step"]


@pytest.mark.parametrize("passing_sibling", [False, True])
def test_failed_audit_is_not_an_empty_successful_scan(ready_status, passing_sibling):
    caches = {"bi_long": {"error": "audit_unavailable"}}
    if passing_sibling:
        caches.update(_passing_cache())
    result = api._summarize_email_alert_audit(caches)
    assert result["overall_status"] == "AUDIT_INCOMPLETE"
    assert result["audit_error_count"] == 1
    assert result["total_alertable_now"] == int(passing_sibling)


def test_biotech_cache_audit_does_not_rebuild_or_write_plans(monkeypatch, tmp_path):
    cache = tmp_path / "biotech.json"
    data = json.dumps({"results": [{"Ticker": "BIOA", "Score": 86}]}).encode()
    cache.write_bytes(data)
    monkeypatch.setattr(api, "_enrich_biotech_alert_trade_levels",
                        lambda: pytest.fail("read audit cannot republish plans"))
    monkeypatch.setattr(api, "_extract_cache_rows_for_alert_audit",
                        lambda *a, **kwargs: json.loads(cache.read_bytes())["results"])
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda *a: a[1])
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *a: {
        "grade": "A", "decision": "WAIT_TRIGGER", "alertable_now": False,
        "suppression_reasons": ["invalid_trade_plan"]})
    result = api._build_alert_audit_for_cache("biotech", str(cache))
    assert result["mail_status"] == "PRECHECK_BLOCKED"
    assert result["rows_checked"] == 1
    assert result["evidence_scope"] == "cache_precheck"
    assert cache.read_bytes() == data


def test_cached_passing_row_has_no_send_now_promise(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_extract_cache_rows_for_alert_audit", lambda *a, **kwargs: [{"Ticker": "AAA"}])
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda *a: a[1])
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *a: {
        "grade": "A", "decision": "TRADE_NOW", "alertable_now": True,
        "suppression_reasons": []})
    result = api._build_alert_audit_for_cache("stock_strategy", str(tmp_path / "missing.json"))
    assert result["mail_status"] == "PRECHECK_PASSED"
    assert result["precheck_passed_count"] == 1
    assert result["delivery_evaluated"] is False
    assert "wuerde jetzt rausgehen" not in result["mail_status_label"]
