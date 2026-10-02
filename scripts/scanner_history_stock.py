"""Causal three-asset replay of the actual public stock producer.

Frozen adjusted OHLCV replaces only I/O. Selection, pattern filters, native
levels and candidate classification are production code. Daily fills are a
separately declared research model, NOT historical SMTP/broker equivalence.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import hashlib
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.scanner_history_audit import STOCKS, WINDOW_START, WINDOW_END, fingerprint
from scripts.scanner_history_runtime import (
    isolated_application, imported_scanner_source_fingerprints, require_unchanged_scanner_sources,
)
_IMPORT_SOURCE_FINGERPRINTS = imported_scanner_source_fingerprints()

UTC = timezone.utc


def load_source(directory, symbol, multiplier, span):
    source = json.loads((directory / f"stock-{symbol}-{multiplier}{span}.json").read_text(encoding="utf-8"))
    if (source.get("symbol") != symbol or source.get("venue") != "us_equity_polygon"
            or source.get("adjusted") is not True or source.get("multiplier") != multiplier
            or source.get("span") != span or source.get("bars_sha256") != fingerprint(source["bars"])):
        raise ValueError("frozen_market_source_mismatch")
    from scripts.scanner_history_bi import canonical_daily_bars, _number
    if span == "day":
        canonical_daily_bars(source["bars"])
    else:
        from scripts.scanner_history_audit import validate_ohlcv
        previous = 0
        for raw in source["bars"]:
            validate_ohlcv(raw)
            stamp = _number(raw["t"])
            if stamp <= 10_000_000_000 or stamp != int(stamp) or stamp % (multiplier*60*1000) or stamp <= previous:
                raise ValueError("frozen_intraday_clock_or_duplicate_invalid")
            previous = stamp
    return source


def summarize_observations(records):
    terminal = [r for r in records if r.get("entry_filled") is True
                and r.get("evaluation_status") == "DECIDED"
                and type(r.get("r_multiple")) in (float, int) and math.isfinite(r["r_multiple"])]
    unambiguous = [r for r in terminal if not r.get("intrabar_ambiguous")]
    winners = sum(r["r_multiple"] > 0 for r in unambiguous)
    count = len(unambiguous)
    interval = None
    if count:
        z = 1.959963984540054
        p = winners / count
        center = (p + z*z/(2*count)) / (1+z*z/count)
        half = z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))/(1+z*z/count)
        interval = [100*(center-half), 100*(center+half)]
    return {"modeled_plans": len(records), "fills": sum(r.get("entry_filled") is True for r in records),
        "no_fill": sum(r.get("outcome") == "NO_FILL" for r in records),
        "unresolved": sum(r.get("evaluation_status") != "DECIDED" and r.get("outcome") != "NO_FILL" for r in records),
        "decided_including_ambiguous_bounds": len(terminal),
        "ambiguous": sum(bool(r.get("intrabar_ambiguous")) for r in records),
        "unambiguous_decided": count, "unambiguous_wins": winners,
        "sample_win_rate_pct": 100*winners/count if count else None,
        "wilson95_pct": interval,
        "mean_net_r_unambiguous": sum(r["r_multiple"] for r in unambiguous)/count if count else None,
        "lower_sum_net_r": sum(r["r_multiple"] for r in terminal) if terminal else None,
        "upper_sum_net_r": sum(r.get("r_multiple_upper", r["r_multiple"]) for r in terminal) if terminal else None}


def model_candidate(bars, signal_index, row, direction):
    from modules.backtests import _simulate_bi_plan_daily
    plan = {key: row[key] for key in ("Entry", "StopLoss", "TP1", "TP2")}
    plan.update(entry_method="market_at_signal", plan_version="stock_native_plan_next_open_spot_v1")
    result = _simulate_bi_plan_daily(bars, signal_index + 1, plan, direction.lower(), horizon_bars=10)
    return {"asset": row.get("Ticker"), "signal_session": bars[signal_index]["date"],
            "direction": direction, "native_plan": plan, **result}


def directional_markouts(bars, signal_index, direction):
    """Reference-close movements, not modeled entry/fill, PnL or mail proof."""
    if direction not in ("LONG", "SHORT"):
        return {"kind": "directional_reference_close_diagnostic", "direction_known": False}
    sign = 1 if direction == "LONG" else -1
    reference = bars[signal_index]["close"]
    result = {"kind": "directional_reference_close_diagnostic", "direction_known": True,
              "reference_close": reference, "trade_profit_equivalent": False}
    for horizon in (1,5,10):
        index = signal_index + horizon
        result[str(horizon)] = (None if index >= len(bars) else
            {"session": bars[index]["date"], "signed_close_change_pct": sign*(bars[index]["close"]/reference-1)*100})
    return result


def summarize_markouts(records):
    summary = {}
    for horizon in (1,5,10):
        values = [r["directional_markouts"][str(horizon)]["signed_close_change_pct"]
                  for r in records if r.get("directional_markouts", {}).get(str(horizon)) is not None]
        summary[str(horizon)] = {"observations": len(values), "right_censored_or_direction_unknown": len(records)-len(values),
                                "positive_reference_movements": sum(v>0 for v in values),
                                "directional_positive_pct": 100*sum(v>0 for v in values)/len(values) if values else None,
                                "mean_signed_close_change_pct": sum(values)/len(values) if values else None}
    return {"kind": "selected_candidate_directional_diagnostic_not_trade_win_rate",
            "overlapping_observations_not_independent": True, "horizons_sessions": summary}


def replay(directory, output, only=None):
    source_fingerprints = dict(_IMPORT_SOURCE_FINGERPRINTS)
    require_unchanged_scanner_sources(source_fingerprints)
    directory, output = directory.resolve(), output.resolve()
    if (ROOT / "output").resolve() not in output.parents or output.exists():
        raise ValueError("use_new_private_report_path")
    protocol = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (protocol.get("stock_symbols") != list(STOCKS)
            or protocol.get("window") != {"start_inclusive": "2026-07-02T00:00:00Z", "end_exclusive": "2026-10-02T00:00:00Z"}):
        raise ValueError("frozen_cohort_mismatch")
    sources = {s: load_source(directory, s, 1, "day") for s in STOCKS}
    intraday = {s: load_source(directory, s, 30, "minute") for s in STOCKS}
    output.parent.mkdir(parents=True, exist_ok=True)
    with isolated_application(output.parent / (output.stem + "-isolated-state")) as api:
        from modules import stock_swing_contract as swing
        from modules.stock_bars import completed_polygon_bars
        from modules.stock_execution import aggregate_regular_session_4h_bars
        from modules.backtests import conservative_trade_exit_index as exit_index_fn

        histories = {s: completed_polygon_bars(src["bars"], multiplier=1, span="day", as_of=WINDOW_END)
                     for s, src in sources.items()}
        raw_by_date = {s: {datetime.fromtimestamp(r["t"]/1000, UTC).astimezone(swing.NY).date().isoformat(): r
                          for r in src["bars"]} for s, src in sources.items()}
        from scripts.scanner_history_bi import expected_sessions
        dates = expected_sessions(WINDOW_START.date(), WINDOW_END.date())
        missing_sessions = {s: [d for d in dates if d not in raw_by_date[s]] for s in STOCKS}
        if any(missing_sessions.values()):
            raise ValueError("frozen_daily_session_coverage_missing")
        class Clock(datetime):
            current = WINDOW_START
            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

        now_history = {}
        snapshots = []
        strategies = list(api.PUBLIC_STOCK_STRATEGIES)
        if only:
            strategies = [s for s in strategies if s in only]
        reports = {s: {"asset_observations": defaultdict(int), "selected": [], "errors": [],
                       "rejected": Counter(), "candidate_reasons": Counter(), "plans": []} for s in strategies}

        def history_io(symbol, *args, **kwargs):
            production_start=(Clock.current.astimezone(swing.NY).date()-timedelta(days=1095)).isoformat()
            return [dict(bar) for bar in now_history[symbol] if bar["date"] >= production_start][-800:]

        def four_hour_io(symbol, limit=24):
            rows = [r for r in intraday[symbol]["bars"] if r["t"]/1000 + 1800 <= Clock.current.timestamp()-900]
            # Today's adapter has an optional clock; inputs are already closed.
            return aggregate_regular_session_4h_bars(rows, swing.NY, limit=limit,
                                                   as_of=Clock.current-timedelta(seconds=900))

        def cache_path(strategy):
            # Distinct historical observation files avoid Windows antivirus
            # contention from replacing one partial cache hundreds of times.
            observation = Clock.current.date().isoformat()+"-"+fingerprint(strategy)[:16]
            return str(output.parent / (output.stem + "-isolated-state") / "runtime" / (observation+".json"))

        with ExitStack() as stack:
            stack.enter_context(patch.object(api, "datetime", Clock))
            stack.enter_context(patch.object(swing, "datetime", Clock))
            stack.enter_context(patch.object(api.time, "time", lambda: Clock.current.timestamp()))
            stack.enter_context(patch.object(api, "_fetch_strategy_snapshot_universe", lambda *a, **k: snapshots))
            stack.enter_context(patch.object(api, "fetch_stock_daily_history_strict", history_io))
            stack.enter_context(patch.object(api, "_fetch_strategy_daily_history", lambda symbol, *a, **k: history_io(symbol)))
            stack.enter_context(patch.object(api, "_fetch_recent_stock_4h_bars", four_hour_io))
            stack.enter_context(patch.object(api, "_load_common_stock_universe", lambda **k: (set(STOCKS), "fixed_historical_sample_known_CS")))
            stack.enter_context(patch.object(api, "fetch_business_quality", lambda *a, **k: {}))
            stack.enter_context(patch.object(api, "_get_market_context_snapshot", lambda: {}))
            stack.enter_context(patch.object(api, "_strategy_cache_path", cache_path))
            for date in dates:
                cutoff = swing.session_close(date)
                Clock.current = cutoff + timedelta(seconds=901)
                previous = swing.completed_sessions(Clock.current, 2)[1]
                now_history = {s: [r for r in histories[s] if r["date"] <= date] for s in STOCKS}
                snapshots = swing.universe({s: raw_by_date[s][date] for s in STOCKS},
                                          {s: raw_by_date[s].get(previous, {}) for s in STOCKS}, date)
                for strategy in strategies:
                    report = reports[strategy]
                    report["asset_observations"].update({s: report["asset_observations"][s]+1 for s in STOCKS})
                    log = io.StringIO()
                    try:
                        with redirect_stdout(log):
                            rows = api._strategy_scan_wrapper(strategy, send_email=False, publish_generic_cache=False)
                        diagnostic = json.loads(Path(cache_path(strategy)).read_text(encoding="utf-8")).get("diagnostics", {})
                        report["rejected"].update(diagnostic.get("rejected", {}))
                        for row in rows:
                            with redirect_stdout(log):
                                state = api._classify_alert_candidate("stock_strategy", row, Clock.current.timestamp())
                            report["candidate_reasons"].update(state.get("suppression_reasons", []))
                            levels = api._alert_trade_levels(row)
                            record = {"asset": row["Ticker"], "session": date, "available_at": Clock.current.isoformat(),
                                      "reference_close": row["price"], "setup_score": row.get("score"),
                                      "candidate_only_alertable": bool(state.get("alertable_now")),
                                      "blocked_by": state.get("suppression_reasons", []),
                                      "native_plan_status": row.get("native_plan_status"),
                                      "native_plan_reason": row.get("native_plan_reason"),
                                      "direction": api._infer_alert_direction(row),
                                      "native_levels_valid": bool(levels.get("valid") and not levels.get("estimated")),
                                      "pattern_count": row.get("pattern_count")}
                            if record["native_levels_valid"] and all(row.get(k) is not None for k in ("Entry", "StopLoss", "TP1", "TP2")):
                                record["levels"] = {k: row[k] for k in ("Entry", "StopLoss", "TP1", "TP2")}
                            report["selected"].append(record)
                    except Exception as error:
                        report["errors"].append({"session": date, "code": str(getattr(error, "code", type(error).__name__)),
                                                 "offline_log_tail": log.getvalue()[-3000:]})
                print(json.dumps({"historical_session": date, "completed_strategies": len(strategies)}), flush=True)

        for strategy, report in reports.items():
            occupied_until = {}
            skipped = 0
            for record in report["selected"]:
                symbol = record["asset"]
                bars = histories[symbol]
                indexes = {b["date"]: i for i,b in enumerate(bars)}
                index = indexes[record["session"]]
                diagnostic_direction = record["direction"] if strategy != api.ELLIOTT_STRATEGY else None
                record["directional_markouts"] = directional_markouts(bars, index, diagnostic_direction)
                if "levels" not in record or not record["candidate_only_alertable"]:
                    continue
                if index <= occupied_until.get(symbol, -1):
                    skipped += 1
                    continue
                row = dict(record["levels"], Ticker=symbol)
                modeled = model_candidate(bars, index, row, record["direction"])
                report["plans"].append(modeled)
                if modeled.get("entry_filled") or modeled.get("outcome") == "UNRESOLVED":
                    occupied_until[symbol] = exit_index_fn(modeled, indexes, index+1)
            report.update(metrics=summarize_observations(report["plans"]), overlap_skips=skipped,
                          directional_diagnostic=summarize_markouts(report["selected"]),
                          chronological_examples=report["selected"][:3], assets=list(STOCKS))
            report["asset_observations"] = dict(report["asset_observations"])
            report["rejected"] = dict(report["rejected"])
            report["candidate_reasons"] = dict(report["candidate_reasons"])
        result = {"kind": "actual_public_stock_producer_small_cohort_replay", "schema_version": 1,
            "manifest_sha256": fingerprint(protocol), "stock_assets": list(STOCKS), "sessions": dates,
            "window_start": WINDOW_START.isoformat(), "window_end_exclusive": WINDOW_END.isoformat(),
            "input_sha256": {s: {"1D": sources[s]["bars_sha256"], "30m": intraday[s]["bars_sha256"]} for s in STOCKS},
            "missing_sessions": missing_sessions,
            "research_fill_model": "candidate_gate_passed_native_plan_next_session_open_50_50_BE_after_TP1_10_sessions",
            "assumed_roundtrip_fee_pct": .2, "assumed_adverse_entry_exit_slippage_pct": .1,
            "global_scanner_win_rate": None, "live_delivery_equivalent": False,
            "limits": ["only_three_fixed_nonrandom_surviving_assets", "current_adjusted_not_point_in_time_archive",
                       "historical_news_regime_quote_spread_and_SMTP_missing", "native_plan_candidate_gate_is_not_final_mail_gate",
                       "pattern_context_is_not_a_trade", "production_history_1095_calendar_days_max800_bars"],
            "strategies": reports}
        require_unchanged_scanner_sources(source_fingerprints)
        result["scanner_source_sha256"] = source_fingerprints
        result["replay_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        output.write_text(json.dumps(result, indent=2, allow_nan=False, ensure_ascii=True)+"\n", encoding="utf-8")
        return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--strategy", action="append")
    a = p.parse_args()
    replay(a.source, a.output, a.strategy)
