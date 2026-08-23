from datetime import datetime, timezone
from threading import Event, Lock, Thread
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


def _fill(order_id, side, price, *, ticker="XYZ", account="DU123", client_id=7):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=ticker),
        execution=SimpleNamespace(
            execId=f"EXEC-{order_id}",
            orderId=order_id,
            permId=1000 + order_id,
            clientId=client_id,
            acctNumber=account,
            side=side,
            shares=10,
            price=price,
        ),
        time=datetime.now(timezone.utc),
    )


def test_fill_serialization_includes_broker_client_id():
    serialized = pa._serialize_fill(_fill(10, "BOT", 10, client_id=23))

    assert serialized["client_id"] == 23


class _ReconcileIB:
    def __init__(self, *, fills=None, positions=None, open_trades=None, values=None):
        self._fills = fills or []
        self._positions = positions or []
        self._open_trades = open_trades or []
        self._values = values or [
            _account_row("Currency", "USD", ""),
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
        self.permId = int(getattr(self, "permId", 0) or 0)
        self.clientId = int(getattr(self, "clientId", 0) or 0)
        self.parentId = int(getattr(self, "parentId", 0) or 0)
        self.outsideRth = getattr(self, "outsideRth", False) is True


class _FakeBrokerEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def emit(self, *args):
        for handler in list(self.handlers):
            handler(*args)


class _CausalFreshRiskIB(_ReconcileIB):
    """IB double with stale caches and request-window risk evidence."""

    def __init__(self, *, failure_mode="ok"):
        super().__init__(
            values=[
                _account_row("Currency", "USD", ""),
                _account_row("NetLiquidation", 999000),
                _account_row("AvailableFunds", 888000),
                _account_row("BuyingPower", 777000),
                _account_row("GrossPositionValue", 666000),
                _account_row("DailyPnL", 555),
            ]
        )
        self.failure_mode = failure_mode
        self.errorEvent = _FakeBrokerEvent()
        self.accountValueEvent = _FakeBrokerEvent()
        self.pnlEvent = _FakeBrokerEvent()
        self.wrapper = SimpleNamespace(
            pnlKey2ReqId={},
            reqId2PnL={},
            _futures={},
            startReq=self._start_req,
            accountUpdateMulti=self._account_update_multi,
        )
        self.client = SimpleNamespace(
            getReqId=self._get_req_id,
            reqExecutions=self._req_executions,
            reqAccountUpdatesMulti=self._req_account_updates_multi,
            cancelAccountUpdatesMulti=self._cancel_account_updates_multi,
            reqPnL=self._req_pnl,
            cancelPnL=self._cancel_pnl,
        )
        self.requests = []
        self.cache_reads = []
        self.disconnected = False
        self._pnl_row = None
        self._next_request_id = 40
        self._account_requests = {}
        self._request_kinds = {}
        self._account_request_modes = {}
        self._pnl_request_id = None
        self._fresh_daily_pnl = -321.0
        self._preexisting_pnl_row = None

    def accountValues(self, _account=None):
        self.cache_reads.append("accountValues")
        return super().accountValues(_account)

    def accountSummary(self, _account=None):
        self.cache_reads.append("accountSummary")
        return list(self._values)

    def pnl(self, *_args):
        self.cache_reads.append("pnl")
        return [SimpleNamespace(account="DU123", modelCode="", dailyPnL=444)]

    def reqAllOpenOrdersAsync(self):
        self.requests.append("orders")
        return ("orders", list(self._open_trades))

    def reqPositionsAsync(self):
        self.requests.append("positions")
        return ("positions", list(self._positions))

    def reqExecutionsAsync(self):
        self.requests.append("fills")
        return ("fills", list(self._fills))

    def _get_req_id(self):
        self._next_request_id += 1
        return self._next_request_id

    def _start_req(self, request_id):
        future = ("request", request_id)
        self.wrapper._futures[request_id] = future
        return future

    def _req_executions(self, request_id, _execution_filter):
        self.requests.append("fills")
        self._request_kinds[request_id] = "fills"

    def _account_update_multi(
        self, _request_id, _account, _model_code, _tag, _value, _currency
    ):
        return None

    def _req_account_updates_multi(
        self, request_id, account, model_code, ledger_and_nlv
    ):
        assert model_code == ""
        self.requests.append("account_values")
        self._account_requests[request_id] = account
        self._request_kinds[request_id] = "account_values"
        self._account_request_modes[request_id] = ledger_and_nlv

    def _cancel_account_updates_multi(self, request_id):
        self.requests.append("account_values_cancel")
        self._account_requests.pop(request_id, None)

    def reqPnL(self, account, modelCode=""):
        self.requests.append("pnl_subscribe")
        self.wrapper.pnlKey2ReqId[(account, modelCode)] = self._get_req_id()
        self._pnl_row = SimpleNamespace(
            account=account,
            modelCode=modelCode,
            dailyPnL=-321.0,
            unrealizedPnL=0.0,
            realizedPnL=0.0,
        )
        return self._pnl_row

    def cancelPnL(self, account, modelCode=""):
        self.requests.append("pnl_cancel")
        self.wrapper.pnlKey2ReqId.pop((account, modelCode), None)

    def _req_pnl(self, request_id, account, model_code=""):
        self.requests.append("pnl_subscribe")
        self._pnl_request_id = request_id
        row = self.wrapper.reqId2PnL[request_id]
        row.account = account
        row.modelCode = model_code
        row.dailyPnL = self._fresh_daily_pnl
        row.unrealizedPnL = 0.0
        row.realizedPnL = 0.0
        self._pnl_row = row

    def _cancel_pnl(self, request_id):
        self.requests.append("pnl_cancel")
        assert request_id == self._pnl_request_id

    def _fresh_account_rows(self):
        rows = [
            _account_row("AccountReady", "true", ""),
            _account_row("Currency", "USD", ""),
            _account_row("NetLiquidation", 100000),
            _account_row("AvailableFunds", 50000),
            _account_row("BuyingPower", 100000),
            _account_row("GrossPositionValue", 0),
        ]
        if self.failure_mode == "account_foreign":
            rows[3] = _account_row(
                "AvailableFunds", 50000, account="DU999"
            )
        elif self.failure_mode == "account_mixed_currency":
            rows[3] = _account_row("AvailableFunds", 50000, "CAD")
        elif self.failure_mode == "account_ready_false":
            rows[0] = _account_row("AccountReady", "false", "")
        elif self.failure_mode == "account_missing":
            rows = [row for row in rows if row.tag != "AvailableFunds"]
        elif self.failure_mode == "account_duplicate":
            rows.append(_account_row("AvailableFunds", 50000))
        elif self.failure_mode == "account_nan":
            rows[3] = _account_row("AvailableFunds", "nan")
        elif self.failure_mode == "account_unset":
            rows[3] = _account_row(
                "AvailableFunds", "1.7976931348623157e+308"
            )
        return rows

    def run(self, request, *, timeout=None):
        assert timeout == pa._BROKER_ORDER_REFRESH_TIMEOUT_SECONDS
        if isinstance(request, _FakeBrokerEvent):
            self.requests.append("pnl_event")
            if self.failure_mode == "pnl_timeout":
                raise TimeoutError("fresh pnl timed out")
            if self.failure_mode == "pnl_error":
                self.errorEvent.emit(-1, 321, "fresh pnl failed", None)
            if self.failure_mode == "pnl_disconnect":
                self.disconnected = True
            if self.failure_mode == "pnl_none":
                return None
            if self.failure_mode == "pnl_preexisting_queued":
                assert self._preexisting_pnl_row is not None
                self.pnlEvent.emit(self._preexisting_pnl_row)
            row = self._pnl_row
            if row is None:
                row = self._preexisting_pnl_row
            if self.failure_mode == "pnl_foreign":
                row.account = "DU999"
            elif self.failure_mode == "pnl_nan":
                row.dailyPnL = float("nan")
            elif self.failure_mode == "pnl_unset":
                row.dailyPnL = 1.7976931348623157e308
            if self.failure_mode != "pnl_missing_timestamp":
                self.pnlEvent.emit(row)
            return row

        key, value = request
        if key == "request":
            key = self._request_kinds[value]
        if key == "fills":
            request_id = value if isinstance(value, int) else 41
            if self.failure_mode == "fills_foreign_request_error":
                self.errorEvent.emit(
                    request_id + 1000, 321, "foreign request failed", None
                )
            if self.failure_mode == "fills_matching_request_error":
                self.errorEvent.emit(request_id, 321, "fresh fills failed", None)
            return list(self._fills)
        if key == "account_values":
            if self.failure_mode == "account_timeout":
                raise TimeoutError("fresh account values timed out")
            if self.failure_mode == "account_error":
                self.errorEvent.emit(-1, 321, "fresh account values failed", None)
            if self.failure_mode == "account_foreign_request_error":
                self.errorEvent.emit(value + 1000, 321, "foreign request failed", None)
            if self.failure_mode == "account_matching_request_error":
                self.errorEvent.emit(value, 321, "fresh account values failed", None)
            if self.failure_mode == "account_2110":
                self.errorEvent.emit(
                    -1,
                    2110,
                    "Connectivity between TWS and server is broken",
                    None,
                )
            if self.failure_mode == "ok":
                self.errorEvent.emit(
                    -1, 2104, "Market data farm connection is OK", None
                )
            if self.failure_mode == "account_disconnect":
                self.disconnected = True
            if self.failure_mode != "account_none":
                rows = self._fresh_account_rows()
                if self._account_request_modes[value] is True:
                    rows = [
                        row
                        for row in rows
                        if row.tag in {"AccountReady", "Currency", "NetLiquidation"}
                    ]
                for row in rows:
                    self.wrapper.accountUpdateMulti(
                        value,
                        row.account,
                        "",
                        row.tag,
                        row.value,
                        row.currency,
                    )
            # The request future resolves at accountUpdateMultiEnd; the
            # causally captured callback rows, not this ack, are the data.
            return None
        return value


class _OrderIB:
    def __init__(self):
        self.RequestTimeout = 0
        self.RaiseRequestErrors = False
        self.errorEvent = _FakeBrokerEvent()
        self.next_order_id = 1
        self.client = SimpleNamespace(getReqId=self._get_req_id)
        self.placed = []
        self.cancelled = []

    def _get_req_id(self):
        order_id = self.next_order_id
        self.next_order_id += 1
        return order_id

    def qualifyContracts(self, contract):
        return [contract]

    def reqContractDetails(self, _contract):
        return [SimpleNamespace(minTick=0.01)]

    def placeOrder(self, contract, order):
        if not order.orderId:
            order.orderId = self.next_order_id
            self.next_order_id += 1
        if not order.permId:
            order.permId = 10_000 + order.orderId
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

    def openTrades(self):
        return list(self.placed)

    def reqAllOpenOrders(self):
        raise AssertionError("blocking open-order refresh must not be used")

    def reqAllOpenOrdersAsync(self):
        return list(self.placed)

    def run(self, awaitable, *, timeout=None):
        assert timeout == pytest.approx(2.0)
        return awaitable

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def sleep(self, _seconds):
        return None


class _FastFillIB(_OrderIB):
    def __init__(self):
        super().__init__()
        self.filled_parent_ids = set()

    def placeOrder(self, contract, order):
        trade = super().placeOrder(contract, order)
        if getattr(order, "transmit", False) is True:
            parent = next(
                row for row in self.placed if row.order.orderId == order.parentId
            )
            parent.orderStatus.status = "Filled"
            parent.orderStatus.filled = parent.order.totalQuantity
            parent.orderStatus.remaining = 0
            self.filled_parent_ids.add(parent.order.orderId)
        return trade

    def openTrades(self):
        return [
            row for row in self.placed
            if row.order.orderId not in self.filled_parent_ids
        ]

    def positions(self, _account=None):
        quantity = sum(
            row.order.totalQuantity for row in self.placed
            if row.order.orderId in self.filled_parent_ids
        )
        if not quantity:
            return []
        return [SimpleNamespace(
            account="DU123", contract=self.placed[0].contract,
            position=quantity, avgCost=10.0,
        )]

    def fills(self):
        return []


class _CancelAckIB(_OrderIB):
    def __init__(
        self,
        *,
        acknowledge_parents=True,
        acknowledge_children=True,
        fill_during_first_cancel=False,
    ):
        super().__init__()
        self.acknowledge_parents = acknowledge_parents
        self.acknowledge_children = acknowledge_children
        self.fill_during_first_cancel = fill_during_first_cancel
        self.fill_active = False
        self.fill_scheduled = False
        self.pending_fill = None
        self.filled_parent = None
        self.events = []

    def cancelOrder(self, order):
        self.events.append(("cancel", order.orderRef))
        super().cancelOrder(order)
        if not order.orderRef.endswith(("-P1", "-P2")):
            if self.acknowledge_children:
                trade = next(row for row in self.placed if row.order is order)
                trade.orderStatus.status = "Cancelled"
                trade.orderStatus.filled = 0
                trade.orderStatus.remaining = trade.order.totalQuantity
            return
        trade = next(row for row in self.placed if row.order is order)
        if self.fill_during_first_cancel and not self.fill_scheduled:
            self.fill_scheduled = True
            self.pending_fill = trade
            # The cancel acknowledgement arrives before the locally cached
            # execution callback.  A causal server request must still reveal
            # the fill before any protective child is removed.
            trade.orderStatus.status = "Cancelled"
            trade.orderStatus.filled = 0
            trade.orderStatus.remaining = trade.order.totalQuantity
        elif self.acknowledge_parents:
            trade.orderStatus.status = "Cancelled"
            trade.orderStatus.filled = 0
            trade.orderStatus.remaining = trade.order.totalQuantity

    def sleep(self, seconds):
        super().sleep(seconds)

    def _fresh_fill_trade(self):
        return self.pending_fill or self.filled_parent

    def reqPositions(self):
        raise AssertionError("blocking positions recovery must not be used")

    def reqPositionsAsync(self):
        self.events.append(("req_positions", len(self.cancelled)))
        trade = self._fresh_fill_trade()
        if trade is None:
            return []
        return [
            SimpleNamespace(
                account="DU123",
                contract=trade.contract,
                position=trade.order.totalQuantity,
                avgCost=10.0,
            )
        ]

    def reqExecutions(self):
        raise AssertionError("blocking executions recovery must not be used")

    def reqExecutionsAsync(self):
        self.events.append(("req_executions", len(self.cancelled)))
        trade = self._fresh_fill_trade()
        if trade is None:
            return []
        return [
            SimpleNamespace(
                contract=trade.contract,
                execution=SimpleNamespace(
                    orderId=trade.order.orderId,
                    acctNumber="DU123",
                    shares=trade.order.totalQuantity,
                ),
            )
        ]

    def positions(self, _account=None):
        self.events.append(("positions", len(self.cancelled)))
        if not self.fill_active:
            return []
        return [
            SimpleNamespace(
                account="DU123",
                contract=self.filled_parent.contract,
                position=self.filled_parent.order.totalQuantity,
                avgCost=10.0,
            )
        ]

    def fills(self):
        self.events.append(("fills", len(self.cancelled)))
        if not self.fill_active:
            return []
        return [
            SimpleNamespace(
                contract=self.filled_parent.contract,
                execution=SimpleNamespace(
                    orderId=self.filled_parent.order.orderId,
                    acctNumber="DU123",
                    shares=self.filled_parent.order.totalQuantity,
                ),
            )
        ]


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


def test_risk_policy_defaults_match_the_durable_admission_contract(isolated_autotrader_store):
    config = pa.config_load()

    assert config["max_total_risk_pct"] == pytest.approx(0.75)
    assert config["max_direction_risk_pct"] == pytest.approx(0.75)
    assert config["max_verified_group_risk_pct"] == pytest.approx(0.50)
    assert config["max_consecutive_losses"] == 3


def test_order_sizing_uses_worst_authorized_stop_limit_for_cash_and_notional():
    config = {
        **pa.DEFAULT_CONFIG,
        "risk_per_trade_pct": 1.0,
        "max_notional_per_trade": 100_000.0,
        "max_shares": 5_000,
        "min_available_funds": 500.0,
    }
    state = {
        "account": {
            "net_liquidation": 100_000.0,
            "available_funds": 1_000.0,
        }
    }

    sizing = pa.calculate_order_quantity(
        10.0,
        9.0,
        state,
        config,
        max_fill_price=15.0,
    )

    assert sizing["quantity"] == 33
    assert sizing["max_fill_price"] == pytest.approx(15.0)
    assert sizing["risk_per_share"] == pytest.approx(6.0)
    assert sizing["notional"] == pytest.approx(495.0)

    short_sizing = pa.calculate_order_quantity(
        10.0,
        11.0,
        state,
        config,
        max_fill_price=9.0,
    )

    assert short_sizing["quantity"] == 50
    assert short_sizing["max_fill_price"] == pytest.approx(9.0)
    assert short_sizing["risk_per_share"] == pytest.approx(2.0)
    assert short_sizing["notional"] == pytest.approx(500.0)


def test_set_execution_armed_advances_and_disarm_fences_durable_generation(
    monkeypatch, isolated_autotrader_store
):
    state = _armed_submission_state()
    reconcile_calls = []

    def reconcile_for_arm(**kwargs):
        reconcile_calls.append(dict(kwargs))
        return state

    monkeypatch.setattr(pa, "reconcile_broker", reconcile_for_arm)

    armed = pa.set_execution_armed(True)
    armed_state = pa._risk_store().execution_state()
    disarmed = pa.set_execution_armed(False)
    disarmed_state = pa._risk_store().execution_state()

    assert armed["ok"] is True
    assert reconcile_calls == [{"require_fresh": True}]
    assert armed_state == {"armed": True, "generation": 1, "reason": None}
    assert disarmed["ok"] is True
    assert disarmed_state == {"armed": False, "generation": 2, "reason": None}


def test_arming_recovers_orphan_only_via_fresh_causal_broker_reconciliation(
    monkeypatch, isolated_autotrader_store
):
    class _OrphanRecoveryStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.execution_armed = False
            self.execution_generation = 7
            self.recovered = False

        def wait_for_execution_writes(self, **_kwargs):
            if self.recovered:
                return {"drained": True, "active_count": 0, "reason": None}
            return {
                "drained": False,
                "active_count": 0,
                "orphaned_count": 1,
                "reason": "execution_writes_orphaned",
            }

        def transition_execution_state(self, armed, **kwargs):
            assert kwargs.get("require_drained") is True
            assert self.recovered is True
            return super().transition_execution_state(armed)

    store = _OrphanRecoveryStore()
    calls = []

    def reconcile(*, require_fresh=False, expected_recovery_generation=None):
        calls.append((require_fresh, expected_recovery_generation))
        assert require_fresh is True
        assert expected_recovery_generation == 7
        store.recovered = True
        return _armed_submission_state()

    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)
    monkeypatch.setattr(pa, "reconcile_broker", reconcile)

    result = pa.set_execution_armed(True)

    assert result["ok"] is True
    assert calls == [(True, 7)]
    assert store.execution_armed is True
    assert store.execution_generation == 8


