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
import threading
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import scanner modules
from modules.scanners import (
    _bi_background_scan,
    _biotech_background_scan,
    _bi_cache_load,
    _biotech_cache_load,
)
from modules.helpers import get_current_trading_session
from modules.data_fetchers import rate_limited_get

# Strategies are defined inline here to avoid Streamlit import at module level
# (they are only needed when imported in a Streamlit context)
STRATEGIES = {
    "Volume Surge": {
        "description": "Aktien/Krypto mit überdurchschnittlichem Volumen UND Bewegung",
        "filters": {"RVOL": (2.0, 50.0), "Change %": (2.0, 100.0)},
    },
    "Bull Flag": {
        "description": "Echte Multi-Day Flag: Fahnenstange (2-7d) + Konsolidierung mit 20 Tageskerzen",
        "filters": {"Change %": (-5.0, 5.0), "RVOL": (0.05, 3.0)},
    },
    "Bear Flag": {
        "description": "Echte Multi-Day Flag: Fahnenstange (2-7d) + Konsolidierung mit 20 Tageskerzen",
        "filters": {"Change %": (-5.0, 5.0), "RVOL": (0.05, 3.0)},
    },
    "Breakout Long": {
        "description": "Momentum-Ausbruch mit Volumen-Bestätigung",
        "filters": {"Change %": (3.0, 50.0), "RVOL": (1.5, 50.0), "Close Position": (0.65, 1.0)},
    },
    "Breakdown Short": {
        "description": "Bearish Momentum-Ausbruch mit Volumen",
        "filters": {"Change %": (-50.0, -3.0), "RVOL": (1.5, 50.0), "Close Position": (0.0, 0.35)},
    },
}

CRYPTO_STRATEGIES = {
    "Momentum": {
        "description": "Crypto Momentum Plays mit hohem Volumen",
        "filters": {"RVOL": (3.0, 100.0), "Change %": (5.0, 200.0)},
    },
    "Volatility": {
        "description": "High Volatility Crypto Breakouts",
        "filters": {"RVOL": (2.0, 50.0)},
    },
}

FUTURES_STRATEGIES = {
    "Trend Following": {
        "description": "Follow major futures trends",
        "filters": {"RVOL": (1.5, 30.0)},
    },
}

FOREX_STRATEGIES = {
    "Pair Momentum": {
        "description": "FX pair momentum trades",
        "filters": {"RVOL": (1.0, 20.0)},
    },
}

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


# ── FastAPI App ──
app = FastAPI(
    title="TradingBot Scanner API",
    description="REST API for trading scanner modules",
    version=API_VERSION,
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
        except:
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
        except:
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
        except:
            pass

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
    )


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
