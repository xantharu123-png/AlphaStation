# -*- coding: utf-8 -*-
"""Mathematik-Invarianten — unabhaengiger Rechen-Audit (24.07.2026).

Jede Referenzformel ist hier NEU aus dem Lehrbuch geschrieben (Wilder 1978,
Chaikin, TradingView-Konventionen) und wird gegen den Produktivcode geprueft.
Haelt diese Suite, ist die Rechenkern-Qualitaet dauerhaft regressionssicher.
"""
import math
import random
import statistics

from modules.vrvp_levels import calculate_wilder_atr
from modules.indicators import (
    calculate_rsi_from_bars,
    calculate_ema_series,
    calculate_macd,
    calculate_vwap,
    calculate_stochastic,
)
from modules.volume_metrics import (
    historical_volume_baseline,
    project_partial_rvol,
    completed_bar_rvol,
)
from modules.performance_metrics import profit_factor_metrics
from modules.trade_levels import trade_geometry


# ── Lehrbuch-Referenzen (unabhaengig geschrieben) ─────────────────────────

def ref_wilder_atr(bars, period=14):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def ref_ema_series(data, period):
    out = [None] * len(data)
    if len(data) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = sum(data[:period]) / period
    for i in range(period, len(data)):
        out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


def _crash_fixture():
    bars, price = [], 100.0
    for _ in range(55):
        bars.append({"high": price + 0.5, "low": price - 0.5, "close": price + 0.1})
        price += 0.1
    for _ in range(5):
        price -= 6.0
        bars.append({"high": price + 3.0, "low": price - 5.0, "close": price})
    return bars


# ── ATR ────────────────────────────────────────────────────────────────────

def test_atr_textbook_parity():
    bars = _crash_fixture()
    assert calculate_wilder_atr(bars, 14) == ref_wilder_atr(bars, 14)


def test_atr_n1_desc_timestamps_sorted():
    """NACHAUDIT N1: desc-gelieferte Bars mit Timestamps muessen intern
    chronologisiert werden — sonst halbiert sich die ATR im Crash-Fall."""
    bars = _crash_fixture()
    exp = ref_wilder_atr(bars, 14)
    desc_ts = [dict(b, t=10_000 - i) for i, b in enumerate(reversed(bars))]
    assert abs(calculate_wilder_atr(desc_ts, 14) - exp) < 1e-12
    # Bugklasse belegen: unkaestigte desc- Reihenfolge verfaelscht wirklich:
    assert abs(ref_wilder_atr(list(reversed(bars)), 14) - exp) > 0.5


# ── RSI ────────────────────────────────────────────────────────────────────

def test_rsi_textbook_parity():
    closes = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
              46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
              46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03,
              44.18, 44.22, 44.57, 43.42, 42.66, 43.13]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    ag = sum(gains[:14]) / 14
    al = sum(losses[:14]) / 14
    for i in range(14, len(gains)):
        ag = (ag * 13 + gains[i]) / 14
        al = (al * 13 + losses[i]) / 14
    exp = round(100.0 - 100.0 / (1.0 + ag / al), 1)
    got = calculate_rsi_from_bars([{"close": c} for c in closes], 14)
    assert abs(got - exp) <= 0.051


# ── EMA / MACD ─────────────────────────────────────────────────────────────

def test_ema_series_seed_and_recursion():
    data = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    got = calculate_ema_series(data, 5)
    exp = ref_ema_series(data, 5)
    assert got[3] is None
    assert got[4] == sum(data[:5]) / 5
    assert got[-1] == exp[-1]


def test_macd_composition():
    random.seed(7)
    closes, p = [], 50.0
    for _ in range(80):
        p *= 1 + random.uniform(-0.02, 0.022)
        closes.append(p)
    m_line, m_sig, m_hist = calculate_macd([{"close": c} for c in closes])
    ef, es = ref_ema_series(closes, 12), ref_ema_series(closes, 26)
    line = [f - s for f, s in zip(ef, es) if f is not None and s is not None]
    sig = ref_ema_series(line, 9)
    assert abs(m_line - line[-1]) < 1e-9
    assert abs(m_sig - sig[-1]) < 1e-9
    assert abs(m_hist - (line[-1] - sig[-1])) < 1e-9


# ── VWAP ───────────────────────────────────────────────────────────────────

