"""Causal Fibonacci projections based on confirmed chronological swing legs.

Fibonacci is intentionally projection-only.  This module cannot create an
invalidation or a blocking barrier without independent structural evidence.
The legacy adapter mirrors the chart payload shape currently used by the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from modules.level_zones import (
    LevelEvidence,
    _coerce_datetime,
    _iso,
    _json_value,
    _normalized_timeframe,
    confirmed_pivot_evidence,
)


DEFAULT_RETRACEMENTS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
DEFAULT_EXTENSIONS = (1.272, 1.618, 2.0)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _normal_direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in ("LONG", "BUY", "BULL", "UP"):
        return "LONG"
    if text in ("SHORT", "SELL", "BEAR", "DOWN"):
        return "SHORT"
    raise ValueError("direction must be LONG or SHORT")


def _round_level_price(price: float) -> float:
    """Match the existing chart precision without collapsing small crypto."""
    value = float(price)
    if abs(value) >= 10:
        return round(value, 2)
    if abs(value) >= 1:
        return round(value, 3)
    if abs(value) >= 0.01:
        return round(value, 5)
    # Keep the same micro-price precision as executable trade levels. A fixed
    # eight-decimal format turns valid sub-cent token projections into zero.
    return float(f"{value:.6g}")


def _ratio_label(ratio: float) -> str:
    """Use the integer labels of the existing directional Fib payload."""
    conventional = {
        0.0: "0%", 0.236: "23%", 0.382: "38%", 0.5: "50%",
        0.618: "61%", 0.786: "78%", 1.0: "100%",
        1.272: "127%", 1.618: "161%", 2.0: "200%",
    }
    for canonical, label in conventional.items():
        if math.isclose(float(ratio), canonical, rel_tol=0.0, abs_tol=1e-12):
            return label
    percentage = format(float(ratio) * 100.0, ".10g")
    return f"{percentage}%"


@dataclass(frozen=True)
class ConfirmedSwingLeg:
    leg_id: str
    direction: str
    start_price: float
    start_at: datetime
    end_price: float
    end_at: datetime
    confirmed_at: datetime
    data_cutoff_at: datetime
    timeframe: str
    start_pivot_index: int
    end_pivot_index: int
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        direction = _normal_direction(self.direction)
        start = _safe_float(self.start_price)
        end = _safe_float(self.end_price)
        start_at = _coerce_datetime(self.start_at)
        end_at = _coerce_datetime(self.end_at)
        confirmed_at = _coerce_datetime(self.confirmed_at)
        cutoff = _coerce_datetime(self.data_cutoff_at)
        if start is None or end is None or start <= 0 or end <= 0:
            raise ValueError("swing prices must be positive")
        if start_at >= end_at:
            raise ValueError("swing start must occur before swing end")
        if confirmed_at < end_at or confirmed_at > cutoff:
            raise ValueError("swing confirmation must be after its end and before cutoff")
        if direction == "LONG" and end <= start:
            raise ValueError("LONG swing must move from a lower low to a higher high")
        if direction == "SHORT" and end >= start:
            raise ValueError("SHORT swing must move from a higher high to a lower low")
        if int(self.start_pivot_index) >= int(self.end_pivot_index):
            raise ValueError("swing pivot indices must be chronological")
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "start_price", start)
        object.__setattr__(self, "end_price", end)
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)
        object.__setattr__(self, "confirmed_at", confirmed_at)
        object.__setattr__(self, "data_cutoff_at", cutoff)
        object.__setattr__(self, "timeframe", _normalized_timeframe(self.timeframe))
        object.__setattr__(self, "start_pivot_index", int(self.start_pivot_index))
        object.__setattr__(self, "end_pivot_index", int(self.end_pivot_index))
        provenance = dict(self.provenance or {})
        try:
            json.dumps(_json_value(provenance), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("swing provenance must be JSON-serialisable") from exc
        object.__setattr__(self, "provenance", provenance)

    @property
    def magnitude(self) -> float:
        return abs(self.end_price - self.start_price)

    @property
    def anchor_high(self) -> float:
        return max(self.start_price, self.end_price)

    @property
    def anchor_low(self) -> float:
        return min(self.start_price, self.end_price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg_id": self.leg_id,
            "direction": self.direction,
            "start_price": self.start_price,
            "start_at": _iso(self.start_at),
            "end_price": self.end_price,
            "end_at": _iso(self.end_at),
            "confirmed_at": _iso(self.confirmed_at),
            "data_cutoff_at": _iso(self.data_cutoff_at),
            "timeframe": self.timeframe,
            "start_pivot_index": self.start_pivot_index,
            "end_pivot_index": self.end_pivot_index,
            "magnitude": self.magnitude,
            "anchor_high": self.anchor_high,
            "anchor_low": self.anchor_low,
            "provenance": _json_value({
                str(key): self.provenance[key] for key in sorted(self.provenance, key=str)
            }),
        }


def select_confirmed_swing_leg(
    bars: Sequence[Mapping[str, Any]],
    *,
    as_of: Any,
    direction: str,
    timeframe: str,
    pivot_left: int = 2,
    pivot_right: int = 2,
    minimum_move_atr: float = 0.0,
    atr: Any = None,
    timestamp_mode: str = "open",
) -> Optional[ConfirmedSwingLeg]:
    """Choose the latest confirmed, chronological swing leg without lookahead.

    LONG requires a confirmed swing low before a confirmed swing high. SHORT
    requires a confirmed swing high before a confirmed swing low. Independent
    period extrema in the wrong temporal order are therefore never accepted.
    """
    side = _normal_direction(direction)
    cutoff = _coerce_datetime(as_of)
    pivots = confirmed_pivot_evidence(
        bars,
        timeframe=timeframe,
        as_of=cutoff,
        pivot_left=pivot_left,
        pivot_right=pivot_right,
        timestamp_mode=timestamp_mode,
    )
    if side == "LONG":
        start_name = "confirmed_swing_low"
        end_name = "confirmed_swing_high"
    else:
        start_name = "confirmed_swing_high"
        end_name = "confirmed_swing_low"
    starts = tuple(item for item in pivots if item.source_name == start_name)
    ends = tuple(item for item in pivots if item.source_name == end_name)
    atr_value = max(0.0, _safe_float(atr, 0.0) or 0.0)
    minimum_multiple = max(0.0, _safe_float(minimum_move_atr, 0.0) or 0.0)
    minimum_move = atr_value * minimum_multiple

    for end in sorted(ends, key=lambda item: (
        int(item.provenance["pivot_index"]), item.confirmed_at
    ), reverse=True):
        end_index = int(end.provenance["pivot_index"])
        eligible_starts = sorted(
            (
                item for item in starts
                if int(item.provenance["pivot_index"]) < end_index
                and item.observed_at < end.observed_at
            ),
            key=lambda item: (int(item.provenance["pivot_index"]), item.confirmed_at),
            reverse=True,
        )
        for start in eligible_starts:
            move = (
                end.midpoint - start.midpoint
                if side == "LONG"
                else start.midpoint - end.midpoint
            )
            if move <= 0 or move + 1e-12 < minimum_move:
                continue
            start_index = int(start.provenance["pivot_index"])
            confirmation = max(start.confirmed_at, end.confirmed_at)
            identity = "|".join((
                side,
                str(timeframe).upper(),
                str(start_index),
                format(start.midpoint, ".12g"),
                _iso(start.observed_at),
                str(end_index),
                format(end.midpoint, ".12g"),
                _iso(end.observed_at),
                _iso(confirmation),
            ))
            leg_id = "fib_leg_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            return ConfirmedSwingLeg(
                leg_id=leg_id,
                direction=side,
                start_price=start.midpoint,
                start_at=start.observed_at,
                end_price=end.midpoint,
                end_at=end.observed_at,
                confirmed_at=confirmation,
                data_cutoff_at=cutoff,
                timeframe=timeframe,
                start_pivot_index=start_index,
                end_pivot_index=end_index,
                provenance={
                    "model": "confirmed_chronological_pivots_v1",
                    "pivot_left": max(1, int(pivot_left)),
                    "pivot_right": max(1, int(pivot_right)),
                    "minimum_move_atr": minimum_multiple,
                    "atr": atr_value,
                    "start_source": start.source_name,
                    "end_source": end.source_name,
                },
            )
    return None


def _normalized_ratios(values: Iterable[float], *, extension: bool) -> Tuple[float, ...]:
    parsed = set()
    for value in values:
        ratio = _safe_float(value)
        if ratio is None:
            raise ValueError("Fibonacci ratios must be finite numbers")
        if extension and ratio <= 1.0:
            raise ValueError("extension ratios must be greater than 1")
        if not extension and not 0.0 <= ratio <= 1.0:
            raise ValueError("retracement ratios must be between 0 and 1")
        parsed.add(ratio)
    return tuple(sorted(parsed))


def project_fibonacci(
    leg: ConfirmedSwingLeg,
    *,
    retracements: Iterable[float] = DEFAULT_RETRACEMENTS,
    extensions: Iterable[float] = DEFAULT_EXTENSIONS,
) -> Tuple[LevelEvidence, ...]:
    """Project deterministic Fib levels as non-structural LevelEvidence."""
    retracement_ratios = _normalized_ratios(retracements, extension=False)
    extension_ratios = _normalized_ratios(extensions, extension=True)
    levels = []
    for kind, ratios in (("retracement", retracement_ratios), ("extension", extension_ratios)):
        for ratio in ratios:
            if leg.direction == "LONG":
                if kind == "retracement":
                    price = leg.end_price - leg.magnitude * ratio
                else:
                    price = leg.start_price + leg.magnitude * ratio
            else:
                if kind == "retracement":
                    price = leg.end_price + leg.magnitude * ratio
                else:
                    price = leg.start_price - leg.magnitude * ratio
            if price <= 0:
                # Extremely large short extensions can cross zero. They are
                # not valid market levels and must not be silently clamped.
                continue
            label = _ratio_label(ratio)
            levels.append(LevelEvidence(
                source_family="fibonacci",
                source_name=f"FIB {label}",
                timeframe=leg.timeframe,
                lower=price,
                upper=price,
                observed_at=leg.end_at,
                confirmed_at=leg.confirmed_at,
                data_cutoff_at=leg.data_cutoff_at,
                strength=0.5,
                projection_only=True,
                provenance={
                    "leg_id": leg.leg_id,
                    "independence_key": f"fibonacci:{leg.leg_id}",
                    "ratio": ratio,
                    "kind": kind,
                    "direction": leg.direction,
                    "anchor_start": leg.start_price,
                    "anchor_end": leg.end_price,
                },
            ))
    return tuple(sorted(levels, key=lambda item: (
        float(item.provenance["ratio"]), item.source_name
    )))


def fibonacci_payload_adapter(
    leg: ConfirmedSwingLeg,
    *,
    lookback_bars: Optional[int] = None,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the existing ``levels``/``meta`` chart contract from one leg."""
    evidence = project_fibonacci(leg)
    levels = {
        _ratio_label(float(item.provenance["ratio"])): _round_level_price(item.reference if hasattr(item, "reference") else item.midpoint)
        for item in evidence
    }
    # Comprehension order follows sorted ratios from project_fibonacci.
    meta: Dict[str, Any] = {
        "direction": leg.direction.lower(),
        "timeframe": leg.timeframe,
        "lookback_bars": int(lookback_bars) if lookback_bars is not None else None,
        "anchor_high": _round_level_price(leg.anchor_high),
        "anchor_low": _round_level_price(leg.anchor_low),
        "model": "confirmed_directional_retracement_v3",
        "basis": basis or "confirmed_chronological_swing_leg",
        "leg_id": leg.leg_id,
        "confirmed_at": _iso(leg.confirmed_at),
        "projection_only": True,
    }
    return {"levels": levels, "meta": meta}


__all__ = [
    "ConfirmedSwingLeg",
    "DEFAULT_EXTENSIONS",
    "DEFAULT_RETRACEMENTS",
    "fibonacci_payload_adapter",
    "project_fibonacci",
    "select_confirmed_swing_leg",
]
