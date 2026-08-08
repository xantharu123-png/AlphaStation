from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from modules import paper_autotrader as pa


@pytest.fixture
def isolated_autotrader_store(monkeypatch, tmp_path):
    data_dir = tmp_path / "autotrader"
    monkeypatch.setattr(pa, "_DATA_DIR", data_dir)
    monkeypatch.setattr(pa, "_CONFIG_FILE", data_dir / "config.json")
    monkeypatch.setattr(pa, "_STATE_FILE", data_dir / "state.json")
    monkeypatch.setattr(pa, "_LOG_FILE", data_dir / "audit.json")
    monkeypatch.setattr(pa, "_STOP_FILE", data_dir / "stop.requested")
    return data_dir


def _account_row(tag, value, currency="BASE", account="DU123"):
    return SimpleNamespace(account=account, tag=tag, value=str(value), currency=currency)


def _fill(order_id, side, price, *, ticker="XYZ", account="DU123"):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=ticker),
        execution=SimpleNamespace(
            execId=f"EXEC-{order_id}",
            orderId=order_id,
            permId=1000 + order_id,
            acctNumber=account,
            side=side,
            shares=10,
            price=price,
        ),
        time=datetime.now(timezone.utc),
    )


class _ReconcileIB:
    def __init__(self, *, fills=None, positions=None, open_trades=None, values=None):
        self._fills = fills or []
        self._positions = positions or []
        self._open_trades = open_trades or []
        self._values = values or [
            _account_row("NetLiquidation", 100000),
            _account_row("AvailableFunds", 50000),
            _account_row("BuyingPower", 100000),
            _account_row("GrossPositionValue", 0),
            _account_row("DailyPnL", 0),
        ]

    def managedAccounts(self):
        return ["DU123"]

    def positions(self, _account=None):
        return self._positions

    def openTrades(self):
        return self._open_trades

    def fills(self):
        return self._fills

    def accountValues(self, _account=None):
        return self._values


class _FakeOrder:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.orderId = int(getattr(self, "orderId", 0) or 0)
        self.parentId = int(getattr(self, "parentId", 0) or 0)


class _OrderIB:
    def __init__(self):
        self.next_order_id = 1
        self.placed = []

    def qualifyContracts(self, contract):
        return [contract]

    def reqContractDetails(self, _contract):
        return [SimpleNamespace(minTick=0.01)]

    def placeOrder(self, contract, order):
        if not order.orderId:
            order.orderId = self.next_order_id
            self.next_order_id += 1
        trade = SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(
                status="Submitted",
                filled=0,
                remaining=getattr(order, "totalQuantity", 0),
                avgFillPrice=0,
            ),
        )
        self.placed.append(trade)
        return trade

    def sleep(self, _seconds):
        return None


def test_risk_config_cannot_arm_execution(isolated_autotrader_store):
    saved = pa.config_save(
        {
            "risk_per_trade_pct": 0.4,
            "mode": "paper_auto",
            "paper_only": False,
            "execution_enabled": True,
            "kill_switch": False,
        }
    )

    assert saved["risk_per_trade_pct"] == pytest.approx(0.4)
    assert saved["paper_only"] is True
    assert saved["mode"] == "paper_review"
    assert saved["execution_enabled"] is False
    assert saved["kill_switch"] is True


def test_account_values_prefer_base_currency():
    ib = _ReconcileIB(
        values=[
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("NetLiquidation", 999, "USD"),
            _account_row("AvailableFunds", 50000, "BASE"),
        ]
    )

    values = pa._account_values(ib, "DU123")

    assert values["NetLiquidation"] == pytest.approx(100000)
    assert values["AvailableFunds"] == pytest.approx(50000)


def test_live_account_is_never_selected():
    account, error = pa._select_paper_account(["U123456"], "U123456")

    assert account is None
    assert "Live-Konto blockiert" in error


