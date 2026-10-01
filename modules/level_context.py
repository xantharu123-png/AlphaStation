"""Read-only evidence adapter for existing chart and scanner levels.

This module neither builds trade levels nor changes eligibility.  In particular,
an ``as_of`` argument is a validation ceiling, never a replacement data cutoff.
Execution prices and physical zone boundaries deliberately remain separate.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math
import re
from typing import Any


LEVEL_CONTEXT_VERSION = 1
_CONFIRMED_FAMILIES = frozenset({"level_zone", "vrvp"})
_PRICE_KEYS = {
    "entry": ("entry", "Entry"),
    "stop": ("stop", "StopLoss", "stop_loss", "Stop"),
    "tp1": ("tp1", "TP1", "target1", "Target1"),
    "tp2": ("tp2", "TP2", "target2", "Target2"),
}


def _mapping(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    # Supports the immutable level_zones dataclasses without importing the API.
    if type(value).__module__ == "modules.level_zones" and callable(getattr(value, "to_dict", None)):
        return value.to_dict()
    return {}


def _first(values: Mapping, *keys: str) -> Any:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None


def _time_text(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else _text(value)


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # A calendar date/naive time is not a proven completion instant.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _geometry_family(model: Any, source: Any = None) -> str:
    text = f"{model or ''} {source or ''}".lower()
    if "turtle" in text or "donchian" in text:
        return "donchian_geometry"
    if "orb" in text or "or midpoint" in text or "or measured" in text:
        return "opening_range_geometry"
    if "bi_shared" in text or "range_" in text or "range " in text:
        return "range_geometry"
    if "penny" in text:
        return "penny_structure"
    if "crypto" in text or "breakout_structure" in text or "listing" in text:
        return "crypto_structure"
    return "unknown"


def _projection(raw: Mapping, purpose: str, model: str | None) -> bool:
    source = str(raw.get("source") or "").lower()
    family = str(raw.get("source_family") or "").lower()
    if raw.get("is_projection") is True or raw.get("projection_only") is True:
        return True
    if any(word in family for word in ("projection", "fibonacci", "measured_move")):
        return True
    if any(word in source for word in ("projection", "measured move", "measured_move",
                                      "range_extension", "range extension", "r_multiple", "r-multiple",
                                      "fib_", "fibonacci", "fallback", "estimated")):
        return True
    if purpose in {"tp1", "tp2"} and re.search(r"\b(?:fib|fibonacci|atr)\b|\d+(?:\.\d+)?r\b", source):
        return True
    # Old own-geometry plans can omit target labels; this does not apply to a
    # target carrying explicit, validated VRVP/zone provenance.
    return bool(purpose in {"tp1", "tp2"} and not source
                and raw.get("causal_structure_validated") is not True
                and _geometry_family(model) in {"donchian_geometry", "opening_range_geometry"})


def _record(raw: Mapping, *, purpose: str, role: str | None, model: str | None,
            validation_as_of: Any = None) -> dict:
    source = _text(_first(raw, "source", "source_name"))
    # A descriptive model/source label is not an original provenance family.
    family = _text(raw.get("source_family")) or "unknown"
    geometry_family = _geometry_family(model, source)
    projected = _projection(raw, purpose, model)
    result = {
        "purpose": purpose,
        "role": role,
        # LevelZone.reference is the quote used for classification, not a level.
        "price": _number(raw.get("price")),
        "price_role": "execution_price" if purpose in {"stop", "tp1", "tp2"} else "level_price",
        "zone_low": _number(_first(raw, "zone_low", "lower")),
        "zone_high": _number(_first(raw, "zone_high", "upper")),
        "zone_id": _text(raw.get("zone_id")),
        "independence_key": _text(raw.get("independence_key")),
        "source": source,
        "source_family": family,
        "geometry_family": geometry_family,
        "model": _text(raw.get("model")) or model,
        "timeframe": _text(raw.get("timeframe")),
        "confirmed_at": _time_text(raw.get("confirmed_at")),
        "data_cutoff_at": _time_text(raw.get("data_cutoff_at")),
        "projection_only": projected,
        "causal_structure_validated": False,
        "evidence_status": "unverified",
        "reason": "producer_confirmation_missing",
    }
    if projected:
        result.update(evidence_status="projection_only", reason="explicit_or_native_projection")
        return result
    if purpose in {"stop", "tp1", "tp2"} and result["price"] is None:
        result["reason"] = "execution_price_invalid_or_missing"
        return result
    if raw.get("causal_structure_validated") is not True:
        if geometry_family in {"range_geometry", "opening_range_geometry", "donchian_geometry"}:
            result.update(evidence_status="native_geometry", reason="native_geometry_not_confirmed_sr")
        return result
    if family.lower() not in _CONFIRMED_FAMILIES:
        result["reason"] = "unsupported_confirmation_family"
        return result
    if not result["zone_id"] or not result["timeframe"]:
        result["reason"] = "zone_identity_or_timeframe_missing"
        return result
    low, high = result["zone_low"], result["zone_high"]
    if low is None or high is None or high < low:
        result["reason"] = "physical_zone_bounds_invalid_or_missing"
        return result
    confirmed, cutoff = _time(result["confirmed_at"]), _time(result["data_cutoff_at"])
    ceiling = _time(validation_as_of)
    if confirmed is None or cutoff is None or (validation_as_of is not None and ceiling is None):
        result.update(evidence_status="causality_unknown", reason="completion_or_cutoff_unknown")
        return result
    if confirmed > cutoff or (ceiling is not None and cutoff > ceiling):
        result["reason"] = "future_evidence_or_cutoff"
        return result
    observed = _time(raw.get("observed_at"))
    if observed is not None and observed > confirmed:
        result["reason"] = "confirmation_before_observation"
        return result
    result.update(evidence_status="confirmed", reason=None, causal_structure_validated=True)
    return result


def _snapshot_records(snapshot: Any, ceiling: Any) -> list[dict]:
    snap = _mapping(snapshot)
    model = _text(snap.get("model"))
    if not model or not model.startswith(("causal_level_zones_v", "directional_level_zones_v")):
        return []
    rows = []
    if isinstance(snap.get("zones"), (list, tuple)):
        rows.extend((zone, None) for zone in snap["zones"])
    else:
        for key, role in (("supports", "support"), ("resistances", "resistance"), ("overlapping", "overlap")):
            if isinstance(snap.get(key), (list, tuple)):
                rows.extend((zone, role) for zone in snap[key])
    result = []
    for value, explicit_role in rows:
        zone = _mapping(value)
        if not zone:
            continue
        evidence = [_mapping(item) for item in zone.get("evidence", ()) if _mapping(item)]
        # Projection provenance stays projection-only even if an aggregate
        # snapshot has an inconsistent/missing projection flag.
        structural = [item for item in evidence if item.get("projection_only") is not True
                      and not any(word in str(item.get("source_family") or "").lower()
                                  for word in ("fib", "projection", "measured_move", "atr_extension"))]
        low, high = _number(zone.get("lower")), _number(zone.get("upper"))
        cutoff, confirmed = _time(snap.get("as_of")), _time(zone.get("confirmed_at"))
        valid_members = bool(evidence and structural and low is not None and high is not None)
        for item in evidence:
            observed = _time(item.get("observed_at"))
            completed = _time(item.get("confirmed_at"))
            member_cutoff = _time(item.get("data_cutoff_at"))
            member_low, member_high = _number(item.get("lower")), _number(item.get("upper"))
            valid_members = bool(valid_members and _text(item.get("source_family"))
                                 and _text(item.get("timeframe")) and observed and completed and member_cutoff
                                 and cutoff and confirmed and observed <= completed <= member_cutoff <= cutoff
                                 and completed <= confirmed and member_low is not None and member_high is not None
                                 and low <= member_low <= member_high <= high)
        raw = {**zone, "source_family": "level_zone", "model": model,
               "source": ", ".join(str(item) for item in zone.get("sources", ())) or None,
               "timeframe": "/".join(sorted({_text(item.get("timeframe")) for item in evidence
                                               if _text(item.get("timeframe"))})) or None,
               "data_cutoff_at": snap.get("as_of"),
               "projection_only": zone.get("projection_only") is True or bool(evidence and not structural),
               "causal_structure_validated": valid_members and _number(zone.get("independent_structural_sources")) is not None}
        role = explicit_role or _text(zone.get("side_at_reference"))
        item = _record(raw, purpose="snapshot_zone", role=role if role in {"support", "resistance", "overlap"} else None,
                       model=model, validation_as_of=ceiling)
        if not item["projection_only"] and evidence and (
            cutoff is None or confirmed is None or any(
                _time(member.get(field)) is None for member in evidence
                for field in ("observed_at", "confirmed_at", "data_cutoff_at"))
        ):
            item.update(evidence_status="causality_unknown", reason="completion_or_cutoff_unknown")
        result.append(item)
    return result


def _dedupe(records: list[dict]) -> list[dict]:
    """Only identical physical zone identities can merge; never merge prices."""
    groups, ordered = {}, []
    for record in records:
        if not record["zone_id"]:
            ordered.append([record])
            continue
        key = (record["source_family"], record["timeframe"], record["zone_id"], record["evidence_status"])
        if key not in groups:
            groups[key] = []
            ordered.append(groups[key])
        groups[key].append(record)
    result = []
    for members in ordered:
        bounds = {(item["zone_low"], item["zone_high"]) for item in members}
        if len(bounds) > 1:
            # Also downgrade the original target/stop objects returned below.
            for item in members:
                item.update(evidence_status="unverified", causal_structure_validated=False,
                            reason="conflicting_bounds_for_same_zone")
        merged = dict(members[0])
        merged["roles"] = sorted({item["role"] for item in members if item["role"]})
        merged["references"] = []
        for item in members:
            reference = {"purpose": item["purpose"], "price": item["price"],
                         "price_role": item["price_role"], "role": item["role"]}
            if reference not in merged["references"]:
                merged["references"].append(reference)
        if len(bounds) > 1:
            merged["conflicting_bounds"] = [{"zone_low": low, "zone_high": high}
                                             for low, high in sorted(bounds, key=lambda pair: repr(pair))]
        result.append(merged)
    return result


def build_level_context(plan: Any, *, direction: Any = None, timeframe: Any = None,
                        as_of: Any = None, snapshot: Any = None) -> dict:
    """Describe existing levels without mutating them or asserting trade release.

    ``plan`` may be a plan dict or a scanner row containing ``trade_setup`` or
    ``_alert_trade_levels``. ``snapshot`` optionally accepts a canonical snapshot
    or directional structure (dict or level_zones dataclass). Explicit per-level
    timeframes/cutoffs are never filled from the caller's display timeframe/time.
    Only confirmed physical zones enter support_levels/resistance_levels.
    """
    row = _mapping(plan)
    nested = _mapping(row.get("trade_setup")) or _mapping(row.get("_alert_trade_levels"))
    values = {**row, **nested}
    model = _text(_first(values, "level_model", "Level_Model", "model", "plan_version", "BI_PlanVersion"))
    side = str(direction or _first(values, "direction", "Direction", "Signal_Direction", "signal", "Signal") or "").upper()
    side = side if side in {"LONG", "SHORT"} else None
    base_family = _text(values.get("source_family")) or "unknown"
    entry = _number(_first(values, *_PRICE_KEYS["entry"]))
    records, targets, stop = [], {}, None
    for purpose in ("stop", "tp1", "tp2"):
        raw_price = _first(values, *_PRICE_KEYS[purpose])
        if raw_price is None:
            continue
        raw = {key: values.get(f"{purpose}_{key}") for key in (
            "source", "source_family", "timeframe", "zone_id", "zone_low", "zone_high",
            "confirmed_at", "observed_at", "data_cutoff_at", "independence_key", "causal_structure_validated", "is_projection")}
        raw["price"] = raw_price
        role = ("support" if side == "LONG" else "resistance") if purpose == "stop" else (
            "resistance" if side == "LONG" else "support")
        item = _record(raw, purpose=purpose, role=role if side else None, model=model, validation_as_of=as_of)
        if purpose == "stop":
            stop = item
        else:
            targets[purpose] = item
        records.append(item)
    for key in ("nearest_barrier", "vrvp_first_barrier", "overhead_resistance", "underlying_support"):
        raw = _mapping(values.get(key))
        if raw:
            role = _text(raw.get("side")) or ("resistance" if key == "overhead_resistance" else
                                              "support" if key == "underlying_support" else None)
            records.append(_record(raw, purpose=key, role=role, model=model, validation_as_of=as_of))
    for key, role in (("support_levels", "support"), ("resistance_levels", "resistance")):
        for raw in values.get(key, ()) if isinstance(values.get(key), (list, tuple)) else ():
            item = _mapping(raw) or {"price": raw}
            records.append(_record(item, purpose=key, role=role, model=model, validation_as_of=as_of))
    # Early Movers retains an actual VRVP profile rather than flat target
    # identity fields. Expose its own proven levels, but do not infer a target's
    # identity merely because a target price is near/equal to a profile price.
    profile = _mapping(values.get("vrvp_levels"))
    if profile:
        for key, role in (("supports", "support"), ("resistances", "resistance")):
            for value in profile.get(key, ()) if isinstance(profile.get(key), (list, tuple)) else ():
                raw = _mapping(value)
                if not raw:
                    continue
                if any(profile.get(flag) is False for flag in ("causal_structure_validated", "causal_completion_verified")):
                    raw["causal_structure_validated"] = False
                records.append(_record(raw, purpose="vrvp_profile_level", role=role,
                                       model=_text(profile.get("model")) or _text(profile.get("profile_method")),
                                       validation_as_of=as_of))
    for key, role in (("Support_1", "support"), ("Resistance_1", "resistance")):
        if key in values and _number(values[key]) is not None:
            records.append(_record({"price": values[key]}, purpose=key, role=role, model=model, validation_as_of=as_of))
    existing_snapshot = snapshot if snapshot is not None else _first(values, "level_structure", "Level_Structure", "structure_snapshot", "directional_structure")
    records.extend(_snapshot_records(existing_snapshot, as_of))
    geometry = []
    for purpose, keys in (
        ("range_high", ("RangeHigh", "Range_High", "range_high")),
        ("range_low", ("RangeLow", "Range_Low", "range_low")),
        ("opening_range_high", ("OR_High", "ORHigh", "or_high")),
        ("opening_range_low", ("OR_Low", "ORLow", "or_low")),
        ("donchian_high", ("DC_High_20",)), ("donchian_low", ("DC_Low_10",)),
        ("trailing_exit", ("Exit_Level", "exit_level")),
    ):
        value = _first(values, *keys)
        if _number(value) is not None:
            item = _record({"price": value}, purpose=purpose, role=None, model=model, validation_as_of=as_of)
            item.update(evidence_status="native_geometry", reason="native_geometry_not_confirmed_sr")
            geometry.append(item)
    records = _dedupe(records)
    confirmed = [item for item in records if item["evidence_status"] == "confirmed"]
    families = sorted({item["source_family"] for item in records + geometry if item["source_family"] != "unknown"})
    if base_family != "unknown" and base_family not in families:
        families.append(base_family)
        families.sort()
    cutoffs = {item["data_cutoff_at"] for item in records if item["data_cutoff_at"]}
    summary = _mapping(values.get("level_structure_summary"))
    cutoff = (_time_text(_first(values, "data_cutoff_at", "analysis_as_of"))
              or _time_text(summary.get("as_of")) or _time_text(_mapping(existing_snapshot).get("as_of")))
    if cutoff is None and len(cutoffs) == 1:
        cutoff = next(iter(cutoffs))
    return {
        "version": LEVEL_CONTEXT_VERSION, "context_model": "level_context_v1", "model": model,
        "source_family": families[0] if len(families) == 1 else "mixed" if families else "unknown",
        "geometry_family": _geometry_family(model),
        "source_families": families, "direction": side,
        "timeframe": _text(timeframe) or _text(_first(values, "timeframe", "swing_timeframe", "VRVP_Timeframe")),
        "timeframes": sorted({item["timeframe"] for item in records if item["timeframe"]}),
        "data_cutoff_at": cutoff, "validation_as_of": _time_text(as_of), "entry": entry,
        "support_levels": [item for item in confirmed if item["role"] == "support"],
        "resistance_levels": [item for item in confirmed if item["role"] == "resistance"],
        "overlapping_levels": [item for item in confirmed if item["role"] == "overlap"],
        "unverified_levels": [item for item in records if item["evidence_status"] not in {"confirmed", "native_geometry"}],
        "geometry_levels": geometry + [item for item in records if item["evidence_status"] == "native_geometry"],
        "stop": stop, "targets": targets,
        "availability": "confirmed" if confirmed else "unverified" if records or geometry else "unavailable",
        "trade_release_asserted": False,
    }
