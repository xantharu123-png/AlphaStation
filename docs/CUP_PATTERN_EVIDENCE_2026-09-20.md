# Datierte Cup-Geometrie im Scanner-Chart

## Auftrag und Grenzen

Ein Cup-Treffer soll durch seine tatsaechlich verwendeten Kerzen nachpruefbar
sein: linker Rand, Boden, rechter Rand, Henkelbeginn, Henkeltief, Henkelende
und Ausbruchskerze. Die Anzeige darf keine idealisierte U-Kurve erfinden.

Normale Schwankungen machen einen Cup nicht automatisch ungueltig. Das
uebergeordnete Muster bleibt eine Bodenbildung mit Rueckkehr zum Rand und
anschliessendem kleinerem Henkel; nicht jede Seitwaertsbewegung ist eine Tasse.
Diese Aenderung fuehrt weder neue Glaettungs-/Zickzack-Gates ein noch lockert
sie bestehende Schwellen. Der zuvor reproduzierte Docht-/Schlusskursfehler
und die schwache Rundungspruefung waren in diesem Aenderungsschritt noch offen.
Sie wurden anschliessend gesondert korrigiert und geprueft:
[Cup-Form und Schlusskurs, 21.09.2026](CUP_PATTERN_REPAIR_2026-09-21.md).

Fachlicher Bezug: [Fidelity: Cup with Handle](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/cup-with-handle).

## Evidenz statt nachtraeglicher Rekonstruktion

`cup_pattern_evidence` bindet die Anker an das vom Detektor ausgewaehlte
Originalfenster. Es enthaelt Version, Symbol, Timeframe 1D, Sitzungsdaten,
Fenstergrenzen, Cup-/Henkellaenge und sieben Anker mit Originalpreis und
OHLC-Preisfeld. Gleichstaende werden deterministisch aufgeloest. Das Datum
des letzten Datenbars ist kein nachtraeglich erfundener Scanzeitpunkt.

Die bisherige Henkelberechnung kann ihr tiefstes Tief auf der Ausbruchskerze
haben. Die Evidenz benennt diesen Fall statt Tief und Schluss derselben Kerze
zu verschmelzen. Widerspruechliche Reihenfolgen bleiben sichtbar; sie werden
nicht fuer eine schoene Zeichnung umsortiert. Keine neue Handelssignalregel.

Alte Cachezeilen ohne gespeicherte Anker bleiben als solche erkennbar.
Eine erneute Mustersuche mit heutigen Chartdaten waere kein Nachweis fuer
den damaligen Scannerfund und findet deshalb im Frontend nicht statt.

## Darstellung und Vergleich

Beim Oeffnen eines Cup-Treffers wird der gesamte Scannerstand an die
Detailansicht weitergegeben; der Anfangs-Timeframe ist 1D. Ein anschliessender
bewusster Timeframe-Wechsel bleibt erhalten. Ankerpunkte werden nur auf
passenden tatsaechlichen Tageskerzen desselben Symbols verortet. Die Preise
muessen zum gespeicherten OHLC-Feld passen. Fehlende oder abweichende
Chartdaten werden benannt und nicht durch plausible Punkte ersetzt.

Die datierte Legende bleibt fuer historische Funde nuetzlich. Spaetere
Chartkerzen erneuern weder den alten Scannerfund noch seine Handelsfreigabe.
Punktdarstellung und begrenzte Randlinie sind keine geglaettete Tassenkurve.
Der Anfangsausschnitt soll alle gespeicherten Punkte umfassen, nicht nur
die letzten 80 Kerzen. Auf anderen Timeframes werden keine 1D-Punkte geraten.

## Abnahme

Backend-Pruefungen: exakte Extrema/Datum/Originalpreise, Gleichstaende,
fehlende und verdrehte Sitzungen, Henkeltief auf der Ausbruchskerze,
begrenzte Serialisierung sowie unveraenderte bestehende Preise und Scores.
Frontend-Pruefungen: Ticker-/Timeframe-/Preisbasis-Bindung, alte und fehlende
Evidenz, echte Punktkoordinaten, historische Funde, keine veralteten Punkte
nach Auswahlwechsel, Desktop und Mobile.

`scripts/audit_cup_geometry_fixture.py` dient ausschliesslich lokaler
Browser-QA mit synthetischen Preisen auf Loopback. Keine API-Initialisierung,
echten Zugangsdaten, Marktanfragen, Scans, Mails oder Orders. Der sichtbare
QA-Fall ist insbesondere KEIN Chart oder Replay von EXPD.

Abschluss der lokalen Abnahme am 20.09.2026:

- Gesamtsuite: 5.412 bestanden, 4 Plattform-Skips, 505,49 Sekunden.
  Der isolierte Testlauf sperrt externe Verbindungen und SMTP; Testdaten
  liegen ausschliesslich unter einem eigenen lokalen Output-Verzeichnis.
- Vergleich gegen Ausgangsrevision 18b90a6: 30 Detektorfaelle
  (12 angenommen, 18 abgelehnt) und 30 vollstaendige Filterfaelle behalten
  alle bisherigen Ergebnisfelder. Nur die beschreibende Evidenz kommt hinzu.
- Gebautes Frontend: a9ab09af0204; Bundle-Pruefung und JS-Syntaxpruefung gruen.
- Echte lokale Browserpruefung: Desktop 1440x1000 und Mobil 390x844,
  Klick aus der Trefferliste, alle sieben Punkte samt Originaldatum/-preis,
  aufklappbare Legende, 1D-/4H-Wechsel sowie responsive Groessenaenderung.
  Mobile Breite folgt dem wirklichen Container; logischer Randabstand
  verhindert abgeschnittene Anker. Bestehender Benutzerzoom bleibt erhalten.
- Alte Evidenz, geaenderte Preisbasis und historische Scannerstaende zeigen
  getrennte Hinweise. Keine erfundenen Marker. Browserkonsole: keine Fehler;
  die vorhandene Tailwind-Produktionswarnung bleibt unabhaengig bestehen.
- Zusaetzlicher unabhaengiger Review von Datenvertrag und Resize-Lifecycle:
  kein blockierender Befund. Keine Signal-, Score- oder Mail-Gates geaendert.

Screenshots und XML-Testprotokoll liegen nur lokal unter `output/` und werden
nicht veroeffentlicht. Der eigene QA-Browser und Loopback-Server sind beendet.
Produktionsrollout, neuer Scan mit gespeicherten Ankern und Sichtpruefung
des konkreten EXPD-Funds bleiben getrennte, noch offene Nachweise.
