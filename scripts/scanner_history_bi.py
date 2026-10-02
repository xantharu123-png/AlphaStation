"""Offline, causal BI Long/Short spot replay of the frozen three-stock cohort.

No provider, application DB, SMTP, news or order call is made. This reproduces
the current 20-factor contract and native plan, not historical mail eligibility
or the full historical scanner universe. Prices are today's adjusted history.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scanner_history_runtime import (
    imported_scanner_source_fingerprints, require_unchanged_scanner_sources,
)
_IMPORT_SOURCE_FINGERPRINTS = imported_scanner_source_fingerprints()

from modules.backtests import _simulate_bi_plan_daily, conservative_trade_exit_index
from modules.bi_trade_plan import BI_PLAN_VERSION, build_bi_trade_plan
from modules.patterns import BI_STOCK_CONTRACT_VERSION, analyze_breakout_imminent
from modules.stock_swing_contract import session_close

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
SYMBOLS = ("AAPL", "MSFT", "NVDA")
WINDOW_START = date(2026, 7, 2)
WINDOW_END = date(2026, 10, 2)  # Exclusive: the last observable session is Oct 1.
ANALYSIS_BARS = 50
PROFILE_BARS = 90
HORIZON_BARS = 10  # Production bi_long/bi_short horizon, not tuned to outcomes.
STARTER_DELAY_SECONDS = 900


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("bi_history_unknown_numeric_value")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("bi_history_invalid_numeric_value") from None
    if not math.isfinite(result):
        raise ValueError("bi_history_nonfinite_value")
    return result


def canonical_daily_bars(rows, *, window_end=WINDOW_END):
    """Keep valid, ascending exchange sessions; never repair missing evidence."""
    if not isinstance(rows, list):
        raise ValueError("bi_history_missing_bars")
    bars, previous_time, seen_sessions = [], 0, set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("bi_history_invalid_bar")
        timestamp = _number(raw.get("t"))
        if timestamp <= 10_000_000_000 or int(timestamp) != timestamp:
            raise ValueError("bi_history_invalid_timestamp")
        try:
            observed = datetime.fromtimestamp(timestamp / 1000, UTC).astimezone(NY)
        except (ValueError, OverflowError, OSError):
            raise ValueError("bi_history_invalid_timestamp") from None
        if timestamp <= previous_time:
            raise ValueError("bi_history_nonascending_timestamp")
        previous_time = timestamp
        day = observed.date().isoformat()
        if day in seen_sessions or observed.time().replace(tzinfo=None).isoformat() != "00:00:00":
            raise ValueError("bi_history_invalid_daily_session_timestamp")
        seen_sessions.add(day)
        closed = session_close(day)
        if closed is None:
            raise ValueError("bi_history_nonexchange_session")
        if observed.date() >= window_end:
            continue  # Future OHLCV cannot affect the censored study.
        prices = {field: _number(raw.get(alias)) for field, alias in (
            ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}
        if (min(prices[key] for key in ("open", "high", "low", "close")) <= 0
                or prices["volume"] < 0
                or prices["high"] < max(prices["open"], prices["low"], prices["close"])
                or prices["low"] > min(prices["open"], prices["high"], prices["close"])):
            raise ValueError("bi_history_invalid_ohlcv")
        bars.append({**prices, "date": day, "time": day})
    return bars


def expected_sessions(start, end):
    days, current = [], start
    while current < end:
        if session_close(current.isoformat()) is not None:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def load_source(path, symbol):
    source = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(source, dict) or source.get("symbol") != symbol or source.get("venue") != "us_equity_polygon"
            or source.get("multiplier") != 1 or source.get("span") != "day"
            or source.get("adjusted") is not True):
        raise ValueError("bi_history_source_contract_invalid")
    if source.get("bars_sha256") != fingerprint(source.get("bars")):
        raise ValueError("bi_history_source_hash_mismatch")
    return canonical_daily_bars(source["bars"]), {
        "file": path.name, "bars_sha256": source["bars_sha256"],
        "downloaded_at": source.get("downloaded_at"),
        "adjustment_limits": source.get("adjustment_limits"),
    }


def _ambiguous(trade):
    status = str(trade.get("evaluation_status") or "")
    return bool(trade.get("intrabar_ambiguous") or trade.get("ambiguity_reasons")
                or status in {"ENTRY_STOP_ORDER_UNRESOLVED", "LIMIT_ENTRY_TARGET_ORDER_UNRESOLVED"})


def _summary(trades):
    filled = [trade for trade in trades if trade.get("entry_filled") is True]
    decided = [trade for trade in filled if trade.get("evaluation_status") == "DECIDED"
               and trade.get("outcome") not in {None, "UNRESOLVED", "NO_FILL"}
               and trade.get("pnl_pct") is not None]
    wins = sum(trade["pnl_pct"] > 0 for trade in decided)
    wins_upper = sum((trade.get("pnl_pct_upper", trade["pnl_pct"]) or 0) > 0 for trade in decided)
    return {
        "filled": len(filled), "decided": len(decided),
        "nofill": sum(trade.get("outcome") == "NO_FILL" for trade in trades),
        "open": sum(trade.get("outcome") == "UNRESOLVED" for trade in trades),
        "ambiguous": sum(_ambiguous(trade) for trade in trades),
        "decided_unambiguous": sum(not _ambiguous(trade) for trade in decided),
        "decided_ambiguous": sum(_ambiguous(trade) for trade in decided),
        "wins_conservative": wins, "wins_upper": wins_upper,
        "win_rate_lower_pct": round(wins / len(decided) * 100, 2) if decided else None,
        "win_rate_upper_pct": round(wins_upper / len(decided) * 100, 2) if decided else None,
        "avg_pnl_lower_pct": round(sum(trade["pnl_pct"] for trade in decided) / len(decided), 4) if decided else None,
        "denominator": "filled_decided_paths_only; nofill/open excluded; OHLC ambiguity separately counted",
    }


def replay_asset(bars, symbol, direction, *, window_start=WINDOW_START,
                 window_end=WINDOW_END, analyzer=analyze_breakout_imminent,
                 planner=build_bi_trade_plan, simulator=_simulate_bi_plan_daily):
    if direction not in {"long", "short"}:
        raise ValueError("bi_history_invalid_direction")
    # Hard censoring applies to both indicator prefixes and outcome paths.
    observed_bars = [bar for bar in bars if bar["date"] < window_end.isoformat()]
    sessions = expected_sessions(window_start, window_end)
    dates = {bar["date"] for bar in observed_bars}
    missing = [day for day in sessions if day not in dates]
    internal = expected_sessions(date.fromisoformat(observed_bars[0]["date"]), window_end) if observed_bars else sessions
    missing_history = [day for day in internal if day not in dates]
    base = {"symbol": symbol, "direction": direction, "raw_selected": 0,
            "native_plan_valid": 0, "simulated_nonoverlapping": 0,
            "overlap_excluded": 0, "analysis_sessions": 0,
            "missing_sessions": sorted(set(missing + missing_history)),
            "native_plan_rejections": {}, "max_green_count": None,
            "max_green_count_date": None, "all20_available_sessions": 0,
            "green_count_distribution": {}, "trades": [], "examples": []}
    if missing or missing_history or not observed_bars:
        return {**base, **_summary([]), "status": "unavailable_session_coverage"}
    first_index = next((index for index, bar in enumerate(observed_bars)
                        if bar["date"] >= window_start.isoformat()), len(observed_bars))
    if first_index + 1 < PROFILE_BARS:
        return {**base, **_summary([]), "status": "insufficient_90_session_warmup"}
    date_to_index = {bar["date"]: index for index, bar in enumerate(observed_bars)}
    blocked_until = -1
    green_counts, rejections, diagnostics, selected_examples = Counter(), Counter(), [], []
    for index in range(first_index, len(observed_bars)):
        bar = observed_bars[index]
        closed = session_close(bar["date"])
        as_of = closed + timedelta(seconds=STARTER_DELAY_SECONDS)
        # Prefix includes the signal session, known only at close + delay.
        prefix = observed_bars[:index + 1]
        result = analyzer(prefix[-ANALYSIS_BARS:], direction=direction)
        base["analysis_sessions"] += 1
        green = getattr(result, "green_count", None)
        available = getattr(result, "available_count", 0)
        if green is not None:
            green_counts[str(green)] += 1
            if base["max_green_count"] is None or green > base["max_green_count"]:
                base["max_green_count"], base["max_green_count_date"] = green, bar["date"]
        base["all20_available_sessions"] += available == 20
        observation = {"signal_session": bar["date"], "decision_as_of": as_of.isoformat(),
                       "green_count": green, "available_count": available,
                       "weighted_score": result[1], "grade": result[5],
                       "hard_gate_failures": list(getattr(result, "hard_gate_failures", ())),
                       "raw_selected": False}
        if len(diagnostics) < 3:
            diagnostics.append(observation)
        contract_ok = (bool(result[0]) and getattr(result, "indicator_contract_ok", False)
                       and available == 20 and green is not None and green >= 17
                       and not getattr(result, "hard_gate_failures", ()))
        if not contract_ok:
            continue
        base["raw_selected"] += 1
        observation["raw_selected"] = True
        plan = planner(prefix[-PROFILE_BARS:], direction=direction,
                       range_days=getattr(result, "consolidation_days", None),
                       live_price=bar["close"], as_of=as_of)
        observation["native_plan_valid"] = bool(plan.get("accepted"))
        observation["plan_rejection"] = plan.get("reason")
        if len(selected_examples) < 3:
            selected_examples.append(observation)
        if not plan.get("accepted"):
            rejections[str(plan.get("reason") or "unknown")] += 1
            continue
        base["native_plan_valid"] += 1
        if index <= blocked_until:
            base["overlap_excluded"] += 1
            observation["overlap_excluded"] = True
            continue
        trade = simulator(observed_bars, index + 1, plan, direction, horizon_bars=HORIZON_BARS)
        trade = {**trade, "symbol": symbol, "direction": direction,
                 "signal_session": bar["date"], "decision_as_of": as_of.isoformat(),
                 "green_count": green, "available_count": available,
                 "grade": result[5], "weighted_score": result[1],
                 "plan": plan, "censor_session_exclusive": window_end.isoformat()}
        base["trades"].append(trade)
        base["simulated_nonoverlapping"] += 1
        observation["trade_outcome"] = trade.get("outcome")
        observation["evaluation_status"] = trade.get("evaluation_status")
        blocked_until = conservative_trade_exit_index(trade, date_to_index, index)
    base.update(native_plan_rejections=dict(sorted(rejections.items())),
                green_count_distribution=dict(sorted(green_counts.items(), key=lambda item: int(item[0]))),
                examples=selected_examples or diagnostics,
                example_selection="first_three_raw_selected_chronologically" if selected_examples
                else "first_three_chronological_analyses_no_qualified_setup",
                **_summary(base["trades"]))
    return {**base, "status": "computed_small_fixed_cohort"}


def build_report(source_directory):
    source_fingerprints = dict(_IMPORT_SOURCE_FINGERPRINTS)
    require_unchanged_scanner_sources(source_fingerprints)
    manifest = json.loads((source_directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("stock_symbols") != list(SYMBOLS)
            or manifest.get("window") != {"start_inclusive": "2026-07-02T00:00:00Z",
                                          "end_exclusive": "2026-10-02T00:00:00Z"}):
        raise ValueError("bi_history_frozen_manifest_mismatch")
    assets, provenance = [], []
    for symbol in SYMBOLS:
        try:
            bars, source = load_source(source_directory / f"stock-{symbol}-1day.json", symbol)
            provenance.append(source)
        except (ValueError, OSError, KeyError, TypeError) as error:
            # Fixed cohort rows remain visible even when a source is unavailable.
            for direction in ("long", "short"):
                assets.append({"symbol": symbol, "direction": direction, "status": "source_unavailable",
                               "error_type": type(error).__name__, "raw_selected": None,
                               "native_plan_valid": None, **_summary([])})
            continue
        for direction in ("long", "short"):
            assets.append(replay_asset(bars, symbol, direction))
    require_unchanged_scanner_sources(source_fingerprints)
    return {
        "kind": "bi_history_causal_spot_replay", "schema_version": 1,
        "scanner_source_sha256": source_fingerprints,
        "window": manifest["window"], "cohort": list(SYMBOLS),
        "manifest_sha256": fingerprint(manifest), "sources": provenance,
        "indicator_contract_version": BI_STOCK_CONTRACT_VERSION, "native_plan_version": BI_PLAN_VERSION,
        "method": {"indicator_completed_prefix_bars": ANALYSIS_BARS,
                   "structure_completed_prefix_bars": PROFILE_BARS,
                   "decision_clock": "exchange_session_close_plus_900_seconds",
                   "execution_start": "next_exchange_session; never the signal candle",
                   "entry_wait_sessions": HORIZON_BARS, "holding_sessions_after_fill": HORIZON_BARS,
                   "fees_roundtrip_pct": .2, "adverse_entry_slippage_fraction": .001,
                   "adverse_exit_slippage_fraction": .001,
                   "passive_limit_entry_slippage": "none; cannot execute below sell limit",
                   "position_model": "50/50 TP1/TP2; breakeven after TP1",
                   "ohlc_ambiguity": "conservative and favorable bounds; never assume intrabar order",
                   "dedup": "per asset+direction pending/open occupation; later lower/upper exit"},
        "limits": ["three fixed nonrandom surviving assets; not global scanner hit rate",
                   "current adjusted prices; point-in-time revisions unavailable",
                   "current indicator/native plan version replay, not historical deployed versions",
                   "no historical news/calendar/SMTP/quote/universe-gate parity",
                   "daily execution model not proof of executable quotes or real fills",
                   "open outcomes censored at 2026-10-02 exclusive, not losses or wins"],
        "assets": assets, "no_mail": True, "no_orders": True, "offline": True,
    }


def render_markdown(report):
    lines = ["# BI Long/Short: kausale historische Stichprobe", "",
             "02.07.–01.10.2026; AAPL, MSFT, NVDA, vor der Auswertung festgelegt.",
             "Keine globale Trefferquote. Aktueller 17/20-Vertrag und echter nativer Plan;",
             "50 abgeschlossene Analyse-/90 Strukturkerzen. Entscheidung Schluss + 15 Minuten,",
             "Ausfuehrung erst Folgesession. 0,2 % Rundlaufgebuehr + 0,1 % adverse Slippage.", "",
             "| Asset | Richtung | Geprueft | 17/20 | Plan gueltig | Duplikate | Fill | Entschieden | Kein Fill | Offen | Ambig | Quote konservativ |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for item in report["assets"]:
        rate = item.get("win_rate_lower_pct")
        lines.append("| " + " | ".join(str(value) for value in (
            item["symbol"], item["direction"], item.get("analysis_sessions", "—"),
            item.get("raw_selected"), item.get("native_plan_valid"), item.get("overlap_excluded", "—"),
            item["filled"], item["decided"], item["nofill"], item["open"], item["ambiguous"],
            f"{rate:.2f}%" if rate is not None else "nicht berechenbar")) + " |")
    lines.extend(["", "Nenner: nur gefuellte, entschiedene Pfade. Offene/ungefuellte Faelle zaehlen nicht als Verlust.",
                  "Ambige OHLC-Pfade sind separat erfasst; die JSON-Datei enthaelt obere/untere Grenzen.", "",
                  "## Erste chronologische Beispiele", ""])
    for item in report["assets"]:
        lines.append(f"### {item['symbol']} {item['direction']}")
        lines.append("")
        lines.append(f"Status: {item['status']}; maximale Konfluenz {item.get('max_green_count')}/20.")
        lines.append("")
        for example in item.get("examples", []):
            lines.append(f"- {example['signal_session']}: {example['green_count']}/20, "
                         f"{example['available_count']}/20 berechenbar; "
                         f"{'gewaehlt' if example['raw_selected'] else 'kein 17/20-Setup'}; "
                         f"Plan: {example.get('plan_rejection') or example.get('native_plan_valid', 'nicht erstellt')}.")
        lines.append("")
    lines.extend(["## Grenzen", "", *[f"- {limit}" for limit in report["limits"]], ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh JSON file under output/; sibling .md is written")
    args = parser.parse_args()
    output = args.output.resolve()
    if (ROOT / "output").resolve() not in output.parents or output.suffix != ".json":
        parser.error("Output must be a fresh .json file below repository output/")
    markdown = output.with_suffix(".md")
    if output.exists() or markdown.exists():
        parser.error("Output already exists; use a new filename")
    report = build_report(args.source.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(output), "asset_direction_rows": len(report["assets"]),
                      "raw_selected": sum(item.get("raw_selected") or 0 for item in report["assets"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
