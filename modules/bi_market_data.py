"""Pure validation of BI's ascending, one-day stock aggregate responses.

An explicitly successful zero-result response may omit the optional results
array. Missing data never becomes a price, an indicator observation or a signal.
See https://massive.com/docs/rest/stocks/aggregates/custom-bars .
"""
from __future__ import annotations

import math


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


class BIAggregateDataError(ValueError):
    """Fixed diagnostic code only: never retain provider payloads or URLs."""

    def __init__(self, reason, *, field="unknown"):
        self.reason = reason if isinstance(reason, str) and reason in BI_DATA_ERROR_REASONS else "invalid_payload"
        self.field = field if isinstance(field, str) and field in BI_DATA_ERROR_FIELDS else "unknown"
        super().__init__(self.reason)


def parse_bi_daily_aggregates(payload):
    """Return validated bars, including a confirmed empty history.

This is deliberately not a generic multi-timespan aggregate parser. BI asks
for 1-day bars, ascending, over at most 320 days (below the 5000 default limit).
Existing explicit lists without optional metadata remain compatible. An absent
list needs positive evidence of success and zero results, not a blanket default.
"""
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
    for bar in bars:
        if not isinstance(bar, dict):
            raise BIAggregateDataError("invalid_bar_type", field="bar")
        values = [bar.get(key) for key in ("t", "o", "h", "l", "c", "v")]
        for key, value in zip(("t", "o", "h", "l", "c", "v"), values):
            try:
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value))
            except OverflowError:
                valid = False
            if not valid:
                raise BIAggregateDataError("invalid_bar_value", field=key)
        timestamp, open_, high, low, close, volume = values
        if timestamp <= last_timestamp:
            raise BIAggregateDataError("invalid_bar_timestamp", field="t")
        for key, value in zip(("o", "h", "l", "c", "v"), values[1:]):
            if value < 0 or (key != "v" and value == 0):
                raise BIAggregateDataError("invalid_bar_value", field=key)
        if high < max(open_, low, close) or low > min(open_, high, close):
            raise BIAggregateDataError("invalid_bar_geometry", field="bar")
        last_timestamp = timestamp
    return bars
