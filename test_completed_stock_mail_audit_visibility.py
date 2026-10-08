"""Completed leaf mail evidence stays visible without claiming delivery or a new cache.

The real persisted-attempt reader and result consumer are exercised. Cache I/O
and presentation-only decorators use the established offline status fixture;
no scanner, provider, SMTP, order or migration operation is needed.
"""
import json
from threading import local

import pytest

import api
from test_stock_strategy_attempt_status import attempt_payload, status_io, write_attempt
from test_stock_strategy_sweep_isolation import _mock_sweep, _row


@pytest.fixture
def admin_status_io(status_io, monkeypatch):
    """Isolate unrelated cache prechecks, recipients and delivery journals.

    The endpoint, its request snapshot, the public result projection and the
    bounded disk attempt reader stay real. Cache precheck errors deliberately
    exercise the endpoint's existing unavailable-cache response shape.
    """
    monkeypatch.setattr(api, "_require_admin", lambda authorization, *, read_only=False: None)

    def unavailable_cache(scanner, path, *, read_only=False):
        raise RuntimeError("offline_cache_unavailable")

    monkeypatch.setattr(api, "_build_alert_audit_for_cache", unavailable_cache)
    monkeypatch.setattr(api, "_common_stock_guard_status", lambda *, read_only=False: {
        "status": "unavailable", "reason": "offline_fixture",
    })
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **kwargs: [])
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {"offline-existing-entry": 123.0})
    monkeypatch.setattr(api, "_email_pipeline_summary", lambda *args, **kwargs: {
        "window_seconds": 86400,
        "events_recorded_since_start": 0,
        "recent_event_count": 0,
        "sent": 0,
        "partial": 0,
        "smtp_accepted_events": 0,
        "skipped": 0,
        "errors": 0,
        "queued_since_start": 0,
        "outbox": {
            "enabled": False, "available": False, "pending": 0, "sent": 0,
            "expired": 0, "dead": 0, "error": "module unavailable",
        },
        "tracker_acceptance": {
            "status": "error", "tracker_pending": True,
            "pending_count": None, "last_error": "module unavailable",
        },
        "last_event": None,
        "last_sent_at": None,
        "note": "Process events plus persistent retry outbox. Outbox counters survive API restarts.",
    })
    monkeypatch.setattr(api, "_email_alert_status", lambda: {
        "configured": False,
        "sender_configured": False,
        "app_password_configured": False,
        "recipient_configured": False,
        "recipient_count": 0,
        "global_recipient_count": 0,
        "subscriber_recipient_count": 0,
        "send_to_subscribers": False,
        "default_trade_horizon": "swing",
        "narrative_pulse": {},
        "crypto_armed_watch_mails_enabled": False,
        "new_listing_dump_watch_mails_enabled": False,
        "startup_cooldown_remaining_seconds": 0,
        "cooldown_entries": 1,
        "scanner_cooldowns_seconds": {
            "default": 28800, "biotech": 28800, "crash_stock": 129600,
            "early_mover_digest": 28800, "early_mover_armed_digest": 28800,
        },
        "min_alert_score": 80,
        "dedupe": {},
        "outbox": {
            "enabled": False, "available": False, "pending": 0, "sent": 0,
            "expired": 0, "dead": 0, "error": "module unavailable",
        },
        "required_keys": ["GMAIL_USER", "GMAIL_APP_PASSWORD"],
        "optional_keys": ["ALERT_EMAIL"],
        "config_sources_checked": [
            "user Streamlit secrets", "application Streamlit secrets",
            "application environment file", "process environment",
        ],
    })
    return status_io


