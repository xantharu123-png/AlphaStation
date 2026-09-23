import copy
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize("first_price,event", [(0.94, "EXIT"), (1.11, "TP1_PARTIAL")])
def test_penny_model_event_survives_smtp_failure_and_next_scan(monkeypatch, tmp_path, first_price, event):
    import api
    from test_penny_stock_scanner import _market_now, _bars, _details, _snapshot

    now = [_market_now()]
    price = [first_price]
    emitted = []
    sent = []
    path_names = ["PENNY_STOCKS_MONITOR_CACHE", "PENNY_STOCKS_STATE",
                  "PENNY_STOCKS_REFERENCE_CACHE", "PENNY_STOCKS_DAILY_CACHE",
                  "PENNY_STOCKS_NEWS_CACHE", "PENNY_STOCKS_SEC_CACHE",
                  "PENNY_STOCKS_TRIGGER_POOL_CACHE"]
    for name in path_names:
        monkeypatch.setattr(api, name, str(tmp_path / (name + ".json")))
    monkeypatch.setattr(api.time, "time", lambda: now[0])
    monkeypatch.setattr(api, "_scan_status", {"penny_positions": {}})
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kwargs: ({"OPEN"}, "audit"))
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, **kwargs: row)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, json=lambda: {"tickers": [{"ticker": "OPEN"}]}))
    monkeypatch.setattr(api, "_penny_normalize_snapshot", lambda *args: {
        **_snapshot(), "ticker": "OPEN", "price": price[0], "bid": price[0], "ask": price[0] + 0.002})
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *args, **kwargs: _bars(now[0]))
    monkeypatch.setattr(api, "get_ticker_details", lambda *args, **kwargs: _details())
    monkeypatch.setattr(api, "get_ticker_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_fetch_sec_filing_context", lambda *args: {"status": "ok", "risk_flags": []})
    monkeypatch.setattr(api, "_penny_fetch_daily_bars", lambda *args: [])
    monkeypatch.setattr(api, "_penny_vrvp_resistances", lambda *args: [])
    monkeypatch.setattr(api, "_penny_apply_robust_rvol", lambda snapshot, *args: snapshot)
    monkeypatch.setattr(api, "_filter_open_equivalent_trade_rows", lambda scanner, rows: (rows, 0))
    monkeypatch.setattr(api, "_email_dedupe_active", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args, **kwargs: None)
    def failed_mail(rows, **kwargs):
        sent.extend(copy.deepcopy(rows))
        return False
    monkeypatch.setattr(api, "_penny_management_email", failed_mail)
    monkeypatch.setattr(api, "_penny_exit_email", failed_mail)
    original_evaluate = api.evaluate_penny_candidate
    def observed(*args, **kwargs):
        row = original_evaluate(*args, **kwargs)
        emitted.append(copy.deepcopy(row))
        return row
    monkeypatch.setattr(api, "evaluate_penny_candidate", observed)
    api._penny_save_dict(api.PENNY_STOCKS_STATE, {"tickers": {"OPEN": {
        "active": True, "position_state_source": "scanner_model_not_broker",
        "last_seen": now[0] - 300, "buy_entry": 1.0, "active_stop": 0.95,
        "tp1_realized": False, "remaining_fraction": 1.0,
        "trade_setup": {"entry": 1.0, "stop_loss": 0.95, "tp1": 1.10, "tp2": 1.18},
    }}})
    api._penny_position_monitor_wrapper()
    assert emitted[-1]["lifecycle"] == event
    persisted_first = api._penny_load_state_tickers()["OPEN"]
    assert persisted_first["active"] is (event != "EXIT")
    assert persisted_first["tp1_realized"] is (event == "TP1_PARTIAL")
    assert persisted_first["active_stop"] == (1.0 if event == "TP1_PARTIAL" else 0.95)
    sent.clear()
    price[0] = 1.04
    now[0] += 300
    api._penny_position_monitor_wrapper()
    assert emitted[-1]["lifecycle"] == ("FORTSETZUNG" if event == "TP1_PARTIAL" else "EXIT")
    assert emitted[-1]["trade_action"] == ("HALTEN" if event == "TP1_PARTIAL" else "JETZT_VERKAUFEN")
    assert sent and sent[0]["model_event_observed_at"] == now[0] - 300
    persisted_second = api._penny_load_state_tickers()["OPEN"]
    assert persisted_second["remaining_fraction"] == (0.5 if event == "TP1_PARTIAL" else 0.0)
