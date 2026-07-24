#!/usr/bin/env python3
"""Wochenreport-Mail (bg_service._run_weekly_report) — Regression-Tests.

Beweist den Freitag-nach-US-Close-Wochenreport des Signal-Trackers:
- Fenster-Logik: nur Freitag (ET) 16:15–23:00; Nachholen im Fenster,
  Verfall danach (kein Samstag-Nachschub)
- Persistentes Wochen-Dedupe weekly_report_{ISO-Jahr}W{ISO-Woche}
  => genau EINE Mail pro Woche, Mark erst nach erfolgreichem Versand (B2)
- Betreff-Format, Scanner-Tabelle mit Gewinn-/Verlust-Toenung,
  Kleine-Stichprobe-Hinweis (<30 entschieden), Leere-Woche-Lebenszeichen
- Werfender Summary-Loader crasht den Scheduler nicht und sendet nichts

Session-unabhaengig: Pfade via __file__, kein echter SMTP-/DB-Zugriff.
"""
import os
import sys
import time
from datetime import datetime as _real_datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bg_service


FRIDAY_1620 = _real_datetime(2026, 6, 12, 16, 20)   # Fr nach US-Close (im Fenster)
FRIDAY_1500 = _real_datetime(2026, 6, 12, 15, 0)    # Fr VOR Boersenschluss
FRIDAY_2000 = _real_datetime(2026, 6, 12, 20, 0)    # Fr abends (Nachhol-Fall)
SATURDAY_1700 = _real_datetime(2026, 6, 13, 17, 0)  # Samstag => Slot verfallen


class _FakeDatetime(_real_datetime):
    """Kontrollierte Uhr im bg-Namespace: now() liefert den gesetzten Zeitpunkt."""
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _summary(**total_overrides):
    """Realistische load_performance_summary(days=7)-Struktur (Schema 1:1)."""
    total = {
        "signals": 12, "open": 2, "tp1_hit": 3, "tp2_hit": 2, "stop_hit": 4,
        "expired": 1, "untracked": 0, "win_rate_pct": 60.0, "avg_r": 0.29,
        "sum_r": 3.5, "alerts_per_day": 1.714,
    }
    total.update(total_overrides)
    # T1/Kalibrier-Loop-Felder (Schema 1:1 zum echten Tracker)
    total.setdefault("avg_r_managed_50_50", 0.55)
    total["decided_signals"] = total["tp1_hit"] + total["tp2_hit"] + total["stop_hit"]
    total["sample_reliable"] = total["decided_signals"] >= 30
    total.setdefault("win_rate_wilson_95", {"lower_pct": 36.0, "upper_pct": 80.0})
    return {
        "generated_at": "2026-06-12T20:20:00+00:00",
        "window_days": 7,
        "total": total,
        "per_scanner": {
            "bi_long": {"signals": 7, "open": 1, "tp1_hit": 2, "tp2_hit": 2,
                        "stop_hit": 1, "expired": 1, "untracked": 0,
                        "win_rate_pct": 80.0, "avg_r": 0.92, "sum_r": 4.6,
                        "alerts_per_day": 1.0,
                        "avg_r_managed_50_50": 1.2, "decided_signals": 5,
                        "sample_reliable": False,
                        "win_rate_wilson_95": {"lower_pct": 36.0, "upper_pct": 99.0}},
            "bear_scan": {"signals": 5, "open": 1, "tp1_hit": 1, "tp2_hit": 0,
                          "stop_hit": 3, "expired": 0, "untracked": 0,
                          "win_rate_pct": 25.0, "avg_r": -0.275, "sum_r": -1.1,
                          "alerts_per_day": 0.714},
        },
        "recent": [
            {"id": 90 + i, "created_at": "2026-06-11T14:00:00+00:00",
             "scanner": "bi_long", "ticker": f"TK{i}", "asset_class": "stock",
             "direction": "LONG", "status": "TP2_HIT", "outcome_detail": "",
             "entry": 10.0, "stop": 9.5, "tp1": 11.0, "tp2": 11.8,
             "r_realized": 2.0, "tp1_hit_at": "2026-06-11T15:00:00+00:00",
             "r_managed_50_50": 1.5}
            for i in range(12)
        ],
    }


