# Signal-Tracker-Reparatur: Runbook

Dieses Runbook gilt fuer belegte historische Trackerfehler. Es ist **kein**
allgemeines Werkzeug, um schlechte Ergebnisse nachtraeglich umzudeuten.
Signalidentitaet, Plan-Level und Versandzeit bleiben unveraendert. Jede Korrektur
braucht externe Marktevidenz, einen exakten Vorzustand, ein Backup und ein
append-only Auditprotokoll.

## Sicherheitsvertrag

- Standard ist read-only Dry-Run.
- Niemals gegen eine laufende Produktionsdatenbank anwenden. API **und**
  Background-Evaluator schreiben in denselben Tracker und muessen im
  Wartungsfenster gestoppt sein.
- Vor Apply: exakten Server-Commit, Health und DB-Pfad pruefen.
- Apply verlangt die Bestaetigung `APPLY_SIGNAL_REPAIR`.
- Das Skript erstellt vor dem ersten Write ein konsistentes SQLite-Backup und
  prueft dessen Integritaet.
- Vor dem DB-Write wird ein dauerhafter `PREPARED`-Auditeintrag geschrieben;
  nach erfolgreichem Commit folgt der verknuepfte `APPLIED`-Eintrag.
- Der Vorzustand wird vor dem Backup und erneut innerhalb `BEGIN IMMEDIATE`
  verglichen. Jede Abweichung blockiert die gesamte Transaktion.
- Das Manifest darf weder Ticker/Richtung/Strategie noch Entry, Stop oder Ziele
  veraendern. Nur Auswertungsfelder sind korrigierbar.
- Keine API-Keys, Mailinhalte oder personenbezogenen Daten in Manifest/Auditlog.

## 1. Kandidaten read-only inspizieren

```bash
cd /home/tradingbot/app
venv/bin/python3 scripts/signal_tracker_repair.py \
  --db data_cache/signal_tracker.sqlite \
  --inspect-ticker ONON --inspect-ticker ECO --inspect-ticker AURA --inspect-ticker CBLL
```

Die Ausgabe liefert die Signal-IDs und den fuer `expected` benoetigten
Before-State. Mehrere Zeilen desselben Tickers muessen ueber Zeitpunkt,
Scanner, Richtung und Plan eindeutig gegen Originalmail und Marktdaten
zugeordnet werden.

## 2. Manifest erstellen

Schema-Version 1:

```json
{
  "schema_version": 1,
  "repair_id": "gap-open-and-stale-fill-202608",
  "corrections": [
    {
      "id": 123,
      "reason": "Konkrete und pruefbare Fehlerbeschreibung.",
      "evidence": {
        "source": "Polygon adjusted aggregates plus original signal mail",
        "observed_at": "2026-08-11T14:00:00Z",
        "summary": "Kurze Zusammenfassung ohne Secret oder Mailadresse."
      },
      "expected": {
        "ticker": "CBLL",
        "scanner": "stock_strategy",
        "created_at": "2026-08-11T13:59:00+00:00",
        "status": "STOP_HIT",
        "entry": 20.41,
        "stop": 19.43,
        "r_realized": -1.57,
        "outcome_detail": "stop_gap_slippage",
        "r_realized_upper": -1.57,
        "r_realized_be": -1.57,
        "entry_filled_at": "2026-08-11T13:59:00+00:00",
        "entry_fill_price": 20.41,
        "stop_hit_at": "2026-08-11T14:00:00+00:00",
        "exit_fill_price": null,
        "stop_gap_slippage_r": null,
        "stop_gap_slippage_pct": null,
        "max_favorable_r": 0.0,
        "max_adverse_r": -1.57
      },
      "updates": {
        "status": "NO_FILL",
        "outcome_detail": "stale_price_invalidated_before_entry",
        "r_realized": null,
        "r_realized_upper": null,
        "r_realized_be": null,
        "entry_filled_at": null,
        "entry_fill_price": null,
        "stop_hit_at": null,
        "exit_fill_price": null,
        "stop_gap_slippage_r": null,
        "stop_gap_slippage_pct": null,
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0
      }
    }
  ]
}
```

Das produktive Manifest wird **nicht** geraten. Insbesondere AURA bleibt ohne
eindeutige Erstsignalzeit unkorrigiert.

## 3. Dry-Run

```bash
venv/bin/python3 scripts/signal_tracker_repair.py \
  --db data_cache/signal_tracker.sqlite \
  --manifest /root/secure-repair/manifest.json
```

Erwartung: `status=dry_run_ok`, exakte Before/After-Diffs und keine DB-Aenderung.

## 4. Wartungsfenster, zweites Backup und Apply

Zuerst den normalen verschluesselten Server-/`data_cache`-Backupweg ausfuehren.
Dann **beide** Tracker-Writer stoppen. Health ist waehrend des kurzen
Wartungsfensters absichtlich nicht erreichbar:

```bash
systemctl stop tradingbot-api tradingbot-bg
venv/bin/python3 scripts/signal_tracker_repair.py \
  --db data_cache/signal_tracker.sqlite \
  --manifest /root/secure-repair/manifest.json \
  --apply --confirm APPLY_SIGNAL_REPAIR \
  --backup-dir /root/secure-repair/backups \
  --audit-log /var/log/alpha_signal_tracker_repairs.jsonl
systemctl start tradingbot-api tradingbot-bg
```

Wenn Apply blockiert, **nicht** den Fingerprint lockern. Zuerst klaeren, welcher
Prozess oder welche Annahme den Vorzustand veraendert hat.

## 5. Nachweis

```bash
systemctl is-active tradingbot-api tradingbot-bg
bash deploy/health_check.sh
venv/bin/python3 scripts/smoke_signal_performance.py
tail -n 2 /var/log/alpha_signal_tracker_repairs.jsonl
```

Danach Performance fuer dieselbe reife Kohorte vor/nach der Korrektur vergleichen.
Backup-Hash, Manifest-Hash, Signal-IDs und Ergebnisdelta gehoeren in den
Auditbericht. Das Backup erst nach dokumentierter Abnahme und regulaerer
Retention entfernen.
