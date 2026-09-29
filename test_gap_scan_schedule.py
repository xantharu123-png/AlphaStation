from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

from modules import gap_scan_schedule as gap


LONG = "strat_gap_momentum_long"
SHORT = "strat_gap_momentum_short"
ZURICH = ZoneInfo("Europe/Zurich")


def swiss(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZURICH)


def utc_text(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


@pytest.mark.parametrize("name", [LONG, SHORT])
def test_monday_swiss_start_does_not_inherit_new_york_weekend(name):
    clock = swiss("2026-09-28T02:00:00")
    assert clock.astimezone(ZoneInfo("America/New_York")).weekday() == 6
    assert gap.automatic_scan_allowed(name, clock)


@pytest.mark.parametrize("clock", ["2026-09-26T02:00:00", "2026-09-27T12:00:00"])
def test_swiss_weekends_are_excluded(clock):
    assert not gap.automatic_scan_allowed(LONG, swiss(clock))
    assert gap.due_slot(gap.scheduled_slot("2026-09-25", 12), swiss(clock)) is None


def test_new_policy_governs_only_canonical_gap_jobs():
    assert gap.is_gap_scan(LONG)
    assert gap.is_gap_scan(SHORT)
    assert not gap.is_gap_scan("strategy_scan")
    assert not gap.is_gap_scan("strat_cup_and_handle_breakout")
    assert gap.automatic_scan_allowed("bi_long", swiss("2026-09-26T02:00:00"))


@pytest.mark.parametrize("day,hour,expected", [
    ("2026-09-30", 2, "2026-09-30T00:00:00+00:00"),
    ("2026-09-30", 12, "2026-09-30T10:00:00+00:00"),
    ("2026-01-30", 2, "2026-01-30T01:00:00+00:00"),
    ("2026-01-30", 12, "2026-01-30T11:00:00+00:00"),
    ("2026-03-30", 2, "2026-03-30T00:00:00+00:00"),
    ("2026-10-26", 2, "2026-10-26T01:00:00+00:00"),
])
def test_configured_times_are_swiss_wall_clock_across_dst(day, hour, expected):
    assert utc_text(gap.scheduled_slot(day, hour)) == expected


@pytest.mark.parametrize("clock,expected", [
    ("2026-09-30T01:59:59", "2026-09-30T00:00:00+00:00"),
    ("2026-09-30T02:00:00", "2026-09-30T00:00:00+00:00"),
    ("2026-09-30T02:00:01", "2026-09-30T10:00:00+00:00"),
    ("2026-09-30T12:01:00", "2026-10-01T00:00:00+00:00"),
    ("2026-09-25T12:01:00", "2026-09-28T00:00:00+00:00"),
])
def test_empty_ledger_has_no_immediate_restart_catchup(clock, expected):
    assert utc_text(gap.initial_slot(swiss(clock))) == expected


def test_next_slot_is_strict_and_skips_weekend():
    first = gap.scheduled_slot("2026-09-25", 2)
    noon = gap.scheduled_slot("2026-09-25", 12)
    assert gap.next_slot(first) == noon
    assert gap.next_slot(noon) == gap.scheduled_slot("2026-09-28", 2)


def test_busy_engine_keeps_pending_slot_until_next_window():
    pending = gap.scheduled_slot("2026-09-30", 2)
    assert gap.due_slot(pending, swiss("2026-09-30T01:59:59")) is None
    assert gap.due_slot(pending, swiss("2026-09-30T02:00:00")) == pending
    assert gap.due_slot(pending, swiss("2026-09-30T11:59:59")) == pending


def test_missed_night_and_noon_do_not_create_two_catchup_runs():
    pending = gap.scheduled_slot("2026-09-30", 2)
    noon = gap.scheduled_slot("2026-09-30", 12)
    assert gap.due_slot(pending, swiss("2026-09-30T12:00:00")) == noon
    assert gap.due_slot(pending, swiss("2026-09-30T18:00:00")) == noon
    consumed_next = gap.next_slot(noon)
    assert gap.due_slot(consumed_next, swiss("2026-09-30T18:00:01")) is None


def test_friday_backlog_never_runs_before_mondays_first_slot():
    pending = gap.scheduled_slot("2026-09-25", 12)
    assert gap.due_slot(pending, swiss("2026-09-28T01:59:59")) is None
    assert gap.due_slot(pending, swiss("2026-09-28T02:00:00")) == gap.scheduled_slot("2026-09-28", 2)


def test_slot_identity_uses_local_calendar_date_not_utc_date():
    winter = gap.scheduled_slot("2026-01-30", 2)
    assert gap.slot_key(winter) == "Europe/Zurich:2026-01-30:02:00"
    assert gap.slot_key(swiss("2026-01-30T02:00:01")) is None


@pytest.mark.parametrize("invalid", [None, True, float("nan"), float("inf"), "bad", "2026-09-30T02:00:00"])
def test_invalid_or_naive_pending_times_are_not_scan_authority(invalid):
    assert gap.due_slot(invalid, swiss("2026-09-30T02:00:00")) is None
    assert gap.slot_key(invalid) is None


def test_snapshot_reports_future_clock_without_a_synthetic_scan_result():
    status = gap.schedule_snapshot(LONG, now_utc=swiss("2026-09-30T09:00:00"))
    assert status["timezone"] == "Europe/Zurich"
    assert status["local_times"] == ["02:00", "12:00"]
    assert status["next_eligible_at"] == "2026-09-30T10:00:00+00:00"
    assert "last_run" not in status
    assert "completed" not in status


def test_due_snapshot_can_report_engine_wait_without_advancing_the_slot():
    pending = gap.scheduled_slot("2026-09-30", 2)
    status = gap.schedule_snapshot(SHORT, next_run=pending, now_utc=swiss("2026-09-30T02:10:00"))
    assert status["scheduled_at"] == "2026-09-30T00:00:00+00:00"
    assert status["next_eligible_at"] == "2026-09-30T00:10:00+00:00"
    assert status["slot_id"] == "Europe/Zurich:2026-09-30:02:00"


def test_weekend_snapshot_rolls_to_swiss_monday_not_new_york_midnight():
    status = gap.schedule_snapshot(LONG, now_utc=swiss("2026-09-26T12:00:00"))
    assert status["automatic_paused"]
    assert status["reason"] == "weekend"
    assert status["next_eligible_at"] == "2026-09-28T00:00:00+00:00"


def test_missing_swiss_dst_time_rolls_to_first_valid_minute():
    # Public slots exclude Sunday; this guards the conversion itself.
    resolved = gap._wall_clock(datetime(2026, 3, 29, 2, 0))
    assert resolved.isoformat() == "2026-03-29T01:00:00+00:00"
    assert resolved.astimezone(ZURICH).hour == 3


def test_repeated_swiss_dst_time_has_one_earliest_occurrence():
    resolved = gap._wall_clock(datetime(2026, 10, 25, 2, 0))
    assert resolved.isoformat() == "2026-10-25T00:00:00+00:00"


def test_invalid_clock_cannot_allow_gap_job():
    assert not gap.automatic_scan_allowed(LONG, "2026-09-30T02:00:00")
    status = gap.schedule_snapshot(LONG, now_utc="bad")
    assert status["automatic_paused"]
    assert status["reason"] == "invalid_schedule_time"
    assert status["next_eligible_at"] is None


def test_first_store_initialization_schedules_both_directions_without_catchup(tmp_path):
    clock = swiss("2026-09-30T09:00:00")
    store = gap.GapScheduleStore(tmp_path / "schedule.json")
    assert store.next_due(LONG, clock) == gap.scheduled_slot("2026-09-30", 12)
    assert store.next_due(SHORT, clock) == gap.scheduled_slot("2026-09-30", 12)
    assert store.pending_slot(LONG, clock) is None
    state = json.loads((tmp_path / "schedule.json").read_text())
    assert set(state["jobs"]) == gap.SCAN_NAMES
    assert all(job["last_attempt_slot"] is None for job in state["jobs"].values())


def test_attempt_claim_survives_restart_and_does_not_assert_success(tmp_path):
    path = tmp_path / "schedule.json"
    store = gap.GapScheduleStore(path)
    before = swiss("2026-09-30T01:59:00")
    started = swiss("2026-09-30T02:00:01")
    due = store.next_due(LONG, before)
    assert store.claim(LONG, due, started)
    restarted = gap.GapScheduleStore(path)
    assert restarted.pending_slot(LONG, started) is None
    assert not restarted.claim(LONG, due, started)
    receipt = restarted.snapshot(started)["jobs"][LONG]
    assert receipt["last_attempt_slot"] == due
    assert receipt["last_attempt_phase"] == "reserved"
    assert receipt["next_due"] == gap.scheduled_slot("2026-09-30", 12)
    assert not {"success", "completed", "last_success"} & set(receipt)
    assert restarted.pending_slot(SHORT, started) == due


def test_engine_contention_release_restores_only_same_reservation(tmp_path):
    store = gap.GapScheduleStore(tmp_path / "schedule.json")
    before = swiss("2026-09-30T01:59:00")
    clock = swiss("2026-09-30T02:05:00")
    due = store.next_due(LONG, before)
    assert store.claim(LONG, due, clock)
    assert store.unclaim(LONG, due, clock)
    assert store.pending_slot(LONG, clock) == due
    assert store.snapshot(clock)["jobs"][LONG]["last_attempt_phase"] == "released"
    assert not store.unclaim(LONG, due, clock)
    assert store.claim(LONG, due, clock)
    later = gap.scheduled_slot("2026-09-30", 12)
    assert not store.unclaim(LONG, later, clock)
    assert store.pending_slot(LONG, clock) is None


def test_old_release_cannot_roll_back_a_newer_attempt(tmp_path):
    store = gap.GapScheduleStore(tmp_path / "schedule.json")
    due = store.next_due(LONG, swiss("2026-09-30T01:59:00"))
    assert store.claim(LONG, due, swiss("2026-09-30T02:00:00"))
    noon = gap.scheduled_slot("2026-09-30", 12)
    assert store.claim(LONG, noon, swiss("2026-09-30T12:00:00"))
    assert not store.unclaim(LONG, due, swiss("2026-09-30T12:01:00"))
    assert store.next_due(LONG, swiss("2026-09-30T12:01:00")) == gap.scheduled_slot("2026-10-01", 2)


def test_durable_backlog_uses_only_latest_same_day_slot(tmp_path):
    path = tmp_path / "schedule.json"
    store = gap.GapScheduleStore(path)
    overnight = store.next_due(LONG, swiss("2026-09-30T01:59:00"))
    restarted = gap.GapScheduleStore(path)
    late = swiss("2026-09-30T14:00:00")
    noon = gap.scheduled_slot("2026-09-30", 12)
    assert restarted.pending_slot(LONG, late) == noon
    assert not restarted.claim(LONG, overnight, late)
    assert restarted.claim(LONG, noon, late)
    assert restarted.pending_slot(LONG, late) is None


@pytest.mark.parametrize("raw", [b"{", b"null", b"{}", b"\xff", b" " * (16 * 1024 + 1)])
def test_corrupted_or_oversized_store_is_preserved_and_blocks_scan(tmp_path, raw):
    path = tmp_path / "schedule.json"
    path.write_bytes(raw)
    store = gap.GapScheduleStore(path)
    with pytest.raises(ValueError, match="^gap_schedule_state_invalid$"):
        store.next_due(LONG, swiss("2026-09-30T01:00:00"))
    assert path.read_bytes() == raw


@pytest.mark.parametrize("mutation", [
    lambda state: state.update(schema_version=2),
    lambda state: state.update(timezone="America/New_York"),
    lambda state: state.update(local_times=["02:00", "11:00"]),
    lambda state: state["jobs"].pop(SHORT),
    lambda state: state["jobs"][LONG].update(next_due=True),
    lambda state: state["jobs"][LONG].update(next_due=float("nan")),
    lambda state: state["jobs"][LONG].update(next_due=1720000000),
    lambda state: state["jobs"][LONG].update(last_attempt_at=1720000000),
    lambda state: state["jobs"][LONG].update(completed=True),
])
def test_wrong_schema_or_non_slot_timestamps_cannot_be_overwritten(tmp_path, mutation):
    path = tmp_path / "schedule.json"
    store = gap.GapScheduleStore(path)
    state = store.snapshot(swiss("2026-09-30T01:00:00"))
    mutation(state)
    path.write_text(json.dumps(state), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="^gap_schedule_state_invalid$"):
        store.pending_slot(LONG, swiss("2026-09-30T02:00:00"))
    assert path.read_bytes() == original


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "schedule.json"
    original = b'{"schema_version":1,"schema_version":2}'
    path.write_bytes(original)
    with pytest.raises(ValueError, match="^gap_schedule_state_invalid$"):
        gap.GapScheduleStore(path).snapshot(swiss("2026-09-30T01:00:00"))
    assert path.read_bytes() == original


def test_failed_atomic_replace_cannot_admit_worker_and_preserves_old_due(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    store = gap.GapScheduleStore(path)
    due = store.next_due(LONG, swiss("2026-09-30T01:00:00"))
    original = path.read_bytes()

    def failed_replace(*_):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(gap.os, "replace", failed_replace)
    with pytest.raises(ValueError, match="^gap_schedule_state_write_failed$"):
        store.claim(LONG, due, swiss("2026-09-30T02:00:00"))
    assert path.read_bytes() == original
    assert store.pending_slot(LONG, swiss("2026-09-30T02:00:00")) == due
    assert {item.name for item in tmp_path.iterdir()} == {"schedule.json", "schedule.json.lock"}


def test_failed_file_fsync_never_replaces_old_state(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    store = gap.GapScheduleStore(path)
    due = store.next_due(LONG, swiss("2026-09-30T01:00:00"))
    original = path.read_bytes()
    monkeypatch.setattr(gap.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ValueError, match="^gap_schedule_state_write_failed$"):
        store.claim(LONG, due, swiss("2026-09-30T02:00:00"))
    assert path.read_bytes() == original


def test_concurrent_claims_on_one_scheduler_store_admit_one_attempt(tmp_path):
    store = gap.GapScheduleStore(tmp_path / "schedule.json")
    due = store.next_due(LONG, swiss("2026-09-30T01:00:00"))
    now = swiss("2026-09-30T02:00:00")
    with ThreadPoolExecutor(max_workers=6) as workers:
        admitted = list(workers.map(lambda _: store.claim(LONG, due, now), range(12)))
    assert admitted.count(True) == 1


def test_two_processes_share_one_atomic_attempt_receipt(tmp_path):
    path = tmp_path / "schedule.json"
    store = gap.GapScheduleStore(path)
    due = store.next_due(LONG, swiss("2026-09-30T01:00:00"))
    now = swiss("2026-09-30T02:00:00").timestamp()
    code = ("import sys; from modules.gap_scan_schedule import GapScheduleStore; "
            "store=GapScheduleStore(sys.argv[1]); "
            "print(int(store.claim(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]))))")
    commands = [[sys.executable, "-B", "-c", code, str(path), LONG, str(due), str(now)]] * 3
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for command in commands]
    outputs = [process.communicate(timeout=15) for process in processes]
    assert all(process.returncode == 0 for process in processes), outputs
    assert sorted(stdout.strip() for stdout, _ in outputs) == ["0", "0", "1"]


def test_snapshot_cannot_modify_persisted_schedule(tmp_path):
    store = gap.GapScheduleStore(tmp_path / "schedule.json")
    now = swiss("2026-09-30T01:00:00")
    snapshot = store.snapshot(now)
    snapshot["jobs"][LONG]["next_due"] = 0
    assert store.next_due(LONG, now) == gap.scheduled_slot("2026-09-30", 2)


def test_state_permissions_are_private_on_posix(tmp_path):
    if os.name != "posix":
        pytest.skip("Windows mode bits do not express POSIX file permissions")
    path = tmp_path / "schedule.json"
    gap.GapScheduleStore(path).next_due(LONG, swiss("2026-09-30T01:00:00"))
    assert path.stat().st_mode & 0o777 == 0o600
