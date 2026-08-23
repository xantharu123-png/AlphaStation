# Fortschrittsledger

Stand: 2026-08-23

| Paket | Status | Nachweis |
|---|---|---|
| Isolierter Worktree | erledigt | Branch `codex/trading-audit-remediation`, Ausgang `24adab5` |
| Reproduzierbare Python-Umgebung | erledigt | frisches `.venv`, Python 3.13.13, gepinnte Requirements |
| Ausgangssuite | erledigt | 1931 bestanden, 4 übersprungen, 0 Fehler in 263,87 s |
| Design | erledigt | `docs/superpowers/specs/2026-08-21-trading-audit-remediation-design.md` |
| Öffentliche Signalreferenz | erledigt | RED-Verträge und Review-Fixes; stabile AS1-Refs, immutable Plansnapshot und dauerhafte Event-Receipts |
| Mail-/R-Semantik und Tracker | erledigt | 439/439 fokussierte Mail-/Tracker-Tests; finaler Re-Review P0/P1/P2 = 0/0/0 |
| Zielreichweite/Headerzeit | erledigt | Zielreichweite 321/321; Dualzeit/Builder/Outbox 375/375; beide re-reviewed |
| Paper-Risikopolitik | erledigt | 385/385 Risiko-/Broker-Tests; zwei finale unabhaengige Reviews P0/P1/P2 = 0/0/0 |
| Batch/Grade/UI | erledigt | 24/24 Frontend-Vertragstests; gemeinsame Paketregression 959/959 |
| Vollabnahme | erledigt | 2490 bestanden, 4 uebersprungen, 0 Fehler in 1066,94 s; Compile, Bundle-Quelle `54bc2efa62cc`, Bundle-SHA-256 `d2e03be31a79983fc91f07a80795fd4ccc70be49dfed8d23a8c80d639b1b9bf9`, JavaScript-Syntax und `git diff --check` gruen |
| Scope-/Secret-Pruefung | erledigt | 44 beabsichtigte Dateien, 0 verdaechtige Dateinamen, 0 Secret-Mustertreffer; `Mailarchiv/` nicht im Scope |

Rulings:

- Keine neuen Trading-Schwellen aus dem 51-Mail-Archiv ableiten.
- Bereits implementierte und getestete Schutzmechanismen nicht duplizieren.
- Keine Commits/Pushes/Serveränderungen ohne neuen Auftrag.
- Öffentliche Referenz aus kanonischer Row-Identität, nicht aus Eingabeposition.
- Referenzkollision blockiert den vollständigen Intent vor SMTP.
- Entry-Mail ordnet jede Referenz eindeutig dem kanonischen Plan zu; ungültige,
  doppelte oder nachträglich korrumpierte Referenzen bleiben fail-closed.
- Legacy-`NULL` bleibt roh unverändert und wird in konsumierten Payloads als
  `legacy_origin_unknown` interpretiert.
- Folge-Mails korrelieren Ref/Ursprung auch nach Outbox-Reload; MFE-R,
  terminales Level-R und offene TP1-/BE-Zustaende sind sprachlich getrennt.
- Ein vollstaendiger kanonischer Plansnapshot, Empfaengerkohorte und Einwilligungs-
  stand werden vor Zustellung gebunden. BE-/Terminal-Ereignisse benoetigen ein
  dauerhaftes, exakt signalgebundenes Receipt; synthetische, nackte oder fremde
  Receipts autorisieren keine Folge-Mail.
- Historische Outbox-Zeilen vor `pending_update_tp1_hit_this_run` bleiben bei
  fehlender Evidenz konservativ `False`; kein Rueckwaerts-Fakt wird erfunden.
- ATR-Zielreichweite ist endliche, rein deskriptive Telemetrie; explizite
  Budgets veraendern keine Health-, ORB-, Revalidierungs- oder Mail-Gates.
- API-Mailbody und Markenheader teilen exakt einen UTC-Renderzeitpunkt; DST wird
  dual dargestellt und fertiges Outbox-HTML nicht neu gerendert.
- Grade-Unterzellen bleiben Reporting und verändern den bestehenden Breaker nicht.
- Paper-Fill-Evidenz wird dauerhaft per `exec_id` dedupliziert; aktuelle Broker-
  Snapshots allein reichen nicht für Verluststreaks.
- Ein Paper-Outcome wird nur unter gueltiger Lease/Fence und atomar persistierter,
  frischer Terminal-Evidenz abgeschlossen. Keine gemappte Parent-, Stop- oder
  Target-Order und keine Brokerposition darf noch offen sein.
- `BROKER_VISIBLE` verlangt vollstaendige intentgebundene P/S/T-Geometrie,
  eindeutige positive `permId`s und aktive Broker-Acks. Terminal offene Orders
  werden ueber die zusammengesetzte Brokeridentitaet plus Geometrie gebunden;
  ihre Reihenfolge wird vor dem Evidenz-Hash kanonisiert.
- Explizite immutable Fill-Sequenzen, spaete Fills/Mappings nach Freigabe,
  monotone Reservation-Zeit und natuerliche Intent-Kollisionen bleiben
  dauerhaft auditierbar und fail-closed.
