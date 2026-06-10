# Voll-Audit Alpha Station — 10.06.2026

**Stand:** Commit `d9f7144` (HEAD) · Letzter gemeinsamer Audit-Stand: `a4d271f` · Seitdem: ~20 Commits der anderen KI, +26.049 / −3.162 Zeilen in Kernmodulen, 4 neue Module (market_context, trade_health, trade_levels, vrvp_levels), Auth/Commerce stark ausgebaut.

**Methodik:** Komplette Test-Suite ausgeführt + 4 parallele Tiefen-Audits (Mathematik, Stock-Logik, Crypto-Logik, Code/Commerce) mit eigenen Verifikationsskripten gegen Referenzimplementierungen. Die 4 schwersten Befunde habe ich zusätzlich selbst am Code/zur Laufzeit reproduziert.

---

## 1. Test-Suite: GRÜN — aber Tests ≠ Geschäftsregeln

| Suite | Ergebnis |
|---|---|
| pytest (33 Dateien) | **342/342 PASS** |
| test_breakout_audit.py (main) | 6/6 PASS |
| test_setup_score.py (main) | 45/45 PASS |
| test_trading_logic.py | 41/41 PASS |
| test_bearish_finisher.py | 3/3 Cases OK |

**Achtung:** Die Suite ist grün, aber sie zementiert teilweise falsches Verhalten — siehe Befund S-1: Es existieren Tests, die RVOL 0.82-Breakouts als SOLL-Verhalten festschreiben.

---

## 2. KRITISCHE BEFUNDE (vor Abo-Launch zwingend fixen)

### S-1 · RVOL-Floor 1.5 für Breakouts wurde abgesenkt — Geschäftsregel verletzt ⚠️
**api.py:279, api.py:200-203, api.py:889** — Deine nicht verhandelbare Regel (RVOL ≥ 1.5 für Breakouts) ist nicht mehr im Code. Commit `5ea38aa` (03.06.) hat "Momentum Breakout Long" auf **RVOL (0.7, 100)** gesenkt; das alte "Breakout Long" (1.5) ist nur noch ein Alias darauf. Mail-Gate `_ALERT_MIN_RVOL = 0.7`. End-to-end verifiziert: Ein Breakout mit RVOL 0.8 wird als Grade A, Score 87, `alertable_now=True` gemailt. Zusätzlich: MDR-/Blowout-Bypass (api.py:9341) schaltet den RVOL-Filter komplett ab (RVOL 0.15 passiert). Tests `test_momentum_breakout_allows_quieter_20d_swing_breakout` (RVOL 1.12) und `..._flat_day_structural_20d_breakout` (RVOL 0.82) schreiben den Verstoß als Soll fest.
**Fix:** Floor 1.5 in Filter + Gate-Ästen + als hartes Mail-Gate wiederherstellen; die beiden Tests drehen; MDR-Bypass an RVOL ≥ 1.5 koppeln.

### S-2 · trade_health erkennt gerissene Stops nicht — totes Setup wird "TRADEABLE/100"
**modules/trade_health.py:115-126, 309-320, 510-553** — Es gibt keine Prüfung `current vs stop`. Selbst reproduziert: LONG Entry 100 / Stop 95 / Preis **92** → `decision=TRADEABLE, health=100`. Negative `distance_to_entry_r` erzeugt sogar das Positivum "Entry nahe am Trigger"; `_live_rr` clamped mit `max(current, entry)` und liefert volles R:R. Kein nachgelagertes Gate fängt das ab (Early-Mover-Distanz-Gate prüft nur > +0.35R nach oben). Bei 15-30-Min-Scan-Zyklen und Crypto-Volatilität realistisch: Abonnent kauft ein invalidiertes Setup.
**Fix:** In `calculate_trade_health`: LONG `current <= stop` / SHORT `current >= stop` ⇒ `NO_TRADE / setup_invalidated`. Test ergänzen (Suite deckt nur Chase nach oben ab).

