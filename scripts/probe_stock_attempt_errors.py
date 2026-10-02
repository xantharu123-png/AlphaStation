"""Bounded, read-only stock-attempt error counters from the API /tmp namespace.

Pipe this standalone standard-library script to server Python. It does not
import the application, read its environment or send a request to any provider.
Only files whose signal-row array is empty can supply diagnostic evidence.
"""
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone


UNIT = "tradingbot-api.service"
MAX_BYTES = 128 * 1024
FILES = {
    "momentum": ("stock_strategy_momentum_breakout_long_attempt.json", "momentum_breakout_long"),
    "cup": ("stock_strategy_cup_and_handle_breakout_attempt.json", "cup_and_handle_breakout"),
}
ERROR_CODES = frozenset({
    "scan_data_unavailable", "scan_provider_unauthorized", "scan_provider_rate_limited",
    "scan_data_incomplete", "scan_data_invalid", "scan_failed", "scan_timeout",
    "scan_already_running", "scan_cache_publish_failed", "scan_partial_cache",
})
COUNT_KEYS = frozenset({
    "checked", "total", "universe_count", "common_stock_universe_count",
    "raw_matches_before_special_filter", "final_results", "max_results",
    "provider_requests", "history_cache_hits", "rate_wait_seconds", "elapsed_seconds",
    "leaf_elapsed_seconds", "special_filter_input_count", "special_filter_checked_count",
    "special_filter_unexamined_count", "special_filter_limit", "excluded_data_symbols",
    "empty_history_symbols", "invalid_history_symbols", "data_retry_attempts",
    "data_retry_recovered", "data_retry_failed", "data_retry_budget_exhausted",
    "data_retry_observation_mismatches",
})
HISTORY_REASONS = frozenset({
    "invalid_payload", "provider_status", "invalid_json", "missing_results",
    "invalid_results_type", "invalid_result_count", "invalid_query_count",
    "result_count_mismatch", "contradictory_empty_response", "unexpected_pagination",
    "invalid_bar_type", "invalid_bar_value", "invalid_bar_geometry",
    "invalid_bar_timestamp", "invalid_data_conversion", "stale_daily_history",
    "timeout", "connection_failure", "tls_failure", "http_unauthorized",
    "http_rate_limited", "http_server_error", "http_client_error",
    "http_unexpected_status", "symbol_exclusion_limit",
})
HISTORY_FIELDS = frozenset({"t", "o", "h", "l", "c", "v", "bar", "unknown"})
VALUE_CLASSES = frozenset({
    "missing", "null", "boolean", "non_numeric", "non_finite", "zero_price",
    "negative_price", "negative_volume", "nonpositive_timestamp",
    "nonascending_timestamp", "timestamp_out_of_range", "future_timestamp",
    "invalid_geometry", "unknown",
})
POSITIONS = frozenset({"only", "first", "interior", "last", "unknown"})
REJECTIONS = ERROR_CODES | frozenset({
    "empty_daily_history", "invalid_daily_history", "insufficient_daily_history", "exception",
    "daily_reference:history_not_current", "daily_reference:previous_session_missing",
    "daily_reference:invalid_reference_value", "daily_reference:reference_price_mismatch",
    "daily_reference:reference_volume_mismatch",
})
PHASES = frozenset({
    "starting", "universe", "history", "analyzing", "special_filter", "enrichment",
    "publish", "mail_guard", "work_timeout", "error", "complete",
})
STAGES = frozenset({"history", "structure", "execution_history", "plan", "cache_publish", "special_filter"})


class ProbeError(Exception):
    """Code-owned public failure; never expose an exception or file content."""


def _counts(value, allowed):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in sorted(allowed)
            if type(value.get(key)) is int and 0 <= value[key] <= 10**9}


