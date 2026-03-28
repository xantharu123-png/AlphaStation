"""
FastAPI Backend for TradingBot Scanner Modules
===============================================

Wraps existing scanner modules (BI Scanner, Biotech Scanner, Bear Scanner)
with REST API endpoints. Uses environment variables for API keys instead of
Streamlit secrets.

Endpoints:
  GET  /api/health          → server status
  GET  /api/strategies      → list strategies by market type
  GET  /api/market-status   → current market session (Pre/Regular/After)
  POST /api/scan            → run main scanner
  POST /api/bi-scan         → trigger BI background scan
  GET  /api/bi-results      → get cached BI scan results
  POST /api/bear-scan       → trigger bear scanner
  GET  /api/bear-results    → get cached bear scan results
  POST /api/biotech-scan    → trigger biotech scanner
  GET  /api/biotech-results → get cached biotech results
"""

import os
import json
import math
import time
import threading
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests as req

# Import scanner modules
from modules.scanners import (
    _bi_background_scan,
    _biotech_background_scan,
    _bi_cache_load,
    _biotech_cache_load,
)
from modules.helpers import get_current_trading_session
from modules.data_fetchers import rate_limited_get, fetch_ohlcv_for_chart
from modules.indicators import calculate_ema_series, calculate_vwap, calculate_rsi_from_bars, calculate_macd

# Import pattern detection
try:
    from modules.patterns import find_harmonic_for_chart, detect_chart_patterns, find_pivots, detect_order_blocks, detect_liquidity_levels
    HAS_PATTERNS = True
except ImportError:
    HAS_PATTERNS = False
    print("[Warning] patterns module not fully loaded")

# Import new listing scanner
try:
    from modules.new_listing_scanner import (
        detect_new_listings,
        calculate_listing_exhaustion,
        fetch_ticker_for,
        fetch_candles_for,
        fetch_cryptocom_orderbook,
    )
    HAS_NEW_LISTING_SCANNER = True
except ImportError:
    HAS_NEW_LISTING_SCANNER = False
    print("[Warning] new_listing_scanner module not found - new listing endpoints will not work")

# ── Load ALL 65+ strategies from modules/strategies.py ──
# Mock streamlit to avoid ImportError (strategies.py imports streamlit for apply_strategy)
import importlib.util
import sys as _sys

def _load_strategies():
    _mock = type(_sys)("streamlit")
    _mock.session_state = {}
    _mock.warning = lambda *a, **k: None
    _real_st = _sys.modules.get("streamlit")
    _sys.modules["streamlit"] = _mock
    try:
        spec = importlib.util.spec_from_file_location("_strategies", "modules/strategies.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return (
            getattr(mod, "STRATEGIES", {}),
            getattr(mod, "CRYPTO_STRATEGIES", {}),
            getattr(mod, "FUTURES_STRATEGIES", {}),
            getattr(mod, "FOREX_STRATEGIES", {}),
            getattr(mod, "INTERNATIONAL_STRATEGIES", {}),
        )
    finally:
        if _real_st:
            _sys.modules["streamlit"] = _real_st
        else:
            _sys.modules.pop("streamlit", None)

STRATEGIES, CRYPTO_STRATEGIES, FUTURES_STRATEGIES, FOREX_STRATEGIES, INTERNATIONAL_STRATEGIES = _load_strategies()
print(f"[Init] Strategies loaded: {len(STRATEGIES)} Stock, {len(CRYPTO_STRATEGIES)} Crypto, {len(FUTURES_STRATEGIES)} Futures, {len(FOREX_STRATEGIES)} Forex, {len(INTERNATIONAL_STRATEGIES)} International")

INVERSE_ETFS = {
    "SQQQ": ("3x Short Nasdaq", "QQQ"), "SPXS": ("3x Short S&P 500", "SPY"),
    "SDOW": ("3x Short Dow", "DIA"), "SH": ("1x Short S&P 500", "SPY"),
    "PSQ": ("1x Short Nasdaq", "QQQ"), "DOG": ("1x Short Dow", "DIA"),
    "SPXU": ("3x Short S&P 500", "SPY"), "QID": ("2x Short Nasdaq", "QQQ"),
    "RWM": ("1x Short Russell", "IWM"), "SRTY": ("3x Short Russell", "IWM"),
    "SOXS": ("3x Short Semis", "SOXX"), "LABD": ("3x Short Biotech", "XBI"),
    "FAZ": ("3x Short Financials", "XLF"), "ERY": ("3x Short Energy", "XLE"),
    "TZA": ("3x Short SmallCap", "IWM"),
    "UVXY": ("1.5x Long VIX", "VIX"), "VIXY": ("1x Long VIX", "VIX"),
}


# ── Configuration & Constants ──
API_VERSION = "1.0.0"
POLYGON_KEY = os.getenv("POLYGON_KEY", "")
BPIQ_API_KEY = os.getenv("BPIQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Cache file paths
BI_CACHE_LONG = "/tmp/bi_cache_long.json"
BI_CACHE_SHORT = "/tmp/bi_cache_short.json"
BEAR_CACHE = "/tmp/bear_scanner_cache.json"
BIOTECH_CACHE = "/tmp/alpha_biotech_cache.json"


# ── Pydantic Models ──
class ScanRequest(BaseModel):
    strategy: str
    market_type: str = "stocks"  # stocks, crypto, futures, forex
    session: Optional[str] = None  # Pre-Market, Regular, After-Hours
    filters: Optional[Dict[str, Any]] = None


class BIScanRequest(BaseModel):
    direction: str = "long"  # long or short
    market_type: str = "stocks"


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    api_keys_configured: Dict[str, bool]


class MarketStatusResponse(BaseModel):
    session: str
    detail: str
    timestamp: str


class StrategiesResponse(BaseModel):
    market_type: str
    strategies: Dict[str, Any]
    count: int


class ScanResultsResponse(BaseModel):
    status: str
    count: int
    data: List[Dict[str, Any]]
    cached_at: Optional[str] = None
    cache_age_seconds: Optional[int] = None


# ── Utility Functions ──
def load_cache_file(filepath: str, max_age_hours: int = 2) -> tuple[List[Dict], Optional[str]]:
    """Load cache file and return (data, cached_at_timestamp)."""
    if not Path(filepath).exists():
        return [], None

    try:
        with open(filepath, "r") as f:
            data = json.load(f)

        cached_at = None
        if isinstance(data, dict):
            cached_at = data.get("cached_at")
            if "results" in data:
                data = data.get("results", [])
            else:
                # Wrap single dict in list for compatibility
                data = [data]

        if not isinstance(data, list):
            data = [data]

        return data, cached_at
    except Exception as e:
        print(f"Error loading cache {filepath}: {e}")
        return [], None


def save_cache_file(filepath: str, data: List[Dict]) -> None:
    """Save cache file with timestamp."""
    try:
        cache_data = {
            "cached_at": datetime.now().isoformat(),
            "results": data,
        }
        with open(filepath, "w") as f:
            json.dump(cache_data, f, indent=2, default=_serialize_json)
    except Exception as e:
        print(f"Error saving cache {filepath}: {e}")


def _serialize_json(obj):
    """Handle NaN, Infinity and other non-JSON-serializable types."""
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return None
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def get_strategies_for_market(market_type: str) -> Dict[str, Any]:
    """Return strategy dict for given market type."""
    strategies_map = {
        "stocks": STRATEGIES,
        "crypto": CRYPTO_STRATEGIES,
        "futures": FUTURES_STRATEGIES,
        "forex": FOREX_STRATEGIES,
        "international": INTERNATIONAL_STRATEGIES,
    }
    return strategies_map.get(market_type, STRATEGIES)


def _bi_background_scan_wrapper(direction: str) -> None:
    """Wrapper to run _bi_background_scan in background without candidates pre-load."""
    try:
        _bi_background_scan(POLYGON_KEY, direction=direction, candidates=None)
    except Exception as e:
        print(f"BI background scan error ({direction}): {e}")


def _biotech_scan_wrapper() -> None:
    """Wrapper to run biotech background scan in background."""
    try:
        _biotech_background_scan(POLYGON_KEY)
    except Exception as e:
        print(f"Biotech background scan error: {e}")


def _bear_scan_wrapper() -> None:
    """Run bear scanner in background — finds inverse ETF opportunities and breakdown stocks."""
    try:
        result = {"inverse_etfs": [], "short_candidates": [], "breakdown_stocks": []}

        # --- Section 1: Inverse ETF performance ---
        for ticker, (desc, underlying) in INVERSE_ETFS.items():
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 40, "sort": "desc"})
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if len(bars) < 2:
                    continue

                close = bars[0]["c"]
                prev_close = bars[1]["c"]
                chg_1d = ((close - prev_close) / prev_close) * 100

                chg_5d = 0
                if len(bars) >= 6:
                    chg_5d = ((close - bars[5]["c"]) / bars[5]["c"]) * 100

                chg_20d = 0
                if len(bars) >= 21:
                    chg_20d = ((close - bars[20]["c"]) / bars[20]["c"]) * 100

                vol = bars[0].get("v", 0)
                avg_vol = sum(b.get("v", 0) for b in bars[1:21]) / min(len(bars) - 1, 20) if len(bars) > 1 else 1
                rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0

                if chg_5d > 5:
                    signal = "STARK"
                elif chg_5d > 2:
                    signal = "Steigend"
                elif chg_5d > 0:
                    signal = "Leicht"
                else:
                    signal = "Fallend"

                result["inverse_etfs"].append({
                    "ticker": ticker, "name": desc, "underlying": underlying,
                    "price": round(close, 2), "change_1d": round(chg_1d, 2),
                    "change_5d": round(chg_5d, 2), "change_20d": round(chg_20d, 2),
                    "volume": vol, "rvol": rvol, "signal": signal,
                })
            except Exception:
                continue

        result["inverse_etfs"].sort(key=lambda x: x.get("change_5d", 0), reverse=True)

        # --- Section 2: Breakdown stocks (big single-day losers) ---
        try:
            snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY})
            if snap_resp.status_code == 200:
                tickers = snap_resp.json().get("tickers", [])
                losers = []
                for t in tickers:
                    try:
                        day = t.get("day", {})
                        prev = t.get("prevDay", {})
                        price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
                        prev_close = prev.get("c", 0)
                        if not price or not prev_close or price < 5:
                            continue
                        vol = day.get("v", 0)
                        dollar_vol = price * vol
                        if dollar_vol < 500000:
                            continue
                        chg_pct = ((price - prev_close) / prev_close) * 100
                        if chg_pct > -4:
                            continue
                        losers.append({
                            "ticker": t.get("ticker", ""),
                            "price": round(price, 2),
                            "change_pct": round(chg_pct, 2),
                            "volume": vol,
                            "dollar_volume": round(dollar_vol, 0),
                        })
                    except Exception:
                        continue
                losers.sort(key=lambda x: x.get("change_pct", 0))
                result["breakdown_stocks"] = losers[:30]
        except Exception as e:
            print(f"Breakdown stocks error: {e}")

        save_cache_file(BEAR_CACHE, [result])
    except Exception as e:
        print(f"Bear scanner error: {e}")


