# Aktuelle Aufgaben / Übergabe

## 08.10.2026, 14:59 Zürich – Momentum-Belege ausgewertet, Richtungsfix gepusht

Dieser Abschnitt ist der neue Arbeitsstand; der 13:49-Stand darunter ist historisch.

- [x] Eng begrenzter Operatorcheck abgeschlossen; **keinen weiteren Export anfordern**.
  Private Datei `output/profitability/momentum-candidate-evidence-20261008T123541Z.json`
  lokal gelesen: alle vier Kandidaten vorhanden, Finalcache Version 20 / 13 Zeilen.
  Cache-SHA256 `d89c928118181c208a8f51c6b61898e9d745f083d12395eee5b9686d3de0365d`.
  Referenzschluss **07.10.2026, 20:00 UTC**. Naive Cachezeit nicht als UTC oder
  als Identitätsnachweis des früheren 13:02-Laufs interpretieren.
- [x] RELL: beide gespeicherten 20T-Median-Dollarvolumen-Aliase stimmen überein:
  **1.771.748,0399167603 USD**, also **228.251,96 USD / 11,41 % unter 2 Mio.**.
  `History_OK=true`; Hoch 20,30 liegt unter TP1-Toleranz 20,6164; Tagesqualität
  86/96 besteht. Rücktest blockiert nicht. Scannergrenze 750.000 prüft dagegen
  aktuellen/projizierten Sitzungsumsatz, **nicht** historischen Median.
  Mailregel seit `99c644ce` (22.07.), Zweck: ein einzelner hoher RVOL-Tag ersetzt
  keine normale Liquidität. Gerade 2 Mio. ist nicht empirisch kalibriert belegt.
- [x] UVE: Entry 45,25 liegt in historischer Resistance-Zone
  **44,96803437823321–45,265858067507324**, sechs ältere Swings 1D/1W/4H,
  letzte Bestätigung 18.09.; TP1 45,27 = **0,03R**. TP2 ausdrücklich Projektion.
- [x] NECB: Entry 27,33 liegt in Resistance-Zone
  **27,28886463346936–27,501135366530644**, vier ältere Swings 1D/4H,
  letzte Bestätigung 24.09.; TP1 27,50 = **0,41R**. Median 948.137,095038355 USD
  zusätzlich unter Mailgrenze. Kein eigener Signaltageshoch-Beleg als Blocker.
- [x] GKOS: finale VRVP-HVN-Zone **168,585–172,42416666666668**, Entry 172,18
  innerhalb; TP1 172,42 = **0,05R**. Profilzeit ist letzte abgeschlossene Profilkerze,
  nicht erstmalige Entstehung des Knotens. Alle **767 nativen Belege** von
  UVE/NECB/GKOS liegen vor Signal-Close; keine zukünftige Pivotbestätigung gefunden.
- [ ] Originale OHLCV/Profile-Beiträge fehlen in diesem Export: gespeicherten Median
  und Pivot-/VRVP-Entstehung nicht als vollständig unabhängig neu berechnet ausgeben.
- [x] **Konkreter zusätzlicher Richtungsfehler reproduziert:** GKOS-Zone
  `lz_5161012c0e0e8531` (172,55401398522704–174,22851097901938) trägt einen
  bestätigten SHORT-Bruch, wurde aber pauschal auch aus LONG-Gegenbarrieren entfernt.
  Synthetische Long/Short-Gegenprüfungen vor Fix: **12 Fehler / 4 bestanden**,
  XML `output/directional-reclaim-red-20261008.xml`.
- [x] Richtungsfix in `modules/level_zones.py` und `api.py` abgeschlossen:
  aktive, geometrisch/zeitlich gebundene Bestätigung statt globalem `reclaimed`-Skip;
  Handelsplan prüft auch RECLAIMED-Zertifikate. Cacheversion **20 → 21**, damit
  alte Pläne nicht ungeprüft weiter freigegeben werden. Unabhängige Schlussprüfung
  ohne verbliebenen konkreten Blocker; gemischte frühere Richtungsanker, strikte
  Retest-Booleanflags und exakt gleicher Snapshot-Cutoff zusätzlich abgesichert.
  Dieser Fehler begünstigte übersehene
  Hindernisse, er repariert **nicht** die konkret belegten Ablehnungen oben.
- [x] Exaktes Sechs-Dateien-Paket aus dem Git-Index separat geprüft:
  **598 bestanden / 1 Windows-Symlink-Skip / 0 Fehler**, eine bekannte AnyIO-Warnung.
  XML `output/release-verification-20261008-directional-10a9cf971199/qa-4eb5a9bf98bc/results.xml`.
  Produkttree **`c2a3a86feb84b25ec92c47b7cbcd6afd55f81aea`** ist unverändert identisch
  mit dem Commit; Frontend-Bundle unverändert korrekt **`8f8a6c0c5bae`**.
  Kein neuer Gesamttest aller Scanner, kein neuer Provider- oder SMTP-Nachweis.
  Frühere Anschluss-QA 268 bestanden/2 neue Test-Assertionfehler (optionales
  `warning_codes`-Feld nicht vorhanden), XML
  `output/mail-fix-qa-65e4a69b609242d28ca29f55c4e40c9c/results.xml` erhalten.
  Assertion korrigiert, weitere echte Binding-Randfälle mit Regressionen ergänzt.
- [x] Produktcommit **`c26aa4898f44e0af0891532f1d5ac1b1c279e2c0`** erstellt/gepusht;
  `origin/main` anschließend exakt per `git ls-remote` bestätigt. Nur zwei
  Produktdateien, vier Diagnosereader-/Testdateien im Paket. Private Exporte,
  vererbtes Deploy-/Calendar-/Commerce-/Handbuch-WIP nicht veröffentlicht.
- [ ] **Server noch nicht aktualisiert.** Nach Abschluss laufender Scans normal
  `pull --ff-only` und API/BG-Neustart durch Operator; kein Deploy-Skript und
  keine Installation ändern. Cache 20 wird nach Update nicht weiter freigegeben;
  neue vollständige Strategiepläne müssen mit Version 21 berechnet werden.
- [ ] **Reguläre Signalzustellung bleibt offen.** Keinen weiteren technischen
  Testversand und keine stillen Schwellen-/Abo-/Kontoeingriffe vornehmen.
  Neue echte SMTP-/Postfach-Freigabe nur anhand eines tatsächlich geeigneten
  vollständigen aktuellen Laufs nachweisen. Biotech-Zugang bleibt separat abgelaufen.

## Historisch: 08.10.2026, 13:49 Zürich – echte Versand- und BI-Prüfung

Dieser Abschnitt ersetzt den 12:45-Einstieg als Arbeitsstand. Keine neue
Regelaufweichung, Testmail, Installation oder Wiederholung eines Live-Aktienscans.

- [x] Live-Health **13:18:43 Zürich**: healthy, **`8c682f4102b3`**,
  Frontend **`8f8a6c0c5bae`**. Die vorige Produktreparatur ist installiert.
- [x] Angemeldeten Nutzer-Tab verwendet. Admin → Mailversand **13:18:31**:
  **SMTP 0 / ausgelassen 5 / Versandfehler 0 / Queue 0**, je ein Swing- und
  Krypto-Empfänger. Aktien-Sammellauf **13:02:26 abgeschlossen, 13 Kandidaten**;
  Cup-Blattlauf 13:02:24 abgeschlossen. Das ist ein neuer vollständiger Lauf
  nach dem Update, aber weiterhin kein belegter regulärer Mailversuch.
- [x] RELL einzeln geprüft: Referenzschluss 07.10., Preis 20, Tagesqualität
  **86/96**, Trade-Score **91**, Setup-Score 92. Konkrete Mailablehnung:
  **`momentum_mail_blocked_thin_baseline_liquidity`**. Der fehlende Rücktest
  ist hier keine harte Sperre. Rohwert des historischen 20T-Median-Dollarvolumens
  fehlt in der App; weder aktuelle RVOL noch AvgVol ersetzen ihn.
- [x] UVE einzeln geprüft: Preis 45,25; native Gegenbarriere **45,27** aus
  bestätigten Swings 1D/1W/4H, Zone überlappt Entry, WAIT_BREAK_RECLAIM.
  TP1 45,27 = 0,03R, TP2 46,92 = 2,46R-Projektion. Diese Herkunft ist nicht
  automatisch das eigene Tageshoch. Original-Zonengeometrie/-Belege fehlen im UI.
  Gleichzeitige Zusammenfassungsgründe können verschiedene Kandidaten betreffen.
- [ ] **Reguläre Signal-Mail weiterhin offen.** Originale eingefrorene RELL-
  Liquiditäts- und UVE/NECB/GKOS-Zonenbelege lesen, dann Ablehnung mit demselben
  Zeitstand gegenrechnen. Bei nachgewiesenem Fehler gezielt reparieren; nicht
  auf Basis eines später geladenen Charts die Freigabe erzwingen.
- [x] **Realer BI-Long-Backtest gestartet und abgeschlossen, 13:29:58:**
  3 Monate / 200 / Mindestpreis 5 / Mindestvolumen 200.000. Ergebnis gespeichert
  und über „Gespeicherte Auswertung laden“ erneut identisch angezeigt. Danach
  Aktien-Ansicht geöffnet und Tools → Backtest erneut geöffnet: Ergebnis
  **automatisch** mit Datum 13:29:58 und identischem NWSA-Trade geladen.
  190 Aktien mit auswertbaren Fenstern; 9.549 BI-Prüfungen, 9.548 abgelehnt,
  0 nicht berechenbar, **1 BI-Setup / 1 Plan / 1 Einstieg**, 0 No-Fill/offene
  Verläufe. **NWSA 21.08.–03.09., +3,03 %, +1,12R, TP1+EOD, Grade B**.
  Kein live verschicktes Signal und keine Broker-Ausführung.
- [x] BI-Funnel rechnerisch abgeglichen:
  11.950 Fenster = 9 laufend belegt + 95 ungültig/unvollständig +
  2.295 Preisfilter + 2 Volumenfilter + 9.549 BI-Prüfungen.
  Tagesabrufe **167/167**, 0 fehlgeschlagen/leer; individuelle Sitzungen
  **12.101/12.800**, also **699 fehlend**. UI kennzeichnet korrekt PARTIAL.
- [ ] BI-Abdeckung **nicht** abgeschlossen: tickerweise Fehltermine und historische
  Instrumentidentität (Umbenennung/Delisting/Handelspause/Providerlücke) fehlen.
  Erfolgreiche Tagesabrufe garantieren keine einzelne Aktienbeobachtung. Später
  datenlose vorab ausgewählte Titel nicht löschen: Survivorship Bias. Backtest-
  Universum verwendet keine vollständige historische Common-Stock-Typprüfung;
  diese getrennte Live-Paritätsgrenze nicht als fertiges Scanner-Audit ausgeben.
- [x] Neuer eng begrenzter lesender Momentum-Collector lokal geprüft:
  `scripts/collect_momentum_candidate_evidence.py` und `.ps1`, nur vier feste
  Kandidaten, Cache-Hash/Prozessidentität, keine Provider-/Konto-/SMTP-Zugriffe.
  Ursprüngliche Preis-/Score-/Liquiditätsaliase samt Konflikten erhalten;
  native und finale VRVP-Geometrie/Levelbeweise, Prozess-/Dateiwechsel geprüft.
  Lesende unabhängige Schlussprüfung: kein offener konkreter Blocker.
  Windows-PowerShell-5-Parserproblem mit case-distinct JSON-Aliasen reproduziert
  und im neuen Wrapper behoben (case-sensitive Dictionary, Originalbytes bleiben).
  **35 bestanden, 1 Symlink-Test Windows-bedingt übersprungen**. Finale XML:
  `output/momentum-reader-qa-20261008-1351-r3.xml` (exakter finaler Teststand
  einschließlich gespeicherter Original-Aliase); erste RED-Evidenz erhalten:
  `output/momentum-reader-qa-20261008-1346.xml` (33 bestanden, 2 Fehler, 1 Skip;
  ein Test-Assertionfehler und der reale neue Wrapper-Parserfehler).
- [ ] **Operatorcheck ausstehend**: einmal lokal in Windows-PowerShell
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projekt\TradingBot\scripts\collect_momentum_candidate_evidence.ps1"`.
  Eng begrenzte Rückfrage gestellt, kein weiterer großer Gesamtexport.
  Danach neue private `output/profitability/momentum-candidate-evidence-*.json`
  lesen und am Original-Zeitstand gegenrechnen. Kein Serverupdate nötig;
  originaler Tageskerzenpräfix ist in dieser finalen Cachedatei nicht gespeichert.
- [x] Frische gezielte Offline-QA: **111 bestanden**, keine Fehler (eine
  bekannte AnyIO-Rewrite-Warnung), isolierter Launcher ohne externe Provider/SMTP.
  Momentum-Vertrag/Gates, Native-Plan-Mailintegration, BI-Diagnosefunnel und
  adversarial Backtest-Gegenprüfungen. XML:
  `output/mail-fix-qa-e3b9e38f5c1c47bea329317cd8e8295a/results.xml`.
  Erster Sandbox-Start blieb ohne Ausgabe; nur die zwei exakt identifizierten
  eigenen Testprozesse beendet. Die erfolgreiche Wiederholung lief mit denselben
  Testargumenten unbuffered außerhalb der Prozess-Sandbox. Nicht als Produktfehler
  oder vollständigen neuen Repositorylauf ausgeben.
- [x] Abschließender lokaler Stand: drei neue Collector-/Wrapper-/Testdateien,
  TODO und Account-Übergabe aktualisiert; **nicht committet/gepusht**.
  Sender/Scanner/Frontend nicht geändert, kein neuer Server-Pull oder Testversand.
  Historischer BI-Lauf hat auf dem Server seine eigene Auswertung gespeichert.
  Vererbtes Deploy-/Calendar-/Commerce-/Dokumentations-WIP unverändert erhalten;
  private Exporte/QA-Evidenz nicht veröffentlichen. Reguläre Mailzustellung
  und vollständige historische Abdeckung bleiben bewusst offen.

## Historischer Einstieg 08.10.2026, 12:45 Zürich – Accountwechsel

**Zuerst lesen:** [Account-Übergabe](docs/HANDOFF_SIGNAL_MAIL_2026-10-08.md).
Dieser Einstieg ersetzt ältere Aussagen „noch nicht installiert“ und alte
Pull-Aufträge weiter unten. Die früheren Abschnitte bleiben historische Evidenz.

- [x] Git und Remote frisch abgeglichen: HEAD und `origin/main`
  **`8c682f4102b38fe30bfb214842f12e7d09203890`**. Produktreparatur
  **`06d9860d1d7fb590e633d30bea2563229ea9ee1a`**; danach nur drei
  Dokumentationsdateien geändert, keine weitere Produktänderung.
- [x] Öffentliche Healthprüfung **08.10.2026, 12:44:59 Zürich**:
  **healthy, Revision `8c682f4102b3`, Bundle `8f8a6c0c5bae`**.
  Die Cup-/Mailreparatur ist damit inzwischen auf Hetzner vorhanden.
  Kein erneuter Pull/Neustart allein für diese Übergabedokumentation nötig;
  hier keine Produktionsänderung. Einzelne Dienstneustarts nicht separat geprüft.
- [x] Vorhandene finale QA-XML erneut gelesen: **12.171 bestanden,
  2 übersprungen, 0 Fehler/Errors**; kein neuer Testlauf.
  Geprüfter Produkttree **`237fdd8199a628d749a8c380058c4a8595ee5f90`**
  stimmt mit dem Produktcommit überein. Vier separate Deploy-Testdateien
  bleiben ausgenommen, vererbtes WIP unverändert.
- [ ] **Nächster Schritt: reguläre Signal-Mail live nachweisen.** Im vorhandenen
  angemeldeten App-Tab Admin → Mailversand lesen; einen nach der Installation
  abgeschlossenen passenden Lauf eindeutig bis finale Prüfung → Sender → SMTP
  → Postfach verfolgen. Letzte Versandansicht ist noch **11:55:49**, also vor
  dem jetzt bestätigten Update: SMTP 0 / ausgelassen 8 / Fehler 0 / Queue 0.
  Keine neue Versandansicht in dieser Dokumentationsrunde gelesen. Technische
  Testmail kam früher an; kein Nachweis einer regulären Signal-Mail.
  Keine neue Testmail, Regelaufweichung oder Wiederholung großer Exporte auf Verdacht.
- [ ] Danach neuer realer **BI-Backtest: 3 Monate / 200 Aktien / Mindestpreis 5 /
  Mindestvolumen 200.000**; Datenabdeckung, 17/20-Kandidaten, Pläne, Entry/Fill,
  No-Fill und offene Folgefenster getrennt prüfen. Wochenreport-Zustellkohorten
  und ursprüngliche VIAV-/AST-/LSPD-Kerzen-/VRVP-Belege bleiben eigene Nachweise.
  Vorhandene historische Studien berücksichtigen, nicht erneut als ungemacht behandeln.
- [x] TODO und Anschlussauftrag aktualisiert. **Diese Übergabeänderungen bleiben
  lokal, noch nicht committet/gepusht.** Keine neuen Programmcode-, Scan-, Mail-,
  Einstellungs-, Datenbank- oder Serveränderungen; kein Testprozess gestartet.
  Geschütztes Deploy-/Calendar-/Commerce-/Dokumentations-WIP und private Exporte
  nicht mitveröffentlichen, löschen oder zurücksetzen.

## 08.10.2026 – Cup-Plan und Mailpfad: repariert, geprüft und gepusht

Dieser Abschnitt ersetzt ältere Installations-/Cup-Angaben darunter. Historische
Tests und offene Live-Nachweise werden nicht nachträglich als abgeschlossen umgedeutet.

- [x] Vorherige Historienreparatur ist inzwischen vom Betreiber installiert:
  `/api/health` erneut **healthy, `5dbad583d3e9`, Bundle `8f8a6c0c5bae`**.
  Admin → Mailversand: automatischer Aktien-Sammellauf **08.10. 11:18:55
  abgeschlossen, 13 Kandidaten**; Cup-Blattlauf 11:18:52 abgeschlossen.
  Damit ist ein neuer vollständiger automatischer Lauf ohne früheren Datenabbruch
  belegt, nicht die tatsächliche Auswertung jeder einzelnen Aktie oder Zustellung.
- [x] Cup-Planfehler mit echten 260 abgeschlossenen Tageskerzen reproduziert:
  alte native Entscheidungen bezogen sich auf andere Entry-/Stop-/Zielpreise.
  Finalisierung erfolgt jetzt am tatsächlichen Cup-Entry und Handle-Stop mit
  denselben unveränderten kausalen D/W/4H-Zonen. Gemessene Cup-Tiefenziele bleiben
  separat markierte Projektionen; kein pauschales Entfernen realer Barrieren.
- [x] Stop-Invaliderung gegen VRVP-Umschreibung geschützt; erster Widerstand,
  R-Abstand, Entscheidung, Preis-Aliase und Herkunft neu auf den endgültigen Plan
  gebunden. Projektion als TP1 führt zu REJECT; konkrete bestehende Ablehnungsgründe
  bleiben erhalten. TP2-Projektion allein entwertet strukturelles TP1 nicht.
- [x] Versions-/Kohärenzvertrag vor Sender und beiden finalen Kursprüfungen;
  alte/widersprüchliche Cup-Pläne werden nicht durch einen hohen Score freigegeben.
  Stop-Bestätigung = Muster-Schlusszeit, Daten-Cutoff separat; undatierte Muster
  dürfen keine zeitgestempelte Strukturautorität aus einem fremden Snapshot übernehmen.
- [x] Durable Cup-Watch-Projektion erhält alle erforderlichen Plan-/Zonenbelege,
  bleibt begrenzt und nimmt keine beliebigen Zusatzfelder auf. Realer Offline-Pfad
  Queue → Lease → bestätigte 5m-Kerzen → Promotion → echter Sender → simuliertes
  SMTP → dauerhaftes TRADE-Journal nachgewiesen; keine neue technische Testmail.
- [x] Startup/REST/Reminder-Quelle prüfen denselben finalen Cup-Vertrag. Junge
  inkompatible Caches dürfen keinen Reparaturscan aufschieben oder einen alten
  Fehler als behoben quittieren. Leere aktuelle Caches und kohärente negative
  Kandidaten bleiben verwendbar; negative Kandidaten sind keine Mailfreigabe.
- [x] **262 gezielte Tests bestanden**, danach **47 finale Cup-/Cache-/Watch-Tests
  bestanden** einschließlich konkreter Fehlergrund-Erhaltung. Mengen überlappen.
  Letzte XML: `output/mail-fix-qa-94e9d35e38544a019285bd92379e2f76/results.xml`.
  Unabhängige lesende Schlussprüfung ohne offene konkrete Beanstandung.
- [x] Exakter endgültiger Produkt-Index: **12.171 bestanden, 2 übersprungen,
  0 Fehler/Errors**, 454,26s. Tree **`237fdd8199a628d749a8c380058c4a8595ee5f90`**.
  XML: `output/release-verification-20261008-cup-release-5bb34e78aea4/qa-57fafe9b2612/results.xml`.
  Die vier separat offenen Deploy-Testdateien ausdrücklich ausgenommen; kein
  uneingeschränkt grüner Repositorylauf. Übriges vererbtes WIP in HEAD-Version.
- [x] Produktcommit **`06d9860d1d7fb590e633d30bea2563229ea9ee1a`** gepusht,
  `origin/main` direkt per SHA bestätigt. Frontend unverändert, Bundle geprüft.
  Nur die zwölf scoped Produkt-/Testdateien; vererbtes Deploy-/Calendar-/Commerce-
  und sonstiges Dokumentations-WIP sowie private Exporte bleiben unangetastet.
  TODO, Übergabe und Prüfbericht begleiten das Produkt als separater Doc-Commit.
- [x] Cup-Paket später auf Hetzner vorhanden: Health **12:44:59** bestätigt
  `8c682f4102b3`; Installation nicht durch diesen Account ausgeführt.
- [ ] **Reguläre Signal-Mail im Postfach** nachweisen. Hier kein Serverupdate,
  Scanstart, Kontoeingriff oder zusätzlicher Testversand.
  Admin-Livefenster **11:55:49**: SMTP-Annahmen 0, ausgelassen 8, Fehler 0, Queue 0;
  diese Versandzahlen stammen vor dem jetzt bestätigten Cup-Update.
  Separater lesender Sender-/Publisher-Abgleich: `—` in Transportspalten kann aus
  nicht erzeugten Ereignisschlüsseln entstehen; kein belegter Verlust des Sweep-
  Audits. Fehlende Zahlen nicht ohne Ereignisbeleg in 0 umwandeln.
- [ ] Eigene ältere Nachweise bleiben offen: reale BI-Dreimonats-/Wochenreport-
  Kohorten und fehlende Originalkerzen-Levelchecks. Nicht durch diese Cup-/Mail-
  Reparatur als erledigt markieren; keine erfundene Trefferquote.

[Aktueller Prüfbericht](docs/CUP_FINAL_PLAN_MAIL_RECOVERY_2026-10-08.md).

