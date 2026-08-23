"""Regressionstests fuer die persistente, atomar geleaste Mail-Outbox."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import api
from modules import mail_outbox as outbox


def _row(db_path: Path, item_id: int):
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return dict(
            conn.execute(
                "SELECT * FROM mail_outbox WHERE id=?",
                (item_id,),
            ).fetchone()
        )


def test_enqueue_normalizes_recipients_and_dedupes_identical_payload(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"

    first = outbox.enqueue(
        "Signal A",
        "<b>body</b>",
        [" b@example.com ", "a@example.com", "a@example.com", "ungueltig"],
        mail_class="trade",
        now=1_000,
        db_path=str(db_path),
    )
    duplicate = outbox.enqueue(
        "Signal A",
        "<b>body</b>",
        ["b@example.com", "a@example.com"],
        mail_class="trade",
        now=1_001,
        db_path=str(db_path),
    )

    assert first is not None
    assert duplicate == first
    items = outbox.due_items(now=1_000, db_path=str(db_path))
    assert len(items) == 1
    assert items[0]["recipients"] == ["a@example.com", "b@example.com"]
    assert items[0]["mail_class"] == "trade"
    assert items[0]["expires_at"] == 1_000 + 90 * 60

    distinct = outbox.enqueue(
        "Signal A",
        "anderer body",
        ["c@example.com"],
        mail_class="trade",
        now=1_002,
        db_path=str(db_path),
    )
    assert distinct != first
    assert len(outbox.due_items(now=1_002, db_path=str(db_path))) == 2


def test_claim_lease_is_exclusive_and_abandoned_claim_is_recovered(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Lease",
        "body",
        ["a@example.com"],
        now=2_000,
        db_path=str(db_path),
    )

    first, expired = outbox._claim_due_items(
        now=2_000, db_path=str(db_path)
    )
    second, _ = outbox._claim_due_items(
        now=2_001, db_path=str(db_path)
    )
    reclaimed, _ = outbox._claim_due_items(
        now=2_000 + outbox.CLAIM_LEASE_SECONDS + 1,
        db_path=str(db_path),
    )

    assert expired == 0
    assert [item["id"] for item in first] == [item_id]
    assert second == []
    assert [item["id"] for item in reclaimed] == [item_id]
    assert _row(db_path, item_id)["status"] == "sending"


def test_process_success_marks_sent_and_updates_stats(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Erfolg",
        "body",
        ["a@example.com"],
        mail_class="swing_trade",
        now=3_000,
        db_path=str(db_path),
    )
    delivered = []

    result = outbox.process_outbox(
        lambda item: delivered.append(item["subject"]),
        now=3_000,
        db_path=str(db_path),
    )
    state = outbox.stats(now=3_001, db_path=str(db_path))

    assert delivered == ["Erfolg"]
    assert result["sent"] == 1
    assert result["failed"] == 0
    assert _row(db_path, item_id)["status"] == "sent"
    assert _row(db_path, item_id)["expires_at"] == 3_000 + 8 * 3600
    assert state["available"] is True
    assert state["queued"] == 0
    assert state["sent"] == 1


def test_outbox_replay_keeps_already_branded_render_time_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    rendered_at = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
    stamp = api._mail_timestamp_dual(rendered_at)
    branded = api._brand_email_html(
        "Outbox Zeit",
        f"<p>Body-Zeit: {stamp}</p>",
        rendered_at=rendered_at,
    )
    outbox.enqueue(
        "Outbox Zeit",
        branded,
        ["a@example.com"],
        now=3_100,
        db_path=str(db_path),
    )
    delivered = []

    result = outbox.process_outbox(
        lambda item: delivered.append(item["body_html"]),
        now=3_100,
        db_path=str(db_path),
    )

    assert result["sent"] == 1
    assert delivered == [branded]
    assert delivered[0].count(stamp) == 2


def test_failed_delivery_uses_backoff_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Retry",
        "body",
        ["a@example.com"],
        now=4_000,
        db_path=str(db_path),
    )

    def fail(_item):
        raise TimeoutError("smtp timeout")

    failed = outbox.process_outbox(
        fail,
        now=4_000,
        db_path=str(db_path),
    )
    after_failure = _row(db_path, item_id)
    too_early = outbox.process_outbox(
        lambda _item: None,
        now=4_000 + outbox.BACKOFF_SECONDS[0] - 1,
        db_path=str(db_path),
    )
    recovered = outbox.process_outbox(
        lambda _item: None,
        now=4_000 + outbox.BACKOFF_SECONDS[0],
        db_path=str(db_path),
    )

    assert failed["failed"] == 1
    assert after_failure["status"] == "pending"
    assert after_failure["attempts"] == 1
    assert after_failure["next_attempt_at"] == 4_000 + outbox.BACKOFF_SECONDS[0]
    assert "smtp timeout" in after_failure["last_error"]
    assert too_early["sent"] == 0
    assert recovered["sent"] == 1
    assert _row(db_path, item_id)["status"] == "sent"


def test_expired_trade_mail_is_never_delivered(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Alt",
        "body",
        ["a@example.com"],
        mail_class="trade",
        now=5_000,
        db_path=str(db_path),
    )
    delivered = []

    result = outbox.process_outbox(
        lambda item: delivered.append(item),
        now=5_000 + outbox.TTL_SECONDS_BY_CLASS["trade"] + 1,
        db_path=str(db_path),
    )

    assert delivered == []
    assert result["expired"] == 1
    assert result["sent"] == 0
    assert _row(db_path, item_id)["status"] == "expired"


def test_max_attempts_moves_item_to_dead(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    monkeypatch.setattr(outbox, "MAX_ATTEMPTS", 1)
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Dead",
        "body",
        ["a@example.com"],
        now=6_000,
        db_path=str(db_path),
    )

    result = outbox.process_outbox(
        lambda _item: (_ for _ in ()).throw(RuntimeError("permanent")),
        now=6_000,
        db_path=str(db_path),
    )

    assert result["failed"] == 1
    assert result["dead"] == 1
    assert _row(db_path, item_id)["status"] == "dead"


def test_signal_update_has_short_ttl_and_content_dedupe(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"

    first = outbox.enqueue(
        "Signal-Update",
        "body-v1",
        ["a@example.com"],
        mail_class="signal_update",
        delivery_dedupe_keys=["signal_update_7_STOP_HIT_recipient_abc"],
        now=7_000,
        db_path=str(db_path),
    )
    exact_duplicate = outbox.enqueue(
        "Signal-Update",
        "body-v1",
        ["a@example.com"],
        mail_class="signal_update",
        delivery_dedupe_keys=["signal_update_7_STOP_HIT_recipient_abc"],
        now=7_001,
        db_path=str(db_path),
    )
    changed_content = outbox.enqueue(
        "Signal-Update",
        "body-v2",
        ["a@example.com"],
        mail_class="signal_update",
        delivery_dedupe_keys=["signal_update_7_STOP_HIT_recipient_abc"],
        now=7_002,
        db_path=str(db_path),
    )

    assert exact_duplicate == first
    assert changed_content != first
    assert _row(db_path, first)["expires_at"] == 7_000 + 15 * 60
    first_item = next(
        item
        for item in outbox.due_items(now=7_002, db_path=str(db_path))
        if item["id"] == first
    )
    assert first_item["delivery_dedupe_keys"] == [
        "signal_update_7_STOP_HIT_recipient_abc"
    ]


def test_partial_failure_retries_only_pending_recipients(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Partial",
        "body",
        ["accepted@example.com", "pending@example.com"],
        mail_class="signal_update",
        now=8_000,
        db_path=str(db_path),
    )

    class PartialDelivery(RuntimeError):
        pending_recipients = ("pending@example.com",)

    result = outbox.process_outbox(
        lambda _item: (_ for _ in ()).throw(PartialDelivery("partial")),
        now=8_000,
        db_path=str(db_path),
    )
    row = _row(db_path, item_id)

    assert result["failed"] == 1
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["recipients_json"] == '["pending@example.com"]'


def test_success_returns_delivery_dedupe_keys_for_ack(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    key = "signal_update_7_STOP_HIT_recipient_abc"
    outbox.enqueue(
        "Ack",
        "body",
        ["a@example.com"],
        mail_class="signal_update",
        delivery_dedupe_keys=[key],
        now=9_000,
        db_path=str(db_path),
    )

    result = outbox.process_outbox(
        lambda _item: None,
        now=9_000,
        db_path=str(db_path),
    )

    assert result["sent"] == 1
    assert result["sent_rows"][0]["delivery_dedupe_keys"] == [key]


def test_unknown_data_outcome_is_quarantined_without_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Uncertain",
        "body",
        ["a@example.com"],
        mail_class="signal_update",
        now=10_000,
        db_path=str(db_path),
    )

    class UnknownOutcome(RuntimeError):
        suppress_retry = True

    result = outbox.process_outbox(
        lambda _item: (_ for _ in ()).throw(UnknownOutcome("after DATA")),
        now=10_000,
        db_path=str(db_path),
    )
    row = _row(db_path, item_id)

    assert result["failed"] == 1
    assert result["uncertain"] == 1
    assert row["status"] == "uncertain"
    assert row["attempts"] == 0
    assert outbox.due_items(now=99_999, db_path=str(db_path)) == []


def test_sent_ack_failure_is_quarantined_and_never_redelivered(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Ack boundary",
        "body",
        ["a@example.com"],
        mail_class="signal_update",
        now=11_000,
        db_path=str(db_path),
    )
    deliveries = []
    original_mark_sent = outbox.mark_sent
    monkeypatch.setattr(outbox, "mark_sent", lambda *args, **kwargs: False)

    first = outbox.process_outbox(
        lambda item: deliveries.append(item["id"]),
        now=11_000,
        db_path=str(db_path),
    )
    monkeypatch.setattr(outbox, "mark_sent", original_mark_sent)
    second = outbox.process_outbox(
        lambda item: deliveries.append(item["id"]),
        now=11_000 + outbox.CLAIM_LEASE_SECONDS + 1,
        db_path=str(db_path),
    )

    assert deliveries == [item_id]
    assert first["sent"] == 0
    assert first["uncertain"] == 1
    assert first["failed"] == 1
    assert second["sent"] == 0
    assert _row(db_path, item_id)["status"] == "uncertain"


def test_abandoned_delivery_phase_is_quarantined_not_recovered(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.enqueue(
        "Crash boundary",
        "body",
        ["a@example.com"],
        now=12_000,
        db_path=str(db_path),
    )
    claimed, _expired = outbox._claim_due_items(
        now=12_000, db_path=str(db_path)
    )
    assert [item["id"] for item in claimed] == [item_id]
    assert outbox.mark_delivering(
        item_id, now=12_000, db_path=str(db_path)
    )

    assert outbox.due_items(
        now=12_000 + outbox.CLAIM_LEASE_SECONDS + 1,
        db_path=str(db_path),
    ) == []
    assert _row(db_path, item_id)["status"] == "uncertain"


def test_direct_unknown_outcome_can_be_persisted_as_uncertain(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    item_id = outbox.quarantine(
        "Direct unknown",
        "body",
        ["a@example.com"],
        mail_class="signal_update",
        delivery_dedupe_keys=["recipient-event-key"],
        error="connection lost after DATA",
        now=13_000,
        db_path=str(db_path),
    )

    assert item_id is not None
    row = _row(db_path, item_id)
    assert row["status"] == "uncertain"
    assert "after DATA" in row["last_error"]
    assert outbox.due_items(now=13_000, db_path=str(db_path)) == []
    monkeypatch.setattr(outbox.time, "time", lambda: 13_000 + 8 * 86400)
    assert outbox.has_uncertain_delivery_key(
        "recipient-event-key", db_path=str(db_path)
    ) is True


def test_unknown_quarantine_falls_back_durably_when_sqlite_write_fails(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    db_path = tmp_path / "outbox.sqlite"
    original_connect = outbox._connect

    def broken_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(outbox, "_connect", broken_connect)
    receipt = outbox.quarantine(
        "Unknown after DATA",
        "body",
        ["a@example.com"],
        delivery_dedupe_keys=["event-recipient-key"],
        now=14_000,
        db_path=str(db_path),
    )
    assert isinstance(receipt, int) and receipt < 0

    monkeypatch.setattr(outbox, "_connect", original_connect)
    monkeypatch.setattr(
        outbox.time,
        "time",
        lambda: 14_000 + outbox.CLAIM_LEASE_SECONDS + 8 * 86400,
    )
    assert outbox.has_uncertain_delivery_key(
        "event-recipient-key", db_path=str(db_path)
    ) is True
    assert outbox.due_items(
        now=14_000 + outbox.CLAIM_LEASE_SECONDS + 8 * 86400,
        db_path=str(db_path),
    ) == []
    assert outbox.stats(db_path=str(db_path))["uncertain"] == 1


def test_tracker_acceptance_journal_merges_hashes_and_keeps_earliest_time(
    tmp_path,
):
    db_path = tmp_path / "outbox.sqlite"
    key_a = hashlib.sha256(b"a@example.com").hexdigest()
    key_b = hashlib.sha256(b"b@example.com").hexdigest()

    first = outbox.record_tracker_acceptance_pending(
        "intent:42", [key_a], accepted_at=20_000, db_path=str(db_path)
    )
    second = outbox.record_tracker_acceptance_pending(
        "intent:42", [key_b], accepted_at=19_000, db_path=str(db_path)
    )

    assert first == second
    pending = outbox.load_tracker_acceptance_pending(db_path=str(db_path))
    assert len(pending) == 1
    assert pending[0]["intent_key"] == "intent:42"
    assert pending[0]["accepted_at"] == 19_000
    assert pending[0]["accepted_recipient_keys"] == sorted([key_a, key_b])
    health = outbox.stats(now=20_100, db_path=str(db_path))
    assert health["tracker_acceptance_available"] is True
    assert health["tracker_acceptance_pending_count"] == 1
    assert health["tracker_acceptance_oldest_at"] == 19_000
    journal_text = Path(
        f"{db_path}.tracker_acceptance.json"
    ).read_text(encoding="utf-8")
    assert "a@example.com" not in journal_text
    assert "b@example.com" not in journal_text
    assert json.loads(journal_text)["entries"]

    assert outbox.mark_tracker_acceptance_done(
        "intent:42", completed_at=21_000, db_path=str(db_path)
    ) is True
    assert outbox.load_tracker_acceptance_pending(db_path=str(db_path)) == []
    assert outbox.stats(now=21_001, db_path=str(db_path))[
        "tracker_acceptance_pending_count"
    ] == 0


def test_bg_service_uses_dedicated_outbox_worker():
    source = Path("bg_service.py").read_text(encoding="utf-8")

    assert "def _mail_outbox_worker_loop" in source
    assert "def _start_mail_outbox_worker" in source
    assert "_start_mail_outbox_worker(secrets)" in source
    assert '"mail_outbox":' not in source
