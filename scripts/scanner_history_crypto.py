"""Offline causal spot study of the real Early-Mover 5m/4h technical core.

This is not a historical replay of the full perpetual scanner. No market cap,
trending rank, OI, funding, order book, native perpetual quote or SMTP evidence
is invented. Fixed hourly observations and four-hour episode spacing are
declared before outcomes; all eligible events and first three examples remain.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scanner_history_runtime import (
    isolated_application, imported_scanner_source_fingerprints,
    require_unchanged_scanner_sources,
)
_IMPORT_SOURCE_FINGERPRINTS = imported_scanner_source_fingerprints()

from scripts.scanner_history_audit import CRYPTO, WINDOW_START, WINDOW_END, fingerprint, unique_bars

HORIZONS = (1, 4, 24)
EVENT_SPACING_SECONDS = 4 * 3600
MISSING_FULL_SCANNER_INPUTS = [
    "point_in_time_CoinGecko_market_cap_universe_trending_narratives",
    "native_perpetual_contract_mapping_and_executable_bid_ask",
    "historical_funding_and_measured_settlement_interval",
    "same_venue_comparable_OI_snapshots", "historical_orderbook_depth_and_impact",
    "accepted_mail_tracker_and_personal_alert_settings",
]


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def load_spot_source(path, contract):
    with Path(path).open(encoding="utf-8") as stream:
        source = json.load(stream)
    if (not isinstance(source, dict) or source.get("symbol") != contract
            or source.get("venue") != "binance_spot" or source.get("quote_currency") != "USDT"
            or source.get("timeframe") != "5m" or not isinstance(source.get("bars"), list)):
        raise ValueError("spot_history_source_contract_invalid")
    bars = unique_bars(source["bars"])
    if fingerprint(bars) != source.get("bars_sha256"):
        raise ValueError("spot_history_fingerprint_mismatch")
    if any(row["t"] % 300000 != 0 for row in bars):
        raise ValueError("spot_history_non_5m_clock")
    clean = [{"timestamp": int(row["t"] / 1000), "open": row["o"], "high": row["h"],
              "low": row["l"], "close": row["c"], "volume": row["v"]} for row in bars]
    return clean, {"source_file": Path(path).name, "source_bars_sha256": source["bars_sha256"],
                   "source_bar_count": len(clean), "venue": "binance_spot", "quote_currency": "USDT"}


def aggregate_completed_4h(bars):
    """Only complete UTC-aligned groups of 48 measured consecutive 5m bars."""
    buckets = {}
    for row in bars:
        bucket = row["timestamp"] // 14400 * 14400
        buckets.setdefault(bucket, []).append(row)
    result = []
    for opened, group in sorted(buckets.items()):
        if len(group) != 48 or [row["timestamp"] for row in group] != list(range(opened, opened + 14400, 300)):
            continue
        result.append({"timestamp": opened, "open": group[0]["open"],
                       "high": max(row["high"] for row in group), "low": min(row["low"] for row in group),
                       "close": group[-1]["close"], "volume": sum(row["volume"] for row in group),
                       "complete": True})
    return result


def _contiguous(rows, interval):
    return bool(rows) and all(right["timestamp"] - left["timestamp"] == interval
                              for left, right in zip(rows, rows[1:]))


def _forward_outcomes(close_by_time, observed_at, price, end_exclusive):
    outcomes = {}
    for hours in HORIZONS:
        evaluated_at = observed_at + hours * 3600
        if evaluated_at >= end_exclusive:
            outcomes[str(hours)] = {"status": "unresolved_window_end"}
            continue
        measured_close = close_by_time.get(evaluated_at)
        if measured_close is None:
            outcomes[str(hours)] = {"status": "unresolved_missing_close"}
            continue
        gross_return = (measured_close / price - 1) * 100
        outcomes[str(hours)] = {"status": "resolved", "evaluated_at": _iso(evaluated_at),
                                "future_close": measured_close, "gross_directional_return_pct": gross_return,
                                "positive_gross_directional_return": gross_return > 0}
    return outcomes


def _summarize(events):
    summary = {}
    for hours in HORIZONS:
        available = [event["outcomes"][str(hours)] for event in events
                     if event["outcomes"][str(hours)]["status"] == "resolved"]
        wins = sum(item["positive_gross_directional_return"] for item in available)
        summary[str(hours)] = {
            "resolved": len(available), "unresolved": len(events) - len(available),
            "positive_gross_return_count": wins,
            "positive_gross_directional_pct": 100 * wins / len(available) if available else None,
            "mean_gross_directional_return_pct": sum(item["gross_directional_return_pct"] for item in available) / len(available) if available else None,
        }
    return summary


def study_asset(api, contract, bars, *, start=WINDOW_START, end=WINDOW_END):
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
    close_times = [row["timestamp"] + 300 for row in bars]
    close_by_time = {closed: row["close"] for closed, row in zip(close_times, bars)}
    htf = aggregate_completed_4h(bars)
    htf_closes = [row["timestamp"] + 14400 for row in htf]
    skipped, observations, matches = Counter(), 0, Counter()
    event_families = {"early_mover_execution_technical_core": [], "early_mover_armed_technical_core": []}
    last_event = {family: float("-inf") for family in event_families}
    for observed in range(start_ts, end_ts, 3600):
        observations += 1
        position = bisect_right(close_times, observed)
        prefix = bars[max(0, position - 36):position]
        if (len(prefix) < 36 or not _contiguous(prefix, 300)
                or prefix[-1]["timestamp"] + 300 != observed):
            skipped["missing_or_incomplete_5m_prefix"] += 1
            continue
        htf_position = bisect_right(htf_closes, observed)
        htf_prefix = htf[max(0, htf_position - 48):htf_position]
        if len(htf_prefix) < 8 or not _contiguous(htf_prefix, 14400):
            skipped["missing_or_incomplete_4h_prefix"] += 1
            continue
        past_price = close_by_time.get(observed - 86400)
        if past_price is None:
            skipped["missing_measured_24h_price_context"] += 1
            continue
        price = prefix[-1]["close"]
        daily_change = (price / past_price - 1) * 100
        row = {"Symbol": contract, "Price": price, "Change24h": daily_change}
        profile = api._early_mover_trigger_profile(row)
        with patch.object(api.time, "time", return_value=float(observed)):
            trigger = api._score_early_mover_trigger_bars(row, prefix, "5m", profile)
            execution_htf = api._early_mover_htf_execution_context(row, htf_prefix, "4h")
            armed_htf = api._early_mover_htf_armed_context(row, htf_prefix, "4h")
        eligible = {
            "early_mover_execution_technical_core": bool(trigger.get("ok") and execution_htf.get("ok")),
            "early_mover_armed_technical_core": bool(trigger.get("pre_breakout_ok") and armed_htf.get("armed_ok")),
        }
        for family, is_eligible in eligible.items():
            if not is_eligible:
                continue
            matches[family] += 1
            if observed - last_event[family] < EVENT_SPACING_SECONDS:
                continue
            last_event[family] = observed
            event_families[family].append({
                "contract": contract, "venue": "binance_spot", "observed_at": _iso(observed),
                "reference_close": price, "direction": "LONG", "actual_24h_change_pct": daily_change,
                "matched_5m_patterns": trigger.get("matched", []),
                "execution_score": trigger.get("execution_score"),
                "pre_breakout_score": trigger.get("pre_breakout_score"),
                "execution_htf_reason": execution_htf.get("reason"), "armed_htf_reason": armed_htf.get("reason"),
                "latest_5m_closed_at": _iso(prefix[-1]["timestamp"] + 300),
                "latest_4h_closed_at": _iso(htf_prefix[-1]["timestamp"] + 14400),
                "outcomes": _forward_outcomes(close_by_time, observed, price, end_ts),
            })
    return {
        "contract": contract, "scheduled_observations": observations, "skipped_observations": dict(skipped),
        "families": {family: {"eligible_hourly_observations": matches[family], "spaced_event_count": len(events),
                               "directional_outcomes": _summarize(events), "first_three_examples": events[:3],
                               "all_events": events} for family, events in event_families.items()},
    }


def study_directory(api, source_directory):
    source_fingerprints = dict(_IMPORT_SOURCE_FINGERPRINTS)
    require_unchanged_scanner_sources(source_fingerprints)
    api_fingerprint = source_fingerprints["api.py"]
    assets = []
    for contract in CRYPTO:
        bars, provenance = load_spot_source(Path(source_directory) / f"crypto-{contract}-5m.json", contract)
        assets.append({**study_asset(api, contract, bars), **provenance})
    require_unchanged_scanner_sources(source_fingerprints)
    return {
        "kind": "crypto_real_technical_core_spot_study", "schema_version": 1,
        "window_start_inclusive": WINDOW_START.isoformat(), "window_end_exclusive": WINDOW_END.isoformat(),
        "fixed_contracts": list(CRYPTO), "observation_grid": "hourly_UTC", "event_spacing_seconds": EVENT_SPACING_SECONDS,
        "criterion": "future_measured_spot_close_greater_than_reference_close; gross_directional_only",
        "horizons_hours": list(HORIZONS), "full_scanner_replay_available": False,
        "missing_full_scanner_inputs": MISSING_FULL_SCANNER_INPUTS,
        "new_listing_replay_available": False,
        "new_listing_missing_inputs": ["contemporaneous_listing_announcement_and_native_launch_time",
                                       "BTC_ETH_SOL_not_a_new_listing_cohort", *MISSING_FULL_SCANNER_INPUTS],
        "limits": ["not_a_full_scanner_hit_rate_or_profitable_trade_backtest", "no_historical_Smtp_acceptance_claim",
                   "fixed_nonrandom_cohort_survivorship_bias", "no_fills_fees_slippage_stop_target_path_defined",
                   "armed_events_are_watch_structures_not_trade_signals", "current_rules_replayed_not_claimed_historical_deployed_revision"],
        "api_source_sha256": api_fingerprint,
        "scanner_source_sha256": source_fingerprints,
        "no_network_or_smtp": True, "assets": assets,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    private = (ROOT / "output").resolve()
    if private not in source.parents or private not in output.parents or output.exists():
        parser.error("Use frozen source and a new output file beneath repository output/")
    import_source_fingerprints = dict(_IMPORT_SOURCE_FINGERPRINTS)
    require_unchanged_scanner_sources(import_source_fingerprints)
    with isolated_application(output.parent / ("crypto-replay-state-" + uuid4().hex[:12])) as api:
        payload = study_directory(api, source)
    require_unchanged_scanner_sources(import_source_fingerprints)
    if payload["scanner_source_sha256"] != import_source_fingerprints:
        raise ValueError("scanner_source_changed_around_application_import")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "technical_spot_study_complete", "file": str(output),
                      "full_scanner_replay_available": False, "assets": [
                          {"contract": asset["contract"], "families": {key: value["spaced_event_count"] for key, value in asset["families"].items()}}
                          for asset in payload["assets"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
