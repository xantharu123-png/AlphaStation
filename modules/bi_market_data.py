"""Pure validation of BI's ascending, one-day stock aggregate responses.

An explicitly successful zero-result response may omit the optional results
array. Missing data never becomes a price, an indicator observation or a signal.
See https://massive.com/docs/rest/stocks/aggregates/custom-bars .
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


BI_DATA_ERROR_REASONS = frozenset({
    "invalid_payload", "provider_status", "invalid_json", "missing_results",
    "invalid_results_type", "invalid_result_count", "invalid_query_count",
    "result_count_mismatch", "contradictory_empty_response", "unexpected_pagination",
    "invalid_bar_type", "invalid_bar_value", "invalid_bar_geometry",
    "invalid_bar_timestamp", "invalid_data_conversion",
})
BI_ISOLATABLE_BAR_ERRORS = frozenset({
    "invalid_bar_type", "invalid_bar_value", "invalid_bar_geometry",
    "invalid_bar_timestamp", "invalid_data_conversion",
})
BI_DATA_ERROR_FIELDS = frozenset({"t", "o", "h", "l", "c", "v", "bar", "unknown"})
BI_DATA_ERROR_VALUE_CLASSES = frozenset({
    "missing", "null", "boolean", "non_numeric", "non_finite",
    "zero_price", "negative_price", "negative_volume", "nonpositive_timestamp",
    "nonascending_timestamp", "timestamp_out_of_range", "future_timestamp",
    "invalid_geometry", "unknown",
})
BI_DATA_ERROR_POSITIONS = frozenset({"only", "first", "interior", "last", "unknown"})


class BIAggregateDataError(ValueError):
    """Fixed diagnostic code only: never retain provider payloads or URLs."""

    def __init__(self, reason, *, field="unknown", value_class="unknown", position="unknown"):
        self.reason = reason if isinstance(reason, str) and reason in BI_DATA_ERROR_REASONS else "invalid_payload"
        self.field = field if isinstance(field, str) and field in BI_DATA_ERROR_FIELDS else "unknown"
        self.value_class = value_class if isinstance(value_class, str) and value_class in BI_DATA_ERROR_VALUE_CLASSES else "unknown"
        self.position = position if isinstance(position, str) and position in BI_DATA_ERROR_POSITIONS else "unknown"
        super().__init__(self.reason)


def parse_bi_daily_aggregates(payload, *, completed_through=None, as_of=None):
    """Return validated bars, including a confirmed empty history.

This is deliberately not a generic multi-timespan aggregate parser. BI asks
for 1-day bars, ascending, over at most 320 days (below the 5000 default limit).
Existing explicit lists without optional metadata remain compatible. An absent
list needs positive evidence of success and zero results, not a blanket default.

For completed-daily swing analysis, the caller may supply the last available
exchange session (including its provider delay) and a fixed aware analysis
clock. Later, not-yet-eligible sessions are excluded BEFORE validating OHLCV:
their prices cannot enter this analysis. Envelope, timestamps, ascending order
and future bounds are still validated for every returned bar. The default
live-price path continues to require valid OHLCV on every bar.
"""
    cutoff = None
    if completed_through is not None:
        if not isinstance(completed_through, str):
            raise ValueError("BI completed session must be an ISO date")
        cutoff = date.fromisoformat(completed_through)
        if (not isinstance(as_of, datetime) or as_of.tzinfo is None
                or as_of.utcoffset() is None):
            raise ValueError("BI completed session requires an aware analysis clock")
        if cutoff > as_of.astimezone(ZoneInfo("America/New_York")).date():
            raise ValueError("BI completed session cannot be in the future")
    elif as_of is not None:
        raise ValueError("BI analysis clock requires a completed session")
    if not isinstance(payload, dict):
        raise BIAggregateDataError("invalid_payload")
    status = str(payload.get("status") or "").upper()
    if status not in {"", "OK", "DELAYED"}:
        raise BIAggregateDataError("provider_status")
    for key, reason in (("resultsCount", "invalid_result_count"),
                        ("queryCount", "invalid_query_count")):
        if key in payload and (type(payload[key]) is not int or payload[key] < 0):
            raise BIAggregateDataError(reason)
    if payload.get("next_url") not in (None, ""):
        # A silently truncated history must not produce a complete BI result.
        raise BIAggregateDataError("unexpected_pagination")

    if "results" not in payload:
        if status not in {"OK", "DELAYED"} or payload.get("resultsCount") != 0:
            raise BIAggregateDataError("missing_results")
        if payload.get("queryCount", 0) != 0:
            raise BIAggregateDataError("contradictory_empty_response")
        return []

    bars = payload["results"]
    if not isinstance(bars, list):
        raise BIAggregateDataError("invalid_results_type")
    if "resultsCount" in payload and payload["resultsCount"] != len(bars):
        raise BIAggregateDataError("result_count_mismatch")
    if not bars and payload.get("queryCount", 0) != 0:
        raise BIAggregateDataError("contradictory_empty_response")
    if "queryCount" in payload and payload["queryCount"] < len(bars):
        raise BIAggregateDataError("invalid_query_count")

    last_timestamp = 0
    selected = []
    for index, bar in enumerate(bars):
        position = ("only" if len(bars) == 1 else "first" if index == 0
                    else "last" if index == len(bars) - 1 else "interior")
        if not isinstance(bar, dict):
            raise BIAggregateDataError("invalid_bar_type", field="bar", position=position)

        def number(key):
            value = bar.get(key)
            problem = ("missing" if key not in bar else "null" if value is None
                       else "boolean" if isinstance(value, bool)
                       else "non_numeric" if not isinstance(value, (int, float)) else None)
            try:
                if problem is None and not math.isfinite(value):
                    problem = "non_finite"
            except OverflowError:
                problem = "non_finite"
            if problem is not None:
                raise BIAggregateDataError("invalid_bar_value", field=key,
                                          value_class=problem, position=position)
            return value

        timestamp = number("t")
        if timestamp <= last_timestamp:
            raise BIAggregateDataError("invalid_bar_timestamp", field="t", position=position,
                                      value_class="nonpositive_timestamp" if timestamp <= 0 else "nonascending_timestamp")
        last_timestamp = timestamp
        if cutoff is not None:
            try:
                observed = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
            except (ValueError, OverflowError, OSError):
                raise BIAggregateDataError("invalid_data_conversion", field="t",
                                          value_class="timestamp_out_of_range", position=position) from None
            if observed > as_of:
                raise BIAggregateDataError("invalid_bar_timestamp", field="t",
                                          value_class="future_timestamp", position=position)
            if observed.astimezone(ZoneInfo("America/New_York")).date() > cutoff:
                continue

        values = [number(key) for key in ("o", "h", "l", "c", "v")]
        open_, high, low, close, volume = values
        for key, value in zip(("o", "h", "l", "c", "v"), values):
            if value < 0 or (key != "v" and value == 0):
                problem = "negative_volume" if key == "v" else "zero_price" if value == 0 else "negative_price"
                raise BIAggregateDataError("invalid_bar_value", field=key,
                                          value_class=problem, position=position)
        if high < max(open_, low, close) or low > min(open_, high, close):
            raise BIAggregateDataError("invalid_bar_geometry", field="bar",
                                      value_class="invalid_geometry", position=position)
        selected.append(bar)
    return selected if cutoff is not None else bars
