"""Causal, dependency-light market-structure level zones.

The module deliberately separates *evidence* from trade decisions.  A level
cannot exist before all bars needed to confirm it have closed, and every
public result has a deterministic, JSON-serialisable representation.  It is
safe to use from scanners, API handlers and offline shadow evaluation without
pulling in pandas or a market-data client.

Fibonacci evidence may be attached to a zone, but evidence marked
``projection_only`` never becomes a blocking barrier by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


UTC = timezone.utc
_CLOSE_TIME_KEYS = ("close_time", "close_timestamp", "end_time", "end", "T")
_OPEN_TIME_KEYS = ("open_time", "timestamp", "time", "ts", "t")


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _coerce_datetime(value: Any) -> datetime:
    """Return an aware UTC datetime from ISO text, datetime or epoch values."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        magnitude = abs(number)
        if magnitude >= 1e18:  # nanoseconds
            number /= 1_000_000_000.0
        elif magnitude >= 1e15:  # microseconds
            number /= 1_000_000.0
        elif magnitude >= 1e12:  # milliseconds
            number /= 1_000.0
        parsed = datetime.fromtimestamp(number, tz=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty timestamp")
        try:
            return _coerce_datetime(float(text))
        except ValueError:
            pass
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError(f"unsupported timestamp: {value!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _timeframe_seconds(timeframe: Any) -> Optional[int]:
    text = str(timeframe or "").strip().upper().replace(" ", "")
    aliases = {
        "D": "1D",
        "DAY": "1D",
        "DAILY": "1D",
        "W": "1W",
        "WEEK": "1W",
        "WEEKLY": "1W",
        "H": "1H",
    }
    text = aliases.get(text, text)
    if len(text) < 2:
        return None
    unit = text[-1]
    try:
        amount = int(text[:-1])
    except ValueError:
        return None
    if amount <= 0:
        return None
    multiplier = {"S": 1, "M": 60, "H": 3600, "D": 86400, "W": 604800}.get(unit)
    return amount * multiplier if multiplier else None


def _normalized_timeframe(timeframe: Any) -> str:
    text = str(timeframe or "").strip().upper().replace(" ", "")
    return {
        "D": "1D",
        "DAY": "1D",
        "DAILY": "1D",
        "W": "1W",
        "WEEK": "1W",
        "WEEKLY": "1W",
        "H": "1H",
    }.get(text, text or "UNKNOWN")


@dataclass(frozen=True)
class CompletedBar:
    opened_at: datetime
    closed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    source_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opened_at": _iso(self.opened_at),
            "closed_at": _iso(self.closed_at),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source_index": self.source_index,
        }


@dataclass(frozen=True)
class LevelEvidence:
    source_family: str
    source_name: str
    timeframe: str
    lower: float
    upper: float
    observed_at: datetime
    confirmed_at: datetime
    data_cutoff_at: datetime
    strength: float = 1.0
    provenance: Mapping[str, Any] = None
    projection_only: bool = False

    def __post_init__(self) -> None:
        lower = _safe_float(self.lower)
        upper = _safe_float(self.upper)
        strength = _safe_float(self.strength, 0.0)
        if lower is None or upper is None or lower <= 0 or upper < lower:
            raise ValueError("level evidence requires 0 < lower <= upper")
        if strength is None or strength < 0:
            raise ValueError("level evidence strength must be non-negative")
        observed = _coerce_datetime(self.observed_at)
        confirmed = _coerce_datetime(self.confirmed_at)
        cutoff = _coerce_datetime(self.data_cutoff_at)
        if confirmed < observed:
            raise ValueError("level evidence cannot be confirmed before it was observed")
        if observed > cutoff or confirmed > cutoff:
            raise ValueError("level evidence cannot be observed or confirmed after its cutoff")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "confirmed_at", confirmed)
        object.__setattr__(self, "data_cutoff_at", cutoff)
        object.__setattr__(self, "source_family", str(self.source_family or "unknown").strip().lower())
        object.__setattr__(self, "source_name", str(self.source_name or "unknown").strip())
        object.__setattr__(self, "timeframe", _normalized_timeframe(self.timeframe))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0

    @property
    def independence_key(self) -> str:
        explicit = str(self.provenance.get("independence_key") or "").strip()
        return explicit or f"{self.source_family}:{self.timeframe}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_family": self.source_family,
            "source_name": self.source_name,
            "timeframe": self.timeframe,
            "lower": self.lower,
            "upper": self.upper,
            "observed_at": _iso(self.observed_at),
            "confirmed_at": _iso(self.confirmed_at),
            "data_cutoff_at": _iso(self.data_cutoff_at),
            "strength": self.strength,
            "projection_only": bool(self.projection_only),
            "independence_key": self.independence_key,
            "provenance": _json_value(self.provenance),
        }


@dataclass(frozen=True)
class LevelZone:
    zone_id: str
    lower: float
    upper: float
    reference: float
    side_at_reference: str
    evidence: Tuple[LevelEvidence, ...]
    independent_sources: int
    independent_structural_sources: int
    touch_count: int
    confirmed_at: datetime
    break_state: str
    strength: float
    origin_roles: Tuple[str, ...] = ()
    break_reclaim_evidence: Optional["BreakReclaimEvidence"] = None
    quality_flags: Tuple[str, ...] = ()

    @property
    def projection_only(self) -> bool:
        return self.independent_structural_sources == 0

    @property
    def source_names(self) -> Tuple[str, ...]:
        return tuple(sorted({item.source_name for item in self.evidence}))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "lower": self.lower,
            "upper": self.upper,
            "reference": self.reference,
            "side_at_reference": self.side_at_reference,
            "sources": list(self.source_names),
            "independent_sources": self.independent_sources,
            "independent_structural_sources": self.independent_structural_sources,
            "touch_count": self.touch_count,
            "confirmed_at": _iso(self.confirmed_at),
            "break_state": self.break_state,
            "strength": self.strength,
            "origin_roles": list(self.origin_roles),
            "break_reclaim_evidence": (
                self.break_reclaim_evidence.to_dict()
                if self.break_reclaim_evidence is not None
                else None
            ),
            "projection_only": self.projection_only,
            "quality_flags": list(self.quality_flags),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class StructureSnapshot:
    symbol: str
    asset_class: str
    horizon: str
    as_of: datetime
    current_price: float
    zones: Tuple[LevelZone, ...]
    atr_by_timeframe: Mapping[str, float]
    completed_bar_counts: Mapping[str, int]
    quality_flags: Tuple[str, ...] = ()
    model: str = "causal_level_zones_v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "horizon": self.horizon,
            "as_of": _iso(self.as_of),
            "current_price": self.current_price,
            "zones": [zone.to_dict() for zone in self.zones],
            "atr_by_timeframe": {
                key: self.atr_by_timeframe[key] for key in sorted(self.atr_by_timeframe)
            },
            "completed_bar_counts": {
                key: self.completed_bar_counts[key] for key in sorted(self.completed_bar_counts)
            },
            "quality_flags": list(self.quality_flags),
        }


