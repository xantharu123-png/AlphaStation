"""Pure weekend policy: no provider, mail, server or scan execution."""
from datetime import datetime, timezone

import pytest

from modules import scan_schedule as schedule


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


STOCKS = [
    "bi_long", "bi_short", "biotech", "bear", "bear_scan", "volume_spikes",
    "penny_stocks", "orb", "turtle", "strategy_scan", "strategies",
    "strat_cup_and_handle_breakout", "strat_wyckoff", "money_flow",
    "cup_handle_watch", "quote_capability",
]
UNCHANGED = [
    "early_movers", "crypto_trade_signals", "crypto_explosion", "new_listing",
    "btc_divergenz", "btc_divergence", "crypto_strat_momentum_breakout_long",
    "market_context", "crash_monitor", "penny_positions", "signal_eval",
    "weekly_report", "insider_cluster_alert", "mail_outbox", "unknown_scan",
    "stock_unknown_scan", "", None, {}, [],
]


@pytest.mark.parametrize("name", STOCKS)
def test_stock_automatic_starts_stop_on_new_york_weekend(name):
    assert schedule.automatic_scan_allowed(name, utc("2026-09-19T03:59:59Z"))
    assert not schedule.automatic_scan_allowed(name, utc("2026-09-19T04:00:00Z"))
    assert not schedule.automatic_scan_allowed(name, utc("2026-09-21T03:59:59Z"))
    assert schedule.automatic_scan_allowed(name, utc("2026-09-21T04:00:00Z"))


@pytest.mark.parametrize("name", UNCHANGED)
def test_non_stock_and_unknown_keys_are_not_caught_by_broad_guessing(name):
    assert schedule.automatic_scan_allowed(name, utc("2026-09-20T16:00:00Z"))
    assert schedule.automatic_scan_allowed(name, "not-a-date")


@pytest.mark.parametrize("value", [
    "not-a-date", "2026-09-21T00:00:00", datetime(2026, 9, 21),
    float("nan"), float("inf"), -float("inf"), 10**999, True, {}, [],
])
def test_invalid_explicit_time_fails_closed_only_for_stocks(value):
    assert not schedule.automatic_scan_allowed("bi_long", value)
    snapshot = schedule.schedule_snapshot("bi_long", now_utc=value)
    assert snapshot == {
        "automatic_paused": True, "reason": "invalid_schedule_time",
        "timezone": "America/New_York", "next_eligible_at": None,
    }
    assert schedule.next_allowed_at(value) is None


@pytest.mark.parametrize("start,expected", [
    ("2026-03-07T05:00:00Z", "2026-03-09T04:00:00Z"),
    ("2026-03-08T06:30:00Z", "2026-03-09T04:00:00Z"),
    ("2026-03-08T07:30:00Z", "2026-03-09T04:00:00Z"),
    ("2026-10-31T04:00:00Z", "2026-11-02T05:00:00Z"),
    ("2026-11-01T05:30:00Z", "2026-11-02T05:00:00Z"),
    ("2026-11-01T06:30:00Z", "2026-11-02T05:00:00Z"),
    ("2026-09-19T03:59:59Z", "2026-09-19T03:59:59Z"),
])
def test_next_allowed_uses_market_calendar_midnight_including_both_dst_changes(start, expected):
    assert schedule.next_allowed_at(utc(start).timestamp()) == utc(expected).timestamp()


def test_snapshot_does_not_claim_weekend_next_run_or_reset_last_run():
    result = schedule.schedule_snapshot(
        "strategy_scan", next_run=utc("2026-09-20T22:00:00Z").timestamp(),
        now_utc=utc("2026-09-20T20:00:00Z"),
    )
    assert result == {
        "automatic_paused": True, "reason": "weekend", "timezone": "America/New_York",
        "next_eligible_at": "2026-09-21T04:00:00+00:00",
    }
    assert "last_run" not in result


def test_snapshot_preserves_later_due_time_and_rolls_future_weekend_without_current_pause():
    monday = utc("2026-09-21T09:00:00Z")
    weekend = utc("2026-09-20T20:00:00Z")
    assert schedule.schedule_snapshot("bi_long", monday, weekend)["next_eligible_at"] == monday.isoformat()
    friday = utc("2026-09-18T18:00:00Z")
    result = schedule.schedule_snapshot("bi_long", weekend, friday)
    assert not result["automatic_paused"]
    assert result["reason"] is None
    assert result["next_eligible_at"] == "2026-09-21T04:00:00+00:00"


def test_snapshot_does_not_invent_a_due_time_on_an_allowed_day():
    result = schedule.schedule_snapshot("bi_long", now_utc=utc("2026-09-21T05:00:00Z"))
    assert result["next_eligible_at"] is None
    assert not result["automatic_paused"]


def test_crypto_next_run_is_not_rolled_to_monday():
    when = utc("2026-09-20T20:00:00Z")
    result = schedule.schedule_snapshot("crypto_strat_wyckoff", when, when)
    assert not result["automatic_paused"]
    assert result["next_eligible_at"] == when.isoformat()


def test_omitted_clock_uses_aware_current_utc_and_keeps_pure_policy(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return utc("2026-09-20T20:00:00Z")

    monkeypatch.setattr(schedule, "datetime", Clock)
    assert not schedule.automatic_scan_allowed("bi_long")
    assert schedule.automatic_scan_allowed("early_movers")