def test_fresh_crash_recovery_uses_bounded_server_snapshots_and_causal_resolver(
    monkeypatch, isolated_autotrader_store
):
    class _FreshRecoveryIB(_CausalFreshRiskIB):
        def positions(self, *_args):
            raise AssertionError("cached positions must not authorize recovery")

        def openTrades(self):
            raise AssertionError("cached orders must not authorize recovery")

        def fills(self):
            raise AssertionError("cached fills must not authorize recovery")

    class _RecoveryEvidenceStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.execution_armed = False
            self.execution_generation = 9
            self.recovery_calls = []

        def reconcile_orphaned_execution_writes(self, expected, **kwargs):
            self.recovery_calls.append((expected, dict(kwargs)))
            return {
                "accepted": True,
                "resolved_count": 1,
                "generation": expected,
                "reason": None,
            }

    ib = _FreshRecoveryIB()
    store = _RecoveryEvidenceStore()
    pa.config_save({"selected_account": "DU123"})
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker(
        require_fresh=True,
        expected_recovery_generation=9,
    )

    assert ib.requests == [
        "orders",
        "positions",
        "fills",
        "account_values",
        "account_values_cancel",
        "pnl_subscribe",
        "pnl_event",
        "pnl_cancel",
    ]
    assert ib.cache_reads == []
    assert state["risk_evidence_unreliable"] is False
    assert len(store.recovery_calls) == 1
    expected, evidence = store.recovery_calls[0]
    assert expected == 9
    assert evidence["orders_snapshot_complete"] is True
    assert evidence["positions_snapshot_complete"] is True
    assert evidence["fills_snapshot_complete"] is True
    assert evidence["risk_evidence_reliable"] is True
    assert evidence["reconciliation_started_at"] <= evidence["observed_at"]


def _run_causal_fresh_reconcile(monkeypatch, ib):
    store = _RecordingRiskStore()
    pa.config_save({"selected_account": "DU123"})
    monkeypatch.setattr(pa, "ib_is_connected", lambda: not ib.disconnected)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)
    return pa.reconcile_broker(require_fresh=True)


def test_fresh_reconcile_uses_only_causal_account_and_pnl_events(
    monkeypatch, isolated_autotrader_store
):
    """Removing the event-window path must expose stale permissive caches."""
    ib = _CausalFreshRiskIB()

    state = _run_causal_fresh_reconcile(monkeypatch, ib)

    assert ib.cache_reads == []
    assert ib.requests == [
        "orders",
        "positions",
        "fills",
        "account_values",
        "account_values_cancel",
        "pnl_subscribe",
        "pnl_event",
        "pnl_cancel",
    ]
    assert state["account_snapshot_complete"] is True
    assert state["pnl_snapshot_complete"] is True
    assert state["account"]["net_liquidation"] == pytest.approx(100000)
    assert state["account"]["available_funds"] == pytest.approx(50000)
    assert state["account"]["gross_position_value"] == pytest.approx(0)
    assert state["daily_pnl"] == pytest.approx(-321)
    assert state["account"]["risk_values_observed_at"]
    assert state["daily_pnl_observed_at"]
    assert state["risk_evidence_unreliable"] is False
    assert pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )["allowed"] is True
    assert ib.errorEvent.handlers == []
    assert ib.pnlEvent.handlers == []
    assert ib.wrapper.pnlKey2ReqId == {}


def test_fresh_account_request_explicitly_disables_lightweight_ledger_mode(
    monkeypatch, isolated_autotrader_store
):
    """IBKR True is lightweight and must not masquerade as a full snapshot."""
    ib = _CausalFreshRiskIB()

    state = _run_causal_fresh_reconcile(monkeypatch, ib)

    assert list(ib._account_request_modes.values()) == [False]
    assert state["account_snapshot_complete"] is True
    assert pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )["allowed"] is True


def test_fresh_pnl_uses_new_request_identity_and_ignores_preexisting_event(
    monkeypatch, isolated_autotrader_store
):
    """A queued event from an older subscription cannot authorize this window."""
    ib = _CausalFreshRiskIB(failure_mode="pnl_preexisting_queued")
    old_row = SimpleNamespace(
        account="DU123",
        modelCode="",
        dailyPnL=-100.0,
        unrealizedPnL=0.0,
        realizedPnL=0.0,
    )
    ib._preexisting_pnl_row = old_row
    ib._fresh_daily_pnl = -2000.0
    ib.wrapper.pnlKey2ReqId[("DU123", "")] = 900
    ib.wrapper.reqId2PnL[900] = old_row

    state = _run_causal_fresh_reconcile(monkeypatch, ib)
    gate = pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )

    assert "pnl_subscribe" in ib.requests
    assert state["pnl_snapshot_complete"] is True
    assert state["daily_pnl"] == pytest.approx(-2000.0)
    assert gate["allowed"] is False
    assert ib.wrapper.pnlKey2ReqId == {("DU123", ""): 900}
    assert ib.wrapper.reqId2PnL == {900: old_row}


@pytest.mark.parametrize(
    "failure_mode",
    ["account_foreign_request_error", "fills_foreign_request_error"],
)
def test_fresh_snapshot_ignores_error_for_foreign_positive_request_id(
    monkeypatch, isolated_autotrader_store, failure_mode
):
    ib = _CausalFreshRiskIB(failure_mode=failure_mode)

    state = _run_causal_fresh_reconcile(monkeypatch, ib)

    assert state["risk_evidence_unreliable"] is False
    assert pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )["allowed"] is True


@pytest.mark.parametrize(
    "failure_mode",
    ["account_matching_request_error", "fills_matching_request_error"],
)
def test_fresh_snapshot_blocks_error_for_matching_positive_request_id(
    monkeypatch, isolated_autotrader_store, failure_mode
):
    ib = _CausalFreshRiskIB(failure_mode=failure_mode)

    state = _run_causal_fresh_reconcile(monkeypatch, ib)

    assert state["risk_evidence_unreliable"] is True
    assert pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )["allowed"] is False


@pytest.mark.parametrize(
    "failure_mode",
    [
        "account_timeout",
        "account_none",
        "account_error",
        "account_2110",
        "account_disconnect",
        "account_foreign",
        "account_mixed_currency",
        "account_ready_false",
        "account_missing",
        "account_duplicate",
        "account_nan",
        "account_unset",
        "pnl_timeout",
        "pnl_none",
        "pnl_error",
        "pnl_disconnect",
        "pnl_missing_timestamp",
        "pnl_foreign",
        "pnl_nan",
        "pnl_unset",
    ],
)
def test_fresh_account_or_pnl_evidence_failure_is_fail_closed_without_cache(
    monkeypatch, isolated_autotrader_store, failure_mode
):
    """Every incomplete or unbound fresh-risk observation must block."""
    ib = _CausalFreshRiskIB(failure_mode=failure_mode)

    state = _run_causal_fresh_reconcile(monkeypatch, ib)
    gate = pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )

    assert "account_values" in ib.requests
    assert "account_values_cancel" in ib.requests
    assert ib.cache_reads == []
    assert gate["allowed"] is False
    assert (
        state.get("account_snapshot_complete") is not True
        or state.get("pnl_snapshot_complete") is not True
        or state["account"].get("risk_value_currency_evidence") != "VERIFIED"
    )
    assert ib.errorEvent.handlers == []
    assert ib.pnlEvent.handlers == []
    assert ib.wrapper.pnlKey2ReqId == {}


def test_parallel_fresh_risk_event_windows_are_process_serialized(monkeypatch):
    """Removing the shared IB lock lets request-global events cross windows."""
    first_entered = Event()
    release_first = Event()
    second_attempted = Event()
    activity_lock = Lock()
    activity = {"current": 0, "maximum": 0}

    class _BlockingFreshIB(_CausalFreshRiskIB):
        def __init__(self, label):
            super().__init__()
            self.label = label

        def run(self, request, *, timeout=None):
            with activity_lock:
                activity["current"] += 1
                activity["maximum"] = max(
                    activity["maximum"], activity["current"]
                )
            try:
                if (
                    self.label == "first"
                    and isinstance(request, tuple)
                    and request[0] == "orders"
                ):
                    first_entered.set()
                    assert release_first.wait(timeout=2)
                return super().run(request, timeout=timeout)
            finally:
                with activity_lock:
                    activity["current"] -= 1

    first_ib = _BlockingFreshIB("first")
    second_ib = _BlockingFreshIB("second")
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    outcomes = {}

    first_thread = Thread(
        target=lambda: outcomes.setdefault(
            "first", pa._bounded_fresh_broker_snapshot(first_ib, "DU123")
        )
    )

    def run_second():
        second_attempted.set()
        outcomes["second"] = pa._bounded_fresh_broker_snapshot(
            second_ib, "DU123"
        )

    second_thread = Thread(target=run_second)
    first_thread.start()
    assert first_entered.wait(timeout=2)
    second_thread.start()
    assert second_attempted.wait(timeout=2)
    assert second_ib.requests == []
    release_first.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert set(outcomes) == {"first", "second"}
    assert activity["maximum"] == 1
    assert first_ib.cache_reads == []
    assert second_ib.cache_reads == []