## Historische Runde 08.10.2026 – Datenabbrüche repariert und gepusht

Damals ersetzte dieser Einstieg die Installationsangabe des Diagnosepakets unten.
Der aktuelle Git-/Serverstand und nächste Schritt stehen am Anfang dieser TODO;
alte Installationsaufträge und Abbruchbefunde hier nicht ungeprüft wiederholen.

- [x] Nutzer-Tab verwendet, keine erneute Anmeldung/Export/Testmail gefordert.
  Live `/api/health`: **`healthy`, `df04ee0813bb`, Bundle `8f8a6c0c5bae`**.
  Das Diagnosepaket wurde vom Betreiber installiert; hier kein Serverupdate.
- [x] Aktueller Cup-Versuch: `scan_data_unavailable`, Phase **Historie laden**,
  4408/12586 geprüft, 576 Providerrequests, 71 Cachehits, noch kein neues Ergebnis.
  Der zugrundeliegende HTTP-/Transportfehler ist ohne Originaljournal nicht bewiesen.
  SSH-BatchMode weiterhin abgewiesen; kein Passwort gelesen oder gespeichert.
- [x] Zwei Turtle-Abbruchfehler reproduziert und repariert: erfolgreiche leere
  Antwort ohne optionales `results`-Array sowie defekte OHLCV noch nicht verfügbarer
  Sitzungen konnten einen gültigen abgeschlossenen Tagesplan verhindern. Gemeinsamer
  strenger 1D-Parser prüft weiterhin Envelope, Count und Zeitfolge für alle Kerzen;
  benötigte abgeschlossene OHLCV bleiben Pflicht. Zusätzlich bisher akzeptierte
  doppelte/zukünftige Zeitstempel und widersprüchliche Counts werden zurückgewiesen.
- [x] Strikter Aktien-Historienabruf wiederholt nur Timeout, ConnectionError und
  HTTP 500/502/503/504 begrenzt und mit identischer Query/Beobachtungszeit. Maximal
  drei Versuche pro Abrufoperation, gemeinsames Budget von 20 Zusatzrequests mit
  bestehenden OHLCV-Wiederholungen; Deadline/Pause/Abbruch/Ratelimiter erhalten.
  Keine Wiederholung für 401/403/429/TLS/JSON/Schema, keine erfundenen Kerzen und
  kein Symbol-Ausschluss bei permanentem Transportfehler.
- [x] RED beobachtet: Turtle **12 fehlgeschlagen, 9 bestanden**; Transport
  **14 fehlgeschlagen, 11 bestanden**. Danach **46 neue Gegenprüfungen bestanden**.
  Breiter gezielter Nachlauf: **414 bestanden, 0 Fehler**. Evidenz:
  `output/mail-fix-qa-3299b9196e1747f49dbd0a12ce9b7d5c/results.xml`.
- [x] Echte StrictFetcher → Plan → finale Signalprüfung → simuliertes SMTP
  → dauerhaftes TRADE-Journal-Integration abgeschlossen: **109 bestanden**, vier
  neue LONG/SHORT × Timeout/503-End-to-End-Fälle. Unabhängige lesende Diffprüfung
  beider Produktionsänderungen und dieser Senderfälle ohne konkrete Blocker.
  XML: `output/mail-fix-qa-ccb79c699afa41dbad3955591d791900/results.xml`.
- [x] Exakter Produkt-Index-Snapshot geprüft: **12.124 bestanden, 2 übersprungen,
  0 Fehler/Errors**, 439,73s. Nur die vier bekannten separat offenen Deploy-
  Testdateien ausgenommen, übriges Deploy-/Calendar-/Commerce-WIP in HEAD-Version.
  Getesteter und committeter Tree `66fa66cbfd1396efae51ae4662f20361f0ff2703`.
  XML: `output/release-verification-20261008-history-ba1ba993afc1/qa-fc5d4c279ef4/results.xml`.
- [x] Genau sechs Produkt-/Testdateien committet und gepusht:
  **`352ea08b07ca82164eec353b064c85fa4c2133e6`**, `origin/main` per SHA abgeglichen.
  Kein privater Export oder vererbtes WIP enthalten. Frontend unverändert;
  Bundle `8f8a6c0c5bae` erneut bestätigt. Begleitende Dokumentation folgt separat.
  [Prüfbericht](docs/STOCK_HISTORY_MAIL_RECOVERY_2026-10-08.md).
- [ ] Danach Operatorupdate und einen neuen vollständigen automatischen Lauf bis
  **echter Signal-Mail im Postfach** belegen. Diese Datenreparaturen sind nicht als
  alleinige Ursache aller fehlenden Mails oder als reale Zustellung nachgewiesen.
- [x] Separater Cup-Kohärenzfehler inzwischen reproduziert, repariert, geprüft und
  unter **`06d9860`** gepusht; vollständiger finaler Plan gegen unveränderte kausale
  Zonen. Siehe aktuellen Anfang und Prüfbericht. Keine pauschale Barrierenlöschung
  oder Freigabe gemessener Cup-Ziele ohne gültigen Strukturbeleg.

Nach Ende aktiver Scans normaler Operator-Pull und API/BG-Neustart, kein
Deployskript/Installationswechsel. Danach Healthrevision mit tatsächlichem
`origin/main` vergleichen; der Dokumentationscommit nach `352ea08` kann die
Healthrevision ändern. Ein neuer vollständiger automatischer Lauf bleibt nötig;
keine weitere technische Testmail oder Regelaufweichung auf Verdacht.

## 08.10.2026 – Diagnose-Reparatur gepusht (historischer Veröffentlichungsstand)

**Aktuelle Übergabe:** [Signal-Mails / Accountwechsel](docs/HANDOFF_SIGNAL_MAIL_2026-10-08.md).
Dieser Abschnitt ersetzt den früheren Einstieg unten. Produktcommit
**`43ea4aa80a9a5ac34d066787f5ee3f7ddba90d86`** wurde nach ausdrücklicher
Freigabe committet und auf `origin/main` gepusht; Remote-SHA abgeglichen.
**Hetzner wurde hier nicht aktualisiert.** Die frühere Liveprüfung bestätigte
`b7415f1cd71f` / `ba7e64a7b792`, nicht das neue Diagnosepaket.

- [x] Bekannte sichere Versandgründe in der Adminansicht erhalten: zusätzliche
  geprüfte Texte für Kursabruf-/Tracker-/Reservierungsgründe; übrige bekannte
  IDs erhalten einen festen technischen Text mit ihrer sicheren ID. Nicht alle
  bekannten IDs sind individuell ins Deutsche übersetzt. Unbekannte Rohtexte,
  Adressen, Subjects, Authentifizierungsdetails und Provider-URLs bleiben verborgen.
  Keine globalen Freigabe-, Sender-, SMTP- oder Schwellenregeln geändert.
- [x] Validierte persistierte `mail_audit`-Zähler der fünf unterstützten
  Einzelstrategien unter `diagnostics.latest_attempt` erhalten. Admin liest
  zusätzlich den **automatischen Aktien-Sammellauf**, dessen Blattläufe bewusst
  `send_email=False` verwenden. Blatt- und Sammellauf bleiben getrennt, mit
  eigenem Zeitpunkt/Run-ID; keine Summierung oder erfundene Workeraktivität.
- [x] Admin → Mailversand → **Letzte Aktien-Mailprüfung** ist standardmäßig
  eingeklappt; keine neuen Diagnoseblöcke in der normalen Aktienliste.
  Kandidaten, Senderaufrufe, SMTP-Annahme, Teilannahme/unklare Teilannahme,
  Fehler, unklare Annahme und Warteschlange getrennt. Fehlende Evidenz = `—`,
  nicht 0; SMTP-Annahme ist kein Postfacheingang. BI/Bear/ORB/Crypto sind von
  dieser zusätzlichen persistierten Aktien-Versuchsansicht nicht abgedeckt.
- [x] RED/GREEN und **724 gezielte Offline-Tests bestanden**. Einschließlich
  aller erlaubten Grund-IDs, Privatsphärengrenzen, strikter Dateiprojektion,
  realem Sammellauf-/Senderguard-/Publikations-/Leser-Roundtrip ohne SMTP und
  kompiliertem JSX. XML:
  `output/mail-fix-qa-6c2fa8c3054342a3a3626549624c07ed/results.xml`.
- [x] Lokale vollständige App mit kontrollierten GET-Fixtures im internen
  Browser geprüft: Adminnavigation, eingeklappte Ansicht, Sammellauf und
  Einzelversuche, Desktop sowie 390×844 mobil; kein horizontaler Seitenüberlauf,
  Tabelle separat scrollbar. Keine Konsolenfehler; bestehende Tailwind-Runtime-
  Warnung weiterhin vorhanden. Kein Produktionsscan, Mailversand oder Login-
  /Kontoeingriff. Viewport zurückgesetzt. Bundleprüfung: `8f8a6c0c5bae`.
- [x] Unabhängige lesende Diffprüfung: keine belegte Privacy-/GET-Schreib- oder
  Versandvertragsregression. Ungefilterter Gesamtlauf dieses Checkouts:
  **11.699 bestanden, 97 fehlgeschlagen, 6 übersprungen**; Fehler ausschließlich
  in den vier vorhandenen Deploy-Testdateien (Windows-/Retirement-Verträge),
  kein vollständig grüner Repositorylauf. XML:
  `output/mail-fix-qa-cdcaba96bf8d4fa79dbc5316412cf8ce/results.xml`.
- [x] Frischer breiter Produkt-Nachlauf der finalen Diagnose:
  **12.070 bestanden, 2 übersprungen, 0 Fehler/Errors**; nur
  `test_deploy_auto_update.py`, `test_deploy_migration.py`,
  `test_deploy_retirement.py`, `test_deploy_security_reaudit.py` ausdrücklich
  ausgenommen. XML:
  `output/mail-fix-qa-59f86c89f6dc4b2c851e43bb7f074673/results.xml`.
  Die 97 Fehler des ungefilterten früheren Gesamtlaufs werden dadurch nicht
  als repariert behauptet. Eigener QA-Server/-Tab geschlossen; Nutzer-Tab erhalten.
- [x] Veröffentlichung ausdrücklich freigegeben und produktseitig abgeschlossen:
  genau sechs Produkt-/Testdateien, keine privaten Exporte oder fremden Änderungen.
  Frischer Testlauf aus dem tatsächlichen Git-Index-Snapshot:
  **12.074 bestanden, 2 übersprungen, 0 Fehler/Errors**, dieselben vier Deploy-
  Testdateien ausdrücklich ausgenommen; Kalender-/Commerce-/Deploy-Dateien in
  unveränderter HEAD-Version. Getesteter und committeter Tree:
  `9cbd25710cb2b38c61ae58db306171dece6a14eb`.
  XML: `output/release-verification-20261008-mail-diagnostics-ffc5c6f0aab2/qa-5f49ff7ebbc0/results.xml`.
  Unabhängige Veröffentlichungsprüfung: keine belegten Code-/Datenschutzblocker
  und keine Abhängigkeit vom ausgeschlossenen WIP. Bundle `8f8a6c0c5bae` bestätigt.
  TODO und Übergabe begleiten den Produktcommit als separater Dokumentationsstand.
- [ ] Operatorinstallation nach Ende laufender Scans mit normalem `git pull
  --ff-only origin main`, anschließend API/BG-Neustart und `/api/health` prüfen.
  Kein Deployskript, Installationswechsel, automatischer Scan oder Testmail.
  Vererbtes Deploy-/Installations-WIP unverändert erhalten; kein `git add -A`,
  Restore des gelöschten Deployskripts oder Server-Eigentumsumbau.
- [ ] Danach passenden aktuellen Lauf bis Freigabe → finale Prüfung → Sender
  → SMTP → **echte Signal-Mail im Postfach** nachweisen. Diese Reparatur erklärt
  bisher fehlende Diagnosen, repariert/beweist nicht selbst die wochenlange
  fehlende Signalzustellung. Keine neue Testmail oder Regelaufweichung auf Verdacht.
- [ ] Reale BI-Dreimonats-/Wochenreport-Kohorten und Originalkerzen-Levelchecks
  bleiben eigene offene Nachweise; nicht aufgrund dieser Diagnose als erledigt markieren.

## 08.10.2026 – Früherer Einstieg für den nächsten Account (historisch)

**Zuerst lesen:** [Aktuelle Account-Übergabe](docs/HANDOFF_SIGNAL_MAIL_2026-10-08.md).
Dieser Einstieg und der folgende Livebefund ersetzen ältere Pull-/Restart-
Aufträge und Aussagen „noch nicht auf Hetzner“ für das veröffentlichte Paket.
Ältere Abschnitte bleiben als historische Prüfprotokolle erhalten; ihre offenen
Checkboxen nicht ohne zeitlich passende Evidenz als heutige Fehler übernehmen.

- [x] Übergabe vorbereitet: bestätigter Serverstand, genaue Diagnose-Lücken,
  Quell-/Test-Einstiege, offene Backtest-/Tracker-/Levelnachweise und geschütztes
  vererbtes WIP dokumentiert. Kein neuer Chat/Account-/Serverzugriff veranlasst.
- [x] **Priorität 1, in der späteren Reparaturrunde lokal abgeschlossen:** bekannte sichere Ablehnungsgründe erhalten
  und die bereits gespeicherte abgeschlossene Mailprüfung sicher sichtbar machen.
  Stand und Grenzen oben; nicht alle technischen IDs individuell übersetzt.
  Mit Fehlerreproduktionen abgesichert; Sender-/SMTP-Regeln unverändert.
- [ ] **Priorität 2:** danach aktuellen passenden Lauf bis Sender, SMTP-Annahme
  und tatsächlichem Postfacheingang nachweisen. Technische Testmail ist kein
  Signalnachweis; neue Testmail, Mailpräferenzen oder Versandplan nicht automatisch ändern.
- [ ] **Priorität 3:** realen BI-Dreimonatslauf, belegte Wochenkohorte sowie
  fehlende Originalkerzen für VIAV/AST/LSPD getrennt abschließen. Vorhandene
  historische Studien berücksichtigen; keine unbelegte Trefferquote berechnen.
- [x] Diese Übergaberunde ändert nur `TODO.md` und die neue Übergabedatei;
  **lokal, nicht committet/gepusht**. Keine neue Programmcode-, Scan-, Mail-,
  Datenbank-, Konfigurations- oder Serveränderung. Kein neuer Testlauf gestartet.

## 08.10.2026 – Erneut keine Signal-Mails: aktuelle Liveprüfung offen

- [x] Nach Nutzeranmeldung direkt in der App und über den öffentlichen
  `/api/health` geprüft: Hetzner tatsächlich `healthy`, Revision
  **`b7415f1cd71f`**, Bundle **`ba7e64a7b792`**. Fehlendes Deployment ist für
  diesen Befund ausgeschlossen; kein weiterer Pull oder Neustart erforderlich.
- [x] Admin-Mailversand, Stand **08.10. 07:35:11** (Browseranzeige): 1 Swing-
  und 1 Crypto-Empfänger; SMTP-Annahmen 0, ausgelassen 5, Versandfehler 0,
  Warteschlange 0. Beschränktes Prozess-/24h-Fenster, keine Tages-/Wochenbilanz.
  Sichtbare Aktienentscheidung 07:30:06 nennt Tagesqualität unter 78,
  Referenz-Tageshoch an/nahe TP1 und dünne 20T-Grundliquidität. Gründe können
  verschiedene Kandidaten betreffen. Crypto meldet fehlende Freigabe/BTC-Kontext.
- [x] Momentaner Momentum-Cache: 13 Kandidaten, 0 vorgeprüfte Signal-Mails.
  RELL live einzeln gelesen: Trade-Score 95, Setup-Score 92, Tagesqualität 86/96
  bei Minimum 78; Mailgrund `momentum_mail_blocked_thin_baseline_liquidity`.
  Offener Rücktest ist eine separate Warnung, nicht dieser Sperrgrund. Codefloor
  2 Mio. USD Median-Dollarvolumen20; konkrete Live-Medianzahl/Originalhistorie
  nicht in der UI vorhanden, deshalb keine fachliche Einzelvalidierung behaupten.
- [x] Zwei unabhängige Quellprüfungen: keine neue SMTP-/Senderregression belegt;
  0,5%-Tageshoch-/TP1-Regel und native Gegenbarriere sind bestehende Verträge,
  nicht derselbe Fehler wie frühere Eigenkerzen-Barrieren. Kein neuer Rechen-
  reproduzierer. Ein eigener enger Offline-Test hing vor pytest-Ausgabe und
  wurde beendet; keine Testbestätigung daraus und keine Produktänderung.
- [x] Belegte Diagnose-Lücke später lokal repariert; damaliger Admin zeigt 07:32:12
  `Versandgrund noch nicht zugeordnet`. Bekannte `no_candidates`, einige
  `final_*`-/Tracker-/Doppelschutzgründe fehlen in der sicheren Übersetzung;
  aus dem UI-Eintrag lässt sich sein konkreter Rohgrund nicht rekonstruieren.
  Vorhandene Admin-Systemlogs sind leer und lesen eine Datei, während die
  ausgelieferten Dienste ins Journal schreiben. Das beweist keinen Versandfehler.
- [x] Abgeschlossene Mailprüfung später lokal sicher sichtbar gemacht: persistierte Blatt-
  und Sweep-Versuche enthalten `mail_audit`, aber die normale Ergebnis-API
  entfernt `latest_attempt.diagnostics`; Scheduler-RAM liefert vollständige
  abgeschlossene Mailprüfungen nicht zuverlässig. Frühere Annahme eines
  vollständigen langlebigen API-Zugangs war falsch und ist hier korrigiert.
  Nur überprüfte Codes/Zähler ausgeben, keine Rohtexte, Adressen oder Geheimnisse.
  Reparatur-/Teststand oben; ursprünglicher Liveeintrag nicht rückwirkend rekonstruiert.
- [ ] Nach dieser Diagnosereparatur echte Freigabe → letzte Prüfung → Sender
  → SMTP → Postfach belegen. Aktueller begrenzter Befund erklärt Vorab-
  Ablehnungen, nicht abschließend die gesamte wochenlange Versandgeschichte.

- [x] Aktuellen Checkout und Veröffentlichung abgeglichen: `b7415f1` enthält
  die Dokumentation zu `8d119ad`; das separate Deploy-/Installations-WIP bleibt
  unverändert. Die veröffentlichte Reparatur vereinheitlicht die App-/Mail-
  Vorprüfung, ist aber kein Nachweis tatsächlicher Signalzustellung.
- [x] Interner Browser aktuell abgemeldet vorgefunden. Alpha-Station-Login
  geöffnet und Nutzer um Anmeldung gebeten; kein erneuter Export oder Testmail.
  Bestehender SSH-Zugang mit BatchMode/StrictHostKeyChecking rein lesend geprüft:
  Server lehnt ihn mit `Permission denied (publickey,password)` ab. Keine
  Zugangsdaten gelesen, keine Server-/Kontoeinstellung geändert.
- [x] Unabhängige lesende Quellprüfung grenzt die benötigten Livebelege ein:
  Admin `/api/email-alert-audit` für Empfänger und Versandentscheidungen;
  `/api/scan-results` für den gespeicherten letzten Versuch
  (`diagnostics.latest_attempt`: `attempt_run_id`, `code_revision`, Status,
  jedoch ohne dessen vollständige Diagnose; siehe Korrektur oben). Ein sicher
  verfügbarer `mail_audit` unterscheidet Ablehnung vor dem Sender von
  `trade_sender_called` und tatsächlichem Transportausgang. Admin-Maximum
  50/24h seit Prozessstart nicht als Wochenstatistik ausgeben; Warteschlange 0
  schließt einen fehlgeschlagenen direkten Signalversand nicht aus.
- [x] Nach Anmeldung aktuelle Serverrevision und sichtbaren Aktien-/Versandstand
  geprüft; fehlende vollständige finale Diagnosen bleiben oben offen. Alten
  Livebefund vom 06.10. nicht als heutige Ursache dargestellt.
- [ ] Konkreten neu belegten Fehler gegebenenfalls reproduzieren und beheben;
  keine weiteren spekulativen Filter-/SMTP-Änderungen. Echter regulärer
  Signalversand und Postfacheingang bleiben offen.
- [x] In dieser Nachfrage nur Diagnose und lokale TODO-Fortschreibung; kein
  Programmcode geändert, kein Commit/Push, kein Pull/Neustart/Scan/Mailversand.

## 07.10.2026, 07:56 Zürich – Geprüfte Reparaturen veröffentlicht

- [x] Nutzer hat Commit und Push ausdrücklich freigegeben („machen“).
- [x] API, Frontendquelle und Bundle unverändert gegenüber dem zuletzt
  geprüften Stand; finale Hashes im Abschlussbericht bestätigt. Private
  Exporte und sonstige `output/`-Artefakte werden nicht veröffentlicht.
- [x] Genau ausgewählte Produkt-/Testdateien aus dem Git-Index in einen
  separaten Release-Snapshot exportiert. Breiter isolierter Produktlauf dort:
  **11.569 bestanden, 2 Windows-Skips, 0 Fehler/Errors**, 452,5s laut XML.
  `output/release-verification-20261007/qa-8b465e7207f5/results.xml`.
  Vier unveränderte test_deploy*.py-Dateien wie zuvor explizit ausgenommen;
  Commerce-/Kalendertests diesmal in ihren unveränderten HEAD-Versionen.
  Runtime-/Test-Snapshot unverändert; nachträgliche Änderungen nur anonymisierte
  Dokumentation und QA-Ergebnis. Keine volle Repo-/Linux-/SMTP-Prüfung behaupten.
- [x] Veröffentlichungsumfang unabhängig geprüft: keine Runtime-Abhängigkeit
  von ausgeschlossenem WIP; Bundle in-memory bytegenau reproduziert, Fingerprint
  `ba7e64a7b792`. Persönliche Kontokonfiguration aus Bericht/TODO neutralisiert;
  keine Zugangsdaten, Empfängeradressen oder private Originalexporte im Stage.
- [x] Begrenztes Releasepaket aus gemeinsamer App-/Mailvorprüfung, BI-
  Startfeedback, gemeinsamem Datenfehlerbudget, Regressionstests und
  zugehöriger Dokumentation committet und auf `origin/main` gepusht:
  **`8d119ad4b8e39bf7c38ecaf956a7555e82f1798f`**. Push bestätigt.
  Vorhandenes separates Deploy-/Installations-/Commercial-WIP bleibt lokal;
  kein Safe-Deploy-Aufruf, kein Serverumbau, keine Produktionsmutation.
- [x] Nach Veröffentlichung TODO und Berichte auf den tatsächlichen Stand
  fortgeschrieben; abschließender Dokumentationscommit enthält keine Runtime-
  oder Teständerung. Private Exporte und separates WIP weiter unversioniert/lokal.
- [x] Serverrevision und Bundle am 08.10. live bestätigt: `healthy`,
  `b7415f1cd71f` / `ba7e64a7b792`. Damit ist das veröffentlichte API-Paket
  aktiv; konkreter Betreiber-Pull/-Restart und beide Dienste wurden nicht einzeln
  beobachtet. Kein erneuter Pull/Restart wegen dieser Dokumentationsübergabe.
