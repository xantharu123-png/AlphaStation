# CONTINUATION PROMPT — Alpha Station Volume Profile & Earnings Integration
# =========================================================================
# Datum: 26. Februar 2026
# Projekt: Alpha Station Scanner (scanner.py + volume_profile.py)
# Plattform: Streamlit, gehostet auf Streamlit Cloud via GitHub
# APIs: Polygon.io (Aktien-Daten), Finnhub (Earnings Calendar, Insider)
# =========================================================================

## KONTEXT

Ich arbeite an meinem Trading-Scanner "Alpha Station" — einer Streamlit-App (scanner.py, ~18'000 Zeilen) die Aktien, Krypto, Forex und Futures scannt. Die App hat verschiedene Strategien (Breakout Long, MA Bounce, Whale Watch, Gap Up, Dip Buy, etc.) und bewertet Aktien mit einem "SetupScore" (0-100).

In den letzten Sessions haben wir zwei grosse Features eingebaut:
1. **Volume Profile Engine V1.1** (volume_profile.py — separates Modul)
2. **Earnings Warning System** (in scanner.py integriert)

---

## 1. VOLUME PROFILE ENGINE V1.1 (volume_profile.py)

### Was es macht
Berechnet Volume Profile aus Daily OHLCV Bars (von Polygon API) ohne Intraday-Daten. Generiert POC (Point of Control), Value Area, HVN (High Volume Nodes), LVN (Low Volume Nodes) und Trading-Signals mit Score-Adjustment (-10 bis +10) auf den SetupScore.

### Kernfunktionen
- `calculate_volume_profile(ohlcv_data, lookback_days, num_bins, atr_value)` — Hauptberechnung
- `analyze_vp_signals(vp, current_price, atr, direction, strategy_type)` — Signal-Analyse
- `get_vp_lookback_for_strategy(strategy_name)` — Lookback: 200d für SMA200, 120d für SMA50, 60d für EMA21
- `get_strategy_type_for_scanner(strategy_name)` — Mappt Strategienamen auf "bounce"/"breakout"/"default"
- `format_vp_for_display(vp, current_price, direction, strategy_type)` — Display-String

### V1.1 Audit-Fixes (kritisch!)
Diese Fixes wurden nach einem 7-Punkte-Audit eingebaut:

**Fix 1: Close-Weighted Volume Distribution**
- V1.0 verteilte Volume gleichmässig über die Bar-Range → POC lag immer am Midpoint
- V1.1 nutzt Dreieck-Verteilung mit Peak am Close-Bin: `weight = 1.0 / (1.0 + abs(bin - close_bin))`
- Ergebnis: POC liegt jetzt nahe am Close-Preis (realistisch, MOC Orders, Closing Auctions)

**Fix 2: Strategy-Type-Aware Scoring**
- KRITISCHER BUG in V1.0: Scoring war blind für Strategie-Typ
- Beispiel: SMA200 Bounce Long bei $46 mit POC bei $50 → V1.0 gab PENALTY (-2) obwohl das ein perfekter Bounce-Entry ist
- V1.1: Drei Modi:
  - `"bounce"`: Unter POC = +3 (Entry-Zone), unter VA Low = +4 (Dip-Buy), am POC = +3
  - `"breakout"`: Über POC = +4 (Bestätigung), über VA = +3, unter POC = -4 (Failed)
  - `"default"`: Generisch, backward-compatible

**Fix 3: HVN Smoothing + Clustering**
- V1.0 fand fragmentierte HVN (2-3 Peaks im selben Cluster)
- V1.1: 3-Bin Rolling Average, Threshold 40% POC (statt 50%), Merge HVN innerhalb 3 Bins

**Fix 4: HVN Bounce Interpretation**
- HVN knapp über Entry-Preis bei Bounce = "Volume-Akzeptanz" (positiv), nicht Resistance

**Fix 5: LVN Differenzierung**
- Bounce in LVN = -2 (kein Volume-Support, gefährlich)
- Breakout in LVN = +2 (wenig Widerstand, Beschleunigung)

### Strategy Mapping (get_strategy_type_for_scanner)
Keywords für bounce: BOUNCE, DIP, PULLBACK, MEAN REVERSION, FLAG, REVERSAL
Keywords für breakout: BREAKOUT, BREAKDOWN, MOMENTUM, SURGE, GAP, WHALE, PENNY, ROCKET, GAINER, LOSER
Alles andere: default

### Short-Logik
- Short Bounce: Preis OBEN = gut (Short-Entry am Widerstand), Preis UNTEN = schlecht (verpasst)
- Short Breakout: Preis UNTEN = gut (bearish Breakout bestätigt), Preis OBEN = schlecht

---

## 2. SCANNER INTEGRATION (scanner.py)

### Import-Aliase (KRITISCH!)
scanner.py hat bereits eine ALTE `calculate_volume_profile()` Funktion bei Zeile ~5077 (für Chart-Analyse, 20 Bins, ohne Close-Weighting). Um Name-Clash zu vermeiden, nutzen wir Import-Aliase:

```python
from volume_profile import (
    calculate_volume_profile as vp_calculate_profile,
    analyze_vp_signals as vp_analyze_signals,
    get_vp_lookback_for_strategy,
    get_strategy_type_for_scanner
)
VP_AVAILABLE = True
```

Die alte `calculate_volume_profile()` (Zeile 5077) bleibt für die Chart-Analyse bestehen. Die Aufrufe bei Zeile 5354 und 8918 nutzen die alte Version. ALLES nach Zeile 15000 nutzt die neuen aliased Versionen.

### VP Integration — Zwei Pipelines

**Pipeline 1: MA Bounce (Zeile ~15087)**
- VP wird direkt im MA-Berechnungsloop berechnet
- Kein Extra-API-Call: `fetch_historical_closes(ticker, poly_key, days=vp_lookback, return_ohlcv=True)` gibt OHLCV-Bars aus dem gleichen Polygon-Request zurück
- `return_ohlcv=True` Parameter wurde zu `fetch_historical_closes()` hinzugefügt (backward-compatible, default=False)

**Pipeline 2: Standard-Pipeline K3 (Zeile ~15588)**
- VP Enrichment für Top 30 Ergebnisse NACH dem Standard-Scan
- Betrifft 17 Strategien: Breakout Long/Short, Early Momentum, Whale Watch, Volume Surge, Gap Up/Down, Dip Buy, Reversal Hunter, Bull/Bear Flag, Penny Rockets, PM Gap & Go, PM Gainers, Ultra-Strategien
- Definiert in `VP_ENRICHMENT_STRATEGIES` Set
- Rate-Limited: 0.3s Pause alle 10 Stocks
- Re-sort nach VP-adjustiertem SetupScore

### VP Display (Zeile ~16163)
- Generisch: Zeigt VP für ALLE Ergebnisse die `VP_Summary` haben
- Zeigt: VP Summary String, einzelne Signal-Texte, Score-Adjustment (grün/rot)
- TradingView-Tipp wird nur noch angezeigt wenn VP NICHT verfügbar

---

## 3. EARNINGS WARNING SYSTEM

### Auslöser
Ich habe Geld verloren weil der Scanner mich nicht gewarnt hat dass eine Aktie (RHLD) abends Earnings hatte. Der Scanner zeigte "Breakout HOLD" ohne Earnings-Warnung.

### Funktionen (ab Zeile ~9973)

**`fetch_earnings_calendar(finnhub_key, days_ahead=7)`**
- 1 API-Call zu Finnhub `/calendar/earnings` für ALLE Earnings der nächsten 7 Tage
- Cached in `st.session_state["_earnings_cache"]` für 30 Minuten
- Returns: Dict `{ticker: {date, hour (bmo/amc/dmh), epsEstimate, revenueEstimate, quarter, year}}`

**`check_earnings_proximity(ticker, earnings_calendar)`**
- Prüft ob Ticker bald Earnings hat
- Levels und Penalties:
  - `YESTERDAY_AMC`: Earnings gestern nach Börsenschluss → -10 (Gap-Risiko heute)
  - `TODAY_BMO`: Earnings heute vor Eröffnung → -15
  - `TODAY_AMC`: Earnings heute nach Börsenschluss → -15 (⛔ NICHT KAUFEN!)
  - `TODAY`: Earnings heute (Uhrzeit unbekannt) → -15
  - `TOMORROW`: Earnings morgen → -10
  - `THIS_WEEK`: Earnings in 2-5 Tagen → -5
  - `NEXT_WEEK`: Earnings in 6-7 Tagen → 0 (nur Info)

### Integration — 3 Stufen (JEDE Strategie abgedeckt!)

**Stufe 1 (Zeile ~15332): MA Bounce Pipeline**
- Earnings-Check direkt nach MA-Berechnung

**Stufe 2 (Zeile ~15729): K4 Standard-Pipeline**
- Earnings-Check nach VP Enrichment (K3)
- Für: Breakout, Gap, Whale, Momentum, etc.

**Stufe 3 (Zeile ~15878): Universal Fallback**
- Prüft `if "EarningsWarning" not in df.columns` → fängt ALLES ab
- Für: Volume Void, Harmonic, Wyckoff, Insider, und jede andere Pipeline
- Wird direkt nach DataFrame-Erstellung ausgeführt

### Anzeige

**Tabelle:** Separate "ER" Spalte nach Ticker
- `⛔ER` = Heute (rot)
- `⚠️ER` = Morgen (gelb)
- `📅ER` = Diese Woche (blau)
- Spalte erscheint NUR wenn mindestens 1 Earnings-Treffer

**Detail-View (Zeile ~16178):** Ganz oben, VOR allem anderen
- TODAY AMC: Rote Box mit "⛔ NICHT KAUFEN! Earnings heute nach Börsenschluss — massiver Gap-Risk morgen. Position VOR Close schliessen oder absichern!"
- TODAY BMO: "🚨 VORSICHT! Earnings heute vor Eröffnung"
- YESTERDAY AMC: "🚨 ACHTUNG! Earnings gestern AMC — heutiger Preis enthält Earnings-Reaktion"
- TOMORROW: Gelbe Warnung mit "Position-Sizing reduzieren"
- THIS_WEEK: Info-Box mit "Haltezeit berücksichtigen"
- NEXT_WEEK: Nur Caption

---

## 4. AUDIT-ERGEBNISSE

Letztes Audit: **86/89 PASS, 0 echte Bugs**

### Mathematik ✅
- Volume Conservation: Ratio 1.0000
- POC = Max-Volume-Bin
- Close-Weighted: POC näher am Close als Midpoint
- VA enthält 68-85% Volume
- VA Low ≤ POC ≤ VA High
- Bins monoton steigend
- Bimodal: HVN + LVN korrekt erkannt

### Logik ✅ (18/18 Szenarien)
- Long Bounce: unter VA (+5), unter POC (+5), am POC (+5), über POC (+7), über VA (+1)
- Long Breakout: über VA (+7), über POC (+4), unter POC (-1), unter VA (-5)
- Short Bounce: über VA (+7), über POC (+2), am POC (+5), unter VA (-6)
- Short Breakout: unter VA (+8), unter POC (+3), über VA (-1)

### Trader-Praxis ✅
- Bounce Pullback: Score positiv (+5)
- Echter Breakout (Konsolidierung → Ausbruch): Score +9
- Failed Breakout: Score -6
- Dip Buy unter VA Low: Score +9
- Short Bounce oben: Score +7
- Gap über VA: Score +9

### 3 "Fails" die keine Bugs sind:
1. A9: Flat Range VA 31% — bei identischen Bars ($50) ist VA korrekt schmal
2. C1: RHLD Testdaten-Limitation — Testdaten erzeugen POC bei $192 ≈ Preis
3. F4: LVN Bounce vs Breakout — POC-Position dominiert korrekt über LVN

---

## 5. BEKANNTE ARCHITEKTUR-DETAILS

### Datei-Struktur
```
projekt-ordner/
├── scanner.py          (~18'000 Zeilen, Streamlit App)
├── volume_profile.py   (VP Engine V1.1, ~473 Zeilen)
```

### API Keys (in Streamlit Secrets)
- `POLYGON_KEY` — Polygon.io (Aktien, OHLCV)
- `FINNHUB_KEY` — Finnhub (Insider, Earnings Calendar)
- `ALPACA_KEY` / `ALPACA_SECRET` — Alpaca (Realtime Preise)

### Wichtige Zeilen-Referenzen in scanner.py
- ~38: VP Import mit Aliase
- ~192-545: STRATEGIES Dict (alle Strategien)
- ~4167: fetch_historical_closes() mit return_ohlcv Parameter
- ~5077: ALTE calculate_volume_profile() (Chart-Analyse, NICHT ÄNDERN)
- ~9973: fetch_earnings_calendar()
- ~10029: check_earnings_proximity()
- ~10200: fetch_stock_data() (Standard Aktien-Scan)
- ~15087: MA Bounce VP Integration
- ~15314: MA Bounce Earnings Check
- ~15588: K3 VP Enrichment (Standard-Pipeline)
- ~15729: K4 Earnings Enrichment (Standard-Pipeline)
- ~15878: Universal Earnings Fallback
- ~16163: VP Display in Detail-View
- ~16178: Earnings Display in Detail-View
- ~16725: TradingView Tipp (konditionell)

### Rate Limiting
- scanner.py hat eigene `rate_limited_get()` Funktion
- VP Enrichment: 0.3s Pause alle 10 Stocks
- Earnings Calendar: 30min Cache in session_state
- Polygon API hat Rate Limits (5 Calls/Min für Free, 100 für Paid)

---

## 6. WAS NOCH OFFEN IST / NÄCHSTE SCHRITTE

- VP in der Ergebnis-Tabelle als Spalte anzeigen (z.B. "VP: +7" oder "VP: -4")
- Earnings-Daten auch für Krypto prüfen (Finnhub hat auch Krypto-Events)
- VP Mini-Chart im Detail-View (horizontales Balkendiagramm)
- Backtest: VP-Signal als zusätzlichen Filter im Backtest-System einbauen
- Performance-Optimierung: VP-Berechnung parallelisieren (concurrent.futures)

---

## 7. GIT BEFEHLE

```bash
cd /path/to/project
git add scanner.py volume_profile.py
git commit -m "VP alle Pipelines + Earnings Warning System

- VP V1.1: Close-Weighted, Strategy-Aware, HVN Smoothing
- VP in Standard-Pipeline (17 Strategien) + MA Bounce
- Earnings: Finnhub Calendar, 30min Cache
- ⛔ TODAY: -15pts, ⚠️ TOMORROW: -10pts, 📅 WEEK: -5pts
- Universal Fallback: ALLE Strategien haben Earnings-Check
- Audit: 86/89 PASS"
git push origin main
```
