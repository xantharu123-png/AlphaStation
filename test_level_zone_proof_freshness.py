"""Select causal, current break evidence before ranking timeframe strength."""
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import LevelEvidence, build_structure_snapshot


BASE = datetime(2026, 9, 21, 20, tzinfo=timezone.utc)


def _bar(hour, direction, *, duration=4, delta=0.2, retest=False):
    close = 100 + (delta if direction == "LONG" else -delta)
    return {
        "open_time": BASE + timedelta(hours=hour - duration),
        "close_time": BASE + timedelta(hours=hour),
        "open": close, "high": max(close + 0.05, 100) if retest else close + 0.05,
        "low": min(close - 0.05, 100) if retest else close - 0.05,
        "close": close, "volume": 1000,
    }


def _snapshot(direction, bars, *, hour=34, extra_seconds=0):
    boundary = LevelEvidence(
        "horizontal_swing", "weekly_boundary", "1W", 100, 100,
        BASE, BASE, BASE,
        provenance={"role_hint": "resistance" if direction == "LONG" else "support"},
    )
    return build_structure_snapshot(
        bars, symbol="SYNTH", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(hours=hour, seconds=extra_seconds),
        current_price=100.8 if direction == "LONG" else 99.2,
        tick_size=0.01, external_evidence=[boundary], include_session_levels=False,
        pivot_left=100, pivot_right=100,
    )


def _proof(snapshot):
    assert len(snapshot.zones) == 1
    return snapshot.zones[0].break_reclaim_evidence


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("fast_hour", [24, 25])
def test_stale_same_or_newer_fast_close_does_not_shadow_current_daily(direction, fast_hour):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24)],
        "4H": [_bar(fast_hour, direction)],
    })
    proof = _proof(snapshot)
    assert proof.timeframe == "1D"
    assert proof.state == "BREAK_CONFIRMED"
    assert proof.retest_observed is False
    assert proof.last_completed_at == BASE + timedelta(hours=24)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("fast_hour", [24, 25])
def test_current_fast_proof_keeps_existing_recency_and_tie_break_ranking(direction, fast_hour):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24)],
        "4H": [_bar(fast_hour, direction)],
    }, hour=26)
    assert _proof(snapshot).timeframe == "4H"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_current_daily_retest_is_preserved_when_fast_proof_expires(direction):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24), _bar(48, direction, duration=24, retest=True)],
        "4H": [_bar(48, direction)],
    }, hour=58)
    assert _proof(snapshot).timeframe == "1D"
    assert _proof(snapshot).state == "RECLAIMED"
    assert _proof(snapshot).retest_observed is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_expired_fast_retest_cannot_override_current_daily_unretested_break(direction):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24)],
        "4H": [_bar(20, direction), _bar(24, direction, retest=True)],
    })
    assert _proof(snapshot).timeframe == "1D"
    assert _proof(snapshot).state == "BREAK_CONFIRMED"
    assert _proof(snapshot).retest_observed is False
    assert "breakout_confirmed_without_retest" in snapshot.quality_flags


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_current_fast_break_wins_over_expired_daily_retest(direction):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24), _bar(48, direction, duration=24, retest=True)],
        "4H": [_bar(100, direction)],
    }, hour=101)
    assert _proof(snapshot).timeframe == "4H"
    assert _proof(snapshot).state == "BREAK_CONFIRMED"
    assert _proof(snapshot).retest_observed is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("retest", [False, True])
def test_only_expired_evidence_is_diagnostic_pending_not_current_role_flip(direction, retest):
    daily = [_bar(24, direction, duration=24)]
    if retest:
        daily.append(_bar(48, direction, duration=24, retest=True))
    snapshot = _snapshot(direction, {"1D": daily, "4H": [_bar(48, direction)]}, hour=100)
    proof = _proof(snapshot)
    assert snapshot.zones[0].break_state == "intact"
    assert proof.state == "RECLAIM_PENDING"
    assert proof.reason == "completed_break_evidence_stale"
    assert proof.break_closed_at is not None  # Historical diagnostics survive.
    assert "breakout_confirmed_without_retest" not in snapshot.quality_flags


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("extra_seconds,accepted", [(2, True), (2.001, False)])
def test_existing_two_bar_plus_two_second_limit_is_exact(direction, extra_seconds, accepted):
    snapshot = _snapshot(direction, {"4H": [_bar(24, direction)]}, hour=32,
                         extra_seconds=extra_seconds)
    assert (_proof(snapshot).state == "BREAK_CONFIRMED") is accepted


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("failure_hour", [24, 25])
def test_fallback_daily_does_not_survive_same_time_or_newer_wrong_side_close(direction, failure_hour):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24)],
        "4H": [_bar(failure_hour, direction, delta=-0.2)],
    })
    assert snapshot.zones[0].break_state == "intact"
    assert _proof(snapshot).break_closed_at is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_conflicting_fast_duplicates_reset_daily_even_when_fast_feed_is_expired(direction):
    bars = {"1D": [_bar(24, direction, duration=24)],
            "4H": [_bar(25, direction), _bar(25, direction, delta=-0.2)]}
    snapshot = _snapshot(direction, bars)
    reversed_snapshot = _snapshot(direction, {
        tf: list(reversed(values)) for tf, values in reversed(list(bars.items()))
    })
    assert snapshot.to_dict() == reversed_snapshot.to_dict()
    assert snapshot.zones[0].break_state == "intact"
    assert "conflicting_completed_bars" in snapshot.quality_flags


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_new_valid_break_after_conflict_cannot_borrow_old_daily_retest(direction):
    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24), _bar(48, direction, duration=24, retest=True)],
        "4H": [_bar(49, direction), _bar(49, direction, delta=-0.2), _bar(53, direction)],
    }, hour=55)
    proof = _proof(snapshot)
    assert proof.timeframe == "4H"
    assert proof.state == "BREAK_CONFIRMED"
    assert proof.break_closed_at == BASE + timedelta(hours=53)
    assert proof.retest_observed is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_repair_reaches_trade_plan_without_weakening_downstream_proof_validation(direction):
    import api

    snapshot = _snapshot(direction, {
        "1D": [_bar(24, direction, duration=24)], "4H": [_bar(24, direction)],
    })
    diagnostics = {}
    plan = api._build_structured_trade_setup(
        direction, snapshot.current_price, 1, 0, 0, 0, 0,
        structure_snapshot=snapshot, require_causal_structure=True, diagnostics=diagnostics,
    )
    assert plan is not None, diagnostics
    assert plan["retest_status"] == "not_confirmed"
    assert "breakout_confirmed_without_retest" in plan["warning_codes"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("retest", [False, True])
def test_all_expired_proofs_cannot_create_trade_plan(direction, retest):
    import api

    bars = [_bar(24, direction, duration=24)]
    if retest:
        bars.append(_bar(48, direction, duration=24, retest=True))
    snapshot = _snapshot(direction, {"1D": bars}, hour=100)
    diagnostics = {}
    plan = api._build_structured_trade_setup(
        direction, snapshot.current_price, 1, 0, 0, 0, 0,
        structure_snapshot=snapshot, require_causal_structure=True, diagnostics=diagnostics,
    )
    assert plan is None
    assert diagnostics["reason"] == (
        "crossed_resistance_unconfirmed" if direction == "LONG" else "crossed_support_unconfirmed"
    )
