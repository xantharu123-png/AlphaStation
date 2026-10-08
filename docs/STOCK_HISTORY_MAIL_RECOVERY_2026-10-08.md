# Historienabbrüche vor dem Signal-Sender – 08.10.2026

## Anlass und aktueller Livebeleg

Der Nutzer erhält weiterhin keine regulären Signal-Mails; eine früher ausdrücklich
freigegebene technische Testmail kam an. Diese Runde sendet keine weitere Testmail,
startet keinen Produktionsscan und ändert keine Kontoeinstellungen.

Im bereits angemeldeten Nutzer-Tab wurde Admin → Mailversand gelesen. Öffentliche
Health-Abfrage: `healthy`, Revision `df04ee0813bb`, Bundle `8f8a6c0c5bae`.
Das vorherige Diagnosepaket ist damit auf Hetzner, nicht nur lokal vorhanden.
Diese Runde hat den Server nicht aktualisiert.

- Cup-Versuch 08.10., 10:03:34 (Browseranzeige): `scan_data_unavailable`, Phase
  Historie laden; 4408/12586 geprüft, 576 Providerrequests, 71 Cachehits,
  Strategie 240s, Historie 200,7s, Struktur 20,1s. Noch kein neuer Ergebnisstand.
- Aktien-Sammellauf 10:03:35: Fehler, 13 Kandidaten. Sender-/Transportzähler
  fehlen (`—`), nicht als gemessene Null interpretieren.
- Momentum-Blattlauf 09:59:32 abgeschlossen. Blattläufe im Sammellauf senden
  absichtlich nicht selbst; der Eigentümer versendet gemeinsame gültige Ergebnisse.
- Mailansicht 10:31:08: 1 Swing- und 1 Crypto-Empfänger, 0 SMTP-Annahmen,
  2 ausgelassene Kryptoentscheidungen, 0 Versandfehler, 0 Warteschlange. Dieses
  seit Prozessstart begrenzte Fenster ist keine Wochen-/Tagesbilanz.
- RELL: Score 95 und Tagesqualität 86/96; die sichtbare Sperre betrifft den
  bestehenden 2-Mio.-USD-20T-Median-Liquiditätsfloor, nicht den offenen Rücktest.
  Der genaue historische Median und die Originalkerzen sind in der UI nicht
  enthalten. Andere sichtbare Momentum-Kandidaten haben niedrigen Trade-Score
  oder unabhängige nahe Gegenbarrieren. Kein belegter Grund, Grenzen zu löschen.
- Ein rein lesender SSH-BatchMode-Versuch wurde abgewiesen. Der konkrete
  ursprüngliche Cup-Transport-/HTTP-Grund ist deshalb nicht nachgewiesen.

## Reproduzierte Fehler und Reparatur

### Turtle: Tagesdaten vor dem falschen Cutoff validiert

Der eigene Validator verlangte immer ein `results`-Array und prüfte sämtliche
OHLCV vor dem Ausschluss noch nicht verfügbarer Tageskerzen. Eine vom Anbieter
positiv bestätigte leere Antwort ohne optionales Array oder ein fehlerhafter
noch nicht verwendbarer Tagesbar konnte den gesamten Lauf und gültige Geschwister
abbrechen. Daneben wurden doppelte/zukünftige Zeitstempel und widersprüchliche
Ergebniscounts nicht ausreichend zurückgewiesen.

Turtle nutzt jetzt den bereits vorhandenen strengen täglichen Parser mit fester
Analyseuhr und letzter verfügbarer Sitzung. Envelope, Counts, alle Zeitstempel,
aufsteigende Reihenfolge und Zukunftsgrenze bleiben für sämtliche Bars Pflicht.
Nur OHLCV von noch nicht verfügbarer Sitzung können die vorherige abgeschlossene
1D-Analyse nicht beeinflussen. Die zusätzliche Splitadjustment-Prüfung und der
Abgleich mit den unabhängigen gruppierten Tagesdaten bleiben erhalten.
Erforderliche fehlerhafte abgeschlossene Daten oder Anbieterfehler verhindern
weiterhin eine neue Veröffentlichung; der letzte gute Cache bleibt unverändert.

### Strikter Aktien-Historienabruf: keinerlei transiente GET-Wiederholung

Ein einzelner Timeout, Verbindungsabbruch oder HTTP 500/502/503/504 beendete
sofort den erforderlichen Historienabruf und damit den betreffenden Lauf.
Der gemeinsame Rate-Limiter stellt keine Wiederholung bereit.

Die Änderung wiederholt ausschließlich diese klar benannten vorübergehenden
GET-Fehler, maximal drei Versuche pro Abrufoperation mit 0,5s/1s Backoff.
Alle Zusatzrequests teilen dasselbe bestehende 20er-Budget mit den OHLCV-
Nachprüfungen und anderen Symbolen des Eigentümerlaufs. URL, Query, Cutoff,
Analyseuhr, Rate-Limiter, Deadline und Pause-/Abbruchpunkte bleiben gleich.
Die bestehende einmalige OHLCV-Nachprüfung ist eine separate Abrufoperation;
auch ihre möglichen Transportwiederholungen verbrauchen das gemeinsame Budget.

