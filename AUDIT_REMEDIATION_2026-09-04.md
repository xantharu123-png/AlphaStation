# Scanner-, Mathematik- und UX-Audit: Reparaturpaket 04.09.2026

Ausgangsstand: `47eca9ac05ecbf599e85a16658f2a8d367feb3c5`, Branch `main`.
Auditbeginn 04.09.2026; abschließende Abnahme 05.09.2026.
Das Paket korrigiert reproduzierbare Implementierungs- und Messfehler. Es ist
**kein Nachweis, dass die Strategien jetzt profitabel sind**. Produktion, echte
Mails, Broker und historische Produktionsdaten wurden nicht verändert.

## 1. Warum die bisherigen Verlustzahlen ernst zu nehmen sind

Im zuvor vollständig gelesenen lokalen Archiv von 51 Mails (17.–19.08.) standen
37 neue Pläne und 24 numerische terminale Ergebnis-Meldungen. Diese 24 betrafen
alle `stock_strategy`, nicht eine repräsentative Kohorte des neuen BI-Scanners:

| Kennzahl des Archiv-Ausschnitts | Wert |
|---|---:|
| Positive / negative Ergebnisse | 8 / 16 |
| Trefferquote | 33,3 % |
| Summe / Mittelwert | −5,06R / −0,211R |
| Durchschnitt positiver / negativer R | +1,35R / −0,991R |
| Profit-Faktor | ungefähr 0,68 |
| Rechnerische Break-even-Quote bei diesen mittleren R, ohne allgemeine Kosten | 42,34 % |

Die Rechnung lautet `p * mittlerer Gewinn − (1−p) * mittlerer Verlust`.
Eine Zielprojektion von +2,17R oder +3,31R ersetzt keinen tatsächlich erreichten
mittleren Gewinn. Teilverkäufe, Break-even, Fehlsignale und Ausführung entscheiden
über das Ergebnis. Höhere Targets können die Trefferquote sogar senken.

Das Archiv ist unvollständig: Für 25 der 37 neuen Pläne fehlte eine anhand von
Ticker und Entry/Stop/TP1/TP2 exakt zuordenbare Folge-Mail; nur sieben ließen sich
exakt mit einem terminalen Ergebnis verbinden. Deshalb
keine Vermischung dieser Zahlen mit aktuellen BI-/Crypto-Ergebnissen, keine
behauptete Kontorendite und keine Schwellenoptimierung anhand dieses Ausschnitts.
Eine aktuelle serverseitige, versionsgetrennte Ergebnis-Kohorte war lokal nicht
verfügbar. Ein Ranking „Scanner X gewinnt, Y verliert“ wäre gegenwärtig erfunden.

Die Metrics-Review-Systematik trennt deshalb drei Messgrößen: historischer
Brutto-Preisweg im Mailarchiv (negativ), technische Regressionen (Abnahme unten)
und zukünftiger Netto-Erwartungswert der neuen Version (noch nicht gemessen).
Das Ziel ist ein positiver, außerhalb der Kalibrierungsdaten bestätigter
Netto-Erwartungswert bei begrenztem Drawdown, nicht möglichst viele Signale.

## 2. Gemeinsame Fehler und umgesetzte Korrekturen

