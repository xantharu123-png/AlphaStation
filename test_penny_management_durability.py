"""Penny model-event durability and concurrent-entry acceptance regressions."""
import copy
from types import SimpleNamespace

import pytest


def _isolate(api, monkeypatch, tmp_path, now):
    for name in ("PENNY_STOCKS_MONITOR_CACHE", "PENNY_STOCKS_STATE",
                 "PENNY_STOCKS_REFERENCE_CACHE", "PENNY_STOCKS_DAILY_CACHE",
                 "PENNY_STOCKS_NEWS_CACHE", "PENNY_STOCKS_SEC_CACHE",
                 "PENNY_STOCKS_TRIGGER_POOL_CACHE", "_EMAIL_DEDUPE_FILE"):
        monkeypatch.setattr(api, name, str(tmp_path / (name + ".json")))
    monkeypatch.setattr(api.time, "time", lambda: now[0])


def _position(now):
    return dict(active=True, position_state_source="scanner_model_not_broker",
        position_event_id="OPEN:original", last_seen=now-300, buy_entry=1.,
        active_stop=.95, tp1_realized=False, remaining_fraction=1.,
        trade_setup=dict(entry=1.,stop_loss=.95,tp1=1.1,tp2=1.18))


@pytest.mark.parametrize("price,kind", [(.94,"EXIT"),(1.11,"TP1_PARTIAL")])
def test_later_symbol_failure_preserves_already_observed_model_transition(monkeypatch,tmp_path,price,kind):
    import api
    from test_penny_stock_scanner import _market_now,_bars,_snapshot,_details
    now = [_market_now()]
    _isolate(api,monkeypatch,tmp_path,now)
    monkeypatch.setattr(api,"_scan_status",{"penny_positions":{}})
    monkeypatch.setattr(api,"_load_common_stock_universe",lambda **kwargs: ({"OPEN","ZZZ"},"audit"))
    monkeypatch.setattr(api,"rate_limited_get",lambda *args,**kwargs: SimpleNamespace(
        status_code=200,json=lambda: {"tickers":[{"ticker":"OPEN"},{"ticker":"ZZZ"}]}))
    monkeypatch.setattr(api,"_penny_normalize_snapshot",lambda raw,*args: {
        **_snapshot(),"ticker":raw["ticker"],"price":price,"bid":price,"ask":price+.002})
    monkeypatch.setattr(api,"_stock_trade_email_status",lambda: {"allowed":True,"session":"US_REGULAR"})
    def bars(symbol,*args,**kwargs):
        if symbol == "ZZZ":
            raise RuntimeError("later symbol provider failure")
        return _bars(now[0])
    monkeypatch.setattr(api,"_fetch_recent_stock_5m_bars",bars)
    monkeypatch.setattr(api,"get_ticker_details",lambda *args,**kwargs: _details())
    monkeypatch.setattr(api,"get_ticker_news",lambda *args,**kwargs: [])
    monkeypatch.setattr(api,"_penny_fetch_sec_filing_context",lambda *args: {"status":"ok","risk_flags":[]})
    monkeypatch.setattr(api,"_penny_fetch_daily_bars",lambda *args: [])
    monkeypatch.setattr(api,"_penny_vrvp_resistances",lambda *args: [])
    monkeypatch.setattr(api,"_penny_apply_robust_rvol",lambda snapshot,*args: snapshot)
    monkeypatch.setattr(api,"_record_suppression_counts",lambda *args,**kwargs: None)
    emitted = []
    evaluate = api.evaluate_penny_candidate
    def observe(*args,**kwargs):
        row = evaluate(*args,**kwargs)
        emitted.append(copy.deepcopy(row))
        return row
    monkeypatch.setattr(api,"evaluate_penny_candidate",observe)
    position = _position(now[0])
    api._penny_save_dict(api.PENNY_STOCKS_STATE,{"tickers":{
        "OPEN":position,"ZZZ":dict(position,position_event_id="ZZZ:original")}})
    with pytest.raises(RuntimeError,match="later symbol provider failure"):
        api._penny_position_monitor_wrapper()
    persisted = api._penny_load_state_tickers()["OPEN"]
    assert emitted[0]["lifecycle"] == kind
    assert persisted["active"] is (kind != "EXIT")
    assert persisted["tp1_realized"] is (kind == "TP1_PARTIAL")
    assert persisted["remaining_fraction"] == (0. if kind == "EXIT" else .5)
    assert len(persisted["model_management_events"]) == 1
    event = next(iter(persisted["model_management_events"].values()))
    assert event["status"] == "pending"
    assert event["observed_at"] == now[0]


