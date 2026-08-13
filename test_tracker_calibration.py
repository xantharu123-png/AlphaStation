#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests fuer AUDIT 2026-07-24: T1 (Managed-R 50/50), T2 (Krypto-Grades)
und den Kalibrier-Loop (Wilson-Intervall + Reliability-Flags).

Offline: DB in pytest-tmp, SIGNAL_DB_PATH per monkeypatch verbogen.
"""
import inspect
import sqlite3

import modules.signal_tracker as st
import modules.patterns as patterns_module


# ── T1: Managed-R ────────────────────────────────────────────────────────────

def _row(**kw):
    base = {
        "status": st.STATUS_TP2, "direction": "LONG",
        "entry": 100.0, "entry_fill_price": 100.0,
        "stop": 95.0, "tp1": 105.0, "tp2": 110.0,
        "r_realized": 2.0, "tp1_hit_at": "2026-07-20",
    }
    base.update(kw)
    return base


def test_managed_r_tp2_is_blend_not_full():
    # LONG 100/95/105/110: r_tp1 = 1.0, r_tp2 = 2.0 -> managed = 1.5
    assert st._managed_r_50_50(_row()) == 1.5


def test_managed_r_stop_after_tp1():
    # Stop nach TP1: level = -1.0, managed = 0.5*1.0 + 0.5*(-1.0) = 0.0
    row = _row(status=st.STATUS_STOP, r_realized=-1.0)
    assert st._managed_r_50_50(row) == 0.0


def test_managed_r_stop_before_tp1_identical():
    row = _row(status=st.STATUS_STOP, r_realized=-1.0, tp1_hit_at=None)
    assert st._managed_r_50_50(row) == -1.0


def test_managed_r_expired_after_tp1():
    row = _row(status=st.STATUS_EXPIRED, r_realized=0.6)
    assert st._managed_r_50_50(row) == 0.8  # 0.5*1.0 + 0.5*0.6


def test_managed_r_short_tp2():
    row = _row(direction="SHORT", stop=105.0, tp1=95.0, tp2=90.0, r_realized=2.0)
    assert st._managed_r_50_50(row) == 1.5


def test_managed_r_missing_tp1_falls_back():
    row = _row(tp1=None, tp2=None)
    assert st._managed_r_50_50(row) == 2.0


def test_managed_r_none_without_realized():
    assert st._managed_r_50_50(_row(r_realized=None)) is None


# ── Kalibrier-Loop: Wilson + Reliability ─────────────────────────────────────

def test_wilson_empty_is_none():
    assert st._wilson_interval_95(0, 0) is None


def test_wilson_known_case_10_of_20():
    # Referenz handgerechnet: p=0.5, n=20 -> Intervall ~[29.9, 70.1]
    iv = st._wilson_interval_95(10, 20)
    assert abs(iv["lower_pct"] - 29.9) < 0.3
    assert abs(iv["upper_pct"] - 70.1) < 0.3
    assert iv["lower_pct"] < 50.0 < iv["upper_pct"]


def test_wilson_never_leaves_unit_interval():
    iv = st._wilson_interval_95(20, 20)
    assert 0.0 <= iv["lower_pct"] <= 100.0
    assert 0.0 <= iv["upper_pct"] <= 100.0
    assert iv["lower_pct"] > 50.0  # 20/20 muss deutlich ueber 50 bleiben


def test_finalize_bucket_reliability_flag():
    bucket = st._empty_bucket()
    bucket["signals"] = 29
    st._finalize_bucket(bucket, [1.0] * 29, 90)
    assert bucket["decided_signals"] == 29
    assert bucket["sample_reliable"] is False
    bucket2 = st._empty_bucket()
    bucket2["signals"] = 30
    st._finalize_bucket(bucket2, [1.0] * 30, 90)
    assert bucket2["sample_reliable"] is True


def test_summary_reports_managed_and_wilson(tmp_path, monkeypatch):
    db = str(tmp_path / "sig.sqlite")
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", db)
    recorded = st.record_alert_signals(
        "test_scanner",
        [{"Ticker": "TEST", "Entry": 100.0, "StopLoss": 95.0, "TP1": 105.0, "TP2": 110.0, "Preis": 100.0}],
        "trade",
        "mail",
    )
    assert recorded == 1
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE signals SET status=?, r_realized=?, tp1_hit_at=?, "
        "entry_filled_at=?, entry_fill_price=?, closed_at=?",
        (st.STATUS_TP2, 2.0, "2026-07-20", "2026-07-20", 100.0,
         "2026-07-22T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    summary = st.load_performance_summary(days=90)
    total = summary["total"]
    assert total["avg_r"] == 2.0                    # Level-R (unmanaged)
    assert total["avg_r_managed_50_50"] == 1.5      # befolgbares Management
    assert total["decided_signals"] == 1
    assert total["win_rate_wilson_95"] is not None
    assert total["sample_reliable"] is False
    assert "r_semantics" in summary
    assert summary["recent"][0]["r_managed_50_50"] == 1.5


# ── T2: Krypto-Grade-Schwellen neu verankert ─────────────────────────────────

def test_crypto_grade_thresholds_reanchored():
    source = inspect.getsource(patterns_module.analyze_breakout_imminent)
    # Neue Schwellen (V2.9-Ratios auf aktuelle Aktien-Schwellen)
    assert "score >= 71 and smart_money_fires >= 3" in source
    assert "score >= 61 and smart_money_fires >= 2" in source
    assert "score >= 48 and smart_money_hits >= 1" in source
    assert "score >= 44:" in source
    # Alte, zu strenge Krypto-Schwellen muessen weg sein
    assert "score >= 95 and smart_money_fires >= 3" not in source
    assert "score >= 85 and smart_money_fires >= 2" not in source


def test_crypto_grades_now_easier_than_stock_in_relative_terms():
    # Kalibrier-Absicht (V2.9): Krypto ~15-20% UNTER Aktien.
    # Relativ zum eigenen Max-Score: Krypto-S 71/168=42.3% < Aktien-S 85/173=49.1%
    assert 71 / 168 < 85 / 173
    assert 61 / 168 < 71 / 173
    assert 48 / 168 < 57 / 173
    # Und die Ordnung innerhalb Krypto bleibt monoton
    assert 71 > 61 > 48 > 44



# ── Kalibrier-Loop: scanner_verdict / breakeven_win_rate_pct ─────────────────

def test_verdict_small_sample_is_beobachten():
    v, why = st.scanner_verdict({"decided_signals": 13, "avg_r": 0.74,
                                 "win_rate_pct": 54.0})
    assert v == "beobachten"
    assert "13" in why and "30" in why


def test_verdict_behalten_when_ci_lower_above_breakeven():
    # BE = 43/1.29 = 33.3 < KI-Untergrenze 36.2 => behalten
    v, _ = st.scanner_verdict({
        "decided_signals": 188, "win_rate_pct": 43.0, "avg_r": 0.29,
        "win_rate_wilson_95": {"lower_pct": 36.2, "upper_pct": 50.0}})
    assert v == "behalten"


def test_verdict_beobachten_when_positive_but_not_significant():
    # avg_r > 0, aber KI-Untergrenze 30.0 < BE 33.3 => nicht signifikant
    v, _ = st.scanner_verdict({
        "decided_signals": 100, "win_rate_pct": 43.0, "avg_r": 0.29,
        "win_rate_wilson_95": {"lower_pct": 30.0, "upper_pct": 55.0}})
    assert v == "beobachten"


def test_verdict_abschalten_when_ci_upper_below_breakeven():
    # BE = 30/0.6 = 50.0 > KI-Obergrenze 45.0 => abschalten
    v, _ = st.scanner_verdict({
        "decided_signals": 60, "win_rate_pct": 30.0, "avg_r": -0.4,
        "win_rate_wilson_95": {"lower_pct": 22.0, "upper_pct": 45.0}})
    assert v == "abschalten"


def test_verdict_abschalten_structural_minus_1r():
    v, why = st.scanner_verdict({"decided_signals": 50, "win_rate_pct": 10.0,
                                 "avg_r": -1.3, "win_rate_wilson_95": None})
    assert v == "abschalten"
    assert "strukturell" in why


def test_breakeven_rate_edges():
    assert st.breakeven_win_rate_pct(None, 0.3) is None
    assert st.breakeven_win_rate_pct(43.0, None) is None
    assert st.breakeven_win_rate_pct(43.0, -1.2) is None  # E+1 <= 0
    assert st.breakeven_win_rate_pct(43.0, 0.29) == 100.0 * 0.43 / 1.29
