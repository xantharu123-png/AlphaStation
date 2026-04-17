# BI-Scanner Audit V2 — Deep Dive

**Datum:** 2026-04-17
**Anlass:** User-Beobachtung — "Grade B/C Aktien kommen durch, obwohl die letzten 2 Kerzen bearish sind. Gefühlt kommt jede Aktie rein trotz vieler Indikatoren."
**Scope:** `analyze_breakout_imminent` in `modules/patterns.py` (5423 Zeilen, 20-Signal-Composite)

## Befund

Das User-Symptom war **reproduzierbar und korrekt**: `test_bearish_finisher.py` Case 1 (25 Bars Konsolidierung + 2 stark bearish Bars mit close UNTER Range-Low) wurde **vor dem Fix** als `is_valid=True, Grade=B, score=59/188` eingestuft — obwohl das Setup objektiv gebrochen war.

### Root Causes

**1. Kein Range-Breakdown-Filter.** Wenn `current_close < prior_20bar_low`, ist das Setup technisch gebrochen (aktiver Breakdown, kein "imminent breakout"). Der Scanner bewertete historische Muster (OBV, Dry-Up, Close-Clustering), aber nicht, dass der aktuelle Close bereits ausserhalb der Range lag.

**2. Kein Recent-Direction-Filter.** Zwei bearish Kerzen (close<open) am Ende wurden nicht als Hard-Gate gewertet. Das Setup konnte trotzdem durch historische Signale valid werden.

**3. `Close Position Clustering` (Signal 4) über 5 Bars gemittelt.** Bei 3 grünen + 2 roten Bars liegt der Mittelwert noch bullisch → +5 bis +10 Punkte vergeben, obwohl die letzten Bars rot waren. Die temporale Gewichtung fehlte.

**4. Grade C-Threshold zu nah an is_valid.** `is_valid >= 45`, `Grade C >= 47` (nur 2 Punkte Delta) — jedes valide Setup wurde automatisch mindestens Grade C. Grade C hatte zudem **keine Smart-Money-Bestätigungspflicht**. Konsequenz: Scanner-Output wirkte floral, weil "Grade C" quasi synonym zu "nicht komplett schlecht" war statt ein echtes Qualitätslevel.

**5. Threshold 45/188 = 23.9%.** Nach den frueheren Audit-Korrekturen war bewusst stark nach unten kalibriert worden, damit ueberhaupt Ergebnisse kamen. Das hat aber die Selektivitaet kaputt gemacht — zu viele historisch-gute-aber-aktuell-bearish Setups kommen durch.

## Implementierte Fixes

Alle in `modules/patterns.py`, am Ende von `analyze_breakout_imminent` (neben dem bestehenden V3.3 Pump-Filter):

### Fix 1: Range-Breakdown-Filter (LONG)
```
Wenn current_close < prior_20bar_low * 0.99 → is_valid=False
```
Toleranz 1% gegen Mikrofluktuation. Feuert, wenn Close wirklich ausserhalb der Range.

### Fix 2: Recent-Bearish-Filter (LONG)
```
Wenn letzte 2 Kerzen bearish (close<open)
UND close < 98% von 5-Bar-High
→ is_valid=False
```
Der Close-Check verhindert, dass ein leichter Zwischenpullback bei starker Range direkt filtert — nur wenn die Bewegung substanziell ist (>2% unter 5-Bar-High).

### Fix 3: Recent-Bullish-Filter (SHORT)
Spiegelbildlich: 2 bullish Kerzen + close > 102% von 5-Bar-Low → Short nicht imminent.

### Fix 4: Grade C schaerfer
```
Grade C: score >= 55 UND smart_money_hits >= 1
```
Statt vorher `score >= 47 ohne SM-Anforderung`. Das gibt Grade C wieder eine echte Qualitäts-Bedeutung (Score-Abstand zu is_valid wächst von 2 auf 10 Punkte, plus mindestens eine Smart-Money-Bestaetigung).

