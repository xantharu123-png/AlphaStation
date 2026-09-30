# Scanner-Nachprüfung: Reparaturen vom 30.09.2026

Basis: `b6be1f2735ef303ae1cb066afbb1b8acf2d726b5`, Branch `main`.
Auftrag: die neun reproduzierten Ursachen aus der erneuten Scannerprüfung
beheben und die korrigierten Abläufe einschließlich des Versands nachprüfen.
Die ursprünglichen 18 fehlgeschlagenen Testfälle betreffen neun Ursachen,
nicht 18 voneinander unabhängige Fehler.

## Implementierte Korrekturen

| Befund | Korrektur | Entscheidende Gegenprobe |
| --- | --- | --- |
| R1 ORB-Freigabe nach Kursrücklauf | ORB-Fachregeln nochmals nach der finalen Bid-/Ask- und Pfadprüfung, auf deren Preis und Zeitpunkt | Long/Short, wieder innerhalb der Range oder exakt auf der Grenze: kein Senderaufruf; gültiger Ausbruch bleibt sendbar |
| R2 Negative Biotech-Nachricht positiv bewertet | Negation vor und nach dem Ereignis, Passivformen, Kontraktionen und Satzgrenzen | Ablehnung/nicht erreichte Endpunkte geben keine positiven Punkte; echte Zulassung und erreichter Endpunkt bleiben positiv |
| R3 ORB-Ausfall als frisches Nullresultat | Fehlende Pflichtfeeds und vollständiger Ausfall der ausgewählten 5m-Historien erzeugen Datenfehler vor dem Cache-Schreiben | Alter Cache wird nicht durch scheinbar erfolgreiche Null ersetzt; einzelne Ausschlüsse bleiben gezählt und gültige Werte nutzbar |
| R4 Turtle mit leerer neuer Sitzung | Vorauswahl und Planberechnung aus denselben abgeschlossenen, datierten und adjustierten Tagessitzungen; Bulk/Einzelhistorie abgeglichen | Leere oder extreme neue Snapshot-Sitzung ändert den Tagesplan nicht; abweichende Kurse, Volumen, Sitzung oder Adjustierung sperren die Veröffentlichung |
| R5 Offene Chartkerze bestätigt OB | Allgemeine Chartmuster, Harmonische und zugehörige Trendfilter verwenden abgeschlossene Kerzen zum eingefrorenen Chartzeitpunkt | Offene 4H-Impulskerze bestätigt keinen Block; offene 5m/15m/1H/4H-Kerze entwertet keinen bereits bestätigten Block |
| R6 Spätere Volatilität verändert historische Erkennung | FVG- und OB-Schwellen aus dem verfügbaren historischen Präfix, nicht dem späteren Gesamtdatensatz | Bullish/bearish: spätere extreme Kerzen ohne Zonenkontakt verändern die ursprüngliche Identität/Stärke nicht |
| R7 FVG-Sprung als Auffüllung | Tatsächliche OHLC-Überlappung, Berührung, Teilauffüllung, vollständige Auffüllung und Entwertung getrennt | Sprung über die Zone ist keine Auffüllung; Rückkehr reaktiviert eine entwertete Zone nicht; getrennte gehandelte Bereiche füllen keine unbekannte Mitte |
| R8 Biotech-Publikation in der Zukunft | Vollständige zeitzonenbewusste Publikationszeit gegen eingefrorenen Auswertungszeitpunkt | Zukunft auch um eine Mikrosekunde ausgeschlossen; fehlende/ungültige/naive Zeit separat gezählt; identischer Zeitpunkt in anderer Zeitzone zulässig |
| R9 Penny-Ersatzkurse in Rückrechnung | Vollständige, endliche numerische OHLCV-Werte erforderlich; keine Close-/Null-Ersatzkerzen | Fehlendes Open/High/Low, boolesche Werte, NaN/Inf bleiben Datenfehler; unbekannte Kerze vor späterem TP darf keinen Gewinn erzeugen |

