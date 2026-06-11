# -*- coding: utf-8 -*-
"""Tests: prozessübergreifender Polygon-Rate-Limiter (Shared-Minuten-Budget).

Hintergrund: api.py (uvicorn) und bg_service.py sind GETRENNTE Prozesse; der
alte In-Prozess-Limiter in modules/data_fetchers.rate_limited_get zählte pro
Prozess => effektiv 2x 200/min gegen Polygon => 429er. Neu: EIN gemeinsames
Minuten-Budget (epoch//60 + Zähler) in einer Datei, serialisiert via
fcntl.flock; ENV POLYGON_BUDGET_PER_MIN (Default 200 GESAMT), Notausstieg
POLYGON_SHARED_BUDGET=0, Fallback auf In-Prozess-Limiter bei flock-Fehlern.

Diese Tests mocken requests und die Uhr (FakeClock ersetzt das time-Modul im
Modul-Namespace) — KEINE echten Polygon-Calls, kein echtes Schlafen. Die
Budget-/Lock-Datei-Pfade sind Modul-Konstanten und werden per monkeypatch
auf tmp_path umgebogen.

Pfad-Konvention: session-unabhängig via __file__ (keine hardcodeten Session-Pfade).
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import modules.data_fetchers as df  # noqa: E402

# Fenster-Arithmetik: Start exakt 10s nach einer Minutengrenze
# (1750000020 % 60 == 0), damit Warte-Erwartungen deterministisch sind.
WINDOW_BOUNDARY = 1750000020.0
START = WINDOW_BOUNDARY + 10.0
WINDOW = int(START // 60)

BIG_SLEEP = 1.0  # alles >= 1s ist ein Fenster-Wait; 0.05s-Spacing liegt darunter


class FakeClock:
    """Deterministische Uhr — ersetzt das time-Modul (nur .time/.sleep genutzt)."""

    def __init__(self, start=START):
        self.now = float(start)
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(float(seconds))
        self.now += max(0.0, float(seconds))

    def big_sleeps(self):
        return [s for s in self.sleeps if s >= BIG_SLEEP]


class FakeResponse:
    status_code = 200

    def json(self):
        return {}


class FakeRequests:
    """Ersetzt das requests-Modul im data_fetchers-Namespace — kein Netz."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append(url)
        return FakeResponse()


def _wire(mod, monkeypatch, tmp_path, clock=None, budget="5"):
    """Verdrahtet ein data_fetchers-Modul(-Exemplar) für deterministische Tests."""
    clock = clock or FakeClock()
    req = FakeRequests()
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod, "requests", req)
    monkeypatch.setattr(mod, "POLYGON_BUDGET_FILE", str(tmp_path / "polygon_rate_budget.json"))
    monkeypatch.setattr(mod, "POLYGON_BUDGET_LOCK_FILE", str(tmp_path / "polygon_rate_budget.lock"))
    monkeypatch.setattr(mod, "_shared_budget_failed", False)
    monkeypatch.setattr(mod, "_last_api_call", 0)
    monkeypatch.setattr(mod, "_api_call_count", 0)
    monkeypatch.setattr(mod, "_api_call_window_start", 0)
    monkeypatch.setenv("POLYGON_BUDGET_PER_MIN", budget)
    monkeypatch.delenv("POLYGON_SHARED_BUDGET", raising=False)
    return clock, req


