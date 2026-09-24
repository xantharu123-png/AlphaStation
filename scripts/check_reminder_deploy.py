"""Read-only pre-deploy reminder inventory. Run reviewed stdin with python3 -I -.

No application imports, writes, migrations, provider requests, SMTP or restart.
A live snapshot is NOT a migration: the running worker can still change state.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

APP = "/home/tradingbot/app"
UNIT = "tradingbot-api.service"
MAX_BYTES = 16 * 1024 * 1024
STATES = {"active", "triggered", "cancelled", "expired", "invalidated"}
DELIVERY = {"sent", "failed", "retry_pending", "disabled", "not_requested", "not_triggered",
            "attempt_in_flight", "uncertain", "uncertain_manual_reconciliation", "outbox_owned", "unsupported"}
ERROR_CODES = {
    "invalid_path", "invalid_timestamp", "naive_timestamp", "invalid_json",
    "invalid_record_list", "invalid_record", "invalid_record_identity",
    "invalid_record_state", "invalid_record_condition", "invalid_record_chronology",
    "inconsistent_creation_timestamp", "inconsistent_expiry_timestamp",
    "invalid_record_event_time", "invalid_delivery_state", "invalid_saved_row",
    "unsafe_parent", "record_not_regular", "record_hardlinks", "record_owner_mismatch",
    "record_group_mismatch", "record_permissions_not_private", "record_other_writable", "record_too_large",
    "file_changed_during_read", "unexpected_process_directory", "unexpected_process_privileges",
    "root_linux_reader_required", "api_not_running", "runtime_changed_during_check",
    "permission_denied", "runtime_path_missing", "os_read_failed", "invalid_value",
    "service_query_failed", "unexpected_error",
}
STAGES = {"preflight", "reader_privileges", "service_pid", "process_identity",
          "runtime_environment", "runtime_namespace", "legacy_snapshot",
          "persistent_snapshot", "legacy_recheck", "process_recheck"}


class PreflightError(ValueError):
    """Only reviewed codes and numeric metadata may leave the privileged reader."""
    def __init__(self, code, *, stage="preflight", metadata=None):
        super().__init__(code if code in ERROR_CODES else "unexpected_error")
        self.code = code if code in ERROR_CODES else "unexpected_error"
        self.stage = stage if stage in STAGES else "preflight"
        allowed = {"mode", "uid", "gid", "expected_uid", "expected_gid", "hardlinks", "size_bytes", "max_bytes"}
        self.metadata = {key: value for key, value in (metadata or {}).items()
                         if key in allowed and type(value) is int and value >= 0}


def error_code(exc):
    if isinstance(exc, PreflightError):
        return exc.code if exc.code in ERROR_CODES else "unexpected_error"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, FileNotFoundError):
        return "runtime_path_missing"
    if isinstance(exc, subprocess.SubprocessError):
        return "service_query_failed"
    if isinstance(exc, OSError):
        return "os_read_failed"
    if isinstance(exc, (ValueError, TypeError, OverflowError)):
        # Existing explicit validation messages are fixed tokens, never substrings.
        if type(exc) is ValueError and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in ERROR_CODES:
            return exc.args[0]
        return "invalid_value"
    return "unexpected_error"


@contextmanager
def diagnostic_stage(name):
    try:
        yield
    except PreflightError as exc:
        if exc.stage == "preflight":
            exc.stage = name if name in STAGES else "preflight"
        raise
    except Exception as exc:
        # Never serialize exception text: JSON/path/Unicode errors can quote secrets.
        raise PreflightError(error_code(exc), stage=name) from None


def service_path(value, base=APP):
    raw = str(value)
    path = PurePosixPath(raw)
    if not raw or "\x00" in raw or ".." in path.parts:
        raise ValueError("invalid_path")
    if not path.is_absolute():
        path = PurePosixPath(base) / path
    return path


def epoch(value):
    if isinstance(value, bool):
        raise ValueError("invalid_timestamp")
    if isinstance(value, (float, int)):
        result = float(value)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("naive_timestamp")
        result = dt.timestamp()
    if not math.isfinite(result) or result <= 0:
        raise ValueError("invalid_timestamp")
    return result


def summarize_legacy(rows, body, now):
    """Anonymous inventory only: malformed legacy rows are never approved.

    Keep every list element in the count. Invalid records conservatively count
    as unresolved even when their claimed state is cancelled; no owner or event
    identity is reconstructed, corrected, dropped or exposed.
    """
    seen, states, delivery, reasons = set(), Counter(), Counter(), Counter()
    invalid_records = pending = unknown_pending = 0
    for item in rows:
        issues = []
        if not isinstance(item, dict):
            issues.append("invalid_record")
            states["unknown"] += 1
            delivery["unknown"] += 1
        else:
            state, status = item.get("status"), item.get("email_delivery_status", "not_triggered")
            states[state if isinstance(state, str) and state in STATES else "unknown"] += 1
            delivery[status if isinstance(status, str) and status in DELIVERY else "unknown"] += 1
            identity, owner = item.get("id"), item.get("owner_email")
            if identity is None or identity == "":
                issues.append("missing_id")
            elif not isinstance(identity, str) or len(identity) > 128:
                issues.append("invalid_id")
            else:
                if identity in seen:
                    issues.append("duplicate_id")
                seen.add(identity)
            if owner is None or isinstance(owner, str) and not owner.strip():
                issues.append("missing_owner")
            elif not isinstance(owner, str) or not 3 <= len(owner) <= 320 or "@" not in owner:
                issues.append("invalid_owner")
            if not issues:
                try:
                    # Reuse the unchanged strict validator, never the application.
                    valid = summarize(json.dumps([item]).encode("utf-8"), now)
                except (ValueError, TypeError, OverflowError, OSError) as exc:
                    issues.append(error_code(exc))
                else:
                    pending += valid["pending_or_unresolved"]
        if issues:
            invalid_records += 1
            unknown_pending += 1
            pending += 1
            reasons.update(set(issues))
    return {"available": True, "schema_valid": invalid_records == 0, "records": len(rows),
            "by_status": dict(states), "by_delivery_status": dict(delivery),
            "invalid_records": invalid_records, "invalid_reason_counts": dict(reasons),
            "pending_or_unresolved": pending, "pending_or_unresolved_unknown_records": unknown_pending,
            "sha256": hashlib.sha256(body).hexdigest()}


def summarize(body, now, *, legacy=False):
    rows = json.loads(body)
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError("invalid_record_list")
    if legacy:
        return summarize_legacy(rows, body, now)
    ids, states, delivery = set(), Counter(), Counter()
    pending = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_record")
        identity, owner = row.get("id"), row.get("owner_email")
        if (not isinstance(identity, str) or not 0 < len(identity) <= 128 or identity in ids
                or not isinstance(owner, str) or not 3 <= len(owner) <= 320 or "@" not in owner):
            raise ValueError("invalid_record_identity")
        ids.add(identity)
        state = row.get("status")
        if state not in STATES or row.get("asset_type") not in {"stock", "crypto"}:
            raise ValueError("invalid_record_state")
        if row.get("condition") not in {"trigger", "retest", "continuation", "trigger_or_retest"}:
            raise ValueError("invalid_record_condition")
        created = epoch(row.get("created_at_epoch", row.get("created_at")))
        expires = epoch(row.get("expires_at"))
        if created > now + 5 or expires < created:
            raise ValueError("invalid_record_chronology")
        if row.get("created_at") and abs(epoch(row["created_at"]) - created) > 1:
            raise ValueError("inconsistent_creation_timestamp")
        if row.get("expires_at_iso") and abs(epoch(row["expires_at_iso"]) - expires) > 1:
            raise ValueError("inconsistent_expiry_timestamp")
        for field in ("updated_at", "triggered_at", "email_sent_at"):
            if row.get(field) and not created <= epoch(row[field]) <= now + 5:
                raise ValueError("invalid_record_event_time")
        status = row.get("email_delivery_status", "not_triggered")
        if status not in DELIVERY:
            raise ValueError("invalid_delivery_state")
        if row.get("row") is not None and not isinstance(row["row"], dict):
            raise ValueError("invalid_saved_row")
        states[state] += 1
        delivery[status] += 1
        pending += int((state == "active" and expires > now) or (state == "triggered" and status not in {"sent", "failed", "disabled", "not_requested"}))
    return {"available": True, "schema_valid": True, "records": len(rows),
            "by_status": dict(states), "by_delivery_status": dict(delivery),
            "pending_or_unresolved": pending, "sha256": hashlib.sha256(body).hexdigest()}


def secure_snapshot(root_fd, path, uid, gid, now, os_api=os, *, legacy=False):
    """Pin namespace parents; no-follow every user-controlled component."""
    target = service_path(path)
    descriptors = []
    parent = root_fd
    flags = os_api.O_RDONLY | os_api.O_NOFOLLOW | os_api.O_NONBLOCK | os_api.O_CLOEXEC
    try:
        for component in target.parts[1:-1]:
            try:
                parent = os_api.open(component, flags | os_api.O_DIRECTORY, dir_fd=parent)
            except FileNotFoundError:
                # A never-created data directory also means no file. Other
                # errors (including symlink/non-directory parents) still hold.
                return {"available": False, "reason": "missing"}
            descriptors.append(parent)
            meta = os_api.fstat(parent)
            if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid not in {0, uid}
                    or meta.st_mode & 0o002 and not meta.st_mode & stat.S_ISVTX):
                raise PreflightError("unsafe_parent", metadata={"mode": stat.S_IMODE(meta.st_mode), "uid": meta.st_uid})
        try:
            fd = os_api.open(target.name, flags, dir_fd=parent)
        except FileNotFoundError:
            return {"available": False, "reason": "missing"}
        descriptors.append(fd)
        before = os_api.fstat(fd)
        metadata = {"mode": stat.S_IMODE(before.st_mode), "uid": before.st_uid, "gid": before.st_gid,
                    "hardlinks": before.st_nlink, "size_bytes": before.st_size}
        if not stat.S_ISREG(before.st_mode):
            raise PreflightError("record_not_regular", metadata=metadata)
        if before.st_nlink != 1:
            raise PreflightError("record_hardlinks", metadata=metadata)
        if before.st_uid != uid:
            raise PreflightError("record_owner_mismatch", metadata=dict(metadata, expected_uid=uid))
        if before.st_gid != gid:
            raise PreflightError("record_group_mismatch", metadata=dict(metadata, expected_gid=gid))
        if before.st_mode & 0o022:
            raise PreflightError("record_other_writable", metadata=metadata)
        # The old writer used open(..., 'w') and inherited the service umask.
        # Read access for others is a privacy warning, not permission to mutate
        # the trusted-owner legacy file. Never relax this for a persistent target.
        privacy_warning = bool(before.st_mode & 0o077)
        if privacy_warning and not legacy:
            raise PreflightError("record_permissions_not_private", metadata=metadata)
        if before.st_size > MAX_BYTES:
            raise PreflightError("record_too_large", metadata=dict(metadata, max_bytes=MAX_BYTES))
        pieces, length = [], 0
        while length <= MAX_BYTES:
            piece = os_api.read(fd, min(16384, MAX_BYTES + 1 - length))
            if not piece:
                break
            pieces.append(piece)
            length += len(piece)
        after = os_api.fstat(fd)
        named = os_api.stat(target.name, dir_fd=parent, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink", "st_uid", "st_gid")
        if length > MAX_BYTES or any(getattr(before, key) != getattr(meta, key) for meta in (after, named) for key in fields):
            raise ValueError("file_changed_during_read")
        result = summarize(b"".join(pieces), now, legacy=legacy)
        result.update(mode=oct(stat.S_IMODE(before.st_mode)), uid=uid, gid=gid, hardlinks=before.st_nlink)
        if privacy_warning:
            result["privacy_warnings"] = ["legacy_record_not_private"]
        return result
    finally:
        for descriptor in reversed(descriptors):
            os_api.close(descriptor)


def process_identity(pid):
    import pwd
    account = pwd.getpwnam("tradingbot")
    proc = Path("/proc") / str(pid)
    if os.readlink(proc / "cwd") != APP:
        raise ValueError("unexpected_process_directory")
    status = (proc / "status").read_text(encoding="ascii")
    for key, expected in (("Uid", account.pw_uid), ("Gid", account.pw_gid)):
        values = next((line.split(":", 1)[1].split() for line in status.splitlines() if line.startswith(key + ":")), [])
        if expected == 0 or values != [str(expected)] * 4:
            raise ValueError("unexpected_process_privileges")
    text = (proc / "stat").read_text(encoding="ascii")
    started = int(text[text.rfind(")") + 1:].split()[19])
    return account.pw_uid, account.pw_gid, started


def collect():
    with diagnostic_stage("reader_privileges"):
        if os.name != "posix" or os.geteuid() != 0:
            raise ValueError("root_linux_reader_required")
    with diagnostic_stage("service_pid"):
        pid_text = subprocess.check_output(["/usr/bin/systemctl", "show", UNIT, "--property=MainPID", "--value"], text=True, timeout=10).strip()
        if not pid_text.isdecimal() or int(pid_text) <= 1:
            raise ValueError("api_not_running")
        pid = int(pid_text)
    with diagnostic_stage("process_identity"):
        identity = process_identity(pid)
    uid, gid, started = identity
    # Read process environment privately. Only one path is retained, never
    # credentials, recipient names or the raw environment in stdout/errors.
    with diagnostic_stage("runtime_environment"):
        environment = (Path("/proc") / str(pid) / "environ").read_bytes()
        data_dir = APP + "/data_cache"
        for field in environment.split(b"\0"):
            if field.startswith(b"ALPHA_DATA_DIR="):
                data_dir = field.split(b"=", 1)[1].decode("utf-8")
        del environment
        target = service_path(data_dir) / "trade_reminders.json"
    source = PurePosixPath("/tmp/alphastation_trade_reminders.json")
    # Intentional, kernel-owned namespace link only; all following components
    # are opened relative to this pinned fd with O_NOFOLLOW.
    with diagnostic_stage("runtime_namespace"):
        root_fd = os.open(f"/proc/{pid}/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    now = datetime.now(timezone.utc).timestamp()
    try:
        with diagnostic_stage("legacy_snapshot"):
            old = secure_snapshot(root_fd, source, uid, gid, now, legacy=True)
        with diagnostic_stage("persistent_snapshot"):
            new = secure_snapshot(root_fd, target, uid, gid, now)
        with diagnostic_stage("legacy_recheck"):
            if old != secure_snapshot(root_fd, source, uid, gid, now, legacy=True):
                raise ValueError("runtime_changed_during_check")
        with diagnostic_stage("process_recheck"):
            if identity != process_identity(pid):
                raise ValueError("runtime_changed_during_check")
    finally:
        os.close(root_fd)
    needs_quiescence = bool(old.get("available") and old.get("records"))
    return {"kind": "reminder_deploy_preflight", "schema_version": 2, "read_only": True,
            "captured_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "api_pid": pid, "process_start_ticks": started, "source_path": str(source), "target_path": str(target),
            "legacy": old, "persistent": new, "backup_created": False, "migration_performed": False,
            "restart_performed": False, "requires_quiescent_migration_review": needs_quiescence,
            "requires_legacy_schema_review": old.get("schema_valid") is False,
            "target_must_not_be_overwritten": bool(new.get("available")),
            "decision": "hold_restart_for_migration_review" if needs_quiescence else "no_legacy_records_at_snapshot",
            "limits": "Live snapshot only. No lock on running writer, backup or permission to restart. Do not create reminders between this check and deployment."}


def main():
    try:
        result = collect()
    except Exception as exc:
        # Exception text may contain a configured path or private payload.
        result = {"kind": "reminder_deploy_preflight", "schema_version": 2, "read_only": True,
                  "decision": "hold_restart_check_failed", "error_code": error_code(exc),
                  "stage": exc.stage if isinstance(exc, PreflightError) and exc.stage in STAGES else "preflight",
                  "error_type": "ValueError" if isinstance(exc, ValueError) else "ReadError"}
        if isinstance(exc, PreflightError) and exc.metadata:
            result["metadata"] = dict(exc.metadata)
            if "mode" in result["metadata"]:
                result["metadata"]["mode"] = oct(result["metadata"]["mode"])
        print(json.dumps(result))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
