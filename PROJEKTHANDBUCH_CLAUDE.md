# Alpha Station - Projekthandbuch und Claude-Übergabe

**Stand:** 18. Juli 2026
**Repository:** `C:\Users\miros\Desktop\TradingBot`
**Produktname:** Alpha Station
**Zweck dieses Dokuments:** Belastbare Übergabe an Claude oder einen anderen Senior-Entwickler, ohne den gesamten Chatverlauf erneut rekonstruieren zu müssen.

> Wichtig: Dieses Dokument unterscheidet konsequent zwischen dem letzten eingecheckten Git-Stand, dem aktuellen lokalen Arbeitsstand und dem tatsächlich auf dem Server ausgerollten Stand. Diese drei Stände dürfen niemals still gleichgesetzt werden.

## 1. Kurzfassung

Alpha Station ist eine webbasierte Trading-Intelligence-Plattform für Aktien und Kryptowährungen. Das Produkt soll Märkte breit scannen, aus vielen Kandidaten wenige verständliche Trading-Signale ableiten, deren Ausführbarkeit und Risiko getrennt bewerten und diese Signale über Weboberfläche, E-Mail und optional Telegram bereitstellen.

Das Ziel ist **nicht**, Gewinne zu garantieren oder Kaufentscheidungen blind zu automatisieren. Das Ziel ist, Daten, Setup-Qualität, Timing, Liquidität, Struktur, Risiko und Marktumfeld so zusammenzuführen, dass ein Trader schneller und konsistenter entscheiden kann.

Der aktuelle technische Stand ist weit fortgeschritten:

- FastAPI-Backend mit statischem React-Frontend
- Hintergrunddienst für Scanner, E-Mails und Signal-Tracking
- Aktien-, Krypto-, Biotech-, BI-, ORB-, Bear-, Crash-, Penny- und Strategie-Scanner
- Strukturbasierte Entry-, Stop-, TP1- und TP2-Pläne
- VRVP-, Support-/Resistance-, ATR-, Fibonacci- und Mehrzeitebenen-Kontext
- Backtesting, Signal-Performance und Exit-Auswertung
- Authentifizierung, Abomodelle und Stripe-Integration
- Produktions-Deployment mit nginx, TLS-Prüfung, systemd-Hardening, Preflight-Tests und Rollback
- Umfangreiche Regressionstests

Der letzte eingecheckte Commit ist aktuell:

```text
3efcfed Harden penny stock execution scanner
```

Der lokale Arbeitsbaum enthält danach jedoch eine große, noch nicht eingecheckte kommerzielle Härtung. Dieser lokale Stand wurde zuletzt mit **887 bestandenen Tests**, Frontend-Bundle-Prüfung, Python-Compile-Checks, Shell-Syntaxprüfung, Browser-Smoke-Test und `pip-audit` ohne bekannte Paket-Schwachstellen verifiziert. Vor jeder Übergabe oder Bereinigung muss dieser Stand zuerst gesichert und bewusst committed werden.

## 2. Produktvision

Alpha Station soll für einen aktiven Trader folgende Fragen beantworten:

1. Welche Aktien oder Coins besitzen gerade ein wirklich relevantes Setup?
2. Ist das Setup nur interessant oder bereits ausführbar?
3. Wo liegt ein fachlich begründeter Entry?
4. Wo ist die Idee objektiv invalidiert?
5. Wo liegen plausible TP1- und TP2-Ziele aus echter Marktstruktur?
6. Ist das Chance-Risiko-Verhältnis am aktuellen Live-Preis noch tragbar?
7. Ist der Trade bereits gechased, überdehnt, illiquide oder fakeout-gefährdet?
8. Unterstützen Marktregime, BTC-Kontext, News, Events und Sektorrotation die Idee?
9. Wie haben vergleichbare Signale historisch und seit Versand tatsächlich abgeschnitten?

Die Oberfläche soll nicht wie eine endlose Watchlist wirken. Der Betreiber möchte primär **aktive Trading-Ideen** sehen. Wartesignale dürfen optional sichtbar sein, müssen aber eindeutig von handelbaren Signalen getrennt werden.

## 3. Nicht-Ziele und ehrliche Grenzen

- Kein Scanner kann einen Trade garantiert richtig vorhersagen.
- Score oder Grade sind keine Gewinnwahrscheinlichkeit.
- Ein Backtest ist keine Garantie für Live-Ergebnisse.
- Eine hohe Setup-Qualität bedeutet nicht automatisch, dass der Einstieg jetzt sinnvoll ist.
- Social-, News- oder Politikdaten dürfen keine vermeintliche Gewissheit oder Fake-News-Prognose erzeugen.
- Das System darf keine personalisierte Anlageberatung oder garantierte Rendite versprechen.
- Ein Ziel darf nicht künstlich nur deshalb gesetzt werden, damit das R:R gut aussieht.
- Ein fehlendes Signal ist besser als ein widersprüchliches oder datenmäßig nicht belegtes Signal.

## 4. Betreiberprioritäten

Diese Produktentscheidungen wurden im Projekt wiederholt und ausdrücklich festgelegt:

| Priorität | Verbindliche Auslegung |
|---|---|
| Aktive Signale statt Watchlisten | Standardansicht zeigt handelbare oder klar vorbereitete Ideen. Breite Beobachtungslisten nur optional. |
| Klarheit vor Informationsmenge | Ein Trader muss Aktion, Entry, Stop, Ziele und Hauptgrund in Sekunden verstehen. |
| Keine Widersprüche | Tabelle, Sidebar, Chart, E-Mail und Reminder müssen denselben finalen Zustand zeigen. |
| Swing und Intraday trennen | Swing verwendet Daily-/4H-Struktur; ORB und echte Execution-Scanner dürfen frische 5m-Bestätigung verlangen. |
| Setup und Timing trennen | Setup-Score beschreibt Qualität. Execution-Score und finaler Status beschreiben, ob jetzt gehandelt werden darf. |
| Struktur statt Fantasie-R:R | Stops und Ziele kommen aus Invalidation, S/R, VRVP, ATR, Fib, Swing-Levels oder Measured Move. |
| Live-Daten ernst nehmen | Stale, partielle oder venue-fremde Daten dürfen nicht als frisches Handelssignal erscheinen. |
| Lieber etwas breiter scannen | Nicht so streng filtern, dass gute Kandidaten vollständig verschwinden. Harte Risiken trotzdem blockieren. |
| Keine Provider-Werbung im Produkt | Externe Anbieter dürfen intern dokumentiert sein, aber nicht unnötig in der Kundenoberfläche erscheinen. |
| Mobile Bedienbarkeit | Scanner, Karten, Tabellen und Sidebar müssen unterwegs verständlich und bedienbar bleiben. |

