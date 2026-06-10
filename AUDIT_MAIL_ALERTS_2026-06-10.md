# Mail-Alert-Audit — 10.06.2026

> **STATUS-UPDATE (gleicher Tag): ALLE Befunde umgesetzt.** Q1/Q2/Q3, H1-H3, B2-B8, M1 gefixt; 471/471 Tests grün (36 neue Mail-Regressionstests: test_mail_class_api.py + test_mail_gates_bg.py). Mail-Klassen jetzt: `🚨 JETZT:` (nur im Versandmoment handelbar, Trigger ≤ 15 Min, Entry-Zone), `👁️ WATCH:` (klar markiert, Abonnenten nur mit Opt-in `watch_mail_optin`), `ℹ️` (Info). bg-Mails laufen durch dieselben Gates wie api (Health/RVOL/estimated/R:R) und teilen das persistente Dedupe. Commit: 33befc1.

**Beschwerde:** Mails mit Setups, die beim Lesen nicht handelbar sind ("Watchlist statt JETZT traden").
**Sollzustand (Betreiber-Entscheidung):** Zwei getrennte Mail-Klassen — 🚨 JETZT-Mails (nur im Versandmoment handelbare Rows) und 👁️ WATCHLIST-Mails (klar gekennzeichnet, nie als Trade-Call formuliert). Trigger beim Versand max. 15 Min alt, Preis noch in Entry-Zone.
**Methodik:** 2 parallele Read-only-Audits (api.py-Mailsystem; bg_service/scanner-Mailsystem) mit Laufzeit-Beweisen: 7/7 + 18/18 Repro-Tests PASS.

## Kernergebnis: Die Beschwerde hat 3 Hauptquellen

**Q1 — Crypto "Early Mover LONG Digest" mailt Watch-Rows als Setups (KRITISCH, api.py).**
Die einzige Sammel-Mail mit gemischtem Body. Zwei Lücken: (a) api.py:1760 erlaubt explizit `WAIT_FOR_RETEST` als mailbare Aktion; (b) der `soft_swing_wait`-Zweig (api.py:2082-2087) lässt Health-Decisions WAIT_FOR_RETEST/WAIT_FOR_TRIGGER/WAIT_FOR_CONTINUATION/WATCH_ONLY weich durch — Row wird `alertable_now=True` und landet als "TRADE_NOW" in der Mail. **Laufzeit-Beweis:** Payload mit 1× echtem Trigger + 2× WAIT/WATCH → Mail "3/3 Setup(s)", alle drei im Body mit Entry/Stop/TP, keine Watch-Kennzeichnung.

**Q2 — Kein Trigger-Alter-Check im Mail-Pfad (KRITISCH, api.py, alle Crypto-Mails).**
Das 180s-Frische-Gate (`_downgrade_expired_crypto_triggers`) läuft NUR im GET-Endpoint (UI), nicht vor dem Mail-Versand. Early-Movers-Scan kann >20 Min laufen (Watchdog 25 Min) — ein am Scan-Anfang berechnetes JETZT_TRADEN ist beim Versand beliebig alt. **Beweis:** Row mit 20-Min-altem Trigger → Mail-Gate sagt TRADE_NOW; dieselbe Row wird im UI auf WARTEN downgegradet. Deshalb: UI sagt WARTEN, Mail sagt Setup.

**Q3 — bg_service-Mails (bi_long/bi_short/biotech) ohne Health-/RVOL-/Chase-Gates (KRITISCH, bg_service.py:894-936).**
bg verschickt für seine Scanner eigene "🚨 Top-Setup"-Mails und prüft dabei nur Grade/Score/Timing/Plan-Geometrie — `calculate_trade_health` wird in bg NIE aufgerufen, kein RVOL-Floor, kein Entry-Zonen-Check, keine estimated-Sperre, kein Score-Blending. **Beweis:** Row mit Preis ÜBER TP1 (Entry längst überrollt) → bg mailt "🚨 2 Top-Setups — BI Scanner Long"; api blockt dieselbe Row (trade_health_no_trade, chase_risk). Ebenso: Grade-S-Row mit RVOL 0.5 → bg mailt, api blockt.

## Weitere Befunde

