# Scanner-Tiefenprüfung und historische Stichproben – 02.10.2026

## Nachtrag: Crypto-Long-Mailanbindung nach Betreiberupdate

Dieser Nachtrag ersetzt die älteren Hinweise auf den ausstehenden Pull des
ersten Pakets. `/api/health` am02.10.10:53:47 UTC bestätigt Hetzner
**healthy / `7f6981f8e4f8` / Frontend `6a488c9c8f1a`**. Die folgenden neuen
Reparaturen sind separat; hier wurde kein Server verändert. Die technische
Testmail kam laut Betreiber an. Keine weitere Mail, kein Scanstart, keine
Abo-/Schalteränderung durch Codex.

### Aktuelle Befunde

- Signal-Mails aktiv, alle Scannerkanäle AN, Mailmodus Swing. Separat
  ausgeschaltete Watchlist-Mails nicht aktiviert.
- Momentum-Snapshot02.10.10:48:55 UTC:12.582/12.582 geprüft,32 Kandidaten,
  höchster Trade-Score78 bei Mailminimum80. Heutiges Versandjournal nennt
  Ablehnungen vor SMTP durch Score/Tagesqualität/TP1-Nähe/20T-Liquidität.
- Cup ist kein neuer Nullscan:5.712/12.582, `scan_data_unavailable`, Phase
  `history`,507s. Innerer Providergrund weiterhin offen; gezielter vorhandener
  Lesetest `scripts/probe_stock_attempt_errors.py` angefragt, kein Gesamtexport.
- Admin-Snapshot02.10.13:18:02 MESZ:1 Swing-Empfänger,0 SMTP-Annahmen,
  6 ausgelassene Entscheidungen,0 Versandfehler,0 Queue. Begrenztes
  Prozessfenster, keine vollständige Inboxhistorie. Späterer automatischer
  Crypto-Long-Retry lief weiter; noch kein tatsächlicher Signalzustellnachweis.

### Reproduzierte Fehler und Korrekturen

1. Erfolgreiche native Crypto-Long-Läufe speicherten Ergebnisse ohne Mailaufruf.
   Worker und Combined-Ansicht ersetzten das nicht. Neuer Dispatcher gehört
   ausschließlich zum frischen vollständig abgeschlossenen `crypto_explosion`.
2. Rollierende24h-Hochs ohne bestätigte Zone wurden als strukturell bestätigt
   markiert. Das sperrte auch nachfolgend wirklich bestätigte VRVP-Ziele. Jetzt
   ist der Skalar nur unbelegte Watchreferenz; echte Profile/Zonen bestätigen
   weiterhin allein den Strukturplan. Ohne echte Gegenbarriere bleibt Watch.
3. Crypto-Strategy verwendete in Trade- und beiden Regime-Watch-Zweigen den
   Aktienkanal. Routing bleibt jetzt im Crypto-Kanal. Der aktuelle manuelle
   Watch-only-Producer bleibt dennoch Trade-Mail-deaktiviert.
4. Admin-Cachevorprüfung und Empfängerzählung enthalten jetzt Crypto Long.
   Gleicher Originalquellenvertrag wie der Sender, keine Quotes/SMTP während
   Diagnose. Zehn Gründe konsistent in API, anonymer SQLite-Telemetrie und
   privatem Evidence-Collector.
5. Combined erklärte0 Longs/Shorts pauschal mit Marktlage oder fehlerfreier
   Quelle. Diese Erklärblöcke entfallen; Richtungszähler und tatsächlicher
   Scannerstatus bleiben maßgeblich.

