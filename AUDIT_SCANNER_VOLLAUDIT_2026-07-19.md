# Audit Aktien- & Krypto-Scanner — Alpha Station

**Datum:** 19. Juli 2026
**Geprüfter Stand:** lokaler Arbeitsbaum vom 18./19.07.2026 (nach Commit `3efcfed`)
**Umfang:** api.py (26.726 Zeilen), modules/* (Indikatoren, Patterns, Scanner, Scorer, Levels, VRVP, Trade-Health, Datenfetcher, Backtests, Signal-Tracker, New-Listing, Penny, Premarket, Strategien), volume_profile.py. Legacy-`scanner.py` nur als Referenz, nicht als Audit-Fokus.
**Methode:** 8 parallele Teilaudits mit Pflicht zu Code-Zitat, Zeilennummer und numerischem Nachrechnen (Python-Repros, teils Live-Verifikation gegen CoinGecko-/MEXC-/Crypto.com-APIs). Die schwersten Befunde wurden anschließend zeilengenau gegengeprüft.

**Legende Verifikationsstufe:** `[V]` = im Haupt-Audit zeilengenau im Code gegengeprüft · `[A]` = im Teilaudit mit Code-Zitat und Repro/Nachrechnung belegt.

---

## 1. Gesamturteil

Die Antwort auf „berechnen wir hier alles korrekt, macht das alles Sinn?" ist zweigeteilt.

**Der mathematische Kern ist gut.** RSI (echtes Wilder-Smoothing, gegen den StockCharts-Referenzdatensatz auf die Nachkommastelle reproduziert), ATR-14, EMA/MACD (TradingView-konform geseedet), ADX, Volume Profile (exakt volumenerhaltend, korrekte 70%-Value-Area-Expansion) und die zentrale Trade-Geometrie (`Stop < Entry < TP1 < TP2`, spiegelbildlich für Short, ohne `abs()`-Tricks) sind sauber implementiert und wurden numerisch nachgerechnet. Auch die im Handbuch §21 gelisteten historischen Fehler sind fast alle nachweislich behoben geblieben — es wurde **keine einzige Regression** dieser Liste gefunden. Der BI-Scanner, der Penny-Scanner und der New-Listing-Scanner sind handwerklich auf hohem Niveau (Partial-Bar-Stripping, fail-closed Safety, Median-RVOL ohne Selbstkontamination, Netto-R:R mit Kosten, Short erst nach bestätigter Schwäche).

**Aber es gibt eine Schicht darüber, die real falsch rechnet.** Der schwerste Einzelfund: Der Krypto-Backtest läuft komplett auf einer falschen Zeitskala, weil CoinGecko-`/ohlc` gar keine Tageskerzen liefert (bei 30 Tagen 4h-Kerzen, ab 31 Tagen 4-Tages-Kerzen) — „change_1d" ist dort real ein 4-Tages-Move, „12 Tage Haltedauer" sind 48 Tage. Daneben stehen zehn weitere Befunde der Kategorie HOCH: ein Einheitenfehler in der MEXC-Anbindung (USD-Volumen um Faktor 100 bis 10 Mio. daneben), ein Signal-Tracker, dessen „belegbarer Track-Record" Entries als immer gefüllt annimmt und Teilverlierer als Wins zählt, Backtests mit Löchern in der Kurshistorie (Stop-Hits unsichtbar), systematische Unterdrückung von Morgen-Signalen bei Bear/Crash/Turtle (RVOL ohne Zeitnormierung), ein Crash in der Pattern-Erkennung genau bei Dreiecks-Tickern, und ein Echtgeld-AutoTrader, der per Limit-Order sofort mitten in der Konsolidierung füllt statt am Breakout.

Das Muster ist konsistent: **Die vielen dokumentierten Audit-Runden haben die Signal-Gates und die Geometrie hart gemacht — die Daten- und Auswertungsschicht (Backtest, Tracker, Exchange-Einheiten, Zeitnormierung) hat dieses Niveau noch nicht.** Für ein kommerzielles Produkt ist das die gefährlichere Schicht, weil sie bestimmt, was ihr euren Kunden als „Evidenz" zeigt.

Zählung: **1× KRITISCH, 11× HOCH, ~21× MITTEL, ~14× NIEDRIG.** Kein Befund deutet auf grob fahrlässige Architektur; die meisten Fixes sind klein und chirurgisch.

---

## 2. Was sauber gelöst ist

Diese Punkte wurden gezielt geprüft und bestanden — sie beantworten den „macht das Sinn?"-Teil positiv:

**Indikator-Mathematik.** `modules/indicators.py`: RSI exakt Wilder (SMA-Seed, alpha=1/14, Randfälle 0/0→50 und avg_loss=0→100 dokumentiert), `calculate_atr_14` korrektes Wilder-ATR (numerisch identisch mit unabhängiger Referenz), MACD Skalar- und Serienvariante bitidentisch, ADX lehrbuchkonform, A/D-Line defensiv gegen NaN/fehlende Keys. `[A]`

**VRVP-Kern.** Volumenzuordnung proportional zur Bin-Überlappung mit exakter Volumenerhaltung (inkl. des früheren Doji-Volumenverlusts, der behoben bleibt), Value Area als kontiguierliche POC-Expansion, POC ∈ [VAL, VAH] garantiert. `[A]`

**Trade-Geometrie als zentrales Gate.** `trade_geometry` (modules/trade_levels.py) erzwingt vorzeichenrichtig Long `Stop<Entry<TP1<TP2` / Short gespiegelt, TP1≠TP2, und wird von Scannern, Tracker, Backtests und Mails gemeinsam genutzt. Verpasste Ziele werden nicht per `abs()` schöngerechnet; `current <= stop` (Long) ⇒ `stop_breached` ⇒ NO_TRADE/CRITICAL/`live_rr=0` — im Test für beide Richtungen nicht aushebelbar. `[A]`

**Kein Look-ahead in den Live-Aktienmetriken.** `_strategy_daily_history_metrics` trennt abgeschlossene Bars von der laufenden Kerze, Turtle-Donchian nimmt die 20 Tage ohne heute, der BI-Scanner strippt den Partial-Bar explizit (`_bi_strip_partial_bar`), der Krypto-5m-Trigger verwirft offene Kerzen (`_completed_candles_only`) und failt bei fehlenden Timestamps closed. `[A]`

**ORB-Altfehler behoben.** Exakt 3 vollständige 5m-Kerzen als Opening Range, DST-sichere ET-Anker, NYSE-Feiertags- und Half-Day-Kalender mit Warnung bei ungepflegten Jahren, Volume-Confirmation nur aus den letzten 3 Post-OR-Bars (kein „ewiger" Breakout-Bar), Late-Session-Caps. `[A]`

**Datenqualitäts-Disziplin Krypto.** CoinGecko-Partial-Scans werden nie als vollständig gecacht und sind Hard-Block in Mail-/Armed-/Visible-Gates; der `rstrip('USD')`-Fehler ist durch endswith+Slice ersetzt; Venue-Liquidität wird über Orderbuchtiefe in bps statt CoinGecko-Aggregatvolumen bewertet (Konzept richtig — Ausnahme MEXC, siehe H1). `[A]`

**Konservative Backtest-/Tracker-Grundregeln.** Stop und TP am selben Bar ⇒ Stop zuerst (alle vier Pfade, inkl. explizitem `ambiguous_same_day`-Flag), TP2 erst ab Folgebar nach TP1, Gap-through-Stops exiten am Open, Grouped-Backtests ohne Survivorship-Bias (historisches Tagesuniversum inkl. später delisteter Ticker). `[A]`

**Mail-Disziplin.** Atomares claim/mark/release-Dedupe gegen parallele Sender, 15-Minuten-Frische-Gate im Early-Mover-Mail-Pfad, Watch strikt von Trade getrennt, Armed-FOMO-Mails hart deaktiviert, News-Matching mit Wortgrenzen (die „war in software"-Falle ist tot). `[A]`

**Penny/New-Listing-Reife.** Penny: Median-RVOL über 20 abgeschlossene Tage (heutiger Tag per ET-Datum ausgeschlossen), Netto-R:R inkl. Spread + 2× Slippage, SEC-Filing-Risiko fail-closed, Offering/Reverse-Split-Matching mit Wortgrenzen. New Listing: Short erst nach First-Crack + Struktur-Crack + bestätigtem, geschlossenem 5m-Micro-Trigger; Safety (Ticker/Orderbuch/Candle-Alter) fail-closed. `[A]`

---

## 3. Befunde — KRITISCH

### K1 · Krypto-„Daily"-Kerzen sind keine Daily-Kerzen — Krypto-Backtest läuft auf falscher Zeitskala `[V]`
**Datei:** modules/data_fetchers.py:715–791 (`fetch_daily_candles_crypto`); Konsumenten api.py:25201 ff. (`_run_crypto_backtest`), api.py:16343/16396 (`/api/crypto-chart`, Label `"timeframe": "1D"`).
**Problem:** Die Funktion ruft CoinGecko `/coins/{id}/ohlc` mit `days ∈ {1,7,14,30,90,180,365}` auf und behandelt jede zurückgegebene Kerze als Tageskerze (der Code-Kommentar behauptet das sogar). CoinGecko liefert auf diesem Endpoint aber automatische Granularität: bis 2 Tage → 30-Minuten-Kerzen, 3–30 Tage → **4-Stunden-Kerzen**, ab 31 Tagen → **4-Tages-Kerzen**. Empirisch im Audit gegen die Live-API bestätigt (days=30 → 180 Bars im 4h-Abstand; days=90 → 23 Bars im 96h-Abstand).
**Auswirkung:** Im Krypto-Backtest ist `change_1d` real ein 4-Tages-Move, `change_7d` ein 28-Tage-Move, das ATR(14) ein 4-Tages-ATR, „Entry am nächsten Open" liegt bis zu 8 Tage nach dem Signal, `max_hold=12 Tage` sind real 48 Tage. Die Strategie-Schwellen (z.B. `1.5 ≤ change_1d ≤ 10.5`) selektieren damit ein völlig anderes Regime als der Live-Scanner. Bei kurzen Zeiträumen greift zusätzlich `len(bars) < 45 → continue` und der Backtest liefert still 0 Trades. Der „1D"-Chart zeigt bei days=30 real ~5,3 Tage 4h-Kerzen (die Trimmung `bars[-(days+2):]` schneidet auf 32 der 180 Bars).
**Fix:** Daily-Bars aus `market_chart`-Hourly aggregieren (die korrekte Aggregation existiert bereits in `fetch_historical_data_crypto`) oder Exchange-Kerzen nutzen (Bybit-Fetcher vorhanden). Zusätzlich generell: Bar-Abstand der Antwort messen und bei ≠24h ablehnen statt „1D" zu labeln — das hätte auch künftige API-Änderungen abgefangen.

---

## 4. Befunde — HOCH

### H1 · MEXC: USD-Volumen und Open Interest ohne `contractSize` — Faktor 100 bis 10.000.000 falsch `[V]`
**Datei:** api.py:17872–17877 (`fetch_mexc_funding_oi`).
**Problem:** MEXC liefert `volume24`/`holdVol` in **Kontrakten**. Der Code rechnet `volume24 * lastPrice` — ohne `contractSize`. Live verifiziert: BTC (contractSize 0,0001) → „$25,6 Billiarden" Tagesvolumen (×10.000 zu hoch); PEPE (contractSize 10.000.000) → „$9" statt $89,4 Mio (×10⁷ zu niedrig). MEXC liefert den korrekten USD-Umsatz fertig im Feld `amount24`, das ungenutzt bleibt.
**Auswirkung:** (a) `fetch_multi_exchange_perps` wählt die „beste" Exchange nach diesem Volumen — für teure Coins gewinnt MEXC fälschlich gegen Binance und wird Chart-/Trigger-/Orderbuch-Quelle; für Coins mit großem contractSize wird echtes MEXC-Volumen ignoriert. (b) Die Liquiditäts-Gates `_EARLY_MOVER_MIN_PERP_VOLUME_USD` (2M/5M) blocken liquide MEXC-only-Coins als `thin_perp_liquidity` bzw. lassen dünne durch. (c) Whale-Accumulation-Schwellen ($200k–$10M OI) arbeiten mit Fantasiezahlen. Einzig `oi_ratio` ist zufällig korrekt (Faktor kürzt sich).
**Fix:** `vol_usdt = amount24`; OI = `holdVol × contractSize × lastPrice` (Contract-Details cachen) oder MEXC-OI aus den absoluten USD-Gates nehmen.

### H2 · Signal-Tracker: Entry gilt als immer gefüllt + geschönte Win-Rate — der „belegbare Track-Record" misst etwas anderes als erzielbare Performance `[V]`
**Datei:** modules/signal_tracker.py:493–566 (Bewertung), 838–856 (`_classify_row`/`_finalize_bucket`).
**Problem 1 (Fill):** Die Bewertung beginnt am Folgetag direkt mit Stop-/TP-Checks gegen den Scanner-Entry. Es gibt keinen Fill-Check (hat der Kurs den Entry je berührt?). Ein Breakout-Signal, dessen Kurs ohne Rücksetzer durchläuft, bucht +2R, obwohl niemand zum Entry kaufen konnte; dreht der Kurs ohne Entry-Touch, bucht es −1R für einen Trade, der nie entstand. `price_at_alert` wird gespeichert, aber nie ausgewertet.
**Problem 2 (Win-Rate):** `wins = tp1_hit + tp2_hit`, `decided = wins + stop_hit`. Ein Signal, das TP1 kurz berührt und dann mit real negativem End-R ausläuft, zählt voll als Win; ausgelaufene Signale ohne TP fallen ganz aus dem Nenner. Rechenbeispiel aus dem Audit: 10 Signale → angezeigte Win-Rate 66,7% bei Buch-Summe **−3,2R**.
**Auswirkung:** Wochenreport und Performance-Seite (Verkaufsargument!) können systematisch besser aussehen als das, was ein Kunde real erzielen konnte. Zusatz: Overnight-Gaps unter den Stop werden pauschal als −1,0R statt mit realem Gap-Verlust gebucht.
**Fix:** Fill-Gate (erster Bar, dessen Range den Entry enthält; Gap über Entry → Fill=Open), NO_FILL als eigener Bucket, Win-Definition an `r_realized > 0` koppeln, EXPIRED in den Nenner oder als separate Quote, Gap-Stops mit realem R.

### H3 · Grouped-Backtests: Preis-/Volumenfilter beim History-Aufbau erzeugt Löcher — Stop-Hits an gefilterten Tagen unsichtbar `[V]`
**Datei:** modules/backtests.py:143–159 (`run_full_backtest_grouped`), 294–312 (`run_bi_v2_backtest`), 906–923 (`run_biotech_backtest`).
**Problem:** `if price < min_price or volume < min_volume: continue` läuft beim Aufbau der per-Ticker-History — der Filter gehört aber nur an den Signaltag. Jeder Tag unter der Schwelle fehlt anschließend in der Simulationssequenz (der Kommentar „Jetzt hat jeder Ticker seine VOLLSTÄNDIGE History" ist genau falsch).
**Auswirkung:** Biotech-Beispiel (min_price=2$): Entry 2,60, Stop 2,20, Crash auf 1,40 über mehrere Tage (alle Bars <2$ fehlen), Erholung auf 2,60 → der Backtest sieht den Crash nie, der Trade kann als Winner enden. Genau die Katastrophen-Tage, die ein Penny-/Biotech-Backtest bestrafen muss, werden herausgefiltert → **Backtest-Ergebnisse systematisch zu gut.** Zusätzlich: `fetch_grouped_daily` hat keinen 429-Retry; ein Fehlertag fehlt still in allen Ticker-Histories (RVOL-Fenster und „max_hold-Tage" laufen über Kalenderlöcher).
**Fix:** History ungefiltert aufbauen, min_price/min_volume nur als Signaltags-Bedingung; Grouped-Fetch mit Retry und explizitem Fehlersignal (None ≠ leerer Handelstag).

### H4 · AutoTrader (Echtgeld-Pfad): Limit-BUY über dem Markt füllt sofort in der Konsolidierung; Chase-Guard ist mathematisch toter Code `[V]`
**Datei:** modules/scanners.py:645–690 (Entry/Guard), 778–790 (Order).
**Problem:** `range_high` wird inklusive des letzten Bars gebildet, also gilt strukturell `close ≤ range_high < entry = range_high + 0,15·ATR` — der Chase-Guard `if close > entry*1.02: continue` kann **nie** feuern. Die Parent-Order ist eine gewöhnliche `LMT`-BUY mit Limit über dem Markt: marketable, füllt sofort zum Ask — mitten in der Range, nicht am Breakout. Der Stop (`range_high − max(0,9·ATR, 10%·Range)`) ist für einen Post-Breakout-Retest kalibriert und kann **über** dem tatsächlichen Fill liegen → Stop triggert unmittelbar (Instant-Loss um Spread/Slippage). Zusätzlich wird der Gap-Fade-Abschlag (×0,5) erst **nach** dem Mindest-Score-Gate angewandt (markierte Fade-Signale bleiben handelbar) und die BI-Analyse läuft auf dem laufenden Partial-Bar (im BI-Scanner längst behoben, hier nicht).
**Auswirkung:** Im Mode `full` (transmit=True) kauft jedes qualifizierte Signal sofort zum aktuellen Kurs mit falsch platziertem Stop. Default `semi` (TWS-Bestätigung) mildert, repariert die Orderstruktur aber nicht. Live-Verhalten ≠ Backtest, obwohl der Code Parität behauptet.
**Fix:** Parent als `STP`/`STP LMT` (auxPrice=Entry), Chase-Guard gegen einen Live-Kurs außerhalb des Range-Fensters, Fade-Abschlag vor das Gate, `_bi_strip_partial_bar` auch hier. Bis dahin: `full`-Mode nicht freigeben.

### H5 · Bear/Crash und Turtle: RVOL ohne Zeitnormierung — Morgen-Signale systematisch unterdrückt `[V]`
**Datei:** api.py:12749–12750 (Bear-Berechnung), 5434–5435 und 5472–5473 (Gates `rvol < 1.0` / `< 1.2`), 12407–12416 + 9313–9326 (Turtle).
**Problem:** Aufgelaufenes Teil-Tagesvolumen wird gegen den Schnitt von 20 **kompletten** Tagen geteilt, ohne Division durch `_us_equity_expected_volume_fraction` — obwohl exakt diese Funktion in Volume-Spikes, ORB, Strategy-Scan und Penny bereits benutzt wird. Um 10:00 ET sind ~22% des Tagesvolumens normal: Eine Aktie, die mit 3-fachem Tempo abverkauft wird, zeigt rvol ≈ 0,66 → Bear-Short-Mail **und** Crash-Alert werden geblockt. Die 1,2er-Schwelle verlangt um 09:50 faktisch ~8× Tempo, um 15:50 nur 1,2× — dieselbe Zahl bedeutet je nach Uhrzeit etwas völlig anderes. Beim Turtle drückt dasselbe unnormierte RVOL über den Score-Cap (`<1.0 → max 69`) frische Vormittags-Breakouts unter die Alert-Schwelle (Grade B statt S/A); alertbar werden sie erst nachmittags — dann greift oft schon der >5%-Extension-Cap.
**Auswirkung:** Genau das Edge-Fenster (aktive Morgen-Flushes, frische Donchian-Breakouts) wird strukturell unterdrückt; der Crash-Alert mit 15-Minuten-Scanintervall ist morgens faktisch taub.
**Fix:** Ein Einzeiler pro Stelle: `rvol_raw / max(_us_equity_expected_volume_fraction(now), 0.01)`, Quelle labeln (wie im Strategy-Scan), Schwellen unverändert lassen.

### H6 · New Listing: Announcement-Zeit überschreibt echte Exchange-Listing-Zeit — 1h-Mindestalter-Guard umgangen `[V]`
**Datei:** modules/new_listing_scanner.py:2845–2848 (i.V.m. 1083–1095, 2951–2958).
**Problem:** `add_to_monitoring` speichert zuerst korrekt `onboard_date/create_time/launch_time`, aber der Update-Block überschreibt `listing_time` anschließend bedingungslos mit der Announcement-Release-Zeit (`announcement_listing_time or …` — Präzedenz falsch herum), bei jedem Lauf neu, solange das Announcement <168h alt ist. Announcements erscheinen Stunden bis Tage **vor** dem Handelsstart.
**Auswirkung:** Beispiel: Announcement Montag 10:00, Perp startet Dienstag 12:00 → beim Scan Dienstag 12:05 ist `listing_age_hours = 26,1` statt 0,08. Damit (a) greift `listing_too_early` (<1h) nie — Shorts sind in den ersten Minuten nach Launch möglich, exakt in der Squeeze-Phase, die der Guard verhindern soll; (b) Score-Komponente „Sweet Spot 24–72h" vergibt 10/10 statt 0 („zu früh"); (c) das 72h-Fenster läuft ~26h zu früh ab.
**Fix:** Präzedenz umdrehen (`monitoring[key].get("listing_time") or announcement_listing_time`); robuster: Handelsstart aus dem Timestamp der ältesten verfügbaren Kerze ableiten und als Obergrenze verwenden.

### H7 · Biotech-Scanner: laufender Partial-Bar in RVOL und Technik-Score — Grade hängt von der Scan-Uhrzeit ab `[V]`
**Datei:** modules/scanners.py:2438–2486 (`end_date = datetime.now()`, `last_vol = volumes[-1]`, kein Strip).
**Problem:** Während der US-Session ist `volumes[-1]` der partielle Tagesumsatz. Um 10:30 ET zeigt ein normaler Titel RVOL ≈ 0,3 → −3-Penalty für praktisch das ganze Universum; ein echter Katalysator-Tag mit 3× Volumen zeigt intraday ~0,9 → weder +6 Technik-Punkte noch der Catalyst-Volume-Bonus (bis +10). Auch Up-Day-Check, `pos_90d` und `red_days` laufen auf der offenen Kerze. Der BI-Scanner strippt den Partial-Bar explizit — der Biotech-Pfad nicht, obwohl beide im selben File liegen.
**Auswirkung:** Derselbe Ticker scort im Morgen-Scan ~10–15 Punkte tiefer als im Abend-Scan → Grade-Flips (B↔C/D) nur durch die Uhr; Auto-Scans alle 2/6h machen das zum Dauerzustand. Zusatzbefund: Der Quick-Scan (alle 2h) überschreibt den Cache-Score und lässt dabei den `rvol_direction`-Parameter weg → Distribution-Tage bekommen den vollen RVOL-Bonus (Full Scan: nur 20%).
**Fix:** Letzten kompletten Tag verwenden (Analog `_bi_strip_partial_bar`) oder RVOL zeitanteilig hochrechnen; Quick-Scan `rvol_direction=old["Tech_Details"]["rvol_up_day"]` mitgeben.

### H8 · patterns.py: IndexError bei exakt 3 Swing-Punkten + nirgends definiertes `log` — stiller Totalausfall der Pattern-Analyse genau bei Triangle/Wedge-Tickern `[V]`
**Datei:** modules/patterns.py:4700–4701, 4716–4717, 4735–4736, 4893–4894, 4907–4908; Exception-Handler 5536 und 5625.
**Problem:** `recent_high_indices = [swing_highs[-4+i]["index"] for i in range(len(recent_highs))]` greift bei nur 3 Swing-Highs auf `swing_highs[-4]` zu → IndexError. Die Zweige laufen ab `len(swing_highs) >= 3` — der Crash tritt also genau dann auf, wenn ein gültiges Dreieck/Wedge mit exakt 3 Swings erkannt würde (per Repro bestätigt). Der Exception-Handler ruft `log.warning(...)` auf — aber `log` ist im Modul nirgends definiert (kein `import logging`) → der Fail-Soft-Pfad wirft selbst `NameError`, die Original-Exception wird verschluckt.
**Auswirkung:** Für Ticker mit Dreiecksbildung gehen **alle** Chart-Patterns verloren (auch Double Top, H&S, C&H, Candles, SMC), api.py fängt still per try/except → in Produktion unsichtbar und irreführend zu debuggen.
**Fix:** Indizes direkt aus den Swing-Objekten ziehen (`recent = swing_highs[-4:]`; Preise und Indizes aus derselben Liste); `import logging; log = logging.getLogger(__name__)` im Modulkopf. Danach Regressionstest mit exakt 3 Swings.

### H9 · Exhaustion-Score (Krypto-Short): die „beste Short-Trigger"-Dimension ist durch Z-Score-Vorzeichen unerreichbar `[V]`
**Datei:** modules/scorers.py:1562–1573 mit `_z_score` (43–54).
**Problem:** Für negatives `change_1h` ist der Z-Score gegen die fixe Referenzliste `[-2…2]` (Mittel 0, σ≈1,29) zwangsläufig **negativ** — die Bedingungen `change_1h < -2.0 AND z_score_1h >= 2.0` (+10), `< -0.5 AND >= 1.5` (+7) und der +4-Zweig sind für jede Eingabe False (nachgerechnet; der +4-Zweig verlangt gleichzeitig `change_1h < ~0,01` und `change_1h ≥ +1,29`).
**Auswirkung:** Ein Coin mit 7d +25% und 1h −4% — der klassische Kipp-Moment — bekommt 0 Timing-Punkte. Der Exhaustion-Score liegt systematisch bis 10 Punkte (von 75-Skala im SellProb-Pfad) zu tief; Grade-S/A und „JETZT"-Timing der Pump&Dump-Shorts kommen strukturell zu spät. Zusatz: Die Dimensions-Summe ist nominell 110, nicht 100; `rvol`/`prev_vol_24h`-Parameter sind tot.
**Fix:** Down-Zweige auf `z_score_1h <= -2.0` (bzw. `abs()`) umstellen; besser: echte Stunden-Volatilität des Coins als Referenz statt fixer Liste; Gewichte auf 100 normieren.

### H10 · VRVP: Kanten leerer Bins gelten als S/R-Level und „Barrieren" — Volume-Voids blockieren genau die Setups, die sie begünstigen `[A]`
**Datei:** modules/volume_analysis.py:119–131 (LVN = jeder Bin <50% Schnitt, kein Lokal-Minimum, kein Zonen-Merge), modules/vrvp_levels.py:142–147 (jede LVN-Kante wird eigenes Level), api.py:7853–7875 (Barrier-Downgrade).
**Problem:** Zusammenhängende leere Bins erzeugen eine Kaskade von „LVN edge"-Leveln an binraster-abhängigen Preisen. Repro: In einem sauberen Zwei-Cluster-Profil waren **alle 5 Resistances** Kanten von Null-Volumen-Bins; `_near_trade_barrier` wählte eine davon 0,66R über dem Entry und setzte `BREAK_RECLAIM_REQUIRED` → `alertable=False`. Fachlich invers: Ein Void über dem Entry ist ein Beschleunigungs-Vakuum (so definiert es euer eigenes Strategie-Set „Volume Void Long"), kein Widerstand. TP1 kann zudem auf solchen Artefakt-Kanten landen statt dort, wo Volumen real wieder einsetzt.
**Auswirkung:** Real handelbare Long-Setups werden auf WARTEN heruntergestuft; Target-Qualität leidet.
**Fix:** LVN-Bins zu Void-Zonen mergen und nur die Außenkanten (Volumen-Wiederanstieg) als Level führen; Barrier-Gate nur auf POC/VAH/VAL/HVN bzw. Gewicht ≥1,4.

### H11 · Early Movers: bestätigter Retest-Einstieg ist als Signaltyp faktisch tot — Scan, Mail und GET widersprechen sich `[V]`
**Datei:** api.py:4201–4215 (Promotion), 2024–2029 (Mail-Gate), 7905/7984–7987 (Canonical Decision).
**Problem:** Der `retest_hold`-Pfad promotet eine `WAIT_FOR_RETEST`-Row bei bestätigtem 5m-Retest zu `trade_signal="JETZT_TRADEN"`, `entry_status="JETZT_TRADEN"`, `alertable_crypto=True` — lässt aber `trade_action="WAIT_FOR_RETEST"` stehen. Das Mail-Gate blockt hart auf `action != "LONG_TRIGGER"` („AUDIT Q-1: nur LONG_TRIGGER ist mailbar"), und die kanonische Decision normalisiert `WAIT_FOR_RETEST` beim GET wieder in einen Wait-State (für `LONG_TRIGGER` existiert die Ausnahme, für den bestätigten Retest nicht).
**Auswirkung:** Einer von fünf designten Trigger-Typen (mit bewusst niedrigerem Threshold als „bevorzugter" Einstieg) erzeugt nie eine Trade-Mail und wird in der UI wieder zu „Warten"; gleichzeitig zählt `stats.trade_now_count` diese Rows als JETZT_TRADEN — Statistik, UI und Mail zeigen drei verschiedene Wahrheiten. Fail-closed (kein Fehltrade), aber ein totes Feature plus Widerspruch zur Handbuch-Regel „keine Widersprüche".
**Fix:** Bei bestätigtem Retest `trade_action` auf `LONG_TRIGGER` (oder eigenes Token `RETEST_CONFIRMED`, das Canonical Decision und Mail-Gate als executable kennen) umsetzen — oder die Promotion entfernen und den Zustand ehrlich als Watch führen. Eine der beiden Richtungen, aber konsistent.

---

## 5. Befunde — MITTEL

### Risiko-/Level-Schicht

**M1 · Kein Mindest-Stop-Abstand, kein R:R-Sanity-Cap `[V]`** — trade_levels.py:155 (`risk <= 0` ist die einzige Schranke), trade_health.py:381–389. Repro: Entry 100, Stop 99,99 → „Live R:R 450, TRADEABLE, Health 100, JETZT_TRADEN". Ein Datenglitch oder überenger Scanner-Stop (unter Spread-Niveau) passiert alle Gates mit absurdem R:R. Fix: `risk >= max(0.25·ATR, Spread, 0.1% Entry)` in trade_health, Warnung ab `live_rr > ~15`.

**M2 · `round_trade_price` zerstört Mikro-Preise `[V]`** — vrvp_levels.py:27–38: letzte Stufe `round(val, 8)`. 2,2e-9 → 0.0; gültige Geometrie eines BabyDoge-Klasse-Coins kollabiert zu Entry==Stop / TP1==TP2 → falsches NO_TRADE (fail-safe, aber Signal weg; POC/VAH/VAL im Payload 0.0). Fix: auf 6 signifikante Stellen runden (`float(f"{val:.6g}")`) — Einzeiler.

**M3 · VRVP-Stop-Tightening ankert an der HVN-Oberkante mit Mini-Puffer `[A]`** — vrvp_levels.py:352–364: nächster „Support" (oft die Oberkante des Volumenclusters) + Puffer max(0,25% Entry, **0,05×ATR**) und Risk-Kompression bis 55%. Repro: Stop wanderte in die Value Area (über POC), R:R optisch 2,27 → 4,64. Ein normaler Rücklauf in die Akzeptanzzone reißt diesen Stop. Fix: Stop-Kandidaten für Long nur HVN-Low/VAL/POC, Puffer ≥0,25–0,5×ATR.

**M4 · `atr`-Parameter von `apply_vrvp_to_trade_setup` bedeutet je Aufrufer etwas anderes `[A]`** — Callsites übergeben Tagesrange (api.py:12894), OR-Größe (22942), ATR5 (19547), ATR14 (11974) und beim NLS sogar `ATH − Preis` (new_listing_scanner.py:2084). Beim NLS-Short heißt das: `min_tp_reward = 0,7 × (ATH−Preis)` — kein VRVP-Level qualifiziert sich je, die Struktur-Targets sind dort faktisch stillgelegt. Fix: Parameter als `atr14` vertraglich festlegen, in der Funktion gegen Entry plausibilisieren, Callsites vereinheitlichen.

**M5 · `_live_rr` fällt bei unvollständigen Leveln auf den alten Scanner-R:R zurück `[A]`** — trade_health.py:168–190: Verstoß gegen Invariante „Live-R:R nie aus altem Scanner-Wert" (begrenzt: ohne valide Geometrie ist TRADEABLE unerreichbar, aber Score/UI tragen einen als „live" etikettierten Stale-Wert). Fix: `live_rr=None` + separates Feld `planned_rr`.

### Krypto-Pipeline

**M6 · BTC-Kontext-Gate fail-open `[A]`** — api.py:1961 (`tailwind`-Default True), 18165–18171 (fehlende BTC-Zeile ⇒ btc=0), 19411–19418 (Exception ⇒ btc_change=0 ⇒ tailwind=True). In Degraded-Data-Szenarien ist das wichtigste Makro-Gate der Long-Pipeline still deaktiviert — dieselbe Fehlerklasse wie der historische „fehlende News ⇒ LOW Risk". Fix: dreiwertig (None = unbekannt ⇒ WAIT), Exception ⇒ `tailwind=False` + Flag.

**M7 · `get_early_movers` liefert bis zu 30+ Minuten alte JETZT_TRADEN ohne Trigger-Downgrade `[A]`** — api.py:19074 ff.: `_downgrade_expired_crypto_triggers` existiert und läuft bei crypto_explosion (19766) und im Mail-Pfad — aber nicht im Early-Movers-GET; der Disk-Cache behält `JETZT_TRADEN`. Euer eigener Kommentar: „5m-Trigger sind nur ~3 Minuten belastbar." Fix: denselben Downgrade in `get_early_movers`/`_decorate_early_mover_results` einbauen.

**M8 · 5m-Trigger: Distanz-Leiter ohne Untergrenze, kein Stop-Breach-Block im Trigger selbst `[A]`** — api.py:3089–3099: `distance_r = −1,5` (unter dem Stop) erfüllt `<= 0.50` → +10; ein Bounce unterhalb des Stops kann Score 96 und `JETZT_TRADEN` in den Scan-Cache schreiben (Mail/Decorate fangen es via trade_health ab, Cache/Statistik nicht). Fix: `last_close <= stop ⇒ ok=False`; Leiter mit Unter- und Obergrenze.

**M9 · Ausschlusslisten lückenhaft; Dedupe/Perp-Match nur über Symbol `[A]`** — api.py:17303–17316, 18683, 18214: Solana-LSDs (JITOSOL/MSOL/BNSOL), RLUSD, solvBTC laufen als „Mover" mit (Beta zu SOL wird als Alpha gescort); Symbol-Kollisionen zwischen verschiedenen CoinGecko-Coins können Funding/OI/Konfluenz des falschen Assets zuordnen (der 2%-Preis-Guard fängt den Trigger, nicht die Scores). Fix: Terms/Symbole ergänzen, Dedupe-Key `(symbol, coin_id)`.

**M10 · `_classify_phase`: stark fallende Coins werden „Phase 1 Accumulation" `[A]`** — api.py:18048 ff.: −20%/24h landet in Phase 1 (alle höheren Phasen verlangen positive Moves) und bekommt den Phase-1-Boost (×1,05) und das „bester Einstieg"-Label. Fix: Downtrend-Ast (z.B. `c24 ≤ −8 ⇒ „Abverkauf"`, kein Boost).

**M11 · Mail-Frische-Gate misst Trigger-/Cache-Alter, nicht das Alter der Preisbasis `[A]`** — api.py:1981–2000, 19048–19058: `_mail_scan_age_sec` wird nach dem Cache-Write gestempelt (≈0s), die CoinGecko-Preisbasis stammt vom Scan-Beginn (Scans dürfen bis 25 min laufen). Swing-Mails können Live-R:R auf einer fast scan-alten Preisbasis ausweisen. Fix: `price_basis_at` stempeln, Gate auf `max(trigger_age, price_basis_age)`; vor Trade-Mail ein einzelner Ticker-Call.

### Aktien-Scanner

**M12 · ORB: Richtung + Volume-Confirmation auf der noch offenen 5m-Kerze `[A]`** — api.py:22798, 22825–22853: Mid-Bar-Tick über OR-High ⇒ sofort `active_breakout`; die 30 Sekunden alte Partial-Kerze kann die Volume-Confirmation der zuvor geschlossenen Breakout-Kerze ersetzen ⇒ Score-Flip-Flop zwischen Scans (bestätigt → Cap 64 → wieder bestätigt) und Mails auf unbestätigten Ausbrüchen. `_completed_candles_only` existiert und wird hier nicht benutzt. Fix: Detection/Confirmation auf geschlossene Kerzen, Live-Preis nur für Entry-Qualität.

**M13 · ORB Failed-Breakout-Setups: konstruktionsbedingt R:R ≈ 0,63 `[A]`** — api.py:22858–22879: Stop am gegenüberliegenden OR-Extrem (Risiko 1,25×OR) bei Target 0,75×OR. Richtung stimmt, aber als Trade wertlos — negative Erwartung selbst bei 60% Trefferquote. Fix: Stop über das Reversal-Extrem statt ans OR-Ende, oder Setups mit R:R<1 nicht listen.

**M14 · Turtle-Score: „Breakout-Stärke" ≡ „Entry-Qualität" `[A]`** — api.py:12391 vs. 12443: `overshoot` ist numerisch identisch mit `breakout_pct` (Entry = dc_high_20) → 40 von 100 Punkten hängen an einer einzigen Messgröße mit fast identischen Bändern. Fix: Faktor 5 durch echte Entry-Metrik ersetzen (Distanz in ATR/N, Retest-Nähe) oder Punkte umverteilen.

**M15 · Bear/Crash: 5m-State ohne Frische-Check — gehaltene (LULD) Aktien werden auf der Pre-Halt-Kerze beurteilt `[A]`** — api.py:5548–5571: `latest_bar_timestamp` wird gespeichert, aber nie gegen das Alter geprüft (das 15-Minuten-Gate existiert nur für early_movers). Ein um 10:12 gehaltener Titel kann um 11:30 noch als „aktiver Flush" alerten. Fix: Bar-Alter >15 min ⇒ wie `latest_missing` behandeln (fail-closed), idealerweise Halt-Status ins Row.

**M16 · Inverse-ETF-Hebelerkennung ist toter Code `[A]`** — api.py:12586–12610: `desc.upper()` enthält nie die kleingeschriebenen Literale `"3x"/"2x"` → `leverage` bleibt 1.0, `decay_warning` ist für alle 17 Einträge False (inkl. SQQQ/SPXS/UVXY). Die Decay-Warnung — der Kernzweck der Liste — erscheint nie. Fix: konsistent lowercase vergleichen; `decay_warning = leverage>1 or "short" in desc.lower()`.

### Backtest/Tracking/Kontext

**M17 · Backtest-Statistik zählt Exit-Reasons, die es nicht mehr gibt `[A]`** — backtests.py:1546–1549 vs. 1333 ff.: Nach der Umstellung auf das Blended-TP-Modell emittiert `simulate_trade` nur noch `TP1_STOP/STOP/BLENDED_TP/TP1+EOD/EOD` — `tp2_rate` ist immer 0, `tp1_rate`/`stop_rate` verpassen Kategorien; bei 0 Verlierern wird `avg_loss=1` gefaked statt PF sauber als ∞/n.a. auszuweisen. Dazu: Dedup-Key ohne Strategie (192–204) → die erste Strategie in der Liste „klaut" überlappende Signale, das Strategie-Ranking hängt von der Iterationsreihenfolge ab; Equity/Max-Drawdown über tickerweise statt chronologisch sortierte Trades (792, 1210) → Cluster-Risiko unterschätzt; `prev_high`-Entries füllen bei Gap-Open zum Triggerpreis statt zum Open (1267/1320, Bull Flag) → Phantom-Gewinne; BI-V2/Biotech simulieren erst ab `idx+1` und überspringen den ersten (oft entscheidenden) Tag nach dem Signal, Biotech etikettiert `signal_date` einen Tag zu spät (356/540/964/1021).

**M18 · Krypto-Tracker bewertet gegen ungeprüft alten Datei-Cache; UTC-Datum verschluckt den ersten Handelstag `[A]`** — signal_tracker.py:582–584 + bg_service.py:1298–1329: Der Preis-Fetcher liest den CoinGecko-Markets-Cache ohne dessen `ts` zu prüfen — läuft der einzige Writer (BTC-Divergenz-Scan) nicht, werden Stops/TPs gegen Stunden bis Tage alte Preise „getroffen". Aktien: `created_date` in UTC statt ET (487–488) → Alerts nach 20:00 ET filtern den kompletten ersten Handelstag aus der Bewertung. Fix: Cache-TTL prüfen (sonst sauber als Fehlversuch), `created_date` als ET-Handelsdatum.

**M19 · Event-Risiko: Datum-only-Events = Mitternacht UTC `[A]`** — market_context.py:378–389: Events ohne Uhrzeit fallen ab 02:00 UTC des Ereignistags aus dem Fenster (`hours < −2`) — das Risiko ist am Morgen des Events, also genau dann, wenn es am höchsten ist, 0. Fix: Datum-only als ET-Tagesfenster behandeln.

**M20 · Penny: Volumenprojektion in den Open-Minuten um Größenordnungen zu hoch; eingefrorene `now_ts` `[A]`** — api.py:21367 ff.: Polygon `day.v` enthält Premarket-Volumen, der Nenner (`_us_equity_expected_volume_fraction`) modelliert nur die Regular-Session → Minute 1 multipliziert mit ×62 (5M PM-Shares werden zu „318M projiziert"). Gates bleiben durch 5m-Trigger/Spread hart, aber Ranking/Anzeige/RVOL-Semantik sind direkt nach Open verzerrt. Dazu: `now_ts` wird vor der bis zu ~8-minütigen Deep-Loop eingefroren und die Kauf-Mail prüft die 360s-Trigger-Frische am Ende nicht erneut. Fix: PM-Anteil herausrechnen bzw. Projektion erst ab Minute 15; Age-Recheck + Live-Spread vor Mail.

**M21 · NLS: Score-Normalisierung /160 benachteiligt Exchanges ohne Funding/L-S-Daten; Instrumenten-Cache-Diff ohne Plausibilität `[A]`** — new_listing_scanner.py:1674 (Crypto.com kann maximal 84/100 erreichen — fehlende Daten wirken wie negative Evidenz; Grade S dort fast unerreichbar) und 1054–1071 (eine partielle Instrumentenliste macht beim nächsten vollen Fetch Hunderte alte Coins zu „neuen Listings" — dieselbe Fehlerklasse wie der historische CoinGecko-Partial-Bug). Fix: gegen erreichbares Maximum normalisieren; Diff nur akzeptieren, wenn `len(current) >= 0.8 × len(cached)`.

### Pattern-/Score-Detail

**M22 · patterns.py-Sammelfund `[A]`** — Flag ohne Fahnenstange kann `is_valid` werden (Score 45 ≥ 40 mit gleichzeitig „Fahnenstange schwach" + „zu tiefes Retracement" in den Details; patterns.py:72–138); Signal 16 (FVG-Magneten) ist per Konstruktion unerreichbar (falsche Zonenliste — `unfilled_bull` liegt definitionsgemäß unter dem Preis, Filter verlangt darüber) → reales Maximum 177 statt 183, Confidence deflationiert; Signal 11 „Relative Stärke" vergleicht nicht gegen einen Benchmark und belohnt 42,6% reiner Zufallsserien mit Grade-relevanten Smart-Money-Punkten (Monte-Carlo, 20.000 Läufe); Krypto-Modus zählt Body-Kompression doppelt (Signal 2 + Signal 20, +10 für ein Phänomen); C&H-Chart-Heuristik meldet „Breakout" an einem Segment-Durchschnitt deutlich unter dem echten Rim (Lehrbuch-Cup: Lip 90,91 bei Rim 100) und divergiert damit sichtbar vom — korrekt gebauten — strikten Scanner in api.py.

**M23 · VWAP-Bänder ≠ deklarierte TradingView-Formel; vier ATR-Definitionen im Umlauf `[A]`** — indicators.py:478–485: Varianz gegen den mitlaufenden statt kumulativen VWAP → Bänder bei kurzen Serien bis ~47% zu eng („überdehnt"-Fehlalarme früh in der Session; aktuell nur Legacy-Chart-Konsument). Parallel existieren SMA-ATR (api.py:9949), Single-Bar-TR (Legacy), 24h-Range-Proxy (Krypto) und das korrekte, aber **nirgends aufgerufene** `calculate_atr_14` — `get_volatility_regime` bekommt je Pfad inkommensurable Größen. Fix: TV-Formel `Σ(v·tp²)/Σv − vwap²`; eine kanonische ATR.

---

## 6. Befunde — NIEDRIG (kompakt)

| # | Fund | Datei | Bemerkung |
|---|---|---|---|
| L1 | `or`-Falle: `_alert_float(rvol,1.0) or 1.0` macht RVOL 0.0 zu 1.0 — Turtle-Cap entfällt genau bei 0-Volumen | api.py:9318 | exakt die Handbuch-§21-Fehlerklasse `[A]` |
| L2 | Fear-Score „5D Strength": Sprung 70→80 bei avg_5d=1,0 (Formel-Tippfehler, gedacht war `70+(x−1)/2·30`) | api.py:23372 | kosmetisch `[A]` |
| L3 | MarketSweepScore: `+12` bedingungslos, nominelles Max 80 < Clamp 82 — Sweep-Kandidaten knapp unter Mail-Schwelle gedeckelt | api.py:18290, 18326 | Kalibrierung `[A]` |
| L4 | Phasen-Quote („Phase 2+3 immer zeigen") wird direkt nach dem Zusammenbau überschrieben — toter Code, Verhalten ≠ Kommentar | api.py:18957–18974 | `[A]` |
| L5 | `_biotech_binary_event_days`: explizites negatives `days_until` umgeht den Negativ-Filter und das T-3-Mail-Gate | api.py:2251 | Row-Daten müssen inkonsistent sein `[A]` |
| L6 | NLS: Funding-Kommentar rechnet 0,3%/8h als „3,6%/Tag" (real 0,9%); Depth-Detailzeile zeigt falschen Beitrag | new_listing_scanner.py:1486, 1434 | nur Text `[A]` |
| L7 | NLS: `min_bars=12` an VRVP wirkungslos (Funktion verlangt hart 20) — junge Listings bekommen still nie Struktur-Level | new_listing_scanner.py:2060 | `[A]` |
| L8 | NLS: bei Ticker-Fetch-Fehler wird auch die Zeit-Expiry aktiver Signale übersprungen; Monitoring-JSON wächst unbegrenzt | new_listing_scanner.py:2899, 2532 | `[A]` |
| L9 | `infer_trade_direction`: `text == "SELL"` ist toter Vergleich (Join-Kette) — SELL-Rows verlieren Richtung + Level-Schätzung (BUY nicht) | trade_levels.py:52–66 | fail-closed, aber asymmetrisch `[A]` |
| L10 | `get_ticker_news` gibt bei HTTP-Fehler `[], []` (Tupel) statt Liste — einziger Caller schützt sich manuell | data_fetchers.py:1129 | API-Vertrag `[A]` |
| L11 | Datenfetcher-Kanten: BPIQ-Teilpagination 4h als Erfolg gecacht; OHLC mit v=0 gecacht; stille 90-Tage-Kappung (Wyckoff/Weekly fordern 120–730); 4H-Aggregation ohne Session-Alignment | data_fetchers.py div. | `[A]` |
| L12 | market_context: Fail-closed hängt allein an Aufrufer-Konvention (`missing_*`-Dicts); VIX/Breadth formal doppelt gewichtet | market_context.py:428 ff. | `[A]` |
| L13 | Legacy-Drift-Fallen: Root-`new_listing_scanner.py` (alte permissive Schwellen) und Root-`volume_profile.py` (inkompatibles Schema, divergente HVN/LVN-Definition) sind tot, aber verwechselbar | Root-Dateien | löschen/Stub `[A]` |
| L14 | Legacy-Anzeige (nur scanner.py-Pfad): OBV-„Trend" mit willkürlichem Nenner; Sub-Cent-`round(,2)` in Chart-Overlays; Wyckoff-Marker löschen Harmonic-Marker; „Fibonacci"-Fallback sind ±5/10/15%-Fantasielevel; Feiertage fehlen im Session-Check | indicators/chart_utils/helpers | nicht produktiv `[A]` |

---

## 7. Priorisierter Fix-Plan

Sortiert nach (Schaden × Sichtbarkeit) / Aufwand. „Aufwand": S < 1h, M = halber Tag, L = 1–2 Tage inkl. Tests.

| Prio | Fix | Findings | Aufwand |
|---|---|---|---|
| 1 | MEXC auf `amount24` + `contractSize` umstellen | H1 | S |
| 2 | RVOL-Zeitnormierung Bear/Crash/Turtle (vorhandene Funktion einsetzen) | H5 | S |
| 3 | patterns.py: Swing-Index-Fix (5 Stellen) + `log` definieren + Regressionstest „exakt 3 Swings" | H8 | S |
| 4 | Exhaustion-Z-Score-Vorzeichen | H9 | S |
| 5 | Announcement-Präzedenz beim Listing-Alter umdrehen | H6 | S |
| 6 | Biotech: Partial-Bar-Strip + Quick-Scan-`rvol_direction` | H7 | S |
| 7 | Backtest-History ungefiltert aufbauen (Filter nur am Signaltag) | H3 | S–M |
| 8 | CoinGecko-Granularität: Daily aus market_chart-Hourly aggregieren + Bar-Abstands-Assertion; Krypto-Backtests danach neu bewerten | K1 | M |
| 9 | Tracker: Fill-Gate, NO_FILL-Bucket, Win-Rate an `r_realized>0`, Gap-Stops real, ET-Datum, Cache-TTL | H2, M18 | M–L |
| 10 | Mindest-Risk-Gate + R:R-Sanity-Cap in trade_health; `round_trade_price` auf 6 signifikante Stellen | M1, M2 | S |
| 11 | LVN-Zonen-Merge + Barrier-Gate nur auf gewichtige Level | H10 | M |
| 12 | Retest-Pfad konsistent machen (Token `RETEST_CONFIRMED` durch Mail-Gate + Canonical Decision ziehen) | H11 | M |
| 13 | ORB auf geschlossene Kerzen; Failed-Breakout-Geometrie oder Delisting von R:R<1 | M12, M13 | M |
| 14 | AutoTrader: STP-LMT-Entry, Live-Chase-Guard, Fade vor Gate, Partial-Bar-Strip — bis dahin `full` nicht freigeben | H4 | M |
| 15 | Early-Movers-GET-Downgrade + BTC-Kontext dreiwertig + Stop-Breach im Trigger | M6–M8 | M |
| 16 | Backtest-Statistik: Exit-Reason-Mapping, Dedup-Key mit Strategie, chronologische Equity, Gap-Fill am Open, Tag-1-Lücke | M17 | M |

Nach Handbuch-Regel gilt für jeden Fix: gezielter Regressionstest (Long+Short, Grenzwerte, fehlende Daten), Full Pytest, Compile — die 887er-Suite hat diese Fehler nicht gefangen, also braucht jeder Fix seinen eigenen neuen Test.

---

## 8. Strukturelle Verbesserungen (über Bugfixes hinaus)

**1. Eine kanonische RVOL.** Es existieren mindestens fünf RVOL-Definitionen (mit/ohne Zeitnormierung, Mean/Median, mit/ohne Signaltag im Nenner, 1-Tages- vs. 20-Tage-Baseline). Der 20-Tage-Schnitt inklusive Signaltag staucht echte 10×-Tage auf angezeigte 6,9× (Formel 20k/(19+k)). Eine gemeinsame Funktion `rvol(bars, now, mode)` — Denominator ohne Signaltag, zeitanteilig, Median-Option — beseitigt die Uhrzeit- und Definitionsdrift in einem Schritt und macht Schwellen zwischen Scannern erstmals vergleichbar.

**2. Eine kanonische ATR.** `calculate_atr_14` ist korrekt und wird nirgends aufgerufen; stattdessen SMA-ATR, Single-Bar-TR, 24h-Range und `ATH−Preis` unter demselben Namen. Vertrag festlegen (`atr14` je Timeframe), `get_volatility_regime`-Grenzen einmal darauf kalibrieren.

**3. Kosten in die Live-R:R.** Der Penny-Scanner rechnet Netto-R:R mit Spread und Slippage — sonst niemand. Bei Krypto (0,1–0,3% je Seite) frisst das bei engen Stops 20–60% des Risikos. Spread/Fees als optionalen Abzug in `trade_health` einbauen und in der Mail als „netto" ausweisen.

**4. Der Track-Record als Produkt-Asset.** Nach dem H2-Fix: NO_FILL-Quote, Win-Rate auf r-Basis, R-Verteilung je Scanner/Grade/Regime und Signalalter auswerten (die Felder existieren schon). Erst dieses Zahlenwerk erlaubt die im Handbuch geforderte evidenzbasierte Schwellen-Kalibrierung — und es ist ehrlicher gegenüber zahlenden Kunden.

**5. Walk-Forward/Out-of-Sample fehlt komplett.** Kein Backtest-Pfad hat einen Zeit-Split; `stats_by_grade` erscheint schon ab n=1. Minimalversion: letzte 2 Monate als Holdout, Sample-Gate n≥30, sonst „zu wenig Daten" — sonst kalibriert ihr Schwellen auf Rauschen.

**6. Granularitäts-Assertions als Klasse.** K1 wäre durch eine einzige generische Prüfung („gemessener Bar-Abstand == deklarierter Timeframe, sonst Fehler") unmöglich gewesen. Diese Assertion in alle Kerzen-Fetcher (CoinGecko, Exchanges, Polygon-Aggregation) einziehen — sie schützt auch gegen künftige stille API-Änderungen.

**7. Legacy-Bereinigung.** Root-`new_listing_scanner.py`, Root-`volume_profile.py` und die nur noch von `scanner.py` konsumierten Anzeige-Pfade (OBV-Trend, VWAP-Band-Chart, Fibonacci-Fallback) sind Verwechslungs- und Drift-Fallen. Löschen oder auf ImportError-Stubs reduzieren — deckt sich mit Handbuch-Schuld Nr. 2.

---

## 9. Einordnung

Von den ~30 historischen Fehlern aus Handbuch §21 wurde **keiner wieder eingeführt** — die Regressionsdisziplin funktioniert. Die neuen Funde sind überwiegend **neue Vertreter bekannter Fehlerklassen an anderen Stellen**: die `or`-Falle beim Turtle-RVOL, fail-open beim BTC-Kontext, die Partial-Cache-Klasse beim NLS-Instrumenten-Diff, Partial-Bar beim Biotech- statt BI-Scanner, Einheitenfehler bei MEXC statt beim Kontrakt-Multiplier. Das spricht dafür, die §21-Liste von konkreten Stellen auf **Fehlerklassen mit Suchmuster** umzustellen und bei jedem neuen Scanner als Checkliste zu erzwingen.

Kein Befund in diesem Report ist eine Aussage über künftige Gewinne. Der Report macht die Rechenwege ehrlich — profitabel machen sie nur Kalibrierung gegen echte Forward-Daten.
