"""Offline functional review of admin-only mail evidence, never SMTP delivery."""
import copy
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import api
from modules import mail_outbox


def _forbidden(*_args, **_kwargs):
    raise AssertionError("A read-only operator audit must not send or mutate")


@pytest.fixture
def audit_environment(monkeypatch):
    """Keep all unrelated providers, real accounts and journals out of this audit."""
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.invalid"})
    monkeypatch.setattr(api, "verify_token", lambda token: {
        "admin": {"email": "admin@example.invalid"},
        "ordinary": {"email": "ordinary@example.invalid"},
    }.get(token))
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_email_alert_status", lambda: {
        "configured": True, "recipient_configured": True,
        "recipient_count": 1, "startup_cooldown_remaining_seconds": 0,
    })
    monkeypatch.setattr(api, "_email_pipeline_summary", lambda **_kwargs: {
        "sent": 0, "skipped": 0, "errors": 0,
        "outbox": {"available": False, "pending": 0},
    })
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **_kwargs: [])
    monkeypatch.setattr(api, "_common_stock_guard_status", lambda: {"available": True})
    monkeypatch.setattr(api, "_build_alert_audit_for_cache", lambda scanner, path: {
        "scanner": scanner, "rows_checked": 0, "alertable_now_count": 0,
        "suppression_counts": {}, "evidence_scope": "cache_precheck",
        "delivery_evaluated": False,
    })
    for function in ("_send_email_alert", "save_cache_file", "save_partial_cache_file",
                     "_enrich_biotech_mail_rows"):
        if hasattr(api, function):
            monkeypatch.setattr(api, function, _forbidden)
    return monkeypatch


@pytest.mark.parametrize("authorization", [None, "Bearer ordinary", "Bearer invalid", "Basic admin"])
def test_audit_rejects_missing_or_non_admin_before_reading(audit_environment, authorization):
    audit_environment.setattr(api, "_build_alert_audit_for_cache", _forbidden)
    audit_environment.setattr(api, "_admin_mail_delivery_status", _forbidden)
    with pytest.raises(api.HTTPException) as denied:
        api.get_email_alert_audit(authorization)
    assert denied.value.status_code == 403


def test_audit_is_inspection_only_and_gap_uses_canonical_stock_strategy(audit_environment):
    calls = []

    def inspect(scanner, path):
        calls.append((scanner, path))
        return {"scanner": scanner, "rows_checked": 1, "alertable_now_count": 0,
                "suppression_counts": {"score_below_alert_threshold": 1}}

    audit_environment.setattr(api, "_build_alert_audit_for_cache", inspect)
    before = copy.deepcopy(api._EMAIL_SEND_LOG)
    response = api.get_email_alert_audit("Bearer admin")
    assert api._EMAIL_SEND_LOG == before
    assert response["summary"]["evidence_scope"] == "cache_precheck"
    assert response["summary"]["delivery_evaluated"] is False
    assert response["delivery"]["pipeline"]["sent"] == 0
    for name in ("Gap Momentum Long", "Gap Momentum Short"):
        assert ("stock_strategy", api._strategy_cache_path(name)) in calls
    for key in ("strat_gap_momentum_long", "strat_gap_momentum_short"):
        assert response["scanners"][key]["scanner"] == key


def test_real_http_route_keeps_header_binding_with_request_scoped_diagnostics(audit_environment):
    import asyncio
    # No lifespan context: do not start a scheduler or scan, even in fixtures.
    async def check():
        for headers, expected in (([], 401), ([(b"authorization", b"Bearer admin")], 200)):
            messages = []
            request_delivered = False
            async def receive():
                nonlocal request_delivered
                if not request_delivered:
                    request_delivered = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await asyncio.Event().wait()
            async def send(message):
                messages.append(message)
            await api.app({
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1", "method": "GET", "scheme": "http",
                "path": "/api/email-alert-audit", "raw_path": b"/api/email-alert-audit",
                "query_string": b"", "headers": headers, "root_path": "",
                "client": ("127.0.0.1", 1234), "server": ("fixture.invalid", 80),
            }, receive, send)
            status = next(message["status"] for message in messages if message["type"] == "http.response.start")
            assert status == expected
            if status == 200:
                body = json.loads(b"".join(message.get("body", b"") for message in messages))
                assert body["status"] == "ok"
                assert datetime.fromisoformat(body["timestamp"]).utcoffset() == timedelta(0)
    asyncio.run(asyncio.wait_for(check(), timeout=10))


