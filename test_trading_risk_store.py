"""Contracts for the durable, broker-evidenced trading-risk store.

The store is intentionally separate from ``paper_autotrader``.  Its API is
small enough to run from a Windows ``spawn`` worker and persists only durable
risk evidence/coordination state; the risk arithmetic remains in
``modules.trading_risk``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import multiprocessing
import os
import queue
import sqlite3
from threading import Event, Thread
from typing import Any

import pytest

from modules import trading_risk_store as risk_store_module
from modules.trading_risk import DEFAULT_RISK_POLICY
from modules.trading_risk_store import TradingRiskStore as _TradingRiskStore


class TradingRiskStore(_TradingRiskStore):
    """Exercise the production store with an explicit complete broker snapshot."""

    def reserve_if_allowed(self, reservation, **kwargs):
        kwargs.setdefault("gross_position_value", 0.0)
        kwargs.setdefault("orders_snapshot_complete", True)
        kwargs.setdefault("available_funds", 100_000.0)
        kwargs.setdefault("min_available_funds", 0.0)
        return super().reserve_if_allowed(reservation, **kwargs)


_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _intent(
    setup_id: str = "A",
    *,
    account: str = "DU1",
    con_id: int = 101,
    parent_order_id: int = 1001,
    child_order_id: int = 1101,
    target_order_id: int | None = None,
    quantity: float = 10,
    entry: float = 100.0,
    stop: float = 95.0,
) -> dict[str, Any]:
    resolved_target_order_id = target_order_id or (child_order_id + 100)
    return {
        "setup_id": setup_id,
        "order_ref": f"AS2-{setup_id}",
        "account": account,
        "con_id": con_id,
        "direction": "LONG",
        "quantity": quantity,
        "entry": entry,
        "stop": stop,
        "parent_order_ids": [parent_order_id],
        "order_ids": [parent_order_id, child_order_id, resolved_target_order_id],
        "tp1": entry + abs(entry - stop) * 2,
        "tp2": entry + abs(entry - stop) * 3,
        "stop_limit": (
            entry + abs(entry - stop) * 0.1
            if entry > stop
            else entry - abs(entry - stop) * 0.1
        ),
        "allocations": [quantity],
        "group_key": "TECH",
        "group_verified": True,
    }


def _reservation(intent: dict[str, Any], reservation_id: str | None = None) -> dict[str, Any]:
    return {
        "reservation_id": reservation_id or f"reservation-{intent['setup_id']}",
        "setup_id": intent["setup_id"],
        "order_ref": intent["order_ref"],
        "account": intent["account"],
        "con_id": intent["con_id"],
        "direction": intent["direction"],
        "quantity": intent["quantity"],
        "entry": intent["entry"],
        "stop": intent["stop"],
        "status": "SUBMITTING",
        "group_key": intent["group_key"],
        "group_verified": intent["group_verified"],
    }


def _immutable_intent(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in intent.items()
        if key not in {"order_ids", "parent_order_ids"}
    }


def _fill(
    exec_id: str,
    side: str,
    *,
    order_id: int = 1001,
    price: float = 100.0,
    shares: float = 1.0,
    time_value: str = "2026-08-21T10:00:00Z",
    account: str = "DU1",
    con_id: int = 101,
    perm_id: int | None = None,
    client_id: int = 7,
) -> dict[str, Any]:
    return {
        "exec_id": exec_id,
        "account": account,
        "con_id": con_id,
        "order_id": order_id,
        "perm_id": order_id + 5000 if perm_id is None else perm_id,
        "client_id": client_id,
        "side": side,
        "shares": shares,
        "price": price,
        "time": time_value,
    }


def _order_mapping(
    intent: dict[str, Any],
    order_id: int,
    *,
    role: str,
    branch: int,
    parent_order_id: int = 0,
) -> dict[str, Any]:
    suffix = {"PARENT": "P", "STOP": "S", "TARGET": "T"}[role]
    direction = str(intent.get("direction") or "LONG").upper()
    entry_action = "BUY" if direction == "LONG" else "SELL"
    exit_action = "SELL" if direction == "LONG" else "BUY"
    order_type = {"PARENT": "STP LMT", "STOP": "STP", "TARGET": "LMT"}[role]
    aux_price = (
        float(intent["entry"])
        if role == "PARENT"
        else float(intent["stop"])
        if role == "STOP"
        else None
    )
    limit_price = (
        float(intent.get("stop_limit") or intent["entry"])
        if role == "PARENT"
        else float(intent.get("tp1") or (float(intent["entry"]) + 10.0))
        if role == "TARGET"
        else None
    )
    return {
        "account": intent["account"],
        "con_id": intent["con_id"],
        "order_id": order_id,
        "perm_id": order_id + 5000,
        "order_ref": f"{intent['order_ref']}-{suffix}{branch}",
        "role": role,
        "branch": branch,
        "parent_order_id": parent_order_id,
        "action": entry_action if role == "PARENT" else exit_action,
        "order_type": order_type,
        "quantity": float(intent["quantity"]),
        "aux_price": aux_price,
        "limit_price": limit_price,
        "oca_group": "" if role == "PARENT" else f"{intent['order_ref']}-O{branch}",
        "oca_type": 0 if role == "PARENT" else 1,
        "tif": "DAY" if role == "PARENT" else "GTC",
        "transmit": role == "TARGET",
        "outside_rth": False,
        "client_id": 7,
    }


def _observed_order(mapping: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    observed = {
        "account": mapping["account"],
        "con_id": mapping["con_id"],
        "order_id": mapping["order_id"],
        "perm_id": mapping["perm_id"],
        "parent_id": mapping["parent_order_id"],
        "order_ref": mapping["order_ref"],
        "action": mapping["action"],
        "order_type": mapping["order_type"],
        "quantity": mapping["quantity"],
        "aux_price": mapping["aux_price"],
        "stop_price": mapping["aux_price"],
        "limit_price": mapping["limit_price"],
        "oca_group": mapping["oca_group"],
        "oca_type": mapping["oca_type"],
        "tif": mapping["tif"],
        "transmit": mapping["transmit"],
        "outside_rth": mapping["outside_rth"],
        "client_id": mapping["client_id"],
        "status": "Submitted",
        "filled": 0.0,
        "remaining": mapping["quantity"],
        "avg_fill_price": None,
    }
    observed.update(overrides)
    return observed


def _register_intent_with_orders(store: Any, intent: dict[str, Any]) -> None:
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    parent_id, child_id, _target_id = intent["order_ids"]
    assert store.register_intent_order(
        intent["setup_id"],
        _order_mapping(intent, parent_id, role="PARENT", branch=1),
    )["accepted"] is True
    assert store.register_intent_order(
        intent["setup_id"],
        _order_mapping(
            intent,
            child_id,
            role="STOP",
            branch=1,
            parent_order_id=parent_id,
        ),
    )["accepted"] is True


def _full_broker_order_evidence(
    store: Any, intent: dict[str, Any]
) -> list[dict[str, Any]]:
    parent_id, stop_id, target_id = intent["order_ids"]
    mappings = [
        _order_mapping(intent, parent_id, role="PARENT", branch=1),
        _order_mapping(
            intent, stop_id, role="STOP", branch=1,
            parent_order_id=parent_id,
        ),
        _order_mapping(
            intent, target_id, role="TARGET", branch=1,
            parent_order_id=parent_id,
        ),
    ]
    target_registered = store.register_intent_order(
        intent["setup_id"], mappings[2]
    )
    assert target_registered.get("accepted") is True
    return [_observed_order(mapping) for mapping in mappings]


def _mark_broker_visible(
    store: Any,
    intent: dict[str, Any],
    *,
    lease_key: str,
    owner_token: str,
    fence_token: int,
    now: Any = _NOW,
) -> dict[str, Any]:
    evidence = _full_broker_order_evidence(store, intent)
    return store.mark_reservation_broker_visible(
        f"reservation-{intent['setup_id']}",
        list(intent["order_ids"]),
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=fence_token,
        now=now,
        broker_order_evidence=evidence,
    )


def _reserve_in_spawn_worker(
    db_path: str,
    setup_id: str,
    reservation: dict[str, Any],
    barrier: Any,
    results: Any,
    max_total_exposure_pct: float = 20.0,
    max_positions: int = 3,
    available_funds: float = 100_000.0,
    min_available_funds: float = 0.0,
) -> None:
    """Top-level target required by Windows multiprocessing spawn."""
    try:
        store = TradingRiskStore(db_path)
        store.initialize()
        lease = store.acquire_lease(
            f"submit:{setup_id}",
            setup_id,
            now=_NOW,
            ttl_seconds=30,
        )
        assert lease["acquired"] is True
        barrier.wait(timeout=15)
        decision = store.reserve_if_allowed(
            reservation,
            net_liquidation=100_000.0,
            positions=[],
            orders=[],
            policy=DEFAULT_RISK_POLICY,
            gross_position_value=0.0,
            max_total_exposure_pct=max_total_exposure_pct,
            max_positions=max_positions,
            available_funds=available_funds,
            min_available_funds=min_available_funds,
            now=_NOW,
            lease_key=f"submit:{setup_id}",
            owner_token=setup_id,
            fence_token=lease["fence_token"],
        )
        results.put({"setup_id": setup_id, "decision": decision})
    except BaseException as exc:  # pragma: no cover - assertion is in parent
        results.put({"setup_id": setup_id, "worker_error": repr(exc)})


def _crash_during_execution_write_worker(
    db_path: str,
    generation: int,
    write_started: Any,
) -> None:
    """Leave a real durable claim behind by terminating inside broker I/O."""
    store = TradingRiskStore(db_path)

    def crash_after_claim() -> None:
        write_started.set()
        os._exit(23)

    store.run_if_execution_generation(
        generation,
        crash_after_claim,
        claim_context={
            "operation_kind": "PLACE_ORDER",
            "account": "DU1",
            "setup_id": "CRASH-CLAIM",
            "order_id": 991,
            "order_ref": "AS2-CRASH-CLAIM-P1",
        },
    )


def test_initialize_reopen_and_intent_order_mapping_are_idempotent_and_immutable(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    intent = _intent()
    store = TradingRiskStore(db_path)

    store.initialize()
    store.initialize()
    immutable_intent = _immutable_intent(intent)
    first = store.register_intent(immutable_intent)
    duplicate = store.register_intent(dict(immutable_intent))

    assert first == {"accepted": True, "idempotent": False, "conflict": None}
    assert duplicate == {"accepted": True, "idempotent": True, "conflict": None}
    first_parent = store.register_intent_order(
        "A", _order_mapping(intent, 1001, role="PARENT", branch=1)
    )
    duplicate_parent = store.register_intent_order(
        "A", _order_mapping(intent, 1001, role="PARENT", branch=1)
    )
    first_stop = store.register_intent_order(
        "A",
        _order_mapping(
            intent, 1101, role="STOP", branch=1, parent_order_id=1001
        ),
    )
    assert first_parent == {"accepted": True, "idempotent": False, "conflict": None}
    assert duplicate_parent == {"accepted": True, "idempotent": True, "conflict": None}
    assert first_stop == {"accepted": True, "idempotent": False, "conflict": None}

    reopened = TradingRiskStore(db_path)
    reopened.initialize()
    assert reopened.load_intent("A") == immutable_intent
    assert reopened.intent_order_ids("A") == [1001, 1101]

    changed_intent = dict(immutable_intent, stop=94.0)
    changed_mapping = reopened.register_intent(changed_intent)
    second_intent = _intent("B", parent_order_id=2001, child_order_id=1101)
    assert reopened.register_intent(_immutable_intent(second_intent))["accepted"] is True
    conflicting_mapping = reopened.register_intent_order(
        "B",
        _order_mapping(
            second_intent,
            1101,
            role="STOP",
            branch=1,
            parent_order_id=2001,
        ),
    )

    assert changed_mapping == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_immutable_conflict",
    }
    assert conflicting_mapping == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_order_mapping_conflict",
    }
    assert reopened.load_intent("A") == immutable_intent
    assert reopened.intent_order_ids("A") == [1001, 1101]


def test_order_mapping_requires_exact_intent_ref_role_branch_and_parent(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("LINK")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True

    wrong_parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    wrong_parent["order_ref"] = "AS2-WRONG-P1"
    wrong_parent["parent_order_id"] = 999
    rejected_parent = store.register_intent_order("LINK", wrong_parent)
    assert rejected_parent["accepted"] is False
    assert rejected_parent["conflict"] == "intent_order_mapping_invalid"

    assert store.register_intent_order(
        "LINK", _order_mapping(intent, 1001, role="PARENT", branch=1)
    )["accepted"] is True
    wrong_child = _order_mapping(
        intent, 1101, role="STOP", branch=1, parent_order_id=999
    )
    rejected_child = store.register_intent_order("LINK", wrong_child)
    assert rejected_child["accepted"] is False
    assert rejected_child["conflict"] == "intent_order_mapping_invalid"


def test_order_identity_conflict_is_durable_and_blocks_future_admission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent("MAP-A", parent_order_id=3001, child_order_id=3101)
    second = _intent("MAP-B", parent_order_id=4001, child_order_id=4101)
    _register_intent_with_orders(store, first)
    assert store.register_intent(_immutable_intent(second))["accepted"] is True

    collision = _order_mapping(second, 3001, role="PARENT", branch=1)
    collision["order_ref"] = f"{second['order_ref']}-P1"
    result = store.register_intent_order("MAP-B", collision)
    assert result["conflict"] == "intent_order_mapping_conflict"

    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "MAP-NEXT",
        parent_order_id=5001,
        child_order_id=5101,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    lease = reopened.acquire_lease(
        "submit:MAP-NEXT", "worker", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:MAP-NEXT",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_intent_natural_identity_conflict_is_durable_and_blocks_admission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent("IDENTITY-A", parent_order_id=3301, child_order_id=3401)
    _register_intent_with_orders(store, first)
    collision = _intent("IDENTITY-B", parent_order_id=3501, child_order_id=3601)
    collision["order_ref"] = first["order_ref"]

    result = store.register_intent(_immutable_intent(collision))

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_identity_conflict",
    }
    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "AFTER-IDENTITY-CONFLICT",
        parent_order_id=3701,
        child_order_id=3801,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    lease = reopened.acquire_lease(
        "submit:AFTER-IDENTITY-CONFLICT", "worker", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-IDENTITY-CONFLICT",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_unmapped_fill_is_persisted_then_attaches_after_order_mapping(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    early_fill = store.append_fill(_fill("EARLY", "BUY"))

    assert early_fill["accepted"] is True
    assert early_fill["persisted"] is True
    assert early_fill["mapping_pending"] is True

    candidate = _intent("CANDIDATE", parent_order_id=2001, child_order_id=2101)
    _register_intent_with_orders(store, candidate)
    lease = store.acquire_lease(
        "submit:CANDIDATE", "worker", now=_NOW, ttl_seconds=30
    )
    blocked = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:CANDIDATE",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert blocked["allowed"] is False
    assert "risk_state_unresolved" in blocked["risk"]["reasons"]

    intent = _intent("LATE")
    _register_intent_with_orders(store, intent)
    evidence = TradingRiskStore(db_path).fill_evidence("LATE")

    assert evidence["reliable"] is True
    assert [fill["exec_id"] for fill in evidence["fills"]] == ["EARLY"]
    admitted = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:CANDIDATE",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admitted["allowed"] is True


def test_malformed_fill_is_durably_remembered_and_blocks_admission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    malformed = _fill("BAD-FILL", "BUY")
    malformed.pop("con_id")

    result = store.append_fill(malformed)
    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "fill_invalid",
        "persisted": True,
    }

    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "AFTER-BAD-FILL",
        parent_order_id=6001,
        child_order_id=6101,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    lease = reopened.acquire_lease(
        "submit:AFTER-BAD-FILL", "worker", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-BAD-FILL",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_fills_canonicalize_aliases_are_idempotent_and_persist_exec_conflicts_fail_closed(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    store.initialize()
    intent = _intent()
    _register_intent_with_orders(store, intent)

    entry = store.append_fill(_fill("E1", "BOT"))
    entry_alias = store.append_fill(_fill("E1", "BUY"))
    exit_fill = store.append_fill(_fill("X1", "SLD", order_id=1101, price=105.0))
    exit_alias = store.append_fill(_fill("X1", "SELL", order_id=1101, price=105.0))
    conflict = store.append_fill(_fill("E1", "BUY", price=101.0))

    assert entry == {"accepted": True, "idempotent": False, "conflict": None, "persisted": True}
    assert entry_alias == {"accepted": True, "idempotent": True, "conflict": None, "persisted": False}
    assert exit_fill == {"accepted": True, "idempotent": False, "conflict": None, "persisted": True}
    assert exit_alias == {"accepted": True, "idempotent": True, "conflict": None, "persisted": False}
    assert conflict == {
        "accepted": False,
        "idempotent": False,
        "conflict": "exec_id_payload_conflict",
        "persisted": True,
    }

    reopened = TradingRiskStore(db_path)
    evidence = reopened.fill_evidence("A")

    assert [fill["side"] for fill in evidence["fills"]] == ["BOT", "SLD"]
    assert evidence["reliable"] is False
    assert evidence["unresolved_codes"] == ["fill_exec_conflict"]
    assert evidence["conflicting_events"] == [
        {
            "exec_id": "E1",
            "side": "BOT",
            "shares": 1.0,
            "price": 101.0,
            "time": "2026-08-21T10:00:00+00:00",
        }
    ]


def test_legacy_fill_ledger_migrates_to_explicit_sequence(tmp_path):
    db_path = tmp_path / "legacy-risk.sqlite"
    payloads = [
        (
            40,
            "LEGACY-ENTRY",
            '{"account":"DU1","con_id":101,"exec_id":"LEGACY-ENTRY",'
            '"order_id":1001,"perm_id":0,"price":100.0,"shares":1.0,'
            '"side":"BOT","time":"2026-08-21T10:00:00+00:00"}',
        ),
        (
            90,
            "LEGACY-EXIT",
            '{"account":"DU1","con_id":101,"exec_id":"LEGACY-EXIT",'
            '"order_id":1101,"perm_id":0,"price":105.0,"shares":1.0,'
            '"side":"SLD","time":"2026-08-21T10:01:00+00:00"}',
        ),
    ]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE trading_risk_fill_events (
                exec_id TEXT PRIMARY KEY,
                setup_id TEXT,
                account TEXT NOT NULL,
                con_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for rowid, exec_id, payload_json in payloads:
            connection.execute(
                """
                INSERT INTO trading_risk_fill_events
                    (rowid, exec_id, setup_id, account, con_id, order_id,
                     payload_json, payload_hash, created_at)
                VALUES (?, ?, 'LEGACY', 'DU1', '101', ?, ?, ?, ?)
                """,
                (
                    rowid,
                    exec_id,
                    "1001" if exec_id.endswith("ENTRY") else "1101",
                    payload_json,
                    f"hash-{exec_id}",
                    "2026-08-21T10:00:00+00:00",
                ),
            )

    store = TradingRiskStore(db_path)
    evidence = store.fill_evidence("LEGACY")

    assert [fill["exec_id"] for fill in evidence["fills"]] == [
        "LEGACY-ENTRY",
        "LEGACY-EXIT",
    ]
    assert [fill["ledger_sequence"] for fill in evidence["fills"]] == [1, 2]
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(trading_risk_fill_events)"
            )
        }
    assert "ledger_sequence" in columns
    assert TradingRiskStore(db_path).fill_evidence("LEGACY")["fills"] == evidence["fills"]


def test_fill_ledger_sequence_survives_rowid_rewrite_and_is_immutable(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("SEQUENCE")
    _register_intent_with_orders(store, intent)
    store.append_fill(_fill("SEQ-ENTRY", "BUY", order_id=1001, price=100.0))
    store.append_fill(_fill("SEQ-EXIT", "SELL", order_id=1101, price=105.0))
    before = store.fill_evidence("SEQUENCE")["fills"]
    assert [fill["ledger_sequence"] for fill in before] == [1, 2]

    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE trading_risk_fill_events SET rowid=100 WHERE exec_id='SEQ-ENTRY'"
        )
        connection.commit()
        connection.execute("VACUUM")

    after = TradingRiskStore(store.db_path).fill_evidence("SEQUENCE")["fills"]
    assert [fill["exec_id"] for fill in after] == ["SEQ-ENTRY", "SEQ-EXIT"]
    assert [fill["ledger_sequence"] for fill in after] == [1, 2]
    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="ledger_sequence_immutable"):
            connection.execute(
                """
                UPDATE trading_risk_fill_events SET ledger_sequence=99
                WHERE exec_id='SEQ-ENTRY'
                """
            )


def test_fill_set_hash_binds_immutable_ledger_order(tmp_path):
    first = TradingRiskStore(tmp_path / "first.sqlite")
    second = TradingRiskStore(tmp_path / "second.sqlite")
    for store in (first, second):
        _register_intent_with_orders(store, _intent("HASH-ORDER"))
    entry = _fill("HASH-ENTRY", "BUY", order_id=1001, price=100.0)
    exit_fill = _fill("HASH-EXIT", "SELL", order_id=1101, price=105.0)
    first.append_fill(entry)
    first.append_fill(exit_fill)
    second.append_fill(exit_fill)
    second.append_fill(entry)

    assert (
        first.fill_evidence("HASH-ORDER")["fill_set_hash"]
        != second.fill_evidence("HASH-ORDER")["fill_set_hash"]
    )


def test_global_exec_conflict_poisoning_is_visible_to_primary_and_incoming_intents(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent("EXEC-A", parent_order_id=3001, child_order_id=3101)
    second = _intent("EXEC-B", parent_order_id=4001, child_order_id=4101)
    _register_intent_with_orders(store, first)
    _register_intent_with_orders(store, second)

    assert store.append_fill(
        _fill("GLOBAL-X", "BUY", order_id=3001, price=100.0)
    )["accepted"] is True
    conflict = store.append_fill(
        _fill("GLOBAL-X", "BUY", order_id=4001, price=101.0)
    )

    assert conflict["conflict"] == "exec_id_payload_conflict"
    assert store.fill_evidence("EXEC-A")["reliable"] is False
    assert store.fill_evidence("EXEC-B")["reliable"] is False
    lease = store.acquire_lease(
        "submit:EXEC-B", "worker", now=_NOW, ttl_seconds=30
    )
    admission = store.reserve_if_allowed(
        _reservation(second),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:EXEC-B",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_outcome_can_move_from_unresolved_to_complete_then_is_immutable(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    store.initialize()
    intent = _intent()
    _register_intent_with_orders(store, intent)
    outcome_lease = store.acquire_lease(
        "submit:A", "outcome-worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:A",
        owner_token="outcome-worker",
        fence_token=outcome_lease["fence_token"],
    )["allowed"] is True
    unresolved = {
        "setup_id": "A",
        "complete": False,
        "realized_r": None,
        "realized_at": None,
        "outcome_evidence": None,
        "unresolved_codes": ["exit_quantity_incomplete"],
    }
    updated_unresolved = {
        **unresolved,
        "entry_quantity": 2.0,
        "last_evidence_at": "2026-08-21T10:04:00+00:00",
    }
    complete = {
        "setup_id": "A",
        "complete": True,
        "realized_r": 1.0,
        "realized_at": "2026-08-21T10:00:00+00:00",
        "outcome_evidence": "broker_fills",
        "unresolved_codes": [],
    }

    assert store.record_outcome(unresolved, now=_NOW) == {
        "accepted": True,
        "idempotent": False,
        "conflict": None,
        "transition": "stored_unresolved",
    }
    assert store.record_outcome(updated_unresolved, now=_NOW) == {
        "accepted": True,
        "idempotent": False,
        "conflict": None,
        "transition": "updated_unresolved",
    }
    assert store.append_fill(
        _fill("OUT-E", "BUY", order_id=1001, price=100.0)
    )["accepted"] is True
    assert store.append_fill(
        _fill("OUT-X", "SELL", order_id=1101, price=105.0)
    )["accepted"] is True
    assert _mark_broker_visible(
        store,
        intent,
        lease_key="submit:A",
        owner_token="outcome-worker",
        fence_token=outcome_lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    complete["fill_set_hash"] = store.fill_evidence("A")["fill_set_hash"]
    terminal_kwargs = {
        "reservation_id": "reservation-A",
        "lease_key": "submit:A",
        "owner_token": "outcome-worker",
        "fence_token": outcome_lease["fence_token"],
        "now": _NOW,
        "terminal_evidence": {
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    }
    assert store.record_outcome(
        complete,
        **terminal_kwargs,
    ) == {
        "accepted": True,
        "idempotent": False,
        "conflict": None,
        "transition": "completed",
    }
    assert store.active_reservations() == []
    assert store.record_outcome(
        dict(complete),
        **terminal_kwargs,
    ) == {
        "accepted": True,
        "idempotent": True,
        "conflict": None,
        "transition": "idempotent",
    }
    conflicting_complete = dict(
        complete,
        realized_r=-999.0,
        realized_at="2099-01-01T00:00:00+00:00",
    )
    assert store.record_outcome(
        conflicting_complete,
        **terminal_kwargs,
    ) == {
        "accepted": False,
        "idempotent": False,
        "conflict": "outcome_derived_mismatch",
        "transition": "rejected",
    }
    persisted = TradingRiskStore(store.db_path).load_outcome("A")
    assert persisted is not None
    assert persisted["realized_r"] == pytest.approx(1.0)
    assert persisted["realized_at"] == "2026-08-21T10:00:00+00:00"
    assert persisted["fill_set_hash"] == complete["fill_set_hash"]

    candidate = _intent(
        "AFTER-CONFLICT",
        parent_order_id=2001,
        child_order_id=2101,
        quantity=0.1,
    )
    _register_intent_with_orders(store, candidate)
    lease = store.acquire_lease(
        "submit:AFTER-CONFLICT", "worker", now=_NOW, ttl_seconds=30
    )
    admission = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-CONFLICT",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert admission["allowed"] is False
    assert admission["decision"] == "risk_blocked"
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def _prepared_terminal_outcome(
    store: TradingRiskStore, setup_id: str, *, target_order_id: int | None = None
):
    intent = _intent(setup_id, target_order_id=target_order_id)
    _register_intent_with_orders(store, intent)
    lease_key = f"submit:{setup_id}"
    lease = store.acquire_lease(lease_key, "outcome-worker", now=_NOW, ttl_seconds=30)
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        intent,
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    assert store.append_fill(
        _fill(f"{setup_id}-ENTRY", "BUY", order_id=1001, price=100.0)
    )["accepted"] is True
    assert store.append_fill(
        _fill(f"{setup_id}-EXIT", "SELL", order_id=1101, price=105.0)
    )["accepted"] is True
    outcome = {
        "setup_id": setup_id,
        "complete": True,
        "realized_r": 1.0,
        "realized_at": "2026-08-21T10:00:00+00:00",
        "outcome_evidence": "broker_fills",
        "unresolved_codes": [],
        "fill_set_hash": store.fill_evidence(setup_id)["fill_set_hash"],
    }
    return intent, lease_key, lease, outcome


def _prepared_released_setup(
    store: TradingRiskStore,
    setup_id: str,
    *,
    parent_order_id: int = 1001,
    child_order_id: int = 1101,
    target_order_id: int | None = None,
):
    intent = _intent(
        setup_id,
        parent_order_id=parent_order_id,
        child_order_id=child_order_id,
        target_order_id=target_order_id,
    )
    _register_intent_with_orders(store, intent)
    if target_order_id is not None:
        assert store.register_intent_order(
            intent["setup_id"],
            _order_mapping(
                intent,
                intent["order_ids"][2],
                role="TARGET",
                branch=1,
                parent_order_id=parent_order_id,
            ),
        )["accepted"] is True
    lease_key = f"submit:{setup_id}"
    lease = store.acquire_lease(lease_key, "release-worker", now=_NOW, ttl_seconds=30)
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key=lease_key,
        owner_token="release-worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True
    assert store.mark_reservation_reconcile_required(
        f"reservation-{setup_id}",
        lease_key=lease_key,
        owner_token="release-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="submission_interrupted",
    )["updated"] is True
    assert store.release_reservation(
        f"reservation-{setup_id}",
        lease_key=lease_key,
        owner_token="release-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="negative full snapshot",
        broker_absence_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": intent["account"],
            "con_id": intent["con_id"],
            "position_open": False,
            "open_order_ids": [],
            "fill_order_ids": [],
        },
    )["updated"] is True
    return intent


def test_unfenced_terminal_booleans_cannot_complete_reservation(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    _, _, _, outcome = _prepared_terminal_outcome(store, "UNFENCED-COMPLETE")

    result = store.record_outcome(
        outcome,
        broker_position_open=False,
        parent_orders_terminal=True,
    )

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "outcome_terminal_evidence_required",
        "transition": "rejected",
    }
    assert store.load_outcome("UNFENCED-COMPLETE") is None
    assert store.active_reservations()[0]["status"] == "BROKER_VISIBLE"


def test_fenced_terminal_evidence_is_persisted_before_reservation_completes(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    _, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "FENCED-COMPLETE"
    )

    result = store.record_outcome(
        outcome,
        reservation_id="reservation-FENCED-COMPLETE",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T14:00:00+02:00",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )

    assert result["accepted"] is True
    assert result["transition"] == "completed"
    reopened = TradingRiskStore(store.db_path)
    persisted = reopened.load_outcome("FENCED-COMPLETE")
    assert persisted is not None
    assert persisted["terminal_evidence"] == "broker_snapshot"
    assert persisted["terminal_evidence_hash"]
    assert reopened.active_reservations() == []
    with sqlite3.connect(store.db_path) as connection:
        evidence = connection.execute(
            """
            SELECT reservation_id, lease_key, fence_token, evidence_json
            FROM trading_risk_terminal_evidence WHERE setup_id=?
            """,
            ("FENCED-COMPLETE",),
        ).fetchone()
    assert evidence is not None
    assert evidence[:3] == (
        "reservation-FENCED-COMPLETE",
        "submit:FENCED-COMPLETE",
        1,
    )


def test_terminal_evidence_rejects_any_still_open_mapped_child(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    _, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "OPEN-CHILD"
    )

    result = store.record_outcome(
        outcome,
        reservation_id="reservation-OPEN-CHILD",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [1101],
        },
    )

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "outcome_terminal_evidence_invalid",
        "transition": "rejected",
    }
    assert store.load_outcome("OPEN-CHILD") is None
    assert store.active_reservations()[0]["status"] == "BROKER_VISIBLE"


@pytest.mark.parametrize(
    "evidence_gap",
    ["setup_conflict", "rejected_same_account_fill", "unmapped_same_account_fill"],
)
def test_complete_outcome_requires_conflict_free_complete_fill_evidence(
    tmp_path, evidence_gap
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "INCOMPLETE-FILL-EVIDENCE"
    )
    if evidence_gap == "setup_conflict":
        conflicting_mapping = _order_mapping(
            intent,
            9991,
            role="PARENT",
            branch=1,
        )
        assert store.register_intent_order(
            intent["setup_id"], conflicting_mapping
        )["conflict"] == "intent_order_mapping_conflict"
    elif evidence_gap == "rejected_same_account_fill":
        malformed = _fill("REJECTED-EVIDENCE", "BUY", order_id=9992)
        malformed["shares"] = 0
        assert store.append_fill(malformed)["conflict"] == "fill_invalid"
    else:
        pending = store.append_fill(
            _fill("UNMAPPED-EVIDENCE", "BUY", order_id=9993)
        )
        assert pending["mapping_pending"] is True

    result = store.record_outcome(
        outcome,
        reservation_id="reservation-INCOMPLETE-FILL-EVIDENCE",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "outcome_fill_evidence_invalid",
        "transition": "rejected",
    }
    assert store.load_outcome("INCOMPLETE-FILL-EVIDENCE") is None
    assert store.active_reservations()[0]["status"] == "BROKER_VISIBLE"
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM trading_risk_terminal_evidence
            WHERE setup_id='INCOMPLETE-FILL-EVIDENCE'
            """
        ).fetchone()[0] == 0


