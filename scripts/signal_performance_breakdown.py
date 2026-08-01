#!/usr/bin/env python3
"""Performance-Breakdown pro Scanner x Kalendermonat.

AUDIT 2026-07-29 (Hit-Rate-Frage). Der Wochenreport aggregiert das Fenster
in EINEN Bucket pro Scanner — damit ist unsichtbar, WANN eine Quote kippte
("frueher ueber 70%"?). Dieses Skript bricht exakt dieselbe Metrik auf
Monatszellen herunter:

  entschieden = Signale mit r_realized (Tracker-Semantik, identisch zum
                Wochenreport)
  Win         = r_realized > 0
  ØR 50/50    = R des empfohlenen 50/50-Managements (T1)

So ist auf einen Blick sichtbar, ob es fruehere Monate mit >70% gab, ob die
Quote mit Stichprobengroesse/Regime schwankt und welcher Scanner sie zieht
oder drueckt. Zellen mit n < 30 sind markiert: keine belastbare Aussage.

Usage (auf dem Server, im App-Verzeichnis):
    venv/bin/python3 scripts/signal_performance_breakdown.py
    venv/bin/python3 scripts/signal_performance_breakdown.py --days 365
    venv/bin/python3 scripts/signal_performance_breakdown.py --scanner stock_strategy
    venv/bin/python3 scripts/signal_performance_breakdown.py --days 21 --per-day

--per-day (AUDIT 2026-08-01, 14-Tage-Kollaps): Monatszellen sind zu grob,
wenn eine Quote mitten im Monat kippt (44% -> 16% -> 0% innerhalb von
14 Tagen). Pro-Tag-Zellen machen sichtbar, an welchem Tag die Signal-Flut
begann, ob sich Verlierer um bestimmte Ereignisse clustern (z. B.
Fed-Mittwoch) und ob Signale NACH einem Fix-Deploy wieder performen.

Exit 0 immer (Analyse-Tool, kein Gate).
"""
from __future__ import annotations  # Annotations lazy: py3.8-kompatibel

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
    args = parser.parse_args()

    from modules import signal_tracker as st

    print(f"DB: {st.SIGNAL_DB_PATH}")
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max(1, args.days))).isoformat()
    with st._db_connection() as conn:  # WAL/Migration wie im Tracker
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM signals WHERE created_at >= ? "
                "AND mail_class = 'trade' "
                "ORDER BY created_at ASC, id ASC",
                (cutoff_iso,),
            ).fetchall()
        ]
    if args.scanner:
        rows = [r for r in rows if str(r.get("scanner") or "") == args.scanner]

    # Zellen: (scanner, Bucket) -> {signals, r[], managed[]}
    # Bucket = "JJJJ-MM" (Default) oder "JJJJ-MM-TT" (--per-day)
    bucket_len = 10 if args.per_day else 7
    cells: dict = defaultdict(lambda: {"signals": 0, "r": [], "managed": []})
    for row in rows:
        month = str(row.get("created_at") or "")[:bucket_len] or "unbekannt"
        scanner = str(row.get("scanner") or "unknown")
        for key in ((scanner, month), ("GESAMT", month)):
            cell = cells[key]
            cell["signals"] += 1
            r_value = row.get("r_realized")
            if r_value is not None:
                cell["r"].append(float(r_value))
            managed = st._managed_r_50_50(row)
            if managed is not None:
                cell["managed"].append(managed)

    scanners = sorted({s for s, _ in cells if s != "GESAMT"})
    if not scanners:
        print("Keine Signale im Fenster.")
        return 0

    print(f"Fenster: {args.days} Tage | Signale: {len(rows)} | "
          f"Scanner: {len(scanners)}")
    print("Semantik: Win = r_realized > 0 über entschiedene Signale "
          "(= Wochenreport). n < 30: Stichprobe zu klein für belastbare Quote.\n")

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
            cell = cells[(scanner, month)]
            r_values = cell["r"]
            decided = len(r_values)
            wins = sum(1 for v in r_values if v > 0)
            win_pct, ci = _fmt_cell(wins, decided, st._wilson_interval_95(wins, decided))
            avg_r = f"{sum(r_values) / decided:+.2f}" if decided else "—"
            managed = cell["managed"]
            avg_m = f"{sum(managed) / len(managed):+.2f}" if managed else "—"
            sum_r = f"{sum(r_values):+.1f}" if decided else "+0.0"
            note = "n < 30" if 0 < decided < 30 else ""
            print(f"{month:<{width}}{cell['signals']:>5}{decided:>8}{win_pct:>7}"
                  f"{ci:>11}{avg_r:>8}{avg_m:>9}{sum_r:>9}  {note}")
        print()

    print("Lies: Kippte die Quote ab einem bestimmten Zeitpunkt (Regime-Wechsel), "
          "oder war sie nur bei kleiner Stichprobe hoch? "
          "Beides siehst du oben direkt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
