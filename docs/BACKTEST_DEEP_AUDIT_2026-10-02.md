# Backtest-Tool: tiefer Audit und Reparatur vom 02.10.2026

## Umfang und Ausgangspunkt

Ausgangsstand: 4ef6c9b284825686fef60913a2e932a8f9414202.
Das vorhandene Backtest Center wurde vor der Reparatur gegen neue Gegenfälle
geprüft: klassische Indikatormodelle, tägliche Regel-/Universumsmodelle,
BI/Biotech und die beiden Krypto-Richtungen; dazu Quellen, Ausführung,
Kennzahlen, Holdout, Cacheidentität und Lebenszyklus der tatsächlichen Oberfläche.

Kein neuer Forex-/Cup-/Wyckoff-/Elliott-/ORB-Backtester wurde erfunden.
Registrierte, aber nicht implementierte Live-Scanner sind keine stillschweigend
validierten Backtestmodelle. modules/momentum_daily_backtest.py wurde geprüft,
aber nicht verändert. Die bekannte Modell-/Live-Abgrenzung bleibt erhalten.

Alle neuen Regressionen liefen offline: künstliche Kurse, temporäre QA-Daten,
keine tatsächlichen Provideraufrufe, SMTP-Nachrichten, Aufträge oder Serveränderungen.
Die älteren privaten Dreimonats-Stichproben unter
output/scanner-deep-audit-20261002/ bleiben Ergebnisse ihrer damaligen Codeversion;
sie werden nicht als mit diesem Rechenkern erneut berechnet ausgegeben.

## Nachgewiesene Fehler und Korrekturen

| Bereich | Reproduzierter Fehler | Korrektur |
| --- | --- | --- |
| Rohpräzision | Fillpreis, Netto-P&L und R vor Aggregation gerundet; kleine Gewinne/Verluste konnten zu Null werden. | Ungerundete Berechnung/Vergleiche; Rundung nur in der Anzeige. Gewinn/Verlust folgt Netto-P&L, nicht widersprechendem Flag. |
| Einstieg/Ausführung | Boolesche OHLC-Werte, offene Kerzen, negative Indizes und bestimmte Long-/Short-Stop-/Limit-Fälle konnten falsche Fills erzeugen. | Strikte Eingänge und richtungsabhängige Fills; unbekannte Verläufe bleiben ungeklärt. |
| Historisches Universum | Späteres Volumen und nach Studienstart verfügbare Historienlänge beeinflussten BI/Biotech-Auswahl. | Ausschließlich vorher bekannte Historie; keine späteren Gewinner-/Überlebensdaten als Rangfilter. |
| Datenqualität | Fehlende OHLCV ergänzt oder Ausschlüsse beim Kopieren von Trade-Listen verloren. | Keine Ersatzkurse/-volumina; Quelllücken und ungeklärte Fälle bleiben sichtbar, auch bei leeren Ergebnissen. |
| Kalender/Quellen | Nächste beobachtete Kerze war nicht zwingend nächste erwartete Sitzung; Providerseiten und Completionflags unzureichend geprüft. | Kalenderprüfung, explizite Adjustierung, vollständige Pagination und erhaltene Qualität; keine Fills über fehlende Sitzungen. |
| Crypto-Zeitraum | Warmup konnte in Studie geraten; zwölf Monate verloren durch 365-Bar-Cap den Vorlauf. | Getrennter Vorlauf/Testbeginn und ausreichende Historie; fehlende/offene Serien sind keine erfolgreiche Nullstudie. |
| Holdout | Train-Trades enthielten erst nach Holdoutbeginn bekannte Exits. | Solche Fälle werden vor Kennzahlberechnung entfernt und als Purge gezählt. |
| Profit-Faktor | PF 1,099 wurde als Vergleichswert 1,10 an die Grenze gegeben. | Rohwert für Grenzvergleiche; gerundeter Text bleibt Anzeige. |
| Kennzahlbedeutung | Verkettete volle Trade-Notionalwerte als Konto-Equity/Drawdown verstanden; fehlende Aliasse noch Null. | Explizite Modell-Tradefolge; unbekannte Performance auch in Rendite-/Drawdown-Aliassen leer. |
| Speicherung | Datei je Ticker/Strategie vermischte Monate/Filter; Fehler überschrieben Erfolg; nicht atomar. | Vollständige V2-Identität, kanonische Zahlen, atomare Publikation; späterer Fehler erhält Erfolg. |
| Lebenszyklus | Reopen las keinen Cache; Fortschritt konnte Abschluss blockieren; alte Antworten/Doppelstarts unzureichend abgegrenzt. | Passives exaktes GET, pure Formularpräferenzen, Deadline, SingleFlight, Generation-/Unmountschutz und Server-Duplikatschutz. |
| Anzeige | Fehlende Teilkennzahlen als Null; Tabellen-Caps verschwiegen nicht übertragene Zeilen. | Fehlendes „—“; Gesamt-/geladene-/sichtbare Einträge getrennt; keine Umetikettierung anderer Auswahl. |

