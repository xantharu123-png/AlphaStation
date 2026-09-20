"""Historical reclaim anchors must remain bound to the current causal zone."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules.level_zones import (
    LevelEvidence, build_level_zones, build_structure_snapshot, evaluate_break_reclaim,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _iso(minutes=0):
    return (BASE + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _payload(direction="LONG"):
    role = "resistance" if direction == "LONG" else "support"
    history = {
        "model": "connected_role_geometry_v1", "zone_id": "test-current-zone",
        "lower": 100.0, "upper": 101.0,
        "membership_confirmed_at": _iso(10),
        "confirmed_at_by_direction": {direction: _iso()},
    }
    evidence = {
        "model": "break_reclaim_close_hold_v2", "state": "RECLAIMED",
        "direction": direction, "zone_id": history["zone_id"],
        "boundary": 101.0 if direction == "LONG" else 100.0,
        "zone_confirmed_at": _iso(), "timeframe": "5m", "as_of": _iso(11),
        "break_closed_at": _iso(5), "last_completed_at": _iso(10),
        "last_completed_close": 101.5 if direction == "LONG" else 99.5,
        "hold_bars_required": 1, "hold_bars_observed": 1,
        "completed_bars_used": 2, "retest_required": True, "retest_observed": True,
        "reclaim_history": deepcopy(history),
    }
    barrier = {
        "side": role, "zone_id": history["zone_id"], "zone_low": 100.0,
        "zone_high": 101.0, "price": evidence["boundary"],
        "confirmed_at": _iso(10), "reclaim_history": history,
        "action": "BREAK_RECLAIM_REQUIRED" if direction == "LONG" else "BREAK_SUPPORT_REQUIRED",
        "break_reclaim_evidence": evidence,
    }
    row = {"scan_price_observed_at": _iso(11), "nearest_barrier": barrier}
    return row, barrier, evidence


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_v2_bound_historical_geometry_is_accepted(direction):
    row, barrier, evidence = _payload(direction)
    assert api._confirmed_break_reclaim_evidence(row, barrier) == evidence


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_zone_history_roundtrips_through_final_mail_evidence_gate(direction):
    role = "resistance" if direction == "LONG" else "support"
    opposite = "support" if direction == "LONG" else "resistance"
    old = LevelEvidence(
        "horizontal_swing", "original_" + role, "5m", 100.0, 101.0,
        BASE, BASE, BASE, provenance={"role_hint": role},
    )
    late = LevelEvidence(
        "session", "new_" + opposite, "5m", 100.5, 100.5,
        BASE + timedelta(minutes=10), BASE + timedelta(minutes=10),
        BASE + timedelta(minutes=10), provenance={"role_hint": opposite},
    )
    zone = build_level_zones([old, late], reference_price=102 if direction == "LONG" else 99)[0]
    bars = [
        {"open_time": BASE, "close_time": BASE + timedelta(minutes=5),
         "open": 100.5, "high": 102.2, "low": 98.8,
         "close": 102 if direction == "LONG" else 99, "volume": 1000},
        {"open_time": BASE + timedelta(minutes=5), "close_time": BASE + timedelta(minutes=10),
         "open": 102 if direction == "LONG" else 99,
         "high": 102.1 if direction == "LONG" else 100.1,
         "low": 100.9 if direction == "LONG" else 98.9,
         "close": 101.5 if direction == "LONG" else 99.5, "volume": 1000},
    ]
    evidence = evaluate_break_reclaim(
        zone, bars, as_of=BASE + timedelta(minutes=11), direction=direction,
        timeframe="5m", hold_bars=1, require_retest=True,
    ).to_dict()
    assert evidence["model"] == "break_reclaim_close_hold_v2"
    assert evidence["state"] == "RECLAIMED"
    row, barrier, _ = _payload(direction)
    barrier.update(zone_id=zone.zone_id, zone_low=zone.lower, zone_high=zone.upper,
                   confirmed_at=zone.to_dict()["confirmed_at"],
                   reclaim_history=zone.to_dict()["reclaim_history"],
                   break_reclaim_evidence=evidence)
    assert api._confirmed_break_reclaim_evidence(row, barrier) == evidence


@pytest.mark.parametrize("mutation", [
    "wrong_direction", "wrong_zone_id", "wrong_boundary", "missing_proof_history",
    "missing_barrier_history", "unknown_basis", "wrong_basis_zone", "expanded_lower",
    "expanded_upper", "mismatched_membership", "missing_anchor", "mismatched_anchor",
    "future_anchor", "anchor_equals_membership", "future_membership", "invalid_geometry",
    "future_as_of", "missing_retest", "incomplete_hold", "failed_latest_close",
    "unknown_model", "bad_anchor_type", "bare_history_map",
    "stale_trigger_before_membership", "other_direction_future_anchor",
])
def test_v2_missing_or_mutated_binding_fails_closed(mutation):
    row, barrier, evidence = _payload()
    if mutation == "wrong_direction":
        evidence["direction"] = "SHORT"
    elif mutation == "wrong_zone_id":
        evidence["zone_id"] = "another-zone"
    elif mutation == "wrong_boundary":
        evidence["boundary"] = 100.0
    elif mutation == "missing_proof_history":
        evidence.pop("reclaim_history")
    elif mutation == "missing_barrier_history":
        barrier.pop("reclaim_history")
    elif mutation == "unknown_basis":
        for obj in (barrier, evidence):
            obj["reclaim_history"]["model"] = "arbitrary-assertion"
    elif mutation == "wrong_basis_zone":
        for obj in (barrier, evidence):
            obj["reclaim_history"]["zone_id"] = "older-zone"
    elif mutation in ("expanded_lower", "expanded_upper"):
        barrier["zone_low" if mutation == "expanded_lower" else "zone_high"] += 0.2
    elif mutation == "mismatched_membership":
        evidence["reclaim_history"]["membership_confirmed_at"] = _iso(9)
    elif mutation == "missing_anchor":
        for obj in (barrier, evidence):
            obj["reclaim_history"]["confirmed_at_by_direction"] = {"SHORT": _iso()}
    elif mutation == "mismatched_anchor":
        evidence["zone_confirmed_at"] = _iso(1)
    elif mutation in ("future_anchor", "anchor_equals_membership", "bad_anchor_type"):
        anchor = _iso(12) if mutation == "future_anchor" else _iso(10) if mutation == "anchor_equals_membership" else True
        evidence["zone_confirmed_at"] = anchor
        for obj in (barrier, evidence):
            obj["reclaim_history"]["confirmed_at_by_direction"]["LONG"] = anchor
    elif mutation == "future_membership":
        barrier["confirmed_at"] = _iso(12)
        for obj in (barrier, evidence):
            obj["reclaim_history"]["membership_confirmed_at"] = _iso(12)
    elif mutation == "stale_trigger_before_membership":
        barrier["confirmed_at"] = _iso(11)
        for obj in (barrier, evidence):
            obj["reclaim_history"]["membership_confirmed_at"] = _iso(11)
    elif mutation == "other_direction_future_anchor":
        for obj in (barrier, evidence):
            obj["reclaim_history"]["confirmed_at_by_direction"]["SHORT"] = _iso(12)
    elif mutation == "invalid_geometry":
        for obj in (barrier, evidence):
            obj["reclaim_history"]["lower"] = 102.0
    elif mutation == "future_as_of":
        evidence["as_of"] = _iso(12)
    elif mutation == "missing_retest":
        evidence["retest_observed"] = False
    elif mutation == "incomplete_hold":
        evidence["hold_bars_observed"] = 0
    elif mutation == "failed_latest_close":
        evidence["last_completed_close"] = 100.5
    elif mutation == "unknown_model":
        evidence["model"] = "break_reclaim_close_hold_v99"
    elif mutation == "bare_history_map":
        for obj in (barrier, evidence):
            obj["reclaim_history"] = {"LONG": _iso()}
    assert api._confirmed_break_reclaim_evidence(row, barrier) is None


def test_v1_cannot_borrow_new_history_to_avoid_original_timestamp_equality():
    row, barrier, evidence = _payload()
    evidence["model"] = "break_reclaim_close_hold_v1"
    assert api._confirmed_break_reclaim_evidence(row, barrier) is None
    barrier["confirmed_at"] = _iso()
    evidence.pop("reclaim_history")
    barrier.pop("reclaim_history")
    assert api._confirmed_break_reclaim_evidence(row, barrier) == evidence


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_valid_v2_clears_only_the_barrier_gate_not_execution_trigger(direction):
    row, barrier, evidence = _payload(direction)
    row.update(Price=evidence["last_completed_close"], direction=direction,
               entry=99.0 if direction == "LONG" else 102.0,
               stop_loss=98.0 if direction == "LONG" else 103.0,
               tp1=105.0 if direction == "LONG" else 96.0,
               trade_action="WAIT_FOR_BREAK_RECLAIM", trade_signal="WARTEN",
               barrier_gate=barrier["action"], execution_trigger_ok=False,
               risk_flags=["near_overhead_resistance", "thin_orderbook"])
    api._apply_trade_barrier_gate(row, "early_movers")
    assert row["barrier_gate_active"] is False
    assert row["structure_status"] == "ACCEPT_AFTER_RECLAIM"
    assert row["execution_trigger_ok"] is False
    assert "thin_orderbook" in row["risk_flags"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("lower,upper", [(100.0, 101.0), (100.1234567, 101.7654321)])
@pytest.mark.parametrize("historical", [False, True])
def test_native_barrier_projection_preserves_bound_history_without_releasing_gate(direction, lower, upper, historical):
    role = "resistance" if direction == "LONG" else "support"
    opposite = "support" if direction == "LONG" else "resistance"
    entry = 99.0 if direction == "LONG" else 102.0
    evidence = [
        LevelEvidence("horizontal_swing", "old_" + role, "5m", lower, upper,
                      BASE, BASE, BASE, provenance={"role_hint": role}),
        LevelEvidence("session", "late_" + opposite, "5m", (lower + upper) / 2, (lower + upper) / 2,
                      BASE + timedelta(minutes=10), BASE + timedelta(minutes=10),
                      BASE + timedelta(minutes=10), provenance={"role_hint": opposite}),
        LevelEvidence("horizontal_swing", "stop_" + opposite, "5m",
                      95.0 if direction == "LONG" else 105.0,
                      95.0 if direction == "LONG" else 105.0,
                      BASE, BASE, BASE, provenance={"role_hint": opposite}),
    ]
    if not historical:
        evidence = [item for item in evidence if not item.source_name.startswith("late_")]
    bars = [{"open_time": BASE + timedelta(minutes=minute - 5),
             "close_time": BASE + timedelta(minutes=minute), "open": entry,
             "high": entry + 0.2, "low": entry - 0.2, "close": entry, "volume": 1000}
            for minute in (5, 10)]
    snapshot = build_structure_snapshot(
        {"5m": bars}, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(minutes=11), current_price=entry,
        external_evidence=evidence, include_session_levels=False,
    )
    zone = next(zone for zone in snapshot.zones if "old_" + role in zone.source_names)
    plan = api._build_structured_trade_setup(
        direction, entry, 1.0, None, None, None, None, current_price=entry,
        structure_snapshot=snapshot, require_causal_structure=True,
    )
    assert plan is not None
    if historical:
        assert plan["nearest_barrier"]["reclaim_history"] == zone.to_dict()["reclaim_history"]
    else:
        assert "reclaim_history" not in plan["nearest_barrier"]
    assert plan["nearest_barrier"]["confirmed_at"] == _iso(10 if historical else 0)
    assert plan["barrier_gate_active"] is True
    barrier = plan["nearest_barrier"]
    assert barrier["zone_low"] == zone.lower
    assert barrier["zone_high"] == zone.upper
    boundary = zone.upper if direction == "LONG" else zone.lower
    assert barrier["reclaim_boundary"] == boundary
    # The later proof is computed by the real evaluator against the same exact
    # zone, not against its rounded display price or a hand-written certificate.
    close = boundary + 0.5 if direction == "LONG" else boundary - 0.5
    later_bars = [{"open_time": BASE + timedelta(minutes=minute - 5),
                   "close_time": BASE + timedelta(minutes=minute), "open": close,
                   "high": max(close, boundary) + 0.02,
                   "low": min(close, boundary) - 0.02, "close": close, "volume": 1000}
                  for minute in (15, 20)]
    proof = evaluate_break_reclaim(
        zone, later_bars, as_of=BASE + timedelta(minutes=21), direction=direction,
        timeframe="5m", hold_bars=1, require_retest=True,
    ).to_dict()
    assert proof["model"] == ("break_reclaim_close_hold_v2" if historical else "break_reclaim_close_hold_v1")
    assert proof["state"] == "RECLAIMED"
    barrier["break_reclaim_evidence"] = proof
    row = {"scan_price_observed_at": _iso(21), "nearest_barrier": barrier}
    assert api._confirmed_break_reclaim_evidence(row, barrier) == proof
