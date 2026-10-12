# Account-Übergabe – Signal-Mails, 08.10.2026

## Aktueller enger Auftrag – 12.10.2026: Schlusskurs-Versandalter

Nutzer hat tatsächliche TBBB-/PLTR-Zustellung am Montag 04:23 UTC mit Freitag-
Referenz 09.10. belegt. Die alte pauschale Aussage „keine Zustellung bestätigt“
darunter ist für diese beiden Mails überholt; volle Trade-Mails/Fills bleiben
separat. Titel „Ausbruch bestätigt“ nicht umbenennen. Keine Landingpage-Änderung.

Zeitkorrektur lokal implementiert: Same-NY-Date-Grenze nur für die erste separate
Ausbruchsmail, Close+15-Minuten-Scheduler-Trigger/Priorität und exakte aktuelle
Session in Startup-Leaves auch bei leeren Ergebnissen. Globaler Swing-Vertrag,
Full-Trade-Gates, Gap-Früh-/Mittagstermine, Kontoeinstellungen und Installation
unverändert. 299 und danach 163 überlappende gezielte Fälle bestanden; unabhängiger
Review ohne Critical/Important-Fund. Gesamtlauf auf eingefrorenen Quellen:
12.517 passed / 97 failed / 7 skipped, exakt dieselben 97 vorhandenen Windows-/
Deployment-Fehler, keine neuen Fehler. Separater Release-Abzug: 366 passed,
keine Fehler. Kein Gesamt-PASS. Noch nicht committet/gepusht/deployt.

Frischer öffentlicher Healthcheck 12.10. ca. 07:50 UTC: healthy,
`2799972938ee`. Neue Zeitkorrektur nicht live. Maßgeblich für weitere Freigabe:
`TODO.md` und `docs/POST_CLOSE_BREAKOUT_MAIL_2026-10-12.md` (Test-XMLs, Grenzen,
Commit-/Servergates). Private Diagnose-/QA-Dateien nicht veröffentlichen; andere
offene FET/BI/Backtest- und Deployment-Aufgaben nicht mit erledigt markieren.

## Freigegebene Fortsetzung – 11.10.2026

Der Nutzer hat nach der Diagnose einen **separaten bestätigten Ausbruch-Hinweis**
ohne behauptete vollständige Handelsfreigabe genehmigt und anschließend explizit
**Beobachtungs-Mails für seinen eigenen Account aktivieren** freigegeben. Die
historische Diagnose-only-Abgrenzung darunter gilt nicht mehr für diese enge Fortsetzung.

Live-Konto: Watchlist-Mails waren AUS, sind jetzt AN; sichtbare gespeicherte Einstellung
und Erfolgsmeldung geprüft. Andere Kanäle, Swing-Modus und Signal-Mail-Schalter unverändert.
Kein Mailversand, Scan oder Serverupdate durch diese UI-Aktion.

Lokal: `api.py` implementiert separate WATCH/stocks_swing-Hinweise für Momentum
Breakout Long und Gap Momentum Long/Short. Zulassung nur bei frischem abgeschlossenem
1D-Ausbruch mit gebundener numerischer Evidenz, kausalem Stop, ACCEPT ohne Gegenbarriere
und einzigem Trade-Sperrgrund `trade_target_not_structural`. Kein geänderter Full-Trade-
Guard, kein Tracker-Eintrag, keine Entry-/Stop-/TP-/R-Werte im Hinweis. Eigene dauerhafte
Identität; keine Überschreibung normaler Trade-Cooldowns, gemeinsames bestehendes Limit.
Unklare SMTP-Annahme behält ihre Sperre; keine Warteschlangen-Wiederholung am Folgetag.

Native Momentum/Gap-Long/Gap-Short-End-to-End-Prüfung: 3 passed, simuliertes SMTP,
`output/breakout-notice-20261010-gap/qa-d88750f17f45/results.xml`.
Grenztests: zunächst 68 passed,
`output/breakout-notice-20261010-contract/qa-1dec025c0a0f/results.xml`.
Finaler kombinierter Lauf **170 passed / 0 failed / 0 skipped**, 1 bekannte AnyIO-
Warnung, 111,23 s; darin 89 Grenzfälle. XML
`output/breakout-notice-final-combined-20261011/qa-2a7b0b830707/results.xml`.
Unabhängiger finaler Review ohne offenen Befund; verdeckte Zielqualitätsfehler,
Normal-Signal-Priorität, unbekannte Zustellung nach kurzer Lease und missgebildete
Gap-Evidenz sind mit RED/GREEN-Regressionsfällen abgesichert. Vollständiger Lauf
auf unveränderten Quellen **12.404 passed / 97 failed / 7 skipped**, 1 bekannte
AnyIO-Warnung, 663,07 s. Kein Gesamtpass: exakt dieselben 97 Fehlernamen in den vier
Deploy-/Windows-Bash-Suites wie vorher, keine zusätzlichen Fehler. XML
`output/breakout-notice-frozen-full-20261011/qa-8f5b343e5834/results.xml`.
Ein früherer Lauf mit 103 Fehlern bleibt im Prüfbericht dokumentiert; veraltete
Literalprüfung repariert und korrekt auslösende Quell-Freeze-Guards auf unveränderten
Quellen wiederholt, 63 passed. Die zusätzliche Kontrolle des separat exportierten
Git-Index besteht: **180 passed / 0 failed / 0 skipped**, 1 bekannte AnyIO-Warnung,
116,91 s. XML `output/breakout-release-index-20261011/qa/qa-f8bde43536ef/results.xml`.
Veröffentlichungsinhalt gegen die echten Index-Blobs geprüft; normale Git-CRLF-
Konvertierung bei LF-Testdateien berücksichtigt. Zusätzlicher Git-Archiv-Abzug:
erneut 180 passed, 0 failed/skip, 1 bekannte AnyIO-Warnung, 97,98 s; dieselben Fälle,
keine verdoppelte Abdeckung. XML
`output/breakout-release-index-20261011/qa-raw/qa-3dce3e1e4434/results.xml`.
**Feature veröffentlicht:** `7be76b50b518359fb8cfa1d1ce66878fe5fa1685`, Commit/Push
erfolgreich. Genau sieben vorgemerkte Dateien; privates `output/` und übriges WIP
ausgenommen. **Nicht auf Hetzner installiert, keine echte Zustellung bestätigt.**
Frischer Healthcheck: healthy, `47d0b9ed93c2`, Bundle `8f8a6c0c5bae`, Serverzeit
`2026-10-10T22:38:09.186826` ohne expliziten Offset. Nächster Schritt: normaler
Pull nach Abschluss laufender Scans, Dienste neu starten, Revision prüfen und
nächste qualifizierte reale Zustellung nachweisen. Kein Deploy-Skript nötig. Details im neuen
`docs/BREAKOUT_OBSERVATION_MAIL_2026-10-11.md`.

