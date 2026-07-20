# Scanner-Audit Fixstatus 2026-07-20

## Umfang

Dieser Status vergleicht `AUDIT_SCANNER_VOLLAUDIT_2026-07-19.md` mit dem
aktuellen Code. Er umfasst Aktien-, Krypto-, Penny-, Biotech-, BI-, ORB-,
Turtle-, Bear/Crash-, New-Listing- und AutoTrader-Pfade sowie Backtests,
Signal-Tracking, Trade-Level, Market Context und E-Mail-Gates.

Status: Alle im Claude-Audit nummerierten Befunde wurden im Code adressiert
und durch den vollstaendigen Regressionstest abgesichert.

## Kritisch und hoch

| Fund | Status | Korrektur |
|---|---|---|
| K1 | Behoben | Krypto-Daily-Daten werden nur als echte UTC-Tageskerzen akzeptiert; Bar-Abstand und Datenqualitaet werden validiert, Backtests nutzen Exchange-Daily-Daten. |
| H1 | Behoben | MEXC-Volumen und OI beruecksichtigen `amount24`/`contractSize`; Fantasie-USD-Werte koennen Venue-Auswahl und Gates nicht mehr verzerren. |
| H2 | Behoben | Signal-Tracker hat Fill-/NO_FILL-Logik, reales Gap-R, positive-R-Wins, Ablaufquote, ET-Handelsdatum und Cache-TTL. |
| H3 | Behoben | Backtest-Historien bleiben lueckenlos; Preis-/Volumenfilter gelten nur am Signaltag, Fetch-Fehler sind von validen Leertagen getrennt. |
| H4 | Behoben | AutoTrader trennt Analyse und Ausfuehrung, nutzt Trigger-Orders, Live-Chase-Gate, Partial-Bar-Strip und Gap-Penalty vor Freigabe. |
| H5 | Behoben | Bear/Crash/Turtle verwenden projected US-RVOL statt Teilvolumen gegen komplette Tage. |
| H6 | Behoben | Exchange-Handelsstart hat Vorrang vor Announcement-Zeit; Kerzenzeit dient als Plausibilitaetsanker. |
| H7 | Behoben | Biotech entfernt laufende Tageskerzen und behaelt RVOL-Richtung auch im Quick-Scan. |
| H8 | Behoben | Swing-Indizes sind fuer exakt drei Pivots korrekt; Logging ist definiert und Exceptions werden nicht mehr still verschluckt. |
| H9 | Behoben | Krypto-Exhaustion bewertet den Downside-Z-Score mit korrektem Vorzeichen. |
| H10 | Behoben | Leere VRVP-Bins sind keine S/R-Barrieren; nur gewichtige Strukturzonen duerfen Stops/Ziele beeinflussen. |
| H11 | Behoben | Bestaetigte Retests bleiben durch Scanner, kanonische Entscheidung, GET und Mail als eigener Zustand erhalten. |

## Mittlere Befunde

| Fund | Status | Korrektur |
|---|---|---|
| M1-M5 | Behoben | Mindest-Risiko, R:R-Sanity, signifikante Preisrundung, ATR-Sanity und frisch berechnetes Live-R:R sind zentral in Trade-Health/Trade-Levels. |
| M6-M11 | Behoben | BTC ist dreiwertig/fail-closed; Early-Mover-Cache laeuft ab; Trigger pruefen Stopbruch und Distanz; Duplikate, Preisalter und Mail-Frische werden geblockt. |
| M12-M16 | Behoben | ORB nutzt abgeschlossene Kerzen und positive Reversal-Geometrie; Turtle-Doppelscore entfernt; Bear-5m-Frische und inverse ETF-Erkennung korrigiert. |
| M17-M20 | Behoben | Backtest-Exitstatistik, Strategie-Dedupe, Chronologie, Gap-Fills und Tag-1-Simulation sind korrigiert; Tracker/Event/Penny-Zeitlogik ist frisch und zeitzonensicher. |
| M21-M23 | Behoben | New-Listing normalisiert nach verfuegbaren Daten und verwirft Partial-Instrumentenlisten; Pattern-Heuristiken, VWAP-Varianz und ATR-Verwendung sind korrigiert. |

## Niedrige Befunde