- [ ] Tatsächliche reguläre Signalzustellung bleibt gesonderte Liveprüfung.
  Frühere technische Testmail nicht erneut verwenden oder als Signalzustellung ausgeben.

## 06.10.2026, 21:30 Zürich – Gemeinsame App-/Mailvorprüfung geprüft (lokal)

- [x] Nutzerauftrag „alles machen“ für die vier gezielten nächsten Schritte
  aufgenommen. Vorhandenes WIP erhalten; kein Reset, Commit, Push oder
  Serverupdate und keine weitere technische Testmail.
- [x] Nachgewiesene falsche grüne Freigabe korrigiert: App und lesende Admin-
  Vorprüfung verwenden die bestehende spezifische Aktien-Qualitätsprüfung
  zusammen mit den allgemeinen Gates. Tagesreferenz und Scannerkurs werden
  anhand der bereits bestehenden Senderregel verglichen; keine neue Toleranz.
  Empfänger, Doppelschutz, Markt-/Versandzeit und finale aktuelle Kursprüfung
  bleiben separate Sendeschritte. Schwellen/Strukturpläne nicht gelockert.
- [x] Wiederholte Dekoration verwendet die ursprünglichen Producer-Felder;
  abgeleitete Scores/BEOBACHTEN dürfen nicht in die nächste Bewertung
  zurückfließen. 57 neue Backend-Fälle, gezielt 424 bestanden (inklusive
  vorhandener Tests): `output/mail-fix-qa-c7f9900dc28343f5869a9686bce5f077/results.xml`.
- [x] Vollständiger Nachlauf fand drei unregistrierte Datenfehler im Decision-
  Mapping. Sechs zusätzliche echte App-/Admin-Consumer-Regressionsfälle RED;
  jetzt exakt diese drei Gründe NO_TRADE statt WATCH, keine Whitelist und
  keine Senderänderung. Neue Backenddatei 63 Fälle. Zusammen mit Registry und
  auf den gemeinsamen Vertrag aktualisiertem Sichtbarkeitstest 242 bestanden:
  `output/mail-fix-qa-51c2e5805e47446582cef24542d4b125/results.xml`.
- [x] Kurze Kandidatenanzeige nennt die tatsächliche Qualitäts-/Datenblockade;
  Rücktest bleibt sekundäre Warnung. Sperrgrund nach Warnung 12 verschwindet
  nicht mehr aus der Kurzfassung, Details bleiben begrenzt. Finale gezielte
  Oberfläche: 131 bestanden, kein Fehler/Skip; vorhandener anyio-Hinweis:
  `output/mail-fix-qa-b1179452807a44f88c1f5642306375cd/results.xml`.
- [x] Bundle frisch gebaut: `ba7e64a7b792`. Desktop und 390×844 im internen
  Browser mit ausdrücklich synthetischem, nur lokalem GET-Stub geprüft:
  Qualitätsblockade/Detailgrund stimmen überein, gültige Freigabe trotz
  offenem Rücktest bleibt grün; keine zusätzlichen Textblöcke. Keine echten
  Scans, Kontoeinstellungen oder SMTP-Aufrufe. Viewport zurückgesetzt.
- [x] Gezielten LSPD-Reader abschließend geprüft. Root-Vollprüfung fand nach
  ersten grünen Fixtures einen abweichenden JSON-Key zwischen Python und
  PowerShell; echte Reader-Antwort statt unabhängig handgeschriebener Wrapper-
  Fixture reproduzierte den Fehler. Korrigiert: 33 bestanden, ein Windows-
  Symlink-Test übersprungen. `output/mail-fix-qa-bdb9cfa7aedd4e22b4cd6b8c6cf45c61/results.xml`.
  Einmalige gezielte LSPD-Abfrage erfolgreich eingegangen; nicht erneut anfordern.
- [x] Ursprünglichen LSPD-Plan-/Zonenbeleg Root/unabhängig gelesen:
  `output/profitability/lspd-plan-evidence-20261006T191129Z.json` (privat,
  nicht veröffentlichen), SHA256 `DD7C42DC8BEA23FFA666D3D96B4EAB81D7329A8C87EDF85900C5C698584AEF96`.
  106 eindeutige Zonen/269 Evidenzen; IDs, Bounds, Cutoffs, Bestätigungen intern
  konsistent. Entry 10,21 innerhalb erster Zone 10,13109–10,22891. Native
  Auswahl reproduziert WAIT_BREAK_RECLAIM/0R Barrierenraum; fehlender Rücktest
  ist nicht alleinige Ursache. Gespeichertes 50/50-R:R 0,21 arithmetisch korrekt.
  Kein belegter falscher LSPD-Ausschluss, keine Regeln gelockert.
- [x] Begrenzte Rundungsprüfung: außerhalb liegende LONG/SHORT-ACCEPT-Pläne
  behalten konservative Targets/R:R; zwei Overlap-Proben runden anders, bleiben
  WAIT und nicht freigegeben. Vier reine Offline-Komponentenproben bestanden,
  kein vollständiger Scanner-/Mail-Replay und keine Quelländerung daraus.
- [ ] Vollständiger ursprünglicher LSPD-OHLCV-/VRVP-Replay: Originalkerzen
  fehlen im Ergebniscache; begrenzte bestehende Evidenzsuche fand sie nicht.
  Späteren Chart nicht rückwirkend als Beweis benutzen, keine vollständige
  fachliche Levelrichtigkeit aus bloßer Zonen-Konsistenz behaupten.
- [x] Vollständiger isolierter Lauf abgeschlossen: 11.722 bestanden, 4 Fehler,
  6 Skips, keine Errors. Zwei fachliche Testabweichungen danach oben korrigiert.
  Zwei unveränderte 30-Sekunden-Windows-Bash-Timeouts separat ohne höhere
  Timeouts wiederholt: 2 bestanden. Ergebnisse nicht zu einem vollständig
  grünen Gesamtpass umdeuten. `output/mail-fix-qa-7cb256a31e574332a523c1b3a0964a96/results.xml`
  und `output/mail-fix-qa-acf662242675495bbf9f21bee29d86dc/results.xml`.
- [x] Abschließender breiter Produktlauf auf API-Hash `F198AC728D405CF8C59E24051AC010A0C6BCAD12C7B370B7551733BB269A7530`
  abgeschlossen: 11.565 bestanden/2 Windows-Skips/0 Fehler oder Errors,
  472,5s; Root las XML und bestätigte API-/HTML-/Bundlehash danach:
  `output/mail-fix-qa-4ee1ba82632248c6bb17f82cb0591cff/results.xml`.
  Nur vier unveränderte test_deploy*.py-Dateien ausgenommen, deren erste
  Gesamtlauf-/Wiederholungsevidenz bleibt separat. Keine neuen externen Aufrufe.
- [x] Aktuelle lesende Livekontrolle 21:17–21:26: SMTP 0/Auslassungen 50/
  Fehler 0/wartend 0 im begrenzten Fenster; kein Wochen- oder Postfachnachweis.
  ECHO aktuell weiterhin grün trotz Tagesqualität 70/96; Details sperren die
  Mail ausdrücklich `<78`, nicht wegen Rücktest. Genau diese Abweichung lokal
  korrigiert; alte generische Admin-Vorprüfung 46/1 ist kein Versandnachweis.
  Kein Scan/Testmail/Einstellungswechsel. Temporärer Agent-Tab geschlossen.
- [ ] Veröffentlichung/Deployment separat erledigen, anschließend echte
  Signalentscheidung → finale Revalidierung → SMTP → Postfach prüfen. Bis dahin
  keine komplette Ursachenbehebung oder tatsächliche Signalzustellung behaupten.

[Abschlussbericht](docs/CACHED_MAIL_ADMISSION_REPAIR_2026-10-06.md).

## 06.10.2026, 19:51 Zürich – Nächster gezielter Reparaturschritt

- [x] App weiterhin angemeldet lesend geprüft; kein Scan oder Mailversand
  gestartet. LSPD-Details bestätigen weiterhin Qualität 96/96, Entry 10,21,
  Gegenbarriere 10,23, Plan-R:R 0,21 und Trade-Score 45. Die UI zeigt nicht
  den vollständigen ursprünglichen Zonen-/Kerzenbeleg.
- [x] Evidenzlücke eingegrenzt: native Cachezeilen speichern `Level_Structure`
  mit Zonenbounds, `confirmed_at`, Rollen, Break-/Reclaim-Evidenz und
  Quellbelegen. Die allgemeine private Exportprojektion entfernt bewusst
  Ticker, IDs und Herkunftsblöcke; nicht ungeprüft erweitern.
- [ ] Zuerst eine aktuelle native LSPD-Cachezeile und die dazugehörigen
  abgeschlossenen Kerzen bis zum selben `as_of` als begrenzten Beleg lesen;
  gespeicherten Scanplan nicht mit einer später neu berechneten Chartstruktur
  gleichsetzen. Dann reproduzierbare Fehlablehnung als RED-Test absichern.
- [ ] App und spezifische Mailvorprüfung auf denselben vollständigen
  Freigabevertrag beziehen; Empfänger-/Doppelschutz und zeitpunktabhängige
  finale Revalidierung separat belassen. Nicht alle Warnkandidaten mailen und
  keine Qualitätsschwellen ohne explizite Regelentscheidung senken.
- [x] In dieser Nachfrage keine Programmcode-/Serveränderung. Nur lesende
  Nachprüfung und TODO-Fortschreibung; keine Reparatur als erledigt behaupten.

## 06.10.2026 – Systemische Nachfrage: Wochen ohne Signal-Mails

- [x] Einzelne ECHO-Sperre nicht als Erklärung für mehrere Wochen verwendet.
  Vier unabhängige Teilprüfungen: historische Periodenbelege, native Level-/
  Plangates, zusätzliche Mailgates und Dispatch/Doppelschutz.
- [x] 22 vorhandene Export-Momentaufnahmen getrennt nach Revision gelesen:
  frühe Daten-/Scanabbrüche; später abgeschlossene Runden mit 48 beziehungsweise
  30 Kandidaten und keinen Transportereignissen. Native Strukturpläne nur
  2/48 beziehungsweise 0/30; dominante Gründe erste Gegenbarriere/R:R und
  unbestätigte überschrittene Zonen. Keine lückenlose Wochenstatistik behaupten.
- [x] Neuere angemeldete Livekontrolle: Momentum 12.607/12.607, 46 Ergebnisse,
  Referenz 05.10., Ergebniszeit 06.10. 16:46:17.170500 Serverzeit. Scores
  2×83, 4×69, 40×45. Früheren Nachtabbruch ausdrücklich nicht fortschreiben.
- [x] LSPD als konkreten gemeinsamen Engpass gelesen: Setup 94, Qualität 96,
  Trade 45; Entry 10,21/Stop 10,04/TP1 10,23/TP2 10,26, Plan-R:R 0,21.
  Gemischte bestätigte Swing-High-/Low-Quelle 1D/1W. Original-Zonenbounds und
  Bestätigungszeiten fehlen; kein belegter falscher Widerstand daraus ableiten.
- [x] Mixed-role-Zonenmechanismus LONG/SHORT nativ reproduziert. Erweiterter
  Zonenrand setzt Bestätigungsanker zurück und kann kompletten Plan blockieren.
  Nachprüfung: bestehender ausdrücklich getesteter konservativer Vertrag,
  kein neu bewiesener Implementierungsfehler. Nicht mit LSPD gleichsetzen.
- [x] Zusätzliche Tageshoch-/TP1-Nähe-Regel mit realer ATR/Scoring/Qualität/
  Health/1D-Struktur isoliert nachgestellt: gültiger Plan bei Score 92,
  Qualität 83, TP1 1,82R trotzdem gesperrt; positive Kontrolle besteht.
  Vier Probe-Assertions unabhängig wiederholt. Regel ausdrücklich geschützt;
  keine Lockerung oder vollständige Senderprüfung behaupten.
- [x] Dispatch/Doppelschutz ohne globalen Blockierungsnachweis geprüft:
  tickerbezogene Gleichwertigkeit, Marker erst nach erfolgreichem Versand,
  erfolgreiche Teilrunden bleiben versandfähig. 162 + 8 Tests bestanden;
  Levelnachprüfung 136 bestanden. XML-Zähler bestätigt, Mengen überlappen.
- [x] Drei vorläufige Trackerzeilen im 30-Tage-UI-Fenster gelesen; ältere
  reife Statistik nicht mit neuem Versand verwechselt. Technische Testmail
  bereits vom Nutzer als angekommen bestätigt; keine weitere versendet.
- [ ] Gemeinsamen Struktur-/Freigabevertrag an aktuellen Originalkerzen mit
  Zonenherkunft, Bounds, Bestätigungszeit, Rolle und Ausbruch kritisch prüfen.
  Bewiesene Rechenfehler korrigieren; bestehende konservative Vertragsregeln
  nicht ohne explizite Änderung als Bug lockern. Kein allgemeiner SMTP- oder
  mathematischer Defekt in dieser begrenzten Nachprüfung neu nachgewiesen.
- [ ] App-/Mailfreigabe eindeutig angleichen: generische Cachevorprüfung ist
  keine vollständige spezifische Momentum-Mailfreigabe. Bereits bestehende
  offene Vertragsfrage bleibt; ein einzelnes ECHO erklärt nicht die Periode.
- [ ] Vererbte lokale BI-Start-/Datenkohortenreparaturen separat veröffentlichen,
  Serverrevision und neuen vollständigen Lauf bestätigen; finale tatsächliche
  Signalzustellung bleibt offen.
- [x] In dieser Nachfrage ausschließlich neue lokale Diagnoseartefakte und
  Dokumentation, keine Programmcodeänderung/Commit/Push/Servermutation.
  API-/HTML-/Bundle-Rohhashes unverändert; bestehendes WIP erhalten.

[Systemischer Prüfbericht](docs/SIGNAL_MAIL_SYSTEMIC_AUDIT_2026-10-06.md).

## 06.10.2026, 18:27 Zürich – Konkrete ECHO-Mailblockade nach erfolgreichem Lauf

- [x] Neue angemeldete Livekontrolle durchgeführt, keine Testmail/Scansteuerung/
  Einstellungen verändert. Browser hatte zunächst keinen Tab; normales Öffnen
  der bekannten App-Adresse stellte die bestehende Anmeldung bereit.
- [x] Maildiagnose 18:27:40: 1 Swing-/1 Kryptoempfänger, 0 SMTP-Annahmen,
  50 Auslassungen, 0 Fehler/0 wartend im begrenzten Fenster. Kein vollständiger
  Tages- oder Postfachnachweis. Aktuelle Momentum-Skips 16:50/17:50 nennen
  unter anderem Tagesqualität unter 78/96; Einzelereignisse sind nicht tickerbezogen.
- [x] Früheren Nachtstand nicht als heutigen aktuellen Scanstatus fortgeschrieben:
  Momentum jetzt 12.607/12.607 geprüft, 46/46 Ergebnisse, Referenzschluss
  05.10.2026, Ergebniszeit 06.10.2026 15:28:14.312302 Serverzeit. Erfolgreichen
  aktuellen Ergebnisstand gelesen, nicht durch manuellen Start erzeugt.
- [x] Konkreter Unterschied am ECHO-Kandidaten: Trade-Score 83/Grade A,
  App „Im Scan freigegeben“, Tagesqualität 70/96. Detailansicht sperrt Signal-Mail
  ausdrücklich wegen Momentum-Mailminimum 78/96, nicht wegen fehlendem Rücktest.
  Angezeigte Teilpunkte 17,929709 + 7,95314 + 14,487289 + 20 + 10 = 70,370138,
  gerundet 70. Dies prüft die angezeigte Summe, nicht erneut die Original-OHLC.
- [x] Quellpfad unabhängig gelesen: Admin-Sammelvorprüfung nur allgemeiner
  Cachecheck (`api.py:10771`); spezifischer Momentum-Mailcheck dagegen vor
  Versand (`14419`) und in Kandidatendetails (`17273`). `<78` erzeugt
  `momentum_mail_blocked_daily_quality_below_threshold` (`9564`).
  Admin „Aktienstrategien 46/1“ ist daher kein vollständiger Mailfreigabenachweis.
  Erstbezeichnung „mailfähig“ im Zwischenbericht ausdrücklich korrigiert.
- [ ] Nutzervorgabe für App-/Mailvertrag klären: zusätzliche Momentum-Mail-
  Qualitätsfilter beibehalten und Freigabeanzeigen eindeutig angleichen, oder
  Mailauswahl auf gültige App-Freigaben umstellen. Keine Filter eigenmächtig
  senken, keine Levels erfinden; Daten-/Plan-/Empfänger-/Doppelschutz bleiben.
- [x] Lokale Veröffentlichung separat geprüft: HEAD bleibt `e83bb1d6abdd`,
  BI-Startfeedback und Datenkohortenpaket uncommittet. Keine neueren Serverexporte
  als 26.09. vorhanden, keine heutige Serverrevision daraus behauptet.
  Nur Diagnose/Dokumentation in dieser Nachfrage; kein neuer Codefix oder Testlauf.

[Nachkontrolle im Bericht](docs/SIGNAL_MAIL_DATA_COHORT_AUDIT_2026-10-06.md).

## 06.10.2026 – Angemeldete Mailprüfung und Datenkohorten-Korrektur

- [x] Aktuelle Admin-Mailansicht nach Nutzeranmeldung gelesen (00:28:33 Zürich):
  1 Swing-/1 Kryptoempfänger, 0 SMTP-Annahmen, 50 Auslassungen, 0 Versandfehler,
  0 wartend im begrenzten RAM-Fenster. Keine Tages-/Postfachquote daraus ableiten.
- [x] Neue Vorprüfung gelesen: BI Long/Short 0 Kandidaten; Aktienstrategien
  37/0 freigegeben, Gap Long/Short 13/3 mit veralteter Sitzung und 0 freigegeben.
  Bear 1/0 Watch-only, Crypto Long 80/0; keine Senderfreigabe aus Cachezahlen.
- [x] Aktueller Momentum-Versuch fehlgeschlagen: `scan_data_invalid`,
  Aktienanalyse, 2.735/12.607 geprüft, 86 s. Turtle `scan_data_incomplete`;
  dessen nächsten automatischen Lauf ohne Start/Pause bis erneutem Fehler
  beobachtet. Altstände bleiben; kein Null-Signal-Abschluss.
- [x] Reihenfolgeabhängige Vermischung der Historien-/Referenz-Fehlerkohorten
  tatsächlich reproduziert: gleiche 20 Historienfehler + 1 Referenzabweichung
  + gültige Aktie → je nach Reihenfolge Abbruch oder unzulässiger Abschluss
  mit 21 Datenausschlüssen.
  Vertiefte Nachprüfung korrigiert die erste Interpretation: maximal 20
  ungültige Aktien insgesamt ist der dokumentierte Vertrag. Der erfolgreiche
  21er-Pfad war die Lücke. Beide Pfade und Cup-Spezialfilter nutzen jetzt den
  gemeinsamen Guard; `daily_reference_exclusions` ist rein diagnostisch.
  Keine pauschale Datenlockerung; heutige Liveursache weiter nicht zugeordnet.
- [x] Erstinterpretation verworfen, nicht ungeprüft veröffentlicht: 176/92
  erste Tests gelten nur historisch. Erster Gesamtlauf bewusst unterbrochen,
  keine vollständige grüne XML-Auswertung daraus.
- [x] 17 finale Grenzregressionen: RED 7 Fehler/10 bestanden mit echten
  isolierten Cachebytes. Zulässige 19+1/1+19, unzulässige 20+1/1+20/20+20 in
  beiden Reihenfolgen sowie homogene21, Cup-Spezialfilter und Diagnoseprojektion.
  `output/mail-fix-qa-d564a7ee9b544bcd84cf8fb9ece912ec/results.xml`.
- [x] Finales GREEN: 443 gezielte Tests bestanden; finale unabhängige
  Nachprüfung 208 bestanden, keine blockierenden Befunde. Beide XML-Zähler
  bestätigt, keine Fehler/Skips; vorhandener anyio-Hinweis. Zahlen überlappen.
  `output/mail-fix-qa-6a87127d801e40da9cabd17f14e3a142/results.xml` und
  `output/mail-fix-qa-70d90d7feb744b0cb888e8f5f6c4d59d/results.xml`.
- [x] Vollständigen isolierten Gesamtlauf und XML geprüft: 11.620 bestanden,
  1 Updater-Subprozess-Timeout nach 30 s, 5 Windows/Linux-Skips, 0 Sammelfehler.
  11.626 Fälle; kein vollständig grüner Gesamtpass behaupten. API-/HTML-/Bundle-/
  Regressionsquellhashes vor/nach identisch:
  `output/mail-fix-qa-d60de01b4e5b41b6a2d65db2fe4e92f7/results.xml`.
- [x] Einzigen fehlgeschlagenen Test unverändert isoliert wiederholt:
  `test_auto_update_passes_safe_directory_to_target_deploy` besteht in 18,32 s,
  XML 1/0/0/0. Timeout nicht reproduziert, konkrete Laufzeitursache unbewiesen;
  keine Zeitgrenze erhöht, kein Test deaktiviert, kein echter Deploy.
  `output/mail-fix-qa-e3d276d90be7409baaa5245f5e97d611/results.xml`.
- [x] Kontoeinstellungen ausschließlich lesend geprüft, nicht geändert;
  persönliche Kanal-/Modus-/Watchlist-Werte nicht veröffentlicht.
  Produktseitig schließt Swing normale Aktienstrategie-/Gap-Mails nicht aus. Persönliche
  Filter und Operator-Fallback unterscheiden sich; keine Empfängerursache aus
  der Moduswahl behaupten. System Health Cooldown 0 s, Scheduler aktiv.
- [x] Turtle-Regeln gegen 14 bestehende Fail-Closed-Verträge geprüft. Ein
  bloßer Sammelcode identifiziert den heutigen konkreten Grund nicht.
  BI-Ausschlusspolitik nicht ungeprüft auf Turtle übertragen.
- [ ] Aktuelle anonyme Momentum-Attempt-Zähler / Turtle-Kohärenzgrund lesen;
  angemeldete UI zeigt nur Sammelcode. System-Logs-UI 0 Zeilen, normaler
  Health-Tab Port 8000 clientseitig gesperrt; keine Sperre umgangen. Einmaliger
  direkter SSH-Leseversuch BatchMode abgewiesen; anonyme Attempt-Abfrage bereits
  angefordert, noch unbeantwortet. Erneute UI-Kontrolle 01:15:24 unveränderte
  SMTP-/Vorprüfzahlen, neuere Auslassungen; keine Postfach-/Tagesquote behaupten.
- [ ] Lokale Reparaturen veröffentlichen/Serverinstallation separat bestätigen;
  anschließend erfolgreicher neuer Scan, finale Freigabe, SMTP und Postfach.
  Noch kein Commit/Push/Serverupdate, keine weitere Testmail oder Scansteuerung.
