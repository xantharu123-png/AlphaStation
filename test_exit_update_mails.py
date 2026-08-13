#!/usr/bin/env python3
"""Pytest-Suite Exit-Update-Mails: Tracker-Transitionen + bg-ℹ️-Sammelmail.

Schliesst den Signal-Kreislauf fuers Abo-Produkt: Wenn ein bereits per
🚨-Mail versendetes Signal vom Signal-Tracker als STOP_HIT/TP1/TP2_HIT/
EXPIRED erkannt wird, geht genau EINE ℹ️-Update-Sammelmail raus.

Teil 1 (modules/signal_tracker.py): evaluate_open_signals liefert
abwaertskompatibel result['transitions'] — inkl. virtuellem Status
TP1_HIT_OPEN fuer "TP1 erreicht, Signal bleibt OPEN".
Teil 2 (bg_service.py): _run_signal_eval_job baut daraus die Info-Mail —
Erst-Mail-Zweitsicherung, persistentes Dedupe je Transition, crash-isoliert.

Komplett offline: Fake-Fetcher, tmp-SQLite (SIGNAL_TRACKER_DB_PATH-ENV +
modulglobales SIGNAL_DB_PATH), Dedupe-File auf tmp_path, Mail-Recorder —
Muster wie test_mail_gates_bg.py / test_signal_tracker.py.
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


# ── Helpers Tracker (Muster test_signal_tracker.py) ──────────────────────────
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


# ── Teil 1: evaluate_open_signals -> result['transitions'] ───────────────────
def test_tracker_stop_transition_has_complete_contract_fields(tracker):
    """STOP-Transition enthaelt Status, Fill-Qualitaet und Live-R:R."""
    recipient_key = "b" * 64
    tracker.record_alert_signals(
        "breakout",
        [_base_row()],
        delivery_recipient_keys=[recipient_key],
        mail_channel="stocks_swing",
    )
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])  # Tag 1: Low <= Stop
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    transitions = result["transitions"]
    assert len(transitions) == 1
    tr = transitions[0]
    assert set(tr) == {
        "id", "ticker", "scanner", "direction", "old_status", "new_status",
        "entry", "entry_fill_price", "stop", "tp1", "tp2", "r_realized",
        "r_realized_upper", "outcome_detail", "evaluation_horizon_bars",
        "tp1_hit_this_run", "asset_class",
        "mail_class", "channel", "mail_channel",
        "adverse_slippage_r", "adverse_slippage_pct",
        "live_rr_tp1", "live_effective_rr",
        "fill_quality", "fill_rejection_reason",
        "strategy", "trade_horizon", "setup_key",
        "exit_fill_price", "stop_gap_slippage_r", "stop_gap_slippage_pct",
        "code_revision", "fill_evidence_mode",
        "delivery_recipient_keys_json",
    }
    assert tr["id"] == _signal("AAPL")["id"]
    assert tr["ticker"] == "AAPL"
    assert tr["scanner"] == "breakout"
    assert tr["mail_class"] == "trade"
    assert tr["channel"] == "email"
    assert tr["mail_channel"] == "stocks_swing"
    assert tr["delivery_recipient_keys_json"] == f'["{recipient_key}"]'
    assert tr["direction"] == "LONG"
    assert tr["old_status"] == "OPEN"
    assert tr["new_status"] == "STOP_HIT"
    assert tr["entry"] == pytest.approx(100.0)
    assert tr["entry_fill_price"] == pytest.approx(100.0)
    assert tr["stop"] == pytest.approx(95.0)
    assert tr["tp1"] == pytest.approx(105.0)
    assert tr["tp2"] == pytest.approx(110.0)
    assert tr["r_realized"] == pytest.approx(-1.0)
    assert tr["exit_fill_price"] == pytest.approx(95.0)
    assert tr["stop_gap_slippage_r"] == pytest.approx(0.0)
    assert tr["stop_gap_slippage_pct"] == pytest.approx(0.0)
    assert tr["tp1_hit_this_run"] is False
    assert tr["asset_class"] == "stock"


def test_tracker_tp1_without_close_yields_tp1_hit_open_exactly_once(tracker):
    """TP1 erreicht OHNE Statuswechsel => virtueller Status TP1_HIT_OPEN;
    Re-Eval ohne neues Ereignis erzeugt KEINE zweite Transition."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(106.0, 99.0, 105.5)])  # TP1 beruehrt, kein Stop/TP2
    r1 = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert [t["new_status"] for t in r1["transitions"]] == ["TP1_HIT_OPEN"]
    tr = r1["transitions"][0]
    assert tr["tp1_hit_this_run"] is True
    assert tr["r_realized"] is None          # nichts realisiert — Signal laeuft weiter
    assert _signal("AAPL")["status"] == "OPEN"
    r2 = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert r2["transitions"] == []           # tp1_hit_at war schon gesetzt