@dataclass(frozen=True)
class DirectionalStructure:
    snapshot: StructureSnapshot
    entry: float
    direction: str
    supports: Tuple[LevelZone, ...]
    resistances: Tuple[LevelZone, ...]
    overlapping: Tuple[LevelZone, ...]
    opposing_barriers: Tuple[LevelZone, ...]
    invalidation_candidates: Tuple[LevelZone, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": "directional_level_zones_v1",
            "entry": self.entry,
            "direction": self.direction,
            "as_of": _iso(self.snapshot.as_of),
            "supports": [zone.to_dict() for zone in self.supports],
            "resistances": [zone.to_dict() for zone in self.resistances],
            "overlapping": [zone.to_dict() for zone in self.overlapping],
            "opposing_barriers": [zone.to_dict() for zone in self.opposing_barriers],
            "invalidation_candidates": [zone.to_dict() for zone in self.invalidation_candidates],
        }


@dataclass(frozen=True)
class StructureDecision:
    status: str
    reason: str
    direction: str
    entry: float
    stop: float
    risk: Optional[float]
    nearest_barrier: Optional[LevelZone]
    barrier_distance: Optional[float]
    barrier_r: Optional[float]
    target1: Optional[float]
    barrier_gate: Optional[str]
    model: str = "structure_decision_v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "status": self.status,
            "reason": self.reason,
            "direction": self.direction,
            "entry": self.entry,
            "stop": self.stop,
            "risk": self.risk,
            "nearest_barrier": self.nearest_barrier.to_dict() if self.nearest_barrier else None,
            "barrier_distance": self.barrier_distance,
            "barrier_r": self.barrier_r,
            "target1": self.target1,
            "barrier_gate": self.barrier_gate,
        }


@dataclass(frozen=True)
class BreakReclaimEvidence:
    state: str
    reason: str
    direction: str
    zone_id: str
    boundary: float
    zone_confirmed_at: datetime
    timeframe: str
    as_of: datetime
    break_closed_at: Optional[datetime]
    last_completed_at: Optional[datetime]
    last_completed_close: Optional[float]
    hold_bars_required: int
    hold_bars_observed: int
    retest_required: bool
    retest_observed: bool
    completed_bars_used: int

    def __post_init__(self) -> None:
        state = str(self.state or "").strip().upper()
        direction = str(self.direction or "").strip().upper()
        if direction not in ("LONG", "SHORT"):
            raise ValueError("break/reclaim direction must be LONG or SHORT")
        boundary = _safe_float(self.boundary)
        if boundary is None or boundary <= 0:
            raise ValueError("break/reclaim boundary must be positive")
        zone_confirmed_at = _coerce_datetime(self.zone_confirmed_at)
        as_of = _coerce_datetime(self.as_of)
        break_closed_at = (
            _coerce_datetime(self.break_closed_at) if self.break_closed_at is not None else None
        )
        last_completed_at = (
            _coerce_datetime(self.last_completed_at) if self.last_completed_at is not None else None
        )
        last_completed_close = _safe_float(self.last_completed_close)
        required = int(self.hold_bars_required)
        observed = int(self.hold_bars_observed)
        completed = int(self.completed_bars_used)

        if zone_confirmed_at > as_of:
            raise ValueError("zone confirmation cannot be after break/reclaim as_of")
        if break_closed_at is not None and not zone_confirmed_at < break_closed_at <= as_of:
            raise ValueError("break close must occur after zone confirmation and by as_of")
        if last_completed_at is not None and not zone_confirmed_at < last_completed_at <= as_of:
            raise ValueError("last completed bar must occur after zone confirmation and by as_of")
        if break_closed_at is not None and (
            last_completed_at is None or break_closed_at > last_completed_at
        ):
            raise ValueError("active break close cannot be after the last completed bar")
        if (last_completed_at is None) != (last_completed_close is None):
            raise ValueError("last completed timestamp and close must be provided together")
        if last_completed_close is not None and last_completed_close <= 0:
            raise ValueError("last completed close must be positive")
        if completed < 0 or required < 0 or observed < 0:
            raise ValueError("break/reclaim bar counts must be non-negative")
        if completed == 0 and last_completed_at is not None:
            raise ValueError("empty break/reclaim evidence cannot have a last completed bar")
        if completed > 0 and last_completed_at is None:
            raise ValueError("completed break/reclaim evidence requires its latest bar")
        if observed > max(0, completed - 1):
            raise ValueError("observed hold bars exceed completed bars after the break")

        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "zone_id", str(self.zone_id or "").strip())
        object.__setattr__(self, "boundary", boundary)
        object.__setattr__(self, "zone_confirmed_at", zone_confirmed_at)
        object.__setattr__(self, "timeframe", _normalized_timeframe(self.timeframe))
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "break_closed_at", break_closed_at)
        object.__setattr__(self, "last_completed_at", last_completed_at)
        object.__setattr__(self, "last_completed_close", last_completed_close)
        object.__setattr__(self, "hold_bars_required", required)
        object.__setattr__(self, "hold_bars_observed", observed)
        object.__setattr__(self, "completed_bars_used", completed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": "break_reclaim_close_hold_v1",
            "state": self.state,
            "reason": self.reason,
            "direction": self.direction,
            "zone_id": self.zone_id,
            "boundary": self.boundary,
            "zone_confirmed_at": _iso(self.zone_confirmed_at),
            "timeframe": self.timeframe,
            "as_of": _iso(self.as_of),
            "break_closed_at": _iso(self.break_closed_at) if self.break_closed_at else None,
            "last_completed_at": _iso(self.last_completed_at) if self.last_completed_at else None,
            "last_completed_close": self.last_completed_close,
            "hold_bars_required": self.hold_bars_required,
            "hold_bars_observed": self.hold_bars_observed,
            "retest_required": self.retest_required,
            "retest_observed": self.retest_observed,
            "completed_bars_used": self.completed_bars_used,
        }


