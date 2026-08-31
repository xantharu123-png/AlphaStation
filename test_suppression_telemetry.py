"""Durable suppression observability without signal/customer data leakage."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import asyncio
import ast
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time

import pytest

import api
from modules import suppression_telemetry as telemetry
from modules import regime_filter
from modules import penny_stock_scanner


def _bi_contract_row(ticker, green=17):
    checks = [
        {
            "id": f"indicator_{index}",
            "key": f"indicator_{index}",
            "available": True,
            "passed": index <= green,
        }
        for index in range(1, 21)
    ]
    return {
        "ticker": ticker,
        "BI_IndicatorChecks": checks,
        "BI_IndicatorsGreen": green,
        "BI_IndicatorsAvailable": 20,
        "BI_IndicatorsTotal": 20,
        "BI_IndicatorsRequired": 17,
        "BI_IndicatorContractOK": True,
        "BI_IndicatorContractVersion": "stock-bi-20-v1",
    }


def _multiprocess_suppression_write(args):
    db_path, observed_at = args
    from modules import suppression_telemetry as process_telemetry

    return process_telemetry.record_suppressions(
        "orb",
        {"final_quote_stale": 1},
        code_revision="123456789abc",
        observed_at=observed_at,
        db_path=db_path,
    )


def _multiprocess_drop_marker(args):
    db_path, count = args
    from modules import suppression_telemetry as process_telemetry

    process_telemetry._record_dropped_write(
        count, db_path=db_path, error_class="sqlite_busy"
    )
    return True


def _advisory_drop_lock_then_exit(db_path):
    from modules import suppression_telemetry as process_telemetry

    path = process_telemetry._drop_journal_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    fd = process_telemetry._acquire_drop_journal_lock(path, timeout=1.0)
    if fd is None:
        raise RuntimeError("could not acquire test lock")
    import os

    os._exit(0)


def test_counters_aggregate_by_scanner_reason_revision_and_hour(tmp_path):
    db = tmp_path / "suppression.sqlite"
    first = 1_800_000_100.0

    assert telemetry.record_suppressions(
        "stock_strategy",
        {"final_quote_stale": 2, "final_market_path_missing": 1},
        code_revision="abcdef123456",
        observed_at=first,
        db_path=str(db),
    ) == 3
    assert telemetry.record_suppressions(
        "stock_strategy",
        {"final_quote_stale": 3},
        code_revision="abcdef123456",
        observed_at=first + 30,
        db_path=str(db),
    ) == 3
    assert telemetry.record_suppressions(
        "stock_strategy",
        {"final_quote_stale": 4},
        code_revision="fedcba654321",
        observed_at=first + 60,
        db_path=str(db),
    ) == 4

    summary = telemetry.load_suppression_summary(
        hours=24,
        now=first + 120,
        db_path=str(db),
    )

    assert summary["available"] is True
    assert summary["status"] == "healthy"
    assert summary["count_unit"] == "reason_occurrences"
    assert summary["reason_occurrences"] == 10
    assert summary["total_count"] == 10
    assert summary["by_scanner"] == [{
        "scanner": "stock_strategy",
        "count": 10,
        "last_seen_at": summary["by_scanner"][0]["last_seen_at"],
    }]
    rows = {
        (row["reason"], row["code_revision"]): row["count"]
        for row in summary["top_reasons"]
    }
    assert rows == {
        ("final_quote_stale", "abcdef123456"): 5,
        ("final_quote_stale", "fedcba654321"): 4,
        ("final_market_path_missing", "abcdef123456"): 1,
    }


def test_free_form_sensitive_values_are_rejected_and_never_persisted(tmp_path):
    db = tmp_path / "suppression.sqlite"
    recipient = "alice@example.com"
    symbol = "PRIVATECOIN"

    recorded = telemetry.record_suppressions(
        "stock_strategy",
        {
            "final_quote_stale": 1,
            "aapl": 5,
            "alice": 6,
            f"quote_for_{symbol}_at_123.45": 7,
            f"recipient:{recipient}": 9,
        },
        code_revision="not-a-revision-with-private-data",
        observed_at=1_800_000_000.0,
        db_path=str(db),
    )

    assert recorded == 1
    summary = telemetry.load_suppression_summary(
        now=1_800_000_001.0,
        db_path=str(db),
    )
    serialized = json.dumps(summary)
    raw_database = db.read_bytes()
    assert recipient not in serialized
    assert symbol not in serialized
    assert "aapl" not in serialized
    assert "alice" not in serialized
    assert recipient.encode() not in raw_database
    assert symbol.encode() not in raw_database
    assert b"aapl" not in raw_database
    assert b"alice" not in raw_database
    assert summary["top_reasons"][0]["code_revision"] == "unknown"

    with sqlite3.connect(str(db)) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(suppression_buckets)")
        }
    assert columns == {
        "bucket_start",
        "scanner",
        "reason",
        "code_revision",
        "first_seen_at",
        "last_seen_at",
        "event_count",
    }


def test_concurrent_increments_are_atomic(tmp_path):
    db = tmp_path / "suppression.sqlite"
    observed_at = 1_800_000_000.0

    def _write(_index):
        return telemetry.record_suppressions(
            "orb",
            {"final_quote_stale": 1},
            code_revision="123456789abc",
            observed_at=observed_at,
            db_path=str(db),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_write, range(40)))

    assert results == [1] * 40
    summary = telemetry.load_suppression_summary(
        now=observed_at + 1,
        db_path=str(db),
    )
    assert summary["total_count"] == 40


def test_multiprocess_upserts_preserve_each_reason_occurrence(tmp_path):
    db = tmp_path / "multiprocess.sqlite"
    observed_at = 1_800_000_000.0
    assert _multiprocess_suppression_write((str(db), observed_at)) == 1

    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                _multiprocess_suppression_write,
                [(str(db), observed_at)] * 8,
            )
        )

    assert results == [1] * 8
    summary = telemetry.load_suppression_summary(
        now=observed_at + 1, db_path=str(db)
    )
    assert summary["reason_occurrences"] == 9


def test_cross_process_drop_marker_degrades_api_reader_summary(tmp_path):
    db = tmp_path / "cross-process.sqlite"
    with ProcessPoolExecutor(max_workers=1) as pool:
        assert list(pool.map(_multiprocess_drop_marker, [(str(db), 3)])) == [
            True
        ]

    summary = telemetry.load_suppression_summary(db_path=str(db))

    assert summary["available"] is True
    assert summary["status"] == "degraded"
    assert summary["dropped_write_reason_occurrences_window"] == 3
    assert summary["dropped_write_reason_occurrences"] == 3
    assert summary["last_drop_class"] == "sqlite_busy"
    assert summary["last_drop_at"] is not None
    assert summary["last_dropped_write_at"] is not None
    serialized = json.dumps(summary)
    assert "cross-process" not in serialized


def test_old_drop_recovers_to_healthy_outside_24_hour_window(tmp_path):
    db = tmp_path / "recovered.sqlite"
    now = 1_800_000_000.0
    telemetry._record_dropped_write(
        4,
        db_path=str(db),
        error_class="sqlite_busy",
        observed_at=now - 25 * 3600,
    )

    summary = telemetry.load_suppression_summary(now=now, db_path=str(db))

    assert summary["writable"] is True
    assert summary["status"] == "healthy"
    assert summary["dropped_write_reason_occurrences_window"] == 0
    assert summary["last_drop_at"] is None


def test_drop_window_counts_only_recent_reason_occurrences(tmp_path):
    db = tmp_path / "window.sqlite"
    now = 1_800_000_000.0
    telemetry._record_dropped_write(
        7, db_path=str(db), observed_at=now - 25 * 3600
    )
    telemetry._record_dropped_write(
        2, db_path=str(db), observed_at=now - 3600
    )
    telemetry._record_dropped_write(
        3, db_path=str(db), observed_at=now - 60
    )

    summary = telemetry.load_suppression_summary(now=now, db_path=str(db))

    assert summary["status"] == "degraded"
    assert summary["dropped_write_reason_occurrences_window"] == 5
    assert summary["last_drop_at"] == telemetry._iso(now - 60)


def test_drop_journal_is_compacted_and_bounded_on_health_read(
    tmp_path, monkeypatch
):
    db = tmp_path / "compact.sqlite"
    journal = telemetry._drop_journal_path(str(db))
    journal.parent.mkdir(parents=True, exist_ok=True)
    now = 1_800_000_000.0
    old = now - 100 * 24 * 3600
    payload = b"".join(
        f"{old + index:.6f}\t1\tio_error\n".encode("ascii")
        for index in range(100)
    ) + b"".join(
        f"{now - index:.6f}\t1\tsqlite_busy\n".encode("ascii")
        for index in range(3)
    )
    journal.write_bytes(payload)
    monkeypatch.setattr(telemetry, "_DROP_JOURNAL_MAX_READ_BYTES", 512)

    summary = telemetry.load_suppression_summary(now=now, db_path=str(db))

    assert summary["dropped_write_reason_occurrences_window"] == 3
    assert journal.stat().st_size <= 512
    assert str(old).encode("ascii") not in journal.read_bytes()


def test_drop_append_cannot_race_read_replace_compaction(
    tmp_path, monkeypatch
):
    db = tmp_path / "race.sqlite"
    journal = telemetry._drop_journal_path(str(db))
    journal.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    journal.write_text(
        f"{now - 100 * 24 * 3600:.6f}\t1\tio_error\n",
        encoding="ascii",
    )
    compaction_entered = threading.Event()
    allow_replace = threading.Event()
    original_compact = telemetry._compact_drop_journal_locked

    def _paused_compact(path, retained_lines):
        compaction_entered.set()
        assert allow_replace.wait(timeout=2)
        return original_compact(path, retained_lines)

    monkeypatch.setattr(
        telemetry, "_compact_drop_journal_locked", _paused_compact
    )
    reader = threading.Thread(
        target=telemetry.load_suppression_summary,
        kwargs={"now": now, "db_path": str(db)},
    )
    reader.start()
    assert compaction_entered.wait(timeout=2)
    writer = threading.Thread(
        target=telemetry._record_dropped_write,
        kwargs={
            "count": 2,
            "db_path": str(db),
            "error_class": "sqlite_busy",
            "observed_at": now,
        },
    )
    writer.start()
    time.sleep(0.05)
    assert writer.is_alive()
    allow_replace.set()
    reader.join(timeout=2)
    writer.join(timeout=2)
    assert not reader.is_alive()
    assert not writer.is_alive()

    persisted = journal.read_text(encoding="ascii")
    assert f"{now:.6f}\t2\tsqlite_busy" in persisted
    summary = telemetry.load_suppression_summary(
        now=now + 1, db_path=str(db)
    )
    assert summary["dropped_write_reason_occurrences_window"] == 2


def test_stale_lockfile_after_process_exit_does_not_block_new_writer(tmp_path):
    db = tmp_path / "stale-lock.sqlite"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_advisory_drop_lock_then_exit, args=(str(db),)
    )
    process.start()
    process.join(timeout=5)
    assert process.exitcode == 0
    assert telemetry._drop_lock_path(
        telemetry._drop_journal_path(str(db))
    ).exists()

    telemetry._record_dropped_write(
        2, db_path=str(db), error_class="sqlite_busy"
    )
    summary = telemetry.load_suppression_summary(db_path=str(db))

    assert summary["dropped_write_reason_occurrences_window"] == 2
    assert summary["status"] == "degraded"


def test_full_journal_preserves_new_cross_process_drop_in_overflow(tmp_path):
    db = tmp_path / "full.sqlite"
    journal = telemetry._drop_journal_path(str(db))
    journal.parent.mkdir(parents=True, exist_ok=True)
    old = time.time() - 100 * 24 * 3600
    line = f"{old:.6f}\t1\tio_error\n".encode("ascii")
    repeats = telemetry._DROP_JOURNAL_MAX_READ_BYTES // len(line) + 2
    journal.write_bytes(line * repeats)
    assert journal.stat().st_size >= telemetry._DROP_JOURNAL_MAX_READ_BYTES

    with ProcessPoolExecutor(max_workers=1) as pool:
        assert list(pool.map(_multiprocess_drop_marker, [(str(db), 3)])) == [
            True
        ]

    overflow = journal.with_name(journal.name + ".overflow")
    assert overflow.is_file()
    summary = telemetry.load_suppression_summary(db_path=str(db))
    assert summary["dropped_write_reason_occurrences_window"] == 3
    assert summary["status"] == "degraded"


def test_boundary_bucket_window_is_explicitly_approximate(tmp_path):
    db = tmp_path / "boundary.sqlite"
    now = 1_800_003_700.0
    since = now - 3600
    bucket_start = int(since // 3600) * 3600
    assert bucket_start < since
    assert telemetry.record_suppressions(
        "orb",
        {"final_quote_stale": 1},
        observed_at=since - 50,
        db_path=str(db),
    ) == 1
    assert telemetry.record_suppressions(
        "orb",
        {"final_quote_stale": 1},
        observed_at=since + 50,
        db_path=str(db),
    ) == 1

    summary = telemetry.load_suppression_summary(
        hours=1, now=now, db_path=str(db)
    )

    assert summary["reason_occurrences"] == 2
    assert summary["window_is_approximate"] is True
    assert summary["window_semantics"] == (
        "approximate_hour_bucket_by_last_seen"
    )
    assert summary["window_start_at"] == telemetry._iso(since)
    assert summary["window_start_bucket_at"] == telemetry._iso(bucket_start)
    assert summary["first_seen_at"] < summary["window_start_at"]


def test_busy_database_read_fails_safe_without_long_health_delay(tmp_path):
    db = tmp_path / "locked.sqlite"
    with sqlite3.connect(str(db)) as setup:
        setup.executescript(telemetry._SCHEMA)
        setup.execute("PRAGMA journal_mode=DELETE")

    blocker = sqlite3.connect(str(db), timeout=0)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        summary = telemetry.load_suppression_summary(db_path=str(db))
        elapsed = time.monotonic() - started
    finally:
        blocker.rollback()
        blocker.close()

    assert summary["available"] is False
    assert elapsed < 0.75


def test_dropped_write_is_observable_without_blocking_or_identity_data(
    tmp_path, monkeypatch
):
    db = tmp_path / "unavailable.sqlite"
    path_key = str(telemetry._drop_journal_path(str(db)).absolute())
    before = telemetry._DROPPED_WRITE_BY_PATH.get(path_key, 0)

    @telemetry.contextmanager
    def _fail_connection(*_args, **_kwargs):
        raise sqlite3.OperationalError("busy private payload")
        yield

    monkeypatch.setattr(telemetry, "_connection", _fail_connection)
    try:
        assert telemetry.record_suppressions(
            "orb", {"final_quote_stale": 2}, db_path=str(db)
        ) == 0
        summary = telemetry.load_suppression_summary(db_path=str(db))
        assert summary["status"] == "degraded"
        assert summary["dropped_write_reason_occurrences"] == before + 2
        assert "private payload" not in json.dumps(summary)
    finally:
        telemetry._DROPPED_WRITE_BY_PATH[path_key] = before
        telemetry._DROPPED_WRITE_EVENTS_BY_PATH.pop(path_key, None)


def test_missing_database_is_healthy_empty_with_separate_write_capability(
    tmp_path, monkeypatch
):
    db = tmp_path / "new" / "suppression.sqlite"

    summary = telemetry.load_suppression_summary(db_path=str(db))

    assert summary["available"] is True
    assert summary["initialized"] is False
    assert summary["writable"] is True
    assert summary["status"] == "healthy"
    assert summary["reason_occurrences"] == 0
    assert summary["count_unit"] == "reason_occurrences"
    assert summary["total_count"] == 0
    assert not db.exists()

    monkeypatch.setattr(telemetry, "_storage_writable", lambda _path: False)
    unwritable = telemetry.load_suppression_summary(
        db_path=str(tmp_path / "blocked" / "suppression.sqlite")
    )
    assert unwritable["available"] is True
    assert unwritable["initialized"] is False
    assert unwritable["writable"] is False
    assert unwritable["status"] == "degraded"
    assert unwritable["total_count"] == 0


def test_api_wrapper_records_revision_without_identity_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api,
        "record_suppressions",
        lambda scanner, reasons, **kwargs: calls.append(
            (scanner, reasons, kwargs)
        ) or 2,
    )

    assert api._record_suppression_counts(
        "stock_strategy",
        {"final_quote_stale": 2},
    ) == 2
    assert calls == [(
        "stock_strategy",
        {"final_quote_stale": 2},
        {"code_revision": api.BUILD_REVISION},
    )]


def test_api_wrapper_maps_unknown_dynamic_codes_without_retaining_text(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api,
        "record_suppressions",
        lambda scanner, reasons, **kwargs: calls.append(
            (scanner, reasons, kwargs)
        ) or sum(reasons.values()),
    )

    assert api._record_suppression_counts(
        "aapl",
        {"aapl": 2, "alice": 3},
    ) == 5
    assert calls == [(
        "unclassified_scanner",
        {"unclassified_code_reason": 5},
        {"code_revision": api.BUILD_REVISION},
    )]


def test_dynamic_provider_reason_families_map_to_stable_non_identity_ids(
    monkeypatch
):
    calls = []
    monkeypatch.setattr(
        api,
        "record_suppressions",
        lambda scanner, reasons, **kwargs: calls.append(
            (scanner, reasons, kwargs)
        ) or sum(reasons.values()),
    )

    raw = {
        "final_market_path_http_403_for_aapl": 1,
        "final_market_path_http_429_for_alice": 2,
        "final_market_path_http_503_for_alice": 3,
        "final_market_path_trailing_trade_watermark_not_reached": 4,
        "final_snapshot_http_403_for_aapl": 5,
        "final_snapshot_http_429_for_alice": 6,
        "final_snapshot_fetch_timeout_for_alice": 7,
        "final_snapshot_payload_decode_for_aapl": 8,
    }
    assert api._record_suppression_counts("turtle", raw) == 36
    assert calls == [(
        "turtle",
        {
            "final_market_path_access_denied": 1,
            "final_market_path_rate_limited": 2,
            "final_market_path_http_error": 3,
            "final_market_path_end_gap": 4,
            "final_snapshot_access_denied": 5,
            "final_snapshot_rate_limited": 6,
            "final_snapshot_fetch_failed": 7,
            "final_snapshot_payload_invalid": 8,
        },
        {"code_revision": api.BUILD_REVISION},
    )]
    serialized = json.dumps(calls)
    assert "aapl" not in serialized
    assert "alice" not in serialized


@pytest.mark.parametrize(
    ("raw_reason", "stable_reason"),
    [
        ("final_last_trade_after_quote_timestamp", "final_watermark_invalid"),
        ("final_quote_older_than_market_path", "final_watermark_invalid"),
        ("final_market_path_not_realtime", "final_watermark_invalid"),
        ("final_handshake_quote_timestamp_missing", "final_handshake_invalid"),
        ("final_handshake_quote_timestamp_regressed", "final_handshake_invalid"),
        ("final_incremental_market_path_bounds_missing", "final_incremental_gap"),
        ("final_incremental_market_path_start_gap", "final_incremental_gap"),
        ("final_incremental_market_path_end_gap", "final_incremental_gap"),
        ("final_incremental_quote_older_than_market_path", "final_incremental_gap"),
        ("final_stop_already_breached", "final_already_touched"),
        ("final_tp1_already_reached", "final_already_touched"),
        ("final_live_trade_stop_invalid", "final_live_geometry_invalid"),
        ("final_revalidation_round_limit", "final_round_limit_reached"),
        ("final_revalidation_advance_failed", "final_advance_failed"),
    ],
)
def test_new_final_reason_families_have_stable_privacy_safe_ids(
    raw_reason, stable_reason
):
    assert api._stable_suppression_reason(raw_reason) == stable_reason
    assert stable_reason in telemetry.ALLOWED_SUPPRESSION_REASONS


def test_explicit_scanner_registry_covers_non_generic_signal_families():
    assert {
        "turtle",
        "strategy_scan",
        "crypto_trade_signals",
        "crypto_explosion",
        "volume_spikes",
        "unclassified_scanner",
    }.issubset(telemetry.ALLOWED_SUPPRESSION_SCANNERS)


def test_literal_suppression_scanner_calls_are_allowlisted():
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    literal_scanners = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_record_suppression_counts"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert literal_scanners <= telemetry.ALLOWED_SUPPRESSION_SCANNERS


def test_cup_watch_unconfirmed_claim_is_counted_once_by_owner(monkeypatch):
    calls = []
    finished = []
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *_a: {"allowed": True, "session": "US_REGULAR"},
    )
    monkeypatch.setattr(
        api, "_previous_us_exchange_trading_date_str", lambda _date: "2026-08-28"
    )
    monkeypatch.setattr(
        api,
        "_claim_cup_handle_watches",
        lambda *_a, **_k: [{
            "id": "claim-1",
            "lease_owner": "owner",
            "generation": 1,
            "ticker": "ONE",
            "breakout_level": 10.0,
            "confirmation_date": "2026-08-28",
            "target_session_date": "2026-08-31",
            "row": {"ticker": "ONE"},
        }],
    )
    monkeypatch.setattr(
        api,
        "_cup_handle_next_session_trigger_state",
        lambda *_a, **_k: {
            "confirmed": False,
            "reason": "cup_next_session_trigger_not_confirmed",
        },
    )
    monkeypatch.setattr(api, "_prune_cup_handle_watches", lambda *_a, **_k: None)
    monkeypatch.setattr(
        api,
        "_finish_cup_handle_watch_claim",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    # 2026-08-31 14:00 UTC is inside the US regular session and has the
    # expected New-York market date used by the queued claim.
    result = api._cup_handle_watch_monitor_wrapper(1_788_185_600.0)

    assert result == {"claimed": 1, "triggered": 0, "completed": 0}
    assert calls == [(
        "cup_handle_watch",
        {"cup_next_session_trigger_not_confirmed": 1},
    )]
    assert len(finished) == 1


def test_cup_watch_owner_does_not_claim_downstream_final_revalidation_reasons():
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    wrapper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_cup_handle_watch_monitor_wrapper"
    )
    string_literals = {
        node.value
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not any(reason.startswith("final_") for reason in string_literals)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_send_strategy_scan_alerts"
        for node in ast.walk(wrapper)
    )


def test_code_owned_mail_classifier_reason_emitters_are_allowlisted():
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    emitter_names = {
        "_alert_trade_health_reasons",
        "_bear_short_rule_reasons",
        "_classify_alert_candidate",
        "_classify_premarket_candidate",
        "_early_mover_long_rule_reasons",
        "_long_entry_rule_reasons",
        "_new_listing_rule_reasons",
        "_orb_signal_gate_reasons",
        "_stock_swing_rule_reasons",
        "_stock_swing_short_rule_reasons",
    }
    emitted = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in emitter_names
    ):
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "append"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                continue
            emitted.add(call.args[0].value)

    assert emitted <= telemetry.ALLOWED_SUPPRESSION_REASONS


def test_return_literal_quality_and_structural_gate_reasons_are_allowlisted():
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    helper_names = {
        "_stock_strategy_mail_quality_state",
        "_structural_barrier_alert_reason",
    }
    emitted = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in helper_names
    ):
        for constant in (
            node for node in ast.walk(function) if isinstance(node, ast.Constant)
        ):
            value = constant.value
            if not isinstance(value, str):
                continue
            if (
                value.startswith("stock_swing_mail_blocked_")
                or value.startswith("momentum_mail_blocked_")
                or value == "near_structural_barrier_wait_trigger"
            ):
                emitted.add(value)

    assert emitted
    assert emitted <= telemetry.ALLOWED_SUPPRESSION_REASONS


def test_imported_regime_reason_registry_is_allowlisted():
    regime_reasons = {
        regime_filter.REASON_MARKET_RED,
        regime_filter.REASON_MARKET_YELLOW,
        regime_filter.REASON_BREAKER_COOLDOWN,
    }
    assert regime_reasons == {
        "market_regime_red",
        "market_regime_yellow",
        "regime_cooldown",
    }
    assert regime_reasons <= telemetry.ALLOWED_SUPPRESSION_REASONS


def test_finite_penny_diagnostic_blocker_registry_is_allowlisted():
    tree = ast.parse(Path(penny_stock_scanner.__file__).read_text(encoding="utf-8"))
    helper_names = {
        "score_broad_penny_candidate",
        "build_penny_trade_plan",
        "evaluate_penny_candidate",
    }
    blocker_names = {"blockers", "cost_blockers", "hard_blockers"}
    emitted = {"invalid_structure_plan"}
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ):
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in blocker_names
                and call.func.attr == "append"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                continue
            emitted.add(call.args[0].value)
        for mapping in (
            node for node in ast.walk(function) if isinstance(node, ast.Dict)
        ):
            for key, value in zip(mapping.keys, mapping.values):
                if not (
                    isinstance(key, ast.Constant)
                    and key.value == "blockers"
                    and isinstance(value, (ast.List, ast.Tuple))
                ):
                    continue
                emitted.update(
                    item.value
                    for item in value.elts
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                )

    assert emitted <= telemetry.ALLOWED_SUPPRESSION_REASONS


def test_finite_penny_buy_revalidation_return_reasons_are_registered():
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_penny_revalidate_buy_candidate"
    )
    emitted = set()
    for returned in (
        node for node in ast.walk(function) if isinstance(node, ast.Return)
    ):
        value = returned.value
        if not isinstance(value, ast.Tuple) or len(value.elts) < 2:
            continue
        reason = value.elts[1]
        if (
            isinstance(reason, ast.Constant)
            and isinstance(reason.value, str)
            and reason.value != "ok"
        ):
            emitted.add(api._stable_suppression_reason(reason.value))

    assert "unclassified_code_reason" not in emitted
    assert emitted <= telemetry.ALLOWED_SUPPRESSION_REASONS


def test_health_exposes_only_safe_suppression_summary(monkeypatch):
    safe_summary = {
        "available": True,
        "initialized": True,
        "writable": True,
        "window_hours": 24,
        "count_unit": "reason_occurrences",
        "reason_occurrences": 3,
        "total_count": 3,
        "by_scanner": [{
            "scanner": "stock_strategy",
            "count": 3,
            "last_seen_at": "2027-01-15T08:00:00+00:00",
        }],
        "top_reasons": [{
            "scanner": "stock_strategy",
            "reason": "final_quote_stale",
            "code_revision": "abcdef123456",
            "count": 3,
            "first_seen_at": "2027-01-15T08:00:00+00:00",
            "last_seen_at": "2027-01-15T08:00:00+00:00",
        }],
    }
    monkeypatch.setattr(
        api,
        "load_suppression_summary",
        lambda **kwargs: safe_summary,
    )

    payload = api._build_system_health()

    assert payload["suppression_telemetry"] == {
        "available": True,
        "initialized": True,
        "writable": True,
        "window_hours": 24,
        "window_semantics": "approximate_hour_bucket_by_last_seen",
        "status": "healthy",
    }
    serialized = json.dumps(payload["suppression_telemetry"])
    assert "stock_strategy" not in serialized
    assert "final_quote_stale" not in serialized
    assert "abcdef123456" not in serialized
    assert "seen_at" not in serialized
    assert "@" not in serialized
    assert "price" not in serialized.lower()
    assert "count" not in payload["suppression_telemetry"]
    assert "reason_occurrences" not in payload["suppression_telemetry"]
    assert "total_count" not in payload["suppression_telemetry"]


def test_read_only_public_status_is_degraded_without_false_empty_claim(
    monkeypatch
):
    monkeypatch.setattr(
        api,
        "load_suppression_summary",
        lambda **_kwargs: {
            "available": True,
            "initialized": True,
            "writable": False,
            "window_hours": 24,
            "total_count": 7,
            "by_scanner": [],
            "top_reasons": [],
        },
    )

    payload = api._build_system_health()

    assert payload["suppression_telemetry"]["status"] == "degraded"
    warning_text = " ".join(payload["warnings"]).lower()
    assert "nicht schreibbar" in warning_text
    assert "ist leer" not in warning_text


def test_dropped_write_degraded_status_propagates_to_public_warning(
    monkeypatch
):
    monkeypatch.setattr(
        api,
        "load_suppression_summary",
        lambda **_kwargs: {
            "available": True,
            "initialized": True,
            "writable": True,
            "status": "degraded",
            "window_hours": 24,
            "count_unit": "reason_occurrences",
            "reason_occurrences": 5,
            "total_count": 5,
            "dropped_write_reason_occurrences": 2,
            "by_scanner": [],
            "top_reasons": [],
        },
    )

    payload = api._build_system_health()

    assert payload["suppression_telemetry"]["status"] == "degraded"
    assert payload["suppression_telemetry"]["writable"] is True
    warning_text = " ".join(payload["warnings"]).lower()
    assert "suppression-telemetrie ist eingeschraenkt" in warning_text
    assert "scanner" not in json.dumps(
        payload["suppression_telemetry"]
    ).lower()


def test_degraded_suppression_status_warns_commercial_readiness(monkeypatch):
    monkeypatch.setattr(
        api,
        "load_suppression_summary",
        lambda **_kwargs: {
            "available": True,
            "initialized": True,
            "writable": True,
            "status": "degraded",
            "window_hours": 24,
            "count_unit": "reason_occurrences",
            "reason_occurrences": 1,
            "total_count": 1,
            "dropped_write_reason_occurrences": 1,
            "by_scanner": [],
            "top_reasons": [],
        },
    )

    payload = asyncio.run(api.api_commercial_readiness())

    assert any(
        "suppression telemetry is degraded" in warning.lower()
        for warning in payload["warnings"]
    )


def test_commercial_readiness_keeps_safe_detailed_aggregates(monkeypatch):
    detailed = {
        "available": True,
        "initialized": True,
        "writable": True,
        "status": "healthy",
        "window_hours": 24,
        "total_count": 2,
        "by_scanner": [{"scanner": "orb", "count": 2}],
        "top_reasons": [{"scanner": "orb", "reason": "final_quote_stale", "count": 2}],
    }
    monkeypatch.setattr(
        api, "load_suppression_summary", lambda **_kwargs: detailed
    )

    payload = asyncio.run(api.api_commercial_readiness())

    assert payload["suppression_telemetry"] == detailed


def test_new_listing_watch_early_return_records_combined_counts(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_NEW_LISTING_SEND_DUMP_WATCH_EMAILS", False)
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, reasons)) or 0,
    )

    assert api._send_new_listing_watch_email(
        {},
        suppressed={"not_active_short_signal": 2},
    ) is False
    assert calls == [(
        "new_listing",
        {
            "not_active_short_signal": 2,
            "new_listing_dump_watch_emails_disabled": 1,
        },
    )]


def test_generic_dedupe_telemetry_counts_only_failed_partial_claims(
    monkeypatch, tmp_path
):
    cache = tmp_path / "generic.json"
    cache.write_text(
        json.dumps([{"ticker": "ONE"}, {"ticker": "TWO"}]),
        encoding="utf-8",
    )
    calls = []

    def _state(_scanner, row, _now=None):
        ticker = row["ticker"]
        return {
            "ticker": ticker,
            "grade": "A",
            "score": 90,
            "price": 10.0,
            "rvol": 2.0,
            "cooldown_key": ticker.lower(),
            "alertable_now": True,
            "suppression_reasons": [],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", _state)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *_: False)
    monkeypatch.setattr(
        api,
        "_email_dedupe_claim",
        lambda key, *_args, **_kwargs: key == "one",
    )
    monkeypatch.setattr(api, "_send_email_alert", lambda *_a, **_k: False)
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    api._check_and_alert("crypto_strategy", str(cache))

    assert (
        "crypto_strategy",
        {"dedupe_claim_not_owned": 1},
    ) in calls


def test_strategy_dedupe_telemetry_counts_only_failed_partial_claims(
    monkeypatch
):
    calls = []

    def _state(_scanner, row, _now=None):
        ticker = row["ticker"]
        return {
            "ticker": ticker,
            "grade": "A",
            "score": 90,
            "price": 10.0,
            "rvol": 2.0,
            "cooldown_key": ticker.lower(),
            "alertable_now": True,
            "suppression_reasons": [],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", _state)
    monkeypatch.setattr(api, "_format_alert_plan_html", lambda _row: "plan")
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(
        api,
        "_email_dedupe_claim",
        lambda key, *_args, **_kwargs: key == "one",
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    class StopAfterClaims(Exception):
        pass

    monkeypatch.setattr(
        api,
        "_regime_mail_decision",
        lambda *_a, **_k: (_ for _ in ()).throw(StopAfterClaims()),
    )

    with pytest.raises(StopAfterClaims):
        api._send_strategy_scan_alerts(
            "partial claims",
            [
                {"ticker": "ONE", "grade": "A", "score": 90},
                {"ticker": "TWO", "grade": "A", "score": 90},
            ],
            market_type="crypto",
        )

    assert (
        "crypto_strategy",
        {"dedupe_claim_not_owned": 1},
    ) in calls


def test_generic_stock_closed_counts_current_dict_candidates_once(
    monkeypatch, tmp_path
):
    cache = tmp_path / "closed.json"
    cache.write_text(
        json.dumps([
            _bi_contract_row("ONE"),
            "not-a-row",
            _bi_contract_row("TWO"),
        ]),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        api, "_stock_trade_email_allowed", lambda _scanner: (False, "closed")
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    api._check_and_alert("bi_long", str(cache))

    assert calls == [("bi_long", {"stock_session_not_executable": 2})]


def test_stock_strategy_closed_counts_candidates_once(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **_k: None)
    monkeypatch.setattr(
        api, "_stock_trade_email_status", lambda *_a: {"allowed": False}
    )
    monkeypatch.setattr(api, "_premarket_window_active", lambda *_a: False)
    monkeypatch.setattr(
        api, "_strategy_rows_daily_close_confirmed", lambda _rows: False
    )
    monkeypatch.setattr(api, "_record_email_event", lambda *_a: None)
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    api._send_strategy_scan_alerts(
        "closed", [{"ticker": "ONE"}, "bad", {"ticker": "TWO"}]
    )

    assert calls == [
        ("stock_strategy", {"stock_session_not_executable": 2})
    ]


def test_stock_strategy_daily_close_watch_has_own_stable_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **_k: None)
    monkeypatch.setattr(
        api, "_stock_trade_email_status", lambda *_a: {"allowed": False}
    )
    monkeypatch.setattr(api, "_premarket_window_active", lambda *_a: False)
    monkeypatch.setattr(
        api, "_strategy_rows_daily_close_confirmed", lambda _rows: True
    )
    monkeypatch.setattr(api, "_record_email_event", lambda *_a: None)
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    api._send_strategy_scan_alerts("watch", [{"ticker": "ONE"}])

    assert calls == [(
        "stock_strategy",
        {"daily_close_confirmed_watch_only_no_afterhours_entry": 1},
    )]


@pytest.mark.parametrize(
    ("sender", "rows"),
    [
        (api._penny_buy_email, [{"ticker": "ONE"}]),
        (api._penny_management_email, [{"ticker": "ONE"}]),
        (api._penny_exit_email, [{"ticker": "ONE"}]),
    ],
)
def test_penny_top_level_closed_gate_counts_candidates_once(
    monkeypatch, sender, rows
):
    calls = []
    monkeypatch.setattr(
        api, "_stock_trade_email_allowed", lambda _scanner: (False, "closed")
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    assert sender(rows, telemetry_scanner="penny_positions") is False
    assert calls == [
        ("penny_positions", {"stock_session_not_executable": 1})
    ]
    assert api._last_delivery_outcome() == "stock_session_not_executable"


def test_penny_position_mail_helper_call_uses_positions_attribution():
    calls = []

    def _sender(rows, *, telemetry_scanner="penny_stocks"):
        calls.append((rows, telemetry_scanner))
        return False

    rows = [{"ticker": "ONE"}]
    assert api._call_penny_mail_helper(
        _sender, rows, telemetry_scanner="penny_positions"
    ) is False
    assert calls == [(rows, "penny_positions")]


def test_penny_position_monitor_mail_calls_are_explicitly_attributed():
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    monitor = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_penny_position_monitor_wrapper"
    )
    calls = [
        node
        for node in ast.walk(monitor)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_call_penny_mail_helper"
    ]
    assert len(calls) == 3
    for call in calls:
        scanner_keyword = next(
            keyword
            for keyword in call.keywords
            if keyword.arg == "telemetry_scanner"
        )
        assert isinstance(scanner_keyword.value, ast.Constant)
        assert scanner_keyword.value.value == "penny_positions"


def test_penny_mail_revalidation_failure_is_counted_at_owner_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api, "_stock_trade_email_allowed", lambda _scanner: (True, "open")
    )
    monkeypatch.setattr(
        api,
        "_penny_revalidate_buy_candidate",
        lambda _row: (None, "private dynamic validation detail"),
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    assert api._penny_buy_email([{"ticker": "ONE"}]) is False
    assert calls == [
        ("penny_stocks", {"final_stock_revalidation_failed": 1})
    ]
    assert "private" not in json.dumps(calls)


def test_penny_mail_adjacent_quote_session_failure_is_counted_once(monkeypatch):
    calls = []
    row = {"ticker": "ONE"}
    monkeypatch.setattr(
        api, "_stock_trade_email_allowed", lambda _scanner: (True, "open")
    )
    monkeypatch.setattr(
        api,
        "_penny_revalidate_buy_candidate",
        lambda _row: ({
            "ticker": "ONE",
            "price_observed_at": None,
            "price_session": "US_REGULAR",
            "quote_evidence_verified": True,
        }, "ok"),
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: calls.append((scanner, dict(reasons))) or 0,
    )

    assert api._penny_buy_email([row]) is False
    assert calls == [
        ("penny_stocks", {"final_quote_or_session_stale": 1})
    ]
