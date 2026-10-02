"""Standalone probe fixtures: local synthetic files, no server or application."""
import importlib.util
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("stock_attempt_error_probe", Path(__file__).parent / "scripts/probe_stock_attempt_errors.py")
probe_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe_module)
NOW = datetime(2026, 10, 2, 0, tzinfo=timezone.utc)


def payload(slug="momentum_breakout_long", **updates):
    record = {"schema_version": 1, "attempt_kind": "stock_strategy", "strategy_slug": slug,
              "results": [], "run_id": "a" * 32, "code_revision": "796f82100000",
              "started_at": "2026-10-01T20:30:00Z", "updated_at": "2026-10-01T20:32:00Z",
              "status": "error", "error_code": "scan_data_invalid", "result_count": None,
              "diagnostics": {"checked": 3379, "universe_count": 12582, "coverage": "incomplete",
                  "final_results": None, "invalid_history_symbols": 21, "excluded_data_symbols": 20,
                  "data_retry_attempts": 20, "data_retry_failed": 20, "provider_requests": 157,
                  "stock_history_error_counts": {"invalid_bar_geometry": 21, "symbol_exclusion_limit": 1},
                  "stock_history_error_fields": {"bar": 21},
                  "stock_history_error_value_classes": {"invalid_geometry": 21},
                  "stock_history_error_positions": {"interior": 21},
                  "rejected": {"invalid_daily_history": 20, "daily_reference:reference_volume_mismatch": 1}}}
    record.update(updates)
    return record


def write_fixture(tmp_path, value, *, name="attempt.json"):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def test_probe_exposes_history_and_reference_limit_signatures():
    result = probe_module._project(payload(), "momentum_breakout_long", NOW)
    assert result["diagnostics"]["invalid_history_symbols"] == 21
    assert result["diagnostics"]["stock_history_error_counts"]["symbol_exclusion_limit"] == 1
    assert result["diagnostics"]["rejected"]["daily_reference:reference_volume_mismatch"] == 1
    assert result["status"] == "error" and result["error_code"] == "scan_data_invalid"


def test_stable_file_read_matches_strict_projection(tmp_path):
    expected = probe_module._project(payload(), "momentum_breakout_long", NOW)
    try:
        result = probe_module._read_attempt_file(write_fixture(tmp_path, payload()), "momentum_breakout_long", NOW)
    except probe_module.ProbeError as error:
        # Windows may still update a newly created fixture's metadata at open.
        # The live Linux guard is not weakened to manufacture valid evidence.
        assert os.name == "nt" and str(error) == "attempt_changed_during_read"
    else:
        assert result == expected


def test_projection_never_emits_unknown_fields_or_sensitive_values():
    record = payload()
    record.update(ticker="PRIVATE_TICKER", recipient="PRIVATE_RECIPIENT", environment="PRIVATE_ENV")
    record["diagnostics"].update(secret="PRIVATE_SECRET", api_url="PRIVATE_URL", rows=["PRIVATE_ROWS"],
        stock_history_error_counts={"invalid_bar_timestamp": 1, "PRIVATE_KEY": 999},
        stock_history_error_fields={"t": 1, "unknown": True},
        stock_history_error_value_classes={"future_timestamp": 1, "non_finite": -1},
        checked=True, total=10**10)
    result = probe_module._project(record, "momentum_breakout_long", NOW)
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded
    assert result["diagnostics"]["stock_history_error_counts"] == {"invalid_bar_timestamp": 1}
    assert result["diagnostics"]["stock_history_error_fields"] == {"t": 1}
    assert "checked" not in result["diagnostics"] and "total" not in result["diagnostics"]
    assert "run_id" not in result


@pytest.mark.parametrize("analysis,mode,expected", [
    ("2026-10-01T16:00:00-04:00", "completed_daily_swing", "2026-10-01T20:00:00+00:00"),
    ("2026-10-01T20:30:00Z", "live_snapshot", "2026-10-01T20:30:00+00:00"),
    ("PRIVATE", "PRIVATE", None), ("2026-10-01T20:30:00", [], None),
])
def test_optional_analysis_identity_is_strictly_projected(analysis, mode, expected):
    record = payload()
    record["diagnostics"].update(analysis_as_of=analysis, data_mode=mode)
    result = probe_module._project(record, "momentum_breakout_long", NOW)["diagnostics"]
    assert result.get("analysis_as_of") == expected
    if expected is None:
        assert "data_mode" not in result and "PRIVATE" not in json.dumps(result)
    else:
        assert result["data_mode"] == mode


