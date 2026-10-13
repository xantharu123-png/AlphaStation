# LEAPS-Scanner: US-Aktien und gekaufte langfristige Calls

Stand: 13.10.2026. Status: **schriftlicher Entwurf zur Nutzerprüfung**.
Die Ausrichtung ist bestätigt; dieses Dokument ist noch nicht freigegeben.
Noch kein Produktcode, Optionsdatenabruf, neuer Vertrag, Mailversand oder Serverupdate.

## 1. Auftrag und Erfolgskriterium

Alpha Station soll gelistete langfristige Calls für US-Aktien finden und
vergleichen, wie im besprochenen SOFI-Beispiel. Der Scanner ist unabhängig von
heutigen Momentum-, BI- oder anderen Swing-Treffern. Er darf nicht nur deren
kleine Shortlist untersuchen. Das initiale Suchfenster beträgt **12 bis 24
Kalendermonate** bis zum Verfall; untersucht werden ausschließlich gekaufte Calls, keine
verkauften Optionen, Spreads oder automatischen Brokerorders.

Ein Ergebnis beantwortet: Welcher tatsächliche Kontrakt? Welche beobachteten
Kosten, Laufzeit, Liquidität und Break-even-Hürde? Wie unterscheiden sich
Aktienbesitz und Call? Was wären die wirtschaftlichen Ergebnisse bei expliziten
Aktienkursen **am Verfall**? Fundamentale und technische Basiswertinformationen
helfen beim Vergleich, sind aber keine nachgewiesene Kursprognose.

Erfolg heißt nachvollziehbare Kontrakte, korrekte Rechnungen, ehrliche Abdeckung
und eine kompakte Oberfläche. Es heißt nicht garantierte Gewinner, identische
Exposure zu 100 Aktien oder eine aus Delta erfundene Trefferwahrscheinlichkeit.

## 2. Gewählter Ansatz und Umfang

Empfohlen: vollständige, nachvollziehbare US-Basiswert-/Kontraktreferenz im
Laufzeitfenster, anschließend getrennte Basiswertanalyse und Kontraktvergleich.
Zwischencaches und kontrollierte Verarbeitung vermeiden, dass bestehende
Scanner oder der Maildienst durch den neuen Datenabruf ausgebremst werden.

Nicht gewählt: nur bestehende Swing-Treffer untersuchen; dies verfehlt den
Vollmarktauftrag. Ebenfalls nicht Standard: sämtliche Laufzeiten/Strikes ohne
Suchfenster bei jedem Poll erneut abrufen; dies verursacht unnötige Last.

Version 1 umfasst Discovery, Datenprüfung, exakte Verfallsrechnung und App-
Vergleich. Ausgeschlossen sind automatische Käufe, Positionsgrößenberatung,
neue Abos, Änderungen bestehender Mail-Einstellungen, Optionsperformance im
Aktien-Tracker sowie ein angeblich live-identischer Optionsbacktest. Ein neuer
LEAPS-Mailkanal wäre eine spätere, ausdrücklich freizugebende Erweiterung.

## 3. Universum und Auswahl

- US-Aktien und passende US-ADR-Notierungen; keine ETFs, Indizes oder unbekannten
  Instrumentklassen in der ersten Version. Listing-Locale und Klassifikation
  werden explizit geprüft, nicht aus dem Tickersymbol abgeleitet.
- Das Universum umfasst Basiswerte mit tatsächlich referenzierten Calls im
  Suchfenster. Eine fehlende lange Laufzeit bedeutet nicht, dass eine Aktie
  grundsätzlich keine Optionen hat. Die UI unterscheidet diese Aussagen.
- Der bestehende `_load_common_stock_universe` ist kein Vollständigkeitsbeleg:
  aktuell fehlen ein expliziter US-Filter und belastbare Pagination-Abschlüsse.
  LEAPS erhält einen eigenen strikten Referenzvertrag; kein Nebenauftrag zur
  globalen Änderung aller alten Scanner.
- Alle Referenzseiten werden verarbeitet, Duplikate über Kontraktidentität
  entfernt und Seitenzyklen erkannt. Ein Budget-/HTTP-Abbruch ist **partiell**,
  nie „keine Möglichkeiten“. Eine unbekannte Gesamtzahl bleibt unbekannt.
