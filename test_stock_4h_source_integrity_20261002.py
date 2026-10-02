"""Causal source integrity of the shared regular-session 4H timing adapter."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from modules.stock_execution import (
    aggregate_regular_session_4h_bars,
    stock_swing_4h_execution_state,
)

NY = ZoneInfo("America/New_York")


def source(day="2026-07-20", count=13):
    opening = datetime.fromisoformat(day).replace(hour=9, minute=30, tzinfo=NY)
    return [dict(t=int((opening + timedelta(minutes=30 * index)).timestamp() * 1000),
                 o=100.0 + index, h=101.0 + index, l=99.0 + index,
                 c=100.5 + index, v=1000.0 + index) for index in range(count)]


def cutoff(day="2026-07-20", hour=16, minute=0):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=NY)


def test_duplicate_cannot_substitute_for_missing_regular_session_slot():
    bars = source(count=8)
    bars[3] = deepcopy(bars[2])
    result = aggregate_regular_session_4h_bars(bars, NY)
    assert result[0]["partial_source_bar"] is True
    assert result[0]["source_bar_count"] == 7


def test_identical_duplicate_does_not_double_volume_or_count():
    bars = source(count=8)
    original = deepcopy(bars)
    result = aggregate_regular_session_4h_bars(bars + [deepcopy(bars[2])], NY)
    assert result[0]["source_bar_count"] == 8
    assert result[0]["volume"] == sum(bar["v"] for bar in bars)
    assert result[0]["partial_source_bar"] is False
    assert bars == original


def test_conflicting_duplicate_fails_closed_not_arbitrary_provider_order():
    bars = source(count=8)
    conflict = dict(bars[2], c=bars[2]["c"] + .1)
    with pytest.raises(ValueError, match="conflicting_duplicate"):
        aggregate_regular_session_4h_bars(bars + [conflict], NY)


@pytest.mark.parametrize("field,value", [
    ("o", True), ("c", False), ("h", float("nan")), ("l", -1),
    ("v", -1), ("v", True), ("v", None), ("c", 103.01),
])
def test_invalid_intermediate_source_bar_cannot_hide_in_valid_aggregate(field, value):
    bars = source(count=8)
    bars[2][field] = value
    with pytest.raises(ValueError, match="invalid_30m"):
        aggregate_regular_session_4h_bars(bars, NY)


@pytest.mark.parametrize("shift_ms", [1, 1000, 60_000])
def test_off_grid_source_timestamp_cannot_count_as_required_slot(shift_ms):
    bars = source(count=8)
    bars[2]["t"] += shift_ms
    with pytest.raises(ValueError, match="off_grid"):
        aggregate_regular_session_4h_bars(bars, NY)


def test_missing_first_slot_retains_canonical_bucket_start_and_partial_evidence():
    bars = source(count=8)
    result = aggregate_regular_session_4h_bars(bars[1:], NY)
    assert result[0]["timestamp"] == bars[0]["t"]
    assert result[0]["partial_source_bar"] is True
    assert result[0]["missing_source_bar_count"] == 1


@pytest.mark.parametrize("day", ["2026-07-03", "2026-07-04", "2026-12-25"])
def test_calendar_closed_session_cannot_invent_regular_session_bars(day):
    assert aggregate_regular_session_4h_bars(source(day), NY) == []


def test_early_close_has_seven_slots_and_no_ghost_afternoon_bucket():
    result = aggregate_regular_session_4h_bars(source("2026-11-27"), NY,
                                             as_of=cutoff("2026-11-27", 18))
    assert len(result) == 1
    assert result[0]["source_bar_count"] == 7
    assert result[0]["expected_source_bar_count"] == 7
    assert result[0]["partial_source_bar"] is False


def test_normal_full_session_reverse_order_has_same_closed_buckets():
    bars = source()
    forward = aggregate_regular_session_4h_bars(bars, NY, as_of=cutoff())
    backward = aggregate_regular_session_4h_bars(list(reversed(bars)), NY, as_of=cutoff())
    assert backward == forward
    assert [bar["source_bar_count"] for bar in forward] == [8, 5]
    assert not any(bar["partial_source_bar"] for bar in forward)


def test_full_number_of_source_slots_does_not_make_forming_bucket_completed():
    bars = source(count=8)
    result = aggregate_regular_session_4h_bars(bars, NY, as_of=cutoff(hour=13, minute=15))
    assert result[0]["source_bar_count"] == 8
    assert result[0]["partial_source_bar"] is True


def test_future_start_is_removed_before_bad_provisional_price_validation():
    bars = source(count=8)
    bars[7]["c"] = True
    result = aggregate_regular_session_4h_bars(bars, NY, as_of=cutoff(hour=13))
    assert result[0]["source_bar_count"] == 7
    assert result[0]["partial_source_bar"] is True


def test_explicit_unclosed_source_flag_keeps_aggregate_partial_even_after_close():
    bars = source(count=8)
    bars[-1]["closed"] = False
    result = aggregate_regular_session_4h_bars(bars, NY, as_of=cutoff())
    assert result[0]["partial_source_bar"] is True


def test_timezone_naive_as_of_fails_explicitly():
    with pytest.raises(ValueError, match="requires_timezone"):
        aggregate_regular_session_4h_bars(source(), NY,
                                          as_of=datetime(2026, 7, 20, 16))


@pytest.mark.parametrize("as_of", [False, 0, "2026-07-20T16:00:00Z"])
def test_explicit_invalid_cutoff_cannot_silently_mean_current_time(as_of):
    with pytest.raises(ValueError, match="requires_timezone"):
        aggregate_regular_session_4h_bars(source(), NY, as_of=as_of)


def test_forming_reclaim_cannot_release_closed_long_rejection():
    from test_stock_swing_4h_rejection import _stable_bars, _bar
    bars = _stable_bars() + [_bar(210, 212, 192, 195, 650_000)]
    partial = dict(_bar(195, 208, 194, 207, 300_000), partial_source_bar=True)
    state = stock_swing_4h_execution_state(bars + [partial])
    assert state["Swing_4H_Execution_Status"] == "WAIT_RECLAIM"
    completed = stock_swing_4h_execution_state(bars + [dict(partial, partial_source_bar=False)])
    assert completed["Swing_4H_Execution_Status"] == "RECLAIMED"


def test_forming_adverse_price_can_veto_a_previous_closed_long_reclaim():
    from test_stock_swing_4h_rejection import _stable_bars, _bar
    bars = _stable_bars() + [_bar(210, 212, 192, 195, 650_000),
                            _bar(195, 208, 194, 207, 300_000)]
    assert stock_swing_4h_execution_state(bars)["Swing_4H_Execution_Status"] == "RECLAIMED"
    partial = dict(_bar(207, 208, 190, 191, 200_000), partial_source_bar=True)
    assert stock_swing_4h_execution_state(bars + [partial])["Swing_4H_Execution_Status"] == "WAIT_RECLAIM"


def api_fetch_fixture(monkeypatch, payload):
    import api
    now = cutoff()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    class Response:
        status_code = 200
        def json(self):
            return payload
    calls = []
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-not-a-real-provider-key")
    monkeypatch.setattr(api, "_STOCK_SWING_EXECUTION_CACHE", {})
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: calls.append((a, kw)) or Response())
    return api, calls, now


@pytest.mark.parametrize("status,adjusted", [("ERROR", True), ("NOT_AUTHORIZED", True),
                                            ("OK", False), ("OK", None)])
def test_api_4h_fetch_cannot_use_failed_or_non_adjusted_payload_with_valid_looking_rows(monkeypatch, status, adjusted):
    api, calls, _ = api_fetch_fixture(monkeypatch, dict(status=status, adjusted=adjusted, results=source()))
    assert api._fetch_recent_stock_4h_bars("AUDT", limit=2) == []
    assert "AUDT" not in api._STOCK_SWING_EXECUTION_CACHE
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["OK", "DELAYED"])
def test_api_4h_fetch_uses_aware_same_clock_and_safe_warm_cache(monkeypatch, status):
    api, calls, now = api_fetch_fixture(monkeypatch, dict(status=status, adjusted=True, results=source()))
    real_adapter = api.aggregate_regular_session_4h_bars
    cutoffs = []
    def capture(*args, **kwargs):
        cutoffs.append(kwargs.get("as_of"))
        return real_adapter(*args, **kwargs)
    monkeypatch.setattr(api, "aggregate_regular_session_4h_bars", capture)
    first = api._fetch_recent_stock_4h_bars("AUDT", limit=2)
    assert len(first) == 2 and not any(bar["partial_source_bar"] for bar in first)
    assert cutoffs == [now]
    second = api._fetch_recent_stock_4h_bars("AUDT", limit=2)
    assert second == first and len(calls) == 1


def test_api_4h_future_clock_cache_entry_cannot_bypass_provider_refresh(monkeypatch):
    api, calls, now = api_fetch_fixture(monkeypatch, dict(status="OK", adjusted=True, results=source()))
    api._STOCK_SWING_EXECUTION_CACHE["AUDT"] = dict(timestamp=now.timestamp() + 1,
                                                   bars=[dict(close=666)] * 40)
    first = api._fetch_recent_stock_4h_bars("AUDT", limit=2)
    assert first and first[0]["close"] != 666
    assert len(calls) == 1


def test_classifier_to_native_short_stop_must_ceil_raw_invalidation_not_rounded_display():
    import api
    from modules.stock_execution import stock_swing_4h_short_execution_state
    from test_stock_swing_4h_rejection import _ophc_like_high_base
    bars = _ophc_like_high_base()
    # This fixture's causal stop is 9.2825. Scaling preserves all percentage
    # pattern decisions but moves the true invalidation just above a tick.
    scale = 9.2800004 / 9.2825
    for bar in bars:
        for field in ("open", "high", "low", "close"):
            bar[field] *= scale
    state = stock_swing_4h_short_execution_state(bars)
    row = dict(state, Signal_Direction="SHORT", Entry=8.5, StopLoss=9.0, TP1=7.0, TP2=6.0)
    enriched = api._apply_stock_short_4h_stop_floor(row)
    assert enriched["StopLoss"] == 9.29
    assert state["Swing_Short_4H_Stop_Floor"] == pytest.approx(9.2800004, abs=1e-12)
    assert enriched["StopLoss"] >= state["Swing_Short_4H_Stop_Floor"]