- [ ] Separater Offlinebefund am Sitzungsschwellenwechsel absichern und beheben:
  nach Bulkabruf veränderter Wrapperzeitpunkt kann Daily-Modus über `all(...)`
  zu `live_snapshot` herabstufen; synthetische AST-Gegenprobe 20:14:59/20:15:00
  UTC ergibt `scan_data_unavailable` wegen fehlendem Livepreis. Kein aktueller
  `scan_data_invalid`-Nachweis; Zeit/Code passen nicht zum heutigen Versuch.
  Kein stale Datenstand für Mails zurückdatieren. Nichtatomare Bulk-/Einzel-
  Providerrevision ist eine zusätzliche offene Annahme, kein belegter Livefehler.

[Prüfbericht](docs/SIGNAL_MAIL_DATA_COHORT_AUDIT_2026-10-06.md).
Bestehendes fremdes WIP erhalten; normale Scan-/Mailberechnung nicht durch
erfundene Levels oder gelockerte Score-/Grade-/Rücktestregeln ersetzt.

## 06.10.2026 – Nachfrage zum Signal-Mailstatus

- [x] Aktuellen lokalen Übergabestand gelesen: BI-Startfeedback ist lokal
  repariert und geprüft, aber weiterhin uncommittet/ungepusht. Dieser
  Frontendfix ist kein Signal-Mailfix. Bestehendes fremdes WIP unverändert.
- [x] Nach neueren lokalen Serverexporten gesucht: jüngster vorhandener
  `hetzner-evidence-*.json` unter `output/profitability` bleibt vom 26.09.
  Kein aktueller Export und keine Antwort auf die bereits angeforderte
  anonyme `suppression_buckets`-Abfrage vorliegend.
- [x] Aktuellen internen Browser geprüft: kein bestehender App-Tab;
  einmaliges normales Öffnen von Alpha Station zeigt die öffentliche
  Landingpage ohne Anmeldung. Temporären Prüftab danach geschlossen.
  Keine Sicherheits-/Anmeldesperre umgangen, keine Zugangsdaten gelesen.
- [ ] Aktuelle Signalentscheidung und Transportereignisse nach einem neuen
  Lauf direkt prüfen. Letzte bestätigte angemeldete Versandkontrolle vom
  05.10. (0 SMTP-Annahmen im begrenzten sichtbaren Fenster, Ablehnungen vor
  SMTP) bleibt historisch; nicht als vollständigen heutigen Tagesstand
  oder Beweis eines defekten SMTP-Dienstes ausgeben. Angekommene technische
  Testmail belegt nur diesen einzelnen Versand, nicht die Signalpipeline.
- [ ] Bereits angeforderte anonyme Serverausgabe auswerten oder nach normaler
  Anmeldung die aktuelle Admin-Versandansicht lesen; keine weitere Testmail
  und kein weiterer Export notwendig. Echte Signalzustellung weiterhin offen.

Nur Statusprüfung und TODO-Dokumentation; kein Codefix, Push, Pull, Scanstart,
Neustart, Einstellungs-/DB-Schreibzugriff oder Mailversand in dieser Nachfrage.

## 05.10.2026 – BI-Startmeldung: laufender Short und Browserzugriff getrennt

- [x] Nutzerklärung: korrekt auf blauen Startknopf geklickt; das offene Aktien-
  Menü war nur zufällig im Screenshot. Menüüberdeckung ist keine bestätigte
  Ursache. Früherer BI-Short-Lauf erklärt nicht den aktuellen stillen Klick.
- [x] Tatsächlichen Frontendfehler offline unabhängig reproduziert:
  `ScanControl.startScan` lädt im `finally` nach jedem Versuch erneut die
  Ergebnisse. Ein erfolgreicher GET des unveränderten Altstands löscht den
  zuvor gesetzten Startfehler in `useScannerFeed.readResults`.
  POST 429/401/500/Transportfehler und Baseline-GET 503 verlieren so ihre
  Rückmeldung; Button wieder blau, kein bestätigter neuer Lauf. Bei Baseline-
  Fehler kein POST. Nachweise mit bestehendem Hook-Harness und dem tatsächlichen
  extrahierten Wrapper, ohne Produktionsanfrage. Kein Menü-/Bedienfehler.
- [x] Startversuchsfehler separat vom Ergebnis-Lesefehler erhalten (wie
  `blockedStartRef`), bis neuer bewusster Start oder eindeutig neuer Laufstand
  beobachtet wird. Auch passive GETs dürfen den unveränderten fehlgeschlagenen
  Versuch nicht still löschen; allein Entfernen des `finally` reicht nicht.
  Lokal implementiert, neu kompiliert und unabhängig geprüft. Konkrete
  HTTP-/Transportantwort beim Nutzer bleibt
  unbekannt; die reproduzierten Fälle nicht als dessen gemessene Antwort ausgeben.
- [x] TDD: 29 neue Regressionen mit tatsächlichem Hook und extrahiertem
  Buttonwrapper. Abschließend 191 gezielte und 112 unabhängige Tests bestanden;
  keine verbleibenden Reviewbefunde. Erststart ohne bekannte Baseline, ungültige
  alte Cachezeit, zukünftige Zeitstempel und verspätete Antworten abgesichert.
- [x] Gerenderte Vorher-/Nachherprüfung im internen Browser: Desktop und
  390 x 844. Startablehnung bleibt bei Button-/manuellem/passivem Altcache-GET;
  späterer angenommener Versuch und Long/Short-Wechsel korrekt. Nur synthetische
  localhost-POSTs, kein echter Scan/Versand. Screenshots und Ablauf unter
  `output/playwright/bi-start-feedback-browser-proof.md`; temporäre Server/Tabs
  geschlossen, Browsergröße zurückgesetzt.
- [x] Vollständiger isolierter Gesamtlauf: 11.604 bestanden, 0 Fehler,
  5 Windows/Linux-Ausnahmen, ein vorhandener anyio-Importhinweis.
  XML `output/mail-fix-qa-b0e0e47f1900438291ab7622a18126a6/results.xml`
  gelesen (11.609 Fälle); HTML/Bundle/Test sowie unveränderte API/Telemetrie
  vor/nach dem Lauf mit identischen SHA256. Neuer Bundlequellhash
  `18bd91c87dcc`; [Prüfbericht](docs/BI_START_FEEDBACK_REPAIR_2026-10-05.md).
- [ ] Dieses abgegrenzte Frontendpaket nach Freigabe committet/gepusht und auf
  Hetzner installiert bestätigen. Der Startfix ist derzeit nur lokal; kein
  Pullbefehl als vermeintliche Installation eines unveröffentlichten Fixes geben.
- [x] Live-UI gegen 18:38 UTC ohne manuellen Scanstart geprüft: BI Long mit
  aktivem Startknopf, letzter Abschluss 18:21:20 Serverzeit, 5.319/5.319 geprüft,
  0 gültige 17/20-Signale. Gleichzeitig BI Short aktiv, 1.810/5.319 geprüft,
  letzter Fortschritt 1 Sekunde alt. Long/Short teilen sich die Aktien-Engine.
- [x] Backend-Startsperre und ACK nachgerechnet: kein paralleler Long-Start
  während Short; `busy/other_scanner_running`, `blocking_scan_key=bi_short`.
  Kein neuer Lauf wird eingereiht. 171 isolierte Offline-Tests bestanden,
  0 Fehler/Skips; XML `output/mail-fix-qa-c49a1a1d20c84dad9ce6f3af35e2054a/results.xml`
  gelesen und Zähler geprüft. Keine Freigaberegeln oder Scannerquellen geändert.
- [x] Reiner öffentlicher Health-GET gegen 18:41 UTC bestätigt Betreiberupdate:
  `healthy`, Revision `e83bb1d6abdd`, Bundle `67924e9f894a`. Kein weiterer Pull
  allein wegen des Diagnosepatches erforderlich. CORS für Port-3000-Origin
  am Health-Endpunkt HTTP 200 mit passendem Allow-Origin/Allow-Credentials.
- [ ] Separater lokaler Browserblocker: ab 18:39:48 UTC scheitern BI-, Scheduler-
  und Systemstatusabfragen mit `Failed to fetch`; beim direkten Öffnen des
  öffentlichen Port-8000-Health-Endpunkts meldet der interne Browser
  `net::ERR_BLOCKED_BY_CLIENT`. Nach einmaligem Neuladen Landingpage statt
  angemeldeter App; das beweist keinen Passwort-/Serverdefekt. API-Zugriff
  im internen Browser wiederherstellen/mit normalem Browser vergleichen;
  keine Sicherheitssperre umgehen und keine Zugangsdaten übernehmen.
- [ ] Optionaler UX-Punkt: Long-Startknopf war trotz belegter gemeinsamer Engine
  aktiv. Tatsächliche Ablehnung wird nach POST erklärt; kein echter Startversuch
  in dieser Prüfung. Vorab klaren Besitzerhinweis statt irreführendem aktiven
  Knopf erst als separate Umsetzung bearbeiten. Die beim Nutzer sichtbare
  genaue Startmeldung ist bislang nicht übermittelt.
- [ ] Nach wiederhergestelltem Zugriff neuen Laufabschluss und konkrete
  Mailfreigabe/SMTP/Postfach prüfen. Startsperre und Browserfehler erklären
  nicht pauschal die bisher fehlenden Signal-Mails.
- [x] Anschließende unabhängige Quellprüfung des finalen Swing-Mailpfads:
  keine neue belegte mechanische Fehlablehnung/verlorener Senderaufruf gefunden.
  Daily-Pläne erhalten keine 5m/4H-Neuabhängigkeit; fehlender Rücktest allein
  sperrt keinen bestätigten Break. Positive simulierte Versandtests im
  Gesamtlauf erneut bestanden. Kein Beweis eines aktuellen echten Versands.
- [ ] Einmalig angeforderte anonyme Read-only-Serverausgabe
  `suppression_buckets` abwarten und nach Revision/Zeit/Grund auswerten.
  Kein weiterer Export oder Testmail nötig/angefordert. Die stündlichen,
  überlappenden Grundzähler sind weder eindeutige Kandidatenzahlen noch
  Sender-/SMTP-Annahmen. Danach nötigenfalls die vorhandenen anonymen
  `diagnostics.mail_audit.transport_events` mit `trade_sender_called`,
  `trade_accepted/partial/unknown/failed` zum aktuellen Lauf prüfen.

In dieser Arbeit kein Produktionsscanstart, keine Pause/Fortsetzung, kein
Serverupdate, Neustart, Einstellungs-/DB-Schreibzugriff oder Mailversand.
Lokales Frontendpaket, neue Regressionen und Prüf-/TODO-Dokumentation geändert,
noch uncommittet; bestehendes fremdes WIP bleibt erhalten. Neuer historischer
BI-Lauf, Wochenkohorte und Original-Chartdaten werden dadurch nicht erledigt.

## 05.10.2026 – Aktuelle Signal-Mailkontrolle nach Betreiberpull

Dieser Abschnitt aktualisiert die Betriebskontrolle nach dem bisherigen Reparaturpaket.

- [x] Live-Health 13:37 UTC: `healthy`, Revision `43180ffea286`,
  Frontend-Bundle `67924e9f894a`. Das Paket mit `f46413c` läuft jetzt auf Hetzner.
  Kein weiterer Pull allein wegen des bisherigen Pakets erforderlich.
- [x] Angemeldete App/Admin-Mailversand direkt geprüft: 1 Swing-/1 Kryptoempfänger,
  0 SMTP-Annahmen, 50 ausgelassen, 0 Versandfehler, 0 wartend im sichtbaren Fenster.
  Maximal 50 RAM-Events seit API-Start, höchstens 24h – kein vollständiger Tagesnachweis.
  Aktuelle Vorprüfung: 37 Aktienstrategie-, 13 Gap-Long-, 3 Gap-Short-Kandidaten,
  jeweils 0 mailfähig. Die Vorprüfung ersetzt keinen Abschluss/Versandnachweis.
- [x] DAC konkret nachgerechnet: Score 88/S und Tagesqualität 87/96 reichen aus,
  aber TP1 170,10 und TP2 174,23 sind Projektionen ab Entry 165,15
  (3 % / 5,5 %), keine bestätigten Strukturziele. `trade_target_not_structural`
  blockiert vor SMTP; der offene Rücktest ist hier nicht die Blockade.
- [x] Neuen automatischen BI-Long-Lauf bis Abschluss beobachtet, ohne Scanstart
  oder Neuladen: 05.10., 14:01:29 Serverzeit, 5.319/5.319 geprüft,
  4.186 analysiert, 120 Kursdatenfehler einzeln ausgeschlossen, 0 gültige Signale.
  Kein globaler Abbruch; fehlerhafte Aktien werden im nächsten Lauf erneut geprüft.
  Ergebnis/Fortschritt haben sich in der App automatisch aktualisiert.
- [x] Diagnosefehler lokal repariert: acht finale Swing-Ablehnungen bleiben mit
  konkretem anonymem Code in Admin/Telemetrie erhalten; leere Endauswahl und
  Mail-Sitzungswächter sind erklärbar. Unbekannte Gründe bleiben unbekannt;
  keine unbestätigte Aussage „Börse geschlossen“ oder garantiertes Serverprotokoll.
  Keine Änderung von Freigaberegeln, Sendern, Dedupe oder Daten-/Preisprüfungen.
- [x] TDD und unabhängige Nachprüfung: 184 gezielte Tests bestanden;
  ein bekannter anyio-Importhinweis. Neue Randfälle zunächst rot nachgewiesen.
  [Prüfbericht](docs/MAIL_LIVE_GATE_AUDIT_2026-10-05.md).
- [x] Eigenständiges anonymes Exportregister mit dem korrigierten App-Register
  synchronisiert. Danach 407 gezielte Mail-/Collector-/Historytests bestanden.
  Vorläufige Gesamtlauffehler samt Ursachen sind im Prüfbericht dokumentiert;
  Ergebnis der finalen Wiederholung mit unveränderten Quellen siehe unten.
- [x] Admin um 16:07 Zürich nachgeprüft: weiterhin keine SMTP-Annahme im
  begrenzten RAM-Fenster; neue generische Events um 16:06 nicht nachträglich
  zuordenbar. System Logs liefert 0 Zeilen und ersetzt kein systemd-Journal.
- [x] Finaler isolierter Gesamtlauf: 11.575 bestanden, 0 Fehler, 5 Windows/Linux-
  Ausnahmen, ein bekannter anyio-Importhinweis. XML und SHA256 vor/nach dem Lauf
  geprüft; Produkt-/Testquellen unverändert. Ausnahmen im Prüfbericht dokumentiert.
- [x] Zusätzlich den abgegrenzten Veröffentlichungsbaum geprüft:
  11.573 bestanden, 0 Fehler, 5 Plattformausnahmen. Kein fremdes Deploy-/Test-WIP
  in dieser Releasekopie. Git-Metadatenproblem der ersten ZIP-Prüfung im
  unveränderten Test reproduziert und durch korrekte Testumgebung behoben;
  kein Produkt-/Testfix oder verdeckter Skip. Nachweise im Prüfbericht.
- [x] Diagnosepatch auf ausdrückliches „go“ gezielt committet/gepusht:
  Codecommit `d566c5d973bd645270ae306cdbf7f6f77f0bba9b`, `origin/main` bestätigt.
  Genau sechs Dateien: API, Telemetrieregister, eigenständiger Collector,
  Regressionstest, Prüfbericht und nur dieser aktuelle TODO-Abschnitt.
  Fremdes Deploy-/Dokumentations-/Test-WIP und private `output/`-Nachweise
  bleiben unveröffentlicht. Abschlussdokumentation enthält keinen weiteren Produktcode.
- [x] Abschlussveröffentlichung `e83bb1d6abdd34a2bd08f2395170fd444f015790`
  auf `origin/main` bestätigt. Letzte reine Health-Lesung 05.10., 17:09:59 UTC:
  Server weiterhin `healthy`, `43180ffea286`, Bundle `67924e9f894a`.
  Erwartete Revision nach Betreiberpull: `e83bb1d6abdd`.
  Diese abschließende lokale Statusnotiz bleibt zusammen mit dem fremden TODO-WIP
  uncommittet; die veröffentlichte Abschlussdokumentation ist bereits auf GitHub.
- [x] Betreiberpull des älteren Diagnosepakets später öffentlich bestätigt:
  05.10., gegen 18:41 UTC, `healthy`, Revision `e83bb1d6abdd`, Bundle
  `67924e9f894a`. Kein Deploy-Skript oder Installationsumbau durch diese Arbeit.
- [ ] Neue konkrete Ablehnungsgründe und echte Signalzustellung prüfen.
  Serverrevision bestätigt die Installation, nicht eine Postfachzustellung.
  Der oben beschriebene spätere Frontend-Startfix ist noch unveröffentlicht.
- [ ] DAC-Originalzonen, `level_structure.as_of`, `completed_bar_counts` und
  Bestätigungszeiten prüfen: fehlt eine echte Gegenbarriere oder wurde sie übersehen?
  Aus dem angezeigten Projektionsziel allein ist das nicht entscheidbar.
  Zusätzliche unabhängige Quellprüfung ohne nachgewiesenen Richtungs-/Zeitfehler;
  Original-`zones/evidence`, Qualitätsflags und Vor-/Nach-VRVP-Zielentscheidung
  bleiben zur fachlichen Nachrechnung erforderlich. Nicht pauschal als erledigt markieren.
- [ ] Generische heutige Events nicht als Score-/Providerfehler ausgeben;
  Originalursache noch offen. SSH-Keyzugriff wird abgewiesen, geschützte
  Detail-API ohne Anmeldung liefert 401; keine Zugangsdaten aus dem Browser übernommen.
  Die Browseranmeldung war für diese UI-Leseprüfungen nutzbar; späterer
  Port-8000-Clientblock und aktuelle Landingpage stehen im neueren Abschnitt oben.
- [ ] Tatsächlich zulässige Signal-Mail → SMTP-Annahme → Postfach nachweisen.
  Technische Testmail kam früher an. Kein neuer Export oder Testmail verlangt/gesendet.
- [ ] Neuer historischer BI-Dreimonatslauf, Wochenkohorte und DAC/VIAV/AST-
  Originaldaten bleiben offen; durch die heutige Live-Diagnose nicht erledigt.

Heute kein Serverupdate, Neustart, manueller Scan, Settings-/Produktions-DB-Schreibzugriff oder
Mailversand. Bestehendes fremdes WIP bleibt erhalten. Nur der Diagnosepatch,
zugehörige Tests und diese Dokumentation wurden lokal bearbeitet.

## 05.10.2026 – Accountwechsel: verifizierte Übergabe und nächster Schritt

Arbeitsverzeichnis: `C:\Projekt\TradingBot`. Dies ist der aktuelle Einstieg für
den nächsten Account. Der technische Abschluss vom 04.10. bleibt darunter erhalten;
andere ältere Aufgaben werden durch diese Übergabe nicht pauschal als erledigt erklärt.

### Veröffentlicht und technisch geprüft

- [x] Lokales `main` am 05.10. geprüft: HEAD
  `43180ffea286efcd408e7805a8880d67fb67652a` (Dokumentationsabschluss).
  Zugehöriger Codecommit: `f46413c2d357475968109aa79269a9c07e21ddd7`.
  Beide wurden am 04.10. gepusht; Remotehash damals bestätigt.
  Am 05.10. keine neue Remoteabfrage, Veröffentlichung oder Serveränderung.
- [x] BI-Auswahl, Zeitgrenzen, Datenabdeckung, Kandidaten-/Plan-/Fill-Diagnose,
  Cachevalidierung und kompakte Anzeige repariert. Mail-Recovery schützt
  Reservierungsowner, bereits versuchte/angenommene Zustellungen und ungültige Zeiten.
  BI bleibt bei 17/20; Score-, Risiko- und Mailregeln wurden nicht gelockert.
- [x] Nachweise vom 04.10.: 11.274 Offline-Tests bestanden, 0 Fehler,
  1 Windows/POSIX-Skip; `tmp/qa-1b8536a9bad0/results.xml`.
  Zusätzlich 18 Desktop-/Mobilfälle mit Playwright grün; geprüfter Bundlehash
  `67924e9f894a`. Gezielte 445 Mail-, 151 Producer-, 89 Frontend/API- und
  45 Cache/API-Tests überlappen und werden nicht addiert. Heute keine neuen Tests.
  Ausnahmen des Gesamtlaufs und der frühere Windows-Queue-Timeout stehen im Bericht.
- [x] Ältere historische Stichproben als bereits dokumentiert übernommen:
  02.07.–01.10.2026, je 3 fixierte Assets, 18 Quelldateien und 88 Fingerprints.
  Damals 0 freigegebene Modellpläne; ohne gefüllte Trades ist die Trefferquote
  nicht berechenbar, nicht 0 %. BI damals 384 Prüfungen, maximal 13/20.
  Diese Ergebnisse gehören zum alten Modellstand, nicht zur Neubewertung vom 04.10.
  [Historischer Prüfbericht](docs/SCANNER_DEEP_AUDIT_2026-10-02.md),
  [private Stichproben](output/scanner-deep-audit-20261002/HISTORICAL_SAMPLE_REPORT.md),
  [private Abdeckungsmatrix](output/scanner-deep-audit-20261002/SCANNER_COVERAGE_MATRIX.md).
  Am 05.10. nur Dokumentationsstatus übernommen, keine Studien neu nachgerechnet.
- [x] Produktdateien im geprüften Paket haben aktuell kein neues uncommittiertes WIP.
  Git-Index leer. Bestehendes Dokumentations-/Deploy-/Test-WIP bleibt erhalten,
  insbesondere die lokale Löschung von `deploy/safe_deploy.sh` sowie das
  nicht getrackte Verzeichnis `output/` mit privaten Nachweisen.
  Nicht mit `git add -A`, Restore/Reset oder Aufräumen überschreiben/veröffentlichen.

### Offen – in dieser Reihenfolge fortsetzen

- [x] Aktuelle API-Revision und Frontend-Bundlehash nach dieser Übergabe geprüft:
  05.10., 13:37 UTC, `healthy`, `43180ffea286`, Bundle `67924e9f894a`.
  Der Betreiberpull ist damit live bestätigt; Details im aktuellen Abschnitt oben.
  API: `http://178.104.69.209:8000/api/health`; Port 3000 liefert das Frontend.
  `f46413c` enthält bereits die Code-Reparatur; `43180ff` ergänzt nur die TODO.
  Nicht allein wegen dieses Dokumentationscommits erneut Dienste neu starten.
  Kein automatischer Pull, Scanstart, Testmail oder Installationsumbau.
- [ ] Nach bestätigtem Codeupdate einen neuen BI-Backtest mit echten Daten prüfen:
  3 Monate, 200 Aktien, Mindestpreis 5, Mindestvolumen 200.000.
  Abdeckung, 17/20-Kandidaten, Ablehnungsgründe, Pläne, Einstiege und offene
  Folgefenster getrennt auswerten. Alte Ergebnisse werden nicht neu umetikettiert.
  Der gespeicherte Null-Lauf vom 03.10., 21:47:24 enthielt keine Diagnosen;
  seine konkrete Ursache lässt sich nachträglich daraus nicht ableiten.
