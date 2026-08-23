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
from datetime import datetime, timedelta, timezone
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
    for spec in specs:
        if len(spec) == 4:
            open_price, h, l, c = spec
        else:
            h, l, c = spec
            open_price = 100.0
        cursor += timedelta(days=1)
        while not st._is_us_equity_session(cursor):
            cursor += timedelta(days=1)
        bars.append({
            "date": cursor.isoformat(),
            "open": open_price,
            "high": h,
            "low": l,
            "close": c,
            "interval_complete": True,
        })
    return bars


def _stock_fetcher(bars_by_ticker):
    return lambda ticker, since_iso_date: bars_by_ticker.get(ticker)


def _mark_be_sent_after_activation(tracker, ticker):
    """Acknowledge the test update after its synthetic Daily activation."""
    signal = _signal(ticker)
    activated_at = tracker._parse_utc_datetime(signal["be_activated_at"])
    assert activated_at is not None
    signal_id = signal["id"]
    accepted_at = activated_at + timedelta(minutes=1)
    receipt_id = tracker.record_followup_delivery_receipt(
        signal_id,
        event_kind="BE",
        delivery_evidence_key=f"signal_be_{signal_id}_recipient_delivered",
        accepted_at=accepted_at,
    )
    assert receipt_id
    return tracker.mark_be_alerts_sent(
        [signal_id],
        sent_at=accepted_at,
        delivery_receipt_ids={signal_id: receipt_id},
    )


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    """Frische tmp-DB pro Test: ENV (Import-Kontrakt) + Modulglobale (Laufzeit)."""
    db_path = str(tmp_path / "signal_tracker_test.sqlite")
    monkeypatch.setenv("SIGNAL_TRACKER_DB_PATH", db_path)
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", db_path)
    monkeypatch.setattr(
        st, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", str(tmp_path / "acceptance.sqlite")
    )
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
        "public_signal_ref", "origin_evidence", "delivery_accepted_at",
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
    assert act["public_signal_ref"] is None
    assert act["origin_evidence"] == "direct_post_send"
    assert act["delivery_accepted_at"] is None
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
    _mark_be_sent_after_activation(tracker, "AAPL")
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


def test_tracker_undelivered_winner_close_keeps_r_realized_be_unresolved(tracker):
    """A triggered winner is not BE evidence before the update was delivered."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars1 = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    bars2 = _bars_after("AAPL", [(106.0, 99.0, 105.5), (111.0, 99.0, 110.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    sig = _signal("AAPL")
    assert sig["status"] == "TP2_HIT"
    assert sig["r_realized"] == pytest.approx(2.0)
    assert sig["be_mail_sent_at"] is None
    assert sig["r_realized_be"] is None


def test_tracker_delivered_winner_close_gets_r_realized_be_unchanged(tracker):
    """A delivered BE update plus a later winner is complete outcome evidence."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars1 = _bars_after("AAPL", [(106.0, 99.0, 105.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    _mark_be_sent_after_activation(tracker, "AAPL")
    bars2 = _bars_after(
        "AAPL", [(106.0, 99.0, 105.5), (101.0, 111.0, 101.0, 110.5)]
    )
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    sig = _signal("AAPL")
    assert sig["status"] == "TP2_HIT"
    assert sig["be_mail_sent_at"]
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

    sent_at = tracker._parse_utc_datetime(pending[0]["activated_at"])
    assert sent_at is not None
    sent_at += timedelta(minutes=1)
    receipt_id = tracker.record_followup_delivery_receipt(
        signal_id,
        event_kind="BE",
        delivery_evidence_key=f"signal_be_{signal_id}_recipient_delivered",
        accepted_at=sent_at,
    )
    assert receipt_id
    assert tracker.mark_be_alerts_sent(
        [signal_id],
        sent_at=sent_at,
        delivery_receipt_ids={signal_id: receipt_id},
    ) == 1
    assert tracker.load_pending_be_activations() == []
    assert tracker.mark_be_alerts_sent(
        [signal_id],
        delivery_receipt_ids={signal_id: receipt_id},
    ) == 1