@pytest.mark.parametrize(
    "failure_mode",
    [
        "timeout",
        "none",
        "request_error",
        "server_disconnect",
        "disconnect",
        "missing_api",
    ],
)
def test_fresh_crash_recovery_failure_never_releases_orphaned_claim(
    monkeypatch, isolated_autotrader_store, failure_mode
):
    class _FailingFreshIB(_ReconcileIB):
        def __init__(self):
            super().__init__()
            self.errorEvent = _FakeBrokerEvent()
            self.requests = []
            if failure_mode == "missing_api":
                self.reqPositionsAsync = None

        def reqAllOpenOrdersAsync(self):
            self.requests.append("orders")
            return ("orders", [])

        def reqPositionsAsync(self):
            self.requests.append("positions")
            return ("positions", [])

        def reqExecutionsAsync(self):
            self.requests.append("fills")
            return ("fills", [])

        def run(self, request, *, timeout=None):
            assert timeout == pa._BROKER_ORDER_REFRESH_TIMEOUT_SECONDS
            if failure_mode == "timeout":
                raise TimeoutError("fresh snapshot timed out")
            if failure_mode == "none":
                return None
            if failure_mode == "request_error":
                self.errorEvent.emit(-1, 321, "fresh snapshot failed", None)
            if failure_mode == "server_disconnect":
                self.errorEvent.emit(
                    -1,
                    2110,
                    "Connectivity between TWS and server is broken",
                    None,
                )
            return request[1]

    class _RejectingRecoveryStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.execution_armed = False
            self.execution_generation = 11
            self.recovery_calls = []

        def reconcile_orphaned_execution_writes(self, expected, **kwargs):
            self.recovery_calls.append((expected, dict(kwargs)))
            return {
                "accepted": False,
                "resolved_count": 0,
                "generation": expected,
                "reason": "execution_recovery_evidence_incomplete",
            }

    ib = _FailingFreshIB()
    store = _RejectingRecoveryStore()
    pa.config_save({"selected_account": "DU123"})
    monkeypatch.setattr(
        pa,
        "ib_is_connected",
        lambda: not (
            failure_mode == "disconnect" and len(ib.requests) >= 3
        ),
    )
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker(
        require_fresh=True,
        expected_recovery_generation=11,
    )

    assert state["risk_evidence_unreliable"] is True
    assert state["execution_write_recovery"]["accepted"] is False
    assert len(store.recovery_calls) == 1
    _, evidence = store.recovery_calls[0]
    assert evidence["risk_evidence_reliable"] is False
    assert not (
        evidence["orders_snapshot_complete"]
        and evidence["positions_snapshot_complete"]
        and evidence["fills_snapshot_complete"]
    )


def test_disarm_store_failure_still_writes_fail_closed_config(
    monkeypatch, isolated_autotrader_store
):
    class _FailingDisarmStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        def transition_execution_state(self, armed, **_kwargs):
            if armed is False and not self.failed:
                self.failed = True
                raise OSError("sqlite temporarily unavailable")
            return super().transition_execution_state(armed)

    store = _FailingDisarmStore()
    pa._config_save_internal(_armed_submission_config(), allow_execution_state=True)
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    result = pa.set_execution_armed(False)

    config = pa.config_load()
    assert result["ok"] is False
    assert store.execution_armed is False
    assert config["kill_switch"] is True
    assert config["execution_enabled"] is False
    assert config["mode"] == "paper_review"


def test_arm_verification_failure_rolls_back_store_and_config(
    monkeypatch, isolated_autotrader_store
):
    class _VerificationFailureStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.execution_armed = False
            self.read_count = 0

        def execution_state(self):
            self.read_count += 1
            if self.read_count == 2:
                raise OSError("verification read failed")
            return super().execution_state()

        def transition_execution_state(self, armed, **_kwargs):
            return super().transition_execution_state(armed)

    store = _VerificationFailureStore()
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)
    monkeypatch.setattr(
        pa, "reconcile_broker", lambda **_kwargs: _armed_submission_state()
    )

    result = pa.set_execution_armed(True)

    config = pa.config_load()
    assert result["ok"] is False
    assert store.execution_armed is False
    assert config["kill_switch"] is True
    assert config["execution_enabled"] is False
    assert config["mode"] == "paper_review"


def test_arm_audit_failure_never_leaves_unacknowledged_execution_enabled(
    monkeypatch, isolated_autotrader_store
):
    class _AuditArmStore(_RecordingRiskStore):
        def transition_execution_state(self, armed, **_kwargs):
            return super().transition_execution_state(armed)

    store = _AuditArmStore()
    store.execution_armed = False
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)
    monkeypatch.setattr(
        pa, "reconcile_broker", lambda **_kwargs: _armed_submission_state()
    )

    def fail_audit(*_args, **_kwargs):
        raise OSError("audit sink unavailable")

    monkeypatch.setattr(pa, "audit_log", fail_audit)

    result = pa.set_execution_armed(True)

    config = pa.config_load()
    assert result["ok"] is False
    assert store.execution_armed is False
    assert config["kill_switch"] is True
    assert config["execution_enabled"] is False
    assert config["mode"] == "paper_review"


def test_arm_verification_window_cannot_submit_before_final_durable_arm(
    monkeypatch, isolated_autotrader_store
):
    nested = {}

    class _SubmitDuringVerificationStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.read_count = 0

        def execution_state(self):
            self.read_count += 1
            if self.read_count == 2:
                nested["result"] = pa.submit_signal(_submission_signal())
                raise OSError("verification read failed after nested submit")
            return super().execution_state()

        def transition_execution_state(self, armed, **_kwargs):
            return super().transition_execution_state(armed)

    ib = _OrderIB()
    store = _SubmitDuringVerificationStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    store.execution_armed = False
    pa._config_save_internal(
        {"mode": "paper_review", "kill_switch": True, "execution_enabled": False},
        allow_execution_state=True,
    )

    result = pa.set_execution_armed(True)

    assert result["ok"] is False
    assert nested["result"]["submitted"] is False
    assert ib.placed == []
    assert store.execution_armed is False
    assert pa.config_load()["execution_enabled"] is False


def test_disarm_audit_failure_returns_honest_negative_result(
    monkeypatch, isolated_autotrader_store
):
    store = _RecordingRiskStore()
    pa._config_save_internal(_armed_submission_config(), allow_execution_state=True)
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    def fail_disarm_audit(message, *_args, **_kwargs):
        if message == "Paper execution disarmed":
            raise OSError("audit sink unavailable")

    monkeypatch.setattr(pa, "audit_log", fail_disarm_audit)

    result = pa.set_execution_armed(False)

    assert result["ok"] is False
    assert store.execution_armed is False
    assert result["config"]["execution_enabled"] is False
    assert any("audit sink unavailable" in error for error in result["errors"])


def test_account_values_prefer_base_currency_for_non_gate_metrics():
    ib = _ReconcileIB(
        values=[
            _account_row("Currency", "USD", ""),
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("GrossPositionValue", 0, "BASE"),
            _account_row("DailyPnL", 0, "BASE"),
            _account_row("BuyingPower", 100000, "BASE"),
            _account_row("BuyingPower", 999, "USD"),
        ]
    )

    values = pa._account_values(ib, "DU123")

    assert values["NetLiquidation"] == pytest.approx(100000)
    assert values["AvailableFunds"] == pytest.approx(50000)
    assert values["BuyingPower"] == pytest.approx(100000)


@pytest.mark.parametrize(
    ("currency_rows", "expected_reason"),
    [
        ([], "Kontobasiswaehrung nicht eindeutig verifiziert"),
        (
            [_account_row("Currency", "CAD", "")],
            "Kontobasiswaehrung ist nicht USD",
        ),
        (
            [
                _account_row("Currency", "USD", ""),
                _account_row("Currency", "CAD", ""),
            ],
            "Kontobasiswaehrung nicht eindeutig verifiziert",
        ),
    ],
)
def test_account_gate_rejects_missing_ambiguous_or_non_usd_base_currency(
    currency_rows, expected_reason
):
    rows = [
        _account_row("NetLiquidation", 100000),
        _account_row("AvailableFunds", 50000),
        *currency_rows,
    ]
    ib = _ReconcileIB(values=rows)
    _values, currency_evidence = pa._account_value_snapshot(ib, "DU123")
    state = _armed_submission_state()
    state["account"].update(currency_evidence)

    gate = pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )

    assert gate["allowed"] is False
    assert expected_reason in gate["reasons"]


def test_account_gate_accepts_unique_current_usd_base_currency_evidence():
    rows = [
        _account_row("Currency", "USD", ""),
        _account_row("NetLiquidation", 100000),
        _account_row("AvailableFunds", 50000),
        _account_row("GrossPositionValue", 0),
        _account_row("DailyPnL", 0),
    ]
    values, currency_evidence = pa._account_value_snapshot(
        _ReconcileIB(values=rows), "DU123"
    )
    state = _armed_submission_state()
    state["account"].update(currency_evidence)

    gate = pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )

    assert values["NetLiquidation"] == pytest.approx(100000)
    assert currency_evidence == {
        "base_currency": "USD",
        "base_currency_evidence": "VERIFIED",
        "risk_value_currency_evidence": "VERIFIED",
        "risk_value_currency_errors": [],
    }
    assert gate["allowed"] is True