- [ ] Mailkette live bis zur echten zulässigen Signal-Mail und zum persönlichen
  Empfang nachweisen: Scannerabschluss → Freigabe → Versandversuch → SMTP-Annahme
  → Postfach. Technische Testmail kam laut Nutzer an; neue Signalzustellung offen.
  Die 50 ausgelassenen Krypto-Entscheidungen ohne SMTP-Annahme/Versandfehler am
  04.10. sind nur ein begrenztes, zeitgebundenes Diagnosefenster. Sonntagsstatus,
  Montag-Zeitplan und damalige Anmeldung nicht ungeprüft als aktuell übernehmen.
  Fehlende Freigaben nicht durch pauschales Lockern der Kriterien umgehen.
- [ ] Wochenkohorte/Tracker-Ergebnisse anhand belegter Zustellungen und Kurswege
  erneut prüfen. Vorhandene historische Stichproben nicht als ungemacht behandeln,
  aber auch nicht als Quote des neuen Pakets ausgeben. Der alte Trackerexport
  endet am 26.09.; Herkunfts-/Tickerlücken und alter Algorithmus begrenzen ihn.
  Vollständige historische Scannerinputs und Netto-Ergebnisse bleiben offen;
  Richtung nach 5 Sitzungen/24 Stunden ist keine Netto-PnL. Offline-/UI-Tests
  schließen diese Lücken nicht. Ältere offene Grenzen unten beachten.
- [ ] Originalkerzen für die exakte VIAV-/AST-Chart- und Levelprüfung beschaffen
  beziehungsweise auswerten; die älteren Screenshots ersetzen diese Daten nicht.

### Sichere Weiterarbeit und Grenzen dieser Übergabe

- Primärer Reparaturbericht:
  [BI-Backtester und Mail-Recovery](docs/BI_BACKTEST_MAIL_RECOVERY_2026-10-04.md).
  Bei weiteren Tests `scripts/run_offline_tests.py` mit isoliertem Zustand nutzen;
  API/BG nicht direkt mit produktiver `.env` oder echtem SMTP importieren.
- BPIQ/Catalyst: das vom Nutzer gemeldete Aboende bei der Diagnose berücksichtigen;
  einen Provider-401 nicht mit einem App-Login- oder SMTP-Fehler gleichsetzen.
- Private Exporte unter `output/profitability/hetzner-evidence-*.json` sowie
  private QA-Artefakte nicht auf GitHub hochladen. Keine Passwörter übernehmen.
- Diese Bearbeitung vom 05.10. ändert nur `TODO.md`. Neue Übergabe noch nicht
  committet/gepusht; kein Server-, Scan-, Mail-, Datenbank- oder Einstellungszugriff.
  Die oben genannten Commits und Tests gehören zum abgeschlossenen Reparaturpaket.

## 04.10.2026 – Aktueller Abschluss: BI-Backtester und Mail-Reservierungen

Dieser Abschnitt ersetzt die älteren offenen Implementierungsstände unten.
Basis: `cabb5b17ee41dc93f40c0a43270ffe71e803b820`. Kein Serverupdate durch Codex.

- [x] BI-Auswahl auf die jüngsten 50 Sitzungen vor dem Testzeitraum korrigiert;
  gewähltes Mindestvolumen 200.000 wird nicht heimlich auf 500.000 angehoben.
  Signalgrenze, letzte abgeschlossene Signalkerze und belegte Handelsfenster korrigiert.
- [x] Abdeckung und vollständiger Trichter implementiert: ausgewählte Aktien,
  auswertbare Historien, 17/20-Kandidaten, Planablehnungen, Pläne, Fills und offene Fälle.
  Fehlende/ungültige Kurse bleiben Datenlücken; kein künstlicher Ersatz.
- [x] API und gespeicherte Berichte prüfen Rohzeilen, Zählerpartitionen und Provenienz.
  Der 150-Zeilen-Anzeigecap verfälscht nicht die vollständigen Zähler.
  Nur BI erhält eine neue Modell-/Cachekennung; ältere Dateien werden nicht gelöscht.
- [x] Kurze BI-Hauptanzeige; Diagnose und Methodik geschlossen. Offene Folgekerzen,
  fehlende Indikatoren, keine Setups und abgelehnte Pläne sind getrennt.
  Gefüllte, noch offene Einstiege sind von „Ausgewertete Trades“ getrennt.
  BI bleibt bei 17/20; Score-, Risiko- und Mailfreigaben werden nicht gelockert.
- [x] Sichere Abbrüche vor SMTP geben unversuchte Reservierungen frei.
  BG bereinigt nach 30 Minuten ausschließlich unberührte, nicht angenommene Reservierungen.
  Alter Sender kann keinen Ersatzowner löschen. ATTEMPTED, unklare DATA-Annahme und
  separat journalisierte SMTP-Annahme bleiben geschützt; Journalfehler stoppen Wiederholung.
  Ungültige Prepared-Zeitstempel werden nicht durch SQLite-Normalisierung gelöscht.
- [x] Unabhängige Cache-/Mail-Nachprüfung; 445 gezielte Mailtests, 151 Producer-Tests,
  89 Frontend-/API-Proben und 45 abschließende Cache-/API-Proben bestanden.
  Diese überlappenden Läufe werden nicht zu einer Gesamtzahl addiert.
- [x] Playwright: 18 Desktop-/Mobilfälle grün, kein externer Request, kein Schreibaufruf,
  keine Laufzeitfehler, kein horizontaler Überlauf. Bundle `67924e9f894a` verifiziert.
- [x] Angemeldete App rein lesend geprüft: gespeicherter BI-Lauf vom 03.10., 21:47:24,
  3 Monate / 200 Aktien / Preis 5 / Volumen 200.000 enthält 0 Pläne/Trades,
  aber keine gespeicherten Ablehnungsdiagnosen. Seine exakte Ursache bleibt unbekannt.
- [x] Live-Mailansicht: zuletzt 50 Entscheidungen vor SMTP ausgelassen, keine
  SMTP-Annahme und kein Versandfehler in diesem sichtbaren Fenster. Kein Nachweis eines
  allgemeinen Transportausfalls. Technische Testmail laut Nutzer angekommen.
  Sonntag: Aktien-Autoläufe pausieren; nächste erlaubte Öffnung 05.10., 04:00 UTC.
- [x] Abschließender eingefrorener Gesamtlauf: 11.274 bestanden, 0 Fehler,
  1 POSIX-Rechtetest unter Windows übersprungen; `tmp/qa-1b8536a9bad0/results.xml`.
  Erster Lauf: 11.264 bestanden, 1 alte Quelltextprüfung rot, 1 POSIX-Rechtetest unter Windows
  übersprungen. Quelltextprüfung korrigiert; danach 67 gezielte Tests grün.
  Ein Windows-Queue-Timeout im Mail-Nachlauf ist dokumentiert; ohne Änderungen
  bestanden anschließend der Atomic-Test (10/10) und der vollständige 445er-Lauf.
- [x] Reparaturpaket committet und gepusht: `f46413c2d357475968109aa79269a9c07e21ddd7`.
  Remotehash kontrolliert. Fremdes WIP und private Exporte nicht aufgenommen.
  Dieser Abschlussabschnitt wird separat als Dokumentationscommit veröffentlicht.
- [ ] Nach Betreiberpull BI-Dreimonatslauf neu berechnen und echte Diagnose auswerten.
- [ ] Neue echte Handelssignalannahme, persönlicher Empfang und Wochenkohorte nachweisen.
  Kein weiterer Export, Scanstart oder Testmail durch diese Bearbeitung.

Live-Health vom 04.10.2026, Serverzeit 20:58:06: healthy,
Revision `cabb5b17ee41`, Bundle `17410b75a42c`.
Server unverändert; keine Einstellungen, Zugangsdaten oder Produktionsdaten geändert.
Bestehendes Dokumentations-/Deploy-/Test-WIP bleibt erhalten.
Prüfbericht: [BI-Backtester und Mail-Recovery](docs/BI_BACKTEST_MAIL_RECOVERY_2026-10-04.md).

## Arbeitsregel – TODO nach jedem Arbeitsabschluss aktualisieren

Auf ausdrücklichen Nutzerwunsch vom 04.10.2026 diese Aufgabenliste am Ende
jeder Bearbeitung aktualisieren, auch bei Unterbrechung oder offenem Zugang.
Erledigt, nur geprüft, noch offen, committet/gepusht und auf dem Server
bestätigt getrennt festhalten; Tests, Nachweise und nächsten Schritt nennen.
Offene Aufgaben nicht allein aufgrund grüner Tests als erledigt markieren.

## 04.10.2026 – Historische Ausgangsprüfung der BI-Nullanzeige (oben ersetzt)

Prüfung vom 03.10.2026 zum Screenshot: BI Long, 3 Monate, 200 ausgewählte
Aktien, Mindestpreis 5 und Mindestvolumen 200.000; Anzeige 0 Signale/0 Trades.
Aktueller lokaler HEAD: `cabb5b17ee41dc93f40c0a43270ffe71e803b820`.

- [x] Zählerpfad nachgerechnet: `n_tickers` zählt ausgewählte Aktien, nicht
  erfolgreich ausgewertete Historien. `signals_found` steigt erst nach
  akzeptiertem Handelsplan; vorherige BI-Kandidaten/Planablehnungen fehlen.
- [x] Drei isolierte Gegenproben bestanden. 64 kontrolliert als qualifiziert
  eingespeiste Analysefenster wurden von der echten Planprüfung abgelehnt;
  Bericht trotzdem 0 Signale/0 No-Fill ohne Ablehnungsgründe. Dies beweist
  die fehlende Diagnose, nicht 64 echte BI-Signale im Markt oder die Ursache
  des konkreten gespeicherten Laufs.
  Private Belege: `output/test_bi_zero_readonly_probe_20261003_e76f.py` und
  `output/mail-fix-qa-cbd99b6b55c745d7aaa7761a675e2218/results.xml`.
- [x] Kein pauschaler Rücktest-/4H-Zwang im BI-Long-Modell: echter Plan aus
  abgeschlossenen Tageskerzen vor Ausbruch akzeptiert, Methode `stop_breakout`.
  Die Bezeichnung „BI-Retest-Engine“ erklärt diesen Long-Pfad nicht korrekt.
- [ ] Auswahl-/Datenabdeckung und Ablehnungstrichter implementieren:
  ausgewählte und tatsächlich auswertbare Aktien, gültige Analysefenster,
  erkannte 17/20-Setups, Planablehnungen mit Gründen, akzeptierte Pläne und Fills.
  Fehlende Daten vor Entstehung eines Trades ebenfalls erfassen; 0 Trades
  ist kein Nachweis vollständiger Daten. 17/20 und Risikoregeln nicht lockern.
- [ ] Ergebnistext zwischen keinen Setups, Datenlücke und Planablehnungen
  unterscheiden; kurze Hauptanzeige, Methoden-/Diagnosedetails aufklappbar.
  BI-Long-Methodenbezeichnung korrigieren und Plan-/Ausführungsherkunft erhalten.
- [ ] Tatsächlichen gespeicherten Dreimonatslauf anhand seiner Daten und
  neuen Diagnosen nachvollziehen. Aktuelle Ursachenbehauptung „Markt hatte
  nichts“ nicht belegt. Letzter direkter Ergebnis-GET lieferte 401; keine
  angemeldete interne Browsersitzung vorhanden. Kein weiterer Export oder
  neuer Provider-Backtest wurde gestartet.
- [ ] Echte neue Signal-Mail und Wochenkohorte bleiben separate offene
  Betriebsprüfungen; technische Testmail ist angekommen, kein neuer
  Handelssignal-Posteingang durch diese BI-Prüfung bestätigt.

Letzte historische Live-Health-Prüfung vom 03.10.2026 (Serverzeitstempel
19:36:34): healthy, Revision `cabb5b17ee41`, Bundle `17410b75a42c`.
Dieser Stand ersetzt ältere Serverstände unten, ist keine neue Liveprüfung
vom 04.10. Die letzte BI-Prüfung änderte keinen Produktcode; dieser Anschluss
ändert ausschließlich TODO-Dokumentation. Keine neue Reparatur, kein Commit,
Push, Serverupdate, Scanstart oder Mailversand. Bestehendes fremdes WIP erhalten.

## 03.10.2026 – Wochenreport: Versandkohorte und kausale Bewertung

Aktueller Auftrag: die irreführende Wochenbilanz (86 reife Einträge,
45 ausgewertet / 40 ungeklärt, −47,5R) und ihre Versandbehauptungen korrigieren.
Ausgangsrevision `9267b0d87e63a30a1231d8a7694efa5ba5312074`.

- [x] Wochenjob und lokale Vorschau verwenden ausschließlich aktivierte,
  kanonisch gebundene SMTP-Annahmen mit gültigen Empfänger- und Zeitbelegen.
  Alte direkte Tracker-Einträge bleiben erhalten, gelten aber nicht mehr als
  nachgewiesene Signalzustellung. Aktivitäten der letzten 7 Tage und das
  separate 30-Tage-Reifefenster werden nicht vermischt.
- [x] Die globale SMTP-Annahme wird ausdrücklich nicht als persönlicher
  Posteingang ausgegeben. Wochenreport/Crash-Mails bleiben eigene Info-Mails;
  ihr Eingang beweist keinen erfolgreichen Handelssignal-Ablauf.
- [x] Erste Einstiegs- und Stop-Berührung innerhalb derselben Tageskerze
  ohne belegten vorherigen Einstieg erzeugt keinen erfundenen Fill/Verlust.
  Historische mehrdeutige Fälle werden beim Lesen ausgeklammert, nicht in der
  Produktionsdatenbank umgeschrieben. Bloße Snapshot-/Pfad-Tags reichen nicht.
- [x] Echte bereits eingestiegene Positionen und belegte Gap-Verluste bleiben
  erhalten. Negative R-Werte werden nicht kosmetisch entfernt; die alte
  Gesamtsumme ist ohne qualifizierte Kohorte keine belastbare Gesamtbilanz.
- [x] Vollständiger, disjunkter Bestandsabgleich: ausgewertet, Evidenz offen,
  ohne Einstieg, noch offen, nicht auswertbar. Offene Kontroll-/BE-/Pfadbelege
  verhindern eine scheinbar endgültige Schlagzeile oder Scannerfreigabe.
  Lesefehler erzeugen keine scheinbar erfolgreiche Nullbilanz und überschreiben
  keine vorhandene Vorschau.
- [x] Unabhängige neue Kausalitäts-/Provenienz-Gegenfälle sowie echte
  Prepare → Attempt → Finalize → Evaluator-Lifecycles lokal geprüft.
  Akzeptanzbindungen überstehen gültige spätere Outcome-Änderungen;
  Doppelversand bleibt gesperrt. Keine Schwellen, Einstellungen oder Orders geändert.
- [x] Playwright-Skill: lokale synthetische Wochenmail auf Desktop1440 und
  Mobil390 geprüft; kein horizontaler Überlauf, keine externen Requests.
  Die Vorschau ist Formatnachweis, keine Rekonstruktion der echten 86 Einträge.
  Private Screenshots liegen unter `output/playwright/weekly-repair-*.png`.
- [x] Drei kalendarische Testfehler gegen unveränderten HEAD reproduziert.
  Nur Test-Clocks korrigiert; Admission-/Expiry-Assertions unverändert.
  142 gezielte Scheduler-Nachbarprüfungen bestanden; kein API-Produktionsfix
  hierfür erforderlich. 37 abschließende Preview-/Kohortenprüfungen bestanden
  (`tmp/qa-f44b35f2d948/results.xml`).
- [x] Eingefrorenen Gesamtlauf abgeschlossen: 11.180 bestanden,
  1 POSIX-Prüfung auf Windows übersprungen, 0 Fehler, 301,34 s.
  `tmp/qa-2a892a8eb305/results.xml`. Alle 11 Quell-/Testdateien entsprechen
  SHA-256-genau dem geprüften Snapshot. Retirierte Deploy-Umstellungstests und
  fremde Commerce-/Kalender-/Deploy-/Handbuch-WIP nicht im Reparaturpaket.
- [x] Nur dieses geprüfte Paket committet und auf `main` gepusht:
  `98da02ef104e0145e3e14c2b53d2a1f8e1440830`, durch `git ls-remote` bestätigt.
  Private Exporte/Browserartefakte und vorhandene fremde Änderungen nicht
  übernommen. Dieser abschließende Statusnachtrag ändert nur Dokumentation.
- [ ] Nach normalem Betreiberpull die echte Wochenkohorte nachrechnen und
  ein neues gültiges Handelssignal vom Scanner bis zur persönlichen Mail prüfen.
  Kein automatischer Replay historischer Einstiegsmails und keine weitere Testmail.

Letzte reine Livekontrolle dieses Auftrags: `/api/health` am03.10.2026
06:19:21 (Serverzeitstempel) healthy / `4ef6c9b28482`, Frontend `5f2a5271c188`.
Kein Serverupdate, Scanstart, SMTP-Versuch oder Produktionsdaten-Umschreiben
durch diesen Auftrag. Die temporär geöffnete App zeigte die Anmeldeseite;
SSH im Batch-Modus wies vorhandene Schlüssel ab. Beide nur lesenden Zugänge
stehen damit ohne Betreiberanmeldung nicht zur Verfügung.
Die technischen Fehler sind lokal prüfbar; neue echte Signalzustellung bleibt
eine getrennte, noch offene Betriebsprüfung.

## 02.10.2026 – Backtest zuerst tief auditiert, dann repariert

Aktueller Auftrag: Backtest Center vor dem Reload-Fix tief prüfen.
Ausgangsrevision `4ef6c9b284825686fef60913a2e932a8f9414202`.

- [x] Neue mathematische/kausale Gegenfälle vor Reparatur reproduziert;
  unabhängiger Originalvergleich 11 rot / 8 grüne Kontrollen.
- [x] Rohpräzision, Netto-P&L/R, Fills, boolesche/offene Preiswerte,
  historische Universumswahl und Datenqualitätsfortführung korrigiert.
- [x] Holdout-Leck, PF-Rundungsgrenze, Krypto-Warmup-/Jahrescap und
  irreführende Konto-/Null-Kennzahlen korrigiert; keine Signalgrenzen gelockert.
- [x] Exakte V2-Cacheidentität, atomare Erfolgsdateien und passives Reopen;
  Fehler überschreiben keinen gespeicherten Erfolg.
- [x] UI-Deadline/SingleFlight/Generation-/Unmountschutz, pure Formular-
  präferenzen, sichere Fehlertexte, Server-/UI-Capzähler und ganzzahliges
  Volumen. Parameterlose Dateien erhalten, nicht als aktuelle Studie ausgegeben.
  Verlassen der Ansicht beendet nicht den Serverworker.
- [x] Controller 30/30, Independent 19/19, gemeinsame Kernfälle205/205 grün.
  Lokale Playwright-Desktop-/Mobilansicht: Reopen/Parameter-Miss/422/Erfolg,
  keine Live-API/Mail/externen Requests, Mobilbreite390/390.
- [x] Quellen-/API-Nachtrag eingefroren: 247/247 grün,
  `tmp/qa-7495abeac921/results.xml`; finaler Browserlauf mit Bundle
  `17410b75a42c` bestanden (Desktop/Mobil, keine externen Requests).
- [x] Eingefrorenes Gesamtpaket offline geprüft: 11.030 bestanden,
  eine POSIX-Prüfung auf Windows übersprungen, keine Fehler.
  `tmp/qa-a8c5b525dfe2/results.xml`, 430,18 s; Deploy-Umstellungstests
  ausdrücklich nicht im Umfang. Erster Vollpaketlauf fand nur fünf alte
  gerundete R-Erwartungen; unabhängig hergeleitet, übrige Assertions erhalten.
- [x] Nur geprüften Backtest-Scope committet und auf `main` gepusht:
  `7bfeb167898f0452de3bb5bfd7714c6c9da941e2`, durch `git ls-remote`
  bestätigt. Bestehende Deploy-/Handbuch- und Commerce-/Kalender-WIP,
  private Exporte und Browserartefakte nicht übernommen.
  Dieser abschließende Statusnachtrag verändert nur Dokumentation.
- [ ] Nach Betreiberpull echten Backtest-Reopen kontrollieren;
  kein Serverupdate oder echter Provider-Backtest durch Codex.

Details: [Backtest-Prüfbericht](docs/BACKTEST_DEEP_AUDIT_2026-10-02.md).
Andere Mail-/Live-/Dreimonatsnachweise bleiben separat.

## 02.10.2026 – Erstaufruf ohne manuelles Neuladen

Zusätzlicher aktueller Befund: Die anfänglich leere Seite war nicht bloß ein
laufender Scan. Ergebnis-GET/Body konnten hängen; transiente Erstlesefehler
wurden nicht automatisch wiederholt, passive Aktualisierungen brachen Reads
ab. Zwei versteckte Referenz-Providerpfade und blockierendes Auth-I/O kamen
hinzu. Die unten dokumentierten Mail-/Cup-Grenzen bleiben separat bestehen.

- [x] Gemeinsamen Scannerfeed mit 20 s Request-/Bodydeadline, SingleFlight und
  automatischem transienten Retry versehen; Retry-After wird nicht umgangen.
- [x] Finale Ergebnisse bei Ladefehlern behalten; Teilstände nicht zu finalen
  Ergebnissen/Nullscans umdeuten; Scope-/Run-/Berechtigungsgrenzen erhalten.
- [x] Ergebnisse aus gespeicherten Instrumentdaten lesen, auch ohne Namen;
  keine Providerseiten/-Einzelabrufe durch den Standard-Ergebnis-GET.
  Fehlende Identitätsbeweise bleiben 503, nicht scheinbar gültige 0 Treffer.
- [x] Cachealter/Firmennamen erhalten und überschreibenden alten Displayread
  gegenüber neuer Worker-Publikation mit Lock/CAS abgesichert.
- [x] Auth-/Kontodatenreads aus dem ASGI-Eventloop verlagert, bestehende
  Auth-/Cookie-/Plan-/read_only-/Throttle-Regeln unverändert geprüft.
- [x] Playwright-Skill: tatsächliches lokales Bundle auf Desktop/Mobil
  geprüft. Verzögerter Erstread 503 → Retry → Treffer ohne Reload; spätere 503
  behalten alte Treffer. 0 Schreibanfragen/Provideraufrufe; eigene Prozesse zu.
- [x] Gezielte Offlineprüfungen bestanden: 390 bestehende Lifecycle/Authfälle
  plus 55 abschließende Kernfälle; keine externen Provider/SMTP/Secrets.
- [x] Eingefrorenen Index-Gesamtlauf abgeschlossen: 10.887 bestanden,
  1 POSIX-Dateirechteprüfung unter Windows übersprungen, 0 Fehler.
  `tmp/qa-b3fbe27b64fa/results.xml`; Deploy-Umstellungstests nicht im Umfang.
- [x] Nur das geprüfte Erstlade-Paket committet und auf `main` gepusht:
  `0b2ae89bd6b9308e66786ce8579d590476e273a1`, durch `git ls-remote` bestätigt.
  Statusnachweis hier und im Prüfbericht gespeichert; kein Serverupdate
  durch den Push.