Zusätzlich im vollständigen ORB-Versandtest reproduziert und repariert:
Der Produzent liefert `target1/target2`, der Signal-Tracker kannte nur
`TP1/tp1` und `TP2/tp2`. Dadurch schlug die Vorbereitung des Zustellungsintents
auch für einen gültigen ORB-Plan fehl. Die nativen Aliasnamen werden jetzt
unverändert übernommen. Fehlende Ziele bleiben fehlend; es gibt keine
Ersatzwerte oder gelockerte Geometrieprüfung.

## Fachliche und technische Grenzen

- BI bleibt bei **mindestens 17 von 20**. Wegen der korrigierten Faktoren
  15/16 trägt neu berechnete Evidenz `stock-bi-20-v5`; Altstände erfüllen
  den aktuellen BI-Vertrag nicht allein durch einen neuen Cachezeitstempel.
- Biotech trägt `biotech-news-v2`. Alte Newsbewertungen werden weder im
  Quick-Scan ungeprüft übernommen noch als aktuelle Trade-/Mailfreigabe
  ausgegeben. Eine Warnanzeige bleibt von einer Freigabe getrennt. Der
  Ablehnungsgrund ist in Klassifizierung, Diagnose und Export registriert.
- Der Background-Entry-Sender bleibt unverändert gesperrt: Die API ist
  weiterhin der einzige Stock-Entry-Versandpfad mit finaler Revalidierung.
- FVG/VI/OG bleiben unterschiedliche Formationen. Eine spätere dritte Kerze
  schreibt nicht rückwirkend den Typ oder Entstehungszeitpunkt einer
  früheren Zwei-Kerzen-Zone um.
- Die OB-Impulsbasis ist eine durchschnittliche Kerzenspanne, **kein ATR**.
  Die Beschreibung wurde entsprechend berichtigt. OHLC-Überlappung ist
  ebenfalls kein Beleg für einzelne Ausführungen an jedem Zwischenpreis.
- Turtle-Snapshotwerte bleiben separat bezeichnete Kontextfelder; sie
  werden nicht als bestätigter Tageskurs oder ausgeführter Einstieg verwendet.
- Fehlende oder widersprüchliche Pflichtdaten werden nicht durch weichere
  Signalbedingungen überdeckt. Der gemeinsame gewichtete BI-Score ersetzt
  weiterhin nicht die 17/20-Regel.

## Regressionstests

Regulär versioniert:

- `test_scanner_reaudit_regressions.py`: ursprüngliche adversariale
  FVG-/OB-/ORB- und Chartfälle, einschließlich echtem ORB-Produzenten,
  Klassifizierung und finaler Revalidierung.
- `test_cross_scanner_reaudit_regressions.py`: Biotech, Turtle und Penny
  sowie zusätzliche mathematische Gegenproben für ADX, Momentum, Cup,
  Wyckoff, Elliott und Krypto-Pfade.
- `test_scanner_reaudit_hardening.py`: Randwerte, Negationen, Zeitgrenzen,
  unbrauchbare Daten, Teilabdeckung und kompletter ORB-Sender mit
  isoliertem SQLite-Tracker und simuliertem SMTP.

Der positive ORB-Versandtest prüft Long und Short bis zum `ACTIVE`-Eintrag
nach simulierter SMTP-Annahme, einschließlich exakt erhaltener
Entry-/Stop-/TP-Werte. Beim Rücklauf in die Range entsteht kein Versand
und kein vorbereiteter Tracker-Datensatz.

Ein vorhandener synthetischer BI-Positivtest musste sachlich korrigiert
werden: Sein alter Orderblock entstand nur durch später gesunkene
Volatilität. Am tatsächlichen Entstehungszeitpunkt waren 0,7031 Impuls
kleiner als 1,5 × 0,4943 Kerzenspanne. Der Verlauf ist jetzt ein negativer
16/20-Kontrollfall. Eine zusätzliche echte, ausreichend starke
Impulsvariante erreicht weiterhin 17/20. Keine Produktionsschwelle wurde
für einen grünen Test reduziert.