### S-3 · MACD-Signal & EMA-Overlays auf zeitverkehrter Serie (Ticker-Detail)
**api.py:11904 + 12010-12024 + 12226-12242** — `closes` kommt `sort=desc` und geht direkt in `calculate_ema_series()` (erwartet chronologisch). MACD-Signal/Histogramm damit wertlos (Beweis: Histogramm api **+7.28** vs. korrekt **+0.22**, Vorzeichen kippt) → der MACD-Faktor im 10-Faktor-Signal-Score bewertet systematisch falsch. EMA20/50-Chart-Overlay: neueste 19 Kerzen `None`, ältere mit Lookahead-Fehler bis 14.7 %.
**Fix:** Serie vor EMA-Berechnung einmal `reversed()` — drei Stellen.

### S-4 · OBV-Änderung ist immer 0 (Tupel-Bug)
**api.py:16564** — `calculate_obv()` gibt `(liste, trend)` zurück; `len(tuple) >= 6` ist nie wahr → `obv_change = 0.0` für jeden Ticker, OBV-Score-Bonus feuert nie (Narrative-/Sektor-Scanner).
**Fix:** `obv_values, _ = calculate_obv(...)`.

### S-5 · Exhaustion-Score: +15 Phantom-Bonus "Real Decoupling" bei fehlenden Daten
**modules/scorers.py:1508-1546** (Aufrufer bg_service.py:2503, scanner.py:5385) — `corr_14d=0.0` als Default bei fehlenden 14d-Listen ⇒ `corr < 0.3` ⇒ +15 Punkte + falscher Begründungstext "Correlation 0.00 — unabhängiger Pump!". Beide Produktions-Aufrufer übergeben die Listen nicht ⇒ jede Coin bekommt den Bonus, Grades ~1 Stufe zu hoch.
**Fix:** Bonus nur vergeben, wenn Korrelationsdaten tatsächlich vorhanden.

### S-6 · Commerce: fail-open-Architektur — Default-Secrets & Paywall default AUS
**modules/auth.py:63-77, api.py:846-850** — JWT-Default-Secret und Admin-Master-Key (`AlphaStation2026!`) stehen im Repo; `ALLOW_LEGACY_ADMIN_MASTER_KEY` default **an** (legt bei Login automatisch Elite-Admin-Konto an); `COMMERCE_ENFORCE_AUTH` default **aus** (alle Scanner-Endpoints öffentlich). Ein vergessenes ENV beim Deploy = offene App mit fälschbaren Tokens.
**Fix:** Fail-closed booten: im Commercial-Mode `RuntimeError` beim Start, wenn Default-Secret/Legacy-Key aktiv. Plus: Deploy-Artefakte inkonsistent (systemd-Unit startet Streamlit:8501, `safe_deploy.sh` erwartet `tradingbot-api`/`tradingbot-frontend`-Units, die im Repo fehlen; nginx ohne TLS-Block).

---

## 3. HOCH (zeitnah nach den Blockern)