- Das Laufzeitfenster benutzt das US-Marktdatum des Runs, inklusive `+12` und
  `+24 Monate`. Ungültige Monatstage werden auf den letzten Tag des Zielmonats
  geklemmt. 365–730 Tage sind kein stiller Ersatz für Kalendermonate.
- Grundfilter: belegbare Instrumentbedingungen und brauchbare Daten. Die
  bisherigen Momentum-Grenzen, BI-17/20, eine Acht-Ticker-Shortlist oder ein
  erfundener LEAPS-Score 80 sind keine Zugangsvoraussetzungen.
- Optionale Nutzerfilter für Spread, Delta, Open Interest, Laufzeit und
  Basiswertmerkmale sind sichtbar und im Run gespeichert. Fehlende Werte sind
  nicht numerisch 0. Aktive Filter mit fehlender Evidenz führen in „nicht prüfbar“.

Fundamentale Qualität und Bewertung bleiben getrennt. Das vorhandene SEC-Modul
kann Cashflow, Verschuldung, Verwässerung und Abdeckung liefern. Banken/REITs
bleiben bei fehlenden Spezialdaten ausdrücklich eingeschränkt; SOFI bekommt
dadurch keine erfundene vollständige Bankbewertung. Kursverlust gegenüber einer
alten Kapitalerhöhung ist für sich kein Nachweis einer Unterbewertung.

Technische Basiswertinformationen verwenden ausreichend abgeschlossene
Tageshistorie, etwa EMA50/200 und belegte Strukturlevels. Fehlende Historie bleibt
unbekannt. Kein kurzfristiger Ausbruch ist vorgeschrieben. Es werden keine
neuen Kursziele, Kurswahrscheinlichkeiten oder zusätzlichen Wyckoff-Listen aus
diesen Merkmalen abgeleitet. SEC-Abfragen werden gebündelt/gecacht und laufen
nicht tausendfach synchron im API-Request oder Eventloop.

## 4. Datenzugang und Kontraktvertrag

Der bestehende Aktien-Datenzugang beweist **keinen Optionszugang**. Vor einem
produktiven Scan braucht es separat bestätigte Referenz-/Quote-Rechte,
Datenverzögerung und zulässige Anzeige für die App-Nutzer. Account/Lizenzstatus
ist aktuell **UNVERIFIED**; weder neue Verträge noch kostenpflichtige Upgrades
werden mit diesem Auftrag selbständig abgeschlossen.

Massive ist der zuerst geprüfte Adapterkandidat, nicht bereits ein aktivierter
Optionsprovider. Referenz und Chain-Snapshot sind getrennte Ressourcen. Der
Adapter erhält Zugang über die bestehende private Serverkonfiguration; keine
Schlüssel in Browser, URLs der Diagnose, Snapshots oder Fehlermeldungen.
Pagination folgt ausschließlich zugelassenen HTTPS-Providerhosts.

Die Kontraktidentität enthält Provider-Symbol, Basiswert, Call-Recht, Strike als
Decimal, Verfall, Währung und Deliverable-/Adjustment-Fingerprint. Verschiedene
Strikes/Laufzeiten derselben Aktie sind verschiedene Ergebnisse. Symbol und
Metadaten müssen übereinstimmen. Provider-Korrekturen dürfen alte Bedingungen
nicht unbemerkt überschreiben.

Die einfache Rechnung gilt nur für nachgewiesene Standardkontrakte mit 100
Aktien und Premium-Multiplikator 100, passenden Ausübungs-/Lieferbedingungen und ohne zusätzliche angepasste
Liefergegenstände. Unverifizierte oder angepasste Bedingungen werden nicht
heimlich als Standard-100 gerechnet. Dokumentiert optionale Providerfelder
werden gemäß Schema interpretiert, nicht pauschal als Fehler bei Abwesenheit.

