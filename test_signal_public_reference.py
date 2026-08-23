"""Public, immutable signal-reference delivery contracts."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

import pytest

from modules import signal_tracker as tracker


def _row(ticker: str, *, entry: float = 100.0) -> dict:
    return {
        "Ticker": ticker,
        "Signal_Direction": "LONG",
        "Entry": entry,
        "StopLoss": entry - 5.0,
        "TP1": entry + 5.0,
        "TP2": entry + 10.0,
        "strategy": "public-reference-contract",
        "trade_horizon": "swing",
    }


@pytest.fixture()
def isolated_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", str(tmp_path / "signals.sqlite"))
    monkeypatch.setattr(
        tracker,
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
        str(tmp_path / "acceptance.sqlite"),
    )
    return tracker


def _db_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            return [dict(row) for row in conn.execute("SELECT * FROM signals ORDER BY id")]
        except sqlite3.OperationalError as exc:
            if "no such table: signals" in str(exc):
                return []
            raise
    finally:
        conn.close()


def _canonical_row_token(row: dict) -> str:
    fields = tracker._prepare_identity_fields(
        tracker.extract_signal_fields(row), "breakout", "stock"
    )
    return tracker._canonical_delivery_row_token("breakout", fields, "stock")


def test_legacy_nulls_and_direct_shadow_origins_are_explicit(isolated_tracker):
    assert isolated_tracker.record_alert_signals("breakout", [_row("DIRECT")]) == 1
    assert isolated_tracker.record_alert_signals(
        "breakout", [_row("SHADOW")], mail_class="shadow"
    ) == 1

    rows = {row["ticker"]: row for row in _db_rows(isolated_tracker.SIGNAL_DB_PATH)}
    assert rows["DIRECT"]["public_signal_ref"] is None
    assert rows["DIRECT"]["origin_evidence"] == "direct_post_send"
    assert rows["SHADOW"]["public_signal_ref"] is None
    assert rows["SHADOW"]["origin_evidence"] == "shadow_counterfactual"


def test_migrated_legacy_row_keeps_null_public_reference_and_unknown_origin(isolated_tracker):
    conn = sqlite3.connect(isolated_tracker.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "CREATE TABLE signals (id INTEGER PRIMARY KEY, created_at TEXT, scanner TEXT, "
            "ticker TEXT, status TEXT, setup_key TEXT, mail_class TEXT)"
        )
        conn.execute(
            "INSERT INTO signals VALUES (1, '2026-08-21T00:00:00+00:00', 'legacy', 'OLD', 'OPEN', NULL, 'trade')"
        )
        conn.commit()
    finally:
        conn.close()

    with isolated_tracker._DB_LOCK:
        with isolated_tracker._db_connection() as conn:
            row = conn.execute(
                "SELECT public_signal_ref, origin_evidence FROM signals WHERE id=1"
            ).fetchone()
    assert tuple(row) == (None, None)
    assert isolated_tracker.normalize_origin_evidence(row[1]) == "legacy_origin_unknown"


def test_public_reference_validation_is_central_and_exact(isolated_tracker):
    assert isolated_tracker.is_valid_public_signal_ref("AS1-0123456789ABCDEF0123") is True
    assert isolated_tracker.is_valid_public_signal_ref("AS1-0123456789abcdef0123") is False
    assert isolated_tracker.is_valid_public_signal_ref("signal-1") is False


def test_prepared_refs_are_immutable_and_become_smtp_evidence(isolated_tracker):
    intent = "public-ref-batch"
    prepared = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", [_row("PREPARED")], intent
    )

    assert prepared["send_allowed"] is True
    signal = prepared["signals"][0]
    public_ref = signal["public_signal_ref"]
    assert re.fullmatch(r"AS1-[0-9A-F]{20}", public_ref)
    assert signal["origin_evidence"] == "delivery_prepared"

    retry = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", [_row("PREPARED")], intent
    )
    assert retry["signals"][0]["public_signal_ref"] == public_ref
    assert isolated_tracker.finalize_alert_delivery(
        intent, ["a" * 64], accepted_at=datetime.now(timezone.utc)
    )["activated"] is True
    accepted = _db_rows(isolated_tracker.SIGNAL_DB_PATH)[0]
    assert accepted["public_signal_ref"] == public_ref
    assert accepted["origin_evidence"] == "smtp_acceptance"


@pytest.mark.parametrize(
    ("corrupt_column", "corrupt_value"),
    [
        ("public_signal_ref", "not-a-public-ref"),
        ("origin_evidence", "direct_post_send"),
    ],
)
def test_finalize_rejects_corrupt_prepared_evidence_without_activation(
    isolated_tracker, corrupt_column, corrupt_value
):
    intent = "finalize-evidence-integrity"
    prepared = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", [_row("EVIDENCE-A"), _row("EVIDENCE-B", entry=120.0)], intent
    )
    assert prepared["send_allowed"] is True
    refs = [signal["public_signal_ref"] for signal in prepared["signals"]]
    conn = sqlite3.connect(isolated_tracker.SIGNAL_DB_PATH)
    try:
        # The partial unique index prevents a duplicate external identity.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE signals SET public_signal_ref=? WHERE ticker='EVIDENCE-B'",
                (refs[0],),
            )
        conn.execute(
            f"UPDATE signals SET {corrupt_column}=? WHERE ticker='EVIDENCE-A'",
            (corrupt_value,),
        )
        conn.commit()
    finally:
        conn.close()

    finalized = isolated_tracker.finalize_alert_delivery(
        intent, ["b" * 64], accepted_at=datetime.now(timezone.utc)
    )

    assert finalized["activated"] is False
    assert finalized["intent_state"] == "INCONSISTENT"
    rows = _db_rows(isolated_tracker.SIGNAL_DB_PATH)
    assert [(row["status"], row["delivery_state"]) for row in rows] == [
        (isolated_tracker.STATUS_PENDING_DELIVERY, "PREPARED"),
        (isolated_tracker.STATUS_PENDING_DELIVERY, "PREPARED"),
    ]
    assert all(row["origin_evidence"] != "smtp_acceptance" for row in rows)


def test_same_ticker_different_plans_and_reordered_retry_keep_row_refs(isolated_tracker):
    intent = "public-ref-reordered"
    first_rows = [_row("SAME", entry=100.0), _row("SAME", entry=120.0)]
    first = isolated_tracker.prepare_alert_delivery_intent("breakout", first_rows, intent)
    assert first["send_allowed"] is True
    first_by_entry = {
        row["entry"]: row["public_signal_ref"]
        for row in _db_rows(isolated_tracker.SIGNAL_DB_PATH)
    }
    assert len(first_by_entry) == 2

    retry = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", list(reversed(first_rows)), intent
    )
    assert retry["send_allowed"] is True
    assert [signal["entry"] for signal in retry["signals"]] == [120.0, 100.0]
    assert [signal["public_signal_ref"] for signal in retry["signals"]] == [
        first_by_entry[120.0],
        first_by_entry[100.0],
    ]
    assert {
        row["entry"]: row["public_signal_ref"]
        for row in _db_rows(isolated_tracker.SIGNAL_DB_PATH)
    } == first_by_entry


def test_duplicate_canonical_row_identity_fails_closed_without_fallback_ref(isolated_tracker):
    row = _row("AMBIGUOUS")
    prepared = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", [row, dict(row)], "public-ref-ambiguous"
    )

    assert prepared["send_allowed"] is False


def test_forced_public_ref_collision_fails_closed_without_partial_intent(
    isolated_tracker, monkeypatch
):
    monkeypatch.setattr(
        isolated_tracker, "_public_signal_reference", lambda *_args: "AS1-AAAAAAAAAAAAAAAAAAAA"
    )

    prepared = isolated_tracker.prepare_alert_delivery_intent(
        "breakout",
        [_row("COLLISION-A"), _row("COLLISION-B")],
        "public-ref-forced-collision",
    )

    assert prepared["send_allowed"] is False
    assert _db_rows(isolated_tracker.SIGNAL_DB_PATH) == []


def test_existing_public_ref_collision_rolls_back_the_entire_new_intent(
    isolated_tracker, monkeypatch
):
    original_ref_builder = isolated_tracker._public_signal_reference
    target_a = _row("TARGET-A")
    target_b = _row("TARGET-B", entry=120.0)
    target_a_token = _canonical_row_token(target_a)
    collision_ref = "AS1-ABCDEF0123456789ABCD"

    def _colliding_ref(intent_key, row_token):
        if intent_key == "seed-intent" or (
            intent_key == "target-intent" and row_token == target_a_token
        ):
            return collision_ref
        return original_ref_builder(intent_key, row_token)

    monkeypatch.setattr(isolated_tracker, "_public_signal_reference", _colliding_ref)
    seed = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", [_row("SEED")], "seed-intent"
    )
    assert seed["send_allowed"] is True
    before = _db_rows(isolated_tracker.SIGNAL_DB_PATH)

    collided = isolated_tracker.prepare_alert_delivery_intent(
        "breakout", [target_a, target_b], "target-intent"
    )

    assert collided["send_allowed"] is False
    assert _db_rows(isolated_tracker.SIGNAL_DB_PATH) == before
