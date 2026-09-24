"""Personal crypto reminders cannot silently enter the LONG-only evaluator."""
from copy import deepcopy

import pytest

import api


def crypto_row(**updates):
    row = {
        "Symbol": "TEST", "scanner": "early_movers", "direction": "LONG",
        "trade_action": "LONG_TRIGGER", "entry": 100, "stop_loss": 95,
        "tp1": 110, "tp2": 120,
        "PerpChartSymbol": "TESTUSDT", "PerpChartExchange": "binance",
    }
    row.update(updates)
    return row


def record(**updates):
    row = {
        "id": "personal-crypto", "ticker": "TEST", "asset_type": "crypto",
        "scanner": "early_movers", "condition": "trigger", "mode": "intraday",
        "owner_email": "owner@example.test", "channel": "email_browser",
        "status": "active", "row": crypto_row(), "expires_at": 10000,
    }
    row.update(updates)
    return row


@pytest.fixture
def reminder_env(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_TRADE_REMINDERS_FILE", str(tmp_path / "reminders.json"))
    monkeypatch.setattr(api, "_reminder_now", lambda: 1000.0)
    monkeypatch.setattr(api, "_authenticated_request_identity", lambda _: ("owner@example.test", False))
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: crypto_row())
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {"owner@example.test": {"email_alerts_enabled": True}}})


@pytest.mark.parametrize("updates", [
    {"scanner": "new_listing"}, {"scanner": "crypto_explosion"},
    {"scanner": ""}, {"condition": "continuation"},
    {"row": crypto_row(direction="SHORT")},
    {"row": crypto_row(Direction="SHORT")},
    {"row": crypto_row(trade_setup={"direction": "SHORT"})},
    {"row": crypto_row(trade_action="SHORT_TRIGGER")},
    {"row": crypto_row(trade_decision="WAIT_FOR_CONTINUATION")},
    {"row": crypto_row(trade_health={"decision": "WAIT_FOR_CONTINUATION"})},
    {"row": crypto_row(scanner="new_listing")},
    {"row": crypto_row(source_scanner="new_listing")},
    {"row": crypto_row(scanner_source="crypto_explosion")},
    {"row": crypto_row(direction=None, stop_loss=105, tp1=90)},
])
def test_post_rejects_unsupported_client_capability(reminder_env, updates):
    request = {"ticker": "TEST", "asset_type": "crypto", "scanner": "early_movers", "condition": "trigger"}
    request.update(updates)
    with pytest.raises(api.HTTPException) as rejected:
        api.create_trade_reminder(api.TradeReminderRequest(**request))
    assert rejected.value.status_code == 400
    assert api._load_trade_reminders() == []


@pytest.mark.parametrize("updates", [
    {"direction": "SHORT"}, {"trade_action": "WAIT_FOR_CONTINUATION"},
    {"PerpChartSymbol": None}, {"PerpChartExchange": None},
    {"entry": None}, {"entry": float("nan")}, {"stop_loss": 105},
    {"tp1": 90}, {"data_invalid": True}, {"partial_data": True},
    {"Symbol": "OTHER"}, {"source_scanner": "new_listing"},
])
def test_post_rejects_unsupported_server_row(reminder_env, monkeypatch, updates):
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: crypto_row(**updates))
    with pytest.raises(api.HTTPException) as rejected:
        api.create_trade_reminder(api.TradeReminderRequest(ticker="TEST", row=crypto_row()))
    assert rejected.value.status_code == 400
    assert api._load_trade_reminders() == []


def test_client_source_claim_cannot_replace_server_cache(reminder_env, monkeypatch):
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: None)
    client = crypto_row(crypto_source_verified="early_movers_cache_v1")
    with pytest.raises(api.HTTPException) as rejected:
        api.create_trade_reminder(api.TradeReminderRequest(ticker="TEST", row=client))
    assert rejected.value.status_code == 400
    assert rejected.value.detail == "crypto_reminder_source_unverifiable"


@pytest.mark.parametrize("condition", ["trigger", "retest", "trigger_or_retest"])
def test_supported_post_persists_only_server_owned_plan(reminder_env, condition):
    api.create_trade_reminder(api.TradeReminderRequest(
        ticker="TESTUSDT", condition=condition,
        row=crypto_row(entry=500, stop_loss=400, tp1=600, client_only=True),
    ))
    stored = api._load_trade_reminders()[0]
    assert stored["ticker"] == "TEST"
    assert stored["row"] == crypto_row()
    assert stored["crypto_source_verified"] == "early_movers_cache_v1"