Cup bewusst NICHT zugelassen: aktueller Finalizer macht aus Projektions-TP1 ein
REJECT/causal_structure_missing, das nicht sicher als bloß fehlender Gegenlevel gelesen
werden kann. BI/Krypto/weitere Scanner unverändert; deren TODOs nicht als erledigt abhaken.
Keine Landingpage-/Installationsmigration und kein Broad-Staging vorhandener Änderungen.

## Aktuelle Maildiagnose – 10.10.2026 nach bestätigter Anmeldung

Maßgeblich ist jetzt die erfolgreich angemeldete Live-Prüfung, nicht der historische
Login-Blocker darunter. Nutzer fragt nach der Ursache fehlender Signal-Mails; keine
neuen Produkt-/Schwellenänderungen beauftragt. Nur lesend gearbeitet, keine weitere
Testmail, kein Scanstart, Provider-Replay, Pull oder Neustart.

- Health Serverrevision `47d0b9ed93c2`, Bundle `8f8a6c0c5bae`, healthy; Serverzeit
  `2026-10-10T21:34:25.555683` ohne expliziten Offset. Publizierter Landing-Rollback
  noch nicht installiert, aber nicht die Ursache der Plan-/Mail-Sperre.
- Admin-Mailansicht **23:37:41 Zürich**: Swing-Empfänger 1 / Crypto 1;
  0 SMTP-Annahmen / 50 ausgelassen / 0 Fehler / Queue 0, im begrenzten letzten
  50-/24h-RAM-Fenster nur Krypto-Ablehnungen. Keine vollständige Versandhistorie.
- Letzter gespeicherter Auto-Sweep **05:55:04 Zürich**, abgeschlossen, 30 Kandidaten.
  Transportspalten `—` sind fehlende Eventzähler, nicht gesicherte Nullen.
  Aktuelle Aktienstrategie-Cache-Vorprüfung: 30 Kandidaten / 0 freigegeben.
- TBBB Score 92, Tagesqualität 85/96; PLTR Score 91, Tagesqualität 88/96.
  Referenzschluss 09.10.; Ausbruch bestätigt, Rücktest offen. Beide konkrete
  Mailablehnung **`trade_target_not_structural`**, nicht Score oder Rücktest.
- Builder ohne Gegenlevel: `api.py:19658` → Struktur `ACCEPT` mit
  `no_confirmed_opposing_barrier`, kausaler Stop möglich, aber Projektionsziele.
  `api.py:19753` → `PROJECTION_ONLY_NO_CONFIRMED_BARRIER`.
  Gemeinsamer Mailguard `api.py:4457` verwirft Projektions-TP1 / schwache Ziele.
  Schon seit `82f5463` (23.08.); Sept30-Commit präzisierte die Ablehnungsanzeige.
  Dies ist eine ausdrücklich kodierte Zielpolitik, kein neu belegter SMTP-Ausfall.
  Der Guard gilt ebenfalls für BI, Turtle, Cup, ORB, Bear und Biotech.
  TP2-Projektion allein ist erlaubt, solange TP1 strukturell bestätigt ist;
  kein belegtes falsches Flag auf einem echten bestätigten TP1.
- Enge frische Offline-Grenzprüfung: 8 passed, 0 failed/skip, 1 AnyIO-Warnung.
  XML `output/signal-sender-boundary-audit-20261010/qa-4d70f316e652/results.xml`.
  Separate Zielpolitikprüfung 4 passed plus 1 überlappender beobachteter Test;
  `output/signal-target-policy-audit-20261010/qa-f8a531212112/results.xml` und
  `qa-560f48d06ca2/results.xml`: realer nativer Cup-Produzent ohne Gegenlevel baut
  ACCEPT + Projektion, gemeinsamer Guard lehnt `trade_target_not_structural` ab.
  Simuliertes SMTP / künstliche Fixtures sind keine echte Zustellung.

