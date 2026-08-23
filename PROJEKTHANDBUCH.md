# Alpha Station — Projekthandbuch

**Stand:** 11. August 2026 · **Arbeitsbranch:** `main` · **Tests:** 1503/1503 gruen
**Repository:** `C:\Projekt\TradingBot` → GitHub `xantharu123-png/AlphaStation`
**Produktion:** `root@178.104.69.209`, `/home/tradingbot/app`
**Letzter geprüfter Code-Commit:** `d11eb4c` lokal auf `main`; `origin/main` steht
weiter auf `9987c7f`.
**Produktionsnachweis:** Der Server ist per SSH erreichbar, aber auf dem neuen PC
fehlt noch ein autorisierter SSH-Schlüssel. Der exakte Server-Rollout wurde daher
**nicht verifiziert**. GitHub-Push und Server-Deployment bleiben getrennte Nachweise.

> Dieses Handbuch ist die chronologische Betriebs- und Statusdokumentation. Die
> verbindlichen Produkt-, Trading- und Sicherheitsregeln stehen in
> `PROJEKTBIBEL.md`; dieses Handbuch verweist auf die Einzel-Dokumente (Abschnitt 8)
> und trennt strikt zwischen
> **Git-Stand**, **gepush-tem Stand** und **auf dem Server ausgerolltem Stand** —
> diese drei sind niemals still gleichzusetzen.

---

## 1. Was Alpha Station ist

Webbasierte Trading-Intelligence-Plattform für Aktien und Kryptowährungen. Sie scannt
Märkte breit, destilliert wenige verständliche Signale, bewertet Setup-Qualität und
Ausführbarkeit **getrennt** und liefert über Web-App, E-Mail und optional Telegram.

**Kernprinzipien (verbindlich, mehrfach im Projekt festgelegt):**

- **Aktive Signale statt Watchlisten** — Wartesignale klar getrennt, nur opt-in.
- **Setup ≠ Timing** — Setup-Score beschreibt Qualität; Gates entscheiden, ob JETZT gehandelt werden darf.
- **Struktur statt Fantasie-R:R** — Entry/Stop/TP aus Invalidation, S/R, VRVP, ATR, Fibonacci, Measured Move.
- **Statistische Ehrlichkeit** — Trefferquoten nur mit Stichprobengröße, Konfidenzintervall und klarer R-Semantik.
- **Keine Anlageberatung** — keine Gewinnversprechen, keine künstlich schöngerechneten Ziele.

**Aktueller Übergabestatus (11.08.2026):**

| Ebene | Status |
|---|---|
| Verbindliche Fach-/Mathematikregeln | In `PROJEKTBIBEL.md` konsolidiert |
| Entwicklungszweig | `main`, Code-Baseline `1bd9a48` vor dem Dokumentencommit |
| Scanner/Mail/Tracker | Implementiert; Forward-Performance weiter empirisch zu belegen |
| Persönliche Positionen | Auswahl, genaue Setup-Zuordnung und empfängerbezogene Folge-/Stop-Mails implementiert |
| IBKR-AutoTrader | Geschützter Paper-V2 implementiert; echter DU-Soak offen, Live blockiert |
| Deployment | Revisionsgebundener Auto-Deploy implementiert; aktueller Serverstand separat zu prüfen |
| Kommerzielle Freigabe | Technische Bausteine vorhanden; Recht/Steuer/Datenlizenz/TLS/Live-Stripe offen zu bestätigen |

**Scanner-Landschaft:** Aktien-Strategie-Sweep (Momentum Breakout/Gap Momentum Long/Short),
BI Long/Short, Biotech, ORB (intraday-only), Bear, Crash-Monitor, Penny-Lifecycle,
Turtle, New Listing, Crypto Early Movers/Explosion/Strategie.

---

## 2. Architektur & Betrieb

| Komponente | Was | Wo |
|---|---|---|
| `api.py` | FastAPI-Backend: Scanner, Alerts, Auth, Versand, zentraler Scheduler und Health-Endpunkte | Service `tradingbot-api` |
| `bg_service.py` | Hintergrunddienst: BI/Biotech, Signal-Auswertung, Stop-Updates, Wochenreport und Mail-Outbox-Worker | Service `tradingbot-bg` |
| `frontend/index.html` | React-SPA (statisch ausgeliefert) | über nginx/api |
| `modules/` | auth, signal_tracker, mail_outbox, regime_filter, patterns, stock_execution, trade_levels, trade_health, email_dedupe u. a. | shared |
| `data_cache/` | Scan-Caches, `signal_tracker.sqlite`, Mail-Outbox und persistenter Regime-Zustand | Server |
| `scripts/` | Smoke-Tests, Report-Preview, Deploy-Helfer | Server |

**Scan-Ownership (Audit H-9):** api-owned: strategy_scan, crash_monitor, bear, orb,
new_listing, crypto. bg-owned: bi_long, bi_short, biotech (feste ET-Zeitfenster).
Override per `BG_SCAN_SET`.

**Deploy-Workflow (der einzige gültige Weg):**
1. Lokal entwickeln + volle Suite grün.
2. Auf `main` committen und `git push origin main`.
3. Der Server darf über `deploy/auto_update.sh` (Cron) aktualisieren oder manuell mit
   `cd /home/tradingbot/app && git pull --ff-only origin main` aktualisiert werden.
4. Deployment ausschließlich über `bash deploy/safe_deploy.sh`: Runtime erkennen,
   Abhängigkeiten/Compile/Tests prüfen, Services neu starten, Healthcheck abwarten;
   bei Fehlern nicht still weiterlaufen.
5. **Immer separat verifizieren:** `git log --oneline -1`,
   `systemctl status tradingbot-api tradingbot-bg nginx` sowie
   `/api/health`/`/api/system-health` und Journald-Logs. Ein erfolgreicher Push ist
   noch kein Beleg für einen erfolgreichen Produktions-Rollout.

Produktionsziel ist FastAPI plus Hintergrunddienst hinter nginx. Ein alter
`tradingbot-frontend`-Service ist nur noch Legacy-Kompatibilität und kein Zielbild
für den kommerziellen Betrieb.

**Lokale Tests (Windows):**
```bash
.venv/Scripts/python.exe -m pytest -q --tb=line -p no:cacheprovider --basetemp=tmp/pytest_audit
```

---

## 3. Chronik der Arbeitsphasen

### 3.1 Juni 2026 — Grundlagen-Audits und Signal-Tracking

- **10.06.:** Vollaudits aller Scanner (Aktien, BI, Biotech, Cup&Handle, Mail-Alerts) +
  Sanierungsbericht (`AUDIT_*_2026-06-10.md`, `FIX_REPORT_SANIERUNG_2026-06-10.md`).
- **11.06.:** KRITISCH: bg_service-Entrypoint wiederhergestellt — der Service lief nie (`c2571e9`).
  Klumpenrisiko-Warnung in Aktien-Mails (ADR-Cluster + Mehrfach-Mover, `ff5396c`).
- **12.06.:** Walk-Forward-Validierung Cup&Handle auf echten Charts (`e59ba9d`);
  automatische Wochenreport-Mail freitags nach US-Close (`3372b6a`);
  Exit-Update-Mails + Performance-Tab (`18e41ba`); Forward-Check der Signal-Mails (`21c5085`).
- **24.–29.06.:** Cup&Handle-Score-Kalibrierung; Swing-Mails aktiviert und verständlich
  gemacht; Momentum-Breakout-Mails verschärft; Stale-Momentum-Spike-Mails blockiert.
- **01.–04.07.:** Swing-Mail-Qualitätsgates; Trade-Plan-Qualität verschärft;
  New-Listing-Mail-Scheduler repariert.

### 3.2 10.–11. Juli — Execution-Härtung und Penny-Lifecycle

- Krypto-Execution-Signal-Integrität (`029f2ee`), Aktien-Breakout-Execution-Qualität (`71316c4`).
- Penny-Lifecycle-Scanner: Signale altern (`6b932fe`), nur aktive anzeigen (`0b689f5`),
  Execution-Härtung (`3efcfed`).

### 3.3 19.–22. Juli — Scanner-Vollaudit, Nachaudit, 4H-Gates

- **19.07.:** Scanner-Vollaudit (`AUDIT_SCANNER_VOLLAUDIT_2026-07-19.md`).
- **20.07.:** Fixstatus, Scheduler-Stalls + Health-Sichtbarkeit, Cache-Versionierung,
  Breakout-Qualitätskalibrierung, Frontend-API-Konnektivität (`8338f2c`, `8e28962` u. a.).
- **21.07.:** Nachaudit-Fixpaket + Codex-Abschlussprüfung (`b397f26`, `2247aff`),
  Handoff-Dokument (`CODEX_HANDOFF_NACHAUDIT_2026-07-21.md`). Stand danach: 985 Tests grün.
- **22.07.:** Penny-Audit (`AUDIT_PENNY_STOCK_SCANNER_2026-07-22.md`), Baseline-Liquiditäts-Gate
  (`99c644c`), **Stock-Swing-Mails nach 4H-Rejection blockiert** (`1ed1270`),
  gechasede Swing-Entries blockiert (`e209e8a`), Penny-Signal-Tracking (`6521520`).

### 3.4 24. Juli — Vollaudit-Tag (Mathematik, Logik, Wahrscheinlichkeit)

Anlass: Vollaudit der von Codex entwickelten App mit Umsetzung aller Befunde bis zur
Produktiv-Verifikation. 9 Commits, Suite 1071 → 1101. Detail-Dokumente:
`AUDIT_ABSCHLUSSBERICHT_2026-07-24.md`, `AUDIT_GESAMT_2026-07-24.md`,
`AUDIT_MATHEMATIK_2026-07-24.md`, `AUDIT_TIEFENAUDIT_TRADINGLOGIK_2026-07-24.md`.

- **Legacy-Bereinigung** (`65a3f5e`): Alt-Dateien nach `archive/`, Info-Leak im KI-Fallback geschlossen.
- **Rechenkern-Audit** (`89334ef`): `test_math_invariants.py` — 43 numerische Prüfungen gegen
  unabhängig nachgerechnete Lehrbuch-Referenzen (ATR/RSI nach Wilder, EMA, MACD, VWAP,
  Stochastik, R:R-Geometrie, Profit Factor, RVOL). **Ergebnis: Rechenkern lehrbuchkorrekt.**
- **Tiefenaudit Trading-Logik** (`e27a37a`): vollständige Lektüre aller Entscheidungspfade.
  Urteil: Kern sauber (18 harte Gates vor JETZT, Stop-first-Konvention, Gap-Fills, kein Look-Ahead).
- **T1 Managed-R** (`1e8b1da`): Tracker buchte TP2 mit vollem Geometrie-R, während die
  Kundenempfehlung 50/50-managt. Neu: `r_managed_50_50` je Signal + `avg_r_managed_50_50`
  je Bucket, retroaktiv ableitbar, beide Semantiken dokumentiert.
- **T2 Krypto-Grade-Rekalibrierung** (`1e8b1da`): Krypto-Schwellen waren strenger als Aktien
  (unbeabsichtigt); neu verankert mit den originalen V2.9-Kalibrier-Ratios.
- **Wilson-Kalibrier-Loop**: `decided_signals`, `win_rate_wilson_95` (Wilson-CI, z = 1,96),
  `sample_reliable` (ab n = 30) in Tracker, UI und Wochenreport.
- **Wochenreport-Veredelung** (`f2e1f13` → `d345ffe`): neue Felder in UI + Mail,
  Server-Smoke-Test, Preview-Renderer, Scanner-Verdikt-Tabelle (behalten/beobachten/abschalten),
  Verdikt-Alarm, Verdikt-Logik in `signal_tracker` zentralisiert.

**Stand nach dem Tag (Server-verifiziert):** 242 Signale, 211 entschieden, Hit-Rate 42,7 %,
Wilson-95 %-KI 36,2–49,4 %, Ø R 50/50 = +0,31, Σ R +66,4. Verdikt: stock_strategy behalten
(KI 36 % > Breakeven 33 %), crash/early_movers/trade_reminder beobachten (Stichprobe < 30).

### 3.5 28. Juli — ATR-Relative Chase-Gates und Mail-Kanal-Auswahl

Anlass: Zwei produktive Fehlverhalten, vom Betreiber gemeldet:

1. **„JETZT SWING"-Mail für RITM, obwohl der Move vorbörslich schon gelaufen war**
   (+6,2 % bei ~2,1 % ATR ≈ 2,9 ATR Tagesmove; TP1 nur noch 2,3 ATR entfernt).
2. **„Lachhafte" TP-Abstände** (HLN: TP1 +33 Cents auf $10,29).

**Diagnose (vollständig verifiziert, `cdeff7e`-Message):** Alle Chase-Schutzschwellen waren
in Prozent oder auf Mehrtages-Runs kalibriert (8 %/12 %, 4H-Runs ≥ 12 %, Breakout ≥ 2 Kerzen alt).
+6,2 % rutschte durch **alle sechs Schutzschichten**. Die Gap-Gates galten nur für den
Strategienamen „Gap Momentum Long" — RITM lief als „Momentum Breakout Long" daran vorbei.
Die eigentliche mathematische Sünde: Niemand verglich den **gelaufenen** Move (in ATR) mit dem
**noch erwartbaren** (Distanz zu TP1 in ATR). HLN wiederum hatte 1,7 % Tages-ATR — das
Vehikel trägt keinen Swing-Trade, die 33-Cents-TPs waren nur das Symptom.

**Umsetzung (Commit `cdeff7e`, Suite 1114):**

| Fix | Regel | Fall |
|---|---|---|
| A: Tagesmove-ATR-Gate | ≥ 2,5 ATR → WAIT_RETEST, ≥ 3,5 ATR → NO_TRADE; long **und** short | RITM 2,9 / GSK 3,0 / KO 3,5 blockiert |
| B: Pre-Market-Gap-Gate | Gap ≥ 3 % + Session ≤ +0,5 % → `swing_gap_done_premarket_wait_retest` | Strategienamen-Lücke geschlossen |
| D: Volatilitätsbudget | ATR < 2,0 %/Tag → `stock_swing_mail_blocked_low_volatility_budget` | HLN (1,7 %) fliegt raus |
| E: Stop-Rausch-Warnung | Stop < 0,9×ATR → Warnhinweis im Plan-HTML | BELFB (0,6×ATR) |

Gegenproben abgesichert: frischer Breakout (1,5 ATR) bleibt mailbar; BELFBs −7,7 %-Tag
(= 1,0 ATR bei 7,7 %-ATR) feuert **kein** Gate — das ATR-Maß trennt sauber zwischen
„extended" und „hohe Eigen-Volatilität". Rows ohne ATR-Metadaten: Bestandsverhalten.

**ATR-Annotationen (Commit `bd19d5e`):** Stop/TP1/TP2 tragen in den Mails ihre Distanz in
Prozent **und** ATR-Einheiten (`-2,4 % ≈ 1,0×ATR`) — Enge ohne Taschenrechner lesbar.

**Mail-Kanal-Auswahl (Commit `b757ade`, Suite 1120):** Sechs einzeln schaltbare Kanäle in
den Alert-Einstellungen (📬 Mail-Kanäle): Aktien Swing, Aktien Intraday, Crypto, Biotech,
Bear & Crash, New Listing. Default überall AN (kein Abonnent verliert ungefragt Mails).
Filter greift im Abonnenten-Routing **und** bei der Betreiber-Mailbox (sofern die Adresse
ein User-Konto ist). Watch-Mails bleiben separat: Opt-in, Default AUS (AUDIT H-3).
Info-Mails (Passwort, Test, Wochenreport, Markt-Puls) bewusst ohne Kanal-Filter.

### 3.6 29. Juli — Orts-Gate gegen Top-Käufe, Breakdown-Werkzeug, UI-Konsistenzpaket

Anlass drei produktive Fehlverhalten plus vier UI-Meldungen des Betreibers:

1. **PBT-Fall:** Mail 29.07. 14:13 UTC — PBT „Gap Momentum Long", Entry $30,69 = **0,36 %
   unterm Tageshoch**, Tagesmove +9,6 % bei 4,0 % ATR = **2,4 ATR** — rutschte um 0,1 ATR
   **unter dem gestrigen 2,5-ATR-Gate durch**. Kurs fiel danach Richtung Stop.
   Lehre: Reine Größen-Schwellen erzeugen immer Grenzfälle; das Muster ist ein
   **Orts-Problem** (Kauf am Tages-Extrem nach ≥ 2 ATR gelaufenem Move).

2. **NVST-Fall (gleiches Muster):** Mail 14:47 UTC — NVST +6,3 % bei 2,67 % ATR = 2,36 ATR,
   Preis 0,45 % unterm Hoch. Ging raus, **bevor** das Orts-Gate deployed war (Push ~17:05 MESZ,
   Mail 16:47 MESZ). Das vom Betreiber vermisste „Gap" war real (Filter verlangt ≥ 3 %,
   Open vs. Vortagesschluss), aber im 4h-Chart unspektakulär — die Mail zeigte es nicht.

**Umsetzung A — Top-Entry-Orts-Gate (Commit `da6c4be`, Suite 1126):**
**Tag ≥ 2,0 ATR gelaufen UND Preis ≤ 1 % am Tages-Extrem → WAIT_RETEST** (kein JETZT).
Identische Logik in allen vier Einstiegspfaden: Aktien long + short
(`_stock_swing_rule_reasons` / `_stock_swing_short_rule_reasons`), Crypto/Intraday long
(`_long_entry_rule_reasons`), Bear-Short (`_bear_short_rule_reasons`).
**Crash-Meldungen bewusst ausgenommen** (ein aktiver Flush liegt per Definition am
Tagestief — Lage-Meldung, kein Short-Einstieg). Gegenproben abgesichert: frischer
Breakout < 2 ATR bleibt mailbar (auch am Hoch); ≥ 1 % Rücksetzer vom Extrem = Retest
bleibt frei; Kurs über/unter dem hinterlegten Extrem (veraltete Tagesdaten) zählt als
„am Extrem"; Rows ohne ATR-/Tages-Metadaten: Bestandsverhalten. Verifiziert:
Produktions-Strategy-Rows tragen `Day_High`/`Day_Low`/`ATR14` (api.py ~12993) — das Gate
ist live wirksam. PBT wäre blockiert worden, RITM auch, NVST auch.