def test_complete_outcome_cannot_move_outcome_time_backwards_or_persist_evidence(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    _, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "OUTCOME-TIME"
    )
    assert store.record_outcome(
        {
            "setup_id": "OUTCOME-TIME",
            "complete": False,
            "realized_r": None,
            "realized_at": None,
            "outcome_evidence": None,
            "unresolved_codes": ["broker_state_unresolved"],
        }
    )["accepted"] is True
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            UPDATE trading_risk_outcomes
            SET updated_at='2026-08-21T12:00:00.000001+00:00'
            WHERE setup_id='OUTCOME-TIME'
            """
        )

    result = store.record_outcome(
        outcome,
        reservation_id="reservation-OUTCOME-TIME",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "outcome_time_regression",
        "transition": "rejected",
    }
    assert store.load_outcome("OUTCOME-TIME")["complete"] is False
    assert store.active_reservations()[0]["status"] == "BROKER_VISIBLE"
    with sqlite3.connect(store.db_path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM trading_risk_terminal_evidence
            WHERE setup_id='OUTCOME-TIME'
            """
        ).fetchone()[0]
    assert count == 0


def test_new_mapped_fill_after_complete_outcome_durably_blocks_admission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed = _intent("CLOSED", parent_order_id=3001, child_order_id=3101, quantity=1)
    _register_intent_with_orders(store, completed)
    completion_lease = store.acquire_lease(
        "submit:CLOSED", "outcome-worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(completed),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:CLOSED",
        owner_token="outcome-worker",
        fence_token=completion_lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        completed,
        lease_key="submit:CLOSED",
        owner_token="outcome-worker",
        fence_token=completion_lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    store.append_fill(_fill("CLOSED-E", "BUY", order_id=3001, price=100.0))
    store.append_fill(_fill("CLOSED-X", "SELL", order_id=3101, price=105.0))
    outcome = {
        "setup_id": "CLOSED",
        "complete": True,
        "realized_r": 1.0,
        "realized_at": "2026-08-21T10:00:00+00:00",
        "outcome_evidence": "broker_fills",
        "unresolved_codes": [],
        "fill_set_hash": store.fill_evidence("CLOSED")["fill_set_hash"],
    }
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-CLOSED",
        lease_key="submit:CLOSED",
        owner_token="outcome-worker",
        fence_token=completion_lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )["accepted"] is True

    late = store.append_fill(
        _fill("CLOSED-LATE", "SELL", order_id=3101, price=104.0)
    )
    assert late["persisted"] is True
    assert late["conflict"] == "fill_set_changed_after_complete"

    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "AFTER-LATE",
        parent_order_id=4001,
        child_order_id=4101,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    lease = reopened.acquire_lease(
        "submit:AFTER-LATE", "worker", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-LATE",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_new_mapped_fill_after_release_durably_blocks_admission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    released = _intent(
        "RELEASED-LATE", parent_order_id=5001, child_order_id=5101, quantity=1
    )
    _register_intent_with_orders(store, released)
    lease = store.acquire_lease(
        "submit:RELEASED-LATE", "worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(released),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:RELEASED-LATE",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True
    assert store.mark_reservation_reconcile_required(
        "reservation-RELEASED-LATE",
        lease_key="submit:RELEASED-LATE",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="submission_interrupted",
    )["updated"] is True
    assert store.release_reservation(
        "reservation-RELEASED-LATE",
        lease_key="submit:RELEASED-LATE",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="negative full snapshot",
        broker_absence_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
            "fill_order_ids": [],
        },
    )["updated"] is True

    late = store.append_fill(
        _fill("RELEASED-LATE-FILL", "BUY", order_id=5001, price=100.0)
    )

    assert late["persisted"] is True
    assert late["conflict"] == "fill_seen_after_release"
    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "AFTER-RELEASED-LATE",
        parent_order_id=5201,
        child_order_id=5301,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    candidate_lease = reopened.acquire_lease(
        "submit:AFTER-RELEASED-LATE", "candidate", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-RELEASED-LATE",
        owner_token="candidate",
        fence_token=candidate_lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


@pytest.mark.parametrize(
    ("late_fill", "reported_conflict", "expected_conflicts"),
    [
        (
            False,
            "order_mapping_seen_after_release",
            {"order_mapping_seen_after_release"},
        ),
        (
            True,
            "fill_seen_after_release",
            {"fill_seen_after_release", "order_mapping_seen_after_release"},
        ),
    ],
)
def test_new_order_mapping_after_release_is_a_durable_conflict(
    tmp_path, late_fill, reported_conflict, expected_conflicts
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    released = _prepared_released_setup(store, "RELEASED-MAPPING")
    if late_fill:
        pending = store.append_fill(
            _fill("RELEASED-PENDING-FILL", "SELL", order_id=1201, price=105.0)
        )
        assert pending["mapping_pending"] is True

    mapped = store.register_intent_order(
        released["setup_id"],
        _order_mapping(
            released,
            1201,
            role="TARGET",
            branch=1,
            parent_order_id=1001,
        ),
    )

    assert mapped == {
        "accepted": True,
        "idempotent": False,
        "conflict": reported_conflict,
    }
    with sqlite3.connect(store.db_path) as connection:
        persisted_conflicts = {
            row[0]
            for row in connection.execute(
                """
                SELECT conflict_kind FROM trading_risk_evidence_conflicts
                WHERE setup_id='RELEASED-MAPPING'
                """
            )
        }
    assert persisted_conflicts == expected_conflicts

    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "AFTER-RELEASED-MAPPING",
        parent_order_id=2001,
        child_order_id=2101,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    lease = reopened.acquire_lease(
        "submit:AFTER-RELEASED-MAPPING", "candidate", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-RELEASED-MAPPING",
        owner_token="candidate",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_late_order_mapping_cannot_hide_fill_set_change_after_completion(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed = _intent("LATE-MAP", parent_order_id=7001, child_order_id=7101, quantity=1)
    _register_intent_with_orders(store, completed)
    completion_lease = store.acquire_lease(
        "submit:LATE-MAP", "outcome-worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(completed),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:LATE-MAP",
        owner_token="outcome-worker",
        fence_token=completion_lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        completed,
        lease_key="submit:LATE-MAP",
        owner_token="outcome-worker",
        fence_token=completion_lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    store.append_fill(_fill("LATE-MAP-E", "BUY", order_id=7001, price=100.0))
    store.append_fill(_fill("LATE-MAP-X", "SELL", order_id=7101, price=105.0))
    outcome = {
        "setup_id": "LATE-MAP",
        "complete": True,
        "realized_r": 1.0,
        "realized_at": "2026-08-21T10:00:00+00:00",
        "outcome_evidence": "broker_fills",
        "unresolved_codes": [],
        "fill_set_hash": store.fill_evidence("LATE-MAP")["fill_set_hash"],
    }
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-LATE-MAP",
        lease_key="submit:LATE-MAP",
        owner_token="outcome-worker",
        fence_token=completion_lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )["accepted"] is True

    pending = store.append_fill(
        _fill("LATE-MAP-HIDDEN", "SELL", order_id=7202, price=104.0)
    )
    assert pending["mapping_pending"] is True

    target_mapping = _order_mapping(
        completed,
        7202,
        role="TARGET",
        branch=1,
        parent_order_id=7001,
    )
    late_mapping = store.register_intent_order("LATE-MAP", target_mapping)
    assert late_mapping["accepted"] is False
    assert late_mapping["conflict"] == "intent_order_mapping_conflict"

    reopened = TradingRiskStore(store.db_path)
    candidate = _intent(
        "AFTER-LATE-MAP",
        parent_order_id=8001,
        child_order_id=8101,
        quantity=0.1,
    )
    _register_intent_with_orders(reopened, candidate)
    lease = reopened.acquire_lease(
        "submit:AFTER-LATE-MAP", "worker", now=_NOW, ttl_seconds=30
    )
    admission = reopened.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:AFTER-LATE-MAP",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    assert admission["allowed"] is False
    assert "risk_state_unresolved" in admission["risk"]["reasons"]


def test_new_order_mapping_after_complete_is_durable_without_a_pending_fill(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-LATE-MAPPING"
    )
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-COMPLETE-LATE-MAPPING",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": completed["account"],
            "con_id": completed["con_id"],
            "position_open": False,
            "open_order_ids": [],
        },
    )["accepted"] is True

    mapped = store.register_intent_order(
        completed["setup_id"],
        _order_mapping(
            completed,
            1202,
            role="TARGET",
            branch=1,
            parent_order_id=1001,
        ),
    )

    assert mapped == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_order_mapping_conflict",
    }
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT 1 FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind='intent_order_mapping_conflict'
            """,
            (completed["setup_id"],),
        ).fetchone() is not None


def test_released_setup_reappearing_mapped_stop_blocks_admission_without_parent(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    released = _prepared_released_setup(store, "RELEASED-CHILD")
    candidate = _intent(
        "AFTER-RELEASED-CHILD",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
        quantity=0.1,
    )
    _register_intent_with_orders(store, candidate)
    lease = store.acquire_lease(
        "submit:AFTER-RELEASED-CHILD", "candidate", now=_NOW, ttl_seconds=30
    )
    child = _observed_order(
        _order_mapping(
            released, 1101, role="STOP", branch=1, parent_order_id=1001
        )
    )

    admission = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=[],
        orders=[child],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        max_total_exposure_pct=20.0,
        max_positions=3,
        now=_NOW,
        lease_key="submit:AFTER-RELEASED-CHILD",
        owner_token="candidate",
        fence_token=lease["fence_token"],
    )

    assert admission["allowed"] is False
    assert admission["decision"] == "risk_blocked"
    assert "risk_state_unresolved" in admission["risk"]["reasons"]
    assert admission["risk"]["current_unresolved_codes"] == [
        "store_live_input_invalid"
    ]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind='child_order_reappeared_after_release'
            """,
            (released["setup_id"],),
        ).fetchone()[0] == 1


