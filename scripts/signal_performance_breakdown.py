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
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# The imported tracker reads code identity via git status; never refresh index.
os.environ["GIT_OPTIONAL_LOCKS"] = "0"


# Internal arithmetic inputs only. Recipients, execution-context blobs, tokens,
# raw mail bodies and account identifiers are never read/exported by this tool.
REPORT_COLUMNS = tuple("""
id created_at scanner mail_class channel status asset_class direction strategy
trade_horizon evaluation_horizon_bars market_regime code_revision
evaluation_model_version fill_evidence_mode path_extrema_evidence_mode
origin_evidence public_signal_ref delivery_accepted_at entry stop tp1 tp2
entry_filled_at entry_fill_price closed_at r_realized r_realized_upper
tp1_hit_at tp2_hit_at stop_hit_at outcome_detail max_favorable_r
be_trigger_at be_activated_at be_mail_sent_at be_delivery_evidence_key
be_exit_at be_exit_fill_price be_exit_evidence_mode be_exit_tp1_order
stop_gap_slippage_r
""".split())
REQUIRED_COLUMNS = frozenset({"id", "created_at", "scanner", "mail_class", "status"})
CELL_DIMENSIONS = (
    "scanner", "strategy", "asset_class", "direction", "trade_horizon",
    "evaluation_horizon_bars", "market_regime", "code_revision",
    "evaluation_model_version", "fill_evidence_mode", "origin_evidence", "channel",
)
OUTCOME_TIMESTAMPS = (
    "entry_filled_at", "closed_at", "tp1_hit_at", "tp2_hit_at", "stop_hit_at",
    "be_trigger_at", "be_activated_at", "be_mail_sent_at", "be_exit_at",
)
REPORT_METRICS = tuple("""
signals open tp1_hit tp2_hit stop_hit expired no_fill untracked decided_signals
win_rate_pct win_rate_wilson_95 avg_r sum_r avg_r_managed_50_50
managed_be_decided_signals managed_be_unresolved avg_r_managed_50_50_be
ambiguous_outcomes upper_unresolved control_eligible_signals
control_resolved_signals control_unresolved terminal_r_unresolved
stop_gap_exits avg_stop_gap_slippage_r
""".split())