@pytest.mark.parametrize("updates", [
    {"scanner": "new_listing"}, {"scanner": ""},
    {"condition": "continuation"}, {"mode": "structure_1d"},
    {"row": crypto_row(direction="SHORT")},
    {"row": crypto_row(trade_setup={"direction": "SHORT"})},
    {"row": crypto_row(trade_action="WAIT_FOR_CONTINUATION")},
    {"row": crypto_row(scanner="crypto_explosion")},
    {"row": crypto_row(direction=None, stop_loss=105, tp1=90)},
])
def test_unsupported_existing_records_never_evaluate_or_send(reminder_env, monkeypatch, updates):
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda _: pytest.fail("unsupported Long evaluation"))
    monkeypatch.setattr(api, "_evaluate_structure_reminder", lambda _: pytest.fail("crypto cannot become stock reminder"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: pytest.fail("unsupported delivery"))
    reminder = record(**updates)
    assert api._evaluate_trade_reminder(reminder)["triggered"] is False
    reminder.update(status="triggered", email_delivery_status="retry_pending", next_email_attempt_at=900)
    assert api._deliver_trade_reminder_email(reminder, {"triggered": True}, now=1000) is False
    assert reminder["email_delivery_status"] == "unsupported"
    assert "next_email_attempt_at" not in reminder


def test_legacy_unverified_saved_row_is_not_source_proof(reminder_env, monkeypatch):
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: None)
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda _: pytest.fail("legacy client claim"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: pytest.fail("legacy client delivery"))
    reminder = record()
    result = api._evaluate_trade_reminder(reminder)
    assert result == {"triggered": False, "reason": "crypto_reminder_source_unverifiable"}
    assert api._deliver_trade_reminder_email(reminder, {"triggered": True}, now=1000) is False


@pytest.mark.parametrize("condition, matched, expected", [
    ("trigger", ["breakout"], True), ("trigger_or_retest", ["breakout"], True),
    ("retest", ["breakout"], False), ("retest", ["retest_hold"], True),
])
def test_verified_saved_long_keeps_supported_conditions(reminder_env, monkeypatch, condition, matched, expected):
    api.create_trade_reminder(api.TradeReminderRequest(ticker="TEST", condition=condition))
    saved = api._load_trade_reminders()[0]
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: None)
    monkeypatch.setattr(api, "_apply_early_mover_signal_state", lambda *args: None)
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda _: {
        "ok": True, "reason": "5m_trigger", "matched": matched,
        "last_candle_timestamp": 1000,
    })
    assert api._evaluate_trade_reminder(saved)["triggered"] is expected


def test_legacy_triggered_retry_is_blocked_in_worker(reminder_env, monkeypatch):
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: pytest.fail("legacy Short delivery"))
    api._save_trade_reminders([record(
        status="triggered", row=crypto_row(direction="SHORT"),
        email_delivery_status="retry_pending", trigger_result={"triggered": True},
    )])
    api._process_trade_reminders_once()
    assert api._load_trade_reminders()[0]["email_delivery_status"] == "unsupported"


def test_cache_lookup_rejects_ambiguous_symbols(monkeypatch):
    monkeypatch.setattr(api, "load_cache_file", lambda *a, **k: ([crypto_row(), crypto_row()], None))
    assert api._find_early_mover_row("TESTUSDT") is None


def test_cache_lookup_returns_independent_snapshot(monkeypatch):
    row = crypto_row(trade_setup={"direction": "LONG"})
    original = deepcopy(row)
    monkeypatch.setattr(api, "load_cache_file", lambda *a, **k: ([row], None))
    found = api._find_early_mover_row("TESTUSDT")
    found["trade_setup"]["direction"] = "SHORT"
    assert row == original