| Fehler | Korrektur / Gegenprüfung |
|---|---|
| Historische Support-/Resistance-Zonen änderten ihre Grenzen mit dem aktuellen Preis. | Cluster und physische Grenzen bleiben von der aktuellen Quote unabhängig. Kursüberlappung ist kein bewiesener Reclaim. |
| Näherer ATR-Fallback verdrängte eine echte Invalidierung. | Struktur hat Vorrang; die Volatilitäts-Mindestdistanz darf den Stop nur weiter außerhalb platzieren. Fehlende kausale Invalidierung wird nicht erfunden. |
| VRVP-Stop lag teilweise innerhalb einer Support-/Resistance-Zone. | LONG unter Unterkante, SHORT über Oberkante; überlappende Zone ist kein schützender Stop. |
| Rundung änderte den tatsächlich ausgewiesenen Trade und konnte den Stop zurück in die Struktur schieben. | Gerichtete Rundung: Stop nach außen, Ziele vor die erste Barriere. Geometrie, Risiko und R/R werden mit den zurückgegebenen Preisen erneut berechnet. |
| Crypto-Mikropreise kollabierten durch eine weitere Acht-Nachkommastellen-Rundung. | Sechs signifikante Stellen und erneute Prüfung der endgültig ausgegebenen CE-Preise; ungültige Geometrie oder zu kleines R/R wird nicht freigegeben. |
| Eine bestätigte erste Barriere ließ auch ein projiziertes TP2 „strukturell“ erscheinen. | Eigene Provenienz für Stop/TP1/TP2; gemischtes Zielpaar ausdrücklich `STRUCTURAL_TP1_PROJECTION_TP2`. |
| BI-Backtest handelte einen anderen Preisplan. | Gemeinsamer reiner Plan-Kern `modules/bi_trade_plan.py`. Die abweichende Daily-Ausführung bleibt ausdrücklich separat gekennzeichnet. |
| TP1 wurde nach vollständigem BE-Ausstieg noch anteilig gutgeschrieben. | Ereignisreihenfolge zuerst; nach vollständigem Exit kein späterer Gewinn. Unbelegte Reihenfolge bleibt unresolved. |
| Tageseröffnung am Ziel wurde von einem späteren Stop überstimmt. | Eröffnung und danach mögliche Ereignisse werden kausal ausgewertet, LONG und SHORT gespiegelt. |
| MFE/MAE enthielten Bewegung nach dem Exit. | Terminale Intrabar-Extrema nur als konservativ nachgewiesene Grenzen; keine erfundenen Tick-Pfade. |
| Unvollständiger Backtest-Horizont wurde als Abschluss gewertet. | `UNRESOLVED`, kein erfundenes PnL. Vollständiger EOD-Exit berücksichtigt modellierte Slippage. |
| Interne fehlende Handelstage wurden übersprungen und konnten Stops verstecken. | NYSE-Sitzungen bzw. explizit 24/7 bei Krypto prüfen. Lücke vor Exit/Entry ergibt `UNRESOLVED` ohne R/PnL; früher belegte Exits bleiben gültig. Fetch-Ausfälle sind als partielle Abdeckung ausgewiesen. |
| Unvollständige Datenqualität ging im API-Adapter verloren; fehlendes R erschien als 0R. | Qualitätsmetadaten bis zur UI weiterreichen. Fehlendes R/ØR bleibt unbekannt, unbestätigte Einstiege zählen nicht als Fill, fehlender PnL nicht als Nullverlust. Teil-Auswertung erhält keine positive Edge-/Paper-Freigabe, bekannte Ergebnisse bleiben diagnostisch sichtbar. |
| Fehlendes Funding wurde null, beliebige Intervalle acht Stunden. | Null von unbekannt getrennt, echte Börsenintervalle, explizite Einheiten und 8h-Vergleichsbasis nur bei bekanntem Intervall. |
| Crypto-Spread fehlte trotz vorhandener Geld-/Briefkurse; extrem weite Spreads waren nur ein Scoreabzug. | Native Tickerkurse verdrahtet, Binance-Book nur im qualifizierten Deep-Check. Unbekannt/ungültig blockiert; die bestehende Crypto-Long-Grenze von 20 Basispunkten wird auch hier angewandt. |
| OI und Volumen stammten teilweise von unterschiedlichen Börsen. | Gleicher Handelsplatz/Vertrag; Veränderung nur gegen zeitlich passende, gleichartige Basis. Fehlende Werte bleiben unbekannt. |
| Crypto-15m-Ersatzdaten wurden als 4H-Struktur bezeichnet. | Tatsächliches Quellintervall wird verwendet und ausgegeben. |
| Explizites Struktur-REJECT konnte in Crypto Explosion wieder zum Entry werden. | REJECT, WAIT und aktives Barrier-Gate blockieren die Freigabe unabhängig vom Zahlen-Score. |
| Erstes final unhandelbares Mail-Setup verdrängte die folgenden. | Kandidaten bis zum ersten final gültigen Setup prüfen; weiterhin maximal ein quote-naher Versand, Claims sauber freigeben. |
| BI-Detailaufruf verlor ausgewählten Plan/Richtung. | Unveränderlicher Auswahl-Snapshot, richtungsgebundener Detailaufruf, Abbruch alter Antworten, keine LONG-Priorität bei Mehrdeutigkeit. |

## 3. Alle Scanner nach demselben Prüfvertrag

Prüfvertrag: Asset/Identität → Datenquelle/Einheiten/abgeschlossene Kerzen →
Trigger und Richtung → Invalidierung/erste Gegenbarriere/R/R → Ausführung →
Tracking/Mail → UI-Aussage. „Geprüft“ bedeutet technische Vertragsprüfung und
zugehörige Regressionen, nicht lückenlose Marktvalidierung oder Profitabilität.

