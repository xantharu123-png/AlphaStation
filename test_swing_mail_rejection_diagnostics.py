"""Keep final Swing-mail failures explainable without leaking private details."""

import json
from datetime import datetime, timezone

import pytest

import api
from modules import suppression_telemetry as telemetry


REASONS = (
    "swing_mode_not_allowed_for_scanner",
    "swing_daily_reference_invalid_or_stale",
    "swing_trade_plan_invalid",
    "swing_reference_price_mismatch",
    "swing_reference_outside_plan",
    "swing_delayed_price_or_path_unconfirmed",
    "swing_delayed_rr_insufficient",
    "swing_delayed_entry_too_extended",
    "no_mail_adjacent_revalidated_rows",
)

CLOSED_REASONS = (
    "us_market_closed",
    "stock_market_closed_before_strategy_mail",
    "stock_market_closed_before_crash_mail",
    "stock_market_closed_before_bear_mail",
)


@pytest.fixture
def operator_snapshot(monkeypatch):
    monkeypatch.setattr(api, "_email_pipeline_summary", lambda **kwargs: {
        "sent": 0, "skipped": 1, "errors": 0,
        "outbox": {"available": True, "pending": 0},
    })
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **kwargs: [])

    def inspect(raw):
        monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "skipped", "subject": "PRIVATE SUBJECT", "reason": raw,
        }])
        return api._admin_mail_delivery_status()

    return inspect


@pytest.mark.parametrize("reason", REASONS)
@pytest.mark.parametrize("prefix", ("mail_adjacent_stock_revalidation:", "final_stock_revalidation:"))
def test_final_swing_reason_survives_operator_redaction(operator_snapshot, reason, prefix):
    result = operator_snapshot(prefix + reason + " owner@example.invalid password=private-secret")
    displayed = result["recent_decisions"][0]["reasons"]
    assert [item["code"] for item in displayed] == [reason]
    assert displayed[0]["label"] != "Weiterer Versandgrund; im Betreiberprotokoll pruefen"
    serialized = json.dumps(result)
    assert "owner@example.invalid" not in serialized
    assert "private-secret" not in serialized
    assert "PRIVATE SUBJECT" not in serialized


@pytest.mark.parametrize("reason", REASONS)
def test_final_swing_rejection_is_persisted_as_its_reviewed_code(tmp_path, reason):
    database = str(tmp_path / "suppression.sqlite")
    stable = api._stable_suppression_reason(reason)
    recorded = telemetry.record_suppressions(
        "stock_strategy", {stable: 1}, code_revision="1234567",
        observed_at=1_800_000_000.0, db_path=database,
    )
    assert recorded == 1
    snapshot = telemetry.load_suppression_summary(now=1_800_000_001.0, db_path=database)
    assert [(row["reason"], row["count"]) for row in snapshot["top_reasons"]] == [(reason, 1)]


@pytest.mark.parametrize("reason", CLOSED_REASONS)
@pytest.mark.parametrize("suffix", (
    "outside_regular_session",
    "US market closed (17:00 ET)",
    "US market-session check failed: provider unavailable",
))
def test_market_guard_preserves_its_branch_without_exposing_raw_detail(operator_snapshot, reason, suffix):
    raw = reason + ":" + suffix
    assert api._stable_suppression_reason(raw) == reason
    displayed = operator_snapshot(raw)["recent_decisions"][0]["reasons"]
    assert [item["code"] for item in displayed] == [reason]
    assert suffix not in json.dumps(displayed)


@pytest.mark.parametrize("reason", CLOSED_REASONS)
def test_unknown_session_is_not_described_as_a_confirmed_market_closure(operator_snapshot, reason):
    raw = reason + ":US market-session check failed: provider unavailable"
    displayed = operator_snapshot(raw)["recent_decisions"][0]["reasons"]
    assert [item["code"] for item in displayed] == [reason]
    assert "geschlossen" not in displayed[0]["label"].lower()


@pytest.mark.parametrize("raw", (
    "swing_delayed_price_or_path_unconfirmed_extra",
    "private_swing_daily_reference_invalid_or_stale",
    "provider:user@example.invalid password=private-secret",
))
def test_unreviewed_values_stay_private_and_unknown(operator_snapshot, raw):
    result = operator_snapshot(raw)
    assert [item["code"] for item in result["recent_decisions"][0]["reasons"]] == ["unclassified_code_reason"]
    assert raw not in json.dumps(result)