def read_snapshot(db_path):
    """Read an explicit SQLite transaction without creating/migrating any DB."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError("Tracker-Datenbank fehlt; keine Datei wurde angelegt.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(signals)")}
        if not REQUIRED_COLUMNS <= columns:
            raise ValueError("Tracker-Schema unvollstaendig; keine Migration ausgefuehrt.")
        selected = [name for name in REPORT_COLUMNS if name in columns]
        # Identifiers come only from our static allowlist, never a DB/user string.
        rows = [dict(row) for row in conn.execute(
            "SELECT " + ",".join('"' + name + '"' for name in selected)
            + " FROM signals WHERE mail_class = 'trade' ORDER BY created_at, id"
        )]
        inventory = dict(conn.execute(
            "SELECT COUNT(*) AS all_rows, "
            "COUNT(CASE WHEN mail_class='trade' THEN 1 END) AS trade_rows, "
            "COUNT(CASE WHEN mail_class='shadow' THEN 1 END) AS shadow_rows, "
            "COUNT(CASE WHEN mail_class IS NULL OR mail_class NOT IN ('trade','shadow') "
            "THEN 1 END) AS other_rows FROM signals"
        ).fetchone())
    inventory["missing_report_columns"] = sorted(set(REPORT_COLUMNS) - columns)
    return rows, inventory


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Non-finite JSON constant")


def read_collected_snapshot(path, now):
    """Consume a private projected server export, not a mutable historical replay."""
    path = Path(path)
    if path.stat().st_size > 200 * 1024 * 1024:
        raise ValueError("Evidence file too large")
    payload = json.loads(path.read_text(encoding="utf-8-sig"),
                         object_pairs_hook=_unique_json_object,
                         parse_constant=_invalid_json_constant)
    if (not isinstance(payload, dict) or payload.get("kind") != "private_server_evidence"
            or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
            or payload.get("read_only") is not True):
        raise ValueError("Not a supported server snapshot")
    stamp = payload.get("captured_at")
    if not isinstance(stamp, str) or len(stamp) > 64:
        raise ValueError("Missing capture time")
    try:
        captured = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Capture time is not ISO formatted") from None
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("Capture time must include timezone")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Comparison time must include timezone")
    captured = captured.astimezone(timezone.utc)
    if captured > now:
        raise ValueError("Missing or future capture time")
    tracker = payload.get("tracker")
    if not isinstance(tracker, dict):
        raise ValueError("Missing tracker snapshot")
    rows, inventory = tracker.get("rows"), tracker.get("inventory")
    if not isinstance(rows, list) or not isinstance(inventory, dict) or len(rows) > 100000:
        raise ValueError("Invalid tracker snapshot")
    count_keys = ("all_rows", "trade_rows", "shadow_rows", "other_rows")
    if set(inventory) != {*count_keys, "missing_report_columns"}:
        raise ValueError("Incomplete inventory schema")
    if any(type(inventory.get(key)) is not int or not 0 <= inventory[key] <= 2**63 - 1 for key in count_keys):
        raise ValueError("Inventory counts must be nonnegative integers")
    if inventory["all_rows"] != sum(inventory[key] for key in count_keys[1:]):
        raise ValueError("Inventory populations do not sum to total")
    if inventory["trade_rows"] != len(rows):
        raise ValueError("Incomplete projected trade population")
    missing = inventory["missing_report_columns"]
    if (not isinstance(missing, list) or any(not isinstance(key, str) for key in missing)
            or len(missing) != len(set(missing)) or not set(missing) <= set(REPORT_COLUMNS)
            or REQUIRED_COLUMNS & set(missing)):
        raise ValueError("Invalid missing-column inventory")
    expected_columns = set(REPORT_COLUMNS) - set(missing)
    seen_ids = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) & set(REPORT_COLUMNS) != expected_columns
                or row.get("mail_class") != "trade"):
            raise ValueError("Invalid projected trade row schema")
        ident = row.get("id")
        if type(ident) is not int or not 0 < ident <= 2**63 - 1 or ident in seen_ids:
            raise ValueError("Trade row IDs must be positive and unique")
        seen_ids.add(ident)
        for key in expected_columns:
            value = row[key]
            if value is not None and type(value) not in (str, int, float):
                raise ValueError("Projected values must be SQLite scalars")
            if type(value) is float and not math.isfinite(value):
                raise ValueError("Projected values must be finite")
    # Ignore extraneous fields from a supplied file; never export raw rows.
    selected = [{key: value for key, value in row.items() if key in REPORT_COLUMNS} for row in rows]
    allowed_inventory = {key: inventory[key] for key in count_keys}
    allowed_inventory["missing_report_columns"] = sorted(missing)
    return selected, allowed_inventory, captured


def _explicit_dimension(row, key, st):
    if key == "origin_evidence":
        return st.normalize_origin_evidence(row.get(key))
    raw = row.get(key)
    # Do not infer LONG, stock, a regime or a version when it was never stored.
    return str(raw).strip() if raw is not None and str(raw).strip() else "unknown"


def build_evidence_report(candidate_rows, inventory, *, days, as_of,
                          mature_only=True, scanner="", per_day=False):
    """Aggregate-only snapshot, not historical replay or a trading release.

    Reuse the existing tracker arithmetic, explicitly gross and descriptive.
    Future-dated outcome evidence is quarantined, never counted as a past win.
    No data/setting write, market call or broker import occurs.
    """
    from modules import signal_tracker as st

    if as_of.tzinfo is None:
        raise ValueError("Report time must include its timezone")
    as_of = as_of.astimezone(timezone.utc)
    candidates = [dict(row) for row in candidate_rows
                  if row.get("mail_class") == "trade"
                  and (not scanner or row.get("scanner") == scanner)]
    selected, cohort = st.select_performance_cohort(
        candidates, days=days, as_of=as_of, mature_only=mature_only,
    )
    rows, future_outcomes = [], 0
    for row in selected:
        if any((stamp := st._parse_utc_datetime(row.get(key))) is not None and stamp > as_of
               for key in OUTCOME_TIMESTAMPS):
            future_outcomes += 1
        else:
            rows.append(row)
    groups = defaultdict(list)
    for row in rows:
        stamp = (row.get("maturity_at") if mature_only
                 else st._signal_causal_start(row).isoformat())
        key = (str(stamp)[:10 if per_day else 7],
               *(_explicit_dimension(row, name, st) for name in CELL_DIMENSIONS))
        groups[key].append(row)

    def metrics(items):
        bucket = st._performance_bucket_for_rows(items, days, as_of)
        result = {key: bucket.get(key) for key in REPORT_METRICS}
        if not result.get("decided_signals"):
            result["sum_r"] = None  # No outcomes is not a measured 0R return.
        result["qualified_origin_rows"] = sum(st._has_trade_qualified_origin(row) for row in items)
        return result

    starts = [stamp for row in candidates
              if (stamp := st._signal_causal_start(row)) is not None and stamp <= as_of]
    return {
        "schema_version": 1,
        "report_kind": "tracker_evidence_snapshot",
        "as_of": as_of.isoformat(), "window_days": days,
        "cohort_mode": "fully_observed" if mature_only else "created_in_window_provisional",
        "maturity_meaning": "planned_window_elapsed_not_complete_outcome_evidence",
        "scanner_filter": scanner or None,
        "bucket": "day" if per_day else "month",
        "read_only": True, "historical_replay": False,
        "cost_basis": "gross_price_path_no_general_roundtrip_costs",
        "net_expectancy_r": None, "account_pnl_usd": None,
        "grants_paper_or_live_permission": False,
        "source_inventory_all_history": inventory,
        "filtered_trade_history": {
            "rows": len(candidates),
            "oldest_causal_start": min(starts).isoformat() if starts else None,
            "newest_causal_start": max(starts).isoformat() if starts else None,
            "missing_or_invalid_causal_start": sum(st._signal_causal_start(row) is None for row in candidates),
            "future_causal_start": sum(st._signal_causal_start(row) > as_of for row in candidates
                                       if st._signal_causal_start(row) is not None),
            "pending_delivery": sum(row.get("status") == st.STATUS_PENDING_DELIVERY for row in candidates),
            "unknown_code_revision": sum(_explicit_dimension(row, "code_revision", st) == "unknown" for row in candidates),
            "unknown_evaluation_model": sum(_explicit_dimension(row, "evaluation_model_version", st) == "unknown" for row in candidates),
        },
        "cohort": {**cohort, "quarantined_future_outcome_rows": future_outcomes,
                   "rows_in_metrics": len(rows)},
        "population_coverage": {
            "trade_tracker": "recorded_trade_rows_only; origin_evidence_is_separate",
            "app_signal_population": "unavailable_from_this_source",
            "shadow": "inventory_count_only; not_all_app_signals",
            "broker_fills_and_complete_costs": "unavailable_from_this_source",
        },
        "aggregate_descriptive_only": metrics(rows),
        "cell_dimensions": ["period", *CELL_DIMENSIONS],
        "cells": [{"identity": dict(zip(("period", *CELL_DIMENSIONS), key)),
                   "metrics": metrics(items)} for key, items in sorted(groups.items())],
        "warnings": [
            "Brutto-Planpfad, keine Netto-Kontorendite; Kosten nicht als null angenommen.",
            "Alte/fehlende Herkunft und Modellversion sind keine aktuelle Scanner-Evidenz.",
            "Kleine, offene oder unvollstaendige Kohorten erlauben kein Gewinner-Ranking.",
            "Deskriptives R erbt Legacy-Arithmetik; control_unresolved zeigt strengere Evidenzluecken.",
            "Wilson-Intervalle sind nicht um korrelierte/ueberlappende Signale bereinigt.",
            "Bestand zum Abfragezeitpunkt, kein historischer Point-in-time-Replay.",
            "Keine Rohzeilen, Empfaenger, Konten oder Zustellreferenzen im Export.",
        ],
    }


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
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help="Expliziter Tracker-Pfad; nur lesend, keine neue DB")
    source.add_argument("--snapshot-json", type=Path,
                        help="Privaten Serverexport offline am erfassten Zeitpunkt auswerten")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="JSON: versionsgetrennte aggregierte Evidenz, keine Rohdaten")
    args = parser.parse_args()

    from modules import signal_tracker as st

    as_of = datetime.now(timezone.utc)
    db_path = args.db or Path(st.SIGNAL_DB_PATH)
    try:
        if args.snapshot_json:
            rows, inventory, as_of = read_collected_snapshot(args.snapshot_json, as_of)
        else:
            rows, inventory = read_snapshot(db_path)
        if args.format == "json":
            report = build_evidence_report(
                rows, inventory, days=max(1, args.days), as_of=as_of,
                mature_only=not args.include_recent, scanner=args.scanner, per_day=args.per_day,
            )
            report["source_kind"] = "imported_server_snapshot" if args.snapshot_json else "local_sqlite"
            report["source_authenticity"] = "not_cryptographically_attested"
            print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        # Do not turn missing/invalid evidence into a successful empty report.
        # Never leak a raw database error/path/row in machine-readable output.
        print("Auswertung nicht verfuegbar: DB/Pfad/Schema/Daten pruefen; nichts migriert.", file=sys.stderr)
        return 2
    print(f"Snapshot: {args.snapshot_json}" if args.snapshot_json else f"DB: {db_path}")
    if args.scanner:
        rows = [r for r in rows if str(r.get("scanner") or "") == args.scanner]
    rows, cohort = st.select_performance_cohort(
        rows, days=max(1, args.days), as_of=as_of, mature_only=not args.include_recent,
    )

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
    print("Diese Textansicht aggregiert Versionen. Versions-/Herkunftszellen: --format json\n")

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