Pro Beobachtung getrennt speichern: Bid/Ask, Sizes, Originalzeit, Empfangszeit,
Feedmodus, Underlying-Kurs/-Quote samt eigener Uhr, Volumen mit Intervall sowie
Open Interest mit seinem Bezugsdatum. Open Interest ist nicht automatisch live.
Greeks/IV dürfen fehlen; Anbieter-Modellwerte bleiben Modellwerte. Delta ist
dimensionslos pro Anteil in [0,1] für diese Long-Calls (`0.70` entspricht lokal
70 Aktien je Standardkontrakt); IV intern annualisiert als Dezimalzahl
(`0.35 = 35%`). Provider-Skala, Modell und Originalstand müssen belegt bzw.
unbekannt markiert sein; keine heuristische Umdeutung von 35 in 0,35.

## 5. Quotequalität und Zeitstände

- Last Trade, Midpoint und Fair Market Value sind keine Ersatzwerte für Ask.
  Ask-basierte Kontraktkosten sind eine beobachtete Kaufpreisindikation, kein
  ausgeführter Kauf und kein garantiert handelbarer Preis.
- Fehlender Ask oder Ask <= 0, gekreuzter Bid/Ask, unbekannte Originalzeit oder
  Zeit aus der Zukunft machen die Quote unbrauchbar. Bid 0/fehlend bedeutet
  einseitige Quote; keine behauptete ausreichende Liquidität.
- Option und Basiswert behalten jeweils Originalzeit und Feedmodus. Empfang,
  Cacheabruf oder Rendering erneuern diese Uhren nicht. Aktien-15-Minuten-
  Verzögerung darf nicht auf Optionsdaten übertragen werden.
- Ein zeitgleicher Vergleich verlangt beidseitige Quotes und höchstens 30s
  Zeitversatz. Bei Echtzeitdaten gilt ein maximales Quotenalter von 60s. Bei
  dokumentiert verzögerten Feeds gilt maximal der belegte Feedlag plus 60s;
  der Lag bleibt ausgewiesen. Nur passende, explizit datierte Beobachtungen
  dürfen gemeinsam verglichen werden. Unbekannter Feedlag ist nicht Echtzeit.
  Diese technischen Fristen behaupten keine Fill-Garantie.
- Außerhalb der belegten Options-Handelssitzung bleibt der letzte gültige Stand
  datiert sichtbar. Er wird nicht als aktueller Einstieg oder „JETZT“ ausgegeben.
  Erweiterte FMV-Zeiten beweisen keine handelbaren Bid/Ask-Quotes.
- Fehlt eine passende Aktien-Ask-Quote, ist der Aktienkostenvergleich nur mit
  klar bezeichnetem Referenzkurs möglich, nicht als aktueller Kaufkostenvergleich.
- Fehlende Greeks verhindern nicht die reine Verfallsalgebra; Delta-Exposure
  und IV-Vergleich bleiben dann leer. Delta 0 ist nicht dasselbe wie fehlend.

`S_0` ist der tatsächlich für den Vergleich verwendete, positive Basiswertkurs
mit Originaldatum und Kursart; nicht beliebig zwischen Tagesabschluss, Chartkurs
und Ask wechseln. Abgeschlossene Tageskerzen für die Unternehmensthese sind
eine separate Datenreihe. Ein unpassender Zeitstand sperrt den zeitgleichen
Vergleich, nicht die explizit datierte isolierte Verfallsrechnung. Fehlt ein
gültiges `S_0` oder ist es 0, bleiben relative Break-even-Hürde, Aktienrendite
und relative Szenariopunkte unbekannt; absolute Verfallskurse und deren Dollar-
Rechnung bleiben bei gültigen Kontrakt-/Premiumdaten möglich.

## 6. Mathematische Ausgabe

Für `n` positive ganze Standardkontrakte, Strike `K`, beobachteten Ask `A` und
Aktienkurs `S_T >= 0` **am Verfall**:

```text
Premiumkosten                 = n × 100 × A
Innerer Wert am Verfall       = n × 100 × max(S_T − K, 0)
Wirtschaftliches Ergebnis     = innerer Wert − Premiumkosten
Rendite auf die Prämie        = Ergebnis / Premiumkosten
Break-even vor Kosten         = K + A
Break-even-Anstieg ab S_0      = (K + A) / S_0 − 1
Lokale Delta-Exposure         = n × 100 × Delta
Spread relativ zum Mid        = (Ask − Bid) / ((Ask + Bid) / 2)
Ask-zu-Bid-Verlust             = (Ask − Bid) / Ask
```

