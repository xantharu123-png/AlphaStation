# Profitabilitaets-Pruefprotokoll – 08.09.2026

Status: Schritt 1 lokal umgesetzt und geprueft; kein Profitabilitaetsnachweis, keine Handelsfreigabe.

## Auftrag und offene Entscheidung

Gewuenscht sind bis zu drei Trades und 150 USD Nettogewinn pro Tag. Drei Trades sind eine Obergrenze, keine Pflicht und keine Zahl garantierter Gewinner. Die genannte Tagesverlusttoleranz von 300 USD ist weder ein freigegebenes Risikobudget noch eine garantierte Verlustgrenze.

**Kapitalangabe `5000k`: UNGEKLAERT.** Vor Positionsgroessen, Risikoparametern oder einer gespeicherten Konfiguration sind Betrag und Kontowaehrung ausdruecklich zu bestaetigen.

| Rein hypothetisches Kapital | 150 USD relativ zum Kapital | 300 USD relativ zum Kapital |
| --- | ---: | ---: |
| 5.000 USD | 3 % pro Tag | 6 % |
| 5.000.000 USD, woertliche Lesart von `5000k` | 0,003 % pro Tag | 0,006 % |

Diese Divisionen sind keine Renditeprognose. Ein Tagesziel darf weder mehr Trades erzwingen noch Hebel, Positionsgroesse oder Stop-Naehe rechtfertigen. Stop-Orders garantieren keinen Ausfuehrungspreis; Stop-Limit-Orders koennen unausgefuehrt bleiben. Deshalb kann auch ein korrektes Risikogate einen Verlust von mehr als 300 USD nicht ausschliessen. [SEC/Investor.gov: Stop-, Stop-Limit- und Trailing-Stop-Orders](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15)

## Lokale Ausgangslage und Messbarkeit

Stand der lokalen Bestandsaufnahme am 08.09.2026; kein aktueller Hetzner-Snapshot:

| Messpunkt | Lokaler Befund | Konsequenz |
| --- | --- | --- |
| Neuester Signal-Datensatz | 05.08.2026 | Kein Beleg fuer heutige Scanner-Leistung |
| Signalstatus | 22 OPEN | Keine abgeschlossene lokale Ergebniskohorte |
| Revisions-/Modellzuordnung | Bei allen lokalen Datensaetzen null | Ergebnisse keiner aktuellen Codeversion zurechnen |
| Aktuelle Nettotrefferquote / Nettoerwartungswert | Unbekannt | Weder Verbesserung noch Profitabilitaet behaupten |
| Tests der Paper-Risikoschicht | 384 bestanden am 08.09.2026 | Vertrags-/Softwaretests, kein Renditenachweis |

Offene Datensaetze sind nicht automatisch derzeit offene Brokerpositionen. App-Signal, tatsaechlich versandte Mail, simulierte Ausfuehrung und Broker-Fill bleiben getrennte Grundgesamtheiten.

## Schrittfolge und Nachweisgrenzen

### 1. Bestandsaudit und Diagnose-CLI – umgesetzt und geprueft

Die bestehende Risiko-, Tracker- und Vergleichsinfrastruktur wurde gelesen; die drei Risikotestsuiten wurden mit einem isolierten temporaeren Datenpfad ausgefuehrt. Die lokale Diagnose-CLI wurde um `--db` und `--format json` fuer aggregierte Evidenz erweitert. Sie liest ausschliesslich den ausgewaehlten Bestand; sie rekonstruiert **keinen historischen As-of-Zustand**, erfindet keine Kurs-/Kostenhistorie und aktiviert nichts.

Der vorhandene Vergleich [scanner_cohort_comparison.py](C:/Projekt/TradingBot/modules/scanner_cohort_comparison.py:104) wird wiederverwendet: gleiche gehashte Inputs, explizite Kosten, getrennte fehlende/ungefuellte/ungeklaerte Ergebnisse, gepaarte Netto-R-Differenz. Kein zweiter Vergleichsmotor und keine nachtraegliche Auswahl der drei besten Tagestrades.

### 2. Aktuellen Produktionsbestand sichern – offen

Zuerst einen konsistenten, lesbaren Hetzner-Snapshot mit Erstellungszeit, Revisionen, Modellversionen und benoetigten Tracker-/Zustellmetadaten beschaffen. Konsistenz eines SQLite-Snapshots einschliesslich laufender WAL-Schreibvorgaenge muss nachgewiesen sein; keine unkoordinierte Kopie nur der Hauptdatei.

Kosten-, Fill- und Kursdaten muessen dieselben Gelegenheiten und Zeitraeume betreffen. Geheimnisse, Mailinhalte und personenbezogene Daten sind fuer eine aggregierte Messung nicht pauschal erforderlich. Fehlende Provenienz bleibt unbekannt. Kein bisheriger lokaler Test ersetzt diesen Produktionsnachweis.

