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
import tempfile
import threading
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from modules.brokers import Order, _get_ib_state, ib_get_contract, ib_is_connected
from modules.trade_levels import trade_geometry


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


def _account_values(ib: Any, account: str) -> Dict[str, float]:
    rows: Iterable[Any] = []
    try:
        rows = ib.accountValues(account) if account else ib.accountValues()
    except Exception:
        try:
            rows = ib.accountSummary(account) if account else ib.accountSummary()
        except Exception:
            rows = []
    values: Dict[str, float] = {}
    priorities: Dict[str, int] = {}
    for row in rows or []:
        if account and str(getattr(row, "account", "") or "") not in {"", account}:
            continue
        tag = str(getattr(row, "tag", "") or "")
        value = _safe_float(getattr(row, "value", None))
        currency = str(getattr(row, "currency", "") or "").strip().upper()
        priority = 2 if currency in {"", "BASE"} else 1
        if tag and value is not None and priority >= priorities.get(tag, 0):
            values[tag] = value
            priorities[tag] = priority
    return values


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
        "parent_id": int(getattr(order, "parentId", 0) or 0),
        "order_ref": _order_ref(order),
        "account": str(getattr(order, "account", "") or ""),
        "action": str(getattr(order, "action", "") or ""),
        "order_type": str(getattr(order, "orderType", "") or ""),
        "quantity": _safe_float(getattr(order, "totalQuantity", None)),
        "limit_price": _safe_float(getattr(order, "lmtPrice", None)),
        "stop_price": _safe_float(getattr(order, "auxPrice", None)),
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
        "exec_id": str(getattr(execution, "execId", "") or ""),
        "order_id": int(getattr(execution, "orderId", 0) or 0),
        "perm_id": int(getattr(execution, "permId", 0) or 0),
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
    matching = [order for order in orders if base_ref and str(order.get("order_ref") or "").startswith(base_ref)]
    parents = [order for order in matching if _is_parent_order_ref(order.get("order_ref"))]
    broker_position = next((position for position in positions if position.get("ticker") == ticker and position.get("quantity")), None)
    old_status = str(intent.get("status") or "UNKNOWN").upper()
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


