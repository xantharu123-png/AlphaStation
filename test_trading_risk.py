"""Pure contracts for broker-evidenced paper portfolio risk.

The normalized input dictionaries mirror ``paper_autotrader`` snapshots:
positions carry account/con_id/signed quantity/avg_cost; orders carry broker
identity, parent/ref linkage and remaining quantity; intents carry the durable
setup identity and original plan; reservations carry the same identity while a
submission is not yet fully broker-visible.  No test relies on SQLite or IBKR
objects directly.
"""

from datetime import datetime, timezone

import pytest

from modules.trading_risk import (
    DEFAULT_RISK_POLICY,
    aggregate_stop_risk,
    consecutive_losses_today,
    derive_intent_outcome,
    evaluate_projected_risk,
    summarize_hypothetical_batch_risk,
)


def _intent(
    setup_id="A",
    *,
    account="DU1",
    con_id=101,
    direction="LONG",
    quantity=10,
    entry=100.0,
    stop=95.0,
    order_ref=None,
    parent_order_ids=(1001, 1002),
    group_key="TECH",
    group_verified=True,
):
    return {
        "setup_id": setup_id,
        "order_ref": order_ref or f"AS2-{setup_id}",
        "account": account,
        "con_id": con_id,
        "direction": direction,
        "quantity": quantity,
        "entry": entry,
        "stop": stop,
        "stop_limit": entry,
        "order_ids": [*parent_order_ids, *(value + 100 for value in parent_order_ids)],
        "parent_order_ids": list(parent_order_ids),
        "group_key": group_key,
        "group_verified": group_verified,
    }


def _position(*, account="DU1", con_id=101, quantity=10, avg_cost=100.0):
    return {
        "account": account,
        "con_id": con_id,
        "ticker": f"C{con_id}",
        "quantity": quantity,
        "avg_cost": avg_cost,
    }


def _order(
    order_id,
    order_ref,
    *,
    account="DU1",
    con_id=101,
    parent_id=0,
    action="SELL",
    order_type="STP",
    quantity=5,
    remaining=None,
    filled=0,
    stop_price=95.0,
    status="Submitted",
):
    return {
        "account": account,
        "con_id": con_id,
        "order_id": order_id,
        "parent_id": parent_id,
        "order_ref": order_ref,
        "action": action,
        "order_type": order_type,
        "quantity": quantity,
        "remaining": quantity if remaining is None else remaining,
        "filled": filled,
        "stop_price": stop_price,
        "status": status,
    }


def _stops(intent, *, stop_price=None, action=None, quantities=(5, 5)):
    exit_action = action or ("SELL" if intent["direction"] == "LONG" else "BUY")
    price = stop_price if stop_price is not None else intent["stop"]
    return [
        _order(
            parent_id,
            f"{intent['order_ref']}-S{index}",
            account=intent["account"],
            con_id=intent["con_id"],
            parent_id=parent_id,
            action=exit_action,
            quantity=quantity,
            stop_price=price,
        )
        for index, (parent_id, quantity) in enumerate(
            zip(intent["parent_order_ids"], quantities), start=1
        )
    ]


def _parent(intent, *, order_id=1001, index=1, remaining=6, quantity=10):
    return _order(
        order_id,
        f"{intent['order_ref']}-P{index}",
        account=intent["account"],
        con_id=intent["con_id"],
        parent_id=0,
        action="BUY" if intent["direction"] == "LONG" else "SELL",
        order_type="STP LMT",
        quantity=quantity,
        remaining=remaining,
        filled=quantity - remaining,
        stop_price=intent["entry"],
    )


def _reservation(intent, *, quantity=None, entry=None, stop=None):
    return {
        "reservation_id": f"R-{intent['setup_id']}",
        "setup_id": intent["setup_id"],
        "order_ref": intent["order_ref"],
        "account": intent["account"],
        "con_id": intent["con_id"],
        "direction": intent["direction"],
        "quantity": intent["quantity"] if quantity is None else quantity,
        "entry": intent["entry"] if entry is None else entry,
        "stop": intent["stop"] if stop is None else stop,
        "status": "SUBMITTING",
        "group_key": intent.get("group_key"),
        "group_verified": intent.get("group_verified"),
    }


def _fill(exec_id, side, shares, price, time_value, *, order_id, account="DU1", con_id=101):
    return {
        "exec_id": exec_id,
        "account": account,
        "con_id": con_id,
        "order_id": order_id,
        "side": side,
        "shares": shares,
        "price": price,
        "time": time_value,
    }


def _current(
    *,
    reliable=True,
    total=0.0,
    long=0.0,
    short=0.0,
    groups=None,
    net_liquidation=100_000.0,
):
    return {
        "reliable": reliable,
        "net_liquidation": net_liquidation,
        "total_risk_usd": total,
        "direction_risk_usd": {"LONG": long, "SHORT": short},
        "verified_group_risk_usd": dict(groups or {}),
        "unresolved_codes": [] if reliable else ["position_intent_missing"],
        "warnings": [],
    }


