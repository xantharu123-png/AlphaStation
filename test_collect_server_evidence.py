from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
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


@pytest.mark.parametrize("reason", [
    "invalid_payload", "provider_status", "invalid_json", "missing_results", "invalid_results_type",
    "invalid_result_count", "invalid_query_count", "result_count_mismatch", "contradictory_empty_response",
    "unexpected_pagination", "invalid_bar_type", "invalid_bar_value", "invalid_bar_geometry",
    "invalid_bar_timestamp", "invalid_data_conversion",
])
def test_daily_data_error_reason_exports_only_known_funnel_code(tmp_path, reason):
    path = tmp_path / "bi_scan_progress_long.json"
    path.write_text(json.dumps({"status": "error", "diagnostics": {
        "data_error_reason": reason, "provider_response": "PRIVATE_PROVIDER_RESPONSE",
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["available"] is True and result["data_error_reason"] == reason
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("reason", [
    None, True, 1, 1.5, [], {}, {"reason": "missing_results", "key": "PRIVATE_KEY"},
    "PRIVATE_REASON", "missing_results PRIVATE_TOKEN", "Missing_Results", " missing_results",
])
def test_daily_data_error_reason_never_copies_unknown_or_nonstring_values(tmp_path, reason):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"diagnostics": {"data_error_reason": reason}}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["available"] is True and "data_error_reason" not in result
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("code", [
    "scan_data_unavailable", "scan_provider_unauthorized", "scan_provider_rate_limited",
    "scan_data_incomplete", "scan_data_invalid",
])
def test_progress_error_status_exports_exact_public_error_code(tmp_path, code):
    path = tmp_path / "bi_scan_progress_short.json"
    path.write_text(json.dumps({"status": "error", "detail": code, "error": "PRIVATE_STACK"}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["status"] == "error" and result["error_code"] == code
    assert "detail" not in result and "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("status", [None, True, 1, [], {}, "running", "done", "Error", "PRIVATE_STATUS"])
def test_progress_detail_is_not_error_code_without_exact_error_status(tmp_path, status):
    path = tmp_path / "progress.json"
    path.write_text(json.dumps({"status": status, "detail": "scan_data_invalid"}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["available"] is True and "error_code" not in result
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("detail", [
    None, True, 1, [], {}, {"code": "scan_data_invalid", "token": "PRIVATE_TOKEN"},
    "PRIVATE_DETAIL", "scan_data_invalid PRIVATE_RESPONSE", " scan_data_invalid",
    "scan_provider_error", "scan_analysis_failed", "exception",
])
def test_progress_error_detail_rejects_free_text_nonstring_and_nonpublic_codes(tmp_path, detail):
    path = tmp_path / "progress.json"
    path.write_text(json.dumps({"status": "error", "detail": detail}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["available"] is True and result["status"] == "error"
    assert "error_code" not in result and "detail" not in result
    assert "PRIVATE" not in json.dumps(result)


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


def _confluence_payload():
    # Synthetic protocol example, never market evidence or scanner output.
    result = {"schema_version": 1, "required_green": 17, "scanner": "bi_long", "direction": "long",
              "run_id": "0123456789abcdef" * 2, "code_revision": "012345abcdef-dirty",
              "contract_version": "stock-bi-20-v2", "started_at": "2026-09-08T12:00:00+02:00"}
    result.update({key: 0 for key in collector.CONFLUENCE_COUNTS})
    result["green_count_histogram"] = {str(n): 0 for n in range(21)}
    result["available_count_histogram"] = {str(n): 0 for n in range(21)}
    result["bar_count_histogram"] = {str(n): 0 for n in range(36, 51)} | {"other": 0}
    result["factor_counts"] = {
        key: {name: 0 for name in collector.CONFLUENCE_FACTOR_COUNTS}
        for key in collector.CONFLUENCE_FACTORS
    }
    result["first_hard_gate_counts"] = {key: 0 for key in collector.CONFLUENCE_HARD_GATES}
    return result


def test_collector_factor_allowlist_matches_registry_without_runtime_app_import():
    import ast
    source = (Path(__file__).parent / "modules" / "patterns.py").read_text(encoding="utf8")
    tree = ast.parse(source)
    registry = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "BI_STOCK_INDICATORS"
                            for target in node.targets))
    assert collector.CONFLUENCE_FACTORS == {row[1] for row in registry}
    collector_tree = ast.parse(Path(collector.__file__).read_text(encoding="utf8"))
    imports = [node for node in ast.walk(collector_tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("modules")
                   or isinstance(node, ast.Import) and any(alias.name.startswith("modules") for alias in node.names)
                   for node in imports)


def test_confluence_summary_exports_fixed_counts_but_no_metadata_or_dynamic_private_keys(tmp_path):
    confluence = _confluence_payload()
    confluence.update(evaluated=12, below_required=10, pre_hard_gate_qualified=2,
                     detail="PRIVATE_TEXT", token="PRIVATE_TOKEN")
    confluence["green_count_histogram"]["17"] = 2
    confluence["green_count_histogram"]["PRIVATE_BIN"] = 10
    confluence["factor_counts"]["atr_squeeze"]["green"] = 7
    confluence["factor_counts"]["atr_squeeze"]["PRIVATE_NESTED"] = 9
    confluence["factor_counts"]["PRIVATE_FACTOR"] = {"green": 200}
    confluence["first_hard_gate_counts"]["PRIVATE_HARD_GATE"] = 2
    path = tmp_path / "confluence.json"
    path.write_text(json.dumps({"results": [{"ticker": "PRIVATE_ROW"}],
                               "diagnostics": {"confluence": confluence}}), encoding="utf8")
    summary = collector.safe_cache_summary(path)
    result = summary["confluence"]
    assert result["available"] is True
    assert result["evaluated"] == 12 and result["green_count_histogram"]["17"] == 2
    assert result["factor_counts"]["atr_squeeze"]["green"] == 7
    assert result["green_count_histogram"]["_omitted_categories"] == 1
    assert result["omitted_factor_categories"] == 1
    assert "PRIVATE" not in json.dumps(summary)
    assert result["run_id"] == confluence["run_id"]
    assert result["code_revision"] == confluence["code_revision"]
    assert result["started_at"] == "2026-09-08T10:00:00+00:00"


@pytest.mark.parametrize("key,value", [
    ("run_id", "PRIVATE_RUN"), ("run_id", "a" * 31), ("run_id", None),
    ("code_revision", "abc1234"), ("code_revision", "a" * 40),
    ("code_revision", "012345abcdef-PRIVATE_SUFFIX"), ("code_revision", ["PRIVATE_VALUE"]),
    ("contract_version", "PRIVATE_CONTRACT"), ("scanner", "PRIVATE_SCANNER"),
    ("scanner", {}), ("direction", "short"), ("direction", ["long"]),
    ("started_at", "2026-09-08T12:00:00"), ("started_at", "PRIVATE_TIME"),
])
def test_confluence_untrusted_or_missing_identity_is_not_exported(key, value):
    payload = _confluence_payload()
    payload[key] = value
    assert collector._confluence_projection(payload) == {"available": False, "schema_status": "invalid_identity"}


@pytest.mark.parametrize("revision", ["012345abcdef", "012345abcdef-dirty", "012345abcdef-tree-unknown", "unknown"])
def test_confluence_revision_identity_retains_dirty_and_uncertain_suffixes(revision):
    payload = _confluence_payload()
    payload["code_revision"] = revision
    result = collector._confluence_projection(payload)
    assert result["available"] is True and result["code_revision"] == revision


@pytest.mark.parametrize("name,scanner", [("bi_cache_short.json", "bi_short"),
                                          ("bi_scan_progress_short.json", "bi_short"),
                                          ("bi_cache_long.json", "bi_long"),
                                          ("bi_scan_progress_long.json", "bi_long")])
def test_confluence_identity_must_match_known_cache_or_progress_direction(tmp_path, name, scanner):
    path = tmp_path / name
    payload = _confluence_payload()
    payload.update(scanner=scanner, direction=scanner.removeprefix("bi_"))
    path.write_text(json.dumps({"diagnostics": {"confluence": payload}}), encoding="utf8")
    assert collector.safe_cache_summary(path)["confluence"]["available"] is True
    opposite = "bi_short" if scanner == "bi_long" else "bi_long"
    payload.update(scanner=opposite, direction=opposite.removeprefix("bi_"))
    path.write_text(json.dumps({"diagnostics": {"confluence": payload}}), encoding="utf8")
    assert collector.safe_cache_summary(path)["confluence"] == {"available": False, "schema_status": "invalid_identity"}


@pytest.mark.parametrize("version", [None, 0, 2, True, "1", "PRIVATE_VERSION"])
def test_confluence_unknown_schema_is_not_reported_as_zero(version):
    payload = _confluence_payload()
    payload["schema_version"] = version
    assert collector._confluence_projection(payload) == {"available": False, "schema_status": "unknown"}


@pytest.mark.parametrize("location,value", [
    ("evaluated", -1), ("evaluated", True), ("evaluated", "4"),
    ("evaluated", 2**63), ("required_green", 16), ("required_green", True),
    ("green_count_histogram", {}), ("factor_counts", {}),
    ("first_hard_gate_counts", None),
])
def test_confluence_incomplete_or_invalid_protocol_is_not_fabricated(location, value):
    payload = _confluence_payload()
    payload[location] = value
    assert collector._confluence_projection(payload) == {"available": False, "schema_status": "invalid"}


def test_missing_confluence_is_omitted_and_true_observed_zero_is_retained(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"diagnostics": {}}), encoding="utf8")
    assert "confluence" not in collector.safe_cache_summary(path)
    path.write_text(json.dumps({"diagnostics": {"confluence": _confluence_payload()}}), encoding="utf8")
    result = collector.safe_cache_summary(path)["confluence"]
    assert result["available"] is True and result["evaluated"] == 0
    assert result["green_count_histogram"]["17"] == 0


def test_confluence_initialization_failure_exports_only_fixed_unavailable_error_block(tmp_path):
    path = tmp_path / "bi_cache_long.json"
    failure = {"available": False, "reason": "initialization_failed", "initialization_errors": 1}
    path.write_text(json.dumps({"diagnostics": {"confluence": {
        **failure, "exception": "PRIVATE_EXCEPTION", "token": "PRIVATE_KEY",
    }}}), encoding="utf8")
    result = collector.safe_cache_summary(path)["confluence"]
    assert result == failure
    assert "evaluated" not in result and "green_count_histogram" not in result
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"available": 0}, {"available": True}, {"reason": "PRIVATE_REASON"},
    {"initialization_errors": True}, {"initialization_errors": 0},
    {"initialization_errors": 2}, {"initialization_errors": "1"},
])
def test_confluence_initialization_failure_requires_exact_allowlisted_types_and_values(change):
    payload = {"available": False, "reason": "initialization_failed", "initialization_errors": 1, **change}
    assert collector._confluence_projection(payload) == {"available": False, "schema_status": "unknown"}


def test_cache_read_uses_readonly_flags_and_preserves_same_file_bytes(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text('{"checked":7,"results":[]}', encoding="utf8")
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    real_open = collector.os.open
    calls = []
    def read_open(target, flags):
        calls.append(flags)
        return real_open(target, flags)
    monkeypatch.setattr(collector.os, "open", read_open)
    assert collector.safe_cache_summary(path)["checked"] == 7
    assert calls and all((flags & (os.O_WRONLY | os.O_RDWR)) == 0 for flags in calls)
    assert not any(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND) for flags in calls)
    assert path.stat().st_ino == before.st_ino
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "oversized"])
def test_cache_unsafe_type_or_oversized_file_never_opens(tmp_path, monkeypatch, kind):
    path = tmp_path / "cache.json"
    path.write_text("{}", encoding="utf8")
    mode = {"symlink": stat.S_IFLNK, "fifo": stat.S_IFIFO,
            "directory": stat.S_IFDIR, "oversized": stat.S_IFREG}[kind]
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(
        st_mode=mode, st_size=collector.CACHE_MAX_BYTES + 1 if kind == "oversized" else 2
    ) if self == path else real_lstat(self))
    def forbidden_open(*args):
        raise AssertionError("An unsafe path must not be opened")
    monkeypatch.setattr(collector.os, "open", forbidden_open)
    assert collector.safe_cache_summary(path) == {"available": False}


