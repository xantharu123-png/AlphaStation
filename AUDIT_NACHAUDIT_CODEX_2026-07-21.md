# Nachaudit der Codex-Fixes — Aktien- & Krypto-Scanner (Alpha Station)

**Datum:** 21. Juli 2026
**Geprüfter Stand:** lokaler Arbeitsbaum vom 20./21.07.2026 (nach Codex-Überarbeitung; Diff gegen den auditierten Stand vom 19.07.: ~9.100 Zeilen über api.py + 15 Module, 2 neue Module `volume_metrics.py`/`performance_metrics.py`)
**Referenzen:** `AUDIT_SCANNER_VOLLAUDIT_2026-07-19.md` (49 nummerierte Befunde), `AUDIT_SCANNER_FIXSTATUS_2026-07-20.md` (Codex-Behauptung: alles behoben, 928 Tests grün)
**Methode:** 6 parallele Verifikations-Agenten mit Alt-Stand, Neu-Stand und vollständigen Diffs; jeder Befund einzeln beurteilt (Runtime-Repros wo möglich, u.a. live gegen die MEXC-API); die schwersten Urteile und alle drei neuen Mittel-Befunde habe ich anschließend selbst zeilengenau nachgeprüft. ~150 der neuen Tests wurden real ausgeführt (modulreine Suiten grün).

---

## 1. Gesamturteil

Codex hat substanziell und überwiegend sauber gearbeitet: **34 von 49 Befunden sind korrekt und vollständig behoben**, darunter alle Schwergewichte K1 (Krypto-Zeitskala), H1 (MEXC-Einheiten), H2 (Tracker-Fill/Win-Rate), H4 (AutoTrader-Order), H5 (RVOL-Zeitnormierung), H8 (patterns-Crash) und H11 (Retest-Pfad). Die Fixes sind handwerklich gut — echte Exchange-Tageskerzen mit Abstands-Validierung, ein zentrales `volume_metrics`-Modul, ein ehrlicher Profit-Factor, ein neuer Out-of-Sample-Split ohne Leakage.

Die Behauptung des Codex-Fixstatus — „**alle** Befunde adressiert, kein bekannter offener Widerspruch" — **stimmt aber nicht**. Real: 12 Befunde sind nur teilweise behoben, 2 gar nicht (M9-Ausschlusslisten/Symbol-Kollisionen und L11-Datenfetcher-Kanten sind byte-identisch zum Altstand, werden im Fixstatus aber als „Behoben" geführt). Dazu kommen **drei neue mittelschwere Fehler aus den Fixes selbst**: eine zeitlich invertierte Wilder-ATR im Bear-Pfad (absteigend sortierte Bars — im Crash-Fall wird die ATR grob halbiert, Stops/TPs der Bear-Setups fallen zu eng aus), ein TypeError im New-Listing-Scanner, der genau die jüngsten/dünnsten Listings still aus der Analyse wirft, und ein vergessener Early-Mover-Pfad, in dem das in H10 beanstandete LVN-Artefakt-Gate weiterlebt. Schließlich verletzt die Umsetzung an mehreren Stellen die eigene Handbuch-Regel „jeder Fix braucht seinen Regressionstest": für H8, H9, M12–M16, H6, M21 und die neue LVN-Regel existiert kein gezielter Test; ein Test zementiert sogar das Verhalten einer toten Funktion.

Kurz: **Der Code ist deutlich besser als vor einer Woche, aber „fertig und launchbar" ist er noch nicht** — es fehlen sieben konkrete Korrekturen (Abschnitt 5, rot markiert) plus die Pflicht-Regressionstests.

---

## 2. Scoreboard: alle 49 Befunde vom 19.07.

Legende: ✅ behoben (verifiziert) · 🟡 teilweise · ❌ nicht behoben · Beleg = v2-Datei:Zeile.