Grade/Score, Struktur, Funding/Spread und Risikogrenzen bleiben unverändert.
Vor jeder Mail: native Venue/Contract, echter5m-Schlusszeitpunkt und BTC-
Frische; keine Run-Zeit als Ersatzbeobachtung. Finale Ask-Quote und lückenloser
1m-Pfad prüfen Stop-/TP1-Berührung, Tiefe, R:R und Einstiegsabstand. Kein
CoinGecko-Ersatz, kein Versand aus altem/partiellem Cache oder Combined-Merge.
Durable Intent/Receipt und Lease/Dedupe verhindern Doppelversand. SMTP-
Ablehnung ändert keinen erfolgreichen Scan in einen Datenfehler; unklare
DATA-Annahme bleibt ohne automatischen Replay.

### Verifikation und produktive Grenze

348 überlappende gezielte Tests bestanden; abschließend51 Dispatcher-Tests.
Positiv: tatsächlicher Scorer → echte VRVP/Health → finale Quote/Pfad → echter
Sender mit Mock-SMTP und realem temporärem Annahmejournal. Ohne bestätigte
Gegenbarriere bleibt der echte Breakout Watch. Unabhängige Kanal-/Register-/
Anonymitätstests grün; frische unabhängige Abschlussprüfung96/96 bestanden.
Die tatsächliche JSX-Komponente ist für beide Richtungen mit Quellenfehler
offline geprüft; keine neue Desktop-/Mobil-Browserabnahme behauptet.
Frontend gebaut, Sourcehash/Syntax geprüft: Bundle `806260a08809`.

Eingefrorener veröffentlichbarer Index-Gesamtlauf:
**10.842 bestanden, 1 übersprungen, 0 Fehler**,479,69s;
`tmp/qa-77f0423f384f/results.xml`. Quellpaket:
`tmp/native-mail-publish-aafb744c1e94/source`. Der Skip betrifft POSIX-
Dateirechte auf Windows; `test_deploy*.py` ausdrücklich ausgeschlossen.
Fokusgruppen überlappen und werden nicht addiert. Offline-QA sperrt externe
Anbieter/SMTP und liest keine lokalen Secrets. Danach ausschließlich
Nachtrag-Dokumentation aktualisiert; Produktions-/Testquellen unverändert.

### Veröffentlichung dieses Nachtrags

Geprüft, committet und gepusht:
**`d2c14b1329aeb4099fc454c4ac9d9a0b0d6c9b72`**. `git ls-remote origin
refs/heads/main` bestätigt exakt diesen Commit. Zwölf freigegebene Pfade,
keine privaten Exporte/Secrets und keine geerbten Deploy-/Handbuchänderungen.
Dieser anschließende Abschlussnachtrag ändert ausschließlich Dokumentation.
Hetzner wurde von Codex nicht aktualisiert; nach normalem Betreiber-Pull muss
das Bundle `806260a08809` erscheinen. Kein Deploy-Skript oder Installationsumbau.

Die historischen Dreimonatsproben unten bleiben an ihre damaligen Source-
Fingerprints des ersten Pakets gebunden. Sie wurden nicht rückwirkend zu
ausgeführten Trades oder einer neuen Gewinnquote erklärt. Echte vollständige
Serverläufe, Handelssignalannahme und Inbox-Eingang bleiben separat zu prüfen.

## Historischer Abschlussstand des ersten Pakets

Die unterbrochene Übergabe wurde am tatsächlichen Checkout fortgesetzt, nicht
anhand alter Chat-Zusagen als erledigt übernommen. Ausgangspunkt ist
`796f8211a5692bb6cc98fb13075827926d30351d`, Branch `main`.
Geerbte Änderungen einschließlich der Entfernung von `deploy/safe_deploy.sh`
bleiben erhalten. Kein Ersatz-Deployer oder Installationsumbau.

**Geprüft, committet und gepusht: `93428a4998b0027740bea1f9c9df188f6743663f`.
Remote `origin/main` separat bestätigt. Auf Hetzner noch nicht aktiviert.**
Kein produktiver Scanstart, Neustart, Handel, erneuter Testmailversand
oder Eingriff in Provider-Abos. Der Betreiber hat den Eingang der einmalig
autorisierten technischen Testmail vom 01.10. bestätigt.

