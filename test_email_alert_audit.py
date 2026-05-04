import json
import time
from datetime import datetime

import api


def test_alert_audit_counts_alertable_and_suppressed(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "alerts.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [
            {"ticker": "AAA", "grade": "A", "score": 72, "rvol": 1.2, "price": 10},
            {"ticker": "BBB", "grade": "B", "score": 62, "rvol": 3.0, "price": 20},
            {"ticker": "CCC", "grade": "S", "score": 90, "rvol": 0.2, "price": 30},
        ],
    }))

    audit = api._build_alert_audit_for_cache("stock_strategy", str(cache_file))

    assert audit["rows_checked"] == 3
    assert audit["alertable_now_count"] == 1
    assert audit["grade_counts"]["A"] == 1
    assert audit["grade_counts"]["B"] == 1
    assert audit["grade_counts"]["S"] == 1
    assert audit["suppression_counts"]["grade_below_alert_threshold"] == 1
    assert audit["suppression_counts"]["rvol_below_alert_threshold"] == 1


def test_long_alert_audit_blocks_extended_fading_move(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "long_fade.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "ticker": "LATE",
            "grade": "A",
            "score": 82,
            "rvol": 2.4,
            "price": 18.2,
            "change_pct": 16.5,
            "close_pos": 0.38,
            "open_to_current_pct": -1.1,
            "latest_bar_change_pct": -0.4,
            "latest_bar_close_pos": 0.2,
            "Signal_Direction": "LONG",
        }],
    }))

    audit = api._build_alert_audit_for_cache("stock_strategy", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["latest_5m_red_fade"] == 1
    assert audit["suppression_counts"]["extended_long_fading_wait_retest"] == 1


def test_long_alert_audit_allows_clean_momentum_continuation(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "long_continuation.json"
    row = {
        "ticker": "RUNR",
        "grade": "A",
        "score": 86,
        "rvol": 2.8,
        "price": 24.5,
        "change_pct": 18.0,
        "close_pos": 0.91,
        "open_to_current_pct": 8.5,
        "latest_bar_change_pct": 0.35,
        "latest_bar_close_pos": 0.82,
        "Extension_ATR": 4.5,
        "Signal_Direction": "LONG",
    }
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [row],
    }))

    audit = api._build_alert_audit_for_cache("stock_strategy", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert api._long_entry_quality(row) == "CONTINUATION_OK"


def test_long_alert_rule_labels_extended_continuation_ok():
    row = {
        "ticker": "MDRX",
        "grade": "A",
        "score": 90,
        "rvol": 2.2,
        "change_pct": 24.0,
        "close_pos": 0.88,
        "latest_bar_change_pct": 0.1,
        "latest_bar_close_pos": 0.7,
        "mdr_tag": "MDR STARK",
        "Signal_Direction": "LONG",
    }

    assert api._long_entry_rule_reasons(row) == []
    assert api._long_entry_quality(row) == "CONTINUATION_OK"


def test_bear_alert_audit_excludes_inverse_etfs(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "inverse_etfs": [
                {"ticker": "LABD", "name": "3x Short Biotech", "signal": "STARK", "rvol": 0.6}
            ],
            "breakdown_stocks": [
                {
                    "ticker": "REAL",
                    "grade": "A",
                    "score": 70,
                    "rvol": 1.1,
                    "price": 12,
                    "change_pct": -6.0,
                    "open_to_current_pct": -5.0,
                    "close_pos": 0.2,
                    "alertable_short": True,
                }
            ],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "REAL"
    assert all(item["ticker"] != "LABD" for item in audit["alertable_preview"])


def test_bear_alert_audit_blocks_overextended_green_reclaim(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear_late.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "breakdown_stocks": [{
                "ticker": "SKBL",
                "grade": "A",
                "score": 59,
                "rvol": 3.4,
                "price": 3.44,
                "change_pct": -24.3,
                "open_to_current_pct": 1.2,
                "close_pos": 0.65,
            }],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["drop_too_extended_no_chase"] == 1
    assert audit["suppression_counts"]["current_candle_green_reclaim"] == 1
    assert audit["suppression_counts"]["not_closing_near_low"] == 1


def test_bear_alert_audit_allows_fresh_breakdown_near_lows(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear_fresh.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "breakdown_stocks": [{
                "ticker": "FRESH",
                "grade": "A",
                "score": 66,
                "rvol": 2.1,
                "price": 9.8,
                "change_pct": -7.0,
                "open_to_current_pct": -6.4,
                "close_pos": 0.12,
            }],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "FRESH"


def test_bear_alert_audit_blocks_latest_5m_green_reclaim(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear_5m_reclaim.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "breakdown_stocks": [{
                "ticker": "BOUNCE",
                "grade": "A",
                "score": 66,
                "rvol": 2.1,
                "price": 9.8,
                "change_pct": -7.0,
                "open_to_current_pct": -6.4,
                "close_pos": 0.12,
                "latest_bar_change_pct": 0.42,
                "latest_bar_close_pos": 0.82,
            }],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["latest_5m_green_reclaim"] == 1


def test_bear_crash_alert_requires_current_sell_pressure():
    late_reclaim = {
        "ticker": "SKBL",
        "grade": "A",
        "score": 70,
        "change_pct": -24.3,
        "open_to_current_pct": 1.2,
        "close_pos": 0.65,
    }
    active_flush = {
        "ticker": "DROP",
        "grade": "A",
        "score": 70,
        "change_pct": -11.0,
        "open_to_current_pct": -9.0,
        "close_pos": 0.1,
        "latest_bar_change_pct": -0.3,
        "latest_bar_close_pos": 0.2,
    }
    latest_5m_bounce = {
        "ticker": "BOUNCE",
        "grade": "A",
        "score": 70,
        "change_pct": -11.0,
        "open_to_current_pct": -9.0,
        "close_pos": 0.1,
        "latest_bar_change_pct": 0.4,
        "latest_bar_close_pos": 0.8,
    }

    assert api._bear_crash_alert_ok(late_reclaim) is False
    assert api._bear_crash_alert_ok(latest_5m_bounce) is False
    assert api._bear_crash_alert_ok(active_flush) is True


def test_email_sender_blocks_inverse_etf_content():
    api._EMAIL_SEND_LOG.clear()

    blocked = api._send_email_alert(
        "Bear Alert",
        "<h3>Inverse ETFs (Signal STARK)</h3><td>LABD</td><td>3x Short Biotech</td>",
        bypass_startup_cooldown=True,
    )

    assert blocked is False
    assert api._EMAIL_SEND_LOG[-1]["status"] == "skipped"
    assert api._EMAIL_SEND_LOG[-1]["reason"] == "blocked_etf_content"


def test_email_etf_guard_allows_stock_setups():
    assert api._email_has_blocked_etf_content(
        "Bear Alert: 1 Aktien-Short",
        "<td>REAL</td><td>Grade A</td><td>RVOL 1.2x</td>",
    ) is False
    assert api._email_has_blocked_etf_content(
        "Momentum Breakout",
        "<td>AMPL</td><td>Amplitude Inc.</td><td>Grade A</td>",
    ) is False


def test_email_dedupe_persists_crash_ticker(tmp_path, monkeypatch):
    dedupe_file = tmp_path / "email_dedupe.json"
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(dedupe_file))

    key = "crash_stock_20260430_NCSM"

    assert api._email_dedupe_claim(key, ttl_seconds=36 * 3600, now=1_000_000.0) is True
    assert api._email_dedupe_claim(key, ttl_seconds=36 * 3600, now=1_000_060.0) is False
    assert json.loads(dedupe_file.read_text())[key] == 1_000_000.0
    assert api._email_dedupe_claim(key, ttl_seconds=36 * 3600, now=1_000_000.0 + 37 * 3600) is True


def test_email_status_exposes_dedupe(tmp_path, monkeypatch):
    dedupe_file = tmp_path / "email_dedupe.json"
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(dedupe_file))
    api._email_dedupe_mark("crash_stock_20260430_NCSM", now=time.time())

    status = api._email_alert_status()

    assert status["dedupe"]["file_exists"] is True
    assert status["dedupe"]["entries"] == 1
    assert status["dedupe"]["active_crash_entries"] == 1
    assert status["dedupe"]["recent"][0]["key"] == "crash_stock_20260430_NCSM"


def test_alert_classifier_respects_cooldown():
    api._EMAIL_COOLDOWN.clear()
    now = 1_000_000.0
    row = {"ticker": "ORB1", "grade": "A", "score": 80, "price": 12}

    first = api._classify_alert_candidate("orb", row, now)
    assert first["alertable_now"] is True

    api._EMAIL_COOLDOWN[first["cooldown_key"]] = now
    second = api._classify_alert_candidate("orb", row, now + 60)
    assert second["alertable_now"] is False
    assert "cooldown_active" in second["suppression_reasons"]


def test_bearish_dedupe_suppresses_duplicate_short_alerts(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    now = 1_000_000.0
    row = {"Ticker": "DUP", "BI_Grade": "A", "BI_Score": 100, "RVOL": 1.4, "Preis": 12}

    api._mark_bearish_stock_alert("DUP", now=now)

    short_state = api._classify_alert_candidate("bi_short", row, now + 60)
    long_state = api._classify_alert_candidate("bi_long", row, now + 60)

    assert short_state["alertable_now"] is False
    assert "bearish_ticker_already_alerted" in short_state["suppression_reasons"]
    assert long_state["alertable_now"] is True


def test_new_listing_pipeline_alerts_only_active_top_grades(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    payload = {
        "signals": [
            {
                "symbol": "WLDUSDT",
                "exchange": "mexc",
                "signal": {
                    "grade": "A",
                    "timing": "[-] JETZT SHORTEN",
                    "timing_quality": 5,
                    "safety_ok": True,
                    "entry": 1.2,
                    "stop_loss": 1.5,
                    "tp1": 0.9,
                    "tp2": 0.7,
                    "rr_effective": 1.5,
                    "risk_pct": 25,
                    "confirmation_ok": True,
                    "continuation_risk": False,
                    "signal_quality": "tradeable",
                    "listing_source": "new_listing",
                    "listing_trade_ok": True,
                    "listing_age_hours": 24,
                    "trade_category": "NEW_LISTING_DUMP",
                    "micro_required": True,
                    "micro_trigger_ok": True,
                    "pump_data": {"micro_score": 75, "micro_trigger_ok": True},
                    "exh_score": 70,
                },
            },
            {"symbol": "LOWUSDT", "exchange": "mexc", "signal": {"grade": "B", "timing": "WATCH"}},
            {
                "symbol": "WATCHUSDT",
                "exchange": "mexc",
                "signal": {
                    "grade": "A",
                    "timing": "[+] WATCHLIST - noch nicht reif",
                    "timing_quality": 2,
                    "safety_ok": True,
                    "rr_effective": 2.2,
                },
            },
            {
                "symbol": "RISKUSDT",
                "exchange": "mexc",
                "signal": {
                    "grade": "S",
                    "timing": "[-] JETZT SHORTEN",
                    "timing_quality": 5,
                    "safety_ok": False,
                    "rr_effective": 3.0,
                },
            },
        ]
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert len(sent) == 1
    assert "Pump & Dump" in sent[0][0]
    assert "WLD" in sent[0][1]
    assert "LOW" not in sent[0][1]
    assert "WATCH" not in sent[0][1]
    assert "RISK" not in sent[0][1]


def test_new_listing_pipeline_sends_daily_radar_when_no_short_now(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)

    payload = {
        "signals": [],
        "watchlist": [{
            "symbol": "BABYUSDT",
            "exchange": "mexc",
            "signal": {
                "grade": "A",
                "timing": "[~] WATCH - BTC risk-on, erst klare Underperformance/deeper crack abwarten",
                "timing_quality": 2,
                "safety_ok": True,
                "rr_effective": 2.0,
                "risk_pct": 12,
                "confirmation_ok": True,
                "continuation_risk": False,
                "signal_quality": "watch_or_blocked",
                "listing_source": "new_listing",
                "listing_trade_ok": False,
                "listing_age_hours": 18,
                "trade_category": "NEW_LISTING_WATCH",
                "micro_required": True,
                "micro_trigger_ok": True,
                "tp1_missed": False,
                "tp2_missed": False,
                "exh_score": 74,
                "pump_data": {
                    "pump_pct": 90,
                    "from_ath_pct": 3,
                    "btc_change_pct": 3.2,
                    "coin_change_pct": 1.0,
                    "btc_divergence": -2.2,
                    "btc_short_context": "BTC_RISK_ON_WAIT_FOR_DEEPER_CRACK",
                },
                "risk_flags": ["btc_risk_on_wait_for_deeper_crack"],
            },
        }],
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert len(sent) == 1
    assert "Crypto New Listing Radar" in sent[0][0]
    assert "BABY" in sent[0][1]
    assert "SHORT NOW" in sent[0][1]


def test_new_listing_radar_ignores_active_pump_rows(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)

    payload = {
        "signals": [],
        "watchlist": [{
            "symbol": "OLDPUMPUSDT",
            "exchange": "binance",
            "signal": {
                "grade": "A",
                "timing": "[~] ACTIVE PUMP WATCH",
                "timing_quality": 2,
                "listing_source": "pump_detection",
                "listing_trade_ok": False,
                "trade_category": "ACTIVE_PUMP_WATCH",
            },
        }],
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert sent == []


def test_new_listing_alert_audit_ignores_watchlist_rows(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "new_listing.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [
            {
                "symbol": "SHORT",
                "grade": "A",
                "signal": "SHORT",
                "source": "signals",
                "listing_source": "new_listing",
                "listing_trade_ok": True,
                "listing_age_hours": 24,
                "trade_category": "NEW_LISTING_DUMP",
                "timing_quality": 5,
                "safety_ok": True,
                "rr_effective": 1.8,
                "risk_pct": 25,
                "confirmation_ok": True,
                "continuation_risk": False,
                "signal_quality": "tradeable",
                "micro_required": True,
                "micro_trigger_ok": True,
                "tp1_missed": False,
                "tp2_missed": False,
            },
            {
                "symbol": "WATCH",
                "grade": "S",
                "signal": "WATCH",
                "source": "watchlist",
                "timing_quality": 2,
                "safety_ok": True,
                "rr_effective": 3.0,
            },
        ],
    }))

    audit = api._build_alert_audit_for_cache("new_listing", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "SHORT"


def test_new_listing_alert_audit_requires_micro_trigger_for_short_now(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "new_listing_micro.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "symbol": "GENIUS",
            "grade": "A",
            "signal": "SHORT",
            "source": "signals",
            "listing_source": "new_listing",
            "listing_trade_ok": True,
            "listing_age_hours": 24,
            "trade_category": "NEW_LISTING_DUMP",
            "timing_quality": 4,
            "safety_ok": True,
            "rr_effective": 2.8,
            "risk_pct": 4.3,
            "confirmation_ok": True,
            "continuation_risk": False,
            "signal_quality": "tradeable",
            "micro_required": True,
            "micro_trigger_ok": False,
            "tp1_missed": False,
            "tp2_missed": False,
        }],
    }))

    audit = api._build_alert_audit_for_cache("new_listing", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["micro_trigger_missing"] == 1


def test_new_listing_alert_audit_blocks_active_pump_watch_rows(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "active_pump.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "symbol": "OLDPUMP",
            "grade": "A",
            "signal": "SHORT",
            "source": "signals",
            "listing_source": "pump_detection",
            "listing_trade_ok": False,
            "trade_category": "ACTIVE_PUMP_WATCH",
            "timing_quality": 5,
            "safety_ok": True,
            "rr_effective": 2.0,
            "risk_pct": 12,
            "confirmation_ok": True,
            "continuation_risk": False,
            "signal_quality": "tradeable",
            "micro_required": True,
            "micro_trigger_ok": True,
            "tp1_missed": False,
            "tp2_missed": False,
        }],
    }))

    audit = api._build_alert_audit_for_cache("new_listing", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["not_new_listing_dump"] == 1
    assert audit["suppression_counts"]["listing_age_not_tradeable"] == 1


def test_new_listing_alert_audit_treats_string_false_as_false(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "string_bool_new_listing.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "symbol": "STRINGBOOL",
            "grade": "A",
            "signal": "SHORT",
            "source": "signals",
            "listing_source": "new_listing",
            "listing_trade_ok": "false",
            "listing_age_hours": "24",
            "trade_category": "NEW_LISTING_DUMP",
            "timing_quality": "5",
            "safety_ok": "true",
            "rr_effective": "2.0",
            "risk_pct": "12",
            "confirmation_ok": "true",
            "continuation_risk": "false",
            "signal_quality": "tradeable",
            "micro_required": "true",
            "micro_trigger_ok": "true",
            "tp1_missed": "false",
            "tp2_missed": "false",
        }],
    }))

    audit = api._build_alert_audit_for_cache("new_listing", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["listing_age_not_tradeable"] == 1


def test_crypto_strategy_alerts_are_watch_only_without_execution_trigger():
    api._EMAIL_COOLDOWN.clear()
    row = {
        "Ticker": "GENIUS",
        "grade": "A",
        "score": 92,
        "RVOL": 3.0,
        "Preis": 0.42,
        "signal_quality": "watch_only",
        "execution_trigger_ok": False,
        "partial_data": False,
    }

    state = api._classify_alert_candidate("crypto_strategy", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "crypto_strategy_watch_only" in state["suppression_reasons"]
    assert "no_crypto_tradeable_signal" in state["suppression_reasons"]
    assert "no_crypto_execution_trigger" in state["suppression_reasons"]


def test_crypto_strategy_scan_does_not_email_snapshot_rows(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_strategy_scan_alerts("Low Cap Rockets", [{
        "Ticker": "PUMP",
        "grade": "S",
        "score": 95,
        "RVOL": 5.0,
        "Preis": 0.12,
        "Change_Pct": 34.0,
        "signal_quality": "watch_only",
        "execution_trigger_ok": False,
        "data_source": "CoinGecko markets",
    }], "crypto")

    assert sent == []


def _early_mover_row(**overrides):
    row = {
        "Symbol": "EMO",
        "Name": "Early Mover",
        "grade": "A",
        "score": 72,
        "Price": 1.25,
        "Change24h": 4.2,
        "VolMCapRatio": 8.5,
        "direction": "LONG",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "CONDITIONAL_LONG",
        "entry_quality": "GOOD",
        "execution_trigger_ok": True,
        "signal_quality": "conditional_long_setup",
        "entry": 1.25,
        "stop_loss": 1.15,
        "tp1": 1.43,
        "tp2": 1.57,
        "live_rr_ratio": 2.4,
        "distance_to_entry_r": 0,
        "late_to_tp1": False,
        "btc_context": {"btc_24h": 1.2, "alpha_24h": 3.0, "tailwind": True},
        "risk_flags": ["requires_5m_trigger"],
        "trade_setup": {
            "trade_action": "LONG_TRIGGER",
            "entry": 1.25,
            "stop_loss": 1.15,
            "tp1": 1.43,
            "tp2": 1.57,
            "live_rr": 2.4,
            "distance_to_entry_r": 0,
            "btc_context": {"btc_24h": 1.2, "alpha_24h": 3.0, "tailwind": True},
        },
    }
    row.update(overrides)
    return row


def test_early_mover_alert_audit_flattens_coins_and_allows_long_trigger(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "early_movers.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "coins": [
                _early_mover_row(),
                _early_mover_row(Symbol="CHASE", trade_action="NO_LONG_CHASE", signal_quality="no_chase", risk_flags=["overheated_phase3"]),
            ],
        }],
    }))

    audit = api._build_alert_audit_for_cache("early_movers", str(cache_file))

    assert audit["rows_checked"] == 2
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "EMO"
    assert audit["suppression_counts"]["early_mover_action_not_alertable"] == 1
    assert audit["suppression_counts"]["early_mover_no_chase"] == 1


