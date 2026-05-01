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
                {"ticker": "REAL", "grade": "A", "score": 70, "rvol": 1.1, "price": 12}
            ],
        }],
    }))

    audit = api._build_alert_audit_for_cache("bear", str(cache_file))

    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == 1
    assert audit["alertable_preview"][0]["ticker"] == "REAL"
    assert all(item["ticker"] != "LABD" for item in audit["alertable_preview"])


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
                    "entry": 1.2,
                    "stop_loss": 1.5,
                    "tp1": 0.9,
                    "tp2": 0.7,
                    "rr_effective": 1.5,
                    "exh_score": 70,
                },
            },
            {"symbol": "LOWUSDT", "exchange": "mexc", "signal": {"grade": "B", "timing": "WATCH"}},
        ]
    }

    api._send_new_listing_pipeline_alerts(payload)

    assert len(sent) == 1
    assert "Pump & Dump" in sent[0][0]
    assert "WLD" in sent[0][1]
    assert "LOW" not in sent[0][1]
