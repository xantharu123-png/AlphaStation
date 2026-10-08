# Account-Übergabe – Signal-Mails, 08.10.2026

## Einstieg und Auftrag

Workspace: `C:\Projekt\TradingBot`, Windows/PowerShell. Zuerst diesen Bericht
und den aktuellen Anfang von `TODO.md` lesen, danach den tatsächlichen Checkout
prüfen. Nicht die gesamte Chatgeschichte neu anfordern.

Der Nutzer erhält weiterhin keine regulären Signal-Mails. Nach der ersten
Dokumentationsübergabe wurde die gezielte Reparatur der beiden Diagnose-Lücken
beauftragt. Danach wurde Commit/Push ausdrücklich freigegeben. **Diagnosecode
und Tests sind unter `43ea4aa80a9a5ac34d066787f5ee3f7ddba90d86` committet,
auf `origin/main` gepusht und dort per SHA abgeglichen. Installation auf
Hetzner und echte reguläre Signalzustellung bleiben offen.** Keine Versandregeln
oder Schwellen geändert. Dieser Bericht aktualisiert den Veröffentlichungsstand.

## Bestätigter Stand

- Neue Produktveröffentlichung:
  **`43ea4aa80a9a5ac34d066787f5ee3f7ddba90d86`**, ausgehend von
  `b7415f1cd71fe02fae184b1b518761947d3c7f9f`. Frühere Runtime-Reparatur
  `8d119ad4b8e39bf7c38ecaf956a7555e82f1798f`. Die begleitende Dokumentation
  erhält einen nachfolgenden Commit; aktuelles HEAD/`origin/main` an Git prüfen.
- Live am **08.10.2026**: `http://178.104.69.209:8000/api/health` lieferte
  `healthy`, Revision **`b7415f1cd71f`**, Bundle **`ba7e64a7b792`**.
  Das frühere API-Paket ist installiert, **nicht die neue Diagnoseveröffentlichung**.
  Ein neuer Operator-Pull ist nun für `43ea4aa` samt nachfolgender Dokumentation
  erforderlich, nicht aufgrund historischer TODO-Einträge. Beide Dienste und
  der konkrete Betreiberbefehl wurden durch Health nicht einzeln nachgewiesen.
- Die frühere veröffentlichte Reparatur vereinheitlicht App-/Mailvorprüfung, erhält
  BI-Startfeedback und begrenzt gemeinsame Datenfehler. Sie ist kein Nachweis
  tatsächlicher Signalzustellung; sie lockert keine SMTP-/Freigaberegeln.
- Neue veröffentlichte Produktdateien: `api.py`, `frontend/index.html`,
  `frontend/app.bundle.js`; Bundle **`8f8a6c0c5bae`**. Die Module
  `modules/scan_mail_audit.py` und `scripts/run_offline_tests.py` unverändert.
  Neue Tests: `test_mail_reason_projection_completion.py`,
  `test_completed_stock_mail_audit_visibility.py`,
  `test_frontend_completed_mail_audit.py`. Diese sechs Dateien sind committet
  und gepusht. Keine sonstigen Runtime-/Deploy-Dateien aufgenommen.

## Letzter Livebefund – nur dieses Zeitfenster

Admin → Mailversand, Browseranzeige **08.10. 07:35:11**:

- 1 Swing-Empfänger, 1 Crypto-Empfänger;
- SMTP-Annahmen **0**, ausgelassen **5**, Versandfehler **0**, Warteschlange **0**;
- Entscheidungen maximal 50 seit API-Start und höchstens 24 Stunden, keine
  vollständige Tages-/Wochenhistorie;
- Aktienentscheidung 07:30:06: Tagesqualität unter 78/96, Tageshoch der
  Referenzkerze an/nahe TP1, dünne 20T-Grundliquidität. Eine Zusammenfassung
  kann verschiedene Kandidaten betreffen, nicht eine widersprüchliche Aktie;
- Crypto-Entscheidungen nennen fehlende Freigabe/BTC-Kontext beziehungsweise
  Watch-/No-Chase-Gründe;
