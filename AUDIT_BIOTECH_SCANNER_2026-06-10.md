# Tiefen-Audit: Biotech-Scanner — 10.06.2026

> **STATUS-UPDATE (gleicher Tag): ALLE Befunde gefixt, 602/602 Tests grün.** K-1: Negativ-Erkennung VOR Positiv-Matching mit Verb-Formen + Verneinungsfenster + Roman→Arabisch — alle 5 Fehlklassifikationen der 32er-Matrix kippen auf Soll ("misses primary endpoint" jetzt 0 + Negativ-Flag statt +22). H-1: neuer Mode NEAR_BINARY_EVENT ab T-3 (MCap-unabhängig, Score-Penalty −10). H-2: rote Gap-Warnung in Mail + Plan-HTML UND hartes Mail-Gate (T-2 ⇒ suppressed). H-3: REST-Endpoint lebt (PRIORITY_WATCH/WATCH_FOR_TRIGGER sichtbar, NEAR_BINARY_EVENT Kontext). H-4: FORWARD-Katalysatoren ("expected") scoren nicht mehr als eingetreten. M-1/2: Offering/Dilution/Discontinued in Negativliste. M-3: Readout-Doppelzählung beseitigt. M-4: synthetic-Flag (ehrliche Klassifizierung, Label flag-basiert). M-5: Device-Clearance tier1→tier4. N: Quartals-Datums-Heuristik (Q3⇒15.08., estimated-Flag), UTC, Plural-Fix, LATER sichtbar. Harnesses (biotech_audit/bpiq/edge_score) unverändert grün. Commit: siehe git log.

**Stand:** HEAD `c6d542c` · Read-only, 5 Verifikations-Harnesses (/tmp/biotech_audit/): 32-Headline-Klassifikations-Matrix, Score-Nachrechnung, 2.500-Fall-Geometrie-Fuzz, Datums-/Crash-Matrix, Signal-Only-Test. KRITISCH- und HOCH-3-Befund vom Chef-Dev eigenhändig reproduziert.

**Vorab-Befund:** ClinicalTrials.gov-Integration ist tot (Live-Scan setzt trial_data hart auf 0); Readouts kommen ausschließlich aus dem BPIQ-Cache. `_check_clinical_trials` ist Importleiche.

## KRITISCH

### K-1 · Trial-FEHLSCHLAG wird als positiver Katalysator gescored
Positiv-Erkennung läuft auf title+description, Negativ-Check nur auf title mit STARREN Phrasen. Verb-Formen fehlen komplett: **"Acme misses primary endpoint, shares plunge" → catalyst_score 22 (tier2), Total 56, Mode WATCH_FOR_TRIGGER — exakt identisch mit "positive Phase 3 data"** (eigenhändig reproduziert). Auch "fell short", "disappointing" ungefangen; "did NOT meet primary endpoint" rettet nur der Edge-Layer (regulatory_keywords), die Verb-Varianten nicht. Das schlimmste Biotech-Outcome — verfehlter primärer Endpunkt — wird dem Abonnenten als Setup serviert. Mit-Ursache: Roman/Arabisch-Inkonsistenz ("Phase 3 trial"→0, "Phase III study"→22).

## HOCH

### H-1 · Binary-Event-Risiko für Mid/Large-Caps nicht entschärft
PDUFA/Phase-3-Readout = binäres Event (Gap ±40-80%, Stop wertlos). Bei days≤3 gibt es nur halt_risk+18 + Flag — **Score bleibt flach (26) und Modus PRIORITY_WATCH (+8) bis T-1** (Laufzeit-Beweis, 3-Mrd-MCap). Mode-Downgrade greift erst ab halt_risk≥25. Nur Micro-Caps (<100M) werden korrekt heruntergestuft — also genau NICHT der vom risk_score bevorzugte 0,5-10-Mrd-"Sweet Spot". Keine Run-up- vs. Event-Risiko-Unterscheidung.

### H-2 · Binary-/Halt-Risiko erscheint in keiner Abonnenten-Fläche
Halt_Risk/near_binary_event/Bio_Trade_Mode stehen in der Cache-Zeile, aber **weder Mail noch Trade-Plan-HTML rendern sie**. Eine T-3-Zeile (Grade A + Health TRADEABLE) kann ohne Binary-Warnung gemailt werden — strukturell nichts verhindert es.

### H-3 · `/api/biotech-results` liefert systematisch 0 Zeilen — toter kommerzieller Endpoint
Die Signal-Only-Policy für biotech listet ALLE sieben möglichen Bio_Trade_Modes als "Kontext" — auch die handelbaren PRIORITY_WATCH und WATCH_FOR_TRIGGER. **Eigenhändig reproduziert: jede Grade-A-Row ist unsichtbar, der REST-Endpoint liefert count=0 bei jedem Scan.** Mail (liest Rohcache) und Streamlit-Tab sind nicht betroffen — der zahlende API-/Frontend-Kunde schon.