Gebühren bleiben separat und bei fehlenden Angaben **unbekannt**, nicht null.
Quote-Debit und Szenariorendite sind vor diesen Kosten. Ein eventueller
kostenbereinigter Wert benötigt explizit vollständig erfasste Kostenannahmen.
Geld/Strikes und Grenzvergleiche benutzen Decimal; gerundet wird nur die Anzeige.

Aktienvergleich: `n × 100` Aktien, eigener anfänglicher Kapitalbetrag und
Dollarergebnis. Nominal weniger Anfangskapital ist nicht delta-neutrale gleiche
Exposure. Delta kann sich verändern; Delta/IV sind keine reale Trefferquote.

Standardausgabe ist ein Kontrakt. Szenariopunkte sind Rechenannahmen, keine
Prognosen: Kurs null, −20%, unverändert, +25%, +50%, +100% gegenüber `S_0`;
zusätzlich frei wählbare nichtnegative Verfallskurse. Für Ergebnisse **vor**
Verfall gibt es in Version 1 keine erfundenen Optionspreise oder Renditen.

Der innere Wert bezeichnet den wirtschaftlichen Wert, keine automatische
Barauszahlung. Aktiencalls können Aktienlieferung/Ausübung verlangen. Ein
Standardkontrakt mit Strike 15 benötigt dann zusätzlich 1.500 USD zum Kauf von
100 Aktien. Brokerfristen und Ausübungsregeln bleiben extern. Nach Ausübung und
Behalten der Aktie beschreibt „maximal die Prämie verlieren“ nicht mehr das
Gesamtrisiko der fortbestehenden Aktien-/Marginposition.

## 7. Ranking und kompakte App

Eigener Scanner **LEAPS** unter Aktien; kein WATCH-/Beobachtungsbranding und
keine neue „JETZT kaufen“-Behauptung. Standardvergleich nach Break-even-Anstieg,
mit stabiler Kontraktidentität als Tie-breaker; andere Spalten sind sortierbar.
Dieser Sortierwert ist keine Gesamtempfehlung. Ein billiger Call oder hoher
hypothetischer Prozentgewinn allein ist keine besondere Qualität.

Liste: Unternehmen, Strike/Verfall, Laufzeit, Kosten je Kontrakt, Break-even und
notwendiger Anstieg, Liquidität/Spread sowie Delta, soweit belegt. Datenstand
und Feedmodus kompakt; Fehler-/Abdeckungsdetails aufklappbar. Basiswertqualität,
Bewertung und Kontraktmerkmale nicht zu einem unkalibrierten Optionsscore mischen.
Die Liste ist paginiert; Anzeigelimits dürfen keine ungeprüften Kontrakte als
untersucht zählen oder das Scanuniversum heimlich auf die angezeigten Zeilen kürzen.

Detail: **zuerst Basiswertchart**, dann Kontraktvergleich und Verfallsszenarien.
Optionsprämie und Aktienkurs klar verschieden benennen. Ausführliche Daten- und
Ausübungsdetails bleiben geschlossen. Kein generischer Aktien-Entry/Stop/TP-
Plan, kein Aktien-Trade-Health auf Optionsprämien, keine automatisch sichtbare
Wyckoff-Liste. Gespeicherte Ergebnisse beim Öffnen automatisch laden.

Ein gemeinsamer Fortschrittsbalken und konsistente Start-/Pause-/Fortsetzen-
Bedienung. Keine doppelte Fortschrittsanzeige. Alte Async-Antworten dürfen nicht
den ausgewählten Kontrakt oder einen neueren Run ersetzen.

## 8. Subsystem und Betriebsgrenzen

Getrennte, rein testbare Verantwortlichkeiten: Optionsreferenz/-transport,
Kontrakt-/Quotevalidierung, Basiswert-Evidenz, Decimal-Rechner und Scannerlauf.
Produktintegration bleibt dünn; neue Module importieren nicht `api.py` und
starten beim Import keine Scheduler, Providerabfragen oder SMTP-Verbindungen.

