"""BI/Biotech progress belongs to a worker, not the latest result cache."""
import json
import threading
from contextlib import contextmanager

import pytest

import api
from modules import scan_control, scanners
from test_frontend_progress_consistency import SOURCE, selected, render
from test_scan_control_api import isolated_api


NOW = 1790787600.0
START = NOW - 100
KEYS = ("bi_long", "bi_short", "biotech")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_control, "_CONTROLS", {})
    monkeypatch.setattr(scan_control, "_LOCAL", threading.local())
    monkeypatch.setattr(api, "_scan_status", {})
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_resume_restarts", {})
    monkeypatch.setattr(api, "_scan_cache_health", lambda *a: {})
    monkeypatch.setattr(api, "_scan_schedule_for", lambda *a: {})
    monkeypatch.setattr(api, "_ce_progress_snapshot", lambda: {})
    monkeypatch.setattr(api.time, "time", lambda: NOW)
    monkeypatch.setattr(scanners, "_bi_progress_path", lambda direction: str(tmp_path / f"{direction}.json"))
    monkeypatch.setattr(scanners, "_biotech_progress_file", lambda: str(tmp_path / "biotech.json"))
    yield


@contextmanager
def owner(key, run_id="run-1"):
    scan_control.register(key, run_id, data_token=("test",), current_data_token=lambda: ("test",),
                          auto_allowed=lambda: True)
    api._scan_status[key] = {"running": True, "last_run_id": run_id, "_started_at": START,
                             "last_run": None, "next_run": None, "interval_min": 180}
    with scan_control.bind(key, run_id):
        yield
    scan_control.end(key, run_id)


def write(key, **kw):
    args = {"checked": 1500, "total": 5300, "hits": 0, **kw}
    if key == "biotech":
        scanners._biotech_progress_write("running", **args)
    else:
        scanners._bi_progress_write(key[3:], "running", **args)


def read(key):
    return scanners._biotech_progress_read() if key == "biotech" else scanners._bi_progress_read(key[3:])


@pytest.mark.parametrize("key", KEYS)
def test_zero_hit_worker_publishes_run_bound_counts_through_real_status_route(isolated, key):
    with owner(key):
        write(key)
        raw = read(key)
        assert raw.get("run_id") == "run-1"
        assert raw.get("owner_scan_key") == key
        progress = api.get_scan_status()["scans"][key]["progress"]
        assert (progress["checked"], progress["total"], progress["hits"]) == (1500, 5300, 0)
        assert progress["run_id"] == "run-1" and progress["running"] is True
        assert progress["timestamp"] == NOW and progress["run_started_at"] == START
        assert progress["seconds_since_progress"] == 0


@pytest.mark.parametrize("key", KEYS)
def test_unbound_thread_never_borrows_another_live_workers_identity(isolated, key):
    with owner(key):
        child = threading.Thread(target=write, args=(key,))
        child.start()
        child.join(2)
        assert not child.is_alive()
        assert "run_id" not in read(key)
        assert "progress" not in api.get_scan_status()["scans"][key]


def test_wrong_direction_writer_is_not_adopted_by_selected_worker(isolated):
    with owner("bi_long"):
        write("bi_short")
        assert "run_id" not in read("bi_short")


@pytest.mark.parametrize("changes", [
    {"run_id": "old-run"}, {"owner_scan_key": "bi_short"}, {"progress_version": 0},
    {"timestamp": START - 1}, {"timestamp": NOW + 10}, {"timestamp": "now"},
    {"timestamp": True}, {"timestamp": None}, {"timestamp": float("nan")},
    {"timestamp": float("inf")}, {"status": "error"}, {"status": "stopped"},
    {"checked": True}, {"checked": -1}, {"checked": 5301}, {"total": "5300"},
    {"hits": True}, {"hits": -1}, {"hits": 1501}, {"checked": 1e10, "total": 1e11},
])
def test_status_route_rejects_stale_foreign_or_malformed_file(isolated, monkeypatch, changes):
    with owner("bi_long"):
        write("bi_long")
        raw = {**read("bi_long"), **changes}
        monkeypatch.setattr(api, "_bi_progress_read", lambda direction: raw)
        assert "progress" not in api.get_scan_status()["scans"]["bi_long"]


@pytest.mark.parametrize("state,valid", [("paused", True), ("pause_requested", True),
                                        ("finishing", True), ("finished", False),
                                        ("restart_required", False)])
def test_last_known_counts_survive_a_pause_not_a_finished_owner(isolated, monkeypatch, state, valid):
    with owner("bi_long"):
        write("bi_long")
        with scan_control._CONDITION:
            scan_control._CONTROLS["bi_long"]["state"] = state
        raw = {**read("bi_long"), "timestamp": START + 1}
        monkeypatch.setattr(api, "_bi_progress_read", lambda direction: raw)
        result = api.get_scan_status()["scans"]["bi_long"]
        assert ("progress" in result) is valid
        if valid:
            assert result["progress"]["seconds_since_progress"] == 99


def test_completed_run_cannot_keep_live_progress_after_restart(isolated):
    with owner("bi_long"):
        write("bi_long")
    api._scan_status["bi_long"]["running"] = False
    assert "progress" not in api.get_scan_status()["scans"]["bi_long"]
    with owner("bi_long", "run-2"):
        assert "progress" not in api.get_scan_status()["scans"]["bi_long"]
        write("bi_long", checked=10)
        assert api.get_scan_status()["scans"]["bi_long"]["progress"]["checked"] == 10


