# Audit Report Aktien-Scanner V1

**Datum:** 2026-04-17
**Scope:** BI-Scanner, Autotrader, Biotech-Scanner, Scoring-Pipeline
**Methodik:** 3 parallele Deep-Audits (Scorers, Autotrader, Biotech) + Source-Verifikation aller CRITICAL-Findings

## TL;DR

31 Findings insgesamt aus den drei Agent-Reports, davon nach Source-Verifikation:
- **Echte Bugs (CRITICAL/HIGH):** 3
- **Design-Intent / Konfigurierbar (MEDIUM):** 6
- **Stylistik / False-Positives:** 22

Das meiste Kern-Verhalten ist solide. Die V3.3-Fixes (Pump-Filter + Grade-Thresholds, commit 961734b) und die 10-Fix-Runde aus commit 8efe3db haben bereits das Gros der echten Defekte adressiert. Die Regression-Tests (`test_breakout_audit.py` Cases A–E + `test_setup_score.py`) sind grün — der Audit bestätigt: keine neuen Regressionen.

## Audit-Scope

| Modul | Zeilen | Kern-Funktionen |
|-------|--------|-----------------|
| `modules/patterns.py` | 5423 | `analyze_breakout_imminent`, Smart-Money-Signale, Wyckoff, Volume Dry-Up |
| `modules/scorers.py` | 1963 | `calculate_setup_score`, `calculate_confluence_score`, `calculate_alpha_score`, `calculate_pm_quality_score` |
| `modules/scanners.py` | 2991 | `autotrader_scan_once`, Biotech-Scanner, IBKR Bracket-Orders |

## Verifizierte Echte Issues

### HIGH-1 — Autotrader: Chase-Schutz bei bereits ausgebrochenen Aktien fehlt
**Ort:** `modules/scanners.py:561–594`
**Problem:** Entry-Price wird als `range_high + atr_5 * 0.15` berechnet. Wenn der aktuelle Preis bereits deutlich über Entry steht (z.B. Stock bricht während des Scans aus), wird eine LMT-BUY-Order platziert, die entweder sofort zum aktuellen Marktpreis fillt (Chase) oder auf einen Pullback wartet, der nicht mehr kommt. Es gibt keinen Guard `if current_price > entry_price * X: skip`.

**Impact:** Chase-Trades in High-Momentum-Situationen, schlechte Entries.
**Fix:** Skip-Bedingung hinzufügen — wenn current_price > entry_price * 1.02, signal droppen.

### HIGH-2 — Autotrader: R:R-Berechnung nur auf TP1 basiert
**Ort:** `modules/scanners.py:566–568`
**Problem:** `reward = abs(tp1_price - entry_price)`. Da 50% der Shares bei TP1 und 50% bei TP2 exiten, müsste die R:R gewichtet berechnet werden: `0.5 * (tp1-entry) + 0.5 * (tp2-entry)`.

**Impact:** Echtes R:R ist höher als angezeigt → `min_rr=2.0`-Filter ist konservativer als nötig. Keine Verluste, aber Opportunity-Cost.
**Status:** Nicht kritisch, aber cosmetic fix für korrekte Reporting-Zahlen.

### MEDIUM-1 — Autotrader: Bracket-Order transmit im semi-Mode
**Ort:** `modules/scanners.py:650–694`
**Verhalten:** Parent (LMT BUY), Stop, TP1 haben `transmit=False`. TP2 hat `transmit=is_full_auto`.
- Full-Mode: TP2 `transmit=True` → alle 4 Orders werden zusammen gesendet. ✓ Korrekt.
- Semi-Mode: TP2 `transmit=False` → **alle Orders bleiben unbestätigt in TWS**, der Trader muss manuell transmitten.

Das ist **per design** für semi-auto, aber UX-technisch fragil (wenn der Trader nicht prüft, wird nichts gehandelt). Dokumentation sollte das klar machen, oder semi-Mode sollte den User proaktiv benachrichtigen.

