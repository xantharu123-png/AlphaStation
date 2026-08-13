#!/usr/bin/env python3
"""Pytest-Suite BE-Trigger (Breakeven): Tracker-Markierung + bg-Stop-Update-Mail.

Datenbasis Exit-Effizienz-Audit 2026-07-30 (237 Signale/90d): 31% der Signale
mit MFE >= +1R endeten <= 0 (Ø +1.64R verschenkt); die BE-Regel haette den
Erwartungswert von +0.18R auf +0.34R gehoben. Der Tracker markiert deshalb
erstmals erreichte +1R-MFE als be_activated_at (einmalig) und rechnet
r_realized_be (Ist-vs-BE A/B); bg_service mailt die scanner-differenzierte
Anweisung "Stop auf Einstand" (crash*: KEIN Teilverkauf; sonst 50/50 an TP1).

Komplett offline: Fake-Fetcher, tmp-SQLite, Dedupe-File auf tmp_path,
Mail-Recorder — Muster test_exit_update_mails.py.
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bg_service
import modules.signal_tracker as st


# ── Helpers Tracker (Muster test_exit_update_mails.py) ───────────────────────
def _base_row(**overrides):
    """Plausible LONG-Alert-Row: Entry 100, Stop 95 (Risk 5), TP1 105, TP2 110."""
    row = {"Ticker": "AAPL", "Entry": 100.0, "StopLoss": 95.0, "TP1": 105.0, "TP2": 110.0}
    row.update(overrides)
    return row


def _signal(ticker):
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE ticker = ? ORDER BY id", (ticker,)
        ).fetchall()]
    finally:
        conn.close()
    assert rows, "Signal %s nicht in der DB" % ticker
    return rows[-1]


def _bars_after(ticker, specs):
    """Complete Daily bars in consecutive US-equity sessions after the alert."""
    cursor = datetime.fromisoformat(_signal(ticker)["created_at"]).astimezone(
        st.ZoneInfo("America/New_York")
    ).date()
    bars = []
    for h, l, c in specs:
        cursor += timedelta(days=1)
        while not st._is_us_equity_session(cursor):
            cursor += timedelta(days=1)
        bars.append({
            "date": cursor.isoformat(),
            "open": 100.0,
            "high": h,
            "low": l,
            "close": c,
            "interval_complete": True,
        })
    return bars


def _stock_fetcher(bars_by_ticker):
    return lambda ticker, since_iso_date: bars_by_ticker.get(ticker)


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    """Frische tmp-DB pro Test: ENV (Import-Kontrakt) + Modulglobale (Laufzeit)."""
    db_path = str(tmp_path / "signal_tracker_test.sqlite")
    monkeypatch.setenv("SIGNAL_TRACKER_DB_PATH", db_path)
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", db_path)
    return st


# ── Teil 1: Tracker — be_activated_at / be_activations / r_realized_be ───────
def test_tracker_mfe_1r_marks_be_exactly_once(tracker):
    """Tag 1: High 106 => MFE +1.2R >= 1R => be_activated_at persistiert und
    genau EINE be_activation; Re-Eval erzeugt weder neue Aktivierung noch
    einen neuen Zeitstempel."""
    tracker.record_alert_signals(
        "breakout", [_base_row()], mail_channel="stocks_swing"
    )
    bars = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    r1 = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert len(r1["be_activations"]) == 1
    act = r1["be_activations"][0]
    assert set(act) == {
        "id", "ticker", "scanner", "direction", "entry", "entry_fill_price",
        "stop", "tp1", "tp2", "mfe", "asset_class", "activated_at",
        "mail_class", "channel", "mail_channel",
        "strategy", "trade_horizon", "setup_key",
        "delivery_recipient_keys_json",
    }
    assert act["ticker"] == "AAPL"
    assert act["scanner"] == "breakout"
    assert act["mail_class"] == "trade"
    assert act["channel"] == "email"
    assert act["mail_channel"] == "stocks_swing"
    assert act["direction"] == "LONG"
    assert act["entry"] == pytest.approx(100.0)
    assert act["stop"] == pytest.approx(95.0)
    assert act["mfe"] == pytest.approx(1.2)
    assert act["asset_class"] == "stock"
    sig = _signal("AAPL")
    assert sig["be_activated_at"]
    assert sig["status"] == "OPEN"
    r2 = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert r2["be_activations"] == []
    assert _signal("AAPL")["be_activated_at"] == sig["be_activated_at"]


def test_tracker_activation_and_stop_same_run_stays_conservative(tracker):
    """MFE >= +1R UND Stop am SELBEN ersten Bar: Intraday-Reihenfolge
    unbewiesen => KEIN be_activated_at, r_realized_be == r_realized (-1R)."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(106.0, 94.5, 95.5)])
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert result["be_activations"] == []
    sig = _signal("AAPL")
    assert sig["status"] == "STOP_HIT"
    assert sig["be_activated_at"] is None
    assert sig["r_realized"] == pytest.approx(-1.0)
    assert sig["r_realized_be"] == pytest.approx(-1.0)


