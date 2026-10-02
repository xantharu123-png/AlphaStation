"""Aggregate an existing private tracker export, without DB/network writes.

Legacy observed results are separate from the current-code market replay.
Ticker/contract is absent from this projected export; no asset samples inferred.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from scripts.signal_performance_breakdown import read_collected_snapshot, build_evidence_report
from scripts.scanner_history_audit import WINDOW_START,WINDOW_END


def report(snapshot,output):
    from modules import signal_tracker as tracker
    snapshot,output=Path(snapshot).resolve(),Path(output).resolve()
    if (ROOT/"output").resolve() not in output.parents or output.exists():
        raise ValueError("use_new_private_report_output")
    rows,inventory,captured=read_collected_snapshot(snapshot,datetime.now(timezone.utc))
    as_of=min(captured,WINDOW_END)
    selected=[r for r in rows if (start:=tracker._signal_causal_start(r)) is not None
              and WINDOW_START<=start<WINDOW_END and start<=as_of]
    grouped=defaultdict(list)
    for row in selected:
        grouped[(row.get("scanner") or "unknown",row.get("strategy") or "unknown")].append(row)
    cells=[]
    for (scanner,strategy),records in sorted(grouped.items()):
        result=build_evidence_report(records,inventory,days=92,as_of=as_of,mature_only=False)
        qualified=[r for r in records if tracker._has_trade_qualified_origin(r)]
        qualified_report=build_evidence_report(qualified,inventory,days=92,as_of=as_of,mature_only=False)
        cells.append(dict(scanner=scanner,strategy=strategy,recorded_rows=len(records),
                          statuses=dict(Counter(r.get("status") or "unknown" for r in records)),
                          metrics=result["aggregate_descriptive_only"],
                          qualified_origin_metrics=qualified_report["aggregate_descriptive_only"],
                          unknown_revision_rows=sum(not r.get("code_revision") for r in records),
                          unknown_model_rows=sum(not r.get("evaluation_model_version") for r in records),
                          quarantined_future_outcome_rows=result["cohort"]["quarantined_future_outcome_rows"]))
    result=dict(kind="legacy_real_server_tracker_descriptive_snapshot",read_only=True,
                source_file=snapshot.name,source_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                captured_at=captured.isoformat(),requested_start=WINDOW_START.isoformat(),
                requested_end_exclusive=WINDOW_END.isoformat(),observable_end=as_of.isoformat(),
                missing_later_period=as_of<WINDOW_END,full_three_month_coverage=False,
                tracker_inventory=inventory,window_recorded_rows=len(selected),cells=cells,
                current_scanner_win_rate=None,net_pnl_usd=None,
                limits=["ticker_and_contract_not_exported_asset_spot_samples_impossible",
                        "legacy_rows_not_current_repaired_scanner_versions",
                        "recorded_tracker_outcomes_not_actual_broker_execution",
                        "gross_price_path_no_general_costs_do_not_assume_zero",
                        "legacy_unknown_origin_separate_from_qualified_origin",
                        "local_fixture_contaminated_tracker_not_used"])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    lines=["# Alte reale Tracker-Ergebnisse – getrennt vom neuen Replay", "",
           f"Gewuenscht: 02.07.–01.10.2026; vorhandener Serverexport endet {captured.isoformat()}.",
           "Keine Quote der aktuellen Reparaturen; keine Netto-Kontorendite. Unbekannte Herkunft bleibt unbekannt.","",
           "| Scanner | Strategie | Zeilen | Entschiedene Fuellpfade | Brutto-positive Pfade | 95%-Intervall | qualifizierte Herkunft |",
           "|---|---|---:|---:|---:|---|---:|"]
    for cell in cells:
        m=cell["metrics"]; interval=m.get("win_rate_wilson_95")
        rate=m.get("win_rate_pct"); rate_text="nicht berechenbar" if rate is None else f"{rate:.2f}%"
        interval_text="–" if not interval else f"{interval['lower_pct']:.1f}–{interval['upper_pct']:.1f}%"
        lines.append(f"| {cell['scanner']} | {cell['strategy']} | {cell['recorded_rows']} | {m.get('decided_signals',0)} | {rate_text} | {interval_text} | {m.get('qualified_origin_rows',0)} |")
    lines += ["", "Offene, ungefuellte und fehlend belegte Ergebnisse zaehlen nicht pauschal als Verlust.",
              "Die JSON-Datei enthaelt Statuszaehler, getrennte qualifizierte Herkunft und Evidenzluecken.",
              "Ticker/Contract fehlen in diesem Export; daher keine erfundenen 2–3 Originalaktien pro Scanner.",
              "Die lokale SQLite-Historie enthaelt Testdatensaetze und wird nicht als Produktionsleistung genutzt."]
    output.with_suffix(".md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();r=report(a.snapshot,a.output)
    print(json.dumps(dict(recorded_rows=r["window_recorded_rows"],groups=len(r["cells"]),
                         captured_at=r["captured_at"],current_scanner_win_rate=None)))
