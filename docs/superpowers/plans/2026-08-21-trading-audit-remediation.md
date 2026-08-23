# Umsetzungsplan: Auditfeste Signalwahrheit und Risikokontrollen

> Design: `docs/superpowers/specs/2026-08-21-trading-audit-remediation-design.md`
>
> Arbeitsregel: sequenziell, testgetrieben, keine Commits, kein Push, kein
> Serverzugriff. `Mailarchiv/` bleibt ausserhalb des Worktrees.

## Aufgabe 1: Öffentliche Signalreferenz als additiver Tracker-Vertrag

Dateien:

- Ändern: `modules/signal_tracker.py`
- Ändern: `api.py`
- Ändern: `scripts/signal_tracker_repair.py`
- Neu: `test_signal_public_reference.py`
- Ändern: `test_alert_delivery_intent_api.py`
- Ändern: `test_signal_tracker_repair.py`

Schritte:

1. Rote Migrationstests für nullable Legacy-Zeilen, kanonische Row-Identität,
   umsortierten Delivery-Intent-Retry, Eindeutigkeit und Unveränderlichkeit
   schreiben.
2. Rotlauf nur dieser Tests dokumentieren.
3. `public_signal_ref` plus `origin_evidence` additiv migrieren;
   Format/Validierung und partielle UNIQUE-Constraint zentralisieren.
4. Delivery-Intent-Zeilen über kanonische Row-Identität referenzieren; direkte
   aktuelle Post-Send- und Shadow-Evidenz markieren, migrierte Altzeilen als
   `legacy_origin_unknown` behandeln.
5. Kollision/Mehrdeutigkeit muss den vollständigen Intent vor SMTP blockieren.
6. Entry-Mail muss exakt die vorbereiteten Referenzen ausgeben.
7. Repair-Inspection darf die Referenz zeigen, Repair-Updates dürfen sie nicht
   ändern oder für Legacy erfinden.
8. Grünlauf und relevante Tracker-/Repair-Regressionen ausführen.

Fokusbefehl:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest_audit `
  test_signal_public_reference.py test_alert_delivery_intent_api.py `
  test_signal_tracker.py test_signal_tracker_repair.py
```

## Aufgabe 2: Folge-Mail-Korrelation und ehrliche R-Semantik

Dateien:

- Ändern: `modules/signal_tracker.py`
- Ändern: `bg_service.py`
- Ändern: `test_exit_update_mails.py`
- Ändern: `test_be_activation.py`
- Ändern: `test_personal_position_followups.py`

Schritte:

1. Rote Renderingtests mit zwei gleichzeitigen Setups desselben Tickers anlegen.
2. Transitionen und pending Terminal-/BE-Payloads um öffentliche Referenz und
   akzeptierten Ursprungszeitpunkt erweitern.
3. Pro Zeile Referenz, Ursprung, kanonischen Zustand, MFE und Level-R trennen.
4. TP1 als offene Zone ohne realisiertes R darstellen; BE-Mail ausdrücklich als
   MFE-/Managementhinweis formulieren.
5. `risikofrei` aus diesen Pfaden entfernen und per Guard-Test verbieten.
6. Entry-, TP1-, BE- und Terminal-Pfad mit derselben Referenz end-to-end prüfen.

Fokusbefehl:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest_audit `
  test_signal_public_reference.py test_exit_update_mails.py `
  test_be_activation.py test_personal_position_followups.py
```

## Aufgabe 3: Zielreichweiten-Telemetrie und dualer Mail-Header

Dateien:

- Ändern: `modules/trade_levels.py`
- Ändern: `api.py`
- Ändern: `bg_service.py`
- Neu: `test_target_reachability.py`
- Ändern: `test_trade_health.py`
- Ändern: `test_orb_target_plan.py`
- Ändern: `test_mailtime.py`

Schritte:

1. Rote Pure-Function-Tests für LONG/SHORT, fehlende/ungültige ATR-Werte,
   geschätzte/native Level-Provenienz, injizierte Budgets und symmetrische
   Distanzen schreiben.
2. `target_reachability` ohne versteckte Default-Hard-Schwelle implementieren.
3. Scanner-/ORB-/finale Revalidierungs-Inputs auf denselben Vertrag führen und
   maschinenlesbare Gründe weiterreichen.
4. Einen Renderzeitpunkt bis zum gebrandeten API-Mail-Header durchreichen und
   dort `_mail_timestamp_dual(rendered_at)` verwenden.
5. Guard-Tests für duale Headerzeit sowie bestehende `JETZT`-/Swing-Invarianten.

Fokusbefehl:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest_audit `
  test_target_reachability.py test_trade_health.py test_orb_target_plan.py `
  test_stock_strategy_final_revalidation.py test_mailtime.py test_mail_class_api.py
