#!/usr/bin/env python3
"""Pytest-Suite Scan-Waechter (Haenge-Alarm + Selbstheilung, AUDIT 2026-07-30).

Teil 1 (api.py): _scan_watchdog_check erkennt Budget-Risse, mailt EINMAL je
Episode an den Betreiber (persistentes Dedupe) und setzt am Hartdeckel
(3x Budget, min. +15 Min) den Zustand zurueck, damit der naechste Takt
frisch startet. Isolierter Alt-Thread wird nie dupliziert-gekillt.

Teil 2 (bg_service.py): Herzschlag-Waechter fuer die sequenzielle
Hauptschleife — _bg_stuck_decision (pure) + Alarm-Mail mit Scan-Namen und
Restart-Befehl, einmal je Episode, Re-Arming nach Erholung.

Komplett offline: Status-Dicts gemockt, Dedupe-Datei auf tmp_path,
Mail-Recorder per monkeypatch.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import api
import bg_service


# ── Helpers api ──────────────────────────────────────────────────────────────
def _setup_api(monkeypatch, tmp_path, name="crypto_explosion", timeout_min=25,
               started_ago_sec=0, running=True):
    """Scan-Status kontrolliert 'haengen' lassen + Mail-Recorder + tmp-Dedupe."""
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setitem(api._SCAN_TIMEOUTS, name, timeout_min)
    started_at = time.time() - started_ago_sec
    monkeypatch.setitem(api._scan_status, name, {
        "running": running, "last_run": None, "next_run": None,
        "interval_min": 15, "_started_at": started_at,
    })
    sent = []

    def _recorder(subject, body_html, **kwargs):
        sent.append({"subject": subject, "body": body_html,
                     "mail_class": kwargs.get("mail_class")})
        return True

    monkeypatch.setattr(api, "_send_email_alert", _recorder)
    return sent, started_at


# ── Teil 1: api — _scan_watchdog_check ───────────────────────────────────────
def test_api_stuck_scan_sends_one_warning_mail(monkeypatch, tmp_path):
    """Budget-Riss => 'stuck' + Warn-Mail mit Scanner-Name, Budget und
    Restart-Hinweis; last_error + _timeout_logged wie bisher gesetzt."""
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=26 * 60)
    event = api._scan_watchdog_check("crypto_explosion")
    assert event == "stuck"
    assert len(sent) == 1
    mail = sent[0]
    assert mail["mail_class"] == "info"
    assert "crypto_explosion" in mail["subject"]
    assert "haengt" in mail["subject"]
    assert "Budget 25 Min" in mail["body"]
    assert "systemctl restart tradingbot-api" in mail["body"]
    state = api._scan_status["crypto_explosion"]
    assert state["_timeout_logged"] is True
    assert "Zeitbudget" in state["last_error"]


def test_api_stuck_mail_only_once_per_episode(monkeypatch, tmp_path):
    """Zweiter Check derselben Episode => kein Event, keine zweite Mail.
    Und selbst ein neuer Prozess (nur Dedupe-Datei) mailt nicht erneut."""
    sent, started_at = _setup_api(monkeypatch, tmp_path, started_ago_sec=30 * 60)
    assert api._scan_watchdog_check("crypto_explosion") == "stuck"
    assert api._scan_watchdog_check("crypto_explosion") is None  # _timeout_logged
    assert len(sent) == 1
    # Frischer Status ohne _timeout_logged (Restart), gleiche Episode
    # (gleiches _started_at) => persistentes Dedupe faengt die Mail ab.
    api._scan_status["crypto_explosion"].pop("_timeout_logged", None)
    api._scan_status["crypto_explosion"]["_started_at"] = started_at
    assert api._scan_watchdog_check("crypto_explosion") == "stuck"
    assert len(sent) == 1  # Dedupe-Key stuck_scan_{name}_{started_at} bekannt


def test_api_hard_cap_recovers_state_for_fresh_start(monkeypatch, tmp_path):
    """Hartdeckel (3x Budget, min. +15 Min): 'recovered', running=False,
    Thread-Register frei, zweite Mail mit 'automatisch zurueckgesetzt'."""
    name = "crypto_explosion"  # Budget 25 Min => Hartdeckel 75 Min
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=80 * 60)
    api._scan_threads[name] = object()  # Platzhalter: isolierter Alt-Thread
    event = api._scan_watchdog_check(name)
    assert event == "recovered"
    state = api._scan_status[name]
    assert state["running"] is False
    assert "automatisch zurueckgesetzt" in state["last_error"]
    assert name not in api._scan_threads
    assert len(sent) == 1
    assert "automatisch zurueckgesetzt" in sent[0]["subject"]
    assert "NICHT noetig" in sent[0]["body"]


def test_api_hard_cap_minimum_is_budget_plus_15min(monkeypatch, tmp_path):
    """Faustregel-Deckel: Budget 5 Min => Hartdeckel 20 Min (nicht 15)."""
    assert api._stuck_hard_cap_sec("penny_positions") == 20 * 60
    assert api._stuck_hard_cap_sec("crypto_explosion") == 75 * 60
    assert api._stuck_hard_cap_sec("unbekannt") == 30 * 60  # Default 10 -> 30


def test_api_within_budget_no_event(monkeypatch, tmp_path):
    """Scan laeuft noch im Budget => None, keine Mail, kein last_error."""
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=10 * 60)
    assert api._scan_watchdog_check("crypto_explosion") is None
    assert sent == []
    assert "last_error" not in api._scan_status["crypto_explosion"]


def test_api_not_running_no_event(monkeypatch, tmp_path):
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=99 * 60, running=False)
    assert api._scan_watchdog_check("crypto_explosion") is None
    assert sent == []


def test_api_watchdog_never_raises_on_garbage_state(monkeypatch, tmp_path):
    """Defensiv: kaputter Status (started_at als String etc.) => None, kein Raise."""
    monkeypatch.setitem(api._scan_status, "kaputt", {"running": True, "_started_at": "muell"})
    assert api._scan_watchdog_check("kaputt") is None


# ── Teil 2: bg — Herzschlag-Waechter ─────────────────────────────────────────
def test_bg_stuck_decision_pure():
    """Frisch => (None, False); stale => Episode-Key + Alarm; bereits
    alarmierte Episode => kein erneuter Alarm; Erholung => (None, False)."""
    now = time.time()
    assert bg_service._bg_stuck_decision(now, now - 60, None, 900) == (None, False)
    key, should = bg_service._bg_stuck_decision(now, now - 2000, None, 900)
    assert key == f"bg_stuck_{int(now - 2000)}" and should is True
    key2, should2 = bg_service._bg_stuck_decision(now, now - 2000, key, 900)
    assert key2 == key and should2 is False   # gleiche Episode: still
    # Neue Episode (anderer Herzschlag-Stand) => neuer Key => wieder Alarm
    key3, should3 = bg_service._bg_stuck_decision(now, now - 1500, key, 900)
    assert key3 != key and should3 is True


def test_bg_stuck_mail_names_scan_and_restart(monkeypatch):
    """Alarm-Mail: nennt den haengenden Scan, die Dauer und den Restart-Befehl."""
    sent = []

    def _recorder(subject, body_html, secrets, mail_class="trade", **kwargs):
        sent.append({"subject": subject, "body": body_html, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    ok = bg_service._send_bg_stuck_mail("bi_long (init)", 95 * 60, 90 * 60, {})
    assert ok is True
    mail = sent[0]
    assert mail["mail_class"] == "info"
    assert "Hintergrund-Dienst" in mail["subject"]
    assert "bi_long (init)" in mail["body"]
    assert "95 Min" in mail["body"]
    assert "systemctl restart tradingbot-bg" in mail["body"]


def test_bg_monitor_alerts_once_and_rearms_after_recovery(monkeypatch):
    """Monitor-Loop (2 Durchlaeufe simuliert ueber Hilfslogik): Episode wird
    nach Versand gemerkt, Erholung rearmiert den Waechter."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", None)  # In-Memory-Fallback
    # Entscheidungslogik direkt pruefen (Loop selbst laeuft 60s-Takt):
    now = time.time()
    bg_service._bg_stuck_alerted["key"] = None
    key, should = bg_service._bg_stuck_decision(now, now - 6000, bg_service._bg_stuck_alerted["key"], 5400)
    assert should is True
    bg_service._bg_stuck_alerted["key"] = key
    # Gleiche Episode, naechster Takt:
    _, should = bg_service._bg_stuck_decision(now + 60, now - 6000, bg_service._bg_stuck_alerted["key"], 5400)
    assert should is False
    # Erholung: frischer Herzschlag => (None, False) => Monitor rearmiert
    key_none, should_none = bg_service._bg_stuck_decision(now + 120, now + 100, bg_service._bg_stuck_alerted["key"], 5400)
    assert key_none is None and should_none is False


