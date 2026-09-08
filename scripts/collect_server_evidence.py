#!/usr/bin/env python3
"""Standalone read-only server evidence export; stdlib only, no app-code import.

Run the reviewed local source through `ssh ... python3 -I -`. Only stdout is
written. No DB migration, cache update, scan, market request, mail or order.
The projected tracker rows are private audit data, NOT a public API response.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import http.client
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import sys


# Same arithmetic allowlist as signal_performance_breakdown. No recipients,
# account blobs, tokens, mail body or environment contents are exported.
TRACKER_COLUMNS = """
id created_at scanner mail_class channel status asset_class direction strategy
trade_horizon evaluation_horizon_bars market_regime code_revision
evaluation_model_version fill_evidence_mode path_extrema_evidence_mode
origin_evidence public_signal_ref delivery_accepted_at entry stop tp1 tp2
entry_filled_at entry_fill_price closed_at r_realized r_realized_upper
tp1_hit_at tp2_hit_at stop_hit_at outcome_detail max_favorable_r
be_trigger_at be_activated_at be_mail_sent_at be_delivery_evidence_key
be_exit_at be_exit_fill_price be_exit_evidence_mode be_exit_tp1_order
stop_gap_slippage_r
""".split()
PATH_ENV = {"ALPHA_DATA_DIR", "SIGNAL_TRACKER_DB_PATH", "ALPHA_RUNTIME_TMP_DIR"}
REQUIRED_COLUMNS = {"id", "created_at", "scanner", "mail_class", "status"}
CACHE_STATUSES = frozenset({"scanning", "running", "done", "error", "stopped", "idle", "pending"})
DIAGNOSTIC_COUNTS = frozenset("""
total checked history_available analyzed indicator_passed data_failures
analysis_errors final_results universe_count common_stock_universe_count
raw_matches_before_special_filter max_results
""".split())
STAGE_COUNTS = frozenset("""
snapshot_universe valid_symbol_and_prev_close common_stock_asset priced_snapshot
change_filter price_filter close_position_filter gap_filter dollar_volume_filter
vortag_filter rvol_filter momentum_breakout_gate momentum_completed_5m_confirmation
reversal_ad_gate raw_matches_before_special_filter final_results
""".split())
# These are exact codes, never a wildcard for provider messages or ticker names.
DATA_FAILURE_CODES = frozenset("""
scan_data_unavailable scan_provider_unauthorized scan_provider_rate_limited
scan_provider_error scan_data_invalid scan_data_incomplete scan_analysis_failed exception
""".split())
PUBLIC_SCAN_ERROR_CODES = frozenset("""
scan_data_unavailable scan_provider_unauthorized scan_provider_rate_limited
scan_data_incomplete scan_data_invalid
""".split())
DATA_ERROR_REASONS = frozenset("""
invalid_payload provider_status invalid_json missing_results invalid_results_type
invalid_result_count invalid_query_count result_count_mismatch contradictory_empty_response
unexpected_pagination invalid_bar_type invalid_bar_value invalid_bar_geometry
invalid_bar_timestamp invalid_data_conversion
""".split())
REJECTION_CODES = DATA_FAILURE_CODES | frozenset("""
insufficient_daily_history insufficient_completed_history insufficient_dollar_liquidity
rvol_anomaly spac_nav already_broke_out cumulative_pump indicator_or_hard_gate_contract
invalid_symbol_or_missing_prev_close missing_price_or_prev_close change_filter
price_filter close_position_filter gap_filter dollar_volume_filter vortag_filter
rvol_filter momentum_breakout_gate premarket_extension_guard reversal_ad_gate
premarket_dollar_volume_filter premarket_missing_quote premarket_spread_guard
plan:invalid plan:insufficient_history plan:invalid_ohlc plan:invalid_live_price
plan:range_too_narrow plan:atr_too_small plan:entry_too_extended
plan:structure_cutoff_missing plan:structural_barrier_blocked plan:invalid_geometry_or_rr
momentum:not_enough_daily_history momentum:invalid_momentum_inputs
momentum:daily_momentum_too_small momentum:rvol_below_breakout_threshold
momentum:daily_close_not_near_high momentum:no_momentum_breakout_structure
momentum:price_below_ema20 momentum:no_ema20_50_trend_reclaim
momentum:rsi_too_weak_for_momentum momentum:rsi_overheated
momentum:bounce_after_recent_selloff momentum:incoherent_signal_direction
momentum:intraday_unavailable momentum:intraday_stale momentum:intraday_not_confirmed
momentum:intraday_failed_breakout momentum:intraday_stale_breakout
momentum:intraday_stale_extension momentum:intraday_data_unavailable
momentum:intraday_confirmation_stale momentum:confirmation_expired_before_publication
reversal_ad:ad_confirms_selloff_falling_knife
""".split())
CACHE_MAX_BYTES = 8 * 1024 * 1024
# Mirrored protocol keys only. The root-invoked collector never imports app code.
CONFLUENCE_COUNTS = frozenset("""
evaluated schema_invalid below_required incomplete pre_hard_gate_qualified
core_valid_count payload_accepted_count observation_errors
""".split())
CONFLUENCE_FACTORS = frozenset("""
atr_squeeze volume_dry_up obv_flow close_position range_duration boundary_tests
adx_turning institutional_flow rsi_drift range_structure directional_persistence
range_compression macd_histogram stochastic_momentum order_block_confluence
fvg_proximity liquidity_pool_proximity fibonacci_confluence volume_void
candle_body_compression
""".split())
CONFLUENCE_FACTOR_COUNTS = frozenset({"evaluated", "green", "red", "unavailable"})
CONFLUENCE_HARD_GATES = frozenset({
    "last_bar_pump", "range_breakdown", "recent_bearish_pressure",
    "recent_bullish_pressure", "unknown",
})
CONFLUENCE_CONTRACT = "stock-bi-20-v2"
BI_CACHE_SCANNERS = {
    "bi_cache_long.json": "bi_long", "bi_cache_long.json.partial": "bi_long",
    "bi_cache_short.json": "bi_short", "bi_cache_short.json.partial": "bi_short",
    "bi_scan_progress_long.json": "bi_long", "bi_scan_progress_short.json": "bi_short",
}


def _iso_timestamp(value):
    """Canonical ISO time only; legacy naive server times stay explicitly naive."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Timestamp is not a bounded ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Timestamp is not ISO formatted") from None
    return parsed.isoformat()