@pytest.mark.parametrize("changes", [
    {"results": [{"ticker": "PRIVATE"}]}, {"strategy_slug": "other"}, {"schema_version": True},
    {"attempt_kind": "other"}, {"run_id": "bad"}, {"code_revision": "PRIVATE"},
    {"status": "PRIVATE"}, {"error_code": "PRIVATE"}, {"diagnostics": []},
    {"status": []}, {"error_code": {"PRIVATE": "PRIVATE"}},
    {"updated_at": "2026-10-03T00:00:00Z"}, {"started_at": "2026-10-01T23:00:00Z"},
    {"started_at": "2026-10-01T20:30:00"}, {"result_count": 1},
])
def test_wrong_identity_or_unbounded_status_is_rejected(changes):
    with pytest.raises(probe_module.ProbeError) as error:
        probe_module._project(payload(**changes), "momentum_breakout_long", NOW)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("status", ["running", "complete"])
def test_real_nonerror_status_contracts(status):
    record = payload(status=status, error_code=None, result_count=2 if status == "complete" else None)
    if status == "complete":
        record["diagnostics"].update(final_results=2, coverage="complete_with_exclusions")
    result = probe_module._project(record, "momentum_breakout_long", NOW)
    assert result["status"] == status
    assert result["diagnostics"]["final_results"] == (2 if status == "complete" else None)


@pytest.mark.parametrize("raw", ['{"results":[],"results":[]}', '{"value":NaN}', '{"value":Infinity}', "not json"])
def test_duplicate_and_nonfinite_json_are_rejected(raw):
    # Parser semantics are independent of NTFS metadata updates between open
    # and read. The live reader still keeps its strict Linux file fingerprint.
    with pytest.raises(probe_module.ProbeError, match="invalid_attempt_json"):
        probe_module._decode_attempt(raw.encode("utf-8"))


def test_malformed_file_is_rejected_even_if_metadata_changes_first(tmp_path):
    path = tmp_path / "attempt.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(probe_module.ProbeError) as error:
        probe_module._read_attempt_file(str(path), "momentum_breakout_long", NOW)
    assert str(error.value) in {"invalid_attempt_json", "attempt_changed_during_read"}


def test_size_limit_and_directory_are_rejected(tmp_path):
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (probe_module.MAX_BYTES + 1))
    with pytest.raises(probe_module.ProbeError, match="attempt_too_large"):
        probe_module._read_attempt_file(str(path), "momentum_breakout_long", NOW)
    with pytest.raises(probe_module.ProbeError, match="attempt_not_regular"):
        probe_module._read_attempt_file(str(tmp_path), "momentum_breakout_long", NOW)


def test_read_uses_nonblocking_nofollow_flags(tmp_path, monkeypatch):
    path = write_fixture(tmp_path, payload())
    original = probe_module.os.open
    calls = []
    def opened(name, flags, **kwargs):
        calls.append(flags)
        return original(name, flags, **kwargs)
    monkeypatch.setattr(probe_module.os, "open", opened)
    try:
        probe_module._read_attempt_file(path, "momentum_breakout_long", NOW)
    except probe_module.ProbeError as error:
        # NTFS may complete pending metadata updates at open. That conservative
        # rejection is correct; this case verifies flags, not stable evidence.
        assert os.name == "nt" and str(error) == "attempt_changed_during_read"
    assert len(calls) == 1
    for flag in (getattr(os, "O_NOFOLLOW", 0), getattr(os, "O_NONBLOCK", 0)):
        assert not flag or calls[0] & flag == flag


def test_file_replaced_during_read_is_rejected(tmp_path, monkeypatch):
    path = write_fixture(tmp_path, payload())
    original = probe_module.os.stat
    count = 0
    def changed(name, **kwargs):
        nonlocal count
        count += 1
        result = original(name, **kwargs)
        if count == 2:
            return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1,
                st_size=result.st_size, st_mtime_ns=result.st_mtime_ns, st_ctime_ns=result.st_ctime_ns)
        return result
    monkeypatch.setattr(probe_module.os, "stat", changed)
    with pytest.raises(probe_module.ProbeError, match="attempt_changed_during_read"):
        probe_module._read_attempt_file(path, "momentum_breakout_long", NOW)


def test_missing_file_emits_only_fixed_reason(tmp_path):
    with pytest.raises(probe_module.ProbeError, match="attempt_missing"):
        probe_module._read_attempt_file(str(tmp_path / "PRIVATE.json"), "momentum_breakout_long", NOW)


