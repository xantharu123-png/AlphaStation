"""Pure shared validation of explicit Wyckoff entry anchors.

This is evidence validation, not quote freshness, execution or mail approval.
Repeated descriptive events cannot replace the trigger's nominated anchors.
"""
from datetime import datetime, timezone
import math
import re
from collections.abc import Mapping


ROLES = ("origin", "reaction", "test", "breakout", "retest")


def _clock(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _positive(value):
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and value > 0)
    except OverflowError:
        return False


def _identity(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value))


def validate_entry_trigger(pattern, *, as_of, timeframe="1D", model=None):
    """Return the nominated trigger if all required evidence is sound, else None.

    Supports selected-chart timeframes for local replay. API callers additionally
    pin the scanner's outer model and 1D metadata and current plan geometry.
    """
    if not isinstance(pattern, Mapping) or not _identity(pattern.get("structure_id")):
        return None
    if model is not None and pattern.get("model") != model:
        return None
    if pattern.get("timeframe") != timeframe:
        return None
    if not all(isinstance(pattern.get(key), str) for key in (
            "entry_state", "signal_state", "structure_state", "phase", "direction", "structure_type", "origin_kind")):
        return None
    if (pattern.get("trade_ready") is not True or pattern.get("entry_state") != "ready"
            or pattern.get("signal_state") != "confirmed"
            or pattern.get("structure_state") not in {"confirmed", "continuation"}
            or pattern.get("phase") not in {"D", "E"} or pattern.get("invalidation_reason")):
        return None
    direction = pattern.get("direction")
    kinds = {"LONG": {"Accumulation", "Reaccumulation"},
             "SHORT": {"Distribution", "Redistribution"}}
    if direction not in kinds or pattern.get("structure_type") not in kinds[direction]:
        return None
    origin_kind = pattern.get("origin_kind")
    if origin_kind not in {"climactic", "nonclimactic", "continuation"}:
        return None
    if (pattern["structure_type"] in {"Reaccumulation", "Redistribution"}) != (origin_kind == "continuation"):
        return None
    trigger = pattern.get("entry_trigger")
    if (not isinstance(trigger, Mapping) or not _identity(trigger.get("trigger_id"))
            or trigger.get("state") != "ready" or trigger.get("reason")):
        return None
    refs = trigger.get("event_ids")
    if not isinstance(refs, Mapping) or set(refs) != set(ROLES):
        return None
    if not all(_identity(refs[role]) for role in ROLES) or len(set(refs.values())) != len(ROLES):
        return None
    cutoff, last, range_at, signal_at, trigger_at = (_clock(value) for value in (
        as_of, pattern.get("latest_completed_at"), pattern.get("range_confirmed_at"),
        pattern.get("signal_confirmed_at"), trigger.get("confirmed_at")))
    if any(value is None for value in (cutoff, last, range_at, signal_at, trigger_at)):
        return None
    if not range_at <= signal_at == trigger_at <= last <= cutoff:
        return None
    events = pattern.get("events")
    if not isinstance(events, list) or not events or len(events) > 10000:
        return None
    by_id = {}
    for event in events:
        if not isinstance(event, Mapping) or not _identity(event.get("event_id")):
            return None
        identity = event["event_id"]
        if identity in by_id:
            return None
        if event.get("structure_id", pattern["structure_id"]) != pattern["structure_id"]:
            return None
        by_id[identity] = event
    long = direction == "LONG"
    allowed = {"origin": {("SC" if long else "BC") if origin_kind == "climactic" else "RangeOrigin"},
               "reaction": {"AR"}, "test": {"ST"},
               "breakout": {"SOS" if long else "SOW"},
               "retest": {"LPS" if long else "LPSY"}}
    previous = None
    for role in ROLES:
        event = by_id.get(refs[role])
        if event is None or not isinstance(event.get("name"), str) or event.get("name") not in allowed[role]:
            return None
        observed, confirmed = _clock(event.get("observed_at")), _clock(event.get("confirmed_at"))
        if observed is None or confirmed is None or not observed <= confirmed <= last:
            return None
        if previous is not None and observed <= previous:
            return None
        if not _positive(event.get("price")) or not _positive(event.get("volume_ratio")):
            return None
        if role == "reaction" and confirmed != range_at:
            return None
        previous = confirmed
    if previous != signal_at:
        return None
    return trigger


__all__ = ["ROLES", "validate_entry_trigger"]
