import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import api


@pytest.fixture(autouse=True)
def _mock_common_stock_universe(monkeypatch, tmp_path, request):
    """Keep alert unit tests offline; individual asset-guard tests override this."""
    test_stocks = {
        "AAA", "BBB", "CCC", "DDD", "LATE", "RUNR", "MDRX", "REAL", "SKBL",
        "FRESH", "BOUNCE", "DROP", "FWRD", "SHORTY", "BADLONG", "BADRR",
        "MOMO", "ORB1", "DUP", "SSWP",
    }
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: (test_stocks, "unit"))
    if "stock_trade_email_status" not in request.node.name:
        monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {
            "allowed": True,
            "session": "US_REGULAR",
            "reason": "unit-test market open",
        })


def test_alert_audit_counts_alertable_and_suppressed(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "alerts.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [
            {"ticker": "AAA", "grade": "A", "score": 82, "rvol": 1.2, "price": 10, "direction": "LONG", "Entry": 10.0, "StopLoss": 9.5, "TP1": 10.8, "TP2": 11.3, "DayHigh": 10.4, "DayLow": 9.5, "latest_bar_change_pct": 0.2, "latest_bar_close_pos": 0.76},
            {"ticker": "BBB", "grade": "B", "score": 62, "rvol": 3.0, "price": 20, "direction": "LONG", "DayHigh": 20.5, "DayLow": 19.0},
            {"ticker": "CCC", "grade": "S", "score": 90, "rvol": 0.2, "price": 30, "direction": "LONG", "DayHigh": 31.0, "DayLow": 28.0},
            {"ticker": "DDD", "grade": "A", "score": 72, "rvol": 1.2, "price": 40, "direction": "LONG", "DayHigh": 41.0, "DayLow": 39.0},
        ],
    }))

    audit = api._build_alert_audit_for_cache("stock_strategy", str(cache_file))

    assert audit["rows_checked"] == 4
    assert audit["alertable_now_count"] == 1
    assert audit["grade_counts"]["A"] == 2
    assert audit["grade_counts"]["B"] == 1
    assert audit["grade_counts"]["S"] == 1
    assert audit["suppression_counts"]["grade_below_alert_threshold"] == 1
    assert audit["suppression_counts"]["rvol_below_alert_threshold"] == 1
    assert audit["suppression_counts"]["score_below_alert_threshold"] == 3
    assert audit["mail_status"] == "SEND_NOW"
    assert audit["decision_counts"]["TRADE_NOW"] == 1
    top_by_reason = {item["reason"]: item for item in audit["suppression_top"]}
    assert top_by_reason["score_below_alert_threshold"]["count"] == 3
    assert "Score unter" in top_by_reason["score_below_alert_threshold"]["label"]
    assert "score_below_alert_threshold=3" in audit["suppression_human"]
    assert "Score unter" in audit["suppression_human"]


def test_stock_trade_email_status_blocks_closed_us_market():
    status = api._stock_trade_email_status(datetime(2026, 6, 2, 5, 1, tzinfo=timezone.utc))

    assert status["allowed"] is False
    assert status["session"] in {"CLOSED", "UNKNOWN"}
    assert "open" in status["reason"].lower() or "geschlossen" in status["reason"].lower() or "closed" in status["reason"].lower()


def test_stock_trade_email_status_allows_regular_us_market():
    status = api._stock_trade_email_status(datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc))

    assert status["allowed"] is True
    assert status["session"] == "US_REGULAR"


def test_stock_strategy_mail_skips_when_us_market_closed(monkeypatch):
    sent_subjects = []
    skipped = []
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {
        "allowed": False,
        "reason": "US market closed unit-test",
        "session": "CLOSED",
    })
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent_subjects.append(subject) or True)
    monkeypatch.setattr(api, "_record_email_event", lambda subject, status, reason=None: skipped.append((subject, status, reason)))

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [{
        "Ticker": "AAA",
        "ticker": "AAA",
        "Strategy": "Momentum Breakout Long",
        "grade": "A",
        "score": 90,
        "Preis": 10.0,
        "RVOL": 2.0,
        "Change_Pct": 4.0,
        "direction": "LONG",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 11.0,
        "TP2": 12.0,
    }], "stocks")

    assert sent_subjects == []
    assert any(item[1] == "skipped" and "stock_market_closed" in str(item[2]) for item in skipped)


def test_stock_mail_blocks_watch_quality_rows(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"FULC"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *args, **kwargs: None)

    state = api._classify_alert_candidate("bear", {
        "Ticker": "FULC",
        "grade": "S",
        "score": 88,
        "RVOL": 6.1,
        "change_pct": -7.2,
        "close_pos": 0.2,
        "entry_quality": "WATCH",
        "trade_setup": {
            "direction": "SHORT",
            "entry": 6.42,
            "stop": 6.85,
            "tp1": 5.88,
            "tp2": 5.35,
        },
    }, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "entry_quality_watch_only" in state["suppression_reasons"]


def test_strategy_email_only_cooldowns_visible_rows(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({f"R{i:02d}" for i in range(12)}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *args, **kwargs: None)

    rows = []
    for idx in range(12):
        ticker = f"R{idx:02d}"
        rows.append({
            "Ticker": ticker,
            "grade": "A",
            "score": 90,
            "RVOL": 2.0,
            "Preis": 10.0 + idx,
            "current_price": 10.0 + idx,
            "change_pct": 3.5,
            "close_pos": 0.8,
            "Signal_Direction": "LONG",
            "trade_setup": {
                "direction": "LONG",
                "entry": 10.0 + idx,
                "stop": 9.5 + idx,
                "tp1": 10.75 + idx,
                "tp2": 11.0 + idx,
            },
        })

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert sent
    assert "Top 10 von 12" in sent[0][0]
    assert "Top 10 von 12" in sent[0][1]
    assert "R09" in sent[0][1]
    assert "R10" not in sent[0][1]
    assert "stock_strategy_R09" in api._EMAIL_COOLDOWN
    assert "stock_strategy_R10" not in api._EMAIL_COOLDOWN


def test_email_alert_audit_summary_explains_blockers(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_email_alert_status", lambda: {
        "configured": True,
        "startup_cooldown_remaining_seconds": 0,
    })
    blocked_file = tmp_path / "blocked.json"
    blocked_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [
            {"ticker": "BBB", "grade": "B", "score": 70, "rvol": 2.0, "price": 20},
            {"ticker": "CCC", "grade": "A", "score": 79, "rvol": 2.0, "price": 30},
        ],
    }))

    audit = api._build_alert_audit_for_cache("stock_strategy", str(blocked_file))
    summary = api._summarize_email_alert_audit({"stock_strategy": audit})

    assert summary["overall_status"] in {"ALL_BLOCKED_BY_GATES", "STARTUP_COOLDOWN"}
    assert summary["total_rows_checked"] == 2
    assert summary["total_alertable_now"] == 0
    top_by_reason = {item["reason"]: item for item in summary["top_blockers"]}
    assert top_by_reason["score_below_alert_threshold"]["count"] == 2
    assert "Score unter" in top_by_reason["score_below_alert_threshold"]["label"]
    assert summary["scanner_statuses"][0]["status"] == "BLOCKED"