def test_be_ack_requires_durable_event_receipt_not_synthetic_key(tracker):
    recipient_key = "b" * 64
    assert tracker.record_alert_signals(
        "breakout",
        [_base_row(Ticker="ACK-EVIDENCE")],
        delivery_recipient_keys=[recipient_key],
    ) == 1
    bars = _bars_after("ACK-EVIDENCE", [(106.0, 99.0, 105.5)])
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"ACK-EVIDENCE": bars})
    )
    signal_id = result["be_activations"][0]["id"]
    expected = f"signal_be_{signal_id}_recipient_delivered"

    assert tracker.mark_be_alerts_sent([signal_id]) == 0
    assert tracker.mark_be_alerts_sent(
        [signal_id], delivery_evidence_keys={signal_id: f"signal_be_{signal_id}"}
    ) == 0
    assert _signal("ACK-EVIDENCE")["be_mail_sent_at"] is None

    sent_at = tracker._parse_utc_datetime(
        _signal("ACK-EVIDENCE")["be_activated_at"]
    )
    assert sent_at is not None
    accepted_at = sent_at + timedelta(minutes=1)
    assert tracker.mark_be_alerts_sent(
        [signal_id],
        sent_at=accepted_at,
        delivery_evidence_keys={signal_id: expected},
    ) == 0
    assert _signal("ACK-EVIDENCE")["be_mail_sent_at"] is None

    recorder = getattr(tracker, "record_followup_delivery_receipt", None)
    assert callable(recorder)
    receipt_id = recorder(
        signal_id,
        event_kind="BE",
        delivery_evidence_key=expected,
        accepted_at=accepted_at,
    )
    assert isinstance(receipt_id, str) and receipt_id.startswith("fr1_")

    # The opaque receipt is bound to both the signal and event type.
    assert tracker.mark_be_alerts_sent(
        [signal_id + 999],
        sent_at=accepted_at,
        delivery_receipt_ids={signal_id + 999: receipt_id},
    ) == 0
    assert tracker.mark_terminal_updates_sent(
        [signal_id], delivery_receipt_ids={signal_id: receipt_id}
    ) == 0

    assert tracker.mark_be_alerts_sent(
        [signal_id],
        sent_at=accepted_at,
        delivery_receipt_ids={signal_id: receipt_id},
    ) == 1
    signal = _signal("ACK-EVIDENCE")
    assert signal["be_mail_sent_at"] is not None
    assert signal["be_delivery_evidence_key"] == receipt_id
    assert tracker._be_delivery_is_proven(signal) is True

    # Crash replay confirms the same correctly consumed receipt, but it cannot
    # be spent for another signal/event.
    assert tracker.mark_be_alerts_sent(
        [signal_id], delivery_receipt_ids={signal_id: receipt_id}
    ) == 1
    assert tracker.mark_be_alerts_sent(
        [signal_id + 999],
        delivery_receipt_ids={signal_id + 999: receipt_id},
    ) == 0

    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        receipt = conn.execute(
            "SELECT signal_id, event_kind, event_status, event_key_hash, "
            "accepted_at, consumed_at FROM followup_delivery_receipts "
            "WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
    finally:
        conn.close()
    assert receipt is not None
    assert receipt[0:3] == (signal_id, "BE", "")
    assert receipt[3] != expected
    assert receipt[4] is not None
    assert receipt[5] is not None


def test_legacy_sql_synthetic_be_key_remains_unproven(tracker):
    """Historical direct key/timestamp writes never become managed-BE proof."""
    assert tracker.record_alert_signals(
        "breakout", [_base_row(Ticker="LEGACY-BE-KEY")]
    ) == 1
    bars = _bars_after("LEGACY-BE-KEY", [(106.0, 99.0, 105.5)])
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"LEGACY-BE-KEY": bars})
    )
    signal_id = result["be_activations"][0]["id"]
    activated_at = tracker._parse_utc_datetime(
        _signal("LEGACY-BE-KEY")["be_activated_at"]
    )
    assert activated_at is not None
    delivered_at = activated_at + timedelta(minutes=1)
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET be_mail_sent_at=?, "
            "be_delivery_evidence_key=?, status='STOP_HIT', r_realized=-1.0, "
            "stop_hit_at=?, closed_at=? WHERE id=?",
            (
                delivered_at.isoformat(),
                f"signal_be_{signal_id}_recipient_delivered",
                delivered_at.isoformat(),
                delivered_at.isoformat(),
                signal_id,
            ),
        )

    signal = _signal("LEGACY-BE-KEY")
    assert tracker._be_delivery_is_proven(signal) is False
    assert tracker._breakeven_after_mfe_resolution(signal) == (None, True)