def normalize_completed_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    as_of: Any,
    timestamp_mode: str = "open",
) -> Tuple[CompletedBar, ...]:
    """Normalize and return only causally completed OHLCV bars.

    If no explicit close timestamp is supplied, the regular timestamp is
    treated as the bar *open* by default and the timeframe duration is added.
    A source whose timestamps denote closes must explicitly pass
    ``timestamp_mode="close"``. Bars without a verifiable timestamp are
    ignored rather than guessed into the historical snapshot.
    """
    cutoff = _coerce_datetime(as_of)
    duration_seconds = _timeframe_seconds(timeframe)
    mode = str(timestamp_mode or "open").strip().lower()
    if mode not in ("open", "close"):
        raise ValueError("timestamp_mode must be 'open' or 'close'")

    parsed: List[CompletedBar] = []
    for source_index, raw in enumerate(bars or ()):
        if isinstance(raw, CompletedBar):
            if raw.closed_at <= cutoff:
                parsed.append(raw)
            continue
        if not isinstance(raw, Mapping):
            continue
        completion_flag = next(
            (raw[key] for key in ("is_closed", "complete", "completed", "final") if key in raw),
            None,
        )
        if completion_flag is not None and str(completion_flag).strip().lower() in (
            "false", "0", "no", "n", "open",
        ):
            continue

        explicit_close = next((raw[key] for key in _CLOSE_TIME_KEYS if raw.get(key) is not None), None)
        regular_time = next((raw[key] for key in _OPEN_TIME_KEYS if raw.get(key) is not None), None)
        try:
            if explicit_close is not None:
                closed_at = _coerce_datetime(explicit_close)
                if regular_time is not None:
                    opened_at = _coerce_datetime(regular_time)
                elif duration_seconds:
                    opened_at = closed_at - timedelta(seconds=duration_seconds)
                else:
                    opened_at = closed_at
            elif regular_time is not None:
                timestamp = _coerce_datetime(regular_time)
                if mode == "close":
                    closed_at = timestamp
                    opened_at = timestamp - timedelta(seconds=duration_seconds or 0)
                else:
                    if not duration_seconds:
                        continue
                    opened_at = timestamp
                    closed_at = timestamp + timedelta(seconds=duration_seconds)
            else:
                continue
        except (OverflowError, OSError, ValueError):
            continue
        if closed_at > cutoff or opened_at > closed_at:
            continue

        close = _safe_float(raw.get("close", raw.get("c")))
        open_ = _safe_float(raw.get("open", raw.get("o", close)), close)
        high = _safe_float(raw.get("high", raw.get("h")))
        low = _safe_float(raw.get("low", raw.get("l")))
        volume = max(0.0, _safe_float(raw.get("volume", raw.get("v", 0.0)), 0.0) or 0.0)
        if close is None or open_ is None or high is None or low is None:
            continue
        if min(open_, high, low, close) <= 0 or high < max(open_, close, low) or low > min(open_, close, high):
            continue
        parsed.append(CompletedBar(
            opened_at=opened_at,
            closed_at=closed_at,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            source_index=source_index,
        ))
    parsed.sort(key=lambda bar: (
        bar.closed_at, bar.opened_at, bar.open, bar.high, bar.low, bar.close, bar.volume
    ))
    grouped_by_close: Dict[datetime, List[CompletedBar]] = {}
    for bar in parsed:
        grouped_by_close.setdefault(bar.closed_at, []).append(bar)

    deduplicated: List[CompletedBar] = []
    for closed_at in sorted(grouped_by_close):
        candidates = grouped_by_close[closed_at]
        signatures = {
            (bar.opened_at, bar.open, bar.high, bar.low, bar.close, bar.volume)
            for bar in candidates
        }
        if len(signatures) != 1:
            # Conflicting candles for one close instant have no safe ordering
            # or authoritative winner. Dropping that instant is fail-closed;
            # critically, two copies can never masquerade as a breakout bar
            # and its subsequent hold confirmation.
            continue
        deduplicated.append(candidates[0])
    return tuple(
        replace(bar, source_index=index) for index, bar in enumerate(deduplicated)
    )