def test_tracker_tp2_transition_with_implied_tp1(tracker):
    """TP2 => new_status TP2_HIT mit Geometrie-R; TP1 fiel im selben Lauf."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(111.0, 99.0, 110.5)])  # Tag 1: High >= TP2
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert [t["new_status"] for t in result["transitions"]] == ["TP2_HIT"]
    tr = result["transitions"][0]
    assert tr["r_realized"] == pytest.approx(2.0)   # (110-100)/(100-95)
    assert tr["tp1_hit_this_run"] is True           # TP2 impliziert TP1 (gleicher Lauf)
    assert _signal("AAPL")["status"] == "TP2_HIT"


def test_terminal_update_is_durable_until_idempotent_ack(tracker):
    recipient_key = "c" * 64
    tracker.record_alert_signals(
        "breakout",
        [_base_row()],
        delivery_recipient_keys=[recipient_key],
        mail_channel="stocks_premarket",
    )
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": bars})
    )
    signal_id = result["transitions"][0]["id"]

    pending = tracker.load_pending_terminal_updates()
    assert len(pending) == 1
    assert pending[0]["id"] == signal_id
    assert pending[0]["new_status"] == "STOP_HIT"
    assert pending[0]["tracker_persisted"] is True
    assert pending[0]["mail_channel"] == "stocks_premarket"
    assert pending[0]["delivery_recipient_keys_json"] == f'["{recipient_key}"]'

    assert tracker.mark_terminal_updates_sent([signal_id]) == 1
    assert tracker.load_pending_terminal_updates() == []
    assert tracker.mark_terminal_updates_sent([signal_id]) == 0


def test_tracker_no_status_change_means_empty_transitions(tracker):
    """Kein Level beruehrt => Signal bleibt OPEN, transitions leer."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(103.0, 99.0, 102.0)])
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    assert result["transitions"] == []
    assert _signal("AAPL")["status"] == "OPEN"


def test_tracker_return_stays_backward_compatible(tracker):
    """Bestands-Kontrakt: Strikte Dict-Vergleiche der Alt-Tests bleiben wahr
    (auch in Laeufen MIT Transition), evaluated/closed/errors unveraendert —
    'transitions' ist reiner Zusatz und fuer alle anderen Zugriffe sichtbar."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])  # Stop am Tag 1
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    # Exakt die Vergleichsform aus test_signal_tracker.py:
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    assert {"evaluated": 1, "closed": 1, "errors": 0} == result  # symmetrisch
    assert len(result["transitions"]) == 1
    assert "transitions" in result and "transitions" in dict(result)
    # Folgelauf ohne offene Signale: Alt-Form ebenfalls stabil, transitions leer
    follow_up = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({}))
    assert follow_up == {"evaluated": 0, "closed": 0, "errors": 0}
    assert follow_up["transitions"] == []


# ── Helpers bg (Muster test_mail_gates_bg.py) ────────────────────────────────
def _transition(**overrides):
    recipient_key = bg_service._recipient_delivery_key("followup@example.com")
    tr = {
        "id": 7, "ticker": "UNF", "scanner": "bi_long", "direction": "LONG",
        "old_status": "OPEN", "new_status": "STOP_HIT", "entry": 10.0,
        "stop": 9.5, "tp1": 11.0, "tp2": 11.8, "r_realized": -1.0,
        "tp1_hit_this_run": False, "asset_class": "stock",
        "mail_class": "trade", "channel": "email",
        "trade_horizon": "swing",
        "delivery_recipient_keys": [recipient_key],
    }
    tr.update(overrides)
    return tr


def _setup_bg(monkeypatch, tmp_path, transitions, origin_keys=()):
    """bg-Setup: Dedupe-File auf tmp_path, Startup-Delay aus, Mail-Recorder,
    Eval-Stub mit festen Transitionen; origin_keys = Erst-Mail-Marks."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    payload = {"evaluated": len(transitions), "closed": 0, "errors": 0,
               "transitions": list(transitions)}
    monkeypatch.setattr(bg_service, "evaluate_open_signals", lambda **kw: payload)
    monkeypatch.setattr(
        bg_service, "_reconcile_pending_accepted_deliveries", lambda: 0
    )
    monkeypatch.setattr(bg_service, "load_pending_terminal_updates", lambda: [])
    monkeypatch.setattr(
        bg_service,
        "mark_terminal_updates_sent",
        lambda signal_ids: len(list(signal_ids)),
    )
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
    if origin_keys:
        Path(bg_service._EMAIL_DEDUPE_FILE).write_text(
            json.dumps({key: time.time() for key in origin_keys})
        )
    sent = []

    def _recorder(subject, body_html, secrets, mail_class="trade", **kwargs):
        sent.append(
            {
                "subject": subject,
                "body": body_html,
                "mail_class": mail_class,
                "recipients": kwargs.get("recipient_emails"),
            }
        )
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    return sent, payload