@pytest.mark.parametrize("strategy,slug", [
    ("Momentum Breakout Long", "momentum_breakout_long"),
    ("Gap Momentum Long", "gap_momentum_long"),
    ("Gap Momentum Short", "gap_momentum_short"),
    ("Cup and Handle Breakout", "cup_and_handle_breakout"),
])
def test_completed_leaf_mail_audit_survives_the_result_consumer_after_restart(status_io, strategy, slug):
    # Break caught: dropping completed diagnostics also drops their per-run
    # mail evidence at the first consumer-visible result boundary.
    root, cache = status_io
    payload = attempt_payload(strategy, status="complete")
    payload["diagnostics"]["mail_audit"] = {
        "schema_version": 1,
        "candidate_rows": 5,
        "reason_occurrences": {"score_below_alert_threshold": 2, "missing_gmail_config": 1},
        "transport_events": {"trade_sender_called": 2, "trade_accepted": 1, "trade_failed": 1},
    }
    path = write_attempt(root, payload)
    before = path.read_bytes()

    result = api.get_scan_results(strategy, None, "stocks")

    assert result.diagnostics["latest_attempt"] == {
        "available": True,
        "status": "complete",
        "strategy_slug": slug,
        "attempt_run_id": "a" * 32,
        "code_revision": "123456abcdef",
        "started_at": "2026-09-14T11:58:54+00:00",
        "updated_at": "2026-09-14T11:58:57+00:00",
        "result_count": 0,
        "error_code": None,
        "status_scope": "persisted_attempt_not_live_worker",
        "mail_audit": {
            "schema_version": 1,
            "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
            "candidate_rows": 5,
            "reason_occurrences": {"missing_gmail_config": 1, "score_below_alert_threshold": 2},
            "transport_events": {"trade_accepted": 1, "trade_failed": 1, "trade_sender_called": 2},
        },
    }
    assert result.cached_at == cache["stamp"]
    assert result.count == 0 and result.data == []
    assert result.scan_run_id is None and result.scan_running is None
    assert result.scan_error is None
    assert "attempt_diagnostics" not in result.diagnostics
    assert "mail_audit" not in result.diagnostics
    assert path.read_bytes() == before and api._scan_status == {}


@pytest.mark.parametrize("bad_count", [True, -1, 1.5, "PRIVATE_COUNT", 10**9 + 1])
def test_completed_mail_audit_exposes_only_bounded_counts_and_fixed_identifiers(status_io, bad_count):
    # Break caught: copying raw completed diagnostics instead of their reviewed
    # projection exposes private values or accepts bool/negative/coerced counts.
    root, _ = status_io
    payload = attempt_payload(status="complete")
    payload["PRIVATE_EXTRA"] = "PRIVATE_PROVIDER_BODY"
    payload["diagnostics"]["raw_provider_body"] = "PRIVATE_PROVIDER_BODY"
    payload["diagnostics"]["mail_audit"] = {
        "schema_version": 1,
        "semantics": "PRIVATE_SEMANTICS",
        "candidate_rows": bad_count,
        "recipient": "PRIVATE_RECIPIENT",
        "reason_occurrences": {
            "score_below_alert_threshold": bad_count,
            "missing_gmail_config": 7,
            "PRIVATE_TICKER_REASON": 3,
        },
        "transport_events": {
            "trade_accepted": bad_count,
            "trade_failed": 2,
            "PRIVATE_SUBJECT_EVENT": 4,
        },
    }
    write_attempt(root, payload)

    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")

    assert result.diagnostics["latest_attempt"]["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "reason_occurrences": {"missing_gmail_config": 7},
        "transport_events": {"trade_failed": 2},
    }
    assert "PRIVATE" not in result.model_dump_json()
    assert "diagnostics" not in result.diagnostics["latest_attempt"]


def test_completed_mail_audit_retains_an_observed_zero_without_inventing_transport_events(status_io):
    # Break caught: treating valid zero evidence as missing, or adding a zero
    # accepted/delivered event that was never observed in this run.
    root, _ = status_io
    payload = attempt_payload(status="complete")
    payload["diagnostics"]["mail_audit"] = {
        "schema_version": 1,
        "candidate_rows": 0,
        "reason_occurrences": {},
        "transport_events": {},
    }
    write_attempt(root, payload)

    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")

    assert result.diagnostics["latest_attempt"]["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "candidate_rows": 0,
        "reason_occurrences": {},
        "transport_events": {},
    }