| Fund | Status | Korrektur |
|---|---|---|
| L1-L4 | Behoben | Null-RVOL bleibt null, Fear-Skala ist stetig, Sweep-Score nutzt den vorgesehenen Bereich und Phasenquoten werden nicht ueberschrieben. |
| L5-L8 | Behoben | Vergangene Biotech-Events werden verworfen; Funding-/Depth-Texte, VRVP-Mindestbars und New-Listing-Expiry sind konsistent. |
| L9-L12 | Behoben | BUY/SELL-Richtung, News-Rueckgabetyp, Data-Fetcher-Kanten und Market-Context-Gewichtung sind vereinheitlicht. |
| L13-L14 | Behoben/isoliert | Root-Legacy-Module sind harte Kompatibilitaets-Stubs; alte Streamlit-/Legacy-Servicepfade werden nicht produktiv gestartet. |

## Zusaetzliche systemische Absicherung

- Eine kanonische Wilder-ATR wird in den produktiven Strukturpfaden genutzt.
- Krypto-Candles werden gegen den deklarierten Timeframe validiert.
- Stop, Entry, TP1 und TP2 muessen gerichtete, nicht ueberlappende Geometrie bilden.
- Live-R:R wird aus aktuellem Preis neu berechnet statt aus Scanner-Altwerten uebernommen.
- BTC-, News-, Exchange- und Kerzendaten fehlen nicht mehr optimistisch, sondern sperren Ausfuehrung.
- Penny-Signale werden direkt vor Freigabe mit Quote, Spread, geschlossener 5m-Kerze, Alter, Entry-Drift und Netto-R:R erneut validiert.
- Backtests trennen NO_FILL von Verlusten und simulieren chronologisch ohne Signaltag-Historienfilter.
- Der vorcompilierte Frontend-Bundle wird per Quellhash auf Aktualitaet geprueft.

## Unabhaengige Codex-Nachpruefung

Nach der Umsetzung des Claude-Audits wurde der produktive Code nochmals
unabhaengig nach parallelen Berechnungen, Look-ahead, Teilkerzen, fehlenden
Daten, falscher R:R-Geometrie und optimistischen Fallbacks durchsucht. Dabei
wurden zusaetzlich folgende Restpfade korrigiert:

- Volumen-Baselines bleiben strikt im angeforderten Zeitfenster; fehlende
  neue Bars werden nicht durch aeltere gueltige Bars aufgefuellt.
- Fehlende 5m-Volumenhistorie kann keinen Krypto-Entry mehr bestaetigen.
  Distribution und Wolfe-Reversal erhalten ebenfalls keine Volumenpunkte
  ohne ausreichende reale Datenabdeckung.
- AutoTrader, BI, SPAC-Erkennung, Gap-Recovery, Order-Block-Naehe,
  Wyckoff-Chart, Sidebar und Backtests verwenden dieselbe True-Range-/Wilder-
  ATR-Definition. Einfache High-Low-Mittel wurden aus diesen Pfaden entfernt.
- Penny-5m-Trigger, Pattern- und Biotech-Volumenlogik behandeln Datenmangel
  fail-closed statt mit Eins-, Null- oder Current-Bar-Fallbacks.
- Market-Cap-Turnover wird nicht mehr als RVOL bezeichnet oder wie eine
  historische Relativvolumen-Messung bewertet.
- Profit Factor ohne Verlust wird als `INF`/nicht endlich ausgewiesen statt
  kuenstlich gedeckelt; Equity/Drawdown, Gap-Fills, NO_FILL und OOS-Chronologie
  werden zeitlich korrekt berechnet.
- Live-R:R beruecksichtigt Execution-Kosten und wird bei Stopbruch oder
  veralteter Preisbasis nicht als handelbar weitergereicht.

Ergebnis der Gegenpruefung: Alle nummerierten Claude-Befunde und alle dabei
neu gefundenen Codex-Restbefunde sind im aktuellen Stand adressiert. Es gibt
keinen bekannten offenen Rechen-, Datenintegritaets- oder Zustandswiderspruch
aus diesen beiden Audits.

## Abnahme

- `python -m pytest -q --tb=short`: 928 bestanden.
- Python-Kompilierung aller geaenderten Produktivmodule: bestanden.
- `scripts/verify_frontend_bundle.py`: bestanden.
- `node --check frontend/app.bundle.js`: bestanden.
- `node --check frontend/boot.js`: bestanden.
- `git diff --check`: keine Patchfehler; nur Windows-Zeilenende-Hinweise.

Die Abnahme belegt Codekonsistenz und Regressionen im getesteten Modell. Sie
ist keine Garantie fuer Gewinne; reale Slippage, Datenanbieter-Ausfaelle und
Marktregime bleiben externe Risiken und werden deshalb weiterhin fail-closed
behandelt.
