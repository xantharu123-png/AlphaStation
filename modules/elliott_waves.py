"""Bounded, causal Elliott *pattern context*, never a trade recommendation.

Fixed left/right-bar fractals identify two observed degrees. A geometric count
is not called confirmed unless every leg also has the required, independently
observed smaller-degree topology. Confirmation means this finite model's
rules, not a unique interpretation, a forecast, or recursively verified waves.

Daily stock callers supply actual session close timestamps. No market client,
live clock, missing-price interpolation or synthetic trade levels is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from itertools import islice
import json
import math
import re
from typing import Any, Mapping, Sequence

from modules.level_zones import CompletedBar, normalize_completed_bars


MODEL = "causal_elliott_v1"
UTC = timezone.utc
PARAMETERS = {
    "version": "elliott_pivots_v1",
    "maximum_bars": 800,
    "minimum_bars": 20,
    "pivot_radii": [3, 5, 8],
    "subdivision_radius": 1,
    "minimum_leg_atr": 0.75,
    "minimum_subwave_atr": 0.20,
    "maximum_patterns": 8,
    "maximum_bars_since_pattern": 120,
    "regular_flat_min_b_retrace": 0.90,
    "regular_flat_max_c_extension": 1.50,
    "confirmation_scope": "one_observed_subdivision_degree",
    "degree_scope": "local_pivot_scale_not_universal_elliott_degree",
}
UNSUPPORTED_FAMILIES = [
    "diagonals", "truncated_impulses", "running_flats", "expanding_triangles",
    "running_triangles", "triangle_throwovers", "double_or_triple_corrections",
    "extended_subdivision_counts", "recursive_subdivisions_beyond_one_degree",
]
_LABELS = {
    "impulse": ("0", "1", "2", "3", "4", "5"),
    "zigzag": ("0", "A", "B", "C"),
    "regular_flat": ("0", "A", "B", "C"),
    "expanded_flat": ("0", "A", "B", "C"),
    "contracting_triangle": ("0", "A", "B", "C", "D", "E"),
}
_SUBDIVISIONS = {
    "impulse": (5, 3, 5, 3, 5),
    "zigzag": (5, 3, 5),
    "regular_flat": (3, 3, 5),
    "expanded_flat": (3, 3, 5),
    "contracting_triangle": (3, 3, 3, 3, 3),
}


def _utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("explicit timezone-aware timestamp required")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value: Any, *, positive: bool = True) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not market evidence")
    parsed = float(value)
    if not math.isfinite(parsed) or (parsed <= 0 if positive else parsed < 0):
        raise ValueError("invalid numeric evidence")
    return parsed


def _input_bars(raw_bars, cutoff, timeframe, timestamp_mode):
    """Use the canonical completion clock without silently dropping bad bars."""
    parsed = []
    for raw in raw_bars:
        if isinstance(raw, CompletedBar):
            try:
                opened, closed = _utc(raw.opened_at), _utc(raw.closed_at)
            except (TypeError, ValueError, OverflowError):
                return (), "invalid_bar_timestamp"
            if closed > cutoff:
                continue
            if opened >= closed:
                return (), "invalid_bar_timestamp"
            clock = raw
            values = (raw.open, raw.high, raw.low, raw.close, raw.volume)
        elif isinstance(raw, Mapping):
            # Values on an unfinished candle are not historical evidence. A
            # price-neutral clock probe permits exclusion *before* validation.
            probe = {**raw, "open": 1., "high": 1., "low": 1., "close": 1., "volume": 0.}
            try:
                clocks = normalize_completed_bars([probe], timeframe=timeframe,
                    as_of=cutoff, timestamp_mode=timestamp_mode)
                if not clocks:
                    flag = next((raw[k] for k in ("is_closed", "complete", "completed", "final") if k in raw), None)
                    if flag is not None and str(flag).strip().lower() in {"false", "0", "no", "n", "open"}:
                        continue
                    future = normalize_completed_bars([probe], timeframe=timeframe,
                        as_of=datetime.max.replace(tzinfo=UTC), timestamp_mode=timestamp_mode)
                    if future:
                        continue
                    return (), "invalid_bar_timestamp"
            except (TypeError, ValueError, OverflowError, OSError):
                return (), "invalid_bar_timestamp"
            clock = clocks[0]
            if clock.opened_at >= clock.closed_at:
                return (), "invalid_bar_timestamp"
            values = tuple(raw.get(long, raw.get(short)) for long, short in (
                ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")))
            values += (raw.get("volume", raw.get("v", 0.0)),)
        else:
            return (), "invalid_bar_payload"
        try:
            open_, high, low, close = (_number(value) for value in values[:4])
            volume = _number(values[4], positive=False)
            if high < max(open_, low, close) or low > min(open_, high, close):
                return (), "invalid_bar_geometry"
        except (TypeError, ValueError, OverflowError):
            return (), "invalid_bar_value"
        parsed.append(CompletedBar(clock.opened_at, clock.closed_at, open_, high, low, close, volume))
    by_close = {}
    for bar in parsed:
        prior = by_close.get(bar.closed_at)
        if prior is not None and prior != bar:
            return (), "conflicting_completed_bars"
        by_close[bar.closed_at] = bar
    ordered = tuple(by_close[k] for k in sorted(by_close))
    if any(right.opened_at < left.closed_at for left, right in zip(ordered, ordered[1:])):
        return (), "overlapping_completed_bars"
    return ordered, None


@dataclass(frozen=True)
class _Pivot:
    index: int
    price: float
    kind: str
    observed_at: datetime
    confirmed_at: datetime
    session: str

    def point(self, label: str):
        return {"label": label, "index": self.index, "price": self.price,
                "kind": self.kind, "price_field": "high" if self.kind == "HIGH" else "low",
                "time": int(self.observed_at.timestamp()), "session": self.session,
                "observed_at": _iso(self.observed_at), "confirmed_at": _iso(self.confirmed_at)}


def _atrs(bars):
    ranges, result = [], []
    for i, bar in enumerate(bars):
        previous = bars[i - 1].close if i else bar.close
        ranges.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
        result.append(sum(ranges[max(0, i - 13):i + 1]) / min(i + 1, 14))
    return result


def _pivots(bars, radius):
    """Immutable confirmed fractals: never replace an old same-side event."""
    result, ambiguous = [], []
    for i in range(radius, len(bars) - radius):
        bar = bars[i]
        neighbors = bars[i - radius:i] + bars[i + 1:i + radius + 1]
        high = all(bar.high > other.high for other in neighbors)
        low = all(bar.low < other.low for other in neighbors)
        if high and low:
            ambiguous.append(_iso(bar.closed_at))
            continue  # An outside bar has no observable intrabar order.
        if high or low:
            result.append(_Pivot(i, bar.high if high else bar.low, "HIGH" if high else "LOW",
                                 bar.closed_at, bars[i + radius].closed_at, bar.opened_at.date().isoformat()))
    return result, ambiguous


def _envelopes(points, bars, atrs, minimum_atr):
    """Every interior high/low must fit its alleged end-point extrema."""
    for start, end in zip(points, points[1:]):
        if end.index <= start.index or start.kind == end.kind:
            return False
        span = abs(end.price - start.price)
        if span <= 0 or span < minimum_atr * max(atrs[start.index], atrs[end.index]):
            return False
        floor, ceiling = sorted((start.price, end.price))
        for bar in bars[start.index:end.index + 1]:
            if bar.low < floor or bar.high > ceiling:
                return False
    return True


def _geometry(points, family):
    """Price rules only; never infer 5/3 subdivision from these endpoints."""
    if family not in _LABELS or len(points) != len(_LABELS[family]):
        return None
    sign = 1 if points[0].kind == "LOW" else -1
    expected = ["LOW" if (i % 2 == 0) == (sign == 1) else "HIGH" for i in range(len(points))]
    if [point.kind for point in points] != expected:
        return None
    p = [sign * point.price for point in points]
    if any(not math.isfinite(point.price) or point.price <= 0 for point in points):
        return None
    if any((p[i + 1] - p[i]) * (1 if i % 2 == 0 else -1) <= 0 for i in range(len(p) - 1)):
        return None
    checks = {"alternating_observed_extrema": True}
    ratios = {}
    if family == "impulse":
        lengths = [p[1] - p[0], p[3] - p[2], p[5] - p[4]]
        checks.update(wave2_preserves_origin=p[2] > p[0],
                      wave3_exceeds_wave1=p[3] > p[1],
                      wave3_not_shortest=lengths[1] >= min(lengths[0], lengths[2]),
                      wave4_no_wave1_overlap=p[4] > p[1],
                      wave5_not_truncated=p[5] > p[3])
        ratios = {"wave2_retrace": (p[1] - p[2]) / lengths[0],
                  "wave3_over_wave1": lengths[1] / lengths[0],
                  "wave4_retrace": (p[3] - p[4]) / lengths[1],
                  "wave5_over_wave1": lengths[2] / lengths[0]}
    elif family in {"zigzag", "regular_flat", "expanded_flat"}:
        a = p[1] - p[0]
        b_ratio, c_extension = (p[1] - p[2]) / a, (p[3] - p[1]) / a
        checks.update(c_exceeds_a=p[3] > p[1])
        if family == "zigzag":
            checks.update(b_preserves_origin=p[2] > p[0])
        elif family == "regular_flat":
            # The 90% and extension bounds define this model's deliberately
            # narrow regular-flat subset; they are not universal hard rules.
            checks.update(b_returns_near_origin=PARAMETERS["regular_flat_min_b_retrace"] <= b_ratio <= 1,
                          c_is_regular_subset=0 < c_extension <= PARAMETERS["regular_flat_max_c_extension"])
        else:
            checks.update(b_beyond_origin=p[2] < p[0])
        ratios = {"b_retrace": b_ratio, "c_over_a": (p[3] - p[2]) / a}
    else:
        checks.update(lower_edge_contracts=p[0] < p[2] < p[4],
                      upper_edge_contracts=p[1] > p[3] > p[5],
                      edges_not_crossed=p[4] < p[5])
        # E is conservatively inside the projected A-C side, not a throwover.
        elapsed = points[3].index - points[1].index
        if elapsed <= 0:
            return None
        upper_at_e = p[1] + (p[3] - p[1]) * (points[5].index - points[1].index) / elapsed
        checks.update(e_inside_ac_boundary=p[5] <= upper_at_e,
                      ac_boundary_above_d=upper_at_e > p[4])
        ratios = {"e_over_a": (p[5] - p[4]) / (p[1] - p[0])}
    if not all(checks.values()):
        return None
    return checks, ratios


def _subdivision(start, end, fine_pivots, expected, bars, atrs, ambiguous_times):
    inner = [p for p in fine_pivots if start.index < p.index < end.index]
    # Endpoint evidence keeps the main degree's later, conservative clock.
    points = [start, *inner, end]
    ambiguous_count = sum(start.observed_at <= time <= end.observed_at for time in ambiguous_times)
    valid = not ambiguous_count and len(points) == expected + 1 and _envelopes(points, bars, atrs, PARAMETERS["minimum_subwave_atr"])
    families = ("impulse",) if expected == 5 else ("zigzag", "regular_flat", "expanded_flat")
    family = next((name for name in families if valid and _geometry(points, name)), None)
    labels = _LABELS[family] if family else tuple(f"P{i}" for i in range(len(points)))
    return {"expected_subwaves": expected, "observed_subwaves": len(points) - 1,
            "ambiguous_subwave_bars": ambiguous_count,
            "subdivision_status": "verified" if family else "unverified",
            "subdivision_family": family,
            "subwaves": [point.point(label) for point, label in zip(points, labels)]}


def _pattern_id(family, direction, points):
    evidence = [(p["observed_at"], p["price"], p["kind"]) for p in points]
    return sha256(json.dumps([MODEL, family, direction, evidence], separators=(",", ":")).encode()).hexdigest()[:20]


def _pattern(points, family, radius, fine_pivots, bars, atrs, ambiguous_times):
    geometry = _geometry(points, family)
    if not geometry or not _envelopes(points, bars, atrs, PARAMETERS["minimum_leg_atr"]):
        return None
    checks, ratios = geometry
    labels = _LABELS[family]
    public_points = [point.point(label) for point, label in zip(points, labels)]
    waves = []
    for i, (start, end, expected) in enumerate(zip(points, points[1:], _SUBDIVISIONS[family])):
        waves.append({"label": labels[i + 1], "from": labels[i], "to": labels[i + 1],
                      **_subdivision(start, end, fine_pivots, expected, bars, atrs, ambiguous_times)})
    verified = all(wave["subdivision_status"] == "verified" for wave in waves)
    direction = "LONG" if points[-1].price > points[0].price else "SHORT"
    return {"id": _pattern_id(family, direction, public_points), "family": family,
            "direction": direction, "direction_meaning": "observed_pattern_move_not_trade_direction",
            "degree": "major", "pivot_radius": radius, "timeframe": None,
            "pattern_status": "confirmed" if verified else "geometry_only",
            "subdivision_status": "verified" if verified else "unverified",
            "points": public_points, "waves": waves,
            "rule_checks": [{"rule": rule, "passed": True} for rule in [*checks, "full_segment_envelopes", "minimum_causal_atr_move"]],
            "ratios": ratios, "ratio_role": "descriptive_not_signal_override",
            "invalidation_level": points[0].price, "invalidation_anchor": "0",
            "observed_at": _iso(points[-1].observed_at),
            "confirmed_at": _iso(max(p.confirmed_at for p in points)),
            "bars_since_completed": len(bars) - 1 - points[-1].index,
            "trade_ready": False, "mail_eligible": False, "signal_kind": "pattern_context"}


def analyze_elliott(bars: Sequence[Mapping[str, Any]], *, as_of, timeframe="1D",
                    direction="ALL", timestamp_mode="open") -> dict:
    """Find known completed geometric counts and observed subwave evidence.

    ``direction`` filters the pattern's observed displacement, not an inferred
    position to enter after its end. Alternatives stay alternatives. The newest
    unconfirmed endpoint is never backfilled as a completed wave.
    """
    cutoff = _utc(as_of)
    tf, side = str(timeframe).strip().upper(), str(direction).strip().upper()
    if not re.fullmatch(r"[1-9][0-9]*[MHDW]", tf):
        raise ValueError("explicit supported timeframe required")
    if side not in {"LONG", "SHORT", "ALL"} or timestamp_mode not in {"open", "close"}:
        raise ValueError("invalid direction or timestamp mode")
    result = {"model": MODEL, "parameter_version": PARAMETERS["version"],
              "status": "ok", "reason": None, "as_of": _iso(cutoff), "timeframe": tf,
              "bars_used": 0, "latest_completed_at": None, "patterns": [],
              "parameters": {**PARAMETERS, "pivot_radii": list(PARAMETERS["pivot_radii"])},
              "unsupported_families": list(UNSUPPORTED_FAMILIES),
              "signal_kind": "pattern_context", "trade_ready": False, "mail_eligible": False,
              "price_evidence_only": True, "alternative_count_policy": "bounded_multiple_hypotheses"}
    if bars is None or isinstance(bars, (str, bytes, Mapping)):
        result.update(status="invalid_data", reason="invalid_bars_payload")
        return result
    try:
        raw = tuple(islice(iter(bars), PARAMETERS["maximum_bars"] + 1))
    except TypeError:
        result.update(status="invalid_data", reason="invalid_bars_payload")
        return result
    if len(raw) > PARAMETERS["maximum_bars"]:
        result.update(status="insufficient_data", reason="analysis_window_exceeds_bounded_model")
        return result
    completed, error = _input_bars(raw, cutoff, tf, timestamp_mode)
    if error:
        result.update(status="invalid_data", reason=error)
        return result
    result.update(bars_used=len(completed),
                  latest_completed_at=_iso(completed[-1].closed_at) if completed else None)
    if len(completed) < PARAMETERS["minimum_bars"]:
        result.update(status="insufficient_data", reason="minimum_completed_bars_missing")
        return result
    atrs = _atrs(completed)
    fine, ambiguous_minor = _pivots(completed, PARAMETERS["subdivision_radius"])
    ambiguous_times = tuple(_utc(time) for time in ambiguous_minor)
    patterns, evidence = {}, []
    for radius in PARAMETERS["pivot_radii"]:
        pivots, ambiguous = _pivots(completed, radius)
        evidence.append({"radius": radius, "confirmed_pivots": len(pivots),
                         "ambiguous_outside_bars": len(ambiguous)})
        for family, labels in _LABELS.items():
            for offset in range(len(pivots) - len(labels) + 1):
                points = pivots[offset:offset + len(labels)]
                if len(completed) - 1 - points[-1].index > PARAMETERS["maximum_bars_since_pattern"]:
                    continue
                item = _pattern(points, family, radius, fine, completed, atrs, ambiguous_times)
                if item and (side == "ALL" or item["direction"] == side):
                    item["timeframe"] = tf
                    # Duplicate geometries at another radius are not another
                    # alternative count. Keep first/earliest confirmation.
                    patterns.setdefault(item["id"], item)
    ordered = sorted(patterns.values(), key=lambda p: (p["observed_at"],
        p["subdivision_status"] == "verified", p["family"], p["id"]), reverse=True)
    result.update(patterns=ordered[:PARAMETERS["maximum_patterns"]],
                  matched_patterns=len(ordered), patterns_truncated=len(ordered) > PARAMETERS["maximum_patterns"],
                  pivot_diagnostics=evidence)
    return result


def _validate_point(point, cutoff):
    if not isinstance(point, Mapping):
        raise ValueError
    price = _number(point["price"])
    observed, confirmed = _utc(point["observed_at"]), _utc(point["confirmed_at"])
    if not observed < confirmed <= cutoff or point["kind"] not in {"HIGH", "LOW"}:
        raise ValueError
    if point["price_field"] != ("high" if point["kind"] == "HIGH" else "low"):
        raise ValueError
    if isinstance(point["time"], bool) or point["time"] != int(observed.timestamp()):
        raise ValueError
    index = point["index"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError
    session = point["session"]
    if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
        raise ValueError
    return _Pivot(index, price, point["kind"], observed, confirmed, session)


def validate_elliott_report(report, *, timeframe="1D", as_of=None) -> bool:
    """Validate a stored report's internal contract; not a price-feed replay.

    This prevents malformed/stale-schema caches or accidental trade promotion.
    Known observations and radius-based confirmations must share one consistent
    bar-index clock across both degrees and all alternative counts.
    Freshness relative to a market session is the caller's separate contract.
    Without original OHLCV this is not proof of real feed correspondence.
    """
    try:
        if not isinstance(report, Mapping) or report.get("model") != MODEL or report.get("parameter_version") != PARAMETERS["version"]:
            return False
        if report.get("parameters") != PARAMETERS or report.get("unsupported_families") != UNSUPPORTED_FAMILIES:
            return False
        cutoff = _utc(report["as_of"])
        if as_of is not None and cutoff > _utc(as_of):
            return False
        if report.get("timeframe") != timeframe or report.get("status") != "ok":
            return False
        if report.get("signal_kind") != "pattern_context" or report.get("trade_ready") is not False or report.get("mail_eligible") is not False:
            return False
        count = report["bars_used"]
        if isinstance(count, bool) or not isinstance(count, int) or not PARAMETERS["minimum_bars"] <= count <= PARAMETERS["maximum_bars"]:
            return False
        latest = _utc(report["latest_completed_at"])
        if latest > cutoff:
            return False
        patterns = report["patterns"]
        if not isinstance(patterns, list) or len(patterns) > PARAMETERS["maximum_patterns"]:
            return False
        ids = set()
        known_clocks = {(count - 1, latest)}
        for pattern in patterns:
            family = pattern["family"]
            labels = _LABELS[family]
            if pattern.get("signal_kind") != "pattern_context" or pattern.get("trade_ready") is not False or pattern.get("mail_eligible") is not False:
                return False
            if pattern.get("timeframe") != timeframe or pattern.get("pivot_radius") not in PARAMETERS["pivot_radii"]:
                return False
            if pattern.get("degree") != "major" or pattern.get("direction_meaning") != "observed_pattern_move_not_trade_direction":
                return False
            points = pattern["points"]
            if not isinstance(points, list) or [p["label"] for p in points] != list(labels):
                return False
            pivots = [_validate_point(p, latest) for p in points]
            if any(b.index <= a.index or b.observed_at <= a.observed_at for a, b in zip(pivots, pivots[1:])):
                return False
            geometry = _geometry(pivots, family)
            radius = pattern["pivot_radius"]
            if not geometry or any(p.index < radius or p.index + radius >= count for p in pivots):
                return False
            for pivot in pivots:
                known_clocks.add((pivot.index, pivot.observed_at))
                known_clocks.add((pivot.index + radius, pivot.confirmed_at))
            age = pattern.get("bars_since_completed")
            if (isinstance(age, bool) or not isinstance(age, int)
                    or age != count - 1 - pivots[-1].index
                    or age > PARAMETERS["maximum_bars_since_pattern"]):
                return False
            checks, ratios = geometry
            expected_checks = [{"rule": rule, "passed": True} for rule in
                               [*checks, "full_segment_envelopes", "minimum_causal_atr_move"]]
            if pattern.get("rule_checks") != expected_checks or any(
                check.get("passed") is not True for check in pattern["rule_checks"]
            ):
                return False
            if pattern.get("ratio_role") != "descriptive_not_signal_override" or pattern.get("ratios") != ratios:
                return False
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                   for value in pattern["ratios"].values()):
                return False
            direction = "LONG" if pivots[-1].price > pivots[0].price else "SHORT"
            if pattern["direction"] != direction or pattern["id"] != _pattern_id(family, direction, points) or pattern["id"] in ids:
                return False
            ids.add(pattern["id"])
            if _number(pattern["invalidation_level"]) != pivots[0].price or pattern.get("invalidation_anchor") != "0":
                return False
            pattern_confirmed = _utc(pattern["confirmed_at"])
            if _utc(pattern["observed_at"]) != pivots[-1].observed_at or pattern_confirmed != max(p.confirmed_at for p in pivots):
                return False
            waves = pattern["waves"]
            if not isinstance(waves, list) or len(waves) != len(labels) - 1:
                return False
            verified = []
            for i, (wave, expected) in enumerate(zip(waves, _SUBDIVISIONS[family])):
                if wave["label"] != labels[i + 1] or wave["from"] != labels[i] or wave["to"] != labels[i + 1] or wave["expected_subwaves"] != expected or isinstance(wave["expected_subwaves"], bool):
                    return False
                sub = wave["subwaves"]
                if not isinstance(sub, list) or not 2 <= len(sub) <= PARAMETERS["maximum_bars"]:
                    return False
                nested = [_validate_point(p, latest) for p in sub]
                if any(p.confirmed_at > pattern_confirmed for p in nested):
                    return False
                if nested[0] != pivots[i] or nested[-1] != pivots[i + 1] or any(b.index <= a.index or b.observed_at <= a.observed_at for a, b in zip(nested, nested[1:])):
                    return False
                # Shared endpoints retain their main-degree confirmation;
                # only independently observed interior turns use minor radius.
                for pivot in nested[1:-1]:
                    known_clocks.add((pivot.index, pivot.observed_at))
                    known_clocks.add((pivot.index + PARAMETERS["subdivision_radius"], pivot.confirmed_at))
                if wave["observed_subwaves"] != len(nested) - 1 or isinstance(wave["observed_subwaves"], bool):
                    return False
                valid_families = ("impulse",) if expected == 5 else ("zigzag", "regular_flat", "expanded_flat")
                matches = len(nested) == expected + 1 and wave["subdivision_family"] in valid_families and bool(_geometry(nested, wave["subdivision_family"]))
                ambiguous = wave["ambiguous_subwave_bars"]
                if isinstance(ambiguous, bool) or not isinstance(ambiguous, int) or not 0 <= ambiguous <= nested[-1].index - nested[0].index + 1:
                    return False
                if wave["subdivision_status"] not in {"verified", "unverified"} or (wave["subdivision_status"] == "verified" and not matches):
                    return False
                if wave["subdivision_status"] == "verified":
                    if ambiguous or [p["label"] for p in sub] != list(_LABELS[wave["subdivision_family"]]):
                        return False
                elif wave["subdivision_family"] is not None or [p["label"] for p in sub] != [f"P{j}" for j in range(len(sub))]:
                    return False
                verified.append(wave["subdivision_status"] == "verified")
            if pattern["subdivision_status"] != ("verified" if all(verified) else "unverified") or pattern["pattern_status"] != ("confirmed" if all(verified) else "geometry_only"):
                return False
        # Identical duplicated evidence is harmless. A bar cannot have two
        # close times, nor may a later index close before/equal an earlier one.
        # No calendar spacing is inferred: gaps and different sessions are OK.
        ordered_clocks = sorted(known_clocks)
        return all(a_index < b_index and a_time < b_time
                   for (a_index, a_time), (b_index, b_time)
                   in zip(ordered_clocks, ordered_clocks[1:]))
    except (TypeError, ValueError, KeyError, OverflowError, AttributeError):
        return False


__all__ = ["MODEL", "PARAMETERS", "analyze_elliott", "validate_elliott_report"]