- [ ] Nach normalem Betreiberpull echten ersten Seitenaufruf kontrollieren.
  API-Health am 02.10.2026 17:28:20 (Serverzeitstempel): healthy / `1c3fb68f51df`
  / Frontend `806260a08809`. Die Erstlade-Korrektur ist dort noch nicht
  installiert; kein Export, Scanstart oder Serverneustart durch Codex.

Details/Nachweise: [Erstlade-Prüfbericht](docs/INITIAL_RESULT_LOAD_REPAIR_2026-10-02.md).
Keine Änderung der Signalbedingungen, keine Behauptung neuer Mailzustellung.

## Neue Livekontrolle 02.10.2026 – nach dem Betreiberupdate

Dieser Abschnitt hat Vorrang vor dem älteren Hinweis „auf Hetzner noch nicht
aktiviert“. Öffentliches `/api/health` am02.10.10:53:47 UTC bestätigt
**healthy / `7f6981f8e4f8` / Frontend `6a488c9c8f1a`**. Das vorherige Paket
ist auf dem Server angekommen; kein erneuter Pull allein für dieses Paket.

- [x] Betreiber hat den Eingang der technischen Testmail erneut bestätigt.
  Keine weitere Testmail gesendet; Transport dieser Nachricht ist belegt,
  noch nicht die Zustellung eines Handelssignals.
- [x] Authentifizierte aktuelle UI geprüft, ohne Scans oder Einstellungen zu
  ändern: Momentum vollständig12.582/12.582,32 Kandidaten, maximal Trade-Score78
  bei Mailminimum80. Neuer Snapshot02.10.10:48:55 UTC. Nicht „Scan abgebrochen“.
- [x] Die Fehlermeldung der Aktienrunde ist dem Cup-Lauf zugeordnet:
 5.712/12.582, `scan_data_unavailable`, Phase `history`,507s. Der alte Cup-
  Ergebnisstand ist kein neuer abgeschlossener Nullscan. Innerer Abruffehler
  noch nicht vorhanden; gezielter vorhandener Probeauftrag unten.
- [x] Fehlender tatsächlicher Signal-Mailanschluss des Crypto-Long-Scanners
  im aktuellen Code bestätigt: erfolgreicher Wrapper speichert nur Ergebnisse;
  auch `_run_scan_safe` und der Combined-Merge dispatchen keine Long-Signale.
  Early-Mover-Sender hat einen anderen Producer/Owner und ersetzt diesen Pfad
  nicht. Das ist zusätzlich zu den aktuellen Ablehnungen ein echter Codefehler.
- [x] Eigenen Crypto-Long-Dispatcher anschließen: unveränderte Grade-/Score-/
  Struktur-/Funding-/Frischegrenzen, native Venue/Contract/Closed-Candle-Quelle,
  finale Quote/Pfadprüfung, korrekter Crypto-Kanal, Durable-Intent/Receipt und
  Doppelversandschutz. Kein zweiter Sender aus dem Combined-Cache.
- [x] Latenten Crypto-Strategy-Kanalfehler korrigieren und prüfen: tatsächlich
  Crypto-Kanal statt `stocks_swing`, auch bei beiden Watch-Seams. Bestehender
  manueller Crypto-Watch-only-Vertrag bleibt deaktiviert für Trade-Mails.
- [x] Rolling24h-Hoch als unbelegte Beobachtung behandeln, nicht als bestätigte
  Strukturidentität. Echter Scorer → echte VRVP/Health → finale Quote/Pfad →
  tatsächlicher Sender mit Mock-SMTP/temporärem Receipt getestet. Ohne echte
  Gegenbarriere weiterhin Watch; keine Score-/Risiko-Lockerung.
- [x] Native Long-Cachevorprüfung und Crypto-Empfängerzahl in Admin ergänzen;
  zehn Gründe konsistent in API/SQLite-Telemetrie/privatem Collector.
- [x] Pauschale Combined-Behauptung „kein Fehler, sondern Marktlage“ bei
 0Longs/Shorts entfernen; Quellenstatus/Zähler statt erfundener Erklärung.
  Tatsächliche JSX-Komponente für beide Richtungen mit Fehlerstatus geprüft;
  Crypto-Empfänger im Admin separat sichtbar. Bundle `806260a08809` gebaut und
  Sourcehash/Syntax geprüft.
- [x] Neue Korrektur unabhängig/offline geprüft: 96 unabhängige Tests grün;
  eingefrorener Index-Gesamtlauf **10.842 bestanden, 1 übersprungen, 0 Fehler**,
  479,69s; `tmp/qa-77f0423f384f/results.xml`. Externe Provider/SMTP gesperrt,
  keine lokalen Secrets. Deploy-Tests ausdrücklich ausgeschlossen.
- [x] Geprüfte Korrektur scoped committet und gepusht:
  **`d2c14b1329aeb4099fc454c4ac9d9a0b0d6c9b72`**, Remote `origin/main`
  unabhängig bestätigt. Genau12 Pfade, keine privaten Exporte/Deploy-WIP.
  Normalen Pull unten verwenden; Server nicht selbst verändert.
- [ ] Cup-Abruffehler über `python3 -I /home/tradingbot/app/scripts/probe_stock_attempt_errors.py`
  im vorhandenen Server-Terminal lesen. Kein Gesamtexport/Passwortloop nötig.
- [ ] Aktueller Crypto-Long-Wiederholungslauf und echte Handelssignalannahme/
  Posteingang bleiben offen. Letzter Admin-Stand nach Restart:0 angenommen,
  6 ausgelassen,0 Fehler,0 Queue am02.10.13:18:02MESZ; begrenztes Prozessfenster,
  keine Inboxhistorie. Automatischer Retry weiter aktiv, kein Start durch Codex.

Nach Abschluss laufender Scans und SMTP-Vorgänge im Server-Terminal:

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Health: `healthy`, neueste `origin/main`-Revision einschließlich des reinen
Dokumentationsnachtrags, Frontend `806260a08809`. Kein Deploy-Skript, keine
Cache-/DB-Löschung. Danach Strg+F5. Echte neue Signalzustellung bleibt offen.

## Historischer Abschlussstand des ersten Pakets 02.10.2026 – Tiefenaudit / Dreimonatsproben

Dieser Abschnitt dokumentiert den Abschluss vor der neuen Livekontrolle oben.
Die Abschnitte ab „01.10.“ bleiben historische Nachweise, keine neuen Pull-/
Versandaufträge. Ausgangs-HEAD ist `796f8211a5692bb6cc98fb13075827926d30351d`.
**Das heutige Reparaturpaket ist committet und gepusht:
`93428a4998b0027740bea1f9c9df188f6743663f`, Remote `origin/main` separat bestätigt.
Auf Hetzner noch nicht aktiviert.** Geerbtes Deploy-/Handbuch-WIP und private
Quellen bleiben erhalten und wurden nicht veröffentlicht.
Veröffentlichung durch „alles erledigen“ freigegeben. Unabhängig geprüftes
93-Dateien-Paket im Git-Index, ohne private Exporte/Secrets/Deploy-WIP.
Exakter Index-Gesamtlauf: **10.777 bestanden, 1 übersprungen, 0 Fehler**,
425,95s; `tmp/qa-2c7fae09a9bc/results.xml`. Ausgeliefert wird auch der isolierte
Testlauncher `scripts/run_offline_tests.py`; keine neue Paket-/Reminder-Migration.

### Erledigt und am aktuellen Quellstand geprüft

- [x] Tatsächliches Inventar statt alter Chat-Auditlisten: 14 öffentliche
  Aktienstrategien, 11 manuelle Krypto-Profile, dedizierte BI-/Bear-/ORB-/Turtle-/
  Volume-/Penny-/Biotech-/Krypto-Pfade, Kontext-/Quote-/Watch-/Positionsjobs.
  Futures/Forex/International sind nicht implementiert; reine Kontextjobs
  haben keine zusätzliche Handelstrefferquote.
- [x] Aktienquellen und gemeinsame Strukturketten vertieft geprüft: abgeschlossene
  Regular-Session-4H-Slots, Adjustierung/Antwortstatus/Duplikate/Verfügbarkeit,
  Rohpräzision bis zum gerichteten Ordertick, SMC/FVG/Orderblock-/Pool-Lifecycle,
  Bear-60-Sitzungsbaseline und native erste Gegenbarriere, Harmonic-Pflicht-
  verhältnisse und bestätigter D-Pivot, inverse ETF-Quellenvertrag.
- [x] Reale MSFT-Probe bestätigt und korrigiert: eine transitive Levelkette
  hatte einen riesigen Unterstützungscluster erzeugt. Bei gleichen historischen
  Quellen Stop443,94 statt370,16; Entry451,10/TP1452,53 unverändert, weiterhin
  WAIT wegen naher Gegenbarriere. Zonenmodelle jetzt v2, keine erfundenen Ziele.
- [x] Hidden-Legacy-Pfade: Dip Buy korrekt Long; RVOL ohne Kappung/Entscheidungs-
  rundung; Volume Void/Churn eigene Cache-/Status-/Producerpfade. Manuelle
  Registrierung für Status, Cache und Datenquelle zusammen unter `_scan_lock`.
  Insider ohne Form-4-Quelle und entfernte Wick/All-Harmonic-Aufrufe ausdrücklich
  501 vor Provider/Worker; keine neuen Mails/Reminder aus alten Ersatzzeilen.
- [x] BI-RVOL/Struktur-/Liquiditätsgrenzen roh korrigiert; BI-Vertrag
  **`stock-bi-20-v8`**, alle20 Faktoren erforderlich, **17/20 unverändert**.
  Biotech Full/Quick verwenden gemeinsame Newsnegation/Ergebnis-/Risikoregeln,
  keine alten positiven Katalysatoren bei frischer Nichtverfügbarkeit.
  Penny strikt datierte echte Daily-/5m-Quellen, keine Ersatzpreise/Futurelevels.
- [x] Krypto-BTC-Kontext/-Vergleichsfenster, echte Closed-ATH-/Triggerkerzen,
  Rohschwellen, Quotes/Book/Contractgröße, bekanntes Null gegenüber unbekannt,
  Listingepisode/Retry/Annahme von Lease getrennt. Kein erfundener MarketCap/OI/
  Funding- oder Neutralwert. Profilcache3, Listingcache/episode4.
- [x] Cup-Watch echter Versandfehler korrigiert: alter30-Tage-Cooldown war als
  aktuelle Mailannahme behandelt worden. Nur frische gültige SMTP-Annahme
  beendet die Watch. Generation strikt integer; alter Claim löscht keine neue
  Generation. Ablauf nach tatsächlicher Session/Frühschluss.
- [x] Frontend-Dauerloading korrigiert: Kalenderprognose ist keine Ergebnisrevision,
  langsame Reads bewahren vorhandene32 Zeilen, angezeigter Snapshot bestimmt
  „Ergebnisstand“ statt Owner-/fremder Laufzeit. Tatsächlich gebautes Bundle im
  lokalen Browser mit gesperrten POSTs/externem I/O geprüft, keine Konsolenfehler.
  Browsertab und temporärer Testserver anschließend geschlossen.
- [x] Abschließender eingefrorener Offline-Gesamtlauf: **10.773 bestanden,
  1 übersprungen, 0 Fehler**, 415,52s; `tmp/qa-b4e0cda07cd9/results.xml`.
  Der Skip betrifft POSIX-Dateirechte auf Windows.
  `test_deploy*.py` ausdrücklich ausgeschlossen, kein Linux-Installernachweis.
  Zwei reine Deploy-Entfernungs-/Installer-Guardtests separat bestanden:
  `tmp/qa-1ed7a76b2832/results.xml`. Fokusgruppen nicht addieren.
- [x] Unabhängiger finaler Cup/Hidden/Krypto-Lifecycle188/188 grün,
  `tmp/qa-8f79860cad66/results.xml`. Alte/future/bool/NaN/Inf/abgelaufene
  Annahmestempel, gleichzeitiges Watch-Upsert und Generationenwechsel geprüft.
- [x] Private historische Studien mit echten Quellen abgeschlossen und gehasht:
  **02.07.–01.10.2026**, 64 US-Sessions, je3 vorab fixierte Assets pro Datenfamilie.
  18 eindeutige Quelldateien; vollständige RTH-Slots und87.264 Spot-Kryptobars.
  Je Scanner erste3 chronologische Beobachtungen, nicht Gewinnercherrypicking.
- [x] Alle fünf finalen `history-*-verified.json` enthalten dieselben88 aktuellen
  Produktions-/Research-Quellfingerprints. Baseline vor Producerimport;
  Prüfung vor/nach Replay und unabhängig gegen tatsächlichen Checkout.
  Ältere `release`-/`final`-/Probe-Dateien bleiben historische Zwischenstände.
- [x] Historische Nenner ehrlich getrennt:100 technische Aktienkandidaten ohne
  Elliott,98 native Level,0 kandidatenseitig freigegebene Modellpläne;
  BI384 Prüfungen, maximal13/20,0 bei17/20. Quote ohne gefüllte Trades ist
  **nicht berechenbar**, nicht0%. 5-Session-/24h-Richtung ist keine Netto-PnL.
  Biotech/Penny/ORB/Bear/Krypto fehlende historische Vollscannerinputs ausdrücklich
  unbekannt; keine erdachten News-/Universe-/Quote-/Funding-/Listingzustände.
- [x] Alte tatsächliche Trackerhistory292 Zeilen separat bewertet; Export endet
  26.09., nicht01.10. Fehlende Herkunft/Ticker und alter Algorithmus verhindern
  eine Gewinnquote des heutigen Reparaturpakets.

[Aktueller Reparatur-/Prüfbericht](docs/SCANNER_DEEP_AUDIT_2026-10-02.md).
Private Übersicht: `output/scanner-deep-audit-20261002/HISTORICAL_SAMPLE_REPORT.md`.
Vollständige Code-/Test-/Quellenmatrix:
`output/scanner-deep-audit-20261002/SCANNER_COVERAGE_MATRIX.md`.
Private Ausgaben sind gitignored; nicht mit Source nach GitHub laden.

### Tatsächlich noch offen / nicht als erledigt übernehmen

1. [x] Scoped Veröffentlichung des heutigen Pakets: Freigabe, unabhängige
   Prüfung,93 konkrete Stage-Pfade, Index-Gesamtlauf, Commit/Push und separater
   Remotehashvergleich abgeschlossen. Private Quellen/Exporte ausgeschlossen.
   Kein safe_deploy, Ersatzinstaller oder Eigentumsumbau.
2. [ ] Nach Betreiberupdate vollständige neue Scannerläufe unter neuen Verträgen
   prüfen: Aktiencache20, BIv8, Kryptoprofil3, Listing4, Zonenmodelle v2.
   Alte Cachezeilen nicht als neue Resultate/Reminderanker umetikettieren.
3. [ ] Konkreten inneren Grund der Serverabbrüche vom01.10. für Momentum/Cup/
   Turtle weiter einholen. Vorhandener gezielter Lesetest
   `scripts/probe_stock_attempt_errors.py` ist fertig; aktuelle Antwort fehlt.
   Kein pauschaler weiterer Gesamtexport nötig. Heute kein direkter SSH-Zugang;
   lokale Präzisions-/Quellfixes beweisen nicht den damaligen Fehleruntercode.
4. [ ] Echte aktuelle **Handelssignalzustellung** separat bis Posteingang prüfen.
   Einmalige technische Testmail am01.10. angenommen UND Betreiberempfang
   bestätigt; Autorisierung verbraucht, nicht erneut senden. Aktuelles begrenztes
   Mailfenster02.10.11:54:18 MESZ:1 Swing-Empfänger,37 ausgelassen,0 angenommen,
   0 Versandfehler,0 Queue. Ablehnungen vorSMTP, kein allgemeiner Transportausfall
   belegt. Keine Freigabe fingieren, Kanäle/17/20/Grade/Risiko lockern.
5. [ ] Vollständige historische Netto-Trefferquoten dort erst mit fehlenden
   damaligen Daten berechnen: News/Earnings/BPIQ/Float/SEC, Marktbreite/VIX/ETFs,
   CG-Universum/MarketCap/Umsatz, Listingzeiten/Contracts/Books/Funding/OI,
   Ausführungs-/Positionszustände. Kleine feste Stichproben und Spot-Candlekerne
   ersetzen diese Inputs nicht. Aboerneuerung/zusätzliche Kosten nicht autorisiert.
6. [ ] Originalkerzen für acht alteVIAV-Strukturen undAST-Zeichnungen bleiben
   unvollständig. 8H nicht als unterstützten Chartzeitrahmen behaupten;
   TradingView-Profil nicht mit unserem OHLCV-Rangeprofil gleichsetzen.

### Nächste Betreiberaktion – kein erneuter Gesamtexport

Direkter SSH-Batchzugang hier mit `Permission denied` abgewiesen. Nach Abschluss
laufender Scanner und SMTP-Vorgänge im eigenen Server-Terminal ausführen:

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Danach Healthausgabe/Revision schicken und Browser hart neu laden. Erwartetes
Bundle `6a488c9c8f1a`; Quellcommit `93428a4` plus Abschlussdokumentation. Kein
Deploy-Skript, keine Paketinstallation, Reminder-Migration, Unitkopie, chown,
daemon-reload oder Cachelöschung. Stop/Pause ist kein Restart-Checkpoint.

### Quell-/Test-/Betriebscheckpoint

- API-SHA256 `1118c313be477386fade5e10fcd1ffd0123c95e44df4cd7abea022ade7fa095f`.
- Frontend Sourcehash `6a488c9c8f1a`; Bundle-/Syntaxprüfung bestanden.
- Python-Kompilierung und repository-normalisierte CRLF-/Whitespace-Prüfung grün.
- Quellenmanifest `412439cd67cd7cd95b7e54933b05bcd6d173e4c7126bd5f0898326b479ef0775`;
  `source-coverage-oct02-final.json` mit echten Dateihashes/64Sessions/Slotprüfung.
- Produktive rein lesende Healthkontrolle02.10.: healthy, Revision
  `796f8211a569`, Bundle `41f168c9f109`; Server seit dieser Arbeit unverändert.
- Interpreter `.codex_pytest_env\Scripts\python.exe`; isolierter Launcher
  `scripts/run_offline_tests.py`, QA-Ausgabe nur in `tmp`. Der frühere private
  Launcher bleibt historisch, nicht Voraussetzung für einen frischen Checkout.
- Keine echte Order, erneute Testmail, Scanstart, Account-/Abo-/Dienständerung
  oder Entfernung privater History während dieses Auftrags. Veröffentlichung
  des geprüften Quellpakets erfolgte nach ausdrücklicher Abschlussfreigabe.

## Historischer Stand 01.10.2026 – nicht mehr maßgeblicher Einstieg

## Maßgeblicher Accountwechsel-Stand 01.10.2026

Diese Zusammenfassung ist der aktuelle Einstieg. Die älteren Abschnitte darunter
sind ein historisches Anschlussprotokoll; deren Pullbefehle, Serverstände und
offene Rolloutkästchen nicht als heutige Arbeitsanweisung übernehmen.
Workspace: `C:\Projekt\TradingBot`, Branch `main`.

### Abgeschlossen und jetzt erneut geprüft

- [x] Level-/Trendlinien-/Volumenprofil-Reparatur committet und veröffentlicht:
  `796f8211a5692bb6cc98fb13075827926d30351d`. Lokales HEAD und GitHub `main`
  am 01.10. erneut geprüft und identisch. Vorgänger `0a3de85` und `77d0480`
  sind enthalten; keine offenen Codeänderungen dieses Reparaturpakets.
- [x] Gemeinsamer verfügbarer Chartdatenstand: offene/future Kerzen bestätigen
  keine Struktur; Starter-US-Aktien berücksichtigen 900 Sekunden Verzögerung
  vor dem Abruf. Live, Krypto und Nicht-US-Routen bleiben davon getrennt.
- [x] Kausale Trendlinien mit drei bestätigten Ankern, eingefrorener Geometrie
  und dauerhafter Entwertung nach Bruch. Horizontale Zonen und Projektionen
  bleiben getrennte Nachweise; keine erfundenen Entry-/Stop-/TP-Level.
- [x] Native und sichtbare Volumenprofile: gültige echte Volumenträger,
  Volumenerhaltung, Mikropreise und geschlossene Kerzen. Sichtbares Profil
  folgt Zoom und Chartpreisskala. Finale Stop-/Risiko-/Zielmetadaten stimmen
  mit dem ausgegebenen Plan überein; historische Nachweise bleiben separat.
- [x] Cache-Neuberechnung abgesichert: Aktienstrategien Version 18,
  BI `stock-bi-20-v6`, Krypto-Profilvertrag 1, New Listing Vertrag 3.
  17/20, Mailfreigaben und Risikogrenzen nicht gelockert.
- [x] Eingefrorener App-Gesamtlauf: **9.959 bestanden, 1 Windows-Skip,
  0 Fehler**, 514,69 Sekunden. Getesteter Code-/Test-Indexbaum
  `88806fc2c21a2bce66b4e16fe788f954eaac07ca`; Commit unterscheidet sich davon
  ausschließlich durch den neuen Prüfbericht. Bei der Übergabe erneut anhand
  von Git und JUnit geprüft, keine erneute lange Testsuite gestartet.
  `test_deploy*.py` war ausdrücklich ausgeschlossen; frühere Windows-Bash-
  Zeitüberschreitungen sind kein erfolgreich geprüfter Installerpfad.
- [x] Vier lokale Browserfälle (Sidebar/Analyse, 1440/390 Pixel) ohne Fehler;
  tatsächliches Bundle mit synthetischen Daten, externe Zugriffe gesperrt.
  Bundle-Quellhash `41f168c9f109`, Syntax-/Bundleprüfung bestanden.
- [x] **Serverupdate inzwischen bestätigt:** öffentliche API-Health-Abfrage
  am 01.10.2026 um 20:30 MESZ (Europe/Zurich) liefert `healthy`,
  Revision `796f8211a569`, Frontend `41f168c9f109`. Das Paket ist aktiv;
  hier kein Pull, Neustart, Scanstart oder Versand. Kein erneuter Pull nötig.
  Diese Health-Antwort ersetzt keine Einzelprüfung aller systemd-Dienste.

[Reparaturbericht](docs/LEVEL_TRENDLINE_VRVP_REPAIR_2026-10-01.md).
Der dortige Serverstand `0a3de85` beschreibt die frühere Abnahme vor dem Update;
maßgeblich für den jetzigen Serverstand ist die neue Health-Prüfung oben.

### Nächster Anschluss – in dieser Reihenfolge

1. [ ] Neue vollständige Strategie-/Gap-/BI-Läufe unter `796f8211a569` prüfen:
   Laufkennung, Datenzeit, korrekter neuer Cachevertrag, vollständiger Abschluss,
   konkrete Ausschlussgründe und finale Plangeometrie. Alte Caches nicht als
   neue Ergebnisse bewerten. Laufende Scanner nicht unnötig neu starten.