Eigener Run-Owner `leaps`, registrierte Phasen Universum → Basiswerte → Chains
→ Berechnung → Publikation. Eigene versionierte, atomare Datenhaltung unter
`ALPHA_DATA_DIR/leaps`. Der letzte vollständige Stand bleibt bei einem Fehler
erhalten; Teilstand und alter vollständiger Stand sind unterscheidbar.
Run-ID, Filter, Marktdatum, Schema-/Methodikversion und Quellenzeiten gehören
zum Snapshot. Kein Schreiben in alte Reminder-/Tracker-Daten.

Die bestehende Budgetfunktion hat noch keine Prioritätswarteschlange. Deshalb
eigene LEAPS-seitige Admission statt globaler Änderung alter Scanner: höchstens
ein gleichzeitiger Providerrequest und pro Minute höchstens der kleinere Wert
aus 20 Requests und 10% des nachgewiesenen gemeinsamen Budgets (abgerundet).
Während aktiver vorrangiger Aktien-/BI-/Alert-Datenläufe startet LEAPS keinen
weiteren Call, sondern meldet Warten/Fortsetzung. Keine Veränderung bestehender
Scannerbudgets, Retries oder Timeouts. Ohne prüfbare gemeinsame Budgetgrenze
gibt es keinen Liveabruf; kein neuer Fail-open-Fallback für Optionen.
Begrenzte eigene Timeouts und Retries. 401/403 stoppt den Optionspfad sichtbar, nicht
tausendfach je Aktie neu versuchen. Budgetende erzeugt partiellen Run mit
gespeicherter Fortsetzung, kein falsches 100%. Pause an sicheren Grenzen;
Browser-Abort stoppt keinen Backend-Job. Laufende Requests haben feste Timeouts.

Anfangs manuell, kein ungefragter Zeitplan. Ein geteilter LEAPS-Run; Start,
Pause und Fortsetzen nur Admin über ausdrücklich LEAPS-gebundene Steuerung.
Kunden dürfen keinen globalen oder fremden Run steuern. Das bestehende
`/api/scan-control` wird dafür nicht generell geöffnet; die UI zeigt Kontrollen
nur bei entsprechender Berechtigung. Authentifizierte Ergebnisendpoints mit
expliziter Scannerberechtigung und Throttling; Feature zunächst deaktiviert.
Admin-Vorschau erst nach verifiziertem Datenzugang, Mehrnutzeranzeige erst nach
geklärten Displayrechten und expliziter Produktfreigabe. Kein neuer Billingtarif.

Status getrennt: vollständiger Lauf ohne Kandidaten, partiell, Daten nicht
berechtigt, Quote unbrauchbar/veraltet, Bedingungen unbekannt, Greeks fehlen,
Basiswert-/Fundamentalabdeckung eingeschränkt. Diagnose enthält keine Secrets.
Universums-, Basiswert-, Chain- und Kontraktzähler haben eigene Nenner;
überlappende Ausschlussgründe werden nicht als disjunkte Gesamtzahl summiert.

Bestehende Aktien-/Kryptoscanner, Mailgrenzen, Kundenpräferenzen und Landingpage
bleiben unverändert. Kein Optionsdatensatz im Aktien-Signaltracker: dessen
Tickeridentität, Tages-OHLC, Stop/TP-Modell und 60-Bar-Horizont sind ungeeignet.
Der allgemeine Mailtransport könnte später verwendet werden, nicht dessen
aktienbezogene Admission-/Performance-Logik.

## 9. Abnahmekriterien vor Produktfreigabe

1. SOFI-Rechenfixture, **keine Livequote**: `S_0=15.34, K=15, A=4.30, n=1`.
   Debit 430, Break-even 19,30; `S_T=22/26/30` ergibt 270/670/1.070 USD bzw.
   62,7907/155,8140/248,8372%. Unverändert: −396 USD/−92,0930%; `S_T<=15`:
   −430 USD. Aktie bei 30: +1.466 USD/+95,5671%, nicht derselbe Dollarertrag.