@pytest.mark.parametrize("missing_or_invalid_audit", [None, {}, {"schema_version": True}, {"schema_version": 2}])
def test_completed_attempt_without_a_valid_mail_audit_does_not_claim_zero_mail_activity(status_io, missing_or_invalid_audit):
    # Break caught: defaulting absent or invalid historical evidence into an
    # apparently measured zero-mail run.
    root, _ = status_io
    payload = attempt_payload(status="complete")
    if missing_or_invalid_audit is not None:
        payload["diagnostics"]["mail_audit"] = missing_or_invalid_audit
    write_attempt(root, payload)

    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")

    assert result.diagnostics["latest_attempt"]["available"] is True
    assert result.diagnostics["latest_attempt"]["status"] == "complete"
    assert "mail_audit" not in result.diagnostics["latest_attempt"]
    assert "mail_audit" not in result.diagnostics


def test_missing_attempt_file_is_unknown_not_a_completed_zero_mail_run(status_io):
    # Break caught: a missing persisted file is made to look like successful
    # completion with zero candidates or zero transport activity.
    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")

    assert result.diagnostics["latest_attempt"] == {"available": False, "reason": "missing"}
    assert "mail_audit" not in result.diagnostics


def test_older_completed_audit_keeps_its_identity_while_newer_cache_remains_authoritative(status_io):
    # Break caught: exposing the audit as current cache evidence, advancing a
    # scheduler timestamp, changing its UUID, or mutating persisted/cache state.
    root, cache = status_io
    cache["stamp"] = "2026-09-14T12:01:00+00:00"
    payload = attempt_payload(status="complete")
    payload["result_count"] = 5
    payload["diagnostics"]["final_results"] = 5
    payload["diagnostics"]["mail_audit"] = {
        "schema_version": 1,
        "candidate_rows": 5,
        "reason_occurrences": {"score_below_alert_threshold": 5},
        "transport_events": {},
    }
    path = write_attempt(root, payload)
    before = path.read_bytes()
    cache_before = (root / "final.json").read_bytes()
    key = api._strategy_scan_status_key("Momentum Breakout Long")
    manual_state = {
        "running": False,
        "last_run_id": "newer-manual-ack",
        "last_attempt_at": "2026-09-14T12:00:00+00:00",
        "last_run": "2026-09-14T12:01:00+00:00",
    }
    api._scan_status[key] = dict(manual_state)

    result = api.get_scan_results("Momentum Breakout Long", None, "stocks")

    assert result.diagnostics["latest_attempt"] == {
        "available": True,
        "status": "complete",
        "strategy_slug": "momentum_breakout_long",
        "attempt_run_id": "a" * 32,
        "code_revision": "123456abcdef",
        "started_at": "2026-09-14T11:58:54+00:00",
        "updated_at": "2026-09-14T11:58:57+00:00",
        "result_count": 5,
        "error_code": None,
        "status_scope": "persisted_attempt_not_live_worker",
        "mail_audit": {
            "schema_version": 1,
            "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
            "candidate_rows": 5,
            "reason_occurrences": {"score_below_alert_threshold": 5},
            "transport_events": {},
        },
    }
    assert result.cached_at == "2026-09-14T12:01:00+00:00"
    assert result.count == 0 and result.data == []
    assert result.diagnostics["final_results"] == 0
    assert "mail_audit" not in result.diagnostics
    assert "attempt_diagnostics" not in result.diagnostics
    assert result.scan_run_id == "newer-manual-ack"
    assert result.scan_last_attempt_at == "2026-09-14T12:00:00+00:00"
    assert result.scan_last_completed_at == "2026-09-14T12:01:00+00:00"
    assert result.scan_running is False and result.scan_error is None
    assert api._scan_status[key] == manual_state
    assert path.read_bytes() == before
    assert (root / "final.json").read_bytes() == cache_before