2. [ ] **Mailversand bleibt offen, nicht als repariert abgeschlossen melden.**
   Bei genau einem aktuellen gültigen Signal die vollständige Kette im selben
   Zeitfenster verfolgen: Scannerfreigabe, Empfänger-/Kanalzulassung,
   Unterdrückungsgrund, Outbox, SMTP-Annahme und tatsächlicher Posteingang.
   Eine Crash-/Infomail ist kein Beleg für Handelssignalzustellung; keine
   Schwellenlockerung oder fingierte Freigabe. Vorhandene private Exporte
   zuerst prüfen, keinen identischen Export ohne konkreten Bedarf verlangen.
3. [x] Einmalige persönliche technische Testmail ist vom Betreiber autorisiert.
   Implementierter Admin-Button stammt aus `0a3de85` und ist jetzt im Rollout
   enthalten. Am **01.10.2026, 22:41:53 MESZ** genau einmal über den Admin-Dialog
   gesendet: Antwort **„Vom Mailserver angenommen“**, danach auch im Versandprotokoll
   eine SMTP-Annahme sichtbar. Kein zweiter Versuch. Der Betreiber hat danach
   den tatsächlichen Postfachempfang bestätigt. Damit ist der technische
   Transport bis ins Postfach nachgewiesen, nicht die Handelssignal-Freigabe.
   Privater Bildnachweis: `output/mail-transport-proof-20261001.jpg`.
4. [ ] API/BG/Frontend als einzelne systemd-Dienste rein lesend prüfen, sofern
   für den konkreten Betriebsfehler erforderlich. Health allein beweist nicht
   jeden Hintergrundlauf. Kein Deploy-Skript, keine Eigentums-/Installationsmigration.
5. [ ] BPIQ/Biotech-Abo ist laut Betreiber abgelaufen: 401 als separate
   Anbieterberechtigung behandeln. Kein globales Mailproblem daraus ableiten;
   andere Scanner unabhängig prüfen. Abos/Schlüssel nicht eigenmächtig ändern.
6. [ ] Originalkerzen für die acht alten VIAV-Strukturen und die drei manuell
   gezeichneten AST-Linien fehlen weiterhin. Keine exakte Nachberechnung behaupten.
   8H ist kein implementierter Chartzeitrahmen; unterstützt sind 5m/15m/1H/4H/1D/1W.
   TradingView-Profil und unser OHLCV-Rangeprofil haben nicht dieselbe Datengrundlage.
7. [ ] Separates geerbtes Deploy-Entfernungs-/Handbuch-WIP bleibt uncommittet.
   Nicht mit dem abgeschlossenen Levelpaket vermischen oder ohne Nachprüfung
   veröffentlichen. Private `output/`-/`tmp/`-Artefakte nicht auf GitHub laden.

### Reproduzierbare Übergabe / lokale Arbeitskopie erhalten

- Funktionierender Interpreter: `.codex_pytest_env\Scripts\python.exe`.
  Isolierter Launcher: `tmp/offline_mail_fix_tests_20260925.py`.
  App-Tests nur mit separaten kurzen QA-Datenpfaden ausführen; keine echten
  Secrets, produktiven Datenbanken oder SMTP für Tests verwenden.
- Finales privates JUnit: `tmp/qa-a7f6faae0bad/results.xml`.
  Getesteter Indexexport: `tmp/levels-publish-8a2e757587d848898ac9f920c13d4a97/source/`.
  Browsernachweis: `output/playwright/levels-20261001/result.json` und Screenshots.
- Geerbte lokale Änderungen erhalten: TODO, Handbuch-/Übergabedateien,
  `COMMERCIAL_LAUNCH_CHECKLIST.md`, `docs/SCANNER_REAUDIT_REPAIR_2026-09-30.md`,
  Deploy-Anleitungen/Updater/Installer/Migration, gelöschtes `deploy/safe_deploy.sh`,
  Deploy-Tests sowie `test_calendar_and_crypto_safety.py` und restliche
  `test_commerce_hardening.py`-Änderungen. `test_deploy_retirement.py` ist untracked.
  Nicht pauschal stagen, zurücksetzen oder löschen.
- Diese Accountwechsel-Aktualisierung ändert ausschließlich `TODO.md` lokal.
  Kein neuer Commit/Push, Codeeingriff oder Server-/Kontoeingriff dafür.

### Direkte Mailprüfung am 01.10.2026, 22:36–22:48 MESZ

- [x] Angemeldete Produktivansicht **Admin → Mailversand** direkt geprüft,
  ohne neuen Export, Scanstart, Neustart oder Einstellungsänderung.
  Vor dem technischen Test: 1 Swing-Empfänger, 50 ausgelassene Entscheidungen,
  0 SMTP-Annahmen, 0 Versandfehler, 0 Warteschlange. Das ist das begrenzte
  Fenster der letzten maximal 50 Ereignisse seit API-Start, höchstens 24 Stunden,
  keine vollständige Versandhistorie.
- [x] Kontokonfiguration rein lesend geprüft; persönliche Kanal-, Mailmodus-
  und Watchlist-Einstellungen werden in dieser Dokumentation nicht veröffentlicht.
  Keine Kanal-/Modusänderung und kein Eingriff in das abgelaufene BPIQ-Abo.
- [x] Crash-Mails sind `info`/`bear`, Aktienstrategie-Mails
  `swing_trade`/`stocks_swing`; Crash-Zustellung beweist daher keinen
  bestandenen Handelsplan. Die aktuellen Ablehnungen erfolgen vor SMTP.
- [x] Gegenprüfung des bestehenden Gap-Vertrags: bestätigte Long-/Short-
  Schlusskursausbrüche **ohne Rücktest** können den echten Produzenten,
  Planprüfer, finalen Revalidator und Sender bis zum Journal durchlaufen.
  **8 gezielte Tests bestanden**, externe Zugriffe und SMTP ersetzt/gesperrt.
  Privates JUnit: `output/mail-fix-qa-a5830c6ecd39480d92b06fec87319571/results.xml`.
- [x] Alle 22 vorhandenen Serverexporte sind historisch; neuester vom
  26.09.2026, Revision `75dad91de2d5`, also vor `796f821`. Kein weiterer
  identischer Export angefordert. Die aktuelle Prüfung erfolgt direkt in der App.
- [x] Reproduzierter Anzeige-/Frischefehler lokal behoben: Gap nutzte bei der
  Ergebnisdiagnose die 60-Minuten-Strategierunde und deren Zwei-Stunden-
  Altersgrenze statt seiner vereinbarten 02:00/12:00-Zeitfenster und aktuellen
  abgeschlossenen 1D-Sitzung. Neue Prüfung berücksichtigt den nachgewiesenen
  abgeschlossenen US-Handelstag einschließlich 15-Minuten-Verfügbarkeit,
  Feiertagen und Frühschluss. Auch leere Ergebnisse benötigen diesen Beleg.
  Veraltete Sitzungen bleiben gesperrt; expliziter Live-Modus bleibt getrennt.
  Keine Änderung am Zeitplan oder an tatsächlichen Mailfreigaben.
- [ ] Echte aktuelle Handelssignalzustellung weiterhin nachweisen; der
  technische Test hat den SMTP-Transport bestätigt, nicht die einzelnen
  Berechnungen hinter allen Live-Ablehnungen. Vorhandene Gap-Caches gegen
  22:40 MESZ waren ca. zehn Stunden alt und nach dem neuen US-Tagesabschluss
  fachlich nicht mehr aktuell. Nächster Gap-Termin laut Scheduler: 02:00 MESZ.

### Laufende Reparatur nach dem bestätigten Testmail-Empfang

- [x] Echter Empfang der einmaligen technischen Testmail vom Betreiber bestätigt.
  Kein zweiter Testversand. Aktuelle SMTP-/Postfachverbindung funktioniert;
  ein genereller Versanddefekt erklärt die fehlenden Handelssignale nicht.
- [x] Weitere Grenzwertfehler lokal reproduziert und korrigiert: ATR-Erweiterung,
  Wick-Anteil, Tageshoch gegenüber TP1, Tagesbewegung, ATR-Mindestbudget und
  Schlusskurslage wurden vor Entscheidungsprüfungen gerundet. Das erzeugte
  sowohl falsche Ablehnungen als auch falsche Freigaben. Entscheidungswerte
  bleiben nun ungerundet; Schwellen und Tickgeometrie der Orderlevel unverändert.
  Cacheversion lokal **19**; Produktionsrevision `796f821` verwendet noch 18.
- [x] Kombinierte Offline-Nachprüfung des eingefrorenen lokalen Gap-/Präzisions-
  Pakets: **558 bestanden, 1 Windows-Skip**, 23,30 Sekunden; private Ergebnisse
  `tmp/qa-a10af1d3653d/results.xml`. Zusätzlich unabhängige Gegenprüfung der
  75 neuen Gap-/Präzisionsfälle und drei Kalendergrenzfälle bestanden.
  Zwischenbundle `0f72f762011d` aufgebaut, Quellzuordnung und Syntax geprüft;
  letzter Stand nach der kompakten Warntextkorrektur: **`c09114e47194`**.
- [ ] **Neu belegter Produktionsabbruch** am 01.10., 20:48–20:49 UTC:
  Momentum stoppt bei 3.379/12.582, Cup bei 1.171/12.582 mit `scan_data_invalid`;
  Turtle scheitert am Vergleich von Sammelfeed und Einzelhistorie mit
  `scan_data_incomplete`. Das sind keine abgeschlossenen Nulltrefferläufe.
  Der äußere Stacktrace verdeckt den inneren Grund. Vorhandene feste Untercodes
  und Ausschlusszähler gezielt lesen; nicht aus Laufzeit oder Abrufzahl erraten.
- [x] Turtle-Feldnamen-/Sessionwechsel-Vermutung kontrolliert: kein Aliasfehler
  nachgewiesen; Adapter liefert sowohl kanonische als auch kurze OHLCV-Namen.
  Preis-/Volumenabweichungen können den Abbruch reproduzieren, sind aber noch
  nicht als tatsächlicher Produktionsgrund belegt. Keine Toleranzen gelockert.
