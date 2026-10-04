# BI-Backtester und Mail-Recovery – 04.10.2026

## Umfang und Ausgangsstand

Fortsetzung aus der tatsächlichen Arbeitskopie, Basis
`cabb5b17ee41dc93f40c0a43270ffe71e803b820`. Bestehendes fremdes Dokumentations-,
Deploy- und Test-WIP ist erhalten und gehört nicht automatisch zu diesem Paket.
Keine Serverinstallation umgestellt; keine produktiven Daten, Einstellungen,
Scans oder Mails durch diesen Auftrag verändert/ausgelöst.

## BI: Ursachen und Reparatur

Der alte Zähler `total_signals` zählt akzeptierte Handelspläne, nicht bereits
qualifizierte BI-Kandidaten. Eine Anzeige von null konnte dadurch sowohl
fehlende Historie als auch abgelehnte Pläne oder tatsächlich fehlende Setups bedeuten.

- Universumsauswahl: die jüngsten 50 abgeschlossenen Sitzungen vor dem
  Testzeitraum statt eines alten Warmup-Ausschnitts. Mindestvolumen entspricht
  dem gewählten Wert; kein zusätzlicher versteckter 500.000-Filter.
- Nur valide, abgeschlossene OHLCV. Fehlende Sitzungen und ungültige Zeilen
  werden nicht zu einem künstlich lückenlosen Analysefenster zusammengedrückt.
- Signalzeitpunkt ist die letzte damals vorhandene Kerze. Keine Signale vor
  dem Studienbeginn; die letzte abgeschlossene Signalkerze wird nicht ausgelassen.
- Abdeckung vor dem ersten Trade und vollständige Kandidaten-/Ablehnungskette
  gespeichert. Preis, Volumen, nicht berechenbare Indikatoren, BI-Prüfung,
  Handelsplan und tatsächlicher Einstieg sind getrennte Stufen.
- API validiert vollständige Rohzeilen vor Kürzung. Cache validiert kombinierte
  Zählerpartitionen; `UNRESOLVED` darf gefüllt oder ungefüllt sein, niemals
  gleichzeitig ein definitiver `NO_FILL`. Der 150-Zeilen-Cap betrifft nur die Tabelle.
- Offene zukünftige Folgekerzen sind keine historische Providerlücke.
  Echte fehlende Daten behalten Vorrang. Fehlende Diagnosen alter Berichte werden
  nicht als neu ermittelte Nullergebnisse ausgegeben.
- Modell- und Planherkunft erhalten; neue Cachekennung nur für BI.
  Andere Backtests und alte Dateien werden nicht gelöscht oder umetikettiert.
- Kompakte UI; Methodik und Prüfdaten standardmäßig geschlossen.
  Der BI-Zähler heißt jetzt „Ausgewertete Trades“, damit bereits gefüllte,
  aber noch nicht abschließend bewertbare Einstiege nicht als fehlend erscheinen.

BI bleibt bei mindestens 17/20 mit bestehenden harten Regeln. Handelsplan,
Risiko, Grade, Score und Mailfreigaben wurden nicht gelockert.

## Mail: reproduzierte Blockierungs- und Replay-Grenzfälle

Ein vor SMTP abgebrochener Render-/Autorisierungs-/Ownership-Schritt konnte
eine vorbereitete Reservierung dauerhaft zurücklassen. Deren vorhandene
Bereinigung wurde bisher vom regelmäßigen BG-Tracker nicht aufgerufen.

Jetzt gibt der Sender sicher unversuchte eigene Reservierungen frei, und der
BG-Job bereinigt nach 30 Minuten alte, unberührte Reservierungen. Kein Replay
und kein Versand durch die Bereinigung.

Die unabhängige Nachprüfung reproduzierte außerdem elf Fehlerfälle:

- PREPARED mit vorhandenem Attempt-Stempel konnte erneut geclaimt werden.
  Snapshot, Auswahl und CAS-Update schließen dies jetzt aus.
- Ein alter Sender konnte einen frisch erzeugten Ersatzowner desselben Keys
  löschen. Cancel bindet an ursprüngliche ID, Prepared-Zeit und exakten Key.
- Bei Legacy-PREPARED konnten die Trackerzeilen zu einer separat journalisierten
  SMTP-Annahme gelöscht werden. Das Annahmejournal blieb erhalten, doch die
  Aktivierung/Zuordnung konnte scheitern. Journalprüfung und kurze Journal→Tracker-Sperren schützen Claim,
  Cancel und Cleanup auch über zwei echte SQLite-Dateien hinweg.
- Ungültige Prepared-Zeitstempel bleiben erhalten. Zwei echte SQL-Gegenproben
  zeigten, dass SQLite etwa den 30. Februar normalisiert und dadurch ungültige
  Datensätze bereinigen konnte. Eine strikte Datumsprüfung verhindert das jetzt;
  die beiden zuvor roten Fälle bestehen nach der Reparatur.

Cleanup und unsichere Pre-SMTP-Abbrüche halten ATTEMPTED zurück. Nur eine
ausdrücklich belegte definitive Nichtannahme darf der Sender freigeben.
Unklare DATA-Ergebnisse und bestätigte Annahmen werden nicht automatisch
gelöscht oder erneut gesendet. Bei Journalfehler bleiben Daten erhalten.
Keine dieser Sperren wird während SMTP oder Providerabrufen gehalten.