@pytest.mark.parametrize(
    "money_rows",
    [
        [
            _account_row("NetLiquidation", 100000, "EUR"),
            _account_row("AvailableFunds", 50000, "EUR"),
            _account_row("GrossPositionValue", 0, "BASE"),
            _account_row("DailyPnL", 0, "BASE"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("NetLiquidation", 100000, "USD"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("GrossPositionValue", 0, "BASE"),
            _account_row("DailyPnL", 0, "BASE"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("NetLiquidation", 99999, "BASE"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("GrossPositionValue", 0, "BASE"),
            _account_row("DailyPnL", 0, "BASE"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("AvailableFunds", 50000, "CAD"),
            _account_row("GrossPositionValue", 0, "BASE"),
            _account_row("DailyPnL", 0, "BASE"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("GrossPositionValue", 12000, "EUR"),
            _account_row("DailyPnL", 0, "BASE"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("GrossPositionValue", 12000, "BASE"),
            _account_row("DailyPnL", -400, "CAD"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("DailyPnL", 0, "BASE"),
        ],
        [
            _account_row("NetLiquidation", 100000, "BASE"),
            _account_row("AvailableFunds", 50000, "BASE"),
            _account_row("GrossPositionValue", 12000, "BASE"),
        ],
    ],
)
def test_account_gate_rejects_unbound_mixed_or_duplicate_money_rows(money_rows):
    rows = [_account_row("Currency", "USD", ""), *money_rows]
    values, currency_evidence = pa._account_value_snapshot(
        _ReconcileIB(values=rows), "DU123"
    )
    state = _armed_submission_state()
    state["account"].update(currency_evidence)
    state["account"]["net_liquidation"] = values.get("NetLiquidation")
    state["account"]["available_funds"] = values.get("AvailableFunds")
    state["account"]["gross_position_value"] = values.get(
        "GrossPositionValue", values.get("StockMarketValue")
    )
    state["daily_pnl"] = values.get("DailyPnL", values.get("Daily PnL"))

    gate = pa.account_gate(
        state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()}
    )

    assert currency_evidence["base_currency"] == "USD"
    assert currency_evidence["risk_value_currency_evidence"] != "VERIFIED"
    assert gate["allowed"] is False
    assert "Kontowerte nicht eindeutig in USD verifiziert" in gate["reasons"]


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
    assert pa._risk_store().transition_execution_state(True)["armed"] is True
    assert pa.account_gate(
        {
            "broker_connected": True,
            "broker_error": None,
            "account": {
                "selected": "DU123",
                "paper": True,
                "base_currency": "USD",
                "base_currency_evidence": "VERIFIED",
                "risk_value_currency_evidence": "VERIFIED",
                "risk_value_currency_errors": [],
                "net_liquidation": 100000,
                "available_funds": 50000,
                "gross_position_value": 0,
            },
            "daily_pnl": 0,
            "positions_snapshot_complete": True,
            "orders_snapshot_complete": True,
            "fills_snapshot_complete": True,
            "risk_evidence_unreliable": False,
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
                "base_currency": "USD",
                "base_currency_evidence": "VERIFIED",
                "risk_value_currency_evidence": "VERIFIED",
                "risk_value_currency_errors": [],
                "net_liquidation": 100000,
                "available_funds": 50000,
                "gross_position_value": 0,
            },
            "daily_pnl": 0,
                "positions": [],
                "open_orders": [],
                "fills": [],
                "orders_snapshot_complete": True,
                "positions_snapshot_complete": True,
                "fills_snapshot_complete": True,
                "risk_evidence_unreliable": False,
                "intents": [],
            }
    )
    ib = _OrderIB()
    contract = SimpleNamespace(symbol="XYZ", conId=123, secType="STK", currency="USD")
    monkeypatch.setattr(pa, "reconcile_broker", lambda **_kwargs: pa.state_read())
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


class _RecordingRiskStore:
    """Narrow durable-store double used to assert the execution boundary."""

    def __init__(self, *, admission_allowed=True, broker_visible=True):
        self.admission_allowed = admission_allowed
        self.broker_visible = broker_visible
        self.calls = []
        self.intents = []
        self.mappings = []
        self.reservations = []
        self.admission_kwargs = []
        self.reconcile_required = []
        self.visible = []
        self.appended_fills = []
        self.outcomes = []
        self.order_observations = []
        self.execution_claim_contexts = []
        self.execution_claim_acks = []
        self.execution_claim_quarantines = []
        self.active_execution_claims = []
        self._next_execution_claim = 1
        self.execution_generation = 1
        self.execution_armed = True

    def execution_state(self):
        return {
            "armed": self.execution_armed,
            "generation": self.execution_generation,
            "reason": None,
        }

    def transition_execution_state(self, armed):
        self.execution_generation += 1
        self.execution_armed = bool(armed)
        return {
            "updated": True,
            "armed": self.execution_armed,
            "generation": self.execution_generation,
            "reason": None,
        }

    def run_if_execution_generation(
        self, expected_generation, operation, **_kwargs
    ):
        self.execution_claim_contexts.append(
            dict(_kwargs.get("claim_context") or {})
        )
        if (
            not self.execution_armed
            or expected_generation != self.execution_generation
        ):
            return {
                "executed": False,
                "result": None,
                "armed": self.execution_armed,
                "generation": self.execution_generation,
                "reason": "execution_generation_fenced",
            }
        claim_id = f"{self._next_execution_claim:032x}"
        self._next_execution_claim += 1
        retained = _kwargs.get("retain_until_ack") is True
        if retained:
            callback = _kwargs.get("on_claim_registered")
            assert callable(callback)
            callback(claim_id)
            self.active_execution_claims.append(claim_id)
        response = {
            "executed": True,
            "result": operation(),
            "armed": True,
            "generation": self.execution_generation,
            "reason": None,
        }
        if retained:
            response["write_id"] = claim_id
        return response

    def acknowledge_execution_write(self, write_id, *, expected_generation):
        self.calls.append("ack_execution_write")
        self.execution_claim_acks.append((write_id, expected_generation))
        if (
            not self.execution_armed
            or expected_generation != self.execution_generation
            or write_id not in self.active_execution_claims
        ):
            return {"updated": False, "reason": "execution_generation_fenced"}
        self.active_execution_claims.remove(write_id)
        return {"updated": True, "reason": None}

    def quarantine_execution_writes(self, write_ids, *, expected_generation):
        claim_ids = list(write_ids)
        self.calls.append("quarantine_execution_writes")
        self.execution_claim_quarantines.append(
            (claim_ids, expected_generation)
        )
        for write_id in claim_ids:
            if write_id in self.active_execution_claims:
                self.active_execution_claims.remove(write_id)
        if self.execution_armed:
            self.execution_generation += 1
            self.execution_armed = False
        return {
            "updated": True,
            "status": "ORPHANED",
            "armed": False,
            "generation": self.execution_generation,
            "reason": None,
        }

    def wait_for_execution_writes(self, **_kwargs):
        return {"drained": True, "active_count": 0, "reason": None}

    def register_intent(self, intent):
        self.calls.append("register_intent")
        self.intents.append(dict(intent))
        return {"accepted": True, "idempotent": False, "conflict": None}

    def acquire_lease(self, lease_key, owner_token, *, now, ttl_seconds):
        self.calls.append("acquire_lease")
        return {"acquired": True, "fence_token": 1}

    def renew_lease(
        self, lease_key, owner_token, fence_token, *, now, ttl_seconds
    ):
        self.calls.append("renew_lease")
        return {"renewed": True, "fence_token": fence_token}

    def reserve_if_allowed(self, reservation, **kwargs):
        self.calls.append("reserve")
        self.reservations.append(dict(reservation))
        self.admission_kwargs.append(dict(kwargs))
        return {
            "allowed": self.admission_allowed,
            "decision": "reserved" if self.admission_allowed else "risk_blocked",
            "risk": {"reasons": [] if self.admission_allowed else ["risk_state_unresolved"]},
        }

    def register_intent_order(self, setup_id, mapping, **kwargs):
        execution_generation = kwargs.get("execution_generation")
        if (
            execution_generation is not None
            and (
                not self.execution_armed
                or execution_generation != self.execution_generation
            )
        ):
            return {
                "accepted": False,
                "idempotent": False,
                "conflict": "execution_generation_fenced",
            }
        self.calls.append("register_intent_order")
        self.mappings.append((setup_id, dict(mapping)))
        return {"accepted": True, "idempotent": False, "conflict": None}

    def mark_reservation_reconcile_required(self, reservation_id, **kwargs):
        self.calls.append("reconcile_required")
        self.reconcile_required.append((reservation_id, dict(kwargs)))
        return {"updated": True, "status": "RECONCILE_REQUIRED"}

    def mark_reservation_broker_visible(self, reservation_id, order_ids, **kwargs):
        self.calls.append("broker_visible")
        self.visible.append((reservation_id, list(order_ids), dict(kwargs)))
        return {
            "updated": self.broker_visible,
            "status": "BROKER_VISIBLE" if self.broker_visible else "RECONCILE_REQUIRED",
        }

    def append_fill(self, fill):
        self.appended_fills.append(dict(fill))
        return {"accepted": True, "idempotent": False, "conflict": None, "persisted": True}

    def fill_evidence(self, _setup_id):
        return {
            "fills": list(self.appended_fills),
            "reliable": True,
            "unresolved_codes": [],
            "conflicting_events": [],
            "fill_set_hash": "ledger-fill-set",
        }

    def active_reservations(self):
        return []

    def observe_open_orders(self, orders, **kwargs):
        account = kwargs["account"]
        snapshot_complete = kwargs["snapshot_complete"]
        observed_at = kwargs["observed_at"]
        self.order_observations.append(
            {
                "orders": [dict(order) for order in orders],
                "positions": [dict(value) for value in kwargs["positions"]],
                "account": account,
                "snapshot_complete": snapshot_complete,
                "positions_snapshot_complete": kwargs["positions_snapshot_complete"],
                "fills_snapshot_complete": kwargs["fills_snapshot_complete"],
                "observed_at": observed_at,
            }
        )
        complete = (
            snapshot_complete is True
            and kwargs["positions_snapshot_complete"] is True
            and kwargs["fills_snapshot_complete"] is True
        )
        return {
            "accepted": complete,
            "conflicts": [],
            "reason": None if complete else "snapshot_incomplete",
            "terminal_setup_ids": [],
            "position_setup_ids": [],
        }

    def record_outcome(self, outcome, **kwargs):
        captured = dict(outcome)
        captured["_record_kwargs"] = dict(kwargs)
        captured["_broker_position_open"] = kwargs.get("broker_position_open")
        captured["_parent_orders_terminal"] = kwargs.get("parent_orders_terminal")
        self.outcomes.append(captured)
        return {"accepted": True, "idempotent": False, "conflict": None, "transition": "completed"}


def _armed_submission_state() -> dict:
    return {
        "status": "running",
        "broker_connected": True,
        "broker_error": None,
        "account": {
            "selected": "DU123",
            "paper": True,
            "base_currency": "USD",
            "base_currency_evidence": "VERIFIED",
            "risk_value_currency_evidence": "VERIFIED",
            "risk_value_currency_errors": [],
            "net_liquidation": 100000,
            "available_funds": 50000,
            "gross_position_value": 0,
        },
        "daily_pnl": 0,
        "positions": [],
        "open_orders": [],
        "fills": [],
        "orders_snapshot_complete": True,
        "positions_snapshot_complete": True,
        "fills_snapshot_complete": True,
        "intents": [],
    }


def _armed_submission_config() -> dict:
    return {
        "selected_account": "DU123",
        "mode": "paper_auto",
        "paper_only": True,
        "execution_enabled": True,
        "kill_switch": False,
        "max_notional_per_trade": 100000,
    }


def _submission_signal() -> dict:
    return {
        "ticker": "XYZ",
        "direction": "LONG",
        "trigger_bar_date": "2026-08-08",
        "entry": 10.003,
        "stop": 9.5,
        "tp1": 11.0,
        "tp2": 12.0,
        "group_key": "TECH",
        "group_verified": True,
    }


def _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib):
    pa._config_save_internal(_armed_submission_config(), allow_execution_state=True)
    pa.state_write(_armed_submission_state())
    contract = SimpleNamespace(symbol="XYZ", conId=123, secType="STK", currency="USD")
    monkeypatch.setattr(pa, "reconcile_broker", lambda **_kwargs: pa.state_read())
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "ib_get_contract", lambda *_args: contract)
    monkeypatch.setattr(pa, "Order", _FakeOrder)
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)


def test_submit_reserves_final_tick_plan_before_first_broker_order(
    monkeypatch, isolated_autotrader_store
):
    class _NickelTickIB(_OrderIB):
        def reqContractDetails(self, _contract):
            return [SimpleNamespace(minTick=0.05)]

    ib = _NickelTickIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    reconcile_calls = []

    def reconcile_for_submit(**kwargs):
        reconcile_calls.append(dict(kwargs))
        return pa.state_read()

    monkeypatch.setattr(pa, "reconcile_broker", reconcile_for_submit)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is True
    assert result["submitted"] is True
    assert reconcile_calls == [{}, {"require_fresh": True}]
    assert store.calls.index("reserve") < store.calls.index("register_intent_order")
    assert store.reservations[0]["entry"] == result["levels"]["entry"]
    assert store.reservations[0]["stop"] == result["levels"]["stop"]
    assert store.reservations[0]["quantity"] == result["sizing"]["quantity"]
    assert store.admission_kwargs[0]["gross_position_value"] == 0
    assert store.admission_kwargs[0]["max_total_exposure_pct"] == 20.0
    assert store.admission_kwargs[0]["max_positions"] == 3
    assert store.admission_kwargs[0]["orders_snapshot_complete"] is True
    assert store.admission_kwargs[0]["available_funds"] == 50000
    assert store.admission_kwargs[0]["min_available_funds"] == 500
    assert len(store.mappings) == 2 * len(ib.placed)
    assert all(mapping[1]["perm_id"] > 0 for mapping in store.mappings[-len(ib.placed):])
    assert store.visible[0][1] == result["order_ids"]
    broker_claims = [
        context
        for context in store.execution_claim_contexts
        if context.get("operation_kind") == "PLACE_ORDER"
    ]
    assert len(broker_claims) == len(ib.placed)
    assert {context["account"] for context in broker_claims} == {"DU123"}
    assert {context["setup_id"] for context in broker_claims} == {
        store.intents[0]["setup_id"]
    }
    assert {context["order_id"] for context in broker_claims} == set(
        result["order_ids"]
    )
    assert all(str(context["order_ref"]).startswith("AS2-") for context in broker_claims)
    assert len(store.execution_claim_acks) == len(ib.placed)
    assert store.calls.index("broker_visible") < store.calls.index(
        "ack_execution_write"
    )
    assert store.active_execution_claims == []
    assert store.execution_claim_contexts[-1]["operation_kind"] == "LOCAL_STATE"


def test_submission_binds_broker_assigned_client_id_not_pre_place_default(
    monkeypatch, isolated_autotrader_store
):
    class _ClientAssignedIB(_OrderIB):
        def placeOrder(self, contract, order):
            trade = super().placeOrder(contract, order)
            trade.order.clientId = 1
            return trade

    ib = _ClientAssignedIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is True
    assert result["submitted"] is True
    assert {mapping[1]["client_id"] for mapping in store.mappings} == {1}
    evidence = store.visible[0][2]["broker_order_evidence"]
    assert {row["client_id"] for row in evidence} == {1}


def test_risk_admission_failure_never_reaches_place_order(
    monkeypatch, isolated_autotrader_store
):
    ib = _OrderIB()
    store = _RecordingRiskStore(admission_allowed=False)
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert ib.placed == []
    assert "reserve" in store.calls
    assert pa.state_read()["intents"] == []


def test_kill_after_admission_fences_paused_submit_before_broker_write(
    monkeypatch, isolated_autotrader_store
):
    admitted = Event()
    resume_submission = Event()

    class _GenerationStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.generation = 1
            self.armed = True

        def execution_state(self):
            return {
                "armed": self.armed,
                "generation": self.generation,
                "reason": None,
            }

        def transition_execution_state(self, armed):
            self.generation += 1
            self.armed = bool(armed)
            return {
                "updated": True,
                "armed": self.armed,
                "generation": self.generation,
                "reason": None,
            }

        def run_if_execution_generation(
            self, expected_generation, operation, **_kwargs
        ):
            if not self.armed or expected_generation != self.generation:
                return {
                    "executed": False,
                    "result": None,
                    "armed": self.armed,
                    "generation": self.generation,
                    "reason": "execution_generation_fenced",
                }
            return {
                "executed": True,
                "result": operation(),
                "armed": True,
                "generation": self.generation,
                "reason": None,
            }

        def reserve_if_allowed(self, reservation, **kwargs):
            result = super().reserve_if_allowed(reservation, **kwargs)
            admitted.set()
            assert resume_submission.wait(timeout=10)
            return result

    ib = _OrderIB()
    store = _GenerationStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    outcome = {}
    worker = Thread(
        target=lambda: outcome.setdefault("result", pa.submit_signal(_submission_signal()))
    )
    worker.start()
    assert admitted.wait(timeout=10)
    try:
        killed = pa.engage_kill_switch()
    finally:
        resume_submission.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert killed["ok"] is True
    assert store.execution_state()["armed"] is False
    assert outcome["result"]["submitted"] is False
    assert ib.placed == []


def test_stale_after_broker_write_is_quarantined_with_protection_and_ids_retained(
    monkeypatch, isolated_autotrader_store
):
    class _StaleAfterTargetWriteStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.write_count = 0

        def run_if_execution_generation(
            self, expected_generation, operation, **_kwargs
        ):
            result = super().run_if_execution_generation(
                expected_generation, operation, **_kwargs
            )
            if not result.get("executed"):
                return result
            self.write_count += 1
            if self.write_count == 3:
                self.execution_generation += 1
                self.execution_armed = False
                result["armed"] = False
                result["generation"] = self.execution_generation
                result["reason"] = "execution_generation_fenced_after_write"
            return result

    ib = _OrderIB()
    store = _StaleAfterTargetWriteStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert "execution_generation_fenced_after_write" in result["error"]
    assert len(ib.placed) == 3
    placed_ids = [trade.order.orderId for trade in ib.placed]
    saved = pa.state_read()["intents"][0]
    assert saved["status"] == "RECONCILE_REQUIRED"
    assert saved["risk_reservation_status"] == "RECONCILE_REQUIRED"
    assert saved["order_ids"] == placed_ids
    assert store.visible == []
    assert store.execution_claim_quarantines
    assert store.active_execution_claims == []
    assert len(store.reconcile_required) == 1
    cancelled_refs = {order.orderRef for order in ib.cancelled}
    assert cancelled_refs == {
        trade.order.orderRef
        for trade in ib.placed
        if trade.order.orderRef.endswith("-P1")
    }
    protective_refs = {
        trade.order.orderRef
        for trade in ib.placed
        if "-S" in trade.order.orderRef or "-T" in trade.order.orderRef
    }
    assert protective_refs
    assert protective_refs.isdisjoint(cancelled_refs)


def test_generation_fenced_after_broker_visible_never_returns_submit_success(
    monkeypatch, isolated_autotrader_store
):
    class _FenceAfterVisibleStore(_RecordingRiskStore):
        def mark_reservation_broker_visible(self, reservation_id, order_ids, **kwargs):
            result = super().mark_reservation_broker_visible(
                reservation_id, order_ids, **kwargs
            )
            self.execution_generation += 1
            self.execution_armed = False
            return result

    ib = _OrderIB()
    store = _FenceAfterVisibleStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    saved = pa.state_read()["intents"][0]
    assert saved["status"] == "RECONCILE_REQUIRED"
    assert saved["risk_reservation_status"] == "RECONCILE_REQUIRED"
    assert saved["manual_reconciliation_required"] is True
    assert len(store.reconcile_required) == 1


def test_kill_unacknowledged_parent_cancel_is_not_reported_successful(
    monkeypatch, isolated_autotrader_store
):
    ib = _OrderIB()
    contract = SimpleNamespace(symbol="XYZ", conId=123)
    parent = _FakeOrder(
        orderId=91,
        parentId=0,
        orderRef="AS2-KILL-P1",
        totalQuantity=10,
    )
    ib.placeOrder(contract, parent)
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)

    result = pa.engage_kill_switch()

    assert result["ok"] is False
    assert result["cancelled_entry_orders"] == []
    assert result["pending_cancel_entry_orders"] == [91]
    assert ib.cancelled == [parent]
    assert result["config"]["kill_switch"] is True
    assert store.execution_armed is False


def test_kill_reports_parent_cancel_only_after_terminal_broker_ack(
    monkeypatch, isolated_autotrader_store
):
    class _AckKillIB(_OrderIB):
        def cancelOrder(self, order):
            super().cancelOrder(order)
            trade = next(row for row in self.placed if row.order is order)
            trade.orderStatus.status = "Cancelled"
            trade.orderStatus.filled = 0
            trade.orderStatus.remaining = order.totalQuantity

    ib = _AckKillIB()
    contract = SimpleNamespace(symbol="XYZ", conId=123)
    parent = _FakeOrder(
        orderId=92,
        parentId=0,
        orderRef="AS2-KILL-ACK-P1",
        totalQuantity=10,
    )
    ib.placeOrder(contract, parent)
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)

    result = pa.engage_kill_switch()

    assert result["ok"] is True
    assert result["cancelled_entry_orders"] == [92]
    assert result["pending_cancel_entry_orders"] == []
    assert result["filled_entry_orders"] == []


def test_kill_ignores_unrelated_ibkr_informational_warning(
    monkeypatch, isolated_autotrader_store
):
    class _WarningIB(_OrderIB):
        def run(self, awaitable, *, timeout=None):
            self.errorEvent.emit(
                -1, 2104, "Market data farm connection is OK", None
            )
            return super().run(awaitable, timeout=timeout)

    ib = _WarningIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)

    result = pa.engage_kill_switch()

    assert result["ok"] is True
    assert result["broker_order_refresh_complete"] is True
    assert result["broker_order_snapshot_complete"] is True