def confirmed_pivot_evidence(
    bars: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    as_of: Any,
    pivot_left: int = 2,
    pivot_right: int = 2,
    timestamp_mode: str = "open",
) -> Tuple[LevelEvidence, ...]:
    """Return strict swing highs/lows only after right-hand bars have closed."""
    left = max(1, int(pivot_left))
    right = max(1, int(pivot_right))
    cutoff = _coerce_datetime(as_of)
    completed = normalize_completed_bars(
        bars, timeframe=timeframe, as_of=cutoff, timestamp_mode=timestamp_mode
    )
    evidence: List[LevelEvidence] = []
    for index in range(left, len(completed) - right):
        pivot = completed[index]
        neighbours = completed[index - left:index] + completed[index + 1:index + right + 1]
        confirmation = completed[index + right].closed_at
        base_provenance = {
            "pivot_index": index,
            "confirmation_bar_index": index + right,
            "pivot_left": left,
            "pivot_right": right,
            "touch_count": 1,
        }
        if all(pivot.high > item.high for item in neighbours):
            evidence.append(LevelEvidence(
                source_family="horizontal_swing",
                source_name="confirmed_swing_high",
                timeframe=timeframe,
                lower=pivot.high,
                upper=pivot.high,
                observed_at=pivot.closed_at,
                confirmed_at=confirmation,
                data_cutoff_at=cutoff,
                strength=1.0 + min(0.5, 0.1 * (left + right)),
                provenance={**base_provenance, "role_hint": "resistance"},
            ))
        if all(pivot.low < item.low for item in neighbours):
            evidence.append(LevelEvidence(
                source_family="horizontal_swing",
                source_name="confirmed_swing_low",
                timeframe=timeframe,
                lower=pivot.low,
                upper=pivot.low,
                observed_at=pivot.closed_at,
                confirmed_at=confirmation,
                data_cutoff_at=cutoff,
                strength=1.0 + min(0.5, 0.1 * (left + right)),
                provenance={**base_provenance, "role_hint": "support"},
            ))
    return tuple(sorted(evidence, key=lambda item: (
        item.observed_at, item.confirmed_at, item.source_name, item.midpoint
    )))


def completed_session_evidence(
    bars: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    as_of: Any,
    timestamp_mode: str = "open",
) -> Tuple[LevelEvidence, ...]:
    """Return high/low/close from the latest verifiably completed session."""
    cutoff = _coerce_datetime(as_of)
    completed = normalize_completed_bars(
        bars, timeframe=timeframe, as_of=cutoff, timestamp_mode=timestamp_mode
    )
    if not completed:
        return ()
    session = completed[-1]
    tf = _normalized_timeframe(timeframe)
    if tf == "1D":
        labels = ("PDH", "PDL", "PDC")
    elif tf == "1W":
        labels = ("PWH", "PWL", "PWC")
    else:
        labels = (f"{tf}_HIGH", f"{tf}_LOW", f"{tf}_CLOSE")
    values = (session.high, session.low, session.close)
    roles = ("resistance", "support", "reference")
    strengths = (1.6, 1.6, 1.2)
    return tuple(LevelEvidence(
        source_family="session",
        source_name=label,
        timeframe=tf,
        lower=value,
        upper=value,
        observed_at=session.closed_at,
        confirmed_at=session.closed_at,
        data_cutoff_at=cutoff,
        strength=strength,
        provenance={
            "role_hint": role,
            "session_opened_at": _iso(session.opened_at),
            "session_closed_at": _iso(session.closed_at),
            # PDH/PDL/PDC from one session share one information origin and
            # therefore must not inflate independent-source confluence.
            "independence_key": f"session:{tf}:{_iso(session.closed_at)}",
            "touch_count": 1,
        },
    ) for label, value, role, strength in zip(labels, values, roles, strengths))


def evidence_from_mapping(
    level: Mapping[str, Any],
    *,
    source_family: str,
    timeframe: str,
    as_of: Any,
    projection_only: bool = False,
) -> LevelEvidence:
    """Adapt a legacy point/zone dictionary into causal level evidence."""
    cutoff = _coerce_datetime(as_of)
    price = _safe_float(level.get("price", level.get("reference")))
    lower = _safe_float(level.get("lower", level.get("zone_low", price)), price)
    upper = _safe_float(level.get("upper", level.get("zone_high", price)), price)
    if lower is None or upper is None:
        raise ValueError("legacy level requires price or lower/upper")
    if upper < lower:
        lower, upper = upper, lower
    observed = level.get("observed_at", level.get("timestamp", cutoff))
    confirmed = level.get("confirmed_at", observed)
    return LevelEvidence(
        source_family=source_family,
        source_name=str(level.get("source") or level.get("name") or source_family),
        timeframe=timeframe,
        lower=lower,
        upper=upper,
        observed_at=observed,
        confirmed_at=confirmed,
        data_cutoff_at=cutoff,
        strength=_safe_float(level.get("strength", level.get("weight", 1.0)), 1.0) or 0.0,
        provenance={
            "legacy_kind": level.get("kind"),
            "legacy_source": level.get("source"),
            "independence_key": level.get("independence_key") or f"{source_family}:{_normalized_timeframe(timeframe)}",
            "touch_count": int(_safe_float(level.get("touch_count"), 1.0) or 1),
        },
        projection_only=bool(projection_only),
    )


def _evidence_sort_key(item: LevelEvidence) -> Tuple[Any, ...]:
    return (
        item.lower,
        item.upper,
        item.source_family,
        item.timeframe,
        item.source_name,
        item.confirmed_at,
        item.observed_at,
    )


def _evidence_origin_role(item: LevelEvidence) -> Optional[str]:
    """Return the causal role encoded by the evidence, never by the live quote."""
    candidates = (
        item.provenance.get("role_hint"),
        item.provenance.get("role"),
        item.provenance.get("legacy_kind"),
    )
    for raw in candidates:
        role = str(raw or "").strip().lower()
        if role in {"resistance", "supply", "high", "upper"}:
            return "resistance"
        if role in {"support", "demand", "low", "lower"}:
            return "support"

    source = (
        str(item.source_name or "").strip().upper().replace("-", "_").replace(" ", "_")
    )
    if source in {"PDH", "PWH", "VAH", "CONFIRMED_SWING_HIGH"}:
        return "resistance"
    if source in {"PDL", "PWL", "VAL", "CONFIRMED_SWING_LOW"}:
        return "support"
    if any(token in source for token in ("RESISTANCE", "SUPPLY", "SWING_HIGH")):
        return "resistance"
    if any(token in source for token in ("SUPPORT", "DEMAND", "SWING_LOW")):
        return "support"
    return None


