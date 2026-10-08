"""Offline regressions for reviewed operator mail-decision reasons."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import api


@pytest.fixture
def isolated_mail_journal(monkeypatch):
    """Keep the projection real while replacing accounts and durable journals."""
    events = []
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", events)
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(api, "load_delivery_acceptance_health", lambda: {
        "status": "ok", "pending_count": 0, "tracker_pending": False,
    })
    monkeypatch.setattr(api, "load_pending_accepted_deliveries", lambda: [])
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **_kwargs: [])
    return events


def _append_decision(events, reason, *, status="skipped"):
    events.append({
        "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "subject": "Private portfolio subject",
        "status": status,
        "reason": reason,
    })


@pytest.mark.parametrize("raw_reason, expected_code", [
    ("no_candidates", "no_candidates"),
    ("final_snapshot_unavailable", "final_snapshot_unavailable"),
    ("final_quote_stale", "final_quote_stale"),
    ("final_quote_stale_after_path", "final_quote_stale_after_path"),
    ("final_quote_stale_at_return", "final_quote_stale_at_return"),
    ("dedupe_claim_not_owned", "dedupe_claim_not_owned"),
    ("tracker_delivery_intent_not_sendable", "tracker_delivery_intent_not_sendable"),
    # Literal non-sendable states emitted by prepare_alert_delivery_intent,
    # plus the sender's fallback when an older contract omits intent_state.
    ("tracker_delivery_intent_missing", "tracker_delivery_intent_missing"),
    ("tracker_delivery_intent_attempted_unknown", "tracker_delivery_intent_attempted_unknown"),
    ("tracker_delivery_intent_accepted_pending", "tracker_delivery_intent_accepted_pending"),
    ("tracker_delivery_intent_active", "tracker_delivery_intent_active"),
    ("tracker_delivery_intent_incomplete_or_inconsistent", "tracker_delivery_intent_incomplete_or_inconsistent"),
    ("tracker_delivery_intent_not_prepared", "tracker_delivery_intent_not_prepared"),
    ("all_strategy_rows_claimed_by_parallel_sender", "all_strategy_rows_claimed_by_parallel_sender"),
])
def test_known_delivery_decision_keeps_its_reviewed_identity(
    isolated_mail_journal, raw_reason, expected_code,
):
    """A missing admin label must not turn a known decision into unknown."""
    _append_decision(isolated_mail_journal, raw_reason)

    result = api._admin_mail_delivery_status()

    reasons = result["recent_decisions"][0]["reasons"]
    assert [reason["code"] for reason in reasons] == [expected_code]
    assert isinstance(reasons[0]["label"], str)
    assert reasons[0]["label"].strip()
    assert reasons[0]["label"] != expected_code
    assert result["smtp_acceptance_is_inbox_delivery"] is False


@pytest.mark.parametrize("raw_reason", [
    "not_no_candidates",
    "dedupe_claim_not_owned_elsewhere",
    "tracker_delivery_intent_not_sendable_unknown",
    "tracker_delivery_intent_active_again",
    "tracker_delivery_intent_accepted_pending_foreign",
    "all_strategy_rows_claimed_by_parallel_sender_retry",
    "not_final_snapshot_fetch_failed_suffix",
    "not_tracker_delivery_attempt_not_owned_suffix",
])
def test_identifier_substrings_are_not_misreported_as_known_decisions(
    isolated_mail_journal, raw_reason,
):
    """Dropping identifier boundaries would fabricate an operator reason."""
    _append_decision(isolated_mail_journal, raw_reason)

    result = api._admin_mail_delivery_status()

    assert [reason["code"] for reason in result["recent_decisions"][0]["reasons"]] == [
        "unclassified_code_reason",
    ]


@pytest.mark.parametrize("raw_reason, expected_code", [
    ("final_quote_stale", "final_quote_stale"),
    ("tracker_delivery_intent_active", "tracker_delivery_intent_active"),
])
def test_known_reason_in_private_error_exports_only_the_reviewed_reason(
    isolated_mail_journal, raw_reason, expected_code,
):
    """Adding a label must not start echoing SMTP errors or private subjects."""
    _append_decision(isolated_mail_journal,
        "SMTPRecipientsRefused owner@example.invalid password=private-secret "
        "https://private-provider.invalid/token/private-secret " + raw_reason,
        status="error",
    )

    result = api._admin_mail_delivery_status()

    assert [reason["code"] for reason in result["recent_decisions"][0]["reasons"]] == [
        expected_code,
    ]
    serialized = json.dumps(result)
    for private_value in (
        "owner@example.invalid", "private-secret", "private-provider.invalid",
        "Private portfolio subject", "SMTPRecipientsRefused",
    ):
        assert private_value not in serialized


@pytest.mark.parametrize("raw_reason", [
    "SMTPRecipientsRefused owner@example.invalid password=private-secret "
    "https://private-provider.invalid/token/private-secret",
    "tracker_delivery_intent_future_status owner@example.invalid password=private-secret "
    "https://private-provider.invalid/token/private-secret",
])
def test_unknown_private_error_stays_unclassified_without_raw_details(
    isolated_mail_journal, raw_reason,
):
    """Unknown errors must retain the safe fallback, never a raw-string label."""
    _append_decision(isolated_mail_journal, raw_reason, status="error")

    result = api._admin_mail_delivery_status()

    assert [reason["code"] for reason in result["recent_decisions"][0]["reasons"]] == [
        "unclassified_code_reason",
    ]
    serialized = json.dumps(result)
    for private_value in (
        "owner@example.invalid", "private-secret", "private-provider.invalid",
        "Private portfolio subject", "SMTPRecipientsRefused",
    ):
        assert private_value not in serialized


@pytest.mark.parametrize("raw_reason, expected_code", [
    # These five raw codes are actually produced by the final snapshot fetch.
    ("final_snapshot_fetch_error", "final_snapshot_fetch_failed"),
    ("final_snapshot_http_403", "final_snapshot_access_denied"),
    ("final_snapshot_http_429", "final_snapshot_rate_limited"),
    ("final_snapshot_http_500", "final_snapshot_http_error"),
    ("final_snapshot_payload_invalid", "final_snapshot_payload_invalid"),
    # Already-normalized identifiers must not lose their reviewed identity.
    ("final_snapshot_access_denied", "final_snapshot_access_denied"),
    ("final_snapshot_fetch_failed", "final_snapshot_fetch_failed"),
    ("final_snapshot_rate_limited", "final_snapshot_rate_limited"),
    ("final_snapshot_http_error", "final_snapshot_http_error"),
    ("final_snapshot_payload_invalid", "final_snapshot_payload_invalid"),
    ("final_snapshot_other", "final_snapshot_other"),
    # The existing mapper deliberately keeps unknown members in a safe family.
    ("final_snapshot_unrecognized_failure", "final_snapshot_other"),
])
def test_final_snapshot_provider_failure_remains_identifiable(
    isolated_mail_journal, raw_reason, expected_code,
):
    """A missing family label must not discard the mapper's safe diagnosis."""
    _append_decision(isolated_mail_journal, raw_reason)

    result = api._admin_mail_delivery_status()

    reasons = result["recent_decisions"][0]["reasons"]
    assert [reason["code"] for reason in reasons] == [expected_code]
    assert reasons[0]["label"].strip()
    assert reasons[0]["label"] != expected_code