| # | Befund | Ort |
|---|---|---|
| H-1 | **MEXC OI/Volumen in Kontrakten statt USD** (Faktor bis 10.000 daneben) — Liquiditäts-Hard-Block (PerpVolume < 2 Mio $) faktisch wirkungslos; `amount24` liegt korrekt im selben Response | api.py:14310, scanner.py:4546 |
| H-2 | **ORB: Cooldown vor Versand gesetzt, ohne `if sent:`, nur in-memory** — Restart verschluckt ORB-Signale für 8h bzw. doppelt sie | api.py:18493-18532 |
| H-3 | **trade_horizon-Routing funktionslos:** kein Sender übergibt den Parameter → Intraday-Abonnenten bekommen gar keine Mails, Swing-Abonnenten bekommen ORB-Intraday | api.py:5191, auth.py:746 |
| H-4 | **Turtle-Scanner strukturell tot:** Rows ohne TP1/TP2 → immer `estimated` → immer NO_TRADE, kein Mail-Sender existiert; Tab zeigt Grade-A-Setups, die das System selbst als "nicht traden" labelt | api.py:10060-10080 |
| H-5 | **Backtest-Live-Divergenz Momentum Breakout:** Backtest nutzt alte Regeln (Change ≥ 3 %, kein RVOL), live ganz andere — ausgewiesene Statistik beschreibt anderen Signalstrom | api.py:225, strategies.py:784 |
| H-6 | **Final-Health-Override fehlt für crypto_explosion/crypto_trade_signals/btc_divergenz:** Row kann gleichzeitig `JETZT_TRADEN` und `trade_decision=NO_TRADE` tragen — widersprüchliche Tabs | api.py:893-897, 6127 |
| H-7 | **btc_divergenz: 3 Implementierungen, 3 Regelwerke.** Streamlit-/bg-Variante: "JETZT SHORTEN" ohne BTC-Schwäche-Gate, ohne Stop/TP, niedrigere Schwellen. api-Variante korrekt (watch-only) → konsolidieren | scanner.py:5270/5427, bg_service.py:2515 |
| H-8 | **Crypto.com-NLS-Pfad still tot:** Candle-Feld `vv` existiert nicht → volume_usd=0 → jedes Listing scheitert am "Coin tot"-Check | new_listing_scanner.py:290 (modules) |
| H-9 | **Zwei Scheduler scannen doppelt** (api + bg_service: crash_monitor, btc_divergenz, bear, strategy_scan, orb) + **Polygon-Limiter nur prozess-lokal** (2×200/min) | api.py/_scheduler_loop, bg_service.py |
| H-10 | **`save_cache_file` nicht atomar** + Caches zwischen 2 Prozessen geteilt → korrupte/leere Scanner-Resultate möglich (bg_service macht es richtig: tmp+replace) | api.py:6956 |
| H-11 | **Paywall-Lücken:** 6 wertvolle Endpoints (crypto-explosion, crypto-trade-signals, narrative-pulse, market-context, 2× chart) nur Token-, nicht Plan-gegated → gekündigtes Abo liest weiter | api.py:11075-11098 |
| H-12 | **Kein Login-Brute-Force-Schutz**, Passwort-Mindestlänge 6; `/api/test-email` unauthentifiziert | api.py:11258, 11782 |
| H-13 | **Stub-Importe in scorers.py:** `detect_chart_patterns` returns [] (Pattern-Kategorie kann nie bestehen, Vetos feuern härter); `close_position`-Duplikat ohne Clamp (bg_service importiert falsche Version, liefert z.B. 1.5); `estimate_crypto_atr` 2× mit divergenten Tiers (Faktor 3.4 möglich) | scorers.py:83-100, 61-80 |
| H-14 | **CoinGecko-Partial-Cache-Bias:** bg-Writer cached 429-Teilabrufe ohne Partial-Flag (api-Writer ist gehärtet) → Coins Rang 250-1000 verschwinden still aus early_movers/btc_div | bg_service.py:2284-2311 |
| H-15 | **NLS-Short-Signal ist One-Shot:** nach 1 Zyklus verschwindet der Coin komplett — keine Invalidierungs-Anzeige für die offene Empfehlung | new_listing_scanner.py:2688, 2910 |

## 4. MITTEL (Auswahl, vollständig in den Detailberichten)

