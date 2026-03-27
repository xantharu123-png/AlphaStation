#!/usr/bin/env python3
"""
Test Suite v3 (FINAL) für calculate_setup_score()
Alle Fixes verifiziert, Trader-Logik validiert
"""
import sys, re, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)

# V69.6: Function moved to modules/scorers.py
from modules.scorers import calculate_setup_score
print("✅ calculate_setup_score() geladen (from modules/scorers.py)\n")

passed = failed = 0

def test(name, score, lo, hi, details=""):
    global passed, failed
    ok = lo <= score <= hi
    print(f"  {'✅' if ok else '❌'} {name}: {score}/100  [{lo}-{hi}] {details}")
    passed += ok; failed += (not ok)

def rank(name, a, b):
    global passed, failed
    ok = a > b
    print(f"  {'✅' if ok else '❌'} {name}: {a} > {b}")
    passed += ok; failed += (not ok)

# ══════════════════════════════════════════════════════════════
print("=" * 70)
print("1. PERFEKTE SETUPS")
print("=" * 70)
test("Perfekter Long",  calculate_setup_score(4.5, 3.5, 0.88, 8, 4, 0.8, 3.0, 8e6, 45, "long"), 95, 100)
test("Perfekter Short", calculate_setup_score(-5.0, 3.0, 0.12, 3, 6, 1.0, 3.5, 12e6, 80, "short"), 95, 100)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("2. GUTE SETUPS")
print("=" * 70)
test("Guter Long", calculate_setup_score(3.0, 1.8, 0.72, 18, 10, 2.5, 2.5, 2e6, 30, "long"), 60, 85)
test("Early Entry",calculate_setup_score(1.0, 2.5, 0.70, 12, 8, 0.5, 4.0, 5e6, 55, "long"), 55, 80)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("3. CHASE SETUPS")
print("=" * 70)
c18 = calculate_setup_score(18, 4.0, 0.90, 5, 3, 0.8, 3.5, 15e6, 25, "long")
c25 = calculate_setup_score(25, 5.0, 0.92, 3, 2, 1.0, 4.0, 20e6, 15, "long")
cs15 = calculate_setup_score(-15, 3.5, 0.08, 5, 8, 1.2, 3.0, 8e6, 40, "short")
test("Chase +18%", c18, 60, 70)
test("Chase +25%", c25, 55, 66)
test("Chase Short -15%", cs15, 60, 70)
rank("Chase 18% > Chase 25%", c18, c25)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("4. WICK PENALTY")
print("=" * 70)
ba = dict(change_pct=3.0, rvol=2.0, close_pos=0.85, upper_wick_pct=10,
          vortag_pct=1.0, atr_pct=3.0, dollar_volume=3e6, price=50, direction="long")
clean = calculate_setup_score(**{**ba, "lower_wick_pct": 5})
mild  = calculate_setup_score(**{**ba, "lower_wick_pct": 35})
hard  = calculate_setup_score(**{**ba, "lower_wick_pct": 55})
test("Clean (LW 5%)", clean, 85, 100)
test("Mild Penalty (LW 35%)", mild, clean-4, clean-2)
test("Hard Penalty (LW 55%)", hard, clean-7, clean-5)
rank("Clean > Mild", clean, mild)
rank("Mild > Hard", mild, hard)

sb = dict(change_pct=-4.0, rvol=2.5, close_pos=0.15, lower_wick_pct=10,
          vortag_pct=1.5, atr_pct=3.0, dollar_volume=5e6, price=60, direction="short")
sc = calculate_setup_score(**{**sb, "upper_wick_pct": 5})
sm = calculate_setup_score(**{**sb, "upper_wick_pct": 40})
sh = calculate_setup_score(**{**sb, "upper_wick_pct": 55})
test("Short Clean", sc, 85, 100)
rank("Short: Clean > Mild", sc, sm)
rank("Short: Mild > Hard", sm, sh)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("5. TIMING FALLBACK (ohne ATR)")
print("=" * 70)
t0 = calculate_setup_score(0.1, 1.0, 0.55, 20, 20, 0.5, None, 500_000, 100, "long")
t1 = calculate_setup_score(1.0, 1.0, 0.55, 20, 20, 0.5, None, 500_000, 100, "long")
t3 = calculate_setup_score(3.0, 1.0, 0.55, 20, 20, 0.5, None, 500_000, 100, "long")
t12= calculate_setup_score(12.0, 1.0, 0.55, 20, 20, 0.5, None, 500_000, 100, "long")
test("0.1% (nichts)", t0, 15, 40)
test("1.0% (kaum)", t1, 25, 50)
test("3.0% (gut)", t3, 50, 70)
test("12% (chase)", t12, 20, 55)
rank("3% > 0.1%", t3, t0)
rank("3% > 12% (sweet > chase)", t3, t12)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("6. RVOL DOUBLE-COUNT FIX")
print("=" * 70)
b = dict(change_pct=3.0, close_pos=0.75, upper_wick_pct=12, lower_wick_pct=8,
         vortag_pct=1.0, atr_pct=3.0, dollar_volume=2e6, price=40, direction="long")