@pytest.mark.parametrize("strategy,slug", [
    ("Elliott Wave Muster", "elliott_wave_muster"),
    ("Momentum Breakout Long", "momentum_breakout_long"),
    ("Gap Momentum Long", "gap_momentum_long"),
    ("Gap Momentum Short", "gap_momentum_short"),
    ("Cup and Handle Breakout", "cup_and_handle_breakout"),
])
def test_admin_mail_audit_reads_each_supported_persisted_leaf_safely(admin_status_io, strategy, slug):
    # Break caught: Admin omits completed per-run evidence or copies unprojected
    # provider/private fields rather than using the validated disk reader.
    root, _ = admin_status_io
    payload = attempt_payload(status="complete")
    payload["strategy_slug"] = slug
    payload["PRIVATE_EXTRA"] = "PRIVATE_PROVIDER_BODY"
    payload["diagnostics"]["mail_audit"] = {
        "schema_version": 1,
        "candidate_rows": 5,
        "recipient": "PRIVATE_RECIPIENT",
        "reason_occurrences": {"missing_gmail_config": 2, "score_below_alert_threshold": -1, "PRIVATE_REASON": 8},
        "transport_events": {"trade_accepted": 1, "trade_failed": True, "PRIVATE_EVENT": 9},
    }
    path = write_attempt(root, payload)
    before = {entry.name: entry.read_bytes() for entry in root.iterdir() if entry.is_file()}
    state = {"running": False, "last_run_id": "keep-scheduler-owner"}
    api._scan_status["strategy_scan"] = dict(state)

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_attempts"] == [{
        "strategy": strategy,
        "available": True,
        "status": "complete",
        "strategy_slug": slug,
        "attempt_run_id": "a" * 32,
        "code_revision": "123456abcdef",
        "started_at": "2026-09-14T11:58:54+00:00",
        "updated_at": "2026-09-14T11:58:57+00:00",
        "result_count": 0,
        "error_code": None,
        "status_scope": "persisted_attempt_not_live_worker",
        "mail_audit": {
            "schema_version": 1,
            "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
            "candidate_rows": 5,
            "reason_occurrences": {"missing_gmail_config": 2},
            "transport_events": {"trade_accepted": 1},
        },
    }]
    assert result["delivery"]["smtp_acceptance_is_inbox_delivery"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert api._scan_status == {"strategy_scan": state}
    assert api._EMAIL_SEND_LOG == []
    assert api._EMAIL_COOLDOWN == {"offline-existing-entry": 123.0}
    assert path.exists()
    assert {entry.name: entry.read_bytes() for entry in root.iterdir() if entry.is_file()} == before


def test_admin_mail_audit_keeps_separate_run_counts_and_chronology(admin_status_io):
    # Break caught: summing distinct leaf attempts or assigning one run's
    # counters to another loses the evidence's identity and chronology.
    root, _ = admin_status_io
    momentum = attempt_payload(status="complete")
    momentum["diagnostics"]["mail_audit"] = {
        "schema_version": 1, "candidate_rows": 3,
        "reason_occurrences": {"score_below_alert_threshold": 3},
        "transport_events": {"trade_sender_called": 1},
    }
    write_attempt(root, momentum)
    gap = attempt_payload("Gap Momentum Short", status="complete")
    gap.update(run_id="b" * 32, started_at="2026-09-14T12:02:00+00:00", updated_at="2026-09-14T12:04:00+00:00")
    gap["diagnostics"]["mail_audit"] = {
        "schema_version": 1, "candidate_rows": 4,
        "reason_occurrences": {"missing_gmail_config": 4},
        "transport_events": {"trade_accepted": 2},
    }
    write_attempt(root, gap)

    result = api.get_email_alert_audit("offline-admin")

    attempts = result["delivery"]["stock_attempts"]
    assert len(attempts) == 2
    by_strategy = {item["strategy"]: item for item in attempts}
    assert by_strategy["Momentum Breakout Long"]["attempt_run_id"] == "a" * 32
    assert by_strategy["Momentum Breakout Long"]["updated_at"] == "2026-09-14T11:58:57+00:00"
    assert by_strategy["Momentum Breakout Long"]["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "candidate_rows": 3,
        "reason_occurrences": {"score_below_alert_threshold": 3},
        "transport_events": {"trade_sender_called": 1},
    }
    assert by_strategy["Gap Momentum Short"]["attempt_run_id"] == "b" * 32
    assert by_strategy["Gap Momentum Short"]["started_at"] == "2026-09-14T12:02:00+00:00"
    assert by_strategy["Gap Momentum Short"]["updated_at"] == "2026-09-14T12:04:00+00:00"
    assert by_strategy["Gap Momentum Short"]["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "candidate_rows": 4,
        "reason_occurrences": {"missing_gmail_config": 4},
        "transport_events": {"trade_accepted": 2},
    }


@pytest.mark.parametrize("invalid_file", [False, True])
def test_admin_mail_audit_has_no_measured_stock_attempts_for_missing_or_invalid_files(admin_status_io, invalid_file):
    # Break caught: missing or invalid attempts appear as completed zero-count
    # records instead of remaining unavailable to this list.
    root, _ = admin_status_io
    if invalid_file:
        payload = attempt_payload(status="complete")
        payload["run_id"] = "PRIVATE_INVALID_RUN"
        write_attempt(root, payload)
    before = {entry.name: entry.read_bytes() for entry in root.iterdir() if entry.is_file()}

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_attempts"] == []
    assert "PRIVATE" not in json.dumps(result)
    assert api._scan_status == {}
    assert api._EMAIL_SEND_LOG == []
    assert api._EMAIL_COOLDOWN == {"offline-existing-entry": 123.0}
    assert {entry.name: entry.read_bytes() for entry in root.iterdir() if entry.is_file()} == before


def test_admin_completed_attempt_without_mail_audit_preserves_unknown_mail_activity(admin_status_io):
    # Break caught: a valid historical completion without audit counters either
    # disappears from Admin or is filled with invented zero-mail evidence.
    root, _ = admin_status_io
    payload = attempt_payload(status="complete")
    payload["diagnostics"]["raw_provider_body"] = "PRIVATE_PROVIDER_BODY"
    path = write_attempt(root, payload)
    before = path.read_bytes()

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_attempts"] == [{
        "strategy": "Momentum Breakout Long",
        "available": True,
        "status": "complete",
        "strategy_slug": "momentum_breakout_long",
        "attempt_run_id": "a" * 32,
        "code_revision": "123456abcdef",
        "started_at": "2026-09-14T11:58:54+00:00",
        "updated_at": "2026-09-14T11:58:57+00:00",
        "result_count": 0,
        "error_code": None,
        "status_scope": "persisted_attempt_not_live_worker",
    }]
    assert "PRIVATE" not in json.dumps(result)
    assert api._scan_status == {}
    assert api._EMAIL_SEND_LOG == []
    assert api._EMAIL_COOLDOWN == {"offline-existing-entry": 123.0}
    assert path.read_bytes() == before