@pytest.mark.parametrize("outcome,accepted,expected", [
    ("failed",False,"pending"),("unknown",False,"unknown"),
    ("accepted_unjournaled",False,"unknown"),("accepted",True,"sent"),
    ("partial_unknown",True,"sent")])
def test_durable_dispatch_outcome_controls(monkeypatch,tmp_path,outcome,accepted,expected):
    import api
    now = [1000.]
    _isolate(api,monkeypatch,tmp_path,now)
    state = _position(now[0])
    row = dict(ticker="OPEN",trade_action="JETZT_VERKAUFEN",position_event_id="OPEN:original")
    api._penny_latch_model_management(state,row,now_ts=now[0])
    api._penny_merge_state_tickers({"OPEN":state},now_ts=now[0])
    attempts=[]
    def sender(rows,**kwargs):
        attempts.append(copy.deepcopy(rows))
        api._set_last_delivery_outcome(outcome)
        return accepted
    monkeypatch.setattr(api,"_penny_exit_email",sender)
    api._penny_dispatch_model_management(telemetry_scanner="penny_positions")
    persisted = api._penny_load_state_tickers()["OPEN"]
    assert persisted["active"] is False
    assert persisted["model_exit_confirmed"] is True
    assert persisted["model_management_events"]["OPEN:original:EXIT"]["status"] == expected
    now[0] += 300
    api._penny_dispatch_model_management(telemetry_scanner="penny_positions")
    assert len(attempts) == (2 if expected == "pending" else 1)
    now[0] += 901
    api._penny_dispatch_model_management(telemetry_scanner="penny_positions")
    persisted = api._penny_load_state_tickers()["OPEN"]
    assert persisted["active"] is False
    assert persisted["model_management_events"]["OPEN:original:EXIT"]["status"] == (
        "expired" if expected == "pending" else expected)


def test_late_discovery_entry_cannot_replace_active_monitor_position(monkeypatch,tmp_path):
    import api
    now = [1000.]
    _isolate(api,monkeypatch,tmp_path,now)
    # Monitor already opened a model position while slow discovery was working.
    existing = dict(_position(now[0]),tp1_realized=True,remaining_fraction=.5,active_stop=1.)
    api._penny_save_dict(api.PENNY_STOCKS_STATE,{"tickers":{"OPEN":existing}})
    candidate = dict(ticker="OPEN",entry=1.04,trigger_timestamp=1000,
        position_event_id="OPEN:later-trigger",trade_action="JETZT_KAUFEN",
        trade_setup=dict(entry=1.04,stop_loss=.98,tp1=1.15,tp2=1.25))
    # This is the exact post-merge wrapper sequence after the independent
    # discovery candidate has passed entry validation but not yet been mailed.
    merged = api._penny_merge_state_tickers({"OPEN":dict(active=False,last_seen=1000,
        position_event_id=candidate["position_event_id"],last_action="ENTRY_BESTAETIGT_MAIL_AUSSTEHEND")},now_ts=1000)
    assert merged["OPEN"]["position_event_id"] == "OPEN:original"
    assert api._penny_claim_model_entry(candidate,now_ts=1001) is None
    persisted = api._penny_load_state_tickers()["OPEN"]
    assert persisted["position_event_id"] == "OPEN:original"
    assert persisted["tp1_realized"] is True
    assert persisted["remaining_fraction"] == .5


def _entry(position_id="OPEN:new"):
    return dict(ticker="OPEN",entry=1.04,trigger_timestamp=1000,
        position_event_id=position_id,trade_action="JETZT_KAUFEN",
        trade_setup=dict(entry=1.04,stop_loss=.98,tp1=1.15,tp2=1.25))


