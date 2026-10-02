"""Bounded, read-only market collection and causal scanner spot studies.

The cohort is frozen before the first market request. This is not a full
historical universe, a broker PnL reconstruction, or permission to send mail.
Source observations and private tracker evidence stay below output/.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UTC = timezone.utc
WINDOW_START = datetime(2026, 7, 2, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 2, tzinfo=UTC)
STOCKS = ("AAPL", "MSFT", "NVDA")
BIOTECH = ("AMGN", "GILD", "REGN")
PENNIES = ("SIRI", "OPEN", "BBAI")
CRYPTO = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
WARMUP_START = "2025-10-01"


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean_market_value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite_market_value")
    return number


def validate_ohlcv(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid_market_record")
    values = {key: _number(raw[key]) for key in ("t", "o", "h", "l", "c", "v")}
    if values["t"] < 10_000_000_000 or values["t"] != int(values["t"]):
        raise ValueError("invalid_market_millisecond_clock")
    if (min(values[key] for key in ("t", "o", "h", "l", "c")) <= 0
            or values["v"] < 0 or values["h"] < max(values["o"], values["l"], values["c"])
            or values["l"] > min(values["o"], values["h"], values["c"])):
        raise ValueError("invalid_market_geometry")
    return values


def unique_bars(rows):
    by_time = {}
    for row in rows:
        clean = validate_ohlcv(row)
        old = by_time.get(clean["t"])
        if old is not None and old != clean:
            raise ValueError("conflicting_market_duplicate")
        by_time[clean["t"]] = clean
    return [by_time[key] for key in sorted(by_time)]


def manifest():
    return {
        "kind": "scanner_history_spot_manifest", "schema_version": 1,
        "frozen_at": iso(datetime.now(UTC)),
        "window": {"start_inclusive": iso(WINDOW_START), "end_exclusive": iso(WINDOW_END)},
        "stock_symbols": list(STOCKS), "biotech_symbols": list(BIOTECH),
        "penny_symbols": list(PENNIES), "crypto_contracts": list(CRYPTO),
        "stock_warmup_start": WARMUP_START,
        "selection_policy": "fixed_first_three_existing_stock_backtest_symbols; fixed_known_sector/price/crypto_spot_examples_before_outcomes",
        "universe_limits": ["nonrandom_small_sample", "current_known_symbols_survivorship_bias",
                            "not_the_full_historical_scanner_universe"],
        "crypto_venue": "binance_spot", "quote_currency": "USDT",
        "crypto_limits": ["spot_is_not_perpetual_contract_execution", "historical_funding_OI_orderbook_missing",
                          "BTC_ETH_SOL_are_not_new_listings"],
        "no_orders": True, "no_mail": True, "no_application_state_changes": True,
    }


def _save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def _request_json(session, url, params):
    # Only the two documented public market-data hosts are in scope. Do not
    # print URLs/errors containing credentials; no response bodies are logged.
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.polygon.io", "data-api.binance.vision"}:
        raise ValueError("market_host_not_allowed")
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=(5, 25), allow_redirects=False)
        except Exception:
            if attempt == 2:
                raise ValueError("market_transport_unavailable") from None
            time.sleep(2)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 2:
                raise ValueError("market_rate_or_server_unavailable")
            time.sleep(5)
            continue
        if response.status_code != 200:
            raise ValueError(f"market_http_{response.status_code}")
        try:
            return response.json()
        except Exception:
            raise ValueError("market_json_invalid") from None
    raise ValueError("market_unavailable")


def collect_polygon(session, symbol, multiplier, span, start, api_key):
    end = (WINDOW_END - timedelta(seconds=1)).date().isoformat()
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{multiplier}/{span}/{start}/{end}"
    params = {"apiKey": api_key, "adjusted": "true", "sort": "asc", "limit": 50000}
    rows, pages, seen_urls = [], 0, set()
    while url:
        if pages >= 12 or url in seen_urls:
            raise ValueError("market_pagination_not_complete")
        seen_urls.add(url)
        payload = _request_json(session, url, params)
        if (not isinstance(payload, dict) or payload.get("status") not in {"OK", "DELAYED"}
                or payload.get("adjusted") is not True or not isinstance(payload.get("results"), list)):
            raise ValueError("polygon_response_contract_invalid")
        rows.extend(payload["results"])
        pages += 1
        next_url = payload.get("next_url")
        if not next_url:
            break
        parsed = urlsplit(next_url)
        if (parsed.scheme != "https" or parsed.hostname != "api.polygon.io"
                or not parsed.path.startswith(f"/v2/aggs/ticker/{symbol}/range/")):
            raise ValueError("polygon_next_url_invalid")
        # Discard any embedded credential and always use this task's own key.
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key.lower() != "apikey"]
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
        params = {"apiKey": api_key}
        time.sleep(.4)
    clean = unique_bars(rows)
    return {"symbol": symbol, "venue": "us_equity_polygon", "multiplier": multiplier, "span": span,
            "adjusted": True, "pages": pages, "bars": clean, "bars_sha256": fingerprint(clean),
            "downloaded_at": iso(datetime.now(UTC)),
            "adjustment_limits": "provider_current_adjusted_history_not_archived_point_in_time_revisions"}


def collect_binance(session, contract):
    start = WINDOW_START - timedelta(days=9)
    cursor = int(start.timestamp() * 1000)
    end_ms = int(WINDOW_END.timestamp() * 1000) - 1
    rows, pages = [], 0
    while cursor <= end_ms:
        if pages >= 35:
            raise ValueError("binance_pagination_not_complete")
        payload = _request_json(session, "https://data-api.binance.vision/api/v3/klines",
                                {"symbol": contract, "interval": "5m", "startTime": cursor,
                                 "endTime": end_ms, "limit": 1000})
        if not isinstance(payload, list):
            raise ValueError("binance_response_invalid")
        if not payload:
            break
        chunk = []
        for raw in payload:
            if not isinstance(raw, list) or len(raw) < 7:
                raise ValueError("binance_kline_invalid")
            opened, closed = _number(raw[0]), _number(raw[6])
            if (opened != int(opened) or opened % 300000 or closed != int(closed)
                    or opened < cursor or closed >= int(WINDOW_END.timestamp() * 1000)
                    or closed - opened != 299999):
                raise ValueError("binance_kline_clock_invalid")
            chunk.append({"t": opened, "o": raw[1], "h": raw[2], "l": raw[3], "c": raw[4], "v": raw[5]})
        clean = unique_bars(chunk)
        if not clean or clean[-1]["t"] < cursor:
            raise ValueError("binance_pagination_no_progress")
        rows.extend(clean)
        cursor = int(clean[-1]["t"]) + 300000
        pages += 1
        time.sleep(.25)
    clean = unique_bars(rows)
    expected_first = int(start.timestamp() * 1000)
    expected_last = int(WINDOW_END.timestamp() * 1000) - 300000
    if (not clean or clean[0]["t"] != expected_first or clean[-1]["t"] != expected_last
            or any(b["t"]-a["t"] != 300000 for a, b in zip(clean, clean[1:]))):
        raise ValueError("binance_source_coverage_incomplete")
    return {"symbol": contract, "venue": "binance_spot", "quote_currency": "USDT", "timeframe": "5m",
            "pages": pages, "bars": clean, "bars_sha256": fingerprint(clean), "downloaded_at": iso(datetime.now(UTC))}


def collect(directory):
    import requests
    from scripts.chase_gate_backtest import _load_polygon_key

    directory.mkdir(parents=True, exist_ok=True)
    protocol_path = directory / "manifest.json"
    if protocol_path.exists():
        raise ValueError("frozen_manifest_already_exists_use_new_directory")
    protocol = manifest()
    _save(protocol_path, protocol)  # Frozen BEFORE any outcome/source request.
    key = _load_polygon_key()
    jobs = [(symbol, 1, "day", WARMUP_START) for symbol in STOCKS + BIOTECH + PENNIES]
    jobs += [(symbol, mult, "minute", "2026-05-01" if mult == 30 else "2026-07-02")
             for symbol in STOCKS for mult in (30, 5)]
    inventory = {"manifest_sha256": fingerprint(protocol), "sources": [], "complete": False}
    session = requests.Session()
    try:
        for symbol, multiplier, span, start in jobs:
            name = f"stock-{symbol}-{multiplier}{span}.json"
            item = {"file": name, "symbol": symbol, "venue": "us_equity_polygon", "multiplier": multiplier, "span": span}
            try:
                if not key:
                    raise ValueError("historical_market_credential_missing")
                source = collect_polygon(session, symbol, multiplier, span, start, key)
                _save(directory / name, source)
                item.update(status="available", bar_count=len(source["bars"]), bars_sha256=source["bars_sha256"])
            except ValueError as error:
                item.update(status="unavailable", error_code=str(error))
            inventory["sources"].append(item)
            _save(directory / "inventory.json", inventory)
            print(json.dumps({key: item.get(key) for key in ("symbol", "span", "multiplier", "status", "bar_count", "error_code")}), flush=True)
            time.sleep(.5)
        for contract in CRYPTO:
            name = f"crypto-{contract}-5m.json"
            item = {"file": name, "symbol": contract, "venue": "binance_spot", "timeframe": "5m"}
            try:
                source = collect_binance(session, contract)
                _save(directory / name, source)
                item.update(status="available", bar_count=len(source["bars"]), bars_sha256=source["bars_sha256"])
            except ValueError as error:
                item.update(status="unavailable", error_code=str(error))
            inventory["sources"].append(item)
            _save(directory / "inventory.json", inventory)
            print(json.dumps({key: item.get(key) for key in ("symbol", "status", "bar_count", "error_code")}), flush=True)
        available = all(item["status"] == "available" for item in inventory["sources"])
        inventory.update(complete=available, collection_finished=True, all_sources_available=available,
                         completed_at=iso(datetime.now(UTC)))
        _save(directory / "inventory.json", inventory)
    finally:
        session.close()
    return inventory


def verify_sources(directory):
    """Inspect the frozen files without network or modifying their hashes."""
    from zoneinfo import ZoneInfo
    from modules.stock_swing_contract import session_close
    from scripts.scanner_history_bi import expected_sessions, canonical_daily_bars
    ny = ZoneInfo("America/New_York")
    expected_days = expected_sessions(WINDOW_START.date(), WINDOW_END.date())
    protocol = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (protocol.get("window") != {"start_inclusive": iso(WINDOW_START), "end_exclusive": iso(WINDOW_END)}
            or protocol.get("stock_symbols") != list(STOCKS)
            or protocol.get("biotech_symbols") != list(BIOTECH)
            or protocol.get("penny_symbols") != list(PENNIES)
            or protocol.get("crypto_contracts") != list(CRYPTO)):
        raise ValueError("frozen_manifest_window_or_cohort_mismatch")
    inventory = json.loads((directory / "inventory.json").read_text(encoding="utf-8"))
    if inventory.get("manifest_sha256") != fingerprint(protocol):
        raise ValueError("frozen_manifest_fingerprint_mismatch")
    expected_sources = {}
    for symbol in STOCKS+BIOTECH+PENNIES:
        expected_sources[f"stock-{symbol}-1day.json"] = (symbol,"us_equity_polygon",1,"day",None)
    for symbol in STOCKS:
        for multiplier in (30,5):
            expected_sources[f"stock-{symbol}-{multiplier}minute.json"] = (symbol,"us_equity_polygon",multiplier,"minute",None)
    for symbol in CRYPTO:
        expected_sources[f"crypto-{symbol}-5m.json"] = (symbol,"binance_spot",None,None,"5m")
    items = inventory.get("sources")
    if not isinstance(items,list) or len(items)!=len(expected_sources):
        raise ValueError("frozen_source_inventory_coverage_invalid")
    seen = set()
    for item in items:
        if not isinstance(item,dict):
            raise ValueError("frozen_source_inventory_schema_invalid")
        name=item.get("file")
        identity=tuple(item.get(key) for key in ("symbol","venue","multiplier","span","timeframe"))
        if (name not in expected_sources or name in seen or identity!=expected_sources[name]
                or item.get("status") not in {"available","unavailable"}):
            raise ValueError("frozen_source_inventory_identity_invalid")
        seen.add(name)
    result = {"kind": "frozen_source_coverage_audit", "manifest_sha256": fingerprint(protocol),
              "expected_us_sessions": len(expected_days), "sources": [], "source_files_unchanged": True}
    for item in inventory["sources"]:
        record = {k: item[k] for k in ("file", "symbol", "venue")}
        if item.get("status") != "available":
            record.update(status="unavailable", error_code=item.get("error_code"))
            result["sources"].append(record)
            continue
        source = json.loads((directory / item["file"]).read_text(encoding="utf-8"))
        bars = source.get("bars")
        if (not isinstance(bars, list) or source.get("bars_sha256") != fingerprint(bars)
                or item.get("bars_sha256") != fingerprint(bars)
                or source.get("symbol") != item["symbol"] or source.get("venue") != item["venue"]):
            raise ValueError("frozen_source_fingerprint_or_identity_mismatch")
        clean = unique_bars(bars)
        if clean != bars:
            raise ValueError("frozen_source_unordered_or_duplicate")
        record.update(status="available", bars_sha256=fingerprint(bars), bar_count=len(bars))
        if source["venue"] == "binance_spot":
            first = int((WINDOW_START-timedelta(days=9)).timestamp()*1000)
            last = int(WINDOW_END.timestamp()*1000)-300000
            missing = int((last-first)/300000)+1-len(bars)
            continuous = (bool(bars) and bars[0]["t"] == first and bars[-1]["t"] == last
                          and all(r["t"] % 300000 == 0 for r in bars)
                          and all(b["t"]-a["t"] == 300000 for a,b in zip(bars,bars[1:])))
            record.update(timeframe="5m", continuous=continuous, missing_expected_bars=missing)
            if not continuous:
                raise ValueError("frozen_crypto_coverage_incomplete")
        elif source.get("span") == "day":
            if source.get("adjusted") is not True or source.get("multiplier") != 1:
                raise ValueError("frozen_daily_adjustment_or_interval_invalid")
            canonical = canonical_daily_bars(bars)
            observed = {r["date"] for r in canonical}
            missing = [d for d in expected_days if d not in observed]
            record.update(timeframe="1D", sessions_checked=len(expected_days), missing_sessions=missing)
            if missing:
                raise ValueError("frozen_daily_session_coverage_missing")
        else:
            multiplier = source.get("multiplier")
            if multiplier not in (5,30) or source.get("span") != "minute" or source.get("adjusted") is not True:
                raise ValueError("frozen_intraday_adjustment_or_interval_invalid")
            stamps = {int(r["t"]) for r in bars}
            expected = set()
            for day in expected_days:
                close = session_close(day)
                opened = datetime.fromisoformat(day).replace(tzinfo=ny, hour=9, minute=30)
                cursor = int(opened.timestamp()*1000)
                while cursor < int(close.timestamp()*1000):
                    expected.add(cursor)
                    cursor += multiplier*60*1000
            if any(r["t"] % (multiplier*60*1000) for r in bars):
                raise ValueError("frozen_intraday_grid_invalid")
            record.update(timeframe=f"{multiplier}m", regular_session_expected=len(expected),
                          regular_session_observed=len(expected & stamps), missing_regular_slots=len(expected-stamps),
                          missing_slot_policy="report_missing_not_fake_zero_volume_bars")
        result["sources"].append(record)
    result["all_sources_available"] = all(r["status"] == "available" for r in result["sources"])
    return result


def supplement_stock_warmup(source_directory, target_directory):
    """Extend only reference history, never select assets from known outcomes."""
    import requests
    import shutil
    from scripts.chase_gate_backtest import _load_polygon_key
    verify_sources(source_directory)
    target_directory.mkdir(parents=True, exist_ok=False)
    old = json.loads((source_directory / "manifest.json").read_text(encoding="utf-8"))
    protocol = {**old, "frozen_at": iso(datetime.now(UTC)), "stock_warmup_start": "2023-07-03",
                "predecessor_manifest_sha256": fingerprint(old),
                "amendment": "extend_stock_warmup_to_production_1095_calendar_day_window; no_change_to_cohort_dates_rules"}
    _save(target_directory / "manifest.json", protocol)  # Before additional requests.
    previous = json.loads((source_directory / "inventory.json").read_text(encoding="utf-8"))
    inventory = {"manifest_sha256": fingerprint(protocol), "sources": [], "complete": False}
    key = _load_polygon_key()
    session = requests.Session()
    try:
        for old_item in previous["sources"]:
            item = dict(old_item)
            if (item["symbol"] in STOCKS and item.get("span") == "day" and item.get("multiplier") == 1):
                if not key:
                    raise ValueError("historical_market_credential_missing")
                source = collect_polygon(session,item["symbol"],1,"day",protocol["stock_warmup_start"],key)
                _save(target_directory/item["file"],source)
                item.update(status="available",bar_count=len(source["bars"]),bars_sha256=source["bars_sha256"])
                print(json.dumps(dict(symbol=item["symbol"],bar_count=item["bar_count"])),flush=True)
            elif item.get("status") == "available":
                shutil.copyfile(source_directory/item["file"],target_directory/item["file"])
            inventory["sources"].append(item)
            _save(target_directory/"inventory.json",inventory)
        available=all(item["status"]=="available" for item in inventory["sources"])
        inventory.update(complete=available,collection_finished=True,all_sources_available=available,
                         completed_at=iso(datetime.now(UTC)))
        _save(target_directory/"inventory.json",inventory)
    finally:
        session.close()
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--collect", type=Path, help="Fresh private output directory, never production state")
    action.add_argument("--verify", type=Path, help="Existing frozen market directory, no network")
    action.add_argument("--supplement-stock-warmup", type=Path, help="Existing source, extend only the three stock daily histories")
    parser.add_argument("--supplement-output", type=Path)
    parser.add_argument("--coverage-output", type=Path)
    args = parser.parse_args()
    directory = (args.collect or args.verify or args.supplement_stock_warmup).resolve()
    private_root = (ROOT / "output").resolve()
    if directory == private_root or private_root not in directory.parents:
        parser.error("Use a new task directory beneath repository output/")
    try:
        if args.collect:
            collect(directory)
        elif args.supplement_stock_warmup:
            if not args.supplement_output:
                raise ValueError("supplement_output_required")
            destination = args.supplement_output.resolve()
            if private_root not in destination.parents or destination.exists():
                raise ValueError("use_new_private_supplement_output")
            supplement_stock_warmup(directory,destination)
        else:
            result = verify_sources(directory)
            if args.coverage_output:
                destination = args.coverage_output.resolve()
                if private_root not in destination.parents or destination.exists():
                    raise ValueError("use_new_private_coverage_output")
                _save(destination, result)
            print(json.dumps(result, allow_nan=False))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
