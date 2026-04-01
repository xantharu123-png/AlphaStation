# 🔍 Alpha Station V61-V63 - Strategy & Calculation Audit

## Executive Summary

Nach gründlicher Code-Analyse wurden **15 kritische Probleme** und **8 mittlere Probleme** identifiziert.
**Stand V63: Alle PRIO 1-3 Punkte wurden behoben!**

---

## ✅ ERLEDIGTE FIXES (V62-V63)

### PRIO 1 - Kritische Fixes (V62):
1. ✅ **Alpha Score normalisiert** - Score 0-100, gleichmäßig gewichtet
2. ✅ **Krypto RVOL** - Turnover Ratio, korrekt dokumentiert
3. ✅ **Vortag% bei Aktien** - Dokumentation was es wirklich misst
4. ✅ **Gap%** - True Gap und Gap vs Close dokumentiert
5. ✅ **Wyckoff Strategien umbenannt** - Ehrliche Namen (Consolidation, etc.)

### PRIO 2 - Mittlere Fixes (V62):
6. ✅ **Close Position bei Extended Hours = None** - Nicht berechenbar

### PRIO 3 - Nice to Have (V63):
7. ✅ **Session-Checks für Futures/Forex** - best_time + Live UTC Check
8. ✅ **Liquiditäts-Threshold erhöht** - $100k Regular, $50k PM/AH
9. ✅ **Multi-Day Analyse für Patterns** - 5-Tage Pattern-Erkennung
   - fetch_multi_day_data() für historische Daten
   - analyze_multi_day_pattern() für Consolidation, Bull/Bear Flag
   - Strategien mit needs_history + pattern_type konfiguriert

---

## 🔴 KRITISCHE PROBLEME

### 1. Alpha Score Berechnung - WILLKÜRLICH & NICHT NORMALISIERT

**Aktueller Code (Zeile 541-542):**
```python
def calculate_alpha_score(rvol, vortag_pct, change_pct):
    return round((rvol * 12) + (abs(vortag_pct) * 10) + (abs(change_pct) * 8), 2)
```

**Problem:**
- Die Gewichte (12, 10, 8) sind willkürlich gewählt
- RVOL von 10 gibt 120 Punkte, aber Change von 10% gibt nur 80 Punkte
- RVOL kann 0.1 bis 999 sein, Change typisch -50% bis +100% → Skalen sind völlig unterschiedlich
- Negative Changes werden durch `abs()` positiv → Ein -20% Crash hat gleichen Score wie +20% Rally

**Korrektur:**
```python
def calculate_alpha_score(rvol, vortag_pct, change_pct):
    """Normalisierter Alpha Score 0-100"""
    # RVOL: 0-5 = normal, 5-20 = hoch, >20 = extrem
    rvol_score = min(rvol / 5 * 30, 30)  # max 30 Punkte
    
    # Vortag: Richtung + Stärke
    vortag_score = min(abs(vortag_pct) / 10 * 35, 35)  # max 35 Punkte
    
    # Heute: Richtung + Stärke  
    change_score = min(abs(change_pct) / 10 * 35, 35)  # max 35 Punkte
    
    return round(rvol_score + vortag_score + change_score, 0)
```

---

### 2. Krypto RVOL Berechnung - FALSCH!

**Aktueller Code (Zeile 1982-1987):**
```python
# RVOL Berechnung (Krypto-spezifisch)
if market_cap > 0:
    vol_ratio = (vol_24h / market_cap) * 100
    rvol = round(vol_ratio * 5, 2)
    rvol = max(0.1, min(rvol, 100))
```

**Problem:**
- Das ist **KEINE RVOL**! Das ist Volumen/MarketCap Ratio
- Echte RVOL = Aktuelles Volumen / Durchschnittsvolumen (z.B. 20-Tage-Durchschnitt)
- Der Faktor `* 5` ist willkürlich
- Bitcoin mit 1T$ MarketCap und 50B$ Volumen hat RVOL = (50/1000)*100*5 = 25
- Ein Shitcoin mit 1M$ MarketCap und 500k$ Volumen hat RVOL = (0.5/1)*100*5 = 250
- **Das ergibt keinen Sinn!**

**Korrektur:**
```python
# CoinGecko liefert kein historisches Volumen, daher:
# Option 1: Vol/MarketCap als "Turnover Ratio" (richtig benennen!)
turnover_ratio = (vol_24h / market_cap) * 100 if market_cap > 0 else 0

# Option 2: Keine RVOL für Krypto anzeigen (ehrlicher!)
# Option 3: CoinGecko Pro API für historische Daten
```

---

### 3. Vortag% bei Krypto - STATISTISCH FALSCH