def _policy(**overrides):
    return {**DEFAULT_RISK_POLICY, **overrides}


def test_hypothetical_batch_risk_is_plan_r_only_and_groups_only_verified():
    rows = [
        {
            "ticker": "AAA",
            "direction": "LONG",
            "entry": 100,
            "stop": 95,
            "group_key": " tech ",
            "group_verified": True,
            # Account-like inputs must never enter this informational summary.
            "account": "DU123",
            "risk_usd": 5000,
        },
        {
            "ticker": "BBB",
            "trade_setup": {"direction": "SHORT", "entry": 50, "stop": 55},
            "group_key": "TECH",
            "group_verified": False,
        },
        {
            "ticker": "BAD-GEOMETRY",
            "direction": "LONG",
            "entry": 100,
            "stop": 105,
            "group_key": "ENERGY",
            "group_verified": True,
        },
        {
            "ticker": "NO-VERIFIED-DIRECTION",
            "entry": 10,
            "stop": 9,
        },
    ]

    result = summarize_hypothetical_batch_risk(rows)

    assert result == {
        "informational_only": True,
        "unit_r_per_plan": 1.0,
        "valid_plans": 2,
        "invalid_or_unverified_plans": 2,
        "total_hypothetical_r": 2.0,
        "by_direction_r": {"LONG": 1.0, "SHORT": 1.0},
        "by_verified_group_r": {"TECH": 1.0},
    }
    flattened = repr(result).lower()
    assert "usd" not in flattened
    assert "account" not in flattened
    assert "suppress" not in flattened


def test_hypothetical_batch_risk_tolerates_non_rows_and_nonfinite_levels():
    result = summarize_hypothetical_batch_risk(
        [None, "bad", {"direction": "LONG", "entry": float("nan"), "stop": 1}]
    )

    assert result["valid_plans"] == 0
    assert result["invalid_or_unverified_plans"] == 3
    assert result["total_hypothetical_r"] == 0.0
    assert result["by_direction_r"] == {}
    assert result["by_verified_group_r"] == {}


def test_aggregate_long_short_mirror_two_branches_and_verified_group():
    long_intent = _intent("LONG", con_id=101, direction="LONG", stop=95.0)
    short_intent = _intent(
        "SHORT",
        con_id=202,
        direction="SHORT",
        stop=105.0,
        parent_order_ids=(2001, 2002),
    )
    result = aggregate_stop_risk(
        100_000,
        [_position(con_id=101, quantity=10), _position(con_id=202, quantity=-10)],
        [*_stops(long_intent), *_stops(short_intent)],
        [long_intent, short_intent],
        [],
    )

    assert result["reliable"] is True
    assert result["unresolved_codes"] == []
    assert result["total_risk_usd"] == pytest.approx(100.0)
    assert result["total_risk_pct"] == pytest.approx(0.1)
    assert result["direction_risk_usd"] == {"LONG": 50.0, "SHORT": 50.0}
    assert result["verified_group_risk_usd"] == {"TECH": 100.0}
    assert [item["source"] for item in result["items"]] == ["position", "position"]


def test_partial_fill_position_plus_parent_remaining_and_reservation_are_not_double_counted():
    intent = _intent("PART", quantity=10, parent_order_ids=(1001,))
    parent = _parent(intent, remaining=6, quantity=10)
    stop = _stops(intent, quantities=(10,))[0]

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=4, avg_cost=101.0)],
        [parent, stop],
        [intent],
        [_reservation(intent)],
    )

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(54.0)
    assert [(item["source"], item["quantity"], item["risk_usd"]) for item in result["items"]] == [
        ("position", 4.0, 24.0),
        ("pending_parent", 6.0, 30.0),
    ]


@pytest.mark.parametrize("terminal_status", ["Cancelled", "ApiCancelled"])
def test_terminal_stop_cannot_protect_a_live_position(terminal_status):
    intent = _intent("CANCELLED-STOP", parent_order_ids=(1001,))
    cancelled_stop = _stops(intent, quantities=(10,))[0]
    cancelled_stop["status"] = terminal_status

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=10)],
        [cancelled_stop],
        [intent],
        [],
    )

    assert result["reliable"] is False
    assert "protective_stop_missing" in result["unresolved_codes"]
    assert result["items"] == []


def test_terminal_reservation_does_not_consume_risk():
    intent = _intent("RELEASED")
    reservation = _reservation(intent)
    reservation["status"] = "Released"

    result = aggregate_stop_risk(100_000, [], [], [intent], [reservation])

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(0.0)
    assert result["items"] == []


def test_overlapping_protective_stops_use_the_conservative_worst_price():
    intent = _intent("WORST", quantity=5)
    stops = _stops(intent, quantities=(5, 5))
    stops[0]["stop_price"] = 95.0
    stops[1]["stop_price"] = 90.0

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=5, avg_cost=100.0)],
        stops,
        [intent],
        [],
    )

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(50.0)


