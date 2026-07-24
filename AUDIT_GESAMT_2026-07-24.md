# Alpha Station — Unabhängiger Gesamtaudit

**Datum:** 24. Juli 2026
**Geprüfter Stand:** `c2af709` (= `origin/main`, Branch `nachaudit-fixes-2026-07-21`, Arbeitsbaum sauber)
**Perspektiven:** Senior Dev · Chef-UX · Professor für Mathematik/Wahrscheinlichkeit/Logik · Trader (50 J., broker-unabhängig)
**Methode:** Vollständiger Testlauf, Git-Forensik, Code-Reviews der Nach-Audit-Commits, Security-Sweep, Verifikation früherer Audit-Claims im Code.

---

## 1. Gesamturteil

**Note: B+ (Gut, mit klar benennbaren Baustellen). Das ist kein Vibe-Coding-Produkt mehr.**

Alpha Station ist heute eine fachlich erstaunlich ehrliche Trading-Plattform. Die drei schlimmsten Krankheiten selbstgebauter Trading-Tools — **Look-ahead-Bias, geschönte Trefferquoten, fail-open bei fehlenden Daten** — sind systematisch und nachweisbar bekämpft: fail-closed-Gates, Netto-R:R nach Spread + beidseitiger Slippage, Score explizit als „Ranking, keine Gewinnwahrscheinlichkeit" deklariert (`penny_stock_scanner.py:1258`), konservative Same-Bar-Annahme (ungünstigere Stop/TP-Sequenz), Out-of-Sample-Split im Backtest.

Die 1057 Tests laufen **real grün** (von mir selbst ausgeführt: `1057 passed in 48.91s`, Projekt-venv Python 3.14.2). Jeder der 12 Commits seit dem letzten Nachaudit (21.07.) enthält eigene Regressionstests. Die Blocker des letzten Nachaudits (N1 Bear-ATR-Chronologie, N2 NLS-TypeError, N3 LVN-Gate, M9 Stablecoin-Kollisionen) sind **im Code verifiziert behoben**.

Das Produkt trägt aber zwei Altlasten, die mit wachsendem Codebestand teurer werden: den **30.358-Zeilen-Monolithen `api.py`** und **~19.000 Zeilen tote Streamlit-Legacy** im Repo. Beides ist kein Launch-Blocker, beides ist strategischer Zins auf eine Hypothek.

---

## 2. Empirisch verifizierte Fakten

| Prüfung | Ergebnis |
|---|---|
| Test-Suite | **1057/1057 grün** (49 s). Hinweis: Standard-Lauf scheitert an `PermissionError` auf `%TEMP%\pytest-of-miros` (Windows-Verzeichnisrechte, fremdbesessen) — Umgebungsproblem, kein Codeproblem; mit `--basetemp=tmp/pytest_audit` sauber |
| Git-Hygiene | Arbeitsbaum sauber, HEAD = `origin/main`, keine ungepushten Commits |
| Secrets | Keine hartkodierten API-Keys/Passwörter im Code (Pattern-Sweep über alle `.py`) |
| Gefährliche Primitive | Kein `verify=False`, kein `shell=True`, kein `pickle`, kein `eval/exec` im Produktivcode |
| Bare excepts | 0 in `api.py`/`scanner.py`, 1 in `bg_service.py` (benigne Cleanup-Guard, Z. 163) |
| TODO/FIXME-Debt | 3 Treffer gesamt — praktisch null |
| Nachaudit-Blocker N1/N2/N3/M9 | Verifiziert im Code behoben (u.a. `new_listing_scanner.py:1864`, `api.py:13717`, `api.py:18591`) |

---

## 3. Befunde nach Perspektive

### 3.1 Senior Dev

**Stärken**
- Saubere Modularisierungs-Richtung: `modules/` (28 Module, ~34k Zeilen) mit klaren Zuständigkeiten; das neue `modules/stock_execution.py` ist vorbildlich — defensives Parsing (NaN/Inf-Guards), dokumentierte Heuristiken, reine Funktionen.
- Build-Verifikation des Frontends per SHA-256-Quellhash (`scripts/build_frontend_bundle.js`), Boot-Error-Overlay statt stiller weißer Seite (`frontend/boot.js`).
- Deployment-Disziplin: Commercial-Strict-Mode mit Boot-Security-Gate (`enforce_commercial_boot_security`), CORS-Allowlist, systemd-Hardening, Preflight/Rollback laut Handbuch.

