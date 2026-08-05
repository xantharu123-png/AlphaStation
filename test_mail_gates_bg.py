"""Regressionstests Mail-Audit-Fixes bg_service (Q3/B2/B3/B5/B6 + Startup-Delay).

Beweist: bg-Mails laufen durch dieselben Qualitaets-Gates wie api —
keine nicht-handelbaren "Top-Setup"-Mails mehr (Betreiber-Beschwerde 10.06.).
"""
import json
import os
import time
from pathlib import Path

import bg_service


def _write_bi_cache(path, rows):
    with path.open("w", encoding="utf-8") as f:
        json.dump({"results": rows, "cached_at": time.time()}, f)


def _base_row(ticker="GOOD", **overrides):
    row = {
        "ticker": ticker,
        "BI_Grade": "S",
        "BI_Score": 120,
        "Preis": 10.05,
        "current_price": 10.05,
        "RVOL": 2.5,
        "direction": "long",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 11.0,
        "TP2": 11.8,
        "Name": "Test Corp",
        "latest_bar_change_pct": 0.4,
        "latest_bar_close_pos": 0.8,
    }
    row.update(overrides)
    return row


def _setup(monkeypatch, tmp_path, rows):
    """Gemeinsames Test-Setup: Caches/Dedupe isolieren, Versand aufzeichnen."""
    cache_file = tmp_path / "bi_cache_long.json"
    _write_bi_cache(cache_file, rows)
    monkeypatch.setattr(bg_service, "_alert_cache_path", lambda _name: str(cache_file))
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(
        bg_service,
        "_has_open_equivalent_trade_safe",
        lambda *args, **kwargs: False,
    )
    # Startup-Delay umgehen (Prozess "laeuft schon lange")
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    # Keine Intraday-Nachladungen via Polygon im Test
    monkeypatch.setattr(
        bg_service, "_fetch_long_latest_intraday_state", lambda *a, **k: {}, raising=False
    )
    monkeypatch.setattr(
        bg_service,
        "_fetch_stock_swing_execution_state",
        lambda *a, **k: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "CLEAR",
            "Swing_4H_Execution_Reason": "unit_test_clear",
        },
        raising=False,
    )
    sent_mails = []

    def _recorder(subject, body_html, secrets, mail_class="trade"):
        sent_mails.append({"subject": subject, "body": body_html, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    return sent_mails


# ── Q3: Health-/RVOL-/estimated-Gates wie api ──────────────────────────────

def test_q3_price_over_tp1_is_blocked_by_health_gate(monkeypatch, tmp_path):
    """Audit-Beweisfall B1: Preis UEBER TP1 (Entry laengst ueberrollt) darf
    nicht mehr als 'Top-Setup' gemailt werden."""
    sent = _setup(monkeypatch, tmp_path, [_base_row("CHSE", Preis=11.8, current_price=11.8)])
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_q3_stop_breach_is_blocked(monkeypatch, tmp_path):
    """Gerissener Stop (Preis unter StopLoss) => NO_TRADE => keine Mail."""
    sent = _setup(monkeypatch, tmp_path, [_base_row("BRCH", Preis=9.2, current_price=9.2)])
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_q3_low_rvol_grade_s_is_blocked(monkeypatch, tmp_path):
    """Audit-Beweisfall: Grade S mit RVOL 0.5 wurde vorher gemailt — jetzt Block."""
    sent = _setup(monkeypatch, tmp_path, [_base_row("LRVL", RVOL=0.5)])
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_q3_estimated_levels_are_blocked(monkeypatch, tmp_path):
    """Row ohne native Entry/Stop/TP (Levels wuerden synthetisiert) => Block."""
    row = _base_row("ESTM")
    for key in ("Entry", "StopLoss", "TP1", "TP2"):
        row.pop(key, None)
    sent = _setup(monkeypatch, tmp_path, [row])
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_q3_clean_row_is_mailed_exactly_once(monkeypatch, tmp_path):
    """Positivkontrolle: sauberes, frisches Setup geht als trade-Mail raus."""
    sent = _setup(monkeypatch, tmp_path, [_base_row("GOOD")])
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert len(sent) == 1
    assert "GOOD" in sent[0]["body"]
    assert sent[0]["mail_class"] == "trade"


def test_swing_owner_mail_does_not_require_intraday_5m_state(monkeypatch, tmp_path):
    """BI/Biotech owner scans are swing scans unless intraday is explicit."""
    row = _base_row("SWNG")
    row.pop("latest_bar_change_pct")
    row.pop("latest_bar_close_pos")
    sent = _setup(monkeypatch, tmp_path, [row])

    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})

    assert len(sent) == 1
    assert "SWNG" in sent[0]["body"]
    assert "SWING_SETUP" in sent[0]["body"]


