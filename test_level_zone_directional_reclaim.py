"""A directional role flip cannot remove an obstacle for the opposite trade."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import (
    LevelEvidence,
    build_structure_snapshot,
    classify_for_trade,
    select_trade_structure,
)


BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _reclaimed_snapshot(direction, *, late_current_role=False):
    sign = 1 if direction == "LONG" else -1
    bars = []
    days = (2, 3) if late_current_role else (1, 2)
    for day in days:
        close = 100 + sign * 0.2
        bars.append({
            "open_time": BASE + timedelta(days=day - 1),
            "close_time": BASE + timedelta(days=day),
            "open": close, "close": close,
            "high": max(close + 0.05, 100) if day == days[-1] else close + 0.05,
            "low": min(close - 0.05, 100) if day == days[-1] else close - 0.05,
            "volume": 1000,
        })
    # Both historical roles can legitimately cluster into one physical zone.
    current_role = "resistance" if direction == "LONG" else "support"
    evidence = [LevelEvidence(
        source_family="horizontal_swing", source_name=f"historical_{role}",
        timeframe="1D", lower=100, upper=100,
        observed_at=BASE,
        confirmed_at=BASE + timedelta(days=int(late_current_role and role == current_role)),
        data_cutoff_at=BASE + timedelta(days=int(late_current_role and role == current_role)),
        provenance={"role_hint": role},
    ) for role in ("support", "resistance")]
    snapshot = build_structure_snapshot(
        {"1D": bars}, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(days=days[-1]), current_price=100 + sign * 0.8,
        tick_size=0.01, external_evidence=evidence,
        include_session_levels=False, pivot_left=100, pivot_right=100,
    )
    assert len(snapshot.zones) == 1
    zone = snapshot.zones[0]
    assert zone.break_state == "reclaimed"
    assert zone.break_reclaim_evidence.direction == direction
    assert set(zone.origin_roles) == {"support", "resistance"}
    return snapshot


@pytest.mark.parametrize("break_direction", ["LONG", "SHORT"])
def test_opposite_reclaim_remains_the_first_barrier(break_direction):
    snapshot = _reclaimed_snapshot(break_direction)
    trade_direction = "SHORT" if break_direction == "LONG" else "LONG"
    directional = classify_for_trade(
        snapshot, entry=snapshot.current_price, direction=trade_direction,
    )
    assert directional.opposing_barriers == snapshot.zones
    stop = snapshot.current_price + (2 if trade_direction == "SHORT" else -2)
    decision = select_trade_structure(directional, stop=stop)
    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.nearest_barrier == snapshot.zones[0]
    assert decision.barrier_r == pytest.approx(0.39)


@pytest.mark.parametrize("break_direction", ["LONG", "SHORT"])
def test_same_direction_reclaim_retains_the_invalidation_level(break_direction):
    snapshot = _reclaimed_snapshot(break_direction)
    directional = classify_for_trade(
        snapshot, entry=snapshot.current_price, direction=break_direction,
    )
    assert not directional.opposing_barriers
    assert directional.invalidation_candidates == snapshot.zones


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_other_direction_old_anchor_does_not_invalidate_membership_time_proof(direction):
    snapshot = _reclaimed_snapshot(direction, late_current_role=True)
    zone = snapshot.zones[0]
    assert zone.reclaim_history is not None
    assert zone.break_reclaim_evidence.zone_confirmed_at == zone.confirmed_at
    assert zone.break_reclaim_evidence.reclaim_history is None
    directional = classify_for_trade(
        snapshot, entry=99 if direction == "LONG" else 101, direction=direction,
    )
    assert not directional.opposing_barriers


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("proof_kind", [
    "valid", "missing", "pending", "wrong_zone", "other_cutoff",
    "no_active_break", "missing_hold", "missing_retest", "wrong_side_close",
    "non_boolean_retest",
])
def test_reclaimed_label_alone_cannot_suppress_a_barrier(direction, proof_kind):
    snapshot = _reclaimed_snapshot(direction)
    zone = snapshot.zones[0]
    proof = zone.break_reclaim_evidence
    if proof_kind == "missing":
        proof = None
    elif proof_kind == "pending":
        proof = replace(proof, state="RECLAIM_PENDING")
    elif proof_kind == "wrong_zone":
        proof = replace(proof, zone_id="other-zone")
    elif proof_kind == "other_cutoff":
        proof = replace(proof, as_of=snapshot.as_of + timedelta(days=1))
    elif proof_kind == "no_active_break":
        proof = replace(proof, break_closed_at=None)
    elif proof_kind == "missing_hold":
        proof = replace(proof, hold_bars_required=2)
    elif proof_kind == "missing_retest":
        proof = replace(proof, retest_observed=False)
    elif proof_kind == "wrong_side_close":
        proof = replace(proof, last_completed_close=99 if direction == "LONG" else 101)
    elif proof_kind == "non_boolean_retest":
        proof = replace(proof, retest_observed="false")
    zone = replace(zone, break_reclaim_evidence=proof)
    snapshot = replace(snapshot, zones=(zone,))
    # A proposed entry on the other side must not bypass the directional
    # certificate binding solely because a legacy state label says reclaimed.
    entry = 99 if direction == "LONG" else 101
    directional = classify_for_trade(snapshot, entry=entry, direction=direction)
    assert bool(directional.opposing_barriers) is (proof_kind != "valid")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_opposite_reclaim_is_an_obstacle_even_when_entry_overlaps_zone(direction):
    snapshot = _reclaimed_snapshot(direction)
    opposite = "SHORT" if direction == "LONG" else "LONG"
    directional = classify_for_trade(snapshot, entry=100, direction=opposite)
    decision = select_trade_structure(directional, stop=102 if opposite == "SHORT" else 98)
    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.reason == "entry_overlaps_opposing_barrier"
    assert decision.barrier_distance == 0


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("proof_kind", [
    "valid", "missing", "opposite", "missing_hold", "future_second", "non_boolean_retest",
])
def test_plan_builder_verifies_reclaimed_certificates(direction, proof_kind):
    # Run API-calling tests through scripts/run_offline_tests.py only.
    import api

    snapshot = _reclaimed_snapshot(direction)
    zone = snapshot.zones[0]
    proof = zone.break_reclaim_evidence
    if proof_kind == "missing":
        proof = None
    elif proof_kind == "opposite":
        opposite = _reclaimed_snapshot("SHORT" if direction == "LONG" else "LONG")
        proof = opposite.zones[0].break_reclaim_evidence
    elif proof_kind == "missing_hold":
        proof = replace(proof, hold_bars_required=2)
    elif proof_kind == "future_second":
        proof = replace(proof, as_of=snapshot.as_of + timedelta(seconds=1))
    elif proof_kind == "non_boolean_retest":
        proof = replace(proof, retest_observed="false")
    snapshot = replace(snapshot, zones=(replace(zone, break_reclaim_evidence=proof),))
    diagnostics = {}
    plan = api._build_structured_trade_setup(
        direction, snapshot.current_price, 1, 0, 0, 0, 0,
        structure_snapshot=snapshot, require_causal_structure=True, diagnostics=diagnostics,
    )
    if proof_kind == "valid":
        assert plan is not None, diagnostics
        assert "breakout_confirmed_without_retest" not in plan.get("warning_codes", [])
    else:
        assert plan is None
        assert diagnostics["reason"] == (
            "crossed_resistance_unconfirmed" if direction == "LONG"
            else "crossed_support_unconfirmed"
        )