def test_same_ticker_be_direct_and_reloaded_events_keep_distinct_refs(tracker):
    """Two plans for one ticker remain separately traceable after a restart."""
    recipient_key = "e" * 64
    intent_key = "be-public-evidence"
    prepared = tracker.prepare_alert_delivery_intent(
        "breakout",
        [
            _base_row(Ticker="SAME", TP1=130.0, TP2=140.0),
            _base_row(Ticker="SAME", TP1=135.0, TP2=145.0),
        ],
        intent_key,
        mail_channel="stocks_swing",
    )
    assert prepared["send_allowed"] is True
    expected_refs = {signal["public_signal_ref"] for signal in prepared["signals"]}
    assert len(expected_refs) == 2
    accepted_at = datetime.now(timezone.utc)
    assert tracker.finalize_alert_delivery(
        intent_key, [recipient_key], accepted_at=accepted_at
    )["activated"] is True

    bars = _bars_after("SAME", [(106.0, 99.0, 105.5)])
    direct = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"SAME": bars})
    )["be_activations"]
    reloaded = tracker.load_pending_be_activations()
    assert {event["public_signal_ref"] for event in direct} == expected_refs
    assert {event["public_signal_ref"] for event in reloaded} == expected_refs

    direct_by_ref = {event["public_signal_ref"]: event for event in direct}
    reloaded_by_ref = {event["public_signal_ref"]: event for event in reloaded}
    for public_ref in expected_refs:
        for field in ("origin_evidence", "delivery_accepted_at", "mfe"):
            assert direct_by_ref[public_ref][field] == reloaded_by_ref[public_ref][field]
        assert direct_by_ref[public_ref]["origin_evidence"] == "smtp_acceptance"
        assert direct_by_ref[public_ref]["delivery_accepted_at"] == accepted_at.isoformat()


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
        "public_signal_ref": "AS1-0123456789ABCDEF0123",
        "origin_evidence": "smtp_acceptance",
        "delivery_accepted_at": "2026-08-21T10:00:00+00:00",
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
        lambda signal_ids, *, delivery_receipt_ids=None: len(list(signal_ids)),
    )
    monkeypatch.setattr(
        bg_service,
        "record_followup_delivery_receipt",
        lambda signal_id, **_kwargs: f"fr1_{int(signal_id):043d}",
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
                    entry_fill_price=10.0, stop=9.0, tp1=11.5, tp2=12.5, mfe=1.1,
                    public_signal_ref="AS1-FEDCBA9876543210ABCD",
                    delivery_accepted_at="2026-08-21T10:05:00+00:00",
                    origin_evidence="smtp_acceptance"),
    ]
    sent, _ = _setup_bg(monkeypatch, tmp_path, acts,
                        origin_keys=("stock_strategy_XYZ", "crash_CRSH"))
    bg_service._run_signal_eval_job(secrets={})
    assert len(sent) == 1
    mail = sent[0]
    assert mail["mail_class"] == "signal_update"
    assert mail["subject"] == (
        "Stop-Update: 2 Trade(s) auf Einstand sichern (MFE >= +1R beobachtet)"
    )
    body = mail["body"]
    assert "XYZ" in body and "50% verkaufen" in body
    assert "CRSH" in body and "KEIN Teilverkauf" in body
    assert "+1.20R" in body and "+1.10R" in body  # MFE-Spalte
    assert "AS1-0123456789ABCDEF0123" in body
    assert "AS1-FEDCBA9876543210ABCD" in body
    assert "21.08.2026 10:00 UTC / 12:00 MESZ" in body
    assert "21.08.2026 10:05 UTC / 12:05 MESZ" in body
    assert "Ursprung historisch nicht belegt" not in body
    assert "MFE >= +1R beobachtet" in body
    assert "Level-R (getrackter Planpfad)" in body
    assert "offen (nicht final)" in body
    assert "Abschluss-R" not in body
    assert "Zielgeometrie-R" not in body
    assert "historischer Kursfortschritt" in body
    assert "geplante Preisrisiko" in body
    assert "Gap-" in body and "Slippage-" in body
    assert "Ausfuehrungsrisiken bleiben bestehen" in body
    assert "Bedingter Management-Hinweis" in body
    assert "Keine neue Entry-Empfehlung; Management-Hinweis zum bestehenden Signal." in body
    assert "keine Handelsaufforderung" not in body.lower()
    for forbidden in ("risikofrei", "Freiroll", "Kursrisiko entfaellt", "+1R gelaufen"):
        assert forbidden.lower() not in body.lower()


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
    evidence_seen = []
    attempts = []

    monkeypatch.setattr(
        bg_service,
        "load_pending_be_activations",
        lambda: [] if acknowledged else [dict(activation)],
    )

    def _ack(signal_ids, *, delivery_receipt_ids=None):
        ids = list(signal_ids)
        acknowledged.extend(ids)
        evidence_seen.append(dict(delivery_receipt_ids or {}))
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
    assert evidence_seen == [{21: f"fr1_{21:043d}"}]

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
    _mark_be_sent_after_activation(tracker, "AAPL")
    bars2 = _bars_after("AAPL", [(106.0, 99.0, 105.5), (104.0, 94.5, 95.5)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    summary = tracker.load_performance_summary(days=7)
    total = summary["total"]
    assert total["avg_r"] == pytest.approx(-1.0)
    assert total["avg_r_be"] == pytest.approx(0.0)
    assert total["be_activations"] == 1
    assert total["be_saved"] == 1
    assert "trackerbasierte, preiswegabgeleitete BE-Gegenrechnung" in summary["r_semantics"]
    assert "keine Broker-Ausfuehrung" in summary["r_semantics"]
    assert "live gemessenes R" not in summary["r_semantics"]
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


def test_raw_be_summary_fails_closed_for_undelivered_trigger_winner_and_loser(
    tracker,
):
    """Missing BE delivery must not leave only the positive outcome in avg R."""
    assert tracker.record_alert_signals(
        "breakout",
        [_base_row(Ticker="BE-WINNER"), _base_row(Ticker="BE-LOSER")],
    ) == 2
    base = datetime.now(timezone.utc) - timedelta(days=2)
    fill_at = base + timedelta(minutes=1)
    trigger_at = base + timedelta(hours=1)
    closed_at = base + timedelta(hours=2)
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET entry_filled_at=?, entry_fill_price=entry, "
            "be_trigger_at=?, be_activated_at=?, closed_at=? "
            "WHERE ticker IN ('BE-WINNER','BE-LOSER')",
            (
                fill_at.isoformat(),
                trigger_at.isoformat(),
                trigger_at.isoformat(),
                closed_at.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, "
            "r_realized_be=2.0, max_favorable_r=2.2, tp1_hit_at=?, "
            "tp2_hit_at=? WHERE ticker='BE-WINNER'",
            (trigger_at.isoformat(), closed_at.isoformat()),
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "r_realized_be=NULL, max_favorable_r=1.2, stop_hit_at=? "
            "WHERE ticker='BE-LOSER'",
            (closed_at.isoformat(),),
        )
        conn.commit()

    summary = tracker.load_performance_summary(days=7)
    for bucket in (summary["total"], summary["per_scanner"]["breakout"]):
        assert bucket["avg_r_be"] is None
        assert bucket["be_decided_signals"] == 0
        assert bucket["be_unresolved"] == 2


def test_raw_be_summary_uses_complete_evidence_for_winner_and_loser(tracker):
    """Delivered winner and causal BE exit form a symmetric two-row sample."""
    assert tracker.record_alert_signals(
        "breakout",
        [_base_row(Ticker="BE-PROVEN-WIN"), _base_row(Ticker="BE-PROVEN-LOSS")],
    ) == 2
    base = datetime.now(timezone.utc) - timedelta(days=2)
    fill_at = base + timedelta(minutes=1)
    trigger_at = base + timedelta(hours=1)
    delivered_at = trigger_at + timedelta(minutes=1)
    closed_at = base + timedelta(hours=2)
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET entry_filled_at=?, entry_fill_price=entry, "
            "be_trigger_at=?, be_activated_at=?, closed_at=?, "
            "r_realized_be=NULL WHERE ticker IN ('BE-PROVEN-WIN','BE-PROVEN-LOSS')",
            (
                fill_at.isoformat(),
                trigger_at.isoformat(),
                trigger_at.isoformat(),
                closed_at.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, "
            "max_favorable_r=2.2, tp1_hit_at=?, tp2_hit_at=? "
            "WHERE ticker='BE-PROVEN-WIN'",
            (trigger_at.isoformat(), closed_at.isoformat()),
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "max_favorable_r=1.2, stop_hit_at=?, be_exit_fill_price=100.0, "
            "be_exit_at=?, be_exit_evidence_mode='daily_open_or_entry_level' "
            "WHERE ticker='BE-PROVEN-LOSS'",
            (closed_at.isoformat(), closed_at.isoformat()),
        )
        conn.commit()

    for ticker in ("BE-PROVEN-WIN", "BE-PROVEN-LOSS"):
        signal_id = _signal(ticker)["id"]
        receipt_id = tracker.record_followup_delivery_receipt(
            signal_id,
            event_kind="BE",
            delivery_evidence_key=f"signal_be_{signal_id}_recipient_delivered",
            accepted_at=delivered_at,
        )
        assert receipt_id
        assert tracker.mark_be_alerts_sent(
            [signal_id], delivery_receipt_ids={signal_id: receipt_id}
        ) == 1

    summary = tracker.load_performance_summary(days=7)
    for bucket in (summary["total"], summary["per_scanner"]["breakout"]):
        assert bucket["avg_r_be"] == pytest.approx(1.0)
        assert bucket["be_decided_signals"] == 2
        assert bucket["be_unresolved"] == 0
        assert bucket["be_saved"] == 1


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


def test_recent_payload_exposes_normalized_origin_and_mfe(tracker):
    """The dashboard payload keeps delivery provenance and observed MFE explicit."""
    tracker.record_alert_signals("breakout", [_base_row()])
    bars = _bars_after("AAPL", [(101.0, 94.5, 96.0)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))

    recent = tracker.load_performance_summary(days=7)["recent"]
    assert len(recent) == 1
    assert recent[0]["public_signal_ref"] is None
    assert recent[0]["origin_evidence"] == "direct_post_send"
    assert recent[0]["delivery_accepted_at"] is None
    assert recent[0]["mfe"] == pytest.approx(0.2)
    assert recent[0]["max_favorable_r"] == recent[0]["mfe"]
