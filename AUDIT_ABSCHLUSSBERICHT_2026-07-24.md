# Tagesdokumentation 2026-07-24 — Vollaudit, Kalibrier-Loop, Verdikt-Alarm

**Datum:** 24. Juli 2026 · **Endstand:** `d345ffe` auf `main` · **Suite:** 1071 → **1101 Tests** (alle grün)
**Anlass:** Vollaudit der von Codex entwickelten Trading-App (Mathematik, Logik, Wahrscheinlichkeiten) und Umsetzung aller Befunde bis zur Produktiv-Verifikation auf dem Server (178.104.69.209).
**Detail-Dokumente:** `AUDIT_GESAMT_2026-07-24.md`, `AUDIT_MATHEMATIK_2026-07-24.md`, `AUDIT_TIEFENAUDIT_TRADINGLOGIK_2026-07-24.md`.

---

## 1. Tagesüberblick — 9 Commits auf `main`

| Zeit | Commit | Inhalt |
|---|---|---|
| 14:26 | `65a3f5e` | Legacy-Dateien nach `archive/`, Info-Leak im KI-Fallback geschlossen |
| 15:17 | `89334ef` | Rechenkern-Audit: 43 numerische Prüfungen gegen Lehrbuch-Referenzen |
| 15:41 | `e27a37a` | Tiefenaudit Trading-Logik: Befunde T1/T2 dokumentiert |
| 16:02 | `1e8b1da` | T1 Managed-R, T2 Krypto-Schwellen, Wilson-Kalibrier-Loop |
| 17:14 | `f2e1f13` | Neue Felder in Frontend (Performance-Tab) + Wochenreport-Mail |
| 17:48 | `9290f2e` | Server-Smoke-Test `scripts/smoke_signal_performance.py` |
| 17:59 | `9857d99` | Mail-Preview-Renderer `scripts/preview_weekly_report.py` |
| 19:23 | `5b54438` | Scanner-Verdikt-Tabelle (behalten/beobachten/abschalten) im Terminal |
| 20:21 | `d345ffe` | Verdikt-Alarm im Wochenreport; Verdikt-Logik zentralisiert |

---

## 2. Phase 1 — Die drei Audits

### 2.1 Gesamt-Audit und Legacy-Bereinigung (`65a3f5e`)

- Veraltete Dateien nach `archive/` verschoben (`scanner.py` → `archive/legacy_streamlit/`, `index.html` → `archive/legacy_frontend/`, `data_fetchers.py.bak`, `start_service.sh`, `h`); Pfad-Referenz in `test_audit_fixes_bg.py` angepasst.
- **Info-Leak geschlossen:** Der KI-Fallback in `api.py` gab `str(exception)` nach außen — interne Fehlertexte sind jetzt nicht mehr extern sichtbar.
- Ergebnis dokumentiert in `AUDIT_GESAMT_2026-07-24.md`.

### 2.2 Rechenkern-Audit — „wird alles richtig berechnet?" (`89334ef`)

Neue Testdatei `test_math_invariants.py`: **13 Tests, 43 numerische Prüfungen** gegen unabhängig nachgerechnete Lehrbuch-Referenzen (Wilder/Elder-Konventionen):

- **ATR** (Wilder-Glättung), **RSI** (Wilder), **EMA**, **MACD** (12/26/9), **VWAP**, **Stochastik %K/%D**
- **R:R-Geometrie** (Entry/Stop/Targets), **Profit Factor**, **RVOL**

**Ergebnis: Rechenkern lehrbuchkorrekt.** Keine Rechenfehler gefunden. Details in `AUDIT_MATHEMATIK_2026-07-24.md`.

### 2.3 Tiefenaudit Trading-Logik (`e27a37a`)

Vollständige Lektüre aller entscheidungstragenden Pfade (kein Sampling): Penny-Scanner (1.408 Zeilen), `simulate_trade`, Fill-/Exit-Logik des Trackers, Flag-Validierung, Setup-Score/Z-Score, Trade-Health.