2. Bid 4,00/Ask 4,30: Spread 7,2289%, Ask-zu-Bid-Verlust 6,9767%. Fehlende
   Werte, bool/NaN/Inf, negative Preise und ungültige Mengen strikt testen;
   Kurs null bleibt ein erlaubtes Verfallsszenario.
3. Kalendergrenzen, Monatsende/Schaltjahr, standard/adjusted Deliverables,
   Symbol-/Metadatenkonflikte und verschiedene Strikes/Laufzeiten gegenprüfen.
4. Originalzeiten einschließlich ns-Epochen, zukünftige/stale Quote, eigene
   Aktien-/Optionsfeedmodi, verzögertes Open Interest, fehlende Greeks und
   verbotene Ask-Fallbacks in Providerfixtures prüfen.
5. Vollständige Pagination, echte leere Antwort versus Entitlementfehler,
   Teilabbruch, Rate Limit, Cachepublikationsfehler und Resume reproduzieren.
6. Desktop/Mobil im Browser: Chart zuerst, kompakte Liste, ein Balken,
   automatische Cacheladung, Scope-/Run-Races und keine JavaScriptfehler.
7. Bestehende Scanner/Mail-/Tracker-Grenzen als Regression prüfen. Keine
   Live-Provider-/SMTP-/Brokeraufrufe in Offline-Tests. Unabhängiges Review der
   Mathematik und Providerverträge, nicht nur hohe Gesamttestzahlen.
8. Separat autorisierte, klein budgetierte Liveprobe bestätigt technischen
   Datenzugang, Laufzeiten und Zeiten/Units. Display-/Mehrnutzerrechte benötigen
   unabhängig davon einen tatsächlichen Account-/Lizenznachweis. Offline-
   Fixtures beweisen weder reale Quotenzugänge noch produktive Marktabdeckung.

Aktuelle Chains/Greeks und heutige SEC-Facts dürfen nicht drei Monate rückdatiert
werden. Ein späterer Optionsbacktest braucht damalige Membership/Terms,
zeitlich verfügbare Quotes/Basiswerte sowie definierte Fills/Kosten. Der
bestehende Aktienbacktest und historische Optionsreferenzen allein reichen nicht.

## 10. Nächste Freigabe und Publikation

Zuerst Nutzerprüfung dieses schriftlichen Entwurfs. Danach gesonderter
Implementierungsplan und Wahl der Ausführung; erst anschließend Produktcode.
Daten-/Lizenzzugang, Offline-/Browserabnahme, Git-Publish, Serverinstallation und
reale Funktionsabnahme bleiben getrennte Schritte. Keine Installation umstellen
oder ein gelöschtes Deploy-Skript wiederherstellen.

Die vorhandenen umfangreichen Audit-Reparaturen im Arbeitsverzeichnis werden
nicht mit diesem Entwurf vermischt, zurückgesetzt oder pauschal gestaged. Ihr
früherer Gesamttest ist kein Test des noch nicht implementierten LEAPS-Scanners.

## Quellen und überprüfte Bestandsgrenzen

Offizielle Grundlagen: [OIC LEAPS](https://www.optionseducation.org/optionsoverview/leaps-strategies),
[Delta](https://www.optionseducation.org/advancedconcepts/delta) und
[Ausübung](https://www.optionseducation.org/referencelibrary/faq/options-exercise).
Providerverträge: [Option Chain Snapshot](https://massive.com/docs/rest/options/snapshots/option-chain-snapshot),
[Contracts](https://massive.com/docs/rest/options/contracts/all-contracts),
[historische Quotes](https://massive.com/docs/rest/options/trades-quotes/quotes),
[Nutzungsbedingungen](https://massive.com/legal/terms).
Öffentliche Dokumentation beweist nicht den Zugang/Lizenzumfang dieses Accounts.

Frisch statisch gelesene Integrationsstellen: `api.py` (Universum, Endpoints),
`modules/data_fetchers.py`, `business_quality.py`, `atomic_cache.py`,
`stock_scan_runtime.py`, `scan_control.py`, `signal_tracker.py`, `auth.py`,
`mail_outbox.py` und `frontend/index.html` (`useScannerFeed`, Navigation/Details).
Wiederverwendung jeweils nur innerhalb der oben beschriebenen Verträge.