### H-4 · Zukunft = Vergangenheit beim Katalysator
"topline results **expected** in Q3 2026" scored identisch (22/tier2) wie "**announces** topline results". Decay hängt am News-Publikationsdatum statt am Event-Datum — eine heutige Ankündigung eines PDUFA in 3 Monaten bekommt den vollen Frische-Score.

## MITTEL
**M-1** Kapitalerhöhung/Dilution ("public offering", "registered direct offering") nicht in der News-Negativliste — nur der Edge-Layer dämpft, die Primärscores sehen die Verwässerung nicht. · **M-2** "discontinued/discontinuation of development" ebenso ungefangen. · **M-3** Derselbe BPIQ-Readout zählt doppelt (pipeline_score +10 UND Edge +8). · **M-4** Synthetische Level werden via expliziter Entry/Stop/TP-Felder als `native=True` eingestuft — die estimated-Sperre des Plan-Guards ist für Biotech wirkungslos (einzige Offenlegung: das heutige M1-Label). · **M-5** MedTech/Devices (SIC 3841/3842) im Biotech-Universum; Device-510(k)-"fda clearance" matcht tier1 (30) wie ein Drug-PDUFA.

## NIEDRIG
Fuzzy-Quartals-Daten ("Q3 2026"/"H2") → Readout fällt still aus der Wertung · "safety concern" matcht Plural nicht · naive Zeitzone kann T-0 als OVERDUE statt near_binary einstufen · LATER-Bucket scored unsichtbar · tote `_load_bpiq_catalyst_cache`-Dublette in scanner.py.

## Explizit SAUBER (laufzeitgeprüft)
Geometrie-Fuzz 2.500 Fälle: **0 Verletzungen** (Stop-Distanz median 8%, p95 12,7% — biotech-typisch weiter, dokumentiert) · Crash-Matrix 0 Crashes (kaputtes JSON, leere News, fehlende Keys → sichere Defaults) · "complete response" ohne "letter" korrekt POSITIV, CRL korrekt negativ (V3-Fix hält) · Time-Decay konsistent, Score-Monotonie linear, RVOL-Bonus up>down korrekt · BPIQ-Kategorien korrekt, invalides Datum sauber degradiert · M1-Kennzeichnung rendert exakt für synthetische Level · Mail-Gate-Kette aktiv (Grade-A-only, RVOL-Guard, Health, 72h-Dedupe, R:R) · Sell-the-news-Schutz vorhanden · Micro-Cap-Binary-Downgrade korrekt.

## Headline-Matrix (Auszug der Fehlklassifikationen)
| Headline | Ist | Soll |
|---|---|---|
| misses primary endpoint, shares plunge | **+22 tier2** | hart negativ |
| Phase III did NOT meet primary endpoint | +22 (Edge rettet teilweise) | hart negativ |
| topline results expected in Q3 2026 | +22 wie eingetreten | FORWARD/Watchlist |
| prices $50M public offering | 0, kein Flag | negativ (Dilution) |
| discontinuation of development | 0, kein Flag | hart negativ |

## Priorisierter Fixplan
1. **K-1:** Negativ-Erkennung VOR Positiv-Matching; Verb-Formen ergänzen (miss/missed/misses … endpoint, fail(s/ed) to meet/achieve, fell short, disappointing); Verneinungsfenster ("did not …") vor Positiv-Keywords; Roman→Arabisch normalisieren.
2. **H-1/H-2:** Echter Event-Risk-Zweig ab T-3 unabhängig von MCap (Score-Penalty + Mode NEAR_BINARY_EVENT) + Mail-/Plan-HTML-Zeile "⚠ Binäres Event in Xd — Stop über Gap wertlos".
3. **H-3:** PRIORITY_WATCH/WATCH_FOR_TRIGGER aus den Kontext-Modes nehmen (Endpoint lebt wieder).
4. **H-4:** Zukunfts-Keywords (expected/anticipated/on track/to report/upcoming) als FORWARD klassifizieren; Decay ans Event-Datum koppeln.
5. **M-1/2:** offering/registered direct/discontinued/terminated in die News-Negativliste (mit Wortgrenzen).
6. **M-3:** Readout nur in EINER Schicht scoren. **M-4:** Synthetik-Level als eigene Klasse im Plan-Guard. **M-5:** Devices von tier1 entkoppeln.
7. **N:** Quartals-Heuristik (Quartalsmitte), Plural-Fix, UTC-Datum, tote Dublette raus.
