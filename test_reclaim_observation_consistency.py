"""Historical reclaim proof must agree with the row's present observation."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

import api
from test_confirmed_break_api import _payload as breakout_payload, _iso
from test_reclaim_history_mail_validation import _payload as historical_payload


def _payload(direction, model):
    if model == "v1":
        row, barrier, proof = breakout_payload(direction)
        proof.update(model="break_reclaim_close_hold_v1", state="RECLAIMED",
                     hold_bars_required=1, hold_bars_observed=1, completed_bars_used=2,
                     retest_required=True, retest_observed=True,
                     last_completed_at=_iso(10), as_of=_iso(11))
        row["scan_price_observed_at"] = _iso(11)
    else:
        row, barrier, proof = historical_payload(direction)
        row.update(direction=direction, price=102.0 if direction == "LONG" else 99.0,
                   entry=102.0 if direction == "LONG" else 99.0,
                   stop_loss=98.0 if direction == "LONG" else 103.0,
                   tp1=110.0 if direction == "LONG" else 91.0,
                   tp2=115.0 if direction == "LONG" else 86.0,
                   structure_status="WAIT_BREAK_RECLAIM", barrier_gate=barrier["action"],
                   trade_setup={"direction": direction, "structure_status": "WAIT_BREAK_RECLAIM",
                                "barrier_gate": barrier["action"]})
    return row, barrier, proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("model", ["v1", "v2"])
@pytest.mark.parametrize("previously_released", [False, True])
def test_lost_current_boundary_cannot_release_reclaimed_gate(direction, model, previously_released):
    row, barrier, _proof = _payload(direction, model)
    if previously_released:
        api._apply_trade_barrier_gate(row, "audit")
        assert row["structure_status"] == "ACCEPT_AFTER_RECLAIM"
        barrier = row["nearest_barrier"]
    row["price"] = 100.5  # Back inside the zone, not through the protective stop.
    assert api._confirmed_trade_break_evidence(row, barrier) is None
    assert api._structural_barrier_alert_reason(row) == "near_structural_barrier_wait_trigger"
    assert not api._alert_trade_plan_ok(row, require_native_levels=False)
    api._apply_trade_barrier_gate(row, "audit")
    assert row["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert row["barrier_gate_active"] is True
    assert not api._alert_trade_plan_ok(row, require_native_levels=False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("model", ["v1", "v2"])
def test_reclaimed_proof_rejects_explicit_opposite_row_direction(direction, model):
    row, barrier, _proof = _payload(direction, model)
    row["direction"] = "SHORT" if direction == "LONG" else "LONG"
    row["trade_setup"]["direction"] = row["direction"]
    assert api._confirmed_trade_break_evidence(row, barrier) is None
    api._apply_trade_barrier_gate(row, "audit")
    assert row["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert row["barrier_gate_active"] is True


@pytest.mark.parametrize("field", ["Preis", "Price", "price", "current", "current_price"])
@pytest.mark.parametrize("value", [100.5, 0, float("nan"), float("inf"), "invalid"])
def test_all_explicit_observation_aliases_fail_closed_for_lost_or_invalid_price(field, value):
    row, barrier, _proof = _payload("LONG", "v2")
    row.pop("price")
    row[field] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("model", ["v1", "v2"])
def test_daily_reference_price_remains_valid_without_new_intraday_quote(direction, model):
    row, barrier, proof = _payload(direction, model)
    # No live quote/network helper is involved. Freshness stays bound to the
    # row's existing observation and the proof's completed candles.
    origin = datetime.fromisoformat(proof["zone_confirmed_at"].replace("Z", "+00:00"))
    first_close = (origin + timedelta(days=1)).isoformat()
    second_close = (origin + timedelta(days=2)).isoformat()
    observed = (origin + timedelta(days=2, hours=1)).isoformat()
    proof.update(break_closed_at=first_close, last_completed_at=second_close, as_of=observed)
    row["scan_price_observed_at"] = observed
    if model == "v2":
        barrier["confirmed_at"] = second_close
        barrier["reclaim_history"]["membership_confirmed_at"] = second_close
        proof["reclaim_history"]["membership_confirmed_at"] = second_close
    proof["timeframe"] = "1D"
    barrier["timeframe"] = "1D"
    row.update(market_data_mode="starter_swing", trade_horizon="swing")
    assert api._confirmed_trade_break_evidence(row, barrier) == proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_legacy_priceless_historical_evidence_is_still_readable(direction):
    row, barrier, proof = historical_payload(direction)
    assert api._confirmed_break_reclaim_evidence(row, barrier) == proof
    # Entry alone describes a plan, not a current market observation.
    row["entry"] = 100.5
    assert api._confirmed_break_reclaim_evidence(row, barrier) == proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("field", ["Signal_Direction", "BI_Direction", "direction", "_direction", "side", "trade_action"])
@pytest.mark.parametrize("nested", [False, True])
def test_conflicting_explicit_direction_alias_cannot_hide_behind_precedence(direction, field, nested):
    row, barrier, _proof = _payload(direction, "v2")
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if nested:
        field = "trade_action" if field == "trade_action" else "direction"
    source = row["trade_setup"] if nested else row
    source[field] = opposite + "_NOW" if field == "trade_action" else opposite
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("field", ["Preis", "Price", "price", "current", "current_price"])
@pytest.mark.parametrize("value", [100.5, 0, float("nan"), float("inf"), "invalid", True, False])
def test_conflicting_observation_alias_cannot_hide_behind_higher_priority_price(direction, field, value):
    row, barrier, _proof = _payload(direction, "v2")
    good = 102.0 if direction == "LONG" else 99.0
    row.update(Preis=good, Price=good, price=good, current=good, current_price=good)
    row[field] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("value", [True, False])
def test_breakout_only_boolean_entry_fallback_cannot_be_a_price(value):
    row, barrier, _proof = breakout_payload("SHORT")
    for field in ("Preis", "Price", "price", "current", "current_price"):
        row.pop(field, None)
    row["entry"] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), float("-inf")])
def test_reclaim_proof_last_close_must_be_a_finite_positive_nonboolean_price(value):
    row, barrier, proof = _payload("SHORT", "v1")
    proof["last_completed_close"] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


def test_boolean_boundary_is_not_one_dollar_structural_evidence():
    row, barrier, proof = _payload("SHORT", "v1")
    row["price"] = 0.5
    barrier["zone_low"] = True
    proof.update(boundary=True, last_completed_close=0.5)
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("field", ["zone_low", "zone_high", "reclaim_boundary"])
@pytest.mark.parametrize("value", [True, False, 0, -1, float("nan"), float("inf"), "invalid"])
def test_invalid_counterpart_barrier_bounds_are_not_price_geometry(field, value):
    row, barrier, _proof = _payload("SHORT", "v1")
    barrier[field] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


def test_legacy_numeric_string_prices_remain_supported():
    row, barrier, proof = _payload("SHORT", "v1")
    row["price"] = "99.0"
    barrier["zone_low"] = "100.0"
    proof.update(boundary="100.0", last_completed_close="99.0")
    assert api._confirmed_trade_break_evidence(row, barrier) == proof


@pytest.mark.parametrize("model", ["v1", "v2"])
@pytest.mark.parametrize("field", ["completed_bars_used", "hold_bars_required", "hold_bars_observed"])
@pytest.mark.parametrize("value", [True, False, -1, 1.5, float("nan"), float("inf"), "1.5", "invalid", None])
def test_reclaim_counters_are_nonboolean_nonnegative_finite_integer_values(model, field, value):
    row, barrier, proof = _payload("SHORT", model)
    proof.update(completed_bars_used=3, hold_bars_required=0, hold_bars_observed=1)
    proof[field] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("model", ["v1", "v2"])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("field", ["retest_required", "retest_observed"])
@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", "missing"])
def test_reclaim_retest_flags_must_be_explicit_booleans(model, required, field, value):
    row, barrier, proof = _payload("SHORT", model)
    proof.update(retest_required=required, retest_observed=required)
    if value == "missing":
        proof.pop(field)
    else:
        proof[field] = value
    assert api._confirmed_trade_break_evidence(row, barrier) is None


@pytest.mark.parametrize("model", ["v1", "v2"])
def test_legacy_integral_numeric_string_counters_and_optional_retest_remain_valid(model):
    row, barrier, proof = _payload("SHORT", model)
    proof.update(completed_bars_used="2", hold_bars_required="1.0", hold_bars_observed="1",
                 retest_required=False, retest_observed=False)
    assert api._confirmed_trade_break_evidence(row, barrier) == proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_rounded_observation_aliases_on_confirmed_side_and_entry_are_compatible(direction):
    row, barrier, proof = _payload(direction, "v2")
    current = 102.001 if direction == "LONG" else 98.999
    row.update(Preis=round(current, 2), price=current, current_price=current,
               entry=100.5, SpotPrice=0.0001)
    assert api._confirmed_trade_break_evidence(row, barrier) == proof


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("history_model", ["connected_role_geometry_v1", "connected_role_boundary_v2"])
@pytest.mark.parametrize("breakout", [False, True])
def test_explicit_history_model_allowlist_keeps_exact_binding(direction, history_model, breakout):
    row, barrier, proof = _payload(direction, "v2")
    barrier["reclaim_history"]["model"] = history_model
    proof["reclaim_history"]["model"] = history_model
    if breakout:
        proof.update(model="break_confirmed_optional_retest_v1", state="BREAK_CONFIRMED",
                     hold_bars_required=0, retest_required=False, retest_observed=False)
    assert api._confirmed_trade_break_evidence(row, barrier) == proof
    for field, value in (("model", "connected_role_boundary_v99"),
                         ("zone_id", "other-zone"), ("lower", 99.0), ("upper", 102.0)):
        changed = deepcopy(row)
        changed_barrier = changed["nearest_barrier"]
        changed_barrier["reclaim_history"][field] = value
        changed_barrier["break_reclaim_evidence"]["reclaim_history"][field] = value
        assert api._confirmed_trade_break_evidence(changed, changed_barrier) is None