def test_complete_target_reappearance_is_durable_idempotent_and_terminal(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-TARGET-REAPPEARED"
    )
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-COMPLETE-TARGET-REAPPEARED",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": completed["account"],
            "con_id": completed["con_id"],
            "position_open": False,
            "open_order_ids": [],
        },
    )["transition"] == "completed"
    target = _observed_order(
        _order_mapping(
            completed, 1201, role="TARGET", branch=1, parent_order_id=1001
        )
    )
    kwargs = {
        "account": "DU1",
        "snapshot_complete": True,
        "positions": [],
        "positions_snapshot_complete": True,
        "fills_snapshot_complete": True,
        "observed_at": _NOW,
    }

    first = store.observe_open_orders([target], **kwargs)
    second = store.observe_open_orders([target], **kwargs)

    assert first["accepted"] is second["accepted"] is True
    assert first["conflicts"] == second["conflicts"] == [
        {
            "setup_id": completed["setup_id"],
            "conflict": "child_order_reappeared_after_complete",
        }
    ]
    assert store.load_outcome(completed["setup_id"])["complete"] is True
    assert store.active_reservations() == []
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind='child_order_reappeared_after_complete'
            """,
            (completed["setup_id"],),
        ).fetchone()[0] == 1


def test_incomplete_order_snapshot_blocks_without_inventing_terminal_child_conflict(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-INCOMPLETE-SNAPSHOT"
    )
    assert store.register_intent_order(
        completed["setup_id"],
        _order_mapping(
            completed, 1201, role="TARGET", branch=1, parent_order_id=1001
        ),
    )["accepted"] is True
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-COMPLETE-INCOMPLETE-SNAPSHOT",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )["accepted"] is True
    target = {
        "account": "DU1",
        "con_id": 101,
        "order_id": 1201,
        "parent_id": 1001,
        "order_ref": f"{completed['order_ref']}-T1",
        "action": "SELL",
        "order_type": "LMT",
        "quantity": 10,
        "remaining": 10,
        "filled": 0,
        "status": "Submitted",
    }

    result = store.observe_open_orders(
        [target],
        account="DU1",
        snapshot_complete=False,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result == {
        "accepted": False,
        "conflicts": [],
        "reason": "orders_snapshot_incomplete",
    }
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind LIKE 'child_order_reappeared%'
            """,
            (completed["setup_id"],),
        ).fetchone()[0] == 0