### Aktien

| Scanner/Familie | Ergebnis und wesentliche Grenze |
|---|---|
| BI LONG / SHORT | Strikt mindestens 17/20, alle 20 prüfbar, harte Blocker zusätzlich. Keine unter-17-Watchlist, kein solches Tracking, keine solche Mail. True Range mit Gaps, verbrauchte Liquidität entfernt, Fib chronologisch, RVOL ohne Eigenvolumen im Nenner. |
| Momentum Breakout Long | Historien- und Strukturpfad geprüft; Vortagesänderung einschließlich Gap korrekt close/close statt Kerzenkörper. Snapshot ist noch kein Fill. |
| Gap Momentum Long / Short | Richtung und gemeinsame Historien-/Geometriepfade geprüft. Gap-, Nachrichten-, Borrow- und Squeeze-Risiken bleiben. |
| Turtle im Strategiemenü | Echte Fehlzuordnung zu „consolidation“ behoben: Donchian20 plus eigener expliziter ATR-/R-Plan. |
| Separater Turtle | Nur abgeschlossener Signaltag; vollständiges Tagesvolumen wird nicht nochmals intraday hochgerechnet. Feste R-Ziele sind eine App-Variante, nicht die ursprüngliche offene Turtle-Exitstrategie. |
| Bull Flag / Bear Flag | Richtiger Pattern-Dispatch, abgeschlossene Historie und Mindestqualität. Pattern-Bestätigung ersetzt keinen frischen Entry-Trigger. |
| Compression Breakout | Abgeschlossene Historie, korrekte Vortagesbewegung; Selektivität nicht für mehr Treffer abgesenkt. |
| Cup and Handle Breakout | Geometrie, Mindestscore und Triggerpfad geprüft; kausale Historie. |
| Trend Reversal | Korrekte Vorperiodenänderung und abgeschlossener Verlauf. Kein Schutzversprechen gegen Falling Knives. |
| MA Bounce Long / Short | Richtung und Steigungen geprüft; Schlusskurslage 0 bleibt 0, statt fälschlich neutral 0,5. |
| Wyckoff Accumulation / Distribution | Abgeschlossene, validierte Kerzen. Volumen/OBV sind Indizien, kein Nachweis institutioneller Käufe/Verkäufe. |
| Biotech full / quick / offline | Unterschiedliche technische Wertungen vereinheitlicht; Polygon-Aliasfehler mit stillschweigendem Score 0 behoben. FDA-, Event- und Verwässerungsrisiko bleibt. |
| Penny / Rocket / Positionsmonitor | OHLC-/Duplikat-/Zeitprüfungen, vorhandene Entry-/Exit- und Liquiditäts-Gates geprüft. Halts und tatsächliche Markttiefe nicht durch Indikatoren garantiert. |
| ORB | Drei eindeutige, ausgerichtete Opening-Range-Intervalle statt lediglich drei Array-Zeilen. |
| Premarket / After Hours | Formende letzte Kerze ausgeschlossen, 16-Uhr-Startkerze nicht mehr als reguläre Session gewertet. Frühe Börsenschlüsse bleiben eine dokumentierte Kalendergrenze. |
| Bear / inverse ETF-Kontext | Extended-Hours-Fallback anhand der Session statt der Trefferzahl. ETF-Übersicht ist keine eigene Ausführungsstrategie. |
| Harmonic / Wolfe / SMC / Volume Void | Abgeschlossene direkte Datenpfade, verbrauchte Liquidität und gemeinsame Levels korrigiert. Historische Harmonic-Erfolgs-Priors sind unkalibriert; Projektionen kein Edge-Beweis. |
| Paper AutoTrader | Bewusst separates Modell mit eigenen strengeren Gates; nicht als identisch mit App/BI-Backtest ausgegeben. Keine Broker-Aktivierung aus diesem Audit. |

### Krypto und Kontext

