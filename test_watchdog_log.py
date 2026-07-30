#!/usr/bin/env python3
"""Tests fuer modules/watchdog_log.py — JSONL-Ereignis-Log des Scan-Waechters.

Deckt: Roundtrip (log -> load), Tage-Filter, Toleranz gegen korrupte Zeilen,
FIFO-Rotation am Zeilen-Cap, Summarize-Aggregation (warn/reset/recovery,
bg-Varianten), Nie-werfen-Garantie bei kaputtem Pfad.

Komplett offline: Pfad via monkeypatch auf tmp_path.
"""
import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from modules import watchdog_log as wl


def _use_tmp(monkeypatch, tmp_path):
    path = str(tmp_path / "watchdog_events.jsonl")
    monkeypatch.setattr(wl, "WATCHDOG_EVENTS_PATH", path)
    return path


def _read_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ── Roundtrip + Filter ───────────────────────────────────────────────────────

def test_log_and_load_roundtrip(monkeypatch, tmp_path):
    path = _use_tmp(monkeypatch, tmp_path)
    wl.log_watchdog_event("warn", "strategy_scan", stuck_min=12.0, mailed=True)
    wl.log_watchdog_event("recovery", "strategy_scan", stuck_min=31.4, mailed=True)
    events = wl.load_watchdog_events(days=7)
    assert len(events) == 2
    assert events[0]["kind"] == "warn"
    assert events[0]["scanner"] == "strategy_scan"
    assert events[0]["mailed"] is True
    assert events[1]["kind"] == "recovery"
    assert events[1]["stuck_min"] == 31.4
    assert os.path.exists(path)


def test_load_filters_old_events(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    now = 1_800_000_000.0
    old = {"ts": now - 10 * 86400, "kind": "warn", "scanner": "a",
           "stuck_min": 5, "mailed": True, "throttled": False}
    fresh = {"ts": now - 3600, "kind": "warn", "scanner": "b",
             "stuck_min": 7, "mailed": True, "throttled": False}
    with open(wl.WATCHDOG_EVENTS_PATH, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(old) + "\n" + json.dumps(fresh) + "\n")
    events = wl.load_watchdog_events(days=7, now=now)
    assert len(events) == 1
    assert events[0]["scanner"] == "b"


def test_load_tolerates_corrupt_lines(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    good = {"ts": 1_800_000_000.0, "kind": "warn", "scanner": "x",
            "stuck_min": 5, "mailed": False, "throttled": True}
    with open(wl.WATCHDOG_EVENTS_PATH, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(good) + "\n")
        fh.write("{kaputt json\n")
        fh.write("\n")
        fh.write(json.dumps([1, 2, 3]) + "\n")  # kein dict
        fh.write(json.dumps({"kind": "warn"}) + "\n")  # ts fehlt -> ts=0 -> alt
    events = wl.load_watchdog_events(days=7, now=1_800_000_100.0)
    assert len(events) == 1
    assert events[0]["scanner"] == "x"


def test_load_missing_file_returns_empty(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert wl.load_watchdog_events(days=7) == []


def test_invalid_kind_is_dropped(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    wl.log_watchdog_event("bogus", "x")
    assert wl.load_watchdog_events(days=7) == []


def test_never_raises_on_broken_path(monkeypatch, tmp_path):
    # Pfad in ein Verzeichnis, das nicht angelegt werden kann (Datei als Dir)
    blocker = tmp_path / "blocker"
    blocker.write_text("bin eine datei", encoding="utf-8")
    monkeypatch.setattr(wl, "WATCHDOG_EVENTS_PATH", str(blocker / "sub" / "ev.jsonl"))
    wl.log_watchdog_event("warn", "x")  # darf nicht werfen
    assert wl.load_watchdog_events(days=7) == []


# ── Rotation ─────────────────────────────────────────────────────────────────

def test_rotation_trims_to_cap(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    pad = "p" * 220  # ~330 Bytes/Zeile => Datei > 512KB-Groessen-Gate
    with open(wl.WATCHDOG_EVENTS_PATH, "w", encoding="utf-8") as fh:
        for i in range(wl.MAX_LINES + 10):
            fh.write(json.dumps({"ts": 1_800_000_000.0, "kind": "warn",
                                 "scanner": f"s{i}-{pad}", "stuck_min": 1,
                                 "mailed": True, "throttled": False}) + "\n")
    wl.log_watchdog_event("warn", "neu", stuck_min=1, mailed=True)
    events = _read_lines(wl.WATCHDOG_EVENTS_PATH)
    assert len(events) == wl.TRIM_TO + 1
    assert events[-1]["scanner"] == "neu"
    # behalten wurden die letzten TRIM_TO der urspruenglichen Zeilen (s510..)
    assert events[0]["scanner"].startswith(f"s{wl.MAX_LINES + 10 - wl.TRIM_TO}-")


# ── Summarize ────────────────────────────────────────────────────────────────

def test_summarize_counts_and_durations():
    events = [
        {"ts": 1, "kind": "warn", "scanner": "a", "stuck_min": 10, "mailed": True},
        {"ts": 2, "kind": "warn", "scanner": "a", "stuck_min": 20,
         "mailed": False, "throttled": True},
        {"ts": 3, "kind": "reset", "scanner": "a", "stuck_min": 30, "mailed": True},
        {"ts": 4, "kind": "recovery", "scanner": "a", "mailed": True},
        {"ts": 5, "kind": "bg_warn", "scanner": "bi_scan", "stuck_min": 16,
         "mailed": True},
        {"ts": 6, "kind": "bg_recovery", "scanner": "bi_scan", "mailed": True},
    ]
    agg = wl.summarize_watchdog_events(events)
    a = agg["per_scanner"]["a"]
    assert a["episodes"] == 3          # 2 warn + 1 reset
    assert a["mailed"] == 2            # warn#1 + reset
    assert a["throttled"] == 1
    assert a["resets"] == 1
    assert a["recoveries"] == 1
    assert a["avg_stuck_min"] == 20.0  # (10+20+30)/3
    bi = agg["per_scanner"]["bi_scan"]
    assert bi["episodes"] == 1
    assert bi["recoveries"] == 1
    assert agg["total"]["episodes"] == 4
    assert agg["total"]["recoveries"] == 2
    assert agg["events"] == 6


def test_summarize_empty():
    agg = wl.summarize_watchdog_events([])
    assert agg["total"]["episodes"] == 0
    assert agg["per_scanner"] == {}