@pytest.mark.parametrize("raw_reason, expected_code", [
    ("final_snapshot_http_403 owner@example.invalid password=private-secret "
     "https://private-provider.invalid/auth/private-secret", "final_snapshot_access_denied"),
    ("final_snapshot_other owner@example.invalid password=private-secret "
     "https://private-provider.invalid/auth/private-secret", "final_snapshot_other"),
])
def test_final_snapshot_family_diagnosis_never_exports_private_provider_details(
    isolated_mail_journal, raw_reason, expected_code,
):
    """Family normalization and fixed labels must retain no raw auth details."""
    _append_decision(isolated_mail_journal, raw_reason, status="error")

    result = api._admin_mail_delivery_status()

    assert [reason["code"] for reason in result["recent_decisions"][0]["reasons"]] == [
        expected_code,
    ]
    serialized = json.dumps(result)
    for private_value in (
        "owner@example.invalid", "private-secret", "private-provider.invalid",
        "Private portfolio subject",
    ):
        assert private_value not in serialized


@pytest.mark.parametrize("reason_code", sorted(
    api.ALLOWED_SUPPRESSION_REASONS - {"unclassified_code_reason"},
))
def test_every_allowlisted_reason_retains_its_identity_in_operator_decisions(
    isolated_mail_journal, reason_code,
):
    """Known identifiers need not have custom prose to remain diagnosable.

    The allowlist supplies inputs only. The independent behavior contract is
    exact identity preservation, not any production normalization or label.
    """
    _append_decision(isolated_mail_journal, reason_code)

    result = api._admin_mail_delivery_status()

    reasons = result["recent_decisions"][0]["reasons"]
    assert [reason["code"] for reason in reasons] == [reason_code]
    assert isinstance(reasons[0]["label"], str)
    assert reasons[0]["label"].strip()
    assert reasons[0]["label"] != reason_code


def test_mixed_summary_retains_known_reasons_without_custom_labels(isolated_mail_journal):
    """One labelled match must not hide another known reason in the same event."""
    _append_decision(isolated_mail_journal,
        "score_below_alert_threshold:2, final_snapshot_fetch_failed:1",
    )

    result = api._admin_mail_delivery_status()

    reasons = result["recent_decisions"][0]["reasons"]
    assert len(reasons) == 2
    assert {reason["code"] for reason in reasons} == {
        "score_below_alert_threshold", "final_snapshot_fetch_failed",
    }