Produktions-BI verwendete Long-Stop-/Short-Limit-Einstiege. Zusätzliche
symmetrische Helpertests behaupten nicht, der produktive Short-Scanner hätte
zuvor Short-Stop verwendet. Kein Signal-, Score- oder 17/20-Mindestwert abgesenkt.

## Gespeicherte Ergebnisse

- Vollständige Auswahl: ticker, strategy, months, max_tickers, min_price, min_volume.
- Universum bleibt leeres Tickerfeld, nicht ersatzweise AAPL.
- Explizite Nullfilter sind nicht ausgelassene Profilstandards.
- Oberfläche speichert nur Formularwerte, keine Backtestergebnisse oder Tokens.
- GET liest genau die Auswahl; andere Monate/Filter sind andere Auswertungen.
- Abgeschlossener Null-Trade-Bericht bleibt sichtbar; fehlender Cache ist kein Lauf.
- Neuer Fehler löscht keinen zuvor erfolgreichen Bericht.
- Alte parameterlose Dateien bleiben erhalten. Unbekannte Auswahl wird nicht
  als passende neue Studie behauptet; für exakte V2-Identität gegebenenfalls
  einmal einen neuen Backtest ausdrücklich starten.
- Verlassen des Tabs beendet Requests/Callbacks, nicht Serverworker.
  Wiederöffnen startet keinen Worker; identische laufende Auswahl darf keinen
  zweiten parallelen POST erzeugen.
- Frischer Erfolg mit fehlgeschlagenem Cache-Schreiben heißt nicht gespeichert,
  nicht dauerhaft gespeichert.

## Prüfnachweise vor finalem Paketlauf

143 neue ausgeführte Fälle: 40 Kern-/Mathematikfälle, 65 API-/Quellenfälle,
19 unabhängige Gegenfälle und 19 Frontendcontrollerfälle. Die folgenden
Gruppen überlappen sich; ihre Zahlen werden nicht zu einem Gesamtergebnis addiert.

- Erste Kerngegenfälle 27/27 rot: tmp/qa-4cecb6c56529/results.xml.
- Weitere sieben Kennzahlfälle rot: tmp/qa-4aa6ab207816/results.xml.
- Drei Aliasfälle rot: tmp/qa-5082985b0aa0/results.xml.
- Eingefrorener Originalstand mit identischer unabhängiger Datei: 11 rot/8 grün,
  tmp/qa-88ec4db533fd/results.xml.
- Unabhängige Gegenprüfung nach Reparatur: 19/19 grün,
  tmp/qa-53810d90968f/results.xml; auch Richtung/Kausalität, Kosten in R,
  uncapped Gapverluste und fehlende Krypto-Wochenendsitzungen.
- Gemeinsame Kern-/Backtestgruppe 205/205 grün:
  tmp/qa-2d72da471bda/results.xml, darunter 40 neue Kernfälle.
- Tatsächlich ausgeführter Frontendcontroller 30/30 grün:
  tmp/qa-0cf09ecd7cca/results.xml; exakte Reads, Null/Missing, Scopewechsel,
  Doppelstarts, Structured-HTTP-Fehler, Unmount, hängender JSON-/Fortschrittsread.
- Quellen-/API-Nachtrag: 247/247 grün, darunter 65 neue API-/Quellenfälle
  und die 19 unabhängigen Gegenprüfungen: tmp/qa-7495abeac921/results.xml.
  Frühere rote Quellenfälle: tmp/qa-a5e63495ab45 (30),
  tmp/qa-46152c0b0100 (7), tmp/qa-4d854e1139b3 (3).