| Fund | Thema | Urteil | Beleg / Rest |
|---|---|---|---|
| K1 | Krypto-Daily keine Daily | ✅ | Backtest: Exchange-1D + Cadence-/Preis-Validierung, Partial-Bar-Drop (api.py:25883–25927); Chart: market_chart-Hourly→UTC-Tage, Median-Gap>6h ⇒ fail-closed (data_fetchers.py:717–810). Latente Lücke s. N5 |
| H1 | MEXC amount24/contractSize | ✅ | api.py:18223–18247; OI ohne contractSize ⇒ 0 + Status (fail-closed); live gegen MEXC nachgerechnet; alle Callsites inkl. NLS umgestellt |
| H2 | Tracker Fill/Win-Rate | ✅ | Fill-Gate Long+Short, Gap⇒Fill=Open, NO_FILL-Buckets, Stop mit realem Gap-R, Win=r_realized>0, Expired im Nenner, ET-Datum (signal_tracker.py:548–631, 978–1001). Randfall N4 |
| H3 | Backtest-History-Löcher | 🟡 | History ungefiltert, Filter nur am Signaltag, Fetch-Retry, None≠{} (backtests.py:159–205; data_fetchers.py:1318–1345). **Rest:** alle 3 Konsumenten machen `if not day_data: continue` — hart gescheiterte Tage fehlen weiter still |
| H4 | AutoTrader-Order | ✅ | STP-LMT-Parent mit Limit-Kappe, Live-Chase-Guard, Fade-Abschlag vor Gate, Partial-Bar-Strip (scanners.py:571–838). Notizen: min_price-Filter beim History-Aufbau bleibt (H3-Klasse); shares=1 ⇒ TP1-Qty 0 |
| H5 | Bear/Crash/Turtle RVOL | ✅ | `_project_us_equity_rvol` an allen 3 Stellen, Baseline ohne Signaltag, Projektion mathematisch korrekt, außerhalb der Session neutral (api.py:1373–1380, 12673, 13033, 5563/5601, 9525–9552). Neue Gegenverzerrung erste ~15 Min s. N8 |
| H6 | Announcement überschreibt Listing-Zeit | 🟡 | Präzedenz gedreht + Alter unbekannt ⇒ fail-closed (NLS:3017–3047, 1719–1740). **Rest:** der im Fixstatus behauptete „Kerzenzeit-Plausibilitätsanker" existiert nicht im Code; spät bekannter launchTime kann einen Announcement-Altbestand nie mehr korrigieren (add_to_monitoring setzt nur bei Neuanlage, NLS:2657); `listing_age_source_override` etikettiert dabei falsch |
| H7 | Biotech Partial-Bar | 🟡 | Strip + ET-Datum + Baseline ohne Selbstkontamination ⇒ uhrzeitinvariant (scanners.py:2502–2536). **Rest:** Quick-Scan übergibt `rvol_direction` weiterhin NICHT (scanners.py:3773–3779, von mir selbst verifiziert) — Distribution-Tage bekommen beim 2h-News-Refresh wieder den vollen RVOL-Bonus (bis +8, Grade-Flip). Der Einzeiler aus dem Fix-Plan fehlt |
| H8 | patterns IndexError + log | ✅ | Alle 5 Stellen index-sauber, `log` definiert; Repro: v1 crasht (IndexError→NameError), v2 liefert korrektes Triangle mit stimmigen Indizes. **Aber: der explizit geforderte Regressionstest fehlt** |
| H9 | Exhaustion-Z-Score | 🟡 | Vorzeichen-Kern behoben; nachgerechnet: 7d+25/1h−4 ⇒ +10-Zweig feuert (scorers.py:1565–1575). **Rest:** 110er-Nominalsumme/Docstring, tote Parameter `rvol`/`prev_vol_24h`; kein Test für die Downside-Zweige |
| H10 | LVN-Kanten als Barrieren | 🟡 | Modul-Pfad: Barrier-/Stop-Gate nur noch strukturelle Level (vrvp_levels.py:265–345, Repro ✓). **Rest:** kein LVN-Zonen-Merge — TP1/TP2 können weiter auf Artefakt-Kanten mitten im Volumen-Void landen, `[:8]`-Cap verdrängt echte Level; und der Early-Mover-api-Pfad wurde vergessen (s. N3, MITTEL) |
| H11 | Retest-Einstieg tot | ✅ | Promotion setzt LONG_TRIGGER + RETEST_CONFIRMED; Mail-Gate, kanonische Decision, GET, Downgrade — komplette Kette runtime-verifiziert (api.py:4318–4331, 2119, 8144–8155) |
| M1 | Kein Mindest-Risk/R:R-Cap | ✅ | Noise-Floor (Spread/ATR/0,1%) + `implausible_live_rr`; Repro 100/99,99 ⇒ NO_TRADE; Short gespiegelt (trade_health.py:381–390, 460–467) |
| M2 | round_trade_price Mikro-Preise | ✅ | 6 signifikante Stellen; 2,2e-9 bleibt 2,2e-9 (vrvp_levels.py:38–42). Folgepunkt N10 (Dedupe-Toleranz-Floor) |
| M3 | Stop-Tightening-Anker | ✅ | Nur POC/VAL/HVN-LOW, Puffer 0,35×ATR, Kompression max 20% (vrvp_levels.py:280–290, 455–468) |
| M4 | ATR-Vertrag | ✅ | `calculate_wilder_atr` kanonisch, Sanity-Clamp, alle 5 api-Callsites + NLS (`ath−current` entfernt) |
| M5 | live_rr-Fallback | ✅ | Unvollständig ⇒ `live_rr=None`, `planned_rr` separat (trade_health.py:166–170, 764–770) |
| M6 | BTC fail-open | ✅ | Gate dreiwertig, `tailwind`-Default False, Fehler ⇒ kein Long, Explosion degradiert (api.py:2029–2069, 19778–19809). Restnotiz: Scan-Extraktion selbst noch zweiwertig, praktisch durch data_warning-Gates abgedeckt |
| M7 | GET-Downgrade Early Movers | ✅ | In get_early_movers + Mail-Pfad aktiv (api.py:19466, 20104–20143). 2 Konsistenzreste s. N6/N7 |
| M8 | Stop-Breach im Trigger | ✅ | Harter Block + Distanz-Leiter mit Ober-/Untergrenze + `entry_distance_valid` (api.py:3122–3131, 3204–3212) |
| M9 | LSD/Stable-Lücken, Symbol-Kollisionen | ❌ | Listen byte-identisch; Runtime-Repro: JITOSOL/MSOL/BNSOL/RLUSD/SOLVBTC laufen durch; „AI"-Kollision erzeugt Cross-Coin-Konfluenz (+10) und falsche Zuordnung (api.py:17660–17673, 19173–19183, 18579–18588). Fixstatus-Claim falsch |
| M10 | Phase 1 für Dump-Coins | ✅ | Phase 0 „Weak/Downtrend": Score×0,45, HIGH-Risk, NO_TRADE, kein Boost, aus Anzeige-Partition raus (api.py:18432–18435, 19207–19225) |
| M11 | Mail-Preisbasis-Alter | 🟡 | Per-Row `checked_at` statt ≈0s, 2%-Preis-Guard, Exchange-Close ersetzt CG-Preis (api.py:2072–2091, 3062–3077). **Rest:** Rows, deren 5m-Check vor dem Preis-Guard scheitert, behalten die Scan-Start-Basis; kein Ticker-Call vor Krypto-Swing-Mail (Penny hat ihn) |
| M12 | ORB offene Kerze | ✅ | Detection + Volume-Confirmation nur auf geschlossenen 5m-Kerzen (api.py:23409–23512) |
| M13 | ORB Failed-Breakout R:R 0,63 | ✅ | Stop hinter Reversal-Exkursion, `trade_geometry` + blended R:R ≥1,25 sonst Delisting (api.py:23514–23561) |
| M14 | Turtle-Doppelzählung | ✅ | „Entry-Qualität" jetzt Candle-Akzeptanz (Close-Position/Retest/Body), unabhängig von breakout_pct (api.py:12704–12720) |
| M15 | Bear 5m-Halt-Staleness | ✅ | Bar-Alter aus geschlossener Kerze, >15 Min ⇒ fail-closed Block, Crash erbt (api.py:5692–5713) |
| M16 | Inverse-ETF-Erkennung | 🟡 | Hebelerkennung repariert (12/17 warnen korrekt). **Rest:** `"inverse"`-Literal weiterhin toter Code — 1x-Shorts (SH/PSQ/DOG/RWM) und VIXY warnen nie; der empfohlene `"short"`-Check fehlt (api.py:12884) |
| M17 | Backtest-Statistik | ✅ | Exit-Reason-Mapping, avg_loss=0, PF „INF" ehrlich (performance_metrics.py), Dedup-Key mit Strategie, chronologische Equity/DD, prev_high-Gap⇒Open-Fill, Tag-1-Simulation, Biotech signal_date — alle 8 Punkte (backtests.py:1597–1643, 215, 818–821, 1299–1324, 565, 1044–1087). Neu und sauber: OOS-Split mit Mindest-Samples, wertet nie auf |
| M18 | Tracker-Cache-TTL + UTC | 🟡 | ET-Handelsdatum ✓ (signal_tracker.py:522). **Rest:** bg_service.py ist byte-unverändert — `_tracker_crypto_fetcher` liest den CoinGecko-Cache weiterhin ohne jede ts-Prüfung, Symbol-only-Match inklusive (bg_service.py:1298–1329, von mir verifiziert). Fixstatus-Claim „Cache-TTL" ist falsch |
| M19 | Event-Risiko Mitternacht-UTC | ✅ | Datum-only = ET-Tagesfenster, ganztägig aktiv, Verfall +2h nach Tagesende (market_context.py:385–401) |
| M20 | Penny Open-Projektion + eingefrorenes now | ✅ | Gewählt wurde die dokumentierte Option B: keine Projektion in Minute 1–14 (api.py:21829–21849); volle Re-Validierung vor Mail mit frischem now/Quote/geschlossener 5m-Kerze/Drift/Netto-R:R (api.py:21892–21975). Bekannte Restlücke der Option: PM-Volumen bleibt nach Minute 15 im Zähler |
| M21 | NLS-Normalisierung + Cache-Diff | 🟡 | Normalisierung über verfügbare Dimensionen + Coverage-Cap, Cache-Gate ≥0,8 ✓ (NLS:1810–1841, 1110–1145). **Rest:** neuer Crash-Bug in der Verfügbarkeitstabelle (s. N2, MITTEL) + Cap-Klippe: eine zusätzlich fehlende Dimension auf Crypto.com ⇒ Hard-Cap 79 |
| M22 | patterns-Sammelfund | 🟡 | 4/5 ✓: Flag-Pole-Hardgate, Signal 16 semantisch korrekt gedreht (max 183→173/168, nachgerechnet), S20-Krypto-Dedup, C&H-Chart rim-basiert (Repro: Breakout 100,0 statt 90,9). **Rest:** Signal 11 weiter ohne Benchmark — Monte-Carlo: 40,0% reiner Zufallsserien bekommen Punkte (vorher 42,4%), nur das Gewicht wurde halbiert |
| M23 | VWAP-Bänder + ATR-Zoo | ✅ | VWAP exakt TV-Formel (Beispiel: std 4,0000 ✓); kanonische Wilder-ATR mit ~20 Callsites, SMA-ATR ersetzt, `get_volatility_regime` entfernt. Restinseln nur SMC-intern/Chart-Overlay (patterns.py:3556/3687/3785, api.py:16400) |
| L1–L7 | or-Falle Turtle, Fear-Sprung, Sweep-Clamp, Phasen-Quote, Biotech-days, Funding-Text, VRVP-min_bars | ✅ | Alle verifiziert (u.a. api.py:9530–9537, 24053, 18692, 19342–19366, 2344/2356; NLS:1613, 2247) |
| L8 | NLS Expiry/Cleanup | 🟡 | Zeit-Expiry läuft jetzt ohne Ticker ✓ (NLS:3092–3118); Monitoring-JSON wird weiterhin nie bereinigt (cleanup_monitoring byte-identisch) |
| L9/L10 | SELL-Erkennung, News-Tupel | ✅ | `\bSELL\b`-Token (trade_levels.py:63–66); `return []` vereinheitlicht (data_fetchers.py:1143/1185) |
| L11 | Datenfetcher-Kanten | ❌ | BPIQ-Partial-Cache, v=0-Bars-Cache, stille 90d-Kappung, 4H-Chunking ohne Alignment — alle 4 byte-identisch zu v1. Fixstatus-Claim falsch |
| L12 | market_context Defaults | 🟡 | VIX/Breadth-Doppelzählung entfernt ✓; Default `data_status="ok"` bleibt Aufrufer-Konvention; neuer Proxy-Pfad rechnet mit Default-Werten (N9) |
| L13 | Root-Duplikate | ✅ | volume_profile.py = harter ImportError-Stub; new_listing_scanner.py = stiller Re-Export-Shim (kein Drift-Risiko mehr, aber „harter Stub" stimmt nur für einen) |
| L14 | Legacy-Anzeige-Pfade | — | Unverändert; weiterhin ohne produktiven Aufrufer (OBV-Trend-Formel inklusive — Konsument ist tot). Unkritisch |

**Bilanz: 34 ✅ · 12 🟡 · 2 ❌ · 1 n/a.**

---

## 3. Neue Befunde aus den Codex-Änderungen

### N1 · [MITTEL, BUG] Bear-VRVP-ATR rechnet auf absteigend sortierten Bars — Wilder-Glättung zeitlich invertiert
api.py:13012 holt die Bear-History mit `"sort": "desc"`, api.py:13172–13175 füttert sie unsortiert in `calculate_wilder_atr`, das chronologische Reihenfolge voraussetzt (`previous_close = parsed[i-1]` — bei desc-Daten ist das der **Folgetag**). Der Seed besteht aus den neuesten TRs, die Glättung läuft dann rückwärts in die Vergangenheit; Repro: 55 ruhige Tage + 5 Crash-Tage ⇒ korrekt 4,87, desc-Pfad 2,20 (**0,45×**). Genau im Bear-Kernfall (frischer Crash) wird die ATR massiv unterschätzt — Stop-Puffer (`atr×0,35`) und TP-Mindestabstand (`atr×0,70`) der Bear-Setups fallen zu klein aus. Alle übrigen Wilder-Callsites wurden geprüft und sind chronologisch sauber; nur dieser Pfad ist betroffen. **Fix:** Bars vor dem Aufruf chronologisieren (`sort=asc` oder `reversed`); defensiv: `calculate_wilder_atr` nach Timestamp sortieren lassen.

### N2 · [MITTEL, CRASH] NLS-Exhaustion wirft TypeError bei None-Volumen-Baseline — dünnste Listings verlieren still die komplette Analyse
new_listing_scanner.py:1812: `"volume_decline": (20, vol_first > 0)` — `vol_first` kommt jetzt aus `historical_volume_baseline(...)` und kann None sein (Repro: Listing mit 5 Kerzen, eine mit 0-Volumen ⇒ TypeError). Der per-Symbol-try/except schluckt die Exception ⇒ das Symbol wird kommentarlos übersprungen: kein Score, kein Signal, keine Watchlist — ausgerechnet die jüngsten/illiquidesten Listings, das Primärziel des Scanners. **Fix:** `bool(vol_first and vol_second)` (behebt zugleich, dass nur `vol_first` geprüft wird).

### N3 · [MITTEL, LOGIK] Early-Mover-Overhead-Resistance akzeptiert weiterhin LVN-Artefaktkanten — der H10-Kernfehler lebt im aktivsten Krypto-Pfad weiter
`_early_mover_vrvp_from_bars` exportiert alle Level inkl. `vrvp_lvn_*` (Gewicht 42), `_annotate_early_mover_overhead_resistance` (api.py:4004–4027) nimmt das erste Level über Entry ohne Kind-/Gewichtsfilter, `_apply_trade_barrier_gate` downgraded damit actionable Rows auf `WAIT_FOR_BREAK_RECLAIM`. Bei frisch gepumpten Coins ist das erste Level über Entry im 5m-Profil oft die Unterkante eines Volumen-Voids — ein Beschleunigungs-Vakuum wird als Widerstand behandelt (exakt die in H10 beanstandete Invers-Logik). Der Modul-Pfad filtert korrekt; diese api-Callsite wurde übersehen. **Fix:** Level mit Source `vrvp_lvn_*` bzw. Gewicht <56 in der Annotation überspringen.

### N4–N11 · [NIEDRIG] Kompakt
**N4** Tracker: liegt der Bar-Open unter dem Stop, wird der Exit auf den prä-Fill-Open gebucht ⇒ r < −1 chronologisch unmöglich (signal_tracker.py:621–624; analog backtests.py:1375–1381) — zu pessimistisch, Gap-Exit nur wenn Fill selbst am Open lag. **N5** `_normalize_crypto_bars`: by-date-Dedup kollabiert Sub-Daily-Kerzen zu Pseudo-Tageskerzen, der Cadence-Validator sieht danach 1-Tages-Abstand — latent, bis ein Exchange-Timeframe-Mapping bricht; Duplikat-Datum sollte Reject sein (api.py:25876–25917). **N6** GET-Downgrade lässt `entry_status="JETZT_TRADEN"` stehen (api.py:20125–20142). **N7** get_early_movers zählt `trade_now_count` vor dem Downgrade — Statistik ≠ ausgelieferte Rows (api.py:19465–19466; crypto_explosion macht es richtig). **N8** Erste ~15 Handelsminuten: Bear/Turtle-Projektion multipliziert Premarket-haltiges `day.v` mit bis zu ×62 — Umkehrung von H5; der Penny-Guard (`fraction=1.0` vor Minute 15) fehlt hier (api.py:13033, 12673). **N9** market_context-Proxy-Pfad rechnet bei komplett fehlenden Marktdaten mit Default-Werten (ad_ratio 1,0) ⇒ „weniger Daten = weniger Risiko" + falsches Basis-Label (market_context.py:455–472). **N10** vrvp-Dedupe-Toleranz `max(entry*0.0012, 1e-9)`: der absolute Floor ist bei Sub-Nano-Coins ~45% des Preises ⇒ Levellisten kollabieren auf 1 Level (vrvp_levels.py:127) — war vorher durch M2-Rundung maskiert. **N11** Kleinvieh: `int(avg_vol_20)`-TypeError-Falle bei None-Baseline im Biotech-Score (scanners.py:2679, äußeres except macht daraus „Technik 0/Chart perfekt"); neue `_vrvp_atr` im BI-Scan läuft auf ungestrippten Bars (scanners.py:1634); verwaistes `_intraday_evf` (api.py:23314); Gap-Recovery-Toleranz wird 1,0 bei <15 Bars (analysis.py:93); BI-Grade-/Validity-Schwellen wurden nicht an die gesenkten Maxima 173/168 angepasst (stille Verschärfung, patterns.py:2083–2104); Biotech-„OVERDUE⇒0"-Neugewichtung existiert nur in der toten Funktion `_check_clinical_trials`, der BPIQ-Live-Pfad scort Überfälliges unverändert — und ein neuer Test zementiert das tote Verhalten (scanners.py:193–206 vs. data_fetchers.py:1423).