def test_unknown_stop_status_cannot_be_assumed_active():
    intent = _intent("UNKNOWN-STATUS", parent_order_ids=(1001,))
    unknown_stop = _stops(intent, quantities=(10,))[0]
    unknown_stop["status"] = ""

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=10)],
        [unknown_stop],
        [intent],
        [],
    )

    assert result["reliable"] is False
    assert "order_status_unknown" in result["unresolved_codes"]
    assert "protective_stop_missing" in result["unresolved_codes"]


def test_spoofed_stop_reference_cannot_protect_a_position():
    intent = _intent("EXACT-REF", parent_order_ids=(1001,))
    spoofed_stop = _stops(intent, quantities=(10,))[0]
    spoofed_stop["order_ref"] = f"{intent['order_ref']}-SPOOF"

    result = aggregate_stop_risk(
        100_000, [_position(quantity=10)], [spoofed_stop], [intent], []
    )

    assert result["reliable"] is False
    assert "protective_stop_missing" in result["unresolved_codes"]


def test_tightened_stop_at_or_beyond_cost_has_zero_remaining_downside():
    intent = _intent("BE", parent_order_ids=(1001,))
    tightened_stop = _stops(intent, stop_price=101.0, quantities=(10,))[0]

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=10, avg_cost=100.0)],
        [tightened_stop],
        [intent],
        [],
    )

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(0.0)
    assert result["items"][0]["risk_usd"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("intents", "orders", "expected"),
    [
        ([], [], "position_intent_missing"),
        (
            [_intent("A"), _intent("B", parent_order_ids=(2001, 2002))],
            [],
            "position_intent_ambiguous",
        ),
        ([_intent("A")], [], "protective_stop_missing"),
    ],
)
def test_unmatched_ambiguous_and_missing_stop_positions_fail_closed(intents, orders, expected):
    result = aggregate_stop_risk(100_000, [_position()], orders, intents, [])

    assert result["reliable"] is False
    assert expected in result["unresolved_codes"]
    assert result["total_risk_usd"] == 0.0


@pytest.mark.parametrize(
    ("orders", "expected"),
    [
        (lambda intent: _stops(intent, action="BUY"), "protective_stop_wrong_side"),
        (lambda intent: _stops(intent, quantities=(2, 3)), "protective_stop_undercovered"),
        (lambda intent: _stops(intent, stop_price=float("nan")), "protective_stop_invalid"),
    ],
)
def test_invalid_wrong_side_and_undercovered_position_stops_fail_closed(orders, expected):
    intent = _intent("A")
    result = aggregate_stop_risk(100_000, [_position()], orders(intent), [intent], [])

    assert result["reliable"] is False
    assert expected in result["unresolved_codes"]


def test_pending_parent_without_its_exact_stop_child_fails_closed():
    intent = _intent("PENDING", parent_order_ids=(1001,))
    parent = _parent(intent, remaining=10, quantity=10)
    wrong_child = _order(
        1101,
        f"{intent['order_ref']}-S2",
        parent_id=1001,
        quantity=10,
        account=intent["account"],
        con_id=intent["con_id"],
    )

    result = aggregate_stop_risk(100_000, [], [parent, wrong_child], [intent], [])

    assert result["reliable"] is False
    assert "pending_stop_missing" in result["unresolved_codes"]


def test_unknown_active_root_order_with_same_instrument_fails_closed():
    intent = _intent("KNOWN", parent_order_ids=(1001,))
    unknown_parent = _order(
        9999,
        "OTHER-P1",
        account=intent["account"],
        con_id=intent["con_id"],
        parent_id=0,
        action="BUY",
        order_type="STP LMT",
        quantity=10,
        remaining=10,
        stop_price=intent["entry"],
    )

    result = aggregate_stop_risk(100_000, [], [unknown_parent], [intent], [])

    assert result["reliable"] is False
    assert "pending_parent_identity_mismatch" in result["unresolved_codes"]


def test_active_root_order_with_invalid_remaining_quantity_fails_closed():
    intent = _intent("BAD-REMAINING", parent_order_ids=(1001,))
    unknown_parent = _parent(intent, order_id=9999, remaining=-1, quantity=10)
    unknown_parent["order_ref"] = "OTHER-P1"

    result = aggregate_stop_risk(100_000, [], [unknown_parent], [intent], [])

    assert result["reliable"] is False
    assert "pending_parent_invalid" in result["unresolved_codes"]


def test_stop_capacity_cannot_be_reused_for_position_and_pending_parent():
    intent = _intent("NO-REUSE", quantity=10, parent_order_ids=(1001,))
    parent = _parent(intent, remaining=6, quantity=10)
    only_six_stop_shares = _stops(intent, quantities=(6,))[0]

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=4)],
        [parent, only_six_stop_shares],
        [intent],
        [],
    )

    assert result["reliable"] is False
    assert any(
        code in result["unresolved_codes"]
        for code in {"protective_stop_undercovered", "pending_stop_undercovered"}
    )


