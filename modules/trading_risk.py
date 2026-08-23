"""Pure, broker-evidenced portfolio-risk contracts for paper trading.

This module deliberately has no persistence or broker dependency.  Callers
provide normalized snapshot dictionaries and receive conservative, structured
decisions that can be stored or rendered elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping, Optional


DEFAULT_RISK_POLICY = {
    "max_total_risk_pct": 0.75,
    "max_direction_risk_pct": 0.75,
    "max_verified_group_risk_pct": 0.50,
    "max_consecutive_losses": 3,
}

_TERMINAL_ORDER_STATUSES = {
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "INACTIVE",
    "EXPIRED",
    "FILLED",
    "APICANCELLED",
}
_ACTIVE_ORDER_STATUSES = {
    "PENDINGSUBMIT",
    "PRESUBMITTED",
    "SUBMITTED",
    "PENDINGCANCEL",
}
_TERMINAL_RESERVATION_STATUSES = {
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "RELEASED",
    "COMPLETED",
    "DONE",
}
_EPSILON = 1e-9


def _records(value: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_number(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token(value: Any) -> str:
    return _text(value).upper()


def _append_once(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _strictly_exceeds(value: float, limit: float) -> bool:
    """Ignore binary-float dust at a cap while preserving real excess."""
    return value > limit and not math.isclose(
        value, limit, rel_tol=1e-12, abs_tol=1e-12
    )


def _utc_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identity(record: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    account = _text(record.get("account"))
    con_id = _text(record.get("con_id"))
    return (account, con_id) if account and con_id else None


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_identity = _identity(left)
    return left_identity is not None and left_identity == _identity(right)


def _intent_direction(intent: Mapping[str, Any]) -> Optional[str]:
    direction = _token(intent.get("direction"))
    return direction if direction in {"LONG", "SHORT"} else None


def _hypothetical_plan_direction(record: Mapping[str, Any]) -> Optional[str]:
    """Return only an explicit, verifiable plan direction (never default LONG)."""
    nested = record.get("trade_setup")
    setup = nested if isinstance(nested, Mapping) else {}
    raw = None
    for source in (record, setup):
        for key in (
            "direction",
            "Direction",
            "Signal_Direction",
            "BI_Direction",
            "_direction",
            "side",
        ):
            value = source.get(key)
            if value is not None and _text(value):
                raw = value
                break
        if raw is not None:
            break
    normalized = _token(raw)
    if normalized in {"LONG", "BUY"}:
        return "LONG"
    if normalized in {"SHORT", "SELL"}:
        return "SHORT"
    return None


def _hypothetical_plan_level(
    record: Mapping[str, Any], aliases: tuple[str, ...]
) -> Optional[float]:
    nested = record.get("trade_setup")
    setup = nested if isinstance(nested, Mapping) else {}
    for source in (record, setup):
        for key in aliases:
            if key in source:
                value = _positive_number(source.get(key))
                if value is not None:
                    return value
    return None


def summarize_hypothetical_batch_risk(
    plans: Optional[Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Summarize mail plans in abstract R units without portfolio claims.

    Each directionally valid Entry/Stop plan contributes exactly ``1R``.  No
    account, quantity, currency, notional or position value is accepted or
    returned.  Direction is never inferred from a default; group totals are
    emitted only for an explicitly verified classification.  The result is
    informational and has no authorization/suppression decision.
    """
    if plans is None:
        raw_plans: list[Any] = []
    elif isinstance(plans, (str, bytes, Mapping)):
        raw_plans = [plans]
    else:
        try:
            raw_plans = list(plans)
        except TypeError:
            raw_plans = [plans]

    valid = 0
    invalid = 0
    direction_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    for plan in raw_plans:
        if not isinstance(plan, Mapping):
            invalid += 1
            continue
        direction = _hypothetical_plan_direction(plan)
        entry = _hypothetical_plan_level(plan, ("entry", "Entry"))
        stop = _hypothetical_plan_level(
            plan, ("stop", "stop_loss", "StopLoss", "Stop", "SL")
        )
        geometry_valid = (
            direction is not None
            and entry is not None
            and stop is not None
            and (
                (direction == "LONG" and stop < entry)
                or (direction == "SHORT" and stop > entry)
            )
        )
        if not geometry_valid:
            invalid += 1
            continue
        valid += 1
        assert direction is not None
        direction_counts[direction] = direction_counts.get(direction, 0) + 1

        nested = plan.get("trade_setup")
        setup = nested if isinstance(nested, Mapping) else {}
        verified = plan.get("group_verified") is True
        group_raw = plan.get("group_key")
        if group_raw is None:
            verified = setup.get("group_verified") is True
            group_raw = setup.get("group_key")
        group_key = _canonical_group_key(group_raw)
        if verified and group_key is not None:
            group_counts[group_key] = group_counts.get(group_key, 0) + 1

    return {
        "informational_only": True,
        "unit_r_per_plan": 1.0,
        "valid_plans": valid,
        "invalid_or_unverified_plans": invalid,
        "total_hypothetical_r": float(valid),
        "by_direction_r": {
            key: float(direction_counts[key])
            for key in ("LONG", "SHORT")
            if key in direction_counts
        },
        "by_verified_group_r": {
            key: float(group_counts[key]) for key in sorted(group_counts)
        },
    }


def _intent_key(intent: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(intent.get("setup_id")),
        _text(intent.get("order_ref")),
        _text(intent.get("account")),
        _text(intent.get("con_id")),
    )


def _parent_ids(intent: Mapping[str, Any]) -> list[str]:
    raw = intent.get("parent_order_ids")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return []
    return [value for value in (_text(item) for item in raw) if value]


def _intent_order_ids(intent: Mapping[str, Any]) -> list[str]:
    raw = intent.get("order_ids")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return []
    return [value for value in (_text(item) for item in raw) if value]


def _order_id(order: Mapping[str, Any]) -> str:
    return _text(order.get("order_id"))