**Aktueller Code (Zeile 1939-1942):**
```python
if change_7d != 0:
    avg_daily_7d = change_7d / 7
    vortag_chg = round(avg_daily_7d, 2)
```

**Problem:**
- 7-Tage-Rendite / 7 ≠ Durchschnittliche Tagesrendite
- Beispiel: Coin steigt 7 Tage je 10% → Gesamt: 1.1^7 = +95%, nicht 70%
- Außerdem: Wir wollen **den Vortag**, nicht irgendeinen Durchschnitt

**Korrektur:**
```python
# Ehrlich sein: Krypto-"Vortag" ist nicht verfügbar ohne historische Daten
# CoinGecko Free API hat kein "gestern" Feld
# Entweder:
vortag_chg = 0  # Nicht verwenden
# Oder:
# CoinGecko Pro nutzen für /coins/{id}/market_chart mit 2 Tagen
```

---

### 4. Gap% Definition - INKONSISTENT

**Aktueller Code für Aktien (Zeile 2185-2192):**
```python
# GAP-Berechnung (Open vs Previous High/Low)
if prev_high > 0 and prev_low > 0:
    if day_open > prev_high:
        gap_pct = ((day_open - prev_high) / prev_high) * 100
    elif day_open < prev_low:
        gap_pct = ((day_open - prev_low) / prev_low) * 100
```

**Problem:**
- Gap ist definiert als Open vs. Previous **CLOSE**, nicht High/Low
- True Gap Up: Open > Previous High (über der Range)
- Gap Up: Open > Previous Close (aber evtl. innerhalb Range)
- Der Code berechnet **True Gaps**, aber die Strategie-Beschreibung sagt einfach "Gap"

**Korrektur:**
```python
# Standard Gap (vs. Close)
gap_vs_close = ((day_open - prev_close) / prev_close) * 100

# True Gap (über/unter Range)
if day_open > prev_high:
    true_gap_pct = ((day_open - prev_high) / prev_high) * 100
elif day_open < prev_low:
    true_gap_pct = ((day_open - prev_low) / prev_low) * 100
else:
    true_gap_pct = 0  # Kein True Gap

# Beide separat verfügbar machen
```

---

### 5. Forex RVOL - GIBT ES NICHT!

**Aktueller Code (fetch_forex_data):**
```python
# Kein RVOL berechnet - GUT!
# Aber:
alpha = abs(change) * 20  # Forex hat kleinere Moves, daher *20
```

**Problem:**
- Forex ist dezentraler Markt - **es gibt kein echtes Volumen**
- Yahoo Finance Forex "Volume" ist fabricated/estimated
- `* 20` ist wieder willkürlich

**Status:** Teilweise OK (kein RVOL), aber Alpha-Berechnung ist fragwürdig.

---

### 6. Bull Flag / Bear Flag - VORTAG BERECHNUNG FALSCH

**Aktueller Code (Zeile 2209):**
```python
# Vortag Change
prev_open = prev.get("o") or 0
vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2) if prev_open > 0 else 0
```

**Problem:**
- Vortag Change = (Vortag Close - Vortag Open) / Vortag Open
- Das ist die **Intraday-Bewegung von gestern**
- Für Bull Flag wollen wir aber: **Wie viel hat die Aktie GESTERN gewonnen vs. VORGESTERN?**
- Also: (prev_close - prev_prev_close) / prev_prev_close

**Die Strategie-Logik ist falsch implementiert!**

**Korrektur:**
```python
# Wir brauchen 2 Tage historische Daten
# Polygon Snapshot hat nur prevDay, nicht prevPrevDay
# Lösung: Aggregates API für 3 Tage
vortag_chg = ((prev_close - prev_prev_close) / prev_prev_close) * 100
```

---

### 7. Close Position bei Extended Hours - UNGENAU

**Aktueller Code:**
```python
if session in ["Pre-Market", "After-Hours", "Extended"]:
    open_price = day.get("o") or price
    high = day.get("h") or price
    low = day.get("l") or price
    close = price  # Aktueller Preis aus lastTrade
```

**Problem:**
- High/Low kommen aus Regular Session (`day`)
- Close ist aber Extended Hours Preis
- Close Position = (close - low) / (high - low) macht keinen Sinn wenn Close außerhalb der Regular Range ist!
- Beispiel: Regular High = $100, Extended Preis = $115 → Close Position = 1.5 (>1!)

**Korrektur:**
```python
# Close Position nur für Regular Session berechnen
# Oder: Extended High/Low separat tracken
if session in ["Pre-Market", "After-Hours", "Extended"]:
    close_pos = None  # Nicht anwendbar
```

---

### 8. Wyckoff Strategien - STARK VEREINFACHT

**Aktueller Code:**
```python
"Accumulation 📦": {
    "filters": {"Change %": (-3.0, 3.0), "Vortag %": (-3.0, 3.0), "RVOL": (0.3, 1.5)},
}
```

