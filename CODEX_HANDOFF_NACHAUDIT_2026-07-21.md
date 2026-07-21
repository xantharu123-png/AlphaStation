# Codex-Übergabe: Nachaudit-Fixpaket & offene Restarbeit

**Stand:** 21. Juli 2026
**Repository:** `C:\Users\miros\Desktop\TradingBot`
**Für:** Codex (nächster Bearbeiter)
**Vorgeschichte:** Vollaudit 19.07. (`AUDIT_SCANNER_VOLLAUDIT_2026-07-19.md`) → Codex-Fixrunde 20.07. (`AUDIT_SCANNER_FIXSTATUS_2026-07-20.md`) → unabhängiges Nachaudit 21.07. (`AUDIT_NACHAUDIT_CODEX_2026-07-21.md`) → dieses Fixpaket.

---

## 1. Ergebnis in einem Satz

**Codex hat gut, aber nicht fertig gearbeitet — 34 von 49 Befunden sind sauber behoben (darunter alle Schwergewichte: Krypto-Zeitskala, MEXC-Einheiten, Tracker-Fill-Logik, AutoTrader-Order, RVOL-Normierung, patterns-Crash, Retest-Pfad), 12 nur teilweise, 2 gar nicht — und der Codex-Bericht „alle Befunde behoben" ist damit nachweislich zu optimistisch.** Der 49. Befund (L14) ist toter Legacy-Code ohne produktiven Aufrufer (n. a.).

Die zwei nicht behobenen: **M9** (Staked-SOL/RLUSD-Ausschlüsse + Symbol-Kollisionen) und **L11** (Datenfetcher-Kanten) waren im gelieferten Stand byte-identisch zum Alt-Code, obwohl der Fixstatus sie als „behoben" führte. M9 (Kern) ist mit diesem Fixpaket jetzt geschlossen; L11 bleibt offen (siehe §4).

---

## 2. Was in diesem Fixpaket gemacht wurde (bereits im Code)

Geänderte Dateien: `api.py`, `bg_service.py`, `modules/vrvp_levels.py`, `modules/new_listing_scanner.py`, `modules/scanners.py`, `modules/backtests.py`. Neu: `test_nachaudit_fixes.py` (22 Tests, alle grün). Jeder Fix trägt im Code einen `NACHAUDIT`-Kommentar.