- Eingefrorener Anwendungsgesamtlauf: 11.030 bestanden, eine POSIX-Prüfung
  auf Windows übersprungen, keine Fehler; Abschlussnachweis unten.

## Tatsächliche Browserprüfung

Playwright-Skill, headless Microsoft Edge, eigener Static-Server auf
127.0.0.1:4362, tatsächliches frontend/index.html plus gebautes app.bundle.js.
Sämtliche API-Routen vor Navigation synthetisch beantwortet; externe Requests blockiert.

Vorprüfung 02.10.2026, 21:08:35–21:08:37 UTC: Dreimonatsbericht automatisch
geladen und beim Reopen erneut gelesen. Sechsmonatsauswahl ohne passenden Cache
zeigt keine falsche Nullstudie/alte Tradezeilen. Structured-HTTP-422 eines
neuen Laufs erhält alten Erfolg; nächster synthetischer Erfolg nach Reopen
wieder geladen. Fünf exakte GETs, zwei ausschließlich simulierte Benutzer-POSTs,
null externe Requests/JavaScript-Laufzeitfehler. Console-422 ist beabsichtigt.

Desktop 1440 px und Mobil 390 px visuell geprüft; Dokumentbreite ebenfalls 390 px.
Breite Historientabelle scrollt innerhalb der Karte, nicht die Seite.
Methodenerklärung standardmäßig aufklappbar. Abschließender Wiederholungslauf
am 02.10.2026, 21:18:48–21:18:50 UTC mit finalem Bundle 17410b75a42c:
alle fünf oben genannten Abläufe erneut bestanden, fünf exakte GETs,
zwei simulierte Benutzer-POSTs, keine externen Requests oder Laufzeitfehler.
Beide abschließenden Screenshots visuell kontrolliert; Mobilbreite 390/390.

Private lokale QA-Artefakte, nicht veröffentlichen:
output/playwright/backtest_deep_browser_check.js,
backtest-restored-desktop.png, backtest-restored-mobile.png.

## Eingefrorene Abschlussprüfung

Unabhängige zweite Nachprüfung nach Code-Freeze: 143/143 bestanden,
tmp/qa-aaa45f6f643e/results.xml; keine verbleibenden konkreten Blocker im
geprüften Backtest-Scope gefunden. Kein Produktions-I/O und keine Dateiedits
durch diesen Reviewer. Eine pytest-Warnung betrifft bereits importiertes AnyIO.

Erster Indexbaum: 952b1d64c86a30df5472da078e32df42e2768702.
Erster Export: tmp/backtest-publish-11c3615c76df45048b7a13ba766fbfe4/source.
Zunächst 17 Backtest-Dateien, mit bestehender Rohpräzisions-Testkorrektur
abschließend 18 Dateien vorgemerkt; geerbte Deploy-/Handbuch-/Commerce-/
Kalenderänderungen sowie private output-Dateien bleiben ausgeschlossen.

Anwendungstest aus diesem Export, mit isolierter Offline-QA-Umgebung:

```powershell
$taskBacktestSource = 'C:\Projekt\TradingBot\tmp\backtest-publish-11c3615c76df45048b7a13ba766fbfe4\source'
$env:ALPHA_QA_SOURCE_ROOT = $taskBacktestSource
$env:ALPHA_QA_OUTPUT_ROOT = 'C:\Projekt\TradingBot\tmp'
& 'C:\Projekt\TradingBot\.codex_pytest_env\Scripts\python.exe' "$taskBacktestSource\scripts\run_offline_tests.py" -q --tb=short --ignore-glob='test_deploy*.py'
```

Die Deploy-Umstellung ist ausdrücklich nicht Teil dieses Anwendungspakets;
deren Tests bleiben außerhalb dieses Laufs.

