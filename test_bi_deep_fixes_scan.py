#!/usr/bin/env python3
"""
BI-Scanner Deep-Fixes (BI-Audit 10.06.2026) — Regression-Tests durch den
ECHTEN Scan-Pfad modules.scanners._bi_background_scan (nur I/O gefaked).

Abgedeckt:
- H-1: Grade-Durchreichung aus patterns (Alt-Leiter 113/99 entfernt),
       RVOL-Guard bleibt, ShortBonus hebt das Grade nicht (short_bonus-Feld)
- H-2: Long-Extension-Gate, Short-Extension-Gate (vorher toter Code),
       adaptives Range-Fenster (Fenster-Kohaerenz), TP1/TP2-Formel-Absicherung,
       Geometrie-Mini-Fuzz (500 je Richtung), kumulativer 2-Tages-Pump-Filter
- M-1: Partial-Bar raus aus der Analyse (Session-Logik, Score-Neutralitaet)

Session-unabhaengig: Pfade via __file__, deterministische Seeds, keine echten
HTTP-Calls, Uhrzeit via FakeDatetime kontrolliert.
"""
import os
import random
import sys
from datetime import datetime as _real_datetime, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import modules.scanners as scanners
from modules.trade_levels import trade_geometry as real_trade_geometry


# ────────────────────────── Helpers / Fixtures ──────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, raw_bars):
        self._raw = raw_bars

    def json(self):
        return {"results": self._raw}


def _to_polygon(bars):
    return [{"t": b["t"], "o": b["open"], "h": b["high"], "l": b["low"],
             "c": b["close"], "v": b["volume"]} for b in bars]


def _attach_ts(bars, end_day=None):
    """Timestamps (12:00 lokal) — letzter Bar endet bei end_day (date)."""
    if end_day is None:
        end_day = (_real_datetime.now() - timedelta(days=3)).date()
    n = len(bars)
    for i, b in enumerate(bars):
        d = end_day - timedelta(days=(n - 1 - i))
        b["t"] = int(_real_datetime(d.year, d.month, d.day, 12, 0).timestamp() * 1000)
    return bars


def _bar(o, h, l, c, v):
    return {"open": o, "high": max(h, o, c), "low": min(l, o, c), "close": c, "volume": int(v)}


def _flat_bars(n=30, price=100.0, half_range=0.6, vol=250_000, seed=7):
    rng = random.Random(seed)
    bars = []
    for _ in range(n):
        c = price + rng.uniform(-0.05, 0.05)
        bars.append(_bar(c, c + half_range, c - half_range, c, vol))
    return _attach_ts(bars)


def _spike_then_flat(n_spike=5, n_flat=10, spike_high=107.0, price=100.0, vol=250_000):
    """Erst Spike-Region bis 107, dann enge Konsolidierung um 100. Der Spike
    liegt im 15-Bar-Fallback-Fenster, aber NICHT im 10-Tage-Adaptivfenster."""
    bars = []
    for _ in range(15):
        bars.append(_bar(price, price + 0.6, price - 0.6, price, vol))
    for _ in range(n_spike):
        bars.append(_bar(price, spike_high, price - 0.5, price + 0.5, vol))
    for _ in range(n_flat):
        bars.append(_bar(price, price + 0.6, price - 0.6, price, vol))
    return _attach_ts(bars)


def _patch_scan_io(monkeypatch, bars_by_ticker, analyze_result=None, analyze_log=None):
    """Faked HTTP/Cache/Progress; optional analyze_breakout_imminent-Stub."""
    saved = {}

    def fake_get(url, params=None, timeout=15):
        import re as _re
        t = _re.search(r"/ticker/([^/]+)/range", url).group(1)
        return _Resp(_to_polygon(bars_by_ticker[t]))

    monkeypatch.setattr(scanners, "rate_limited_get", fake_get)
    monkeypatch.setattr(scanners, "_bi_cache_save",
                        lambda results, direction="long", **kw: saved.update(
                            {"results": results, "direction": direction, "meta": kw}))
    monkeypatch.setattr(scanners, "_bi_progress_write", lambda *a, **k: None)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda d: False)
    monkeypatch.setattr(scanners, "_bi_clear_stop", lambda d: None)
    monkeypatch.setattr(scanners, "_detect_chart_patterns", lambda *a, **k: [])
    monkeypatch.setattr(scanners, "calculate_short_bonus_signals",
                        lambda *a, **k: {"bonus_score": 0, "details": []})
    # VRVP fuer deterministische Level-Tests neutralisieren
    monkeypatch.setattr(scanners, "build_vrvp_structure", lambda *a, **k: None)
    monkeypatch.setattr(scanners, "apply_vrvp_to_trade_setup", lambda setup, vrvp, **k: setup)

    if analyze_result is not None:
        def fake_analyze(bars, direction="long"):
            if analyze_log is not None:
                analyze_log.append({"bars": list(bars), "direction": direction})
            return analyze_result

        monkeypatch.setattr(scanners, "analyze_breakout_imminent", fake_analyze)
    return saved