def build_level_zones(
    evidence: Iterable[LevelEvidence],
    *,
    reference_price: Any,
    tick_size: Any = None,
    spread: Any = None,
    atr_by_timeframe: Optional[Mapping[str, Any]] = None,
    atr_zone_fraction: float = 0.10,
) -> Tuple[LevelZone, ...]:
    """Cluster causal evidence into adaptive zones without crossing the price.

    ``spread`` is an absolute price distance.  The adaptive half-width is the
    maximum of the supplied evidence width, two ticks, 0.75 spreads and the
    configured ATR fraction. Evidence below and above ``reference_price`` is
    clustered independently, preventing support/resistance cross-merges.
    """
    reference = _safe_float(reference_price)
    if reference is None or reference <= 0:
        raise ValueError("reference_price must be positive")
    tick = max(0.0, _safe_float(tick_size, 0.0) or 0.0)
    quoted_spread = max(0.0, _safe_float(spread, 0.0) or 0.0)
    atr_fraction = max(0.0, _safe_float(atr_zone_fraction, 0.10) or 0.0)
    atr_map = {
        _normalized_timeframe(key): max(0.0, _safe_float(value, 0.0) or 0.0)
        for key, value in (atr_by_timeframe or {}).items()
    }
    epsilon = max(math.ulp(reference) * 16, tick * 1e-6)

    expanded: Dict[str, List[Tuple[LevelEvidence, float, float]]] = {
        "support": [], "resistance": [], "overlap": []
    }
    for item in sorted(tuple(evidence), key=_evidence_sort_key):
        if not isinstance(item, LevelEvidence):
            raise TypeError("build_level_zones accepts LevelEvidence objects")
        center = item.midpoint
        atr = atr_map.get(item.timeframe, 0.0)
        half_width = max(
            (item.upper - item.lower) / 2.0,
            tick * 2.0,
            quoted_spread * 0.75,
            atr * atr_fraction,
            math.ulp(center) * 16,
        )
        lower = max(math.ulp(center), center - half_width)
        upper = center + half_width
        if item.upper < reference:
            side = "support"
            upper = min(upper, reference - epsilon)
            lower = min(lower, upper)
        elif item.lower > reference:
            side = "resistance"
            lower = max(lower, reference + epsilon)
            upper = max(upper, lower)
        else:
            side = "overlap"
        expanded[side].append((item, lower, upper))

    zones: List[LevelZone] = []
    for side in ("support", "overlap", "resistance"):
        members: List[Tuple[LevelEvidence, float, float]] = []
        cluster_lower = cluster_upper = 0.0

        def flush() -> None:
            nonlocal members, cluster_lower, cluster_upper
            if not members:
                return
            items = tuple(sorted((row[0] for row in members), key=_evidence_sort_key))
            key_strengths: Dict[str, float] = {}
            structural_touch_counts: Dict[str, int] = {}
            projection_touch_counts: Dict[str, int] = {}
            structural_keys = set()
            weighted_sum = 0.0
            total_weight = 0.0
            for item, _lower, _upper in members:
                key_strengths[item.independence_key] = max(
                    key_strengths.get(item.independence_key, 0.0), item.strength
                )
                evidence_touches = max(
                    1, int(_safe_float(item.provenance.get("touch_count"), 1.0) or 1)
                )
                if item.projection_only:
                    # Several Fib ratios from one leg are one derived source,
                    # not repeated market touches. They may add confluence
                    # once, but cannot inflate strength merely by ratio count.
                    projection_touch_counts[item.independence_key] = max(
                        projection_touch_counts.get(item.independence_key, 0), evidence_touches
                    )
                else:
                    structural_touch_counts[item.independence_key] = (
                        structural_touch_counts.get(item.independence_key, 0) + evidence_touches
                    )
                if not item.projection_only:
                    structural_keys.add(item.independence_key)
                weight = max(0.01, item.strength)
                weighted_sum += item.midpoint * weight
                total_weight += weight
            touch_keys = set(structural_touch_counts) | set(projection_touch_counts)
            touch_count = sum(
                structural_touch_counts.get(key) or projection_touch_counts.get(key, 0)
                for key in touch_keys
            )
            independent_strengths = sorted(key_strengths.values(), reverse=True)
            combined_strength = (independent_strengths[0] if independent_strengths else 0.0)
            combined_strength += 0.25 * sum(independent_strengths[1:])
            combined_strength += min(0.5, max(0, touch_count - 1) * 0.05)
            zone_reference = weighted_sum / total_weight if total_weight else (cluster_lower + cluster_upper) / 2.0
            zone_reference = min(cluster_upper, max(cluster_lower, zone_reference))
            confirmed_at = max(item.confirmed_at for item in items)
            break_state_items = tuple(item for item in items if not item.projection_only) or items
            break_states = {
                str(item.provenance.get("break_state") or "intact").strip().lower()
                for item in break_state_items
            }
            break_state = next(iter(break_states)) if len(break_states) == 1 else "intact"
            origin_roles = tuple(sorted({
                role
                for item in break_state_items
                for role in (_evidence_origin_role(item),)
                if role is not None
            }))
            flags: List[str] = []
            if not structural_keys:
                flags.append("projection_only")
            if len(key_strengths) == 1:
                flags.append("single_independent_source")
            # Zone identity belongs to the causal evidence, not to the current
            # quote. ``side`` and the clipped adaptive bounds can change as the
            # market crosses a level; including either would make a persisted
            # break/reclaim gate lose the zone it refers to at exactly that
            # transition. The evidence tuple remains deterministic while the
            # presentation/classification fields are free to move with price.
            identity = "|".join(
                f"{item.independence_key}:{item.source_family}:{item.timeframe}:"
                f"{item.source_name}:{format(item.lower, '.12g')}:"
                f"{format(item.upper, '.12g')}:{_iso(item.observed_at)}:"
                f"{_iso(item.confirmed_at)}"
                for item in items
            )
            zone_id = "lz_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            zones.append(LevelZone(
                zone_id=zone_id,
                lower=cluster_lower,
                upper=cluster_upper,
                reference=zone_reference,
                side_at_reference=side,
                evidence=items,
                independent_sources=len(key_strengths),
                independent_structural_sources=len(structural_keys),
                touch_count=touch_count,
                confirmed_at=confirmed_at,
                break_state=break_state,
                strength=round(combined_strength, 6),
                origin_roles=origin_roles,
                quality_flags=tuple(flags),
            ))
            members = []
            cluster_lower = cluster_upper = 0.0

        for row in sorted(expanded[side], key=lambda value: (
            value[1], value[2], _evidence_sort_key(value[0])
        )):
            if not members:
                members = [row]
                cluster_lower, cluster_upper = row[1], row[2]
            elif row[1] <= cluster_upper:
                members.append(row)
                cluster_lower = min(cluster_lower, row[1])
                cluster_upper = max(cluster_upper, row[2])
            else:
                flush()
                members = [row]
                cluster_lower, cluster_upper = row[1], row[2]
        flush()
    return tuple(sorted(zones, key=lambda zone: (zone.lower, zone.upper, zone.zone_id)))


