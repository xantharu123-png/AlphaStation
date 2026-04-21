"""
Broker Module — Interactive Brokers Integration (V69.9)

IB-Verbindung, Kontraktverwaltung und Order-Submission.
Benötigt ib_insync (optional).
"""
import threading
from datetime import datetime


def _debug_log(msg, error=None):
    """Minimaler Debug-Logger für Broker-Modul."""
    if error:
        _IB_STATE.setdefault("errors", []).append(f"{msg}: {error}")



# Check if ib_insync is available
try:
    from ib_insync import IB, Stock, Future, Forex, Crypto, LimitOrder, StopOrder, Order
    IB_INSYNC_AVAILABLE = True
except ImportError:
    IB_INSYNC_AVAILABLE = False
    IB = Stock = Future = Forex = Crypto = LimitOrder = StopOrder = Order = None


_IB_STATE_LOCK = threading.RLock()
_IB_STATE = {
    "ib": None,
    "connected": False,
    "error": None,
    "connect_time": None,
    "errors": [],
}


def _get_ib_state():
    """Cached IB connection state shared across the server process."""
    return _IB_STATE


def ib_connect(host="127.0.0.1", port=7497, client_id=1):
    """Connect to TWS. Returns True on success."""
    if not IB_INSYNC_AVAILABLE:
        return False
    with _IB_STATE_LOCK:
        state = _get_ib_state()
        # Already connected?
        if state["connected"] and state["ib"]:
            try:
                if state["ib"].isConnected():
                    return True
            except Exception:
                pass

        # Clean up stale client before reconnecting
        if state["ib"]:
            try:
                state["ib"].disconnect()
            except Exception:
                pass

        # New connection
        try:
            ib = IB()
            ib.connect(host, port, clientId=client_id, timeout=5)
            state["ib"] = ib
            state["connected"] = True
            state["error"] = None
            state["connect_time"] = datetime.now()
            return True
        except ConnectionRefusedError:
            state["ib"] = None
            state["connected"] = False
            state["error"] = "TWS nicht gestartet! Starte TWS/IB Gateway zuerst."
            return False
        except Exception as e:
            state["ib"] = None
            state["connected"] = False
            state["error"] = str(e)[:100]
            _debug_log("IB connect failed", e)
            return False


def ib_disconnect():
    """Gracefully disconnect from TWS."""
    with _IB_STATE_LOCK:
        state = _get_ib_state()
        if state["ib"]:
            try:
                state["ib"].disconnect()
            except Exception:
                pass
        state["ib"] = None
        state["connected"] = False
        state["error"] = None
        state["connect_time"] = None


def ib_is_connected():
    """Check if TWS connection is alive."""
    state = _get_ib_state()
    if not state["connected"] or not state["ib"]:
        return False
    try:
        is_connected = state["ib"].isConnected()
        state["connected"] = bool(is_connected)
        if not is_connected:
            state["ib"] = None
            state["connect_time"] = None
        return is_connected
    except Exception:
        state["connected"] = False
        state["ib"] = None
        state["connect_time"] = None
        return False


def ib_get_contract(ticker, market_type, exchange="US"):
    """Create IB contract object based on market type."""
    if not IB_INSYNC_AVAILABLE:
        return None
    try:
        if market_type == "Aktien":
            if exchange == "US":
                return Stock(ticker, "SMART", "USD")
            elif exchange == "DE":
                return Stock(ticker.replace(".DE", ""), "IBIS", "EUR")
            elif exchange == "UK":
                return Stock(ticker.replace(".L", ""), "LSE", "GBP")
            else:
                return Stock(ticker, "SMART", "USD")
        elif market_type == "Futures":
            # Common futures mapping
            futures_map = {
                "ES": ("ES", "CME"), "NQ": ("NQ", "CME"), "YM": ("YM", "CBOT"),
                "CL": ("CL", "NYMEX"), "GC": ("GC", "COMEX"), "SI": ("SI", "COMEX"),
                "ZB": ("ZB", "CBOT"), "ZN": ("ZN", "CBOT"), "VX": ("VIX", "CFE"),
                "RTY": ("RTY", "CME"), "6E": ("EUR", "CME"), "6J": ("JPY", "CME"),
            }
            base = ticker.split("=")[0] if "=" in ticker else ticker
            if base in futures_map:
                sym, exch = futures_map[base]
                return Future(sym, exchange=exch)
            return Future(base, exchange="CME")
        elif market_type == "Forex":
            if len(ticker) >= 6:
                pair = ticker.replace("/", "").replace(".", "")
                return Forex(pair[:3] + pair[3:6])
            return None
        elif market_type == "Krypto":
            crypto_map = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "AVAX": "AVAX"}
            sym = ticker.split("-")[0].split("/")[0].upper()
            if sym in crypto_map:
                return Crypto(sym, "PAXOS", "USD")
            return None
    except Exception as e:
        _debug_log(f"IB contract creation failed: {ticker}", e)
    return None