Der Auftrag wurde parallel nach Aktien-/Struktur-, BI/Biotech/Penny-, Krypto-,
Forschungsquellen- und UI-Pfaden geprüft. Das tatsächliche Inventar ist die
Grundlage: 14 öffentliche Aktienstrategien, 11 manuelle Krypto-Profile sowie
dedizierte Scanner, Kontext- und Überwachungsjobs. Konfigurierte, aber nicht
implementierte Märkte und reine Kontextjobs werden nicht zu Handelsscannern
umetikettiert.

## Konkrete nachgewiesene Reparaturen

### Aktien, gemeinsame Level und technische Strukturen

- Splitbereinigte Quellen, feste timezone-aware Uhr, echte abgeschlossene
  Regular-Session-Slots, Feiertage und Frühschluss: offene, zukünftige,
  widersprüchliche oder unvollständige 30m-Buckets bestätigen kein 4H-Muster.
  Anbieterfehler mit HTTP200 werden nicht als brauchbare Historie gecacht.
- Preis-, ATR-, Bewegungs-, Schlusskurslage-, Wick- und RVOL-Grenzen verwenden
  ungerundete Entscheidungswerte. Anzeigeformatierung ist kein Handelstick;
  Long-/Short-Orderlevel werden anschließend gerichtet quantisiert.
- Transitiv überlappende Unterstützungsbänder dürfen keine riesige Zone bilden.
  Alle Bänder eines Clusters benötigen einen gemeinsamen Schnitt. Echte breite
  Einzelstrukturen bleiben erlaubt; keine willkürliche Prozentbegrenzung.
- Reale MSFT-Gegenprobe am 30.07.: identische 753 Tages-/40 abgeschlossene
  4H-Kerzen. Stop nach Reparatur **443,94 statt 370,16**; Entry 451,10 und erste
  Gegenbarriere 452,53 unverändert. Der Plan bleibt **WAIT**, nicht künstlich
  freigegeben. Modelle `causal_level_zones_v2` / `directional_level_zones_v2`.
- SMC/FVG/Orderblocks/Liquidity: Rohkanten, Bestätigung, eingefrorene
  ATR-Geometrie, Auffüllung und Invalidierung bleiben kausal. Ungültige Pools
  kehren nicht ohne neue bestätigte Berührungen zurück. BI verwendet dieselbe
  echte Struktur, nicht deren gerundetes Anzeigelabel.
- Harmonic benötigt alle Pflichtverhältnisse und rechtsseitig abgeschlossene
  Bestätigungsbars für den letzten D-Pivot. Bull/Bear haben getrennte Richtungen.
- Bear benötigt tatsächlich 60 vorherige Sitzungen; der Abruf berücksichtigt
  Signalkerze und Quellvertrag. Native v2-Gegenbarrieren werden nicht durch
  künstliche R-Ziele ersetzt. Inverse ETF-Historien bleiben strikt datiert,
  adjustiert und zum festgelegten Zeitpunkt verfügbar.
- ORB Long/Short, Turtle, Cup, Flags, MA-Bounce, Wyckoff und Elliott wurden über
  ihre tatsächlichen Producer-/Plan-/Cache-/Sendergrenzen gegengetestet.
  Elliott ist Musterkontext, keine automatische Handelsfreigabe.

### Legacy-Aufrufe und manuelle Strategien

- Volume Void Long/Short und High Volume Churn laufen über ihren eigenen
  Strategie-, Status- und Cachepfad, nicht über Volume Spikes.
- Neue manuelle Status-/Cache-/Datenquellenregistrierung erfolgt zusammen unter
  `_scan_lock`; keine halb registrierte Strategie oder globale Testverschmutzung.
- Dip Buy bleibt **Long** trotz negativer Tagesbewegung. RVOL wird weder auf50
  gekappt noch vor Min-/Max-Prüfungen gerundet.