## 5. Kanonisches Signalmodell

Historisch entstanden viele Fehler, weil mehrere Komponenten eigene Begriffe und eigene Entscheidungen erzeugten. Künftig darf es nur einen kanonischen finalen Signalzustand geben.

### 5.1 Drei getrennte Bewertungen

| Dimension | Bedeutung | Darf allein `JETZT TRADEN` auslösen? |
|---|---|---|
| Setup-Score | Qualität der übergeordneten Idee, Struktur, Momentum, Volumen, Kontext | Nein |
| Execution-Score | Qualität des aktuellen Einstiegs, Trigger, Distanz, Candle, Spread, Live-R:R | Nein |
| Finaler Entscheid | Zusammenführung aller harten Gates und Risiken | Ja |

Ein S-Setup kann korrekt `WARTEN` oder `NO_TRADE` sein. In diesem Fall muss die UI den Grund klar nennen und darf S nicht wie eine sofortige Kaufempfehlung präsentieren.

### 5.2 Erlaubte finale Zustände

| Zustand | Bedeutung |
|---|---|
| `TRADE_NOW` / `JETZT_TRADEN` | Alle harten Gates bestanden, aktueller Einstieg noch gültig |
| `WAIT_FOR_TRIGGER` | Gutes Setup, aber bestätigender Trigger fehlt |
| `WAIT_FOR_RETEST` | Ausbruch oder Impuls vorhanden, Einstieg erst am sauberen Retest |
| `MANAGE` / `HOLD` | Bereits ausgelöstes Signal wird weiter verwaltet |
| `EXIT` | Invalidation, Stop, Ziel oder Exit-Regel ist eingetreten |
| `NO_TRADE` | Setup aktuell nicht handeln; harter Blocker oder ungültige Geometrie |

`BEOBACHTEN` darf höchstens eine kundenfreundliche Anzeige für einen genau definierten `WAIT_*`-Zustand sein. Es darf nie spezifische Timing-Information vernichten.

### 5.3 Harte Invarianten

Für Long gilt immer:

```text
Stop < Entry < TP1 < TP2
```

Für Short gilt immer:

```text
Stop > Entry > TP1 > TP2
```

Zusätzlich gilt:

- TP1 und TP2 dürfen nicht identisch sein.
- Ein bereits verpasstes Ziel darf nicht mehr als zukünftiger Reward zählen.
- `abs(entry - target)` darf falsche Richtung nicht kaschieren.
- Ein gerissener Stop ergibt sofort `NO_TRADE` oder `EXIT`.
- Live-R:R muss aus aktuellem Preis neu berechnet werden.
- Alter Scanner-R:R darf bei vorhandenem Live-Preis nicht wiederverwendet werden.
- Ein Chart-Timeframe darf keine anders aggregierten Daten vortäuschen.
- Kein `JETZT_TRADEN`, wenn die Sidebar gleichzeitig `CRITICAL`, Stop gerissen, Daten stale oder harter No-Chase sagt.
- Kein Stock-Trade-Alert außerhalb des dafür definierten Handels- oder Swing-Mailfensters.

## 6. Swing versus Intraday

Eine der wichtigsten Lehren des Projekts: Nicht jeder Scanner darf mit demselben 5m-Trigger behandelt werden.

| Modus | Primäre Timeframes | Typische Verwendung |
|---|---|---|
| Swing | Daily und 4H, optional Weekly-Kontext | Momentum Breakout, Gap Momentum, BI, Biotech, Turtle, Cup & Handle |
| Intraday | 5m mit höherem 15m-/1H-Kontext | ORB, Penny-Execution, frische Krypto-Execution |
| Hybrid | Swing-Setup plus optionaler 5m-Einstieg | Early Entry/Starter und Add-on am bestätigten Breakout |

Verbindliche Regel:

- Ein Swing-Signal darf nicht allein wegen eines fehlenden 5m-Triggers verworfen werden.
- Ein Intraday-Signal darf nicht allein aus einer alten Daily- oder 4H-Struktur `JETZT_TRADEN` werden.
- 1m-Daten sind zu noisy für die zentrale Ausführungsentscheidung und wurden deshalb weitgehend durch 5m ersetzt.
- Der verwendete Modus muss in UI, E-Mail, Backtest und Signal-Tracker gespeichert werden.

## 7. Zielarchitektur

### 7.1 Produktionskomponenten

| Komponente | Datei / Dienst | Aufgabe |
|---|---|---|
| FastAPI | `api.py`, `tradingbot-api.service` | API, Auth, Scanner-Orchestrierung, UI-Daten, Commerce |
| Hintergrunddienst | `bg_service.py`, `tradingbot-bg.service` | schwere Scans, E-Mails, Signal-Tracking, Wochenreport |
| Frontend | `frontend/index.html`, `frontend/app.bundle.js`, `frontend/boot.js` | statische React-Oberfläche |
| nginx | `deploy/nginx-tradingbot.conf` | TLS, statische Dateien, API-Proxy, Rate-Limits, Security-Header |
| Persistente Daten | `data_cache/` | Auth-DB, Runtime-Cache, Dedupe, Signal-Tracker |
| Deployment | `deploy/safe_deploy.sh` | Preflight, Tests, Pull, Restart, Healthcheck, Rollback |

Produktionsfluss:

```text
Browser
  -> HTTPS nginx :443
     -> statisches Frontend
     -> /api/* auf FastAPI 127.0.0.1:8000
FastAPI und Background Service
  -> gemeinsamer service-eigener Runtime-Cache
  -> externe Markt-/Catalyst-/News-/Stripe-/Mail-Dienste
```

### 7.2 Nicht mehr Teil der Zielarchitektur

- Streamlit auf Port `8501`
- separater Python-HTTP-Frontend-Dienst auf Port `3000`
- öffentlich erreichbares Uvicorn auf Port `8000`
- Runtime-Babel im Browser
- CDN-Abhängigkeit für React/Babel/Charts beim Boot
- Service-Python unter einem abweichenden `/root/venv`

Alte Dateien wie `scanner.py` enthalten weiterhin Legacy-Logik. Sie sind historische Referenz, aber nicht die kommerzielle Hauptanwendung.

## 8. Wichtige Dateien

