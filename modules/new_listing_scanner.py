#!/usr/bin/env python3
"""
🆕 New Listing Dump Scanner V1
================================
Erkennt neue PERP-Listings auf Crypto-Exchanges und generiert Short-Signale
nach dem typischen Pump-Dump-Muster bei neuen Listings.

Strategie (basierend auf Marktdaten 2024-2026):
- 54% der neuen Listings pumpen am ersten Tag
- 89% dumpen danach, 70% unter Peak innerhalb 2 Wochen
- Pump-Fenster: 2-6 Stunden
- Profitable Seite: SHORT nach dem Pump

Pipeline:
1. DETECT   — Neue PERP-Instrumente auf Exchange erkennen (Cache-Diff)
2. MONITOR  — Pump tracken via 1h-Candles (ATH, Volume, Momentum)
3. SCORE    — Pump-Exhaustion berechnen (7 Komponenten, 0-100)
4. SAFETY   — Liquiditäts-Checks (Spread, Depth, Volume)
5. SIGNAL   — Short-Entry mit Entry/Stop/TP wenn Score + Safety OK

Unterstützte Exchanges:
- Binance (Größte Exchange — 581 Perps, onboardDate verfügbar)
- MEXC (Schnellste Listings — 750 Perps, isNew-Flag + createTime)
- Bitget (539 Perps, launchTime verfügbar)
- Crypto.com Exchange (238 Perps, PERP-Trading)
"""

import os
import json
import time
import logging
import traceback
import re
import html as html_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests as req
except ImportError:
    import urllib.request
    req = None

log = logging.getLogger("bg_service")

# ═══════════════════════════════════════════════════════════════════════════════
# KONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
CACHE_DIR = Path(__file__).parent.parent / "data_cache"
INSTRUMENTS_CACHE = CACHE_DIR / "nls_instruments_cache.json"
MONITORING_FILE = CACHE_DIR / "nls_monitoring.json"
RESULTS_FILE = CACHE_DIR / "new_listing_scanner.json"

CONFIG = {
    # ── Detection ──
    "monitor_hours_max": 72,            # Neue Listings max 72h tracken

    # ── Safety Minimums ──
    "min_volume_24h_usd": 50_000,       # Minimum $50K tägliches Volume
    "max_spread_pct": 2.0,              # Max Bid-Ask-Spread in %
    "min_book_depth_usd": 2_000,        # Min $2K pro Seite im Orderbuch

    # ── Pump Detection ──
    "min_pump_pct": 15,                 # Min Pump % für Signal-Qualifikation
    "pump_window_candles": 24,          # Letzte 24 1h-Candles für Pump-Analyse

    # ── Exhaustion Thresholds ──
    "exh_short_entry": 65,              # ExhScore für Short-Signal
    "exh_watch": 45,                    # ExhScore für Watchlist

    # ── Risk Management ──
    "stop_above_ath_pct": 8.0,          # Stop 8% über ATH (Schutz gegen Fake-Breakouts)
    "tp1_from_ath_pct": 20.0,           # TP1: -20% vom ATH
    "tp2_from_ath_pct": 40.0,           # TP2: -40% vom ATH
    "max_position_hours": 48,           # Max Haltedauer
    "max_leverage": 10,                 # Max empfohlener Hebel
}

# Deep-audit guardrails: the original thresholds were too permissive for live
# P&D shorts. These overrides keep small/illiquid pumps visible in monitoring,
# but block them from active "short now" signals.
CONFIG.update({
    "min_volume_24h_usd": 500_000,
    "max_spread_pct": 1.2,
    "min_book_depth_usd": 10_000,
    "max_ticker_age_sec": 15 * 60,
    "max_candle_age_sec": 2 * 3600,
    "max_leverage": 3,
    "min_short_rr": 1.5,
    "max_signal_risk_pct": 35.0,
    "min_from_ath_for_short_pct": 3.0,
    "max_early_crack_from_ath_pct": 18.0,
    "early_crack_entry_score": 45,
    "early_crack_stop_buffer_pct": 3.0,
    "min_stop_above_entry_pct": 2.0,
    "micro_crack_enabled": True,
    "micro_timeframe": "5m",
    "micro_candle_count": 72,
    "ultra_micro_enabled": False,
    "ultra_micro_timeframe": "5m",
    "ultra_micro_candle_count": 45,
    "ultra_micro_max_age_hours": 6.0,
    "ultra_micro_min_score": 78,
    "micro_min_score": 70,
    "micro_min_crack_pct": 1.2,
    "micro_max_from_high_pct": 14.0,
    "micro_stop_buffer_pct": 1.5,
    "new_listing_short_min_age_hours": 1.0,
    "new_listing_short_max_age_hours": 72.0,
    "announcement_watch_hours": 168.0,
    "btc_tailwind_risk_change_pct": 2.0,
    "btc_tailwind_min_divergence_pct": -5.0,
    "btc_tailwind_min_crack_pct": 8.0,
})

# ═══════════════════════════════════════════════════════════════════════════════
# EXCHANGE API LAYER
# ═══════════════════════════════════════════════════════════════════════════════

def _api_get(url, params=None, timeout=15):
    """Robuster API-Call mit Fehlerbehandlung."""
    try:
        if req:
            resp = req.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            log.warning(f"API {resp.status_code}: {url}")
            return None
        else:
            # Fallback ohne requests
            import urllib.parse
            if params:
                url += "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
    except Exception as e:
        log.warning(f"API Error {url}: {e}")
        return None


# ── Crypto.com Exchange API ──────────────────────────────────────────────────