def _nonnegative_count(value):
    return type(value) is int and 0 <= value <= 2**63 - 1


def _count_projection(value, allowed):
    if not isinstance(value, dict):
        return None
    result = {key: count for key, count in value.items()
              if key in allowed and _nonnegative_count(count)}
    omitted = len(value) - len(result)
    if omitted:
        # Keep evidence of unsupported schema without leaking free-form keys.
        result["_omitted_categories"] = omitted
    return result


def _confluence_identity(value, expected_scanner):
    """Strict identifiers from the scanner call, not arbitrary source strings."""
    scanner = value.get("scanner")
    if (type(scanner) is not str or scanner not in ("bi_long", "bi_short")
            or (expected_scanner is not None and scanner != expected_scanner)
            or value.get("direction") != scanner.removeprefix("bi_")):
        return None
    run_id, revision = value.get("run_id"), value.get("code_revision")
    if type(run_id) is not str or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        return None
    if (type(revision) is not str
            or re.fullmatch(r"(?:[0-9a-f]{12}(?:-dirty|-tree-unknown)?|unknown)", revision) is None
            or value.get("contract_version") != CONFLUENCE_CONTRACT):
        return None
    try:
        started = datetime.fromisoformat(_iso_timestamp(value.get("started_at")))
        if started.tzinfo is None or started.utcoffset() is None:
            return None
        started_at = started.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
    return {"scanner": scanner, "direction": scanner.removeprefix("bi_"),
            "run_id": run_id, "code_revision": revision,
            "contract_version": CONFLUENCE_CONTRACT, "started_at": started_at}