def test_bg_heartbeat_touch_updates_state():
    before = bg_service._bg_heartbeat["ts"]
    time.sleep(0.01)
    bg_service._bg_heartbeat_touch("bi_long")
    assert bg_service._bg_heartbeat["ts"] > before
    assert bg_service._bg_heartbeat["current"] == "bi_long"
    bg_service._bg_heartbeat_touch()  # ohne Namen: aktueller Scan bleibt stehen
    assert bg_service._bg_heartbeat["current"] == "bi_long"


# ── Teil 3: Entwarnungs-Mails (30.07., nach erstem Live-Alarm) ───────────────
def test_api_recovery_mail_after_warned_episode_completes(monkeypatch, tmp_path):
    """Warn-Episode -> erfolgreicher Folge-Lauf loest genau EINE Entwarnung
    ('laeuft wieder') mit Episoden-Dauer aus und loescht die Marker."""
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=26 * 60)
    assert api._scan_watchdog_check("crypto_explosion") == "stuck"
    assert len(sent) == 1
    state = api._scan_status["crypto_explosion"]
    assert state.get("_episode_started_at") is not None
    # Episode 'zu Ende': Haenger-Run weg, naechster Takt startet frisch
    state["running"] = False
    state["_started_at"] = None
    api._scan_threads.pop("crypto_explosion", None)
    monkeypatch.setattr(api, "_require_fresh_scan_cache", lambda *a, **k: None)
    api._run_scan_safe("crypto_explosion", lambda: None)
    t = api._scan_threads.get("crypto_explosion")
    assert t is not None
    t.join(timeout=5)
    assert not t.is_alive()
    assert len(sent) == 2
    rec = sent[1]
    assert "laeuft wieder" in rec["subject"]
    assert "crypto_explosion" in rec["subject"]
    assert "Min" in rec["body"] and "Kein Neustart noetig" in rec["body"]
    assert rec["mail_class"] == "info"
    state = api._scan_status["crypto_explosion"]
    assert "_episode_started_at" not in state
    assert "_recovered_at" not in state