# ────────────────────────── H-1: Grade-Durchreichung ──────────────────────────

def test_h1_grade_passthrough_no_alt_ladder(monkeypatch):
    """Score 90 + Grade A aus patterns: Alt-Leiter haette auf C regraded (90<99).
    Jetzt wird das patterns-Grade durchgereicht."""
    saved = _patch_scan_io(
        monkeypatch, {"TEST": _flat_bars()},
        analyze_result=(True, 90, 188, ["ok"], "high", "A", 3, 3),
    )
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    assert saved["results"], "Kandidat haette gespeichert werden muessen"
    row = saved["results"][0]
    assert row["BI_Grade"] == "A", f"Grade muss aus patterns durchgereicht werden, ist {row['BI_Grade']}"
    assert row["BI_Score"] == 90


def test_h1_rvol_guard_still_downgrades(monkeypatch):
    """RVOL-Guard bleibt: S/A mit RVOL < 0.7 wird auf B gestuft."""
    bars = _flat_bars()
    bars[-1]["volume"] = 80_000  # letzter kompletter Tag: RVOL ~0.33
    saved = _patch_scan_io(
        monkeypatch, {"TEST": bars},
        analyze_result=(True, 95, 188, ["ok"], "high", "A", 4, 4),
    )
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    row = saved["results"][0]
    assert row["RVOL"] < 0.7
    assert row["BI_Grade"] == "B"
    assert "RVOL" in row["BI_GradeLabel"]


def test_h1_short_bonus_in_score_not_in_grade(monkeypatch):
    """ShortBonus fliesst in den SCORE und wird separat ausgewiesen (short_bonus),
    hebt aber das Grade nicht mehr ueber die Leiter."""
    saved = _patch_scan_io(
        monkeypatch, {"TEST": _flat_bars()},
        analyze_result=(True, 60, 188, ["ok"], "high", "B", 2, 2),
    )
    monkeypatch.setattr(scanners, "calculate_short_bonus_signals",
                        lambda *a, **k: {"bonus_score": 40, "details": ["Earnings-Risk"]})
    scanners._bi_background_scan("k", direction="short", candidates=["TEST"])
    row = saved["results"][0]
    assert row["short_bonus"] == 40
    assert row["ShortBonusScore"] == 40
    assert row["BI_Score"] == 100  # 60 + 40 im Score
    assert row["BI_Grade"] == "B"  # Grade bleibt patterns-Grade (Alt-Leiter haette 100>=99+ Bonus-Logik vermischt)


# ────────────────────────── H-2: Extension-Gates + Fenster ──────────────────────────

def test_h2_long_extension_reject(monkeypatch, capsys):
    """Spike-High im 15-Bar-Fallback-Fenster: Entry ~7% ueber Kurs → Reject."""
    saved = _patch_scan_io(
        monkeypatch, {"TEST": _spike_then_flat()},
        analyze_result=(True, 70, 188, ["ok"], "high", "B", 2, 2),  # kein range_days → Fallback 15
    )
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    assert saved["results"] == [], "Entry 7% ueber Kurs muss verworfen werden"
    assert "entry_too_extended" in capsys.readouterr().out


def test_h2_adaptive_window_prevents_spike_reference(monkeypatch):
    """Gleiche Bars, aber patterns meldet 10-Tage-Konsolidierung: adaptives
    Fenster nutzt die enge Range statt des Spikes → Kandidat bleibt, Entry nah."""
    saved = _patch_scan_io(
        monkeypatch, {"TEST": _spike_then_flat()},
        analyze_result=(True, 70, 188, [" Solide Konsolidierung: 10 Tage"], "high", "B", 2, 2),
    )
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    assert saved["results"], "Mit adaptivem Fenster muss der Kandidat durchgehen"
    row = saved["results"][0]
    live = 100.0
    assert (row["Entry"] - live) / live < 0.03, f"Entry {row['Entry']} muss nahe Kurs sein"
    assert row["RangeHigh"] < 102, "RangeHigh muss aus der Konsolidierung kommen, nicht vom Spike"


