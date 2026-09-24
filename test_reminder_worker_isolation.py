"""Offline per-owner isolation without relaxing durable delivery ownership."""
from copy import deepcopy

import pytest

import api
from test_email_alert_audit import _reminder_early_mover_row


def record(identity="healthy", **updates):
    row = {
        "id": identity, "ticker": "TEST", "asset_type": "stock", "status": "active",
        "owner_email": "owner@example.test", "channel": "email_browser",
        "expires_at": 2000.0, "last_checked_at": 0,
        "row": {"direction": "LONG", "entry": 100, "stop_loss": 95, "tp1": 110, "tp2": 120},
    }
    row.update(updates)
    return row


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_TRADE_REMINDERS_FILE", str(tmp_path / "reminders.json"))
    monkeypatch.setattr(api, "_reminder_now", lambda: 1000.0)
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {"owner@example.test": {"email_alerts_enabled": True}}})
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: pytest.fail("unexpected test transport"))


@pytest.mark.parametrize("field", ["expires_at", "last_checked_at"])
@pytest.mark.parametrize("value", ["malformed", float("nan"), float("inf"), True, None])
def test_bad_active_timing_is_invalidated_without_starving_later_owner(env, monkeypatch, field, value):
    api._save_trade_reminders([record("broken", **{field: value}), record()])
    evaluated = []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: evaluated.append(row["id"]) or {"triggered": False})
    api._process_trade_reminders_once()
    assert evaluated == ["healthy"]
    broken = api._load_trade_reminders()[0]
    assert broken["status"] == "invalidated"
    assert broken["invalidation_reason"] == "reminder_metadata_invalid"


@pytest.mark.parametrize("field, value", [
    ("email_attempts", "malformed"), ("email_attempts", float("nan")),
    ("email_attempts", -1), ("email_attempts", 1.5),
    ("next_email_attempt_at", "malformed"), ("next_email_attempt_at", float("inf")),
])
def test_bad_retry_metadata_fails_one_delivery_without_starving_later_owner(env, monkeypatch, field, value):
    api._save_trade_reminders([record("broken", status="triggered", **{field: value}), record()])
    evaluated = []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: evaluated.append(row["id"]) or {"triggered": False})
    api._process_trade_reminders_once()
    assert evaluated == ["healthy"]
    broken = api._load_trade_reminders()[0]
    assert broken["email_delivery_status"] == "failed"
    assert broken["email_delivery_reason"] == "reminder_delivery_metadata_invalid"


@pytest.mark.parametrize("failure", [ValueError("private parse"), RuntimeError("private failure"), OSError("private provider")])
def test_delivery_preflight_failure_is_bounded_and_later_owner_is_evaluated(env, monkeypatch, failure):
    api._save_trade_reminders([record("broken", status="triggered"), record()])
    evaluated = []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: evaluated.append(row["id"]) or {"triggered": False})
    def failed_users():
        raise failure
    monkeypatch.setattr(api, "_load_users", failed_users)
    api._process_trade_reminders_once()
    assert evaluated == ["healthy"]
    broken = api._load_trade_reminders()[0]
    assert broken["email_delivery_status"] == "retry_pending"
    assert broken["email_attempts"] == 1
    assert broken["next_email_attempt_at"] == 1060
    api._process_trade_reminders_once()
    assert api._load_trade_reminders()[0]["email_attempts"] == 1
    assert "private" not in str(broken)


@pytest.mark.parametrize("failure", [OSError("disk"), ValueError("serialization"), RuntimeError("write hook")])
def test_any_delivery_store_write_failure_aborts_before_later_owner(env, monkeypatch, failure):
    api._save_trade_reminders([record("broken", status="triggered"), record()])
    evaluated = []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: evaluated.append(row["id"]) or {"triggered": False})
    def failed_save(rows):
        raise failure
    monkeypatch.setattr(api, "_save_trade_reminders", failed_save)
    with pytest.raises(api._TradeReminderPersistenceError):
        api._process_trade_reminders_once()
    assert evaluated == []


def test_exception_after_inflight_claim_is_uncertain_and_never_retried(env, monkeypatch):
    api._save_trade_reminders([record("broken", status="triggered"), record()])
    evaluated, sends = [], []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: evaluated.append(row["id"]) or {"triggered": False})
    def unknown_transport(*args, **kwargs):
        sends.append(1)
        raise RuntimeError("unclassified transport outcome")
    monkeypatch.setattr(api, "_send_email_alert", unknown_transport)
    api._process_trade_reminders_once()
    assert evaluated == ["healthy"]
    broken = api._load_trade_reminders()[0]
    assert broken["email_delivery_status"] == "uncertain_manual_reconciliation"
    assert broken["email_delivery_manual_reconciliation_required"] is True
    api._process_trade_reminders_once()
    assert sends == [1]


