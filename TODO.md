# Aktuelle Aufgaben / Übergabe

Stand: **29.09.2026**, nach der Nachprüfung der Scan-/Chart-Zeitstände.
Workspace: `C:\Projekt\TradingBot`, Branch `main`.
Live bestätigte Basis: **`5acd7cf3468116a0fdf8437659be4baaac4f59f2`**,
Frontend **`22c113cb3556`**. Öffentliche Health-Antwort vom **29.09.2026,
19:20:35** (Serverzeit): `healthy`, Revision und Bundle stimmen überein.
Neue lokale Nachkorrektur: Frontend **`00c5288ac5f0`**; Details im
[Nachprüfbericht](docs/SCAN_PRICE_BASIS_FOLLOWUP_2026-09-29.md).
Commit-/Pushstand der Nachkorrektur am Git-Verlauf prüfen; sie ist mit dieser
Health-Antwort noch **nicht** als auf Hetzner ausgerollt bestätigt.

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

## Als Nächstes – offen, nicht als erledigt melden

1. [ ] **Nur die neue Nachkorrektur ausrollen**, sobald sie gepusht ist und keine
   laufenden Scans unterbrochen werden. `5acd7cf` ist bereits nachgewiesen live.
2. [ ] Danach neue Health-Revision und Bundle **`00c5288ac5f0`**, aktive Dienste
   und Browser mit Strg+F5 prüfen. Health allein bestätigt nicht alle Dienste.
3. [ ] Einen neuen vollständigen Strategie-/Wyckoff-Lauf abwarten; alte
   Cacheversionen dürfen nicht als neu berechnete Ergebnisse gelten.
4. [ ] **Exakte acht historische VIAV-Strukturen:** ohne deren Original-OHLCV
   noch nicht einzeln nachgerechnet. Live waren sieben als gescheitert und eine
   Distribution mit abgelaufenem Einstieg markiert – keine acht aktuellen Signale.
   Falls Originalkerzen verfügbar werden, eingefrorenen Datenstand mit dem
   vorhandenen Replay prüfen; keine bloße Sichtprüfung als Vollnachweis ausgeben.
5. [ ] **Echte Signal-Mailzustellung** anhand eines neuen gültigen Signals,
   Zustellungsjournal und tatsächlichem Empfang bestätigen. Das letzte Paket
   ändert keinen SMTP-Transport; UI-Kandidaten sind keine automatisch versandten Mails.

## Serverbefehl für die Nachkorrektur

Erst nach bestätigtem Push und Abschluss laufender Scans im Server-Terminal:

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

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
- Übernommene Dokumentationsänderungen vom 28.09. wurden bewahrt und hier
  um die Nachprüfung vom 29.09. ergänzt; Git-Status zeigt offene Änderungen.
- Ältere übergreifende Aufgaben bleiben in
  [Profitabilitäts-Prüfplan](docs/PROFITABILITY_PROTOCOL_2026-09-08.md)
  und [Projekthandbuch](PROJEKTHANDBUCH.md); deren historische Serverstände nicht
  mit einem heute geprüften Stand verwechseln.