def test_link_is_rejected_before_open(monkeypatch):
    monkeypatch.setattr(probe_module.os, "stat", lambda *a, **k: SimpleNamespace(st_mode=probe_module.stat.S_IFLNK))
    monkeypatch.setattr(probe_module.os, "open", lambda *a, **k: pytest.fail("A link must not be opened"))
    with pytest.raises(probe_module.ProbeError, match="attempt_not_regular"):
        probe_module._read_attempt_file("fixture.json", "momentum_breakout_long", NOW)


def test_service_snapshot_uses_only_fixed_unit_and_process_start_ticks(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(stdout="1234\n")
    monkeypatch.setattr(probe_module.subprocess, "run", run)
    tail = ["S"] + ["0"] * 18 + ["5678"]
    raw = ("1234 (python (worker)) " + " ".join(tail)).encode("ascii")
    def opened(path, mode):
        assert path == "/proc/1234/stat" and mode == "rb"
        return io.BytesIO(raw)
    monkeypatch.setattr("builtins.open", opened)
    assert probe_module._service_snapshot() == (1234, 5678)
    assert commands[0][0] == ["/usr/bin/systemctl", "show", "tradingbot-api.service", "--property=MainPID", "--value"]
    assert commands[0][1]["timeout"] == 5 and commands[0][1]["check"] is True


@pytest.mark.parametrize("pid_text", ["0", "-1", "PRIVATE", "1234\n5678", "12345678901"])
def test_invalid_service_pid_does_not_read_process_files(monkeypatch, pid_text):
    monkeypatch.setattr(probe_module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=pid_text))
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("Invalid PID must not be followed"))
    with pytest.raises(probe_module.ProbeError, match="api_process_unavailable"):
        probe_module._service_snapshot()


def test_namespace_tmp_uses_root_descriptor_and_no_host_fallback(monkeypatch):
    for name, value in (("O_DIRECTORY", 0x10000), ("O_NOFOLLOW", 0x20000), ("O_NONBLOCK", 0x40000)):
        monkeypatch.setattr(probe_module.os, name, value, raising=False)
    calls, closed = [], []
    def opened(path, flags, **kwargs):
        calls.append((path, flags, kwargs))
        return 11 if len(calls) == 1 else 22
    monkeypatch.setattr(probe_module.os, "open", opened)
    monkeypatch.setattr(probe_module.os, "close", closed.append)
    assert probe_module._open_namespace_tmp(1234) == 22
    assert calls[0][0] == "/proc/1234/root"
    assert calls[1][0] == "tmp" and calls[1][2] == {"dir_fd": 11}
    assert calls[1][1] & probe_module.os.O_NOFOLLOW
    assert calls[1][1] & probe_module.os.O_NONBLOCK
    assert closed == [11]


@pytest.fixture
def mocked_service(monkeypatch, tmp_path):
    snapshots = [(1234, 5678), (1234, 5678)]
    monkeypatch.setattr(probe_module, "_service_snapshot", lambda: snapshots.pop(0))
    handle_path = tmp_path / "fd"
    handle_path.touch()
    monkeypatch.setattr(probe_module, "_open_namespace_tmp", lambda pid: os.open(handle_path, os.O_RDONLY))
    calls = []
    def read(name, slug, now, *, directory_fd):
        calls.append((name, slug, directory_fd))
        return probe_module._project(payload(slug), slug, NOW)
    monkeypatch.setattr(probe_module, "_read_attempt_file", read)
    return snapshots, calls


def test_complete_probe_is_bounded_to_two_fixed_leaves_and_honest_turtle(mocked_service):
    snapshots, calls = mocked_service
    result = probe_module.probe()
    assert result["status"] == "read_complete" and result["api_pid_stable"] is True
    assert [(name, slug) for name, slug, fd in calls] == list(probe_module.FILES.values())
    assert set(result["attempts"]) == {"momentum", "cup"}
    assert result["turtle"] == {"available": False, "reason": "dedicated_attempt_not_persisted"}


@pytest.mark.parametrize("after", [(1235, 5678), (1234, 5679)])
def test_pid_change_discards_attempt_evidence(mocked_service, after):
    snapshots, calls = mocked_service
    snapshots[1] = after
    result = probe_module.probe()
    assert result["status"] == "inconclusive" and result["reason"] == "api_process_changed"
    assert "attempts" not in result and result["api_pid_stable"] is False


def test_probe_does_not_import_app_or_read_environment():
    import ast
    tree = ast.parse(Path(SPEC.origin).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert imported == {"json", "os", "re", "stat", "subprocess", "datetime"}
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"} for node in ast.walk(tree))