def test_delivery_counts_are_channel_scoped_not_global_recipient_count(audit_environment):
    calls = []

    def recipients(**kwargs):
        calls.append(kwargs)
        return ["intraday@example.invalid"] if kwargs["mail_channel"] == "stocks_intraday" else []

    audit_environment.setattr(api, "_resolve_email_alert_recipients", recipients)
    response = api.get_email_alert_audit("Bearer admin")
    assert response["email_alerts"]["recipient_count"] == 1
    assert response["delivery"]["recipient_counts"] == {"stocks_swing": 0, "stocks_intraday": 1}
    assert any(item["mail_class"] == "swing_trade" and item["trade_horizon"] == "swing" for item in calls)


def test_recipient_resolver_failure_is_unknown_not_zero(audit_environment):
    def unavailable(**_kwargs):
        raise RuntimeError("accounts temporarily unavailable")

    audit_environment.setattr(api, "_resolve_email_alert_recipients", unavailable)
    status = api._admin_mail_delivery_status()
    assert status["recipient_counts"] == {"stocks_swing": None, "stocks_intraday": None}


def test_entire_endpoint_redacts_smtp_exception_addresses_and_subject(audit_environment):
    api._EMAIL_SEND_LOG.append({
        "timestamp": datetime.now(timezone.utc).isoformat(), "status": "error",
        "subject": "PRIVATE: portfolio position ABCD 250 shares",
        "reason": "SMTPRecipientsRefused: owner@example.invalid password=private-secret "
                  "https://private-provider.invalid/token/private-secret score_below_alert_threshold",
    })
    serialized = json.dumps(api.get_email_alert_audit("Bearer admin"))
    for secret in ("owner@example.invalid", "private-secret", "PRIVATE: portfolio", "private-provider.invalid"):
        assert secret not in serialized
    assert "score_below_alert_threshold" in serialized


def test_unknown_process_event_status_is_allowlisted_not_echoed(audit_environment):
    api._EMAIL_SEND_LOG.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "SMTP password=private-secret", "reason": "",
    })
    response = api._admin_mail_delivery_status()
    assert "private-secret" not in json.dumps(response)
    assert response["recent_decisions"][0]["status"] == "unknown"


@pytest.mark.parametrize("reason,expected", [
    ("SMTPAuthenticationError:not_delivered", "smtp_authentication_failed"),
    ("SMTPRecipientsRefused:not_delivered", "smtp_recipients_rejected"),
    ("SMTPSenderRefused:not_delivered", "smtp_sender_rejected"),
    ("SMTPDataError:not_delivered", "smtp_message_rejected"),
    ("TimeoutError:not_delivered", "smtp_delivery_failed"),
    ("SMTPDataError:outcome_unknown", "smtp_delivery_outcome_unknown"),
    ("TimeoutError:outcome_unknown", "smtp_delivery_outcome_unknown"),
])
def test_reviewed_transport_failures_remain_explainable_without_raw_details(audit_environment, reason, expected):
    api._EMAIL_SEND_LOG.append({
        "timestamp": datetime.now(timezone.utc).isoformat(), "status": "error",
        "subject": "Private subject and recipient", "reason": reason,
    })
    result = api._admin_mail_delivery_status()
    reasons = result["recent_decisions"][0]["reasons"]
    assert [item["code"] for item in reasons] == [expected]
    assert "Private subject" not in json.dumps(result)