@pytest.mark.parametrize(
    ("connected", "expose_ib", "refresh_mode"),
    [
        (False, True, "ok"),
        (True, False, "ok"),
        (True, True, "timeout"),
        (True, True, "none"),
        (True, True, "request_error"),
        (True, True, "server_disconnect"),
    ],
)
def test_kill_without_complete_broker_order_snapshot_is_never_successful(
    monkeypatch, isolated_autotrader_store, connected, expose_ib, refresh_mode
):
    ib = _OrderIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: connected)
    if not expose_ib:
        monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": None})
    elif refresh_mode == "timeout":
        def fail_refresh(_awaitable, *, timeout=None):
            raise TimeoutError("open-order refresh timed out")

        monkeypatch.setattr(ib, "run", fail_refresh)
    elif refresh_mode == "none":
        monkeypatch.setattr(ib, "run", lambda _awaitable, *, timeout=None: None)
    elif refresh_mode == "request_error":
        def fail_with_error_event(_awaitable, *, timeout=None):
            ib.errorEvent.emit(-1, 321, "open-order request failed", None)
            return []

        monkeypatch.setattr(ib, "run", fail_with_error_event)
    elif refresh_mode == "server_disconnect":
        def fail_with_server_disconnect(_awaitable, *, timeout=None):
            ib.errorEvent.emit(
                -1,
                2110,
                "Connectivity between TWS and server is broken",
                None,
            )
            return []

        monkeypatch.setattr(ib, "run", fail_with_server_disconnect)

    result = pa.engage_kill_switch()

    assert result["ok"] is False
    assert result["broker_order_snapshot_complete"] is False
    assert ib.RequestTimeout == 0
    assert ib.RaiseRequestErrors is False
    assert store.execution_armed is False
    assert result["config"]["execution_enabled"] is False
    assert any("Broker-Order" in error for error in result["errors"])


def test_kill_with_undrained_broker_write_is_disarmed_but_not_successful(
    monkeypatch, isolated_autotrader_store
):
    class _UndrainedStore(_RecordingRiskStore):
        def wait_for_execution_writes(self, **_kwargs):
            return {
                "drained": False,
                "active_count": 1,
                "reason": "execution_writes_active",
            }

    ib = _OrderIB()
    store = _UndrainedStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    previous_generation = store.execution_generation

    result = pa.engage_kill_switch()

    assert result["ok"] is False
    assert result["execution_state"] == {
        "updated": True,
        "armed": False,
        "generation": previous_generation + 1,
        "reason": None,
    }
    assert store.execution_state()["armed"] is False
    assert result["config"]["kill_switch"] is True
    assert result["config"]["execution_enabled"] is False
    assert any("execution_writes_active" in error for error in result["errors"])


def test_kill_recovers_crashed_claim_only_after_fresh_broker_reconciliation(
    monkeypatch, isolated_autotrader_store
):
    class _OrphanKillStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.recovered = False

        def wait_for_execution_writes(self, **_kwargs):
            if self.recovered:
                return {"drained": True, "active_count": 0, "reason": None}
            return {
                "drained": False,
                "active_count": 0,
                "orphaned_count": 1,
                "reason": "execution_writes_orphaned",
            }

    ib = _OrderIB()
    store = _OrphanKillStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    recovery_calls = []

    def reconcile(*, require_fresh=False, expected_recovery_generation=None):
        recovery_calls.append((require_fresh, expected_recovery_generation))
        assert require_fresh is True
        assert expected_recovery_generation == store.execution_generation
        store.recovered = True
        return _armed_submission_state()

    monkeypatch.setattr(pa, "reconcile_broker", reconcile)

    result = pa.engage_kill_switch()

    assert result["ok"] is True
    assert result["execution_drain"] == {
        "drained": True,
        "active_count": 0,
        "reason": None,
    }
    assert result["manual_reconciliation_required"] is False
    assert recovery_calls == [(True, store.execution_generation)]


def test_missing_order_snapshot_completeness_is_not_certified_for_admission(
    monkeypatch, isolated_autotrader_store
):
    class _CompletenessStore(_RecordingRiskStore):
        def reserve_if_allowed(self, reservation, **kwargs):
            result = super().reserve_if_allowed(reservation, **kwargs)
            if kwargs.get("orders_snapshot_complete") is not True:
                return {
                    "allowed": False,
                    "decision": "risk_blocked",
                    "risk": {"reasons": ["risk_state_unresolved"]},
                }
            return result

    ib = _OrderIB()
    store = _CompletenessStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    state = pa.state_read()
    state.pop("orders_snapshot_complete", None)
    pa.state_write(state)

    result = pa.submit_signal(_submission_signal())

    assert result["submitted"] is False
    assert result["gate"]["allowed"] is False
    assert "Order-Snapshot unvollstaendig" in result["gate"]["reasons"]
    assert store.admission_kwargs == []
    assert ib.placed == []


def test_partial_submit_is_cancelled_and_marked_for_fenced_reconciliation(
    monkeypatch, isolated_autotrader_store
):
    class _FailAfterFirstIB(_OrderIB):
        def placeOrder(self, contract, order):
            if self.placed:
                raise RuntimeError("simulated broker failure")
            return super().placeOrder(contract, order)

    ib = _FailAfterFirstIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert ib.cancelled
    assert store.reconcile_required
    saved = pa.state_read()["intents"][0]
    assert saved["status"] == "RECONCILE_REQUIRED"
    assert saved["manual_reconciliation_required"] is True


def test_ambiguous_place_order_exception_retains_id_and_cancels_candidate(
    monkeypatch, isolated_autotrader_store
):
    class _AmbiguousSendIB(_OrderIB):
        def __init__(self):
            super().__init__()
            self.accepted = []
            self.client = SimpleNamespace(getReqId=self._get_req_id)

        def _get_req_id(self):
            order_id = self.next_order_id
            self.next_order_id += 1
            return order_id

        def placeOrder(self, contract, order):
            if not order.orderId:
                order.orderId = self._get_req_id()
            self.accepted.append(order)
            raise ConnectionError("socket outcome ambiguous after send")

    ib = _AmbiguousSendIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert len(ib.accepted) == 1
    assert ib.accepted[0].orderId > 0
    assert ib.cancelled == [ib.accepted[0]]
    saved = pa.state_read()["intents"][0]
    assert saved["order_ids"] == [ib.accepted[0].orderId]
    assert saved["status"] == "RECONCILE_REQUIRED"
    assert saved["manual_reconciliation_required"] is True
    assert store.reconcile_required


def test_persisted_mapping_conflict_is_not_submission_success(
    monkeypatch, isolated_autotrader_store
):
    class _ConflictingMappingStore(_RecordingRiskStore):
        def register_intent_order(self, setup_id, mapping, **_kwargs):
            self.calls.append("register_intent_order")
            self.mappings.append((setup_id, dict(mapping)))
            return {
                "accepted": True,
                "idempotent": False,
                "conflict": "order_mapping_seen_after_release",
            }

    ib = _OrderIB()
    store = _ConflictingMappingStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert len(ib.placed) == 1
    assert ib.cancelled == [ib.placed[0].order]
    assert store.reconcile_required
    assert not store.visible
    assert pa.state_read()["intents"][0]["status"] == "RECONCILE_REQUIRED"


def test_missing_exact_broker_visibility_is_not_reported_as_submission_success(
    monkeypatch, isolated_autotrader_store
):
    class _InvisibleIB(_OrderIB):
        def openTrades(self):
            return []

    ib = _InvisibleIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert ib.cancelled
    assert store.reconcile_required
    assert not store.visible


def test_fast_fill_recovery_never_cancels_protective_children(
    monkeypatch, isolated_autotrader_store
):
    ib = _FastFillIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert store.reconcile_required
    assert not store.visible
    cancelled_refs = {order.orderRef for order in ib.cancelled}
    assert all("-S" not in ref and "-T" not in ref for ref in cancelled_refs)
    assert pa.state_read()["intents"][0]["status"] == "RECONCILE_REQUIRED"


def test_recovery_removes_protection_only_after_parent_ack_and_fresh_no_fill(
    monkeypatch, isolated_autotrader_store
):
    ib = _CancelAckIB()
    store = _RecordingRiskStore(broker_visible=False)
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    cancelled_refs = [order.orderRef for order in ib.cancelled]
    assert len(cancelled_refs) == 6
    assert all(ref.endswith(("-P1", "-P2")) for ref in cancelled_refs[:2])
    assert all("-S" in ref or "-T" in ref for ref in cancelled_refs[2:])
    second_parent_event = max(
        index
        for index, event in enumerate(ib.events)
        if event[0] == "cancel" and event[1].endswith(("-P1", "-P2"))
    )
    first_protection_event = min(
        index
        for index, event in enumerate(ib.events)
        if event[0] == "cancel" and ("-S" in event[1] or "-T" in event[1])
    )
    assert any(
        event[0] in {"req_positions", "req_executions"}
        and event[1] >= 2
        and second_parent_event < index < first_protection_event
        for index, event in enumerate(ib.events)
    )
    assert store.reconcile_required


def test_recovery_never_reports_unacknowledged_child_cancel_as_removed(
    monkeypatch, isolated_autotrader_store
):
    ib = _CancelAckIB(acknowledge_children=False)
    store = _RecordingRiskStore(broker_visible=False)
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    saved = pa.state_read()["intents"][0]
    protective_ids = {
        trade.order.orderId
        for trade in ib.placed
        if "-S" in trade.order.orderRef or "-T" in trade.order.orderRef
    }
    assert saved["manual_reconciliation_required"] is True
    assert saved["protective_orders_retained"] is True
    assert set(saved["protection_cancel_requested_order_ids"]) == protective_ids
    assert set(saved["protection_cancel_pending_order_ids"]) == protective_ids
    assert saved["protection_cancel_acknowledged_order_ids"] == []


def test_fill_callback_during_parent_cancel_keeps_all_protective_orders(
    monkeypatch, isolated_autotrader_store
):
    ib = _CancelAckIB(fill_during_first_cancel=True)
    store = _RecordingRiskStore(broker_visible=False)
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    cancelled_refs = {order.orderRef for order in ib.cancelled}
    parent_refs = {
        trade.order.orderRef
        for trade in ib.placed
        if trade.order.orderRef.endswith(("-P1", "-P2"))
    }
    protective_refs = {
        trade.order.orderRef
        for trade in ib.placed
        if "-S" in trade.order.orderRef or "-T" in trade.order.orderRef
    }
    assert cancelled_refs == parent_refs
    assert protective_refs.isdisjoint(cancelled_refs)
    assert store.reconcile_required
    assert pa.state_read()["intents"][0]["status"] == "RECONCILE_REQUIRED"


def test_missing_parent_cancel_ack_keeps_protection_even_with_empty_snapshots(
    monkeypatch, isolated_autotrader_store
):
    ib = _CancelAckIB(acknowledge_parents=False)
    store = _RecordingRiskStore(broker_visible=False)
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    cancelled_refs = {order.orderRef for order in ib.cancelled}
    parent_refs = {
        trade.order.orderRef
        for trade in ib.placed
        if trade.order.orderRef.endswith(("-P1", "-P2"))
    }
    assert cancelled_refs == parent_refs
    assert store.reconcile_required
    saved = pa.state_read()["intents"][0]
    assert saved["status"] == "RECONCILE_REQUIRED"
    assert saved["manual_reconciliation_required"] is True


def test_tighten_stop_is_fail_closed_without_any_broker_call(
    monkeypatch, isolated_autotrader_store
):
    class _NoBrokerMutationIB:
        def __init__(self):
            self.open_trade_calls = 0
            self.place_calls = 0

        def openTrades(self):
            self.open_trade_calls += 1
            return []

        def placeOrder(self, *_args):
            self.place_calls += 1
            raise AssertionError("tighten_stop must not call placeOrder")

    ib = _NoBrokerMutationIB()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})

    result = pa.tighten_stop("XYZ", 9.75)

    assert result == {
        "ok": False, "error": "authorized_geometry_revision_unavailable"
    }
    assert ib.open_trade_calls == 0
    assert ib.place_calls == 0


