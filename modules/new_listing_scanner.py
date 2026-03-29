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
- Crypto.com Exchange (Primär — PERP-Trading)
- MEXC (Sekundär — Frühwarnung, listet am schnellsten)
"""

import os
import json
import time
import logging
import traceback
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
    else:
        return fetch_cryptocom_ticker(symbol)


def fetch_candles_for(symbol, exchange, timeframe="1h", count=50):
    """Holt Candles für ein Symbol von der richtigen Exchange."""
    if exchange == "mexc":
        return fetch_mexc_candles(symbol, timeframe, count)
    elif exchange == "bitget":
        return fetch_bitget_candles(symbol, timeframe, count)
    else:
        return fetch_cryptocom_candles(symbol, timeframe, count)


# ═══════════════════════════════════════════════════════════════════════════════
# LISTING DETECTION (Multi-Exchange Cache-Diff)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_new_listings():
    """
    Prüft 3 Exchanges auf neue PERP-Listings:
    - Crypto.com (237 Perps)
    - MEXC (755 Perps — schnellste Listings!)
    - Bitget (539 Perps)

    Gibt neue PERP-Instrumente + alle aktuellen zurück.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    all_new = []
    all_perps = []

    # ── Alle 3 Exchanges abfragen ──
    exchanges = {
        "crypto.com": fetch_cryptocom_instruments,
        "mexc": fetch_mexc_futures_instruments,
        "bitget": fetch_bitget_futures_instruments,
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
            cached_symbols = set()
            if cache_file.exists():
                try:
                    cached = json.loads(cache_file.read_text())
                    cached_symbols = set(cached.get("symbols", []))
                except Exception:
                    pass

            # Diff
            new_symbols = current_symbols - cached_symbols
            new_listings = [p for p in perps if p["symbol"] in new_symbols]

            if new_listings:
                log.info(f"🆕 NLS: {len(new_listings)} neue Perps auf {ex_name}: "
                         f"{', '.join(n['symbol'] for n in new_listings[:10])}")
                all_new.extend(new_listings)

            # Cache aktualisieren
            if current_symbols:
                cache_file.write_text(json.dumps({
                    "symbols": list(current_symbols),
                    "last_update": datetime.now(timezone.utc).isoformat(),
                    "count": len(current_symbols),
                    "exchange": ex_name,
                }, indent=2))

        except Exception as e:
            log.warning(f"NLS {ex_name} Error: {e}\n{traceback.format_exc()}")

    # ── Stock-Token & Index-Filter ──
    # MEXC listet Stock-Perps (AAPLSTOCK_USDT etc.) und Indices (US30_USDT)
    # Die folgen NICHT dem Crypto-Pump-Dump-Muster → rausfiltern
    STOCK_PATTERNS = ("STOCK_", "STOCK-", "US30_", "US30-", "HK50_", "HK50-",
                      "SP500_", "SP500-", "EU50_", "NASDAQ_", "FTSE_")
    before_filter = len(all_new)
    all_new = [n for n in all_new
               if not any(pat in n["symbol"].upper() for pat in STOCK_PATTERNS)]
    filtered = before_filter - len(all_new)
    if filtered:
        log.info(f" NLS: {filtered} Stock-Tokens/Indices gefiltert (kein Crypto-Pump-Dump)")

    # ── MEXC isNew-Flag als Bonus-Erkennung ──
    # Coins die schon im Cache waren aber von MEXC als "isNew" markiert sind
    # = kürzlich gelistet, aber VOR unserem Seed → trotzdem überwachen!
    known_new = {n["symbol"] for n in all_new}
    for p in all_perps:
        if p.get("exchange") == "mexc" and p.get("is_new"):
            sym = p["symbol"]
            if sym not in known_new and not any(pat in sym.upper() for pat in STOCK_PATTERNS):
                all_new.append(p)
                known_new.add(sym)
                log.info(f" NLS: {sym} via MEXC isNew-Flag erkannt (war schon im Cache)")

    # ── Bitget launchTime Bonus-Erkennung ──
    # Coins mit launchTime in den letzten 30 Tagen = kürzlich gelistet
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000)
    for p in all_perps:
        if p.get("exchange") == "bitget" and p.get("launch_time", 0) > cutoff_ms:
            sym = p["symbol"]
            if sym not in known_new and not any(pat in sym.upper() for pat in STOCK_PATTERNS):
                all_new.append(p)
                known_new.add(sym)
                lt_str = datetime.fromtimestamp(p["launch_time"] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
                log.info(f" NLS: {sym} via Bitget launchTime erkannt (gelistet {lt_str})")

    # ── MEXC createTime Bonus-Erkennung ──
    # Coins mit createTime in den letzten 30 Tagen
    for p in all_perps:
        if p.get("exchange") == "mexc" and p.get("create_time", 0) > cutoff_ms:
            sym = p["symbol"]
            if sym not in known_new and not any(pat in sym.upper() for pat in STOCK_PATTERNS):
                all_new.append(p)
                known_new.add(sym)
                ct_str = datetime.fromtimestamp(p["create_time"] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
                log.info(f" NLS: {sym} via MEXC createTime erkannt (gelistet {ct_str})")

    if all_new:
        log.info(f"🆕 NLS TOTAL: {len(all_new)} neue/kürzliche Listings über alle Exchanges")

    return all_new, all_perps


# ═══════════════════════════════════════════════════════════════════════════════
# PUMP EXHAUSTION SCORING (7 Komponenten, 0-100)
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_listing_exhaustion(candles, ticker, book=None):
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

    pump_pct = (ath - first_price) / first_price * 100
    current_from_ath = (ath - current_price) / ath * 100 if ath > 0 else 0
    total_range = ath - atl if ath > atl else 0.0001

    pump_data = {
        "first_price": first_price,
        "current_price": current_price,
        "ath": ath,
        "atl": atl,
        "pump_pct": round(pump_pct, 1),
        "from_ath_pct": round(current_from_ath, 1),
        "candle_count": n,
        "hours_tracked": n,  # 1h Candles
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
    # 7. OI vs PRICE DIVERGENZ (0-5)
    #    OI steigt während Preis stagniert/fällt = Longs sind trapped
    # ═══════════════════════════════════════════════════════════════════════
    if ticker and ticker.get("open_interest", 0) > 0:
        oi = ticker["open_interest"]
        # Hohe OI bei fallenden Preisen = trapped longs
        if current_from_ath >= 5 and oi > 0:
            pts = 5
            details.append(f" OI: {oi:,.0f} bei {current_from_ath:.1f}% unter ATH → {pts}/5 (Trapped Longs)")
        elif current_from_ath >= 2:
            pts = 2
            details.append(f" OI: {oi:,.0f} → {pts}/5")
        else:
            pts = 0
            details.append(f" OI: {oi:,.0f} (neutral)")
        score += pts
    else:
        details.append(" OI: keine Daten")

    # ═══════════════════════════════════════════════════════════════════════
    # 8. FUNDING RATE (0-5 Bonus)
    #    Hohe positive Funding = Longs zahlen Shorts → Überhitzung
    #    Nur MEXC/Bitget liefern funding_rate im Ticker
    # ═══════════════════════════════════════════════════════════════════════
    fr = ticker.get("funding_rate", 0) if ticker else 0
    if fr and fr > 0:
        fr_pct = fr * 100  # z.B. 0.001 → 0.1%
        if fr_pct >= 0.1:    # Extrem hohe Funding (> 0.1% pro 8h)
            pts = 5
        elif fr_pct >= 0.05:  # Überdurchschnittlich
            pts = 3
        elif fr_pct >= 0.01:  # Leicht positiv
            pts = 1
        else:
            pts = 0
        score += pts
        pump_data["funding_rate"] = round(fr_pct, 4)
        details.append(f" Funding: {fr_pct:.3f}% (positive = Longs zahlen) → {pts}/5")
    elif fr and fr < 0:
        # Negative Funding = Shorts zahlen → gegen uns, Score-Malus
        fr_pct = fr * 100
        score = max(0, score - 3)
        pump_data["funding_rate"] = round(fr_pct, 4)
        details.append(f" Funding: {fr_pct:.3f}% (negativ! Shorts zahlen) → -3 Malus")
    else:
        details.append(" Funding: keine Daten (Crypto.com)")

    return min(100, score), details, pump_data


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

    # 1. Volume Minimum
    vol_24h = ticker.get("volume_usd_24h", 0) if ticker else 0
    if vol_24h < CONFIG["min_volume_24h_usd"]:
        warnings.append(f"[!] Volume zu niedrig: ${vol_24h:,.0f} (min ${CONFIG['min_volume_24h_usd']:,})")
        is_safe = False

    # 2. Spread Maximum
    if ticker and ticker.get("bid") and ticker.get("ask"):
        mid = (ticker["bid"] + ticker["ask"]) / 2
        spread = (ticker["ask"] - ticker["bid"]) / mid * 100 if mid > 0 else 99
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
        last_vol = candles[-1].get("volume_usd", 0)
        prev_vol = candles[-2].get("volume_usd", 0)
        if last_vol == 0 and prev_vol == 0:
            warnings.append("[!] Kein Volume in letzten 2 Stunden — Coin möglicherweise tot")
            is_safe = False

    # 5. Preis-Crash Detection (Rug Pull Schutz)
    if candles and len(candles) >= 3:
        recent_drop = 0
        for i in range(-3, 0):
            if candles[i]["open"] > 0:
                drop = (candles[i]["close"] - candles[i]["open"]) / candles[i]["open"] * 100
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
    stop = ath * (1 + CONFIG["stop_above_ath_pct"] / 100)
    tp1 = ath * (1 - CONFIG["tp1_from_ath_pct"] / 100)
    tp2 = ath * (1 - CONFIG["tp2_from_ath_pct"] / 100)

    risk = abs(stop - entry)
    reward1 = abs(entry - tp1)
    reward2 = abs(entry - tp2)
    rr1 = round(reward1 / risk, 2) if risk > 0 else 0
    rr2 = round(reward2 / risk, 2) if risk > 0 else 0

    # ── Timing Score ──
    if exh_score >= CONFIG["exh_short_entry"] and safety_ok:
        timing = "[-] JETZT SHORTEN"
        timing_quality = 5
    elif exh_score >= CONFIG["exh_short_entry"] and not safety_ok:
        timing = "[~] SIGNAL aber Liquiditäts-Risiko"
        timing_quality = 3
    elif exh_score >= CONFIG["exh_watch"]:
        timing = "[+] WATCHLIST — noch nicht reif"
        timing_quality = 2
    else:
        timing = "[o] Kein Signal — Pump noch aktiv"
        timing_quality = 0

    # ── Grading ──
    if exh_score >= 80 and rr1 >= 2.0 and safety_ok:
        grade = "S"
        grade_label = " S — ELITE SHORT"
    elif exh_score >= 65 and rr1 >= 1.5 and safety_ok:
        grade = "A"
        grade_label = " A — STRONG SHORT"
    elif exh_score >= 50 and rr1 >= 1.0:
        grade = "B"
        grade_label = " B — MODERATE"
    elif exh_score >= 40:
        grade = "C"
        grade_label = " C — WEAK"
    else:
        grade = "D"
        grade_label = "[X] D — NO TRADE"

    return {
        "symbol": symbol,
        "direction": "SHORT",
        "entry": round(entry, 6),
        "stop_loss": round(stop, 6),
        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "rr1": rr1,
        "rr2": rr2,
        "risk_pct": round((stop - entry) / entry * 100, 2),
        "exh_score": exh_score,
        "timing": timing,
        "timing_quality": timing_quality,
        "grade": grade,
        "grade_label": grade_label,
        "safety_ok": safety_ok,
        "safety_warnings": safety_warnings,
        "pump_data": pump_data,
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


def add_to_monitoring(symbol, exchange="crypto.com"):
    """Fügt ein neues Listing zur Überwachung hinzu."""
    monitoring = load_monitoring_list()
    if symbol not in monitoring:
        monitoring[symbol] = {
            "symbol": symbol,
            "exchange": exchange,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "status": "monitoring",  # monitoring | signal | expired
            "last_exh_score": 0,
            "peak_exh_score": 0,
        }
        save_monitoring_list(monitoring)
        log.info(f"🆕 NLS: {symbol} zur Überwachung hinzugefügt")
    return monitoring


def cleanup_monitoring(monitoring):
    """Entfernt abgelaufene Einträge (> 72h)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CONFIG["monitor_hours_max"])
    to_remove = []
    for sym, data in monitoring.items():
        try:
            detected = datetime.fromisoformat(data["detected_at"].replace("Z", "+00:00"))
            if detected < cutoff:
                to_remove.append(sym)
        except Exception:
            pass
    for sym in to_remove:
        monitoring[sym]["status"] = "expired"
        log.info(f"⏰ NLS: {sym} — Monitoring abgelaufen (>{CONFIG['monitor_hours_max']}h)")
    return monitoring


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPTFUNKTION (wird von bg_service.py aufgerufen)
# ═══════════════════════════════════════════════════════════════════════════════

def run_new_listing_scanner():
    """
    Hauptfunktion des New Listing Dump Scanners.

    1. Prüft auf neue PERP-Listings (Cache-Diff)
    2. Für jedes überwachte Listing: Candles + Ticker + Book holen
    3. Exhaustion Score berechnen
    4. Safety prüfen
    5. Ggf. Short-Signal generieren
    6. Ergebnisse als JSON speichern

    Returns: dict mit Ergebnissen
    """
    log.info("🆕 === New Listing Scanner gestartet ===")
    start_time = time.time()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_listings_detected": [],
        "signals": [],
        "watchlist": [],
        "monitoring": [],
        "errors": [],
    }

    try:
        # ── Phase 1: Neue Listings erkennen ──
        new_listings, all_perps = detect_new_listings()
        for nl in new_listings:
            results["new_listings_detected"].append(nl["symbol"])
            add_to_monitoring(nl["symbol"], nl.get("exchange", "crypto.com"))

        # ── Phase 2: Alle überwachten Listings analysieren ──
        monitoring = load_monitoring_list()
        monitoring = cleanup_monitoring(monitoring)

        active = {k: v for k, v in monitoring.items()
                  if v.get("status") == "monitoring"}

        log.info(f" NLS: {len(active)} Listings in Überwachung, "
                 f"{len(new_listings)} neu erkannt, "
                 f"{len(all_perps)} PERP-Instrumente total")

        for symbol, mon_data in active.items():
            try:
                time.sleep(0.5)  # Rate Limiting
                exchange = mon_data.get("exchange", "crypto.com")

                # Daten holen (Multi-Exchange Adapter)
                ticker = fetch_ticker_for(symbol, exchange)
                if not ticker:
                    continue

                candles = fetch_candles_for(symbol, exchange, "1h", 50)
                time.sleep(0.3)

                # Orderbook nur für Crypto.com (MEXC/Bitget haben kein öffentliches Depth-API)
                book = fetch_cryptocom_orderbook(symbol, 10) if exchange == "crypto.com" else None

                # Candle-Mindestanzahl prüfen
                if not candles or len(candles) < 3:
                    log.info(f"⏳ NLS: {symbol} — nur {len(candles) if candles else 0} Candles, warte auf History")
                    mon_data["status"] = "waiting_for_history"
                    continue

                # Exhaustion Score berechnen
                exh_score, exh_details, pump_data = calculate_listing_exhaustion(
                    candles, ticker, book
                )

                # Safety prüfen
                safety_ok, safety_warnings = check_safety(ticker, book, candles)

                # Monitoring-Status aktualisieren
                mon_data["last_exh_score"] = exh_score
                mon_data["peak_exh_score"] = max(mon_data.get("peak_exh_score", 0), exh_score)
                mon_data["last_check"] = datetime.now(timezone.utc).isoformat()
                mon_data["pump_pct"] = pump_data.get("pump_pct", 0)

                # ── Already-Dumped Filter ──
                # Wenn Preis schon >40% unter ATH ist, shorten wir NICHT (falling knife)
                from_ath = pump_data.get("from_ath_pct", 0)
                if from_ath > 40:
                    log.info(f" NLS: {symbol} übersprungen — bereits {from_ath:.0f}% unter ATH (falling knife)")
                    mon_data["status"] = "expired_dumped"
                    results["monitoring"].append({
                        "symbol": symbol,
                        "exh_score": exh_score,
                        "pump_pct": pump_data.get("pump_pct", 0),
                        "from_ath_pct": from_ath,
                        "volume_ratio": pump_data.get("vol_ratio", 0),
                        "safety_ok": safety_ok,
                        "grade": "SKIP",
                        "timing": " Already dumped",
                        "hours_tracked": pump_data.get("hours_tracked", 0),
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
                    "signal": signal,
                }

                if signal["timing_quality"] >= 4:
                    results["signals"].append(entry)
                    mon_data["status"] = "signal"
                    log.info(f"[-] NLS SHORT SIGNAL: {symbol} — ExhScore {exh_score}, "
                             f"Pump {pump_data.get('pump_pct', 0):.0f}%, "
                             f"RR {signal['rr1']:.1f}x, Grade {signal['grade']}")
                elif signal["timing_quality"] >= 2:
                    results["watchlist"].append(entry)

                results["monitoring"].append({
                    "symbol": symbol,
                    "exh_score": exh_score,
                    "pump_pct": pump_data.get("pump_pct", 0),
                    "from_ath_pct": pump_data.get("from_ath_pct", 0),
                    "volume_ratio": pump_data.get("vol_ratio", 0),
                    "safety_ok": safety_ok,
                    "grade": signal.get("grade", "?"),
                    "timing": signal.get("timing", "?"),
                    "hours_tracked": pump_data.get("hours_tracked", 0),
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
    results["total_perps"] = len(all_perps) if 'all_perps' in dir() else 0

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
        for ex in ["crypto_com", "mexc", "bitget"]
    )
    if all_seeded:
        return False  # Alle Caches existieren schon

    log.info(" NLS: Erster Start — lade initiale Instrument-Listen für 3 Exchanges...")
    total = 0

    # Crypto.com
    exchanges_data = {
        "crypto_com": ("crypto.com", fetch_cryptocom_instruments),
        "mexc": ("mexc", fetch_mexc_futures_instruments),
        "bitget": ("bitget", fetch_bitget_futures_instruments),
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

    log.info(f" NLS: Total {total} Perps über 3 Exchanges gecached")
    return total > 0