**Nächste Entscheidung:** Soll ein bestätigter Ausbruch ohne historische Gegenbarriere
als Ausbruch-Benachrichtigung mit getrenntem Planstatus versendet werden? Nicht ungefragt
den vollständigen Entry/Stop/TP-Plan freigeben, Projektionsziele als historische Struktur
umetikettieren oder allgemeine Grenzen lockern. Vollständige originale TBBB-/PLTR-Zonen
fehlen weiterhin; kein Allzeithoch-/Providerfehler behaupten. Reguläre reale Zustellung
und historische Wochen-Ursache nicht vollständig belegt. Wochenendpause erklärt heute
keine neuen automatischen Aktienstarts, aber nicht die wochenlange Vorgeschichte.

Nur diese Dokumentation und TODO geändert; noch kein Commit/Push dieser Diagnose.
Bestehendes WIP unverändert. Die ausführliche aktuelle TODO enthält weitere offene
BI-/Report-Aufgaben; diese nicht durch die Maildiagnose als erledigt kennzeichnen.

## Neuer Auftrag – 10.10.2026, 23:10 Zürich: Landingpage-Texte zurücknehmen

Der Nutzer hat ausdrücklich die Rücknahme der eigenmächtigen Marketing-Umschreibung
verlangt. Lokal sind die Juli-/August-Texte anhand `8e28962^` / `3efcfed` wiederhergestellt.
Nur `AuthPage`-Copy und generiertes Frontend-Bundle geändert; kein neuer Wortlaut.
Login/Legal/Tarifdaten, BI-17/20, Paper-Modi, Scanner/Mail/Backtest unverändert.
Auch die alten statischen Kundenstimmen wurden textlich zurückgesetzt; ihre Echtheit
oder aktuelle Erfolgs-/Echtzeitbehauptungen sind dadurch nicht nachgewiesen.

Lokale Darstellung, Features, Pricing, FAQ und Login-Dialog überprüft.
Bundleprüfung erfolgreich: `26922d75a963`. Screenshot unter
`output/playwright/landing-rollback-20261010/landing-restored-desktop.png`.
Vollständiger isolierter QA-Lauf abgeschlossen: **12.312 bestanden / 97 fehlgeschlagen /
7 übersprungen / 1 bekannte AnyIO-Rewrite-Warnung**, 526,51 Sekunden. **Kein Gesamtpass.**
XML mit sämtlichen Fehlernamen: `output/landing-rollback-20261010/qa-f94762f5d082/results.xml`.
Alle 97 Fehler liegen in vier Deployment-Suites (Auto-Update 55, Migration 10,
Retirement 5, Security-Reaudit 27); WindowsApps-/WSL-Bash kann die Windowspfade
nicht öffnen. Die Skips betreffen Windows-Symlink-/Linux-/POSIX-Verträge.
Git-Bash-Gegenprobe des Tests `test_auto_update_probe_uses_command_scoped_safe_directory`
bleibt rot: Fake-Git-Fixture wird nicht genutzt, tatsächliches lokales HEAD statt
Testrevision. Nicht behaupten, ein Shellwechsel hätte alle Fehler beseitigt.
XML `output/landing-rollback-20261010/deployment-git-bash-targeted/qa-373d94e1a7c5/results.xml`.
Anfänglicher Sandboxlauf ohne Ausgabe und abgebrochener Git-Bash-Suiteversuch sind
keine Pässe. Deployment-/Testharness nicht im Copy-Rollback repariert.

**Veröffentlichung 10.10., 23:30 Zürich:** Nutzer hat Commit/Push ausdrücklich
beauftragt. Source-Commit `702fec7ded1c46979f25b0899422335c7b7ac652` ist gepusht;
Remote-main unmittelbar danach per `git ls-remote` identisch bestätigt.
Nur `frontend/index.html` und `frontend/app.bundle.js` (76+/76− zusammen).
Auf dem exakten isolierten Index-Snapshot `bda612d6ad135c01941d8c3a540a2aa908e1b987`
zusätzlich **1.003 passed / 0 failed / 0 skipped / 1 AnyIO-Rewrite-Warnung**,
153,34 Sekunden: alle 37 Frontend-Suites plus `test_auth_session_security.py`.
XML: `output/landing-rollback-publish-20261010-498e0cae9f44/qa-09a3e3201e0e/results.xml`.
Kein neuer Gesamtlauf; die zuvor dokumentierten Deployment-Fehler bleiben offen.
Bundle reproduziert nach Normalisierung der CRLF/LF-Zeilenenden, unabhängiger
Copy-/AST-Abgleich ohne funktionale Änderungen. Diese beiden Dokumente werden
separat veröffentlicht; sonstiges WIP nicht übernommen.

Kein Pull/Neustart/Servereingriff. Normalen Operator-Pull bereitstellen,
kein Deployskript oder Installationsumbau. Vorhandenes WIP erhalten.
Reguläre Mailzustellung weiterhin offen. Die vorgeschlagenen BI-/Report-Korrekturen
darunter sind nicht durch den Copy-Rollback freigegeben.

## Maßgeblicher Anschlussstand – 10.10.2026, 22:55 Zürich

Frisch geprüft: lokales HEAD und GitHub-main sind `47d0b9ed93c29630381ed18c82c38b0479909b1d`.
Hetzner-Health **20:49:43 UTC** ist healthy mit `47d0b9ed93c2` / Frontend `8f8a6c0c5bae`.
Der alte Hinweis „Hetzner nicht aktualisiert“ darunter ist historisch. Kein Servereingriff
dieser Runde. Vererbtes WIP erhalten, keine Landingpage-/Produktänderung.