| Scanner/Familie | Ergebnis und wesentliche Grenze |
|---|---|
| Early Movers: Volumen/Microcaps/Positioning | Fehlendes Funding gibt keine neutralen Akkumulationspunkte. Börsengleiches OI, getaktete Vergleichsbasis; Positioning ist keine bewiesene Whale-Akkumulation. |
| Crypto Explosion / Long Engine | Echte 5m/15m/4H-Zuordnung; Funding/Intervall und Spread unbekannt blockieren aktuelle Handelsfreigabe. Shared-REJECT wird nicht übergangen. |
| New Listing / Short Engine | Tatsächliche Börsenintervalle; fehlende Messwerte erhöhen den Score nicht über einen verkleinerten Nenner. MEXC-Contracts ohne Quoteamount sind keine erfundene Dollar-Liquidität. |
| Kombinierte Crypto Signals | Verwendet Long-/Short-Producer mit denselben Struktur-/Execution-Gates; keine unabhängige Erfolgsbehauptung für den Mix. |
| Active Pump | Kontext/Shortlist, kein eigenständiger unmittelbarer Trade ohne Trigger, Alter, Liquidität und Plan. |
| BTC-Divergenz | Bitcoin-Identität statt Symbolkollision, fehlende Vergleichsänderung nicht null; echte 4H-RSI-Kadenz, flache Reihe RSI50. Ranking ausdrücklich Kontext, keine Prozent-Trefferquote oder Short-Anweisung. Signal-only blendet nicht ausführbare Kontextzeilen aus. |
| Crash Monitor API und Background | Keine VIX-Erfindung aus UVXY, keine Entwarnung durch fehlende Daten. Teilscore/Abdeckung separat. Interner Score, nicht CNN-Index oder Verlustwahrscheinlichkeit. |
| Market Weather / Sektoren / Narrative / Money Flow | Vortagsvolumen wird nicht nochmals mit dem heutigen Intraday-Faktor hochgerechnet. 5D/20D benötigen echte 6/21 Kurse; fehlende Horizonte und CMF/OBV/RVOL bleiben unbekannt. Formende Tagesdaten sind ausdrücklich markiert. Kontext, keine ausgeführten Trades. |
| Generische CoinGecko-Strategien (11 Varianten) | Sie bleiben Beobachtungs-/Kontextpfade ohne eigene Handelsfreigabe. Die bisher überlappende 7D/7-Näherung ist durch einen expliziten geometrischen 6D-Proxy ohne die aktuellen 24h ersetzt. Dieser ist kein tatsächlicher Vortageskurs und kein täglich gemessener Verlauf. |
| Volume Spikes / generische Legacy-Strategien | Trigger-/Datenpfade und gemeinsame Mail-/Plan-Gates getrennt von einer tatsächlichen Handelsfreigabe. Dormante Background-Pfade wurden nicht neu aktiviert. |

Inventur und Prüfungstiefe: Alle 20 Scheduler-/Cache-Schlüssel sind zugeordnet:
`quote_capability`, `cup_handle_watch`, `bi_long`, `bi_short`, `bear`, `biotech`,
`early_movers`, `crypto_trade_signals`, `crypto_explosion`, `crash_monitor`,
`market_context`, `btc_divergenz`, `money_flow`, `new_listing`, `volume_spikes`,
`penny_stocks`, `penny_positions`, `orb`, `turtle`, `strategy_scan`.
`quote_capability` ist ein Betriebsmonitor, keine Strategie. `penny_positions`
wurde als bestehender Paper-Monitor samt Pipeline eingeordnet, nicht vollständig
neu pro Ausführungspfad bewiesen. Das komplette Marktregime-Modell wurde nicht
empirisch neu validiert. Volume Spikes verwendet einen Tages-/Vortages-Pace-Proxy,
kein 20-Tage-RVOL. Diese Grenzen dürfen durch „alle Scanner auditiert“ nicht
verschwinden. Die vorhandene Cup-Triggerqueue ist keine neue BI-Watchlist.

## 4. UI-Verifikation

Die echte gebaute Oberfläche lief auf `127.0.0.1:8765` mit einer klar markierten
synthetischen Fixture, ohne API-Import, echte Zugangsdaten, Versand oder Orders.
Reproduzierbar mit `scripts/audit_ui_fixture.py` (nur lokale QA, kein Produktserver).

- Desktop und 390×844: BI LONG/SHORT-Auswahl, 17/20-Zähler, 20 aufklappbare
  Einzelprüfungen; absichtlich widersprüchliche LONG-Detailantwort überschreibt
  den ausgewählten SHORT-Plan nicht (Entry100, Stop105, TP190, TP285).
- Snapshot-Datum/Version sichtbar; aktuelle Detailquote wird als solche und nicht
  als Scannerpreis bezeichnet. Fehlende zugehörige Änderung bleibt unbekannt.
- Strukturelles TP1 und projiziertes TP2 getrennt beschriftet.
- Crash bei fehlenden Daten: Strich plus „DATEN UNVOLLSTÄNDIG“, kein erfundener
  Null-/50-Punkte-Score, UVXY nicht als VIX. Fehlende A/D-Aussage nicht „Neutral“.