def _read_state(tmp_path):
    with open(str(tmp_path / "polygon_rate_budget.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fresh_module(name):
    """Lädt eine frische Kopie von modules/data_fetchers.py — simuliert einen
    eigenen Prozess (eigener Modul-State, aber gleiche Budget-Datei)."""
    path = os.path.join(ROOT, "modules", "data_fetchers.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. Unter Budget: Calls passieren ohne Fenster-Wait, Zähler korrekt ────────
def test_unter_budget_keine_wartezeit(monkeypatch, tmp_path):
    clock, req = _wire(df, monkeypatch, tmp_path, budget="5")
    for _ in range(5):
        df.rate_limited_get("https://api.polygon.io/test")
    assert len(req.calls) == 5
    assert clock.big_sleeps() == []  # nur 0.05s-Spacing, kein Fenster-Wait
    assert _read_state(tmp_path) == {"window": WINDOW, "count": 5}


# ── 2. Budget erschöpft: nächster Call wartet bis zum Fensterwechsel ──────────
def test_budget_erschoepft_wartet_bis_fensterwechsel(monkeypatch, tmp_path):
    clock, req = _wire(df, monkeypatch, tmp_path, budget="5")
    for _ in range(5):
        df.rate_limited_get("https://api.polygon.io/test")
    assert clock.big_sleeps() == []

    df.rate_limited_get("https://api.polygon.io/test")  # 6. Call
    assert len(req.calls) == 6  # Call ging trotzdem raus — niemals Calls verlieren
    waits = clock.big_sleeps()
    assert len(waits) == 1, f"genau EIN Fenster-Wait erwartet, got {waits}"
    assert 40.0 <= waits[0] <= 60.0  # Rest des Minuten-Fensters (~49.8s)
    assert int(clock.now // 60) == WINDOW + 1  # erst NACH Fensterwechsel
    assert _read_state(tmp_path) == {"window": WINDOW + 1, "count": 1}


# ── 3. Zwei "Prozesse": Summe der Calls im Fenster == Budget, nicht 2x ────────
def test_zwei_prozesse_teilen_ein_budget(monkeypatch, tmp_path):
    proc_a = _fresh_module("df_proc_a")
    proc_b = _fresh_module("df_proc_b")
    clock = FakeClock()  # EINE Uhr für beide "Prozesse"
    _, req_a = _wire(proc_a, monkeypatch, tmp_path, clock=clock, budget="6")
    _, req_b = _wire(proc_b, monkeypatch, tmp_path, clock=clock, budget="6")

    # 6 Calls abwechselnd: alle laufen im selben Fenster durch (Summe == Budget)
    for i in range(6):
        mod = proc_a if i % 2 == 0 else proc_b
        mod.rate_limited_get("https://api.polygon.io/test")
    assert clock.big_sleeps() == []
    assert len(req_a.calls) + len(req_b.calls) == 6
    assert _read_state(tmp_path) == {"window": WINDOW, "count": 6}

    # 7. Call (egal welcher Prozess) wartet aufs nächste Fenster —
    # alter Bug wäre: jeder Prozess hat SEIN eigenes Budget (2x gesamt).
    proc_b.rate_limited_get("https://api.polygon.io/test")
    assert len(clock.big_sleeps()) == 1
    assert _read_state(tmp_path) == {"window": WINDOW + 1, "count": 1}


# ── 4. Korrupte Budget-Datei => Selbstheilung ─────────────────────────────────
def test_korrupte_budget_datei_selbstheilung(monkeypatch, tmp_path):
    clock, req = _wire(df, monkeypatch, tmp_path, budget="5")
    budget_file = tmp_path / "polygon_rate_budget.json"

    budget_file.write_text("{kaputt!! kein json", encoding="utf-8")
    df.rate_limited_get("https://api.polygon.io/test")
    assert len(req.calls) == 1
    assert _read_state(tmp_path) == {"window": WINDOW, "count": 1}

    # Variante: valides JSON, aber kein dict => ebenfalls Selbstheilung
    budget_file.write_text("[1, 2, 3]", encoding="utf-8")
    df.rate_limited_get("https://api.polygon.io/test")
    assert len(req.calls) == 2
    assert _read_state(tmp_path) == {"window": WINDOW, "count": 1}


# ── 5. flock wirft => Fallback auf In-Prozess-Limiter, Warnung EINMALIG ───────
class _BrokenFcntl:
    LOCK_EX = 2
    LOCK_UN = 8

    @staticmethod
    def flock(fd, op):
        raise OSError(5, "flock kaputt (exotisches FS)")


def test_flock_fehler_fallback_und_einmalige_warnung(monkeypatch, tmp_path, capsys):
    _, req = _wire(df, monkeypatch, tmp_path, budget="5")
    monkeypatch.setattr(df, "_fcntl", _BrokenFcntl())

    for _ in range(3):
        df.rate_limited_get("https://api.polygon.io/test")

    assert len(req.calls) == 3  # kein Call verloren, kein Crash
    out = capsys.readouterr().out
    assert out.count("Shared-Polygon-Budget deaktiviert") == 1  # EINMALIGE Warnung
    assert df._shared_budget_failed is True
    assert not (tmp_path / "polygon_rate_budget.json").exists()  # nie geschrieben
    assert df._api_call_count == 3  # In-Prozess-Limiter lief als Fallback weiter


# ── 6. ENV-Override POLYGON_SHARED_BUDGET=0 => altes Verhalten ────────────────
def test_env_override_altes_verhalten(monkeypatch, tmp_path):
    clock, req = _wire(df, monkeypatch, tmp_path, budget="2")
    monkeypatch.setenv("POLYGON_SHARED_BUDGET", "0")

    for _ in range(4):  # mehr Calls als das Shared-Budget (2) — darf NICHT bremsen
        df.rate_limited_get("https://api.polygon.io/test")

    assert len(req.calls) == 4
    assert clock.big_sleeps() == []  # kein Fenster-Wait
    assert not (tmp_path / "polygon_rate_budget.json").exists()
    assert not (tmp_path / "polygon_rate_budget.lock").exists()
    assert df._api_call_count == 4  # alter In-Prozess-Zähler aktiv
    assert df._shared_budget_failed is False  # kein Fallback-Status gesetzt


# ── 7. Fensterwechsel resettet den Zähler ─────────────────────────────────────
def test_fensterwechsel_resettet_zaehler(monkeypatch, tmp_path):
    clock, req = _wire(df, monkeypatch, tmp_path, budget="5")
    for _ in range(3):
        df.rate_limited_get("https://api.polygon.io/test")
    assert _read_state(tmp_path) == {"window": WINDOW, "count": 3}

    clock.now += 60.0  # nächstes Minuten-Fenster, ohne dass jemand warten musste
    df.rate_limited_get("https://api.polygon.io/test")
    assert _read_state(tmp_path) == {"window": WINDOW + 1, "count": 1}
    assert clock.big_sleeps() == []
    assert len(req.calls) == 4


# ── 8. Smoke: api + bg_service importieren mit neuem Limiter-Code ─────────────
def test_smoke_api_und_bg_service_importierbar():
    """Beide Prozesse müssen mit dem neuen Shared-Limiter-Code starten können."""
    code = "import api; import bg_service; print('SMOKE_OK')"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "POLYGON_SHARED_BUDGET": "1"},
    )
    assert proc.returncode == 0, f"Import-Smoke fehlgeschlagen:\n{proc.stderr[-2000:]}"
    assert "SMOKE_OK" in proc.stdout
