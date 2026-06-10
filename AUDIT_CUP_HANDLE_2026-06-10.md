# Tiefen-Audit: Cup-&-Handle-Breakout-Scanner — 10.06.2026

> **STATUS-UPDATE (gleicher Tag): ALLE Befunde gefixt, 543/543 Tests grün (16 neue Regressionstests).** K-1: Anti-Fenster-Shopping (Lip ≥ 97% des globalen Pre-Breakout-Hochs + pivot-treue Bestauswahl) — 98er-Fakeout ⇒ None, echter Breakout meldet Entry 101.20 statt 98.09. K-2: INTRADAY_UNCONFIRMED/BEOBACHTEN/WAIT_FOR_* sind nicht mehr mailbar + neue "(Daily Close bestätigt)"-Mail nach Tagesschluss (eng gegated: alle Rows CONFIRMED + heutige Daily-Kerze + eigener Dedupe). H-1: Strategy-Key + pattern-Token ⇒ Mail-Floor 1.5 greift. M-1..M-5: Handle-Drift-Gate, Dry-up-Hard-Gate (≤1.15×), Stop-Cap 10% (Fuzz: max 9.99% statt 16.6%), Grade S erst ab 90, ehrliche Labels. N-1..N-4 aufgeräumt. Fuzz nachher: 782/2500 Matches, 0 Verletzungen, 0 Crashes.

**Stand:** HEAD `77b9eec` (alle Sanierungen vom 10.06. enthalten) · Read-only-Audit mit 6 Verifikationsskripten (Geometrie-Tests, 2.500-Fall-Fuzz, End-to-End-Mail-Pfade, Fenster-Shopping-Beweis). Beide KRITISCH-Befunde zusätzlich vom Chef-Dev eigenhändig reproduziert.

## Funktionsweise (Ist-Stand, api.py:9022-9301)

Pipeline: `/api/scan` → Snapshot-Filter (Change −2…+12%, RVOL 1.5-50, ClosePos ≥ 0.45, Preis ≥ 5$, $-Vol ≥ 2M, geerbt: Vortag ±3%) → max. 180 Kandidaten mit 180 Tagen Daily-History → Detektor: Doppelschleife Fensterlänge (170→70) × Handle-Länge (5-24 Bars); Cup 45-165 Bars, Tiefe 10-45%, Rim-Verhältnis 0.86-1.16, U-Form ≥ 3 Bodennahe Bars; Handle 1-16% tief (≤ 58% der Cup-Tiefe), über der 45%-Linie; Breakout: Close ≥ Lip×1.002 oder (High ≥ Lip×1.006 + ClosePos ≥ 0.65), Extension −1.5…+8%, RVOL ≥ 1.5 hart (Avg20 ohne Breakout-Bar); Entry = Fenster-Lip, Stop = min(HandleLow − 0.2·ATR, Lip − 0.22·Tiefe), TP1/TP2 = Entry + 0.5/1.0 × Cup-Tiefe (measured move); Gates: blended R:R ≥ 1.8, live R:R ≥ 1.4, Pattern-Score ≥ 80; final = 0.2·base + 0.8·pattern ⇒ ab 80 immer Grade "S". Session offen ⇒ INTRADAY_UNCONFIRMED/BEOBACHTEN (UI).

## KRITISCH

### K-1 · Fenster-Shopping: CONFIRMED unter dem echten Widerstand, Pivot systematisch falsch
Der Bestätigungs-Check läuft **pro Suchfenster**, die Bestauswahl nimmt das score-optimale Fenster — auch wenn dessen "Lip" unter dem echten Strukturhoch liegt. **Eigenhändig reproduziert:** Echte Struktur mit Rim 101.20 → bei Kurs **98.00** (−3,2% unterm Rim, Breakout nie passiert): `CONFIRMED, Entry 97.44, Score 100`. Selbst beim echten Breakout (102.0) meldet der Scanner Entry 98.09 statt 101.20. Konsequenz: Abonnent kauft "bestätigte Breakouts" direkt unter echter Resistance (klassischer Fakeout-Einstieg); Stop/TP referenzieren die falsche Struktur; TP1 kollidiert mit dem echten Rim.
**Fix:** Anti-Shopping-Gate — Fenster-Lip muss ≥ ~97% des globalen Pre-Handle-Hochs der letzten ~150 Bars sein; Regressionstests: 98.0-Fall ⇒ None, 102.0-Fall ⇒ Entry ≈ 101.2.