| Pfad | Rolle | Risiko bei Änderungen |
|---|---|---|
| `api.py` | Monolithischer API-, Scanner-, Alert- und Scheduler-Kern | Sehr hoch |
| `bg_service.py` | Hintergrundläufe, E-Mail, Tracker, Wochenreport | Sehr hoch |
| `frontend/index.html` | React-Quellcode und UI | Sehr hoch |
| `frontend/app.bundle.js` | generiertes, produktives Frontend-Bundle | Nicht manuell editieren |
| `frontend/boot.js` | Boot-Fehlerdiagnose | Hoch |
| `modules/scanners.py` | BI und weitere Scannerlogik | Hoch |
| `modules/penny_stock_scanner.py` | Penny-Lifecycle und Execution | Hoch |
| `modules/new_listing_scanner.py` | Krypto-Listings, Pump/Crack, Safety | Hoch |
| `modules/trade_health.py` | finale Ausführbarkeits- und Risikobewertung | Kritisch |
| `modules/trade_levels.py` | Entry-/Stop-/Target-Geometrie | Kritisch |
| `modules/vrvp_levels.py` | Volumenprofil und strukturelle Levels | Kritisch |
| `modules/market_context.py` | Markt-, News- und Eventkontext | Hoch |
| `modules/backtests.py` | historische Simulationen | Hoch |
| `modules/signal_tracker.py` | Forward-Tracking real versandter Signale | Hoch |
| `modules/auth.py` | Benutzer, Pläne, Stripe, JWT, Rechte | Kritisch |
| `modules/email_dedupe.py` | prozessübergreifendes E-Mail-Dedupe | Kritisch |
| `deploy/verify_commercial_edge.sh` | TLS-/Port-/Edge-Prüfung | Kritisch |
| `COMMERCIAL_LAUNCH_CHECKLIST.md` | verbindliche Launch-Gates | Kritisch |

## 9. Scannerbestand

### 9.1 Aktien

| Scanner / Bereich | Zweck | Kernlogik |
|---|---|---|
| Strategie-Scanner | verschiedene Long-/Short-/Swing-Strategien | Volluniversum, Strategie-Gates, Struktur, RVOL, Score |
| Momentum Breakout Long | echte Momentum-/Strukturausbrüche | Breakout-Level, RVOL, Candle-Qualität, Wick-Risiko, S/R-Barrieren |
| Gap Momentum Long/Short | fortsetzbare Gaps | Gap-Richtung, RVOL, Struktur, kein verspätetes Chasen |
| BI Long/Short | mehrfaktorielle institutionelle Setups | Trend, Momentum, Volumen, Struktur, technische Konfluenz |
| Biotech | Catalyst- und Event-Risiko | Catalyst, Dilution, CRL, Trial-Fail, Sell-the-News, Halt-Risiko |
| Bear / Crash | Short- und aktive Flush-Situationen | aktuelle Candle, Restpotenzial, kein Short nach bereits beendetem Drop |
| ORB | Opening Range Breakout | vollständige 15m-OR, aktueller Ausbruch, frisches Volumen, Marktzeit |
| Turtle | Daily Breakout | Turtle-Level, ATR, strukturierter Plan |
| Cup & Handle | Daily/Weekly Swingpattern | Cup-Geometrie, Handle, Breakout, kalibrierter Score |
| Volume Spikes | ungewöhnliches Aktienvolumen | Vollständige Daten, Common-Stock-Filter, Liquidität |
| Penny Stocks | Pump-Aufbau, bestätigte Entries und Exit-Lifecycle | Float, Dollarvolumen, 5m, Daily, VRVP, News/Offering/Reverse-Split-Risiko |
| Premarket | Vorbörsliche Movers | Premarket-Preis, Volumen, Gap und Datenfrische |

### 9.2 Kryptowährungen

| Scanner / Bereich | Zweck | Kernlogik |
|---|---|---|
| Early Movers | frühe Long-Kandidaten | breites Coin-Universum, Phase, BTC-Kontext, Venue-Liquidität, 5m-Execution |
| Crypto Explosion | bevorstehende oder bestätigte Long-Ausbrüche | exchange-native Perps, 5m/15m/4H, Coil, Reclaim, Volumen, OI/Funding |
| Crypto Trade Signals | einheitliche Long-/Short-Entscheidung | Long-Explosion plus Short-Pump/Crack, Konfliktauflösung |
| New Listings | neue Listings überwachen und handeln | Listing-Alter, Pump, erste Strukturbrüche, Safety, Orderbook, 5m-Crack |
| Pump & Dump / Short | Short erst nach bestätigter Schwäche | Pump vorher, Crack/Rejection jetzt, nicht blind gegen laufenden Pump shorten |
| BTC-Divergenz | relative Coin-Stärke oder -Schwäche zu BTC | datumssynchrone Renditen, Korrelation, Beta, BTC-Regime, Long/Short-Gate |
| Money Flow | Kapitalrotation | Sektor-/Narrativdaten und Relative Strength |
| Crypto Volume Spikes | ungewöhnlicher Flow | Venue-Daten, Liquidität und kein Stable-/Wrapped-/Leveraged-Token |

### 9.3 Tools und Kontext

- Marktstatus und Börsenöffnungszeiten
- offizieller Wirtschaftskalender
- fünf wichtige Börsenkalender inklusive Feiertagen
- Market Weather / Marktregime
- Crash Monitor
- Sektor- und Narrative Pulse
- Signal-Performance und Wochenreport
- Backtest Center mit Fortschritt
- Chartanalyse mit EMA, VWAP, S/R, Fibonacci, Bollinger, VRVP, Pattern und Volumen
- Trade-Reminder mit Mail- und Browserbenachrichtigung
- Watchlist als separates Nutzertool, nicht als Ersatz für Scanner-Signale

## 10. Datenquellen und Datenqualität

Interne technische Quellen dürfen dokumentiert werden; die öffentliche UI soll möglichst provider-neutral bleiben.

| Datenart | Technischer Ursprung |
|---|---|
| US-Aktien-Snapshots, Bars, Referenztypen, News | Polygon |
| Krypto-Marktuniversum | CoinGecko, nur mit Partial-/Rate-Limit-Kennzeichnung |
| Krypto-Execution | Binance, Bybit, MEXC, Bitget und weitere unterstützte Venues |
| Biotech-Catalysts | Premium-Catalyst-API plus interne Normalisierung |
| AI-Analyse | optionaler externer AI-Anbieter |
| Zahlungen | Stripe |
| E-Mail | Gmail SMTP mit App-Passwort |

Verbindliche Qualitätsregeln:

- CoinGecko-Gesamtvolumen ist kein Ersatz für das Volumen am konkreten Handelsplatz.
- Ein Coin darf nicht mit Daten einer anderen Coin-ID oder eines gleichnamigen Symbols gemappt werden.
- Exchange, Instrumenttyp, Spot/Perp und Quote-Währung müssen sichtbar und konsistent sein.
- Partielle API-Antworten dürfen nicht als vollständiger Scan gecacht werden.
- HTTP 401, 429 oder Timeouts müssen als Fehler/partial/stale sichtbar werden.
- Aktienuniversen müssen Common Stocks beziehungsweise bewusst erlaubte ADRs enthalten; ETFs, ETPs, Warrants und gehebelte Produkte werden ausgeschlossen, sofern der Scanner sie nicht explizit behandelt.
- Chartpreis, Headerpreis, Setup-Preis und letzte Kerze müssen denselben Split-/Adjustment- und Freshness-Kontext verwenden.

## 11. Entry, Stop und Targets

### 11.1 Level-Priorität

Stops und Ziele werden nicht zufällig und nicht nur über gewünschtes R:R gesetzt. Geeignete Quellen sind:

1. objektive Setup-Invalidation
2. Swing High / Swing Low
3. mehrfach bestätigter Support / Resistance
4. VRVP POC, VAH, VAL, HVN und LVN
5. höherer Timeframe, bevorzugt 4H, Daily und Weekly
6. ATR-Puffer
7. direktionale Fibonacci-Level
8. Measured Move oder Range Extension
9. gleitende Durchschnitte nur, wenn sie tatsächlich strukturell relevant sind

### 11.2 Mehrzeitebenen-Barrieren

Vor einem Long-Trade muss geprüft werden, ob direkt über dem Entry eine starke 4H-, Daily- oder Weekly-Resistance liegt. Vor einem Short gilt das spiegelbildlich für Support.

Eine sichtbare Barriere darf nicht ignoriert werden, nur weil ein theoretisches R:R dahinter attraktiv aussieht. Liegt TP1 hinter einer ungelösten starken Barriere, muss entweder:

- TP1 vor die Barriere gelegt werden,
- der Entry auf einen bestätigten Durchbruch warten,
- oder der Trade als unattraktiv blockiert werden.

### 11.3 Early Entry und Main Entry

Für geeignete Swing-Breakouts kann ein zweistufiger Plan sinnvoll sein:

| Stufe | Bedeutung |
|---|---|
| Starter / Anticipation | kleine Position bei bestätigtem Higher Low, VWAP-/VAH-/EMA-Hold und tragbarer Struktur |
| Main / Breakout | Add-on erst oberhalb des objektiven Breakout-Levels mit Bestätigung |

Ein Starter darf nur erscheinen, wenn echte Struktur unter oder am aktuellen Kurs hält. Ein knapp über dem Kurs liegendes Level ist kein Hold. Ist das Main-Level zu weit entfernt, darf kein künstlicher Starter erzeugt werden.

## 12. Score, Grade und Risiko

Historisch war dies eine der größten UX-Fehlerquellen.

| Feld | Bedeutung |
|---|---|
| Grade | relative Qualität des Setups innerhalb des Scanners |
| Setup-Score | Stärke der Grundidee |
| Entry-/Execution-Score | Qualität des Einstiegs jetzt |
| Entry-Risk | Ausführungsrisiko am aktuellen Preis |
| Fakeout-Risk | Risiko eines nicht haltenden Ausbruchs |
| Chase-Risk | Risiko, zu weit nach dem idealen Entry einzusteigen |
| Market Risk | allgemeines Marktumfeld, nicht identisch mit Trade-Risiko |

Ein hoher Score und `MEDIUM` Risk sind möglich: Der Coin oder die Aktie kann strukturell stark sein, aber der aktuelle Einstieg noch riskant. Die UI muss diese Unterscheidung erklären, ohne widersprüchliche Handlungsanweisungen zu erzeugen.

Score-Schwellen sind scannerabhängig und müssen kalibriert werden. Sie dürfen nicht so gewählt werden, dass fast jede Row 90+ erhält. Cup & Handle wurde deshalb bereits per Backtest und Walk-Forward nachkalibriert.

## 13. Alerts und E-Mails

### 13.1 Mailklassen

| Klasse | Betreff / Bedeutung | Empfänger |
|---|---|---|
| Trade | `JETZT` | nur bei handelbarem Signal und passender Präferenz |
| Watch | `WATCH` | nur mit explizitem Watch-Opt-in |
| Info | Kalender, Narrative, Wochenreport | nur mit passender Präferenz |
| Exit | Stop, Ziel, Invalidierung oder Management-Update | Empfänger des Ursprungssignals |

### 13.2 Pflichtfelder einer Trade-Mail

- Symbol und Instrument
- Long oder Short
- Scanner beziehungsweise Strategie
- Signalzeit und Datenzeit
- aktueller Preis
- Entry
- Stop
- TP1
- TP2
- effektives R:R
- Setup- und Execution-Qualität
- Hauptrisiko oder Blocker
- Timeframe beziehungsweise Modus
- konkrete Level-Quellen

### 13.3 Mail-Gates

- Trade-Mails grundsätzlich erst ab der produktseitig festgelegten Mindestqualität, derzeit meist Score 80 und Grade S/A/A+.
- Kein Versand bei stale/partial Daten.
- Kein Versand bei gerissenem Stop oder verpasstem TP1.
- Kein Versand bei ungültiger Plan-Geometrie.
- Kein Versand, wenn finaler Health-State nicht handelbar ist.
- Kein Intraday-Aktienalert außerhalb des gültigen Marktfensters.
- Keine Wiederholungsmail nur wegen kleiner Score-Änderung.
- Persistentes, prozessübergreifendes Dedupe verwenden.
- Bear- und Crash-Mail dürfen denselben Ticker nicht unkontrolliert doppelt senden.
- Watch-Mails dürfen nie wie Kauf-/Verkaufsempfehlungen formuliert sein.

## 14. Signal-Tracking und Backtests

Zwei Arten von Evidenz müssen getrennt bleiben:

| Verfahren | Zweck |
|---|---|
| Historischer Backtest | Strategieidee auf historischen Daten prüfen |
| Forward-Tracking | tatsächlich versandte Signale nach Versand auswerten |

Backtest und Live-Regeln müssen dieselben Definitionen für Entry, Stop, Targets, RVOL, Richtung, Timing und Kosten verwenden. Wenn Intraday-Daten fehlen, muss der Backtest die Approximation offen kennzeichnen.

Mindestmetriken:

- Anzahl Trades
- Win Rate
- Profit Factor
- Expectancy in R
- Max Drawdown
- Durchschnittsgewinn und -verlust
- Treffer nach Grade und Scanner
- MAE/MFE
- Slippage-/Fee-Annahme
- Sample-Size-Freigabe
- Walk-Forward oder Out-of-Sample-Prüfung

Die Performance-Seite und der Wochenreport sollen nicht nur Trefferquote, sondern die reale R-Bilanz aller versandten Signale zeigen.

