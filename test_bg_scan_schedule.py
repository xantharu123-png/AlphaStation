"""Run the real legacy BG dispatcher offline with an explicitly opted-in owner.

Every scan, mail, signal handler, watchdog, clock wait and storage path is stubbed.
The shared calendar policy itself is real: these tests cover dispatch wiring.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import bg_service as bg
from modules import scan_schedule as schedule


@pytest.fixture
def service(monkeypatch, tmp_path):
    state = {"now": datetime(2026, 9, 19, 14, 5, tzinfo=timezone.utc), "calls": []}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz) if tz is not None else state["now"].replace(tzinfo=None)

    def wait(seconds):
        if seconds == 1:  # End after one actual scheduler iteration.
            state["ticks"] = state.get("ticks", 0) + 1
            if state.get("second_iteration_at") and state["ticks"] <= 30:
                state["now"] = state["second_iteration_at"]
            else:
                bg._running = False
        if seconds == 120 and state.get("after_biotech_wait"):
            state["now"] = state["after_biotech_wait"]

    def record(name):
        def called(*args, **kwargs):
            suffix = ":" + args[1] if name == "bi" else ""
            state["calls"].append(name + suffix)
            if name == state.get("advance_after") and state["calls"].count(name) >= state.get("advance_after_n", 1):
                state["now"] = state["advance_to"]
            return []
        return called

    monkeypatch.setattr(bg, "_running", True)
    monkeypatch.setattr(bg, "datetime", Clock)
    monkeypatch.setattr(schedule, "datetime", Clock)
    monkeypatch.setattr(bg, "time", SimpleNamespace(time=lambda: state["now"].timestamp(), sleep=wait))
    monkeypatch.setattr(bg, "signal", SimpleNamespace(SIGTERM=15, SIGINT=2, signal=lambda *a: None))
    monkeypatch.setattr(bg, "PID_FILE", tmp_path / "bg.pid")
    monkeypatch.setattr(bg, "_load_secrets", lambda: {"POLYGON_KEY": "offline-key"})
    monkeypatch.setattr(bg, "_resolve_bg_scan_set", lambda: (set(bg.BG_ALL_SCANS), set()))
    for name in ("_update_status", "_bg_heartbeat_touch", "_start_bg_stuck_monitor",
                 "_cleanup_old_cache", "_cleanup_email_cooldown", "_check_and_alert_scan_results",
                 "_alert_nls_signals"):
        monkeypatch.setattr(bg, name, lambda *a, **k: None)
    for name, label in {
        "_start_mail_outbox_worker": "outbox", "_fetch_crash_monitor": "crash",
        "_run_btc_divergence": "btc", "_run_new_listing_scanner": "new_listing",
        "_run_bi_scanner": "bi", "_run_biotech_scanner": "biotech",
        "_run_bear_scanner": "bear", "_run_strategy_scanner": "strategies",
        "_run_orb_scanner": "orb", "_run_signal_eval_job": "signal_eval",
        "_run_weekly_report": "weekly_report", "_run_insider_cluster_alert": "insider",
    }.items():
        monkeypatch.setattr(bg, name, record(label))
    return state


def test_weekend_blocks_initial_fixed_and_interval_stock_paths_but_preserves_maintenance(service):
    bg.run_service()
    assert not ({"bi:long", "bi:short", "biotech", "bear", "strategies", "orb"} & set(service["calls"]))
    assert {"outbox", "crash", "btc", "new_listing", "signal_eval", "weekly_report", "insider"} <= set(service["calls"])


def test_monday_keeps_owned_stock_dispatch_and_crypto_unchanged(service):
    service["now"] = datetime(2026, 9, 21, 14, 20, tzinfo=timezone.utc)
    bg.run_service()
    assert {"bi:long", "bi:short", "biotech", "bear", "strategies", "orb", "btc", "signal_eval"} <= set(service["calls"])


def test_default_ownership_is_not_reactivated_by_calendar_rule(service, monkeypatch):
    monkeypatch.setattr(bg, "_resolve_bg_scan_set", lambda: (set(), set(bg.BG_ALL_SCANS)))
    service["now"] = datetime(2026, 9, 21, 14, 20, tzinfo=timezone.utc)
    bg.run_service()
    assert set(service["calls"]) == {"outbox", "signal_eval", "weekly_report", "insider"}


def test_biotech_initial_wait_rechecks_real_dispatch_date(service):
    service["now"] = datetime(2026, 9, 19, 3, 59, tzinfo=timezone.utc)
    service["after_biotech_wait"] = datetime(2026, 9, 19, 4, 1, tzinfo=timezone.utc)
    bg.run_service()
    assert "bi:long" in service["calls"]  # Friday initial work was eligible.
    assert "biotech" not in service["calls"]  # Two-minute startup delay crossed Saturday.


def test_prior_long_job_cannot_leave_fixed_or_interval_dispatch_using_yesterdays_clock(service):
    # Crash remains allowed and can take long enough to cross into Saturday.
    # Later stock slots must recheck the live clock rather than cached now_et.
    service["now"] = datetime(2026, 9, 18, 14, 20, tzinfo=timezone.utc)
    service["advance_after"] = "crash"
    service["advance_to"] = datetime(2026, 9, 19, 14, 20, tzinfo=timezone.utc)
    bg.run_service()
    assert not ({"bi:long", "bi:short", "biotech", "bear", "strategies", "orb"} & set(service["calls"]))
    assert {"btc", "new_listing", "signal_eval"} <= set(service["calls"])


def test_manual_run_once_remains_allowed_on_weekends(service):
    bg.run_once()
    assert {"bi:long", "biotech", "bear", "strategies"} <= set(service["calls"])


def test_fixed_slot_due_on_friday_is_rechecked_after_an_earlier_fixed_job_crosses_midnight(service):
    service["now"] = datetime(2026, 9, 18, 14, 20, tzinfo=timezone.utc)
    service["advance_after"] = "crash"
    service["advance_after_n"] = 2  # Initial crash stays Friday; the fixed-slot crash crosses midnight.
    service["advance_to"] = datetime(2026, 9, 19, 14, 20, tzinfo=timezone.utc)
    bg.run_service()
    assert service["calls"].count("bi:long") == 2  # Initial + first fixed slot precede crash.
    assert service["calls"].count("biotech") == 1  # Initial only, not the later Friday slot.
    assert not ({"strategies", "bear", "orb"} & set(service["calls"]))
    assert "signal_eval" in service["calls"]


def test_monday_automatic_resume_needs_no_restart_and_does_not_mark_skipped_slots_done(service):
    service["second_iteration_at"] = datetime(2026, 9, 21, 14, 20, tzinfo=timezone.utc)
    bg.run_service()
    assert service["calls"].count("bi:long") == 1  # No Saturday initial or scheduled stock run.
    assert {"biotech", "bear", "strategies", "orb", "signal_eval"} <= set(service["calls"])
