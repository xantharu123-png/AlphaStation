# Alpha Station — Projekthandbuch

**Stand:** 29. Juli 2026 · **Endstand Code:** `2c66d4f` auf `main` · **Tests:** 1130 (alle grün)
**Repository:** `C:\Users\miros\Desktop\TradingBot` → GitHub `xantharu123-png/AlphaStation`
**Produktion:** `root@178.104.69.209`, `/home/tradingbot/app`

> Dieses Handbuch ist das Master-Dokument. Es ersetzt keine Detail-Lektüre, sondern
> verweist auf die Einzel-Dokumente (Abschnitt 8). Es trennt strikt zwischen
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

**Scanner-Landschaft:** Aktien-Strategie-Sweep (Momentum Breakout/Gap Momentum Long/Short),
BI Long/Short, Biotech, ORB (intraday-only), Bear, Crash-Monitor, Penny-Lifecycle,
Turtle, New Listing, Crypto Early Movers/Explosion/Strategie.

---

## 2. Architektur & Betrieb

| Komponente | Was | Wo |
|---|---|---|
| `api.py` | FastAPI-Backend: Scanner, Alerts, Auth, Versand, Scheduler (30-Min-Takt Strategie-Sweep) | Service `tradingbot-api` |
| `bg_service.py` | Hintergrunddienst: bi_long/bi_short/biotech (Mail-Ownership), Wochenreport | Service `tradingbot-bg` |
| `frontend/index.html` | React-SPA (statisch ausgeliefert) | über nginx/api |
| `modules/` | auth, signal_tracker, patterns, stock_execution, trade_levels, trade_health, email_dedupe u. a. | shared |
| `data_cache/` | Scan-Caches, `signal_tracker.sqlite` | Server |
| `scripts/` | Smoke-Tests, Report-Preview, Deploy-Helfer | Server |

**Scan-Ownership (Audit H-9):** api-owned: strategy_scan, crash_monitor, bear, orb,
new_listing, crypto. bg-owned: bi_long, bi_short, biotech (feste ET-Zeitfenster).
Override per `BG_SCAN_SET`.

**Deploy-Workflow (der einzige gültige Weg):**
1. Lokal entwickeln + volle Suite grün.
2. `git push origin HEAD:main` (Arbeitsbranch `nachaudit-fixes-2026-07-21`).
3. Auf dem Server: `cd /home/tradingbot/app && git pull`.
4. `systemctl restart tradingbot-api` (bei bg-Änderungen auch `tradingbot-bg`).
5. Verifizieren: `git log --oneline -1` + Logs (`journalctl -u tradingbot-api`).

