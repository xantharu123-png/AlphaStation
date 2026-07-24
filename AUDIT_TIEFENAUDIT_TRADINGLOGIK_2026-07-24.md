# Tiefenaudit Trading-Logik — alle Scanner, Zeile für Zeile

**Datum:** 24. Juli 2026 (Nachmittag) · **Stand:** `89334ef` · **Anlass:** Vollaudit der Trading-Entscheidungslogik, ergänzend zum Rechenkern-Audit vom selben Tag.
**Methode:** Vollständige Lektüre der entscheidungstragenden Pfade (kein Sampling): `penny_stock_scanner.py` (1.408 Z. komplett), `simulate_trade` in `backtests.py`, Fill-/Exit-Logik in `signal_tracker.py`, `validate_flag_pattern` + BI-Aggregation in `patterns.py`, `calculate_setup_score`/`_z_score`/Exhaustion-Caps in `scorers.py`, `trade_health.py` Hauptbewertung, Verifikation der N11-Restpunkte in `scanners.py`.

---

## 1. Ergebnis in Kürze

Die Entscheidungslogik ist **im Kern sauber und traderisch erwachsen**: 18 harte Gates vor `JETZT_KAUFEN`, Stop-first-Konvention in allen drei Simulations-/Evaluierungspfaden, Gap-Fills am Open statt am Wunschpreis, kein Look-Ahead im Backtest, Lifecycle-Zustandsmaschine ohne Widersprüche.

**Zwei MITTEL-Befunde** (keine Rechenfehler, aber Methodik-/Kalibrier-Inkonsistenzen mit Außenwirkung) und vier Niedrig-Notizen.

---

## 2. MITTEL-Befunde

### T1 — Track-Record bucht TP2 mit vollem R, das empfohlene Management verdient nur den halben Blend