**Bewusste Verschärfungen, die Kalibrierung brauchen (keine Bugs):** Netto-R:R zieht jetzt Round-Trip-Kosten ab, aber `HARD_MIN_RR=1.0`/`PREFERRED=1.5` blieben unverändert ⇒ Grenz-Setups mit Spread-Daten rutschen still unter die alten Schwellen, Spread wird zudem doppelt bestraft (Liquidity-Säule + Netto-R:R). Penny: ≥3 von 12 Baseline-Bars mit 0-Volumen ⇒ Kandidat komplett geblockt — „Dead-Tape-Ignition" ist damit strukturell nicht mehr handelbar (dokumentieren oder Fenster verlängern). `volume_metrics`-Baselines schließen 0-Volumen-Tage aus dem Nenner aus ⇒ Dollar-Vol-Gates minimal lockerer.

---

## 4. Bewertung des Codex-Fixstatus-Dokuments

Das Dokument ist in der Substanz überwiegend richtig, aber als Abnahmebeleg nicht verlässlich: (1) **Falsche „Behoben"-Claims:** M9 und L11 sind unverändert; die M18-Cache-TTL existiert nicht (bg_service.py wurde gar nicht angefasst); der behauptete „Kerzenzeit-Plausibilitätsanker" (H6) kommt im Code nicht vor; „OVERDUE nur Warnkontext" ist nur in toter Funktion umgesetzt; „harte Kompatibilitäts-Stubs" trifft nur auf eine von zwei Root-Dateien zu. (2) **„Durch Regressionstest abgesichert" ist für viele Fixes unbelegt:** für H8 (im Fix-Plan ausdrücklich gefordert), H9, M12–M16, H6, M21, die LVN-Regel und L1/L2/L5 existiert kein gezielter Test; einzelne Tests suggerieren mehr Abdeckung als vorhanden (der Stable-Filter-Test prüft nur USDE/PAXG — genau die offenen M9-Assets fehlen). (3) Die „928 Tests grün"-Zahl konnte ich aus meinem Audit-Snapshot heraus nicht unabhängig reproduzieren (Snapshot enthielt nicht alle unveränderten Module); alle hier lauffähigen modulreinen Suiten (~150 Tests) waren grün — kein Widerspruch, aber auch kein unabhängiger Beleg.

