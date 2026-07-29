#!/usr/bin/env python3
"""Exit-Effizienz-Analyse (AUDIT 2026-07-29, Folge der WS-Messung).

Befund der WebSocket-Messung: Das System loest Signale in Tagen auf (Median
TP1-Zeit 2,3 d), Transport-Speed ist kein Hebel. Aber: MFE-Nutzung -22% —
das mediane Signal realisiert nur -22% seines Maximalgewinns. Offene Gewinne
werden systematisch verschenkt. DIESES Skript misst, wie gross der Leck ist
und welche Management-Regel ihn schliesst — alles aus signal_tracker.sqlite,
kein API noetig.

Gemessen (pro Scanner + gesamt):
  * Giveback-Quote: Anteil Signale mit MFE >= +1R, aber r_realized <= 0
    ("war 1R im Plus, endete im Verlust") — die Kernzahl.
  * Teil-Giveback: MFE >= +1R, aber r_realized < +0.5R.
  * Ø Giveback in R (MFE - realized) bei MFE >= 0.5R.
  * Haltezeit-Buckets (closed_at - created_at): <24h / 1-3d / >3d.

Gegenprobe (pure Funktionen aus modules.signal_tracker, getestet):
  Regel A "BE nach +1R":        simulate_breakeven_after_mfe
  Regel B "50/50 + BE-Rest":    simulate_managed_5050_breakeven
  Referenz "50/50 (Ist-Empf.)": _managed_r_50_50

Ehrliche Limitationen (werden eingeblendet):
  * Aktien-MFE kommt aus Daily-OHLC — Intraday-Spitzen darueber hinaus
    sieht die DB nicht (Giveback ist eher GROESSER als gemessen).
  * Crypto-MFE ist Spot-Check-Stichprobe (stuendlich) — unterschaetzt MFE
    deutlich; Crypto-Zahlen nur richtungsweisend.
  * 'ambiguous_same_day' bleibt konservativ unveraendert (Reihenfolge
    unbekannt) — die Simulation untertreibt den Regel-Nutzen eher.

Usage (auf dem Server, im App-Verzeichnis):
    venv/bin/python3 scripts/exit_efficiency_analysis.py
    venv/bin/python3 scripts/exit_efficiency_analysis.py --days 120

Exit 0 immer (Analyse-Tool, kein Gate).
"""
from __future__ import annotations  # Annotations lazy: py3.8-kompatibel

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _f(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _fmt(value: float | None, suffix: str = "") -> str:
    return f"{value:+.2f}{suffix}" if isinstance(value, float) else "—"


def _hold_bucket(row: dict) -> str:
    created = _parse_ts(row.get("created_at"))
    closed = _parse_ts(row.get("closed_at"))
    if not created or not closed:
        return "?"
    hours = (closed - created).total_seconds() / 3600.0
    if hours < 24:
        return "<24h"
    if hours <= 72:
        return "1-3d"
    return ">3d"


def _metrics(rows: list[dict], st) -> dict:
    decided = [r for r in rows if _f(r.get("r_realized")) is not None]
    realized = []
    managed = []
    sim_a = []
    sim_b = []
    gb_full = 0      # MFE >= 1R, realized <= 0
    gb_partial = 0   # MFE >= 1R, realized < 0.5R
    gb_amounts = []
    mfe1 = 0
    for r in decided:
        real = _f(r.get("r_realized"))
        mfe = _f(r.get("max_favorable_r"))
        realized.append(real)
        m = st._managed_r_50_50(r)
        a = st.simulate_breakeven_after_mfe(r, 1.0)
        b = st.simulate_managed_5050_breakeven(r)
        if m is not None:
            managed.append(m)
        if a is not None:
            sim_a.append(a)
        if b is not None:
            sim_b.append(b)
        if mfe is not None and mfe >= 1.0:
            mfe1 += 1
            if real <= 0:
                gb_full += 1
            if real < 0.5:
                gb_partial += 1
        if mfe is not None and mfe >= 0.5:
            gb_amounts.append(mfe - real)
    n = len(decided)
    return {
        "n": n,
        "avg_realized": _avg(realized),
        "avg_managed": _avg(managed),
        "avg_sim_a": _avg(sim_a),
        "avg_sim_b": _avg(sim_b),
        "mfe1": mfe1,
        "gb_full": gb_full,
        "gb_partial": gb_partial,
        "avg_giveback": _avg(gb_amounts),
        "buckets": _bucket_metrics(decided),
    }


def _bucket_metrics(decided: list[dict]) -> dict:
    buckets: dict[str, list] = {}
    for r in decided:
        buckets.setdefault(_hold_bucket(r), []).append(r)
    out = {}
    for name, rows_b in buckets.items():
        real = [_f(r.get("r_realized")) for r in rows_b]
        real = [v for v in real if v is not None]
        gb = len([r for r in rows_b
                  if (_f(r.get("max_favorable_r")) or 0) >= 1.0 and (_f(r.get("r_realized")) or 0) <= 0])
        mfe1 = len([r for r in rows_b if (_f(r.get("max_favorable_r")) or 0) >= 1.0])
        out[name] = {"n": len(rows_b), "avg_realized": _avg(real), "gb_full": gb, "mfe1": mfe1}
    return out


def _print_block(title: str, m: dict) -> None:
    header = f"{'Scanner':<16} {'n':>4} {'ØR ist':>8} {'ØR 5050':>8} {'ØR A':>8} {'ØR B':>8} {'MFE≥1R':>7} {'→≤0':>6} {'→<0.5R':>7} {'ØGiveb.':>8}"
    print(f"\n--- {title} ---")
    print(header)
    print("-" * len(header))
    for row in m:
        print(
            f"{row['name']:<16} {row['n']:>4} {_fmt(row['avg_realized']):>8} {_fmt(row['avg_managed']):>8} "
            f"{_fmt(row['avg_sim_a']):>8} {_fmt(row['avg_sim_b']):>8} "
            f"{row['mfe1']:>7} {row['gb_full']:>6} {row['gb_partial']:>7} {_fmt(row['avg_giveback']):>8}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Fenster in Tagen (Default 90)")
    parser.add_argument("--db", type=str, default="", help="Pfad zur signal_tracker.sqlite (Default: App-Pfad)")
    args = parser.parse_args()

    from modules import signal_tracker as st

    db_path = args.db or str(st.SIGNAL_DB_PATH)
    print(f"DB: {db_path}")
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max(1, args.days))).isoformat()

    if args.db:
        import sqlite3

        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE created_at >= ? ORDER BY created_at ASC, id ASC", (cutoff_iso,)
        ).fetchall()]
        conn.close()
    else:
        with st._db_connection() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM signals WHERE created_at >= ? ORDER BY created_at ASC, id ASC", (cutoff_iso,)
            ).fetchall()]

    print(f"Fenster: {args.days} Tage | Signale: {len(rows)}")
    print("Regel A = BE-Stop nach +1R | Regel B = 50/50 + BE-Rest nach TP1 | ØR 5050 = Ist-Empfehlung")
    print("(Giveback-Spalten: von Signalen mit MFE≥+1R endeten X ≤ 0 bzw. < +0.5R)")

    by_scanner: dict[str, list[dict]] = {}
    for r in rows:
        by_scanner.setdefault(str(r.get("scanner") or "?"), []).append(r)

    blocks = []
    for scanner in sorted(by_scanner, key=lambda s: -len(by_scanner[s])):
        m = _metrics(by_scanner[scanner], st)
        m["name"] = scanner
        blocks.append(m)
    total = _metrics(rows, st)
    total["name"] = "GESAMT"
    blocks.append(total)
    _print_block("Pro Scanner (entschiedene Signale)", blocks)

    print("\n--- Haltezeit-Buckets (GESAMT) ---")
    print(f"{'Bucket':<8} {'n':>5} {'ØR ist':>8} {'MFE≥1R':>7} {'→≤0':>5}")
    print("-" * 40)
    for name in ("<24h", "1-3d", ">3d", "?"):
        b = total["buckets"].get(name)
        if b:
            print(f"{name:<8} {b['n']:>5} {_fmt(b['avg_realized']):>8} {b['mfe1']:>7} {b['gb_full']:>5}")

    # Empfehlungslogik
    print("\n=== AUSWERTUNG ===")
    n = total["n"]
    if not n:
        print("  Keine entschiedenen Signale — nichts zu messen.")
        return 0
    share_full = 100.0 * total["gb_full"] / total["mfe1"] if total["mfe1"] else 0.0
    d_a = (total["avg_sim_a"] - total["avg_realized"]) if (total["avg_sim_a"] is not None and total["avg_realized"] is not None) else None
    d_b = (total["avg_sim_b"] - total["avg_realized"]) if (total["avg_sim_b"] is not None and total["avg_realized"] is not None) else None
    d_b_ref = (total["avg_sim_b"] - total["avg_managed"]) if (total["avg_sim_b"] is not None and total["avg_managed"] is not None) else None
    print(f"  Signale mit MFE ≥ +1R: {total['mfe1']} von {n} ({100.0 * total['mfe1'] / n:.0f}%)")
    print(f"  davon ≤ 0 geendet (Total-Giveback): {total['gb_full']} = {share_full:.0f}% der MFE≥1R-Faelle")
    print(f"  davon < +0.5R geendet (Teil-Giveback): {total['gb_partial']}")
    if total["avg_giveback"] is not None:
        print(f"  Ø verschenkt pro Signal mit MFE ≥ 0.5R: {total['avg_giveback']:+.2f}R")
    if d_a is not None:
        print(f"\n  Regel A (BE nach +1R):        ØR {_fmt(total['avg_realized'])} → {_fmt(total['avg_sim_a'])}  ({d_a:+.3f}R/Signal)")
    if d_b is not None:
        print(f"  Regel B (50/50 + BE-Rest):    ØR {_fmt(total['avg_realized'])} → {_fmt(total['avg_sim_b'])}  ({d_b:+.3f}R/Signal"
              + (f"; vs. Ist-50/50 {_fmt(total['avg_managed'])}: {d_b_ref:+.3f}R)" if d_b_ref is not None else ")"))
    print("\n  Faustregeln:")
    print("   * Total-Giveback > 20% UND Regel-Delta > +0.10R/Signal → Regel implementieren (Monitor zieht Stop nach).")
    print("   * Giveback konzentriert in >3d-Bucket → eher TP1 straffen/Trailen als BE.")
    print("   * Delta < +0.05R → Leck zu klein; Management bleibt wie empfohlen (50/50).")
    print("\n  Limitationen: Aktien-MFE aus Daily-OHLC (unterschaetzt Intraday-Spitzen),")
    print("  Crypto-MFE = Spot-Stichprobe (unterschaetzt deutlich). 'ambiguous_same_day'")
    print("  bleibt konservativ — Regel-Nutzen wird eher UNTER- als ueberschaetzt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
