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
from modules.data_fetchers import rate_limited_get

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
        if isinstance(data, dict) and "cached_at" in data:
            cached_at = data.get("cached_at")
            data = data.get("results", [])

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
    ]

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

        # Candlestick data for chart (last 30 bars, reversed to chronological)
        candles = [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)}
                   for b in reversed(bars[:30])]

        return {
            "ticker": ticker, "price": round(close, 2), "open": round(opn, 2),
            "high": round(high, 2), "low": round(low, 2), "volume": vol,
            "prev_close": round(prev_close, 2),
            "change_1d": chg_1d, "change_5d": chg_5d, "change_20d": chg_20d,
            "ma20": ma20, "ma50": ma50, "rvol": rvol, "rsi": rsi,
            "high_20d": high_20d, "low_20d": low_20d, "range_position": range_pos,
            "pivot": pivot, "support_1": support_1, "resistance_1": resist_1,
            "avg_volume": round(avg_vol), "candles": candles,
        }
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


# ── Crash Monitor (VIX + Market Breadth) ──
CRASH_MONITOR_CACHE = "/tmp/crash_monitor_cache.json"

def _crash_monitor_wrapper() -> None:
    """Fetch VIX, major indices, and market breadth data."""
    try:
        result = {"vix": {}, "indices": [], "breadth": {}}

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

        save_cache_file(CRASH_MONITOR_CACHE, [result])
    except Exception as e:
        print(f"Crash monitor error: {e}")


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
                    # Signal
                    div = r["div_5d"]
                    if div > 5:
                        r["signal"] = "OUTPERFORM"
                    elif div < -5:
                        r["signal"] = "UNDERPERFORM"
                    elif abs(div) < 2:
                        r["signal"] = "KORRELIERT"
                    else:
                        r["signal"] = "LEICHTE DIV."
                else:
                    r["div_1d"] = 0
                    r["div_5d"] = 0
                    r["signal"] = "REFERENZ"

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
