# Penny-Stock-Scanner: Vollaudit und Abnahme 22.07.2026

## Ziel und Grenze

Der Scanner soll aktive, ausfuehrbare Penny-Stock-Tradingideen liefern und eine bereits aktivierte Modellposition bis zum Exit begleiten. Er soll keine breite Watchlist als Kaufsignal verkaufen. Score und Grade sind Rankingmerkmale, keine Treffer- oder Gewinnwahrscheinlichkeit.

## Daten- und Laufzeitmodell

1. Der 30-Minuten-Discovery-Lauf laedt das vollstaendige aktive US-Common-Stock-Universum und prueft alle Symbole im Preisband 0.20 bis 5.00 USD.
2. Der 5-Minuten-Monitor prueft aktive Modellpositionen und den kurzlebigen technischen Trigger-Pool erneut mit frischen Intraday-, Quote-, Struktur-, News- und SEC-Daten.
3. Ein Discovery-Treffer allein ist kein Kauf. `JETZT_KAUFEN` entsteht erst nach abgeschlossener 5-Minuten-Bestaetigung und anschliessender Live-Revalidierung.
4. Die Standard-UI zeigt nur `JETZT_KAUFEN`, `HALTEN` und `JETZT_VERKAUFEN`. `TRIGGER_WARTEN` und `BEOBACHTEN` sind eine optionale Vorstufe.

## Mathematische Pruefung

### Getrennte Scores

- `setup_quality_score` bewertet Basis, Kompression, Naehe zum Level, EMA-/VWAP-Struktur und getestete Widerstandszone.
- `entry_quality_score` bewertet Frische und Qualitaet des abgeschlossenen 5-Minuten-Triggers, Schlusskursposition, Volumen und Spread.
- `dump_risk_score` bewertet Ueberdehnung, obere Wicks, Distribution, Fortschrittsverlust und strukturbezogene Rueckfallrisiken.
- `trade_score = 0.45 * setup + 0.55 * entry - 0.15 * dump_risk`, anschliessend auf 0 bis 100 begrenzt.

Die vier Werte sind bewusst nicht austauschbar. Ein hoher Setup-Score ohne frischen Entry-Trigger bleibt eine Vorstufe. Ein hoher Entry-Score mit hoher Dump-Gefahr darf den Kauf-Gate nicht umgehen.

### Ausfuehrung und R:R

- Risiko pro Aktie: `entry - stop` fuer Long.
- Round-Trip-Kosten: Spread plus zweimal modellierte Slippage.
- Netto-TP1-R:R: `(tp1 - entry - round_trip_cost) / (entry - stop + round_trip_cost)`.
- Effektives R:R verwendet die modellierte Teilverkaufsgewichtung von TP1 und TP2 nach Kosten.
- Mindestwerte: Netto-TP1 mindestens 1.0R, effektives Netto-R:R mindestens 1.5R.
- Die naechste belastbare Widerstandsbarriere muss mindestens 1.35R entfernt sein.
- TP1 und TP2 muessen oberhalb des Entries, voneinander verschieden und strukturell begruendet sein.

### Frische und Chronologie

- Kauftrigger duerfen hoechstens 360 Sekunden alt sein.
- Nur abgeschlossene 5-Minuten-Kerzen koennen einen Entry bestaetigen.
- Replay wertet ausschliesslich Bars nach dem Signalzeitpunkt aus.
- Wenn Stop und Ziel in derselben OHLC-Kerze liegen und die Reihenfolge unbekannt ist, wird konservativ die unguenstigere Sequenz angenommen.

## Traderlogische Abnahme

### Kauf-Gates

Ein Kauf benoetigt gleichzeitig:

- aktive US-Common-Stock-Identitaet und Penny-Preisband,
- frischen Breakout oder sauberen Retest auf abgeschlossener 5-Minuten-Kerze,
- ausreichendes aktuelles und projiziertes Dollarvolumen,
- robustes RTH-RVOL gegen einen Median-Benchmark,
- ausfuehrbaren Spread und ein Mindest-Modellorderlimit,
- keine harte SEC-, Unternehmens- oder Promotionsblockade,
- keine bereits verlorene Invalidation und keine unzulaessige Entry-Drift,
- gueltige Strukturziele und ausreichendes Netto-R:R,
- Mindestwerte fuer Setup-, Entry- und Trade-Score sowie begrenztes Dump-Risiko.

### Exit-Gates

Harte Exits bleiben Schutz-Stop, TP2 und harte neue Unternehmensrisiken. Ein technischer Exit verlangt bestaetigten Strukturverlust:

- zwei abgeschlossene 5-Minuten-Schlusskurse unter VWAP,
- Strukturbruch mit starkem Verkaufsvolumen,
- mehrfach gescheiterter Ausbruch mit Distribution,
- oder hohes Dump-Risiko zusammen mit einem Strukturbruch.

Ein einzelner Warnwert wie `volume_no_progress`, eine rote Kerze oder `dump_risk >= 65` reicht nicht mehr fuer `JETZT_VERKAUFEN`.

## Dev- und Zustandsabnahme

- Discovery und Positionsmonitor besitzen getrennte Cache-/Health-Zustaende.
- Aktive Positionen werden im Monitor priorisiert und nicht von einem spaeteren Discovery-Lauf deaktiviert.
- Kauf, TP1-Management und Exit werden transaktional behandelt: State-Aenderung erst nach erfolgreichem Mailversand.
- Dedupe-Keys und Lifecycle-IDs sind ticker- und signalgebunden.
- Stale Quotes, stale Trades, stale Bars und unzuverlaessige Preisquellen failen geschlossen fuer neue Entries.
- Ein Exit kann eine frische, verlaessliche Trade-Price-Quelle verwenden, damit ein fehlender Bid/Ask-Spread den Schutz-Stop nicht blockiert.
- News- und SEC-Dilutionslogik sind angeglichen: Eine Shelf-Registrierung ist nur Kapazitaetswarnung; ein Offering, ATM oder Securities Purchase Agreement bleibt ein harter Finanzierungsblocker.

## UX- und Mailabnahme

- Die Standardansicht ist Signal-only; Vorstufen sind nur ueber den Nutzer-Schalter sichtbar.
- Die zusaetzliche hervorgehobene Liste `Entry fast bereit` wurde entfernt, weil sie Wartesignale wie fast sichere Entries wirken liess.
- Kaufmails erklaeren, dass einzelne Warnmerkmale kein Exit sind.
- Exitmails nennen den konkreten bestaetigten Exitgrund und den aktiven Stop.
- Mail, API und UI verwenden dieselben kanonischen Trade-Aktionen.

## Verifikation

- Penny-spezifische Regressionstests decken Frische, Universumsabdeckung, Trigger-Pool, Strukturplan, Kosten-R:R, Lifecycle, Mailtransaktionen, Replay, UI-Modus und technische Exitbestaetigung ab.
- Penny-spezifischer Testlauf: `65 passed`.
- Vollstaendiger Projekt-Testlauf: `1019 passed`.
- Python-Compile fuer `api.py` und `modules/penny_stock_scanner.py`: bestanden.
- Frontend-Build und Bundle-Verifikation: bestanden; Source-Hash `8bda8aeae93a`.
- `git diff --check`: bestanden; nur lokale Git-Hinweise zur Windows-Zeilenendung.

## Ehrliche Restrisiken

- Ein Scanner kann keinen Pump sicher vorhersagen und keine Gewinne garantieren.
- Historische OHLCV-Daten bilden Queue-Position, partielle Fills, Halts und reale Slippage nur begrenzt ab.
- Float-, News-, SEC- und Quote-Daten koennen fehlen oder verspaetet sein; neue Entries muessen dann fail-closed bleiben.
- Die Schwellen brauchen reale Forward-Daten, Regime-Segmentierung, Konfidenzintervalle und Walk-forward-Kalibrierung. Unit-Tests beweisen Codeinvarianten, nicht Profitabilitaet.