**Befunde**

| # | Schwere | Befund |
|---|---|---|
| D1 | **Mittel (strategisch Hoch)** | `api.py`: 30.358 Zeilen, 96 Endpunkte, 579 Funktionen in einer Datei. Merge-Konflikte, Review-Unfähigkeit, Ladezeiten der Toolchain. Die Module existieren bereits — die Extraktion muss konsequent weitergehen (Ziel: api.py = nur noch Routing/Composition Root). |
| D2 | **Mittel** | Tote Legacy: `scanner.py` (18.965 Zeilen Streamlit) wird produktiv nicht mehr referenziert (nur als „deaktiviert" in `deploy/` dokumentiert), `start_service.sh` startet sie aber weiterhin — widersprüchlich. Dazu `modules/data_fetchers.py.bak`, Backup-Archive (`cowork_backup_20260404.tar.gz`, `backup_pre_sanierung_20260610.tar.gz`, `backup_before_refactor.zip`) und ~25 Alt-Audit-/Handoff-Dateien im Repo-Root. In `archive/` verschieben oder löschen; `start_service.sh` entfernen oder auf FastAPI umstellen. |
| D3 | **Niedrig** | Hardcodierte `/tmp/*`-Pfade in `bg_service.py` (und Cleanup-Globs) — nicht portabel (Windows-Dev kann den Dienst nicht nativ laufen lassen); über `ALPHA_DATA_DIR`/tempfile abstrahieren. |
| D4 | **Niedrig** | Lokale Branch-Topologie verwirrend: lokaler `main` hängt 9 Commits hinter `origin/main` (e004194), gearbeitet wird auf `nachaudit-fixes-2026-07-21`, der mittlerweile = origin/main ist. Lokalen `main` fast-forwarden oder löschen. |
| D5 | **Niedrig** | `ADMIN_EMAILS`-Default enthält eine reale Mailadresse im Quelltext (`auth.py:118`). Funktional unkritisch (ENV überschreibt), gehört aber nicht ins Repo. |

### 3.2 Professor für Mathematik, Wahrscheinlichkeit & Logik

**Was mathematisch sauber ist (Stichproben, im Code verifiziert):**
- **Kostenmodell:** `net_rr = (tp − entry − k) / (risk + k)` mit `k = entry·(spread + 2·slippage_bps)/10⁴` — Kosten schlagen symmetrisch auf Zähler **und** Nenner durch. Das ist die ehrliche Variante; die meisten Retail-Tools belassen Kosten nur im Zähler. Mindest-Gates: Netto-TP1 ≥ 1.0R, effektiv ≥ 1.5R.
- **Kanonische Invarianten:** Stop < Entry < TP1 < TP2 (Long, gespiegelt Short), TP1 ≠ TP2, Live-R:R wird neu gerechnet statt Scanner-R:R wiederzuverwenden — im Handbuch als harte Invarianten verankert und in `trade_health.py`/`trade_geometry` durchgesetzt.
- **Semantische Ehrlichkeit:** `score_semantics` sagt wörtlich „heuristic ranking, not a win probability". Backtests: ehrlicher Profit-Factor („INF"), Exit-Reason-Mapping, chronologische Equity, OOS-Split ohne Leakage (M17, verifiziert in `modules/performance_metrics.py`/`backtests.py`).
- **Wilder-ATR** kanonisch zentralisiert; die zeitlich invertierte Bear-ATR (N1) ist gefixt — genau der Bug-Typ, der in Crash-Phasen Stops halbiert hätte.

**Offene mathematische Punkte:**