def test_pending_branch_capacity_is_reserved_before_allocating_position_stops():
    intent = _intent(
        "BRANCH-CAPACITY",
        quantity=10,
        parent_order_ids=(1001, 1002),
    )
    pending_first_branch = _parent(
        intent, order_id=1001, index=1, remaining=5, quantity=5
    )
    stops = _stops(intent, quantities=(5, 5))

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=5)],
        [pending_first_branch, *stops],
        [intent],
        [],
    )

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(50.0)
    assert [(item["source"], item["quantity"]) for item in result["items"]] == [
        ("position", 5.0),
        ("pending_parent", 5.0),
    ]


def test_stop_capacity_cannot_be_reused_across_duplicate_position_rows():
    intent = _intent("DUP-POS", quantity=10, parent_order_ids=(1001,))
    one_stop = _stops(intent, quantities=(5,))[0]

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=5), _position(quantity=5)],
        [one_stop],
        [intent],
        [],
    )

    assert result["reliable"] is False
    assert "protective_stop_undercovered" in result["unresolved_codes"]


def test_filled_parent_without_position_or_terminal_outcome_fails_closed():
    intent = _intent("FILLED-GAP", parent_order_ids=(1001,))
    intent["status"] = "FILLED_NO_POSITION"
    filled_parent = _parent(intent, remaining=0, quantity=10)
    filled_parent["status"] = "Filled"
    stop = _stops(intent, quantities=(10,))[0]

    result = aggregate_stop_risk(
        100_000, [], [filled_parent, stop], [intent], []
    )

    assert result["reliable"] is False
    assert "filled_parent_position_unresolved" in result["unresolved_codes"]


def test_reservation_counts_without_broker_equivalent_and_dedupes_visible_parent():
    intent = _intent("RES", parent_order_ids=(1001,))
    reservation = _reservation(intent)
    reserved_only = aggregate_stop_risk(100_000, [], [], [intent], [reservation])

    parent = _parent(intent, remaining=10, quantity=10)
    stop = _stops(intent, quantities=(10,))[0]
    broker_visible = aggregate_stop_risk(
        100_000, [], [parent, stop], [intent], [reservation]
    )

    assert reserved_only["total_risk_usd"] == pytest.approx(50.0)
    assert [item["source"] for item in reserved_only["items"]] == ["reservation"]
    assert broker_visible["total_risk_usd"] == pytest.approx(50.0)
    assert [item["source"] for item in broker_visible["items"]] == ["pending_parent"]


def test_partial_broker_visibility_only_dedupes_matching_reservation_quantity():
    intent = _intent("PARTIAL-RES", quantity=10, parent_order_ids=(1001,))
    reservation = _reservation(intent)
    position_stop = _stops(intent, quantities=(4,))[0]

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=4, avg_cost=101.0)],
        [position_stop],
        [intent],
        [reservation],
    )

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(54.0)
    assert [(item["source"], item["quantity"], item["risk_usd"]) for item in result["items"]] == [
        ("position", 4.0, 24.0),
        ("reservation", 6.0, 30.0),
    ]


def test_committed_notional_dedupes_position_parent_and_reservation_by_contract():
    intent = _intent("NOTIONAL", quantity=10, parent_order_ids=(1001,))
    reservation = _reservation(intent)
    parent = _parent(intent, remaining=3, quantity=10)
    stop = _stops(intent, quantities=(10,))[0]

    result = aggregate_stop_risk(
        100_000,
        [_position(quantity=4, avg_cost=100.0)],
        [parent, stop],
        [intent],
        [reservation],
    )

    assert result["reliable"] is True
    assert [(item["source"], item["quantity"], item["notional_usd"]) for item in result["items"]] == [
        ("position", 4.0, 400.0),
        ("pending_parent", 3.0, 300.0),
        ("reservation", 3.0, 300.0),
    ]
    assert result["position_notional_usd"] == pytest.approx(400.0)
    assert result["non_position_committed_notional_usd"] == pytest.approx(600.0)
    assert result["committed_notional_usd"] == pytest.approx(1_000.0)
    assert result["committed_setup_count"] == 1
    assert result["committed_contract_count"] == 1
    assert result["committed_contracts"] == [
        {"account": "DU1", "con_id": "101", "setup_ids": ["NOTIONAL"]}
    ]


@pytest.mark.parametrize(
    ("order_type", "suffix"),
    [("STP", "S1"), ("LMT", "T1")],
)
def test_active_mapped_child_without_position_parent_or_reservation_fails_closed(
    order_type, suffix
):
    intent = _intent("ORPHAN-CHILD", parent_order_ids=(1001,))
    child = _order(
        1101,
        f"{intent['order_ref']}-{suffix}",
        parent_id=1001,
        order_type=order_type,
        quantity=10,
        remaining=10,
    )

    result = aggregate_stop_risk(100_000, [], [child], [intent], [])

    assert result["reliable"] is False
    assert "active_child_without_exposure" in result["unresolved_codes"]