# ── Background Scheduler ──
# Runs all scans automatically at defined intervals (like old Streamlit version)
_scheduler_running = False
_scan_status = {
    "bi_long": {"running": False, "last_run": None, "next_run": None, "interval_min": 15},
    "bi_short": {"running": False, "last_run": None, "next_run": None, "interval_min": 15},
    "bear": {"running": False, "last_run": None, "next_run": None, "interval_min": 20},
    "biotech": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "early_movers": {"running": False, "last_run": None, "next_run": None, "interval_min": 10},
    "crash_monitor": {"running": False, "last_run": None, "next_run": None, "interval_min": 10},
    "btc_divergenz": {"running": False, "last_run": None, "next_run": None, "interval_min": 15},
    "money_flow": {"running": False, "last_run": None, "next_run": None, "interval_min": 20},
    "new_listing": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "volume_spikes": {"running": False, "last_run": None, "next_run": None, "interval_min": 10},
}

def _run_scan_safe(name, func):
    """Run a scan function safely, updating status."""
    _scan_status[name]["running"] = True
    try:
        func()
        _scan_status[name]["last_run"] = datetime.now().isoformat()
    except Exception as e:
        print(f"[Scheduler] {name} error: {e}")
    finally:
        _scan_status[name]["running"] = False

def _scheduler_loop():
    """Background loop that triggers all scans at their defined intervals."""
    global _scheduler_running
    print("[Scheduler] Starting automatic background scans...")

    # Initial delay to let server fully start
    time.sleep(5)

    # Run all scans once immediately on startup
    scan_tasks = [
        ("early_movers", _early_movers_wrapper),
        ("crash_monitor", _crash_monitor_wrapper),
        ("btc_divergenz", _btc_divergenz_wrapper),
        ("money_flow", _money_flow_wrapper),
        ("bi_long", lambda: _bi_background_scan_wrapper("long")),
        ("bi_short", lambda: _bi_background_scan_wrapper("short")),
        ("bear", _bear_scan_wrapper),
        ("biotech", _biotech_scan_wrapper),
        ("volume_spikes", _volume_spikes_wrapper),
    ]

    # Only add new_listing scan if module is available
    if HAS_NEW_LISTING_SCANNER:
        scan_tasks.append(("new_listing", _new_listing_wrapper))

    # Stagger initial scans to avoid API rate limits
    for name, func in scan_tasks:
        if not _scheduler_running:
            break
        print(f"[Scheduler] Initial scan: {name}")
        _run_scan_safe(name, func)
        time.sleep(10)  # 10s pause between initial scans

    # Then loop with interval checks
    last_run_times = {name: time.time() for name in _scan_status}

    while _scheduler_running:
        now = time.time()
        for name, func in scan_tasks:
            if not _scheduler_running:
                break
            interval_sec = _scan_status[name]["interval_min"] * 60
            elapsed = now - last_run_times.get(name, 0)
            if elapsed >= interval_sec and not _scan_status[name]["running"]:
                print(f"[Scheduler] Running: {name} (interval: {_scan_status[name]['interval_min']}min)")
                _run_scan_safe(name, func)
                last_run_times[name] = time.time()
                _scan_status[name]["next_run"] = datetime.fromtimestamp(
                    last_run_times[name] + interval_sec
                ).isoformat()
                time.sleep(5)  # Small pause between scans
        time.sleep(30)  # Check every 30 seconds


@asynccontextmanager
async def lifespan(app):
    """Start background scheduler on startup, stop on shutdown."""
    global _scheduler_running
    _scheduler_running = True
    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    scheduler_thread.start()
    print("[Scheduler] Background scan scheduler started")
    yield
    _scheduler_running = False
    print("[Scheduler] Background scan scheduler stopped")


# ── FastAPI App ──
app = FastAPI(
    title="TradingBot Scanner API",
    description="REST API for trading scanner modules",
    version=API_VERSION,
    lifespan=lifespan,
)

# CORS middleware for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173", "http://178.104.69.209:3000", "http://178.104.69.209"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ──

@app.get("/api/health", response_model=HealthResponse)
def get_health():
    """Check API health and configuration status."""
    return HealthResponse(
        status="healthy",
        version=API_VERSION,
        timestamp=datetime.now().isoformat(),
        api_keys_configured={
            "POLYGON_KEY": bool(POLYGON_KEY),
            "BPIQ_API_KEY": bool(BPIQ_API_KEY),
            "ANTHROPIC_API_KEY": bool(ANTHROPIC_API_KEY),
        },
    )


@app.get("/api/market-status", response_model=MarketStatusResponse)
def get_market_status():
    """Get current market session (Pre-Market, Regular, After-Hours)."""
    session, detail = get_current_trading_session()
    return MarketStatusResponse(
        session=session,
        detail=detail,
        timestamp=datetime.now().isoformat(),
    )