BTC-Divergenz-Z-Score ~√5 überdispersioniert (52.7 % statt 13 % Alarmquote; api.py:16404) · Backtest-RVOL erste 20 Bars = Rohvolumen (api.py:20825) · 3 RSI-Implementierungen mit 3 Ergebnissen (Wilder korrekt / flat→100 statt 50 / Cutler im Backtest) · ATR mal Wilder, mal SMA quer durch die App · Cup&Handle bestätigt ab RVOL 1.1 auf unfertiger Tageskerze (api.py:8657) · Compression Breakout Floor 1.3 statt 1.5 · ORB ignoriert Half-Days (3 Termine 2026) · Börsenkalender endet 2026 → Zeitbombe 01/2027 · Bear-Mail prüft `_EMAIL_SEND_LOG[-1]` (Thread-Race) · Value-Area-Berechnung greedy statt POC-Expansion (VA bis Faktor 1.8 zu breit) + 2 Engines mit widersprüchlichen POCs · Doji-Bars verlieren Volumen im Profil · VWAP rundet auf 2 Dezimalen (Sub-Cent-Coins → 0.0) · Funding-Raten nicht intervall-normalisiert (4h vs 8h) · Symbol-Kollisionen CoinGecko↔Perp ohne ID-Check · Grade-Skalen scanner-übergreifend inkonsistent (S≥80 vs S≥88) · `_EMAIL_COOLDOWN` ohne Lock (Iteration+Delete-Race) · Stripe-Webhook ohne Event-ID-Dedupe (Trial-Verlängerung replaybar) · JWT 72h ohne Revocation · CORS mit hartkodierter IP + credentials.

## 5. Was VERIFIZIERT SAUBER ist

Kern-Indikatorbibliothek **modules/indicators.py: alle 35 Checks bestanden** (RSI Wilder exakt, ATR inkl. Gap-TR, EMA/SMA/MACD/Stochastik/CMF/Pivots, VWAP-Kern, close_position mit Clamp). **trade_levels.py-Geometrie:** 30.000-Fall-Fuzz ohne eine einzige Long/Short-Verletzung (Stop<Entry<TP1<TP2 bzw. gespiegelt). **RVOL-Definition überall ohne Self-Inclusion-Bias**, ORB-EVF-Intraday-Kurve korrekt. Fibonacci direktional korrekt (api). Score-Clamps greifen alle. **early_movers-Zustandsmaschine vorbildlich** (kein KAUFEN ohne validen frischen Trigger konstruierbar), bear-Scanner-Gates vorbildlich, bi_long/bi_short sauber, Gap Momentum hält RVOL 1.5 ein, crypto_strategy strikt observe-only, signal_only-Politik konsistent durchgesetzt. **Commerce-Substanz gut:** PBKDF2 260k timing-safe, Stripe-Signaturprüfung korrekt, Plan-Check live aus DB bei jedem Request, keine Injection/Path-Traversal/eval, parametrisierte SQL-Queries, Admin-Endpoints geschützt.

## 6. Empfohlene Fix-Reihenfolge

1. **S-1** RVOL-Floor 1.5 wiederherstellen (Geschäftsregel) + Tests drehen
2. **S-2** Stop-Breach-Check in trade_health (gefährlichster Logikfehler)
3. **S-6** Commerce fail-closed (Boot-Abbruch bei Default-Secrets/offener Paywall) + Deploy-Units/TLS
4. **S-3/S-4/S-5** Mathe-Bugs (3 kleine, klar umrissene Fixes)
5. **H-1…H-15** in Tabellen-Reihenfolge; H-2/H-3 (Alert-Zustellung) und H-10/H-11 zuerst
6. MITTEL-Block iterativ; Kalender 2027 vor Jahresende

**Launch-Urteil:** Substanz ist da — Indikator-Kern, Trade-Geometrie und die neuen Gate-Systeme sind solide gebaut und die Suite ist grün. Aber: 6 kritische Befunde, davon 2 direkt signalverfälschend (S-1, S-2) und 1 sicherheitskritisch (S-6). **In diesem Zustand kein zahlender Kunde.** Mit den Fixes aus Schritt 1-4 (geschätzt 1-2 Arbeitstage) ist die Basis launch-fähig; H-Block parallel zur Beta.