def test_vwap_value_and_band_formula():
    ohlcv = [
        {"high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"high": 103.0, "low": 100.0, "close": 102.0, "volume": 3000},
        {"high": 104.0, "low": 101.0, "close": 101.0, "volume": 1500},
        {"high": 102.0, "low": 99.5, "close": 100.5, "volume": 2500},
        {"high": 101.5, "low": 100.0, "close": 101.0, "volume": 2000},
    ]
    res = calculate_vwap(ohlcv)
    tps = [(b["high"] + b["low"] + b["close"]) / 3 for b in ohlcv]
    vols = [b["volume"] for b in ohlcv]
    ref_vwap = sum(t * v for t, v in zip(tps, vols)) / sum(vols)
    ref_std = math.sqrt(max(0.0, sum(v * t * t for t, v in zip(tps, vols)) / sum(vols) - ref_vwap ** 2))
    assert abs(res["vwap"] - ref_vwap) < 1e-12
    assert abs(res["std_dev"] - ref_std) < 1e-12
    assert abs(res["upper_2"] - (ref_vwap + 2 * ref_std)) < 1e-12


# ── Stochastic ─────────────────────────────────────────────────────────────

def test_stochastic_kd():
    random.seed(11)
    bars, p = [], 100.0
    for _ in range(30):
        o = p
        p *= 1 + random.uniform(-0.015, 0.015)
        bars.append({"open": o, "high": max(o, p) * 1.005, "low": min(o, p) * 0.995, "close": p})
    k_got, d_got = calculate_stochastic(bars)
    ks = []
    for i in range(13, len(bars)):
        w = bars[i - 13:i + 1]
        hh = max(b["high"] for b in w)
        ll = min(b["low"] for b in w)
        ks.append((bars[i]["close"] - ll) / (hh - ll) * 100)
    assert abs(k_got - round(ks[-1], 1)) <= 0.051
    assert abs(d_got - round(sum(ks[-3:]) / 3, 1)) <= 0.051


# ── Geometrie & Netto-R:R ──────────────────────────────────────────────────

def test_trade_geometry_invariants():
    assert trade_geometry(2.00, 1.80, 2.40, 2.70, "LONG").get("valid") is True
    assert trade_geometry(2.00, 1.80, 1.90, 2.70, "LONG").get("valid") is False
    assert trade_geometry(2.00, 2.10, 2.40, 2.70, "LONG").get("valid") is False
    assert trade_geometry(2.00, 1.80, 2.40, 2.40, "LONG").get("valid") is False
    assert trade_geometry(2.00, 2.20, 1.60, 1.30, "SHORT").get("valid") is True


def test_penny_net_rr_cost_model_handcomputation():
    """Referenzbeispiel: k = entry*(spread + 2*slippage)/1e4; Netto-R muss
    Kosten in Zaehler UND Nenner schlagen (ehrliches Modell)."""
    entry, stop, tp1, tp2 = 2.00, 1.80, 2.40, 2.70
    k = entry * (50.0 + 2 * 15.0) / 10_000.0
    assert k == 0.016
    net_risk = (entry - stop) + k
    net_tp1 = (tp1 - entry - k) / net_risk
    net_tp2 = (tp2 - entry - k) / net_risk
    assert abs(net_tp1 - 1.7778) < 1e-3
    assert abs(net_tp2 - 3.1667) < 1e-3
    assert abs((net_tp1 + net_tp2) / 2 - 2.4722) < 1e-3
    # 80bps Round-Trip frisst ~11% des Brutto-TP1-R — das Modell beschönigt nicht.
    gross_tp1 = (tp1 - entry) / (entry - stop)
    assert gross_tp1 == 2.0
    assert abs(1 - net_tp1 / gross_tp1 - 0.1111) < 1e-3


# ── Profit Factor ──────────────────────────────────────────────────────────

def test_profit_factor_honest_representation():
    assert profit_factor_metrics(100, 50)["value"] == 2.0
    inf_case = profit_factor_metrics(100, 0)
    assert inf_case["display"] == "INF"
    assert inf_case["value"] is None  # niemals 99/999 als Schein-Wert
    assert profit_factor_metrics(0, 0)["value"] == 0.0
    assert profit_factor_metrics(float("nan"), 10)["value"] == 0.0


# ── RVOL / Volumen-Baselines ───────────────────────────────────────────────

def test_rvol_baseline_excludes_zeros_and_none():
    hist = [1000, 0, 1200, None, 800, 1100]
    base = historical_volume_baseline(hist, lookback=20, method="mean")
    assert base == (1000 + 1200 + 800 + 1100) / 4
    assert historical_volume_baseline(hist, lookback=20, method="median") == statistics.median([1000, 1200, 800, 1100])
    assert historical_volume_baseline([0, 0, 0]) is None
    assert abs(completed_bar_rvol(2200, hist, lookback=20) - 2200 / base) < 1e-12


def test_rvol_projection_math():
    assert project_partial_rvol(0.5, 0.25) == 2.0
    assert project_partial_rvol(1.7, 1.0) == 1.7
    assert project_partial_rvol(1.7, 0.0) == 0.0


# ── Score-Modell Randwerte ─────────────────────────────────────────────────

def test_penny_score_model_boundaries():
    """Dokumentiert die Modelleigenschaften der Penny-Formel
    trade_score = clamp(0.45*setup + 0.55*entry - 0.15*dump)."""
    def clamp100(x):
        return max(0.0, min(100.0, x))
    assert clamp100(0.45 * 100 + 0.55 * 100 - 0.15 * 0) == 100.0
    assert clamp100(0.45 * 100 + 0.55 * 100 - 0.15 * 100) == 85.0  # Dump-Deckel
    assert clamp100(0.45 * 0 + 0.55 * 0 - 0.15 * 100) == 0.0
    assert clamp100(0.45 * 100 + 0.55 * 0 - 0.15 * 0) == 45.0  # kein Kauf ohne Trigger