def _confluence_projection(value, expected_scanner=None):
    """Schema-1 numeric protocol only; absent/unknown fields never become zero."""
    if not isinstance(value, dict):
        return {"available": False, "schema_status": "invalid"}
    if (value.get("available") is False and value.get("reason") == "initialization_failed"
            and type(value.get("initialization_errors")) is int
            and value["initialization_errors"] == 1):
        return {"available": False, "reason": "initialization_failed", "initialization_errors": 1}
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return {"available": False, "schema_status": "unknown"}
    if (type(value.get("required_green")) is not int or value["required_green"] != 17
            or any(not _nonnegative_count(value.get(key)) for key in CONFLUENCE_COUNTS)):
        return {"available": False, "schema_status": "invalid"}
    identity = _confluence_identity(value, expected_scanner)
    if identity is None:
        return {"available": False, "schema_status": "invalid_identity"}
    containers = {
        "green_count_histogram": frozenset(str(n) for n in range(21)),
        "available_count_histogram": frozenset(str(n) for n in range(21)),
        "bar_count_histogram": frozenset(str(n) for n in range(36, 51)) | {"other"},
        "first_hard_gate_counts": CONFLUENCE_HARD_GATES,
    }
    for key, allowed in containers.items():
        counts = value.get(key)
        if not isinstance(counts, dict) or any(not _nonnegative_count(counts.get(k)) for k in allowed):
            return {"available": False, "schema_status": "invalid"}
    factors = value.get("factor_counts")
    if not isinstance(factors, dict):
        return {"available": False, "schema_status": "invalid"}
    for key in CONFLUENCE_FACTORS:
        counts = factors.get(key)
        if (not isinstance(counts, dict)
                or any(not _nonnegative_count(counts.get(k)) for k in CONFLUENCE_FACTOR_COUNTS)):
            return {"available": False, "schema_status": "invalid"}
    result = {"available": True, "schema_version": 1, "required_green": 17}
    result.update(identity)
    result.update({key: value[key] for key in sorted(CONFLUENCE_COUNTS)})
    result.update({key: _count_projection(value[key], allowed) for key, allowed in containers.items()})
    result["factor_counts"] = {
        key: _count_projection(factors[key], CONFLUENCE_FACTOR_COUNTS)
        for key in sorted(CONFLUENCE_FACTORS)
    }
    if len(factors) > len(CONFLUENCE_FACTORS):
        result["omitted_factor_categories"] = len(factors) - len(CONFLUENCE_FACTORS)
    return result


def tracker_snapshot(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Tracker is not a regular file")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(signals)")}
        if not REQUIRED_COLUMNS <= columns:
            raise ValueError("Missing required tracker schema")
        inventory = dict(connection.execute(
            "SELECT COUNT(*) AS all_rows, "
            "COUNT(CASE WHEN mail_class='trade' THEN 1 END) AS trade_rows, "
            "COUNT(CASE WHEN mail_class='shadow' THEN 1 END) AS shadow_rows, "
            "COUNT(CASE WHEN mail_class IS NULL OR mail_class NOT IN ('trade','shadow') "
            "THEN 1 END) AS other_rows FROM signals"
        ).fetchone())
        if inventory["trade_rows"] > 100000:
            raise ValueError("Tracker exceeds bounded export size; no truncated success")
        selected = [name for name in TRACKER_COLUMNS if name in columns]
        rows = [dict(row) for row in connection.execute(
            "SELECT " + ",".join('"' + name + '"' for name in selected)
            + " FROM signals WHERE mail_class='trade' ORDER BY created_at,id"
        )]
    inventory["missing_report_columns"] = sorted(set(TRACKER_COLUMNS) - columns)
    return {"inventory": inventory, "rows": rows}


def service_pid(unit):
    # Fixed system utility, no shell, no repository hooks/config/code execution.
    value = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--property=MainPID", "--value"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    pid = int(value)
    if pid <= 0:
        raise ValueError("Service inactive; writer path cannot be verified")
    return pid


def _proc_directory(pid):
    return Path("/proc") / str(pid)


def _namespace_path(runtime, service_path):
    """Keep /proc/PID/root intact: resolve() would escape a PrivateTmp view."""
    target = PurePosixPath(str(service_path))
    if not target.is_absolute() or ".." in target.parts:
        raise ValueError("Expected an absolute service path without parent traversal")
    return Path(runtime["process_root"]).joinpath(*target.parts[1:])