def _setup(monkeypatch, tmp_path, summary=None, now=FRIDAY_1620):
    """Dedupe auf tmp, Startup-Delay aus, Uhr fixiert, Versand aufgezeichnet."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    _FakeDatetime._now = now
    monkeypatch.setattr(bg_service, "datetime", _FakeDatetime)
    monkeypatch.setattr(
        bg_service, "load_performance_summary",
        lambda days=7: summary if summary is not None else _summary(),
        raising=False,
    )
    sent_mails = []

    def _recorder(subject, body_html, secrets, mail_class="trade"):
        sent_mails.append({"subject": subject, "body": body_html,
                           "secrets": secrets, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    return sent_mails


# ── Fenster-Logik + Dedupe ───────────────────────────────────────────────────

def test_friday_after_close_sends_info_mail(monkeypatch, tmp_path):
    """Freitag 16:20 ET (Fenster offen) => genau eine ℹ️-Mail."""
    sent = _setup(monkeypatch, tmp_path)
    assert bg_service._run_weekly_report({}) is True
    assert len(sent) == 1
    assert sent[0]["mail_class"] == "info"


def test_friday_before_close_sends_nothing(monkeypatch, tmp_path):
    """Freitag 15:00 ET (Boerse offen) => kein Versand."""
    sent = _setup(monkeypatch, tmp_path, now=FRIDAY_1500)
    assert bg_service._run_weekly_report({}) is False
    assert sent == []


def test_saturday_slot_expired_no_catchup(monkeypatch, tmp_path):
    """Samstag => Slot verfallen, KEIN Nachschicken."""
    sent = _setup(monkeypatch, tmp_path, now=SATURDAY_1700)
    assert bg_service._run_weekly_report({}) is False
    assert sent == []


def test_dedupe_exactly_one_mail_per_week(monkeypatch, tmp_path):
    """Zweiter Lauf in derselben ISO-Woche (Fr 20:00, Fenster noch offen)
    => persistentes Dedupe blockt, es bleibt bei EINER Mail."""
    sent = _setup(monkeypatch, tmp_path)
    assert bg_service._run_weekly_report({}) is True
    _FakeDatetime._now = FRIDAY_2000
    assert bg_service._run_weekly_report({}) is False
    assert len(sent) == 1


def test_failed_send_retries_later_in_window(monkeypatch, tmp_path):
    """B2-Muster + Nachhol-Fall: Versand um 16:20 scheitert (kein Dedupe-Mark)
    => Lauf um 20:00 im Fenster holt nach und sendet."""
    sent = _setup(monkeypatch, tmp_path)
    attempts = {"n": 0}

    def _flaky(subject, body_html, secrets, mail_class="trade"):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return False  # SMTP down um 16:20
        sent.append({"subject": subject, "body": body_html,
                     "secrets": secrets, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _flaky)
    assert bg_service._run_weekly_report({}) is False
    _FakeDatetime._now = FRIDAY_2000
    assert bg_service._run_weekly_report({}) is True
    assert len(sent) == 1 and attempts["n"] == 2


# ── Mail-Inhalt ──────────────────────────────────────────────────────────────

def test_subject_format_and_seven_day_window(monkeypatch, tmp_path):
    """Betreff exakt im Spezifikations-Format; Summary wird mit days=7 geladen.
    (ℹ️-Praefix setzt _apply_mail_class_prefix erst im echten Versand.)"""
    sent = _setup(monkeypatch, tmp_path)
    seen_days = []
    monkeypatch.setattr(
        bg_service, "load_performance_summary",
        lambda days: (seen_days.append(days), _summary())[1],
        raising=False,
    )
    bg_service._run_weekly_report({})
    assert seen_days == [7]
    assert sent[0]["subject"] == (
        "Wochenreport Signal-Tracker: +3.5R | 12 Signale | Hit-Rate 60%"
    )


def test_scanner_table_rows_and_pnl_tinting(monkeypatch, tmp_path):
    """Je-Scanner-Tabelle enthaelt beide Zeilen; Σ R >= 0 gruen (#e9f7ef),
    < 0 rot (#fdecea) — Toenung steht im <tr> VOR dem Scanner-Namen."""
    sent = _setup(monkeypatch, tmp_path)
    bg_service._run_weekly_report({})
    body = sent[0]["body"]
    assert "bi_long" in body and "bear_scan" in body
    assert "+4.6R" in body and "-1.1R" in body
    green_pos, red_pos = body.index("#e9f7ef"), body.index("#fdecea")
    assert green_pos < body.index("bi_long") < red_pos < body.index("bear_scan")
    # Kompakte Liste der letzten Signale (max ~10) mit Status
    assert "TK0" in body and "TK9" in body and "TK10" not in body
    assert "TP2_HIT" in body


def test_small_sample_hint_below_30_decided(monkeypatch, tmp_path):
    """9 entschiedene Signale (<30) => Stichproben-Hinweis im Body."""
    sent = _setup(monkeypatch, tmp_path)  # decided = 3+2+4 = 9
    bg_service._run_weekly_report({})
    assert ("Stichprobe noch klein — keine Schwellen-Entscheidungen "
            "daraus ableiten.") in sent[0]["body"]


def test_no_small_sample_hint_at_30_decided(monkeypatch, tmp_path):
    """Genau 30 entschiedene Signale => Hinweis entfaellt (Grenze ist <30)."""
    big = _summary(signals=33, open=2, tp1_hit=10, tp2_hit=10, stop_hit=10,
                   expired=1, untracked=0)
    sent = _setup(monkeypatch, tmp_path, summary=big)
    bg_service._run_weekly_report({})
    assert "Stichprobe noch klein" not in sent[0]["body"]


def test_empty_week_sends_lifesign_mail(monkeypatch, tmp_path):
    """0 Signale => Mail geht TROTZDEM raus (Lebenszeichen), gleiche Dedupe."""
    empty = {
        "generated_at": "2026-06-12T20:20:00+00:00", "window_days": 7,
        "total": {"signals": 0, "open": 0, "tp1_hit": 0, "tp2_hit": 0,
                  "stop_hit": 0, "expired": 0, "untracked": 0,
                  "win_rate_pct": None, "avg_r": None, "sum_r": 0.0,
                  "alerts_per_day": 0.0},
        "per_scanner": {}, "recent": [],
    }
    sent = _setup(monkeypatch, tmp_path, summary=empty)
    assert bg_service._run_weekly_report({}) is True
    assert len(sent) == 1
    assert "Keine Signale diese Woche" in sent[0]["body"]
    assert sent[0]["subject"].startswith(
        "Wochenreport Signal-Tracker: +0.0R | 0 Signale")
    # Gleiches Dedupe wie volle Woche: zweiter Lauf bleibt still
    assert bg_service._run_weekly_report({}) is False
    assert len(sent) == 1


def test_raising_summary_loader_no_crash_no_mail(monkeypatch, tmp_path):
    """Werfendes load_performance_summary => kein Crash, keine Mail,
    KEIN Dedupe-Mark (naechster Takt im Fenster darf erneut versuchen)."""
    sent = _setup(monkeypatch, tmp_path)

    def _boom(days=7):
        raise RuntimeError("DB kaputt")

    monkeypatch.setattr(bg_service, "load_performance_summary", _boom, raising=False)
    assert bg_service._run_weekly_report({}) is False
    assert sent == []
    assert not bg_service._email_dedupe_active(
        bg_service._weekly_report_dedupe_key(FRIDAY_1620),
        bg_service._WEEKLY_REPORT_DEDUPE_SEC,
    )



# ── T1/Kalibrier-Loop: Managed-R + Wilson im Mail-Body (AUDIT 2026-07-24) ────

def test_managed_r_column_in_head_and_scanner_table(monkeypatch, tmp_path):
    """T1: Ø R 50/50 in Kopf- UND Scanner-Tabelle (total +0.55R, bi_long +1.20R)."""
    sent = _setup(monkeypatch, tmp_path)
    bg_service._run_weekly_report({})
    body = sent[0]["body"]
    assert "Ø R 50/50" in body
    assert "+0.55R" in body  # total.avg_r_managed_50_50
    assert "+1.20R" in body  # bi_long.avg_r_managed_50_50


def test_wilson_ci_next_to_head_hit_rate(monkeypatch, tmp_path):
    """Kalibrier-Loop: Wilson-KI steht an der Hit-Rate der Kopftabelle."""
    sent = _setup(monkeypatch, tmp_path)
    bg_service._run_weekly_report({})
    assert "KI 36–80%" in sent[0]["body"]


def test_managed_r_in_recent_rows(monkeypatch, tmp_path):
    """Letzte Signale zeigen das 50/50-Management-R hinter r_realized."""
    sent = _setup(monkeypatch, tmp_path)
    bg_service._run_weekly_report({})
    assert "(50/50: +1.50R)" in sent[0]["body"]


def test_summary_without_new_fields_still_renders(monkeypatch, tmp_path):
    """Rueckwaertskompatibel: Summary ohne T1/Kalibrier-Felder => Spalte bleibt,
    Werte '–', kein KI-Span, kein Crash; decided-Fallback = lokale Summe."""
    legacy = _summary()
    for bucket in [legacy["total"], *legacy["per_scanner"].values()]:
        for key in ("avg_r_managed_50_50", "win_rate_wilson_95",
                    "decided_signals", "sample_reliable"):
            bucket.pop(key, None)
    for row in legacy["recent"]:
        row.pop("r_managed_50_50", None)
    sent = _setup(monkeypatch, tmp_path, summary=legacy)
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Ø R 50/50" in body        # Spaltenkoepfe bleiben
    assert "(KI " not in body         # kein Wilson-Span ohne Feld
    # decided-Fallback: 3+2+4 = 9 < 30 => Stichproben-Hinweis weiterhin da
    assert "Stichprobe noch klein" in body
