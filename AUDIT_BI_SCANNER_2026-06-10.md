# Tiefen-Audit: BI-Scanner (bi_long / bi_short) — 10.06.2026

> **STATUS-UPDATE (gleicher Tag): ALLE Befunde gefixt, 602/602 Tests grün (45 neue).** K-2: MACD-Hist-Serie (Sweep 15-90 Bars crashfrei, Signal 13 lebt, Autotrader entsperrt). K-1: bg ruft jetzt den echten `_bi_background_scan` (Phantom-Import + 199-Zeilen-Falsch-Fallback gelöscht; Cache-E2E: 2 Kandidaten ⇒ 2 results statt 0). H-1: Alt-Grade-Leiter raus — Fuzz-Top-50 jetzt S:6/A:17 statt 0,0%; ShortBonus separat. H-2: Long-Extension-Gate (>5%-Entries: 22%→1%), adaptives Range-Fenster, Short-Gate lebendig + TP1/TP2-Absicherung, kumulativer 2-Tages-Pump-Filter (100/100 abgefangen). M-1: Partial-Bar raus aus der Analyse. M-2: OBV+InstAcc deduped (≥2-fires auf Nur-Volumen: 74%→35%), OB/Liq-hits nur mit 3%-Nähe. M-3: Distribution-Malus (Durchrutscher 84%→69%). N: max_score=183 korrigiert, Cache-Frische durchgesetzt (2h-Mail-Gate). Geometrie-Fuzz: 0 Verletzungen in 6.666+ Fällen. Harnesses 6/6 + 3/3 unverändert. Commit: siehe git log.

**Stand:** HEAD `c6d542c` · Read-only, 10 Verifikationsskripte (/tmp/bi_audit/), End-to-End-Fuzz 3.884 Long + 3.387 Short durch den echten Scan-Code. Die 2 KRITISCH-Befunde zusätzlich vom Chef-Dev eigenhändig reproduziert.

## Architektur-Befund vorweg
`analyze_breakout_imminent` (patterns.py, 20 Komponenten, max real 173 Punkte) hat DREI Konsumenten mit unterschiedlichen Fenstern, Schwellen und Level-Modellen: api/scanners (30 Bars, Schwelle 45/40, Struktur-Level), bg-Fallback (120/320 Tage, Schwelle 85/75!, S/R-Level), Autotrader (50 Bars). Das ist die Wurzel beider KRITISCH-Befunde.

## KRITISCH

### K-1 · Der Produktions-Scan-Owner (bg) schreibt stündlich einen LEEREN BI-Cache — der Hauptscanner ist live faktisch tot
Kette: bg_service.py:1647 importiert `_bi_background_scan_standalone` aus modules.scanners — **die Funktion existiert nirgends** (eigenhändig verifiziert: 0 Treffer) → ImportError → Fallback `_run_bi_analysis_direct` → schickt volle 120/320-Tage-Historie in die Analyse → **MACD-TypeError bei JEDER Aktie mit ≥35 Bars** (K-2) → per-Ticker-except schluckt → `results=[]` → Cache überschrieben. bg ist seit der Scan-Ownership-Aufteilung der Owner von bi_long/bi_short und läuft stündlich 10-16 ET — **er überschreibt die guten api-Scheduler-Ergebnisse (180-Min-Takt) binnen einer Stunde mit leeren.** UI-Tab leer, bg-Mails unmöglich, Tracker bekommt nichts. Selbst ohne Crash wäre der Fallback fachlich falsch (Schwelle 85/75 statt 45/40, anderes Level-Modell, Intraday-RVOL ohne Normalisierung, kein Geometrie-Gate).
**Sofortmaßnahme ohne Code:** `BG_SCAN_SET` ohne bi_long/bi_short setzen (api-Scheduler übernimmt allein). **Richtiger Fix:** bg auf `modules.scanners._bi_background_scan` umstellen, `_run_bi_analysis_direct` löschen.

### K-2 · MACD-Komponente (Signal 13) ist in JEDEM Pfad defekt — API-Vertragsbruch
indicators.calculate_macd liefert SKALARE `(macd, signal, hist)`; patterns.py:1367f behandelt `hist` als LISTE (`len(hist)>=3`, `hist[-1]-hist[-3]`). Bei ≤34 Bars (api-Pfad nutzt exakt 30): calculate_macd → (None,None,None) → Signal 13 ist **toter Code, 10 Punkte nie vergebbar**. Bei ≥35 Bars: **TypeError-Crash** (eigenhändig reproduziert: "object of type 'float' has no len()") → bg-Leercache (K-1) und **Autotrader (50 Bars) crasht pro Ticker → findet nie ein Signal.**

## HOCH

### H-1 · Drei konkurrierende Grade-Systeme — die dokumentierte Leiter ist tot, das Mail-Gate unerreichbar kalibriert
Die V3.3/V4-Leiter (S=85+4f, A=71+3f, B=57+2h, C=55+1h — unsere dokumentierte Konvention) wird in scanners.py:1521 und bg_service.py:1797 mit einer Alt-Leiter (113/99/85/75) ÜBERSCHRIEBEN, die patterns.py selbst als "praktisch unerreichbar" bezeichnet. **Laufzeit-Beweis: 595 valide Lehrbuch-Akkumulationen → 0,0% erreichen Scanner-Grade S/A** (nach Konvention wären es 200) — und das Mail-Gate liest genau dieses Grade. bi_long-Mails sind strukturell ausgehungert; bi_short-Mails nur erreichbar, weil der ShortBonus (0-50, Earnings/Stage-4/Insider) VOR dem Regrade in den Score fließt — das Grade misst dann den Bonus, nicht das Setup. 640 perfektionierte Setups: Maximum 98 — die A-Schwelle 99 liegt ÜBER der empirischen Obergrenze. UI zeigt fast alles als C/D.