## 15. Authentifizierung und Abomodelle

### 15.1 Aktuelle Pläne

| Plan | Preis im Code | Wesentliche Rechte |
|---|---:|---|
| Trial | 1 USD / 24h | weitgehender Testzugang, limitierte AI-Nutzung |
| Basic | 29 USD / Monat | begrenzte Scanner, keine Trade-Setups oder E-Mails |
| Pro | 79 USD / Monat | nahezu alle Scanner, Trade-Setups, Alerts, AI-Limit |
| Elite | 149 USD / Monat | inklusive ORB, Backtest, API und höherer AI-Nutzung |

Preise und Stripe-Price-IDs müssen vor Launch im echten Stripe-Livekonto bestätigt werden. Repository-Defaults sind keine Freigabe.

### 15.2 Auth-Stand

- Benutzer liegen persistent in SQLite.
- Passwörter nutzen PBKDF2 mit Migrationslogik für alte Hashes.
- JWT bleibt für externe API-Clients kompatibel.
- Der Browser nutzt im aktuellen lokalen Stand eine HttpOnly-Session-Cookie-Variante.
- Login, Logout, Passwortwechsel und Session-Revocation sind getestet.
- Admin-Konten werden über `ADMIN_EMAILS` erkannt.
- `ALLOW_LEGACY_ADMIN_MASTER_KEY` muss in Produktion `0` sein.
- Der Master-Key kommt nur aus der Server-ENV und darf niemals in Git oder Dokumentation stehen.

## 16. Deployment und Serverbetrieb

### 16.1 Zielserver

```text
Host: 178.104.69.209
App-Verzeichnis: /home/tradingbot/app
API: 127.0.0.1:8000
Produktionsdienste: tradingbot-api, tradingbot-bg
Öffentlicher Zugang: nginx über HTTPS
```

### 16.2 Normaler sicherer Deploy

```bash
cd /home/tradingbot/app
bash deploy/safe_deploy.sh
```

Das Skript führt selbst `git fetch` und bei Bedarf `git pull --ff-only` aus. Vor dem Pull wird die Zielrevision in ein temporäres Verzeichnis exportiert und geprüft.

### 16.3 Kommerzieller Deploy

```bash
cd /home/tradingbot/app
bash deploy/verify_commercial_edge.sh
curl -s http://127.0.0.1:8000/api/commercial-readiness | python3 -m json.tool
COMMERCIAL_DEPLOY=1 bash deploy/safe_deploy.sh
```

### 16.4 Was `safe_deploy.sh` prüft

- sauberer tracked Server-Worktree
- Zielrevision per Preflight vor Aktivierung
- korrekte service-eigene Venv unter `/home/tradingbot/app/venv`
- Requirements-Hash und Installation
- Shell-Syntax aller Deploy-Skripte
- Python-Compile-All
- Frontend-Bundle-Verifikation
- Smoke-Tests oder vollständige Tests im Commercial Mode
- TLS-/nginx-/Port-Prüfung
- systemd-Unit-Synchronisierung
- API-Health beziehungsweise Commercial Readiness
- automatischer Rollback auf die vorherige Revision bei Fehlern

### 16.5 Bekannte Betriebsfallen

- Ubuntu 24.04 blockiert System-Pip über PEP 668. Niemals blind `python3 -m pip install` systemweit ausführen.
- Nicht in PowerShell Linux-Befehle wie `cd /home/...` ausführen. Erst per SSH auf den Server wechseln.
- Ein erster `curl: (7)` direkt nach Service-Restart kann ein kurzer Startzustand sein; entscheidend ist, ob der Retry-Loop danach Health erreicht.
- Nicht mehr `tradingbot-frontend` oder `tradingbot.service` restarten. Diese gehören nicht zur Zielarchitektur.
- `.env` muss Modus `600` besitzen und darf nie committed werden.
- API- und Background-Dienst müssen denselben Runtime-State sehen, ohne globales `/tmp` anderer Prozesse zu teilen.

## 17. Frontend-Build

`frontend/index.html` enthält weiterhin den React-Quellblock, aber Produktion lädt das vorgebaute `frontend/app.bundle.js`. Runtime-Babel wurde entfernt, weil es mehrfach White-Screens und CDN-Ausfälle verursacht hat.

Nach jeder Änderung am React-Quellblock:

```powershell
node scripts\build_frontend_bundle.js
python scripts\verify_frontend_bundle.py
```

Danach muss ein normaler Chrome- oder Edge-Smoke-Test erfolgen. Kein Codex-Browser-Workaround oder temporäres Debug-Profil darf als einzige Frontend-Verifikation gelten.

## 18. Tests und Definition of Done

Aktuell existieren 63 `test_*.py`-Dateien. Der zuletzt verifizierte lokale Gesamtstand bestand **887 Tests**.

Für jede fachliche Änderung gilt mindestens:

1. gezielter Regressionstest für den gefundenen Fehler
2. Tests für Long und Short, falls die Logik direktional ist
3. Tests für Grenzwerte und fehlende Daten
4. Python-Compile-Prüfung
5. vollständiger Pytest-Lauf
6. Frontend-Build und Bundle-Verifikation bei UI-Änderungen
7. Browser-Smoke-Test bei UI- oder Auth-Änderungen
8. `git diff --check`
9. Server-Preflight vor Aktivierung

Ein grüner Testlauf beweist nur das getestete Verhalten. Tests dürfen keine fachlich falsche Geschäftsregel zementieren. Jede neue Schwelle benötigt eine trader-logische Begründung und möglichst historische beziehungsweise Forward-Evidenz.

## 19. Was bisher umgesetzt wurde

### 19.1 Scanner- und Tradinglogik

- Common-Stock-Filter gegen ETFs, ETPs, Warrants und gehebelte Produkte
- persistentere Broker-State-Logik
- einheitlichere R:R-Berechnung zwischen Live und Backtest
- Gap-Timing auf tatsächliches Gap statt Tagesänderung umgestellt
- ORB mit vollständiger Opening Range, ATR, Retest, Volume Freshness und Late-Session-Caps
- Krypto-Strategien von Aktienuniversen getrennt
- CoinGecko-Partial-Scan-Erkennung
- New-Listing-Lifecycle statt reiner Listing-Mail
- 5m- statt 1m-Execution für riskante Krypto- und Penny-Signale
- BTC-Kontext und Venue-Liquidität für Krypto
- Pump/Crack-Short erst nach bestätigter Schwäche statt blind gegen laufenden Pump
- Momentum-Breakout mit Wick-/Follow-through-Qualität
- Early Entry plus Main Breakout Entry
- Penny-Stock-Lifecycle mit Aufbau, Entry, Manage und Exit
- Multi-Timeframe-Barrieren und VRVP in Trade-Plänen
- Cup-&-Handle-Kalibrierung plus Walk-Forward-Prüfung
- Stock- und Crypto-Execution-Integrität gegen widersprüchliche Zustände