def build_structure_snapshot(
    bars_by_timeframe: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    symbol: str,
    asset_class: str,
    horizon: str,
    as_of: Any,
    current_price: Any,
    tick_size: Any = None,
    spread: Any = None,
    atr_by_timeframe: Optional[Mapping[str, Any]] = None,
    pivot_left: int = 2,
    pivot_right: int = 2,
    timestamp_mode: str = "open",
    external_evidence: Iterable[LevelEvidence] = (),
    include_session_levels: bool = True,
) -> StructureSnapshot:
    """Build one immutable snapshot from bars available at ``as_of``."""
    cutoff = _coerce_datetime(as_of)
    price = _safe_float(current_price)
    if price is None or price <= 0:
        raise ValueError("current_price must be positive")
    evidence: List[LevelEvidence] = []
    for item in external_evidence or ():
        if not isinstance(item, LevelEvidence):
            raise TypeError("external_evidence accepts LevelEvidence objects")
        # An adapter may have been built with a later data cutoff.  Even if
        # its displayed confirmation is older, accepting it would make the
        # snapshot's causal boundary unverifiable.
        if (
            item.observed_at > cutoff
            or item.confirmed_at > cutoff
            or item.data_cutoff_at > cutoff
        ):
            raise ValueError("external evidence crosses snapshot as_of")
        evidence.append(item)
    completed_counts: Dict[str, int] = {}
    completed_by_timeframe: Dict[str, Tuple[CompletedBar, ...]] = {}
    for raw_tf in sorted((bars_by_timeframe or {}), key=lambda value: _normalized_timeframe(value)):
        timeframe = _normalized_timeframe(raw_tf)
        bars = bars_by_timeframe[raw_tf]
        completed = normalize_completed_bars(
            bars, timeframe=timeframe, as_of=cutoff, timestamp_mode=timestamp_mode
        )
        completed_counts[timeframe] = len(completed)
        completed_by_timeframe[timeframe] = completed
        evidence.extend(confirmed_pivot_evidence(
            completed,
            timeframe=timeframe,
            as_of=cutoff,
            pivot_left=pivot_left,
            pivot_right=pivot_right,
            timestamp_mode="close",
        ))
        if include_session_levels and timeframe in ("1D", "1W"):
            evidence.extend(completed_session_evidence(
                completed,
                timeframe=timeframe,
                as_of=cutoff,
                timestamp_mode="close",
            ))

    normalized_atr = {
        _normalized_timeframe(key): max(0.0, _safe_float(value, 0.0) or 0.0)
        for key, value in (atr_by_timeframe or {}).items()
    }
    tick = max(0.0, _safe_float(tick_size, 0.0) or 0.0)
    quoted_spread = max(0.0, _safe_float(spread, 0.0) or 0.0)
    zones = build_level_zones(
        evidence,
        reference_price=price,
        tick_size=tick_size,
        spread=spread,
        atr_by_timeframe=normalized_atr,
    )
    # A quote crossing a known level is not sufficient evidence that the level
    # changed role.  Re-evaluate the stable evidence zone from completed bars
    # on every snapshot.  This is deterministic across processes and prevents
    # an intrabar jump above resistance (or below support) from silently
    # removing the first-barrier gate.
    trigger_candidates = sorted(
        (
            (_timeframe_seconds(timeframe), timeframe, bars)
            for timeframe, bars in completed_by_timeframe.items()
            if _timeframe_seconds(timeframe) is not None
        ),
        key=lambda item: (item[0], item[1]),
    )
    evaluated_zones: List[LevelZone] = []
    crossed_pending = False
    for zone in zones:
        transition_direction: Optional[str] = None
        transition_label: Optional[str] = None
        if "resistance" in zone.origin_roles and price > zone.upper:
            transition_direction = "LONG"
            transition_label = "resistance"
        elif "support" in zone.origin_roles and price < zone.lower:
            transition_direction = "SHORT"
            transition_label = "support"
        if transition_direction is None or not trigger_candidates:
            evaluated_zones.append(zone)
            continue

        after_confirmation = [
            candidate
            for candidate in trigger_candidates
            if any(bar.closed_at > zone.confirmed_at for bar in candidate[2])
        ]
        _seconds, trigger_timeframe, trigger_bars = (
            after_confirmation[0] if after_confirmation else trigger_candidates[0]
        )
        zone_width = max(0.0, zone.upper - zone.lower)
        transition = evaluate_break_reclaim(
            zone,
            trigger_bars,
            as_of=cutoff,
            direction=transition_direction,
            timeframe=trigger_timeframe,
            breakout_buffer=max(tick * 1.0, quoted_spread * 0.25),
            hold_bars=1,
            require_retest=True,
            retest_tolerance=max(tick * 2.0, quoted_spread * 0.75, zone_width * 0.20),
            timestamp_mode="close",
        )
        flags = list(zone.quality_flags)
        if transition.state == "RECLAIMED":
            flags.append(f"former_{transition_label}_reclaimed")
            break_state = "reclaimed"
        else:
            flags.append(f"crossed_{transition_label}_reclaim_pending")
            break_state = "intact"
            crossed_pending = True
        evaluated_zones.append(replace(
            zone,
            break_state=break_state,
            break_reclaim_evidence=transition,
            quality_flags=tuple(sorted(set(flags))),
        ))
    zones = tuple(evaluated_zones)
    quality_flags: List[str] = []
    if not any(completed_counts.values()):
        quality_flags.append("no_completed_bars")
    if not zones:
        quality_flags.append("no_confirmed_levels")
    if crossed_pending:
        quality_flags.append("crossed_level_reclaim_pending")
    return StructureSnapshot(
        symbol=str(symbol or "").strip().upper(),
        asset_class=str(asset_class or "unknown").strip().lower(),
        horizon=str(horizon or "unknown").strip().lower(),
        as_of=cutoff,
        current_price=price,
        zones=zones,
        atr_by_timeframe=normalized_atr,
        completed_bar_counts=completed_counts,
        quality_flags=tuple(quality_flags),
    )


