# Fibonacci-Nachaudit und Korrektur - 07.09.2026

## Auftrag und Grenze

Fortsetzung nach dem gezielten Fibonacci-Audit, Basis `4dc49eb`.
Belegte Implementierungsfehler korrigieren; keine neue Strategie, keine
Aenderung der 17/20-Regel, keine Watchlist, keine zusaetzliche Pflicht zu
Multi-Timeframe-Konfluenz oder ATR-Selektion im BI. Keine Order, Mail,
Produktionsdatenkorrektur, Cron-Aktivierung oder Server-Aenderung.

## Befunde und Umsetzung

1. **Gebrochene Swing-Projektion im Chart/Adapter:** Der gemeinsame Kern
   liefert fuer einen ausgewaehlten Long-Swing nach einem spaeteren
   abgeschlossenen Tief unter seinem Ursprung kein Fib mehr; Short symmetrisch.
   Auch ein Wick-Bruch mit anschliessender Schlusskurserholung zaehlt. Reine
   Beruehrung bleibt erlaubt. Keine Rettung durch aeltere Start-/Endanker.
   Erst eine neue bestaetigte Bewegung kann wieder eine Projektion liefern.
   Die Pruefung verwendet die normalisierte abgeschlossene Kerzenfolge und
   damit dieselben Indizes wie die Pivot-Erkennung. Offene, zukuenftige und
   doppelte Kerzen duerfen die Entscheidung nicht verschieben.

2. **Zwei Lebenszyklus-Pruefungen:** Die bislang separate BI-Bruchpruefung
   wurde durch dieselbe Kernpruefung ersetzt, die auch Chart und historischer
   S/R-Adapter verwenden. Bestehende Mindestbewegungs-/ATR-Auswahl bleibt
   unveraendert; kein neuer Filter wurde vor diese Auswahl geschoben.

3. **US-Handelszeiten auf Krypto angewendet:** Provider-Routing und
   Asset-Zuordnung stammen aus `chart_market_context`. Bestehende Symbol-/
   Alias- und Provider-Routen bleiben gleich. Nur der US-Equity-Pfad bekommt
   den US-Tagesadapter. Krypto und andere Nicht-US-Pfade behalten ihre
   Provider-Zeitstempel. Ein UTC-Kryptotag wird nicht mehr bereits am
   US-Aktienschluss als abgeschlossen behandelt. Fibonacci und S/R im Chart
   verwenden denselben Eingangsadapter; explizite Abschlussflags bleiben
   erhalten. Das ist Routing-Metadatenlogik, keine externe Instrumentpruefung.

4. **Detailansicht ignorierte die Signalrichtung:** Ein explizites LONG/SHORT
   wird an die Fib-Berechnung weitergereicht. Automatische Richtung bleibt
   nur ohne explizite Auswahl bestehen. Gleiche Tagesdaten, Richtung und
   Daten-Cutoff liefern im Detail und Chart dieselben Level und dieselbe
   Leg-ID. API-Metadaten benennen Richtungsherkunft, Mindestbewegung und
   ausdruecklich fehlende Multi-Timeframe-Bestaetigung.

## Bewusst unveraenderter Analysevertrag

| Pfad | Datenfenster | Mindestbewegung | Pivot-Bestaetigung |
|---|---|---|---|
| Aktien-BI, Faktor 18 | letzte 30 abgeschlossene Tageskerzen | kein ATR-Minimum | 2 links / 2 rechts |
| Detail | bis 60 abgeschlossene Tageskerzen | 1 ATR, falls verfuegbar | 2 / 2 |
| Chart 5m / 15m | bis 80 Kerzen | 1 ATR, falls verfuegbar | 2 / 2 |
| Chart 1H / 4H | bis 100 / 120 Kerzen | 1 ATR, falls verfuegbar | 2 / 2 |
| Chart 1D / 1W | bis 60 / 52 Kerzen | 1 ATR, falls verfuegbar | 2 / 2 |

Die gemeinsame Mathematik erzeugt weiterhin nur Projektionen. Fibonacci ist
kein eigenstaendiger Strukturbeweis, kein bewiesener Support/Widerstand und
keine Gewinnwahrscheinlichkeit. Faktor 18 muss bei 17/20 nicht zwingend gruen
sein. Der gewichtete Score darf die Mindestzahl nicht umgehen. Die Fenster
und ATR-Schwellen werden nicht ohne prospektive Auswertung gleichgeschaltet.

## Abnahme