### 19.2 UI und Mobile

- responsive Navigation und mobile Scannerkarten
- markierte aktuell ausgewählte Tabellenzeile
- klarere Grade-/Score-/Action-Spalten
- Sidebar mit Trade-Plan, Strukturleveln und Chart
- Timeframe- und Datenquellenhinweise
- Reminder für echte Trigger-/Retest-Zustände
- Performance-, Narrative-, Kalender- und Backtest-Tabs
- Chart- und Sidebar-Freshness-Guards
- produktneutrale Catalyst-Bezeichnungen
- Precompiled Frontend-Bundle und Boot-Fehlerseite statt White-Screen

### 19.3 Alerts

- Score-/Grade-Gates
- Mailklassen Trade, Watch und Info
- persistente Cooldowns und Dedupe
- weniger Crash-/Bear-Doppelmeldungen
- Market-Open- und Timing-Gates
- Entry, Stop, TP1, TP2 und Level-Quellen in Trade-Mails
- New-Listing-Watch versus bestätigter Short getrennt
- Narrative Pulse nach Nutzerfrequenz
- Exit-/Invalidierungs-Updates
- wöchentlicher Performance-Report
- optionales Telegram-Mirroring

### 19.4 Kommerzielle Härtung

- SQLite-Auth und Plan-Gates
- Stripe Checkout, Billing Portal und Webhook-Dedupe
- Passwort- und Login-Härtung
- Browser-Session über HttpOnly Cookie im aktuellen lokalen Stand
- nginx-TLS, Security-Header und Rate-Limits
- Service-User statt Root
- systemd-Sandboxing
- Commercial-Readiness-Endpoint
- sichere Venv- und Deployment-Architektur
- Preflight plus automatischer Rollback
- Secrets- und Abhängigkeitsprüfung

## 20. Die größten Schwierigkeiten und ihre Ursachen

### 20.1 Monolith und doppelte Logik

`api.py`, `bg_service.py`, `scanner.py` und das große Frontend enthalten teilweise ähnliche oder historische Regelwerke. Fixes landeten deshalb gelegentlich nur in einem Pfad. Langfristig sollten Signalentscheidung, Planbau und Mail-Gates in gemeinsame Module extrahiert werden.

### 20.2 White-Screens im Frontend

Ursachen waren ungültiges JSX, auseinandergerissene Komponenten, Runtime-Babel und externe CDN-Abhängigkeit. Das Produkt nutzt jetzt ein selbst gehostetes, vorgebautes Bundle mit Boot-Diagnose. Dieses Design nicht rückgängig machen.

### 20.3 Score versus Timing

Hohe Grades wurden zu oft als direkte Handlungsanweisung interpretiert. Gleichzeitig erzeugten verschiedene Komponenten eigene Chase-, Fakeout- und Health-Texte. Das führte zu Aussagen wie `JETZT TRADEN` neben `NO_TRADE` oder `Chase HIGH` neben `nicht gechased`.

### 20.4 Zu strenge versus zu lockere Scanner

Zu strenge Gates erzeugten leere Listen und keine Mails. Zu lockere Gates erzeugten späte oder schlechte Setups. Die Lösung ist nicht ein einzelner globaler Threshold, sondern:

- breites Kandidatenuniversum
- transparente Setup-Rangliste
- separate harte Ausführungs-Gates
- optional sichtbare Wait-Kandidaten
- Forward-Auswertung je Scanner und Regime

### 20.5 Datenmischung und Staleness

Chart, Header, Scanner und Trade-Plan nutzten zeitweise unterschiedliche Datenstände oder Timeframes. Besonders gefährlich waren Daily-Fallbacks hinter Intraday-Buttons, CoinGecko-Volumen als Venue-Liquidität und stale Caches als `success`.

### 20.6 Stop und Targets

Frühere Pläne waren teilweise reine ATR-/R:R-Konstruktionen, hatten gleiche Ziele, falsche Richtung oder lagen hinter offensichtlichen Barrieren. Die aktuelle Zielrichtung ist echte Struktur plus validierte Geometrie und Level-Quellen.

### 20.7 E-Mail-Spam und fehlende Mails

Mehrere Prozesse, unterschiedliche Dedupe-Keys, Cooldown vor erfolgreichem Versand und divergente Mail-Gates führten sowohl zu Spam als auch zu langen Phasen ohne Alerts. Das neue `modules/email_dedupe.py` soll prozessübergreifend atomar arbeiten.

### 20.8 Server- und Deployment-Drift

Historisch existierten falsche Service-Namen, falsche Verzeichnisse, Streamlit-Reste, `/root/venv`, PEP-668-Probleme und Healthchecks gegen geschützte Endpoints. Die neuen Deploy-Artefakte sollen genau diese Drift verhindern.

### 20.9 Zugang und Master-Login

Änderungen an JWT, CORS und Middleware brachen zeitweise Login oder OPTIONS-Requests. Der aktuelle lokale Auth-Stand bewahrt Bearer-Kompatibilität und nutzt für den Browser ein HttpOnly Cookie. Den Master-Key niemals hartcodieren oder durch ein Demo-Fallback ersetzen.

### 20.10 Erwartungsmanagement

Ein Verlusttrade beweist nicht automatisch einen Codefehler, aber ein widersprüchlicher, stale, illiquider oder falsch gemappter Trade schon. Jede Reklamation muss deshalb gegen den damaligen exakten Payload, Candle-Zeitpunkt, Venue, Trigger und Mail-Log reproduziert werden.

## 21. Historische Fehler, die nicht wieder eingeführt werden dürfen