- Insider ohne Form-4-Quelle sowie entfernte Wick-/All-Harmonic-Aufrufe antworten
  ausdrücklich `501 / stock_strategy_not_implemented`, bevor Daten oder Worker
  angefasst werden. Alte Zeilen erzeugen keine Signal-Mail oder neue Reminder.
  Der kombinierte All-Harmonic-Pfad hatte eine bärische Struktur als Long behandelt.
- Penny Rockets ist ein Alias des bestehenden Penny-Scanners, kein zweiter
  unabhängiger Scanner. Futures/Forex/International sind nicht implementiert.

### BI, Biotech und Penny

- BI-RVOL und Strukturgrenzen bleiben roh; künstliche Liquiditätsboni durch
  gerundete Werte wurden korrigiert. Alle20 Faktoren müssen verfügbar sein;
  **17/20 bleibt unverändert**. Der aktuelle Vertrag ist `stock-bi-20-v8`.
- Biotech Full/Quick teilen Negations-, Ergebnis- und Risikosemantik. CRL,
  abgelehnte Zulassung und verfehlte Endpunkte werden nicht positiv interpretiert.
  Frisch fehlende Kalender-/Katalysatordaten löschen alte positive Angaben.
- Biotech wählt zunächst die abgeschlossene Sitzung und prüft anschließend
  deren echte OHLCV. Eine ungenutzte offene Bar ist kein Fehler der gültigen
  Tageshistorie. `biotech_completed_bar_v3` kennzeichnet den neuen Vertrag.
- Penny verwendet echte datierte Daily-/5m-Quellen; keine ersetzten Preise,
  Zukunftslevel oder herausgefilterten widersprüchlichen geschlossenen Bars.
  Bekannte Null und unbekannte Daten bleiben getrennt.
- Das abgelaufene BPIQ-Abo bleibt ein eigener externer Berechtigungsfehler.
  Andere Scanner, technische Mailzustellung und deren Freigaben sind unabhängig.

### Krypto, Listing- und Versandlebenszyklus

- Gemessene Closed-Candle-/BTC-Zeitfenster, Dauer, Aktualität und mathematische
  Vergleichswerte werden konsistent geprüft. Fehlender BTC-Kontext wird nicht
  als neutraler oder Short-freigebender Kontext ersetzt.
- Preis-, Range-, Umsatz-, MarketCap- und Quote-Eingaben: keine Bool-/NaN-/Inf-
  Werte, erfundene Schlusskursposition oder gerundete Schwellenfreigabe.
  Crypto.com tatsächlich bekanntes Nullvolumen bleibt Null; fehlender Proxy
  ist keine Ausführungsevidenz. Native Kontraktgröße und Bookgeometrie bleiben Pflicht.
- New Listing verwendet tatsächlich geschlossene ATH-/Triggerkerzen und einen
  festen Zeitstand. Eindeutige SMTP-Ablehnung darf erneut geprüft werden;
  Lease oder bloßer Versuch sind keine Annahme. Episodenvertrag v4.
- Cup-Watch: ein alter30-Tage-Cooldown war fälschlich als erfolgter Versand
  behandelt worden. Nur eine gültige frische SMTP-Annahme ist ein Abschlussbeleg.
  Generationen sind strikt ganzzahlig; alte Claims löschen keine erneuerte Watch.
  Ablaufgrenze folgt tatsächlicher Handelssitzung einschließlich Frühschluss.
- Neue Unterdrückungsgründe sind in Maildiagnose und datensparsamem Collector
  konsistent registriert. Keine Erweiterung auf persönliche Kurs-/Empfängerinhalte.

### Scan-Anzeige

- Bewegliche Kalender-/Nächster-Lauf-Prognosen ändern nicht die Ergebnisrevision.
  Tatsächlicher Lauf-, Kontroll-, Fehler- oder Ergebniswechsel tut dies weiterhin.