def test_reconcile_persists_con_id_fills_and_uses_ledger_hash_for_outcomes(
    monkeypatch, isolated_autotrader_store
):
    store = _RecordingRiskStore()
    pa.config_save({"selected_account": "DU123"})
    pa.state_write(
        {
            "status": "running",
            "intents": [
                {
                    "setup_id": "SETUP-LEDGER",
                    "order_ref": "AS2-SETUP-LEDGER",
                    "ticker": "XYZ",
                    "account": "DU123",
                    "con_id": 123,
                    "direction": "LONG",
                    "quantity": 10,
                    "entry": 10.0,
                    "stop": 9.0,
                    "group_key": "TECH",
                    "group_verified": True,
                    "status": "WORKING",
                    "risk_reservation_id": "reservation-SETUP-LEDGER",
                    "order_ids": [10, 11],
                    "parent_order_ids": [10],
                }
            ],
        }
    )
    ledger_entry = {
        "exec_id": "LEDGER-ENTRY",
        "account": "DU123",
        "con_id": 123,
        "order_id": 10,
        "side": "BOT",
        "shares": 10.0,
        "price": 10.0,
        "time": "2026-08-21T10:00:00+00:00",
        "ledger_sequence": 1,
    }
    ledger_exit = {
        "exec_id": "LEDGER-EXIT",
        "account": "DU123",
        "con_id": 123,
        "order_id": 11,
        "side": "SLD",
        "shares": 10.0,
        "price": 11.0,
        "time": "2026-08-21T10:01:00+00:00",
        "ledger_sequence": 2,
    }
    store.fill_evidence = lambda _setup_id: {
        "fills": [ledger_entry, ledger_exit],
        "reliable": True,
        "unresolved_codes": [],
        "conflicting_events": [],
        "fill_set_hash": "ledger-fill-set",
    }
    unrelated_order = _FakeOrder(
        action="BUY",
        orderType="LMT",
        lmtPrice=25.0,
        totalQuantity=1,
        parentId=0,
        account="DU123",
        orderRef="MANUAL-UNRELATED",
        orderId=999,
    )
    unrelated_trade = SimpleNamespace(
        contract=SimpleNamespace(symbol="ABC", conId=456),
        order=unrelated_order,
        orderStatus=SimpleNamespace(
            status="Submitted", filled=0, remaining=1, avgFillPrice=0
        ),
    )
    ib = _ReconcileIB(
        fills=[_fill(10, "BOT", 10)], open_trades=[unrelated_trade]
    )
    ib._fills[0].contract.conId = 123
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    pa.reconcile_broker()

    assert store.appended_fills[0]["con_id"] == 123
    assert store.outcomes[0]["complete"] is True
    assert store.outcomes[0]["fill_set_hash"] == "ledger-fill-set"
    assert store.outcomes[0]["_broker_position_open"] is False
    assert store.outcomes[0]["_parent_orders_terminal"] is True
    terminal_call = store.outcomes[0]["_record_kwargs"]
    assert terminal_call["reservation_id"] == "reservation-SETUP-LEDGER"
    assert terminal_call["lease_key"] == "submit:SETUP-LEDGER"
    assert terminal_call["owner_token"].startswith("paper-autotrader-reconcile:")
    assert terminal_call["fence_token"] == 1
    assert terminal_call["now"].tzinfo is not None
    assert terminal_call["terminal_evidence"] == {
        "snapshot_complete": True,
        "observed_at": terminal_call["terminal_evidence"]["observed_at"],
        "account": "DU123",
        "con_id": 123,
        "position_open": False,
        "open_order_ids": [999],
        "open_orders": terminal_call["terminal_evidence"]["open_orders"],
    }
    assert terminal_call["terminal_evidence"]["open_orders"][0]["order_id"] == 999
    observed_at = datetime.fromisoformat(
        terminal_call["terminal_evidence"]["observed_at"]
    )
    assert observed_at.tzinfo is not None
    assert store.calls[-2:] == ["acquire_lease", "renew_lease"]


@pytest.mark.parametrize("lease_failure", ["held", "fenced"])
def test_complete_outcome_fails_closed_without_an_owned_submit_fence(lease_failure):
    class _UnavailableLeaseStore(_RecordingRiskStore):
        def acquire_lease(self, lease_key, owner_token, *, now, ttl_seconds):
            self.calls.append("acquire_lease")
            if lease_failure == "held":
                return {"acquired": False, "reason": "lease_held", "fence_token": 7}
            return {"acquired": True, "fence_token": 8}

        def renew_lease(
            self, lease_key, owner_token, fence_token, *, now, ttl_seconds
        ):
            self.calls.append("renew_lease")
            return {"renewed": False, "reason": "lease_fenced"}

    store = _UnavailableLeaseStore()
    store.appended_fills = [
        {
            "exec_id": "FENCE-E",
            "account": "DU123",
            "con_id": 123,
            "order_id": 10,
            "side": "BOT",
            "shares": 10.0,
            "price": 10.0,
            "time": "2026-08-21T10:00:00+00:00",
            "ledger_sequence": 1,
        },
        {
            "exec_id": "FENCE-X",
            "account": "DU123",
            "con_id": 123,
            "order_id": 11,
            "side": "SLD",
            "shares": 10.0,
            "price": 11.0,
            "time": "2026-08-21T10:01:00+00:00",
            "ledger_sequence": 2,
        },
    ]
    intent = {
        "setup_id": "FENCED-COMPLETE",
        "order_ref": "AS2-FENCED-COMPLETE",
        "ticker": "XYZ",
        "account": "DU123",
        "con_id": 123,
        "direction": "LONG",
        "quantity": 10,
        "entry": 10.0,
        "stop": 9.0,
        "risk_reservation_id": "reservation-FENCED-COMPLETE",
        "order_ids": [10, 11],
        "parent_order_ids": [10],
    }

    result = pa._record_ledger_outcome(
        store,
        intent,
        [],
        [],
        orders_snapshot_complete=True,
        snapshot_observed_at=datetime.now(timezone.utc),
    )

    assert result["accepted"] is False
    assert "lease" in result["conflict"]
    assert all(outcome["complete"] is False for outcome in store.outcomes)
    assert all(
        "terminal_evidence" not in outcome["_record_kwargs"]
        for outcome in store.outcomes
    )
    assert store.calls[0] == "acquire_lease"
    if lease_failure == "fenced":
        assert store.calls[-1] == "renew_lease"


@pytest.mark.parametrize("contradiction", ["position", "parent"])
def test_existing_complete_outcome_is_not_idempotent_on_live_broker_exposure(
    contradiction,
):
    store = _RecordingRiskStore()
    store.appended_fills = [
        {
            "exec_id": "COMPLETE-ENTRY",
            "account": "DU123",
            "con_id": 123,
            "order_id": 10,
            "side": "BOT",
            "shares": 10,
            "price": 10,
            "time": "2026-08-21T10:00:00+00:00",
            "ledger_sequence": 1,
        },
        {
            "exec_id": "COMPLETE-EXIT",
            "account": "DU123",
            "con_id": 123,
            "order_id": 11,
            "side": "SLD",
            "shares": 10,
            "price": 11,
            "time": "2026-08-21T10:01:00+00:00",
            "ledger_sequence": 2,
        },
    ]
    store.load_outcome = lambda _setup_id: {
        "setup_id": "EXISTING-COMPLETE",
        "complete": True,
        "fill_set_hash": "ledger-fill-set",
    }
    intent = {
        "setup_id": "EXISTING-COMPLETE",
        "order_ref": "AS2-EXISTING-COMPLETE",
        "ticker": "XYZ",
        "account": "DU123",
        "con_id": 123,
        "direction": "LONG",
        "quantity": 10,
        "entry": 10,
        "stop": 9,
        "order_ids": [10, 11],
        "parent_order_ids": [10],
    }
    positions = (
        [
            {
                "account": "DU123",
                "con_id": 123,
                "ticker": "XYZ",
                "quantity": 10,
                "avg_cost": 10,
            }
        ]
        if contradiction == "position"
        else []
    )
    orders = (
        [
            {
                "account": "DU123",
                "con_id": 123,
                "order_id": 10,
                "parent_id": 0,
                "order_ref": "AS2-EXISTING-COMPLETE-P1",
                "status": "Submitted",
            }
        ]
        if contradiction == "parent"
        else []
    )

    result = pa._record_ledger_outcome(
        store,
        intent,
        positions,
        orders,
        orders_snapshot_complete=True,
        snapshot_observed_at=datetime.now(timezone.utc),
    )

    assert result["accepted"] is False
    assert result["conflict"] == "complete_terminal_snapshot_contradiction"
    assert result["evidence_reliable"] is False
    assert store.outcomes == []


def test_complete_outcome_rejects_an_open_order_without_a_positive_id():
    store = _RecordingRiskStore()
    store.appended_fills = [
        {
            "exec_id": "ORDER-ID-E",
            "account": "DU123",
            "con_id": 123,
            "order_id": 10,
            "side": "BOT",
            "shares": 10.0,
            "price": 10.0,
            "time": "2026-08-21T10:00:00+00:00",
            "ledger_sequence": 1,
        },
        {
            "exec_id": "ORDER-ID-X",
            "account": "DU123",
            "con_id": 123,
            "order_id": 11,
            "side": "SLD",
            "shares": 10.0,
            "price": 11.0,
            "time": "2026-08-21T10:01:00+00:00",
            "ledger_sequence": 2,
        },
    ]
    intent = {
        "setup_id": "INVALID-OPEN-ID",
        "order_ref": "AS2-INVALID-OPEN-ID",
        "ticker": "XYZ",
        "account": "DU123",
        "con_id": 123,
        "direction": "LONG",
        "quantity": 10,
        "entry": 10.0,
        "stop": 9.0,
        "risk_reservation_id": "reservation-INVALID-OPEN-ID",
        "order_ids": [10, 11],
        "parent_order_ids": [10],
    }

    result = pa._record_ledger_outcome(
        store,
        intent,
        [],
        [{"order_id": 0, "status": "Submitted"}],
        orders_snapshot_complete=True,
        snapshot_observed_at=datetime.now(timezone.utc),
    )

    assert result["accepted"] is False
    assert result["conflict"] == "outcome_terminal_evidence_unavailable"
    assert result["evidence_reliable"] is False
    assert "terminal_snapshot_invalid" in result["evidence_unresolved_codes"]
    assert store.outcomes == []
    assert "acquire_lease" not in store.calls


@pytest.mark.parametrize(
    ("order_ref_suffix", "field", "bad_value"),
    [
        ("-P1", "action", "SELL"),
        ("-P1", "orderType", "MKT"),
        ("-P1", "totalQuantity", 999),
        ("-P1", "auxPrice", 10.50),
        ("-P1", "lmtPrice", 11.00),
        ("-S1", "action", "BUY"),
        ("-S1", "auxPrice", 8.00),
        ("-S1", "ocaGroup", "WRONG-OCA"),
        ("-S1", "ocaType", 2),
        ("-S1", "tif", "DAY"),
        ("-S1", "transmit", True),
        ("-T1", "lmtPrice", 99.00),
    ],
)
def test_broker_visibility_rejects_any_mutated_authorized_order_field(
    monkeypatch,
    isolated_autotrader_store,
    order_ref_suffix,
    field,
    bad_value,
):
    """A broker echo with the right identity but wrong protection is not success."""

    class _MutatingIB(_OrderIB):
        def placeOrder(self, contract, order):
            trade = super().placeOrder(contract, order)
            if order.orderRef.endswith(order_ref_suffix):
                setattr(trade.order, field, bad_value)
            return trade

    ib = _MutatingIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert ib.cancelled
    assert store.reconcile_required
    assert not store.visible


def test_broker_visibility_rejects_an_unexpected_order_in_the_setup_namespace(
    monkeypatch, isolated_autotrader_store
):
    class _ExtraSetupOrderIB(_OrderIB):
        def openTrades(self):
            trades = list(self.placed)
            if not trades:
                return trades
            parent = trades[0]
            extra = _FakeOrder(
                action="BUY",
                orderType="LMT",
                lmtPrice=10.0,
                totalQuantity=1,
                parentId=0,
                account="DU123",
                orderRef=parent.order.orderRef.rsplit("-", 1)[0] + "-X9",
                ocaGroup="",
                ocaType=0,
                tif="DAY",
                transmit=True,
                orderId=999,
            )
            trades.append(
                SimpleNamespace(
                    contract=parent.contract,
                    order=extra,
                    orderStatus=SimpleNamespace(
                        status="Submitted", filled=0, remaining=1, avgFillPrice=0
                    ),
                )
            )
            return trades

    ib = _ExtraSetupOrderIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert ib.cancelled
    assert store.reconcile_required
    assert not store.visible


def test_open_trades_failure_cannot_finalize_balanced_partial_fills(
    monkeypatch, isolated_autotrader_store
):
    pa.config_save({"selected_account": "DU123"})
    pa.state_write(
        {
            "status": "running",
            "intents": [
                {
                    "setup_id": "PARTIAL-SNAPSHOT",
                    "order_ref": "AS2-PARTIAL-SNAPSHOT",
                    "ticker": "XYZ",
                    "account": "DU123",
                    "con_id": 123,
                    "direction": "LONG",
                    "quantity": 10,
                    "entry": 10.0,
                    "stop": 9.0,
                    "group_key": "TECH",
                    "group_verified": True,
                    "status": "WORKING",
                    "order_ids": [10, 11],
                    "parent_order_ids": [10],
                }
            ],
        }
    )
    store = _RecordingRiskStore()
    store.appended_fills = [
        {
            "exec_id": "PARTIAL-E",
            "account": "DU123",
            "con_id": 123,
            "order_id": 10,
            "side": "BOT",
            "shares": 5.0,
            "price": 10.0,
            "time": "2026-08-21T10:00:00+00:00",
            "ledger_sequence": 1,
        },
        {
            "exec_id": "PARTIAL-X",
            "account": "DU123",
            "con_id": 123,
            "order_id": 11,
            "side": "SLD",
            "shares": 5.0,
            "price": 10.2,
            "time": "2026-08-21T10:01:00+00:00",
            "ledger_sequence": 2,
        },
    ]

    class _BrokenOrdersIB(_ReconcileIB):
        def openTrades(self):
            raise RuntimeError("open order snapshot unavailable")

    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": _BrokenOrdersIB()})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    assert store.outcomes[-1]["complete"] is False
    assert store.outcomes[-1]["_parent_orders_terminal"] is False
    assert "acquire_lease" not in store.calls
    assert "terminal_evidence" not in store.outcomes[-1]["_record_kwargs"]
    assert state["risk_evidence_unreliable"] is True
    assert pa.account_gate(state, pa.config_load())["allowed"] is False


