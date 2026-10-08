#!/usr/bin/env python3
"""Project only four stored Momentum candidates from the running API namespace.

Stream this reviewed ASCII source to /usr/bin/python3 -I - as the operator.
No application imports, environment reads, credentials, network or writes.
The final cache does not contain original OHLCV; this reader never fetches it.
Stored plans are not fresh trade approval, SMTP evidence or inbox delivery.
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
TICKERS = ("RELL", "UVE", "NECB", "GKOS")
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 150
MAX_ZONES = 256
MAX_EVIDENCE = 256
MAX_TOTAL_EVIDENCE = 4096
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_COUNT = 1_000_000
SEMANTICS = "stored_momentum_candidates_not_fresh_approval_or_mail_delivery"
TIMEFRAMES = frozenset({"1D", "1W", "4H", "1H", "15M", "5M", "15m", "5m"})
DIRECTIONS = frozenset({"LONG", "SHORT"})
ROLES = frozenset({"support", "resistance", "overlap"})
SOURCE_NAMES = frozenset({"PDH", "PDL", "PDC", "PWH", "PWL", "PWC",
                          "4H_HIGH", "4H_LOW", "4H_CLOSE", "confirmed_swing_high",
                          "confirmed_swing_low", "VAH", "VAL", "POC", "VWAP"})
SOURCE_FAMILIES = frozenset({"horizontal_swing", "session", "volume_profile", "fibonacci",
                             "vwap", "level_zone", "vrvp"})
MODELS = frozenset({"causal_level_zones_v2", "directional_level_zones_v2",
                    "structure_decision_v1", "connected_role_geometry_v1",
                    "connected_role_boundary_v2", "break_confirmed_optional_retest_v1",
                    "break_reclaim_close_hold_v1", "break_reclaim_close_hold_v2",
                    "causal_level_zones_v2+invalidation_first_v2"})
LEVEL_MODELS = MODELS | frozenset(model + "+vrvp" for model in MODELS)
PLAN_REASONS = frozenset({"invalid_entry_or_direction", "causal_structure_missing",
                         "causal_structure_unavailable", "crossed_resistance_unconfirmed",
                         "crossed_support_unconfirmed", "no_structural_invalidation",
                         "invalid_stop_risk", "invalid_trade_geometry", "native_structure_plan",
                         "first_opposing_barrier_before_minimum_rr", "direction_missing",
                         "plan_unavailable", "entry_overlaps_opposing_barrier",
                         "no_confirmed_opposing_barrier", "first_opposing_barrier_is_tradable_tp1",
                         "invalid_stop_geometry", "structure_unavailable",
                         "first_opposing_barrier_before_minimum_reward",
                         "tp1_marked_structural_without_causal_identity", "rounded_trade_geometry_invalid"})
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
ZONE_ID = re.compile(r"(?:lz_[0-9a-f]{16}|vrvp-zone-[0-9a-f]{20})\Z")
PROFILE_ID = re.compile(r"vrvp-profile-[0-9a-f]{20}\Z")
FIXED_SOURCES = frozenset({"VRVP POC", "VRVP VAH", "VRVP VAL", "VRVP HVN mid", "VRVP HVN low",
                           "VRVP HVN high", "risk fallback after VRVP validation",
                           "projection fallback (no second independent structural barrier)"})
PUBLIC_REASONS = frozenset({"candidate_shape_invalid", "structure_symbol_conflict",
                            "invalid_cache_shape", "not_regular", "too_large",
                            "changed_during_read", "cache_unavailable", "invalid_json",
                            "api_inactive", "service_identity_unavailable", "service_changed",
                            "unsupported_arguments", "unsupported_platform", "output_too_large",
                            "reader_failed"})
NUMBER_FIELDS = ("score", "Score", "base_score", "Setup_Score", "price", "Price", "Preis",
                 "current_price", "MedianDollarVol20", "median_dollar_volume_20d",
                 "median_dollar_vol20", "Day_High", "day_high", "DayHigh", "High", "high",
                 "TP1", "tp1", "TakeProfit1", "target1", "Entry", "entry", "StopLoss",
                 "stop_loss", "TP2", "tp2", "ATR14", "ATR_Pct", "High_20D", "Low_20D",
                 "Support_1", "Resistance_1", "RVOL", "rvol", "Day_Open", "Day_Low",
                 "Breakout_Continuation_Score", "breakout_continuation_score")
ALIAS_GROUPS = {
    "ticker": ("ticker", "Ticker"),
    "score": ("score", "Score"),
    "price": ("price", "Price", "Preis", "current_price"),
    "median_dollar_volume_20d": ("MedianDollarVol20", "median_dollar_volume_20d", "median_dollar_vol20"),
    "history_ok": ("History_OK", "history_ok"),
    "history_bars": ("History_Bars", "history_bars"),
    "day_high": ("Day_High", "day_high", "DayHigh", "High", "high"),
    "tp1": ("TP1", "tp1", "TakeProfit1", "target1"),
    "level_structure": ("level_structure", "Level_Structure"),
    "level_model": ("level_model", "Level_Model"),
    "quality_score": ("Breakout_Continuation_Score", "breakout_continuation_score"),
}


class EvidenceError(ValueError):
    """Only fixed public reason codes may cross an output boundary."""


def _reason(error):
    return str(error) if isinstance(error, EvidenceError) and str(error) in PUBLIC_REASONS else "reader_failed"


def _mapping(value):
    if value is None:
        return {}
    if type(value) is not dict:
        raise EvidenceError("candidate_shape_invalid")
    return value


def _objects(value, limit):
    if value is None:
        return []
    if type(value) is not list or len(value) > limit or any(type(item) is not dict for item in value):
        raise EvidenceError("candidate_shape_invalid")
    return value


def _numbers(source, fields):
    # Preserve raw numeric values and aliases; bools/NaN/Inf are not observations.
    return {key: source[key] for key in fields if type(source.get(key)) in (int, float)
            and abs(source[key]) <= 1e15 and math.isfinite(source[key])}


def _counts(source, fields):
    return {key: source[key] for key in fields if type(source.get(key)) is int
            and 0 <= source[key] <= MAX_COUNT}


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
            if type(source.get(key)) is str and len(source[key]) <= 30 and ZONE_ID.fullmatch(source[key])}


def _profile_ids(source, fields):
    return {key: source[key] for key in fields
            if type(source.get(key)) is str and len(source[key]) == 33 and PROFILE_ID.fullmatch(source[key])}


def _enum_list(value, allowed):
    if value is None:
        return []
    if type(value) is not list or len(value) > 256:
        raise EvidenceError("candidate_shape_invalid")
    return [item for item in value if type(item) is str and item in allowed]


def _independence_key(value):
    if type(value) is not str or len(value) > 160:
        return None
    if PROFILE_ID.fullmatch(value):
        return value
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


def _source_label(value):
    # Permit only native source-name/timeframe grammar, never arbitrary notes.
    if type(value) is not str or len(value) > 320:
        return None
    if value in FIXED_SOURCES or (value.endswith(" invalidation") and value[:-13] in FIXED_SOURCES):
        return value
    match = re.fullmatch(r"([A-Za-z0-9_]+(?: \+ [A-Za-z0-9_]+)*) \(([^()]+)\)( invalidation)?", value)
    if match:
        names = match[1].split(" + ")
        if len(names) <= 16 and all(name in SOURCE_NAMES for name in names) and _timeframe(match[2]):
            return value
    return None


def _provenance(value):
    source = _mapping(value)
    result = {**_counts(source, ("pivot_index", "confirmation_bar_index", "pivot_left", "pivot_right", "touch_count")),
              **_timestamps(source, ("session_opened_at", "session_closed_at")),
              **_enums(source, {"role_hint": ROLES, "legacy_kind": {"SUPPORT", "RESISTANCE", "OVERLAP"},
                                "legacy_source": SOURCE_NAMES})}
    if (key := _independence_key(source.get("independence_key"))) is not None:
        result["independence_key"] = key
    return result


def _reclaim_history(value):
    source = _mapping(value)
    result = {**_numbers(source, ("lower", "upper")), **_zone_ids(source, ("zone_id",)),
              **_timestamps(source, ("membership_confirmed_at",)),
              **_enums(source, {"model": {"connected_role_geometry_v1", "connected_role_boundary_v2"}})}
    directions = _mapping(source.get("confirmed_at_by_direction"))
    result["confirmed_at_by_direction"] = {
        side: stamp for side in sorted(DIRECTIONS) if (stamp := _timestamp(directions.get(side))) is not None}
    return result


def _break_reclaim(value):
    source = _mapping(value)
    result = {**_numbers(source, ("boundary", "last_completed_close")),
              **_counts(source, ("hold_bars_required", "hold_bars_observed", "completed_bars_used")),
              **_booleans(source, ("retest_required", "retest_observed")),
              **_timestamps(source, ("zone_confirmed_at", "as_of", "break_closed_at", "last_completed_at")),
              **_zone_ids(source, ("zone_id",)),
              **_enums(source, {"model": MODELS, "state": BREAK_STATES, "reason": BREAK_REASONS,
                                "direction": DIRECTIONS, "timeframe": TIMEFRAMES})}
    if source.get("reclaim_history") is not None:
        result["reclaim_history"] = _reclaim_history(source["reclaim_history"])
    return result


def _evidence(value):
    result = {**_numbers(value, ("lower", "upper", "strength")), **_booleans(value, ("projection_only",)),
              **_timestamps(value, ("observed_at", "confirmed_at", "data_cutoff_at")),
              **_enums(value, {"source_family": SOURCE_FAMILIES, "source_name": SOURCE_NAMES,
                               "timeframe": TIMEFRAMES})}
    if (key := _independence_key(value.get("independence_key"))) is not None:
        result["independence_key"] = key
    result["provenance"] = _provenance(value.get("provenance"))
    return result


def _zone(value):
    result = {**_numbers(value, ("lower", "upper", "reference", "strength")),
              **_counts(value, ("touch_count", "independent_sources", "independent_structural_sources")),
              **_booleans(value, ("projection_only",)), **_zone_ids(value, ("zone_id",)),
              **_timestamps(value, ("confirmed_at",)),
              **_enums(value, {"side_at_reference": ROLES, "break_state": BREAK_STATES})}
    for field, allowed in (("sources", SOURCE_NAMES), ("origin_roles", ROLES), ("quality_flags", QUALITY_FLAGS)):
        result[field] = _enum_list(value.get(field), allowed)
    result["evidence"] = [_evidence(item) for item in _objects(value.get("evidence"), MAX_EVIDENCE)]
    if value.get("break_reclaim_evidence") is not None:
        result["break_reclaim_evidence"] = _break_reclaim(value["break_reclaim_evidence"])
    if value.get("reclaim_history") is not None:
        result["reclaim_history"] = _reclaim_history(value["reclaim_history"])
    return result


def _structure(value, ticker):
    source = _mapping(value)
    if source.get("symbol") not in (None, ticker):
        raise EvidenceError("structure_symbol_conflict")
    result = {**_numbers(source, ("current_price",)), **_timestamps(source, ("as_of",)),
              **_enums(source, {"model": MODELS, "asset_class": {"stock"}, "horizon": {"swing"}})}
    if source.get("symbol") == ticker:
        result["symbol"] = ticker
    zones = _objects(source.get("zones"), MAX_ZONES)
    if sum(len(_objects(zone.get("evidence"), MAX_EVIDENCE)) for zone in zones) > MAX_TOTAL_EVIDENCE:
        raise EvidenceError("candidate_shape_invalid")
    result["zones"] = [_zone(item) for item in zones]
    result["atr_by_timeframe"] = _numbers(_mapping(source.get("atr_by_timeframe")), sorted(TIMEFRAMES))
    result["completed_bar_counts"] = _counts(_mapping(source.get("completed_bar_counts")), sorted(TIMEFRAMES))
    result["quality_flags"] = _enum_list(source.get("quality_flags"), QUALITY_FLAGS)
    return result


def _barrier(value):
    source = _mapping(value)
    result = {**_numbers(source, ("price", "zone_low", "zone_high", "distance_r", "distance_atr",
                                  "distance_pct", "entry_boundary", "minimum_reward", "minimum_rr",
                                  "strength", "reclaim_boundary")),
              **_counts(source, ("independent_sources",)),
              **_booleans(source, ("overlapping", "reclaimed", "breakout_confirmed", "below_minimum_reward",
                                    "entry_inside_zone", "is_projection", "structural", "causal_structure_validated")),
              **_timestamps(source, ("confirmed_at", "data_cutoff_at")), **_zone_ids(source, ("zone_id",)),
              **_profile_ids(source, ("profile_id",)),
              **_enums(source, {"side": ROLES, "action": GATES, "source_family": SOURCE_FAMILIES,
                                "kind": {"POC", "VAH", "VAL", "HVN"},
                                "distance_basis": {"zone_low", "zone_high"}})}
    if "action" in source and source["action"] is None:
        result["action"] = None
    if (timeframe := _timeframe(source.get("timeframe"))) is not None:
        result["timeframe"] = timeframe
    if (label := _source_label(source.get("source"))) is not None:
        result["source"] = label
    if (key := _independence_key(source.get("independence_key"))) is not None:
        result["independence_key"] = key
    for field in ("break_reclaim", "break_reclaim_evidence"):
        if source.get(field) is not None:
            result[field] = _break_reclaim(source[field])
    if source.get("reclaim_history") is not None:
        result["reclaim_history"] = _reclaim_history(source["reclaim_history"])
    return result


def _decision(value):
    source = _mapping(value)
    result = {**_numbers(source, ("entry", "stop", "risk", "barrier_distance", "barrier_r", "target1", "target2")),
              **_booleans(source, ("entry_eligible",)),
              **_enums(source, {"model": MODELS, "status": STRUCTURE_STATES, "reason": PLAN_REASONS,
                                "direction": DIRECTIONS, "barrier_gate": GATES,
                                "geometry_updated_by": {"vrvp_trade_setup"}})}
    if "barrier_gate" in source and source["barrier_gate"] is None:
        result["barrier_gate"] = None
    if "nearest_barrier" in source:
        # Native decisions contain a full zone; final VRVP decisions contain a
        # flat barrier. Preserve the safe union without inventing either shape.
        barrier = source["nearest_barrier"]
        result["nearest_barrier"] = None if barrier is None else {**_zone(_mapping(barrier)), **_barrier(barrier)}
    for field in ("stop_evidence", "target1_evidence", "target2_evidence"):
        if field in source:
            result[field] = None if source[field] is None else _barrier(source[field])
    return result


def _plan_fields(source):
    result = {**_enums(source, {"direction": DIRECTIONS, "structure_status": STRUCTURE_STATES,
                                "structure_reason": PLAN_REASONS, "barrier_gate": GATES,
                                "level_model": LEVEL_MODELS, "Level_Model": LEVEL_MODELS,
                                "target_quality": {"PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
                                                   "STRUCTURAL_TP1_PROJECTION_TP2", "STRUCTURAL_FIRST_BARRIER"},
                                "breakout_confirmation": {"confirmed_close"},
                                "retest_status": {"confirmed", "not_confirmed"}}),
              **_booleans(source, ("barrier_gate_active",))}
    if "barrier_gate" in source and source["barrier_gate"] is None:
        result["barrier_gate"] = None
    for field in ("nearest_barrier", "overhead_resistance", "underlying_support", "structure_decision"):
        if field in source:
            result[field] = (None if source[field] is None else
                             _decision(source[field]) if field == "structure_decision" else _barrier(source[field]))
    return result


def _trade_setup(value):
    source = _mapping(value)
    prefixes = ("stop", "tp1", "tp2")
    result = {**_numbers(source, ("entry", "Entry", "stop", "StopLoss", "tp1", "TP1", "tp2", "TP2",
                                  "risk", "rr", "rr_tp1", "rr_tp2", "atr", "room_to_barrier_r",
                                  "vrvp_poc", "vrvp_vah", "vrvp_val")
                         + tuple(prefix + suffix for prefix in prefixes for suffix in ("_zone_low", "_zone_high"))),
              **_booleans(source, ("stop_is_projection", "tp1_is_projection", "tp2_is_projection", "vrvp_applied")
                          + tuple(prefix + "_causal_structure_validated" for prefix in prefixes)),
              **_zone_ids(source, tuple(prefix + "_zone_id" for prefix in prefixes)),
              **_timestamps(source, tuple(prefix + suffix for prefix in prefixes
                                           for suffix in ("_confirmed_at", "_data_cutoff_at"))),
              **_plan_fields(source)}
    for prefix in prefixes:
        result.update(_enums(source, {prefix + "_source_family": SOURCE_FAMILIES}))
        if (timeframe := _timeframe(source.get(prefix + "_timeframe"))) is not None:
            result[prefix + "_timeframe"] = timeframe
        if (key := _independence_key(source.get(prefix + "_independence_key"))) is not None:
            result[prefix + "_independence_key"] = key
        if (label := _source_label(source.get(prefix + "_source"))) is not None:
            result[prefix + "_source"] = label
    result["alias_conflicts"] = _alias_conflicts(source, {"entry": ("entry", "Entry"), "stop": ("stop", "StopLoss"),
                                                        "tp1": ("tp1", "TP1"), "tp2": ("tp2", "TP2")})
    return result


def _swing_reference(row):
    result = {**_numbers(row, ("swing_reference_close",)),
              **_counts(row, ("stock_swing_contract_version", "swing_data_delay_seconds")),
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


def _alias_conflicts(source, groups=ALIAS_GROUPS):
    result = []
    for name, fields in groups.items():
        values = [source[field] for field in fields if field in source]
        if len(values) > 1 and any(type(value) is not type(values[0]) or value != values[0] for value in values[1:]):
            result.append(name)
    return result


def _candidate(row, index, ticker):
    aliases = {key: row[key] for key in ("ticker", "Ticker") if row.get(key) in TICKERS}
    if any(row.get(key) not in (None, "", ticker) for key in ("ticker", "Ticker")):
        return {"ticker": ticker, "status": "ticker_alias_conflict", "row_index": index,
                "ticker_aliases": aliases, "alias_conflicts": ["ticker"]}
    diagnostic = _mapping(row.get("native_plan_diagnostics"))
    result = {"ticker": ticker, "status": "ok", "row_index": index, "ticker_aliases": aliases,
              "alias_conflicts": _alias_conflicts(row),
              "scanner_numbers": {**_numbers(row, NUMBER_FIELDS), **_counts(row, ("History_Bars", "history_bars"))},
              "scanner_booleans": _booleans(row, ("History_OK", "history_ok")),
              "scanner_timestamps": _timestamps(row, ("analysis_as_of", "Analysis_As_Of", "as_of")),
              "scanner_enums": _enums(row, {"Strategy": {"Momentum Breakout Long"}, "strategy": {"Momentum Breakout Long"},
                                             "grade": {"S", "A", "B", "C", "D", "F"}, "Grade": {"S", "A", "B", "C", "D", "F"}}),
              "swing_reference": _swing_reference(row),
              "native_plan_diagnostics": {**_enums(diagnostic, {"status": {"built", "unavailable"}, "reason": PLAN_REASONS})},
              **_enums(row, {"native_plan_status": {"built", "unavailable"}, "native_plan_reason": PLAN_REASONS}),
              **_plan_fields(row), "trade_setup": _trade_setup(row.get("trade_setup"))}
    if diagnostic.get("barrier") is not None:
        result["native_plan_diagnostics"]["barrier"] = _barrier(diagnostic["barrier"])
    # Keep both present aliases, including explicit nulls: never choose a winner.
    for field in ("level_structure", "Level_Structure"):
        if field in row:
            result[field] = None if row[field] is None else _structure(row[field], ticker)
    return result


def _snapshot_metadata(source):
    source = _mapping(source)
    result = {"name": CACHE_NAME}
    if type(source.get("sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        result["sha256"] = source["sha256"]
    for field, limit in (("size_bytes", MAX_BYTES), ("mtime_ns", 2**63 - 1)):
        if type(source.get(field)) is int and 0 <= source[field] <= limit:
            result[field] = source[field]
    return result


def _cache_clock(value):
    """Preserve cache serialization time without assuming the server timezone."""
    if type(value) is not str or not 19 <= len(value) <= 40:
        return {}
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return {}
    result = {"cached_at": value, "cached_at_timezone": "unknown_local_time"}
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        if (normalized := _timestamp(value)) is None:
            return {}
        result.update(cached_at_timezone="explicit_utc_offset", cached_at_utc=normalized)
    return result


def project_payload(payload, snapshot):
    if type(payload) is not dict or ("partial" in payload and payload["partial"] is not False):
        raise EvidenceError("invalid_cache_shape")
    rows = payload.get("results")
    if type(rows) is not list or len(rows) > MAX_ROWS or any(type(row) is not dict for row in rows):
        raise EvidenceError("invalid_cache_shape")
    cache = {**_snapshot_metadata(snapshot), "rows_total": len(rows), **_counts(payload, ("cache_version",)),
             **_numbers(payload, ("timestamp",)), **_booleans(payload, ("partial",)),
             **_cache_clock(payload.get("cached_at"))}
    cache["diagnostics"] = _timestamps(_mapping(payload.get("diagnostics")), ("analysis_as_of",))
    if type(payload.get("timestamp")) in (int, float) and "timestamp" in cache:
        try:
            cache["timestamp_utc"] = datetime.fromtimestamp(payload["timestamp"], timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    elif (stamp := _timestamp(payload.get("timestamp"))) is not None:
        cache["timestamp_utc"] = stamp
    candidates = []
    for ticker in TICKERS:
        matches = [(index, row) for index, row in enumerate(rows)
                   if row.get("ticker") == ticker or row.get("Ticker") == ticker]
        if not matches:
            candidates.append({"ticker": ticker, "status": "not_found"})
        elif len(matches) != 1:
            candidates.append({"ticker": ticker, "status": "not_unique", "row_indices": [index for index, _ in matches]})
        else:
            index, row = matches[0]
            try:
                candidates.append(_candidate(row, index, ticker))
            except EvidenceError as error:
                candidates.append({"ticker": ticker, "status": "invalid", "row_index": index, "reason": _reason(error)})
    return {"schema_version": 1, "kind": "momentum_candidate_evidence", "read_only": True,
            "semantics": SEMANTICS, "status": "ok", "cache": cache, "candidates": candidates,
            "raw_daily_prefix": {"status": "not_stored_in_final_result_cache",
                                 "availability_elsewhere": "unknown_not_inspected", "provider_fetch_performed": False}}


def _identity(info):
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
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
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
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
    return payload, {"name": CACHE_NAME, "size_bytes": len(raw), "mtime_ns": after.st_mtime_ns, "sha256": digest}


def service_pid():
    try:
        result = subprocess.run(["/usr/bin/systemctl", "show", API_UNIT, "--property=MainPID", "--value"],
                                check=True, capture_output=True, text=True, timeout=10)
        value = result.stdout.strip()
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 2**31 - 1:
            raise EvidenceError("api_inactive")
        return int(value)
    except (OSError, subprocess.SubprocessError):
        raise EvidenceError("service_identity_unavailable") from None


def process_root(pid):
    # /proc/PID/root is an intentional kernel namespace link; do not resolve it.
    return Path("/proc") / str(pid) / "root"


def process_start_ticks(pid):
    try:
        with open(Path("/proc") / str(pid) / "stat", "r", encoding="ascii") as stream:
            text = stream.read(4097)
        tail = text[text.rindex(")") + 2:].split()
        value = int(tail[19])  # proc stat field 22; tail begins with field 3.
        if len(text) > 4096 or value <= 0:
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError, IndexError):
        raise EvidenceError("service_identity_unavailable") from None


def collect():
    pid = service_pid()
    started = process_start_ticks(pid)
    result = project_payload(*read_cache(process_root(pid) / "tmp" / CACHE_NAME))
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
        output = json.dumps({"schema_version": 1, "kind": "momentum_candidate_evidence", "read_only": True,
                             "semantics": SEMANTICS, "status": "unavailable", "reason": _reason(error)})
        print(output)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