Isolierter Mail-/Plan-Subset: **162 passed / 1 skipped / 1 AnyIO-Rewrite-Warnung**,
87,20 Sekunden; XML `output/mail-fix-qa-0cb9c86217e542958c52faa769c5518d/results.xml`.
Keine echte SMTP-/Providerverbindung. Kein Gesamtlauf; separater Agentensandboxlauf
ohne Ausgabe abgebrochen, nicht als Testpass werten. Sender-Quellprüfung findet keinen
neuen belegten falschen Blocker. Reguläre aktuelle Zustellung bleibt offen.

In-App-Seite geöffnet, Anmeldung fehlt; Nutzer hat statt Anmeldebestätigung nach
Landingpage-Änderungen und den geplanten Korrekturen gefragt. Native Browserprüfung
wegen nicht eindeutig bestimmbarer Adresse sicherheitsseitig gestoppt; diese Runde
keine weitere Browserautomation. Nur die bestehende Landingpage gelesen.

Drei begrenzte Befunde zur Nutzerentscheidung, **noch nicht umgesetzt**:

1. Wochenreport-Betreff: aktueller reiner Trackerpfad zählt denselben Fall bei ungeklärter
   Obergrenze gleichzeitig numerisch resolved und evidence-unresolved. Betreff zeigt
   1/1 für insgesamt 1, Body korrekt 0/1. Betreff aus validierten disjunkten
   `report_counts`; bekannte Teil-R-Werte nicht löschen. Testeinstieg:
   `test_weekly_report_delivery_presentation.py::test_unknown_upper_order_blocks_even_if_lower_value_is_numerical`.
2. BI-Diagnosevalidator: akzeptiert explizite 200 Ticker × 64 Testsitzungen bei nur 200
   erwarteten individuellen Sitzungen. Optionales `expected_test_sessions` muss bei
   Vorhandensein den Nenner binden; alte Diagnosen ohne Feld kompatibel erhalten.
   Testeinstieg `test_bi_backtest_diagnostics_api.py`; reine Logik
   `modules/backtest_diagnostics.py`. Kein Beleg, dass der echte Oct8-Nenner falsch war:
   Kalender bestätigt 167 Abrufsitzungen, 64 Testsitzungen, 12.800 tickerweise Sitzungen.
3. BI-699-Fehltermine: vorhandene gespeicherte Mapping
   `diagnostics.coverage.missing_sessions_by_ticker` nur in eingeklappten Prüfdetails
   sichtbar machen. Keine neue Datenabfrage, keine ungeprüfte Ursachenzuordnung.
   Reader bereits GET `/api/backtest-results` für gespeicherte Requestidentität.

Der Nutzer verlangt jetzt genaue Erklärung vor Zustimmung. Nicht so tun, als wäre
die Umsetzung freigegeben oder als erklärten diese Darstellungs-/Validatorbefunde
die fehlenden regulären Mails. Zweistündige Fortsetzung unterbrochen, nicht abgeschlossen.
Aktuelle ausführliche TODO enthält sichere nächste Schritte. Nur diese beiden
Dokumente geändert, noch nicht committet/gepusht.

## Maßgeblicher Anschlussstand – 08.10.2026, 14:59 Zürich

Der Operator hat den eng begrenzten Check ausgeführt. **Nicht erneut nach Export fragen.**
Private Datei `output/profitability/momentum-candidate-evidence-20261008T123541Z.json`
lokal gelesen, vier Kandidaten OK, Cacheversion 20 / 13 Zeilen, SHA256
`d89c928118181c208a8f51c6b61898e9d745f083d12395eee5b9686d3de0365d`.
Referenzsession 07.10., 20:00 UTC; naive Cachezeit hat keine verifizierte Zeitzone
und bindet diesen Export nicht automatisch an die zuvor beobachtete AutoSweep-Zeit.

Originale Mailablehnungen eingegrenzt:

- RELL: gespeicherter historischer 20T-Median **1.771.748,0399167603 USD**
  (beide Aliase identisch), 11,41 % unter Mailgrenze 2 Mio.; History_OK, Tagesqualität
  und Hoch/TP1-Prüfung bestehen. Aktueller/projizierter Umsatz-Minimum 750.000
  ist eine andere Statistik. Mailregel aus `99c644ce` vom 22.07.; keine neue
  alias-/einheitenbedingte Sperre. Keine eigenmächtige Senkung der Grenze.
- UVE-Zone 44,96803437823321–45,265858067507324, sechs ältere Resistance-Swings;
  Entry 45,25 innen, TP1 45,27 nur 0,03R, TP2 ausdrücklich Projektion.
- NECB-Zone 27,28886463346936–27,501135366530644, vier ältere Resistance-Swings;
  Entry 27,33 innen, TP1 27,50 nur 0,41R; Median 948.137,095038355 USD.
- GKOS finale VRVP-HVN-Zone 168,585–172,42416666666668, Entry 172,18 innen,
  TP1 172,42 nur 0,05R. Native Belege aller drei Titel (767) ohne zukünftige
  Bestätigungen. Originale OHLCV/Profilbeiträge fehlen: kein vollständiger
  unabhängiger Neubau der Historie aus dem Export behaupten.