def test_biotech_audit_adds_missing_trade_levels(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"BIOA"}, "unit"))
    cache_file = tmp_path / "biotech.json"
    monkeypatch.setattr(api, "BIOTECH_CACHE", str(cache_file))
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "Ticker": "BIOA",
            "Grade": "A",
            "Score": 86,
            "RVOL": 1.4,
            "Preis": 12.0,
            "latest_bar_change_pct": 0.2,
            "latest_bar_close_pos": 0.76,
            "Tech_Details": {
                "support": 11.4,
                "resistance": 12.8,
                "high_90d": 13.2,
                "low_90d": 9.5,
                "range_10d%": 8.0,
                "pos_90d": 68,
            },
        }],
    }))

    audit = api._build_alert_audit_for_cache("biotech", str(cache_file))

    assert audit["alertable_now_count"] == 1
    row = json.loads(cache_file.read_text())["results"][0]
    assert row["Signal_Direction"] == "LONG"
    assert row["Entry"] > row["StopLoss"]
    assert row["TP1"] > row["Entry"]
    assert row["TP2"] > row["TP1"]


def test_biotech_alert_persistent_dedupe_survives_restart(tmp_path, monkeypatch):
    api._EMAIL_SEND_LOG.clear()
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PFE"}, "unit"))
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, bypass_startup_cooldown=False: sent.append((subject, body)) or True)
    cache_file = tmp_path / "biotech.json"
    row = {
        "Ticker": "PFE",
        "Grade": "A",
        "Score": 94,
        "RVOL": 1.31,
        "Preis": 26.48,
        "Signal_Direction": "LONG",
        "Entry": 26.48,
        "StopLoss": 25.78,
        "TP1": 27.71,
        "TP2": 29.49,
        "latest_bar_change_pct": 0.2,
        "latest_bar_close_pos": 0.76,
    }
    cache_file.write_text(json.dumps({"cached_at": datetime.now().isoformat(), "results": [row]}))

    api._check_and_alert("biotech", str(cache_file))
    api._EMAIL_COOLDOWN.clear()  # Simulate server restart/deploy after the first mail.
    api._check_and_alert("biotech", str(cache_file))

    assert len(sent) == 1
    dedupe = json.loads((tmp_path / "email_dedupe.json").read_text())
    assert "biotech_PFE" in dedupe
    state_after_8h = api._classify_alert_candidate("biotech", row, time.time() + api._EMAIL_COOLDOWN_SEC + 60)
    assert state_after_8h["alertable_now"] is False
    assert "persistent_dedupe_active" in state_after_8h["suppression_reasons"]


def test_stock_alert_skip_reason_is_logged(tmp_path, monkeypatch):
    api._EMAIL_SEND_LOG.clear()
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"AAA"}, "unit"))
    cache_file = tmp_path / "bi_long.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "Ticker": "AAA",
            "BI_Grade": "B",
            "BI_Score": 74,
            "RVOL": 1.4,
            "Preis": 10.0,
            "BI_Direction": "LONG",
            "Entry": 10.0,
            "StopLoss": 9.5,
            "TP1": 10.8,
            "TP2": 11.3,
        }],
    }))

    api._check_and_alert("bi_long", str(cache_file))

    assert api._EMAIL_SEND_LOG[-1]["status"] == "skipped"
    assert api._EMAIL_SEND_LOG[-1]["subject"] == "bi_long Stock Alert"
    assert "no_alertable_stock_setups" in api._EMAIL_SEND_LOG[-1]["reason"]
    assert "grade_below_alert_threshold" in api._EMAIL_SEND_LOG[-1]["reason"]


def test_stock_strategy_swing_audit_does_not_block_on_latest_5m_fade(tmp_path):
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
    assert "latest_5m_red_fade" not in audit["suppression_counts"]
    assert "extended_long_fading_wait_retest" not in audit["suppression_counts"]


def test_intraday_long_alert_audit_allows_clean_momentum_continuation(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_DEFAULT_TRADE_HORIZON", "intraday")
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"RUNR"}, "unit"))
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
        "DayHigh": 25.0,
        "DayLow": 22.5,
        "price": 24.5,
        "Entry": 24.5,
        "StopLoss": 23.6,
        "TP1": 25.9,
        "TP2": 26.8,
    }
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [row],
    }))

    audit = api._build_alert_audit_for_cache("bi_long", str(cache_file))

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
                    "score": 84,
                    "rvol": 1.1,
                    "price": 12,
                    "direction": "SHORT",
                    "DayHigh": 13.2,
                    "DayLow": 11.4,
                    "change_pct": -6.0,
                    "open_to_current_pct": -5.0,
                    "close_pos": 0.2,
                    "latest_bar_change_pct": -0.2,
                    "latest_bar_close_pos": 0.2,
                    "alertable_short": True,
                    "Entry": 12.0,
                    "StopLoss": 12.8,
                    "TP1": 10.8,
                    "TP2": 10.0,
                }
            ],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "REAL"
    assert all(item["ticker"] != "LABD" for item in audit["alertable_preview"])


def test_bear_quality_counts_nested_stock_rows_and_explains_empty_cache():
    results = [{
        "inverse_etfs": [{"ticker": "LABD"}],
        "breakdown_stocks": [],
        "diagnostics": {
            "raw_candidates": 12,
            "excluded_non_common": 5,
            "dollar_volume_filtered": 4,
            "history_missing": 3,
            "processed_common_stocks": 0,
        },
    }]

    quality = api._scan_quality_payload("bear", cache_age_seconds=60, results=results)

    assert quality["result_count"] == 0
    assert "Keine Treffer im Cache" in quality["warnings"]
    assert any("keine echte Short-Aktie" in warning for warning in quality["warnings"])
    assert quality["diagnostics"]["raw_candidates"] == 12