@app.get("/api/scan-status")
def get_scan_status():
    """Get status of all background scans (running, last_run, next_run)."""
    return {
        "scheduler_running": _scheduler_running,
        "scans": _scan_status,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/ticker-detail")
def get_ticker_detail(ticker: str = Query(..., description="Ticker symbol (e.g. NVDA, AAPL, X:BTCUSD)")):
    """Get detailed price data for a single ticker (30 days, key metrics)."""
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 60, "sort": "desc"})
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")
        bars = resp.json().get("results", [])
        if not bars:
            raise HTTPException(status_code=404, detail=f"No data for '{ticker}'")

        close = bars[0]["c"]
        high = bars[0]["h"]
        low = bars[0]["l"]
        opn = bars[0]["o"]
        vol = bars[0].get("v", 0)
        prev_close = bars[1]["c"] if len(bars) > 1 else close
        chg_1d = round(((close - prev_close) / prev_close) * 100, 2)
        chg_5d = round(((close - bars[min(5, len(bars)-1)]["c"]) / bars[min(5, len(bars)-1)]["c"]) * 100, 2) if len(bars) > 5 else 0
        chg_20d = round(((close - bars[min(20, len(bars)-1)]["c"]) / bars[min(20, len(bars)-1)]["c"]) * 100, 2) if len(bars) > 20 else 0

        # Calculate key indicators
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        volumes = [b.get("v", 0) for b in bars]

        ma20 = round(sum(closes[:20]) / min(len(closes), 20), 2)
        ma50 = round(sum(closes[:50]) / min(len(closes), 50), 2) if len(closes) >= 50 else None
        avg_vol = sum(volumes[1:21]) / min(len(volumes) - 1, 20) if len(volumes) > 1 else 1
        rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0

        # RSI (14-period)
        rsi = None
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(14):
                diff = closes[i] - closes[i+1]  # bars sorted desc: [0]=newest
                if diff > 0:
                    # Price went UP (newer > older) = gain
                    gains.append(diff)
                else:
                    # Price went DOWN = loss
                    losses.append(abs(diff))
            avg_gain = sum(gains) / 14 if gains else 0.001
            avg_loss = sum(losses) / 14 if losses else 0.001
            rs = avg_gain / avg_loss
            rsi = round(100 - (100 / (1 + rs)), 1)

        # High/Low 20d
        high_20d = round(max(highs[:20]), 2) if len(highs) >= 20 else round(max(highs), 2)
        low_20d = round(min(lows[:20]), 2) if len(lows) >= 20 else round(min(lows), 2)
        range_pos = round((close - low_20d) / (high_20d - low_20d) * 100, 1) if high_20d != low_20d else 50

        # Support/Resistance (simple pivot)
        pivot = round((high + low + close) / 3, 2)
        support_1 = round(2 * pivot - high, 2)
        resist_1 = round(2 * pivot - low, 2)

        # ========== MASSIVE EXPANSION STARTS HERE ==========

        # 1. ADDITIONAL EMAs (exponential moving averages)
        def calculate_ema(data, period):
            """Calculate EMA using formula: EMA = price * k + EMA_prev * (1-k)"""
            if len(data) < period:
                return None
            k = 2 / (period + 1)
            ema = sum(data[-period:]) / period  # Start with SMA
            for i in range(len(data) - period - 1, -1, -1):
                ema = data[i] * k + ema * (1 - k)
            return round(ema, 2)

        ema9 = calculate_ema(closes, 9)
        ema20 = calculate_ema(closes, 20)
        ema50 = calculate_ema(closes, 50)
        ema100 = calculate_ema(closes, 100)
        ema200 = calculate_ema(closes, 200)

        # 2. VWAP (from last 20 bars)
        vwap = None
        if len(bars) >= 20:
            vwap_bars = bars[:20]  # Most recent 20
            cum_tp_vol = sum(((b["h"] + b["l"] + b["c"]) / 3) * b.get("v", 0) for b in vwap_bars)
            cum_vol = sum(b.get("v", 0) for b in vwap_bars)
            if cum_vol > 0:
                vwap = round(cum_tp_vol / cum_vol, 2)

        # 3. MACD
        ema12 = calculate_ema(closes, 12)
        ema26 = calculate_ema(closes, 26)
        macd = None
        macd_signal = None
        macd_histogram = None
        if ema12 is not None and ema26 is not None:
            macd = round(ema12 - ema26, 2)
            # Signal line is EMA9 of MACD line - simplified: use approximation
            if len(closes) >= 34:
                macd_line_vals = []
                for i in range(len(closes) - 1, -1, -1):
                    test_ema12 = calculate_ema(closes[:len(closes)-i], 12) if len(closes) - i >= 12 else None
                    test_ema26 = calculate_ema(closes[:len(closes)-i], 26) if len(closes) - i >= 26 else None
                    if test_ema12 and test_ema26:
                        macd_line_vals.append(test_ema12 - test_ema26)
                if len(macd_line_vals) >= 9:
                    macd_signal = calculate_ema(list(reversed(macd_line_vals)), 9)
            if macd_signal is not None:
                macd_histogram = round(macd - macd_signal, 2)

        # 4. Bollinger Bands (20-period, 2 std dev)
        bb_upper = None
        bb_lower = None
        if len(closes) >= 20:
            ma20_bb = sum(closes[:20]) / 20
            variance = sum((x - ma20_bb) ** 2 for x in closes[:20]) / 20
            stddev = variance ** 0.5
            bb_upper = round(ma20 + 2 * stddev, 2)
            bb_lower = round(ma20 - 2 * stddev, 2)

        # 5. Fibonacci Retracement Levels (from 20d high/low)
        fib_levels = {}
        if len(bars) >= 20:
            high_20 = max(highs[:20])
            low_20 = min(lows[:20])
            range_fib = high_20 - low_20
            fib_ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
            for ratio in fib_ratios:
                level_price = low_20 + range_fib * ratio
                fib_levels[f"{int(ratio*100)}%"] = round(level_price, 2)

        # 6. ATR (Average True Range, 14-period)
        atr = None
        if len(bars) >= 15:
            tr_values = []
            for i in range(14):
                high_i = highs[i]
                low_i = lows[i]
                prev_close_i = closes[i + 1] if i < len(closes) - 1 else closes[i]
                tr = max(high_i - low_i, abs(high_i - prev_close_i), abs(low_i - prev_close_i))
                tr_values.append(tr)
            atr = round(sum(tr_values) / 14, 2)

        # 7. Signal Scoring (10-factor system)
        signals = []
        score = 0

        # 1. Trend (price vs MA20/MA50)
        if close > ma20 and (ma50 is None or close > ma50):
            signals.append({"name": "Trend", "status": "bullish", "detail": "Price above MA20/MA50", "points": 2})
            score += 2
        elif close < ma20:
            signals.append({"name": "Trend", "status": "bearish", "detail": "Price below MA20", "points": 0})
        else:
            signals.append({"name": "Trend", "status": "neutral", "detail": "Price near MA20", "points": 1})
            score += 1

        # 2. RSI
        if rsi is not None:
            if rsi > 70:
                signals.append({"name": "RSI", "status": "bearish", "detail": f"Overbought (RSI {rsi})", "points": 0})
            elif rsi < 30:
                signals.append({"name": "RSI", "status": "bullish", "detail": f"Oversold (RSI {rsi})", "points": 2})
                score += 2
            else:
                signals.append({"name": "RSI", "status": "neutral", "detail": f"Neutral (RSI {rsi})", "points": 1})
                score += 1

        # 3. Volume (RVOL)
        if rvol > 1.2:
            signals.append({"name": "Volume", "status": "bullish", "detail": f"High volume ({rvol}x avg)", "points": 2})
            score += 2
        elif rvol < 0.8:
            signals.append({"name": "Volume", "status": "bearish", "detail": f"Low volume ({rvol}x avg)", "points": 0})
        else:
            signals.append({"name": "Volume", "status": "neutral", "detail": f"Normal volume ({rvol}x avg)", "points": 1})
            score += 1

        # 4. MACD
        if macd is not None and macd_signal is not None:
            if macd > macd_signal:
                signals.append({"name": "MACD", "status": "bullish", "detail": f"MACD above signal", "points": 2})
                score += 2
            else:
                signals.append({"name": "MACD", "status": "bearish", "detail": f"MACD below signal", "points": 0})

        # 5. Bollinger Position
        if bb_upper is not None and bb_lower is not None:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_pos = (close - bb_lower) / bb_range
                if bb_pos > 0.8:
                    signals.append({"name": "Bollinger", "status": "bearish", "detail": "Near upper band", "points": 0})
                elif bb_pos < 0.2:
                    signals.append({"name": "Bollinger", "status": "bullish", "detail": "Near lower band", "points": 2})
                    score += 2
                else:
                    signals.append({"name": "Bollinger", "status": "neutral", "detail": "Within bands", "points": 1})
                    score += 1

        # 6. ATR (volatility)
        if atr is not None:
            avg_price = (high + low) / 2
            atr_pct = (atr / avg_price) * 100 if avg_price > 0 else 0
            if atr_pct < 1:
                signals.append({"name": "Volatility", "status": "bearish", "detail": f"Low ATR ({atr_pct:.1f}%)", "points": 0})
            elif atr_pct > 3:
                signals.append({"name": "Volatility", "status": "bullish", "detail": f"High ATR ({atr_pct:.1f}%)", "points": 2})
                score += 2
            else:
                signals.append({"name": "Volatility", "status": "neutral", "detail": f"Normal ATR ({atr_pct:.1f}%)", "points": 1})
                score += 1

        # 7. Price vs VWAP
        if vwap is not None:
            if close > vwap:
                signals.append({"name": "VWAP", "status": "bullish", "detail": f"Price above VWAP", "points": 2})
                score += 2
            else:
                signals.append({"name": "VWAP", "status": "bearish", "detail": f"Price below VWAP", "points": 0})

        # 8. Support/Resistance proximity
        dist_to_support = close - support_1
        dist_to_resist = resist_1 - close
        if dist_to_support > 0 and dist_to_support < (high_20d - low_20d) * 0.05:
            signals.append({"name": "Support", "status": "bullish", "detail": f"Near support ({support_1})", "points": 2})
            score += 2
        elif dist_to_resist > 0 and dist_to_resist < (high_20d - low_20d) * 0.05:
            signals.append({"name": "Resistance", "status": "bearish", "detail": f"Near resistance ({resist_1})", "points": 0})
        else:
            signals.append({"name": "S/R", "status": "neutral", "detail": "Away from key levels", "points": 1})
            score += 1

        # 9. Range position (20D)
        if range_pos > 70:
            signals.append({"name": "Range", "status": "bearish", "detail": f"At 20D high ({range_pos:.0f}%)", "points": 0})
        elif range_pos < 30:
            signals.append({"name": "Range", "status": "bullish", "detail": f"At 20D low ({range_pos:.0f}%)", "points": 2})
            score += 2
        else:
            signals.append({"name": "Range", "status": "neutral", "detail": f"Mid-range ({range_pos:.0f}%)", "points": 1})
            score += 1

        # 10. Momentum (5D change)
        if chg_5d > 2:
            signals.append({"name": "Momentum", "status": "bullish", "detail": f"Strong up ({chg_5d:.1f}%)", "points": 2})
            score += 2
        elif chg_5d < -2:
            signals.append({"name": "Momentum", "status": "bearish", "detail": f"Strong down ({chg_5d:.1f}%)", "points": 0})
        else:
            signals.append({"name": "Momentum", "status": "neutral", "detail": f"Neutral ({chg_5d:.1f}%)", "points": 1})
            score += 1

        # Signal grading
        signal_grade = "S" if score >= 18 else "A" if score >= 14 else "B" if score >= 10 else "C" if score >= 6 else "D"

        # 8. Confluence Score
        bullish_count = sum(1 for s in signals if s["status"] == "bullish")
        bearish_count = sum(1 for s in signals if s["status"] == "bearish")
        neutral_count = sum(1 for s in signals if s["status"] == "neutral")
        confluence_direction = "LONG" if bullish_count > bearish_count else "SHORT" if bearish_count > bullish_count else "NEUTRAL"

        confluence = {
            "bullish": bullish_count,
            "bearish": bearish_count,
            "neutral": neutral_count,
            "direction": confluence_direction
        }

        # 9. Trade Setup
        trade_setup = None
        if signal_grade in ['S', 'A']:
            entry = close
            # Stop = support_1 or low_20d (whichever is closer below)
            stop = max(support_1, low_20d) if support_1 > low_20d else support_1
            risk = entry - stop
            if risk > 0:
                tp1 = entry + risk
                tp2 = entry + risk * 1.618
                rr = (tp1 - entry) / risk if risk > 0 else 0
                direction = "LONG" if chg_5d > 0 else "SHORT"
                trade_setup = {
                    "entry": round(entry, 2),
                    "stop": round(stop, 2),
                    "tp1": round(tp1, 2),
                    "tp2": round(tp2, 2),
                    "rr": round(rr, 2),
                    "direction": direction
                }

        # 10. Candlestick data for chart (last 60 bars, reversed to chronological, with EMA overlays)
        candles = []
        bars_for_chart = list(reversed(bars[:60]))

        # Calculate EMA20 and EMA50 for each candle
        closes_reversed = list(reversed(closes[:60]))
        ema20_values = []
        ema50_values = []

        for i in range(len(closes_reversed)):
            slice_closes = closes_reversed[:i+1]
            e20 = calculate_ema(slice_closes, min(20, len(slice_closes)))
            e50 = calculate_ema(slice_closes, min(50, len(slice_closes)))
            ema20_values.append(e20)
            ema50_values.append(e50)

        for idx, b in enumerate(bars_for_chart):
            candle = {
                "t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0),
                "ema20": ema20_values[idx],
                "ema50": ema50_values[idx]
            }
            candles.append(candle)

        return {
            "ticker": ticker, "price": round(close, 2), "open": round(opn, 2),
            "high": round(high, 2), "low": round(low, 2), "volume": vol,
            "prev_close": round(prev_close, 2),
            "change_1d": chg_1d, "change_5d": chg_5d, "change_20d": chg_20d,
            "ma20": ma20, "ma50": ma50, "rvol": rvol, "rsi": rsi,
            "high_20d": high_20d, "low_20d": low_20d, "range_position": range_pos,
            "pivot": pivot, "support_1": support_1, "resistance_1": resist_1,
            "avg_volume": round(avg_vol),
            # New fields
            "ema9": ema9, "ema20": ema20, "ema50": ema50, "ema100": ema100, "ema200": ema200,
            "vwap": vwap,
            "macd": macd, "macd_signal": macd_signal, "macd_histogram": macd_histogram,
            "bb_upper": bb_upper, "bb_lower": bb_lower,
            "fib_levels": fib_levels,
            "atr": atr,
            "signals": signals, "signal_score": score, "signal_grade": signal_grade,
            "confluence": confluence,
            "trade_setup": trade_setup,
            "candles": candles,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart-data")
def get_chart_data(
    ticker: str = Query(..., description="Ticker symbol"),
    timeframe: str = Query("1D", description="5m, 15m, 1H, 4H, 1D, 1W"),
    overlays: str = Query("ema,vwap,sr,fib", description="Comma-separated: ema,vwap,sr,fib,patterns")
):
    """Get OHLCV data with chart overlays for TradingView Lightweight Charts."""
    try:
        # Fetch OHLCV bars for the requested timeframe
        ohlcv = fetch_ohlcv_for_chart(ticker, POLYGON_KEY, timeframe=timeframe, bars=300)
        if not ohlcv or len(ohlcv) < 5:
            raise HTTPException(status_code=404, detail=f"No chart data for '{ticker}' ({timeframe})")

        overlay_list = [x.strip().lower() for x in overlays.split(",")]
        result = {
            "ticker": ticker,
            "timeframe": timeframe,
            "candles": ohlcv,  # Already in {time, open, high, low, close, volume} format
        }

        closes = [bar["close"] for bar in ohlcv]
        highs = [bar["high"] for bar in ohlcv]
        lows = [bar["low"] for bar in ohlcv]
        volumes = [bar.get("volume", 0) for bar in ohlcv]
        times = [bar["time"] for bar in ohlcv]

        # EMA Overlays (as time-series for line drawing)
        if "ema" in overlay_list:
            ema_overlays = {}
            for period in [9, 20, 50, 100, 200]:
                if len(closes) >= period:
                    try:
                        ema_vals = calculate_ema_series(closes, period)
                        ema_data = []
                        start = len(times) - len(ema_vals)
                        for i, val in enumerate(ema_vals):
                            if val is not None:
                                ema_data.append({"time": times[start + i], "value": round(val, 2)})
                        if ema_data:
                            ema_overlays[f"ema{period}"] = ema_data
                    except Exception:
                        pass
            result["ema"] = ema_overlays

        # VWAP (cumulative per-bar)
        if "vwap" in overlay_list and len(ohlcv) >= 10:
            try:
                vwap_data = []
                cum_tp_vol = 0
                cum_vol = 0
                for bar in ohlcv:
                    tp = (bar["high"] + bar["low"] + bar["close"]) / 3
                    vol = bar.get("volume", 0)
                    cum_tp_vol += tp * vol
                    cum_vol += vol
                    if cum_vol > 0:
                        vwap_data.append({"time": bar["time"], "value": round(cum_tp_vol / cum_vol, 2)})
                result["vwap"] = vwap_data
            except Exception:
                pass

        # Support/Resistance levels
        if "sr" in overlay_list:
            try:
                last = ohlcv[-1]
                h = last["high"]
                l = last["low"]
                c = last["close"]
                pivot = round((h + l + c) / 3, 2)
                s1 = round(2 * pivot - h, 2)
                r1 = round(2 * pivot - l, 2)
                s2 = round(pivot - (h - l), 2)
                r2 = round(pivot + (h - l), 2)
                h20 = round(max(highs[-20:]), 2) if len(highs) >= 20 else round(max(highs), 2)
                l20 = round(min(lows[-20:]), 2) if len(lows) >= 20 else round(min(lows), 2)
                result["sr"] = {
                    "pivot": pivot, "s1": s1, "r1": r1, "s2": s2, "r2": r2,
                    "high_20": h20, "low_20": l20,
                }
            except Exception:
                pass

        # Fibonacci levels
        if "fib" in overlay_list and len(ohlcv) >= 20:
            try:
                h20 = max(highs[-20:])
                l20 = min(lows[-20:])
                rng = h20 - l20
                fib = {}
                for ratio in [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]:
                    fib[f"{int(ratio*100)}%"] = round(l20 + rng * ratio, 2)
                result["fib"] = fib
            except Exception:
                pass

        # Pattern detection (Harmonic, Chart Patterns, Order Blocks, Liquidity)
        if "patterns" in overlay_list and HAS_PATTERNS:
            try:
                # find_harmonic_for_chart needs {time, open, high, low, close, volume}
                # detect_chart_patterns / detect_order_blocks need same format
                # find_pivots needs {date, high, low, close}
                patterns_result = {}

                # Harmonic patterns
                try:
                    harmonics = find_harmonic_for_chart(ohlcv)
                    if harmonics:
                        patterns_result["harmonic"] = harmonics[:3]
                except Exception as e:
                    print(f"Harmonic error: {e}")

                # Chart patterns (Double Top/Bottom, H&S, Triangles)
                try:
                    chart_pats = detect_chart_patterns(ohlcv)
                    if chart_pats:
                        patterns_result["chart_patterns"] = chart_pats[:5]
                except Exception as e:
                    print(f"Chart patterns error: {e}")

                # Pivots (for marker annotations)
                try:
                    pivot_input = [{"date": str(b["time"]), "high": b["high"], "low": b["low"], "close": b["close"]} for b in ohlcv]
                    pivots_list = find_pivots(pivot_input, window=3)
                    if pivots_list:
                        # Add time reference for chart markers
                        for p in pivots_list:
                            if p.get("index") is not None and p["index"] < len(ohlcv):
                                p["time"] = ohlcv[p["index"]]["time"]
                        patterns_result["pivots"] = pivots_list[-15:]
                except Exception as e:
                    print(f"Pivots error: {e}")

                # Order blocks
                try:
                    obs = detect_order_blocks(ohlcv)
                    if obs:
                        patterns_result["order_blocks"] = obs
                except Exception as e:
                    print(f"Order blocks error: {e}")

                # Liquidity levels
                try:
                    liq = detect_liquidity_levels(ohlcv)
                    if liq:
                        patterns_result["liquidity"] = liq
                except Exception as e:
                    print(f"Liquidity error: {e}")

                result["patterns"] = patterns_result
            except Exception as e:
                print(f"Pattern detection error: {e}")

        # Bollinger Bands
        if len(closes) >= 20:
            try:
                bb_data = []
                for i in range(19, len(closes)):
                    window = closes[i-19:i+1]
                    ma = sum(window) / 20
                    std = (sum((x - ma) ** 2 for x in window) / 20) ** 0.5
                    bb_data.append({
                        "time": times[i],
                        "upper": round(ma + 2 * std, 2),
                        "middle": round(ma, 2),
                        "lower": round(ma - 2 * std, 2)
                    })
                result["bollinger"] = bb_data
            except Exception:
                pass

        # Volume data
        vol_data = [{"time": bar["time"], "value": bar.get("volume", 0), "color": "rgba(16,185,129,0.3)" if bar["close"] >= bar["open"] else "rgba(220,38,38,0.3)"} for bar in ohlcv]
        result["volume"] = vol_data

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ai-analysis")
def get_ai_analysis(ticker: str = Query(..., description="Ticker symbol")):
    """Generate AI analysis for a ticker using Claude."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not configured")

    # First get ticker data
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
        bars = resp.json().get("results", []) if resp.status_code == 200 else []

        price_info = ""
        if bars:
            close = bars[0]["c"]
            prev = bars[1]["c"] if len(bars) > 1 else close
            chg = round(((close - prev) / prev) * 100, 2)
            closes = [b["c"] for b in bars[:20]]
            ma20 = round(sum(closes) / len(closes), 2)
            high_20 = round(max(b["h"] for b in bars[:20]), 2)
            low_20 = round(min(b["l"] for b in bars[:20]), 2)
            vol = bars[0].get("v", 0)
            price_info = f"Preis: ${close}, Veraenderung: {chg}%, MA20: ${ma20}, 20d-Hoch: ${high_20}, 20d-Tief: ${low_20}, Vol: {vol}"
    except Exception:
        price_info = "Preisdaten nicht verfuegbar"

    # Call Claude API
    try:
        claude_resp = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": f"""Analysiere {ticker} als Trading-Setup. Aktuelle Daten: {price_info}

Antworte auf Deutsch, strukturiert:
1. TECHNISCHE EINSCHAETZUNG (2-3 Saetze: Trend, Momentum, Key Levels)
2. SIGNAL: BUY / SELL / WATCH (ein Wort)
3. TRADE SETUP: Entry, Stop Loss, Take Profit (konkrete Preise)
4. RISIKO: Niedrig / Mittel / Hoch
5. ZUSAMMENFASSUNG (1 Satz)

Kurz und praezise, keine langen Erklaerungen."""}],
            },
            timeout=30,
        )
        if claude_resp.status_code == 200:
            content = claude_resp.json().get("content", [{}])[0].get("text", "Analyse nicht verfuegbar")
            return {"ticker": ticker, "analysis": content, "model": "claude-sonnet-4-20250514", "timestamp": datetime.now().isoformat()}
        else:
            return {"ticker": ticker, "analysis": f"API Fehler: {claude_resp.status_code}", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"ticker": ticker, "analysis": f"Fehler: {str(e)}", "timestamp": datetime.now().isoformat()}


@app.get("/api/strategies", response_model=StrategiesResponse)
def list_strategies(market_type: str = Query("stocks", description="Market type: stocks, crypto, futures, forex")):
    """List all strategies for a given market type."""
    strategies = get_strategies_for_market(market_type)

    return StrategiesResponse(
        market_type=market_type,
        strategies=strategies,
        count=len(strategies),
    )


@app.post("/api/scan")
def run_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    """
    Run main scanner with specified strategy and market type.

    For now, delegates to BI background scan since fetch_stock_data has Streamlit dependencies.
    """
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    # Validate strategy
    strategies = get_strategies_for_market(request.market_type)
    if request.strategy not in strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{request.strategy}' for market '{request.market_type}'"
        )

    # Use BI scanner as primary scan
    direction = "long" if "long" in request.strategy.lower() else "short"
    background_tasks.add_task(_bi_background_scan_wrapper, direction)

    return {
        "status": "started",
        "message": f"Scan started for strategy '{request.strategy}' ({direction})",
        "strategy": request.strategy,
        "direction": direction,
    }


@app.get("/api/scan-results", response_model=ScanResultsResponse)
def get_scan_results(direction: str = Query("long", description="long or short")):
    """Get cached scan results (delegates to BI cache since main scan uses BI scanner)."""
    if direction not in ["long", "short"]:
        raise HTTPException(status_code=400, detail="Direction must be 'long' or 'short'")

    cache_file = BI_CACHE_LONG if direction == "long" else BI_CACHE_SHORT
    results, cached_at = load_cache_file(cache_file)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception:
            pass

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
    )


@app.post("/api/bi-scan")
def trigger_bi_scan(request: BIScanRequest, background_tasks: BackgroundTasks):
    """Trigger BI background scan (long or short direction)."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    if request.direction not in ["long", "short"]:
        raise HTTPException(status_code=400, detail="Direction must be 'long' or 'short'")

    background_tasks.add_task(_bi_background_scan_wrapper, request.direction)

    return {
        "status": "started",
        "message": f"BI scan started ({request.direction})",
        "direction": request.direction,
    }


@app.get("/api/bi-results", response_model=ScanResultsResponse)
def get_bi_results(direction: str = Query("long", description="long or short")):
    """Get cached BI scan results."""
    if direction not in ["long", "short"]:
        raise HTTPException(status_code=400, detail="Direction must be 'long' or 'short'")

    cache_file = BI_CACHE_LONG if direction == "long" else BI_CACHE_SHORT
    results, cached_at = load_cache_file(cache_file)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception:
            pass

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
    )


