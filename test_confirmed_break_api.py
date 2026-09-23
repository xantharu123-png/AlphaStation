"""Independent API boundary tests for optional-retest confirmed break evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules.level_zones import LevelEvidence, build_structure_snapshot


BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _iso(minutes=0):
    return (BASE + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _payload(direction="LONG"):
    long = direction == "LONG"
    proof = {
        "model": "break_confirmed_optional_retest_v1", "state": "BREAK_CONFIRMED",
        "direction": direction, "zone_id": "confirmed-zone", "boundary": 101.0 if long else 100.0,
        "zone_confirmed_at": _iso(), "timeframe": "5m", "as_of": _iso(6),
        "break_closed_at": _iso(5), "last_completed_at": _iso(5),
        "last_completed_close": 102.0 if long else 99.0,
        "hold_bars_required": 0, "hold_bars_observed": 0, "completed_bars_used": 1,
        "retest_required": False, "retest_observed": False,
    }
    barrier = {
        "side": "resistance" if long else "support", "zone_id": "confirmed-zone",
        "zone_low": 100.0, "zone_high": 101.0, "price": proof["boundary"],
        "confirmed_at": _iso(), "timeframe": "5m", "distance_r": 0.25,
        "action": "BREAK_RECLAIM_REQUIRED" if long else "BREAK_SUPPORT_REQUIRED",
        "break_reclaim_evidence": proof,
    }
    row = {
        "Symbol": "TEST", "direction": direction, "price": 102.0 if long else 99.0,
        "scan_price_observed_at": _iso(6), "nearest_barrier": barrier,
        "entry": 102.0 if long else 99.0, "stop_loss": 98.0 if long else 103.0,
        "tp1": 110.0 if long else 91.0, "tp2": 115.0 if long else 86.0,
        "barrier_gate": barrier["action"], "structure_status": "WAIT_BREAK_RECLAIM",
        "trade_action": "WAIT_FOR_BREAK_RECLAIM", "execution_trigger_ok": False,
        "risk_flags": ["thin_orderbook", "near_overhead_resistance" if long else "near_underlying_support"],
        "trade_setup": {"direction": direction, "barrier_gate": barrier["action"],
                        "structure_status": "WAIT_BREAK_RECLAIM"},
    }
    return row, barrier, proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_fresh_confirmed_break_accepts_without_claiming_retest_or_execution(direction):
    row, barrier, proof = _payload(direction)
    assert api._confirmed_break_reclaim_evidence(row, barrier) is None
    assert api._confirmed_trade_break_evidence(row, barrier) == proof
    result = api._apply_trade_barrier_gate(row, "audit")
    assert result["reclaimed"] is False
    assert result["breakout_confirmed"] is True
    assert row["structure_status"] == "ACCEPT_AFTER_BREAKOUT"
    assert row["barrier_gate_active"] is False
    assert row["execution_trigger_ok"] is False
    assert "thin_orderbook" in row["risk_flags"]
    assert row["retest_status"] == "not_confirmed"
    assert "breakout_confirmed_without_retest" in row["warning_codes"]
    assert api._alert_trade_plan_ok(row, require_native_levels=False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("changes", [
    {"zone_id": "other"}, {"boundary": 98.0}, {"zone_confirmed_at": _iso(-60)},
    {"break_closed_at": _iso()}, {"break_closed_at": _iso(7)},
    {"last_completed_at": _iso(4)}, {"as_of": _iso(8)}, {"timeframe": "bad"},
    {"state": "RECLAIMED"}, {"hold_bars_required": 1}, {"hold_bars_required": False},
    {"hold_bars_observed": True}, {"completed_bars_used": 0}, {"completed_bars_used": 0.5},
    {"retest_required": True}, {"retest_required": "false"}, {"retest_observed": True},
])
def test_optional_break_certificate_rejects_tampering(direction, changes):
    row, barrier, proof = _payload(direction)
    proof.update(changes)
    assert api._confirmed_trade_break_evidence(row, barrier) is None
    assert not api._alert_trade_plan_ok(row, require_native_levels=False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["current_wrong_side", "last_wrong_side", "stale", "wrong_direction", "wrong_row_direction"])
def test_optional_break_rejects_wrong_side_direction_and_staleness(direction, kind):
    row, barrier, proof = _payload(direction)
    wrong = 99.0 if direction == "LONG" else 102.0
    if kind == "current_wrong_side":
        row["price"] = wrong
    elif kind == "last_wrong_side":
        proof["last_completed_close"] = wrong
    elif kind == "stale":
        row["scan_price_observed_at"] = _iso(30)
    elif kind == "wrong_row_direction":
        row["direction"] = "SHORT" if direction == "LONG" else "LONG"
        row["trade_setup"]["direction"] = row["direction"]
    else:
        proof["direction"] = "SHORT" if direction == "LONG" else "LONG"
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_true_retest_retains_legacy_strict_model(direction):
    row, barrier, proof = _payload(direction)
    proof.update(model="break_reclaim_close_hold_v1", state="RECLAIMED",
                 hold_bars_required=1, hold_bars_observed=1, completed_bars_used=2,
                 retest_required=True, retest_observed=True,
                 last_completed_at=_iso(10), as_of=_iso(11))
    row["scan_price_observed_at"] = _iso(11)
    assert api._confirmed_break_reclaim_evidence(row, barrier) == proof
    result = api._apply_trade_barrier_gate(row, "audit")
    assert result["reclaimed"] is True
    assert row["structure_status"] == "ACCEPT_AFTER_RECLAIM"
    assert "breakout_confirmed_without_retest" not in row.get("warning_codes", [])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["current_wrong_side", "stale"])
def test_previously_accepted_break_is_revalidated_before_reuse(direction, kind):
    row, barrier, _proof = _payload(direction)
    api._apply_trade_barrier_gate(row, "audit")
    assert row["structure_status"] == "ACCEPT_AFTER_BREAKOUT"
    if kind == "current_wrong_side":
        row["price"] = 99.0 if direction == "LONG" else 102.0
    else:
        row["scan_price_observed_at"] = _iso(30)
    assert api._confirmed_trade_break_evidence(row, row["nearest_barrier"]) is None
    assert api._structural_barrier_alert_reason(row) is not None
    assert not api._alert_trade_plan_ok(row, require_native_levels=False)
    api._apply_trade_barrier_gate(row, "audit")
    assert row["barrier_gate_active"] is True
    assert row["structure_status"] != "ACCEPT"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_confirmed_break_cannot_erase_an_unrelated_structure_rejection(direction):
    row, _barrier, _proof = _payload(direction)
    row.update(structure_status="REJECT", structure_reason="invalid_stop_risk",
               structure_decision={"status": "REJECT", "reason": "invalid_stop_risk"})
    row["trade_setup"].update(structure_status="REJECT", structure_reason="invalid_stop_risk",
                              structure_decision={"status": "REJECT", "reason": "invalid_stop_risk"})
    api._apply_trade_barrier_gate(row, "audit")
    assert row["structure_status"] == "REJECT"
    assert row["structure_reason"] == "invalid_stop_risk"
    assert not api._alert_trade_plan_ok(row, require_native_levels=False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_confirmed_break_does_not_bypass_reward_risk_or_stop_validation(direction):
    row, _barrier, _proof = _payload(direction)
    api._apply_trade_barrier_gate(row, "audit")
    low_reward = deepcopy(row)
    low_reward["tp1"] = row["entry"] + (0.25 if direction == "LONG" else -0.25)
    assert not api._alert_trade_plan_ok(low_reward, require_native_levels=False)
    wrong_stop = deepcopy(row)
    wrong_stop["stop_loss"] = row["entry"] + (1 if direction == "LONG" else -1)
    assert not api._alert_trade_plan_ok(wrong_stop, require_native_levels=False)


def _native_snapshot(direction, *, retest=False, future=False):
    sign = 1 if direction == "LONG" else -1
    role = "resistance" if direction == "LONG" else "support"
    evidence = LevelEvidence("horizontal_swing", "known_boundary", "1D", 100, 100,
                             BASE, BASE, BASE, provenance={"role_hint": role})
    bars = []
    for day in (1, 2) if retest else (1,):
        close = 100 + sign * 0.2
        bars.append({"open_time": BASE + timedelta(days=day - 1),
                     "close_time": BASE + timedelta(days=day), "open": close,
                     "high": max(close + 0.05, 100 if day == 2 else close),
                     "low": min(close - 0.05, 100 if day == 2 else close),
                     "close": close, "volume": 1000})
    cutoff = BASE if future else BASE + timedelta(days=2 if retest else 1)
    return build_structure_snapshot(
        {"1D": bars}, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=cutoff, current_price=100 + sign * 0.8, tick_size=0.01,
        external_evidence=[evidence], include_session_levels=False, pivot_left=3, pivot_right=3,
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("retest", [False, True])
def test_real_snapshot_to_native_plan_keeps_break_and_retest_distinct(direction, retest):
    snapshot = _native_snapshot(direction, retest=retest)
    result = api._build_structured_trade_setup(
        direction, snapshot.current_price, 1, 0, 0, 0, 0,
        structure_snapshot=snapshot, require_causal_structure=True,
    )
    assert result is not None
    zone = snapshot.zones[0]
    assert result["stop"] < zone.lower if direction == "LONG" else result["stop"] > zone.upper
    if not retest:
        assert result["retest_status"] == "not_confirmed"
        assert result["breakout_evidence"][0]["state"] == "BREAK_CONFIRMED"
    else:
        assert "breakout_confirmed_without_retest" not in result.get("warning_codes", [])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_future_break_cannot_create_native_plan(direction):
    snapshot = _native_snapshot(direction, future=True)
    assert api._build_structured_trade_setup(
        direction, snapshot.current_price, 1, 0, 0, 0, 0,
        structure_snapshot=snapshot, require_causal_structure=True,
    ) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("mutation", [None, "zone", "bounds", "anchor", "membership", "missing"])
def test_optional_break_historical_geometry_must_match_exactly(direction, mutation):
    row, barrier, proof = _payload(direction)
    history = {"model": "connected_role_geometry_v1", "zone_id": barrier["zone_id"],
               "lower": barrier["zone_low"], "upper": barrier["zone_high"],
               "membership_confirmed_at": _iso(5), "confirmed_at_by_direction": {direction: _iso()}}
    barrier.update(confirmed_at=_iso(5), reclaim_history=deepcopy(history))
    proof["reclaim_history"] = deepcopy(history)
    if mutation == "zone":
        proof["reclaim_history"]["zone_id"] = "other"
    elif mutation == "bounds":
        proof["reclaim_history"]["lower"] = 99
    elif mutation == "anchor":
        proof["reclaim_history"]["confirmed_at_by_direction"][direction] = _iso(-5)
    elif mutation == "membership":
        proof["reclaim_history"]["membership_confirmed_at"] = _iso(10)
        barrier["reclaim_history"]["membership_confirmed_at"] = _iso(10)
        barrier["confirmed_at"] = _iso(10)
    elif mutation == "missing":
        barrier.pop("reclaim_history")
    result = api._confirmed_trade_break_evidence(row, barrier)
    assert result == proof if mutation is None else result is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_barrier_warning_lifecycle_is_idempotent_and_preserves_other_warnings(direction):
    row, _barrier, _proof = _payload(direction)
    row["warning_codes"] = ["unrelated_warning"]
    api._apply_trade_barrier_gate(row, "audit")
    api._apply_trade_barrier_gate(row, "audit")
    assert row["warning_codes"] == ["unrelated_warning", "breakout_confirmed_without_retest"]
    row["scan_price_observed_at"] = _iso(30)
    api._apply_trade_barrier_gate(row, "audit")
    assert row["barrier_gate_active"] is True
    assert row["warning_codes"] == ["unrelated_warning"]
    assert "retest_warning" not in row
    assert "retest_warning" not in row["trade_setup"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_true_retest_clears_only_obsolete_optional_retest_warning(direction):
    row, _barrier, _proof = _payload(direction)
    row["warning_codes"] = ["unrelated_warning"]
    api._apply_trade_barrier_gate(row, "audit")
    proof = deepcopy(row["nearest_barrier"]["break_reclaim"])
    proof.update(model="break_reclaim_close_hold_v1", state="RECLAIMED",
                 hold_bars_required=1, hold_bars_observed=1, completed_bars_used=2,
                 retest_required=True, retest_observed=True,
                 last_completed_at=_iso(10), as_of=_iso(11))
    row["nearest_barrier"].update(break_reclaim=proof, break_reclaim_evidence=proof)
    row["scan_price_observed_at"] = _iso(11)
    api._apply_trade_barrier_gate(row, "audit")
    assert row["structure_status"] == "ACCEPT_AFTER_RECLAIM"
    assert row["warning_codes"] == ["unrelated_warning"]
    assert "retest_warning" not in row