def sweep_payload():
    """Hand-checked complete automatic run, separate from every leaf attempt."""
    return {
        "schema_version": 1,
        "attempt_kind": "stock_strategy_sweep",
        "strategy_slug": "stock_strategy_sweep",
        "run_id": "c" * 32,
        "code_revision": "123456abcdef",
        "status": "complete",
        "started_at": "2026-09-14T11:58:54+00:00",
        "updated_at": "2026-09-14T11:58:57+00:00",
        "results": [],
        "result_count": 2,
        "error_code": None,
        "diagnostics": {
            "coverage": "complete", "final_results": 2,
            "strategies_total": 4, "strategies_attempted": 4,
            "strategies_completed": 4, "strategies_failed": 0,
            "current_result_count": 2, "mail_status": "guarded",
            "mail_audit": {
                "schema_version": 1, "candidate_rows": 2,
                "reason_occurrences": {"score_below_alert_threshold": 1},
                "transport_events": {"trade_sender_called": 1, "trade_accepted": 1},
            },
        },
    }


def write_sweep(root, payload):
    path = root / "stock_strategy_sweep_attempt.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    return path


def test_admin_reads_automatic_sweep_mail_evidence_from_its_own_fixed_file(admin_status_io):
    # Break caught: reading only leaf files misses the combined automatic mail
    # guard, because the sweep calls leaves with send_email=False.
    root, _ = admin_status_io
    path = write_sweep(root, sweep_payload())
    before = {entry.name: entry.read_bytes() for entry in root.iterdir() if entry.is_file()}

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_sweep"] == {
        "available": True,
        "status": "complete",
        "strategy_slug": "stock_strategy_sweep",
        "attempt_run_id": "c" * 32,
        "code_revision": "123456abcdef",
        "started_at": "2026-09-14T11:58:54+00:00",
        "updated_at": "2026-09-14T11:58:57+00:00",
        "result_count": 2,
        "error_code": None,
        "status_scope": "persisted_attempt_not_live_worker",
        "mail_audit": {
            "schema_version": 1,
            "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
            "candidate_rows": 2,
            "reason_occurrences": {"score_below_alert_threshold": 1},
            "transport_events": {"trade_accepted": 1, "trade_sender_called": 1},
        },
    }
    assert result["delivery"]["stock_attempts"] == []
    assert result["delivery"]["smtp_acceptance_is_inbox_delivery"] is False
    assert api._scan_status == {}
    assert api._EMAIL_SEND_LOG == []
    assert api._EMAIL_COOLDOWN == {"offline-existing-entry": 123.0}
    assert path.exists()
    assert {entry.name: entry.read_bytes() for entry in root.iterdir() if entry.is_file()} == before


