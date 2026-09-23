# Scanner-Steuerung: sichere Pause und frisches Fortsetzen

## Stand und Nachweisgrenze

Der Folgeauftrag vom 23.09.2026 erweitert die bislang nur vereinheitlichte
Bedienung um echte Backend-Pausen fuer weitere Discovery-Scanner. Eine Pause
ist kooperativ: laufende Provideraufrufe werden nicht gewaltsam beendet.
`pause_requested` bedeutet noch nicht Stillstand. Erst ein sicherer Haltepunkt
ohne weiterlaufende Kindjobs bestaetigt `paused`.

Die frueher dokumentierte oeffentliche Health-Antwort vom
`2026-09-23T07:36:12.553192` UTC (`healthy`, Revision `3c667b6f165d`, Frontend
`5be25ee8c547`) bleibt ein historischer Ausgangspunkt, kein Ausrollnachweis
dieser Erweiterung. Der vorliegende Nachweis ist lokal und offline. Commit,
Push, Serverupdate und anschliessende Betriebspruefung sind getrennte Schritte.

## Gemeinsamer API-Vertrag

`modules/scan_control_policy.py::capability` ist die gemeinsame Policy fuer
Controller und API. `/api/scan-status` meldet fuer jeden echten Owner dessen
`control`; eine generische Strategiebezeichnung erzeugt keinen zweiten Owner.
`/api/scan-control` verlangt weiterhin Administratorrechte und die genaue
Lauf-ID eines noch lebenden Workers. Ein zweiter manueller oder automatischer
Start darf einen geparkten Worker nicht duplizieren.

Zusaetzliche oeffentliche Felder:

- `supported`: Die Pipeline hat implementierte sichere Pausenpunkte.
- `resume_policy`: `continue_if_valid`, `restart_fresh` oder `null`.
- `unsupported_reason`: `protection_monitor`, `mixed_position_management`,
  `unsupported_scanner` oder bei Unterstuetzung `null`.
- `protected`: Die fehlende Pause ist eine ausdrueckliche Schutzentscheidung.

`owner_scan_key`, `run_id`, `state`, `worker_alive`, `paused_seconds`,
`paused_at`, `resume_at`, `auto_resume` und `scope` bleiben erhalten.
`scope` ist `scanner`, fuer die Aktienrunde `strategy_round`.
Die dedizierten Aliase von `/api/scan` melden jetzt echte Startannahme und
Lauf-ID ueber `_manual_scan_ack`; ein abgelehnter Start heisst nicht `started`.

## Tatsaechliche Backend-Abdeckung

| Owner | Pause/Fortsetzung |
| --- | --- |
| `bi_long`, `bi_short`, `strat_*`, `strategy_scan` | Unterstuetzt. Bestehender `continue_if_valid`-Vertrag: nur derselbe gueltige Daten-/Konfigurationsstand darf weiterlaufen; sonst frischer Neustart. |
| `biotech`, `bear`, `turtle`, `orb` | Unterstuetzt, `restart_fresh`. |
| `penny_stocks`, `volume_spikes`, `money_flow` | Unterstuetzt, `restart_fresh`. Penny betrifft Discovery, nicht den Positionsmonitor. |
| `early_movers`, `btc_divergenz`, `crypto_explosion`, `crypto_strat_*` | Unterstuetzt, `restart_fresh`. Crypto-Fortsetzen wird nicht durch den Aktien-Wochenendkalender verschoben. |
| `penny_positions`, `cup_handle_watch`, `quote_capability`, `crash_monitor`, `market_context` | Absichtlich nicht pausierbar: `protection_monitor`, `protected=true`. |
| `new_listing`, `crypto_trade_signals` | Absichtlich nicht pausierbar: `mixed_position_management`, `protected=true`. |
| Unbekannte Owner; nicht unterstuetzte Futures-/Forex-Pipelines | Kein Pausenvertrag: `unsupported_scanner`. |

`restart_fresh` bedeutet: Nach bestaetigter Pause werden der alte Stack und
seine nicht abgeschlossenen Resultate verworfen. Erst nach tatsaechlichem
Thread-Ende darf die Restart-Queue einen neuen exklusiven Lauf starten.
Eine lauflokale Nonce verhindert versehentliche Wiederverwendung auch bei
identischen Windows-Uhr-Ticks. Das ist kein Rueckladen alter Checkpoints.
Der letzte erfolgreiche Ergebnisstand wird durch die Unterbrechung nicht
erneut datiert oder durch ein frisches leeres Ergebnis ersetzt.

