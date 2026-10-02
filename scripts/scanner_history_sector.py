"""Offline Biotech technical and Penny daily-level spot studies, not trade rates.

The complete sector scanners require historical event/float/quote information
that is not in the frozen OHLCV collection. Unknown full-scanner results remain
unknown; technical scores and daily levels are measured independently.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scanner_history_runtime import (
    imported_scanner_source_fingerprints, require_unchanged_scanner_sources,
)
_IMPORT_SOURCE_FINGERPRINTS = imported_scanner_source_fingerprints()

from modules import scanners
from modules.penny_stock_scanner import parse_penny_daily_aggregates, _daily_resistance_levels
from scripts.scanner_history_bi import (WINDOW_START, WINDOW_END, fingerprint,
                                       expected_sessions, load_source, session_close)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
BIOTECH = ("AMGN", "GILD", "REGN")
PENNY = ("SIRI", "OPEN", "BBAI")


def raw_daily(bar):
    opened = datetime.fromisoformat(bar["date"]).replace(tzinfo=NY)
    return {"t": int(opened.timestamp() * 1000), **{
        alias: bar[field] for field, alias in (
            ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}}


def _checked_sessions(bars, window_start, window_end):
    observed = [bar for bar in bars if bar["date"] < window_end.isoformat()]
    if not observed:
        raise ValueError("sector_history_no_data")
    missing = set(expected_sessions(date.fromisoformat(observed[0]["date"]), window_end)) - {
        bar["date"] for bar in observed}
    if missing:
        raise ValueError("sector_history_missing_session")
    indexed = {bar["date"]: index for index, bar in enumerate(observed)}
    return observed, [(day, indexed[day]) for day in expected_sessions(window_start, window_end)]


def replay_biotech_technical(bars, symbol, *, window_start=WINDOW_START, window_end=WINDOW_END):
    observed, sessions = _checked_sessions(bars, window_start, window_end)
    records = []
    for day, index in sessions:
        clock = (session_close(day) + timedelta(seconds=900)).astimezone(UTC)
        # Emulate the actual 90-calendar-day query, not an invented 90-bar score.
        start_day = (clock.astimezone(NY) - timedelta(days=90)).date().isoformat()
        raw = [raw_daily(bar) for bar in observed[:index + 1] if bar["date"] >= start_day]
        payload = {"status": "OK", "adjusted": True, "results": raw,
                   "resultsCount": len(raw), "queryCount": len(raw)}
        class Reply:
            status_code = 200
            def json(self):
                return payload
        def historical_daily_only(url, **kwargs):
            if not url.endswith(f"/range/1/day/{start_day}/{day}") or f"/ticker/{symbol}/" not in url:
                raise AssertionError("unexpected_sector_technical_request")
            return Reply()
        with patch.dict(os.environ, {"STOCK_SWING_DATA_MODE": "starter_swing"}), \
                patch.object(scanners, "rate_limited_get", historical_daily_only):
            result = scanners._biotech_technical_score("offline-frozen-history", symbol, as_of=clock)
        records.append({"session": day, "decision_as_of": clock.isoformat(), "query_start_day": start_day,
                        "query_completed_bars": len(raw), **result})
    histogram = Counter(str(record["technical_score"]) for record in records)
    return {"symbol": symbol, "scope": "actual_biotech_daily_technical_adapter_only",
            "analysis_sessions": len(records), "technical_score_histogram": dict(sorted(histogram.items(), key=lambda pair: int(pair[0]))),
            "mean_technical_score": sum(record["technical_score"] for record in records) / len(records) if records else None,
            "max_technical_score": max((record["technical_score"] for record in records), default=None),
            "records": records, "chronological_examples": records[:3],
            "full_scanner_candidates": None, "win_rate_pct": None,
            "full_scanner_status": "not_calculable_missing_point_in_time_news_calendar_float",
            "missing_inputs": ["dated news/descriptions with publication clocks", "point-in-time catalyst calendar",
                               "historical shares/market cap/float", "historical application mail/quote gates"],
            "event_score_invented": False, "no_trade_claim": True}


def replay_penny_daily_levels(bars, symbol, *, window_start=WINDOW_START, window_end=WINDOW_END):
    observed, sessions = _checked_sessions(bars, window_start, window_end)
    records = []
    for day, index in sessions:
        clock = (session_close(day) + timedelta(seconds=900)).astimezone(UTC)
        start_day = (clock - timedelta(days=240)).date().isoformat()
        raw = [raw_daily(bar) for bar in observed[:index + 1] if bar["date"] >= start_day]
        canonical = parse_penny_daily_aggregates({"status": "OK", "adjusted": True,
            "results": raw, "resultsCount": len(raw), "queryCount": len(raw)}, as_of=clock)
        reference = observed[index]["close"]
        levels = _daily_resistance_levels(canonical, reference, now_ts=clock.timestamp())
        records.append({"session": day, "decision_as_of": clock.isoformat(), "reference_close": reference,
                        "price_only_range_0_2_to_5": .2 <= reference <= 5,
                        "daily_level_observations": levels})
    return {"symbol": symbol, "scope": "penny_price_only_daily_levels_not_execution_scanner",
            "analysis_sessions": len(records),
            "price_only_range_sessions": sum(record["price_only_range_0_2_to_5"] for record in records),
            "sessions_with_overhead_daily_level": sum(bool(record["daily_level_observations"]) for record in records),
            "records": records, "chronological_examples": records[:3],
            "full_scanner_candidates": None, "win_rate_pct": None,
            "full_scanner_status": "not_calculable_missing_intraday_quote_float_event_inputs",
            "missing_inputs": ["completed contemporaneous 5-minute RTH bars", "timestamped RTH quote/spread",
                               "historical float/SEC/market cap", "historical event/capital-entry state"],
            "event_score_invented": False, "no_trade_claim": True}


def build_report(directory):
    source_fingerprints = dict(_IMPORT_SOURCE_FINGERPRINTS)
    require_unchanged_scanner_sources(source_fingerprints)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("biotech_symbols") != list(BIOTECH) or manifest.get("penny_symbols") != list(PENNY)
            or manifest.get("window") != {"start_inclusive": "2026-07-02T00:00:00Z", "end_exclusive": "2026-10-02T00:00:00Z"}):
        raise ValueError("sector_frozen_manifest_mismatch")
    report = {"kind": "sector_partial_inputs_history_spot_study", "schema_version": 1,
              "manifest_sha256": fingerprint(manifest), "window": manifest["window"],
              "biotech": [], "penny": [], "sources": [], "offline": True,
              "no_mail": True, "no_orders": True, "global_scanner_win_rate": None,
              "limits": ["fixed nonrandom surviving three-asset cohorts per sector",
                         "current adjusted OHLCV, not archived point-in-time revisions",
                         "technical score / daily resistance is not a complete scanner signal",
                         "missing news/calendar/float/5m/quote inputs cannot be replaced by zero"]}
    for family, symbols, function in (("biotech", BIOTECH, replay_biotech_technical),
                                     ("penny", PENNY, replay_penny_daily_levels)):
        for symbol in symbols:
            try:
                bars, source = load_source(directory / f"stock-{symbol}-1day.json", symbol)
                report["sources"].append(source)
                report[family].append(function(bars, symbol))
            except (ValueError, OSError, KeyError, scanners.ScannerDataError) as error:
                report[family].append({"symbol": symbol, "status": "source_or_technical_unavailable",
                    "error_type": type(error).__name__, "full_scanner_candidates": None, "win_rate_pct": None})
    require_unchanged_scanner_sources(source_fingerprints)
    report["scanner_source_sha256"] = source_fingerprints
    return report


def render_markdown(report):
    lines = ["# Biotech / Penny: historische Teilpruefung", "",
             "02.07.–01.10.2026, je drei vorab festgelegte Assets. Kein kompletter Scanner-Backtest.", "",
             "## Biotech: echter technischer Tagesadapter", "",
             "| Asset | Sessions | Durchschnitt Technikscore | Maximum | Vollstaendige Trefferquote |",
             "|---|---:|---:|---:|---|"]
    for item in report["biotech"]:
        mean = item.get("mean_technical_score")
        lines.append(f"| {item['symbol']} | {item.get('analysis_sessions', '—')} | "
                     f"{f'{mean:.2f}' if mean is not None else '—'} | {item.get('max_technical_score', '—')} | nicht berechenbar |")
    lines.extend(["", "News, Kalender, Float und damalige Ausfuehrung fehlen; kein Ereignisscore wurde erfunden.",
                  "Technik nutzt abgeschlossene Kerzen, denselben 90-Kalendertage-Query und Schluss + 15 Minuten.",
                  "", "## Penny: echte Tageslevels, fehlende Ausfuehrungsdaten", "",
                  "| Asset | Sessions | Preis allein 0,20–5 USD | Tageswiderstand vorhanden | Vollstaendige Trefferquote |",
                  "|---|---:|---:|---:|---|"])
    for item in report["penny"]:
        lines.append(f"| {item['symbol']} | {item.get('analysis_sessions', '—')} | {item.get('price_only_range_sessions', '—')} | "
                     f"{item.get('sessions_with_overhead_daily_level', '—')} | nicht berechenbar |")
    lines.extend(["", "Tagespreis und Widerstand beweisen keinen Penny-Trigger. 5m-RTH-Bars, Quotes/Spread, Float/SEC und Ereigniszustand fehlen.",
                  "Alle Quellen, Rohberechnungen und ersten drei chronologischen Beispiele stehen in der JSON-Datei.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if (ROOT / "output").resolve() not in output.parents or output.suffix != ".json" or output.exists() or output.with_suffix(".md").exists():
        parser.error("Use a fresh private .json path under repository output/")
    report = build_report(args.source.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(output), "biotech_assets": len(report["biotech"]), "penny_assets": len(report["penny"])}))


if __name__ == "__main__":
    main()
