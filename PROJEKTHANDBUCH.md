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

---

## 4. Das Mail-System (Stand 29.07.)

**Klassen:** `trade` / `swing_trade` (handelbar, Telegram-Spiegel), `watch` (Opt-in),
`info` (Passwort, Test, Reports). Betreff-Präfixe: 🚨 JETZT SWING / 👁️ WATCH.

**Empfänger-Logik (`modules/auth.get_email_alert_recipients`):**
Plan hat E-Mail-Alerts → `email_alerts_enabled` → Watch-Opt-in (nur watch-Klasse) →
Narrative-Frequenz (nur narrative_pulse) → Trade-Horizont (swing/intraday/both) →
**Mail-Kanal** (seit 28.07.). Betreiber-Fallback (`ALERT_EMAIL`) bekommt alle Klassen,
Kanal-Opt-out greift aber auch dort (28.07.).

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
  Metrik auf **Scanner × Kalendermonat** herunter (Regime-/Stichproben-Analyse).

---

## 6. Testlandschaft

**1130 Tests, alle grün** (29.07.). Wichtige Suiten:

| Datei | Deckt ab |
|---|---|
| `test_math_invariants.py` | Rechenkern gegen Lehrbuch-Referenzen |
| `test_tracker_calibration.py` | Managed-R, Wilson-KI, sample_reliable |
| `test_email_alert_audit.py` | Alert-Gates, Swing-Regeln, Chase-Gates (28.07.) |
| `test_mail_class_api.py` | Mail-Klassen, ATR-Annotation, Kanal-Versand (28.07.) |
| `test_commerce_hardening.py` | Auth, Billing, Kanal-Settings (28.07.) |
| `test_cluster_warning_mail.py` | Klumpenrisiko, Sweep-End-to-End |
| `test_stock_execution_regressions.py`, `test_stock_swing_4h_rejection.py` | 4H-Execution-Gates |

**Regeln:** Nie ohne `--basetemp=tmp/pytest_audit` (Sandbox). Produktivcode-Änderungen
erst nach voller Suite pushen. Keine Abhängigkeit vom echten Wirtschaftskalender in
Tests (Market-Context mocken — 29.07., FOMC-Lehre). Suite-Stand-Historie:
985 (21.07.) → 1101 (24.07.) → 1104 (28.07. ATR-Annotation) → 1114 (28.07. Chase-Gates) →
1120 (28.07. Mail-Kanäle) → 1126 (29.07. Orts-Gate) → 1130 (29.07. UX-Paket).

---

## 7. Offene Punkte

| Prio | Punkt | Kontext |
|---|---|---|
| 1 | **C: Scan-Taktung** — Sweep läuft 30-Min-Takt, Mails erst in US-Regular-Session; vorbörsliche/frühe Moves (RITM, NVST) entstehen im Blindfenster. Optionen: Takt 9:30–11:00 ET auf 10–15 Min verdichten; Pre-Market-Watch-Mailklasse (Produktentscheidung) | Vom Betreiber angesprochen, bewusst vertagt |
| 2 | **Schwellen-Verifikation** — 2,5/3,5 ATR, 2,0 %-Budget und 2,0-ATR-Orts-Gate sind an 7 Produktivfällen kalibriert; an Server-Logs prüfen (`swing_day_move_*`, `swing_top_entry_*`, `top_entry_*`, `bottom_entry_*`, `low_volatility`). Falls `top_entry`/`bottom_entry` nie auftauchen: Crypto-Rows ohne `day_high`/`day_low` → Anreicherung nachbauen | Commits `cdeff7e`, `da6c4be` |
| 3 | **Retest-Plan-Mail** — Chase-Gates unterdrücken Zeilen aktuell ganz; eine WATCH-Mail mit konkretem Retest-Entry (statt Market-Entry) wäre ein eigenes Feature | Betreiber will keine Watch-Mails — nur falls er es sich anders überlegt |
| 4 | **Gate-Wirkung auswerten** — nach 1–2 Wochen Produktivlauf `scripts/signal_performance_breakdown.py` laufen lassen: Hit-Rate/R je Monat vor vs. nach den Chase-/Orts-Gates vergleichen | Commit `da6c4be` |
| 5 | **EN/DE-Sprach-Toggle existiert nicht** — UI ist fest deutsch (29.07. vereinheitlicht); ein echter Toggle wäre ein eigenes Feature, falls gewünscht | Betreiber-Frage 29.07. |
| 6 | JWT_SECRET als ENV setzen (Warnung bei jedem Start; Sessions invalidieren bei Neustart) | Commercial-Readiness |
| 7 | Server-Grundpflege: „System restart required", ausstehende Ubuntu-Updates | Infrastruktur, kein Bot-Thema |

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
| `archive/` | Alt-Versionen: Legacy-Frontend, Streamlit, alte Audits |

---

## 9. Arbeitsregeln, die sich bewährt haben

1. **Erst lesen, dann behaupten** — jede Diagnose gegen den echten Code und echte Zahlen verifizieren.
2. **Klein schneiden, voll testen, pushen** — jede Änderung: Suite komplett grün, dann sofort auf `main`.
3. **Server-Anweisung immer mitgeben** — Pull + Restart der betroffenen Services + Verifikationsbefehl.
4. **Ehrlichkeit vor Tempo** — ungeprüfte Annahmen als solche markieren; Schwellen als Kalibrierung kennzeichnen.
5. **Bestandsverhalten schützen** — neue Gates greifen nur mit vorhandenen Metadaten; Defaults ändern nichts für Bestandsnutzer.
