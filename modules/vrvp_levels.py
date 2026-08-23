"""
Shared VRVP/volume-profile trade level helpers.

The local volume profile is built from OHLCV bars, so it is an approximation
of TradingView's tick-based VRVP. We use it as structural confluence for
support/resistance and targets, not as a standalone signal generator.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Any, Dict, List, Optional, Tuple

from modules.level_zones import normalize_completed_bars
from modules.volume_analysis import (
    OHLCV_VOLUME_PROFILE_ASSUMPTION,
    OHLCV_VOLUME_PROFILE_METHOD,
    OHLCV_VOLUME_PROFILE_SOURCE,
    calculate_volume_profile,
    merge_lvn_bins,
)
from modules.trade_levels import trade_geometry


UTC = timezone.utc
_VRVP_CLOSE_TIME_KEYS = ("close_time", "close_timestamp", "end_time", "end", "T")
_VRVP_OPEN_TIME_KEYS = ("open_time", "timestamp", "time", "ts", "t")
_VRVP_DATE_KEY = "date"


def _temporal_value_present(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    return not isinstance(value, str) or bool(value.strip())


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return val


def _utc_timestamp_text(value: Any) -> Optional[str]:
    """Render a supported cutoff value as stable UTC provenance text."""
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            magnitude = abs(number)
            if magnitude >= 1e18:
                number /= 1_000_000_000.0
            elif magnitude >= 1e15:
                number /= 1_000_000.0
            elif magnitude >= 1e12:
                number /= 1_000.0
            parsed = datetime.fromtimestamp(number, tz=UTC)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return _utc_timestamp_text(float(text))
            except ValueError:
                pass
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        else:
            return None
    except (OverflowError, OSError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date_temporal_adapter(
    bar: Dict[str, Any],
    *,
    timeframe: str,
    date_session_context: str,
) -> Optional[Dict[str, Any]]:
    """Adapt a date-only bar to explicit, conservative completion times.

    Daily ``date`` fields do not state whether they represent a candle open or
    close.  The default therefore treats the named UTC calendar day as an
    interval that completes at the next UTC midnight.  Callers which know the
    source is a US regular-session equity feed may opt into the exact 16:00 ET
    close through ``date_session_context='us_equity_regular'``.
    """
    raw_date = bar.get(_VRVP_DATE_KEY)
    if raw_date is None or not str(raw_date).strip():
        return None
    text = str(raw_date).strip()
    date_only = len(text) >= 10 and text[:10].count("-") == 2 and not any(
        token in text[10:] for token in ("T", ":")
    )
    normalized_timeframe = str(timeframe or "").strip().upper().replace(" ", "")
    normalized_timeframe = {
        "D": "1D",
        "DAY": "1D",
        "DAILY": "1D",
        "W": "1W",
        "WEEK": "1W",
        "WEEKLY": "1W",
    }.get(normalized_timeframe, normalized_timeframe)

    try:
        parsed = datetime.fromisoformat(text[:10] if date_only else text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)

    context = str(date_session_context or "conservative_calendar_day").strip().lower()
    adapted = dict(bar)
    if date_only and normalized_timeframe == "1D" and context == "us_equity_regular":
        try:
            from zoneinfo import ZoneInfo

            session_day = parsed.date()
            eastern = ZoneInfo("America/New_York")
            opened_at = datetime(
                session_day.year,
                session_day.month,
                session_day.day,
                9,
                30,
                tzinfo=eastern,
            ).astimezone(UTC)
            closed_at = datetime(
                session_day.year,
                session_day.month,
                session_day.day,
                16,
                0,
                tzinfo=eastern,
            ).astimezone(UTC)
        except (ImportError, OSError, ValueError):
            return None
    elif normalized_timeframe == "1D":
        opened_at = parsed
        closed_at = parsed + timedelta(days=1)
    elif normalized_timeframe == "1W":
        opened_at = parsed
        closed_at = parsed + timedelta(days=7)
    elif not date_only:
        # A full datetime has sufficient granularity for the canonical
        # timeframe normalizer.  Date-only intraday bars remain unverifiable.
        adapted["open_time"] = parsed
        return adapted
    else:
        return None

    adapted["open_time"] = opened_at
    adapted["close_time"] = closed_at
    return adapted


def round_trade_price(price: Any) -> float:
    val = _safe_float(price, 0.0) or 0.0
    aval = abs(val)
    if aval >= 100:
        return round(val, 2)
    if aval >= 10:
        return round(val, 2)
    if aval >= 1:
        return round(val, 3)
    if aval >= 0.01:
        return round(val, 5)
    if aval > 0:
        # Preserve six significant digits for micro-priced crypto. Fixed
        # decimal rounding can otherwise collapse a valid level to 0.0.
        return float(f"{val:.6g}")
    return 0.0


def _normalized_vrvp_bar(bar: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return one numeric VRVP bar while preserving data-quality metadata."""
    close = _safe_float(bar.get("close", bar.get("c")))
    high = _safe_float(bar.get("high", bar.get("h", close)))
    low = _safe_float(bar.get("low", bar.get("l", close)))
    open_ = _safe_float(bar.get("open", bar.get("o", close)))
    volume = _safe_float(bar.get("volume", bar.get("v", 0)), 0.0) or 0.0
    if close is None or high is None or low is None or open_ is None:
        return None
    if close <= 0 or high <= 0 or low <= 0 or high < low or volume <= 0:
        return None
    normalized: Dict[str, Any] = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    # Preserve only provenance metadata needed to describe the profile.
    # Numeric OHLCV consumers continue to see the exact same five fields.
    for key in (
        "data_quality",
        "source",
        "source_timeframe",
        "volume_available",
        "volume_is_estimate",
    ):
        if key in bar:
            normalized[key] = bar.get(key)
    return normalized


