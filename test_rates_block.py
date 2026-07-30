#!/usr/bin/env python3
"""Pytest-Suite Zins-Block (Treasury Rates, FRED DGS2/10/30) — 2026-07-30.

Mess-First-Kontrakt: Der Block annotiert Market-Context und Signal-Tracker
(rates_json), aendert aber weder Scoring noch Gating. Die Suite sichert:
  - CSV-Parsing (fredgraph.csv: '.'-Feiertage, Header-Varianten, Sortierung)
  - bp-Aenderungen (5d/20d) und Regime-Grenzen (+/-10bp, +/-25bp)
  - Kurven-Spreads, Stale-Flag, ehrliche Missing-Bloecke
  - Market-Context: rates fliessen durch, overall_risk_score UNVERAENDERT
  - Tracker: rates_json-Spalte (Migration, Kompakt-JSON, None bei Missing)

Komplett offline: CSV-Texte und Serien werden injiziert, kein Netz.
"""
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import modules.market_context as mc
import modules.signal_tracker as st
import modules.treasury_rates as tr


# ── Fixtures / Helpers ───────────────────────────────────────────────────────
def _series(values, start="2026-06-01"):
    """Aufsteigende (date, value)-Beobachtungen aus Tageswerten."""
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), float(v)) for i, v in enumerate(values)]


def _full_series(dgs10_values, dgs2=4.20, dgs30=5.10):
    """SeriesMap mit konstanten DGS2/DGS30 neben der variablen DGS10."""
    n = len(dgs10_values)
    return {
        "DGS2": _series([dgs2] * n),
        "DGS10": _series(dgs10_values),
        "DGS30": _series([dgs30] * n),
    }


SAMPLE_CSV = """DATE,DGS2,DGS10,DGS30
2026-07-20,4.14,4.49,4.99
2026-07-21,4.15,.,5.01
2026-07-22,4.18,4.55,5.06
2026-07-23,,4.56,5.06
2026-07-24,4.21,4.58,.
"""


# ── Teil 1: Parsing ──────────────────────────────────────────────────────────
def test_parse_skips_dot_and_missing_values():
    series = tr.parse_fred_csv(SAMPLE_CSV)
    # DGS10: 4 Werte (21.07. '.' uebersprungen); DGS2: 4 (23.07. leer); DGS30: 3
    assert [v for _, v in series["DGS10"]] == [4.49, 4.55, 4.56, 4.58]
    assert [v for _, v in series["DGS2"]] == [4.14, 4.15, 4.18, 4.21]
    assert [v for _, v in series["DGS30"]] == [4.99, 5.01, 5.06, 5.06]
    # aufsteigend sortiert, ISO-Daten
    assert [d for d, _ in series["DGS10"]] == sorted(d for d, _ in series["DGS10"])


def test_parse_empty_and_garbage_is_honest():
    for text in ("", "   ", "kein csv\n1,2,3"):
        series = tr.parse_fred_csv(text)
        assert series["DGS10"] == [] and series["DGS2"] == [] and series["DGS30"] == []


def test_parse_trims_to_max_cache_obs():
    values = [4.0 + i * 0.001 for i in range(140)]
    series = tr.parse_fred_csv(
        "DATE,DGS2,DGS10,DGS30\n"
        + "\n".join(f"{d},{v},," for d, v in _series(values))
    )
    assert len(series["DGS2"]) == tr.MAX_CACHE_OBS
    # juengster Wert bleibt erhalten
    assert series["DGS2"][-1][1] == pytest.approx(values[-1])


# ── Teil 2: bp-Aenderungen, Regime-Grenzen, Kurve ────────────────────────────
def test_change_bp_math():
    values = [4.0] * 25 + [4.05, 4.10, 4.15, 4.20, 4.25]
    block = tr.build_rates_block(_full_series(values))
    assert block["status"] == "ok"
    assert block["change_5d_bp"] == pytest.approx(25.0)
    assert block["change_20d_bp"] == pytest.approx(25.0)
    assert block["dgs30_change_20d_bp"] == pytest.approx(0.0)
    assert block["regime"] == "rising_fast"


@pytest.mark.parametrize(
    "delta_bp,expected",
    [
        (30.0, "rising_fast"),
        (25.0, "rising_fast"),   # >= 25 Grenze inklusiv
        (24.9, "rising"),
        (10.0, "rising"),        # >= 10 Grenze inklusiv
        (9.9, "stable"),
        (0.0, "stable"),
        (-9.9, "stable"),
        (-10.0, "falling"),
        (-24.9, "falling"),
        (-25.0, "falling_fast"),
        (-30.0, "falling_fast"),
    ],
)
def test_regime_boundaries(delta_bp, expected):
    values = [4.0] * 21 + [4.0 + delta_bp / 100.0]
    block = tr.build_rates_block(_full_series(values))
    assert block["regime"] == expected
    assert block["change_20d_bp"] == pytest.approx(delta_bp, abs=0.05)


