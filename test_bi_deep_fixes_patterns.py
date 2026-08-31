#!/usr/bin/env python3
"""
Regressions-Tests fuer die BI-Scanner-Tiefen-Fixes (BI-Audit 2026-06-10):

  K-2  MACD-Vertrag: calculate_macd_histogram_series (Serie statt Skalar-Missbrauch)
       -> Signal 13 crashfrei bei jedem n, Punkte ab n>=36 vergebbar
  M-2  fires/hits-Deduplizierung: OBV+InstAcc = max 1 fire/hit (Volumen-Komplex);
       OB-/Liq-"vorhanden"-hits nur mit 3%-Naehe-Bedingung
  M-3  Distribution-Malus Long: Lower-Highs (3-Bar) >= 65% + Down-Vol > 1.2x Up-Vol
       => score -= 10 (nur Long; Akkumulation unberuehrt)
  N    max_score = 173 (Doppelzaehlungen entfernt), confidence <= 100%

Synthetische, deterministische Serien — kein Netz, keine API.
"""
import inspect
import sys, os, random

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from modules.patterns import analyze_breakout_imminent
from modules.indicators import calculate_macd, calculate_macd_histogram_series


# ====================================================================
# Generatoren (deterministisch, autark — kein Import aus Session-Pfaden)
# ====================================================================

def _mk(o, h, l, c, v=1_000_000):
    return {"open": o, "high": max(h, o, c), "low": min(l, o, c),
            "close": c, "volume": int(v)}


def gen_textbook_accumulation(seed=1, n_pre=60, n_range=28, base=30.0, range_pct=5.0,
                              dryup=0.45, accum_bias=1.9, vol_base=900_000, hl_drift=0.35):
    """Lehrbuch-Wyckoff-Akkumulation (identische Logik wie Audit-Generator s3)."""
    rng = random.Random(seed)
    bars = []
    p = base * 0.92
    for i in range(n_pre):
        drift = base * 0.0012
        noise = rng.uniform(-1, 1) * base * 0.008
        o = p
        c = max(0.5, p + drift + noise)
        h = max(o, c) + base * rng.uniform(0.001, 0.006)
        l = min(o, c) - base * rng.uniform(0.001, 0.006)
        bars.append(_mk(o, h, l, c, vol_base * rng.uniform(0.8, 1.3)))
        p = c
    range_high = base * (1 + range_pct / 200.0)
    range_low = base * (1 - range_pct / 200.0)
    p = (range_high + range_low) / 2
    for i in range(n_range):
        frac = i / max(1, n_range - 1)
        cur_low = range_low + (range_high - range_low) * hl_drift * frac
        squeeze = 1.0 - 0.55 * frac
        width = (range_high - cur_low) * squeeze
        center = range_high - width * rng.uniform(0.15, 0.5)
        o = center + rng.uniform(-0.25, 0.25) * width
        up_day = rng.random() < 0.58
        c = min(range_high, max(cur_low, o + (width * 0.35 if up_day else -width * 0.30)))
        h = max(o, c) + width * rng.uniform(0.05, 0.25)
        l = min(o, c) - width * rng.uniform(0.05, 0.25)
        h = min(h, range_high * 1.002)
        l = max(l, cur_low * 0.998)
        v_dry = vol_base * (1.0 - (1.0 - dryup) * frac)
        v = v_dry * (accum_bias if (up_day and rng.random() < 0.5) else rng.uniform(0.6, 1.1))
        bars.append(_mk(o, h, l, c, v))
    return bars