def _normalize_ohlcv_bars_with_provenance(
    bars: List[Dict[str, Any]],
    lookback: Optional[int] = None,
    *,
    timeframe: str = "1D",
    as_of: Any = None,
    timestamp_mode: str = "open",
    date_session_context: str = "conservative_calendar_day",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Normalize VRVP input and verify completion before applying lookback.

    A timestamped batch is fail-closed: every contributing bar must have a
    verifiable close at or before ``as_of``.  Untimestamped legacy batches keep
    their historical order/slicing behaviour, but their provenance explicitly
    states that causal completion could not be verified.
    """
    raw_bars = [bar for bar in (bars or []) if isinstance(bar, dict)]
    requested_date_context = str(
        date_session_context or "conservative_calendar_day"
    ).strip().lower()
    effective_date_context = (
        "us_equity_regular"
        if requested_date_context == "us_equity_regular"
        else "conservative_calendar_day"
    )
    has_timestamped_input = any(
        any(
            _temporal_value_present(bar.get(key))
            for key in _VRVP_CLOSE_TIME_KEYS + _VRVP_OPEN_TIME_KEYS
        )
        or bool(str(bar.get(_VRVP_DATE_KEY) or "").strip())
        for bar in raw_bars
    )
    effective_lookback = int(lookback) if lookback and int(lookback) > 0 else None

    if not has_timestamped_input:
        source = raw_bars[-effective_lookback:] if effective_lookback else raw_bars
        parsed = [item for item in (_normalized_vrvp_bar(bar) for bar in source) if item]
        return parsed, {
            "causal_completion_verified": False,
            "completion_filter_mode": "legacy_no_timestamps",
            "legacy_without_timestamps": True,
            "timestamp_mode": None,
            "as_of": None,
            "input_bar_count": len(raw_bars),
            "timestamped_input_count": 0,
            "date_temporal_input_count": 0,
            "date_temporal_adapted_count": 0,
            "completed_before_lookback_count": None,
            "raw_completed_before_dedup_count": None,
            "identical_duplicate_count": None,
            "conflicting_duplicate_count": None,
            "duplicate_conflict_fail_closed": False,
            "excluded_not_causally_completed_count": None,
            "excluded_missing_timestamp_count": 0,
            "excluded_unadaptable_date_count": 0,
            "lookback_applied_after_completion_filter": False,
            "date_session_context": None,
            "date_session_context_requested": None,
            "date_session_context_fallback": False,
            "date_completion_semantics": None,
            "profile_confirmed_at": None,
        }

    cutoff = as_of if as_of is not None else datetime.now(tz=UTC)
    completed: List[Tuple[datetime, int, Dict[str, Any]]] = []
    completed_by_close: Dict[
        datetime,
        Tuple[datetime, Tuple[float, float, float, float, float], int],
    ] = {}
    excluded_not_completed = 0
    excluded_missing_timestamp = 0
    excluded_unadaptable_date = 0
    timestamped_count = 0
    date_temporal_count = 0
    date_adapted_count = 0
    identical_duplicate_count = 0
    conflicting_duplicate_count = 0
    raw_completed_count = 0

    for source_index, bar in enumerate(raw_bars):
        has_standard_timestamp = any(
            _temporal_value_present(bar.get(key))
            for key in _VRVP_CLOSE_TIME_KEYS + _VRVP_OPEN_TIME_KEYS
        )
        has_date = bool(str(bar.get(_VRVP_DATE_KEY) or "").strip())
        if not has_standard_timestamp and not has_date:
            excluded_missing_timestamp += 1
            continue
        timestamped_count += 1
        causal_input = bar
        if not has_standard_timestamp and has_date:
            date_temporal_count += 1
            adapted = _date_temporal_adapter(
                bar,
                timeframe=timeframe,
                date_session_context=effective_date_context,
            )
            if adapted is None:
                excluded_unadaptable_date += 1
                excluded_not_completed += 1
                continue
            causal_input = adapted
            date_adapted_count += 1
        try:
            causal_bar = normalize_completed_bars(
                [causal_input],
                timeframe=timeframe,
                as_of=cutoff,
                timestamp_mode=timestamp_mode,
            )
        except (OverflowError, OSError, TypeError, ValueError):
            causal_bar = ()
        if not causal_bar:
            excluded_not_completed += 1
            continue
        normalized = _normalized_vrvp_bar(bar)
        if normalized is None:
            excluded_not_completed += 1
            continue
        raw_completed_count += 1
        opened_at = causal_bar[0].opened_at
        closed_at = causal_bar[0].closed_at
        value_signature = (
            float(normalized["open"]),
            float(normalized["high"]),
            float(normalized["low"]),
            float(normalized["close"]),
            float(normalized["volume"]),
        )
        # One fixed-timeframe candle close can have exactly one opening instant
        # and one OHLCV payload.  Keying by the full interval let two candles
        # with the same close but contradictory opens masquerade as independent
        # observations because each row was normalized in isolation.  Treat
        # every disagreement for the same close instant as a batch-level
        # conflict and fail the whole profile closed below.
        prior = completed_by_close.get(closed_at)
        if prior is not None:
            if prior[0] == opened_at and prior[1] == value_signature:
                identical_duplicate_count += 1
            else:
                conflicting_duplicate_count += 1
            continue
        completed_by_close[closed_at] = (opened_at, value_signature, source_index)
        completed.append((closed_at, source_index, normalized))

    completed.sort(key=lambda item: (item[0], item[1]))
    completed_count = len(completed)
    duplicate_conflict_fail_closed = conflicting_duplicate_count > 0
    if duplicate_conflict_fail_closed:
        completed = []
    elif effective_lookback:
        completed = completed[-effective_lookback:]
    profile_confirmed_at = _utc_timestamp_text(completed[-1][0]) if completed else None
    parsed = [item[2] for item in completed]

    return parsed, {
        "causal_completion_verified": not duplicate_conflict_fail_closed,
        "completion_filter_mode": (
            "timestamped_duplicate_conflict_rejected"
            if duplicate_conflict_fail_closed
            else "timestamped_completed_only"
        ),
        "legacy_without_timestamps": False,
        "timestamp_mode": str(timestamp_mode or "open").strip().lower(),
        "as_of": _utc_timestamp_text(cutoff),
        "input_bar_count": len(raw_bars),
        "timestamped_input_count": timestamped_count,
        "date_temporal_input_count": date_temporal_count,
        "date_temporal_adapted_count": date_adapted_count,
        "completed_before_lookback_count": completed_count,
        "raw_completed_before_dedup_count": raw_completed_count,
        "identical_duplicate_count": identical_duplicate_count,
        "conflicting_duplicate_count": conflicting_duplicate_count,
        "duplicate_conflict_fail_closed": duplicate_conflict_fail_closed,
        "excluded_not_causally_completed_count": excluded_not_completed,
        "excluded_missing_timestamp_count": excluded_missing_timestamp,
        "excluded_unadaptable_date_count": excluded_unadaptable_date,
        "lookback_applied_after_completion_filter": True,
        "date_session_context": effective_date_context if date_temporal_count else None,
        "date_session_context_requested": requested_date_context if date_temporal_count else None,
        "date_session_context_fallback": bool(
            date_temporal_count and requested_date_context != effective_date_context
        ),
        "date_completion_semantics": (
            "us_equity_regular_session_16_et"
            if date_temporal_count
            and effective_date_context == "us_equity_regular"
            else "calendar_interval_closes_next_utc_boundary"
            if date_temporal_count
            else None
        ),
        "profile_confirmed_at": profile_confirmed_at,
    }


def normalize_ohlcv_bars(
    bars: List[Dict[str, Any]],
    lookback: Optional[int] = None,
    *,
    timeframe: str = "1D",
    as_of: Any = None,
    timestamp_mode: str = "open",
    date_session_context: str = "conservative_calendar_day",
) -> List[Dict[str, Any]]:
    """Normalize mixed API bars; timestamped batches use completed bars only."""
    parsed, _ = _normalize_ohlcv_bars_with_provenance(
        bars,
        lookback=lookback,
        timeframe=timeframe,
        as_of=as_of,
        timestamp_mode=timestamp_mode,
        date_session_context=date_session_context,
    )
    return parsed


def calculate_wilder_atr(
    bars: List[Dict[str, Any]],
    period: int = 14,
    lookback: Optional[int] = None,
) -> float:
    """Return a canonical Wilder ATR for mixed API bar shapes.

    ATR is an absolute price distance, calculated on the same timeframe as
    ``bars``. Volume is intentionally not required because true range only
    depends on high, low, and the previous close. Returning ``0.0`` for fewer
    than ``period + 1`` valid bars keeps callers explicit about their fallback.
    """
    try:
        period = max(1, int(period))
    except (TypeError, ValueError):
        period = 14

    # NACHAUDIT N1 (defensiv): Wilder-ATR setzt chronologische Bars voraus.
    # APIs liefern teils sort=desc (neueste zuerst) — dann laeuft die
    # Glaettung rueckwaerts und previous_close ist der Folgetag. Wenn
    # Timestamps vorhanden sind, wird deshalb VOR dem Lookback-Slice
    # aufsteigend sortiert.
    raw_bars = [bar for bar in (bars or []) if isinstance(bar, dict)]

    def _bar_sort_ts(bar: Dict[str, Any]) -> Optional[float]:
        for key in ("t", "timestamp", "time", "ts"):
            value = bar.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
        return None

    ts_values = [_bar_sort_ts(bar) for bar in raw_bars]
    if len(raw_bars) >= 2 and all(ts is not None for ts in ts_values):
        raw_bars = [bar for _, bar in sorted(zip(ts_values, raw_bars), key=lambda pair: pair[0])]

    source = raw_bars[-lookback:] if lookback and lookback > 0 else raw_bars
    parsed: List[Dict[str, float]] = []
    for bar in source:
        if not isinstance(bar, dict):
            continue
        close = _safe_float(bar.get("close", bar.get("c")))
        high = _safe_float(bar.get("high", bar.get("h")))
        low = _safe_float(bar.get("low", bar.get("l")))
        if close is None or high is None or low is None:
            continue
        if close <= 0 or high <= 0 or low <= 0 or high < low:
            continue
        parsed.append({"high": high, "low": low, "close": close})

    if len(parsed) < period + 1:
        return 0.0

    true_ranges: List[float] = []
    for index in range(1, len(parsed)):
        bar = parsed[index]
        previous_close = parsed[index - 1]["close"]
        true_ranges.append(max(
            bar["high"] - bar["low"],
            abs(bar["high"] - previous_close),
            abs(bar["low"] - previous_close),
        ))

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((period - 1) * atr + true_range) / period
    return float(atr) if math.isfinite(atr) and atr > 0 else 0.0


def _dedupe_levels(levels: List[Dict[str, Any]], entry: float) -> List[Dict[str, Any]]:
    """Merge almost-identical profile levels without losing the strongest source."""
    if not levels or entry <= 0:
        return []
    # Treat levels inside 0.12% as the same zone. That keeps penny crypto and
    # larger stocks both stable without a fixed cent threshold.
    # NACHAUDIT N10: Floor relativ statt absolut — 1e-9 war bei Sub-Nano-
    # Coins ~45% des Preises und kollabierte alle Level zu einem.
    # A fixed absolute floor can collapse distinct levels for ultra-low-priced
    # tokens. math.ulp keeps the numerical floor relative to the actual value.
    tolerance = max(entry * 0.0012, math.ulp(entry) * 16)
    merged: List[Dict[str, Any]] = []
    for level in sorted(levels, key=lambda x: _safe_float(x.get("price"), 0.0) or 0.0):
        price = _safe_float(level.get("price"))
        if price is None or price <= 0:
            continue
        if merged and abs(price - (_safe_float(merged[-1].get("price"), price) or price)) <= tolerance:
            if (_safe_float(level.get("weight"), 0.0) or 0.0) > (_safe_float(merged[-1].get("weight"), 0.0) or 0.0):
                merged[-1] = level
            else:
                merged[-1].setdefault("merged_sources", []).append(level.get("source", "VRVP"))
            continue
        merged.append(level)
    return merged


def _profile_identity(
    profile: Dict[str, Any],
    *,
    timeframe: str,
    confirmed_at: Optional[str],
) -> str:
    """Return a stable identity for one causally frozen volume profile."""
    parts = (
        str(timeframe or profile.get("timeframe") or "UNKNOWN").strip().upper(),
        str(confirmed_at or "UNVERIFIED"),
        str(profile.get("method") or OHLCV_VOLUME_PROFILE_METHOD),
        f"{_safe_float(profile.get('range_low'), 0.0) or 0.0:.12g}",
        f"{_safe_float(profile.get('range_high'), 0.0) or 0.0:.12g}",
        str(int(_safe_float(profile.get("bin_count"), 0.0) or 0)),
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"vrvp-profile-{digest}"


def _profile_zone_bounds(
    profile: Dict[str, Any],
    price: Any,
    kind: str,
    *,
    explicit_low: Any = None,
    explicit_high: Any = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Resolve the traded price bin represented by a VRVP level."""
    value = _safe_float(price)
    if value is None or value <= 0:
        return None, None
    lower = _safe_float(explicit_low)
    upper = _safe_float(explicit_high)
    if lower is not None and upper is not None and 0 < lower <= upper:
        return lower, upper

    bins = [row for row in (profile.get("bins") or []) if isinstance(row, dict)]
    normalized_kind = str(kind or "").strip().upper()
    tolerance = max(abs(value) * 1e-10, math.ulp(value) * 16)
    matched: Optional[Dict[str, Any]] = None
    if normalized_kind == "VAH":
        matched = next(
            (
                row for row in bins
                if _safe_float(row.get("high")) is not None
                and math.isclose(
                    _safe_float(row.get("high")) or 0.0,
                    value,
                    rel_tol=1e-10,
                    abs_tol=tolerance,
                )
            ),
            None,
        )
    elif normalized_kind == "VAL":
        matched = next(
            (
                row for row in bins
                if _safe_float(row.get("low")) is not None
                and math.isclose(
                    _safe_float(row.get("low")) or 0.0,
                    value,
                    rel_tol=1e-10,
                    abs_tol=tolerance,
                )
            ),
            None,
        )
    if matched is None:
        matched = next(
            (
                row for row in bins
                if (_safe_float(row.get("low"), value) or value) - tolerance
                <= value
                <= (_safe_float(row.get("high"), value) or value) + tolerance
            ),
            None,
        )
    if matched is not None:
        lower = _safe_float(matched.get("low"))
        upper = _safe_float(matched.get("high"))
        if lower is not None and upper is not None and 0 < lower <= upper:
            return lower, upper

    width = _safe_float(profile.get("bin_width"), 0.0) or 0.0
    if width > 0:
        if normalized_kind == "VAH":
            return max(math.ulp(value), value - width), value
        if normalized_kind == "VAL":
            return value, value + width
        return max(math.ulp(value), value - width / 2.0), value + width / 2.0
    return value, value


def _make_level(
    price: Any,
    source: str,
    kind: str,
    weight: float,
    *,
    timeframe: Optional[str] = None,
    profile_id: Optional[str] = None,
    confirmed_at: Optional[str] = None,
    data_cutoff_at: Optional[str] = None,
    zone_low: Any = None,
    zone_high: Any = None,
    causal_structure_validated: bool = False,
) -> Optional[Dict[str, Any]]:
    val = _safe_float(price)
    if val is None or val <= 0:
        return None
    lower = _safe_float(zone_low, val)
    upper = _safe_float(zone_high, val)
    if lower is None or upper is None or lower <= 0 or upper < lower:
        lower = upper = val
    normalized_timeframe = str(timeframe or "").strip().upper() or None
    identity = str(profile_id or "").strip() or None
    normalized_kind = str(kind or "").strip().upper()
    zone_seed = "|".join((
        identity or "unverified-profile",
        normalized_kind,
        f"{lower:.12g}",
        f"{upper:.12g}",
    ))
    zone_id = f"vrvp-zone-{hashlib.sha256(zone_seed.encode('utf-8')).hexdigest()[:20]}"
    validated = bool(
        causal_structure_validated
        and identity
        and confirmed_at
        and normalized_timeframe
        and lower > 0
        and upper >= lower
    )
    return {
        "price": val,
        "rounded": round_trade_price(val),
        "source": source,
        "kind": normalized_kind,
        "weight": round(float(weight or 0.0), 3),
        "source_family": "vrvp",
        "timeframe": normalized_timeframe,
        "profile_id": identity,
        # All nodes produced by one profile share an evidence key.  Separate
        # prices from the same histogram are not independent confirmations.
        "independence_key": identity,
        "zone_id": zone_id,
        "zone_low": lower,
        "zone_high": upper,
        "lower": lower,
        "upper": upper,
        "observed_at": confirmed_at,
        "confirmed_at": confirmed_at,
        "data_cutoff_at": data_cutoff_at,
        "causal_structure_validated": validated,
    }


def build_vrvp_structure(
    bars: List[Dict[str, Any]],
    current_price: Any,
    direction: str = "LONG",
    *,
    timeframe: str = "1D",
    num_bins: int = 24,
    min_bars: int = 20,
    lookback: Optional[int] = None,
    as_of: Any = None,
    timestamp_mode: str = "open",
    date_session_context: str = "conservative_calendar_day",
) -> Optional[Dict[str, Any]]:
    """Build a VRVP structure from legacy bars or causally completed bars."""
    current = _safe_float(current_price)
    if current is None or current <= 0:
        return None
    ohlcv, completion_provenance = _normalize_ohlcv_bars_with_provenance(
        bars or [],
        lookback=lookback,
        timeframe=timeframe,
        as_of=as_of,
        timestamp_mode=timestamp_mode,
        date_session_context=date_session_context,
    )
    if len(ohlcv) < min_bars:
        return None
    effective_bins = max(12, int(num_bins or 24))
    profile = calculate_volume_profile(
        ohlcv,
        num_bins=effective_bins,
        timeframe=timeframe,
    )
    if not profile:
        return None

    profile_quality = "ok" if len(ohlcv) >= max(min_bars, 30) else "thin"
    profile_method = str(profile.get("method") or OHLCV_VOLUME_PROFILE_METHOD)
    data_quality = str(profile.get("data_quality") or "ohlcv_bar_approximation")
    bin_width = _safe_float(profile.get("bin_width"))
    if bin_width is None:
        range_high = _safe_float(profile.get("range_high"))
        range_low = _safe_float(profile.get("range_low"))
        if range_high is not None and range_low is not None and range_high > range_low:
            bin_width = (range_high - range_low) / effective_bins
    profile_confirmed_at = completion_provenance.get("profile_confirmed_at")
    causal_profile = bool(
        completion_provenance.get("causal_completion_verified") is True
        and profile_confirmed_at
    )
    profile_id = _profile_identity(
        profile,
        timeframe=timeframe,
        confirmed_at=profile_confirmed_at,
    )
    provenance = {
        "source": OHLCV_VOLUME_PROFILE_SOURCE,
        "approximation": True,
        "tick_data_used": False,
        "method": profile_method,
        "volume_allocation_assumption": str(
            profile.get("volume_allocation_assumption")
            or OHLCV_VOLUME_PROFILE_ASSUMPTION
        ),
        "timeframe": str(timeframe or profile.get("timeframe") or "unknown"),
        "bin_count": int(profile.get("bin_count") or effective_bins),
        "bin_width": bin_width,
        "bar_count": len(ohlcv),
        "contributing_bar_count": int(
            profile.get("contributing_bar_count") or len(ohlcv)
        ),
        "volume_coverage_ratio": _safe_float(
            profile.get("volume_coverage_ratio"), 1.0
        ),
        "data_quality": data_quality,
        "profile_quality": profile_quality,
        "input_data_quality": list(profile.get("input_data_quality") or []),
        "source_timeframes": list(profile.get("source_timeframes") or []),
        "volume_is_estimate": profile.get("volume_is_estimate") is True,
        "profile_id": profile_id,
        "profile_confirmed_at": profile_confirmed_at,
        "causal_structure_validated": causal_profile,
        **completion_provenance,
    }

    avg_vol = _safe_float(profile.get("avg_volume"), 0.0) or 0.0
    all_levels: List[Dict[str, Any]] = []
    common_level_metadata = {
        "timeframe": str(timeframe or profile.get("timeframe") or "UNKNOWN"),
        "profile_id": profile_id,
        "confirmed_at": profile_confirmed_at,
        "data_cutoff_at": completion_provenance.get("as_of"),
        "causal_structure_validated": causal_profile,
    }
    for source, key, weight in (
        ("VRVP POC", "poc", 2.2),
        ("VRVP VAH", "vah", 1.7),
        ("VRVP VAL", "val", 1.7),
    ):
        price = profile.get(key)
        zone_low, zone_high = _profile_zone_bounds(profile, price, key.upper())
        level = _make_level(
            price,
            source,
            key.upper(),
            weight,
            zone_low=zone_low,
            zone_high=zone_high,
            **common_level_metadata,
        )
        if level:
            all_levels.append(level)

    for hvn in profile.get("hvns") or []:
        vol = _safe_float(hvn.get("volume"), 0.0) or 0.0
        weight = 1.45 + (vol / avg_vol if avg_vol > 0 else 0.0) * 0.15
        zone_low, zone_high = _profile_zone_bounds(
            profile,
            hvn.get("mid"),
            "HVN",
            explicit_low=hvn.get("low"),
            explicit_high=hvn.get("high"),
        )
        # Low/mid/high aliases preserve the legacy chart contract, but their
        # shared zone_id and profile independence_key prevent one HVN bin from
        # masquerading as multiple independent targets.
        for price, suffix in (
            (hvn.get("mid"), "HVN mid"),
            (hvn.get("low"), "HVN low"),
            (hvn.get("high"), "HVN high"),
        ):
            level = _make_level(
                price,
                f"VRVP {suffix}",
                "HVN",
                weight,
                zone_low=zone_low,
                zone_high=zone_high,
                **common_level_metadata,
            )
            if level:
                all_levels.append(level)

    lvn_zones = profile.get("lvn_zones") or merge_lvn_bins(profile.get("lvns") or [])
    # LVNs are traversal voids, not traded-volume barriers.  Keep their zones
    # exclusively in ``volume_voids`` so direct consumers of ``levels`` cannot
    # accidentally promote an LVN edge to TP1/TP2.
    all_levels = _dedupe_levels(all_levels, current)
    levels = [
        level for level in all_levels
        if level.get("causal_structure_validated") is True
    ]
    supports = sorted(
        [level for level in levels if (_safe_float(level.get("price"), 0) or 0) < current],
        key=lambda item: item["price"],
        reverse=True,
    )
    resistances = sorted(
        [level for level in levels if (_safe_float(level.get("price"), 0) or 0) > current],
        key=lambda item: item["price"],
    )

    return {
        "timeframe": timeframe,
        "direction": str(direction or "").upper(),
        "bars": len(ohlcv),
        "poc": round_trade_price(profile.get("poc")),
        "vah": round_trade_price(profile.get("vah")),
        "val": round_trade_price(profile.get("val")),
        "range_high": round_trade_price(profile.get("range_high")),
        "range_low": round_trade_price(profile.get("range_low")),
        "supports": supports[:8],
        "resistances": resistances[:8],
        "levels": levels[:24],
        "volume_voids": lvn_zones,
        "profile_quality": profile_quality,
        "source": OHLCV_VOLUME_PROFILE_SOURCE,
        # Additive machine-readable provenance. These fields explicitly mark
        # that no tick/order-book volume was used.
        "approximation": True,
        "profile_method": profile_method,
        "bin_width": bin_width,
        "data_quality": data_quality,
        "causal_completion_verified": completion_provenance.get("causal_completion_verified"),
        "completion_filter_mode": completion_provenance.get("completion_filter_mode"),
        "as_of": completion_provenance.get("as_of"),
        "profile_id": profile_id,
        "profile_confirmed_at": profile_confirmed_at,
        "causal_structure_validated": causal_profile,
        "unverified_profile_level_count": len(all_levels) - len(levels),
        "date_session_context": completion_provenance.get("date_session_context"),
        "date_completion_semantics": completion_provenance.get("date_completion_semantics"),
        "provenance": provenance,
    }


def _get_level(setup: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key in setup:
            val = _safe_float(setup.get(key))
            if val is not None:
                return val
    return None


def _set_level_aliases(setup: Dict[str, Any], key: str, value: float) -> None:
    rounded = round_trade_price(value)
    if key == "entry":
        for alias in ("entry", "Entry"):
            setup[alias] = rounded
    elif key == "stop":
        for alias in ("stop", "StopLoss", "stop_loss"):
            setup[alias] = rounded
    elif key == "tp1":
        for alias in ("tp1", "TP1", "target1"):
            if alias in setup or key == "tp1":
                setup[alias] = rounded
    elif key == "tp2":
        for alias in ("tp2", "TP2", "target2"):
            if alias in setup or key == "tp2":
                setup[alias] = rounded


def _distance_ok(price: float, entry: float, min_reward: float, direction: str) -> bool:
    if direction == "LONG":
        return price > entry and (price - entry) >= min_reward
    return 0 < price < entry and (entry - price) >= min_reward


def _level_kind(level: Dict[str, Any]) -> str:
    return str(level.get("kind") or "").strip().upper()


def _level_source(level: Dict[str, Any]) -> str:
    return str(level.get("source") or "VRVP level").strip()


def _is_lvn_edge(level: Dict[str, Any]) -> bool:
    return _level_kind(level) == "LVN_EDGE" or "LVN" in _level_source(level).upper()


def _has_causal_zone_metadata(level: Dict[str, Any]) -> bool:
    """Whether a structural level can later be matched to reclaim evidence."""
    lower = _safe_float(level.get("zone_low", level.get("lower")))
    upper = _safe_float(level.get("zone_high", level.get("upper")))
    return bool(
        level.get("causal_structure_validated") is True
        and str(level.get("zone_id") or "").strip()
        and str(level.get("confirmed_at") or "").strip()
        and str(level.get("timeframe") or "").strip()
        and str(level.get("source_family") or "").strip()
        and str(level.get("independence_key") or "").strip()
        and lower is not None
        and upper is not None
        and lower > 0
        and upper >= lower
    )


def _is_structural_barrier(level: Dict[str, Any]) -> bool:
    """True only for causally frozen traded-volume zones."""
    if _is_lvn_edge(level) or not _has_causal_zone_metadata(level):
        return False
    kind = _level_kind(level)
    source = _level_source(level).upper()
    return kind in {"POC", "VAH", "VAL", "HVN"} or any(
        token in source for token in ("VRVP POC", "VRVP VAH", "VRVP VAL", "VRVP HVN")
    )


def _is_stop_anchor(level: Dict[str, Any], side: str) -> bool:
    """Use only defensible volume acceptance zones as stop invalidation."""
    if not _is_structural_barrier(level):
        return False
    kind = _level_kind(level)
    source = _level_source(level).upper()
    if kind == "POC" or "VRVP POC" in source:
        return True
    if side == "LONG":
        return kind == "VAL" or "VRVP VAL" in source or "HVN LOW" in source
    return kind == "VAH" or "VRVP VAH" in source or "HVN HIGH" in source


def _candidate_prices(vrvp: Dict[str, Any], side: str, target: bool) -> List[Tuple[float, str]]:
    key = "resistances" if (side == "LONG") == target else "supports"
    candidates: List[Tuple[float, str]] = []
    for level in vrvp.get(key) or []:
        # Empty-volume (LVN) edges describe a traversal gap, not traded
        # acceptance.  They may therefore neither become a stop anchor nor a
        # structural target.  Only POC/VAH/VAL/HVN evidence is eligible.
        if target and not _is_structural_barrier(level):
            continue
        if not target and not _is_stop_anchor(level, side):
            continue
        price = _safe_float(level.get("price"))
        if price and price > 0:
            candidates.append((price, _level_source(level)))
    return candidates


def _target_level_candidate(
    level: Dict[str, Any],
    vrvp: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _is_structural_barrier(level):
        return None
    price = _safe_float(level.get("price"))
    if price is None or price <= 0:
        return None
    return {
        "price": price,
        "source": _level_source(level),
        "kind": _level_kind(level) or None,
        "source_family": str(level.get("source_family") or "vrvp"),
        "timeframe": str(level.get("timeframe") or vrvp.get("timeframe") or ""),
        "independence_key": str(level.get("independence_key") or "") or None,
        "profile_id": level.get("profile_id") or vrvp.get("profile_id"),
        "zone_id": level.get("zone_id"),
        "zone_low": _safe_float(level.get("zone_low", level.get("lower"))),
        "zone_high": _safe_float(level.get("zone_high", level.get("upper"))),
        "confirmed_at": level.get("confirmed_at"),
        "data_cutoff_at": level.get("data_cutoff_at") or vrvp.get("as_of"),
        "causal_structure_validated": True,
        "strength": round(float(_safe_float(level.get("weight"), 1.0) or 1.0), 2),
    }


def _target_level_candidates(
    vrvp: Dict[str, Any], side: str, entry: float
) -> List[Dict[str, Any]]:
    key = "resistances" if side == "LONG" else "supports"
    candidates: List[Dict[str, Any]] = []
    for level in vrvp.get(key) or []:
        candidate = _target_level_candidate(level, vrvp)
        if not candidate:
            continue
        price = float(candidate["price"])
        if (side == "LONG" and price > entry) or (side == "SHORT" and 0 < price < entry):
            candidates.append(candidate)
    candidates.sort(key=lambda item: float(item["price"]), reverse=side == "SHORT")
    return candidates


def _asset_profile(asset_type: str) -> Dict[str, float]:
    text = str(asset_type or "").lower()
    if "crypto" in text:
        return {"min_tp_pct": 0.045, "max_stop_mult": 1.45, "stop_buffer_pct": 0.004}
    if "intraday" in text or "orb" in text:
        return {"min_tp_pct": 0.006, "max_stop_mult": 1.25, "stop_buffer_pct": 0.0015}
    return {"min_tp_pct": 0.025, "max_stop_mult": 1.35, "stop_buffer_pct": 0.0025}


def _barrier_profile(asset_type: str) -> Dict[str, float]:
    """Distance thresholds where the next opposite VRVP zone becomes a gate.

    A nearby resistance/support is not automatically a target. If it is too
    close relative to risk, the setup first needs a break/reclaim instead of a
    blind entry.
    """
    text = str(asset_type or "").lower()
    if "crypto" in text:
        return {"max_r": 1.25, "max_pct": 1.8, "max_pct_r": 1.8}
    if "intraday" in text or "orb" in text:
        return {"max_r": 1.10, "max_pct": 0.9, "max_pct_r": 1.6}
    return {"max_r": 1.25, "max_pct": 2.5, "max_pct_r": 1.8}


def _barrier_zone_geometry(
    level: Dict[str, Any], side: str, entry: float
) -> Optional[Dict[str, Any]]:
    """Return the first touched zone edge and distance from ``entry``.

    A volume node is a zone, not a point target.  For a long, resistance starts
    at its lower edge; for a short, support starts at its upper edge.  An entry
    already inside/touching the zone therefore has zero room before resistance
    or support and must be treated as an immediate barrier.
    """
    price = _safe_float(level.get("price"))
    if price is None or price <= 0 or side not in {"LONG", "SHORT"} or entry <= 0:
        return None
    zone_low = _safe_float(level.get("zone_low", level.get("lower")), price)
    zone_high = _safe_float(level.get("zone_high", level.get("upper")), price)
    if (
        zone_low is None
        or zone_high is None
        or zone_low <= 0
        or zone_high < zone_low
    ):
        zone_low = zone_high = price
    tolerance = max(entry * 1e-12, math.ulp(entry) * 16)
    if side == "LONG":
        if zone_high < entry - tolerance:
            return None
        boundary = zone_low
        raw_distance = zone_low - entry
        distance_basis = "zone_low"
    else:
        if zone_low > entry + tolerance:
            return None
        boundary = zone_high
        raw_distance = entry - zone_high
        distance_basis = "zone_high"
    distance = 0.0 if raw_distance <= tolerance else raw_distance
    inside_zone = bool(zone_low - tolerance <= entry <= zone_high + tolerance)
    return {
        "price": price,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "entry_boundary": boundary,
        "distance": distance,
        "distance_basis": distance_basis,
        "entry_inside_zone": inside_zone,
    }


def _nearest_target_level(vrvp: Dict[str, Any], side: str, entry: float) -> Optional[Dict[str, Any]]:
    key = "resistances" if side == "LONG" else "supports"
    levels: List[Tuple[float, float, Dict[str, Any]]] = []
    for level in vrvp.get(key) or []:
        if not _is_structural_barrier(level):
            continue
        geometry = _barrier_zone_geometry(level, side, entry)
        if geometry is None:
            continue
        levels.append((
            float(geometry["distance"]),
            abs(float(geometry["price"]) - entry),
            level,
        ))
    if not levels:
        return None
    return sorted(levels, key=lambda item: (item[0], item[1]))[0][2]


def _near_trade_barrier(
    vrvp: Dict[str, Any],
    side: str,
    entry: float,
    risk: float,
    asset_type: str,
    *,
    minimum_reward: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if not vrvp or side not in ("LONG", "SHORT") or entry <= 0 or risk <= 0:
        return None
    level = _nearest_target_level(vrvp, side, entry)
    if not level:
        return None
    price = _safe_float(level.get("price"))
    if price is None or price <= 0:
        return None
    zone_geometry = _barrier_zone_geometry(level, side, entry)
    if zone_geometry is None:
        return None
    distance = float(zone_geometry["distance"])
    distance_pct = (distance / entry) * 100.0
    distance_r = distance / risk
    if minimum_reward is not None and minimum_reward > 0:
        # The first real barrier is the honest TP1.  A trade that cannot reach
        # the configured minimum reward before that barrier must wait for a
        # break/reclaim; the barrier must never be skipped in favour of a more
        # distant, synthetic TP1.
        is_close = distance + max(math.ulp(entry) * 16, entry * 1e-12) < minimum_reward
    else:
        # Compatibility path for callers which only request the legacy
        # proximity classification.
        profile = _barrier_profile(asset_type)
        is_close = (
            distance_r <= profile["max_r"]
            or (distance_pct <= profile["max_pct"] and distance_r <= profile["max_pct_r"])
        )
    if not is_close:
        return None
    side_label = "resistance" if side == "LONG" else "support"
    barrier = {
        "side": side_label,
        "price": round_trade_price(price),
        "source": str(level.get("source") or "VRVP level"),
        "kind": _level_kind(level) or None,
        "timeframe": level.get("timeframe") or vrvp.get("timeframe"),
        "source_family": level.get("source_family") or "vrvp",
        "profile_id": level.get("profile_id") or vrvp.get("profile_id"),
        "independence_key": level.get("independence_key"),
        "zone_id": level.get("zone_id"),
        "zone_low": zone_geometry["zone_low"],
        "zone_high": zone_geometry["zone_high"],
        "entry_boundary": zone_geometry["entry_boundary"],
        "distance_basis": zone_geometry["distance_basis"],
        "entry_inside_zone": zone_geometry["entry_inside_zone"],
        "confirmed_at": level.get("confirmed_at"),
        "data_cutoff_at": level.get("data_cutoff_at") or vrvp.get("as_of"),
        "causal_structure_validated": level.get("causal_structure_validated") is True,
        "distance_pct": round(distance_pct, 2),
        "distance_r": round(distance_r, 2),
        "strength": round(float(_safe_float(level.get("weight"), 1.0) or 1.0), 2),
        "action": "BREAK_RECLAIM_REQUIRED" if side == "LONG" else "BREAK_SUPPORT_REQUIRED",
    }
    barrier["reclaim_boundary"] = (
        barrier.get("zone_high") if side == "LONG" else barrier.get("zone_low")
    )
    if minimum_reward is not None and minimum_reward > 0:
        barrier["minimum_reward"] = round_trade_price(minimum_reward)
        barrier["minimum_rr"] = round(minimum_reward / risk, 2)
        barrier["below_minimum_reward"] = True
    return barrier


def _first_barrier_context(
    vrvp: Dict[str, Any],
    side: str,
    entry: float,
    risk: float,
    minimum_reward: float,
) -> Optional[Dict[str, Any]]:
    """Describe the first structural counter-barrier without activating a gate."""
    level = _nearest_target_level(vrvp, side, entry)
    if not level:
        return None
    price = _safe_float(level.get("price"))
    if price is None or price <= 0:
        return None
    zone_geometry = _barrier_zone_geometry(level, side, entry)
    if zone_geometry is None:
        return None
    distance = float(zone_geometry["distance"])
    distance_r = distance / risk
    distance_pct = distance / entry * 100.0
    provenance = vrvp.get("provenance") if isinstance(vrvp.get("provenance"), dict) else {}
    profile_source = vrvp.get("source") or OHLCV_VOLUME_PROFILE_SOURCE
    approximation = vrvp.get("approximation")
    if approximation is None and profile_source == OHLCV_VOLUME_PROFILE_SOURCE:
        approximation = True
    context = {
        "side": "resistance" if side == "LONG" else "support",
        "price": round_trade_price(price),
        "source": _level_source(level),
        "kind": _level_kind(level) or None,
        "timeframe": level.get("timeframe") or vrvp.get("timeframe"),
        "source_family": level.get("source_family") or "vrvp",
        "profile_id": level.get("profile_id") or vrvp.get("profile_id"),
        "independence_key": level.get("independence_key"),
        "zone_id": level.get("zone_id"),
        "zone_low": zone_geometry["zone_low"],
        "zone_high": zone_geometry["zone_high"],
        "entry_boundary": zone_geometry["entry_boundary"],
        "distance_basis": zone_geometry["distance_basis"],
        "entry_inside_zone": zone_geometry["entry_inside_zone"],
        "confirmed_at": level.get("confirmed_at"),
        "data_cutoff_at": level.get("data_cutoff_at") or vrvp.get("as_of"),
        "causal_structure_validated": level.get("causal_structure_validated") is True,
        "distance_pct": round(distance_pct, 2),
        "distance_r": round(distance_r, 2),
        "strength": round(float(_safe_float(level.get("weight"), 1.0) or 1.0), 2),
        "minimum_reward": round_trade_price(minimum_reward),
        "minimum_rr": round(minimum_reward / risk, 2),
        "below_minimum_reward": bool(
            distance + max(math.ulp(entry) * 16, entry * 1e-12) < minimum_reward
        ),
        "structural": True,
        "profile_source": profile_source,
        "profile_approximation": approximation if isinstance(approximation, bool) else None,
        "profile_method": vrvp.get("profile_method") or provenance.get("method"),
    }
    context["reclaim_boundary"] = (
        context.get("zone_high") if side == "LONG" else context.get("zone_low")
    )
    return context


def _attach_barrier_gate(enriched: Dict[str, Any], barrier: Optional[Dict[str, Any]], side: str) -> None:
    if not barrier:
        return
    key = "overhead_resistance" if side == "LONG" else "underlying_support"
    flag = "near_overhead_resistance" if side == "LONG" else "near_underlying_support"
    label = "Resistance erst brechen/reclaimen" if side == "LONG" else "Support erst brechen/reclaimen"
    enriched["nearest_barrier"] = barrier
    enriched[key] = barrier
    enriched["barrier_gate"] = barrier.get("action")
    enriched["barrier_gate_reason"] = label
    flags = list(enriched.get("risk_flags") or [])
    flags.append(flag)
    enriched["risk_flags"] = list(dict.fromkeys(flags))
    notes = list(enriched.get("notes") or [])
    notes.append(
        f"Nahe {barrier.get('side')} {barrier.get('price')} ({barrier.get('timeframe') or 'VRVP'}, "
        f"{barrier.get('distance_r')}R) - {label}"
    )
    enriched["notes"] = list(dict.fromkeys(notes))

    prior_status = str(enriched.get("structure_status") or "").strip().upper()
    if prior_status and prior_status not in {"ACCEPT", "WAIT_BREAK_RECLAIM"}:
        # A new barrier can add evidence to an already rejected/unavailable
        # setup, but WAIT must never promote or replace that stronger state.
        enriched["barrier_gate_active"] = True
        return

    enriched["structure_status"] = "WAIT_BREAK_RECLAIM"
    enriched["structure_reason"] = "first_opposing_barrier_before_minimum_reward"
    enriched["barrier_gate_active"] = True
    enriched["trade_action"] = "WAIT_FOR_BREAK_RECLAIM"
    enriched["entry_status"] = "WAIT_FOR_BREAK_RECLAIM"
    enriched["signal_quality"] = "wait_trigger"
    decision = dict(enriched.get("structure_decision") or {})
    decision.setdefault("model", "structure_decision_v1")
    decision.update({
        "status": "WAIT_BREAK_RECLAIM",
        "reason": "first_opposing_barrier_before_minimum_reward",
        "direction": side,
        "nearest_barrier": dict(barrier),
        "barrier_r": barrier.get("distance_r"),
        "target1": barrier.get("price"),
        "barrier_gate": barrier.get("action"),
    })
    enriched["structure_decision"] = decision

    # A starter plan and an active first-barrier gate are mutually exclusive.
    # Remove every compatibility alias so no downstream surface can still
    # present an anticipation entry while the canonical structure says WAIT.
    for field in (
        "starter_plan",
        "starter_entry",
        "early_entry",
        "starter_stop",
        "starter_tp1",
        "starter_tp2",
        "entry_plan_type",
    ):
        enriched.pop(field, None)


def _sync_accepted_barrier_state(
    enriched: Dict[str, Any], barrier: Dict[str, Any], side: str
) -> None:
    """Keep accepted structure metadata internally consistent without trading it."""
    enriched["barrier_gate"] = None
    enriched.pop("barrier_gate_reason", None)
    enriched["barrier_gate_active"] = False
    prior_status = str(enriched.get("structure_status") or "").strip().upper()
    if prior_status and prior_status not in {"ACCEPT", "WAIT_BREAK_RECLAIM"}:
        # Resolving a VRVP barrier is not permission to overwrite an unrelated
        # causal-data REJECT/UNAVAILABLE/BLOCKED decision with ACCEPT.
        return
    enriched["structure_status"] = "ACCEPT"
    enriched["structure_reason"] = "first_opposing_barrier_is_tradable_tp1"
    decision = dict(enriched.get("structure_decision") or {})
    decision.setdefault("model", "structure_decision_v1")
    decision.update({
        "status": "ACCEPT",
        "reason": "first_opposing_barrier_is_tradable_tp1",
        "direction": side,
        "nearest_barrier": dict(barrier),
        "barrier_r": barrier.get("distance_r"),
        "target1": barrier.get("price"),
        "barrier_gate": None,
    })
    enriched["structure_decision"] = decision

    # Structural acceptance only removes values that were themselves produced
    # by the old barrier gate.  It must never manufacture TRADE_NOW or erase an
    # unrelated execution decision.
    for field, stale_value in (
        ("trade_action", "WAIT_FOR_BREAK_RECLAIM"),
        ("entry_status", "WAIT_FOR_BREAK_RECLAIM"),
        ("signal_quality", "wait_trigger"),
    ):
        if enriched.get(field) == stale_value:
            enriched.pop(field, None)


def _target_on_trade_side(price: Optional[float], entry: float, side: str) -> bool:
    if price is None or price <= 0:
        return False
    return price > entry if side == "LONG" else price < entry


def _barrier_target_candidate(
    barrier: Dict[str, Any],
    *,
    source_family: str,
) -> Optional[Dict[str, Any]]:
    price = _safe_float(barrier.get("price"))
    if price is None or price <= 0:
        return None
    family = str(
        barrier.get("source_family")
        or barrier.get("canonical_source_family")
        or source_family
    ).strip()
    timeframe = str(barrier.get("timeframe") or "").strip()
    zone_id = str(barrier.get("zone_id") or "").strip() or None
    confirmed_at = barrier.get("confirmed_at")
    if not zone_id or not timeframe or not confirmed_at:
        return None
    independence_key = str(barrier.get("independence_key") or "").strip() or None
    if independence_key is None and zone_id and timeframe:
        independence_key = f"{family}:{timeframe}:{zone_id}"
    return {
        "price": price,
        "source": str(barrier.get("source") or "existing confirmed structural barrier"),
        "kind": barrier.get("kind"),
        "source_family": family,
        "timeframe": timeframe or None,
        "independence_key": independence_key,
        "profile_id": barrier.get("profile_id"),
        "zone_id": zone_id,
        "zone_low": _safe_float(barrier.get("zone_low", barrier.get("lower"))),
        "zone_high": _safe_float(barrier.get("zone_high", barrier.get("upper"))),
        "confirmed_at": confirmed_at,
        "data_cutoff_at": barrier.get("data_cutoff_at"),
        "causal_structure_validated": barrier.get("causal_structure_validated") is True,
        "strength": _safe_float(barrier.get("strength"), 1.0) or 1.0,
    }


def _has_causal_zone_identity(candidate: Optional[Dict[str, Any]]) -> bool:
    """A structural claim needs an exact zone, timeframe and confirmation time."""
    if not isinstance(candidate, dict):
        return False
    return bool(
        str(candidate.get("zone_id") or "").strip()
        and str(candidate.get("timeframe") or "").strip()
        and candidate.get("confirmed_at")
    )


def _protected_existing_tp1_candidate(
    setup: Dict[str, Any], tp1: Optional[float], entry: float, side: str
) -> Optional[Dict[str, Any]]:
    """Keep an existing TP1 only when its causal structural identity survived."""
    if setup.get("tp1_is_projection") is not False or not _target_on_trade_side(tp1, entry, side):
        return None
    timeframe = str(setup.get("tp1_timeframe") or "").strip() or None
    family = str(setup.get("tp1_source_family") or "existing_confirmed_target").strip()
    zone_id = str(setup.get("tp1_zone_id") or "").strip() or None
    confirmed_at = setup.get("tp1_confirmed_at")
    if not zone_id or not timeframe or not confirmed_at:
        return None
    independence_key = str(setup.get("tp1_independence_key") or "").strip() or None
    if independence_key is None and zone_id and timeframe:
        independence_key = f"{family}:{timeframe}:{zone_id}"
    return {
        "price": float(tp1),
        "source": str(setup.get("tp1_source") or "existing confirmed TP1"),
        "kind": setup.get("tp1_kind"),
        "source_family": family,
        "timeframe": timeframe,
        "independence_key": independence_key,
        "zone_id": zone_id,
        "zone_low": _safe_float(setup.get("tp1_zone_low")),
        "zone_high": _safe_float(setup.get("tp1_zone_high")),
        "confirmed_at": confirmed_at,
        "data_cutoff_at": setup.get("tp1_data_cutoff_at"),
        "causal_structure_validated": True,
        "protected_existing_tp1": True,
    }


def _existing_tp2_candidate(
    setup: Dict[str, Any], tp2: Optional[float], entry: float, side: str
) -> Optional[Dict[str, Any]]:
    """Accept a pre-existing TP2 as structural only with explicit identity."""
    if setup.get("tp2_is_projection") is not False or not _target_on_trade_side(tp2, entry, side):
        return None
    source = str(setup.get("tp2_source") or "").strip()
    timeframe = str(setup.get("tp2_timeframe") or "").strip()
    family = str(setup.get("tp2_source_family") or "").strip()
    zone_id = str(setup.get("tp2_zone_id") or "").strip() or None
    confirmed_at = setup.get("tp2_confirmed_at")
    independence_key = str(setup.get("tp2_independence_key") or "").strip() or None
    if independence_key is None and zone_id and timeframe and family:
        independence_key = f"{family}:{timeframe}:{zone_id}"
    if (
        not source
        or not timeframe
        or not family
        or not independence_key
        or not zone_id
        or not confirmed_at
    ):
        return None
    return {
        "price": float(tp2),
        "source": source,
        "kind": setup.get("tp2_kind"),
        "source_family": family,
        "timeframe": timeframe,
        "independence_key": independence_key,
        "zone_id": zone_id,
        "zone_low": _safe_float(setup.get("tp2_zone_low")),
        "zone_high": _safe_float(setup.get("tp2_zone_high")),
        "confirmed_at": confirmed_at,
        "data_cutoff_at": setup.get("tp2_data_cutoff_at"),
        "causal_structure_validated": True,
    }


def _targets_are_independent(
    first: Dict[str, Any], second: Dict[str, Any]
) -> bool:
    """Price separation alone is never structural independence."""
    required = ("source_family", "timeframe", "independence_key")
    if any(not str(first.get(key) or "").strip() for key in required):
        return False
    if any(not str(second.get(key) or "").strip() for key in required):
        return False
    first_key = str(first.get("independence_key"))
    second_key = str(second.get("independence_key"))
    if first_key == second_key:
        return False
    first_zone = str(first.get("zone_id") or "").strip()
    second_zone = str(second.get("zone_id") or "").strip()
    return not (first_zone and second_zone and first_zone == second_zone)


def _persist_target_provenance(
    setup: Dict[str, Any], prefix: str, candidate: Dict[str, Any]
) -> None:
    for suffix, key in (
        ("source_family", "source_family"),
        ("timeframe", "timeframe"),
        ("independence_key", "independence_key"),
        ("zone_id", "zone_id"),
        ("zone_low", "zone_low"),
        ("zone_high", "zone_high"),
        ("confirmed_at", "confirmed_at"),
        ("data_cutoff_at", "data_cutoff_at"),
    ):
        value = candidate.get(key)
        if value not in (None, ""):
            setup[f"{prefix}_{suffix}"] = value


def apply_vrvp_to_trade_setup(
    setup: Dict[str, Any],
    vrvp: Optional[Dict[str, Any]],
    *,
    direction: Optional[str] = None,
    asset_type: str = "stock",
    atr: Optional[float] = None,
) -> Dict[str, Any]:
    """Return setup enriched with VRVP support/resistance where it improves structure."""
    if not isinstance(setup, dict):
        return setup
    enriched = dict(setup)
    if not vrvp:
        enriched["vrvp_applied"] = False
        return enriched

    side = str(direction or enriched.get("direction") or "").upper()
    entry = _get_level(enriched, "entry", "Entry")
    stop = _get_level(enriched, "stop", "StopLoss", "stop_loss")
    tp1 = _get_level(enriched, "tp1", "TP1", "target1")
    tp2 = _get_level(enriched, "tp2", "TP2", "target2")
    if side not in ("LONG", "SHORT") and entry is not None and stop is not None:
        side = "LONG" if stop < entry else "SHORT"
    if side not in ("LONG", "SHORT") or entry is None or stop is None or entry <= 0 or stop <= 0:
        enriched["vrvp_applied"] = False
        return enriched

    risk = (entry - stop) if side == "LONG" else (stop - entry)
    if risk <= 0:
        enriched["vrvp_applied"] = False
        return enriched

    profile = _asset_profile(asset_type)
    atr_value = _safe_float(atr, 0.0) or 0.0
    if atr_value < 0 or atr_value > entry * 0.50:
        enriched["vrvp_atr_warning"] = "implausible_atr_ignored"
        atr_value = 0.0
    used: List[str] = []
    # Stop only moves to a nearby VRVP invalidation zone when it does not widen
    # risk too aggressively. Otherwise we keep the existing structure stop.
    stop_candidates = _candidate_prices(vrvp, side, target=False)
    if stop_candidates:
        if side == "LONG":
            valid_stops = [(p, s) for p, s in stop_candidates if p < entry]
            valid_stops.sort(key=lambda x: x[0], reverse=True)
            for support, source in valid_stops:
                proposed = support - max(entry * profile["stop_buffer_pct"], atr_value * 0.35)
                new_risk = entry - proposed
                if new_risk >= risk * 0.80 and new_risk <= risk * profile["max_stop_mult"]:
                    stop = proposed
                    enriched["stop_source"] = f"{source} invalidation"
                    used.append("stop")
                    break
        else:
            valid_stops = [(p, s) for p, s in stop_candidates if p > entry]
            valid_stops.sort(key=lambda x: x[0])
            for resistance, source in valid_stops:
                proposed = resistance + max(entry * profile["stop_buffer_pct"], atr_value * 0.35)
                new_risk = proposed - entry
                if new_risk >= risk * 0.80 and new_risk <= risk * profile["max_stop_mult"]:
                    stop = proposed
                    enriched["stop_source"] = f"{source} invalidation"
                    used.append("stop")
                    break
        risk = (entry - stop) if side == "LONG" else (stop - entry)

    # Recalculate all reward floors after a possible structural stop change.
    # Otherwise the gate could compare the first barrier with a stale R unit.
    min_tp_reward = max(risk * 1.5, entry * profile["min_tp_pct"], atr_value * 0.70)

    vrvp_first_barrier = _first_barrier_context(vrvp, side, entry, risk, min_tp_reward)
    if vrvp_first_barrier:
        enriched["vrvp_first_barrier"] = vrvp_first_barrier

    # A setup can already carry a confirmed D/W/4H barrier from the shared
    # level-zone engine. VRVP is another evidence family, not permission to
    # jump over that closer barrier.
    existing_raw = enriched.get("nearest_barrier")
    existing_barrier: Optional[Dict[str, Any]] = None
    if isinstance(existing_raw, dict) and existing_raw.get("structural") is not False:
        existing_price = _safe_float(existing_raw.get("price"))
        existing_side = str(existing_raw.get("side") or "").lower()
        expected_side = "resistance" if side == "LONG" else "support"
        existing_zone_geometry = _barrier_zone_geometry(existing_raw, side, entry)
        if (
            existing_price is not None
            and existing_zone_geometry is not None
            and existing_side == expected_side
        ):
            existing_barrier = dict(existing_raw)
            existing_barrier["price"] = round_trade_price(existing_price)
            existing_distance = float(existing_zone_geometry["distance"])
            existing_barrier["zone_low"] = existing_zone_geometry["zone_low"]
            existing_barrier["zone_high"] = existing_zone_geometry["zone_high"]
            existing_barrier["entry_boundary"] = existing_zone_geometry["entry_boundary"]
            existing_barrier["distance_basis"] = existing_zone_geometry["distance_basis"]
            existing_barrier["entry_inside_zone"] = existing_zone_geometry["entry_inside_zone"]
            existing_barrier["distance_r"] = round(existing_distance / risk, 2)
            existing_barrier["distance_pct"] = round(existing_distance / entry * 100.0, 2)
            existing_barrier["minimum_reward"] = round_trade_price(min_tp_reward)
            existing_barrier["minimum_rr"] = round(min_tp_reward / risk, 2)
            existing_barrier["below_minimum_reward"] = bool(
                existing_distance + max(math.ulp(entry) * 16, entry * 1e-12)
                < min_tp_reward
            )
            existing_barrier.setdefault("source_family", "level_zone")
            if not existing_barrier.get("independence_key"):
                zone_id = str(existing_barrier.get("zone_id") or "").strip()
                barrier_tf = str(existing_barrier.get("timeframe") or "").strip()
                if zone_id and barrier_tf:
                    existing_barrier["independence_key"] = f"level_zone:{barrier_tf}:{zone_id}"
            if not existing_barrier.get("reclaim_boundary"):
                existing_barrier["reclaim_boundary"] = (
                    existing_barrier.get("zone_high")
                    if side == "LONG"
                    else existing_barrier.get("zone_low")
                )

    canonical_barrier: Optional[Dict[str, Any]] = None
    canonical_source = ""
    for source_name, candidate in (
        ("level_zone", existing_barrier),
        ("vrvp", vrvp_first_barrier),
    ):
        if not candidate:
            continue
        candidate_geometry = _barrier_zone_geometry(candidate, side, entry)
        if candidate_geometry is None:
            continue
        candidate_distance = float(candidate_geometry["distance"])
        canonical_geometry = (
            _barrier_zone_geometry(canonical_barrier, side, entry)
            if canonical_barrier is not None
            else None
        )
        if (
            canonical_barrier is None
            or canonical_geometry is None
            or candidate_distance < float(canonical_geometry["distance"])
        ):
            canonical_barrier = dict(candidate)
            canonical_source = source_name

    tolerance = max(entry * 1e-12, math.ulp(entry) * 16)
    protected_tp1 = _protected_existing_tp1_candidate(enriched, tp1, entry, side)
    existing_identifies_tp1 = bool(
        existing_barrier
        and _has_causal_zone_identity(existing_barrier)
        and tp1 is not None
        and abs((_safe_float(existing_barrier.get("price"), entry) or entry) - tp1)
        <= tolerance
    )
    unverified_structural_tp1_claim = bool(
        enriched.get("tp1_is_projection") is False
        and _target_on_trade_side(tp1, entry, side)
        and protected_tp1 is None
        and not existing_identifies_tp1
    )
    unverified_tp1_price = tp1 if unverified_structural_tp1_claim else None
    if unverified_structural_tp1_claim:
        # A boolean label is not structural evidence.  Remove the unverified
        # target from selection so a distant price cannot survive merely by
        # declaring ``tp1_is_projection=False``.
        tp1 = None
    canonical_target = (
        _barrier_target_candidate(canonical_barrier, source_family=canonical_source)
        if canonical_barrier is not None
        else None
    )
    if canonical_target is not None and not _target_on_trade_side(
        _safe_float(canonical_target.get("price")), entry, side
    ):
        # An overlapping zone is still an immediate gate, but its representative
        # point can sit behind the entry and therefore cannot be a valid TP.
        canonical_target = None
    canonical_geometry = (
        _barrier_zone_geometry(canonical_barrier, side, entry)
        if canonical_barrier is not None
        else None
    )
    protected_precedes_barrier = bool(
        protected_tp1
        and (
            canonical_barrier is None
            or abs(float(protected_tp1["price"]) - entry) + tolerance
            < float((canonical_geometry or {"distance": float("inf")})["distance"])
        )
    )

    # Only the barrier that actually is TP1 may activate/clear a gate. A farther
    # VRVP level cannot supersede an explicitly confirmed closer TP1 merely
    # because the caller omitted ``nearest_barrier``.
    active_canonical_barrier = None if protected_precedes_barrier else canonical_barrier
    if active_canonical_barrier is not None:
        below_minimum = bool(active_canonical_barrier.get("below_minimum_reward"))
        active_canonical_barrier["action"] = (
            "BREAK_RECLAIM_REQUIRED" if side == "LONG" else "BREAK_SUPPORT_REQUIRED"
        ) if below_minimum else None
        active_canonical_barrier["reclaimed"] = bool(
            active_canonical_barrier.get("reclaimed", False)
        )
        active_canonical_barrier["structural"] = True
        active_canonical_barrier["canonical_source_family"] = canonical_source
        enriched["nearest_barrier"] = active_canonical_barrier
        barrier_key = "overhead_resistance" if side == "LONG" else "underlying_support"
        enriched[barrier_key] = active_canonical_barrier
        if below_minimum:
            _attach_barrier_gate(enriched, active_canonical_barrier, side)
        else:
            _sync_accepted_barrier_state(enriched, active_canonical_barrier, side)
            stale_flags = {"near_overhead_resistance", "near_underlying_support"}
            enriched["risk_flags"] = [
                flag for flag in (enriched.get("risk_flags") or [])
                if flag not in stale_flags
            ]

    if unverified_structural_tp1_claim:
        claim = {
            "price": round_trade_price(unverified_tp1_price),
            "source": str(enriched.get("tp1_source") or "unverified existing TP1"),
            "zone_id": enriched.get("tp1_zone_id"),
            "timeframe": enriched.get("tp1_timeframe"),
            "confirmed_at": enriched.get("tp1_confirmed_at"),
            "causal_structure_validated": False,
        }
        enriched["unverified_tp1_claim"] = claim
        enriched["tp1_is_projection"] = True
        enriched["tp1_structure"] = "unverified_projection_claim"
        enriched["target_quality"] = "PROJECTION_ONLY_NO_CAUSAL_IDENTITY"
        enriched["structure_status"] = "REJECT"
        enriched["structure_reason"] = "tp1_marked_structural_without_causal_identity"
        enriched["trade_action"] = "NO_TRADE"
        enriched["entry_status"] = "NO_TRADE"
        enriched["signal_quality"] = "blocked_structure"
        flags = list(enriched.get("risk_flags") or [])
        flags.append("unverified_structural_tp1_claim")
        enriched["risk_flags"] = list(dict.fromkeys(flags))
        if active_canonical_barrier is None:
            enriched["barrier_gate"] = "CAUSAL_BARRIER_METADATA_REQUIRED"
            enriched["barrier_gate_active"] = True
            enriched["barrier_gate_reason"] = "tp1_missing_causal_zone_identity"
        decision = dict(enriched.get("structure_decision") or {})
        decision.update({
            "model": decision.get("model") or "structure_decision_v1",
            "status": "REJECT",
            "reason": enriched["structure_reason"],
            "direction": side,
            "unverified_tp1_claim": dict(claim),
            "target1": claim["price"],
            "barrier_gate": enriched.get("barrier_gate"),
        })
        enriched["structure_decision"] = decision

    if protected_precedes_barrier and protected_tp1 is not None:
        protected_price = float(protected_tp1["price"])
        protected_geometry = _barrier_zone_geometry(protected_tp1, side, entry)
        protected_distance = float(
            (protected_geometry or {"distance": abs(protected_price - entry)})["distance"]
        )
        if protected_distance + tolerance < min_tp_reward:
            protected_barrier = {
                **protected_tp1,
                "side": "resistance" if side == "LONG" else "support",
                "price": round_trade_price(protected_price),
                "distance_pct": round(protected_distance / entry * 100.0, 2),
                "distance_r": round(protected_distance / risk, 2),
                "minimum_reward": round_trade_price(min_tp_reward),
                "minimum_rr": round(min_tp_reward / risk, 2),
                "below_minimum_reward": True,
                "structural": True,
                "reclaimed": False,
                "canonical_source_family": "existing_confirmed_target",
            }
            if protected_geometry is not None:
                protected_barrier.update({
                    "zone_low": protected_geometry["zone_low"],
                    "zone_high": protected_geometry["zone_high"],
                    "entry_boundary": protected_geometry["entry_boundary"],
                    "distance_basis": protected_geometry["distance_basis"],
                    "entry_inside_zone": protected_geometry["entry_inside_zone"],
                })
            protected_barrier["reclaim_boundary"] = (
                protected_barrier.get("zone_high")
                if side == "LONG"
                else protected_barrier.get("zone_low")
            )
            if protected_tp1.get("causal_structure_validated") is True:
                protected_barrier["action"] = (
                    "BREAK_RECLAIM_REQUIRED" if side == "LONG" else "BREAK_SUPPORT_REQUIRED"
                )
                _attach_barrier_gate(enriched, protected_barrier, side)

    target_candidates = _target_level_candidates(vrvp, side, entry)
    existing_target = (
        _barrier_target_candidate(existing_barrier, source_family="level_zone")
        if existing_barrier is not None
        else None
    )
    for candidate in (
        existing_target,
        protected_tp1,
        _existing_tp2_candidate(enriched, tp2, entry, side),
    ):
        if candidate:
            target_candidates.append(candidate)
    target_candidates.sort(
        key=lambda item: float(item["price"]),
        reverse=side == "SHORT",
    )

    selected_tp1: Optional[Dict[str, Any]] = None
    selected_tp2: Optional[Dict[str, Any]] = None
    if protected_precedes_barrier:
        selected_tp1 = protected_tp1
    elif canonical_target is not None:
        selected_tp1 = canonical_target
    elif target_candidates:
        selected_tp1 = target_candidates[0]

    if selected_tp1 is not None:
        independent_distance = max(entry * 0.0012, math.ulp(entry) * 16)
        first_price = float(selected_tp1["price"])
        for candidate in target_candidates:
            candidate_price = float(candidate["price"])
            beyond_tp1 = candidate_price > first_price if side == "LONG" else candidate_price < first_price
            if (
                beyond_tp1
                and abs(candidate_price - first_price) > independent_distance
                and _targets_are_independent(selected_tp1, candidate)
            ):
                selected_tp2 = candidate
                break

    selected_tp1_family = (
        str(selected_tp1.get("source_family") or "") if selected_tp1 else None
    )
    selected_tp2_family = (
        str(selected_tp2.get("source_family") or "") if selected_tp2 else None
    )
    if selected_tp1:
        tp1 = float(selected_tp1["price"])
        enriched["tp1_source"] = selected_tp1["source"]
        enriched["tp1_is_projection"] = False
        enriched["tp1_structure"] = f"first_opposing_{selected_tp1_family or 'structural'}_barrier"
        _persist_target_provenance(enriched, "tp1", selected_tp1)
        if selected_tp1_family == "vrvp":
            used.append("tp1")
    if selected_tp2:
        tp2 = float(selected_tp2["price"])
        enriched["tp2_source"] = selected_tp2["source"]
        enriched["tp2_is_projection"] = False
        enriched["tp2_structure"] = "next_independent_structural_barrier"
        _persist_target_provenance(enriched, "tp2", selected_tp2)
        if selected_tp2_family == "vrvp":
            used.append("tp2")

    # Preserve valid existing targets only when VRVP has no opposing structural
    # barrier.  A close real barrier must remain the honest TP1 and must never
    # be overwritten by a farther risk projection.
    if tp1 is None or (selected_tp1 is None and not _distance_ok(tp1, entry, max(risk * 1.5, entry * 0.006), side)):
        tp1 = entry + risk * 1.6 if side == "LONG" else max(0.00000001, entry - risk * 1.6)
        enriched["tp1_source"] = "risk fallback after VRVP validation"
        enriched["tp1_is_projection"] = True
        enriched["tp1_structure"] = "risk_projection_fallback"
    if selected_tp1 is not None and selected_tp2 is None:
        # There is no second independent VRVP structure.  Keep the geometry
        # valid with an explicit projection rather than pretending the level is
        # observed market structure.  The first-barrier gate remains intact.
        projected_reward = max(
            risk * 2.45,
            abs(tp1 - entry) + max(risk * 0.55, entry * 0.012),
        )
        tp2 = (
            entry + projected_reward
            if side == "LONG"
            else max(0.00000001, entry - projected_reward)
        )
        enriched["tp2_source"] = "projection fallback (no second independent structural barrier)"
        enriched["tp2_is_projection"] = True
        enriched["tp2_structure"] = "projection_after_first_structural_barrier"
        enriched["tp2_projection_reason"] = "no_second_independent_structural_barrier"
    elif selected_tp2 is None and (
        tp2 is None
        or not _distance_ok(
            tp2,
            entry,
            max(abs(tp1 - entry) + risk * 0.55, risk * 2.4, entry * 0.012),
            side,
        )
    ):
        tp2 = (
            entry + max(risk * 2.45, abs(tp1 - entry) * 1.35)
            if side == "LONG"
            else max(0.00000001, entry - max(risk * 2.45, abs(tp1 - entry) * 1.35))
        )
        enriched["tp2_source"] = "risk fallback after VRVP validation"
        enriched["tp2_is_projection"] = True
        enriched["tp2_structure"] = "risk_projection_fallback"

    geometry = trade_geometry(entry, stop, tp1, tp2, side)
    if not geometry["valid"]:
        enriched["vrvp_applied"] = False
        enriched["vrvp_geometry_errors"] = list(geometry.get("errors") or [])
        return enriched

    _set_level_aliases(enriched, "entry", entry)
    _set_level_aliases(enriched, "stop", stop)
    _set_level_aliases(enriched, "tp1", tp1)
    _set_level_aliases(enriched, "tp2", tp2)

    risk = float(geometry["risk"])
    rr_tp1 = float(geometry["rr_tp1"])
    rr_tp2 = float(geometry["rr_tp2"])
    rr = float(geometry["rr"])

    enriched["risk"] = round_trade_price(risk)
    enriched["rr"] = round(rr, 2)
    enriched["rr_tp1"] = round(rr_tp1, 2)
    enriched["rr_tp2"] = round(rr_tp2, 2)
    enriched["direction"] = side
    enriched["vrvp_applied"] = bool(used)
    enriched["vrvp_timeframe"] = vrvp.get("timeframe")
    enriched["vrvp_poc"] = vrvp.get("poc")
    enriched["vrvp_vah"] = vrvp.get("vah")
    enriched["vrvp_val"] = vrvp.get("val")
    enriched["vrvp_levels"] = {
        "supports": [lvl.get("rounded") for lvl in (vrvp.get("supports") or [])[:4]],
        "resistances": [lvl.get("rounded") for lvl in (vrvp.get("resistances") or [])[:4]],
    }
    old_model = str(enriched.get("level_model") or "structure_first_v2")
    if used and "vrvp" not in old_model.lower():
        enriched["level_model"] = f"{old_model}+vrvp"
    if selected_tp1_family == "vrvp" or selected_tp2_family == "vrvp":
        enriched["target_quality"] = "STRUCTURAL_VRVP"
    if used:
        notes = list(enriched.get("notes") or [])
        notes.append(f"VRVP {vrvp.get('timeframe')} als Support/Resistance-Konfluenz genutzt")
        enriched["notes"] = notes
    return enriched
