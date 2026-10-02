# Scanner-Tiefenprüfung und historische Stichproben – 02.10.2026

## Maßgeblicher Abschlussstand

Die unterbrochene Übergabe wurde am tatsächlichen Checkout fortgesetzt, nicht
anhand alter Chat-Zusagen als erledigt übernommen. Ausgangspunkt ist
`796f8211a5692bb6cc98fb13075827926d30351d`, Branch `main`.
Geerbte Änderungen einschließlich der Entfernung von `deploy/safe_deploy.sh`
bleiben erhalten. Kein Ersatz-Deployer oder Installationsumbau.

**Lokal geprüft und repariert; noch nicht committet, gepusht oder auf Hetzner
aktiviert.** Kein produktiver Scanstart, Neustart, Handel, erneuter Testmailversand
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

## Livebetrieb und noch offene Abnahme

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

Die Veröffentlichungsfreigabe liegt nun vor. Scoped Änderungsprüfung und
Index-Gesamtlauf sind abgeschlossen; Commit/Push und danach Betreiber-Pull ohne
Deploy-Skript folgen. Private Quellen/Exporte und geerbtes Deploy-/Handbuch-WIP
sind nicht Teil des93-Dateien-Pakets. Keine neuen Pakete oder Reminder-Migration
erforderlich. Nach dem Betreiberupdate bleiben vollständige neue Läufe unter
den neuen Cacheverträgen und getrennte reale Signal-/SMTP-/Postfachabnahme nötig.
Laufende Scanner und SMTP-Versand vorher abschließen lassen: Pause ist kein
persistenter Restart-Checkpoint. Keine Unit-/Eigentums-/Installationsumstellung.

Offizielle Daten-/Methodenreferenzen:
[Massive/Polygon Aggregatefelder und Adjustierung](https://massive.com/docs/rest/stocks/aggregates/custom-bars?auth=login),
[Binance Spot-Klinevertrag](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md?plain=1),
[NIST Binomial-Konfidenzintervalle](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
