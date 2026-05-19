from datetime import datetime, timedelta

import modules.auth as auth


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


def test_alert_settings_update_respects_user_token(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    registered = auth.register_user("elite@example.com", "secret", "Elite")
    assert registered["success"] is True
    db = auth._load_users()
    db["users"]["elite@example.com"]["plan"] = "elite"
    auth._save_users(db)

    token = "unit-token"
    monkeypatch.setattr(auth, "verify_token", lambda value: {"email": "elite@example.com"} if value == token else None)
    updated = auth.update_user_alert_settings(token, enabled=False, alert_email="desk@example.com")

    assert updated["success"] is True
    settings = auth.get_user_alert_settings(token)
    assert settings["email_alerts_enabled"] is False
    assert settings["alert_email"] == "desk@example.com"
    assert settings["has_email_alerts"] is True