@pytest.mark.parametrize("verified_value", [False, None, 1])
def test_unverified_group_warns_but_total_and_direction_risk_still_apply(verified_value):
    intent = _intent("UNGROUPED", group_key="TECH", group_verified=verified_value)
    result = aggregate_stop_risk(
        100_000,
        [_position()],
        _stops(intent),
        [intent],
        [],
    )

    assert result["reliable"] is True
    assert result["total_risk_usd"] == pytest.approx(50.0)
    assert result["direction_risk_usd"]["LONG"] == pytest.approx(50.0)
    assert result["verified_group_risk_usd"] == {}
    assert result["warnings"] == ["group_classification_unavailable"]


def test_risk_caps_allow_equality_and_block_only_strict_excess():
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    equal = evaluate_projected_risk(
        _current(),
        {"direction": "LONG", "risk_usd": 750.0},
        _policy(),
        [],
        now,
    )
    excess = evaluate_projected_risk(
        _current(),
        {"direction": "LONG", "risk_usd": 750.01},
        _policy(),
        [],
        now,
    )

    assert equal["allowed"] is True
    assert equal["reasons"] == []
    assert equal["projected_total_risk_pct"] == pytest.approx(0.75)
    assert excess["allowed"] is False
    assert excess["reasons"] == [
        "max_total_risk_exceeded",
        "max_direction_risk_exceeded",
    ]


def test_risk_cap_decimal_equality_ignores_float_representation_only():
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    policy = {
        **DEFAULT_RISK_POLICY,
        "max_total_risk_pct": 1.0,
        "max_direction_risk_pct": 1.0,
    }
    current = _current(net_liquidation=30.0)

    equal = evaluate_projected_risk(
        current,
        {"direction": "LONG", "risk_usd": 3 * 0.1, "group_verified": False},
        policy,
        [],
        now,
    )
    excess = evaluate_projected_risk(
        current,
        {"direction": "LONG", "risk_usd": 0.30001, "group_verified": False},
        policy,
        [],
        now,
    )

    assert equal["allowed"] is True
    assert excess["reasons"] == [
        "max_total_risk_exceeded",
        "max_direction_risk_exceeded",
    ]


def test_verified_group_cap_and_unverified_candidate_warning_are_independent():
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    current = _current(total=300, long=300, groups={"TECH": 300})
    at_cap = evaluate_projected_risk(
        current,
        {
            "direction": "LONG",
            "risk_usd": 200,
            "group_key": "TECH",
            "group_verified": True,
        },
        _policy(),
        [],
        now,
    )
    over_cap = evaluate_projected_risk(
        current,
        {
            "direction": "LONG",
            "risk_usd": 200.01,
            "group_key": "TECH",
            "group_verified": True,
        },
        _policy(),
        [],
        now,
    )
    unverified = evaluate_projected_risk(
        current,
        {
            "direction": "LONG",
            "risk_usd": 400,
            "group_key": "TECH",
            "group_verified": False,
        },
        _policy(),
        [],
        now,
    )

    assert at_cap["allowed"] is True
    assert over_cap["reasons"] == ["max_verified_group_risk_exceeded"]
    assert unverified["allowed"] is True
    assert "group_classification_unavailable" in unverified["warnings"]


def test_verified_group_cap_uses_a_canonical_case_insensitive_key():
    result = evaluate_projected_risk(
        _current(groups={" TECH ": 200.0, "tech": 200.0}),
        {
            "direction": "LONG",
            "risk_usd": 101.0,
            "group_key": "Tech",
            "group_verified": True,
        },
        _policy(),
        [],
        datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )

    assert result["allowed"] is False
    assert result["reasons"] == ["max_verified_group_risk_exceeded"]
    assert result["projected_verified_group_risk_usd"] == pytest.approx(501.0)


