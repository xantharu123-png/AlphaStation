"""Pure contract tests: completed-close notice timing, no provider or mail I/O."""
from datetime import date, datetime

import pytest

from modules import stock_swing_contract as swing


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def reference_row(session="2026-10-09", observed="2026-10-09T20:00:00Z"):
    # Literal provider metadata keeps expected session/close independent of the
    # calendar implementation being exercised by the public timing contracts.
    return {
        "stock_swing_contract_version": 1,
        "stock_swing_mode": "completed_daily_swing",
        "swing_analysis_session": session,
        "swing_reference_close": 100.0,
        "swing_data_delay_seconds": 900,
        "swing_timeframe": "1D",
        "trade_horizon": "swing",
        "scan_price_observed_at": observed,
        "scan_price_source": "polygon_completed_1d_swing",
        "price_observed_at": observed,
        "price_source": "polygon_completed_1d_swing",
        "price_mode": "swing_reference_close",
        "price_session": "COMPLETED_US_SESSION",
        "fill_evidence_verified": False,
        "entry_quality": "SWING_PLAN",
    }


def notice_current(row, as_of):
    contract = getattr(swing, "breakout_notice_current", None)
    assert callable(contract), "missing completed-close breakout notice timing contract"
    return contract(row, as_of)


def scan_due(previous_started_at, as_of):
    contract = getattr(swing, "post_close_scan_due", None)
    assert callable(contract), "missing completed-close scan timing contract"
    return contract(previous_started_at, as_of)


def test_friday_swing_remains_valid_monday_but_cannot_be_a_first_breakout_notice():
    row = reference_row()
    monday = utc("2026-10-12T14:00:00Z")
    assert swing.validate(row, monday) is True
    assert notice_current(row, monday) is False


@pytest.mark.parametrize("now,want", [
    ("2026-10-09T20:14:59Z", False),
    ("2026-10-09T20:15:00Z", True),
    ("2026-10-10T00:00:00Z", True),  # Friday evening in New York.
    ("2026-10-10T03:59:59Z", True),
    ("2026-10-10T04:00:00Z", False),  # Saturday starts in New York.
    ("2026-10-10T20:15:00Z", False),
    ("2026-10-11T20:15:00Z", False),
    ("2026-10-12T14:00:00Z", False),
])
def test_notice_uses_reference_session_new_york_date_not_utc_date(now, want):
    assert notice_current(reference_row(), utc(now)) is want


@pytest.mark.parametrize("session,observed,now,want", [
    ("2026-09-04", "2026-09-04T20:00:00Z", "2026-09-07T20:15:00Z", False),
    ("2026-11-25", "2026-11-25T21:00:00Z", "2026-11-26T21:15:00Z", False),
    ("2026-11-27", "2026-11-27T18:00:00Z", "2026-11-27T18:14:59Z", False),
    ("2026-11-27", "2026-11-27T18:00:00Z", "2026-11-27T18:15:00Z", True),
    ("2026-11-27", "2026-11-27T18:00:00Z", "2026-11-28T04:59:59Z", True),
    ("2026-11-27", "2026-11-27T18:00:00Z", "2026-11-28T05:00:00Z", False),
    ("2028-07-03", "2028-07-03T17:00:00Z", "2028-07-03T17:14:59Z", False),
    ("2028-07-03", "2028-07-03T17:00:00Z", "2028-07-03T17:15:00Z", True),
    ("2026-03-06", "2026-03-06T21:00:00Z", "2026-03-06T21:15:00Z", True),
    ("2026-03-09", "2026-03-09T20:00:00Z", "2026-03-09T20:15:00Z", True),
])
def test_notice_inherits_real_early_close_holiday_and_dst_calendar(session, observed, now, want):
    assert notice_current(reference_row(session, observed), utc(now)) is want


@pytest.mark.parametrize("field,value", [
    ("stock_swing_mode", "realtime"),
    ("stock_swing_contract_version", True),
    ("scan_price_source", "polygon_snapshot"),
    ("scan_price_observed_at", "2026-10-09T19:59:00Z"),
    ("swing_timeframe", "5m"),
    ("swing_data_delay_seconds", 0),
    ("swing_reference_close", float("nan")),
    ("fill_evidence_verified", True),
])
def test_notice_cannot_skip_existing_swing_metadata_validation(field, value):
    row = reference_row()
    row[field] = value
    assert notice_current(row, utc("2026-10-09T20:15:00Z")) is False


@pytest.mark.parametrize("as_of", [None, True, 0, "2026-10-09T20:15:00Z",
                                      date(2026, 10, 9), datetime(2026, 10, 9, 20, 15)])
def test_notice_invalid_or_naive_clock_fails_closed(as_of):
    assert notice_current(reference_row(), as_of) is False


