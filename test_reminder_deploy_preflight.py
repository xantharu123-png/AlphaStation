"""Portable, offline tests for the strictly read-only Linux deploy preflight."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parent / "scripts" / "check_reminder_deploy.py"
SPEC = importlib.util.spec_from_file_location("reminder_preflight", SOURCE)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc).timestamp()


def row():
    return {"id": "private-identity", "owner_email": "SECRET_USER@example.test", "ticker": "SECRET_TICKER",
            "asset_type": "stock", "condition": "retest", "status": "active", "row": {"secret": "PRIVATE_TOKEN"},
            "created_at": "2026-09-24T10:00:00Z", "created_at_epoch": NOW - 7200,
            "expires_at": NOW + 7200, "expires_at_iso": "2026-09-24T14:00:00Z"}


def test_summary_contains_no_private_records_and_never_changes_input():
    data = json.dumps([row()]).encode()
    before = bytes(data)
    result = check.summarize(data, NOW)
    assert data == before
    assert result["records"] == result["pending_or_unresolved"] == 1
    assert result["by_status"] == {"active": 1}
    assert all(secret not in json.dumps(result) for secret in ("SECRET", "PRIVATE", "private-identity"))


def test_valid_legacy_inventory_preserves_strict_counts_and_original_bytes():
    body = json.dumps([row()]).encode()
    original = bytes(body)
    strict = check.summarize(body, NOW)
    legacy = check.summarize(body, NOW, legacy=True)
    assert {key: legacy[key] for key in strict} == strict
    assert legacy["invalid_records"] == legacy["pending_or_unresolved_unknown_records"] == 0
    assert legacy["invalid_reason_counts"] == {} and body == original


@pytest.mark.parametrize("state", ["active", "triggered", "cancelled"])
def test_orphan_legacy_record_is_counted_not_repaired_or_silently_dropped(state):
    item = row(); item.pop("owner_email"); item["status"] = state
    body = json.dumps([item]).encode(); original = bytes(body)
    result = check.summarize(body, NOW, legacy=True)
    assert result["records"] == result["invalid_records"] == 1
    assert result["schema_valid"] is False and result["by_status"] == {state: 1}
    assert result["invalid_reason_counts"] == {"missing_owner": 1}
    assert result["pending_or_unresolved"] == result["pending_or_unresolved_unknown_records"] == 1
    assert body == original and "owner_email" not in item
    assert not any(secret in json.dumps(result) for secret in ("SECRET", "PRIVATE", "private-identity"))
    with pytest.raises(ValueError):
        check.summarize(body, NOW)


@pytest.mark.parametrize("field,value,reason", [("id", None, "missing_id"), ("id", 123, "invalid_id"),
                                                ("owner_email", None, "missing_owner"),
                                                ("owner_email", "PRIVATE_BAD_OWNER", "invalid_owner")])
def test_legacy_identity_diagnostics_distinguish_missing_and_invalid(field, value, reason):
    item = row(); item[field] = value
    result = check.summarize(json.dumps([item]).encode(), NOW, legacy=True)
    assert result["invalid_reason_counts"] == {reason: 1}
    assert result["schema_valid"] is False
    assert "PRIVATE" not in json.dumps(result) and "SECRET" not in json.dumps(result)


def test_legacy_duplicates_null_records_and_unknown_states_are_anonymous():
    item = row()
    rows = [item, deepcopy(item), None, {"status": "SECRET_STATE", "email_delivery_status": "SECRET_DELIVERY"}]
    body = json.dumps(rows).encode(); original = bytes(body)
    result = check.summarize(body, NOW, legacy=True)
    assert result["records"] == 4 and result["invalid_records"] == 3
    assert result["by_status"] == {"active": 2, "unknown": 2}
    assert result["by_delivery_status"] == {"not_triggered": 2, "unknown": 2}
    assert result["invalid_reason_counts"] == {"duplicate_id": 1, "invalid_record": 1, "missing_id": 1, "missing_owner": 1}
    assert result["pending_or_unresolved_unknown_records"] == 3
    assert body == original and "SECRET" not in json.dumps(result) and "private-identity" not in json.dumps(result)


@pytest.mark.parametrize("body", [b'{"PRIVATE":', b'{"private":"SECRET"}', b'null'])
def test_legacy_inventory_never_relaxes_json_or_root_list_validation(body):
    with pytest.raises(ValueError):
        check.summarize(body, NOW, legacy=True)


def test_legacy_nonidentity_schema_failure_stays_unresolved_without_private_detail():
    item = row(); item["created_at"] = "PRIVATE invalid date"
    result = check.summarize(json.dumps([item]).encode(), NOW, legacy=True)
    assert result["invalid_reason_counts"] == {"invalid_value": 1}
    assert result["schema_valid"] is False and result["pending_or_unresolved_unknown_records"] == 1
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("damage", ["nonlist", "duplicate", "owner", "state", "condition", "created_future", "expires_before", "nan", "iso_mismatch", "naive", "invalid_sent"])
def test_invalid_schema_and_dates_fail_closed(damage):
    item = row(); rows = [item]
    if damage == "nonlist": rows = {"private": "secret"}
    elif damage == "duplicate": rows.append(deepcopy(item))
    elif damage == "owner": item.pop("owner_email")
    elif damage == "state": item["status"] = "surprise"
    elif damage == "condition": item["condition"] = "unsupported"
    elif damage == "created_future": item["created_at_epoch"] = NOW + 300
    elif damage == "expires_before": item["expires_at"] = NOW - 10000
    elif damage == "nan": item["expires_at"] = float("nan")
    elif damage == "iso_mismatch": item["expires_at_iso"] = "2026-09-24T15:00:00Z"
    elif damage == "naive": item["created_at"] = "2026-09-24T10:00:00"
    elif damage == "invalid_sent": item["email_sent_at"] = "2026-09-24T15:00:00Z"
    with pytest.raises((ValueError, TypeError)):
        check.summarize(json.dumps(rows).encode(), NOW)


class FakeFilesystem:
    O_RDONLY, O_NOFOLLOW, O_NONBLOCK, O_CLOEXEC, O_DIRECTORY = 0, 1, 2, 4, 8

    def __init__(self, damage=None):
        self.damage = damage
        self.body = json.dumps([row()]).encode()
        self.stream = io.BytesIO(self.body)
        self.paths, self.calls, self.closed = {1: ""}, [], []
        self.count = 1
        self.reads = 0

    def open(self, name, flags, dir_fd):
        path = self.paths[dir_fd] + "/" + name
        self.calls.append((path, flags))
        assert flags & self.O_NOFOLLOW
        assert flags & self.O_NONBLOCK
        if self.damage == "symlink" and name == "store.json": raise OSError("symlink refused")
        if self.damage == "missing" and name == "store.json": raise FileNotFoundError()
        if self.damage == "missing_parent" and name == "absent": raise FileNotFoundError()
        if self.damage == "parent_symlink" and name == "data": raise OSError("private parent path")
        self.count += 1
        self.paths[self.count] = path
        return self.count

    def metadata(self, path, named=False):
        leaf = path.endswith("store.json")
        mode = 0o666 if self.damage == "writable" else 0o644 if self.damage == "public" else 0o600
        return SimpleNamespace(st_mode=(stat.S_IFIFO if self.damage == "fifo" else stat.S_IFREG) | mode if leaf else stat.S_IFDIR | 0o755,
                               st_uid=(0 if self.damage == "owner" else 1000) if leaf else 0,
                               st_gid=(0 if self.damage == "group" else 1000) if leaf else 0, st_dev=1, st_ino=99 if named and self.damage == "renamed" else 3,
                               st_nlink=2 if leaf and self.damage == "hardlink" else 1,
                               st_size=len(self.body) if leaf else 4096, st_mtime_ns=1, st_ctime_ns=1)

    def fstat(self, fd): return self.metadata(self.paths[fd])
    def stat(self, name, *, dir_fd, follow_symlinks):
        assert follow_symlinks is False
        return self.metadata(self.paths[dir_fd] + "/" + name, named=True)
    def read(self, fd, limit):
        self.reads += 1
        return self.stream.read(limit)
    def close(self, fd): self.closed.append(fd)


def test_pinned_no_follow_read_releases_descriptors_and_reads_no_other_path():
    fs = FakeFilesystem()
    result = check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, fs)
    assert result["records"] == 1
    assert [path for path, _ in fs.calls] == ["/data", "/data/store.json"]
    assert fs.closed == [3, 2]


@pytest.mark.parametrize("damage", ["symlink", "fifo", "hardlink", "owner", "public", "renamed"])
def test_unsafe_or_replaced_file_is_rejected_without_write(damage):
    fs = FakeFilesystem(damage)
    with pytest.raises((ValueError, OSError)):
        check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, fs)
    assert fs.closed


def test_missing_file_is_not_created():
    fs = FakeFilesystem("missing")
    assert check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, fs) == {"available": False, "reason": "missing"}


def test_missing_parent_is_absence_but_symlink_parent_still_fails():
    fs = FakeFilesystem("missing_parent")
    assert check.secure_snapshot(1, "/data/absent/store.json", 1000, 1000, NOW, fs) == {"available": False, "reason": "missing"}
    assert fs.closed == [2] and fs.reads == 0
    with pytest.raises(OSError):
        check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, FakeFilesystem("parent_symlink"))


def test_legacy_default_umask_is_inventory_warning_not_migration_approval():
    fs = FakeFilesystem("public")
    result = check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, fs, legacy=True)
    assert result["mode"] == "0o644" and result["records"] == 1
    assert result["privacy_warnings"] == ["legacy_record_not_private"]
    fs = FakeFilesystem("public")
    with pytest.raises(check.PreflightError) as failure:
        check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, fs)
    assert failure.value.code == "record_permissions_not_private"
    assert fs.reads == 0


def test_safe_snapshot_enables_tolerant_schema_inventory_only_for_legacy():
    def filesystem():
        fs = FakeFilesystem()
        item = row(); item.pop("owner_email")
        fs.body = json.dumps([item]).encode(); fs.stream = io.BytesIO(fs.body)
        return fs
    result = check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, filesystem(), legacy=True)
    assert result["schema_valid"] is False and result["invalid_reason_counts"] == {"missing_owner": 1}
    with pytest.raises(ValueError):
        check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, filesystem())


@pytest.mark.parametrize("damage,code", [("writable", "record_other_writable"), ("owner", "record_owner_mismatch"),
                                          ("group", "record_group_mismatch"), ("hardlink", "record_hardlinks"),
                                          ("fifo", "record_not_regular")])
def test_legacy_inventory_does_not_relax_integrity_guards(damage, code):
    fs = FakeFilesystem(damage)
    with pytest.raises(check.PreflightError) as failure:
        check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, fs, legacy=True)
    assert failure.value.code == code and fs.reads == 0


@pytest.mark.parametrize("value", ["/data/../root", "../secret", "/data\x00private", ""])
def test_parent_traversal_and_empty_override_fail_closed(value):
    with pytest.raises(ValueError): check.service_path(value)


def test_default_and_relative_data_paths_match_api_semantics():
    assert str(check.service_path("data_cache")) == "/home/tradingbot/app/data_cache"
    assert str(check.service_path("/srv/alpha")) == "/srv/alpha"


def test_failure_output_never_echoes_private_exception(monkeypatch, capsys):
    monkeypatch.setattr(check, "collect", lambda: (_ for _ in ()).throw(ValueError("password=SECRET")))
    assert check.main() == 2
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err
    assert json.loads(output.out)["decision"] == "hold_restart_check_failed"
    assert json.loads(output.out)["error_code"] == "invalid_value"


@pytest.mark.parametrize("damage,code", [("public", "record_permissions_not_private"),
                                          ("owner", "record_owner_mismatch"), ("group", "record_group_mismatch")])
def test_safe_diagnostics_include_stage_and_nonsecret_metadata(monkeypatch, capsys, damage, code):
    def fail():
        with check.diagnostic_stage("persistent_snapshot"):
            check.secure_snapshot(1, "/data/store.json", 1000, 1000, NOW, FakeFilesystem(damage))
    monkeypatch.setattr(check, "collect", fail)
    assert check.main() == 2
    text = capsys.readouterr().out
    result = json.loads(text)
    assert result["error_code"] == code and result["stage"] == "persistent_snapshot"
    assert result["metadata"]["mode"] in {"0o600", "0o644"}
    assert all(secret not in text for secret in ("SECRET", "PRIVATE", "private-identity", "/data/"))


@pytest.mark.parametrize("damage,code", [("owner", "invalid_record_identity"), ("timestamp", "invalid_value"),
                                         ("json", "invalid_json")])
def test_legacy_schema_failure_is_diagnostic_not_silently_migratable(monkeypatch, capsys, damage, code):
    item = row()
    if damage == "owner": item.pop("owner_email")
    if damage == "timestamp": item["created_at"] = "PRIVATE malformed date"
    body = b'{"PRIVATE":' if damage == "json" else json.dumps([item]).encode()
    def fail():
        with check.diagnostic_stage("legacy_snapshot"):
            check.summarize(body, NOW)
    monkeypatch.setattr(check, "collect", fail)
    assert check.main() == 2
    text = capsys.readouterr().out
    assert "PRIVATE" not in text and "SECRET" not in text
    result = json.loads(text)
    assert result["error_code"] == code and result["stage"] == "legacy_snapshot"


def test_unknown_code_stage_and_metadata_are_never_echoed(monkeypatch, capsys):
    def fail():
        raise check.PreflightError("password=SECRET", stage="PRIVATE", metadata={"mode": "SECRET", "uid": 1000, "SECRET": 42})
    monkeypatch.setattr(check, "collect", fail)
    assert check.main() == 2
    text = capsys.readouterr().out
    assert "SECRET" not in text and "PRIVATE" not in text
    result = json.loads(text)
    assert result["error_code"] == "unexpected_error" and result["stage"] == "preflight"
    assert result["metadata"] == {"uid": 1000}


def test_current_unsupported_delivery_state_is_inventoried():
    item = row(); item["status"] = "triggered"; item["email_delivery_status"] = "unsupported"
    assert check.summarize(json.dumps([item]).encode(), NOW)["by_delivery_status"] == {"unsupported": 1}


@pytest.mark.parametrize("records,schema_valid", [(0, True), (1, True), (1, False)])
def test_collect_legacy_permission_warning_never_grants_restart(monkeypatch, records, schema_valid):
    fake_os = SimpleNamespace(name="posix", geteuid=lambda: 0, O_RDONLY=0, O_DIRECTORY=1, O_CLOEXEC=2,
                              open=lambda *args: 9, close=lambda fd: None)
    monkeypatch.setattr(check, "os", fake_os)
    monkeypatch.setattr(check.subprocess, "check_output", lambda *args, **kwargs: "123\n")
    monkeypatch.setattr(check, "process_identity", lambda pid: (1000, 1000, 88))
    class EnvironmentPath:
        def __truediv__(self, other): return self
        def read_bytes(self): return b"OTHER_SECRET=PRIVATE\0"
    monkeypatch.setattr(check, "Path", lambda *args: EnvironmentPath())
    calls = []
    def snapshot(root, path, uid, gid, now, *, legacy=False):
        calls.append(legacy)
        return ({"available": True, "records": records, "schema_valid": schema_valid, "mode": "0o644", "privacy_warnings": ["legacy_record_not_private"]}
                if legacy else {"available": False, "reason": "missing"})
    monkeypatch.setattr(check, "secure_snapshot", snapshot)
    result = check.collect()
    assert calls == [True, False, True]
    assert result["decision"] == ("hold_restart_for_migration_review" if records else "no_legacy_records_at_snapshot")
    assert result["requires_quiescent_migration_review"] is bool(records)
    assert result["requires_legacy_schema_review"] is (not schema_valid)
    assert not result["restart_performed"] and not result["migration_performed"] and not result["backup_created"]
    assert "privacy_warnings" in result["legacy"]
    assert "PRIVATE" not in json.dumps(result)


def test_script_has_no_application_import_or_mutating_syscall():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name and (name == "api" or name.startswith("modules")) for name in roots)
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls.intersection({"write", "write_text", "write_bytes", "mkdir", "chmod", "chown", "unlink", "rename", "sendmail"})
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "replace" and isinstance(node.func.value, ast.Name)
                   and node.func.value.id in {"os", "os_api"} for node in ast.walk(tree))
    assert "restart" not in [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
