# Biotech-Scanner Audit V3 — Deep Dive

**Datum:** 2026-04-17
**Anlass:** User-Frage nach BI-Audit V2 — "wie sieht es mit dem bioscanner aus?"
**Scope:** `_biotech_background_scan` und Helfer in `modules/scanners.py` (~600 LOC)
sowie `_calculate_biotech_catalyst_score` in `modules/data_fetchers.py`

## Befund

Der Biotech-Scanner litt an einem ähnlichen Symptom wie der BI-Scanner vor V2:
**zu viele schwache Kandidaten kommen als Grade C / B durch.** Die Root-Causes
sind aber andere — und einer davon ist ein semantischer Bug, der saubere Trades
fälschlich abstrafte.

### Root Causes

**1. `complete response` als Negativ-Catalyst klassifiziert (semantischer Bug).**
In `BIOTECH_NEGATIVE_CATALYSTS` (scanners.py Z. 95) stand `"complete response": -20`.
Im FDA-Kontext ist *"Complete Response Letter (CRL)"* eine Ablehnung — *"complete
response"* alleine in Trial-News bezeichnet aber die *beste* Outcome-Kategorie
(Patient zeigt vollständige Remission). Konsequenz: Onko-News mit Trial-Erfolg
wurden als Negativ-Flag gewertet → -20 Punkte → Aktie fiel raus oder bekam
schlechtes Grade. Gleichzeitig stand `"complete response"` korrekt in
`_positive_result_kws` (Z. 2592) und `"complete response letter"` in
`_negative_result_kws` (Z. 2596) — die Negativ-Dictionary war also
inkonsistent mit dem Result-Sentiment-Code.

**2. Grade-Threshold C = 35 = min_required (mit Catalyst sogar 20).** Identisches
Problem wie beim BI-Scanner: jede valide Zeile war automatisch mindestens Grade C.
Konkret war:
- `min_required = 20` mit Catalyst → triviale Schwelle (Tier-1-Catalyst alleine
  bringt 30*2/160*100 ≈ 37 Punkte)
- `min_required = 35` ohne Catalyst → Grade C startete bei genau diesem Wert
- Grade C hatte keinerlei zusätzliches Signal-Requirement

**3. `chart_health` (0-10 Skala) wurde berechnet, aber NICHT in den Score
eingebracht.** Im Code-Kommentar (Z. 2076–2078) steht explizit:
> CHART HEALTH (separate Metrik, beeinflusst Score NICHT)
> Gibt dem Trader eine schnelle Einschätzung ob der Chart tradebar ist,
> ohne gute Catalyst-Trades zu verstecken.

Die Absicht war legitim (gute Catalysts nicht durch chart-Filter killen), aber
das Resultat war: Aktien mit Chart-Health 4/10 (=Kritisch) kamen als Grade B
durch, weil der Catalyst-Score den Rest dominierte. Das war exakt die Klasse
von Output, über die der User sich beschwert hat.

**4. Kein Recent-Bearish Hard-Gate.** Analog zum BI-Scanner V2 fehlte hier ein
Filter, der den aktuellen Trend (letzte 2 Kerzen) als Hart-Stopper bewertet.

## Implementierte Fixes

Alle in `modules/scanners.py`:

### Fix 1: `complete response` semantisch korrigieren
```diff
- "complete response": -20,
+ "complete response letter": -20,
+ "crl issued": -20,
```
Trial-News mit "complete response" werden nicht mehr fälschlich als Ablehnung
gewertet. CRL bleibt klar als Negativ-Flag, weil das eindeutig FDA-Ablehnung ist.

### Fix 2: `chart_health`-Penalty in den Score einbauen
Direkt nach der Score-Finalisierung, vor `min_required`-Gate:
```python
if _chart_health_val <= 4:
    total_score = max(0, total_score - 15)  # Kritisch
elif _chart_health_val <= 6:
    total_score = max(0, total_score - 8)   # Schwach
```
Begründung: Selbst der beste Catalyst rechtfertigt keinen Trade, wenn der Chart
gerade aktiv abverkauft wird. Die Penalty ist gestaffelt, damit gute Setups mit
nur leicht angeschlagenem Chart noch durchkommen können (mit niedrigerem Grade).

### Fix 3: Recent-Bearish Hard-Gate
```python
if ("4/5 rote Tage" in _recent_action or "5/5 rote Tage" in _recent_action) \
        and _bearish_patterns:
    total_score = max(0, total_score - 10)
```
Nutzt die schon vorhandenen Felder aus `_biotech_technical_score`
(`recent_action` zählt rote Tage, `candle_patterns` enthält bearish Pattern).
Doppelte Bestätigung (Counting + Pattern) verhindert False-Positives bei
einzelnen Outlier-Tagen.

### Fix 4: `min_required` anheben
```diff
- min_required = 20  (mit Catalyst)
- min_required = 35  (ohne Catalyst)
+ min_required = 35  (mit Catalyst oder Readout)
+ min_required = 45  (ohne Catalyst)
```
Der alte 20er-Threshold ließ jeden Tier-1-Catalyst-Stock durch, auch wenn
Technik/Risk komplett schlecht waren. Neu: Catalyst + irgendeine Bestätigung.