- Ein langsamer Hintergrundabruf lässt vorhandene Ergebnisse stehen, statt
  dauernd „Ergebnisse werden geladen“ über bereits bestätigten Zeilen zu zeigen.
- „Ergebnisstand“ bezieht sich auf den dargestellten Snapshot. Owner-Abschluss,
  fremder Lauf, Teilresultat und Zukunfts-/ungültige Zeitstempel bleiben getrennt.
- Tatsächliches gebautes Bundle im lokalen Browser geprüft:32 synthetische
  Zeilen, Kalenderänderung ohne zweiten Ergebnisabruf; zwei8-Sekunden-
  Hintergrundabrufe ohne Ladebanner oder Zeilenverlust. Kein Browser-Konsolenfehler.
  Der Testserver blockierte POSTs und hatte keine externen Provider-/SMTP-Zugriffe.

## Abschließende Verifikation

Eingefrorener Offline-Gesamtlauf mit isolierten DB-/Cache-/Dateipfaden und
gesperrtem tatsächlichem SMTP/Provider-I/O:

```powershell
$env:ALPHA_QA_OUTPUT_ROOT='C:\Projekt\TradingBot\tmp'
.\.codex_pytest_env\Scripts\python.exe scripts/run_offline_tests.py -q --tb=short --durations=10 --ignore-glob=test_deploy*.py
```

**10.773 bestanden, 1 übersprungen, 0 Fehler, 415,52 Sekunden.**
Privates JUnit: `tmp/qa-b4e0cda07cd9/results.xml` (10.774 Fälle).
Der damalige private Launcher `tmp/offline_mail_fix_tests_20260925.py` wird
für die Veröffentlichung als `scripts/run_offline_tests.py` mit gleicher
Isolation mitgeliefert. Der exakte vorgesehene Commit-Inhalt wird separat
aus dem Git-Index exportiert und damit nochmals vollständig geprüft.
Dieser separate Lauf ist abgeschlossen: **10.777 bestanden, 1 übersprungen,
0 Fehler, 425,95 Sekunden**; `tmp/qa-2c7fae09a9bc/results.xml`.
Geprüft wurde der explizite93-Dateien-Index ohne geerbtes Deploy-/Handbuch-WIP.
Die vier zusätzlichen Fälle stammen aus den unveränderten veröffentlichten
Kalender-/Commerce-Tests statt deren geerbten lokalen Änderungen. Danach wurden
nur zwei leere EOF-Zeilen und Dokumentation bereinigt, kein Produktivcode geändert.
Der Skip betrifft ausschließlich POSIX-Dateirechte auf Windows.
Bestehender `anyio`-Pytest-Rewritehinweis, kein neuer Laufzeitfehler.
`test_deploy*.py` ist ausdrücklich ausgeschlossen: kein behaupteter
Linux-Installer-/Migrationsnachweis. Zwei reine Entfernungs-/Installer-Guard-
Kontrollen separat bestanden (`tmp/qa-1ed7a76b2832/results.xml`).

Unabhängige fokussierte Nachprüfungen sind überlappende Gruppen, nicht additive
Testzahlen: Aktien359; BI/Penny/Biotech1074; Research114; neue Frontendgruppe342;
abschließender Cup/Hidden/Krypto-Lifecycle188. Neue Gegenbeispiele waren vor
den zugehörigen Reparaturen rot; Positivkontrollen verhindern pauschales Abschalten.

Frontend aufgebaut und Source-Zuordnung/Syntax geprüft; Bundlehash
`6a488c9c8f1a`. API-SHA256
`1118c313be477386fade5e10fcd1ffd0123c95e44df4cd7abea022ade7fa095f`.
Python-Kompilierung und repository-normalisierte CRLF-/Whitespace-Prüfung grün.
Die fünf fertigen Forschungsberichte haben dieselben88 Produktions-/Research-
Quellfingerprints. Diese wurden vor Producerimport gebunden und vor/nach Replay
sowie unabhängig mit dem aktuellen Checkout abgeglichen.