**Lokale Tests (Windows):**
```bash
.codex_test_venv/Scripts/python.exe -m pytest -q --tb=line -p no:cacheprovider --basetemp=tmp/pytest_audit
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
  an TP1 50 % verkaufen, Rest risikofrei" (Regel B). Gleiche Sicherungen wie bei
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

---

## 4. Das Mail-System (Stand 29.07., abends)

**Klassen:** `trade` / `swing_trade` (handelbar, Telegram-Spiegel), `watch` (Opt-in),
`info` (Passwort, Test, Reports). Betreff-Präfixe: 🚨 JETZT SWING / 👁️ WATCH.

**Empfänger-Logik (`modules/auth.get_email_alert_recipients`):**
Plan hat E-Mail-Alerts → `email_alerts_enabled` → Watch-Opt-in (nur watch-Klasse) →
Narrative-Frequenz (nur narrative_pulse) → Trade-Horizont (swing/intraday/both) →
**Mail-Kanal** (seit 28.07.). Betreiber-Fallback (`ALERT_EMAIL`) bekommt alle Klassen,
Kanal-Opt-out greift aber auch dort (28.07.).

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
Pre-Market-Gap, **Top-Entry-Orts-Gate (29.07.)**) → Trade-Plan-Guard (valide Level,
natives Setup, R:R) →
Trade-Health → K-2a (Intraday-unbestätigt) → Cooldown/Dedupe (8 h/Ticker).

---

## 5. Signal-Qualität und statistische Ehrlichkeit

- **Zwei R-Semantiken:** Level-R (`avg_r`, geometrisch) und Managed-R 50/50
  (`avg_r_managed_50_50`, befolgbares Management: TP1 = halb raus). Beide in
  Tracker, UI, Wochenreport; maschinenlesbar in `summary["r_semantics"]`.
- **Wilson-95 %-KI** um jede Hit-Rate; **`sample_reliable`** ab 30 entschiedenen Signalen;
  UI-Warnbanner bei kleiner Stichprobe.
- **Scanner-Verdikt** (behalten/beobachten/abschalten) aus KI-Untergrenze vs. Breakeven;
  zentral in `signal_tracker`, mit Alarm im Wochenreport.
- **Server-Verifikation:** `scripts/smoke_signal_performance.py` prüft die Felder live
  gegen `signal_tracker.sqlite`; `scripts/preview_weekly_report.py` rendert den Report
  ohne Versand; `scripts/signal_performance_breakdown.py` (29.07.) bricht dieselbe
  Metrik auf **Scanner × Kalendermonat** herunter (Regime-/Stichproben-Analyse);
  `scripts/websocket_benefit_analysis.py` (29.07.) misst die **Zeit-Sensitivität**
  (TP1-/Stop-Zeiten ab Mail, Extension zum Mail-Zeitpunkt in ATR, T-10/T-15-Preisvorteil
  samt Stop-Gegencheck) als Datengrundlage für die Phase-2-Entscheidung (WebSocket).

---

## 6. Testlandschaft

**1242 Tests, alle grün** (30.07.) — seit dem PM-Fenster-Mock (3.14) erstmals
zu jeder Tageszeit. Wichtige Suiten:

| Datei | Deckt ab |
|---|---|
| `test_math_invariants.py` | Rechenkern gegen Lehrbuch-Referenzen |
| `test_tracker_calibration.py` | Managed-R, Wilson-KI, sample_reliable |
| `test_exit_efficiency.py` (30.07.) | Giveback-Messung, BE-/50-50-Simulation |
| `test_be_activation.py` (30.07.) | BE-Trigger: be_activated_at, r_realized_be, Stop-Update-Mail |
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
31.07. Zweitkalibrierung Intervall 60 Min / Budget 35 Min, alle grün).

---

## 7. Offene Punkte

| Prio | Punkt | Kontext |
|---|---|---|
| 1 | **PM-Radar live beobachten** — Schwellen ($500k PM-Liquidität, 7 % Spread, 3,0 ATR Decke, Score 85) sind Erstkalibrierung; nach einigen Handelstagen an Server-Logs prüfen (`premarket_*` Reject-Gründe in der Scan-Diagnostik) und PM-Signale im Tracker gegen Regular-Signale vergleichen. Bei Spam → Schwelle hoch / Kanal aus; bei zu wenig Treffern → Beleglage prüfen | 3.7 |
| 2 | **Schwellen-Verifikation** — 2,5/3,5 ATR, 2,0 %-Budget und 2,0-ATR-Orts-Gate sind an 7 Produktivfällen kalibriert; an Server-Logs prüfen (`swing_day_move_*`, `swing_top_entry_*`, `top_entry_*`, `bottom_entry_*`, `low_volatility`). Falls `top_entry`/`bottom_entry` nie auftauchen: Crypto-Rows ohne `day_high`/`day_low` → Anreicherung nachbauen | Commits `cdeff7e`, `da6c4be` |
| 3 | **Retest-Plan-Mail** — Chase-Gates unterdrücken Zeilen aktuell ganz; eine WATCH-Mail mit konkretem Retest-Entry (statt Market-Entry) wäre ein eigenes Feature | Betreiber will keine Watch-Mails — nur falls er es sich anders überlegt |
| 4 | **Gate-Wirkung auswerten** — nach 1–2 Wochen Produktivlauf `scripts/signal_performance_breakdown.py` laufen lassen: Hit-Rate/R je Monat vor vs. nach den Chase-/Orts-Gates vergleichen | Commit `da6c4be` |
| 5 | ~~Exit-Effizienz — Regel abgeleitet~~ — **IMPLEMENTIERT (3.9):** BE-Trigger live (be_activated_at, r_realized_be, Stop-Update-Mail) **+ Wochenreport-Nachweis** („Ø R BE"-Spalte, Ergebnis-Box: Aktivierungen, bewahrte Verlierer, Ø R Ist vs. BE). Nächste Prüfung: Freitags-Report der kommenden Wochen gegen die Erwartung +0,14…+0,16 R/Signal | 3.8/3.9 |
| 5a | ~~Phase 2: WebSocket-Trigger~~ — **ENTSCIEDEN: nicht bauen.** Alle drei Entscheidungsregeln verfehlt (TP1 < 30 min: 0 %; Extension ≥ 2 ATR: 16 %; T-10-Vorteil: +0,1 %). Restfälle durch Orts-Gate + PM-Radar abgedeckt. Zahlen in 3.7 | Messung 29.07. |
| 6 | **EN/DE-Sprach-Toggle existiert nicht** — UI ist fest deutsch (29.07. vereinheitlicht); ein echter Toggle wäre ein eigenes Feature, falls gewünscht | Betreiber-Frage 29.07. |
| 7 | ~~JWT_SECRET als ENV setzen~~ — **ERLEDIGT (30.07.):** per `openssl rand -hex 32` in `.env` auf dem Server fixiert, Boot-Log warnfrei | 3.11 |
| 8 | Server-Grundpflege: „System restart required", ausstehende Ubuntu-Updates — **Ein-Befehl-Skript liegt bereit (3.13):** `bash deploy/os_maintenance.sh` (Update → Reboot mit @reboot-Selbstverifikation → Log). Wartungsfenster beachten (nie Fr 22:00–Sa 06:00) | Infrastruktur, kein Bot-Thema |
| 9 | **Zins-Regime Phase 2: Auswertung** — `rates_json` annotiert ab 30.07. jedes neue Signal (FRED DGS2/10/30, Regime-Label Erstkalibrierung ±10/±25 bp). Sobald ≥ ~100 entschiedene Signale pro Regime-Zelle: Skript analog `exit_efficiency_analysis.py` — Hit-Rate/ØR je Regime; nur bei signifikantem Delta weiches Gate für zinssensible Longs (Biotech/Growth). Zusatzoption: HYG-Trend als Credit-Risiko-Proxy in den Context | 3.12 |
| 10 | ~~2 ET-Session-abhängige Tests zeitrobust machen~~ — **ERLEDIGT (30.07., 3.14):** `_premarket_window_active` in `_mock_mail_env` + Einzeltest gemockt; Suite jetzt rund um die Uhr grün | 3.14 |

---

## 8. Dokumentenlandkarte

| Dokument | Inhalt |
|---|---|
| `PROJEKTHANDBUCH.md` | **dieses Master-Dokument** |
| `PROJEKTHANDBUCH_CLAUDE.md` | Ausführliches Vorgänger-Handbuch (Stand 21.07.): Produktvision, Signalmodell, Invarianten, Architekturdetails |
| `AUDIT_ABSCHLUSSBERICHT_2026-07-24.md` | Tagesdokumentation Vollaudit 24.07. |
| `AUDIT_MATHEMATIK_2026-07-24.md` | Rechenkern-Prüfung im Detail |
| `AUDIT_TIEFENAUDIT_TRADINGLOGIK_2026-07-24.md` | Befunde T1/T2 im Detail |
| `AUDIT_GESAMT_2026-07-24.md` | Gesamtaudit + Legacy-Bereinigung |
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