### H-2 · Kein Long-Extension-Gate + Fenster-Mismatch Analyse↔Level
Signale rechnen auf dem adaptiven Konsolidierungsfenster, Entry/Stop/TP auf dem fixen 15-Bar-Fenster — enthält dieses einen Spike, ist die Entry-Referenz der Spike statt der Konsolidierung. Fuzz: **22% der validen Long-Kandidaten haben Entry >5% über Kurs** (median 12,4%, max 28,9%) — "imminent"-Signale mit Trigger, der nie sauber triggert, und Fantasie-R:R (p95 = 15,2). Zudem passieren 2-Tages-Pumps (+9-12%/Tag) den 1-Tages-Pump-Filter in 25% der Fälle.

## MITTEL
**M-1 Partial-Bar im Score:** Der laufende Tag zählt als vollwertige Kerze in den Kontraktions-Signalen (RVOL korrekt ausgeschlossen, Analyse nicht): morgens +3 Punkte median (max +26), ATR-Squeeze meldet in 96% "stark", **8/25 marginale Setups flippen invalid→valid** nur durch die Tagesuhr. · **M-2 Pseudo-Konfirmation:** OBV-Flow und InstAccumulation messen dasselbe Up-Volumen-Phänomen — bei reinen Volumen-Serien feuern in 74% ≥2 "fires"; dazu Gratis-hits ohne Richtungsaussage (OrderBlock "vorhanden" 42%, Liquidity 15% auf Random Walks). "B braucht 2 hits" ist real oft 1 Signal + Rauschen. · **M-3 Distribution passiert Long-Validität:** Lehrbuch-Distribution erreicht Ø 59,5 Long-Punkte, 67/80 über Threshold — der Score bestraft Distribution nicht; mehr Kontraktion erhöht den Score NICHT (Sättigung).

## NIEDRIG
max_score-Arithmetik falsch (183/173 statt 188 → direction_confidence nie >92%) · Short-Extension-Gate beweisbar toter Code (Range inkl. letzter Kerze ⇒ Extension ≡ 0) · **bi_short-TP1-Altbefund auf HEAD widerlegt** (0/3.387 Verletzungen — gleiche Fensterlogik macht TP1<Entry strukturell; Formel-Fix nur nötig, falls das Range-Fenster je auf Pre-Breakdown umgestellt wird: dann TP1 = min(alt, entry−0.5·risk)) · load_cache_file ignoriert max_age_hours · NaN-Hygiene.

## Explizit SAUBER (laufzeitgeprüft)
Level-Geometrie beider Richtungen: **0 Verletzungen in 7.271 End-to-End-Fällen**, Stops eng (median 1,0-1,75%, max <5,2% — kein C&H-Problem) · RVOL nutzt den letzten KOMPLETTEN Handelstag (api-Pfad) · Pump-/Range-Breakdown-/Recent-Bearish-Gates wie dokumentiert · Numerik-Kern crashfrei (außer MACD-Vertragsbruch) · trade_levels normalize/geometry NaN-fest · Mail-Gates api+bg auf Parität inkl. estimated-Sperre und geteiltem Dedupe · Candidate≠Signal sauber getrennt (9ea379b) · RVOL-Guard an allen 3 Stellen konsistent · Signal-Tracker-Hooks vorhanden.

## Fachabgleich Wyckoff/VCP (Kurzform)
Gut: Higher Lows (Random-Walk-korrigiert), Boundary-Tests, Dry-up-Komponente. Teilweise: Kontraktion sättigt zu früh (VCP-Tiefe undifferenziert), Liquidity ohne Nähe-Bedingung. Fehlt: Distribution-Malus, echte relative Stärke vs. Markt (nur "Resilience"-Proxy — der Code gibt es selbst zu), Long-Chase-Schutz, Stage-Kontext für Longs.

## Priorisierter Fixplan
1. **K-2** MACD-Vertrag reparieren (Hist-Serie für Signal 13) — entsperrt bg, Autotrader, 10 Punkte
2. **K-1** bg auf `_bi_background_scan` umstellen, `_run_bi_analysis_direct` löschen; bis dahin BG_SCAN_SET-Sofortmaßnahme
3. **H-1** EINE Grade-Quelle: Regrade-Blöcke (scanners 1521, bg 1797) entfernen, patterns-Leiter durchreichen; ShortBonus separat ausweisen; Mail-Fußzeile korrigieren
4. **H-2** Long-Extension-Gate (Reject wenn (Entry−Close)/Close > max(2×ATR%, 3%)); Entry-Referenz auf das adaptive Fenster; Pump-Filter auf kumulierte 2-3-Tages-Moves
5. **M-1** Partial-Bar aus der Analyse ausschließen (wie beim RVOL)
6. **M-2/3** OBV+InstAcc deduplizieren (max-Prinzip), "vorhanden"-hits ohne Nähe keine sm_hits, Distribution-Malus für Long
7. **N** max_score korrigieren, toten Extension-Code entscheiden, load_cache_file-Frische, bg-Mail-Frische-Check