def gen_textbook_distribution(seed=2, n_pre=60, n_range=28, base=40.0, range_pct=6.0,
                              vol_base=1_200_000):
    """Lehrbuch-Distribution: Lower Highs, Down-Volumen steigt (Audit-Generator s7.3)."""
    rng = random.Random(seed)
    bars = []
    p = base * 1.06
    for i in range(n_pre):
        noise = rng.uniform(-1, 1) * base * 0.008
        o = p
        c = max(0.5, p - base * 0.0010 + noise)
        h = max(o, c) + base * rng.uniform(0.001, 0.006)
        l = min(o, c) - base * rng.uniform(0.001, 0.006)
        bars.append(_mk(o, h, l, c, vol_base * rng.uniform(0.8, 1.2)))
        p = c
    range_high = base * (1 + range_pct / 200.0)
    range_low = base * (1 - range_pct / 200.0)
    for i in range(n_range):
        frac = i / max(1, n_range - 1)
        cur_high = range_high - (range_high - range_low) * 0.38 * frac
        width = (cur_high - range_low) * (1.0 - 0.4 * frac)
        o = range_low + width * rng.uniform(0.3, 0.8)
        down_day = rng.random() < 0.58
        c = max(range_low, min(cur_high, o - (width * 0.35 if down_day else -width * 0.28)))
        h = max(o, c) + width * rng.uniform(0.05, 0.2)
        l = min(o, c) - width * rng.uniform(0.05, 0.2)
        l = max(l, range_low * 0.998)
        v = vol_base * (1.0 + 0.8 * frac) * (1.8 if down_day else 0.8)
        bars.append(_mk(o, h, l, c, v))
    return bars


def gen_vol_only(seed, n=90, base=25.0, vol_base=600_000):
    """NUR-Volumen-Akkumulation: Preis symmetrisches Rauschen, Up-Tage 2.5x Volumen."""
    rng = random.Random(seed)
    bars = []
    p = base
    for i in range(n):
        step = rng.choice([1, -1]) * base * 0.004
        o = p
        c = max(0.5, p + step)
        h = max(o, c) + base * 0.002
        l = min(o, c) - base * 0.002
        up = c > o
        bars.append(_mk(o, h, l, c, vol_base * (2.5 if up else 0.55)))
        p = c
    return bars


def gen_macd_turn(n=50):
    """Synthetischer MACD-Aufwaertsdreher: beschleunigter Abverkauf, letzte 3 Bars
    bremsen ab (Selling Exhaustion) -> Histogramm negativ + frisch steigend."""
    bars = []
    p = 50.0
    end_rates = [-0.009, -0.0085, -0.008]
    n_down = n - len(end_rates)
    for i in range(n_down):
        p *= (1 - 0.005 * (1 + i / n_down))
        bars.append(_mk(p * 1.002, p * 1.005, p * 0.995, p, 900_000))
    for r in end_rates:
        p *= (1 + r)
        bars.append(_mk(p * 1.001, p * 1.004, p * 0.996, p, 1_000_000))
    return bars


def gen_liq_series(pool_high):
    """Enge Serie mit kontrolliertem Buyside-Pool (2 Equal Highs bei idx 6/12,
    ausserhalb des 15-Bar-Range-Fensters) + Range-High-Anker 101.0 bei idx 20.
    Basis-Highs monoton fallend -> keine ungewollten Equal-High-Cluster."""
    bars = []
    for i in range(30):
        h = 100.4 - 0.012 * i          # monoton fallend, keine Basis-Pivots
        bars.append(_mk(99.9, h, 99.6, 100.0))
    bars[6] = _mk(100.0, pool_high, 99.8, 100.1)
    bars[12] = _mk(100.0, pool_high, 99.8, 100.1)
    bars[20] = _mk(100.0, 101.0, 99.8, 100.2)   # range_high_17-Anker (Single, kein Pool)
    return bars


