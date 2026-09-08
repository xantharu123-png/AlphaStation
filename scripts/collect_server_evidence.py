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
from pathlib import Path
import re
import sqlite3
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


def runtime_identity(unit, app):
    pid = service_pid(unit)
    proc = Path("/proc") / str(pid)
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
    temp = Path(environment.get("ALPHA_RUNTIME_TMP_DIR", "/tmp"))
    if not temp.is_absolute():
        temp = cwd / temp
    return {"pid": pid, "uid": ownership["Uid"], "gid": ownership["Gid"],
            "cwd": str(cwd), "tracker": str(tracker.resolve(strict=True)),
            "progress_dir": str(temp.resolve(strict=True))}


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


def safe_cache_summary(path):
    """Counts/numeric funnel only, not cached tickers or free-form messages."""
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            return {"available": False}
        payload = json.loads(path.read_text(encoding="utf-8"))
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
            for key, allowed in (("rejected", REJECTION_CODES), ("stage_counts", STAGE_COUNTS),
                                 ("data_failures", DATA_FAILURE_CODES), ("legitimate_filters", REJECTION_CODES)):
                counts = _count_projection(diagnostics.get(key), allowed)
                if counts is not None:
                    result[key] = counts
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
    caches = {name: safe_cache_summary(path) for name, path in {
        "bi_long": "/tmp/bi_cache_long.json", "bi_short": "/tmp/bi_cache_short.json",
        "momentum": "/tmp/strategy_momentum_breakout_long_cache.json",
        "bi_long_progress": Path(runtimes[units[0]]["progress_dir"]) / "bi_scan_progress_long.json",
        "bi_short_progress": Path(runtimes[units[0]]["progress_dir"]) / "bi_scan_progress_short.json",
    }.items()}
    if any(runtime_identity(unit, app) != runtimes[unit] for unit in units):
        raise ValueError("Writer restarted or paths changed during collection")
    return {"schema_version": 1, "kind": "private_server_evidence",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True, "runtime": runtimes, "health": health,
            "tracker": tracker, "scanner_caches": caches,
            "notes": ["Tracker rows are one SQLite read transaction including WAL.",
                      "SQLite/cache reading runs as verified non-root tradingbot UID/GID.",
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