### 3. Eine feste Strategie und eine Exit-Alternative vorab registrieren – offen

Vor Betrachtung des Holdout-Ergebnisses festhalten: Scanner/Revision, Aktien oder Krypto, Long oder Short, Handelsplatz, Zeithorizont, Session/Zeitzone, Datenqualitaet, universumsbezogene Auswahl, Einstieg, Rangfolge gleichzeitig verfuegbarer Signale, Stop, Exit und Kostenannahmen. ORB-Intraday und BI-Swing nicht vermischen. Die konkrete Teststrategie ist noch nicht ausgewaehlt.

Baseline und **eine** konkret definierte Exit-Alternative verwenden dieselben zu diesem Zeitpunkt verfuegbaren Chancen und denselben Risikomassstab. Entwicklung und spaeterer Holdout werden zeitlich getrennt; ueberlappende Halteperioden duerfen keine Information zwischen beiden Fenstern uebertragen. Start, Ende, Auswertungsdatum und Regeln werden vorab eingefroren. Kein fortlaufendes Testen bis ein positives Resultat erscheint.

Keine Stops passend zu 50 USD Gewinn enger setzen; keine Fibonacci-Anker, Levels oder Schwellen nach dem besten historischen Ergebnis auswaehlen. Der BI-Vertrag bleibt mindestens 17/20 bestaetigte Faktoren plus harte Blocker; darunter keine Signalausgabe, Watchlist, Mail oder Signaltracking.

### 4. Netto-Ledger und prospektives Tagesbudget – offen

Zuerst offline modellieren, erst nach Kapitalbestaetigung und gesonderter Pruefung eine persistierte Ausfuehrungskonfiguration erwaegen. Benoetigt werden:

- Tatsachengetreue Entry-/Exit-Fills, Teilfuellungen, Gebuehren, Spread/Slippage ohne Doppelabzug, Finanzierung/Funding und gegebenenfalls Leihkosten. Modellkosten und Brokerkosten explizit unterscheiden; fehlende Kosten nicht als null behandeln.
- Fester Sessionbeginn, Kontowaehrung und Startkapitalbasis; Regeln fuer Mitternacht, Sommerzeit, offene Altpositionen, Gewinne und Neustarts.
- Maximal drei **Entry-Slots pro definierter Session**, nicht drei gleichzeitige Positionen und nicht drei Gewinner. Pending-Orders reservieren einen Slot. Jede tatsaechliche Entry-Teilfuellung verbraucht ihn endgueltig fuer diese Session. Nur nachweislich vollstaendig ungefuellte, abschliessend mit dem Broker abgeglichene Stornos/Ablehnungen geben eine Reservierung frei. Teilgefuellte Stornos geben keinen Slot zurueck; Retries desselben Intents bleiben idempotent.
- Noch verfuegbares Verlustbudget inklusive bereits realisierter Nettogewinne/-verluste, offener und reservierter Risiken sowie Kosten-/Gap-Stress. Realisiertes PnL plus Entry-zu-Stop-Risiko und Mark-to-Market-PnL plus verbleibendes Mark-zu-Stop-Risiko sind unterschiedliche Rechenbasen: unrealisiertes PnL nicht doppelt zaehlen.
- Keine automatische Risikoerhoehung durch Tagesgewinne oder die neue Session. Regeln fuer Gewinnwiederverwendung und uebernommene Positionen werden vorab festgelegt; die genannte Toleranz erhoeht bestehende strengere Limits nicht.
- Gemeinsames Exposure, korrelierte Positionen, Cash/Settlement, Kapitalbindung, Handelsplatz und Short-Borrow anhand tatsaechlicher Brokerdaten pruefen. Kein Hebel oder Short-Verfuegbarkeit aus einem Signal ableiten.

Konkrete bestehende Luecken:

