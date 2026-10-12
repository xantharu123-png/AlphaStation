# Schlusskurs-Ausbruchsmails: verspätete Erstzustellung

## Auftrag und reproduzierter Fehler

TBBB/PLTR wurden laut Nutzer am Montag, 12.10.2026, 04:23 UTC versendet,
bezogen auf den abgeschlossenen Freitag, 09.10.2026. Der bestehende globale
Swing-Vertrag akzeptiert Freitag am Montag vor dem nächsten Schlusskurs zu Recht
als letzte vollständige Tagessession. Er begrenzte jedoch nicht das Alter einer
**erstmals erzeugten Ausbruchsmail**. Ein eigener Schlusskurs-Trigger fehlte;
ein kurz vor Datenverfügbarkeit gestarteter Stundenlauf konnte den nächsten
Lauf bis zum normalen Intervall verschieben. Der genaue ursprüngliche 04:23-
Startauslöser ist ohne damaligen Serverjournalbeleg nicht bestimmt.

Begrenzter Nutzerauftrag: zeitnah nach Verfügbarkeit der bestätigten Tagesdaten
senden, keine Erstnachholung am Folgetag/Wochenende/Montag. Die Bezeichnung
„Ausbruch bestätigt“ und die vorhandenen Gap-Morgentermine bleiben unverändert.
Keine Landingpage-, Chart-, Risiko-, Schwellen-, Kontoeinstellungs- oder
Installationsänderung gehört zu diesem Patch.

## Verhalten

- `stock_swing.breakout_notice_current`: bestehende exakte 1D-Referenzprüfung
  plus gleiche New-York-Kalenderdate von Versand und Referenzsession.
  Normaler Swing-Vertrag bleibt unverändert. Beispiel: Freitag ab 20:15 UTC
  möglich, Freitagabend ET trotz Samstag UTC weiterhin möglich; ab Samstag
  00:00 ET und am Montag keine erste Freitags-Ausbruchsmail mehr.
- `stock_swing.post_close_scan_due`: genau nach Kalenderclose + bestehender
  900-Sekunden-Providerverzögerung wird ein neuer Lauf fällig, wenn bisher nur
  vorher gestartete Versuche vorliegen. Feiertage, Frühschlüsse und DST werden
  vom vorhandenen Börsenkalender übernommen, keine feste 22-Uhr-Annahme.
- API-Scheduler: Sonderfälligkeit beeinflusst **Priorität und Startbedingung**.
  Leichte Jobpositionen, Gap-Reservierung, laufende/manuell pausierte schwere
  Worker und wiederaufgenommene Worker behalten ihre bestehenden Rechte.
  Verweigerte Aufnahme konsumiert den Trigger nicht; erfolgreicher Start
  konsumiert ihn. Bei anschließendem Worker-/Providerfehler gilt der normale
  Stundenretry, kein 30-Sekunden-Startloop.
- Startup: passende aktuelle Session-Metadaten in jeder automatischen Leaf-
  Datei sind notwendig, auch bei null Treffern. Aktuelle leere Resultate bleiben
  verwendbar. Ein neuer Dateizeitstempel allein macht alte Daten nicht frisch.
- Sender: neue Altersprüfung im gemeinsamen Evidenzpfad, auch nach Rendering
  direkt vor dem Transport. Claim, Empfängerpräferenz, Budget, Vollsignal-
  Vorrang, native Stop-/Ausbruchbelege und vollständige Handelsplanprüfung
  bleiben unverändert. Intern weiterhin der vorhandene separate Kanal; keine
  Umbenennung oder Behauptung eines vollständigen Plans.

Die vorhandenen Gap-Termine 02:00 und mittags werden nicht verschoben oder
abgeschaltet. Deren volle Handelsplan-/Rücktestmails behalten ihre bisherigen
Regeln. Nur eine erstmalige separate Ausbruchsmail muss ebenfalls rechtzeitig
zur Referenzsession sein.

## Frische QA

Alle API-Tests über `scripts/run_offline_tests.py`: private temporäre Auth-,
Tracker-, Outbox- und Dedupe-Pfade, `.invalid`-Adressen, keine echten Credentials,
externe Netzwerkverbindungen und SMTP gesperrt. Lokaler Loopback wurde für
Windows/Asyncio erlaubt; der übrige Testschutz blieb aktiv.

- Unveränderte bestehende Mail-Suite: **89 passed**, XML
  `output/close-mail-baseline-20261012/qa-4d1a797d1980/results.xml`.
- Neue Integration vor API-Korrektur: **6 failed / 1 passed**, XML
  `output/close-mail-red-20261012/qa-b6b211f10968/results.xml`.
  Echte Assertion-Failures: verspätete Direkt-/Sweep-Mail, fehlender Close-Start,
  alter leerer Startup-Cache, fehlende Close-Priorität und verweigerte Aufnahme.
- Separate Kalender-/Vertrags-TDD: zuerst **95 failed**, danach **97 passed**;
  zwei zusätzliche Uhr-Austauschtests zuvor **2 failed / 95 passed**.