def _timestamp(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ProbeError("invalid_attempt_time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ProbeError("invalid_attempt_time") from None


def _project(payload, expected_slug, now):
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1 or payload.get("attempt_kind") != "stock_strategy"
            or payload.get("strategy_slug") != expected_slug or payload.get("results") != []
            or not isinstance(payload.get("run_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", payload["run_id"])
            or not isinstance(payload.get("code_revision"), str)
            or not re.fullmatch(r"(?:[0-9a-f]{12}(?:-dirty|-tree-unknown)?|unknown)", payload["code_revision"])):
        raise ProbeError("invalid_attempt_identity")
    started, updated = _timestamp(payload.get("started_at")), _timestamp(payload.get("updated_at"))
    if not started <= updated <= now:
        raise ProbeError("invalid_attempt_time")
    status, error = payload.get("status"), payload.get("error_code")
    diagnostics = payload.get("diagnostics")
    if not isinstance(status, str) or status not in {"running", "complete", "error"} or not isinstance(diagnostics, dict):
        raise ProbeError("invalid_attempt_status")
    if status == "error" and (not isinstance(error, str) or error not in ERROR_CODES or payload.get("result_count") is not None):
        raise ProbeError("invalid_attempt_status")
    if status != "error" and error not in (None, ""):
        raise ProbeError("invalid_attempt_status")
    count = payload.get("result_count")
    if status == "complete":
        if (type(count) is not int or not 0 <= count <= 10**9
                or not isinstance(diagnostics.get("coverage"), str)
                or diagnostics.get("coverage") not in {"complete", "complete_with_exclusions"}
                or type(diagnostics.get("final_results")) is not int or diagnostics["final_results"] != count):
            raise ProbeError("invalid_attempt_completion")
    elif count is not None:
        raise ProbeError("invalid_attempt_status")
    projected = _counts(diagnostics, COUNT_KEYS)
    if diagnostics.get("final_results") is None:
        projected["final_results"] = None
    if isinstance(diagnostics.get("coverage"), str) and diagnostics["coverage"] in {"complete", "complete_with_exclusions", "incomplete"}:
        projected["coverage"] = diagnostics["coverage"]
    if isinstance(diagnostics.get("runtime_phase"), str) and diagnostics["runtime_phase"] in PHASES:
        projected["runtime_phase"] = diagnostics["runtime_phase"]
    if isinstance(diagnostics.get("data_mode"), str) and diagnostics["data_mode"] in {"completed_daily_swing", "live_snapshot"}:
        projected["data_mode"] = diagnostics["data_mode"]
    if "analysis_as_of" in diagnostics:
        try:
            projected["analysis_as_of"] = _timestamp(diagnostics["analysis_as_of"]).isoformat()
        except ProbeError:
            pass
    for key, allowed in (
        ("stock_history_error_counts", HISTORY_REASONS),
        ("stock_history_error_fields", HISTORY_FIELDS),
        ("stock_history_error_value_classes", VALUE_CLASSES),
        ("stock_history_error_positions", POSITIONS),
        ("rejected", REJECTIONS), ("stage_elapsed_ms", STAGES),
    ):
        if isinstance(diagnostics.get(key), dict):
            projected[key] = _counts(diagnostics[key], allowed)
    if status != "complete":
        projected.update(coverage="incomplete", final_results=None)
    return {"available": True, "status": status, "error_code": error if status == "error" else None,
            "code_revision": payload["code_revision"], "started_at": started.isoformat(),
            "updated_at": updated.isoformat(), "diagnostics": projected}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("invalid_attempt_json")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ProbeError("invalid_attempt_json")


def _decode_attempt(raw):
    """Pure strict decoder; independent of live filesystem race detection."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("invalid_attempt_json") from None


def _fingerprint(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _read_attempt_file(filename, expected_slug, now, *, directory_fd=None):
    """The live caller supplies a fixed leaf and an already opened /tmp fd."""
    try:
        before = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ProbeError("attempt_not_regular")
        if before.st_size > MAX_BYTES:
            raise ProbeError("attempt_too_large")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(filename, flags, dir_fd=directory_fd)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or _fingerprint(before) != _fingerprint(opened):
                raise ProbeError("attempt_changed_during_read")
            raw = os.read(descriptor, MAX_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if len(raw) > MAX_BYTES:
            raise ProbeError("attempt_too_large")
        if _fingerprint(opened) != _fingerprint(after) or _fingerprint(after) != _fingerprint(current):
            raise ProbeError("attempt_changed_during_read")
        payload = _decode_attempt(raw)
        return _project(payload, expected_slug, now)
    except FileNotFoundError:
        raise ProbeError("attempt_missing") from None
    except PermissionError:
        raise ProbeError("attempt_unreadable") from None
    except OSError:
        raise ProbeError("attempt_read_failed") from None


def _service_snapshot():
    try:
        response = subprocess.run(
            ["/usr/bin/systemctl", "show", UNIT, "--property=MainPID", "--value"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=5,
        )
        value = response.stdout.strip()
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
            raise ProbeError("api_process_unavailable")
        pid = int(value)
        with open(f"/proc/{pid}/stat", "rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ProbeError("api_process_unavailable")
        # /proc stat field22 follows the final ')' of the process-name field.
        closing = raw.rfind(b")")
        if closing < 0:
            raise ProbeError("api_process_unavailable")
        fields = raw[closing + 2:].split()
        ticks = int(fields[19])
        if ticks <= 0:
            raise ProbeError("api_process_unavailable")
        return pid, ticks
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        raise ProbeError("api_process_unavailable") from None


def _open_namespace_tmp(pid):
    # /proc/PID/root is the expected kernel-managed namespace link. NOFOLLOW
    # applies to its tmp directory and to each leaf, not to that required link.
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(f"/proc/{pid}/root", flags)
        try:
            return os.open("tmp", flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        finally:
            os.close(root_fd)
    except OSError:
        raise ProbeError("service_tmp_unavailable") from None


def probe():
    result = {"kind": "stock_attempt_errors", "schema_version": 1, "read_only": True,
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "turtle": {"available": False, "reason": "dedicated_attempt_not_persisted"}}
    try:
        before = _service_snapshot()
        directory_fd = _open_namespace_tmp(before[0])
        try:
            attempts = {}
            for label, (filename, slug) in FILES.items():
                try:
                    attempts[label] = _read_attempt_file(filename, slug, datetime.now(timezone.utc), directory_fd=directory_fd)
                except ProbeError as error:
                    attempts[label] = {"available": False, "reason": str(error)}
        finally:
            os.close(directory_fd)
        if _service_snapshot() != before:
            raise ProbeError("api_process_changed")
        result.update(status="read_complete", api_pid=before[0], api_pid_stable=True, attempts=attempts,
                      source="api_service_tmp_namespace",
                      limits="Aggregate snapshot only; no rows, no scan, no update, no mail. Turtle error is held in authenticated API RAM.")
    except ProbeError as error:
        result.update(status="inconclusive", reason=str(error), api_pid_stable=False)
    return result


def main():
    print(json.dumps(probe(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