## Drei Monate echte Quellen, feste Stichproben

Fenster **02.07.–01.10.2026**, Ende02.10.exklusiv;64 US-Sitzungen.
Vorab fixierte Assets: AAPL/MSFT/NVDA; AMGN/GILD/REGN; SIRI/OPEN/BBAI;
BTCUSDT/ETHUSDT/SOLUSDT (Spot). Erste drei chronologische Beobachtungen,
nicht nachträglich ausgewählte Gewinner.

18 tatsächlich erhobene, gehashte Quelldateien; lange Daily-Warmups, vollständige
5m-/30m-RTH-Slots der festen Aktienkohorte und87.264 Spot-Kryptokerzen.
Die damalige Verfügbarkeit wird mit Datum/Uhr geprüft; keine spätere Kerze
bestätigt rückwirkend das Setup.

- Öffentliche Aktienstrategien:100 technische Kandidaten ohne Elliott;
  98 mit nativen Leveln, keiner im Sample kandidatenseitig freigegeben.
  Elliott192 Musterkontexte, keine192 Trades.
- BI384 Asset-/Richtungsprüfungen, alle20 Faktoren verfügbar; maximal13/20,
  kein17/20-Signal. Das ist kein Nullbefund für das gesamte Aktienuniversum.
- ORB3Long-/2Short-Teilbefunde, Bear7 Discovery-Beobachtungen. Biotech/Penny
  getrennte technische Teilprüfungen ohne erfundene Nachrichten-/Ausführungsdaten.
- Krypto-Ausführungskern: Referenzkurs nach24h höher bei BTC69/125(55,2%),
  ETH63/99(63,6%), SOL61/112(54,5%). Das ist die Richtungsdiagnose des echten
  Candlekerns, **keine** Netto-Handelsgewinnquote des vollständigen Scanners.

Der Null-Gatebefund wurde zusätzlich plausibilisiert: Der ursprüngliche
Produktionsscorer berechnet die technischen Setup-Scores (16–83), nicht ein
Research-Ersatz. 79 Kandidaten verfehlen die Gradegrenze, 95 den separaten
Trade-Score; die Gründe überlappen. MSFT am25.09. mit Setup-Score83 scheitert
zusätzlich an der nahen Gegenbarriere/Trade-Health. Fehlende Fundamentaldaten
werden nicht als künstlicher negativer Score verwendet. Leerer historischer
Marktkontext ist ebenfalls kein behaupteter negativer Regimebefund. Trotzdem
ist das keine Nachbildung damaliger vollständiger News-/Mail-/Ausführungszustände.

Die je Scanner aufgeführten5-Session-/24h-Verläufe sind keine Stop-/TP-Fills.
Ohne freigegebene gefüllte Pläne ist die Handelsgewinnquote **nicht berechenbar**,
nicht0%. Nachrichten, native Perp-Books/Funding/OI, damalige MarketCap-/Universums-
Snapshots, tatsächliche Empfänger/Orderzustände und alte Listingzeitpunkte fehlen
für verschiedene Vollscanner. Diese fehlenden Daten werden nicht als Verluste,
Neutralwerte oder ausgeführte Trades erfunden. Heutige adjustierte Anbieterhistorie
ist kein unverändertes damaliges Point-in-Time-Archiv; die feste kleine Auswahl
ist nicht zufällig und nicht das damalige Gesamtuniversum.

Privater lesbarer Gesamtbericht mit Tabellen, Daten, Kursen und Quelle je Fall:
`output/scanner-deep-audit-20261002/HISTORICAL_SAMPLE_REPORT.md`.
Finale Rohberichte heißen ausschließlich `history-*-verified.json`; ältere
`release`-/`final`-/Probe-Dateien bleiben ihrem früheren Stand zugeordnet.
292 alte Trackerzeilen wurden separat ausgewertet; Export endet26.09., nicht01.10.
Fehlende Herkunft/Ticker und damalige andere Verträge erlauben keine Quote des
heutigen Pakets.

