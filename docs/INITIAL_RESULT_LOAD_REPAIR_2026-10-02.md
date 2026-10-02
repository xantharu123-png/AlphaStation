# Erstaufruf: gespeicherte Scanner-Ergebnisse ohne Reload

## Befund und Umfang

Der Betreiber meldete am 02.10.2026 eine anfangs leere Scannerseite, deren
gespeicherte Treffer erst nach einem Browserreload erschienen. Codeprüfung,
isolierte Gegenprüfungen und ein lokaler Browserablauf reproduzieren mehrere
Ursachen. Die genaue gemeldete Produktionslatenz wurde nicht gemessen.

- Der erste Ergebnis-GET und sein JSON-Body hatten keine Zeitgrenze.
- Ein transienter erster Lesefehler wurde ohne neue Schedulerrevision nicht
  erneut gelesen; passive Aktualisierungen konnten laufende Reads abbrechen.
- Die Ergebnisdekoration konnte selbst ein leeres Resultat um einen externen
  Instrumentdatenabruf verlängern. Fehlende Firmennamen lösten Referenzseiten
  aus; die nachgelagerte Tabellenklassifikation enthielt denselben versteckten
  Providerpfad noch einmal.
- Synchrone Auth-/Kontodatenprüfungen liefen in asynchroner Middleware und
  Account-GETs direkt im Eventloop und blockierten unabhängige API-Reads.

## Korrektur

Der gemeinsame `useScannerFeed` begrenzt Request UND Body auf 20 s. Passive
Reads teilen einen laufenden Abruf und sammeln maximal eine Folgeabfrage.
Netzwerk-/Body-Transportfehler, Timeout, 408/429 und 5xx außer 501 werden ohne
Reload erneut gelesen: 1,5/5/15/30 s Backoff, mit Vorrang für `Retry-After`.
Auch echte Schedulerrevisionen umgehen diese Frist nicht. Fristen jenseits
der JS-Timergrenze werden nicht auf einen sofortigen Retry verkürzt.
401/403 und falsches JSON/Schema bleiben terminal. Scopewechsel und Unmount
räumen Timer/Reads auf; eine andere Run-ID ersetzt keine Abschlussbestätigung.

Vorhandene finale Treffer bleiben bei Hintergrundreads/Fehlern erhalten und
als alter Stand gekennzeichnet. Fehlerhafte Teilstände werden verworfen,
nicht zu finalen Treffern oder erfolgreichen Nullscans umgedeutet. Private
Transportfehlermeldungen werden nicht im Frontend veröffentlicht.

Ergebnisdekoration und Tabellenklassifikation verwenden ausschließlich
gespeicherte Instrumentmetadaten. Firmennamen bleiben verfügbar, unbekannte
Instrumente werden nicht zu freigegebenen Aktien, ETF-/Produktfilter bleiben
aktiv. Fehlt jeder Identitätsnachweis, liefert der Read 503 statt eines
erfolgreichen Nullergebnisses; der Browser wiederholt ihn automatisch.
Alte Metadaten werden für die Anzeige nicht künstlich verjüngt. Ein
RLock/CAS verhindert, dass ein älterer Displayread neuere Worker-Metadaten
überschreibt. Netzwerkfähige Scanner-/Maildefaults und die strikte
diagnostische Referenzprüfung bleiben unverändert.

Auth-/Planchecks und initiale Account-GETs nutzen Starlettes Threadpool.
Token-, Cookie-, Admin-, Plan-, read_only- und Throttleentscheidungen bleiben
dieselben; ContextVars und pro Aufruf erzeugte SQLite-Verbindungen sind geprüft.

## Nachweise

- Auth: 13 reproduzierbar rote Barrierentests vorher,
  `tmp/qa-718ec20233e0/results.xml`; 184 gezielte Tests danach grün,
  `tmp/qa-70033e8d0779/results.xml`.
- Frontend: Original `1c3fb68` mit 11/16 neuen Fällen rot,
  `tmp/qa-4e10d908d404/results.xml`; 18 ursprüngliche Recoveryfälle grün,
  `tmp/qa-b6b097e54440/results.xml`. Zusätzlicher Privacyfall hinzugefügt.
- Referenz-Read: 8/11 Gegenprüfungen vor Korrektur rot,
  `tmp/qa-7beb308e177d/results.xml`. Verdeckter zweiter Providerpfad separat
  nachgewiesen und korrigiert. Zwei CAS-Gegenfälle zunächst rot,
  `tmp/qa-d33ebb2af24f/results.xml`, danach grün in 26 gezielten Tests,
  `tmp/qa-61249bf590f9/results.xml`.
- Bestehende Lifecycle-/Scancontrol-/Authregressionen: 390 grün,
  `tmp/qa-18757fe9fc8d/results.xml`; geänderte Altvertragsfixtures prüfen
  zusätzliche Recovery, nicht weniger Zeilen-/Run-/Freigabesicherheit.