def test_reconcile_captures_complete_trade_between_polls(monkeypatch, isolated_autotrader_store):
    pa.config_save({"selected_account": "DU123"})
    pa.state_write(
        {
            "status": "running",
            "intents": [
                {
                    "setup_id": "SETUP-1",
                    "order_ref": "AS2-SETUP-1",
                    "ticker": "XYZ",
                    "status": "WORKING",
                    "order_ids": [10, 11],
                    "parent_order_ids": [10],
                }
            ],
        }
    )
    ib = _ReconcileIB(fills=[_fill(10, "BOT", 10), _fill(11, "SLD", 11)])
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})

    state = pa.reconcile_broker()

    intent = state["intents"][0]
    assert intent["status"] == "TERMINAL"
    assert intent["filled_at"]
    assert state["cooldown_tickers"]["XYZ"] == datetime.now(timezone.utc).date().isoformat()
    assert state["trades_today"] == 1
    assert state["account"]["paper"] is True
    assert state["account"]["gross_position_value"] == pytest.approx(0)


def test_broker_gross_position_value_is_used_for_exposure():
    state = {
        "account": {"gross_position_value": 5200},
        "positions": [{"ticker": "XYZ", "quantity": 10, "avg_cost": 10}],
    }

    assert pa._current_exposure(state) == pytest.approx(5200)


def test_submit_signal_builds_two_oca_brackets_without_starting_cooldown(
    monkeypatch, isolated_autotrader_store
):
    config = pa._config_save_internal(
        {
            "selected_account": "DU123",
            "mode": "paper_auto",
            "paper_only": True,
            "execution_enabled": True,
            "kill_switch": False,
        },
        allow_execution_state=True,
    )
    assert pa.account_gate(
        {
            "broker_connected": True,
            "broker_error": None,
            "account": {
                "selected": "DU123",
                "paper": True,
                "net_liquidation": 100000,
                "available_funds": 50000,
            },
            "daily_pnl": 0,
        },
        config,
    )["allowed"] is True

    pa.state_write(
        {
            "status": "running",
            "broker_connected": True,
            "broker_error": None,
            "account": {
                "selected": "DU123",
                "paper": True,
                "net_liquidation": 100000,
                "available_funds": 50000,
                "gross_position_value": 0,
            },
            "daily_pnl": 0,
            "positions": [],
            "open_orders": [],
            "fills": [],
            "intents": [],
        }
    )
    ib = _OrderIB()
    contract = SimpleNamespace(symbol="XYZ", conId=123, secType="STK", currency="USD")
    monkeypatch.setattr(pa, "reconcile_broker", pa.state_read)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "ib_get_contract", lambda *_args: contract)
    monkeypatch.setattr(pa, "Order", _FakeOrder)

    result = pa.submit_signal(
        {
            "ticker": "XYZ",
            "direction": "LONG",
            "trigger_bar_date": "2026-08-08",
            "entry": 10,
            "stop": 9.5,
            "tp1": 11,
            "tp2": 12,
        }
    )

    assert result["success"] is True
    assert result["submitted"] is True
    assert len(ib.placed) == 6
    parents = [trade.order for trade in ib.placed if trade.order.orderRef.endswith(("-P1", "-P2"))]
    stops = [trade.order for trade in ib.placed if "-S" in trade.order.orderRef]
    targets = [trade.order for trade in ib.placed if "-T" in trade.order.orderRef]
    assert len(parents) == len(stops) == len(targets) == 2
    assert sum(order.totalQuantity for order in parents) == result["sizing"]["quantity"]
    for index in range(2):
        assert stops[index].parentId == targets[index].parentId == parents[index].orderId
        assert stops[index].ocaGroup == targets[index].ocaGroup
        assert stops[index].ocaType == targets[index].ocaType == 1
        assert targets[index].transmit is True

    state = pa.state_read()
    assert state["intents"][0]["status"] == "WORKING"
    assert not state["intents"][0].get("filled_at")
    assert state["cooldown_tickers"] == {}