- Provider-Protokollfehler und unvollstaendige Legacy-Geometrie werden nicht als
  leere vollstaendige Snapshots zertifiziert. `tighten_stop` bleibt vor dem
  Broker-Aufruf fail-closed gesperrt, bis eine gezaeunte monotone Geometrie-
  Revision implementiert und abgenommen ist.
- Re-Arm und unmittelbare Pre-Reservation verlangen jeweils frische kausale
  Orders-/Positions-/Fill-/Account-/PnL-Snapshots. Account und PnL verwenden
  je Fenster eigene rohe Request-IDs; fremde, alte oder gecachte Events koennen
  keine Freigabe erteilen.
- Generation-Fencing, Kill-Cancel-Acks, Prozess-Lock-Owner-Claims und Crash-
  Recovery verhindern, dass alte Worker, abgelaufene Leases oder TTL allein
  Brokerfreiheit behaupten. Limit-Risiko, Exposure und Cash werden mit dem
  konservativen Worst-Fill-Preis in USD auch fuer aktive bzw. pending Parents
  geprueft.

Aktuell belegte Vertraege:

- Prepared-Delivery-Zeilen erhalten ausschliesslich persistierte, stabile
  `AS1-[0-9A-F]{20}`-Referenzen. Reorder-Retries bleiben planbezogen; fehlende,
  ungueltige, doppelte oder kollidierende Referenzen blockieren den neuen Intent
  vollstaendig vor SMTP. Rohes Legacy-`NULL` bleibt erhalten und wird nur im
  Payload als `legacy_origin_unknown` normalisiert.
- Folge-Mails unterscheiden MFE-R (Kursfortschritt), terminales Level-R und
  offene TP1-/BE-Zustaende. TP1 belegt keinen Teilverkauf; BE reduziert nur
  geplantes Preisrisiko, nicht Gap-, Slippage- oder Ausfuehrungsrisiko.
- Tracker-Telemetrie endet am belegten Exit. Open-Gaps werden vor spaeteren
  Tagesextrema ausgewertet; unaufloesbare OHLC-Reihenfolgen bleiben als solche
  markiert. MFE/MAE, Kontrollpopulationen und Breaker-Denominatoren duerfen
  weder Post-Exit-Kurse noch Legacy-Urspruenge als gueltige Evidenz verwenden.
- Die in Task 3 erfassten gebrandeten API-Pfade verwenden fuer Inhalt und
  Markenheader denselben Renderzeitpunkt und zeigen UTC plus MEZ/MESZ;
  Outbox-Replay rendert das fertige HTML nicht neu. Historische unbranded
  BG-Nebenpfade sind damit nicht pauschal migriert.
- Target-Reachability bleibt endliche deskriptive ATR-Telemetrie mit
  Provenienz. Ohne gueltige ATR/Geometrie bleibt sie unavailable; explizite
  Budgets sind kein Default, keine Wahrscheinlichkeit und kein Gate.
- Der DU-only Paper-Risk-Store besitzt immutable Intents/Order-Mappings,
  append-only Fill-Ledger mit expliziter immutable Sequenz, gezaeunte Leases und
  atomare Reservierungen. Konfliktierende oder unvollstaendige Fill-Evidenz,
  spaete Fills/Mappings und unvollstaendige Broker-Snapshots bleiben fail-closed.
  COMPLETE bindet Lease/Fence, Terminal-Snapshot, Outcome und Reservation in
  einer Transaktion. Policies: 0,75 % Gesamt/Richtung, 0,50 % verifizierte
  Gruppe, drei Verlustserien.
- Die Batch-Anzeige bleibt hypothetisch (1R je gueltigem Plan), ohne Dollar-
  oder Konto-Aussage und ohne Mail-Suppression; Gruppensummen erscheinen nur
  fuer explizit verifizierte Gruppen. Grade-Zellen sind reine 5D-Reports
  (Scanner/Grade/Richtung/Horizont/Regime), nur mit qualifizierter Origin-/Fill-
  Evidenz und 50/50+BE; reliable nur bei `n >= 30` und `unresolved = 0`.
- Das Frontend bezeichnet alle AutoTrader-Funktionen oeffentlich als Paper-only,
  mischt keine Metrikfamilien/Denominatoren und wertet STOP/EXPIRED nach TP1
  nicht als positiven Terminalfall. Stabile CTA-IDs, fehlertolerantes Scrollen
  und ein nicht-blockierendes Boot-Overlay sind vertraglich getestet.

Unveraenderte Grenzen:

- Das 51-Mail-Archiv ist kein Backtest und darf keine neuen Trading-Schwellen
  begruenden.
- Kein Commit, Push, Server- oder Live-System-Schritt war Teil dieser Arbeiten.
- Live bleibt blockiert. Der reale mehrtaegige DU-Soak steht noch aus.
- Der Browser-Sichttest nach den letzten UI-Aenderungen ist mangels verfuegbarem
  Browserkanal ein externer visueller Gate; Bundle-/DOM-Vertraege sind lokal
  gruen, aber keine visuelle Produktfreigabe.
