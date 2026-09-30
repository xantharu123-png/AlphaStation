# Aktuelle Aufgaben / Übergabe

Stand: **30.09.2026**, Gap-/BI-Reparatur und erneute Gegenprüfung.
Workspace: `C:\Projekt\TradingBot`, Branch `main`.
Aktueller Anschlussauftrag: alle sieben Gap-/BI-Auditbefunde und verwandte
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
   ist am 30.09. geprüft: Scheduler aktiv, Mailkonfiguration vorhanden,
   Signal-Mails aktiv, Aktien Swing AN, Mailmodus Swing. Keine Zugangsdaten
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