# ── Teil 2: bg-ℹ️-Sammelmail im signal_eval-Job ──────────────────────────────
def test_bg_transitions_send_exactly_one_info_mail(monkeypatch, tmp_path):
    """Sammelmail: alle Transitionen eines Laufs in EINER Info-Mail; Body mit
    Ticker + Ereignis + R; UNTRACKED ist kein Exit-Ereignis und fliegt raus."""
    transitions = [
        _transition(),
        _transition(id=8, ticker="MBX", scanner="crypto_explosion",
                    new_status="TP1_HIT_OPEN", r_realized=None,
                    tp1_hit_this_run=True, asset_class="crypto",
                    entry=2.0, stop=1.8, tp1=2.4, tp2=2.8),
        _transition(id=9, ticker="OLDX", scanner="biotech",
                    new_status="EXPIRED", r_realized=0.4),
        _transition(id=11, ticker="GHST", scanner="bi_long",
                    new_status="UNTRACKED", r_realized=None),
    ]
    sent, _ = _setup_bg(
        monkeypatch, tmp_path, transitions,
        origin_keys=("bi_long_UNF", "crypto_explosion_MBX", "biotech_OLDX", "bi_long_GHST"),
    )
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1
    mail = sent[0]
    assert mail["mail_class"] == "signal_update"
    assert mail["recipients"] == ["followup@example.com"]
    assert mail["subject"] == "Signal-Update: 3 Position(en) — 1 Stop / 1 TP"
    body = mail["body"]
    assert "UNF" in body and "Stop erreicht" in body and "-1.00R" in body
    assert "MBX" in body and "TP1 erreicht — Rest Freiroll Richtung TP2" in body
    assert "OLDX" in body and "Verfallen" in body and "+0.40R" in body
    assert "GHST" not in body  # UNTRACKED = Datenproblem, kein Abo-Ereignis


def test_bg_same_transition_second_run_is_deduped(monkeypatch, tmp_path):
    """Persistenter Key signal_update_{id}_{status}: Re-Eval => keine 2. Mail."""
    sent, _ = _setup_bg(monkeypatch, tmp_path, [_transition()],
                        origin_keys=("bi_long_UNF",))
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1
    marks = json.loads(Path(bg_service._EMAIL_DEDUPE_FILE).read_text())
    assert "signal_update_7_STOP_HIT" in marks
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1


def test_bg_reloads_terminal_transition_after_crash_and_acks_once(
    tracker, monkeypatch, tmp_path
):
    """A crash after tracker commit but before enqueue loses no exit update."""
    recipient_key = bg_service._recipient_delivery_key("followup@example.com")
    tracker.record_alert_signals(
        "breakout",
        [_base_row()],
        delivery_recipient_keys=[recipient_key],
        mail_channel="stocks_swing",
    )
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])
    committed = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": bars})
    )
    assert len(committed["transitions"]) == 1
    # Simulated process loss: the in-memory transition is discarded here.
    sent, _ = _setup_bg(monkeypatch, tmp_path, [])
    monkeypatch.setattr(
        bg_service, "load_pending_terminal_updates",
        tracker.load_pending_terminal_updates,
    )
    monkeypatch.setattr(
        bg_service, "mark_terminal_updates_sent",
        tracker.mark_terminal_updates_sent,
    )

    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1
    assert "AAPL" in sent[0]["body"]
    assert tracker.load_pending_terminal_updates() == []

    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1