def test_api_no_recovery_mail_without_episode(monkeypatch, tmp_path):
    """Normaler erfolgreicher Lauf ohne vorherige Warnung => keine Entwarnung."""
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=0, running=False)
    api._scan_threads.pop("crypto_explosion", None)
    monkeypatch.setattr(api, "_require_fresh_scan_cache", lambda *a, **k: None)
    api._run_scan_safe("crypto_explosion", lambda: None)
    t = api._scan_threads.get("crypto_explosion")
    assert t is not None
    t.join(timeout=5)
    assert sent == []


def test_api_recovery_mail_deduped_per_episode(monkeypatch, tmp_path):
    """Gleiche Episode (gleicher Start) => hoechstens eine Entwarnung,
    auch prozessuebergreifend (persistentes Dedupe, Mark erst nach Versand).
    Voraussetzung: die Episode wurde angekuendigt (Warn-Key aktiv, 30.07.)."""
    sent, started_at = _setup_api(monkeypatch, tmp_path)
    api._email_dedupe_mark(f"stuck_scan_crypto_explosion_{int(started_at)}")
    assert api._send_stuck_recovery_mail("crypto_explosion", 40 * 60, started_at) is True
    assert api._send_stuck_recovery_mail("crypto_explosion", 45 * 60, started_at) is False
    assert len(sent) == 1
    assert "Episode beendet nach ca. 40 Min" in sent[0]["body"]


def test_bg_recovery_decision_pure():
    """Ohne Alarm => (False, 0); nach Alarm => (True, Dauer ab letztem Herzschlag)."""
    now = time.time()
    assert bg_service._bg_recovery_decision({"key": None, "since": None}, now) == (False, 0.0)
    should, secs = bg_service._bg_recovery_decision({"key": "bg_stuck_1", "since": now - 95 * 60}, now)
    assert should is True and secs == pytest.approx(95 * 60, abs=1)