| Sev | # | Befund | Ort |
|---|---|---|---|
| HOCH | H1 | Swing-Score-Cap `min(score, max(entry_score, 80))` macht Entry-Score wirkungslos (entry_score 10 → Cap 80 → alertable) | api.py:4778 |
| HOCH | H2 | Betreff-Kennzeichnung uneinheitlich: JETZT/WATCH/INFO aus Betreff nicht erkennbar; Digest mit Watch-Rows klingt wie Trade-Call | api.py (12 Sender) |
| HOCH | H3 | Kein Mail-Klassen-Routing: Watch- und sogar Test-Mails gehen an alle Abonnenten ohne Opt-in (`recipient_emails=None` hängt Abonnenten an jede Mail) | api.py:12078, auth.py:836 |
| HOCH | B2 | Doppel-Mail-Risiko bi/biotech: api UND bg mailen; bg liest das geteilte persistente Dedupe-File für bi_long/biotech nicht (nur in-memory, restart-unsicher) | bg:933/1009 vs api:5454 |
| HOCH | B3 | NLS-Dedupe-Key-Mismatch api (`new_listing_TSTUSD`) vs bg (`new_listing_TST`) → Doppel-Mails möglich | bg:3013, api:4884 |
| HOCH | B4 | bg-Plan-Gate ohne estimated-Sperre (`allow_estimated=True`, rr=None passiert): biotech-WATCHLIST-Row mit synthetischem 3%-Stop wäre als "Top-Setup" mailbar | bg:274-279, 358-364 |
| MITTEL | B5 | NLS-Invalidierung (Stop gerissen) wird nie nachgemailt — tote SHORT-Empfehlung bleibt unkorrigiert im Postfach | bg:2964 |
| MITTEL | M1 | Biotech-Level werden zur Mailzeit synthetisiert, gelten danach als "nativ" — Kennzeichnung im Mail-HTML fehlt | api.py:9199 |
| MITTEL | B6 | bg kennt keinen WATCHLIST-Typ; Richtungs-Fallback "SHORT" falsch für Long-Scanner; Footer behauptet persistenten Cooldown | bg:962-966 |
| MITTEL | B7 | bg ruft Empfängerliste ohne trade_horizon ab (Routing-Filter umgangen); SMTP-Konventionen divergieren | bg:817 |
| NIEDRIG | B8 | bg-Strategy-Pfad setzt Cooldown vor Versand (gleicher Bug-Typ wie ORB H-2 gestern, Pfad aktuell dormant) | bg:1961 |

## Was nachweislich SAUBER ist

Aktien-Pfade in api.py rendern nur JETZT-Rows (Laufzeit-Beweis: 1 gute + 3 schlechte Rows → nur die gute im Body): BI/Biotech/Bear/Crash/ORB/Strategie-Sweep. WAIT-Decisions werden im Aktien-Pfad hart auf Score ≤ 69 gekappt — die Crypto-Soft-Lücke (Q1) ist ein Sonderweg, kein Systemfehler. ORB-Mails sind ≤ ~5 Min frisch. Reminder-Mails machen Live-Neubewertung (≤ 60s). Dump-Watch-Mail ist vorbildlich gekennzeichnet ("NICHT SHORTEN"). NLS-Short-Mails (bg) haben nahezu api-paritätische Gates. ARMED-Mails sind hart deaktiviert. Narrative Pulse hat eigenes Opt-in. btc_divergenz verschickt korrekt KEINE Mails (watch-only). Streamlit (scanner.py) verschickt gar keine Mails. Keine hartcodierten Empfänger.

## Fixplan (Reihenfolge)

1. **Q1:** `soft_swing_wait`-Zweig entfernen + `WAIT_FOR_RETEST` aus erlaubten Mail-Aktionen streichen → Crypto wird so hart wie Aktien. Retest-Kandidaten stattdessen in separate 👁️-WATCHLIST-Mail.
2. **Q2:** `_MAIL_TRIGGER_MAX_AGE_SEC = 900` (15 Min, Betreiber-Vorgabe): Trigger-Alter-Check + `_downgrade_expired_crypto_triggers` VOR dem Mail-Bau; Chase-Schutz prüft Entry-Zone.
3. **Q3/B4:** bg spiegelt die api-Gates (Health via `modules.trade_health` direkt importierbar, RVOL-Floor, estimated-Sperre, Entry-Zone) — ODER bi/biotech-Mails komplett an api konsolidieren (bg bleibt reiner Scanner). Empfehlung: Konsolidierung (weniger doppelte Logik).
4. **B2/B3:** bg auf das geteilte persistente Dedupe-File + api-Key-Format umstellen (Funktionen existieren in bg bereits).
5. *