- 07:32:12: **„Versandgrund noch nicht zugeordnet“**. Konkreter Rohgrund
  weiterhin unbekannt, nicht ohne Evidenz als `no_candidates` ausgeben.

Momentum-Vorprüfung: 13 gespeicherte Kandidaten, 0 freigegebene Signal-Mails.
RELL einzeln im UI geprüft: Trade-Score 95, Setup-Score 92, Tagesqualität 86/96,
aber Mailgrund `momentum_mail_blocked_thin_baseline_liquidity`. Offener Rücktest
ist eine separate Warnung. Der Codefloor beträgt 2 Mio. USD Median-Dollarvolumen20;
die konkrete Live-Medianzahl/Originalhistorie war in der UI nicht verfügbar.
Deshalb weder deren Zahlenrichtigkeit behaupten noch die Schwelle lockern.

Technische Testmail war früher laut Nutzer angekommen; diese einmalige
Autorisierung ist verbraucht. Das ersetzt keine reguläre Signal-Mail. 0 sichtbare
SMTP-Annahmen beweisen keinen SMTP-Ausfall. Warteschlange 0 beweist keinen
erfolgreichen direkten Tradeversand: zeitkritische Trade-Mails werden nach
Transportfehlern nicht verspätet als Einstiegssignal nachgesendet.

## Lokal abgeschlossener Diagnoseblock

### 1. Sichere Gründe vervollständigen

`api.py`: `_admin_mail_delivery_status()`, `_stable_suppression_reason()`,
`_ALERT_SUPPRESSION_LABELS`; Frontend: Admin-Mailtabelle.

Behoben: bekannte `no_candidates`-, `final_*`-, Tracker- und Doppelschutzgründe
erhalten zusätzliche geprüfte Texte. Alle übrigen erlaubten IDs bleiben über
einen festen technischen Text mit der sicheren ID identifizierbar; nicht jede
ID ist individuell übersetzt. Die globale Normalisierung, Freigaberegeln und
SMTP-Grenze bleiben unverändert. Gegenprüfungen umfassen alle erlaubten IDs,
gemischte Zusammenfassungen, Identifier-Kollisionen, tatsächliche Provider-
Fehlerfamilien und Datenschutz. Keine Rohtexte, Subjects, Adressen, SMTP-
Ausnahmen oder Provider-URLs ausgeben. Unbekannte Gründe bleiben unbekannt.

### 2. Abgeschlossene Mailprüfung sichtbar machen

`api.py`: `_publish_stock_strategy_attempt()`, `_read_stock_strategy_attempt()`,
`_stock_strategy_result_attempt()`; `modules/scan_mail_audit.py`.

Persistierte Blatt- und Sweep-Versuche enthalten die letzte Mailprüfung.
Der Einzelstrategie-Cache wird vor dem Mailguard geschrieben, der Sweep-Cache
danach. Die normale Ergebnis-API entfernt aber `latest_attempt.diagnostics`;
Scheduler-RAM liefert abgeschlossene Mailprüfungen nicht zuverlässig.
Die frühere Annahme, vollständiges `mail_audit` sei dort langlebig verfügbar,
war falsch. Jetzt bleibt die sichere Mailprojektion in
`diagnostics.latest_attempt.mail_audit` erhalten. Admin erhält getrennt
`delivery.stock_attempts` (fünf unterstützte Einzelstrategien) und
`delivery.stock_sweep` (automatischer Aktien-Sammellauf). Wichtig: automatische
Blattläufe verwenden `send_email=False`; reguläre Sammelversand-Evidenz liegt
im Sweep. Nicht nur Blattdateien ansehen. Keine alten Cacheprüfungen als echte
Versandprüfung umetikettieren. BI/Bear/ORB/Crypto sind von dieser ergänzenden
persistierten Aktien-Versuchsansicht nicht abgedeckt.