def test_terminal_order_id_reuse_is_identity_mismatch_not_reappearance(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-ORDER-ID-REUSE"
    )
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-COMPLETE-ORDER-ID-REUSE",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )["accepted"] is True
    reused = _observed_order(
        _order_mapping(
            completed, 1101, role="STOP", branch=1, parent_order_id=1001
        ),
        order_ref="AS2-SOME-OTHER-SETUP-S1",
    )

    mismatch = store.observe_open_orders(
        [reused],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )
    unrelated = dict(reused, con_id=999)
    unrelated_result = store.observe_open_orders(
        [unrelated],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert mismatch["conflicts"] == [
        {
            "setup_id": completed["setup_id"],
            "conflict": "terminal_child_identity_mismatch_after_complete",
        }
    ]
    # Durable contradictions remain visible on later clean/unrelated
    # snapshots until an explicit repair workflow resolves them.
    assert unrelated_result["conflicts"] == mismatch["conflicts"]
    with sqlite3.connect(store.db_path) as connection:
        kinds = {
            row[0]
            for row in connection.execute(
                "SELECT conflict_kind FROM trading_risk_evidence_conflicts WHERE setup_id=?",
                (completed["setup_id"],),
            )
        }
    assert kinds == {"terminal_child_identity_mismatch_after_complete"}


@pytest.mark.parametrize("terminal_state", ["complete", "release"])
@pytest.mark.parametrize("role", ["PARENT", "STOP", "TARGET"])
def test_exact_terminal_order_ref_with_new_broker_id_is_durable_identity_mismatch(
    tmp_path, terminal_state, role
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    setup_id = f"NEW-ID-{terminal_state.upper()}-{role}"
    target_id = 1201 if role == "TARGET" else None
    if terminal_state == "complete":
        intent, lease_key, lease, outcome = _prepared_terminal_outcome(
            store, setup_id, target_order_id=target_id
        )
        assert store.record_outcome(
            outcome,
            reservation_id=f"reservation-{setup_id}",
            lease_key=lease_key,
            owner_token="outcome-worker",
            fence_token=lease["fence_token"],
            now=_NOW,
            terminal_evidence={
                "snapshot_complete": True,
                "observed_at": "2026-08-21T12:00:00Z",
                "account": "DU1",
                "con_id": 101,
                "position_open": False,
                "open_order_ids": [],
            },
        )["accepted"] is True
    else:
        intent = _prepared_released_setup(
            store, setup_id, target_order_id=target_id
        )
    original_id = {"PARENT": 1001, "STOP": 1101, "TARGET": 1201}[role]
    observed = _observed_order(
        _order_mapping(
            intent,
            original_id,
            role=role,
            branch=1,
            parent_order_id=0 if role == "PARENT" else 1001,
        ),
        order_id=9999,
    )

    result = store.observe_open_orders(
        [observed],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    subject = "terminal_parent_order" if role == "PARENT" else "terminal_child"
    expected_kind = f"{subject}_identity_mismatch_after_{terminal_state}"
    assert result["accepted"] is True
    assert result["conflicts"] == [
        {"setup_id": setup_id, "conflict": expected_kind}
    ]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind=?
            """,
            (setup_id, expected_kind),
        ).fetchone()[0] == 1


def test_unknown_active_broker_order_rejects_observation_without_setup_conflict(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    unknown = {
        "account": "DU1",
        "con_id": 999,
        "order_id": 9999,
        "parent_id": 9001,
        "order_ref": "AS2-UNKNOWN-S1",
        "action": "SELL",
        "order_type": "STP",
        "quantity": 3,
        "remaining": 3,
        "filled": 0,
        "status": "Submitted",
    }

    result = store.observe_open_orders(
        [unknown],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result["accepted"] is False
    assert result["reason"] == "unknown_broker_order"
    assert result["conflicts"] == []
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM trading_risk_evidence_conflicts"
        ).fetchone()[0] == 0


def test_malformed_active_child_rejects_full_snapshot_without_false_conflict(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    completed, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-MALFORMED-CHILD"
    )
    assert store.record_outcome(
        outcome,
        reservation_id="reservation-COMPLETE-MALFORMED-CHILD",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
        },
    )["accepted"] is True
    malformed = {
        "account": "DU1",
        "con_id": 101,
        "order_id": 1101,
        "parent_id": 1001,
        "order_ref": f"{completed['order_ref']}-S1",
        "action": "SELL",
        "order_type": "STP",
        "quantity": 10,
        "filled": 0,
        "stop_price": 95,
        "status": "Submitted",
    }

    result = store.observe_open_orders(
        [malformed],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result == {
        "accepted": False,
        "conflicts": [],
        "reason": "orders_snapshot_invalid",
    }
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM trading_risk_evidence_conflicts WHERE setup_id=?",
            (completed["setup_id"],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("terminal_state", ["complete", "release"])
def test_active_parent_reappearance_is_a_durable_terminal_conflict(
    tmp_path, terminal_state
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    setup_id = f"PARENT-{terminal_state.upper()}"
    if terminal_state == "complete":
        intent, lease_key, lease, outcome = _prepared_terminal_outcome(store, setup_id)
        assert store.record_outcome(
            outcome,
            reservation_id=f"reservation-{setup_id}",
            lease_key=lease_key,
            owner_token="outcome-worker",
            fence_token=lease["fence_token"],
            now=_NOW,
            terminal_evidence={
                "snapshot_complete": True,
                "observed_at": "2026-08-21T12:00:00Z",
                "account": "DU1",
                "con_id": 101,
                "position_open": False,
                "open_order_ids": [],
            },
        )["accepted"] is True
    else:
        intent = _prepared_released_setup(store, setup_id)
    parent = _observed_order(
        _order_mapping(intent, 1001, role="PARENT", branch=1)
    )

    result = store.observe_open_orders(
        [parent],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result["conflicts"] == [
        {
            "setup_id": setup_id,
            "conflict": f"parent_order_reappeared_after_{terminal_state}",
        }
    ]
    assert store.load_outcome(setup_id) is None or store.load_outcome(setup_id)[
        "complete"
    ] is True
    assert store.active_reservations() == []


@pytest.mark.parametrize("terminal_state", ["complete", "release"])
def test_position_reappearance_without_a_new_active_setup_is_terminal_conflict(
    tmp_path, terminal_state
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    setup_id = f"POSITION-{terminal_state.upper()}"
    if terminal_state == "complete":
        _, lease_key, lease, outcome = _prepared_terminal_outcome(store, setup_id)
        assert store.record_outcome(
            outcome,
            reservation_id=f"reservation-{setup_id}",
            lease_key=lease_key,
            owner_token="outcome-worker",
            fence_token=lease["fence_token"],
            now=_NOW,
            terminal_evidence={
                "snapshot_complete": True,
                "observed_at": "2026-08-21T12:00:00Z",
                "account": "DU1",
                "con_id": 101,
                "position_open": False,
                "open_order_ids": [],
            },
        )["accepted"] is True
    else:
        _prepared_released_setup(store, setup_id)

    result = store.observe_open_orders(
        [],
        account="DU1",
        snapshot_complete=True,
        positions=[
            {
                "account": "DU1",
                "con_id": 101,
                "ticker": "XYZ",
                "quantity": 10,
                "avg_cost": 100,
            }
        ],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result["conflicts"] == [
        {
            "setup_id": setup_id,
            "conflict": f"position_reappeared_after_{terminal_state}",
        }
    ]


@pytest.mark.parametrize("old_terminal", ["complete", "release"])
def test_legitimate_reentry_same_contract_is_not_attributed_to_old_terminal_setup(
    tmp_path, old_terminal
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    old_setup = f"OLD-{old_terminal.upper()}"
    if old_terminal == "complete":
        _, lease_key, lease, outcome = _prepared_terminal_outcome(store, old_setup)
        assert store.record_outcome(
            outcome,
            reservation_id=f"reservation-{old_setup}",
            lease_key=lease_key,
            owner_token="outcome-worker",
            fence_token=lease["fence_token"],
            now=_NOW,
            terminal_evidence={
                "snapshot_complete": True,
                "observed_at": "2026-08-21T12:00:00Z",
                "account": "DU1",
                "con_id": 101,
                "position_open": False,
                "open_order_ids": [],
            },
        )["accepted"] is True
    else:
        _prepared_released_setup(store, old_setup)
    reentry = _intent(
        f"NEW-AFTER-{old_terminal.upper()}",
        parent_order_id=2001,
        child_order_id=2101,
    )
    _register_intent_with_orders(store, reentry)
    reentry_lease = store.acquire_lease(
        f"submit:{reentry['setup_id']}", "reentry", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(reentry),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0,
        max_total_exposure_pct=20,
        max_positions=3,
        now=_NOW,
        lease_key=f"submit:{reentry['setup_id']}",
        owner_token="reentry",
        fence_token=reentry_lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        reentry,
        lease_key=f"submit:{reentry['setup_id']}",
        owner_token="reentry",
        fence_token=reentry_lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    assert store.append_fill(
        _fill(
            f"{reentry['setup_id']}-ENTRY",
            "BUY",
            order_id=2001,
            shares=10,
            price=100,
        )
    )["accepted"] is True

    observation = store.observe_open_orders(
        [_observed_order(_order_mapping(
            reentry, 2101, role="STOP", branch=1, parent_order_id=2001
        ))],
        account="DU1",
        snapshot_complete=True,
        positions=[
            {
                "account": "DU1",
                "con_id": 101,
                "ticker": "XYZ",
                "quantity": 10,
                "avg_cost": 100,
            }
        ],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert observation["accepted"] is True
    assert observation["conflicts"] == []
    assert observation["position_setup_ids"] == [reentry["setup_id"]]
    assert old_setup in observation["terminal_setup_ids"]


def test_incomplete_fill_snapshot_cannot_assert_terminal_position_reappearance(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    released = _prepared_released_setup(store, "RELEASED-FILL-SNAPSHOT")

    result = store.observe_open_orders(
        [],
        account="DU1",
        snapshot_complete=True,
        positions=[
            {
                "account": "DU1",
                "con_id": released["con_id"],
                "ticker": "XYZ",
                "quantity": 10,
                "avg_cost": 100,
            }
        ],
        positions_snapshot_complete=True,
        fills_snapshot_complete=False,
        observed_at=_NOW,
    )

    assert result == {
        "accepted": False,
        "conflicts": [],
        "reason": "fills_snapshot_incomplete",
    }
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM trading_risk_evidence_conflicts WHERE setup_id=?",
            (released["setup_id"],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("registered_orphan", [False, True])
def test_unknown_broker_position_rejects_observation_without_invented_setup(
    tmp_path, registered_orphan
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    if registered_orphan:
        orphan = _intent(
            "REGISTERED-ONLY",
            con_id=999,
            parent_order_id=9001,
            child_order_id=9101,
        )
        _register_intent_with_orders(store, orphan)

    result = store.observe_open_orders(
        [],
        account="DU1",
        snapshot_complete=True,
        positions=[
            {
                "account": "DU1",
                "con_id": 999,
                "ticker": "MANUAL",
                "quantity": 2,
                "avg_cost": 50,
            }
        ],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result["accepted"] is False
    assert result["reason"] == "unknown_broker_position"
    assert result["conflicts"] == []
    assert result["position_setup_ids"] == []


@pytest.mark.parametrize("evidence_problem", ["missing_fill", "wrong_sign", "fill_conflict"])
def test_unreliable_active_reentry_cannot_explain_a_terminal_contract_position(
    tmp_path, evidence_problem
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    released = _prepared_released_setup(store, f"OLD-{evidence_problem.upper()}")
    active = _intent(
        f"ACTIVE-{evidence_problem.upper()}",
        parent_order_id=2001,
        child_order_id=2101,
    )
    _register_intent_with_orders(store, active)
    lease_key = f"submit:{active['setup_id']}"
    lease = store.acquire_lease(lease_key, "active", now=_NOW, ttl_seconds=30)
    assert store.reserve_if_allowed(
        _reservation(active),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0,
        max_total_exposure_pct=20,
        max_positions=3,
        now=_NOW,
        lease_key=lease_key,
        owner_token="active",
        fence_token=lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        active,
        lease_key=lease_key,
        owner_token="active",
        fence_token=lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    if evidence_problem != "missing_fill":
        entry = _fill(
            f"{active['setup_id']}-ENTRY",
            "BUY",
            order_id=2001,
            shares=10,
            price=100,
        )
        assert store.append_fill(entry)["accepted"] is True
        if evidence_problem == "fill_conflict":
            assert store.append_fill(dict(entry, price=101))["conflict"] == (
                "exec_id_payload_conflict"
            )
    broker_quantity = -10 if evidence_problem == "wrong_sign" else 10

    observation = store.observe_open_orders(
        [],
        account="DU1",
        snapshot_complete=True,
        positions=[
            {
                "account": "DU1",
                "con_id": active["con_id"],
                "ticker": "XYZ",
                "quantity": broker_quantity,
                "avg_cost": 100,
            }
        ],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert observation["accepted"] is True
    assert observation["position_setup_ids"] == []
    assert observation["conflicts"]
    assert {
        conflict["conflict"] for conflict in observation["conflicts"]
    } == {"position_attribution_unresolved"}
    assert all(
        not conflict["conflict"].startswith("position_reappeared_after_")
        for conflict in observation["conflicts"]
    )
    assert released["setup_id"] in observation["terminal_setup_ids"]


def test_multiple_terminal_histories_use_neutral_position_attribution_conflict(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _prepared_released_setup(store, "RELEASED-HISTORY-A")
    second = _prepared_released_setup(
        store,
        "RELEASED-HISTORY-B",
        parent_order_id=2001,
        child_order_id=2101,
    )

    observation = store.observe_open_orders(
        [],
        account="DU1",
        snapshot_complete=True,
        positions=[
            {
                "account": "DU1",
                "con_id": 101,
                "ticker": "XYZ",
                "quantity": 10,
                "avg_cost": 100,
            }
        ],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert observation["position_setup_ids"] == []
    assert observation["conflicts"] == [
        {
            "setup_id": first["setup_id"],
            "conflict": "position_attribution_unresolved",
        },
        {
            "setup_id": second["setup_id"],
            "conflict": "position_attribution_unresolved",
        },
    ]
    with sqlite3.connect(store.db_path) as connection:
        kinds = {
            row[0]
            for row in connection.execute(
                "SELECT conflict_kind FROM trading_risk_evidence_conflicts"
            )
        }
    assert kinds == {"position_attribution_unresolved"}


def test_current_risk_intents_require_a_nonterminal_reservation(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    orphan = _intent("ORPHAN", parent_order_id=1001, child_order_id=1101)
    active = _intent("ACTIVE", parent_order_id=2001, child_order_id=2101)
    _register_intent_with_orders(store, orphan)
    _register_intent_with_orders(store, active)
    lease = store.acquire_lease("submit:ACTIVE", "worker", now=_NOW, ttl_seconds=30)
    assert store.reserve_if_allowed(
        _reservation(active),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0,
        max_total_exposure_pct=20,
        max_positions=3,
        now=_NOW,
        lease_key="submit:ACTIVE",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True

    connection = store._connect()
    try:
        setup_ids = {
            intent["setup_id"] for intent in store._risk_intents(connection, "DU1")
        }
    finally:
        connection.close()

    assert setup_ids == {active["setup_id"]}


def test_complete_outcome_requires_matching_persisted_fill_set(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("NO-FILLS")
    _register_intent_with_orders(store, intent)
    asserted = {
        "setup_id": "NO-FILLS",
        "complete": True,
        "realized_r": 99.0,
        "realized_at": "2026-08-21T10:05:00+00:00",
        "outcome_evidence": "broker_fills",
        "unresolved_codes": [],
        "fill_set_hash": "not-a-real-ledger-hash",
    }

    result = store.record_outcome(asserted)

    assert result["accepted"] is False
    assert result["conflict"] == "outcome_fill_evidence_invalid"
    assert store.load_outcome("NO-FILLS") is None


def test_execution_generation_blocks_stale_broker_write_after_kill(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    armed = store.transition_execution_state(True)
    executed = []

    killed = store.transition_execution_state(False)
    stale = store.run_if_execution_generation(
        armed["generation"], lambda: executed.append("broker-write")
    )

    assert armed == {
        "updated": True,
        "armed": True,
        "generation": 1,
        "reason": None,
    }
    assert killed == {
        "updated": True,
        "armed": False,
        "generation": 2,
        "reason": None,
    }
    assert stale == {
        "executed": False,
        "result": None,
        "armed": False,
        "generation": 2,
        "reason": "execution_generation_fenced",
    }
    assert executed == []


def test_kill_transition_fences_inflight_write_without_holding_database_lock(
    tmp_path,
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    armed = store.transition_execution_state(True)
    write_started = Event()
    release_write = Event()
    write_outcome = {}
    kill_outcome = {}
    kill_finished = Event()

    def broker_write():
        write_started.set()
        assert release_write.wait(timeout=10)
        return "broker-result"

    writer = Thread(
        target=lambda: write_outcome.setdefault(
            "result",
            store.run_if_execution_generation(armed["generation"], broker_write),
        )
    )
    writer.start()
    assert write_started.wait(timeout=10)

    def kill():
        kill_outcome["result"] = store.transition_execution_state(False)
        kill_finished.set()

    killer = Thread(target=kill)
    killer.start()
    try:
        assert kill_finished.wait(timeout=2)
    finally:
        release_write.set()
        writer.join(timeout=10)
        killer.join(timeout=10)

    assert not writer.is_alive()
    assert not killer.is_alive()
    assert kill_outcome["result"]["generation"] == armed["generation"] + 1
    assert kill_outcome["result"]["armed"] is False
    assert write_outcome["result"] == {
        "executed": True,
        "result": "broker-result",
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": "execution_generation_fenced_after_write",
    }


def test_execution_write_drain_timeout_remains_durably_disarmed(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    armed = store.transition_execution_state(True)
    write_started = Event()
    release_write = Event()

    def broker_write():
        write_started.set()
        assert release_write.wait(timeout=10)
        return "broker-result"

    writer = Thread(
        target=lambda: store.run_if_execution_generation(
            armed["generation"], broker_write
        )
    )
    writer.start()
    assert write_started.wait(timeout=10)
    killed = store.transition_execution_state(False)
    try:
        timed_out = store.wait_for_execution_writes(
            timeout_seconds=0.05, poll_interval_seconds=0.005
        )
    finally:
        release_write.set()
        writer.join(timeout=10)

    assert timed_out == {
        "drained": False,
        "active_count": 1,
        "reason": "execution_writes_active",
    }
    assert store.execution_state() == {
        "armed": False,
        "generation": killed["generation"],
        "reason": None,
    }
    assert store.wait_for_execution_writes(
        timeout_seconds=0.05, poll_interval_seconds=0.005
    ) == {"drained": True, "active_count": 0, "reason": None}


def test_process_crash_orphans_write_and_atomically_fences_generation(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    context = multiprocessing.get_context("spawn")
    write_started = context.Event()
    worker = context.Process(
        target=_crash_during_execution_write_worker,
        args=(str(db_path), armed["generation"], write_started),
    )

    worker.start()
    assert write_started.wait(timeout=15)
    worker.join(timeout=15)
    assert not worker.is_alive()
    assert worker.exitcode == 23

    drain = store.wait_for_execution_writes(
        timeout_seconds=0.1, poll_interval_seconds=0.005
    )
    assert drain == {
        "drained": False,
        "active_count": 0,
        "orphaned_count": 1,
        "reason": "execution_writes_orphaned",
    }
    assert store.execution_state() == {
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": None,
    }
    executed = []
    stale = store.run_if_execution_generation(
        armed["generation"], lambda: executed.append("broker-write")
    )
    assert stale["executed"] is False
    assert stale["reason"] == "execution_generation_fenced"
    assert executed == []
    unresolved = store.reconcile_orphaned_execution_writes(
        armed["generation"] + 1,
        reconciliation_started_at=datetime.now(timezone.utc),
        observed_at=datetime.now(timezone.utc) + timedelta(microseconds=1),
        orders_snapshot_complete=True,
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        risk_evidence_reliable=True,
        reconciled_accounts=["DU1"],
    )
    assert unresolved == {
        "accepted": False,
        "resolved_count": 0,
        "generation": armed["generation"] + 1,
        "reason": "execution_recovery_broker_visibility_unproven",
    }


def test_retained_place_order_claim_survives_exception_until_quarantined(
    tmp_path,
):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    registered = []

    def ambiguous_write():
        raise ConnectionError("socket outcome ambiguous after send")

    with pytest.raises(ConnectionError, match="ambiguous after send"):
        store.run_if_execution_generation(
            armed["generation"],
            ambiguous_write,
            claim_context={
                "operation_kind": "PLACE_ORDER",
                "account": "DU1",
                "setup_id": "RETAINED",
                "order_id": 777,
                "order_ref": "AS2-RETAINED-P1",
            },
            retain_until_ack=True,
            on_claim_registered=registered.append,
        )
    assert len(registered) == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT status FROM trading_risk_execution_writes WHERE write_id=?",
            (registered[0],),
        ).fetchone()[0] == "ACTIVE"

    quarantined = store.quarantine_execution_write(
        registered[0], expected_generation=armed["generation"]
    )
    assert quarantined == {
        "updated": True,
        "status": "ORPHANED",
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": None,
    }
    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    )["reason"] == "execution_writes_orphaned"


def test_retained_place_order_claim_ack_requires_exact_broker_visible_mapping(
    tmp_path,
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(
        "ACK-CLAIM",
        parent_order_id=991,
        child_order_id=992,
        target_order_id=993,
    )
    _register_intent_with_orders(store, intent)
    lease_key = "submit:ACK-CLAIM"
    owner_token = "ack-claim-worker"
    lease = store.acquire_lease(
        lease_key, owner_token, now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        available_funds=100_000.0,
        min_available_funds=0.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        orders_snapshot_complete=True,
        now=_NOW,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=lease["fence_token"],
    )["allowed"] is True
    armed = store.transition_execution_state(True)
    registered = []
    guarded = store.run_if_execution_generation(
        armed["generation"],
        lambda: "broker-write-complete",
        claim_context={
            "operation_kind": "PLACE_ORDER",
            "account": "DU1",
            "setup_id": "ACK-CLAIM",
            "order_id": 991,
            "order_ref": "AS2-ACK-CLAIM-P1",
        },
        retain_until_ack=True,
        on_claim_registered=registered.append,
    )
    assert guarded["write_id"] == registered[0]
    assert store.acknowledge_execution_write(
        registered[0], expected_generation=armed["generation"]
    ) == {
        "updated": False,
        "reason": "execution_claim_visibility_unproven",
    }
    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    )["active_count"] == 1

    assert _mark_broker_visible(
        store,
        intent,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    assert store.acknowledge_execution_write(
        registered[0], expected_generation=armed["generation"]
    ) == {"updated": True, "reason": None}
    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    ) == {"drained": True, "active_count": 0, "reason": None}


def test_visible_reservation_cannot_reconcile_wrong_order_identity_claim(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(
        "WRONG-CLAIM-ID",
        parent_order_id=991,
        child_order_id=992,
        target_order_id=993,
    )
    _register_intent_with_orders(store, intent)
    lease_key = "submit:WRONG-CLAIM-ID"
    owner_token = "wrong-claim-worker"
    lease = store.acquire_lease(
        lease_key, owner_token, now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        available_funds=100_000.0,
        min_available_funds=0.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        orders_snapshot_complete=True,
        now=_NOW,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        intent,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    armed = store.transition_execution_state(True)
    registered = []
    store.run_if_execution_generation(
        armed["generation"],
        lambda: "ambiguous-wrong-order",
        claim_context={
            "operation_kind": "PLACE_ORDER",
            "account": "DU1",
            "setup_id": "WRONG-CLAIM-ID",
            "order_id": 999,
            "order_ref": "AS2-WRONG-CLAIM-ID-P1",
        },
        retain_until_ack=True,
        on_claim_registered=registered.append,
    )
    quarantined = store.quarantine_execution_write(
        registered[0], expected_generation=armed["generation"]
    )
    started = datetime.now(timezone.utc)
    assert store.reconcile_orphaned_execution_writes(
        quarantined["generation"],
        reconciliation_started_at=started,
        observed_at=started + timedelta(microseconds=1),
        orders_snapshot_complete=True,
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        risk_evidence_reliable=True,
        reconciled_accounts=["DU1"],
    ) == {
        "accepted": False,
        "resolved_count": 0,
        "generation": quarantined["generation"],
        "reason": "execution_recovery_broker_visibility_unproven",
    }


def test_orphaned_write_requires_complete_causal_reconciliation(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    intent = _intent(
        "CRASH-CLAIM",
        parent_order_id=991,
        child_order_id=992,
        target_order_id=993,
    )
    _register_intent_with_orders(store, intent)
    lease_key = "submit:CRASH-CLAIM"
    owner_token = "crash-claim-evidence"
    lease = store.acquire_lease(
        lease_key, owner_token, now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        available_funds=100_000.0,
        min_available_funds=0.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        orders_snapshot_complete=True,
        now=_NOW,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        intent,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=lease["fence_token"],
        now=_NOW,
    )["updated"] is True
    armed = store.transition_execution_state(True)
    context = multiprocessing.get_context("spawn")
    write_started = context.Event()
    worker = context.Process(
        target=_crash_during_execution_write_worker,
        args=(str(db_path), armed["generation"], write_started),
    )
    worker.start()
    assert write_started.wait(timeout=15)
    worker.join(timeout=15)
    assert worker.exitcode == 23
    orphaned = store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    )
    assert orphaned["reason"] == "execution_writes_orphaned"
    recovery_generation = store.execution_state()["generation"]

    incomplete = store.reconcile_orphaned_execution_writes(
        recovery_generation,
        reconciliation_started_at=datetime.now(timezone.utc),
        observed_at=datetime.now(timezone.utc),
        orders_snapshot_complete=True,
        positions_snapshot_complete=False,
        fills_snapshot_complete=True,
        risk_evidence_reliable=True,
        reconciled_accounts=["DU1"],
    )
    assert incomplete == {
        "accepted": False,
        "resolved_count": 0,
        "generation": recovery_generation,
        "reason": "execution_recovery_evidence_incomplete",
    }
    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    )["reason"] == "execution_writes_orphaned"

    with sqlite3.connect(db_path) as connection:
        raw_orphaned_at = connection.execute(
            "SELECT orphaned_at FROM trading_risk_execution_writes "
            "WHERE status='ORPHANED'"
        ).fetchone()[0]
    orphaned_at = datetime.fromisoformat(raw_orphaned_at)
    noncausal = store.reconcile_orphaned_execution_writes(
        recovery_generation,
        reconciliation_started_at=orphaned_at - timedelta(microseconds=1),
        observed_at=datetime.now(timezone.utc),
        orders_snapshot_complete=True,
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        risk_evidence_reliable=True,
        reconciled_accounts=["DU1"],
    )
    assert noncausal == {
        "accepted": False,
        "resolved_count": 0,
        "generation": recovery_generation,
        "reason": "execution_recovery_snapshot_not_causal",
    }

    wrong_account = store.reconcile_orphaned_execution_writes(
        recovery_generation,
        reconciliation_started_at=datetime.now(timezone.utc),
        observed_at=datetime.now(timezone.utc) + timedelta(microseconds=1),
        orders_snapshot_complete=True,
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        risk_evidence_reliable=True,
        reconciled_accounts=["DU2"],
    )
    assert wrong_account == {
        "accepted": False,
        "resolved_count": 0,
        "generation": recovery_generation,
        "reason": "execution_recovery_account_coverage_incomplete",
    }

    recovery_started = datetime.now(timezone.utc)
    recovered = store.reconcile_orphaned_execution_writes(
        recovery_generation,
        reconciliation_started_at=recovery_started,
        observed_at=recovery_started + timedelta(microseconds=1),
        orders_snapshot_complete=True,
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        risk_evidence_reliable=True,
        reconciled_accounts=["DU1"],
    )
    assert recovered == {
        "accepted": True,
        "resolved_count": 1,
        "generation": recovery_generation,
        "reason": None,
    }
    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    ) == {"drained": True, "active_count": 0, "reason": None}
    rearmed = store.transition_execution_state(
        True,
        expected_generation=recovery_generation,
        require_drained=True,
    )
    assert rearmed == {
        "updated": True,
        "armed": True,
        "generation": recovery_generation + 1,
        "reason": None,
    }


def test_legacy_execution_write_without_liveness_protocol_never_auto_reaps(
    tmp_path,
):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO trading_risk_execution_writes
                (write_id, generation, started_at)
            VALUES (?, ?, ?)
            """,
            ("legacy-write", armed["generation"], _NOW.isoformat()),
        )

    drain = store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    )
    assert drain == {
        "drained": False,
        "active_count": 1,
        "reason": "execution_writes_active",
    }
    assert store.execution_state()["armed"] is False


def test_live_execution_lock_never_expires_from_claim_age(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    write_started = Event()
    release_write = Event()

    def broker_write():
        write_started.set()
        assert release_write.wait(timeout=10)

    writer = Thread(
        target=lambda: store.run_if_execution_generation(
            armed["generation"], broker_write
        )
    )
    writer.start()
    assert write_started.wait(timeout=10)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE trading_risk_execution_writes SET started_at=?",
            ("2000-01-01T00:00:00+00:00",),
        )
    try:
        assert store.wait_for_execution_writes(
            timeout_seconds=0, poll_interval_seconds=0.005
        ) == {
            "drained": False,
            "active_count": 1,
            "reason": "execution_writes_active",
        }
        assert store.execution_state() == {
            "armed": True,
            "generation": armed["generation"],
            "reason": None,
        }
    finally:
        store.transition_execution_state(False)
        release_write.set()
        writer.join(timeout=10)
    assert not writer.is_alive()


def test_unverifiable_execution_lock_fences_and_remains_blocking(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO trading_risk_execution_writes
                (write_id, generation, started_at,
                 lock_protocol_version, status)
            VALUES (?, ?, ?, 1, 'ACTIVE')
            """,
            ("not-a-valid-lock-identity", armed["generation"], _NOW.isoformat()),
        )

    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    ) == {
        "drained": False,
        "active_count": 1,
        "reason": "execution_writes_active",
    }
    assert store.execution_state() == {
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": None,
    }


def test_defective_execution_lock_path_is_unknown_not_owner_death(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    write_id = "b" * 32
    lock_path = store._execution_lock_dir / f"{write_id}.lock"
    lock_path.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO trading_risk_execution_writes
                (write_id, generation, started_at,
                 lock_protocol_version, status)
            VALUES (?, ?, ?, 1, 'ACTIVE')
            """,
            (write_id, armed["generation"], _NOW.isoformat()),
        )

    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    ) == {
        "drained": False,
        "active_count": 1,
        "reason": "execution_writes_active",
    }
    assert store.execution_state() == {
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": None,
    }


def test_non_contention_lock_backend_error_is_unknown_and_fences(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    write_id = "d" * 32
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO trading_risk_execution_writes
                (write_id, generation, started_at,
                 lock_protocol_version, status)
            VALUES (?, ?, ?, 1, 'ACTIVE')
            """,
            (write_id, armed["generation"], _NOW.isoformat()),
        )

    def fail_lock(*_args, **_kwargs):
        raise OSError(errno.ENOLCK, "lock table unavailable")

    if os.name == "nt":
        import msvcrt

        monkeypatch.setattr(msvcrt, "locking", fail_lock)
    else:
        import fcntl

        monkeypatch.setattr(fcntl, "flock", fail_lock)

    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    ) == {
        "drained": False,
        "active_count": 1,
        "reason": "execution_writes_active",
    }
    assert store.execution_state() == {
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": None,
    }


def test_unknown_execution_claim_status_is_never_treated_as_drained(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    armed = store.transition_execution_state(True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO trading_risk_execution_writes
                (write_id, generation, started_at,
                 lock_protocol_version, status)
            VALUES (?, ?, ?, 1, 'CORRUPT_STATUS')
            """,
            ("c" * 32, armed["generation"], _NOW.isoformat()),
        )

    assert store.wait_for_execution_writes(
        timeout_seconds=0, poll_interval_seconds=0.005
    ) == {
        "drained": False,
        "active_count": 1,
        "reason": "execution_writes_active",
    }
    assert store.execution_state() == {
        "armed": False,
        "generation": armed["generation"] + 1,
        "reason": None,
    }