def classify_for_trade(
    snapshot: StructureSnapshot,
    *,
    entry: Any,
    direction: str,
) -> DirectionalStructure:
    """Classify immutable zones relative to an entry and trade direction."""
    price = _safe_float(entry)
    if price is None or price <= 0:
        raise ValueError("entry must be positive")
    side = str(direction or "").strip().upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    supports = tuple(sorted(
        (zone for zone in snapshot.zones if zone.upper < price),
        key=lambda zone: (price - zone.upper, zone.zone_id),
    ))
    resistances = tuple(sorted(
        (zone for zone in snapshot.zones if zone.lower > price),
        key=lambda zone: (zone.lower - price, zone.zone_id),
    ))
    overlapping = tuple(sorted(
        (zone for zone in snapshot.zones if zone.lower <= price <= zone.upper),
        key=lambda zone: (-zone.strength, zone.zone_id),
    ))
    if side == "LONG":
        opposing_pool = overlapping + resistances
        invalidation = supports
        distance = lambda zone: max(0.0, zone.lower - price)
    else:
        opposing_pool = overlapping + supports
        invalidation = resistances
        distance = lambda zone: max(0.0, price - zone.upper)
    opposing = tuple(sorted(
        (
            zone for zone in opposing_pool
            if not zone.projection_only and zone.break_state != "reclaimed"
        ),
        key=lambda zone: (distance(zone), -zone.strength, zone.zone_id),
    ))
    invalidation = tuple(
        zone for zone in invalidation
        if not zone.projection_only and zone.break_state != "broken"
    )
    return DirectionalStructure(
        snapshot=snapshot,
        entry=price,
        direction=side,
        supports=supports,
        resistances=resistances,
        overlapping=overlapping,
        opposing_barriers=opposing,
        invalidation_candidates=invalidation,
    )


def select_trade_structure(
    directional_structure: DirectionalStructure,
    *,
    stop: Any,
    minimum_rr: float = 1.35,
    minimum_barrier_strength: float = 0.0,
    minimum_structural_sources: int = 1,
) -> StructureDecision:
    """Select the first opposing barrier; never skip it to manufacture R:R."""
    entry = directional_structure.entry
    direction = directional_structure.direction
    stop_value = _safe_float(stop)
    if stop_value is None or stop_value <= 0:
        stop_value = 0.0
    valid_stop = (
        stop_value > 0
        and ((direction == "LONG" and stop_value < entry) or (direction == "SHORT" and stop_value > entry))
    )
    if not valid_stop:
        return StructureDecision(
            status="REJECT",
            reason="invalid_stop_geometry",
            direction=direction,
            entry=entry,
            stop=stop_value,
            risk=None,
            nearest_barrier=None,
            barrier_distance=None,
            barrier_r=None,
            target1=None,
            barrier_gate=None,
        )
    risk = abs(entry - stop_value)
    snapshot = directional_structure.snapshot
    if not snapshot.zones or "no_confirmed_levels" in snapshot.quality_flags:
        return StructureDecision(
            status="REJECT",
            reason="structure_unavailable",
            direction=direction,
            entry=entry,
            stop=stop_value,
            risk=risk,
            nearest_barrier=None,
            barrier_distance=None,
            barrier_r=None,
            target1=None,
            barrier_gate=None,
        )
    barriers = tuple(
        zone for zone in directional_structure.opposing_barriers
        if zone.strength >= max(0.0, minimum_barrier_strength)
        and zone.independent_structural_sources >= max(1, int(minimum_structural_sources))
    )
    if not barriers:
        return StructureDecision(
            status="ACCEPT",
            reason="no_confirmed_opposing_barrier",
            direction=direction,
            entry=entry,
            stop=stop_value,
            risk=risk,
            nearest_barrier=None,
            barrier_distance=None,
            barrier_r=None,
            target1=None,
            barrier_gate=None,
        )
    barrier = barriers[0]
    entry_overlaps_barrier = barrier.lower <= entry <= barrier.upper
    if direction == "LONG":
        barrier_distance = 0.0 if entry_overlaps_barrier else max(0.0, barrier.lower - entry)
        target1 = barrier.upper if entry_overlaps_barrier else barrier.lower
        gate = "BREAK_RECLAIM_REQUIRED"
    else:
        barrier_distance = 0.0 if entry_overlaps_barrier else max(0.0, entry - barrier.upper)
        target1 = barrier.lower if entry_overlaps_barrier else barrier.upper
        gate = "BREAK_SUPPORT_REQUIRED"
    barrier_r = barrier_distance / risk
    if entry_overlaps_barrier:
        return StructureDecision(
            status="WAIT_BREAK_RECLAIM",
            reason="entry_overlaps_opposing_barrier",
            direction=direction,
            entry=entry,
            stop=stop_value,
            risk=risk,
            nearest_barrier=barrier,
            barrier_distance=barrier_distance,
            barrier_r=barrier_r,
            target1=target1,
            barrier_gate=gate,
        )
    if barrier_r + 1e-12 < max(0.0, float(minimum_rr)):
        return StructureDecision(
            status="WAIT_BREAK_RECLAIM",
            reason="first_opposing_barrier_before_minimum_rr",
            direction=direction,
            entry=entry,
            stop=stop_value,
            risk=risk,
            nearest_barrier=barrier,
            barrier_distance=barrier_distance,
            barrier_r=barrier_r,
            target1=target1,
            barrier_gate=gate,
        )
    return StructureDecision(
        status="ACCEPT",
        reason="first_opposing_barrier_is_tradable_tp1",
        direction=direction,
        entry=entry,
        stop=stop_value,
        risk=risk,
        nearest_barrier=barrier,
        barrier_distance=barrier_distance,
        barrier_r=barrier_r,
        target1=target1,
        barrier_gate=None,
    )