## Regression-Ergebnisse

Alle vier Test-Suiten grün nach den Fixes:

| Suite | Ergebnis |
|-------|----------|
| `test_breakout_audit.py` (Cases A–E, BI-Regression) | 6/6 |
| `test_setup_score.py` (Scorer-Regression) | 45/45 |
| `test_trading_logic.py` (Fees/MaxDD/Patterns) | 41/41 |
| `test_bearish_finisher.py` (NEU, User-Symptom) | 3/3 |

**Case D (Healthy Consolidation)** weiterhin is_valid=True — der Range-Breakdown-Filter feuert nur bei tatsächlichem Close unter Range-Low, nicht bei langsam steigenden Konsolidierungen.

**Case B (Real Breakout)** weiterhin Grade B — die letzte Kerze ist eine grüne Volume-Spike-Bar (uptick), nicht bearish.

## Impact-Abschaetzung

Die Fixes sollten die Scan-Treffer-Listen spuerbar reduzieren und die Qualitaet erhoehen:

- **Range-Breakdown-Filter**: schneidet alle "Breakdown-kaschiert-als-Breakout"-Faelle ab. Vermutlich ~5–15% der bisherigen Treffer.
- **Recent-Bearish-Filter**: schneidet "ueberfaellig aber aktuell in Abverkauf"-Setups ab. Vermutlich weitere 10–20%.
- **Grade C schaerfer**: bisher als Grade C markierte Setups ohne SM fallen auf Grade D zurueck. Grade C wird selektiver, aber bleibt valide als Watchlist.

Gesamt: **~25–35% weniger Treffer erwartet, mit deutlich hoeherer Qualitaet der verbliebenen Signale.**

## Was NICHT geaendert wurde (bewusst)

- **is_valid-Threshold 45 long / 40 short.** Nicht angehoben, weil die vorherigen Audit-Runden bewusst kalibriert haben, dass ueberhaupt Ergebnisse kommen. Die neuen Hard-Gates (Range-Breakdown + Recent-Bearish) sollten die Selektivitaet besser treffen als eine pauschale Threshold-Anhebung. Falls nach Live-Beobachtung immer noch zu viel durchkommt, ist 50 der naechste Schritt.

- **Close Position Clustering (Signal 4).** Die 5-Bar-Mittelung bleibt; die Hard-Gate-Filter am Ende adressieren das Recency-Problem. Signal 4 umzubauen würde das etablierte Score-Verhalten in Case B/D riskieren.

- **Smart-Money-Minimum fuer is_valid.** Nicht eingefuehrt, weil das is_valid-Gate moeglichst unabhaengig vom Grade-System bleiben soll. Die Grade-Filter wirken als Qualitaets-Layer darueber.

## Follow-up Ideen (nicht implementiert)

1. **Close Position zeitlich gewichtet**: letzte 2 Bars 2x, Bars -3 bis -5 1x. Feineres Signal statt hartes Gate.
2. **Explizite Volume-am-Rueckgang-Pruefung**: wenn rote Kerzen mit hoechstem Volume der Serie kommen = institutioneller Abverkauf → noch harter gaten.
3. **Confidence-Penalty statt is_valid=False** fuer Grenzfaelle: wenn Recent-Bearish knapp gemisstet wird, Confidence halbieren, damit UI-User es sieht.

## Code-Referenzen

- Fixes: `modules/patterns.py:1853-1908` (nach Pump-Filter)
- Grade-C-Change: `modules/patterns.py:1814-1815`
- Test: `test_bearish_finisher.py` (neu, 3 Cases)

## Abgrenzung zum V1-Audit

Das V1-Audit (commit 3e5ab6a, heute frueher) fokussierte auf Autotrader + Scorers. Dieses V2-Audit geht spezifisch tief in `analyze_breakout_imminent` nach User-Feedback ueber konkretes Output-Verhalten.