def test_missing_auto_sweep_file_is_unknown_not_zero_mail_activity(admin_status_io):
    # Break caught: absent sweep evidence is defaulted to successful zero mail
    # activity or confused with an available individual leaf attempt.
    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_sweep"] == {"available": False, "reason": "missing"}
    assert result["delivery"]["stock_attempts"] == []


@pytest.mark.parametrize("field,bad", [
    ("schema_version", True),
    ("attempt_kind", "stock_strategy"),
    ("strategy_slug", "momentum_breakout_long"),
    ("strategy_slug", "../PRIVATE_PATH"),
    ("run_id", "PRIVATE_RUN_ID"),
    ("code_revision", "PRIVATE_REVISION"),
    ("status", "PRIVATE_STATUS"),
    ("started_at", "2026-09-14T11:58:54"),
    ("updated_at", "2026-09-14T11:00:00+00:00"),
    ("updated_at", "2035-01-01T00:00:00+00:00"),
    ("result_count", True),
    ("result_count", -1),
    ("result_count", 10**9 + 1),
    ("result_count", 0),
    ("error_code", "PRIVATE_PROVIDER_MESSAGE"),
    ("results", [{"ticker": "PRIVATE_SYMBOL"}]),
])
def test_invalid_auto_sweep_metadata_is_unavailable_without_echoing_private_payload(admin_status_io, field, bad):
    # Break caught: relaxing the sweep reader's existing leaf identity, time,
    # completion or empty-results guards exposes invalid diagnostic evidence.
    root, _ = admin_status_io
    payload = sweep_payload()
    payload[field] = bad
    path = write_sweep(root, payload)
    before = path.read_bytes()

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_sweep"] == {"available": False, "reason": "invalid"}
    assert "PRIVATE" not in json.dumps(result)
    assert api._scan_status == {}
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad_count", [True, -1, 1.5, "PRIVATE_COUNT", 10**9 + 1])
def test_auto_sweep_mail_counts_are_projected_before_admin_exposure(admin_status_io, bad_count):
    # Break caught: the sweep's raw mail diagnostic object bypasses the same
    # non-coercing count and fixed-identifier policy used for individual leaves.
    root, _ = admin_status_io
    payload = sweep_payload()
    payload["PRIVATE_EXTRA"] = "PRIVATE_PROVIDER_BODY"
    payload["diagnostics"]["mail_audit"] = {
        "schema_version": 1, "candidate_rows": bad_count,
        "semantics": "PRIVATE_SEMANTICS", "recipient": "PRIVATE_RECIPIENT",
        "reason_occurrences": {"score_below_alert_threshold": bad_count, "missing_gmail_config": 7, "PRIVATE_REASON": 5},
        "transport_events": {"trade_accepted": bad_count, "trade_failed": 2, "PRIVATE_EVENT": 6},
    }
    write_sweep(root, payload)

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_sweep"]["available"] is True
    assert result["delivery"]["stock_sweep"]["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "reason_occurrences": {"missing_gmail_config": 7},
        "transport_events": {"trade_failed": 2},
    }
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("file_problem", ["duplicate_key", "oversized", "directory"])
def test_auto_sweep_reader_retains_bounded_regular_file_and_unique_json_guards(admin_status_io, monkeypatch, file_problem):
    # Break caught: the new sweep path skips the bounded read, regular-file
    # validation or duplicate-object-key rejection used by the leaf path.
    root, _ = admin_status_io
    path = root / "stock_strategy_sweep_attempt.json"
    if file_problem == "directory":
        path.mkdir()
    else:
        path = write_sweep(root, sweep_payload())
        if file_problem == "oversized":
            monkeypatch.setattr(api, "_STOCK_ATTEMPT_READ_MAX_BYTES", 8)
        else:
            raw = json.dumps(sweep_payload())
            path.write_text('{"schema_version":1,' + raw[1:], encoding="utf8")

    result = api.get_email_alert_audit("offline-admin")

    assert result["delivery"]["stock_sweep"] == {"available": False, "reason": "invalid"}
    assert api._scan_status == {}