def test_cache_exchange_to_other_inode_or_fifo_is_rejected_after_nonblocking_open(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text('{"checked":99}', encoding="utf8")
    real_fstat = collector.os.fstat
    for changed_mode in (stat.S_IFREG, stat.S_IFIFO):
        def changed_file(fd):
            original = real_fstat(fd)
            return SimpleNamespace(st_mode=changed_mode, st_size=original.st_size,
                                   st_dev=original.st_dev, st_ino=original.st_ino + 1)
        monkeypatch.setattr(collector.os, "fstat", changed_file)
        assert collector.safe_cache_summary(path) == {"available": False}


def test_cache_growth_during_read_still_has_hard_byte_bound(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text('{"checked":99}', encoding="utf8")
    monkeypatch.setattr(collector, "CACHE_MAX_BYTES", 8)
    original = path.stat()
    fake_stat = SimpleNamespace(st_mode=stat.S_IFREG, st_size=2,
                                st_dev=original.st_dev, st_ino=original.st_ino,
                                st_mtime_ns=original.st_mtime_ns)
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self: fake_stat if self == path else real_lstat(self))
    monkeypatch.setattr(collector.os, "fstat", lambda fd: fake_stat)
    assert collector.safe_cache_summary(path) == {"available": False}


def test_cache_in_place_write_during_read_is_unavailable_even_when_size_is_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_bytes(b'{"checked":11}')
    original = path.stat()
    real_fdopen = collector.os.fdopen
    class MutatingReader:
        def __init__(self, descriptor, mode):
            self.stream = real_fdopen(descriptor, mode)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, limit):
            raw = self.stream.read(limit)
            path.write_bytes(b'{"checked":22}')
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns + 1000000000))
            return raw
    monkeypatch.setattr(collector.os, "fdopen", MutatingReader)
    assert collector.safe_cache_summary(path) == {"available": False}
    assert path.stat().st_ino == original.st_ino and path.stat().st_size == original.st_size


