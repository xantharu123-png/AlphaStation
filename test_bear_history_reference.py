"""Session/causality counterexamples for the pure Bear baseline adapter."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from modules.bear_history import bear_reference_metrics
from test_stock_audit_repair_contracts import daily_bars

NOW = datetime(2026, 9, 30, 13, 50, tzinfo=timezone.utc)


def history(count=60):
    bars = daily_bars(count, session="2026-09-29")
    for index, bar in enumerate(bars):
        close = 100.0 + index * .1
        bar.update(o=close, h=close + .5, l=close - .5, c=close, v=1_000_000. + index * 1000.)
    return bars


@pytest.mark.parametrize("order", ["asc", "desc", "shuffled"])
def test_previous_completed_session_is_never_discarded_by_position(order):
    bars = history()
    ordered = bars if order == "asc" else list(reversed(bars)) if order == "desc" else bars[::2] + bars[1::2]
    result = bear_reference_metrics(ordered, signal_session="2026-09-30", as_of=NOW)
    assert result["latest_reference_session"] == "2026-09-29"
    assert result["reference_count"] == 60
    assert result["ma20"] == pytest.approx(sum(bar["c"] for bar in bars[-20:]) / 20.)
    assert result["ma50"] == pytest.approx(sum(bar["c"] for bar in bars[-50:]) / 50.)
    assert result["avg_volume20"] == pytest.approx(sum(bar["v"] for bar in bars[-20:]) / 20.)
    assert result["low_20d"] == min(bar["l"] for bar in bars[-20:])
    assert result["low_60d"] == min(bar["l"] for bar in bars)


def test_current_forming_and_later_completed_signal_bar_do_not_pollute_reference():
    bars = history()
    today = daily_bars(1, session="2026-09-30")[0]
    today.update(o=500., h=501., l=499., c=500., v=50_000_000.)
    before = bear_reference_metrics(bars, signal_session="2026-09-30", as_of=NOW)
    during = bear_reference_metrics(bars + [today], signal_session="2026-09-30", as_of=NOW)
    after = bear_reference_metrics(bars + [today], signal_session="2026-09-30", as_of=NOW.replace(hour=21))
    for key in ("ma20", "ma50", "avg_volume20", "low_20d", "low_60d", "reference_count"):
        assert before[key] == during[key] == after[key]
    assert len(after["completed_bars"]) == len(before["completed_bars"]) + 1


def test_partial_older_history_does_not_claim_full_50_or_60_session_metrics():
    result = bear_reference_metrics(history(25), signal_session="2026-09-30", as_of=NOW)
    assert result["ma50"] is result["low_60d"] is None
    assert result["ma20"] is not None


def test_same_session_identical_duplicate_is_deduplicated_without_double_weight():
    bars = history()
    result = bear_reference_metrics(bars + [deepcopy(bars[-1])], signal_session="2026-09-30", as_of=NOW)
    assert result["reference_count"] == 60
    assert result["ma20"] == pytest.approx(sum(bar["c"] for bar in bars[-20:]) / 20.)


def test_conflicting_duplicate_session_is_a_data_error():
    bars = history()
    conflicting = dict(bars[-1], v=bars[-1]["v"] + 1)
    with pytest.raises(ValueError, match="bear_history_conflicting_session"):
        bear_reference_metrics(bars + [conflicting], signal_session="2026-09-30", as_of=NOW)


def test_conflicting_explicit_completion_flags_are_not_silently_promoted():
    bars = history()
    conflicting = dict(bars[-1], complete=False)
    with pytest.raises(ValueError, match="bear_history_conflicting_session"):
        bear_reference_metrics(bars + [conflicting], signal_session="2026-09-30", as_of=NOW)


def test_later_certified_sessions_cannot_enter_an_older_observation_prefix():
    bars = history()
    tomorrow = daily_bars(1, session="2026-10-01")[0]
    tomorrow.update(o=500., h=501., l=499., c=500., v=50_000_000.)
    result = bear_reference_metrics(bars + [tomorrow], signal_session="2026-09-30",
                                    as_of=datetime(2026, 10, 2, 21, tzinfo=timezone.utc))
    assert result["reference_count"] == 60
    assert all(bar["date"] <= "2026-09-30" for bar in result["completed_bars"])


@pytest.mark.parametrize("future_session,clock", [
    ("2026-09-30", NOW),
    ("2026-10-01", datetime(2026, 10, 2, 21, tzinfo=timezone.utc)),
])
def test_irrelevant_future_or_unfinished_bar_prices_are_never_validated_as_reference(future_session, clock):
    bars = history()
    provisional = daily_bars(1, session=future_session)[0]
    provisional.update(o=None, h=float("nan"), l=0, c=-1, v=-10)
    result = bear_reference_metrics(bars + [provisional], signal_session="2026-09-30", as_of=clock)
    assert result["reference_count"] == 60
    assert result["ma20"] == pytest.approx(sum(bar["c"] for bar in bars[-20:]) / 20.)


@pytest.mark.parametrize("field,value", [("c", float("nan")), ("h", 1.), ("o", None), ("t", None),
                                         ("v", -1), ("c", True), ("t", float("inf"))])
def test_bad_price_volume_or_missing_date_does_not_become_an_indicator(field, value):
    bars = history()
    bars[-1][field] = value
    with pytest.raises(ValueError, match="bear_history_bar_invalid"):
        bear_reference_metrics(bars, signal_session="2026-09-30", as_of=NOW)


@pytest.mark.parametrize("session,clock,reason", [
    ("2026-09-30", NOW.replace(tzinfo=None), "time_context_invalid"),
    ("2026-10-01", NOW, "signal_session_future"),
    ("2026-09-27", NOW, "signal_session_invalid"),
    ("2026-09-30T00:00:00", NOW, "signal_session_invalid"),
])
def test_explicit_exchange_session_and_aware_clock_required(session, clock, reason):
    with pytest.raises(ValueError, match=reason):
        bear_reference_metrics(history(), signal_session=session, as_of=clock)


def test_insufficient_completed_prior_history_is_not_a_partial_ma20():
    with pytest.raises(ValueError, match="insufficient_completed_reference"):
        bear_reference_metrics(history(19), signal_session="2026-09-30", as_of=NOW)