def gen_ob_series(consol_level):
    """Bullish-OB-Serie: baerische Kerze (ob_low=99.8) + Displacement, danach
    Konsolidierung auf consol_level. OB liegt nahe Range-Low (near_support),
    Preis-Naehe zur Zone steuert consol_level (102 nah / 105 fern)."""
    bars = []
    for i in range(15):
        bars.append(_mk(100.0, 100.4, 99.6, 100.1))
    bars.append(_mk(100.5, 100.6, 99.6, 99.8))   # baerische OB-Kerze
    bars.append(_mk(99.9, consol_level + 0.4, 99.7, consol_level + 0.3, 2_500_000))
    bars.append(_mk(consol_level + 0.2, consol_level + 0.5, consol_level - 0.2, consol_level + 0.2))
    for i in range(12):
        w = 0.1 * (i % 3)
        bars.append(_mk(consol_level + w * 0.5, consol_level + 0.5 + w,
                        consol_level - 0.3, consol_level + 0.2 + w * 0.3))
    return bars


def _details(bars, direction="long"):
    res = analyze_breakout_imminent(bars, direction=direction)
    return res


# ====================================================================
# K-2: MACD-Vertrag
# ====================================================================

def test_k2_sweep_no_crash():
    """Bar-Sweep n=15..90: KEIN Crash bei irgendeinem n (vorher TypeError ab 35)."""
    bars = gen_textbook_accumulation(seed=11)
    for n in range(15, 91):
        res = analyze_breakout_imminent(bars[-n:], direction="long")
        assert isinstance(res[1], int), f"n={n}: Score kein int"
        res_s = analyze_breakout_imminent(bars[-n:], direction="short")
        assert isinstance(res_s[1], int), f"n={n} short: Score kein int"


def test_k2_signal13_awards_points_n50():
    """Synthetischer MACD-Aufwaertsdreher bei n=50 -> 10-Punkte-Divergenz-Zweig."""
    bars = gen_macd_turn(n=50)
    closes = [b["close"] for b in bars]
    hist = [h for h in calculate_macd_histogram_series(closes) if h is not None]
    assert hist[-1] < 0 and (hist[-1] - hist[-3]) > 0 and hist[-2] < hist[-1], \
        f"Dreher-Geometrie verletzt: {hist[-3:]}"
    res = analyze_breakout_imminent(bars, direction="long")
    macd_det = [d for d in res[3] if "MACD" in d]
    assert macd_det, "Kein MACD-Detail"
    assert "Divergenz bullisch" in macd_det[0], \
        f"Signal 13 vergibt keine Divergenz-Punkte: {macd_det[0]!r}"


def test_k2_none_padding_contract():
    """Serie: Laenge==Input, None-Prefix bis Index 32, erster Wert bei Index 33."""
    bars = gen_textbook_accumulation(seed=11)
    closes = [b["close"] for b in bars]
    assert calculate_macd_histogram_series([]) == []
    s33 = calculate_macd_histogram_series(closes[:33])
    assert len(s33) == 33 and all(v is None for v in s33), "n=33 muss komplett None sein"
    s34 = calculate_macd_histogram_series(closes[:34])
    assert len(s34) == 34, "Laenge muss Input-Laenge entsprechen"
    non_none = [i for i, v in enumerate(s34) if v is not None]
    assert non_none == [33], f"Erster berechenbarer Index muss 33 sein, ist {non_none}"


def test_k2_scalar_contract_untouched():
    """calculate_macd liefert weiterhin SKALARE; Serie[-1] == Skalar-Histogramm."""
    bars = gen_textbook_accumulation(seed=11)
    m_line, m_sig, m_hist = calculate_macd(bars)
    assert isinstance(m_hist, float) and isinstance(m_line, float), "Skalar-Vertrag verletzt"
    series = calculate_macd_histogram_series([b["close"] for b in bars])
    assert abs(series[-1] - m_hist) < 1e-9, \
        f"Serien-Ende ({series[-1]}) != Skalar-Hist ({m_hist}) — EMA-Konvention abweichend"
    short = calculate_macd(bars[:20])
    assert short == (None, None, None), "Skalar-Kurzdaten-Vertrag verletzt"


# ====================================================================
# M-2: fires/hits-Deduplizierung
# ====================================================================