def test_rearm_requires_current_generation_and_fully_drained_writes(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    armed = store.transition_execution_state(True)
    write_started = Event()
    release_write = Event()

    def broker_write():
        write_started.set()
        assert release_write.wait(timeout=10)
        return "broker-result"

    writer = Thread(
        target=lambda: store.run_if_execution_generation(
            armed["generation"], broker_write
        )
    )
    writer.start()
    assert write_started.wait(timeout=10)
    killed = store.transition_execution_state(False)
    try:
        blocked_active = store.transition_execution_state(
            True,
            expected_generation=killed["generation"],
            require_drained=True,
        )
        assert blocked_active == {
            "updated": False,
            "armed": False,
            "generation": killed["generation"],
            "reason": "execution_writes_active",
        }
    finally:
        release_write.set()
        writer.join(timeout=10)

    blocked_stale = store.transition_execution_state(
        True,
        expected_generation=armed["generation"],
        require_drained=True,
    )
    rearmed = store.transition_execution_state(
        True,
        expected_generation=killed["generation"],
        require_drained=True,
    )

    assert blocked_stale == {
        "updated": False,
        "armed": False,
        "generation": killed["generation"],
        "reason": "execution_generation_fenced",
    }
    assert rearmed == {
        "updated": True,
        "armed": True,
        "generation": killed["generation"] + 1,
        "reason": None,
    }


def test_rearm_cannot_bypass_active_write_drain_by_omitting_flag(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    armed = store.transition_execution_state(True)
    write_started = Event()
    release_write = Event()

    def broker_write():
        write_started.set()
        assert release_write.wait(timeout=10)

    writer = Thread(
        target=lambda: store.run_if_execution_generation(
            armed["generation"], broker_write
        )
    )
    writer.start()
    assert write_started.wait(timeout=10)
    killed = store.transition_execution_state(False)
    try:
        bypass = store.transition_execution_state(
            True,
            expected_generation=killed["generation"],
        )
        assert bypass == {
            "updated": False,
            "armed": False,
            "generation": killed["generation"],
            "reason": "execution_writes_active",
        }
    finally:
        release_write.set()
        writer.join(timeout=10)
    assert not writer.is_alive()


def test_atomic_reservation_rejects_generation_fenced_before_admission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("GENERATION-ADMISSION")
    _register_intent_with_orders(store, intent)
    armed = store.transition_execution_state(True)
    lease = store.acquire_lease(
        "submit:GENERATION-ADMISSION",
        "worker-a",
        now=_NOW,
        ttl_seconds=30,
    )
    store.transition_execution_state(False)

    decision = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        orders_snapshot_complete=True,
        execution_generation=armed["generation"],
        now=_NOW,
        lease_key="submit:GENERATION-ADMISSION",
        owner_token="worker-a",
        fence_token=lease["fence_token"],
    )

    assert decision == {
        "allowed": False,
        "decision": "execution_generation_fenced",
    }
    assert store.active_reservations(now=_NOW) == []


def test_order_callback_mapping_rejects_generation_fenced_after_broker_write(
    tmp_path,
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("GENERATION-MAPPING")
    assert store.register_intent(intent)["accepted"] is True
    armed = store.transition_execution_state(True)
    store.transition_execution_state(False)

    result = store.register_intent_order(
        intent["setup_id"],
        _order_mapping(intent, 7001, role="PARENT", branch=1),
        execution_generation=armed["generation"],
    )

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "execution_generation_fenced",
    }
    assert store.intent_order_ids(intent["setup_id"]) == []


def test_broker_visible_callback_rejects_generation_fenced_after_ack(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("GENERATION-VISIBLE")
    _register_intent_with_orders(store, intent)
    armed = store.transition_execution_state(True)
    lease_key = "submit:GENERATION-VISIBLE"
    lease = store.acquire_lease(
        lease_key,
        "worker-a",
        now=_NOW,
        ttl_seconds=30,
    )
    reservation = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        orders_snapshot_complete=True,
        execution_generation=armed["generation"],
        now=_NOW,
        lease_key=lease_key,
        owner_token="worker-a",
        fence_token=lease["fence_token"],
    )
    evidence = _full_broker_order_evidence(store, intent)
    store.transition_execution_state(False)

    result = store.mark_reservation_broker_visible(
        f"reservation-{intent['setup_id']}",
        list(intent["order_ids"]),
        lease_key=lease_key,
        owner_token="worker-a",
        fence_token=lease["fence_token"],
        now=_NOW,
        broker_order_evidence=evidence,
        execution_generation=armed["generation"],
    )

    assert result == {
        "updated": False,
        "reason": "execution_generation_fenced",
    }
    assert store.active_reservations(now=_NOW)[0]["status"] == "SUBMITTING"


def test_fenced_lease_takeover_rejects_stale_owner_and_reservation_survives_expiry(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    store.initialize()
    intent = _intent()
    _register_intent_with_orders(store, intent)

    first = store.acquire_lease("submit:A", "worker-a", now=_NOW, ttl_seconds=30)
    held = store.acquire_lease("submit:A", "worker-b", now=_NOW, ttl_seconds=30)
    held_same_owner = store.acquire_lease(
        "submit:A", "worker-a", now=_NOW, ttl_seconds=30
    )
    takeover_time = datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc)
    takeover = store.acquire_lease("submit:A", "worker-b", now=takeover_time, ttl_seconds=30)
    stale = store.renew_lease(
        "submit:A",
        "worker-a",
        first["fence_token"],
        now=takeover_time,
        ttl_seconds=30,
    )

    assert first["acquired"] is True
    assert held == {"acquired": False, "reason": "lease_held", "fence_token": first["fence_token"]}
    assert held_same_owner == {
        "acquired": False,
        "reason": "lease_held",
        "fence_token": first["fence_token"],
    }
    assert takeover["acquired"] is True
    assert takeover["fence_token"] == first["fence_token"] + 1
    assert stale == {"renewed": False, "reason": "lease_fenced"}

    reserved = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=takeover_time,
        lease_key="submit:A",
        owner_token="worker-b",
        fence_token=takeover["fence_token"],
    )
    assert reserved["allowed"] is True

    after_expiry = datetime(2026, 8, 21, 12, 3, tzinfo=timezone.utc)
    reopened = TradingRiskStore(store.db_path)
    assert reopened.active_reservations(now=after_expiry) == [
        {
            **_reservation(intent),
            "cash_basis_price_usd": 100.5,
            "cash_required_usd": 1_005.0,
            "risk_basis_price_usd": 100.5,
            "risk_per_share_usd": 5.5,
        }
    ]

    stale_transition = reopened.mark_reservation_reconcile_required(
        "reservation-A",
        lease_key="submit:A",
        owner_token="worker-b",
        fence_token=takeover["fence_token"],
        now=after_expiry,
        reason="process_restart",
    )
    assert stale_transition == {"updated": False, "reason": "lease_fenced"}

    recovery = reopened.acquire_lease(
        "submit:A", "worker-c", now=after_expiry, ttl_seconds=30
    )
    assert recovery["acquired"] is True
    assert reopened.mark_reservation_reconcile_required(
        "reservation-A",
        lease_key="submit:A",
        owner_token="worker-c",
        fence_token=recovery["fence_token"],
        now=after_expiry,
        reason="process_restart",
    )["updated"] is True
    assert reopened.active_reservations()[0]["status"] == "RECONCILE_REQUIRED"
    full_evidence = _full_broker_order_evidence(reopened, intent)
    wrong_visible = reopened.mark_reservation_broker_visible(
        "reservation-A",
        [999999],
        lease_key="submit:A",
        owner_token="worker-c",
        fence_token=recovery["fence_token"],
        now=after_expiry,
    )
    assert wrong_visible == {"updated": False, "reason": "broker_order_ids_mismatch"}
    assert len(reopened.active_reservations()) == 1
    visible = reopened.mark_reservation_broker_visible(
        "reservation-A",
        list(intent["order_ids"]),
        lease_key="submit:A",
        owner_token="worker-c",
        fence_token=recovery["fence_token"],
        now=after_expiry,
        broker_order_evidence=full_evidence,
    )
    assert visible["updated"] is True
    assert reopened.active_reservations()[0]["status"] == "BROKER_VISIBLE"
    second_attempt = reopened.reserve_if_allowed(
        _reservation(intent, "another-reservation-id"),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=after_expiry,
        lease_key="submit:A",
        owner_token="worker-c",
        fence_token=recovery["fence_token"],
    )
    assert second_attempt == {"allowed": False, "decision": "already_reserved"}


def test_broker_visible_reservation_covers_a_competing_stale_broker_snapshot(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent(
        "VISIBLE-RISK",
        parent_order_id=9001,
        child_order_id=9101,
        target_order_id=9151,
        quantity=100,
    )
    second = _intent(
        "STALE-NEXT",
        parent_order_id=9201,
        child_order_id=9301,
        target_order_id=9351,
        quantity=60,
    )
    for intent in (first, second):
        intent["group_verified"] = False
        _register_intent_with_orders(store, intent)
    first_lease = store.acquire_lease(
        "submit:VISIBLE-RISK", "first", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(first),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:VISIBLE-RISK",
        owner_token="first",
        fence_token=first_lease["fence_token"],
    )["allowed"] is True
    assert _mark_broker_visible(
        store,
        first,
        lease_key="submit:VISIBLE-RISK",
        owner_token="first",
        fence_token=first_lease["fence_token"],
        now=_NOW,
    )["updated"] is True

    second_lease = store.acquire_lease(
        "submit:STALE-NEXT", "second", now=_NOW, ttl_seconds=30
    )
    stale_admission = store.reserve_if_allowed(
        _reservation(second),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:STALE-NEXT",
        owner_token="second",
        fence_token=second_lease["fence_token"],
    )
    assert stale_admission["allowed"] is False
    assert "max_total_risk_exceeded" in stale_admission["risk"]["reasons"]


def test_reserved_replay_after_fence_takeover_never_reauthorizes_submission(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("REPLAY")
    _register_intent_with_orders(store, intent)
    first = store.acquire_lease("submit:REPLAY", "attempt-a", now=_NOW, ttl_seconds=30)
    reservation = _reservation(intent)
    initial = store.reserve_if_allowed(
        reservation,
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:REPLAY",
        owner_token="attempt-a",
        fence_token=first["fence_token"],
    )
    assert initial["allowed"] is True

    takeover_time = datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc)
    takeover = store.acquire_lease(
        "submit:REPLAY", "attempt-b", now=takeover_time, ttl_seconds=30
    )
    replay = store.reserve_if_allowed(
        reservation,
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=takeover_time,
        lease_key="submit:REPLAY",
        owner_token="attempt-b",
        fence_token=takeover["fence_token"],
    )

    assert replay["allowed"] is False
    assert replay["decision"] == "already_reserved"
    assert len(store.active_reservations()) == 1


def test_release_requires_reconcile_state_and_bound_negative_broker_evidence(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("RELEASE-GUARD")
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:RELEASE-GUARD", "worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:RELEASE-GUARD",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True

    direct = store.release_reservation(
        "reservation-RELEASE-GUARD",
        lease_key="submit:RELEASE-GUARD",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="no orders seen",
    )
    assert direct == {"updated": False, "reason": "reservation_transition_invalid"}
    assert store.mark_reservation_reconcile_required(
        "reservation-RELEASE-GUARD",
        lease_key="submit:RELEASE-GUARD",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="submission_interrupted",
    )["updated"] is True

    missing = store.release_reservation(
        "reservation-RELEASE-GUARD",
        lease_key="submit:RELEASE-GUARD",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="no orders seen",
    )
    assert missing == {"updated": False, "reason": "broker_absence_evidence_required"}

    visible = store.release_reservation(
        "reservation-RELEASE-GUARD",
        lease_key="submit:RELEASE-GUARD",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="negative full snapshot",
        broker_absence_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [1001],
            "fill_order_ids": [],
        },
    )
    assert visible == {"updated": False, "reason": "broker_evidence_present"}

    released = store.release_reservation(
        "reservation-RELEASE-GUARD",
        lease_key="submit:RELEASE-GUARD",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        reason="negative full snapshot",
        broker_absence_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:00Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
            "fill_order_ids": [],
        },
    )
    assert released == {"updated": True, "status": "RELEASED"}
    assert store.active_reservations() == []


def test_release_rejects_empty_snapshot_observed_before_reconcile_transition(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("STALE-RELEASE")
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:STALE-RELEASE", "worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:STALE-RELEASE",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True

    reconcile_at = datetime(
        2026, 8, 21, 12, 0, 10, 500_000, tzinfo=timezone.utc
    )
    assert store.mark_reservation_reconcile_required(
        "reservation-STALE-RELEASE",
        lease_key="submit:STALE-RELEASE",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=reconcile_at,
        reason="submission_interrupted",
    )["updated"] is True

    rejected = store.release_reservation(
        "reservation-STALE-RELEASE",
        lease_key="submit:STALE-RELEASE",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=reconcile_at,
        reason="negative full snapshot",
        broker_absence_evidence={
            "snapshot_complete": True,
            # This is one microsecond before reconciliation, expressed in a
            # different timezone, while still well inside the freshness limit.
            "observed_at": "2026-08-21T14:00:10.499999+02:00",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
            "fill_order_ids": [],
        },
    )

    assert rejected == {
        "updated": False,
        "reason": "broker_absence_evidence_invalid",
    }
    assert store.active_reservations()[0]["status"] == "RECONCILE_REQUIRED"


def test_reservation_transition_time_cannot_move_backwards(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("MONOTONIC-TRANSITION")
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:MONOTONIC-TRANSITION", "worker", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:MONOTONIC-TRANSITION",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )["allowed"] is True

    later = datetime(2026, 8, 21, 12, 0, 20, tzinfo=timezone.utc)
    earlier = datetime(2026, 8, 21, 12, 0, 10, tzinfo=timezone.utc)
    assert store.mark_reservation_reconcile_required(
        "reservation-MONOTONIC-TRANSITION",
        lease_key="submit:MONOTONIC-TRANSITION",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=later,
        reason="first_reconcile",
    )["updated"] is True

    regressed = store.mark_reservation_reconcile_required(
        "reservation-MONOTONIC-TRANSITION",
        lease_key="submit:MONOTONIC-TRANSITION",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=earlier,
        reason="backdated_reconcile",
    )
    assert regressed == {
        "updated": False,
        "reason": "reservation_time_regression",
    }

    release = store.release_reservation(
        "reservation-MONOTONIC-TRANSITION",
        lease_key="submit:MONOTONIC-TRANSITION",
        owner_token="worker",
        fence_token=lease["fence_token"],
        now=earlier,
        reason="backdated negative snapshot",
        broker_absence_evidence={
            "snapshot_complete": True,
            "observed_at": "2026-08-21T12:00:10Z",
            "account": "DU1",
            "con_id": 101,
            "position_open": False,
            "open_order_ids": [],
            "fill_order_ids": [],
        },
    )
    assert release == {
        "updated": False,
        "reason": "reservation_time_regression",
    }
    assert store.active_reservations()[0]["status"] == "RECONCILE_REQUIRED"


def test_atomic_reserve_allows_cap_equality_and_blocks_only_strict_excess(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    store.initialize()
    at_cap = _intent(
        "CAP",
        parent_order_id=2001,
        child_order_id=2101,
        quantity=750.0 / 5.5,
    )
    excess = _intent(
        "EXCESS",
        con_id=202,
        parent_order_id=3001,
        child_order_id=3101,
        quantity=0.002,
    )
    # This case isolates total/direction equality.  A verified TECH group has
    # its own stricter 0.50%-cap and would otherwise invalidate the fixture.
    at_cap["group_verified"] = False
    excess["group_verified"] = False
    _register_intent_with_orders(store, at_cap)
    _register_intent_with_orders(store, excess)
    lease = store.acquire_lease("submit:CAP", "worker-cap", now=_NOW, ttl_seconds=30)

    equal = store.reserve_if_allowed(
        _reservation(at_cap),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:CAP",
        owner_token="worker-cap",
        fence_token=lease["fence_token"],
    )
    excess_lease = store.acquire_lease(
        "submit:EXCESS", "worker-excess", now=_NOW, ttl_seconds=30
    )
    strictly_over = store.reserve_if_allowed(
        _reservation(excess),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:EXCESS",
        owner_token="worker-excess",
        fence_token=excess_lease["fence_token"],
    )

    assert equal["allowed"] is True
    assert equal["decision"] == "reserved"
    assert equal["risk"]["projected_total_risk_pct"] == 0.75
    assert strictly_over["allowed"] is False
    assert strictly_over["decision"] == "risk_blocked"
    assert strictly_over["risk"]["reasons"] == [
        "max_total_risk_exceeded",
        "max_direction_risk_exceeded",
    ]
    assert [item["reservation_id"] for item in store.active_reservations(now=_NOW)] == [
        "reservation-CAP"
    ]


@pytest.mark.parametrize(
    ("candidate_quantity", "expected_allowed"),
    [(897.0 / 100.5, True), (897.0 / 100.5 + 0.001, False)],
)
def test_notional_cap_uses_nonzero_gross_then_adds_pending_and_residual_once(
    tmp_path, candidate_quantity, expected_allowed
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    existing = _intent("NOTIONAL-OPEN", quantity=10)
    candidate = _intent(
        "NOTIONAL-CANDIDATE",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
        quantity=candidate_quantity,
    )
    _register_intent_with_orders(store, existing)
    _register_intent_with_orders(store, candidate)
    existing_lease = store.acquire_lease(
        "submit:NOTIONAL-OPEN", "existing", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(existing),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        gross_position_value=0,
        max_total_exposure_pct=20,
        max_positions=3,
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:NOTIONAL-OPEN",
        owner_token="existing",
        fence_token=existing_lease["fence_token"],
    )["allowed"] is True
    candidate_lease = store.acquire_lease(
        "submit:NOTIONAL-CANDIDATE", "candidate", now=_NOW, ttl_seconds=30
    )
    parent = _observed_order(
        _order_mapping(existing, 1001, role="PARENT", branch=1),
        remaining=3,
        filled=7,
    )
    stop = _observed_order(
        _order_mapping(
            existing, 1101, role="STOP", branch=1, parent_order_id=1001
        ),
        remaining=10,
        filled=0,
    )

    result = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000,
        positions=[
            {
                "account": "DU1",
                "con_id": 101,
                "ticker": "OPEN",
                "quantity": 4,
                "avg_cost": 100,
            }
        ],
        orders=[parent, stop],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=500,
        max_total_exposure_pct=2,
        max_positions=2,
        now=_NOW,
        lease_key="submit:NOTIONAL-CANDIDATE",
        owner_token="candidate",
        fence_token=candidate_lease["fence_token"],
    )

    assert result["allowed"] is expected_allowed
    assert result["risk"]["position_notional_usd"] == pytest.approx(400)
    assert result["risk"]["gross_position_value"] == pytest.approx(500)
    assert result["risk"]["position_exposure_component_usd"] == pytest.approx(500)
    assert result["risk"]["non_position_committed_notional_usd"] == pytest.approx(603)
    assert result["risk"]["candidate_notional_usd"] == pytest.approx(
        candidate_quantity * 100.5
    )
    assert result["risk"]["projected_total_exposure_usd"] == pytest.approx(
        1_103 + candidate_quantity * 100.5
    )
    if expected_allowed:
        assert result["risk"]["projected_total_exposure_usd"] == pytest.approx(2_000)
    else:
        assert "max_total_exposure_exceeded" in result["risk"]["reasons"]


@pytest.mark.parametrize(
    ("quantity", "expected_allowed"),
    [(0.3 / 0.101, True), (0.3 / 0.101 + 0.0001, False)],
)
def test_notional_decimal_cap_blocks_only_real_strict_excess(
    tmp_path, quantity, expected_allowed
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(
        "DECIMAL-NOTIONAL",
        quantity=quantity,
        entry=0.1,
        stop=0.09,
    )
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:DECIMAL-NOTIONAL", "worker", now=_NOW, ttl_seconds=30
    )

    result = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=30,
        positions=[],
        orders=[],
        policy={
            **DEFAULT_RISK_POLICY,
            "max_total_risk_pct": 1.0,
            "max_direction_risk_pct": 1.0,
        },
        gross_position_value=0,
        max_total_exposure_pct=1,
        max_positions=3,
        now=_NOW,
        lease_key="submit:DECIMAL-NOTIONAL",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is expected_allowed
    if not expected_allowed:
        assert "max_total_exposure_exceeded" in result["risk"]["reasons"]


@pytest.mark.parametrize("gross_value", [None, float("nan"), -1.0])
def test_explicit_invalid_gross_position_value_fails_closed(tmp_path, gross_value):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("INVALID-GROSS", quantity=0.1)
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:INVALID-GROSS", "worker", now=_NOW, ttl_seconds=30
    )

    result = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=gross_value,
        max_total_exposure_pct=20,
        max_positions=3,
        now=_NOW,
        lease_key="submit:INVALID-GROSS",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["risk"]["reasons"]


@pytest.mark.parametrize("missing", ["gross", "snapshot_completeness"])
def test_each_missing_capacity_input_independently_fails_closed(tmp_path, missing):
    store = _TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(f"MISSING-{missing.upper()}", quantity=0.1)
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        f"submit:{intent['setup_id']}", "worker", now=_NOW, ttl_seconds=30
    )
    kwargs = {
        "reservation": _reservation(intent),
        "net_liquidation": 100_000,
        "available_funds": 100_000,
        "min_available_funds": 0,
        "positions": [],
        "orders": [],
        "policy": DEFAULT_RISK_POLICY,
        "max_total_exposure_pct": 20,
        "max_positions": 3,
        "now": _NOW,
        "lease_key": f"submit:{intent['setup_id']}",
        "owner_token": "worker",
        "fence_token": lease["fence_token"],
    }
    if missing == "gross":
        kwargs["orders_snapshot_complete"] = True
    else:
        kwargs["gross_position_value"] = 0

    result = store.reserve_if_allowed(**kwargs)

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["risk"]["reasons"]


