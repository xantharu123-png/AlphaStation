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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
