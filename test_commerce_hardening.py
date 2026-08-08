import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    """LB-1 AUDIT FIX: Legacy-Bootstrap nur noch mit ENV-gesetztem Key —
    der alte hartcodierte Repo-Key ist kompromittiert und gesperrt."""
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_JWT", True)
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"miroslav.mikulic@gmail.com"})
    monkeypatch.setattr(auth, "ADMIN_MASTER_KEY", "")
    monkeypatch.setattr(auth, "ADMIN_MASTER_KEY_CONFIGURED", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", True)
    monkeypatch.setattr(auth, "LEGACY_ADMIN_MASTER_KEY", "Env-Bootstrap-Key-2026!")
    monkeypatch.setattr(auth, "create_token", lambda user_id, email, plan="free": f"token:{email}:{plan}")

    result = auth.login_user("miroslav.mikulic@gmail.com", "Env-Bootstrap-Key-2026!")

    assert result["success"] is True
    assert result["user"]["plan"] == "elite"
    assert "miroslav.mikulic@gmail.com" in auth._load_users()["users"]


def test_compromised_repo_master_key_is_rejected_even_if_configured(monkeypatch, tmp_path):
    """LB-1 AUDIT FIX: 'AlphaStation2026!' stand im Git-Verlauf und muss in
    BEIDEN Pfaden (ADMIN_MASTER_KEY + Legacy) abgelehnt werden."""
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"miroslav.mikulic@gmail.com"})
    monkeypatch.setattr(auth, "ADMIN_MASTER_KEY", "AlphaStation2026!")
    monkeypatch.setattr(auth, "ADMIN_MASTER_KEY_CONFIGURED", True)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", True)
    monkeypatch.setattr(auth, "LEGACY_ADMIN_MASTER_KEY", "AlphaStation2026!")

    assert auth._is_admin_master_login("miroslav.mikulic@gmail.com", "AlphaStation2026!") is False


def test_admin_token_always_resolves_to_elite_limits(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"miroslav.mikulic@gmail.com"})
    token = "admin-token"
    monkeypatch.setattr(auth, "verify_token", lambda value: {"email": "miroslav.mikulic@gmail.com"} if value == token else None)

    limits = auth.get_user_limits(token)

    assert limits["plan"] == "elite"
    assert limits["is_admin"] is True
    assert limits["allowed_tabs"] is None


def test_only_minimal_health_and_legal_endpoints_stay_public():
    assert "/api/health" in api._PUBLIC_API_PATHS
    assert "/api/system-health" not in api._PUBLIC_API_PATHS
    assert "/api/commercial-readiness" not in api._PUBLIC_API_PATHS
    assert "/api/commercial-readiness" in api._LOOPBACK_OR_ADMIN_API_PATHS
    assert "/api/legal-info" in api._PUBLIC_API_PATHS


def test_operational_diagnostics_and_global_autotrader_require_admin():
    protected_calls = (
        lambda: api.get_email_alert_status(None),
        lambda: api.get_email_alert_audit(None),
        lambda: api.get_catalyst_data_status(None),
        lambda: api.autotrader_status(None),
        lambda: api.autotrader_update_config(api.AutotraderConfigUpdate(config={}), None),
        lambda: api.autotrader_reconcile(None),
        lambda: api.autotrader_arm(api.AutotraderArmRequest(armed=False), None),
        lambda: api.autotrader_kill_switch(None),
        lambda: api.autotrader_tighten_stop(
            api.AutotraderStopUpdate(ticker="AAPL", stop=100), None
        ),
        lambda: api.autotrader_prune_intents(None),
        lambda: api.autotrader_start(None),
        lambda: api.autotrader_stop(None),
        lambda: api.autotrader_run_single_scan(None),
        lambda: api.autotrader_clear_positions(None),
    )

    for protected_call in protected_calls:
        with pytest.raises(api.HTTPException) as denied:
            protected_call()
        assert denied.value.status_code == 403


def test_autotrader_arm_requires_exact_confirmation(monkeypatch):
    monkeypatch.setattr(
        api,
        "_require_admin",
        lambda _authorization: ({"email": "admin@example.com"}, "admin@example.com"),
    )
    calls = []
    monkeypatch.setattr(
        api._paper_autotrader,
        "set_execution_armed",
        lambda armed: calls.append(armed) or {"ok": True, "armed": armed},
    )

    with pytest.raises(api.HTTPException) as denied:
        api.autotrader_arm(
            api.AutotraderArmRequest(armed=True, confirmation="aktivieren"),
            "Bearer admin",
        )
    assert denied.value.status_code == 400
    assert calls == []

    result = api.autotrader_arm(
        api.AutotraderArmRequest(
            armed=True,
            confirmation="PAPER AUTO AKTIVIEREN",
        ),
        "Bearer admin",
    )
    assert result["ok"] is True
    assert calls == [True]

    api.autotrader_arm(
        api.AutotraderArmRequest(armed=False),
        "Bearer admin",
    )
    assert calls == [True, False]


def test_non_admin_frontend_does_not_offer_global_autotrader():
    source = Path(__file__).with_name("frontend").joinpath("index.html").read_text(encoding="utf-8")
    assert ".filter(t => t.id !== 'autotrader' || isAdmin)" in source
    assert ".filter(t => t.id !== 'backtest' || isAdmin)" not in source


def test_customer_tab_catalog_is_explicit_and_never_exposes_autotrader():
    for plan in ("trial", "basic", "pro", "elite"):
        tabs = auth.SCANNER_TABS_BY_PLAN[plan]
        assert isinstance(tabs, list)
        assert "autotrader" not in tabs
        assert "admin" not in tabs
    assert "backtest" in auth.SCANNER_TABS_BY_PLAN["trial"]
    assert "backtest" in auth.SCANNER_TABS_BY_PLAN["elite"]
    assert "backtest" not in auth.SCANNER_TABS_BY_PLAN["pro"]


def test_manual_heavy_scans_respect_plan_interval(monkeypatch):
    monkeypatch.setattr(api, "_MANUAL_SCAN_LAST_STARTED", {})

    first_retry, first_interval = api._manual_scan_throttle_claim(
        "pro@example.com", "/api/scan", "pro", now=1000.0
    )
    second_retry, second_interval = api._manual_scan_throttle_claim(
        "pro@example.com", "/api/scan", "pro", now=1001.0
    )
    other_scanner_retry, _ = api._manual_scan_throttle_claim(
        "pro@example.com", "/api/bi-scan", "pro", now=1001.0
    )

    assert first_retry == 0
    assert first_interval == 300
    assert second_interval == 300
    assert second_retry == 299
    assert other_scanner_retry == 0