Dies sind ausführbar belegte mögliche Versandblocker. Die gerade sichtbaren
legitimen Ablehnungen vor SMTP werden dadurch nicht pauschal als Fehler erklärt.

## Prüfungen und Nachweise

- Mail: **445 bestanden**, 0 Fehler/Skips, darunter 71 Liveness-/Ownership-/Journalfälle
  und drei echte Zwei-Dateien-SQLite-Races: `tmp/qa-1c845373850f/results.xml`.
  Ein vorheriger Lauf hatte einen Windows-Multiprocessing-Queue-Timeout:
  444 bestanden, 1 Fehler (`tmp/qa-d6f243222f3c/results.xml`). Ohne Änderung
  oder Ausschluss bestanden anschließend der isolierte Atomic-Testlauf (10/10,
  `tmp/qa-74cc3dcb89d4/results.xml`) und der gesamte unveränderte 445er-Lauf.
  Die zwei roten Datums-Gegenproben stehen in `tmp/qa-1726bcf64525/results.xml`;
  der gezielte Nachlauf bestand 94 Tests (`tmp/qa-1945060641e4/results.xml`).
- BI-Producer und Nachbarn: **151 bestanden**: `tmp/qa-0dcbc42d6111/results.xml`.
- Frontend/API-Proben: **89 bestanden**: `tmp/qa-2a6ab06b0bc5/results.xml`.
- Finale Cache/API-Proben: **45 bestanden**: `tmp/qa-1b9e3f716fe4/results.xml`.
- Breiter erster Lauf: 11.264 bestanden, 1 rot, 1 übersprungen. Die rote alte
  Quelltextprüfung zählte nur `if day_data is None:` und erkannte eine kombinierte
  None-/Typprüfung nicht. AST-Prüfung erhält die drei erforderlichen None-Guards;
  gezielter Nachlauf **67 bestanden**: `tmp/qa-6bc64dc86c4a/results.xml`.
- Ein Zwischen-Gesamtlauf bestand 11.271 Tests, 1 übersprungen:
  `tmp/qa-a941cba59aae/results.xml`.
- Abschließender eingefrorener Gesamtlauf nach Datums- und UI-Nachprüfung:
  **11.274 bestanden**, 0 Fehler, 1 übersprungen, 588,92 Sekunden;
  `tmp/qa-1b8536a9bad0/results.xml` (11.275 Fälle einschließlich Skip).
- Die überlappenden gezielten Läufe werden nicht addiert.
- Gesamtlauf verwendet den vorhandenen Offline-Launcher: Fake-Zugangsdaten,
  neue private Zustandsdateien und gesperrte externe Netze/SMTP.
  Ausgeschlossen sind vorhandenes fremdes/retiriertes Deploy-Test-WIP sowie
  `test_calendar_and_crypto_safety.py` und `test_commerce_hardening.py`.
- Ein übersprungener POSIX-Dateirechtetest ist unter Windows nicht sinnvoll.
  Die AnyIO-Importwarnung ist bekannt; kein Testfehler.
- Playwright CLI: **18 Fälle**, Desktop 1440 und Mobil 390 Pixel, neun Zustände.
  Keine Schreibaufrufe/externe Requests/Laufzeitfehler; Details geschlossen;
  kein horizontaler Überlauf. Root hat Screenshots visuell geprüft.
  Private Artefakte: `output/playwright/bi-diag-20261004-1791146219910/`.
  Synthetische Daten, keine historischen Marktrenditen oder echte Signalzeilen.
- Frontend-Bundle `67924e9f894a` gebaut und verifiziert.

## Tatsächlich gelesener Betrieb und verbleibender Abschluss

Angemeldete App am 04.10. rein lesend geprüft. Der gespeicherte BI-Lauf vom
03.10., 21:47:24 (Zürich), 3 Monate/200 Aktien/Preis 5/Volumen 200.000 zeigt
0 Pläne und 0 Trades, enthält aber keinen Kandidaten-/Ablehnungstrichter.
Die exakte Ursache dieses alten Laufs ist daraus nicht ableitbar. Nach dem
Betreiberpull ist ein neuer BI-Lauf mit den neuen Diagnosen erforderlich.

Die aktuelle Admin-Mailansicht zeigt 50 vorab ausgelassene Entscheidungen,
keine SMTP-Annahme und keinen Versandfehler im sichtbaren Fenster. Darunter
Krypto-Freigabe-, Score-/Grade-/No-Chase- und Kontextgründe. Am Sonntag sind
Aktien-Autoläufe pausiert; nächster erlaubter Beginn 05.10., 04:00 UTC.
Kein Beleg eines allgemeinen SMTP-Ausfalls. Technische Testmail laut Nutzer
angekommen; eine neue echte Handelssignal-Mail ist hier nicht bestätigt.

Letzte Live-Health: 04.10., Serverzeit 18:57:02, healthy,
Revision `cabb5b17ee41`, Bundle `17410b75a42c`. Der Server wurde nicht aktualisiert.
Nach normalem Betreiberpull: BI-Lauf neu berechnen, echte zulässige Signalannahme
und persönlichen Empfang sowie die korrigierte Wochenkohorte getrennt prüfen.
Keine neue Testmail oder wiederholter Export für die lokale Reparatur verlangt.