@app.post("/api/bear-scan")
def trigger_bear_scan(background_tasks: BackgroundTasks):
    """Trigger bear scanner (short opportunities)."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    background_tasks.add_task(_bear_scan_wrapper)

    return {
        "status": "started",
        "message": "Bear scan started",
    }


@app.get("/api/bear-results", response_model=ScanResultsResponse)
def get_bear_results():
    """Get cached bear scan results."""
    results, cached_at = load_cache_file(BEAR_CACHE)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception:
            pass

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
    )


@app.post("/api/biotech-scan")
def trigger_biotech_scan(background_tasks: BackgroundTasks):
    """Trigger biotech background scan (FDA catalysts, clinical trials)."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    background_tasks.add_task(_biotech_scan_wrapper)

    return {
        "status": "started",
        "message": "Biotech scan started",
    }


@app.get("/api/biotech-results", response_model=ScanResultsResponse)
def get_biotech_results():
    """Get cached biotech scan results."""
    results, cached_at = load_cache_file(BIOTECH_CACHE)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception:
            pass

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
    )


# ── Early Movers ──
EARLY_MOVERS_CACHE = "/tmp/early_movers_cache.json"

def _early_movers_wrapper() -> None:
    """Fetch pre/post market movers via Polygon snapshot."""
    try:
        url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/gainers"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY})
        gainers = []
        if resp.status_code == 200:
            for t in resp.json().get("tickers", [])[:20]:
                day = t.get("day", {})
                prev = t.get("prevDay", {})
                price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
                prev_c = prev.get("c", 0)
                chg = ((price - prev_c) / prev_c * 100) if prev_c else 0
                vol = day.get("v", 0)
                gainers.append({"ticker": t.get("ticker",""), "price": round(price,2), "change_pct": round(chg,2), "volume": vol})

        url2 = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/losers"
        resp2 = rate_limited_get(url2, params={"apiKey": POLYGON_KEY})
        losers = []
        if resp2.status_code == 200:
            for t in resp2.json().get("tickers", [])[:20]:
                day = t.get("day", {})
                prev = t.get("prevDay", {})
                price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
                prev_c = prev.get("c", 0)
                chg = ((price - prev_c) / prev_c * 100) if prev_c else 0
                vol = day.get("v", 0)
                losers.append({"ticker": t.get("ticker",""), "price": round(price,2), "change_pct": round(chg,2), "volume": vol})

        save_cache_file(EARLY_MOVERS_CACHE, [{"gainers": gainers, "losers": losers}])
    except Exception as e:
        print(f"Early movers error: {e}")