def reconcile_broker() -> Dict[str, Any]:
    """Replace local market truth with the current IBKR paper account snapshot."""
    state = state_read()
    config = config_load()
    state["last_reconcile"] = _utc_now()
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

    try:
        raw_positions = ib.positions(account) if account else ib.positions()
    except TypeError:
        raw_positions = ib.positions()
    positions = [
        _serialize_position(position)
        for position in (raw_positions or [])
        if str(getattr(position, "account", "") or account) == account
    ]
    try:
        trades = list(ib.openTrades() or [])
    except Exception:
        trades = []
    orders = [
        _serialize_trade(trade)
        for trade in trades
        if str(getattr(getattr(trade, "order", None), "account", "") or account) == account
    ]
    try:
        raw_fills = list(ib.fills() or [])
    except Exception:
        raw_fills = []
    fills = [
        _serialize_fill(fill)
        for fill in raw_fills
        if str(getattr(getattr(fill, "execution", None), "acctNumber", "") or account) == account
    ]

    values = _account_values(ib, account)
    net_liquidation = values.get("NetLiquidation")
    available_funds = values.get("AvailableFunds", values.get("FullAvailableFunds"))
    buying_power = values.get("BuyingPower")
    gross_position_value = values.get("GrossPositionValue")
    if gross_position_value is None and values.get("StockMarketValue") is not None:
        gross_position_value = abs(values["StockMarketValue"])
    daily_pnl = _daily_pnl(ib, account, values)
    daily_pnl_pct = (
        daily_pnl / net_liquidation * 100.0
        if daily_pnl is not None and net_liquidation and net_liquidation > 0
        else None
    )

    intents = state.get("intents", [])
    cooldown_tickers = state.get("cooldown_tickers", {})
    today = datetime.now(timezone.utc).date().isoformat()
    for intent in intents:
        parent_filled, _ = _intent_fill_flags(intent, fills)
        intent["status"] = _intent_status(intent, orders, positions, fills)
        first_fill = (
            (parent_filled or intent["status"] in {"ACTIVE", "FILLED_NO_POSITION"})
            and not intent.get("filled_at")
        )
        if first_fill:
            intent["filled_at"] = state["last_reconcile"]
            ticker = str(intent.get("ticker") or "").upper()
            if ticker:
                cooldown_tickers[ticker] = today
        intent["reconciled_at"] = state["last_reconcile"]

    trades_today = len(
        {
            str(intent.get("setup_id"))
            for intent in intents
            if str(intent.get("filled_at") or "")[:10] == today and intent.get("setup_id")
        }
    )
    state.update({
        "broker_connected": True,
        "broker_error": None,
        "positions": positions,
        "open_orders": orders,
        "fills": fills[-300:],
        "intents": intents[-500:],
        "cooldown_tickers": cooldown_tickers,
        "account": {
            "managed_accounts": accounts,
            "selected": account,
            "paper": bool(account and account.startswith("DU")),
            "net_liquidation": net_liquidation,
            "available_funds": available_funds,
            "buying_power": buying_power,
            "gross_position_value": abs(gross_position_value) if gross_position_value is not None else None,
        },
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
        "trades_today": trades_today,
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
    if not account.get("paper") or not str(account.get("selected") or "").startswith("DU"):
        reasons.append("Kein eindeutig ausgewaehltes Paperkonto")
    net_liquidation = _safe_float(account.get("net_liquidation"))
    available_funds = _safe_float(account.get("available_funds"))
    if net_liquidation is None or net_liquidation <= 0:
        reasons.append("NetLiquidation fehlt")
    if available_funds is None:
        reasons.append("AvailableFunds fehlt")
    elif available_funds < config["min_available_funds"]:
        reasons.append("Verfuegbare Mittel unter Mindestreserve")
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


def _setup_id(signal: Dict[str, Any]) -> str:
    material = "|".join(
        str(signal.get(key) or "")
        for key in ("ticker", "direction", "trigger_bar_date", "entry", "stop", "tp1", "tp2")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:18].upper()


def _active_intent(intent: Dict[str, Any]) -> bool:
    return str(intent.get("status") or "").upper() in {"SUBMITTING", "WORKING", "ACTIVE", "FILLED_NO_POSITION"}


def calculate_order_quantity(
    entry: float,
    stop: float,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    risk_per_share = abs(float(entry) - float(stop))
    account = state.get("account") or {}
    net_liquidation = _safe_float(account.get("net_liquidation"))
    available_funds = _safe_float(account.get("available_funds"))
    if risk_per_share <= 0 or not net_liquidation or available_funds is None:
        return {"quantity": 0, "error": "Kontorisiko oder Stop-Distanz ungueltig"}
    risk_budget = net_liquidation * config["risk_per_trade_pct"] / 100.0
    risk_qty = math.floor(risk_budget / risk_per_share)
    notional_qty = math.floor(config["max_notional_per_trade"] / entry)
    cash_qty = math.floor(max(0.0, available_funds - config["min_available_funds"]) / entry)
    quantity = max(0, min(risk_qty, notional_qty, cash_qty, int(config["max_shares"])))
    return {
        "quantity": quantity,
        "risk_budget": round(risk_budget, 2),
        "risk_per_share": risk_per_share,
        "notional": round(quantity * entry, 2),
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

    tick = contract_min_tick(ib, contract, entry)
    if direction == "LONG":
        main_action, exit_action = "BUY", "SELL"
        entry_tick = round_to_tick(entry, tick, "up")
        stop_limit = round_to_tick(
            _safe_float(signal.get("stop_limit")) or entry * 1.003,
            tick,
            "up",
        )
        stop_tick = round_to_tick(stop, tick, "down")
        tp1_tick = round_to_tick(tp1, tick, "down")
        tp2_tick = round_to_tick(tp2, tick, "down")
    else:
        main_action, exit_action = "SELL", "BUY"
        entry_tick = round_to_tick(entry, tick, "down")
        stop_limit = round_to_tick(
            _safe_float(signal.get("stop_limit")) or entry * 0.997,
            tick,
            "down",
        )
        stop_tick = round_to_tick(stop, tick, "up")
        tp1_tick = round_to_tick(tp1, tick, "up")
        tp2_tick = round_to_tick(tp2, tick, "up")
    rounded_geometry = trade_geometry(entry_tick, stop_tick, tp1_tick, tp2_tick, direction)
    if not rounded_geometry.get("valid"):
        return {**preview, "success": False, "error": "Tick-Rundung zerstoert Order-Geometrie"}

    quantity = int(sizing["quantity"])
    allocations = [quantity] if quantity == 1 else [math.ceil(quantity / 2), math.floor(quantity / 2)]
    placed: List[Any] = []
    intent = {
        "setup_id": setup_id,
        "order_ref": order_ref,
        "ticker": ticker,
        "direction": direction,
        "account": account,
        "quantity": quantity,
        "entry": entry_tick,
        "stop": stop_tick,
        "tp1": tp1_tick,
        "tp2": tp2_tick,
        "tick": tick,
        "risk_budget": sizing["risk_budget"],
        "notional": sizing["notional"],
        "status": "SUBMITTING",
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
            parent_trade = ib.placeOrder(contract, parent)
            placed.append(parent_trade)
            parent_id = int(parent_trade.order.orderId)
            parent_ids.append(parent_id)

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
                transmit=False,
            )
            placed.append(ib.placeOrder(contract, stop_order))

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
                transmit=True,
            )
            placed.append(ib.placeOrder(contract, take_profit))
        if hasattr(ib, "sleep"):
            ib.sleep(0.25)

        state = state_read()
        for saved in state["intents"]:
            if saved.get("setup_id") == setup_id:
                saved["status"] = "WORKING"
                saved["submitted_at"] = _utc_now()
                saved["order_ids"] = [int(trade.order.orderId) for trade in placed]
                saved["parent_order_ids"] = parent_ids
        state_write(state)
        audit_log("Paper bracket submitted", "TRADE", ticker=ticker, setup_id=setup_id, quantity=quantity)
        return {
            **preview,
            "submitted": True,
            "levels": {"entry": entry_tick, "stop": stop_tick, "tp1": tp1_tick, "tp2": tp2_tick},
            "order_ids": [int(trade.order.orderId) for trade in placed],
        }
    except Exception as exc:
        for trade in reversed(placed):
            try:
                ib.cancelOrder(trade.order)
            except Exception:
                pass
        state = state_read()
        for saved in state["intents"]:
            if saved.get("setup_id") == setup_id:
                saved["status"] = "ERROR"
                saved["error"] = str(exc)[:200]
        state_write(state)
        audit_log("Paper bracket failed", "ERROR", ticker=ticker, setup_id=setup_id, error=str(exc)[:200])
        return {**preview, "success": False, "submitted": False, "error": str(exc)[:200]}


def set_execution_armed(armed: bool) -> Dict[str, Any]:
    config = config_load()
    if armed:
        state = reconcile_broker()
        account = state.get("account") or {}
        if not state.get("broker_connected") or not account.get("paper"):
            return {"ok": False, "error": state.get("broker_error") or "Paperkonto nicht bereit"}
        config.update({"mode": "paper_auto", "kill_switch": False, "execution_enabled": True})
        config = _config_save_internal(config, allow_execution_state=True)
        gate = account_gate(state, config)
        if not gate["allowed"]:
            config = _config_save_internal(
                {"mode": "paper_review", "kill_switch": True, "execution_enabled": False},
                allow_execution_state=True,
            )
            return {"ok": False, "error": "; ".join(gate["reasons"]), "config": config}
        audit_log("Paper execution armed", "WARN", account=account.get("selected"))
        return {"ok": True, "config": config}
    config = _config_save_internal(
        {"mode": "paper_review", "kill_switch": True, "execution_enabled": False},
        allow_execution_state=True,
    )
    audit_log("Paper execution disarmed", "INFO")
    return {"ok": True, "config": config}


def engage_kill_switch() -> Dict[str, Any]:
    """Disarm and cancel only unfilled Alpha Station parent entry orders."""
    config = _config_save_internal(
        {"mode": "paper_review", "kill_switch": True, "execution_enabled": False},
        allow_execution_state=True,
    )
    cancelled: List[int] = []
    errors: List[str] = []
    if ib_is_connected():
        ib = _get_ib_state().get("ib")
        if ib:
            try:
                for trade in list(ib.openTrades() or []):
                    order = getattr(trade, "order", None)
                    status = str(getattr(getattr(trade, "orderStatus", None), "status", "") or "").upper()
                    if (
                        _order_ref(order).startswith("AS2-")
                        and _is_parent_order_ref(_order_ref(order))
                        and int(getattr(order, "parentId", 0) or 0) == 0
                        and status not in {"FILLED", "CANCELLED", "APICANCELLED"}
                    ):
                        ib.cancelOrder(order)
                        cancelled.append(int(getattr(order, "orderId", 0) or 0))
            except Exception as exc:
                errors.append(str(exc)[:200])
    audit_log("Kill switch engaged", "WARN", cancelled=cancelled, errors=errors)
    reconcile_broker()
    return {"ok": not errors, "config": config, "cancelled_entry_orders": cancelled, "errors": errors}


def tighten_stop(ticker: str, new_stop: float) -> Dict[str, Any]:
    """Modify an existing protective stop only if risk is reduced."""
    state = reconcile_broker()
    config = config_load()
    account = state.get("account") or {}
    if not state.get("broker_connected") or not account.get("paper"):
        return {"ok": False, "error": state.get("broker_error") or "Paperkonto nicht bereit"}
    ticker = str(ticker or "").upper().strip()
    requested = _safe_float(new_stop)
    if not ticker or requested is None or requested <= 0:
        return {"ok": False, "error": "Ticker oder Stop ungueltig"}
    position = next((row for row in state["positions"] if row.get("ticker") == ticker and row.get("quantity")), None)
    if not position:
        return {"ok": False, "error": "Keine Brokerposition fuer Ticker"}
    stop_snapshots = [
        row for row in state["open_orders"]
        if row.get("ticker") == ticker
        and str(row.get("order_type") or "").upper() in {"STP", "STOP"}
        and str(row.get("order_ref") or "").startswith("AS2-")
    ]
    if not stop_snapshots:
        return {"ok": False, "error": "Kein verwalteter Schutz-Stop gefunden"}
    direction = str(position.get("direction") or "LONG")
    current_stops = [_safe_float(row.get("stop_price")) for row in stop_snapshots]
    if any(stop is None for stop in current_stops):
        return {"ok": False, "error": "Aktueller Stop fehlt"}
    if direction == "LONG" and any(requested <= stop for stop in current_stops):
        return {"ok": False, "error": "Stop darf bei LONG niemals gelockert werden"}
    if direction == "SHORT" and any(requested >= stop for stop in current_stops):
        return {"ok": False, "error": "Stop darf bei SHORT niemals gelockert werden"}

    ib = _get_ib_state().get("ib")
    trades_by_id = {
        int(getattr(getattr(trade, "order", None), "orderId", 0) or 0): trade
        for trade in list(ib.openTrades() or [])
    }
    target_trades = [trades_by_id.get(int(row["order_id"])) for row in stop_snapshots]
    if any(trade is None for trade in target_trades):
        return {"ok": False, "error": "Nicht alle Broker-Stops konnten zugeordnet werden"}

    modified_ids: List[int] = []
    rounded_levels: List[float] = []
    try:
        for target_trade in target_trades:
            contract = target_trade.contract
            tick = contract_min_tick(ib, contract, requested)
            rounded = round_to_tick(requested, tick, "up" if direction == "SHORT" else "down")
            target_trade.order.auxPrice = rounded
            target_trade.order.transmit = True
            modified = ib.placeOrder(contract, target_trade.order)
            modified_ids.append(int(modified.order.orderId))
            rounded_levels.append(rounded)
        if hasattr(ib, "sleep"):
            ib.sleep(0.2)
        audit_log(
            "Protective stops tightened",
            "TRADE",
            ticker=ticker,
            old_stops=current_stops,
            new_stops=rounded_levels,
            account=account.get("selected"),
        )
        return {
            "ok": True,
            "ticker": ticker,
            "old_stops": current_stops,
            "new_stop": rounded_levels[0],
            "order_ids": modified_ids,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "modified_order_ids": modified_ids}


def prune_terminal_intents() -> Dict[str, Any]:
    state = state_read()
    before = len(state["intents"])
    state["intents"] = [intent for intent in state["intents"] if _active_intent(intent)]
    state_write(state)
    return {"ok": True, "removed": before - len(state["intents"])}