---

## 5. Launch-Ampel — was vor einer Veröffentlichung zu tun ist

🔴 **Blocker (vor Launch/Verkauf fixen; alles kleine, chirurgische Eingriffe):**
1. N1 Bear-ATR-Sortierung (Einzeiler + defensiver Sort in `calculate_wilder_atr`).
2. N2 NLS-TypeError (`bool(vol_first and vol_second)`).
3. N3 Early-Mover-LVN-Filter in `_annotate_early_mover_overhead_resistance`.
4. H7-Rest: `rvol_direction` im Biotech-Quick-Scan (der Einzeiler aus Fix-Plan Prio 6).
5. M9: Ausschluss-Terms („staked sol", RLUSD, solvBTC …), Dedupe-Key `(symbol, coin_id)`, Perp-Match-Plausibilisierung — falsche Asset-Zuordnung ist ein Vertrauensrisiko gegenüber zahlenden Kunden.
6. M18-Rest: ts-Prüfung im `_tracker_crypto_fetcher` (bg_service.py) — der Track-Record ist Verkaufsargument und darf nicht gegen tagealte Preise bewerten.
7. Pflicht-Regressionstests nachziehen (H8-Dreieck mit exakt 3 Swings, H9-Downside-Zweige, M12/M13-ORB, M15, M16, N1–N3) — Handbuch §18 verlangt das ausdrücklich.

🟡 **Zeitnah (nicht launchblockierend, aber vor Kalibrier-Entscheidungen):** H3-Rest (`None`-Tage als failed_days ausweisen), H6-Rest (Kerzen-Anker + Re-Anker + Label), H10-Rest (LVN-Zonen-Merge, sonst bleiben TP-Ziele auf Binraster-Kanten), M11-Rest (Ticker-Call vor Krypto-Swing-Mail), M16-Rest („short"/„vix"-Check), M21-Cap-Klippe, N4–N11, Netto-R:R-Schwellen und BI-Grade-Schwellen einmal gegen Forward-Daten rekalibrieren, L8-Cleanup, L11-Kanten, Biotech-News-Reihenfolge und BI-Short-Bonus>MaxScore (siehe Abschnitt 6).

🟢 **In Ordnung / nur dokumentieren:** die 34 verifizierten Fixes; die bewussten Fail-closed-Verschärfungen (Wyckoff ohne Volumen, Harmonics-Malus, Penny-Baseline) als Verhaltensänderungen im Handbuch festhalten.

---

## 6. Selbstkorrektur — was ich an meiner eigenen Arbeit markiere

Auf die Frage, was ich vor einer Veröffentlichung an meiner eigenen Arbeit korrigieren würde: Die Befunde vom 19.07. haben der Nachprüfung standgehalten — kein einziges Finding musste zurückgenommen werden, alle 49 Urteile bestätigt. Vier Punkte hätte ich aber besser machen können, und zwei davon hatten reale Folgen:

1. **Unvollständige Callsite-Listen in Fix-Empfehlungen.** Bei H10 habe ich das Barrier-Gate im Modul benannt, aber nicht alle Konsumenten aufgezählt — Codex hat exakt den nicht gelisteten Early-Mover-Pfad übersehen (heute N3). Lehre: Fix-Empfehlungen künftig immer mit vollständiger `grep`-Callsite-Liste.
2. **Vier Nebenbefunde der Teilaudits nicht nummeriert.** Biotech-News-Reihenfolge (2↑/3↓ ⇒ +4 statt −4), BI-Short-Bonus kann BI_MaxScore übersteigen, die `pos_90d`-or-Falle und der falsche Paritäts-Docstring des Backtest-Technikscores standen in den Teilaudits, aber nicht in meiner nummerierten Liste — folgerichtig hat Codex sie nicht gefixt. Sie sind jetzt in der 🟡-Liste nachnummeriert. Alle vier wurden im Nachaudit erneut verifiziert und sind weiterhin offen.
3. **Alternativ-Optionen ohne Restlücken-Hinweis.** Bei M20 habe ich „Projektion erst ab Minute 15" als gleichwertige Alternative angeboten, ohne dazuzusagen, dass dann PM-Volumen nach Minute 15 im Zähler bleibt; bei H5 fehlte der Hinweis, dass `day.v` Premarket enthält (heute N8). Beide Restlücken waren absehbar.
4. **M18 zu unauffällig platziert.** Dass die Cache-TTL-Hälfte in `bg_service.py` liegt, stand nur im Fließtext — die Datei tauchte im Fix-Plan nicht auf, und Codex hat sie komplett unangetastet gelassen.

Transparenz zur Methode: Die „928 Tests grün"-Abnahme habe ich nicht unabhängig reproduziert (mein Audit-Snapshot umfasste nur geänderte Dateien plus Module — Tests, die das komplette Repo brauchen, liefen nur teilweise). Die Urteile in diesem Report stützen sich deshalb auf Code-Lektüre, eigene Runtime-Repros und ~150 real ausgeführte Modul-Tests, nicht auf die Suite-Zahl.

---

## 7. Fazit

Codex hat den Maschinenraum in kurzer Zeit erheblich verbessert — 34 von 49 Befunden sind sauber weg, darunter alles, was Backtest-Zeitskala, Einheiten und Track-Record-Grundlogik betraf. Aber der Fixstatus-Report übertreibt den Fertigstellungsgrad („alles behoben" stimmt für 12 Befunde nur teilweise und für 2 gar nicht), drei der Fixes haben neue mittelschwere Fehler eingeschleppt, und die Regressionstest-Pflicht des eigenen Handbuchs wurde für viele Fixes ignoriert. Die 🔴-Liste oben ist ein überschaubares Paket — größtenteils Einzeiler plus Tests. Danach ist der Scanner-Kern aus meiner Sicht rechnerisch launchfähig; die kommerziellen Blocker aus Handbuch §24 (Secrets-Rotation, Stripe-Live, Staging-Verifikation, Forward-Validation) bleiben davon unberührt bestehen.