## Sicherheitsgrenzen

- Finale Cache-Publikation und anschliessende Mail erfolgen erst nach `seal()`.
  Nach dieser Abschlussgrenze wird eine neue Pausenanforderung abgelehnt.
  Vorhandene Live-Vorschauen bleiben explizite Teilergebnisse, keine finalen
  Signale; bei einem Pause-Neustart werden ihre Dateien bereinigt.
- Biotech besitzt den Haltepunkt in seiner internen Aktienschleife und die
  Abschlussversiegelung direkt vor seinem internen Finalcache. Nur ein Punkt
  im aeusseren Wrapper waere unzureichend gewesen.
- Crypto Explosion stoppt nach einer Pausenanforderung die Vergabe weiterer
  Kandidaten pro Venue. Begonnene Kandidaten laufen zu Ende. Erst nachdem
  **alle** Futures den Executor verlassen haben, parkt der Owner. Selbst
  Fortsetzen vor diesem Join darf eine bereits abgekuerzte Kandidatenmenge
  niemals als vollstaendiges Ergebnis publizieren.
- Combined besitzt einen echten eigenen Worker, ruft seine Quellen aber
  synchron auf. New Listing vermischt Discovery mit Stop- und 24h-Ablaufpflege
  und schreibt eine gemeinsame Monitoring-Datei. Diese Pflege wird nicht durch
  eine scheinbare Discovery-Pause eingefroren. Die Trennung waere eine eigene
  Lifecycle-Aenderung. Ungebundene Child-Wrapper erzeugen keinen Control-Owner.
- Combined ist gegen parallele Explosion-/New-Listing-Owner gesperrt,
  einschliesslich noch lebender Thread-Tails. Eine pausierte eigenstaendige
  Explosion blockiert dagegen keinen eigenstaendigen New-Listing- oder
  Penny-Positionslauf.
- Bereits beobachtete Penny-TP1-/Exit-Ereignisse bleiben vor spaeteren
  Pausenpunkten dauerhaft gespeichert. Die neue Steuerung verschiebt diese
  Schutzereignisse nicht in einen fluechtigen Ergebnis-Zwischenspeicher.

Schwellen, BI-17-von-20-Vertrag, optionaler Retest, Swing-1D-Vertrag,
Mail-Eignung und Tracking-Gates wurden durch diese Steuerung nicht geaendert.
Es gibt keinen globalen Stopp von Schutz-, Tracker- oder Outbox-Arbeit.

## Lokale Pruefung

**698 gezielte Tests bestanden (10,52 Sekunden)** im isolierten Launcher
`output/run_offline_repair_tests.py`; externe Provider- und SMTP-Verbindungen
sind dabei gesperrt. Dies ist kein neuer Gesamtlauf aller Projekttests.

Neue Gates:

- `test_dedicated_scan_control_policy.py`: erlaubte/geschuetzte Owner,
  Metadaten, exakte Run-Identitaet und nichtblockierende Future-Abfragen.
- `test_dedicated_scanner_pause.py`: echte Worker-Pause, frischer Ersatzlauf,
  Admin-/Run-ID-Schutz, eingefrorene Uhr, Aktien-/Crypto-Wochenendgrenze,
  echte parallele Venue-Worker inklusive Resume-vor-Join-Rennen,
  unabhaengige Schutzjobs, finaler Publish-Zaun, interne Biotech-Unterbrechung
  und wahrheitsgemaesse Startantworten der dedizierten Aliase.

Mitgelaufen sind bestehende Control-/API-/BI-Pause-, Crypto-Explosion-,
Penny-, Early-Mover-, Biotech-, ORB-, Bear- und verwandte Scannerregressionen.
Unabhaengige Read-only-Pruefung bestaetigte die Join-/Ownership- und
Schutzjob-Grenzen. `git diff --check` fuer die Steuerungsaenderungen ist sauber.

Frontend-Build (`540078fa6755`), gerenderte Desktop-/Mobilbedienung und der
abschliessende vollstaendige Testbestand (7.272 bestanden, 4 uebersprungen)
sind in `docs/SCANNER_DEEP_AUDIT_REPAIR_2026-09-23.md` protokolliert.
Der Server wurde dabei nicht aktualisiert oder live verifiziert. Die frueheren
568 UI-/Control-Tests und alten Screenshot-/Bundle-Hashes sind historische
Nachweise des vorherigen Darstellungsstands, nicht dieser Backend-Erweiterung.