@app.post("/api/early-movers-scan")
def trigger_early_movers(background_tasks: BackgroundTasks):
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
    background_tasks.add_task(_early_movers_wrapper)
    return {"status": "started", "message": "Early Movers scan started"}


@app.get("/api/early-movers-results")
def get_early_movers():
    results, cached_at = load_cache_file(EARLY_MOVERS_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── Crash Monitor (VIX + Market Breadth) + Fear Score ──
# Note: _crash_monitor_wrapper is defined later with fear score functionality
CRASH_MONITOR_CACHE = "/tmp/crash_monitor_cache.json"


@app.post("/api/crash-monitor-scan")
def trigger_crash_monitor(background_tasks: BackgroundTasks):
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
    background_tasks.add_task(_crash_monitor_wrapper)
    return {"status": "started", "message": "Crash monitor scan started"}


@app.get("/api/crash-monitor-results")
def get_crash_monitor():
    results, cached_at = load_cache_file(CRASH_MONITOR_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── BTC Divergenz ──
BTC_DIVERGENZ_CACHE = "/tmp/btc_divergenz_cache.json"

def _btc_divergenz_wrapper() -> None:
    """Compare BTC vs correlated assets for divergence signals."""
    try:
        assets = [
            ("X:BTCUSD", "BTC", "Bitcoin"),
            ("X:ETHUSD", "ETH", "Ethereum"),
            ("MSTR", "MSTR", "MicroStrategy"),
            ("COIN", "COIN", "Coinbase"),
            ("MARA", "MARA", "Marathon Digital"),
            ("RIOT", "RIOT", "Riot Platforms"),
            ("CLSK", "CLSK", "CleanSpark"),
            ("BITF", "BITF", "Bitfarms"),
            ("GBTC", "GBTC", "Grayscale BTC Trust"),
        ]
        results = []
        btc_data = None

        for sym, short, name in assets:
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/2024-01-01/2099-12-31"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if len(bars) < 2:
                    continue
                close = bars[0]["c"]
                prev = bars[1]["c"]
                chg_1d = ((close - prev) / prev) * 100
                chg_5d = ((close - bars[min(5, len(bars)-1)]["c"]) / bars[min(5, len(bars)-1)]["c"]) * 100 if len(bars) > 5 else 0
                chg_20d = ((close - bars[min(20, len(bars)-1)]["c"]) / bars[min(20, len(bars)-1)]["c"]) * 100 if len(bars) > 20 else 0

                entry = {"ticker": sym, "symbol": short, "name": name,
                         "price": round(close, 2), "change_1d": round(chg_1d, 2),
                         "change_5d": round(chg_5d, 2), "change_20d": round(chg_20d, 2)}

                if sym == "X:BTCUSD":
                    btc_data = entry
                results.append(entry)
            except Exception:
                continue

        # Calculate divergence vs BTC
        if btc_data:
            for r in results:
                if r["ticker"] != "X:BTCUSD":
                    r["div_1d"] = round(r["change_1d"] - btc_data["change_1d"], 2)
                    r["div_5d"] = round(r["change_5d"] - btc_data["change_5d"], 2)
                    # Signal - actionable Labels
                    div = r["div_5d"]
                    if div > 5:
                        r["signal"] = "KAUFEN"
                    elif div < -5:
                        r["signal"] = "MEIDEN"
                    elif abs(div) < 2:
                        r["signal"] = "ABWARTEN"
                    else:
                        r["signal"] = "BEOBACHTEN"
                else:
                    r["div_1d"] = 0
                    r["div_5d"] = 0
                    r["signal"] = "BTC"

        save_cache_file(BTC_DIVERGENZ_CACHE, results)
    except Exception as e:
        print(f"BTC divergenz error: {e}")


@app.post("/api/btc-divergenz-scan")
def trigger_btc_divergenz(background_tasks: BackgroundTasks):
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
    background_tasks.add_task(_btc_divergenz_wrapper)
    return {"status": "started", "message": "BTC Divergenz scan started"}


@app.get("/api/btc-divergenz-results")
def get_btc_divergenz():
    results, cached_at = load_cache_file(BTC_DIVERGENZ_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── Money Flow (Sector Performance) ──
MONEY_FLOW_CACHE = "/tmp/money_flow_cache.json"

SECTOR_ETFS = {
    "XLK": "Technologie", "XLF": "Finanzen", "XLV": "Gesundheit",
    "XLE": "Energie", "XLI": "Industrie", "XLY": "Konsum (zyklisch)",
    "XLP": "Konsum (defensiv)", "XLU": "Versorger", "XLRE": "Immobilien",
    "XLB": "Grundstoffe", "XLC": "Kommunikation",
}

def _money_flow_wrapper() -> None:
    """Fetch sector ETF performance for money flow analysis."""
    try:
        sectors = []
        for ticker, name in SECTOR_ETFS.items():
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if len(bars) < 2:
                    continue
                close = bars[0]["c"]
                prev = bars[1]["c"]
                chg_1d = ((close - prev) / prev) * 100
                chg_5d = ((close - bars[min(5, len(bars)-1)]["c"]) / bars[min(5, len(bars)-1)]["c"]) * 100 if len(bars) > 5 else 0
                chg_20d = ((close - bars[min(20, len(bars)-1)]["c"]) / bars[min(20, len(bars)-1)]["c"]) * 100 if len(bars) > 20 else 0

                vol = bars[0].get("v", 0)
                avg_vol = sum(b.get("v", 0) for b in bars[1:11]) / min(len(bars)-1, 10) if len(bars) > 1 else 1
                rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0

                # Flow signal
                if chg_5d > 2 and rvol > 1.2:
                    flow = "ZUFLUSS"
                elif chg_5d < -2 and rvol > 1.2:
                    flow = "ABFLUSS"
                elif rvol > 1.5:
                    flow = "HOHE AKTIVITAET"
                else:
                    flow = "NEUTRAL"

                sectors.append({
                    "ticker": ticker, "sector": name, "price": round(close, 2),
                    "change_1d": round(chg_1d, 2), "change_5d": round(chg_5d, 2),
                    "change_20d": round(chg_20d, 2), "volume": vol, "rvol": rvol,
                    "flow_signal": flow,
                })
            except Exception:
                continue

        sectors.sort(key=lambda x: x.get("change_5d", 0), reverse=True)
        save_cache_file(MONEY_FLOW_CACHE, sectors)
    except Exception as e:
        print(f"Money flow error: {e}")


@app.post("/api/money-flow-scan")
def trigger_money_flow(background_tasks: BackgroundTasks):
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
    background_tasks.add_task(_money_flow_wrapper)
    return {"status": "started", "message": "Money Flow scan started"}


@app.get("/api/money-flow-results")
def get_money_flow():
    results, cached_at = load_cache_file(MONEY_FLOW_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── New Listing Scanner ──
NEW_LISTING_CACHE = "/tmp/new_listing_scanner.json"

def _new_listing_wrapper() -> None:
    """Detect new listings and calculate exhaustion scores."""
    if not HAS_NEW_LISTING_SCANNER:
        print("[New Listing] Module not available")
        return

    try:
        Path("/opt/tradingbot/data_cache").mkdir(parents=True, exist_ok=True)

        # Detect new listings
        new_listings, all_perps = detect_new_listings()
        if not new_listings:
            print("[New Listing] No new listings detected")
            save_cache_file(NEW_LISTING_CACHE, [])
            return

        results = []
        for listing in new_listings[:20]:  # Limit to 20
            try:
                symbol = listing.get("symbol", "")
                exchange = listing.get("exchange", "crypto_com")

                # Fetch ticker data
                ticker_data = fetch_ticker_for(symbol, exchange)
                if not ticker_data:
                    continue

                # Fetch candles
                candles = fetch_candles_for(symbol, exchange)

                # Fetch orderbook
                orderbook = fetch_cryptocom_orderbook(f"{symbol}_PERP") if exchange == "crypto_com" else None

                # Calculate exhaustion
                exhaustion_score, exhaustion_details, pump_data = calculate_listing_exhaustion(
                    candles or [], symbol, orderbook
                )

                results.append({
                    "symbol": symbol,
                    "exchange": exchange,
                    "price": ticker_data.get("price", 0),
                    "change_24h": ticker_data.get("change_24h", 0),
                    "volume_24h": ticker_data.get("volume_24h", 0),
                    "market_cap": ticker_data.get("market_cap", 0),
                    "exhaustion_score": exhaustion_score,
                    "exhaustion_details": exhaustion_details,
                    "pump_data": pump_data,
                    "listing_date": listing.get("listing_date"),
                    "time_since_listing_hours": listing.get("time_since_listing_hours"),
                })
            except Exception as e:
                print(f"[New Listing] Error processing {listing.get('symbol', 'unknown')}: {e}")
                continue

        save_cache_file(NEW_LISTING_CACHE, results)
        print(f"[New Listing] Processed {len(results)} new listings")
    except Exception as e:
        print(f"New listing wrapper error: {e}")


@app.post("/api/new-listing-scan")
def trigger_new_listing_scan(background_tasks: BackgroundTasks):
    """Trigger new listing scanner (Crypto.com and other exchanges)."""
    if not HAS_NEW_LISTING_SCANNER:
        raise HTTPException(status_code=400, detail="New listing scanner module not available")

    background_tasks.add_task(_new_listing_wrapper)
    return {"status": "started", "message": "New Listing scan started"}


@app.get("/api/new-listing-results")
def get_new_listing_results():
    """Get cached new listing scan results."""
    results, cached_at = load_cache_file(NEW_LISTING_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── Volume Spikes Scanner ──
VOLUME_SPIKES_CACHE = "/tmp/volume_spikes_cache.json"

def _volume_spikes_wrapper() -> None:
    """Find stocks with unusual volume (RVOL > 3.0, price > $2)."""
    try:
        # Fetch market snapshot
        snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY})

        spikes = []
        if snap_resp.status_code == 200:
            tickers = snap_resp.json().get("tickers", [])
            for t in tickers:
                try:
                    day = t.get("day", {})
                    prev = t.get("prevDay", {})

                    price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
                    if not price or price < 2:
                        continue

                    vol = day.get("v", 0)
                    prev_vol = prev.get("v", 0)

                    # Calculate RVOL
                    if prev_vol > 0:
                        rvol = vol / prev_vol
                    else:
                        continue

                    if rvol > 3.0:
                        prev_close = prev.get("c", 0)
                        chg = ((price - prev_close) / prev_close * 100) if prev_close else 0

                        spikes.append({
                            "ticker": t.get("ticker", ""),
                            "price": round(price, 2),
                            "change_pct": round(chg, 2),
                            "volume": vol,
                            "rvol": round(rvol, 2),
                            "dollar_volume": round(price * vol, 0),
                        })
                except Exception:
                    continue

        # Sort by RVOL descending
        spikes.sort(key=lambda x: x.get("rvol", 0), reverse=True)
        save_cache_file(VOLUME_SPIKES_CACHE, spikes[:50])  # Keep top 50
    except Exception as e:
        print(f"Volume spikes error: {e}")


@app.post("/api/volume-spikes-scan")
def trigger_volume_spikes(background_tasks: BackgroundTasks):
    """Trigger volume spikes scanner."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    background_tasks.add_task(_volume_spikes_wrapper)
    return {"status": "started", "message": "Volume Spikes scan started"}


@app.get("/api/volume-spikes-results")
def get_volume_spikes():
    """Get cached volume spikes results."""
    results, cached_at = load_cache_file(VOLUME_SPIKES_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── Fear Score (12-factor) ──
def _calculate_fear_score(vix_data: Dict, breadth_data: Dict, indices_data: List[Dict]) -> tuple[int, str, Dict]:
    """
    Calculate comprehensive fear/panic score (0-100).
    Returns (score, fear_level_string, details_dict)
    """
    score = 0
    details = {}

    # 1. VIX Level (0-8 points)
    if vix_data and "price" in vix_data:
        vix = vix_data["price"]
        if vix >= 30:
            score += 8
            details["vix_level"] = 8
        elif vix >= 25:
            score += 6
            details["vix_level"] = 6
        elif vix >= 20:
            score += 4
            details["vix_level"] = 4
        elif vix >= 15:
            score += 2
            details["vix_level"] = 2
        else:
            details["vix_level"] = 0

    # 2. VIX Change 1D (0-8 points)
    if vix_data and "change_1d" in vix_data:
        vix_chg_1d = abs(vix_data["change_1d"])
        if vix_chg_1d > 10:
            score += 8
            details["vix_change_1d"] = 8
        elif vix_chg_1d > 5:
            score += 6
            details["vix_change_1d"] = 6
        elif vix_chg_1d > 2:
            score += 4
            details["vix_change_1d"] = 4
        else:
            details["vix_change_1d"] = 0

    # 3. VIX Change 5D (0-8 points)
    if vix_data and "change_5d" in vix_data:
        vix_chg_5d = abs(vix_data["change_5d"])
        if vix_chg_5d > 20:
            score += 8
            details["vix_change_5d"] = 8
        elif vix_chg_5d > 10:
            score += 6
            details["vix_change_5d"] = 6
        elif vix_chg_5d > 5:
            score += 4
            details["vix_change_5d"] = 4
        else:
            details["vix_change_5d"] = 0

    # Helper to find index by ticker
    def get_index_data(ticker):
        for idx in indices_data:
            if idx.get("ticker") == ticker:
                return idx
        return None

    spy_data = get_index_data("SPY")
    qqq_data = get_index_data("QQQ")
    dia_data = get_index_data("DIA")
    iwm_data = get_index_data("IWM")

    # 4. S&P 500 Change 1D (0-8 points)
    if spy_data and "change_1d" in spy_data:
        spy_chg_1d = spy_data["change_1d"]
        if spy_chg_1d < -2:
            score += 8
            details["spy_change_1d"] = 8
        elif spy_chg_1d < -1:
            score += 6
            details["spy_change_1d"] = 6
        elif spy_chg_1d < -0.5:
            score += 4
            details["spy_change_1d"] = 4
        else:
            details["spy_change_1d"] = 0

    # 5. S&P 500 Change 5D (0-8 points)
    if spy_data and "change_5d" in spy_data:
        spy_chg_5d = spy_data["change_5d"]
        if spy_chg_5d < -5:
            score += 8
            details["spy_change_5d"] = 8
        elif spy_chg_5d < -3:
            score += 6
            details["spy_change_5d"] = 6
        elif spy_chg_5d < -1:
            score += 4
            details["spy_change_5d"] = 4
        else:
            details["spy_change_5d"] = 0

    # 6. Nasdaq Change 1D (0-8 points)
    if qqq_data and "change_1d" in qqq_data:
        qqq_chg_1d = qqq_data["change_1d"]
        if qqq_chg_1d < -2:
            score += 8
            details["qqq_change_1d"] = 8
        elif qqq_chg_1d < -1:
            score += 6
            details["qqq_change_1d"] = 6
        elif qqq_chg_1d < -0.5:
            score += 4
            details["qqq_change_1d"] = 4
        else:
            details["qqq_change_1d"] = 0

    # 7. Nasdaq Change 5D (0-8 points)
    if qqq_data and "change_5d" in qqq_data:
        qqq_chg_5d = qqq_data["change_5d"]
        if qqq_chg_5d < -5:
            score += 8
            details["qqq_change_5d"] = 8
        elif qqq_chg_5d < -3:
            score += 6
            details["qqq_change_5d"] = 6
        elif qqq_chg_5d < -1:
            score += 4
            details["qqq_change_5d"] = 4
        else:
            details["qqq_change_5d"] = 0

    # 8. A/D Ratio (0-8 points)
    if breadth_data and "ad_ratio" in breadth_data:
        ad_ratio = breadth_data["ad_ratio"]
        if ad_ratio < 0.5:
            score += 8
            details["ad_ratio"] = 8
        elif ad_ratio < 0.7:
            score += 6
            details["ad_ratio"] = 6
        elif ad_ratio < 1.0:
            score += 4
            details["ad_ratio"] = 4
        else:
            details["ad_ratio"] = 0

    # 9. Russell vs S&P divergence (0-8 points)
    if iwm_data and spy_data:
        iwm_chg = iwm_data.get("change_5d", 0)
        spy_chg = spy_data.get("change_5d", 0)
        divergence = spy_chg - iwm_chg  # If SPY up and IWM down = positive divergence
        if divergence > 5:  # IWM significantly underperforming
            score += 8
            details["russell_spy_div"] = 8
        elif divergence > 3:
            score += 6
            details["russell_spy_div"] = 6
        elif divergence > 1:
            score += 4
            details["russell_spy_div"] = 4
        else:
            details["russell_spy_div"] = 0

    # 10. Count indices with negative 5D change (0-8 points)
    negative_count = 0
    for idx in indices_data:
        if idx.get("change_5d", 0) < 0:
            negative_count += 1

    if negative_count == 4:
        score += 8
        details["negative_indices"] = 8
    elif negative_count == 3:
        score += 6
        details["negative_indices"] = 6
    elif negative_count == 2:
        score += 4
        details["negative_indices"] = 4
    else:
        details["negative_indices"] = 0

    # 11. VIX term structure (skip for now - would need futures data)
    details["vix_term_structure"] = 0

    # 12. Consecutive red days for S&P (0-8 points)
    # This would require historical data - estimate from 1D and 5D
    if spy_data:
        spy_1d = spy_data.get("change_1d", 0)
        if spy_1d < 0:  # Red today
            score += 4  # Assume multiple red days if in downtrend
            details["consecutive_red_days"] = 4
        else:
            details["consecutive_red_days"] = 0

    # Cap at 100
    score = min(score, 100)

    # Determine fear level
    if score >= 80:
        fear_level = "PANIK"
    elif score >= 60:
        fear_level = "ANGST"
    elif score >= 40:
        fear_level = "NEUTRAL"
    elif score >= 20:
        fear_level = "OPTIMISMUS"
    else:
        fear_level = "GIER"

    return score, fear_level, details


# Update crash monitor to include fear score
def _crash_monitor_wrapper() -> None:
    """Fetch VIX, major indices, and market breadth data with fear score."""
    try:
        result = {"vix": {}, "indices": [], "breadth": {}, "fear_score": 0, "fear_level": ""}

        # VIX via Polygon
        vix_tickers = [
            ("I:VIX", "VIX", "Volatility Index"),
            ("SPY", "S&P 500", "S&P 500 ETF"),
            ("QQQ", "Nasdaq", "Nasdaq 100 ETF"),
            ("DIA", "Dow Jones", "Dow Jones ETF"),
            ("IWM", "Russell 2000", "Russell 2000 ETF"),
        ]

        for sym, name, desc in vix_tickers:
            try:
                if sym.startswith("I:"):
                    url = f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/2024-01-01/2099-12-31"
                else:
                    url = f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/2024-01-01/2099-12-31"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if len(bars) < 2:
                    continue
                close = bars[0]["c"]
                prev = bars[1]["c"]
                chg_1d = ((close - prev) / prev) * 100
                chg_5d = ((close - bars[min(5, len(bars)-1)]["c"]) / bars[min(5, len(bars)-1)]["c"]) * 100 if len(bars) > 5 else 0
                chg_20d = ((close - bars[min(20, len(bars)-1)]["c"]) / bars[min(20, len(bars)-1)]["c"]) * 100 if len(bars) > 20 else 0

                entry = {"ticker": sym, "name": name, "description": desc,
                         "price": round(close, 2), "change_1d": round(chg_1d, 2),
                         "change_5d": round(chg_5d, 2), "change_20d": round(chg_20d, 2)}

                if sym == "I:VIX":
                    result["vix"] = entry
                    # Stress level
                    if close >= 30:
                        entry["level"] = "EXTREME"
                    elif close >= 25:
                        entry["level"] = "HIGH"
                    elif close >= 20:
                        entry["level"] = "ELEVATED"
                    else:
                        entry["level"] = "LOW"
                else:
                    result["indices"].append(entry)
            except Exception:
                continue

        # Market breadth - count gainers vs losers via snapshot
        try:
            snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY})
            if snap_resp.status_code == 200:
                tickers = snap_resp.json().get("tickers", [])
                up = 0
                down = 0
                unchanged = 0
                for t in tickers:
                    day = t.get("day", {})
                    prev = t.get("prevDay", {})
                    pc = day.get("c", 0)
                    pp = prev.get("c", 0)
                    if pc and pp:
                        if pc > pp:
                            up += 1
                        elif pc < pp:
                            down += 1
                        else:
                            unchanged += 1
                total = up + down + unchanged
                ratio = round(up / down, 2) if down > 0 else 0
                result["breadth"] = {
                    "advancing": up, "declining": down, "unchanged": unchanged,
                    "total": total, "ad_ratio": ratio,
                    "breadth_signal": "BULLISH" if ratio > 1.5 else "BEARISH" if ratio < 0.7 else "NEUTRAL"
                }
        except Exception:
            pass

        # Calculate fear score
        fear_score, fear_level, fear_details = _calculate_fear_score(
            result.get("vix", {}),
            result.get("breadth", {}),
            result.get("indices", [])
        )
        result["fear_score"] = fear_score
        result["fear_level"] = fear_level
        result["fear_details"] = fear_details

        save_cache_file(CRASH_MONITOR_CACHE, [result])
    except Exception as e:
        print(f"Crash monitor error: {e}")


# ── Kalender (Economic Calendar) ──
def _calculate_next_occurrence(month: int, day: int) -> str:
    """Calculate next occurrence of a recurring event."""
    from datetime import date, timedelta
    today = date.today()
    next_date = date(today.year, month, day)
    if next_date < today:
        next_date = date(today.year + 1, month, day)
    return next_date.isoformat()


@app.get("/api/kalender")
def get_economic_calendar():
    """Get upcoming economic events and important dates."""
    try:
        events = []

        # Major recurring events (simplified - hardcoded with dynamic dates)
        # In production, would fetch from economic calendar API

        # FOMC meetings (typically 8 per year, Jan/Mar/May/Jun/Jul/Sep/Nov/Dec)
        fomc_months = [1, 3, 5, 6, 7, 9, 11, 12]
        fomc_day = 14  # Approximate
        for month in fomc_months:
            try:
                next_date = _calculate_next_occurrence(month, fomc_day)
                events.append({
                    "date": next_date,
                    "event": "FOMC Meeting",
                    "importance": "high",
                    "description": "Federal Reserve Interest Rate Decision",
                    "impact": "Sehr Hoch"
                })
            except Exception:
                pass

        # CPI (1st week of each month, reported ~12 days after month end)
        try:
            next_cpi = _calculate_next_occurrence(4, 10)  # Approximate next
            events.append({
                "date": next_cpi,
                "event": "CPI (Verbraucherpreisindex)",
                "importance": "high",
                "description": "US Consumer Price Index YoY",
                "impact": "Sehr Hoch"
            })
        except Exception:
            pass

        # NFP (1st Friday of each month)
        try:
            next_nfp = _calculate_next_occurrence(4, 3)  # Approximate
            events.append({
                "date": next_nfp,
                "event": "NFP (Non-Farm Payroll)",
                "importance": "high",
                "description": "US Employment Report",
                "impact": "Sehr Hoch"
            })
        except Exception:
            pass

        # GDP (end of each quarter)
        for month in [3, 6, 9, 12]:
            try:
                next_gdp = _calculate_next_occurrence(month, 28)
                events.append({
                    "date": next_gdp,
                    "event": "GDP",
                    "importance": "high",
                    "description": "Gross Domestic Product Report",
                    "impact": "Sehr Hoch"
                })
            except Exception:
                pass

        # Earnings seasons (approximate)
        earnings_months = [4, 7, 10, 1]  # Q1, Q2, Q3, Q4
        for month in earnings_months:
            try:
                next_earnings = _calculate_next_occurrence(month, 15)
                events.append({
                    "date": next_earnings,
                    "event": "Earnings Season",
                    "importance": "high",
                    "description": "Corporate Earnings Reports",
                    "impact": "Hoch"
                })
            except Exception:
                pass

        # Fed Fund Rate Decision (typically day 14)
        try:
            next_rate = _calculate_next_occurrence(3, 18)
            events.append({
                "date": next_rate,
                "event": "Fed Funds Rate Decision",
                "importance": "high",
                "description": "Federal Reserve Interest Rate Announcement",
                "impact": "Sehr Hoch"
            })
        except Exception:
            pass

        # Sort by date
        events.sort(key=lambda x: x["date"])

        return {
            "status": "success",
            "events": events,
            "timestamp": datetime.now().isoformat(),
            "note": "Simplified calendar - dates are approximate for recurring events"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }


# ── Backtest Engine ──
BACKTEST_CACHE = "/tmp/backtest_cache.json"


class BacktestRequest(BaseModel):
    ticker: str = "AAPL"
    strategy: str = "sma_crossover"  # sma_crossover, rsi_mean_reversion, ema_crossover
    months: int = 6


def _run_backtest(ticker: str, strategy: str, months: int) -> Dict:
    """Run a simple backtest on historical data."""
    try:
        # Fetch daily bars
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": months * 22 + 60, "sort": "desc"})
        if resp.status_code != 200:
            return {"error": f"Keine Daten fuer {ticker}"}
        bars = resp.json().get("results", [])
        if len(bars) < 60:
            return {"error": f"Zu wenige Daten fuer {ticker} ({len(bars)} Bars)"}

        # Reverse to chronological
        bars = list(reversed(bars))
        closes = [b["c"] for b in bars]
        dates = [datetime.fromtimestamp(b["t"] / 1000).strftime("%Y-%m-%d") for b in bars]

        trades = []
        position = None  # None = no position, dict = open position

        if strategy == "sma_crossover":
            # SMA20/SMA50 crossover
            for i in range(50, len(closes)):
                sma20 = sum(closes[i-20:i]) / 20
                sma50 = sum(closes[i-50:i]) / 50
                prev_sma20 = sum(closes[i-21:i-1]) / 20
                prev_sma50 = sum(closes[i-51:i-1]) / 50

                if position is None:
                    # Buy signal: SMA20 crosses above SMA50
                    if prev_sma20 <= prev_sma50 and sma20 > sma50:
                        position = {"entry_date": dates[i], "entry_price": closes[i]}
                else:
                    # Sell signal: SMA20 crosses below SMA50
                    if prev_sma20 >= prev_sma50 and sma20 < sma50:
                        pnl = ((closes[i] - position["entry_price"]) / position["entry_price"]) * 100
                        trades.append({
                            "entry_date": position["entry_date"],
                            "entry_price": round(position["entry_price"], 2),
                            "exit_date": dates[i],
                            "exit_price": round(closes[i], 2),
                            "pnl_pct": round(pnl, 2),
                            "type": "LONG"
                        })
                        position = None

        elif strategy == "rsi_mean_reversion":
            # RSI oversold/overbought mean reversion
            for i in range(15, len(closes)):
                gains, losses = [], []
                for j in range(14):
                    diff = closes[i-j] - closes[i-j-1]
                    if diff > 0:
                        gains.append(diff)
                    else:
                        losses.append(abs(diff))
                avg_gain = sum(gains) / 14 if gains else 0.001
                avg_loss = sum(losses) / 14 if losses else 0.001
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))

                if position is None:
                    if rsi < 30:  # Oversold → Buy
                        position = {"entry_date": dates[i], "entry_price": closes[i]}
                else:
                    if rsi > 70:  # Overbought → Sell
                        pnl = ((closes[i] - position["entry_price"]) / position["entry_price"]) * 100
                        trades.append({
                            "entry_date": position["entry_date"],
                            "entry_price": round(position["entry_price"], 2),
                            "exit_date": dates[i],
                            "exit_price": round(closes[i], 2),
                            "pnl_pct": round(pnl, 2),
                            "type": "LONG"
                        })
                        position = None

        elif strategy == "ema_crossover":
            # EMA9/EMA21 crossover (faster signals)
            def calc_ema_series(data, period):
                emas = [sum(data[:period]) / period]
                k = 2 / (period + 1)
                for val in data[period:]:
                    emas.append(val * k + emas[-1] * (1 - k))
                return emas

            if len(closes) > 21:
                ema9 = calc_ema_series(closes, 9)
                ema21 = calc_ema_series(closes, 21)
                # Align: ema9 starts at index 8, ema21 starts at index 20
                for i in range(1, min(len(ema9), len(ema21))):
                    bar_idx = 20 + i  # offset for ema21 start
                    if bar_idx >= len(dates):
                        break
                    if position is None:
                        if i > 0 and ema9[i + 12] > ema21[i] and ema9[i + 11] <= ema21[i - 1]:
                            position = {"entry_date": dates[bar_idx], "entry_price": closes[bar_idx]}
                    else:
                        if i > 0 and ema9[i + 12] < ema21[i] and ema9[i + 11] >= ema21[i - 1]:
                            pnl = ((closes[bar_idx] - position["entry_price"]) / position["entry_price"]) * 100
                            trades.append({
                                "entry_date": position["entry_date"],
                                "entry_price": round(position["entry_price"], 2),
                                "exit_date": dates[bar_idx],
                                "exit_price": round(closes[bar_idx], 2),
                                "pnl_pct": round(pnl, 2),
                                "type": "LONG"
                            })
                            position = None

        # Close any open position at last bar
        if position is not None:
            pnl = ((closes[-1] - position["entry_price"]) / position["entry_price"]) * 100
            trades.append({
                "entry_date": position["entry_date"],
                "entry_price": round(position["entry_price"], 2),
                "exit_date": dates[-1],
                "exit_price": round(closes[-1], 2),
                "pnl_pct": round(pnl, 2),
                "type": "LONG (offen)"
            })

        # Calculate statistics
        total_trades = len(trades)
        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses_list = [t for t in trades if t["pnl_pct"] <= 0]
        win_rate = round(len(wins) / total_trades * 100, 1) if total_trades > 0 else 0
        avg_pnl = round(sum(t["pnl_pct"] for t in trades) / total_trades, 2) if total_trades > 0 else 0
        total_return = round(sum(t["pnl_pct"] for t in trades), 2)
        avg_win = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0
        avg_loss = round(sum(t["pnl_pct"] for t in losses_list) / len(losses_list), 2) if losses_list else 0
        best_trade = round(max(t["pnl_pct"] for t in trades), 2) if trades else 0
        worst_trade = round(min(t["pnl_pct"] for t in trades), 2) if trades else 0

        # Max drawdown
        max_dd = 0
        peak = 0
        equity = 0
        for t in trades:
            equity += t["pnl_pct"]
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        max_drawdown = round(max_dd, 2)

        return {
            "ticker": ticker,
            "strategy": strategy,
            "months": months,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "trades": trades[-50:],  # Last 50 trades max
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "ticker": ticker, "strategy": strategy}


@app.post("/api/backtest")
def run_backtest(request: BacktestRequest, background_tasks: BackgroundTasks):
    """Run a backtest for a ticker with given strategy."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    result = _run_backtest(request.ticker, request.strategy, request.months)

    # Cache result
    try:
        cache_key = f"/tmp/backtest_{request.ticker}_{request.strategy}.json"
        with open(cache_key, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "results": result}, f, default=_serialize_json)
    except Exception:
        pass

    return {"status": "success", "data": result}


@app.get("/api/backtest-results")
def get_backtest_results(ticker: str = Query("AAPL"), strategy: str = Query("sma_crossover")):
    """Get cached backtest results."""
    cache_key = f"/tmp/backtest_{ticker}_{strategy}.json"
    if Path(cache_key).exists():
        try:
            with open(cache_key, "r") as f:
                data = json.load(f)
            return {"status": "success", "data": data.get("results", {}), "cached_at": data.get("cached_at")}
        except Exception:
            pass
    return {"status": "success", "data": {}, "cached_at": None}


@app.get("/")
def root():
    """Root endpoint — API info."""
    return {
        "name": "TradingBot Scanner API",
        "version": API_VERSION,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


# ── Run with uvicorn ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