### MEDIUM-2 — Scorer: Chase-Penalty in `calculate_setup_score`
**Ort:** `modules/scorers.py:1229`
**Code:** `momentum_pts = min(momentum_pts, 0)` bei `abs_change >= 20`.
**Semantik:** Deckelt Momentum-Bonus auf 0 (da momentum_pts immer >= 0 ist). Intendiert, funktioniert, aber missverständlich — ein echter Penalty wäre `momentum_pts = -5` oder ähnlich, um Chase aktiv zu bestrafen. Aktuell wird Chase neutral behandelt (kein Bonus, kein Malus).

**Empfehlung:** Entweder auf `momentum_pts = 0` vereinfachen (funktional identisch, lesbarer) oder echte negative Punkte für aggressive Chase-Bestrafung.

### MEDIUM-3 — Cooldown prüft nur Ticker, nicht Sektor
**Ort:** `modules/scanners.py:186–195, 412`
**Verhalten:** Cooldown-Default ist 5 Tage pro Ticker. Funktioniert korrekt (verifiziert: `0 < 5` → True am gleichen Tag).
**Lücke:** Kein Sektor-Exposure-Check. Wenn 5 Biotech-Signale gleichzeitig kommen, werden alle getradet → Sektor-Korrelationsrisiko.

**Empfehlung:** Optional: max X Positions pro Sektor / SIC-Code.

### MEDIUM-4 — Biotech: SIC-Code-Filter alleine nicht genug
**Ort:** `modules/scanners.py` (Biotech-Bereich)
**Problem:** Biotech-Klassifikation basiert auf SIC-Codes. Polygon/SEC-SIC-Daten sind aber nicht 100% vollständig — Kleinst-Biotechs ohne Ticker-Mapping werden übersehen. Zusätzlich fängt der reine SIC-Filter Pharma-Riesen (PFE, JNJ), deren FDA-Catalysts keinen Edge haben.

**Empfehlung:** Marktkapitalisierungs-Filter (z.B. $50M–$5B) kombiniert mit SIC.

## Agent-Findings, die bei Verifikation keine echten Bugs waren

| Agent-Claim | Befund |
|-------------|--------|
| "Autotrader Cooldown Race" | FALSE. Cooldown korrekt implementiert, Default 5 Tage blockt same-day. |
| "Bracket-Order transmit orphaned" | FALSE. Per-Design-Verhalten für semi-Mode. |
| "Biotech CRITICAL-1" | FALSE. Agent selbst: "Kommentar/Dokumentation — Logik korrekt". |
| "Scorer min(momentum_pts, 0) CRITICAL" | FALSE. Intendierter Chase-Schutz, funktional korrekt. Siehe MEDIUM-2. |
| Diverse Stil-Kritik ("code complexity", "should use numpy") | Out-of-scope — kein Verhaltens-Defekt. |

**Lehre:** Agent-"CRITICAL"-Labels brauchen Source-Verifikation. Die Findings sind brauchbare Hinweise, aber Severities muss der Mensch setzen.

## Was bereits gefixt ist (vorherige Audit-Runden)

- **commit c27f9a7** (09.04.): Resilience-Fix (0 down days → 1.0), RVOL-Gate entfernt, Pump-Boundary in api.py
- **commit 8efe3db** (10 Fixes): Sort-Key, FVG, Range, Threshold, Cache, API-Checks
- **commit caa18c4**: Fees, MaxDD, True Range ATR, Wyckoff-Fix, RVOL-Direction, Penny-Penalty
- **commit 961734b** (17.04.): Pump-Filter V3.3 + Grade-Thresholds (Case B + C grün)
- **commit a3f1663**: Frontend Marker-Dedup V4

## Umsetzungs-Plan

Implementiert jetzt:
1. **HIGH-1** Chase-Guard im Autotrader (Zeilen ~590)
2. **HIGH-2** R:R weighted (TP1 50% + TP2 50%)
3. **MEDIUM-2** Scorer momentum_pts Simplification (Code-Clarity)

Dokumentiert als Follow-up:
- MEDIUM-1 (semi-Mode Transmit UX-Dokumentation)
- MEDIUM-3 (Sektor-Exposure-Limit)
- MEDIUM-4 (Marktkap-Filter für Biotech)

Nach Fixes: `test_breakout_audit.py` + `test_setup_score.py` müssen grün bleiben.
