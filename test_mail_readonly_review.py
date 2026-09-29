"""Independent negative review of operator diagnostics; no network or SMTP."""
import asyncio
import inspect
import json
import sqlite3
from types import ModuleType

import pytest

import api
from modules import auth, email_dedupe, mail_outbox, signal_tracker


def _forbidden(*args, **kwargs):
    raise AssertionError("Diagnostic entered a writer, provider, or sender")


def _request(path, token=None):
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return api.Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": headers,
        "client": ("203.0.113.9", 4321), "server": ("fixture.invalid", 443),
    })


@pytest.mark.parametrize("enforced", [True, False])
@pytest.mark.parametrize("token, expected", [("admin", 200), ("ordinary", 403), ("revoked", 401), (None, 401)])
def test_audit_http_gate_is_readonly_even_before_endpoint(monkeypatch, enforced, token, expected):
    calls = []
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", enforced)
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.invalid"})

    def verify(value, *, read_only=False):
        calls.append((value, read_only))
        assert read_only is True
        return {"email": value + "@example.invalid"} if value in {"admin", "ordinary"} else None

    monkeypatch.setattr(api, "verify_token", verify)
    monkeypatch.setattr(api, "_commerce_gate_denial", _forbidden)
    monkeypatch.setattr(api, "get_user_plan", _forbidden)

    async def next_handler(request):
        assert token == "admin"
        return api.JSONResponse({"ok": True})

    result = asyncio.run(api.commerce_auth_gate(_request("/api/email-alert-audit", token), next_handler))
    assert result.status_code == expected
    assert calls == ([] if token is None else [(token, True)])


def test_other_requests_keep_their_existing_auth_default(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", True)
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.invalid"})

    def verify(value, *, read_only=False):
        calls.append(read_only)
        return {"email": "admin@example.invalid"}

    monkeypatch.setattr(api, "verify_token", verify)
    monkeypatch.setattr(api, "_commerce_gate_denial", lambda *args: None)

    async def next_handler(request):
        return api.JSONResponse({"ok": True})

    result = asyncio.run(api.commerce_auth_gate(_request("/api/scan-status", "admin"), next_handler))
    assert result.status_code == 200
    assert calls == [False]


def test_real_module_without_readonly_accessor_cannot_fall_back_to_mutating_stats(monkeypatch):
    old_module = ModuleType("old_outbox_fixture")
    old_module.stats = _forbidden
    monkeypatch.setattr(api, "_mail_outbox", old_module)
    assert api._readonly_mail_outbox_stats()["available"] is False


@pytest.mark.parametrize("target", ["outbox", "tracker", "auth"])
def test_readonly_query_failure_closes_connection_and_preserves_schema(monkeypatch, tmp_path, target):
    path = tmp_path / (target + ".sqlite")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    before = path.read_bytes()
    before_files = sorted(item.name for item in tmp_path.iterdir())
    closed = []
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()

    def instrumented_connect(*args, **kwargs):
        assert "mode=ro" in str(args[0])
        assert kwargs.get("uri") is True
        return connect(*args, factory=Connection, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", instrumented_connect)
    if target == "outbox":
        monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
        status = mail_outbox.readonly_stats(db_path=str(path))
        assert status["available"] is False
    elif target == "tracker":
        with pytest.raises(sqlite3.OperationalError):
            with signal_tracker._diagnostic_readonly_connection(str(path)) as conn:
                conn.execute("SELECT * FROM missing")
    else:
        monkeypatch.setattr(auth, "AUTH_DB_PATH", str(path))
        monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
        with pytest.raises(sqlite3.OperationalError):
            auth._load_users_readonly()
    assert closed == [True]
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == before_files


def test_readonly_auth_missing_store_is_unknown_not_operator_optin(monkeypatch, tmp_path):
    path = tmp_path / "missing" / "auth.sqlite"
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(path))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(auth, "_sqlite_conn", _forbidden)
    with pytest.raises(sqlite3.OperationalError):
        auth.mail_channel_enabled("operator@example.invalid", "stocks_swing", read_only=True)
    # Productive sender default was deliberately not changed by diagnostics.
    assert auth.mail_channel_enabled("operator@example.invalid", "stocks_swing") is True
    assert not path.parent.exists()


def test_readonly_legacy_expiry_evaluates_copy_without_import_or_rewrite(monkeypatch, tmp_path):
    path = tmp_path / "legacy.json"
    data = {"users": {"expired@example.invalid": {
        "id": "id-expired", "plan": "elite", "manual_plan_source": "coupon",
        "manual_plan_ends_at": "2020-01-01T00:00:00+00:00", "email_alerts_enabled": True,
    }}}
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(path))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", False)
    monkeypatch.setattr(auth, "_load_users", _forbidden)
    monkeypatch.setattr(auth, "_sqlite_conn", _forbidden)
    effective = auth._load_effective_users_atomic(read_only=True)
    assert effective["expired@example.invalid"]["plan"] == "expired"
    assert "manual_plan_ends_at" not in effective["expired@example.invalid"]
    assert auth.get_email_alert_recipients(read_only=True) == []
    assert path.read_bytes() == before


def test_readonly_dedupe_filters_in_memory_without_creating_lock_or_rewriting(monkeypatch, tmp_path):
    path = tmp_path / "dedupe.json"
    path.write_text(json.dumps({"recent": 900., "old": 1.}), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(email_dedupe, "_locked_store", _forbidden)
    monkeypatch.setattr(email_dedupe, "_write_unlocked", _forbidden)
    assert email_dedupe.load_email_dedupe(str(path), now=1000., max_keep_seconds=200, read_only=True) == {"recent": 900.}
    assert email_dedupe.email_dedupe_remaining(str(path), "recent", 300, now=1000., read_only=True) == 200
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["dedupe.json"]


def test_operator_recipient_snapshot_propagates_readonly_and_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "_SECRETS", {"ALERT_EMAIL": "operator@example.invalid"})

    def channel(email, name, *, read_only=False):
        calls.append((name, read_only))
        assert read_only is True
        return True

    def recipients(*args, read_only=False, **kwargs):
        assert read_only is True
        raise sqlite3.OperationalError("fixture unavailable")

    monkeypatch.setattr(api, "mail_channel_enabled", channel)
    monkeypatch.setattr(api, "get_email_alert_recipients", recipients)
    with pytest.raises(sqlite3.OperationalError):
        api._resolve_email_alert_recipients(mail_channel="stocks_swing", read_only=True)
    assert calls == [("stocks_swing", True)]


def test_diagnostic_arguments_do_not_change_sender_and_worker_defaults():
    for function in (auth.verify_token, auth.mail_channel_enabled, auth.get_email_alert_recipients,
                     auth._load_effective_users_atomic, signal_tracker.load_pending_accepted_deliveries,
                     signal_tracker.load_delivery_acceptance_health, api._require_admin,
                     api._resolve_email_alert_recipients, email_dedupe.load_email_dedupe,
                     email_dedupe.email_dedupe_remaining):
        assert inspect.signature(function).parameters["read_only"].default is False