def test_m2_volume_complex_max_one_fire():
    """Nur-Volumen-Serie, OBV-fire UND InstAcc-fire beide aktiv, keine anderen
    fire-Quellen -> fires muss exakt 1 sein (vorher 2 = Pseudo-Konfirmation)."""
    other_fires = ["ADX Wende", "Hohe Resilience", "Schwache Resilience",
                   "OB nahe Breakout", "Stop-Hunt"]
    bars = gen_vol_only(seed=3)[-30:]
    iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
    assert any("OBV-Divergenz" in d for d in det), "Vorbedingung: OBV-fire-Tag fehlt"
    assert any("Inst.-Akkumulation" in d for d in det), "Vorbedingung: InstAcc-fire-Tag fehlt"
    assert not any(any(t in d for t in other_fires) for d in det), \
        "Vorbedingung verletzt: fremde fire-Quelle aktiv"
    assert fires == 1, f"Volumen-Komplex muss auf 1 fire dedupliziert sein, ist {fires}"
    assert any("M-2 Dedup" in d for d in det), "Dedup-Detaileintrag fehlt"


def test_m2_volume_complex_population():
    """Population: Nur-Volumen-Serien duerfen aus dem Volumen-Komplex allein
    keine 2 fires mehr beziehen (beide Tags aktiv => Zaehler trotzdem max +1)."""
    for seed in range(40):
        bars = gen_vol_only(seed=seed)[-30:]
        iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
        obv_f = any("OBV-Divergenz" in d for d in det)
        acc_f = any("Inst.-Akkumulation" in d for d in det)
        other = sum(1 for d in det for t in ["ADX Wende", "Hohe Resilience",
                    "Schwache Resilience", "OB nahe Breakout", "Stop-Hunt"] if t in d)
        if obv_f and acc_f:
            assert fires <= 1 + other, \
                f"seed={seed}: Doppel-fire trotz Dedupe (fires={fires}, fremde={other})"


def test_m2_liq_far_no_hit():
    """Buyside-Pool 6% ueber Range-High: Punkte ja (+5), SM-hit NEIN."""
    bars = gen_liq_series(pool_high=107.0)
    iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
    liq_det = [d for d in det if "Buyside Liq" in d]
    assert liq_det, f"Kein Buyside-Liq-Detail: {[d for d in det if 'Liq' in d]}"
    assert "kein SM-hit" in liq_det[0], f"Pool >3% entfernt muss hit-frei sein: {liq_det[0]!r}"


def test_m2_liq_near_hit():
    """Buyside-Pool 0.3% unterm Range-High (Equal Highs an der Boundary): hit JA."""
    bars = gen_liq_series(pool_high=100.7)
    iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
    liq_det = [d for d in det if "Liq nahe Range-High" in d]
    assert liq_det, \
        f"Pool binnen 3% muss hit geben: {[d for d in det if 'Liq' in d]}"


def test_m2_ob_near_vs_far_hit():
    """OB stuetzt Range-Low: hit nur, wenn die Zone binnen 3% des Preises liegt."""
    far = analyze_breakout_imminent(gen_ob_series(105.0), direction="long")
    far_det = [d for d in far[3] if "stuetzt Range-Low" in d]
    assert far_det and "kein SM-hit" in far_det[0], \
        f"OB-Zone >3% vom Preis darf keinen hit geben: {far_det}"
    near = analyze_breakout_imminent(gen_ob_series(102.0), direction="long")
    near_det = [d for d in near[3] if "stuetzt Range-Low" in d]
    assert near_det and "nahe Preis" in near_det[0], \
        f"OB-Zone binnen 3% muss hit geben: {near_det}"


