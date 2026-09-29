"""An unavailable tracker is not evidence of an empty activation backlog."""
from types import SimpleNamespace

import api
from test_admin_mail_delivery_status import audit_environment

_REAL_PIPELINE_SUMMARY = api._email_pipeline_summary


def _unavailable_tracker(monkeypatch):
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "load_delivery_acceptance_health", lambda **kwargs: {
        "status": "error", "tracker_pending": True, "pending_count": 0,
        "last_error": "OperationalError: diagnostic database unavailable",
    })
    monkeypatch.setattr(api, "load_pending_accepted_deliveries", lambda **kwargs: [])
    monkeypatch.setattr(api, "_mail_outbox", SimpleNamespace(
        readonly_stats=lambda: {"enabled": True, "available": True,
            "tracker_acceptance_available": True, "tracker_acceptance_pending_count": 0},
        load_tracker_acceptance_pending_readonly=lambda: [],
    ))
    return api._email_pipeline_summary(public=True)["tracker_acceptance"]


def test_unavailable_tracker_retains_public_error_boundary(monkeypatch):
    status = _unavailable_tracker(monkeypatch)
    assert status["status"] == "error"
    assert status["available"] is False
    assert status["has_error"] is True


def test_unavailable_tracker_must_not_report_known_empty_backlog(monkeypatch):
    status = _unavailable_tracker(monkeypatch)
    assert status["tracker_pending"] is True
    assert status["pending_count"] is None
    assert status["known_pending_count"] == 0


def test_available_empty_tracker_still_reports_known_zero(monkeypatch):
    _unavailable_tracker(monkeypatch)
    monkeypatch.setattr(api, "load_delivery_acceptance_health", lambda **kwargs: {
        "status": "ok", "tracker_pending": False, "pending_count": 0,
        "reconciled_count": 0, "legacy_open_cohort_unknown_count": 0,
    })
    status = api._email_pipeline_summary(public=True)["tracker_acceptance"]
    assert status["available"] is True
    assert status["tracker_pending"] is False
    assert status["pending_count"] == 0


def test_unknown_tracker_preserves_known_fallback_minimum(monkeypatch):
    _unavailable_tracker(monkeypatch)
    monkeypatch.setattr(api._mail_outbox, "load_tracker_acceptance_pending_readonly", lambda: [
        {"intent_key": "known-1"}, {"intent_key": "known-2"},
    ])
    status = api._email_pipeline_summary(public=True)["tracker_acceptance"]
    assert status["pending_count"] is None
    assert status["known_pending_count"] == 2
    assert status["fallback_journal_pending_count"] == 2
    assert status["tracker_journal_pending_count"] is None


def test_unknown_status_public_projector_does_not_claim_available_zero():
    status = api._public_tracker_acceptance_status({})
    assert status["status"] == "unavailable"
    assert status["available"] is False
    assert status["pending_count"] is None
    assert status["tracker_pending"] is True


def test_complete_admin_api_payload_preserves_null_backlog(audit_environment):
    audit_environment.setattr(api, "_email_pipeline_summary", _REAL_PIPELINE_SUMMARY)
    _unavailable_tracker(audit_environment)
    response = api.get_email_alert_audit("Bearer admin")
    status = response["delivery"]["pipeline"]["tracker_acceptance"]
    assert status["status"] == "error"
    assert status["available"] is False
    assert status["pending_count"] is None
    assert status["tracker_pending"] is True
