# Fix-Report Voll-Sanierung — 10.06.2026

Alle Befunde aus `AUDIT_REPORT_VOLLAUDIT_2026-06-10.md` wurden umgesetzt. Backup vor den Fixes: `backup_pre_sanierung_20260610.tar.gz`.

## Verifikation (finaler Stand, von mir unabhängig geprüft)

| Check | Ergebnis |
|---|---|
| pytest komplette Suite | **434/434 PASS** (vorher 342 — 81 neue Regressionstests + Umbauten) |
| main()-Harnesses | 6/6 Breakout, 3/3 Bearish-Finisher, 45/45 Setup-Score, 41/41 Trading-Logic |
| Import-Smokes | api, bg_service, scanner — OK |
| Syntax aller .py (ast.parse) | 0 Fehler |
| S-2 Laufzeit-Repro | LONG 100/95 @ 92 → **NO_TRADE, health 15, CRITICAL** (vorher TRADEABLE/100); SHORT analog; Pullback @ 98 bleibt korrekt TRADEABLE |
| S-6 Laufzeit-Repro | COMMERCE_ENFORCE_AUTH=1 ohne JWT_SECRET → **RuntimeError beim Boot** (fail-closed); Legacy-Admin-Key default AUS |
| S-1 Laufzeit-Repro | Mail-Gate: Breakout-Strategie → 1.5, bi_long (Pre-Breakout) → 0.7; Gate-Äste alle ≥ 1.5 |

## Umgesetzte Fixes

### KRITISCH (alle 6)
**S-1 RVOL-Floor 1.5:** Filter "Momentum Breakout Long" (1.5, Change ≥ 2 %), alle 4 Momentum-Gate-Äste auf 1.5, MDR-/Blowout-Bypass nur noch mit RVOL ≥ 1.5, neues scanner-/strategiebasiertes Mail-Gate `_alert_min_rvol_for_row` (Breakout-Pfade + turtle hart 1.5; bi_long bleibt 0.7 als bewusste Pre-Breakout-Ausnahme). Die zwei Tests, die 0.82/1.12-Breakouts als Soll festschrieben, wurden gedreht (jetzt: Ablehnung + Positivfall ≥ 1.5).
**S-2 Stop-Breach:** trade_health erkennt gerissene Stops (LONG current ≤ stop, SHORT current ≥ stop, == zählt als Breach) → NO_TRADE / health ≤ 15 / CRITICAL / live_rr 0; Clamping maskiert nichts mehr; negatives Entry-Distanz-"Positivum" entfernt.
**S-3 MACD/EMA zeitverkehrt:** Ticker-Detail rechnet EMA12/26/Signal und EMA20/50-Overlays jetzt auf chronologischer Serie; Overlay-Alignment zu Chart-Bars korrigiert.
**S-4 OBV-Tupel:** `obv_values, _ = calculate_obv(...)` — obv_change liefert wieder echte Werte.
**S-5 Phantom-Decoupling:** +15-Bonus nur noch bei tatsächlich vorhandenen 14d-Korrelationsdaten (Default None statt 0.0).
**S-6 Commerce fail-closed:** `enforce_commercial_boot_security()` (Auto-Aufruf bei COMMERCE_ENFORCE_AUTH=1) → RuntimeError bei Default-/fehlendem JWT-Secret oder aktivem Legacy-Key; Legacy-Key-Default "0"; ephemeres Zufalls-Secret im Dev-Mode; Passwort-Minimum 10; Stripe-Webhook-Event-Dedupe (persistent, idempotent); JWT_EXPIRY_HOURS (Default 24h, war 72).

