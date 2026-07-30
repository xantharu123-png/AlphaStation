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
import json
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
                          "alerts_per_day": 0.714,
                          "avg_r_managed_50_50": -0.3, "decided_signals": 4,
                          "sample_reliable": False,
                          "win_rate_wilson_95": {"lower_pct": 4.0,
                                                 "upper_pct": 70.0}},
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
    monkeypatch.setattr(bg_service, "_WEEKLY_VERDICT_STATE_FILE",
                        str(tmp_path / "verdicts.json"))
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    # Waechter-Log hermetisch: kein Lesen aus data_cache/ (30.07.)
    monkeypatch.setattr(bg_service, "_load_watchdog_events", lambda days=7: [])
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



# ── Verdikt-Alarm (Kalibrier-Loop, AUDIT 2026-07-24) ────────────────────────

def _crash_bucket(decided=31):
    """behalten-Kandidat: BE = 54.8/1.5 = 36.5 < KI-Untergrenze 37.5."""
    wins = int(round(decided * 0.548))
    return {"signals": decided + 9, "open": 2, "tp1_hit": wins // 2,
            "tp2_hit": wins - wins // 2, "stop_hit": decided - wins,
            "expired": 7, "untracked": 0, "win_rate_pct": 54.8, "avg_r": 0.5,
            "sum_r": round(0.5 * decided, 1), "alerts_per_day": 5.7,
            "avg_r_managed_50_50": 0.45, "decided_signals": decided,
            "sample_reliable": decided >= 30,
            "win_rate_wilson_95": {"lower_pct": 37.5, "upper_pct": 71.2}}


def _summary_with_crash(decided=31):
    s = _summary()
    s["per_scanner"] = {"crash": _crash_bucket(decided)}
    return s


def _write_verdict_state(tmp_path, state):
    (tmp_path / "verdicts.json").write_text(
        json.dumps(state), encoding="utf-8")


def _read_verdict_state(tmp_path):
    return json.loads((tmp_path / "verdicts.json").read_text(encoding="utf-8"))


def test_verdict_alert_on_30_crossing(monkeypatch, tmp_path):
    """crash waechst 13 → 31 entschieden => 30er-Alarm, State aktualisiert."""
    _write_verdict_state(tmp_path, {"crash": {"decided": 13, "verdict": "beobachten"}})
    sent = _setup(monkeypatch, tmp_path, summary=_summary_with_crash(31))
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Verdikt-Alarm" in body
    assert "überschreitet 30er-Marke" in body
    assert "13 → 31 entschieden" in body
    assert "<b>behalten</b>" in body
    assert _read_verdict_state(tmp_path)["crash"] == {"decided": 31,
                                                      "verdict": "behalten"}


def test_verdict_alert_on_verdict_change(monkeypatch, tmp_path):
    """behalten → abschalten (KI-Obergrenze 45% < Breakeven 50%) => Alarm."""
    _write_verdict_state(tmp_path, {"stock_strategy": {"decided": 100,
                                                       "verdict": "behalten"}})
    s = _summary()
    s["per_scanner"] = {
        "stock_strategy": {"signals": 130, "open": 5, "tp1_hit": 10, "tp2_hit": 8,
                           "stop_hit": 42, "expired": 65, "untracked": 0,
                           "win_rate_pct": 30.0, "avg_r": -0.4, "sum_r": -24.0,
                           "alerts_per_day": 18.6, "avg_r_managed_50_50": -0.35,
                           "decided_signals": 60, "sample_reliable": True,
                           "win_rate_wilson_95": {"lower_pct": 22.0,
                                                  "upper_pct": 45.0}},
    }
    sent = _setup(monkeypatch, tmp_path, summary=s)
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Verdikt-Alarm" in body
    assert "Verdikt-Wechsel" in body
    assert "behalten → <b>abschalten</b>" in body


def test_no_verdict_alert_when_unchanged(monkeypatch, tmp_path):
    """State entspricht dem aktuellen Bild => kein Alarm-Block in der Mail."""
    _write_verdict_state(tmp_path, {
        "bi_long": {"decided": 5, "verdict": "beobachten"},
        "bear_scan": {"decided": 4, "verdict": "beobachten"},
    })
    sent = _setup(monkeypatch, tmp_path)  # _summary: bi_long 5, bear_scan 4
    assert bg_service._run_weekly_report({}) is True
    assert "Verdikt-Alarm" not in sent[0]["body"]


def test_baseline_run_saves_state_without_alarm(monkeypatch, tmp_path):
    """Erster Lauf ohne State-Datei: Baseline still speichern, kein Alarm."""
    sent = _setup(monkeypatch, tmp_path)
    assert bg_service._run_weekly_report({}) is True
    assert "Verdikt-Alarm" not in sent[0]["body"]
    state = _read_verdict_state(tmp_path)
    assert state["bi_long"] == {"decided": 5, "verdict": "beobachten"}
    assert state["bear_scan"] == {"decided": 4, "verdict": "beobachten"}