Nur validierte, begrenzte Zähler/IDs/Zeitstände sicher projizieren. Fehlende
Evidenz als unbekannt behandeln, nicht als null. Senderaufruf ist nicht SMTP-
Annahme; `trade_sender_called` und tatsächliche Transportausgänge unterscheiden.
Chronologie und Identität bleiben erhalten: ein abgeschlossener Versuch allein
erfindet keinen neuen Ergebnisstand, aktiven Worker oder Schedulerabschluss.
Die Adminansicht **Letzte Aktien-Mailprüfung** ist standardmäßig eingeklappt.
Acht getrennte Kandidaten-/Transportfelder nicht addieren;
fehlende Nachweise `—` und beobachtete Null 0 unterscheiden. Keine neue Diagnose
in der normalen Aktienliste. Der Reader bleibt strikt begrenzt und rein lesend.

Vorhandene Tests als Ausgangspunkt:

- `test_admin_mail_delivery_status.py`
- `test_frontend_admin_mail_status.py`
- `test_stock_strategy_attempt_status.py`
- `test_scan_mail_audit.py` und `test_scan_mail_audit_adversarial.py`
- `test_mail_readonly_review.py`

Privater anonymisierender Reader, falls später ein gezielter Serverbeleg nötig
ist: `scripts/collect_server_evidence.py`, `safe_strategy_attempt_summary()`
und `_scan_mail_audit_projection()`. Nicht standardmäßig erneut einen großen
Gesamtexport anfordern. `GET /api/email-alert-status` ist kein Ersatz für eine
rein lesende Prüfung: sein Guard kann Providerdaten auffrischen.

## Nächster Schritt: Installation und echte Kette nachweisen

Das abgegrenzte Paket ist veröffentlicht; private `output/`-Artefakte und
Deployänderungen blieben ausgeschlossen. Installation separat bestätigen;
aktueller Livebefund `b7415f1` ist kein Deployment der neuen Diagnose. Für die
vorhandene Installation genügt der normale Operator-Pull mit API/BG-Neustart
nach Ende laufender Scans; kein neues Deployskript oder Installationsumbau.

Passenden aktuellen Lauf mit Revision/Identität/Zeitstand erfassen:
Scannerabschluss → Freigabe → finale Revalidierung → Sender → SMTP → Postfach.
Diese Nachweise fehlen noch. Aktuelle Vorab-Ablehnungen erklären nicht allein
die gesamte wochenlange Versandgeschichte. Keine neue Sender-/Filterreparatur
ohne konkreten Fehlerreproduzierer und keine profitable Strategie daraus behaupten.

## Weitere offene Nachweise nicht vergessen

- Neuer realer BI-Backtest: 3 Monate/200 Aktien, Mindestpreis 5,
  Mindestvolumen 200.000; Abdeckung, 17/20-Kandidaten, Pläne, Einstiege und
  ungelöste Folgefenster getrennt prüfen. Bericht:
  [BI-Backtester/Mail-Recovery](BI_BACKTEST_MAIL_RECOVERY_2026-10-04.md).
- Wochenreport/Tracker: belegte Zustellkohorten und kausale Kurswege nachrechnen.
  Alten Report oder alte Tracker-R-Werte nicht als Performance des neuen Pakets übernehmen.
- Historische Dreimonats-Stichproben existieren bereits:
  [Tiefenaudit vom 02.10.](SCANNER_DEEP_AUDIT_2026-10-02.md), private Artefakte
  `output/scanner-deep-audit-20261002/`. Alter Modellstand, je drei fixierte
  Assets, keine freigegebenen Modellpläne; ohne Trades ist die Trefferquote
  nicht berechenbar, nicht 0 %. Keine vollständige neue Scanner-Rangliste bewiesen.
- Exakte VIAV-/AST-/LSPD-Levelprüfung braucht die ursprünglichen abgeschlossenen
  OHLCV-/VRVP-Daten zum selben Stand. Beim LSPD-Beleg wurden Zonenidentität und
  Planarithmetik geprüft, nicht die vollständige Kerzenherkunft.
- BPIQ/Biotech-Abo ist laut Nutzer abgelaufen; separates 401-Providerproblem,
  kein Beleg eines SMTP-Fehlers. Keine Abo-/Kostenänderung autorisiert.
