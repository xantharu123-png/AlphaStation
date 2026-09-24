"""Offline completed-session selection before BI price validation, not imputation."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from modules.bi_market_data import BIAggregateDataError, parse_bi_daily_aggregates
from modules import stock_swing_contract as swing
from test_bi_market_data import _lifecycle
from test_bi_deep_fixes_scan import _attach_ts, _flat_bars, _to_polygon
from test_bi_diagnostics_integration import _result

NY = ZoneInfo("America/New_York")


def _bar(session, **changes):
    stamp = datetime.combine(date.fromisoformat(session), datetime.min.time(), tzinfo=NY)
    return dict(t=int(stamp.timestamp() * 1000), o=10, h=12, l=9, c=11, v=100, **changes)


@pytest.mark.parametrize("field", ["o", "h", "l", "c", "v"])
@pytest.mark.parametrize("value", [None, 0, -1, "PRIVATE", float("nan")])
def test_uncompleted_prices_are_never_consumed_or_required(field, value):
    first = _bar("2026-09-22")
    current = _bar("2026-09-23")
    current[field] = value
    payload = {"results": [first, current], "resultsCount": 2, "queryCount": 2}
    result = parse_bi_daily_aggregates(
        payload, completed_through="2026-09-22", as_of=datetime(2026, 9, 23, 18, tzinfo=timezone.utc),
    )
    assert result == [first] and result[0] is first
    assert payload["results"][-1] is current  # No price repair or payload mutation.


@pytest.mark.parametrize("session,minute,eligible", [
    ("2026-09-23", 14, False), ("2026-09-23", 15, True),
    ("2026-11-27", 14, False), ("2026-11-27", 15, True),
    ("2026-01-09", 14, False), ("2026-01-09", 15, True),
])
def test_exchange_close_plus_delay_governs_validation_including_dst_and_early_close(session, minute, eligible):
    now = swing.session_close(session) + timedelta(minutes=minute)
    latest = swing.completed_sessions(now, 1)[0]
    bar = _bar(session)
    bar["o"] = 0
    if eligible:
        with pytest.raises(BIAggregateDataError) as caught:
            parse_bi_daily_aggregates({"results": [bar]}, completed_through=latest, as_of=now)
        assert caught.value.field == "o" and caught.value.value_class == "zero_price"
    else:
        assert parse_bi_daily_aggregates({"results": [bar]}, completed_through=latest, as_of=now) == []


@pytest.mark.parametrize("corruption,reason", [
    ("future", "invalid_bar_timestamp"), ("missing_t", "invalid_bar_value"),
    ("invalid_t", "invalid_bar_value"), ("descending", "invalid_bar_timestamp"),
    ("duplicate", "invalid_bar_timestamp"), ("nonobject", "invalid_bar_type"),
    ("count", "result_count_mismatch"), ("pagination", "unexpected_pagination"),
])
def test_partial_selection_cannot_hide_response_or_timestamp_corruption(corruption, reason):
    payload = {"results": [_bar("2026-09-22"), _bar("2026-09-23")]}
    current = payload["results"][-1]
    current["o"] = None
    if corruption == "future":
        current["t"] = _bar("2026-09-24")["t"]
    elif corruption == "missing_t":
        del current["t"]
    elif corruption == "invalid_t":
        current["t"] = "PRIVATE"
    elif corruption == "descending":
        payload["results"].reverse()
    elif corruption == "duplicate":
        payload["results"].append(dict(current))
    elif corruption == "nonobject":
        payload["results"][-1] = None
    elif corruption == "count":
        payload["resultsCount"] = 1
    elif corruption == "pagination":
        payload["next_url"] = "https://PRIVATE"
    with pytest.raises(BIAggregateDataError) as caught:
        parse_bi_daily_aggregates(payload, completed_through="2026-09-22",
                                  as_of=datetime(2026, 9, 23, 18, tzinfo=timezone.utc))
    assert caught.value.reason == reason
    assert "PRIVATE" not in repr(vars(caught.value))


@pytest.mark.parametrize("value,classification", [
    (None, "null"), (True, "boolean"), ("PRIVATE_TOKEN", "non_numeric"),
    (float("nan"), "non_finite"), (float("inf"), "non_finite"),
    (10 ** 400, "non_finite"), (0, "zero_price"), (-1, "negative_price"),
])
def test_completed_bad_open_is_still_rejected_with_safe_classification(value, classification):
    bar = _bar("2026-09-22")
    bar["o"] = value
    with pytest.raises(BIAggregateDataError) as caught:
        parse_bi_daily_aggregates({"results": [bar]}, completed_through="2026-09-22",
                                  as_of=datetime(2026, 9, 23, 18, tzinfo=timezone.utc))
    assert caught.value.reason == "invalid_bar_value"
    assert caught.value.field == "o"
    assert caught.value.value_class == classification
    assert caught.value.position == "only"
    assert "PRIVATE" not in repr(vars(caught.value))


def test_missing_field_and_untrusted_metadata_are_classified_without_values():
    bar = _bar("2026-09-22")
    del bar["o"]
    with pytest.raises(BIAggregateDataError) as caught:
        parse_bi_daily_aggregates({"results": [bar]})
    assert caught.value.value_class == "missing"
    invalid = BIAggregateDataError("PRIVATE", field="PRIVATE", value_class="PRIVATE", position="PRIVATE")
    assert "PRIVATE" not in repr(vars(invalid))
    assert invalid.value_class == invalid.position == "unknown"


@pytest.mark.parametrize("position,index", [("first", 0), ("interior", 1), ("last", 2)])
def test_defect_position_is_bounded_metadata(position, index):
    bars = [_bar(day) for day in ("2026-09-21", "2026-09-22", "2026-09-23")]
    bars[index]["o"] = 0
    with pytest.raises(BIAggregateDataError) as caught:
        parse_bi_daily_aggregates({"results": bars})
    assert caught.value.position == position


@pytest.mark.parametrize("kwargs", [
    {"completed_through": "2026-09-22"},
    {"completed_through": "2026-09-22", "as_of": datetime(2026, 9, 23)},
    {"completed_through": 123, "as_of": datetime(2026, 9, 23, tzinfo=timezone.utc)},
    {"completed_through": "2026-09-24", "as_of": datetime(2026, 9, 23, tzinfo=timezone.utc)},
    {"as_of": datetime(2026, 9, 23, tzinfo=timezone.utc)},
])
def test_partial_selection_requires_explicit_valid_boundaries(kwargs):
    with pytest.raises(ValueError):
        parse_bi_daily_aggregates({"results": []}, **kwargs)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("green,expected", [(16, 0), (17, 1)])
def test_actual_swing_scan_ignores_ineligible_open_without_changing_17_of_20(monkeypatch, tmp_path, direction, green, expected):
    cutoff = datetime(2026, 9, 23, 18, tzinfo=timezone.utc)
    raw = _to_polygon(_attach_ts(_flat_bars(), end_day=date(2026, 9, 22)))
    partial = _bar("2026-09-23")
    partial["o"] = None
    payload = {"results": raw + [partial]}
    scanners, tickers, final, _, _, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(green), direction, [payload],
    )
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz) if tz else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(scanners, "datetime", Clock)
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")
    scanners._bi_background_scan("fixture", direction, tickers)
    cache = json.loads(final.read_text())
    assert analyses == [{"direction": direction, "bar_count": 50}]
    assert cache["count"] == expected
    assert cache["diagnostics"]["excluded_uncompleted_bars"] == 1
    assert cache["diagnostics"]["data_failures"] == 0
    assert cache["diagnostics"]["coverage"] == "complete"
    assert cache["diagnostics"]["analysis_session_dates"] == {"2026-09-22": 1}


@pytest.mark.parametrize("swing_mode", [False, True])
def test_actual_scan_excludes_bad_completed_series_and_publishes_independent_valid_rows(monkeypatch, tmp_path, swing_mode):
    raw = _to_polygon(_attach_ts(_flat_bars(), end_day=date(2026, 9, 22)))
    bad = copy.deepcopy(raw)
    bad[-2]["o"] = 0
    scanners, tickers, final, before, _, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "short", [{"results": bad}, {"results": raw}],
    )
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing" if swing_mode else "realtime")
    scanners._bi_background_scan("fixture", "short", tickers)
    cache = json.loads(final.read_text())
    diagnostics = cache["diagnostics"]
    assert len(analyses) == 1 and final.read_bytes() != before
    assert cache["count"] == 1 and cache["results"][0]["Ticker"] == tickers[1]
    assert diagnostics["data_error_value_classes"] == {"zero_price": 1}
    assert diagnostics["data_error_positions"] == {"interior": 1}
    assert diagnostics["data_error_fields"] == {"o": 1}
    assert diagnostics["data_failures"] == diagnostics["quarantined_symbols"] == 1
    assert diagnostics["coverage"] == "complete_with_exclusions"
    assert diagnostics["excluded_data_symbols"] == 1
    assert diagnostics["data_retry_attempts"] == diagnostics["data_retry_failed"] == 1


def test_live_path_still_requires_valid_uncompleted_prices():
    bar = _bar("2026-09-23")
    bar["o"] = None
    with pytest.raises(BIAggregateDataError, match="invalid_bar_value"):
        parse_bi_daily_aggregates({"results": [bar]})


@pytest.mark.parametrize("empty", [
    {"status": "OK", "resultsCount": 0},
    {"status": "DELAYED", "resultsCount": 0, "queryCount": 0},
    {"status": "OK", "results": [], "resultsCount": 0},
])
def test_completed_swing_empty_history_does_not_break_excluded_bar_count(monkeypatch, tmp_path, empty):
    raw = _to_polygon(_flat_bars())
    scanners, tickers, final, _, _, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [empty, {"results": raw}],
    )
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")
    scanners._bi_background_scan("fixture", "long", tickers)
    result = json.loads(final.read_text())
    assert len(analyses) == 1 and result["diagnostics"]["coverage"] == "complete"
    assert result["diagnostics"]["excluded_uncompleted_bars"] == 0
    assert result["diagnostics"]["rejected"]["insufficient_daily_history"] == 1


def test_explicit_null_results_are_not_confused_with_documented_omitted_empty():
    with pytest.raises(BIAggregateDataError, match="invalid_results_type"):
        parse_bi_daily_aggregates({"status": "OK", "resultsCount": 0, "results": None},
                                  completed_through="2026-09-22",
                                  as_of=datetime(2026, 9, 23, 18, tzinfo=timezone.utc))
