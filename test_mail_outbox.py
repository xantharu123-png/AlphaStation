"""Regressionstests fuer die persistente, atomar geleaste Mail-Outbox."""

import sqlite3
from pathlib import Path

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


def test_enqueue_normalizes_recipients_and_dedupes_active_subject(
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
        "anderer body",
        ["c@example.com"],
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
    assert state["available"] is True
    assert state["queued"] == 0
    assert state["sent"] == 1


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


def test_bg_service_uses_dedicated_outbox_worker():
    source = Path("bg_service.py").read_text(encoding="utf-8")

    assert "def _mail_outbox_worker_loop" in source
    assert "def _start_mail_outbox_worker" in source
    assert "_start_mail_outbox_worker(secrets)" in source
    assert '"mail_outbox":' not in source