def test_inconsistent_total_and_direction_snapshot_fails_closed():
    result = evaluate_projected_risk(
        _current(total=0.0, long=500.0, short=0.0),
        {"direction": "SHORT", "risk_usd": 300.0},
        _policy(),
        [],
        datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["reasons"]


def test_corrupt_verified_group_snapshot_fails_closed():
    current = _current(total=400.0, long=400.0)
    current["verified_group_risk_usd"] = "corrupt"
    result = evaluate_projected_risk(
        current,
        {
            "direction": "LONG",
            "risk_usd": 101.0,
            "group_key": "TECH",
            "group_verified": True,
        },
        _policy(),
        [],
        datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["reasons"]


def test_missing_verified_group_key_is_reported_as_unavailable():
    intent = _intent("NO-GROUP", group_key="", group_verified=True)
    result = aggregate_stop_risk(100_000, [], [], [intent], [_reservation(intent)])

    assert result["reliable"] is True
    assert result["verified_group_risk_usd"] == {}
    assert result["warnings"] == ["group_classification_unavailable"]


def test_unresolved_current_risk_and_unreliable_loss_streak_fail_closed():
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    unresolved_outcome = {
        "setup_id": "BAD",
        "complete": False,
        "realized_r": None,
        "realized_at": "2026-08-21T10:00:00+00:00",
        "unresolved_codes": ["exit_quantity_incomplete"],
    }
    result = evaluate_projected_risk(
        _current(reliable=False),
        {"direction": "LONG", "risk_usd": 10},
        _policy(),
        [unresolved_outcome],
        now,
    )

    assert result["allowed"] is False
    assert result["reasons"] == ["risk_state_unresolved", "loss_streak_unresolved"]


def test_consecutive_loss_cap_blocks_after_three_complete_losses_today():
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    outcomes = [
        {
            "setup_id": str(index),
            "complete": True,
            "realized_r": -0.5,
            "realized_at": f"2026-08-21T0{index}:00:00+00:00",
            "outcome_evidence": "broker_fills",
            "unresolved_codes": [],
        }
        for index in range(1, 4)
    ]

    result = evaluate_projected_risk(
        _current(),
        {"direction": "SHORT", "risk_usd": 10},
        _policy(),
        outcomes,
        now,
    )

    assert result["allowed"] is False
    assert result["reasons"] == ["max_consecutive_losses_reached"]
    assert result["consecutive_losses_today"] == 3


@pytest.mark.parametrize(
    ("direction", "entry_side", "exit_side", "entry_prices", "exit_prices", "stop", "entry_vwap", "exit_vwap"),
    [
        ("LONG", "BOT", "SLD", (100.0, 102.0), (105.0, 106.0), 96.0, 101.2, 105.8),
        ("SHORT", "SELL", "BUY", (100.0, 98.0), (95.0, 94.0), 104.0, 98.8, 94.2),
    ],
)
def test_fill_pairing_long_short_partial_vwap_and_actual_closed_quantity(
    direction,
    entry_side,
    exit_side,
    entry_prices,
    exit_prices,
    stop,
    entry_vwap,
    exit_vwap,
):
    intent = _intent(
        direction,
        direction=direction,
        quantity=10,
        stop=stop,
        parent_order_ids=(10,),
    )
    intent["order_ids"] = [10, 11]
    fills = [
        _fill("E1", entry_side, 2, entry_prices[0], "2026-08-21T09:00:00Z", order_id=10),
        _fill("E2", entry_side, 3, entry_prices[1], "2026-08-21T09:01:00Z", order_id=10),
        _fill("X1", exit_side, 1, exit_prices[0], "2026-08-21T10:00:00Z", order_id=11),
        _fill("X2", exit_side, 4, exit_prices[1], "2026-08-21T10:01:00Z", order_id=11),
    ]

    outcome = derive_intent_outcome(
        intent,
        fills,
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert outcome["complete"] is True
    assert outcome["unresolved_codes"] == []
    assert outcome["entry_quantity"] == pytest.approx(5.0)
    assert outcome["exit_quantity"] == pytest.approx(5.0)
    assert outcome["entry_vwap"] == pytest.approx(entry_vwap)
    assert outcome["exit_vwap"] == pytest.approx(exit_vwap)
    assert outcome["realized_pnl_usd"] == pytest.approx(23.0)
    assert outcome["realized_r"] == pytest.approx(23.0 / 26.0)
    assert outcome["realized_at"] == "2026-08-21T10:01:00+00:00"
    assert outcome["outcome_evidence"] == "broker_fills"


@pytest.mark.parametrize(
    ("direction", "entry_side", "exit_side"),
    [("LONG", "BUY", "SELL"), ("SHORT", "SELL", "BUY")],
)
def test_fill_sequence_cannot_temporarily_overclose_then_reopen(
    direction, entry_side, exit_side
):
    intent = _intent(
        "FLIP",
        direction=direction,
        stop=95.0 if direction == "LONG" else 105.0,
        parent_order_ids=(10,),
    )
    intent["order_ids"] = [10, 11]
    fills = [
        _fill("E1", entry_side, 5, 100, "2026-08-21T09:00:00Z", order_id=10),
        _fill("X1", exit_side, 7, 101, "2026-08-21T09:01:00Z", order_id=11),
        _fill("E2", entry_side, 2, 100, "2026-08-21T09:02:00Z", order_id=10),
    ]

    outcome = derive_intent_outcome(
        intent,
        fills,
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert outcome["complete"] is False
    assert "fill_side_flip" in outcome["unresolved_codes"]


def test_equal_timestamp_mixed_fill_sides_are_ambiguous_without_broker_sequence():
    intent = _intent("SAME-TIME", parent_order_ids=(10,))
    intent["order_ids"] = [10, 11]
    entry = _fill("E", "BUY", 5, 100, "2026-08-21T09:00:00Z", order_id=10)
    exit_fill = _fill("X", "SELL", 5, 101, "2026-08-21T09:00:00Z", order_id=11)

    first = derive_intent_outcome(
        intent,
        [entry, exit_fill],
        broker_position_open=False,
        parent_orders_terminal=True,
    )
    reversed_input = derive_intent_outcome(
        intent,
        [exit_fill, entry],
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert first["complete"] is False
    assert reversed_input["complete"] is False
    assert "fill_sequence_ambiguous" in first["unresolved_codes"]
    assert first["unresolved_codes"] == reversed_input["unresolved_codes"]


def test_string_order_ids_do_not_authenticate_individual_characters():
    intent = _intent("BAD-IDS", parent_order_ids=(10,))
    intent["order_ids"] = "12"
    outcome = derive_intent_outcome(
        intent,
        [_fill("E", "BUY", 1, 100, "2026-08-21T09:00:00Z", order_id=1)],
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert outcome["complete"] is False
    assert "intent_identity_invalid" in outcome["unresolved_codes"]


@pytest.mark.parametrize(
    ("direction", "entry_side", "exit_side", "stop"),
    [
        ("LONG", "BUY", "SELL", 95.0),
        ("SHORT", "SELL", "BUY", 105.0),
    ],
)
def test_fill_sides_must_match_parent_and_child_order_roles(
    direction, entry_side, exit_side, stop
):
    intent = _intent(
        "ROLE", direction=direction, stop=stop, parent_order_ids=(10,)
    )
    intent["order_ids"] = [10, 11]
    outcome = derive_intent_outcome(
        intent,
        [
            _fill("E", entry_side, 5, 100, "2026-08-21T09:00:00Z", order_id=11),
            _fill("X", exit_side, 5, 101, "2026-08-21T10:00:00Z", order_id=10),
        ],
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert outcome["complete"] is False
    assert "fill_order_role_mismatch" in outcome["unresolved_codes"]


def test_rejected_duplicate_fill_still_advances_last_evidence_time():
    intent = _intent("DUP-TIME", parent_order_ids=(10,))
    intent["order_ids"] = [10, 11]
    outcome = derive_intent_outcome(
        intent,
        [
            _fill("E", "BUY", 5, 100, "2026-08-20T09:00:00Z", order_id=10),
            _fill("E", "BUY", 5, 101, "2026-08-21T10:00:00Z", order_id=10),
        ],
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert outcome["complete"] is False
    assert "fill_duplicate" in outcome["unresolved_codes"]
    assert outcome["last_evidence_at"] == "2026-08-21T10:00:00+00:00"


@pytest.mark.parametrize(
    ("fills", "broker_open", "parents_terminal", "expected"),
    [
        (
            [
                _fill("E", "BOT", 5, 100, "2026-08-21T09:00:00Z", order_id=10),
                _fill("X", "SLD", 4, 105, "2026-08-21T10:00:00Z", order_id=11),
            ],
            False,
            True,
            "exit_quantity_incomplete",
        ),
        (
            [
                _fill("E", "BOT", 5, 100, "2026-08-21T09:00:00Z", order_id=10),
                _fill("X", "SLD", 6, 105, "2026-08-21T10:00:00Z", order_id=11),
            ],
            False,
            True,
            "fill_exit_overage",
        ),
        (
            [
                _fill("X", "SLD", 5, 105, "2026-08-21T08:00:00Z", order_id=11),
                _fill("E", "BOT", 5, 100, "2026-08-21T09:00:00Z", order_id=10),
            ],
            False,
            True,
            "fill_side_flip",
        ),
        (
            [_fill("E", "UNKNOWN", 5, 100, "2026-08-21T09:00:00Z", order_id=10)],
            False,
            True,
            "fill_side_unknown",
        ),
        (
            [_fill("E", "BOT", 5, None, "2026-08-21T09:00:00Z", order_id=10)],
            False,
            True,
            "fill_value_invalid",
        ),
        (
            [
                _fill("E", "BOT", 5, 100, "2026-08-21T09:00:00Z", order_id=10),
                _fill("X", "SLD", 5, 105, "2026-08-21T10:00:00Z", order_id=11),
            ],
            True,
            True,
            "broker_position_still_open",
        ),
        (
            [
                _fill("E", "BOT", 5, 100, "2026-08-21T09:00:00Z", order_id=10),
                _fill("X", "SLD", 5, 105, "2026-08-21T10:00:00Z", order_id=11),
            ],
            False,
            False,
            "parent_orders_not_terminal",
        ),
    ],
)
def test_incomplete_or_invalid_fill_evidence_stays_unresolved(
    fills, broker_open, parents_terminal, expected
):
    intent = _intent("BAD", parent_order_ids=(10,))
    intent["order_ids"] = [10, 11]

    outcome = derive_intent_outcome(
        intent,
        fills,
        broker_position_open=broker_open,
        parent_orders_terminal=parents_terminal,
    )

    assert outcome["complete"] is False
    assert expected in outcome["unresolved_codes"]
    assert outcome["realized_r"] is None
    assert outcome["realized_at"] is None
    assert outcome["outcome_evidence"] is None


def test_consecutive_losses_use_only_same_utc_day_and_nonnegative_resets():
    now = datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc)
    outcomes = [
        {"setup_id": "OLD", "complete": True, "realized_r": -1, "realized_at": "2026-08-20T23:59:59Z", "outcome_evidence": "broker_fills", "unresolved_codes": []},
        {"setup_id": "D", "complete": True, "realized_r": -0.2, "realized_at": "2026-08-21T00:20:00Z", "outcome_evidence": "broker_fills", "unresolved_codes": []},
        {"setup_id": "B", "complete": True, "realized_r": -1, "realized_at": "2026-08-21T00:10:00Z", "outcome_evidence": "broker_fills", "unresolved_codes": []},
        {"setup_id": "C", "complete": True, "realized_r": 0, "realized_at": "2026-08-21T00:15:00Z", "outcome_evidence": "broker_fills", "unresolved_codes": []},
        {"setup_id": "A", "complete": True, "realized_r": -1, "realized_at": "2026-08-21T00:05:00Z", "outcome_evidence": "broker_fills", "unresolved_codes": []},
    ]

    result = consecutive_losses_today(outcomes, now)

    assert result == {
        "consecutive_losses": 1,
        "streak_reliable": True,
        "unresolved_codes": [],
        "utc_date": "2026-08-21",
    }


def test_unresolved_today_never_invents_a_win_or_loss_for_streak():
    result = consecutive_losses_today(
        [
            {
                "setup_id": "UNKNOWN",
                "complete": False,
                "realized_r": None,
                "realized_at": "2026-08-21T00:10:00Z",
                "unresolved_codes": ["exit_quantity_incomplete"],
            }
        ],
        datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )

    assert result["consecutive_losses"] == 0
    assert result["streak_reliable"] is False
    assert result["unresolved_codes"] == ["loss_outcome_unresolved"]


def test_derived_unresolved_outcome_uses_last_fill_time_for_today_reliability():
    intent = _intent("PARTIAL-OUTCOME", parent_order_ids=(10,))
    intent["order_ids"] = [10, 11]
    outcome = derive_intent_outcome(
        intent,
        [
            _fill("E", "BUY", 5, 100, "2026-08-21T09:00:00Z", order_id=10),
            _fill("X", "SELL", 4, 101, "2026-08-21T10:00:00Z", order_id=11),
        ],
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert outcome["complete"] is False
    assert outcome["realized_at"] is None
    assert outcome["last_evidence_at"] == "2026-08-21T10:00:00+00:00"
    streak = consecutive_losses_today(
        [outcome], datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    )
    assert streak["streak_reliable"] is False
    assert streak["unresolved_codes"] == ["loss_outcome_unresolved"]


def test_unresolved_outcome_without_any_evidence_time_fails_closed():
    result = consecutive_losses_today(
        [
            {
                "setup_id": "NO-TIME",
                "complete": False,
                "realized_r": None,
                "realized_at": None,
                "unresolved_codes": ["exit_quantity_incomplete"],
            }
        ],
        datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )

    assert result["streak_reliable"] is False
    assert result["unresolved_codes"] == ["loss_outcome_unresolved"]


def test_mixed_results_at_the_same_realized_time_make_streak_order_unresolved():
    outcomes = [
        {
            "setup_id": "LOSS",
            "complete": True,
            "realized_r": -1,
            "realized_at": "2026-08-21T10:00:00Z",
            "outcome_evidence": "broker_fills",
            "unresolved_codes": [],
        },
        {
            "setup_id": "RESET",
            "complete": True,
            "realized_r": 0,
            "realized_at": "2026-08-21T10:00:00Z",
            "outcome_evidence": "broker_fills",
            "unresolved_codes": [],
        },
    ]
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)

    first = consecutive_losses_today(outcomes, now)
    reversed_input = consecutive_losses_today(list(reversed(outcomes)), now)

    assert first == reversed_input
    assert first["streak_reliable"] is False
    assert first["unresolved_codes"] == ["loss_order_ambiguous"]


def test_future_dated_broker_outcome_fails_closed():
    result = consecutive_losses_today(
        [
            {
                "setup_id": "FUTURE",
                "complete": True,
                "realized_r": -1,
                "realized_at": "2026-08-21T13:00:00Z",
                "outcome_evidence": "broker_fills",
                "unresolved_codes": [],
            }
        ],
        datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )

    assert result["streak_reliable"] is False
    assert result["unresolved_codes"] == ["loss_outcome_unresolved"]