def test_open_trades_failure_preserves_a_legacy_intent_without_status(
    monkeypatch, isolated_autotrader_store
):
    pa.config_save({"selected_account": "DU123"})
    pa.state_write(
        {
            "status": "running",
            "intents": [
                {
                    "setup_id": "LEGACY-NO-STATUS",
                    "order_ref": "AS2-LEGACY-NO-STATUS",
                    "ticker": "XYZ",
                }
            ],
        }
    )

    class _BrokenOrdersIB(_ReconcileIB):
        def openTrades(self):
            raise RuntimeError("open order snapshot unavailable")

    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": _BrokenOrdersIB()})

    state = pa.reconcile_broker()

    assert state["intents"][0].get("status") is None
    assert state["risk_evidence_unreliable"] is True


def test_open_trades_failure_preserves_the_last_known_orders_as_stale_evidence(
    monkeypatch, isolated_autotrader_store
):
    pa.config_save({"selected_account": "DU123"})
    last_known = {
        "ticker": "XYZ",
        "con_id": 123,
        "order_id": 77,
        "parent_id": 0,
        "order_ref": "AS2-LAST-P1",
        "account": "DU123",
        "action": "BUY",
        "order_type": "STP LMT",
        "quantity": 10,
        "status": "Submitted",
    }
    pa.state_write(
        {"status": "running", "intents": [], "open_orders": [last_known]}
    )

    class _BrokenOrdersIB(_ReconcileIB):
        def openTrades(self):
            raise RuntimeError("open order snapshot unavailable")

    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": _BrokenOrdersIB()})

    state = pa.reconcile_broker()

    assert state["open_orders"] == [last_known]
    assert state["orders_snapshot_complete"] is False


def test_tick_rounding_cannot_reduce_rr_below_the_configured_floor(
    monkeypatch, isolated_autotrader_store
):
    ib = _OrderIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)
    pa._config_save_internal(
        {
            **_armed_submission_config(),
            "min_rr": 2.0,
            "max_total_exposure_pct": 50.0,
        },
        allow_execution_state=True,
    )

    result = pa.submit_signal(
        {
            "ticker": "XYZ",
            "direction": "LONG",
            "trigger_bar_date": "2026-08-21",
            "entry": 10.00,
            "stop": 9.97,
            "tp1": 10.045,
            "tp2": 10.075,
        }
    )

    assert result["success"] is False
    assert result["submitted"] is False
    assert "R:R" in result["error"]
    assert ib.placed == []


def test_submission_renews_the_fence_before_each_broker_write_and_transition(
    monkeypatch, isolated_autotrader_store
):
    events = []

    class _LeaseAwareIB(_OrderIB):
        def placeOrder(self, contract, order):
            events.append("place_order")
            return super().placeOrder(contract, order)

    class _LeaseAwareStore(_RecordingRiskStore):
        def renew_lease(self, *args, **kwargs):
            events.append("renew")
            return super().renew_lease(*args, **kwargs)

        def register_intent_order(self, setup_id, mapping, **kwargs):
            events.append("register_mapping")
            return super().register_intent_order(setup_id, mapping, **kwargs)

        def mark_reservation_broker_visible(self, reservation_id, order_ids, **kwargs):
            events.append("broker_visible")
            return super().mark_reservation_broker_visible(
                reservation_id, order_ids, **kwargs
            )

    ib = _LeaseAwareIB()
    store = _LeaseAwareStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["submitted"] is True
    guarded = {"place_order", "register_mapping", "broker_visible"}
    for index, event in enumerate(events):
        if event in guarded:
            assert index > 0 and events[index - 1] == "renew", events


def test_fence_loss_stops_placement_and_uses_a_new_recovery_lease(
    monkeypatch, isolated_autotrader_store
):
    events = []

    class _FenceLossIB(_OrderIB):
        def placeOrder(self, contract, order):
            events.append("place_order")
            return super().placeOrder(contract, order)

        def cancelOrder(self, order):
            events.append("cancel_order")
            return super().cancelOrder(order)

    class _FenceLossStore(_RecordingRiskStore):
        def __init__(self):
            super().__init__()
            self.acquire_count = 0
            self.primary_renewals = 0

        def acquire_lease(self, lease_key, owner_token, *, now, ttl_seconds):
            self.acquire_count += 1
            self.calls.append("acquire_lease")
            return {"acquired": True, "fence_token": self.acquire_count}

        def renew_lease(
            self, lease_key, owner_token, fence_token, *, now, ttl_seconds
        ):
            events.append("renew_recovery" if fence_token == 2 else "renew_primary")
            if fence_token == 1:
                self.primary_renewals += 1
                if self.primary_renewals == 3:
                    return {"renewed": False, "reason": "lease_fenced"}
            return {"renewed": True, "fence_token": fence_token}

        def mark_reservation_reconcile_required(self, reservation_id, **kwargs):
            assert kwargs["fence_token"] == 2
            events.append("reconcile_required")
            return super().mark_reservation_reconcile_required(
                reservation_id, **kwargs
            )

    ib = _FenceLossIB()
    store = _FenceLossStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["success"] is False
    assert result["submitted"] is False
    assert len(ib.placed) == 1
    assert ib.cancelled == [ib.placed[0].order]
    assert store.acquire_count == 2
    assert store.reconcile_required
    assert events[events.index("cancel_order") - 1] == "renew_recovery"
    assert events[events.index("reconcile_required") - 1] == "renew_recovery"


def test_invalid_con_id_fill_is_durably_rejected_and_blocks_submission_gate(
    monkeypatch, isolated_autotrader_store
):
    class _RejectingFillStore(_RecordingRiskStore):
        def append_fill(self, fill):
            self.appended_fills.append(dict(fill))
            return {
                "accepted": False,
                "idempotent": False,
                "conflict": "fill_invalid",
                "persisted": True,
            }

    pa.config_save({"selected_account": "DU123"})
    fill = _fill(10, "BOT", 10)
    fill.contract.conId = 0
    ib = _ReconcileIB(fills=[fill])
    store = _RejectingFillStore()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    assert store.appended_fills[0]["con_id"] == 0
    assert state["risk_evidence_unreliable"] is True
    assert pa.account_gate(state, pa.config_load())["allowed"] is False


def test_mapping_pending_fill_marks_runtime_evidence_unreliable(
    monkeypatch, isolated_autotrader_store
):
    class _PendingFillStore(_RecordingRiskStore):
        def append_fill(self, fill):
            self.appended_fills.append(dict(fill))
            return {
                "accepted": True,
                "idempotent": False,
                "conflict": None,
                "persisted": True,
                "mapping_pending": True,
            }

    pa.config_save({"selected_account": "DU123"})
    fill = _fill(10, "BOT", 10)
    fill.contract.conId = 123
    store = _PendingFillStore()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(
        pa, "_get_ib_state", lambda: {"ib": _ReconcileIB(fills=[fill])}
    )
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    assert state["risk_evidence_unreliable"] is True
    assert any("mapping" in error.lower() for error in state["risk_evidence_errors"])


def test_fill_ledger_exception_marks_evidence_unreliable(
    monkeypatch, isolated_autotrader_store
):
    class _BrokenFillStore(_RecordingRiskStore):
        def append_fill(self, fill):
            raise RuntimeError("sqlite unavailable")

    pa.config_save({"selected_account": "DU123"})
    fill = _fill(10, "BOT", 10)
    fill.contract.conId = 123
    ib = _ReconcileIB(fills=[fill])
    store = _BrokenFillStore()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    assert state["risk_evidence_unreliable"] is True
    assert any("append" in error.lower() for error in state["risk_evidence_errors"])
    assert pa.account_gate(state, pa.config_load())["allowed"] is False


def test_rejected_ledger_outcome_marks_evidence_unreliable(
    monkeypatch, isolated_autotrader_store
):
    class _RejectingOutcomeStore(_RecordingRiskStore):
        def record_outcome(self, outcome, **kwargs):
            super().record_outcome(outcome, **kwargs)
            return {
                "accepted": False,
                "idempotent": False,
                "conflict": "outcome_derived_mismatch",
                "transition": "rejected",
            }

    pa.config_save({"selected_account": "DU123"})
    pa.state_write(
        {
            "status": "running",
            "intents": [
                {
                    "setup_id": "REJECTED-OUTCOME",
                    "order_ref": "AS2-REJECTED-OUTCOME",
                    "ticker": "XYZ",
                    "account": "DU123",
                    "con_id": 123,
                    "direction": "LONG",
                    "quantity": 10,
                    "entry": 10.0,
                    "stop": 9.0,
                    "status": "WORKING",
                    "risk_reservation_id": "reservation-REJECTED-OUTCOME",
                    "order_ids": [10, 11],
                    "parent_order_ids": [10],
                }
            ],
        }
    )
    store = _RejectingOutcomeStore()
    store.appended_fills = [
        {
            "exec_id": "E",
            "account": "DU123",
            "con_id": 123,
            "order_id": 10,
            "side": "BOT",
            "shares": 10.0,
            "price": 10.0,
            "time": "2026-08-21T10:00:00+00:00",
            "ledger_sequence": 1,
        },
        {
            "exec_id": "X",
            "account": "DU123",
            "con_id": 123,
            "order_id": 11,
            "side": "SLD",
            "shares": 10.0,
            "price": 11.0,
            "time": "2026-08-21T10:01:00+00:00",
            "ledger_sequence": 2,
        },
    ]
    ib = _ReconcileIB()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    assert state["risk_evidence_unreliable"] is True
    assert any("outcome" in error.lower() for error in state["risk_evidence_errors"])
    assert pa.account_gate(state, pa.config_load())["allowed"] is False


def test_persisted_unreliable_fill_evidence_cannot_disappear_from_runtime_state(
    monkeypatch, isolated_autotrader_store
):
    class _UnreliableEvidenceStore(_RecordingRiskStore):
        def fill_evidence(self, _setup_id):
            return {
                "fills": [],
                "reliable": False,
                "unresolved_codes": ["fill_exec_conflict"],
                "conflicting_events": [{"exec_id": "CONFLICT"}],
                "fill_set_hash": None,
            }

    pa.config_save({"selected_account": "DU123"})
    pa.state_write(
        {
            "status": "running",
            "intents": [
                {
                    "setup_id": "PERSISTED-CONFLICT",
                    "order_ref": "AS2-PERSISTED-CONFLICT",
                    "ticker": "XYZ",
                    "account": "DU123",
                    "con_id": 123,
                    "direction": "LONG",
                    "quantity": 10,
                    "entry": 10.0,
                    "stop": 9.0,
                    "status": "WORKING",
                    "order_ids": [10, 11],
                    "parent_order_ids": [10],
                }
            ],
        }
    )
    store = _UnreliableEvidenceStore()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": _ReconcileIB()})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    assert state["risk_evidence_unreliable"] is True
    assert any("fill_exec_conflict" in error for error in state["risk_evidence_errors"])


def test_submission_persists_the_complete_immutable_restart_plan(
    monkeypatch, isolated_autotrader_store
):
    ib = _OrderIB()
    store = _RecordingRiskStore()
    _configure_risk_submission(monkeypatch, isolated_autotrader_store, store, ib)

    result = pa.submit_signal(_submission_signal())

    assert result["submitted"] is True
    intent = store.intents[0]
    assert intent["tp1"] == pytest.approx(result["levels"]["tp1"])
    assert intent["tp2"] == pytest.approx(result["levels"]["tp2"])
    assert intent["stop_limit"] == pytest.approx(10.05)
    assert intent["allocations"] == [227, 227]


def _restart_intent(setup_id="RESTART"):
    return {
        "setup_id": setup_id,
        "order_ref": f"AS2-{setup_id}",
        "account": "DU123",
        "con_id": 123,
        "direction": "LONG",
        "quantity": 10,
        "entry": 10.0,
        "stop": 9.5,
        "tp1": 11.0,
        "tp2": 12.0,
        "stop_limit": 10.05,
        "allocations": [5, 5],
        "ticker": "XYZ",
        "group_key": "TECH",
        "group_verified": True,
    }


def _restart_trades(intent):
    contract = SimpleNamespace(symbol="XYZ", conId=123, secType="STK", currency="USD")
    trades = []
    next_id = 1
    for branch, (quantity, target) in enumerate(
        zip(intent["allocations"], (intent["tp1"], intent["tp2"])), start=1
    ):
        parent_id = next_id
        specs = [
            _FakeOrder(
                action="BUY",
                orderType="STP LMT",
                auxPrice=intent["entry"],
                lmtPrice=intent["stop_limit"],
                totalQuantity=quantity,
                parentId=0,
                account="DU123",
                orderRef=f"{intent['order_ref']}-P{branch}",
                ocaGroup="",
                ocaType=0,
                tif="DAY",
                transmit=False,
                orderId=next_id,
            ),
            _FakeOrder(
                action="SELL",
                orderType="STP",
                auxPrice=intent["stop"],
                totalQuantity=quantity,
                parentId=parent_id,
                account="DU123",
                orderRef=f"{intent['order_ref']}-S{branch}",
                ocaGroup=f"{intent['order_ref']}-O{branch}",
                ocaType=1,
                tif="GTC",
                transmit=False,
                orderId=next_id + 1,
            ),
            _FakeOrder(
                action="SELL",
                orderType="LMT",
                lmtPrice=target,
                totalQuantity=quantity,
                parentId=parent_id,
                account="DU123",
                orderRef=f"{intent['order_ref']}-T{branch}",
                ocaGroup=f"{intent['order_ref']}-O{branch}",
                ocaType=1,
                tif="GTC",
                transmit=True,
                orderId=next_id + 2,
            ),
        ]
        for order in specs:
            order.permId = 20_000 + order.orderId
            order.clientId = 7
            order.outsideRth = False
            trades.append(
                SimpleNamespace(
                    contract=contract,
                    order=order,
                    orderStatus=SimpleNamespace(
                        status="Submitted",
                        filled=0,
                        remaining=order.totalQuantity,
                        avgFillPrice=0,
                    ),
                )
            )
        next_id += 3
    return trades


