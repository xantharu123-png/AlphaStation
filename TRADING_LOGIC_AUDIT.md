# 🔍 TRADING LOGIC AUDIT — Scanner.py
## Datum: 19. Februar 2026
## Methode: Jede Strategie aus Trader-Perspektive geprüft: "Ergibt das auf dem Chart Sinn?"

---

## 🔴 KRITISCHE BUGS (Trading-Entscheidungen falsch)

### BUG 1: SQUEEZE / CAPITULATION ist DEAD CODE
**Datei:** classify_pm_setup(), Zeilen 7871-7876
**Problem:** Der SQUEEZE-Check (`abs_change >= 10`) kommt NACH dem Block `abs_change >= 5` (Zeile 7843), der schon returnt.
```
Aktie +20%, Position 80% → Line 7845: abs_change >= 5 ✓ → is_up ✓ + position >= 70 ✓ 
                          → RETURN "MOMENTUM" (Line 7849)
                          → SQUEEZE-Block wird NIE erreicht!
```
**Auswirkung:** Extreme Squeeze-Kandidaten (+20%, +30%) werden als normales "MOMENTUM" klassifiziert. Der Trader verpasst das SQUEEZE-Signal.
**Fix:** SQUEEZE-Check VOR den 5%-Block verschieben.

---

### BUG 2: classify_pm_setup — Lücke bei 3-5% Moves in Mid-Range
**Datei:** classify_pm_setup(), Zeilen 7878-7896
**Problem:** Stock +4%, PM-Position 45% (Mitte der Range):
- STARKE MOVES (>5%) → nein, nur 4%
- MODERATE (3-5%) → pm_position 45% matcht weder >=65 noch <35
- KLEINE (2-3%) → nein, 4% ist > 3
- **DEFAULT → "WATCH" 👀**

**Auswirkung:** Ein +4% Move mit mittlerer Position bekommt ein nichtssagendes "WATCH" statt einer klaren Einschätzung.
**Fix:** Mid-Range Returns zum MODERATE-Block hinzufügen.

---

### BUG 3: Reversal Setup (📦🪤) — Gleiche Trend-Blindheit wie Reversal Hunter
**Datei:** Strategie-Definition Zeile 385-392
**Filter:** Vortag -8% bis -2%, Change +2-15%, RVOL 1.5-10
**Problem:** Prüft NUR "gestern rot + heute grün" — genau wie Reversal Hunter vor dem Fix. Ein MYRG im Uptrend ($200→$266) mit einem roten Tag gestern und +3% heute würde dieses Signal triggern.
**Fix:** Gleiche Trend-Erkennung wie bei Reversal Hunter einbauen.

---

### BUG 4: MA Bounce Timing — MA Distance immer 0 → aufgeblähter Score
**Datei:** calculate_ma_bounce_timing(), Zeile 2481
**Problem:** `ma_distance = abs(row_data.get("MA_Distance%", 0))` — dieses Feld wird vom Scanner nie befüllt. Ergebnis: ma_distance = 0 → "Perfekt am MA" → 1.5 Punkte automatisch.
**Auswirkung:** MA Bounce Timing-Score ist um 1.5/5.5 Punkte aufgebläht. Ein Stock 10% vom MA entfernt bekommt "Perfekt am MA" angezeigt.
**Fix:** Wenn kein MA-Daten → neutralen Score (0.5) vergeben oder Faktor überspringen.

---

## 🟡 LOGIK-SCHWÄCHEN (Funktioniert, aber produziert schlechte Signale)

### SCHWÄCHE 5: Dip Buy RVOL-Floor zu niedrig (0.3)
**Filter:** RVOL 0.3 – 1.5
**Problem:** RVOL 0.3 = nur 30% des normalen Volumens. Das ist kein "Dip Buy" — das ist eine Aktie die niemand handeln will. Echte Dip-Buyer brauchen mindestens normales Volumen um sicher rein und raus zu kommen.
**Beispiel:** Stock -5%, RVOL 0.3, $12 → Scanner sagt "Dip Buy" → Trader kauft → Spread ist riesig, kein Käufer da zum Exitten.
**Fix:** RVOL-Floor auf 0.6 anheben (mindestens 60% des normalen Volumens).

---

### SCHWÄCHE 6: Gap Up — RVOL-Floor 0.5 zu niedrig  
**Filter:** Gap % 2-50%, RVOL 0.5-100
**Problem:** RVOL 0.5 = halbes normales Volumen. Ein Gap auf halber Lautstärke ist oft ein Gap-Fill-Setup, KEIN Gap-and-Go. Echte Gap & Go braucht überdurchschnittliches Volumen.
**Fix:** Gap Up RVOL-Floor auf 1.0 anheben. Gap Up (High Vol) ist schon bei 2.0 — dann deckt Gap Up den Bereich 1.0-2.0 ab.

---