def test_disabled_watch_mail_is_explained_without_requesting_server_logs(audit_environment):
    api._EMAIL_SEND_LOG.append({"timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "skipped", "reason": "new_listing_dump_watch_emails_disabled"})
    reasons = api._admin_mail_delivery_status()["recent_decisions"][0]["reasons"]
    assert reasons[0]["code"] == "new_listing_dump_watch_emails_disabled"


def test_recent_decisions_and_frontend_window_do_not_include_old_events(audit_environment):
    api._EMAIL_SEND_LOG.append({
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
        "status": "sent", "reason": "",
    })
    response = api._admin_mail_delivery_status()
    assert response["recent_decisions"] == []


def test_aggregate_and_dedicated_gap_caches_are_not_double_counted(audit_environment):
    """The strategy aggregate and dedicated Gap cache can be the same result."""
    aggregate = api.STRATEGY_SCAN_CACHE
    long_path = api._strategy_cache_path("Gap Momentum Long")
    short_path = api._strategy_cache_path("Gap Momentum Short")
    rows = {
        aggregate: [{"ticker": "GL", "strategy": "Gap Momentum Long"},
                    {"ticker": "GS", "strategy": "Gap Momentum Short"}],
        long_path: [{"ticker": "GL", "strategy": "Gap Momentum Long"}],
        short_path: [{"ticker": "GS", "strategy": "Gap Momentum Short"}],
    }

    audit_environment.setattr(api, "load_cache_file", lambda path, **kwargs: (copy.deepcopy(rows.get(path, [])), None))

    def inspect(scanner, path, *, read_only=False):
        source = api._extract_cache_rows_for_alert_audit(scanner, path, exclude_dedicated_gap=read_only)
        return {"scanner": scanner, "rows_checked": len(source),
                "alertable_now_count": len(source), "suppression_counts": {},
                "alertable_preview": copy.deepcopy(source)}

    audit_environment.setattr(api, "_build_alert_audit_for_cache", inspect)
    summary = api.get_email_alert_audit("Bearer admin")["summary"]
    assert summary["total_rows_checked"] == 2
    assert summary["total_alertable_now"] == 2


def _pipeline_without_real_journals(monkeypatch, events):
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", events)
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(api, "load_delivery_acceptance_health", lambda: {
        "status": "ok", "pending_count": 0, "tracker_pending": False,
    })
    monkeypatch.setattr(api, "load_pending_accepted_deliveries", lambda: [])


def test_partial_smtp_acceptance_is_visible_in_pipeline_counters(monkeypatch):
    _pipeline_without_real_journals(monkeypatch, [{
        "timestamp": datetime.now(timezone.utc).isoformat(), "status": "partial",
        "subject": "Two recipients; one accepted", "reason": "",
    }])
    pipeline = api._email_pipeline_summary(public=True)
    accepted = pipeline.get("smtp_accepted_events", pipeline.get("sent", 0) + pipeline.get("partial", 0))
    assert accepted == 1


def test_unknown_outbox_preserves_unavailable_counter_semantics(monkeypatch):
    _pipeline_without_real_journals(monkeypatch, [])
    pipeline = api._email_pipeline_summary(public=True)
    assert pipeline["outbox"]["available"] is False
    # The frontend must gate unknown counters on available and render a dash.
    source = (Path(__file__).parent / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "mailAudit.delivery.pipeline?.outbox?.available ?" in source


def test_operator_delivery_get_does_not_expire_or_recover_outbox_rows(monkeypatch, tmp_path):
    """Use a disposable real SQLite outbox to catch mutations hidden in stats()."""
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    outbox_path = tmp_path / "mail.sqlite"
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(outbox_path))
    old_time = time.time() - 2 * 24 * 3600
    record_id = mail_outbox.enqueue("Audit fixture", "<p>fixture</p>",
        ["recipient@example.invalid"], mail_class="trade", now=old_time)
    assert record_id is not None
    with sqlite3.connect(outbox_path) as connection:
        before = connection.execute("SELECT status, attempts, last_error FROM mail_outbox WHERE id=?", (record_id,)).fetchone()
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(api, "load_delivery_acceptance_health", lambda: {
        "status": "ok", "pending_count": 0, "tracker_pending": False,
    })
    monkeypatch.setattr(api, "load_pending_accepted_deliveries", lambda: [])
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **kwargs: [])
    api._admin_mail_delivery_status()
    with sqlite3.connect(outbox_path) as connection:
        after = connection.execute("SELECT status, attempts, last_error FROM mail_outbox WHERE id=?", (record_id,)).fetchone()
    assert after == before


def test_readonly_outbox_missing_database_does_not_create_state(monkeypatch, tmp_path):
    target = tmp_path / "missing" / "mail.sqlite"
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    status = mail_outbox.readonly_stats(db_path=str(target))
    assert status["available"] is False
    assert not target.parent.exists()


def test_readonly_outbox_does_not_initialize_an_incomplete_schema(monkeypatch, tmp_path):
    target = tmp_path / "old-schema.sqlite"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE historical_table(value TEXT)")
    before = target.read_bytes()
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    status = mail_outbox.readonly_stats(db_path=str(target))
    assert status["available"] is False
    assert target.read_bytes() == before
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("historical_table",)]