def _prepare_restart_reservation(store, intent):
    assert store.register_intent(intent)["accepted"] is True
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    lease = store.acquire_lease(
        f"submit:{intent['setup_id']}", "crashed-worker", now=old, ttl_seconds=1
    )
    reservation = {
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
    admitted = store.reserve_if_allowed(
        reservation,
        net_liquidation=100_000.0,
        available_funds=100_000.0,
        min_available_funds=0.0,
        positions=[],
        orders=[],
        policy=pa.DEFAULT_RISK_POLICY,
        gross_position_value=0.0,
        orders_snapshot_complete=True,
        max_total_exposure_pct=20.0,
        max_positions=3,
        now=old,
        lease_key=f"submit:{intent['setup_id']}",
        owner_token="crashed-worker",
        fence_token=lease["fence_token"],
    )
    assert admitted["allowed"] is True


def _run_restart_reconcile(monkeypatch, store, trades, *, state_intents=None):
    pa.config_save({"selected_account": "DU123"})
    pa.state_write({"status": "running", "intents": list(state_intents or [])})
    ib = _ReconcileIB(open_trades=trades)
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": ib})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)
    return pa.reconcile_broker()


def test_periodic_reconcile_observes_terminal_child_and_preserves_terminal_status(
    monkeypatch, isolated_autotrader_store
):
    intent = {
        **_restart_intent("TERMINAL-CHILD-OBSERVED"),
        "status": "TERMINAL",
        "order_ids": [1, 2, 3, 4, 5, 6],
        "parent_order_ids": [1, 4],
    }

    class _TerminalConflictStore(_RecordingRiskStore):
        def observe_open_orders(self, orders, **kwargs):
            super().observe_open_orders(orders, **kwargs)
            return {
                "accepted": True,
                "conflicts": [
                    {
                        "setup_id": intent["setup_id"],
                        "conflict": "terminal_child_identity_mismatch_after_release",
                    }
                ],
                "reason": None,
            }

    store = _TerminalConflictStore()

    state = _run_restart_reconcile(
        monkeypatch,
        store,
        _restart_trades(intent),
        state_intents=[intent],
    )

    assert len(store.order_observations) == 1
    assert store.order_observations[0]["snapshot_complete"] is True
    assert state["intents"][0]["status"] == "TERMINAL"
    assert "terminal_child_identity_mismatch_after_release" in state["intents"][0][
        "risk_evidence_error"
    ]
    assert state["risk_evidence_unreliable"] is True
    assert store.outcomes == []


def test_rejected_order_observation_prevents_local_status_mutation(
    monkeypatch, isolated_autotrader_store
):
    intent = {
        **_restart_intent("REJECTED-ORDER-OBSERVATION"),
        "status": "SUBMITTING",
        "order_ids": [1, 2, 3, 4, 5, 6],
        "parent_order_ids": [1, 4],
    }

    class _RejectedObservationStore(_RecordingRiskStore):
        def observe_open_orders(self, orders, **kwargs):
            super().observe_open_orders(orders, **kwargs)
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "unknown_broker_order",
            }

    store = _RejectedObservationStore()

    state = _run_restart_reconcile(
        monkeypatch,
        store,
        [],
        state_intents=[intent],
    )

    assert len(store.order_observations) == 1
    assert state["intents"][0]["status"] == "SUBMITTING"
    assert state["risk_evidence_unreliable"] is True


def test_restart_recovery_reconstructs_all_missing_mappings_under_a_new_fence(
    monkeypatch, isolated_autotrader_store
):
    store = pa._risk_store()
    intent = _restart_intent()
    _prepare_restart_reservation(store, intent)

    state = _run_restart_reconcile(monkeypatch, store, _restart_trades(intent))

    assert store.intent_order_ids(intent["setup_id"]) == [1, 2, 3, 4, 5, 6]
    assert store.active_reservations()[0]["status"] == "BROKER_VISIBLE"
    assert state["risk_evidence_unreliable"] is False
    assert len(state["intents"]) == 1
    assert state["intents"][0]["setup_id"] == intent["setup_id"]
    assert state["intents"][0]["ticker"] == "XYZ"
    assert state["intents"][0]["order_ids"] == [1, 2, 3, 4, 5, 6]
    assert state["intents"][0]["parent_order_ids"] == [1, 4]


def test_durable_restart_intent_can_later_finalize_and_release_its_reservation(
    monkeypatch, isolated_autotrader_store
):
    class _ReconcileClock(datetime):
        current = datetime(2099, 8, 21, 12, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            value = cls.fromtimestamp(
                cls.current.timestamp(), tz or timezone.utc
            )
            if tz is None:
                return value.replace(tzinfo=None)
            return value

    monkeypatch.setattr(pa, "datetime", _ReconcileClock)
    store = pa._risk_store()
    intent = _restart_intent("RESTART-OUTCOME")
    _prepare_restart_reservation(store, intent)
    _run_restart_reconcile(monkeypatch, store, _restart_trades(intent))
    fills = []
    for order_id, side, price in (
        (1, "BOT", 10.0),
        (4, "BOT", 10.0),
        (3, "SLD", 11.0),
        (6, "SLD", 12.0),
    ):
        fill = _fill(order_id, side, price)
        fill.contract.conId = 123
        fill.execution.shares = 5
        fill.execution.permId = 20_000 + order_id
        fills.append(fill)
    monkeypatch.setattr(
        pa, "_get_ib_state", lambda: {"ib": _ReconcileIB(fills=fills)}
    )

    held_state = pa.reconcile_broker()

    assert held_state["risk_evidence_unreliable"] is True
    assert store.load_outcome(intent["setup_id"])["complete"] is False
    assert store.active_reservations()

    _ReconcileClock.current = datetime(
        2099, 8, 21, 12, 1, 1, tzinfo=timezone.utc
    )
    state = pa.reconcile_broker()

    assert state["intents"][0]["status"] == "TERMINAL"
    assert state["risk_evidence_unreliable"] is False, state[
        "risk_evidence_errors"
    ]
    assert store.load_outcome(intent["setup_id"])["complete"] is True
    assert store.active_reservations() == []

    replay_state = pa.reconcile_broker()

    assert replay_state["risk_evidence_unreliable"] is False, replay_state[
        "risk_evidence_errors"
    ]
    assert store.load_outcome(intent["setup_id"])["complete"] is True
    assert store.active_reservations() == []


def test_restart_recovery_restores_local_order_ids_for_outcome_reconciliation(
    monkeypatch, isolated_autotrader_store
):
    store = pa._risk_store()
    intent = _restart_intent("RESTART-LOCAL")
    _prepare_restart_reservation(store, intent)
    local_intent = {
        **intent,
        "ticker": "XYZ",
        "status": "SUBMITTING",
        "order_ids": [],
        "parent_order_ids": [],
    }

    state = _run_restart_reconcile(
        monkeypatch,
        store,
        _restart_trades(intent),
        state_intents=[local_intent],
    )

    restored = state["intents"][0]
    assert restored["status"] == "WORKING"
    assert restored["order_ids"] == [1, 2, 3, 4, 5, 6]
    assert restored["parent_order_ids"] == [1, 4]
    assert restored["risk_reservation_status"] == "BROKER_VISIBLE"


def test_restart_recovery_maps_visible_partial_orders_but_stays_reconcile_required(
    monkeypatch, isolated_autotrader_store
):
    store = pa._risk_store()
    intent = _restart_intent("RESTART-PARTIAL")
    _prepare_restart_reservation(store, intent)
    trades = _restart_trades(intent)[:1]

    local_intent = {
        **intent,
        "ticker": "XYZ",
        "status": "SUBMITTING",
        "order_ids": [],
        "parent_order_ids": [],
    }
    state = _run_restart_reconcile(
        monkeypatch, store, trades, state_intents=[local_intent]
    )

    assert store.intent_order_ids(intent["setup_id"]) == [1]
    assert store.active_reservations()[0]["status"] == "RECONCILE_REQUIRED"
    assert state["risk_evidence_unreliable"] is True
    assert state["intents"][0]["status"] == "RECONCILE_REQUIRED"
    assert state["intents"][0]["order_ids"] == [1]
    assert state["intents"][0]["parent_order_ids"] == [1]


def test_restart_recovery_rejects_ambiguous_order_refs_without_mapping_them(
    monkeypatch, isolated_autotrader_store
):
    store = pa._risk_store()
    intent = _restart_intent("RESTART-AMBIGUOUS")
    _prepare_restart_reservation(store, intent)
    trades = _restart_trades(intent)
    duplicate = SimpleNamespace(
        contract=trades[1].contract,
        order=_FakeOrder(**vars(trades[1].order)),
        orderStatus=trades[1].orderStatus,
    )

    state = _run_restart_reconcile(monkeypatch, store, [*trades, duplicate])

    assert store.intent_order_ids(intent["setup_id"]) == []
    assert store.active_reservations()[0]["status"] == "RECONCILE_REQUIRED"
    assert state["risk_evidence_unreliable"] is True
    assert pa.account_gate(state, pa.config_load())["allowed"] is False


def test_restart_recovery_rejects_a_persisted_mapping_conflict():
    intent = _restart_intent("RESTART-MAPPING-CONFLICT")

    class _ConflictingRestartStore(_RecordingRiskStore):
        def active_reservations(self):
            return [
                {
                    "reservation_id": "reservation-RESTART-MAPPING-CONFLICT",
                    "setup_id": intent["setup_id"],
                    "account": "DU123",
                    "status": "SUBMITTING",
                }
            ]

        def load_intent(self, setup_id):
            assert setup_id == intent["setup_id"]
            return dict(intent)

        def intent_order_ids(self, setup_id):
            assert setup_id == intent["setup_id"]
            return []

        def register_intent_order(self, setup_id, mapping):
            self.calls.append("register_intent_order")
            self.mappings.append((setup_id, dict(mapping)))
            return {
                "accepted": True,
                "idempotent": False,
                "conflict": "fill_seen_after_release",
            }

    store = _ConflictingRestartStore()
    local_intents = []

    errors = pa._recover_restart_mappings(
        store,
        _restart_trades(intent),
        "DU123",
        local_intents,
    )

    assert len(store.mappings) == 1
    assert store.reconcile_required
    assert not store.visible
    assert "fill_seen_after_release" in errors[0]
    assert local_intents[0]["status"] == "RECONCILE_REQUIRED"


@pytest.mark.parametrize("provider", ["positions", "openTrades", "fills"])
@pytest.mark.parametrize("failure_mode", ["none", "non_iterable", "conversion"])
def test_broker_provider_protocol_failures_are_independently_fail_closed(
    monkeypatch, isolated_autotrader_store, provider, failure_mode
):
    class _ProtocolIB(_ReconcileIB):
        def _broken(self):
            if failure_mode == "none":
                return None
            if failure_mode == "non_iterable":
                return 7
            if provider == "positions":
                return [SimpleNamespace(
                    account="DU123", contract=SimpleNamespace(symbol="XYZ", conId="bad"),
                    position=1, avgCost=10,
                )]
            if provider == "openTrades":
                return [SimpleNamespace(
                    contract=SimpleNamespace(symbol="XYZ", conId=123),
                    order=SimpleNamespace(account="DU123", orderId="bad"),
                    orderStatus=SimpleNamespace(status="Submitted", filled=0, remaining=1),
                )]
            bad_fill = _fill(1, "BOT", 10)
            bad_fill.contract.conId = "bad"
            return [bad_fill]

        def positions(self, _account=None):
            return self._broken() if provider == "positions" else []

        def openTrades(self):
            return self._broken() if provider == "openTrades" else []

        def fills(self):
            return self._broken() if provider == "fills" else []

    intent = {
        "setup_id": "PROVIDER-FAIL", "order_ref": "AS2-PROVIDER-FAIL",
        "ticker": "XYZ", "status": "SUBMITTING", "order_ids": [1],
        "parent_order_ids": [1],
    }
    pa.config_save({"selected_account": "DU123"})
    pa.state_write({"status": "running", "intents": [intent]})
    store = _RecordingRiskStore()
    monkeypatch.setattr(pa, "ib_is_connected", lambda: True)
    monkeypatch.setattr(pa, "_get_ib_state", lambda: {"ib": _ProtocolIB()})
    monkeypatch.setattr(pa, "_risk_store", lambda: store, raising=False)

    state = pa.reconcile_broker()

    flag = {
        "positions": "positions_snapshot_complete",
        "openTrades": "orders_snapshot_complete",
        "fills": "fills_snapshot_complete",
    }[provider]
    assert state[flag] is False
    assert state["risk_evidence_unreliable"] is True
    assert state["intents"][0]["status"] == "SUBMITTING"
    assert store.outcomes
    assert all(outcome.get("complete") is False for outcome in store.outcomes)
    assert all(
        "terminal_evidence" not in outcome.get("_record_kwargs", {})
        for outcome in store.outcomes
    )


def test_legacy_state_missing_any_snapshot_completeness_flag_fails_account_gate():
    for missing in (
        "positions_snapshot_complete", "orders_snapshot_complete", "fills_snapshot_complete"
    ):
        state = _armed_submission_state()
        state.update({"broker_connected": True, "risk_evidence_unreliable": False})
        state.pop(missing)
        result = pa.account_gate(state, {**pa.DEFAULT_CONFIG, **_armed_submission_config()})
        assert result["allowed"] is False, missing


def test_trade_serialization_exposes_full_broker_geometry():
    order = _FakeOrder(
        orderId=1, permId=91, clientId=7, parentId=0, orderRef="AS2-SERIAL-P1",
        account="DU123", action="BUY", orderType="STP LMT", totalQuantity=5,
        auxPrice=10.0, lmtPrice=9.9, ocaGroup="", ocaType=0, tif="DAY",
        transmit=False, outsideRth=False,
    )
    trade = SimpleNamespace(
        contract=SimpleNamespace(symbol="XYZ", conId=123), order=order,
        orderStatus=SimpleNamespace(status="Submitted", filled=1, remaining=4, avgFillPrice=10),
    )
    serialized = pa._serialize_trade(trade)
    assert {key: serialized[key] for key in (
        "client_id", "aux_price", "oca_group", "oca_type", "tif", "transmit",
        "outside_rth",
    )} == {
        "client_id": 7, "aux_price": 10.0, "oca_group": "", "oca_type": 0,
        "tif": "DAY", "transmit": False, "outside_rth": False,
    }