@pytest.mark.parametrize("container", ["row", "trade_setup", "trade_health"])
@pytest.mark.parametrize("field", [
    "trade_action", "trade_signal", "entry_status", "trade_decision",
    "action", "signal", "decision",
])
@pytest.mark.parametrize("state", ["SHORT_NOW", "WAIT_FOR_CONTINUATION"])
def test_explicit_state_aliases_cannot_be_overridden_by_long_cache(
    reminder_env, monkeypatch, container, field, state,
):
    submitted = crypto_row()
    target = submitted if container == "row" else submitted.setdefault(container, {})
    target[field] = state
    expected_reason = (
        "unsupported_crypto_reminder_direction" if state == "SHORT_NOW"
        else "unsupported_crypto_reminder_condition"
    )
    with pytest.raises(api.HTTPException) as rejected:
        api.create_trade_reminder(api.TradeReminderRequest(ticker="TEST", row=submitted))
    assert rejected.value.status_code == 400
    assert rejected.value.detail == expected_reason
    assert api._load_trade_reminders() == []

    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda _: pytest.fail("unsupported alias evaluation"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: pytest.fail("unsupported alias delivery"))
    reminder = record(row=submitted)
    assert api._evaluate_trade_reminder(reminder) == {"triggered": False, "reason": expected_reason}
    reminder.update(status="triggered", email_delivery_status="retry_pending")
    assert api._deliver_trade_reminder_email(reminder, {"triggered": True}, now=1000) is False
    assert reminder["email_delivery_reason"] == expected_reason


@pytest.mark.parametrize("container", ["row", "trade_setup", "trade_health"])
@pytest.mark.parametrize("field", [
    "trade_action", "trade_signal", "entry_status", "trade_decision",
    "action", "signal", "decision",
])
@pytest.mark.parametrize("state", ["SHORT_NOW", "WAIT_FOR_CONTINUATION"])
def test_server_state_aliases_are_also_unsupported(
    reminder_env, monkeypatch, container, field, state,
):
    cached = crypto_row()
    target = cached if container == "row" else cached.setdefault(container, {})
    target[field] = state
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: cached)
    with pytest.raises(api.HTTPException) as rejected:
        api.create_trade_reminder(api.TradeReminderRequest(ticker="TEST", row=crypto_row()))
    assert rejected.value.status_code == 400
    assert api._load_trade_reminders() == []


@pytest.mark.parametrize("result", [
    {"entry": 100, "stop": 105, "tp1": 90, "tp2": 80},
    {"entry": 100, "stop": 95, "tp1": 110, "tp2": 90},
    {"entry": 100, "stop": float("nan"), "tp1": 110, "tp2": 120},
    {"entry": 100, "stop": 95, "tp1": float("inf"), "tp2": 120},
    {"row": crypto_row(direction="SHORT")},
    {"row": crypto_row(direction=None, entry=100, stop_loss=105, tp1=90)},
    {"row": crypto_row(scanner="new_listing")},
])
def test_delivery_rejects_actual_result_plan_despite_valid_saved_and_cached_long(
    reminder_env, monkeypatch, result,
):
    sends = []
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: sends.append(k) or True)
    reminder = record(status="triggered", email_delivery_status="retry_pending")
    assert api._deliver_trade_reminder_email(reminder, dict(result, triggered=True), now=1000) is False
    assert sends == []
    assert reminder["email_delivery_status"] == "unsupported"
    assert reminder.get("email_attempts", 0) == 0


@pytest.mark.parametrize("container", ["result", "row", "trade_setup", "trade_health"])
@pytest.mark.parametrize("field", [
    "trade_action", "trade_signal", "entry_status", "trade_decision",
    "action", "signal", "decision",
])
@pytest.mark.parametrize("state", ["SHORT_NOW", "WAIT_FOR_CONTINUATION"])
def test_delivery_rejects_actual_result_state_aliases(
    reminder_env, monkeypatch, container, field, state,
):
    result = {"triggered": True, "entry": 100, "stop": 95, "tp1": 110, "tp2": 120}
    if container == "result":
        target = result
    elif container == "row":
        target = result.setdefault("row", crypto_row())
    else:
        target = result.setdefault("row", crypto_row()).setdefault(container, {})
    target[field] = state
    sends = []
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: sends.append(k) or True)
    reminder = record(status="triggered", email_delivery_status="retry_pending")
    assert api._deliver_trade_reminder_email(reminder, result, now=1000) is False
    assert sends == []
    assert reminder["email_delivery_status"] == "unsupported"


def test_supported_delivery_preserves_the_triggered_plan_not_new_cache(
    reminder_env, monkeypatch,
):
    monkeypatch.setattr(api, "_find_early_mover_row", lambda _: crypto_row(entry=200, stop_loss=190, tp1=220, tp2=240))
    bodies = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **k: bodies.append(body) or True)
    reminder = record(status="triggered", email_delivery_status="retry_pending")
    result = {"triggered": True, "entry": 100, "stop": 95, "tp1": 110, "tp2": 120}
    assert api._deliver_trade_reminder_email(reminder, result, now=1000) is True
    assert "$100.00" in bodies[0]
    assert "$200.00" not in bodies[0]