def test_trade_reminders_are_scoped_to_the_authenticated_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_TRADE_REMINDERS_FILE", str(tmp_path / "trade_reminders.json"))
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.com"})
    identities = {
        "token-a": {"email": "a@example.com"},
        "token-b": {"email": "b@example.com"},
        "admin-token": {"email": "admin@example.com"},
    }
    monkeypatch.setattr(api, "verify_token", lambda token: identities.get(token))

    first = api.create_trade_reminder(
        api.TradeReminderRequest(ticker="AAA", asset_type="stock"),
        authorization="Bearer token-a",
    )["reminder"]
    second = api.create_trade_reminder(
        api.TradeReminderRequest(ticker="BBB", asset_type="stock"),
        authorization="Bearer token-b",
    )["reminder"]

    owner_a = api.get_trade_reminders(status=None, authorization="Bearer token-a")
    owner_b = api.get_trade_reminders(status=None, authorization="Bearer token-b")
    admin = api.get_trade_reminders(status=None, authorization="Bearer admin-token")

    assert [row["ticker"] for row in owner_a["reminders"]] == ["AAA"]
    assert [row["ticker"] for row in owner_b["reminders"]] == ["BBB"]
    assert {row["ticker"] for row in admin["reminders"]} == {"AAA", "BBB"}

    with pytest.raises(api.HTTPException) as denied:
        api.cancel_trade_reminder(second["id"], authorization="Bearer token-a")
    assert denied.value.status_code == 404

    assert api.cancel_trade_reminder(first["id"], authorization="Bearer token-a")["status"] == "ok"


def test_new_trade_reminder_replaces_existing_symbol_and_returns_iso_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_TRADE_REMINDERS_FILE", str(tmp_path / "trade_reminders.json"))
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "ADMIN_EMAILS", set())
    monkeypatch.setattr(api, "verify_token", lambda token: {"email": "owner@example.com"})
    monkeypatch.setattr(api, "_reminder_now", lambda: 1_700_000_000.0)

    first = api.create_trade_reminder(
        api.TradeReminderRequest(ticker="BTCUSDT", asset_type="crypto", condition="trigger"),
        authorization="Bearer owner-token",
    )["reminder"]
    second = api.create_trade_reminder(
        api.TradeReminderRequest(ticker="BTCUSDT", asset_type="crypto", condition="retest", duration_hours=3),
        authorization="Bearer owner-token",
    )["reminder"]

    stored = api._load_trade_reminders()
    assert first["ticker"] == "BTC"
    assert first["expires_at"].endswith("+00:00")
    assert second["remaining_seconds"] == 10_800
    assert [row["status"] for row in stored] == ["cancelled", "active"]