### HOCH (alle 15)
H-1 MEXC-Einheiten: USD-Volumen aus `amount24`, OI mit contractSize bzw. `oi_usd_estimate`-Flag + konservative Folge-Schwellen (Whale-Score gedeckelt, ExhScore ohne Schätz-OI) — in scanner.py, modules/new_listing_scanner.py; alter best_oi-Bug mitbehoben. · H-2 ORB-Cooldown erst nach `sent=True` + persistentes Dedupe. · H-3 trade_horizon: alle 11 Sender explizit (ORB "intraday", Rest "swing") — Intraday-Abonnenten bekommen jetzt Alerts, Swing-Abonnenten kein ORB mehr. · H-4 Turtle: TP1/TP2 (1.5R/2.5R) + natives trade_setup → Rows sind handelbar statt strukturell NO_TRADE. · H-5 Backtest "Momentum Breakout Long" live-synchron (Change ≥ 2 %, RVOL ≥ 1.5, ClosePos ≥ 0.5). · H-6 crypto_explosion/crypto_trade_signals in beide Guards, btc_divergenz in Health-Guard (watch-only, bewusst kein Plan-Guard) — keine widersprüchlichen JETZT_TRADEN/NO_TRADE-Rows mehr. · H-7 btc_divergenz konsolidiert: gemeinsame Status-Logik in scanner+bg (Paritätstest über ~5.600 Kombinationen), "JETZT SHORTEN" nur bei BTC-Schwäche, Schwellen einheitlich 65, kein actionable Signal ohne Stop (expliziter Hinweis), Alarmbox nur bei BTC-Schwäche, `btc_gate`-Feld. · H-8 Crypto.com-Candles: vv-Fallback v*close — NLS-Pfad wieder funktionsfähig. · H-9 Scheduler-Ownership: `BG_SCAN_SET`-ENV, Default bg = bi_long/bi_short/biotech/new_listing, api = crash/btc_div/bear/strategies/orb; Start-Logging. · H-10 `save_cache_file` atomar (tmp+os.replace). · H-11 Paywall: 6 Endpoints plan-gegated, Public-Allowlist statt Blanket-Ausnahme. · H-12 Login-Throttle (5 Fails/10 Min → 429+Retry-After) + /api/test-email admin-only. · H-13 Stubs beseitigt: detect_chart_patterns delegiert an patterns.py, close_position an indicators (Clamp + min_range_pct wirken; bg nutzt direkt indicators), estimate_crypto_atr einmal kanonisch (mit Pump-Kappung). · H-14 Partial-Cache: bg schreibt keine 429-Teilabrufe mehr als Voll-Cache; scanner-Konsument behandelt partial als stale. · H-15 NLS-Lifecycle: Signal-Coins bleiben überwacht → "invalidated" (Stop gerissen, sichtbar) / "expired" (24h); kein One-Shot mehr.

### MITTEL/NIEDRIG (Auswahl)
BTC-Div-Z-Score ÷ sqrt(5) (Hinweis: betroffener Block ist aktuell toter Legacy-Code — Fix als Schutz bei Reaktivierung) · Backtest-RVOL erste 20 Bars = None statt Rohvolumen · Cup&Handle: Floors 1.5 + kein CONFIRMED auf unfertiger Tageskerze (INTRADAY_UNCONFIRMED während Session) · Compression 1.5 · ORB respektiert Half-Days (13:00 ET) · NYSE-Kalender 2027 ergänzt (Quelle: offizielle NYSE/ICE-Mitteilung; einziger Early Close 2027: 26.11.) + `calendar_coverage_until` im commercial-readiness + Warnung bei unbekanntem Jahr · Bear/Crash-Mails: direkter Send-Rückgabewert + persistenter Tages-Dedupe · _EMAIL_COOLDOWN mit Lock · CORS aus ENV (Default unverändert) · CryptoExplosion: 180s-Trigger-TTL beim GET + robustere Bitget-Change-Lesart · Leveraged-Token-Filter (3L/3S/…, UP/DOWN, BULL/BEAR) zentral in api + scanner/bg-Spiegel · Stablecoin-Listen vervollständigt · Value Area per POC-Expansion (kontiguierlich) · Doji-Bars verlieren kein Volumen mehr · VWAP ohne 2-Dezimal-Rundung + volumengewichtete Bänder · _to_float "1,5"→1.5 · NLS-Pump-Detektion mit 500k-Liquiditätsfloor + Cross-Exchange-Dedupe · Symbol-Kollisions-Schutz (1000x-Mapping + Faktor-3-Plausi) · Exhaustion-Dimension-5-Cap 12 · OI-Komponente ehrlich gelabelt · Coinglass-Fehler werden geloggt · Root-new_listing_scanner.py als DEPRECATED markiert · scanner.py-Importcrash (scan_harmonic_batch fehlt in patterns.py) guarded.