def test_bg_reconciles_cross_db_acceptance_without_second_smtp(
    monkeypatch,
):
    recipient_key = bg_service._recipient_delivery_key("a@example.com")
    finalized = []
    acknowledged = []

    class JournalOutbox:
        @staticmethod
        def load_tracker_acceptance_pending():
            return [{
                "intent_key": "intent:accepted",
                "accepted_recipient_keys": [recipient_key],
                "accepted_at": 12_345.0,
            }]

        @staticmethod
        def mark_tracker_acceptance_done(intent_key):
            acknowledged.append(intent_key)
            return True

    def _finalize(intent_key, recipient_keys, accepted_at=None):
        finalized.append((intent_key, tuple(recipient_keys), accepted_at))
        return {"activated": True, "signal_ids": [41, 42]}

    monkeypatch.setattr(bg_service, "_mail_outbox", JournalOutbox())
    monkeypatch.setattr(bg_service, "load_pending_accepted_deliveries", lambda: [])
    monkeypatch.setattr(bg_service, "finalize_alert_delivery", _finalize)
    monkeypatch.setattr(
        bg_service,
        "_send_email_alert",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("acceptance reconciliation must never call SMTP")
        ),
    )

    assert bg_service._reconcile_pending_accepted_deliveries() == 2
    assert finalized == [
        ("intent:accepted", (recipient_key,), 12_345.0)
    ]
    assert acknowledged == ["intent:accepted"]


def test_bg_no_transitions_no_mail(monkeypatch, tmp_path):
    sent, _ = _setup_bg(monkeypatch, tmp_path, [])
    stats = bg_service._run_signal_eval_job(secrets={})
    assert sent == []
    assert stats["evaluated"] == 0


def test_bg_failing_mail_builder_still_returns_eval_stats(monkeypatch, tmp_path):
    """Fehler im Update-Mail-Bau darf den Eval-Job NIE crashen."""
    sent, payload = _setup_bg(monkeypatch, tmp_path, [_transition()],
                              origin_keys=("bi_long_UNF",))

    def _boom(*a, **k):
        raise RuntimeError("Mail-Bau kaputt")

    monkeypatch.setattr(bg_service, "_send_signal_update_mail", _boom)
    stats = bg_service._run_signal_eval_job(secrets={})
    assert stats is payload  # Eval-Ergebnis kommt trotz Mail-Crash zurueck
    assert sent == []


def test_bg_without_origin_mark_no_update_mail(monkeypatch, tmp_path):
    """Nicht per E-Mail versendetes Ursprungssignal bleibt fail-closed."""
    sent, _ = _setup_bg(
        monkeypatch,
        tmp_path,
        [_transition(channel="telegram")],
    )
    bg_service._run_signal_eval_job(secrets={})
    assert sent == []
    dedupe_file = Path(bg_service._EMAIL_DEDUPE_FILE)
    marks = json.loads(dedupe_file.read_text()) if dedupe_file.exists() else {}
    assert not any(str(k).startswith("signal_update_") for k in marks)


def test_tracker_email_origin_does_not_expire_with_legacy_dedupe(monkeypatch, tmp_path):
    """Lange Swing-/BI-Trades behalten ihren dauerhaften Mail-Nachweis."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    Path(bg_service._EMAIL_DEDUPE_FILE).write_text(
        json.dumps({"bi_long_UNF": time.time() - 8 * 86400})
    )
    assert bg_service._signal_origin_was_mailed(_transition()) is True
    assert bg_service._signal_origin_was_mailed("bi_long", "UNF") is False


@pytest.mark.parametrize(
    "origin",
    [
        {"id": 7, "mail_class": "shadow", "channel": "email"},
        {"id": 7, "mail_class": "trade", "channel": "telegram"},
        {"id": 0, "mail_class": "trade", "channel": "email"},
    ],
)
def test_tracker_origin_provenance_fails_closed(origin):
    assert bg_service._signal_origin_was_mailed(origin) is False


def test_bg_new_listing_origin_matches_raw_symbol_mark(monkeypatch, tmp_path):
    """new_listing markiert das ROH-Symbol (B3), der Tracker fuehrt das
    Display-Symbol — die Zweitsicherung muss beide verheiraten."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    Path(bg_service._EMAIL_DEDUPE_FILE).write_text(
        json.dumps({"new_listing_TSTUSDT": time.time()})
    )
    assert bg_service._signal_origin_was_mailed("new_listing", "TST") is True
    assert bg_service._signal_origin_was_mailed("new_listing", "OTHER") is False
    assert bg_service._signal_origin_was_mailed("bi_long", "TST") is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