def ib_calc_shares(price, size_value, size_type="Shares"):
    """Calculate number of shares from dollar amount or direct shares."""
    if size_type == "Dollar":
        if price <= 0:
            return 0
        return max(1, int(size_value / price))
    return max(1, int(size_value))


def ib_submit_bracket(ticker, entry, sl, tp_list, shares, direction, market_type, exchange="US"):
    """
    Submit bracket order to TWS with transmit=False.
    User must confirm in TWS manually.

    Returns: dict with success, message, order_ids
    """
    if not ib_is_connected():
        return {"success": False, "message": "Nicht mit TWS verbunden!", "order_ids": []}

    state = _get_ib_state()
    ib = state["ib"]

    # Create contract
    contract = ib_get_contract(ticker, market_type, exchange)
    if not contract:
        return {"success": False, "message": f"Ticker '{ticker}' nicht erkannt!", "order_ids": []}

    # Qualify contract
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return {"success": False, "message": f"Contract nicht gefunden: {ticker}", "order_ids": []}
    except Exception as e:
        return {"success": False, "message": f"Contract-Fehler: {str(e)[:80]}", "order_ids": []}

    # Validate levels
    if direction == "LONG":
        if sl >= entry:
            return {"success": False, "message": "Stop-Loss muss unter Entry liegen (LONG)!", "order_ids": []}
        for tp in tp_list:
            if tp and tp <= entry:
                return {"success": False, "message": "Take-Profit muss über Entry liegen (LONG)!", "order_ids": []}
    else:  # SHORT
        if sl <= entry:
            return {"success": False, "message": "Stop-Loss muss über Entry liegen (SHORT)!", "order_ids": []}
        for tp in tp_list:
            if tp and tp >= entry:
                return {"success": False, "message": "Take-Profit muss unter Entry liegen (SHORT)!", "order_ids": []}

    try:
        # Main action
        main_action = "BUY" if direction == "LONG" else "SELL"
        exit_action = "SELL" if direction == "LONG" else "BUY"

        # Parent order — Limit at entry price
        parent = Order(
            action=main_action,
            orderType="LMT",
            lmtPrice=round(entry, 2),
            totalQuantity=shares,
            transmit=False
        )
        parent_trade = ib.placeOrder(contract, parent)
        parent_id = parent_trade.order.orderId

        # Stop Loss order
        stop_order = Order(
            action=exit_action,
            orderType="STP",
            auxPrice=round(sl, 2),
            totalQuantity=shares,
            parentId=parent_id,
            transmit=False
        )
        stop_trade = ib.placeOrder(contract, stop_order)

        # Take Profit orders
        tp_clean = [tp for tp in tp_list if tp and tp > 0]
        order_ids = [parent_id, stop_trade.order.orderId]

        if len(tp_clean) == 1:
            # Single TP — all shares
            tp_order = Order(
                action=exit_action,
                orderType="LMT",
                lmtPrice=round(tp_clean[0], 2),
                totalQuantity=shares,
                parentId=parent_id,
                transmit=False  # Last child still False — user confirms in TWS
            )
            tp_trade = ib.placeOrder(contract, tp_order)
            order_ids.append(tp_trade.order.orderId)
        elif len(tp_clean) >= 2:
            # Split shares between TP1 and TP2
            tp1_shares = shares // 2
            tp2_shares = shares - tp1_shares

            tp1_order = Order(
                action=exit_action,
                orderType="LMT",
                lmtPrice=round(tp_clean[0], 2),
                totalQuantity=tp1_shares,
                parentId=parent_id,
                transmit=False
            )
            tp1_trade = ib.placeOrder(contract, tp1_order)
            order_ids.append(tp1_trade.order.orderId)

            tp2_order = Order(
                action=exit_action,
                orderType="LMT",
                lmtPrice=round(tp_clean[1], 2),
                totalQuantity=tp2_shares,
                parentId=parent_id,
                transmit=False
            )
            tp2_trade = ib.placeOrder(contract, tp2_order)
            order_ids.append(tp2_trade.order.orderId)

        ib.sleep(0.3)  # Brief wait for TWS to process

        return {
            "success": True,
            "message": f"Bracket Order in TWS bereit! {len(order_ids)} Orders warten auf Bestätigung.",
            "order_ids": order_ids,
            "parent_id": parent_id
        }
    except Exception as e:
        return {"success": False, "message": f"Order-Fehler: {str(e)[:100]}", "order_ids": []}