def test_admin_keeps_auto_sweep_mail_counts_separate_from_individual_leaf_mail_counts(admin_status_io):
    # Break caught: a combined automatic send's counters are attributed to a
    # leaf or added to a separate manual leaf's mail evidence.
    root, _ = admin_status_io
    sweep = sweep_payload()
    write_sweep(root, sweep)
    leaf = attempt_payload(status="complete")
    leaf.update(started_at="2026-09-14T12:02:00+00:00", updated_at="2026-09-14T12:04:00+00:00")
    leaf["diagnostics"]["mail_audit"] = {
        "schema_version": 1, "candidate_rows": 4,
        "reason_occurrences": {"missing_gmail_config": 4},
        "transport_events": {"trade_sender_called": 3, "trade_failed": 3},
    }
    write_attempt(root, leaf)

    result = api.get_email_alert_audit("offline-admin")

    automatic = result["delivery"]["stock_sweep"]
    individual = result["delivery"]["stock_attempts"]
    assert automatic["attempt_run_id"] == "c" * 32
    assert automatic["updated_at"] == "2026-09-14T11:58:57+00:00"
    assert automatic["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "candidate_rows": 2,
        "reason_occurrences": {"score_below_alert_threshold": 1},
        "transport_events": {"trade_accepted": 1, "trade_sender_called": 1},
    }
    assert len(individual) == 1
    assert individual[0]["strategy"] == "Momentum Breakout Long"
    assert individual[0]["attempt_run_id"] == "a" * 32
    assert individual[0]["updated_at"] == "2026-09-14T12:04:00+00:00"
    assert individual[0]["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "candidate_rows": 4,
        "reason_occurrences": {"missing_gmail_config": 4},
        "transport_events": {"trade_failed": 3, "trade_sender_called": 3},
    }