def test_competing_entry_claims_have_one_owner(monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    barrier = Barrier(2)
    def claim(index):
        barrier.wait(timeout=5)
        return api._penny_claim_model_entry(_entry(f"OPEN:{index}"),now_ts=1000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(claim,[1,2]))
    assert sum(token is not None for token in tokens) == 1


@pytest.mark.parametrize("notification_sent",[True,False])
def test_reentry_after_exit_uses_new_identity_and_preserves_old_event(monkeypatch,tmp_path,notification_sent):
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    state = _position(1000)
    api._penny_latch_model_management(state,dict(ticker="OPEN",trade_action="JETZT_VERKAUFEN",
        position_event_id="OPEN:original"),now_ts=1000)
    candidate = _entry()
    # Real wrappers first publish a validated, not-yet-activated new candidate.
    api._penny_merge_state_tickers({"OPEN":dict(state,active=False,last_seen=1001,
        position_event_id="OPEN:new",last_action="ENTRY_BESTAETIGT_MAIL_AUSSTEHEND")},now_ts=1001)
    token = api._penny_claim_model_entry(candidate,now_ts=1001)
    assert token
    assert api._penny_finish_model_entry(candidate,token,notification_sent=notification_sent,now_ts=1002)
    persisted = api._penny_load_state_tickers()["OPEN"]
    assert persisted["active"] is True
    assert persisted["position_event_id"] == "OPEN:new"
    assert persisted["remaining_fraction"] == 1.
    assert persisted["entry_notification_sent"] is notification_sent
    assert "OPEN:original:EXIT" in persisted["model_management_events"]
    assert not persisted["model_entry_claim"]


def test_stale_snapshot_cannot_erase_or_restore_entry_claim(monkeypatch,tmp_path):
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    initial = dict(active=False,last_seen=999,model_entry_claim=None)
    api._penny_save_dict(api.PENNY_STOCKS_STATE,{"tickers":{"OPEN":initial}})
    row = _entry()
    token = api._penny_claim_model_entry(row,now_ts=1000)
    assert token
    api._penny_merge_state_tickers({"OPEN":dict(initial,last_seen=1001)},now_ts=1001)
    assert api._penny_load_state_tickers()["OPEN"]["model_entry_claim"] == token
    old = api._penny_load_state_tickers()["OPEN"]
    assert api._penny_finish_model_entry(row,token,notification_sent=False,now_ts=1002)
    api._penny_merge_state_tickers({"OPEN":dict(old,last_seen=1003)},now_ts=1003)
    assert not api._penny_load_state_tickers()["OPEN"]["model_entry_claim"]


def test_old_position_event_cannot_close_new_active_position(monkeypatch,tmp_path):
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    current = dict(_position(1000),position_event_id="OPEN:new")
    api._penny_save_dict(api.PENNY_STOCKS_STATE,{"tickers":{"OPEN":current}})
    stale = _position(1000)
    api._penny_latch_model_management(stale,dict(ticker="OPEN",trade_action="JETZT_VERKAUFEN",
        position_event_id="OPEN:original"),now_ts=1001)
    current = api._penny_load_state_tickers()["OPEN"]
    assert current["active"] is True
    assert current["position_event_id"] == "OPEN:new"
    assert not current.get("model_management_events")


def test_expired_entry_claim_can_be_replaced_but_old_owner_cannot_finalize(monkeypatch,tmp_path):
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    first = _entry("OPEN:first")
    second = _entry("OPEN:second")
    old_token = api._penny_claim_model_entry(first,now_ts=1000)
    assert api._penny_claim_model_entry(second,now_ts=1899) is None
    new_token = api._penny_claim_model_entry(second,now_ts=1900)
    assert new_token and new_token != old_token
    assert api._penny_finish_model_entry(first,old_token,notification_sent=True,now_ts=1901) is False
    assert api._penny_finish_model_entry(second,new_token,notification_sent=False,now_ts=1902) is True
    assert api._penny_load_state_tickers()["OPEN"]["position_event_id"] == "OPEN:second"


def test_concurrent_dispatch_sends_each_durable_event_once(monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    state = _position(1000)
    api._penny_latch_model_management(state,dict(ticker="OPEN",trade_action="JETZT_VERKAUFEN",
        position_event_id="OPEN:original"),now_ts=1000)
    entered,release = Event(),Event()
    calls=[]
    def sender(rows,**kwargs):
        calls.append(copy.deepcopy(rows))
        entered.set()
        assert release.wait(timeout=5)
        api._set_last_delivery_outcome("accepted")
        return True
    monkeypatch.setattr(api,"_penny_exit_email",sender)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(api._penny_dispatch_model_management,telemetry_scanner="penny_stocks")
        assert entered.wait(timeout=5)
        second = pool.submit(api._penny_dispatch_model_management,telemetry_scanner="penny_positions")
        second.result(timeout=5)
        release.set()
        first.result(timeout=5)
    assert len(calls) == 1


def test_old_notification_ack_does_not_mutate_new_position(monkeypatch,tmp_path):
    import api
    _isolate(api,monkeypatch,tmp_path,[1000.])
    state = _position(1000)
    api._penny_latch_model_management(state,dict(ticker="OPEN",trade_action="JETZT_VERKAUFEN",
        position_event_id="OPEN:original"),now_ts=1000)
    def sender(rows,**kwargs):
        new = _entry()
        token = api._penny_claim_model_entry(new,now_ts=1001)
        assert token
        assert api._penny_finish_model_entry(new,token,notification_sent=False,now_ts=1002)
        api._set_last_delivery_outcome("accepted")
        return True
    monkeypatch.setattr(api,"_penny_exit_email",sender)
    api._penny_dispatch_model_management(telemetry_scanner="penny_positions")
    current = api._penny_load_state_tickers()["OPEN"]
    assert current["active"] is True
    assert current["position_event_id"] == "OPEN:new"
    assert current["exit_email_sent"] is False
    assert current["model_management_events"]["OPEN:original:EXIT"]["status"] == "sent"


@pytest.mark.parametrize("old_notification_sent", [False, True])
def test_exit_reentry_then_late_old_hold_cannot_replace_current_owner(monkeypatch,tmp_path,old_notification_sent):
    import api
    now = [1000.]
    _isolate(api,monkeypatch,tmp_path,now)
    old = _position(now[0])
    api._penny_save_dict(api.PENNY_STOCKS_STATE,{"tickers":{"OPEN":old}})
    # Discovery holds an old detached snapshot while the independent monitor
    # observes an exit, then opens a different validated model position.
    late_hold = copy.deepcopy(old)
    api._penny_latch_model_management(old,dict(ticker="OPEN",trade_action="JETZT_VERKAUFEN",
        position_event_id="OPEN:original"),now_ts=1001)
    if old_notification_sent:
        def sender(rows,**kwargs):
            api._set_last_delivery_outcome("accepted")
            return True
        monkeypatch.setattr(api,"_penny_exit_email",sender)
        now[0] = 1001
        api._penny_dispatch_model_management(telemetry_scanner="penny_positions")
    new = _entry()
    new.update(entry=1.2,trade_setup=dict(entry=1.2,stop_loss=1.15,tp1=1.3,tp2=1.4))
    token = api._penny_claim_model_entry(new,now_ts=1002)
    assert token
    assert api._penny_finish_model_entry(new,token,notification_sent=False,now_ts=1003)
    before = api._penny_load_state_tickers()["OPEN"]

    late_hold.update(last_seen=1004,last_action="HALTEN")
    api._penny_latch_model_management(late_hold,dict(ticker="OPEN",trade_action="HALTEN",
        position_event_id="OPEN:original"),now_ts=1004)
    api._penny_merge_state_tickers({"OPEN":late_hold},now_ts=1005)
    current = api._penny_load_state_tickers()["OPEN"]
    assert current == before
    assert current["active"] is True
    assert current["position_event_id"] == "OPEN:new"
    assert current["buy_entry"] == 1.2
    assert current["active_stop"] == 1.15
    assert current["exit_email_sent"] is False
    assert current["model_management_events"]["OPEN:original:EXIT"]["status"] == (
        "sent" if old_notification_sent else "pending")

    # A genuine update of the current owner still advances normally.
    api._penny_merge_state_tickers({"OPEN":dict(current,last_seen=1006,
        last_action="HALTEN",trade_score=72)},now_ts=1006)
    current = api._penny_load_state_tickers()["OPEN"]
    assert current["last_seen"] == 1006
    assert current["trade_score"] == 72
    assert current["position_event_id"] == "OPEN:new"
    assert current["active_stop"] == 1.15
