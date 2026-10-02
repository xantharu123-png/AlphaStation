"""Native Explosion producer -> final quote/path -> actual offline SMTP/journal.

All state is disposable. Only provider/SMTP boundaries are replaced; admission,
trade health, revalidation, intent ownership, receipts and cooldown stay real.
"""
from copy import deepcopy
from datetime import datetime, timezone
import sqlite3
import threading
import time

import pytest

import api
from modules import mail_outbox, signal_tracker as tracker
from test_alert_delivery_intent_api import _AcceptedSMTP, _setup, _decoded_wire_message
from test_crypto_explosion_scanner import _bars, _candidate


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    now = int(time.time())
    monkeypatch.setattr(api.time, "time", lambda: now)
    monkeypatch.setattr(api, "_EMAIL_STARTUP_TIME", now - 3600)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_CE_RUN_LOCK", threading.Lock())
    monkeypatch.setattr(api, "_CE_PROGRESS", {})
    monkeypatch.setattr(api, "CRYPTO_EXPLOSION_CACHE", str(tmp_path / "explosion.json"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    monkeypatch.setattr(tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", str(tmp_path / "receipts.sqlite"))
    monkeypatch.setattr(api, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(tmp_path / "outbox.sqlite"))
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")

    class SMTP(_AcceptedSMTP):
        calls = 0
        messages = []
        recipient_batches = []
        failure = None

        def sendmail(self, sender, recipients, message):
            type(self).calls += 1
            type(self).messages.append(message)
            type(self).recipient_batches.append(tuple(recipients))
            if type(self).failure is not None:
                raise type(self).failure
            return {}

    db = _setup(monkeypatch, tmp_path, SMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", SMTP)
    observed = now // 300 * 300 - 300
    row = {
        "Symbol": "TEST", "symbol": "TEST", "ticker": "TEST",
        "exchange": "binance", "venue": "binance", "contract": "TESTUSDT",
        "contract_symbol": "TESTUSDT", "stable_ref": "binance:TESTUSDT",
        "direction": "LONG", "strategy": "Explosion Long", "market_type": "crypto",
        "scanner_source": "crypto_explosion", "timeframe": "5m",
        "Price": 10.0, "price": 10.0, "entry": 10.0,
        "stop": 9.5, "stop_loss": 9.5, "tp1": 11.0, "tp2": 12.0,
        "trade_signal": "JETZT_TRADEN", "trade_action": "LONG_NOW",
        "trade_decision": "TRADEABLE", "execution_trigger_ok": True,
        "alertable_crypto": True, "risk_level": "LOW", "score": 95, "Grade": "S",
        "grade": "S", "explosion_score": 95, "entry_score": 94,
        "RVOL": 3.0, "rvol": 3.0, "vol_confirmed": True,
        "volume_model": "exchange_5m_vs_median", "close_pos_5m": .8,
        "risk_reward": 3.0, "live_rr_ratio": 3.0, "rr_tp1": 2.0,
        "execution_candle_timestamp": observed - 300,
        "execution_data_age_seconds": now - observed,
        "scan_price_observed_at": observed, "scan_price_source": "binance:TESTUSDT:5m:close",
        "funding_rate": .01, "funding_rate_unit": "percent", "funding_source": "binance",
        "funding_interval_hours": 8, "funding_available": True,
        "spread_pct": .02, "spread_execution_ok": True, "HasPerp": True, "isCrypto": True,
        "btc_context": {"known": True, "data_status": "ok", "btc_24h": 0.0,
                        "coin_24h": 4.0, "alpha_24h": 4.0, "observed_at": now - 10,
                        "tailwind": True},
        "target_quality": "STRUCTURAL_TP1_PROJECTION_TP2", "tp1_is_projection": False,
        "tp2_is_projection": True, "tp1_source": "observed_24h_high_liquidity",
        "tp2_source": "measured_move_projection", "stop_source": "confirmed swing low",
        "level_model": "breakout_structure_v1", "barrier_gate_active": False,
        "trade_setup": {"direction": "LONG", "entry": 10.0, "stop": 9.5,
                        "stop_loss": 9.5, "tp1": 11.0, "tp2": 12.0,
                        "tp1_is_projection": False, "tp2_is_projection": True,
                        "tp1_source": "observed_24h_high_liquidity",
                        "target_quality": "STRUCTURAL_TP1_PROJECTION_TP2",
                        "level_model": "breakout_structure_v1"},
    }
    api._stamp_fresh_crypto_profile_contract(row)
    calls = []
    quote = {
        "ok": True, "price": 10.01, "bid": 10.0, "ask": 10.01,
        "spread_bps": 10.0, "depth_10bps_min_usd": 100_000,
        "depth_25bps_min_usd": 200_000, "depth_50bps_min_usd": 300_000,
        "observed_ts": now, "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "source": "binance:TESTUSDT:live_orderbook_top", "price_mode": "ask",
        "price_session": "CRYPTO_24_7",
    }
    path = [{"timestamp": ts, "open": 10.0, "high": 10.1, "low": 9.9,
             "close": 10.01, "volume": 1000} for ts in range(observed, now + 1, 60)]

    def fetch_quote(contract, venue, direction):
        calls.append(("quote", contract, venue, direction))
        return dict(quote, source=f"{venue}:{contract}:live_orderbook_top")

    def fetch_path(contract, venue, timeframe, count):
        calls.append(("path", contract, venue, timeframe, count))
        return deepcopy(path)

    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", fetch_quote)
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fetch_path)
    return {"row": row, "SMTP": SMTP, "db": db, "now": now, "quote": quote,
            "path": path, "calls": calls, "receipts": str(tmp_path / "receipts.sqlite")}


def test_native_long_reaches_real_sender_one_receipt_correct_channel_and_owner(delivery):
    row, smtp = delivery["row"], delivery["SMTP"]
    before = deepcopy(row)
    assert api._crypto_explosion_mail_block_reason(row, delivery["now"]) is None
    state = api._classify_alert_candidate("crypto_explosion", row, delivery["now"])
    assert state["alertable_now"], state["suppression_reasons"]
    assert api._send_crypto_explosion_alerts([row]) is True
    assert smtp.calls == 1 and row == before
    assert delivery["calls"][0] == ("quote", "TESTUSDT", "binance", "LONG")
    assert delivery["calls"][1][:4] == ("path", "TESTUSDT", "binance", "1m")
    with sqlite3.connect(delivery["db"]) as conn:
        record = conn.execute("SELECT scanner,mail_channel,status,delivery_state,entry FROM signals").fetchone()
    assert record[:4] == ("crypto_explosion", "crypto", tracker.STATUS_OPEN, "ACTIVE")
    assert record[4] == pytest.approx(10.01)
    with sqlite3.connect(delivery["receipts"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM delivery_acceptance_journal").fetchone()[0] == 1
    assert any(event["status"] == "sent" for event in api._EMAIL_SEND_LOG)
    assert "binance:TESTUSDT" in _decoded_wire_message(smtp.messages[0])
    assert api._send_crypto_explosion_alerts([row]) is False
    assert smtp.calls == 1
    assert state["cooldown_key"] in api._EMAIL_COOLDOWN


@pytest.mark.parametrize("change", [
    {"trade_signal": "EXPLOSION_ARMED"}, {"trade_action": "WAIT_FOR_RETEST"},
    {"alertable_crypto": False}, {"execution_trigger_ok": False},
    {"risk_level": "HIGH"}, {"partial_data": True},
    {"funding_available": False}, {"funding_interval_hours": None},
    {"funding_rate": .08}, {"spread_pct": .21}, {"spread_pct": True},
    {"spread_execution_ok": False}, {"tp1_is_projection": True},
    {"barrier_gate_active": True}, {"contract_symbol": "OTHERUSDT"},
    {"scan_price_observed_at": None}, {"scan_price_source": "coingecko:current_price"},
    {"crypto_profile_cache_version": 2}, {"score": 79, "Grade": "B", "grade": "B"},
])
def test_unreleased_native_states_never_fetch_quote_or_enter_smtp(delivery, change):
    row = dict(delivery["row"], **change)
    assert api._send_crypto_explosion_alerts([row]) is False
    assert not delivery["calls"] and delivery["SMTP"].calls == 0
    assert api._EMAIL_COOLDOWN == {}


@pytest.mark.parametrize("change", [
    {"known": False}, {"observed_at": None}, {"observed_at": True},
    {"btc_24h": float("nan")}, {"btc_24h": True}, {"alpha_24h": 999.0},
    {"data_status": "unknown"},
])
def test_unknown_invalid_btc_is_not_repaired_into_measured_zero(delivery, change):
    row = deepcopy(delivery["row"])
    row["btc_context"].update(change)
    assert not api._send_crypto_explosion_alerts([row])
    assert delivery["SMTP"].calls == 0 and not delivery["calls"]


def test_old_close_cannot_be_refreshed_by_new_cache_time(delivery):
    row = deepcopy(delivery["row"])
    row.update(execution_candle_timestamp=delivery["now"] - 1200,
               scan_price_observed_at=delivery["now"] - 900,
               execution_data_age_seconds=0, cached_at=delivery["now"])
    assert not api._send_crypto_explosion_alerts([row])
    assert delivery["SMTP"].calls == 0 and not delivery["calls"]


@pytest.mark.parametrize("field,value", [
    ("spread_bps", 21), ("depth_10bps_min_usd", 0),
    ("depth_25bps_min_usd", 0), ("depth_50bps_min_usd", 0),
    ("price", 10.8), ("price", 11.1), ("price", 9.4),
])
def test_final_execution_change_blocks_and_releases_unsent_claim(delivery, field, value):
    delivery["quote"][field] = value
    assert not api._send_crypto_explosion_alerts([delivery["row"]])
    assert delivery["SMTP"].calls == 0 and api._EMAIL_COOLDOWN == {}
    assert not api._load_email_dedupe(now=delivery["now"])


@pytest.mark.parametrize("field,value", [("low", 9.4), ("high", 11.1)])
def test_stop_or_tp_touch_since_scan_prevents_late_retrace_mail(delivery, field, value):
    delivery["path"][1][field] = value
    assert not api._send_crypto_explosion_alerts([delivery["row"]])
    assert delivery["SMTP"].calls == 0
    assert not api._load_email_dedupe(now=delivery["now"])


def test_refused_data_can_retry_fresh_row_without_false_receipt(delivery):
    smtp = delivery["SMTP"]
    smtp.failure = api.smtplib.SMTPDataError(550, b"rejected")
    assert not api._send_crypto_explosion_alerts([delivery["row"]])
    assert smtp.calls == 1 and api._EMAIL_COOLDOWN == {}
    assert not api._load_email_dedupe(now=delivery["now"])
    smtp.failure = None
    assert api._send_crypto_explosion_alerts([delivery["row"]])
    assert smtp.calls == 2


def test_unknown_data_receipt_preserves_quarantine_no_automatic_replay(delivery):
    smtp = delivery["SMTP"]
    smtp.failure = TimeoutError("DATA reply unknown")
    row = delivery["row"]
    key = api._classify_alert_candidate("crypto_explosion", row, delivery["now"])["cooldown_key"]
    assert not api._send_crypto_explosion_alerts([row])
    assert smtp.calls == 1 and api._EMAIL_COOLDOWN == {}
    assert mail_outbox.has_uncertain_delivery_key(key)
    smtp.failure = None
    assert not api._send_crypto_explosion_alerts([row])
    assert smtp.calls == 1
    with sqlite3.connect(delivery["db"]) as conn:
        assert conn.execute("SELECT delivery_state FROM signals").fetchall() == [("ATTEMPTED",)]


def test_wrapper_caches_complete_origin_then_dispatches_and_keeps_scan_success(delivery, monkeypatch):
    rows = [delivery["row"]]
    monkeypatch.setattr(api, "_run_crypto_explosion_scan", lambda: (rows, {"result_count": 1, "chart_checked": 1}))
    original = api._send_crypto_explosion_alerts

    def after_cache(actual):
        assert api.load_cache_file(api.CRYPTO_EXPLOSION_CACHE)[0] == rows
        assert api._ce_progress_snapshot()["status"] == "done"
        return original(actual)

    monkeypatch.setattr(api, "_send_crypto_explosion_alerts", after_cache)
    delivery["SMTP"].failure = api.smtplib.SMTPDataError(550, b"rejected")
    api._crypto_explosion_wrapper()
    assert api._ce_progress_snapshot()["status"] == "done"
    assert api.load_cache_file(api.CRYPTO_EXPLOSION_CACHE)[0] == rows
    assert delivery["SMTP"].calls == 1


def test_incomplete_wrapper_never_reuses_previous_cache_for_mail(delivery, monkeypatch):
    api.save_cache_file(api.CRYPTO_EXPLOSION_CACHE, [delivery["row"]])
    monkeypatch.setattr(api, "_run_crypto_explosion_scan", lambda: ([], {"incomplete": True}))
    with pytest.raises(RuntimeError, match="previous cache retained"):
        api._crypto_explosion_wrapper()
    assert delivery["SMTP"].calls == 0 and not delivery["calls"]


def test_native_producer_observation_is_actual_closed_candle_not_run_time(monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: {
        "known": True, "data_status": "ok", "btc_24h": 0.0, "coin_24h": change,
        "alpha_24h": change, "observed_at": now - 10, "tailwind": True,
    })
    bars5 = _bars(90, start=9.48, step=.004, volume=1000, last={
        "open": 9.93, "high": 9.97, "low": 9.90, "close": 9.95, "volume": 1200,
    })
    bars15 = _bars(60, start=9.42, step=.009, volume=3000, interval=900)
    bars4h = _bars(60, start=9.4, step=.006, volume=5000, interval=14400)
    row = api._score_crypto_explosion_candidate(_candidate(), bars5, bars15, bars4h)
    assert row is not None
    assert row["scan_price_observed_at"] == bars5[-1]["timestamp"] + 300
    assert row["scan_price_observed_at"] < now
    assert row["price"] == bars5[-1]["close"]
    assert row["scan_price_source"] == "bybit:TESTUSDT:5m:close"
    assert row["venue"] == "bybit" and row["contract_symbol"] == "TESTUSDT"


def test_actual_producer_native_structure_can_reach_dispatch_without_gate_mocks(delivery, monkeypatch):
    now = delivery["now"]
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: {
        "known": True, "data_status": "ok", "btc_24h": 0.0, "coin_24h": change,
        "alpha_24h": change, "observed_at": now - 10, "tailwind": True,
    })
    bars5 = _bars(90, start=9.50, step=.004, volume=1000, last={
        "open": 10.00, "high": 10.14, "low": 9.98, "close": 10.12, "volume": 3200,
    })
    bars15 = _bars(60, start=9.42, step=.009, volume=3000, interval=900)
    # A genuinely populated older HTF volume zone above the current breakout
    # provides the structural target; no plan/health/VRVP gate is replaced.
    bars4h = _bars(60, start=11.4, step=.002, volume=5000, interval=14400)
    observation = delivery["row"]["scan_price_observed_at"]
    for idx, bar in enumerate(bars5):
        bar["timestamp"] = observation - (len(bars5) - idx) * 300
    native = api._score_crypto_explosion_candidate(_candidate(price=10.12, change=8.0), bars5, bars15, bars4h)
    assert native is not None
    assert native["alertable_crypto"] is True, {
        "risk_reasons": native.get("risk_reasons"),
        "setup_reason": native.get("trade_setup", {}).get("structure_reason"),
        "target_quality": native.get("target_quality"),
        "tp1_is_projection": native.get("tp1_is_projection"),
        "funding_available": native.get("funding_available"),
        "spread_execution_ok": native.get("spread_execution_ok"),
        "structure_status": native.get("structure_status"), "barrier_gate": native.get("barrier_gate"),
    }
    assert native["trade_signal"] == "JETZT_TRADEN"
    assert native["trade_setup"]["tp1_causal_structure_validated"] is True
    assert native["trade_setup"]["tp1_zone_id"]
    assert native["trade_setup"]["tp1_timeframe"] == "4H"
    assert native["trade_setup"]["tp1_confirmed_at"]
    delivery["quote"].update(price=10.121, ask=10.121, bid=10.12)
    for bar in delivery["path"]:
        bar.update(open=10.12, high=10.14, low=10.10, close=10.121)
    assert api._send_crypto_explosion_alerts([native]) is True
    assert delivery["SMTP"].calls == 1


def test_btc_neutral_tailwind_false_does_not_newly_block_native_release(delivery):
    row = deepcopy(delivery["row"])
    row["btc_context"].update(btc_24h=-2.0, coin_24h=1.0, alpha_24h=3.0, tailwind=False)
    row["risk_level"] = "MEDIUM"
    assert api._send_crypto_explosion_alerts([row]) is True


def test_distinct_native_venues_keep_distinct_mail_identity(delivery):
    first = deepcopy(delivery["row"])
    second = dict(deepcopy(first), exchange="bybit", venue="bybit",
                  stable_ref="bybit:TESTUSDT", scan_price_source="bybit:TESTUSDT:5m:close")
    first_key = api._classify_alert_candidate("crypto_explosion", first, delivery["now"])["cooldown_key"]
    second_key = api._classify_alert_candidate("crypto_explosion", second, delivery["now"])["cooldown_key"]
    assert first_key != second_key
    assert api._send_crypto_explosion_alerts([first, second])
    assert delivery["SMTP"].calls == 2


def test_inflight_owned_lease_cannot_be_stolen_or_replayed(delivery):
    row = delivery["row"]
    key = api._classify_alert_candidate("crypto_explosion", row, delivery["now"])["cooldown_key"]
    assert api._email_dedupe_claim(key, api._alert_dedupe_ttl_seconds("crypto_explosion"), now=delivery["now"])
    assert not api._send_crypto_explosion_alerts([row])
    assert not delivery["calls"] and delivery["SMTP"].calls == 0


def test_clock_expiring_during_final_path_does_not_refresh_prior_evidence(delivery, monkeypatch):
    original = api._revalidate_early_mover_mail_candidate

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.get("ok") is True
        # Simulate elapsed time after an actually successful final gate; the
        # dispatcher must not rewrite the old candle/BTC clock as current.
        monkeypatch.setattr(api.time, "time", lambda: delivery["now"] + 400)
        return result

    monkeypatch.setattr(api, "_revalidate_early_mover_mail_candidate", delayed)
    assert not api._send_crypto_explosion_alerts([delivery["row"]])
    assert delivery["SMTP"].calls == 0 and api._EMAIL_COOLDOWN == {}


def test_failed_smtp_is_not_relabelled_as_no_candidates(delivery):
    delivery["SMTP"].failure = api.smtplib.SMTPDataError(550, b"rejected")
    assert not api._send_crypto_explosion_alerts([delivery["row"]])
    assert delivery["SMTP"].calls == 1
    assert not any(event["subject"] == "Crypto Long Alert" and event["status"] == "skipped"
                   for event in api._EMAIL_SEND_LOG)


def test_dispatch_exception_cannot_change_completed_scanner_to_error(delivery, monkeypatch):
    monkeypatch.setattr(api, "_run_crypto_explosion_scan", lambda: ([delivery["row"]], {"result_count": 1}))
    monkeypatch.setattr(api, "_send_crypto_explosion_alerts", lambda rows: (_ for _ in ()).throw(ValueError("boundary failed")))
    api._crypto_explosion_wrapper()
    assert api._ce_progress_snapshot()["status"] == "done"
    assert api.load_cache_file(api.CRYPTO_EXPLOSION_CACHE)[0] == [delivery["row"]]
    assert any(event["status"] == "error" and event["reason"] == "crypto_explosion_mail_dispatch_exception"
               for event in api._EMAIL_SEND_LOG)


def test_all_native_mail_reasons_are_registered_for_private_diagnostics(delivery):
    for reason in (
        "crypto_explosion_watch_only", "crypto_explosion_data_or_risk_blocked",
        "crypto_explosion_native_contract_missing_or_conflicting",
        "crypto_explosion_execution_stale_or_source_unproven",
        "crypto_explosion_btc_context_unknown_or_stale",
        "crypto_explosion_funding_or_spread_unqualified", "crypto_explosion_invalid_row",
        "crypto_explosion_duplicate_identity", "crypto_explosion_mail_quota_deferred",
        "crypto_explosion_mail_dispatch_exception",
    ):
        assert reason in api.ALLOWED_SUPPRESSION_REASONS
        assert api._stable_suppression_reason(reason) == reason
        assert api._alert_reason_label(reason) != reason.replace("_", " ")


def test_observed_24h_high_without_genuine_profile_barrier_cannot_mail(delivery, monkeypatch):
    now = delivery["now"]
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: {
        "known": True, "data_status": "ok", "btc_24h": 0.0, "coin_24h": change,
        "alpha_24h": change, "observed_at": now - 10, "tailwind": True,
    })
    bars5 = _bars(90, start=9.50, step=.004, volume=1000, last={
        "open": 10.00, "high": 10.14, "low": 9.98, "close": 10.12, "volume": 3200,
    })
    bars15 = _bars(60, start=9.42, step=.009, volume=3000, interval=900)
    bars4h = _bars(60, start=9.4, step=.006, volume=5000, interval=14400)
    row = api._score_crypto_explosion_candidate(_candidate(price=10.12, change=8.0), bars5, bars15, bars4h)
    assert row is not None
    assert row["execution_trigger_ok"] is True
    assert row["tp1_is_projection"] is True
    assert row["alertable_crypto"] is False and row["trade_signal"] == "EXPLOSION_ARMED"
    assert not api._send_crypto_explosion_alerts([row])
    assert delivery["SMTP"].calls == 0 and not delivery["calls"]