**Neuer tatsächlicher Produktfehler:** `classify_for_trade` entfernte alle
`reclaimed`-Zonen aus Gegenbarrieren ohne ihre Richtung. GKOS-Zone
`lz_5161012c0e0e8531`, 172,55401398522704–174,22851097901938, ist SHORT-reclaimed,
bleibt aber für LONG Widerstand. Auch API-Planloop hatte einen pauschalen Label-Skip.
Lokaler Fix prüft gebundene Richtung/Geometrie/Zeit/aktive Bestätigung und lässt
RECLAIMED durch den bestehenden strengen API-Proofverifier laufen. Cache 20→21
für Wiederaufbau alter Pläne. **Keine Aufweichung der Mailregeln.**
RED 12 Fehler/4 bestanden: `output/directional-reclaim-red-20261008.xml`;
finale exakte Git-Index-Snapshot **598 bestanden / 1 Windows-Symlink-Skip**,
0 Fehler, eine bekannte AnyIO-Rewrite-Warnung. XML:
`output/release-verification-20261008-directional-10a9cf971199/qa-4eb5a9bf98bc/results.xml`.
Unabhängige Schlussprüfung ohne konkreten offenen Blocker. Zusätzlich
History-Randfall (älterer Anker nur in der anderen Richtung), exakter Cutoff
und echte Booleanflags durch Regressionen geschützt. Früherer isolierter
Zwischenlauf 268 bestanden/2 neue Assertionfehler erhalten; optionales
`warning_codes`-Feld im Test korrekt behandelt. Kein neuer Repository-Gesamtlauf.

**Produktcommit `c26aa4898f44e0af0891532f1d5ac1b1c279e2c0` ist gepusht**;
Remote anschließend exakt per `git ls-remote` bestätigt. Geprüfter Produkttree
`c2a3a86feb84b25ec92c47b7cbcd6afd55f81aea` stimmt mit Commit überein.
Frontend-Bundle unverändert korrekt `8f8a6c0c5bae`. Nur zwei Produktdateien und
vier neue Reader-/Testdateien veröffentlicht; keine privaten Exporte oder
vererbtes Deploy-/Calendar-/Commerce-/Dokumentations-WIP im Produktcommit.

**Hetzner nicht aktualisiert.** Nach Abschluss laufender Scans normalen Operator-
Pull und API/BG-Neustart anbieten; kein Deploy-Skript/Installationsumbau.
Alte Cacheversion 20 bleibt nach Update nicht mailfähig; ein neuer vollständiger
Strategielauf muss Version 21 liefern. Keine Freigabe eines alten Plans erzwingen.
Dieser Fehler übersah Hindernisse, er erklärt keine falsche Mail-Sperre dieser vier.
Reguläre Signalzustellung weiterhin offen; letzte live gelesene Health-/SMTP-Werte
stehen im historischen Abschnitt, nicht als frisch beobachtet ausgeben.

## Historischer Anschlussstand – 08.10.2026, 13:49 Zürich

Fortsetzung im vorhandenen Checkout/angemeldeten App-Tab. Git weiterhin
`8c682f4102b38fe30bfb214842f12e7d09203890`; Health **13:18:43** healthy,
Revision `8c682f4102b3`, Bundle `8f8a6c0c5bae`. Kein neuer Pull/Neustart,
Kontoeingriff, technischer Testversand oder Live-Aktien-Scan gestartet.

### Frischer Mailbefund nach Installation

Admin **13:18:31**: SMTP 0, ausgelassen 5, Versandfehler 0, Queue 0; Swing/Krypto
je ein Empfänger. AutoSweep **13:02:26 vollständig abgeschlossen, 13 Kandidaten**,
Cup 13:02:24 abgeschlossen. RELL besteht Score/Tagesqualität (91/86 von 96),
wird aber konkret wegen `momentum_mail_blocked_thin_baseline_liquidity` abgelehnt;
Rücktest fehlt lediglich als Warnung. Originaler Median-Dollarvolumen20-Wert
ist im UI nicht sichtbar. UVE 45,25 trifft eine native überlappende Swingzone
bei 45,27 (1D/1W/4H); TP1 0,03R, TP2 2,46R-Projektion. Originale Zonenbelege
fehlen. Später geladene Chartkerzen sind kein Ersatz des eingefrorenen Scanbelegs.

Lesende Source-Gegenprüfung fand hier keinen neuen belegten Schwellen-/
Alias-/Zeitrahmenfehler. Reguläre Zustellung bleibt offen, nicht als repariert
oder „alle Filter sind schuld“ behaupten. Gezielter stdlib-Collector für
RELL/UVE/NECB/GKOS lokal fertig/geprüft; keine Änderung des Senderprodukts.

