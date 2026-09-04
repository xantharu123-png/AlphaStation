#!/usr/bin/env python3
"""Read-only scanner performance by the same cohort as the main API.

Default: fully observed signals grouped by maturity month/day. --include-recent
uses the causal delivery timestamp and explicitly reports a provisional cohort.
Only terminal filled rows enter R/win arithmetic; unresolved BE cases stay visible.
No schema migration, signal evaluation, network call or database write occurs.

Usage: python scripts/signal_performance_breakdown.py --days 365 --per-day
"""
from __future__ import annotations  # Annotations lazy: py3.8-kompatibel

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _fmt_cell(wins: int, decided: int, wilson: dict | None) -> tuple[str, str]:
    if decided <= 0:
        return "—", "—"
    win_pct = f"{100.0 * wins / decided:.0f}%"
    if wilson:
        ci = f"{wilson['lower_pct']:.0f}–{wilson['upper_pct']:.0f}%"
    else:
        ci = "—"
    return win_pct, ci


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365,
                        help="Fenster in Tagen (Default 365 = maximale Historie)")
    parser.add_argument("--scanner", type=str, default="",
                        help="Nur diesen Scanner zeigen (z. B. stock_strategy)")
    parser.add_argument("--per-day", action="store_true",
                        help="Zellen pro Tag statt pro Monat (Regime-Brueche sichtbar machen)")
    parser.add_argument("--include-recent", action="store_true",
                        help="Vorlaeufige Versandkohorte statt vollstaendig beobachteter Kohorte")
    args = parser.parse_args()

    from modules import signal_tracker as st

    print(f"DB: {st.SIGNAL_DB_PATH}")
    as_of = datetime.now(timezone.utc)
    # Reporting never migrates or creates a production DB.
    db_path = Path(st.SIGNAL_DB_PATH).resolve()
    if not db_path.is_file():
        print("Keine Tracker-Datenbank vorhanden.")
        return 0
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM signals WHERE mail_class = 'trade' "
                "ORDER BY created_at ASC, id ASC"
            ).fetchall()
        ]
    rows, cohort = st.select_performance_cohort(
        rows, days=max(1, args.days), as_of=as_of, mature_only=not args.include_recent,
    )
    if args.scanner:
        rows = [r for r in rows if str(r.get("scanner") or "") == args.scanner]

    # Zellen: (scanner, Bucket) -> {signals, r[], managed[]}
    # Bucket = "JJJJ-MM" (Default) oder "JJJJ-MM-TT" (--per-day)
    bucket_len = 10 if args.per_day else 7
    cells: dict = defaultdict(list)
    for row in rows:
        cohort_time = (st._signal_causal_start(row).isoformat() if args.include_recent
                       else row.get("maturity_at"))
        month = str(cohort_time or "")[:bucket_len] or "unbekannt"
        scanner = str(row.get("scanner") or "unknown")
        for key in ((scanner, month), ("GESAMT", month)):
            cells[key].append(row)

    scanners = sorted({s for s, _ in cells if s != "GESAMT"})
    if not scanners:
        print("Keine Signale im Fenster.")
        return 0

    print(f"Fenster: {args.days} Tage | Signale: {len(rows)} | "
          f"Scanner: {len(scanners)}")
    print("Kohorte: " + ("Versandzeit, vorlaeufig" if args.include_recent else "Reifezeit, vollstaendig beobachtet"))
    print(f"Noch nicht reif: {cohort['excluded_not_mature']}")
    print("Semantik: gleiche Kohorten- und Fill/Terminal-Pruefung wie Hauptstatistik; "
          "Level-R vor allgemeinen Kosten, keine Broker-PnL. n < 30 nicht belastbar.\n")

    label = "Tag" if args.per_day else "Monat"
    width = 11 if args.per_day else 9
    header = (f"{label:<{width}}{'Sig':>5}{'Entsch':>8}{'Win%':>7}{'KI95':>11}"
              f"{'ØR':>8}{'ØR5050':>9}{'ΣR':>9}  Anmerkung")
    for scanner in scanners + ["GESAMT"]:
        months = sorted(m for s, m in cells if s == scanner)
        if not months:
            continue
        print(f"== {scanner} " + "=" * max(4, 88 - len(scanner)))
        print(header)
        print("-" * 100)
        for month in months:
            cell = st._performance_bucket_for_rows(cells[(scanner, month)], max(1, args.days), as_of)
            decided = cell["decided_signals"]
            win_pct = f"{cell['win_rate_pct']:.0f}%" if decided else "—"
            ci_raw = cell["win_rate_wilson_95"]
            ci = f"{ci_raw['lower_pct']:.0f}–{ci_raw['upper_pct']:.0f}%" if ci_raw else "—"
            avg_r = f"{cell['avg_r']:+.2f}" if cell["avg_r"] is not None else "—"
            avg_m = f"{cell['avg_r_managed_50_50']:+.2f}" if cell["avg_r_managed_50_50"] is not None else "—"
            sum_r = f"{cell['sum_r']:+.1f}" if decided else "+0.0"
            note = ("n < 30; " if 0 < decided < 30 else "") + f"BE unaufgeloest: {cell['managed_be_unresolved']}"
            print(f"{month:<{width}}{cell['signals']:>5}{decided:>8}{win_pct:>7}"
                  f"{ci:>11}{avg_r:>8}{avg_m:>9}{sum_r:>9}  {note}")
        print()

    print("Lies: Kippte die Quote ab einem bestimmten Zeitpunkt (Regime-Wechsel), "
          "oder war sie nur bei kleiner Stichprobe hoch? "
          "Beides siehst du oben direkt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