def runtime_identity(unit, app):
    pid = service_pid(unit)
    proc = _proc_directory(pid)
    cwd = (proc / "cwd").resolve(strict=True)
    if cwd != app:
        raise ValueError("Unexpected service working directory")
    ownership = {}
    for line in (proc / "status").read_text(encoding="ascii").splitlines():
        key, separator, values = line.partition(":")
        if separator and key in {"Uid", "Gid"}:
            ids = [int(value) for value in values.split()]
            if len(ids) != 4 or len(set(ids)) != 1:
                raise ValueError("Mixed process privilege identity")
            ownership[key] = ids[0]
    if set(ownership) != {"Uid", "Gid"}:
        raise ValueError("Missing process privilege identity")
    # Inspect only needed paths; never print or return raw environment bytes.
    environment = {}
    for item in (proc / "environ").read_bytes().split(b"\0"):
        key, separator, value = item.partition(b"=")
        name = key.decode("ascii", errors="ignore")
        if separator and name in PATH_ENV:
            environment[name] = value.decode("utf-8", errors="strict")
    data = Path(environment.get("ALPHA_DATA_DIR", str(app / "data_cache")))
    if not data.is_absolute():
        data = cwd / data
    tracker = Path(environment.get("SIGNAL_TRACKER_DB_PATH", str(data / "signal_tracker.sqlite")))
    if not tracker.is_absolute():
        tracker = cwd / tracker
    temp = PurePosixPath(environment.get("ALPHA_RUNTIME_TMP_DIR") or "/tmp")
    if not temp.is_absolute():
        temp = PurePosixPath(cwd.as_posix()) / temp
    # This intentional kernel-provided symlink is retained, not resolved to the
    # host root. Cache paths below it use the verified service's mount namespace.
    process_root = proc / "root"
    identity = {"pid": pid, "uid": ownership["Uid"], "gid": ownership["Gid"],
            "cwd": str(cwd), "tracker": str(tracker.resolve(strict=True)),
            "process_root": str(process_root), "progress_dir": str(temp)}
    if not _namespace_path(identity, temp).is_dir():
        raise ValueError("Service progress directory is unavailable")
    return identity


def drop_reader_privileges(uid, gid, os_api=None):
    """Irreversible Linux UID/GID drop before SQLite can open WAL/SHM files."""
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise ValueError("Reader must be a non-root service account")
    if os_api is None:
        import os as os_api
    if os_api.geteuid() == 0:
        os_api.setgroups([])
        os_api.setresgid(gid, gid, gid)
        os_api.setresuid(uid, uid, uid)
        if os_api.getgroups():
            raise ValueError("Supplementary root groups survived drop")
    if os_api.getresuid() != (uid, uid, uid) or os_api.getresgid() != (gid, gid, gid):
        raise ValueError("Reader privilege identity mismatch")


def local_health():
    # Fixed loopback destination, no environment proxies or HTTP redirects.
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=10)
    try:
        connection.request("GET", "/api/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Health did not return HTTP200")
        data = response.read(16385)
        if len(data) > 16384:
            raise ValueError("Health response too large")
        payload = json.loads(data)
        if not isinstance(payload, dict) or payload.get("status") != "healthy":
            raise ValueError("API is not healthy")
        health = {"status": "healthy", "timestamp": _iso_timestamp(payload.get("timestamp"))}
        for key in ("revision", "frontend_bundle"):
            value = payload.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-f]{12}|unknown)", value):
                raise ValueError("Health identity is not a revision identifier")
            health[key] = value
        return health
    finally:
        connection.close()