401/403/429, TLS-Fehler, ungültiges JSON, Schema-/Count-/Zeitstempelfehler
erhalten keine Transportwiederholung. Dauerhaft fehlende Pflichtdaten bleiben
ein globaler Fehler, kein stiller Symbolausschluss. Keine Kerze wird erfunden,
entfernt oder teilweise repariert. Freigabe-, Score-, Qualität-, Barrieren-,
Entry-/Stop-/Ziel- und SMTP-Regeln sind unverändert.

## Prüfung

- Turtle RED: **12 fehlgeschlagen, 9 bestanden**;
  `output/mail-fix-qa-ce4f4a660ce5420783501711eeb683dd/results.xml`.
- Transport RED: **14 fehlgeschlagen, 11 bestanden**;
  `output/mail-fix-qa-a369008db6ea4fbb957b596c2de3adc7/results.xml`.
- Nach minimaler Reparatur: **46 neue Fälle bestanden**;
  `output/mail-fix-qa-0c8e297b69f34b02a46fdb0e4e00953a/results.xml`.
- Bestehende historische/Scanner-/Mailregressionen: **414 bestanden**;
  `output/mail-fix-qa-3299b9196e1747f49dbd0a12ce9b7d5c/results.xml`.
- Neue End-to-End-Varianten und zugehörige Regressionen: **109 bestanden**;
  `output/mail-fix-qa-ccb79c699afa41dbad3955591d791900/results.xml`.
  Vier neue Varianten LONG/SHORT × Timeout/503 laufen durch den echten strikten
  Fetcher, Parser, nativen Plan, Tagesqualität, letzten Guard und Sender bis zu
  ausschließlich simuliertem SMTP und einem dauerhaften
  `trade/email/stocks_swing/ACTIVE`-Eintrag. Genau eine Annahme; WATCH genügt nicht;
  kein Broker-Fill wird behauptet. Daten-/Kontextanbieter, Uhr, Cachepfade und SMTP
  sind kontrolliert; kein Levelbuilder, Score, Qualitäts- oder Freigabeguard ersetzt.
  Diese Testmengen überlappen und werden nicht summiert.
- Unabhängige lesende Prüfung beider Produktionsänderungen und der vier neuen
  Senderfälle: keine konkreten Blocker; separater Review-Versuch hing in der
  bekannten Windows-Sandbox-Importgrenze und wurde beendet. Die erfolgreichen
  Testläufe stammen vom Hauptagenten mit dem gesicherten Offline-Launcher.
- Frontend unverändert; Bundleprüfung bestätigt `8f8a6c0c5bae`.
- Exakter Git-Index-Snapshot: Tree `66fa66cbfd1396efae51ae4662f20361f0ff2703`.
  Frischer breiter Produktlauf: **12.124 bestanden, 2 übersprungen, 0 Fehler/Errors**,
  439,73s. XML:
  `output/release-verification-20261008-history-ba1ba993afc1/qa-fc5d4c279ef4/results.xml`.
  Nur `test_deploy_auto_update.py`, `test_deploy_migration.py`,
  `test_deploy_retirement.py`, `test_deploy_security_reaudit.py` ausgenommen;
  vererbte Calendar-/Commerce-/Deploy-Dateien aus unverändertem HEAD.
- Produktcommit **`352ea08b07ca82164eec353b064c85fa4c2133e6`** enthält genau die
  sechs eigenen Produkt-/Testdateien; Tree stimmt mit dem geprüften Snapshot überein.
  Auf `origin/main` gepusht und dessen SHA abgeglichen. Dokumentation folgt separat.
  Kein Serverupdate; der letzte bestätigte Live-Stand bleibt `df04ee0813bb`.

## Grenzen und nächste Nachweise

Die Reparaturen schließen nachgestellte fehlerhafte Datenabbrüche. Es ist nicht
bewiesen, dass der heutige Cup-Providerfehler exakt einer der transienten Kategorien
entspricht oder diese Fehler allein alle wochenlang fehlenden Signal-Mails erklären.
Offline-Annahme und Testjournal sind keine echte SMTP- oder Postfachzustellung.
Nach Operatorinstallation ist ein neuer vollständiger automatischer Lauf bis
Signal-Freigabe → letzte Prüfung → Sender → SMTP → Postfach noch zu belegen.

Ein separater Cup-Kohärenzfehler bleibt offen: Cup ersetzt seine Planpreise,
lässt vorherige native Entscheidungsfelder aber bestehen. Das beweist inkonsistente
Metadaten, nicht einen vollständig qualifizierten fälschlich gesperrten Cup-Trade.
Finalen Plan gegen echte unabhängige Zonen reproduzieren; kein pauschales Entfernen
physischer Barrieren oder Ändern der Freigaberegel für gemessene Cup-Ziele.

Geschütztes fremdes Deploy-/Installations-/Calendar-/Commerce-/Handbuch-WIP,
private Exporte und laufende Serverdienste wurden nicht verändert oder veröffentlicht.

## Verwendete Arbeitsverfahren

Systematic Debugging lokalisierte den aktuellen Fehler vor SMTP; Test-Driven
Development verlangte rote reale Parser-/Fetcher-Reproduktionen vor der Änderung.
Dispatching Parallel Agents trennte Reproduktion und unabhängige Prüfung;
Requesting Code Review prüfte den tatsächlichen Diff. Verification Before
Completion verlangt den frischen exakten Release-Test vor Commit/Push.
Using Superpowers steuerte die Auswahl dieser Verfahren; keine neue Produktpolitik.