def test_readonly_outbox_reports_fallback_journals_without_lock_files(monkeypatch, tmp_path):
    target = tmp_path / "disabled.sqlite"
    Path(str(target) + ".uncertain.json").write_text(json.dumps({"entries": {"u": {"error": "private"}}}), encoding="utf-8")
    Path(str(target) + ".tracker_acceptance.json").write_text(json.dumps({"entries": {
        "a": {"status": "pending", "intent_key": "intent-a", "accepted_at": 100.},
        "b": {"status": "done", "intent_key": "intent-b", "accepted_at": 50.},
    }}), encoding="utf-8")
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "0")
    before = sorted(path.name for path in tmp_path.iterdir())
    snapshot = mail_outbox.readonly_stats(db_path=str(target))
    assert snapshot["uncertain"] == 1
    assert snapshot["tracker_acceptance_pending_count"] == 1
    assert snapshot["tracker_acceptance_oldest_at"] == 100.
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_readonly_tracker_missing_stores_remain_missing(monkeypatch, tmp_path):
    from modules import signal_tracker
    tracker = tmp_path / "uninitialized" / "tracker.sqlite"
    journal = tmp_path / "uninitialized" / "journal.sqlite"
    monkeypatch.setattr(signal_tracker, "_db_path", lambda: str(tracker))
    monkeypatch.setattr(signal_tracker, "_delivery_journal_path", lambda: str(journal))
    monkeypatch.setattr(signal_tracker, "_db_connection", _forbidden)
    monkeypatch.setattr(signal_tracker, "_delivery_journal_connection", _forbidden)
    assert signal_tracker.load_pending_accepted_deliveries(read_only=True) == []
    status = signal_tracker.load_delivery_acceptance_health(read_only=True)
    assert status["status"] == "error"
    assert status["tracker_pending"] is True
    assert not tracker.parent.exists()


def test_readonly_auth_effective_expiry_does_not_change_account_store(monkeypatch, tmp_path):
    from modules import auth
    target = tmp_path / "auth.sqlite"
    expired = {"id": "trial", "plan": "trial", "trial_ends_at": "2020-01-01T00:00:00+00:00",
               "email_alerts_enabled": True}
    elite = {"id": "paid", "plan": "elite", "stripe_subscription_id": "sub-test",
             "email_alerts_enabled": True}
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE users(email TEXT PRIMARY KEY, data TEXT)")
        connection.executemany("INSERT INTO users VALUES (?,?)", [
            ("expired@example.invalid", json.dumps(expired)),
            ("paid@example.invalid", json.dumps(elite)),
        ])
    before = target.read_bytes()
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(target))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(auth, "_sqlite_conn", _forbidden)
    effective = auth._load_effective_users_atomic(read_only=True)
    assert effective["expired@example.invalid"]["plan"] == "expired"
    assert auth.get_email_alert_recipients(trade_horizon="swing", mail_channel="stocks_swing", read_only=True) == ["paid@example.invalid"]
    assert target.read_bytes() == before