def test_regime_none_when_too_few_observations():
    block = tr.build_rates_block(_full_series([4.0] * 10))
    assert block["status"] == "ok"              # Level verwertbar
    assert block["change_20d_bp"] is None       # aber keine 20d-Aenderung
    assert block["regime"] is None
    assert "21 Beobachtungen" in block["regime_basis"]


def test_curve_spreads_and_levels():
    block = tr.build_rates_block(_full_series([4.56] * 25, dgs2=4.21, dgs30=5.16))
    assert block["dgs10"] == pytest.approx(4.56)
    assert block["curve_10s2s_bp"] == pytest.approx(35.0)
    assert block["curve_30s10s_bp"] == pytest.approx(60.0)


def test_missing_blocks_are_honest():
    block = tr.build_rates_block(None, fetch_error="HTTP 503")
    assert block["status"] == "missing"
    assert "HTTP 503" in block["reason"]
    assert block["regime"] is None
    block2 = tr.build_rates_block({"DGS10": [], "DGS2": [], "DGS30": []})
    assert block2["status"] == "missing"
    assert block2["regime"] is None


def test_stale_flag_respects_injected_today():
    series = _full_series([4.5] * 25)
    fresh = tr.build_rates_block(series, today="2026-06-27")   # as_of = 2026-06-25
    assert fresh["as_of"] == "2026-06-25"
    assert fresh["stale"] is False
    stale = tr.build_rates_block(series, today="2026-07-10")
    assert stale["stale"] is True
    assert stale["stale_days"] == 15


# ── Teil 3: Market-Context — Annotation ja, Scoring-Aenderung nein ──────────
def test_market_context_passes_rates_through_without_scoring_change():
    block = tr.build_rates_block(_full_series([4.0] * 25 + [4.05, 4.10, 4.15, 4.20, 4.25]))
    assert block["regime"] == "rising_fast"
    ctx_without = mc.build_market_context()
    ctx_with = mc.build_market_context(rates_data=block)
    # Mess-First-Invariante: Zinsen duerfen Scoring/Regime/Warnungen NICHT aendern
    assert ctx_with["overall_risk_score"] == ctx_without["overall_risk_score"]
    assert ctx_with["regime"] == ctx_without["regime"]
    assert ctx_with["trade_mode"] == ctx_without["trade_mode"]
    assert ctx_with["warnings"] == ctx_without["warnings"]
    # Annotation liegt im Block
    assert ctx_with["rates"]["status"] == "ok"
    assert ctx_with["rates"]["regime"] == "rising_fast"
    assert ctx_without["rates"]["status"] == "missing"


def test_market_context_missing_rates_reason_passthrough():
    ctx = mc.build_market_context(rates_data={"status": "missing", "reason": "FRED down", "regime": None})
    assert ctx["rates"]["status"] == "missing"
    assert ctx["rates"]["reason"] == "FRED down"


# ── Teil 4: Tracker — rates_json-Spalte ──────────────────────────────────────
def _base_row(**overrides):
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


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_tracker_test.sqlite")
    monkeypatch.setenv("SIGNAL_TRACKER_DB_PATH", db_path)
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", db_path)
    return st


def test_compact_rates_json_contract():
    block = tr.build_rates_block(_full_series([4.0] * 25 + [4.3] * 5))
    payload = json.loads(st._compact_rates_json(block))
    assert payload["regime"] == "rising_fast"
    assert payload["as_of"]
    assert payload["dgs10"] == pytest.approx(4.3)
    # kompakt: keine Schwellen-/Basis-Texte in der DB
    assert "thresholds" not in payload and "regime_basis" not in payload
    assert st._compact_rates_json(None) is None
    assert st._compact_rates_json({"status": "missing", "reason": "x"}) is None
    assert st._compact_rates_json({"status": "ok"}) is None  # ohne as_of ehrlich leer


def test_tracker_stores_rates_annotation(tracker):
    block = tr.build_rates_block(_full_series([4.0] * 25 + [4.3] * 5))
    inserted = tracker.record_alert_signals("breakout", [_base_row()], rates_context=block)
    assert inserted == 1
    sig = _signal("AAPL")
    payload = json.loads(sig["rates_json"])
    assert payload["regime"] == "rising_fast"
    assert payload["change_20d_bp"] == pytest.approx(30.0)


def test_tracker_without_rates_keeps_null_and_records(tracker):
    inserted = tracker.record_alert_signals("breakout", [_base_row(Ticker="MSFT")])
    assert inserted == 1
    assert _signal("MSFT")["rates_json"] is None
    # Missing-Block ebenfalls: kein erfundener Kontext
    inserted2 = tracker.record_alert_signals(
        "breakout",
        [_base_row(Ticker="NVDA")],
        rates_context={"status": "missing", "reason": "FRED down", "regime": None},
    )
    assert inserted2 == 1
    assert _signal("NVDA")["rates_json"] is None
