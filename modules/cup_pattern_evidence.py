"""Bounded, descriptive geometry from the detector's already-selected window.

This module never accepts/rejects a trade or re-selects a pattern. Its dates are
input-session labels, not a new scan-time or candle-completion attestation.
"""
from datetime import date
import math
import re


VERSION = "cup_geometry_v1"
AS_OF_SEMANTICS = "selected_input_last_session"
INDEX_BASIS = "selected_window_zero_based"
ANCHOR_FIELDS = {
    "left_rim": "high", "bottom": "low", "right_rim": "high",
    "handle_start": "close", "handle_low": "low", "handle_end": "close",
    "breakout": "close",
}
REASONS = frozenset({"missing_session", "invalid_session", "non_monotonic_sessions",
                     "invalid_geometry", "invalid_bar_values", "invalid_evidence",
                     "legacy_evidence_missing", "symbol_mismatch"})


def _session(value):
    if type(value) is not str or len(value) != 10:
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def _symbol(value):
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if re.fullmatch(r"[A-Z0-9][A-Z0-9._^\-]{0,31}", value) else None


def unavailable(reason, *, symbol=None):
    result = {"version": VERSION, "status": "unavailable", "timeframe": "1D",
              "reason": reason if type(reason) is str and reason in REASONS else "invalid_evidence"}
    if _symbol(symbol):
        result["symbol"] = _symbol(symbol)
    return result


def _issues(anchors):
    issues = []
    if anchors["left_rim"]["index"] >= anchors["bottom"]["index"]:
        issues.append("left_rim_not_before_bottom")
    if anchors["bottom"]["index"] >= anchors["right_rim"]["index"]:
        issues.append("bottom_not_before_right_rim")
    if anchors["handle_low"]["index"] == anchors["breakout"]["index"]:
        issues.append("handle_low_on_breakout")
    return issues


def build_cup_geometry_evidence(segment, *, cup_length, handle_length,
                                session_getter, number_getter):
    """Describe exactly this selected split, using first-tie extrema as max/min.

    Indices are relative to the selected window AFTER the detector's existing
    price cleaning; dated anchors, not indices, bind them to a displayed chart.
    The existing handle minimum includes the final breakout candle, whereas
    handle_end intentionally marks the preceding candle.
    """
    if (type(cup_length) is not int or type(handle_length) is not int
            or cup_length < 45 or handle_length < 5
            or not isinstance(segment, (list, tuple))
            or len(segment) != cup_length + handle_length or len(segment) > 170):
        return unavailable("invalid_geometry")
    try:
        sessions = [session_getter(bar) for bar in segment]
    except Exception:
        return unavailable("invalid_session")
    if any(value in (None, "") for value in sessions):
        return unavailable("missing_session")
    if any(_session(value) is None for value in sessions):
        return unavailable("invalid_session")
    if any(right <= left for left, right in zip(sessions, sessions[1:])):
        return unavailable("non_monotonic_sessions")
    try:
        values = [{field: number_getter(bar, field, field[0])
                   for field in ("high", "low", "close")} for bar in segment]
    except Exception:
        return unavailable("invalid_bar_values")
    if any(_number(value) is None for row in values for value in row.values()):
        return unavailable("invalid_bar_values")
    # These ranges match the existing detector, including its overlapping rim
    # and middle regions. Do not force a nicer chronology or pick another low.
    left = range(0, max(8, int(cup_length * .32)))
    middle = range(int(cup_length * .22), int(cup_length * .78))
    right = range(int(cup_length * .62), cup_length)
    handle = range(cup_length, len(segment))
    indices = {
        "left_rim": max(left, key=lambda i: values[i]["high"]),
        "bottom": min(middle, key=lambda i: values[i]["low"]),
        "right_rim": max(right, key=lambda i: values[i]["high"]),
        "handle_start": cup_length,
        "handle_low": min(handle, key=lambda i: values[i]["low"]),
        "handle_end": len(segment) - 2,
        "breakout": len(segment) - 1,
    }
    anchors = {name: {"session": sessions[index], "price": values[index][field],
                       "price_field": field, "index": index}
               for name, field in ANCHOR_FIELDS.items() for index in [indices[name]]}
    issues = _issues(anchors)
    return {"version": VERSION, "status": "available", "timeframe": "1D",
            "as_of_session": sessions[-1], "as_of_semantics": AS_OF_SEMANTICS,
            "window_start_session": sessions[0], "window_end_session": sessions[-1],
            "cup_length": cup_length, "handle_length": handle_length,
            "index_basis": INDEX_BASIS, "handle_includes_breakout": True,
            "handle_low_on_breakout": indices["handle_low"] == indices["breakout"],
            "geometry_status": "chronology_mismatch" if issues else "ordered",
            "geometry_issues": issues, "anchors": anchors}


