"""Persistent Cup watches: exchange sessions, finite evidence, one-shot delivery."""
from datetime import datetime, timezone

import pytest

import api
from modules import cup_handle_watch_queue as queue
from test_cup_delivery_paths import _row, _monitor_fixture, NOW


def _entry(monkeypatch, **changes):
    return dict(id="CUPX|2026-08-28|2026-08-31", ticker="CUPX",
                confirmation_date="2026-08-28", target_session_date="2026-08-31",
                breakout_level=101.2, expires_at=NOW.timestamp()+3600,
                created_at=NOW.timestamp()-60, updated_at=NOW.timestamp(),
                row=_row(monkeypatch)) | changes


@pytest.mark.parametrize("changes", [
    {"breakout_level": True}, {"breakout_level": float("inf")},
    {"expires_at": float("inf")}, {"expires_at": True},
    {"lease_until": float("nan")}, {"generation": True}, {"generation": 1.9},
    {"confirmation_date": "invalid"}, {"target_session_date": "2026-08-30"},
    {"confirmation_date": "2026-08-31"},
])
def test_cup_watch_store_rejects_corrupt_clock_price_or_session(monkeypatch, tmp_path, changes):
    path = tmp_path/"watch.json"
    assert queue.upsert_watch(_entry(monkeypatch, **changes), path=path) is False
    assert not path.exists()


def test_cup_watch_expiry_follows_real_early_close_and_rejects_weekend():
    expected = datetime(2026, 11, 27, 18, 15, tzinfo=timezone.utc).timestamp()
    assert api._cup_handle_watch_expiry_ts("2026-11-27") == expected
    assert api._cup_handle_watch_expiry_ts("2026-11-28") is None


@pytest.mark.parametrize("generation", [True, 1.9, "1", -1])
def test_finish_claim_never_coerces_another_generation_identity(monkeypatch, tmp_path, generation):
    path = tmp_path / "strict-generation.json"
    assert queue.upsert_watch(_entry(monkeypatch), path=path)
    claimed = queue.claim_for_session("2026-08-31", now_ts=NOW.timestamp(), path=path)
    assert len(claimed) == 1
    item = claimed[0]
    assert item["generation"] == 1
    assert not queue.finish_claim(item["id"], item["lease_owner"], remove=True,
                                  generation=generation, path=path)
    assert queue.queue_snapshot(path=path)["items"][item["id"]]["generation"] == 1
    assert queue.finish_claim(item["id"], item["lease_owner"], remove=True,
                              generation=1, path=path)


@pytest.mark.parametrize("delivery", ["lease_only", "failed", "accepted"])
def test_confirmed_cup_watch_is_removed_only_after_accepted_delivery(monkeypatch, tmp_path, delivery):
    finished, suppressed = _monitor_fixture(monkeypatch, _row(monkeypatch))
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path/"dedupe.json"))
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    # This test isolates the monitor/actual durable dedupe boundary. Pattern
    # and market math have their independent producer/final-mail tests.
    key = "stock_strategy_CUPX_deepwatch"
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *_a: {"cooldown_key": key})
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", lambda *_a, **_k: {
        "confirmed": True, "trigger_type": "5m_retest_held",
        "trigger_observed_ts": NOW.timestamp()-60, "reason": "closed_5m_retest_held"})
    def sender(*_args):
        assert api._email_dedupe_claim(key, 3600, now=NOW.timestamp())
        if delivery == "accepted":
            api._email_dedupe_mark(key, now=NOW.timestamp())
        elif delivery == "failed":
            assert api._email_dedupe_release(key, claimed_at=NOW.timestamp())
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", sender)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result["triggered"] == 1
    assert result["completed"] == int(delivery == "accepted")
    assert finished[0][1]["remove"] is (delivery == "accepted")


@pytest.mark.parametrize("receipt", [True, float("inf"), float("nan"),
                                     NOW.timestamp() + 60, NOW.timestamp() - 30*86400])
@pytest.mark.parametrize("source", ["memory", "durable"])
def test_old_or_corrupt_send_receipt_cannot_complete_current_cup_watch(monkeypatch, tmp_path, receipt, source):
    finished, _ = _monitor_fixture(monkeypatch, _row(monkeypatch))
    key = "stock_strategy_CUPX_receipt_audit"
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {key: receipt} if source == "memory" else {})
    monkeypatch.setattr(api, "_load_email_dedupe", lambda: {key: receipt} if source == "durable" else {})
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *_a: {"cooldown_key": key})
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", lambda *_a, **_k: {
        "confirmed": True, "trigger_type": "5m_retest_held", "trigger_observed_ts": NOW.timestamp()-60})
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", lambda *_a: None)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result["triggered"] == 1
    assert result["completed"] == 0
    assert finished[0][1]["remove"] is False
