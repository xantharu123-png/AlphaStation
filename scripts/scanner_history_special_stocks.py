"""Offline technical-stage studies of ORB, Bear and Volume Spikes.

Actual production helpers/wrappers see only frozen, completed observations.
Directional reference movements are not fills, profit, or historical mails.
Run in a fresh subprocess; the application is imported in disposable state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.scanner_history_runtime import (
    isolated_application, imported_scanner_source_fingerprints,
    require_unchanged_scanner_sources,
)
_IMPORT_SOURCE_FINGERPRINTS = imported_scanner_source_fingerprints()

from scripts.scanner_history_audit import STOCKS, WINDOW_START, WINDOW_END, fingerprint
from scripts.scanner_history_bi import expected_sessions
from scripts.scanner_history_stock import load_source, directional_markouts, summarize_markouts

UTC = timezone.utc


class Reply:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return deepcopy(self.payload)


def orb_technical_event(api, raw_bars, prior_day, *, daily_prefix, as_of, symbol):
    """The actual ORB prefilter, range and active-excursion volume stages.

    Snapshot fields are reconstructed solely from completed RTH 5m aggregates.
    This is intentionally NOT final ORB score/plan/live-quote/mail qualification.
    """
    from modules import stock_swing_contract as swing
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or symbol not in STOCKS:
        raise ValueError("orb_replay_clock_or_asset_invalid")
    clock = as_of.astimezone(swing.NY)
    watermark = clock - timedelta(seconds=900)
    opening = clock.replace(hour=9, minute=30, second=0, microsecond=0)
    market_open_ms = int(opening.timestamp()*1000)
    complete, or_bars = api._orb_completed_regular_bars(
        raw_bars, market_open_ms=market_open_ms, as_of=watermark)
    if not complete or len(or_bars) != 3:
        return None, "opening_range_incomplete"
    # Restrict even if the caller accidentally supplied other sessions.
    complete = [b for b in complete if datetime.fromtimestamp(b["t"]/1000, UTC).astimezone(swing.NY).date() == clock.date()
                and b["t"] < int(swing.session_close(clock.date().isoformat()).timestamp()*1000)]
    post_or = [b for b in complete if b["t"] >= market_open_ms+900_000]
    if not post_or:
        return None, "no_completed_post_opening_range_bar"
    previous_close, previous_volume = prior_day["c"], prior_day["v"]
    if not (5 <= previous_close <= 2000) or previous_volume < 500_000:
        return None, "orb_previous_day_prefilter"
    session_volume = sum(b["v"] for b in complete)
    # The watermark owns both price-volume observation and its EVF projection.
    evf = api._us_equity_expected_volume_fraction(watermark)
    rvol = session_volume/(previous_volume*evf) if previous_volume*evf > 0 else 0
    gap_pct = (complete[0]["o"]/previous_close-1)*100
    if abs(gap_pct) < 1.5 and rvol < 1.3:
        return None, "orb_gap_and_rvol_prefilter"
    high, low = max(b["h"] for b in or_bars), min(b["l"] for b in or_bars)
    size, size_pct = high-low, (high/low-1)*100
    # Actual daily ATR helper, with I/O replaced by the known daily prefix.
    fallback = (prior_day["h"]-prior_day["l"])/previous_close*100
    history_start = (watermark-timedelta(days=35)).date().isoformat()
    history_end = (watermark-timedelta(days=1)).date().isoformat()
    atr_prefix = [b for b in daily_prefix if history_start <= datetime.fromtimestamp(b["t"]/1000, UTC).astimezone(swing.NY).date().isoformat() <= history_end]
    with patch.object(api, "rate_limited_get", lambda *a, **k: Reply({"status": "OK", "adjusted": True,
            "results": deepcopy(atr_prefix)})):
        atr_pct, atr_model = api._fetch_orb_atr_pct(symbol, watermark, fallback)
    if size_pct < .3 or (previous_close*atr_pct/100 > 0 and size > previous_close*atr_pct/100*2):
        return None, "orb_range_size_filter"
    price = post_or[-1]["c"]
    direction = "LONG" if price > high else "SHORT" if price < low else None
    if direction is None:
        return None, "current_completed_close_inside_or"
    volume = api._orb_active_excursion_volume(or_bars, post_or, direction, high, low, rvol)
    if volume["confirmed"] is not True:
        return None, "active_excursion_volume_unconfirmed"
    points, _ = api._orb_volume_score(True, volume["launch_volume"], volume["baseline_volume"], volume["baseline_volume"] > 0)
    return {"direction": direction, "reference_price": price, "reference_bar_timestamp": post_or[-1]["t"],
            "or_high": high, "or_low": low, "or_size_pct": size_pct, "gap_pct": gap_pct,
            "projected_rvol": rvol, "atr_pct": atr_pct, "atr_model": atr_model,
            "active_excursion_volume": volume, "production_volume_points": points,
            "stage": "orb_prefilter_range_active_excursion_volume_only",
            "native_trade_plan_checked": False, "final_mail_gate_checked": False}, None


def intraday_directional_markouts(bars, signal_index, direction, reference_price):
    """Future session closes relative to the causal 5m observation, never fills."""
    sign = 1 if direction == "LONG" else -1
    result = {"kind": "directional_5m_reference_close_diagnostic", "direction_known": True,
              "reference_close": reference_price, "trade_profit_equivalent": False}
    for horizon in (1, 5, 10):
        index = signal_index+horizon
        result[str(horizon)] = (None if index >= len(bars) else {"session": bars[index]["date"],
            "signed_close_change_pct": sign*(bars[index]["close"]/reference_price-1)*100})
    return result


def run_daily_observation_wrappers(api, snapshots, daily_prefixes, *, as_of):
    """Replay the actual Volume-Spikes and Bear producers' discovery stages."""
    captured = {}

    def provider(url, **kwargs):
        if "/aggs/ticker/" in url:
            symbol = url.split("/ticker/")[1].split("/")[0]
            if symbol not in daily_prefixes:
                raise RuntimeError("unfrozen_market_source_requested")
            limit = int(kwargs.get("params", {}).get("limit", 60))
            rows = list(reversed(daily_prefixes[symbol]))[:limit]
            return Reply({"status": "OK", "adjusted": True, "results": rows})
        if "/snapshot/locale/us/markets/stocks/" in url:
            return Reply({"status": "OK", "tickers": snapshots})
        raise RuntimeError("unfrozen_market_source_requested")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of.astimezone(tz) if tz else as_of.replace(tzinfo=None)

    with ExitStack() as stack:
        stack.enter_context(patch.object(api, "datetime", Clock))
        stack.enter_context(patch.object(api, "rate_limited_get", provider))
        stack.enter_context(patch.object(api, "save_cache_file", lambda path, rows: captured.update({path: deepcopy(rows)})))
        stack.enter_context(patch.object(api, "_load_common_stock_universe", lambda **kw: (set(STOCKS), "frozen_known_common_stock_cohort")))
        stack.enter_context(patch.object(api, "_attach_stock_company_name", lambda row, **kw: row))
        stack.enter_context(patch.object(api, "_scan_control_point", lambda **kw: None))
        stack.enter_context(patch.object(api, "INVERSE_ETFS", {}))
        stack.enter_context(patch.object(api, "get_current_trading_session", lambda: ("Closed", None)))
        stack.enter_context(patch.object(api, "_fetch_bear_latest_intraday_state", lambda *a, **kw: {}))
        # No reconstructed quotes/news. Never qualify or attempt a historical mail.
        blocked = lambda *a, **kw: {"alertable_now": False, "grade": None,
                                   "suppression_reasons": ["historical_execution_inputs_missing"]}
        stack.enter_context(patch.object(api, "_classify_alert_candidate", blocked))
        stack.enter_context(patch.object(api, "_classify_crash_alert_candidate", blocked))
        stack.enter_context(patch.object(api, "_record_email_event", lambda *a, **kw: None))
        stack.enter_context(patch.object(api, "_record_suppression_counts", lambda *a, **kw: None))
        with redirect_stdout(io.StringIO()):
            api._volume_spikes_wrapper()
            api._bear_scan_wrapper()
    spikes = captured.get(api.VOLUME_SPIKES_CACHE, [])
    bears = captured.get("/tmp/bear_scanner_cache.json", [{}])[0].get("breakdown_stocks", [])
    return spikes, bears