| # | Schwere | Befund |
|---|---|---|
| M1 | **Mittel (langfristig)** | **Kalibrierung fehlt strukturell.** Alle Schwellen (0.45/0.55-Score-Gewichte, RVOL-Cuts, 1.5R-Netto-Mindestens, 360s-Trigger-Frische) sind Experten-Heuristiken. Der 22.07.-Audit sagt es selbst ehrlich: „Schwellen brauchen Forward-Daten, Regime-Segmentierung, Konfidenzintervalle, Walk-forward". Solange kein Kalibrier-Loop aus dem Signal-Tracker (der jetzt vorhanden ist) in die Schwellen zurückführt, bleibt das System ein gut gebautes, aber unkalibriertes Experten-System. Empfehlung: quartalsweise Wilson-Intervalle pro Scanner/Regime + Schwellen-Review, kein Auto-Tuning. |
| M2 | **Niedrig** | Score-Gewichte `0.45·setup + 0.55·entry − 0.15·dump` können bei `dump_risk=100` und perfekten Subscores nie über 85 kommen — die obere Grade-Grenze ist faktisch vom Dump-Risiko gekoppelt. Kein Bug, aber eine nicht dokumentierte Modell-Eigenschaft. |
| M3 | **Niedrig** | Kein Positionsgrößen-/Portfolio-Layer (kein Kelly, kein Risk-per-Trade-Budget). Als Produktentscheidung vertretbar — sollte im UI/Handbuch explizit als bewusste Grenze stehen, damit Kunden Entry/Stop nicht als Komplett-Anleitung lesen. |

### 3.3 Trader (50 Jahre, broker-unabhängig)

**Was mir als altem Trader gefällt:**
- **No-Chase-Disziplin ist Code, nicht Marketing:** gechashte Entries werden blockiert (`e209e8a`), 4H-Rejections sperren Swing-Mails (`1ed1270`), Baseline-Liquidität gated Alerts (`99c644c`). Die App sagt öfter „nicht jetzt" als „kauf" — genau so überlebt man.
- **Kosten zuerst:** Slippage + Spread im R:R, bevor irgendjemand jubelt. Die meisten Tools, die ich in 50 Jahren sterben sah, starben an unterschätzten Transaktionskosten.
- **Fail-closed überall:** stale Quotes/Bars/Trades → kein Entry. Fehlende Daten = kein Signal statt Fantasie-Signal.
- **Penny-Lifecycle ist erwachsen:** Discovery ≠ Kauf; Kauf erst nach abgeschlossener 5m-Bestätigung + Live-Revalidierung; Exit nur bei bestätigtem Strukturverlust, nicht bei einer roten Kerze. Die transaktionale Mail-Logik (State erst nach erfolgreichem Versand) zeigt, dass jemand an den Betrieb und nicht nur an Screenshots gedacht hat.

**Was ich kritisch sehe:**

| # | Schwere | Befund |
|---|---|---|
| T1 | **Mittel** | **Signal-Inflation-Risiko durch Scanner-Zoo.** ~10 Scanner (Aktien, Krypto, Biotech, BI, ORB, Bear, Crash, Penny, Early Mover, New Listing, Strategien) werfen parallel Signale. Die kanonische Zustandslogik (TRADE_NOW/WAIT/NO_TRADE) zähmt das — aber es gibt keine **scanner-übergreifende Tages-Obergrenze** oder Portfolio-Korrelationssicht. Fünf „JETZT TRADEN" im selben Sektor am selben Morgen sind faktisch eine Position. Der Cluster-Warning-Mail-Mechanismus (`test_cluster_warning_mail.py`) deutet in die richtige Richtung; eine echte aggregierte Exposure-Sicht fehlt. |
| T2 | **Niedrig** | KI-Fallback (`c2af709`) erzeugt regelbasierten „Beispiel-Long-Plan" mit konkreten Stop/TP-Zahlen aus trivialer 20-Tage-Logik. Ehrlich gelabelt als Schnellcheck — aber Kunden lesen Zahlen, keine Labels. Die Heuristik (`close>ma20 && chg≥1.5% && dist≤4%` ⇒ WATCH_LONG) ist grob genug, um in News-Spitzen falsche Präzision vorzutäuschen. Optional: im Fallback keine konkreten Level, nur Regime-Text. |
| T3 | **Niedrig** | `str(e)` im KI-„unreachable"-Pfad geht Richtung Kunde. Requests-Exceptions können interne Hostnamen/URLs enthalten. Auf generische Meldung kürzen. |

### 3.4 Chef-UX