def _order_parent_id(order: Mapping[str, Any]) -> str:
    return _text(order.get("parent_id"))


def _order_capacity_key(order: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(order.get("account")),
        _text(order.get("con_id")),
        _order_id(order),
        _text(order.get("order_ref")),
    )


def _order_is_active(order: Mapping[str, Any]) -> bool:
    return _token(order.get("status")) in _ACTIVE_ORDER_STATUSES


def _order_status_known(order: Mapping[str, Any]) -> bool:
    status = _token(order.get("status"))
    return status in _ACTIVE_ORDER_STATUSES or status in _TERMINAL_ORDER_STATUSES


def _order_remaining(order: Mapping[str, Any]) -> Optional[float]:
    remaining = _finite_number(order.get("remaining"))
    if remaining is not None:
        return remaining if remaining >= 0 else None
    quantity = _finite_number(order.get("quantity"))
    filled = _finite_number(order.get("filled"))
    if quantity is None:
        return None
    if filled is None:
        filled = 0.0
    value = quantity - filled
    return value if value >= 0 else None


def _is_stop_order(order: Mapping[str, Any]) -> bool:
    return "STP" in _token(order.get("order_type"))


def _expected_exit_action(direction: str) -> str:
    return "SELL" if direction == "LONG" else "BUY"


def _expected_entry_action(direction: str) -> str:
    return "BUY" if direction == "LONG" else "SELL"


def _matches_intent_stop(
    order: Mapping[str, Any],
    intent: Mapping[str, Any],
    *,
    parent_id: Optional[str] = None,
    expected_ref: Optional[str] = None,
) -> bool:
    if not _same_identity(order, intent) or not _is_stop_order(order):
        return False
    intended_parents = set(_parent_ids(intent))
    actual_parent = _order_parent_id(order)
    if parent_id is not None:
        if actual_parent != parent_id:
            return False
    elif actual_parent not in intended_parents:
        return False
    parent_ids = _parent_ids(intent)
    if actual_parent not in parent_ids:
        return False
    branch = parent_ids.index(actual_parent) + 1
    reference = _text(order.get("order_ref"))
    intent_ref = _text(intent.get("order_ref"))
    branch_ref = f"{intent_ref}-S{branch}"
    if not intent_ref or reference != branch_ref:
        return False
    return expected_ref is None or reference == expected_ref


def _stop_risk_for_quantity(
    intent: Mapping[str, Any],
    quantity: float,
    entry_price: float,
    orders: list[Mapping[str, Any]],
    *,
    missing_code: str,
    wrong_side_code: str,
    undercovered_code: str,
    invalid_code: str,
    parent_id: Optional[str] = None,
    expected_ref: Optional[str] = None,
    allow_nonloss_stop: bool = False,
    available_stop_quantity: Optional[
        dict[tuple[str, str, str, str], float]
    ] = None,
) -> tuple[Optional[float], Optional[str]]:
    direction = _intent_direction(intent)
    if direction is None or quantity <= 0 or not math.isfinite(entry_price):
        return None, invalid_code
    candidates = [
        order
        for order in orders
        if _order_is_active(order)
        and _matches_intent_stop(
            order, intent, parent_id=parent_id, expected_ref=expected_ref
        )
    ]
    if not candidates:
        return None, missing_code
    expected_action = _expected_exit_action(direction)
    if any(_token(order.get("action")) != expected_action for order in candidates):
        return None, wrong_side_code

    priced_candidates: list[
        tuple[float, str, str, Mapping[str, Any], float]
    ] = []
    for order in candidates:
        capacity_key = _order_capacity_key(order)
        active_quantity = (
            available_stop_quantity.get(capacity_key)
            if available_stop_quantity is not None
            else _order_remaining(order)
        )
        stop_price = _positive_number(order.get("stop_price"))
        if active_quantity is None or stop_price is None:
            return None, invalid_code
        invalid_geometry = (
            (direction == "LONG" and stop_price >= entry_price)
            or (direction == "SHORT" and stop_price <= entry_price)
        )
        if invalid_geometry and not allow_nonloss_stop:
            return None, invalid_code
        risk_per_share = (
            max(0.0, entry_price - stop_price)
            if direction == "LONG"
            else max(0.0, stop_price - entry_price)
        )
        priced_candidates.append(
            (
                risk_per_share,
                _order_id(order),
                _text(order.get("order_ref")),
                order,
                active_quantity,
            )
        )

    remaining_quantity = quantity
    risk_usd = 0.0
    # A position snapshot does not reveal which parent branch supplied each
    # share.  Allocate protection from the largest per-share loss first so an
    # overlapping set of valid stops can never understate broker risk.
    allocations: list[tuple[tuple[str, str, str, str], float]] = []
    for risk_per_share, _order_key, _reference, order, active_quantity in sorted(
        priced_candidates,
        key=lambda item: (-item[0], item[1], item[2]),
    ):
        used_quantity = min(remaining_quantity, active_quantity)
        if used_quantity > 0:
            risk_usd += used_quantity * risk_per_share
            remaining_quantity -= used_quantity
            allocations.append((_order_capacity_key(order), used_quantity))
        if remaining_quantity <= _EPSILON:
            break
    if remaining_quantity > _EPSILON:
        return None, undercovered_code
    if available_stop_quantity is not None:
        for capacity_key, used_quantity in allocations:
            available_stop_quantity[capacity_key] = max(
                0.0,
                available_stop_quantity[capacity_key] - used_quantity,
            )
    return risk_usd, None


def _group_details(record: Mapping[str, Any]) -> tuple[Optional[str], bool]:
    group_key = _canonical_group_key(record.get("group_key"))
    return group_key, record.get("group_verified") is True


def _canonical_group_key(value: Any) -> Optional[str]:
    normalized = " ".join(_text(value).split()).upper()
    return normalized or None