@pytest.mark.parametrize("key", KEYS)
def test_actual_scheduler_worker_identity_reaches_status_and_frontend(isolated_api, monkeypatch, tmp_path, key):
    monkeypatch.setattr(scanners, "_bi_progress_path", lambda direction: str(tmp_path / f"{direction}.json"))
    monkeypatch.setattr(scanners, "_biotech_progress_file", lambda: str(tmp_path / "biotech.json"))
    ready, finish = isolated_api.event(), isolated_api.event()

    def work():
        write(key)
        ready.set()
        assert finish.wait(10)

    api._scan_status[key] = {"running": False, "last_run": None, "next_run": None, "interval_min": 180}
    assert api._run_scan_safe(key, work)
    assert ready.wait(5)
    try:
        state = api.get_scan_status()["scans"][key]
        assert state["progress"]["run_id"] == state["run_id"] == read(key)["run_id"]
        assert selected({"scanKey": key, "schedulerState": state})["checked"] == 1500
    finally:
        finish.set()
        api._scan_threads[key].join(5)
    assert "progress" not in api.get_scan_status()["scans"][key]


@pytest.mark.parametrize("key", KEYS)
def test_failed_atomic_write_preserves_last_complete_snapshot(isolated, monkeypatch, key):
    with owner(key):
        write(key)
        before = read(key)
        def fail_replace(*a):
            raise OSError("fixture replacement failure")
        monkeypatch.setattr(scanners.os, "replace", fail_replace)
        write(key, checked=1600)
        assert read(key) == before


def test_bound_but_ended_owner_cannot_label_a_late_write(isolated):
    with owner("bi_long"):
        scan_control.end("bi_long", "run-1")
        write("bi_long")
        assert "run_id" not in read("bi_long")


@pytest.mark.parametrize("status", ["scanning", "done"])
def test_preparation_and_completed_analysis_keep_owner_not_final_result_claim(isolated, monkeypatch, status):
    with owner("bi_long"):
        write("bi_long")
        raw = {**read("bi_long"), "status": status}
        monkeypatch.setattr(api, "_bi_progress_read", lambda direction: raw)
        progress = api.get_scan_status()["scans"]["bi_long"]["progress"]
        assert progress["running"] is True and progress["status"] == status


def test_age_of_last_known_progress_is_not_reset_while_paused(isolated, monkeypatch):
    with owner("bi_long"):
        write("bi_long")
        monkeypatch.setattr(api.time, "time", lambda: NOW + 3600)
        with scan_control._CONDITION:
            scan_control._CONTROLS["bi_long"]["state"] = "paused"
        state = api.get_scan_status()["scans"]["bi_long"]
        result = selected({"scanKey": "bi_long", "schedulerState": state})
        assert result["checked"] == 1500 and result["seconds_since_progress"] == 3600


def ui_options():
    control = {"supported": True, "owner_scan_key": "bi_long", "run_id": "run-1",
               "worker_alive": True, "state": "running", "scope": "scanner"}
    return {"scanKey": "bi_long", "running": True, "count": 0,
            "info": {"scan_run_id": "run-1", "scan_running": True, "partial": False,
                     "scan_control": control, "checked": 5300, "total": 5300},
            "schedulerState": {"run_id": "run-1", "running": True, "control": control,
                "progress": {"progress_version": 1, "owner_scan_key": "bi_long", "run_id": "run-1",
                    "status": "running", "running": True, "checked": 1500, "total": 5300, "hits": 0,
                    "timestamp": NOW, "observed_at": NOW, "run_started_at": START,
                    "seconds_since_progress": 0}}}


def test_ui_shows_current_bi_progress_without_any_partial_hit_cache():
    result = selected(ui_options())
    assert (result["checked"], result["total"], result["percent"], result["hits"]) == (1500, 5300, 28, 0)
    assert result["source"] == "runtime" and result["currentResult"] is False
    render("""
const main=components.ScanControl({onScan:()=>{},isScanning:true,progressOverride:PROGRESS});
assert.match(text(main),/1500 \\/ 5300 analysiert/);
assert.ok(nodes(main).some(n=>n.props.style?.width==='28%'));
assert.ok(!/undefined|NaN|Fortschritt noch nicht/.test(text(main)));
""".replace("PROGRESS", json.dumps(result)))


@pytest.mark.parametrize("changes", [
    {"run_id": "old"}, {"owner_scan_key": "bi_short"}, {"progress_version": 0},
    {"timestamp": START - 1}, {"timestamp": NOW + 10}, {"timestamp": None},
    {"running": False}, {"status": "error"}, {"run_started_at": None}, {"observed_at": None},
])
def test_ui_rejects_unverified_file_identity_even_if_api_regresses(changes):
    data = ui_options()
    data["schedulerState"]["progress"].update(changes)
    assert selected(data)["hasProgress"] is False


@pytest.mark.parametrize("source", ["result", "scheduler"])
def test_ui_refuses_counters_for_wrong_live_owner(source):
    data = ui_options()
    branch, key = ("info", "scan_control") if source == "result" else ("schedulerState", "control")
    data[branch][key] = {**data[branch][key], "owner_scan_key": "bi_short"}
    assert selected(data)["hasProgress"] is False


def test_bi_does_not_claim_scan_running_below_a_paused_control():
    bi = SOURCE[SOURCE.index("function BIScannerTab("):SOURCE.index("function BiotechTab(")]
    assert "Scan laeuft: {selectedProgress.hasProgress" not in bi
    assert "progressOverride={selectedProgress}" in bi