**Umsetzung B — Hit-Rate-Einordnung + Breakdown-Werkzeug (Commit `da6c4be`):**
Betreiber-Frage „43 % ist schlecht, früher 70 %". Einordnung: Trefferquote ohne R:R ist
bedeutungslos — Breakeven beim 50/50-Management ≈ 33 %, Wilson-KI 36–49 %, +0,31 R/Trade,
Σ +66,4 R. Die „70 %" sind lokal nicht verifizierbar (DB auf dem Server); wahrscheinlichste
Erklärungen: andere Metrik (TP1-Berührung ≠ entschiedener Trade), kleinere Stichprobe,
anderes Regime, Semantik vor dem T1-Fix. Neues Werkzeug
`scripts/signal_performance_breakdown.py` (Server): Win-Rate/Ø R/Σ R **pro Scanner ×
Kalendermonat** aus `signal_tracker.sqlite`, Zellen mit n < 30 markiert — zeigt, ob und
wann die Quote kippte.

**Umsetzung C — UI/UX-Konsistenzpaket (Commit `2c66d4f`, Suite 1130):**

| # | Befund | Fix |
|---|---|---|
| 1 | „No Trade" vs. „Nicht traden" gemischt | Es gibt **keinen EN/DE-Sprach-Toggle** — die UI ist fest deutsch mit hartcodierten Englisch-Resten. Vereinheitlicht auf Deutsch (trade_health, api, ORB-Frontend; Bundle neu gebaut + verifiziert). Ein echter EN/DE-Toggle wäre ein eigenes Feature |
| 2 | Ganze ORB-Liste „Blockiert" — was heißt das, was nützt sie | Badge 1 zeigt jetzt den **konkreten Sperr-Grund** aus Trade-Health („Live R:R zu schwach", „TP1 schon gelaufen", „Stop im Tagesrauschen", „Geometrie ungültig" …), Badge 2 = Entscheidung „Nicht traden"; Detailzeile nennt die Gründe; Erklärtext über der Liste. Nutzen der Liste: Transparenz — Top-Kandidaten **mit** Qualitätsurteil statt leerer Liste; Sortierung bleibt: handelbar zuerst, Nicht-traden zuletzt |
| 3 | „Letzter Scan: vor 18h" trotz Server | Anzeige-Problem: bg-owned Scanner (bi_long/bi_short/biotech, H-9) laufen **außerhalb** der api — deren In-Memory-`last_run` veraltet. Neu `_effective_scan_timing`: last_run/next_run = max(In-Memory, **Cache-Datei-mtime**). Falls der Cache wirklich 18 h alt ist (echter Stillstand): `systemctl status tradingbot-bg` + `journalctl -u tradingbot-bg -n 60` |
| 4 | NVST: Mail nach gelaufenem Move, „wo ist das Gap" | Orts-Gate (A) blockiert den Fall nach Deploy; Mail zeigt das gemessene Gap jetzt explizit: „Gap +3,2 % (Open vs. Vortagesschluss)" unter der Tagesänderung |

**Kalenderfeste Tests (in `2c66d4f`):** Vier Tests hingen am **echten Wirtschaftskalender**
(FOMC-Tag 29.07.2026 → Event-Penalty in trade_health → chase_risk HIGH → NICHT_TRADEN
statt WARTEN; am 28.07. noch grün). Market-Context in diesen Tests neutral gemockt —
das Produktivverhalten (defensivere Bewertung an Event-Tagen) ist gewollt und bleibt.

### 3.7 29. Juli (abends) — Pre-Market-Radar und Opening-Takt (Punkt C, RITM/NVST)

**Anlass:** RITM (28.07.) und NVST (29.07.) explodierten vorbörslich bzw. in der ersten
Handelsstunde; die Swing-Mails kamen erst 10:12 bzw. 10:47 ET — nach dem Move.

**Root-Cause-Analyse (drei Faktoren, gemeinsam das Blindfenster):**

1. **Session-Gate:** `_stock_trade_email_status` ließ Aktien-Mails nur 9:30–16:00 ET zu;
   vorbörsliche Kandidaten wurden strukturell geskippt.
2. **RVOL ist vorbörslich kein sinnvoller Maßstab (die eigentliche Root Cause):** Der
   Scan las PM-Kurse zwar korrekt ein (`lastTrade`, Extended-Price-Pfad), aber
   `rvol20` = winziges PM-Volumen vs. 20-Tage-**Ganztages**-Schnitt (~0,1–0,3) →
   RVOL-Filter (≥ 1,5) rejected praktisch **jeden** PM-Mover; zusätzlich killte das
   Dollar-Vol-Gate ($200k, ohne Projektion vorbörslich) den Rest. In den ersten 15
   Regular-Minuten drückte der Projektions-Guard den Score unter 80.
3. **Takt:** 30-Minuten-Sweep + mehrminütige Laufzeit → nach Open weitere ~35 Minuten.

**Umsetzung (Bausteine B1–B3, Suite 1147):**

