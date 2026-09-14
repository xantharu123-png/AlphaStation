from contextlib import closing
from datetime import datetime, timezone
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


@pytest.mark.parametrize("reason", ["timeout", "connection_failure", "tls_failure", "http_unauthorized",
    "http_rate_limited", "http_request_timeout", "http_server_error", "http_client_error",
    "http_unexpected_status", "malformed_json", "unexpected_failure"])
def test_transport_diagnostics_allowlisted_and_old_payload_compatible(tmp_path, reason):
    path = tmp_path / "bi_scan_progress_long.json"
    path.write_text(json.dumps({"diagnostics": {
        "transport_error_reason": reason, "transport_error_counts": {reason: 2, "PRIVATE_URL": 4},
        "transport_requests": 3, "transport_retries": 2, "transport_recovered_incidents": 1,
        "transport_retry_budget_exhausted": 0, "transport_raw_error": "PRIVATE_KEY",
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["transport_error_reason"] == reason
    assert result["transport_error_counts"] == {reason: 2, "_omitted_categories": 1}
    assert result["numeric_diagnostics"] == {"transport_requests": 3, "transport_retries": 2,
        "transport_recovered_incidents": 1, "transport_retry_budget_exhausted": 0}
    assert "PRIVATE" not in json.dumps(result)
    path.write_text('{"diagnostics":{}}', encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert "transport_error_reason" not in result and "transport_error_counts" not in result
    assert result["numeric_diagnostics"] == {}


@pytest.mark.parametrize("unsafe", ["PRIVATE", [], {}, True, -1, 1.5, None])
def test_transport_projection_discards_invalid_values(tmp_path, unsafe):
    path = tmp_path / "bi_scan_progress_long.json"
    path.write_text(json.dumps({"diagnostics": {
        "transport_error_reason": unsafe, "transport_retries": unsafe,
        "transport_error_counts": {"timeout": unsafe},
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert "transport_error_reason" not in result
    assert "transport_retries" not in result["numeric_diagnostics"]
    assert result["transport_error_counts"] == {"_omitted_categories": 1}


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
    assert state["paths"][:5] == [
        namespace / "tmp" / "bi_cache_long.json",
        namespace / "tmp" / "bi_cache_short.json",
        namespace / "tmp" / "strategy_momentum_breakout_long_cache.json",
        namespace / "run" / "alpha-progress" / "bi_scan_progress_long.json",
        namespace / "run" / "alpha-progress" / "bi_scan_progress_short.json",
    ]
    assert namespace / "tmp" / "crypto_explosion_cache.json" in state["paths"]
    assert namespace / "tmp" / "strategy_gap_momentum_long_cache.json" in state["paths"]
    assert len(state["paths"]) == 5 + len(collector.FIXED_SCANNER_CACHES) + len(collector.STOCK_STRATEGY_CACHE_NAMES) - 1
    assert result["read_only"] is True
    assert result["mail_evidence"]["outbox"] == {"available": False, "reason": "unverified_runtime_path"}


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


def _outbox_db(path, rows=()):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE mail_outbox(status,mail_class,attempts,created_at,next_attempt_at,expires_at,sent_at,subject,body_html,recipients_json,last_error)")
        connection.executemany("INSERT INTO mail_outbox VALUES(?,?,?,?,?,?,?,'PRIVATE_SUBJECT','PRIVATE_BODY','PRIVATE_EMAIL','PRIVATE_ERROR')", rows)
        connection.commit()


def _suppression_db(path, rows=()):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE suppression_buckets(bucket_start,scanner,reason,first_seen_at,last_seen_at,event_count,code_revision)")
        connection.executemany("INSERT INTO suppression_buckets VALUES(?,?,?,?,?,?,'PRIVATE_REVISION')", rows)
        connection.commit()


@pytest.mark.parametrize("snapshot,create", [(collector.outbox_snapshot, _outbox_db),
                                             (collector.suppression_snapshot, _suppression_db)])
def test_mail_missing_unknown_schema_and_real_zero_are_distinct_and_never_create_db(tmp_path, snapshot, create):
    path = tmp_path / "mail.sqlite"
    assert snapshot(path, 200000) == {"available": False, "reason": "missing"}
    assert not path.exists()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE unrelated(id)")
    assert snapshot(path, 200000) == {"available": False, "reason": "unknown_schema"}
    create(path)
    result = snapshot(path, 200000)
    assert result["available"] is True
    assert result.get("rows", result.get("reason_occurrences")) == 0


def test_outbox_metadata_only_counts_pending_expired_sent_unknown_and_failed_attempt_counter(tmp_path):
    path = tmp_path / "mail.sqlite"
    now = 200000
    _outbox_db(path, [
        ("pending", "trade", 2, now - 100, now - 10, now + 100, None),
        ("pending", "info", 3, now - 200, now - 10, now - 1, None),
        ("sent", "swing_trade", 0, now - 300, now - 10, now + 100, now - 20),
        ("PRIVATE_STATUS", "PRIVATE_CLASS", 0, now - 90000, now - 10, now + 100, None),
    ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = collector.outbox_snapshot(path, now)
    assert result["available"] is True and result["rows"] == 4
    assert result["by_status"]["pending"] == 2 and result["by_status"]["sent"] == 1
    assert result["due_pending"] == result["pending_past_expiry"] == 1
    assert result["created_last_24h"] == 3 and result["sent_last_24h"] == 1
    assert result["stored_attempt_counter_sum"] == 5 and result["max_stored_attempt_counter"] == 3
    assert result["unknown_status_rows"] == result["unknown_mail_class_rows"] == 1
    assert result["oldest_open_created_at"] == now - 200
    assert "PRIVATE" not in json.dumps(result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_suppression_window_counts_occurrences_not_unique_signals_and_suppresses_unknown_keys(tmp_path):
    path = tmp_path / "suppression.sqlite"
    now = 200100
    start = int((now - 86400) // 3600) * 3600
    _suppression_db(path, [
        (start, "bi_long", "smtp_delivery_failed", start + 1, start + 2, 3),
        (start + 3600, "bi_long", "smtp_delivery_failed", start + 3601, start + 3602, 4),
        (start + 3600, "bi_short", "cooldown_active", start + 3601, start + 3602, 5),
        (start + 3600, "PRIVATE_SCANNER", "PRIVATE_REASON", start + 3601, start + 3602, 6),
        (start - 3600, "bi_long", "smtp_delivery_failed", start - 3500, start - 3400, 100),
    ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = collector.suppression_snapshot(path, now)
    assert result["available"] is True and result["reason_occurrences"] == 18
    assert result["unknown_dimension_occurrences"] == 6
    assert result["by_scanner_reason"] == [
        {"scanner": "bi_long", "reason": "smtp_delivery_failed", "reason_occurrences": 7},
        {"scanner": "bi_short", "reason": "cooldown_active", "reason_occurrences": 5},
    ]
    assert "up_to_1h" in result["window_semantics"]
    assert "not_unique" in result["count_unit"] and "PRIVATE" not in json.dumps(result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_mail_queries_are_ro_queryonly_one_transaction_and_select_no_content(tmp_path, monkeypatch):
    path = tmp_path / "outbox.sqlite"
    _outbox_db(path)
    original = collector.sqlite3.connect
    calls, statements = [], []
    def connect(database, **kwargs):
        calls.append((database, kwargs))
        connection = original(database, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(collector.sqlite3, "connect", connect)
    assert collector.outbox_snapshot(path, 200000)["available"] is True
    assert calls[0][0].endswith("?mode=ro") and calls[0][1]["uri"] is True
    assert statements.count("BEGIN") == 1 and "PRAGMA query_only=ON" in statements
    projected = next(text for text in statements if 'FROM "mail_outbox"' in text)
    assert all(name not in projected for name in ("subject", "body_html", "recipients_json", "last_error"))
    assert not any(text.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for text in statements)


def test_mail_wal_snapshot_reads_latest_commit_without_consuming_queue(tmp_path):
    path = tmp_path / "outbox.sqlite"
    _outbox_db(path)
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO mail_outbox(status,mail_class,attempts,created_at,next_attempt_at,expires_at) VALUES('pending','trade',0,1,1,300000)")
        writer.commit()
        result = collector.outbox_snapshot(path, 200000)
        assert result["rows"] == result["due_pending"] == 1
        assert writer.execute("SELECT status FROM mail_outbox").fetchone()[0] == "pending"


def test_mail_query_row_limit_is_unknown_not_truncated_success(tmp_path, monkeypatch):
    path = tmp_path / "outbox.sqlite"
    _outbox_db(path, [("pending", "trade", 0, 1, 1, 300000, None)] * 2)
    monkeypatch.setattr(collector, "EVIDENCE_MAX_ROWS", 1)
    assert collector.outbox_snapshot(path, 200000) == {"available": False, "reason": "row_limit_exceeded"}


@pytest.mark.parametrize("invalid", [None, -1, "PRIVATE_ATTEMPT", 1.5])
def test_mail_invalid_attempt_metadata_is_not_zero_or_exposed(tmp_path, invalid):
    path = tmp_path / "outbox.sqlite"
    _outbox_db(path, [("pending", "trade", invalid, 1, 1, 300000, None)])
    assert collector.outbox_snapshot(path, 200000) == {"available": False, "reason": "invalid_data"}


def test_mail_unsafe_type_does_not_open_sqlite(tmp_path, monkeypatch):
    path = tmp_path / "directory.sqlite"
    path.mkdir()
    monkeypatch.setattr(collector.sqlite3, "connect", lambda *a, **kw: pytest.fail("unsafe type must not open"))
    assert collector.outbox_snapshot(path, 200000) == {"available": False, "reason": "unsafe_file_type"}


def test_mail_writer_routes_and_mount_namespace_inode_must_agree(tmp_path, monkeypatch):
    one, two = tmp_path / "one.sqlite", tmp_path / "two.sqlite"
    _outbox_db(one)
    _outbox_db(two)
    runtimes = {"api": {"_mail_paths": {"outbox": "/same.sqlite"}, "pid": 1},
                "bg": {"_mail_paths": {"outbox": "/same.sqlite"}, "pid": 2}}
    monkeypatch.setattr(collector, "_namespace_path", lambda runtime, path: one if runtime["pid"] == 1 else two)
    assert collector.mail_store_evidence(runtimes, 200000)["outbox"] == {
        "available": False, "reason": "writer_file_mismatch"}
    runtimes["bg"]["_mail_paths"]["outbox"] = "/different.sqlite"
    assert collector.mail_store_evidence(runtimes, 200000)["outbox"] == {
        "available": False, "reason": "writer_path_mismatch"}
    runtimes["bg"]["_mail_paths"]["outbox"] = "/same.sqlite"
    monkeypatch.setattr(collector, "_namespace_path", lambda runtime, path: one)
    result = collector.mail_store_evidence(runtimes, 200000)
    assert result["outbox"]["available"] is True and result["outbox"]["rows"] == 0
    assert result["delivery_journal"] == {"available": False, "reason": "unverified_runtime_path"}


def test_mail_paths_respect_runtime_data_and_individual_overrides_without_exporting_environment(tmp_path, monkeypatch):
    proc, app, _ = _mock_runtime_process(tmp_path, monkeypatch)
    with (proc / "environ").open("ab") as stream:
        stream.write(b"\0ALPHA_DATA_DIR=state\0MAIL_OUTBOX_DB_PATH=mail-custom.sqlite\0SUPPRESSION_TELEMETRY_DB_PATH=other/suppress.sqlite\0")
    result = collector.runtime_identity("tradingbot-api.service", app.resolve())
    assert result["_mail_paths"] == {
        "outbox": str(app / "mail-custom.sqlite"), "suppression": str(app / "other" / "suppress.sqlite"),
        "delivery_journal": str(app / "signal_tracker_delivery_acceptance.sqlite")}
    assert "PRIVATE_KEY" not in json.dumps(result)


def test_mail_code_allowlists_match_reviewed_registry_without_app_import():
    import ast
    tree = ast.parse((Path(__file__).parent / "modules" / "suppression_telemetry.py").read_text(encoding="utf8"))
    for suffix in ("SCANNERS", "REASONS"):
        source = next(ast.literal_eval(node.value.args[0]) for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "ALLOWED_SUPPRESSION_" + suffix
                              for target in node.targets))
        assert getattr(collector, "SUPPRESSION_" + suffix) == source


@pytest.mark.parametrize("version", ["stock-bi-20-v2", "stock-bi-20-v3"])
def test_confluence_preserves_exact_historical_or_current_contract_version(version):
    payload = _confluence_payload()
    payload["contract_version"] = version
    result = collector._confluence_projection(payload)
    assert result["available"] is True and result["contract_version"] == version


def test_new_optional_bi_diagnostics_strict_projection_and_missing_stays_missing(tmp_path):
    path = tmp_path / "bi_scan_progress_long.json"
    confluence = _confluence_payload()
    confluence["failed_pair_counts"] = {f"{left:02}:{right:02}": 0 for left in range(1, 21) for right in range(left + 1, 21)}
    confluence["failed_pair_counts"]["01:20"] = 12
    confluence["failed_pair_counts"]["PRIVATE_PAIR"] = 99
    confluence["consolidation_days_histogram"] = {str(n): 0 for n in range(51)} | {"other": 0}
    path.write_text(json.dumps({"diagnostics": {
        "confluence": confluence, "run_as_of": "2026-09-09T12:00:00+02:00", "quarantined_symbols": 2,
        "data_error_counts": {"invalid_bar_geometry": 2, "PRIVATE_REASON": 3},
        "data_error_fields": {"v": 2, "PRIVATE_FIELD": 3},
        "analysis_session_dates": {"2026-09-08": 125, "other": 1, "PRIVATE_DATE": 8},
    }}), encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert result["numeric_diagnostics"]["quarantined_symbols"] == 2
    assert result["run_as_of"] == "2026-09-09T10:00:00+00:00"
    assert result["confluence"]["failed_pair_counts"]["01:20"] == 12
    assert result["analysis_session_dates"]["2026-09-08"] == 125
    assert "PRIVATE" not in json.dumps(result)
    path.write_text('{"diagnostics":{}}', encoding="utf8")
    result = collector.safe_cache_summary(path)
    assert all(key not in result for key in ("run_as_of", "data_error_counts", "data_error_fields", "analysis_session_dates"))


def test_collection_strips_internal_mail_paths_and_reads_after_privilege_drop(tmp_path, monkeypatch):
    app, runtimes, state = _mock_collection(tmp_path, monkeypatch)
    for runtime in runtimes.values():
        runtime["_mail_paths"] = {"outbox": "/PRIVATE_PATH.sqlite"}
    def snapshot(values, now):
        assert state["dropped"] is True and values == runtimes
        return {"outbox": {"available": False, "reason": "missing"}}
    monkeypatch.setattr(collector, "mail_store_evidence", snapshot)
    result = collector.collect(app)
    assert "PRIVATE_PATH" not in json.dumps(result)
    assert "not_collected" in result["scanner_attempt_coverage"]["other_scanners"]


def test_fixed_scanner_cache_names_and_stock_strategy_names_match_api_source_without_import():
    import ast
    import re
    tree = ast.parse((Path(__file__).parent / "api.py").read_text(encoding="utf8"))
    source_strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                      and type(node.value) is str}
    assert set(collector.FIXED_SCANNER_CACHES.values()) <= source_strings
    order = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "STOCK_STRATEGY_ORDER"
                         for target in node.targets))
    slugs = {re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", name.lower().replace(" ", "_").replace("/", "_"))).strip("_")
             for name in order}
    assert set(collector.STOCK_STRATEGY_CACHE_NAMES) == slugs


def test_mail_oversized_or_binary_dimensions_never_leave_sqlite(tmp_path):
    path = tmp_path / "mail.sqlite"
    _outbox_db(path, [("PRIVATE" * 10000, sqlite3.Binary(b"PRIVATE_BLOB"), 0, 1, 1, 300000, None)])
    with collector._evidence_rows(path, "mail_outbox", ("status", "mail_class", "attempts")) as rows:
        assert rows == [{"status": None, "mail_class": None, "attempts": 0}]
    result = collector.outbox_snapshot(path, 200000)
    assert result["available"] is True and result["unknown_status_rows"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_suppression_only_reads_overlapping_window_before_applying_row_bound(tmp_path, monkeypatch):
    path = tmp_path / "suppression.sqlite"
    now = 200100
    start = int((now - 86400) // 3600) * 3600
    _suppression_db(path, [(start - 3600, "bi_long", "cooldown_active", start - 3500, start - 3400, 1)] * 5
                    + [(start, "bi_long", "cooldown_active", start + 1, start + 2, 7)])
    monkeypatch.setattr(collector, "EVIDENCE_MAX_ROWS", 1)
    result = collector.suppression_snapshot(path, now)
    assert result["available"] is True and result["reason_occurrences"] == 7


def _attempt_payload(slug="momentum_breakout_long", status="error"):
    sweep = slug == "stock_strategy_sweep"
    result = {"schema_version": 1, "attempt_kind": "stock_strategy_sweep" if sweep else "stock_strategy",
              "strategy_slug": slug, "run_id": "ab" * 16, "code_revision": "012345abcdef-dirty",
              "started_at": "2026-09-09T12:00:00+02:00", "updated_at": "2026-09-09T12:01:00+02:00",
              "results": [], "status": status, "result_count": 0 if status == "complete" else None,
              "error_code": "scan_data_invalid" if status == "error" else None,
              "diagnostics": {"coverage": "incomplete" if status != "complete" else "complete",
                              "final_results": 0 if status == "complete" else None}}
    if sweep:
        result["diagnostics"].update(strategy_results={}, mail_status="not_attempted", mail_error_code=None,
                                     strategies_total=4, strategies_attempted=0, strategies_completed=0, strategies_failed=0)
    return result


@pytest.mark.parametrize("status", ["running", "complete", "error"])
@pytest.mark.parametrize("slug", sorted(collector.STOCK_ATTEMPT_SLUGS | {"stock_strategy_sweep"}))
def test_strategy_attempt_preserves_zero_vs_failure_and_fixed_identity(tmp_path, slug, status):
    path = tmp_path / "attempt.json"
    payload = _attempt_payload(slug, status)
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, slug)
    assert result["available"] is True and result["strategy_slug"] == slug
    assert result["status"] == status and result["result_count"] == (0 if status == "complete" else None)
    assert result["started_at"] == "2026-09-09T10:00:00+00:00"
    assert result["error_code"] == ("scan_data_invalid" if status == "error" else None)
    if slug == "stock_strategy_sweep":
        assert result["mail_status_semantics"] == "guard_execution_not_delivery_evidence"


def test_sweep_attempt_separates_failed_leaf_from_completed_sibling_and_mail_guard(tmp_path):
    path = tmp_path / "sweep.json"
    payload = _attempt_payload("stock_strategy_sweep")
    payload["diagnostics"].update(
        strategies_attempted=4, strategies_completed=3, strategies_failed=1, current_result_count=2,
        strategy_results={
            "momentum_breakout_long": {"status": "error", "result_count": None,
                                       "error_code": "scan_data_unavailable"},
            "gap_momentum_long": {"status": "complete", "result_count": 2, "aggregate_candidate_count": 2},
            "PRIVATE_SLUG": {"PRIVATE_SECRET": "PRIVATE_BODY"},
        }, mail_status="guarded", mail_error_code=None,
    )
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "stock_strategy_sweep")
    assert result["available"] is True and result["result_count"] is None
    assert result["numeric_diagnostics"]["current_result_count"] == 2
    assert result["strategy_results"]["momentum_breakout_long"]["result_count"] is None
    assert "aggregate_candidate_count" not in result["strategy_results"]["momentum_breakout_long"]
    assert result["strategy_results"]["gap_momentum_long"]["result_count"] == 2
    assert result["mail_status"] == "guarded" and "PRIVATE" not in json.dumps(result)
    assert result["omitted_strategy_categories"] == 1


@pytest.mark.parametrize("change", [
    {"strategy_slug": "PRIVATE_SLUG"}, {"attempt_kind": "PRIVATE_KIND"},
    {"run_id": "PRIVATE_RUN"}, {"code_revision": "PRIVATE_REVISION"},
    {"started_at": "2026-09-09T12:00:00"}, {"updated_at": "2026-01-01T00:00:00+00:00"},
    {"status": "PRIVATE_STATUS"}, {"status": "error", "result_count": 0},
    {"status": "complete", "result_count": None}, {"error_code": "PRIVATE_ERROR"},
    {"error_code": {}}, {"results": [{"ticker": "PRIVATE_TICKER"}]}, {"diagnostics": None},
])
def test_strategy_attempt_rejects_incoherent_or_untrusted_schema_without_leak(tmp_path, change):
    path = tmp_path / "attempt.json"
    path.write_text(json.dumps({**_attempt_payload(), **change}), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "momentum_breakout_long")
    assert result == {"available": False, "reason": "invalid_or_unreadable"}


@pytest.mark.parametrize("version", [True, 0, 2, "1", None])
def test_strategy_attempt_unknown_schema_is_not_zero(tmp_path, version):
    path = tmp_path / "attempt.json"
    path.write_text(json.dumps({**_attempt_payload(), "schema_version": version}), encoding="utf8")
    assert collector.safe_strategy_attempt_summary(path, "momentum_breakout_long") == {
        "available": False, "reason": "unknown_schema"}


def test_strategy_attempt_projection_omits_raw_diagnostic_and_unknown_keys(tmp_path):
    path = tmp_path / "attempt.json"
    payload = _attempt_payload()
    payload.update(error="PRIVATE_STACK", exception="PRIVATE_EXCEPTION", ticker="PRIVATE_TICKER")
    payload["diagnostics"].update(error="PRIVATE_MESSAGE", universe_count=4,
        data_failures={"scan_data_invalid": 1, "PRIVATE_REASON": 2},
        rejected={"missing_price_or_prev_close": 4, "PRIVATE_REJECTION": 2},
        stage_counts={"priced_snapshot": 0, "PRIVATE_STAGE": 3})
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "momentum_breakout_long")
    assert result["available"] is True and result["numeric_diagnostics"]["universe_count"] == 4
    assert result["rejected"]["missing_price_or_prev_close"] == 4
    assert "PRIVATE" not in json.dumps(result)


def test_attempt_time_comparison_uses_instants_with_variable_fractional_precision(tmp_path):
    path = tmp_path / "attempt.json"
    payload = _attempt_payload()
    payload.update(started_at="2026-09-09T12:00:00.000000+02:00", updated_at="2026-09-09T10:00:00Z")
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "momentum_breakout_long")
    assert result["available"] is True and result["started_at"] == result["updated_at"]


def test_failed_sweep_child_nullable_aggregate_is_unknown_not_zero(tmp_path):
    path = tmp_path / "sweep.json"
    payload = _attempt_payload("stock_strategy_sweep")
    payload["diagnostics"]["strategy_results"] = {
        "momentum_breakout_long": {"status": "error", "result_count": None,
                                  "error_code": "scan_data_invalid", "aggregate_candidate_count": None}}
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "stock_strategy_sweep")
    assert result["available"] is True
    assert result["strategy_results"]["momentum_breakout_long"]["aggregate_candidate_count"] is None


def test_collection_attempt_files_use_api_namespace_and_runtime_override_after_drop(tmp_path, monkeypatch):
    app, runtimes, state = _mock_collection(tmp_path, monkeypatch)
    calls = []
    def summary(path, slug):
        assert state["dropped"]
        calls.append((path, slug))
        return {"available": False, "reason": "missing"}
    monkeypatch.setattr(collector, "safe_strategy_attempt_summary", summary)
    result = collector.collect(app)
    namespace = Path(runtimes["tradingbot-api.service"]["process_root"])
    assert len(calls) == 5
    assert (namespace / "run" / "alpha-progress" / "stock_strategy_momentum_breakout_long_attempt.json",
            "momentum_breakout_long") in calls
    assert (namespace / "run" / "alpha-progress" / "stock_strategy_sweep_attempt.json", "stock_strategy_sweep") in calls
    assert result["stock_strategy_attempts"]["momentum_breakout_long"] == {"available": False, "reason": "missing"}


def _delivery_journal_db(path, rows=()):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE delivery_acceptance_journal(intent_key TEXT PRIMARY KEY,accepted_at,"
                           "recipient_keys_json,journaled_at,state,reconciled_at,retry_count,reconcile_error)")
        connection.executemany("INSERT INTO delivery_acceptance_journal VALUES(?,?,'PRIVATE_RECIPIENT_KEYS',"
                               "'PRIVATE_JOURNALED_AT',?,'PRIVATE_RECONCILED_AT',?,'PRIVATE_ERROR')", rows)
        connection.commit()


def _delivery_iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def test_delivery_journal_missing_unknown_schema_and_zero_are_distinct_without_creation(tmp_path):
    path = tmp_path / "journal.sqlite"
    assert collector.delivery_journal_snapshot(path, 200000) == {"available": False, "reason": "missing"}
    assert not path.exists()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE unrelated(id)")
    assert collector.delivery_journal_snapshot(path, 200000) == {"available": False, "reason": "unknown_schema"}
    _delivery_journal_db(path)
    result = collector.delivery_journal_snapshot(path, 200000)
    assert result["available"] is True and result["rows"] == 0
    assert result["by_state"] == {"PENDING": 0, "RECONCILED": 0}
    assert result["oldest_pending_accepted_at"] is result["latest_accepted_at"] is None


def test_delivery_journal_counts_first_acceptance_metadata_not_messages_or_inbox_receipts(tmp_path):
    path = tmp_path / "journal.sqlite"
    now = 200000
    _delivery_journal_db(path, [
        ("PRIVATE_INTENT_1", _delivery_iso(now - 86401), "PENDING", 3),
        ("PRIVATE_INTENT_2", _delivery_iso(now - 86400), "PENDING", 2),
        ("PRIVATE_INTENT_3", _delivery_iso(now), "RECONCILED", 4),
        ("PRIVATE_INTENT_4", _delivery_iso(now + 1), "RECONCILED", 0),
        ("PRIVATE_INTENT_5", _delivery_iso(now - 20), "PRIVATE_STATE", 1),
    ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = collector.delivery_journal_snapshot(path, now)
    assert result["available"] is True and result["rows"] == 5
    assert result["by_state"] == {"PENDING": 2, "RECONCILED": 2}
    assert result["unknown_state_rows"] == result["future_accepted_rows"] == 1
    assert result["first_accepted_last_24h"] == 3
    assert result["oldest_pending_accepted_at"] == now - 86401
    assert result["latest_accepted_at"] == now
    assert result["stored_reconciliation_retry_count_sum"] == 10
    assert result["max_stored_reconciliation_retry_count"] == 4
    assert result["count_unit"] == "intent_rows_not_messages_recipients_or_smtp_attempts"
    assert result["coverage"] == "independent_acceptance_journal_metadata_only"
    assert result["delivery_semantics"] == "smtp_acceptance_metadata_not_inbox_receipt"
    assert result["recipient_cohort_validation"] == "not_collected"
    assert result["window_semantics"] == "earliest_recorded_acceptance_per_intent_in_inclusive_24h_window"
    assert "PRIVATE" not in json.dumps(result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_delivery_journal_old_replayed_intent_and_retained_reconciled_time_are_not_new_acceptances(tmp_path):
    path = tmp_path / "journal.sqlite"
    now = 200000
    _delivery_journal_db(path, [("PRIVATE_INTENT", _delivery_iso(now - 86401), "PENDING", 5)])
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE delivery_acceptance_journal SET journaled_at=?,reconciled_at=?",
                           (_delivery_iso(now - 10), _delivery_iso(now - 20)))
        connection.commit()
    result = collector.delivery_journal_snapshot(path, now)
    assert result["available"] is True and result["first_accepted_last_24h"] == 0
    assert result["by_state"] == {"PENDING": 1, "RECONCILED": 0}


def test_delivery_journal_timezone_offsets_count_as_instants(tmp_path):
    path = tmp_path / "journal.sqlite"
    now = datetime(2026, 9, 14, 10, tzinfo=timezone.utc).timestamp()
    _delivery_journal_db(path, [("PRIVATE_INTENT", "2026-09-14T12:00:00+02:00", "PENDING", 0)])
    result = collector.delivery_journal_snapshot(path, now)
    assert result["first_accepted_last_24h"] == 1
    assert result["latest_accepted_at"] == result["oldest_pending_accepted_at"] == now


@pytest.mark.parametrize("invalid", [None, "PRIVATE_TIMESTAMP", "2026-09-14", "2026-09-14T10:00:00",
                                     "PRIVATE" * 1000, sqlite3.Binary(b"PRIVATE_TIME"), 200000,
                                     "0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59",
                                     "1969-12-31T23:59:59+00:00"],
                         ids=["null", "invalid", "date_only", "naive", "oversized", "blob", "numeric",
                              "utc_underflow", "utc_overflow", "negative_epoch"])
def test_delivery_journal_invalid_acceptance_time_is_unknown_not_zero_or_exposed(tmp_path, invalid):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path, [("PRIVATE_INTENT", invalid, "PENDING", 0)])
    assert collector.delivery_journal_snapshot(path, 200000) == {"available": False, "reason": "invalid_data"}


@pytest.mark.parametrize("invalid", [None, -1, "PRIVATE_COUNTER", 1.5, sqlite3.Binary(b"PRIVATE_COUNTER")])
def test_delivery_journal_invalid_retry_counter_is_unknown_not_zero_or_exposed(tmp_path, invalid):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path, [("PRIVATE_INTENT", _delivery_iso(1), "PENDING", invalid)])
    assert collector.delivery_journal_snapshot(path, 200000) == {"available": False, "reason": "invalid_data"}


@pytest.mark.parametrize("unknown", [None, "PRIVATE_STATE", "PRIVATE" * 1000, sqlite3.Binary(b"PRIVATE_STATE")],
                         ids=["null", "unknown", "oversized", "blob"])
def test_delivery_journal_unknown_state_has_no_raw_dimension_output(tmp_path, unknown):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path, [("PRIVATE_INTENT", _delivery_iso(1), unknown, 0)])
    result = collector.delivery_journal_snapshot(path, 200000)
    assert result["available"] is True and result["unknown_state_rows"] == 1
    assert result["by_state"] == {"PENDING": 0, "RECONCILED": 0}
    assert "PRIVATE" not in json.dumps(result)


def test_delivery_journal_query_is_bounded_read_only_metadata_only(tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path, [("PRIVATE_INTENT", _delivery_iso(1), "PENDING", 0)])
    original = collector.sqlite3.connect
    calls, statements = [], []
    def connect(database, **kwargs):
        calls.append((database, kwargs))
        connection = original(database, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(collector.sqlite3, "connect", connect)
    assert collector.delivery_journal_snapshot(path, 200000)["available"] is True
    assert calls[0][0].endswith("?mode=ro") and calls[0][1]["uri"] is True
    assert statements.count("BEGIN") == 1 and "PRAGMA query_only=ON" in statements
    assert "PRAGMA trusted_schema=OFF" in statements
    projected = next(statement for statement in statements if 'FROM "delivery_acceptance_journal"' in statement)
    assert all(name not in projected for name in ("intent_key", "recipient_keys_json", "reconcile_error",
                                                "journaled_at", "reconciled_at"))
    assert "length(\"accepted_at\")<=64" in projected and "LIMIT 100001" in projected
    assert not any(statement.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for statement in statements)


def test_delivery_journal_wal_latest_commit_visible_without_reconciliation(tmp_path):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path)
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO delivery_acceptance_journal(intent_key,accepted_at,state,retry_count) "
                       "VALUES('PRIVATE_INTENT',?,'PENDING',7)", (_delivery_iso(1),))
        writer.commit()
        result = collector.delivery_journal_snapshot(path, 200000)
        assert result["rows"] == result["by_state"]["PENDING"] == 1
        assert writer.execute("SELECT state,retry_count FROM delivery_acceptance_journal").fetchone() == ("PENDING", 7)


def test_delivery_journal_row_limit_is_unknown_not_truncated_success(tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path, [(str(i), _delivery_iso(1), "PENDING", 0) for i in range(2)])
    monkeypatch.setattr(collector, "EVIDENCE_MAX_ROWS", 1)
    assert collector.delivery_journal_snapshot(path, 200000) == {"available": False, "reason": "row_limit_exceeded"}


def test_delivery_journal_unsafe_file_type_is_not_opened(tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    path.mkdir()
    monkeypatch.setattr(collector.sqlite3, "connect", lambda *a, **kw: pytest.fail("unsafe file must not open"))
    assert collector.delivery_journal_snapshot(path, 200000) == {"available": False, "reason": "unsafe_file_type"}


def test_delivery_journal_writer_paths_and_namespace_inodes_must_match(tmp_path, monkeypatch):
    one, two = tmp_path / "one.sqlite", tmp_path / "two.sqlite"
    _delivery_journal_db(one)
    _delivery_journal_db(two)
    runtimes = {"api": {"_mail_paths": {"delivery_journal": "/same.sqlite"}, "pid": 1},
                "bg": {"_mail_paths": {"delivery_journal": "/same.sqlite"}, "pid": 2}}
    monkeypatch.setattr(collector, "_namespace_path", lambda runtime, path: one if runtime["pid"] == 1 else two)
    assert collector.mail_store_evidence(runtimes, 200000)["delivery_journal"] == {
        "available": False, "reason": "writer_file_mismatch"}
    runtimes["bg"]["_mail_paths"]["delivery_journal"] = "/different.sqlite"
    assert collector.mail_store_evidence(runtimes, 200000)["delivery_journal"] == {
        "available": False, "reason": "writer_path_mismatch"}
    runtimes["bg"]["_mail_paths"]["delivery_journal"] = "/same.sqlite"
    monkeypatch.setattr(collector, "_namespace_path", lambda runtime, path: one)
    assert collector.mail_store_evidence(runtimes, 200000)["delivery_journal"]["rows"] == 0


@pytest.mark.parametrize("tracker_name,journal_override,expected", [
    ("signal_tracker.sqlite", None, "signal_tracker_delivery_acceptance.sqlite"),
    ("custom", "   ", "custom_delivery_acceptance.sqlite"),
    ("custom.sqlite3", "", "custom_delivery_acceptance.sqlite3"),
    ("custom.sqlite", "  private/journal.sqlite  ", "private/journal.sqlite"),
])
def test_delivery_journal_path_matches_trimmed_override_or_raw_tracker_sibling(tmp_path, monkeypatch,
                                                                            tracker_name, journal_override, expected):
    proc, app, tracker = _mock_runtime_process(tmp_path, monkeypatch)
    (app / tracker_name).touch()
    environment = f"SIGNAL_TRACKER_DB_PATH={tracker_name}\0PRIVATE_KEY=DO_NOT_EXPORT"
    if journal_override is not None:
        environment += "\0SIGNAL_DELIVERY_JOURNAL_DB_PATH=" + journal_override
    (proc / "environ").write_bytes(environment.encode())
    real_resolve = Path.resolve
    def tracker_host_resolution(self, *args, **kwargs):
        if self == app / tracker_name:
            return app / "different_host_path.sqlite"
        return real_resolve(self, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", tracker_host_resolution)
    result = collector.runtime_identity("tradingbot-api.service", app.resolve())
    assert result["tracker"] == str(app / "different_host_path.sqlite")
    assert result["_mail_paths"]["delivery_journal"] == str(app / expected)
    assert "PRIVATE_KEY" not in json.dumps(result)


def test_delivery_journal_path_defaults_with_data_dir_and_keeps_absolute_override(tmp_path, monkeypatch):
    proc, app, _ = _mock_runtime_process(tmp_path, monkeypatch)
    data = app / "state"
    data.mkdir()
    (data / "signal_tracker.sqlite").touch()
    (proc / "environ").write_bytes(b"ALPHA_DATA_DIR=state")
    assert collector.runtime_identity("tradingbot-api.service", app.resolve())["_mail_paths"]["delivery_journal"] == str(
        data / "signal_tracker_delivery_acceptance.sqlite")
    override = tmp_path / "external" / "journal.sqlite"
    (proc / "environ").write_bytes(f"ALPHA_DATA_DIR=state\0SIGNAL_DELIVERY_JOURNAL_DB_PATH={override}".encode())
    assert collector.runtime_identity("tradingbot-api.service", app.resolve())["_mail_paths"]["delivery_journal"] == str(override)


def test_delivery_journal_future_pending_metadata_has_no_past_acceptance_timestamps(tmp_path):
    path = tmp_path / "journal.sqlite"
    _delivery_journal_db(path, [("PRIVATE_INTENT", _delivery_iso(200001), "PENDING", 0)])
    result = collector.delivery_journal_snapshot(path, 200000)
    assert result["available"] is True and result["by_state"]["PENDING"] == 1
    assert result["future_accepted_rows"] == 1 and result["first_accepted_last_24h"] == 0
    assert result["latest_accepted_at"] is result["oldest_pending_accepted_at"] is None


def test_collection_reads_real_journal_in_api_namespace_after_drop_and_hides_internal_route(tmp_path, monkeypatch):
    app, runtimes, state = _mock_collection(tmp_path, monkeypatch)
    journal = tmp_path / "journal.sqlite"
    _delivery_journal_db(journal, [("PRIVATE_INTENT", _delivery_iso(1), "PENDING", 3)])
    route = "/PRIVATE_JOURNAL_ROUTE.sqlite"
    for runtime in runtimes.values():
        runtime["_mail_paths"] = {"delivery_journal": route}
        namespace = Path(runtime["process_root"])
        namespace.mkdir(parents=True)
        (namespace / route.lstrip("/")).hardlink_to(journal)
    original = collector.delivery_journal_snapshot
    calls = []
    def snapshot(path, now):
        assert state["dropped"] is True
        calls.append(path)
        return original(path, now)
    monkeypatch.setattr(collector, "delivery_journal_snapshot", snapshot)
    result = collector.collect(app)
    assert calls == [Path(runtimes["tradingbot-api.service"]["process_root"]) / route.lstrip("/")]
    evidence = result["mail_evidence"]["delivery_journal"]
    assert evidence["available"] is True and evidence["by_state"]["PENDING"] == 1
    assert evidence["stored_reconciliation_retry_count_sum"] == 3
    assert "PRIVATE" not in json.dumps(result) and "_mail_paths" not in json.dumps(result)


@pytest.mark.parametrize("store,overrides", [
    ("delivery_journal", {"SIGNAL_DELIVERY_JOURNAL_DB_PATH": "linked_dir/../journal.sqlite"}),
    ("outbox", {"MAIL_OUTBOX_DB_PATH": "linked_dir/../mail.sqlite"}),
    ("suppression", {"SUPPRESSION_TELEMETRY_DB_PATH": "linked_dir/../suppress.sqlite"}),
    ("delivery_journal", {"SIGNAL_TRACKER_DB_PATH": "linked_dir/../tracker.sqlite"}),
    ("delivery_journal", {"ALPHA_DATA_DIR": "linked_dir/../state"}),
    ("outbox", {"ALPHA_DATA_DIR": "linked_dir/../state"}),
    ("suppression", {"ALPHA_DATA_DIR": "linked_dir/../state"}),
], ids=["journal_override", "outbox_override", "suppression_override", "tracker_derived_journal",
        "data_derived_journal", "data_derived_outbox", "data_derived_suppression"])
def test_parent_traversal_mail_routes_are_unverified_without_reading_lexical_alternate(tmp_path, monkeypatch,
                                                                                    store, overrides):
    proc, app, tracker = _mock_runtime_process(tmp_path, monkeypatch)
    environment = {} if "ALPHA_DATA_DIR" in overrides else {"SIGNAL_TRACKER_DB_PATH": str(tracker)}
    environment.update(overrides)
    configured_tracker = Path(environment.get("SIGNAL_TRACKER_DB_PATH", str(
        app / environment.get("ALPHA_DATA_DIR", "data_cache") / "signal_tracker.sqlite")))
    if not configured_tracker.is_absolute():
        configured_tracker = app / configured_tracker
    real_resolve = Path.resolve
    def model_symlink_tracker_resolution(self, *args, **kwargs):
        # Model a valid application target after POSIX symlink/.. traversal,
        # without requiring Windows symlink-creation privileges in this test.
        if self == configured_tracker and ".." in self.parts:
            return real_resolve(tracker, strict=True)
        return real_resolve(self, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", model_symlink_tracker_resolution)
    (proc / "environ").write_bytes("\0".join(f"{key}={value}" for key, value in environment.items()).encode())
    runtime = collector.runtime_identity("tradingbot-api.service", app.resolve())
    alternate = tmp_path / "lexical_alternate.sqlite"
    alternate.write_bytes(b"The lexically normalized path must not be opened")
    namespace_calls, snapshot_calls = [], []
    def namespace(_runtime, path):
        namespace_calls.append(path)
        return alternate
    def forbidden_snapshot(path, now):
        snapshot_calls.append(path)
        return {"available": True, "rows": 99}
    monkeypatch.setattr(collector, "_namespace_path", namespace)
    for name in ("outbox_snapshot", "suppression_snapshot", "delivery_journal_snapshot"):
        monkeypatch.setattr(collector, name, forbidden_snapshot)
    reader_runtime = {"_mail_paths": {store: runtime["_mail_paths"][store]}}
    result = collector.mail_store_evidence({"api": reader_runtime, "bg": reader_runtime}, 200000)
    assert result[store] == {"available": False, "reason": "unverified_runtime_path"}
    assert runtime["_mail_paths"][store] is None
    assert namespace_calls == snapshot_calls == []