**Urteil: Kern sauber und traderisch erwachsen** — 18 harte Gates vor `JETZT_KAUFEN`, Stop-first-Konvention in allen drei Simulationspfaden, Gap-Fills am Open, kein Look-Ahead im Backtest.

**Zwei MITTEL-Befunde** (Methodik, keine Rechenfehler):

- **T1:** Der Tracker buchte TP2 mit vollem Geometrie-R, während Backtest und Kundenempfehlung 50/50-managen („TP1 = halb raus") — Track-Record und befolgbares Ergebnis maßen Verschiedenes.
- **T2:** Krypto-Grade-Schwellen (95/85/72/60) waren nach der V3.3-Reskalierung der Aktien-Schwellen stehen geblieben und dadurch **strenger als Aktien** (S: 56,5 % vs. 49,1 % des Max-Scores) — unbeabsichtigte Grade-Knappeheit bei Krypto.

Details in `AUDIT_TIEFENAUDIT_TRADINGLOGIK_2026-07-24.md`.

---

## 3. Phase 2 — Umsetzung der Befunde (`1e8b1da`)

### 3.1 T1: Managed-R (50/50) als zweite, offizielle R-Semantik

`modules/signal_tracker.py`:

- **`avg_r_managed_50_50`** je Bucket + **`r_managed_50_50`** je Signal:
  `r_managed = 0.5·r_tp1 + 0.5·r_realized_rest` bei TP1-Treffer, sonst `r_realized`.
- Beispiele (Long 100/95/105/110): TP2 → 1,5R (statt 2,0R Level); Stop nach TP1 → 0,0R (statt −1,0R); Expired nach TP1 bei +0,6R → 0,8R.
- **Retroaktiv** aus vorhandenen Fill-/TP1-/Final-Records ableitbar — keine Datenmigration nötig.
- Das Level-R (`avg_r`) bleibt unverändert; `summary["r_semantics"]` dokumentiert beide Semantiken maschinenlesbar.

### 3.2 T2: Krypto-Grade-Schwellen neu verankert

`analyze_breakout_imminent(crypto_mode=True)` in `modules/patterns.py`:

| Grade | alt | neu | Herleitung |
|---|---|---|---|
| S | 95 (+3 fires) | **71** (+3 fires) | 85 × 0,841 (V2.9-Ratio) |
| A | 85 (+2 fires) | **61** (+2 fires) | 71 × 0,859 |
| B | 72 (+1 hit) | **48** (+1 hit) | 57 × 0,847 |
| C | 60 | **44** | 55 × 0,80 |

Die originalen V2.9-Kalibrier-Ratios wurden auf die aktuellen V3.3-Aktien-Schwellen angewendet — Krypto liegt damit wieder beabsichtigt ~15–20 % unter Aktien statt über ihnen.

### 3.3 Kalibrier-Loop: Statistische Ehrlichkeit in der Summary

Je Bucket neu:

- **`decided_signals`** — Anzahl entschiedener Signale (Win oder Stop),
- **`win_rate_wilson_95`** — Wilson-Score-Konfidenzintervall (z = 1,96; exakter Binomial-CI-Ersatz, robust bei kleinen Stichproben und Quoten nahe 0/100 %),
- **`sample_reliable`** — `true` ab 30 entschiedenen Signalen (die im Wochenreport verankerte Mindest-Stichprobe).

---

## 4. Phase 3 — Sichtbarkeit in UI und Mail (`f2e1f13`)

**Frontend, Performance-Tab** (`frontend/index.html`):

- Neue Kopf-Karte **„Ø R 50/50"** (Grid auf 6 Karten); Hit-Rate-Karte zeigt das **Wilson-95%-KI** im Hint.
- **Amber-Warnbanner** bei `sample_reliable === false` („X von 30 entschiedenen Signalen").
- Scanner-Tabelle: Spalte **Ø R 50/50**, unter der Hit-Rate „KI x–y % · kleine Stichprobe".
- Letzte Signale: 50/50-R unter dem realisierten R; Methodik-Fußzeile erklärt beide R-Semantiken.

**Wochenreport-Mail** (`bg_service.py`):

- **Ø R 50/50**-Spalte in Kopf- und Scanner-Tabelle; Hit-Rate mit KI-Span; `decided_signals` aus dem Bucket (Fallback: lokale Summe); Fußtext dokumentiert 50/50-Mechanik + Wilson-KI.
- **Rückwärtskompatibel:** alte Summaries ohne neue Felder rendern mit „–".

---

## 5. Phase 4 — Server-Tooling und Produktiv-Verifikation (`9290f2e`, `9857d99`, `5b54438`)

Drei Werkzeuge (laufen ohne Auth direkt auf der Datenschicht):

1. **`scripts/smoke_signal_performance.py`** — prüft Key-Präsenz und Werte-Invarianten der neuen Felder; Exit 0/1.
2. **`scripts/preview_weekly_report.py`** — rendert die Wochenreport-Mail mit echten Daten (gleicher Code-Pfad wie der Freitags-Job, **ohne Versand**) nach `weekly_report_preview.html`.
3. **Scanner-Verdikt-Tabelle** im Preview — Terminal-Ausgabe, kein scp/Browser nötig.

**Produktiv-Verifikation auf dem Server (19:25 Uhr, PASS):**

```
Fenster: 90 Tage | Signale: 249 | entschieden: 211 | Scanner: 4
Wilson: [36.2, 49.4] | sample_reliable: true | avg_r: 0.315 | managed: 0.31
sum_r: +66.371
```

---

## 6. Live-Befund aus den Produktivdaten (90-Tage-Fenster)

| Scanner | Sig | Entsch. | Hit % | KI 95 | Ø R | Ø R 50/50 | Σ R | Verdikt |
|---|---|---|---|---|---|---|---|---|
| stock_strategy | 220 | 188 | 43 | 36–50 | +0,29 | +0,30 | **+55,2** | behalten (KI 36 > BE 33) |
| crash | 18 | 13 | 54 | 29–77 | +0,74 | +0,58 | +9,6 | beobachten (n < 30) |
| early_movers | 10 | 10 | 20 | 6–51 | +0,16 | +0,21 | +1,6 | beobachten (n < 30) |
| trade_reminder | 1 | 0 | — | — | — | — | 0,0 | beobachten |
| **GESAMT** | 249 | 211 | 43 | 36–49 | +0,32 | +0,31 | **+66,4** | behalten |

**Fachliche Einordnung:**

- **stock_strategy ist der Motor** (89 % aller entschiedenen Signale): implizierter Ø-Gewinn ≈ 2,0R bei 1R Risiko. Edge real, aber Marge nur **3 Prozentpunkte** (KI-Untergrenze 36 % vs. Breakeven 33 %) — Re-Check bei ~250 entschieden.
- **crash zeigt negatives Management-Delta** (0,58 vs. 0,74 = −0,16R): TP1-Teilverkauf kostet hier Geld (Reversals laufen weiter). **Nicht** ändern bei n = 13 — Alarm feuert bei n ≥ 30.
- **early_movers ist Hochvarianz by design:** 20 % Treffer, implizierter Ø-Gewinn ≈ 4,8R — 8er-Verlustserien sind Normalbetrieb. Umgekehrtes Vorzeichen: 50/50 hilft (+0,05R, Spike-and-Fade-Charakter).
- **Portfolio:** +66,4R in 90 Tagen, zu ~83 % von einem Scanner getragen — Diversifikation nominal, nicht real.

---

## 7. Phase 5 — Verdikt-Alarm im Wochenreport (`d345ffe`)

**Zentralisierung:** `scanner_verdict()` + `breakeven_win_rate_pct()` leben jetzt in `modules/signal_tracker.py` — einzige Wahrheit für Tracker, Preview und Mail.

**Verdikt-Regeln** (E = Ø R, p = Hit-Rate, Breakeven p* = p/(1+E)):

- `behalten` — n ≥ 30, E > 0 **und** Wilson-Untergrenze > p*
- `abschalten` — n ≥ 30 **und** (E ≤ −1R **oder** Wilson-Obergrenze < p*)
- `beobachten` — alles andere / kleine Stichprobe

**Alarm-Mechanik in `bg_service.py`:**

- Persistenter Verdikt-State (JSON, atomar geschrieben) mit Woche-für-Woche-Vergleich.
- Gelber **Alarm-Block ganz oben in der Mail** bei: (a) Überschreiten der 30er-Marke, (b) Verdikt-Wechsel (deckt „behalten → abschalten" bei gebrochener KI-Untergrenze ab).
- **Baseline-Regel:** erster Lauf legt den State still an (kein Spam).
- **Save-after-send:** State wird erst nach erfolgreichem SMTP-Versand gespeichert — kein Alarm geht bei Versandfehlern verloren (Spiegel der Dedupe-Logik).

---

## 8. Test-Entwicklung über den Tag

| Stand | Suite | Neu |
|---|---|---|
| Morgen | 1071 | — |
| `1e8b1da` | 1085 | +14 (`test_tracker_calibration.py`: Managed-R-Matrix inkl. Short, Wilson-Grenzen, Reliability, Summary-Integration, T2-Monotonie) |
| `f2e1f13` | 1090 | +5 (4 Mail: Spalten/KI/Recent/Legacy-Kompat; 1 Frontend-Pins) |
| `d345ffe` | **1101** | +11 (5 Alarm: 30er-Crossing, Verdikt-Wechsel, unverändert, Baseline, Versandfehler-State-Erhalt; 6 Verdikt-Unit) |

Dazu `test_math_invariants.py` (43 numerische Prüfungen) aus dem Rechenkern-Audit.

---

## 9. Bewusst NICHT geändert

- **Keine Exit-Regel-Änderungen** bei crash/early_movers: n = 13/10 ist Rauschen; jede Regeländerung daraus wäre Kurvenanpassung. Der Alarm meldet, sobald die Stichprobe reif ist.
- **Keine Änderung am Wochenreport-Betreff** (bestehender Pin) und **keine API-Vertragsänderung** — `/api/signal-performance` reicht das Summary-Dict unverändert durch, neue Felder fließen automatisch.

---

## 10. Betrieb: Was jetzt aktiv überwacht wird

- **Freitags 16:15–23:00 ET:** Wochenreport mit Ø-R-50/50-Spalten, Wilson-KI, Stichproben-Hinweis — und ab der zweiten Woche mit Verdikt-Alarmblock bei Änderungen.
- **Trigger:** crash erreicht n ≥ 30 → Mail-Alarm mit Verdikt; stock_strategy KI-Untergrenze < Breakeven → „behalten → …"-Alarm; jeder andere Verdikt-Wechsel → Alarm.
- **Manuell jederzeit:** `venv/bin/python3 scripts/preview_weekly_report.py --days 90` auf dem Server (Tabelle + Verdikte im Terminal).

---

## 11. Offene Punkte

1. **Deployment-Bestätigung für `d345ffe` ausstehend** — Pull + Restart beider Dienste (`tradingbot-api`, `tradingbot-bg`) wurde angewiesen; die Verdikt-Baseline entsteht mit der nächsten Wochenmail.
2. **`JWT_SECRET` auf dem Server nicht gesetzt** (Auth-Warnung im Server-Log): ephemerer Zufalls-Secret → **alle Sessions werden bei jedem Neustart ungültig**. Für den Produktivbetrieb `JWT_SECRET` als ENV setzen.
3. **Re-Checks:** crash bei n ≥ 30 (Management-Delta −0,16R verifizieren); stock_strategy bei ~250 entschieden (3-pp-Marge).
4. Zeilenenden: `modules/patterns.py`, `bg_service.py`, `modules/signal_tracker.py` wurden auf LF normalisiert (vorher gemischt) — Roh-Diffs wirken größer, `git diff -w` zeigt die echten Änderungen.