### Fix 5: Grade C braucht Signal, B-Threshold höher
```python
if total_score >= 75:
    grade = "A"
elif total_score >= 62:                      # war: 55
    grade = "B"
elif total_score >= 45 and (_has_cat_signal or _has_tech_signal):  # war: 35 ohne Bedingung
    grade = "C"
else:
    grade = "D"
```
Grade-C verlangt jetzt eines der beiden: Catalyst-Signal (catalyst_score>0
oder Readout) oder echtes Tech-Signal (technical_score>=8). Reine momentum-
oder pipeline-getriebene Setups ohne Catalyst und ohne Volume/Trend fallen
zurück auf D.

## Regression-Ergebnisse

| Suite | Vorher | Nachher |
|-------|--------|---------|
| `test_breakout_audit.py` (BI-Scanner Cases A-E) | 6/6 | 6/6 |
| `test_bearish_finisher.py` (BI-Scanner Bearish-Filter) | 3/3 | 3/3 |
| `test_setup_score.py` (Scorer-Regression) | 45/45 | 45/45 |
| `test_trading_logic.py` (Patterns + Fees + MaxDD) | 41/41 | 41/41 |
| `test_biotech_audit.py` (NEU) | — | 5/5 |

Die neue Biotech-Test-Suite deckt ab:
1. NEGATIVE_CATALYSTS Semantik (complete response NICHT drin, complete response letter drin)
2. Grade-Threshold-Logik (12 Cases mit Edge-Werten)
3. Chart-Health-Penalty (7 Cases inkl. Floor-bei-0)
4. min_required-Logik (7 Cases)
5. End-to-End-Pipeline (6 Cases die alle 4 Fixes verketten)

## Impact-Abschätzung

Die Fixes wirken in beide Richtungen — manche Treffer fallen weg, manche kommen
zurück, die Treffer-Liste wird selektiver:

- **Fix 1 (complete response)**: ~1-3% mehr legitime Trial-Erfolg-Aktien kommen
  durch (vorher fälschlich -20 Punkte).
- **Fix 2 (chart_health Penalty)**: ~10-20% weniger Treffer mit kaputtem Chart;
  die verbliebenen sind tradeable. Diese Klasse war exakt der User-Pain-Point.
- **Fix 3 (Recent-Bearish)**: ~5-10% weniger "alle letzten Tage rot"-Setups.
- **Fix 4 (min_required hoch)**: ~15-25% weniger Grade-D/C-Treffer mit
  marginalem Score.
- **Fix 5 (Grade C strenger)**: bisherige Grade-C-Aktien ohne Signal werden
  jetzt als D gefiltert (die D-Liste ist eh meist hidden).

**Gesamt:** ~25-40% weniger Treffer in der Biotech-Liste, mit deutlich höherem
Median-Grade und klar besser tradebaren Charts.

## Was NICHT geändert wurde (bewusst)

- **Catalyst-Gewichtung 2x in `_calculate_biotech_catalyst_score`.** Der Catalyst
  ist legitim das primäre Signal in Biotech (FDA-Approval > Volume-Pattern). Die
  Gewichtung bleibt; der Filter wirkt jetzt auf den fertigen Score.
- **Penny/Micro-Stock Warnings.** Die laufen als Display-Marker, nicht als Filter.
  Wer Penny-Biotechs explizit will (Catalyst-Squeeze-Trades), kann sie nehmen.
- **`_biotech_risk_score`.** Wirkt korrekt gegen Tail-Risiken (Dilution,
  Going Concern). Keine Änderung nötig.

## Follow-up Ideen (nicht implementiert)

1. **Polygon-Sentiment integrieren statt nur Keyword-Matching.** Aktuell wird
   per Keyword in News-Titeln gesucht, das verpasst nuancierte Formulierungen
   ("met endpoint with 22% improvement").
2. **CT.gov-Phase-Übergänge tracken.** Phase-2 → Phase-3 Übergang ist ein
   starkes Signal, das aktuell nicht explizit gewertet wird.
3. **Sektorspezifische RVOL-Thresholds.** Biotech hat naturgemäß höhere
   Volatilität; ein RVOL von 3x ist hier weniger ungewöhnlich als bei Large-Cap.

## Code-Referenzen

- Fix 1 (Negative Catalysts): `modules/scanners.py:95-113`
- Fix 2-5 (Score-Pipeline + Grade): `modules/scanners.py:2452-2510`
- Test-Suite: `test_biotech_audit.py` (5 Test-Funktionen)

## Abgrenzung zu BI-Audit V2

V2 (`AUDIT_REPORT_BI_V2.md`) fokussierte auf den Aktien-BI-Scanner
(`analyze_breakout_imminent`). V3 ist parallel dazu im Biotech-Scanner. Die
Probleme reimen sich strukturell (Grade-C zu lasch, kein Chart/Recent-Direction-
Filter), aber der Code-Ort ist anders.