Erster eingefrorener Gesamtlauf: 11.025 bestanden, fünf alte R-Erwartungen
fehlgeschlagen, eine POSIX-Dateirechteprüfung auf Windows übersprungen;
tmp/qa-e1d4b405c8a7/results.xml, 457,63 s. Sämtliche fünf Fehler stehen in
den bestehenden Backtestfällen von test_strategy_trade_setup.py: Erwartungen
0,45/1,95 bzw. gerundeter EOD-Oberwert statt Rohpräzision. Nach unabhängiger
Herleitung werden nur diese Erwartungen auf ungerundete Werte korrigiert;
die übrigen Assertions und der eingefrorene Laufzeitcode bleiben erhalten.
Der abschließende Wiederholungslauf prüft das daraus entstandene Paket.
Unabhängige Decimal-Rechnung bestätigt Long TP1/BE 0,44975,
Short TP1/BE 0,45025, Long TP1/TP2 1,949, Short TP1/TP2 1,951 sowie
EOD 1,4437554972513743. Die ganze betroffene Testdatei besteht danach
28/28: tmp/qa-909629e9ddc2/results.xml. Toleranz 1e-12, keine abgeschwächten
Ausführungs-/Richtungs-/Mehrdeutigkeitsprüfungen, keine Produktionsänderung.
Ein zweiter Reviewer bestätigt unabhängig dieselben ungerundeten Werte,
Kosten und unveränderten Outcome-/Mehrdeutigkeitsprüfungen.

Finaler Wiederholungskandidat: Indexbaum d7182662389d6f5dea463062ed9396ed03a58efc,
tmp/backtest-final-publish-081c56d3439f4d87b2dc1d0fee7195fb/source.
Alle sieben Laufzeitdateien stimmen normalisiert bytegenau mit dem ersten
geprüften Kandidaten überein. Ausschließlich eine bestehende Testdatei und
Prüfbericht-Nachträge hinzugekommen.

**Finaler Gesamtlauf abgeschlossen: 11.030 bestanden, eine Prüfung
übersprungen, keine Fehler.** 11.031 JUnit-Fälle, failures=0, errors=0:
tmp/qa-a8c5b525dfe2/results.xml; pytest-Laufzeit 430,18 s.
Aufruf wie oben, aber ALPHA_QA_SOURCE_ROOT zeigt auf den finalen Export.
Der Skip betrifft ausschließlich
test_gap_scan_schedule::test_state_permissions_are_private_on_posix;
Windows-Modusbits belegen keine POSIX-Dateirechte. Einzige Warnung:
bereits importiertes AnyIO-Modul bei pytest-Assertion-Rewrite.

Die Nachträge nach diesem Export verändern ausschließlich die Dokumentation,
nicht den geprüften Laufzeitcode oder Tests. Private Exporte, Browserartefakte
und die geerbten fremden Änderungen sind nicht Teil der Veröffentlichung.

Normalisierte SHA-256 (UTF-8, CRLF → LF), Worktree und Export gleich:

- api.py: 7002364ae84f98a357cbe4696769844fe6c20328820b781a78fd4fc193fb9f7c
- frontend/index.html: 90931cbcb6570073a66d540758955672bbcdb4e5ac2e3577f79c172a5cd02643
- frontend/app.bundle.js: e5309a50fe8a1f1d4feaa2d2bbaa9590a677b07717724a559f1df2c73d7aafd4

Eigener lokaler Playwright-Browser geschlossen und eigener Static-Server
anhand Interpreter, vollständigem Startbefehl und PID 1982320 identifiziert
und beendet. Nutzerbrowser und andere Prozesse nicht verändert.

## Veröffentlichung

Implementierungscommit: 7bfeb167898f0452de3bb5bfd7714c6c9da941e2.
git push origin main erfolgreich; git ls-remote origin refs/heads/main
bestätigte genau diesen Hash. Der anschließende Statusnachtrag in diesem
Prüfbericht und TODO verändert ausschließlich Dokumentation.

Server nicht verändert. Normaler Betreiberpull erst nach Abschluss laufender
Scans/Versandvorgänge; kein Deployskript und keine Installationsumstellung.
Nach Pull API und Hintergrunddienst neu starten, danach Strg+F5.
Echte Cache-Wiederherstellung nach Betreiberupdate bleibt Betriebsabnahme.

## Abnahmegrenzen

Feste Kosten-/Slippage-/Daily-Fillmodelle bleiben Modelle, keine historischen
Quotes/Ausführungen. Aktuelle Branchenmetadaten sind kein vollständig historischer
Point-in-Time-Marktbestand. Überlappende Tickertrades sind kein rekonstruierter
Kontopfad. Diese Grenzen werden nicht durch neue Regressionstests aufgehoben.

Kein Deploy-Skript, kein Eigentümer-/Installationsumbau, keine Cache-/DB-Löschung.
Server in diesem Auftrag nicht aktualisiert. Frühere offene Signalzustellungen
und Provider-Abos sind separate Betriebsgrenzen, durch Backtest-Audit nicht erledigt.
