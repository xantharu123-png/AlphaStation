import multiprocessing
from pathlib import Path
import threading

from modules.email_dedupe import (
    email_delivery_claim,
    email_delivery_mark,
    email_delivery_release,
    email_dedupe_active,
    email_dedupe_claim,
    email_dedupe_mark,
    email_dedupe_release,
    load_email_dedupe,
)


ROOT = Path(__file__).resolve().parent


def _claim_worker(path, start_event, result_queue):
    start_event.wait(timeout=10)
    result_queue.put(email_dedupe_claim(path, "same-signal", 3600, now=1000.0))


def test_claim_is_atomic_across_processes(tmp_path):
    path = str(tmp_path / "dedupe.json")
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    workers = [
        context.Process(target=_claim_worker, args=(path, start_event, result_queue))
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    start_event.set()
    results = [result_queue.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert results.count(True) == 1
    assert results.count(False) == 3


def test_concurrent_marks_do_not_overwrite_other_keys(tmp_path):
    path = str(tmp_path / "dedupe.json")
    threads = [
        threading.Thread(target=email_dedupe_mark, args=(path, f"key-{index}"), kwargs={"now": 1000.0 + index})
        for index in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert set(load_email_dedupe(path, now=1025.0)) == {f"key-{index}" for index in range(20)}


def test_failed_sender_can_release_only_its_own_claim(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert email_dedupe_claim(path, "signal", 3600, now=1000.0)
    assert not email_dedupe_release(path, "signal", claimed_at=999.0)
    assert email_dedupe_active(path, "signal", 3600, now=1001.0)
    assert email_dedupe_release(path, "signal", claimed_at=1000.0)
    assert not email_dedupe_active(path, "signal", 3600, now=1001.0)


def test_delivery_claim_uses_short_lease_and_only_mark_starts_sent_ttl(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert email_delivery_claim(path, "signal", 8 * 3600, claim_ttl_seconds=120, now=1000.0)
    assert not email_delivery_claim(path, "signal", 8 * 3600, claim_ttl_seconds=120, now=1100.0)
    assert not email_dedupe_active(path, "signal", 8 * 3600, now=1100.0)

    # A crashed sender may be retried after the short lease, not after eight hours.
    assert email_delivery_claim(path, "signal", 8 * 3600, claim_ttl_seconds=120, now=1121.0)
    email_delivery_mark(path, "signal", now=1122.0)
    assert email_dedupe_active(path, "signal", 8 * 3600, now=1123.0)
    assert not email_delivery_claim(path, "signal", 8 * 3600, claim_ttl_seconds=120, now=1123.0)


def test_delivery_release_never_deletes_a_sent_marker(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert email_delivery_claim(path, "signal", 3600, now=1000.0)
    email_delivery_mark(path, "signal", now=1001.0)
    assert not email_delivery_release(path, "signal", claimed_at=1000.0)
    assert email_dedupe_active(path, "signal", 3600, now=1002.0)


def test_api_and_background_service_share_atomic_dedupe_helpers():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    bg_source = (ROOT / "bg_service.py").read_text(encoding="utf-8")
    for source in (api_source, bg_source):
        assert "from modules.email_dedupe import" in source
        assert "_shared_email_delivery_claim" in source
        assert "_shared_email_delivery_mark" in source
        assert "claim_ttl_seconds=900" in source
    assert "_email_dedupe_release(dedupe_key, claimed_at=now)" in api_source


def _source_section(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_orb_and_penny_mailers_claim_only_rows_they_send():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    orb = _source_section(api_source, "def _orb_scanner_wrapper", "def trigger_orb_scan")
    penny = _source_section(api_source, "def _penny_stock_scanner_wrapper", "def trigger_penny_stock_scan")

    assert "_email_dedupe_claim(" in orb
    assert "_email_dedupe_release(_ck, claimed_at=_alert_now)" in orb
    assert "buy_candidates[:5]" in penny
    assert "exit_candidates[:5]" in penny
    assert "claimed_buy_candidates" in penny
    assert "claimed_exit_candidates" in penny
    assert "claimed_at=side_effect_now" in penny


def test_new_listing_invalidation_mail_uses_atomic_claim_and_rollback():
    bg_source = (ROOT / "bg_service.py").read_text(encoding="utf-8")
    invalidation = _source_section(
        bg_source,
        "def _alert_nls_invalidations",
        "def _alert_nls_signals",
    )
    assert "_email_delivery_claim(" in invalidation
    assert "invalidation_key, _NLS_INVALIDATION_DEDUPE_SEC" in invalidation
    assert "_email_delivery_release(invalidation_key, claimed_at=now)" in invalidation
    assert "_email_delivery_release_or_quarantine(" in invalidation