def evaluate_break_reclaim(
    zone: LevelZone,
    completed_trigger_bars: Sequence[Mapping[str, Any]],
    *,
    as_of: Any,
    direction: str,
    timeframe: str,
    breakout_buffer: float = 0.0,
    hold_bars: int = 1,
    require_retest: bool = False,
    retest_tolerance: float = 0.0,
    timestamp_mode: str = "open",
) -> BreakReclaimEvidence:
    """Require a completed breakout close and subsequent completed hold bars."""
    side = str(direction or "").strip().upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    cutoff = _coerce_datetime(as_of)
    buffer_value = max(0.0, _safe_float(breakout_buffer, 0.0) or 0.0)
    tolerance = max(0.0, _safe_float(retest_tolerance, 0.0) or 0.0)
    required_holds = max(0, int(hold_bars))
    bars = normalize_completed_bars(
        completed_trigger_bars,
        timeframe=timeframe,
        as_of=cutoff,
        timestamp_mode=timestamp_mode,
    )
    zone_confirmed_at = _coerce_datetime(zone.confirmed_at)
    if zone_confirmed_at > cutoff:
        raise ValueError("zone cannot be evaluated before it was confirmed")
    bars = tuple(bar for bar in bars if bar.closed_at > zone_confirmed_at)
    last_completed = bars[-1] if bars else None
    boundary = zone.upper if side == "LONG" else zone.lower
    active_break: Optional[datetime] = None
    observed_holds = 0
    retest_observed = False

    for bar in bars:
        breakout = (
            bar.close > boundary + buffer_value
            if side == "LONG"
            else bar.close < boundary - buffer_value
        )
        held = bar.close > boundary if side == "LONG" else bar.close < boundary
        if active_break is None:
            if breakout:
                active_break = bar.closed_at
                observed_holds = 0
                retest_observed = False
            continue
        if not held:
            active_break = None
            observed_holds = 0
            retest_observed = False
            continue
        observed_holds += 1
        if side == "LONG":
            retested = bar.low <= boundary + tolerance and bar.close > boundary
        else:
            retested = bar.high >= boundary - tolerance and bar.close < boundary
        retest_observed = retest_observed or retested

    if active_break is None:
        state = "INTACT"
        reason = "no_active_completed_break_close"
    elif observed_holds < required_holds:
        state = "RECLAIM_PENDING"
        reason = "completed_hold_bars_missing"
    elif require_retest and not retest_observed:
        state = "RECLAIM_PENDING"
        reason = "completed_retest_missing"
    else:
        state = "RECLAIMED"
        reason = "completed_break_close_and_hold_confirmed"
    return BreakReclaimEvidence(
        state=state,
        reason=reason,
        direction=side,
        zone_id=zone.zone_id,
        boundary=boundary,
        zone_confirmed_at=zone_confirmed_at,
        timeframe=timeframe,
        as_of=cutoff,
        break_closed_at=active_break,
        last_completed_at=last_completed.closed_at if last_completed else None,
        last_completed_close=last_completed.close if last_completed else None,
        hold_bars_required=required_holds,
        hold_bars_observed=observed_holds,
        retest_required=bool(require_retest),
        retest_observed=retest_observed,
        completed_bars_used=len(bars),
    )


def legacy_level_adapter(
    snapshot: StructureSnapshot,
    *,
    entry: Any = None,
    direction: str = "LONG",
) -> Dict[str, Any]:
    """Return point-style supports/resistances without losing zone metadata."""
    directional = classify_for_trade(
        snapshot,
        entry=entry if entry is not None else snapshot.current_price,
        direction=direction,
    )

    def row(zone: LevelZone, kind: str) -> Dict[str, Any]:
        return {
            "price": zone.reference,
            "zone_low": zone.lower,
            "zone_high": zone.upper,
            "source": " + ".join(zone.source_names),
            "kind": kind,
            "strength": zone.strength,
            "weight": zone.strength,
            "zone_id": zone.zone_id,
            "confirmed_at": _iso(zone.confirmed_at),
            "independent_sources": zone.independent_sources,
            "projection_only": zone.projection_only,
        }

    supports = [row(zone, "SUPPORT") for zone in directional.supports]
    resistances = [row(zone, "RESISTANCE") for zone in directional.resistances]
    overlaps = [row(zone, "OVERLAP") for zone in directional.overlapping]
    levels = sorted(supports + overlaps + resistances, key=lambda item: (item["price"], item["zone_id"]))
    return {
        "model": snapshot.model,
        "as_of": _iso(snapshot.as_of),
        "timeframes": sorted(snapshot.completed_bar_counts),
        "supports": supports,
        "resistances": resistances,
        "overlaps": overlaps,
        "levels": levels,
        "quality_flags": list(snapshot.quality_flags),
    }


__all__ = [
    "BreakReclaimEvidence",
    "CompletedBar",
    "DirectionalStructure",
    "LevelEvidence",
    "LevelZone",
    "StructureDecision",
    "StructureSnapshot",
    "build_level_zones",
    "build_structure_snapshot",
    "classify_for_trade",
    "completed_session_evidence",
    "confirmed_pivot_evidence",
    "evaluate_break_reclaim",
    "evidence_from_mapping",
    "legacy_level_adapter",
    "normalize_completed_bars",
    "select_trade_structure",
]
