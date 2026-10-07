#!/usr/bin/env python3
"""Read only the stored LSPD Momentum plan in the running API's /tmp namespace.

Stream this reviewed ASCII source to /usr/bin/python3 -I - as the operator.
No app imports, environment reads, providers, sockets, credentials or writes.
Stored scanner evidence is NOT a fresh trade approval or proof of mail delivery.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import date, datetime, timezone


API_UNIT = "tradingbot-api.service"
CACHE_NAME = "strategy_momentum_breakout_long_cache.json"
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 150
MAX_ZONES = 256
MAX_EVIDENCE = 256
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
SEMANTICS = "stored_lspd_plan_and_zones_not_fresh_approval_or_mail_delivery"
TIMEFRAMES = frozenset({"1D", "1W", "4H", "1H", "15M", "5M", "15m", "5m"})
DIRECTIONS = frozenset({"LONG", "SHORT"})
ROLES = frozenset({"support", "resistance", "overlap"})
SOURCE_NAMES = frozenset({"PDH", "PDL", "PDC", "PWH", "PWL", "PWC",
                          "4H_HIGH", "4H_LOW", "4H_CLOSE", "confirmed_swing_high",
                          "confirmed_swing_low", "VAH", "VAL", "POC", "VWAP"})
SOURCE_FAMILIES = frozenset({"horizontal_swing", "session", "volume_profile", "fibonacci",
                             "vwap", "level_zone"})
MODELS = frozenset({"causal_level_zones_v2", "directional_level_zones_v2",
                    "structure_decision_v1", "connected_role_boundary_v2",
                    "break_confirmed_optional_retest_v1", "break_reclaim_close_hold_v1",
                    "break_reclaim_close_hold_v2", "causal_level_zones_v2+invalidation_first_v2"})
PLAN_REASONS = frozenset({"invalid_entry_or_direction", "causal_structure_missing",
                         "causal_structure_unavailable", "crossed_resistance_unconfirmed",
                         "crossed_support_unconfirmed", "no_structural_invalidation",
                         "invalid_stop_risk", "invalid_trade_geometry", "native_structure_plan",
                         "first_opposing_barrier_before_minimum_rr", "direction_missing",
                         "plan_unavailable", "entry_overlaps_opposing_barrier",
                         "no_confirmed_opposing_barrier", "first_opposing_barrier_is_tradable_tp1",
                         "invalid_stop_geometry", "structure_unavailable"})
BREAK_REASONS = frozenset({"completed_hold_bars_missing", "completed_retest_missing",
                          "completed_break_close_confirmed_retest_optional",
                          "completed_break_evidence_stale", "conflicting_completed_bars",
                          "no_active_completed_break_close", "latest_zone_membership_not_covered",
                          "completed_break_close_and_hold_confirmed"})
QUALITY_FLAGS = frozenset({"session_levels_precede_signal_session", "no_completed_bars",
                          "no_confirmed_levels", "crossed_level_reclaim_pending",
                          "breakout_confirmed_without_retest", "conflicting_completed_bars",
                          "projection_only", "mixed_source_roles"})
STRUCTURE_STATES = frozenset({"ACCEPT", "WAIT_BREAK_RECLAIM", "REJECT", "STRUCTURE_UNAVAILABLE"})
BREAK_STATES = frozenset({"intact", "unbroken", "broken", "break_confirmed", "reclaimed",
                         "failed_break", "INTACT", "RECLAIM_PENDING", "BREAK_CONFIRMED",
                         "RECLAIMED", "UNCONFIRMED", "FAILED"})
GATES = frozenset({"BREAK_RECLAIM_REQUIRED", "BREAK_SUPPORT_REQUIRED"})
ZONE_ID = re.compile(r"lz_[0-9a-f]{16}\Z")


class EvidenceError(ValueError):
    """Only fixed public reason codes may cross the CLI failure boundary."""


def _mapping(value):
    if value is None:
        return {}
    if type(value) is not dict:
        raise EvidenceError("lspd_shape_invalid")
    return value


def _objects(value, limit):
    if value is None:
        return []
    if type(value) is not list or len(value) > limit or any(type(item) is not dict for item in value):
        raise EvidenceError("lspd_shape_invalid")
    return value


def _numbers(source, fields):
    return {key: source[key] for key in fields if type(source.get(key)) in (int, float)
            and abs(source[key]) <= 1e15 and math.isfinite(source[key])}


def _booleans(source, fields):
    return {key: source[key] for key in fields if type(source.get(key)) is bool}


def _enums(source, fields):
    return {key: source[key] for key, allowed in fields.items()
            if type(source.get(key)) is str and source[key] in allowed}


def _timestamp(value):
    if type(value) is not str or not 19 <= len(value) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        pass
    return None


def _timestamps(source, fields):
    return {key: parsed for key in fields if (parsed := _timestamp(source.get(key))) is not None}


def _timeframe(value):
    if type(value) is str and len(value) <= 40 and 1 <= len(value.split("/")) <= 8:
        if all(part in TIMEFRAMES for part in value.split("/")):
            return value
    return None


def _zone_ids(source, fields):
    return {key: source[key] for key in fields
            if type(source.get(key)) is str and ZONE_ID.fullmatch(source[key])}


def _enum_list(value, allowed):
    if value is None:
        return []
    if type(value) is not list or len(value) > 256:
        raise EvidenceError("lspd_shape_invalid")
    return [item for item in value if type(item) is str and item in allowed]


def _independence_key(value):
    # Native keys are protocol identities, never arbitrary provenance text.
    if type(value) is not str or len(value) > 160:
        return None
    parts = value.split(":")
    if len(parts) == 2 and parts[0] in SOURCE_FAMILIES and _timeframe(parts[1]):
        return value
    if len(parts) == 3 and parts[0] == "level_zone" and _timeframe(parts[1]) and ZONE_ID.fullmatch(parts[2]):
        return value
    if value.startswith("session:"):
        timeframe, separator, stamp = value[len("session:"):].partition(":")
        normalized = _timestamp(stamp)
        if separator and timeframe in TIMEFRAMES and normalized:
            return "session:" + timeframe + ":" + normalized
    return None


def _provenance(value):
    source = _mapping(value)
    result = {**_numbers(source, ("pivot_index", "confirmation_bar_index", "pivot_left",
                                  "pivot_right", "touch_count")),
              **_timestamps(source, ("session_opened_at", "session_closed_at")),
              **_enums(source, {"role_hint": ROLES,
                                "legacy_kind": {"SUPPORT", "RESISTANCE", "OVERLAP"},
                                "legacy_source": SOURCE_NAMES})}
    key = _independence_key(source.get("independence_key"))
    if key:
        result["independence_key"] = key
    return result


def _reclaim_history(value):
    source = _mapping(value)
    result = {**_numbers(source, ("lower", "upper")), **_zone_ids(source, ("zone_id",)),
              **_timestamps(source, ("membership_confirmed_at",)),
              **_enums(source, {"model": {"connected_role_boundary_v2"}})}
    directions = _mapping(source.get("confirmed_at_by_direction"))
    result["confirmed_at_by_direction"] = {
        side: stamp for side in sorted(DIRECTIONS)
        if (stamp := _timestamp(directions.get(side))) is not None}
    return result


def _break_reclaim(value):
    source = _mapping(value)
    result = {**_numbers(source, ("boundary", "last_completed_close", "hold_bars_required",
                                  "hold_bars_observed", "completed_bars_used")),
              **_booleans(source, ("retest_required", "retest_observed")),
              **_timestamps(source, ("zone_confirmed_at", "as_of", "break_closed_at", "last_completed_at")),
              **_zone_ids(source, ("zone_id",)),
              **_enums(source, {"model": MODELS, "state": BREAK_STATES,
                                "reason": BREAK_REASONS, "direction": DIRECTIONS,
                                "timeframe": TIMEFRAMES})}
    if source.get("reclaim_history") is not None:
        result["reclaim_history"] = _reclaim_history(source["reclaim_history"])
    return result


def _evidence(value):
    result = {**_numbers(value, ("lower", "upper", "strength")),
              **_booleans(value, ("projection_only",)),
              **_timestamps(value, ("observed_at", "confirmed_at", "data_cutoff_at")),
              **_enums(value, {"source_family": SOURCE_FAMILIES,
                               "source_name": SOURCE_NAMES, "timeframe": TIMEFRAMES})}
    key = _independence_key(value.get("independence_key"))
    if key:
        result["independence_key"] = key
    result["provenance"] = _provenance(value.get("provenance"))
    return result


def _zone(value):
    result = {**_numbers(value, ("lower", "upper", "reference", "strength", "touch_count",
                                 "independent_sources", "independent_structural_sources")),
              **_booleans(value, ("projection_only",)), **_zone_ids(value, ("zone_id",)),
              **_timestamps(value, ("confirmed_at",)),
              **_enums(value, {"side_at_reference": ROLES, "break_state": BREAK_STATES})}
    result["sources"] = _enum_list(value.get("sources"), SOURCE_NAMES)
    result["origin_roles"] = _enum_list(value.get("origin_roles"), ROLES)
    result["quality_flags"] = _enum_list(value.get("quality_flags"), QUALITY_FLAGS)
    result["evidence"] = [_evidence(item) for item in _objects(value.get("evidence"), MAX_EVIDENCE)]
    if value.get("break_reclaim_evidence") is not None:
        result["break_reclaim_evidence"] = _break_reclaim(value["break_reclaim_evidence"])
    if value.get("reclaim_history") is not None:
        result["reclaim_history"] = _reclaim_history(value["reclaim_history"])
    return result


def _structure(value):
    source = _mapping(value)
    result = {**_numbers(source, ("current_price",)), **_timestamps(source, ("as_of",)),
              **_enums(source, {"model": MODELS, "asset_class": {"stock"}, "horizon": {"swing"}})}
    if source.get("symbol") not in (None, "LSPD"):
        raise EvidenceError("structure_symbol_conflict")
    if source.get("symbol") == "LSPD":
        result["symbol"] = "LSPD"
    result["zones"] = [_zone(item) for item in _objects(source.get("zones"), MAX_ZONES)]
    for field in ("atr_by_timeframe", "completed_bar_counts"):
        result[field] = _numbers(_mapping(source.get(field)), sorted(TIMEFRAMES))
    result["quality_flags"] = _enum_list(source.get("quality_flags"), QUALITY_FLAGS)
    return result


def _barrier(value):
    source = _mapping(value)
    result = {**_numbers(source, ("price", "zone_low", "zone_high", "distance_r", "distance_atr",
                                  "strength", "independent_sources", "reclaim_boundary")),
              **_booleans(source, ("overlapping", "reclaimed", "structural", "causal_structure_validated")),
              **_timestamps(source, ("confirmed_at", "data_cutoff_at")),
              **_zone_ids(source, ("zone_id",)),
              **_enums(source, {"side": ROLES, "action": GATES, "source_family": SOURCE_FAMILIES})}
    if (timeframe := _timeframe(source.get("timeframe"))) is not None:
        result["timeframe"] = timeframe
    if source.get("reclaim_history") is not None:
        result["reclaim_history"] = _reclaim_history(source["reclaim_history"])
    return result


def _trade_setup(value):
    source = _mapping(value)
    prefixes = ("stop", "tp1", "tp2")
    result = {**_numbers(source, ("entry", "stop", "tp1", "tp2", "risk", "rr", "rr_tp1", "rr_tp2",
                                  "atr", "room_to_barrier_r", "vrvp_poc", "vrvp_vah", "vrvp_val")
                         + tuple(prefix + suffix for prefix in prefixes for suffix in ("_zone_low", "_zone_high"))),
              **_booleans(source, ("stop_is_projection", "tp1_is_projection", "tp2_is_projection",
                                    "barrier_gate_active", "vrvp_applied")
                          + tuple(prefix + "_causal_structure_validated" for prefix in prefixes)),
              **_zone_ids(source, tuple(prefix + "_zone_id" for prefix in prefixes)),
              **_timestamps(source, tuple(prefix + suffix for prefix in prefixes
                                            for suffix in ("_confirmed_at", "_data_cutoff_at"))),
              **_enums(source, {"direction": DIRECTIONS, "structure_status": STRUCTURE_STATES,
                                "structure_reason": PLAN_REASONS, "barrier_gate": GATES,
                                "level_model": MODELS,
                                "target_quality": {"PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
                                                   "STRUCTURAL_TP1_PROJECTION_TP2", "STRUCTURAL_FIRST_BARRIER"},
                                "breakout_confirmation": {"confirmed_close"},
                                "retest_status": {"confirmed", "not_confirmed"}})}
    for prefix in prefixes:
        if (timeframe := _timeframe(source.get(prefix + "_timeframe"))) is not None:
            result[prefix + "_timeframe"] = timeframe
        if (key := _independence_key(source.get(prefix + "_independence_key"))) is not None:
            result[prefix + "_independence_key"] = key
    for field in ("nearest_barrier", "overhead_resistance", "underlying_support"):
        if source.get(field) is not None:
            result[field] = _barrier(source[field])
    if source.get("structure_decision") is not None:
        decision = _mapping(source["structure_decision"])
        result["structure_decision"] = {
            **_numbers(decision, ("entry", "stop", "risk", "barrier_distance", "barrier_r", "target1")),
            **_enums(decision, {"model": MODELS, "status": STRUCTURE_STATES,
                                "reason": PLAN_REASONS, "direction": DIRECTIONS, "barrier_gate": GATES})}
        if decision.get("nearest_barrier") is not None:
            result["structure_decision"]["nearest_barrier"] = _zone(_mapping(decision["nearest_barrier"]))
    return result


def _swing_reference(row):
    result = {**_numbers(row, ("stock_swing_contract_version", "swing_reference_close", "swing_data_delay_seconds")),
              **_timestamps(row, ("scan_price_observed_at", "price_observed_at")),
              **_booleans(row, ("fill_evidence_verified",)),
              **_enums(row, {"stock_swing_mode": {"completed_daily_swing"}, "swing_timeframe": {"1D"},
                             "trade_horizon": {"swing"}, "scan_price_source": {"polygon_completed_1d_swing"},
                             "price_source": {"polygon_completed_1d_swing"}, "price_mode": {"swing_reference_close"},
                             "price_session": {"COMPLETED_US_SESSION"}})}
    session = row.get("swing_analysis_session")
    if type(session) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", session):
        try:
            result["swing_analysis_session"] = date.fromisoformat(session).isoformat()
        except ValueError:
            pass
    return result


def project_payload(payload, snapshot):
    if type(payload) is not dict or payload.get("partial") is True:
        raise EvidenceError("invalid_cache_shape")
    rows = payload.get("results")
    if type(rows) is not list or len(rows) > MAX_ROWS or any(type(row) is not dict for row in rows):
        raise EvidenceError("invalid_cache_shape")
    matches = [(index, row) for index, row in enumerate(rows)
               if row.get("ticker") == "LSPD" or row.get("Ticker") == "LSPD"]
    if not matches:
        raise EvidenceError("lspd_not_found")
    if len(matches) != 1:
        raise EvidenceError("lspd_not_unique")
    index, row = matches[0]
    if any(row.get(key) not in (None, "", "LSPD") for key in ("ticker", "Ticker")):
        raise EvidenceError("ticker_alias_conflict")
    diagnostic = _mapping(row.get("native_plan_diagnostics"))
    structure = row.get("level_structure", row.get("Level_Structure"))
    if row.get("level_structure") is not None and row.get("Level_Structure") is not None:
        if row["level_structure"] != row["Level_Structure"]:
            raise EvidenceError("structure_alias_conflict")
    cache = {**snapshot, "rows_total": len(rows), **_numbers(payload, ("cache_version", "timestamp")),
             **_booleans(payload, ("partial",))}
    if type(payload.get("timestamp")) in (int, float):
        try:
            cache["timestamp_utc"] = datetime.fromtimestamp(payload["timestamp"], timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    elif (stamp := _timestamp(payload.get("timestamp"))) is not None:
        cache["timestamp_utc"] = stamp
    return {"schema_version": 1, "kind": "lspd_plan_evidence", "read_only": True,
            "semantics": SEMANTICS, "status": "ok", "cache": cache,
            "lspd": {"ticker": "LSPD", "row_index": index,
                     "scanner_numbers": _numbers(row, ("score", "Score", "price", "Price", "Preis", "History_Bars",
                                                         "High_20D", "Low_20D", "Support_1", "Resistance_1", "ATR14")),
                     "swing_reference": _swing_reference(row),
                     "native_plan_diagnostics": _enums(diagnostic, {"status": {"built", "unavailable"}, "reason": PLAN_REASONS}),
                     **_enums(row, {"native_plan_status": {"built", "unavailable"}, "native_plan_reason": PLAN_REASONS}),
                     "trade_setup": _trade_setup(row.get("trade_setup")),
                     "level_structure": _structure(structure)},
            "raw_daily_prefix": {"status": "not_stored_in_final_result_cache",
                                 "availability_elsewhere": "unknown_not_inspected",
                                 "provider_fetch_performed": False}}


def _identity(info):
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    # Linux ctime is change time. Windows 3.12 lstat/fstat disagree about ctime
    # (creation vs change time), so keep it only on the deployed POSIX reader.
    return identity + ((info.st_ctime_ns,) if os.name == "posix" else ())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("invalid_json")
        result[key] = value
    return result


def read_cache(path):
    """Bounded coherent regular-file read, including the post-hash pathname."""
    path = Path(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("not_regular")
        if before.st_size > MAX_BYTES:
            raise EvidenceError("too_large")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
                raise EvidenceError("changed_during_read")
            raw = stream.read(MAX_BYTES + 1)
            digest = hashlib.sha256(raw).hexdigest()
            after = os.fstat(stream.fileno())
            named_after = path.lstat()
            if _identity(opened) != _identity(after) or _identity(opened) != _identity(named_after):
                raise EvidenceError("changed_during_read")
            if not stat.S_ISREG(named_after.st_mode) or len(raw) != opened.st_size:
                raise EvidenceError("changed_during_read")
        if len(raw) > MAX_BYTES:
            raise EvidenceError("too_large")
    except OSError:
        raise EvidenceError("cache_unavailable") from None
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise EvidenceError("invalid_json") from None
    return payload, {"name": CACHE_NAME, "size_bytes": len(raw), "mtime_ns": after.st_mtime_ns,
                     "sha256": digest}


def service_pid():
    try:
        result = subprocess.run(["/usr/bin/systemctl", "show", API_UNIT, "--property=MainPID", "--value"],
                                check=True, capture_output=True, text=True, timeout=10)
        value = result.stdout.strip()
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
            raise EvidenceError("api_inactive")
        return int(value)
    except (OSError, subprocess.SubprocessError):
        raise EvidenceError("service_identity_unavailable") from None


def process_root(pid):
    # Do not resolve(): /proc/PID/root is the intentional kernel namespace link.
    return Path("/proc") / str(pid) / "root"


def process_start_ticks(pid):
    try:
        with open(Path("/proc") / str(pid) / "stat", "r", encoding="ascii") as stream:
            text = stream.read(4097)
        tail = text[text.rindex(")") + 2:].split()
        value = int(tail[19])  # field 22; tail begins with field 3.
        if len(text) > 4096 or value <= 0:
            raise ValueError
        return value
    except (OSError, ValueError, IndexError):
        raise EvidenceError("service_identity_unavailable") from None


def collect():
    pid = service_pid()
    started = process_start_ticks(pid)
    cache_path = process_root(pid) / "tmp" / CACHE_NAME
    result = project_payload(*read_cache(cache_path))
    if service_pid() != pid or process_start_ticks(pid) != started:
        raise EvidenceError("service_changed")
    result["api"] = {"unit": API_UNIT, "pid": pid, "start_ticks": started}
    return result


def main():
    try:
        if len(sys.argv) != 1:
            raise EvidenceError("unsupported_arguments")
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise EvidenceError("unsupported_platform")
        result = collect()
        output = json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True)
        if len(output.encode("ascii")) > MAX_OUTPUT_BYTES:
            raise EvidenceError("output_too_large")
    except Exception as error:
        # Never print application data, paths, subprocess stderr or exception text.
        reason = str(error) if isinstance(error, EvidenceError) else "reader_failed"
        output = json.dumps({"schema_version": 1, "kind": "lspd_plan_evidence", "read_only": True,
                             "semantics": SEMANTICS, "status": "unavailable", "reason": reason})
        print(output)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
