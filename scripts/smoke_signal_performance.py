#!/usr/bin/env python3
"""Smoke-Test: Signal-Performance-Summary mit T1/Kalibrier-Feldern.

AUDIT 2026-07-24. Prueft auf dem Server OHNE Auth direkt an der Datenschicht,
ob load_performance_summary die neuen Felder liefert:

  total / per_scanner: decided_signals, win_rate_wilson_95,
                       sample_reliable, avg_r_managed_50_50
  recent:              r_managed_50_50
  summary:             r_semantics

Wichtig: der KEY muss existieren; der Wert darf None sein (z. B. keine
entschiedenen Signale im Fenster). None ist legitim — ein fehlender KEY
bedeutet Alt-Stand deployed => FAIL.

Usage (auf dem Server, im App-Verzeichnis):
    venv/bin/python3 scripts/smoke_signal_performance.py --days 90

Exit 0 = alle Felder live, 1 = Alt-Stand/Fehler.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BUCKET_KEYS = ("decided_signals", "win_rate_wilson_95",
               "sample_reliable", "avg_r_managed_50_50")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    try:
        from modules import signal_tracker
    except Exception as exc:  # noqa: BLE001 - Smoke-Test soll alles abfangen
        print(f"FAIL: modules.signal_tracker nicht importierbar: {exc}")
        return 1

    print(f"DB: {signal_tracker.SIGNAL_DB_PATH}")
    summary = signal_tracker.load_performance_summary(days=args.days)
    total = summary.get("total") or {}
    per_scanner = summary.get("per_scanner") or {}
    recent = summary.get("recent") or []

    failures = []

    def check(cond, label):
        print(("  OK   " if cond else "  FAIL ") + label)
        if not cond:
            failures.append(label)

    print(f"\nFenster: {args.days} Tage | Signale: {total.get('signals', 0)} | "
          f"entschieden: {total.get('decided_signals', '?')} | "
          f"Scanner: {len(per_scanner)} | recent: {len(recent)}\n")

    # 1) Summary-Level
    check("r_semantics" in summary, "summary.r_semantics vorhanden")

    # 2) total-Bucket
    for key in BUCKET_KEYS:
        check(key in total, f"total.{key} Key vorhanden")

    # 3) per_scanner-Buckets
    for name in sorted(per_scanner):
        bucket = per_scanner[name] or {}
        for key in BUCKET_KEYS:
            check(key in bucket, f"per_scanner[{name}].{key}")

    # 4) recent-Zeilen
    if recent:
        missing = [str(r.get("ticker", "?")) for r in recent
                   if isinstance(r, dict) and "r_managed_50_50" not in r]
        check(not missing,
              "recent[*].r_managed_50_50 (fehlt bei: "
              + (", ".join(missing) if missing else "—") + ")")
    else:
        print("  INFO recent leer — nichts zu pruefen")

    # 5) Werte-Invarianten bei entschiedenen Signalen
    decided = total.get("decided_signals")
    if isinstance(decided, int) and decided > 0:
        wilson = total.get("win_rate_wilson_95")
        check(isinstance(wilson, dict)
              and 0.0 <= wilson.get("lower_pct", -1)
              <= wilson.get("upper_pct", -1) <= 100.0,
              f"Wilson-Werte plausibel: {wilson}")
        check(isinstance(total.get("avg_r_managed_50_50"), (int, float)),
              f"avg_r_managed_50_50 befuellt: "
              f"{total.get('avg_r_managed_50_50')}")
    else:
        print("  INFO keine entschiedenen Signale im Fenster — "
              "None-Werte legitim")

    # Kompakte Gesamtsicht
    print("\ntotal:", json.dumps({k: total.get(k) for k in (
        "signals", "decided_signals", "win_rate_pct", "win_rate_wilson_95",
        "sample_reliable", "avg_r", "avg_r_managed_50_50", "sum_r")},
        ensure_ascii=False))

    if failures:
        print(f"\nFAIL ({len(failures)}) — vermutlich Alt-Stand deployed "
              "(git pull + Service-Restart?)")
        return 1
    print("\nPASS — T1/Kalibrier-Felder live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