def test_gross_and_position_snapshot_presence_must_be_consistent(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    candidate = _intent(
        "GROSS-POSITION-CONSISTENCY",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
        quantity=0.1,
    )
    _register_intent_with_orders(store, candidate)
    lease = store.acquire_lease(
        "submit:GROSS-POSITION-CONSISTENCY", "worker", now=_NOW, ttl_seconds=30
    )

    unknown_slots = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=1_000,
        max_total_exposure_pct=20,
        max_positions=3,
        now=_NOW,
        lease_key="submit:GROSS-POSITION-CONSISTENCY",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert unknown_slots["allowed"] is False
    assert "risk_state_unresolved" in unknown_slots["risk"]["reasons"]


def test_nonzero_position_with_explicit_zero_gross_fails_closed(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    existing = _intent("POSITION-WITH-ZERO-GROSS")
    candidate = _intent(
        "AFTER-ZERO-GROSS",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
        quantity=0.1,
    )
    _register_intent_with_orders(store, existing)
    _register_intent_with_orders(store, candidate)
    lease = store.acquire_lease(
        "submit:AFTER-ZERO-GROSS", "worker", now=_NOW, ttl_seconds=30
    )
    stop = {
        "account": "DU1",
        "con_id": 101,
        "order_id": 1101,
        "parent_id": 1001,
        "order_ref": "AS2-POSITION-WITH-ZERO-GROSS-S1",
        "action": "SELL",
        "order_type": "STP",
        "quantity": 10,
        "remaining": 10,
        "filled": 0,
        "stop_price": 95,
        "status": "Submitted",
    }

    result = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000,
        positions=[
            {
                "account": "DU1",
                "con_id": 101,
                "ticker": "OPEN",
                "quantity": 10,
                "avg_cost": 100,
            }
        ],
        orders=[stop],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0,
        max_total_exposure_pct=20,
        max_positions=3,
        now=_NOW,
        lease_key="submit:AFTER-ZERO-GROSS",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["risk"]["reasons"]


@pytest.mark.parametrize("missing_field", ["positions", "orders"])
def test_missing_broker_snapshot_fails_closed(tmp_path, missing_field):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("NO-SNAPSHOT")
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:NO-SNAPSHOT", "worker", now=_NOW, ttl_seconds=30
    )
    snapshots = {"positions": [], "orders": []}
    snapshots[missing_field] = None

    result = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=snapshots["positions"],
        orders=snapshots["orders"],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:NO-SNAPSHOT",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert result["decision"] == "risk_blocked"
    assert "risk_state_unresolved" in result["risk"]["reasons"]
    assert store.active_reservations() == []


def test_one_shot_snapshot_iterators_are_materialized_once_without_losing_risk(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    existing = _intent("OPEN", parent_order_id=6001, child_order_id=6101)
    candidate = _intent("NEXT", parent_order_id=7001, child_order_id=7101)
    _register_intent_with_orders(store, existing)
    _register_intent_with_orders(store, candidate)
    lease = store.acquire_lease("submit:NEXT", "worker", now=_NOW, ttl_seconds=30)
    positions = (
        item
        for item in [
            {
                "account": "DU1",
                "con_id": 101,
                "ticker": "XYZ",
                "quantity": 10,
                "avg_cost": 100.0,
            }
        ]
    )

    result = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=100_000.0,
        positions=positions,
        orders=iter([]),
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:NEXT",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["risk"]["reasons"]


@pytest.mark.parametrize("bad_status", ["RELEASED", "COMPLETED", "anything"])
def test_initial_reservation_must_be_submitting(tmp_path, bad_status):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("BAD-STATUS")
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:BAD-STATUS", "worker", now=_NOW, ttl_seconds=30
    )
    reservation = _reservation(intent)
    reservation["status"] = bad_status

    result = store.reserve_if_allowed(
        reservation,
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:BAD-STATUS",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert store.active_reservations() == []


def test_reservation_risk_identity_is_bound_to_immutable_intent(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("BOUND", quantity=100)
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease("submit:BOUND", "worker", now=_NOW, ttl_seconds=30)
    forged = _reservation(intent)
    forged.update(
        {
            "quantity": 1,
            "entry": 99,
            "stop": 98,
            "group_key": "OTHER",
            "group_verified": False,
        }
    )

    result = store.reserve_if_allowed(
        forged,
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:BOUND",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert result["decision"] == "risk_blocked"
    assert store.active_reservations() == []


def test_second_reservation_id_for_same_setup_is_not_another_authorization(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("ONE-AUTH")
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease("submit:ONE-AUTH", "worker", now=_NOW, ttl_seconds=30)
    first = store.reserve_if_allowed(
        _reservation(intent, "first-id"),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:ONE-AUTH",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )
    second = store.reserve_if_allowed(
        _reservation(intent, "second-id"),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key="submit:ONE-AUTH",
        owner_token="worker",
        fence_token=lease["fence_token"],
    )

    assert first["allowed"] is True
    assert second["allowed"] is False
    assert second["decision"] == "already_reserved"
    assert len(store.active_reservations()) == 1


def test_spawned_atomic_reserve_allows_exactly_one_half_percent_candidate(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    store.initialize()
    first = _intent(
        "RACE-A",
        parent_order_id=4001,
        child_order_id=4101,
        quantity=500.0 / 5.5,
    )
    second = _intent(
        "RACE-B",
        parent_order_id=5001,
        child_order_id=5101,
        quantity=500.0 / 5.5,
    )
    _register_intent_with_orders(store, first)
    _register_intent_with_orders(store, second)

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results: Any = context.Queue()
    workers = [
        context.Process(
            target=_reserve_in_spawn_worker,
            args=(str(db_path), intent["setup_id"], _reservation(intent), barrier, results),
        )
        for intent in (first, second)
    ]
    for worker in workers:
        worker.start()
    received = []
    try:
        for _ in workers:
            received.append(results.get(timeout=20))
    except queue.Empty as exc:  # pragma: no cover - failure details below
        raise AssertionError("spawn workers did not return a reservation decision") from exc
    finally:
        for worker in workers:
            worker.join(timeout=20)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("worker_error" not in item for item in received)
    decisions = [item["decision"] for item in received]
    assert sum(decision["allowed"] for decision in decisions) == 1
    blocked = next(decision for decision in decisions if not decision["allowed"])
    assert blocked["decision"] == "risk_blocked"
    assert "max_total_risk_exceeded" in blocked["risk"]["reasons"]
    assert len(TradingRiskStore(db_path).active_reservations(now=_NOW)) == 1


@pytest.mark.parametrize(
    ("quantity", "exposure_pct", "max_positions", "expected_reason"),
    [
        (100, 15.0, 10, "max_total_exposure_exceeded"),
        (1, 100.0, 1, "max_positions_reached"),
    ],
)
def test_spawned_atomic_reserve_applies_notional_and_position_limits_once(
    tmp_path, quantity, exposure_pct, max_positions, expected_reason
):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    store.initialize()
    first = _intent(
        "CAPACITY-A",
        con_id=101,
        parent_order_id=4001,
        child_order_id=4101,
        quantity=quantity,
        stop=99.5,
    )
    second = _intent(
        "CAPACITY-B",
        con_id=202,
        parent_order_id=5001,
        child_order_id=5101,
        quantity=quantity,
        stop=99.5,
    )
    _register_intent_with_orders(store, first)
    _register_intent_with_orders(store, second)

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results: Any = context.Queue()
    workers = [
        context.Process(
            target=_reserve_in_spawn_worker,
            args=(
                str(db_path),
                intent["setup_id"],
                _reservation(intent),
                barrier,
                results,
                exposure_pct,
                max_positions,
            ),
        )
        for intent in (first, second)
    ]
    for worker in workers:
        worker.start()
    received = []
    try:
        for _ in workers:
            received.append(results.get(timeout=20))
    except queue.Empty as exc:  # pragma: no cover - failure details below
        raise AssertionError("spawn workers did not return a capacity decision") from exc
    finally:
        for worker in workers:
            worker.join(timeout=20)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("worker_error" not in item for item in received)
    decisions = [item["decision"] for item in received]
    assert sum(decision["allowed"] for decision in decisions) == 1
    blocked = next(decision for decision in decisions if not decision["allowed"])
    assert expected_reason in blocked["risk"]["reasons"]
    assert len(TradingRiskStore(db_path).active_reservations()) == 1


def test_cash_cap_allows_exact_minimum_reserve_and_persists_cash_requirement(
    tmp_path,
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("CASH-BOUNDARY", quantity=10, entry=100.0, stop=99.5)
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:CASH-BOUNDARY", "cash-worker", now=_NOW, ttl_seconds=30
    )

    result = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        available_funds=1_500.5,
        min_available_funds=500.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        max_total_exposure_pct=100.0,
        max_positions=10,
        now=_NOW,
        lease_key="submit:CASH-BOUNDARY",
        owner_token="cash-worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is True
    assert result["risk"]["available_funds_usd"] == pytest.approx(1_500.5)
    assert result["risk"]["min_available_funds_usd"] == pytest.approx(500.0)
    assert result["risk"]["active_reserved_cash_usd"] == pytest.approx(0.0)
    assert result["risk"]["candidate_notional_usd"] == pytest.approx(1_000.5)
    assert result["risk"]["projected_total_exposure_usd"] == pytest.approx(1_000.5)
    assert result["risk"]["projected_cash_use_usd"] == pytest.approx(1_000.5)
    active = store.active_reservations(now=_NOW)
    assert active[0]["cash_basis_price_usd"] == pytest.approx(100.05)
    assert active[0]["cash_required_usd"] == pytest.approx(1_000.5)


def test_active_reservations_reaggregate_adverse_long_and_short_fill_risk(
    tmp_path,
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent(
        "WORST-LONG",
        con_id=101,
        parent_order_id=4001,
        child_order_id=4101,
        quantity=100,
        entry=10.0,
        stop=9.0,
    )
    first.update(
        {
            "stop_limit": 15.0,
            "tp1": 20.0,
            "tp2": 25.0,
            "group_verified": False,
        }
    )
    second = _intent(
        "WORST-SHORT",
        con_id=202,
        parent_order_id=5001,
        child_order_id=5101,
        quantity=50,
        entry=8.0,
        stop=10.0,
    )
    second.update(
        {
            "direction": "SHORT",
            "stop_limit": 6.0,
            "tp1": 5.0,
            "tp2": 3.0,
            "group_verified": False,
        }
    )
    _register_intent_with_orders(store, first)
    _register_intent_with_orders(store, second)
    first_lease = store.acquire_lease(
        "submit:WORST-LONG", "first", now=_NOW, ttl_seconds=30
    )

    first_result = store.reserve_if_allowed(
        _reservation(first),
        net_liquidation=100_000.0,
        available_funds=100_000.0,
        min_available_funds=0.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        max_total_exposure_pct=100.0,
        max_positions=10,
        now=_NOW,
        lease_key="submit:WORST-LONG",
        owner_token="first",
        fence_token=first_lease["fence_token"],
    )

    assert first_result["allowed"] is True
    assert first_result["risk"]["projected_total_risk_usd"] == pytest.approx(600.0)
    active = store.active_reservations(now=_NOW)
    assert active[0]["risk_basis_price_usd"] == pytest.approx(15.0)
    assert active[0]["risk_per_share_usd"] == pytest.approx(6.0)
    assert active[0]["cash_basis_price_usd"] == pytest.approx(15.0)
    second_lease = store.acquire_lease(
        "submit:WORST-SHORT", "second", now=_NOW, ttl_seconds=30
    )

    second_result = store.reserve_if_allowed(
        _reservation(second),
        net_liquidation=100_000.0,
        available_funds=100_000.0,
        min_available_funds=0.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        max_total_exposure_pct=100.0,
        max_positions=10,
        now=_NOW,
        lease_key="submit:WORST-SHORT",
        owner_token="second",
        fence_token=second_lease["fence_token"],
    )

    assert second_result["allowed"] is False
    assert "max_total_risk_exceeded" in second_result["risk"]["reasons"]
    assert second_result["risk"]["projected_total_risk_usd"] == pytest.approx(800.0)
    assert second_result["risk"]["projected_total_exposure_usd"] == pytest.approx(1_900.0)


@pytest.mark.parametrize(
    ("available_funds", "min_available_funds"),
    [
        (float("nan"), 500.0),
        (-1.0, 0.0),
        (1_000.0, float("nan")),
        (1_000.0, -1.0),
    ],
)
def test_cash_cap_rejects_nonfinite_or_negative_inputs(
    tmp_path, available_funds, min_available_funds
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("CASH-INVALID", quantity=1, entry=100.0, stop=99.5)
    _register_intent_with_orders(store, intent)
    lease = store.acquire_lease(
        "submit:CASH-INVALID", "cash-worker", now=_NOW, ttl_seconds=30
    )

    result = store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        available_funds=available_funds,
        min_available_funds=min_available_funds,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        max_total_exposure_pct=100.0,
        max_positions=10,
        now=_NOW,
        lease_key="submit:CASH-INVALID",
        owner_token="cash-worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert "cash_capacity_unresolved" in result["risk"]["reasons"]
    assert store.active_reservations(now=_NOW) == []


def test_spawned_cash_cap_counts_inflight_reservations_atomically(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    store = TradingRiskStore(db_path)
    store.initialize()
    first = _intent(
        "CASH-RACE-A", con_id=101, parent_order_id=4001,
        child_order_id=4101, quantity=6, stop=99.5,
    )
    second = _intent(
        "CASH-RACE-B", con_id=202, parent_order_id=5001,
        child_order_id=5101, quantity=6, stop=99.5,
    )
    _register_intent_with_orders(store, first)
    _register_intent_with_orders(store, second)

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results: Any = context.Queue()
    workers = [
        context.Process(
            target=_reserve_in_spawn_worker,
            args=(
                str(db_path), intent["setup_id"], _reservation(intent),
                barrier, results, 100.0, 10, 1_500.0, 500.0,
            ),
        )
        for intent in (first, second)
    ]
    for worker in workers:
        worker.start()
    received = []
    try:
        for _ in workers:
            received.append(results.get(timeout=20))
    except queue.Empty as exc:  # pragma: no cover - failure details below
        raise AssertionError("spawn workers did not return a cash decision") from exc
    finally:
        for worker in workers:
            worker.join(timeout=20)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("worker_error" not in item for item in received)
    decisions = [item["decision"] for item in received]
    assert sum(decision["allowed"] for decision in decisions) == 1
    blocked = next(decision for decision in decisions if not decision["allowed"])
    assert "min_cash_reserve_exceeded" in blocked["risk"]["reasons"]
    active = TradingRiskStore(db_path).active_reservations(now=_NOW)
    assert len(active) == 1
    assert active[0]["cash_required_usd"] == pytest.approx(600.3)


def test_atomic_reserve_blocks_a_second_setup_for_the_same_contract(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent(
        "CONTRACT-A", parent_order_id=6001, child_order_id=6101, quantity=1, stop=99.5
    )
    second = _intent(
        "CONTRACT-B", parent_order_id=7001, child_order_id=7101, quantity=1, stop=99.5
    )
    _register_intent_with_orders(store, first)
    _register_intent_with_orders(store, second)
    first_lease = store.acquire_lease(
        "submit:CONTRACT-A", "first", now=_NOW, ttl_seconds=30
    )
    assert store.reserve_if_allowed(
        _reservation(first),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0,
        max_total_exposure_pct=100,
        max_positions=10,
        now=_NOW,
        lease_key="submit:CONTRACT-A",
        owner_token="first",
        fence_token=first_lease["fence_token"],
    )["allowed"] is True
    second_lease = store.acquire_lease(
        "submit:CONTRACT-B", "second", now=_NOW, ttl_seconds=30
    )

    result = store.reserve_if_allowed(
        _reservation(second),
        net_liquidation=100_000,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0,
        max_total_exposure_pct=100,
        max_positions=10,
        now=_NOW,
        lease_key="submit:CONTRACT-B",
        owner_token="second",
        fence_token=second_lease["fence_token"],
    )

    assert result["allowed"] is False
    assert result["risk"]["reasons"] == ["duplicate_contract_committed"]


_REQUIRED_ORDER_GEOMETRY_FIELDS = (
    "action", "order_type", "quantity", "aux_price", "limit_price",
    "oca_group", "oca_type", "tif", "transmit", "outside_rth", "client_id",
)


@pytest.mark.parametrize("missing_field", _REQUIRED_ORDER_GEOMETRY_FIELDS)
def test_intent_order_mapping_requires_complete_authorized_geometry(tmp_path, missing_field):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(f"MISSING-{missing_field}")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    mapping = _order_mapping(intent, 1001, role="PARENT", branch=1)
    mapping.pop(missing_field)

    result = store.register_intent_order(intent["setup_id"], mapping)

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_order_mapping_invalid",
    }


def _reserve_before_mapping(store, intent, owner="observer-worker"):
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    lease_key = f"submit:{intent['setup_id']}"
    lease = store.acquire_lease(lease_key, owner, now=_NOW, ttl_seconds=30)
    assert lease["acquired"] is True
    assert store.reserve_if_allowed(
        _reservation(intent),
        net_liquidation=100_000.0,
        positions=[],
        orders=[],
        policy=DEFAULT_RISK_POLICY,
        now=_NOW,
        lease_key=lease_key,
        owner_token=owner,
        fence_token=lease["fence_token"],
    )["allowed"] is True
    return lease_key, lease


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("order_ref", "AS2-MUTATED-P9"),
        ("parent_id", 77),
        ("action", "SELL"),
        ("order_type", "LMT"),
        ("quantity", 9.0),
        ("aux_price", 101.0),
        ("limit_price", 98.0),
        ("oca_group", "WRONG-OCA"),
        ("oca_type", 1),
        ("tif", "GTC"),
        ("transmit", True),
        ("outside_rth", True),
        ("client_id", 99),
    ],
)
def test_active_exact_id_order_geometry_mutation_is_durable_and_fail_closed(
    tmp_path, field, mutated
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("ACTIVE-GEOMETRY")
    _reserve_before_mapping(store, intent)
    mapping = _order_mapping(intent, 1001, role="PARENT", branch=1)
    assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True
    order = _observed_order(mapping)
    order[field] = mutated
    if field == "aux_price":
        order["stop_price"] = mutated

    result = store.observe_open_orders(
        [order], account="DU1", snapshot_complete=True, positions=[],
        positions_snapshot_complete=True, fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result["accepted"] is False
    assert result["reason"] == "order_geometry_conflict"
    assert result["conflicts"] == [
        {"setup_id": intent["setup_id"], "conflict": "active_order_geometry_mismatch"}
    ]
    replay = store.observe_open_orders(
        [], account="DU1", snapshot_complete=True, positions=[],
        positions_snapshot_complete=True, fills_snapshot_complete=True,
        observed_at=_NOW,
    )
    assert replay["accepted"] is False
    assert replay["conflicts"] == result["conflicts"]


def test_active_mapping_without_nonterminal_reservation_is_durable_and_fail_closed(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("ORPHAN-MAPPING")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    mapping = _order_mapping(intent, 1001, role="PARENT", branch=1)
    assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True

    result = store.observe_open_orders(
        [_observed_order(mapping)], account="DU1", snapshot_complete=True,
        positions=[], positions_snapshot_complete=True, fills_snapshot_complete=True,
        observed_at=_NOW,
    )

    assert result["accepted"] is False
    assert result["conflicts"] == [
        {"setup_id": intent["setup_id"], "conflict": "active_order_without_reservation"}
    ]


def test_perm_id_zero_can_be_enriched_once_but_positive_drift_conflicts(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("PERM-ENRICH")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    mapping = _order_mapping(intent, 1001, role="PARENT", branch=1)
    mapping["perm_id"] = 0
    assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True

    first = store.register_intent_order(intent["setup_id"], {**mapping, "perm_id": 9001})
    drift = store.register_intent_order(intent["setup_id"], {**mapping, "perm_id": 9002})

    assert first == {"accepted": True, "idempotent": True, "conflict": None}
    assert drift["accepted"] is False
    assert drift["conflict"] == "intent_order_mapping_conflict"


def test_broker_visible_requires_complete_geometry_positive_perm_and_ack(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = {**_intent("BROKER-ACK"), "tp1": 110.0, "tp2": 120.0,
              "stop_limit": 100.5, "allocations": [10]}
    lease_key, lease = _reserve_before_mapping(store, intent)
    parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    stop = _order_mapping(intent, 1101, role="STOP", branch=1, parent_order_id=1001)
    target = _order_mapping(intent, 1201, role="TARGET", branch=1, parent_order_id=1001)
    for mapping in (parent, stop):
        assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True
    missing = store.mark_reservation_broker_visible(
        "reservation-BROKER-ACK", [1001, 1101], lease_key=lease_key,
        owner_token="observer-worker", fence_token=lease["fence_token"], now=_NOW,
        broker_order_evidence=[_observed_order(parent), _observed_order(stop)],
    )
    assert missing == {"updated": False, "reason": "broker_order_geometry_incomplete"}
    assert store.register_intent_order(intent["setup_id"], target)["accepted"] is True
    no_ack = store.mark_reservation_broker_visible(
        "reservation-BROKER-ACK", [1001, 1101, 1201], lease_key=lease_key,
        owner_token="observer-worker", fence_token=lease["fence_token"], now=_NOW,
        broker_order_evidence=[
            _observed_order({**mapping, "perm_id": 0}, status="PendingSubmit")
            for mapping in (parent, stop, target)
        ],
    )
    assert no_ack == {"updated": False, "reason": "broker_ack_evidence_invalid"}


@pytest.mark.parametrize("kind", ["identical", "conflicting", "ref_namespace"])
def test_duplicate_order_snapshot_is_rejected_before_persistence(tmp_path, kind):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("DUP-ORDER")
    _reserve_before_mapping(store, intent)
    mapping = _order_mapping(intent, 1001, role="PARENT", branch=1)
    assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True
    first = _observed_order(mapping)
    second = (dict(first) if kind == "identical" else
              {**first, "action": "SELL"} if kind == "conflicting" else
              {**first, "order_id": 9999})
    result = store.observe_open_orders(
        [first, second], account="DU1", snapshot_complete=True, positions=[],
        positions_snapshot_complete=True, fills_snapshot_complete=True, observed_at=_NOW,
    )
    assert result["accepted"] is False
    assert result["reason"] == "orders_snapshot_duplicate"
    assert result["conflicts"] == []


def test_duplicate_position_snapshot_is_rejected_before_persistence(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    position = {"account": "DU1", "con_id": 101, "quantity": 1, "avg_cost": 100}
    result = store.observe_open_orders(
        [], account="DU1", snapshot_complete=True,
        positions=[position, dict(position)], positions_snapshot_complete=True,
        fills_snapshot_complete=True, observed_at=_NOW,
    )
    assert result["accepted"] is False
    assert result["reason"] == "positions_snapshot_duplicate"


def test_terminal_ref_fallback_is_accountwide_and_con_id_drift_is_durable(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _prepared_released_setup(store, "REF-CON-DRIFT")
    mapping = _order_mapping(intent, 1001, role="PARENT", branch=1)
    result = store.observe_open_orders(
        [_observed_order(mapping, con_id=9999, order_id=7777)], account="DU1",
        snapshot_complete=True, positions=[], positions_snapshot_complete=True,
        fills_snapshot_complete=True, observed_at=_NOW,
    )
    assert result["accepted"] is True
    assert result["conflicts"] == [{
        "setup_id": intent["setup_id"],
        "conflict": "terminal_parent_order_identity_mismatch_after_release",
    }]


def test_complete_terminal_evidence_rejects_unmapped_open_order_id(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-UNMAPPED-OPEN"
    )
    result = store.record_outcome(
        outcome, reservation_id=f"reservation-{intent['setup_id']}",
        lease_key=lease_key, owner_token="outcome-worker",
        fence_token=lease["fence_token"], now=_NOW,
        terminal_evidence={
            "snapshot_complete": True, "observed_at": _NOW.isoformat(),
            "account": intent["account"], "con_id": intent["con_id"],
            "position_open": False, "open_order_ids": [9999],
        },
    )
    assert result["accepted"] is False
    assert result["conflict"] == "outcome_terminal_evidence_invalid"


@pytest.mark.parametrize(
    ("open_order_ids", "accepted"),
    [([2001], True), ([2001, 2001], False)],
)
def test_complete_terminal_evidence_validates_other_active_setup_order_ids(
    tmp_path, open_order_ids, accepted
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, "COMPLETE-WITH-OTHER"
    )
    other = _intent("OTHER-ACTIVE", con_id=202, parent_order_id=2001, child_order_id=2101)
    other_lease_key, other_lease = _reserve_before_mapping(
        store, other, owner="other-worker"
    )
    other_mappings = [
        _order_mapping(other, 2001, role="PARENT", branch=1),
        _order_mapping(other, 2101, role="STOP", branch=1, parent_order_id=2001),
        _order_mapping(other, 2201, role="TARGET", branch=1, parent_order_id=2001),
    ]
    for mapping in other_mappings:
        assert store.register_intent_order(other["setup_id"], mapping)["accepted"] is True
    other_orders = [_observed_order(mapping) for mapping in other_mappings]
    assert store.mark_reservation_broker_visible(
        "reservation-OTHER-ACTIVE", [2001, 2101, 2201],
        lease_key=other_lease_key, owner_token="other-worker",
        fence_token=other_lease["fence_token"], now=_NOW,
        broker_order_evidence=other_orders,
    )["updated"] is True
    other_order = other_orders[0]
    result = store.record_outcome(
        outcome, reservation_id=f"reservation-{intent['setup_id']}",
        lease_key=lease_key, owner_token="outcome-worker",
        fence_token=lease["fence_token"], now=_NOW,
        terminal_evidence={
            "snapshot_complete": True, "observed_at": _NOW.isoformat(),
            "account": intent["account"], "con_id": intent["con_id"],
            "position_open": False, "open_order_ids": open_order_ids,
            "open_orders": [other_order],
        },
    )
    assert result["accepted"] is accepted
    if not accepted:
        assert result["conflict"] == "outcome_terminal_evidence_invalid"
        return

    assert result["transition"] == "completed"
    with sqlite3.connect(store.db_path) as connection:
        evidence_json = connection.execute(
            "SELECT evidence_json FROM trading_risk_terminal_evidence WHERE setup_id=?",
            (intent["setup_id"],),
        ).fetchone()[0]
    persisted_order = json.loads(evidence_json)["open_orders"][0]
    assert persisted_order["status"] == "SUBMITTED"
    assert persisted_order["remaining"] == pytest.approx(other_order["remaining"])


@pytest.mark.parametrize(
    ("role", "branch", "field", "value"),
    [
        ("PARENT", 2, None, None),
        ("PARENT", 1, "aux_price", 101.0),
        ("PARENT", 1, "limit_price", 102.0),
        ("STOP", 1, "aux_price", 94.0),
        ("TARGET", 1, "limit_price", 111.0),
    ],
)
def test_order_mapping_rejects_out_of_range_branch_and_wrong_role_price(
    tmp_path, role, branch, field, value
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("BRANCH-PRICE")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    if role != "PARENT":
        assert store.register_intent_order(intent["setup_id"], parent)["accepted"] is True
    mapping = _order_mapping(
        intent,
        {"PARENT": 1001, "STOP": 1101, "TARGET": 1201}[role],
        role=role,
        branch=branch,
        parent_order_id=0 if role == "PARENT" else 1001,
    )
    if field is not None:
        mapping[field] = value

    result = store.register_intent_order(intent["setup_id"], mapping)

    assert result["accepted"] is False
    assert result["conflict"] == "intent_order_mapping_invalid"


@pytest.mark.parametrize("duplicate_kind", ["order_id", "perm_id"])
def test_broker_visible_rejects_duplicate_ack_identity(tmp_path, duplicate_kind):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("DUPLICATE-ACK")
    lease_key, lease = _reserve_before_mapping(store, intent)
    mappings = [
        _order_mapping(intent, 1001, role="PARENT", branch=1),
        _order_mapping(intent, 1101, role="STOP", branch=1, parent_order_id=1001),
        _order_mapping(intent, 1201, role="TARGET", branch=1, parent_order_id=1001),
    ]
    for mapping in mappings:
        assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True
    evidence = [_observed_order(mapping) for mapping in mappings]
    if duplicate_kind == "order_id":
        evidence[1]["order_id"] = evidence[0]["order_id"]
    else:
        evidence[1]["perm_id"] = evidence[0]["perm_id"]

    result = store.mark_reservation_broker_visible(
        f"reservation-{intent['setup_id']}",
        [mapping["order_id"] for mapping in mappings],
        lease_key=lease_key,
        owner_token="observer-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        broker_order_evidence=evidence,
    )

    assert result == {"updated": False, "reason": "broker_ack_evidence_invalid"}


@pytest.mark.parametrize("unused_sentinel", [0.0, 1.7976931348623157e308])
def test_terminal_evidence_canonicalizes_unused_broker_price_sentinels(
    tmp_path, unused_sentinel
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent, lease_key, lease, outcome = _prepared_terminal_outcome(
        store, f"CANONICAL-UNUSED-{str(unused_sentinel)[0]}"
    )
    other = _intent(
        f"OTHER-UNUSED-{str(unused_sentinel)[0]}",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    other_lease_key, other_lease = _reserve_before_mapping(
        store, other, owner="other-worker"
    )
    mappings = [
        _order_mapping(other, 2001, role="PARENT", branch=1),
        _order_mapping(other, 2101, role="STOP", branch=1, parent_order_id=2001),
        _order_mapping(other, 2201, role="TARGET", branch=1, parent_order_id=2001),
    ]
    for mapping in mappings:
        assert store.register_intent_order(other["setup_id"], mapping)["accepted"] is True
    evidence = [_observed_order(mapping) for mapping in mappings]
    assert store.mark_reservation_broker_visible(
        f"reservation-{other['setup_id']}",
        [mapping["order_id"] for mapping in mappings],
        lease_key=other_lease_key,
        owner_token="other-worker",
        fence_token=other_lease["fence_token"],
        now=_NOW,
        broker_order_evidence=evidence,
    )["updated"] is True
    observed_stop = {**evidence[1], "limit_price": unused_sentinel}

    result = store.record_outcome(
        outcome,
        reservation_id=f"reservation-{intent['setup_id']}",
        lease_key=lease_key,
        owner_token="outcome-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        terminal_evidence={
            "snapshot_complete": True,
            "observed_at": _NOW.isoformat(),
            "account": intent["account"],
            "con_id": intent["con_id"],
            "position_open": False,
            "open_order_ids": [observed_stop["order_id"]],
            "open_orders": [observed_stop],
        },
    )

    assert result["accepted"] is True
    with sqlite3.connect(store.db_path) as connection:
        evidence_json = connection.execute(
            "SELECT evidence_json FROM trading_risk_terminal_evidence WHERE setup_id=?",
            (intent["setup_id"],),
        ).fetchone()[0]
    persisted_order = json.loads(evidence_json)["open_orders"][0]
    assert persisted_order["limit_price"] is None


def test_first_reserve_call_blocks_geometry_conflict_observed_in_same_transaction(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    active = _intent("ACTIVE-SAME-CALL")
    active_lease_key, active_lease = _reserve_before_mapping(
        store, active, owner="active-worker"
    )
    active_mappings = [
        _order_mapping(active, 1001, role="PARENT", branch=1),
        _order_mapping(active, 1101, role="STOP", branch=1, parent_order_id=1001),
        _order_mapping(active, 1201, role="TARGET", branch=1, parent_order_id=1001),
    ]
    for mapping in active_mappings:
        assert store.register_intent_order(active["setup_id"], mapping)["accepted"] is True
    active_orders = [_observed_order(mapping) for mapping in active_mappings]
    assert store.mark_reservation_broker_visible(
        f"reservation-{active['setup_id']}",
        [mapping["order_id"] for mapping in active_mappings],
        lease_key=active_lease_key,
        owner_token="active-worker",
        fence_token=active_lease["fence_token"],
        now=_NOW,
        broker_order_evidence=active_orders,
    )["updated"] is True
    active_orders[0]["client_id"] = 99

    candidate = _intent(
        "CANDIDATE-SAME-CALL",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    assert store.register_intent(_immutable_intent(candidate))["accepted"] is True
    lease_key = f"submit:{candidate['setup_id']}"
    lease = store.acquire_lease(lease_key, "candidate-worker", now=_NOW, ttl_seconds=30)

    result = store.reserve_if_allowed(
        _reservation(candidate),
        net_liquidation=1_000_000.0,
        positions=[],
        orders=active_orders,
        policy=DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        max_total_exposure_pct=100.0,
        max_positions=10,
        orders_snapshot_complete=True,
        now=_NOW,
        lease_key=lease_key,
        owner_token="candidate-worker",
        fence_token=lease["fence_token"],
    )

    assert result["allowed"] is False
    assert "risk_state_unresolved" in result["risk"]["reasons"]
    replay = store.observe_open_orders(
        [],
        account="DU1",
        snapshot_complete=True,
        positions=[],
        positions_snapshot_complete=True,
        fills_snapshot_complete=True,
        observed_at=_NOW,
    )
    assert {
        "setup_id": active["setup_id"],
        "conflict": "active_order_geometry_mismatch",
    } in replay["conflicts"]


def test_fill_conflict_blocks_primary_and_incoming_accounts_only(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    primary = _intent("FILL-PRIMARY", account="DU1", con_id=101)
    incoming = _intent(
        "FILL-INCOMING",
        account="DU2",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    unrelated = _intent(
        "FILL-UNRELATED",
        account="DU3",
        con_id=303,
        parent_order_id=3001,
        child_order_id=3101,
    )
    for intent in (primary, incoming):
        _register_intent_with_orders(store, intent)
    assert store.register_intent(_immutable_intent(unrelated))["accepted"] is True
    assert store.append_fill(
        _fill(
            "CROSS-ACCOUNT-EXEC",
            "BUY",
            account=primary["account"],
            con_id=primary["con_id"],
            order_id=primary["order_ids"][0],
            price=100.0,
        )
    )["accepted"] is True
    conflict = store.append_fill(
        _fill(
            "CROSS-ACCOUNT-EXEC",
            "BUY",
            account=incoming["account"],
            con_id=incoming["con_id"],
            order_id=incoming["order_ids"][0],
            price=101.0,
        )
    )
    assert conflict["conflict"] == "exec_id_payload_conflict"

    def admit(intent, owner):
        lease_key = f"submit:{intent['setup_id']}"
        lease = store.acquire_lease(lease_key, owner, now=_NOW, ttl_seconds=30)
        return store.reserve_if_allowed(
            _reservation(intent),
            net_liquidation=1_000_000.0,
            positions=[],
            orders=[],
            policy=DEFAULT_RISK_POLICY,
            gross_position_value=0.0,
            max_total_exposure_pct=100.0,
            max_positions=10,
            orders_snapshot_complete=True,
            now=_NOW,
            lease_key=lease_key,
            owner_token=owner,
            fence_token=lease["fence_token"],
        )

    primary_result = admit(primary, "primary-worker")
    incoming_result = admit(incoming, "incoming-worker")
    unrelated_result = admit(unrelated, "unrelated-worker")

    assert primary_result["allowed"] is False
    assert incoming_result["allowed"] is False
    assert unrelated_result["allowed"] is True


def test_register_intent_rejects_accountwide_base_order_ref_collision(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    first = _intent("REF-FIRST", account="DU1", con_id=101)
    second = _intent("REF-SECOND", account="DU1", con_id=202)
    second["order_ref"] = first["order_ref"]

    assert store.register_intent(_immutable_intent(first))["accepted"] is True
    result = store.register_intent(_immutable_intent(second))

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_identity_conflict",
    }
    assert store.load_intent(second["setup_id"]) is None


def test_register_mapping_rejects_duplicate_positive_perm_id(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("DUPLICATE-PERM-REGISTER")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    stop = _order_mapping(intent, 1101, role="STOP", branch=1, parent_order_id=1001)
    stop["perm_id"] = parent["perm_id"]
    assert store.register_intent_order(intent["setup_id"], parent)["accepted"] is True

    result = store.register_intent_order(intent["setup_id"], stop)

    assert result == {
        "accepted": False,
        "idempotent": False,
        "conflict": "intent_order_mapping_conflict",
    }
    assert store.intent_order_ids(intent["setup_id"]) == [parent["order_id"]]


def test_broker_visible_persists_canonical_ack_evidence_and_hash(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("PERSIST-ACK")
    lease_key, lease = _reserve_before_mapping(store, intent)
    mappings = [
        _order_mapping(intent, 1001, role="PARENT", branch=1),
        _order_mapping(intent, 1101, role="STOP", branch=1, parent_order_id=1001),
        _order_mapping(intent, 1201, role="TARGET", branch=1, parent_order_id=1001),
    ]
    for mapping in mappings:
        assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True
    evidence = [_observed_order(mapping) for mapping in mappings]
    evidence[1]["limit_price"] = 1.7976931348623157e308
    evidence[2]["aux_price"] = 0.0

    result = store.mark_reservation_broker_visible(
        f"reservation-{intent['setup_id']}",
        [mapping["order_id"] for mapping in mappings],
        lease_key=lease_key,
        owner_token="observer-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        broker_order_evidence=evidence,
    )

    assert result["updated"] is True
    with sqlite3.connect(store.db_path) as connection:
        reservation_json = connection.execute(
            "SELECT reservation_json FROM trading_risk_reservations WHERE setup_id=?",
            (intent["setup_id"],),
        ).fetchone()[0]
    persisted = json.loads(reservation_json)
    ack = persisted["broker_ack_evidence"]
    assert ack["observed_at"] == _NOW.isoformat()
    assert [(order["role"], order["branch"]) for order in ack["orders"]] == [
        ("PARENT", 1),
        ("STOP", 1),
        ("TARGET", 1),
    ]
    assert all(order["status"] == "SUBMITTED" for order in ack["orders"])
    assert ack["orders"][1]["limit_price"] is None
    assert ack["orders"][2]["aux_price"] is None
    encoded = json.dumps(ack, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert persisted["broker_ack_evidence_hash"] == hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("perm_id", 0),
        ("perm_id", -1),
        ("perm_id", 6001.5),
        ("client_id", -1),
        ("client_id", 7.5),
    ],
)
def test_fill_rejects_nonpositive_perm_or_invalid_client_identity(
    tmp_path, field, value
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    fill = _fill("INVALID-BROKER-IDENTITY", "BUY")
    fill[field] = value

    result = store.append_fill(fill)

    assert result["accepted"] is False
    assert result["conflict"] == "fill_invalid"


@pytest.mark.parametrize(
    ("identity_override", "wrong_value"),
    [
        ("perm_id", 9001),
        ("client_id", 8),
    ],
)
def test_fill_mapping_requires_matching_positive_perm_and_client_id(
    tmp_path, identity_override, wrong_value
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(f"FILL-IDENTITY-{identity_override.upper()}")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    assert store.register_intent_order(intent["setup_id"], parent)["accepted"] is True
    fill = _fill("WRONG-BROKER-IDENTITY", "BUY")
    fill[identity_override] = wrong_value

    result = store.append_fill(fill)

    assert result["accepted"] is True
    assert result["mapping_pending"] is True
    assert store.fill_evidence(intent["setup_id"])["fills"] == []


@pytest.mark.parametrize(
    ("field", "legacy_value"),
    [("perm_id", 6001.5), ("client_id", 7.5)],
)
def test_fill_mapping_does_not_coerce_corrupt_legacy_broker_identity(
    tmp_path, field, legacy_value
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent(f"NO-COERCE-{field.upper()}")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    assert store.register_intent_order(intent["setup_id"], parent)["accepted"] is True
    corrupt = {**parent, field: legacy_value}
    encoded = json.dumps(
        corrupt, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            UPDATE trading_risk_intent_orders SET mapping_json=?, mapping_hash=?
            WHERE setup_id=? AND role='PARENT'
            """,
            (
                encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                intent["setup_id"],
            ),
        )

    result = store.append_fill(_fill("NO-COERCION", "BUY"))

    assert result["accepted"] is True
    assert result["mapping_pending"] is True
    assert store.fill_evidence(intent["setup_id"])["fills"] == []


def test_early_pending_fill_attaches_only_after_exact_perm_enrichment(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    intent = _intent("EARLY-EXACT-FILL")
    assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    fill = _fill("EARLY-EXACT", "BUY", perm_id=9001, client_id=7)

    pending = store.append_fill(fill)
    parent = _order_mapping(intent, 1001, role="PARENT", branch=1)
    zero_perm_mapping = {**parent, "perm_id": 0}
    zero_registered = store.register_intent_order(
        intent["setup_id"], zero_perm_mapping
    )

    assert pending["mapping_pending"] is True
    assert zero_registered["accepted"] is True
    assert store.fill_evidence(intent["setup_id"])["fills"] == []

    enriched = store.register_intent_order(
        intent["setup_id"], {**parent, "perm_id": 9001}
    )

    assert enriched == {"accepted": True, "idempotent": True, "conflict": None}
    fills = store.fill_evidence(intent["setup_id"])["fills"]
    assert [item["exec_id"] for item in fills] == ["EARLY-EXACT"]


def test_mapping_attachment_signals_exact_pending_fill_conflict(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    primary = _intent("CONFLICT-PRIMARY", account="DU1", con_id=101)
    incoming = _intent(
        "CONFLICT-INCOMING",
        account="DU2",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    assert store.register_intent(_immutable_intent(primary))["accepted"] is True
    assert store.register_intent(_immutable_intent(incoming))["accepted"] is True
    assert store.register_intent_order(
        primary["setup_id"],
        _order_mapping(primary, 1001, role="PARENT", branch=1),
    )["accepted"] is True
    assert store.append_fill(_fill("LATE-CONFLICT", "BUY"))["accepted"] is True
    conflict = store.append_fill(
        _fill(
            "LATE-CONFLICT",
            "BUY",
            account="DU2",
            con_id=202,
            order_id=2001,
            price=101.0,
        )
    )
    assert conflict["conflict"] == "exec_id_payload_conflict"

    result = store.register_intent_order(
        incoming["setup_id"],
        _order_mapping(incoming, 2001, role="PARENT", branch=1),
    )

    assert result["accepted"] is True
    assert result["conflict"] == "exec_id_payload_conflict"
    with sqlite3.connect(store.db_path) as connection:
        attached = connection.execute(
            """
            SELECT incoming_setup_id FROM trading_risk_fill_conflicts
            WHERE exec_id='LATE-CONFLICT'
            """
        ).fetchone()
    assert attached[0] == incoming["setup_id"]


def test_mapping_registration_quarantines_malformed_pending_conflict(tmp_path):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    primary = _intent("MALFORMED-CONFLICT-PRIMARY", account="DU1", con_id=101)
    incoming = _intent(
        "MALFORMED-CONFLICT-INCOMING",
        account="DU2",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    for intent in (primary, incoming):
        assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    assert store.register_intent_order(
        primary["setup_id"],
        _order_mapping(primary, 1001, role="PARENT", branch=1),
    )["accepted"] is True
    assert store.append_fill(_fill("MALFORMED-CONFLICT", "BUY"))["accepted"] is True
    assert store.append_fill(
        _fill(
            "MALFORMED-CONFLICT",
            "BUY",
            account="DU2",
            con_id=202,
            order_id=2001,
            price=101.0,
        )
    )["conflict"] == "exec_id_payload_conflict"
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            UPDATE trading_risk_fill_conflicts
            SET payload_json='{', payload_hash=?
            WHERE exec_id='MALFORMED-CONFLICT'
            """,
            ("0" * 64,),
        )

    result = store.register_intent_order(
        incoming["setup_id"],
        _order_mapping(incoming, 2001, role="PARENT", branch=1),
    )

    assert result["accepted"] is True
    assert result["conflict"] == "fill_conflict_payload_invalid"
    with sqlite3.connect(store.db_path) as connection:
        persisted = connection.execute(
            """
            SELECT 1 FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind='fill_conflict_payload_invalid'
            """,
            (incoming["setup_id"],),
        ).fetchone()
    assert persisted is not None


def test_unscoped_malformed_fill_conflict_is_global_but_not_mapping_attributed(
    tmp_path,
):
    store = TradingRiskStore(tmp_path / "risk.sqlite")
    primary = _intent("UNKNOWN-PRIMARY", account="DU1", con_id=101)
    unrelated_mapping_intent = _intent(
        "UNKNOWN-UNRELATED-MAPPING",
        account="DU2",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    for intent in (primary, unrelated_mapping_intent):
        assert store.register_intent(_immutable_intent(intent))["accepted"] is True
    assert store.register_intent_order(
        primary["setup_id"],
        _order_mapping(primary, 1001, role="PARENT", branch=1),
    )["accepted"] is True
    assert store.append_fill(_fill("UNKNOWN-CONFLICT", "BUY"))["accepted"] is True
    assert store.append_fill(
        _fill(
            "UNKNOWN-CONFLICT",
            "BUY",
            account="DU1",
            con_id=999,
            order_id=9999,
            perm_id=14999,
            price=101.0,
        )
    )["conflict"] == "exec_id_payload_conflict"
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            UPDATE trading_risk_fill_conflicts
            SET payload_json='{', payload_hash=?,
                incoming_account=NULL, incoming_con_id=NULL,
                incoming_order_id=NULL, incoming_perm_id=NULL,
                incoming_client_id=NULL
            WHERE exec_id='UNKNOWN-CONFLICT'
            """,
            ("0" * 64,),
        )

    mapping_result = store.register_intent_order(
        unrelated_mapping_intent["setup_id"],
        _order_mapping(
            unrelated_mapping_intent, 2001, role="PARENT", branch=1
        ),
    )

    assert mapping_result == {
        "accepted": True,
        "idempotent": False,
        "conflict": None,
    }
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            """
            SELECT 1 FROM trading_risk_evidence_conflicts
            WHERE setup_id=? AND conflict_kind='fill_conflict_payload_invalid'
            """,
            (unrelated_mapping_intent["setup_id"],),
        ).fetchone() is None

    for account, con_id, parent_id, child_id in (
        ("DU2", 302, 3001, 3101),
        ("DU3", 303, 4001, 4101),
    ):
        candidate = _intent(
            f"UNKNOWN-BLOCK-{account}",
            account=account,
            con_id=con_id,
            parent_order_id=parent_id,
            child_order_id=child_id,
            quantity=0.1,
        )
        assert store.register_intent(_immutable_intent(candidate))["accepted"] is True
        lease_key = f"submit:{candidate['setup_id']}"
        lease = store.acquire_lease(
            lease_key, f"worker-{account}", now=_NOW, ttl_seconds=30
        )
        admission = store.reserve_if_allowed(
            _reservation(candidate),
            net_liquidation=100_000.0,
            positions=[],
            orders=[],
            policy=DEFAULT_RISK_POLICY,
            gross_position_value=0.0,
            max_total_exposure_pct=100.0,
            max_positions=10,
            orders_snapshot_complete=True,
            now=_NOW,
            lease_key=lease_key,
            owner_token=f"worker-{account}",
            fence_token=lease["fence_token"],
        )
        assert admission["allowed"] is False
        assert admission["risk"]["current_unresolved_codes"] == [
            "store_live_input_invalid"
        ]


def _persist_valid_broker_visible_setup(db_path):
    store = TradingRiskStore(db_path)
    intent = _intent("STARTUP-BROKER-VISIBLE")
    lease_key, lease = _reserve_before_mapping(store, intent)
    mappings = [
        _order_mapping(intent, 1001, role="PARENT", branch=1),
        _order_mapping(intent, 1101, role="STOP", branch=1, parent_order_id=1001),
        _order_mapping(intent, 1201, role="TARGET", branch=1, parent_order_id=1001),
    ]
    for mapping in mappings:
        assert store.register_intent_order(intent["setup_id"], mapping)["accepted"] is True
    result = store.mark_reservation_broker_visible(
        f"reservation-{intent['setup_id']}",
        [mapping["order_id"] for mapping in mappings],
        lease_key=lease_key,
        owner_token="observer-worker",
        fence_token=lease["fence_token"],
        now=_NOW,
        broker_order_evidence=[_observed_order(mapping) for mapping in mappings],
    )
    assert result["updated"] is True
    return intent, mappings


def _rewrite_json_row(connection, table, json_column, hash_column, where, payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    connection.execute(
        f"UPDATE {table} SET {json_column}=?, {hash_column}=? WHERE {where}",
        (encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()),
    )


def test_startup_audit_preserves_valid_broker_visible_evidence(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    intent, _mappings = _persist_valid_broker_visible_setup(db_path)

    reopened = TradingRiskStore(db_path)

    active = reopened.active_reservations()
    assert active[0]["setup_id"] == intent["setup_id"]
    assert active[0]["status"] == "BROKER_VISIBLE"


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_target",
        "zero_mapping_perm",
        "duplicate_mapping_perm",
        "missing_ack",
        "ack_hash_mismatch",
        "unacknowledged_order",
        "reservation_hash_mismatch",
        "malformed_reservation_json",
    ],
)
def test_startup_audit_quarantines_unproven_broker_visible_state(
    tmp_path, corruption
):
    db_path = tmp_path / "risk.sqlite"
    intent, mappings = _persist_valid_broker_visible_setup(db_path)
    with sqlite3.connect(db_path) as connection:
        if corruption == "missing_target":
            connection.execute(
                "DELETE FROM trading_risk_intent_orders WHERE setup_id=? AND role='TARGET'",
                (intent["setup_id"],),
            )
        elif corruption in {"zero_mapping_perm", "duplicate_mapping_perm"}:
            target = dict(mappings[2])
            target["perm_id"] = 0 if corruption == "zero_mapping_perm" else mappings[1]["perm_id"]
            encoded = json.dumps(
                target, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            connection.execute(
                """
                UPDATE trading_risk_intent_orders
                SET mapping_json=?, mapping_hash=?
                WHERE setup_id=? AND role='TARGET'
                """,
                (
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    intent["setup_id"],
                ),
            )
        elif corruption == "reservation_hash_mismatch":
            connection.execute(
                """
                UPDATE trading_risk_reservations SET reservation_hash=?
                WHERE setup_id=?
                """,
                ("0" * 64, intent["setup_id"]),
            )
        elif corruption == "malformed_reservation_json":
            connection.execute(
                """
                UPDATE trading_risk_reservations
                SET reservation_json=?, reservation_hash=? WHERE setup_id=?
                """,
                ("{", "0" * 64, intent["setup_id"]),
            )
        else:
            row = connection.execute(
                "SELECT reservation_json FROM trading_risk_reservations WHERE setup_id=?",
                (intent["setup_id"],),
            ).fetchone()
            payload = json.loads(row[0])
            if corruption == "missing_ack":
                payload.pop("broker_ack_evidence", None)
                payload.pop("broker_ack_evidence_hash", None)
            elif corruption == "ack_hash_mismatch":
                payload["broker_ack_evidence_hash"] = "0" * 64
            else:
                payload["broker_ack_evidence"]["orders"][0]["status"] = "PENDINGSUBMIT"
                ack_encoded = json.dumps(
                    payload["broker_ack_evidence"],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                payload["broker_ack_evidence_hash"] = hashlib.sha256(
                    ack_encoded.encode("utf-8")
                ).hexdigest()
            _rewrite_json_row(
                connection,
                "trading_risk_reservations",
                "reservation_json",
                "reservation_hash",
                f"setup_id='{intent['setup_id']}'",
                payload,
            )

    reopened = TradingRiskStore(db_path)

    quarantined = reopened.active_reservations()[0]
    assert quarantined["status"] == "RECONCILE_REQUIRED"
    assert quarantined["transition_reason"] == "startup_broker_visible_evidence_invalid"
    if corruption in {"missing_ack", "malformed_reservation_json"}:
        assert "broker_ack_evidence" not in quarantined
        assert "broker_ack_evidence_hash" not in quarantined
    with sqlite3.connect(db_path) as connection:
        conflict_kinds = {
            row[0]
            for row in connection.execute(
                "SELECT conflict_kind FROM trading_risk_evidence_conflicts WHERE setup_id=?",
                (intent["setup_id"],),
            )
        }
    assert "startup_broker_visible_evidence_invalid" in conflict_kinds


def test_startup_audit_quarantines_cross_setup_duplicate_positive_perm(tmp_path):
    db_path = tmp_path / "risk.sqlite"
    visible, visible_mappings = _persist_valid_broker_visible_setup(db_path)
    store = TradingRiskStore(db_path)
    other = _intent(
        "LEGACY-DUPLICATE-PERM",
        account="DU2",
        con_id=202,
        parent_order_id=2001,
        child_order_id=2101,
    )
    assert store.register_intent(_immutable_intent(other))["accepted"] is True
    other_parent = _order_mapping(other, 2001, role="PARENT", branch=1)
    assert store.register_intent_order(other["setup_id"], other_parent)["accepted"] is True
    duplicate = {**other_parent, "perm_id": visible_mappings[0]["perm_id"]}
    encoded = json.dumps(
        duplicate, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE trading_risk_intent_orders SET mapping_json=?, mapping_hash=?
            WHERE setup_id=? AND role='PARENT'
            """,
            (
                encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                other["setup_id"],
            ),
        )

    reopened = TradingRiskStore(db_path)

    quarantined = reopened.active_reservations()[0]
    assert quarantined["setup_id"] == visible["setup_id"]
    assert quarantined["status"] == "RECONCILE_REQUIRED"