| Befund | Verifizierter Codebezug | Bedeutung |
| --- | --- | --- |
| `max_positions=3` begrenzt gleichzeitige Positionen | [paper_autotrader.py](C:/Projekt/TradingBot/modules/paper_autotrader.py:56) | Kein Drei-Trades-pro-Tag-Vertrag |
| Tagesverlustgate vergleicht aktuelles Broker-DailyPnL mit prozentualer aktueller NetLiquidation | [paper_autotrader.py](C:/Projekt/TradingBot/modules/paper_autotrader.py:1830) | Keine feste Session-Startbasis und kein vorab reserviertes Tagesbudget |
| Atomare Reservierung besitzt keine DailyPnL-/Sessionbudget-Eingabe | [trading_risk_store.py](C:/Projekt/TradingBot/modules/trading_risk_store.py:5405) | Bestehende Stoprisiko-/Cash-Reservierung nicht als Tagesverlustgarantie darstellen |
| Broker-Fill-Serializer uebernimmt keine Kommissionen; Outcome ist Preisdifferenz mal Menge | [paper_autotrader.py](C:/Projekt/TradingBot/modules/paper_autotrader.py:561), [trading_risk.py](C:/Projekt/TradingBot/modules/trading_risk.py:1182) | Brutto-Flat kann netto negativ sein und dennoch die bisherige Verlustserie zuruecksetzen |
| Verlustserie wird pro UTC-Tag berechnet | [trading_risk.py](C:/Projekt/TradingBot/modules/trading_risk.py:1215) | Nicht automatisch dieselbe Session wie Broker-DailyPnL oder US-Handel |
| Tracker/Shadow weisen allgemeine Roundtrip-Kosten nicht aus | [signal_tracker.py](C:/Projekt/TradingBot/modules/signal_tracker.py:7419), [signal_tracker.py](C:/Projekt/TradingBot/modules/signal_tracker.py:8422) | Brutto-R nicht als realen Nettogewinn ausgeben |

Die existierende Paper-Schicht besitzt bereits konservative Stoprisiko-, Cash-, Identitaets-, Parallelitaets- und dauerhafte Reservierungsgates. Diese werden nicht ersetzt oder gelockert. Ihre Quellcode-Defaults sind Review-Modus, deaktivierte Ausfuehrung und aktiver Kill-Switch; der Produktionszustand ist damit nicht verifiziert. [Paper-Defaults](C:/Projekt/TradingBot/modules/paper_autotrader.py:49)

### 5. Abhaengigkeitsgerechte Auswertung und Forward-Paper-Pruefung – offen

Primaer berichten: Nettoerwartungswert je Trade und Session, Kostenanteil, Verlusttage, maximaler Drawdown, simulierte Budgetueberschreitungen, Opportunitaets-/Fill-Haeufigkeit und Evidenzabdeckung. Trefferquote, MAE/MFE und gepaarte Netto-R-Differenz erklaeren diese Werte, ersetzen sie aber nicht.

Tage und ueberlappende Exposures als abhaengige Gruppen behandeln. Das vorhandene Wilson-Intervall ist ausdruecklich unabhaengigkeitsbasiert und keine ausreichende Unsicherheitsschaetzung fuer korrelierte Signale. [Bestehende Vergleichsgrenze](C:/Projekt/TradingBot/modules/scanner_cohort_comparison.py:210)

Eine Zahl wie 30 Trades ist keine automatische Freigabe. Vorab wirtschaftlich relevante Mindestwirkung, gewuenschte Praezision, Datenabdeckung und Kosten-/Gap-Stress festlegen; die benoetigte unabhaengige Evidenz danach bestimmen. Ein breites Intervall oder unzureichende Abdeckung bedeutet **unentschieden**, nicht profitabel. Beobachtete Grenzverletzungen werden nicht nachtraeglich entfernt.

Erst anschliessend einen gesondert freigegebenen Forward-Paper-Test pruefen. Ein positives historisches Tagesmodell bestaetigt weder minutengenaue Ausfuehrung noch reale Brokerergebnisse. [Modellgrenzen](C:/Projekt/TradingBot/modules/backtest_methodology.py:78)

## Unveraenderliche Freigabegrenze

Dieses Protokoll und die Diagnose veraendern keine Scanner-Schwellen, Serverdienste, Cronjobs, Kontogroessen oder Handelsparameter. Weder Live-Handel noch die Aktivierung von Broker-Paper-Orders ist dadurch autorisiert. Profitabilitaet, taegliche 150 USD und die Einhaltung von exakt 300 USD Maximalverlust werden nicht versprochen.

## Diagnose benutzen und richtig lesen

Lokal, ohne DB-Migration, Netzwerkabruf oder Ausfuehrung:

```powershell
.\.codex_pytest_env\Scripts\python.exe -B scripts/signal_performance_breakdown.py --db data_cache/signal_tracker.sqlite --days 30 --format json
```

Auf Hetzner, **erst wenn diese Skriptversion vorhanden und der aktive Datenpfad bestaetigt ist**, als Servicebenutzer:

```bash
sudo -u tradingbot /home/tradingbot/app/venv/bin/python -B /home/tradingbot/app/scripts/signal_performance_breakdown.py --db /home/tradingbot/app/data_cache/signal_tracker.sqlite --days 30 --format json
```

Kein Dienst-Neustart fuer die Auswertung erforderlich. Sie liest in einer SQLite-Transaktion mit `mode=ro` und `query_only`; kein Schema-Helper und keine Haupt-API werden aufgerufen. Git-Index-Refresh ist fuer den Diagnoseprozess deaktiviert. Der explizite Pfad muss zum laufenden Writer passen: Umgebungs-Overrides sind nicht durch diesen Default-Pfad ausgeschlossen.