def test_h2_short_extension_reject_and_control(monkeypatch, capsys):
    """Short-Extension-Gate (vorher toter Code): LIVE-Kurs deutlich unter dem
    Range-Low des Konsolidierungsfensters = Breakdown verpasst → Reject.
    Greift auch, wenn der letzte KOMPLETTE Close noch in der Range liegt
    (Intraday-Crash darf nicht in den Pullback-Zweig durchrutschen).
    Kontrolle: Kurs knapp unter Range-Low → bleibt."""
    # Fenster: 10 komplette Tage, enge Bars (ATR ~2 → Limit max(2xATR%, 3%) ~4.4%),
    # Range 100-106 via zwei Touch-Bars. Crash-Bar -10.7% (unter dem -15%-
    # Already-Crashed-Tagesfilter, aber klar unter dem Extension-Limit).
    def mk(crash_close):
        bars = []
        for _ in range(12):
            bars.append(_bar(103, 104, 102, 103, 250_000))
        bars.append(_bar(103, 106, 102, 103, 250_000))   # Range-High-Touch
        bars.append(_bar(103, 104, 100, 103, 250_000))   # Range-Low-Touch
        for _ in range(6):
            bars.append(_bar(103, 104, 102, 103, 250_000))
        bars.append(_bar(100, 100.5, crash_close - 0.5, crash_close, 900_000))
        return _attach_ts(bars)

    # Partial-Bar deterministisch abschneiden (Session-Erkennung separat getestet)
    monkeypatch.setattr(scanners, "_bi_strip_partial_bar", lambda b: b[:-1])

    saved = _patch_scan_io(
        monkeypatch, {"TEST": mk(92.0)},
        analyze_result=(True, 70, 188, [" Solide Konsolidierung: 10 Tage"], "high", "B", 2, 2),
    )
    scanners._bi_background_scan("k", direction="short", candidates=["TEST"])
    assert saved["results"] == [], "Kurs 8% unter Range-Low muss verworfen werden"
    assert "entry_too_extended" in capsys.readouterr().out

    saved2 = _patch_scan_io(
        monkeypatch, {"TEST": mk(99.0)},
        analyze_result=(True, 70, 188, [" Solide Konsolidierung: 10 Tage"], "high", "B", 2, 2),
    )
    scanners._bi_background_scan("k", direction="short", candidates=["TEST"])
    assert saved2["results"], "Kurs knapp unter Range-Low (1%) muss durchgehen"