def test_bear_alert_audit_blocks_overextended_green_reclaim(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear_late.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "breakdown_stocks": [{
                "ticker": "SKBL",
                "grade": "A",
                "score": 89,
                "rvol": 3.4,
                "price": 3.44,
                "direction": "SHORT",
                "DayHigh": 4.2,
                "DayLow": 3.3,
                "change_pct": -24.3,
                "open_to_current_pct": 1.2,
                "close_pos": 0.65,
            }],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["swing_short_drop_too_extended_no_chase"] == 1
    assert audit["suppression_counts"]["swing_short_current_candle_reclaim"] == 1
    assert audit["suppression_counts"]["swing_short_not_closing_weak"] == 1


def test_bear_alert_audit_allows_fresh_breakdown_near_lows(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear_fresh.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "breakdown_stocks": [{
                "ticker": "FRESH",
                "grade": "A",
                "score": 86,
                "rvol": 2.1,
                "price": 9.8,
                "direction": "SHORT",
                "DayHigh": 10.6,
                "DayLow": 9.6,
                "change_pct": -7.0,
                "open_to_current_pct": -6.4,
                "close_pos": 0.12,
                "latest_bar_change_pct": -0.2,
                "latest_bar_close_pos": 0.18,
                "Entry": 9.8,
                "StopLoss": 10.3,
                "TP1": 9.0,
                "TP2": 8.55,
            }],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "FRESH"


def test_bear_swing_alert_audit_ignores_latest_5m_reclaim_when_daily_state_is_weak(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"BOUNCE"}, "unit"))
    cache_file = tmp_path / "bear_5m_reclaim.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{
            "breakdown_stocks": [{
                "ticker": "BOUNCE",
                "grade": "A",
                "score": 86,
                "rvol": 2.1,
                "price": 9.8,
                "direction": "SHORT",
                "DayHigh": 10.6,
                "DayLow": 9.6,
                "change_pct": -7.0,
                "open_to_current_pct": -6.4,
                "close_pos": 0.12,
                "latest_bar_change_pct": 0.42,
                "latest_bar_close_pos": 0.82,
                "Entry": 9.8,
                "StopLoss": 10.3,
                "TP1": 9.0,
                "TP2": 8.55,
                "dollar_volume": 12_000_000,
            }],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert "latest_5m_green_reclaim" not in audit["suppression_counts"]


def test_stock_latest_intraday_state_ignores_unfinished_5m_candle(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            now_ms = int(time.time() * 1000)
            return {
                "results": [
                    {"t": now_ms - 900_000, "o": 10.0, "h": 10.2, "l": 9.9, "c": 10.05, "v": 1000},
                    {"t": now_ms - 360_000, "o": 10.0, "h": 10.0, "l": 9.6, "c": 9.7, "v": 1800},
                    {"t": now_ms - 60_000, "o": 9.7, "h": 10.2, "l": 9.7, "c": 10.15, "v": 9000},
                ]
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: FakeResponse())

    state = api._fetch_bear_latest_intraday_state("DROP")

    assert state["latest_bar_change_pct"] < 0
    assert state["latest_bar_close_pos"] < 0.4


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


def test_bear_crash_alert_allows_active_flush_despite_short_no_chase():
    fwrd_like = {
        "ticker": "FWRD",
        "grade": "S",
        "score": 100,
        "change_pct": -22.6,
        "open_to_current_pct": -22.2,
        "close_pos": 0.07,
        "latest_bar_change_pct": 0.0,
        "latest_bar_close_pos": 0.5,
        "rvol": 4.4,
        "short_block_reasons": ["drop_too_extended_no_chase"],
        "entry_quality": "NO_CHASE",
        "alertable_short": False,
    }

    assert api._bear_crash_alert_ok(fwrd_like) is True


def test_bear_crash_audit_is_separate_from_regular_short_alert(tmp_path):
    api._EMAIL_COOLDOWN.clear()
    cache_file = tmp_path / "bear.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "breakdown_stocks": [
            {
                "ticker": "FWRD",
                "grade": "S",
                "score": 100,
                "price": 16.5,
                "Change %": -22.6,
                "open_to_current_pct": -22.2,
                "close_pos": 0.07,
                "latest_bar_change_pct": 0.0,
                "latest_bar_close_pos": 0.5,
                "rvol": 4.4,
                "entry": 16.4,
                "stop": 17.2,
                "tp1": 15.0,
                "tp2": 14.0,
            }
        ],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["swing_short_drop_too_extended_no_chase"] == 1
    assert audit["crash_alertable_now_count"] == 1
    assert audit["crash_alertable_preview"][0]["ticker"] == "FWRD"


def test_alert_decision_labels_wait_retest_instead_of_no_trade():
    state = api._alert_decision_from_reasons("stock_strategy", ["hard_extended_long_wait_retest"])

    assert state["decision"] == "WAIT_RETEST"
    assert state["decision_label"] == "Auf Retest warten"


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


def test_email_guard_blocks_tradr_single_stock_etp():
    assert api._email_has_blocked_etf_content(
        "Crash Alert",
        "<td>IREZ</td><td>$12.12</td><td>-14.5%</td>",
    ) is True


def test_stock_alert_classifier_blocks_non_stock_products():
    state = api._classify_alert_candidate(
        "bear",
        {
            "ticker": "IREZ",
            "grade": "S",
            "score": 72,
            "price": 12.12,
            "rvol": 0.3,
            "change_pct": -14.5,
            "open_to_current_pct": -12.0,
            "close_pos": 0.1,
        },
        now=1_000_000.0,
    )

    assert state["alertable_now"] is False
    assert state["decision"] == "NO_TRADE"
    assert state["asset_exclusion_reason"] == "known ETF/ETP ticker"
    assert "non_common_stock_product" in state["suppression_reasons"]