| B | Inhalt | Details |
|---|---|---|
| **B1** | **PM-Modus im Strategy-Scan** (`session_name == "Pre-Market"`) | Ersetzt RVOL-/Dollar-Vol-Filter durch: **absolute PM-Liquidität ≥ $500k** (strategie-übersteuerbar via `premarket_min_dollar_volume`), **Spread-Guard > 7 %** Bid/Ask → Reject, fehlende Quote → Reject. `_premarket_rvol_proxy` ($500k→1,5 … $5M→3,0) speist als `rvol_effective` Momentum-Gate + Scoring (sonst kein Score ≥ 80 möglich). **PM-Extensions-Decke > 3,0 ATR** → Reject (PM-Äquivalent zum Orts-Gate: gelaufener Move = keine Frühwarnung). Fallback-Cap 76 greift nicht für die designed PM-Quelle. Row-Flags: `Premarket`, `PM_DollarVol`, `RVOL_PM_Raw` (Rohwert bleibt transparent), `RVOL` = effektiver Wert |
| **B2** | **PM-Mail als eigener Kanal** `stocks_premarket` („Aktien Pre-Market") | Fenster **7:00–9:25 ET** (Mo–Fr). Dedizierter Klassifizierer `_classify_premarket_candidate` (statt Regular-Maschinerie, die PM-Rows systematisch verwerfen würde): Score ≥ **85** (strenger als 80), Top-Grade, PM-Liquidität, Extensions-Decke, valide Level, Common-Stock-Guard. Eigener Cooldown-Namespace `..._pm` → **Regular-Mail nach Open bleibt möglich**. Betreff „Aktien Pre-Market Radar", Pflicht-Warnhinweis (dünne Liquidität, nur Limit, kleine Größe, Opening-Range abwarten), RVOL-Spalte zeigt „PM $2,0M". Default **an**, in den Alert-Einstellungen abschaltbar (Frontend rendert die Optionen dynamisch — kein Frontend-Eingriff nötig) |
| **B3** | **Dynamischer Scan-Takt** | `_effective_scan_interval_min`: `strategy_scan` im Fenster **9:25–11:30 ET** alle **10 statt 60 Minuten** (Basis-Takt seit 31.07. auf 60 Min — Swing-Horizont braucht keinen 30-Min-Takt; Opening-Takt bleibt für frische Ausbrüche). Alle anderen Scanner unverändert |

**Bewusst nicht gebaut (Phase 2):** WebSocket-Trigger (Polygon-Streams). Begründung:
Engpass war die Gate-Logik, nicht der Transport; Stream = anderes Paradigma (Event-Router,
Reconnects, Gaps, Tick-Filter gegen PM-Einzelprint-Rauschen) auf einem produktiven
Einzel-VPS; Echtzeit-Tier beim Provider ungeprüft. B1–B3 deckt ~90 % des Nutzens ab.
Legitime Phase-2-Ziele: Level-Cross-Trigger, Sekunden-statt-Minuten-Detection nach Open,
Tick-nahes Stop/TP-Monitoring — als eigener Ingest-Service mit Designdokument.

**Tests:** `test_premarket_radar.py` (17): Fenster-Grenzen (kalenderfest, fester
Wochentag), Proxy-Schwellen, PM-Gate-Gründe, dynamischer Takt, Klassifizierer
(alertable/Score/Liquidität/Extension/Level/Cooldown-Namespace), Mail E2E
(Kanal, Warnhinweis, PM-$-Zelle, Non-PM-Rows ausgeschlossen, Regular-Pfad bei
offenem Markt im Fenster unberührt), Kanal-Registrierung.

**WebSocket-Entscheid — gemessen statt Bauchgefühl (29.07., `scripts/websocket_benefit_analysis.py`
auf dem Server, 237 Tracker-Signale + n=80 Polygon-Stichprobe, 90 Tage):**

| Entscheidungsregel | Schwelle | Gemessen | Urteil |
|---|---|---|---|
| TP1 < 30 min nach Mail („Minuten sind Geld") | > 40 % | **0 % (0/64)** — Median TP1-Zeit 2,3 Tage, Stop 1,3 Tage | verfehlt |
| Extension ≥ 2 ATR zum Mail-Zeitpunkt („Alert zu spät") | > 25 % | **16 %** (Median 1,4 ATR; ≥ 3 ATR: 0 %) | verfehlt |
| Median-Preisvorteil Entry T-10 | > 1,5 % | **+0,1 %** (p75 +0,3 %) | um Faktor 10 verfehlt |
| Stop-Gegencheck (Früh-Entry wäre gestoppt worden) | < 15 % | 0/80 | unschädlich, aber wertlos |

**Fazit: Phase 2 (WebSocket/Level-Trigger) wird NICHT gebaut.** Das System ist ein
Swing-System — Auflösung in Tagen, nicht Minuten; 10 Minuten früher ändern den Entry
im Median um 0,1 %. Die 16-%-Restfälle (Muster RITM/NVST) sind seit heute durch
Orts-Gate (blockt „JETZT" nach gelaufenem Move) + PM-Radar (fängt frische PM-Moves)
strukturell abgedeckt. Ehrliches Caveat: Die Stichprobe misst nur **gemailte**
Signale (Survivorship) — Moves, die nie ein Gate passierten, bleiben unsichtbar;
dagegen wirken B1 (PM-Fenster) und B3 (10-Min-Opening-Takt).
**Nebenbefund mit echtem Hebel:** MFE-Nutzung stock_strategy **−22 %** (Median des
realisierten R relativ zum Maximalgewinn) — die Wertvernichtung sitzt auf der
**Exit-Seite** (offene Gewinne werden verschenkt), nicht im Transport.

---

### 3.8 30. Juli — Exit-Effizienz gemessen, Management-Regel abgeleitet

**Messung** (`scripts/exit_efficiency_analysis.py` auf dem Server, 237 entschiedene
Signale, 90 Tage; Simulationsfunktionen in `modules/signal_tracker.py`, 13 Tests,
Suite 1160):

| Befund | Zahl |
|---|---|
| Signale mit MFE ≥ +1 R | 126/237 (53 %) |
| davon ≤ 0 geendet (**Total-Giveback**) | **39 = 31 %** |
| Ø verschenkt (MFE ≥ 0,5 R) | **+1,64 R pro Signal** |
| ØR ist → Regel A (BE nach +1 R) | +0,18 → **+0,34** (+0,157 R/Signal) |
| ØR ist → Regel B (50/50 + BE-Rest) | +0,18 → **+0,33** (+0,143 R; vs. Ist-50/50 +0,147 R) |

**Scanner-Differenzierung (wichtig):** stock_strategy (n=208): +0,20 → +0,33 —
Regel lohnt. early_movers (n=13, Crypto): −0,32 → +0,26 — Regel dreht das Vorzeichen
(Giveback 5/7, Ø 3,08 R; Crypto-MFE ist Spot-Stichprobe → Richtung belastbar,
Größe unterschätzt). crash (n=16): realized +0,40 > 50/50-managed +0,27 — **hier
schadet Teilverkauf**; nur BE-Schutz (+0,46), kein TP1-Zwang. trade_reminder: n=0.
Haltezeit: Giveback-Rate <24h 50 %, 1–3d 54 %, >3d 25 % (absolut 24/39 in >3d —
lange Läufer, die verblassen; die >3d-Trades tragen ØR +0,56, die kurzen negativ).

**Abgeleitete Regel (Implementierung in 3.9, gleicher Tag):** **Breakeven-Nachzug nach +1 R** für
alle Aktien- + Crypto-Swing-Signale; bei stock_strategy/early_movers zusätzlich
50/50 an TP1 (Regel B), bei crash **ohne** TP1-Zwang (Regel A pur). Umsetzung als
Stop-Update-Alert aus der Positions-/Signal-Überwachung (kein Broker-Zugriff —
der Betreiber zieht den Stop selbst nach), Tracker misst die Regel live weiter
(BE-adjustiertes R als eigenes Feld). Annahme-Restrisiko: BE-Stop = exakter
Einstand (Slippage −0,05…−0,1 R einkalkuliert — Delta bleibt > +0,10 R); Simulation
konservativ (ambiguous_same_day unangetastet).

---

### 3.9 30. Juli — BE-Trigger implementiert: Stop-Update-Mail bei +1 R

**Umsetzung der Regel aus 3.8** (Suite 1171, alle grün):

- **Tracker (`modules/signal_tracker.py`):** zwei neue Spalten per idempotenter
  Migration — `be_activated_at` (ISO-Zeitpunkt, einmalig) und `r_realized_be`
  (BE-adjustiertes R). `evaluate_open_signals` markiert jedes Signal, das erstmals
  **MFE ≥ +1 R** erreicht, und meldet es in `result["be_activations"]`. Bei
  terminalem Exit wird `r_realized_be` nach `breakeven_adjusted_r()` geschrieben —
  die A/B-Messgröße Ist vs. BE-Regel.
- **Konservativ-Regeln:** Aktivierung UND Verlust-Exit im selben Eval-Lauf ⇒
  Intraday-Reihenfolge unbewiesen ⇒ **keine** Aktivierung, kein BE-Kredit
  (`r_realized_be = r_realized`). `ambiguous_same_day` bleibt generell unangetastet.
  Abwärtskompatibel: `be_activations` wird im Gleichheitsvergleich des
  Ergebnis-Dicts ignoriert (wie `transitions`).
- **Mail (`bg_service._send_be_alert_mail`):** läuft im stündlichen Eval-Job ⇒
  Latenz ≤ 1 h nach dem MFE-Cross. EINE Sammelmail, `mail_class="info"`, Betreff
  „Stop-Update: n Trade(s) auf Einstand sichern (+1R gelaufen)".
  Scanner-differenziert: **crash\*** → „Stop auf Einstand — KEIN Teilverkauf"
  (Daten: Halten +0,40 R schlug 50/50 +0,27 R); alle anderen → „Stop auf Einstand;
  an TP1 50 % verkaufen; Restposition mit Stop auf Einstand — Gap-, Slippage-
  und Ausführungsrisiko bleibt" (Regel B). Gleiche Sicherungen wie bei
  Exit-Update-Mails: Zweitsicherung `_signal_origin_was_mailed`, persistentes
  Dedupe `signal_be_{id}` (7 d), Mark erst nach erfolgreichem Versand, Fehler im
  Mail-Bau beschädigen den Eval-Job nie.
- **Erwarteter Effekt (Messbasis 3.8):** +0,14…+0,16 R/Signal; Live-Nachweis über
  `r_realized_be` vs. `r_realized` (nächster Schritt: Aufnahme in Wochenreport).
- **Tests:** `test_be_activation.py` (11): einmalige Markierung, Same-Run-
  Konservativfall, BE→Stop = 0 R, Gewinner unverändert, Pure-Funktion,
  Abwärtskompatibilität, Mail-Texte scanner-differenziert, Dedupe,
  Origin-Zweitsicherung, Mail-Crash-Toleranz.
- **Wochenreport-Integration (gleicher Tag, nachgeliefert):**
  `load_performance_summary` liefert je Bucket `avg_r_be` (live gemessenes R
  unter der Einstand-Regel — kein Backtest), `be_activations` und `be_saved`
  (Verlierer, die die Regel auf ≥ 0 R gedreht hätte). Der Freitags-Report
  zeigt eine „Ø R BE"-Spalte in Kopf- UND Scanner-Tabelle plus eine grüne
  Ergebnis-Box (Aktivierungen, bewahrte Verlierer, Ø R Ist vs. Ø R BE);
  Alt-Summaries ohne BE-Felder rendern „–" und keine Box. Das Preview-Skript
  prüft die neuen Elemente als Smoke-Checks mit. Suite 1187.
- **Dashboard-Integration (gleicher Tag):** der Performance-Tab in der App
  zeigt dieselben BE-Daten — Kopf-Karte „Ø R BE" (Hint: Einstand-Regel ab
  +1R, live), grünes Ergebnis-Banner (Aktivierungen, bewahrte Verlierer,
  Ø R Ist vs. BE), „Ø R BE"-Spalte in der Scanner-Tabelle und eine BE-Zeile
  in „Letzte Signale", sobald BE-R vom Ist-R abweicht. Der API-Endpoint
  `/api/signal-performance` reicht die Summary unveraendert durch — neue
  Felder flossen ohne Code-Aenderung; recent fuehrt jetzt auch
  `r_realized_be`. Suite 1189.

---

### 3.10 30. Juli — Scan-Wächter: Hänge-Alarm + Selbstheilung

**Anlass Betreiber-Frage „der Server scannt nicht":** Server-Diagnose (journalctl
beider Dienste) zeigte das Gegenteil — alle Scanner-Caches waren Sekunden bis
wenige Minuten frisch (btc_divergenz 31 s, strategy_scan 354 s, …). Das
18-Stunden-„Loch" der Aktien-Scanner ist **Design** (feste ET-Fenster
10:00–16:05 ET = 16:00–22:05 MESZ, Daily-Bars ändern sich kaum untertägig);
die irreführende „vor 18h"-Anzeige war bereits am 29.07. gefixt worden
(Cache-mtime-Merge in den Status-Zeiten).

**Gebaut wurde trotzdem die echte Lücke — Sichtbarkeit bei echten Hängern:**

- **api.py `_scan_watchdog_check`:** ersetzt den stillen Inline-Watchdog.
  Reißt ein Scan sein Zeitbudget (`_SCAN_TIMEOUTS`, Default 10 Min), geht
  **einmal je Episode eine Warn-Mail** an den Betreiber (Scanner, Dauer,
  Budget, Restart-Befehl; persistentes Dedupe `stuck_scan_{name}_{start}`,
  7 d, Mark erst nach Versand). Am **Hartdeckel (3× Budget, min. Budget + 15
  Min)** Selbstheilung: Zustand wird freigegeben (`running=False`,
  Thread-Register bereinigt), der nächste Intervall-Takt startet frisch —
  der isolierte Alt-Thread erkennt an der `_run_id`, dass er veraltet ist,
  und rührt den neuen Zustand nicht an (kein Thread-Kill, kein Parallelstart).
- **bg_service Herzschlag-Wächter:** die bg-Hauptschleife arbeitet
  sequenziell — ein Hänger dort stoppt ALLES (BI-Scanner, Signal-Eval,
  Wochenreport). Neuer Daemon-Thread `_bg_stuck_monitor_loop` beobachtet das
  Herzschlag-Zeitstempel (`_bg_heartbeat_touch` an Schleifenanfang + vor
  jedem Scan, inkl. Init-Phase); > 90 Min (`BG_STUCK_THRESHOLD_SEC`, ENV) ohne
  Lebenszeichen ⇒ Alarm-Mail mit hängendem Scan-Namen und
  `systemctl restart tradingbot-bg`. Einmal je Episode, Re-Arming nach
  Erholung. Thread kann nicht getötet werden — die Mail ist die Abhilfe.
- **Tests:** `test_stuck_scan_watchdog.py` (11): Mail einmalig/Episoden-Dedupe
  über Restart hinweg, Hartdeckel-Reset + Freigabe, Deckel-Faustregel,
  im-Budget/idle/kaputter Status, bg-Entscheidungslogik, Mail-Inhalte,
  Re-Arming. Suite **1182**.

### 3.11 30. Juli (vormittags) — Betreiber-Entlastung: Auto-Update, JWT, Runbook, Gesundheitscheck

**Anlass:** repetitives Pull/Restart-Ritual nach jedem Push + JWT-Warnung im Boot-Log
(„ephemerer Zufalls-Secret" — alle Sessions starben bei jedem Neustart).

- **JWT_SECRET auf dem Server fixiert** (generiert via `openssl rand -hex 32`,
  in `/home/tradingbot/app/.env`, chmod 600; api.service liest EnvironmentFile).
  Boot-Log danach warnfrei. Reines Server-Config, kein Code.
- **`deploy/auto_update.sh` + Root-Cron `*/10`:** Server holt sich Updates selbst —
  `git fetch`, bei neuem origin/main: Fast-Forward-Pull, `pip install` nur wenn
  requirements.txt sich änderte, Restart beider Dienste, Verifikation
  (`systemctl is-active` + Git-Hash), Log nach `/var/log/alpha_autoupdate.log`.
  Schmutziger Worktree ⇒ kein Pull (Schutz). Betreiber muss nichts mehr tippen;
  Verifikation auf dem Server: Eintrag + Skript executable, erster Lauf bestätigt.
- **`deploy/SERVER_WARTUNG.md`:** Runbook für Ubuntu-Updates/Reboot ohne Schaden —
  Wartungsfenster (nie Fr 22:00–Sa 06:00 wegen Wochenreport), Upgrade-Block,
  60-Sekunden-Verifikation, enable-Fallback, TL;DR.
- **`deploy/health_check.sh`:** Ein-Befehl-Ampel (`bash deploy/health_check.sh`):
  Dienste active+enabled, API-Antwort, Tracebacks (2h), Scheduler-Takt,
  Cache-Frische aller 8 Caches mit Fenster-/Wochenend-Logik (Premarket darf
  nachts alt sein, Crypto nie), JWT, Cron + Git-Stand (HEAD vs. origin/main),
  Disk/RAM. Pro roter Zeile steht der Fix-Befehl dabei; Exit 1 bei Fehlern
  (monitoring-tauglich). Cache-Pfade/-Intervalle gegen api.py verifiziert.
- **Tests:** Shell/Docs only — `bash -n` Syntax-Check; Suite-Stand unverändert **1189**.

**Nachbesserung am selben Tag (erster Live-Lauf):** Der Gesundheitscheck meldete
zwei Fehlalarm-Typen — Scan-Fenster-Logik fehlte (Stillstand vor 10:00 ET ist
Design, 3.10) und die Caches liegen in systemd-`PrivateTmp`-Namespaces, die root
direkt nicht sieht (die sichtbare 9,7 Tage alte Crypto-Datei ist ein Namespace-Überrest).
Fix: fenster-/wochentagsbewusster Takt-Check + Namespace-auflösender Cache-Finder
(jüngste Sicht aus /tmp, api- und bg-Namespace gewinnt). Echter Restbefund:
`tradingbot-bg` war nicht `enabled` → per `systemctl enable` fixiert.

### 3.12 30. Juli (nachmittags) — Zins-Block (FRED) als Mess-Annotation, kein Gate

**Anlass Betreiber-Frage:** „30Y bei 5,21 %, höchst seit vor 2008 — soll das System
sowas berücksichtigen?" Verifikation: 30Y 5,16–5,17 % (23./24.07.), 52W-Hoch 5,18 %
(19.05.), **+30 bp in einem Monat** — Level plausibel, die *Geschwindigkeit* ist der
eigentliche Befund. Analyse des Bestands: Market-Context hatte VIX, Breadth,
Index-Momentum, Kalender-Events, Headlines — aber **kein Zins-Regime**, obwohl die
Scanner genau das zinssensitivste Segment (Biotech/Growth-Momentum) bevorzugen.

**Entscheid (Mess-First, wie 3.8→3.9):** Erst annotieren, dann auswerten, dann
erst ggf. gaten. Statistik-Begründung: ~240 entschiedene Signale/90 d auf
Regime-Zellen aufgeteilt ergeben zu breite Wilson-Intervalle — ein hartes Gate
nach Zeitungsmeldung wäre Overfitting an Headlines.

**Gebaut:**
- **`modules/treasury_rates.py` (neu):** FRED-Abruf ohne API-Key
  (`fredgraph.csv?id=DGS2,DGS10,DGS30`, '.'-Feiertage-tolerant), bp-Änderungen
  5d/20d der DGS10, DGS30-20d, Kurven-Spreads 10s2s/30s10s, Stale-Flag (>4 Tage).
  Regime-Label **Erstkalibrierung**: ±10 bp/20d = rising/falling, ±25 bp/20d =
  rising_fast/falling_fast — ausdrücklich Label, kein Gate.
- **Market-Context:** `build_market_context(..., rates_data=)` → `context["rates"]`;
  **Invariante per Test bewiesen:** overall_risk_score/regime/trade_mode/warnings
  bleiben identisch. `_market_context_wrapper` holt den Block mit 6h-Datei-Cache
  (`/tmp/treasury_rates_cache.json`; Live → Cache → Stale-Cache → ehrlicher
  Missing-Block mit Grund).
- **Signal-Tracker:** neue Spalte `rates_json` (additive Migration);
  `_safe_record_alert_signals` annotiert jedes neue Signal mit dem aktuellen
  Zins-Block (kompaktes JSON: Level, Änderungen, Spreads, Regime; Missing → NULL,
  kein erfundener Kontext; TypeError-Fallback für gemischte Code-Stände).
- **Phase 2 (offen):** Regime-Split-Auswertung (Hit-Rate/ØR nach Zins-Regime)
  als Skript wie `exit_efficiency_analysis.py`, sobald ≥ ~100 entschiedene
  Signale pro Zelle; erst bei signifikantem Delta weiches Gate für
  zinssensible Longs.
- **Tests:** `test_rates_block.py` (24): Parsing/`.`/Trim, bp-Mathematik,
  Regime-Grenzen inkl. Grenzfall-Semantik, Kurven, Stale, Missing-Ehrlichkeit,
  Scoring-Invariante, Tracker-Annotation/NULL-Fälle. Suite **1213**
  (1211 grün; 2 altbestehende Tests sind ET-Session-abhängig und nur im
  Pre-Market-Fenster 08:00–09:25 ET rot — Stash-Gegenprobe ohne diese Änderung
  zeigt identische Failures; Fix als eigener Punkt offen).

### 3.13 30. Juli (abends) — Erster Live-Alarm → Entwarnungs-Mails + Ein-Befehl-OS-Wartung

**Anlass 1:** Um 16:17 MESZ feuerte der Scan-Wächter seinen **ersten echten
Alarm** (`strategy_scan` > 10 Min — der erste Strategie-Scan nach US-Open,
klassischer Netz-Hänger). Der Ablauf bewährte sich (Warn-Mail einmalig,
Hartdeckel-Selbstheilung). Sichtbare UX-Lücke: Der Betreiber bekäme Warnung
und ggf. Reset-Meldung, aber **nie die Entwarnung**, dass wieder alles läuft.

**Gebaut — Entwarnungs-Mails (grünes Pendant zur Warnung):**
- **api.py:** `_send_stuck_recovery_mail` („Scan-Waechter: {name} laeuft
  wieder — Episode beendet nach ca. X Min"). Auslöser ist der **erste echte
  Erfolg nach der Episode** im Worker-Finally — nicht der Reset selbst (der
  sagt nur „versucht es erneut"). Episode-Marker `_episode_started_at`
  ueberlebt Hartdeckel-Reset und Folge-Runs im Scan-Status und wird erst nach
  erfolgreichem Lauf gelöst; Versand-Dedupe persistent am Episode-Start
  (`stuck_recovery_{name}_{start}`, Mark erst nach Versand, Retry beim
  nächsten Erfolg).
- **bg_service.py:** `_bg_recovery_decision` (pure) + `_send_bg_recovery_mail`
  („Hintergrund-Dienst laeuft wieder"); der Monitor speichert beim Alarm
  `since` (letzter guter Herzschlag = Episode-Beginn) und mailt die Entwarnung
  beim ersten frischen Herzschlag danach, inkl. Episoden-Dauer.

**Anlass 2:** Betreiber-Frage „was muss ich noch selbst machen?" —
**`deploy/os_maintenance.sh`**: Ubuntu-Pflege in einem Befehl. Update → bei
Reboot-Bedarf @reboot-Einmal-Cron (selbstreinigend) → Post-Reboot-Verifikation
(Dienste active/enabled + health_check) → alles in
`/var/log/alpha_os_maintenance.log`. Ohne Reboot-Bedarf: Dienste-Restart +
Health-Check direkt. Abbruch VOR Reboot bei Upgrade-Fehler.

- **Tests:** +5 in `test_stuck_scan_watchdog.py` (16 gesamt): Entwarnung nach
  gewarnter Episode (Worker E2E via Thread-Join), keine Entwarnung ohne
  Episode, Dedupe je Episode, bg-Decision pure, bg-Mail-Inhalt. Suite **1218,
  alle grün** — inkl. der 2 ET-Session-Tests (Lauf um 10:45 ET = Markt offen;
  bestätigt die PM-Fenster-Diagnose aus 3.12).

### 3.14 30. Juli (spät) — Wächter-Mailflut: Fehlalarm-Budget + Anti-Spam-Throttle

**Anlass:** Betreiber bekam „gefuelt 5 Wächter-Mails pro Stunde"
(`strategy_scan haengt`). Diagnose: `strategy_scan` läuft im **30-Min-Takt**,
stand aber NICHT in `_SCAN_TIMEOUTS` → 10-Min-Default-Budget. Der Sweep über
60+ Kandidaten braucht regelmäßig >10 Min → jeder langsame Lauf wurde als
„Haenge-Episode" gemeldet (Fehlalarm, kein echter Hänger), plus Reset-Mails.

**Fixes (drei Ebenen):**
- **Budget-Kalibrierung:** `strategy_scan: 25` Min (Erstkalibrierung;
  Nachkalibrierung an `[Scheduler] strategy_scan DONE in Xs` offen).
- **Anti-Spam-Throttle:** max. **eine Warn-Mail je Scanner pro 6h**
  (`stuck_throttle_{name}`, unabhängig vom Episoden-Dedupe). Ein chronisch
  langsamer Scan kann den Betreiber nicht mehr zuspamen; Selbstheilung läuft
  trotzdem (Throttle deckelt nur Mails, nie die Logik).
- **Kontext-Kohärenz:** Reset-/Entwarnungs-Mails nur, wenn die Warnung der
  Episode nicht gedrosselt war (sonst kaemen kontext-lose Folge-Mails).
  Praezisierung: bei **gescheiterter** Warnung (kein Throttle-Mark) gehen
  Reset/Entwarnung trotzdem raus — der Betreiber bliebe sonst blind.
- **Zeitrobuste Tests (Kap.-7-Punkt 10 ERLEDIGT):** beide ET-Session-Flakes
  hingen an `_premarket_window_active` (Wall-Clock 07:00–09:25 ET wechselte
  den Mail-Pfad in den PM-Modus); jetzt zentral gemockt
  (`_mock_mail_env` + Einzeltest).
- **Tests:** +5 (Teil 4): Budget/Hartdeckel, Throttle 1-Mail/6h, Re-Arm nach
  6h, Reset-Unterdrückung bei gedrosselter Episode, Entwarnungs-Logik
  (gedrosselt/gescheitert/versandt). Suite **1223, alle grün** — erstmals
  zeitunabhängig.

### 3.15 30. Juli (Nacht) — Wächter-Ereignis-Log + Wächter-Sektion im Wochenreport

**Anlass:** Gedrosselte Wächter-Episoden (3.14) waren nur noch im Journal
sichtbar — der Betreiber konnte nicht mehr erkennen, WIE OFT Scanner haengen,
ohne Mails zu zaehlen. Ziel: einmal pro Woche die volle Wahrheit statt
Einzelsicht pro Mail.

**Gebaut:**
- **`modules/watchdog_log.py`** — JSONL-Ereignis-Log
  (`data_cache/watchdog_events.jsonl`, bewusst NICHT /tmp wegen PrivateTmp):
  `log_watchdog_event(kind, scanner, stuck_min, mailed, throttled)` mit
  Kinds `warn | reset | recovery | bg_warn | bg_recovery`; wirft NIE
  (Log-Ausfall darf Waechter/Mailversand nie stoppen); FIFO-Rotation
  (2000 Zeilen → 1500, Guessen-Gate 512 KB); `load_watchdog_events(days)`
  toleriert korrupte Zeilen; `summarize_watchdog_events` aggregiert je
  Scanner (Episoden, gemeldet, gedrosselt, Resets, Entwarnungen, Ø Dauer).
- **Wiring:** api.py loggt in `_send_stuck_scan_mail` (warn/reset, auch die
  Throttle-Unterdrückungen) und `_send_stuck_recovery_mail`;
  bg_service.py loggt `bg_warn`/`bg_recovery` in den beiden bg-Mailern.
  Import ueberall try/except-guarded.
- **Wochenreport-Sektion** `_watchdog_report_section`: >0 Episoden → gelbe
  Tabelle „🐕 Scan-Waechter diese Woche" (Scanner | Episoden | gemeldet |
  gedrosselt | Resets | Entwarnungen | Ø Dauer) inkl. Drossel-Hinweis;
  0 Episoden → gruene Zeile „Keine Hänge-Episoden diese Woche ✓";
  Ladefehler → Sektion faellt weg, Report geht immer raus. Im Builder als
  `watchdog_events=None` injizierbar (Tests); `_run_weekly_report` laedt
  selbst (7 Tage). Preview-Skript prueft die Sektion jetzt als Pflicht-Check.
- **Tests:** +19: `test_watchdog_log.py` (9: Roundtrip, Tage-Filter,
  Korrupt-Zeilen, Rotation, Summarize, Nie-werfen), Watchdog-Event-Assertions
  (6: warn/reset/recovery × mailed/throttled, bg-Kinds, tmp-Umleitung via
  autouse-Fixture), Report-Sektion (4: Tabelle mit Events, All-clear,
  Loader-Ausfall, Direkt-Injektion). Suite **1242, alle grün**.

**Effekt:** Der Betreiber sieht Freitag im Wochenreport auf einen Blick, ob
die Throttle (3.14) nur Spam verhinderte oder ob ein Scanner chronisch
haengt — inkl. der Episoden, die bewusst NICHT gemailt wurden.

### 3.16 31. Juli (früh) — Verifizierung + Zweitkalibrierung: Intervall 60 Min, Budget 35 Min

**Live-Verifizierung der 3.14-Fixes (Server-Journal, 30./31.07.):**
Vor dem Deploy (17:04–18:08): 2 WATCHDOG-Fehlalarme bei 3 Sweeps. Nach dem
Deploy (19:00 → Folgetag 05:25, 15+ Sweeps): **0 Wächter-Einträge, 0 Mails.**
Throttle + Erstkalibrierung wirken wie gebaut.

**Neue Messerkenntnis:** US-Session-Laufzeiten des Sweeps **21,5–23,3 Min**
(nach US-Close ~14 Min) → das 25-Min-Budget hatte nur ~2–4 Min Marge; ein
hektischer Tag mit mehr Kandidaten hätte den Fehlalarm zurückgeholt.

**Zweitkalibrierung (Betreiber-Wunsch „1x pro Stunde reicht"):**
- **Intervall `strategy_scan` 30 → 60 Min.** Begründung: Swing-Horizont
  (mehrtägig) — ob ein Signal 30 oder 60 Min alt ist, ist ohne Informations-
  gewinn; der 8h-Ticker-Cooldown dominiert ohnehin. **Opening-Fenster
  (9:25–11:30 ET) bleibt 10 Min** (B3): frische Ausbrüche am Open werden
  weiterhin schnell gefangen — `min(base, 10)` greift automatisch.
- **Budget 25 → 35 Min** (= P95 23,3 Min + ~50 % Puffer), Hartdeckel 105 Min.
- Nebenbefund: doppelte DONE-Zeilen (gleiche Sekunde) nach 22:00 =
  Nachhol-Logik des Schedulers, harmlos, Beobachtung läuft.
- **Tests:** 4 angepasst (Budget/Hartdeckel 35/105, Basis-Takt 60 in
  `test_premarket_radar`, `test_email_alert_audit`-Quellen-Assert,
  Watchdog-Kalibrier-Test). Suite **1242, alle grün**.

### 3.17 31. Juli (früh) — Smart-Money-Radar: Info-Block, NIE Trigger

**Anlass (Betreiber):** „Kann man überwachen, wenn jemand Großes massiv kauft/
verkauft — BlackRock BTC, Aktien, Gold, Öl?" Ehrliche Antwort: Akteure sind
unsichtbar (Iceberg/Dark Pool/OTC), aber ihre **Fußspuren** sind messbar.
Betreiber-Vorgabe: als abrufbarer Info-Block, **niemals** Trigger.

**Gebaut:**
- **`modules/smart_money_radar.py`** — drei Sektionen, jede mit eigenem
  `status` (ok/disabled/error), Teilausfälle killen den Block nie:
  1. **BTC-ETF-Flows** (Farside-HTML, ohne Key; Std-lib-Parser, tolerant):
     IBIT (BlackRock) + Gesamt-Flüsse in Mio. USD, tagesaktuell (EOD) — das
     IST der institutionelle Kauf, einen Tag verspätet.
  2. **Volumen-Wellen** (Polygon Tages-Aggs): RVOL (letzter Bar / Ø 20) für
     SPY/QQQ/IWM/GLD/SLV/USO/UNG/TLT/UUP/HYG/EEM + BTC/ETH; „🌊 Welle" ab
     1,8× — die Minute-genauere Fußspur in Gold/Öl/Aktien/Krypto.
  3. **Whale-Transfers** (optional via `WHALE_ALERT_KEY`): große On-Chain-
     Transfers, klassifiziert als Exchange-Zufluss (Verkaufsdruck) /
     Abfluss (Akkumulation) / Wallet-to-Wallet. Ohne Key: disabled-Hinweis.
  Cache: Gesamt-Artefakt 30 Min in `data_cache/smart_money_radar.json`
  (atomar geschrieben), Stale-Fallback, wirft NIE.
- **Abruf:** `GET /api/smart-money-radar` (JSON, öffentlich, `?refresh=1`
  erzwingt Neuaufbau) + **Seite `/smart-money`** (handgeschriebenes
  `frontend/smart_money.html` ohne React-Bundle, dunkler Alpha-Stil,
  Aktualisieren-Button, Pflicht-Disclaimer „Nur Kontext — kein Signal").
- **Ehrlichkeits-Guard (Test):** `test_radar_not_imported_in_trigger_paths`
  scheitert, sobald das Modul außerhalb von `api.py` (Endpunkt) importiert
  wird — Scoring/Gates/Mails können den Block also gar nicht „aus Versehen"
  als Trigger nutzen.
- **Tests:** +17 (`test_smart_money_radar.py`): Farside-Parsing (Klammer =
  negativ), RVOL-Rechnung, Whale-Klassifizierung, Disabled-/Fehler-Pfade,
  Cache fresh/stale, Nie-werfen, Endpunkt + Seite, Guard. Suite **1259,
  alle grün**.

**Einordnung (Trading):** ETF-Zufluss-Serie + Whale-Akkumulation + RVOL-Welle
in derselben Richtung = starkes Hintergrund-Regime für bestehende Setups.
Allein daraus kaufen wäre genau das Chasen, das die Gates verhindern.

### 3.18 31. Juli (vormittags) — Monster-Volumen Aktien: 4. Radar-Sektion mit eigener Baseline

**Anlass (Betreiber):** „Und Aktien? API-Kosten OK, wenn es was bringt."
Bei Aktien gibt es kein On-Chain — die ehrlichste Spur ist die
Volumen-Welle im Einzelwert.

**Design (Senior-Entscheid):** Statt 21 teurer Historien-Calls pro Refresh
nutzt die Sektion den **Polygon Market-Snapshot** (ganzer US-Markt in EINEM
Call) und baut daraus täglich eine **eigene 20-Tage-Volumen-Baseline** auf
(`data_cache/smart_money_volumes.json`, Rolling-Store 30 Tage, idempotent je
Handelstag). Daten-Datum = jüngster `updated`-Zeitstempel als ET-Tag →
Wochenend-/Feiertags-Aufrufe verfälschen die Baseline nicht (Wochenend-Lehre).
Kosten: **1 Call pro Refresh** (Radar-Cache 30 Min).

**Logik:** Filter ≥ 50 M$ Tages-$-Volumen (Rauschfilter), RVOL = heute /
Ø 20 eigene Baseline-Tage (nur mit ≥ 3 Tagen), „🌊 Welle" ab 1,8×,
Richtung aus Tages-Change (grün Kauf-, rot Verkaufswelle), Top 15.
Aufbauphase (< 20 Baseline-Tage): Badge „building", Ranking vorläufig nach
$-Volumen, Hinweis im Klartext — keine versteckte Unreife.

**Frontend:** Neue Karte „🏦 Monster-Volumen Aktien" auf `/smart-money`
(zwischen ETF-Flows und Makro-Wellen), building-Badge in Amber.
Extern-401 verifiziert als Middleware-Verhalten (Cookie-Session im Browser
→ same-origin fetch läuft automatisch authentifiziert).

**Tests:** +7 (Rolling-Store-Kürzung/Idempotenz, Baseline-Ausschluss des
Datentags, RVOL/Filter/Richtung/Top-N, Building→Reifung, volle Baseline mit
Welle, Fehler-Nie-werfen). Suite **1266, alle grün**.

### 3.19 31. Juli (vormittags) — Insider-Trades (SEC Form 4): 5. Radar-Sektion

**Anlass (Betreiber):** „ja" zur Königsdisziplin — der einzige Fall, wo
„jemand kauft" **namentlich** sichtbar wird: Meldepflichtige Insider-Deals.

**Quelle (gratis, offiziell):** SEC EDGAR Current-Filings-Atom (neueste
Form 4) → pro Filing `index.json` → primäres Ownership-XML. Fair-Use:
`SEC_USER_AGENT` (ENV-übersteuerbar), max. 12 XML-Dateien pro Refresh,
Radar-Cache 30 Min begrenzt die Last. 1–2 Tage Verspätung ist der Natur der
Meldefrist geschuldet (Form 4 = binnen 2 Geschäftstagen).

**Logik (ehrlich eng):** Nur **Open-Market**-Transaktionen (Code `P` = Kauf,
`S` = Verkauf) — Grants (A), Option-Exercise (M) & Co. fliegen raus, weil
sie keine Marktmeinung sind. Rauschfilter ≥ **$100k** Deal-Wert. Ausgabe:
Ticker, Insider-Name, Rolle (Director/Officer/10%-Owner), Richtung
(🟢 KAUF/🔴 VERKAUF), Wert, Datum, Top 15 nach Wert. Parser tolerant
(Std-lib ElementTree, keine neuen Dependencies).

**Frontend:** Karte „🕵️ Insider-Trades (SEC Form 4)" auf `/smart-money`
zwischen Monster-Volumen und Makro-Wellen.

**Tests:** +6 (Atom-Parsing, P/S-Filter & Mindestwert, Kauf/Verkauf-Richtung,
E2E mit gemocktem HTTP inkl. User-Agent-Assert, Empty-Pfad, Nie-werfen).
Suite **1272, alle grün**.

### 3.20 31. Juli (vormittags) — Insider-Cluster-Detektor: 6. Radar-Sektion

**Anlass (Betreiber):** „klar" — das statistisch stärkste Insider-Signal:
**3+ verschiedene Insider kaufen dieselbe Firma innerhalb von 14 Tagen**
(klassische Literatur: Lakonishok & Lee; Cluster-Breite schlägt Einzel-Deals).

**Design:** Gleiches Muster wie die Aktien-Baseline (3.18): rollierender
Insider-Verlauf `data_cache/insider_trades_history.json` (45 Tage, Dedupe
über Filing-Deal-Schlüssel, idempotent je Refresh) — Cluster-Reife wächst
mit jedem Tag. Refactoring: `_fetch_latest_form4_trades()` teilt die volle
Trade-Liste zwischen Anzeige-Sektion (Top 15) und Verlauf (komplett).

**Logik (pure `detect_insider_clusters`):** Fenster 14 Tage, Gruppierung je
(Firma, Richtung), Cluster ab **3 verschiedenen Insidern** (gleicher Insider
mit 2 Deals zählt einmal), Kauf-Cluster vor Verkauf-Clustern sortiert
(stärkeres Signal zuerst), Summenwert + Namen + Breite. **EDGAR-Ausfall
toleriert:** Der Verlauf allein reicht für die Detektion. Aufbauphase
(< 14 Verlauf-Tage): Badge „building" mit Klartext-Hinweis.

**Frontend:** Karte „🧩 Insider-Cluster (stärkstes Signal)" direkt unter den
ETF-Flows — bewusst weit oben, weil es das signalstärkste Element ist.

**Tests:** +5 (Dedupe/Prune des Verlaufs, 3-Insider-Kauf-Cluster mit
Mehrfach-Deal-Dedupe, Verkauf-Seite + Sortierung, Unter-Schwelle leer,
Building→Cluster mit EDGAR-Ausfall-Toleranz). Suite **1277, alle grün**.

### 3.21 31. Juli (mittags) — ℹ️-Mail nur bei NEUEM Insider-KAUF-Cluster

**Anlass (Betreiber):** „sag mir Bescheid, wenn es zählt" — damit die Seite
nicht täglich besucht werden muss. Einzige Mail, die der Radar je auslöst;
**info-Klasse, nie Trigger** (Betreiber-Vorgabe).

**Job (`bg_service._run_insider_cluster_alert`, self-gated, 15-Min-Anklopf):**
- **Fenster Mo–Fr 16:30–23:00 ET** (Form 4 = EOD-Daten; Wochenende frei).
- **Tages-Markier-Key** `insider_cluster_scan_{datum}` — egal ob Treffer
  oder nicht: max. 1 Prüfung/Tag, kein Dauerfeuer; wird NUR bei erfolgreichem
  Versand oder „nichts Neues" gesetzt (SMTP-Fehler ⇒ Retry im Fenster, B2).
- **Cluster-Dedupe über Zusammensetzung** (Symbol + Richtung + Hash der
  Insider-Namen, TTL 14 Tage = Cluster-Fenster): dasselbe Cluster mailt nie
  zweimal — ein **gewachsenes** Cluster (vierter Insider) ist neue
  Information und mailt erneut. Verkauf-Cluster mailen **nie**.
- Mail im Hausstil: Firma, Breite, Summe, Namen, Pflicht-Disclaimer
  „Kein Signal, kein Trigger".
- **Guard-Test präzisiert:** `modules.smart_money_radar` darf nur noch in
  `api.py` (Lese-Endpunkt) und `bg_service.py` (diese ℹ️-Mail) importiert
  werden — Scanner/Tracker/Scoring bleiben hart gesperrt.

**Tests:** +9 (`test_insider_cluster_mail.py`): Fenster-Gate, genau eine
Mail je neuem Cluster, kein Doppel am Folgetag, gewachsenes Cluster mailt,
Verkauf mailt nie, Tages-Key ohne Fetch-Dauerfeuer, SMTP-Fehler-Retry,
Modul-fehlt/Exception nie-werfen, Scheduler-Wiring. Suite **1286, alle grün**.

### 3.22 31. Juli (nachmittags) — BHC-Audit: Mehrtages-Chase-Gates, ehrlicher Betreff, duale Zeitstempel

**Anlass (Betreiber, drei Befunde an einer Live-Mail):** BHC kam 16:09 MESZ
als „🚨 JETZT SWING" — obwohl der Chart die fette Kerze längst zeigte und
der Body „14:09 UTC" schrieb, was mit der Postfach-Anzeige kollidierte.

**Diagnose (drei getrennte Befunde):**
1. **Tages-Anker-Blindheit (Hauptfehler):** Alle Chase-Gates (28./29.07.)
   maßen nur den HEUTIGEN Tag. BHC: heute +5,1 % ≈ 1,2 ATR → unauffällig.
   Der Move lief aber über ~3 Tage (+38 % in 5 Tagen ≈ **8,8 ATR** bei
   ATR 4,34 %) — die fette Kerze war von gestern. Kein Gate sah das.
2. **Betreff-Dissonanz:** Jede Swing-Mail trug pauschal „🚨 JETZT SWING",
   auch wenn die Timing-Spalte nur „Swing-Setup aktiv" sagte — und obwohl
   der eigene Mail-Disclaimer „mehrtägiger Plan" sagt.
3. **Zeitstempel-Verwirrung:** Bodies zeigten nur UTC (das Postfach zeigt
   Lokalzeit); bg_service-Mails zeigten UTC-Uhrzeit sogar mit **falschem
   „CET"-Label** (Server läuft UTC).

**Fixes:**
- **Mehrtages-Gate** (`_swing_multi_day_move_atr`): |Change_5D| ÷ ATR-% —
  das Row-Feld `Change_5D` existierte bereits (history_metrics, komplette
  Daily-Bars; **keine neue Datenquelle**). ≥ 5 ATR/5d →
  `swing_multi_day_extended_wait_retest`; ≥ 7 ATR/5d →
  `swing_multi_day_exhausted_no_chase` (in beiden Entscheidungspfaden
  registriert: `no_trade_markers` → NO_TRADE; Score-Cap 45).
  Schwellen-Logik: 5 ATR/5 Tage = jeder Tag ein voller ATR-Tag in eine
  Richtung (selten, heiß gelaufen); BHC 8,8 ATR = klar erschöpft.
- **Vortag-Anker:** gestriger Tag ≥ 2,5 ATR UND heute grün UND Preis
  ≤ 1 % am Tageshoch → `swing_prevday_run_top_entry_wait_retest`
  (Tag-2-Top-Kauf; fängt auch Rows mit dünner 5d-Historie ab).
- **Betreff:** `swing_trade`-Präfix „🚨 JETZT SWING: " → **„🚨 SWING: "**.
  „JETZT" bleibt ausschließlich Intraday-Trade-Mails (15-Min-Frische-Gate).
  Legacy-Marker ersetzt alte „JETZT SWING"-Betreffe ohne Emoji-Stapelung.
- **Dualer Zeitstempel** (neues `modules/mailtime.py`; EU-Sommerzeit-Regel
  fest implementiert, keine tzdata-Dependency): **alle** Mail-Bodies in
  api.py (12 Stellen) und bg_service.py (10 Stellen) zeigen jetzt
  „31.07.2026 14:09 UTC / 16:09 MESZ".

**Tests:** +14 (4 Gate-Tests: BHC-Repro NO_TRADE, 5,3-ATR-Zwischenstufe
WAIT_RETEST, Vortag-Anker isoliert, Gegenprobe frischer Breakout aus ruhiger
Woche bleibt TRADE_NOW; 9 mailtime-Tests inkl. DST-Stichtage März/Oktober +
api/bg-Aliase; Betreff-Test erweitert um JETZT-frei + Legacy-Ersatz).
Suite **1300, alle grün**.

### 3.23 31. Juli (abends) — Tiefen-Audit: Short-Spiegelung, drei Guards, Backtest-Messschleife

**Anlass (Betreiber):** „alles machen + intensives Audit — solche Fehler
dürfen nicht mehr passieren." Vollständiges Dokument:
`AUDIT_2026-07-31_TIEFENAUDIT.md`. Fehlerklassen: **K1 Anker** (Tag statt
Woche), **K2 Registrierung** (Decision-Mapping = explizite Menge, kein
Suffix), **K3 UX-Ehrlichkeit** (Betreff/Zeit).

**Audit-Befund über den BHC-Fall hinaus:** Die **Short-Seite hatte dieselbe
Lücke gespiegelt** — -38 % über 3 Tage, heute -3 % = „frischer" Short auf
dem Boden. Mehrtages- (≥ 5/≥ 7 ATR) und Vortag-Anker (≥ 2,5 ATR + rot +
≤ 1 % am Tagestief) jetzt symmetrisch (`swing_short_multi_day_*`,
`swing_short_prevday_run_bottom_entry_*`), in beiden Entscheidungspfaden
registriert.

**Drei strukturelle Guards (Prävention, nicht Symptom):**
1. **Reasons-Registry** (`test_reason_registry.py`, bidirektional): jeder
   der 150 erzeugten Gate-Gründe muss im Decision-Mapping landen oder in
   der eingefrorenen 81er-WATCH-Whitelist stehen — ein neuer Grund ohne
   Mapping schlägt sofort an (genau der heutige Fast-Fehler).
2. **Zeitstempel-Guard** (`test_mailtime.py`): verbietet direkte
   UTC-/CET-Mail-Stempel; nur `_mail_timestamp_dual()` ist erlaubt.
3. **Backtest-Konsistenz** (`test_chase_gate_backtest.py`):
   `scripts/chase_gate_backtest.py` rekonstruiert Gate-Inputs aus
   Polygon-Tages-Bars (der Tracker speichert sie nicht — ehrlicher
   Daten-Lücken-Befund) und ruft die **echten** Produktiv-Gates; zählt,
   wie viele der 90-Tage-Signale die neuen Gates blockiert hätten, inkl.
   ØR-/Treffer-Vergleich („gespart oder gekostet"). Läuft auf dem Server:
   `venv/bin/python3 scripts/chase_gate_backtest.py --days 90`.

**Beobachtungspunkte (bewusst nicht gefixt, Begründung im Audit-Dok):**
`crash_drop_too_extended` → WATCH-Kandidat; `rvol_below_bear_threshold`
außerhalb base_blockers; Crypto-Mehrtages-Anker nur weich (Exhaustion-
Score, kein Gate); zwei Mail-Aufrufe mit implizitem mail_class-Default.

**Tests:** +10 (2 Short-Repros, 4 Backtest, 3 Registry, 1 Zeitstempel-Guard).
Suite **1310, alle grün**. Repo-Hygiene: versehentlich getrackte
Laufzeit-Artefakte (Insider-Cache, Report-Preview) enttrackt + gitignore
(`adce713`) — sonst haette der naechste Server-Pull blockiert.

### 3.24 31. Juli (spät) — Backtest-Zahlen: Gate trifft mehrheitlich richtig, MDR-Konflikt sichtbar

**Messung auf dem Server** (`chase_gate_backtest.py --days 90`): 288
Signale gemessen (2 ohne History). **28 blockiert (10 %)** — Quote
vernünftig, nicht überschießend; 12 davon hart (≥ 7 ATR/5d).

**Outcome (nur entschiedene, n=245):**
| Gruppe | n | ØR | Treffer |
|---|---|---|---|
| blockiert | 25 | **+0.06R** | 40 % |
| frei | 220 | **+0.17R** | 40 % |

**Lesart (Trading-Statistik, nicht Skript-Binarlogik):** Identische
Trefferquote, aber blockierte Signale brachten **65 % weniger R pro Trade**
— das Gate selektiert genau die heißesten Moves, und die performten pro
Risikoeinheit schlechter (Mean-Reversion-These bestätigt in Richtung).
Detail: **7 der 15 gelisteten Blockierten waren volle -1R-Stops**
(Extended → Reversal → Stop); 60 % der Blockierten endeten ≤ 0.
**ABER:** n=25 ist klein, R-Streuung groß — Hinweis, kein Beweis. Das
Skript-Urteil wurde deshalb von „GEKOSTET/GESPART (binär)" auf den
**Opportunitätsvergleich** korrigiert (blockiert-Ø vs. frei-Ø).

**MDR-Konflikt (wichtigster inhaltlicher Befund):** CCXI (5d +65 %) lief
+3.67R — ein Multi-Day-Runner, den das Gate geblockt hätte. Der Scanner
BELOHNT genau dieses Muster (MDR-Bonus bis +15 Score für Vortag+Heute-
Kontinuität), das Gate BESTRAFT es. Beide können nicht gleichzeitig recht
haben; die Daten (n=12 harte Blocks) reichen nicht zur Auflösung.
**Entscheidung:** Schwellen bleiben (5/7 ATR), keine Regel-Änderung ohne
bessere Daten — Auflösung per Shadow-Messung (Vorschlag unten).

**Nebenbefund:** 102/288 Fälle, in denen ALT-Gates im Backtest zusätzlich
feuern — die dokumentierte Ganztages-Verzerrung (High/Low des ganzen Tages
statt Mail-Minute); bestätigt, dass die 10 %-Blockquote eine **untere
Schranke** ist.

**Selektionsproblem (ehrlich):** Ab jetzt werden geblockte Signale nie
gemailt → der Tracker sieht sie nie → es gibt **keine** Live-Daten mehr
über die Gate-Kosten. Dieser Backtest war die einzige Messung; die
dauerhafte Lösung ist **Shadow-Tracking** (blockierte Signale still im
Tracker loggen, ohne Mail, nicht in der Win-Rate) — als nächster Schritt
vorgeschlagen, noch nicht gebaut (aendert Tracker-Semantik).

---

### 3.25 31. Juli (Nacht) — Shadow-Tracking gebaut: die Gate-Kosten werden ab jetzt gemessen

**Auslöser:** Der in 3.24 beschriebene Selektions-Blindflug. Ohne diese
Messung bleibt der MDR-Konflikt (Scanner-Bonus vs. Chase-Gate) dauerhaft
unauflösbar.

**Was gebaut wurde (vier harte Regeln, alle test-eingefroren):**

1. **Was geloggt wird** (`api.py`, `_send_strategy_scan_alerts`): Nur
   Aktien-Swing-Rows (`stock_strategy`/`strategy_scan`, regulaere Session,
   kein PM-Radar), die **ausschließlich** an Swing-Timing-Gruenden
   scheitern — fail-closed Whitelist
   `_SHADOW_TRACKABLE_TIMING_REASONS` (34 Gruende, explizit:
   `swing_*` inkl. Multi-Day-/Vortags-/Orts-/Day-Move-Gates, Short-Seite
   gespiegelt). **Bewusst ausgenommen:** Base-Blocker (Score/Grade/RVOL/
   Asset), Plan-Geometrie-Gates (kein auswertbarer Trade-Plan),
   Cooldown/Dedupe (Signal wurde schon gemailt → Doppelzaehlung),
   `intraday_unconfirmed_pattern` (andere Fragestellung),
   `swing_short_not_down_enough` („kein Setup", wuerde fluten). Ein NEUER
   Gate-Grund landet nicht automatisch im Shadow-Track — Registry-Test
   `test_shadow_whitelist_matches_swing_rule_sources` erzwingt die
   explizite Entscheidung.
2. **Keine Mail, niemals:** Shadow-Rows gehen nur in den Tracker
   (`mail_class='shadow'`, `channel='shadow'`, Block-Gruende in neuer
   Spalte `block_reasons`, idempotente Migration). Die Exit-/BE-Mails in
   `bg_service` filtern `mail_class != 'trade'` — zusätzlich zur
   bestehenden Zweitsicherung `_signal_origin_was_mailed` (die fuer
   Shadow ohnehin nie greift).
3. **Kein Statistik-Einfluss:** `load_performance_summary` filtert hart
   `mail_class = 'trade'` (Win-Rate, Wilson, Verdikt, Wochenreport-
   Tabellen unveraendert). Ebenso die vier Analyse-Skripte
   (`exit_efficiency_analysis`, `websocket_benefit_analysis`,
   `signal_performance_breakdown`; `chase_gate_backtest` hatte den Filter
   schon).
4. **Kein Dedupe-Konflikt:** Der Tracker-Dedupe-Key ist jetzt
   `(scanner, ticker, mail_class, OPEN)` — ein Shadow-Signal blockiert
   weder eine spaetere echte Mail desselben Tickers noch umgekehrt.
   Trade-Verhalten (ein OPEN pro Ticker) bleibt unveraendert.

**Auswertung:** Shadow-Signale laufen durch denselbe stuendlichen
Eval-Pfad (Daily-OHLC, 5-Bar-Expiry) — Transitionen/BE-Aktivierungen
tragen jetzt `mail_class` (Vertrags-Erweiterung, in
`test_be_activation.py`/`test_exit_update_mails.py` eingefroren).
Neue Funktion `shadow_summary(days)`: n, offen, entschieden, Trefferquote,
ØR/ΣR, Gruende-Verteilung, letzte Signale. **Wochenreport-Sektion
„🕶 Shadow-Messung (Chase-Gates)"** (nur bei ≥ 1 Shadow-Signal):
ØR geblockt vs. ØR gemailt nebeneinander, Top-3-Blockgruende,
Vorsichts-Hinweis solange entschiedene < 30.

**Erwartetes Volumen / Eval-Last:** Backtest-Quote ~10 % der Signale
(≈ 1–3 Shadow-Signale/Tag) — die zusaetzliche Eval-Last (ein
Daily-Bar-Fetch pro offenem Shadow-Signal) ist vernachlaessigbar.

**Entscheidungsregel in ~4 Wochen** (ab n ≥ 30 entschiedenen):
ØR_shadow deutlich < ØR_trade → Gates sparen Geld (bestaetigt);
ØR_shadow ≥ ØR_trade → Gates kosten → Schwellen (5/7 ATR) neu justieren
und MDR-Bonus/Gate-Konflikt mit echten Forward-Daten aufloesen.

**Tests:** `test_shadow_tracking.py` (21 Tests: Whitelist-Helper,
Registry-Guard, Record/Dedupe-Isolation, Summary-Filter, shadow_summary,
Eval-Integration, Mail-Filter Update/BE, Wochenreport-Render,
Pipeline-Integration inkl. „Shadow auch ohne jede Mail", Real-Classify-
Integration). Suite **1331, alle gruen**.

### 3.26 1. August — Performance-Einbruch offengelegt, Mail-Outbox und Regime-Gate

**Anlass:** Der Forward-Tracker zeigte einen realen Qualitätsbruch: 7 Tage mit
56 Signalen/21 Entscheidungen, 0 % Trefferquote und −26,2 R; 14 Tage mit
101 Signalen/57 Entscheidungen, 16 % Trefferquote und −44,6 R. Diese Zahlen wurden
nicht beschönigt. Gleichzeitig konnte ein SMTP-Fehler eine Mail im flüchtigen
Versandpfad verlieren.

**Gebaut:**

- SMTP-Fallback von Port 465 auf 587/STARTTLS.
- Persistente SQLite-Mail-Outbox (`modules/mail_outbox.py`) mit atomarem Claim,
  Lease, Retry, Ablauf und Dead-Letter-Status. Semantik: at least once; bei einem
  Prozessabsturz exakt zwischen SMTP-Erfolg und Outbox-Acknowledge ist eine
  Doppelmail möglich, ein stiller Verlust aber nicht mehr der Normalfall.
- Persistenter Markt-/Performance-Regime-Filter (`modules/regime_filter.py`):
  Scanner laufen immer weiter, aber RED kann Swing-Mails zu Watch/Shadow
  degradieren; YELLOW verlangt +5 Score und begrenzt die Mail auf höchstens zwei
  Zeilen. Der Markt-Kontext selbst bleibt bei Datenfehlern fail-open, der
  Performance-Breaker nicht.
- Breaker-Erstkalibrierung: Trip ab mindestens 10 entschiedenen Signalen bei
  ØR ≤ −0,30 und Trefferquote ≤ 25 %; Freigabe erst nach mindestens fünf neuen
  Trade-/Shadow-Beobachtungen mit ØR > −0,10 und Trefferquote ≥ 30 %.

Detailbericht: `AUDIT_2026-08-01.md`.

### 3.27 2. August — Scanner-Recovery, Lauf-ID-Schutz und Tracker-Kohorten

- Scannerläufe erhalten eindeutige Lauf-Identitäten; ein noch lebender Thread darf
  nicht parallel ein zweites Mal gestartet werden. Timeouts werden als hängender
  Lauf sichtbar statt den Status fälschlich auf „fertig/Cache-Fehler" zu setzen.
- Leichte Jobs blockieren die zentrale Scheduler-Schleife nicht mehr; schwere Jobs
  besitzen Laufzeitbudgets und veröffentlichen nur abgeschlossene, ehrlich
  gekennzeichnete Ergebnisse.
- Signal-Tracker trennt jetzt **created-in-window** von **fully-observed**. Ein
  Signal zählt nur dann als vollständig beobachtet, wenn sein Beobachtungsfenster
  abgeschlossen ist; offene junge Signale verfälschen keine Trefferquote.
- 50/50-Management wird als tatsächlicher Payoff gerechnet: 50 % bei TP1, Rest bis
  TP2/Stop/BE. Geometrisches Level-R und realisiertes Managed-R bleiben getrennt.

### 3.28 3. August — ORB-Laufzeit, Signalvalidierung und Zielpläne

- ORB-UI und Backend verwenden denselben finalen Handelsstatus; Breakout erkannt,
  Entry-Lage und Trade-Freigabe sind getrennte Aussagen und dürfen sich nicht mehr
  als „Entry gut" neben „No Trade" widersprechen.
- ORB-Targets werden auf gültige Richtung, Mindestabstand, Invalidation und
  nutzbares R:R geprüft. Zu enge Cent-Ziele oder identische TP1/TP2 werden nicht als
  fertiger Plan akzeptiert.
- Scanner-Runtime/Progress wurde gegen überlappende Läufe und rückspringende
  Fortschrittsanzeigen gehärtet; Ergebnis-Sortierung zeigt handelbar vor warten und
  blockiert.

### 3.29 4. August — Unternehmensidentität in Aktien-Signalen

- Aktien-Scanner, Detailansicht, E-Mails und Telegram führen neben dem Ticker den
  vollständigen Unternehmensnamen, soweit die Referenzdaten ihn liefern.
- Die Identität wird aus geprüften Common-Stock-Referenzdaten übernommen; das
  reduziert Verwechslungen bei ähnlichen Symbolen und erschwert, dass Fonds,
  Warrants oder strukturierte Produkte als Aktie dargestellt werden.

### 3.30 5. August — Tracker-/Dedupe-Härtung und ausfallsichere Stop-Update-Mail

- Alert-Dedupe arbeitet atomar und trennt Signal-, Update- und Stop-Update-
  Namespaces. Ein Update verbraucht nicht mehr versehentlich den Schlüssel einer
  anderen Mailklasse.
- Der Signal-Tracker speichert für BE-Aktivierungen zusätzlich
  `be_mail_sent_at`. `load_pending_be_activations()` lädt nicht zugestellte
  Aktivierungen bis zu sieben Tage nach; `mark_be_alerts_sent()` bestätigt sie erst
  nach erfolgreichem Versand.
- Aktuelle und nachgeladene BE-Aktivierungen werden je Signal-ID zusammengeführt.
  Schlägt SMTP fehl oder startet der Dienst neu, wird die Stop-Update-Mail im
  nächsten Eval-Lauf erneut versucht. Der DB-Zustand ist hier bewusst die einzige
  Retry-Quelle; die allgemeine Outbox wird für diese Mail nicht parallel verwendet.
- Stop-Update und allgemeines Signal-Update bleiben zwei getrennte Mailtypen. Eine
  ausbleibende Stop-Mail ist damit diagnostizierbar und nicht mehr vom zufälligen
  Timing genau eines Auswertungslaufs abhängig.

**Verifikation dieses Endstands:** `test_be_activation.py` gezielt 17/17 grün,
volle Suite **1400/1400 grün**, Python-Compile und `git diff --check` grün.
Commit `5aea225` ist auf GitHub `main`; der Produktions-Rollout wurde in dieser
Sitzung nicht per SSH bestätigt.

### 3.31 5. August — Persönliche Positionen für Folge- und Stop-Mails

- Nutzer können ein Scanner-Signal in der Detailansicht als tatsächlich gekauft
  oder geshortet markieren und später wieder entfernen. Die Markierung speichert
  Ticker, Richtung, Scanner, Signal-ID, Asset-Typ und Unternehmensname; maximal
  250 persönliche Positionen pro Konto.
- In den Alert-Einstellungen gibt es zwei klar getrennte Modi:
  `Alle Systemsignale` behält das bisherige Verhalten, `Nur meine Trades` filtert
  allgemeine Signal-Updates und Stop-/Einstand-Mails auf die persönlich markierten
  Positionen.
- Erstsignal-Mails, Scanner-Ergebnisse, der globale Signal-Tracker und der
  Wochenreport bleiben vollständig. Die persönliche Auswahl darf den objektiven
  Forward-Track-Record nicht verändern oder gute/schlechte Systemsignale aus der
  Statistik entfernen.
- Die Zustellung und Deduplizierung erfolgt pro Empfänger. Ein SMTP-Fehler bei
  einem Nutzer blockiert keine anderen Nutzer; beim Retry wird nur der fehlende
  Empfänger erneut bedient. Mehrere Konten mit derselben Zieladresse werden sicher
  zusammengeführt; `all` hat dabei Vorrang vor `mine`.
- Die Markierung ist ausdrücklich **keine Broker-Order**. Diese Trennung bleibt
  auch nach Einführung des Paper-AutoTraders bestehen: persönliche Positionen
  und echte Broker-Positionen sind getrennte Datenquellen.

**Mail-Stichprobe vom 3./4. August:** Die beigefügten Folge-Mails zeigen ein
gemischtes Bild: unter anderem erreichte OWL TP2, KC und LBRX erreichten TP1;
GIB, ALRS und KSPI liefen in den Stop. Viele neuere Swing-Signale waren zum
Prüfzeitpunkt noch offen. Daraus wird bewusst keine belastbare Trefferquote
abgeleitet, weil Beobachtungsfenster und Stichprobe unvollständig sind.

**Verifikation:** fünf neue Regressionstests für Normalisierung, Profil-Merge,
Signalzuordnung, Empfängerfilter und isolierten Retry; volle Suite
**1405/1405 grün**, Python-Compile und `git diff --check` grün.

### 3.32 6. August — Stabile Setup-Identität und Same-Day-Auswertung

- Der Signal-Tracker identifiziert ein Setup nicht mehr nur über gerundete
  Entry-/Stop-/TP-Werte. `setup_key`, Strategie und Zeithorizont werden
  persistiert; für ältere Signale bleibt ein toleranter Geometrievergleich als
  kontrollierter Fallback erhalten. Kleine Rundungs- oder Kursformatänderungen
  erzeugen dadurch kein zweites scheinbar neues Signal.
- Aktien-Signale vom laufenden Handelstag werden mit abgeschlossenen 5-Minuten-
  Intervallen ausgewertet, statt bis zur nächsten Daily-Kerze offen zu bleiben.
  Eine fehlende Intraday-Antwort wird weder als Kursfortschritt noch als
  Datenfehler persistiert; der nächste Lauf darf sauber erneut prüfen.
- Persönliche Positionen speichern dieselbe Setup-Identität und die relevanten
  Plan-Level. Folge- und Stop-Mails im Modus `Nur meine Trades` werden zuerst per
  Signal-ID beziehungsweise `setup_key` und erst danach über kompatible
  Geometrie zugeordnet. Gleiches Symbol und gleiche Richtung allein reichen bei
  mehreren Setups nicht mehr aus.
- Erstsignal-Mails, Scanner-Ergebnisse und der globale Forward-Track-Record
  bleiben weiterhin vollständig. Die persönliche Zuordnung verändert nur die
  empfängerbezogenen Folge- und Stop-Mails.

**Verifikation:** gezielte Tracker-/Positionssuite **43/43 grün**, vollständige
Suite **1410/1410 grün**, Python-Compile, `git diff --check` und der verifizierte
Frontend-Bundle-Hash **0c8663e92b1c** sind grün.

### 3.33 8. August — Kontrollierter IBKR-Paper-AutoTrader

- Die Ausführung ist technisch **Paper-only**. Standardzustand ist
  `paper_review`, Ausführung aus und Kill-Switch aktiv. Nur ein von IBKR
  gemeldetes `DU...`-Paperkonto darf das Konto-Gate passieren; Livekonten werden
  unabhängig von UI oder Konfiguration abgewiesen.
- Normale Risiko-Konfiguration kann den Ausführungszustand nicht verändern.
  Arming erfordert einen Admin, die exakte Bestätigung
  `PAPER AUTO AKTIVIEREN`, eine aktive Paper-Verbindung und sämtliche Konto- und
  Risiko-Gates. Disarm und Kill-Switch bleiben jederzeit möglich.
- IBKR ist die Quelle für Positionen, offene Orders, Fills, Kontowerte und
  Tages-PnL. Nach Neustart oder Verbindungsunterbruch rekonstruiert die
  Reconciliation den Brokerzustand; lokale Scheinpositionen, erfundener PnL und
  ein manueller Positions-Reset sind ausgeschlossen.
- Jeder Setup-Intent und jede Order erhält eine stabile, eindeutige Referenz.
  Cooldown und Trade-Zähler beginnen erst nach echtem Fill. Auch ein vollständig
  gefüllter und wieder geschlossener Trade zwischen zwei Abfragen bleibt über
  die IBKR-Fills nachvollziehbar.
- Pro Teilposition wird ein Parent mit Stop und Take-Profit als OCA-Gruppe
  übermittelt. Reine Watch-/Retest-Signale werden nicht ausgeführt. Entry, Stop,
  TP1 und TP2 müssen aus einem validierten Scanner-Plan stammen; der AutoTrader
  erfindet keine Ersatz-Level.
- Defensive Standardlimits: maximal 3 Positionen, 0,25 % Kontorisiko pro Trade,
  1 % Tagesverlust, 2.000 USD Order-Notional, 20 % Bruttoexposure sowie Reserve-,
  Stückzahl-, Mindest-R:R- und Daily-PnL-Prüfung.
- Der Kill-Switch sperrt neue Ausführung und storniert ausschließlich noch nicht
  gefüllte Alpha-Station-Parent-Entries. Er liquidiert keine bestehende Position
  blind. Der Stop-Manager darf vorhandene Stops nur enger, niemals weiter vom
  Risiko weg setzen.
- Status, Reconciliation, Arming, Kill-Switch, Stop-Anpassung und Intent-Pruning
  liegen hinter der Admin-Authentifizierung. Das Admin-Panel zeigt ausschließlich
  den abgeglichenen Brokerzustand und ein persistentes Audit-Protokoll.
- Technische Abnahme am 8. August 2026: **1417/1417 Repository-Tests** und
  **123/123 fokussierte Paper-/Auth-/Frontend-Tests** bestanden; `api.py`,
  `modules/scanners.py` und `modules/paper_autotrader.py` kompilieren fehlerfrei.
  Das ausgelieferte Frontend-Bundle wurde aus der eingebetteten Quelle neu
  erzeugt und mit Hash **c0b3b13a6c86** verifiziert.
- Bewusste Grenze: Unit-/Integrationsprüfungen mit einem Fake-Broker ersetzen
  keinen mehrtägigen TWS-/IB-Gateway-Paper-Soak. **Live-Trading ist nicht
  freigegeben und bleibt blockiert.**

### 3.34 8. August - Revisionsgebundener Auto-Deploy und Frontend-Nachweis

- Der Auto-Updater fuehrt bei einem neuen `origin/main` nicht mehr das moeglicherweise
  veraltete lokale Deploy-Skript aus. Er extrahiert `deploy/safe_deploy.sh` direkt
  aus dem gefetchten Ziel-Commit und uebergibt dessen vollstaendige Revision als
  `EXPECTED_REVISION`.
- `safe_deploy.sh` prueft vor und nach dem Pull, dass erwarteter Ziel-Commit,
  ausgechecktes `HEAD` und der spaeter laufende API-Prozess exakt zusammenpassen.
  `/api/health` und `/api/system-health` liefern dafuer `revision` und
  `frontend_bundle`.
- Ein HTTP-200 allein gilt nicht mehr als erfolgreicher Rollout. Die API muss die
  erwartete Git-Revision und den erwarteten Bundle-Hash melden; zusaetzlich muessen
  ausgeliefertes `index.html`, `app.bundle.js` und `boot.js` bytegleich mit dem
  ausgecheckten Ziel-Commit sein.
- Derselbe Identitaetsvertrag gilt beim automatischen Rollback. Erst wenn alter
  Commit, API-Revision, Bundle-Hash und ausgelieferte Frontend-Dateien wieder
  uebereinstimmen, wird der Rollback als gesund gemeldet.
- Dadurch bedeutet `Health OK` nun: exakt der gefetchte Build ist aktiv. Ein
  GitHub-Push oder ein beliebiger erreichbarer Health-Endpunkt allein bleibt kein
  Produktionsnachweis.
- Lokale Abnahme: 93 Fokus-Tests und 1420 Tests der vollen Suite sowie Bash-Syntax,
  Python-Compile und Frontend-Bundle-Pruefung waren gruen. Der separate
  Produktionsnachweis bleibt bis zur Serverpruefung offen.

### 3.35 8. August - Persönliche Positionen und exakte Folge-Mail-Zuordnung

- Nutzer können ein versendetes Setup als tatsächlich gekauft/geshortet markieren
  und dabei Richtung, Setup-Referenz, Fill und Stückzahl festhalten.
- Folge-, Signal-Update- und Stop-Mails können je Empfänger wahlweise nur die
  persönlichen Positionen oder weiterhin alle globalen Signale enthalten.
- Erstsignal-Mails, Scanneruniversum und objektiver globaler Forward-Track-Record
  bleiben davon unberührt. Persönliche Auswahl darf die Modellstatistik nicht
  nachträglich selektieren.
- Deduplizierung und Retry erfolgen je Empfänger und Signalidentität. Die Auswahl
  eines Nutzers kann die Zustellung eines anderen nicht mehr verbrauchen.
- Same-Day-Auswertung und stabile Setup-Identität verhindern, dass gleichnamige
  Ticker/Scannerläufe oder neu berechnete Level mit einer alten Position vermischt
  werden (`cb80b90`, `5f5c410`).

### 3.36 8.-10. August - Geschützter IBKR-Paper-AutoTrader V2

- Broker-Reconciliation, stabile Order-/Setup-Referenzen, Bracket-/OCA-Orders,
  Partial-Fill-/Reject-Verarbeitung, Restart-Sicherheit, Stop-Manager,
  Tagesverlust-/Exposure-Limits, Kill-Switch, Admin-UI und Audit-Log wurden als
  defensive Paper-Infrastruktur zusammengeführt (`aa5c395`).
- IBKR bleibt Quelle für Positionen, Fills, Orders und PnL. Lokale Scheinpositionen
  oder blindes Löschen/Glattstellen sind ausgeschlossen.
- Die Ausführung ist standardmäßig deaktiviert und auf ein `DU...`-Paperkonto
  begrenzt. Ein mehrtägiger realer TWS-/IB-Gateway-Soak wurde noch nicht belegt;
  Live-Trading bleibt ausdrücklich blockiert.

### 3.37 9.-10. August - Signalmathematik, Business Quality und ehrliche Auswertung

- Rechenkern, Tracker, Backtests, Stop-/Zielpläne und Mail-Gates wurden auf dieselbe
  R-Geometrie ausgerichtet. Ungültige Ziele dürfen nicht mit `abs()` positiv
  gerechnet werden; Live-R:R nutzt den aktuellen ausführbaren Preis.
- Gefüllt, entschieden, offen, No-Fill und unaufgelöst werden in Statistik und
  Backtest getrennt. Same-Bar-Stop/TP ohne Intraday-Reihenfolge wird konservativ
  oder als Ergebnisintervall behandelt statt chronologisch erfunden.
- Gap-through-Stop, Kosten, Slippage, strategieabhängige Haltedauer und
  Entry-Bar-Kausalität wurden gehärtet. Ein enger Stop darf das beworbene R:R nicht
  künstlich schönrechnen (`1d3145a`).
- Business-/Asset-Qualität, strukturelle Setup-Qualität und aktuelles Trade-Timing
  sind getrennte Größen. Ein hochwertiges Unternehmen ist nicht automatisch ein
  guter Einstieg; ein guter Trigger repariert kein schlechtes Basis-Setup
  (`744e9b2`).

### 3.38 10. August - Post-Pump-Swing-Shorts und 4H-Rejection

- Ein stark gestiegener Wert wird nicht allein wegen einer roten Kerze zum
  Swing-Short. Für Post-Pump-Shorts wird eine bestätigte 4H-Rejection bzw. ein
  Strukturbruch verlangt.
- Der OPHC-Fall zeigte die Gefahr: ein Gap-Momentum-Short direkt nach vertikalem
  Anstieg konnte trotz rechnerisch gutem R:R fachlich unbestätigt sein. Der neue
  Guard blockiert solche Sofortfreigaben und verlangt eine nachweisbare Umkehr
  (`1bd9a48`).

### 3.39 11. August - Projektbibel und PC-Übergabe

- `PROJEKTBIBEL.md` ist ab jetzt die normative Referenz für Zustände, Mathematik,
  Datenfrische, Scannerlogik, Stops/Ziele, Tracker, Broker, UX und Deployment.
- `HANDOFF_PC_WECHSEL_2026-08-11.md` trennt Git-Bestand, Secrets, Serverdaten und
  Produktionsnachweis und enthält den reproduzierbaren Setup-/Test-/Deploy-Ablauf
  für den neuen Windows-PC.
- Das Handbuch bleibt Chronik und aktueller Betriebsstand. Alte Chat- oder
  Audit-Aussagen gelten nicht als implementiert, wenn Patch, Test und aktueller
  Code sie nicht belegen.

### 3.40 11. August - Neuer PC: reproduzierter Build und Secret-Redaktion

- Python 3.12, die exakten `requirements.txt`-Abhängigkeiten und Node.js LTS wurden
  auf dem neuen Windows-PC eingerichtet. Python-Compile und Frontend-Bundle-Prüfung
  sind grün; der Bundle-Quellhash ist `a9df895d5455`.
- Der Turtle-Regressionsfall isoliert jetzt den Common-Stock-Universe-Loader. Der
  Test ist damit offline-deterministisch; das produktive Fail-closed-Verhalten des
  Asset-Guards wurde ausdrücklich nicht gelockert.
- Polygon-Schlüssel in URL-Querywerten werden vor der Fehlerausgabe zentral
  redigiert. Sowohl `api.py` als auch `bg_service.py` verwenden denselben Helper;
  Canary-Regressionstests belegen Maskierung und unverändertes Fallback-Verhalten.
- Endabnahme des Code-Commits `d11eb4c`: **1503/1503 Tests grün**, Python-Compile,
  Frontend-Bundle und `git diff --check` grün. Die vier lokal hinterlegten
  API-Schlüssel haben jeweils null Treffer in versionierten Dateien.
- `d11eb4c` ist bewusst nur lokal committed. Ein Push könnte den Produktions-
  Auto-Updater auslösen; gepusht wird erst, wenn der SSH-Zugang wiederhergestellt
  ist und der Rollout danach separat überwacht werden kann.

---

## 4. Das Mail-System (Stand 11.08.2026)

**Klassen:** `trade` / `swing_trade` (handelbar, Telegram-Spiegel), `watch` (Opt-in),
`info` (Passwort, Test, Reports). Betreff-Präfixe: 🚨 JETZT (nur Intraday-`trade`) /
🚨 SWING (`swing_trade`, seit 31.07. ohne „JETZT") / 👁️ WATCH.

**Empfänger-Logik (`modules/auth.get_email_alert_recipients`):**
Plan hat E-Mail-Alerts → `email_alerts_enabled` → Watch-Opt-in (nur watch-Klasse) →
Narrative-Frequenz (nur narrative_pulse) → Trade-Horizont (swing/intraday/both) →
**Mail-Kanal** (seit 28.07.). Betreiber-Fallback (`ALERT_EMAIL`) bekommt alle Klassen,
Kanal-Opt-out greift aber auch dort (28.07.).

**Persönliche Positionen (seit 08.08.):** Ein Nutzer kann ein konkretes versendetes
Setup mit tatsächlicher Richtung, Fill und Stückzahl markieren. Folge- und
Stop-Mails lassen sich wahlweise auf diese Positionen begrenzen oder wie bisher
für alle Signale empfangen. Erstsignal-Mails und der globale Forward-Track-Record
bleiben vollständig. Dedupe/Retry sind empfängerbezogen, damit persönliche Filter
keinen anderen Empfänger blockieren.

**Kanäle:** `stocks_swing`, `stocks_intraday`, `crypto`, `biotech`, `bear`,
`new_listing` und seit 29.07. **`stocks_premarket`** — der Pre-Market-Radar
(7:00–9:25 ET, eigener Betreff, eigener `_pm`-Cooldown-Namespace, Warnhinweis;
Default an, pro User abschaltbar). Die Regular-Swing-Mail nach Open wird durch
eine PM-Mail desselben Tickers **nicht** verbraucht (getrennte Namespaces).

**Pre-Market-Gates (29.07.):** Scan-Seite ersetzt RVOL durch absolute PM-Liquidität
(≥ $500k), Spread-Guard (≤ 7 %) und ATR-Extensions-Decke (≤ 3,0); Mail-Seite verlangt
Score ≥ 85 + Top-Grade + valide Level über `_classify_premarket_candidate`.

**Qualitätsgates vor jeder Aktien-Swing-Mail** (Kette, jeder Grund = Zeile raus):
Grade/Score/RVOL → Common-Stock-Guard → 4H-Execution-State → **Volatilitätsbudget (neu)** →
Momentum-spezifisch (Breakout-Typ, Frische, Continuation, Fakeout, Wick, RVOL-Floor,
Late-Session, TP1-schon-berührt, Spike-Rejection, ≥ 8 %-Move-Chase) → Swing-Regelgründe
(≥ 12 %/≥ 8 %+RVOL, Fading, Highs-Halten, Gap-Gates, Tagesmove-ATR,
Pre-Market-Gap, Top-Entry-Orts-Gate (29.07.), **Mehrtages-ATR ≥ 5/≥ 7 +
Vortag-Anker (31.07., BHC)**) → Trade-Plan-Guard (valide Level,
natives Setup, R:R) →
Trade-Health → K-2a (Intraday-unbestätigt) → Cooldown/Dedupe (8 h/Ticker).

**Betreff-Präfixe (31.07.):** 🚨 JETZT nur noch Intraday-`trade` (15-Min-Frische);
`swing_trade` = **🚨 SWING** (mehrtägige Pläne, kein „JETZT"); 👁️ WATCH; ℹ️ Info.
**Zeitstempel (31.07.):** alle Mail-Bodies dual „UTC / MESZ" (`modules/mailtime.py`).

**Zustellsicherheit (01.–05.08.):** Allgemeine Mails werden über die persistente
SQLite-Outbox versendet. Der Worker claimt fällige Zeilen atomar, besitzt eine
Lease und verschiebt wiederholt fehlgeschlagene oder abgelaufene Zustellungen in
einen nachvollziehbaren Status. SMTP 465 fällt auf 587/STARTTLS zurück. Die
Outbox garantiert keine mathematische Exactly-once-Zustellung, verhindert aber den
früheren stillen Verlust bei temporären SMTP-/Prozessfehlern.

**Stop-Update-Mail:** Sobald ein getracktes, ursprünglich versendetes Signal +1 R
erreicht, wird `be_activated_at` persistiert. Die gesonderte Stop-Mail fordert auf,
den Rest auf Einstand zu sichern. Ihre Zustellung wird über `be_mail_sent_at` in
`signal_tracker.sqlite` bestätigt. Nicht bestätigte Aktivierungen werden bis zu
sieben Tage erneut geladen und erst nach erfolgreichem Mailversand quittiert. Das
allgemeine „Signal-Update" und das „Stop-Update" sind fachlich und technisch
getrennt.

**Regime vor Mailfreigabe:** Scanner-Score allein löst keine Mail aus. Asset-Guard,
Trade-Plan, Timing/Chase/Fakeout, Liquidität, Datenfrische und Markt-/Performance-
Regime müssen zusammenpassen. RED unterdrückt neue Swing-Käufe bzw. führt sie nur
als Shadow-Beobachtung; YELLOW verschärft Score und Zeilenzahl. Der Tracker misst
auch blockierte, grundsätzlich qualifizierte Shadow-Signale, damit die Kosten der
Gates später belegbar sind.

**Identität:** Aktienmails führen Ticker und Unternehmensname. Bei fehlender oder
unklarer Common-Stock-Referenz darf der Name nicht aus einem ähnlich klingenden
Symbol geraten werden; Asset-Typ und Ticker bleiben die verbindliche Identität.

---

## 5. Signal-Qualität und statistische Ehrlichkeit

- **Kohorten sind explizit:** `created_in_window` beantwortet, welche Signale im
  Berichtsfenster entstanden; `fully_observed` bewertet nur Signale, deren gesamtes
  Beobachtungsfenster abgeschlossen ist. Offene junge Signale werden weder als
  Treffer noch als Verlust in eine abgeschlossene Trefferquote gedrückt.
- **Zwei R-Semantiken:** Level-R (`avg_r`, geometrisch) und Managed-R 50/50
  (`avg_r_managed_50_50`, befolgbares Management: 50 % bei TP1, Rest bis
  TP2/Stop/BE). Beide in Tracker, UI, Wochenreport; maschinenlesbar in
  `summary["r_semantics"]`. Ein TP1-Treffer ist nicht automatisch ein voller
  Gewinner und ein späterer BE-Exit des Reststücks wird entsprechend gewichtet.
- **Wilson-95 %-KI** um jede Hit-Rate; **`sample_reliable`** ab 30 entschiedenen Signalen;
  UI-Warnbanner bei kleiner Stichprobe.
- **Scanner-Verdikt** (behalten/beobachten/abschalten) aus KI-Untergrenze vs. Breakeven;
  zentral in `signal_tracker`, mit Alarm im Wochenreport.
- **Regime-/Shadow-Nachweis:** Mailfreigaben und blockierte qualifizierte Setups
  werden getrennt getrackt. Erst ein ausreichend großer Forward-Datensatz darf
  entscheiden, ob ein Gate verbessert oder nur gute Trades verhindert hat.
- **Betriebsmetriken sind keine Performance:** Scannerlauf, Cache-Frische,
  Mail-Zustellung und Trading-Ergebnis werden separat berichtet. „System gesund"
  bedeutet nicht „Strategie profitabel" und ein hoher Setup-Score ist keine
  Eintrittswahrscheinlichkeit in Prozent.
- **Backtest-Kausalität:** Entry erst nach Signalentstehung; strategieabhängige
  Haltedauer, reale Kosten, Gap-through-Stop und Same-Bar-Ambiguität werden
  berücksichtigt. Wo Tages-OHLC die Reihenfolge von Stop und Ziel nicht beweist,
  wird konservativ bzw. mit Ergebnisgrenzen berichtet, niemals mit erfundener
  Intrabar-Chronologie.
- **Server-Verifikation:** `scripts/smoke_signal_performance.py` prüft die Felder live
  gegen `signal_tracker.sqlite`; `scripts/preview_weekly_report.py` rendert den Report
  ohne Versand; `scripts/signal_performance_breakdown.py` (29.07.) bricht dieselbe
  Metrik auf **Scanner × Kalendermonat** herunter (Regime-/Stichproben-Analyse);
  `scripts/websocket_benefit_analysis.py` (29.07.) misst die **Zeit-Sensitivität**
  (TP1-/Stop-Zeiten ab Mail, Extension zum Mail-Zeitpunkt in ATR, T-10/T-15-Preisvorteil
  samt Stop-Gegencheck) als Datengrundlage für die Phase-2-Entscheidung (WebSocket).

---

## 6. Testlandschaft

**1501 Tests, alle gruen** (11.08.2026; Laufzeit 71,92 s). Zusaetzlich ist
`scripts/verify_frontend_bundle.py` gruen; Quell- und Bundle-Hash stimmen ueberein.
Der Stand umfasst Rechenkern, Scanner-/Mail-Vertraege, Scheduler-Recovery,
Tracker-Kohorten, Regime-Filter, ORB-Zielplaene, Aktienidentitaet,
persoenliche Positionen, Paper-AutoTrader-Schutz und ausfallsichere Stop-Updates.
Wichtige Suiten:

| Datei | Deckt ab |
|---|---|
| `test_math_invariants.py` | Rechenkern gegen Lehrbuch-Referenzen |
| `test_tracker_calibration.py` | Managed-R, Wilson-KI, sample_reliable |
| `test_exit_efficiency.py` (30.07.) | Giveback-Messung, BE-/50-50-Simulation |
| `test_be_activation.py` | BE-Trigger, persistente Aktivierung, offene Zustellung, Retry und Versand-Acknowledge |
| `test_mail_outbox.py` | Persistente Mail-Outbox: Claim/Lease, Retry, Ablauf und Dead Letter |
| `test_email_dedupe_atomic.py` | Atomisches Dedupe und getrennte Namespaces je Mailklasse |
| `test_regime_filter.py` | Persistenter Markt-/Performance-Breaker, RED/YELLOW-Gates und Erholung |
| `test_orb_target_plan.py`, `test_orb_result_sorting.py` | ORB-Zielgeometrie, Mindest-R:R, finaler Handelsstatus und Sortierung |
| `test_stock_company_identity.py` | Common-Stock-Identität sowie Firmennamen in Aktien-Signalen |
| `test_stuck_scan_watchdog.py` (30.07.) | Scan-Wächter: Hänge-Mail, Hartdeckel-Reset, bg-Herzschlag, Entwarnungs-Mails, **Event-Log** |
| `test_watchdog_log.py` (30.07.) | JSONL-Event-Log: Roundtrip, Filter, Rotation, Summarize, Nie-werfen |
| `test_rates_block.py` (30.07.) | Zins-Block: FRED-Parsing, Regime-Grenzen, Scoring-Invariante, rates_json |
| `test_email_alert_audit.py` | Alert-Gates, Swing-Regeln, Chase-Gates (28.07.) |
| `test_mail_class_api.py` | Mail-Klassen, ATR-Annotation, Kanal-Versand (28.07.) |
| `test_commerce_hardening.py` | Auth, Billing, Kanal-Settings (28.07.) |
| `test_cluster_warning_mail.py` | Klumpenrisiko, Sweep-End-to-End |
| `test_stock_execution_regressions.py`, `test_stock_swing_4h_rejection.py` | 4H-Execution-Gates |
| `test_premarket_radar.py` (29.07.) | PM-Fenster, RVOL-Proxy, PM-Gates, Opening-Takt, PM-Mail E2E |

**Regeln:** Nie ohne `--basetemp=tmp/pytest_audit` (Sandbox). Produktivcode-Änderungen
erst nach voller Suite pushen. Keine Abhängigkeit vom echten Wirtschaftskalender in
Tests (Market-Context mocken — 29.07., FOMC-Lehre) und keine Abhängigkeit von der
echten Uhrzeit/Session (`_premarket_window_active` mocken — 30.07., PM-Fenster-Lehre).
Suite-Stand-Historie:
985 (21.07.) → 1101 (24.07.) → 1104 (28.07. ATR-Annotation) → 1114 (28.07. Chase-Gates) →
1120 (28.07. Mail-Kanäle) → 1126 (29.07. Orts-Gate) → 1130 (29.07. UX-Paket) →
1147 (29.07. PM-Radar) → 1160 (30.07. Exit-Effizienz) → 1171 (30.07. BE-Trigger) →
1182 (30.07. Scan-Wächter) → 1187 (30.07. BE im Wochenreport) → 1189 (30.07. BE im Dashboard) →
1213 (30.07. Zins-Block) → 1218 (30.07. Entwarnungs-Mails, alle grün) →
1223 (30.07. Wächter-Throttle + Budget + zeitrobuste Suite, alle grün) →
1242 (30.07. Wächter-Event-Log + Wochenreport-Sektion, alle grün;
31.07. Zweitkalibrierung Intervall 60 Min / Budget 35 Min, alle grün) →
1259 (31.07. Smart-Money-Radar Info-Block + Nie-Trigger-Guard, alle grün) →
1266 (31.07. Monster-Volumen-Aktien-Sektion mit eigener Baseline, alle grün) →
1272 (31.07. SEC-Insider-Trades-Sektion (Form 4), alle grün) →
1277 (31.07. Insider-Cluster-Detektor, alle grün) →
1286 (31.07. ℹ️-Cluster-Mail mit Kompositions-Dedupe, alle grün) →
1300 (31.07. BHC: Mehrtages-Chase-Gates + Vortag-Anker, SWING-Betreff ohne „JETZT",
duale Mail-Zeitstempel `test_mailtime.py`, alle grün) →
1310 (31.07. Tiefen-Audit: Short-Spiegelung, Reasons-Registry-Guard, Zeitstempel-Guard,
Chase-Gate-Backtest-Skript, alle grün) →
1331 (31.07. Shadow-Tracking: Whitelist-Helper, Registry-Guard, Record/Dedupe-Isolation,
Summary-Filter, shadow_summary, Eval-/Mail-Vertrag (`mail_class` in transitions/be_activations),
Wochenreport-Sektion, Pipeline-Integration, alle grün) →
1400 (05.08. Regime-/Outbox-Härtung, Scanner-Recovery, korrekte Tracker-Kohorten und
50/50-Payoffs, ORB-Zielpläne/Sortierung, Unternehmensidentität, atomisches Dedupe
und persistente Stop-Update-Retries; alle grün) →
1405 (05.08. persönliche Positionen, empfängerbezogene Folge-/Stop-Mails,
per-Empfänger-Dedupe und isolierter Retry; alle grün) →
1501 (11.08. Übergabestand: exakte Signalidentität, Paper-AutoTrader-Schutz,
Business-Quality/Timing-Trennung, verschärfte Signalmathematik und
Post-Pump-Short-Gates; volle Suite und Frontend-Bundle-Prüfung grün).

---

## 7. Offene Punkte

| Prio | Punkt | Kontext |
|---|---|---|
| 1 | **SSH-Zugang wiederherstellen und aktuellen `main`-Stand produktiv verifizieren:** autorisierten privaten Schlüssel sicher auf den neuen PC übertragen; danach Server-HEAD, produktive Services, `/api/health`, `/api/system-health`, Scheduler-/Mail-Logs und tatsächlich ausgelieferte Frontend-Datei prüfen. GitHub-Push allein reicht nicht. | Deployment-Vertrag, 3.30/3.31/3.40; `HANDOFF_PC_WECHSEL_2026-08-11.md` |
| 2 | **Mail-Zustellung beobachten:** Outbox-Zahlen (`pending`, `retry`, `dead_letter`) und offene `be_mail_sent_at`-Aktivierungen prüfen; Testmail plus absichtlich simulierten temporären Fehler nachvollziehen. | 3.26/3.30, Abschnitt 4 |
| 3 | **Forward-Kalibrierung statt Bauchgefühl:** je Scanner und Regime mindestens 30 vollständig beobachtete Signale sammeln; freigegebene Signale gegen Shadow-Kohorte mit Hit-Rate, Wilson-KI, Level-R und Managed-R vergleichen. Erst dann Schwellen verändern. | Abschnitt 5 |
| 4 | **PM-/Swing-/Orts-Gates produktiv messen:** Reject-Gründe und Ergebnisdaten nach mehreren Handelstagen auswerten. Lockerung nur, wenn verpasster Erwartungswert statistisch höher als vermiedener Verlust ist. | 3.7, Commits `cdeff7e`/`da6c4be` |
| 5 | **Stop-Management nachweisen:** kommende Wochenreports auf Aktivierungen, zugestellte Stop-Mails, bewahrte Verlierer und realisiertes Managed-R prüfen; theoretische +R-Annahme nicht als erreicht ausgeben. | 3.8/3.9/3.30 |
| 6 | **Regime- und Zinssegmentierung auswerten:** ab ausreichend großen Zellen Marktregime und `rates_json` gegen Forward-Ergebnisse prüfen; zusätzliche Gates nur bei belastbarem Delta. | 3.12/3.26 |
| 7 | **Auto-Update überwachen:** Cron-/Deploy-Log muss Pull, sicheren Testlauf, Service-Neustart und Health-Nachweis enthalten; bei Fehlern kein stilles Teil-Deployment. | `deploy/auto_update.sh`, `deploy/safe_deploy.sh` |
| 8 | **Server-Grundpflege:** ausstehende Ubuntu-Updates/Reboot in einem Wartungsfenster über `bash deploy/os_maintenance.sh`; danach vollständige Service- und Health-Verifikation. | Infrastruktur |
| 9 | **IBKR-Paper-Soak nachweisen:** TWS/IB Gateway mit DU-Konto über mehrere Sessions testen (Disconnect/Reconnect, Partial Fill, Reject, Gap, Stop/TP-OCA, Restart-Reconciliation, Kill-Switch). Erst nach dokumentierter Paper-Freigabe über Live-Automation neu entscheiden; aktuell bleibt Live blockiert. | Broker-Sicherheit, 3.31/3.33 |
| 10 | **EN/DE-Sprach-Toggle:** UI bleibt derzeit bewusst deutsch; nur als eigenes Produktfeature umsetzen, nicht als Launch-Blocker. | UX |

---

## 8. Dokumentenlandkarte

| Dokument | Inhalt |
|---|---|
| `PROJEKTBIBEL.md` | **Normative Hauptquelle:** unverhandelbare Fach-, Mathematik-, Daten-, Mail-, Broker- und Deploymentregeln |
| `PROJEKTHANDBUCH.md` | Chronologischer Ist-Stand, Architektur, Fixhistorie, Testlandschaft und offene Nachweise |
| `HANDOFF_PC_WECHSEL_2026-08-11.md` | Reproduzierbare Übergabe auf einen neuen PC: Clone, Secrets, Tests, Git, Deploy und Produktionsprüfung |
| `PROJEKTHANDBUCH_CLAUDE.md` | Ausführliches Vorgänger-Handbuch (Stand 21.07.): Produktvision, Signalmodell, Invarianten, Architekturdetails |
| `AUDIT_ABSCHLUSSBERICHT_2026-07-24.md` | Tagesdokumentation Vollaudit 24.07. |
| `AUDIT_MATHEMATIK_2026-07-24.md` | Rechenkern-Prüfung im Detail |
| `AUDIT_TIEFENAUDIT_TRADINGLOGIK_2026-07-24.md` | Befunde T1/T2 im Detail |
| `AUDIT_GESAMT_2026-07-24.md` | Gesamtaudit + Legacy-Bereinigung |
| `AUDIT_2026-08-01.md` | Performance-Einbruch, Mail-Outbox, Regime-Filter und statistische Konsequenzen |
| `AUDIT_SCANNER_VOLLAUDIT_2026-07-19.md` / `AUDIT_SCANNER_FIXSTATUS_2026-07-20.md` | Scanner-Vollaudit + Fixstatus |
| `CODEX_HANDOFF_NACHAUDIT_2026-07-21.md` | Übergabe Nachaudit (Codex ↔ Claude) |
| `AUDIT_PENNY_STOCK_SCANNER_2026-07-22.md` | Penny-Scanner-Audit |
| `COMMERCIAL_LAUNCH_CHECKLIST.md` | Kommerzielle Startbereitschaft |
| `deploy/DEPLOY_ANLEITUNG.md` | Deployment-Details (nginx, TLS, systemd) |
| `deploy/SERVER_WARTUNG.md` (30.07.) | Wartungs-Runbook: Update-Fenster, Verifikation, Gesundheitscheck |
| `deploy/auto_update.sh` (30.07.) | Cron-gesteuertes Selbst-Update des Servers (alle 10 min) |
| `deploy/health_check.sh` (30.07.) | Ein-Befehl-Gesundheitscheck (Ampel, Exit-Code monitoring-tauglich) |
| `archive/` | Alt-Versionen: Legacy-Frontend, Streamlit, alte Audits |

---

## 9. Arbeitsregeln, die sich bewährt haben

1. **Erst lesen, dann behaupten** — jede Diagnose gegen den echten Code und echte Zahlen verifizieren.
2. **Klein schneiden, voll testen, pushen** — jede Änderung: Suite komplett grün, dann sofort auf `main`.
3. **Server-Anweisung immer mitgeben** — Pull + Restart der betroffenen Services + Verifikationsbefehl.
4. **Ehrlichkeit vor Tempo** — ungeprüfte Annahmen als solche markieren; Schwellen als Kalibrierung kennzeichnen.
5. **Bestandsverhalten schützen** — neue Gates greifen nur mit vorhandenen Metadaten; Defaults ändern nichts für Bestandsnutzer.
6. **Nie auf Produktion entwickeln oder committen** — lokal ändern, testen und nach `main` pushen; der Server zieht nur den geprüften Commit.
7. **Secrets und Zustandsdaten getrennt halten** — `.env`, `data_cache/`, Tracker-/Outbox-DB und Brokerzustand weder committen noch bei einer Migration überschreiben.
8. **Technik, Produktion und Performance getrennt abnehmen** — grüne Tests beweisen Codekonsistenz, Health beweist Erreichbarkeit, Forward-Daten beweisen erst die reale Signalgüte.

---

## 10. Nachtrag 13.08.2026 - Signal-Pipeline, Mailforensik und Rollout-Grenze

### 10.1 Warum der bisherige Track Record nicht belastbar genug war

Die forensische Sichtung der Signal-Update-Mails vom 06.-12.08.2026 ergab 16
Digests mit 45 Ereigniszeilen. Nach Deduplizierung identischer Plangeometrien
blieben 41 Plaene: 33 terminale Ereignisse und 8 Faelle mit TP1, deren Rest noch
offen war. In den 33 terminalen Ereignissen standen 12 positive und 21 negative
Ausgaenge, +21,70R und -23,81R, damit netto -2,11R.

Diese Zahl beschreibt nur den beobachteten Update-Ereignisstrom. Sie ist keine
vollstaendige Created- oder Matured-Kohorte der in diesem Zeitraum versendeten
Entries: den Update-Mails fehlten stabile Signal-ID und urspruengliche
Signalzeit; aeltere Plaene koennen enthalten sein, waehrend `NO_FILL`,
`UNTRACKED`, offene Signale ohne TP1 und ausgefallene Zustellungen fehlen
koennen. Auch die 12:21-Aufteilung ist deshalb keine belastbare kuenftige
Trefferwahrscheinlichkeit.

Vier Extremfaelle wurden gegen Marktdaten und vorhandene Mailbelege neu
eingeordnet:

| Fall | Rohwert | Auditwert | Grenze/Ursache |
|---|---:|---:|---|
| ONON | -4,65R | -3,94R | fehlendes echtes Daily-Open war durch laufenden Close ersetzt worden |
| ECO | -1,27R | -1,00R | echtes Open lag ueber Stop; normale spaetere Stopberuehrung |
| CBLL | -1,57R | `NO_FILL` | Planpreis war vor Mail veraltet; Markt lag bereits unter Stop |
| AURA | -1,38R | konservativ -1,38R; wahrscheinlich -1,00R | gleiche Missing-Open-Signatur, aber Erstmailzeit nicht eindeutig belegt |

Konservativ ergeben diese vier Faelle -6,32R statt -8,87R: Verbesserung um
+2,55R, drei Verluste plus ein `NO_FILL` statt vier Verlusten. Die wahrscheinliche
AURA-Variante ergaebe +2,93R Verbesserung, bleibt aber ausdruecklich
unbestaetigt. Die Korrektur erklaert einen Teil der extremen Ausschlaege; sie
wandelt den negativen Rohstrom nicht in einen Profitabilitaetsnachweis um.

### 10.2 Neuer kausaler Mail-/Fill-Vertrag

Der Produktionspfad muss Signalzeit, Datenzeit, Scanzeit, vorbereiteten
Delivery-Intent, SMTP-Akzeptanzzeit und Fillzeit getrennt halten. Eine Quote
unmittelbar vor Versand validiert Preis, Spread, Session und Marktpfad, ist aber
kein Fill. Tracking beginnt nur mit nachgewiesener DATA-Akzeptanz fuer die
konkrete Empfaengerkohorte oder einem dokumentierten Brokerfill.

Ohne Brokerfill ist der First Executable Price die erste realistisch handelbare
Beobachtung ab diesem Start: Long zum Ask, Short zum Bid, inklusive Spread,
Slippage und Kosten. Daily-Auswertung braucht ein echtes Open;
laufende Bars oder Close-als-Open duerfen weder Gap-Fill noch terminalen Exit
erzeugen. Fehlt ein lueckenloser Post-Alert-Pfad, bleibt der Datensatz `OPEN`
oder `UNTRACKED`; eine guenstige oder unguenstige Intrabar-Reihenfolge wird nicht
erfunden.

### 10.3 Delivery-Intent, Teilannahme und Legacy-Kohorten

Vor SMTP wird ein stabiler Intent aus Signalidentitaet, Mailklasse und
vorgesehener Empfaengerliste persistiert. Nur der atomare Owner von
`PREPARED -> ATTEMPTED` darf DATA senden. Jede tatsaechliche Annahme wird sofort
mit pseudonymisiertem Empfaenger und Akzeptanzzeit journalisiert.

- Unbekannter DATA-Ausgang: Quarantaene, kein automatischer Neuversand.
- Teilannahme: nur die in diesem Versuch akzeptierten Empfaenger bilden die
  kausale Signal-Kohorte; ein spaeterer Retry wird nicht in diesen Start gemischt.
- Folge-, Exit- und Break-even-Mails: nur Ursprungskohorte geschnitten mit
  aktuellem Opt-in; kein Exit an neue Abonnenten ohne Entry.
- Altes offenes Signal ohne Empfaengerledger:
  `legacy_open_cohort_unknown`, keine geratenen Empfaenger, degradierter Health-
  Status und manuelle Behandlung.
- Terminale und Break-even-Updates bleiben durable pending, bis ihre Zustellung
  bestaetigt wurde.

### 10.4 Ehrliche Kohorten, Managed BE und Kalibrierung

Created- und Matured-in-window, gefuellt, entschieden, `NO_FILL`, `OPEN`,
`UNTRACKED` und unaufgeloest werden getrennt berichtet. Hit-Rate, Wilson-
Intervall und Profit Factor verwenden nur gefuellte und entschiedene Signale.
Level-R, 50/50-Managed-R und 50/50-plus-Break-even-R sind getrennte Semantiken.
Ist die BE-Anweisung nicht nachweisbar zugestellt, bleibt
`managed_be_unresolved`; der Fall wird weder als 0R gewertet noch aus den
Verlusten entfernt.

Eine Scannerfreigabe darf nur aus der gemeinsamen Zelle Scanner x Richtung x
Horizont x exogenem, zum Signalzeitpunkt persistiertem Marktregime entstehen.
Je Zelle sind mindestens 30 vollstaendig beobachtete Entscheidungen, ein
Wilson-95-Prozent-Intervall und null unresolved Kontrollfaelle erforderlich.
Scannerweite Mischwerte duerfen schwache Richtungs-, Horizont- oder Regimezellen
nicht kaschieren.

### 10.5 Repair-Runbook und Produktionsgates

Historische Korrekturen laufen ausschliesslich nach
`deploy/SIGNAL_TRACKER_REPAIR.md` mit
`scripts/signal_tracker_repair.py`: read-only Kandidateninspektion, extern
belegtes Manifest, exakter Before-State, Dry-Run, gestoppte API- und BG-Writer,
konsistentes Backup, `BEGIN IMMEDIATE`-Recheck, append-only Audit und
Nachverifikation. Bei AURA wird ohne eindeutigen Erstmail-/DB-Nachweis nichts
angewendet; generell werden keine Werte geraten.

Vor einem Rollout sind volle lokale Testsuite, Python-Compile, neu gebautes und
verifiziertes Frontend-Bundle, `git diff --check`, Secret-Diff-Scan und Push
zwingend. Produktion braucht zusaetzlich Backup von Tracker, Zustelljournal und
Outbox, bei Repair eine Vier-Augen-Pruefung sowie denselben Commit in
Server-HEAD, API-Revision, Bundle-Hash, Services und Health. Realtime-Quote-
Berechtigung und Quote-Recency muessen produktiv belegt sein; offene Legacy-
Kohorten und unbekannte SMTP-Ausgaenge muessen null oder dokumentiert manuell
behandelt sein.

Der ausfuehrliche Beleg steht in `AUDIT_SIGNAL_PIPELINE_2026-08-13.md`. Die
lokale Implementierung ist kein Nachweis fuer einen Server-Rollout und weder
historische Korrektur noch gruene Tests beweisen Profitabilitaet. Diese kann nur
eine neue kausal vollstaendige Forward-Kohorte zeigen.

### 10.6 Mobile-/Store-Grenze

Der aktuelle Dateibaum enthaelt keine nachgewiesene native iOS-/Android-Huelle,
kein Xcode-/Gradle-Projekt und keine Store-Signing-Konfiguration. Die vorhandene
responsive Web-App ist deshalb nicht allein durch einen gruenen Frontend-Build
App-Store- oder Google-Play-bereit. Vor einer Store-Einreichung braucht es eine
bewusste native Architektur, reale iOS-/Android-Geraetetests, macOS/Xcode fuer
iOS, Android-SDK/JDK, Release-Signing, Store-Produkte/IAP statt ausschliesslich
Stripe-Webcheckout, Datenschutz-/Trackingdeklarationen, Store-Texte und die
jeweiligen Entwicklerkonten. Kein Codex-Plugin ersetzt diese Nachweise.

### 10.7 Verifizierter lokaler Endstand dieses Audits

Der finale Arbeitsbaum wurde nach allen Reparaturen und zwei unabhaengigen
Folgeaudit-Runden erneut vollstaendig geprueft:

- 1768/1768 Pytest-Faelle bestanden (isolierte DB-/Outbox-/SMTP-Umgebung),
- 47 geaenderte/neue Python-Dateien kompiliert,
- Bundle `a6c74874a925` aus der Quelle neu gebaut und verifiziert,
- `node --check`, `git diff --check` und Secret-Musterscan gruen,
- lokale reale Outbox: 0 aktive Eintraege,
- finaler unabhaengiger Audit: P0 0, P1 0, P2 0.

Die Browser-QA bestaetigte bei 1440 px und 390 px keinen horizontalen Overflow
und keine Konsolenfehler. Die Landingpage entfernt unbelegte Profit-/Social-
Proof-Aussagen; Paperzahlen sind als illustrative Demo ohne echte Ergebnisse
gekennzeichnet. Das externe Smart-Money-Skript ist CSP-kompatibel und zeigte im
HTML-Injection-Test keine DOM-Ausfuehrung. Als nicht-blockierender P3-Hinweis
bleibt die allgemeine Tailwind-Runtime-Warnung des lokal vendorten Skripts.

Aktien-Reminder verwenden nur frische, abgeschlossene 5-Minuten-Kerzen in einer
ausfuehrbaren US-Session und benennen Preis, UTC-Kerzenschluss und die Grenze
`kein Live-Bid/Ask` explizit. Stale Daten fuehren weder zu Mail noch Tracking;
der Reminder bleibt fuer einen spaeteren sicheren Retry aktiv.

Der Server wurde mit diesem Stand nicht als aktualisiert nachgewiesen. Deshalb
bleiben Deployment, produktive Realtime-Quote-Berechtigung, Legacy-Kohorten,
Forward-Performance, IBKR-Paper-Soak und App-Store-/Google-Play-Freigabe eigene
offene Nachweise.

### 10.8 Git- und Live-Status nach lokaler Abnahme

Der Implementierungscommit ist lokal `e9cba06`. Push nach GitHub war auf dem
neuen PC nicht moeglich, weil weder HTTPS-Schreib-Credential noch `gh`-Login
oder SSH-Schluessel vorhanden sind; `origin/main` blieb auf `9987c7f`.

Der oeffentliche Health-Endpunkt antwortete HTTP 200, meldete aber weiterhin
Revision `de4e7cfac0ec` und Bundle `c0b3b13a6c86`; das Frontend lieferte noch
die alte Landing-Copy. SSH war mangels autorisiertem privatem Schluessel
gesperrt. Es erfolgte deshalb weder Deployment noch Produktions-Repair. Vor dem
Rollout muessen GitHub- und Serverzugang sicher wiederhergestellt, produktive
Datenbanken gesichert, Quote-Entitlement/Recency belegt und danach derselbe
Commit in Server-HEAD, API, Bundle, Services und Health nachgewiesen werden.

### 10.9 Lokale Audit-Remediation (21.-23.08.2026)

Der isolierte Worktree ergaenzt die folgende lokal getestete Schutzschicht. Die
Angaben sind Implementierungs- und Vollsuite-Nachweise; sie sind weder ein
Server-, Konto- noch ein Performancebeleg.

- **AS1 und Herkunft:** Prepared-Trade-Zeilen tragen eine stabile externe
  `AS1-[0-9A-F]{20}`-Referenz aus kanonischem Delivery-Intent/Plan. Reorder-
  Retries bleiben 1:1 zugeordnet; Kollision, Duplikat oder nachtraegliche
  Korruption blockiert den Intent vor SMTP. `origin_evidence` trennt
  Vorbereitung, SMTP-Akzeptanz, direkten Post-Send-Pfad und Shadow. Rohes
  Legacy-`NULL` bleibt in der DB erhalten und erscheint in Payloads nur als
  `legacy_origin_unknown`.
- **Snapshot, Kohorte und Receipts:** Der Intent bindet den vollstaendigen
  kanonischen Plansnapshot, Empfaengerkohorte und Einwilligungsstand. BE- und
  Terminal-Folgemails verlangen ein dauerhaftes exakt signalgebundenes Receipt;
  synthetische, nackte oder fremde Receipts bleiben fail-closed.
- **Ehrliche Folge-Mails:** Ref und belegter Ursprung werden auch nach Outbox-
  Reload gezeigt. UTC und MEZ/MESZ stammen aus genau einem Renderzeitpunkt;
  Replay rendert gebrandetes Outbox-HTML nicht neu. MFE-R ist Kursfortschritt,
  nicht Gewinn. TP1 bedeutet Kurszone erreicht bei offener Position, ohne
  behaupteten Teilverkauf; Level-R ist erst terminal final. BE senkt nur
  geplantes Preisrisiko, nicht Gap-, Slippage- oder Ausfuehrungsrisiko.
- **Zielreichweite:** Die ATR-Distanzen/Provenienz sind deskriptive
  Reichweiten-Telemetrie. Fehlende Daten bleiben unavailable, explizite Budgets
  sind kein Default und veraendern weder Trading-Health noch ORB, finale
  Revalidierung oder Mailfreigabe; sie sagen keine Trefferwahrscheinlichkeit
  aus.
- **Paper-Risiko:** Der separate SQLite Risk Store persistiert unveraenderliche
  Intents/Order-Mappings, append-only Fill-Evidenz je `exec_id` mit expliziter
  immutable Sequenz, gezaeunte Submit-Leases und atomare Risikoreservierungen.
  Konflikte, spaete Fills/Mappings oder unvollstaendige Evidenz bleiben
  fail-closed. `COMPLETE` benoetigt gueltige Lease/Fence und einen frischen
  vollstaendigen Broker-Snapshot ohne Position und ohne offene gemappte Order;
  Terminal-Evidenz, Outcome und Reservation werden atomar persistiert. Die
  Paper-Policy betraegt 0,75 % Gesamt- und Richtungsrisiko, 0,50 % je
  verifizierter Gruppe und drei vollstaendig belegte Verlustserien. Endgueltige
  Tick-Geometrie und Quantity werden vor Platzierung neu berechnet;
  Broker-Sichtbarkeit wird danach nur mit vollstaendiger Parent-/Stop-/Target-
  Geometrie, eindeutigen positiven `permId`s und aktiven Broker-Acks belegt.
  Provider-`None`, nicht iterierbare oder doppelte Snapshot-Zeilen sowie
  unvollstaendige Legacy-Geometrie bleiben fail-closed. Terminal offene Orders
  werden ueber Konto, Client, Contract, Order-ID, `permId`, Referenz und
  Geometrie gebunden und vor dem Evidenz-Hash kanonisch sortiert. Automatisches
  Stop-Nachziehen ist absichtlich gesperrt, bis eine dauerhaft gezaeunte,
  monotone Geometrie-Revision implementiert und separat abgenommen ist.
- **Frische und Crash-Sicherheit:** Re-Arm und unmittelbare Pre-Reservation
  verlangen frische kausale Orders-/Positions-/Fill-/Account-/PnL-Snapshots.
  Account/PnL nutzen eigene rohe Request-IDs und objektgebundene Listener;
  fremde, alte oder gecachte Events autorisieren nichts. Generation-Fencing,
  Kill-Cancel-Acks, OS-Prozess-Lock-Owner-Claims und Crash-Recovery verhindern
  Freigabe nur durch TTL/Lease-Ablauf. Limit-Risiko, Exposure und Cash werden in
  USD mit Worst-Fill-Preis auch fuer aktive oder pending Parents geprueft.
- **Batch und Grade:** Der Mailhinweis ist nur hypothetisches 1R je gueltigem
  Plan, nach Richtung und verifizierter Gruppe; keine Dollar-/Konto-Aussage und
  keine Suppression. Die Grade-Kalibrierung ist eine Reporting-Zelle aus
  Scanner/Grade/Richtung/Horizont/Regime, nur mit terminaler Origin-/Fill-
  Evidenz und 50/50+BE. Sie ist erst bei `n >= 30` und `unresolved = 0`
  belastbar, ist keine Wahrscheinlichkeit und kann weder Verdict noch Breaker
  aendern.
- **Tracker und UI:** MFE/MAE enden am belegten Exit; Open-Gaps haben Vorrang vor
  spaeteren Tagesextrema, unaufloesbare OHLC-Reihenfolgen bleiben unaufgeloest.
  Kontrollpopulationen und Breaker verwenden explizite Nenner und qualifizierte
  Origin-/Fill-Evidenz. Das Frontend ist oeffentlich Paper-only, mischt keine
  Metrikfamilien und zaehlt STOP/EXPIRED nach TP1 nicht positiv; CTA-/Scroll-/
  Boot-Vertraege sind automatisiert geprueft.

Fokussierte Nachweise: 439/439 Mail-/Tracker-, 385/385 Risiko-/Broker- und 24/24
Frontend-Vertragstests; die gemeinsame Paketregression umfasst 959/959 Tests.
Die Vollsuite bestand mit 2490 Tests und 4 Skips in 1066,94 Sekunden. Compile,
Bundle-Quelle `54bc2efa62cc`, Bundle-SHA-256
`d2e03be31a79983fc91f07a80795fd4ccc70be49dfed8d23a8c80d639b1b9bf9`,
JavaScript-Syntax, `git diff --check` und der Scope-/Secret-Scan (44 Dateien,
0/0 Treffer, `Mailarchiv/` ausgeschlossen) waren gruen. Die finalen
unabhaengigen Reviews meldeten P0/P1/P2 = 0/0/0.

Der 51-Mail-Ausschnitt zeigte 37 neue Plaene und 24 terminal/verfallen sichtbare
Trackerfaelle: 8 positiv, 16 negativ, Rohsumme -5,06R, Profit-Faktor 0,68; fuer
25 neue Plaene fehlte im Archiv jedes Folgeergebnis. Das ist kein Backtest,
keine vollstaendige Kohorte und kein Broker-PnL, deshalb wurden daraus keine
Trading-Schwellen abgeleitet. Server und Live-System blieben unveraendert; es
gab keinen Commit und keinen Push. Reale TWS/Gateway-/DU-Soaks, Deployment,
visueller Browser-Smoke der letzten UI-Aenderungen und jede Live-Freigabe
bleiben offen.