lo = calculate_setup_score(**{**b, "rvol": 1.0})
hi = calculate_setup_score(**{**b, "rvol": 3.5})
test(f"RVOL Spread = {hi-lo}pts (nur Kat 1)", hi-lo, 12, 16, f"{lo} vs {hi}")

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("7. EARLY ENTRY + TIMING MIN-GATE")
print("=" * 70)
early = calculate_setup_score(1.2, 2.0, 0.70, 12, 8, 0.5, 4.0, 3e6, 35, "long")
sweet = calculate_setup_score(5.0, 2.0, 0.70, 12, 8, 0.5, 4.0, 3e6, 35, "long")
sub   = calculate_setup_score(0.3, 2.0, 0.70, 12, 8, 0.5, 4.0, 3e6, 35, "long")
test("Early Entry (0.3x ATR)", early, 55, 80)
test("Sweet Spot (1.25x ATR)", sweet, 75, 100)
test("Sub-Early (0.075x ATR, 0.3% chg)", sub, 45, 65, "Timing=0 (< 0.5% gate)")
rank("Sweet > Early", sweet, early)
rank("Early > Sub-Early", early, sub)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("8. RANGREIHENFOLGE")
print("=" * 70)
S = {
    "A) Perfekt":     calculate_setup_score(4.5, 3.5, 0.88, 8, 4, 0.8, 3.0, 8e6, 45, "long"),
    "B) Gut":         calculate_setup_score(3.0, 2.0, 0.72, 18, 8, 1.0, 2.5, 2e6, 30, "long"),
    "C) Early":       calculate_setup_score(1.2, 2.5, 0.68, 12, 6, 0.5, 5.0, 4e6, 50, "long"),
    "D) Mittel":      calculate_setup_score(1.5, 1.2, 0.58, 22, 15, 3.5, 2.0, 800_000, 25, "long"),
    "E) Chase 15%":   calculate_setup_score(15, 3.0, 0.85, 5, 3, 1.0, 3.0, 10e6, 20, "long"),
    "F) Chase 25%":   calculate_setup_score(25, 5.0, 0.92, 3, 2, 1.0, 4.0, 20e6, 15, "long"),
    "G) Schwach":     calculate_setup_score(0.5, 0.6, 0.48, 25, 20, 4.0, 2.0, 60_000, 8, "long"),
    "H) Flat+Base":   calculate_setup_score(0.1, 0.4, 0.50, 15, 15, 0.3, 1.5, 200_000, 100, "long"),
}
for i, (n, s) in enumerate(sorted(S.items(), key=lambda x: x[1], reverse=True), 1):
    bar = "█" * (s // 2) + "░" * (50 - s // 2)
    print(f"  {i}. {bar} {s:3d}  {n}")

print()
rank("Perfekt > Gut", S["A) Perfekt"], S["B) Gut"])
rank("Perfekt > Chase", S["A) Perfekt"], S["E) Chase 15%"])
rank("Gut > Mittel", S["B) Gut"], S["D) Mittel"])
rank("Gut > Chase 15%", S["B) Gut"], S["E) Chase 15%"])
rank("Early > Mittel", S["C) Early"], S["D) Mittel"])
rank("Chase 15% > Chase 25%", S["E) Chase 15%"], S["F) Chase 25%"])
rank("Mittel > Schwach", S["D) Mittel"], S["G) Schwach"])
# Flat+Base > Schwach ist korrekt: Flat hat ruhigen Vortag (Base), 
# OK Liquidität und neutrale Kerze → besseres Watchlist-Material
# als illiquide Aktie mit schlechter Kerze und lautem Vortag
rank("Flat+Base > Schwach (Base > kein Setup)", S["H) Flat+Base"], S["G) Schwach"])

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("9. EDGE CASES")
print("=" * 70)
test("Alles None/Zero", calculate_setup_score(0,0,None,None,None,None,None,0,0,"long"), 0, 5)
test("Nur Change=5%", calculate_setup_score(5.0,None,None,None,None,None,None,None,10,"long"), 20, 28)
test("Extreme Werte", calculate_setup_score(-999,999,0.0,0,100,0,0.01,999e6,1,"short"), 0, 100)
test("Negativer Preis", calculate_setup_score(3.0,2.0,0.7,10,10,1.0,3.0,1e6,-5,"long"), 50, 100)
test("Max Wick Penalty", calculate_setup_score(0.5,0.5,0.25,40,70,5.0,None,50_000,3,"long"), 0, 15)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("10. EXAKTE BREAKDOWNS")
print("=" * 70)
test("Perfekt=100", calculate_setup_score(4.5,3.5,0.88,8,4,0.8,3.0,8e6,45,"long"), 100, 100)
test("Chase18=68", calculate_setup_score(18,4.0,0.90,5,3,0.8,3.5,15e6,25,"long"), 68, 68)
test("Chase25=65", calculate_setup_score(25,5.0,0.92,3,2,1.0,4.0,20e6,15,"long"), 65, 65)
test("Short15=68", calculate_setup_score(-15,3.5,0.08,5,8,1.2,3.0,8e6,40,"short"), 68, 68)

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
total = passed + failed
if failed:
    print(f"ERGEBNIS: {passed}/{total} — {failed} FEHLGESCHLAGEN ❌")
else:
    print(f"ERGEBNIS: {passed}/{total} ✅ ALLE TESTS GRÜN")
print("=" * 70)
