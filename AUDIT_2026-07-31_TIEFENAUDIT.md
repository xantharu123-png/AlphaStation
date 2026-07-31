# Tiefen-Audit 2026-07-31 — „Solche Fehler dürfen nicht mehr passieren"

**Anlass:** BHC-Mail (16:09 MESZ): „🚨 JETZT SWING" bei bereits gelaufenem
3-Tage-Move (+38 % ≈ 8,8 ATR), Zeitstempel-Verwirrung UTC vs. Postfach.
**Betreiber-Auftrag:** Backtest der Gates gegen die eigenen Signale +
intensives Gesamt-Audit + strukturelle Verankerung.

**Methode:** Code-Ebene, jeder Befund mit Test belegt. Keine Bauchgefühle.

---

## 1. Fehlerklassen-Analyse (warum passierte der BHC-Fehler?)

Drei unabhängige Fehler wirkten zusammen — jeder bekam eine eigene
Gegenmaßnahme:

| # | Fehlerklasse | Konkret | Gegenmaßnahme |
|---|---|---|---|
| K1 | **Anker-Fehler** | Gates maßen nur den heutigen Tag (+5,1 % = 1,2 ATR = „frisch"); der Move lief über 3 Tage (8,8 ATR) | Mehrtages- + Vortag-Anker (Long **und** Short) |
| K2 | **Registrierungs-Fehler** | Decision-Mapping ist eine explizite Menge, kein Suffix — ein neuer Grund ohne Registrierung wird WATCH statt NO_TRADE | **Registry-Guard-Test** (jeder Grund muss gemappt oder whitelistet sein) |
| K3 | **UX-Ehrlichkeit** | Betreff „JETZT SWING" pauschal; Zeitstempel nur UTC / falsches CET-Label | „SWING"-Betreff; duale Zeitstempel überall + Guard |

---

## 2. Befunde & Fixes (alle produktiv, alle getestet)

- **F1 — Long-Mehrtages-Anker fehlte:** `|Change_5D|/ATR% ≥ 5` → wait_retest,
  `≥ 7` → no_chase. Vortag ≥ 2,5 ATR + heute grün + ≤ 1 % am Tageshoch →
  Tag-2-Top-Kauf geblockt. (BHC: 8,8 ATR → NO_TRADE.)
- **F2 — Short-Seite symmetrisch fehlte (Audit-Hauptbefund):** Dieselbe
  Lücke für Abwärts-Moves — -38 % über 3 Tage, heute -3 % = „frischer"
  Short auf dem Boden. Gespiegelt gefixt (`swing_short_multi_day_*`,
  `swing_short_prevday_run_bottom_entry_*`).
- **F3 — Zwei Registrierungspfade nötig:** Neuer no_chase-Grund muss in
  `_alert_decision_from_reasons.no_trade_markers` **und** im Score-Cap-Set
  stehen. Beides erledigt; der Registry-Guard (K2) prüft ab jetzt jeden
  Grund automatisch — der Ist-Stand (81 bewusste WATCH-Gründe von 150) ist
  bidirektional eingefroren.
- **F4 — Betreff:** `swing_trade` = „🚨 SWING: " (ohne JETZT); „JETZT" nur
  noch Intraday-`trade` mit 15-Min-Frische-Gate.
- **F5 — Zeitstempel:** `modules/mailtime.py` (EU-DST fest implementiert,
  keine tzdata-Dependency); **alle** 22 Mail-Stellen in api + bg_service
  dual „UTC / MESZ". Guard-Test verbietet direkte Stempel.
- **F6 — Keine Messbasis im Tracker:** Die signals-Tabelle speichert keine
  Gate-Inputs. `scripts/chase_gate_backtest.py` rekonstruiert sie aus
  Polygon-Tages-Bars und ruft die **echten** Produktiv-Gates (kein
  Nachbau). Konsistenz per Test abgesichert.

## 3. Beobachtungspunkte (bewusst NICHT gefixt — mit Begründung)

- **B1 `crash_drop_too_extended` → WATCH statt NO_TRADE:** Semantisch ein
  Chase-Hinweis. Kein Fix ohne Stichprobe — der Backtest (unten) liefert
  die Datenlage; dann entscheiden.
- **B2 `rvol_below_bear_threshold` → WATCH:** Qualitäts-Blocker außerhalb
  der base_blockers. Prüfen, ob er alertable_now tatsächlich beeinflusst
  (Kandidat für base_blockers).
- **B3 Crypto-Mehrtages-Anker nur weich:** 7d-Überdehnung fließt in den
  Exhaustion-**Score** (scorers.py), nicht in ein hartes Gate. Crypto-Rows
  tragen change_7d im Scoring-Pfad, nicht im Gate-Pfad. Feature-Größe, kein
  Schnellschuss — nach Aktien-Datenlage entscheiden.
- **B4 Zwei Mail-Aufrufe ohne explizite mail_class** (NLS aktiver Dump,
  Default „trade"): semantisch vertretbar (aktives Signal), aber implizit.
  Beim nächsten Umbau explizit setzen.
- **B5 `swing_short_not_down_enough` → WATCH:** „kein Signal" als
  Beobachtungs-Label — semantisch ok, kein Handlungsbedarf.

## 4. Backtest auf dem Server ausführen

```bash
cd /home/tradingbot/app && git pull
venv/bin/python3 scripts/chase_gate_backtest.py --days 90
# kleiner Probe-Lauf: --sample 60
```

Ausgabe: Wie viele gemailte Signale die neuen Gates blockiert hätten,
nach Grund aufgeschlüsselt, **plus Outcome-Vergleich** (ØR/Trefferquote
blockiert vs. frei) → beantwortet „hätte das Gate Geld gespart?".
Limitationen (ehrlich): Day_High/Low = Ganztages-Extrem (Orts-Gate feuert
im Backtest seltener), kein Intraday-RVOL → Blockquote ist untere Schranke.

## 5. Verankerung (damit die Fehlerklassen nicht wiederkommen)

| Guard | Datei | Verhindert |
|---|---|---|
| Reasons-Registry (bidirektional) | `test_reason_registry.py` | K2: neue Gründe ohne Mapping |
| Zeitstempel-Guard | `test_mailtime.py` | K3: nackte UTC-/falsche CET-Stempel |
| Backtest-Konsistenz | `test_chase_gate_backtest.py` | Skript misst = Produktion misst |
| Gate-Repros Long/Short | `test_email_alert_audit.py` | K1: BHC-Muster beider Richtungen |

**Suite: 1310 Tests, alle grün** (vor Audit: 1286; +24).

## 6. Verbleibende ehrliche Unsicherheit

- Ob die Schwellen (5/7 ATR Woche, 2,5 ATR Vortag) kalibriert sind, zeigt
  erst der Backtest mit echten Daten (Punkt 4) — sie sind konservativ
  gesetzt (5 ATR/5 Tage = jeder Tag ein voller ATR-Tag).
- Der Backtest misst Long-Signale vollständig; Short-Signale des
  crash-Scanners nur, soweit Tages-Bars reichen (gleiche Rekonstruktion).