def test_actual_auto_sweep_captures_real_sender_boundary_without_attributing_mail_to_leaf(admin_status_io, monkeypatch):
    # Break caught: the combined guard's real sender call is not persisted with
    # the automatic attempt, or auto leaves regain send_email=True and inherit
    # mail permission. The provider leaf is isolated; capture, sender guard,
    # attempt publication, disk read and Admin consumer remain real.
    root, _ = admin_status_io
    monkeypatch.setattr(api, "_stock_attempt_run_ids", {})
    monkeypatch.setattr(api, "_AUTO_STOCK_ALERT_STRATEGIES", [
        "Momentum Breakout Long", "Gap Momentum Long", "Gap Momentum Short", "Cup and Handle Breakout",
    ])
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(api, "_scan_control_point", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_SECRETS", {})
    monkeypatch.setattr(api, "_EMAIL_DELIVERY_CONTEXT", local())
    monkeypatch.setattr(api, "_record_email_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "record_suppressions", lambda *args, **kwargs: 0)
    monkeypatch.setattr(api.smtplib, "SMTP", lambda *args, **kwargs: pytest.fail("No SMTP in sweep publication tests"))
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", lambda *args, **kwargs: pytest.fail("No SMTP in sweep publication tests"))
    leaf_path = write_attempt(root, attempt_payload(status="complete"))
    leaf_before = leaf_path.read_bytes()
    _, _, observed = _mock_sweep(monkeypatch, root, {
        "Momentum Breakout Long": [_row("PRIVATE_CURRENT")],
    })

    def real_sender_boundary(name, rows, market):
        assert name == "Aktien Auto-Sweep" and market == "stocks" and len(rows) == 1
        assert api._send_email_alert(
            "Offline automatic mail", "<p>Offline sender boundary</p>",
            bypass_startup_cooldown=True,
        ) is False

    monkeypatch.setattr(api, "_send_strategy_scan_alerts", real_sender_boundary)

    api._stock_strategy_alert_sweep_wrapper()
    result = api.get_email_alert_audit("offline-admin")

    # _mock_sweep also rejects any leaf call not explicitly send_email=False
    # and publish_generic_cache=False, preserving the automatic ownership split.
    assert observed["attempts"] == [
        "Momentum Breakout Long", "Gap Momentum Long", "Gap Momentum Short", "Cup and Handle Breakout",
    ]
    automatic = result["delivery"]["stock_sweep"]
    assert automatic["available"] is True and automatic["status"] == "complete"
    assert automatic["strategy_slug"] == "stock_strategy_sweep"
    assert automatic["result_count"] == 1
    assert automatic["started_at"] == "2026-09-14T15:00:00+00:00"
    assert automatic["updated_at"] == "2026-09-14T15:00:00+00:00"
    assert automatic["status_scope"] == "persisted_attempt_not_live_worker"
    assert automatic["mail_audit"] == {
        "schema_version": 1,
        "semantics": "overlapping_reason_occurrences_and_message_events_not_inbox_delivery",
        "candidate_rows": 1,
        "reason_occurrences": {"missing_gmail_config": 1},
        "transport_events": {"trade_sender_called": 1},
    }
    assert len(result["delivery"]["stock_attempts"]) == 1
    assert "mail_audit" not in result["delivery"]["stock_attempts"][0]
    assert result["delivery"]["smtp_acceptance_is_inbox_delivery"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert leaf_path.read_bytes() == leaf_before
    assert api._scan_status == {}
    assert api._EMAIL_SEND_LOG == []
    assert api._EMAIL_COOLDOWN == {"offline-existing-entry": 123.0}