Die Standardkohorte umfasst im Fenster zeitlich gereifte Plaene. Gereift bedeutet **Beobachtungsfrist abgelaufen**, nicht automatisch vollstaendige Kurse, Fill- oder Exit-Nachweise. `--include-recent` liefert stattdessen eine ausdruecklich vorlaeufige Versandkohorte; offene junge Gewinner duerfen nicht mit bereits abgeschlossenen schnellen Stops zu einer vermeintlich finalen Quote vermischt werden.

JSON trennt Zeitabschnitt, Scanner, Strategie, Assetklasse, Richtung, Haltedauer, Regime, Code- und Evaluationsversion, Fill-/Ursprungsnachweis und Kanal. Fehlende Dimensionen bleiben `unknown`. Die Textansicht bleibt grober und benennt ihre Versionsmischung ausdruecklich. Es gibt keinen historischen `--as-of`-Replay: neu ausgelesene mutable Zeilen koennen einen vergangenen Datenbankzustand nicht rekonstruieren. Zukunftsdatierte Fill-/Exit-Evidenz wird sichtbar aus der JSON-Ergebnisarithmetik ausgesondert, nicht als vergangener Gewinn gewertet.

Die R-Arithmetik wird aus dem bestehenden Tracker wiederverwendet, einschliesslich seiner deskriptiven Legacy-Grenzen. `control_unresolved` und fehlende Herkunft sind strengere Warnzeichen als ein positiver Brutto-Durchschnitt. Ein leeres Ergebnis ist `null`, nicht 0R. Keine Empfaenger, Mailtexte, Konten, Signalreferenzen oder Rohzeilen werden exportiert. Die private lokale Beispielausgabe bleibt unter `output/profitability/step1-local-evidence-20260908.json`, ausserhalb des Commits.

Aktueller lokaler 30-Tage-Snapshot: 22 historische Trade-Zeilen, null neu angelegte Signale im Fenster, fuenf Plaene mit im Fenster abgelaufener Beobachtungsfrist; alle fuenf noch OPEN. Null entschiedene Ergebnisse und null qualifizierte Ursprungsnachweise. Nettoperformance und Dollar-PnL bleiben unbekannt. Das ist ein Befund ueber die lokale Datei, nicht ueber Hetzner.

## Abnahme und reproduzierbare Evidenz

- Finale gezielte Tests der Diagnose und des vorhandenen Vergleichs: **54 bestanden** in 1,63 s.
- Vollstaendige Testsuite auf den finalen Skript-/Testbytes: **3561 bestanden, 4 uebersprungen** in 634,79 s. Isolierte temporaere Daten-, Laufzeit- und Dedupe-Pfade; JUnit lokal unter `tmp/profit_evidence_full_20260908.xml`.
- Die vier Skips betreffen fehlende Windows-Symlink-Rechte beziehungsweise Linux-spezifische `O_NOFOLLOW`-/FIFO-/Rename-Vertraege. Daraus wird kein neuer Linux-Deployment-Nachweis abgeleitet.
- Unabhaengige Bestandspruefung der drei Paper-Risikosuiten: **384 bestanden** in 40,20 s, mit isoliertem temporaerem Datenpfad. Kein Broker wurde aktiviert.
- Unabhaengiges Review: gemeinsame Tracker-Arithmetik, unbekannte Versionen, Zukunftsquarantaene, Quellenabdeckung und private Exportfelder geprueft. Die empfohlenen Zusatzfaelle fuer eine gueltige leere DB und gueltige SMTP-/BE-Nachweise sind enthalten.
- Compile und Frontend-Bundle-Pruefung bestanden; UI, API, Tracker, Paper-Ausfuehrung und Bundle wurden durch diesen Schritt nicht geaendert. Bundle-Kennung bleibt `938257a0d05f`; keine neue visuelle Abnahme erforderlich oder behauptet.
- Tatsaechlicher lokaler JSON-Leselauf am 08.09.2026 um 14:02:28 UTC. DB-SHA256 vor und nach dem Lesen identisch: `924b94bd1f01ad6d5becfa91698bb0c9f37f3204fe6df72607eb9cbc1fc595bc`.
- Gepruefte Skriptbytes: SHA256 `c5acb23fcdc3a9ecfe37019f7875a8c46c8b2e9d0bd2b2662c415048b0548020`; Testdatei: `2db912ecfebd685b804555a5cd28935de1e5c6ef44c5ed4889ca62f34f2a1d02`.

Diese Abnahme betrifft die Lesediagnose und das Pruefprotokoll, nicht einen profitablen Scanner, ein ausgefuehrtes Deployment oder die Umsetzung der offenen Schritte 2 bis 5.