- 116 neue deterministische Regressionen in drei Testdateien; synthetische
  Kurse und gemockte Provider, keine echten Netzabfragen.
- 177 gezielte Tests inklusive BI-Indikator-/Downstream-Vertrag bestanden.
- 12 der neuen Lebenszyklus-Fehlerfaelle scheitern erwartungsgemaess gegen
  den unveraenderten alten Kern und bestehen nach dem Fix.
- Differenzialpruefung: 200 deterministische valide Reihen mit je 30
  Tageskerzen, jeweils LONG und SHORT, liefern 400/400 identische Ergebnisse
  fuer alter Kern plus bisherige BI-Bruchpruefung gegen neuen Kern. 166 Legs
  bleiben verfuegbar, 234 nicht verfuegbar; keine Abweichung ausser der
  additiven Provenienzangabe. Keine echte Marktdaten- oder Erfolgsstudie.
- Unabhaengiger Reviewer: 116/116 neue Tests bestanden, keine offenen
  Befunde im Diff. Zusaetzliche X:BTCUSD-Detail/Chart-Gegenproben bestaetigen
  LONG/SHORT-Paritaet auch mit rohen Polygon-Feldern und laufender UTC-Bar.
- Vollstaendige Testsuite auf unveraenderten Code-/Testdateien: **3313
  bestanden, vier plattformbedingte Skips, null Fehler**, 307,51 Sekunden.
  Die vier bestehenden Windows-/Linux-Sicherheits-Skips sind kein
  Linux-Produktionsnachweis. JUnit lokal: `tmp/fibonacci_release_20260907.xml`.
- Python-Compile, Frontend-Bundle-Verifikation (`cc0d82106285`), Diff-Pruefung
  und gezielter Credential-Musterscan bestanden. Die lokalen Tests sind kein
  Servernachweis; die verschiedenen Testgruppen ueberlappen und werden nicht
  als unabhaengige Gesamtzahlen addiert.
- Frontend-Dateien und Bundle unveraendert; keine neue visuelle Freigabe
  behauptet. Dieses Paket aendert Berechnung und API-Daten, nicht das Layout.

## Offene Grenzen - keine pauschale Zuverlaessigkeitsfreigabe

- Noch kein Nachweis hoeherer neuer Trefferquote oder positiver
  Netto-Erwartung. Dafuer braucht es abgeschlossene neue Forward-Kohorten,
  getrennt nach Scanner, Richtung, Markt, Version und Ausfuehrungskosten.
- Keine neue Weekly/Daily/4H-Fib-Konfluenz und kein verpflichtender Fib-Filter
  in allen Krypto-Scannern. Das waere eine neue Strategieentscheidung.
- Wochencharts nutzen weiter Provider-Anfang plus sieben Tage; das ist kein
  boersenkalendergenauer Wochenabschluss und kann US-Wochenlevel verspaeten.
- Nicht-US-Aktien/Forex/Futures behalten Provider-Anfang plus Bar-Dauer;
  ein vollstaendiger Handelskalender wurde nicht implementiert. Der
  US-Tagesadapter bleibt bei regulaer 16 Uhr New York (kein Early-Close-Modell).
- Historische 4H-Aggregationsluecken/Teilbucket-Metadaten werden nicht durch
  diesen Fix automatisch geheilt. Die Datenlieferanten benoetigen dafuer
  einen separaten Vollstaendigkeitsvertrag.
- Der allgemeine Ticker-Detail-Endpunkt enthaelt ausserhalb Fibonacci noch
  US-Aktien-spezifische ATR-/Struktur-/Sessionberechnungen. Seine gesamten
  Krypto-/Nicht-US-Metriken sind durch diesen Fib-Fix nicht freigegeben.
- Die UI zeigt weiterhin verkuerzte Prozentlabels (z.B. 61% fuer intern
  61,8%); Ankerzeitpunkte und Qualitaetsstatus sind noch nicht umfassend im
  Layout sichtbar. Die Preise selbst verwenden die praezisen Ratios.

## Rollout

Die vorherige Serverversion `4dc49eb` wurde vom Nutzer mit erfolgreicher
Recovery und Healthy-Antwort nachgewiesen. Das ist nicht der Nachweis dieses
neuen Pakets. Git-Push, manueller Server-Pull und anschliessende Pruefung von
API-Revision, Services und Health bleiben getrennte Schritte. Cron bleibt
unveraendert. Das Paket enthaelt keine DB-Schemaaenderung.