- `_get_ib_state()` darf nicht bei jedem Zugriff einen neuen Zustand erzeugen.
- Gap-Timing darf nicht `abs(change_pct)` statt `gap_pct` verwenden.
- Backtest und Live dürfen kein unterschiedliches TP-/R:R-Modell verwenden.
- Crypto-Strategien dürfen nie über US-Aktien-Snapshots laufen.
- Teilweise geladene CoinGecko-Seiten dürfen nicht als vollständiger Cache gelten.
- `rstrip('USD')` darf nicht zum Entfernen eines exakten Symbolsuffix verwendet werden.
- 24/7-Krypto und Börsentage dürfen nicht per Arrayindex statt Datum verglichen werden.
- Neue Listings im Status `waiting_for_history` müssen erneut geprüft werden.
- `abs()` darf verpasste Short-Ziele nicht in positiven Reward verwandeln.
- ORB darf keine zwei statt drei vollständigen 5m-Kerzen als 15m-Range akzeptieren.
- ORB darf einen alten Breakout-Bar nicht unbegrenzt als aktuelle Volume Confirmation verwenden.
- News-Substring-Suche darf `war` in `software` oder `sec` in `sector` nicht matchen.
- Fehlende Newsdaten dürfen nicht als `LOW Risk` behandelt werden.
- Staler Market Context darf Scanner nicht still beeinflussen.
- `0R` darf nicht durch `or 999` zu `999R` werden.
- Fehlender 5m-State darf einen erweiterten Long nicht automatisch bestätigen.
- Ein Crash-Alert darf sich nicht durch eine pauschale Drop-No-Chase-Regel selbst blockieren.
- `WAIT_FOR_RETEST` darf nicht pauschal zu `NO_TRADE` oder `BEOBACHTEN` plattgedrückt werden.
- Crypto-Chart-Fallbacks dürfen keine Daily-Daten hinter 5m-/15m-Buttons zeigen.
- Reminder dürfen nicht für `NO_TRADE` oder reine Watch-Zeilen erstellt werden.
- S/R und Fibonacci müssen denselben Timeframe und dieselbe Richtung wie der Chart verwenden.
- Gleiche Aktie auf 4H und 1D darf nicht wegen Datenmischung zwei angeblich aktuelle Preise zeigen.
- API-Reference-Fehler dürfen bei Asset-Validierung nicht automatisch fail-open sein.
- SMTP-Cooldown darf erst nach erfolgreichem Versand gesetzt werden.
- Ein API-Key, Passwort oder Secret darf niemals in Chat, Git oder UI ausgegeben werden.

## 22. Aktueller Git- und Arbeitsstand

### 22.1 Eingecheckter Stand

```text
Branch: main
HEAD: 3efcfed Harden penny stock execution scanner
origin/main: 3efcfed
```

### 22.2 Lokaler, noch nicht eingecheckter Stand

Der Arbeitsbaum ist umfangreich verändert. Darin liegen unter anderem:

- kommerzielle Produktions-ENV-Vorlage
- Commercial Launch Checklist
- Auth-/Cookie-Härtung
- nginx-, systemd- und Safe-Deploy-Härtung
- Scanner-, Trade-Health-, Level-, VRVP- und Backtest-Fixes
- Frontend-Bundle und Bootloader
- atomisches E-Mail-Dedupe
- zusätzliche Runtime-Safety- und Security-Tests

Wichtige neue, derzeit untracked Dateien:

```text
deploy/verify_commercial_edge.sh
frontend/app.bundle.js
frontend/boot.js
modules/email_dedupe.py
pytest.ini
scripts/build_frontend_bundle.js
scripts/verify_frontend_bundle.py
test_auth_session_security.py
test_email_dedupe_atomic.py
test_premarket_tracker_math.py
test_strategy_scan_runtime_safety.py
```

`AGENTS.md` ist eine lokale Agentenanweisung und darf nicht automatisch mit dem Produktcommit vermischt werden.

Es existieren außerdem bewusst gelöschte alte Backups, Legacy-Units und veraltete Auditdateien. Vor dem Commit Diff einzeln prüfen, nicht pauschal zurückholen.

## 23. Aktueller Verifikationsstand

Zuletzt lokal verifiziert:

| Check | Ergebnis |
|---|---|
| vollständige Pytest-Suite | 887 bestanden |
| kritische Python-Dateien | Compile OK |
| Deploy-Shellskripte | `bash -n` OK |
| Frontend-Bundle | Hash und Verifikation OK |
| Chrome Headless Smoke | Login-UI geladen, kein Boot-/Scriptfehler |
| `git diff --check` | keine inhaltlichen Whitespace-Fehler |
| `pip-audit` | keine bekannten Schwachstellen in installierten Requirements |
| Secret-Scan aktueller tracked Stand | keine erkannten aktiven Provider-Keys oder Private Keys |

Diese Verifikation gilt für den lokalen Arbeitsstand vom 18. Juli 2026, nicht automatisch für GitHub oder Server.

## 24. Kommerzielle Freigabe

### 24.1 Aktuelles Urteil

**Noch kein öffentliches Paid-Launch-Go.** Die Softwarebasis ist weit, aber die kommerzielle Freigabe hängt nicht nur von grünem Code ab.

### 24.2 Harte Blocker vor Verkauf

- lokalen Abschlussstand bewusst committen und pushen
- exakt diese Revision auf Staging beziehungsweise Produktion deployen
- alle jemals in Git-History oder Chat offengelegten Secrets widerrufen und ersetzen
- Repository-Sichtbarkeit, Collaborators, Deploy Keys und PATs prüfen
- `HISTORICAL_SECRETS_ROTATED=1` erst nach realer Rotation
- `SOURCE_REPOSITORY_ACCESS_REVIEWED=1` erst nach realem Review
- echte HTTPS-Domain und exakte CORS-Origin setzen
- Stripe Live Keys, Webhook und echte Price IDs setzen
- Auth-DB-Backup und Restore testen
- Datenlizenz und Redistribution schriftlich prüfen
- AGB, Datenschutz, Risiko-Hinweise, Refund/Cancellation, Steuern/VAT und Anbieterangaben prüfen
- `deploy/verify_commercial_edge.sh` bestehen
- `/api/commercial-readiness` muss `commercial_ready: true` liefern
- Commercial Deploy muss vollständige Tests auf der Zielrevision bestehen

Private interne Nutzung oder geschlossene Beta ist technisch früher möglich, aber nur mit klarer Haftungs- und Datenfreigabe.

## 25. Offene technische Schulden

1. `api.py` und `frontend/index.html` sind zu groß und sollten schrittweise modularisiert werden.
2. Legacy-Logik in `scanner.py` und einzelne Legacy-Mailpfade sollten entfernt oder klar isoliert werden.
3. Scannerentscheidungen müssen langfristig in einem gemeinsamen Decision-Modul statt in API, BG und UI gespiegelt werden.
4. Backtest- und Live-Parität benötigt je Strategie eine maschinenlesbare gemeinsame Regeldefinition.
5. Schwellen und Scores benötigen fortlaufende Kalibrierung mit genügend Sample Size, Regime-Splits und Walk-Forward.
6. Echte Slippage-/Spread-/Orderbook-Modelle sind noch begrenzt.
7. Calendar Coverage muss regelmäßig über das aktuelle Jahr hinaus gepflegt oder automatisiert werden.
8. Datenprovider-Limits und gemeinsames Budget zwischen Prozessen bleiben betriebskritisch.
9. Browser-Push ist weniger robust als E-Mail und muss pro Browserberechtigung getestet werden.
10. Mobile UX braucht weiterhin reale Geräte- und One-Hand-Tests.