def test_failed_send_keeps_old_verdict_state(monkeypatch, tmp_path):
    """Versand-Fehler => Verdikt-State bleibt alt, Alarm geht nicht verloren."""
    old_state = {"crash": {"decided": 13, "verdict": "beobachten"}}
    _write_verdict_state(tmp_path, old_state)
    _setup(monkeypatch, tmp_path, summary=_summary_with_crash(31))
    monkeypatch.setattr(bg_service, "_send_email_alert",
                        lambda *args, **kwargs: False)  # SMTP down
    assert bg_service._run_weekly_report({}) is False
    assert _read_verdict_state(tmp_path) == old_state


# ── BE-Spalte + BE-Box (BE-Trigger, AUDIT 2026-07-30) ────────────────────────

def _summary_with_be():
    """_summary() + BE-Felder (Schema 1:1 zum echten Tracker seit 30.07.)."""
    s = _summary()
    s["total"]["avg_r_be"] = 0.61
    s["total"]["be_activations"] = 5
    s["total"]["be_saved"] = 2
    s["per_scanner"]["bi_long"]["avg_r_be"] = 1.4
    s["per_scanner"]["bi_long"]["be_activations"] = 4
    s["per_scanner"]["bi_long"]["be_saved"] = 1
    return s


def test_be_column_in_head_and_scanner_table(monkeypatch, tmp_path):
    """Ø R BE in Kopf- UND Scanner-Tabelle (total +0.61R, bi_long +1.40R)."""
    sent = _setup(monkeypatch, tmp_path, summary=_summary_with_be())
    bg_service._run_weekly_report({})
    body = sent[0]["body"]
    assert body.count("Ø R BE") >= 2   # Spaltenkopf in beiden Tabellen
    assert "+0.61R" in body            # total.avg_r_be
    assert "+1.40R" in body            # bi_long.avg_r_be


def test_be_box_with_activations_and_saved(monkeypatch, tmp_path):
    """Gruene BE-Box: Aktivierungen, verhinderte Verlierer, Ist-vs-BE-Vergleich."""
    sent = _setup(monkeypatch, tmp_path, summary=_summary_with_be())
    bg_service._run_weekly_report({})
    body = sent[0]["body"]
    assert "Einstand-Regel (seit 30.07. live)" in body
    assert "5 Signale" in body
    assert "2 vor einem Verlust bewahrt" in body
    assert "+0.29R" in body and "+0.61R" in body   # ØR Ist vs. ØR BE


def test_no_be_box_without_activations(monkeypatch, tmp_path):
    """Alt-Summary OHNE BE-Felder: keine Box, Spaltenkoepfe bleiben, Werte '–'."""
    sent = _setup(monkeypatch, tmp_path)  # _summary() ohne BE-Felder
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Ø R BE" in body                            # Spaltenkopf bleibt
    assert "Einstand-Regel (seit 30.07. live)" not in body   # keine Box
    assert "Einstand-Regel (Stop auf Einstand ab +1R" in body  # Footer-Semantik


# ── Waechter-Sektion (JSONL-Event-Log, 30.07.) ───────────────────────────────

def _wd_events_sample():
    """2 Warnungen (1 gedrosselt) + 1 Entwarnung fuer strategy_scan."""
    now = time.time()
    return [
        {"ts": now - 3600, "kind": "warn", "scanner": "strategy_scan",
         "stuck_min": 26, "mailed": True, "throttled": False},
        {"ts": now - 3500, "kind": "warn", "scanner": "strategy_scan",
         "stuck_min": 28, "mailed": False, "throttled": True},
        {"ts": now - 3400, "kind": "recovery", "scanner": "strategy_scan",
         "stuck_min": 30, "mailed": True, "throttled": False},
    ]


def test_watchdog_table_with_events(monkeypatch, tmp_path):
    """Gelber Block: Episoden-Zahl, Scanner-Name, Drossel-Hinweis, Tabelle."""
    sent = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bg_service, "_load_watchdog_events",
                        lambda days=7: _wd_events_sample())
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Scan-Waechter diese Woche: 2 Hänge-Episode(n)" in body
    assert "strategy_scan" in body
    assert "gedrosselt" in body
    assert "27 Min" in body  # Ø Dauer (26+28)/2
    assert "Keine Hänge-Episoden" not in body


def test_watchdog_all_clear_without_events(monkeypatch, tmp_path):
    """0 Episoden => gruene Entwarnungs-Zeile statt Tabelle."""
    sent = _setup(monkeypatch, tmp_path)  # _setup patcht Loader auf []
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Keine Hänge-Episoden diese Woche" in body
    assert "Hänge-Episode(n)" not in body


def test_watchdog_loader_failure_drops_section(monkeypatch, tmp_path):
    """Werfender Log-Loader => Sektion faellt weg, Report geht trotzdem raus."""
    sent = _setup(monkeypatch, tmp_path)

    def _boom(days=7):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(bg_service, "_load_watchdog_events", _boom)
    assert bg_service._run_weekly_report({}) is True
    body = sent[0]["body"]
    assert "Wochenreport Signal-Tracker" in body
    assert "Hänge-Episoden" not in body


def test_build_mail_watchdog_events_injected():
    """Direkt-Injektion (ohne Loader): Tabelle zeigt Episoden/Reset-Spalten."""
    subject, body = bg_service._build_weekly_report_mail(
        _summary(), now_et=FRIDAY_1620, watchdog_events=_wd_events_sample())
    assert "Scan-Waechter diese Woche" in body
    assert "Entwarnungen" in body