def test_stock_alert_asset_guard_uses_common_stock_universe():
    assert api._stock_alert_asset_exclusion_reason(
        "REAL",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) is None
    assert api._stock_alert_asset_exclusion_reason(
        "IREZ",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "known ETF/ETP ticker"
    assert api._stock_alert_asset_exclusion_reason(
        "NVBD",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "not in common-stock universe (unit)"
    assert api._stock_alert_asset_exclusion_reason(
        "NVDG",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "not in common-stock universe (unit)"
    assert api._stock_alert_asset_exclusion_reason(
        "NVDB",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "not in common-stock universe (unit)"
    assert api._stock_alert_asset_exclusion_reason(
        "BATT",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "not in common-stock universe (unit)"
    assert api._stock_alert_asset_exclusion_reason(
        "CORZZ",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "not in common-stock universe (unit)"
    assert api._stock_alert_asset_exclusion_reason(
        "FAKEETF",
        common_stock_universe={"REAL"},
        universe_source="unit",
    ) == "not in common-stock universe (unit)"


def test_strategy_scan_decoration_filters_single_stock_etps(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda: ({"REAL"}, "unit"))
    rows = [
        {"Ticker": "NVBD", "score": 99, "grade": "S"},
        {"Ticker": "NVDB", "score": 99, "grade": "S"},
        {"Ticker": "NVDG", "score": 99, "grade": "S"},
        {"Ticker": "BATT", "score": 99, "grade": "S"},
        {"Ticker": "KSTR", "score": 99, "grade": "S"},
        {"Ticker": "LEUX", "score": 99, "grade": "S"},
        {"Ticker": "CORZZ", "score": 99, "grade": "S"},
        {"Ticker": "NOTREAL", "score": 99, "grade": "S"},
        {"Ticker": "REAL", "score": 80, "grade": "S"},
    ]

    decorated = api._decorate_scan_results(rows, "strategy_scan", cache_age_seconds=10)

    assert [row["Ticker"] for row in decorated] == ["REAL"]


def test_reference_type_blocks_etfs_without_ticker_blacklist(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "results": {
                    "ticker": "NVDB",
                    "market": "stocks",
                    "type": "ETF",
                    "name": "ProShares UltraShort NVIDIA ETF",
                }
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: FakeResponse())
    api._ORB_REFERENCE_CACHE.clear()

    assert "NVDB" not in api.NON_STOCK_ETP_TICKERS
    assert api._stock_alert_asset_exclusion_reason("NVDB", require_reference=True) == "type=ETF"


def test_reference_name_keyword_uses_whole_words():
    assert api._reference_asset_exclusion_reason("CS", "Fundamental Global Inc", "stocks") is None
    assert api._reference_asset_exclusion_reason("CS", "Example Acquisition Warrant", "stocks") == "non-stock product keyword"


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


def test_alert_trade_levels_derive_missing_targets_from_entry_stop():
    levels = api._alert_trade_levels({
        "Ticker": "SHORTY",
        "direction": "SHORT",
        "Entry": 10.0,
        "StopLoss": 11.0,
    })

    assert levels["entry"] == 10.0
    assert levels["stop"] == 11.0
    assert levels["tp1"] == 8.5
    assert levels["tp2"] == 7.5
    assert levels["rr"] == 2.0
    assert levels["valid"] is True
    assert levels["estimated"] is True


def test_estimated_trade_levels_do_not_pass_active_email_gate():
    row = {
        "ticker": "FWRD",
        "grade": "S",
        "score": 92,
        "rvol": 2.1,
        "price": 17.33,
        "direction": "SHORT",
        "change_pct": -5.6,
        "open_to_current_pct": -5.6,
        "close_pos": 0.18,
        "Entry": 17.33,
        "StopLoss": 17.91,
    }

    state = api._classify_alert_candidate("bear", row)

    assert state["alertable_now"] is False
    assert "estimated_trade_plan" in state["suppression_reasons"]
    assert api._alert_trade_plan_ok(row) is False


def test_bear_structure_levels_are_native_and_can_pass_email_gate():
    setup = api._build_bear_structure_trade_setup(
        entry=17.33,
        day_high=18.15,
        day_low=16.72,
        day_open=17.95,
        ma20=18.05,
        ma50=19.20,
        low_20d=15.20,
        low_60d=13.40,
        change_pct=-6.5,
    )
    row = {
        "ticker": "FWRD",
        "grade": "S",
        "score": 88,
        "rvol": 2.1,
        "price": 17.33,
        "direction": "SHORT",
        "change_pct": -6.5,
        "open_to_current_pct": -4.0,
        "close_pos": 0.18,
        "latest_bar_change_pct": -0.2,
        "latest_bar_close_pos": 0.18,
        **setup,
    }

    levels = api._alert_trade_levels(row)
    state = api._classify_alert_candidate("bear", row)

    assert levels["valid"] is True
    assert levels["native"] is True
    assert levels["estimated"] is False
    assert levels["direction"] == "SHORT"
    assert state["alertable_now"] is True
    assert "estimated_trade_plan" not in state["suppression_reasons"]


def test_alert_trade_levels_reject_inverted_long_targets():
    levels = api._alert_trade_levels({
        "Ticker": "BADLONG",
        "direction": "LONG",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 9.8,
        "TP2": 11.5,
    })

    assert levels["valid"] is False
    assert levels["rr"] is None
    assert "invalid_long_tp1" in levels["errors"]


def test_alert_classifier_blocks_invalid_trade_geometry():
    api._EMAIL_COOLDOWN.clear()
    state = api._classify_alert_candidate("stock_strategy", {
        "ticker": "BADRR",
        "grade": "A",
        "score": 90,
        "rvol": 1.5,
        "price": 10.0,
        "direction": "LONG",
        "entry": 10.0,
        "stop": 9.5,
        "tp1": 9.8,
        "tp2": 11.5,
    }, now=1_000_000.0)

    assert state["alertable_now"] is False
    assert "invalid_trade_plan" in state["suppression_reasons"]


def test_generic_scanner_email_includes_entry_stop_tp1_tp2(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    cache_file = tmp_path / "orb_alert.json"
    cache_file.write_text(json.dumps({
        "results": [{
            "ticker": "REAL",
            "grade": "A",
            "score": 88,
            "rvol": 1.4,
            "price": 20.25,
            "direction": "LONG",
            "entry": 20.5,
            "stop": 19.5,
            "target1": 22.0,
            "target2": 23.5,
        }]
    }))

    api._check_and_alert("orb", str(cache_file))

    assert len(sent) == 1
    body = sent[0][1]
    assert "Entry" in body
    assert "Stop" in body
    assert "TP1/TP2" in body
    assert "$20.5" in body
    assert "$19.5" in body
    assert "$22" in body
    assert "$23.5" in body


def test_strategy_scan_email_includes_entry_stop_tp1_tp2(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_strategy_scan_alerts("Momentum Breakout Long", [{
        "Ticker": "MOMO",
        "grade": "A",
        "score": 91,
        "RVOL": 2.2,
        "Preis": 12.4,
        "Change_Pct": 5.5,
        "Signal_Direction": "LONG",
        "change_pct": 5.5,
        "close_pos": 0.88,
        "latest_bar_change_pct": 0.15,
        "latest_bar_close_pos": 0.74,
        "trade_setup": {
            "direction": "LONG",
            "entry": 12.5,
            "stop": 11.9,
            "tp1": 13.4,
            "tp2": 14.0,
        },
    }], "stocks")

    assert len(sent) == 1
    body = sent[0][1]
    assert "Momentum Breakout Long" in body
    assert "Entry" in body
    assert "Stop" in body
    assert "TP1/TP2" in body
    assert "$12.5" in body
    assert "$11.9" in body
    assert "$13.4" in body
    assert "$14" in body


def test_stock_strategy_sweep_is_scheduled_and_tracked():
    source = Path("api.py").read_text(encoding="utf-8")
    assert "_AUTO_STOCK_ALERT_STRATEGIES" in source
    assert '("strategy_scan", _stock_strategy_alert_sweep_wrapper)' in source
    assert '"strategy_scan": "/tmp/strategy_scan_cache.json"' in source
    assert '"strategy_scan": {"running": False, "last_run": None, "next_run": None, "interval_min": 30}' in source


def test_strategy_sweep_email_keeps_row_strategy_name(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [{
        "Ticker": "SSWP",
        "Strategy": "Gap Momentum Long",
        "grade": "A",
        "score": 92,
        "RVOL": 2.6,
        "Preis": 15.2,
        "Change_Pct": 6.1,
        "Signal_Direction": "LONG",
        "change_pct": 6.1,
        "close_pos": 0.84,
        "latest_bar_change_pct": 0.18,
        "latest_bar_close_pos": 0.76,
        "trade_setup": {
            "direction": "LONG",
            "entry": 15.2,
            "stop": 14.4,
            "tp1": 16.5,
            "tp2": 17.4,
        },
    }], "stocks")

    assert len(sent) == 1
    assert "Gap Momentum Long" in sent[0][1]


def test_stock_strategy_email_is_labeled_as_swing_not_intraday(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"SWNG"}, "unit"))
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [{
        "Ticker": "SWNG",
        "Strategy": "Momentum Breakout Long",
        "grade": "A",
        "score": 90,
        "RVOL": 2.1,
        "Preis": 12.3,
        "Change_Pct": 4.2,
        "Signal_Direction": "LONG",
        "change_pct": 4.2,
        "close_pos": 0.82,
        "latest_bar_change_pct": 0.12,
        "latest_bar_close_pos": 0.72,
        "trade_setup": {
            "direction": "LONG",
            "entry": 12.3,
            "stop": 11.8,
            "tp1": 13.2,
            "tp2": 14.0,
        },
    }], "stocks")

    assert len(sent) == 1
    subject, body = sent[0]
    assert "Aktien Strategie Swing" in subject
    assert "Swing-Setup: mehrtaegiger Plan" in body
    assert "Intraday-Trigger sind optional" in body
    assert "frische 5m-Bestaetigung" not in body


def test_stock_strategy_swing_email_does_not_fetch_or_require_5m(monkeypatch):
    row = {
        "Ticker": "SWNG",
        "grade": "A",
        "score": 90,
        "RVOL": 2.1,
        "Preis": 12.3,
        "current_price": 12.3,
        "Signal_Direction": "LONG",
        "change_pct": 4.2,
        "close_pos": 0.82,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "dollar_volume": 8_000_000,
        "trade_setup": {
            "direction": "LONG",
            "entry": 12.3,
            "stop": 11.8,
            "tp1": 13.2,
            "tp2": 14.0,
        },
    }
    monkeypatch.setattr(
        api,
        "_fetch_long_latest_intraday_state",
        lambda ticker: (_ for _ in ()).throw(AssertionError("Swing stock strategy must not fetch 5m bars")),
    )

    enriched = api._enrich_stock_alert_5m_state("stock_strategy", row, "Momentum Breakout Long")
    state = api._classify_alert_candidate("stock_strategy", enriched, 1_000_000.0)

    assert enriched["entry_quality"] == "SWING_SETUP"
    assert "latest_bar_change_pct" not in enriched
    assert "latest_5m_red_fade" not in state["suppression_reasons"]
    assert "fresh_5m_state_missing_wait_retest" not in state["suppression_reasons"]


def test_strategy_scan_failed_email_does_not_set_cooldown(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: False)

    api._send_strategy_scan_alerts("Momentum Breakout Long", [{
        "Ticker": "MOMO",
        "grade": "A",
        "score": 91,
        "RVOL": 2.2,
        "Preis": 12.4,
        "Change %": 3.0,
        "Signal_Direction": "LONG",
        "Entry": 12.4,
        "StopLoss": 11.8,
        "TP1": 13.4,
        "TP2": 14.0,
        "latest_bar_change_pct": 0.2,
        "latest_bar_close_pos": 0.76,
    }], "stocks")

    assert "stock_strategy_MOMO" not in api._EMAIL_COOLDOWN


def test_strategy_scan_dedupes_same_ticker_inside_one_mail(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    row = {
        "Ticker": "MOMO",
        "grade": "A",
        "score": 91,
        "RVOL": 2.2,
        "Preis": 12.4,
        "Change %": 3.0,
        "Signal_Direction": "LONG",
        "Entry": 12.4,
        "StopLoss": 11.8,
        "TP1": 13.4,
        "TP2": 14.0,
        "latest_bar_change_pct": 0.2,
        "latest_bar_close_pos": 0.76,
    }

    api._send_strategy_scan_alerts("Momentum Breakout Long", [dict(row), dict(row)], "stocks")

    assert len(sent) == 1
    assert sent[0][1].count("<b>MOMO</b>") == 1


def test_alert_classifier_respects_cooldown():
    api._EMAIL_COOLDOWN.clear()
    now = 1_000_000.0
    row = {
        "ticker": "ORB1", "grade": "A", "score": 80, "price": 12, "direction": "LONG",
        "Entry": 12.0, "StopLoss": 11.4, "TP1": 12.9, "TP2": 13.5,
        "DayHigh": 12.4, "DayLow": 11.4,
    }

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
    short_row = {
        "Ticker": "DUP", "BI_Grade": "A", "BI_Score": 100, "RVOL": 1.4, "Preis": 12,
        "BI_Direction": "SHORT", "Entry": 12.0, "StopLoss": 12.6, "TP1": 11.1, "TP2": 10.5,
        "latest_bar_change_pct": -0.2, "latest_bar_close_pos": 0.2,
    }
    long_row = {
        "Ticker": "DUP", "BI_Grade": "A", "BI_Score": 100, "RVOL": 1.4, "Preis": 12,
        "BI_Direction": "LONG", "Entry": 12.0, "StopLoss": 11.4, "TP1": 12.9, "TP2": 13.5,
        "latest_bar_change_pct": 0.2, "latest_bar_close_pos": 0.76,
    }

    api._mark_bearish_stock_alert("DUP", now=now)

    short_state = api._classify_alert_candidate("bi_short", short_row, now + 60)
    long_state = api._classify_alert_candidate("bi_long", long_row, now + 60)

    assert short_state["alertable_now"] is False
    assert "bearish_ticker_already_alerted" in short_state["suppression_reasons"]
    assert long_state["alertable_now"] is True


def test_bi_short_alert_blocks_late_crash_chase():
    row = {
        "Ticker": "LATE",
        "BI_Grade": "A",
        "BI_Score": 90,
        "RVOL": 3.2,
        "Preis": 3.4,
        "change_pct": -24.0,
        "close_pos": 0.22,
        "latest_bar_change_pct": 0.8,
        "latest_bar_close_pos": 0.7,
    }

    state = api._classify_alert_candidate("bi_short", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert state["decision"] == "NO_TRADE"
    assert "swing_short_drop_too_extended_no_chase" in state["suppression_reasons"]
    assert "latest_5m_green_reclaim" not in state["suppression_reasons"]


def test_extended_long_requires_fresh_intraday_state_for_continuation():
    row = {
        "ticker": "RUNR",
        "grade": "A",
        "score": 86,
        "rvol": 2.8,
        "price": 24.5,
        "change_pct": 18.0,
        "close_pos": 0.91,
        "open_to_current_pct": 8.5,
        "Extension_ATR": 4.5,
        "Signal_Direction": "LONG",
    }

    assert api._long_entry_quality(row) == "WAIT_RETEST"


def test_extended_long_legacy_change_column_uses_no_chase_gate():
    row = {
        "Ticker": "RUNR",
        "Grade": "A",
        "Score": 86,
        "RVOL": 2.8,
        "Price": 24.5,
        "Change %": 18.0,
        "Close Position": 0.91,
        "Open_To_Current_Pct": 8.5,
        "Extension_ATR": 4.5,
        "Signal_Direction": "LONG",
    }

    assert "fresh_5m_state_missing_wait_retest" in api._long_entry_rule_reasons(row)
    assert api._long_entry_quality(row) == "WAIT_RETEST"


def test_new_listing_pipeline_alerts_only_active_top_grades(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
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
                    "tp2": 0.6,
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
                    "pump_data": {
                        "micro_score": 75,
                        "micro_trigger_ok": True,
                        "btc_change_pct": 1.2,
                        "coin_change_pct": -4.4,
                        "btc_divergence": -5.6,
                    },
                    "exh_score": 85,
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
    assert "BTC 1.2%" in sent[0][1]
    assert "Coin -4.4%" in sent[0][1]
    assert "Div -5.6%" in sent[0][1]
    assert "LOW" not in sent[0][1]
    assert "WATCH" not in sent[0][1]
    assert "RISK" not in sent[0][1]


def test_new_listing_pipeline_sends_dump_watch_when_no_short_now(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)
    monkeypatch.setattr(api, "_NEW_LISTING_SEND_DUMP_WATCH_EMAILS", True)

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
                    "exh_score": 84,
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
    assert "Crypto New Listing Dump-Watch" in sent[0][0]
    assert "NICHT SHORTEN" in sent[0][0]
    assert "BABY" in sent[0][1]
    assert "Chart aktiv beobachten" in sent[0][1]


def test_new_listing_dump_watch_can_be_disabled(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    events = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_record_email_event", lambda subject, status, reason="": events.append((subject, status, reason)))
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)
    monkeypatch.setattr(api, "_NEW_LISTING_SEND_DUMP_WATCH_EMAILS", False)

    payload = {
        "signals": [],
        "monitoring": [{
            "symbol": "BABYUSDT",
            "exchange": "mexc",
            "source": "new_listing",
            "listing_age_hours": 18,
            "trade_category": "NEW_LISTING_WATCH",
            "grade": "A",
            "exh_score": 84,
            "pump_pct": 90,
            "from_ath_pct": 3,
            "rr_effective": 2.0,
            "risk_flags": [],
        }],
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert sent == []
    assert events[-1] == ("Crypto New Listing Dump-Watch", "skipped", "new_listing_dump_watch_emails_disabled")


def test_new_listing_pipeline_does_not_send_low_score_watch_mail(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)

    payload = {
        "signals": [],
        "watchlist": [],
        "monitoring": [{
            "symbol": "STARUSDT",
            "exchange": "mexc",
            "source": "new_listing",
            "listing_age_hours": 18,
            "trade_category": "NEW_LISTING_WATCH",
            "timing": "Watch - waiting for first real dump trigger",
            "grade": "C",
            "exh_score": 67,
            "pump_pct": 18,
            "from_ath_pct": 4,
            "rr_effective": 1.4,
            "risk_flags": ["wait_for_dump_trigger"],
        }],
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert sent == []


def test_new_listing_pipeline_does_not_mail_unpumped_new_listing(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)

    payload = {
        "signals": [],
        "watchlist": [],
        "monitoring": [{
            "symbol": "QUIETUSDT",
            "exchange": "mexc",
            "source": "new_listing",
            "listing_age_hours": 6,
            "trade_category": "NEW_LISTING_WATCH",
            "timing": "Listed, still waiting for pump",
            "grade": "C",
            "exh_score": 60,
            "pump_pct": 4,
            "from_ath_pct": 0.5,
            "risk_flags": ["pump_too_small"],
        }],
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert sent == []


def test_new_listing_pipeline_does_not_mail_pure_exchange_announcement(monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl_seconds, now=None: True)

    payload = {
        "signals": [],
        "watchlist": [],
        "monitoring": [],
        "announcement_watchlist": [{
            "base": "HOOLI",
            "exchange": "bitget",
            "source": "bitget_announcement",
            "title": "[Initial listing] Bitget to list Hooli (HOOLI) in the GameFi zone",
            "url": "https://example.com/hooli",
            "age_hours": 2,
            "matched_contracts": [{"exchange": "bitget", "symbol": "HOOLIUSDT"}],
        }],
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert sent == []


def test_new_listing_watch_ignores_active_pump_rows(monkeypatch):
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
                "exh_score": 86,
                "entry": 1.0,
                "stop_loss": 1.2,
                "tp1": 0.7,
                "tp2": 0.5,
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
            "exh_score": 86,
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
            "exh_score": 86,
        }],
    }))

    audit = api._build_alert_audit_for_cache("new_listing", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 0
    assert audit["suppression_counts"]["not_new_listing_dump"] == 1
    assert audit["suppression_counts"]["listing_age_not_tradeable"] == 1


def test_new_listing_flatten_uses_monitoring_candle_price_and_dedupes_zero_rows():
    payload = {
        "monitoring": [
            {
                "symbol": "AZTECUSDT",
                "exchange": "binance",
                "price": 0,
                "pump_pct": 11.2,
                "from_ath_pct": 23.6,
                "exh_score": 38,
                "trade_category": "EXHAUSTION_WATCH",
            },
            {
                "symbol": "AZTECUSDT",
                "exchange": "binance",
                "price": 0.02173,
                "pump_pct": 11.2,
                "from_ath_pct": 23.6,
                "exh_score": 38,
                "trade_category": "EXHAUSTION_WATCH",
            },
        ]
    }

    rows = api._flatten_new_listing_pipeline_results(payload)

    assert len(rows) == 1
    assert rows[0]["symbol"] == "AZTEC"
    assert rows[0]["price"] == 0.02173
    assert rows[0]["trade_category"] == "EXHAUSTION_WATCH"


def test_new_listing_ui_keeps_dump_watch_but_hides_pure_announcements():
    rows = [
        {
            "symbol": "WATCH",
            "exchange": "mexc",
            "price": 1.25,
            "pump_pct": 28,
            "from_ath_pct": 12,
            "exhaustion_score": 46,
            "trade_category": "EXHAUSTION_WATCH",
            "source": "monitoring",
            "trade_action": "BEOBACHTEN",
            "trade_signal": "BEOBACHTEN",
            "grade": "C",
        },
        {
            "symbol": "HEADLINE",
            "exchange": "bitget",
            "price": 0,
            "pump_pct": 0,
            "from_ath_pct": 0,
            "exhaustion_score": 0,
            "trade_category": "ANNOUNCEMENT_WATCH",
            "source": "announcement",
            "trade_action": "BEOBACHTEN",
            "trade_signal": "BEOBACHTEN",
            "grade": "WATCH",
        },
    ]

    visible, stats = api._decorate_new_listing_display_results(rows, cache_age_seconds=10)

    assert [row["symbol"] for row in visible] == ["WATCH"]
    assert stats["visible_watch_rows"] == 1
    assert stats["tradeable_short_signals"] == 0
    assert stats["hidden_announcement_rows"] == 1


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
            "exh_score": "86",
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
        "score": 86,
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
        "risk_flags": [],
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
                _early_mover_row(
                    trade_signal="JETZT_TRADEN",
                    signal_quality="tradeable",
                    execution_trigger_ok=True,
                    execution_quality_score=92,
                    alertable_crypto=True,
                    entry_score=88,
                ),
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
        _alert_horizon="intraday",
        trade_action="WAIT_FOR_RETEST",
        execution_trigger_ok=False,
        entry_status="WAIT_FOR_RETEST",
        entry_quality="EXTENDED",
        entry_score=88,
        execution_quality_score=88,
        distance_to_entry_r=0.2,
        risk_flags=["no_market_entry"],
    )
    far = dict(near, Symbol="FAR", distance_to_entry_r=0.9, risk_flags=["no_market_entry", "chased_from_entry"])

    near_state = api._classify_alert_candidate("early_movers", near, 1_000_000.0)
    assert near_state["alertable_now"] is False
    assert near_state["decision"] == "WAIT_TRIGGER"
    assert "early_mover_wait_entry_confirmation" in near_state["suppression_reasons"]
    far_state = api._classify_alert_candidate("early_movers", far, 1_000_000.0)
    assert far_state["alertable_now"] is False
    assert "early_mover_retest_not_near_entry" in far_state["suppression_reasons"]
    assert "early_mover_chased_from_entry" in far_state["suppression_reasons"]


def test_early_mover_zero_r_distance_stays_near_entry():
    row = _early_mover_row(
        Symbol="ZERO",
        _alert_horizon="intraday",
        trade_action="WAIT_FOR_RETEST",
        execution_trigger_ok=False,
        entry_status="WAIT_FOR_RETEST",
        entry_quality="EXTENDED",
        entry_score=88,
        execution_quality_score=88,
        distance_to_entry_r=0,
        risk_flags=["no_market_entry"],
    )

    state = api._classify_alert_candidate("early_movers", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert state["decision"] == "WAIT_TRIGGER"
    assert "early_mover_wait_entry_confirmation" in state["suppression_reasons"]
    assert "early_mover_retest_not_near_entry" not in state["suppression_reasons"]


def test_stock_alert_trade_health_blocks_chased_live_entry():
    row = {
        "ticker": "MOMO",
        "grade": "A",
        "score": 90,
        "rvol": 2.4,
        "price": 11.7,
        "current_price": 11.7,
        "direction": "LONG",
        "Entry": 10.0,
        "StopLoss": 9.2,
        "TP1": 12.0,
        "TP2": 12.4,
        "DayHigh": 12.0,
        "DayLow": 9.8,
        "latest_bar_change_pct": 0.2,
        "latest_bar_close_pos": 0.7,
    }

    state = api._classify_alert_candidate("stock_strategy", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "trade_health_chase_risk" in state["suppression_reasons"]
    assert state["decision"] == "NO_TRADE"


def test_intraday_long_alert_score_is_capped_when_fresh_5m_trigger_is_missing():
    row = {
        "ticker": "RUNR",
        "grade": "S",
        "score": 96,
        "rvol": 2.8,
        "price": 24.5,
        "current_price": 24.5,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "change_pct": 18.0,
        "close_pos": 0.91,
        "Extension_ATR": 4.5,
        "Entry": 24.5,
        "StopLoss": 23.6,
        "TP1": 25.9,
        "TP2": 26.8,
    }

    state = api._classify_alert_candidate("bi_long", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert state["score"] < api._ALERT_MIN_SCORE
    assert "score_below_alert_threshold" in state["suppression_reasons"]


def test_intraday_stock_alert_score_keeps_clean_confirmed_continuation_alertable(monkeypatch):
    monkeypatch.setattr(api, "_DEFAULT_TRADE_HORIZON", "intraday")
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"RUNR"}, "unit"))
    row = {
        "ticker": "RUNR",
        "grade": "A",
        "score": 90,
        "rvol": 2.8,
        "price": 24.5,
        "current_price": 24.5,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "change_pct": 18.0,
        "close_pos": 0.91,
        "open_to_current_pct": 8.5,
        "latest_bar_change_pct": 0.35,
        "latest_bar_close_pos": 0.82,
        "Extension_ATR": 4.5,
        "Entry": 24.5,
        "StopLoss": 23.6,
        "TP1": 25.9,
        "TP2": 26.8,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "dollar_volume": 12_000_000,
    }

    state = api._classify_alert_candidate("bi_long", row, 1_000_000.0)

    assert state["alertable_now"] is True
    assert state["score"] >= api._ALERT_MIN_SCORE


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


def test_early_mover_blocks_extreme_turnover_without_alpha():
    row = _early_mover_row(
        Symbol="GALA",
        Change24h=0.7,
        VolMCapRatio=96.0,
        btc_context={"btc_24h": 1.0, "alpha_24h": -0.3, "tailwind": True},
    )

    state = api._classify_alert_candidate("early_movers", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "early_mover_turnover_without_alpha" in state["suppression_reasons"]


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
    assert "V/MCap 8.5%" in sent[0][1]
    assert "Swing-Struktur" in sent[0][1]


def test_early_mover_digest_cooldown_blocks_fresh_symbols(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda row: {
        "ok": True,
        "reason": "5m_breakout_volume_confirmed",
        "volume_ratio": 1.6,
    })
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
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda row: {
        "ok": True,
        "reason": "5m_breakout_volume_confirmed",
        "volume_ratio": 1.6,
    })
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


def test_early_mover_email_requires_realtime_5m_trigger(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_DEFAULT_TRADE_HORIZON", "intraday")
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda row: {
        "ok": False,
        "reason": "no_fresh_5m_trigger",
    })
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_early_mover_long_alerts({"coins": [_early_mover_row(Symbol="OBSERVEONLY")]})

    assert sent == []


def test_early_mover_email_blocks_1m_only_trigger(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    api._EMAIL_SEND_LOG.clear()
    monkeypatch.setattr(api, "_DEFAULT_TRADE_HORIZON", "intraday")
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda row: {
        "ok": True,
        "reason": "adaptive_1m_retest_hold",
        "timeframe": "1m",
        "execution_score": 100,
        "volume_ratio": 2.8,
    })
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_early_mover_long_alerts({"coins": [_early_mover_row(Symbol="GALA")]})

    assert sent == []
    assert api._EMAIL_SEND_LOG[-1]["status"] == "skipped"
    assert "early_mover_1m_trigger_disabled" in api._EMAIL_SEND_LOG[-1]["reason"]


def test_early_mover_email_checks_realtime_trigger_when_cache_unconfirmed(tmp_path, monkeypatch):
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_DEFAULT_TRADE_HORIZON", "intraday")
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda row: {
        "ok": True,
        "reason": "5m_breakout_volume_confirmed",
        "volume_ratio": 1.8,
    })
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    api._send_early_mover_long_alerts({"coins": [
        _early_mover_row(Symbol="LIVEOK", execution_trigger_ok=False)
    ]})

    assert len(sent) == 1
    assert "LIVEOK" in sent[0][1]
    assert "5m_breakout_volume_confirmed" in sent[0][1]


def test_early_mover_signal_state_only_marks_trade_now_after_trigger():
    row = _early_mover_row(execution_trigger_ok=False)

    api._apply_early_mover_signal_state(row, {"ok": False, "reason": "no_fresh_5m_trigger"})

    assert row["trade_signal"] == "WARTEN"
    assert row["entry_status"] == "WAIT_FOR_TRIGGER"
    assert row["alertable_crypto"] is False
    assert row["execution_trigger_ok"] is False

    api._apply_early_mover_signal_state(row, {"ok": True, "reason": "5m_vwap_reclaim"})

    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["alertable_crypto"] is True


def test_early_mover_signal_state_keeps_1m_trigger_as_wait():
    row = _early_mover_row(execution_trigger_ok=False)

    api._apply_early_mover_signal_state(row, {
        "ok": True,
        "reason": "adaptive_1m_retest_hold",
        "timeframe": "1m",
        "execution_score": 100,
    })

    assert row["trade_signal"] == "WARTEN"
    assert row["entry_status"] == "WAIT_FOR_TRIGGER"
    assert row["alertable_crypto"] is False
    assert row["execution_trigger_ok"] is False
    assert "Entry-Bestaetigung" in row["signal_label"]


def test_trade_reminder_triggers_early_mover_email(tmp_path, monkeypatch):
    reminder_file = tmp_path / "trade_reminders.json"
    monkeypatch.setattr(api, "_TRADE_REMINDERS_FILE", str(reminder_file))
    monkeypatch.setattr(api, "_reminder_now", lambda: 1_000_000.0)
    row = _early_mover_row(Symbol="BROCCOLI")
    monkeypatch.setattr(api, "_find_early_mover_row", lambda symbol: row)
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda row: {
        "ok": True,
        "reason": "5m_breakout_volume_confirmed",
        "last_close": 1.31,
        "volume_ratio": 1.7,
    })
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, bypass_startup_cooldown=False: sent.append((subject, body, bypass_startup_cooldown)) or True)

    api._save_trade_reminders([{
        "id": "rem1",
        "ticker": "BROCCOLI",
        "asset_type": "crypto",
        "scanner": "early_movers",
        "condition": "trigger_or_retest",
        "channel": "email_browser",
        "status": "active",
        "row": row,
        "created_at": api._reminder_iso(999_000.0),
        "expires_at": 1_010_000.0,
        "expires_at_iso": api._reminder_iso(1_010_000.0),
        "last_checked_at": 0,
    }])

    api._process_trade_reminders_once()
    reminders = api._load_trade_reminders()

    assert reminders[0]["status"] == "triggered"
    assert reminders[0]["trigger_result"]["reason"] == "5m_breakout_volume_confirmed"
    assert sent and sent[0][2] is True


def test_new_listing_watch_mail_blocks_cross_exchange_announcement_mismatch():
    payload = {
        "monitoring": [{
            "symbol": "UP_USDT",
            "exchange": "mexc",
            "source": "new_listing",
            "announcement_source": "bitget_announcement",
            "announcement_title": "Bitget to list Superform (UP)",
            "trade_category": "NEW_LISTING_WATCH",
            "listing_age_hours": 20.1,
            "pump_pct": 24.8,
            "from_ath_pct": 3.7,
            "exh_score": 55,
            "rr_effective": 5.0,
            "risk_flags": [],
        }]
    }

    assert api._new_listing_watch_candidates(payload) == []


def test_new_listing_watch_mail_requires_score_and_safety_ok():
    payload = {
        "monitoring": [{
            "symbol": "LOW_USDT",
            "exchange": "mexc",
            "source": "new_listing",
            "trade_category": "NEW_LISTING_WATCH",
            "listing_age_hours": 20.1,
            "pump_pct": 24.8,
            "from_ath_pct": 3.7,
            "exh_score": 21,
            "rr_effective": 5.0,
            "risk_flags": ["safety_failed", "early_crack_score_too_low", "micro_trigger_missing"],
        }]
    }

    assert api._new_listing_watch_candidates(payload) == []


def test_new_listing_watch_mail_blocks_low_rr_confusing_watch():
    payload = {
        "monitoring": [{
            "symbol": "GENIUS_USDT",
            "exchange": "binance",
            "source": "new_listing",
            "trade_category": "NEW_LISTING_WATCH",
            "listing_age_hours": 60.9,
            "pump_pct": 27.8,
            "from_ath_pct": 15.3,
            "exh_score": 72,
            "rr_effective": 0.5,
            "risk_flags": ["rr_too_low", "micro_trigger_missing"],
        }]
    }

    assert api._new_listing_watch_candidates(payload) == []


def test_alert_plan_html_separates_tp1_tp2_rr_and_marks_runner():
    row = {
        "ticker": "NCI",
        "direction": "LONG",
        "Entry": 10.32,
        "StopLoss": 9.98,
        "TP1": 11.55,
        "TP2": 16.05,
        "trade_setup": {
            "stop_source": "s1_invalidation",
            "tp1_source": "vrvp_hvn_high",
            "tp2_source": "vrvp_hvn_low",
        },
    }

    body = api._format_alert_plan_html(row)

    assert "R:R eff 4.31" in body
    assert "TP1 3.6R / TP2 16.9R" in body
    assert "TP2 ist Runner-Ziel" in body
    assert "VRVP HVN-Oberkante" in body


def test_stock_strategy_swing_blocks_extended_long_without_volume(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"CLS"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *args, **kwargs: None)
    row = {
        "ticker": "CLS",
        "grade": "A",
        "score": 88,
        "rvol": 1.2,
        "price": 467.09,
        "current_price": 467.09,
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "change_pct": 9.5,
        "close_pos": 0.74,
        "open_to_current_pct": 6.0,
        "Entry": 467.09,
        "StopLoss": 450.79,
        "TP1": 509.11,
        "TP2": 537.13,
    }

    state = api._classify_alert_candidate("stock_strategy", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "swing_extended_without_volume_wait_retest" in state["suppression_reasons"]
    assert state["decision"] == "WAIT_RETEST"


def test_stock_strategy_swing_short_blocks_chased_drop(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"NU"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *args, **kwargs: None)
    row = {
        "ticker": "NU",
        "grade": "A",
        "score": 86,
        "rvol": 2.0,
        "price": 11.62,
        "current_price": 11.62,
        "direction": "SHORT",
        "Signal_Direction": "SHORT",
        "change_pct": -10.5,
        "close_pos": 0.18,
        "open_to_current_pct": -7.0,
        "Entry": 11.62,
        "StopLoss": 11.95,
        "TP1": 10.70,
        "TP2": 10.04,
    }

    state = api._classify_alert_candidate("stock_strategy", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "swing_short_drop_extended_wait_failed_reclaim" in state["suppression_reasons"]
    assert state["decision"] == "WAIT_RETEST"