- Erste integrierte Nachprüfung: **299 passed**, 1 bekannte AnyIO-Warnung,
  92,58 s, XML `output/close-mail-green-20261012/qa-580e4076b525/results.xml`.
- Ergänzte Mitternacht/Producer-Metadaten/Retry und ältere Cache-Verträge:
  **163 passed**, 1 bekannte AnyIO-Warnung, 15,80 s, XML
  `output/close-mail-compatibility-20261012/qa-413325411431/results.xml`.
  Dieser Lauf überlappt den vorigen; Zahlen nicht zur Abdeckung addieren.
- Unabhängiger read-only Review: keine Critical-/Important-Funde. Optionale
  separate Realtime-Startup-Charakterisierung nicht notwendig für das Gate;
  neue Cache-Prüfungen sind explizit auf Starter-Swing begrenzt.
- Separater Abzug des exakt vorgemerkten Veröffentlichungsinhalts (Tree
  `295de8c7f317612547662d40bb13ba53add7d847`): **366 passed / 0 failed /
  0 skipped**, 1 bekannte AnyIO-Warnung, 95,55 s. XML
  `output/close-mail-release-295de8c7f317/qa/qa-3efa7ef8cccc/results.xml`.
  Alle zehn Produkt-/Testdateien entsprechen diesem Abzug nach regulärer
  Git-EOL-Konvertierung; API-Bytes auch roh identisch. Keine privaten Exporte
  oder übernommenen Deployment-Änderungen im vorgemerkten Inhalt. Die spätere
  Ergänzung des QA-/Publish-Status betrifft ausschließlich Dokumentation.
- Eingefrorener Gesamtlauf: **12.517 passed / 97 failed / 7 skipped**, 1 bekannte
  AnyIO-Warnung, 481,03 s; **kein Gesamt-PASS**. XML
  `output/close-mail-frozen-full-20261012/qa-724838b211c7/results.xml`.
  Vorher-Baseline: 12.404 passed / 97 failed / 7 skipped. Exakter Vergleich
  sortierter `classname::name`: dieselben 97 Fehler, keine neuen oder anderen.
  Unverändert Auto-Update 55, Migration 10, Retirement 5, Security-Reaudit 27.
  Es bestehen zusätzlich 113 neue Fälle. Die bekannten Windows-/Deployment-
  Probleme wurden hier nicht umgebaut; Fehlerlisten und Traces bleiben im XML.

Quellhashes beim Start des Gesamtlaufs:

- `api.py`: `871e1528ad0ae34829245d58e8bb3d63d579a64d289e9804e69ac28b06bd5574`
- `modules/stock_swing_contract.py`:
  `0e965592d606373426c5c607712f58788c3639c651e2061637d4669f0341fadf`

Beide Hashes nach dem Gesamtlauf unverändert; historische Source-Freeze-Guards
bestanden ebenfalls. Die separate Release-QA enthält keine übrigen WIP-Dateien.

## Grenzen und Veröffentlichung

Ein laufender schwerer/manuell pausierter Scan wird nicht abgebrochen. Er kann
den neuen Schlusskurslauf verzögern. Anschließend sind Datenabruf, beide Sweep-
Leaves und der gemeinsame Versandguard nötig: nicht behaupten, dass jede Mail
genau 15 Minuten nach Schlusskurs oder jede Aktie überhaupt versendet wird.
Qualitäts-/Empfänger-/Doppelschutz bleiben erforderlich. Lang anhaltende
Provider-/Workerprobleme können eine rechtzeitige Mail verhindern; der neue
Vertrag erlaubt ausdrücklich keine nachträgliche Erstmail am nächsten Tag.

Feature-Commit **fd983fa63d88c6504df0cecc23759fd22afc59c8** erfolgreich nach
`origin/main` gepusht; `git ls-remote` bestätigt exakt diesen Hash. Nur die
13 geprüften Code-/Test-/Übergabedateien wurden veröffentlicht. Privates
`output/` und sämtliche übernommenen Deployment-/anderen Änderungen bleiben
unveröffentlicht und erhalten. Nachfolgende Status-Ergänzung nur Dokumentation.
Nicht auf Hetzner installiert.
Keine echten Scans, Mailversuche, Serveränderungen oder Einstellungen durch
diesen Patch. Neue Serverrevision und reguläre Zustellung nach Update offen.
Die vom Nutzer gelieferten ursprünglichen TBBB-/PLTR-Mails sind Inboxbelege des
alten Pfads, nicht die Live-Abnahme dieser Zeitkorrektur.

Frischer öffentlicher Healthcheck 12.10.2026 ca. 07:50 UTC am API-Port 8000:
`healthy`, Revision `2799972938ee`. Die neue Zeitkorrektur ist noch nicht live.
Frontend-Port 3000 stellt diese API-Route nicht bereit (404); kein Auth-Fehler
oder Dienstabbruch daraus ableiten.