- Mobile Crash-Seite ohne horizontalen Overflow (Dokument375, Viewport390).
- Die vier Sidebar-/Crypto-/Backtest-Preisformatter wurden anschließend mit
  tatsächlich ausgeführtem JavaScript geprüft: Mikropreise bleiben mit sechs
  signifikanten Stellen getrennt; unbekannt/bool/leer wird kein Nullpreis.
- Abschließender Backtest-Browserfall (Desktop und 390×844): zwei ungeklärte
  Signale/eine fehlende Sitzung werden sichtbar; keine Paper-Freigabe,
  fehlendes R als „—“, Mikropreise `1.01200e-8` und `1.02212e-8` unterscheidbar.
  Kein horizontaler Seiten-Overflow, keine Browserfehler. Die Daten waren
  synthetisch; der QA-POST liefert nur eine feste Antwort und führt keinen
  Backtest aus. Lokaler Testserver und Browser-Tabs danach geschlossen.
- Keine Browser-Konsolenfehler im geprüften Ablauf; bekannte Warnung der lokal
  vendorten Tailwind-Runtime bleibt. Temporäre Viewport-Änderung zurückgesetzt.
- Leerzustand des Charts geprüft; keine Behauptung über reale aktuelle Chartdaten
  oder browserseitige Live-Backtest-Ausführung.

## 5. Versionen, Daten und Rollout

- Stock-BI-Contract `stock-bi-20-v2`; alte `v1`-Cachezeilen werden abgewiesen.
  Die Mindestzahl bleibt **17**, nicht abgesenkt. Nach Deploy kann bis zum neuen
  Scan eine leere BI-Liste korrekt sein.
- Stock-Strategiecache Version7. Neue Pläne `invalidation_first_v2`, gemeinsamer
  BI-Plan `bi_shared_structure_v3`.
- Neue Tracker-Auswertungen `causal-legs-extrema-v2`, additive nullable Felder
  für Modell/Ereignisreihenfolge/Extrema-Evidenz. Keine pauschale DB-Reparatur,
  kein Replay historischer geschlossener Zeilen, keine Stash-/Datei-Löschung.
- BE-Summaries können sich durch korrigierte Arithmetik ändern; Rohhistorie bleibt.
  Das ist von neuen Ergebnissen der neuen Scanner-Version zu unterscheiden.
- BI-Daily-Modell `daily_next_session_50_50_be_after_tp1_v2` ist ausdrücklich
  **nicht** identisch mit SMTP-Zeitpunkt, Intraday-Fill oder sämtlichen Live-Gates.
- Backtest `data_quality` weist Fetch-Ausfälle und Sitzungs-Lücken auch bei
  leeren Kohorten aus. Undatierte Legacy-/synthetische Barfolgen sind ausdrücklich
  `legacy_bar_sequence_unverified`, kein Nachweis vollständiger Marktdaten.
- Preiseweg-R ist keine Netto-Kontorendite. Allgemeine Roundtrip-Kosten für
  Gebühren/Spread/Borrow/Funding fehlen weiterhin im Tracker; die UI sagt das.
- Private Mailarchive und `output/` bleiben lokal und werden nicht gepusht.
- Server-Pull/Services/Health/Bundle und Cron sind separat zu verifizieren.
  Dieses Paket richtet bewusst keinen alten Root-Cron wieder ein.

Erwartbare fachliche Auswirkungen, keine Erfolgsversprechen: Ein ehrlicher
Struktur-Stop kann weiter entfernt liegen, das ausgewiesene R/R dadurch sinken
und ein Setup korrekt entfallen. Eine schlechtere ausgewiesene Zahl nach
Messkorrektur bedeutet nicht automatisch schlechteres neues Trading. Umgekehrt
beweist eine bessere Anzeige noch keinen echten Gewinn. Fehlende Daten oder ein
zu weiter Spread werden nicht durch mehr Indikatorpunkte kompensiert.

## 6. Was für einen belastbaren Handelsvorteil weiterhin nötig ist

1. Neue unveränderte Forward-Kohorten getrennt nach Scanner, Richtung, Asset,
   Strategie-/Codeversion, Marktregime und tatsächlichem Versand/Fill sammeln.
   Reine App-Signale sind nicht automatisch dieselbe Population wie Mail-Signale.