def test_readonly_auth_token_still_checks_revocation_and_version(monkeypatch, tmp_path):
    from modules import auth
    target = tmp_path / "auth.sqlite"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE users(email TEXT PRIMARY KEY, data TEXT)")
        connection.execute("CREATE TABLE revoked_tokens(jti TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO users VALUES (?,?)", ("admin@example.invalid", json.dumps({"id": "account-id", "token_version": 2})))
        connection.execute("INSERT INTO revoked_tokens VALUES ('revoked')")
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(target))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(auth, "_sqlite_conn", _forbidden)
    before = target.read_bytes()
    def token(jti, version=2):
        return auth.pyjwt.encode({"email": "admin@example.invalid", "sub": "account-id", "ver": version,
                                  "jti": jti, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                                 auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    assert auth.verify_token(token("valid"), read_only=True)
    assert auth.verify_token(token("revoked"), read_only=True) is None
    assert auth.verify_token(token("valid", version=1), read_only=True) is None
    assert target.read_bytes() == before


def test_admin_cache_only_audit_never_refreshes_missing_stock_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {"tickers": None, "names": None, "loaded_at": 0})
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", _forbidden)
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", _forbidden)
    monkeypatch.setattr(api, "rate_limited_get", _forbidden)
    monkeypatch.setattr(api, "load_cache_file", lambda *args, **kwargs: ([{
        "ticker": "ABCD", "strategy": "Gap Momentum Long", "score": 90, "grade": "S", "rvol": 2.,
    }], None))
    with pytest.raises(RuntimeError, match="audit_common_stock_basis_unavailable"):
        api._build_alert_audit_for_cache("stock_strategy", "unused.json", read_only=True)
    assert api._common_stock_guard_status(read_only=True)["available"] is False


def test_no_partial_events_produces_explicit_numeric_zero(monkeypatch):
    _pipeline_without_real_journals(monkeypatch, [])
    snapshot = api._email_pipeline_summary(public=True)
    assert snapshot["partial"] == 0
    assert type(snapshot["partial"]) is int
    assert snapshot["smtp_accepted_events"] == 0


def test_actual_admin_get_leaves_every_local_state_file_unchanged(monkeypatch, tmp_path):
    """DELETE-journal stores: actual getter leaves all bytes/mtimes untouched.

    WAL coordination (-wal/-shm), not business data, is tested separately;
    immutable=1 would wrongly hide new accepted events in an active WAL.
    """
    from modules import auth, email_dedupe, signal_tracker

    auth_path = tmp_path / "auth.sqlite"
    outbox_path = tmp_path / "outbox.sqlite"
    tracker_path = tmp_path / "tracker.sqlite"
    journal_path = tmp_path / "acceptance.sqlite"
    dedupe_path = tmp_path / "dedupe.json"
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(auth_path))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.invalid"})
    monkeypatch.setattr(api, "verify_token", auth.verify_token)
    monkeypatch.setattr(api, "get_email_alert_recipients", auth.get_email_alert_recipients)
    monkeypatch.setattr(api, "mail_channel_enabled", auth.mail_channel_enabled)
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "sender@example.invalid", "GMAIL_APP_PASSWORD": "offline-only", "ALERT_EMAIL": "admin@example.invalid"})
    with sqlite3.connect(auth_path) as connection:
        connection.execute("CREATE TABLE users(email TEXT PRIMARY KEY, data TEXT, updated_at TEXT)")
        connection.execute("CREATE TABLE revoked_tokens(jti TEXT PRIMARY KEY)")
        connection.executemany("INSERT INTO users VALUES (?,?,?)", [
            ("admin@example.invalid", json.dumps({"id": "admin-id", "token_version": 2, "plan": "elite", "stripe_subscription_id": "sub-a"}), "fixture"),
            ("expired@example.invalid", json.dumps({"id": "trial-id", "plan": "trial", "trial_ends_at": "2020-01-01T00:00:00+00:00"}), "fixture"),
        ])
    connection.close()
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(outbox_path))
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    seed_connections = []
    original_connect = mail_outbox._connect
    def fixture_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        seed_connections.append(connection)
        return connection
    monkeypatch.setattr(mail_outbox, "_connect", fixture_connect)
    assert mail_outbox.enqueue("Private pending subject", "Private body", ["admin@example.invalid"],
                              mail_class="trade", now=time.time() - 2 * 24 * 3600)
    # sqlite3's transaction context commits but does not close. Complete all
    # fixture writer lifetimes/checkpoints BEFORE the immutable GET snapshot.
    for connection in seed_connections:
        connection.close()
    monkeypatch.setattr(api, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(signal_tracker, "_db_path", lambda: str(tracker_path))
    monkeypatch.setattr(signal_tracker, "_delivery_journal_path", lambda: str(journal_path))
    with signal_tracker._db_connection():
        pass
    with signal_tracker._delivery_journal_connection():
        pass
    for path in (outbox_path, tracker_path, journal_path):
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        finally:
            connection.close()
    monkeypatch.setattr(api, "load_delivery_acceptance_health", signal_tracker.load_delivery_acceptance_health)
    monkeypatch.setattr(api, "load_pending_accepted_deliveries", signal_tracker.load_pending_accepted_deliveries)
    dedupe_path.write_text(json.dumps({"stored_fixture": time.time()}), encoding="utf-8")
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(dedupe_path))
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [{"timestamp": datetime.now(timezone.utc).isoformat(),
        "subject": "Private subject", "status": "partial", "reason": "SMTP owner@example.invalid password=private-secret"}])

    stock_path = tmp_path / "common-stock.json"
    stock_path.write_text(json.dumps({"cached_at": time.time(), "tickers": ["ABCD"], "names": {"ABCD": "Alpha Common Company"}}), encoding="utf-8")
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(stock_path))
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {"tickers": None, "names": None, "loaded_at": 0})
    for constant in ("BI_CACHE_LONG", "BI_CACHE_SHORT", "BIOTECH_CACHE", "BEAR_CACHE", "ORB_CACHE",
                     "NEW_LISTING_CACHE", "EARLY_MOVERS_CACHE", "STRATEGY_SCAN_CACHE"):
        path = tmp_path / (constant.lower() + ".json")
        path.write_text(json.dumps({"cached_at": datetime.now().isoformat(), "results": []}), encoding="utf-8")
        monkeypatch.setattr(api, constant, str(path))
    def strategy_path(name, *args):
        return str(tmp_path / (name.lower().replace(" ", "_") + ".json"))
    monkeypatch.setattr(api, "_strategy_cache_path", strategy_path)
    for direction in ("Long", "Short"):
        Path(strategy_path("Gap Momentum " + direction)).write_text(json.dumps({
            "cached_at": datetime.now().isoformat(), "results": [{"ticker": "ABCD", "strategy": "Gap Momentum " + direction,
            "grade": "B", "score": 40, "rvol": 2., "price": 10.}]}), encoding="utf-8")
    for module, functions in ((api, ("_send_email_alert", "rate_limited_get", "_load_common_stock_universe",
                                   "_enrich_stock_alert_5m_state", "save_cache_file")),
                              (auth, ("_sqlite_conn", "_maybe_migrate_legacy_json", "_save_users")),
                              (signal_tracker, ("_db_connection", "_delivery_journal_connection")),
                              (mail_outbox, ("_connect", "_expire_overdue", "_recover_abandoned_claims")),
                              (email_dedupe, ("_locked_store",))):
        for function in functions:
            monkeypatch.setattr(module, function, _forbidden)

    def state():
        return {str(path.relative_to(tmp_path)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in tmp_path.rglob("*") if path.is_file()}

    before = state()
    token = auth.pyjwt.encode({"email": "admin@example.invalid", "sub": "admin-id", "ver": 2,
                              "jti": "diagnostic-valid", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                             auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    response = api.get_email_alert_audit("Bearer " + token)
    assert response["status"] == "ok"
    assert response["delivery"]["pipeline"]["partial"] == 1
    assert response["summary"]["audit_error_count"] == 0
    assert "private-secret" not in json.dumps(response)
    assert state() == before


def test_readonly_wal_observes_uncheckpointed_rows_without_business_mutation(monkeypatch, tmp_path):
    """SQLite may update WAL coordination files; it must not alter queue rows."""
    target = tmp_path / "live-wal.sqlite"
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    connections = []
    original = mail_outbox._connect
    def fixture_writer(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connections.append(connection)
        return connection
    monkeypatch.setattr(mail_outbox, "_connect", fixture_writer)
    try:
        record = mail_outbox.enqueue("WAL fixture", "body", ["owner@example.invalid"],
                                    mail_class="trade", now=time.time() - 2 * 24 * 3600,
                                    db_path=str(target))
        assert record is not None
        assert Path(str(target) + "-wal").stat().st_size > 0
        writer = connections[-1]
        before = writer.execute("SELECT * FROM mail_outbox ORDER BY id").fetchall()
        monkeypatch.setattr(mail_outbox, "_connect", _forbidden)
        monkeypatch.setattr(mail_outbox, "_expire_overdue", _forbidden)
        monkeypatch.setattr(mail_outbox, "_recover_abandoned_claims", _forbidden)
        snapshot = mail_outbox.readonly_stats(db_path=str(target))
        assert snapshot["available"] is True
        assert snapshot["pending"] == 1
        assert snapshot["expired"] == 0
        after = writer.execute("SELECT * FROM mail_outbox ORDER BY id").fetchall()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
    finally:
        for connection in connections:
            connection.close()