**Beleg:** `signal_tracker.py:686` bucht bei `TP2_HIT` `r_realized = signed_r(tp2)` — das **volle** TP2-Geometrie-R. Der Backtest (`backtests.py:1410/1417/1454`) und die Penny-Empfehlung („TP1 = 50 % Teilverkauf") rechnen dagegen **50/50 geblendet**: `(tp1 + tp2)/2`.

**Konsequenz:** Für jedes TP2-Outcome ist das getrackte R höher als das R, das ein Kunde nach der eigenen Handlungsempfehlung der App realisiert hätte. Beispiel TP1=2R/TP2=4R: Tracker bucht 4.0R; das 50/50-Management realisiert 3.0R; Stop nach TP1 bucht der Tracker −1R, das Management nur −0.5R+1R=+0.5R … — die Stichproben vergleichen Äpfel (Level-Qualität) mit Birnen (Management-Ergebnis). Da der Track-Record ein Verkaufsargument ist, gehört hier methodische Klarheit her.

**Konvention vs. Absicht:** Die Buchung ist im Modul-Docstring dokumentiert („Geometrie-R von TP2") — also kein versteckter Bug, sondern eine definierte, aber produktinkonsistente Konvention.

**Empfehlung (Entscheidung beim Betreiber, bewusst NICHT einseitig geändert):**
- Option A (ehrlichste): `r_realized = 0.5·r_tp1 + 0.5·r_outcome` buchen, sobald der Plan 50/50 empfiehlt — Track-Record = befolgbares Management.
- Option B: Weiter Level-R buchen, aber im Reporting als „Signal-Level-R (unmanaged)" labeln und zusätzlich `r_managed_50_50` ausweisen.

### T2 — Krypto-Grade-Schwellen wurden der V3.3-Reskalierung nie angepasst: Krypto jetzt strenger als Aktien

**Beleg:** `patterns.py:2056–2066` (Krypto) vs. `2086–2095` (Aktien). Die Kommentarkette belegt die Historie: V2.9 setzte Krypto-Schwellen „~15 % unter Aktien" (S=95 bei damaligem Aktien-S=113 ✓ konsistent). V3.3 senkte die **Aktien**-Schwellen um −28 Punkte (S: 113→85), **Krypto blieb unangetastet**.

**Konsequenz (nachgerechnet):**

| Grade | Aktien (Score/Max) | Krypto (Score/Max) | Relativ |
|---|---|---|---|
| S | 85/173 = 49,1 % | 95/168 = 56,5 % | Krypto **+7,4 pp strenger** |
| A | 71/173 = 41,0 % | 85/168 = 50,6 % | **+9,6 pp** |
| B | 57/173 = 32,9 % | 72/168 = 42,9 % | **+10,0 pp** |
| C | 55/173 = 31,8 % | 60/168 = 35,7 % | +3,9 pp |

Das widerspricht der dokumentierten Kalibrier-Absicht („Crypto ~15 % unter Aktien, weil weniger Volume-Signale erreichbar"). Krypto-Setups bekommen systematisch schlechtere Grades als gleichwertige Aktien-Setups.

**Empfehlung:** Schwellen neu verankern (z. B. Krypto ≈ Aktien −15 % relativ zum eigenen Maximum: S≈72, A≈60, B≈48, C≈47 — oder Aktien-Konvention prozentual spiegeln). **Bewusst nicht einseitig geändert:** Grade-Schwellen sind Produkt-/Kalibrier-Entscheidungen und gehören mit Forward-Daten abgeglichen.

---

## 3. NIEDRIG-Notizen

| # | Befund | Ort |
|---|---|---|
| N1 | **Spread fließt vierfach ein:** `spread_score` (+10) in Setup **und** Entry, Dump-Risk-Penalty (−12) sowie Netto-R:R. Illiquide Namen werden vierfach bestraft — legitime Härte, aber undokumentiert und bei der Kalibrierung zu berücksichtigen. | `penny_stock_scanner.py:940–945, 956–961, 979` |
| N2 | **Zwei EMA-Konventionen:** Penny-Modul nutzt rekursiven Seed (`cleaned[0]`), `indicators.py` den SMA-Seed (TV-konform). Beide valide; bei kurzen 5m-Serien messbar unterschiedlich. Kanonisieren. | `penny_stock_scanner.py:75–83` vs. `indicators.py:497–510` |
| N3 | **Stale Kommentare:** „max_score 188" (ist 173/168) in `patterns.py:2074`; „Z≥2 = 95 % Konfidenz" (gilt nur unter Normalverteilung) in `scorers.py:43`. | s. links |
| N4 | **Flag-„Fahnenstange" = volle Tagesrange:** Ein Doji-Tag mit riesigen Wicks erzeugt eine riesige Pole ohne echten Move → Retracement-Verhältnisse verzerrt. Randfall, fail-safe (Retracement wirkt dann flacher als real → eher mehr Punkte). | `patterns.py:166–176` |

---

## 4. Was die Tiefenprüfung explizit als korrekt bestätigt

**Penny-Lifecycle (komplett gelesen):**
- Subscore-Arithmetik: Setup = 25+20+25+10+10+10 = 100 ✓; Entry = 10+50+15+15+10 = 100 ✓; Clamps an jedem Punkt.
- 18 harte Gates (Frische ≤ 360 s, abgeschlossene 5m-Kerze, $-Vol ≥ 500 k/3 M proj., RVOL ≥ 1,5, Spread bekannt & ≤ 120 bps, Order-Minimum $250, Setup ≥ 70, Entry ≥ 75, Trade ≥ 80, Dump ≤ 45, SEC-Daten vorhanden, Drift ≤ 0,35 R, Live-Preis hält Struktur) — alle fail-closed.
- TP1-Ratchet `active_stop = max(original_stop, min(mark·0,998, breakeven_incl_costs))` — mathematisch korrekte Einbahnstraße.
- Replay: Stop-first im Bar, Gap-Fill `min(stop, open)`, Kosten beidseitig, TP1 50 % → Break-even. Konservativ in jeder Ambiguität.

**Backtest `simulate_trade`:** Kein Look-Ahead (next_open / at_close+1 / prev_high-Trigger mit Gap- und Touch-Fill), Stop-first, TP2 frühestens Folgebar nach TP1, 0,05 % Slippage + 0,2 % Fees im R, −2R-Gap-Cap, Blending exakt 50/50. Ehrliche Maschine.

**Signal-Tracker:** Fill-Modell broker-realistisch (Gap über TP1 ⇒ NO_FILL; Open ≥ Entry ⇒ Fill am Open; Limit-Touch ⇒ Fill am Limit), Same-Day-Stop/TP ⇒ Stop first, Stop-Gap ⇒ Exit am Open (R < −1 möglich). UNTRACKED fließt nicht in Win-Rate ein. Einziger methodischer Makel: T1 oben.

**BI-Scanner:** Vier Anti-Garbage-Gates (Pump >8 % und >5σ, Range-Breakdown, Recent-Direction long/short) sind statistisch sauber konstruiert; Score wird VOR Confidence/Grade geclamppt (kein >100 %-Pfad). Flag-Validator: symmetrisch long/short, Mcap-gestaffelte Krypto-Schwellen, alle drei Kriterien Pflicht (fail-closed).

**Setup-Score (scorers.py):** Kategoriesumme exakt 100, Kerze nie negativ (`max(0, …)`), Long/Short gespiegelt, High-ATR-„Early-Entry"-Falle entschärft.

**N11-Verifikation (Restliste Vor-Audit):** `int(avg_vol_20 or 0)` ✓, `rvol_direction` im Quick-Scan ✓, H-2-Formel-Absicherung Short-Geometrie ✓ — alle gefixt.

---

## 5. Gesamturteil nach drei Audit-Wellen heute

| Ebene | Urteil |
|---|---|
| Rechenkern (Indikatoren, R:R, Statistik) | ✅ bewiesen korrekt (43 numerische Prüfungen, als Suite verankert) |
| Entscheidungslogik (Gates, Lifecycle, Fills, Simulation) | ✅ sauber, konservativ, fail-closed |
| Methodik-Konsistenz (Track-Record ↔ Empfehlung ↔ Backtest) | 🟡 T1 — eine Buchungs-Konvention klären |
| Kalibrier-Konsistenz (Krypto ↔ Aktien Grade-Schwellen) | 🟡 T2 — einmal neu verankern |
| Sprach-/Kommentar-Präzision | 🟢 4 Kleinigkeiten |

**Die App rechnet richtig. Die zwei 🟡-Punkte sind keine Fehler im Rechnen, sondern Stellen, an denen zwei ehrliche Teilsysteme verschiedene Wahrheiten erzählen — genau die Sorte Befund, die man nur findet, wenn man alles liest.**