| ID | Datei / Stelle | Was war falsch | Was wurde gemacht |
|---|---|---|---|
| **N1** | `api.py` Bear-Scan (`sort=desc`-Fetch) + `modules/vrvp_levels.py::calculate_wilder_atr` | Bear-ATR rechnete auf absteigend sortierten Bars → Wilder-Glättung zeitlich invertiert, ATR im Crash ~0,45× → Stops/TPs zu eng | `list(reversed(bars))` vor der Berechnung; `calculate_wilder_atr` sortiert defensiv nach Timestamp (nur wenn alle Bars numerische TS haben → Backtest-`date`-String-Bars bleiben unberührt) |
| **N2** | `modules/new_listing_scanner.py::calculate_listing_exhaustion` (~1816) | `vol_first > 0` mit `vol_first=None` → TypeError, vom try/except verschluckt → jüngste/dünnste Listings fielen still aus | `bool(vol_first and vol_second)` |
| **N3** | `api.py::_annotate_early_mover_overhead_resistance` + `_apply_early_mover_vrvp_targets` | LVN-Kanten (Volumen-Vakuum) wurden als Overhead-Barriere gewertet → valide Long-Setups auf `WAIT_FOR_BREAK_RECLAIM` degradiert (H10-Restpfad) | LVN-Level (`source` startet mit `vrvp_lvn`) an beiden Stellen ausgeschlossen |
| **H7** | `modules/scanners.py` Biotech-Quick-Scan (~3789) | 2h-News-Refresh übergab `rvol_direction` nicht → Distribution-Tag bekam vollen RVOL-Bonus (Grade-Flip) | `rvol_direction=(Tech_Details).rvol_up_day` mitgegeben |
| **M9** | `api.py` `EXCLUDED_CRYPTO_*` + Dedupe (`seen_symbol_ids`) | SOL-LSDs/RLUSD/SolvBTC liefen als Mover; zwei gleichnamige CoinGecko-Coins mergten zu einer Row (Cross-Coin-Konfluenz) | Symbole/Textterms ergänzt; `seen_symbol_ids` (fail-safe: abweichende/fehlende ID → kein Merge) |
| **M18** | `bg_service.py::_tracker_crypto_fetcher` | Kein Cache-Alter-Check → Tracker bewertete gegen tage­alte Preise | Beide Zeitfelder (`ts` float + `cached_at` ISO) + mtime-Fallback, TTL 3 h |
| **M16** | `api.py` Inverse-ETF-Block | 1x-Short/VIX-Produkte bekamen nie `decay_warning`; `"inverse"`-Literal toter Code | `short`/`vix` in Beschreibung → Warnung; `decay_note` nennt 1x nicht mehr „1.0x Hebel" |
| **N6/N7** | `api.py::_downgrade_expired_crypto_triggers` + `get_early_movers` | `entry_status` blieb `JETZT_TRADEN`; `trade_now_count` vor Downgrade gezählt | `entry_status→WAIT_FOR_TRIGGER`; Stats nach Downgrade |
| **N8** | `api.py::_us_equity_expected_volume_fraction` | RVOL-Projektion in Minute 1–14 überzeichnete PM-lastige Mover (×62); Guard saß nur im Bear/Turtle-Pfad | Guard `minutes_open < 15 → 1.0` zentral in die Fraction-Funktion → gilt für Strategy-Scan, Volume-Spikes, Bear/Turtle, ORB |
| **N10** | `modules/vrvp_levels.py::_dedupe_levels` | Dedupe-Floor `1e-9` ≈ 45 % bei Sub-Nano-Coins → Level kollabierten | Floor `1e-12` |
| **N11** | `modules/scanners.py` (~2681) | `int(avg_vol_20)` mit `None` → stiller Technik-Score-Ausfall | `int(avg_vol_20 or 0)` (nur dieser Teil; Rest siehe §4) |
| **H3** | `modules/backtests.py` (3 Funktionen) | Hart gescheiterte Fetch-Tage (`None`) still verschluckt → Stop-Hits unsichtbar | `failed_fetch_days` gezählt, im Summary + als Warnung |
| **L8** | `modules/new_listing_scanner.py::cleanup_monitoring` | `nls_monitoring.json` wuchs unbegrenzt | Purge expired/invalidated nach 7 Tagen; bereits-expired nicht neu markieren |
| Klein | `modules/scanners.py` | Biotech-News 2↑/3↓ ergab +4 statt −4; `pos_90d`-or-Falle; falscher Paritäts-Docstring | Negativ-Check vorgezogen; `pos_90d`-None-Check; Docstring korrigiert |

### Re-Audit-Korrekturen (Fehler, die IM Fixpaket selbst gefunden und sofort behoben wurden)

Das Fixpaket wurde nach der Umsetzung noch einmal adversarial geprüft. Drei Fehler in den eigenen Fixes wurden dabei gefunden und nachgebessert:

1. **M18 v1 war effektiv kaputt:** las nur `ts`; der zweite Cache-Writer (`api.py::_fetch_coingecko_markets`) schreibt aber `cached_at` → Tracker hätte nach jedem api-Scan dauerhaft `None` geliefert (stille Abschaltung). Jetzt beide Felder + mtime, TTL 30 min→3 h (der `ts`-Writer, der BTC-Divergenz-Scan, läuft nur alle 2 h).
2. **L8 v1 konnte crashen:** `basis_dt < purge_cutoff` stand außerhalb des try → ein naiver persistierter Zeitstempel hätte `cleanup_monitoring` per `TypeError: naive vs aware` für den ganzen Lauf abgebrochen. Jetzt im try, naive TS werden tz-aware normalisiert.
3. **N8 v1 war unvollständig:** Guard saß nur im Bear/Turtle-Helfer, die Direkt-Projektions-Callsites blieben offen. Jetzt zentral in der Fraction-Funktion.