```

## Aufgabe 4: Brokerbelegte Paper-Risikopolitik

Dateien:

- Neu: `modules/trading_risk.py`
- Neu: `test_trading_risk.py`
- Ändern: `modules/paper_autotrader.py`
- Ändern: `test_paper_autotrader.py`

Schritte:

1. Rote Tests für Total-, Richtungs- und Gruppenrisiko inklusive Pending-Orders
   sowie fehlende Pflichtdaten schreiben.
2. Reine Risikoaggregation und Entscheidung implementieren; unmatched
   Positionen/Pending-Parents/Schutzstops müssen explizit unresolved und
   fail-closed sein.
3. Rote Fill-Pairing-Tests für Long/Short, Teilfills, vollständigen Abschluss,
   unvollständige Evidenz und heutigen Verluststreak schreiben.
4. Ein persistentes, nach `exec_id` dedupliziertes Fill-Ledger aufbauen und
   Broker-Fills je Intent zu `realized_r`, `realized_at` und
   `outcome_evidence=broker_fills` verdichten; Unbekannt bleibt unbekannt.
5. Konfigurationsnormalisierung um begrenzte Policyfelder und die dokumentierten
   defensiven Paper-Defaults erweitern.
6. Eine prozessübergreifende Order-Lease plus persistente Risikoreservierung
   einführen; Konkurrenztest mit zwei Submittern zuerst rot schreiben.
7. Risikoentscheidung nach Sizing/Tick-Rundung, aber vor jeder Brokerorder
   ausführen; bestehende DailyPnL-/Notional-Gates beibehalten.
8. Restart, verkürztes Fill-Fenster, doppelte `exec_id`, fehlender Schutzstop,
   Partial-Fill und Persistenz prüfen.

Fokusbefehl:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest_audit `
  test_trading_risk.py test_paper_autotrader.py test_broker_order_safety.py
```

## Aufgabe 5: Batch-Klumpenanzeige und Grade-Kalibrierung

Dateien:

- Ändern: `modules/trading_risk.py`
- Ändern: `api.py`
- Ändern: `modules/signal_tracker.py`
- Ändern: `bg_service.py`
- Ändern: `test_cluster_warning_mail.py`
- Neu: `test_tracker_grade_calibration.py`
- Ändern: `test_tracker_calibration.py`
- Ändern: `test_weekly_report_mail.py`

Schritte:

1. Rote Batch-Tests für Richtung und nur verifizierte Gruppenklassifikation.
2. Bestehenden Clusterhinweis um explizite hypothetische R-Belastung erweitern;
   keine individuelle Kontoposition behaupten und keine unkalibrierte Mail-
   Unterdrückung einführen.
3. Rote Kalibrierungstests für die gemeinsame Zelle
   Scanner/Grade/Richtung/Horizont/Regime schreiben.
4. Nur vollständig beobachtete Trade-Zeilen mit belegter Origin-Evidenz
   gruppieren; n, Wilson-KI, ØR,
   Summe R und Profit Factor ausgeben; `sample_reliable` erst ab 30 und ohne
   ungelöste Managed-BE-Fälle.
5. Wochenreport zeigt belastbare Zellen und einen klaren Hinweis, dass Grade
   Rangklassen und keine Wahrscheinlichkeiten sind.
6. Regression: bestehende vierdimensionale `calibration_cells`, Breaker- und
   Regime-Mailentscheidungen bleiben byte-/semantikgleich; Grade-Zellen sind nur
   Reporting.

Fokusbefehl:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest_audit `
  test_cluster_warning_mail.py test_tracker_grade_calibration.py `
  test_tracker_calibration.py test_weekly_report_mail.py test_performance_tab.py
```

## Aufgabe 6: Dokumentation und vollständige Verifikation

Dateien:

- Ändern: `PROJEKTBIBEL.md`
- Ändern: `PROJEKTHANDBUCH.md`
- Ändern: `docs/superpowers/plans/2026-08-21-trading-audit-remediation-progress.md`

Schritte:

1. Nur tatsächlich bewiesene neue Verträge und Grenzen dokumentieren.
2. Fokussierte Gesamtsuite der fünf Pakete ausführen.
3. Vollständige Suite ausführen:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest_audit
```

4. Compile, Bundle, Diff und Scope prüfen:

```powershell
.\.venv\Scripts\python.exe -m compileall -q api.py bg_service.py modules
.\.venv\Scripts\python.exe scripts\verify_frontend_bundle.py
git diff --check
git status --short
```

5. Unabhängige Task-Reviews und abschliessenden Whole-Branch-Review durchführen.
6. Weder committen noch pushen; dem Nutzer den lokalen Worktree, genaue Tests,
   verbleibende externe Gates und den Server-/Push-Status nennen.