def test_strict_registration_requires_current_legal_consent(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCIAL_STRICT_MODE", True)
    monkeypatch.setattr(api, "LEGAL_TERMS_VERSION", "2026-07-01")
    monkeypatch.setattr(api, "LEGAL_PRIVACY_VERSION", "2026-07-01")

    with pytest.raises(api.HTTPException) as missing:
        asyncio.run(api.api_register(api.RegisterRequest(
            email="consent@example.com",
            password="secret-pass-123",
            name="Consent",
        )))
    assert missing.value.status_code == 400

    with pytest.raises(api.HTTPException) as stale:
        asyncio.run(api.api_register(api.RegisterRequest(
            email="consent@example.com",
            password="secret-pass-123",
            name="Consent",
            accept_terms=True,
            terms_version="2026-06-01",
            privacy_version="2026-07-01",
        )))
    assert stale.value.status_code == 409


def test_registration_throttle_blocks_account_spam(monkeypatch):
    monkeypatch.setattr(api, "_REGISTER_ATTEMPTS", {})
    monkeypatch.setattr(api, "_REGISTER_THROTTLE_MAX_ATTEMPTS", 2)

    assert api._register_throttle_retry_after("203.0.113.7", now=1000.0) == 0
    api._register_throttle_record_attempt("203.0.113.7", now=1000.0)
    api._register_throttle_record_attempt("203.0.113.7", now=1001.0)

    assert api._register_throttle_retry_after("203.0.113.7", now=1002.0) > 0
    assert api._register_throttle_retry_after("203.0.113.8", now=1002.0) == 0


def test_registration_persists_versioned_legal_consent(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    consent = {
        "accepted": True,
        "accepted_at": "2026-07-17T12:00:00+00:00",
        "terms_version": "2026-07-01",
        "privacy_version": "2026-07-01",
    }

    result = auth.register_user(
        "consent@example.com",
        "secret-pass-123",
        "Consent",
        legal_consent=consent,
    )

    assert result["success"] is True
    assert auth._load_users()["users"]["consent@example.com"]["legal_consent"] == consent


def test_production_api_disables_interactive_docs_and_sets_security_headers():
    source = Path(__file__).with_name("api.py").read_text(encoding="utf-8")
    assert 'docs_url=None if COMMERCIAL_STRICT_MODE' in source
    assert 'openapi_url=None if COMMERCIAL_STRICT_MODE' in source
    assert '"X-Content-Type-Options", "nosniff"' in source
    assert '"Strict-Transport-Security"' in source


def test_auth_gate_allows_cors_preflight():
    api_source = Path(__file__).with_name("api.py").read_text(encoding="utf-8")
    assert 'request.method == "OPTIONS"' in api_source


def test_email_alert_recipients_include_only_active_alert_plans(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email in ("pro@example.com", "basic@example.com", "off@example.com", "expiredtrial@example.com"):
        assert auth.register_user(email, "secret-pass-123", email.split("@")[0])["success"] is True

    db = auth._load_users()
    db["users"]["pro@example.com"]["plan"] = "pro"
    db["users"]["pro@example.com"]["alert_email"] = "signals@example.com"
    db["users"]["basic@example.com"]["plan"] = "basic"
    db["users"]["off@example.com"]["plan"] = "elite"
    db["users"]["off@example.com"]["email_alerts_enabled"] = False
    db["users"]["expiredtrial@example.com"]["plan"] = "trial"
    db["users"]["expiredtrial@example.com"]["trial_ends_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    auth._save_users(db)

    assert auth.get_email_alert_recipients() == ["signals@example.com"]
    assert auth._load_users()["users"]["expiredtrial@example.com"]["plan"] == "expired"


def test_expired_coupon_access_is_removed_from_plan_and_mail_routing(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    assert auth.register_user("coupon@example.com", "secret-pass-123", "Coupon")["success"] is True

    db = auth._load_users()
    user = db["users"]["coupon@example.com"]
    user.update({
        "plan": "pro",
        "manual_plan_source": "coupon:TEST30",
        "manual_plan_ends_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    })
    auth._save_users({"users": {"coupon@example.com": user}})
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token: {"email": "coupon@example.com"} if token == "coupon-token" else None,
    )

    assert auth.get_user_plan("coupon-token") == "expired"
    assert "coupon@example.com" not in auth.get_email_alert_recipients()
    stored = auth._load_users()["users"]["coupon@example.com"]
    assert stored["plan"] == "expired"
    assert "manual_plan_ends_at" not in stored
    assert "manual_plan_source" not in stored


def test_paid_subscription_is_not_expired_by_old_coupon_metadata(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    assert auth.register_user("paid@example.com", "secret-pass-123", "Paid")["success"] is True

    db = auth._load_users()
    user = db["users"]["paid@example.com"]
    user.update({
        "plan": "pro",
        "stripe_subscription_id": "sub_paid",
        "manual_plan_source": "coupon:OLD",
        "manual_plan_ends_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    })
    auth._save_users({"users": {"paid@example.com": user}})
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token: {"email": "paid@example.com"} if token == "paid-token" else None,
    )

    assert auth.get_user_plan("paid-token") == "pro"
    stored = auth._load_users()["users"]["paid@example.com"]
    assert stored["stripe_subscription_id"] == "sub_paid"
    assert "manual_plan_ends_at" not in stored
    assert "manual_plan_source" not in stored


def test_auth_upsert_and_explicit_delete_do_not_remove_other_accounts(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    for email in ("first@example.com", "second@example.com"):
        assert auth.register_user(email, "secret-pass-123", email)["success"] is True

    first = auth._load_users()["users"]["first@example.com"]
    first["plan"] = "pro"
    auth._save_users({"users": {"first@example.com": first}})

    assert set(auth._load_users()["users"]) == {"first@example.com", "second@example.com"}
    assert auth._delete_user("first@example.com") is True
    assert set(auth._load_users()["users"]) == {"second@example.com"}


def test_login_preserves_concurrently_activated_paid_subscription(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_JWT", True)
    monkeypatch.setattr(
        auth,
        "create_token",
        lambda user_id, email, plan="expired": f"token:{email}:{plan}",
    )
    assert auth.register_user(
        "race@example.com", "secret-pass-123", "Race"
    )["success"] is True

    def _activate_paid(current):
        current["plan"] = "pro"
        current["stripe_customer_id"] = "cus_paid"
        current["stripe_subscription_id"] = "sub_paid"

    auth._update_user_atomic("race@example.com", _activate_paid)

    result = auth.login_user("race@example.com", "secret-pass-123")
    stored = auth._load_users()["users"]["race@example.com"]

    assert result["success"] is True
    assert result["user"]["plan"] == "pro"
    assert stored["plan"] == "pro"
    assert stored["stripe_customer_id"] == "cus_paid"
    assert stored["stripe_subscription_id"] == "sub_paid"
    assert stored["last_login"]


def test_alert_settings_update_preserves_billing_fields(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token: {"email": "settings@example.com"}
        if token == "settings-token"
        else None,
    )
    assert auth.register_user(
        "settings@example.com", "secret-pass-123", "Settings"
    )["success"] is True

    def _activate_paid(current):
        current["plan"] = "elite"
        current["stripe_customer_id"] = "cus_settings"
        current["stripe_subscription_id"] = "sub_settings"

    auth._update_user_atomic("settings@example.com", _activate_paid)

    result = auth.update_user_alert_settings(
        "settings-token",
        alert_email="alerts@example.com",
        trade_alert_horizon="both",
    )
    stored = auth._load_users()["users"]["settings@example.com"]

    assert result["success"] is True
    assert result["settings"]["has_email_alerts"] is True
    assert stored["plan"] == "elite"
    assert stored["stripe_customer_id"] == "cus_settings"
    assert stored["stripe_subscription_id"] == "sub_settings"
    assert stored["alert_email"] == "alerts@example.com"
    assert stored["trade_alert_horizon"] == "both"


def test_coupon_redemption_has_real_expiry_and_is_single_use_per_account(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_COUPON_PATH", tmp_path / "coupons.json")
    monkeypatch.setattr(
        api,
        "verify_token",
        lambda token: {"email": "redeem@example.com"} if token == "redeem-token" else None,
    )
    assert auth.register_user("redeem@example.com", "secret-pass-123", "Redeem")["success"] is True
    api._save_coupons({
        "coupons": [{
            "code": "PRO30",
            "plan": "pro",
            "duration_days": 30,
            "active": True,
            "uses": 0,
            "max_uses": 10,
            "redeemed_by": [],
            "redemptions": [],
        }]
    })

    before = datetime.now(timezone.utc) + timedelta(days=29, hours=23)
    result = api.redeem_coupon(
        api.RedeemCouponRequest(code="pro30"),
        authorization="Bearer redeem-token",
    )
    expires_at = datetime.fromisoformat(result["expires_at"])

    assert result["new_plan"] == "pro"
    assert expires_at > before
    assert expires_at < datetime.now(timezone.utc) + timedelta(days=30, minutes=1)
    with pytest.raises(api.HTTPException) as duplicate:
        api.redeem_coupon(
            api.RedeemCouponRequest(code="PRO30"),
            authorization="Bearer redeem-token",
        )
    assert duplicate.value.status_code == 409


def test_trade_alert_recipients_respect_swing_intraday_horizon(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email, horizon in (
        ("swing@example.com", "swing"),
        ("intraday@example.com", "intraday"),
        ("both@example.com", "both"),
    ):
        assert auth.register_user(email, "secret-pass-123", email.split("@")[0])["success"] is True
        db = auth._load_users()
        db["users"][email]["plan"] = "elite"
        db["users"][email]["trade_alert_horizon"] = horizon
        auth._save_users(db)

    assert auth.get_email_alert_recipients(trade_horizon="swing") == ["both@example.com", "swing@example.com"]
    assert auth.get_email_alert_recipients(trade_horizon="intraday") == ["both@example.com", "intraday@example.com"]


def test_alert_settings_update_respects_user_token(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    registered = auth.register_user("elite@example.com", "secret-pass-123", "Elite")
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
        penny_show_watch_rows=True,
    )

    assert updated["success"] is True
    settings = auth.get_user_alert_settings(token)
    assert settings["email_alerts_enabled"] is False
    assert settings["alert_email"] == "desk@example.com"
    assert settings["narrative_email_frequency"] == "weekly"
    assert settings["trade_alert_horizon"] == "both"
    assert settings["scanner_trade_horizon"] == "intraday"
    assert settings["penny_show_watch_rows"] is True
    assert settings["has_email_alerts"] is True


def test_signup_requires_min_password_length_of_10(monkeypatch, tmp_path):
    # S-6 Audit-Fix: Mindestlaenge 6 -> 10
    _isolate_auth_store(monkeypatch, tmp_path)

    too_short = auth.register_user("short@example.com", "nine-char", "Shorty")
    assert too_short["success"] is False
    assert "10" in too_short["message"]

    long_enough = auth.register_user("long@example.com", "ten-chars!", "Longy")
    assert long_enough["success"] is True


def test_signup_rejects_malformed_and_oversized_identity_fields(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    assert auth.register_user("missing-domain@example", "secret-pass-123", "User")["success"] is False
    assert auth.register_user("two@@example.com", "secret-pass-123", "User")["success"] is False
    assert auth.register_user("valid@example.com", "x" * 1025, "User")["success"] is False
    assert auth.register_user("valid@example.com", "secret-pass-123", "x" * 101)["success"] is False
    assert auth.register_user("valid@example.com", "secret-pass-123", "Bad\nName")["success"] is False


def test_stripe_webhook_duplicate_event_is_idempotent(monkeypatch, tmp_path):
    # S-6 Audit-Fix: Event-ID-Dedupe — Stripe-Retries duerfen Plan-Updates
    # nicht doppelt anwenden, Antwort bleibt success (HTTP 200).
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    assert auth.register_user("dupe@example.com", "secret-pass-123", "Dupe")["success"] is True

    event = {
        "id": "evt_unit_dedupe_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "metadata": {"email": "dupe@example.com", "plan": "pro"},
            "payment_status": "paid",
            "subscription": "sub_unit_1",
            "customer": "cus_unit_1",
        }},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    first = auth.handle_stripe_webhook(b"{}", "sig")
    assert first["success"] is True
    assert auth._load_users()["users"]["dupe@example.com"]["plan"] == "pro"

    # Plan manuell zuruecksetzen — das Duplikat darf ihn NICHT erneut hochstufen
    db = auth._load_users()
    db["users"]["dupe@example.com"]["plan"] = "expired"
    auth._save_users(db)

    second = auth.handle_stripe_webhook(b"{}", "sig")
    assert second["success"] is True
    assert second.get("duplicate") is True
    assert auth._load_users()["users"]["dupe@example.com"]["plan"] == "expired"


@pytest.mark.parametrize(
    ("payment_status", "plan"),
    (("unpaid", "pro"), ("paid", "operator")),
)
def test_stripe_webhook_never_grants_access_for_unpaid_or_unknown_plan(
    monkeypatch, tmp_path, payment_status, plan
):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    assert auth.register_user("guard@example.com", "secret-pass-123", "Guard")["success"] is True
    original_plan = auth._load_users()["users"]["guard@example.com"]["plan"]
    event = {
        "id": f"evt_guard_{payment_status}_{plan}",
        "type": "checkout.session.completed",
        "data": {"object": {
            "metadata": {"email": "guard@example.com", "plan": plan},
            "payment_status": payment_status,
            "subscription": "sub_guard",
            "customer": "cus_guard",
        }},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    result = auth.handle_stripe_webhook(b"{}", "sig")

    assert result["success"] is False
    assert auth._load_users()["users"]["guard@example.com"]["plan"] == original_plan
    assert event["id"] not in auth._load_processed_webhook_events()


@pytest.mark.parametrize("status", ["paused", "incomplete", "past_due", "unpaid"])
def test_current_stripe_subscription_non_active_status_expires_access(
    monkeypatch, tmp_path, status
):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    assert auth.register_user("paused@example.com", "secret-pass-123", "Paused")["success"] is True
    db = auth._load_users()
    user = db["users"]["paused@example.com"]
    user["plan"] = "pro"
    user["stripe_customer_id"] = "cus_current"
    user["stripe_subscription_id"] = "sub_current"
    auth._save_users(db)
    event = {
        "id": f"evt_status_{status}",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_current",
            "customer": "cus_current",
            "status": status,
        }},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    result = auth.handle_stripe_webhook(b"{}", "sig")

    assert result["success"] is True
    assert auth._load_users()["users"]["paused@example.com"]["plan"] == "expired"


def test_old_subscription_event_cannot_expire_new_subscription(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    assert auth.register_user("current@example.com", "secret-pass-123", "Current")["success"] is True
    db = auth._load_users()
    user = db["users"]["current@example.com"]
    user["plan"] = "elite"
    user["stripe_customer_id"] = "cus_shared"
    user["stripe_subscription_id"] = "sub_new"
    auth._save_users(db)
    event = {
        "id": "evt_old_deleted",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_old", "customer": "cus_shared"}},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    result = auth.handle_stripe_webhook(b"{}", "sig")

    assert result["success"] is True
    assert result["changed"] is False
    assert result["ignored_reason"] == "subscription_not_current"
    current = auth._load_users()["users"]["current@example.com"]
    assert current["plan"] == "elite"
    assert current["stripe_subscription_id"] == "sub_new"


def test_old_active_subscription_event_cannot_replace_new_subscription(
    monkeypatch, tmp_path
):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    monkeypatch.setattr(
        auth,
        "STRIPE_PRICE_IDS",
        {"trial": "price_trial", "pro_monthly": "price_pro"},
    )
    assert auth.register_user(
        "active-current@example.com", "secret-pass-123", "Current"
    )["success"] is True
    db = auth._load_users()
    user = db["users"]["active-current@example.com"]
    user["plan"] = "elite"
    user["stripe_customer_id"] = "cus_shared"
    user["stripe_subscription_id"] = "sub_new"
    auth._save_users(db)
    event = {
        "id": "evt_old_active",
        "type": "customer.subscription.updated",
        "created": 1_800_000_000,
        "data": {"object": {
            "id": "sub_old",
            "customer": "cus_shared",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_pro"}}]},
        }},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    result = auth.handle_stripe_webhook(b"{}", "sig")

    assert result["success"] is True
    assert result["changed"] is False
    assert result["ignored_reason"] == "subscription_not_current"
    current = auth._load_users()["users"]["active-current@example.com"]
    assert current["plan"] == "elite"
    assert current["stripe_subscription_id"] == "sub_new"


def test_stale_event_for_same_subscription_cannot_restore_old_plan(
    monkeypatch, tmp_path
):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    monkeypatch.setattr(
        auth,
        "STRIPE_PRICE_IDS",
        {
            "trial": "price_trial",
            "basic_monthly": "price_basic",
            "elite_monthly": "price_elite",
        },
    )
    assert auth.register_user(
        "ordered@example.com", "secret-pass-123", "Ordered"
    )["success"] is True
    db = auth._load_users()
    user = db["users"]["ordered@example.com"]
    user["plan"] = "elite"
    user["stripe_customer_id"] = "cus_ordered"
    user["stripe_subscription_id"] = "sub_ordered"
    user["stripe_subscription_event_created"] = 1_800_000_100
    auth._save_users(db)
    event = {
        "id": "evt_stale_same_subscription",
        "type": "customer.subscription.updated",
        "created": 1_800_000_000,
        "data": {"object": {
            "id": "sub_ordered",
            "customer": "cus_ordered",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_basic"}}]},
        }},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    result = auth.handle_stripe_webhook(b"{}", "sig")

    assert result["success"] is True
    assert result["changed"] is False
    assert result["ignored_reason"] == "subscription_event_stale"
    current = auth._load_users()["users"]["ordered@example.com"]
    assert current["plan"] == "elite"
    assert current["stripe_subscription_id"] == "sub_ordered"


def test_stale_deletion_cannot_expire_newer_same_subscription_state(
    monkeypatch, tmp_path
):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    assert auth.register_user(
        "delete-order@example.com", "secret-pass-123", "Ordered"
    )["success"] is True
    db = auth._load_users()
    user = db["users"]["delete-order@example.com"]
    user["plan"] = "pro"
    user["stripe_customer_id"] = "cus_delete_order"
    user["stripe_subscription_id"] = "sub_delete_order"
    user["stripe_subscription_event_created"] = 1_800_000_100
    auth._save_users(db)
    event = {
        "id": "evt_stale_delete",
        "type": "customer.subscription.deleted",
        "created": 1_800_000_000,
        "data": {"object": {
            "id": "sub_delete_order",
            "customer": "cus_delete_order",
        }},
    }
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )

    result = auth.handle_stripe_webhook(b"{}", "sig")

    assert result["success"] is True
    assert result["changed"] is False
    assert result["ignored_reason"] == "subscription_event_stale"
    current = auth._load_users()["users"]["delete-order@example.com"]
    assert current["plan"] == "pro"
    assert current["stripe_subscription_id"] == "sub_delete_order"


def test_checkout_rejects_unknown_plan_before_stripe(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "verify_token", lambda token: {"email": "user@example.com"})
    with pytest.raises(api.HTTPException) as exc:
        asyncio.run(
            api.api_create_checkout(
                api.CheckoutRequest(plan="operator"),
                authorization="Bearer valid",
            )
        )
    assert exc.value.status_code == 400


def test_checkout_blocks_reused_trial_and_existing_subscription(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    for email in ("trial@example.com", "paid@example.com"):
        assert auth.register_user(email, "secret-pass-123", "User")["success"] is True

    def _used_trial(current):
        current["plan"] = "expired"
        current["trial_used_at"] = datetime.now(timezone.utc).isoformat()
        current["trial_ends_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()

    def _paid(current):
        current["plan"] = "pro"
        current["stripe_subscription_id"] = "sub_existing"

    auth._update_user_atomic("trial@example.com", _used_trial)
    auth._update_user_atomic("paid@example.com", _paid)

    trial = auth.get_checkout_eligibility("trial@example.com", "trial")
    paid = auth.get_checkout_eligibility("paid@example.com", "elite")

    assert trial == {
        "allowed": False,
        "code": "trial_already_used",
        "message": "Der Trial wurde fuer diesen Account bereits verwendet",
    }
    assert paid["allowed"] is False
    assert paid["code"] == "active_subscription"


def test_checkout_endpoint_returns_conflict_before_creating_duplicate(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "verify_token", lambda token: {"email": "paid@example.com"})
    monkeypatch.setattr(
        api,
        "get_checkout_eligibility",
        lambda email, plan: {
            "allowed": False,
            "code": "active_subscription",
            "message": "already subscribed",
        },
    )
    monkeypatch.setattr(
        api,
        "create_checkout_session",
        lambda **kwargs: pytest.fail("Stripe checkout must not be created"),
    )

    with pytest.raises(api.HTTPException) as exc:
        asyncio.run(
            api.api_create_checkout(
                api.CheckoutRequest(plan="pro"),
                authorization="Bearer valid",
            )
        )

    assert exc.value.status_code == 409


def test_second_trial_checkout_event_cannot_extend_access(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    assert auth.register_user(
        "trial-once@example.com", "secret-pass-123", "Trial"
    )["success"] is True

    events = [
        {
            "id": "evt_trial_first",
            "type": "checkout.session.completed",
            "created": 1_800_000_000,
            "data": {"object": {
                "metadata": {"email": "trial-once@example.com", "plan": "trial"},
                "payment_status": "paid",
                "subscription": None,
                "customer": "cus_trial",
            }},
        },
        {
            "id": "evt_trial_second",
            "type": "checkout.session.completed",
            "created": 1_800_086_400,
            "data": {"object": {
                "metadata": {"email": "trial-once@example.com", "plan": "trial"},
                "payment_status": "paid",
                "subscription": None,
                "customer": "cus_trial",
            }},
        },
    ]
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: events.pop(0)),
    )

    first = auth.handle_stripe_webhook(b"{}", "sig")
    first_expiry = auth._load_users()["users"]["trial-once@example.com"]["trial_ends_at"]
    second = auth.handle_stripe_webhook(b"{}", "sig")
    stored = auth._load_users()["users"]["trial-once@example.com"]

    assert first["success"] is True and first["changed"] is True
    assert second["success"] is True and second["changed"] is False
    assert second["ignored_reason"] == "trial_already_used"
    assert stored["trial_ends_at"] == first_expiry


def test_billing_portal_ignores_untrusted_return_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "PUBLIC_APP_URL", "https://app.alphastation.example.com")
    monkeypatch.setattr(api, "verify_token", lambda token: {"email": "user@example.com"})
    monkeypatch.setattr(
        api,
        "create_billing_portal",
        lambda email, return_url: captured.update(email=email, return_url=return_url) or "https://billing.stripe.test/session",
    )

    result = asyncio.run(
        api.api_billing_portal(
            api.BillingPortalRequest(return_url="https://attacker.example/phish"),
            authorization="Bearer valid",
        )
    )

    assert result["url"].startswith("https://billing.stripe.test/")
    assert captured["return_url"] == "https://app.alphastation.example.com"


@pytest.mark.parametrize(
    "url",
    (
        "http://app.example.com",
        "https://127.0.0.1",
        "https://localhost",
        "https://your-domain.example",
        "https://app.example.com/path",
    ),
)
def test_public_commercial_origin_rejects_insecure_or_placeholder_urls(url):
    assert api._validated_public_https_origin(url) is None


def test_public_commercial_origin_accepts_real_https_domain():
    assert api._validated_public_https_origin("https://app.alphastation.ch/") == "https://app.alphastation.ch"


def test_exchange_calendars_cover_verified_2027_schedules():
    exchanges = {item["code"]: item for item in api.EXCHANGE_CALENDARS_2026}

    assert set(exchanges) == {"US", "LSE", "XETRA", "TSE", "HKEX"}
    assert all(
        api._exchange_calendar_coverage_until(exchange) == "2027-12-31"
        for exchange in exchanges.values()
    )
    assert exchanges["US"]["holidays"]["2027-11-25"] == "Thanksgiving Day"
    assert exchanges["LSE"]["early_closes"]["2027-12-24"]["close"] == "12:30"
    assert exchanges["XETRA"]["holidays"]["2027-03-26"] == "Karfreitag"
    assert exchanges["TSE"]["holidays"]["2027-09-20"] == "Respect for the Aged Day"
    assert exchanges["HKEX"]["early_closes"]["2027-02-05"]["close"] == "12:00"


def _configure_ready_commercial_runtime(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "PUBLIC_APP_URL", "https://app.alphastation.ch")
    monkeypatch.setattr(api, "_cors_origins", {"https://app.alphastation.ch"})
    monkeypatch.setattr(api, "COMMERCIAL_STRICT_MODE", True)
    monkeypatch.setattr(api, "COUPONS_ENABLED", False)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", True)
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "HISTORICAL_SECRETS_ROTATED", True)
    monkeypatch.setattr(api, "SOURCE_REPOSITORY_ACCESS_REVIEWED", True)
    monkeypatch.setattr(api, "LEGAL_ENTITY_NAME", "Alpha Station Test GmbH")
    monkeypatch.setattr(api, "LEGAL_OPERATOR_NAME", "Test Operator")
    monkeypatch.setattr(api, "LEGAL_POSTAL_ADDRESS", "Teststrasse 1, 8000 Zurich, Switzerland")
    monkeypatch.setattr(api, "LEGAL_CONTACT_EMAIL", "legal@alphastation.test")
    monkeypatch.setattr(api, "LEGAL_TERMS_VERSION", "2026-01-01")
    monkeypatch.setattr(api, "LEGAL_PRIVACY_VERSION", "2026-01-01")
    monkeypatch.setattr(api, "EXCHANGE_CALENDARS_2026", [{"code": "US"}])
    monkeypatch.setattr(api, "_exchange_calendar_coverage_until", lambda exchange: "2099-12-31")
    monkeypatch.setattr(api, "record_alert_signals", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "load_performance_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(api, "get_signal_count", lambda: 12)
    monkeypatch.setattr(
        api,
        "auth_security_status",
        lambda: {
            "critical": [],
            "warnings": [],
            "stripe_key_mode": "live",
            "stripe_webhook_configured": True,
            "stripe_default_price_ids": [],
            "stripe_invalid_price_ids": [],
            "stripe_catalog_verified": True,
            "stripe_catalog_errors": [],
            "commercial_ready": True,
        },
    )
    monkeypatch.setattr(
        api,
        "_build_system_health",
        lambda: {
            "status": "healthy",
            "api_keys_configured": {
                "market_data": True,
                "catalyst_data": True,
                "ai_assistant": True,
            },
            "email_alerts": {"configured": True},
            "scheduler": {"running": True, "stale_or_missing_scans": []},
        },
    )


def test_commercial_readiness_blocks_missing_human_attestations(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", False)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", False)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", False)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is False
    assert result["status"] == "blocked"
    assert len(result["critical"]) >= 3


def test_commercial_readiness_can_only_pass_complete_runtime(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", True)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", True)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", True)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is True
    assert result["status"] == "ready"
    assert result["critical"] == []
    assert result["coupons"]["commercial_safe"] is True
    assert all(
        item["explicit"] and not item["forbidden_tabs"]
        for item in result["customer_tab_policy"].values()
    )


def test_commercial_readiness_blocks_unresolved_repository_secret_exposure(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", True)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", True)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", True)
    monkeypatch.setattr(api, "HISTORICAL_SECRETS_ROTATED", False)
    monkeypatch.setattr(api, "SOURCE_REPOSITORY_ACCESS_REVIEWED", False)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is False
    assert result["attestations"]["historical_secrets_rotated"] is False
    assert any("Historical credentials" in item for item in result["critical"])
    assert any("Source repository" in item for item in result["critical"])


def test_commercial_readiness_blocks_default_allow_customer_tabs(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", True)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", True)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", True)
    unsafe_policy = dict(api.SCANNER_TABS_BY_PLAN)
    unsafe_policy["elite"] = None
    monkeypatch.setattr(api, "SCANNER_TABS_BY_PLAN", unsafe_policy)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is False
    assert any("elite uses default-allow" in item for item in result["critical"])


def test_commercial_readiness_blocks_privileged_customer_tabs(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", True)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", True)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", True)
    unsafe_policy = {
        plan_id: list(tabs or [])
        for plan_id, tabs in api.SCANNER_TABS_BY_PLAN.items()
    }
    unsafe_policy["pro"].append("autotrader")
    monkeypatch.setattr(api, "SCANNER_TABS_BY_PLAN", unsafe_policy)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is False
    assert any("pro exposes privileged tabs" in item for item in result["critical"])


def test_frontend_root_route_is_registered_exactly_once():
    root_routes = [
        route
        for route in api.app.routes
        if getattr(route, "path", None) == "/"
        and "GET" in set(getattr(route, "methods", set()) or set())
    ]

    assert len(root_routes) == 1
    assert getattr(root_routes[0], "name", "") == "serve_frontend"


def test_commercial_readiness_blocks_non_transactional_coupons(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", True)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", True)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", True)
    monkeypatch.setattr(api, "COUPONS_ENABLED", True)

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is False
    assert result["coupons"]["transactional"] is False
    assert any("Coupons are enabled" in item for item in result["critical"])


def test_coupon_endpoint_fails_closed_when_disabled(monkeypatch):
    monkeypatch.setattr(api, "COUPONS_ENABLED", False)
    monkeypatch.setattr(
        api,
        "verify_token",
        lambda token: {"email": "coupon@example.com"}
        if token == "coupon-token"
        else None,
    )

    with pytest.raises(api.HTTPException) as blocked:
        api.redeem_coupon(
            api.RedeemCouponRequest(code="PRO30"),
            authorization="Bearer coupon-token",
        )

    assert blocked.value.status_code == 503


def test_commercial_readiness_blocks_incomplete_public_legal_notice(monkeypatch):
    _configure_ready_commercial_runtime(monkeypatch)
    monkeypatch.setattr(api, "LEGAL_REVIEW_APPROVED", True)
    monkeypatch.setattr(api, "DATA_LICENSE_APPROVED", True)
    monkeypatch.setattr(api, "TAX_SETUP_APPROVED", True)
    monkeypatch.setattr(api, "LEGAL_POSTAL_ADDRESS", "")

    result = asyncio.run(api.api_commercial_readiness())

    assert result["commercial_ready"] is False
    assert any("LEGAL_POSTAL_ADDRESS" in item for item in result["critical"])


def test_public_plans_match_canonical_auth_prices_and_basic_access():
    result = asyncio.run(api.api_get_plans())
    published = {plan["id"]: plan for plan in result["plans"]}

    for plan_id in ("basic", "pro", "elite"):
        assert published[plan_id]["name"] == auth.PLANS[plan_id]["name"]
        assert published[plan_id]["price"] == auth.PLANS[plan_id]["price"]
    assert auth.PLANS["basic"]["max_scanner_tabs"] == len(auth.SCANNER_TABS_BY_PLAN["basic"])


def test_public_legal_info_exposes_only_public_operator_fields(monkeypatch):
    monkeypatch.setattr(api, "LEGAL_ENTITY_NAME", "Alpha Station Test GmbH")
    monkeypatch.setattr(api, "LEGAL_OPERATOR_NAME", "Test Operator")
    monkeypatch.setattr(api, "LEGAL_POSTAL_ADDRESS", "Teststrasse 1, Zurich")
    monkeypatch.setattr(api, "LEGAL_CONTACT_EMAIL", "legal@alphastation.test")
    monkeypatch.setattr(api, "LEGAL_TERMS_VERSION", "2026-01-01")
    monkeypatch.setattr(api, "LEGAL_PRIVACY_VERSION", "2026-01-01")

    result = asyncio.run(api.api_legal_info())

    assert result["configured"] is True
    assert set(result) == {
        "entity_name", "operator_name", "postal_address", "contact_email",
        "terms_version", "privacy_version", "configured",
    }


def test_frontend_uses_same_origin_in_production_and_explicit_dev_override():
    frontend = Path(__file__).with_name("frontend").joinpath("index.html").read_text(encoding="utf-8")

    assert "window.ALPHA_API_BASE" in frontend
    assert "DEV_FRONTEND_PORTS.has(window.location.port)" in frontend
    assert ": window.location.origin" in frontend
    assert "usePublicLegalInfo" in frontend
    assert "usePublicPlans" in frontend


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
    assert status["commercial_ready"] is False
    assert "auth_db_path" not in status


def _configure_mock_live_stripe(monkeypatch, tmp_path, *, basic_amount=2900):
    _isolate_auth_store(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_DEFAULT", False)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_EPHEMERAL", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", False)
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_SECRET_KEY", "sk_live_unit")
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "whsec_unit")
    price_ids = {
        "trial": "price_live_trial_unit",
        "basic_monthly": "price_live_basic_unit",
        "pro_monthly": "price_live_pro_unit",
        "elite_monthly": "price_live_elite_unit",
    }
    monkeypatch.setattr(auth, "STRIPE_PRICE_IDS", price_ids)
    prices = {
        price_ids["trial"]: SimpleNamespace(
            unit_amount=100, currency="usd", active=True, recurring=None,
        ),
        price_ids["basic_monthly"]: SimpleNamespace(
            unit_amount=basic_amount,
            currency="usd",
            active=True,
            recurring=SimpleNamespace(interval="month"),
        ),
        price_ids["pro_monthly"]: SimpleNamespace(
            unit_amount=7900,
            currency="usd",
            active=True,
            recurring=SimpleNamespace(interval="month"),
        ),
        price_ids["elite_monthly"]: SimpleNamespace(
            unit_amount=14900,
            currency="usd",
            active=True,
            recurring=SimpleNamespace(interval="month"),
        ),
    }
    monkeypatch.setattr(
        auth,
        "stripe",
        SimpleNamespace(Price=SimpleNamespace(retrieve=lambda price_id: prices[price_id])),
    )


def test_auth_security_status_verifies_live_stripe_catalog(monkeypatch, tmp_path):
    _configure_mock_live_stripe(monkeypatch, tmp_path)

    status = auth.auth_security_status()

    assert status["stripe_catalog_verified"] is True
    assert status["stripe_catalog_errors"] == []


def test_auth_security_status_blocks_wrong_live_stripe_amount(monkeypatch, tmp_path):
    _configure_mock_live_stripe(monkeypatch, tmp_path, basic_amount=3900)

    status = auth.auth_security_status()

    assert status["stripe_catalog_verified"] is False
    assert any("basic_monthly amount" in item for item in status["stripe_catalog_errors"])
    assert status["commercial_ready"] is False


def test_narrative_alert_recipients_respect_frequency(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email, frequency in (
        ("daily@example.com", "daily"),
        ("twice@example.com", "twice_daily"),
        ("off@example.com", "off"),
    ):
        assert auth.register_user(email, "secret-pass-123", email.split("@")[0])["success"] is True
        db = auth._load_users()
        db["users"][email]["plan"] = "elite"
        db["users"][email]["narrative_email_frequency"] = frequency
        auth._save_users(db)

    assert auth.get_email_alert_recipients("narrative_pulse", "daily") == ["daily@example.com"]
    assert auth.get_email_alert_recipients("narrative_pulse", "twice_daily") == ["twice@example.com"]
    assert "off@example.com" not in auth.get_email_alert_recipients("narrative_pulse")


def test_frontend_is_precompiled_self_hosted_and_cache_safe():
    root = Path(__file__).parent
    frontend = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    nginx = (root / "deploy" / "nginx-tradingbot.conf").read_text(encoding="utf-8")
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    assert '<script src="/app.bundle.js"' in frontend
    assert '<script src="/boot.js"></script>' in frontend
    assert "onerror=" not in frontend
    assert "Babel.transform" not in frontend
    assert "cdn.jsdelivr.net/npm/react" not in frontend
    assert "fonts.googleapis.com" not in frontend
    assert (root / "frontend" / "app.bundle.js").is_file()
    assert (root / "frontend" / "boot.js").is_file()
    assert (root / "scripts" / "build_frontend_bundle.js").is_file()
    assert (root / "scripts" / "verify_frontend_bundle.py").is_file()
    boot = (root / "frontend" / "boot.js").read_text(encoding="utf-8")
    assert "textContent" in boot
    assert "innerHTML" not in boot
    assert "Content-Security-Policy" in nginx
    assert "script-src 'self'" in nginx
    assert "zone=tb_register" in nginx
    assert "location = /api/auth/register" in nginx
    assert "zone=tb_password_reset" in nginx
    assert "location = /api/auth/password-reset/request" in nginx
    assert "location = /boot.js" in nginx
    assert "bak|backup|old|orig|patch|diff|zip|tar|tgz|sql|sqlite|sqlite3|db" in nginx
    assert "return 404;" in nginx
    assert "scripts/verify_frontend_bundle.py" in deploy
    assert "location = /index.html" in nginx
    assert 'Cache-Control "no-store, no-cache, must-revalidate"' in nginx
    assert not (root / "frontend" / "index.html.backup").exists()


def test_production_units_use_unprivileged_hardened_services():
    root = Path(__file__).parent
    install_script = (root / "deploy" / "install.sh").read_text(encoding="utf-8")
    for unit_name in ("tradingbot-api.service", "tradingbot-bg.service"):
        unit = (root / "deploy" / unit_name).read_text(encoding="utf-8")
        assert "User=tradingbot" in unit
        assert "Group=tradingbot" in unit
        assert "UMask=0077" in unit
        assert "NoNewPrivileges=true" in unit
        assert "RestrictNamespaces=true" in unit
        assert "CapabilityBoundingSet=" in unit
        assert "PrivateTmp=true" in unit
        assert "BindPaths=/home/tradingbot/app/data_cache/runtime:/tmp" in unit
        assert "TimeoutStopSec=30" in unit
    assert 'install -d -m 0700 -o tradingbot -g tradingbot "$APP_DIR/data_cache/runtime"' in install_script
    assert not (root / "deploy" / "tradingbot.service").exists()

    api_unit = (root / "deploy" / "tradingbot-api.service").read_text(encoding="utf-8")
    assert 'Environment="API_BIND_HOST=127.0.0.1"' in api_unit
    assert "--host ${API_BIND_HOST} --port 8000" in api_unit


def test_commercial_deploy_fails_closed_on_insecure_edge_or_legacy_frontends():
    root = Path(__file__).parent
    edge = (root / "deploy" / "verify_commercial_edge.sh").read_text(encoding="utf-8")
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")
    install_script = (root / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert "PUBLIC_APP_URL must be a bare HTTPS origin" in edge
    assert "openssl x509 -checkend 604800" in edge
    assert "tradingbot-frontend.service tradingbot.service" in edge
    assert ":3000|:8501" in edge
    assert "FastAPI port 8000 is exposed publicly" in edge
    assert "127\\.0\\.0\\.1:8000" in edge
    assert "verify_commercial_edge" in deploy
    assert deploy.count("verify_commercial_edge") >= 3
    assert "disable_legacy_frontends" in install_script
    nginx_pos = install_script.index("sudo nginx -t && sudo systemctl reload nginx")
    precheck_pos = install_script.index("ALLOW_LEGACY_FRONTENDS=1")
    disable_pos = install_script.index("    disable_legacy_frontends")
    full_check_pos = install_script.index(
        'bash "$APP_DIR/deploy/verify_commercial_edge.sh"',
        disable_pos,
    )
    assert nginx_pos < precheck_pos < disable_pos < full_check_pos


def test_deploy_preflights_target_before_updating_live_worktree():
    root = Path(__file__).parent
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    export_pos = deploy.index('git archive "origin/$BRANCH"')
    preflight_pos = deploy.index('run_source_checks "$preflight_dir"')
    pull_pos = deploy.index('git pull --ff-only origin "$BRANCH"')
    assert export_pos < preflight_pos < pull_pos
    assert "mktemp -d /tmp/alphastation-preflight." in deploy
    assert "Local revision changed during preflight; refusing deploy." in deploy
    assert 'install -d -m 0700 -o tradingbot -g tradingbot "$APP_DIR/data_cache/runtime"' in deploy


def test_deploy_migrates_legacy_runtime_before_hardened_unit_switch():
    root = Path(__file__).parent
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    assert 'SERVICE_VENV_DIR="$APP_DIR/venv"' in deploy
    assert 'VENV_DIR="$SERVICE_VENV_DIR"' in deploy
    assert "does not match the hardened service runtime" in deploy
    assert "Preparing the hardened runtime" in deploy
    assert "ensure_runtime_dependencies" in deploy
    assert "prepare_runtime_state" in deploy
    assert 'chown -R tradingbot:tradingbot "$APP_DIR/data_cache"' in deploy
    assert "alphastation_trade_reminders.json" in deploy
    assert "alphastation_email_dedupe.json" in deploy


def test_deploy_rolls_back_code_dependencies_and_units_after_live_failure():
    root = Path(__file__).parent
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in deploy
    assert "rollback_deployment()" in deploy
    assert 'git reset --hard "$old_rev_full"' in deploy
    assert "tradingbot-pip-rollback.log" in deploy
    assert "sync_service_units" in deploy
    assert 'trap handle_deploy_error ERR' in deploy
    assert "Rollback health OK; previous revision restored." in deploy
    assert "Tracked worktree changes detected" in deploy


def test_deploy_synchronizes_hardened_service_units_before_restart():
    root = Path(__file__).parent
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    sync_pos = deploy.index('echo "[deploy] Synchronizing hardened systemd units..."')
    restart_pos = deploy.index('echo "[deploy] Restarting services: $SERVICES"')
    assert sync_pos < restart_pos
    assert 'install -m 0644 "$APP_DIR/deploy/$unit" "/etc/systemd/system/$unit"' in deploy
    assert "configure_api_bind_mode" in deploy
    assert "systemctl daemon-reload" in deploy


def test_legacy_direct_frontend_gets_explicit_noncommercial_api_compatibility():
    root = Path(__file__).parent
    deploy = (root / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    condition = '[ "$LEGACY_FRONTEND_ACTIVE" = "1" ] && [ "$COMMERCIAL_DEPLOY" != "1" ]'
    assert condition in deploy
    assert 'Environment="API_BIND_HOST=0.0.0.0"' in deploy
    assert "ExecStart=/home/tradingbot/app/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000" in deploy
    assert 'rm -f -- "$API_BIND_OVERRIDE_FILE"' in deploy
    assert "legacy frontend :3000 -> public API :8000" in deploy


def test_direct_runtime_dependencies_are_exactly_pinned():
    root = Path(__file__).parent
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert requirements
    assert all("==" in line for line in requirements if line.strip() and not line.startswith("#"))


# ── AUDIT 2026-07-28: Feingranulare Mail-Kanal-Auswahl ───────────────────────


def test_mail_channel_optout_filters_recipients(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)

    for email in ("kanal-aus@example.com", "kanal-an@example.com"):
        assert auth.register_user(email, "secret-pass-123", email.split("@")[0])["success"] is True
        db = auth._load_users()
        db["users"][email]["plan"] = "elite"
        auth._save_users(db)
    db = auth._load_users()
    db["users"]["kanal-aus@example.com"]["mail_channels"] = {"crypto": False}
    auth._save_users(db)

    # Crypto-Kanal: Opt-out-User faellt raus, der andere bleibt.
    assert auth.get_email_alert_recipients(mail_channel="crypto") == ["kanal-an@example.com"]
    # Anderer Kanal: beide dabei.
    assert auth.get_email_alert_recipients(mail_channel="stocks_swing") == ["kanal-an@example.com", "kanal-aus@example.com"]
    # Kein Kanal (Bestandsaufrufe) und unbekannter Kanal: kein Filter.
    assert auth.get_email_alert_recipients() == ["kanal-an@example.com", "kanal-aus@example.com"]
    assert auth.get_email_alert_recipients(mail_channel="unbekannt") == ["kanal-an@example.com", "kanal-aus@example.com"]


def test_mail_channel_settings_roundtrip_and_validation(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    assert auth.register_user("kanal@example.com", "secret-pass-123", "Kanal")["success"] is True
    db = auth._load_users()
    db["users"]["kanal@example.com"]["plan"] = "elite"
    auth._save_users(db)

    token = "unit-token"
    monkeypatch.setattr(auth, "verify_token", lambda value: {"email": "kanal@example.com"} if value == token else None)

    updated = auth.update_user_alert_settings(token, mail_channels={"crypto": False, "bear": False})
    assert updated["success"] is True
    settings = auth.get_user_alert_settings(token)
    assert settings["mail_channels"]["crypto"] is False
    assert settings["mail_channels"]["bear"] is False
    assert settings["mail_channels"]["stocks_swing"] is True
    assert {opt["key"] for opt in settings["mail_channel_options"]} == set(auth.MAIL_CHANNELS)

    rejected = auth.update_user_alert_settings(token, mail_channels={"gibts_nicht": False})
    assert rejected["success"] is False


def test_mail_channel_enabled_defaults_and_optout(monkeypatch, tmp_path):
    _isolate_auth_store(monkeypatch, tmp_path)
    # Unbekannte Adresse / kein Kanal / unbekannter Kanal: True (Bestandsverhalten).
    assert auth.mail_channel_enabled("ghost@example.com", "crypto") is True
    assert auth.mail_channel_enabled("ghost@example.com", "") is True
    assert auth.mail_channel_enabled("ghost@example.com", "unbekannt") is True

    assert auth.register_user("optout@example.com", "secret-pass-123", "Opt")["success"] is True
    db = auth._load_users()
    db["users"]["optout@example.com"]["plan"] = "elite"
    auth._save_users(db)
    # User ohne Kanal-Settings: True.
    assert auth.mail_channel_enabled("optout@example.com", "crypto") is True

    db = auth._load_users()
    db["users"]["optout@example.com"]["mail_channels"] = {"crypto": False}
    auth._save_users(db)
    assert auth.mail_channel_enabled("optout@example.com", "crypto") is False
    assert auth.mail_channel_enabled("optout@example.com", "bear") is True


def test_scanner_mail_channel_mapping_is_complete_for_known_scanners():
    expected = {
        "stock_strategy": "stocks_swing",
        "orb": "stocks_intraday",
        "early_movers": "crypto",
        "biotech": "biotech",
        "bi_long": "biotech",
        "crash_monitor": "bear",
        "bear": "bear",
        "new_listing": "new_listing",
    }
    for scanner, channel in expected.items():
        assert auth.scanner_mail_channel(scanner) == channel
    assert auth.scanner_mail_channel("unbekannter_scanner") == ""
    # Jeder gemappte Kanal muss ein gueltiger Kanal sein.
    for scanner in auth._SCANNER_MAIL_CHANNEL:
        assert auth.scanner_mail_channel(scanner) in auth.MAIL_CHANNELS