def project_cup_geometry_evidence(evidence, *, symbol=None):
    """Whitelist validated geometry without copying arbitrary nested payloads."""
    bound_symbol = _symbol(symbol)
    if not isinstance(evidence, dict):
        return unavailable("legacy_evidence_missing", symbol=bound_symbol)
    source_symbol = _symbol(evidence.get("symbol"))
    if ("symbol" in evidence and source_symbol is None) or (
            bound_symbol and source_symbol and bound_symbol != source_symbol):
        return unavailable("symbol_mismatch", symbol=bound_symbol)
    bound_symbol = bound_symbol or source_symbol
    if evidence.get("version") != VERSION or evidence.get("timeframe") != "1D":
        return unavailable("invalid_evidence", symbol=bound_symbol)
    if evidence.get("status") == "unavailable":
        return unavailable(evidence.get("reason"), symbol=bound_symbol)
    if (evidence.get("status") != "available"
            or evidence.get("as_of_semantics") != AS_OF_SEMANTICS
            or evidence.get("index_basis") != INDEX_BASIS
            or evidence.get("handle_includes_breakout") is not True):
        return unavailable("invalid_evidence", symbol=bound_symbol)
    cup_length, handle_length = evidence.get("cup_length"), evidence.get("handle_length")
    if (type(cup_length) is not int or type(handle_length) is not int
            or cup_length < 45 or handle_length < 5 or cup_length + handle_length > 170):
        return unavailable("invalid_evidence", symbol=bound_symbol)
    total = cup_length + handle_length
    start, end = _session(evidence.get("window_start_session")), _session(evidence.get("window_end_session"))
    if start is None or end is None or start >= end or evidence.get("as_of_session") != end:
        return unavailable("invalid_evidence", symbol=bound_symbol)
    if (date.fromisoformat(end) - date.fromisoformat(start)).days < total - 1:
        return unavailable("invalid_evidence", symbol=bound_symbol)
    source = evidence.get("anchors")
    if not isinstance(source, dict):
        return unavailable("invalid_evidence", symbol=bound_symbol)
    index_ranges = {
        "left_rim": range(max(8, int(cup_length * .32))),
        "bottom": range(int(cup_length * .22), int(cup_length * .78)),
        "right_rim": range(int(cup_length * .62), cup_length),
        "handle_start": (cup_length,), "handle_low": range(cup_length, total),
        "handle_end": (total - 2,), "breakout": (total - 1,),
    }
    anchors = {}
    for name, field in ANCHOR_FIELDS.items():
        raw = source.get(name)
        if (not isinstance(raw, dict) or raw.get("price_field") != field
                or type(raw.get("index")) is not int or raw["index"] not in index_ranges[name]
                or _session(raw.get("session")) is None or not start <= raw["session"] <= end
                or (raw["index"] == 0 and raw["session"] != start)
                or _number(raw.get("price")) is None):
            return unavailable("invalid_evidence", symbol=bound_symbol)
        anchors[name] = {"session": raw["session"], "price": raw["price"],
                         "price_field": field, "index": raw["index"]}
    if anchors["breakout"]["session"] != end:
        return unavailable("invalid_evidence", symbol=bound_symbol)
    ordered = sorted(anchors.values(), key=lambda anchor: anchor["index"])
    if any((right["index"] == left["index"] and right["session"] != left["session"])
           or (right["index"] > left["index"] and right["session"] <= left["session"])
           for left, right in zip(ordered, ordered[1:])):
        return unavailable("invalid_evidence", symbol=bound_symbol)
    issues = _issues(anchors)
    result = {"version": VERSION, "status": "available", "timeframe": "1D",
              "as_of_session": end, "as_of_semantics": AS_OF_SEMANTICS,
              "window_start_session": start, "window_end_session": end,
              "cup_length": cup_length, "handle_length": handle_length,
              "index_basis": INDEX_BASIS, "handle_includes_breakout": True,
              "handle_low_on_breakout": anchors["handle_low"]["index"] == anchors["breakout"]["index"],
              "geometry_status": "chronology_mismatch" if issues else "ordered",
              "geometry_issues": issues, "anchors": anchors}
    if bound_symbol:
        result["symbol"] = bound_symbol
    return result