### Deploy (LB-3)
`tradingbot-api.service` (uvicorn :8000) + `tradingbot-bg.service` aktualisiert (EnvironmentFile, Hardening, PrivateTmp=false wegen 9 geteilter /tmp-Caches — dokumentiert) · nginx: 80→301, TLS-Block (certbot-Pfade), HSTS/nosniff/DENY, limit_req auf Login, gzip; Streamlit als Legacy auskommentiert · Frontend statisch via nginx (kein eigener Service; safe_deploy-Liste konsistent) · DEPLOY_ANLEITUNG, COMMERCIAL_LAUNCH_CHECKLIST (alle neuen Pflicht-ENVs) und .env.production.example aktualisiert. Server-Migration: Units kopieren → alte Streamlit-Unit disable → daemon-reload → enable/start → certbot → nginx reload → /api/commercial-readiness prüfen.

## Offene Punkte (bewusst nicht geändert / für dich)
1. **git-Commit blockiert:** `.git\index.lock` (0 Bytes, von einem abgestürzten Prozess) ist über die Sandbox nicht löschbar. Bitte auf Windows löschen (Explorer oder PowerShell: `Remove-Item .git\index.lock`), dann committen — Commit-Message-Vorschlag liegt unten im Bericht-Ordnerverlauf bzw. einfach "Voll-Sanierung Audit 2026-06-10".
2. **Grade-Skalen** scanner-übergreifend (S ≥ 80 vs. S ≥ 88 bei crypto_explosion): Produktdesign-Entscheidung, Tests zementieren den Ist-Zustand — auf Wunsch vereinheitliche ich sie.
3. **Polygon-Budget prozessübergreifend** (api+bg teilen 200/min nicht): ENV-Kontrakt `BG_POLYGON_BUDGET_PER_MIN` ist dokumentiert, echtes Shared-Budget bräuchte einen kleinen File-Lock-Limiter in modules/data_fetchers.py — ca. 1h Aufwand, sag Bescheid.
4. **bi_long/bi_short/biotech/new_listing stehen auch im api-Scheduler:** bg ist jetzt Owner; falls api die vier zusätzlich scannt, empfehle ich api-seitig dieselbe Ownership-ENV — aktuell unkritisch (atomare Writes + gleiche Logik), aber doppelte API-Last.
5. **Toter z-Score-Block** in `_btc_divergenz_wrapper` (unreachable nach early return): gefixt, aber Aufräumen (löschen oder reaktivieren) steht aus.
6. **scan_harmonic_batch** existiert nicht in modules/patterns.py (Migrationsleiche der anderen KI) — Import ist jetzt guarded (No-Op + Warnung); Harmonic-Pattern-Scan im Streamlit-UI ist damit faktisch deaktiviert, bis die Funktion wiederhergestellt wird.

## Launch-Status
Alle 6 kritischen Befunde + alle 15 HOCH-Befunde + MITTEL-Block sind behoben und testgesichert (434/434). Aus Audit-Sicht ist die Basis jetzt launch-fähig, sobald: (a) Deployment mit den neuen Units + TLS + Pflicht-ENVs aufgesetzt ist (Checkliste), (b) `/api/commercial-readiness` auf dem Server grün meldet, (c) der git-Commit gemacht ist.