def test_tracker_be_then_stop_realizes_zero_r(tracker):
    """Lauf 1 aktiviert BE (MFE +1.2R), Lauf 2 faellt der Kurs durch den Stop:
    r_realized = -1R, aber r_realized_be = 0.0 (Ausstieg am Einstand)."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars1 = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    assert _signal("AAPL")["be_activated_at"]
    tracker.mark_be_alerts_sent([_signal("AAPL")["id"]])
    bars2 = _bars_after("AAPL", [(106.0, 99.0, 105.5), (104.0, 94.5, 95.5)])
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    assert result["be_activations"] == []  # BE war bereits aktiviert
    sig = _signal("AAPL")
    assert sig["status"] == "STOP_HIT"
    assert sig["r_realized"] == pytest.approx(-1.0)
    assert sig["r_realized_be"] == pytest.approx(0.0)


def test_tracker_mfe_below_1r_no_be(tracker):
    """MFE +0.8R < 1R => weder be_activated_at noch be_activations."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(104.0, 99.0, 103.0)])  # favorable = (104-100)/5 = 0.8
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert result["be_activations"] == []
    sig = _signal("AAPL")
    assert sig["be_activated_at"] is None
    assert sig["max_favorable_r"] == pytest.approx(0.8)


def test_tracker_winner_close_gets_r_realized_be_unchanged(tracker):
    """TP2 nach BE-Aktivierung: r_realized_be == r_realized (+2R) — die Regel
    kreditiert nur Verlust-Exits nach Aktivierung."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars1 = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    bars2 = _bars_after("AAPL", [(106.0, 99.0, 105.5), (111.0, 99.0, 110.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    sig = _signal("AAPL")
    assert sig["status"] == "TP2_HIT"
    assert sig["r_realized"] == pytest.approx(2.0)
    assert sig["r_realized_be"] == pytest.approx(2.0)


def test_breakeven_adjusted_r_pure_cases(tracker):
    """Pure Funktion: alle Regelzweige inkl. ambiguous_same_day-Konservativfall."""
    f = tracker.breakeven_adjusted_r
    assert f({}) is None
    assert f({"r_realized": None}) is None
    assert f({"r_realized": -1.0}) == pytest.approx(-1.0)          # nie aktiviert
    assert f({"r_realized": 0.8, "be_activated_at": "x"}) == pytest.approx(0.8)
    assert f({"r_realized": -1.0, "be_activated_at": "x"}) is None
    assert f({"r_realized": -0.2, "be_activated_at": "x"}) is None
    assert f({
        "r_realized": -1.0,
        "be_activated_at": "x",
        "be_exit_at": "2026-08-13T14:05:00+00:00",
        "be_exit_fill_price": 100.0,
        "entry_fill_price": 100.0,
        "stop": 95.0,
        "direction": "LONG",
    }) == pytest.approx(0.0)
    assert f({"r_realized": -1.0, "be_activated_at": "x",
              "outcome_detail": "ambiguous_same_day"}) == pytest.approx(-1.0)


def test_tracker_return_stays_backward_compatible_with_be(tracker):
    """Strikte Alt-Vergleiche bleiben wahr — 'be_activations' wird wie
    'transitions' im Gleichheitsvergleich ignoriert, sonst normal sichtbar."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    assert {"evaluated": 1, "closed": 0, "errors": 0} == result  # symmetrisch
    assert len(result["be_activations"]) == 1
    assert "be_activations" in result and "be_activations" in dict(result)


def test_tracker_be_mail_stays_pending_until_acknowledged(tracker):
    recipient_key = "a" * 64
    tracker.record_alert_signals(
        "breakout",
        [_base_row()],
        delivery_recipient_keys=[recipient_key],
        mail_channel="stocks_premarket",
    )
    bars = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": bars})
    )
    signal_id = result["be_activations"][0]["id"]

    pending = tracker.load_pending_be_activations()
    assert len(pending) == 1
    assert pending[0]["id"] == signal_id
    assert pending[0]["channel"] == "email"
    assert pending[0]["mail_channel"] == "stocks_premarket"
    assert pending[0]["tracker_persisted"] is True
    assert pending[0]["delivery_recipient_keys_json"] == f'["{recipient_key}"]'

    assert tracker.mark_be_alerts_sent([signal_id]) == 1
    assert tracker.load_pending_be_activations() == []
    assert tracker.mark_be_alerts_sent([signal_id]) == 0


