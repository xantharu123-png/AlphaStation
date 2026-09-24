"""Offline reminder tests: no provider or SMTP calls are allowed."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from modules import scanner_reminders as reminders


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def bar(day, op, high, low, close, direction="LONG"):
    if direction == "SHORT":
        op, high, low, close = 200 - op, 200 - low, 200 - high, 200 - close
    return {"date": day, "o": op, "h": high, "l": low, "c": close, "v": 1000}


def record(direction="LONG", condition="retest"):
    return {"id": "daily-one", "ticker": "XYZ", "asset_type": "stock", "mode": "structure_1d",
            "condition": condition, "created_at_epoch": epoch("2026-09-15T22:00:00Z"),
            "zone": {"zone_id": "frozen-zone", "lower": 99, "upper": 101, "direction": direction,
                     "confirmed_at": "2026-09-14T20:00:00Z"}}


def path(direction="LONG"):
    return [bar("2026-09-14", 99, 100, 98, 99, direction),
            bar("2026-09-15", 100, 105, 99.5, 104, direction),
            bar("2026-09-16", 104, 105, 100, 103, direction)]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_daily_retest_requires_prior_break_then_touch_and_hold(direction):
    result = reminders.evaluate(record(direction), path(direction), now=epoch("2026-09-16T21:00:00Z"))
    assert result["triggered"] is True
    assert result["reason"] == "daily_retest_confirmed"
    assert result["is_trade_signal"] is False
    assert result["break_closed_at"] < result["candle_closed_at"]
    assert result["notification_kind"] == "personal_structure_update"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_near_zone_without_touch_is_not_retest(direction):
    bars = path(direction)
    bars[-1] = bar("2026-09-16", 104, 105, 102, 103, direction)
    assert not reminders.evaluate(record(direction), bars, now=epoch("2026-09-16T21:00:00Z"))["triggered"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_breakout_and_touch_in_same_bar_is_not_retest(direction):
    bars = path(direction)
    bars[1] = bar("2026-09-15", 99, 100, 98, 99, direction)
    assert not reminders.evaluate(record(direction), bars, now=epoch("2026-09-16T21:00:00Z"))["triggered"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_touch_without_closed_hold_is_not_retest(direction):
    bars = path(direction)
    bars[-1] = bar("2026-09-16", 104, 105, 99, 100, direction)
    assert not reminders.evaluate(record(direction), bars, now=epoch("2026-09-16T21:00:00Z"))["triggered"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_invalidated_after_activation_is_terminal_not_signal(direction):
    bars = path(direction) + [bar("2026-09-17", 100, 101, 97, 98, direction)]
    result = reminders.evaluate(record(direction), bars, now=epoch("2026-09-17T21:00:00Z"))
    assert result["invalidated"] is True
    assert result["triggered"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_new_daily_breakout_not_retest_can_notify(direction):
    reminder = record(direction, "trigger")
    reminder["created_at_epoch"] = epoch("2026-09-15T19:00:00Z")
    result = reminders.evaluate(reminder, path(direction)[:2], now=epoch("2026-09-15T21:00:00Z"))
    assert result["reason"] == "daily_breakout_confirmed"


def test_old_event_and_unfinished_candle_cannot_trigger():
    reminder = record()
    reminder["created_at_epoch"] = epoch("2026-09-16T20:30:00Z")
    assert not reminders.evaluate(reminder, path(), now=epoch("2026-09-16T21:00:00Z"))["triggered"]
    assert not reminders.evaluate(record(), path(), now=epoch("2026-09-16T20:10:00Z"))["triggered"]


def test_weekend_and_starter_delay_do_not_add_execution_session_gate():
    bars = path()[:2] + [bar("2026-09-16", 104, 106, 102, 104),
                       bar("2026-09-17", 104, 106, 102, 104),
                       bar("2026-09-18", 104, 105, 100, 103)]
    result = reminders.evaluate(record(), bars, now=epoch("2026-09-19T10:00:00Z"))
    assert result["triggered"] is True
    assert result["candle_closed_at"].startswith("2026-09-18")


@pytest.mark.parametrize("damage", ["nan", "missing_time", "duplicate", "bad_ohlc", "stale"])
def test_invalid_or_stale_provider_data_cannot_trigger(damage):
    bars = path()
    if damage == "nan": bars[-1]["h"] = float("nan")
    elif damage == "missing_time": bars[-1].pop("date")
    elif damage == "duplicate": bars.append(deepcopy(bars[-1]))
    elif damage == "bad_ohlc": bars[-1]["l"] = 200
    elif damage == "stale": bars = bars[:2]
    assert not reminders.evaluate(record(), bars, now=epoch("2026-09-16T21:00:00Z"))["triggered"]


def server_row():
    return {"Ticker": "XYZ", "Signal_Direction": "LONG", "level_structure": {
        "model": "causal_level_zones_v1", "symbol": "XYZ", "asset_class": "stock", "horizon": "swing",
        "as_of": "2026-09-16T20:00:00Z", "current_price": 104, "completed_bar_counts": {"1D": 100},
        "zones": [{"zone_id": "server-zone", "lower": 99, "upper": 101, "origin_roles": ["resistance"],
                   "confirmed_at": "2026-09-14T20:00:00Z", "evidence": [{"timeframe": "1D"}]}]}}


@pytest.mark.parametrize("damage", ["model", "symbol", "age", "future", "projection", "geometry", "no_evidence"])
def test_zone_anchor_rejects_unusable_server_snapshot(damage):
    row = server_row(); snapshot = row["level_structure"]; zone = snapshot["zones"][0]
    if damage == "model": snapshot["model"] = "estimated"
    elif damage == "symbol": snapshot["symbol"] = "OTHER"
    elif damage == "age": snapshot["as_of"] = "2026-09-15T20:00:00Z"
    elif damage == "future": snapshot["as_of"] = "2026-09-17T20:00:00Z"
    elif damage == "projection": zone["projection_only"] = True
    elif damage == "geometry": zone["lower"] = 105
    elif damage == "no_evidence": zone["evidence"] = []
    with pytest.raises(ValueError):
        reminders.anchor_from_row(row, ticker="XYZ", direction="LONG", condition="retest", now=epoch("2026-09-16T21:00:00Z"))


def test_store_migrates_once_without_deleting_backup(tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "data" / "new.json"
    reminders.save_records(old, [record()])
    assert reminders.load_records(new, legacy_path=old) == [record()]
    assert old.exists()
    reminders.save_records(old, [])
    assert reminders.load_records(new, legacy_path=old) == [record()]


def test_store_corruption_is_not_silently_treated_as_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not":"list"}')
    with pytest.raises(ValueError): reminders.load_records(bad)


def api_fixture(monkeypatch, tmp_path):
    import api
    monkeypatch.setattr(api, "_TRADE_REMINDERS_FILE", str(tmp_path / "records.json"))
    monkeypatch.setattr(api, "_authenticated_request_identity", lambda auth: ("user@example.test", False))
    monkeypatch.setattr(api, "_reminder_now", lambda: epoch("2026-09-16T21:00:00Z"))
    monkeypatch.setattr(api, "_structure_reminder_server_row", lambda *args: server_row())
    return api


def request(api, condition="retest", **overrides):
    kwargs = dict(ticker="XYZ", asset_type="stock", scanner="Momentum Breakout Long", condition=condition,
                  duration_hours=24 * 14, row={"reminder_mode": "structure_1d", "direction": "LONG", "entry": 0.01,
                                             "reminder_zone_id": "server-zone", "level_structure": {"fabricated": True}})
    kwargs.update(overrides)
    return api.TradeReminderRequest(**kwargs)


def test_api_ignores_client_levels_and_keeps_distinct_conditions(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    first = api.create_trade_reminder(request(api))["reminder"]
    repeated = api.create_trade_reminder(request(api))["reminder"]
    other = api.create_trade_reminder(request(api, "trigger"))["reminder"]
    assert first["id"] == repeated["id"] != other["id"]
    assert first["zone"]["upper"] == 101
    assert first["remaining_seconds"] == 24 * 14 * 3600
    assert "entry" not in api._load_trade_reminders()[0]["row"]
    assert len([r for r in api._load_trade_reminders() if r["status"] == "active"]) == 2


def test_api_save_failure_never_reports_active(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_save_trade_reminders", lambda rows: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError): api.create_trade_reminder(request(api))


def test_structure_notice_is_personal_info_and_never_calls_trade_tracker(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    reminder = api._load_trade_reminders()[0]
    calls = []
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {"user@example.test": {"email_alerts_enabled": True}}})
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    result = {"last_close": 103, "reason": "daily_retest_confirmed", "candle_closed_at": "2026-09-17T20:00:00Z"}
    assert api._deliver_trade_reminder_email(reminder, result)
    assert api._deliver_trade_reminder_email(reminder, result)
    assert len(calls) == 1
    assert calls[0][1]["mail_class"] == "info"
    assert calls[0][1]["tracking_scope"] == "personal"
    assert calls[0][1]["recipient_emails"] == ["user@example.test"]
    assert "kein neues" in calls[0][0][1]


def test_invalidated_records_stop_without_delivery(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: {"triggered": False, "invalidated": True, "reason": "daily_structure_invalidated"})
    monkeypatch.setattr(api, "_deliver_trade_reminder_email", lambda *args: pytest.fail("no delivery for invalidation"))
    api._process_trade_reminders_once()
    assert api._load_trade_reminders()[0]["status"] == "invalidated"


def test_structure_dispatch_never_uses_intraday_execution_evaluator(monkeypatch):
    import api
    monkeypatch.setattr(api, "_evaluate_stock_reminder", lambda *args: pytest.fail("5m forbidden"))
    monkeypatch.setattr(api, "_evaluate_structure_reminder", lambda *args: {"reason": "daily_only"})
    assert api._evaluate_trade_reminder(record()) == {"reason": "daily_only"}


def test_server_cache_lookup_binds_direction_identity_and_usable_data(monkeypatch):
    import api
    row = server_row()
    monkeypatch.setattr(api, "load_cache_file", lambda *args: ([row], "2026-09-16T20:00:00Z"))
    monkeypatch.setattr(api, "load_cache_metadata", lambda *args: {"cache_version": api.STOCK_STRATEGY_CACHE_VERSION})
    assert api._structure_reminder_server_row("XYZ", "Compression Breakout", "LONG")["Ticker"] == "XYZ"
    with pytest.raises(ValueError): api._structure_reminder_server_row("XYZ", "Compression Breakout", "SHORT")
    with pytest.raises(ValueError): api._structure_reminder_server_row("MISSING", "Compression Breakout", "LONG")
    row["market_data_valid"] = False
    with pytest.raises(ValueError): api._structure_reminder_server_row("XYZ", "Compression Breakout", "LONG")


def test_owner_scoped_personal_poll_excludes_others_even_for_admin(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    rows = api._load_trade_reminders()
    rows.append(dict(rows[0], id="someone-else", owner_email="other@example.test"))
    api._save_trade_reminders(rows)
    monkeypatch.setattr(api, "_authenticated_request_identity", lambda auth: ("user@example.test", True))
    public = api.get_trade_reminders(status=None, personal_only=True)
    assert public["count"] == 1
    assert public["reminders"][0]["mode"] == "structure_1d"


def test_expiry_and_cancel_do_not_evaluate_or_send(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    created = api.create_trade_reminder(request(api))["reminder"]
    assert api.cancel_trade_reminder(created["id"])["status"] == "ok"
    api.create_trade_reminder(request(api))
    rows = api._load_trade_reminders()
    rows[-1]["expires_at"] = api._reminder_now() - 1
    api._save_trade_reminders(rows)
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda *args: pytest.fail("expired or cancelled"))
    api._process_trade_reminders_once()
    assert [row["status"] for row in api._load_trade_reminders()] == ["cancelled", "expired"]


def test_trigger_is_persisted_before_transport_and_never_re_evaluated(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    evaluations = []
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda *args: evaluations.append(1) or {"triggered": True, "reason": "daily_retest_confirmed"})
    def send(reminder, result, now):
        assert api._load_trade_reminders()[0]["status"] == "triggered"
        reminder["email_delivery_status"] = "sent"
        reminder["email_sent_at"] = api._reminder_iso(now)
        return True
    monkeypatch.setattr(api, "_deliver_trade_reminder_email", send)
    api._process_trade_reminders_once()
    api._process_trade_reminders_once()
    assert evaluations == [1]


@pytest.mark.parametrize("hours", [float("nan"), float("inf"), -1, 0])
def test_invalid_duration_rejected(monkeypatch, tmp_path, hours):
    api = api_fixture(monkeypatch, tmp_path)
    with pytest.raises(api.HTTPException): api.create_trade_reminder(request(api, duration_hours=hours))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_missing_intermediate_session_or_explicit_unclosed_bar_blocks(direction):
    bars = path(direction) + [bar("2026-09-18", 103, 106, 102, 104, direction)]
    assert not reminders.evaluate(record(direction), bars, now=epoch("2026-09-18T21:00:00Z"))["triggered"]
    bars = path(direction)
    bars[-1]["is_closed"] = False
    assert not reminders.evaluate(record(direction), bars, now=epoch("2026-09-16T21:00:00Z"))["triggered"]


def test_evaluation_exception_isolated_from_other_reminders(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    rows = api._load_trade_reminders()
    rows.append(dict(rows[0], id="second"))
    api._save_trade_reminders(rows)
    checked = []
    def evaluate(row):
        checked.append(row["id"])
        if row["id"] != "second": raise ValueError("bad record")
        return {"triggered": False, "reason": "waiting"}
    monkeypatch.setattr(api, "_evaluate_trade_reminder", evaluate)
    api._process_trade_reminders_once()
    assert len(checked) == 2
    assert api._load_trade_reminders()[0]["last_check"]["reason"] == "reminder_evaluation_error"


def test_outbox_is_only_retry_owner(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    row = api._load_trade_reminders()[0]
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {"user@example.test": {}}})
    sends = []
    def send(*args, **kwargs):
        sends.append(1)
        api._set_last_delivery_outcome("outbox_queued")
        return False
    monkeypatch.setattr(api, "_send_email_alert", send)
    api._deliver_trade_reminder_email(row, {}, now=1000)
    api._deliver_trade_reminder_email(row, {}, now=2000)
    assert sends == [1]
    assert row["email_delivery_status"] == "outbox_owned"


def test_crash_after_smtp_acceptance_never_resends(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {"user@example.test": {}}})
    monkeypatch.setattr(api, "_evaluate_trade_reminder", lambda row: {"triggered": True, "reason": "daily_retest_confirmed"})
    sends = []
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: sends.append(1) or True)
    save = api._save_trade_reminders
    def crash(rows):
        if sends: raise OSError("after accepted")
        save(rows)
    monkeypatch.setattr(api, "_save_trade_reminders", crash)
    with pytest.raises(OSError): api._process_trade_reminders_once()
    monkeypatch.setattr(api, "_save_trade_reminders", save)
    api._process_trade_reminders_once()
    assert sends == [1]
    assert api._load_trade_reminders()[0]["email_delivery_status"] == "uncertain_manual_reconciliation"


def test_missing_owner_never_sends_personal_mail(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    row = api._load_trade_reminders()[0]
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {}})
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: pytest.fail("deleted owner"))
    assert not api._deliver_trade_reminder_email(row, {})
    assert row["email_delivery_reason"] == "reminder_owner_missing"


@pytest.mark.parametrize("failure", [False, True])
def test_posix_parent_directory_synced_and_closed_even_on_error(failure):
    from types import SimpleNamespace
    calls = []
    def sync(fd):
        calls.append(("fsync", fd))
        if failure: raise OSError("directory sync failed")
    fake = SimpleNamespace(name="posix", O_RDONLY=0, O_DIRECTORY=8,
                           open=lambda path, flags: calls.append(("open", path, flags)) or 17,
                           fsync=sync, close=lambda fd: calls.append(("close", fd)))
    if failure:
        with pytest.raises(OSError): reminders._sync_parent_directory("/data", fake)
    else:
        reminders._sync_parent_directory("/data", fake)
    assert calls == [("open", "/data", 8), ("fsync", 17), ("close", 17)]


def test_non_posix_directory_sync_has_no_false_portability_claim():
    from types import SimpleNamespace
    reminders._sync_parent_directory("ignored", SimpleNamespace(name="nt"))


def test_parent_sync_occurs_after_atomic_replace(monkeypatch, tmp_path):
    path = tmp_path / "records.json"
    def verify(parent):
        assert parent == tmp_path
        assert reminders.load_records(path) == [record()]
        assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(reminders, "_sync_parent_directory", verify)
    reminders.save_records(path, [record()])


def test_claim_parent_sync_failure_prevents_smtp(monkeypatch, tmp_path):
    api = api_fixture(monkeypatch, tmp_path)
    api.create_trade_reminder(request(api))
    row = api._load_trade_reminders()[0]
    monkeypatch.setattr(api, "_load_users", lambda: {"users": {"user@example.test": {}}})
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: pytest.fail("SMTP before durable claim"))
    monkeypatch.setattr(reminders, "_sync_parent_directory", lambda path: (_ for _ in ()).throw(OSError("directory sync failed")))
    with pytest.raises(OSError): api._deliver_trade_reminder_email(row, {})