def test_bg_recovery_mail_content(monkeypatch):
    """Entwarnungs-Mail: 'laeuft wieder', Dauer, Scan-Name, kein Neustart noetig."""
    sent = []

    def _recorder(subject, body_html, secrets, mail_class="trade", **kwargs):
        sent.append({"subject": subject, "body": body_html, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    ok = bg_service._send_bg_recovery_mail("bi_long (init)", 95 * 60, {})
    assert ok is True
    mail = sent[0]
    assert mail["mail_class"] == "info"
    assert "laeuft wieder" in mail["subject"]
    assert "Hintergrund-Dienst" in mail["subject"]
    assert "95 Min" in mail["body"]
    assert "bi_long (init)" in mail["body"]
    assert "Kein Neustart noetig" in mail["body"]


# ── Teil 4: Anti-Spam-Throttle + Budget-Kalibrierung (30.07., Live-Flut) ────
def test_strategy_scan_budget_kalibriert():
    """strategy_scan (30-Min-Takt, 60+ Kandidaten) hat eigenes 25-Min-Budget
    statt 10-Min-Default; Hartdeckel = 3x Budget."""
    assert api._SCAN_TIMEOUTS["strategy_scan"] == 25
    assert api._stuck_hard_cap_sec("strategy_scan") == 75 * 60


def test_warn_throttle_one_mail_per_scanner_per_6h(monkeypatch, tmp_path):
    """Zwei Episoden desselben Scanners kurz hintereinander => nur die ERSTE
    mailt; die zweite Warnung haengt in der 6h-Throttle (Event bleibt 'stuck')."""
    sent, started1 = _setup_api(monkeypatch, tmp_path, timeout_min=10, started_ago_sec=26 * 60)
    assert api._scan_watchdog_check("crypto_explosion") == "stuck"
    assert len(sent) == 1
    # Neue Episode (neuer Start, weiterhin ueber Budget) — z.B. nach
    # Hartdeckel-Reset + frischem Haenger
    api._scan_status["crypto_explosion"].pop("_timeout_logged", None)
    api._scan_status["crypto_explosion"]["_started_at"] = started1 + 600
    assert api._scan_watchdog_check("crypto_explosion") == "stuck"
    assert len(sent) == 1  # Throttle: keine zweite Mail


def test_warn_throttle_rearms_after_6h(monkeypatch, tmp_path):
    """Throttle-Mark aelter als 6h => naechste Episode mailt wieder."""
    sent, _ = _setup_api(monkeypatch, tmp_path, started_ago_sec=26 * 60)
    api._shared_email_dedupe_mark(
        api._EMAIL_DEDUPE_FILE, "stuck_throttle_crypto_explosion",
        now=time.time() - 7 * 3600,
    )
    assert api._scan_watchdog_check("crypto_explosion") == "stuck"
    assert len(sent) == 1
    assert "haengt" in sent[0]["subject"]


def test_reset_mail_suppressed_when_episode_unannounced(monkeypatch, tmp_path):
    """Warnung war throttle-gedeckelt (Episode nie angekuendigt) => auch die
    Hartdeckel-Reset-Mail wird unterdrueckt; die Selbstheilung laeuft trotzdem."""
    sent, started = _setup_api(monkeypatch, tmp_path, started_ago_sec=80 * 60)
    # Simuliere: eine fruehere Episode hat die 6h-Throttle bereits verbraucht
    api._email_dedupe_mark("stuck_throttle_crypto_explosion")
    api._scan_threads["crypto_explosion"] = object()
    event = api._scan_watchdog_check("crypto_explosion")
    assert event == "recovered"
    assert api._scan_status["crypto_explosion"]["running"] is False
    assert sent == []  # weder Warnung noch Reset-Mail


def test_recovery_mail_suppressed_only_when_warn_throttled(monkeypatch, tmp_path):
    """Entwarnungs-Logik: unterdrueckt NUR bei throttle-gedeckelter Warnung;
    bei gescheiterter oder versandter Warnung geht die Entwarnung raus."""
    sent, started = _setup_api(monkeypatch, tmp_path)
    s1, s2, s3 = started, started - 3600, started - 7200
    # 1) Warn-Versand war gescheitert (kein Mark) => Entwarnung willkommen
    assert api._send_stuck_recovery_mail("crypto_explosion", 40 * 60, s1) is True
    # 2) Warnung throttle-gedeckelt => Entwarnung unterdrueckt (kontext-los)
    api._email_dedupe_mark("stuck_throttle_crypto_explosion")
    assert api._send_stuck_recovery_mail("crypto_explosion", 40 * 60, s2) is False
    # 3) Episode angekuendigt (Warn-Key) => Entwarnung trotz Throttle
    api._email_dedupe_mark(f"stuck_scan_crypto_explosion_{int(s3)}")
    assert api._send_stuck_recovery_mail("crypto_explosion", 40 * 60, s3) is True
    assert len(sent) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
