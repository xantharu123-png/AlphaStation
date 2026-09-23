"""Confirmed breakout is actionable evidence; retest remains a distinct fact."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import (
    LevelEvidence, build_structure_snapshot, classify_for_trade,
    evaluate_break_reclaim, legacy_level_adapter,
)


BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _bar(hour, direction, *, delta=0.2, retest=False, duration=24, **extra):
    sign = 1 if direction == "LONG" else -1
    close = 100.0 + sign * delta
    high, low = close + 0.05, close - 0.05
    if retest:
        high, low = max(high, 100.0), min(low, 100.0)
    return {
        "open_time": BASE + timedelta(hours=hour - duration),
        "close_time": BASE + timedelta(hours=hour),
        "open": close, "high": high, "low": low, "close": close,
        "volume": 1000, **extra,
    }


def _snapshot(direction, bars, *, hour=96, members=()):
    role = "resistance" if direction == "LONG" else "support"
    anchor = LevelEvidence(
        source_family="horizontal_swing", source_name="known_boundary",
        timeframe="1D", lower=100, upper=100,
        observed_at=BASE, confirmed_at=BASE, data_cutoff_at=BASE,
        provenance={"role_hint": role},
    )
    return build_structure_snapshot(
        bars, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(hours=hour),
        current_price=100.8 if direction == "LONG" else 99.2,
        tick_size=0.01, external_evidence=[anchor, *members],
        include_session_levels=False, pivot_left=100, pivot_right=100,
    )


def _zone(snapshot):
    return next(zone for zone in snapshot.zones if zone.lower < 100 < zone.upper)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_one_completed_break_has_distinct_optional_retest_proof(direction):
    bars = [_bar(24, direction)]
    snapshot = _snapshot(direction, {"1D": bars}, hour=24)
    zone = _zone(snapshot)
    proof = zone.break_reclaim_evidence
    assert zone.break_state == "break_confirmed"
    assert proof.state == "BREAK_CONFIRMED"
    assert proof.hold_bars_required == 0
    assert proof.hold_bars_observed == 0
    assert proof.retest_required is False
    assert proof.retest_observed is False
    assert proof.completed_bars_used == 1
    assert proof.zone_confirmed_at < proof.break_closed_at == proof.last_completed_at <= proof.as_of
    assert proof.to_dict()["model"] == "break_confirmed_optional_retest_v1"
    warning = "breakout_confirmed_without_retest"
    assert warning in snapshot.quality_flags and warning in zone.quality_flags
    assert not any("reclaimed" in flag for flag in zone.quality_flags)
    directional = classify_for_trade(snapshot, entry=snapshot.current_price, direction=direction)
    assert zone in directional.invalidation_candidates
    assert not directional.opposing_barriers
    legacy = next(row for row in legacy_level_adapter(snapshot, direction=direction)["levels"]
                  if row["zone_id"] == zone.zone_id)
    assert legacy["break_state"] == "break_confirmed"
    assert legacy["break_reclaim_evidence"] == proof.to_dict()
    assert json.loads(json.dumps(snapshot.to_dict()))["zones"][0]["break_state"] == "break_confirmed"
    # Explicit strategy callers can still request the original strict contract.
    strict = evaluate_break_reclaim(
        zone, bars, as_of=snapshot.as_of, direction=direction, timeframe="1D",
        hold_bars=1, require_retest=True,
    )
    assert strict.state == "RECLAIM_PENDING"
    assert strict.to_dict()["model"] == "break_reclaim_close_hold_v1"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["wick", "wrong_side", "future", "open", "at_anchor"])
def test_unconfirmed_crossing_does_not_create_break(direction, kind):
    if kind == "wick":
        bar = _bar(24, direction, delta=-0.1, retest=True)
        bar["high" if direction == "LONG" else "low"] = 100.5 if direction == "LONG" else 99.5
    else:
        bar = _bar(48 if kind == "future" else 0 if kind == "at_anchor" else 24,
                   direction, delta=-0.1 if kind == "wrong_side" else 0.2)
        if kind == "open":
            bar["is_closed"] = False
    assert _zone(_snapshot(direction, {"1D": [bar]}, hour=24)).break_state == "intact"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_retest_is_stronger_evidence_and_drops_warning(direction):
    zone = _zone(_snapshot(direction, {"1D": [_bar(24, direction), _bar(48, direction, retest=True)]}))
    assert zone.break_state == "reclaimed"
    assert zone.break_reclaim_evidence.retest_observed is True
    assert "breakout_confirmed_without_retest" not in zone.quality_flags


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_truncated_fast_timeframe_preserves_daily_retest(direction):
    daily = [_bar(24, direction), _bar(48, direction, retest=True),
             _bar(72, direction), _bar(96, direction)]
    fast = [_bar(hour, direction, duration=4) for hour in range(72, 97, 4)]
    snapshot = _snapshot(direction, {"1D": daily, "4H": fast})
    zone = _zone(snapshot)
    assert zone.break_state == "reclaimed"
    assert zone.break_reclaim_evidence.timeframe == "1D"
    assert zone.break_reclaim_evidence.break_closed_at == BASE + timedelta(hours=24)
    assert "breakout_confirmed_without_retest" not in snapshot.quality_flags


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("failed_tf", ["1D", "4H"])
def test_newer_failed_close_resets_all_timeframes(direction, failed_tf):
    old = [_bar(24, direction), _bar(48, direction, retest=True)]
    bars = {"1D": old, "4H": [_bar(52, direction, duration=4)]}
    bars[failed_tf] = [*bars[failed_tf], _bar(72, direction, delta=-0.1, duration=4)]
    zone = _zone(_snapshot(direction, bars))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.break_closed_at is None
    assert zone.break_reclaim_evidence.last_completed_at == BASE + timedelta(hours=72)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("same_tf", [False, True])
def test_same_time_conflicts_fail_closed_and_are_order_invariant(direction, same_tf):
    old = [_bar(24, direction), _bar(48, direction, retest=True)]
    positive, negative = _bar(72, direction), _bar(72, direction, delta=-0.1)
    bars = {"1D": old + [positive, negative]} if same_tf else {"1D": old + [positive], "4H": [negative]}
    first = _snapshot(direction, bars)
    reordered = _snapshot(direction, {tf: list(reversed(values)) for tf, values in reversed(list(bars.items()))})
    assert first.to_dict() == reordered.to_dict()
    assert _zone(first).break_state == "intact"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("new_retest", [False, True])
def test_new_break_after_global_reset_cannot_borrow_old_retest(direction, new_retest):
    bars = {"1D": [_bar(24, direction), _bar(48, direction, retest=True)],
            "4H": [_bar(72, direction, delta=-0.1, duration=4), _bar(76, direction, duration=4)]}
    if new_retest:
        bars["4H"].append(_bar(80, direction, retest=True, duration=4))
    zone = _zone(_snapshot(direction, bars))
    assert zone.break_state == ("reclaimed" if new_retest else "break_confirmed")
    assert zone.break_reclaim_evidence.break_closed_at == BASE + timedelta(hours=76)
    assert zone.break_reclaim_evidence.retest_observed is new_retest


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_overlap_wick_before_new_break_cannot_prove_retest(direction):
    bars = {"4H": [_bar(72, direction, delta=-0.1, duration=4),
                    _bar(76, direction, duration=4), _bar(80, direction, retest=True, duration=12)]}
    zone = _zone(_snapshot(direction, bars))
    assert zone.break_state == "break_confirmed"
    assert zone.break_reclaim_evidence.retest_observed is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_inside_break_buffer_after_reset_is_not_a_new_break(direction):
    bars = {"1D": [_bar(24, direction), _bar(48, direction, retest=True), _bar(96, direction, delta=0.025)],
            "4H": [_bar(72, direction, delta=-0.1, duration=4)]}
    assert _zone(_snapshot(direction, bars)).break_state == "intact"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_future_failure_does_not_change_fixed_snapshot(direction):
    bars = [_bar(24, direction)]
    prefix = _snapshot(direction, {"1D": bars}, hour=24)
    future = _snapshot(direction, {"1D": bars + [_bar(48, direction, delta=-0.1)]}, hour=24)
    assert prefix.to_dict() == future.to_dict()


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("changes", [
    {"break_closed_at": None}, {"hold_bars_required": 1}, {"retest_required": True},
    {"retest_observed": True},
    {"retest_required": None}, {"retest_observed": 0},
    {"hold_bars_required": False}, {"hold_bars_observed": 0.5},
    {"completed_bars_used": True}, {"completed_bars_used": 1.5},
    {"completed_bars_used": 0}, {"last_completed_close": 100.0},
    {"break_closed_at": BASE + timedelta(hours=120)},
])
def test_confirmed_break_dataclass_rejects_invalid_certificates(direction, changes):
    proof = _zone(_snapshot(direction, {"1D": [_bar(24, direction)]})).break_reclaim_evidence
    with pytest.raises(ValueError):
        replace(proof, **changes)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_optional_retest_proof_preserves_exact_historical_geometry_binding(direction):
    time = BASE + timedelta(hours=24)
    role = "resistance" if direction == "LONG" else "support"
    member = LevelEvidence(
        source_family="horizontal_swing", source_name="same_geometry_later_member",
        timeframe="1D", lower=100, upper=100,
        observed_at=time, confirmed_at=time, data_cutoff_at=time,
        provenance={"role_hint": role},
    )
    zone = _zone(_snapshot(direction, {"1D": [_bar(24, direction)]}, members=[member]))
    proof = zone.break_reclaim_evidence
    assert zone.break_state == "break_confirmed"
    assert proof.to_dict()["model"] == "break_confirmed_optional_retest_v1"
    assert proof.zone_confirmed_at == BASE < zone.confirmed_at
    assert proof.reclaim_history.to_dict() == zone.reclaim_history.to_dict()
    with pytest.raises(ValueError, match="bind this zone"):
        replace(proof, reclaim_history=replace(proof.reclaim_history, zone_id="other"))
    with pytest.raises(ValueError, match="bind this zone"):
        replace(proof, reclaim_history=replace(
            proof.reclaim_history, membership_confirmed_at=BASE + timedelta(hours=48),
        ))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_uncovered_later_membership_cannot_be_upgraded_to_confirmed_break(direction):
    time = BASE + timedelta(hours=48)
    role = "resistance" if direction == "LONG" else "support"
    member = LevelEvidence(
        source_family="horizontal_swing", source_name="uncovered_new_membership",
        timeframe="1D", lower=100, upper=100,
        observed_at=time, confirmed_at=time, data_cutoff_at=time,
        provenance={"role_hint": role},
    )
    zone = _zone(_snapshot(direction, {"1D": [_bar(24, direction)]}, members=[member]))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.reason == "latest_zone_membership_not_covered"
    with pytest.raises(ValueError, match="bind this zone"):
        replace(zone.break_reclaim_evidence, state="BREAK_CONFIRMED",
                hold_bars_required=0, retest_required=False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_post_reset_new_break_preserves_uncovered_membership_diagnostic(direction):
    time = BASE + timedelta(hours=96)
    member = LevelEvidence(
        source_family="horizontal_swing", source_name="later_contained_membership",
        timeframe="1D", lower=100, upper=100,
        observed_at=time, confirmed_at=time, data_cutoff_at=time,
        provenance={"role_hint": "resistance" if direction == "LONG" else "support"},
    )
    bars = {"4H": [_bar(72, direction, delta=-0.1, duration=4),
                    _bar(76, direction, duration=4)]}
    snapshot = _snapshot(direction, bars, members=[member])
    proof = _zone(snapshot).break_reclaim_evidence
    assert _zone(snapshot).break_state == "intact"
    assert proof.state == "RECLAIM_PENDING"
    assert proof.reason == "latest_zone_membership_not_covered"
    assert proof.break_closed_at == BASE + timedelta(hours=76)
    assert "conflicting_completed_bars" not in snapshot.quality_flags