- [x] Separat reproduzierter Turtle-Quellenvertragsfehler lokal korrigiert: ausdrücklich nicht
  splitbereinigte Einzelhistorie wurde für einen splitbereinigten Plan akzeptiert.
  Anfrage nun ausdrücklich splitbereinigt, widersprüchliche vorhandene
  Antwortkennzeichnung vor jeder Berechnung abgewiesen. Fehlende alte Kennzeichnung
  bleibt bei ausdrücklicher Anfrage kompatibel. **38 gezielte Tests bestanden**;
  diese erklären den
  heutigen Datenabbruch noch nicht. [Anbieter-Datenvertrag](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
- [x] Gezielter Lesetest `scripts/probe_stock_attempt_errors.py` und 45 Regressionen
  fertig. Er liest nur zwei feste Attempt-Dateien im `/tmp`-Namespace des API-
  Prozesses; prüft PID/Startzeit, Dateityp, Größe, unveränderten Inhalt und JSON-
  Vertrag. Keine App-Imports, Umgebungs-/Zugangsdaten oder Kurs-/Empfängerzeilen.
  Turtle wird ehrlich als nicht dauerhaft gespeicherte Diagnose gemeldet.
  Unabhängige erste Abnahme: 54/54 Probe-/Turtlefälle bestanden.
- [x] Abschließender kombinierter Offline-Lauf nach Turtle-, Probe- und UI-
  Nachkorrekturen: **905 bestanden, 1 Windows-Skip**, 32,49 Sekunden,
  0 Fehler; bestehende anyio-Importwarnung. Privates JUnit:
  `tmp/qa-f18387473152/results.xml`. Erstlauf enthielt einen Windows-
  Metadatenrennen-Test und eine alte UI-Text-Erwartung; Parserprüfung sauber
  vom Dateirennen getrennt und kompakte Warnung im tatsächlichen UI-Ablauf geprüft.
  Keine Linux-Dateiprüfung oder Handelsgrenze gelockert.
- [ ] Antwort auf den gezielten aktuellen Untercode-Check auswerten:
  `Get-Content -Raw "C:\Projekt\TradingBot\scripts\probe_stock_attempt_errors.py" | ssh -T -o StrictHostKeyChecking=yes root@178.104.69.209 "/usr/bin/python3 -I -"`.
  Konkrete Karte entscheidet zwischen Historienformatfehler, Referenzmismatch
  und Ausschlusslimit. Direkter SSH-Zugang abgewiesen; direkte API-Navigation
  im Browser blockiert. Keine Zugangsdaten aus der Sitzung extrahiert.
  Nicht blind Ausschlusslimits, Kohärenzprüfungen oder Freigaben entfernen.
- [ ] Aktuelles Paket noch **nicht committet/gepusht oder auf Hetzner aktiviert**.
  Geerbtes Deploy-/Handbuch-WIP und private Exporte/Bildbeweise bleiben getrennt.
  Kein Deploy-Skript, keine Installationsmigration, kein Serverneustart in dieser Prüfung.

## Historisches Anschlussprotokoll – frühere Stände, kein neuer Pullauftrag

## Anschluss 30.09., technische Testmail und native Plan-Nachprüfung

- [x] Drei Planfehler aus neuen Long-/Short-Kerzenfolgen nachgestellt:
  TP2 verliert unabhängige Zone/Quellfamilie; TP1 verliert Validierung;
  VRVP zieht Stop in die ursprüngliche Invalidierungszone. Lokal korrigiert.
- [x] 16 neue kausale Plan-/Cachetests bestanden. Engere Gegenbarrieren,
  Projektionsstatus, Zukunftskerzen und neu berechnetes Risiko/R:R geprüft.
  Aktienstrategie-Cachevertrag jetzt 17; Version 16 verlangt einen neuen Lauf.
- [x] Sendergegenprüfung: echte Gap-Pläne Long/Short erreichen mit simuliertem
  SMTP den Tracker; BPIQ-401 und Watch-AUS sind keine globale Swing-Mail-Sperre.
  Im isolierten Transportpfad kein neuer Mailablehnungsfehler nachgewiesen.
- [x] Technische Testmail vom Betreiber ausdrücklich freigegeben. Admin-Button
  mit Inline-Bestätigung, eigene Adminadresse statt Verteiler, Klasse `info`,
  kein Handelssignal/keine Order. Keine automatische Wiederholung oder spätere
  Warteschlangen-Zustellung dieser persönlichen technischen Prüfung.
- [x] 11 neue UI-Handler-/Cookie-/Doppelklick-/Abbruchtests bestanden;
  Bundle `baffb67797ba`. Neue Testmail löst keinen Scanner aus.
- [x] 21 neue Backend-/Transporttests und eingefrorener Gesamtlauf bestanden:
  9.809 bestanden, 5 Plattform-Skips, keine Fehler in 1.191,42 Sekunden.
  SHA256 der acht Code-/Testdateien unverändert. Bestehende anyio-Importwarnung.
- [x] Scoped Commit/Push der neun zugehörigen Dateien:
  `0a3de85639039c739baf014a6d002301bb9d3744` auf `main`, HEAD und `origin/main`
  identisch. Bestehende Deploy-Änderungen und private QA-/Exportdateien nicht
  eingeschlossen. Der Commit enthält auch den zuvor veröffentlichten BI-
  Fortschrittsfix `77d0480` als Vorgänger.
- [ ] Betreiber-Pull und neuer vollständiger Strategielauf. Kein Serverupdate
  durch diesen Anschluss, keine Installationsumstellung/Deploy-Skript.
  Erwartet: Revision `0a3de8563903`, Frontend `baffb67797ba`. Nach Abschluss
  laufender Scans normal pullen, API/BG neu starten, Health prüfen, Strg+F5.
- [ ] Genau eine freigegebene technische Testmail auf Hetzner senden und
  SMTP-Ergebnis sowie Empfang prüfen. Sie wurde hier noch NICHT gesendet.
  Direkte SSH-Authentifizierung fehlt; vorhandener Browser besitzt den neuen
  Button erst nach Pull. Kein weiterer privater Export angefordert.
- [ ] Gültige reale Signal-Mail danach getrennt kontrollieren. Live-Ansicht
  19:44 UTC: 31 ausgelassen, 0 SMTP-Annahmen, 0 Versandfehler, 0 Warteschlange
  im begrenzten Fenster seit API-Start. Die neuen Planfälle beweisen nicht die
  alleinige Ursache sämtlicher ausbleibender Mails. Keine Grenzen gelockert.

[Aktueller Bericht](docs/NATIVE_PLAN_MAIL_REAUDIT_2026-09-30.md).

## Anschluss 30.09. nach Accountwechsel

- [x] `d06b8af58f35` am 30.09. um 17:49 UTC live auf Hetzner bestätigt:
  gesund, Bundle `2ebd16934df3`; lokales HEAD und `origin/main` identisch.
  Die beiden vorigen Pakete sind bereits eingespielt; dafür kein weiterer Pull.
- [x] BI-Fortschritt reproduziert und lokal an tatsächliche Lauf-/Worker-ID
  gebunden, auch ohne ersten Treffer. API und UI prüfen dieselbe Zuordnung;
  Altläufe, Richtungsverwechslungen und ungültige Zähler bleiben ausgeschlossen.
  Biotech nutzt denselben Fortschrittsvertrag und atomare Veröffentlichung.
- [x] Widersprüchlichen zweiten BI-Text „Scan läuft“ bei Pause entfernt.
  Desktop, pausierter Lauf, fremder Short-Lauf und Mobilansicht geprüft;
  205 gezielte Tests bestanden. Eingefrorener Gesamtlauf: 9.761 bestanden,
  5 Plattform-Skips, keine Fehler in 1.439,70 Sekunden. SHA256 aller sieben
  geänderten Code-/Testdateien unverändert; Bundle `15fd2ea4752d`.
- [x] Fortschrittskorrektur als `77d048077321` committet und auf `origin/main`
  bestätigt. Ausschließlich neun zugehörige Code-/Test-/Berichtsdateien;
  keine privaten Exporte oder bestehenden Deploy-Änderungen enthalten.
- [ ] Betreiber-Pull von `77d0480` nach laufenden Scans; diese Revision ist
  noch nicht auf Hetzner bestätigt. Kein Deploy-Skript, keine Umstellung.
- [ ] Neue vollständige Biotech-Auswertung und tatsächliche Signalzustellung
  bleiben offen. Mailansicht 18:14 UTC: 8 ausgelassen, 0 SMTP-Annahmen,
  0 Versandfehler, Warteschlange leer im begrenzten Laufzeitfenster.
  Biotech-Vorprüfung noch 12 Altkandidaten mit altem News-Vertrag.
  Scheduler 18:21 UTC: Strategierunde abgeschlossen, BI Long läuft,
  leichte Überwachungsprüfungen laufen parallel; Biotech weiterhin Altdaten.
- [x] Ursache des BPIQ-401 am 30.09. vom Betreiber erklärt: Biotech-Abo beim
  Anbieter abgelaufen. Kein globaler Mail-Schalter; andere Scanner und
  Alpha-Station-Empfängerberechtigungen bleiben davon unabhängig. Keine
  Zugangsdaten, Abos oder Mailpräferenzen geändert.
- [x] Reale Mail-/Kanalprüfung 30.09. ausschließlich lesend; persönliche
  Präferenzen nicht veröffentlicht. Schlusskontrolle 18:46 UTC: 16 ausgelassen,
  0 SMTP-Annahmen, 0 Versandfehler, leere Warteschlange; begrenztes Fenster
  seit API-Start, kein historischer Gesamt- oder Postfachnachweis.
  Konkrete Momentum-Beispiele: ABCL Score 87, Tagesqualität 73/96 statt 78
  und kein bestätigtes Strukturziel; FPI Tagesqualität 89/96, aber fehlende
  Planwerte und Score 45; IDT Score 69, Tagesqualität 66/96. BI-Caches leer.
  Somit Ablehnung vor SMTP, nicht durch das Biotech-Abo.
- [ ] Echten Transport/Empfang gesondert prüfen, sobald ein gültiges Signal
  entsteht oder der Betreiber eine technische Testmail ausdrücklich anfordert.
  Kein erzwungenes Handelssignal, keine Schwellenlockerung, keine Testmail
  oder Signalwiederholung in dieser Prüfung. 175 gezielte Offline-Tests
  bestanden (110 Momentum, 65 Provider/Scheduler/Empfängerrouting).
- [ ] Bestehende lokale Entfernung des Deploy-Skripts samt abhängigen
  Schutzprüfungen bleibt als getrennte, uncommittete Arbeit erhalten.

[Prüfbericht](docs/BI_PROGRESS_RUN_BINDING_2026-09-30.md).

Normaler Betreiberbefehl (erst nach Abschluss laufender Scans):

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Erwartet: `revision` **`77d048077321`**, `frontend_bundle` **`15fd2ea4752d`**.
Danach App mit Strg+F5 laden. Kein Neustart oder Scanstart durch diesen Anschluss.

## Anschluss 30.09., Abend: reale Mailprüfung und präzise Ablehnungsgründe

- [x] Hetzner live gesund auf `716f2ebd1e0c` bestätigt (16:28 UTC).
  Vorherige Biotech-/Schedulerkorrektur ist damit vom Betreiber eingespielt.
- [x] Aktien-Strategierunde abgeschlossen, BI Long danach laufend zusammen
  mit leichten Prüfungen beobachtet; kein eigener Scanstart/Neustart.
- [x] Langsame Admin-Maildiagnose auf anfragegebundene Referenzwiederverwendung
  umgestellt; keine zusätzliche Cache-Wiederverwendung im Sender.
- [x] Crash-Hinweise und Handelssignale in der Vorprüfung getrennt, bekannte
  SMTP-Fehlercodes verständlich zugeordnet; UTC und unbekannter nächster Lauf
  eindeutig beschriftet.
- [x] Falsches „R:R unter Mindestwert“ bei bloßen Projektionszielen reproduziert
  und behoben. Struktur, Ausbruchsbestätigung, Zielaufteilung und numerisches
  R:R getrennt; Freigaberegeln unverändert. Telemetrie/Export mitgezogen.
- [x] 400 fokussierte Gegenproben, danach 370 Diagnose-/Exportprüfungen grün;
  Desktop/Mobil geprüft. Eingefrorener Gesamtlauf: 9.704 bestanden, 5 Skips
  in 880,59 Sekunden. SHA256 der 13 geänderten Code-/Testdateien unverändert.
  Zusätzlich 2.880 Zulassungsentscheidungen gegen die bisherige Version
  verglichen: identisch. Bundle `2ebd16934df3`.
- [x] Diagnose-Patch nach Gesamtabnahme als `d06b8af` committet und nach
  `origin/main` gepusht. Nur 15 zugehörige Code-/Test-/Berichtsdateien;
  keine privaten Exporte oder bestehenden Installationsänderungen enthalten.
- [x] Hetzner auf `d06b8af` aktualisiert: beim Accountwechsel am 30.09.
  um 17:49 UTC live gesund bestätigt. Der Betreiber hat das Update eingespielt;
  kein Serverneustart durch diesen Anschluss. Der lokale TODO samt früherer
  Handbuch-/Deploy-WIP bleibt ausdrücklich uncommittet.
- [ ] Neue vollständige Biotech-Auswertung sowie SMTP-Annahme eines tatsächlich
  freigegebenen Signals und Empfang prüfen. Schlusskontrolle 17:25 UTC:
  14 übersprungene
  Versandentscheidungen, 0 SMTP-Annahmen, 0 Versandfehler, 0 Warteschlange im
  begrenzten Laufzeitfenster. Kein Beweis über sämtliche historischen Mails.
- [ ] BPIQ-Zugriff bleibt ein separates Anbieter-Autorisierungsproblem (401),
  kein SMTP-Problem; keine Zugangsdaten geändert.
- [x] Separate BI-Fortschrittslücke lokal behoben, Serverabnahme noch offen.
  Ursprünglicher Befund: Live läuft BI Long, Anzeige bleibt
  „Fortschritt noch nicht bestaetigt“. Im Code sind BI-Zähler vorhanden,
  aber `get_scan_status` gibt deren Zeit-/Laufbindung nicht weiter;
  `scannerSelectedProgress` ignoriert solche ungebundenen Zähler korrekt.
  Ein partieller Ergebnis-Cache entsteht erst beim ersten Treffer. Dadurch
  blieb ein arbeitender Nulltrefferlauf unsichtbar. Im nächsten Anschluss
  als `77d0480` repariert: Fortschrittsdaten bereits beim Schreiben sicher
  an Lauf/Owner binden und bis zur UI durchreichen; keine alten Zähler übernehmen.
  Dieser Fund wurde nicht in den eingefrorenen Mail-Abnahmelauf hineineditiert.

[Details](docs/MAIL_DIAGNOSTIC_CAUSES_2026-09-30.md).

Stand: **30.09.2026**, Reparatur der neun erneuten Scannerbefunde.
Workspace: `C:\Projekt\TradingBot`, Branch `main`.
Aktueller Anschlussauftrag: R1–R9 aus der Nachprüfung von `b6be1f2` beheben.
Implementiert: ORB-Finalquote/Datenausfall, Biotech-Negation/Publikationszeit,
Turtle-Sitzungskohärenz, abgeschlossene Chartkerzen, kausale FVG-/OB-Schwellen,
FVG-Entwertung und Penny-OHLCV. Zusätzlich ORB-Zielalias am Tracker repariert.
Lokale Gesamtabnahme abgeschlossen: **9.611 bestanden, 0 Fehler,
5 Plattform-Skips**, darunter 224 neue gezielte Gegenproben. SHA256 aller
392 Python-Dateien während des Schlusslaufs unverändert. Paket für
Commit/Push abgenommen; Veröffentlichung am Git-Verlauf prüfen.
Server unverändert; sicherer Rollout und reale Zustellung bleiben offen.
[Aktueller Reparaturbericht](docs/SCANNER_REAUDIT_REPAIR_2026-09-30.md).

### Aktuelle Abnahme / Rolloutgrenze

- [x] Alle neun dokumentierten Ursachen implementiert und Gegenproben in
  reguläre Tests übernommen; 17/20 unverändert, BI-Vertrag jetzt v5.
- [x] Zusätzlichen ORB-Fehler `target1/target2` am Zustellungsintent behoben;
  Long/Short bis zum Tracker mit simuliertem SMTP geprüft.
- [x] Biotech-Altdaten getrennt von `biotech-news-v2`; keine erneute Freigabe
  nur durch Cache-Schreibzeit. Hintergrund-Entry-Sender unverändert gesperrt.
- [x] Abschließende Gesamtsuite und Diff-/Syntax-/Bundleabnahme dokumentiert:
  9.611 bestanden, 5 Plattform-Skips; Bundle `4379c5dca540` unverändert.
- [x] Reparaturpaket für Commit/Push abgenommen; keine privaten `output/`-
  Dateien. Veröffentlichte Revision anhand Git/Remote prüfen.
- [x] Server-Rechteinventur am 30.09. vom Nutzer erhalten: Home root:root
  0755; App und `.git` tradingbot:tradingbot 0755; `venv` 0775;
  `data_cache` 0750. API/BG aktiv als tradingbot, weiterhin BindPaths
  `data_cache/runtime:/tmp:rbind`, kein StateDirectory; Frontend aktiv als
  root. API hat nur `legacy-direct-frontend.conf`, BG/Frontend keine Drop-ins.
- [x] Betreiberentscheidung: `safe_deploy.sh` lokal entfernt, keinen Ersatz-
  Installer und keine Installationsumstellung einrichten. Abhängige Auto-Update-
  und Migrationsaufrufe stoppen ohne das Skript vor Änderungen. Anleitungen und
  Tests sind angepasst; bestehende Scanner-/Mailprüfungen bleiben erhalten.
- [x] Gezielte Abhängigkeitsprüfung: zunächst 255 bestanden, 4 Plattform-Skips,
  2 Fehler durch fehlende temporäre Lock-Pfade in umgezogenen Tests. Beide
  Test-Fixtures korrigiert; vollständiger Nachlauf der 12 Entfernungs- und
  umgezogenen Trust-Tests bestanden. Alle vier abhängigen Shell-Dateien
  mit `bash -n` und den Diff mit `git diff --check` geprüft. Kein neuer
  Gesamtlauf der Scanner-Suite und kein Server-/SMTP-Eingriff.
- [ ] Entfernung ist noch nicht veröffentlicht oder auf Hetzner angewendet.
  Der letzte Deploy-Versuch brach vor Pull/Neustart ab; API/BG/Frontend waren
  laut Nutzerinventur aktiv. Manuellen Rollout passend zum vorhandenen Aufbau
  gesondert prüfen; Produktionsdaten, Reminder und Secrets erhalten.
- [ ] Erst nach sicherem Rollout neue vollständige Scans und reale
  Mailzustellung prüfen. Lokale Transporttests sind keine Posteingangsbelege.

## Vorherige Gap-/BI-Reparatur (Basis `b6be1f2`)

Vorheriger Anschlussauftrag: alle sieben Gap-/BI-Auditbefunde und verwandte
Rechenfehler beheben. Lokale Abnahme abgeschlossen: **9.385 Tests bestanden,
5 Plattform-Skips**, davon 80 neue Regressionstests. Desktop/Mobil, Syntax und
Bundle geprueft. [Reparaturbericht](docs/GAP_BI_CALCULATION_REPAIR_2026-09-30.md).
BI-Regelversion **v4**, Aktienstrategie-Cache **16**; 17/20 unveraendert.

## Aktuelle Gap-/BI-Reparatur

- [x] ADX-Initialisierung, ungerundete ADX-/RSI-/Stochastic-Entscheidung,
  Null-ADX und korrekt zusammengesetzte Zwei-Tages-Rendite.
- [x] Gleiche Sitzung/OHLCV-Basis fuer Gap und gemeinsamen 1D-Strategiepfad;
  Einzelausschluss, Fehlercluster und Erhaltung alter Final-Caches getestet.
- [x] BI nur aus referenzgeprueftem CS-Universum; bestaetigte Anteilsklassen
  nicht per Punkt/Suffix ausschliessen. Veraltete Historien getrennt zaehlen.
- [x] Bekannte Planwarnungen nach 17/20 sichtbar erhalten; unabhaengige
  Mail-/Tracking-Sperre und kein Verdraengen gueltiger Plaene durch Warnkandidaten.
- [x] Gegenproben in regulaere Tests uebernommen; feste Diagnosegruende
  und Export angepasst. Daten-/unbekannte Fehler bleiben ausgeschlossen.
- [x] Abschliessenden eingefrorenen Gesamtlauf dokumentiert: 9.385 bestanden,
  0 Fehler, 5 Plattform-Skips. SHA256 aller 22 Python-Dateien unveraendert.
- [x] Reparaturpaket fuer Commit/Push abgenommen; keine privaten Exporte
  oder Browserartefakte Bestandteil des Pakets. Veroeffentlichung siehe Git-Verlauf.
- [ ] Rollout, neue vollstaendige Gap-/BI-Laeufe und reale Mailzustellung pruefen.

## Vorheriges Gap-/Mail-Paket

Vorgaengerauftrag: Gap Momentum Long/Short **02:00 und 12:00
Europe/Zurich, Montag–Freitag**, mathematischer Audit und Abschluss der
Maildiagnose. [Neuer Prüfbericht](docs/GAP_MOMENTUM_SCHEDULE_AUDIT_2026-09-30.md).
Dieses Vorgaengerpaket (`f16a1cc`) ist lokal umgesetzt und unabhängig nachgeprüft:
**9.304 Tests bestanden, 5 Plattform-Skips**. Zur Veröffentlichung freigegeben;
der Produktions-Rollout ist noch nicht erledigt.
Die folgende Live-Revision ist der vorherige Serverstand, nicht der neue Zeitplan.
Live bestätigte Code-Revision: **`b742bbeb3a40`**,
Frontend **`00c5288ac5f0`**. Öffentliche Health-Antwort vom **29.09.2026,
21:52:35** (Serverzeit): `healthy`, Revision und Bundle stimmen überein.
Damit ist auch die Scan-/Chart-Textkorrektur auf Hetzner aktiv; Details im
[Nachprüfbericht](docs/SCAN_PRICE_BASIS_FOLLOWUP_2026-09-29.md).
Für das neue Gap-/Mail-Paket ist nach erfolgreicher Veröffentlichung ein Pull
mit API-/BG-Neustart erforderlich; laufende Scans vorher beenden lassen.
Der alte Health-Nachweis belegt nicht den Zustand aller Dienste, neue vollständige
Scans oder Mailzustellung.

## Neuer Gap-/Mail-Auftrag vom 30.09.2026

- [x] Gap Long/Short auf feste Werktagsslots 02:00/12:00 Zürich umgestellt;
  dauerhafte Zulassung, Sommer-/Winterzeit, Wochenenden, Pause/Fortsetzen und
  konkurrierende Aktienworker geprüft. Keine zusätzlichen Startup-/Stundenläufe.
- [x] Gap-Mathematik und fachliche Auswahl korrigiert: fremder 79-Punkte-Deckel,
  ungerundete RVOL-Grenze, explizite Schuldpapiere. Cacheversion 15.
- [x] Maildiagnose einschließlich Auth-Middleware, Outbox, Tracker und Dedupe
  rein lesend. Unbekannte Zustellungsdaten bleiben unbekannt, nicht scheinbar null.
- [x] Kompakter Bereich Admin → Mailversand lokal am Desktop und Mobilgerät
  geprüft; keine neuen Diagnose-Textblöcke unter jeder Aktie.
- [x] Erneute Mailprüfung identischer offener Gap-Pläne nach Ablauf des
  8-Stunden-Cooldowns mit echtem Produzenten und simuliertem SMTP geprüft.
- [x] Finalen eingefrorenen Gesamttest abgeschlossen: 9.304 bestanden,
  5 Plattform-Skips. Bundle `4379c5dca540`, Syntax von 20 Python-Dateien und
  Diff geprüft; Code, Tests und Dokumentation zur Veröffentlichung freigegeben.
- [ ] Neue Revision auf Hetzner ausrollen und neue Gap-Läufe prüfen.
- [ ] Echte Signalzustellung nach Rollout bestätigen; keine Testmail oder
  fingierte Signal-Freigabe als Ersatz für einen gültigen Produktionslauf.

## Erledigt

- [x] VIAV-Widerspruch live in der angemeldeten App bestätigt: Chartkurs
  39,13 USD unter Plan-Stop 39,29 USD, trotzdem alte Bewertung „Jetzt traden
  (98/100)“. Ursache: vermischte Beobachtungsstände.
- [x] Aktien-Seitenleiste: Kurs, Prozentänderung, Stop-/Zielstatus und Abstand
  verwenden denselben passenden Chartstand; gespeicherte Planlevel bleiben
  unverändert. Historische Bewertung und Ablehnungsgründe sind geschlossen
  unter „Berechnungsbasis“ abrufbar, keine erfundene neue Health-Freigabe.
- [x] Eigener Wyckoff-Schalter in Seitenleiste und Chartanalyse, unabhängig
  von Patterns. Gescheiterte historische Strukturen nur in Details.
- [x] Wyckoff-Zwischenbrüche bei mehrkerziger Spring-/UTAD-Erholung,
  Plateau-Bestätigungszeitpunkte und Rückdatierung von Erholungsfristen korrigiert.
- [x] Regelversion `wyckoff_v3_rules_3`, Strategie-Cacheversion 14.
- [x] Redundante Ergebniszähler entfernt; Datenstand und geschlossene Diagnose
  bleiben. Echte Fehler, unvollständige Daten und blockierte Starts bleiben sichtbar.
- [x] Unabhängiges Audit einschließlich Nachprüfung abgeschlossen.
- [x] Desktop-/Mobilprüfung: 1440×1000 und 390×844, keine Konsolenfehler/Überläufe.
- [x] Finale Gesamtsuite: **9.105 bestanden, 4 Windows-/Linux-Plattformskips**;
  Bundlebindung, JavaScript-Syntax und Git-Diff geprüft.
- [x] Commit und GitHub-Push bestätigt. Private `output/`-Dateien nicht hochgeladen.
- [x] Vorheriges Paket `c3594ff`: Reminder-Laufzeiten 1/3/7/14/30 Tage,
  getrennte Mail-/App-Auswahl, Löschung aktiver Reminder, einmalige Auslösung
  ohne erneute Anzeige sowie Chart zuerst. Dieses Paket ist im aktuellen HEAD enthalten.
- [x] Rollout von `5acd7cf` am 29.09. per Health-Revision und Frontend-Fingerprint
  bestätigt. Kein Pull oder Neustart durch diese Nachprüfung.
- [x] Verbliebene VIAV-Formulierung korrigiert: Tabelle **„Im Scan freigegeben“**,
  Seitenleiste **„Chartkurs unter/über Entry“**. Kein Schluss aus einem einzelnen
  Chartkurs darauf, ob der Einstieg früher erreicht oder eine Order ausgeführt wurde.
- [x] Gleichartige Stop-/TP1-Texte einschließlich exakter Preisgleichheit korrigiert.
  Gespeicherte Level, Scannerregeln und serverseitige Mailfreigabe unverändert.
- [x] Nachkorrektur: **1.452 Tests bestanden** (Frontend, Wyckoff, Reminder),
  vier lokale Browserfälle (Long/Short, 1440/390 px) ohne Konsolenfehler oder
  Seitenleistenüberlauf; Bundlebindung, JavaScript-Syntax und Git-Diff geprüft.
- [x] Neue Code-Revision `b742bbe` und Frontend-Bundle `00c5288ac5f0`
  auf Hetzner per Health bestätigt. Keine Dienste durch diese Nachprüfung verändert.
- [x] Tatsächlich öffentlich ausgelieferte `index.html` und `app.bundle.js`
  auf Port 3000 mit dem Checkout verglichen: beide HTTP 200, beide Inhalts-Hashes
  identisch nach alleiniger CRLF/LF-Normalisierung. Bundle-Quellfingerprint
  `00c5288ac5f0550904f6f8650dea1b35928224dfe9473cf87104eda3f243e81f`.
- [x] Erneute isolierte Versand-/Scanner-Regressionsprüfung: **132 bestanden**,
  eine harmlose Pytest-Importwarnung. Geprüft: native Aktienpläne/Mailintegration,
  Swing-Isolation, SMTP-Ablehnungsbehandlung, Outbox, abschließende
  Strategierevalidierung, Pre-Market und Mail-Kandidatenpool. Keine echte Mail
  versandt und keine Produktionsdaten verändert.

## Als Nächstes – offen, nicht als erledigt melden

1. [ ] **Alle Dienste einzeln prüfen.** Revision,
   Bundle und ausgelieferte Frontend-Dateien sind bereits nachgewiesen.
   SSH ohne Passworteingabe wurde abgewiesen. Die angemeldete Browseransicht
   ist am 30.09. geprüft: Scheduler aktiv, Mailkonfiguration vorhanden.
   Persönliche Kanal-/Modus-Einstellungen nicht veröffentlicht. Keine Zugangsdaten
   gespeichert und keine Konto-Einstellungen geändert.
2. [ ] Einen neuen vollständigen Strategie-/Wyckoff-Lauf prüfen; alte
   Cacheversionen dürfen nicht als neu berechnete Ergebnisse gelten.
   Browser-Anmeldung ist vorhanden; neuer Zeitplan/Rollout bleibt separat.
3. [ ] **Exakte acht historische VIAV-Strukturen:** ohne deren Original-OHLCV
   noch nicht einzeln nachgerechnet. Live waren sieben als gescheitert und eine
   Distribution mit abgelaufenem Einstieg markiert – keine acht aktuellen Signale.
   Falls Originalkerzen verfügbar werden, eingefrorenen Datenstand mit dem
   vorhandenen Replay prüfen; keine bloße Sichtprüfung als Vollnachweis ausgeben.
   Die erneute Suche in den benannten privaten Audit-/Browserartefakten ergab
   Screenshots und synthetische UI-Fälle, keinen belegten Original-Kerzensatz.
4. [ ] **Echte Signal-Mailzustellung** anhand eines neuen gültigen Signals,
   Zustellungsjournal und tatsächlichem Empfang bestätigen. Das letzte Paket
   ändert keinen SMTP-Transport; UI-Kandidaten sind keine automatisch versandten Mails.
   Nutzer meldet eine empfangene Mail am **29.09.**; Betreff/Signalreferenz fehlen
   noch. Ohne Zuordnung weder eine Handelssignalzustellung noch einen allgemeinen
   SMTP-Ausfall behaupten. Anschließend Freigaben, Unterdrückungsgründe, Outbox und
   SMTP-Akzeptanzen im selben Zeitfenster vergleichen.

## Nachbörsen-/Nächster-Handelstag-Idee – untersucht, noch kein neuer Scanner

- Vorhanden: **Gap Momentum Long/Short**; die alten **Earnings Mover**-Namen
  sind Aliasse dieser Scanner, keine zusätzlichen unabhängigen Läufe
  (`modules/strategies.py`, `api.py`: `STOCK_STRATEGY_ALIASES`).
- Nutzer hat bestehende Gap-Scanner gewählt: feste Läufe 02:00/12:00 Zürich
  statt eines neuen Nachbörsen-Scanners. Mathematik korrigiert: fremden
  79-Punkte-Deckel entfernt, RVOL nicht vor Auswahl runden, Schuldpapiere
  ausschließen; Cacheversion 15. Dauerhafter Zeitplan und unabhängige
  Admission-Prüfung umgesetzt. Abschlussprüfungen grün; Rollout noch offen.
- Der Standard-Swingmodus verwendet abgeschlossene **1D-Börsensitzungen**.
  In diesem Modus schaltet `_strategy_scan_wrapper` die Beimischung von
  Nachbörsenpreisen bewusst ab. Der Datensatz bleibt ein Tagesplan, kein
  Nachbörsen- oder Live-Einstieg (`modules/stock_swing_contract.py`).
- Eine eigene zusammengefasste **Vorbereitung für den nächsten Handelstag**
  mit Abendbericht und erneuter Morgenprüfung ist noch nicht implementiert.
  Die Nutzerfrage nach ihrer Existenz aktiviert noch keinen neuen Versandplan.
- Vorschlag zur anschließenden Umsetzung: Long/Short getrennt; Tagesstruktur,
  Trend, relative Stärke, Liquidität, ATR und echte Unterstützungs-/Widerstandszonen.
  Nachbörsenbewegung separat mit Kurszeit, Volumen und Datenverzögerung anzeigen.
  Relatives Nachbörsenvolumen nur gegen vergleichbare Nachbörsen-Zeitfenster messen.
  Kompakte Ausgabe: Aktie, Richtung, Begründung, bestätigbarer Trigger,
  strukturelle Invalidierung, nächste Zielzone und Ereigniswarnung. Keine erfundenen
  Level, keine Pflichtzahl an Treffern und keine automatische Handelsfreigabe.
- Nächste Sitzung über den Börsenkalender ermitteln (Feiertage, verkürzte Tage,
  Zeitumstellung); vor Handelsbeginn Gap/News/Datenstand erneut prüfen. Eine
  Short-Auswahl beweist keine beim Broker verfügbare Aktienleihe.
- Datenquellen geprüft: [Massive-Nachbörsendaten](https://massive.com/knowledge-base/article/does-massive-offer-pre-market-and-after-hours-data),
  [Aggregate](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
  [FINRA](https://www.finra.org/investors/insights/extended-hours-trading)
  erläutert unter anderem, warum Nachbörsenpreise den nächsten Eröffnungskurs
  nicht festlegen. Deshalb Vorbereitung und aktuelle Handelsfreigabe getrennt halten.

## Nachweis der erneuten lokalen Prüfung

```powershell
& '.\.codex_pytest_env\Scripts\python.exe' -B tmp/offline_mail_fix_tests_20260925.py -q --tb=short test_stock_native_plan_mail_integration.py test_stock_swing_mail_isolation.py test_api_smtp_rejection_recovery.py test_mail_outbox.py test_stock_strategy_final_revalidation.py test_premarket_radar.py test_stock_mail_candidate_pool.py
```

Ergebnis: `132 passed, 1 warning in 40.29s`.
Privates JUnit-Artefakt:
`output/mail-fix-qa-c948d45cbf4a4bc3b81779c866256261/results.xml`.
Das ist eine gezielte Nachprüfung, kein neuer Gesamtsuiten- oder Zustellnachweis.

## Wiederaufnahme ohne erneute Untersuchung erledigter Arbeit

- Zuerst Git-Stand und [Prüfbericht](docs/CHART_PRICE_WYCKOFF_AUDIT_2026-09-28.md)
  sowie [Nachprüfung](docs/SCAN_PRICE_BASIS_FOLLOWUP_2026-09-29.md)
  lesen; dort stehen Testbefehle, Artefakte und genaue Grenzen.
- Funktionierender lokaler Testinterpreter: `.codex_pytest_env\Scripts\python.exe`.
  Die alte `.venv` kann auf eine fehlende Python-Installation zeigen.
- API-Tests nur mit dem isolierenden lokalen Launcher
  `tmp/offline_mail_fix_tests_20260925.py`: keine produktiven DBs, Secrets oder SMTP.
- Vorhandene private Exporte zuerst auswerten; keinen identischen Export ohne
  konkreten neuen Bedarf verlangen. `output/`, Secrets und Browser-Anmeldung privat halten.
- Keine Lockerung von BI 17/20, Datenprüfungen oder Mailfreigaben als Ersatz für Fehlerbehebung.
- Die Übernahmen vom 28./29.09. bleiben erhalten. Bei der Nachprüfung vom
  29./30.09. wurden nur diese Aufgabenliste und der Handbuch-Einstieg aktualisiert;
  keine Scanner-, Mail-, Konto-, Datenbank- oder Serverkonfiguration geändert.
  Der anschließende neue Gap-/Mail-Auftrag verändert jetzt Code gemäß obigem
  Prüfbericht; diese frühere reine Nachprüfung ist kein Rolloutnachweis dafür.
- Ältere übergreifende Aufgaben bleiben in
  [Profitabilitäts-Prüfplan](docs/PROFITABILITY_PROTOCOL_2026-09-08.md)
  und [Projekthandbuch](PROJEKTHANDBUCH.md); deren historische Serverstände nicht
  mit einem heute geprüften Stand verwechseln.