def _read_cache_payload(path):
    """Bounded, read-only regular-file read; never wait on an exchanged FIFO."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > CACHE_MAX_BYTES:
        raise ValueError("Cache is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size > CACHE_MAX_BYTES
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError("Cache file changed during open")
        raw = stream.read(CACHE_MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError("Cache file changed during read")
    if len(raw) > CACHE_MAX_BYTES:
        raise ValueError("Cache exceeds bounded size")
    return json.loads(raw.decode("utf-8"))


def safe_cache_summary(path):
    """Counts/numeric funnel only, not cached tickers or free-form messages."""
    try:
        payload = _read_cache_payload(path)
        if not isinstance(payload, dict):
            return {"available": False}
        result = {"available": True}
        for key in ("checked", "total", "hits", "no_data", "count"):
            value = payload.get(key)
            if _nonnegative_count(value):
                result[key] = value
        if type(payload.get("partial")) is bool:
            result["partial"] = payload["partial"]
        stamp = payload.get("timestamp")
        if type(stamp) in (int, float) and 0 <= stamp <= 253402300799 and math.isfinite(stamp):
            result["timestamp"] = stamp
        if "cached_at" in payload:
            try:
                result["cached_at"] = _iso_timestamp(payload["cached_at"])
            except (ValueError, TypeError):
                result["cached_at"] = None
        status = payload.get("status")
        if isinstance(status, str) and status in CACHE_STATUSES:
            result["status"] = status
        elif "status" in payload:
            result["status"] = "unknown"
        detail = payload.get("detail")
        if status == "error" and type(detail) is str and detail in PUBLIC_SCAN_ERROR_CODES:
            result["error_code"] = detail
        rows = payload.get("results")
        result["raw_rows"] = len(rows) if isinstance(rows, list) else None
        diagnostics = payload.get("diagnostics") or {}
        if isinstance(diagnostics, dict):
            result["numeric_diagnostics"] = {
                key: value for key, value in diagnostics.items()
                if key in DIAGNOSTIC_COUNTS and _nonnegative_count(value)
            }
            if diagnostics.get("coverage") in ("complete", "incomplete"):
                result["coverage"] = diagnostics["coverage"]
            if type(diagnostics.get("scan_in_progress")) is bool:
                result["scan_in_progress"] = diagnostics["scan_in_progress"]
            reason = diagnostics.get("data_error_reason")
            if type(reason) is str and reason in DATA_ERROR_REASONS:
                result["data_error_reason"] = reason
            for key, allowed in (("rejected", REJECTION_CODES), ("stage_counts", STAGE_COUNTS),
                                 ("data_failures", DATA_FAILURE_CODES), ("legitimate_filters", REJECTION_CODES)):
                counts = _count_projection(diagnostics.get(key), allowed)
                if counts is not None:
                    result[key] = counts
            if "confluence" in diagnostics:
                result["confluence"] = _confluence_projection(
                    diagnostics["confluence"], BI_CACHE_SCANNERS.get(Path(path).name))
        return result
    except (OSError, ValueError, TypeError):
        return {"available": False}


def collect(app):
    app = Path(app).resolve(strict=True)
    units = ("tradingbot-api.service", "tradingbot-bg.service")
    runtimes = {unit: runtime_identity(unit, app) for unit in units}
    if len({item["tracker"] for item in runtimes.values()}) != 1:
        raise ValueError("API/BG use different trackers; no misleading combined export")
    import pwd
    service_account = pwd.getpwnam("tradingbot")
    if any((runtime["uid"], runtime["gid"]) != (service_account.pw_uid, service_account.pw_gid)
           for runtime in runtimes.values()):
        raise ValueError("Writers do not use the expected service identity")
    # SQLite readers may need WAL shared-memory sidecars. They must never be
    # created as root in the service-owned tree; immutable=1 is NOT safe for WAL.
    drop_reader_privileges(service_account.pw_uid, service_account.pw_gid)
    health = local_health()
    tracker = tracker_snapshot(runtimes[units[0]]["tracker"])
    api_runtime = runtimes[units[0]]
    caches = {name: safe_cache_summary(_namespace_path(api_runtime, path)) for name, path in {
        "bi_long": "/tmp/bi_cache_long.json", "bi_short": "/tmp/bi_cache_short.json",
        "momentum": "/tmp/strategy_momentum_breakout_long_cache.json",
        "bi_long_progress": PurePosixPath(api_runtime["progress_dir"]) / "bi_scan_progress_long.json",
        "bi_short_progress": PurePosixPath(api_runtime["progress_dir"]) / "bi_scan_progress_short.json",
    }.items()}
    if any(runtime_identity(unit, app) != runtimes[unit] for unit in units):
        raise ValueError("Writer restarted or paths changed during collection")
    return {"schema_version": 1, "kind": "private_server_evidence",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True, "runtime": runtimes, "health": health,
            "tracker": tracker, "scanner_caches": caches,
            "notes": ["Tracker rows are one SQLite read transaction including WAL.",
                      "SQLite/cache reading runs as verified non-root tradingbot UID/GID.",
                      "Cache/progress paths use the verified API process mount namespace.",
                      "Normal SQLite WAL/SHM coordination is possible; no root-owned sidecars.",
                      "Cache/health files are separately observed, not an atomic cross-file snapshot.",
                      "No recipients, account blobs, mail bodies or API keys selected.",
                      "No broker fills/cost ledger; price-path results are not net account PnL.",
                      "Keep this projected row export private; do not commit or publish it."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="/home/tradingbot/app")
    args = parser.parse_args()
    try:
        result = collect(args.app)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except Exception:
        print("Evidence export failed: verify active services, writer paths and readable DB; nothing changed.", file=sys.stderr)
        return 2
    print(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