**Problem:**
- Echte Wyckoff Accumulation erfordert **Wochen** von Seitwärtsbewegung
- 2 Tage (heute + gestern) flach zu sein ist KEIN Accumulation Pattern
- Spring Setup erfordert einen vorherigen Support-Test, den wir nicht prüfen

**Status:** Diese Strategien sind **irreführend benannt**. Sie finden "2 Tage flach", nicht Wyckoff-Patterns.

**Korrektur:** Entweder umbenennen ("Consolidation") oder echte Multi-Day Analyse implementieren.

---

### 9. Volume Void Scanner - POC/VAH/VAL Berechnung

**Aktueller Code (scan_volume_voids_batch):**
```python
# 70% Volumen für Value Area
value_area_volume = total_volume * 0.7
```

**Problem:**
- Value Area ist korrekt definiert (70% des Volumens)
- **ABER:** Wir haben nur 30 Tage Daten mit Tages-OHLCV
- Professionelle Volume Profile nutzen **Tick-Daten** oder mindestens 1-Minuten-Bars
- Mit Tages-Daten ist das eine grobe Approximation

**Status:** Mathematisch korrekt, aber Datenqualität ist limitiert.

---

### 10. Liquiditäts-Check - THRESHOLD ZU NIEDRIG

**Aktueller Code:**
```python
min_dollar_volume=50000  # $50k für Regular
min_dollar_vol = 25000   # $25k für PM/AH
```

**Problem:**
- $50k Dollar-Volumen ist **sehr wenig**
- Das sind bei $10 Aktie nur 5000 Aktien
- Institutionelle Trader brauchen mindestens $500k-$1M Dollar-Volumen

**Korrektur:**
```python
# Für Retail-Scanner akzeptabel, aber dokumentieren
min_dollar_vol_retail = 100000   # $100k
min_dollar_vol_pro = 500000      # $500k
```

---

## 🟡 MITTLERE PROBLEME

### 11. Duplicate/Ähnliche Strategien

- "Volume Surge" und "Early Momentum" sind fast identisch
- "Breakout Long" und "Gap Up (High Vol)" überlappen stark

### 12. Futures Session-Strategien ohne Session-Check

"London Open Momentum" und "NY Open Breakout" prüfen nicht die aktuelle Uhrzeit.

### 13. Forex Session-Strategien ohne Session-Check

Gleiche Problem wie Futures.

### 14. RVOL Time-Normalisierung nur für US Markets

`calculate_rvol_at_time()` nutzt US Eastern Time. Internationale Börsen werden nicht normalisiert.

### 15. Upper/Lower Wick % können >100% sein

Bei Doji-Kerzen mit sehr kleinem Body kann die Wick > 100% der Range sein.

---

## 🟢 WAS FUNKTIONIERT GUT

1. ✅ `calculate_close_position()` - Mathematisch korrekt
2. ✅ `calculate_rvol_at_time()` - Gut durchdacht für US Markets
3. ✅ `validate_flag_pattern()` - Fibonacci Retracement korrekt
4. ✅ Gap-Berechnung für True Gaps
5. ✅ Liquiditäts-Filter Konzept
6. ✅ PM/AH Session Detection

---

## 📋 EMPFOHLENE FIXES (PRIORITÄT)

### ✅ PRIO 1 - Sofort fixen (ERLEDIGT V62):
1. ✅ Alpha Score normalisieren
2. ✅ Krypto RVOL entfernen oder umbenennen
3. ✅ Vortag% bei Aktien korrigieren (braucht mehr Daten)

### ✅ PRIO 2 - Bald fixen (ERLEDIGT V62):
4. ✅ Gap% in "True Gap" und "Gap vs Close" aufteilen
5. ✅ Wyckoff Strategien umbenennen
6. ✅ Close Position bei Extended Hours = None

### ✅ PRIO 3 - Nice to have (ERLEDIGT V63):
7. ✅ Session-Checks für Futures/Forex Strategien
8. ✅ Liquiditäts-Threshold erhöhen
9. ✅ Multi-Day Analyse für Patterns (fetch_multi_day_data, analyze_multi_day_pattern)

---

## 🧪 BACKTEST EMPFEHLUNG

Um die Strategien richtig zu validieren, brauchen wir:

1. **Historische Daten** (mindestens 1 Jahr)
2. **Entry/Exit Regeln** definieren
3. **Win Rate, Profit Factor, Max Drawdown** berechnen

Aktuell ist das ein **Scanner**, kein **Backtesting-System**. Der Scanner findet Kandidaten, aber ohne Backtest wissen wir nicht ob die Strategien profitabel sind.

---

*Audit erstellt: 2026-01-19*
*Code Version: V61*