- Playwright-CLI, tatsächliches lokales Bundle `5f2a5271c188`, synthetische
  API-Antworten: erster GET 6,5 s verzögert → 503, Scheduler-Takt unterbrach ihn
  nicht. Retry nach 1,516 s; QAONE nach 8,643 s ohne Reload sichtbar. Späterer 503
  behielt Treffer mit Altstandhinweis; 390 px Mobilansicht ohne Überlauf.
  Null JS-Laufzeitfehler/Abbrüche/Schreibanfragen/externe Anfragen. Die zwei
  erwarteten 503-Consolemeldungen gehören zur Fehlerfixture.
  Artefakte: `output/playwright/initial-load-browser-{proof.json,report.md}`
  und drei `initial-load-*.png`; nur synthetische Daten, nicht Produktion.

## Abschließender Paketlauf

Code und Tests wurden aus dem vorgemerkten Index in einen eigenen lokalen
Quellordner exportiert, nicht gegen die übrigen geerbten Worktree-Änderungen
geprüft. Der Offline-Launcher verwendet isolierte Datenbanken/Runtimepfade,
synthetische Zugangsdaten und blockiert externe Provider- und SMTP-Aufrufe.
Nur die abschließende Statusdokumentation wurde anschließend ergänzt.

```powershell
$taskQaSource = 'C:\Projekt\TradingBot\tmp\initial-load-publish-0aee6be05a7045a7bb40bd0e8dbcec32\source'
$env:ALPHA_QA_SOURCE_ROOT = $taskQaSource
$env:ALPHA_QA_OUTPUT_ROOT = 'C:\Projekt\TradingBot\tmp'
$env:NODE_PATH = 'C:\Projekt\TradingBot\node_modules'
& '.\.codex_pytest_env\Scripts\python.exe' "$taskQaSource\scripts\run_offline_tests.py" -q --tb=short --ignore-glob='test_deploy*.py'
```

Ergebnis: **10.887 bestanden, 1 übersprungen, 0 Fehler** in 438,89 s.
JUnit: `tmp/qa-b3fbe27b64fa/results.xml` (10.888 Fälle, failures=0, errors=0).
Der Skip betrifft ausschließlich
`test_gap_scan_schedule::test_state_permissions_are_private_on_posix`:
Windows-Modusbits belegen keine POSIX-Dateirechte. Eine bekannte pytest-
Warnung betrifft das bereits importierte AnyIO-Modul.

Der erste vollständige Paketlauf hatte nur zwei veraltete Anzeige-Test-Doubles
mit fehlenden `cache_only`-/`display_only`-Parametern als Fehler. Diese prüfen
jetzt beide Read-only-Parameter ausdrücklich; sämtliche fachlichen Assertions
bleiben erhalten. Die 24 Fälle dieser Testdatei bestehen separat ebenfalls.
Der obige zweite Gesamtlauf prüft das korrigierte Paket vollständig.

Die ausdrücklich nicht gewünschte Deploy-Umstellung ist nicht Teil des Pakets;
`test_deploy*.py` wurde deshalb aus diesem Anwendungstestlauf ausgeschlossen.
Private Exporte, Browserartefakte und übrige geerbte Änderungen sind nicht
vorgemerkt. Der Test ist kein Produktionsscan oder Zustellnachweis.

Normalisierte SHA-256 (UTF-8, CRLF → LF), Worktree und eingefrorener Code gleich:

- `api.py`: `9f2728875057df703751250a98279fa54a7997ab794250f49411d4a37b9787d0`
- `frontend/index.html`: `a06c541c46f6528ec5fda3891e54d62780445607f5cd1c78e0cd188a0f603825`
- `frontend/app.bundle.js`: `505426d6c3d7a6dea58b3e97a4aca6529687dc99582938fd39de6a085ceb4208`

Korrekturcommit: `0b2ae89bd6b9308e66786ce8579d590476e273a1`.
`git push origin main` erfolgreich; `git ls-remote origin refs/heads/main`
bestätigte genau diesen Hash. Diese abschließende Übergabedokumentation
wird separat committet; sie verändert den geprüften Laufzeitcode nicht.
Keine Produktionsänderung durch den Git-Push.

## Grenzen und Betrieb

Keine Testmail, Scanstarts, Bestellungen, Einstellungs-, Cache-/DB-Löschungen
oder Serveränderungen. Eigene lokale Browser-/Serverprozesse beendet.
Der Anbieterzugang des Biotech-Abos und der separate Cup-Abruffehler werden
hierdurch nicht repariert. Biotech-Enrichment-GETs außerhalb dieses geprüften
Standard-Ergebnislesepfads können weiterhin Providerarbeit enthalten.
Echte neue Signalzustellung bleibt ein eigener offener Produktionsnachweis.

Der öffentliche API-Health-Read am 02.10.2026 um 17:28:20 (Serverzeitstempel)
bestätigt `healthy`, Revision `1c3fb68f51df`, Frontend `806260a08809`.
Die neue Erstlade-Korrektur ist auf diesem Stand noch nicht installiert.
Der vorangegangene Health-Versuch auf Frontendport 3000 lieferte 404;
das war kein Nachweis eines API-Ausfalls. Die API liegt hier auf Port 8000.
Manueller normaler Pull nach Veröffentlichung und nach Abschluss laufender
Scans/SMTP, kein Deployskript und kein Installationsumbau.