def _add_risk_item(
    items: list[dict[str, Any]],
    warnings: list[str],
    *,
    source: str,
    intent: Mapping[str, Any],
    quantity: float,
    risk_usd: float,
    notional_usd: float,
    grouping: Optional[Mapping[str, Any]] = None,
) -> None:
    direction = _intent_direction(intent)
    if direction is None:
        return
    grouping = grouping or intent
    group_key, group_verified = _group_details(grouping)
    if not group_verified or group_key is None:
        _append_once(warnings, "group_classification_unavailable")
    items.append(
        {
            "source": source,
            "setup_id": _text(intent.get("setup_id")),
            "order_ref": _text(intent.get("order_ref")),
            "account": _text(intent.get("account")),
            "con_id": _text(intent.get("con_id")),
            "direction": direction,
            "quantity": float(quantity),
            "risk_usd": float(risk_usd),
            "notional_usd": float(notional_usd),
            "group_key": group_key,
            "group_verified": group_verified,
        }
    )


def _matching_intents(
    record: Mapping[str, Any], intents: list[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    return [intent for intent in intents if _same_identity(record, intent)]


def _reservation_matches_intent(
    reservation: Mapping[str, Any], intent: Mapping[str, Any]
) -> bool:
    if not _same_identity(reservation, intent):
        return False
    setup_id = _text(reservation.get("setup_id"))
    order_ref = _text(reservation.get("order_ref"))
    intent_setup_id = _text(intent.get("setup_id"))
    intent_order_ref = _text(intent.get("order_ref"))
    if setup_id and setup_id != intent_setup_id:
        return False
    if order_ref and order_ref != intent_order_ref:
        return False
    return bool(setup_id or order_ref)


def _matches_mapped_child(
    order: Mapping[str, Any], intent: Mapping[str, Any]
) -> bool:
    """Require immutable broker id, branch parent and exact AS2 child ref."""
    if not _same_identity(order, intent):
        return False
    order_id = _order_id(order)
    parent_id = _order_parent_id(order)
    order_ids = set(_intent_order_ids(intent))
    parent_ids = _parent_ids(intent)
    if not order_id or order_id not in order_ids or parent_id not in parent_ids:
        return False
    branch = parent_ids.index(parent_id) + 1
    base_ref = _text(intent.get("order_ref"))
    reference = _text(order.get("order_ref"))
    return bool(base_ref) and reference in {
        f"{base_ref}-S{branch}",
        f"{base_ref}-T{branch}",
    }


def aggregate_stop_risk(
    net_liquidation: Any,
    positions: Optional[Iterable[Mapping[str, Any]]],
    orders: Optional[Iterable[Mapping[str, Any]]],
    intents: Optional[Iterable[Mapping[str, Any]]],
    reservations: Optional[Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Aggregate only broker-protected or durable-reserved stop risk.

    Any unresolvable live exposure marks the snapshot unreliable.  Known risk
    is still returned for diagnostics, but callers must fail closed on
    ``reliable is False``.
    """
    normalized_intents = _records(intents)
    normalized_orders = _records(orders)
    normalized_positions = _records(positions)
    normalized_reservations = _records(reservations)
    unresolved_codes: list[str] = []
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    visible_quantities: dict[tuple[str, str, str, str], float] = {}
    position_seen: set[tuple[str, str, str, str]] = set()
    available_stop_quantity: dict[tuple[str, str, str, str], float] = {}

    def mark_visible(intent: Mapping[str, Any], quantity: float) -> None:
        key = _intent_key(intent)
        visible_quantities[key] = visible_quantities.get(key, 0.0) + max(0.0, quantity)

    normalized_net_liquidation = _positive_number(net_liquidation)
    if normalized_net_liquidation is None:
        _append_once(unresolved_codes, "net_liquidation_invalid")
    for order in normalized_orders:
        if not _order_status_known(order):
            _append_once(unresolved_codes, "order_status_unknown")
        if _order_is_active(order) and _is_stop_order(order):
            stop_quantity = _order_remaining(order)
            if stop_quantity is None:
                _append_once(unresolved_codes, "protective_stop_invalid")
                continue
            capacity_key = _order_capacity_key(order)
            if capacity_key in available_stop_quantity:
                _append_once(unresolved_codes, "stop_order_identity_duplicate")
                continue
            available_stop_quantity[capacity_key] = stop_quantity

    # A child stop can cover both already-filled shares and the unfilled
    # remainder of its own parent, but its quantity may only be consumed once.
    # Reserve each exact pending branch first; positions may use only the
    # residual capacity across all branches.
    position_stop_quantity = dict(available_stop_quantity)
    pending_stop_quantity = {
        capacity_key: 0.0 for capacity_key in available_stop_quantity
    }
    for intent in normalized_intents:
        direction = _intent_direction(intent)
        if direction is None:
            continue
        for index, parent_id in enumerate(_parent_ids(intent), start=1):
            matching_parents = [
                order
                for order in normalized_orders
                if _same_identity(order, intent)
                and _order_id(order) == parent_id
                and _order_parent_id(order) in {"", "0"}
                and _order_is_active(order)
            ]
            if len(matching_parents) != 1:
                continue
            parent = matching_parents[0]
            remaining = _order_remaining(parent)
            if remaining is None or remaining <= _EPSILON:
                continue
            if (
                _text(parent.get("order_ref"))
                != f"{_text(intent.get('order_ref'))}-P{index}"
                or _token(parent.get("action"))
                != _expected_entry_action(direction)
            ):
                continue
            expected_stop_ref = f"{_text(intent.get('order_ref'))}-S{index}"
            stop_candidates = [
                order
                for order in normalized_orders
                if _order_is_active(order)
                and _matches_intent_stop(
                    order,
                    intent,
                    parent_id=parent_id,
                    expected_ref=expected_stop_ref,
                )
            ]
            for stop_order in sorted(
                stop_candidates,
                key=lambda item: (_order_id(item), _text(item.get("order_ref"))),
            ):
                capacity_key = _order_capacity_key(stop_order)
                available = position_stop_quantity.get(capacity_key, 0.0)
                used = min(remaining, available)
                if used > 0:
                    position_stop_quantity[capacity_key] = available - used
                    pending_stop_quantity[capacity_key] += used
                    remaining -= used
                if remaining <= _EPSILON:
                    break

    for position in normalized_positions:
        raw_quantity = _finite_number(position.get("quantity"))
        if raw_quantity is None or abs(raw_quantity) <= _EPSILON:
            if raw_quantity is None:
                _append_once(unresolved_codes, "position_quantity_invalid")
            continue
        matches = _matching_intents(position, normalized_intents)
        if not matches:
            _append_once(unresolved_codes, "position_intent_missing")
            continue
        if len(matches) != 1:
            _append_once(unresolved_codes, "position_intent_ambiguous")
            continue
        intent = matches[0]
        position_seen.add(_intent_key(intent))
        direction = _intent_direction(intent)
        if direction is None or (
            direction == "LONG" and raw_quantity < 0
        ) or (
            direction == "SHORT" and raw_quantity > 0
        ):
            _append_once(unresolved_codes, "position_direction_mismatch")
            continue
        average_cost = _positive_number(position.get("avg_cost"))
        if average_cost is None:
            _append_once(unresolved_codes, "position_cost_invalid")
            continue
        quantity = abs(raw_quantity)
        risk_usd, error = _stop_risk_for_quantity(
            intent,
            quantity,
            average_cost,
            normalized_orders,
            missing_code="protective_stop_missing",
            wrong_side_code="protective_stop_wrong_side",
            undercovered_code="protective_stop_undercovered",
            invalid_code="protective_stop_invalid",
            allow_nonloss_stop=True,
            available_stop_quantity=position_stop_quantity,
        )
        mark_visible(intent, quantity)
        if error is not None:
            _append_once(unresolved_codes, error)
            continue
        _add_risk_item(
            items,
            warnings,
            source="position",
            intent=intent,
            quantity=quantity,
            risk_usd=float(risk_usd),
            notional_usd=quantity * average_cost,
        )

    known_parent_order_ids: set[str] = set()
    for intent in normalized_intents:
        direction = _intent_direction(intent)
        intended_parents = _parent_ids(intent)
        if direction is None:
            continue
        entry = _positive_number(intent.get("entry"))
        stop_limit = _positive_number(intent.get("stop_limit"))
        if (
            entry is None
            or stop_limit is None
            or (direction == "LONG" and stop_limit < entry)
            or (direction == "SHORT" and stop_limit > entry)
        ):
            _append_once(unresolved_codes, "pending_parent_invalid")
            continue
        adverse_fill = (
            max(entry, stop_limit)
            if direction == "LONG"
            else min(entry, stop_limit)
        )
        notional_basis = max(entry, stop_limit)
        for index, parent_id in enumerate(intended_parents, start=1):
            matching_parents = [
                order
                for order in normalized_orders
                if _same_identity(order, intent)
                and _order_id(order) == parent_id
                and _order_parent_id(order) in {"", "0"}
                and _order_is_active(order)
            ]
            if len(matching_parents) > 1:
                _append_once(unresolved_codes, "pending_parent_ambiguous")
                continue
            if not matching_parents:
                continue
            parent = matching_parents[0]
            known_parent_order_ids.add(_order_id(parent))
            pending_quantity = _order_remaining(parent)
            if pending_quantity is None:
                _append_once(unresolved_codes, "pending_parent_invalid")
                continue
            expected_parent_ref = f"{_text(intent.get('order_ref'))}-P{index}"
            if _text(parent.get("order_ref")) != expected_parent_ref:
                _append_once(unresolved_codes, "pending_parent_identity_mismatch")
                mark_visible(intent, pending_quantity)
                continue
            if _token(parent.get("action")) != _expected_entry_action(direction):
                _append_once(unresolved_codes, "pending_parent_invalid")
                mark_visible(intent, pending_quantity)
                continue
            if pending_quantity <= _EPSILON:
                continue
            mark_visible(intent, pending_quantity)
            expected_stop_ref = f"{_text(intent.get('order_ref'))}-S{index}"
            risk_usd, error = _stop_risk_for_quantity(
                intent,
                pending_quantity,
                adverse_fill,
                normalized_orders,
                missing_code="pending_stop_missing",
                wrong_side_code="pending_stop_wrong_side",
                undercovered_code="pending_stop_undercovered",
                invalid_code="pending_stop_invalid",
                parent_id=parent_id,
                expected_ref=expected_stop_ref,
                available_stop_quantity=pending_stop_quantity,
            )
            if error is not None:
                _append_once(unresolved_codes, error)
                continue
            _add_risk_item(
                items,
                warnings,
                source="pending_parent",
                intent=intent,
                quantity=pending_quantity,
                risk_usd=float(risk_usd),
                notional_usd=pending_quantity * notional_basis,
            )

    for order in normalized_orders:
        if not _order_is_active(order) or _order_parent_id(order) not in {"", "0"}:
            continue
        pending_quantity = _order_remaining(order)
        if pending_quantity is None:
            _append_once(unresolved_codes, "pending_parent_invalid")
            continue
        if pending_quantity <= _EPSILON:
            continue
        order_id = _order_id(order)
        if not order_id:
            _append_once(unresolved_codes, "pending_parent_identity_mismatch")
            continue
        if order_id not in known_parent_order_ids:
            matching_intents = _matching_intents(order, normalized_intents)
            _append_once(
                unresolved_codes,
                "pending_parent_identity_mismatch"
                if matching_intents
                else "pending_parent_intent_missing",
            )

    for reservation in normalized_reservations:
        if _token(reservation.get("status")) in _TERMINAL_RESERVATION_STATUSES:
            continue
        matches = [
            intent
            for intent in normalized_intents
            if _reservation_matches_intent(reservation, intent)
        ]
        if not matches:
            _append_once(unresolved_codes, "reservation_intent_missing")
            continue
        if len(matches) != 1:
            _append_once(unresolved_codes, "reservation_intent_ambiguous")
            continue
        intent = matches[0]
        direction = _intent_direction(intent)
        reservation_direction = _token(reservation.get("direction"))
        quantity = _positive_number(reservation.get("quantity"))
        entry = _positive_number(reservation.get("entry"))
        stop = _positive_number(reservation.get("stop"))
        intent_quantity = _positive_number(intent.get("quantity"))
        intent_entry = _positive_number(intent.get("entry"))
        intent_stop = _positive_number(intent.get("stop"))
        intent_limit = _positive_number(intent.get("stop_limit"))
        adverse_fill = (
            max(intent_entry, intent_limit)
            if direction == "LONG"
            and intent_entry is not None
            and intent_limit is not None
            else min(intent_entry, intent_limit)
            if direction == "SHORT"
            and intent_entry is not None
            and intent_limit is not None
            else None
        )
        risk_per_share = (
            abs(adverse_fill - intent_stop)
            if adverse_fill is not None and intent_stop is not None
            else None
        )
        notional_basis = (
            max(intent_entry, intent_limit)
            if intent_entry is not None and intent_limit is not None
            else None
        )
        persisted_risk_basis = _positive_number(
            reservation.get("risk_basis_price_usd")
        )
        persisted_risk_per_share = _positive_number(
            reservation.get("risk_per_share_usd")
        )
        persisted_cash_basis = _positive_number(
            reservation.get("cash_basis_price_usd")
        )
        if (
            direction is None
            or reservation_direction != direction
            or quantity is None
            or entry is None
            or stop is None
            or intent_quantity is None
            or intent_entry is None
            or intent_stop is None
            or intent_limit is None
            or adverse_fill is None
            or risk_per_share is None
            or risk_per_share <= 0
            or notional_basis is None
            or not math.isclose(quantity, intent_quantity, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(entry, intent_entry, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(stop, intent_stop, rel_tol=0, abs_tol=1e-12)
            or (direction == "LONG" and stop >= entry)
            or (direction == "SHORT" and stop <= entry)
            or (
                reservation.get("risk_basis_price_usd") is not None
                and (
                    persisted_risk_basis is None
                    or not math.isclose(
                        persisted_risk_basis, adverse_fill, rel_tol=0, abs_tol=1e-12
                    )
                )
            )
            or (
                reservation.get("risk_per_share_usd") is not None
                and (
                    persisted_risk_per_share is None
                    or not math.isclose(
                        persisted_risk_per_share,
                        risk_per_share,
                        rel_tol=0,
                        abs_tol=1e-12,
                    )
                )
            )
            or (
                reservation.get("cash_basis_price_usd") is not None
                and (
                    persisted_cash_basis is None
                    or not math.isclose(
                        persisted_cash_basis,
                        notional_basis,
                        rel_tol=0,
                        abs_tol=1e-12,
                    )
                )
            )
        ):
            _append_once(unresolved_codes, "reservation_invalid")
            continue
        remaining_quantity = max(
            0.0,
            quantity - visible_quantities.get(_intent_key(intent), 0.0),
        )
        if remaining_quantity <= _EPSILON:
            continue
        mark_visible(intent, remaining_quantity)
        _add_risk_item(
            items,
            warnings,
            source="reservation",
            intent=intent,
            quantity=remaining_quantity,
            risk_usd=remaining_quantity * risk_per_share,
            notional_usd=remaining_quantity * notional_basis,
            grouping=reservation,
        )

    # Child orders are future broker actions even when their parent and
    # position have disappeared.  Every active child must therefore resolve
    # to one immutable intent and no more quantity than that intent's visible
    # position/pending/reserved exposure.  This catches stale STOP/TARGET
    # orders after a release or terminal outcome instead of treating them as
    # zero exposure.
    for order in normalized_orders:
        if not _order_is_active(order) or _order_parent_id(order) in {"", "0"}:
            continue
        remaining = _order_remaining(order)
        if remaining is None:
            _append_once(unresolved_codes, "active_child_invalid")
            continue
        if remaining <= _EPSILON:
            continue
        identity_matches = _matching_intents(order, normalized_intents)
        if not identity_matches:
            _append_once(unresolved_codes, "active_child_intent_missing")
            continue
        exact_matches = [
            intent
            for intent in identity_matches
            if _matches_mapped_child(order, intent)
        ]
        if len(exact_matches) != 1:
            _append_once(
                unresolved_codes,
                "active_child_intent_ambiguous"
                if len(exact_matches) > 1
                else "active_child_identity_mismatch",
            )
            continue
        intent = exact_matches[0]
        direction = _intent_direction(intent)
        if (
            direction is None
            or _token(order.get("action")) != _expected_exit_action(direction)
        ):
            _append_once(unresolved_codes, "active_child_invalid")
            continue
        visible = visible_quantities.get(_intent_key(intent), 0.0)
        if visible <= _EPSILON:
            _append_once(unresolved_codes, "active_child_without_exposure")
        elif remaining - visible > _EPSILON:
            _append_once(unresolved_codes, "active_child_quantity_exceeds_exposure")

    for intent in normalized_intents:
        if (
            _token(intent.get("status")) == "FILLED_NO_POSITION"
            and _intent_key(intent) not in position_seen
        ):
            _append_once(unresolved_codes, "filled_parent_position_unresolved")

    direction_risk_usd = {"LONG": 0.0, "SHORT": 0.0}
    verified_group_risk_usd: dict[str, float] = {}
    for item in items:
        direction = item["direction"]
        direction_risk_usd[direction] += float(item["risk_usd"])
        if item["group_verified"] and item["group_key"]:
            group_key = str(item["group_key"])
            verified_group_risk_usd[group_key] = (
                verified_group_risk_usd.get(group_key, 0.0)
                + float(item["risk_usd"])
            )
    total_risk_usd = sum(float(item["risk_usd"]) for item in items)
    position_notional_usd = sum(
        float(item["notional_usd"])
        for item in items
        if item["source"] == "position"
    )
    non_position_committed_notional_usd = sum(
        float(item["notional_usd"])
        for item in items
        if item["source"] != "position"
    )
    committed_by_contract: dict[tuple[str, str], set[str]] = {}
    committed_setups: set[str] = set()
    for item in items:
        account = _text(item.get("account"))
        con_id = _text(item.get("con_id"))
        setup_id = _text(item.get("setup_id"))
        if account and con_id:
            committed_by_contract.setdefault((account, con_id), set())
            if setup_id:
                committed_by_contract[(account, con_id)].add(setup_id)
        if setup_id:
            committed_setups.add(setup_id)
    committed_contracts = [
        {
            "account": account,
            "con_id": con_id,
            "setup_ids": sorted(setup_ids),
        }
        for (account, con_id), setup_ids in sorted(committed_by_contract.items())
    ]
    total_risk_pct = (
        total_risk_usd / normalized_net_liquidation * 100.0
        if normalized_net_liquidation is not None
        else None
    )
    return {
        "reliable": not unresolved_codes,
        "net_liquidation": normalized_net_liquidation,
        "total_risk_usd": total_risk_usd,
        "total_risk_pct": total_risk_pct,
        "position_notional_usd": position_notional_usd,
        "non_position_committed_notional_usd": non_position_committed_notional_usd,
        "committed_notional_usd": (
            position_notional_usd + non_position_committed_notional_usd
        ),
        "committed_setup_count": len(committed_setups),
        "committed_contract_count": len(committed_contracts),
        "committed_contracts": committed_contracts,
        "direction_risk_usd": direction_risk_usd,
        "verified_group_risk_usd": verified_group_risk_usd,
        "items": items,
        "unresolved_codes": unresolved_codes,
        "warnings": warnings,
    }


def derive_intent_outcome(
    intent: Mapping[str, Any],
    fills: Optional[Iterable[Mapping[str, Any]]],
    *,
    broker_position_open: Any,
    parent_orders_terminal: Any,
) -> dict[str, Any]:
    """Derive a completed long/short result only from coherent broker fills."""
    intent = intent if isinstance(intent, Mapping) else {}
    direction = _intent_direction(intent)
    unresolved_codes: list[str] = []
    entry_events: list[tuple[datetime, Optional[float], str, float, float]] = []
    exit_events: list[tuple[datetime, Optional[float], str, float, float]] = []
    intent_order_ids = set(_intent_order_ids(intent))
    parent_order_ids = set(_parent_ids(intent))
    seen_exec_ids: set[str] = set()
    evidence_times: list[datetime] = []

    if direction is None or not intent_order_ids or not parent_order_ids:
        _append_once(unresolved_codes, "intent_identity_invalid")
    expected_entry_sides = {"BOT", "BUY"} if direction == "LONG" else {"SLD", "SELL"}
    expected_exit_sides = {"SLD", "SELL"} if direction == "LONG" else {"BOT", "BUY"}

    for fill in _records(fills):
        if not _same_identity(fill, intent):
            continue
        raw_event_time = _utc_datetime(fill.get("time"))
        if raw_event_time is not None:
            evidence_times.append(raw_event_time)
        if _order_id(fill) not in intent_order_ids:
            _append_once(unresolved_codes, "fill_order_unrecognized")
            continue
        exec_id = _text(fill.get("exec_id"))
        if not exec_id or exec_id in seen_exec_ids:
            _append_once(unresolved_codes, "fill_duplicate")
            continue
        seen_exec_ids.add(exec_id)
        side = _token(fill.get("side"))
        if side in expected_entry_sides:
            event_kind = "entry"
        elif side in expected_exit_sides:
            event_kind = "exit"
        else:
            _append_once(unresolved_codes, "fill_side_unknown")
            continue
        order_is_parent = _order_id(fill) in parent_order_ids
        if (event_kind == "entry") != order_is_parent:
            _append_once(unresolved_codes, "fill_order_role_mismatch")
        quantity = _positive_number(fill.get("shares"))
        price = _positive_number(fill.get("price"))
        event_time = raw_event_time
        if quantity is None or price is None:
            _append_once(unresolved_codes, "fill_value_invalid")
            continue
        if event_time is None:
            _append_once(unresolved_codes, "fill_time_invalid")
            continue
        sequence = _finite_number(
            fill.get("ledger_sequence", fill.get("sequence"))
        )
        event = (event_time, sequence, exec_id, quantity, price)
        (entry_events if event_kind == "entry" else exit_events).append(event)

    events_by_time: dict[datetime, list[tuple[str, Optional[float]]]] = {}
    for event_kind, events in (("entry", entry_events), ("exit", exit_events)):
        for event_time, sequence, _exec_id, _quantity, _price in events:
            events_by_time.setdefault(event_time, []).append((event_kind, sequence))
    for same_time_events in events_by_time.values():
        kinds = {event_kind for event_kind, _sequence in same_time_events}
        sequences = [sequence for _event_kind, sequence in same_time_events]
        if (
            len(kinds) > 1
            and (
                any(sequence is None for sequence in sequences)
                or len(set(sequences)) != len(sequences)
            )
        ):
            _append_once(unresolved_codes, "fill_sequence_ambiguous")

    ordered_events = sorted(
        [
            (time, sequence, exec_id, "entry", quantity, price)
            for time, sequence, exec_id, quantity, price in entry_events
        ]
        + [
            (time, sequence, exec_id, "exit", quantity, price)
            for time, sequence, exec_id, quantity, price in exit_events
        ],
        key=lambda value: (
            value[0],
            value[1] if value[1] is not None else math.inf,
            value[3],
            value[2],
        ),
    )
    open_quantity = 0.0
    for _time, _sequence, _exec_id, kind, quantity, _price in ordered_events:
        if kind == "entry":
            open_quantity += quantity
        else:
            if quantity > open_quantity + _EPSILON:
                _append_once(unresolved_codes, "fill_side_flip")
            open_quantity -= quantity
        if open_quantity < -_EPSILON:
            _append_once(unresolved_codes, "fill_side_flip")

    entry_quantity = sum(item[3] for item in entry_events)
    exit_quantity = sum(item[3] for item in exit_events)
    entry_vwap = (
        sum(quantity * price for _time, _sequence, _exec_id, quantity, price in entry_events)
        / entry_quantity
        if entry_quantity > _EPSILON
        else None
    )
    exit_vwap = (
        sum(quantity * price for _time, _sequence, _exec_id, quantity, price in exit_events)
        / exit_quantity
        if exit_quantity > _EPSILON
        else None
    )
    if entry_quantity <= _EPSILON:
        _append_once(unresolved_codes, "entry_quantity_incomplete")
    if exit_quantity + _EPSILON < entry_quantity:
        _append_once(unresolved_codes, "exit_quantity_incomplete")
    if exit_quantity > entry_quantity + _EPSILON:
        _append_once(unresolved_codes, "fill_exit_overage")
    if broker_position_open is not False:
        _append_once(unresolved_codes, "broker_position_still_open")
    if parent_orders_terminal is not True:
        _append_once(unresolved_codes, "parent_orders_not_terminal")

    stop = _positive_number(intent.get("stop"))
    if (
        entry_vwap is None
        or stop is None
        or direction is None
        or (direction == "LONG" and stop >= entry_vwap)
        or (direction == "SHORT" and stop <= entry_vwap)
    ):
        _append_once(unresolved_codes, "risk_basis_invalid")

    complete = not unresolved_codes
    if complete:
        assert entry_vwap is not None and exit_vwap is not None and stop is not None
        realized_pnl_usd = (
            (exit_vwap - entry_vwap) * exit_quantity
            if direction == "LONG"
            else (entry_vwap - exit_vwap) * exit_quantity
        )
        initial_risk_usd = abs(entry_vwap - stop) * entry_quantity
        realized_r = realized_pnl_usd / initial_risk_usd
        realized_at = max(item[0] for item in exit_events).isoformat()
        outcome_evidence = "broker_fills"
    else:
        realized_pnl_usd = None
        realized_r = None
        realized_at = None
        outcome_evidence = None
    last_evidence_at = max(evidence_times).isoformat() if evidence_times else None
    return {
        "setup_id": _text(intent.get("setup_id")),
        "order_ref": _text(intent.get("order_ref")),
        "direction": direction,
        "complete": complete,
        "entry_quantity": entry_quantity,
        "exit_quantity": exit_quantity,
        "entry_vwap": entry_vwap,
        "exit_vwap": exit_vwap,
        "realized_pnl_usd": realized_pnl_usd,
        "realized_r": realized_r,
        "realized_at": realized_at,
        "last_evidence_at": last_evidence_at,
        "outcome_evidence": outcome_evidence,
        "unresolved_codes": unresolved_codes,
    }


def consecutive_losses_today(
    outcomes: Optional[Iterable[Mapping[str, Any]]], now: Any
) -> dict[str, Any]:
    """Return the trailing UTC-day loss streak without inventing an outcome."""
    current_time = _utc_datetime(now)
    if current_time is None:
        return {
            "consecutive_losses": 0,
            "streak_reliable": False,
            "unresolved_codes": ["loss_outcome_unresolved"],
            "utc_date": "",
        }
    day = current_time.date()
    valid_outcomes: list[tuple[datetime, str, float]] = []
    unresolved_codes: list[str] = []
    for outcome in _records(outcomes):
        realized_at = _utc_datetime(outcome.get("realized_at"))
        evidence_time = (
            realized_at
            or _utc_datetime(outcome.get("last_evidence_at"))
            or _utc_datetime(outcome.get("terminal_observed_at"))
        )
        outcome_valid = (
            outcome.get("complete") is True
            and outcome.get("outcome_evidence") == "broker_fills"
            and not bool(outcome.get("unresolved_codes"))
            and _finite_number(outcome.get("realized_r")) is not None
            and realized_at is not None
        )
        if evidence_time is None:
            if not outcome_valid:
                _append_once(unresolved_codes, "loss_outcome_unresolved")
            continue
        if evidence_time > current_time:
            _append_once(unresolved_codes, "loss_outcome_unresolved")
            continue
        if evidence_time.date() != day:
            continue
        if not outcome_valid:
            _append_once(unresolved_codes, "loss_outcome_unresolved")
            continue
        assert realized_at is not None
        realized_r = _finite_number(outcome.get("realized_r"))
        assert realized_r is not None
        valid_outcomes.append((realized_at, _text(outcome.get("setup_id")), realized_r))
    losses = 0
    outcomes_by_time: dict[datetime, list[float]] = {}
    for realized_at, _setup_id, realized_r in valid_outcomes:
        outcomes_by_time.setdefault(realized_at, []).append(realized_r)
    for realized_at in sorted(outcomes_by_time):
        realized_values = outcomes_by_time[realized_at]
        has_loss = any(value < 0 for value in realized_values)
        has_reset = any(value >= 0 for value in realized_values)
        if has_loss and has_reset:
            _append_once(unresolved_codes, "loss_order_ambiguous")
            continue
        if has_loss:
            losses += len(realized_values)
        else:
            losses = 0
    return {
        "consecutive_losses": losses,
        "streak_reliable": not unresolved_codes,
        "unresolved_codes": unresolved_codes,
        "utc_date": day.isoformat(),
    }


def _policy_value(policy: Mapping[str, Any], key: str) -> Optional[float]:
    value = policy.get(key, DEFAULT_RISK_POLICY[key])
    return _finite_number(value)


def evaluate_projected_risk(
    current_risk: Mapping[str, Any],
    candidate: Mapping[str, Any],
    policy: Optional[Mapping[str, Any]],
    outcomes: Optional[Iterable[Mapping[str, Any]]],
    now: Any,
) -> dict[str, Any]:
    """Evaluate caps from a known risk snapshot; equality remains allowed."""
    current_risk = current_risk if isinstance(current_risk, Mapping) else {}
    candidate = candidate if isinstance(candidate, Mapping) else {}
    policy_values = policy if isinstance(policy, Mapping) else DEFAULT_RISK_POLICY
    reasons: list[str] = []
    warnings: list[str] = [
        str(value)
        for value in current_risk.get("warnings", [])
        if _text(value)
    ]
    current_reliable = (
        current_risk.get("reliable") is True
        and not bool(current_risk.get("unresolved_codes"))
    )
    if not current_reliable:
        _append_once(reasons, "risk_state_unresolved")

    streak = consecutive_losses_today(outcomes, now)
    if not streak["streak_reliable"]:
        _append_once(reasons, "loss_streak_unresolved")

    net_liquidation = _positive_number(current_risk.get("net_liquidation"))
    total_current = _finite_number(current_risk.get("total_risk_usd"))
    candidate_risk = _finite_number(candidate.get("risk_usd"))
    direction = _token(candidate.get("direction"))
    raw_direction_current = current_risk.get("direction_risk_usd")
    direction_current = (
        raw_direction_current if isinstance(raw_direction_current, Mapping) else {}
    )
    long_current = _finite_number(direction_current.get("LONG"))
    short_current = _finite_number(direction_current.get("SHORT"))
    current_direction_risk = _finite_number(direction_current.get(direction))
    if (
        net_liquidation is None
        or total_current is None
        or total_current < 0
        or candidate_risk is None
        or candidate_risk < 0
        or direction not in {"LONG", "SHORT"}
        or current_direction_risk is None
        or current_direction_risk < 0
        or long_current is None
        or long_current < 0
        or short_current is None
        or short_current < 0
    ):
        _append_once(reasons, "risk_state_unresolved")
        if candidate_risk is None or candidate_risk < 0 or direction not in {"LONG", "SHORT"}:
            _append_once(reasons, "candidate_risk_invalid")
    elif not math.isclose(
        total_current,
        long_current + short_current,
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        _append_once(reasons, "risk_state_unresolved")

    projected_total_risk_usd = (
        total_current + candidate_risk
        if total_current is not None and candidate_risk is not None
        else None
    )
    projected_direction_risk_usd = (
        current_direction_risk + candidate_risk
        if current_direction_risk is not None and candidate_risk is not None
        else None
    )
    projected_total_risk_pct = (
        projected_total_risk_usd / net_liquidation * 100.0
        if projected_total_risk_usd is not None and net_liquidation is not None
        else None
    )
    projected_direction_risk_pct = (
        projected_direction_risk_usd / net_liquidation * 100.0
        if projected_direction_risk_usd is not None and net_liquidation is not None
        else None
    )

    group_key = _canonical_group_key(candidate.get("group_key"))
    group_verified = candidate.get("group_verified") is True
    projected_verified_group_risk_usd = None
    projected_verified_group_risk_pct = None
    if not group_verified:
        _append_once(warnings, "group_classification_unavailable")
    elif group_key is None:
        _append_once(warnings, "group_classification_unavailable")
    else:
        raw_groups = current_risk.get("verified_group_risk_usd")
        groups = raw_groups if isinstance(raw_groups, Mapping) else None
        current_group_risk = 0.0
        group_values_valid = groups is not None
        for raw_key, raw_value in (groups or {}).items():
            value = _finite_number(raw_value)
            if value is None or value < 0:
                group_values_valid = False
                break
            if _canonical_group_key(raw_key) != group_key:
                continue
            current_group_risk += value
        if not group_values_valid or candidate_risk is None:
            _append_once(reasons, "risk_state_unresolved")
        else:
            projected_verified_group_risk_usd = current_group_risk + candidate_risk
            if net_liquidation is not None:
                projected_verified_group_risk_pct = (
                    projected_verified_group_risk_usd / net_liquidation * 100.0
                )

    max_total = _policy_value(policy_values, "max_total_risk_pct")
    max_direction = _policy_value(policy_values, "max_direction_risk_pct")
    max_group = _policy_value(policy_values, "max_verified_group_risk_pct")
    max_losses = _policy_value(policy_values, "max_consecutive_losses")
    if any(value is None or value < 0 for value in (max_total, max_direction, max_group, max_losses)):
        _append_once(reasons, "risk_policy_invalid")
    elif "risk_state_unresolved" not in reasons:
        if (
            projected_total_risk_pct is not None
            and _strictly_exceeds(projected_total_risk_pct, max_total)
        ):
            _append_once(reasons, "max_total_risk_exceeded")
        if (
            projected_direction_risk_pct is not None
            and _strictly_exceeds(projected_direction_risk_pct, max_direction)
        ):
            _append_once(reasons, "max_direction_risk_exceeded")
        if (
            group_verified
            and group_key is not None
            and projected_verified_group_risk_pct is not None
            and _strictly_exceeds(projected_verified_group_risk_pct, max_group)
        ):
            _append_once(reasons, "max_verified_group_risk_exceeded")
        if streak["streak_reliable"] and streak["consecutive_losses"] >= max_losses:
            _append_once(reasons, "max_consecutive_losses_reached")

    return {
        "allowed": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "projected_total_risk_usd": projected_total_risk_usd,
        "projected_total_risk_pct": projected_total_risk_pct,
        "projected_direction_risk_usd": projected_direction_risk_usd,
        "projected_direction_risk_pct": projected_direction_risk_pct,
        "projected_verified_group_risk_usd": projected_verified_group_risk_usd,
        "projected_verified_group_risk_pct": projected_verified_group_risk_pct,
        "consecutive_losses_today": streak["consecutive_losses"],
        "loss_streak_reliable": streak["streak_reliable"],
        "loss_streak_unresolved_codes": streak["unresolved_codes"],
    }


__all__ = [
    "DEFAULT_RISK_POLICY",
    "aggregate_stop_risk",
    "consecutive_losses_today",
    "derive_intent_outcome",
    "evaluate_projected_risk",
    "summarize_hypothetical_batch_risk",
]