- Forex/Futures sind nicht vollständig implementierte Scanner. Kein neues
  Modul oder After-Hours-Mailabo als Nebenaufgabe beginnen.

## QA, Arbeitskopie und Zugriff

Letzter veröffentlichter Release-Snapshot vom 07.10.: **11.569 bestanden,
2 Windows-Skips**, vier unveränderte Deploy-Testdateien ausgeschlossen.
`output/release-verification-20261007/qa-8b465e7207f5/results.xml`.
Das ist historische Paket-QA, kein frischer Linux-/Provider-/SMTP-Nachweis.
Der erste enge Offline-Test am 08.10. hing vor pytest-Ausgabe und wurde beendet.
Ursache im Windows-Sandboxlauf: Eventloop-Import verwendet Socketpair/Loopback
(`modules/brokers.py`, `_fallback_socketpair.accept()`). Der isolierte Launcher
läuft mit genehmigter lokaler Prozessfreigabe; externes Netzwerk/SMTP bleiben
gesperrt. Keine echten Zugangsdaten oder `.env` importieren.

Frische QA der Diagnose-Reparatur:

- Veröffentlichungs-Gate aus dem tatsächlichen Git-Index-Snapshot (nicht dem
  schmutzigen Arbeitsverzeichnis): **12.074 bestanden, 2 übersprungen,
  0 Fehler/Errors**, 346,71s laut pytest. Nur die unten genannten vier Deploy-
  Dateien ausgenommen; Kalender-/Commerce-/Deploy-Dateien aus unverändertem HEAD.
  Getesteter Tree entspricht dem Produktcommit:
  `9cbd25710cb2b38c61ae58db306171dece6a14eb`.
  `output/release-verification-20261008-mail-diagnostics-ffc5c6f0aab2/qa-5f49ff7ebbc0/results.xml`.
  Bundle `8f8a6c0c5bae` aus dem Snapshot geprüft. Zusätzliche unabhängige
  lesende Release-Prüfung fand keine belegten Code-/Datenschutzblocker oder
  Abhängigkeit vom ausgeschlossenen WIP. Keine Linux-/Provider-/SMTP-/CI-
  Prüfung behaupten. Folgende Läufe dokumentieren die vorherige Arbeitskopie:
- RED-Reproduktionen vor Produktänderungen; **724 gezielte Tests bestanden**,
  einschließlich echter automatischer Sweep-/Senderguard-/Publikations-/Leser-
  Kette ohne SMTP, strikter Datenschutzprojektion und kompiliertem JSX.
  `output/mail-fix-qa-6c2fa8c3054342a3a3626549624c07ed/results.xml`.
- Ungefilterter Gesamtlauf: **11.699 bestanden, 97 fehlgeschlagen,
  6 übersprungen**, 392,65s. Alle Fehler in `test_deploy_auto_update.py` (55),
  `test_deploy_migration.py` (10), `test_deploy_retirement.py` (5),
  `test_deploy_security_reaudit.py` (27); vorhandene Windows-/Retirement-
  Vertragskonflikte. Nicht als vollständig grünen Gesamtlauf melden.
  `output/mail-fix-qa-cdcaba96bf8d4fa79dbc5316412cf8ce/results.xml`.
- Finaler breiter Produktlauf der endgültigen Änderungen mit ausschließlich
  diesen vier Dateien ausgenommen: **12.070 bestanden, 2 übersprungen,
  0 Fehler/Errors**, 329,45s laut pytest. XML:
  `output/mail-fix-qa-59f86c89f6dc4b2c851e43bb7f074673/results.xml`.
  Die 97 Fehler des ungefilterten früheren Laufs bleiben dokumentiert;
  Deploy-/Installations-WIP wurde nicht geändert oder repariert.