**Stärken**
- Kanonische Zustände überall konsistent (Tabelle = Mail = Sidebar = Chart) — das Handbuch erzwingt es, die Tests sichern es (`test_bi_scanner_display_policy.py`, `test_trade_reminder_ui.py`).
- „Entry fast bereit"-Liste wurde **bewusst entfernt**, weil sie Wartesignale wie sichere Entries aussehen ließ. Das ist UX-Reife: lieber weniger Dopamin als mehr Klarheit.
- Signal-only-Default, Vorstufen nur per Schalter; Boot-Error-Overlay mit echter Fehlermeldung statt weißer Seite; Scan-Fortschritt live (`88de148`).
- Auth via httpOnly-Cookie (`credentials: 'include'`), kein Token im localStorage — XSS kann die Session nicht auslesen. localStorage nur für harmlose UI-Prefs (Watchlist, Reminder).

**Befunde**

| # | Schwere | Befund |
|---|---|---|
| U1 | **Mittel** | **Mobile ist Anspruch, aber dünn belegt:** Das Handbuch erklärt „Mobile Bedienbarkeit" zur Betreiberpriorität, das 11.045-zeilige `index.html` enthält aber nur **3 `@media`-Queries**. Für ein Produkt, das unterwegs Entry/Stop/TP in Sekunden verständlich machen will, ist das zu wenig. Empfehlung: Breakpoint-Audit der Scanner-Tabellen (horizontales Scrollen vs. Karten), Touch-Targets ≥ 44 px, und mindestens einen Smoke-Test pro Hauptansicht bei 390 px Breite. |
| U2 | **Niedrig** | 11k Zeilen Inline-React in `index.html` + Precompile-per-Hash ist pragmatisch robust, aber ohne Source-Maps bleibt Produktiv-Debugging Rätselraten. Mittelfristig: echter Vite-Build. |
| U3 | **Niedrig** | KI-Fallback-Texte sind deutsch mit ASCII-Umlaut-Ersatz („verfuegbar") im Backend, Rest der App mutmaßlich korrekte Umlaute — inkonsistenter Sprach-Feinschliff an den Rändern. |

---

## 4. Konsolidierte Maßnahmenliste

**Vor kommerziellem Launch (🔴):** *nichts Blockierendes gefunden.* Die 🔴-Liste des 21.07.-Nachaudits ist geschlossen und verifiziert.

**Kurzfristig (🟡, nächster Sprint):**
1. `scanner.py`-Leiche + `.bak` + Backup-Archive + `start_service.sh` aus dem Repo-Root räumen (archivieren/löschen) — D2.
2. KI-Fallback: `str(e)` durch generische Meldung ersetzen (T3); prüfen, ob konkrete Stop/TP-Zahlen im Fallback gewollt sind (T2).
3. Lokalen `main` auf `origin/main` ziehen oder löschen (D4); `ADMIN_EMAILS`-Default leeren (D5).
4. Mobile-Breakpoint-Audit der drei Hauptansichten (U1).

**Strategisch (🟠, nächstes Quartal):**
5. `api.py`-Extraktion fortsetzen: Routing von Logik trennen, Ziel < 10k Zeilen (D1).
6. Kalibrier-Loop: Signal-Tracker → Wilson-Intervalle pro Scanner/Regime → dokumentierter Schwellen-Review (M1). Der Tracker existiert; die Mathematik dazu ist eine Woche Arbeit.
7. Scanner-übergreifende Exposure-/Korrelationssicht (T1).
8. Vite-Build-Pipeline mit Source-Maps (U2); `/tmp`-Pfade abstrahieren (D3).

---

## 5. Schlusswort — als Trader gesprochen

Ich habe in 50 Jahren hunderte Tools gesehen. Die meisten lügen in drei Stufen: erst die Trefferquote, dann die Kosten, dann sich selbst. Dieses System tut an allen drei Stellen das Gegenteil — und dokumentiert seine eigenen Grenzen öffentlich im Handbuch. Das ist selten und wertvoll.

Der eigentliche Risikofaktor ab hier ist nicht mehr der Code, sondern die **Versuchung, ein ehrliches System aggressiv zu vermarkten**. Solange Score ≠ Wahrscheinlichkeit in jedem Sales-Text so klar steht wie im Quelltext, ist das Produkt vertretbar, gut gebaut und — nach dem Schwellen-Kalibrierungs-Loop — sogar richtig stark.

**Launch-Ampel: 🟢 (mit den vier 🟡-Sofortaufgaben als Aufräum-Woche).**