# ── Helpers bg (Muster test_exit_update_mails.py) ────────────────────────────
def _activation(**overrides):
    recipient_key = bg_service._recipient_delivery_key("followup@example.com")
    act = {
        "id": 21, "ticker": "XYZ", "scanner": "stock_strategy", "direction": "LONG",
        "entry": 100.0, "entry_fill_price": 100.0, "stop": 95.0, "tp1": 105.0,
        "tp2": 110.0, "mfe": 1.2, "asset_class": "stock",
        "activated_at": "2026-07-30T10:00:00",
        "mail_class": "trade", "channel": "email",
        "trade_horizon": "swing",
        "delivery_recipient_keys": [recipient_key],
    }
    act.update(overrides)
    return act


def _setup_bg(monkeypatch, tmp_path, activations, origin_keys=()):
    """bg-Setup: Dedupe-File auf tmp_path, Startup-Delay aus, Mail-Recorder,
    Eval-Stub mit festen be_activations; origin_keys = Erst-Mail-Marks."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    payload = {"evaluated": len(activations), "closed": 0, "errors": 0,
               "transitions": [], "be_activations": list(activations)}
    monkeypatch.setattr(bg_service, "evaluate_open_signals", lambda **kw: payload)
    monkeypatch.setattr(
        bg_service, "_reconcile_pending_accepted_deliveries", lambda: 0
    )
    monkeypatch.setattr(bg_service, "load_pending_terminal_updates", lambda: [])
    monkeypatch.setattr(bg_service, "load_pending_be_activations", lambda: [])
    monkeypatch.setattr(
        bg_service,
        "_followup_recipient_profiles",
        lambda _secrets: [
            {
                "email": "followup@example.com",
                "position_update_scope": "all",
                "personal_positions": [],
            }
        ],
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: {"followup@example.com"},
    )
    monkeypatch.setattr(
        bg_service,
        "mark_be_alerts_sent",
        lambda signal_ids: len(list(signal_ids)),
    )
    if origin_keys:
        Path(bg_service._EMAIL_DEDUPE_FILE).write_text(
            json.dumps({key: time.time() for key in origin_keys})
        )
    sent = []

    def _recorder(subject, body_html, secrets, mail_class="trade", **kwargs):
        sent.append({"subject": subject, "body": body_html, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    return sent, payload


# ── Teil 2: bg-ℹ️-Stop-Update-Mail im signal_eval-Job ────────────────────────
def test_bg_be_activations_send_one_info_mail_scanner_aware(monkeypatch, tmp_path):
    """EINE Sammelmail fuer alle Aktivierungen; Default-Scanner bekommt die
    50/50-an-TP1-Anweisung, crash-Scanner ausdruecklich KEINEN Teilverkauf."""
    acts = [
        _activation(),
        _activation(id=22, ticker="CRSH", scanner="crash", entry=10.0,
                    entry_fill_price=10.0, stop=9.0, tp1=11.5, tp2=12.5, mfe=1.1),
    ]
    sent, _ = _setup_bg(monkeypatch, tmp_path, acts,
                        origin_keys=("stock_strategy_XYZ", "crash_CRSH"))
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1
    mail = sent[0]
    assert mail["mail_class"] == "signal_update"
    assert mail["subject"] == "Stop-Update: 2 Trade(s) auf Einstand sichern (+1R gelaufen)"
    body = mail["body"]
    assert "XYZ" in body and "50% verkaufen" in body
    assert "CRSH" in body and "KEIN Teilverkauf" in body
    assert "+1.20R" in body and "+1.10R" in body  # MFE-Spalte


def test_bg_be_mail_second_run_deduped(monkeypatch, tmp_path):
    """Persistenter Key signal_be_{id}: Re-Eval => keine zweite Mail."""
    sent, _ = _setup_bg(monkeypatch, tmp_path, [_activation()],
                        origin_keys=("stock_strategy_XYZ",))
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1
    marks = json.loads(Path(bg_service._EMAIL_DEDUPE_FILE).read_text())
    assert "signal_be_21" in marks
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1


def test_bg_be_without_origin_mark_silent(monkeypatch, tmp_path):
    """Nicht per E-Mail versendetes Ursprungssignal bleibt fail-closed."""
    sent, _ = _setup_bg(
        monkeypatch,
        tmp_path,
        [_activation(channel="telegram")],
    )
    stats = bg_service._run_signal_eval_job(secrets={})
    assert sent == []
    assert stats["evaluated"] == 1
    dedupe_file = Path(bg_service._EMAIL_DEDUPE_FILE)
    marks = json.loads(dedupe_file.read_text()) if dedupe_file.exists() else {}
    assert not any(str(k).startswith("signal_be_") for k in marks)


def test_bg_failing_be_mail_still_returns_eval_stats(monkeypatch, tmp_path):
    """Fehler im BE-Mail-Bau darf den Eval-Job NIE crashen."""
    sent, payload = _setup_bg(monkeypatch, tmp_path, [_activation()],
                              origin_keys=("stock_strategy_XYZ",))

    def _boom(*a, **k):
        raise RuntimeError("BE-Mail kaputt")

    monkeypatch.setattr(bg_service, "_send_be_alert_mail", _boom)
    stats = bg_service._run_signal_eval_job(secrets={})
    assert stats is payload  # Eval-Ergebnis kommt trotz Mail-Crash zurueck
    assert sent == []


def test_bg_retries_persisted_be_mail_after_send_failure(monkeypatch, tmp_path):
    _setup_bg(monkeypatch, tmp_path, [])
    activation = _activation(tracker_persisted=True, mail_class="trade")
    acknowledged = []
    attempts = []

    monkeypatch.setattr(
        bg_service,
        "load_pending_be_activations",
        lambda: [] if acknowledged else [dict(activation)],
    )

    def _ack(signal_ids):
        ids = list(signal_ids)
        acknowledged.extend(ids)
        return len(ids)

    def _flaky_mail(subject, body_html, secrets, mail_class="trade", **kwargs):
        attempts.append(kwargs)
        return len(attempts) > 1

    monkeypatch.setattr(bg_service, "mark_be_alerts_sent", _ack)
    monkeypatch.setattr(bg_service, "_send_email_alert", _flaky_mail)

    bg_service._run_signal_eval_job(secrets={})
    assert len(attempts) == 1
    assert attempts[0]["enqueue_on_failure"] is False
    assert acknowledged == []

    bg_service._run_signal_eval_job(secrets={})
    assert len(attempts) == 2
    assert acknowledged == [21]

    bg_service._run_signal_eval_job(secrets={})
    assert len(attempts) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── Teil 3: BE-Metriken in load_performance_summary (Wochenreport-Daten) ─────
def test_summary_reports_be_metrics(tracker):
    """BE→Stop-Szenario: avg_r_be 0.0 vs avg_r -1.0, be_activations/be_saved 1 —
    genau die Zahlen, die der Wochenreport als Ist-vs-BE-Nachweis zeigt."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars1 = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    tracker.mark_be_alerts_sent([_signal("AAPL")["id"]])
    bars2 = _bars_after("AAPL", [(106.0, 99.0, 105.5), (104.0, 94.5, 95.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    summary = tracker.load_performance_summary(days=7)
    total = summary["total"]
    assert total["avg_r"] == pytest.approx(-1.0)
    assert total["avg_r_be"] == pytest.approx(0.0)
    assert total["be_activations"] == 1
    assert total["be_saved"] == 1
    bucket = summary["per_scanner"]["breakout"]
    assert bucket["avg_r_be"] == pytest.approx(0.0)
    assert bucket["be_activations"] == 1
    assert bucket["be_saved"] == 1


def test_summary_without_activation_be_equals_ist(tracker):
    """Stop OHNE vorheriges +1R: r_realized_be == r_realized (Regel greift
    nicht) — avg_r_be == avg_r, Zaehler 0. Belegt: BE veraendert nur, was
    vorher aktiviert war."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])  # Stop am Tag 1, MFE +0.2R
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    total = tracker.load_performance_summary(days=7)["total"]
    assert total["avg_r"] == pytest.approx(-1.0)
    assert total["avg_r_be"] == pytest.approx(-1.0)
    assert total["be_activations"] == 0
    assert total["be_saved"] == 0


# ── Teil 4: Dashboard-Vertrag (Performance-Tab rendert die BE-Felder) ────────
def test_frontend_performance_tab_renders_be_fields():
    """frontend/index.html muss die BE-Felder der Summary tatsaechlich anzeigen:
    Kopf-Karte + Scanner-Spalte 'Ø R BE', Ergebnis-Banner, BE-Zeile in den
    letzten Signalen, Footer-Semantik."""
    src = (Path(_DIR) / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "'Ø R BE'" in src                       # Kopf-Karte
    assert ">Ø R BE</th>" in src                   # Scanner-Tabellen-Spalte
    assert "total.avg_r_be" in src                 # Karte belegt
    assert "s.avg_r_be" in src                     # Scanner-Zelle belegt
    assert "total.be_activations" in src           # Ergebnis-Banner
    assert "total.be_saved" in src                 # bewahrte Verlierer im Banner
    assert "sig.r_realized_be" in src              # BE-Zeile je Signal
    assert "kein Backtest" in src                  # Ehrlichkeits-Footer


def test_recent_payload_contains_r_realized_be(tracker):
    """Die recent-Liste der Summary fuehrt r_realized_be mit (Dashboard-Zeile)."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    recent = tracker.load_performance_summary(days=7)["recent"]
    assert recent and "r_realized_be" in recent[0]
    assert recent[0]["r_realized_be"] == pytest.approx(-1.0)  # ohne Aktivierung = Ist