- Bundleprüfung **`8f8a6c0c5bae`**. Interner Browser,
  `http://127.0.0.1:18782/` mit kontrollierten GET-Fixtures, keine Upstream-
  Verbindung: Admin → Mailversand → eingeklappte Prüfung → eigene Sweep-/Blatt-
  Zähler und Zeiten sichtbar. Desktop und 390×844 mobil; kein Seitenüberlauf,
  Tabelle separat scrollbar, keine Konsolenfehler/Framework-Overlay. Vorhandene
  Tailwind-Runtimewarnung bleibt. Viewport zurückgesetzt. UI-Beleg ist lokale
  Fixture-QA, nicht reale Versandaktivität.
  Bildnachweise außerhalb des Repositorys:
  `C:\Users\miros\.codex\visualizations\2026\08\11\019fef3d-c9a9-7803-ab66-6631a424c1de\mail-diagnose-20261008-desktop.jpg`
  und `mail-diagnose-20261008-mobile.jpg` im selben Verzeichnis.
- Unabhängige lesende Abschlussprüfung des Diffs: keine belegte Privacy-/GET-
  Schreib-/Zustands- oder Sendervertragsregression; ersetzt keine reale SMTP-QA.

Für spätere lokale Tests `.codex_pytest_env\Scripts\python.exe` und
`scripts/run_offline_tests.py` benutzen; dieser Launcher isoliert Zustand,
fingiert Zugangsdaten und sperrt externes I/O/SMTP. API nicht direkt mit echter
`.env` importieren. Erst RED-Reproduktion, gezieltes GREEN, proportionaler
Nachlauf und Bundleprüfung bei tatsächlichen Frontendänderungen.

Vererbtes Dirty-WIP erhalten: Handbücher/Commercial/Handoff, Deploy-/Install-
Dateien, `docs/SCANNER_REAUDIT_REPAIR_2026-09-30.md`, mehrere Kalender-/Commerce-/
Deploy-Tests, untracked `test_deploy_retirement.py`, privates `output/`.
`deploy/safe_deploy.sh` ist lokal gelöscht, aber dessen Retirement nicht in
diesem Release veröffentlicht. Kein Ersatzinstaller, Eigentumsumbau, Reset,
Clean, Cachelöschen oder `git add -A`. Private Exporte nicht auf GitHub hochladen.

Direkter SSH-Batchzugang wird abgewiesen; Passwort nur vom Nutzer im Terminal
eingeben lassen, nicht speichern oder lesen. Zuletzt war der interne Browser
angemeldet; im neuen Account frisch prüfen, nicht als dauerhafte Anmeldung
voraussetzen. Admin-Systemlogs waren leer und lesen eine andere Datei als das
systemd-Journal; das ist kein Beweis fehlender Aktivitäten.

**Diagnosecode und Tests committet/gepusht; diese Übergabe dokumentiert den
freigegebenen Veröffentlichungsstand. Keine Produktions-/Mail-/Datenbank-/
Einstellungsänderung. Reale Signal-Mail
weiter nicht bestätigt.** Eigener lokaler Fixtureprozess und QA-Tab geschlossen;
Nutzer-Tab unverändert geöffnet gelassen. Keine privaten QA-Artefakte veröffentlichen.

## Kurzer Anschlussauftrag zum Kopieren

> In `C:\Projekt\TradingBot` nahtlos fortsetzen. Zuerst `TODO.md` und
> `docs/HANDOFF_SIGNAL_MAIL_2026-10-08.md` lesen und den Checkout erhalten.
> Live lief zuletzt `b7415f1cd71f` / `ba7e64a7b792`. Neue Diagnose ist unter
> `43ea4aa80a9a5ac34d066787f5ee3f7ddba90d86` auf `origin/main` veröffentlicht;
> aktuelles Git-/Remote-HEAD samt begleitendem Dokumentationscommit prüfen,
> dann Operatorinstallation separat bestätigen. Danach regulären Sweep bis finale Prüfung,
> Sender, SMTP und Postfach nachweisen. Fehlende Maildaten nicht als 0 ausgeben;
> fünf Blattversuche und Sweep getrennt halten. Keine Schwellen lockern,
> keine erneute technische Testmail oder Produktionsänderung ohne Auftrag.
> Nach jedem abgeschlossenen Block TODO mit echter QA und Veröffentlichungs-
> sowie Livezustand aktualisieren.