@pytest.mark.parametrize("state", ["attempt_in_flight", "uncertain", "uncertain_manual_reconciliation", "outbox_owned"])
def test_bad_counter_never_reopens_terminal_uncertainty_or_outbox_ownership(env, state):
    reminder = record(status="triggered", email_delivery_status=state, email_attempts="legacy")
    api._save_trade_reminders([reminder])
    api._process_trade_reminders_once()
    stored = api._load_trade_reminders()[0]
    assert stored["email_delivery_status"] == (
        "outbox_owned" if state == "outbox_owned" else "uncertain_manual_reconciliation")


def test_known_disabled_orphan_is_retained_unchanged(env):
    orphan = record(status="triggered", owner_email=None, email_delivery_status="disabled", email_attempts="legacy")
    api._save_trade_reminders([orphan])
    api._process_trade_reminders_once()
    assert api._load_trade_reminders() == [orphan]


@pytest.mark.parametrize("matched, expected", [(["vwap_reclaim"], False), (["breakout"], True), (["retest_hold"], True)])
def test_crypto_reminder_respects_final_retest_execution_decision(env, monkeypatch, matched, expected):
    row = _reminder_early_mover_row("TEST")
    row["trade_action"] = row["trade_setup"]["trade_action"] = "WAIT_FOR_RETEST"
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: deepcopy(row))
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda _: {
        "ok": True, "reason": "adaptive_5m_" + matched[0], "timeframe": "5m",
        "matched": matched, "last_close": 1.26, "execution_score": 100,
    })
    reminder = record(asset_type="crypto", scanner="early_movers", condition="trigger", row=row)
    result = api._evaluate_trade_reminder(reminder)
    assert result["triggered"] is expected


@pytest.mark.parametrize("flag", ["execution_trigger_ok", "alertable_crypto"])
def test_legacy_triggered_crypto_result_with_rejected_execution_never_sends(env, monkeypatch, flag):
    row = _reminder_early_mover_row("TEST")
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: deepcopy(row))
    reminder = record(status="triggered", asset_type="crypto", scanner="early_movers", row=row)
    assert api._deliver_trade_reminder_email(reminder, {"row": dict(row, **{flag: False})}, now=1000) is False
    assert reminder["email_delivery_reason"] == "crypto_reminder_execution_not_ready"


@pytest.mark.parametrize("state", ["OUTBOX_OWNED", " OutBox_Owned ", " FAILED ",
                                    " UnCeRtAiN ", " ATTEMPT_IN_FLIGHT ", " SENT "])
def test_terminal_status_case_and_malformed_retry_never_reopens_or_starves(env, monkeypatch, state):
    terminal = record("terminal", status="triggered", email_delivery_status=state,
                      email_attempts="legacy", next_email_attempt_at="malformed")
    api._save_trade_reminders([terminal, record()])
    evaluated = []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: evaluated.append(row["id"]) or {"triggered": False})
    api._process_trade_reminders_once()
    assert evaluated == ["healthy"]
    stored = api._load_trade_reminders()[0]
    expected = "uncertain_manual_reconciliation" if state.strip().lower() in {
        "uncertain", "attempt_in_flight"} else state.strip().lower()
    assert stored["email_delivery_status"].strip().lower() == expected
    assert "email_sent_at" not in stored


@pytest.mark.parametrize("state", [" SENT ", " OUTBOX_OWNED ", " FAILED ",
                                    " UnCeRtAiN ", " ATTEMPT_IN_FLIGHT ",
                                    " UNCERTAIN_MANUAL_RECONCILIATION "])
def test_persisted_terminal_status_beats_stale_retry_record_without_smtp(env, state):
    persisted = record(status="triggered", email_delivery_status=state)
    api._save_trade_reminders([persisted])
    stale = record(status="triggered", email_delivery_status="retry_pending")
    api._deliver_trade_reminder_email(stale, {}, now=1000)
    assert stale["email_delivery_status"].strip().lower() == state.strip().lower()
    assert "email_sent_at" not in stale
    assert api._load_trade_reminders() == [persisted]


def test_terminal_sent_without_timestamp_or_existing_id_never_resends(env):
    api._save_trade_reminders([])
    reminder = record("unpersisted", status="triggered", email_delivery_status=" SENT ")
    api._attempt_trade_reminder_delivery(reminder, {}, 1000)
    assert "email_sent_at" not in reminder
    assert api._load_trade_reminders() == []
