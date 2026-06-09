from datetime import datetime, timedelta
from pathlib import Path

import modules.auth as auth
import api


def _isolate_auth_store(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(auth, "AUTH_DB_LEGACY_JSON_PATH", str(tmp_path / "legacy_users.json"))


def test_auth_store_uses_sqlite_and_pbkdf2_passwords(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_JWT", True)
    monkeypatch.setattr(auth, "create_token", lambda user_id, email, plan="free": f"token:{email}:{plan}")

    result = auth.register_user("pro@example.com", "very-secret", "Pro User")
    assert result["success"] is True

    db = auth._load_users()
    stored_hash = db["users"]["pro@example.com"]["password_hash"]

    assert stored_hash.startswith("pbkdf2_sha256$")
    assert auth.login_user("pro@example.com", "very-secret")["success"] is True
    assert auth.login_user("pro@example.com", "wrong")["success"] is False


def test_legacy_admin_bootstrap_restores_access_when_user_db_is_empty(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_JWT", True)
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"miroslav.mikulic@gmail.com"})
    monkeypatch.setattr(auth, "ADMIN_MASTER_KEY", "")
    monkeypatch.setattr(auth, "ADMIN_MASTER_KEY_CONFIGURED", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", True)
    monkeypatch.setattr(auth, "create_token", lambda user_id, email, plan="free": f"token:{email}:{plan}")

    result = auth.login_user("miroslav.mikulic@gmail.com", auth.LEGACY_ADMIN_MASTER_KEY)

    assert result["success"] is True
    assert result["user"]["plan"] == "elite"
    assert "miroslav.mikulic@gmail.com" in auth._load_users()["users"]


def test_admin_token_always_resolves_to_elite_limits(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"miroslav.mikulic@gmail.com"})
    token = "admin-token"
    monkeypatch.setattr(auth, "verify_token", lambda value: {"email": "miroslav.mikulic@gmail.com"} if value == token else None)

    limits = auth.get_user_limits(token)

    assert limits["plan"] == "elite"
    assert limits["is_admin"] is True
    assert limits["allowed_tabs"] is None


def test_deploy_health_endpoints_stay_public():
    assert "/api/health" in api._PUBLIC_API_PATHS
    assert "/api/system-health" in api._PUBLIC_API_PATHS
    assert "/api/commercial-readiness" in api._PUBLIC_API_PATHS


def test_auth_gate_allows_cors_preflight():
    api_source = Path(__file__).with_name("api.py").read_text(encoding="utf-8")
    assert 'request.method == "OPTIONS"' in api_source


def test_email_alert_recipients_include_only_active_alert_plans(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email in ("pro@example.com", "basic@example.com", "off@example.com", "expiredtrial@example.com"):
        assert auth.register_user(email, "secret", email.split("@")[0])["success"] is True

    db = auth._load_users()
    db["users"]["pro@example.com"]["plan"] = "pro"
    db["users"]["pro@example.com"]["alert_email"] = "signals@example.com"
    db["users"]["basic@example.com"]["plan"] = "basic"
    db["users"]["off@example.com"]["plan"] = "elite"
    db["users"]["off@example.com"]["email_alerts_enabled"] = False
    db["users"]["expiredtrial@example.com"]["plan"] = "trial"
    db["users"]["expiredtrial@example.com"]["trial_ends_at"] = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    auth._save_users(db)

    assert auth.get_email_alert_recipients() == ["signals@example.com"]
    assert auth._load_users()["users"]["expiredtrial@example.com"]["plan"] == "expired"


def test_trade_alert_recipients_respect_swing_intraday_horizon(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email, horizon in (
        ("swing@example.com", "swing"),
        ("intraday@example.com", "intraday"),
        ("both@example.com", "both"),
    ):
        assert auth.register_user(email, "secret", email.split("@")[0])["success"] is True
        db = auth._load_users()
        db["users"][email]["plan"] = "elite"
        db["users"][email]["trade_alert_horizon"] = horizon
        auth._save_users(db)

    assert auth.get_email_alert_recipients(trade_horizon="swing") == ["both@example.com", "swing@example.com"]
    assert auth.get_email_alert_recipients(trade_horizon="intraday") == ["both@example.com", "intraday@example.com"]


def test_alert_settings_update_respects_user_token(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    registered = auth.register_user("elite@example.com", "secret", "Elite")
    assert registered["success"] is True
    db = auth._load_users()
    db["users"]["elite@example.com"]["plan"] = "elite"
    auth._save_users(db)

    token = "unit-token"
    monkeypatch.setattr(auth, "verify_token", lambda value: {"email": "elite@example.com"} if value == token else None)
    updated = auth.update_user_alert_settings(
        token,
        enabled=False,
        alert_email="desk@example.com",
        narrative_email_frequency="weekly",
        trade_alert_horizon="both",
        scanner_trade_horizon="intraday",
    )

    assert updated["success"] is True
    settings = auth.get_user_alert_settings(token)
    assert settings["email_alerts_enabled"] is False
    assert settings["alert_email"] == "desk@example.com"
    assert settings["narrative_email_frequency"] == "weekly"
    assert settings["trade_alert_horizon"] == "both"
    assert settings["scanner_trade_horizon"] == "intraday"
    assert settings["has_email_alerts"] is True


def test_auth_security_status_blocks_demo_secrets_and_legacy_bootstrap(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_DEFAULT", True)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", True)
    monkeypatch.setattr(auth, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "")

    status = auth.auth_security_status()

    assert status["commercial_ready"] is False
    assert any("JWT_SECRET" in item for item in status["critical"])
    assert any("Legacy admin" in item for item in status["critical"])


def test_auth_security_status_warns_on_test_stripe_keys_and_default_prices(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_DEFAULT", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", False)
    monkeypatch.setattr(auth, "STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")

    status = auth.auth_security_status()

    assert status["stripe_key_mode"] == "test"
    assert any("test key" in item for item in status["warnings"])
    assert status["stripe_default_price_ids"]


def test_narrative_alert_recipients_respect_frequency(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email, frequency in (
        ("daily@example.com", "daily"),
        ("twice@example.com", "twice_daily"),
        ("off@example.com", "off"),
    ):
        assert auth.register_user(email, "secret", email.split("@")[0])["success"] is True
        db = auth._load_users()
        db["users"][email]["plan"] = "elite"
        db["users"][email]["narrative_email_frequency"] = frequency
        auth._save_users(db)

    assert auth.get_email_alert_recipients("narrative_pulse", "daily") == ["daily@example.com"]
    assert auth.get_email_alert_recipients("narrative_pulse", "twice_daily") == ["twice@example.com"]
    assert "off@example.com" not in auth.get_email_alert_recipients("narrative_pulse")
