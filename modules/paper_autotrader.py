"""Safety-first IBKR paper execution and broker reconciliation.

The scanner may propose setups, but this module is the only component allowed
to turn a proposal into an IBKR order. Local JSON is an audit/intents store;
positions, fills, open orders and account risk always come from the broker.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from modules.brokers import (
    Order,
    _IB_STATE_LOCK,
    _get_ib_state,
    ib_get_contract,
    ib_is_connected,
)
from modules.trade_levels import trade_geometry
from modules.trading_risk import DEFAULT_RISK_POLICY, derive_intent_outcome
from modules.trading_risk_store import TradingRiskStore


SCHEMA_VERSION = 2
_STORE_LOCK = threading.RLock()
_BASE_DATA_DIR = Path(
    os.environ.get("ALPHA_DATA_DIR", Path(__file__).resolve().parent.parent / "data_cache")
)
_DATA_DIR = _BASE_DATA_DIR / "autotrader"
_CONFIG_FILE = _DATA_DIR / "config.json"
_STATE_FILE = _DATA_DIR / "state.json"
_LOG_FILE = _DATA_DIR / "audit.json"
_STOP_FILE = _DATA_DIR / "stop.requested"


DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "mode": "paper_review",
    "paper_only": True,
    "execution_enabled": False,
    "kill_switch": True,
    "selected_account": "",
    "max_positions": 3,
    "risk_per_trade_pct": 0.25,
    "max_total_risk_pct": DEFAULT_RISK_POLICY["max_total_risk_pct"],
    "max_direction_risk_pct": DEFAULT_RISK_POLICY["max_direction_risk_pct"],
    "max_verified_group_risk_pct": DEFAULT_RISK_POLICY[
        "max_verified_group_risk_pct"
    ],
    "max_consecutive_losses": DEFAULT_RISK_POLICY["max_consecutive_losses"],
    "max_daily_loss_pct": 1.0,
    "max_notional_per_trade": 2000.0,
    "max_total_exposure_pct": 20.0,
    "max_shares": 5000,
    "min_available_funds": 500.0,
    "require_daily_pnl": True,
    "excluded_grades": ["A"],
    "min_bi_pct": 55,
    "min_smart_money": 2,
    "scan_interval_min": 15,
    "cooldown_days": 5,
    "trading_hours_only": True,
    "min_rr": 2.0,
    "max_tickers_scan": 300,
    "min_price": 5.0,
    "min_volume": 200000,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "stopped",
        "last_scan": None,
        "last_reconcile": None,
        "positions": [],
        "open_orders": [],
        "fills": [],
        "intents": [],
        "account": {},
        "daily_pnl": None,
        "daily_pnl_pct": None,
        "trades_today": 0,
        "cooldown_tickers": {},
        "broker_connected": False,
        "broker_error": None,
    }


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return fallback


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clamp_number(value: Any, low: float, high: float, *, integer: bool = False) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    number = max(low, min(high, number))
    return int(number) if integer else number


def normalize_config(candidate: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a strict, paper-only configuration with bounded risk values."""
    raw = dict(DEFAULT_CONFIG)
    if isinstance(candidate, dict):
        raw.update(candidate)

    config = dict(DEFAULT_CONFIG)
    config["mode"] = "paper_auto" if raw.get("mode") == "paper_auto" else "paper_review"
    config["paper_only"] = True
    config["execution_enabled"] = _as_bool(raw.get("execution_enabled", False))
    config["kill_switch"] = _as_bool(raw.get("kill_switch", True))
    config["selected_account"] = str(raw.get("selected_account") or "").strip().upper()[:32]
    config["max_positions"] = _clamp_number(raw.get("max_positions"), 1, 20, integer=True)
    config["risk_per_trade_pct"] = _clamp_number(raw.get("risk_per_trade_pct"), 0.05, 1.0)
    config["max_total_risk_pct"] = _clamp_number(
        raw.get("max_total_risk_pct"), 0.01, 10.0
    )
    config["max_direction_risk_pct"] = _clamp_number(
        raw.get("max_direction_risk_pct"), 0.01, 10.0
    )
    config["max_verified_group_risk_pct"] = _clamp_number(
        raw.get("max_verified_group_risk_pct"), 0.01, 10.0
    )
    config["max_consecutive_losses"] = _clamp_number(
        raw.get("max_consecutive_losses"), 1, 20, integer=True
    )
    config["max_daily_loss_pct"] = _clamp_number(raw.get("max_daily_loss_pct"), 0.25, 3.0)
    config["max_notional_per_trade"] = _clamp_number(
        raw.get("max_notional_per_trade"), 100.0, 100000.0
    )
    config["max_total_exposure_pct"] = _clamp_number(
        raw.get("max_total_exposure_pct"), 1.0, 50.0
    )
    config["max_shares"] = _clamp_number(raw.get("max_shares"), 1, 100000, integer=True)
    config["min_available_funds"] = _clamp_number(
        raw.get("min_available_funds"), 0.0, 1000000.0
    )
    config["require_daily_pnl"] = _as_bool(raw.get("require_daily_pnl", True))
    config["excluded_grades"] = [
        str(grade).upper() for grade in raw.get("excluded_grades", ["A"]) if str(grade).strip()
    ][:8]
    config["min_bi_pct"] = _clamp_number(raw.get("min_bi_pct"), 0, 100)
    config["min_smart_money"] = _clamp_number(
        raw.get("min_smart_money"), 0, 20, integer=True
    )
    config["scan_interval_min"] = _clamp_number(
        raw.get("scan_interval_min"), 1, 1440, integer=True
    )
    config["cooldown_days"] = _clamp_number(raw.get("cooldown_days"), 0, 90, integer=True)
    config["trading_hours_only"] = _as_bool(raw.get("trading_hours_only", True))
    config["min_rr"] = _clamp_number(raw.get("min_rr"), 1.0, 10.0)
    config["max_tickers_scan"] = _clamp_number(
        raw.get("max_tickers_scan"), 10, 5000, integer=True
    )
    config["min_price"] = _clamp_number(raw.get("min_price"), 0.01, 100000.0)
    config["min_volume"] = _clamp_number(raw.get("min_volume"), 0, 1000000000, integer=True)

    # Arming is a separate deliberate action. A saved config must never bypass
    # the kill switch, and review mode can never transmit orders.
    if config["kill_switch"] or config["mode"] != "paper_auto":
        config["execution_enabled"] = False
    config["schema_version"] = SCHEMA_VERSION
    return config


def config_load() -> Dict[str, Any]:
    with _STORE_LOCK:
        return normalize_config(_read_json(_CONFIG_FILE, {}))


_EXECUTION_CONFIG_FIELDS = {"mode", "paper_only", "execution_enabled", "kill_switch"}


def _config_save_internal(
    candidate: Dict[str, Any], *, allow_execution_state: bool = False
) -> Dict[str, Any]:
    with _STORE_LOCK:
        existing = config_load()
        updates = dict(candidate) if isinstance(candidate, dict) else {}
        if not allow_execution_state:
            for field in _EXECUTION_CONFIG_FIELDS:
                updates.pop(field, None)
        existing.update(updates)
        config = normalize_config(existing)
        _atomic_json_write(_CONFIG_FILE, config)
        return config