## 26. Empfohlene nächste Schritte für Claude

### Schritt 1: Arbeitsstand sichern

```powershell
git status --short --branch
git diff --stat
git diff --name-status
```

Keine Datei zurücksetzen. Keine fremden oder sachfremden Änderungen verwerfen.

### Schritt 2: Aktuellen lokalen Abschluss erneut verifizieren

```powershell
python -m compileall -q api.py bg_service.py modules
python scripts\verify_frontend_bundle.py
python -m pytest -q
git diff --check
```

Falls das Frontend verändert wurde:

```powershell
node scripts\build_frontend_bundle.js
python scripts\verify_frontend_bundle.py
```

### Schritt 3: Diff fachlich aufteilen und committen

Empfohlene Commitgruppen:

1. Commercial/Auth/Deployment Hardening
2. Scanner/Trade-Level/Runtime Safety
3. Frontend Prebuild/Boot und UX
4. Tests und Dokumentation

Wenn die Änderungen zu eng gekoppelt sind, ist ein sauber erklärter Release-Commit besser als künstliches Cherry-Picking mit kaputtem Zwischenstand.

### Schritt 4: Secrets und externe Freigaben erledigen

Diese Aufgaben sind Betreiberaufgaben. Claude darf Flags nicht einfach auf `1` setzen, ohne dass die reale Rotation oder Prüfung erfolgt ist.

### Schritt 5: Staging-Deploy

Zielrevision deployen, Health, Readiness, Login, Checkout-Testmodus, alle Haupttabs und exemplarische Scanner prüfen.

### Schritt 6: Forward-Validation

Für mindestens mehrere Wochen jeden versandten Trade automatisch tracken. Ergebnisse nach Scanner, Marktregime, Grade, Setup-Score, Entry-Score und Signalalter auswerten. Schwellen erst danach datenbasiert ändern.

## 27. Arbeitsregeln für den nächsten Agenten

- Erst Code und Tests lesen, dann ändern.
- Keine pauschalen globalen Threshold-Fixes ohne Scannerkontext.
- Keine Watchlist als aktive Trade-Liste verkaufen.
- Keine UI-Kosmetik verwenden, um einen Backend-Widerspruch zu verstecken.
- Ursache beheben, nicht nur Label ändern.
- Jede Nutzerbeschwerde mit damaligem Payload und Zeitstempel reproduzieren.
- Keine echten API-Keys, Passwörter oder Tokens in Ausgabe, Tests oder Commits.
- Keine fremden Arbeitsbaumänderungen zurücksetzen.
- Frontend nur über Quelle plus Bundle-Build ändern.
- Kein Runtime-Babel und keine CDN-Abhängigkeit reaktivieren.
- Keine neue Scannerlogik ohne Long-/Short-, stale-, partial- und Grenzwerttests.
- Bei Tradinglogik immer Mathematik, Traderlogik, Datenqualität, Timing und UX gemeinsam prüfen.
- Keine Profitversprechen.
- Nicht committen oder pushen, solange der Betreiber es nicht ausdrücklich verlangt.

## 28. Abnahmekriterien pro Signal

Ein Signal darf als `JETZT_TRADEN` erscheinen, wenn alle folgenden Aussagen wahr sind:

| Bereich | Prüfung |
|---|---|
| Identität | korrektes Symbol, Asset, Venue und Instrument |
| Daten | frisch, vollständig und timeframe-konsistent |
| Setup | scanner-spezifische Struktur erfüllt |
| Timing | aktueller Trigger oder Swing-Zustand gültig |
| Liquidität | Dollarvolumen, Spread und Venue ausreichend |
| Entry | aktueller Preis in erlaubter Zone |
| Stop | objektive Invalidation, noch nicht gerissen |
| TP1/TP2 | korrekt gerichtet, verschieden, erreichbar und strukturbezogen |
| R:R | am Live-Preis über Mindestwert |
| Chase | nicht überdehnt oder explizit bestätigte Continuation |
| Fakeout | kein harter Wick-/Rejection-/Volume-Blocker |
| Barrieren | keine ungelöste starke Gegenbarriere vor TP1 |
| Kontext | keine harte Event-, BTC- oder Marktblockade |
| Status | eine einzige kanonische finale Entscheidung |
| Kommunikation | Tabelle, Sidebar, Chart und Mail stimmen überein |
| Tracking | Signal wird mit vollständigem Snapshot gespeichert |

## 29. Definition des Projekterfolgs

Alpha Station ist erfolgreich, wenn es nicht einfach viele Treffer produziert, sondern:

- wenige, nachvollziehbare und zeitlich gültige Signale liefert,
- Risiken sichtbar und widerspruchsfrei behandelt,
- keine Datenqualität vortäuscht,
- reale Resultate transparent trackt,
- schlechte Regeln anhand von Evidenz verbessert,
- auf Desktop und Mobile stabil bedienbar ist,
- Abonnenten technisch und rechtlich sauber verwaltet,
- und bei Fehlern sicher ausrollt oder automatisch zurückrollt.

Das Produkt soll den Trader unterstützen, nicht sein Urteilsvermögen ersetzen.

## 30. Startprompt für Claude

```text
Lies zuerst PROJEKTHANDBUCH_CLAUDE.md, COMMERCIAL_LAUNCH_CHECKLIST.md,
deploy/safe_deploy.sh und den aktuellen git status. Der lokale Arbeitsbaum
enthält einen noch nicht eingecheckten kommerziellen Abschlussstand; nichts
zurücksetzen. Prüfe jede Aufgabe gegen die kanonischen Signalzustände und die
Invarianten für Datenfrische, Entry/Stop/TP-Geometrie, Setup-vs-Execution,
Swing-vs-Intraday und UI/Mail-Parität. Ändere keine Schwelle pauschal und
versprich keine Gewinne. Nach Codeänderungen gezielte Tests, Full Pytest,
Compile, Frontend-Bundle-Verifikation und bei UI/Auth einen normalen
Chrome/Edge-Smoke-Test ausführen. Vor Commit oder Push den Betreiber fragen.
```

---

**Letzter Hinweis:** Alte Auditberichte dokumentieren wichtige historische Fehler, aber der Code ist seitdem weiterentwickelt worden. Sie sind Ursachen- und Regressionreferenz, nicht automatisch der aktuelle Fehlerstand. Der aktuelle lokale Code, die aktuelle Testsuite und dieses Handbuch haben Vorrang.