def test_swing_owner_blocks_unreclaimed_4h_rejection(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_base_row("TEL")])
    monkeypatch.setattr(
        bg_service,
        "_fetch_stock_swing_execution_state",
        lambda *args, **kwargs: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "WAIT_RECLAIM",
            "Swing_4H_Execution_Reason": "failed_4h_breakout_not_reclaimed",
            "Swing_4H_Reclaim_Level": 201.0,
        },
    )

    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": "test"})

    assert sent == []


def test_swing_owner_fails_closed_when_4h_state_is_unavailable(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_base_row("MISS")])
    monkeypatch.setattr(
        bg_service,
        "_fetch_stock_swing_execution_state",
        lambda *args, **kwargs: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "DATA_UNAVAILABLE",
            "Swing_4H_Execution_Reason": "insufficient_4h_history",
        },
    )

    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": "test"})

    assert sent == []


def test_intraday_owner_mail_requires_fresh_5m_state(monkeypatch, tmp_path):
    row = _base_row("MISS")
    row.pop("latest_bar_change_pct")
    row.pop("latest_bar_close_pos")
    sent = _setup(monkeypatch, tmp_path, [row])

    bg_service._check_and_alert_scan_results(
        "bi_long", {"POLYGON_KEY": ""}, trade_horizon="intraday"
    )

    assert sent == []


def test_trade_plan_gate_blocks_weak_tp1_hidden_by_distant_tp2(monkeypatch, tmp_path):
    sent = _setup(
        monkeypatch,
        tmp_path,
        [_base_row("RUNR", Entry=10.0, StopLoss=9.5, TP1=10.3, TP2=14.0)],
    )

    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})

    assert sent == []


def test_trade_plan_gate_blocks_targets_that_are_too_close(monkeypatch, tmp_path):
    sent = _setup(
        monkeypatch,
        tmp_path,
        [_base_row("CLSE", Entry=10.0, StopLoss=9.5, TP1=10.8, TP2=10.9)],
    )

    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})

    assert sent == []


def test_health_gate_blocks_high_execution_risks(monkeypatch):
    cases = (
        {"chase_risk": "HIGH", "fakeout_risk": "LOW", "liquidity_risk": "LOW"},
        {"chase_risk": "LOW", "fakeout_risk": "CRITICAL", "liquidity_risk": "LOW"},
        {"chase_risk": "LOW", "fakeout_risk": "LOW", "liquidity_risk": "CRITICAL"},
    )
    for risks in cases:
        monkeypatch.setattr(
            bg_service,
            "calculate_trade_health",
            lambda *_args, _risks=risks, **_kwargs: {
                "decision": "TRADEABLE",
                "health_score": 95,
                **_risks,
            },
        )
        assert bg_service._bg_alert_health_reasons(_base_row(), "bi_long")


def test_active_crash_is_not_blocked_by_regular_short_no_chase_rule():
    row = {
        "change_pct": -15.0,
        "close_pos": 0.1,
        "open_to_current_pct": -8.0,
        "latest_bar_change_pct": -1.0,
        "latest_bar_close_pos": 0.1,
        "RVOL": 3.0,
        "alertable_short": True,
    }

    assert "drop_too_extended_no_chase" in bg_service._bear_short_rule_reasons(row)
    assert bg_service._bear_crash_alert_ok(row) is True


def test_q3_mixed_rows_only_clean_one_in_body(monkeypatch, tmp_path):
    """Kern der Beschwerde: gemischte Liste => NUR die handelbare Row im Body."""
    sent = _setup(
        monkeypatch,
        tmp_path,
        [
            _base_row("GOOD"),
            _base_row("CHSE", Preis=11.8, current_price=11.8),
            _base_row("LRVL", RVOL=0.5),
            _base_row("BRCH", Preis=9.2, current_price=9.2),
        ],
    )
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert len(sent) == 1
    body = sent[0]["body"]
    assert "GOOD" in body
    for bad in ("CHSE", "LRVL", "BRCH"):
        assert bad not in body, f"Nicht handelbare Row {bad} im Mail-Body!"


# ── B2: geteiltes persistentes Dedupe (api-Key-Format), nur nach Erfolg ────

def test_b2_api_dedupe_mark_blocks_bg_mail(monkeypatch, tmp_path):
    """Hat api bereits gemailt (gleicher Key im geteilten File), schweigt bg."""
    sent = _setup(monkeypatch, tmp_path, [_base_row("GOOD")])
    dedupe_file = Path(bg_service._EMAIL_DEDUPE_FILE)
    dedupe_file.write_text(json.dumps({"bi_long_GOOD": time.time()}))
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_b2_successful_send_writes_shared_dedupe_mark(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_base_row("GOOD")])
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert len(sent) == 1
    marks = json.loads(Path(bg_service._EMAIL_DEDUPE_FILE).read_text())
    assert "bi_long_GOOD" in marks