2. Netto-Erwartungswert, Profit-Faktor, Drawdown, Ausführungs-/Datenlücken und
   Unresolved-Anteil auswerten; offene junge Trades nicht nur mit schnellen Stops
   vergleichen. Keine Auswahl ausschließlich geschlossener Gewinner.
3. Zeitliche Out-of-sample-/Walk-forward-Prüfung mit Kosten und überlappenden,
   korrelierten Signalen; getrennte Asset-/Richtungsresultate. Bestehende n30-Gates
   sind eine technische Mindesthürde, kein statistischer Profitabilitätsbeweis.
4. Indikatorfamilien auf Doppelzählung/Ablation prüfen. 17/20 bedeutet **nicht**
   85% Gewinnwahrscheinlichkeit; RSI, Stochastic, MACD und MAs sind korreliert.
   Keine Lockerung der vom Nutzer gewünschten Grenze und kein unter-17-Tracking.
5. Exitvarianten nur prospektiv oder kausal in isolierten Studien vergleichen:
   50/50, späteres BE, Trailing und struktureller Exit. Kein nachträgliches
   Herauspicken des profitabelsten Targets und keine Gewinn-Garantie.

Diese Forschungs-/Produktionsnachweise sind offen. Das Reparaturpaket ersetzt sie
nicht, und schlechte Zahlen werden nicht durch höhere Scores oder mehr Mails
„repariert“.

## 7. Abnahme

Vollständiger, während der Ausführung unveränderter Gesamtlauf am 05.09.2026:
**3138 bestanden, 4 übersprungen, keine Fehler, 700,49 Sekunden**. Alle geprüften
Code-Dateihashes waren vor/nach dem Lauf identisch. XML: lokal
`tmp/audit_final.xml`. Die vier Skips betreffen Windows-Symlinkrechte sowie
Linux-spezifische O_DIRECTORY/O_NOFOLLOW/FIFO-/Rename-Prüfungen; sie sind kein
Linux-Produktionsnachweis.

Danach wurde ausschließlich die abschließende Backtest-API-/UI-Weitergabe von
Datenqualität und fehlendem R ergänzt. Anschließend bestanden **alle 3014
Nicht-Deployment-Tests erneut** in 122,79 Sekunden, ohne Fehler oder Skips
(`tmp/audit_release_final.xml`). Die 165 Deploymentfälle sind unverändert:
161 bestanden im Gesamtlauf, vier plattformspezifische Skips wie oben. Damit
sind 3175 unterschiedliche bestandene Fälle und vier Skips abgedeckt, nicht
etwa 3138+3014 unabhängige Tests. Auch die Code-Dateihashes des finalen
Wiederholungslaufs blieben identisch. Kein Test wurde zum Verbergen eines
Fehlers entfernt.

Weitere fokussierte Evidenz: Stock306, Tracker551, Shared/NLS221, unabhängige
Krypto-Messverträge85, finaler Root-Integrationsblock195 Tests bestanden. Diese
Zahlen überlappen und dürfen nicht zu einer Gesamtsumme addiert werden.

55 geänderte/neue Python-Dateien kompilierten; JavaScript-Syntax,
Bundle-Verifikation, `git diff --check` und Credential-Musterscan waren grün.
Quell-Bundle: `cc0d82106285`; Bundle-SHA-256:
`c3398f8bbae6f3f4cd53852a8117ba8f8181c770c5b969aafe021131df6c0ddc`.
Der Commit enthält ausschließlich das geprüfte Code-/Test-/Dokumentationspaket.
GitHub-Push ist kein Deployment: Server, DB-Backup/Schema-Rollout, Services,
API-Revision, Bundle und neue Forward-Kohorten sind separat zu prüfen.

Primärquelle für Bybit-Minutenkontrakt:
[Bybit Instruments Info](https://bybit-exchange.github.io/docs/v5/market/instrument).
Weitere Einheitenverträge:
[MEXC](https://mexcdevelop.github.io/apidocs/contract_v1_en/#get-contract-funding-rate),
[Bitget](https://www.bitget.com/api-doc/uta/public/Get-Current-Funding-Rate),
[Binance](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info).
Top-of-Book-Felder gegen die offiziellen Verträge geprüft:
[Binance Book Ticker](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Order-Book-Ticker),
[Bitget Classic Tickers](https://www.bitget.com/api-doc/classic/contract/market/Get-All-Symbol-Ticker),
[MEXC Ticker](https://mexcdevelop.github.io/apidocs/contract_v1_en/#get-contract-ticker-information).