### K-2 · Session-Downgrade schützt nur die UI — die Mail nicht; bestätigte C&H-Mail ist architektonisch unmöglich
Kein Mail-Gate liest `entry_status`/`trade_signal`. **Eigenhändig reproduziert:** Session offen, Row = `BEOBACHTEN/INTRADAY_UNCONFIRMED` ⇒ Mail "Top 1 Setup(s) — Cup and Handle Breakout", Grade S, Mail-Score 93, **ohne jeden Unconfirmed-Hinweis im Body**. Spiegelproblem: Nach Daily-Close (einziger legitimer Bestätigungszeitpunkt) blockt das Session-Gate ALLE Aktien-Mails. Netto: Die einzigen C&H-Mails, die je rausgehen können, sind genau die unbestätigten — der Session-Fix vom Vormittag wirkt nur in der Anzeige.
**Fix:** (a) Suppression-Reason für `INTRADAY_UNCONFIRMED`/`BEOBACHTEN`/`WAIT_FOR_*` im Klassifikator; (b) Produktentscheidung: C&H nach Daily-Close als "Setup bestätigt"-Mail vom Session-Gate ausnehmen (sonst ist der Kanal tot) ODER Intraday-Mails explizit als 👁️ WATCH labeln.

## HOCH

**H-1 · RVOL-Mail-Floor-Lücke:** Das Breakout-Token-Gate (`_alert_min_rvol_for_row`) matcht auf den `Strategy`-Key — C&H-Rows tragen ihn nicht (nur `pattern`) ⇒ Mail-Floor 0.7 statt 1.5. End-to-End bewiesen: Row mit RVOL 1.2 wurde gemailt. Aktuell kein realer Eintrittsvektor (Snapshot-Filter + Detektor sitzen mit 1.5 davor), aber die letzte Verteidigungslinie der Geschäftsregel ist für C&H wirkungslos. **Fix:** `row["Strategy"] = strategy_name` im Scan-Wrapper (1 Zeile) + Token-Matching zusätzlich auf `pattern`/`pattern_type`.

## MITTEL

| # | Befund | Konsequenz |
|---|---|---|
| M-1 | Kein Handle-Abwärtsdrift-Check — aufwärts keilender Handle (O'Neil-Failure-Signal) bekommt Score 100 | strukturell schwache Setups als Elite gelabelt |
| M-2 | Volumen-Dry-up im Handle nur +6-Bonus, kein Gate — Handle-Volumen 2.2× Cup-Schnitt (Distribution!) wird akzeptiert | das wichtigste Qualitätssignal des Patterns ist zahnlos |
| M-3 | Stop-Distanzen bis 16.6% (Fuzz: Median 8.7%, p95 14.1%, 61.5% über O'Neils 8%-Limit) — Stop skaliert mit Cup-Tiefe statt gedeckelt | R-Einheit pro Trade zu groß, Positionsgrößen-Disziplin unmöglich |
| M-4 | Grade-Inflation: final ≥ 80 erzwungen ⇒ JEDE C&H-Row ist "S" (base_score 0 → final 80 → S) | Elite-Label entwertet |
| M-5 | `confirmation_timeframe: "5m"` verspricht weiterhin mehr als implementiert (nur Fade-Check, kein 5m-Trigger über Pivot) | irreführende Metadaten |

## NIEDRIG
Geerbter Vortag-±3%-Filter undokumentiert (Breakouts nach starkem Vortag unsichtbar) · toter Score-Zweig `rvol >= 1.1` + ungelesene Parameter · Zweit-Implementierung in modules/patterns.py (Chart) mit anderen Schwellen, meldet "Breakout" schon ab 0.95×Lip — Chart und Scanner können sich widersprechen · NaN-Bars werden still entfernt.

## Explizit SAUBER (laufzeitgeprüft)
Detektor-RVOL-Gate exakt (1.49 rein / 1.51 raus), Numerik robust (leer/NaN/0-Preise/flach → sauberes None), **Fuzz 2.500 Geometrien: 0 Verletzungen** von Stop<Entry<TP1<TP2, R:R-Gates und Score-Range, 0 Crashes · V-Bottom-/Tiefen-/Handle-Positions-/Chase-Guards greifen · UI-Seite des Session-Fixes wirkt · Mails nach Börsenschluss hart geblockt, Cooldown + persistentes Dedupe · measured-move-Kursziele Standard-konform · MDR-Bypass kann RVOL nicht mehr unterlaufen · 6/6 Bestandstests grün.

## Fixplan (priorisiert)
1. K-1 Anti-Fenster-Shopping (globales Pre-Handle-Hoch als Referenz)
2. K-2 Unconfirmed-Suppression im Mail-Gate + Produktentscheidung Daily-Close-Mail vs. WATCH-Label
3. H-1 Strategy-Key + Token-Erweiterung (Geschäftsregel-Floor)
4. M-1/M-2 Handle-Drift-Gate + Dry-up als Hard-Gate
5. M-3 Stop-Cap 8-10%
6. M-4/M-5 Grade-Spreizung (S ab ~90) + ehrliche Labels
7. N-x Aufräumen (Vortag-Filter dokumentieren, toter Code, patterns.py harmonisieren)