def test_early_mover_retest_alert_requires_near_entry():
    near = _early_mover_row(
        Symbol="RETEST",
        trade_action="WAIT_FOR_RETEST",
        execution_trigger_ok=False,
        entry_status="WAIT_FOR_RETEST",
        entry_quality="EXTENDED",
        distance_to_entry_r=0.2,
        risk_flags=["no_market_entry"],
    )
    far = dict(near, Symbol="FAR", distance_to_entry_r=0.9, risk_flags=["no_market_entry", "chased_from_entry"])

    assert api._classify_alert_candidate("early_movers", near, 1_000_000.0)["alertable_now"] is True
    far_state = api._classify_alert_candidate("early_movers", far, 1_000_000.0)
    assert far_state["alertable_now"] is False
    assert "early_mover_retest_not_near_entry" in far_state["suppression_reasons"]
    assert "early_mover_chased_from_entry" in far_state["suppression_reasons"]


def test_early_mover_blocks_btc_headwind_and_partial_data():
    row = _early_mover_row(
        Symbol="HEADWIND",
        btc_context={"btc_24h": -3.5, "alpha_24h": 1.0, "tailwind": False},
        risk_flags=["btc_headwind", "data_warning"],
        data_warning="CoinGecko partial data",
    )

    state = api._classify_alert_candidate("early_movers", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "early_mover_btc_headwind" in state["suppression_reasons"]
    assert "early_mover_data_warning" in state["suppression_reasons"]


def test_early_mover_email_sends_trade_plan_and_dedupes(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    payload = {"coins": [_early_mover_row(Symbol="MAILME")]}

    api._send_early_mover_long_alerts(payload)
    api._send_early_mover_long_alerts(payload)

    assert len(sent) == 1
    assert "Crypto Early Mover LONG" in sent[0][0]
    assert "MAILME" in sent[0][1]
    assert "Entry" in sent[0][1]
    assert "BTC" in sent[0][1]


def test_early_mover_digest_cooldown_blocks_fresh_symbols(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_early_mover_long_alerts({"coins": [_early_mover_row(Symbol="FIRST")]})
    api._send_early_mover_long_alerts({"coins": [_early_mover_row(Symbol="SECOND")]})

    assert len(sent) == 1
    assert "FIRST" in sent[0][1]
    assert "SECOND" not in sent[0][1]
    status = api._email_dedupe_status(now=time.time())
    digest = [item for item in status["recent"] if item["key"] == api._EARLY_MOVER_DIGEST_KEY]
    assert digest
    assert 0 < digest[0]["remaining_seconds"] <= api._EARLY_MOVER_DIGEST_DEDUPE_SEC


def test_early_mover_digest_limits_mail_to_top_rows(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    rows = [
        _early_mover_row(Symbol=f"ROW{idx}", score=90 - idx, grade="S" if idx == 0 else "A")
        for idx in range(api._EARLY_MOVER_MAX_EMAIL_ROWS + 2)
    ]

    api._send_early_mover_long_alerts({"coins": rows})

    assert len(sent) == 1
    assert f"{api._EARLY_MOVER_MAX_EMAIL_ROWS}/{len(rows)}" in sent[0][0]
    assert "ROW0" in sent[0][1]
    assert f"ROW{api._EARLY_MOVER_MAX_EMAIL_ROWS + 1}" not in sent[0][1]