---

## 3. Verifikationsstand dieses Fixpakets

- `python -m compileall` über alle geänderten Dateien: **OK**.
- `python -m pytest test_nachaudit_fixes.py`: **22/22 grün**.
- Betroffene Bestands-Suiten (VRVP, Signal-Tracker, Backtest-Math, Early-Movers, Penny, BI-Patterns, AD-Divergenz, Market-Context, Strategy-Scan-Runtime, Crypto-Execution u. a.): **299/301 grün**. Die 2 roten sind reine Umgebungsartefakte der Prüf-Kopie (leere `frontend/index.html`), kein Codefehler.
- **Vor dem Commit Pflicht:** voller lokaler `python -m pytest -q` (erwartet 959) + `python scripts\verify_frontend_bundle.py` + Chrome/Edge-Smoke-Test (keine UI-/Frontend-Datei wurde in diesem Fixpaket geändert, daher genügt der Standard-Smoke-Test).

---

## 4. Deine Aufgabe, Codex — offene Restliste (priorisiert)

Alle folgenden Punkte sind bewusst NICHT launchblockierend, sollten aber vor Forward-Kalibrier-Entscheidungen erledigt werden. Reihenfolge = Empfehlung.

**A. Fehlende Regressionstests (Handbuch §18 verlangt sie, aus der Codex-Runde offen):**
- Gezielte Tests für **M12/M13** (ORB nur geschlossene Kerzen; Failed-Breakout-Geometrie R:R ≥ 1), **M14** (Turtle-Doppelzählung), **M15** (Bear-5m-Frische). Ohne diese Tests ist „durch Regressionstest abgesichert" für diese Fixes unbelegt.
- 8 der 22 Nachaudit-Tests sind reine Source-String-Asserts (Textvorkommen statt Verhalten). Wo möglich in echte Verhaltenstests umbauen (v. a. M9-Kollision, M16, N7, H3, H7).

**B. Restpfade derselben Fehlerklassen:**
- **H3-Restklasse:** `if not day_data: continue` ohne `None`-Unterscheidung existiert noch im BI-AutoTrader-History-Aufbau (`modules/scanners.py`) und in ORB/bg-Callsites — dieselbe Löcher-in-der-Historie-Klasse wie der behobene Grouped-Backtest.
- **M9-Rest:** Perp-Matching (`perp_data.get(symbol)`) prüft die Preisplausibilität des zugeordneten Kontrakts nicht (der 2%-Guard fängt nur den Trigger, nicht die Funding/OI-Scores). Empfehlung: bei Perp-Zuordnung Kontraktpreis gegen CoinGecko-Preis auf < ~2 % Abweichung prüfen, sonst Funding/OI verwerfen.
- **`_tracker_crypto_fetcher`-Rest:** matcht weiterhin nur per Symbol (erster Treffer nach MarketCap). Ideal: per Coin-ID statt Symbol.

**C. Struktur-/Level-Restarbeit:**
- **H6-Rest:** kein Kerzenzeit-Anker fürs Listing-Alter, kein Re-Anker bei nachträglich bekanntem Launch-Timestamp; `listing_age_source_override` kann falsch etikettieren. (Entschärfung: Crypto.com hat keinen Announcement-Fetcher.)
- **H10-Rest:** LVN-Bins werden nicht zu Void-Zonen gemergt → TP-Ziele können auf Binraster-Kanten statt am Volumen-Wiederanstieg liegen; der `[:8]`-Cap kann echte Strukturlevel verdrängen.
- **M11-Rest:** Krypto-Swing-Mails ohne erfolgreichen 5m-Check tragen die Preisbasis vom Scan-Beginn; ein Ticker-Call unmittelbar vor der Trade-Mail fehlt (Penny hat ihn).
- **M21-Rest:** Coverage-Cap-Klippe — eine zusätzlich fehlende Dimension auf Crypto.com ⇒ Hard-Cap 79.