def test_b2_failed_send_sets_no_cooldown_or_mark(monkeypatch, tmp_path):
    """SMTP-Fehler => kein Cooldown/Mark => naechster Lauf darf erneut mailen."""
    _setup(monkeypatch, tmp_path, [_base_row("GOOD")])
    monkeypatch.setattr(bg_service, "_send_email_alert", lambda *a, **k: False)
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert "bi_long_GOOD" not in bg_service._EMAIL_COOLDOWN
    dedupe_file = Path(bg_service._EMAIL_DEDUPE_FILE)
    marks = json.loads(dedupe_file.read_text()) if dedupe_file.exists() else {}
    assert "bi_long_GOOD" not in marks


# ── B3/B5: NLS Roh-Symbol-Keys + einmalige Invalidierungs-Update-Mail ──────

def test_b3_source_uses_raw_symbol_dedupe_key():
    """Key-Format muss api-kompatibel sein (new_listing_{RAW}, nicht Display)."""
    import inspect

    src = inspect.getsource(bg_service)
    assert 'f"new_listing_{str(raw_symbol' in src or "new_listing_{raw_symbol" in src


def test_b5_invalidation_mail_sent_once_for_previously_mailed_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    sent = []
    monkeypatch.setattr(
        bg_service,
        "_send_email_alert",
        lambda s, b, sec, mail_class="trade": sent.append({"subject": s, "mail_class": mail_class}) or True,
    )
    # Erst-Mail-Mark vorhanden (Signal wurde gemailt)
    Path(bg_service._EMAIL_DEDUPE_FILE).write_text(json.dumps({"new_listing_TSTUSDT": time.time()}))
    results = {
        "monitoring": [
            {
                "symbol": "TSTUSDT",
                "status": "invalidated",
                "trade_category": "SIGNAL_INVALIDATED",
                "status_reason": "Stop gerissen",
                "price": 0.95,
            }
        ]
    }
    bg_service._alert_nls_invalidations(results, {})
    assert len(sent) == 1
    assert sent[0]["mail_class"] == "info"
    # Zweiter Lauf: dedupet, keine weitere Mail
    bg_service._alert_nls_invalidations(results, {})
    assert len(sent) == 1


def test_b5_no_invalidation_mail_without_first_mail_mark(monkeypatch, tmp_path):
    """Signal wurde nie gemailt => auch keine Invalidierungs-Mail (kein Spam)."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    sent = []
    monkeypatch.setattr(
        bg_service, "_send_email_alert", lambda *a, **k: sent.append(1) or True
    )
    results = {
        "monitoring": [
            {"symbol": "NVRX", "status": "invalidated", "trade_category": "SIGNAL_INVALIDATED"}
        ]
    }
    bg_service._alert_nls_invalidations(results, {})
    assert sent == []


# ── B6: Mail-Klassen-Praefixe (mit api abgestimmte Konvention) ─────────────

def test_b6_mail_class_prefixes_and_no_emoji_stacking():
    f = bg_service._apply_mail_class_prefix
    assert f("3 Top-Setups — BI Long", "trade").startswith("🚨 JETZT: ")
    assert f("Retest-Zonen", "watch").startswith("👁️ WATCH: ")
    assert f("Signal invalidiert", "info").startswith("ℹ️ ")
    # Vorhandenes Legacy-Emoji wird ersetzt, nicht gestapelt
    out = f("🚨 3 Top-Setups", "trade")
    assert out.count("🚨") == 1
    # Idempotent: nochmal anwenden aendert nichts
    assert f(out, "trade") == out


# ── Startup-Delay: nach Restart 5 Min Mail-Sperre ──────────────────────────

def test_startup_delay_blocks_mail_after_restart(monkeypatch):
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time())

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("SMTP darf im Startup-Delay nie erreicht werden")

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP_SSL", _Boom)
    ok = bg_service._send_email_alert(
        "Test", "<b>x</b>", {"GMAIL_USER": "a@b.c", "GMAIL_APP_PASSWORD": "x"}
    )
    assert ok is False


def test_watch_mail_does_not_fall_back_to_operator_without_opt_in(monkeypatch):
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    monkeypatch.setattr(bg_service, "HAS_AUTH_ALERT_RECIPIENTS", False)

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP must not be reached without watch opt-in")

    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _Boom)

    ok = bg_service._send_email_alert(
        "Retest watch",
        "<p>x</p>",
        {
            "GMAIL_USER": "operator@example.com",
            "GMAIL_APP_PASSWORD": "pw",
            "ALERT_EMAIL": "operator@example.com",
        },
        mail_class="watch",
    )

    assert ok is False
