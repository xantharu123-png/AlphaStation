from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

_SPEC = importlib.util.spec_from_file_location("server_evidence", Path(__file__).parent / "scripts" / "collect_server_evidence.py")
collector = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collector)


def test_projection_matches_existing_report_arithmetic_allowlist():
    from scripts.signal_performance_breakdown import REPORT_COLUMNS
    assert set(collector.TRACKER_COLUMNS) == set(REPORT_COLUMNS)


def test_tracker_projection_is_read_only_and_excludes_private_blobs(tmp_path):
    path = tmp_path / "tracker.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE signals(id,created_at,scanner,mail_class,status,delivery_recipient_keys_json,execution_context_json)")
        conn.execute("INSERT INTO signals VALUES(1,'2026-09-08','bi_long','trade','OPEN','PRIVATE_EMAIL','SECRET_ACCOUNT')")
        conn.execute("INSERT INTO signals VALUES(2,'2026-09-08','bi_long','shadow','OPEN','','')")
        conn.commit()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    data = collector.tracker_snapshot(path)
    assert data["inventory"]["trade_rows"] == 1
    assert data["inventory"]["shadow_rows"] == 1
    assert len(data["rows"]) == 1
    assert "PRIVATE_EMAIL" not in json.dumps(data)
    assert "SECRET_ACCOUNT" not in json.dumps(data)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_live_wal_rows_are_visible_without_migration(tmp_path):
    path = tmp_path / "wal.sqlite"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE signals(id,created_at,scanner,mail_class,status)")
        writer.execute("INSERT INTO signals VALUES(1,'2026-09-08','bi_long','trade','OPEN')")
        writer.commit()
        assert collector.tracker_snapshot(path)["inventory"]["all_rows"] == 1
        assert writer.execute("SELECT count(*) FROM signals").fetchone()[0] == 1


def test_missing_db_is_never_created(tmp_path):
    path = tmp_path / "absent.sqlite"
    with pytest.raises(FileNotFoundError):
        collector.tracker_snapshot(path)
    assert not path.exists()


def test_wrong_schema_is_rejected(tmp_path):
    path = tmp_path / "wrong.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE unrelated(id)")
    with pytest.raises(ValueError):
        collector.tracker_snapshot(path)


def test_cache_summary_omits_rows_free_text_and_secrets(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"results": [{"email": "PRIVATE_EMAIL"}], "detail": "SECRET_KEY",
                               "checked": 10, "total": 12,
                               "diagnostics": {"error": "SECRET_ERROR", "final_results": 0,
                                               "rejected": {"rvol_filter": 7}}}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["raw_rows"] == 1
    assert result["rejected"] == {"rvol_filter": 7}
    assert "PRIVATE_EMAIL" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)


def test_export_failure_has_no_partial_success_or_sensitive_exception(monkeypatch, capsys):
    def fail(_app):
        raise ValueError("private-account apiKey=SECRET")
    monkeypatch.setattr(collector, "collect", fail)
    monkeypatch.setattr(collector.sys, "argv", ["collect"])
    assert collector.main() == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert "SECRET" not in result.err


def test_root_reader_drops_all_privileges_before_sqlite_can_create_sidecars():
    state = {"uid": (0, 0, 0), "gid": (0, 0, 0), "groups": [0], "calls": []}
    def update(kind, value):
        state[kind] = value
        state["calls"].append(kind)
    fake = SimpleNamespace(geteuid=lambda: state["uid"][1], getgroups=lambda: state["groups"],
                           getresuid=lambda: state["uid"], getresgid=lambda: state["gid"],
                           setgroups=lambda value: update("groups", value),
                           setresgid=lambda *value: update("gid", value),
                           setresuid=lambda *value: update("uid", value))
    collector.drop_reader_privileges(1001, 1002, fake)
    assert state == {"uid": (1001, 1001, 1001), "gid": (1002, 1002, 1002),
                     "groups": [], "calls": ["groups", "gid", "uid"]}


@pytest.mark.parametrize("uid,gid", [(0, 1001), (1001, 0), (True, 1001), (-1, 1001)])
def test_reader_never_accepts_root_or_invalid_identity(uid, gid):
    with pytest.raises(ValueError):
        collector.drop_reader_privileges(uid, gid, SimpleNamespace())


def test_existing_wrong_reader_identity_is_rejected():
    fake = SimpleNamespace(geteuid=lambda: 1002, getresuid=lambda: (1002,) * 3,
                           getresgid=lambda: (1002,) * 3)
    with pytest.raises(ValueError):
        collector.drop_reader_privileges(1001, 1001, fake)


def test_collection_privilege_drop_precedes_any_db_or_cache_read():
    import inspect
    source = inspect.getsource(collector.collect)
    assert source.index("drop_reader_privileges(") < source.index("tracker_snapshot(")
    assert source.index("drop_reader_privileges(") < source.index("safe_cache_summary(")