def replay(directory, output):
    source_fingerprints = dict(_IMPORT_SOURCE_FINGERPRINTS)
    require_unchanged_scanner_sources(source_fingerprints)
    directory, output = directory.resolve(), output.resolve()
    if (ROOT/"output").resolve() not in output.parents or output.exists():
        raise ValueError("use_new_private_report_path")
    protocol = json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    if (protocol.get("stock_symbols") != list(STOCKS) or protocol.get("window") != {
            "start_inclusive": "2026-07-02T00:00:00Z", "end_exclusive": "2026-10-02T00:00:00Z"}):
        raise ValueError("frozen_cohort_mismatch")
    sources = {s: load_source(directory, s, 1, "day") for s in STOCKS}
    intraday = {s: load_source(directory, s, 5, "minute") for s in STOCKS}
    dates = expected_sessions(WINDOW_START.date(), WINDOW_END.date())
    output.parent.mkdir(parents=True, exist_ok=True)
    with isolated_application(output.parent/(output.stem+"-isolated-state")) as api:
        from modules import stock_swing_contract as swing
        from modules.stock_bars import completed_polygon_bars
        histories = {s: completed_polygon_bars(src["bars"], as_of=WINDOW_END) for s, src in sources.items()}
        daily = {s: {b["date"]: b for b in histories[s]} for s in STOCKS}
        raw_daily = {s: {datetime.fromtimestamp(b["t"]/1000, UTC).astimezone(swing.NY).date().isoformat(): b
                        for b in sources[s]["bars"]} for s in STOCKS}
        if any(any(d not in daily[s] for d in dates) for s in STOCKS):
            raise ValueError("frozen_daily_session_coverage_missing")
        intraday_days = {s: {} for s in STOCKS}
        for s in STOCKS:
            for bar in intraday[s]["bars"]:
                day = datetime.fromtimestamp(bar["t"]/1000, UTC).astimezone(swing.NY).date().isoformat()
                intraday_days[s].setdefault(day, []).append(bar)
        reports = {name: {"observations": [], "exclusions": Counter(), "asset_sessions": {s: 0 for s in STOCKS}}
                   for name in ("ORB Long technical stage", "ORB Short technical stage", "Volume Spikes completed-session discovery", "Bear completed-session discovery")}
        for day in dates:
            close = swing.session_close(day)
            as_of = close.astimezone(UTC)+timedelta(seconds=900)
            previous = swing.completed_sessions(as_of, 2)[1]
            if any(previous not in raw_daily[s] for s in STOCKS):
                raise ValueError("frozen_previous_daily_session_missing")
            prefixes = {s: [b for b in sources[s]["bars"] if b["t"] <= raw_daily[s][day]["t"]] for s in STOCKS}
            snapshots = [{"ticker": s, "day": dict(raw_daily[s][day]), "prevDay": dict(raw_daily[s][previous])} for s in STOCKS]
            spikes, bears = run_daily_observation_wrappers(api, snapshots, prefixes, as_of=as_of)
            for name, rows in (("Volume Spikes completed-session discovery", spikes), ("Bear completed-session discovery", bears)):
                report = reports[name]
                for s in STOCKS:
                    report["asset_sessions"][s] += 1
                for row in rows:
                    s = row["ticker"]
                    direction = "SHORT" if name.startswith("Bear") or row.get("signal_type") == "BREAKDOWN" else "LONG" if row.get("signal_type") == "BREAKOUT" else None
                    report["observations"].append({"asset": s, "session": day, "available_at": as_of.isoformat(),
                        "direction": direction, "production_row": row, "stage": "actual_producer_discovery_only",
                        "directional_markouts": directional_markouts(histories[s], next(i for i,b in enumerate(histories[s]) if b["date"]==day), direction)})
            for s in STOCKS:
                raw = intraday_days[s].get(day, [])
                opening = close.replace(hour=9, minute=30)
                # A fixed prime-session 5m poll grid, not hindsight's best candle.
                # Starter observation delay: 09:50..10:45 close available 10:05..11:00.
                found = set()
                for name in ("ORB Long technical stage", "ORB Short technical stage"):
                    reports[name]["asset_sessions"][s] += 1
                for minutes in range(20, 76, 5):
                    cutoff = opening+timedelta(minutes=minutes)
                    available = cutoff.astimezone(UTC)+timedelta(seconds=900)
                    event, reason = orb_technical_event(api, raw, raw_daily[s][previous], daily_prefix=prefixes[s][:-1], as_of=available, symbol=s)
                    if event is None:
                        for name in ("ORB Long technical stage", "ORB Short technical stage"):
                            reports[name]["exclusions"][reason] += 1
                        continue
                    direction = event["direction"]
                    if direction in found:
                        continue
                    found.add(direction)
                    name = "ORB Long technical stage" if direction == "LONG" else "ORB Short technical stage"
                    reports[name]["observations"].append({"asset": s, "session": day, "available_at": available.isoformat(),
                        **event, "directional_markouts": intraday_directional_markouts(histories[s], next(i for i,b in enumerate(histories[s]) if b["date"]==day), direction, event["reference_price"]),
                        "markout_reference": "causal_completed_5m_close_not_intraday_fill"})
            print(json.dumps({"special_stock_session": day}), flush=True)
        for name, report in reports.items():
            report.update(selected=len(report["observations"]), exclusions=dict(report["exclusions"]),
                chronological_examples=report["observations"][:3], directional_diagnostic=summarize_markouts(report["observations"]),
                qualified_trade_win_rate=None, historical_mail_delivery_verified=False)
        result = {"kind": "frozen_three_stock_special_technical_study", "schema_version": 1,
            "window_start": WINDOW_START.isoformat(), "window_end_exclusive": WINDOW_END.isoformat(),
            "manifest_sha256": fingerprint(protocol), "assets": list(STOCKS), "sessions": dates,
            "input_sha256": {s: {"1D": sources[s]["bars_sha256"], "5m": intraday[s]["bars_sha256"]} for s in STOCKS},
            "reports": reports, "unavailable": {"Crash Monitor": ["historical_VIX_index", "SPY_QQQ_DIA_IWM", "full_historical_common_stock_market_breadth"],
                "Bear inverse ETF": ["inverse_ETF_history_outside_frozen_cohort"]},
            "limits": ["three_fixed_nonrandom_surviving_assets_not_historical_universe", "current_adjusted_not_point_in_time_archive",
                "discovery_and_ORB_technical_stage_not_final_signal_or_execution", "overlapping_directional_observations_not_independent",
                "reference_close_1_5_10_session_movements_are_not_trade_PnL", "right_censored_at_2026_10_01_close",
                "no_historical_news_quotes_orderbooks_recipient_prefs_or_SMTP"]}
        require_unchanged_scanner_sources(source_fingerprints)
        result["scanner_source_sha256"] = source_fingerprints
        output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    replay(args.source, args.output)