def test_m2_textbook_reaches_3_fires():
    """Echte Mehrquellen-Akkumulation erreicht weiterhin >= 3 fires
    (Volumen-Komplex=1 + ADX/Resilience/OB/Liq-mit-Naehe)."""
    best = 0
    for seed in range(15):
        bars = gen_textbook_accumulation(seed=seed, base=30, range_pct=3.0, dryup=0.30,
                                         accum_bias=2.8, hl_drift=0.5)[-30:]
        iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
        best = max(best, fires)
        if fires >= 3:
            break
    assert best >= 3, f"Lehrbuch-Setup erreicht keine 3 fires mehr (max {best}) — Dedupe zu hart"


# ====================================================================
# M-3: Distribution-Malus (nur Long)
# ====================================================================

def test_m3_distribution_malus_applies():
    """Lehrbuch-Distribution: Malus-Detail vorhanden, Long-Score sinkt."""
    hit_cnt = 0
    scores = []
    for seed in range(30):
        bars = gen_textbook_distribution(seed=seed)[-30:]
        iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
        scores.append(sc)
        if any("Distribution-Malus: -10" in d for d in det):
            hit_cnt += 1
    assert hit_cnt >= 21, f"Malus greift nur in {hit_cnt}/30 Lehrbuch-Distributionen"
    avg = sum(scores) / len(scores)
    assert avg < 55.0, f"Ø Long-Score auf Distribution zu hoch: {avg:.1f} (alt ~59.5)"


def test_m3_accumulation_untouched():
    """Lehrbuch-Akkumulation verliert KEINE Punkte durch den Malus."""
    for seed in range(30):
        bars = gen_textbook_accumulation(seed=seed)[-30:]
        iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
        assert not any("Distribution-Malus" in d for d in det), \
            f"seed={seed}: Malus trifft faelschlich eine Akkumulation"


def test_m3_short_direction_no_malus():
    """Short-Richtung bekommt keinen Distribution-Malus (Short profitiert davon)."""
    for seed in range(10):
        bars = gen_textbook_distribution(seed=seed)[-30:]
        iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="short")
        assert not any("Distribution-Malus" in d for d in det), \
            f"seed={seed}: Malus faelschlich in Short-Richtung"


# ====================================================================
# N: max_score-Arithmetik
# ====================================================================

def test_n_max_score_pinned_173():
    """max_score bleibt 173; confidence ist die echte Green-Quote."""
    bars = gen_textbook_accumulation(seed=11)[-50:]
    iv, sc, mx, det, conf, gr, fires, hits = analyze_breakout_imminent(bars, direction="long")
    assert mx == 173, f"max_score muss 173 sein, ist {mx}"
    result = analyze_breakout_imminent(bars, direction="long")
    assert result.indicator_contract_ok is True
    assert conf == round(result.green_count / 20 * 100), (
        f"confidence inkonsistent: {conf} vs {result.green_count}/20"
    )
    assert result.weighted_score_pct == round(sc / 173 * 100)
    assert 0 <= conf <= 100, f"confidence ausserhalb [0,100]: {conf}"
    early = analyze_breakout_imminent(bars[:5], direction="long")
    assert early[2] == 173, f"Early-Return max_score muss 173 sein, ist {early[2]}"
    assert early[4] == 0 and early.indicator_contract_ok is False
    # Score-Cap: score darf max_score nie ueberschreiten -> confidence <= 100 strukturell
    for seed in range(10):
        b2 = gen_textbook_accumulation(seed=seed, range_pct=3.0, dryup=0.3,
                                       accum_bias=2.8, hl_drift=0.5)[-40:]
        r = analyze_breakout_imminent(b2, direction="long")
        assert r[1] <= r[2] and r[4] <= 100


def test_score_is_capped_before_confidence_and_grade_are_derived():
    source = inspect.getsource(analyze_breakout_imminent)

    cap_index = source.index("score = max(0, min(score, max_score))")
    confidence_index = source.index("direction_confidence =", cap_index)
    grade_index = source.index("grade =", confidence_index)

    assert cap_index < confidence_index < grade_index


# ====================================================================
# Standalone-Runner
# ====================================================================

def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} Tests bestanden")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