@pytest.mark.parametrize("previous_started_at", [None, 0])
@pytest.mark.parametrize("now,want", [
    ("2026-10-09T20:00:00Z", False),
    ("2026-10-09T20:14:59Z", False),
    ("2026-10-09T20:15:00Z", True),
    ("2026-10-10T00:00:00Z", True),
    ("2026-10-10T03:59:59Z", True),
    ("2026-10-10T04:00:00Z", False),
    ("2026-10-10T20:15:00Z", False),
    ("2026-10-11T20:15:00Z", False),
    ("2026-10-12T14:00:00Z", False),
    ("2026-10-12T20:15:00Z", True),
])
def test_unstarted_scan_only_becomes_due_after_current_new_york_session_data_ready(previous_started_at, now, want):
    assert scan_due(previous_started_at, utc(now)) is want


@pytest.mark.parametrize("previous,now,want", [
    ("2026-10-09T19:00:00Z", "2026-10-09T20:15:00Z", True),
    ("2026-10-09T20:14:59Z", "2026-10-09T20:15:00Z", True),
    ("2026-10-09T20:15:00Z", "2026-10-09T20:15:00Z", False),
    ("2026-10-09T20:15:00Z", "2026-10-09T21:00:00Z", False),
    ("2026-10-09T20:16:00Z", "2026-10-10T03:59:59Z", False),
    ("2026-10-09T20:15:00Z", "2026-10-12T14:00:00Z", False),
    ("2026-10-09T20:15:00Z", "2026-10-12T20:15:00Z", True),
])
def test_only_a_start_before_current_session_data_ready_leaves_scan_due(previous, now, want):
    assert scan_due(utc(previous).timestamp(), utc(now)) is want


@pytest.mark.parametrize("now,want", [
    ("2026-09-07T20:15:00Z", False),  # Labor Day.
    ("2026-11-26T21:15:00Z", False),  # Thanksgiving.
    ("2026-04-03T20:15:00Z", False),  # Good Friday.
    ("2026-03-06T21:14:59Z", False),  # Before spring DST: close is 21:00 UTC.
    ("2026-03-06T21:15:00Z", True),
    ("2026-03-09T20:14:59Z", False),  # After spring DST: close is 20:00 UTC.
    ("2026-03-09T20:15:00Z", True),
    ("2026-10-30T20:15:00Z", True),
    ("2026-11-02T21:14:59Z", False),  # After fall DST.
    ("2026-11-02T21:15:00Z", True),
    ("2026-11-27T18:14:59Z", False),  # Friday winter 13:00 ET early close.
    ("2026-11-27T18:15:00Z", True),
    ("2026-12-24T18:15:00Z", True),
    ("2028-07-03T17:14:59Z", False),  # Summer 13:00 ET early close.
    ("2028-07-03T17:15:00Z", True),
    ("2026-07-02T17:15:00Z", False),  # Full session, not an invented early close.
    ("2026-07-02T20:15:00Z", True),
])
def test_scan_due_reuses_exchange_holidays_early_closes_and_dst(now, want):
    assert scan_due(0, utc(now)) is want


@pytest.mark.parametrize("previous_started_at", [True, False, -1, float("nan"),
                                                  float("inf"), float("-inf"), "bad",
                                                  utc("2026-10-09T20:15:01Z").timestamp()])
def test_invalid_or_future_scan_start_fails_closed(previous_started_at):
    assert scan_due(previous_started_at, utc("2026-10-09T20:15:00Z")) is False


@pytest.mark.parametrize("as_of", [None, True, 0, "2026-10-09T20:15:00Z",
                                      date(2026, 10, 9), datetime(2026, 10, 9, 20, 15)])
def test_scan_due_invalid_or_naive_clock_fails_closed(as_of):
    assert scan_due(0, as_of) is False


@pytest.mark.parametrize("mode", ["realtime", "", "disabled"])
def test_post_close_scan_is_not_due_outside_starter_swing_mode(monkeypatch, mode):
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", mode)
    assert scan_due(0, utc("2026-10-09T20:15:00Z")) is False


def test_post_close_scan_uses_default_starter_swing_mode(monkeypatch):
    monkeypatch.delenv("STOCK_SWING_DATA_MODE", raising=False)
    assert scan_due(0, utc("2026-10-09T20:15:00Z")) is True


@pytest.mark.parametrize("contract", ["notice", "scan"])
def test_explicit_aware_as_of_stays_valid_when_default_clock_is_overridden(monkeypatch, contract):
    as_of = utc("2026-10-09T20:15:00Z")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of.astimezone(tz) if tz else as_of.replace(tzinfo=None)

    monkeypatch.setattr(swing, "datetime", Clock)
    if contract == "notice":
        assert notice_current(reference_row(), as_of) is True
    else:
        assert scan_due(0, as_of) is True