**D. Datenfetcher (L11, komplett offen):** in `modules/data_fetchers.py`: BPIQ-Teilpagination wird als 4h-Cache geschrieben; OHLC-Bars mit `v=0` werden gecacht; stille `min(days, 90)`-Kappung (Wyckoff/Weekly fordern mehr); 4H-Aggregation ohne Session-/Boundary-Alignment.

**E. Kalibrierung (kein Bug, aber vor Scharfschaltung nötig):**
- Netto-R:R-Schwellen (`HARD_MIN_RR`/`PREFERRED_MIN_RR`) wurden nach Einführung der Round-Trip-Kosten NICHT rekalibriert; Spread wird doppelt bestraft (Liquiditäts-Säule + Netto-R:R). Prüfen, ob legitime Grenz-Setups still unter die alten Schwellen rutschen.
- BI-Grade-/Validity-Schwellen wurden nach dem gesenkten Maximum (183→173/168) nicht angepasst.
- Signal 11 („Relative Stärke") hat weiterhin keinen echten Benchmark-Vergleich (misst nur Persistenz; ~40 % reiner Zufallsserien bekommen Punkte).
- Biotech-OVERDUE-Neugewichtung existiert nur in der toten Funktion `_check_clinical_trials`; der BPIQ-Live-Pfad scort Überfälliges unverändert.
- Backtest-Technikscore (`_compute_biotech_technical_from_bars`) ist NICHT paritätisch zum Live-Score (Docstring sagt das jetzt ehrlich) — entweder zusammenführen oder Backtest-Schwellen konservativ interpretieren.

**F. Niedrig:** Tracker bucht Gap-Exits vor dem Fill zu pessimistisch (N4); `_normalize_crypto_bars`-Dedup könnte den Cadence-Validator bei künftig falschem Exchange-Mapping maskieren (N5); market_context-Proxy rechnet bei komplett fehlenden Marktdaten mit Default-Werten (N9); BI-Short-Bonus kann `BI_MaxScore` übersteigen (Anzeige); `invalidated_at` wird in `cleanup_monitoring` gelesen, aber nirgends geschrieben.

---

## 5. Bewusste Verhaltensänderungen (kein Bug — Erwartungsmanagement)

Diese Änderungen aus der Codex-Fixrunde sind gewollt fail-closed, ändern aber sichtbares Verhalten und sollten so kommuniziert werden:
- Wyckoff/Harmonics liefern ohne ausreichende Volumendaten keine bzw. gemalusterte Signale (Krypto-Charts ohne Volumen).
- Penny blockt „Dead-Tape-Ignitions" (≥ 3 von 12 Baseline-5m-Bars mit 0-Volumen) komplett — der klassische Tote-Tape→Zündung-Fall ist strukturell nicht mehr handelbar.
- Netto-R:R zieht Round-Trip-Kosten ab; leere Signallisten können daher „korrekt streng" statt „Scanner kaputt" bedeuten.

---

## 6. Arbeitsregeln (unverändert, für dich besonders relevant)

- Erst Code + Tests lesen, dann ändern. Keine Schwelle pauschal ändern. Keine Gewinnversprechen.
- **Jeder Fix braucht seinen Regressionstest** (Long + Short, Grenzwerte, fehlende Daten), dann Full Pytest + Compile + Bundle-Verify.
- **Neu aus dem Nachaudit gelernt (jetzt Pflichtregeln, siehe Handbuch §21):** API-Bars vor jeder Indikator-Berechnung chronologisch sicherstellen (`sort=desc` dreht Wilder/EMA um); `None`-Werte aus Baseline-Helfern nie ungeprüft vergleichen; Fix-Claims nur mit vollständiger `grep`-Callsite-Liste; „behoben" nie ohne Diff-Beleg behaupten.
- Vor Commit/Push den Betreiber fragen. Nichts Fremdes im Arbeitsbaum zurücksetzen.