def _mock_runtime_process(tmp_path, monkeypatch, progress_dir="/tmp"):
    proc = tmp_path / "proc" / "321"
    app = proc / "cwd"
    app.mkdir(parents=True)
    tracker = app / "signal_tracker.sqlite"
    tracker.write_bytes(b"identity-check-only")
    process_root = proc / "root"
    process_root.joinpath(*collector.PurePosixPath(progress_dir).parts[1:]).mkdir(parents=True)
    (proc / "status").write_text("Name:\tpython\nUid:\t1001\t1001\t1001\t1001\nGid:\t1002\t1002\t1002\t1002\n", encoding="ascii")
    environment = [f"SIGNAL_TRACKER_DB_PATH={tracker}", "PRIVATE_KEY=DO_NOT_EXPORT"]
    if progress_dir != "/tmp":
        environment.append("ALPHA_RUNTIME_TMP_DIR=" + progress_dir)
    (proc / "environ").write_bytes("\0".join(environment).encode())
    monkeypatch.setattr(collector, "service_pid", lambda unit: 321)
    monkeypatch.setattr(collector, "_proc_directory", lambda pid: proc)
    return proc, app, tracker


@pytest.mark.parametrize("progress_dir", ["/tmp", "/run/alpha-private"])
def test_runtime_identity_keeps_verified_process_root_and_service_progress_path(tmp_path, monkeypatch, progress_dir):
    proc, app, tracker = _mock_runtime_process(tmp_path, monkeypatch, progress_dir)
    real_resolve = Path.resolve
    def no_namespace_resolve(self, *args, **kwargs):
        assert proc / "root" not in (self, *self.parents), "Do not resolve paths out of the service namespace"
        return real_resolve(self, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", no_namespace_resolve)
    result = collector.runtime_identity("tradingbot-api.service", app.resolve())
    assert result["pid"] == 321 and (result["uid"], result["gid"]) == (1001, 1002)
    assert result["process_root"] == str(proc / "root")
    assert result["progress_dir"] == progress_dir
    assert result["tracker"] == str(tracker.resolve())
    assert "PRIVATE" not in json.dumps(result)


def test_runtime_identity_still_rejects_wrong_cwd_or_mixed_process_privileges(tmp_path, monkeypatch):
    proc, app, _ = _mock_runtime_process(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="working directory"):
        collector.runtime_identity("tradingbot-api.service", tmp_path)
    (proc / "status").write_text("Uid:\t0\t1001\t0\t1001\nGid:\t1002\t1002\t1002\t1002\n", encoding="ascii")
    with pytest.raises(ValueError, match="Mixed process"):
        collector.runtime_identity("tradingbot-api.service", app.resolve())


@pytest.mark.parametrize("target", ["relative/cache.json", "/tmp/../secret", "C:\\tmp\\cache.json"])
def test_namespace_cache_targets_must_be_absolute_service_paths_without_traversal(target):
    with pytest.raises(ValueError):
        collector._namespace_path({"process_root": "/proc/321/root"}, target)


def _mock_collection(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    runtimes = {}
    for pid, unit in ((321, "tradingbot-api.service"), (654, "tradingbot-bg.service")):
        runtimes[unit] = {"pid": pid, "uid": 1001, "gid": 1002, "cwd": str(app),
                          "tracker": str(app / "tracker.sqlite"),
                          "process_root": str(tmp_path / "proc" / str(pid) / "root"),
                          "progress_dir": "/run/alpha-progress"}
    monkeypatch.setattr(collector, "runtime_identity", lambda unit, app: dict(runtimes[unit]))
    monkeypatch.setitem(collector.sys.modules, "pwd", SimpleNamespace(
        getpwnam=lambda name: SimpleNamespace(pw_uid=1001, pw_gid=1002)))
    state = {"dropped": False, "paths": []}
    monkeypatch.setattr(collector, "drop_reader_privileges", lambda uid, gid: state.update(dropped=True))
    monkeypatch.setattr(collector, "local_health", lambda: _health_payload())
    def snapshot(path):
        assert state["dropped"]
        return {"inventory": {}, "rows": []}
    monkeypatch.setattr(collector, "tracker_snapshot", snapshot)
    return app, runtimes, state


def test_collection_reads_api_namespace_caches_and_override_progress_after_privilege_drop(tmp_path, monkeypatch):
    app, runtimes, state = _mock_collection(tmp_path, monkeypatch)
    def summary(path):
        assert state["dropped"]
        state["paths"].append(Path(path))
        return {"available": False}
    monkeypatch.setattr(collector, "safe_cache_summary", summary)
    result = collector.collect(app)
    namespace = Path(runtimes["tradingbot-api.service"]["process_root"])
    assert state["paths"] == [
        namespace / "tmp" / "bi_cache_long.json",
        namespace / "tmp" / "bi_cache_short.json",
        namespace / "tmp" / "strategy_momentum_breakout_long_cache.json",
        namespace / "run" / "alpha-progress" / "bi_scan_progress_long.json",
        namespace / "run" / "alpha-progress" / "bi_scan_progress_short.json",
    ]
    assert result["read_only"] is True


def test_collection_never_falls_back_to_host_cache_when_private_tmp_is_missing(tmp_path, monkeypatch):
    app, runtimes, state = _mock_collection(tmp_path, monkeypatch)
    result = collector.collect(app)
    assert all(value == {"available": False} for value in result["scanner_caches"].values())


def test_collection_rejects_process_restart_during_namespace_read(tmp_path, monkeypatch):
    app, runtimes, state = _mock_collection(tmp_path, monkeypatch)
    def summary(path):
        runtimes["tradingbot-api.service"]["pid"] = 322
        runtimes["tradingbot-api.service"]["process_root"] = str(tmp_path / "proc" / "322" / "root")
        return {"available": False}
    monkeypatch.setattr(collector, "safe_cache_summary", summary)
    with pytest.raises(ValueError, match="restarted or paths changed"):
        collector.collect(app)