**Jetzt ausstehend: ein eng begrenzter Operatorcheck**, im Chat angefragt:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projekt\TradingBot\scripts\collect_momentum_candidate_evidence.ps1"
```

Keine Provider-/Kontodaten-/SMTP-Zugriffe, Pulls, Neustarts oder Scans. Reader
wird aus dem lokalen geprüften Checkout über SSH gestreamt, nur feste API-
Prozessnamespace und vier festgelegte Kandidaten. Datei-/Prozessidentität und
Hash verhindern stilles Vermischen eines wechselnden Ergebnisses. Originale
Zahlen, Alias-Konflikte, native/VRVP-Levelbeweise bleiben erhalten; naive
Cachezeit hat ausdrücklich keine UTC-Autorität. Nur lokale private Datei
`output/profitability/momentum-candidate-evidence-*.json`, kein Upload/Commit.
Nach „fertig“ direkt diese Datei auswerten, nicht erneut Login/Gesamtexport fordern.
Tages-OHLCV liegt nicht in diesem Finalcache, Verfügbarkeit anderswo unbekannt.

Collector-QA **35 bestanden, 1 Windows-Symlink-Skip**, finale XML
`output/momentum-reader-qa-20261008-1351-r3.xml` (finale Alias-Gegenprüfungen).
Anfangs-RED 33/2/1 erhalten:
ein falscher String-Assertiontest und **echter neuer PS5-Wrapperfehler** durch
`ticker`/`Ticker`-Aliase. Case-sensitive JSON-Dictionary-Parsing repariert;
Originalbytes werden unverändert gespeichert. Unabhängige finale lesende
Quellprüfung ohne offenen Sicherheits-/Evidenzverlustblocker.
Neue Dateien `scripts/collect_momentum_candidate_evidence.py`, `.ps1` und
`test_momentum_candidate_evidence_reader.py` lokal, **nicht committet/gepusht**.

### Reales BI-Ergebnis erstmals frisch ausgeführt

BI Long, **3 Monate / 200 / Preis 5 / Volumen 200.000**, abgeschlossen **13:29:58**.
Gespeichertes Ergebnis erneut identisch geladen. 190/200 nutzbare Aktien,
9.549 berechenbare BI-Fenster → 1 Setup → 1 Plan → 1 Einstieg.
**NWSA 21.08.–03.09.2026: +3,03 %, +1,12R, TP1+EOD, Grade B**.
Keine No-Fills oder ungeklärten Trades. Das ist eine beobachtete Modellsimulation,
keine Mail/Broker-Ausführung und bei einem Trade keine allgemeine Trefferquote.
Zusätzlich Aktien-Ansicht geöffnet und Tools → Backtest erneut geöffnet:
gespeicherte Auswahl/Ergebnis **automatisch** mit identischem Datum/Trade geladen;
alter Wiederöffnen-Fehler in diesem realen Ablauf nicht mehr reproduziert.

Abdeckung PARTIAL: 167/167 Tagesabrufe erfolgreich, 0 leere/fehlgeschlagene;
12.101/12.800 individuelle Test-Sitzungen, **699 fehlend**, 95 unbrauchbare Fenster.
Funnel 9+95+2.295+2+9.549=11.950 stimmt. Fehlende tickerweise Lebenszyklusbelege
nicht als Providerfehler erfinden und später datenlose Titel nicht nachträglich
aus der vorab gewählten Kohorte entfernen. Universum-Typprüfung ist nicht mit
allen Live-Common-Stock-Gates identisch. Aktuelle Details und Restaufgaben im
neuen Anfang von `TODO.md`; historische unvollständige Studien erhalten.

Bildnachweis: `C:/Users/miros/.codex/visualizations/2026/08/11/019fef3d-c9a9-7803-ab66-6631a424c1de/bi-live-result-20261008.jpg`.
Hier wurde ein **historischer Produktions-Backtest** gestartet (Providerlesevorgänge
und Ergebnis-/Fortschrittscache); das nicht als völlig mutationsfreie Runde darstellen.
Neue Code-/Test-/Dokumentationsänderungen bisher lokal, nicht committet/gepusht.

Frische isolierte Anschluss-QA: **111 bestanden**, 0 Fehler, eine AnyIO-Warnung;
`output/mail-fix-qa-e3b9e38f5c1c47bea329317cd8e8295a/results.xml`.
Erster Teststart in der Prozess-Sandbox hing ohne Ausgabe; ausschließlich
identifizierte eigene Testprozesse beendet und denselben Offline-Launcher
unbuffered außerhalb dieser Sandbox erfolgreich wiederholt. Kein neuer
vollständiger Repositorylauf und kein neues SMTP-/Postfach-Signal bewiesen.

## Historischer Anschlussstand – 08.10.2026, 12:45 Zürich

Diese Zusammenfassung hat Vorrang vor den historischen Runden darunter.
Workspace `C:\Projekt\TradingBot`; zuerst `TODO.md`, diesen Abschnitt und
[Cup-/Mail-Prüfbericht](CUP_FINAL_PLAN_MAIL_RECOVERY_2026-10-08.md) lesen.
Checkout und vererbtes WIP erhalten; keine erneute Rekonstruktion aus der ganzen Chatgeschichte.

### Git, QA und Server getrennt bestätigt

- **HEAD und `origin/main`: `8c682f4102b38fe30bfb214842f12e7d09203890`**,
  in dieser Runde frisch per Git/Remote abgeglichen. Produktcommit
  **`06d9860d1d7fb590e633d30bea2563229ea9ee1a`**; der anschließende
  Commit ändert nur TODO, Übergabe und Cup-Prüfbericht.
- Exakter Produkttree **`237fdd8199a628d749a8c380058c4a8595ee5f90`** erneut
  abgeglichen; finale QA-XML erneut gelesen: **12.171 bestanden, 2 übersprungen,
  0 Fehler/Errors**, kein neuer Testlauf. Evidenz:
  `output/release-verification-20261008-cup-release-5bb34e78aea4/qa-57fafe9b2612/results.xml`.
  Vier separate Deploy-Testdateien ausgenommen; kein vollständig grüner WIP-Lauf.
- Öffentliche Healthprüfung **08.10.2026, 12:44:59 Zürich**:
  **healthy, `8c682f4102b3`, Bundle `8f8a6c0c5bae`**. Das Cup-Paket ist
  inzwischen auf Hetzner vorhanden. Kein erneuter Pull/API-Neustart nur wegen
  dieser Dokumentationsübergabe. Einzelne Dienstneustarts wurden nicht separat
  geprüft. Hier keine Serveränderung ausgeführt.
- **Reguläre Signal-Mail weiterhin nicht nachgewiesen.** Letzte Admin-Ansicht
  **11:55:49**: SMTP 0 / ausgelassen 8 / Fehler 0 / Queue 0; zeitlich vor dem
  jetzt bestätigten Update. In dieser Runde keine neue Versandansicht gelesen.
  Eine frühere technische Testmail kam laut Nutzer an; sie ist kein reguläres
  Handelssignal und deren einmalige Erlaubnis nicht erneut verwenden.

### In dieser Reihenfolge fortsetzen

1. Git/Health frisch lesen und vorhandenen Alpha-Station-Tab verwenden; Anmeldung
   prüfen, nicht voraussetzen oder den Nutzer erneut nach der ganzen Geschichte fragen.
   Kein redundanter Pull/Restart, kein Scanstart nur zur Statusabfrage.
2. Admin → Mailversand lesen: einen geeigneten **nach Installation abgeschlossenen**
   Lauf mit Scanner, Zeitstand und Kandidat bis Freigabe → finale Revalidierung →
   Sender → SMTP-Annahme → Nutzer-Postfach verfolgen. Liegt ein konkreter Fehler
   vor, zuerst reproduzieren, dann gezielt reparieren; keine Filter lockern, um
   eine Mail zu erzwingen. `—` bedeutet fehlende Ereigniszahl, nicht bewiesenes 0;
   mehrere Gründe einer Zusammenfassung können zu verschiedenen Kandidaten gehören.
3. Danach tatsächlichen BI-Dreimonatslauf (200 Aktien, Preis 5, Volumen 200.000),
   Wochenreport-Zustellkohorten und Originalkerzen-Levelchecks abschließen.
   Vorhandene historische Studien/Details stehen weiter unten; Null-Trades sind
   keine berechenbare Trefferquote. BPIQ-Abo abgelaufen = separates Providerproblem.

Für weitere API-QA ausschließlich `.codex_pytest_env\Scripts\python.exe`
mit `scripts/run_offline_tests.py` und isolierten Zustands-/Outputpfaden benutzen;
kein direkter API-Import mit realer `.env`, SMTP oder privaten Daten.
Protected WIP: Deploy/Install/Retirement, Calendar, Commerce und andere Handbücher;
private `output/`-Exporte nicht veröffentlichen. Kein `git add -A`, Reset/Clean,
Restore von `deploy/safe_deploy.sh` oder Installations-/Eigentumsumbau.
Kein neuer Test-/Fixtureprozess in dieser Dokumentationsrunde gestartet.
**Nur TODO und diese Übergabe aktualisiert, lokal und noch nicht committet/gepusht.**

## Aktuell: Cup-Finalplan und Versandkontext repariert und gepusht

Dieser Abschnitt ersetzt die älteren Installations-/Cup-Angaben unten.
Health inzwischen **healthy, `8c682f4102b3`, Bundle `8f8a6c0c5bae`**; siehe
frische Prüfung oben. Die vorherige History-/Turtle-Reparatur ist ebenfalls
installiert. Vor dem Cup-Update beobachteter Aktien-Sammellauf
am 08.10. 11:18:55 abgeschlossen, 13 Kandidaten; Cup 11:18:52 abgeschlossen.
Live-Mailfenster 11:55:49 SMTP 0 / ausgelassen 8 / Fehler 0 / Queue 0. Keine neue Testmail,
kein Produktionsscan oder Serverupdate hier. Reguläre Signalzustellung offen.

Der separate Cup-Fehler wurde mit echten, gemeinsamen historischen Kerzen
reproduziert und lokal repariert: finale Preise/Entscheidung/Risiko/Zonen-/
Herkunftsbelege sind einheitlich; Pattern-Stop bleibt bei VRVP erhalten;
Measured Targets sind keine Strukturautorität. Derselbe finale Receipt gilt im
Sender, Revalidator, Watch, Startup/REST und Reminder-Quellreader. Gültige negative
Kandidaten bleiben Kontext, nicht Handelssignale. Keine Schwellenlockerung.

262 breite gezielte Tests und danach 47 finale Cup-/Cache-/Watch-Tests bestanden
(überlappende Mengen). Exakter finaler Produkt-Index **12.171 bestanden,
2 übersprungen, 0 Fehler/Errors**, vier separate Deploy-Testdateien ausgenommen.
Tree **`237fdd8199a628d749a8c380058c4a8595ee5f90`**; Produktcommit
**`06d9860d1d7fb590e633d30bea2563229ea9ee1a`** gepusht und Remote-SHA bestätigt.
Nur zwölf scoped Produkt-/Testdateien; Frontend unverändert. Details:
[Cup-/Mail-Prüfbericht](CUP_FINAL_PLAN_MAIL_RECOVERY_2026-10-08.md).
Dieser Bericht, TODO und Prüfnachweis begleiten das Produkt als separater Doc-Commit.
Aktuelles HEAD/`origin/main` vor Operatorinstallation abgleichen; keine
nachfolgenden Dokumentations-SHAs als ungeprüfte Codeänderung verwechseln.
Geschütztes Deploy-/Calendar-/Commerce-/Dokumentations-WIP nicht mitveröffentlichen.
Keine alten unbekannten Daten als Freigabe-/Postfachevidenz umdeuten.

## Historische Reparaturrunde: Historienabbrüche vor dem Sender

Dieser Abschnitt ersetzt die frühere Installationsangabe unten. Live ist bereits
**`df04ee0813bb` / `8f8a6c0c5bae` / healthy**. Das Diagnosepaket wurde installiert.
Aktueller Cup-Versuch scheitert in der Historienphase (`scan_data_unavailable`),
nicht in einem belegten SMTP-Aufruf. Sein konkreter Providerfehler ist unbekannt;
SSH ist ohne interaktive Anmeldung nicht zugänglich.

Danach wurden tatsächliche Turtle-Datenabbrüche sowie fehlende begrenzte
Wiederholung transienter Strict-History-GETs reproduziert und repariert.
[Aktueller Prüfbericht und Grenzen](STOCK_HISTORY_MAIL_RECOVERY_2026-10-08.md).
46 neue Fälle, 414 gezielte bestehende Prüfungen und danach 109 Sender-/History-
Prüfungen bestanden; diese Mengen überlappen, nicht addieren. Die vier neuen
StrictFetcher-End-to-End-Fälle verwenden tatsächliche Plan-/Mailguards und
simuliertes SMTP, kein Nachweis echter Postfachzustellung. Der exakte
Produkt-Index-Snapshot bestand **12.124 Tests, 2 übersprungen, 0 Fehler/Errors**;
nur die vier bekannten separaten Deploy-Testdateien ausgenommen.
Produktcommit **`352ea08b07ca82164eec353b064c85fa4c2133e6`** wurde gepusht und
`origin/main` per SHA bestätigt. Freigabegrenzen,
unabhängige Barrieren, Kontoeinstellungen und Server unverändert.

Nächste Schritte/Veröffentlichungsstand immer am aktuellen Anfang von `TODO.md`
prüfen. Separater Cup-Planmetadaten-Kohärenzfehler ist in der neueren Runde oben
repariert; Barrieren bleiben unverändert. Keine weitere
Testmail oder wiederholte Exporte auf Verdacht. Vererbtes WIP bleibt geschützt.

## Historischer Einstieg und Auftrag der Diagnose-Runde

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

## Historisch bestätigter Stand der Diagnose-Runde

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

## Historischer Livebefund der Diagnose-Runde – nur dieses Zeitfenster

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

## Historische Cup-Runde: Installation bestätigt, Signalkette damals noch offen

Der frühere Installationsauftrag ist durch Health `8c682f4102b3` inzwischen
überholt. Private `output/`-Artefakte und Deployänderungen blieben ausgeschlossen.
Damals war kein weiterer Pull/Restart nötig. Das gilt **nicht** für den neuen
Richtungsfix `c26aa48`; dessen Operatorupdate ist im aktuellen Anfang aufgeführt.
Keine Installationsumstellung beginnen. Maßgeblicher Stand und Reihenfolge stehen oben.

Passenden aktuellen Lauf mit Revision/Identität/Zeitstand erfassen:
Scannerabschluss → Freigabe → finale Revalidierung → Sender → SMTP → Postfach.
Diese Nachweise fehlen noch. Aktuelle Vorab-Ablehnungen erklären nicht allein
die gesamte wochenlange Versandgeschichte. Keine neue Sender-/Filterreparatur
ohne konkreten Fehlerreproduzierer und keine profitable Strategie daraus behaupten.

## Historische Restaufgaben der Cup-Runde – aktueller Status steht oben

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

## Historische Diagnose-QA, Arbeitskopie und sichere Weiterarbeit

Die folgenden Zahlen gehören zu den früheren Diagnose-/Release-Runden.
Aktuelle Produkt-QA und Veröffentlichung stehen am Anfang dieser Übergabe.
Damals veröffentlichter Release-Snapshot vom 07.10.: **11.569 bestanden,
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
> Produktcommit `c26aa4898f44e0af0891532f1d5ac1b1c279e2c0` gepusht und remote
> bestätigt; späterer Dokumentations-HEAD separat per Git prüfen. Produkttree
> `c2a3a86feb84b25ec92c47b7cbcd6afd55f81aea`: **598 gezielte Tests bestanden,
> 1 Windows-Skip**, kein neuer Gesamtlauf. Richtungsgebundene Zonenprüfung und
> API-Certificates repariert, Stockcache 20→21; Mailgrenzen unverändert.
> Letzte live gelesene Health 08.10.13:18:43: healthy `8c682f4102b3`, Bundle
> `8f8a6c0c5bae`; Admin 13:18:31 SMTP 0 / ausgelassen 5 / Fehler 0 / Queue 0.
> Das sind historische Beobachtungen, keine frische Zustellbestätigung. Server
> hier nicht aktualisiert: nach laufenden Scans normalen autorisierten Operator-
> Pull/Restart anbieten, kein Deploy-Skript. Neue vollständige Version-21-Pläne
> und reguläre Signalkette bis finale Prüfung → Sender → SMTP → Postfach prüfen.
> Vier Originalbelege aus dem bereits erhaltenen privaten Momentum-Export sind
> ausgewertet; RELL unter 2-Mio.-Median-Mailgrenze, UVE/NECB/GKOS nahe Zonen.
> Kein erneuter identischer Export, keine Testmail oder ungefragte Schwellenänderung.
> Realer BI-Backtest 13:29:58 und automatisches Wiederöffnen bestätigt:
> NWSA +3,03 % / +1,12R, ein Trade; PARTIAL-Abdeckung 699 Sitzungen ungeklärt.
> Danach tickerweise Lebenszyklen, Wochenreport und fehlende Originalkerzenbelege
> abschließen. Fremdes WIP und private Dateien erhalten; Release- und Livezustand
> getrennt halten. TODO und Übergabe werden mit dieser Dokumentationsrunde gespeichert.
> Nach jedem abgeschlossenen Block TODO mit echter QA und Veröffentlichungs-
> sowie Livezustand aktualisieren.