def _to_float(value, default=0.0):
    """Safe float conversion for noisy exchange payloads."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_epoch_seconds(ts):
    """Normalize exchange timestamps in seconds or milliseconds to seconds."""
    try:
        ts_float = float(ts)
    except (TypeError, ValueError):
        return 0
    if ts_float > 10_000_000_000:
        ts_float /= 1000
    return int(ts_float)


def _data_age_seconds(ts):
    ts_sec = _normalize_epoch_seconds(ts)
    if ts_sec <= 0:
        return None
    return max(0, int(time.time()) - ts_sec)


def _parse_book_side(side):
    parsed = []
    for row in side or []:
        try:
            if isinstance(row, dict):
                price = _to_float(row.get("price") or row.get("p"))
                qty = _to_float(row.get("quantity") or row.get("qty") or row.get("q"))
            else:
                price = _to_float(row[0])
                qty = _to_float(row[1])
            if price > 0 and qty > 0:
                parsed.append((price, qty))
        except Exception:
            continue
    return parsed


def _monitor_key(symbol, exchange):
    return f"{str(exchange or 'crypto.com').lower()}:{symbol}"


def _is_tradeable_short_signal(signal):
    if not isinstance(signal, dict):
        return False
    try:
        rr_effective = float(signal.get("rr_effective", 0) or 0)
        risk_pct = float(signal.get("risk_pct", 999) or 999)
    except (TypeError, ValueError):
        return False

    return (
        signal.get("direction") == "SHORT"
        and signal.get("timing_quality", 0) >= 4
        and signal.get("grade") in ("S", "A", "A+")
        and signal.get("safety_ok") is True
        and signal.get("confirmation_ok") is True
        and signal.get("btc_context_ok", True) is True
        and (not signal.get("micro_required", bool(CONFIG.get("micro_crack_enabled"))) or signal.get("micro_trigger_ok") is True)
        and not signal.get("continuation_risk")
        and not signal.get("tp1_missed")
        and not signal.get("tp2_missed")
        and rr_effective >= CONFIG["min_short_rr"]
        and risk_pct <= CONFIG["max_signal_risk_pct"]
        and signal.get("listing_trade_ok") is True
    )


CRYPTOCOM_BASE = "https://api.crypto.com/exchange/v1/public"

def fetch_cryptocom_instruments():
    """
    Holt alle Instrumente von Crypto.com Exchange.
    Gibt Liste von PERP-Instrumenten zurück mit Listing-Info.
    """
    data = _api_get(f"{CRYPTOCOM_BASE}/get-instruments")
    if not data or "result" not in data:
        return []

    instruments = data["result"].get("data", [])
    perps = []
    for inst in instruments:
        if inst.get("inst_type") == "PERPETUAL_SWAP":
            perps.append({
                "symbol": inst.get("symbol", ""),
                "base": inst.get("base_ccy", ""),
                "quote": inst.get("quote_ccy", ""),
                "instrument_name": inst.get("symbol", ""),
                "tradable": inst.get("tradable", False),
                "max_leverage": inst.get("max_leverage", "1"),
                "exchange": "crypto.com",
            })
    return perps


def fetch_cryptocom_ticker(instrument_name):
    """Ticker-Daten für ein Instrument."""
    data = _api_get(f"{CRYPTOCOM_BASE}/get-tickers", {"instrument_name": instrument_name})
    if not data or "result" not in data:
        return None
    tickers = data["result"].get("data", [])
    if tickers:
        t = tickers[0]
        return {
            "price": float(t.get("a", t.get("last", 0))),
            "bid": float(t.get("b", 0)),
            "ask": float(t.get("k", 0)),
            "high_24h": float(t.get("h", 0)),
            "low_24h": float(t.get("l", 0)),
            "volume_24h": float(t.get("v", 0)),
            "volume_usd_24h": float(t.get("vv", 0)),
            "change_24h": float(t.get("c", 0)) * 100 if t.get("c") else 0,
            "open_interest": float(t.get("oi", 0)),
            "timestamp": t.get("t", 0),
        }
    return None


def fetch_cryptocom_candles(instrument_name, timeframe="1h", count=50):
    """1h-Candles für ein Instrument. Max 50 pro Call."""
    tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1D"}
    data = _api_get(f"{CRYPTOCOM_BASE}/get-candlestick", {
        "instrument_name": instrument_name,
        "timeframe": tf_map.get(timeframe, "1h"),
        "count": count,
    })
    if not data or "result" not in data:
        return []
    raw = data["result"].get("data", [])
    candles = []
    for c in raw:
        candles.append({
            "timestamp": c.get("t", 0),
            "open": float(c.get("o", 0)),
            "high": float(c.get("h", 0)),
            "low": float(c.get("l", 0)),
            "close": float(c.get("c", 0)),
            "volume": float(c.get("v", 0)),
            "volume_usd": float(c.get("vv", 0)),
        })
    # Chronologisch sortieren (älteste zuerst)
    candles.sort(key=lambda x: x["timestamp"])
    return candles


def fetch_cryptocom_orderbook(instrument_name, depth=10):
    """Orderbuch für Spread + Depth Analyse."""
    data = _api_get(f"{CRYPTOCOM_BASE}/get-book", {
        "instrument_name": instrument_name,
        "depth": depth,
    })
    if not data or "result" not in data:
        return None
    book = data["result"].get("data", [])
    if book:
        b = book[0]
        return {
            "bids": [(float(x[0]), float(x[1])) for x in b.get("bids", [])],
            "asks": [(float(x[0]), float(x[1])) for x in b.get("asks", [])],
        }
    return None


# ── MEXC Futures API (Frühwarnung — listet am schnellsten, 755 Perps) ────────

def fetch_mexc_orderbook(symbol, depth=20):
    """MEXC Futures order book normalized to bids/asks tuples."""
    data = _api_get(f"{MEXC_FUTURES_BASE}/depth/{symbol}", {"limit": depth})
    if not data or not data.get("success"):
        return None
    book = data.get("data", {})
    bids = _parse_book_side(book.get("bids") or book.get("Bids"))
    asks = _parse_book_side(book.get("asks") or book.get("Asks"))
    return {"bids": bids[:depth], "asks": asks[:depth]} if bids and asks else None


def fetch_binance_orderbook(symbol, depth=20):
    """Binance Futures order book normalized to bids/asks tuples."""
    data = _api_get(f"{BINANCE_FUTURES_BASE}/depth", {"symbol": symbol, "limit": depth})
    if not data:
        return None
    bids = _parse_book_side(data.get("bids"))
    asks = _parse_book_side(data.get("asks"))
    return {"bids": bids[:depth], "asks": asks[:depth]} if bids and asks else None


def fetch_bitget_orderbook(symbol, depth=20):
    """Bitget Futures order book normalized to bids/asks tuples."""
    data = _api_get(f"{BITGET_BASE}/market/orderbook", {
        "productType": "USDT-FUTURES",
        "symbol": symbol,
        "limit": str(depth),
    })
    if not data or data.get("code") not in (None, "00000"):
        return None
    book = data.get("data", {})
    bids = _parse_book_side(book.get("bids"))
    asks = _parse_book_side(book.get("asks"))
    return {"bids": bids[:depth], "asks": asks[:depth]} if bids and asks else None


MEXC_FUTURES_BASE = "https://contract.mexc.com/api/v1/contract"

def fetch_mexc_futures_instruments():
    """
    Holt alle MEXC Futures-Kontrakte mit createTime und isNew-Flag.
    MEXC listet am schnellsten (Stunden nach Token-Launch).
    """
    data = _api_get(f"{MEXC_FUTURES_BASE}/detail")
    if not data or not data.get("success"):
        return []

    contracts = data.get("data", [])
    perps = []
    for c in contracts:
        if c.get("quoteCoin") == "USDT" and c.get("state") == 0:  # state 0 = aktiv
            perps.append({
                "symbol": c.get("symbol", ""),
                "base": c.get("baseCoin", ""),
                "quote": "USDT",
                "instrument_name": c.get("symbol", ""),
                "tradable": True,
                "max_leverage": str(c.get("maxLeverage", 1)),
                "exchange": "mexc",
                "is_new": c.get("isNew", False),
                "create_time": c.get("createTime", 0),
            })
    return perps


def fetch_mexc_ticker(symbol):
    """MEXC Futures Ticker."""
    data = _api_get(f"{MEXC_FUTURES_BASE}/ticker", {"symbol": symbol})
    if not data or not data.get("success"):
        return None
    t = data.get("data", {})
    if not t or not isinstance(t, dict):
        return None

    return {
        "price": float(t.get("lastPrice", 0)),
        "bid": float(t.get("bid1", 0)),
        "ask": float(t.get("ask1", 0)),
        "high_24h": float(t.get("high24Price", 0)),
        "low_24h": float(t.get("lower24Price", 0)),
        "volume_24h": float(t.get("volume24", 0)),
        "volume_usd_24h": float(t.get("amount24", 0)),
        "change_24h": float(t.get("riseFallRate", 0)) * 100,
        "open_interest": float(t.get("holdVol", 0)),
        "funding_rate": float(t.get("fundingRate", 0)),
        "timestamp": t.get("timestamp", 0),
    }


def fetch_mexc_candles(symbol, timeframe="1h", count=50):
    """MEXC Futures Klines."""
    tf_map = {"1m": "Min1", "5m": "Min5", "15m": "Min15", "1h": "Min60", "4h": "Hour4", "1d": "Day1"}
    data = _api_get(f"{MEXC_FUTURES_BASE}/kline/{symbol}", {
        "interval": tf_map.get(timeframe, "Min60"),
        "limit": count,
    })
    if not data or not data.get("success"):
        return []
    raw = data.get("data", {})
    if not isinstance(raw, dict):
        return []

    times = raw.get("time", [])
    opens = raw.get("open", [])
    highs = raw.get("high", [])
    lows = raw.get("low", [])
    closes = raw.get("close", [])
    vols = raw.get("vol", [])
    amounts = raw.get("amount", [])

    candles = []
    for i in range(len(times)):
        vol_usd = float(amounts[i]) if i < len(amounts) else float(vols[i]) * float(closes[i]) if i < len(vols) and i < len(closes) else 0
        candles.append({
            "timestamp": int(times[i]) if i < len(times) else 0,
            "open": float(opens[i]) if i < len(opens) else 0,
            "high": float(highs[i]) if i < len(highs) else 0,
            "low": float(lows[i]) if i < len(lows) else 0,
            "close": float(closes[i]) if i < len(closes) else 0,
            "volume": float(vols[i]) if i < len(vols) else 0,
            "volume_usd": vol_usd,
        })
    candles.sort(key=lambda x: x["timestamp"])
    return candles


# ── Binance Futures API (581 Perps, onboardDate verfügbar) ───────────────────

BINANCE_FUTURES_BASE = "https://fapi.binance.com/fapi/v1"

def fetch_binance_futures_instruments():
    """
    Holt alle Binance USDT-M Perpetual-Kontrakte mit onboardDate.
    Binance = größte Exchange, Listings hier sind besonders relevant.
    """
    data = _api_get(f"{BINANCE_FUTURES_BASE}/exchangeInfo")
    if not data:
        return []

    symbols = data.get("symbols", [])
    perps = []
    for s in symbols:
        if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING":
            perps.append({
                "symbol": s.get("symbol", ""),
                "base": s.get("baseAsset", ""),
                "quote": s.get("quoteAsset", "USDT"),
                "instrument_name": s.get("symbol", ""),
                "tradable": True,
                "max_leverage": "125",  # Binance default max
                "exchange": "binance",
                "onboard_date": s.get("onboardDate", 0),
            })
    return perps


def fetch_binance_ticker(symbol):
    """Binance Futures 24h Ticker + OI + Funding + Long/Short Ratio."""
    data = _api_get(f"{BINANCE_FUTURES_BASE}/ticker/24hr", {"symbol": symbol})
    if not data or not isinstance(data, dict):
        return None

    result = {
        "price": float(data.get("lastPrice", 0)),
        "bid": float(data.get("lastPrice", 0)),
        "ask": float(data.get("lastPrice", 0)),
        "high_24h": float(data.get("highPrice", 0)),
        "low_24h": float(data.get("lowPrice", 0)),
        "volume_24h": float(data.get("volume", 0)),
        "volume_usd_24h": float(data.get("quoteVolume", 0)),
        "change_24h": float(data.get("priceChangePercent", 0)),
        "open_interest": 0,
        "funding_rate": 0,
        "long_short_ratio": 0,
        "timestamp": int(data.get("closeTime", 0)),
    }

    # OI separat holen
    try:
        oi_data = _api_get(f"{BINANCE_FUTURES_BASE}/openInterest", {"symbol": symbol})
        if oi_data and isinstance(oi_data, dict):
            result["open_interest"] = float(oi_data.get("openInterest", 0))
    except Exception:
        pass

    # Funding Rate separat holen
    try:
        fr_data = _api_get(f"{BINANCE_FUTURES_BASE}/premiumIndex", {"symbol": symbol})
        if fr_data and isinstance(fr_data, dict):
            result["funding_rate"] = float(fr_data.get("lastFundingRate", 0))
    except Exception:
        pass

    # Top Trader Long/Short Ratio (Accounts)
    try:
        ls_data = _api_get("https://fapi.binance.com/futures/data/topLongShortAccountRatio",
                           {"symbol": symbol, "period": "1h", "limit": 1})
        if ls_data and isinstance(ls_data, list) and len(ls_data) > 0:
            result["long_short_ratio"] = float(ls_data[0].get("longShortRatio", 0))
    except Exception:
        pass

    return result


def fetch_binance_candles(symbol, timeframe="1h", count=50):
    """Binance Futures Klines."""
    tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    data = _api_get(f"{BINANCE_FUTURES_BASE}/klines", {
        "symbol": symbol,
        "interval": tf_map.get(timeframe, "1h"),
        "limit": count,
    })
    if not data or not isinstance(data, list):
        return []
    candles = []
    for c in data:
        # Format: [openTime, open, high, low, close, volume, closeTime, quoteVolume, ...]
        if isinstance(c, list) and len(c) >= 8:
            candles.append({
                "timestamp": int(c[0]) // 1000,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "volume_usd": float(c[7]),
            })
    candles.sort(key=lambda x: x["timestamp"])
    return candles


# ── Bitget Futures API (539 Perps, launchTime verfügbar) ─────────────────────

BITGET_BASE = "https://api.bitget.com/api/v2/mix"

def fetch_bitget_futures_instruments():
    """
    Holt alle Bitget USDT-Futures-Kontrakte mit launchTime.
    """
    data = _api_get(f"{BITGET_BASE}/market/contracts", {"productType": "USDT-FUTURES"})
    if not data:
        return []

    contracts = data.get("data", [])
    perps = []
    for c in contracts:
        if c.get("symbolStatus") == "normal":
            lt_raw = c.get("launchTime", "") or ""
            try:
                lt = int(lt_raw) if lt_raw else 0
            except (ValueError, TypeError):
                lt = 0
            perps.append({
                "symbol": c.get("symbol", ""),
                "base": c.get("baseCoin", ""),
                "quote": "USDT",
                "instrument_name": c.get("symbol", ""),
                "tradable": True,
                "max_leverage": str(c.get("maxLever", 1)),
                "exchange": "bitget",
                "launch_time": lt,
            })
    return perps


def fetch_bitget_ticker(symbol):
    """Bitget Futures Ticker."""
    data = _api_get(f"{BITGET_BASE}/market/ticker",
                    {"productType": "USDT-FUTURES", "symbol": symbol})
    if not data:
        return None
    tickers = data.get("data", [])
    if not tickers:
        return None
    t = tickers[0] if isinstance(tickers, list) else tickers
    if not isinstance(t, dict):
        return None
    return {
        "price": float(t.get("lastPr", 0)),
        "bid": float(t.get("bidPr", 0)),
        "ask": float(t.get("askPr", 0)),
        "high_24h": float(t.get("high24h", 0)),
        "low_24h": float(t.get("low24h", 0)),
        "volume_24h": float(t.get("baseVolume", 0)),
        "volume_usd_24h": float(t.get("usdtVolume", 0)),
        "change_24h": float(t.get("change24h", 0)) * 100,
        "open_interest": float(t.get("holdingAmount", 0)),
        "funding_rate": float(t.get("fundingRate", 0)),
        "timestamp": int(t.get("ts", 0)),
    }


def fetch_bitget_candles(symbol, timeframe="1h", count=50):
    """Bitget Futures Candles."""
    tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
    data = _api_get(f"{BITGET_BASE}/market/candles", {
        "productType": "USDT-FUTURES",
        "symbol": symbol,
        "granularity": tf_map.get(timeframe, "1H"),
        "limit": str(count),
    })
    if not data:
        return []
    raw = data.get("data", [])
    candles = []
    for c in raw:
        # Format: [ts, open, high, low, close, vol, quoteVol]
        if isinstance(c, list) and len(c) >= 7:
            candles.append({
                "timestamp": int(c[0]) // 1000,  # ms → sec
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "volume_usd": float(c[6]),
            })
    candles.sort(key=lambda x: x["timestamp"])
    return candles


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-EXCHANGE ADAPTER — einheitliches Interface
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ticker_for(symbol, exchange):
    """Holt Ticker für ein Symbol von der richtigen Exchange."""
    if exchange == "mexc":
        return fetch_mexc_ticker(symbol)
    elif exchange == "bitget":
        return fetch_bitget_ticker(symbol)
    elif exchange == "binance":
        return fetch_binance_ticker(symbol)
    else:
        return fetch_cryptocom_ticker(symbol)


def fetch_candles_for(symbol, exchange, timeframe="1h", count=50):
    """Holt Candles für ein Symbol von der richtigen Exchange."""
    if exchange == "mexc":
        return fetch_mexc_candles(symbol, timeframe, count)
    elif exchange == "bitget":
        return fetch_bitget_candles(symbol, timeframe, count)
    elif exchange == "binance":
        return fetch_binance_candles(symbol, timeframe, count)
    else:
        return fetch_cryptocom_candles(symbol, timeframe, count)


def _clean_listing_base_symbol(symbol):
    """Normalize an announcement or contract symbol to a tradable base coin."""
    s = str(symbol or "").upper().strip()
    s = re.sub(r"[^A-Z0-9_/-]", "", s)
    for suffix in ("_USDT", "-USDT", "/USDT", "USDT", "_USDC", "-USDC", "/USDC", "USDC"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip("_-/")


def _extract_listing_symbols_from_title(title):
    """Extract likely crypto symbols from exchange listing announcement titles."""
    text = str(title or "")
    if not text:
        return []
    # Stock/TradFi perps are exchange instruments, but not crypto new-listing shorts.
    if re.search(r"\b(stock|tradfi|equity|shares?|index|indices)\b", text, flags=re.I):
        return []

    symbols = []
    symbols.extend(re.findall(r"\(([A-Z0-9]{2,24})\)", text))
    symbols.extend(re.findall(r"\b([A-Z0-9]{2,24})(?:USDT|USDC)\b", text))

    blocked = {"UTC", "VIP", "USD", "USDT", "USDC", "ETF", "ETP", "BTCUSD"}
    out = []
    seen = set()
    for sym in symbols:
        base = _clean_listing_base_symbol(sym)
        if not base or base in blocked or base.isdigit():
            continue
        if base not in seen:
            seen.add(base)
            out.append(base)
    return out


def fetch_binance_listing_announcements(limit=20):
    """Official Binance announcement feed for recent crypto listing/futures-launch notices."""
    data = _api_get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query", {
        "type": 1,
        "catalogId": 48,
        "pageNo": 1,
        "pageSize": min(max(int(limit), 1), 50),
    }, timeout=15)
    announcements = []
    catalogs = (data or {}).get("data", {}).get("catalogs", []) if isinstance(data, dict) else []
    articles = catalogs[0].get("articles", []) if catalogs else []
    for article in articles:
        title = article.get("title", "")
        if not re.search(r"\b(will list|futures will launch|will launch|initial listing|new listing)\b", title, flags=re.I):
            continue
        symbols = _extract_listing_symbols_from_title(title)
        if not symbols:
            continue
        announcements.append({
            "source": "binance_announcement",
            "exchange": "binance",
            "title": title,
            "symbols": symbols,
            "release_ms": int(article.get("releaseDate") or 0),
            "url": f"https://www.binance.com/en/support/announcement/{article.get('code')}" if article.get("code") else "",
        })
    return announcements


def fetch_bitget_listing_announcements(limit=10):
    """Official Bitget announcements endpoint for recent spot/futures coin listings."""
    data = _api_get("https://api.bitget.com/api/v2/public/annoucements", {
        "annType": "coin_listings",
        "language": "en_US",
        "limit": min(max(int(limit), 1), 10),
    }, timeout=15)
    announcements = []
    rows = data.get("data", []) if isinstance(data, dict) else []
    for row in rows:
        title = row.get("annTitle", "")
        subtype = str(row.get("annSubType", "") or "").lower()
        if subtype not in ("spot", "futures"):
            continue
        if not re.search(r"\b(to list|initial listing|new .*listing|launch|pre-market|pre-listing)\b", title, flags=re.I):
            continue
        symbols = _extract_listing_symbols_from_title(title)
        if not symbols:
            continue
        announcements.append({
            "source": "bitget_announcement",
            "exchange": "bitget",
            "title": title,
            "symbols": symbols,
            "release_ms": int(row.get("cTime") or 0),
            "url": row.get("annUrl", ""),
            "subtype": subtype,
        })
    return announcements


def _parse_mexc_listing_announcements_html(html, limit=20):
    announcements = []
    seen = set()
    pattern = re.compile(
        r'<div class="SearchResultItem_titleWrapper[^>]*>\s*'
        r'<a[^>]+title="([^"]+)"[^>]+href="([^"]+)"[^>]*>.*?</a>\s*'
        r'<time[^>]+dateTime="([^"]+)"',
        flags=re.S,
    )
    for title, href, dt_text in pattern.findall(str(html or "")):
        title = html_lib.unescape(re.sub(r"\s+", " ", title)).strip()
        if not re.search(r"\b(to list|will list|initial listing|new listing|usdt-m futures|first in market|pre-market)\b", title, flags=re.I):
            continue
        symbols = _extract_listing_symbols_from_title(title)
        if not symbols:
            continue
        try:
            release_ms = int(datetime.fromisoformat(dt_text.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            release_ms = 0
        href = html_lib.unescape(href)
        if href.startswith("/"):
            href = "https://www.mexc.fm" + href
        key = (title, release_ms)
        if key in seen:
            continue
        seen.add(key)
        announcements.append({
            "source": "mexc_announcement",
            "exchange": "mexc",
            "title": title,
            "symbols": symbols,
            "release_ms": release_ms,
            "url": href,
        })
        if len(announcements) >= limit:
            break
    return announcements


def fetch_mexc_listing_announcements(limit=20):
    """Official MEXC announcement page for recent spot/futures coin listings."""
    html = ""
    for url in (
        "https://www.mexc.fm/announcements/new-listings",
        "https://www.mexc.co/announcements/new-listings",
    ):
        try:
            if req:
                resp = req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                if resp.status_code == 200 and "MEXC" in resp.text:
                    html = resp.text
                    break
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=15) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                    break
        except Exception as e:
            log.warning(f"MEXC announcement fetch error {url}: {e}")
    if not html:
        return []
    return _parse_mexc_listing_announcements_html(html, limit=limit)


def fetch_listing_announcements():
    """Fetch and normalize recent new-listing announcements from supported exchanges."""
    announcements = []
    for fetcher in (fetch_binance_listing_announcements, fetch_bitget_listing_announcements, fetch_mexc_listing_announcements):
        try:
            announcements.extend(fetcher())
        except Exception as e:
            log.warning(f"NLS announcement fetch error: {e}")

    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=float(CONFIG.get("announcement_watch_hours", 168)))).timestamp() * 1000)
    deduped = {}
    for ann in announcements:
        release_ms = int(ann.get("release_ms") or 0)
        if release_ms and release_ms < cutoff_ms:
            continue
        for base in ann.get("symbols", []) or []:
            key = f"{ann.get('source')}:{base}:{release_ms}"
            if key not in deduped:
                item = dict(ann)
                item["base"] = base
                deduped[key] = item
    return sorted(deduped.values(), key=lambda item: int(item.get("release_ms") or 0), reverse=True)


def _announcement_age_hours(announcement):
    try:
        release_ms = int(announcement.get("release_ms") or 0)
    except (TypeError, ValueError):
        release_ms = 0
    if release_ms <= 0:
        return None
    return max(0, round((time.time() - (release_ms / 1000)) / 3600, 1))


def _announcement_exchange(announcement):
    """Return the exchange named by an announcement source."""
    exchange = str((announcement or {}).get("exchange") or "").strip().lower()
    if exchange:
        return exchange
    source = str((announcement or {}).get("source") or "").strip().lower()
    if source.endswith("_announcement"):
        return source[: -len("_announcement")]
    return source


def _attach_announcement_contracts(announcement_watchlist, all_perps, is_stock_token_func=None):
    """
    Attach only same-exchange contracts to exchange announcements.

    A Bitget headline is not proof that an existing MEXC contract with the same
    base is the announced Bitget market. Cross-exchange matches stay
    informational and must not become tradeable new-listing candidates.
    """
    is_stock_token_func = is_stock_token_func or (lambda _symbol: False)
    perps_by_base = {}
    for p in all_perps or []:
        sym = p.get("symbol", "")
        if is_stock_token_func(sym):
            continue
        base = _clean_listing_base_symbol(p.get("base") or sym)
        if base:
            perps_by_base.setdefault(base, []).append(p)

    announcement_backed_new = []
    for ann in announcement_watchlist or []:
        base = ann.get("base") or ""
        ann_exchange = _announcement_exchange(ann)
        matching = perps_by_base.get(base, [])
        same_exchange = [p for p in matching if str(p.get("exchange") or "").lower() == ann_exchange]
        cross_exchange = [p for p in matching if str(p.get("exchange") or "").lower() != ann_exchange]

        ann["age_hours"] = _announcement_age_hours(ann)
        ann["matched_contracts"] = [
            {"exchange": p.get("exchange"), "symbol": p.get("symbol")}
            for p in same_exchange[:2]
        ]
        ann["cross_exchange_contracts"] = [
            {"exchange": p.get("exchange"), "symbol": p.get("symbol")}
            for p in cross_exchange[:4]
        ]
        ann["contract_confirmed"] = bool(same_exchange)
        ann["tradable_contract_confirmed"] = bool(same_exchange)
        if not same_exchange:
            ann["watch_reason"] = "contract_not_live_on_announcement_exchange"
            continue

        for p in same_exchange[:2]:
            item = dict(p)
            item["announcement_source"] = ann.get("source")
            item["announcement_exchange"] = ann_exchange
            item["announcement_title"] = ann.get("title")
            item["announcement_url"] = ann.get("url")
            item["announcement_release_ms"] = ann.get("release_ms")
            item["announcement_base"] = base
            item["contract_confirmed"] = True
            item["tradable_contract_confirmed"] = True
            item["contract_confirmation"] = "same_exchange_announcement_contract"
            announcement_backed_new.append(item)

    return announcement_backed_new


# ═══════════════════════════════════════════════════════════════════════════════
# LISTING DETECTION (Multi-Exchange Cache-Diff)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_orderbook_for(symbol, exchange, depth=20):
    """Holt ein echtes Orderbook fuer Safety/Spread-Checks."""
    if exchange == "mexc":
        return fetch_mexc_orderbook(symbol, depth)
    elif exchange == "bitget":
        return fetch_bitget_orderbook(symbol, depth)
    elif exchange == "binance":
        return fetch_binance_orderbook(symbol, depth)
    else:
        return fetch_cryptocom_orderbook(symbol, depth)


def detect_new_listings():
    """
    Prüft 3 Exchanges auf neue PERP-Listings:
    - Crypto.com (237 Perps)
    - MEXC (755 Perps — schnellste Listings!)
    - Bitget (539 Perps)

    WICHTIG: Beim ersten Lauf (kein Cache vorhanden) wird der Cache geseeded
    OHNE alles als "neu" zu melden. Nur Instrumente mit nachweislich kürzlichem
    Listing-Datum (launchTime/createTime/isNew) werden als New Listings erkannt.
    Ab dem 2. Lauf greift zusätzlich Cache-Diff für neue Symbole.

    Gibt neue PERP-Instrumente + alle aktuellen zurück.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    all_new = []
    all_perps = []

    # ── Stock-Token & Index-Filter Patterns ──
    STOCK_PATTERNS = ("STOCK_", "STOCK-", "US30_", "US30-", "HK50_", "HK50-",
                      "SP500_", "SP500-", "EU50_", "NASDAQ_", "FTSE_")

    def _is_stock_token(sym):
        return any(pat in sym.upper() for pat in STOCK_PATTERNS)

    def _listing_ts_ms(instrument):
        for field in ("onboard_date", "create_time", "launch_time"):
            try:
                value = int(instrument.get(field) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return 0

    # ── Alle 4 Exchanges abfragen ──
    exchanges = {
        "crypto.com": fetch_cryptocom_instruments,
        "mexc": fetch_mexc_futures_instruments,
        "bitget": fetch_bitget_futures_instruments,
        "binance": fetch_binance_futures_instruments,
    }

    for ex_name, fetcher in exchanges.items():
        try:
            perps = fetcher()
            if not perps:
                log.warning(f"NLS: Keine Instrumente von {ex_name}")
                continue

            all_perps.extend(perps)
            current_symbols = {p["symbol"] for p in perps}

            # Exchange-spezifischer Cache
            cache_file = CACHE_DIR / f"nls_cache_{ex_name.replace('.', '_')}.json"
            is_first_run = not cache_file.exists()
            cached_symbols = set()

            if not is_first_run:
                try:
                    cached = json.loads(cache_file.read_text())
                    cached_symbols = set(cached.get("symbols", []))
                except Exception:
                    is_first_run = True  # Korrupter Cache = wie erster Lauf

            if is_first_run:
                # ══ ERSTER LAUF: Cache seeden, NICHTS als "neu" aus Diff melden ══
                # Beim allerersten Lauf sind alle Symbole unbekannt. Wir SEEDEN
                # den Cache, damit ab dem nächsten Lauf Cache-Diff funktioniert.
                # Neue Listings werden NUR über launchTime/createTime/isNew erkannt.
                log.info(f"🌱 NLS: Erster Lauf für {ex_name} — "
                         f"seede Cache mit {len(current_symbols)} Symbolen "
                         f"(KEIN Cache-Diff, nur Timestamp-basierte Erkennung)")
            else:
                # ══ FOLGE-LAUF: Cache-Diff erkennt wirklich neue Symbole ══
                new_symbols = current_symbols - cached_symbols
                if new_symbols:
                    new_listings = [p for p in perps if p["symbol"] in new_symbols]
                    # Zusätzlicher Filter: Stock-Tokens raus
                    new_listings = [n for n in new_listings if not _is_stock_token(n["symbol"])]
                    if new_listings:
                        log.info(f"🆕 NLS: {len(new_listings)} neue Perps auf {ex_name}: "
                                 f"{', '.join(n['symbol'] for n in new_listings[:10])}")
                        all_new.extend(new_listings)

            # Cache aktualisieren (immer, auch beim ersten Lauf)
            if current_symbols:
                cache_file.write_text(json.dumps({
                    "symbols": list(current_symbols),
                    "last_update": datetime.now(timezone.utc).isoformat(),
                    "count": len(current_symbols),
                    "exchange": ex_name,
                }, indent=2))

        except Exception as e:
            log.warning(f"NLS {ex_name} Error: {e}\n{traceback.format_exc()}")

    # ── Deduplizieren (gleicher Base-Coin auf mehreren Exchanges) ──
    known_new = {n["symbol"] for n in all_new}
    max_new_listing_age_hours = float(CONFIG.get("new_listing_short_max_age_hours", CONFIG.get("monitor_hours_max", 72)))
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=max_new_listing_age_hours)).timestamp() * 1000)

    # Official exchange announcements catch listings that cache-diff/timestamps miss
    # (spot, pre-market, or futures launch pages before the perp appears).
    announcement_watchlist = fetch_listing_announcements()
    announcement_new = _attach_announcement_contracts(
        announcement_watchlist,
        all_perps,
        is_stock_token_func=_is_stock_token,
    )
    for item in announcement_new:
        sym = item.get("symbol", "")
        if not sym or sym in known_new or _is_stock_token(sym):
            continue
        all_new.append(item)
        known_new.add(sym)
        log.info(f"NLS: {sym} via {item.get('announcement_source')} announcement erkannt ({item.get('announcement_title')})")

    # ── MEXC isNew-Flag als zuverlässige Erkennung ──
    # MEXC markiert kürzlich gelistete Coins mit isNew=True
    for p in all_perps:
        if p.get("exchange") == "mexc" and p.get("is_new"):
            sym = p["symbol"]
            listing_ts = _listing_ts_ms(p)
            if listing_ts and listing_ts <= cutoff_ms:
                continue
            if sym not in known_new and not _is_stock_token(sym):
                all_new.append(p)
                known_new.add(sym)
                log.info(f"🆕 NLS: {sym} via MEXC isNew-Flag erkannt")

    # ── Bitget launchTime Erkennung ──
    # Nur Listings im aktiven Short-Fenster aufnehmen; aeltere Coins bleiben Beobachtung, keine Trade-Kandidaten.
    for p in all_perps:
        if p.get("exchange") == "bitget" and p.get("launch_time", 0) > cutoff_ms:
            sym = p["symbol"]
            if sym not in known_new and not _is_stock_token(sym):
                all_new.append(p)
                known_new.add(sym)
                lt_str = datetime.fromtimestamp(p["launch_time"] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
                log.info(f"🆕 NLS: {sym} via Bitget launchTime erkannt (gelistet {lt_str})")

    # ── MEXC createTime Erkennung ──
    # Coins im aktiven Short-Fenster
    for p in all_perps:
        if p.get("exchange") == "mexc" and p.get("create_time", 0) > cutoff_ms:
            sym = p["symbol"]
            if sym not in known_new and not _is_stock_token(sym):
                all_new.append(p)
                known_new.add(sym)
                ct_str = datetime.fromtimestamp(p["create_time"] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
                log.info(f"🆕 NLS: {sym} via MEXC createTime erkannt (gelistet {ct_str})")

    # ── Binance onboardDate Erkennung ──
    # Coins im aktiven Short-Fenster
    for p in all_perps:
        if p.get("exchange") == "binance" and p.get("onboard_date", 0) > cutoff_ms:
            sym = p["symbol"]
            if sym not in known_new and not _is_stock_token(sym):
                all_new.append(p)
                known_new.add(sym)
                ob_str = datetime.fromtimestamp(p["onboard_date"] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
                log.info(f"🆕 NLS: {sym} via Binance onboardDate erkannt (gelistet {ob_str})")

    if all_new:
        log.info(f"🆕 NLS TOTAL: {len(all_new)} neue/kürzliche Listings über alle Exchanges")
    else:
        log.info(f"📋 NLS: Keine neuen Listings erkannt (Cache-Diff + Timestamp-Check)")

    return all_new, all_perps, announcement_watchlist


# ═══════════════════════════════════════════════════════════════════════════════
# PUMP EXHAUSTION SCORING (7 Komponenten, 0-100)
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_listing_exhaustion(candles, ticker, book=None, listing_age_hours=None, is_new_listing=True):
    """
    Berechnet Pump-Exhaustion Score speziell für neue Listings.

    Komponenten:
    1. Pump Magnitude    (0-20) — Wie weit vom Startpreis gepumpt?
    2. Volume Decline     (0-20) — Sinkt das Volume? (Distribution-Signal)
    3. Momentum Decay     (0-15) — Werden die Returns pro Candle kleiner?
    4. Wick Rejection     (0-15) — Große obere Dochte = Verkäufer
    5. Price vs ATH       (0-15) — Wie nah am ATH? Höher = exhausted
    6. Spread & Depth     (0-10) — Liquidität trocknet aus?
    7. OI vs Price        (0-5)  — OI steigt während Preis stagniert = Squeeze-Risiko
    8. Funding Rate       (0-5)  — Hohe positive Funding = Longs überhitzt (Bonus)

    Returns: (score: int, details: list[str], pump_data: dict)
    """
    score = 0
    details = []

    if not candles or len(candles) < 3:
        return 0, ["[X] Zu wenige Candles (<3)"], {}

    # ── Basisdaten ──
    first_price = candles[0]["open"]
    current_price = candles[-1]["close"]
    ath = max(c["high"] for c in candles)
    atl = min(c["low"] for c in candles)
    n = len(candles)

    if first_price <= 0 or current_price <= 0 or ath <= 0:
        return 0, ["[X] Ungültige Preisdaten"], {}

    # Pump% = Höchster Punkt vs Startpreis
    # CAVE: Bei älteren Coins (>48 Candles) ist first_price nicht der Listing-Preis
    # sondern nur der Anfang des beobachteten Fensters.
    # In dem Fall ist pump_pct weniger aussagekräftig.
    pump_pct = (ath - first_price) / first_price * 100
    # Sanity Check: Wenn der Pump unrealistisch hoch ist UND der Coin schon
    # stark unter ATH handelt, war der "Pump" nur normale Volatilität im Fenster
    if n >= 48 and pump_pct > 0 and pump_pct < 20:
        # Bei langem Fenster: kleine Swings sind kein echter Pump
        pump_pct = max(0, pump_pct - 5)  # Konservativere Schätzung
    current_from_ath = (ath - current_price) / ath * 100 if ath > 0 else 0
    total_range = ath - atl if ath > atl else 0.0001
    recent_window = candles[-min(6, n):]
    prior_recent_window = recent_window[:-1] if len(recent_window) > 1 else recent_window
    recent_rejection_high = max(c["high"] for c in prior_recent_window) if prior_recent_window else ath
    last_candle = candles[-1]
    last_range = max(0.0000001, last_candle["high"] - last_candle["low"])
    last_close_pos = (last_candle["close"] - last_candle["low"]) / last_range
    last_change_pct = (
        (last_candle["close"] - last_candle["open"]) / last_candle["open"] * 100
        if last_candle["open"] > 0 else 0
    )
    recent_crack_depth_pct = (
        (recent_rejection_high - current_price) / recent_rejection_high * 100
        if recent_rejection_high > 0 else 0
    )
    prior_3_lows = [c["low"] for c in candles[-4:-1]]
    prior_3_low = min(prior_3_lows) if prior_3_lows else 0
    prior_6_high = max((c["high"] for c in candles[-7:-1]), default=ath)
    last_ath_index = max(i for i, c in enumerate(candles) if c["high"] == ath)
    bars_since_ath = max(0, n - 1 - last_ath_index)

    pump_data = {
        "first_price": first_price,
        "current_price": current_price,
        "ath": ath,
        "atl": atl,
        "pump_pct": round(pump_pct, 1),
        "from_ath_pct": round(current_from_ath, 1),
        "candle_count": n,
        "hours_tracked": n,  # 1h Candles
        "listing_age_hours": round(listing_age_hours, 1) if listing_age_hours is not None else None,
        "recent_rejection_high": round(recent_rejection_high, 8),
        "recent_crack_depth_pct": round(recent_crack_depth_pct, 2),
        "last_candle_change_pct": round(last_change_pct, 2),
        "last_close_pos": round(last_close_pos, 2),
        "prior_3_low": round(prior_3_low, 8) if prior_3_low else 0,
        "prior_3_low_broken": bool(prior_3_low and current_price < prior_3_low),
        "lower_high_confirmed": bool(last_candle["high"] < prior_6_high and current_price < recent_rejection_high),
        "bars_since_ath": bars_since_ath,
    }

    # ═══════════════════════════════════════════════════════════════════════
    # 1. PUMP MAGNITUDE (0-20)
    #    Stärkerer Pump = höhere Dump-Wahrscheinlichkeit
    #    <15% = kein Signal, 15-30% = moderat, 30-60% = stark, >60% = extrem
    # ═══════════════════════════════════════════════════════════════════════
    if pump_pct >= 100:
        pts = 20
    elif pump_pct >= 60:
        pts = 16
    elif pump_pct >= 30:
        pts = 12
    elif pump_pct >= 15:
        pts = 7
    else:
        pts = 0
    score += pts
    details.append(f"UP Pump: {pump_pct:+.0f}% vom Start → {pts}/20 Punkte")

    # ═══════════════════════════════════════════════════════════════════════
    # 2. VOLUME DECLINE (0-20)
    #    Vergleiche Volume erste Hälfte vs zweite Hälfte.
    #    Sinkend = Distribution (Smart Money verkauft)
    # ═══════════════════════════════════════════════════════════════════════
    mid = max(1, n // 2)
    vol_first = sum(c["volume_usd"] for c in candles[:mid]) / max(1, mid)
    vol_second = sum(c["volume_usd"] for c in candles[mid:]) / max(1, n - mid)

    if vol_first > 0:
        vol_ratio = vol_second / vol_first
        if vol_ratio < 0.3:      # Volume kollapiert (< 30% der ersten Hälfte)
            pts = 20
        elif vol_ratio < 0.5:    # Deutlich weniger
            pts = 15
        elif vol_ratio < 0.7:    # Leicht abnehmend
            pts = 10
        elif vol_ratio < 0.9:    # Fast gleich
            pts = 5
        else:                    # Volume steigt noch — kein Exhaustion
            pts = 0
    else:
        pts = 0
        vol_ratio = 0

    score += pts
    pump_data["vol_ratio"] = round(vol_ratio, 2)
    details.append(f" Volume Decline: {vol_ratio:.0%} der ersten Hälfte → {pts}/20")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. MOMENTUM DECAY (0-15)
    #    Vergleiche stündliche Returns: werden sie kleiner?
    #    Letzte 6 Candles vs vorherige 6
    # ═══════════════════════════════════════════════════════════════════════
    if n >= 6:
        recent_returns = []
        earlier_returns = []
        for i in range(max(1, n - 6), n):
            if candles[i - 1]["close"] > 0:
                ret = (candles[i]["close"] - candles[i - 1]["close"]) / candles[i - 1]["close"] * 100
                recent_returns.append(ret)
        for i in range(max(1, n - 12), max(1, n - 6)):
            if candles[i - 1]["close"] > 0:
                ret = (candles[i]["close"] - candles[i - 1]["close"]) / candles[i - 1]["close"] * 100
                earlier_returns.append(ret)

        avg_recent = sum(recent_returns) / len(recent_returns) if recent_returns else 0
        avg_earlier = sum(earlier_returns) / len(earlier_returns) if earlier_returns else 0

        # Momentum-Verlust: früher positiv, jetzt negativ oder flacher
        if avg_earlier > 0.5 and avg_recent < -0.5:
            pts = 15  # Klarer Momentum-Wechsel
        elif avg_earlier > 0.3 and avg_recent < 0:
            pts = 12
        elif avg_earlier > 0 and avg_recent < avg_earlier * 0.3:
            pts = 8
        elif avg_recent < avg_earlier:
            pts = 4
        else:
            pts = 0

        score += pts
        pump_data["momentum_recent"] = round(avg_recent, 3)
        pump_data["momentum_earlier"] = round(avg_earlier, 3)
        details.append(f" Momentum: {avg_earlier:+.2f}%/h → {avg_recent:+.2f}%/h → {pts}/15")
    else:
        details.append(" Momentum: zu wenig Candles (<6)")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. WICK REJECTION (0-15)
    #    Große obere Dochte = Verkäufer drücken Preis runter
    #    Durchschnittliche obere Wick-% der letzten 6 Candles
    # ═══════════════════════════════════════════════════════════════════════
    recent_candles = candles[-min(6, n):]
    upper_wicks = []
    for c in recent_candles:
        rng = c["high"] - c["low"]
        if rng > 0:
            body_top = max(c["open"], c["close"])
            uw_pct = (c["high"] - body_top) / rng * 100
            upper_wicks.append(uw_pct)

    avg_wick = sum(upper_wicks) / len(upper_wicks) if upper_wicks else 0

    if avg_wick >= 50:       # Dochte > 50% der Candle = starke Ablehnung
        pts = 15
    elif avg_wick >= 35:
        pts = 11
    elif avg_wick >= 20:
        pts = 7
    elif avg_wick >= 10:
        pts = 3
    else:
        pts = 0

    score += pts
    pump_data["avg_upper_wick_pct"] = round(avg_wick, 1)
    details.append(f" Wick Rejection: ∅{avg_wick:.0f}% obere Dochte → {pts}/15")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. PRICE vs ATH (0-15)
    #    Je näher am ATH, desto erschöpfter (Sell Pressure am Top)
    #    Aber: Preis MUSS schon gefallen sein (sonst noch im Pump)
    # ═══════════════════════════════════════════════════════════════════════
    pos_in_range = (current_price - atl) / total_range if total_range > 0 else 0.5

    # Neue Listings haben enge Ranges → pos_in_range kann misleading sein
    # Primär current_from_ath nutzen, pos_in_range als Bonus
    if current_from_ath >= 10 and pos_in_range >= 0.5:
        # Ideal: deutlich unter ATH aber noch in oberer Hälfte
        pts = 15
    elif current_from_ath >= 5 and pos_in_range >= 0.4:
        # Gut: 5-10% unter ATH, noch akzeptable Range-Position
        pts = 12
    elif current_from_ath >= 5 and pos_in_range >= 0.2:
        # OK: 5%+ unter ATH, tiefere Range — Dump läuft schon
        pts = 8
    elif current_from_ath >= 3 and pos_in_range >= 0.3:
        # Früh: gerade erst vom ATH, kleiner Rücksetzer
        pts = 6
    elif current_from_ath >= 1:
        # Sehr früh: kaum vom ATH weg
        pts = 3
    elif pos_in_range >= 0.85:
        # Noch am ATH, kaum gefallen → noch zu früh
        pts = 1
    else:
        # Kein Rücksetzer vom ATH
        pts = 0

    score += pts
    pump_data["pos_in_range"] = round(pos_in_range, 2)
    details.append(f" Position: {pos_in_range:.0%} im Range, {current_from_ath:.1f}% unter ATH → {pts}/15")

    # ═══════════════════════════════════════════════════════════════════════
    # 6. SPREAD & DEPTH (0-10)
    #    Weiter Spread + dünnes Orderbuch = Liquidität trocknet aus
    # ═══════════════════════════════════════════════════════════════════════
    pts = 0
    if ticker and ticker.get("bid") and ticker.get("ask"):
        bid = ticker["bid"]
        ask = ticker["ask"]
        mid_price = (bid + ask) / 2
        spread_pct = (ask - bid) / mid_price * 100 if mid_price > 0 else 0

        if spread_pct >= 1.5:
            pts = 7
        elif spread_pct >= 0.5:
            pts = 4
        elif spread_pct >= 0.1:
            pts = 1

        pump_data["spread_pct"] = round(spread_pct, 3)
        details.append(f" Spread: {spread_pct:.2f}% → {pts}/10")
    else:
        details.append(" Spread: keine Daten")

    if book:
        bid_depth = sum(p * q for p, q in book.get("bids", []))
        ask_depth = sum(p * q for p, q in book.get("asks", []))
        total_depth = bid_depth + ask_depth
        pump_data["book_depth_usd"] = round(total_depth, 0)

        if total_depth < 2000:
            pts += 3
        elif total_depth < 5000:
            pts += 1
        details.append(f"   Depth: ${total_depth:,.0f} → +{min(3, max(0, pts-4))}/3")

    score += min(10, pts)

    # ═══════════════════════════════════════════════════════════════════════
    # 7. OI vs PRICE DIVERGENZ (0-10)
    #    OI steigt während Preis stagniert/fällt = Longs sind trapped
    #    Höheres Gewicht weil einer der zuverlässigsten Indikatoren
    # ═══════════════════════════════════════════════════════════════════════
    if ticker and ticker.get("open_interest", 0) > 0:
        oi = ticker["open_interest"]
        if current_from_ath >= 10 and oi > 0:
            pts = 10  # Preis stark unter ATH + hohe OI = massive trapped Longs
            details.append(f"📊 OI: {oi:,.0f} bei {current_from_ath:.1f}% unter ATH → {pts}/10 (Trapped Longs!)")
        elif current_from_ath >= 5 and oi > 0:
            pts = 7
            details.append(f"📊 OI: {oi:,.0f} bei {current_from_ath:.1f}% unter ATH → {pts}/10 (Trapped Longs)")
        elif current_from_ath >= 2:
            pts = 4
            details.append(f"📊 OI: {oi:,.0f} → {pts}/10")
        else:
            pts = 0
            details.append(f"📊 OI: {oi:,.0f} (neutral)")
        score += pts
        pump_data["open_interest"] = oi
    else:
        details.append("📊 OI: keine Daten")

    # ═══════════════════════════════════════════════════════════════════════
    # 8. FUNDING RATE (0-15)
    #    DER stärkste Dump-Indikator bei neuen Listings!
    #    Hohe positive Funding = Longs überhitzt, zahlen Shorts
    #    Extrem hohe Funding (>0.1%) bei neuen Listings = fast sicherer Dump
    # ═══════════════════════════════════════════════════════════════════════
    fr = ticker.get("funding_rate", 0) if ticker else 0
    if fr and fr > 0:
        fr_pct = fr * 100  # z.B. 0.001 → 0.1%
        if fr_pct >= 0.3:      # Extrem (> 0.3% pro 8h = 3.6%/Tag Kosten!)
            pts = 15
        elif fr_pct >= 0.1:    # Sehr hoch (> 0.1%)
            pts = 12
        elif fr_pct >= 0.05:   # Überdurchschnittlich
            pts = 8
        elif fr_pct >= 0.02:   # Leicht erhöht
            pts = 4
        elif fr_pct >= 0.01:   # Leicht positiv
            pts = 2
        else:
            pts = 0
        score += pts
        pump_data["funding_rate"] = round(fr_pct, 4)
        details.append(f"💰 Funding: {fr_pct:.3f}% (Longs zahlen) → {pts}/15")
    elif fr and fr < 0:
        fr_pct = fr * 100
        # Negative Funding = Shorts zahlen → GEGEN unseren Short
        malus = 5 if fr_pct < -0.05 else 3 if fr_pct < -0.02 else 1
        score = max(0, score - malus)
        pump_data["funding_rate"] = round(fr_pct, 4)
        details.append(f"💰 Funding: {fr_pct:.3f}% (negativ! Shorts zahlen) → -{malus} Malus")
    else:
        details.append("💰 Funding: keine Daten")

    # ═══════════════════════════════════════════════════════════════════════
    # 9. LONG/SHORT RATIO (0-15)
    #    Wenn >65% der Top-Trader Long sind → einseitig positioniert
    #    = Liquidation Cascade wahrscheinlich → Dump
    #    Binance liefert topLongShortAccountRatio
    # ═══════════════════════════════════════════════════════════════════════
    ls_ratio = ticker.get("long_short_ratio", 0) if ticker else 0
    if ls_ratio and ls_ratio > 0:
        # long_short_ratio > 1 = mehr Longs als Shorts
        long_pct = (ls_ratio / (1 + ls_ratio)) * 100  # z.B. 2.5 → 71.4% Long
        if long_pct >= 80:       # Extrem einseitig → fast sicherer Dump
            pts = 15
        elif long_pct >= 72:     # Stark einseitig
            pts = 12
        elif long_pct >= 65:     # Deutlich Long-lastig
            pts = 8
        elif long_pct >= 58:     # Leicht Long-lastig
            pts = 4
        elif long_pct >= 55:     # Marginal
            pts = 2
        else:
            pts = 0
        score += pts
        pump_data["long_short_ratio"] = round(ls_ratio, 2)
        pump_data["long_pct"] = round(long_pct, 1)
        details.append(f"⚖️ L/S Ratio: {ls_ratio:.2f} ({long_pct:.0f}% Long) → {pts}/15")
    else:
        details.append("⚖️ L/S Ratio: keine Daten")

    # ═══════════════════════════════════════════════════════════════════════
    # 10. CONSECUTIVE RED CANDLES (0-10)
    #     4+ rote Candles hintereinander nach ATH = Distribution aktiv
    #     Einfach aber sehr effektiv als Bestätigung
    # ═══════════════════════════════════════════════════════════════════════
    red_streak = 0
    max_red_streak = 0
    for c in candles:
        if c["close"] < c["open"]:
            red_streak += 1
            max_red_streak = max(max_red_streak, red_streak)
        else:
            red_streak = 0

    # Aktuelle Streak (am Ende der Candle-Reihe) zählt mehr
    current_red_streak = 0
    for c in reversed(candles):
        if c["close"] < c["open"]:
            current_red_streak += 1
        else:
            break

    effective_streak = max(max_red_streak, current_red_streak)
    if effective_streak >= 6:
        pts = 10  # 6+ rote Candles = starke Distribution
    elif effective_streak >= 5:
        pts = 8
    elif effective_streak >= 4:
        pts = 6
    elif effective_streak >= 3:
        pts = 3
    else:
        pts = 0
    score += pts
    pump_data["red_streak"] = effective_streak
    pump_data["current_red_streak"] = current_red_streak
    details.append(f"🔴 Red Candles: {effective_streak} hintereinander (aktuell {current_red_streak}) → {pts}/10")

    # ═══════════════════════════════════════════════════════════════════════
    # 11. ZEIT SEIT LISTING (0-10)
    #     Neue Listings haben vorhersehbaren Lebenszyklus:
    #     0-6h: Hype-Phase (zu früh für Short)
    #     6-24h: Peak-Zone (aufpassen)
    #     24-72h: ideales Short-Fenster (Hype vorbei, Dump beginnt)
    #     >72h: späte Phase (Dump läuft oder schon durch)
    # ═══════════════════════════════════════════════════════════════════════
    if is_new_listing:
        hours = listing_age_hours if listing_age_hours is not None else pump_data.get("hours_tracked", n)
        if hours >= 24 and hours <= 72:
            pts = 10  # Sweet Spot: Hype ist vorbei, Dump-Phase
        elif hours >= 12 and hours < 24:
            pts = 7   # Noch in der Transition
        elif hours >= 6 and hours < 12:
            pts = 4   # Noch recht früh aber möglich
        elif hours > 72 and hours <= 168:
            pts = 5   # Spät aber noch relevant (1-7 Tage)
        elif hours > 168:
            pts = 2   # Sehr spät (>7 Tage)
        else:
            pts = 0   # Zu früh (<6h)
        score += pts
        pump_data["listing_age_hours"] = round(hours, 1)
        details.append(f"⏱️ Listing Alter: {hours:.1f}h → {pts}/10 ({'Sweet Spot!' if 24 <= hours <= 72 else 'Zu früh' if hours < 6 else 'Spät' if hours > 72 else ''})")
    else:
        pump_data["listing_age_hours"] = None
        details.append("⏱️ Listing Alter: aktiver Pump, kein New-Listing-Altersbonus")

    # ═══════════════════════════════════════════════════════════════════════
    # 12. BTC KORRELATION (0-10)
    #     Wenn BTC stabil/steigt aber der Coin fällt → eigenständiger Dump
    #     Stärkeres Signal als marktweiter Abverkauf
    # ═══════════════════════════════════════════════════════════════════════
    btc_divergence_pts = 0
    try:
        btc_candles = fetch_binance_candles("BTCUSDT", "1h", min(n, 24))
        if btc_candles and len(btc_candles) >= 3:
            btc_first = btc_candles[0]["open"]
            btc_last = btc_candles[-1]["close"]
            btc_change = ((btc_last - btc_first) / btc_first * 100) if btc_first > 0 else 0

            # Coin-Change über gleichen Zeitraum
            coin_recent = candles[-min(len(candles), len(btc_candles)):]
            coin_first = coin_recent[0]["open"] if coin_recent else first_price
            coin_change = ((current_price - coin_first) / coin_first * 100) if coin_first > 0 else 0

            divergence = coin_change - btc_change  # negativ = Coin underperformt BTC

            if divergence <= -15:       # Coin fällt 15%+ mehr als BTC
                btc_divergence_pts = 10
            elif divergence <= -10:
                btc_divergence_pts = 8
            elif divergence <= -5:
                btc_divergence_pts = 5
            elif divergence <= -2:
                btc_divergence_pts = 3
            else:
                btc_divergence_pts = 0

            # Bonus: BTC steigt aber Coin fällt = extrastarkes Signal
            if btc_change > 0 and coin_change < -3:
                btc_divergence_pts = min(10, btc_divergence_pts + 2)

            btc_tailwind_risk = (
                btc_change >= CONFIG["btc_tailwind_risk_change_pct"]
                and divergence > CONFIG["btc_tailwind_min_divergence_pct"]
            )
            if btc_tailwind_risk:
                btc_context = "BTC_RISK_ON_WAIT_FOR_DEEPER_CRACK"
            elif divergence <= CONFIG["btc_tailwind_min_divergence_pct"]:
                btc_context = "COIN_UNDERPERFORMS_BTC_SHORT_TAILWIND"
            else:
                btc_context = "NEUTRAL"

            pump_data["btc_change_pct"] = round(btc_change, 1)
            pump_data["coin_change_pct"] = round(coin_change, 1)
            pump_data["btc_divergence"] = round(divergence, 1)
            pump_data["btc_tailwind_risk"] = btc_tailwind_risk
            pump_data["btc_short_context"] = btc_context
            details.append(f"₿ BTC Divergenz: BTC {btc_change:+.1f}% vs Coin {coin_change:+.1f}% (Div: {divergence:+.1f}%) → {btc_divergence_pts}/10")
        else:
            pump_data["btc_short_context"] = "UNKNOWN"
            details.append("₿ BTC Divergenz: keine BTC-Daten")
    except Exception as e:
        pump_data["btc_short_context"] = "UNKNOWN"
        details.append(f"₿ BTC Divergenz: Fehler ({e})")

    score += btc_divergence_pts

    # ═══════════════════════════════════════════════════════════════════════
    # GESAMT-SCORE (max 155 Punkte → normalisiert auf 0-100)
    # ═══════════════════════════════════════════════════════════════════════
    # Komponenten: 20+20+15+15+15+10+10+15+15+10+10+10(btc_div) = 165 theoretisch
    # Spread+Depth ist min(10, pts) → max 10. Echtes Max variiert mit Daten.
    # Nutze 160 als Basis (nicht alle Komponenten liefern gleichzeitig Max)
    max_possible = 160
    normalized = int(round(score / max_possible * 100))

    pump_data["raw_score"] = score
    pump_data["max_score"] = max_possible
    details.append(f"══ GESAMT: {score}/{max_possible} Punkte → normalisiert {normalized}/100")

    return min(100, normalized), details, pump_data


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def check_safety(ticker, book, candles):
    """
    Sicherheitsprüfung bevor ein Short-Signal generiert wird.

    Returns: (is_safe: bool, warnings: list[str])
    """
    warnings = []
    is_safe = True

    if not ticker:
        return False, ["[!] Kein frischer Ticker - kein Live-Short-Signal"]

    ticker_age = _data_age_seconds(ticker.get("timestamp"))
    if ticker_age is None:
        warnings.append("[!] Ticker ohne Timestamp - Datenalter unbekannt")
        is_safe = False
    elif ticker_age > CONFIG["max_ticker_age_sec"]:
        warnings.append(f"[!] Ticker stale: {ticker_age}s alt (max {CONFIG['max_ticker_age_sec']}s)")
        is_safe = False

    if candles:
        candle_age = _data_age_seconds(candles[-1].get("timestamp"))
        if candle_age is None:
            warnings.append("[!] Letzte Candle ohne Timestamp - Datenalter unbekannt")
            is_safe = False
        elif candle_age > CONFIG["max_candle_age_sec"]:
            warnings.append(f"[!] Candle stale: {candle_age}s alt (max {CONFIG['max_candle_age_sec']}s)")
            is_safe = False
    else:
        warnings.append("[!] Keine Candles - kein Setup")
        is_safe = False

    bid = _to_float(ticker.get("bid"))
    ask = _to_float(ticker.get("ask"))
    if bid <= 0 or ask <= 0 or ask < bid:
        warnings.append("[!] Kein belastbarer Bid/Ask - Spread unbekannt")
        is_safe = False

    if not book:
        warnings.append("[!] Kein Orderbook - Liquiditaet nicht verifizierbar")
        is_safe = False

    # 1. Volume Minimum
    vol_24h = _to_float(ticker.get("volume_usd_24h"))
    if vol_24h < CONFIG["min_volume_24h_usd"]:
        warnings.append(f"[!] Volume zu niedrig: ${vol_24h:,.0f} (min ${CONFIG['min_volume_24h_usd']:,})")
        is_safe = False

    # 2. Spread Maximum
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100 if mid > 0 else 99
        if spread > CONFIG["max_spread_pct"]:
            warnings.append(f"[!] Spread zu weit: {spread:.2f}% (max {CONFIG['max_spread_pct']}%)")
            is_safe = False

    # 3. Orderbook Depth
    if book:
        bid_depth = sum(p * q for p, q in book.get("bids", []))
        ask_depth = sum(p * q for p, q in book.get("asks", []))
        min_side = min(bid_depth, ask_depth)
        if min_side < CONFIG["min_book_depth_usd"]:
            warnings.append(f"[!] Orderbuch dünn: ${min_side:,.0f}/Seite (min ${CONFIG['min_book_depth_usd']:,})")
            is_safe = False

    # 4. Candle-Anomalie: Keine Trades in letzter Stunde = tot
    if candles and len(candles) >= 2:
        last_vol = _to_float(candles[-1].get("volume_usd"))
        prev_vol = _to_float(candles[-2].get("volume_usd"))
        if last_vol == 0 and prev_vol == 0:
            warnings.append("[!] Kein Volume in letzten 2 Stunden — Coin möglicherweise tot")
            is_safe = False

    # 5. Preis-Crash Detection (Rug Pull Schutz)
    if candles and len(candles) >= 3:
        recent_drop = 0
        for i in range(-3, 0):
            candle_open = _to_float(candles[i].get("open"))
            candle_close = _to_float(candles[i].get("close"))
            if candle_open > 0:
                drop = (candle_close - candle_open) / candle_open * 100
                recent_drop += drop
        if recent_drop < -30:
            warnings.append(f"[!!] Möglicher Rug Pull: {recent_drop:.0f}% in 3 Stunden!")
            is_safe = False

    if is_safe:
        warnings.append("[OK] Alle Safety-Checks bestanden")

    return is_safe, warnings


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def _upper_wick_pct(candle):
    rng = _to_float(candle.get("high")) - _to_float(candle.get("low"))
    if rng <= 0:
        return 0.0
    body_top = max(_to_float(candle.get("open")), _to_float(candle.get("close")))
    return max(0.0, (_to_float(candle.get("high")) - body_top) / rng * 100)


def _close_position(candle):
    rng = _to_float(candle.get("high")) - _to_float(candle.get("low"))
    if rng <= 0:
        return 0.5
    return (_to_float(candle.get("close")) - _to_float(candle.get("low"))) / rng


def calculate_micro_crack_trigger(candles, pump_data=None, ticker=None, timeframe=None):
    """5m execution trigger for Pump & Dump shorts."""
    pump_data = pump_data or {}
    result = {
        "micro_trigger_ok": False,
        "micro_score": 0,
        "micro_reasons": [],
        "micro_warnings": [],
        "micro_stop_loss": 0,
        "micro_rejection_high": 0,
        "micro_from_high_pct": 0,
        "micro_rr_preview": 0,
        "micro_current_price": 0,
        "micro_timeframe": timeframe or CONFIG.get("micro_timeframe", "5m"),
    }
    tf = str(timeframe or CONFIG.get("micro_timeframe", "5m")).lower()
    result["micro_timeframe"] = tf
    if tf != "5m":
        result["micro_warnings"].append("one_minute_execution_disabled")
        return result

    min_candles = 18
    min_score = int(CONFIG["micro_min_score"])

    if not candles or len(candles) < min_candles:
        result["micro_warnings"].append("micro_not_enough_candles")
        return result

    clean = [c for c in candles if _to_float(c.get("open")) > 0 and _to_float(c.get("close")) > 0]
    if len(clean) < min_candles:
        result["micro_warnings"].append("micro_invalid_candles")
        return result

    window = clean[-min(len(clean), int(CONFIG["micro_candle_count"])):]
    last = window[-1]
    entry = _to_float(last.get("close"))
    if entry <= 0:
        result["micro_warnings"].append("micro_invalid_entry")
        return result

    highs = [_to_float(c.get("high")) for c in window]
    micro_high = max(highs)
    high_index = max(i for i, value in enumerate(highs) if value == micro_high)
    bars_since_high = len(window) - 1 - high_index
    from_high_pct = (micro_high - entry) / micro_high * 100 if micro_high > 0 else 0

    recent_before_last = window[-16:-3] if len(window) >= 16 else window[:-3]
    recent_support = min((_to_float(c.get("low")) for c in recent_before_last), default=0)
    support_break = bool(recent_support and entry < recent_support)
    prior_high_zone = window[max(0, high_index - 2):min(len(window), high_index + 4)]
    rejection_high = max((_to_float(c.get("high")) for c in prior_high_zone), default=micro_high)
    recent_high = max((_to_float(c.get("high")) for c in window[-7:-1]), default=0)
    prior_swing_high = max((_to_float(c.get("high")) for c in window[-25:-7]), default=0)
    after_high_window = window[high_index + 1:-1]
    recent_high_after_peak = max((_to_float(c.get("high")) for c in after_high_window), default=0)
    lower_high = bool(
        (prior_swing_high and recent_high and recent_high < prior_swing_high * 0.995)
        or (recent_high_after_peak and recent_high_after_peak < micro_high * 0.995)
    )
    if lower_high:
        lower_high_stop_ref = recent_high_after_peak or recent_high
        if lower_high_stop_ref > entry:
            rejection_high = min(rejection_high, lower_high_stop_ref)

    last_change_pct = (
        (_to_float(last.get("close")) - _to_float(last.get("open"))) / _to_float(last.get("open")) * 100
        if _to_float(last.get("open")) > 0 else 0
    )
    last_close_pos = _close_position(last)
    red_streak = 0
    for candle in reversed(window):
        if _to_float(candle.get("close")) < _to_float(candle.get("open")):
            red_streak += 1
        else:
            break

    recent_3 = window[-3:]
    avg_upper_wick = sum(_upper_wick_pct(c) for c in recent_3) / max(1, len(recent_3))
    avg_vol_window = window[-25:-5]
    avg_vol = sum(_to_float(c.get("volume_usd")) for c in avg_vol_window) / max(1, len(avg_vol_window))
    recent_vol = sum(_to_float(c.get("volume_usd")) for c in recent_3) / max(1, len(recent_3))
    sell_volume = recent_vol >= avg_vol * 1.15 if avg_vol > 0 else False

    pump_ref = window[-13]["open"] if len(window) >= 13 else window[0]["open"]
    micro_pump_pct = (micro_high - pump_ref) / pump_ref * 100 if pump_ref > 0 else 0
    too_early = from_high_pct < CONFIG["micro_min_crack_pct"]
    too_late = from_high_pct > CONFIG["micro_max_from_high_pct"]
    still_squeezing = last_change_pct > 1.2 and last_close_pos > 0.72 and not support_break

    score = 0
    reasons = []
    if micro_pump_pct >= 20:
        score += 20
        reasons.append("micro_pump_extreme")
    elif micro_pump_pct >= 12:
        score += 15
        reasons.append("micro_pump_strong")
    elif micro_pump_pct >= 7:
        score += 10
        reasons.append("micro_pump_visible")
    if CONFIG["micro_min_crack_pct"] <= from_high_pct <= 8:
        score += 20
        reasons.append("first_crack_not_chased")
    elif from_high_pct <= CONFIG["micro_max_from_high_pct"]:
        score += 12
        reasons.append("crack_extended_but_tradeable")
    if support_break:
        score += 20
        reasons.append("micro_support_break")
    if lower_high:
        score += 12
        reasons.append("lower_high_confirmed")
    if avg_upper_wick >= 30:
        score += 10
        reasons.append("rejection_wicks")
    if red_streak >= 2:
        score += 12
        reasons.append("red_streak")
    elif last_change_pct < 0 and last_close_pos < 0.45:
        score += 8
        reasons.append("bearish_close")
    if sell_volume:
        score += 10
        reasons.append("sell_volume_pickup")
    if bars_since_high <= 12:
        score += 8
        reasons.append("fresh_crack")

    local_stop = rejection_high * (1 + CONFIG["micro_stop_buffer_pct"] / 100)
    min_stop = entry * (1 + CONFIG["min_stop_above_entry_pct"] / 100)
    micro_stop = max(local_stop, min_stop)
    ath = _to_float(pump_data.get("ath"))
    tp1 = ath * (1 - CONFIG["tp1_from_ath_pct"] / 100) if ath > 0 else 0
    rr_preview = (entry - tp1) / (micro_stop - entry) if micro_stop > entry and tp1 > 0 else 0

    warnings = []
    if too_early:
        warnings.append("micro_too_early_no_crack")
    if too_late:
        warnings.append("micro_too_late_chased")
    if still_squeezing:
        warnings.append("micro_still_squeezing")
    if rr_preview < CONFIG["min_short_rr"]:
        warnings.append("micro_rr_too_low")

    trigger_ok = (
        score >= min_score
        and not too_early
        and not too_late
        and not still_squeezing
        and micro_stop > entry
        and rr_preview >= CONFIG["min_short_rr"]
        and (support_break or lower_high)
        and (red_streak >= 1 or avg_upper_wick >= 30 or last_change_pct < 0)
    )
    result.update({
        "micro_trigger_ok": trigger_ok,
        "micro_score": int(min(100, score)),
        "micro_reasons": reasons,
        "micro_warnings": warnings,
        "micro_stop_loss": round(micro_stop, 8),
        "micro_current_price": round(entry, 8),
        "micro_rejection_high": round(rejection_high, 8),
        "micro_from_high_pct": round(from_high_pct, 2),
        "micro_rr_preview": round(rr_preview, 2),
        "micro_support_break": support_break,
        "micro_lower_high": lower_high,
        "micro_red_streak": red_streak,
        "micro_last_close_pos": round(last_close_pos, 2),
        "micro_last_change_pct": round(last_change_pct, 2),
        "micro_avg_upper_wick_pct": round(avg_upper_wick, 1),
        "micro_sell_volume": sell_volume,
        "micro_pump_pct": round(micro_pump_pct, 2),
        "micro_bars_since_high": bars_since_high,
        "micro_timeframe": tf,
    })
    return result


def generate_short_signal(symbol, pump_data, exh_score, exh_details, safety_ok, safety_warnings):
    """
    Generiert ein Short-Signal mit Entry, Stop-Loss und Take-Profit Levels.

    Entry: Aktueller Preis (Market) oder leicht unter aktuellem Preis (Limit)
    Stop: ATH + 8% (absolutes Maximum — Schutz gegen Fake-Breakouts)
    TP1: -20% vom ATH (wahrscheinlichstes Dump-Ziel)
    TP2: -40% vom ATH (Extended Dump)
    """
    ath = pump_data.get("ath", 0)
    current = pump_data.get("current_price", 0)

    if ath <= 0 or current <= 0:
        return None

    entry = current
    hard_stop = ath * (1 + CONFIG["stop_above_ath_pct"] / 100)
    stop = hard_stop
    stop_model = "ath_hard_stop"
    rejection_high = _to_float(pump_data.get("recent_rejection_high"))
    if rejection_high > entry:
        local_stop = rejection_high * (1 + CONFIG["early_crack_stop_buffer_pct"] / 100)
        min_stop = entry * (1 + CONFIG["min_stop_above_entry_pct"] / 100)
        stop = min(hard_stop, max(local_stop, min_stop))
        stop_model = "local_rejection_stop"
    micro_stop = _to_float(pump_data.get("micro_stop_loss"))
    if pump_data.get("micro_trigger_ok") and micro_stop > entry:
        stop = min(hard_stop, micro_stop)
        stop_model = "micro_crack_stop"
    tp1 = ath * (1 - CONFIG["tp1_from_ath_pct"] / 100)
    tp2 = ath * (1 - CONFIG["tp2_from_ath_pct"] / 100)

    risk = max(0, stop - entry)
    reward1 = max(0, entry - tp1)
    reward2 = max(0, entry - tp2)
    rr1 = round(reward1 / risk, 2) if risk > 0 else 0
    rr2 = round(reward2 / risk, 2) if risk > 0 else 0
    tp1_missed = tp1 >= entry
    tp2_missed = tp2 >= entry
    rr_effective = 0 if tp2_missed else (rr2 if tp1_missed else rr1)
    risk_pct = round((stop - entry) / entry * 100, 2) if entry > 0 else 999

    from_ath = _to_float(pump_data.get("from_ath_pct"))
    pump_pct = _to_float(pump_data.get("pump_pct"))
    momentum_recent = _to_float(pump_data.get("momentum_recent"))
    current_red_streak = int(_to_float(pump_data.get("current_red_streak")))
    avg_upper_wick = _to_float(pump_data.get("avg_upper_wick_pct"))
    recent_crack_depth = _to_float(pump_data.get("recent_crack_depth_pct"))
    prior_3_low_broken = bool(pump_data.get("prior_3_low_broken"))
    lower_high_confirmed = bool(pump_data.get("lower_high_confirmed"))
    micro_trigger_ok = bool(pump_data.get("micro_trigger_ok"))
    micro_score = _to_float(pump_data.get("micro_score"))
    micro_required = bool(CONFIG.get("micro_crack_enabled"))
    micro_execution_ok = (not micro_required) or micro_trigger_ok
    btc_tailwind_risk = bool(pump_data.get("btc_tailwind_risk"))
    btc_divergence = _to_float(pump_data.get("btc_divergence"))
    listing_gate_present = "listing_source" in pump_data or "listing_age_hours" in pump_data
    listing_source = str(pump_data.get("listing_source", "") or "").lower()
    listing_age_raw = pump_data.get("listing_age_hours")
    try:
        listing_age_hours = float(listing_age_raw) if listing_age_raw is not None else None
    except (TypeError, ValueError):
        listing_age_hours = None
    min_listing_age = float(CONFIG["new_listing_short_min_age_hours"])
    max_listing_age = float(CONFIG["new_listing_short_max_age_hours"])
    is_new_listing_source = listing_source == "new_listing"
    listing_age_known = listing_age_hours is not None
    listing_info_missing = not listing_gate_present
    listing_too_early = is_new_listing_source and listing_age_known and listing_age_hours < min_listing_age
    listing_expired = is_new_listing_source and listing_age_known and listing_age_hours > max_listing_age
    listing_trade_ok = is_new_listing_source and listing_age_known and not listing_too_early and not listing_expired
    early_crack_window_ok = (
        CONFIG["min_from_ath_for_short_pct"]
        <= from_ath
        <= CONFIG["max_early_crack_from_ath_pct"]
    )

    first_crack_ok = from_ath >= CONFIG["min_from_ath_for_short_pct"]
    structural_crack_ok = first_crack_ok and (
        prior_3_low_broken
        or lower_high_confirmed
        or recent_crack_depth >= CONFIG["min_from_ath_for_short_pct"]
        or micro_trigger_ok
    )
    turn_confirmed = first_crack_ok and (
        structural_crack_ok
        or momentum_recent <= 0
        or current_red_streak >= 1
        or avg_upper_wick >= 20
    )
    continuation_risk = (
        not first_crack_ok
        or (
            momentum_recent > 0.5
            and current_red_streak == 0
            and avg_upper_wick < 20
            and not structural_crack_ok
        )
    )
    rr_ok = rr_effective >= CONFIG["min_short_rr"]
    risk_ok = risk_pct <= CONFIG["max_signal_risk_pct"]
    btc_tailwind_override = (
        from_ath >= CONFIG["btc_tailwind_min_crack_pct"]
        or btc_divergence <= CONFIG["btc_tailwind_min_divergence_pct"]
        or (micro_trigger_ok and micro_score >= 85 and recent_crack_depth >= 5)
    )
    btc_context_ok = (not btc_tailwind_risk) or btc_tailwind_override
    early_crack_ok = (
        (exh_score >= CONFIG["early_crack_entry_score"] or (micro_trigger_ok and micro_score >= CONFIG["micro_min_score"]))
        and early_crack_window_ok
        and structural_crack_ok
        and turn_confirmed
        and not continuation_risk
        and not tp1_missed
        and not tp2_missed
        and listing_trade_ok
        and btc_context_ok
    )
    exhaustion_short_ok = exh_score >= CONFIG["exh_short_entry"]
    trade_setup_ok = (
        (exhaustion_short_ok or early_crack_ok)
        and safety_ok
        and turn_confirmed
        and rr_ok
        and risk_ok
        and micro_execution_ok
        and pump_pct >= CONFIG["min_pump_pct"]
        and not continuation_risk
        and not tp1_missed
        and not tp2_missed
        and listing_trade_ok
        and btc_context_ok
    )

    risk_flags = []
    if pump_pct < CONFIG["min_pump_pct"]:
        risk_flags.append("pump_too_small")
    if not first_crack_ok:
        risk_flags.append("no_first_crack")
    if continuation_risk:
        risk_flags.append("continuation_risk")
    if not turn_confirmed:
        risk_flags.append("turn_not_confirmed")
    if not rr_ok:
        risk_flags.append("rr_too_low")
    if not risk_ok:
        risk_flags.append("risk_too_wide")
    if not safety_ok:
        risk_flags.append("safety_failed")
    if first_crack_ok and not structural_crack_ok:
        risk_flags.append("crack_structure_weak")
    if from_ath > CONFIG["max_early_crack_from_ath_pct"] and not exhaustion_short_ok:
        risk_flags.append("early_crack_window_missed")
    if exh_score < CONFIG["early_crack_entry_score"] and not micro_trigger_ok:
        risk_flags.append("early_crack_score_too_low")
    if micro_required and not micro_trigger_ok:
        risk_flags.append("micro_trigger_missing")
    if not btc_context_ok:
        risk_flags.append("btc_risk_on_wait_for_deeper_crack")
    if listing_info_missing:
        risk_flags.append("listing_info_missing")
    elif not is_new_listing_source:
        risk_flags.append("active_pump_watch_only")
    elif not listing_age_known:
        risk_flags.append("listing_age_unknown")
    elif listing_too_early:
        risk_flags.append("listing_too_early")
    elif listing_expired:
        risk_flags.append("listing_age_expired")

    if listing_info_missing:
        trade_category = "LISTING_INFO_MISSING"
    elif not is_new_listing_source:
        trade_category = "ACTIVE_PUMP_WATCH"
    elif not listing_age_known:
        trade_category = "UNKNOWN_LISTING_AGE"
    elif listing_too_early:
        trade_category = "NEW_LISTING_TOO_EARLY"
    elif listing_expired:
        trade_category = "NEW_LISTING_EXPIRED"
    elif trade_setup_ok:
        trade_category = "NEW_LISTING_DUMP"
    else:
        trade_category = "NEW_LISTING_WATCH"

    # ── Timing Score ──
    if tp2_missed:
        timing = "[X] ZU SPÄT — TP-Zonen bereits verpasst"
        timing_quality = 0
    elif tp1_missed:
        timing = "[~] TP1 verpasst — nur noch Extended-Dump möglich"
        timing_quality = 2 if safety_ok and exh_score >= CONFIG["exh_watch"] else 1
    elif listing_info_missing:
        timing = "[~] BEOBACHTEN - Listing-Kontext fehlt, keine Short-Mail"
        timing_quality = 2 if exh_score >= CONFIG["exh_watch"] or early_crack_window_ok else 1
    elif not btc_context_ok:
        timing = "[~] BEOBACHTEN - BTC risk-on, erst klare Underperformance/deeper crack abwarten"
        timing_quality = 2 if exh_score >= CONFIG["exh_watch"] or early_crack_window_ok else 1
    elif not is_new_listing_source:
        timing = "[~] ACTIVE PUMP BEOBACHTEN - kein New Listing, keine Short-Mail"
        timing_quality = 2 if exh_score >= CONFIG["exh_watch"] or early_crack_window_ok else 1
    elif not listing_age_known:
        timing = "[~] BEOBACHTEN - Listing-Alter unklar, keine Short-Mail"
        timing_quality = 2 if exh_score >= CONFIG["exh_watch"] or early_crack_window_ok else 1
    elif listing_too_early:
        timing = "[~] BEOBACHTEN - neues Listing noch zu frueh fuer Short"
        timing_quality = 2 if exh_score >= CONFIG["exh_watch"] or early_crack_window_ok else 1
    elif listing_expired:
        timing = "[~] BEOBACHTEN - Listing-Fenster abgelaufen"
        timing_quality = 2 if exh_score >= CONFIG["exh_watch"] or early_crack_window_ok else 1
    elif trade_setup_ok:
        if early_crack_ok and not exhaustion_short_ok:
            timing = "[-] JETZT SHORTEN — Early Crack/Rejection"
            timing_quality = 4
        else:
            timing = "[-] JETZT SHORTEN"
            timing_quality = 5
    elif micro_required and not micro_trigger_ok and (exh_score >= CONFIG["early_crack_entry_score"] or early_crack_window_ok):
        timing = "[~] BEOBACHTEN - Micro-Crack fehlt"
        timing_quality = 2
    elif exh_score >= CONFIG["exh_short_entry"] and continuation_risk:
        timing = "[~] BEOBACHTEN - Pump laeuft noch, erst Crack/Rejection abwarten"
        timing_quality = 2
    elif exh_score >= CONFIG["exh_short_entry"] and (not rr_ok or not risk_ok):
        timing = "[~] BEOBACHTEN - R:R/Risiko noch nicht sauber"
        timing_quality = 2
    elif exh_score >= CONFIG["exh_short_entry"] and not turn_confirmed:
        timing = "[~] BEOBACHTEN - Umkehr noch nicht bestaetigt"
        timing_quality = 2
    elif exh_score >= CONFIG["exh_short_entry"] and not safety_ok:
        timing = "[~] SIGNAL aber Liquiditäts-Risiko"
        timing_quality = 3
    elif exh_score >= CONFIG["exh_watch"] or early_crack_window_ok:
        timing = "[+] BEOBACHTEN - noch nicht reif"
        timing_quality = 2
    else:
        timing = "[o] Kein Signal — Pump noch aktiv"
        timing_quality = 0

    # ── Grading ──
    if tp2_missed:
        grade = "D"
        grade_label = "[X] D â€” NO TRADE"
    elif trade_setup_ok and rr_effective >= 2.0 and (exh_score >= 80 or (early_crack_ok and exh_score >= 60)):
        grade = "S"
        grade_label = " S — ELITE SHORT"
    elif trade_setup_ok and (exhaustion_short_ok or early_crack_ok):
        grade = "A"
        grade_label = " A — EARLY CRACK SHORT" if early_crack_ok and not exhaustion_short_ok else " A — STRONG SHORT"
    elif exh_score >= 50 and rr_effective >= 1.0:
        grade = "B"
        grade_label = " B — MODERATE"
    elif exh_score >= 40:
        grade = "C"
        grade_label = " C — WEAK"
    else:
        grade = "D"
        grade_label = "[X] D — NO TRADE"

    tradeability_probe = {
        "direction": "SHORT",
        "timing_quality": timing_quality,
        "grade": grade,
        "safety_ok": safety_ok,
        "confirmation_ok": turn_confirmed,
        "continuation_risk": continuation_risk,
        "tp1_missed": tp1_missed,
        "tp2_missed": tp2_missed,
        "micro_required": micro_required,
        "micro_trigger_ok": micro_trigger_ok,
        "btc_context_ok": btc_context_ok,
        "listing_trade_ok": listing_trade_ok,
        "rr_effective": rr_effective,
        "risk_pct": risk_pct,
    }
    is_tradeable = _is_tradeable_short_signal(tradeability_probe)
    if is_tradeable:
        trade_signal = "JETZT_TRADEN"
        signal_label = "Jetzt shorten"
    elif tp1_missed or tp2_missed or risk_pct > CONFIG["max_signal_risk_pct"]:
        trade_signal = "NICHT_TRADEN"
        signal_label = "Nicht traden"
    elif not btc_context_ok or listing_too_early:
        trade_signal = "WARTEN"
        signal_label = "Warten"
    else:
        trade_signal = "BEOBACHTEN"
        signal_label = "Achtung beobachten"

    return {
        "symbol": symbol,
        "direction": "SHORT",
        "entry": round(entry, 6),
        "stop_loss": round(stop, 6),
        "hard_stop_loss": round(hard_stop, 6),
        "stop_model": stop_model,
        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "rr1": rr1,
        "rr2": rr2,
        "rr_effective": rr_effective,
        "tp1_missed": tp1_missed,
        "tp2_missed": tp2_missed,
        "risk_pct": risk_pct,
        "exh_score": exh_score,
        "timing": timing,
        "timing_quality": timing_quality,
        "grade": grade,
        "grade_label": grade_label,
        "confirmation_ok": turn_confirmed,
        "structural_crack_ok": structural_crack_ok,
        "early_crack_ok": early_crack_ok,
        "continuation_risk": continuation_risk,
        "first_crack_ok": first_crack_ok,
        "micro_required": micro_required,
        "micro_trigger_ok": micro_trigger_ok,
        "micro_score": micro_score,
        "btc_tailwind_risk": btc_tailwind_risk,
        "btc_context_ok": btc_context_ok,
        "btc_short_context": pump_data.get("btc_short_context", "UNKNOWN"),
        "btc_change_pct": pump_data.get("btc_change_pct"),
        "coin_change_pct": pump_data.get("coin_change_pct"),
        "btc_divergence": pump_data.get("btc_divergence"),
        "setup_type": (
            "early_crack" if early_crack_ok and not exhaustion_short_ok
            else "exhaustion_short" if exhaustion_short_ok
            else "watch"
        ),
        "risk_flags": risk_flags,
        "signal_quality": "tradeable" if is_tradeable else "watch_or_blocked",
        "trade_signal": trade_signal,
        "trade_action": "SHORT_NOW" if is_tradeable else trade_signal,
        "signal_label": signal_label,
        "safety_ok": safety_ok,
        "safety_warnings": safety_warnings,
        "pump_data": pump_data,
        "listing_source": listing_source,
        "listing_age_hours": round(listing_age_hours, 1) if listing_age_hours is not None else None,
        "listing_age_source": pump_data.get("listing_age_source"),
        "listing_trade_ok": listing_trade_ok,
        "trade_category": trade_category,
        "exh_details": exh_details,
        "max_leverage": CONFIG["max_leverage"],
        "max_position_hours": CONFIG["max_position_hours"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MONITORING MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

def load_monitoring_list():
    """Lädt die Liste der aktuell überwachten Listings."""
    if MONITORING_FILE.exists():
        try:
            return json.loads(MONITORING_FILE.read_text())
        except Exception:
            pass
    return {}


def save_monitoring_list(monitoring):
    """Speichert die Monitoring-Liste."""
    MONITORING_FILE.write_text(json.dumps(monitoring, indent=2, default=str))


def add_to_monitoring(symbol, exchange="crypto.com", listing_ts_ms=None, source="new_listing"):
    """Fügt ein neues Listing zur Überwachung hinzu."""
    monitoring = load_monitoring_list()
    key = _monitor_key(symbol, exchange)
    if symbol in monitoring and key not in monitoring:
        monitoring[key] = monitoring.pop(symbol)
        monitoring[key]["symbol"] = symbol
        monitoring[key]["exchange"] = exchange
        save_monitoring_list(monitoring)
    if key not in monitoring:
        listing_time = None
        if listing_ts_ms:
            try:
                listing_time = datetime.fromtimestamp(int(listing_ts_ms) / 1000, tz=timezone.utc).isoformat()
            except Exception:
                listing_time = None
        monitoring[key] = {
            "symbol": symbol,
            "exchange": exchange,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "listing_time": listing_time,
            "source": source,
            "status": "monitoring",  # monitoring | signal | expired
            "last_exh_score": 0,
            "peak_exh_score": 0,
        }
        save_monitoring_list(monitoring)
        log.info(f"🆕 NLS: {symbol} zur Überwachung hinzugefügt")
    return monitoring


def cleanup_monitoring(monitoring):
    """Entfernt abgelaufene Einträge. Neue Listings: 72h, Pumps: 48h."""
    now = datetime.now(timezone.utc)
    cutoff_listing = now - timedelta(hours=CONFIG["monitor_hours_max"])
    cutoff_pump = now - timedelta(hours=48)  # Pumps dumpen schneller
    to_remove = []
    for sym, data in monitoring.items():
        try:
            is_pump = data.get("source") == "pump_detection"
            if is_pump:
                age_basis = data.get("detected_at")
                cutoff = cutoff_pump
            else:
                # Exchange listing time is stricter than local detection time. This keeps
                # 3-14 day old "recent" exchange rows from clogging the short scanner.
                age_basis = data.get("listing_time") or data.get("detected_at")
                cutoff = cutoff_listing
            detected = datetime.fromisoformat(str(age_basis).replace("Z", "+00:00"))
            if detected < cutoff:
                to_remove.append(sym)
        except Exception:
            pass
    for sym in to_remove:
        monitoring[sym]["status"] = "expired"
        source = monitoring[sym].get("source", "new_listing")
        log.info(f"⏰ P&D: {sym} ({source}) — Monitoring abgelaufen")
    return monitoring


# ═══════════════════════════════════════════════════════════════════════════════
# PUMP DETECTION — Zweiter Erkennungsweg neben neue Listings
# Scannt ALLE Perps auf extreme Pump-Bedingungen und fügt sie zum Monitoring hinzu
# ═══════════════════════════════════════════════════════════════════════════════

def detect_active_pumps(all_perps):
    """
    Scannt alle bekannten Perps auf aktive Pump & Dump Bedingungen.
    Nutzt Ticker-Daten (24h Change, Volume, Funding) um extreme Pumps zu erkennen.

    Kriterien für Pump-Erkennung:
    - 24h Change > 25%  ODER
    - 24h Change > 15% UND Funding < -0.05%  ODER
    - 24h Change > 15% UND Volume extrem hoch

    Returns: Liste von {symbol, exchange, pump_reason, ticker_data}
    """
    monitoring = load_monitoring_list()
    already_monitored = set(monitoring.keys())
    detected_pumps = []

    # Gruppiere Perps nach Exchange für Batch-Processing
    exchange_perps = {}
    for p in all_perps:
        ex = p.get("exchange", "")
        if ex not in exchange_perps:
            exchange_perps[ex] = []
        exchange_perps[ex].append(p)

    # Schnell-Scan: Nutze Bulk-Ticker-APIs (1 Call pro Exchange statt pro Coin)
    exchange_tickers = {}

    # MEXC: Bulk-Ticker (alle Futures in einem Call)
    try:
        resp = req.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("data"):
                for t in data["data"]:
                    sym = t.get("symbol", "")
                    if not sym.endswith("_USDT"):
                        continue
                    change = float(t.get("riseFallRate", 0)) * 100
                    volume = float(t.get("amount24", 0))
                    fr = float(t.get("fundingRate", 0))
                    last_price = float(t.get("lastPrice", 0))
                    exchange_tickers[f"mexc:{sym}"] = {
                        "symbol": sym,
                        "exchange": "mexc",
                        "change_24h": change,
                        "volume_usd_24h": volume,
                        "funding_rate": fr,
                        "price": last_price,
                    }
    except Exception as e:
        log.warning(f"Pump-Detection MEXC Error: {e}")

    # Bitget: Bulk-Ticker
    try:
        resp = req.get("https://api.bitget.com/api/v2/mix/market/tickers",
                       params={"productType": "USDT-FUTURES"}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "00000" and data.get("data"):
                for t in data["data"]:
                    sym = t.get("symbol", "")
                    if not sym.endswith("USDT"):
                        continue
                    change = float(t.get("change24h", 0)) * 100
                    volume = float(t.get("usdtVolume", 0))
                    fr = float(t.get("fundingRate", 0))
                    last_price = float(t.get("lastPr", 0))
                    exchange_tickers[f"bitget:{sym}"] = {
                        "symbol": sym,
                        "exchange": "bitget",
                        "change_24h": change,
                        "volume_usd_24h": volume,
                        "funding_rate": fr,
                        "price": last_price,
                    }
    except Exception as e:
        log.warning(f"Pump-Detection Bitget Error: {e}")

    # Binance: Bulk-Ticker
    try:
        resp = req.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for t in data:
                    sym = t.get("symbol", "")
                    if not sym.endswith("USDT"):
                        continue
                    change = float(t.get("priceChangePercent", 0))
                    volume = float(t.get("quoteVolume", 0))
                    last_price = float(t.get("lastPrice", 0))
                    exchange_tickers[f"binance:{sym}"] = {
                        "symbol": sym,
                        "exchange": "binance",
                        "change_24h": change,
                        "volume_usd_24h": volume,
                        "funding_rate": 0,  # Binance braucht separaten Call
                        "price": last_price,
                    }
    except Exception as e:
        log.warning(f"Pump-Detection Binance Error: {e}")

    log.info(f"🔍 Pump-Detection: {len(exchange_tickers)} Perp-Ticker geladen")

    # Filter auf extreme Pumps
    for key, ticker in exchange_tickers.items():
        change = ticker["change_24h"]
        volume = ticker["volume_usd_24h"]
        fr = ticker["funding_rate"]
        fr_pct = fr * 100
        sym = ticker["symbol"]
        exchange = ticker["exchange"]
        monitor_key = _monitor_key(sym, exchange)

        # Skip wenn schon überwacht
        if monitor_key in already_monitored or sym in already_monitored:
            continue

        # === PUMP-KRITERIEN ===
        pump_reasons = []

        # Kriterium 1: Extremer Pump (>25% in 24h)
        if change > 25:
            pump_reasons.append(f"24h +{change:.0f}% (extremer Pump)")

        # Kriterium 2: Starker Pump + positive Funding = ueberhitzte Longs.
        # Negative Funding ist fuer Shorts eher Squeeze-Risiko, kein Trigger.
        if change > 15 and fr_pct > 0.05:
            pump_reasons.append(f"24h +{change:.0f}% + FR {fr_pct:.3f}% (Longs ueberhitzt)")

        # Kriterium 3: Starker Pump + extremes Volume
        if change > 15 and volume > 50_000_000:
            pump_reasons.append(f"24h +{change:.0f}% + Vol ${volume/1e6:.0f}M (Hype-Volume)")

        if not pump_reasons:
            continue

        # Doppel-Check: Nicht shorten wenn Pump schon vorbei (negative 24h = schon gedumpt)
        if change < 0:
            continue

        detected_pumps.append({
            "symbol": sym,
            "exchange": ticker["exchange"],
            "pump_reasons": pump_reasons,
            "change_24h": change,
            "volume_usd_24h": volume,
            "funding_rate": fr,
            "negative_funding_squeeze_risk": fr_pct < -0.05,
            "price": ticker["price"],
            "source": "pump_detection",
        })

    # Sortiere nach Change (stärkste Pumps zuerst)
    detected_pumps.sort(key=lambda x: x["change_24h"], reverse=True)

    # Max 15 Pumps zur Überwachung hinzufügen (Rate-Limiting beachten)
    added = 0
    for pump in detected_pumps[:15]:
        sym = pump["symbol"]
        key = _monitor_key(sym, pump["exchange"])
        if key not in already_monitored and sym not in already_monitored:
            add_to_monitoring(sym, pump["exchange"], source="pump_detection")
            # Markiere als pump-detected (nicht new-listing)
            monitoring = load_monitoring_list()
            if key in monitoring:
                monitoring[key]["source"] = "pump_detection"
                monitoring[key]["pump_reasons"] = pump["pump_reasons"]
                monitoring[key]["change_24h_detected"] = pump["change_24h"]
                monitoring[key]["negative_funding_squeeze_risk"] = pump.get("negative_funding_squeeze_risk", False)
                save_monitoring_list(monitoring)
            added += 1
            log.info(f"🔥 PUMP DETECTED: {sym} auf {pump['exchange']} — "
                     f"{', '.join(pump['pump_reasons'])}")

    log.info(f"🔥 Pump-Detection: {len(detected_pumps)} Pumps erkannt, {added} neu zum Monitoring")
    return detected_pumps


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPTFUNKTION (wird von bg_service.py aufgerufen)
# ═══════════════════════════════════════════════════════════════════════════════

def run_new_listing_scanner():
    """
    Pump & Dump Scanner (ehemals New Listing Scanner).

    ZWEI Erkennungswege:
    1. Neue PERP-Listings erkennen (Cache-Diff, launchTime, isNew)
    2. Aktive Pumps erkennen (24h Change + Funding + Volume)

    Pipeline danach identisch:
    - Candles + Ticker holen
    - Exhaustion Score berechnen
    - Safety prüfen
    - Short-Signal generieren
    - Ergebnisse als JSON speichern

    Returns: dict mit Ergebnissen
    """
    log.info("🔥 === Pump & Dump Scanner gestartet ===")
    start_time = time.time()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_listings_detected": [],
        "pumps_detected": [],
        "announcement_watchlist": [],
        "signals": [],
        "watchlist": [],
        "monitoring": [],
        "errors": [],
    }

    try:
        # ── Phase 1a: Neue Listings erkennen ──
        detected = detect_new_listings()
        if len(detected) == 3:
            new_listings, all_perps, announcement_watchlist = detected
        else:
            new_listings, all_perps = detected
            announcement_watchlist = []
        results["announcement_watchlist"] = announcement_watchlist[:30]
        for nl in new_listings:
            results["new_listings_detected"].append(nl["symbol"])
            listing_ts = (
                nl.get("onboard_date")
                or nl.get("create_time")
                or nl.get("launch_time")
                or nl.get("announcement_release_ms")
            )
            monitoring = add_to_monitoring(nl["symbol"], nl.get("exchange", "crypto.com"), listing_ts_ms=listing_ts, source="new_listing")
            key = _monitor_key(nl["symbol"], nl.get("exchange", "crypto.com"))
            if key in monitoring and nl.get("announcement_source"):
                announcement_listing_time = None
                try:
                    if nl.get("announcement_release_ms"):
                        announcement_listing_time = datetime.fromtimestamp(
                            int(nl.get("announcement_release_ms")) / 1000,
                            tz=timezone.utc,
                        ).isoformat()
                except Exception:
                    announcement_listing_time = None
                monitoring[key].update({
                    "source": "new_listing",
                    "status": "monitoring",
                    "listing_time": announcement_listing_time or monitoring[key].get("listing_time"),
                    "listing_detection": "exchange_announcement",
                    "listing_age_source_override": "announcement_time",
                    "announcement_source": nl.get("announcement_source"),
                    "announcement_exchange": nl.get("announcement_exchange"),
                    "announcement_title": nl.get("announcement_title"),
                    "announcement_url": nl.get("announcement_url"),
                    "announcement_release_ms": nl.get("announcement_release_ms"),
                    "announcement_base": nl.get("announcement_base"),
                    "contract_confirmed": bool(nl.get("contract_confirmed")),
                    "tradable_contract_confirmed": bool(nl.get("tradable_contract_confirmed")),
                    "contract_confirmation": nl.get("contract_confirmation"),
                })
                save_monitoring_list(monitoring)

        # ── Phase 1b: Aktive Pumps erkennen (NEUER Erkennungsweg) ──
        try:
            active_pumps = detect_active_pumps(all_perps)
            for pump in active_pumps:
                results["pumps_detected"].append({
                    "symbol": pump["symbol"],
                    "exchange": pump["exchange"],
                    "change_24h": pump["change_24h"],
                    "reasons": pump["pump_reasons"],
                })
        except Exception as e:
            log.warning(f"Pump-Detection Error: {e}\n{traceback.format_exc()}")
            results["errors"].append(f"Pump-Detection: {str(e)}")

        # ── Phase 2: Alle überwachten Listings analysieren ──
        monitoring = load_monitoring_list()
        monitoring = cleanup_monitoring(monitoring)

        active = {k: v for k, v in monitoring.items()
                  if v.get("status") in ("monitoring", "waiting_for_history")}

        log.info(f"🔥 P&D: {len(active)} Coins in Überwachung, "
                 f"{len(new_listings)} neue Listings, "
                 f"{len(results.get('pumps_detected', []))} Pumps erkannt, "
                 f"{len(all_perps)} PERP-Instrumente total")

        for mon_key, mon_data in active.items():
            try:
                time.sleep(0.5)  # Rate Limiting
                symbol = mon_data.get("symbol") or str(mon_key).split(":", 1)[-1]
                exchange = mon_data.get("exchange", "crypto.com")

                # Daten holen (Multi-Exchange Adapter)
                ticker = fetch_ticker_for(symbol, exchange)
                if not ticker:
                    continue

                candles = fetch_candles_for(symbol, exchange, "1h", 50)
                time.sleep(0.3)

                # Orderbook nur für Crypto.com (MEXC/Bitget haben kein öffentliches Depth-API)
                book = fetch_orderbook_for(symbol, exchange, 20)
                if book and book.get("bids") and book.get("asks"):
                    ticker["bid"] = book["bids"][0][0]
                    ticker["ask"] = book["asks"][0][0]

                listing_age_hours = None
                listing_age_source = None
                source = mon_data.get("source", "new_listing")
                is_new_source = source == "new_listing"
                if is_new_source:
                    age_basis = mon_data.get("listing_time") or mon_data.get("detected_at")
                    listing_age_source = (
                        mon_data.get("listing_age_source_override")
                        or ("exchange_timestamp" if mon_data.get("listing_time") else "detected_at")
                    )
                    try:
                        age_dt = datetime.fromisoformat(str(age_basis).replace("Z", "+00:00"))
                        listing_age_hours = max(0, (datetime.now(timezone.utc) - age_dt).total_seconds() / 3600)
                    except Exception:
                        listing_age_hours = None

                # Candle-Mindestanzahl prüfen
                if not candles or len(candles) < 3:
                    log.info(f"⏳ NLS: {symbol} — nur {len(candles) if candles else 0} Candles, warte auf History")
                    mon_data["status"] = "waiting_for_history"
                    mon_data["listing_age_hours"] = round(listing_age_hours, 1) if listing_age_hours is not None else None
                    mon_data["listing_age_source"] = listing_age_source
                    mon_data["listing_trade_ok"] = False
                    mon_data["trade_category"] = "WAITING_FOR_HISTORY"
                    results["monitoring"].append({
                        "symbol": symbol,
                        "exchange": exchange,
                        "source": source,
                        "listing_age_hours": round(listing_age_hours, 1) if listing_age_hours is not None else None,
                        "listing_age_source": listing_age_source,
                        "listing_trade_ok": False,
                        "trade_category": "WAITING_FOR_HISTORY",
                        "grade": "WAIT",
                        "timing": "Waiting for history",
                        "risk_flags": ["waiting_for_history"],
                        "announcement_source": mon_data.get("announcement_source"),
                        "announcement_title": mon_data.get("announcement_title"),
                        "announcement_url": mon_data.get("announcement_url"),
                    })
                    continue
                if mon_data.get("status") == "waiting_for_history":
                    mon_data["status"] = "monitoring"

                # Exhaustion Score berechnen
                exh_score, exh_details, pump_data = calculate_listing_exhaustion(
                    candles, ticker, book,
                    listing_age_hours=listing_age_hours,
                    is_new_listing=is_new_source,
                )
                pump_data["listing_source"] = source
                pump_data["listing_age_hours"] = round(listing_age_hours, 1) if listing_age_hours is not None else None
                pump_data["listing_age_source"] = listing_age_source
                pump_data["is_new_listing"] = is_new_source

                # Safety prüfen
                safety_ok, safety_warnings = check_safety(ticker, book, candles)

                if CONFIG.get("micro_crack_enabled") and pump_data.get("pump_pct", 0) >= CONFIG["min_pump_pct"]:
                    micro_tf = CONFIG["micro_timeframe"]
                    micro_candles = fetch_candles_for(
                        symbol,
                        exchange,
                        micro_tf,
                        CONFIG["micro_candle_count"],
                    )
                    micro_result = calculate_micro_crack_trigger(micro_candles, pump_data, ticker, timeframe=micro_tf)

                    # Fresh listings can move quickly, but execution alerts still require
                    # a completed 5m structure to reduce noise and false starts.
                    use_ultra = (
                        CONFIG.get("ultra_micro_enabled")
                        and is_new_source
                        and listing_age_hours is not None
                        and listing_age_hours <= CONFIG.get("ultra_micro_max_age_hours", 6.0)
                        and not micro_result.get("micro_trigger_ok")
                    )
                    if use_ultra:
                        ultra_tf = CONFIG.get("ultra_micro_timeframe", "1m")
                        ultra_candles = fetch_candles_for(
                            symbol,
                            exchange,
                            ultra_tf,
                            CONFIG.get("ultra_micro_candle_count", 45),
                        )
                        ultra_result = calculate_micro_crack_trigger(ultra_candles, pump_data, ticker, timeframe=ultra_tf)
                        if ultra_result.get("micro_trigger_ok") or ultra_result.get("micro_score", 0) > micro_result.get("micro_score", 0):
                            ultra_result["ultra_early_trigger"] = bool(ultra_result.get("micro_trigger_ok"))
                            micro_result = ultra_result

                    pump_data.update(micro_result)
                    micro_price = _to_float(pump_data.get("micro_current_price"))
                    ath_price = _to_float(pump_data.get("ath"))
                    if micro_price > 0:
                        pump_data["current_price"] = micro_price
                        if ath_price > 0:
                            pump_data["from_ath_pct"] = round((ath_price - micro_price) / ath_price * 100, 1)

                # Monitoring-Status aktualisieren
                mon_data["last_exh_score"] = exh_score
                mon_data["peak_exh_score"] = max(mon_data.get("peak_exh_score", 0), exh_score)
                mon_data["last_check"] = datetime.now(timezone.utc).isoformat()
                mon_data["pump_pct"] = pump_data.get("pump_pct", 0)
                mon_data["from_ath_pct"] = pump_data.get("from_ath_pct", 0)
                mon_data["safety_ok"] = safety_ok
                mon_data["safety_warnings"] = safety_warnings[:5]

                # ── Already-Dumped Filter ──
                # Wenn Preis schon >40% unter ATH ist, shorten wir NICHT (falling knife)
                from_ath = pump_data.get("from_ath_pct", 0)
                if from_ath > 40:
                    log.info(f" NLS: {symbol} übersprungen — bereits {from_ath:.0f}% unter ATH (falling knife)")
                    mon_data["status"] = "expired_dumped"
                    mon_data["listing_age_hours"] = round(listing_age_hours, 1) if listing_age_hours is not None else None
                    mon_data["listing_age_source"] = listing_age_source
                    mon_data["listing_trade_ok"] = False
                    mon_data["trade_category"] = "ALREADY_DUMPED"
                    mon_data["btc_change_pct"] = pump_data.get("btc_change_pct")
                    mon_data["coin_change_pct"] = pump_data.get("coin_change_pct")
                    mon_data["btc_divergence"] = pump_data.get("btc_divergence")
                    mon_data["btc_short_context"] = pump_data.get("btc_short_context", "UNKNOWN")
                    mon_data["btc_tailwind_risk"] = pump_data.get("btc_tailwind_risk", False)
                    results["monitoring"].append({
                        "symbol": symbol,
                        "source": source,
                        "listing_age_hours": round(listing_age_hours, 1) if listing_age_hours is not None else None,
                        "listing_age_source": listing_age_source,
                        "listing_trade_ok": False,
                        "trade_category": "ALREADY_DUMPED",
                        "btc_change_pct": pump_data.get("btc_change_pct"),
                        "coin_change_pct": pump_data.get("coin_change_pct"),
                        "btc_divergence": pump_data.get("btc_divergence"),
                        "btc_short_context": pump_data.get("btc_short_context", "UNKNOWN"),
                        "btc_tailwind_risk": pump_data.get("btc_tailwind_risk", False),
                        "exh_score": exh_score,
                        "pump_pct": pump_data.get("pump_pct", 0),
                        "from_ath_pct": from_ath,
                        "volume_ratio": pump_data.get("vol_ratio", 0),
                        "safety_ok": safety_ok,
                        "grade": "SKIP",
                        "timing": " Already dumped",
                        "hours_tracked": pump_data.get("hours_tracked", 0),
                        "risk_flags": ["already_dumped"],
                        "announcement_source": mon_data.get("announcement_source"),
                        "announcement_title": mon_data.get("announcement_title"),
                        "announcement_url": mon_data.get("announcement_url"),
                    })
                    continue

                # Signal generieren
                signal = generate_short_signal(
                    symbol, pump_data, exh_score, exh_details,
                    safety_ok, safety_warnings
                )

                if not signal:
                    continue

                entry = {
                    "symbol": symbol,
                    "exchange": mon_data.get("exchange", "crypto.com"),
                    "detected_at": mon_data.get("detected_at", ""),
                    "listing_source": source,
                    "listing_age_hours": signal.get("listing_age_hours"),
                    "listing_age_source": listing_age_source,
                    "listing_trade_ok": signal.get("listing_trade_ok", False),
                    "trade_category": signal.get("trade_category", "UNKNOWN"),
                    "announcement_source": mon_data.get("announcement_source"),
                    "announcement_title": mon_data.get("announcement_title"),
                    "announcement_url": mon_data.get("announcement_url"),
                    "signal": signal,
                }

                mon_data["rr_effective"] = signal.get("rr_effective", 0)
                mon_data["risk_pct"] = signal.get("risk_pct", 0)
                mon_data["confirmation_ok"] = signal.get("confirmation_ok", False)
                mon_data["continuation_risk"] = signal.get("continuation_risk", False)
                mon_data["signal_quality"] = signal.get("signal_quality", "watch_or_blocked")
                mon_data["risk_flags"] = signal.get("risk_flags", [])
                mon_data["setup_type"] = signal.get("setup_type", "watch")
                mon_data["stop_model"] = signal.get("stop_model", "")
                mon_data["micro_trigger_ok"] = pump_data.get("micro_trigger_ok", False)
                mon_data["micro_score"] = pump_data.get("micro_score", 0)
                mon_data["btc_change_pct"] = pump_data.get("btc_change_pct")
                mon_data["coin_change_pct"] = pump_data.get("coin_change_pct")
                mon_data["btc_divergence"] = pump_data.get("btc_divergence")
                mon_data["btc_short_context"] = pump_data.get("btc_short_context", "UNKNOWN")
                mon_data["btc_tailwind_risk"] = pump_data.get("btc_tailwind_risk", False)
                mon_data["listing_age_hours"] = signal.get("listing_age_hours")
                mon_data["listing_age_source"] = listing_age_source
                mon_data["listing_trade_ok"] = signal.get("listing_trade_ok", False)
                mon_data["trade_category"] = signal.get("trade_category", "UNKNOWN")

                if _is_tradeable_short_signal(signal):
                    results["signals"].append(entry)
                    mon_data["status"] = "signal"
                    log.info(f"[-] NLS SHORT SIGNAL: {symbol} — ExhScore {exh_score}, "
                             f"Pump {pump_data.get('pump_pct', 0):.0f}%, "
                             f"RR {signal['rr_effective']:.1f}x, Grade {signal['grade']}")
                elif signal["timing_quality"] >= 2:
                    results["watchlist"].append(entry)

                results["monitoring"].append({
                    "symbol": symbol,
                    "exh_score": exh_score,
                    "pump_pct": pump_data.get("pump_pct", 0),
                    "from_ath_pct": pump_data.get("from_ath_pct", 0),
                    "volume_ratio": pump_data.get("vol_ratio", 0),
                    "safety_ok": safety_ok,
                    "safety_warnings": safety_warnings[:5],
                    "grade": signal.get("grade", "?"),
                    "timing": signal.get("timing", "?"),
                    "rr_effective": signal.get("rr_effective", 0),
                    "risk_pct": signal.get("risk_pct", 0),
                    "confirmation_ok": signal.get("confirmation_ok", False),
                    "continuation_risk": signal.get("continuation_risk", False),
                    "signal_quality": signal.get("signal_quality", "watch_or_blocked"),
                    "risk_flags": signal.get("risk_flags", []),
                    "setup_type": signal.get("setup_type", "watch"),
                    "stop_model": signal.get("stop_model", ""),
                    "stop_loss": signal.get("stop_loss", 0),
                    "hard_stop_loss": signal.get("hard_stop_loss", 0),
                    "micro_trigger_ok": pump_data.get("micro_trigger_ok", False),
                    "micro_score": pump_data.get("micro_score", 0),
                    "micro_reasons": pump_data.get("micro_reasons", []),
                    "micro_warnings": pump_data.get("micro_warnings", []),
                    "micro_from_high_pct": pump_data.get("micro_from_high_pct", 0),
                    "btc_change_pct": pump_data.get("btc_change_pct"),
                    "coin_change_pct": pump_data.get("coin_change_pct"),
                    "btc_divergence": pump_data.get("btc_divergence"),
                    "btc_short_context": pump_data.get("btc_short_context", "UNKNOWN"),
                    "btc_tailwind_risk": pump_data.get("btc_tailwind_risk", False),
                    "exchange": exchange,
                    "source": mon_data.get("source", "new_listing"),
                    "listing_age_hours": signal.get("listing_age_hours"),
                    "listing_age_source": listing_age_source,
                    "listing_trade_ok": signal.get("listing_trade_ok", False),
                    "trade_category": signal.get("trade_category", "UNKNOWN"),
                    "hours_tracked": pump_data.get("hours_tracked", 0),
                    "announcement_source": mon_data.get("announcement_source"),
                    "announcement_title": mon_data.get("announcement_title"),
                    "announcement_url": mon_data.get("announcement_url"),
                })

            except Exception as e:
                log.warning(f"NLS Error {symbol}: {e}\n{traceback.format_exc()}")
                results["errors"].append(f"{symbol}: {str(e)}")

        # Monitoring speichern
        save_monitoring_list(monitoring)

    except Exception as e:
        log.error(f"NLS Fatal: {e}\n{traceback.format_exc()}")
        results["errors"].append(f"Fatal: {str(e)}")

    # ── Ergebnisse speichern ──
    results["duration_sec"] = round(time.time() - start_time, 1)
    results["total_perps"] = len(all_perps) if 'all_perps' in locals() else 0

    try:
        RESULTS_FILE.write_text(json.dumps(results, indent=2, default=str))
    except Exception as e:
        log.error(f"NLS Save Error: {e}")

    sig_count = len(results["signals"])
    watch_count = len(results["watchlist"])
    mon_count = len(results["monitoring"])
    log.info(f"🆕 NLS fertig: {sig_count} Signale, {watch_count} Watchlist, "
             f"{mon_count} monitoring ({results['duration_sec']}s)")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SEED: Initiale Instrument-Liste laden (erster Run)
# ═══════════════════════════════════════════════════════════════════════════════

def seed_instrument_cache():
    """
    Beim allerersten Start: Alle aktuellen PERPs ALLER Exchanges in den Cache laden,
    damit beim nächsten Run keine Falsch-Positiven entstehen.
    """
    CACHE_DIR.mkdir(exist_ok=True)

    # Prüfe ob ALLE Exchange-Caches existieren
    all_seeded = all(
        (CACHE_DIR / f"nls_cache_{ex}.json").exists()
        for ex in ["crypto_com", "mexc", "bitget", "binance"]
    )
    if all_seeded:
        return False  # Alle Caches existieren schon

    log.info(" NLS: Erster Start — lade initiale Instrument-Listen für 4 Exchanges...")
    total = 0

    exchanges_data = {
        "crypto_com": ("crypto.com", fetch_cryptocom_instruments),
        "mexc": ("mexc", fetch_mexc_futures_instruments),
        "bitget": ("bitget", fetch_bitget_futures_instruments),
        "binance": ("binance", fetch_binance_futures_instruments),
    }

    for cache_key, (ex_name, fetcher) in exchanges_data.items():
        cache_file = CACHE_DIR / f"nls_cache_{cache_key}.json"
        if cache_file.exists():
            continue
        try:
            perps = fetcher()
            if perps:
                symbols = [p["symbol"] for p in perps]
                cache_file.write_text(json.dumps({
                    "symbols": symbols,
                    "last_update": datetime.now(timezone.utc).isoformat(),
                    "count": len(symbols),
                    "exchange": ex_name,
                    "seeded": True,
                }, indent=2))
                total += len(symbols)
                log.info(f" NLS: {len(symbols)} Perps von {ex_name} gecached")
            time.sleep(1)
        except Exception as e:
            log.warning(f"NLS Seed {ex_name}: {e}\n{traceback.format_exc()}")

    # Legacy INSTRUMENTS_CACHE auch schreiben (Kompatibilität)
    if not INSTRUMENTS_CACHE.exists():
        INSTRUMENTS_CACHE.write_text(json.dumps({
            "symbols": [],
            "last_update": datetime.now(timezone.utc).isoformat(),
            "count": 0,
            "seeded": True,
            "note": "Multi-Exchange: siehe nls_cache_*.json",
        }, indent=2))

    log.info(f" NLS: Total {total} Perps über 4 Exchanges gecached")
    return total > 0