def test_h2_short_tp_formula_guarantees_geometry(monkeypatch):
    """TP1 = min(rl - 0.272*size, entry - 0.5*risk), TP2 = min(rl - 0.618*size,
    tp1 - 0.25*risk): Entry nahe Range-Low + weiter Stop → der 0.5R-Term
    bindet, die Geometrie Stop > Entry > TP1 > TP2 bleibt strukturell
    garantiert — und das R:R-Gate verwirft den 0.5R-Fall ehrlich."""
    bars = []
    for _ in range(20):
        bars.append(_bar(103, 106, 100, 103, 250_000))
    # Letzter kompletter Tag schliesst nahe Range-Low mit grosser Spanne (hoher ATR)
    bars.append(_bar(103, 104, 100, 100.2, 400_000))
    _attach_ts(bars)

    geo_calls = []

    def spy_geometry(entry, stop, tp1, tp2, direction_up=None):
        res = real_trade_geometry(entry, stop, tp1, tp2, direction_up)
        geo_calls.append({"entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "res": res})
        return res

    saved = _patch_scan_io(
        monkeypatch, {"TEST": bars},
        analyze_result=(True, 70, 188, [" Solide Konsolidierung: 15 Tage"], "high", "B", 2, 2),
    )
    monkeypatch.setattr(scanners, "trade_geometry", spy_geometry)
    scanners._bi_background_scan("k", direction="short", candidates=["TEST"])

    assert geo_calls, "Level muessen bewertet worden sein"
    g = geo_calls[0]
    entry, stop, tp1, tp2 = g["entry"], g["stop"], g["tp1"], g["tp2"]
    assert stop > entry > tp1 > tp2, f"Geometrie verletzt: {stop}/{entry}/{tp1}/{tp2}"
    risk = stop - entry
    rl, size = 100.0, 6.0  # Fenster (15 komplette Tage): Range 100-106
    assert tp1 == round(max(0.01, min(rl - 0.272 * size, entry - 0.5 * risk)), 2)
    assert tp2 == round(max(0.01, min(rl - 0.618 * size, tp1 - 0.25 * risk)), 2)
    # Der 0.5R-Term bindet (97.3 statt 98.37 der reinen Range-Formel)
    assert tp1 <= round(entry - 0.5 * risk, 2) + 0.011
    assert g["res"]["valid"], "Levels muessen geometrisch gueltig sein"
    # R:R 0.5 < 1.2 → Scanner verwirft den Kandidaten (kein Fantasie-Ziel im Cache)
    assert saved["results"] == []


def test_h2_geometry_mini_fuzz_500_both_directions(monkeypatch):
    """500 zufaellige Serien je Richtung durch den echten Scan-Pfad:
    JEDE bewertete Level-Geometrie (auch verworfene) ist konsistent geloggt,
    JEDE gespeicherte Row besteht trade_geometry + R:R >= 1.2. 0 Crashes."""
    for direction in ("long", "short"):
        rng = random.Random(42 if direction == "long" else 4242)
        bars_by_ticker = {}
        for i in range(500):
            n = 35
            base = rng.uniform(5, 150)
            b = []
            p = base
            for _ in range(n):
                drift = rng.uniform(-0.01, 0.01)
                o = p
                c = max(0.5, p * (1 + drift))
                h = max(o, c) * (1 + rng.uniform(0.001, 0.02))
                l = min(o, c) * (1 - rng.uniform(0.001, 0.02))
                b.append(_bar(o, h, l, c, rng.uniform(220_000, 2_000_000)))
                p = c
            bars_by_ticker[f"T{i:04d}"] = _attach_ts(b)

        rd_rng = random.Random(99)

        def fake_analyze(bars, direction=direction):
            rd = rd_rng.choice([0, 5, 8, 12, 15, 20])
            details = [f" Solide Konsolidierung: {rd} Tage"] if rd else [" Keine Konsolidierung: 2 Tage"]
            return (True, 60, 188, details, "high", "B", 2, 2)

        saved = _patch_scan_io(monkeypatch, bars_by_ticker)
        monkeypatch.setattr(scanners, "analyze_breakout_imminent", fake_analyze)

        geo_log = []

        def spy_geometry(entry, stop, tp1, tp2, direction_up=None):
            res = real_trade_geometry(entry, stop, tp1, tp2, direction_up)
            geo_log.append(res)
            return res

        monkeypatch.setattr(scanners, "trade_geometry", spy_geometry)

        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scanners._bi_background_scan("k", direction=direction,
                                         candidates=[f"T{i:04d}" for i in range(500)])
        out = buf.getvalue()
        assert "Error analyzing" not in out, f"Crashes im Scan-Pfad ({direction})"
        assert len(geo_log) >= 50, f"zu wenige Level-Bewertungen ({direction}): {len(geo_log)}"
        assert saved["results"], f"keine Treffer im Mini-Fuzz ({direction})"
        for row in saved["results"]:
            g = real_trade_geometry(row["Entry"], row["StopLoss"], row["TP1"], row["TP2"],
                                    direction.upper())
            assert g["valid"], f"{direction} Geometrie-Verletzung: {row['Ticker']} {g['errors']}"
            assert g["rr"] is None or g["rr"] >= 1.2
            if direction == "short":
                risk = row["StopLoss"] - row["Entry"]
                assert row["TP1"] <= round(row["Entry"] - 0.5 * risk, 2) + 0.011, \
                    f"TP1-Formel verletzt: {row['Ticker']}"


def test_h2_cumulative_pump_filter(monkeypatch):
    """2 Tage x +7% (Σ 14% > 12%, > 4x StdDev) → Reject VOR der Analyse.
    Kontrolle: 2 x +4% (Σ 8%) → Analyse laeuft."""
    def mk(d1, d2):
        bars = _flat_bars(n=28, price=100.0, half_range=0.4, seed=3)
        c1 = 100.0 * (1 + d1)
        c2 = c1 * (1 + d2)
        bars.append(_bar(100.0, c1 + 0.2, 99.8, c1, 600_000))
        bars.append(_bar(c1, c2 + 0.2, c1 - 0.2, c2, 700_000))
        return _attach_ts(bars)

    calls = []
    saved = _patch_scan_io(
        monkeypatch, {"TEST": mk(0.07, 0.07)},
        analyze_result=(True, 80, 188, ["ok"], "high", "B", 2, 2), analyze_log=calls,
    )
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    assert calls == [], "Kumulativer Pump muss VOR der Analyse rejecten"
    assert saved["results"] == []

    calls2 = []
    saved2 = _patch_scan_io(
        monkeypatch, {"TEST": mk(0.04, 0.04)},
        analyze_result=(True, 80, 188, ["ok"], "high", "B", 2, 2), analyze_log=calls2,
    )
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    assert len(calls2) == 1, "Σ 8% (unter 12%) darf NICHT rejecten"


# ────────────────────────── M-1: Partial-Bar ──────────────────────────

class _FakeDatetime(_real_datetime):
    """Kontrollierte Uhr: now() liefert den gesetzten Zeitpunkt (tz-ignorant)."""
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


def test_m1_helper_session_logic(monkeypatch):
    """Partial-Bar-Erkennung: morgens strippen, nach US-Close behalten,
    Vortags-Bar immer behalten."""
    bars = _flat_bars(n=20, seed=11)
    today = "2026-06-10"
    bars[-1] = dict(bars[-1])

    # Bar von gestern → nie strippen
    bars[-1]["date"] = "2026-06-09"
    _FakeDatetime._now = _real_datetime(2026, 6, 10, 10, 30)
    monkeypatch.setattr(scanners, "datetime", _FakeDatetime)
    for b in bars:
        b.setdefault("date", "2026-01-01")
    assert scanners._bi_strip_partial_bar(bars) == bars

    # Heutiger Bar, 10:30 ET (Markt offen) → strippen
    bars[-1]["date"] = today
    _FakeDatetime._now = _real_datetime(2026, 6, 10, 10, 30)
    assert scanners._bi_strip_partial_bar(bars) == bars[:-1]

    # Heutiger Bar, 17:00 ET (nach US-Close) → komplett, behalten
    _FakeDatetime._now = _real_datetime(2026, 6, 10, 17, 0)
    assert scanners._bi_strip_partial_bar(bars) == bars


def test_m1_analysis_excludes_partial_bar(monkeypatch):
    """Scan-Pfad: Die an analyze uebergebenen Bars enden mit dem letzten
    KOMPLETTEN Tag — identisch mit und ohne heutigen Partial-Pump-Bar
    (Score-Neutralitaet, die 8/25 invalid→valid-Flips sind unmoeglich)."""
    base = _flat_bars(n=30, seed=5)
    # Letzter kompletter Tag = gestern (relativ zur Fake-Uhr)
    end_day = _real_datetime(2026, 6, 9).date()
    _attach_ts(base, end_day=end_day)

    pump = _bar(100.0, 106.0, 99.8, 105.5, 90_000)  # heutiger Partial-Bar, +5.5%
    pump["t"] = int(_real_datetime(2026, 6, 10, 12, 0).timestamp() * 1000)
    with_partial = base + [pump]

    _FakeDatetime._now = _real_datetime(2026, 6, 10, 10, 30)  # morgens, Markt offen
    monkeypatch.setattr(scanners, "datetime", _FakeDatetime)

    log_a, log_b = [], []
    _patch_scan_io(monkeypatch, {"TEST": base},
                   analyze_result=(True, 80, 188, ["ok"], "high", "B", 2, 2), analyze_log=log_a)
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])

    _patch_scan_io(monkeypatch, {"TEST": with_partial},
                   analyze_result=(True, 80, 188, ["ok"], "high", "B", 2, 2), analyze_log=log_b)
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])

    assert len(log_a) == 1 and len(log_b) == 1
    dates_a = [b["date"] for b in log_a[0]["bars"]]
    dates_b = [b["date"] for b in log_b[0]["bars"]]
    assert dates_b == dates_a, "Partial-Bar darf die Analyse-Bars nicht veraendern"
    assert dates_b[-1] == "2026-06-09", "Analyse muss mit letztem KOMPLETTEN Tag enden"
    closes_a = [b["close"] for b in log_a[0]["bars"]]
    closes_b = [b["close"] for b in log_b[0]["bars"]]
    assert closes_a == closes_b


def test_m1_after_close_bar_counts(monkeypatch):
    """Nach US-Close ist der heutige Bar komplett und MUSS in die Analyse."""
    base = _flat_bars(n=30, seed=5)
    end_day = _real_datetime(2026, 6, 10).date()
    _attach_ts(base, end_day=end_day)

    _FakeDatetime._now = _real_datetime(2026, 6, 10, 17, 30)  # nach Close
    monkeypatch.setattr(scanners, "datetime", _FakeDatetime)

    log = []
    _patch_scan_io(monkeypatch, {"TEST": base},
                   analyze_result=(True, 80, 188, ["ok"], "high", "B", 2, 2), analyze_log=log)
    scanners._bi_background_scan("k", direction="long", candidates=["TEST"])
    assert log[0]["bars"][-1]["date"] == "2026-06-10", "Kompletter heutiger Bar muss drin bleiben"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