### SCHWÄCHE 7: RSI-Schätzung inkonsistent und irreführend
**Betrifft:** calculate_breakout_timing, calculate_reversal_timing, calculate_ma_bounce_timing
**Problem:** 
- Breakout: `estimated_rsi = 50 + (change_pct * 2.5)`
- Reversal: `estimated_rsi = 50 + (change_pct * 3.0)`
- MA Bounce: `estimated_rsi = 50 + (change_pct * 3.0)`

Unterschiedliche Multiplikatoren für den gleichen Stock! Und alle ignorieren den vorherigen RSI-Stand. Ein Stock bei RSI 75 der +2% macht hat real ~RSI 77, nicht RSI 55.

**Auswirkung:** Reversal-Timing sagt "RSI ~56 — Nicht überverkauft" für einen Stock der real RSI 35 hat.
**Fix:** Einheitlicher Multiplikator (3.0) + klar als "Tages-RSI (geschätzt)" labeln + Disclaimer "Basiert nur auf heutigem Change, nicht auf Multi-Tag RSI".

---

### SCHWÄCHE 8: Whale Watch ohne Close Position — fängt Churn
**Filter:** RVOL 3.0+, Change 2%+
**Problem:** Hohes Volumen + leichter Anstieg könnte Distribution sein (Big Player verkaufen in Stärke). Ohne Close Position Filter kann man nicht unterscheiden ob der "Whale" kauft (close near high) oder verkauft (close near low trotz leichtem Plus).
**Beispiel:** Stock +2.5%, RVOL 4.0, Close Position 0.2 (nahe Tagestief) → "Whale Watch" Bullish Signal → aber die Whales VERKAUFEN!
**Fix:** Close Position 0.55+ für Whale Watch Long, 0.45- für Short hinzufügen.

---

### SCHWÄCHE 9: Breakout Timing — ATR% oft 0 → unzuverlässige Überdehnung
**Datei:** calculate_breakout_timing(), Zeile 2208
**Problem:** `atr_pct = row_data.get("ATR%", 0)` — Feld nicht immer befüllt. Fallback nutzt pauschale 2.5% ATR-Schätzung. Ein volatiler Biotech (real ATR 8%) wird als "überdehnt" bewertet bei +6% Move, ein Low-Vol Utility (real ATR 1%) als "normal".
**Fix:** ATR aus Historical Data berechnen wenn verfügbar, oder Fallback klar als Schätzung kennzeichnen.

---

## 🟢 KORREKT (Nach Audit bestätigt)

| Bereich | Status | Begründung |
|---------|--------|------------|
| Breakout Long/Short Filter | ✅ | 3%+ Change, 1.5+ RVOL, Close Position = sinnvoll |
| PM Trade Setups (Long) | ✅ | Breakout/VWAP PB/Retest mit echten Levels |
| PM Trade Setups (Short) | ✅ | Breakdown/VWAP Rejection/Retest logisch korrekt |
| Short TP Berechnung | ✅ | TP1/TP2 korrekt unter Entry |
| R-Value Display (abs) | ✅ | abs() für Short korrekt |
| classify_pm_setup 5%+ Block | ✅ | Position-aware Logik funktioniert |
| Wick Strategien | ✅ | Upper Wick + Change<3% = Short-Signal korrekt |
| Consolidation Breakout | ✅ | Multi-Day Pattern Detection funktioniert |
| Primary/Alt Setup Auswahl | ✅ | Position-basierte Auswahl korrekt |
| PDL als Short-Resistance | ✅ | PDL = ehemalige Unterstützung → wird Widerstand |
| Close Position None-Handling | ✅ | Filter wird übersprungen bei kleiner Range |
| Long/Short Trennung PM | ✅ | PM_Chg% > 0 = Long, < 0 = Short, exklusiv |

---

## PRIORITÄT für Fixes

1. 🔴 **BUG 1** (SQUEEZE dead code) — Einfacher Fix, verhindert verpasste Squeeze-Signale
2. 🔴 **BUG 2** (Mid-range gap) — Einfacher Fix, verhindert nutzlose "WATCH" Labels
3. 🔴 **BUG 3** (Reversal Setup trend-blind) — Gleicher Fix wie Reversal Hunter
4. 🔴 **BUG 4** (MA Distance phantom score) — Einfacher Fix
5. 🟡 **SCHWÄCHE 5** (Dip Buy RVOL) — RVOL 0.3 → 0.6
6. 🟡 **SCHWÄCHE 6** (Gap RVOL) — RVOL 0.5 → 1.0
7. 🟡 **SCHWÄCHE 7** (RSI inkonsistent) — Einheitlicher Multiplikator
8. 🟡 **SCHWÄCHE 8** (Whale Watch Churn) — Close Position Filter
9. 🟡 **SCHWÄCHE 9** (ATR Fallback) — Besserer Fallback