def config_save(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Save risk settings without allowing an implicit execution-state change."""
    return _config_save_internal(candidate, allow_execution_state=False)


def _risk_store() -> TradingRiskStore:
    """Open the independent durable-risk ledger for this local data directory."""
    return TradingRiskStore(_DATA_DIR / "trading_risk.sqlite")


def _risk_policy(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: config.get(key, DEFAULT_RISK_POLICY[key])
        for key in DEFAULT_RISK_POLICY
    }


def state_read() -> Dict[str, Any]:
    with _STORE_LOCK:
        state = _read_json(_STATE_FILE, {})
        result = _default_state()
        if isinstance(state, dict):
            result.update(state)
        result["schema_version"] = SCHEMA_VERSION
        for key in ("positions", "open_orders", "fills", "intents"):
            if not isinstance(result.get(key), list):
                result[key] = []
        if not isinstance(result.get("cooldown_tickers"), dict):
            result["cooldown_tickers"] = {}
        return result


def state_write(state: Dict[str, Any]) -> Dict[str, Any]:
    with _STORE_LOCK:
        payload = _default_state()
        payload.update(state if isinstance(state, dict) else {})
        payload["schema_version"] = SCHEMA_VERSION
        _atomic_json_write(_STATE_FILE, payload)
        return payload


def audit_log(message: str, level: str = "INFO", **context: Any) -> None:
    with _STORE_LOCK:
        entries = _read_json(_LOG_FILE, [])
        if not isinstance(entries, list):
            entries = []
        entry = {
            "time": _utc_now(),
            "level": str(level or "INFO").upper(),
            "msg": str(message),
        }
        if context:
            entry["context"] = context
        entries.append(entry)
        _atomic_json_write(_LOG_FILE, entries[-500:])


def audit_read(limit: int = 100) -> List[Dict[str, Any]]:
    entries = _read_json(_LOG_FILE, [])
    return entries[-max(1, min(int(limit), 500)) :] if isinstance(entries, list) else []


def stop_requested() -> bool:
    return _STOP_FILE.exists()


def request_stop() -> None:
    _STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STOP_FILE.write_text("stop\n", encoding="ascii")


def clear_stop() -> None:
    try:
        _STOP_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _contract_symbol(contract: Any) -> str:
    return str(getattr(contract, "symbol", "") or getattr(contract, "localSymbol", "")).upper()


def _order_ref(order: Any) -> str:
    return str(getattr(order, "orderRef", "") or "")


def _is_parent_order_ref(order_ref: Any) -> bool:
    return bool(re.search(r"-P\d*$", str(order_ref or "")))


def _account_value_snapshot(
    ib: Any,
    account: str,
    *,
    rows_override: Optional[Iterable[Any]] = None,
    require_daily_pnl_row: bool = True,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    rows: Iterable[Any]
    if rows_override is not None:
        rows = rows_override
    else:
        rows = []
        try:
            rows = ib.accountValues(account) if account else ib.accountValues()
        except Exception:
            try:
                rows = ib.accountSummary(account) if account else ib.accountSummary()
            except Exception:
                rows = []
    values: Dict[str, float] = {}
    priorities: Dict[str, int] = {}
    base_currency_candidates: set[str] = set()
    required_value_groups: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
        ("NetLiquidation", ("NETLIQUIDATION",)),
        ("AvailableFunds", ("AVAILABLEFUNDS", "FULLAVAILABLEFUNDS")),
        (
            "GrossPositionValue",
            ("GROSSPOSITIONVALUE", "STOCKMARKETVALUE"),
        ),
    )
    if require_daily_pnl_row:
        required_value_groups += (("DailyPnL", ("DAILYPNL",)),)
    tracked_value_tags = {
        normalized_tag
        for _label, aliases in required_value_groups
        for normalized_tag in aliases
    }
    required_value_rows: Dict[str, List[Tuple[str, str, float]]] = {
        normalized_tag: [] for normalized_tag in tracked_value_tags
    }
    for row in rows or []:
        if account and str(getattr(row, "account", "") or "") not in {"", account}:
            continue
        tag = str(getattr(row, "tag", "") or "")
        normalized_tag = re.sub(r"[^A-Z]", "", tag.upper())
        raw_value = str(getattr(row, "value", "") or "").strip().upper()
        if normalized_tag in {"CURRENCY", "BASECURRENCY", "ACCOUNTCURRENCY"}:
            if re.fullmatch(r"[A-Z]{3}", raw_value):
                base_currency_candidates.add(raw_value)
        value = _safe_float(getattr(row, "value", None))
        if value is not None and abs(value) >= sys.float_info.max / 2:
            value = None
        currency = str(getattr(row, "currency", "") or "").strip().upper()
        if normalized_tag in tracked_value_tags and value is not None:
            required_value_rows[normalized_tag].append((tag, currency, value))
        priority = 2 if currency in {"", "BASE"} else 1
        if tag and value is not None and priority >= priorities.get(tag, 0):
            values[tag] = value
            priorities[tag] = priority
    base_currency = (
        next(iter(base_currency_candidates))
        if len(base_currency_candidates) == 1
        else None
    )
    risk_value_currency_errors: List[str] = []
    for label, aliases in required_value_groups:
        selected_tag = next(
            (
                normalized_tag
                for normalized_tag in aliases
                if required_value_rows[normalized_tag]
            ),
            None,
        )
        candidates = required_value_rows[selected_tag] if selected_tag else []
        invalid_reason: Optional[str] = None
        if not candidates:
            invalid_reason = "missing"
        elif len(candidates) != 1:
            invalid_reason = "duplicate_or_conflicting_rows"
        else:
            _tag, row_currency, _value = candidates[0]
            resolved_currency = (
                base_currency if row_currency == "BASE" else row_currency or None
            )
            if base_currency is None:
                invalid_reason = "base_currency_unverified"
            elif resolved_currency != base_currency:
                invalid_reason = (
                    f"currency_{resolved_currency or 'missing'}_does_not_match_"
                    f"{base_currency}"
                )
        if invalid_reason is not None:
            risk_value_currency_errors.append(f"{label}:{invalid_reason}")
            for value_tag in list(values):
                if re.sub(r"[^A-Z]", "", value_tag.upper()) in aliases:
                    values.pop(value_tag, None)

    if len(base_currency_candidates) == 1:
        currency_evidence: Dict[str, Any] = {
            "base_currency": base_currency,
            "base_currency_evidence": "VERIFIED",
        }
    else:
        currency_evidence = {
            "base_currency": None,
            "base_currency_evidence": (
                "MISSING" if not base_currency_candidates else "AMBIGUOUS"
            ),
        }
    currency_evidence["risk_value_currency_evidence"] = (
        "VERIFIED"
        if not risk_value_currency_errors and base_currency is not None
        else (
            "MISSING"
            if base_currency is None
            or any(error.endswith(":missing") for error in risk_value_currency_errors)
            else "AMBIGUOUS"
        )
    )
    currency_evidence["risk_value_currency_errors"] = risk_value_currency_errors
    return values, currency_evidence


def _account_values(ib: Any, account: str) -> Dict[str, float]:
    return _account_value_snapshot(ib, account)[0]


def _daily_pnl(ib: Any, account: str, values: Dict[str, float]) -> Optional[float]:
    for key in ("DailyPnL", "Daily PnL"):
        if key in values:
            return values[key]
    try:
        pnl_rows = list(ib.pnl() or [])
        if not pnl_rows and hasattr(ib, "reqPnL"):
            ib.reqPnL(account)
            if hasattr(ib, "sleep"):
                ib.sleep(0.15)
            pnl_rows = list(ib.pnl() or [])
        for row in pnl_rows:
            if str(getattr(row, "account", "") or account) == account:
                value = _safe_float(getattr(row, "dailyPnL", None))
                if value is not None:
                    return value
    except Exception:
        return None
    return None


def _managed_accounts(ib: Any) -> List[str]:
    try:
        return sorted({str(account).strip().upper() for account in ib.managedAccounts() if account})
    except Exception:
        return []


def _select_paper_account(accounts: List[str], configured: str) -> Tuple[Optional[str], Optional[str]]:
    paper_accounts = [account for account in accounts if account.startswith("DU")]
    if configured:
        if configured not in accounts:
            return None, "Das konfigurierte IBKR-Konto ist nicht verbunden."
        if not configured.startswith("DU"):
            return None, "Live-Konto blockiert: AutoTrader V2 erlaubt nur IBKR-Paperkonten (DU...)."
        return configured, None
    if len(paper_accounts) == 1:
        return paper_accounts[0], None
    if not paper_accounts:
        return None, "Kein IBKR-Paperkonto (DU...) verbunden."
    return None, "Mehrere Paperkonten verbunden; selected_account muss eindeutig gesetzt werden."


def _serialize_position(position: Any) -> Dict[str, Any]:
    contract = getattr(position, "contract", None)
    quantity = _safe_float(getattr(position, "position", None)) or 0.0
    return {
        "account": str(getattr(position, "account", "") or ""),
        "ticker": _contract_symbol(contract),
        "con_id": int(getattr(contract, "conId", 0) or 0),
        "sec_type": str(getattr(contract, "secType", "") or ""),
        "currency": str(getattr(contract, "currency", "") or ""),
        "quantity": quantity,
        "direction": "LONG" if quantity > 0 else "SHORT" if quantity < 0 else "FLAT",
        "avg_cost": _safe_float(getattr(position, "avgCost", None)),
    }


def _serialize_trade(trade: Any) -> Dict[str, Any]:
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    return {
        "ticker": _contract_symbol(contract),
        "con_id": int(getattr(contract, "conId", 0) or 0),
        "order_id": int(getattr(order, "orderId", 0) or 0),
        "perm_id": int(getattr(order, "permId", 0) or 0),
        "client_id": int(getattr(order, "clientId", 0) or 0),
        "parent_id": int(getattr(order, "parentId", 0) or 0),
        "order_ref": _order_ref(order),
        "account": str(getattr(order, "account", "") or ""),
        "action": str(getattr(order, "action", "") or ""),
        "order_type": str(getattr(order, "orderType", "") or ""),
        "quantity": _safe_float(getattr(order, "totalQuantity", None)),
        "limit_price": _safe_float(getattr(order, "lmtPrice", None)),
        "stop_price": _safe_float(getattr(order, "auxPrice", None)),
        "aux_price": _normalized_order_price(getattr(order, "auxPrice", None)),
        "oca_group": str(getattr(order, "ocaGroup", "") or ""),
        "oca_type": int(_safe_float(getattr(order, "ocaType", 0)) or 0),
        "tif": str(getattr(order, "tif", "") or "").strip().upper(),
        "transmit": getattr(order, "transmit", False) is True,
        "outside_rth": getattr(order, "outsideRth", False) is True,
        "status": str(getattr(status, "status", "") or ""),
        "filled": _safe_float(getattr(status, "filled", None)) or 0.0,
        "remaining": _safe_float(getattr(status, "remaining", None)),
        "avg_fill_price": _safe_float(getattr(status, "avgFillPrice", None)),
    }


def _serialize_fill(fill: Any) -> Dict[str, Any]:
    execution = getattr(fill, "execution", None)
    contract = getattr(fill, "contract", None)
    fill_time = getattr(fill, "time", None)
    return {
        "ticker": _contract_symbol(contract),
        "con_id": int(getattr(contract, "conId", 0) or 0),
        "exec_id": str(getattr(execution, "execId", "") or ""),
        "order_id": int(getattr(execution, "orderId", 0) or 0),
        "perm_id": int(getattr(execution, "permId", 0) or 0),
        "client_id": int(getattr(execution, "clientId", 0) or 0),
        "account": str(getattr(execution, "acctNumber", "") or ""),
        "side": str(getattr(execution, "side", "") or ""),
        "shares": _safe_float(getattr(execution, "shares", None)),
        "price": _safe_float(getattr(execution, "price", None)),
        "time": fill_time.isoformat() if hasattr(fill_time, "isoformat") else str(fill_time or ""),
    }


def _intent_fill_flags(intent: Dict[str, Any], fills: List[Dict[str, Any]]) -> Tuple[bool, bool]:
    order_ids = {int(order_id) for order_id in intent.get("order_ids", []) if order_id}
    parent_ids = {int(order_id) for order_id in intent.get("parent_order_ids", []) if order_id}
    filled_ids = {
        int(fill.get("order_id") or 0)
        for fill in fills
        if int(fill.get("order_id") or 0) in order_ids
    }
    return bool(filled_ids & parent_ids), bool(filled_ids - parent_ids)


def _intent_status(
    intent: Dict[str, Any],
    orders: List[Dict[str, Any]],
    positions: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
) -> str:
    base_ref = str(intent.get("order_ref") or "")
    ticker = str(intent.get("ticker") or "").upper()
    known_order_ids = {
        int(value)
        for value in intent.get("order_ids", [])
        if int(_safe_float(value) or 0) > 0
    }
    exact_ref = re.compile(rf"^{re.escape(base_ref)}-(?:P|S|T)[1-9][0-9]*$") if base_ref else None
    matching = [
        order
        for order in orders
        if exact_ref is not None
        and exact_ref.fullmatch(str(order.get("order_ref") or ""))
        and (
            not known_order_ids
            or int(_safe_float(order.get("order_id")) or 0) in known_order_ids
        )
    ]
    parents = [order for order in matching if _is_parent_order_ref(order.get("order_ref"))]
    broker_position = next((position for position in positions if position.get("ticker") == ticker and position.get("quantity")), None)
    old_status = str(intent.get("status") or "UNKNOWN").upper()
    if old_status in {
        "RECONCILE_REQUIRED",
        "TERMINAL",
        "COMPLETE",
        "COMPLETED",
        "RELEASED",
        "DONE",
    }:
        return old_status
    parent_filled, exit_filled = _intent_fill_flags(intent, fills)
    if broker_position:
        return "ACTIVE"
    if parents:
        statuses = {str(parent.get("status") or "").upper() for parent in parents}
        if "FILLED" in statuses:
            return "FILLED_NO_POSITION"
        if statuses and statuses.issubset({"CANCELLED", "APICANCELLED", "INACTIVE"}):
            return "TERMINAL"
        if statuses & {"PENDINGSUBMIT", "PRESUBMITTED", "SUBMITTED", "PENDINGCANCEL"}:
            return "WORKING"
    matching_statuses = {str(order.get("status") or "").upper() for order in matching}
    if matching_statuses & {"PENDINGSUBMIT", "PRESUBMITTED", "SUBMITTED", "PENDINGCANCEL"}:
        return "FILLED_NO_POSITION" if parent_filled or intent.get("filled_at") else "WORKING"
    if exit_filled:
        return "TERMINAL"
    if old_status in {"ACTIVE", "FILLED_NO_POSITION"} and intent.get("filled_at"):
        return "TERMINAL"
    if parent_filled:
        return "FILLED_NO_POSITION" if not intent.get("filled_at") else "TERMINAL"
    if old_status in {"WORKING", "SUBMITTING"} and not matching:
        return "TERMINAL"
    return old_status


def _intent_broker_position_open(intent: Dict[str, Any], positions: List[Dict[str, Any]]) -> bool:
    account = str(intent.get("account") or "").upper()
    con_id = int(_safe_float(intent.get("con_id")) or 0)
    ticker = str(intent.get("ticker") or "").upper()
    for position in positions:
        if not position.get("quantity"):
            continue
        if account and str(position.get("account") or "").upper() != account:
            continue
        if con_id and int(_safe_float(position.get("con_id")) or 0) != con_id:
            continue
        if not con_id and ticker and str(position.get("ticker") or "").upper() != ticker:
            continue
        return True
    return False


def _intent_parent_orders_terminal(intent: Dict[str, Any], orders: List[Dict[str, Any]]) -> bool:
    parent_ids = {
        int(order_id)
        for order_id in intent.get("parent_order_ids", [])
        if int(_safe_float(order_id) or 0) > 0
    }
    if not parent_ids:
        return False
    active = {
        "PENDINGSUBMIT",
        "PRESUBMITTED",
        "SUBMITTED",
        "PENDINGCANCEL",
    }
    for order in orders:
        if int(_safe_float(order.get("order_id")) or 0) not in parent_ids:
            continue
        if str(order.get("status") or "").upper() in active:
            return False
    # ``openTrades`` is the broker's live-order snapshot.  An absent parent is
    # terminal only for outcome derivation; the fill ledger still has to prove
    # the actual execution quantities.
    return True


def _record_ledger_outcome(
    risk_store: TradingRiskStore,
    intent: Dict[str, Any],
    positions: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    *,
    orders_snapshot_complete: bool = False,
    snapshot_observed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Derive outcomes only from the durable fill ledger plus live snapshots."""
    setup_id = str(intent.get("setup_id") or "")
    if not setup_id:
        return {"accepted": True, "transition": "skipped"}
    evidence = risk_store.fill_evidence(setup_id)
    broker_position_open = _intent_broker_position_open(intent, positions)
    parent_orders_terminal = (
        orders_snapshot_complete
        and _intent_parent_orders_terminal(intent, orders)
    )
    outcome = derive_intent_outcome(
        intent,
        evidence.get("fills"),
        broker_position_open=broker_position_open,
        parent_orders_terminal=parent_orders_terminal,
    )
    outcome["fill_set_hash"] = evidence.get("fill_set_hash")
    existing_outcome: Optional[Dict[str, Any]] = None
    load_outcome = getattr(risk_store, "load_outcome", None)
    if callable(load_outcome):
        existing_outcome = load_outcome(setup_id)
    if isinstance(existing_outcome, dict) and existing_outcome.get("complete") is True:
        same_fill_set = (
            existing_outcome.get("fill_set_hash") == evidence.get("fill_set_hash")
        )
        terminal_snapshot_consistent = (
            orders_snapshot_complete
            and broker_position_open is False
            and parent_orders_terminal is True
        )
        if same_fill_set and terminal_snapshot_consistent:
            # COMPLETE is immutable.  Replaying it with a fresh observed_at
            # would create a different terminal-evidence hash, so a matching
            # durable fill set is acknowledged without another write.
            result = {
                "accepted": True,
                "idempotent": True,
                "conflict": None,
                "transition": "idempotent",
            }
        elif not same_fill_set:
            result = {
                "accepted": False,
                "idempotent": False,
                "conflict": "outcome_complete_fill_evidence_changed",
                "transition": "rejected",
            }
        else:
            result = {
                "accepted": False,
                "idempotent": False,
                "conflict": "complete_terminal_snapshot_contradiction",
                "transition": "rejected",
            }
    elif outcome.get("complete") is not True:
        # UNRESOLVED remains a ledger observation.  It neither releases risk
        # nor needs the terminal broker-snapshot authority below.
        result = risk_store.record_outcome(
            outcome,
            broker_position_open=broker_position_open,
            parent_orders_terminal=parent_orders_terminal,
        )
    else:
        reservation_id = str(intent.get("risk_reservation_id") or "")
        observed_at = snapshot_observed_at
        open_order_ids: set[int] = set()
        open_order_ids_valid = True
        for order in orders:
            order_id = _safe_float(order.get("order_id"))
            if (
                order_id is None
                or order_id <= 0
                or not float(order_id).is_integer()
            ):
                open_order_ids_valid = False
                break
            open_order_ids.add(int(order_id))
        if (
            not reservation_id
            or not orders_snapshot_complete
            or not open_order_ids_valid
            or broker_position_open
            or not parent_orders_terminal
            or not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            result = {
                "accepted": False,
                "idempotent": False,
                "conflict": "outcome_terminal_evidence_unavailable",
                "transition": "rejected",
            }
        else:
            lease_key = f"submit:{setup_id}"
            owner_token = f"paper-autotrader-reconcile:{uuid.uuid4().hex}"
            lease_now = datetime.now(timezone.utc)
            try:
                lease = risk_store.acquire_lease(
                    lease_key,
                    owner_token,
                    now=lease_now,
                    ttl_seconds=_SUBMIT_LEASE_TTL_SECONDS,
                )
            except Exception as exc:
                lease = {"acquired": False, "reason": str(exc)[:120]}
            if not lease.get("acquired"):
                result = {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "outcome_terminal_lease_unavailable",
                    "transition": "rejected",
                }
            else:
                fence_token = int(lease["fence_token"])
                record_now = datetime.now(timezone.utc)
                try:
                    renewed = risk_store.renew_lease(
                        lease_key,
                        owner_token,
                        fence_token,
                        now=record_now,
                        ttl_seconds=_SUBMIT_LEASE_TTL_SECONDS,
                    )
                except Exception as exc:
                    renewed = {"renewed": False, "reason": str(exc)[:120]}
                if not renewed.get("renewed"):
                    result = {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_terminal_lease_fenced",
                        "transition": "rejected",
                    }
                else:
                    terminal_evidence = {
                        "snapshot_complete": True,
                        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
                        "account": str(intent.get("account") or "").upper(),
                        "con_id": int(_safe_float(intent.get("con_id")) or 0),
                        "position_open": False,
                        "open_order_ids": sorted(open_order_ids),
                        "open_orders": [dict(order) for order in orders],
                    }
                    result = risk_store.record_outcome(
                        outcome,
                        broker_position_open=broker_position_open,
                        parent_orders_terminal=parent_orders_terminal,
                        reservation_id=reservation_id,
                        lease_key=lease_key,
                        owner_token=owner_token,
                        fence_token=fence_token,
                        now=record_now,
                        terminal_evidence=terminal_evidence,
                    )
    result = dict(result)
    evidence_codes = list(evidence.get("unresolved_codes") or [])
    terminal_snapshot_invalid = result.get("conflict") in {
        "outcome_terminal_evidence_unavailable",
        "complete_terminal_snapshot_contradiction",
    }
    if terminal_snapshot_invalid:
        evidence_codes.append("terminal_snapshot_invalid")
    result["evidence_reliable"] = (
        evidence.get("reliable") is True and not terminal_snapshot_invalid
    )
    result["evidence_unresolved_codes"] = list(dict.fromkeys(evidence_codes))
    intent["fill_set_hash"] = evidence.get("fill_set_hash")
    return result


def _broker_error_is_request_failure(error_code: Any) -> bool:
    try:
        code = int(error_code)
    except (TypeError, ValueError):
        return True
    # Only explicitly benign farm-status notifications may be ignored.  In
    # particular 2110 means TWS has lost its upstream IB-server connection;
    # the local API socket can remain connected, so ib_is_connected() cannot
    # safely clear that condition.
    benign_status_codes = {2104, 2106, 2107, 2108, 2158}
    return code not in benign_status_codes


def _bounded_fresh_broker_snapshot(ib: Any, account: str) -> Dict[str, Any]:
    """Fetch one serialized, causal server snapshot for risk authorization."""
    if not str(account or "").startswith("DU"):
        raise ValueError("fresh broker snapshot requires an exact paper account")

    # ib_insync exposes global events and several string-keyed request futures.
    # Serializing the complete collection window prevents two local fresh
    # snapshots from overwriting futures or attributing each other's events.
    with _IB_STATE_LOCK:
        run_bounded = getattr(ib, "run", None)
        error_event = getattr(ib, "errorEvent", None)
        pnl_event = getattr(ib, "pnlEvent", None)
        client = getattr(ib, "client", None)
        wrapper = getattr(ib, "wrapper", None)
        requests = {
            "orders": getattr(ib, "reqAllOpenOrdersAsync", None),
            "positions": getattr(ib, "reqPositionsAsync", None),
        }
        required_callables = (
            run_bounded,
            getattr(client, "getReqId", None),
            getattr(client, "reqExecutions", None),
            getattr(client, "reqAccountUpdatesMulti", None),
            getattr(client, "cancelAccountUpdatesMulti", None),
            getattr(client, "reqPnL", None),
            getattr(client, "cancelPnL", None),
            getattr(wrapper, "startReq", None),
            getattr(wrapper, "accountUpdateMulti", None),
        )
        if (
            error_event is None
            or pnl_event is None
            or any(not callable(method) for method in requests.values())
            or any(not callable(method) for method in required_callables)
        ):
            raise RuntimeError("bounded fresh broker snapshot is unavailable")

        snapshot_started_at = datetime.now(timezone.utc)
        request_errors: List[str] = []
        active_request_ids: set[int] = set()

        def capture_error(
            request_id: Any,
            error_code: Any,
            error_string: Any,
            *_args: Any,
        ) -> None:
            if not _broker_error_is_request_failure(error_code):
                return
            try:
                normalized_request_id = int(request_id)
            except (TypeError, ValueError):
                normalized_request_id = None
            # A positive id is request-scoped.  Only the exact active request
            # can invalidate this window; errors from unrelated IB activity
            # must not be attributed to it.  Global/no-id errors remain fatal.
            if (
                normalized_request_id is not None
                and normalized_request_id > 0
                and normalized_request_id not in active_request_ids
            ):
                return
            request_errors.append(
                f"{str(request_id)[:20]}/{str(error_code)[:20]}:"
                f"{str(error_string)[:120]}"
            )

        def raise_request_errors() -> None:
            if request_errors:
                raise RuntimeError(
                    "fresh broker snapshot error: "
                    + "; ".join(request_errors)[:160]
                )

        subscribed = False
        snapshot: Dict[str, Any] = {}
        try:
            error_event += capture_error
            subscribed = True
            for key in ("orders", "positions"):
                active_request_ids.clear()
                try:
                    raw = run_bounded(
                        requests[key](),
                        timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS,
                    )
                    raise_request_errors()
                    if raw is None:
                        raise ValueError(f"fresh {key} snapshot returned None")
                    snapshot[key] = list(raw)
                finally:
                    active_request_ids.clear()

            try:
                from ib_insync import ExecutionFilter
            except Exception as exc:
                raise RuntimeError(
                    "bounded fresh executions request is unavailable"
                ) from exc
            fills_request_id = int(client.getReqId())
            if fills_request_id <= 0:
                raise ValueError("fresh fills request id is invalid")
            fills_future: Any = None
            active_request_ids.clear()
            active_request_ids.add(fills_request_id)
            try:
                fills_future = wrapper.startReq(fills_request_id)
                if fills_future is None:
                    raise ValueError("fresh fills request returned no future")
                client.reqExecutions(fills_request_id, ExecutionFilter())
                raw_fills = run_bounded(
                    fills_future,
                    timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS,
                )
                raise_request_errors()
                if raw_fills is None:
                    raise ValueError("fresh fills snapshot returned None")
                snapshot["fills"] = list(raw_fills)
            finally:
                active_request_ids.clear()
                pending_futures = getattr(wrapper, "_futures", None)
                if (
                    isinstance(pending_futures, dict)
                    and pending_futures.get(fills_request_id) is fills_future
                ):
                    pending_futures.pop(fills_request_id, None)
                    pending_results = getattr(wrapper, "_results", None)
                    if isinstance(pending_results, dict):
                        pending_results.pop(fills_request_id, None)

            account_request_id = int(client.getReqId())
            if account_request_id <= 0:
                raise ValueError("fresh account request id is invalid")
            account_rows: List[Any] = []
            account_observations: List[datetime] = []
            account_capture_errors: List[str] = []
            account_window_started_at = datetime.now(timezone.utc)
            original_account_update = wrapper.accountUpdateMulti
            wrapper_dict = getattr(wrapper, "__dict__", {})
            had_instance_account_update = "accountUpdateMulti" in wrapper_dict
            prior_instance_account_update = wrapper_dict.get("accountUpdateMulti")
            account_cancel_error: Optional[Exception] = None

            def capture_account_update(
                request_id: Any,
                observed_account: Any,
                model_code: Any,
                tag: Any,
                value: Any,
                currency: Any,
            ) -> Any:
                observed_at = datetime.now(timezone.utc)
                if int(request_id) == account_request_id:
                    normalized_account = str(observed_account or "").strip().upper()
                    normalized_model = str(model_code or "").strip()
                    normalized_tag = str(tag or "").strip()
                    if normalized_account != account:
                        account_capture_errors.append(
                            "fresh account row belongs to a foreign account"
                        )
                    elif normalized_model:
                        account_capture_errors.append(
                            "fresh account row belongs to a foreign model"
                        )
                    elif not normalized_tag:
                        account_capture_errors.append(
                            "fresh account row has no tag"
                        )
                    else:
                        account_rows.append(
                            SimpleNamespace(
                                account=normalized_account,
                                modelCode=normalized_model,
                                tag=normalized_tag,
                                value=value,
                                currency=str(currency or "").strip().upper(),
                            )
                        )
                        account_observations.append(observed_at)
                return original_account_update(
                    request_id,
                    observed_account,
                    model_code,
                    tag,
                    value,
                    currency,
                )

            setattr(wrapper, "accountUpdateMulti", capture_account_update)
            active_request_ids.clear()
            active_request_ids.add(account_request_id)
            account_future: Any = None
            try:
                account_future = wrapper.startReq(account_request_id)
                if account_future is None:
                    raise ValueError("fresh account request returned no future")
                client.reqAccountUpdatesMulti(
                    account_request_id,
                    account,
                    "",
                    False,
                )
                run_bounded(
                    account_future,
                    timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS,
                )
                raise_request_errors()
            finally:
                try:
                    client.cancelAccountUpdatesMulti(account_request_id)
                except Exception as exc:
                    account_cancel_error = exc
                if had_instance_account_update:
                    setattr(
                        wrapper,
                        "accountUpdateMulti",
                        prior_instance_account_update,
                    )
                else:
                    try:
                        delattr(wrapper, "accountUpdateMulti")
                    except AttributeError:
                        pass
                active_request_ids.clear()
                pending_futures = getattr(wrapper, "_futures", None)
                if (
                    isinstance(pending_futures, dict)
                    and pending_futures.get(account_request_id) is account_future
                ):
                    pending_futures.pop(account_request_id, None)
                    pending_results = getattr(wrapper, "_results", None)
                    if isinstance(pending_results, dict):
                        pending_results.pop(account_request_id, None)
            if account_cancel_error is not None:
                raise RuntimeError(
                    "fresh account request cleanup failed: "
                    + str(account_cancel_error)[:120]
                )
            raise_request_errors()
            account_window_completed_at = datetime.now(timezone.utc)
            if account_capture_errors:
                raise ValueError("; ".join(account_capture_errors)[:160])
            if not account_rows or not account_observations:
                raise ValueError("fresh account request returned no timestamped rows")
            if any(
                observed_at < account_window_started_at
                or observed_at > account_window_completed_at
                for observed_at in account_observations
            ):
                raise ValueError("fresh account row is outside the request window")
            ready_rows = [
                row
                for row in account_rows
                if re.sub(r"[^A-Z]", "", str(row.tag).upper())
                == "ACCOUNTREADY"
            ]
            if len(ready_rows) != 1 or str(ready_rows[0].value).strip().lower() != "true":
                raise ValueError("fresh account snapshot is not ready")
            if not ib_is_connected():
                raise ConnectionError(
                    "IBKR connection lost during fresh account snapshot"
                )
            snapshot["account_values"] = account_rows
            snapshot["account_values_observed_at"] = max(account_observations)

            pnl_objects = getattr(wrapper, "reqId2PnL", None)
            if not isinstance(pnl_objects, dict):
                raise RuntimeError("fresh PnL request registry is unavailable")
            pnl_observations: List[Tuple[Any, datetime]] = []
            pnl_capture_errors: List[str] = []
            pnl_window_started_at = datetime.now(timezone.utc)
            pnl_request_id = int(client.getReqId())
            if pnl_request_id <= 0 or pnl_request_id in pnl_objects:
                raise ValueError("fresh PnL request id is invalid or already active")
            created_pnl_object = SimpleNamespace(
                account=account,
                modelCode="",
                dailyPnL=float("nan"),
                unrealizedPnL=float("nan"),
                realizedPnL=float("nan"),
            )
            matched_pnl_event = type(pnl_event)()
            pnl_cancel_error: Optional[Exception] = None
            pnl_request_attempted = False

            def capture_pnl(row: Any) -> None:
                # Global pnlEvent can contain queued rows from an older or
                # concurrent subscription.  Only the object installed under
                # this request's unique id may complete the local waiter.
                if row is not created_pnl_object:
                    return
                observed_at = datetime.now(timezone.utc)
                row_account = str(getattr(row, "account", "") or "").strip().upper()
                row_model = str(getattr(row, "modelCode", "") or "").strip()
                if row_account != account or row_model:
                    pnl_capture_errors.append(
                        "fresh PnL event is not bound to the selected account"
                    )
                else:
                    pnl_observations.append((row, observed_at))
                matched_pnl_event.emit(row)

            pnl_event += capture_pnl
            pnl_objects[pnl_request_id] = created_pnl_object
            active_request_ids.clear()
            active_request_ids.add(pnl_request_id)
            try:
                pnl_request_attempted = True
                client.reqPnL(pnl_request_id, account, "")
                pnl_row = run_bounded(
                    matched_pnl_event,
                    timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS,
                )
                raise_request_errors()
            finally:
                pnl_event -= capture_pnl
                if pnl_request_attempted:
                    try:
                        client.cancelPnL(pnl_request_id)
                    except Exception as exc:
                        pnl_cancel_error = exc
                if pnl_objects.get(pnl_request_id) is created_pnl_object:
                    pnl_objects.pop(pnl_request_id, None)
                active_request_ids.clear()
            if pnl_cancel_error is not None:
                raise RuntimeError(
                    "fresh PnL request cleanup failed: "
                    + str(pnl_cancel_error)[:120]
                )
            raise_request_errors()
            pnl_window_completed_at = datetime.now(timezone.utc)
            if pnl_capture_errors:
                raise ValueError("; ".join(pnl_capture_errors)[:160])
            if pnl_row is None:
                raise ValueError("fresh PnL event returned None")
            if pnl_row is not created_pnl_object:
                raise ValueError("fresh PnL event is not bound to its request")
            matching_observations = [
                observed_at
                for observed_row, observed_at in pnl_observations
                if observed_row is created_pnl_object
            ]
            if not matching_observations:
                raise ValueError("fresh PnL event has no causal timestamp")
            if any(
                observed_at < pnl_window_started_at
                or observed_at > pnl_window_completed_at
                for observed_at in matching_observations
            ):
                raise ValueError("fresh PnL event is outside the request window")
            daily_pnl = _safe_float(getattr(pnl_row, "dailyPnL", None))
            if daily_pnl is None or abs(daily_pnl) >= sys.float_info.max / 2:
                raise ValueError("fresh DailyPnL is missing or unset")
            if not ib_is_connected():
                raise ConnectionError(
                    "IBKR connection lost during fresh PnL snapshot"
                )
            snapshot["daily_pnl"] = daily_pnl
            snapshot["daily_pnl_observed_at"] = matching_observations[-1]
            snapshot["started_at"] = snapshot_started_at
            snapshot["observed_at"] = datetime.now(timezone.utc)
            return snapshot
        finally:
            if subscribed:
                error_event -= capture_error


def reconcile_broker(
    *,
    require_fresh: bool = False,
    expected_recovery_generation: Optional[int] = None,
) -> Dict[str, Any]:
    """Replace local market truth with the current IBKR paper account snapshot."""
    state = state_read()
    config = config_load()
    snapshot_started_at = datetime.now(timezone.utc)
    snapshot_observed_at = snapshot_started_at
    state["last_reconcile"] = snapshot_observed_at.isoformat()
    if not ib_is_connected():
        state.update({
            "broker_connected": False,
            "broker_error": "IBKR ist nicht verbunden.",
            "positions": [],
            "open_orders": [],
            "fills": [],
            "account": {},
            "daily_pnl": None,
            "daily_pnl_pct": None,
        })
        state_write(state)
        return state

    ib = _get_ib_state().get("ib")
    if not ib:
        state["broker_connected"] = False
        state["broker_error"] = "IBKR-Verbindungsobjekt fehlt."
        return state_write(state)

    accounts = _managed_accounts(ib)
    account, account_error = _select_paper_account(accounts, config.get("selected_account", ""))
    if account_error:
        state.update({
            "broker_connected": True,
            "broker_error": account_error,
            "account": {"managed_accounts": accounts, "selected": None, "paper": False},
            "positions": [],
            "open_orders": [],
            "fills": [],
            "daily_pnl": None,
            "daily_pnl_pct": None,
        })
        return state_write(state)

    fresh_snapshot: Optional[Dict[str, Any]] = None
    fresh_snapshot_error = ""
    if require_fresh:
        try:
            fresh_snapshot = _bounded_fresh_broker_snapshot(ib, account)
            snapshot_started_at = fresh_snapshot["started_at"]
            snapshot_observed_at = fresh_snapshot["observed_at"]
            if (
                not isinstance(snapshot_started_at, datetime)
                or snapshot_started_at.tzinfo is None
                or not isinstance(snapshot_observed_at, datetime)
                or snapshot_observed_at.tzinfo is None
                or snapshot_observed_at < snapshot_started_at
            ):
                raise ValueError("fresh broker snapshot timestamp is invalid")
            state["last_reconcile"] = snapshot_observed_at.isoformat()
        except Exception as exc:
            fresh_snapshot_error = str(exc)[:160]
            fresh_snapshot = None

    risk_evidence_errors: List[str] = []
    positions_snapshot_complete = True
    try:
        if require_fresh:
            if fresh_snapshot is None:
                raise RuntimeError(fresh_snapshot_error or "fresh snapshot unavailable")
            raw_positions = fresh_snapshot["positions"]
        else:
            try:
                raw_positions = ib.positions(account) if account else ib.positions()
            except TypeError:
                raw_positions = ib.positions()
        if raw_positions is None:
            raise ValueError("positions provider returned None")
        raw_positions = list(raw_positions)
        positions = [
            _serialize_position(position)
            for position in raw_positions
            if str(getattr(position, "account", "") or account) == account
        ]
    except Exception as exc:
        raw_positions = []
        positions = []
        positions_snapshot_complete = False
        risk_evidence_errors.append(
            f"IBKR Positions-Snapshot fehlgeschlagen: {str(exc)[:160]}"
        )
    orders_snapshot_complete = True
    orders_snapshot_error = ""
    try:
        if require_fresh:
            if fresh_snapshot is None:
                raise RuntimeError(fresh_snapshot_error or "fresh snapshot unavailable")
            raw_trades = fresh_snapshot["orders"]
        else:
            raw_trades = ib.openTrades()
        if raw_trades is None:
            raise ValueError("openTrades provider returned None")
        trades = list(raw_trades)
        orders = [
            _serialize_trade(trade)
            for trade in trades
            if str(getattr(getattr(trade, "order", None), "account", "") or account)
            == account
        ]
    except Exception as exc:
        trades = []
        orders = list(state.get("open_orders", []))
        orders_snapshot_complete = False
        orders_snapshot_error = f"IBKR Open-Order-Snapshot fehlgeschlagen: {str(exc)[:160]}"
        risk_evidence_errors.append(orders_snapshot_error)
    fills_snapshot_complete = True
    try:
        if require_fresh:
            if fresh_snapshot is None:
                raise RuntimeError(fresh_snapshot_error or "fresh snapshot unavailable")
            raw_fills = fresh_snapshot["fills"]
        else:
            raw_fills = ib.fills()
        if raw_fills is None:
            raise ValueError("fills provider returned None")
        raw_fills = list(raw_fills)
        fills = [
            _serialize_fill(fill)
            for fill in raw_fills
            if str(getattr(getattr(fill, "execution", None), "acctNumber", "") or account)
            == account
        ]
    except Exception as exc:
        raw_fills = []
        fills = []
        fills_snapshot_complete = False
        risk_evidence_errors.append(
            f"IBKR Fill-Snapshot fehlgeschlagen: {str(exc)[:160]}"
        )
    if require_fresh and fresh_snapshot is None:
        snapshot_observed_at = datetime.now(timezone.utc)
        state["last_reconcile"] = snapshot_observed_at.isoformat()
    # The JSON state keeps a bounded display cache only.  Broker fills are
    # first appended to the durable, globally idempotent ledger, which also
    # accepts an early fill before its order mapping exists.
    try:
        risk_store: Optional[TradingRiskStore] = _risk_store()
    except Exception as exc:
        risk_store = None
        message = f"Risk-Ledger nicht verfuegbar: {str(exc)[:160]}"
        risk_evidence_errors.append(message)
        audit_log("Risk ledger unavailable during reconcile", "ERROR", error=str(exc)[:200])
    terminal_exposure_conflicts: Dict[str, str] = {}
    durable_terminal_setup_ids: set[str] = set()
    position_setup_ids: set[str] = set()
    order_observation_reliable = False
    if risk_store is not None:
        for fill in fills:
            try:
                append_result = risk_store.append_fill(fill)
                if (
                    not append_result.get("accepted")
                    or append_result.get("conflict")
                    or append_result.get("mapping_pending") is True
                ):
                    risk_evidence_errors.append(
                        "Risk-Fill-Append abgelehnt: "
                        + str(
                            append_result.get("conflict")
                            or (
                                "mapping_pending"
                                if append_result.get("mapping_pending") is True
                                else "unknown"
                            )
                        )[:120]
                    )
            except Exception as exc:
                risk_evidence_errors.append(
                    f"Risk-Fill-Append fehlgeschlagen: {str(exc)[:160]}"
                )
                audit_log("Risk fill ledger append failed", "ERROR", error=str(exc)[:200])
        if orders_snapshot_complete:
            risk_evidence_errors.extend(
                _recover_restart_mappings(
                    risk_store,
                    trades,
                    account,
                    state.get("intents", []),
                )
            )
        try:
            observation = risk_store.observe_open_orders(
                orders,
                account=account,
                snapshot_complete=orders_snapshot_complete,
                positions=positions,
                positions_snapshot_complete=positions_snapshot_complete,
                fills_snapshot_complete=fills_snapshot_complete,
                observed_at=snapshot_observed_at,
            )
            if not observation.get("accepted"):
                risk_evidence_errors.append(
                    "Risk-Order-Snapshot abgelehnt: "
                    + str(observation.get("reason") or "unknown")[:120]
                )
            else:
                order_observation_reliable = True
                durable_terminal_setup_ids = {
                    str(setup_id)
                    for setup_id in observation.get("terminal_setup_ids") or []
                    if str(setup_id)
                }
                position_setup_ids = {
                    str(setup_id)
                    for setup_id in observation.get("position_setup_ids") or []
                    if str(setup_id)
                }
            for conflict in observation.get("conflicts") or []:
                setup_id = str(conflict.get("setup_id") or "")
                conflict_kind = str(conflict.get("conflict") or "")
                if setup_id and conflict_kind:
                    terminal_exposure_conflicts[setup_id] = conflict_kind
                    risk_evidence_errors.append(
                        f"Risk-Order-Evidence konfliktbehaftet: {conflict_kind[:120]}"
                    )
        except Exception as exc:
            risk_evidence_errors.append(
                f"Risk-Order-Snapshot fehlgeschlagen: {str(exc)[:160]}"
            )

    account_snapshot_complete = False
    pnl_snapshot_complete = False
    risk_values_observed_at: Optional[str] = None
    daily_pnl_observed_at: Optional[str] = None
    if require_fresh:
        # A failed fresh request must never fall back to ib_insync's caches.
        if fresh_snapshot is None:
            values, currency_evidence = _account_value_snapshot(
                ib,
                account,
                rows_override=[],
                require_daily_pnl_row=False,
            )
            daily_pnl = None
            risk_evidence_errors.append(
                "IBKR Fresh-Risk-Snapshot fehlgeschlagen: "
                + (fresh_snapshot_error or "unbekannt")[:160]
            )
        else:
            try:
                raw_account_values = fresh_snapshot.get("account_values")
                if raw_account_values is None:
                    raise ValueError("fresh account values returned None")
                values, currency_evidence = _account_value_snapshot(
                    ib,
                    account,
                    rows_override=list(raw_account_values),
                    require_daily_pnl_row=False,
                )
                account_observed_at = fresh_snapshot.get(
                    "account_values_observed_at"
                )
                pnl_observed_at = fresh_snapshot.get("daily_pnl_observed_at")
                if (
                    not isinstance(account_observed_at, datetime)
                    or account_observed_at.tzinfo is None
                    or not isinstance(pnl_observed_at, datetime)
                    or pnl_observed_at.tzinfo is None
                    or account_observed_at < snapshot_started_at
                    or pnl_observed_at < snapshot_started_at
                    or account_observed_at > snapshot_observed_at
                    or pnl_observed_at > snapshot_observed_at
                ):
                    raise ValueError("fresh risk evidence timestamp is invalid")
                daily_pnl = _safe_float(fresh_snapshot.get("daily_pnl"))
                if (
                    daily_pnl is None
                    or abs(daily_pnl) >= sys.float_info.max / 2
                ):
                    raise ValueError("fresh DailyPnL is missing or unset")
                net_liquidation_candidate = values.get("NetLiquidation")
                available_funds_candidate = values.get(
                    "AvailableFunds", values.get("FullAvailableFunds")
                )
                gross_position_value_candidate = values.get(
                    "GrossPositionValue", values.get("StockMarketValue")
                )
                account_snapshot_complete = (
                    currency_evidence.get("base_currency_evidence")
                    == "VERIFIED"
                    and str(currency_evidence.get("base_currency") or "").upper()
                    == "USD"
                    and currency_evidence.get("risk_value_currency_evidence")
                    == "VERIFIED"
                    and net_liquidation_candidate is not None
                    and available_funds_candidate is not None
                    and gross_position_value_candidate is not None
                )
                pnl_snapshot_complete = account_snapshot_complete
                risk_values_observed_at = account_observed_at.isoformat()
                daily_pnl_observed_at = pnl_observed_at.isoformat()
                if not account_snapshot_complete:
                    risk_evidence_errors.append(
                        "IBKR Fresh-Account-Snapshot unvollstaendig oder nicht USD: "
                        + "; ".join(
                            currency_evidence.get("risk_value_currency_errors")
                            or ["account_risk_values_unverified"]
                        )[:160]
                    )
            except Exception as exc:
                values, currency_evidence = _account_value_snapshot(
                    ib,
                    account,
                    rows_override=[],
                    require_daily_pnl_row=False,
                )
                daily_pnl = None
                risk_evidence_errors.append(
                    f"IBKR Fresh-Risk-Evidence fehlgeschlagen: {str(exc)[:160]}"
                )
        currency_evidence["daily_pnl_currency_evidence"] = (
            "VERIFIED" if pnl_snapshot_complete else "MISSING"
        )
    else:
        values, currency_evidence = _account_value_snapshot(ib, account)
        daily_pnl = _daily_pnl(ib, account, values)
    net_liquidation = values.get("NetLiquidation")
    available_funds = values.get("AvailableFunds", values.get("FullAvailableFunds"))
    buying_power = values.get("BuyingPower")
    gross_position_value = values.get("GrossPositionValue")
    if gross_position_value is None and values.get("StockMarketValue") is not None:
        gross_position_value = abs(values["StockMarketValue"])
    daily_pnl_pct = (
        daily_pnl / net_liquidation * 100.0
        if daily_pnl is not None and net_liquidation and net_liquidation > 0
        else None
    )

    intents = state.get("intents", [])
    cooldown_tickers = state.get("cooldown_tickers", {})
    today = datetime.now(timezone.utc).date().isoformat()
    for intent in intents:
        setup_id = str(intent.get("setup_id") or "")
        terminal_exposure_conflict = terminal_exposure_conflicts.get(setup_id)
        durable_terminal = setup_id in durable_terminal_setup_ids
        attributed_positions = positions if setup_id in position_setup_ids else []
        parent_filled, _ = _intent_fill_flags(intent, fills)
        if (
            orders_snapshot_complete
            and order_observation_reliable
            and not terminal_exposure_conflict
            and not durable_terminal
        ):
            intent["status"] = _intent_status(
                intent, orders, attributed_positions, fills
            )
        elif durable_terminal:
            intent["status"] = "TERMINAL"
        first_fill = (
            (
                parent_filled
                or str(intent.get("status") or "").upper()
                in {"ACTIVE", "FILLED_NO_POSITION"}
            )
            and not intent.get("filled_at")
            and not durable_terminal
        )
        if first_fill:
            intent["filled_at"] = state["last_reconcile"]
            ticker = str(intent.get("ticker") or "").upper()
            if ticker:
                cooldown_tickers[ticker] = today
        if risk_store is not None:
            if terminal_exposure_conflict:
                intent["risk_evidence_error"] = terminal_exposure_conflict
                intent["reconciled_at"] = state["last_reconcile"]
                continue
            if durable_terminal:
                intent.pop("risk_evidence_error", None)
                intent["reconciled_at"] = state["last_reconcile"]
                continue
            try:
                outcome_result = _record_ledger_outcome(
                    risk_store,
                    intent,
                    attributed_positions,
                    orders,
                    orders_snapshot_complete=(
                        orders_snapshot_complete
                        and positions_snapshot_complete
                        and fills_snapshot_complete
                        and order_observation_reliable
                    ),
                    snapshot_observed_at=snapshot_observed_at,
                )
                if outcome_result.get("evidence_reliable") is False:
                    evidence_codes = ",".join(
                        str(code)
                        for code in outcome_result.get(
                            "evidence_unresolved_codes", []
                        )
                    ) or "fill_evidence_unreliable"
                    message = f"Risk-Fill-Evidence unzuverlaessig: {evidence_codes[:120]}"
                    intent["risk_evidence_error"] = message
                    risk_evidence_errors.append(message)
                elif (
                    not outcome_result.get("accepted")
                    or outcome_result.get("conflict")
                ):
                    message = (
                        "Risk-Outcome abgelehnt: "
                        + str(outcome_result.get("conflict") or "unknown")[:120]
                    )
                    intent["risk_evidence_error"] = message
                    risk_evidence_errors.append(message)
                else:
                    intent.pop("risk_evidence_error", None)
            except Exception as exc:
                # A failed evidence write must not be converted into a local
                # success claim; the next broker reconciliation retries it.
                intent["risk_evidence_error"] = str(exc)[:200]
                risk_evidence_errors.append(
                    f"Risk-Outcome fehlgeschlagen: {str(exc)[:160]}"
                )
        intent["reconciled_at"] = state["last_reconcile"]

    trades_today = len(
        {
            str(intent.get("setup_id"))
            for intent in intents
            if str(intent.get("filled_at") or "")[:10] == today and intent.get("setup_id")
        }
    )
    execution_write_recovery: Optional[Dict[str, Any]] = None
    if require_fresh and expected_recovery_generation is not None:
        recovery_evidence_reliable = (
            not risk_evidence_errors
            and positions_snapshot_complete
            and orders_snapshot_complete
            and fills_snapshot_complete
            and account_snapshot_complete
            and pnl_snapshot_complete
            and order_observation_reliable
            and ib_is_connected()
            and currency_evidence.get("base_currency_evidence") == "VERIFIED"
            and str(currency_evidence.get("base_currency") or "").upper() == "USD"
            and currency_evidence.get("risk_value_currency_evidence") == "VERIFIED"
            and currency_evidence.get("daily_pnl_currency_evidence") == "VERIFIED"
        )
        if risk_store is None:
            execution_write_recovery = {
                "accepted": False,
                "resolved_count": 0,
                "generation": expected_recovery_generation,
                "reason": "execution_recovery_store_unavailable",
            }
        else:
            try:
                execution_write_recovery = (
                    risk_store.reconcile_orphaned_execution_writes(
                        expected_recovery_generation,
                        reconciliation_started_at=snapshot_started_at,
                        observed_at=snapshot_observed_at,
                        orders_snapshot_complete=orders_snapshot_complete
                        and order_observation_reliable,
                        positions_snapshot_complete=positions_snapshot_complete,
                        fills_snapshot_complete=fills_snapshot_complete,
                        risk_evidence_reliable=recovery_evidence_reliable,
                        reconciled_accounts=[account],
                    )
                )
            except Exception as exc:
                execution_write_recovery = {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": expected_recovery_generation,
                    "reason": str(exc)[:160],
                }
        if execution_write_recovery.get("accepted") is not True:
            risk_evidence_errors.append(
                "Execution-Write-Recovery abgelehnt: "
                + str(execution_write_recovery.get("reason") or "unknown")[:120]
            )
    broker_connected_now = ib_is_connected()
    state.update({
        "broker_connected": broker_connected_now,
        "broker_error": orders_snapshot_error
        or (fresh_snapshot_error if require_fresh and fresh_snapshot is None else None),
        "orders_snapshot_complete": orders_snapshot_complete,
        "positions_snapshot_complete": positions_snapshot_complete,
        "fills_snapshot_complete": fills_snapshot_complete,
        "account_snapshot_complete": account_snapshot_complete,
        "pnl_snapshot_complete": pnl_snapshot_complete,
        "risk_snapshot_fresh_required": bool(require_fresh),
        "risk_evidence_unreliable": bool(risk_evidence_errors),
        "risk_evidence_errors": list(dict.fromkeys(risk_evidence_errors)),
        "positions": positions,
        "open_orders": orders,
        "fills": fills[-300:],
        "intents": intents[-500:],
        "cooldown_tickers": cooldown_tickers,
        "account": {
            "managed_accounts": accounts,
            "selected": account,
            "paper": bool(account and account.startswith("DU")),
            **currency_evidence,
            "base_currency_observed_at": risk_values_observed_at,
            "risk_values_observed_at": risk_values_observed_at,
            "net_liquidation": net_liquidation,
            "available_funds": available_funds,
            "buying_power": buying_power,
            "gross_position_value": abs(gross_position_value) if gross_position_value is not None else None,
        },
        "daily_pnl": daily_pnl,
        "daily_pnl_observed_at": daily_pnl_observed_at,
        "daily_pnl_pct": daily_pnl_pct,
        "trades_today": trades_today,
        "execution_write_recovery": execution_write_recovery,
    })
    return state_write(state)


def account_gate(state: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = state or state_read()
    config = config or config_load()
    reasons: List[str] = []
    account = state.get("account") or {}
    if not state.get("broker_connected"):
        reasons.append("IBKR nicht verbunden")
    if state.get("broker_error"):
        reasons.append(str(state["broker_error"]))
    if state.get("risk_evidence_unreliable"):
        reasons.append("Risk-Evidence unzuverlaessig")
    if state.get("risk_snapshot_fresh_required") is True:
        if state.get("account_snapshot_complete") is not True:
            reasons.append("Frischer Konto-Snapshot unvollstaendig")
        if state.get("pnl_snapshot_complete") is not True:
            reasons.append("Frischer DailyPnL-Snapshot unvollstaendig")
        if not account.get("risk_values_observed_at"):
            reasons.append("Frischer Konto-Zeitstempel fehlt")
        if not state.get("daily_pnl_observed_at"):
            reasons.append("Frischer DailyPnL-Zeitstempel fehlt")
    if state.get("positions_snapshot_complete") is not True:
        reasons.append("Positions-Snapshot unvollstaendig")
    if state.get("orders_snapshot_complete") is not True:
        reasons.append("Order-Snapshot unvollstaendig")
    if state.get("fills_snapshot_complete") is not True:
        reasons.append("Fill-Snapshot unvollstaendig")
    if not account.get("paper") or not str(account.get("selected") or "").startswith("DU"):
        reasons.append("Kein eindeutig ausgewaehltes Paperkonto")
    if account.get("base_currency_evidence") != "VERIFIED":
        reasons.append("Kontobasiswaehrung nicht eindeutig verifiziert")
    elif str(account.get("base_currency") or "").strip().upper() != "USD":
        reasons.append("Kontobasiswaehrung ist nicht USD")
    if account.get("risk_value_currency_evidence") != "VERIFIED":
        reasons.append("Kontowerte nicht eindeutig in USD verifiziert")
    if (
        state.get("risk_snapshot_fresh_required") is True
        and account.get("daily_pnl_currency_evidence") != "VERIFIED"
    ):
        reasons.append("DailyPnL nicht eindeutig an USD-Konto gebunden")
    net_liquidation = _safe_float(account.get("net_liquidation"))
    available_funds = _safe_float(account.get("available_funds"))
    gross_position_value = _safe_float(account.get("gross_position_value"))
    if net_liquidation is None or net_liquidation <= 0:
        reasons.append("NetLiquidation fehlt")
    if available_funds is None:
        reasons.append("AvailableFunds fehlt")
    elif available_funds < config["min_available_funds"]:
        reasons.append("Verfuegbare Mittel unter Mindestreserve")
    if gross_position_value is None or gross_position_value < 0:
        reasons.append("GrossPositionValue fehlt")
    daily_pnl = _safe_float(state.get("daily_pnl"))
    if config.get("require_daily_pnl") and daily_pnl is None:
        reasons.append("DailyPnL fehlt")
    if daily_pnl is not None and net_liquidation:
        loss_limit = net_liquidation * config["max_daily_loss_pct"] / 100.0
        if daily_pnl <= -loss_limit:
            reasons.append("Tagesverlustlimit erreicht")
    if config.get("kill_switch"):
        reasons.append("Kill-Switch aktiv")
    if not config.get("execution_enabled"):
        reasons.append("Order-Ausfuehrung nicht aktiviert")
    if config.get("mode") != "paper_auto":
        reasons.append("Nur Review-Modus aktiv")
    return {"allowed": not reasons, "reasons": list(dict.fromkeys(reasons))}


def round_to_tick(price: float, tick: float, direction: str = "nearest") -> float:
    value = Decimal(str(price))
    increment = Decimal(str(tick if tick and tick > 0 else 0.01))
    units = value / increment
    rounding = {
        "up": ROUND_CEILING,
        "down": ROUND_FLOOR,
        "nearest": ROUND_HALF_UP,
    }.get(direction, ROUND_HALF_UP)
    return float(units.to_integral_value(rounding=rounding) * increment)


def contract_min_tick(ib: Any, contract: Any, reference_price: float) -> float:
    try:
        details = list(ib.reqContractDetails(contract) or [])
        ticks = [_safe_float(getattr(detail, "minTick", None)) for detail in details]
        valid = [tick for tick in ticks if tick is not None and tick > 0]
        if valid:
            return min(valid)
    except Exception:
        pass
    # Conservative US-stock fallback. Sub-dollar securities commonly trade in
    # finer increments, while ordinary stocks use cents.
    return 0.0001 if reference_price < 1 else 0.01


def _risk_intent_payload(
    *,
    setup_id: str,
    order_ref: str,
    account: str,
    con_id: int,
    direction: str,
    quantity: int,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    stop_limit: float,
    allocations: List[int],
    signal: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "setup_id": setup_id,
        "order_ref": order_ref,
        "account": account,
        "con_id": con_id,
        "direction": direction,
        "quantity": quantity,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "stop_limit": stop_limit,
        "allocations": list(allocations),
        "ticker": str(signal.get("ticker") or "").strip().upper(),
        "group_key": str(signal.get("group_key") or "").strip().upper(),
        "group_verified": signal.get("group_verified") is True,
    }


def _risk_reservation_payload(intent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "reservation_id": f"reservation-{intent['setup_id']}",
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


def _order_mapping_payload(
    intent: Dict[str, Any],
    trade: Any,
    *,
    role: str,
    branch: int,
    parent_order_id: int = 0,
    authorized_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    order = getattr(trade, "order", None)
    payload = {
        "account": intent["account"],
        "con_id": intent["con_id"],
        "order_id": int(getattr(order, "orderId", 0) or 0),
        "perm_id": int(getattr(order, "permId", 0) or 0),
        "client_id": int(getattr(order, "clientId", 0) or 0),
        "order_ref": str(getattr(order, "orderRef", "") or ""),
        "role": role,
        "branch": branch,
        "parent_order_id": parent_order_id,
    }
    # Broker/session identity is read from the acknowledged order object.  A
    # restart plan may override only the immutable strategy geometry; it must
    # not accidentally drop clientId/outsideRth from the durable mapping.
    payload.update(_authorized_order_fields(order))
    if authorized_fields is not None:
        payload.update(
            {
                key: value
                for key, value in authorized_fields.items()
                if key != "client_id"
            }
        )
    return payload


def _normalized_order_price(value: Any) -> Optional[float]:
    price = _safe_float(value)
    if price is None or price <= 0 or price > 1e100:
        return None
    return price


def _authorized_order_fields(order: Any) -> Dict[str, Any]:
    """Normalize the broker fields that make an order the authorized order."""
    return {
        "action": str(getattr(order, "action", "") or "").strip().upper(),
        "order_type": str(getattr(order, "orderType", "") or "").strip().upper(),
        "quantity": _safe_float(getattr(order, "totalQuantity", None)),
        "aux_price": _normalized_order_price(getattr(order, "auxPrice", None)),
        "limit_price": _normalized_order_price(getattr(order, "lmtPrice", None)),
        "oca_group": str(getattr(order, "ocaGroup", "") or ""),
        "oca_type": int(_safe_float(getattr(order, "ocaType", 0)) or 0),
        "tif": str(getattr(order, "tif", "") or "").strip().upper(),
        "transmit": getattr(order, "transmit", False) is True,
        "outside_rth": getattr(order, "outsideRth", False) is True,
        "client_id": int(_safe_float(getattr(order, "clientId", 0)) or 0),
    }


def _authorized_field_matches(field: str, expected: Any, actual: Any) -> bool:
    if field in {"quantity", "aux_price", "limit_price"}:
        if expected is None or actual is None:
            return expected is actual
        return math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=1e-9)
    return expected == actual


def _restart_order_plan(intent: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Rebuild the immutable bracket plan needed after a process crash."""
    direction = str(intent.get("direction") or "").upper()
    order_ref = str(intent.get("order_ref") or "")
    entry = _safe_float(intent.get("entry"))
    stop = _safe_float(intent.get("stop"))
    tp1 = _safe_float(intent.get("tp1"))
    tp2 = _safe_float(intent.get("tp2"))
    stop_limit = _safe_float(intent.get("stop_limit"))
    quantity = int(_safe_float(intent.get("quantity")) or 0)
    raw_allocations = intent.get("allocations")
    if (
        direction not in {"LONG", "SHORT"}
        or not order_ref.startswith("AS2-")
        or None in {entry, stop, tp1, tp2, stop_limit}
        or quantity <= 0
        or not isinstance(raw_allocations, list)
    ):
        return None
    allocations: List[int] = []
    for value in raw_allocations:
        numeric = _safe_float(value)
        if numeric is None or numeric <= 0 or int(numeric) != numeric:
            return None
        allocations.append(int(numeric))
    if not allocations or len(allocations) > 2 or sum(allocations) != quantity:
        return None
    targets = [tp1] if len(allocations) == 1 else [tp1, tp2]
    main_action, exit_action = (
        ("BUY", "SELL") if direction == "LONG" else ("SELL", "BUY")
    )
    plan: List[Dict[str, Any]] = []
    for branch, (branch_quantity, target) in enumerate(
        zip(allocations, targets), start=1
    ):
        oca_group = f"{order_ref}-O{branch}"
        plan.extend(
            [
                {
                    "role": "PARENT",
                    "branch": branch,
                    "order_ref": f"{order_ref}-P{branch}",
                    "parent_ref": "",
                    "fields": {
                        "action": main_action,
                        "order_type": "STP LMT",
                        "quantity": float(branch_quantity),
                        "aux_price": entry,
                        "limit_price": stop_limit,
                        "oca_group": "",
                        "oca_type": 0,
                        "tif": "DAY",
                        "transmit": False,
                        "outside_rth": False,
                    },
                },
                {
                    "role": "STOP",
                    "branch": branch,
                    "order_ref": f"{order_ref}-S{branch}",
                    "parent_ref": f"{order_ref}-P{branch}",
                    "fields": {
                        "action": exit_action,
                        "order_type": "STP",
                        "quantity": float(branch_quantity),
                        "aux_price": stop,
                        "limit_price": None,
                        "oca_group": oca_group,
                        "oca_type": 1,
                        "tif": "GTC",
                        "transmit": False,
                        "outside_rth": False,
                    },
                },
                {
                    "role": "TARGET",
                    "branch": branch,
                    "order_ref": f"{order_ref}-T{branch}",
                    "parent_ref": f"{order_ref}-P{branch}",
                    "fields": {
                        "action": exit_action,
                        "order_type": "LMT",
                        "quantity": float(branch_quantity),
                        "aux_price": None,
                        "limit_price": target,
                        "oca_group": oca_group,
                        "oca_type": 1,
                        "tif": "GTC",
                        "transmit": True,
                        "outside_rth": False,
                    },
                },
            ]
        )
    return plan


def _restart_live_mappings(
    intent: Dict[str, Any], trades: List[Any]
) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    plan = _restart_order_plan(intent)
    if plan is None:
        return [], False, "restart_plan_invalid"
    base_ref = str(intent["order_ref"])
    expected_by_ref = {item["order_ref"]: item for item in plan}
    scoped = [
        trade
        for trade in trades
        if str(getattr(getattr(trade, "order", None), "orderRef", "") or "").startswith(
            f"{base_ref}-"
        )
    ]
    refs = [
        str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
        for trade in scoped
    ]
    order_ids = [
        int(getattr(getattr(trade, "order", None), "orderId", 0) or 0)
        for trade in scoped
    ]
    if (
        len(refs) != len(set(refs))
        or len(order_ids) != len(set(order_ids))
        or any(order_id <= 0 for order_id in order_ids)
        or any(order_ref not in expected_by_ref for order_ref in refs)
    ):
        return [], False, "restart_order_snapshot_ambiguous"
    trade_by_ref = {order_ref: trade for order_ref, trade in zip(refs, scoped)}
    mappings: List[Dict[str, Any]] = []
    for expected in plan:
        trade = trade_by_ref.get(expected["order_ref"])
        if trade is None:
            continue
        order = getattr(trade, "order", None)
        contract = getattr(trade, "contract", None)
        if (
            str(getattr(order, "account", "") or "").upper()
            != str(intent.get("account") or "").upper()
            or int(getattr(contract, "conId", 0) or 0)
            != int(_safe_float(intent.get("con_id")) or 0)
        ):
            return [], False, "restart_order_identity_mismatch"
        actual_fields = _authorized_order_fields(order)
        if any(
            not _authorized_field_matches(
                field, value, actual_fields.get(field)
            )
            for field, value in expected["fields"].items()
        ):
            return [], False, "restart_order_contract_mismatch"
        parent_order_id = 0
        if expected["parent_ref"]:
            parent_trade = trade_by_ref.get(expected["parent_ref"])
            if parent_trade is None:
                return [], False, "restart_parent_visibility_incomplete"
            parent_order_id = int(
                getattr(getattr(parent_trade, "order", None), "orderId", 0) or 0
            )
        if int(getattr(order, "parentId", 0) or 0) != parent_order_id:
            return [], False, "restart_parent_identity_mismatch"
        mappings.append(
            _order_mapping_payload(
                intent,
                trade,
                role=expected["role"],
                branch=expected["branch"],
                parent_order_id=parent_order_id,
                authorized_fields=expected["fields"],
            )
        )
    complete = len(mappings) == len(plan)
    return mappings, complete, None


def _broker_order_evidence_exactly_visible(
    ib: Any,
    contract: Any,
    mappings: List[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Require the live broker snapshot to confirm every submitted mapping."""
    try:
        trades = list(ib.openTrades() or [])
    except Exception:
        return None
    expected_con_id = int(getattr(contract, "conId", 0) or 0)
    if expected_con_id <= 0 or not mappings:
        return None
    base_refs = {
        re.sub(r"-(?:P|S|T)\d+$", "", str(mapping.get("order_ref") or ""))
        for mapping in mappings
    }
    if len(base_refs) != 1 or not next(iter(base_refs), ""):
        return None
    base_ref = next(iter(base_refs))
    exact_ref = re.compile(rf"^{re.escape(base_ref)}-(?:P|S|T)[1-9][0-9]*$")
    namespace_trades = [
        trade
        for trade in trades
        if str(
            getattr(getattr(trade, "order", None), "orderRef", "") or ""
        ).startswith(f"{base_ref}-")
    ]
    if any(
        not exact_ref.fullmatch(
            str(getattr(getattr(trade, "order", None), "orderRef", "") or "")
        )
        for trade in namespace_trades
    ):
        return None
    scoped_trades = namespace_trades
    if len(scoped_trades) != len(mappings):
        return None
    matched_order_ids: set[int] = set()
    matched_order_refs: set[str] = set()
    matched_perm_ids: set[int] = set()
    matched_identities: set[tuple[str, int, int, int]] = set()
    evidence: List[Dict[str, Any]] = []
    required_fields = {
        "action",
        "order_type",
        "quantity",
        "aux_price",
        "limit_price",
        "oca_group",
        "oca_type",
        "tif",
        "transmit",
        "outside_rth",
        "client_id",
    }
    for mapping in mappings:
        matches = []
        for trade in scoped_trades:
            order = getattr(trade, "order", None)
            trade_contract = getattr(trade, "contract", None)
            if (
                int(getattr(order, "orderId", 0) or 0) != mapping["order_id"]
                or str(getattr(order, "orderRef", "") or "") != mapping["order_ref"]
                or str(getattr(order, "account", "") or "").upper() != mapping["account"]
                or int(getattr(trade_contract, "conId", 0) or 0) != expected_con_id
                or int(getattr(order, "parentId", 0) or 0) != mapping["parent_order_id"]
            ):
                continue
            actual_fields = _authorized_order_fields(order)
            if any(
                field not in mapping
                or not _authorized_field_matches(
                    field, mapping.get(field), actual_fields.get(field)
                )
                for field in required_fields
            ):
                continue
            status = str(
                getattr(getattr(trade, "orderStatus", None), "status", "") or ""
            ).strip().upper()
            perm_id = int(_safe_float(getattr(order, "permId", 0)) or 0)
            mapped_perm_id = int(_safe_float(mapping.get("perm_id")) or 0)
            if (
                status not in {"PRESUBMITTED", "SUBMITTED"}
                or perm_id <= 0
                or (mapped_perm_id > 0 and mapped_perm_id != perm_id)
            ):
                continue
            matches.append(trade)
        if len(matches) != 1:
            return None
        matched = matches[0]
        order = matched.order
        order_id = int(getattr(order, "orderId", 0) or 0)
        order_ref = str(getattr(order, "orderRef", "") or "")
        perm_id = int(_safe_float(getattr(order, "permId", 0)) or 0)
        client_id = int(_safe_float(getattr(order, "clientId", 0)) or 0)
        identity = (
            str(getattr(order, "account", "") or "").upper(),
            client_id,
            expected_con_id,
            order_id,
        )
        if (
            order_id in matched_order_ids
            or order_ref in matched_order_refs
            or perm_id in matched_perm_ids
            or identity in matched_identities
        ):
            return None
        matched_order_ids.add(order_id)
        matched_order_refs.add(order_ref)
        matched_perm_ids.add(perm_id)
        matched_identities.add(identity)
        mapping["perm_id"] = perm_id
        evidence.append(_serialize_trade(matched))
    return evidence


def _broker_orders_exactly_visible(
    ib: Any,
    contract: Any,
    mappings: List[Dict[str, Any]],
) -> bool:
    return _broker_order_evidence_exactly_visible(ib, contract, mappings) is not None


_SUBMIT_LEASE_TTL_SECONDS = 60
_EXECUTION_DRAIN_TIMEOUT_SECONDS = 2.0
_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS = 2.0


class _LeaseFenceLost(RuntimeError):
    pass


class _ExecutionGenerationLost(RuntimeError):
    pass


def _renew_submission_lease(
    risk_store: TradingRiskStore,
    lease_key: str,
    owner_token: str,
    fence_token: int,
    execution_generation: Optional[int] = None,
) -> None:
    try:
        renewed = risk_store.renew_lease(
            lease_key,
            owner_token,
            fence_token,
            now=datetime.now(timezone.utc),
            ttl_seconds=_SUBMIT_LEASE_TTL_SECONDS,
        )
    except Exception as exc:
        raise _LeaseFenceLost(f"Risk-Submit-Lease konnte nicht erneuert werden: {exc}") from exc
    if not renewed.get("renewed"):
        raise _LeaseFenceLost(
            "Risk-Submit-Lease verloren: "
            + str(renewed.get("reason") or "lease_fenced")[:120]
        )
    if execution_generation is not None:
        try:
            execution_state = risk_store.execution_state()
        except Exception as exc:
            raise _ExecutionGenerationLost(
                f"Execution-Generation nicht pruefbar: {exc}"
            ) from exc
        if (
            execution_state.get("armed") is not True
            or execution_state.get("generation") != execution_generation
        ):
            raise _ExecutionGenerationLost("Execution-Generation wurde gefenced")


def _place_order_with_execution_guard(
    risk_store: TradingRiskStore,
    execution_generation: int,
    ib: Any,
    contract: Any,
    order: Any,
    placed: List[Any],
    pending_execution_write_ids: List[str],
    *,
    setup_id: str,
) -> Any:
    if int(getattr(order, "orderId", 0) or 0) <= 0:
        client = getattr(ib, "client", None)
        get_request_id = getattr(client, "getReqId", None)
        if callable(get_request_id):
            order.orderId = int(get_request_id())
    if int(getattr(order, "orderId", 0) or 0) <= 0:
        raise RuntimeError("Broker-Order-ID konnte vor Write nicht reserviert werden")

    def place_and_retain() -> Any:
        try:
            trade = ib.placeOrder(contract, order)
        except Exception:
            # A socket/API exception does not prove that TWS rejected the
            # write.  Retain the preallocated immutable broker identity so
            # recovery can cancel/quarantine the possibly accepted order.
            if int(getattr(order, "orderId", 0) or 0) > 0:
                placed.append(
                    SimpleNamespace(
                        contract=contract,
                        order=order,
                        orderStatus=SimpleNamespace(
                            status="UNKNOWN",
                            filled=None,
                            remaining=None,
                        ),
                    )
                )
            raise
        # Retain the broker result before the post-I/O generation check.  If a
        # concurrent kill fenced this write, recovery must still know the
        # exact order that may now exist at the broker.
        placed.append(trade)
        return trade

    guarded = risk_store.run_if_execution_generation(
        execution_generation,
        place_and_retain,
        claim_context={
            "operation_kind": "PLACE_ORDER",
            "account": str(getattr(order, "account", "") or "").upper(),
            "setup_id": setup_id,
            "order_id": int(getattr(order, "orderId", 0) or 0),
            "order_ref": _order_ref(order),
        },
        retain_until_ack=True,
        on_claim_registered=pending_execution_write_ids.append,
    )
    if (
        not guarded.get("executed")
        or guarded.get("reason")
        or guarded.get("armed") is not True
        or guarded.get("generation") != execution_generation
    ):
        raise _ExecutionGenerationLost(
            str(guarded.get("reason") or "execution_generation_fenced")[:120]
        )
    if guarded.get("write_id") not in pending_execution_write_ids:
        raise _ExecutionGenerationLost("execution_claim_registration_missing")
    return guarded.get("result")


def _cancel_placed_orders(
    ib: Any,
    placed: List[Any],
    *,
    before_cancel: Optional[Any] = None,
) -> Dict[str, List[int]]:
    requested_order_ids: List[int] = []
    failed_order_ids: List[int] = []
    for trade in reversed(placed):
        order = getattr(trade, "order", None)
        order_id = int(getattr(order, "orderId", 0) or 0)
        if before_cancel is not None:
            try:
                before_cancel()
            except Exception:
                # Cancelling exposure is the one broker-side effect that must
                # still be attempted after a fence has disappeared.
                pass
        try:
            ib.cancelOrder(order)
        except Exception:
            if order_id > 0:
                failed_order_ids.append(order_id)
        else:
            if order_id > 0:
                requested_order_ids.append(order_id)
    return {
        "requested_order_ids": requested_order_ids,
        "failed_order_ids": failed_order_ids,
    }


def _cancel_acknowledgements(
    trades: List[Any],
) -> Tuple[List[int], List[int]]:
    acknowledged: List[int] = []
    pending: List[int] = []
    for trade in trades:
        order = getattr(trade, "order", None)
        order_id = int(getattr(order, "orderId", 0) or 0)
        status = getattr(trade, "orderStatus", None)
        token = str(getattr(status, "status", "") or "").strip().upper()
        filled = _safe_float(getattr(status, "filled", None))
        if token in {"CANCELLED", "APICANCELLED"} and filled == 0:
            if order_id > 0:
                acknowledged.append(order_id)
        elif order_id > 0:
            pending.append(order_id)
    return acknowledged, pending


def _submission_recovery_exposure(
    ib: Any, placed: List[Any]
) -> Tuple[bool, bool]:
    """Return (exposure_possible, broker_snapshots_complete)."""
    parents = [
        trade for trade in placed
        if _is_parent_order_ref(_order_ref(getattr(trade, "order", None)))
    ]
    if not parents:
        return False, True
    for trade in parents:
        status = getattr(trade, "orderStatus", None)
        total = _safe_float(getattr(trade.order, "totalQuantity", None))
        filled = _safe_float(getattr(status, "filled", None))
        remaining = _safe_float(getattr(status, "remaining", None))
        token = str(getattr(status, "status", "") or "").strip().upper()
        if (
            token in {"FILLED", "PARTIALLYFILLED", "PARTIALLY FILLED"}
            or (filled is not None and filled > 0)
            or (
                total is not None
                and remaining is not None
                and 0 <= remaining < total
            )
        ):
            return True, True

    accounts = {
        str(getattr(trade.order, "account", "") or "").strip().upper()
        for trade in parents
    }
    con_ids = {
        int(_safe_float(getattr(trade.contract, "conId", 0)) or 0)
        for trade in parents
    }
    if len(accounts) != 1 or "" in accounts or len(con_ids) != 1 or 0 in con_ids:
        return False, False
    account = next(iter(accounts))
    con_id = next(iter(con_ids))
    parent_ids = {int(trade.order.orderId) for trade in parents}
    # Only bounded server requests issued after every parent cancel
    # acknowledgement can authorize protection removal. Cached data and
    # unbounded synchronous requests are deliberately ineligible.
    run_bounded = getattr(ib, "run", None)
    error_event = getattr(ib, "errorEvent", None)
    positions_method = getattr(ib, "reqPositionsAsync", None)
    fills_method = getattr(ib, "reqExecutionsAsync", None)
    if (
        not callable(run_bounded)
        or error_event is None
        or not callable(positions_method)
        or not callable(fills_method)
    ):
        return False, False
    request_errors: List[str] = []

    def capture_error(
        _request_id: Any,
        error_code: Any,
        error_string: Any,
        *_args: Any,
    ) -> None:
        if _broker_error_is_request_failure(error_code):
            request_errors.append(
                f"{str(error_code)[:20]}:{str(error_string)[:120]}"
            )

    subscribed = False
    try:
        error_event += capture_error
        subscribed = True
        raw_positions = run_bounded(
            positions_method(), timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS
        )
        if request_errors or raw_positions is None:
            raise RuntimeError("fresh positions snapshot failed")
        raw_fills = run_bounded(
            fills_method(), timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS
        )
        if request_errors or raw_fills is None:
            raise RuntimeError("fresh fills snapshot failed")
        positions = list(raw_positions)
        fills = list(raw_fills)
    except Exception:
        return False, False
    finally:
        if subscribed:
            error_event -= capture_error
    if not ib_is_connected():
        return False, False
    for position in positions:
        if (
            str(getattr(position, "account", "") or "").strip().upper()
            == account
            and int(
                _safe_float(
                    getattr(getattr(position, "contract", None), "conId", 0)
                )
                or 0
            )
            == con_id
            and (_safe_float(getattr(position, "position", None)) or 0) != 0
        ):
            return True, True
    for fill in fills:
        execution = getattr(fill, "execution", None)
        if (
            int(_safe_float(getattr(execution, "orderId", 0)) or 0)
            in parent_ids
            and str(getattr(execution, "acctNumber", "") or "")
            .strip()
            .upper()
            == account
            and int(
                _safe_float(
                    getattr(getattr(fill, "contract", None), "conId", 0)
                )
                or 0
            )
            == con_id
            and (_safe_float(getattr(execution, "shares", None)) or 0) > 0
        ):
            return True, True
    return False, True


def _parents_cancelled_without_fill(parents: List[Any]) -> bool:
    if not parents:
        return False
    for trade in parents:
        status = getattr(trade, "orderStatus", None)
        token = str(getattr(status, "status", "") or "").strip().upper()
        filled = _safe_float(getattr(status, "filled", None))
        if token not in {"CANCELLED", "APICANCELLED"} or filled != 0:
            return False
    return True


def _recover_failed_submission(
    risk_store: TradingRiskStore,
    ib: Any,
    placed: List[Any],
    reservation_id: str,
    *,
    lease_key: str,
    owner_token: str,
    fence_token: int,
    reason: str,
    force_new_lease: bool,
) -> Dict[str, Any]:
    credentials: Optional[Tuple[str, int]] = None
    if not force_new_lease:
        try:
            _renew_submission_lease(
                risk_store, lease_key, owner_token, fence_token
            )
            credentials = (owner_token, fence_token)
        except _LeaseFenceLost:
            force_new_lease = True
    if force_new_lease:
        recovery_owner = f"paper-autotrader-recovery:{uuid.uuid4().hex}"
        try:
            acquired = risk_store.acquire_lease(
                lease_key,
                recovery_owner,
                now=datetime.now(timezone.utc),
                ttl_seconds=_SUBMIT_LEASE_TTL_SECONDS,
            )
        except Exception as exc:
            acquired = {"acquired": False, "reason": str(exc)[:120]}
        if acquired.get("acquired"):
            credentials = (recovery_owner, int(acquired["fence_token"]))

    def renew_before_cancel() -> None:
        if credentials is None:
            return
        _renew_submission_lease(
            risk_store, lease_key, credentials[0], credentials[1]
        )

    parents = [
        trade for trade in placed
        if _is_parent_order_ref(_order_ref(getattr(trade, "order", None)))
    ]
    protective_orders = [trade for trade in placed if trade not in parents]
    # Entry orders are always cancelled first.  Child protection may only be
    # removed after the broker has causally acknowledged every parent cancel
    # and a fresh post-cancel exposure snapshot proves no fill/position.
    parent_cancel = _cancel_placed_orders(
        ib, parents, before_cancel=renew_before_cancel
    )
    sleep_method = getattr(ib, "sleep", None)
    if callable(sleep_method):
        try:
            sleep_method(0.25)
        except Exception:
            pass
    parent_cancel_acknowledged = _parents_cancelled_without_fill(parents)
    exposure_possible, snapshots_complete = _submission_recovery_exposure(ib, placed)
    protection_removal_authorized = (
        parent_cancel_acknowledged
        and snapshots_complete
        and not exposure_possible
    )
    protection_cancel = {
        "requested_order_ids": [],
        "failed_order_ids": [],
    }
    protection_cancel_acknowledged_order_ids: List[int] = []
    protection_cancel_pending_order_ids = [
        int(getattr(getattr(trade, "order", None), "orderId", 0) or 0)
        for trade in protective_orders
        if int(getattr(getattr(trade, "order", None), "orderId", 0) or 0) > 0
    ]
    if protection_removal_authorized:
        protection_cancel = _cancel_placed_orders(
            ib, protective_orders, before_cancel=renew_before_cancel
        )
        for _ in range(20):
            (
                protection_cancel_acknowledged_order_ids,
                protection_cancel_pending_order_ids,
            ) = _cancel_acknowledgements(protective_orders)
            if not protection_cancel_pending_order_ids:
                break
            if not callable(sleep_method):
                break
            try:
                sleep_method(0.1)
            except Exception:
                break
    protection_removal_safe = (
        protection_removal_authorized
        and not protection_cancel_pending_order_ids
        and not protection_cancel["failed_order_ids"]
        and len(protection_cancel_acknowledged_order_ids)
        == len(protective_orders)
    )
    recovery_metadata = {
        "parent_cancel_acknowledged": parent_cancel_acknowledged,
        "parent_cancel_requested_order_ids": parent_cancel["requested_order_ids"],
        "parent_cancel_failed_order_ids": parent_cancel["failed_order_ids"],
        "broker_snapshots_complete": snapshots_complete,
        "exposure_possible": exposure_possible,
        "protection_removal_authorized": protection_removal_authorized,
        "protection_removal_safe": protection_removal_safe,
        "protection_cancel_requested_order_ids": protection_cancel[
            "requested_order_ids"
        ],
        "protection_cancel_pending_order_ids": (
            protection_cancel_pending_order_ids
        ),
        "protection_cancel_acknowledged_order_ids": (
            protection_cancel_acknowledged_order_ids
        ),
        "protection_cancel_failed_order_ids": protection_cancel[
            "failed_order_ids"
        ],
        "protective_orders_retained": bool(
            protective_orders and not protection_removal_safe
        ),
    }
    if credentials is None:
        return {
            "updated": False,
            "reason": "recovery_lease_unavailable",
            **recovery_metadata,
        }
    try:
        _renew_submission_lease(
            risk_store, lease_key, credentials[0], credentials[1]
        )
        result = risk_store.mark_reservation_reconcile_required(
            reservation_id,
            lease_key=lease_key,
            owner_token=credentials[0],
            fence_token=credentials[1],
            now=datetime.now(timezone.utc),
            reason=reason[:200],
        )
    except Exception as exc:
        return {"updated": False, "reason": str(exc)[:120], **recovery_metadata}
    return {**result, **recovery_metadata}


def _recover_restart_mappings(
    risk_store: TradingRiskStore,
    trades: List[Any],
    account: str,
    local_intents: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Recover crash-window mappings while retaining a fenced reservation."""
    errors: List[str] = []
    try:
        reservations = list(risk_store.active_reservations() or [])
    except Exception as exc:
        return [f"Restart-Recovery Reservations fehlgeschlagen: {str(exc)[:140]}"]
    for reservation in reservations:
        status = str(reservation.get("status") or "").upper()
        if (
            status not in {"SUBMITTING", "RECONCILE_REQUIRED"}
            or str(reservation.get("account") or "").upper() != account.upper()
        ):
            continue
        setup_id = str(reservation.get("setup_id") or "")
        local_intent = next(
            (
                item
                for item in (local_intents or [])
                if str(item.get("setup_id") or "") == setup_id
            ),
            None,
        )
        lease_key = f"submit:{setup_id}"
        owner_token = f"paper-autotrader-restart:{uuid.uuid4().hex}"
        try:
            lease = risk_store.acquire_lease(
                lease_key,
                owner_token,
                now=datetime.now(timezone.utc),
                ttl_seconds=_SUBMIT_LEASE_TTL_SECONDS,
            )
        except Exception as exc:
            errors.append(
                f"Restart-Recovery Lease fehlgeschlagen ({setup_id}): {str(exc)[:120]}"
            )
            continue
        if not lease.get("acquired"):
            errors.append(
                f"Restart-Recovery Lease belegt ({setup_id}): "
                + str(lease.get("reason") or "lease_held")[:80]
            )
            continue
        fence_token = int(lease["fence_token"])
        try:
            intent = risk_store.load_intent(setup_id)
            if not isinstance(intent, dict):
                raise RuntimeError("restart_intent_missing")
            if local_intent is None and local_intents is not None:
                local_intent = {
                    **intent,
                    "ticker": str(intent.get("ticker") or "").upper(),
                    "status": "RECONCILE_REQUIRED",
                    "risk_reservation_id": reservation.get("reservation_id"),
                    "risk_reservation_status": status,
                    "order_ids": [],
                    "parent_order_ids": [],
                    "recovered_at": _utc_now(),
                }
                local_intents.append(local_intent)
            mappings, complete, mapping_error = _restart_live_mappings(
                intent, trades
            )
            if mapping_error:
                raise RuntimeError(mapping_error)
            for mapping in mappings:
                _renew_submission_lease(
                    risk_store, lease_key, owner_token, fence_token
                )
                registered = risk_store.register_intent_order(setup_id, mapping)
                if (
                    not registered.get("accepted")
                    or registered.get("conflict")
                ):
                    raise RuntimeError(
                        "restart_mapping_rejected:"
                        + str(registered.get("conflict") or "unknown")[:80]
                    )
            _renew_submission_lease(
                risk_store, lease_key, owner_token, fence_token
            )
            broker_order_evidence = (
                _broker_order_evidence_exactly_visible(
                    SimpleNamespace(openTrades=lambda: trades),
                    SimpleNamespace(conId=int(intent["con_id"])),
                    mappings,
                )
                if complete
                else None
            )
            if broker_order_evidence is not None:
                # A mapping persisted before the broker ack may contain
                # permId=0.  Re-register the exact acknowledged geometry so
                # the store performs its atomic set-once enrichment.
                for mapping in mappings:
                    registered = risk_store.register_intent_order(setup_id, mapping)
                    if not registered.get("accepted") or registered.get("conflict"):
                        raise RuntimeError(
                            "restart_ack_mapping_rejected:"
                            + str(registered.get("conflict") or "unknown")[:80]
                        )
                transition = risk_store.mark_reservation_broker_visible(
                    reservation["reservation_id"],
                    [mapping["order_id"] for mapping in mappings],
                    lease_key=lease_key,
                    owner_token=owner_token,
                    fence_token=fence_token,
                    now=datetime.now(timezone.utc),
                    broker_order_evidence=broker_order_evidence,
                )
                if not transition.get("updated"):
                    raise RuntimeError(
                        "restart_broker_visible_rejected:"
                        + str(transition.get("reason") or "unknown")[:80]
                    )
                if local_intent is not None:
                    local_intent["status"] = "WORKING"
                    local_intent["risk_reservation_status"] = "BROKER_VISIBLE"
            else:
                transition = risk_store.mark_reservation_reconcile_required(
                    reservation["reservation_id"],
                    lease_key=lease_key,
                    owner_token=owner_token,
                    fence_token=fence_token,
                    now=datetime.now(timezone.utc),
                    reason="restart_order_snapshot_incomplete",
                )
                if not transition.get("updated"):
                    raise RuntimeError(
                        "restart_reconcile_transition_rejected:"
                        + str(transition.get("reason") or "unknown")[:80]
                    )
                errors.append(
                    f"Restart-Recovery unvollstaendig ({setup_id})"
                )
                if local_intent is not None:
                    local_intent["status"] = "RECONCILE_REQUIRED"
                    local_intent["risk_reservation_status"] = "RECONCILE_REQUIRED"
            if local_intent is not None:
                local_intent["order_ids"] = [
                    mapping["order_id"] for mapping in mappings
                ]
                local_intent["parent_order_ids"] = [
                    mapping["order_id"]
                    for mapping in mappings
                    if mapping["role"] == "PARENT"
                ]
        except Exception as exc:
            reason = str(exc)[:160]
            try:
                _renew_submission_lease(
                    risk_store, lease_key, owner_token, fence_token
                )
                risk_store.mark_reservation_reconcile_required(
                    reservation["reservation_id"],
                    lease_key=lease_key,
                    owner_token=owner_token,
                    fence_token=fence_token,
                    now=datetime.now(timezone.utc),
                    reason=reason,
                )
            except Exception:
                pass
            if local_intent is not None:
                local_intent["status"] = "RECONCILE_REQUIRED"
                local_intent["risk_reservation_status"] = "RECONCILE_REQUIRED"
            errors.append(f"Restart-Recovery fehlgeschlagen ({setup_id}): {reason}")
    return errors


def _setup_id(signal: Dict[str, Any]) -> str:
    material = "|".join(
        str(signal.get(key) or "")
        for key in ("ticker", "direction", "trigger_bar_date", "entry", "stop", "tp1", "tp2")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:18].upper()


def _active_intent(intent: Dict[str, Any]) -> bool:
    return str(intent.get("status") or "").upper() in {
        "SUBMITTING",
        "RECONCILE_REQUIRED",
        "WORKING",
        "ACTIVE",
        "FILLED_NO_POSITION",
    }


def calculate_order_quantity(
    entry: float,
    stop: float,
    state: Dict[str, Any],
    config: Dict[str, Any],
    *,
    max_fill_price: Optional[float] = None,
) -> Dict[str, Any]:
    authorized_fill_price = _safe_float(max_fill_price)
    if authorized_fill_price is None:
        authorized_fill_price = float(entry)
    cash_basis_price = max(float(entry), authorized_fill_price)
    risk_per_share = abs(authorized_fill_price - float(stop))
    account = state.get("account") or {}
    net_liquidation = _safe_float(account.get("net_liquidation"))
    available_funds = _safe_float(account.get("available_funds"))
    if (
        risk_per_share <= 0
        or authorized_fill_price <= 0
        or cash_basis_price <= 0
        or not net_liquidation
        or available_funds is None
    ):
        return {"quantity": 0, "error": "Kontorisiko oder Stop-Distanz ungueltig"}
    risk_budget = net_liquidation * config["risk_per_trade_pct"] / 100.0
    risk_qty = math.floor(risk_budget / risk_per_share)
    notional_qty = math.floor(
        config["max_notional_per_trade"] / cash_basis_price
    )
    cash_qty = math.floor(
        max(0.0, available_funds - config["min_available_funds"])
        / cash_basis_price
    )
    quantity = max(0, min(risk_qty, notional_qty, cash_qty, int(config["max_shares"])))
    return {
        "quantity": quantity,
        "risk_budget": round(risk_budget, 2),
        "risk_per_share": risk_per_share,
        "max_fill_price": authorized_fill_price,
        "cash_basis_price": cash_basis_price,
        "notional": round(quantity * cash_basis_price, 2),
        "error": None if quantity > 0 else "Risikobudget reicht nicht fuer eine Aktie",
    }


def _current_exposure(state: Dict[str, Any]) -> float:
    positions = state.get("positions", [])
    broker_exposure = _safe_float((state.get("account") or {}).get("gross_position_value"))
    if broker_exposure is not None and (broker_exposure > 0 or not positions):
        return abs(broker_exposure)
    exposure = 0.0
    for position in positions:
        quantity = abs(_safe_float(position.get("quantity")) or 0.0)
        price = _safe_float(position.get("market_price")) or _safe_float(position.get("avg_cost")) or 0.0
        exposure += quantity * price
    return exposure


def submit_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and optionally submit one idempotent Paper bracket order."""
    config = config_load()
    state = reconcile_broker()
    direction = str(signal.get("direction") or "LONG").upper()
    ticker = str(signal.get("ticker") or "").upper().strip()
    entry = _safe_float(signal.get("entry"))
    stop = _safe_float(signal.get("stop"))
    tp1 = _safe_float(signal.get("tp1"))
    tp2 = _safe_float(signal.get("tp2"))
    if not ticker or None in {entry, stop, tp1, tp2}:
        return {"success": False, "submitted": False, "error": "Order-Level unvollstaendig"}
    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    if not geometry.get("valid"):
        return {"success": False, "submitted": False, "error": "Order-Geometrie ungueltig"}
    if (geometry.get("rr") or 0) < config["min_rr"]:
        return {"success": False, "submitted": False, "error": "R:R unter Mindestwert"}

    setup_id = _setup_id({**signal, "direction": direction})
    order_ref = f"AS2-{setup_id}"
    if any(str(intent.get("setup_id")) == setup_id and _active_intent(intent) for intent in state["intents"]):
        return {"success": False, "submitted": False, "error": "Setup bereits aktiv", "setup_id": setup_id}
    if any(str(position.get("ticker")) == ticker and position.get("quantity") for position in state["positions"]):
        return {"success": False, "submitted": False, "error": "Brokerposition bereits offen", "setup_id": setup_id}
    if any(str(order.get("ticker")) == ticker for order in state["open_orders"]):
        return {"success": False, "submitted": False, "error": "Brokerorder fuer Ticker bereits offen", "setup_id": setup_id}
    gate = account_gate(state, config)
    sizing = calculate_order_quantity(entry, stop, state, config)
    preview = {
        "success": True,
        "submitted": False,
        "setup_id": setup_id,
        "order_ref": order_ref,
        "ticker": ticker,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "sizing": sizing,
        "gate": gate,
    }
    if not gate["allowed"]:
        if sizing.get("error"):
            preview["preview_warning"] = sizing["error"]
        return preview

    active_tickers = {
        str(position.get("ticker") or "").upper()
        for position in state["positions"]
        if position.get("quantity")
    }
    active_tickers.update(
        str(intent.get("ticker") or "").upper()
        for intent in state["intents"]
        if _active_intent(intent)
    )
    active_tickers.discard("")
    if len(active_tickers) >= config["max_positions"]:
        return {**preview, "success": False, "error": "Maximale Positionen erreicht"}
    if sizing["quantity"] <= 0:
        return {**preview, "success": False, "error": sizing["error"]}
    net_liquidation = _safe_float((state.get("account") or {}).get("net_liquidation")) or 0.0
    projected_exposure = _current_exposure(state) + sizing["notional"]
    if net_liquidation and projected_exposure > net_liquidation * config["max_total_exposure_pct"] / 100.0:
        return {**preview, "success": False, "error": "Gesamtexposure-Limit erreicht"}

    ib = _get_ib_state().get("ib")
    if not ib or Order is None:
        return {**preview, "success": False, "error": "IBKR Order-API nicht verfuegbar"}
    account = str((state.get("account") or {}).get("selected") or "")
    contract = ib_get_contract(ticker, "Aktien", "US")
    if not contract:
        return {**preview, "success": False, "error": "IBKR-Kontrakt nicht gefunden"}
    try:
        qualified = list(ib.qualifyContracts(contract) or [])
        if not qualified:
            return {**preview, "success": False, "error": "IBKR-Kontrakt nicht qualifiziert"}
        contract = qualified[0]
    except Exception as exc:
        return {**preview, "success": False, "error": f"Kontraktfehler: {str(exc)[:120]}"}

    con_id = int(getattr(contract, "conId", 0) or 0)
    if con_id <= 0:
        return {**preview, "success": False, "error": "IBKR-Kontrakt-ID ungueltig"}

    tick = contract_min_tick(ib, contract, entry)
    if direction == "LONG":
        main_action, exit_action = "BUY", "SELL"
        entry_tick = round_to_tick(entry, tick, "up")
        stop_tick = round_to_tick(stop, tick, "down")
        tp1_tick = round_to_tick(tp1, tick, "down")
        tp2_tick = round_to_tick(tp2, tick, "down")
        stop_limit = round_to_tick(
            _safe_float(signal.get("stop_limit")) or entry_tick * 1.003,
            tick,
            "up",
        )
    else:
        main_action, exit_action = "SELL", "BUY"
        entry_tick = round_to_tick(entry, tick, "down")
        stop_tick = round_to_tick(stop, tick, "up")
        tp1_tick = round_to_tick(tp1, tick, "up")
        tp2_tick = round_to_tick(tp2, tick, "up")
        stop_limit = round_to_tick(
            _safe_float(signal.get("stop_limit")) or entry_tick * 0.997,
            tick,
            "down",
        )
    rounded_geometry = trade_geometry(entry_tick, stop_tick, tp1_tick, tp2_tick, direction)
    if not rounded_geometry.get("valid"):
        return {**preview, "success": False, "error": "Tick-Rundung zerstoert Order-Geometrie"}
    if (rounded_geometry.get("rr") or 0) < config["min_rr"]:
        return {**preview, "success": False, "error": "R:R nach Tick-Rundung unter Mindestwert"}

    # Refresh the broker evidence once more immediately before the durable
    # lease/reservation boundary.  A cached local snapshot is never enough to
    # authorise a new order.
    state = reconcile_broker(require_fresh=True)
    # Recalculate from both the executable tick-rounded geometry and the
    # freshly observed account values used for atomic risk admission.
    sizing = calculate_order_quantity(
        entry_tick,
        stop_tick,
        state,
        config,
        max_fill_price=stop_limit,
    )
    preview.update(
        {
            "entry": entry_tick,
            "stop": stop_tick,
            "tp1": tp1_tick,
            "tp2": tp2_tick,
            "sizing": sizing,
        }
    )
    if sizing["quantity"] <= 0:
        return {**preview, "success": False, "error": sizing["error"]}
    final_gate = account_gate(state, config)
    preview["gate"] = final_gate
    if not final_gate["allowed"]:
        return {**preview, "success": False, "error": "; ".join(final_gate["reasons"])}
    if any(str(intent.get("setup_id")) == setup_id and _active_intent(intent) for intent in state["intents"]):
        return {**preview, "success": False, "error": "Setup bereits aktiv", "setup_id": setup_id}
    if any(str(position.get("ticker")) == ticker and position.get("quantity") for position in state["positions"]):
        return {**preview, "success": False, "error": "Brokerposition bereits offen", "setup_id": setup_id}
    if any(str(order.get("ticker")) == ticker for order in state["open_orders"]):
        return {**preview, "success": False, "error": "Brokerorder fuer Ticker bereits offen", "setup_id": setup_id}
    active_tickers = {
        str(position.get("ticker") or "").upper()
        for position in state["positions"]
        if position.get("quantity")
    }
    active_tickers.update(
        str(intent.get("ticker") or "").upper()
        for intent in state["intents"]
        if _active_intent(intent)
    )
    active_tickers.discard("")
    if len(active_tickers) >= config["max_positions"]:
        return {**preview, "success": False, "error": "Maximale Positionen erreicht"}
    refreshed_net_liquidation = _safe_float((state.get("account") or {}).get("net_liquidation")) or 0.0
    if refreshed_net_liquidation and (
        _current_exposure(state) + sizing["notional"]
        > refreshed_net_liquidation * config["max_total_exposure_pct"] / 100.0
    ):
        return {**preview, "success": False, "error": "Gesamtexposure-Limit erreicht"}

    account = str((state.get("account") or {}).get("selected") or "")
    if not account.startswith("DU"):
        return {**preview, "success": False, "error": "Kein Paperkonto fuer Risk-Reservation"}

    quantity = int(sizing["quantity"])
    allocations = [quantity] if quantity == 1 else [math.ceil(quantity / 2), math.floor(quantity / 2)]
    risk_intent = _risk_intent_payload(
        setup_id=setup_id,
        order_ref=order_ref,
        account=account,
        con_id=con_id,
        direction=direction,
        quantity=quantity,
        entry=entry_tick,
        stop=stop_tick,
        tp1=tp1_tick,
        tp2=tp2_tick,
        stop_limit=stop_limit,
        allocations=allocations,
        signal=signal,
    )
    try:
        risk_store = _risk_store()
    except Exception as exc:
        return {**preview, "success": False, "error": f"Risk-Ledger nicht verfuegbar: {str(exc)[:120]}"}
    try:
        execution_state = risk_store.execution_state()
    except Exception as exc:
        return {
            **preview,
            "success": False,
            "error": f"Execution-Generation nicht verfuegbar: {str(exc)[:120]}",
        }
    execution_generation = execution_state.get("generation")
    if execution_state.get("armed") is not True:
        return {**preview, "success": False, "error": "Execution-Generation ist disarmed"}
    if not isinstance(execution_generation, int) or execution_generation <= 0:
        return {**preview, "success": False, "error": "Execution-Generation ist ungueltig"}
    registered = risk_store.register_intent(risk_intent)
    if not registered.get("accepted"):
        return {**preview, "success": False, "error": "Unveraenderlicher Risk-Intent konfliktbehaftet"}
    now = datetime.now(timezone.utc)
    lease_key = f"submit:{setup_id}"
    owner_token = f"paper-autotrader:{uuid.uuid4().hex}"
    lease = risk_store.acquire_lease(
        lease_key,
        owner_token,
        now=now,
        ttl_seconds=_SUBMIT_LEASE_TTL_SECONDS,
    )
    if not lease.get("acquired"):
        return {**preview, "success": False, "error": "Risk-Submit-Lease ist belegt"}
    fence_token = lease["fence_token"]
    reservation = _risk_reservation_payload(risk_intent)
    admission = risk_store.reserve_if_allowed(
        reservation,
        net_liquidation=refreshed_net_liquidation,
        available_funds=(state.get("account") or {}).get("available_funds"),
        min_available_funds=config["min_available_funds"],
        positions=state["positions"],
        orders=state["open_orders"],
        policy=_risk_policy(config),
        gross_position_value=(state.get("account") or {}).get("gross_position_value"),
        max_total_exposure_pct=config["max_total_exposure_pct"],
        max_positions=config["max_positions"],
        orders_snapshot_complete=state.get("orders_snapshot_complete", False) is True,
        execution_generation=execution_generation,
        now=now,
        lease_key=lease_key,
        owner_token=owner_token,
        fence_token=fence_token,
    )
    if not admission.get("allowed"):
        reasons = admission.get("risk", {}).get("reasons", [])
        detail = ", ".join(str(reason) for reason in reasons) or str(admission.get("decision") or "risk_blocked")
        return {**preview, "success": False, "error": f"Risk-Admission blockiert: {detail}", "risk": admission.get("risk")}

    placed: List[Any] = []
    mappings: List[Dict[str, Any]] = []
    pending_execution_write_ids: List[str] = []
    intent = {
        "setup_id": setup_id,
        "order_ref": order_ref,
        "ticker": ticker,
        **risk_intent,
        "tp1": tp1_tick,
        "tp2": tp2_tick,
        "tick": tick,
        "risk_budget": sizing["risk_budget"],
        "notional": sizing["notional"],
        "status": "SUBMITTING",
        "risk_reservation_id": reservation["reservation_id"],
        "created_at": _utc_now(),
        "order_ids": [],
        "parent_order_ids": [],
    }
    state["intents"].append(intent)
    state_write(state)
    try:
        parent_ids: List[int] = []
        targets = [(tp1_tick, allocations[0])]
        if len(allocations) > 1 and allocations[1] > 0:
            targets.append((tp2_tick, allocations[1]))
        for index, (target, target_qty) in enumerate(targets, start=1):
            parent = Order(
                action=main_action,
                orderType="STP LMT",
                auxPrice=entry_tick,
                lmtPrice=stop_limit,
                totalQuantity=target_qty,
                account=account,
                orderRef=f"{order_ref}-P{index}",
                tif="DAY",
                outsideRth=False,
                transmit=False,
            )
            parent_authorized_fields = _authorized_order_fields(parent)
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            parent_trade = _place_order_with_execution_guard(
                risk_store,
                execution_generation,
                ib,
                contract,
                parent,
                placed,
                pending_execution_write_ids,
                setup_id=setup_id,
            )
            parent_id = int(parent_trade.order.orderId)
            parent_ids.append(parent_id)
            parent_mapping = _order_mapping_payload(
                risk_intent,
                parent_trade,
                role="PARENT",
                branch=index,
                authorized_fields=parent_authorized_fields,
            )
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            parent_registered = risk_store.register_intent_order(
                setup_id,
                parent_mapping,
                execution_generation=execution_generation,
            )
            if (
                not parent_registered.get("accepted")
                or parent_registered.get("conflict")
            ):
                raise RuntimeError(
                    "Risk-Mapping fuer Parent abgelehnt: "
                    + str(parent_registered.get("conflict") or "unknown")[:100]
                )
            mappings.append(parent_mapping)

            stop_order = Order(
                action=exit_action,
                orderType="STP",
                auxPrice=stop_tick,
                totalQuantity=target_qty,
                parentId=parent_id,
                account=account,
                orderRef=f"{order_ref}-S{index}",
                ocaGroup=f"{order_ref}-O{index}",
                ocaType=1,
                tif="GTC",
                outsideRth=False,
                transmit=False,
            )
            stop_authorized_fields = _authorized_order_fields(stop_order)
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            stop_trade = _place_order_with_execution_guard(
                risk_store,
                execution_generation,
                ib,
                contract,
                stop_order,
                placed,
                pending_execution_write_ids,
                setup_id=setup_id,
            )
            stop_mapping = _order_mapping_payload(
                risk_intent,
                stop_trade,
                role="STOP",
                branch=index,
                parent_order_id=parent_id,
                authorized_fields=stop_authorized_fields,
            )
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            stop_registered = risk_store.register_intent_order(
                setup_id,
                stop_mapping,
                execution_generation=execution_generation,
            )
            if (
                not stop_registered.get("accepted")
                or stop_registered.get("conflict")
            ):
                raise RuntimeError(
                    "Risk-Mapping fuer Stop abgelehnt: "
                    + str(stop_registered.get("conflict") or "unknown")[:100]
                )
            mappings.append(stop_mapping)

            take_profit = Order(
                action=exit_action,
                orderType="LMT",
                lmtPrice=target,
                totalQuantity=target_qty,
                parentId=parent_id,
                account=account,
                orderRef=f"{order_ref}-T{index}",
                ocaGroup=f"{order_ref}-O{index}",
                ocaType=1,
                tif="GTC",
                outsideRth=False,
                transmit=True,
            )
            target_authorized_fields = _authorized_order_fields(take_profit)
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            target_trade = _place_order_with_execution_guard(
                risk_store,
                execution_generation,
                ib,
                contract,
                take_profit,
                placed,
                pending_execution_write_ids,
                setup_id=setup_id,
            )
            target_mapping = _order_mapping_payload(
                risk_intent,
                target_trade,
                role="TARGET",
                branch=index,
                parent_order_id=parent_id,
                authorized_fields=target_authorized_fields,
            )
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            target_registered = risk_store.register_intent_order(
                setup_id,
                target_mapping,
                execution_generation=execution_generation,
            )
            if (
                not target_registered.get("accepted")
                or target_registered.get("conflict")
            ):
                raise RuntimeError(
                    "Risk-Mapping fuer Ziel abgelehnt: "
                    + str(target_registered.get("conflict") or "unknown")[:100]
                )
            mappings.append(target_mapping)
        if hasattr(ib, "sleep"):
            ib.sleep(0.25)

        broker_order_evidence = _broker_order_evidence_exactly_visible(
            ib, contract, mappings
        )
        if broker_order_evidence is None:
            raise RuntimeError("Broker-Sichtbarkeit der Order-Mappings fehlt")
        for mapping in mappings:
            _renew_submission_lease(
                risk_store,
                lease_key,
                owner_token,
                fence_token,
                execution_generation,
            )
            acknowledged_mapping = risk_store.register_intent_order(
                setup_id,
                mapping,
                execution_generation=execution_generation,
            )
            if (
                not acknowledged_mapping.get("accepted")
                or acknowledged_mapping.get("conflict")
            ):
                raise RuntimeError(
                    "Risk-Ack-Mapping abgelehnt: "
                    + str(acknowledged_mapping.get("conflict") or "unknown")[:100]
                )
        _renew_submission_lease(
            risk_store,
            lease_key,
            owner_token,
            fence_token,
            execution_generation,
        )
        visible = risk_store.mark_reservation_broker_visible(
            reservation["reservation_id"],
            [mapping["order_id"] for mapping in mappings],
            lease_key=lease_key,
            owner_token=owner_token,
            fence_token=fence_token,
            now=datetime.now(timezone.utc),
            broker_order_evidence=broker_order_evidence,
            execution_generation=execution_generation,
        )
        if not visible.get("updated"):
            raise RuntimeError("Risk-Reservation konnte nicht als broker-sichtbar markiert werden")
        for write_id in list(pending_execution_write_ids):
            acknowledged_claim = risk_store.acknowledge_execution_write(
                write_id,
                expected_generation=execution_generation,
            )
            if not acknowledged_claim.get("updated"):
                raise _ExecutionGenerationLost(
                    str(
                        acknowledged_claim.get("reason")
                        or "execution_claim_ack_failed"
                    )[:120]
                )
            pending_execution_write_ids.remove(write_id)
        _renew_submission_lease(
            risk_store,
            lease_key,
            owner_token,
            fence_token,
            execution_generation,
        )

        def persist_working_state() -> None:
            working_state = state_read()
            for saved in working_state["intents"]:
                if saved.get("setup_id") == setup_id:
                    saved["status"] = "WORKING"
                    saved["submitted_at"] = _utc_now()
                    saved["order_ids"] = [
                        int(trade.order.orderId) for trade in placed
                    ]
                    saved["parent_order_ids"] = parent_ids
                    saved["risk_reservation_status"] = "BROKER_VISIBLE"
            state_write(working_state)

        persisted = risk_store.run_if_execution_generation(
            execution_generation,
            persist_working_state,
            claim_context={
                "operation_kind": "LOCAL_STATE",
                "account": account,
                "setup_id": setup_id,
                "order_ref": order_ref,
            },
        )
        if (
            not persisted.get("executed")
            or persisted.get("reason")
            or persisted.get("armed") is not True
            or persisted.get("generation") != execution_generation
        ):
            raise _ExecutionGenerationLost(
                str(persisted.get("reason") or "execution_generation_fenced")[:120]
            )
        audit_log("Paper bracket submitted", "TRADE", ticker=ticker, setup_id=setup_id, quantity=quantity)
        return {
            **preview,
            "submitted": True,
            "levels": {"entry": entry_tick, "stop": stop_tick, "tp1": tp1_tick, "tp2": tp2_tick},
            "order_ids": [int(trade.order.orderId) for trade in placed],
        }
    except Exception as exc:
        claim_quarantine: Dict[str, Any] = {
            "updated": not pending_execution_write_ids,
            "reason": None,
        }
        if pending_execution_write_ids:
            try:
                claim_quarantine = risk_store.quarantine_execution_writes(
                    list(pending_execution_write_ids),
                    expected_generation=execution_generation,
                )
                if claim_quarantine.get("updated"):
                    pending_execution_write_ids.clear()
            except Exception as quarantine_exc:
                claim_quarantine = {
                    "updated": False,
                    "reason": str(quarantine_exc)[:120],
                }
        failure_reason = str(exc)
        if not claim_quarantine.get("updated"):
            failure_reason = (
                failure_reason
                + "; Execution-Claim-Quarantaene fehlgeschlagen: "
                + str(claim_quarantine.get("reason") or "unknown")[:120]
            )
        recovery = _recover_failed_submission(
            risk_store,
            ib,
            placed,
            reservation["reservation_id"],
            lease_key=lease_key,
            owner_token=owner_token,
            fence_token=fence_token,
            reason=failure_reason,
            force_new_lease=isinstance(exc, _LeaseFenceLost),
        )
        state = state_read()
        for saved in state["intents"]:
            if saved.get("setup_id") == setup_id:
                saved["status"] = "RECONCILE_REQUIRED"
                saved["risk_reservation_status"] = "RECONCILE_REQUIRED"
                saved["order_ids"] = [
                    int(getattr(getattr(trade, "order", None), "orderId", 0) or 0)
                    for trade in placed
                    if int(
                        getattr(getattr(trade, "order", None), "orderId", 0) or 0
                    )
                    > 0
                ]
                saved["parent_order_ids"] = [
                    int(getattr(getattr(trade, "order", None), "orderId", 0) or 0)
                    for trade in placed
                    if _is_parent_order_ref(
                        _order_ref(getattr(trade, "order", None))
                    )
                    and int(
                        getattr(getattr(trade, "order", None), "orderId", 0) or 0
                    )
                    > 0
                ]
                saved["error"] = failure_reason[:200]
                saved["execution_claim_quarantine"] = dict(claim_quarantine)
                saved["risk_recovery_pending"] = (
                    not claim_quarantine.get("updated", False)
                    or not recovery.get("updated", False)
                )
                saved["manual_reconciliation_required"] = bool(
                    pending_execution_write_ids
                    or (
                        placed
                        and not recovery.get("protection_removal_safe", False)
                    )
                )
                saved["protective_orders_retained"] = bool(
                    recovery.get("protective_orders_retained")
                )
                saved["protection_cancel_requested_order_ids"] = list(
                    recovery.get("protection_cancel_requested_order_ids") or []
                )
                saved["protection_cancel_pending_order_ids"] = list(
                    recovery.get("protection_cancel_pending_order_ids") or []
                )
                saved["protection_cancel_acknowledged_order_ids"] = list(
                    recovery.get("protection_cancel_acknowledged_order_ids") or []
                )
                saved["protection_cancel_failed_order_ids"] = list(
                    recovery.get("protection_cancel_failed_order_ids") or []
                )
        state_write(state)
        audit_log("Paper bracket failed", "ERROR", ticker=ticker, setup_id=setup_id, error=failure_reason[:200])
        return {**preview, "success": False, "submitted": False, "error": failure_reason[:200]}


def set_execution_armed(armed: bool) -> Dict[str, Any]:
    config = config_load()
    try:
        risk_store = _risk_store()
    except Exception as exc:
        risk_store = None
        store_error = str(exc)[:120]
    else:
        store_error = None

    fail_closed_values = {
        "mode": "paper_review",
        "kill_switch": True,
        "execution_enabled": False,
    }

    def persist_fail_closed() -> Tuple[Dict[str, Any], Optional[str]]:
        try:
            return (
                _config_save_internal(
                    fail_closed_values,
                    allow_execution_state=True,
                ),
                None,
            )
        except Exception as exc:
            fallback = dict(config)
            fallback.update(fail_closed_values)
            return fallback, str(exc)[:120]

    def fail_arming_closed(
        error: str,
        *,
        execution_state: Optional[Dict[str, Any]] = None,
        execution_drain: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        rollback_errors: List[str] = []
        rollback_state: Dict[str, Any] = execution_state or {
            "updated": False,
            "reason": "arming_failed_before_transition",
        }
        rollback_drain: Dict[str, Any] = execution_drain or {
            "drained": False,
            "active_count": None,
            "reason": "arming_failed_before_drain",
        }
        if risk_store is not None:
            try:
                rollback_state = risk_store.transition_execution_state(False)
            except Exception as exc:
                rollback_errors.append(f"durable_disarm_failed:{str(exc)[:120]}")
            else:
                try:
                    rollback_drain = risk_store.wait_for_execution_writes(
                        timeout_seconds=_EXECUTION_DRAIN_TIMEOUT_SECONDS
                    )
                except Exception as exc:
                    rollback_errors.append(
                        f"execution_drain_failed:{str(exc)[:120]}"
                    )
                if not rollback_drain.get("drained"):
                    rollback_errors.append(
                        "execution_writes_not_drained:"
                        + str(rollback_drain.get("reason") or "unknown")[:120]
                    )
        fail_closed_config, config_error = persist_fail_closed()
        if config_error:
            rollback_errors.append(f"fail_closed_config_failed:{config_error}")
        return {
            "ok": False,
            "error": str(error)[:200],
            "config": fail_closed_config,
            "execution_state": rollback_state,
            "execution_drain": rollback_drain,
            "errors": rollback_errors,
        }

    if armed:
        if risk_store is None:
            fail_closed_config, config_error = persist_fail_closed()
            errors = [] if config_error is None else [config_error]
            return {
                "ok": False,
                "error": f"Risk-Ledger nicht verfuegbar: {store_error}",
                "config": fail_closed_config,
                "errors": errors,
            }
        try:
            execution_state = risk_store.execution_state()
        except Exception as exc:
            return fail_arming_closed(
                f"Execution-Generation nicht pruefbar: {str(exc)[:120]}"
            )
        if execution_state.get("armed") is True:
            return fail_arming_closed(
                "Execution-Generation ist bereits armed",
                execution_state=execution_state,
            )
        try:
            execution_drain = risk_store.wait_for_execution_writes(
                timeout_seconds=_EXECUTION_DRAIN_TIMEOUT_SECONDS
            )
        except Exception as exc:
            return fail_arming_closed(
                f"Execution-Writes nicht pruefbar: {str(exc)[:120]}",
                execution_state=execution_state,
            )
        recovering_orphan = (
            execution_drain.get("drained") is not True
            and execution_drain.get("reason") == "execution_writes_orphaned"
            and int(execution_drain.get("active_count") or 0) == 0
        )
        if not execution_drain.get("drained") and not recovering_orphan:
            return fail_arming_closed(
                "Execution-Writes sind nicht vollstaendig drainiert",
                execution_state=execution_state,
                execution_drain=execution_drain,
            )
        if recovering_orphan:
            try:
                execution_state = risk_store.execution_state()
            except Exception as exc:
                return fail_arming_closed(
                    f"Orphan-Generation nicht pruefbar: {str(exc)[:120]}",
                    execution_state=execution_state,
                    execution_drain=execution_drain,
                )
            if execution_state.get("armed") is not False:
                return fail_arming_closed(
                    "Orphan-Recovery ist nicht dauerhaft disarmed",
                    execution_state=execution_state,
                    execution_drain=execution_drain,
                )
        try:
            state = (
                reconcile_broker(
                    require_fresh=True,
                    expected_recovery_generation=execution_state.get("generation"),
                )
                if recovering_orphan
                else reconcile_broker(require_fresh=True)
            )
        except Exception as exc:
            return fail_arming_closed(
                f"Broker-Reconciliation fehlgeschlagen: {str(exc)[:120]}",
                execution_state=execution_state,
                execution_drain=execution_drain,
            )
        if recovering_orphan:
            try:
                execution_drain = risk_store.wait_for_execution_writes(
                    timeout_seconds=_EXECUTION_DRAIN_TIMEOUT_SECONDS
                )
            except Exception as exc:
                return fail_arming_closed(
                    f"Orphan-Recovery-Drain nicht pruefbar: {str(exc)[:120]}",
                    execution_state=execution_state,
                    execution_drain=execution_drain,
                )
            if not execution_drain.get("drained"):
                return fail_arming_closed(
                    "Orphan-Execution-Write ist nicht kausal reconciled",
                    execution_state=execution_state,
                    execution_drain=execution_drain,
                )
        account = state.get("account") or {}
        if not state.get("broker_connected") or not account.get("paper"):
            return fail_arming_closed(
                state.get("broker_error") or "Paperkonto nicht bereit",
                execution_state=execution_state,
                execution_drain=execution_drain,
            )
        config.update({"mode": "paper_auto", "kill_switch": False, "execution_enabled": True})
        gate = account_gate(state, config)
        if not gate["allowed"]:
            return fail_arming_closed(
                "; ".join(gate["reasons"]),
                execution_state=execution_state,
                execution_drain=execution_drain,
            )
        try:
            verified_state = risk_store.execution_state()
            if (
                verified_state.get("armed") is not False
                or verified_state.get("generation") != execution_state.get("generation")
            ):
                return fail_arming_closed(
                    "Execution-Generation wurde waehrend Arming gefenced",
                    execution_state=verified_state,
                    execution_drain=execution_drain,
                )
            config = _config_save_internal(config, allow_execution_state=True)
            verified_config = config_load()
        except Exception as exc:
            return fail_arming_closed(
                f"Arming-Verifikation fehlgeschlagen: {str(exc)[:120]}",
                execution_state=locals().get("verified_state", execution_state),
                execution_drain=execution_drain,
            )
        if (
            verified_config.get("execution_enabled") is not True
            or verified_config.get("kill_switch") is not False
            or verified_config.get("mode") != "paper_auto"
        ):
            return fail_arming_closed(
                "Execution-Config wurde waehrend Arming gefenced",
                execution_state=verified_state,
                execution_drain=execution_drain,
            )
        try:
            audit_log(
                "Paper execution arming authorized",
                "WARN",
                account=account.get("selected"),
            )
        except Exception as exc:
            return fail_arming_closed(
                f"Arming-Audit fehlgeschlagen: {str(exc)[:120]}",
                execution_state=verified_state,
                execution_drain=execution_drain,
            )
        # This durable compare-and-swap is deliberately the final fallible
        # operation.  Until it commits, every submission still observes the
        # database as disarmed even though the file config has been prepared.
        try:
            execution_state = risk_store.transition_execution_state(
                True,
                expected_generation=verified_state.get("generation"),
                require_drained=True,
            )
        except Exception as exc:
            return fail_arming_closed(
                f"Execution-Generation konnte nicht aktiviert werden: {str(exc)[:120]}",
                execution_state=verified_state,
                execution_drain=execution_drain,
            )
        if not execution_state.get("updated"):
            return fail_arming_closed(
                "Execution-Generation konnte nicht aktiviert werden",
                execution_state=execution_state,
                execution_drain=execution_drain,
            )
        return {"ok": True, "config": config, "execution_state": execution_state}

    disarm_errors: List[str] = []
    if risk_store is None:
        execution_state = {"updated": False, "reason": store_error}
        execution_drain = {
            "drained": False,
            "active_count": None,
            "reason": "execution_generation_unavailable",
        }
    else:
        try:
            execution_state = risk_store.transition_execution_state(False)
        except Exception as exc:
            disarm_errors.append(f"durable_disarm_failed:{str(exc)[:120]}")
            try:
                execution_state = risk_store.transition_execution_state(False)
            except Exception as retry_exc:
                execution_state = {
                    "updated": False,
                    "reason": str(retry_exc)[:120],
                }
                disarm_errors.append(
                    f"durable_disarm_retry_failed:{str(retry_exc)[:120]}"
                )
        execution_drain = {
            "drained": False,
            "active_count": None,
            "reason": "durable_disarm_failed",
        }
        if execution_state.get("updated"):
            try:
                execution_drain = risk_store.wait_for_execution_writes(
                    timeout_seconds=_EXECUTION_DRAIN_TIMEOUT_SECONDS
                )
            except Exception as exc:
                execution_drain = {
                    "drained": False,
                    "active_count": None,
                    "reason": str(exc)[:120],
                }
                disarm_errors.append(f"execution_drain_failed:{str(exc)[:120]}")
            if not execution_drain.get("drained"):
                disarm_errors.append(
                    "execution_writes_not_drained:"
                    + str(execution_drain.get("reason") or "unknown")[:120]
                )
    config, config_error = persist_fail_closed()
    if config_error:
        disarm_errors.append(f"fail_closed_config_failed:{config_error}")
    try:
        audit_log("Paper execution disarmed", "INFO")
    except Exception as exc:
        disarm_errors.append(f"disarm_audit_failed:{str(exc)[:120]}")
    return {
        "ok": execution_state.get("updated") is True
        and execution_drain.get("drained") is True
        and not disarm_errors,
        "config": config,
        "execution_state": execution_state,
        "execution_drain": execution_drain,
        "errors": disarm_errors,
    }


def engage_kill_switch() -> Dict[str, Any]:
    """Disarm and cancel only unfilled Alpha Station parent entry orders."""
    errors: List[str] = []
    try:
        execution_state = _risk_store().transition_execution_state(False)
    except Exception as exc:
        execution_state = {"updated": False, "reason": str(exc)[:120]}
    if not execution_state.get("updated"):
        errors.append(
            "Execution-Generation konnte nicht gefenced werden: "
            + str(execution_state.get("reason") or "unknown")[:120]
        )
    fail_closed_values = {
        "mode": "paper_review",
        "kill_switch": True,
        "execution_enabled": False,
    }
    try:
        config = _config_save_internal(
            fail_closed_values,
            allow_execution_state=True,
        )
    except Exception as exc:
        config = dict(fail_closed_values)
        errors.append(f"Fail-Closed-Config konnte nicht gespeichert werden: {str(exc)[:120]}")
    execution_drain = {
        "drained": False,
        "active_count": None,
        "reason": "execution_generation_unavailable",
    }
    recovering_orphan = False
    if execution_state.get("updated"):
        try:
            execution_drain = _risk_store().wait_for_execution_writes(
                timeout_seconds=_EXECUTION_DRAIN_TIMEOUT_SECONDS
            )
        except Exception as exc:
            execution_drain = {
                "drained": False,
                "active_count": None,
                "reason": str(exc)[:120],
            }
        recovering_orphan = (
            execution_drain.get("drained") is not True
            and execution_drain.get("reason") == "execution_writes_orphaned"
            and int(execution_drain.get("active_count") or 0) == 0
        )
        if not execution_drain.get("drained") and not recovering_orphan:
            errors.append(
                "Execution-Writes nicht drainiert: "
                + str(execution_drain.get("reason") or "unknown")[:120]
            )
    cancel_candidates: List[Any] = []
    broker_order_refresh_complete = False
    broker_order_snapshot_complete = False
    ib = None
    if not ib_is_connected():
        errors.append("Broker-Order-Snapshot nicht verfuegbar: IBKR nicht verbunden")
    else:
        try:
            ib = _get_ib_state().get("ib")
        except Exception as exc:
            errors.append(
                "Broker-Order-Snapshot nicht verfuegbar: " + str(exc)[:120]
            )
        if ib is None:
            errors.append("Broker-Order-Snapshot nicht verfuegbar: IBKR-Objekt fehlt")
        else:
            try:
                refresh_open_orders = getattr(ib, "reqAllOpenOrdersAsync", None)
                run_bounded = getattr(ib, "run", None)
                error_event = getattr(ib, "errorEvent", None)
                if not callable(refresh_open_orders) or not callable(run_bounded):
                    raise RuntimeError("bounded open-order refresh is unavailable")
                if error_event is None:
                    raise RuntimeError("open-order refresh error evidence is unavailable")
                refresh_errors: List[str] = []

                def capture_refresh_error(
                    _request_id: Any,
                    error_code: Any,
                    error_string: Any,
                    *_args: Any,
                ) -> None:
                    if _broker_error_is_request_failure(error_code):
                        refresh_errors.append(
                            f"{str(error_code)[:20]}:{str(error_string)[:120]}"
                        )

                subscribed = False
                try:
                    error_event += capture_refresh_error
                    subscribed = True
                    raw_open_trades = run_bounded(
                        refresh_open_orders(),
                        timeout=_BROKER_ORDER_REFRESH_TIMEOUT_SECONDS,
                    )
                finally:
                    if subscribed:
                        error_event -= capture_refresh_error
                if refresh_errors:
                    raise RuntimeError(
                        "open-order refresh broker error: "
                        + "; ".join(refresh_errors)[:140]
                    )
                if raw_open_trades is None:
                    raise ValueError("open-order refresh returned None")
                open_trades = list(raw_open_trades)
                broker_order_refresh_complete = True
                if not ib_is_connected():
                    raise ConnectionError(
                        "IBKR connection lost after open-order refresh"
                    )
                broker_order_snapshot_complete = True
                for trade in open_trades:
                    order = getattr(trade, "order", None)
                    status = str(getattr(getattr(trade, "orderStatus", None), "status", "") or "").upper()
                    if (
                        _order_ref(order).startswith("AS2-")
                        and _is_parent_order_ref(_order_ref(order))
                        and int(getattr(order, "parentId", 0) or 0) == 0
                        and status not in {"FILLED", "CANCELLED", "APICANCELLED"}
                    ):
                        cancel_candidates.append(trade)
                        try:
                            ib.cancelOrder(order)
                        except Exception as exc:
                            errors.append(
                                "Parent-Cancel fehlgeschlagen "
                                f"({int(getattr(order, 'orderId', 0) or 0)}): "
                                + str(exc)[:120]
                            )
            except Exception as exc:
                broker_order_snapshot_complete = False
                errors.append(f"Broker-Order-Refresh fehlgeschlagen: {str(exc)[:160]}")

    def classify_cancel_candidates() -> Tuple[List[int], List[int], List[int]]:
        confirmed: List[int] = []
        pending: List[int] = []
        filled: List[int] = []
        for trade in cancel_candidates:
            order = getattr(trade, "order", None)
            order_id = int(getattr(order, "orderId", 0) or 0)
            status = getattr(trade, "orderStatus", None)
            token = str(getattr(status, "status", "") or "").strip().upper()
            filled_quantity = _safe_float(getattr(status, "filled", None))
            if token in {"FILLED", "PARTIALLYFILLED", "PARTIALLY FILLED"} or (
                filled_quantity is not None and filled_quantity > 0
            ):
                filled.append(order_id)
            elif token in {"CANCELLED", "APICANCELLED"} and filled_quantity == 0:
                confirmed.append(order_id)
            else:
                pending.append(order_id)
        return confirmed, pending, filled

    cancelled, pending_cancel, filled_during_kill = classify_cancel_candidates()
    sleep_method = getattr(ib, "sleep", None)
    for _ in range(20):
        if not pending_cancel:
            break
        if not callable(sleep_method):
            break
        try:
            sleep_method(0.1)
        except Exception as exc:
            errors.append(f"Cancel-Ack-Wait fehlgeschlagen: {str(exc)[:120]}")
            break
        cancelled, pending_cancel, filled_during_kill = classify_cancel_candidates()
    for order_id in pending_cancel:
        errors.append(f"Parent-Cancel nicht bestaetigt ({order_id})")
    for order_id in filled_during_kill:
        errors.append(f"Parent waehrend Kill gefuellt ({order_id})")
    if broker_order_snapshot_complete and not ib_is_connected():
        broker_order_snapshot_complete = False
        errors.append(
            "Broker-Order-Snapshot nicht mehr verifizierbar: IBKR-Verbindung verloren"
        )
    try:
        if recovering_orphan:
            reconcile_broker(
                require_fresh=True,
                expected_recovery_generation=execution_state.get("generation"),
            )
            execution_drain = _risk_store().wait_for_execution_writes(
                timeout_seconds=_EXECUTION_DRAIN_TIMEOUT_SECONDS
            )
            if not execution_drain.get("drained"):
                errors.append(
                    "Orphan-Execution-Write nicht kausal reconciled: "
                    + str(execution_drain.get("reason") or "unknown")[:120]
                )
        else:
            reconcile_broker()
    except Exception as exc:
        errors.append(f"Kill-Reconciliation fehlgeschlagen: {str(exc)[:120]}")
        if recovering_orphan:
            try:
                execution_drain = _risk_store().wait_for_execution_writes(
                    timeout_seconds=0
                )
            except Exception:
                pass
            if not execution_drain.get("drained"):
                errors.append(
                    "Orphan-Execution-Write bleibt blockierend: "
                    + str(execution_drain.get("reason") or "unknown")[:120]
                )
    try:
        audit_log("Kill switch engaged", "WARN", cancelled=cancelled, errors=errors)
    except Exception as exc:
        errors.append(f"Kill-Audit fehlgeschlagen: {str(exc)[:120]}")
    return {
        "ok": not errors,
        "config": config,
        "execution_state": execution_state,
        "execution_drain": execution_drain,
        "cancelled_entry_orders": cancelled,
        "pending_cancel_entry_orders": pending_cancel,
        "filled_entry_orders": filled_during_kill,
        "broker_order_refresh_complete": broker_order_refresh_complete,
        "broker_order_snapshot_complete": broker_order_snapshot_complete,
        "manual_reconciliation_required": (
            not broker_order_snapshot_complete
            or execution_drain.get("drained") is not True
            or bool(pending_cancel)
            or bool(filled_during_kill)
        ),
        "errors": errors,
    }


def tighten_stop(ticker: str, new_stop: float) -> Dict[str, Any]:
    """Modify an existing protective stop only if risk is reduced."""
    # A stop-price/transmit mutation changes the durable broker geometry.  The
    # current ledger has no fenced, crash-safe geometry-revision handshake, so
    # modifying IBKR first would make an authorized tighten indistinguishable
    # from tampering.  Keep this feature fail-closed until that protocol exists.
    return {"ok": False, "error": "authorized_geometry_revision_unavailable"}


def prune_terminal_intents() -> Dict[str, Any]:
    state = state_read()
    before = len(state["intents"])
    state["intents"] = [intent for intent in state["intents"] if _active_intent(intent)]
    state_write(state)
    return {"ok": True, "removed": before - len(state["intents"])}