Alle Tests laufen mit gesperrtem externem Netzwerk/SMTP, unechten
Zugangsdaten und je Lauf neuen isolierten Datenbanken. Private Exporte
und Laufartefakte unter `output/` gehören nicht zum Commit.

### Abschließende lokale Abnahme

- **9.611 bestanden, 0 Fehler, 5 Plattform-Skips**, vollständiger frischer Lauf
  mit unverändertem Code in 349,90 Sekunden. Nachweis:
  `output/mail-fix-qa-e05b4db392ec4051b787442f0b359703/results.xml`.
- SHA256 von **392 Python-Dateien** vor/nach dem Lauf identisch; keine
  fehlenden oder zwischenzeitlich geänderten Quelldateien.
- Die drei neuen Testdateien enthalten **224 bestandene Gegenproben**.
  Die bisherigen betroffenen Ablaufprüfungen bestanden separat mit 359 Tests;
  Gegenproben einschließlich Diagnose/Export bestanden mit 584 Tests.
  Diese Läufe überschneiden sich und werden nicht addiert.
- Alle 20 geänderten/neuen Python-Dateien kompiliert; `git diff --check`
  fehlerfrei. Frontend unverändert, Bundle **`4379c5dca540`** verifiziert.
- Die fünf Skips betreffen Windows-Symlinkrechte bzw. POSIX-/Linux-Dateisystem-
  Verträge, nicht übersprungene Scannerfälle. Die einzige Warnung betrifft
  das bereits importierte `anyio`-Modul des isolierten Teststarters.
- Private Exporte, Datenbanken und Zugangsdaten nicht im Reparaturpaket.
  Hinzugefügte Zeilen und neue Paketdateien auf Credential-Signaturen geprüft.

Frühere unterbrochene oder fehlgeschlagene Läufe sind keine Abnahme. Dabei
wurden veraltete positive ORB-/Biotech-Testdaten an den aktuellen Produzenten-
Nachweis angepasst und der neue Biotech-Diagnosecode im Export ergänzt.
Ein zwischenzeitlicher Lauf hatte außerdem durch gleichzeitiges Editieren
veraltete `inspect.getsource()`-Zeilenpositionen; der obige eingefrorene Lauf
ersetzt diesen Nachweis vollständig. Keine Schutzregel wurde gelockert und
kein fehlgeschlagener Scannerfall deaktiviert.

Das geprüfte Paket ist für Commit und Push freigegeben. Die Reparaturrevision
ist über den Git-Commit dieser Datei nachzuvollziehen; der Produktionsnachweis
bleibt davon getrennt.

## Produktionsstand / Rollout

In diesem Reparaturauftrag wurde kein Serverupdate, Dienstneustart oder
echter SMTP-Versand ausgelöst.
Ein anschließender rein lesender SSH-Versuch mit `BatchMode=yes` wurde mit
`Permission denied (publickey,password)` abgewiesen. Daher wurden auch
Eigentümer, Dienststatus und Health nicht als neu live verifiziert ausgegeben.

Der zuletzt vom Nutzer ausgeführte `safe_deploy.sh`-Aufruf wurde bereits
bei der Quellvertrauensprüfung gestoppt:
`/home/tradingbot/app must be root-owned and not group/world-writable`.
Dieser Abbruch liegt vor Pull und Neustart. Er ist nicht durch rekursives
`chown`, Abschalten der Prüfung oder einen gewöhnlichen Pull/Neustart zu
umgehen. Die read-only Rechte-/Pfadinventur fehlt noch; anschließend gilt
der bestehende geprüfte Migrationsweg gemäß `deploy/SERVER_WARTUNG.md`.

Nach sicherem Rollout: exakte API-/Git-Revision, Dienste und Bundle prüfen;
neue vollständige Scannerläufe auswerten und echte Signalzustellung im
Zustellungsjournal mit der Empfängerbeobachtung abgleichen. Historische
Tracker-Datensätze werden nicht rückwirkend umgeschrieben.
