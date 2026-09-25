"""Causal zone membership must not erase an already valid role transition."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import (
    BreakReclaimEvidence,
    LevelEvidence,
    build_level_zones,
    build_structure_snapshot,
    evaluate_break_reclaim,
    legacy_level_adapter,
)


BASE = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _evidence(role, *, day=0, lower=100.0, upper=None, family="horizontal_swing",
              name=None, projection=False, timeframe="1D"):
    time = BASE + timedelta(days=day)
    return LevelEvidence(
        source_family=family, source_name=name or role, timeframe=timeframe,
        lower=lower, upper=lower if upper is None else upper,
        observed_at=time, confirmed_at=time, data_cutoff_at=time,
        provenance={"role_hint": role}, projection_only=projection,
    )


def _bar(day, direction, *, failure=False):
    closed = BASE + timedelta(days=day)
    sign = 1 if direction == "LONG" else -1
    close = 100 + sign * (-0.1 if failure else 0.2)
    high, low = ((100.4, 100.0) if direction == "LONG" else (100.0, 99.6))
    return {"open_time": closed - timedelta(days=1), "close_time": closed,
            "open": close, "high": max(high, close), "low": min(low, close),
            "close": close, "volume": 1000}


def _role(direction):
    return "resistance" if direction == "LONG" else "support"


def _snapshot(direction, evidence, bars=None, *, day=2, sessions=False):
    return build_structure_snapshot(
        {"1D": bars or [_bar(1, direction), _bar(2, direction)]},
        symbol="TEST", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(days=day), current_price=100.2 if direction == "LONG" else 99.8,
        tick_size=0.01, include_session_levels=sessions, external_evidence=evidence,
        pivot_left=3, pivot_right=3,
    )


def _zone(snapshot):
    # Reference close labels now remain separately visible at the same price.
    # This suite checks the actual role-bearing boundary, not its annotation.
    return next(zone for zone in snapshot.zones
                if zone.lower < 100 < zone.upper and zone.origin_roles)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_session_member_does_not_erase_existing_reclaim(direction):
    old = _evidence(_role(direction))
    before = _zone(_snapshot(direction, [old]))
    after = _zone(_snapshot(direction, [old], sessions=True))

    assert (before.lower, before.upper) == (after.lower, after.upper)
    assert before.break_state == "reclaimed"
    assert after.break_state == "reclaimed"
    assert after.confirmed_at == BASE + timedelta(days=2)
    proof = after.break_reclaim_evidence
    assert proof.zone_confirmed_at == BASE
    assert proof.completed_bars_used == 2
    assert proof.retest_observed
    assert proof.break_closed_at == BASE + timedelta(days=1)
    assert proof.to_dict()["model"] == "break_reclaim_close_hold_v2"
    assert dict(after.reclaim_confirmation_times)[direction] == BASE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("late_kind", ["same_role", "opposite_role", "reference", "projection"])
def test_contained_late_evidence_preserves_old_role_proof(direction, late_kind):
    role = _role(direction)
    opposite = "support" if role == "resistance" else "resistance"
    late = _evidence(
        role if late_kind in {"same_role", "projection"} else
        opposite if late_kind == "opposite_role" else "reference",
        day=2, projection=late_kind == "projection",
        family="session" if late_kind == "reference" else "horizontal_swing",
        name="PDC" if late_kind == "reference" else None,
    )
    zone = _zone(_snapshot(direction, [_evidence(role), late]))
    assert zone.break_state == "reclaimed"
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("lower,upper", [(99.97, 100.01), (99.99, 100.03)])
def test_only_breakout_edge_extension_resets_directional_history(direction, lower, upper):
    zone = _zone(_snapshot(direction, [
        _evidence(_role(direction)),
        _evidence(_role(direction), day=2, lower=lower, upper=upper),
    ]))
    breakout_edge_changed = upper > 100.02 if direction == "LONG" else lower < 99.98
    assert zone.break_state == ("intact" if breakout_edge_changed else "reclaimed")
    proof = zone.break_reclaim_evidence
    assert proof.zone_confirmed_at == (BASE + timedelta(days=2) if breakout_edge_changed else BASE)
    assert proof.completed_bars_used == (0 if breakout_edge_changed else 2)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_new_disconnected_component_invalidates_earlier_edge_until_late_bridge(direction):
    # The edge itself existed on day 0. A new island on day 1 must still reset
    # proof; looking only for the earliest edge would incorrectly borrow it.
    role = _role(direction)
    edge, other = (100.05, 99.95) if direction == "LONG" else (99.95, 100.05)
    zone = _zone(_snapshot(direction, [
        _evidence(role, lower=edge),
        _evidence(role, day=1, lower=other),
        _evidence(role, day=2, lower=99.97, upper=100.03),
    ]))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE + timedelta(days=2)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("late_role", ["support", "resistance"])
def test_opposite_edge_extension_preserves_only_the_unchanged_direction(direction, late_role):
    lower, upper = (99.97, 100.01) if direction == "LONG" else (99.99, 100.03)
    zone = _zone(_snapshot(direction, [
        _evidence(_role(direction)),
        _evidence(late_role, day=2, lower=lower, upper=upper),
    ]))
    assert zone.break_state == "reclaimed"
    assert zone.confirmed_at == BASE + timedelta(days=2)
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE
    assert zone.reclaim_history.to_dict()["model"] == "connected_role_boundary_v2"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_opposite_edge_growth_does_not_hide_a_subsequent_failed_close(direction):
    lower, upper = (99.97, 100.01) if direction == "LONG" else (99.99, 100.03)
    zone = _zone(_snapshot(direction, [
        _evidence(_role(direction)),
        _evidence(_role(direction), day=3, lower=lower, upper=upper),
    ], [_bar(1, direction), _bar(2, direction), _bar(3, direction, failure=True)], day=3))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.completed_bars_used == 3
    assert zone.break_reclaim_evidence.break_closed_at is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_late_bridge_between_old_disjoint_members_cannot_backdate_geometry(direction):
    role = _role(direction)
    members = [_evidence(role, lower=99.95), _evidence(role, lower=100.05),
               _evidence(role, day=2, lower=99.97, upper=100.03)]
    zone = _zone(_snapshot(direction, members))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE + timedelta(days=2)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_connected_old_geometry_formed_by_multiple_members_is_preserved(direction):
    role = _role(direction)
    members = [_evidence(role, lower=99.99), _evidence(role, lower=100.01),
               _evidence(role, day=2)]
    zone = _zone(_snapshot(direction, members))
    assert zone.break_state == "reclaimed"
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["reference", "projection"])
def test_reference_or_projection_cannot_supply_missing_causal_geometry(direction, kind):
    role = _role(direction)
    # The wider old interval cannot authorize a reclaim. Real role-bearing
    # geometry reaches those bounds only on day 2.
    old = _evidence("reference" if kind == "reference" else role,
                    lower=99.95, upper=100.05,
                    family="session" if kind == "reference" else "fibonacci",
                    name="PDC" if kind == "reference" else "FIB",
                    projection=kind == "projection")
    late = _evidence(role, day=2, lower=99.95, upper=100.05)
    zone = _zone(_snapshot(direction, [old, late]))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE + timedelta(days=2)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_relevant_role_first_appearing_late_cannot_borrow_other_role(direction):
    role = _role(direction)
    opposite = "support" if role == "resistance" else "resistance"
    zone = _zone(_snapshot(direction, [_evidence(opposite), _evidence(role, day=2)]))
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.zone_confirmed_at == BASE + timedelta(days=2)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_later_failed_close_still_invalidates_historical_reclaim(direction):
    role = _role(direction)
    bars = [_bar(1, direction), _bar(2, direction), _bar(3, direction, failure=True)]
    zone = _zone(_snapshot(direction, [_evidence(role), _evidence(role, day=3)], bars, day=3))
    proof = zone.break_reclaim_evidence
    assert zone.break_state == "intact"
    assert proof.state == "INTACT"
    assert proof.break_closed_at is None
    assert proof.last_completed_at == BASE + timedelta(days=3)
    assert proof.completed_bars_used == 3


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_public_evaluator_and_snapshot_have_identical_anchor_metadata(direction):
    snapshot = _snapshot(direction, [_evidence(_role(direction))], sessions=True)
    zone = _zone(snapshot)
    proof = evaluate_break_reclaim(
        zone, [_bar(1, direction), _bar(2, direction)],
        as_of=BASE + timedelta(days=2), direction=direction, timeframe="1D",
        breakout_buffer=0.01, hold_bars=1, require_retest=True, retest_tolerance=0.02,
    )
    assert proof.to_dict() == zone.break_reclaim_evidence.to_dict()
    payload = json.loads(json.dumps(snapshot.to_dict()))
    serialized = next(item for item in payload["zones"] if item["zone_id"] == zone.zone_id)
    assert serialized["confirmed_at"] == "2026-09-08T00:00:00Z"
    assert serialized["reclaim_history"]["confirmed_at_by_direction"][direction] == "2026-09-06T00:00:00Z"
    assert serialized["reclaim_history"] == serialized["break_reclaim_evidence"]["reclaim_history"]
    assert serialized["break_reclaim_evidence"]["zone_confirmed_at"] == "2026-09-06T00:00:00Z"
    assert replace(proof) == proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_future_failure_is_not_consumed_at_fixed_snapshot_cutoff(direction):
    members = [_evidence(_role(direction)), _evidence(_role(direction), day=2)]
    prefix = [_bar(1, direction), _bar(2, direction)]
    before = _snapshot(direction, members, prefix)
    after = _snapshot(direction, members, prefix + [_bar(3, direction, failure=True)])
    assert before.to_dict() == after.to_dict()
    assert _zone(after).break_state == "reclaimed"


def test_zone_cannot_be_evaluated_before_latest_membership_even_with_old_anchor():
    zone = build_level_zones([_evidence("resistance"), _evidence("support", day=2)],
                             reference_price=100.2, tick_size=0.01)[0]
    with pytest.raises(ValueError, match="before it was confirmed"):
        evaluate_break_reclaim(zone, [_bar(1, "LONG")], as_of=BASE + timedelta(days=1),
                               direction="LONG", timeframe="1D")


def test_anchor_metadata_is_immutable_and_rejects_invalid_time_or_direction():
    zone = build_level_zones([_evidence("resistance")], reference_price=100.2)[0]
    assert isinstance(zone.reclaim_confirmation_times, tuple)
    with pytest.raises(ValueError):
        replace(zone, reclaim_confirmation_times=(("SIDEWAYS", BASE),))
    with pytest.raises(ValueError):
        replace(zone, reclaim_confirmation_times=(("LONG", BASE + timedelta(days=1)),))
    with pytest.raises(ValueError):
        replace(zone, reclaim_confirmation_times=(("LONG", BASE), ("LONG", BASE)))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_v2_reclaim_cannot_use_trigger_history_older_than_current_membership(direction):
    zone = build_level_zones(
        [_evidence(_role(direction)), _evidence(_role(direction), day=3)],
        reference_price=100.2, tick_size=0.01,
    )[0]
    proof = evaluate_break_reclaim(
        zone, [_bar(1, direction), _bar(2, direction)], as_of=BASE + timedelta(days=3),
        direction=direction, timeframe="1D", hold_bars=1, require_retest=True,
    )
    assert proof.state == "RECLAIM_PENDING"
    assert proof.reason == "latest_zone_membership_not_covered"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_multitimeframe_selection_does_not_hide_newer_failed_close(direction):
    old_bars = [_bar(1, direction), _bar(2, direction)]
    snapshot = build_structure_snapshot(
        {"4H": old_bars, "1D": old_bars + [_bar(3, direction, failure=True)]},
        symbol="TEST", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(days=3), current_price=100.2 if direction == "LONG" else 99.8,
        tick_size=0.01, include_session_levels=False,
        external_evidence=[_evidence(_role(direction)), _evidence(_role(direction), day=3)],
        pivot_left=3, pivot_right=3,
    )
    zone = _zone(snapshot)
    assert zone.break_state == "intact"
    assert zone.break_reclaim_evidence.timeframe == "1D"
    assert zone.break_reclaim_evidence.last_completed_at == BASE + timedelta(days=3)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_legacy_adapter_preserves_bound_v2_history(direction):
    snapshot = _snapshot(direction, [_evidence(_role(direction))], sessions=True)
    zone = _zone(snapshot)
    row = next(item for item in legacy_level_adapter(snapshot, direction=direction)["levels"]
               if item["zone_id"] == zone.zone_id)
    assert row["reclaim_history"] == zone.break_reclaim_evidence.to_dict()["reclaim_history"]
    assert row["confirmed_at"] == "2026-09-08T00:00:00Z"


@pytest.mark.parametrize("mutate", [
    lambda history: replace(history, zone_id="different"),
    lambda history: replace(history, lower=history.lower + 0.01),
    lambda history: replace(history, confirmation_times=(("SHORT", BASE + timedelta(days=1)),)),
    lambda history: replace(history, membership_confirmed_at=BASE + timedelta(days=3)),
])
def test_v2_evidence_rejects_detached_or_future_geometry_binding(mutate):
    proof = _zone(_snapshot("SHORT", [_evidence("support")], sessions=True)).break_reclaim_evidence
    with pytest.raises(ValueError):
        replace(proof, reclaim_history=mutate(proof.reclaim_history))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0])
def test_history_rejects_invalid_geometry(value):
    proof = _zone(_snapshot("LONG", [_evidence("resistance")], sessions=True)).break_reclaim_evidence
    with pytest.raises(ValueError):
        replace(proof.reclaim_history, lower=value)