def test_cache_projection_accepts_current_bi_and_strategy_funnels(tmp_path):
    path = tmp_path / "funnel.json"
    payload = {
        "status": "done", "partial": False, "checked": 2000, "total": 2000,
        "cached_at": "2026-09-08T12:00:00.123456", "timestamp": 1788868800.25,
        "results": [], "diagnostics": {
            "coverage": "complete", "checked": 2000, "history_available": 1600,
            "analyzed": 900, "indicator_passed": 0, "data_failures": 0,
            "analysis_errors": 0, "final_results": 0,
            "stage_counts": {"snapshot_universe": 2000, "priced_snapshot": 1900,
                             "momentum_completed_5m_confirmation": 3},
            "rejected": {"indicator_or_hard_gate_contract": 900,
                         "plan:structural_barrier_blocked": 3, "rvol_filter": 150},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["available"] and result["status"] == "done"
    assert result["coverage"] == "complete"
    assert result["numeric_diagnostics"]["history_available"] == 1600
    assert result["numeric_diagnostics"]["data_failures"] == 0
    assert result["stage_counts"] == payload["diagnostics"]["stage_counts"]
    assert result["rejected"] == payload["diagnostics"]["rejected"]


def test_cache_free_labels_strings_and_nested_private_fields_are_omitted(tmp_path):
    path = tmp_path / "private_labels.json"
    path.write_text(json.dumps({
        "status": "FAILED apiKey=PRIVATE_STATUS", "cached_at": "PRIVATE_DATE",
        "detail": "PRIVATE_DETAIL", "checked": 12, "results": [{"key": "PRIVATE_ROW"}],
        "diagnostics": {"PRIVATE_DIAG_KEY": 42, "coverage": "PRIVATE_COVERAGE",
                        "data_failures": {"scan_data_incomplete": 1, "PRIVATE_FAILURE": 8},
                        "rejected": {"rvol_filter": 2, "PRIVATE_REASON": 3},
                        "stage_counts": {"priced_snapshot": 4, "PRIVATE_STAGE": 5},
                        "legitimate_filters": {"price_filter": 6, "PRIVATE_FILTER": 7}},
    }), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert "PRIVATE" not in json.dumps(result)
    assert result["status"] == "unknown" and result["cached_at"] is None
    assert result["numeric_diagnostics"] == {}
    assert result["data_failures"] == {"scan_data_incomplete": 1, "_omitted_categories": 1}
    assert result["rejected"] == {"rvol_filter": 2, "_omitted_categories": 1}
    assert result["stage_counts"] == {"priced_snapshot": 4, "_omitted_categories": 1}


@pytest.mark.parametrize("value", [-1, True, 2.5, "5", None, float("nan"), float("inf"), {}])
def test_cache_counts_are_nonnegative_integer_fields_only(tmp_path, value):
    path = tmp_path / "invalid_counts.json"
    path.write_text(json.dumps({"checked": value, "diagnostics": {
        "checked": value, "rejected": {"rvol_filter": value},
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert "checked" not in result and "checked" not in result["numeric_diagnostics"]
    assert result["rejected"] == {"_omitted_categories": 1}
    json.dumps(result, allow_nan=False)


def _mock_health(monkeypatch, payload, status=200):
    state = {"closed": False, "requests": []}
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf8")
    response = SimpleNamespace(status=status, read=lambda limit: raw[:limit])
    def connect(host, port, timeout):
        assert (host, port, timeout) == ("127.0.0.1", 8000, 10)
        return SimpleNamespace(request=lambda *args, **kwargs: state["requests"].append((args, kwargs)),
                               getresponse=lambda: response,
                               close=lambda: state.update(closed=True))
    monkeypatch.setattr(collector.http.client, "HTTPConnection", connect)
    return state


def _health_payload(**changes):
    return {"status": "healthy", "revision": "abcdef123456", "frontend_bundle": "012345abcdef",
            "timestamp": "2026-09-08T12:00:00.123456", **changes}


def test_health_exports_only_canonical_metadata_not_arbitrary_api_fields(monkeypatch):
    state = _mock_health(monkeypatch, _health_payload(api_keys={"token": "PRIVATE_KEY"}))
    result = collector.local_health()
    assert result == _health_payload()
    assert "PRIVATE_KEY" not in json.dumps(result)
    assert state["closed"]
    assert state["requests"][0][0] == ("GET", "/api/health")


@pytest.mark.parametrize("change", [
    {"revision": "PRIVATE_KEY"}, {"revision": "a" * 32},
    {"revision": {"secret": "PRIVATE_VALUE"}}, {"frontend_bundle": ["PRIVATE_VALUE"]},
    {"timestamp": "PRIVATE_MESSAGE"}, {"timestamp": {"secret": "PRIVATE_VALUE"}},
    {"status": "error"},
])
def test_health_invalid_metadata_never_leaks_in_error_or_result(monkeypatch, change):
    state = _mock_health(monkeypatch, _health_payload(**change))
    with pytest.raises(ValueError) as exc:
        collector.local_health()
    assert "PRIVATE" not in str(exc.value)
    assert state["closed"]


@pytest.mark.parametrize("status,payload", [(302, _health_payload()), (500, _health_payload()), (200, b"X" * 16385), (200, [])])
def test_health_no_redirect_non200_oversized_or_nonobject_response(monkeypatch, status, payload):
    state = _mock_health(monkeypatch, payload, status=status)
    with pytest.raises(ValueError):
        collector.local_health()
    assert state["closed"]