## Frühere Livekontrolle vor dem Betreiberupdate

Letzte rein lesende Liveprüfung am02.10.: API `healthy`, Revision
`796f8211a569`, Frontend `41f168c9f109` – also **nicht** die heutigen lokalen
Reparaturen. Erneute Admin-Mailkontrolle11:54:18 MESZ:1 Swing-Empfänger,37 ausgelassene
Entscheidungen,0 SMTP-Annahmen,0 Versandfehler,0 Warteschlange. Das begrenzte
Fenster umfasst maximal50 Ereignisse seit API-Start/höchstens24h, nicht das
gesamte Postfach oder eine vollständige Versandhistorie.

Die fehlenden Handelssignal-Mails werden in diesem Ausschnitt **vor SMTP**
ausgeschlossen. Der bestätigte Testmail-Empfang belegt den Transport; ein
aktuelles gültiges reales Handelssignal bis Posteingang bleibt nachzuweisen.
Kein Signal fingieren, Empfängerkanal oder BI-/Grade-/Risikogrenzen lockern.
Die letzte Momentum-Entscheidung11:51:54 nennt Score, Tageskerzenqualität,
TP1-Nähe und normale20T-Dollar-Liquidität, ausdrücklich nicht fehlenden Rücktest.
Die neue Strategie-Runde ist als abgeschlossen sichtbar; mindestens ein
Krypto-Worker lief während dieser rein lesenden Kontrolle weiter. Kein Restart
ausgeführt. SSH-Batchzugriff mit unverändertem Hostkey scheitert an Anmeldung.

Die konkreten inneren Fehlergründe der produktiven Momentum-/Cup-/Turtle-
Abbrüche vom01.10. sind weiterhin nicht erhalten. Der vorhandene gezielte
Lesetest ist fertig; direkter SSH-Zugang steht hier nicht zur Verfügung.
Die lokalen Quell-/Präzisionsreparaturen sind kein Beweis des damaligen Subcodes.
Originalkerzen der alten achtVIAV-Strukturen und manuell gezeichnetenAST-Linien
sind ebenfalls weiterhin nicht vollständig vorhanden.

Die Veröffentlichungsfreigabe liegt vor. Scoped Änderungsprüfung,
Index-Gesamtlauf, Commit und Push sind abgeschlossen; der Betreiber-Pull ohne
Deploy-Skript steht noch aus. Private Quellen/Exporte und geerbtes Deploy-/Handbuch-WIP
sind nicht Teil des93-Dateien-Pakets. Keine neuen Pakete oder Reminder-Migration
erforderlich. Nach dem Betreiberupdate bleiben vollständige neue Läufe unter
den neuen Cacheverträgen und getrennte reale Signal-/SMTP-/Postfachabnahme nötig.
Laufende Scanner und SMTP-Versand vorher abschließen lassen: Pause ist kein
persistenter Restart-Checkpoint. Keine Unit-/Eigentums-/Installationsumstellung.

Normaler manueller Serverweg nach Abschluss aktiver Scans/SMTP-Vorgänge:

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Health soll `healthy` und Frontend-Source `6a488c9c8f1a` zeigen. Die Git-Revision
ist der neueste `origin/main`-Stand einschließlich der Abschlussdokumentation;
der Quellcommit ist oben eindeutig angegeben. Kein Reset bei Serveränderungen,
keine Cache-/DB-Löschung und keine erneute unautorisierte Testmail.

Offizielle Daten-/Methodenreferenzen:
[Massive/Polygon Aggregatefelder und Adjustierung](https://massive.com/docs/rest/stocks/aggregates/custom-bars?auth=login),
[Binance Spot-Klinevertrag](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md?plain=1),
[NIST Binomial-Konfidenzintervalle](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
