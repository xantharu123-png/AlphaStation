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
import re
import smtplib
import threading
import html
import uuid
from copy import deepcopy
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import FastAPI, BackgroundTasks, Query, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# V3.4: Auth & Subscription System
try:
    from modules.auth import (
        register_user, login_user, verify_token, get_user_plan,
        get_user_limits, check_tab_access, check_feature,
        create_checkout_session, create_billing_portal,
        handle_stripe_webhook, PLANS, SCANNER_TABS_BY_PLAN,
        get_email_alert_recipients, get_user_alert_settings,
        update_user_alert_settings, auth_security_status,
        _load_users, _save_users,
        ADMIN_EMAILS, AUTH_DB_PATH,
    )
    HAS_AUTH = True
except ImportError as _auth_err:
    HAS_AUTH = False
    print(f"[Warning] Auth module not loaded: {_auth_err}")
import requests as req

# Import scanner modules
from modules.scanners import (
    _bi_background_scan,
    _biotech_background_scan,
    _bi_cache_load,
    _biotech_cache_load,
    _bi_progress_read,
    _biotech_progress_read,
    _autotrader_config_load,
    _autotrader_config_save,
    _autotrader_state_read,
    _autotrader_state_write,
    _autotrader_log,
    _autotrader_request_stop,
    _autotrader_is_market_hours,
    autotrader_scan_once,
    autotrader_background_loop,
)
from modules.helpers import get_current_trading_session
from modules.data_fetchers import (
    rate_limited_get,
    fetch_ohlcv_for_chart,
    fetch_grouped_daily,
    fetch_daily_candles_crypto,
    fetch_multi_day_data,
    get_bpiq_catalyst_watchlist,
)
from modules.indicators import calculate_ema_series, calculate_vwap, calculate_rsi_from_bars, calculate_macd, calculate_obv
from modules.volume_analysis import calculate_volume_profile, find_volume_voids
from modules.trade_levels import normalize_alert_trade_levels

try:
    from modules.backtests import run_bi_v2_backtest, run_biotech_backtest
    HAS_ADVANCED_BACKTESTS = True
except ImportError as _backtest_err:
    HAS_ADVANCED_BACKTESTS = False
    print(f"[Warning] Advanced backtest engines not loaded: {_backtest_err}")

try:
    from modules.scorers import calculate_setup_score as calculate_stock_setup_score
except ImportError:
    calculate_stock_setup_score = None

from modules.trade_health import calculate_trade_health
from modules.market_context import analyze_headlines, build_event_risk, build_market_context, missing_headline_risk

# Import pattern detection
try:
    from modules.patterns import find_harmonic_for_chart, detect_chart_patterns, find_pivots, detect_order_blocks, detect_liquidity_levels
    HAS_PATTERNS = True
except ImportError:
    HAS_PATTERNS = False
    print("[Warning] patterns module not fully loaded")

# Import real S/R calculation
try:
    from modules.analysis import calculate_sr_from_historical, analyze_multi_day_pattern
    HAS_REAL_SR = True
except ImportError:
    HAS_REAL_SR = False
    print("[Warning] analysis module not fully loaded - using simple S/R")
    analyze_multi_day_pattern = None

# Import new listing scanner
try:
    from modules.new_listing_scanner import (
        detect_new_listings,
        calculate_listing_exhaustion,
        fetch_ticker_for,
        fetch_candles_for,
        fetch_orderbook_for,
        fetch_cryptocom_orderbook,
        run_new_listing_scanner,
        seed_instrument_cache,
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
            getattr(mod, "BACKTEST_STRATEGY_RULES", {}),
        )
    finally:
        if _real_st:
            _sys.modules["streamlit"] = _real_st
        else:
            _sys.modules.pop("streamlit", None)

STRATEGIES, CRYPTO_STRATEGIES, FUTURES_STRATEGIES, FOREX_STRATEGIES, INTERNATIONAL_STRATEGIES, BACKTEST_RULES = _load_strategies()
print(f"[Init] Strategies loaded: {len(STRATEGIES)} Stock, {len(CRYPTO_STRATEGIES)} Crypto, {len(FUTURES_STRATEGIES)} Futures, {len(FOREX_STRATEGIES)} Forex, {len(INTERNATIONAL_STRATEGIES)} International, {len(BACKTEST_RULES)} Backtest Rules")

STOCK_STRATEGY_ORDER = [
    "Momentum Breakout Long",
    "Gap Momentum Long",
    "Gap Momentum Short",
    "Turtle Breakout",
    "Bull Flag",
    "Bear Flag",
    "Compression Breakout",
    "Cup and Handle Breakout",
    "Trend Reversal",
    "MA Bounce Long",
    "MA Bounce Short",
    "Wyckoff Accumulation",
    "Wyckoff Distribution",
]

_AUTO_STOCK_ALERT_STRATEGIES = [
    "Momentum Breakout Long",
    "Gap Momentum Long",
    "Gap Momentum Short",
]

STOCK_STRATEGY_ALIASES = {
    "Breakout Long": "Momentum Breakout Long",
    "Early Momentum": "Momentum Breakout Long",
    "Whale Watch": "Momentum Breakout Long",
    "Volume Surge": "Momentum Breakout Long",
    "Earnings Mover Long": "Gap Momentum Long",
    "Gap Up": "Gap Momentum Long",
    "Gap Up (High Vol)": "Gap Momentum Long",
    "Earnings Mover Short": "Gap Momentum Short",
    "Gap Down": "Gap Momentum Short",
    "Gap Down (High Vol)": "Gap Momentum Short",
    "Whale Watch Short ": "Gap Momentum Short",
    "Consolidation ": "Compression Breakout",
    "Consolidation Breakout ": "Compression Breakout",
    "Tight Range ": "Compression Breakout",
    "Reversal Hunter": "Trend Reversal",
    "Reversal Setup ": "Trend Reversal",
    "SMA 50 Bounce Long ": "MA Bounce Long",
    "SMA 200 Bounce Long ": "MA Bounce Long",
    "EMA 21 Bounce (Swing) ": "MA Bounce Long",
    "SMA 50 Bounce Short ": "MA Bounce Short",
    "SMA 200 Bounce Short ": "MA Bounce Short",
    "Wyckoff Accumulation ⬆": "Wyckoff Accumulation",
    "Wyckoff Distribution ⬇": "Wyckoff Distribution",
}

STOCK_STRATEGY_HIDDEN = {
    "Penny Rockets",
    "Dip Buy",
    "Volume Void Long ⬆",
    "Volume Void Short ⬇",
    "Harmonic Bullish ⬆",
    "Harmonic Bearish ⬇",
    "High Volume Churn ",
    "Insider Buying",
    "Insider Selling",
}

STOCK_STRATEGY_REMOVED_FROM_MENU = {
    "Harmonic All Patterns ",
    "Long Wick Up",
    "Long Wick Down",
}


def _clone_stock_strategy(
    base_name: str,
    *,
    filters: Optional[Dict[str, Any]] = None,
    merged_from: Optional[List[str]] = None,
    display_group: Optional[str] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Create a public-facing stock strategy config without mutating the base template."""
    strategy = deepcopy(STRATEGIES.get(base_name, {}))
    if filters is not None:
        merged_filters = dict(strategy.get("filters", {}))
        merged_filters.update(filters)
        strategy["filters"] = merged_filters
    strategy.update(overrides)
    if merged_from:
        strategy["merged_from"] = list(merged_from)
    if display_group:
        strategy["display_group"] = display_group
    return strategy


def _register_public_stock_strategies() -> Dict[str, Dict[str, Any]]:
    """Expose a tighter stock menu while keeping old names as backend-compatible aliases."""
    public_strategies = {
        "Momentum Breakout Long": _clone_stock_strategy(
            "Breakout Long",
            filters={
                "Change %": (3.0, 200.0),
                "RVOL": (1.8, 100.0),
                "Close Position": (0.62, 1.0),
                "Preis": (5.0, 100000.0),
            },
            description="Konsolidierter Momentum-Scanner für Breakout-, Early- und Whale-Setups.",
            logic="Breakout + Trendhaltigkeit + sauberes Volumenprofil = priorisierter Momentum-Kandidat.",
            min_dollar_volume=750000,
            merged_from=["Breakout Long", "Early Momentum", "Whale Watch", "Volume Surge"],
            display_group="Momentum",
        ),
        "Gap Momentum Long": _clone_stock_strategy(
            "Earnings Mover Long",
            filters={
                "Gap %": (3.0, 100.0),
                "Change %": (2.0, 200.0),
                "RVOL": (1.5, 100.0),
                "Close Position": (0.55, 1.0),
                "Preis": (5.0, 100000.0),
            },
            description="Gap-Up + News-/Momentum-Scanner mit Fokus auf haltbare Long-Gaps.",
            logic="Gap + Volumen + Halt oberhalb des Opens = Gap-and-go Priorität.",
            min_dollar_volume=1000000,
            merged_from=["Earnings Mover Long", "Gap Up", "Gap Up (High Vol)"],
            display_group="Gap",
        ),
        "Gap Momentum Short": _clone_stock_strategy(
            "Earnings Mover Short",
            filters={
                "Gap %": (-100.0, -3.0),
                "Change %": (-200.0, -2.0),
                "RVOL": (1.5, 100.0),
                "Close Position": (0.0, 0.45),
                "Preis": (5.0, 100000.0),
            },
            description="Gap-Down + News-/Momentum-Scanner für Continuation-Shorts.",
            logic="Großer negativer Gap + schwacher Intraday-Halt = priorisierter Short-Kandidat.",
            min_dollar_volume=1000000,
            merged_from=["Earnings Mover Short", "Gap Down", "Gap Down (High Vol)", "Whale Watch Short"],
            display_group="Gap",
        ),
        "Turtle Breakout": _clone_stock_strategy(
            "Turtle Breakout",
            description="Trendfolgesetup nach klassischem Donchian-Breakout.",
            merged_from=["Turtle Breakout"],
            display_group="Momentum",
        ),
        "Bull Flag": _clone_stock_strategy(
            "Bull Flag",
            description="Mehrtägige Bull Flag mit echter Pullback-Validierung.",
            merged_from=["Bull Flag"],
            display_group="Pullback",
        ),
        "Bear Flag": _clone_stock_strategy(
            "Bear Flag",
            description="Mehrtägige Bear Flag für strukturierte Short-Fortsetzungen.",
            merged_from=["Bear Flag"],
            display_group="Pullback",
        ),
        "Compression Breakout": _clone_stock_strategy(
            "Consolidation Breakout ",
            filters={
                "Change %": (1.2, 50.0),
                "Vortag %": (-3.0, 3.0),
                "RVOL": (1.3, 50.0),
            },
            description="Komprimierte Mehrtagesrange mit bestätigtem Ausbruch.",
            logic="Enger Aufbau + steigendes Interesse + Breakout-Tag = strukturierter Long-Kandidat.",
            merged_from=["Consolidation", "Consolidation Breakout", "Tight Range"],
            display_group="Structure",
        ),
        "Cup and Handle Breakout": _clone_stock_strategy(
            "Consolidation Breakout ",
            filters={
                "Change %": (-2.0, 12.0),
                "RVOL": (0.8, 50.0),
                "Close Position": (0.45, 1.0),
                "Preis": (5.0, 100000.0),
            },
            description="Cup-and-Handle Breakout mit 1D-Struktur und frischer Breakout-Bestaetigung.",
            logic="Runder 1D-Cup + kontrollierter Handle + Breakout ueber Lip mit Volumen = Long-Signal.",
            min_dollar_volume=2_000_000,
            needs_history=False,
            needs_cup_handle=True,
            history_days=180,
            cup_handle_timeframe="1D",
            confirmation_timeframe="5m",
            merged_from=["Cup and Handle"],
            display_group="Structure",
        ),
        "Trend Reversal": _clone_stock_strategy(
            "Reversal Setup ",
            filters={
                "Change %": (1.5, 15.0),
                "Vortag %": (-10.0, -2.0),
                "RVOL": (1.2, 12.0),
            },
            description="Mehrtägige Trend-Umkehr nach kontrolliertem Abverkauf.",
            logic="Downtrend + Reversal-Tag + Volumenbestätigung = Long-Reversal mit Struktur.",
            merged_from=["Reversal Hunter", "Reversal Setup"],
            display_group="Structure",
        ),
        "MA Bounce Long": _clone_stock_strategy(
            "SMA 50 Bounce Long ",
            filters={"Preis": (5.0, 1000.0), "Change %": (-6.0, 3.0)},
            description="Pullback an EMA21, SMA50 oder SMA200 im Aufwärtstrend.",
            logic="Trendstütze + kontrollierter Pullback + Halt oberhalb des Moving Averages = Long-Bounce.",
            min_dollar_volume=1000000,
            needs_ma=True,
            ma_profiles=[
                {"ma_type": "EMA", "ma_period": 21, "ma_approach": "from_above", "ma_distance_max": 2.0},
                {"ma_type": "SMA", "ma_period": 50, "ma_approach": "from_above", "ma_distance_max": 3.0},
                {"ma_type": "SMA", "ma_period": 200, "ma_approach": "from_above", "ma_distance_max": 3.0},
            ],
            merged_from=["SMA 50 Bounce Long", "SMA 200 Bounce Long", "EMA 21 Bounce (Swing)"],
            display_group="Pullback",
        ),
        "MA Bounce Short": _clone_stock_strategy(
            "SMA 50 Bounce Short ",
            filters={"Preis": (5.0, 1000.0), "Change %": (-3.0, 6.0)},
            description="Bounce in fallende SMA50/SMA200-Zonen für strukturierte Shorts.",
            logic="Widerstand am fallenden Durchschnitt + schwacher Close = Short-Rejection.",
            min_dollar_volume=1000000,
            needs_ma=True,
            ma_profiles=[
                {"ma_type": "SMA", "ma_period": 50, "ma_approach": "from_below", "ma_distance_max": 3.0},
                {"ma_type": "SMA", "ma_period": 200, "ma_approach": "from_below", "ma_distance_max": 3.0},
            ],
            merged_from=["SMA 50 Bounce Short", "SMA 200 Bounce Short"],
            display_group="Pullback",
        ),
        "Wyckoff Accumulation": _clone_stock_strategy(
            "Wyckoff Accumulation ⬆",
            description="Wyckoff-Akkumulation mit Mehrtagesstruktur und Smart-Money-Kontext.",
            merged_from=["Wyckoff Accumulation ⬆"],
            display_group="Smart Money",
        ),
        "Wyckoff Distribution": _clone_stock_strategy(
            "Wyckoff Distribution ⬇",
            description="Wyckoff-Distribution für schleichende Schwäche vor dem Breakdown.",
            merged_from=["Wyckoff Distribution ⬇"],
            display_group="Smart Money",
        ),
    }

    for name, config in public_strategies.items():
        config["canonical_name"] = name
        STRATEGIES[name] = config

    return public_strategies


PUBLIC_STOCK_STRATEGIES = _register_public_stock_strategies()


def _normalize_strategy_key(strategy_name: str) -> str:
    return re.sub(r"\s+", " ", str(strategy_name or "")).strip().lower()


def _build_stock_strategy_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for name in STRATEGIES.keys():
        lookup[_normalize_strategy_key(name)] = name
    for alias, canonical in STOCK_STRATEGY_ALIASES.items():
        lookup[_normalize_strategy_key(alias)] = canonical
    for canonical in PUBLIC_STOCK_STRATEGIES.keys():
        lookup[_normalize_strategy_key(canonical)] = canonical
    return lookup


STOCK_STRATEGY_LOOKUP = _build_stock_strategy_lookup()

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

NON_STOCK_ETP_TICKERS = {
    "IREX", "IREZ", "APLZ", "LCIZ", "NBIZ", "MSTX", "MSTU", "MSTZ", "TSLL", "TSLQ",
    "NVDL", "NVDQ", "NVDU", "NVDD", "NVDS", "NVDX", "NVDY",
    "CONL", "GGLL", "GGLS", "AAPU", "AAPD", "AMZU", "AMZD", "METU", "METD",
    "SOXL", "SOXS", "TQQQ", "SQQQ", "UPRO", "SPXU", "SPXL", "SPXS", "LABU", "LABD",
    "TECL", "TECS", "FNGU", "FNGD", "BOIL", "KOLD", "GUSH", "DRIP", "NUGT", "DUST",
    "JNUG", "JDST", "YINN", "YANG", "UVXY", "VIXY", "VXX", "BITO", "BITI",
}

STOCK_SCANNER_ASSET_GUARD_NAMES = {
    "strategy_scan", "stock_strategy", "bi_long", "bi_short", "biotech", "bear", "orb", "turtle", "volume_spikes"
}

NON_STOCK_ETP_KEYWORDS = {
    "ETF", "ETN", "ETP", "FUND", "2X", "3X", "LEVERAGED", "INVERSE",
    "ULTRA", "ULTRAPRO", "BULL", "BEAR", "DAILY TARGET", "TRADR", "T-REX",
    "DIREXION", "PROSHARES", "GRANITESHARES", "YIELDMAX", "ROUNDHILL", "DEFIANCE",
    "REX SHARES", "MICROSECTORS", "VOLATILITY SHARES", "WARRANT", "RIGHT", "UNIT",
}

STOCK_SCANNER_ALLOWED_REFERENCE_TYPES = {"CS", "ADRC", "ADRP"}
ORB_ALLOWED_POLYGON_TYPES = STOCK_SCANNER_ALLOWED_REFERENCE_TYPES
_ORB_REFERENCE_CACHE: Dict[str, tuple[bool, str]] = {}
_ORB_ATR_CACHE: Dict[str, float] = {}

ORB_START_MINUTE = 9 * 60 + 45
ORB_PRIMARY_END_MINUTE = 11 * 60
ORB_SCAN_END_MINUTE = 16 * 60


# ── Configuration & Constants ──
API_VERSION = "1.0.0"
POLYGON_KEY = os.getenv("POLYGON_KEY", "")
BPIQ_API_KEY = os.getenv("BPIQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_AI_PROVIDER_MODEL = "".join(["clau", "de", "-sonnet-4-20250514"])
AI_PROVIDER_MODEL = os.getenv("AI_PROVIDER_MODEL") or os.getenv("ANTHROPIC_MODEL") or DEFAULT_AI_PROVIDER_MODEL

# Cache file paths
BI_CACHE_LONG = "/tmp/bi_cache_long.json"
BI_CACHE_SHORT = "/tmp/bi_cache_short.json"
BEAR_CACHE = "/tmp/bear_scanner_cache.json"
BIOTECH_CACHE = "/tmp/alpha_biotech_cache.json"
STRATEGY_SCAN_CACHE = "/tmp/strategy_scan_cache.json"  # Fallback / generisch

def _strategy_cache_path(strategy_name: str, market_type: str = "stocks") -> str:
    """Separate Cache-Datei pro Strategie — verhindert gegenseitiges Überschreiben."""
    safe_name = re.sub(r"[^a-z0-9_]+", "_", strategy_name.lower().replace(" ", "_").replace("/", "_"))
    safe_name = re.sub(r"_+", "_", safe_name).strip("_") or "strategy"
    market_prefix = re.sub(r"[^a-z0-9_]+", "_", (market_type or "stocks").lower()).strip("_")
    if market_prefix and market_prefix != "stocks":
        return f"/tmp/{market_prefix}_strategy_{safe_name}_cache.json"
    return f"/tmp/strategy_{safe_name}_cache.json"


def _looks_like_non_stock_etp_symbol(ticker: str) -> Optional[str]:
    """Last-ditch symbol guard; primary stock filtering is by reference asset type."""
    tk = str(ticker or "").upper().strip()
    if not tk:
        return "empty ticker"
    if tk in NON_STOCK_ETP_TICKERS or tk in INVERSE_ETFS:
        return "known ETF/ETP ticker"
    if len(tk) >= 4 and tk[-1] in ("X", "Q") and tk[-2] in ("X", "Q", "S"):
        return "leveraged ETF ticker pattern"
    return None


def _name_has_non_stock_product_keyword(name: str) -> bool:
    normalized_name = re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper()).strip()
    if not normalized_name:
        return False
    padded_name = f" {normalized_name} "
    for keyword in NON_STOCK_ETP_KEYWORDS:
        normalized_keyword = re.sub(r"[^A-Z0-9]+", " ", keyword.upper()).strip()
        if normalized_keyword and f" {normalized_keyword} " in padded_name:
            return True
    return False


def _reference_asset_exclusion_reason(asset_type: str = "", name: str = "", market: str = "") -> Optional[str]:
    """Classify Polygon reference metadata into tradeable stock vs non-stock product."""
    ref_type = str(asset_type or "").upper().strip()
    ref_name = str(name or "").upper().strip()
    ref_market = str(market or "").lower().strip()

    if ref_market and ref_market != "stocks":
        return f"market={ref_market}"
    if ref_type and ref_type not in STOCK_SCANNER_ALLOWED_REFERENCE_TYPES:
        return f"type={ref_type}"
    if _name_has_non_stock_product_keyword(ref_name):
        return "non-stock product keyword"
    if not ref_type:
        return "missing reference type"
    return None


def _stock_alert_asset_exclusion_reason(
    ticker: str,
    common_stock_universe: Optional[set[str]] = None,
    universe_source: str = "",
    require_reference: bool = False,
) -> Optional[str]:
    """Return why a ticker must not be used as an actionable stock alert."""
    tk = str(ticker or "").upper().strip()
    if not tk:
        return "empty ticker"
    cheap_reason = _looks_like_non_stock_etp_symbol(tk)
    if cheap_reason:
        return cheap_reason
    if "." in tk or "/" in tk:
        return "non-standard ticker class"
    if common_stock_universe is not None:
        if tk not in common_stock_universe:
            return f"not in common-stock universe ({universe_source or 'unknown source'})"
        return None
    if require_reference:
        is_stock, reason = _is_orb_common_stock_candidate(tk)
        if not is_stock:
            return reason
    return None


def _is_orb_common_stock_candidate(ticker: str) -> tuple[bool, str]:
    """Use Polygon reference data to keep ORB focused on common stocks/ADRs."""
    tk = str(ticker or "").upper().strip()
    cheap_reason = _looks_like_non_stock_etp_symbol(tk)
    if cheap_reason:
        return False, cheap_reason
    if tk in _ORB_REFERENCE_CACHE:
        return _ORB_REFERENCE_CACHE[tk]
    if not POLYGON_KEY:
        return False, "reference unavailable: missing Polygon key"

    try:
        url = f"https://api.polygon.io/v3/reference/tickers/{tk}"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY}, timeout=8)
        if resp.status_code != 200:
            result = (False, f"reference unavailable HTTP {resp.status_code}")
            _ORB_REFERENCE_CACHE[tk] = result
            return result

        details = resp.json().get("results", {}) or {}
        asset_type = str(details.get("type", "") or "").upper()
        name = str(details.get("name", "") or "").upper()
        market = str(details.get("market", "") or "").lower()

        exclusion_reason = _reference_asset_exclusion_reason(asset_type, name, market)
        result = (False, exclusion_reason) if exclusion_reason else (True, asset_type or "reference ok")
    except Exception as e:
        result = (False, f"reference error: {e}")

    _ORB_REFERENCE_CACHE[tk] = result
    return result


COMMON_STOCK_UNIVERSE_CACHE = "/tmp/polygon_common_stock_universe.json"
_COMMON_STOCK_UNIVERSE_MEM: Dict[str, Any] = {"loaded_at": 0, "tickers": None, "source": "not_loaded"}


def _load_common_stock_universe(max_age_seconds: int = 24 * 3600) -> tuple[Optional[set[str]], str]:
    """Return active common-stock/ADR tickers for breadth filtering without per-symbol reference calls."""
    now_ts = time.time()
    mem_tickers = _COMMON_STOCK_UNIVERSE_MEM.get("tickers")
    mem_loaded_at = float(_COMMON_STOCK_UNIVERSE_MEM.get("loaded_at", 0) or 0)
    stale_mem_tickers = set(mem_tickers or []) if mem_tickers is not None else set()
    if stale_mem_tickers and now_ts - mem_loaded_at < max_age_seconds:
        return stale_mem_tickers, str(_COMMON_STOCK_UNIVERSE_MEM.get("source") or "memory")

    stale_cached_tickers: set[str] = stale_mem_tickers
    stale_cached_source = str(_COMMON_STOCK_UNIVERSE_MEM.get("source") or "memory")
    stale_cached_at = mem_loaded_at
    try:
        if os.path.exists(COMMON_STOCK_UNIVERSE_CACHE):
            with open(COMMON_STOCK_UNIVERSE_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_at = float(cached.get("cached_at", 0) or 0)
            cached_tickers = set(cached.get("tickers", []) or [])
            if cached_tickers and now_ts - cached_at < max_age_seconds:
                _COMMON_STOCK_UNIVERSE_MEM.update({"loaded_at": now_ts, "tickers": sorted(cached_tickers), "source": "file_cache"})
                return cached_tickers, "file_cache"
            if cached_tickers:
                stale_cached_tickers = cached_tickers
                stale_cached_source = "stale_file_cache"
                stale_cached_at = cached_at
    except Exception as cache_err:
        print(f"[Common Stock Universe] cache read error: {cache_err}")

    if not POLYGON_KEY:
        if stale_cached_tickers:
            _COMMON_STOCK_UNIVERSE_MEM.update({
                "loaded_at": stale_cached_at or now_ts,
                "tickers": sorted(stale_cached_tickers),
                "source": stale_cached_source,
            })
            return stale_cached_tickers, stale_cached_source
        return None, "missing_polygon_key"

    tickers: set[str] = set()
    try:
        for asset_type in sorted(ORB_ALLOWED_POLYGON_TYPES):
            url = "https://api.polygon.io/v3/reference/tickers"
            params = {
                "apiKey": POLYGON_KEY,
                "market": "stocks",
                "active": "true",
                "type": asset_type,
                "limit": 1000,
                "sort": "ticker",
                "order": "asc",
            }
            pages = 0
            while url and pages < 20:
                resp = rate_limited_get(url, params=params, timeout=20)
                if resp.status_code != 200:
                    print(f"[Common Stock Universe] {asset_type} HTTP {resp.status_code}")
                    break
                payload = resp.json()
                for item in payload.get("results", []) or []:
                    tk = str(item.get("ticker", "") or "").upper().strip()
                    market = str(item.get("market", "") or "").lower()
                    item_type = str(item.get("type", "") or "").upper()
                    name = str(item.get("name", "") or "").upper()
                    if not tk or market != "stocks" or item_type not in ORB_ALLOWED_POLYGON_TYPES:
                        continue
                    if _looks_like_non_stock_etp_symbol(tk) or _name_has_non_stock_product_keyword(name):
                        continue
                    tickers.add(tk)
                next_url = payload.get("next_url")
                url = next_url if next_url else None
                params = {"apiKey": POLYGON_KEY} if next_url else {}
                pages += 1

        if tickers:
            try:
                os.makedirs(os.path.dirname(COMMON_STOCK_UNIVERSE_CACHE) or ".", exist_ok=True)
                with open(COMMON_STOCK_UNIVERSE_CACHE, "w", encoding="utf-8") as f:
                    json.dump({"cached_at": now_ts, "tickers": sorted(tickers)}, f)
            except Exception as write_err:
                print(f"[Common Stock Universe] cache write error: {write_err}")
            _COMMON_STOCK_UNIVERSE_MEM.update({"loaded_at": now_ts, "tickers": sorted(tickers), "source": "polygon_reference"})
            return tickers, "polygon_reference"
    except Exception as e:
        print(f"[Common Stock Universe] fetch error: {e}")

    if stale_cached_tickers:
        _COMMON_STOCK_UNIVERSE_MEM.update({
            "loaded_at": stale_cached_at or now_ts,
            "tickers": sorted(stale_cached_tickers),
            "source": stale_cached_source,
        })
        return stale_cached_tickers, stale_cached_source

    return None, "unavailable"


def _common_stock_guard_status() -> Dict[str, Any]:
    tickers, source = _load_common_stock_universe()
    mem_loaded_at = float(_COMMON_STOCK_UNIVERSE_MEM.get("loaded_at", 0) or 0)
    age_seconds = int(max(0, time.time() - mem_loaded_at)) if mem_loaded_at else None
    return {
        "configured": bool(POLYGON_KEY),
        "available": bool(tickers),
        "ticker_count": len(tickers or []),
        "source": source,
        "age_seconds": age_seconds,
        "stale": str(source).startswith("stale"),
        "allowed_reference_types": sorted(STOCK_SCANNER_ALLOWED_REFERENCE_TYPES),
    }


# ── ORB risk helpers ──
def _fetch_orb_atr_pct(ticker: str, as_of_et: datetime, fallback_pct: float, periods: int = 14) -> tuple[float, str]:
    """Return a real daily ATR percentage for ORB sizing, with a safe fallback."""
    tk = str(ticker or "").upper().strip()
    try:
        fallback = float(fallback_pct or 0)
    except Exception:
        fallback = 0.0
    if not tk or not POLYGON_KEY:
        return fallback, "prev_day_range"

    cache_key = f"{tk}:{as_of_et.strftime('%Y-%m-%d')}:{periods}"
    if cache_key in _ORB_ATR_CACHE:
        return _ORB_ATR_CACHE[cache_key], "atr14_cached"

    try:
        end_day = (as_of_et - timedelta(days=1)).strftime("%Y-%m-%d")
        start_day = (as_of_et - timedelta(days=35)).strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/1/day/{start_day}/{end_day}"
        resp = rate_limited_get(url, params={
            "apiKey": POLYGON_KEY,
            "adjusted": "true",
            "sort": "asc",
            "limit": 80,
        }, timeout=8)
        if resp.status_code != 200:
            return fallback, f"prev_day_range_http_{resp.status_code}"

        bars = resp.json().get("results", []) or []
        bars = [b for b in bars if b.get("h", 0) > 0 and b.get("l", 0) > 0 and b.get("c", 0) > 0]
        if len(bars) < 6:
            return fallback, "prev_day_range_short_history"

        true_ranges = []
        prev_close = None
        for bar in bars:
            high = float(bar.get("h", 0))
            low = float(bar.get("l", 0))
            close = float(bar.get("c", 0))
            if prev_close is not None:
                true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
            prev_close = close

        true_ranges = true_ranges[-periods:]
        ref_close = float(bars[-1].get("c", 0))
        if not true_ranges or ref_close <= 0:
            return fallback, "prev_day_range_no_atr"

        atr_pct = (sum(true_ranges) / len(true_ranges)) / ref_close * 100
        atr_pct = round(max(0.0, atr_pct), 4)
        _ORB_ATR_CACHE[cache_key] = atr_pct
        return atr_pct, f"atr{len(true_ranges)}"
    except Exception as e:
        return fallback, f"prev_day_range_error:{e}"


# ── Email Alert System ──
_EMAIL_CONFIG_KEYS = (
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "ALERT_EMAIL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SSL_PORT",
)


def _parse_kv_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    except Exception as exc:
        print(f"[Config] Could not read {path}: {exc}")
    return values


def _load_secrets():
    """Load config from secrets files, .env and process env without letting partial files shadow Gmail config."""
    secrets = {}
    paths = [
        Path.home() / ".streamlit" / "secrets.toml",
        Path(__file__).parent / ".streamlit" / "secrets.toml",
        Path(__file__).parent / ".env",
    ]
    for sp in paths:
        if sp.exists():
            secrets.update(_parse_kv_file(sp))
    for key in (
        "POLYGON_KEY",
        "BPIQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "FINNHUB_KEY",
        *_EMAIL_CONFIG_KEYS,
    ):
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets

_SECRETS = _load_secrets()
PUBLIC_APP_URL = (
    os.environ.get("PUBLIC_APP_URL")
    or os.environ.get("APP_BASE_URL")
    or _SECRETS.get("PUBLIC_APP_URL")
    or _SECRETS.get("APP_BASE_URL")
    or "http://178.104.69.209:3000"
).rstrip("/")
COMMERCE_ENFORCE_AUTH = str(
    os.environ.get("COMMERCE_ENFORCE_AUTH")
    or _SECRETS.get("COMMERCE_ENFORCE_AUTH")
    or "0"
).strip().lower() in {"1", "true", "yes", "on"}
ALERT_SEND_TO_SUBSCRIBERS = str(
    os.environ.get("ALERT_SEND_TO_SUBSCRIBERS")
    or _SECRETS.get("ALERT_SEND_TO_SUBSCRIBERS")
    or "1"
).strip().lower() not in {"0", "false", "no", "off"}
_EARLY_MOVER_SEND_ARMED_EMAILS = str(
    os.environ.get("EARLY_MOVER_SEND_ARMED_EMAILS")
    or _SECRETS.get("EARLY_MOVER_SEND_ARMED_EMAILS")
    or "0"
).strip().lower() in {"1", "true", "yes", "on"}

# Fix: POLYGON_KEY aus secrets.toml laden falls env var leer
if not POLYGON_KEY:
    POLYGON_KEY = _SECRETS.get("POLYGON_KEY", "")
if not BPIQ_API_KEY:
    BPIQ_API_KEY = _SECRETS.get("BPIQ_API_KEY", "")
if not ANTHROPIC_API_KEY:
    ANTHROPIC_API_KEY = _SECRETS.get("ANTHROPIC_API_KEY", "")
for _cfg_key in ("POLYGON_KEY", "BPIQ_API_KEY", "ANTHROPIC_API_KEY", "FINNHUB_KEY"):
    if _SECRETS.get(_cfg_key) and not os.environ.get(_cfg_key):
        os.environ[_cfg_key] = _SECRETS[_cfg_key]

_EMAIL_COOLDOWN = {}
_EMAIL_COOLDOWN_SEC = 3600 * 8  # V2.6: 8h pro Ticker
_EMAIL_DEDUPE_FILE = "/tmp/alphastation_email_dedupe.json"
_CRASH_ALERT_DEDUPE_SEC = 36 * 3600
_BIOTECH_ALERT_DEDUPE_SEC = 72 * 3600  # Catalyst setups persist for days; avoid repeat ticker mails.
_EMAIL_STARTUP_TIME = time.time()  # V2.6b: Startup-Zeitpunkt für Cooldown nach Restart
_EMAIL_STARTUP_DELAY = 300  # 5 Min nach Restart keine Mails (Cache-Daten = alt)
_EMAIL_SEND_LOG: List[Dict[str, Any]] = []
_ALERT_TOP_GRADES = {"S", "A", "A+"}
_ALERT_MIN_SCORE = 80
_ALERT_RVOL_GUARD_SCANNERS = {"bi_long", "bi_short", "biotech", "strategy_scan", "stock_strategy"}
_ALERT_MIN_RVOL = 0.7
_ALERT_MIN_LEVEL_RR = 1.0
_ALERT_TRADE_PLAN_GUARD_SCANNERS = {
    "bi_long", "bi_short", "biotech", "bear", "orb", "stock_strategy",
    "strategy_scan", "turtle", "volume_spikes", "crypto_strategy", "early_movers", "new_listing",
}
_ALERT_TRADE_HEALTH_GUARD_SCANNERS = set(_ALERT_TRADE_PLAN_GUARD_SCANNERS)
_NEW_LISTING_MIN_ALERT_RR = 1.5
_NEW_LISTING_WATCH_MIN_SCORE = 45
_NEW_LISTING_WATCH_MIN_PUMP_PCT = 15.0
_NEW_LISTING_WATCH_MIN_RR = 1.0
_NEW_LISTING_WATCH_DEDUPE_SEC = 20 * 3600
_NEW_LISTING_WATCH_BLOCK_FLAGS = {
    "safety_failed",
    "safety_not_ok",
    "early_crack_score_too_low",
    "rr_too_low",
    "risk_too_wide",
    "listing_age_expired",
    "listing_age_not_tradeable",
    "tp1_missed",
    "tp2_missed",
}
_EARLY_MOVER_MIN_ALERT_RR = 1.5
_EARLY_MOVER_RETEST_MAX_DISTANCE_R = 0.35
_EARLY_MOVER_DIGEST_DEDUPE_SEC = 2 * 3600
_EARLY_MOVER_DIGEST_KEY = "early_movers_long_digest"
_EARLY_MOVER_ARMED_DIGEST_DEDUPE_SEC = 4 * 3600
_EARLY_MOVER_ARMED_DIGEST_KEY = "early_movers_explosion_armed_digest"
_EARLY_MOVER_MAX_EMAIL_ROWS = 5
_EARLY_MOVER_TRIGGER_TTL = 180
_EARLY_MOVER_MARKET_PAGES = 4  # 4 * 250 = Top-1000 CoinGecko universe
_EARLY_MOVER_TRIGGER_SCAN_LIMIT = 1000
_EARLY_MOVER_MAX_DISPLAY = 160
_EARLY_MOVER_MIN_ARMED_SETUP_SCORE = 84
_EARLY_MOVER_MIN_ARMED_PREBREAKOUT_SCORE = 76
_EARLY_MOVER_MIN_ARMED_LIVE_RR = 1.8
_EARLY_MOVER_MAX_ARMED_DISTANCE_R = 0.35
_EARLY_MOVER_VISIBLE_MIN_SETUP_SCORE = 80
_EARLY_MOVER_VISIBLE_MIN_ENTRY_SCORE = 72
_EARLY_MOVER_VISIBLE_MIN_LIVE_RR = 1.45
_EARLY_MOVER_VISIBLE_MAX_DISTANCE_R = 0.75
_EARLY_MOVER_VISIBLE_LIMIT = 40
_EARLY_MOVER_TRIGGER_CACHE_MAX = 1500
_EARLY_MOVER_TRIGGER_CACHE: Dict[str, Dict[str, Any]] = {}
_EARLY_MOVER_WAIT_ONLY_FLAGS = {
    "observe_only_scanner",
    "no_intraday_execution_trigger",
    "requires_5m_trigger",
    "no_market_entry",
    "micro_trigger_missing",
    "pre_breakout_armed",
    "not_pre_breakout_coil",
    "pre_breakout_score_below_threshold",
}
_EARLY_MOVER_MIN_PERP_VOLUME_USD = 2_000_000
_EARLY_MOVER_WARN_PERP_VOLUME_USD = 5_000_000
_EARLY_MOVER_TURNOVER_WARN_PCT = 60.0
_EARLY_MOVER_TURNOVER_CHURN_BLOCK_PCT = 90.0
_EARLY_MOVER_MAX_SPREAD_BPS = 20.0
_EARLY_MOVER_MIN_DEPTH_10BPS_USD = 5_000
_EARLY_MOVER_MIN_DEPTH_25BPS_USD = 25_000
_EARLY_MOVER_MIN_DEPTH_50BPS_USD = 50_000
_TRADE_REMINDERS_FILE = "/tmp/alphastation_trade_reminders.json"
_TRADE_REMINDER_CHECK_SEC = 60
_TRADE_REMINDER_MAX_HOURS = 24
_TRADE_REMINDER_LOCK = threading.Lock()
_trade_reminder_running = False
_BEARISH_STOCK_ALERT_DEDUPE_SEC = 8 * 3600
_BEARISH_STOCK_ALERT_SCANNERS = {"bi_short", "bear"}
_LONG_ENTRY_ALERT_SCANNERS = {
    "bi_long", "biotech", "stock_strategy", "strategy_scan", "turtle", "volume_spikes"
}
_STOCK_EMAIL_ASSET_GUARD_SCANNERS = {
    "bear", "bi_short", "bi_long", "biotech", "orb", "stock_strategy", "strategy_scan"
}
_STOCK_ALERT_SCANNERS = set(_STOCK_EMAIL_ASSET_GUARD_SCANNERS) | {"turtle", "volume_spikes"}
_CRYPTO_STRATEGY_ALERTS_ENABLED = False
_EMAIL_BLOCKED_ETF_TICKERS = set(NON_STOCK_ETP_TICKERS) | set(INVERSE_ETFS.keys()) | {
    "SDS", "UDOW", "SVXY", "TVIX",
}
_SEND_WATCHLIST_EMAILS = False
_SIGNAL_ONLY_SCANNERS = {
    "bear", "bi_short", "bi_long", "biotech", "orb", "turtle",
    "stock_strategy", "strategy_scan", "volume_spikes",
    "early_movers", "new_listing", "btc_divergenz", "crypto_strategy",
}
_CRYPTO_SIGNAL_ONLY_SCANNERS = {"early_movers", "new_listing", "btc_divergenz", "crypto_strategy"}
_STOCK_RESULT_TRADE_STATE_SCANNERS = {
    "bear", "bi_short", "bi_long", "biotech", "orb", "turtle",
    "stock_strategy", "strategy_scan", "volume_spikes",
}
_DISPLAY_ONLY_SUPPRESSION_REASONS = {
    "cooldown_active",
    "persistent_dedupe_active",
    "bearish_ticker_already_alerted",
}

print(f"[Init] POLYGON_KEY: {'gesetzt' if POLYGON_KEY else 'FEHLT!'}")
print(f"[Init] Email alerts: {'AKTIV' if _SECRETS.get('GMAIL_USER') and _SECRETS.get('GMAIL_APP_PASSWORD') else 'INAKTIV (GMAIL_USER/GMAIL_APP_PASSWORD fehlt)'}")


def _email_alert_status() -> Dict[str, Any]:
    gmail_user = _SECRETS.get("GMAIL_USER", "")
    gmail_pass = _SECRETS.get("GMAIL_APP_PASSWORD", "")
    alert_to = _SECRETS.get("ALERT_EMAIL", gmail_user)
    platform_recipients = []
    if ALERT_SEND_TO_SUBSCRIBERS and HAS_AUTH:
        try:
            platform_recipients = get_email_alert_recipients()
        except Exception:
            platform_recipients = []
    configured_recipients = [addr for addr in str(alert_to).split(",") if addr.strip()]
    startup_remaining = max(0, int(_EMAIL_STARTUP_DELAY - (time.time() - _EMAIL_STARTUP_TIME)))
    return {
        "configured": bool(gmail_user and gmail_pass),
        "sender_configured": bool(gmail_user),
        "app_password_configured": bool(gmail_pass),
        "recipient_configured": bool(configured_recipients or platform_recipients),
        "recipient_count": len(set([addr.strip().lower() for addr in configured_recipients if addr.strip()] + platform_recipients)),
        "global_recipient_count": len(configured_recipients),
        "subscriber_recipient_count": len(platform_recipients),
        "send_to_subscribers": ALERT_SEND_TO_SUBSCRIBERS,
        "crypto_armed_watch_mails_enabled": False,
        "startup_cooldown_remaining_seconds": startup_remaining,
        "cooldown_entries": len(_EMAIL_COOLDOWN),
        "scanner_cooldowns_seconds": {
            "default": _EMAIL_COOLDOWN_SEC,
            "biotech": _BIOTECH_ALERT_DEDUPE_SEC,
            "crash_stock": _CRASH_ALERT_DEDUPE_SEC,
            "early_mover_digest": _EARLY_MOVER_DIGEST_DEDUPE_SEC,
            "early_mover_armed_digest": _EARLY_MOVER_ARMED_DIGEST_DEDUPE_SEC,
        },
        "min_alert_score": _ALERT_MIN_SCORE,
        "dedupe": _email_dedupe_status(),
        "required_keys": ["GMAIL_USER", "GMAIL_APP_PASSWORD"],
        "optional_keys": ["ALERT_EMAIL"],
        "config_sources_checked": [
            str(Path.home() / ".streamlit" / "secrets.toml"),
            str(Path(__file__).parent / ".streamlit" / "secrets.toml"),
            str(Path(__file__).parent / ".env"),
            "process environment",
        ],
    }


def _record_email_event(subject: str, status: str, reason: str = "") -> None:
    _EMAIL_SEND_LOG.append({
        "timestamp": datetime.now().isoformat(),
        "subject": subject,
        "status": status,
        "reason": reason,
    })
    if len(_EMAIL_SEND_LOG) > 50:
        del _EMAIL_SEND_LOG[:-50]


_ALERT_SUPPRESSION_LABELS = {
    "missing_ticker": "Ticker fehlt",
    "grade_below_alert_threshold": "Grade unter S/A/A+",
    "score_below_alert_threshold": f"Score unter {_ALERT_MIN_SCORE}",
    "rvol_below_alert_threshold": f"RVOL unter {_ALERT_MIN_RVOL}x",
    "cooldown_active": "8h Mail-Cooldown aktiv",
    "persistent_dedupe_active": "persistenter Mail-Dedupe aktiv",
    "bearish_ticker_already_alerted": "Bear/Crash fuer diesen Ticker schon gemeldet",
    "non_common_stock_product": "kein handelbarer Common Stock/ADR",
    "invalid_trade_plan": "Entry/Stop/TP ungueltig",
    "estimated_trade_plan": "Entry/Stop/TP nur geschaetzt",
    "trade_rr_below_threshold": "R:R unter Mindestwert",
    "trade_missing_entry": "Entry fehlt",
    "trade_missing_stop": "Stop fehlt",
    "trade_missing_tp1": "TP1 fehlt",
    "trade_wrong_direction": "Entry/Stop/TP passen nicht zur Richtung",
    "trade_health_no_trade": "Trade-Health sagt nicht traden",
    "trade_health_wait_for_retest": "Retest abwarten",
    "trade_health_wait_for_trigger": "Execution-Trigger fehlt",
    "trade_health_wait_for_continuation": "Continuation-Bestaetigung fehlt",
    "trade_health_chase_risk": "Chase-Risiko zu hoch",
    "trade_health_fakeout_risk": "Fakeout-Risiko zu hoch",
    "trade_health_liquidity_risk": "Liquiditaets-/Slippage-Risiko zu hoch",
    "latest_5m_red_fade": "Long: letzte 5m-Kerze faded",
    "current_candle_red_fade": "Long: aktuelle Kerze faded",
    "not_holding_highs_after_up_move": "Long: haelt Ausbruchshochs nicht",
    "latest_5m_green_reclaim": "Short: letzte 5m-Kerze bounced/reclaimt",
    "fresh_5m_state_missing_wait_trigger": "Aktien: frische 5m-Bestaetigung fehlt",
    "fresh_5m_state_missing_wait_retest": "Aktien: frische 5m-Bestaetigung fehlt, Retest abwarten",
    "extended_long_fading_wait_retest": "Long erweitert und fading: Retest abwarten",
    "hard_extended_long_wait_retest": "Long zu weit gelaufen: Retest abwarten",
    "drop_too_extended_no_chase": "Short/Crash-Drop schon sehr erweitert",
    "current_candle_green_reclaim": "Short: aktuelle Kerze reclaimed",
    "not_closing_near_low": "Short: Kurs schliesst nicht nahe Tagestief",
    "target_already_missed": "TP1 bereits verpasst",
    "early_mover_action_not_alertable": "Crypto: nur Watch/Retest, kein Long-Jetzt",
    "early_mover_no_chase": "Crypto: No-Chase",
    "early_mover_late_to_tp1": "Crypto: zu nah/ueber TP1",
    "early_mover_chased_from_entry": "Crypto: zu weit vom Entry",
    "early_mover_retest_not_near_entry": "Crypto: Retest noch nicht nahe Entry",
    "early_mover_btc_headwind": "Crypto: BTC-Gegenwind",
    "early_mover_data_warning": "Crypto: Daten unvollstaendig/partial",
    "early_mover_blowoff_turnover": "Crypto: Blowoff/Turnover-Risiko",
    "early_mover_turnover_without_alpha": "Crypto: viel Umsatz ohne Alpha",
    "early_mover_execution_liquidity_too_thin": "Crypto: Orderbuch/Perp-Liquiditaet zu duenn",
    "early_mover_live_rr_below_threshold": "Crypto: Live R:R unter Mindestwert",
    "early_mover_weak_targets": "Crypto: Zielzonen zu eng/ungueltig",
    "early_mover_htf_current_bar_rejecting": "Crypto: 4H-Kerze rejected gerade",
    "early_mover_htf_pullback_after_spike": "Crypto: 4H-Pullback nach Spike",
    "early_mover_htf_two_red_after_spike": "Crypto: zwei rote 4H-Kerzen nach Spike",
    "early_mover_htf_lower_high_after_sweep": "Crypto: 4H Lower-High nach Sweep",
    "early_mover_htf_active_red_candle": "Crypto: aktive rote 4H-Kerze",
    "early_mover_1m_trigger_disabled": "Crypto: 1m-Trigger deaktiviert, Trade braucht 5m-Bestaetigung",
    "early_mover_1m_trigger_watch_only": "Crypto: 1m-Trigger deaktiviert, Trade braucht 5m-Bestaetigung",
    "early_mover_execution_timeframe_disabled_use_5m": "Crypto: nur 5m-Execution-Trigger erlaubt",
    "no_fresh_5m_trigger": "kein frischer 5m Trigger",
    "micro_trigger_missing": "Pump/Dump: Micro-Crack fehlt",
    "pump_continuation_risk": "Pump laeuft noch, Short zu frueh",
    "safety_not_ok": "Safety-Check nicht OK",
    "safety_failed": "Safety-Check nicht OK",
    "risk_too_wide": "Stop/Risk zu breit",
    "rr_too_low": "R:R zu schwach",
    "early_crack_score_too_low": "Crack-Qualitaet zu schwach",
    "wait_for_dump_trigger": "Dump-Trigger abwarten",
    "btc_risk_on_wait_for_deeper_crack": "BTC risk-on: tieferen Crack abwarten",
    "turn_not_confirmed": "Turn/Rejection nicht bestaetigt",
    "no_first_crack": "erster Strukturbruch fehlt",
    "crack_structure_weak": "Crack-Struktur zu schwach",
    "listing_age_expired": "New-Listing-Fenster vorbei",
    "tp1_missed": "TP1 bereits verpasst",
    "tp2_missed": "TP2 bereits verpasst",
    "not_new_listing_dump": "kein echter New-Listing-Dump",
    "listing_age_not_tradeable": "Listing-Alter nicht im Trade-Fenster",
    "crypto_strategy_watch_only": "Crypto-Strategie ist Watch-only",
    "no_crypto_tradeable_signal": "kein tradebares Crypto-Signal",
    "no_crypto_execution_trigger": "kein Crypto-Execution-Trigger",
    "partial_crypto_data": "Crypto-Daten unvollstaendig",
}


def _alert_reason_label(reason: str) -> str:
    reason = str(reason or "").strip()
    if not reason:
        return "Unbekannter Blocker"
    return _ALERT_SUPPRESSION_LABELS.get(reason, reason.replace("_", " "))


def _top_alert_reasons(reason_counts: Dict[str, int], max_items: int = 8) -> List[Dict[str, Any]]:
    return [
        {"reason": reason, "label": _alert_reason_label(reason), "count": count}
        for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:max_items]
    ]


def _format_alert_suppression_summary(
    suppressed: Dict[str, int],
    grade_counts: Optional[Dict[str, int]] = None,
    max_items: int = 8,
) -> str:
    parts = [
        f"{reason}={count} ({_alert_reason_label(reason)})"
        for reason, count in sorted(suppressed.items(), key=lambda item: (-item[1], item[0]))[:max_items]
    ]
    summary = ", ".join(parts) if parts else "no_candidates"
    if grade_counts:
        grades = ", ".join(
            f"{grade}:{count}"
            for grade, count in sorted(grade_counts.items(), key=lambda item: str(item[0]))
        )
        if grades:
            summary = f"{summary}; grades={grades}"
    return summary[:900]


def _load_email_dedupe(now: Optional[float] = None, max_keep_seconds: int = 7 * 86400) -> Dict[str, float]:
    now = now or time.time()
    try:
        if not os.path.exists(_EMAIL_DEDUPE_FILE):
            return {}
        with open(_EMAIL_DEDUPE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        dedupe: Dict[str, float] = {}
        for key, ts in raw.items():
            try:
                ts_float = float(ts)
            except (TypeError, ValueError):
                continue
            if now - ts_float <= max_keep_seconds:
                dedupe[str(key)] = ts_float
        return dedupe
    except Exception:
        return {}


def _alert_dedupe_ttl_seconds(scanner_name: str) -> int:
    scanner = str(scanner_name or "").lower()
    if scanner == "biotech":
        return _BIOTECH_ALERT_DEDUPE_SEC
    return _EMAIL_COOLDOWN_SEC


def _email_dedupe_ttl_for_key(key: str) -> int:
    key = str(key or "")
    if key.startswith("crash_stock_"):
        return _CRASH_ALERT_DEDUPE_SEC
    if key == _EARLY_MOVER_DIGEST_KEY:
        return _EARLY_MOVER_DIGEST_DEDUPE_SEC
    if key == _EARLY_MOVER_ARMED_DIGEST_KEY:
        return _EARLY_MOVER_ARMED_DIGEST_DEDUPE_SEC
    if key.startswith("early_movers_armed_"):
        return _EMAIL_COOLDOWN_SEC
    if key.startswith("biotech_"):
        return _BIOTECH_ALERT_DEDUPE_SEC
    return _EMAIL_COOLDOWN_SEC


def _email_dedupe_status(now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    dedupe = _load_email_dedupe(now=now)
    recent = []
    for key, ts in sorted(dedupe.items(), key=lambda item: item[1], reverse=True)[:20]:
        ttl = _email_dedupe_ttl_for_key(key)
        recent.append({
            "key": key,
            "timestamp": datetime.fromtimestamp(ts).isoformat(),
            "age_seconds": int(max(0, now - ts)),
            "remaining_seconds": int(max(0, ttl - (now - ts))),
        })
    return {
        "file": _EMAIL_DEDUPE_FILE,
        "file_exists": os.path.exists(_EMAIL_DEDUPE_FILE),
        "entries": len(dedupe),
        "active_crash_entries": len([key for key in dedupe if key.startswith("crash_stock_")]),
        "recent": recent,
    }


def _save_email_dedupe(dedupe: Dict[str, float]) -> None:
    tmp_path = f"{_EMAIL_DEDUPE_FILE}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(dedupe, f)
        os.replace(tmp_path, _EMAIL_DEDUPE_FILE)
    except Exception as exc:
        print(f"[Alert] Dedupe-Datei konnte nicht gespeichert werden: {exc}")
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def _email_dedupe_active(key: str, ttl_seconds: int, now: Optional[float] = None) -> bool:
    now = now or time.time()
    dedupe = _load_email_dedupe(now=now)
    last = dedupe.get(key)
    return last is not None and now - last < ttl_seconds


def _email_dedupe_remaining(key: str, ttl_seconds: int, now: Optional[float] = None) -> int:
    now = now or time.time()
    dedupe = _load_email_dedupe(now=now)
    last = dedupe.get(key)
    if last is None:
        return 0
    return int(max(0, ttl_seconds - (now - last)))


def _bearish_stock_alert_key(ticker: str) -> str:
    return f"bearish_stock_{str(ticker or '').strip().upper()}"


def _bearish_stock_alert_remaining(ticker: str, now: Optional[float] = None) -> int:
    if not ticker:
        return 0
    return _email_dedupe_remaining(_bearish_stock_alert_key(ticker), _BEARISH_STOCK_ALERT_DEDUPE_SEC, now)


def _mark_bearish_stock_alert(ticker: str, now: Optional[float] = None) -> None:
    if ticker:
        _email_dedupe_mark(_bearish_stock_alert_key(ticker), now=now)


def _email_dedupe_mark(key: str, now: Optional[float] = None) -> None:
    now = now or time.time()
    dedupe = _load_email_dedupe(now=now)
    dedupe[key] = now
    _save_email_dedupe(dedupe)


def _email_dedupe_claim(key: str, ttl_seconds: int, now: Optional[float] = None) -> bool:
    """Return True only once per key+TTL, even after process restarts."""
    now = now or time.time()
    if _email_dedupe_active(key, ttl_seconds, now=now):
        return False
    _email_dedupe_mark(key, now=now)
    return True


def _email_has_blocked_etf_content(subject: str, body_html: str) -> bool:
    """Hard guard: this app mails trade candidates, not ETF/ETP hedge watchlists."""
    content = f"{subject or ''} {body_html or ''}".upper()
    if any(marker in content for marker in (
        "INVERSE ETF",
        "INVERSE ETFS",
        "LEVERAGED ETF",
        "LEVERAGED ETFS",
        "3X SHORT",
        "2X SHORT",
    )):
        return True
    tokens = set(re.findall(r"\b[A-Z]{2,6}\b", content))
    if tokens & _EMAIL_BLOCKED_ETF_TICKERS:
        return True
    return any(_looks_like_non_stock_etp_symbol(token) for token in tokens)


def _alert_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return val


def _score_grade_for_value(score_value: Any) -> Tuple[str, str]:
    score = _alert_float(score_value, 0) or 0
    if score >= 80:
        return "S", "Excellent"
    if score >= 60:
        return "A", "Stark"
    if score >= 40:
        return "B", "Solide"
    if score >= 25:
        return "C", "Schwach"
    return "D", "Uninteressant"


def _alert_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "ja"):
        return True
    if text in ("0", "false", "no", "n", "nein", "none", "null", ""):
        return False
    return default


def _extract_alert_grade(row: Dict[str, Any]) -> str:
    for key in ("BI_Grade", "Grade", "grade", "rating", "ExhGrade", "base_grade"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper()
    return ""


def _extract_alert_score(row: Dict[str, Any]) -> Any:
    nested = row.get("signal") if isinstance(row.get("signal"), dict) else {}
    pump_data = nested.get("pump_data") if isinstance(nested.get("pump_data"), dict) else {}
    keys = (
        "BI_Score", "Score", "score", "Alpha", "Setup_Score",
        "exhaustion_score", "ExhScore", "exh_score", "raw_score",
        "SellProb", "micro_score", "MicroScore",
    )
    for source in (row, nested, pump_data):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return 0


def _extract_alert_rvol(row: Dict[str, Any]) -> Optional[float]:
    for key in ("RVOL", "rvol", "relative_volume"):
        if key in row:
            return _alert_float(row.get(key))
    return None


def _extract_alert_ticker(row: Dict[str, Any]) -> str:
    for key in ("ticker", "Ticker", "symbol", "Symbol", "contract"):
        value = row.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def _extract_alert_price(row: Dict[str, Any]) -> Any:
    for key in ("Preis", "Price", "price", "current", "current_price", "entry"):
        value = row.get(key)
        if value not in (None, ""):
            return value
    return 0


def _alert_get_any(row: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Read scanner rows safely across old UI/cache column names."""
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def _extract_new_listing_signal_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    sig = row.get("signal", {}) if isinstance(row, dict) else {}
    nested = sig if isinstance(sig, dict) else {}
    pump_data = nested.get("pump_data", {}) if isinstance(nested.get("pump_data", {}), dict) else {}
    timing_text = str(nested.get("timing", sig if not isinstance(sig, dict) else row.get("timing", "")) or "")
    row_source = str(row.get("source", "") or "").lower()
    listing_source = str(
        nested.get("listing_source", pump_data.get("listing_source", row.get("listing_source", ""))) or ""
    ).lower()
    return {
        "grade": str(nested.get("grade", row.get("grade", "")) or "").strip().upper(),
        "timing": timing_text,
        "timing_quality": _alert_float(nested.get("timing_quality", row.get("timing_quality")), 0) or 0,
        "rr_effective": _alert_float(
            nested.get("rr_effective", nested.get("rr1", row.get("rr_effective", row.get("rr1")))),
            0,
        ) or 0,
        "safety_ok": _alert_bool(nested.get("safety_ok", row.get("safety_ok", False))),
        "tp1_missed": _alert_bool(nested.get("tp1_missed", row.get("tp1_missed", False))),
        "tp2_missed": _alert_bool(nested.get("tp2_missed", row.get("tp2_missed", False))),
        "confirmation_ok": _alert_bool(nested.get("confirmation_ok", row.get("confirmation_ok", False))),
        "continuation_risk": _alert_bool(nested.get("continuation_risk", row.get("continuation_risk", False))),
        "risk_pct": _alert_float(nested.get("risk_pct", row.get("risk_pct")), 999) or 999,
        "signal_quality": str(nested.get("signal_quality", row.get("signal_quality", "")) or "").lower(),
        "row_source": row_source,
        "listing_source": listing_source,
        "listing_trade_ok": _alert_bool(nested.get("listing_trade_ok", pump_data.get("listing_trade_ok", row.get("listing_trade_ok", False)))),
        "listing_age_hours": _alert_float(nested.get("listing_age_hours", pump_data.get("listing_age_hours", row.get("listing_age_hours")))),
        "trade_category": str(nested.get("trade_category", row.get("trade_category", "")) or ""),
        "micro_required": _alert_bool(nested.get("micro_required", row.get("micro_required", True)), True),
        "micro_trigger_ok": _alert_bool(
            nested.get("micro_trigger_ok", row.get("micro_trigger_ok", pump_data.get("micro_trigger_ok", False)))
        ),
    }


def _new_listing_rule_reasons(row: Dict[str, Any]) -> List[str]:
    fields = _extract_new_listing_signal_fields(row)
    reasons: List[str] = []
    timing_upper = fields["timing"].upper()
    row_source = fields["row_source"]
    listing_source = fields["listing_source"]
    if row_source and row_source not in ("signals", "new_listing"):
        reasons.append("not_active_short_signal")
    if not listing_source:
        reasons.append("listing_source_unknown")
    elif listing_source != "new_listing":
        reasons.append("not_new_listing_dump")
    if not fields["listing_trade_ok"]:
        reasons.append("listing_age_not_tradeable")
    if fields["timing_quality"] < 4 or "SHORT" not in timing_upper:
        reasons.append("not_active_short_timing")
    if not fields["safety_ok"]:
        reasons.append("safety_not_ok")
    if fields["tp1_missed"] or fields["tp2_missed"]:
        reasons.append("target_already_missed")
    if fields["rr_effective"] < _NEW_LISTING_MIN_ALERT_RR:
        reasons.append("rr_below_alert_threshold")
    if not fields["confirmation_ok"]:
        reasons.append("turn_not_confirmed")
    if fields["continuation_risk"]:
        reasons.append("pump_continuation_risk")
    if fields["micro_required"] and not fields["micro_trigger_ok"]:
        reasons.append("micro_trigger_missing")
    if fields["risk_pct"] > 35:
        reasons.append("risk_too_wide")
    if fields["signal_quality"] and fields["signal_quality"] != "tradeable":
        reasons.append("not_tradeable_signal_quality")
    return reasons


def _extract_early_mover_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    setup = row.get("trade_setup", {}) if isinstance(row.get("trade_setup"), dict) else {}
    btc = row.get("btc_context", setup.get("btc_context", {}))
    btc_context = btc if isinstance(btc, dict) else {}
    risk_flags = row.get("risk_flags", setup.get("risk_flags", []))
    if not isinstance(risk_flags, list):
        risk_flags = [str(risk_flags)] if risk_flags else []
    distance_to_entry_r = _alert_float(row.get("distance_to_entry_r", setup.get("distance_to_entry_r")))
    btc_24h = _alert_float(btc_context.get("btc_24h"), 0) or 0
    btc_7d = _alert_float(btc_context.get("btc_7d"), 0) or 0
    alpha_24h = _alert_float(row.get("BtcRelative24h", btc_context.get("alpha_24h")), 0) or 0
    change24 = _alert_float(row.get("Change24h", row.get("change_24h", row.get("change24h"))), 0) or 0
    return {
        "direction": str(row.get("direction", setup.get("direction", "")) or "").upper(),
        "trade_action": str(row.get("trade_action", setup.get("trade_action", "")) or "").upper(),
        "entry_status": str(row.get("entry_status", setup.get("entry_status", "")) or "").upper(),
        "entry_quality": str(row.get("entry_quality", setup.get("entry_quality", "")) or "").upper(),
        "signal_quality": str(row.get("signal_quality", "") or "").lower(),
        "live_rr": _alert_float(row.get("live_rr_ratio", setup.get("live_rr")), 0) or 0,
        "distance_to_entry_r": distance_to_entry_r if distance_to_entry_r is not None else 999,
        "late_to_tp1": _alert_bool(row.get("late_to_tp1", setup.get("late_to_tp1", False))),
        "execution_trigger_ok": _alert_bool(row.get("execution_trigger_ok", False)),
        "partial_data": _alert_bool(row.get("partial_data", row.get("data_partial", False))),
        "data_warning": row.get("data_warning"),
        "btc_tailwind": _alert_bool(btc_context.get("tailwind"), True),
        "btc_24h": btc_24h,
        "btc_7d": btc_7d,
        "btc_hard_headwind": bool(btc_24h <= -3.0 or btc_7d <= -7.0),
        "change24": change24,
        "vol_mcap": _alert_float(row.get("VolMCapRatio", row.get("vol_mcap", row.get("Vol/MCap"))), 0) or 0,
        "alpha_24h": alpha_24h,
        "risk_flags": [str(flag).lower() for flag in risk_flags],
    }


def _early_mover_btc_allows_long(fields: Dict[str, Any]) -> bool:
    """BTC may be neutral/choppy, but not in a hard dump unless the setup is only watched."""
    if fields.get("btc_hard_headwind"):
        return False
    if fields.get("btc_tailwind"):
        return True
    return (fields.get("alpha_24h") or 0) >= 1.0 and (fields.get("change24") or 0) >= 1.0


def _early_mover_alert_key(row: Dict[str, Any], ticker: Optional[str] = None) -> str:
    symbol = (ticker or _extract_alert_ticker(row)).upper()
    action = str(row.get("trade_action", row.get("entry_status", "long")) or "long").lower()
    action = re.sub(r"[^a-z0-9_]+", "_", action).strip("_") or "long"
    return f"early_movers_{symbol}_{action}" if symbol else ""


def _early_mover_long_rule_reasons(row: Dict[str, Any]) -> List[str]:
    """Only mail Early-Mover crypto rows that are close to an actual long decision."""
    fields = _extract_early_mover_fields(row)
    reasons: List[str] = []
    action = fields["trade_action"]
    risk_flags = set(fields["risk_flags"])

    if fields["direction"] and fields["direction"] != "LONG":
        reasons.append("early_mover_not_long")
    if action not in ("LONG_TRIGGER", "WAIT_FOR_RETEST"):
        reasons.append("early_mover_action_not_alertable")
    if fields["signal_quality"] == "no_chase" or "overheated_phase3" in risk_flags:
        reasons.append("early_mover_no_chase")
    if not _early_mover_btc_allows_long(fields):
        reasons.append("early_mover_btc_headwind")
    if fields["partial_data"] or fields["data_warning"] or "data_warning" in risk_flags:
        reasons.append("early_mover_data_warning")
    if fields["late_to_tp1"] or "tp1_already_reached" in risk_flags:
        reasons.append("early_mover_late_to_tp1")
    if "chased_from_entry" in risk_flags:
        reasons.append("early_mover_chased_from_entry")
    if "very_high_volume_turnover" in risk_flags:
        reasons.append("early_mover_blowoff_turnover")
    raw_turnover_churn = (
        fields["vol_mcap"] >= _EARLY_MOVER_TURNOVER_CHURN_BLOCK_PCT
        and fields["alpha_24h"] <= 0
        and fields["change24"] < 2.0
    )
    if raw_turnover_churn or risk_flags.intersection({"turnover_without_alpha", "extreme_turnover_churn"}):
        reasons.append("early_mover_turnover_without_alpha")
    if risk_flags.intersection({"thin_perp_liquidity", "thin_orderbook", "market_impact_risk", "no_perp_execution_market"}):
        reasons.append("early_mover_execution_liquidity_too_thin")
    if risk_flags.intersection({"weak_structural_targets", "duplicate_targets", "targets_too_close", "invalid_target_plan"}) or _early_mover_target_plan_issues(row):
        reasons.append("early_mover_weak_targets")
    if fields["live_rr"] < _EARLY_MOVER_MIN_ALERT_RR:
        reasons.append("early_mover_live_rr_below_threshold")
    # Email delivery performs a fresh exchange-trigger check before sending.
    # Do not block here just because the cached scan row was not pre-confirmed.
    if action == "WAIT_FOR_RETEST" and fields["distance_to_entry_r"] > _EARLY_MOVER_RETEST_MAX_DISTANCE_R:
        reasons.append("early_mover_retest_not_near_entry")
    return reasons


def _flatten_early_mover_rows(payload_or_rows: Any) -> List[Dict[str, Any]]:
    containers = payload_or_rows if isinstance(payload_or_rows, list) else [payload_or_rows]
    rows: List[Dict[str, Any]] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        coins = container.get("coins")
        if isinstance(coins, list):
            rows.extend([coin for coin in coins if isinstance(coin, dict)])
        elif _extract_alert_ticker(container):
            rows.append(container)
    return rows


def _format_alert_price(value: Any) -> str:
    price = _alert_float(value)
    if price is None:
        return "-"
    if abs(price) >= 100:
        return f"${price:,.2f}"
    if abs(price) >= 1:
        return f"${price:,.4f}".rstrip("0").rstrip(".")
    return f"${price:.8f}".rstrip("0").rstrip(".")


def _first_trade_level(row: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    """Read a positive trade level from top-level or nested scanner payloads."""
    sources: List[Dict[str, Any]] = [row]
    for nested_key in ("trade_setup", "setup", "signal"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
            pump_data = nested.get("pump_data")
            if isinstance(pump_data, dict):
                sources.append(pump_data)
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            value = _alert_float(source.get(key))
            if value is not None and value > 0:
                return value
    return None


def _infer_alert_direction(row: Dict[str, Any]) -> str:
    setup = row.get("trade_setup", {}) if isinstance(row.get("trade_setup"), dict) else {}
    text = " ".join(str(value or "") for value in (
        row.get("Signal_Direction"),
        row.get("BI_Direction"),
        row.get("direction"),
        row.get("_direction"),
        row.get("side"),
        row.get("trade_action"),
        setup.get("direction"),
        setup.get("trade_action"),
    )).upper()
    if "SHORT" in text or text == "SELL":
        return "SHORT"
    if "LONG" in text or "BUY" in text:
        return "LONG"
    return ""


def _alert_trade_levels(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Entry/Stop/TP1/TP2 for every mail path.

    If a legacy scanner row has Entry/Stop but no targets, derive conservative
    R-multiple targets so alerts never show an idealized price without a plan.
    """
    return normalize_alert_trade_levels(
        row,
        price_fallback=_extract_alert_price(row),
        allow_estimated=True,
    )


def _format_alert_plan_html(row: Dict[str, Any]) -> str:
    levels = _alert_trade_levels(row)
    if not levels.get("valid"):
        error_text = html.escape(", ".join(levels.get("errors", [])[:3]) or "invalid_trade_plan")
        return (
            '<span style="color:#dc2626;font-weight:bold">Kein gueltiger Trade-Plan</span>'
            f'<br><span style="color:#64748b;font-size:12px">{error_text}</span>'
        )
    entry = _format_alert_price(levels.get("entry"))
    stop = _format_alert_price(levels.get("stop"))
    tp1 = _format_alert_price(levels.get("tp1"))
    tp2 = _format_alert_price(levels.get("tp2"))
    rr = levels.get("rr")
    rr_text = f'<br><span style="color:#64748b;font-size:12px">R:R {rr:.2f}</span>' if isinstance(rr, (int, float)) else ""
    source_text = (
        '<br><span style="color:#b45309;font-size:11px">Level geschaetzt - native Scanner-Level fehlen/teilweise fehlen</span>'
        if levels.get("estimated") else ""
    )
    return (
        f'<span>Entry <b>{entry}</b></span><br>'
        f'<span>Stop <b style="color:#dc2626">{stop}</b></span><br>'
        f'<span>TP1/TP2 <b style="color:#059669">{tp1} / {tp2}</b></span>'
        f'{rr_text}'
        f'{source_text}'
    )


def _alert_trade_plan_ok(
    row: Dict[str, Any],
    min_rr: float = _ALERT_MIN_LEVEL_RR,
    require_native_levels: bool = True,
) -> bool:
    levels = _alert_trade_levels(row)
    if not levels.get("valid"):
        return False
    if require_native_levels and levels.get("estimated"):
        return False
    rr = levels.get("rr")
    return not isinstance(rr, (int, float)) or rr >= min_rr


def _alert_trade_health_reasons(row: Dict[str, Any], scanner_name: str) -> List[str]:
    """Final execution-quality gate for actionable mails.

    Scanner scores answer "interesting?". This guard answers "tradeable now?".
    """
    levels = _alert_trade_levels(row)
    health_row = dict(row)
    if levels.get("valid"):
        health_row.setdefault("entry", levels.get("entry"))
        health_row.setdefault("Entry", levels.get("entry"))
        health_row.setdefault("stop_loss", levels.get("stop"))
        health_row.setdefault("StopLoss", levels.get("stop"))
        health_row.setdefault("tp1", levels.get("tp1"))
        health_row.setdefault("TP1", levels.get("tp1"))
        health_row.setdefault("tp2", levels.get("tp2"))
        health_row.setdefault("TP2", levels.get("tp2"))
        health_row.setdefault("direction", levels.get("direction"))
    if "current_price" not in health_row:
        health_row["current_price"] = _extract_alert_price(health_row)

    health_scanner = {
        "early_movers": "crypto_early_movers",
        "crypto_strategy": "crypto_strategy",
        "new_listing": "new_listing",
    }.get(scanner_name, scanner_name)
    health = calculate_trade_health(health_row, scanner_name=health_scanner)
    decision = str(health.get("decision", "") or "").upper()
    reasons: List[str] = []
    if decision != "TRADEABLE":
        reasons.append(f"trade_health_{decision.lower() or 'not_tradeable'}")
    if int(health.get("health_score") or 0) < 80:
        reasons.append("trade_health_score_below_80")
    if str(health.get("chase_risk", "") or "").upper() in {"HIGH", "CRITICAL"}:
        reasons.append("trade_health_chase_risk")
    if str(health.get("fakeout_risk", "") or "").upper() in {"HIGH", "CRITICAL"}:
        reasons.append("trade_health_fakeout_risk")
    if str(health.get("liquidity_risk", "") or "").upper() == "CRITICAL":
        reasons.append("trade_health_liquidity_risk")
    return list(dict.fromkeys(reasons))


def _stock_alert_trade_score(row: Dict[str, Any], scanner_name: str) -> int:
    """Turn a stock scanner setup score into a real trade-now score.

    Stock scanners can be correct that a symbol is interesting while the current
    entry is still late, fading, missing a fresh 5m state, or using weak levels.
    Mail decisions should therefore be capped by execution quality, not by the
    raw setup score alone.
    """
    raw_score = _alert_float(_extract_alert_score(row), 0) or 0
    levels = _alert_trade_levels(row)
    health_row = dict(row)
    if levels.get("valid"):
        health_row.setdefault("entry", levels.get("entry"))
        health_row.setdefault("Entry", levels.get("entry"))
        health_row.setdefault("stop_loss", levels.get("stop"))
        health_row.setdefault("StopLoss", levels.get("stop"))
        health_row.setdefault("tp1", levels.get("tp1"))
        health_row.setdefault("TP1", levels.get("tp1"))
        health_row.setdefault("tp2", levels.get("tp2"))
        health_row.setdefault("TP2", levels.get("tp2"))
        health_row.setdefault("direction", levels.get("direction"))
    health_row.setdefault("current_price", _extract_alert_price(health_row))

    health = calculate_trade_health(health_row, scanner_name=scanner_name)
    health_score = int(_alert_float(health.get("health_score"), 0) or 0)
    entry_score = int(_alert_float(health.get("entry_quality_score"), 0) or 0)
    fakeout_score = int(_alert_float(health.get("fakeout_risk_score"), 0) or 0)
    liquidity_score = int(_alert_float(health.get("liquidity_score"), 0) or 0)
    weakest_pillar = min([score for score in (entry_score, fakeout_score, liquidity_score) if score is not None] or [0])

    score = min(
        raw_score * 0.42 + health_score * 0.38 + weakest_pillar * 0.20,
        weakest_pillar + 18,
    )

    decision = str(health.get("decision") or "").upper()
    if decision == "WAIT_FOR_CONTINUATION":
        score = min(score, 76)
    elif decision in {"WAIT_FOR_RETEST", "WAIT_FOR_TRIGGER", "WATCH_ONLY"}:
        score = min(score, 69)
    elif decision and decision != "TRADEABLE":
        score = min(score, 45)

    if not levels.get("valid") or levels.get("estimated"):
        score = min(score, 45)

    if scanner_name in _LONG_ENTRY_ALERT_SCANNERS:
        long_reasons = _long_entry_rule_reasons(row)
        if long_reasons:
            score = min(score, 69 if any(reason.endswith("wait_retest") for reason in long_reasons) else 55)
    if scanner_name in ("bear", "bi_short"):
        short_reasons = _bear_short_rule_reasons(row)
        if short_reasons:
            if "drop_too_extended_no_chase" in short_reasons:
                score = min(score, 45)
            else:
                score = min(score, 62)

    return int(round(max(0, min(100, score))))


def _median_float(values: List[Any], default: float = 0.0) -> float:
    nums = sorted([v for v in (_alert_float(value) for value in values) if v is not None])
    if not nums:
        return default
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2


def _normalize_crypto_exchange(value: Any) -> str:
    exchange = str(value or "").strip().lower().replace(" ", "_")
    if exchange in ("mexc", "bitget", "binance", "crypto_com"):
        return exchange
    if exchange == "crypto.com":
        return "crypto_com"
    return exchange


def _compact_usd(value: Any) -> str:
    amount = _alert_float(value, 0) or 0
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount:.0f}"


def _book_liquidity_metrics(book: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    bids_raw = (book or {}).get("bids") or []
    asks_raw = (book or {}).get("asks") or []

    def _side(raw_side: Any) -> List[Tuple[float, float]]:
        parsed: List[Tuple[float, float]] = []
        for level in raw_side if isinstance(raw_side, list) else []:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            price = _alert_float(level[0])
            qty = _alert_float(level[1])
            if price and price > 0 and qty and qty > 0:
                parsed.append((price, qty))
        return parsed

    bids = sorted(_side(bids_raw), key=lambda x: x[0], reverse=True)
    asks = sorted(_side(asks_raw), key=lambda x: x[0])
    if not bids or not asks:
        return {"ok": False, "reason": "orderbook_empty"}

    bid = bids[0][0]
    ask = asks[0][0]
    mid = (bid + ask) / 2
    if mid <= 0 or ask <= bid:
        return {"ok": False, "reason": "orderbook_bad_prices"}

    def _depth(side: List[Tuple[float, float]], bps: float, is_ask: bool) -> float:
        if is_ask:
            cutoff = mid * (1 + bps / 10000)
            return sum(price * qty for price, qty in side if price <= cutoff)
        cutoff = mid * (1 - bps / 10000)
        return sum(price * qty for price, qty in side if price >= cutoff)

    bid_depth_10 = _depth(bids, 10, False)
    ask_depth_10 = _depth(asks, 10, True)
    bid_depth_25 = _depth(bids, 25, False)
    ask_depth_25 = _depth(asks, 25, True)
    bid_depth_50 = _depth(bids, 50, False)
    ask_depth_50 = _depth(asks, 50, True)
    total_bid_depth = sum(price * qty for price, qty in bids)
    total_ask_depth = sum(price * qty for price, qty in asks)
    return {
        "ok": True,
        "bid": round(bid, 12),
        "ask": round(ask, 12),
        "mid": round(mid, 12),
        "spread_bps": round((ask - bid) / mid * 10000, 2),
        "depth_10bps_bid_usd": round(bid_depth_10, 2),
        "depth_10bps_ask_usd": round(ask_depth_10, 2),
        "depth_10bps_min_usd": round(min(bid_depth_10, ask_depth_10), 2),
        "depth_25bps_bid_usd": round(bid_depth_25, 2),
        "depth_25bps_ask_usd": round(ask_depth_25, 2),
        "depth_25bps_min_usd": round(min(bid_depth_25, ask_depth_25), 2),
        "depth_50bps_bid_usd": round(bid_depth_50, 2),
        "depth_50bps_ask_usd": round(ask_depth_50, 2),
        "depth_50bps_min_usd": round(min(bid_depth_50, ask_depth_50), 2),
        "top_book_bid_usd": round(total_bid_depth, 2),
        "top_book_ask_usd": round(total_ask_depth, 2),
        "top_book_min_usd": round(min(total_bid_depth, total_ask_depth), 2),
    }


def _early_mover_static_liquidity(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Execution liquidity, not CoinGecko popularity.

    CoinGecko volume is aggregate market volume. For a live signal we need the
    actual perp/trigger venue to have enough turnover; otherwise small orders
    can paint the chart and invalidate the scanner edge.
    """
    has_perp = bool(entry.get("HasPerp"))
    perp_volume = _alert_float(entry.get("PerpVolume24h"), 0) or 0
    spot_volume = _alert_float(entry.get("Vol24h") or entry.get("volume") or entry.get("dollar_volume"), 0) or 0
    flags: List[str] = []
    reasons: List[str] = []
    score_penalty = 0
    hard_block = False

    if not has_perp:
        flags.append("no_perp_execution_market")
        reasons.append("kein Perp/Trigger-Markt - nur Watchlist")
        score_penalty += 25
        hard_block = True
    elif perp_volume < _EARLY_MOVER_MIN_PERP_VOLUME_USD:
        flags.append("thin_perp_liquidity")
        reasons.append(f"Perp-Volumen duenn: {_compact_usd(perp_volume)}/24h")
        score_penalty += 25
        hard_block = True
    elif perp_volume < _EARLY_MOVER_WARN_PERP_VOLUME_USD:
        flags.append("perp_liquidity_watch")
        reasons.append(f"Perp-Volumen nur mittel: {_compact_usd(perp_volume)}/24h")
        score_penalty += 8

    if spot_volume and spot_volume < 1_000_000:
        flags.append("thin_spot_liquidity")
        reasons.append(f"Gesamtvolumen duenn: {_compact_usd(spot_volume)}/24h")
        score_penalty += 10

    return {
        "hard_block": hard_block,
        "score_penalty": score_penalty,
        "flags": flags,
        "reasons": reasons,
        "perp_volume_24h": round(perp_volume, 2),
        "spot_volume_24h": round(spot_volume, 2),
    }


def _early_mover_orderbook_liquidity(contract: str, exchange: str) -> Dict[str, Any]:
    if not HAS_NEW_LISTING_SCANNER:
        return {"ok": False, "reason": "orderbook_module_missing", "reasons": ["orderbook_module_missing"]}
    try:
        book = fetch_orderbook_for(str(contract), exchange, depth=50)
    except Exception as exc:
        return {"ok": False, "reason": "orderbook_fetch_failed", "detail": str(exc)[:120], "reasons": ["orderbook_fetch_failed"]}

    metrics = _book_liquidity_metrics(book)
    if not metrics.get("ok"):
        metrics["reasons"] = [metrics.get("reason", "orderbook_unavailable")]
        return metrics

    reasons = []
    if metrics["spread_bps"] > _EARLY_MOVER_MAX_SPREAD_BPS:
        reasons.append("spread_too_wide")
    if metrics["depth_10bps_min_usd"] < _EARLY_MOVER_MIN_DEPTH_10BPS_USD:
        reasons.append("thin_book_10bps")
    if metrics["depth_25bps_min_usd"] < _EARLY_MOVER_MIN_DEPTH_25BPS_USD:
        reasons.append("thin_book_25bps")
    if metrics["depth_50bps_min_usd"] < _EARLY_MOVER_MIN_DEPTH_50BPS_USD:
        reasons.append("thin_book_50bps")

    metrics.update({
        "ok": not reasons,
        "reason": "ok" if not reasons else "thin_orderbook_market_impact",
        "reasons": reasons,
        "thresholds": {
            "max_spread_bps": _EARLY_MOVER_MAX_SPREAD_BPS,
            "min_depth_10bps_usd": _EARLY_MOVER_MIN_DEPTH_10BPS_USD,
            "min_depth_25bps_usd": _EARLY_MOVER_MIN_DEPTH_25BPS_USD,
            "min_depth_50bps_usd": _EARLY_MOVER_MIN_DEPTH_50BPS_USD,
        },
    })
    return metrics


def _early_mover_trigger_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
    btc = row.get("btc_context", setup.get("btc_context", {}))
    btc_context = btc if isinstance(btc, dict) else {}
    action = str(row.get("trade_action", row.get("entry_status", "")) or "").upper()
    phase = _alert_float(row.get("phase", row.get("Phase")), 0) or 0
    change24 = _alert_float(row.get("Change24h", row.get("change_24h", row.get("change24h"))), 0) or 0
    vol_mcap = _alert_float(row.get("VolMCapRatio", row.get("vol_mcap", row.get("Vol/MCap"))), 0) or 0
    alpha_24h = _alert_float(row.get("BtcRelative24h", btc_context.get("alpha_24h")), 0) or 0
    distance_value = _alert_float(row.get("distance_to_entry_r"))
    distance_r = distance_value if distance_value is not None else 999
    risk_flags = [str(flag).lower() for flag in (row.get("risk_flags") or []) if flag is not None]
    fast_coin = bool(abs(change24) >= 8 or vol_mcap >= 35 or int(phase) == 2 or "breakout" in " ".join(risk_flags))
    near_retest = bool(action == "WAIT_FOR_RETEST" or distance_r <= _EARLY_MOVER_RETEST_MAX_DISTANCE_R)
    turnover_without_alpha = bool(
        vol_mcap >= _EARLY_MOVER_TURNOVER_CHURN_BLOCK_PCT
        and alpha_24h <= 0
        and change24 < 2.0
    )
    return {
        "action": action,
        "fast_coin": fast_coin,
        "near_retest": near_retest,
        "change24": change24,
        "vol_mcap": vol_mcap,
        "alpha_24h": alpha_24h,
        "distance_to_entry_r": distance_r,
        "requires_5m_confirmation": turnover_without_alpha or "turnover_without_alpha" in risk_flags,
    }


def _timeframe_seconds(timeframe: str) -> Optional[int]:
    tf = str(timeframe or "").strip().lower()
    return {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
        "1w": 604800,
    }.get(tf)


def _candle_epoch_seconds(bar: Dict[str, Any]) -> Optional[float]:
    if not isinstance(bar, dict):
        return None
    for key in ("timestamp", "open_time", "openTime", "time", "t", "ts", "start"):
        value = bar.get(key)
        if value in (None, ""):
            continue
        try:
            if isinstance(value, str) and not value.replace(".", "", 1).isdigit():
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            ts = float(value)
        except Exception:
            continue
        if ts <= 0:
            continue
        if ts > 1_000_000_000_000_000:
            ts = ts / 1_000_000_000
        elif ts > 10_000_000_000:
            ts = ts / 1000
        return ts
    return None


def _completed_candles_only(
    bars: List[Dict[str, Any]],
    timeframe: str,
    now_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Use only closed candles for execution checks.

    Live 5m bars can start bullish/bearish and then completely reverse before close.
    Trigger, no-chase and alert gates therefore must ignore the still-forming bar.
    """
    if not bars:
        return []
    seconds = _timeframe_seconds(timeframe)
    if not seconds:
        return list(bars)
    now_value = float(now_ts if now_ts is not None else time.time())
    latest_ts = _candle_epoch_seconds(bars[-1])
    if latest_ts is not None and latest_ts + seconds > now_value:
        return list(bars[:-1])
    return list(bars)


def _early_mover_execution_threshold(matched: List[str], profile: Dict[str, Any]) -> int:
    """Adaptive 5m trigger threshold by setup type.

    A clean retest/hold should not need the same score as a raw momentum breakout.
    The hard filters above still block chase candles, bad R-distance and thin books.
    """
    if not matched:
        return 999
    thresholds = []
    if "retest_hold" in matched:
        thresholds.append(64)
    if "higher_low_vwap_hold" in matched:
        thresholds.append(64)
    if "vwap_reclaim" in matched:
        thresholds.append(70)
    if "breakout" in matched:
        thresholds.append(72)
    if "trend_continuation" in matched:
        thresholds.append(70)
    threshold = min(thresholds) if thresholds else 76
    if profile.get("fast_coin") and (profile.get("alpha_24h") or 0) >= 2:
        threshold -= 3
    if profile.get("requires_5m_confirmation"):
        threshold = max(threshold, 70)
    return max(62, min(78, int(threshold)))


def _score_early_mover_trigger_bars(row: Dict[str, Any], bars: List[Dict[str, Any]], timeframe: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    timeframe = str(timeframe or "").lower()
    if timeframe != "5m":
        return {
            "ok": False,
            "reason": "execution_timeframe_disabled_use_5m",
            "timeframe": timeframe,
            "execution_score": 0,
        }

    min_bars = 12
    original_bar_count = len(bars or [])
    bars = _completed_candles_only(bars or [], timeframe)
    dropped_open_bar = bool(original_bar_count and len(bars) < original_bar_count)
    if not bars or len(bars) < min_bars:
        return {
            "ok": False,
            "reason": f"not_enough_{timeframe}_candles",
            "timeframe": timeframe,
            "execution_score": 0,
            "dropped_open_candle": dropped_open_bar,
        }

    try:
        clean = []
        for bar in bars:
            open_ = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            volume = float(bar.get("volume", 0) or 0)
            if open_ > 0 and close > 0 and high > low:
                cleaned_bar = {"open": open_, "high": high, "low": low, "close": close, "volume": max(volume, 0)}
                timestamp = _candle_epoch_seconds(bar)
                if timestamp is not None:
                    cleaned_bar["timestamp"] = timestamp
                clean.append(cleaned_bar)
        if len(clean) < min_bars:
            return {"ok": False, "reason": f"bad_{timeframe}_candles", "timeframe": timeframe, "execution_score": 0}

        recent_len = 24
        window_len = 8
        recent = clean[-recent_len:] if len(clean) >= recent_len else clean
        last = clean[-1]
        prev = clean[:-1]
        prev_window = prev[-window_len:] if len(prev) >= window_len else prev
        last_open = last["open"]
        last_high = last["high"]
        last_low = last["low"]
        last_close = last["close"]
        last_vol = last["volume"]

        typical_value = 0.0
        volume_sum = 0.0
        for bar in recent:
            typical = (bar["high"] + bar["low"] + bar["close"]) / 3
            typical_value += typical * bar["volume"]
            volume_sum += bar["volume"]
        vwap = typical_value / volume_sum if volume_sum > 0 else _median_float([b.get("close") for b in recent], last_close)
        median_vol = _median_float([b.get("volume", 0) for b in prev[-30:]], max(last_vol, 1))
        vol_ratio = last_vol / max(median_vol, 1)
        prev_high = max(bar["high"] for bar in prev_window)
        prev_low = min(bar["low"] for bar in prev_window)
        prev_close = prev[-1]["close"] if prev else last_close
        close_pos = (last_close - last_low) / max(last_high - last_low, 1e-9)
        candle_change_pct = ((last_close - last_open) / last_open * 100) if last_open else 0
        range_pct = ((last_high - last_low) / last_close * 100) if last_close else 0

        setup_obj = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
        tp1 = _alert_float(row.get("tp1") or setup_obj.get("tp1"))
        entry = _alert_float(row.get("entry") or setup_obj.get("entry"))
        stop = _alert_float(row.get("stop_loss") or row.get("stop") or setup_obj.get("stop_loss") or setup_obj.get("stop"))
        risk = abs(entry - stop) if entry is not None and stop is not None and entry != stop else None
        distance_r = ((last_close - entry) / risk) if entry is not None and risk and risk > 0 else None

        if tp1 is not None and last_close >= tp1:
            return {
                "ok": False,
                "reason": "tp1_already_reached_intraday",
                "timeframe": timeframe,
                "execution_score": 0,
                "last_close": round(last_close, 10),
            }

        breakout_vol = 1.25
        hold_vol = 1.10
        breakout_buffer = 1.0015
        max_safe_candle = 5.0
        max_safe_range = 7.0
        retest_reclaim = 1.0

        breakout = last_close > prev_high * breakout_buffer and vol_ratio >= breakout_vol and close_pos >= 0.60
        vwap_reclaim = prev_close < vwap and last_close > vwap and vol_ratio >= breakout_vol and close_pos >= 0.58
        hl_hold = last_close > vwap and last_low > prev_low and last_close > last_open and vol_ratio >= hold_vol and close_pos >= 0.55
        retest_hold = bool(
            entry is not None
            and risk
            and -0.10 <= (distance_r if distance_r is not None else 999) <= 0.35
            and last_low <= entry * 1.004
            and last_close >= entry * retest_reclaim
            and last_close > last_open
            and last_close >= vwap * 0.997
            and close_pos >= 0.55
            and vol_ratio >= hold_vol
        )
        continuation = bool(
            profile.get("fast_coin")
            and last_close > prev_close
            and last_close > vwap
            and close_pos >= 0.62
            and vol_ratio >= breakout_vol
            and (distance_r is None or distance_r <= 0.55)
        )

        matched = []
        score = 0.0
        if retest_hold:
            matched.append("retest_hold")
            score += 34
        if breakout:
            matched.append("breakout")
            score += 34
        if vwap_reclaim:
            matched.append("vwap_reclaim")
            score += 30
        if hl_hold:
            matched.append("higher_low_vwap_hold")
            score += 28
        if continuation and not matched:
            matched.append("trend_continuation")
            score += 25

        if vol_ratio >= 2.2:
            score += 20
        elif vol_ratio >= 1.6:
            score += 16
        elif vol_ratio >= hold_vol:
            score += 10

        if close_pos >= 0.78:
            score += 14
        elif close_pos >= 0.62:
            score += 10
        elif close_pos < 0.45:
            score -= 15

        if distance_r is not None:
            if -0.10 <= distance_r <= 0.25:
                score += 18
            elif distance_r <= 0.50:
                score += 10
            elif distance_r <= 0.75:
                score += 2
            else:
                score -= 28
            if distance_r < -0.25:
                score -= 18
        elif matched:
            score += 8

        chase_candle = candle_change_pct >= max_safe_candle and close_pos >= 0.86 and range_pct >= max_safe_range
        if chase_candle and (distance_r is None or distance_r > 0.45):
            score -= 35
            matched = [m for m in matched if m != "breakout"]
        elif candle_change_pct <= max_safe_candle:
            score += 6

        last_12 = clean[-12:] if len(clean) >= 12 else clean
        prior_24 = clean[-36:-12] if len(clean) >= 36 else clean[:-12]
        recent_high = max(bar["high"] for bar in last_12)
        recent_low = min(bar["low"] for bar in last_12)
        recent_range_pct = ((recent_high - recent_low) / last_close * 100) if last_close else 0
        recent_open = last_12[0]["open"] if last_12 else last_open
        recent_change_pct = ((last_close - recent_open) / recent_open * 100) if recent_open else 0
        consecutive_green = 0
        for bar in reversed(last_12):
            if bar["close"] > bar["open"]:
                consecutive_green += 1
            else:
                break
        prior_range_pct = 0.0
        if prior_24:
            prior_high = max(bar["high"] for bar in prior_24)
            prior_low = min(bar["low"] for bar in prior_24)
            prior_mid = _median_float([bar["close"] for bar in prior_24], last_close)
            prior_range_pct = ((prior_high - prior_low) / max(prior_mid, 1e-9) * 100) if prior_mid else 0
        near_range_high_pct = ((recent_high - last_close) / last_close * 100) if last_close else 999
        first_half = last_12[: max(1, len(last_12) // 2)]
        second_half = last_12[max(1, len(last_12) // 2):] or last_12
        higher_lows = min(bar["low"] for bar in second_half) >= min(bar["low"] for bar in first_half) * 0.997
        vwap_hold = last_close >= vwap * 0.997
        compression = bool(prior_range_pct > 0 and recent_range_pct <= max(1.25, prior_range_pct * 0.72))
        tight_coil = recent_range_pct <= max(2.2, min(4.0, abs(profile.get("change24", 0) or 0) * 0.35 + 1.2))
        near_breakout = near_range_high_pct <= 0.75
        previous_volume = _median_float([bar["volume"] for bar in prior_24], median_vol) if prior_24 else median_vol
        recent_volume = _median_float([bar["volume"] for bar in last_12[:-1]], median_vol) if len(last_12) > 1 else median_vol
        volume_dryup = bool(previous_volume > 0 and recent_volume <= previous_volume * 0.90)
        volume_wake = bool(vol_ratio >= 0.85)
        recent_extension = bool(recent_change_pct >= 5.0 or (consecutive_green >= 6 and recent_change_pct >= 3.0))
        pre_score = 0
        pre_reasons = []
        if vwap_hold:
            pre_score += 20
            pre_reasons.append("vwap_hold")
        if higher_lows:
            pre_score += 18
            pre_reasons.append("higher_lows")
        if compression or tight_coil:
            pre_score += 22
            pre_reasons.append("compression")
        if near_breakout:
            pre_score += 18
            pre_reasons.append("near_range_high")
        if volume_dryup:
            pre_score += 10
            pre_reasons.append("volume_dryup")
        if volume_wake:
            pre_score += 10
            pre_reasons.append("volume_wake")
        if recent_extension:
            pre_score -= 22
            pre_reasons.append("recent_5m_run_already_extended")
        if chase_candle or candle_change_pct >= max_safe_candle or range_pct >= max_safe_range:
            pre_score -= 25
            pre_reasons.append("anti_chase_block")
        if distance_r is not None:
            if distance_r <= _EARLY_MOVER_MAX_ARMED_DISTANCE_R:
                pre_score += 8
            elif distance_r > 0.65:
                pre_score -= 20
                pre_reasons.append("too_far_from_entry")
        pre_score = int(round(max(0, min(pre_score, 100))))
        pre_structure_ok = bool(
            vwap_hold
            and higher_lows
            and (compression or tight_coil)
            and near_breakout
            and not recent_extension
        )
        pre_breakout_ok = bool(pre_score >= _EARLY_MOVER_MIN_ARMED_PREBREAKOUT_SCORE and pre_structure_ok)
        if pre_score >= _EARLY_MOVER_MIN_ARMED_PREBREAKOUT_SCORE and not pre_structure_ok:
            pre_reasons.append("pre_breakout_structure_incomplete")

        threshold = _early_mover_execution_threshold(matched, profile)
        ok = bool(matched and score >= threshold)
        if ok:
            reason = f"adaptive_{timeframe}_{matched[0]}"
        elif chase_candle:
            reason = f"single_{timeframe}_candle_chase"
        elif not matched:
            reason = f"no_fresh_{timeframe}_trigger"
        else:
            reason = "execution_score_below_threshold"

        last_timestamp = _candle_epoch_seconds(last)
        return {
            "ok": ok,
            "reason": reason,
            "symbol": _extract_alert_ticker(row),
            "timeframe": timeframe,
            "execution_score": int(round(max(0, min(score, 100)))),
            "execution_threshold": threshold if threshold < 999 else None,
            "execution_model": "adaptive_execution_v1",
            "matched": matched,
            "last_close": round(last_close, 10),
            "last_candle_timestamp": int(last_timestamp) if last_timestamp is not None else None,
            "dropped_open_candle": dropped_open_bar,
            "vwap": round(vwap, 10),
            "volume_ratio": round(vol_ratio, 2),
            "close_pos": round(close_pos, 2),
            "candle_change_pct": round(candle_change_pct, 2),
            "range_pct": round(range_pct, 2),
            "distance_to_entry_r": round(distance_r, 2) if distance_r is not None else None,
            "pre_breakout_ok": pre_breakout_ok,
            "pre_breakout_score": pre_score,
            "pre_breakout_reasons": pre_reasons,
            "pre_breakout_reason": "5m_coil_near_breakout" if pre_breakout_ok else "pre_breakout_not_ready",
            "recent_range_pct": round(recent_range_pct, 2),
            "recent_change_pct": round(recent_change_pct, 2),
            "consecutive_green_5m": consecutive_green,
            "prior_range_pct": round(prior_range_pct, 2),
            "near_range_high_pct": round(near_range_high_pct, 2),
            "vwap_hold": vwap_hold,
            "higher_lows": higher_lows,
            "volume_dryup": volume_dryup,
        }
    except Exception as exc:
        return {"ok": False, "reason": "trigger_parse_failed", "detail": str(exc)[:120], "timeframe": timeframe, "execution_score": 0}


def _early_mover_htf_armed_context(row: Dict[str, Any], bars: List[Dict[str, Any]], timeframe: str = "4h") -> Dict[str, Any]:
    """Gate EXPLOSION_ARMED with higher-timeframe context.

    A 5m coil after a long 4h rebound is not "shortly before breakout"; it is
    often the place where late longs provide exit liquidity. This check only
    affects pre-breakout/armed state, not confirmed 5m trade triggers.
    """
    timeframe = str(timeframe or "4h").lower()
    clean = []
    for bar in _completed_candles_only(bars or [], timeframe):
        try:
            open_ = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            if open_ > 0 and close > 0 and high > low:
                clean.append({"open": open_, "high": high, "low": low, "close": close})
        except Exception:
            continue

    if len(clean) < 10:
        return {"armed_ok": False, "reason": "htf_not_enough_bars", "timeframe": timeframe, "bar_count": len(clean)}

    recent = clean[-8:]
    prior = clean[-32:-8] if len(clean) >= 40 else clean[:-8]
    last_close = recent[-1]["close"]
    recent_open = recent[0]["open"]
    recent_high = max(bar["high"] for bar in recent)
    recent_low = min(bar["low"] for bar in recent)
    recent_change_pct = ((last_close - recent_open) / recent_open * 100) if recent_open else 0
    recent_range_pct = ((recent_high - recent_low) / last_close * 100) if last_close else 999
    near_recent_high_pct = ((recent_high - last_close) / last_close * 100) if last_close else 999
    rebound_from_recent_low_pct = ((last_close - recent_low) / recent_low * 100) if recent_low else 0
    last_bar = recent[-1]
    last_bar_close_pos = (last_bar["close"] - last_bar["low"]) / max(last_bar["high"] - last_bar["low"], 1e-9)
    last_bar_red = last_bar["close"] < last_bar["open"]
    green_count = sum(1 for bar in recent if bar["close"] > bar["open"])
    consecutive_green = 0
    for bar in reversed(recent):
        if bar["close"] > bar["open"]:
            consecutive_green += 1
        else:
            break

    prior_range_pct = 0.0
    if prior:
        prior_high = max(bar["high"] for bar in prior)
        prior_low = min(bar["low"] for bar in prior)
        prior_mid = _median_float([bar["close"] for bar in prior], last_close)
        prior_range_pct = ((prior_high - prior_low) / max(prior_mid, 1e-9) * 100) if prior_mid else 0

    reasons = []
    if recent_change_pct >= 6.0:
        reasons.append("htf_move_already_extended")
    if rebound_from_recent_low_pct >= 8.0:
        reasons.append("htf_rebound_from_lows_already_extended")
    if consecutive_green >= 4 or (green_count >= 6 and recent_change_pct >= 4.0):
        reasons.append("htf_green_run_extended")
    if last_bar_red and last_bar_close_pos <= 0.45:
        reasons.append("htf_current_bar_rejecting")
    if near_recent_high_pct > 1.2:
        reasons.append("not_near_4h_breakout_level")
    if recent_range_pct > 7.0 and recent_change_pct > 3.0:
        reasons.append("htf_not_compressed")
    if prior_range_pct and recent_range_pct > max(6.0, prior_range_pct * 0.95) and recent_change_pct > 2.5:
        reasons.append("htf_range_not_tightening")

    return {
        "armed_ok": not reasons,
        "reason": "htf_context_ok" if not reasons else reasons[0],
        "reasons": reasons,
        "timeframe": timeframe,
        "recent_change_pct": round(recent_change_pct, 2),
        "recent_range_pct": round(recent_range_pct, 2),
        "near_recent_high_pct": round(near_recent_high_pct, 2),
        "rebound_from_recent_low_pct": round(rebound_from_recent_low_pct, 2),
        "last_bar_close_pos": round(last_bar_close_pos, 2),
        "green_count_8": green_count,
        "consecutive_green": consecutive_green,
        "prior_range_pct": round(prior_range_pct, 2),
    }


def _early_mover_htf_execution_context(row: Dict[str, Any], bars: List[Dict[str, Any]], timeframe: str = "4h") -> Dict[str, Any]:
    """Block live long entries when the higher timeframe is rejecting a spike.

    A 5m retest can briefly look valid while the 4h candle is actively selling
    off from a liquidity sweep. In that situation the correct action is wait,
    not JETZT_TRADEN.
    """
    timeframe = str(timeframe or "4h").lower()
    clean = []
    for bar in bars or []:
        try:
            open_ = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            if open_ > 0 and close > 0 and high > low:
                clean.append({"open": open_, "high": high, "low": low, "close": close})
        except Exception:
            continue

    if len(clean) < 8:
        return {
            "ok": False,
            "reason": "htf_execution_context_missing",
            "timeframe": timeframe,
            "bar_count": len(clean),
        }

    recent = clean[-8:]
    last = recent[-1]
    prev = recent[-2]
    recent_high = max(bar["high"] for bar in recent)
    recent_low = min(bar["low"] for bar in recent)
    last_close = last["close"]
    last_open = last["open"]
    last_high = last["high"]
    last_low = last["low"]
    last_close_pos = (last_close - last_low) / max(last_high - last_low, 1e-9)
    last_change_pct = ((last_close - last_open) / last_open * 100) if last_open else 0
    pullback_from_high_pct = ((recent_high - last_close) / last_close * 100) if last_close else 999
    bounce_from_low_pct = ((last_close - recent_low) / recent_low * 100) if recent_low else 0
    prev_red = prev["close"] < prev["open"]
    last_red = last_close < last_open
    lower_high = last_high < prev["high"] * 0.998
    lower_close = last_close < prev["close"] * 0.998

    reasons = []
    if last_red and last_close_pos <= 0.35 and pullback_from_high_pct >= 1.5:
        reasons.append("htf_current_bar_rejecting")
    if pullback_from_high_pct >= 3.0 and last_red and lower_close:
        reasons.append("htf_pullback_after_spike")
    if prev_red and last_red and pullback_from_high_pct >= 2.0:
        reasons.append("htf_two_red_after_spike")
    if pullback_from_high_pct >= 4.5 and lower_high and lower_close:
        reasons.append("htf_lower_high_after_sweep")
    if last_change_pct <= -2.2 and last_close_pos <= 0.45:
        reasons.append("htf_active_red_candle")

    return {
        "ok": not reasons,
        "reason": "htf_execution_context_ok" if not reasons else reasons[0],
        "reasons": reasons,
        "timeframe": timeframe,
        "pullback_from_high_pct": round(pullback_from_high_pct, 2),
        "bounce_from_low_pct": round(bounce_from_low_pct, 2),
        "last_bar_change_pct": round(last_change_pct, 2),
        "last_bar_close_pos": round(last_close_pos, 2),
        "last_bar_red": last_red,
        "lower_high": lower_high,
        "lower_close": lower_close,
    }


def _verify_early_mover_intraday_trigger(row: Dict[str, Any]) -> Dict[str, Any]:
    """Confirm Early-Mover mail candidates with adaptive exchange execution candles."""
    symbol = _extract_alert_ticker(row)
    contract = row.get("PerpChartSymbol") or row.get("PerpMatchSymbol")
    exchange = _normalize_crypto_exchange(row.get("PerpChartExchange") or row.get("BestExchange"))
    if not HAS_NEW_LISTING_SCANNER:
        return {"ok": False, "reason": "exchange_chart_module_missing"}
    if not contract or not exchange:
        return {"ok": False, "reason": "no_perp_chart_for_realtime_trigger"}

    profile = _early_mover_trigger_profile(row)
    cache_key = f"{exchange}:{contract}:adaptive_5m_v2"
    now = time.time()
    cached = _EARLY_MOVER_TRIGGER_CACHE.get(cache_key)
    if cached and now - cached.get("ts", 0) < _EARLY_MOVER_TRIGGER_TTL:
        return dict(cached["result"])

    checks: List[Tuple[str, int]] = [("5m", 36)]

    results = []
    for timeframe, count in checks:
        try:
            bars = fetch_candles_for(str(contract), exchange, timeframe=timeframe, count=count)
            completed_bars = _completed_candles_only(bars or [], timeframe)
            scored = _score_early_mover_trigger_bars(row, bars, timeframe, profile)
            vrvp = _early_mover_vrvp_from_bars(completed_bars)
            if vrvp:
                scored["vrvp"] = vrvp
            if scored.get("ok"):
                try:
                    htf_bars = fetch_candles_for(str(contract), exchange, timeframe="4h", count=48)
                    execution_context = _early_mover_htf_execution_context(row, htf_bars, "4h")
                except Exception as htf_exc:
                    execution_context = {
                        "ok": False,
                        "reason": "htf_execution_context_fetch_failed",
                        "detail": str(htf_exc)[:120],
                        "timeframe": "4h",
                    }
                scored["htf_execution_context"] = execution_context
                if not execution_context.get("ok"):
                    scored["ok"] = False
                    scored["reason"] = execution_context.get("reason") or "htf_execution_rejecting"
                    scored["htf_rejection_reasons"] = execution_context.get("reasons", [])
            if scored.get("pre_breakout_ok"):
                try:
                    htf_bars = fetch_candles_for(str(contract), exchange, timeframe="4h", count=48)
                    htf_context = _early_mover_htf_armed_context(row, htf_bars, "4h")
                except Exception as htf_exc:
                    htf_context = {
                        "armed_ok": False,
                        "reason": "htf_context_fetch_failed",
                        "detail": str(htf_exc)[:120],
                        "timeframe": "4h",
                    }
                scored["htf_context"] = htf_context
                if not htf_context.get("armed_ok"):
                    scored["pre_breakout_ok"] = False
                    scored["pre_breakout_reason"] = htf_context.get("reason") or "htf_context_not_ready"
                    reasons = list(scored.get("pre_breakout_reasons") or [])
                    reasons.extend(htf_context.get("reasons") or [htf_context.get("reason")])
                    scored["pre_breakout_reasons"] = list(dict.fromkeys([r for r in reasons if r]))
        except Exception as exc:
            scored = {"ok": False, "reason": "trigger_fetch_failed", "detail": str(exc)[:120], "timeframe": timeframe, "execution_score": 0}
        scored.update({"symbol": symbol, "contract": contract, "exchange": exchange})
        results.append(scored)
        if scored.get("ok"):
            break

    result = max(results, key=lambda item: item.get("execution_score", 0)) if results else {"ok": False, "reason": "no_trigger_checks", "execution_score": 0}
    result["adaptive_checks"] = [
        {
            "timeframe": item.get("timeframe"),
            "ok": bool(item.get("ok")),
            "score": item.get("execution_score", 0),
            "reason": item.get("reason"),
        }
        for item in results
    ]
    if result.get("ok"):
        liquidity = _early_mover_orderbook_liquidity(str(contract), exchange)
        result["liquidity"] = liquidity
        if not liquidity.get("ok"):
            result["ok"] = False
            result["reason"] = "thin_orderbook_market_impact"
            result["liquidity_reasons"] = liquidity.get("reasons", [])

    _EARLY_MOVER_TRIGGER_CACHE[cache_key] = {"ts": now, "result": result}
    if len(_EARLY_MOVER_TRIGGER_CACHE) > _EARLY_MOVER_TRIGGER_CACHE_MAX:
        overflow = len(_EARLY_MOVER_TRIGGER_CACHE) - _EARLY_MOVER_TRIGGER_CACHE_MAX
        oldest = sorted(_EARLY_MOVER_TRIGGER_CACHE.items(), key=lambda item: item[1].get("ts", 0))[:max(overflow, 50)]
        for key, _ in oldest:
            _EARLY_MOVER_TRIGGER_CACHE.pop(key, None)
    return dict(result)


def _early_mover_mail_trigger_block_reason(trigger_check: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(trigger_check, dict) or not trigger_check.get("ok"):
        reason = trigger_check.get("reason", "no_intraday_trigger") if isinstance(trigger_check, dict) else "no_intraday_trigger"
        return f"early_mover_{reason}"
    timeframe = str(trigger_check.get("timeframe") or "").lower()
    if timeframe == "1m":
        return "early_mover_1m_trigger_disabled"
    return None


def _early_mover_explosion_score(row: Dict[str, Any], trigger_check: Optional[Dict[str, Any]] = None) -> int:
    """Score the pre-breakout coil itself, independent from broad daily setup score."""
    check = trigger_check if isinstance(trigger_check, dict) else row.get("intraday_trigger")
    check = check if isinstance(check, dict) else {}
    fields = _extract_early_mover_fields(row)
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
    flags = set(fields.get("risk_flags") or [])

    setup_score = _alert_float(row.get("setup_score", row.get("score")), 0) or 0
    pre_score = _alert_float(check.get("pre_breakout_score", row.get("pre_breakout_score")), 0) or 0
    live_rr = fields["live_rr"]
    distance_r = fields["distance_to_entry_r"]
    risk_level = str(row.get("risk_level", "") or "").upper()
    target_quality = str(row.get("target_quality", setup.get("target_quality", "")) or "").upper()
    alpha_24h = fields["alpha_24h"]

    score = pre_score * 0.58 + min(setup_score, 100) * 0.18
    if live_rr >= 2.5:
        score += 10
    elif live_rr >= 1.8:
        score += 7
    elif live_rr >= _EARLY_MOVER_VISIBLE_MIN_LIVE_RR:
        score += 4
    else:
        score -= 12

    if distance_r <= 0.25:
        score += 8
    elif distance_r <= _EARLY_MOVER_MAX_ARMED_DISTANCE_R:
        score += 5
    elif distance_r <= _EARLY_MOVER_VISIBLE_MAX_DISTANCE_R:
        score += 1
    else:
        score -= 18

    btc_ok = _early_mover_btc_allows_long(fields)
    if fields["btc_tailwind"]:
        score += 5
    elif btc_ok:
        score += 2
    else:
        score -= 16
    if alpha_24h >= 2:
        score += 4
    elif alpha_24h < -2:
        score -= 5

    if target_quality == "STRUCTURAL":
        score += 5
    elif target_quality.startswith("WEAK"):
        score -= 6

    if risk_level == "MEDIUM":
        score -= 5
    elif risk_level == "HIGH":
        score -= 30

    penalty_map = {
        "perp_liquidity_watch": 5,
        "weak_structural_targets": 4,
        "high_volume_turnover": 5,
        "extreme_volume_turnover": 9,
        "very_high_volume_turnover": 13,
        "turnover_without_alpha": 20,
        "extreme_turnover_churn": 20,
        "chased_from_entry": 18,
        "btc_headwind": 3 if btc_ok else 18,
        "btc_caution": 3,
        "partial_crypto_data": 20,
        "data_warning": 20,
        "thin_perp_liquidity": 30,
        "thin_orderbook": 30,
        "market_impact_risk": 30,
        "no_perp_execution_market": 35,
        "overheated_phase3": 35,
    }
    for flag, penalty in penalty_map.items():
        if flag in flags:
            score -= penalty

    return int(round(max(0, min(100, score))))


def _early_mover_explosion_armed_state(row: Dict[str, Any], trigger_check: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
    """True when the coin is a high-conviction pre-breakout setup, not a generic watch row."""
    check = trigger_check if isinstance(trigger_check, dict) else row.get("intraday_trigger")
    check = check if isinstance(check, dict) else {}
    fields = _extract_early_mover_fields(row)
    flags = set(fields.get("risk_flags") or [])
    action = fields["trade_action"]
    setup_score = int(_alert_float(row.get("setup_score", row.get("score")), 0) or 0)
    pre_score = int(_alert_float(check.get("pre_breakout_score", row.get("pre_breakout_score")), 0) or 0)
    pre_ok = bool(check.get("pre_breakout_ok", row.get("pre_breakout_armed_candidate", False)))
    explosion_score = _early_mover_explosion_score(row, check)
    reasons: List[str] = []

    if action not in ("LONG_TRIGGER", "WAIT_FOR_RETEST"):
        reasons.append("action_not_armed")
    if bool(check.get("ok")):
        reasons.append("already_trade_triggered")
    if setup_score < _EARLY_MOVER_MIN_ARMED_SETUP_SCORE:
        reasons.append("setup_score_below_armed_threshold")
    if pre_score < _EARLY_MOVER_MIN_ARMED_PREBREAKOUT_SCORE:
        reasons.append("pre_breakout_score_below_threshold")
    if not pre_ok:
        reasons.append("not_pre_breakout_coil")
    htf_context = check.get("htf_context") if isinstance(check.get("htf_context"), dict) else row.get("htf_context")
    if not isinstance(htf_context, dict):
        reasons.append("htf_context_missing")
    elif not htf_context.get("armed_ok", False):
        reasons.append(htf_context.get("reason") or "htf_context_not_ready")
    if explosion_score < _EARLY_MOVER_VISIBLE_MIN_SETUP_SCORE:
        reasons.append("explosion_score_below_threshold")
    if str(row.get("risk_level", "") or "").upper() == "HIGH":
        reasons.append("risk_high")
    if fields["live_rr"] < _EARLY_MOVER_VISIBLE_MIN_LIVE_RR:
        reasons.append("live_rr_below_armed_threshold")
    if fields["distance_to_entry_r"] > _EARLY_MOVER_VISIBLE_MAX_DISTANCE_R:
        reasons.append("too_far_from_entry_for_armed")
    btc_ok = _early_mover_btc_allows_long(fields)
    if not btc_ok:
        reasons.append("btc_not_tailwind")
    if fields["partial_data"] or fields["data_warning"]:
        reasons.append("data_not_clean")
    if fields["late_to_tp1"]:
        reasons.append("tp1_already_reached")

    hard_flags = {
        "overheated_phase3",
        "data_warning",
        "chased_from_entry",
        "very_high_volume_turnover",
        "turnover_without_alpha",
        "extreme_turnover_churn",
        "thin_perp_liquidity",
        "thin_orderbook",
        "market_impact_risk",
        "no_perp_execution_market",
        "weak_structural_targets",
        "duplicate_targets",
        "targets_too_close",
        "invalid_target_plan",
    }
    if not btc_ok:
        hard_flags.add("btc_headwind")
    for flag in sorted(flags.intersection(hard_flags)):
        reasons.append(flag)

    return not reasons, reasons


def _early_mover_armed_orderbook_block_reason(row: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Armed alerts still need a tradable perp book; otherwise the mail creates FOMO in illiquid names."""
    contract = row.get("PerpChartSymbol") or row.get("PerpMatchSymbol")
    exchange = _normalize_crypto_exchange(row.get("PerpChartExchange") or row.get("BestExchange"))
    if not contract or not exchange:
        return "no_perp_execution_market", {}

    liquidity = _early_mover_orderbook_liquidity(str(contract), exchange)
    row["armed_orderbook_liquidity"] = liquidity
    if liquidity.get("ok"):
        return None, liquidity

    reasons = liquidity.get("reasons") if isinstance(liquidity.get("reasons"), list) else []
    reason = reasons[0] if reasons else liquidity.get("reason", "orderbook_not_ok")
    return f"armed_orderbook_{reason}", liquidity


def _early_mover_entry_score(row: Dict[str, Any]) -> int:
    """Score whether this Early-Mover is actionable now, separate from setup quality."""
    fields = _extract_early_mover_fields(row)
    setup_score = int(_alert_float(row.get("setup_score", row.get("score")), 0) or 0)
    action = str(row.get("trade_action", row.get("entry_status", "")) or "").upper()
    signal = str(row.get("trade_signal", "") or "").upper()
    entry_status = str(row.get("entry_status", "") or "").upper()
    risk_level = str(row.get("risk_level", "") or "").upper()
    flags = {str(flag).lower() for flag in (row.get("risk_flags") or [])}
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}

    score = setup_score
    if signal == "JETZT_TRADEN":
        score += 8
    elif row.get("pre_breakout_armed"):
        pre_score = _alert_float(row.get("pre_breakout_score"), 0) or 0
        score = max(score - 12, int(setup_score * 0.72 + pre_score * 0.28))
    elif action == "LONG_TRIGGER" or entry_status == "WAIT_FOR_TRIGGER":
        score -= 38
    elif action == "WAIT_FOR_RETEST" or entry_status == "WAIT_FOR_RETEST":
        score -= 30
    elif action == "WAIT_FOR_CONTINUATION":
        score -= 42
    elif action == "WAIT_FOR_LIQUIDITY":
        score -= 50
    elif action == "WAIT_FOR_BTC_CONFIRMATION":
        score -= 45
    elif action in ("NO_LONG_CHASE", "NO_TRADE") or signal == "NICHT_TRADEN":
        score = min(score, 25)
    elif signal in ("WARTEN", "BEOBACHTEN"):
        score -= 22

    if risk_level == "HIGH":
        score -= 30
    elif risk_level == "MEDIUM":
        score -= 10

    flag_penalties = {
        "thin_orderbook": 35,
        "market_impact_risk": 35,
        "thin_perp_liquidity": 30,
        "perp_liquidity_watch": 8,
        "no_perp_execution_market": 28,
        "turnover_without_alpha": 18,
        "extreme_turnover_churn": 20,
        "extreme_volume_turnover": 14,
        "very_high_volume_turnover": 14,
        "high_volume_turnover": 8,
        "btc_headwind": 18,
        "partial_crypto_data": 18,
        "data_warning": 15,
        "chased_from_entry": 16,
        "overheated_phase3": 28,
        "oi_snapshot_only": 6,
        "weak_structural_targets": 26,
    }
    btc_ok = _early_mover_btc_allows_long(fields)
    for flag in flags:
        if flag == "btc_headwind" and btc_ok:
            score -= 3
            continue
        score -= flag_penalties.get(flag, 0)
    if "vrvp_target_confirmed" in flags:
        score += 5

    live_rr = _alert_float(row.get("live_rr_ratio", setup.get("live_rr")), 0) or 0
    if live_rr and live_rr < 1.5:
        score -= 16
    elif signal == "JETZT_TRADEN" and live_rr >= 2.0:
        score += 4

    distance_r = _alert_float(row.get("distance_to_entry_r", setup.get("distance_to_entry_r")), 0) or 0
    if distance_r >= 0.75:
        score -= 16
    elif distance_r >= 0.35:
        score -= 8

    execution_score = _alert_float(row.get("execution_quality_score"), None)
    if signal == "JETZT_TRADEN" and execution_score is not None:
        score = max(score, int(setup_score * 0.55 + execution_score * 0.45))

    return max(0, min(100, int(round(score))))


def _early_mover_entry_score_label(row: Dict[str, Any]) -> str:
    score = int(_alert_float(row.get("entry_score"), 0) or 0)
    signal = str(row.get("trade_signal", "") or "").upper()
    action = str(row.get("trade_action", "") or "").upper()
    if signal == "JETZT_TRADEN":
        return "ENTRY OK"
    if action == "WAIT_FOR_RETEST":
        return "RETEST WARTEN"
    if action == "LONG_TRIGGER":
        return "5M WARTEN"
    if score >= 75:
        return "FAST BEREIT"
    if score >= 50:
        return "WATCH"
    return "NICHT BEREIT"


def _early_mover_trade_score(row: Dict[str, Any]) -> int:
    """Score a *trade now* candidate without letting one strong pillar hide a weak one.

    Setup quality, entry/timing and the fresh 5m execution trigger are different
    dimensions. A max() score is dangerous here: a 90 setup with a mediocre entry
    can look like an S trade. This score uses a weighted blend capped by the
    weakest pillar, so mails need setup, timing and execution to agree.
    """
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
    setup_score = int(_alert_float(row.get("setup_score", row.get("score")), 0) or 0)
    entry_value = _alert_float(row.get("entry_score"), None)
    entry_score = int(entry_value if entry_value is not None else _early_mover_entry_score(row))
    execution_value = _alert_float(row.get("execution_quality_score"), None)
    execution_score = int(execution_value if execution_value is not None else (80 if _alert_bool(row.get("execution_trigger_ok")) else 0))
    signal = str(row.get("trade_signal", "") or "").upper()

    if signal != "JETZT_TRADEN":
        return max(0, min(100, min(setup_score, entry_score)))

    pillars = [max(0, setup_score), max(0, entry_score), max(0, execution_score)]
    weighted = pillars[0] * 0.38 + pillars[1] * 0.34 + pillars[2] * 0.28
    weakest_cap = min(pillars) + 14
    score = min(weighted, weakest_cap)

    fields = _extract_early_mover_fields(row)
    risk_level = str(row.get("risk_level", "") or "").upper()
    flags = {str(flag).lower() for flag in (row.get("risk_flags") or setup.get("risk_flags") or [])}

    if fields["live_rr"] < _EARLY_MOVER_MIN_ALERT_RR:
        score = min(score, 45)
    if fields["distance_to_entry_r"] > _EARLY_MOVER_VISIBLE_MAX_DISTANCE_R:
        score = min(score, 55)
    if risk_level == "MEDIUM":
        score = min(score, 82)
    elif risk_level == "HIGH":
        score = min(score, 35)

    hard_flags = {
        "overheated_phase3",
        "partial_crypto_data",
        "data_warning",
        "tp1_already_reached",
        "chased_from_entry",
        "very_high_volume_turnover",
        "turnover_without_alpha",
        "extreme_turnover_churn",
        "thin_perp_liquidity",
        "thin_orderbook",
        "market_impact_risk",
        "no_perp_execution_market",
        "weak_structural_targets",
        "duplicate_targets",
        "targets_too_close",
        "invalid_target_plan",
    }
    if flags.intersection(hard_flags):
        score = min(score, 35)

    return int(round(max(0, min(100, score))))


def _early_mover_vrvp_from_bars(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build compact VRVP levels from exchange candles for target selection."""
    if not bars or len(bars) < 20:
        return None
    ohlcv = []
    for bar in bars:
        high = _alert_float(bar.get("high"))
        low = _alert_float(bar.get("low"))
        close = _alert_float(bar.get("close"))
        open_ = _alert_float(bar.get("open"), close)
        volume = _alert_float(bar.get("volume"), 0) or 0
        if high is None or low is None or close is None or high <= 0 or low <= 0 or high < low:
            continue
        ohlcv.append({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
    if len(ohlcv) < 20:
        return None

    profile = calculate_volume_profile(ohlcv, num_bins=18)
    if not profile:
        return None

    levels = []

    def _add_level(price: Any, source: str, weight: int) -> None:
        number = _alert_float(price)
        if number is None or number <= 0:
            return
        levels.append({
            "price": _round_crypto_price(number),
            "source": source,
            "weight": weight,
        })

    _add_level(profile.get("vah"), "vrvp_vah_resistance", 90)
    _add_level(profile.get("poc"), "vrvp_poc_acceptance", 85)
    _add_level(profile.get("val"), "vrvp_val_support", 75)
    for hvn in profile.get("hvns") or []:
        _add_level(hvn.get("mid"), "vrvp_hvn_resistance", 80)
    for lvn in profile.get("lvns") or []:
        _add_level(lvn.get("high"), "vrvp_lvn_upper_edge", 65)

    unique = {}
    for level in levels:
        key = round(float(level["price"]), 10)
        if key not in unique or level["weight"] > unique[key]["weight"]:
            unique[key] = level

    return {
        "poc": _round_crypto_price(profile.get("poc")),
        "vah": _round_crypto_price(profile.get("vah")),
        "val": _round_crypto_price(profile.get("val")),
        "levels": sorted(unique.values(), key=lambda item: item["price"]),
        "source": "exchange_vrvp_5m",
    }


def _apply_early_mover_vrvp_targets(row: Dict[str, Any], vrvp: Optional[Dict[str, Any]]) -> None:
    """Use VRVP resistance/acceptance levels as TP candidates when they are valid."""
    if not isinstance(vrvp, dict):
        return
    entry = _alert_float(row.get("entry"))
    stop = _alert_float(row.get("stop_loss", row.get("stop")))
    if entry is None or stop is None or entry <= 0 or stop <= 0 or stop >= entry:
        return

    risk = max(entry - stop, entry * 0.01)
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
    target_req = setup.get("target_min_pct_required") if isinstance(setup.get("target_min_pct_required"), dict) else {}
    min_tp1_pct = (_alert_float(target_req.get("tp1"), 5.5) or 5.5) / 100
    min_tp2_pct = (_alert_float(target_req.get("tp2"), 9.5) or 9.5) / 100
    min_tp1 = max(entry + risk * 1.35, entry * (1 + min_tp1_pct))
    min_tp2 = max(entry + risk * 2.6, entry * (1 + min_tp2_pct))

    levels = [
        level for level in (vrvp.get("levels") or [])
        if _alert_float(level.get("price")) is not None and _alert_float(level.get("price")) > entry
    ]
    if not levels:
        return

    valid_tp1 = [level for level in levels if (_alert_float(level.get("price")) or 0) >= min_tp1]
    if not valid_tp1:
        return
    tp1_level = valid_tp1[0]
    tp1 = _alert_float(tp1_level.get("price"))

    valid_tp2 = [
        level for level in levels
        if (_alert_float(level.get("price")) or 0) >= max(min_tp2, (tp1 or 0) + risk * 0.25)
    ]
    tp2_level = valid_tp2[0] if valid_tp2 else None
    current_tp2 = _alert_float(row.get("tp2"), 0) or 0
    tp2 = _alert_float(tp2_level.get("price")) if tp2_level else current_tp2
    if tp1 is None or tp2 is None or tp2 <= tp1:
        return

    min_tp2_gap = max(entry * 0.018, risk * 0.45)
    if (tp2 - tp1) < min_tp2_gap:
        flags = row.get("risk_flags") if isinstance(row.get("risk_flags"), list) else []
        row["risk_flags"] = list(dict.fromkeys([*flags, "targets_too_close", "weak_structural_targets"]))
        row["target_quality"] = "WEAK_STRUCTURAL_TARGETS"
        if setup:
            setup_flags = setup.get("risk_flags") if isinstance(setup.get("risk_flags"), list) else []
            setup["risk_flags"] = list(dict.fromkeys([*setup_flags, "targets_too_close", "weak_structural_targets"]))
            setup["target_quality"] = "WEAK_STRUCTURAL_TARGETS"
            setup_warnings = setup.get("warnings") if isinstance(setup.get("warnings"), list) else []
            setup["warnings"] = list(dict.fromkeys([*setup_warnings, "target_plan_invalid"]))
            row["trade_setup"] = setup
        return

    current_tp1 = _alert_float(row.get("tp1"), 0) or 0
    current_quality = str(row.get("target_quality") or setup.get("target_quality") or "").upper()
    should_replace_tp1 = current_quality.startswith("WEAK") or current_tp1 <= 0 or tp1 < current_tp1 or str(row.get("tp1_source", "")).endswith("_too_close")
    should_replace_tp2 = current_quality.startswith("WEAK") or current_tp2 <= 0 or tp2 < current_tp2 or str(row.get("tp2_source", "")).endswith("_too_close")
    if not should_replace_tp1 and not should_replace_tp2:
        return

    if should_replace_tp1:
        row["tp1"] = _round_crypto_price(tp1)
        row["tp1_source"] = tp1_level.get("source") or "vrvp_resistance"
    if should_replace_tp2:
        row["tp2"] = _round_crypto_price(tp2)
        row["tp2_source"] = (tp2_level or {}).get("source") or "vrvp_resistance"

    new_tp1 = _alert_float(row.get("tp1"), tp1) or tp1
    new_tp2 = _alert_float(row.get("tp2"), tp2) or tp2
    rr_tp1 = round((new_tp1 - entry) / risk, 2)
    rr_tp2 = round((new_tp2 - entry) / risk, 2)
    live_entry = max(_alert_float(row.get("Price"), entry) or entry, entry)
    live_risk = max(live_entry - stop, risk)
    live_reward = ((new_tp1 - live_entry) + (new_tp2 - live_entry)) / 2
    row.update({
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "risk_reward": round((rr_tp1 + rr_tp2) / 2, 2),
        "live_rr_ratio": round(max(0.0, live_reward) / live_risk, 2) if live_risk > 0 else 0,
        "target_quality": "STRUCTURAL_VRVP",
        "vrvp_levels": vrvp,
    })
    flags = [str(flag) for flag in (row.get("risk_flags") or []) if str(flag) != "weak_structural_targets"]
    row["risk_flags"] = list(dict.fromkeys(flags + ["vrvp_target_confirmed"]))

    if setup:
        setup.update({
            "tp1": row.get("tp1"),
            "tp2": row.get("tp2"),
            "tp1_source": row.get("tp1_source"),
            "tp2_source": row.get("tp2_source"),
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "rr": row.get("risk_reward"),
            "live_rr": row.get("live_rr_ratio"),
            "target_quality": row.get("target_quality"),
            "vrvp_levels": vrvp,
        })
        row["trade_setup"] = setup


def _clear_early_mover_wait_flags_for_trade_now(row: Dict[str, Any]) -> None:
    """Remove stale watch/trigger-needed flags after a fresh 5m trigger confirms.

    The same row starts as a watch candidate and later gets promoted to
    JETZT_TRADEN. Without this cleanup the UI can show both states at once.
    """
    if str(row.get("trade_signal", "") or "").upper() != "JETZT_TRADEN":
        return
    if not bool(row.get("execution_trigger_ok")):
        return

    def _clean_list(values: Any) -> List[Any]:
        if not isinstance(values, list):
            return []
        cleaned = []
        for value in values:
            normalized = str(value or "").strip().lower()
            if normalized in _EARLY_MOVER_WAIT_ONLY_FLAGS:
                continue
            cleaned.append(value)
        return list(dict.fromkeys(cleaned))

    row["risk_flags"] = _clean_list(row.get("risk_flags"))
    row["risk_reasons"] = _clean_list(row.get("risk_reasons"))
    setup = row.get("trade_setup")
    if isinstance(setup, dict):
        setup["risk_flags"] = _clean_list(setup.get("risk_flags"))
        setup["warnings"] = _clean_list(setup.get("warnings"))
        row["trade_setup"] = setup


def _early_mover_target_plan_issues(row: Dict[str, Any]) -> List[str]:
    """Return hard target-plan issues that make a crypto long non-actionable."""
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
    entry = _alert_float(row.get("entry", setup.get("entry")))
    stop = _alert_float(row.get("stop_loss", row.get("stop", setup.get("stop_loss", setup.get("stop")))))
    tp1 = _alert_float(row.get("tp1", setup.get("tp1")))
    tp2 = _alert_float(row.get("tp2", setup.get("tp2")))
    target_quality = str(row.get("target_quality", setup.get("target_quality", "")) or "").upper()
    issues: List[str] = []

    if entry is None or stop is None or tp1 is None or tp2 is None or entry <= 0:
        return ["invalid_target_plan"]
    risk = max(entry - stop, entry * 0.01)
    if stop >= entry or tp1 <= entry:
        issues.append("invalid_target_plan")
    min_tp2_gap = max(entry * 0.018, risk * 0.45)
    if tp2 <= tp1:
        issues.append("duplicate_targets")
    elif (tp2 - tp1) < min_tp2_gap:
        issues.append("targets_too_close")
    rr1 = (tp1 - entry) / risk if risk > 0 else 0
    rr2 = (tp2 - entry) / risk if risk > 0 else 0
    if rr1 < 1.2 or rr2 < max(1.6, rr1 + 0.35):
        issues.append("targets_too_close")
    if target_quality.startswith("WEAK"):
        issues.append("weak_structural_targets")
    return list(dict.fromkeys(issues))


def _block_early_mover_trade_on_bad_targets(row: Dict[str, Any]) -> None:
    """Downgrade JETZT_TRADEN if targets are duplicate, too tight or weak."""
    issues = _early_mover_target_plan_issues(row)
    if not issues:
        return
    all_issues = list(dict.fromkeys([*issues, "weak_structural_targets"]))

    def _merge_flags(key: str) -> None:
        current = row.get(key) if isinstance(row.get(key), list) else []
        row[key] = list(dict.fromkeys([*current, *all_issues]))

    _merge_flags("risk_flags")
    _merge_flags("risk_reasons")
    row["target_quality"] = "WEAK_STRUCTURAL_TARGETS"
    setup = row.get("trade_setup")
    if isinstance(setup, dict):
        setup["target_quality"] = "WEAK_STRUCTURAL_TARGETS"
        setup_flags = setup.get("risk_flags") if isinstance(setup.get("risk_flags"), list) else []
        setup["risk_flags"] = list(dict.fromkeys([*setup_flags, *all_issues]))
        setup_warnings = setup.get("warnings") if isinstance(setup.get("warnings"), list) else []
        setup["warnings"] = list(dict.fromkeys([*setup_warnings, "target_plan_invalid"]))
        row["trade_setup"] = setup

    if str(row.get("trade_signal", "") or "").upper() in {"JETZT_TRADEN", "EXPLOSION_ARMED"}:
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: TP1/TP2 sind keine sauberen Struktur-Ziele"
        row["signal_quality"] = "wait_target_plan"
        row["entry_status"] = "WAIT_FOR_RETEST"
        row["trade_action"] = "WAIT_FOR_RETEST"
        row["alertable_crypto"] = False
        row["pre_breakout_armed"] = False


def _apply_early_mover_signal_state(row: Dict[str, Any], trigger_check: Optional[Dict[str, Any]] = None) -> None:
    """Expose a simple user-facing decision: observe, wait, no trade, or trade now."""
    action = str(row.get("trade_action", "") or "").upper()
    trigger_ok = bool(trigger_check.get("ok")) if isinstance(trigger_check, dict) else bool(row.get("execution_trigger_ok"))
    trigger_reason = str((trigger_check or {}).get("reason", "") or "")
    trigger_block_reason = _early_mover_mail_trigger_block_reason(trigger_check) if isinstance(trigger_check, dict) else None
    effective_trigger_ok = bool(trigger_ok and not trigger_block_reason)

    row["execution_trigger_ok"] = effective_trigger_ok
    if isinstance(trigger_check, dict):
        row["intraday_trigger"] = trigger_check
        if trigger_check.get("execution_score") is not None:
            row["execution_quality_score"] = trigger_check.get("execution_score")
        if trigger_check.get("timeframe"):
            row["execution_timeframe"] = trigger_check.get("timeframe")
        if trigger_check.get("pre_breakout_score") is not None:
            row["pre_breakout_score"] = trigger_check.get("pre_breakout_score")
            row["pre_breakout_reason"] = trigger_check.get("pre_breakout_reason")
            row["pre_breakout_reasons"] = trigger_check.get("pre_breakout_reasons", [])
            row["pre_breakout_armed_candidate"] = bool(trigger_check.get("pre_breakout_ok"))
            row["pre_breakout_metrics"] = {
                "recent_range_pct": trigger_check.get("recent_range_pct"),
                "recent_change_pct": trigger_check.get("recent_change_pct"),
                "prior_range_pct": trigger_check.get("prior_range_pct"),
                "near_range_high_pct": trigger_check.get("near_range_high_pct"),
                "vwap_hold": trigger_check.get("vwap_hold"),
                "higher_lows": trigger_check.get("higher_lows"),
                "volume_dryup": trigger_check.get("volume_dryup"),
                "volume_ratio": trigger_check.get("volume_ratio"),
                "consecutive_green_5m": trigger_check.get("consecutive_green_5m"),
            }
        if isinstance(trigger_check.get("htf_context"), dict):
            row["htf_context"] = trigger_check.get("htf_context")
        if isinstance(trigger_check.get("liquidity"), dict):
            row["execution_liquidity"] = trigger_check["liquidity"]
        if trigger_check.get("reason") == "thin_orderbook_market_impact":
            flags = row.get("risk_flags") if isinstance(row.get("risk_flags"), list) else []
            flags.extend(["thin_orderbook", "market_impact_risk"])
            row["risk_flags"] = list(dict.fromkeys(flags))
            reasons = row.get("risk_reasons") if isinstance(row.get("risk_reasons"), list) else []
            reasons.append("Orderbuch zu duenn - kleiner Trade kann Kerze bewegen")
            row["risk_reasons"] = list(dict.fromkeys(reasons))
            row["risk_level"] = "HIGH"
        if isinstance(trigger_check.get("vrvp"), dict):
            _apply_early_mover_vrvp_targets(row, trigger_check.get("vrvp"))

    armed_ok, armed_reasons = _early_mover_explosion_armed_state(row, trigger_check)
    explosion_score = _early_mover_explosion_score(row, trigger_check)
    explosion_grade, explosion_grade_label = _score_grade_for_value(explosion_score)
    row["explosion_score"] = explosion_score
    row["explosion_grade"] = explosion_grade
    row["explosion_grade_label"] = explosion_grade_label
    row["pre_breakout_armed"] = armed_ok
    row["pre_breakout_block_reasons"] = armed_reasons

    if action in ("LONG_TRIGGER", "WAIT_FOR_RETEST") and trigger_ok and not trigger_block_reason:
        row["trade_signal"] = "JETZT_TRADEN"
        score_txt = f" Score {trigger_check.get('execution_score')}/100" if isinstance(trigger_check, dict) and trigger_check.get("execution_score") is not None else ""
        tf_txt = str(trigger_check.get("timeframe") or "adaptive") if isinstance(trigger_check, dict) else "adaptive"
        row["signal_label"] = f"Jetzt traden: {tf_txt} Execution-Trigger bestaetigt ({trigger_reason or 'ok'}{score_txt})"
        row["signal_quality"] = "tradeable"
        row["entry_status"] = "JETZT_TRADEN"
        row["alertable_crypto"] = True
    elif action in ("LONG_TRIGGER", "WAIT_FOR_RETEST") and armed_ok:
        row["trade_signal"] = "EXPLOSION_ARMED"
        score_txt = f" Score {explosion_score}/100" if explosion_score is not None else ""
        row["signal_label"] = f"Breakout-Watch: 5m/4h-Coil bestaetigt, Entry erst bei Breakout/Reclaim{score_txt}"
        row["signal_quality"] = "pre_breakout_armed"
        row["entry_status"] = "PRE_BREAKOUT_ARMED"
        row["alertable_crypto"] = False
    elif action in ("LONG_TRIGGER", "WAIT_FOR_RETEST") and trigger_ok and trigger_block_reason:
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: 1m-Trigger ist deaktiviert; Trade-Mail/Trade braucht 5m-Bestaetigung"
        row["signal_quality"] = "wait_trigger"
        row["entry_status"] = "WAIT_FOR_5M_CONFIRMATION"
        row["alertable_crypto"] = False
    elif action == "NO_LONG_CHASE":
        row["trade_signal"] = "NICHT_TRADEN"
        row["signal_label"] = "Nicht traden: Bewegung ist zu weit gelaufen"
        row["signal_quality"] = "no_chase"
        row["entry_status"] = "NICHT_TRADEN"
        row["alertable_crypto"] = False
    elif action == "WAIT_FOR_BTC_CONFIRMATION":
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: BTC bestaetigt das Setup noch nicht"
        row["signal_quality"] = "wait"
        row["entry_status"] = "WARTEN"
        row["alertable_crypto"] = False
    elif action == "WAIT_FOR_LIQUIDITY":
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: Perp/Orderbuch-Liquiditaet ist zu duenn"
        row["signal_quality"] = "wait"
        row["entry_status"] = "WARTEN"
        row["alertable_crypto"] = False
    elif action == "WAIT_FOR_RETEST":
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: Retest nahe Entry fehlt"
        row["signal_quality"] = "wait_retest"
        row["entry_status"] = "WAIT_FOR_RETEST"
        row["alertable_crypto"] = False
    elif action == "WAIT_FOR_CONTINUATION":
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: neue Continuation-Flag fehlt"
        row["signal_quality"] = "wait_continuation"
        row["entry_status"] = "WAIT_FOR_CONTINUATION"
        row["alertable_crypto"] = False
    elif action == "LONG_TRIGGER":
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: Execution-Trigger fehlt noch"
        row["signal_quality"] = "wait_trigger"
        row["entry_status"] = "WAIT_FOR_TRIGGER"
        row["alertable_crypto"] = False
    else:
        row["trade_signal"] = "BEOBACHTEN"
        row["signal_label"] = "Achtung beobachten: noch kein Trade-Signal"
        row["signal_quality"] = "observe"
        row["entry_status"] = "BEOBACHTEN"
        row["alertable_crypto"] = False

    _clear_early_mover_wait_flags_for_trade_now(row)
    _block_early_mover_trade_on_bad_targets(row)
    row["entry_score"] = _early_mover_entry_score(row)
    if row.get("trade_signal") == "EXPLOSION_ARMED" and row["entry_score"] < _EARLY_MOVER_VISIBLE_MIN_ENTRY_SCORE:
        reasons = list(row.get("pre_breakout_block_reasons") or [])
        reasons.append("entry_score_below_armed_threshold")
        row["pre_breakout_armed"] = False
        row["pre_breakout_block_reasons"] = list(dict.fromkeys(reasons))
        row["trade_signal"] = "WARTEN"
        row["signal_label"] = "Warten: Breakout-Watch blockiert, Entry-Risk ist zu hoch"
        row["signal_quality"] = "wait_trigger"
        row["entry_status"] = "WAIT_FOR_TRIGGER"
        row["alertable_crypto"] = False
        row["entry_score"] = _early_mover_entry_score(row)
    row["entry_score_label"] = _early_mover_entry_score_label(row)


def _reminder_now() -> float:
    return time.time()


def _reminder_iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts or _reminder_now(), timezone.utc).isoformat()


def _load_trade_reminders() -> List[Dict[str, Any]]:
    try:
        if not os.path.exists(_TRADE_REMINDERS_FILE):
            return []
        with open(_TRADE_REMINDERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, list) else []
    except Exception as exc:
        print(f"[Reminder] load error: {exc}")
        return []


def _save_trade_reminders(reminders: List[Dict[str, Any]]) -> None:
    tmp_path = f"{_TRADE_REMINDERS_FILE}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(reminders, f, indent=2, default=_serialize_json)
        os.replace(tmp_path, _TRADE_REMINDERS_FILE)
    except Exception as exc:
        print(f"[Reminder] save error: {exc}")


def _find_early_mover_row(symbol: str) -> Optional[Dict[str, Any]]:
    rows, _ = load_cache_file(EARLY_MOVERS_CACHE, max_age_hours=24)
    wanted = str(symbol or "").upper().replace("USDT", "")
    for row in _flatten_early_mover_rows(rows):
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("Symbol", row.get("symbol", row.get("ticker", "")))).upper().replace("USDT", "")
        if row_symbol == wanted:
            return dict(row)
    return None


def _fetch_recent_stock_5m_bars(ticker: str, limit: int = 24) -> List[Dict[str, Any]]:
    if not ticker or not POLYGON_KEY:
        return []
    try:
        try:
            from zoneinfo import ZoneInfo
            today_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            today_et = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker.upper()}/range/5/minute/{today_et}/{today_et}"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "adjusted": "true", "sort": "asc", "limit": 500}, timeout=10)
        if resp.status_code != 200:
            return []
        bars = resp.json().get("results", []) or []
        cleaned = []
        for bar in bars:
            cleaned_bar = {
                "open": float(bar.get("o", 0) or 0),
                "high": float(bar.get("h", 0) or 0),
                "low": float(bar.get("l", 0) or 0),
                "close": float(bar.get("c", 0) or 0),
                "volume": float(bar.get("v", 0) or 0),
                "timestamp": bar.get("t"),
            }
            if cleaned_bar["open"] > 0 and cleaned_bar["close"] > 0 and cleaned_bar["high"] >= cleaned_bar["low"]:
                cleaned.append(cleaned_bar)
        return _completed_candles_only(cleaned, "5m")[-limit:]
    except Exception as exc:
        print(f"[Reminder] stock bars error {ticker}: {exc}")
        return []


def _evaluate_stock_reminder(reminder: Dict[str, Any]) -> Dict[str, Any]:
    row = reminder.get("row") if isinstance(reminder.get("row"), dict) else {}
    ticker = str(reminder.get("ticker", "")).upper()
    direction = str(row.get("direction", row.get("Signal_Direction", "LONG")) or "LONG").upper()
    entry = _alert_float(row.get("entry", row.get("Entry", row.get("entry_price"))))
    stop = _alert_float(row.get("stop_loss", row.get("stop", row.get("Stop Loss"))))
    if not ticker or entry is None:
        return {"triggered": False, "reason": "missing_stock_entry"}
    bars = _fetch_recent_stock_5m_bars(ticker)
    if len(bars) < 4:
        return {"triggered": False, "reason": "not_enough_stock_5m_bars"}
    last = bars[-1]
    prev = bars[-8:-1] if len(bars) >= 9 else bars[:-1]
    last_close = last["close"]
    last_open = last["open"]
    last_high = last["high"]
    last_low = last["low"]
    median_vol = _median_float([b.get("volume", 0) for b in prev], max(last.get("volume", 1), 1))
    vol_ratio = last.get("volume", 0) / max(median_vol, 1)
    close_pos = (last_close - last_low) / max(last_high - last_low, 1e-9)
    risk = abs(entry - stop) if stop is not None else max(entry * 0.02, 0.01)
    distance_r = abs(last_close - entry) / max(risk, 1e-9)

    if direction == "SHORT":
        trigger = last_close <= entry and last_close < last_open and close_pos <= 0.45 and vol_ratio >= 1.1
        near_retest = distance_r <= 0.25 and last_close <= entry * 1.003
    else:
        trigger = last_close >= entry and last_close > last_open and close_pos >= 0.55 and vol_ratio >= 1.1
        near_retest = distance_r <= 0.25 and last_close >= entry * 0.997

    if trigger or near_retest:
        return {
            "triggered": True,
            "reason": "5m_trigger" if trigger else "retest_near_entry",
            "last_close": round(last_close, 6),
            "distance_to_entry_r": round(distance_r, 2),
            "volume_ratio": round(vol_ratio, 2),
        }
    return {
        "triggered": False,
        "reason": "stock_trigger_not_ready",
        "last_close": round(last_close, 6),
        "distance_to_entry_r": round(distance_r, 2),
        "volume_ratio": round(vol_ratio, 2),
    }


def _evaluate_trade_reminder(reminder: Dict[str, Any]) -> Dict[str, Any]:
    asset_type = str(reminder.get("asset_type", "crypto") or "crypto").lower()
    ticker = str(reminder.get("ticker", "")).upper()
    if asset_type == "crypto":
        row = _find_early_mover_row(ticker)
        if not row:
            return {"triggered": False, "reason": "coin_not_in_latest_scan"}
        trigger_check = _verify_early_mover_intraday_trigger(row)
        _apply_early_mover_signal_state(row, trigger_check)
        if trigger_check.get("ok"):
            setup = row.get("trade_setup", {}) if isinstance(row.get("trade_setup"), dict) else {}
            return {
                "triggered": True,
                "reason": trigger_check.get("reason", "crypto_trigger_ready"),
                "last_close": trigger_check.get("last_close"),
                "entry": row.get("entry", setup.get("entry")),
                "stop": row.get("stop_loss", setup.get("stop_loss")),
                "tp1": row.get("tp1", setup.get("tp1")),
                "tp2": row.get("tp2", setup.get("tp2")),
                "live_rr": row.get("live_rr_ratio", setup.get("live_rr")),
                "row": row,
            }
        return {"triggered": False, "reason": trigger_check.get("reason", "crypto_trigger_not_ready"), "check": trigger_check}
    return _evaluate_stock_reminder(reminder)


def _format_reminder_email(reminder: Dict[str, Any], result: Dict[str, Any]) -> str:
    ticker = html.escape(str(reminder.get("ticker", "?")).upper())
    reason = html.escape(str(result.get("reason", "Trigger bereit")).replace("_", " "))
    entry = _format_alert_price(result.get("entry") or (reminder.get("row") or {}).get("entry"))
    stop = _format_alert_price(result.get("stop") or (reminder.get("row") or {}).get("stop_loss") or (reminder.get("row") or {}).get("stop"))
    tp1 = _format_alert_price(result.get("tp1") or (reminder.get("row") or {}).get("tp1"))
    tp2 = _format_alert_price(result.get("tp2") or (reminder.get("row") or {}).get("tp2"))
    price = _format_alert_price(result.get("last_close"))
    rr = result.get("live_rr") or (reminder.get("row") or {}).get("live_rr_ratio")
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827">
    <h2 style="color:#059669">Reminder: {ticker} Trigger ist da</h2>
    <p><b>Grund:</b> {reason}</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;background:#f8fafc">
      <tr><td>Preis jetzt</td><td><b>{price}</b></td></tr>
      <tr><td>Entry</td><td><b>{entry}</b></td></tr>
      <tr><td>Stop</td><td><b style="color:#dc2626">{stop}</b></td></tr>
      <tr><td>TP1 / TP2</td><td><b style="color:#059669">{tp1} / {tp2}</b></td></tr>
      <tr><td>Live R:R</td><td><b>{rr if rr is not None else '-'}</b></td></tr>
    </table>
    <p style="font-size:12px;color:#64748b">Reminder wurde automatisch deaktiviert. Bitte Chart/Spread vor Entry kurz prüfen.</p>
    </body></html>
    """


def _process_trade_reminders_once() -> None:
    now = _reminder_now()
    changed = False
    with _TRADE_REMINDER_LOCK:
        reminders = _load_trade_reminders()
        for reminder in reminders:
            if reminder.get("status") != "active":
                continue
            if now >= float(reminder.get("expires_at", 0) or 0):
                reminder["status"] = "expired"
                reminder["updated_at"] = _reminder_iso(now)
                changed = True
                continue
            last_checked = float(reminder.get("last_checked_at", 0) or 0)
            if now - last_checked < _TRADE_REMINDER_CHECK_SEC:
                continue
            reminder["last_checked_at"] = now
            result = _evaluate_trade_reminder(reminder)
            reminder["last_check"] = result
            reminder["updated_at"] = _reminder_iso(now)
            changed = True
            if result.get("triggered"):
                reminder["status"] = "triggered"
                reminder["triggered_at"] = _reminder_iso(now)
                reminder["trigger_result"] = result
                channel = str(reminder.get("channel", "email_browser"))
                if "email" in channel:
                    _send_email_alert(
                        f"Reminder: {reminder.get('ticker', '').upper()} Trigger bereit",
                        _format_reminder_email(reminder, result),
                        bypass_startup_cooldown=True,
                    )
        if changed:
            _save_trade_reminders(reminders)


def _trade_reminder_loop() -> None:
    print("[Reminder] Trade reminder monitor started")
    while _trade_reminder_running:
        try:
            _process_trade_reminders_once()
        except Exception as exc:
            print(f"[Reminder] loop error: {exc}")
        time.sleep(_TRADE_REMINDER_CHECK_SEC)
    print("[Reminder] Trade reminder monitor stopped")


def _start_trade_reminder_loop() -> None:
    global _trade_reminder_running
    if _trade_reminder_running:
        return
    _trade_reminder_running = True
    threading.Thread(target=_trade_reminder_loop, daemon=True).start()


def _stop_trade_reminder_loop() -> None:
    global _trade_reminder_running
    _trade_reminder_running = False


def _send_early_mover_long_alerts(payload: Dict[str, Any]) -> bool:
    """Mail only active Early-Mover long/retest candidates; watch/no-chase rows stay UI-only."""
    now = time.time()
    candidates = []
    suppressed: Dict[str, int] = {}
    seen_keys = set()

    for row in _flatten_early_mover_rows(payload):
        trigger_check = _verify_early_mover_intraday_trigger(row)
        trigger_block_reason = _early_mover_mail_trigger_block_reason(trigger_check)
        if trigger_block_reason:
            suppressed[trigger_block_reason] = suppressed.get(trigger_block_reason, 0) + 1
            continue
        _apply_early_mover_signal_state(row, trigger_check)

        state = _classify_alert_candidate("early_movers", row, now)
        if not state["alertable_now"]:
            for reason in state["suppression_reasons"]:
                suppressed[reason] = suppressed.get(reason, 0) + 1
            continue
        key = state["cooldown_key"]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        setup = row.get("trade_setup", {}) if isinstance(row.get("trade_setup"), dict) else {}
        btc_context = row.get("btc_context", setup.get("btc_context", {}))
        if not isinstance(btc_context, dict):
            btc_context = {}
        candidates.append({
            "key": key,
            "symbol": state["ticker"],
            "name": row.get("Name", row.get("name", "")),
            "grade": state["grade"],
            "score": state["score"],
            "price": row.get("Price", state["price"]),
            "action": row.get("trade_action", setup.get("trade_action", "")),
            "entry": row.get("entry", setup.get("entry")),
            "stop": row.get("stop_loss", row.get("stop", setup.get("stop_loss", setup.get("stop")))),
            "tp1": row.get("tp1", setup.get("tp1")),
            "tp2": row.get("tp2", setup.get("tp2")),
            "live_rr": row.get("live_rr_ratio", setup.get("live_rr")),
            "distance_r": row.get("distance_to_entry_r", setup.get("distance_to_entry_r")),
            "change24": row.get("Change24h"),
            "vol_mcap": row.get("VolMCapRatio"),
            "btc_24h": btc_context.get("btc_24h"),
            "alpha_24h": btc_context.get("alpha_24h"),
            "exchange": row.get("PerpChartExchange", row.get("Exchange", "")),
            "trigger": trigger_check,
            "execution_score": trigger_check.get("execution_score"),
            "execution_timeframe": trigger_check.get("timeframe"),
        })

    if not candidates:
        if suppressed:
            _record_email_event("Crypto Early Mover LONG Alert", "skipped", f"no_active_long_setups:{suppressed}")
        return False
    digest_remaining = _email_dedupe_remaining(_EARLY_MOVER_DIGEST_KEY, _EARLY_MOVER_DIGEST_DEDUPE_SEC, now)
    if digest_remaining > 0:
        _record_email_event(
            "Crypto Early Mover LONG Alert",
            "skipped",
            f"digest_cooldown_active:{digest_remaining}s candidates={len(candidates)}",
        )
        return False

    def _fmt_num(value, suffix="", decimals=1, default="-"):
        number = _alert_float(value)
        if number is None:
            return default
        return f"{number:.{decimals}f}{suffix}"

    def _candidate_rank(item: Dict[str, Any]) -> Tuple[float, float, float]:
        grade_rank = {"S": 4, "A+": 3, "A": 2}.get(str(item.get("grade", "")).upper(), 0)
        trigger_rank = 1 if str(item.get("action", "")).upper() == "LONG_TRIGGER" else 0
        score = _alert_float(item.get("score"), 0) or 0
        return (grade_rank, trigger_rank, score)

    candidates = sorted(candidates, key=_candidate_rank, reverse=True)
    email_rows = candidates[:_EARLY_MOVER_MAX_EMAIL_ROWS]

    rows = ""
    for item in email_rows:
        symbol = html.escape(str(item["symbol"]))
        name = html.escape(str(item["name"] or ""))
        action = html.escape(str(item["action"] or "LONG"))
        exchange = html.escape(str(item["exchange"] or ""))
        trigger = item.get("trigger", {}) if isinstance(item.get("trigger"), dict) else {}
        trigger_text = html.escape(str(trigger.get("reason", "execution_trigger")))
        exec_score = _fmt_num(item.get("execution_score"), "/100", 0)
        exec_tf = html.escape(str(item.get("execution_timeframe") or trigger.get("timeframe") or "adaptive"))
        volume_ratio = _fmt_num(trigger.get("volume_ratio"), "x", 2)
        rows += (
            f'<tr><td style="padding:8px;border-bottom:1px solid #eee"><b>{symbol}</b><br><span style="color:#777">{name}</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{action}<br><span style="color:#777">{exchange} {exec_tf}</span><br><span style="color:#059669">{trigger_text} ({volume_ratio}, EQ {exec_score})</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{html.escape(str(item["grade"]))} / {html.escape(str(item["score"]))}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{_format_alert_price(item["price"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">Entry {_format_alert_price(item["entry"])}<br>Stop {_format_alert_price(item["stop"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">TP1 {_format_alert_price(item["tp1"])}<br>TP2 {_format_alert_price(item["tp2"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{_fmt_num(item["live_rr"], "R", 2)}<br><span style="color:#777">Dist {_fmt_num(item["distance_r"], "R", 2)}</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">24h {_fmt_num(item["change24"], "%")}<br>V/MCap {_fmt_num(item["vol_mcap"], "%")}<br>BTC {_fmt_num(item["btc_24h"], "%")} / Alpha {_fmt_num(item["alpha_24h"], "%")}</td></tr>'
        )

    body = f'''<html><body style="font-family:Arial,sans-serif;max-width:980px;margin:0 auto">
    <h2 style="color:#059669">Crypto Early Mover LONG Digest</h2>
    <p style="color:#666">{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC | Top {len(email_rows)} von {len(candidates)} aktiven Long/Retest Setup(s)</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:#ecfdf5"><th style="padding:8px;text-align:left">Coin</th>
    <th style="padding:8px;text-align:left">Aktion</th><th style="padding:8px;text-align:left">Grade/Score</th>
    <th style="padding:8px;text-align:left">Preis</th><th style="padding:8px;text-align:left">Entry/Stop</th>
    <th style="padding:8px;text-align:left">TP1/TP2</th><th style="padding:8px;text-align:left">Live R</th>
    <th style="padding:8px;text-align:left">Kontext</th></tr>
    {rows}</table>
    <p style="color:#999;font-size:12px;margin-top:20px">Digest-Cooldown: {_EARLY_MOVER_DIGEST_DEDUPE_SEC // 3600}h. Nur Score >= {_ALERT_MIN_SCORE}, Grade S/A/A+, Live R:R >= {_EARLY_MOVER_MIN_ALERT_RR}, kein BTC-Gegenwind, kein No-Chase, kein extremes Vol/MCap ohne Alpha, keine Partial-Daten, unverpasster TP1 und bestaetigter 5m-Exchange-Trigger. Ohne 5m-Bestaetigung bleibt es BEOBACHTEN.</p>
    </body></html>'''
    sent = _send_email_alert(f"Crypto Early Mover LONG Digest: {len(email_rows)}/{len(candidates)} Setup(s)", body)
    if sent:
        _email_dedupe_mark(_EARLY_MOVER_DIGEST_KEY, now=now)
        for item in email_rows:
            _EMAIL_COOLDOWN[item["key"]] = now
            _email_dedupe_mark(item["key"], now=now)
    return bool(sent)


def _send_early_mover_armed_alerts(payload: Dict[str, Any]) -> bool:
    """Armed/watch mails are deliberately disabled.

    GMX showed the failure mode clearly: a pre-breakout watch can reverse before
    confirmation. Users need actionable trade alerts, not FOMO prompts.
    """
    _record_email_event(
        "Crypto Explosion Armed Alert",
        "skipped",
        "armed_watch_mail_hard_disabled_trade_signals_only",
    )
    return False


def _extract_long_entry_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "change_pct": _alert_float(_alert_get_any(row, "change_pct", "Change_Pct", "Change%", "Change %", "Änderung%", "todaysChangePerc")),
        "close_pos": _alert_float(_alert_get_any(row, "close_pos", "Close_Position", "Close Position", "Range_Pos", "Range Position")),
        "open_to_current_pct": _alert_float(_alert_get_any(row, "open_to_current_pct", "Open_To_Current_Pct", "intraday_change_pct")),
        "latest_bar_change_pct": _alert_float(row.get("latest_bar_change_pct")),
        "latest_bar_close_pos": _alert_float(row.get("latest_bar_close_pos")),
        "extension_atr": _alert_float(row.get("Extension_ATR", row.get("extension_atr"))),
        "rvol": _alert_float(row.get("rvol", row.get("RVOL"))),
        "mdr_tag": str(row.get("mdr_tag", "") or "").upper(),
    }


def _long_continuation_ok(fields: Dict[str, Any]) -> bool:
    close_pos = fields.get("close_pos")
    latest_change = fields.get("latest_bar_change_pct")
    latest_close_pos = fields.get("latest_bar_close_pos")
    rvol = fields.get("rvol")
    mdr_tag = fields.get("mdr_tag", "")

    latest_available = latest_change is not None and latest_close_pos is not None
    latest_ok = (
        latest_available
        and (
            latest_change >= -0.05
            or latest_close_pos >= 0.55
        )
    )
    volume_ok = rvol is None or rvol >= 1.2
    holding_highs = close_pos is not None and close_pos >= 0.78
    mdr_ok = "MDR" in mdr_tag and "CRASH" not in mdr_tag and close_pos is not None and close_pos >= 0.65
    return (holding_highs and latest_ok and volume_ok) or (mdr_ok and latest_ok)


def _long_entry_rule_reasons(row: Dict[str, Any]) -> List[str]:
    """Block late long mails only when the move is fading, not when it is cleanly continuing."""
    direction = str(row.get("Signal_Direction", row.get("direction", "")) or "").lower()
    if "short" in direction:
        return []
    fields = _extract_long_entry_fields(row)
    reasons: List[str] = []
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    latest_change = fields["latest_bar_change_pct"]
    latest_close_pos = fields["latest_bar_close_pos"]
    extension_atr = fields["extension_atr"]

    latest_red_fade = (
        latest_change is not None
        and latest_close_pos is not None
        and latest_change < -0.15
        and latest_close_pos < 0.45
    )
    latest_missing = latest_change is None or latest_close_pos is None
    intraday_red_fade = open_to_current is not None and open_to_current < -0.25
    not_holding_highs = change is not None and change > 3 and close_pos is not None and close_pos < 0.55
    extended = (change is not None and change >= 12) or (extension_atr is not None and extension_atr >= 4.0)
    hard_extended = (change is not None and change >= 30) or (extension_atr is not None and extension_atr >= 6.0)
    continuation_ok = _long_continuation_ok(fields)

    if latest_red_fade:
        reasons.append("latest_5m_red_fade")
    if latest_missing:
        reasons.append("fresh_5m_state_missing_wait_trigger")
    if intraday_red_fade:
        reasons.append("current_candle_red_fade")
    if not_holding_highs and (extended or latest_red_fade or intraday_red_fade):
        reasons.append("not_holding_highs_after_up_move")
    if hard_extended and not continuation_ok:
        reasons.append("hard_extended_long_wait_retest")
    elif extended and latest_missing:
        reasons.append("fresh_5m_state_missing_wait_retest")
    elif extended and (latest_red_fade or intraday_red_fade or not_holding_highs):
        reasons.append("extended_long_fading_wait_retest")
    return reasons


def _long_entry_quality(row: Dict[str, Any]) -> str:
    reasons = _long_entry_rule_reasons(row)
    if reasons:
        if any(reason.endswith("wait_retest") for reason in reasons):
            return "WAIT_RETEST"
        return "FADE_WATCH"
    fields = _extract_long_entry_fields(row)
    extended = (
        (fields["change_pct"] is not None and fields["change_pct"] >= 12)
        or (fields["extension_atr"] is not None and fields["extension_atr"] >= 4.0)
    )
    if extended and _long_continuation_ok(fields):
        return "CONTINUATION_OK"
    return "TRADEABLE"


def _extract_bear_short_fields(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Fields used to decide whether a bear row is still a tradeable short entry."""
    return {
        "change_pct": _alert_float(_alert_get_any(row, "change_pct", "Change_Pct", "Change%", "Change %", "Änderung%", "todaysChangePerc")),
        "close_pos": _alert_float(_alert_get_any(row, "close_pos", "Close_Position", "Close Position", "Range_Pos", "Range Position")),
        "open_to_current_pct": _alert_float(_alert_get_any(row, "open_to_current_pct", "Open_To_Current_Pct", "intraday_change_pct")),
        "latest_bar_change_pct": _alert_float(row.get("latest_bar_change_pct")),
        "latest_bar_close_pos": _alert_float(row.get("latest_bar_close_pos")),
        "rvol": _alert_float(row.get("rvol", row.get("RVOL"))),
        "score": _alert_float(row.get("score", row.get("Score"))),
    }


def _bear_short_rule_reasons(row: Dict[str, Any]) -> List[str]:
    """Prevent Bear Scanner mails from becoming FOMO shorts after the move is gone."""
    fields = _extract_bear_short_fields(row)
    reasons: List[str] = []
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    latest_bar_change = fields["latest_bar_change_pct"]
    latest_bar_close_pos = fields["latest_bar_close_pos"]
    rvol = fields["rvol"]
    latest_missing = latest_bar_change is None or latest_bar_close_pos is None

    if change is None:
        reasons.append("missing_current_drop")
    elif change > -3:
        reasons.append("not_down_enough_for_breakdown")
    elif change <= -12:
        reasons.append("drop_too_extended_no_chase")

    if open_to_current is not None and open_to_current > 0.2:
        reasons.append("current_candle_green_reclaim")
    if close_pos is not None and close_pos > 0.45:
        reasons.append("not_closing_near_low")
    if latest_missing:
        reasons.append("fresh_5m_state_missing_wait_trigger")
    if (
        latest_bar_change is not None
        and latest_bar_close_pos is not None
        and latest_bar_change > 0.15
        and latest_bar_close_pos > 0.55
    ):
        reasons.append("latest_5m_green_reclaim")
    if rvol is not None and rvol < 1.0:
        reasons.append("rvol_below_bear_threshold")

    return reasons


def _bear_entry_quality(row: Dict[str, Any]) -> str:
    reasons = _bear_short_rule_reasons(row)
    if not reasons:
        return "TRADEABLE"
    if "drop_too_extended_no_chase" in reasons:
        return "NO_CHASE"
    if "current_candle_green_reclaim" in reasons:
        return "RECLAIM_WATCH"
    return "WATCH"


def _bear_crash_rule_reasons(row: Dict[str, Any]) -> List[str]:
    """Crash alert is informational: active flush only, not a normal short-entry gate."""
    reasons: List[str] = []
    hard_blocks = set(_bear_short_rule_reasons(row)) - {"drop_too_extended_no_chase"}
    reasons.extend(sorted(hard_blocks))
    fields = _extract_bear_short_fields(row)
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    rvol = fields["rvol"]
    if change is None:
        if "missing_current_drop" not in reasons:
            reasons.append("missing_current_drop")
    elif change > -10:
        reasons.append("crash_drop_too_small")
    elif change <= -30:
        reasons.append("crash_drop_too_extended")
    if open_to_current is not None and open_to_current > 0.2:
        reasons.append("current_candle_green_reclaim")
    if close_pos is not None and close_pos > 0.35:
        reasons.append("crash_not_pressing_lows")
    if rvol is not None and rvol < 1.2:
        reasons.append("crash_rvol_below_threshold")
    latest_bar_change = fields["latest_bar_change_pct"]
    latest_bar_close_pos = fields["latest_bar_close_pos"]
    if (
        latest_bar_change is not None
        and latest_bar_close_pos is not None
        and latest_bar_change > 0.15
        and latest_bar_close_pos > 0.55
    ):
        reasons.append("latest_5m_green_reclaim")
    return list(dict.fromkeys(reasons))


def _bear_crash_alert_ok(row: Dict[str, Any]) -> bool:
    return not _bear_crash_rule_reasons(row)


def _classify_crash_alert_candidate(row: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    ticker = _extract_alert_ticker(row)
    grade = _extract_alert_grade(row)
    score = _alert_float(_extract_alert_score(row), 0) or 0
    rvol = _extract_alert_rvol(row)
    reasons: List[str] = []

    if not ticker:
        reasons.append("missing_ticker")
    asset_exclusion_reason = None
    if ticker:
        common_stock_universe, common_stock_source = _load_common_stock_universe()
        asset_exclusion_reason = _stock_alert_asset_exclusion_reason(
            ticker,
            common_stock_universe=common_stock_universe,
            universe_source=common_stock_source,
            require_reference=common_stock_universe is None,
        )
        if asset_exclusion_reason:
            reasons.append("non_common_stock_product")
    if grade not in _ALERT_TOP_GRADES:
        reasons.append("grade_below_alert_threshold")
    if score < _ALERT_MIN_SCORE:
        reasons.append("score_below_alert_threshold")
    reasons.extend(_bear_crash_rule_reasons(row))

    levels = _alert_trade_levels(row)
    if not levels.get("valid"):
        reasons.append("invalid_trade_plan")
        for err in levels.get("errors", [])[:2]:
            reasons.append(f"trade_{err}")
    elif levels.get("estimated"):
        reasons.append("estimated_trade_plan")
    elif not _alert_trade_plan_ok(row):
        reasons.append("trade_rr_below_threshold")
    reasons.extend(_alert_trade_health_reasons(row, "bear"))

    dedupe_key = f"crash_stock_{datetime.now().strftime('%Y%m%d')}_{ticker}" if ticker else ""
    dedupe_remaining = _email_dedupe_remaining(dedupe_key, _CRASH_ALERT_DEDUPE_SEC, now) if dedupe_key else 0
    if dedupe_remaining > 0:
        reasons.append("persistent_dedupe_active")

    return {
        "ticker": ticker,
        "grade": grade,
        "score": score,
        "price": _extract_alert_price(row),
        "rvol": rvol,
        "cooldown_key": dedupe_key,
        "persistent_dedupe_remaining_seconds": int(dedupe_remaining),
        "asset_exclusion_reason": asset_exclusion_reason,
        "alertable_now": not reasons,
        "suppression_reasons": list(dict.fromkeys(reasons)),
        **_alert_decision_from_reasons("crash", reasons),
    }


def _fetch_bear_latest_intraday_state(ticker: str) -> Dict[str, Optional[float]]:
    """Fetch the latest 5m candle so Bear mails do not chase into a live bounce."""
    if not ticker or not POLYGON_KEY:
        return {}
    try:
        bars = _fetch_recent_stock_5m_bars(ticker, limit=24)
        if not bars:
            return {}
        bar = bars[-1]
        open_ = bar.get("open", 0) or 0
        high = bar.get("high", 0) or 0
        low = bar.get("low", 0) or 0
        close = bar.get("close", 0) or 0
        if not open_ or not close:
            return {}
        change_pct = ((close - open_) / open_) * 100
        close_pos = ((close - low) / (high - low)) if high > low else 0.5
        return {
            "latest_bar_change_pct": round(change_pct, 2),
            "latest_bar_close_pos": round(close_pos, 3),
            "latest_bar_timestamp": bar.get("timestamp"),
        }
    except Exception:
        return {}


def _build_bear_structure_trade_setup(
    *,
    entry: float,
    day_high: float,
    day_low: float,
    day_open: float,
    ma20: Optional[float] = None,
    ma50: Optional[float] = None,
    low_20d: Optional[float] = None,
    low_60d: Optional[float] = None,
    change_pct: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Build a native short plan from reclaim invalidation and lower support.

    This keeps Bear/Crash mails from using synthetic R-only targets while still
    giving active flushes a concrete plan when structure is available.
    """
    try:
        entry = float(entry or 0)
        day_high = float(day_high or entry)
        day_low = float(day_low or entry)
        day_open = float(day_open or entry)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None

    day_range = max(day_high - day_low, entry * 0.025)
    buffer = max(day_range * 0.06, entry * 0.004)
    min_risk = max(entry * 0.012, day_range * 0.12)
    max_risk = entry * (0.10 if change_pct is not None and change_pct <= -10 else 0.08)

    stop_candidates: List[tuple[float, str]] = []
    if day_open > entry:
        stop_candidates.append((day_open + buffer, "day_open_reclaim"))
    if ma20 and entry < float(ma20) <= entry * 1.14:
        stop_candidates.append((float(ma20) + buffer, "ma20_reclaim"))
    if ma50 and entry < float(ma50) <= entry * 1.16:
        stop_candidates.append((float(ma50) + buffer, "ma50_reclaim"))
    if day_high > entry:
        stop_candidates.append((day_high + buffer, "day_high_reclaim"))

    stop = None
    stop_source = "intraday_reclaim_zone"
    for level, label in sorted(stop_candidates, key=lambda item: item[0] - entry):
        risk_candidate = level - entry
        if min_risk <= risk_candidate <= max_risk:
            stop = level
            stop_source = label
            break
    if stop is None:
        fallback_risk = min(max(day_range * 0.35, entry * 0.025), max_risk)
        if fallback_risk < min_risk:
            return None
        stop = entry + fallback_risk

    risk = stop - entry
    if risk <= 0:
        return None

    target_candidates: List[tuple[float, str]] = []
    if 0 < day_low < entry:
        target_candidates.append((day_low, "day_low_liquidity"))
    if low_20d and 0 < float(low_20d) < entry:
        target_candidates.append((float(low_20d), "20d_low_support"))
    if low_60d and 0 < float(low_60d) < entry:
        target_candidates.append((float(low_60d), "60d_low_support"))
    target_candidates.extend([
        (entry - day_range * 0.80, "intraday_measured_move"),
        (entry - day_range * 1.30, "extended_measured_move"),
    ])

    def _pick_short_target(min_rr: float, max_below: float, fallback_rr: float, fallback_label: str) -> tuple[float, str]:
        min_price = entry - risk * min_rr
        valid = sorted(
            {round(p, 6): label for p, label in target_candidates if 0 < p < max_below}.items(),
            reverse=True,
        )
        for level, label in valid:
            if level <= min_price:
                return level, label
        return max(0.01, entry - risk * fallback_rr), fallback_label

    tp1, tp1_source = _pick_short_target(1.25, entry, 1.5, "measured_move_fallback")
    tp2, tp2_source = _pick_short_target(2.10, tp1 - risk * 0.20, 2.5, "measured_move_fallback")
    if tp2 >= tp1:
        tp2 = max(0.01, tp1 - max(risk, day_range * 0.50))
        tp2_source = "measured_move_fallback"

    rr_tp1 = (entry - tp1) / risk if risk > 0 else 0
    rr_tp2 = (entry - tp2) / risk if risk > 0 else 0
    return {
        "Entry": _round_trade_price(entry),
        "StopLoss": _round_trade_price(stop),
        "TP1": _round_trade_price(tp1),
        "TP2": _round_trade_price(tp2),
        "entry": _round_trade_price(entry),
        "stop_loss": _round_trade_price(stop),
        "tp1": _round_trade_price(tp1),
        "tp2": _round_trade_price(tp2),
        "Risk": _round_trade_price(risk),
        "risk": _round_trade_price(risk),
        "rr": round((rr_tp1 + rr_tp2) / 2, 2),
        "rr_tp1": round(rr_tp1, 2),
        "rr_tp2": round(rr_tp2, 2),
        "level_model": "bear_structure_first_v1",
        "trade_setup_source": "native_bear_structure",
        "stop_source": stop_source,
        "tp1_source": tp1_source,
        "tp2_source": tp2_source,
    }


def _fetch_long_latest_intraday_state(ticker: str) -> Dict[str, Optional[float]]:
    """Same 5m state, used to block fading long mails without blocking continuation."""
    return _fetch_bear_latest_intraday_state(ticker)


def _stock_alert_is_short_context(scanner_name: str, row: Dict[str, Any], strategy_name: str = "") -> bool:
    direction = str(row.get("Signal_Direction", row.get("direction", row.get("side", ""))) or "").lower()
    strategy = str(strategy_name or row.get("strategy", row.get("Strategy", "")) or "").lower()
    if scanner_name in _BEARISH_STOCK_ALERT_SCANNERS:
        return True
    if "short" in direction or "bear" in direction or direction in {"sell", "put"}:
        return True
    return any(token in strategy for token in ("short", "bear", "distribution", "gap momentum short", "ma bounce short"))


def _enrich_stock_alert_5m_state(scanner_name: str, row: Dict[str, Any], strategy_name: str = "") -> Dict[str, Any]:
    """Attach fresh 5m state before any actionable stock mail decision."""
    ticker = _extract_alert_ticker(row)
    grade = _extract_alert_grade(row)
    if not ticker or grade not in _ALERT_TOP_GRADES:
        return row

    enriched = dict(row)
    if enriched.get("latest_bar_change_pct") is None or enriched.get("latest_bar_close_pos") is None:
        enriched.update(_fetch_long_latest_intraday_state(ticker))

    if _stock_alert_is_short_context(scanner_name, enriched, strategy_name):
        enriched["short_block_reasons"] = _bear_short_rule_reasons(enriched)
        enriched["entry_quality"] = _bear_entry_quality(enriched)
        enriched["alertable_short"] = not enriched["short_block_reasons"]
    elif scanner_name in _LONG_ENTRY_ALERT_SCANNERS:
        enriched["long_entry_quality"] = _long_entry_quality(enriched)
        enriched["alertable_long"] = not _long_entry_rule_reasons(enriched)
    return enriched


def _alert_cooldown_remaining(key: str, now: Optional[float] = None) -> int:
    now = now or time.time()
    last = _EMAIL_COOLDOWN.get(key)
    if not last:
        return 0
    return max(0, int(_EMAIL_COOLDOWN_SEC - (now - last)))


def _alert_decision_from_reasons(scanner_name: str, reasons: List[str]) -> Dict[str, str]:
    """Normalize alert gating into trade/watch/no-trade language."""
    if not reasons:
        return {
            "decision": "TRADE_NOW",
            "decision_label": "Jetzt traden",
            "decision_reason": "Alle Alert-Gates bestanden",
        }
    wait_retest_markers = {
        "hard_extended_long_wait_retest",
        "extended_long_fading_wait_retest",
        "not_holding_highs_after_up_move",
        "early_mover_retest_not_near_entry",
        "trade_health_wait_for_retest",
    }
    wait_trigger_markers = {
        "trade_health_wait_for_trigger",
        "trade_health_wait_for_continuation",
        "fresh_5m_state_missing_wait_trigger",
        "fresh_5m_state_missing_wait_retest",
        "current_candle_red_fade",
    }
    no_trade_markers = {
        "drop_too_extended_no_chase",
        "target_already_missed",
        "early_mover_no_chase",
        "early_mover_late_to_tp1",
        "early_mover_chased_from_entry",
        "early_mover_blowoff_turnover",
        "early_mover_turnover_without_alpha",
        "pump_continuation_risk",
        "safety_not_ok",
        "risk_too_wide",
        "not_new_listing_dump",
        "listing_age_not_tradeable",
        "non_common_stock_product",
        "estimated_trade_plan",
        "invalid_trade_plan",
        "trade_rr_below_threshold",
        "trade_health_no_trade",
        "trade_health_chase_risk",
        "trade_health_fakeout_risk",
        "trade_health_liquidity_risk",
    }
    no_trade_prefixes = ("grade_below", "missing_", "partial_", "not_tradeable", "trade_invalid_")
    has_no_trade = any(reason in no_trade_markers or reason.startswith(no_trade_prefixes) for reason in reasons)
    if has_no_trade:
        return {
            "decision": "NO_TRADE",
            "decision_label": "Nicht traden",
            "decision_reason": ", ".join(reasons[:4]),
        }
    if any(reason in wait_retest_markers or reason.endswith("wait_retest") for reason in reasons):
        return {
            "decision": "WAIT_RETEST",
            "decision_label": "Auf Retest warten",
            "decision_reason": ", ".join(reasons[:4]),
        }
    if any(reason in wait_trigger_markers for reason in reasons):
        return {
            "decision": "WAIT_TRIGGER",
            "decision_label": "Auf Trigger warten",
            "decision_reason": ", ".join(reasons[:4]),
        }
    return {
        "decision": "WATCH",
        "decision_label": "Beobachten",
        "decision_reason": ", ".join(reasons[:4]),
    }


def _classify_alert_candidate(scanner_name: str, row: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    ticker = _extract_alert_ticker(row)
    grade = _extract_alert_grade(row)
    score = _alert_float(_extract_alert_score(row), 0) or 0
    raw_grade = grade
    raw_score = score
    rvol = _extract_alert_rvol(row)
    reasons = []

    if scanner_name == "new_listing":
        nl_fields = _extract_new_listing_signal_fields(row)
        if nl_fields["grade"]:
            grade = nl_fields["grade"]
    elif scanner_name == "early_movers":
        signal = str(row.get("trade_signal", "") or "").upper()
        entry_score = _alert_float(row.get("entry_score"), None)
        if entry_score is None:
            entry_score = _early_mover_entry_score(row)
        execution_score = _alert_float(row.get("execution_quality_score"), None)
        explosion_score = _alert_float(row.get("explosion_score"), None)
        if signal == "JETZT_TRADEN":
            score = _early_mover_trade_score(row)
        elif signal == "EXPLOSION_ARMED" and explosion_score is not None:
            score = min(_alert_float(explosion_score, 0) or 0, _alert_float(entry_score, 0) or 0)
        elif execution_score is not None:
            score = min(_alert_float(score, 0) or 0, _alert_float(entry_score, 0) or 0, _alert_float(execution_score, 0) or 0)
        else:
            score = min(_alert_float(score, 0) or 0, _alert_float(entry_score, 0) or 0)
        grade, _ = _score_grade_for_value(score)
    elif scanner_name in _STOCK_ALERT_SCANNERS:
        score = _stock_alert_trade_score(row, scanner_name)

    if not ticker:
        reasons.append("missing_ticker")
    asset_exclusion_reason = None
    if ticker and scanner_name in _STOCK_EMAIL_ASSET_GUARD_SCANNERS:
        common_stock_universe, common_stock_source = _load_common_stock_universe()
        asset_exclusion_reason = _stock_alert_asset_exclusion_reason(
            ticker,
            common_stock_universe=common_stock_universe,
            universe_source=common_stock_source,
            require_reference=common_stock_universe is None,
        )
        if asset_exclusion_reason:
            reasons.append("non_common_stock_product")
    if grade not in _ALERT_TOP_GRADES:
        reasons.append("grade_below_alert_threshold")
    if score < _ALERT_MIN_SCORE:
        reasons.append("score_below_alert_threshold")
    if scanner_name in _ALERT_RVOL_GUARD_SCANNERS and (rvol is None or rvol < _ALERT_MIN_RVOL):
        reasons.append("rvol_below_alert_threshold")
    base_blockers = {
        "missing_ticker",
        "non_common_stock_product",
        "grade_below_alert_threshold",
        "score_below_alert_threshold",
        "rvol_below_alert_threshold",
    }
    base_actionable = not any(reason in reasons for reason in base_blockers)
    stock_diagnostics_allowed = (
        scanner_name in _STOCK_ALERT_SCANNERS
        and not any(reason in reasons for reason in {"missing_ticker", "non_common_stock_product"})
        and (raw_grade in _ALERT_TOP_GRADES or raw_score >= _ALERT_MIN_SCORE)
    )
    quality_gate_actionable = base_actionable or stock_diagnostics_allowed

    if base_actionable and scanner_name == "new_listing":
        reasons.extend(_new_listing_rule_reasons(row))
    if quality_gate_actionable and scanner_name in ("bear", "bi_short"):
        reasons.extend(_bear_short_rule_reasons(row))
    if quality_gate_actionable and scanner_name in _LONG_ENTRY_ALERT_SCANNERS:
        reasons.extend(_long_entry_rule_reasons(row))
    if base_actionable and scanner_name == "crypto_strategy":
        if not _CRYPTO_STRATEGY_ALERTS_ENABLED:
            reasons.append("crypto_strategy_watch_only")
        signal_quality = str(row.get("signal_quality", "") or "").lower()
        if signal_quality != "tradeable":
            reasons.append("no_crypto_tradeable_signal")
        if not bool(row.get("execution_trigger_ok") or row.get("crypto_entry_ok") or row.get("alertable_crypto")):
            reasons.append("no_crypto_execution_trigger")
        if bool(row.get("partial_data") or row.get("data_partial")):
            reasons.append("partial_crypto_data")
    if scanner_name == "early_movers":
        reasons.extend(_early_mover_long_rule_reasons(row))
    if quality_gate_actionable and scanner_name in _ALERT_TRADE_PLAN_GUARD_SCANNERS:
        levels = _alert_trade_levels(row)
        if not levels.get("valid"):
            reasons.append("invalid_trade_plan")
            for err in levels.get("errors", [])[:2]:
                reasons.append(f"trade_{err}")
        elif levels.get("estimated"):
            reasons.append("estimated_trade_plan")
        elif not _alert_trade_plan_ok(row):
            reasons.append("trade_rr_below_threshold")
    if quality_gate_actionable and scanner_name in _ALERT_TRADE_HEALTH_GUARD_SCANNERS:
        reasons.extend(_alert_trade_health_reasons(row, scanner_name))

    cooldown_key = _early_mover_alert_key(row, ticker) if scanner_name == "early_movers" else (f"{scanner_name}_{ticker}" if ticker else "")
    cooldown_ttl = _alert_dedupe_ttl_seconds(scanner_name)
    cooldown_last = _EMAIL_COOLDOWN.get(cooldown_key) if cooldown_key else None
    cooldown_remaining = max(0, int(cooldown_ttl - (now - cooldown_last))) if cooldown_last else 0
    if cooldown_remaining > 0:
        reasons.append("cooldown_active")
    dedupe_remaining = _email_dedupe_remaining(cooldown_key, cooldown_ttl, now) if cooldown_key else 0
    if dedupe_remaining > 0:
        reasons.append("persistent_dedupe_active")
    bearish_remaining = _bearish_stock_alert_remaining(ticker, now) if scanner_name in _BEARISH_STOCK_ALERT_SCANNERS else 0
    if bearish_remaining > 0:
        reasons.append("bearish_ticker_already_alerted")

    decision = _alert_decision_from_reasons(scanner_name, reasons)
    return {
        "ticker": ticker,
        "grade": grade,
        "score": int(score) if float(score).is_integer() else round(score, 2),
        "price": _extract_alert_price(row),
        "rvol": rvol,
        "cooldown_key": cooldown_key,
        "cooldown_remaining_seconds": cooldown_remaining,
        "persistent_dedupe_remaining_seconds": dedupe_remaining,
        "bearish_dedupe_remaining_seconds": bearish_remaining,
        "asset_exclusion_reason": asset_exclusion_reason,
        "alertable_now": not reasons,
        "suppression_reasons": reasons,
        **decision,
    }


def _extract_cache_rows_for_alert_audit(scanner_name: str, cache_file: str) -> List[Dict[str, Any]]:
    rows, _ = load_cache_file(cache_file, max_age_hours=24)
    if scanner_name == "orb":
        flat = []
        for container in rows:
            if isinstance(container, dict):
                for key in ("breakouts", "failed_breakouts", "candidates"):
                    flat.extend([r for r in container.get(key, []) or [] if isinstance(r, dict)])
        return flat
    if scanner_name == "bear":
        flat = []
        for container in rows:
            if isinstance(container, dict):
                flat.extend([r for r in container.get("breakdown_stocks", []) or [] if isinstance(r, dict)])
        return flat
    if scanner_name == "new_listing":
        flat = [r for r in rows if isinstance(r, dict)]
        return [
            r for r in flat
            if str(r.get("source", "")).lower() == "signals"
            or str(r.get("signal", "")).upper().startswith("SHORT")
        ]
    if scanner_name == "early_movers":
        return _flatten_early_mover_rows(rows)
    return [r for r in rows if isinstance(r, dict)]


def _build_alert_audit_for_cache(scanner_name: str, cache_file: str) -> Dict[str, Any]:
    if scanner_name == "biotech":
        _enrich_biotech_alert_trade_levels()
    rows = _extract_cache_rows_for_alert_audit(scanner_name, cache_file)
    now = time.time()
    grade_counts: Dict[str, int] = {}
    decision_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    alertable = []
    watch_preview = []
    crash_reason_counts: Dict[str, int] = {}
    crash_decision_counts: Dict[str, int] = {}
    crash_alertable = []
    armed_reason_counts: Dict[str, int] = {}
    armed_alertable = []
    for row in rows:
        if scanner_name in _STOCK_ALERT_SCANNERS:
            row = _enrich_stock_alert_5m_state(scanner_name, row)
        state = _classify_alert_candidate(scanner_name, row, now)
        grade_counts[state["grade"] or "UNKNOWN"] = grade_counts.get(state["grade"] or "UNKNOWN", 0) + 1
        decision = state.get("decision") or "UNKNOWN"
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        if state["alertable_now"]:
            alertable.append(state)
        elif len(watch_preview) < 10 and state.get("decision") in ("WATCH", "WAIT_RETEST", "WAIT_TRIGGER"):
            watch_preview.append(state)
        for reason in state["suppression_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if scanner_name == "bear":
            crash_state = _classify_crash_alert_candidate(row, now)
            crash_decision = crash_state.get("decision") or "UNKNOWN"
            crash_decision_counts[crash_decision] = crash_decision_counts.get(crash_decision, 0) + 1
            if crash_state["alertable_now"]:
                crash_alertable.append(crash_state)
            for reason in crash_state["suppression_reasons"]:
                crash_reason_counts[reason] = crash_reason_counts.get(reason, 0) + 1
        if scanner_name == "early_movers":
            trigger = row.get("intraday_trigger") if isinstance(row.get("intraday_trigger"), dict) else None
            armed_ok, armed_reasons = _early_mover_explosion_armed_state(row, trigger)
            if armed_ok:
                armed_score = _alert_float(row.get("explosion_score"), None)
                if armed_score is None:
                    armed_score = _early_mover_explosion_score(row, trigger)
                armed_grade, _ = _score_grade_for_value(int(armed_score))
                if armed_grade in _ALERT_TOP_GRADES and armed_score >= _EARLY_MOVER_MIN_ARMED_SETUP_SCORE:
                    armed_alertable.append({
                        "ticker": state.get("ticker"),
                        "grade": armed_grade,
                        "score": int(armed_score),
                        "setup_score": row.get("setup_score", row.get("score")),
                        "price": state.get("price"),
                        "entry": row.get("entry"),
                        "stop": row.get("stop_loss", row.get("stop")),
                        "tp1": row.get("tp1"),
                        "tp2": row.get("tp2"),
                        "pre_breakout_score": (trigger or {}).get("pre_breakout_score", row.get("pre_breakout_score")),
                        "live_rr": row.get("live_rr_ratio"),
                        "distance_to_entry_r": row.get("distance_to_entry_r"),
                        "mail_type": "EXPLOSION_ARMED",
                        "note": "Armed-Watch ist kein Trade und wird nicht gemailt.",
                    })
                else:
                    armed_reason_counts["armed_grade_or_score_below_threshold"] = armed_reason_counts.get("armed_grade_or_score_below_threshold", 0) + 1
            else:
                for reason in armed_reasons[:4]:
                    armed_reason_counts[reason] = armed_reason_counts.get(reason, 0) + 1

    cache_age = None
    if os.path.exists(cache_file):
        cache_age = int(max(0, time.time() - os.path.getmtime(cache_file)))

    audit = {
        "scanner": scanner_name,
        "cache_file": os.path.basename(cache_file),
        "cache_exists": os.path.exists(cache_file),
        "cache_age_seconds": cache_age,
        "rows_checked": len(rows),
        "grade_counts": grade_counts,
        "decision_counts": decision_counts,
        "alertable_now_count": len(alertable),
        "alertable_preview": alertable[:10],
        "watch_preview": watch_preview,
        "mail_status": "SEND_NOW" if alertable else "NO_MAIL",
        "mail_status_label": "Mail wuerde jetzt rausgehen" if alertable else "Keine Mail: Gates blockieren oder nur Watch",
        "suppression_counts": reason_counts,
        "suppression_top": _top_alert_reasons(reason_counts),
        "suppression_human": _format_alert_suppression_summary(reason_counts, grade_counts),
    }
    if scanner_name == "bear":
        audit.update({
            "crash_alertable_now_count": len(crash_alertable),
            "crash_alertable_preview": crash_alertable[:10],
            "crash_decision_counts": crash_decision_counts,
            "crash_suppression_counts": crash_reason_counts,
            "crash_suppression_top": _top_alert_reasons(crash_reason_counts),
            "crash_mail_status": "SEND_NOW" if crash_alertable else "NO_MAIL",
            "crash_mail_status_label": "Crash-Mail wuerde jetzt rausgehen" if crash_alertable else "Keine Crash-Mail: Gates blockieren oder Dedupe aktiv",
        })
    if scanner_name == "early_movers":
        armed_mail_enabled = False
        audit.update({
            "armed_watch_count": len(armed_alertable),
            "armed_watch_preview": armed_alertable[:10],
            "armed_alertable_now_count": len(armed_alertable) if armed_mail_enabled else 0,
            "armed_alertable_preview": armed_alertable[:10] if armed_mail_enabled else [],
            "armed_suppression_counts": armed_reason_counts,
            "armed_suppression_top": _top_alert_reasons(armed_reason_counts),
            "armed_mail_status": (
                "ARMED_READY"
                if armed_mail_enabled and armed_alertable
                else "DISABLED"
                if armed_alertable
                else "NO_ARMED_MAIL"
            ),
            "armed_mail_status_label": (
                "Armed-Watch-Mails sind deaktiviert; Mails nur fuer bestaetigte Trade-Signale."
                if armed_alertable
                else "Keine Armed-Mail: Watch/Armed ist kein bestaetigtes Trade-Signal"
            ),
            "armed_mail_enabled": armed_mail_enabled,
        })
    return audit


def _summarize_email_alert_audit(scanners: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    total_rows = 0
    total_alertable = 0
    total_crash_alertable = 0
    total_armed_alertable = 0
    aggregate_reasons: Dict[str, int] = {}
    scanner_statuses = []
    for name, audit in scanners.items():
        if not isinstance(audit, dict):
            continue
        if audit.get("error"):
            scanner_statuses.append({
                "scanner": name,
                "status": "ERROR",
                "label": f"Audit-Fehler: {audit.get('error')}",
            })
            continue
        rows = int(audit.get("rows_checked") or 0)
        alertable = int(audit.get("alertable_now_count") or 0)
        crash_alertable = int(audit.get("crash_alertable_now_count") or 0)
        armed_alertable = int(audit.get("armed_alertable_now_count") or 0)
        total_rows += rows
        total_alertable += alertable
        total_crash_alertable += crash_alertable
        total_armed_alertable += armed_alertable
        for counts_key in ("suppression_counts", "crash_suppression_counts", "armed_suppression_counts"):
            counts = audit.get(counts_key) or {}
            if isinstance(counts, dict):
                for reason, count in counts.items():
                    aggregate_reasons[reason] = aggregate_reasons.get(reason, 0) + int(count or 0)
        if alertable or crash_alertable:
            status = "SEND_NOW"
            label = f"{alertable + crash_alertable} Mail-Kandidat(en) jetzt"
        elif armed_alertable:
            status = "ARMED_READY"
            label = f"{armed_alertable} Explosion-Armed Kandidat(en); kein Market-Buy, Orderbook-Check im Sendelauf"
        elif rows == 0:
            status = "NO_CANDIDATES"
            label = "Keine aktuellen Kandidaten im Cache"
        else:
            status = "BLOCKED"
            top = _top_alert_reasons(audit.get("suppression_counts") or {}, max_items=1)
            label = top[0]["label"] if top else "Nur Watch/unter Alert-Gates"
        scanner_statuses.append({
            "scanner": name,
            "status": status,
            "label": label,
            "rows_checked": rows,
            "alertable_now_count": alertable,
            "crash_alertable_now_count": crash_alertable,
            "armed_alertable_now_count": armed_alertable,
            "cache_age_seconds": audit.get("cache_age_seconds"),
        })

    email_status = _email_alert_status()
    startup_cooldown = int(email_status.get("startup_cooldown_remaining_seconds") or 0)
    configured = bool(email_status.get("configured"))
    if not configured:
        overall = "EMAIL_NOT_CONFIGURED"
        next_step = "GMAIL_USER/GMAIL_APP_PASSWORD/ALERT_EMAIL pruefen."
    elif startup_cooldown > 0:
        overall = "STARTUP_COOLDOWN"
        next_step = f"Noch {startup_cooldown}s Startup-Cooldown nach Restart."
    elif total_alertable + total_crash_alertable > 0:
        overall = "MAIL_READY"
        next_step = "Mindestens ein Kandidat besteht alle Gates; Mail sollte beim naechsten Alert-Lauf kommen."
    elif total_armed_alertable > 0:
        overall = "ARMED_READY"
        next_step = "Crypto Armed-Kandidaten vorhanden; Mail kommt, wenn Armed-Digest-Dedupe und Orderbook-Check frei sind."
    elif total_rows == 0:
        overall = "NO_CANDIDATES"
        next_step = "Scanner-Caches enthalten aktuell keine Kandidaten fuer Mail-Audit."
    else:
        overall = "ALL_BLOCKED_BY_GATES"
        next_step = "Keine Mail ist korrekt: Score/Grade/Timing/R:R/Trade-Health/Dedupe blockt aktuell."

    return {
        "overall_status": overall,
        "next_step": next_step,
        "total_rows_checked": total_rows,
        "total_alertable_now": total_alertable,
        "total_crash_alertable_now": total_crash_alertable,
        "total_armed_alertable_now": total_armed_alertable,
        "top_blockers": _top_alert_reasons(aggregate_reasons, max_items=10),
        "scanner_statuses": scanner_statuses,
    }


def _alert_suppression_summary_for_rows(scanner_name: str, rows: List[Dict[str, Any]], now: Optional[float] = None) -> str:
    now = now or time.time()
    suppressed: Dict[str, int] = {}
    grade_counts: Dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = _classify_alert_candidate(scanner_name, row, now)
        grade_counts[state["grade"] or "UNKNOWN"] = grade_counts.get(state["grade"] or "UNKNOWN", 0) + 1
        for reason in state["suppression_reasons"]:
            suppressed[reason] = suppressed.get(reason, 0) + 1
    return _format_alert_suppression_summary(suppressed, grade_counts)


def _crash_alert_suppression_summary_for_rows(rows: List[Dict[str, Any]], now: Optional[float] = None) -> str:
    now = now or time.time()
    suppressed: Dict[str, int] = {}
    grade_counts: Dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = _classify_crash_alert_candidate(row, now)
        grade_counts[state["grade"] or "UNKNOWN"] = grade_counts.get(state["grade"] or "UNKNOWN", 0) + 1
        for reason in state["suppression_reasons"]:
            suppressed[reason] = suppressed.get(reason, 0) + 1
    return _format_alert_suppression_summary(suppressed, grade_counts)


def _extract_email_body_inner(body_html: str) -> str:
    """Accept old full HTML mails and extract only the content for the branded shell."""
    text = str(body_html or "")
    match = re.search(r"<body[^>]*>(.*?)</body>", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = match.group(1)
    text = re.sub(r"^\s*<html[^>]*>\s*", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s*</html>\s*$", "", text, flags=re.IGNORECASE | re.DOTALL)
    # Keep legacy alert bodies from leaking the old product name.
    text = text.replace("TradingBot Alert", "Alpha Station Alert")
    text = text.replace("TradingBot", "Alpha Station")
    return text.strip()


def _brand_email_html(subject: str, body_html: str) -> str:
    """Wrap every alert in the same Alpha Station email layout."""
    safe_subject = html.escape(str(subject or "Alpha Station Alert"))
    inner = _extract_email_body_inner(body_html)
    timestamp = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#0a0f1e;font-family:Arial,Helvetica,sans-serif;color:#111827">
    <div style="display:none;max-height:0;overflow:hidden;color:transparent;opacity:0">Alpha Station Signal Update</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0a0f1e;padding:28px 12px">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:860px;border-collapse:collapse">
                    <tr>
                        <td style="padding:26px 28px;border-radius:22px 22px 0 0;background:#111827;background-image:linear-gradient(135deg,#111827 0%,#172554 56%,#312e81 100%);border:1px solid rgba(148,163,184,0.22);border-bottom:0">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="vertical-align:middle">
                                        <table role="presentation" cellpadding="0" cellspacing="0">
                                            <tr>
                                                <td style="width:44px;height:44px;border-radius:14px;background:#2563eb;background-image:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#ffffff;font-size:22px;font-weight:900;text-align:center;line-height:44px">A</td>
                                                <td style="padding-left:12px">
                                                    <div style="font-size:22px;line-height:1.1;font-weight:900;color:#f8fafc;letter-spacing:-0.4px">Alpha Station</div>
                                                    <div style="font-size:12px;line-height:1.5;color:#93c5fd;letter-spacing:0.08em;text-transform:uppercase">Trading Intelligence</div>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                    <td align="right" style="vertical-align:middle">
                                        <div style="display:inline-block;padding:7px 12px;border-radius:999px;background:rgba(16,185,129,0.13);border:1px solid rgba(52,211,153,0.32);color:#6ee7b7;font-size:12px;font-weight:800">Signal Mail</div>
                                    </td>
                                </tr>
                            </table>
                            <div style="margin-top:24px;font-size:26px;line-height:1.25;font-weight:900;color:#ffffff;letter-spacing:-0.5px">{safe_subject}</div>
                            <div style="margin-top:7px;color:#cbd5e1;font-size:13px">{timestamp}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="background:#f8fafc;border-left:1px solid rgba(148,163,184,0.25);border-right:1px solid rgba(148,163,184,0.25);padding:0">
                            <div style="padding:24px 28px">
                                <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:18px;padding:22px;box-shadow:0 12px 36px rgba(15,23,42,0.08)">
                                    {inner}
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:20px 28px 24px;border-radius:0 0 22px 22px;background:#0f172a;border:1px solid rgba(148,163,184,0.22);border-top:0;color:#94a3b8;font-size:12px;line-height:1.7">
                            <div style="font-weight:800;color:#e2e8f0;margin-bottom:4px">Alpha Station</div>
                            <div>Automatischer Analyse-Alert. Keine Anlageberatung, keine Kauf-/Verkaufsempfehlung. Trading erfolgt eigenverantwortlich.</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


def _send_email_alert(
    subject,
    body_html,
    bypass_startup_cooldown: bool = False,
    recipient_emails: Optional[List[str]] = None,
):
    """Sendet E-Mail Alert via Gmail SMTP."""
    if _email_has_blocked_etf_content(subject, body_html):
        print(f"[Alert] SKIP (ETF/ETP-Inhalt blockiert): {subject}")
        _record_email_event(subject, "skipped", "blocked_etf_content")
        return False
    # V2.6b: Nach Restart 5 Min warten (alte Cache-Daten erzeugen Phantom-Alerts)
    if not bypass_startup_cooldown and time.time() - _EMAIL_STARTUP_TIME < _EMAIL_STARTUP_DELAY:
        print(f"[Alert] SKIP (Startup-Cooldown): {subject}")
        _record_email_event(subject, "skipped", "startup_cooldown")
        return False
    gmail_user = _SECRETS.get("GMAIL_USER", "")
    gmail_pass = _SECRETS.get("GMAIL_APP_PASSWORD", "")
    alert_to = _SECRETS.get("ALERT_EMAIL", gmail_user)
    if not gmail_user or not gmail_pass:
        print("[Alert] SKIP: GMAIL_USER oder GMAIL_APP_PASSWORD fehlt")
        _record_email_event(subject, "skipped", "missing_gmail_config")
        return False
    if recipient_emails is not None:
        recipients = [addr.strip().lower() for addr in recipient_emails if str(addr).strip()]
    else:
        recipients = [addr.strip().lower() for addr in str(alert_to).split(",") if addr.strip()]
    if recipient_emails is None and ALERT_SEND_TO_SUBSCRIBERS and HAS_AUTH:
        try:
            recipients.extend(get_email_alert_recipients())
        except Exception as exc:
            print(f"[Alert] Subscriber recipients skipped: {exc}")
    recipients = sorted(set(addr for addr in recipients if "@" in addr))
    if not recipients:
        print("[Alert] SKIP: ALERT_EMAIL/GMAIL_USER Empfaenger fehlt")
        _record_email_event(subject, "skipped", "missing_recipient")
        return False
    branded_body_html = _brand_email_html(subject, body_html)
    for attempt in range(3):
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"Alpha Station Alert <{gmail_user}>"
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            plain = re.sub(r"<[^>]+>", "", branded_body_html.replace("<br>", "\n").replace("</tr>", "\n"))
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(branded_body_html, "html", "utf-8"))
            # Try port 587 (STARTTLS) first, fallback to 465 (SSL)
            try:
                server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, recipients, msg.as_string())
                server.quit()
            except Exception:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
                    server.login(gmail_user, gmail_pass)
                    server.sendmail(gmail_user, recipients, msg.as_string())
            print(f"[Alert] Email gesendet: {subject}")
            _record_email_event(subject, "sent")
            return True
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"[Alert] Email FEHLER nach 3 Versuchen: {e}")
                _record_email_event(subject, "error", str(e))
                return False


def _check_and_alert(scanner_name, cache_file):
    """Prüft Scan-Ergebnisse auf Grade S/A/B und sendet Alert."""
    now = time.time()
    try:
        if not os.path.exists(cache_file):
            print(f"[Alert] {scanner_name}: Cache-Datei nicht vorhanden: {cache_file}")
            return
        with open(cache_file, "r") as f:
            data = json.load(f)
        results = data if isinstance(data, list) else data.get("results", data.get("data", []))
        if isinstance(results, list) and len(results) > 0 and isinstance(results[0], dict) and "data" in results[0]:
            results = results[0].get("data", results)
        if not isinstance(results, list):
            print(f"[Alert] {scanner_name}: Cache hat kein gültiges results-Array")
            return
        print(f"[Alert] {scanner_name}: {len(results)} Ergebnisse gefunden, prüfe Grades...")
        # V2.6: Grade-Schwellen VERSCHÄRFT — nur noch hochkarätige Setups
        # BI: S/A (kein B mehr!), Biotech: A (hat kein S)
        alerts = []
        suppressed: Dict[str, int] = {}
        _alert_grades = _ALERT_TOP_GRADES
        for r in results:
            if not isinstance(r, dict):
                continue
            if scanner_name in _STOCK_ALERT_SCANNERS:
                r = _enrich_stock_alert_5m_state(scanner_name, r)
            elif scanner_name in _LONG_ENTRY_ALERT_SCANNERS:
                ticker_probe = _extract_alert_ticker(r)
                grade_probe = _extract_alert_grade(r)
                if ticker_probe and grade_probe in _ALERT_TOP_GRADES:
                    r = dict(r)
                    r.update(_fetch_long_latest_intraday_state(ticker_probe))
                    r["long_entry_quality"] = _long_entry_quality(r)
                    r["alertable_long"] = not _long_entry_rule_reasons(r)
            state = _classify_alert_candidate(scanner_name, r, now)
            ticker = state["ticker"]
            grade = state["grade"]
            score = state["score"]
            _rvol_check = state["rvol"] if state["rvol"] is not None else 0
            if not state["alertable_now"]:
                for reason in state["suppression_reasons"]:
                    suppressed[reason] = suppressed.get(reason, 0) + 1
                continue
            # RVOL Guard: Grade S/A braucht min RVOL 0.7 — Sicherheitsnetz
            if scanner_name in _ALERT_RVOL_GUARD_SCANNERS and grade in ("S", "A", "A+") and _rvol_check < 0.7:
                grade = "B"  # Downgrade — kein Alert
            if grade not in _alert_grades:
                continue
            ck = f"{scanner_name}_{ticker}"
            ck_ttl = _alert_dedupe_ttl_seconds(scanner_name)
            if ck in _EMAIL_COOLDOWN and now - _EMAIL_COOLDOWN[ck] < ck_ttl:
                continue
            alerts.append({"ticker": ticker, "grade": grade, "score": score,
                           "price": _extract_alert_price(r),
                           "direction": r.get("BI_Direction", r.get("direction", "")),
                           "rvol": r.get("RVOL", r.get("rvol", 0)),
                           "entry_quality": r.get("long_entry_quality", ""),
                           "trade_plan_html": _format_alert_plan_html(r),
                           "cooldown_key": ck})
        if not alerts:
            # Log warum keine Alerts
            all_grades = [_extract_alert_grade(r) or "?" for r in results if isinstance(r, dict)]
            grade_counts = dict((g, all_grades.count(g)) for g in set(all_grades))
            print(f"[Alert] {scanner_name}: Keine alertbaren Grades. Vorhandene Grades: {grade_counts}; suppressed={suppressed}")
            if scanner_name in _STOCK_ALERT_SCANNERS:
                _record_email_event(
                    f"{scanner_name} Stock Alert",
                    "skipped",
                    f"no_alertable_stock_setups:{_format_alert_suppression_summary(suppressed, grade_counts)}",
                )
            return
        labels = {"bi_long": "BI Scanner LONG", "bi_short": "BI Scanner SHORT",
                  "biotech": "Biotech Scanner", "bear": "Bear Scanner"}
        label = labels.get(scanner_name, scanner_name)
        n = len(alerts)
        # Emoji pro Grade
        _grade_emoji = {"S": "🏆", "A": "🔥", "A+": "🔥", "B": "⭐"}
        subject = f"🚨 {n} Top-Setup{'s' if n > 1 else ''} — {label}"
        rows = ""
        for a in alerts:
            emoji = _grade_emoji.get(a["grade"], "📊")
            rows += f'<tr><td style="padding:8px;border-bottom:1px solid #eee"><b>{a["ticker"]}</b></td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{emoji} {a["grade"]}</td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{a["score"]}</td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{_format_alert_price(a["price"])}</td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{a["rvol"]}x</td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{a["trade_plan_html"]}</td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{a.get("entry_quality", "")}</td></tr>'
        body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
        <h2 style="color:#1a73e8">🚨 TradingBot Alert — {label}</h2>
        <p style="color:#666">{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC | {n} starke Setups</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr style="background:#f5f5f5"><th style="padding:8px;text-align:left">Ticker</th>
        <th style="padding:8px;text-align:left">Grade</th><th style="padding:8px;text-align:left">Score</th>
        <th style="padding:8px;text-align:left">Preis</th><th style="padding:8px;text-align:left">RVOL</th>
        <th style="padding:8px;text-align:left">Entry / Stop / TP</th><th style="padding:8px;text-align:left">Timing</th></tr>
        {rows}</table>
        <p style="color:#999;font-size:12px;margin-top:20px">Automatischer Alert — S = ELITE | A = STARK | B = SOLIDE</p>
        </body></html>'''
        print(f"[Alert] {scanner_name}: Sende Alert für {n} Treffer: {[a['ticker'] for a in alerts]}")
        sent = _send_email_alert(subject, body)
        if sent:
            for alert in alerts:
                _EMAIL_COOLDOWN[alert["cooldown_key"]] = now
                _email_dedupe_mark(alert["cooldown_key"], now=now)
                if scanner_name in _BEARISH_STOCK_ALERT_SCANNERS:
                    _mark_bearish_stock_alert(alert["ticker"], now=now)
    except Exception as e:
        import traceback
        print(f"[Alert] Check-Fehler {scanner_name}: {e}\n{traceback.format_exc()}")


def _send_strategy_scan_alerts(strategy_name: str, results: List[Dict[str, Any]], market_type: str = "stocks") -> None:
    """Mail top S/A strategy rows when a manual or scheduled strategy scan produces them."""
    if not results:
        return
    scanner_key = "crypto_strategy" if market_type == "crypto" else "stock_strategy"
    now = time.time()
    alerts = []
    suppressed: Dict[str, int] = {}
    grade_counts: Dict[str, int] = {}
    seen_cooldown_keys = set()
    for row in results[:50]:
        if not isinstance(row, dict):
            continue
        grade_for_counts = _extract_alert_grade(row) or "UNKNOWN"
        grade_counts[grade_for_counts] = grade_counts.get(grade_for_counts, 0) + 1
        if scanner_key in _STOCK_ALERT_SCANNERS:
            row = _enrich_stock_alert_5m_state(scanner_key, row, strategy_name)
        state = _classify_alert_candidate(scanner_key, row, now)
        if not state["alertable_now"]:
            for reason in state["suppression_reasons"]:
                suppressed[reason] = suppressed.get(reason, 0) + 1
            continue
        if state["cooldown_key"] in seen_cooldown_keys:
            suppressed["duplicate_ticker_in_scan"] = suppressed.get("duplicate_ticker_in_scan", 0) + 1
            continue
        seen_cooldown_keys.add(state["cooldown_key"])
        alerts.append({
            "cooldown_key": state["cooldown_key"],
            "ticker": state["ticker"],
            "grade": state["grade"],
            "score": state["score"],
            "price": state["price"],
            "rvol": _alert_float(state["rvol"], 0) or 0,
            "change_pct": _alert_float(_alert_get_any(row, "change_pct", "Change_Pct", "Change%", "Change %", "Änderung%", default=0), 0) or 0,
            "entry_quality": row.get("long_entry_quality", _long_entry_quality(row) if scanner_key in _LONG_ENTRY_ALERT_SCANNERS else ""),
            "strategy": row.get("Strategy") or row.get("strategy") or strategy_name,
            "market_type": market_type,
            "trade_plan_html": _format_alert_plan_html(row),
        })

    if not alerts:
        if market_type == "stocks":
            _record_email_event(
                f"Aktien Strategie Alert - {strategy_name}",
                "skipped",
                f"no_alertable_strategy_setups:{_format_alert_suppression_summary(suppressed, grade_counts)}",
            )
        return

    rows = ""
    for a in alerts[:10]:
        rows += (
            f'<tr><td style="padding:8px;border-bottom:1px solid #eee"><b>{a["ticker"]}</b></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["strategy"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["grade"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["score"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{_format_alert_price(a["price"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["change_pct"]:+.1f}%</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["rvol"]:.1f}x</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["trade_plan_html"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["entry_quality"]}</td></tr>'
        )
    label = "Crypto Strategie" if market_type == "crypto" else "Aktien Strategie Swing"
    horizon_note = (
        "Swing-Setup: mehrtaegiger Plan. Entry/Stop/TP sind Struktur-Level; "
        "nicht als Intraday-Scalp oder sofortiger Minuten-TP interpretieren."
        if market_type == "stocks"
        else "Crypto Strategie-Alert: nur mit bestaetigtem Exchange-Trigger handeln."
    )
    trigger_note = (
        "Intraday-5m/1m-Trigger werden separat gebaut und gemailt."
        if market_type == "stocks"
        else "Nur Score, Grade, frische Trigger-Qualitaet und Cooldown-Gates."
    )
    body = f'''<html><body style="font-family:Arial,sans-serif;max-width:760px;margin:0 auto">
    <h2 style="color:#1a73e8">{label} Alert - {strategy_name}</h2>
    <p style="color:#666">{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC | {len(alerts)} S/A Setup(s) ab Score {_ALERT_MIN_SCORE}</p>
    <p style="background:#eef6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;color:#1e3a8a;font-size:13px">{horizon_note}</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:#f5f5f5"><th style="padding:8px;text-align:left">Ticker</th>
    <th style="padding:8px;text-align:left">Strategie</th><th style="padding:8px;text-align:left">Grade</th>
    <th style="padding:8px;text-align:left">Score</th><th style="padding:8px;text-align:left">Preis</th>
    <th style="padding:8px;text-align:left">Change</th><th style="padding:8px;text-align:left">RVOL</th>
    <th style="padding:8px;text-align:left">Entry / Stop / TP</th><th style="padding:8px;text-align:left">Timing</th></tr>
    {rows}</table>
    <p style="color:#999;font-size:12px;margin-top:20px">Nur Score >= {_ALERT_MIN_SCORE}, Grade S/A/A+ und Alert-Gates; 8h Cooldown pro Ticker. {trigger_note}</p>
    </body></html>'''
    sent = _send_email_alert(f"{label}: {len(alerts)} Top-Setup(s) - {strategy_name}", body)
    if sent:
        for alert in alerts:
            if alert.get("cooldown_key"):
                _EMAIL_COOLDOWN[alert["cooldown_key"]] = now
                _email_dedupe_mark(alert["cooldown_key"], now=now)


def _new_listing_nested_signal(entry: Dict[str, Any]) -> Dict[str, Any]:
    sig = entry.get("signal", {}) if isinstance(entry, dict) else {}
    return sig if isinstance(sig, dict) else {}


def _new_listing_watch_candidates(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return candidates

    for bucket in ("signals", "watchlist"):
        for entry in payload.get(bucket, []) or []:
            if not isinstance(entry, dict):
                continue
            sig = _new_listing_nested_signal(entry)
            fields = _extract_new_listing_signal_fields(entry)
            if fields["listing_source"] != "new_listing":
                continue
            if _new_listing_exchange_mismatch(entry):
                continue
            if entry.get("announcement_source") and "contract_confirmed" in entry and not _alert_bool(entry.get("contract_confirmed")):
                continue
            symbol = _display_crypto_contract_symbol(entry.get("symbol") or sig.get("symbol") or "")
            if not symbol:
                continue
            pump = sig.get("pump_data", {}) if isinstance(sig.get("pump_data", {}), dict) else {}
            candidates.append({
                "symbol": symbol,
                "exchange": entry.get("exchange", ""),
                "bucket": bucket,
                "grade": fields["grade"] or str(entry.get("grade", "") or ""),
                "timing": sig.get("timing", entry.get("timing", "")),
                "category": fields["trade_category"] or sig.get("trade_category", entry.get("trade_category", "")),
                "age": fields["listing_age_hours"],
                "pump_pct": _alert_float(pump.get("pump_pct", entry.get("pump_pct")), 0) or 0,
                "from_ath_pct": _alert_float(pump.get("from_ath_pct", entry.get("from_ath_pct")), 0) or 0,
                "exh_score": _alert_float(sig.get("exh_score", entry.get("exh_score")), 0) or 0,
                "rr": fields["rr_effective"],
                "btc_change": _alert_float(pump.get("btc_change_pct", sig.get("btc_change_pct")), None),
                "coin_change": _alert_float(pump.get("coin_change_pct", sig.get("coin_change_pct")), None),
                "btc_divergence": _alert_float(pump.get("btc_divergence", sig.get("btc_divergence")), None),
                "btc_context": str(pump.get("btc_short_context", sig.get("btc_short_context", "")) or ""),
                "risk_flags": sig.get("risk_flags", entry.get("risk_flags", [])) if isinstance(sig.get("risk_flags", entry.get("risk_flags", [])), list) else [],
                "title": entry.get("announcement_title", ""),
                "url": entry.get("announcement_url", ""),
                "listing_source": fields["listing_source"],
            })

    for item in payload.get("monitoring", []) or []:
        if not isinstance(item, dict) or item.get("source") != "new_listing":
            continue
        if _new_listing_exchange_mismatch(item):
            continue
        if item.get("announcement_source") and "contract_confirmed" in item and not _alert_bool(item.get("contract_confirmed")):
            continue
        category = str(item.get("trade_category", "") or "")
        if category in ("ALREADY_DUMPED", "NEW_LISTING_EXPIRED"):
            continue
        symbol = _display_crypto_contract_symbol(item.get("symbol", ""))
        if not symbol:
            continue
        candidates.append({
            "symbol": symbol,
            "exchange": item.get("exchange", ""),
            "bucket": "monitoring",
            "grade": item.get("grade", ""),
            "timing": item.get("timing", ""),
            "category": category or "NEW_LISTING_WATCH",
            "age": _alert_float(item.get("listing_age_hours")),
            "pump_pct": _alert_float(item.get("pump_pct"), 0) or 0,
            "from_ath_pct": _alert_float(item.get("from_ath_pct"), 0) or 0,
            "exh_score": _alert_float(item.get("exh_score"), 0) or 0,
            "rr": _alert_float(item.get("rr_effective"), 0) or 0,
            "btc_change": _alert_float(item.get("btc_change_pct"), None),
            "coin_change": _alert_float(item.get("coin_change_pct"), None),
            "btc_divergence": _alert_float(item.get("btc_divergence"), None),
            "btc_context": str(item.get("btc_short_context", "") or ""),
            "risk_flags": item.get("risk_flags", []) if isinstance(item.get("risk_flags", []), list) else [],
            "title": item.get("announcement_title", ""),
            "url": item.get("announcement_url", ""),
            "listing_source": item.get("source", ""),
        })

    deduped: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate["symbol"]
        old = deduped.get(key)
        candidate_rank = (
            candidate["bucket"] == "signals",
            candidate["bucket"] in ("watchlist", "monitoring"),
            candidate["bucket"] == "announcement",
            candidate["exh_score"] or 0,
            candidate["rr"] or 0,
        )
        old_rank = (
            old["bucket"] == "signals",
            old["bucket"] in ("watchlist", "monitoring"),
            old["bucket"] == "announcement",
            old["exh_score"] or 0,
            old["rr"] or 0,
        ) if old else None
        if old is None or candidate_rank > old_rank:
            deduped[key] = candidate
    visible = []
    for c in deduped.values():
        score = c.get("exh_score") or 0
        pump_pct = c.get("pump_pct") or 0
        rr = c.get("rr") or 0
        flags = set(c.get("risk_flags") or [])
        watch_quality = (
            score >= _NEW_LISTING_WATCH_MIN_SCORE
            and rr >= _NEW_LISTING_WATCH_MIN_RR
            and not (flags & _NEW_LISTING_WATCH_BLOCK_FLAGS)
        )
        if pump_pct >= _NEW_LISTING_WATCH_MIN_PUMP_PCT and watch_quality:
            visible.append(c)
    return sorted(
        visible,
        key=lambda c: (c["bucket"] == "announcement", c["bucket"] != "signals", -(c["exh_score"] or 0), c.get("age") or 999),
    )[:12]


def _send_new_listing_watch_email(payload: Dict[str, Any], suppressed: Optional[Dict[str, int]] = None, now: Optional[float] = None) -> bool:
    if not _SEND_WATCHLIST_EMAILS:
        _record_email_event("Crypto New Listing Watchlist", "skipped", "watchlist_emails_disabled_signal_only_mode")
        return False
    now = now or time.time()
    candidates = _new_listing_watch_candidates(payload)
    if not candidates:
        _record_email_event("Crypto New Listing Watchlist", "skipped", "no_new_listing_watch_candidates")
        return False

    watch_dt = datetime.fromtimestamp(now, timezone.utc)
    day_key = watch_dt.strftime("%Y%m%d")
    dedupe_key = f"new_listing_watch_{day_key}"
    if not _email_dedupe_claim(dedupe_key, _NEW_LISTING_WATCH_DEDUPE_SEC, now=now):
        _record_email_event("Crypto New Listing Watchlist", "skipped", "daily_watchlist_dedupe_active")
        return False

    def _fmt(value, suffix="", default="-"):
        if value is None:
            return default
        try:
            return f"{float(value):.1f}{suffix}"
        except (TypeError, ValueError):
            return str(value)

    def _safe(value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

    def _watch_action(c: Dict[str, Any]) -> Tuple[str, str]:
        flags = set(c.get("risk_flags") or [])
        if "btc_risk_on_wait_for_deeper_crack" in flags:
            return "WARTEN", "BTC ist nicht klar bearish - tieferen 5m Crack abwarten."
        if "micro_trigger_missing" in flags or "wait_for_dump_trigger" in flags:
            return "WARTEN", "5m Strukturbruch/Rejection fehlt noch."
        if "turn_not_confirmed" in flags or "crack_structure_weak" in flags:
            return "WARTEN", "Turn/Rejection ist noch zu schwach."
        return "BEOBACHTEN", "Nur auf Watchlist - Short-Freigabe fehlt."

    def _missing_steps(c: Dict[str, Any]) -> str:
        flags = set(c.get("risk_flags") or [])
        priority = [
            "micro_trigger_missing",
            "wait_for_dump_trigger",
            "btc_risk_on_wait_for_deeper_crack",
            "turn_not_confirmed",
            "crack_structure_weak",
            "no_first_crack",
            "pump_continuation_risk",
        ]
        labels = [_alert_reason_label(flag) for flag in priority if flag in flags]
        if not labels:
            labels = ["5m Crack/Rejection, Safety OK und R:R muessen fuer die Short-Mail passen"]
        return "<br>".join(f"- {_safe(label)}" for label in labels[:4])

    rows = ""
    for c in candidates:
        action, action_note = _watch_action(c)
        title = str(c.get("title") or c.get("timing") or "")
        if len(title) > 95:
            title = title[:92] + "..."
        link = f'<br><a href="{_safe(c.get("url"))}" style="color:#2563eb">Quelle</a>' if c.get("url") else ""
        why = (
            f'Pump {_fmt(c["pump_pct"], "%")}, Abstand ATH {_fmt(c["from_ath_pct"], "%")}, '
            "aber noch kein bestaetigter Short-Trigger."
        )
        rows += (
            f'<tr><td style="padding:10px;border-bottom:1px solid #eee;vertical-align:top"><b>{_safe(c["symbol"])}</b><br><span style="color:#777">{_safe(c["exchange"])}</span>{link}</td>'
            f'<td style="padding:10px;border-bottom:1px solid #eee;vertical-align:top"><span style="display:inline-block;background:#fee2e2;color:#991b1b;font-weight:700;border-radius:6px;padding:3px 7px">NICHT SHORTEN</span><br><b>{_safe(action)}</b><br><span style="color:#555">{_safe(action_note)}</span></td>'
            f'<td style="padding:10px;border-bottom:1px solid #eee;vertical-align:top">{_safe(why)}<br><span style="color:#777">Alter {_fmt(c["age"], "h")} | {_safe(title)}</span></td>'
            f'<td style="padding:10px;border-bottom:1px solid #eee;vertical-align:top"><b>{_safe(c["grade"] or "-")} / {int(c["exh_score"] or 0)}</b><br>{_fmt(c["rr"], "R")}<br><span style="color:#777">BTC {_fmt(c["btc_change"], "%")} | Coin {_fmt(c["coin_change"], "%")} | Div {_fmt(c["btc_divergence"], "%")}</span></td>'
            f'<td style="padding:10px;border-bottom:1px solid #eee;vertical-align:top;color:#92400e">{_missing_steps(c)}</td></tr>'
        )

    suppressed_text = ""
    if suppressed:
        suppressed_text = "<p style='color:#777;font-size:12px'>Warum keine Jetzt-Short-Mail: " + ", ".join(f"{_safe(_alert_reason_label(k))}: {v}" for k, v in sorted(suppressed.items())) + "</p>"
    body = f'''<html><body style="font-family:Arial,sans-serif;max-width:980px;margin:0 auto">
    <h2 style="color:#f97316">Crypto New Listing Watch - NICHT SHORTEN</h2>
    <p style="color:#666">{watch_dt.strftime("%d.%m.%Y %H:%M")} UTC | {len(candidates)} Coin(s) nur zur Beobachtung</p>
    <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:12px;margin:12px 0;color:#7c2d12">
      <b>Was jetzt tun?</b> Nicht shorten. Coin auf Watchlist lassen und auf die separate <b>Jetzt-Short-Mail</b> warten.
      Diese Watch-Mail bedeutet nur: New Listing hat bereits gepumpt, aber 5m Crack/Rejection, Safety oder Timing sind noch nicht voll bestaetigt.
    </div>
    {suppressed_text}
    <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:#fff7ed"><th style="padding:8px;text-align:left">Coin</th>
    <th style="padding:8px;text-align:left">Aktion</th><th style="padding:8px;text-align:left">Warum im Blick</th>
    <th style="padding:8px;text-align:left">Qualitaet</th><th style="padding:8px;text-align:left">Was fehlt</th></tr>
    {rows}</table>
    <p style="color:#999;font-size:12px;margin-top:20px">Watch-Mail maximal 1x taeglich und nur nach Pump >= {_NEW_LISTING_WATCH_MIN_PUMP_PCT:.0f}%, Watch-Score >= {_NEW_LISTING_WATCH_MIN_SCORE}, R:R >= {_NEW_LISTING_WATCH_MIN_RR:.1f}R und ohne Safety-/Low-Quality-Blocker. JETZT SHORTEN kommt separat nur bei 5m Micro-Crack/Rejection, Safety OK, ausreichendem R:R und New-Listing-Alter im Fenster.</p>
    </body></html>'''
    return _send_email_alert(f"Crypto New Listing beobachten - NICHT SHORTEN: {len(candidates)} Coin(s)", body)


def _send_new_listing_pipeline_alerts(payload: Dict[str, Any]) -> None:
    """Mail S/A active Pump-&-Dump short signals from the FastAPI pipeline."""
    signals = payload.get("signals", []) if isinstance(payload, dict) else []
    if not signals:
        _send_new_listing_watch_email(payload if isinstance(payload, dict) else {})
        return
    now = time.time()
    alerts = []
    suppressed: Dict[str, int] = {}

    def _fmt_pct(value: Any) -> str:
        number = _alert_float(value)
        if number is None:
            return "-"
        return f"{number:.1f}%"

    for entry in signals:
        if not isinstance(entry, dict):
            continue
        sig = entry.get("signal", {}) or {}
        fields = _extract_new_listing_signal_fields(entry)
        state = _classify_alert_candidate("new_listing", entry, now)
        if not state.get("alertable_now"):
            for reason in state.get("suppression_reasons", []):
                suppressed[reason] = suppressed.get(reason, 0) + 1
            continue
        symbol = _display_crypto_contract_symbol(entry.get("symbol") or sig.get("symbol") or "")
        cooldown_key = state.get("cooldown_key") or f"new_listing_{symbol}"
        pump = sig.get("pump_data", {}) if isinstance(sig.get("pump_data", {}), dict) else {}
        alerts.append({
            "symbol": symbol,
            "exchange": entry.get("exchange", ""),
            "grade": fields["grade"],
            "timing": sig.get("timing", ""),
            "setup": sig.get("setup_type", ""),
            "stop_model": sig.get("stop_model", ""),
            "entry": sig.get("entry", 0),
            "stop": sig.get("stop_loss", sig.get("stop", 0)),
            "tp1": sig.get("tp1", 0),
            "tp2": sig.get("tp2", 0),
            "rr": fields["rr_effective"],
            "exh_score": state.get("score", 0),
            "micro_score": pump.get("micro_score", 0),
            "btc_change": pump.get("btc_change_pct", sig.get("btc_change_pct")),
            "coin_change": pump.get("coin_change_pct", sig.get("coin_change_pct")),
            "btc_divergence": pump.get("btc_divergence", sig.get("btc_divergence")),
            "btc_context": pump.get("btc_short_context", sig.get("btc_short_context", "")),
            "cooldown_key": cooldown_key,
        })
    if not alerts:
        if suppressed:
            _record_email_event("Pump & Dump SHORT Alert", "skipped", f"no_active_short_signals:{suppressed}")
        _send_new_listing_watch_email(payload if isinstance(payload, dict) else {}, suppressed=suppressed, now=now)
        return

    rows = ""
    for a in alerts[:10]:
        rows += (
            f'<tr><td style="padding:8px;border-bottom:1px solid #eee"><b>{a["symbol"]}</b></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["exchange"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["grade"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["setup"] or a["timing"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">${a["entry"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">${a["stop"]}<br><span style="color:#999;font-size:11px">{a["stop_model"]}</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">${a["tp1"]} / ${a["tp2"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">{a["rr"]}R<br><span style="color:#999;font-size:11px">Micro {a["micro_score"]}</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">BTC {_fmt_pct(a["btc_change"])}<br>Coin {_fmt_pct(a["coin_change"])}<br>Div {_fmt_pct(a["btc_divergence"])}</td></tr>'
        )
    body = f'''<html><body style="font-family:Arial,sans-serif;max-width:820px;margin:0 auto">
    <h2 style="color:#dc2626">Pump & Dump SHORT Alert</h2>
    <p style="color:#666">{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC | {len(alerts)} aktive S/A Signale ab Score {_ALERT_MIN_SCORE}</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:#fef2f2"><th style="padding:8px;text-align:left">Coin</th>
    <th style="padding:8px;text-align:left">Exchange</th><th style="padding:8px;text-align:left">Grade</th>
    <th style="padding:8px;text-align:left">Timing</th><th style="padding:8px;text-align:left">Entry</th>
    <th style="padding:8px;text-align:left">Stop</th><th style="padding:8px;text-align:left">TP1/TP2</th>
    <th style="padding:8px;text-align:left">R</th><th style="padding:8px;text-align:left">BTC</th></tr>
    {rows}</table>
    <p style="color:#999;font-size:12px;margin-top:20px">Nur echte New-Listing-Dump JETZT-SHORTEN Signale: Score >= {_ALERT_MIN_SCORE}, New-Listing-Quelle + gueltiges Listing-Alter, Timing-Quality >=4, Safety OK, erster Crack/Rejection bestaetigt, kein Pump-Continuation-Risk, TP-Zonen nicht verpasst, R:R >= {_NEW_LISTING_MIN_ALERT_RR}; Active-Pumps bleiben Beobachtung ohne Trade-Mail; 8h Cooldown pro Coin.</p>
    </body></html>'''
    sent = _send_email_alert(f"Pump & Dump: {len(alerts)} SHORT Top-Signal(e)", body)
    if sent:
        for alert in alerts:
            _EMAIL_COOLDOWN[alert["cooldown_key"]] = now
            _email_dedupe_mark(alert["cooldown_key"], now=now)


# Cleanup cooldown (alle 4h wird automatisch bereinigt)
def _cleanup_email_cooldown():
    now = time.time()
    expired = [k for k, ts in _EMAIL_COOLDOWN.items() if now - ts > _EMAIL_COOLDOWN_SEC]
    for k in expired:
        del _EMAIL_COOLDOWN[k]


# ── Pydantic Models ──
class ScanRequest(BaseModel):
    strategy: str
    market_type: str = "stocks"  # stocks, crypto, futures, forex
    session: Optional[str] = None  # Pre-Market, Regular, After-Hours
    filters: Optional[Dict[str, Any]] = None


class BIScanRequest(BaseModel):
    direction: str = "long"  # long or short
    market_type: str = "stocks"


class TradeReminderRequest(BaseModel):
    ticker: str
    asset_type: str = "crypto"  # crypto or stock
    scanner: str = "early_movers"
    condition: str = "trigger_or_retest"
    duration_hours: float = 6
    channel: str = "email_browser"  # email, browser, email_browser
    row: Optional[Dict[str, Any]] = None


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
    data_source: Optional[str] = None
    data_quality: Optional[Dict[str, Any]] = None
    warnings: Optional[List[str]] = None
    exclusion_policy: Optional[List[str]] = None


# ── Utility Functions ──

# Key mapping: Scanner output → Frontend expected keys
_BI_KEY_MAP = {
    "Ticker": "ticker", "Name": "name", "Preis": "price", "Change%": "change_pct",
    "BI_Score": "score", "BI_MaxScore": "max_score", "BI_Grade": "grade",
    "BI_GradeLabel": "grade_label", "BI_Confidence": "confidence", "BI_Details": "details",
    "Entry": "entry", "StopLoss": "stop_loss", "TP1": "tp1", "TP2": "tp2",
    "RiskReward": "risk_reward", "RVOL": "rvol", "SmartMoney": "smart_money",
    "Volumen": "volume", "AvgVolumen": "avg_volume",
    "MDR_Tag": "mdr_tag", "MDR_Bonus": "mdr_bonus",
}
_BIOTECH_KEY_MAP = {
    "Ticker": "ticker", "Name": "name", "Score": "score", "Grade": "grade",
    "Risk_Flag": "risk_flag", "Catalyst": "catalyst", "Catalyst_Score": "catalyst_score",
    "Pipeline_Score": "pipeline_score", "Readout_Score": "readout_score",
    "Technical_Score": "technical_score", "Risk_Score": "risk_score",
    "Momentum_Score": "momentum_score", "News_Momentum": "news_momentum",
    "RVOL": "rvol", "Preis": "price", "Price": "price",
    "MCap_M": "mcap_m", "Market_Cap": "market_cap", "Shares_M": "shares_m",
    "Chart_Health": "chart_health", "Chart": "chart", "Drawdown": "drawdown",
    "Float_Cat": "float_cat", "Headline": "headline",
    "Catalyst_Date": "catalyst_date", "Catalyst_Keyword": "catalyst_keyword",
    "Readout_Label": "readout_label", "Event_Result": "event_result",
    "BPIQ_Available": "catalyst_data_available", "BPIQ_Catalysts": "catalyst_events",
    "Selloff_Reason": "selloff_reason", "Negative_Flags": "negative_flags",
    "Bio_Edge_Score": "bio_edge_score", "Catalyst_Power": "catalyst_power",
    "Bio_Risk_Penalty": "bio_risk_penalty", "Bio_Trade_Mode": "bio_trade_mode",
    "Bio_Risk_Flags": "bio_risk_flags", "Bio_Positive_Factors": "bio_positive_factors",
    "Dilution_Risk": "dilution_risk", "Regulatory_Risk": "regulatory_risk",
    "Sell_The_News_Risk": "sell_the_news_risk", "Halt_Risk": "halt_risk",
    "Phase3": "phase3", "Phase2": "phase2", "Phase1": "phase1",
    "Active_Trials": "active_trials",
}

def _normalize_keys(results: list, key_map: dict) -> list:
    """Normalize scanner result keys to lowercase frontend-compatible format."""
    normalized = []
    for item in results:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        new_item = {}
        for k, v in item.items():
            new_key = key_map.get(k, k.lower() if k and k[0].isupper() else k)
            new_item[new_key] = v
        normalized.append(new_item)
    return normalized


def _sanitize_public_catalyst_events(events: Any) -> list:
    """Remove upstream/provider-specific field names from public catalyst payloads."""
    if not isinstance(events, list):
        return []
    sanitized = []
    for event in events:
        if not isinstance(event, dict):
            continue
        clean = {}
        for key, value in event.items():
            if key == "source":
                continue
            if key == "bpiq_available":
                continue
            clean_key = "catalyst_score" if key == "bpiq_score" else key
            clean[clean_key] = value
        clean["source"] = "Premium catalyst calendar"
        if "catalyst_score" not in clean and "score" in clean:
            clean["catalyst_score"] = clean.get("score")
        sanitized.append(clean)
    return sanitized


def _sanitize_biotech_public_results(results: list) -> list:
    """Keep Biotech API responses product-owned and provider-neutral."""
    for item in results:
        if not isinstance(item, dict):
            continue
        item.pop("bpiq_available", None)
        item.pop("bpiq_catalysts", None)
        if "catalyst_events" in item:
            item["catalyst_events"] = _sanitize_public_catalyst_events(item.get("catalyst_events"))
        if "readout_details" in item:
            item["readout_details"] = _sanitize_public_catalyst_events(item.get("readout_details"))
    return results


SCAN_DATA_SOURCES = {
    "strategy_scan": "Polygon snapshots + strategy engine",
    "bi_long": "Polygon snapshots + BI scanner",
    "bi_short": "Polygon snapshots + BI scanner",
    "bear": "Polygon gainers/losers + bearish scanner",
    "biotech": "Polygon + biotech catalyst scanner",
    "early_movers": "CoinGecko markets + exchange perp feeds",
    "crash_monitor": "Polygon indices/VIX + market breadth",
    "market_context": "Crash/Fear cache + Polygon news + economic calendar",
    "btc_divergenz": "CoinGecko crypto markets + exchange perp context",
    "money_flow": "Polygon sector ETF bars",
    "new_listing": "Exchange PERP listings + orderbook/safety checks",
    "volume_spikes": "Polygon US stock gainers/losers snapshots",
    "orb": "Polygon intraday bars + official market hours",
    "turtle": "Polygon daily bars + Turtle breakout logic",
}

SCAN_EXCLUSION_POLICIES = {
    "common": [
        "No trade when price/volume data is missing or stale.",
        "No trade when R:R is below strategy minimum or target is already missed.",
        "No trade when liquidity/spread/volume quality is not sufficient.",
    ],
    "orb": [
        "Exclude non-common-stock products, ETFs/ETPs and incomplete opening ranges.",
        "Downgrade or reject chased breakouts far from OR entry.",
        "Require current breakout state and recent volume confirmation.",
    ],
    "early_movers": [
        "Flag partial CoinGecko scans and rate-limit fallbacks.",
        "Separate early accumulation, breakout and overheated/chased phase.",
    ],
    "new_listing": [
        "Reject missed TP zones and unsafe liquidity/orderbook conditions.",
        "Keep waiting_for_history listings eligible for later re-check.",
    ],
    "market_context": [
        "Market weather is context only; it adjusts aggressiveness, not standalone buy/sell signals.",
        "High headline/event risk means smaller size, stronger confirmation and no market chasing.",
    ],
}

RISK_POLICY = {
    "max_loss_per_trade_pct": 1.0,
    "max_loss_per_day_pct": 3.0,
    "preferred_min_rr": 1.5,
    "hard_min_rr": 1.0,
    "min_rvol": 0.7,
    "max_spread_pct_stocks": 2.0,
    "max_spread_pct_crypto": 2.5,
    "block_chased_after_tp1": True,
    "warn_near_high_impact_event_min": 45,
    "policy_note": "Defensive guardrails; not a profit guarantee.",
}


def _effective_scan_result_count(scanner_name: str, results: List[Dict[str, Any]]) -> int:
    """Count user-visible rows, not just top-level cache containers."""
    if scanner_name == "bear":
        total = 0
        for container in results or []:
            if isinstance(container, dict):
                total += len([r for r in container.get("breakdown_stocks", []) or [] if isinstance(r, dict)])
        return total
    if scanner_name == "early_movers":
        total = 0
        for container in results or []:
            if isinstance(container, dict):
                total += len([r for r in container.get("coins", []) or [] if isinstance(r, dict)])
        return total
    if scanner_name == "orb":
        total = 0
        for container in results or []:
            if isinstance(container, dict):
                for key in ("breakouts", "failed_breakouts", "candidates"):
                    total += len([r for r in container.get(key, []) or [] if isinstance(r, dict)])
        return total
    return len(results or [])


def _bear_empty_warning_from_results(results: List[Dict[str, Any]]) -> str:
    """Human-readable reason when the Bear/Short scanner has no stock rows."""
    payload = next((item for item in results or [] if isinstance(item, dict)), {})
    diag = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
    if not diag:
        return "Keine aktuellen Short-Aktien im Cache. Starte einen frischen Short-Scan."

    raw = int(diag.get("raw_candidates", 0) or 0)
    processed = int(diag.get("processed_common_stocks", 0) or 0)
    excluded = int(diag.get("excluded_non_common", 0) or 0)
    no_history = int(diag.get("history_missing", 0) or 0)
    low_volume = int(diag.get("dollar_volume_filtered", 0) or 0)
    weak_drop = int(diag.get("drop_filtered", 0) or 0)
    invalid_price = int(diag.get("price_or_prev_close_filtered", 0) or 0)

    if raw <= 0:
        return "Keine aktuellen Loser vom Marktdaten-Endpoint. Das passiert ausserhalb der US-Session, bei Rate-Limits oder wenn gerade kein verwertbarer Drop vorliegt."
    if excluded >= raw:
        return f"{raw} Kandidaten gefunden, aber alle als ETF/ETP/nicht handelbare Aktienprodukte entfernt."
    if processed <= 0:
        blockers = []
        if invalid_price:
            blockers.append(f"{invalid_price} ohne sauberen Preis/Vortag")
        if low_volume:
            blockers.append(f"{low_volume} mit zu wenig Dollar-Volumen")
        if weak_drop:
            blockers.append(f"{weak_drop} ohne echten Breakdown")
        if no_history:
            blockers.append(f"{no_history} ohne genug Historie")
        detail = ", ".join(blockers) if blockers else "alle durch Sicherheitsfilter entfernt"
        return f"{raw} Kandidaten gefunden, aber keine echte Short-Aktie blieb uebrig: {detail}."
    return "Short-Scan lief, aber nach Common-Stock-, Volumen-, History- und Breakdown-Filtern blieb keine handelbare Aktie uebrig."


def _scan_quality_payload(scanner_name: str, cache_age_seconds: Optional[int], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    interval = _scan_status.get(scanner_name, {}).get("interval_min")
    stale_after = (interval * 60 * 2) if interval else None
    stale = bool(cache_age_seconds is not None and stale_after and cache_age_seconds > stale_after)
    warnings = []
    effective_count = _effective_scan_result_count(scanner_name, results)
    if cache_age_seconds is None:
        warnings.append("Cache-Zeit unbekannt")
    elif stale:
        warnings.append(f"Cache alt: {cache_age_seconds}s")
    if not effective_count:
        warnings.append("Keine Treffer im Cache")
        if scanner_name == "bear":
            warnings.append(_bear_empty_warning_from_results(results))
    diagnostics = None
    if scanner_name == "bear":
        first = next((item for item in results or [] if isinstance(item, dict)), {})
        diagnostics = first.get("diagnostics") if isinstance(first, dict) else None
    return {
        "scanner": scanner_name,
        "data_source": SCAN_DATA_SOURCES.get(scanner_name, "Scanner cache"),
        "cache_age_seconds": cache_age_seconds,
        "cache_status": "unknown" if cache_age_seconds is None else ("stale" if stale else "fresh"),
        "result_count": effective_count,
        "warnings": warnings,
        "exclusion_policy": SCAN_EXCLUSION_POLICIES["common"] + SCAN_EXCLUSION_POLICIES.get(scanner_name, []),
        "market_context": _get_market_context_snapshot().get("summary"),
        "diagnostics": diagnostics,
        "signal_only": scanner_name in _SIGNAL_ONLY_SCANNERS,
        "signal_policy": "Nur echte Trade-Signale; Watch-/Warte-/Kontext-Zeilen werden aus Trading-Listen entfernt." if scanner_name in _SIGNAL_ONLY_SCANNERS else "Kontext-/Statusdaten",
    }


def _attach_trade_health(item: Dict[str, Any], scanner_name: str, market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Attach the central execution-quality payload to one scanner row."""
    health = calculate_trade_health(item, scanner_name=scanner_name, market_context=market_context)
    item["trade_health"] = health
    item["trade_health_score"] = health.get("health_score")
    item["trade_decision"] = health.get("decision")
    item["trade_decision_label"] = health.get("decision_label")
    item["fakeout_risk"] = health.get("fakeout_risk")
    item["chase_risk"] = health.get("chase_risk")
    item["market_context"] = (market_context or {}).get("summary") if market_context else None
    item.setdefault("entry_quality", health.get("entry_quality"))
    item.setdefault("entry_quality_score", health.get("entry_quality_score"))
    return health


def _apply_trade_health_final_signal(item: Dict[str, Any], scanner_name: str) -> None:
    """Make the central Trade Health decision the final user-facing state.

    Scanner-specific logic may mark a setup as JETZT_TRADEN before the global
    health pass recalculates chase/fakeout/entry risk from current price.
    The UI must never show "JETZT TRADEN" when Trade Health says wait/no-trade.
    """
    if scanner_name != "early_movers":
        return
    health = item.get("trade_health") if isinstance(item.get("trade_health"), dict) else {}
    decision = str(item.get("trade_decision") or health.get("decision") or "").upper()
    if not decision or decision == "TRADEABLE":
        return

    label = health.get("decision_label") or item.get("trade_decision_label") or decision
    flags = item.get("risk_flags") if isinstance(item.get("risk_flags"), list) else []
    reasons = item.get("risk_reasons") if isinstance(item.get("risk_reasons"), list) else []

    if decision == "NO_TRADE":
        item["trade_signal"] = "NICHT_TRADEN"
        item["entry_status"] = "NO_TRADE"
        item["trade_action"] = "NO_TRADE"
        item["signal_quality"] = "no_trade_health"
        item["signal_label"] = f"Nicht traden: {label}"
        item["alertable_crypto"] = False
        item["execution_trigger_ok"] = False
        flags.append("trade_health_no_trade")
        reasons.append("Trade Health blockt dieses Setup")
    elif decision == "WAIT_FOR_RETEST":
        item["trade_signal"] = "WARTEN"
        item["entry_status"] = "WAIT_FOR_RETEST"
        item["trade_action"] = "WAIT_FOR_RETEST"
        item["signal_quality"] = "wait_retest"
        item["signal_label"] = f"Warten: {label}"
        item["alertable_crypto"] = False
        flags.append("trade_health_wait_for_retest")
    elif decision == "WAIT_FOR_TRIGGER":
        item["trade_signal"] = "WARTEN"
        item["entry_status"] = "WAIT_FOR_TRIGGER"
        item["trade_action"] = "WAIT_FOR_TRIGGER"
        item["signal_quality"] = "wait_trigger"
        item["signal_label"] = f"Warten: {label}"
        item["alertable_crypto"] = False
        flags.append("trade_health_wait_for_trigger")
    elif decision == "WAIT_FOR_CONTINUATION":
        item["trade_signal"] = "WARTEN"
        item["entry_status"] = "WAIT_FOR_CONTINUATION"
        item["trade_action"] = "WAIT_FOR_CONTINUATION"
        item["signal_quality"] = "wait_continuation"
        item["signal_label"] = f"Warten: {label}"
        item["alertable_crypto"] = False
        flags.append("trade_health_wait_for_continuation")
    elif decision == "WATCH_ONLY":
        item["trade_signal"] = "BEOBACHTEN"
        item["entry_status"] = "BEOBACHTEN"
        item["trade_action"] = "BEOBACHTEN"
        item["signal_quality"] = "observe"
        item["signal_label"] = f"Beobachten: {label}"
        item["alertable_crypto"] = False
        flags.append("trade_health_watch_only")

    item["risk_flags"] = list(dict.fromkeys(flags))
    if reasons:
        item["risk_reasons"] = list(dict.fromkeys(reasons))
    setup = item.get("trade_setup") if isinstance(item.get("trade_setup"), dict) else None
    if setup is not None:
        setup["trade_action"] = item.get("trade_action")
        setup["entry_status"] = item.get("entry_status")
        setup["signal_label"] = item.get("signal_label")
        setup["alertable_crypto"] = False


def _scanner_result_trade_state(scanner_name: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Classify one scanner row for the table itself, not just for email.

    Email cooldowns/dedupes should never make a scanner row look worse. But
    entry timing, fakeout risk, non-stock products, invalid levels and chase
    risk must affect the row that the trader sees.
    """
    state = _classify_alert_candidate(scanner_name, row, time.time())
    display_reasons = [
        reason for reason in (state.get("suppression_reasons") or [])
        if reason not in _DISPLAY_ONLY_SUPPRESSION_REASONS
    ]
    decision = _alert_decision_from_reasons(scanner_name, display_reasons)
    tradeable_now = not display_reasons
    score = _alert_float(state.get("score"), 0) or 0
    trade_grade, trade_grade_label = _score_grade_for_value(score)
    return {
        **state,
        "alertable_now": tradeable_now,
        "display_reasons": display_reasons,
        "trade_score": int(score) if float(score).is_integer() else round(score, 2),
        "trade_grade": trade_grade,
        "trade_grade_label": trade_grade_label,
        **decision,
    }


def _apply_scanner_result_trade_state(item: Dict[str, Any], scanner_name: str) -> None:
    """Make user-facing stock scanner rows reflect tradeability, not raw interest."""
    if scanner_name not in _STOCK_RESULT_TRADE_STATE_SCANNERS:
        return

    ticker = _extract_alert_ticker(item)
    if not ticker:
        return

    raw_score = _alert_float(item.get("score", item.get("Score", item.get("BI_Score"))), None)
    raw_grade = item.get("grade", item.get("Grade", item.get("BI_Grade")))
    state = _scanner_result_trade_state(scanner_name, item)
    trade_score = state["trade_score"]
    trade_grade = state["trade_grade"]
    levels_for_direction = _alert_trade_levels(item)
    direction = str(
        item.get("direction")
        or item.get("Direction")
        or item.get("Signal_Direction")
        or (levels_for_direction.get("direction") if isinstance(levels_for_direction, dict) else "")
        or ""
    ).upper()

    if raw_score is not None:
        item.setdefault("setup_score", round(raw_score, 2))
        item.setdefault("raw_score", round(raw_score, 2))
    if raw_grade:
        item.setdefault("setup_grade", raw_grade)
        item.setdefault("raw_grade", raw_grade)

    item["score"] = trade_score
    item["Score"] = trade_score
    item["grade"] = trade_grade
    item["Grade"] = trade_grade
    item["trade_score"] = trade_score
    item["trade_grade"] = trade_grade
    item["scanner_decision"] = state.get("decision")
    item["scanner_decision_label"] = state.get("decision_label")
    item["scanner_decision_reason"] = state.get("decision_reason")
    item["scanner_suppression_reasons"] = state.get("display_reasons", [])

    if state.get("alertable_now"):
        item["trade_signal"] = "JETZT_TRADEN"
        item["entry_status"] = "JETZT_TRADEN"
        item["trade_action"] = "SHORT_NOW" if direction == "SHORT" else "LONG_NOW"
        item["signal_label"] = "Jetzt traden"
    elif state.get("decision") == "WAIT_RETEST":
        item["trade_signal"] = "WARTEN"
        item["entry_status"] = "WAIT_FOR_RETEST"
        item["trade_action"] = "WAIT_FOR_RETEST"
        item["signal_label"] = "Auf Retest warten"
    elif state.get("decision") == "WAIT_TRIGGER":
        item["trade_signal"] = "WARTEN"
        item["entry_status"] = "WAIT_FOR_TRIGGER"
        item["trade_action"] = "WAIT_FOR_TRIGGER"
        item["signal_label"] = "Trigger fehlt"
    elif state.get("decision") == "NO_TRADE":
        item["trade_signal"] = "NICHT_TRADEN"
        item["entry_status"] = "NO_TRADE"
        item["trade_action"] = "NO_TRADE"
        item["signal_label"] = "Nicht traden"
    else:
        item["trade_signal"] = "BEOBACHTEN"
        item["entry_status"] = "BEOBACHTEN"
        item["trade_action"] = "BEOBACHTEN"
        item["signal_label"] = "Beobachten"


def _decorate_scan_results(results: List[Dict[str, Any]], scanner_name: str, cache_age_seconds: Optional[int]) -> List[Dict[str, Any]]:
    """Add consistent signal explanations and risk warnings to scanner rows."""
    decorated = []
    market_context = _get_market_context_snapshot()
    stock_guard_universe = None
    stock_guard_source = ""
    if scanner_name in STOCK_SCANNER_ASSET_GUARD_NAMES:
        stock_guard_universe, stock_guard_source = _load_common_stock_universe()
    for raw in results or []:
        if not isinstance(raw, dict):
            decorated.append(raw)
            continue
        item = dict(raw)
        why = []
        warnings = []

        ticker_for_guard = str(item.get("Ticker") or item.get("ticker") or item.get("symbol") or "").upper().strip()
        if ticker_for_guard and scanner_name in STOCK_SCANNER_ASSET_GUARD_NAMES:
            exclusion_reason = _stock_alert_asset_exclusion_reason(
                ticker_for_guard,
                common_stock_universe=stock_guard_universe,
                universe_source=stock_guard_source,
                require_reference=stock_guard_universe is None,
            )
            if exclusion_reason:
                continue

        score = item.get("score", item.get("Score", item.get("BI_Score", item.get("exhaustion_score", item.get("fear_score")))))
        grade = item.get("grade", item.get("Grade", item.get("BI_Grade")))
        direction = item.get("direction", item.get("Direction", item.get("signal", item.get("Signal"))))
        rvol = item.get("rvol", item.get("RVOL"))
        rr = item.get("risk_reward", item.get("RiskReward", item.get("rr_effective", item.get("rr1"))))
        source = item.get("source") or item.get("data_source") or SCAN_DATA_SOURCES.get(scanner_name, "Scanner cache")

        numeric_score = _alert_float(score)
        if scanner_name == "turtle" and numeric_score is not None:
            capped_score, turtle_flags = _turtle_score_cap(
                numeric_score,
                item.get("Change_Pct", item.get("change_pct")),
                rvol,
                item.get("Breakout_Pct", item.get("breakout_pct")),
            )
            if capped_score < numeric_score:
                item["raw_score"] = round(numeric_score, 2)
                item["score"] = round(capped_score, 2)
                score = item["score"]
                warnings.append("Turtle-Score gedeckelt: " + ", ".join(turtle_flags))
            else:
                item.setdefault("score", round(numeric_score, 2))
            grade = _strategy_score_to_grade(float(item.get("score", numeric_score)))
            item["grade"] = grade
        elif numeric_score is not None:
            item.setdefault("score", round(numeric_score, 2))
            if not grade:
                grade = _strategy_score_to_grade(numeric_score)
                item["grade"] = grade

        if scanner_name in _STOCK_RESULT_TRADE_STATE_SCANNERS:
            raw_quality_parts = []
            if grade:
                raw_quality_parts.append(f"Grade {grade}")
            if score is not None:
                raw_quality_parts.append(f"Score {score}")
            if raw_quality_parts:
                why.append("Setup-Rohwert: " + " / ".join(raw_quality_parts))
        else:
            if grade:
                why.append(f"Grade {grade}")
            if score is not None:
                why.append(f"Score {score}")
        if direction:
            why.append(f"Signal/Richtung: {direction}")
        if rvol is not None:
            why.append(f"RVOL {rvol}")
            try:
                if float(rvol) < 0.7:
                    warnings.append("RVOL niedrig - Signal vorsichtig behandeln")
            except Exception:
                pass
        if rr is not None:
            why.append(f"R:R {rr}")
            try:
                if float(rr) < 1.5:
                    warnings.append("R:R unter defensivem Mindestbereich")
            except Exception:
                pass
        if item.get("tp1_missed"):
            warnings.append("TP1 bereits verpasst")
        if item.get("tp2_missed"):
            warnings.append("TP-Zonen bereits verpasst - No-Trade-Kandidat")
        if item.get("partial_data") or item.get("data_warning"):
            warnings.append(str(item.get("data_warning") or "Unvollstaendige Daten"))
        if cache_age_seconds is None:
            warnings.append("Cache-Alter unbekannt")

        health = _attach_trade_health(item, scanner_name, market_context)
        why.append(f"Trade Health: {health.get('decision_label')} ({health.get('health_score')}/100)")
        why.append(f"Fakeout-Risiko: {health.get('fakeout_risk')} | Chase-Risiko: {health.get('chase_risk')}")
        warnings.extend(health.get("warnings") or [])
        exclusion_reasons = health.get("exclusion_reasons") or []
        _apply_scanner_result_trade_state(item, scanner_name)
        _apply_trade_health_final_signal(item, scanner_name)
        if item.get("scanner_decision_label"):
            why.append(f"Scanner-Aktion: {item.get('scanner_decision_label')} ({item.get('trade_score')}/100)")
        if item.get("scanner_suppression_reasons"):
            exclusion_reasons = list(dict.fromkeys([*exclusion_reasons, *item.get("scanner_suppression_reasons", [])]))

        item["_quality"] = {
            "why_in": why or ["Scanner-Regeln erfuellt, aber keine Detailgruende geliefert"],
            "warnings": list(dict.fromkeys(warnings)),
            "data_source": source,
            "data_age_seconds": cache_age_seconds,
            "exclusion_reasons": exclusion_reasons,
            "risk_policy": {
                "min_rr_preferred": RISK_POLICY["preferred_min_rr"],
                "min_rvol_preferred": RISK_POLICY["min_rvol"],
                "chased_targets_blocked": True,
            },
            "market_context": market_context.get("summary"),
        }
        decorated.append(item)
    return decorated


def _early_mover_visible_candidate(row: Dict[str, Any]) -> bool:
    """Show elite crypto trigger candidates without turning the tab into a watchlist.

    This is deliberately stricter than "interesting coin" and looser than
    "send a trade mail": the UI should not be empty when a coin is high quality
    and close to a 5m trigger, but mails still require confirmed execution.
    """
    fields = _extract_early_mover_fields(row)
    action = fields["trade_action"]
    signal = str(row.get("trade_signal", "") or "").upper()
    signal_quality = fields["signal_quality"]
    flags = set(fields["risk_flags"])
    grade = _extract_alert_grade(row)
    decision = str(row.get("trade_decision") or (row.get("trade_health") or {}).get("decision") or "").upper()
    setup_score = int(_alert_float(row.get("setup_score", row.get("score")), 0) or 0)
    entry_score_value = _alert_float(row.get("entry_score"), None)
    entry_score = int(entry_score_value if entry_score_value is not None else _early_mover_entry_score(row))
    explosion_score = int(_alert_float(row.get("explosion_score"), None) if _alert_float(row.get("explosion_score"), None) is not None else _early_mover_explosion_score(row))
    risk_level = str(row.get("risk_level", "") or "").upper()

    if decision == "NO_TRADE":
        return False
    if action not in ("LONG_TRIGGER", "WAIT_FOR_RETEST"):
        return False
    if setup_score < _EARLY_MOVER_VISIBLE_MIN_SETUP_SCORE and grade not in _ALERT_TOP_GRADES and explosion_score < _ALERT_MIN_SCORE:
        return False
    if signal in {"NICHT_TRADEN", "BEOBACHTEN"} and signal_quality == "observe":
        return False
    if signal_quality == "no_chase" or risk_level == "HIGH":
        return False
    execution_score = _alert_float(row.get("execution_quality_score"), None)
    if signal == "JETZT_TRADEN":
        if entry_score < _EARLY_MOVER_VISIBLE_MIN_ENTRY_SCORE and (execution_score is None or execution_score < _EARLY_MOVER_MIN_ARMED_PREBREAKOUT_SCORE):
            return False
    elif signal == "EXPLOSION_ARMED" or row.get("pre_breakout_armed"):
        if explosion_score < _ALERT_MIN_SCORE:
            return False
    elif grade and grade not in _ALERT_TOP_GRADES:
        return False
    elif setup_score < _EARLY_MOVER_VISIBLE_MIN_SETUP_SCORE:
        return False
    elif entry_score < _EARLY_MOVER_VISIBLE_MIN_ENTRY_SCORE and explosion_score < _ALERT_MIN_SCORE:
        return False

    if not _early_mover_btc_allows_long(fields):
        return False
    if fields["live_rr"] < _EARLY_MOVER_VISIBLE_MIN_LIVE_RR:
        return False
    if fields["distance_to_entry_r"] > _EARLY_MOVER_VISIBLE_MAX_DISTANCE_R:
        return False

    hard_flags = {
        "overheated_phase3",
        "partial_crypto_data",
        "data_warning",
        "tp1_already_reached",
        "chased_from_entry",
        "very_high_volume_turnover",
        "turnover_without_alpha",
        "extreme_turnover_churn",
        "thin_perp_liquidity",
        "thin_orderbook",
        "market_impact_risk",
        "no_perp_execution_market",
    }
    return not bool(flags.intersection(hard_flags))


def _early_mover_visible_sort_key(row: Dict[str, Any]) -> tuple:
    signal = str(row.get("trade_signal", "") or "").upper()
    action = str(row.get("trade_action", "") or "").upper()
    signal_rank = 0 if signal == "JETZT_TRADEN" else 1 if signal == "EXPLOSION_ARMED" or row.get("pre_breakout_armed") else 2
    action_rank = 0 if action == "LONG_TRIGGER" else 1 if action == "WAIT_FOR_RETEST" else 3
    return (
        signal_rank,
        action_rank,
        -int(_alert_float(row.get("explosion_score"), 0) or 0),
        -int(_alert_float(row.get("pre_breakout_score"), 0) or 0),
        -int(_alert_float(row.get("entry_score"), 0) or 0),
        -int(_alert_float(row.get("setup_score", row.get("score")), 0) or 0),
    )


def _scanner_row_is_trade_signal(row: Dict[str, Any], scanner_name: str) -> bool:
    """True for rows that should survive the user-facing scanner filter.

    For most signal-only scanners this means an actual trade signal. Early
    Movers are intentionally split: the tab should show elite scored candidates,
    while the mail gate remains stricter and only sends confirmed entries.
    """
    if not isinstance(row, dict):
        return False

    action = str(row.get("trade_action") or row.get("action") or "").upper()
    signal = str(row.get("trade_signal") or row.get("signal") or row.get("Signal") or "").upper()
    decision = str(row.get("trade_decision") or (row.get("trade_health") or {}).get("decision") or "").upper()
    entry_status = str(row.get("entry_status") or "").upper()
    signal_quality = str(row.get("signal_quality") or "").lower()
    trade_category = str(row.get("trade_category") or "").upper()

    explicit_trade = (
        signal == "JETZT_TRADEN"
        or action in {"SHORT_NOW", "LONG_NOW", "TRADE_NOW"}
        or bool(row.get("alertable_crypto"))
    )
    armed_trade_setup = bool(
        scanner_name == "early_movers"
        and (
            row.get("pre_breakout_armed")
            or signal == "EXPLOSION_ARMED"
            or entry_status == "PRE_BREAKOUT_ARMED"
            or signal_quality == "pre_breakout_armed"
        )
    )
    wait_or_watch = (
        "WATCH" in signal
        or "WATCH" in action
        or "BEOBACHTEN" in signal
        or "BEOBACHTEN" in action
        or signal in {"WARTEN", "WAIT"}
        or action.startswith("WAIT_FOR_")
        or entry_status.startswith("WAIT_FOR_")
        or entry_status in {"WARTEN", "BEOBACHTEN"}
        or signal_quality in {"observe", "wait", "wait_retest", "wait_trigger", "watch_or_blocked"}
        or trade_category.endswith("_WATCH")
        or trade_category in {"ANNOUNCEMENT_WATCH", "PUMP_RUNNING_WATCH", "ACTIVE_PUMP_WATCH", "EXHAUSTION_WATCH"}
    )

    if scanner_name == "early_movers":
        return _early_mover_visible_candidate(row)

    if scanner_name in _CRYPTO_SIGNAL_ONLY_SCANNERS:
        return bool(explicit_trade and not wait_or_watch and _row_has_alert_quality(row, require_top_grade=False))

    if wait_or_watch and not explicit_trade:
        return False

    if explicit_trade:
        return _row_has_alert_quality(row, require_top_grade=False)

    if decision == "TRADEABLE":
        return _row_has_alert_quality(row, require_top_grade=True)

    return False


def _row_has_alert_quality(row: Dict[str, Any], require_top_grade: bool = True) -> bool:
    """Keep weak/C-grade rows out of signal-only scanner lists."""
    grade = _extract_alert_grade(row)
    score = _alert_float(_extract_alert_score(row), None)
    if grade and require_top_grade and grade not in _ALERT_TOP_GRADES:
        return False
    if score is not None and score < _ALERT_MIN_SCORE:
        return False
    return True


def _filter_signal_rows(rows: List[Dict[str, Any]], scanner_name: str) -> Tuple[List[Dict[str, Any]], int]:
    kept: List[Dict[str, Any]] = []
    suppressed = 0
    for row in rows or []:
        if isinstance(row, dict) and _scanner_row_is_trade_signal(row, scanner_name):
            kept.append(row)
        else:
            suppressed += 1
    return kept, suppressed


def _apply_signal_only_policy(scanner_name: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove watchlist/wait/context rows from user-facing scanner results."""
    if scanner_name not in _SIGNAL_ONLY_SCANNERS:
        return results

    container_keys = {
        "bear": ("breakdown_stocks",),
        "orb": ("breakouts", "failed_breakouts", "candidates"),
        "early_movers": ("coins",),
    }.get(scanner_name)

    if container_keys:
        filtered_payloads: List[Dict[str, Any]] = []
        for payload in results or []:
            if not isinstance(payload, dict):
                continue
            payload = dict(payload)
            stats = dict(payload.get("stats") or {})
            total_suppressed = 0
            total_visible = 0
            for key in container_keys:
                rows = payload.get(key)
                if isinstance(rows, list):
                    visible, suppressed = _filter_signal_rows(rows, scanner_name)
                    visible_before_limit = len(visible)
                    if scanner_name == "early_movers":
                        visible = sorted(visible, key=_early_mover_visible_sort_key)[:_EARLY_MOVER_VISIBLE_LIMIT]
                    payload[key] = visible
                    stats[f"{key}_raw_count"] = len(rows)
                    stats[f"{key}_suppressed_watch_rows"] = suppressed
                    if scanner_name == "early_movers":
                        stats[f"{key}_visible_before_limit"] = visible_before_limit
                        stats[f"{key}_trimmed_signal_rows"] = max(0, visible_before_limit - len(visible))
                    total_suppressed += suppressed
                    total_visible += len(visible)
            stats["signal_only"] = True
            stats["visible_trade_signals"] = total_visible
            stats["suppressed_watch_rows"] = total_suppressed
            if scanner_name == "early_movers":
                coins = payload.get("coins") if isinstance(payload.get("coins"), list) else []
                stats["visible_candidates"] = total_visible
                stats["confirmed_trade_signals"] = sum(
                    1 for c in coins
                    if isinstance(c, dict) and str(c.get("trade_signal", "")).upper() == "JETZT_TRADEN"
                )
                stats["unified_count"] = len(coins)
                stats["phase_1_count"] = sum(1 for c in coins if isinstance(c, dict) and c.get("phase") == 1)
                stats["phase_2_count"] = sum(1 for c in coins if isinstance(c, dict) and c.get("phase") == 2)
                stats["phase_3_count"] = sum(1 for c in coins if isinstance(c, dict) and c.get("phase") == 3)
                stats["trade_now_count"] = sum(
                    1 for c in coins
                    if isinstance(c, dict) and str(c.get("trade_signal", "")).upper() == "JETZT_TRADEN"
                )
                stats["explosion_armed_count"] = sum(
                    1 for c in coins
                    if isinstance(c, dict) and (
                        c.get("pre_breakout_armed")
                        or str(c.get("trade_signal", "")).upper() == "EXPLOSION_ARMED"
                    )
                )
            payload["stats"] = stats
            filtered_payloads.append(payload)
        return filtered_payloads

    visible, suppressed = _filter_signal_rows(results, scanner_name)
    for row in visible:
        if isinstance(row, dict):
            quality = row.setdefault("_quality", {})
            quality["signal_only"] = True
            quality["suppressed_watch_rows"] = suppressed
    return visible


def _decorate_orb_results(results: List[Dict[str, Any]], cache_age_seconds: Optional[int]) -> List[Dict[str, Any]]:
    """Decorate ORB container rows and the nested breakout/candidate rows."""
    def _orb_trade_rank(row: Dict[str, Any]) -> tuple:
        health = row.get("trade_health") or {}
        decision = str(row.get("trade_decision") or health.get("decision") or "").upper()
        entry_quality = str(row.get("entry_quality") or health.get("entry_quality") or "").upper()
        decision_rank = {
            "TRADEABLE": 0,
            "WAIT_FOR_RETEST": 1,
            "WAIT_FOR_CONTINUATION": 2,
            "WAIT_FOR_TRIGGER": 3,
            "WATCH_ONLY": 4,
            "NO_TRADE": 9,
        }.get(decision, 6)
        entry_rank = {
            "GOOD": 0,
            "EXTENDED": 1,
            "LATE": 2,
            "CHASE": 4,
        }.get(entry_quality, 3)
        late_rank = 1 if row.get("late_to_tp1") else 0
        score = _alert_float(row.get("score"), 0) or 0
        health_score = _alert_float(row.get("trade_health_score") or health.get("health_score"), 0) or 0
        live_rr = _alert_float(row.get("live_rr_ratio") or health.get("metrics", {}).get("live_rr"), 0) or 0
        distance_r = _alert_float(row.get("distance_to_entry_r") or health.get("metrics", {}).get("distance_to_entry_r"), 999) or 999
        return (decision_rank, entry_rank, late_rank, -health_score, -score, -live_rr, distance_r)

    decorated = _decorate_scan_results(results, "orb", cache_age_seconds)
    for payload in decorated:
        if not isinstance(payload, dict):
            continue
        for list_key in ("breakouts", "failed_breakouts", "candidates"):
            rows = payload.get(list_key)
            if isinstance(rows, list):
                decorated_rows = _decorate_scan_results(rows, "orb", cache_age_seconds)
                if list_key == "breakouts":
                    decorated_rows = sorted(decorated_rows, key=_orb_trade_rank)
                    payload["breakout_decision_counts"] = {
                        "tradeable": sum(1 for row in decorated_rows if str(row.get("trade_decision") or "").upper() == "TRADEABLE"),
                        "wait": sum(1 for row in decorated_rows if str(row.get("trade_decision") or "").upper() in {"WAIT_FOR_RETEST", "WAIT_FOR_CONTINUATION", "WAIT_FOR_TRIGGER", "WATCH_ONLY"}),
                        "no_trade": sum(1 for row in decorated_rows if str(row.get("trade_decision") or "").upper() == "NO_TRADE"),
                    }
                payload[list_key] = decorated_rows
    return _apply_signal_only_policy("orb", decorated)


def _decorate_early_mover_results(results: List[Dict[str, Any]], cache_age_seconds: Optional[int]) -> List[Dict[str, Any]]:
    """Decorate Early Mover container rows and the nested crypto coin rows."""
    decorated = _decorate_scan_results(results, "early_movers", cache_age_seconds)
    for payload in decorated:
        if not isinstance(payload, dict):
            continue
        rows = payload.get("coins")
        if isinstance(rows, list):
            payload["coins"] = _decorate_scan_results(rows, "early_movers", cache_age_seconds)
    return _apply_signal_only_policy("early_movers", decorated)


def load_cache_file(filepath: str, max_age_hours: int = 2) -> tuple[List[Dict], Optional[str]]:
    """Load cache file and return (data, cached_at_timestamp) (thread-safe).

    Supports two cache formats:
    - New format: {"cached_at": "ISO-string", "results": [...]}
    - Scanner format: {"timestamp": unix_epoch_float, "results": [...]}
    """
    with _cache_lock:
        if not Path(filepath).exists():
            return [], None

        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            cached_at = None
            if isinstance(data, dict):
                # Try new format first (ISO string)
                cached_at = data.get("cached_at")
                # Fall back to scanner format (Unix epoch float)
                if not cached_at and "timestamp" in data:
                    try:
                        ts = data["timestamp"]
                        if isinstance(ts, (int, float)) and ts > 1000000000:
                            cached_at = datetime.fromtimestamp(ts).isoformat()
                    except Exception as e:
                        print(f"Error parsing cache timestamp from {filepath}: {e}")
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
    """Save cache file with timestamp (thread-safe)."""
    with _cache_lock:
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


def get_public_strategies_for_market(market_type: str, include_hidden: bool = False) -> Dict[str, Any]:
    """Return the curated strategy menu that should be visible in the UI."""
    strategies = get_strategies_for_market(market_type)
    if market_type != "stocks":
        return strategies

    public_strategies = {
        name: deepcopy(STRATEGIES[name])
        for name in STOCK_STRATEGY_ORDER
        if name in STRATEGIES
    }

    if include_hidden:
        for name in sorted(STOCK_STRATEGY_HIDDEN):
            if name in STRATEGIES:
                public_strategies[name] = deepcopy(STRATEGIES[name])

    return public_strategies


def resolve_strategy_name(strategy_name: str, market_type: str = "stocks") -> str:
    """Map legacy/duplicate stock strategy names onto the curated canonical set."""
    if market_type != "stocks":
        return strategy_name
    return STOCK_STRATEGY_LOOKUP.get(_normalize_strategy_key(strategy_name), strategy_name)


def _get_ma_profiles(strat: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return one or more normalized MA profiles for bounce strategies."""
    profiles = strat.get("ma_profiles")
    if profiles:
        normalized_profiles = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            normalized_profiles.append({
                "ma_type": str(profile.get("ma_type", "SMA") or "SMA").upper(),
                "ma_period": int(profile.get("ma_period", 50) or 50),
                "ma_approach": str(profile.get("ma_approach", "from_above") or "from_above"),
                "ma_distance_max": float(profile.get("ma_distance_max", 3.0) or 3.0),
            })
        if normalized_profiles:
            return normalized_profiles

    return [{
        "ma_type": str(strat.get("ma_type", "SMA") or "SMA").upper(),
        "ma_period": int(strat.get("ma_period", 50) or 50),
        "ma_approach": str(strat.get("ma_approach", "from_above") or "from_above"),
        "ma_distance_max": float(strat.get("ma_distance_max", 3.0) or 3.0),
    }]


def _get_max_ma_period(strat: Dict[str, Any]) -> int:
    """Highest lookback needed for merged MA bounce strategies."""
    periods = [profile.get("ma_period", 50) for profile in _get_ma_profiles(strat)]
    return max(int(period or 50) for period in periods) if periods else 50


def _strategy_score_to_grade(score: float) -> str:
    """Shared grade ladder for generic strategy scans."""
    if score >= 80:
        return "S"
    if score >= 65:
        return "A"
    if score >= 45:
        return "B"
    if score >= 30:
        return "C"
    return "D"


def _clamp_float(value: Any, low: float, high: float, default: float = 0.0) -> float:
    """Clamp numeric values from API payloads without letting NaN/None leak into scoring."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = default
    if math.isnan(val) or math.isinf(val):
        val = default
    return max(low, min(high, val))


def _round_trade_price(price: float) -> float:
    if price >= 1:
        return round(price, 2)
    if price >= 0.1:
        return round(price, 3)
    return round(price, 5)


def _round_level_price(price: float) -> float:
    """Round chart levels without destroying small crypto prices."""
    try:
        price = float(price)
    except (TypeError, ValueError):
        return 0.0
    if abs(price) >= 10:
        return round(price, 2)
    if abs(price) >= 1:
        return round(price, 3)
    if abs(price) >= 0.01:
        return round(price, 5)
    return round(price, 8)


def _normalize_chart_direction(value: Any) -> str:
    """Normalize scanner/chart direction into long/short for directional overlays."""
    text = str(value or "").upper()
    if any(token in text for token in ("SHORT", "BEAR", "SELL", "PUT", "DOWN")):
        return "short"
    if any(token in text for token in ("LONG", "BULL", "BUY", "CALL", "UP")):
        return "long"
    return ""


def _fib_lookback_for_timeframe(timeframe: str) -> int:
    tf = str(timeframe or "1D")
    if tf in ("5m", "15m"):
        return 80
    if tf == "1H":
        return 100
    if tf == "4H":
        return 120
    if tf == "1W":
        return 52
    return 60


def _calculate_directional_fib_levels(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    timeframe: str = "1D",
    direction: Any = None,
    lookback: Optional[int] = None,
) -> Dict[str, Any]:
    """Return Fibonacci retracements/extensions from the selected timeframe and setup direction.

    Long: pullback levels are below the swing high, extensions above it.
    Short: pullback levels are above the swing low, extensions below it.
    Input series must be chronological (oldest -> newest).
    """
    clean = []
    for h, l, c in zip(highs or [], lows or [], closes or []):
        h_val = _alert_float(h)
        l_val = _alert_float(l)
        c_val = _alert_float(c)
        if h_val is None or l_val is None or c_val is None:
            continue
        clean.append((h_val, l_val, c_val))
    if len(clean) < 3:
        return {}

    lb = min(int(lookback or _fib_lookback_for_timeframe(timeframe)), len(clean))
    recent = clean[-lb:]
    period_high = max(row[0] for row in recent)
    period_low = min(row[1] for row in recent)
    rng = period_high - period_low
    if rng <= 0:
        return {}

    dir_norm = _normalize_chart_direction(direction)
    if not dir_norm:
        first_close = recent[0][2]
        last_close = recent[-1][2]
        mid = period_low + rng * 0.5
        dir_norm = "long" if (last_close >= first_close or last_close >= mid) else "short"

    retracements = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    levels: Dict[str, float] = {}
    if dir_norm == "short":
        for ratio in retracements:
            levels[f"{int(ratio * 100)}%"] = _round_level_price(period_low + rng * ratio)
        levels["127%"] = _round_level_price(period_low - rng * 0.272)
        levels["161%"] = _round_level_price(period_low - rng * 0.618)
        levels["200%"] = _round_level_price(period_low - rng)
    else:
        for ratio in retracements:
            levels[f"{int(ratio * 100)}%"] = _round_level_price(period_high - rng * ratio)
        levels["127%"] = _round_level_price(period_high + rng * 0.272)
        levels["161%"] = _round_level_price(period_high + rng * 0.618)
        levels["200%"] = _round_level_price(period_high + rng)

    return {
        "levels": levels,
        "meta": {
            "direction": dir_norm,
            "timeframe": str(timeframe or "1D"),
            "lookback_bars": lb,
            "anchor_high": _round_level_price(period_high),
            "anchor_low": _round_level_price(period_low),
            "model": "directional_retracement_v2",
            "basis": "selected_chart_timeframe" if str(timeframe or "1D") != "1D" else "daily_detail_timeframe",
        },
    }


def _turtle_score_cap(score: float, change_pct: Any, rvol: Any, breakout_pct: Any) -> tuple[float, List[str]]:
    """Cap Turtle score when the Donchian breakout lacks live confirmation."""
    capped = float(score or 0)
    flags: List[str] = []
    change = _alert_float(change_pct, 0.0) or 0.0
    rel_vol = _alert_float(rvol, 1.0) or 1.0
    breakout = _alert_float(breakout_pct, 0.0) or 0.0

    if rel_vol < 1.0:
        capped = min(capped, 69)
        flags.append("RVOL unter 1.0x")
    elif rel_vol < 1.3:
        capped = min(capped, 79)
        flags.append("RVOL nur leicht bestaetigt")

    if change < 0.75 and rel_vol < 1.5:
        capped = min(capped, 79)
        flags.append("Tagesmomentum noch schwach")

    if breakout > 5.0:
        capped = min(capped, 74)
        flags.append("Breakout bereits weit weg vom Trigger")

    return max(0, min(100, capped)), flags


def _build_structured_trade_setup(
    direction: str,
    entry: float,
    atr: Optional[float],
    support_1: Optional[float],
    resistance_1: Optional[float],
    high_20d: Optional[float],
    low_20d: Optional[float],
    range_pos: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Build realistic sidebar trade levels from invalidation and target structure.

    The stop is placed behind a real invalidation level first. R:R is only used
    afterwards as a quality filter, not as the reason for the stop/target.
    """
    try:
        side = str(direction or "").upper()
        entry = float(entry or 0)
    except (TypeError, ValueError):
        return None
    if side not in ("LONG", "SHORT") or entry <= 0:
        return None

    atr_value = float(atr or 0)
    if atr_value <= 0:
        atr_value = entry * 0.03

    min_risk = max(entry * 0.015, atr_value * 0.45)
    buffer = max(entry * 0.003, atr_value * 0.10)
    warnings: List[str] = []
    notes: List[str] = []

    support = float(support_1 or 0)
    resistance = float(resistance_1 or 0)
    hi20 = float(high_20d or 0)
    lo20 = float(low_20d or 0)
    range_size = hi20 - lo20 if hi20 > lo20 else 0.0

    def _unique_levels(levels: List[tuple[float, str]], reverse: bool = False) -> List[tuple[float, str]]:
        seen: Dict[float, str] = {}
        for price, label in levels:
            if price and price > 0:
                seen.setdefault(round(float(price), 6), label)
        return sorted(seen.items(), reverse=reverse)

    def _select_long_target(
        candidates: List[tuple[float, str]],
        min_reward: float,
        fallback: float,
        fallback_label: str,
    ) -> tuple[float, str]:
        valid = _unique_levels([(p, l) for p, l in candidates if p > entry])
        structural = [(p, l) for p, l in valid if "fallback" not in l.lower() and "atr" not in l.lower()]
        synthetic = [(p, l) for p, l in valid if (p, l) not in structural]
        for group in (structural, synthetic):
            for price, label in group:
                if price - entry >= min_reward:
                    return price, label
        return fallback, fallback_label

    def _select_short_target(
        candidates: List[tuple[float, str]],
        min_reward: float,
        fallback: float,
        fallback_label: str,
    ) -> tuple[float, str]:
        valid = _unique_levels([(p, l) for p, l in candidates if 0 < p < entry], reverse=True)
        structural = [(p, l) for p, l in valid if "fallback" not in l.lower() and "atr" not in l.lower()]
        synthetic = [(p, l) for p, l in valid if (p, l) not in structural]
        for group in (structural, synthetic):
            for price, label in group:
                if entry - price >= min_reward:
                    return price, label
        return max(0.01, fallback), fallback_label

    if side == "LONG":
        stop_candidates: List[tuple[float, str]] = []
        if 0 < support < entry:
            stop_candidates.append((support - buffer, "S1 invalidation"))
        if 0 < lo20 < entry:
            stop_candidates.append((lo20 - buffer, "20D low invalidation"))
        stop_candidates.append((entry - max(atr_value * 1.2, entry * 0.03), "ATR invalidation fallback"))

        selected_stop = None
        stop_source = "ATR invalidation fallback"
        for price, label in _unique_levels(stop_candidates, reverse=True):
            if entry - price >= min_risk:
                selected_stop = price
                stop_source = label
                break
        stop = selected_stop if selected_stop is not None else entry - min_risk
        risk = entry - stop
        if risk <= 0:
            return None

        candidates: List[tuple[float, str]] = []
        if resistance > entry:
            candidates.append((resistance, "R1"))
        if hi20 > entry:
            candidates.append((hi20, "20D High"))
        if range_size > 0:
            candidates.extend([
                (hi20 + range_size * 0.272, "127% range extension"),
                (hi20 + range_size * 0.382, "138% range extension"),
                (hi20 + range_size * 0.618, "161% range extension"),
            ])
        candidates.extend([
            (entry + atr_value * 2.0, "2 ATR"),
            (entry + atr_value * 3.5, "3.5 ATR"),
            (entry + risk * 1.5, "measured move 1.5R fallback"),
            (entry + risk * 2.5, "measured move 2.5R fallback"),
        ])

        near_barriers = [
            label for price, label in candidates
            if price > entry and (price - entry) < risk * 1.25
        ]
        if near_barriers:
            warnings.append(f"Nahe Resistance ({', '.join(dict.fromkeys(near_barriers))}) - TP nicht zu eng setzen")

        min_tp1 = entry + max(risk * 1.35, atr_value * 1.10, entry * 0.03)
        min_tp2 = entry + max(risk * 2.25, atr_value * 2.20, entry * 0.055)
        if range_size > 0 and hi20 > entry and (hi20 - entry) < risk * 1.25:
            min_tp1 = max(min_tp1, hi20 + range_size * 0.272)
            min_tp2 = max(min_tp2, hi20 + range_size * 0.618)
        tp1, tp1_source = _select_long_target(candidates, min_tp1 - entry, min_tp1, "measured move fallback")
        tp2, tp2_source = _select_long_target(
            [(p, l) for p, l in candidates if p > tp1 + risk * 0.25],
            min_tp2 - entry,
            min_tp2,
            "measured move fallback",
        )
        if tp2 <= tp1:
            tp2 = tp1 + max(risk, atr_value, entry * 0.03)
            tp2_source = "measured move fallback"
    else:
        stop_candidates = []
        if resistance > entry:
            stop_candidates.append((resistance + buffer, "R1 invalidation"))
        if hi20 > entry:
            stop_candidates.append((hi20 + buffer, "20D high invalidation"))
        stop_candidates.append((entry + max(atr_value * 1.2, entry * 0.03), "ATR invalidation fallback"))

        selected_stop = None
        stop_source = "ATR invalidation fallback"
        for price, label in _unique_levels(stop_candidates):
            if price - entry >= min_risk:
                selected_stop = price
                stop_source = label
                break
        stop = selected_stop if selected_stop is not None else entry + min_risk
        risk = stop - entry
        if risk <= 0:
            return None

        candidates = []
        if support > 0 and support < entry:
            candidates.append((support, "S1"))
        if lo20 > 0 and lo20 < entry:
            candidates.append((lo20, "20D Low"))
        if range_size > 0:
            candidates.extend([
                (lo20 - range_size * 0.272, "127% range extension"),
                (lo20 - range_size * 0.382, "138% range extension"),
                (lo20 - range_size * 0.618, "161% range extension"),
            ])
        candidates.extend([
            (entry - atr_value * 2.0, "2 ATR"),
            (entry - atr_value * 3.5, "3.5 ATR"),
            (entry - risk * 1.5, "measured move 1.5R fallback"),
            (entry - risk * 2.5, "measured move 2.5R fallback"),
        ])

        near_barriers = [
            label for price, label in candidates
            if price < entry and (entry - price) < risk * 1.25
        ]
        if near_barriers:
            warnings.append(f"Nahe Support-Zone ({', '.join(dict.fromkeys(near_barriers))}) - TP nicht zu eng setzen")

        min_tp1 = entry - max(risk * 1.35, atr_value * 1.10, entry * 0.03)
        min_tp2 = entry - max(risk * 2.25, atr_value * 2.20, entry * 0.055)
        if range_size > 0 and 0 < lo20 < entry and (entry - lo20) < risk * 1.25:
            min_tp1 = min(min_tp1, lo20 - range_size * 0.272)
            min_tp2 = min(min_tp2, lo20 - range_size * 0.618)
        tp1, tp1_source = _select_short_target(candidates, entry - min_tp1, min_tp1, "measured move fallback")
        tp2, tp2_source = _select_short_target(
            [(p, l) for p, l in candidates if p < tp1 - risk * 0.25],
            entry - min_tp2,
            min_tp2,
            "measured move fallback",
        )
        if tp2 >= tp1:
            tp2 = max(0.01, tp1 - max(risk, atr_value, entry * 0.03))
            tp2_source = "measured move fallback"

    reward1 = abs(tp1 - entry)
    reward2 = abs(tp2 - entry)
    rr_tp1 = reward1 / risk if risk > 0 else 0
    rr_tp2 = reward2 / risk if risk > 0 else 0
    blended_rr = (rr_tp1 + rr_tp2) / 2 if rr_tp2 > 0 else rr_tp1

    if range_pos is not None:
        try:
            rp = float(range_pos)
            if side == "LONG" and rp >= 70:
                notes.append("Entry ist hoch in der 20D-Range; ideal ist Breakout/Retest statt Blind-Chase")
            elif side == "SHORT" and rp <= 30:
                notes.append("Entry ist tief in der 20D-Range; ideal ist Breakdown/Retest statt Blind-Chase")
        except (TypeError, ValueError):
            pass

    return {
        "entry": _round_trade_price(entry),
        "stop": _round_trade_price(stop),
        "tp1": _round_trade_price(tp1),
        "tp2": _round_trade_price(tp2),
        "rr": round(blended_rr, 2),
        "rr_tp1": round(rr_tp1, 2),
        "rr_tp2": round(rr_tp2, 2),
        "risk": _round_trade_price(risk),
        "model": "Struktur-Invalidation + Zielzonen; R:R nur Filter",
        "level_model": "structure_first_v2",
        "stop_source": stop_source,
        "tp1_source": tp1_source,
        "tp2_source": tp2_source,
        "warnings": warnings,
        "notes": notes,
        "direction": side,
    }


def _infer_strategy_direction(strategy_name: str, filters: Dict[str, Any]) -> str:
    """Infer whether a stock strategy should reward long or short price action."""
    name = _normalize_strategy_key(strategy_name)
    bearish_tokens = ("short", "down", "bear", "breakdown", "distribution", "selling")
    if any(token in name for token in bearish_tokens):
        return "short"

    change_bounds = filters.get("Change %")
    if isinstance(change_bounds, (list, tuple)) and len(change_bounds) >= 2:
        if float(change_bounds[1]) <= 0:
            return "short"

    gap_bounds = filters.get("Gap %")
    if isinstance(gap_bounds, (list, tuple)) and len(gap_bounds) >= 2:
        if float(gap_bounds[1]) <= 0:
            return "short"

    close_bounds = filters.get("Close Position")
    if isinstance(close_bounds, (list, tuple)) and len(close_bounds) >= 2:
        if float(close_bounds[1]) <= 0.5:
            return "short"

    return "long"


def _candle_wicks_pct(open_price: float, high: float, low: float, close: float) -> tuple[float, float]:
    """Return upper/lower wick percentages on a 0-100 scale for setup scoring."""
    total_range = max(0.0, high - low)
    if total_range <= 0:
        return 0.0, 0.0
    body_top = max(open_price, close)
    body_bottom = min(open_price, close)
    upper_wick_pct = max(0.0, high - body_top) / total_range * 100
    lower_wick_pct = max(0.0, body_bottom - low) / total_range * 100
    return upper_wick_pct, lower_wick_pct


def _snapshot_atr_pct(day: Dict[str, Any], prev: Dict[str, Any], price: float) -> float:
    """Use yesterday's range as conservative ATR proxy, falling back to current range."""
    prev_close = float(prev.get("c") or 0)
    prev_high = float(prev.get("h") or 0)
    prev_low = float(prev.get("l") or 0)
    if prev_close > 0 and prev_high > prev_low:
        return max(0.1, (prev_high - prev_low) / prev_close * 100)

    day_high = float(day.get("h") or 0)
    day_low = float(day.get("l") or 0)
    ref_price = price or float(day.get("c") or 0)
    if ref_price > 0 and day_high > day_low:
        return max(0.1, (day_high - day_low) / ref_price * 100)
    return 2.5


def _fetch_strategy_snapshot_universe(strategy_name: str) -> List[Dict[str, Any]]:
    """Fetch a broad stock universe, with top movers only as a supplement."""
    merged: Dict[str, Dict[str, Any]] = {}

    def _add_tickers(tickers: List[Dict[str, Any]], source: str) -> None:
        for item in tickers or []:
            ticker = str(item.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            existing = merged.get(ticker)
            if existing:
                existing.update(item)
                sources = set(existing.get("_sources", []))
                sources.add(source)
                existing["_sources"] = sorted(sources)
            else:
                cloned = dict(item)
                cloned["_sources"] = [source]
                merged[ticker] = cloned

    try:
        full_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        full_resp = rate_limited_get(full_url, params={"apiKey": POLYGON_KEY}, timeout=30)
        if full_resp.status_code == 200:
            _add_tickers(full_resp.json().get("tickers", []), "full")
        else:
            print(f"[Strategy Scan] full snapshot API error: {full_resp.status_code}")
    except Exception as e:
        print(f"[Strategy Scan] full snapshot error: {e}")

    for endpoint in ("gainers", "losers"):
        try:
            url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/{endpoint}"
            resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 250}, timeout=15)
            if resp.status_code != 200:
                print(f"[Strategy Scan] {endpoint} API error: {resp.status_code}")
                continue
            _add_tickers(resp.json().get("tickers", []), endpoint)
        except Exception as e:
            print(f"[Strategy Scan] {endpoint} error: {e}")

    print(f"[Strategy Scan] {strategy_name}: Snapshot universe {len(merged)} Ticker")
    return list(merged.values())


def _score_strategy_candidate(
    *,
    strategy_name: str,
    filters: Dict[str, Any],
    change_pct: float,
    rvol: float,
    close_pos: float,
    dollar_vol: float,
    gap_pct: float,
    vortag_pct: float,
    price: float,
    day_open: float,
    day_high: float,
    day_low: float,
    prev_atr_pct: float,
) -> tuple[int, Dict[str, Any]]:
    """Blend fast snapshot scoring with the richer ATR/wick setup scorer."""
    direction = _infer_strategy_direction(strategy_name, filters)
    abs_change = abs(change_pct)
    atr_pct = max(prev_atr_pct or 0, 0.1)
    extension_ratio = abs_change / atr_pct if atr_pct > 0 else 0
    upper_wick_pct, lower_wick_pct = _candle_wicks_pct(day_open, day_high, day_low, price)

    score = 0

    # Timing: reward meaningful but not overextended moves.
    if abs_change < 0.5:
        score += 2
    elif extension_ratio <= 2.0 and abs_change <= 8:
        score += 28
    elif extension_ratio <= 3.0 and abs_change <= 12:
        score += 20
    elif extension_ratio <= 4.0 and abs_change <= 18:
        score += 10
    else:
        score += 2

    if rvol >= 3.0:
        score += 22
    elif rvol >= 2.0:
        score += 16
    elif rvol >= 1.5:
        score += 10
    elif rvol >= 1.0:
        score += 5

    if direction == "long":
        if close_pos >= 0.8:
            score += 14
        elif close_pos >= 0.6:
            score += 9
        if upper_wick_pct > 45:
            score -= 10
        elif upper_wick_pct > 30:
            score -= 5
    else:
        if close_pos <= 0.2:
            score += 14
        elif close_pos <= 0.4:
            score += 9
        if lower_wick_pct > 45:
            score -= 10
        elif lower_wick_pct > 30:
            score -= 5

    if dollar_vol >= 10_000_000:
        score += 14
    elif dollar_vol >= 5_000_000:
        score += 9
    elif dollar_vol >= 1_000_000:
        score += 5

    if "Gap %" in filters:
        gap_aligned = (direction == "long" and gap_pct > 0) or (direction == "short" and gap_pct < 0)
        abs_gap = abs(gap_pct)
        if gap_aligned and 2 <= abs_gap <= 8:
            score += 14
        elif gap_aligned and abs_gap > 8:
            score += 8
        elif gap_aligned and abs_gap >= 1:
            score += 5
        elif abs_gap >= 2:
            score -= 8

    if direction == "long":
        if vortag_pct < -5:
            score -= 12
        elif vortag_pct < -2:
            score -= 6
        if close_pos < 0.3 and change_pct > 0:
            score -= 8
    else:
        if vortag_pct > 5:
            score -= 12
        elif vortag_pct > 2:
            score -= 6
        if close_pos > 0.7 and change_pct < 0:
            score -= 8

    setup_score = None
    if calculate_stock_setup_score is not None:
        try:
            setup_score = calculate_stock_setup_score(
                change_pct=change_pct,
                rvol=rvol,
                close_pos=close_pos,
                upper_wick_pct=upper_wick_pct,
                lower_wick_pct=lower_wick_pct,
                vortag_pct=vortag_pct,
                atr_pct=atr_pct,
                dollar_volume=dollar_vol,
                price=price,
                direction=direction,
            )
            score = round(score * 0.35 + float(setup_score) * 0.65)
        except Exception as e:
            print(f"[Strategy Scan] setup scorer failed: {e}")

    # Hard cap late chase/blowout entries unless the richer score still says it is elite.
    if extension_ratio > 4.0 or abs_change >= 20:
        score = min(score, 74)
    if extension_ratio > 5.0 or abs_change >= 35:
        score = min(score, 64)

    meta = {
        "direction": direction,
        "setup_score": round(float(setup_score), 1) if setup_score is not None else None,
        "atr_pct": round(atr_pct, 2),
        "extension_atr": round(extension_ratio, 2),
        "upper_wick_pct": round(upper_wick_pct, 1),
        "lower_wick_pct": round(lower_wick_pct, 1),
    }
    return int(_clamp_float(score, 0, 100)), meta


def _calc_sma_series(values: List[float], period: int) -> List[Optional[float]]:
    """Simple moving average series aligned to the input length."""
    result: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for idx in range(period, len(values)):
        window_sum += values[idx] - values[idx - period]
        result[idx] = window_sum / period
    return result


def _fetch_strategy_daily_history(
    ticker: str,
    min_days: int,
    history_cache: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Fetch daily bars once per ticker for strategy validation."""
    cache_key = f"{ticker}:{min_days}"
    if cache_key in history_cache:
        return history_cache[cache_key]

    daily_bars: List[Dict[str, Any]] = []

    # Cheap path for short lookbacks.
    if min_days <= 60:
        try:
            daily_bars = fetch_multi_day_data(ticker, POLYGON_KEY, days=max(min_days + 8, min_days))
        except Exception:
            daily_bars = []

    if not daily_bars or len(daily_bars) < min_days:
        try:
            ohlcv = fetch_ohlcv_for_chart(ticker, POLYGON_KEY, timeframe="1D", bars=max(min_days + 30, 120))
        except Exception:
            ohlcv = None
        if ohlcv:
            daily_bars = []
            for bar in ohlcv:
                try:
                    daily_bars.append({
                        "date": datetime.fromtimestamp(bar["time"]).strftime("%Y-%m-%d") if bar.get("time") else "",
                        "open": float(bar.get("open", bar.get("close", 0)) or 0),
                        "high": float(bar.get("high", bar.get("close", 0)) or 0),
                        "low": float(bar.get("low", bar.get("close", 0)) or 0),
                        "close": float(bar.get("close", 0) or 0),
                        "volume": float(bar.get("volume", 0) or 0),
                    })
                except Exception as item_err:
                    print(f"[Strategy Scan] history parse skip {ticker}: {item_err}")
                    continue

    history_cache[cache_key] = daily_bars
    return daily_bars


def _apply_pattern_strategy_filter(candidate: Dict[str, Any], strat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate multi-day pattern strategies with real daily history."""
    if analyze_multi_day_pattern is None:
        return None

    history_days = int(strat.get("history_days", 20) or 20)
    daily_bars = candidate.get("_daily_bars", [])
    if len(daily_bars) < history_days:
        return None

    pattern_type = str(strat.get("pattern_type", "consolidation") or "consolidation")
    pattern_window = daily_bars[-history_days:]
    is_valid, pattern_score, details = analyze_multi_day_pattern(pattern_window, pattern_type)
    if not is_valid:
        return None

    quality_floor = {
        "consolidation": 45,
        "bull_flag": 45,
        "bear_flag": 45,
        "consolidation_breakout": 55,
        "churn": 55,
        "wyckoff_accumulation": 60,
        "wyckoff_distribution": 60,
        "reversal_setup": 50,
    }.get(pattern_type, 50)
    if pattern_score < quality_floor:
        return None

    base_score = float(candidate.get("base_score", candidate.get("score", 0)) or 0)
    liquidity_floor = max(int(strat.get("min_dollar_volume", 200_000) or 0), 500_000)
    if float(candidate.get("Dollar_Volume", 0) or 0) < liquidity_floor:
        return None

    final_score = round(min(100, base_score * 0.4 + float(pattern_score) * 0.6))
    enriched = dict(candidate)
    enriched.update({
        "pattern_type": pattern_type,
        "pattern_score": round(float(pattern_score), 1),
        "pattern_details": details[:4],
        "history_days": history_days,
        "score": final_score,
        "grade": _strategy_score_to_grade(final_score),
    })
    return enriched


def _apply_void_strategy_filter(
    candidate: Dict[str, Any],
    strat: Dict[str, Any],
    strategy_name: str,
) -> Optional[Dict[str, Any]]:
    """Validate volume-void setups with profile-aware direction checks."""
    daily_bars = candidate.get("_daily_bars", [])
    if len(daily_bars) < 40:
        return None

    liquidity_floor = max(int(strat.get("min_dollar_volume", 200_000) or 0), 750_000)
    if float(candidate.get("Dollar_Volume", 0) or 0) < liquidity_floor:
        return None

    profile = calculate_volume_profile(daily_bars[-90:], num_bins=24)
    if not profile:
        return None

    price = float(candidate.get("price", candidate.get("Preis", 0)) or 0)
    if price <= 0:
        return None

    voids = find_volume_voids(price, profile, min_void_size_pct=0.8)
    if not voids:
        return None

    is_long = "short" not in strategy_name.lower() and "⬇" not in strategy_name
    relevant = voids.get("nearest_void_above") if is_long else voids.get("nearest_void_below")
    if not relevant:
        return None

    if is_long:
        distance_pct = ((relevant.get("low", price) - price) / price) * 100 if price > 0 else 99
    else:
        distance_pct = ((price - relevant.get("high", price)) / price) * 100 if price > 0 else 99
    if distance_pct < 0 or distance_pct > 8:
        return None

    size_pct = float(relevant.get("size_pct", 0) or 0)
    directional_voids = voids.get("voids_above", []) if is_long else voids.get("voids_below", [])
    close_pos = float(candidate.get("Close_Position", candidate.get("close_pos", 0.5)) or 0.5)

    void_score = 0.0
    if distance_pct <= 1.5:
        void_score += 35
    elif distance_pct <= 3:
        void_score += 25
    elif distance_pct <= 6:
        void_score += 15
    else:
        void_score += 5

    if size_pct >= 4:
        void_score += 25
    elif size_pct >= 2:
        void_score += 18
    elif size_pct >= 1:
        void_score += 10

    if len(directional_voids) >= 2:
        void_score += 15
    elif directional_voids:
        void_score += 8

    poc = float(voids.get("poc", 0) or 0)
    if poc > 0:
        if is_long and price <= poc * 1.02:
            void_score += 15
        elif (not is_long) and price >= poc * 0.98:
            void_score += 15
        else:
            void_score += 5

    if is_long and close_pos >= 0.35:
        void_score += 10
    elif (not is_long) and close_pos <= 0.65:
        void_score += 10

    if void_score < 55:
        return None

    base_score = float(candidate.get("base_score", candidate.get("score", 0)) or 0)
    final_score = round(min(100, base_score * 0.35 + void_score * 0.65))

    target_price = relevant.get("high") if is_long else relevant.get("low")
    enriched = dict(candidate)
    enriched.update({
        "VoidSize%": round(size_pct, 2),
        "VoidDist%": round(distance_pct, 2),
        "VoidsAbove": len(voids.get("voids_above", [])),
        "VoidsBelow": len(voids.get("voids_below", [])),
        "void_score": round(void_score, 1),
        "void_target": round(float(target_price or 0), 2) if target_price else None,
        "poc": round(poc, 2) if poc else None,
        "vah": round(float(voids.get("vah", 0) or 0), 2) if voids.get("vah") else None,
        "val": round(float(voids.get("val", 0) or 0), 2) if voids.get("val") else None,
        "score": final_score,
        "grade": _strategy_score_to_grade(final_score),
    })
    return enriched


def _apply_harmonic_strategy_filter(candidate: Dict[str, Any], strat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Keep only harmonic setups that are still actionable near the entry zone."""
    if not HAS_PATTERNS:
        return None

    daily_bars = candidate.get("_daily_bars", [])
    if len(daily_bars) < 60:
        return None

    liquidity_floor = max(int(strat.get("min_dollar_volume", 200_000) or 0), 1_000_000)
    if float(candidate.get("Dollar_Volume", 0) or 0) < liquidity_floor:
        return None

    patterns = find_harmonic_for_chart(daily_bars[-220:])
    if not patterns:
        return None

    direction_filter = str(strat.get("harmonic_direction", "ALL") or "ALL").upper()
    if direction_filter != "ALL":
        patterns = [pat for pat in patterns if str(pat.get("direction", "")).upper() == direction_filter]
    if not patterns:
        return None

    price = float(candidate.get("price", candidate.get("Preis", 0)) or 0)
    if price <= 0:
        return None

    best_pattern = None
    best_rank = -999.0
    for pattern in patterns:
        trade = pattern.get("trade", {}) or {}
        entry = float(trade.get("entry", price) or price)
        rr_ratio = float(trade.get("risk_reward", 0) or 0)
        success_rate = float(pattern.get("success_rate", 0) or 0)
        harmonic_score = float(pattern.get("score", 0) or 0)
        distance_pct = abs(entry - price) / price * 100 if price > 0 else 99
        rank = harmonic_score * 0.6 + success_rate * 0.25 + min(rr_ratio, 4.0) * 5 - max(0.0, distance_pct - 4.0) * 3
        if rank > best_rank:
            best_rank = rank
            best_pattern = pattern

    if not best_pattern:
        return None

    trade = best_pattern.get("trade", {}) or {}
    entry = float(trade.get("entry", price) or price)
    distance_pct = abs(entry - price) / price * 100 if price > 0 else 99
    rr_ratio = float(trade.get("risk_reward", 0) or 0)
    harmonic_score = float(best_pattern.get("score", 0) or 0)
    success_rate = float(best_pattern.get("success_rate", 0) or 0)

    if harmonic_score < 65 or rr_ratio < 1.8 or distance_pct > 10:
        return None

    composite_score = harmonic_score * 0.7 + success_rate * 0.3
    if rr_ratio >= 2.5:
        composite_score += 8
    if distance_pct <= 3:
        composite_score += 8
    elif distance_pct <= 5:
        composite_score += 4
    composite_score = min(100, composite_score)

    base_score = float(candidate.get("base_score", candidate.get("score", 0)) or 0)
    final_score = round(min(100, base_score * 0.25 + composite_score * 0.75))

    enriched = dict(candidate)
    enriched.update({
        "harmonic_pattern": best_pattern.get("pattern"),
        "harmonic_direction": best_pattern.get("direction"),
        "harmonic_score": round(composite_score, 1),
        "harmonic_matches": best_pattern.get("matches"),
        "harmonic_success_rate": round(success_rate, 1),
        "entry": round(entry, 2),
        "stop_loss": round(float(trade.get("stop_loss", 0) or 0), 2) if trade.get("stop_loss") else None,
        "tp1": round(float(trade.get("tp1", 0) or 0), 2) if trade.get("tp1") else None,
        "tp2": round(float(trade.get("tp2", 0) or 0), 2) if trade.get("tp2") else None,
        "risk_reward": round(rr_ratio, 2),
        "entry_distance_pct": round(distance_pct, 2),
        "score": final_score,
        "grade": _strategy_score_to_grade(final_score),
    })
    return enriched


def _bar_num(bar: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in bar:
            try:
                value = float(bar.get(key) or 0)
            except (TypeError, ValueError):
                value = default
            if math.isfinite(value):
                return value
    return default


def _calc_recent_atr(bars: List[Dict[str, Any]], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    true_ranges: List[float] = []
    recent = bars[-(period + 1):]
    for idx in range(1, len(recent)):
        high = _bar_num(recent[idx], "high", "h")
        low = _bar_num(recent[idx], "low", "l")
        prev_close = _bar_num(recent[idx - 1], "close", "c")
        if high <= 0 or low <= 0:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0


def _detect_cup_handle_breakout(
    daily_bars: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Strict daily Cup-and-Handle detector for actionable long breakouts only."""
    cleaned = [
        bar for bar in daily_bars
        if _bar_num(bar, "high", "h") > 0 and _bar_num(bar, "low", "l") > 0 and _bar_num(bar, "close", "c") > 0
    ]
    if len(cleaned) < 70:
        return None

    bars = cleaned[-180:]
    last = bars[-1]
    price = float(current_price or _bar_num(last, "close", "c") or 0)
    if price <= 0:
        return None

    best: Optional[Dict[str, Any]] = None
    n = len(bars)
    max_window = min(n, 170)

    for window_len in range(max_window, 69, -10):
        segment = bars[-window_len:]
        min_handle = max(5, int(window_len * 0.07))
        max_handle = min(24, max(min_handle, int(window_len * 0.22)))
        for handle_len in range(min_handle, max_handle + 1):
            cup = segment[:-handle_len]
            handle = segment[-handle_len:]
            if len(cup) < 45 or len(handle) < 5:
                continue

            cup_len = len(cup)
            left = cup[:max(8, int(cup_len * 0.32))]
            middle = cup[int(cup_len * 0.22):int(cup_len * 0.78)]
            right = cup[int(cup_len * 0.62):]
            if not left or not middle or not right:
                continue

            left_lip = max(_bar_num(bar, "high", "h") for bar in left)
            right_lip = max(_bar_num(bar, "high", "h") for bar in right)
            cup_lip = max(left_lip, right_lip)
            bottom = min(_bar_num(bar, "low", "l") for bar in middle)
            if cup_lip <= 0 or bottom <= 0 or bottom >= cup_lip:
                continue

            depth_abs = cup_lip - bottom
            depth_pct = depth_abs / cup_lip * 100
            if depth_pct < 10 or depth_pct > 45:
                continue

            lip_ratio = right_lip / left_lip if left_lip > 0 else 0
            if lip_ratio < 0.86 or lip_ratio > 1.16:
                continue

            bottom_zone = bottom + depth_abs * 0.18
            rounded_bottom_bars = sum(1 for bar in middle if _bar_num(bar, "low", "l") <= bottom_zone)
            if rounded_bottom_bars < 3:
                continue

            handle_high = max(_bar_num(bar, "high", "h") for bar in handle)
            handle_low = min(_bar_num(bar, "low", "l") for bar in handle)
            handle_close = _bar_num(handle[-1], "close", "c")
            handle_depth_pct = (cup_lip - handle_low) / cup_lip * 100
            if handle_depth_pct < 1.0 or handle_depth_pct > min(16.0, depth_pct * 0.58):
                continue
            if handle_low <= bottom + depth_abs * 0.45:
                continue
            if handle_high > cup_lip * 1.08:
                continue

            close_pos = 0.5
            last_high = _bar_num(last, "high", "h")
            last_low = _bar_num(last, "low", "l")
            if last_high > last_low:
                close_pos = (handle_close - last_low) / (last_high - last_low)
            breakout_confirmed = (
                handle_close >= cup_lip * 1.002
                or (last_high >= cup_lip * 1.006 and close_pos >= 0.65)
            )
            if not breakout_confirmed:
                continue

            extension_pct = (price - cup_lip) / cup_lip * 100
            if extension_pct < -1.5 or extension_pct > 8.0:
                continue

            recent_volumes = [_bar_num(bar, "volume", "v") for bar in segment[-21:-1] if _bar_num(bar, "volume", "v") > 0]
            last_volume = _bar_num(last, "volume", "v")
            avg20_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0
            if avg20_volume <= 0 or last_volume <= 0:
                continue
            rvol = last_volume / avg20_volume
            if rvol < 1.1:
                continue

            cup_volumes = [_bar_num(bar, "volume", "v") for bar in cup if _bar_num(bar, "volume", "v") > 0]
            handle_volumes = [_bar_num(bar, "volume", "v") for bar in handle[:-1] if _bar_num(bar, "volume", "v") > 0]
            cup_avg_volume = sum(cup_volumes) / len(cup_volumes) if cup_volumes else 0
            handle_avg_volume = sum(handle_volumes) / len(handle_volumes) if handle_volumes else 0
            handle_volume_contracts = bool(cup_avg_volume and handle_avg_volume and handle_avg_volume <= cup_avg_volume * 1.15)

            atr = _calc_recent_atr(segment, 14)
            if atr <= 0:
                atr = max(depth_abs * 0.08, cup_lip * 0.02)

            entry = cup_lip
            stop = min(handle_low - atr * 0.20, cup_lip - depth_abs * 0.22)
            stop = max(stop, bottom + depth_abs * 0.25)
            if stop >= entry:
                stop = handle_low - atr * 0.35
            if stop <= 0 or stop >= entry:
                continue

            tp1 = entry + depth_abs * 0.50
            tp2 = entry + depth_abs * 1.00
            risk = entry - stop
            blended_reward = ((tp1 - entry) * 0.5) + ((tp2 - entry) * 0.5)
            rr = blended_reward / risk if risk > 0 else 0
            live_rr = (((tp1 - price) * 0.5) + ((tp2 - price) * 0.5)) / (price - stop) if price > stop else 0
            if rr < 1.8 or live_rr < 1.4:
                continue

            score = 50.0
            if 12 <= depth_pct <= 32:
                score += 15
            elif depth_pct <= 40:
                score += 8
            score += max(0.0, 10.0 - abs(1.0 - lip_ratio) * 50.0)
            if 3 <= handle_depth_pct <= 12:
                score += 10
            elif handle_depth_pct <= 16:
                score += 5
            if rvol >= 2.0:
                score += 12
            elif rvol >= 1.5:
                score += 9
            elif rvol >= 1.1:
                score += 5
            if extension_pct <= 2.5:
                score += 10
            elif extension_pct <= 5:
                score += 5
            if rr >= 2.5:
                score += 8
            elif rr >= 1.8:
                score += 4
            if handle_volume_contracts:
                score += 6
            if rounded_bottom_bars >= 5:
                score += 4

            score = int(_clamp_float(round(score), 0, 100))
            if score < 80:
                continue

            match = {
                "score": score,
                "cup_depth_pct": round(depth_pct, 2),
                "handle_depth_pct": round(handle_depth_pct, 2),
                "cup_lip": _round_level_price(cup_lip),
                "cup_bottom": _round_level_price(bottom),
                "handle_low": _round_level_price(handle_low),
                "entry": _round_level_price(entry),
                "stop_loss": _round_level_price(stop),
                "tp1": _round_level_price(tp1),
                "tp2": _round_level_price(tp2),
                "risk_reward": round(rr, 2),
                "live_rr_ratio": round(live_rr, 2),
                "extension_pct": round(extension_pct, 2),
                "breakout_rvol": round(rvol, 2),
                "handle_volume_contracts": handle_volume_contracts,
                "cup_length": cup_len,
                "handle_length": handle_len,
                "timeframe": "1D",
                "confirmation_timeframe": "5m",
            }
            if not best or (match["score"], match["risk_reward"]) > (best["score"], best["risk_reward"]):
                best = match

    return best


def _apply_cup_handle_strategy_filter(candidate: Dict[str, Any], strat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    daily_bars = candidate.get("_daily_bars", [])
    liquidity_floor = max(int(strat.get("min_dollar_volume", 0) or 0), 2_000_000)
    if float(candidate.get("Dollar_Volume", 0) or 0) < liquidity_floor:
        return None

    price = float(candidate.get("price", candidate.get("Preis", 0)) or 0)
    setup = _detect_cup_handle_breakout(daily_bars, current_price=price)
    if not setup:
        return None

    final_score = int(max(float(candidate.get("base_score", candidate.get("score", 0)) or 0) * 0.20, 0) + setup["score"] * 0.80)
    final_score = int(_clamp_float(final_score, 0, 100))
    grade = _strategy_score_to_grade(final_score)
    if final_score < 80:
        return None

    enriched = dict(candidate)
    ticker = _extract_alert_ticker(enriched)
    if ticker and (enriched.get("latest_bar_change_pct") is None or enriched.get("latest_bar_close_pos") is None):
        enriched.update(_fetch_long_latest_intraday_state(ticker))

    trade_setup = {
        "direction": "LONG",
        "trade_action": "LONG_NOW",
        "entry_status": "BREAKOUT_CONFIRMED",
        "entry": setup["entry"],
        "stop_loss": setup["stop_loss"],
        "tp1": setup["tp1"],
        "tp2": setup["tp2"],
        "risk_reward": setup["risk_reward"],
        "live_rr": setup["live_rr_ratio"],
        "rr_model": "50/50 TP1/TP2 measured cup depth",
        "source": "cup_handle_1d_breakout",
    }
    enriched.update({
        "pattern": "Cup and Handle Breakout",
        "pattern_type": "cup_handle_breakout",
        "pattern_timeframe": setup["timeframe"],
        "confirmation_timeframe": setup["confirmation_timeframe"],
        "pattern_score": setup["score"],
        "CupDepth%": setup["cup_depth_pct"],
        "HandleDepth%": setup["handle_depth_pct"],
        "Breakout_Level": setup["cup_lip"],
        "Handle_Low": setup["handle_low"],
        "Breakout_RVOL": setup["breakout_rvol"],
        "entry": setup["entry"],
        "Entry": setup["entry"],
        "stop_loss": setup["stop_loss"],
        "StopLoss": setup["stop_loss"],
        "tp1": setup["tp1"],
        "TP1": setup["tp1"],
        "tp2": setup["tp2"],
        "TP2": setup["tp2"],
        "risk_reward": setup["risk_reward"],
        "live_rr_ratio": setup["live_rr_ratio"],
        "entry_distance_pct": setup["extension_pct"],
        "target_model": "cup_depth_measured_move",
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "LONG_NOW",
        "entry_status": "BREAKOUT_CONFIRMED",
        "direction": "LONG",
        "Signal_Direction": "LONG",
        "scanner_note": "Cup-and-Handle Breakout: 1D structure, fresh 5m execution trigger confirmed.",
        "trade_setup": trade_setup,
        "score": final_score,
        "grade": grade,
        "base_grade": grade,
    })
    long_reasons = _long_entry_rule_reasons(enriched)
    if long_reasons:
        return None
    enriched["long_entry_quality"] = _long_entry_quality(enriched)
    enriched["alertable_long"] = True
    return enriched


def _apply_ma_strategy_filter(candidate: Dict[str, Any], strat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate MA bounce setups with trend, structure and pullback quality."""
    daily_bars = candidate.get("_daily_bars", [])
    ma_profiles = _get_ma_profiles(strat)
    max_period = _get_max_ma_period(strat)
    if len(daily_bars) < max_period + 10:
        return None

    liquidity_floor = max(int(strat.get("min_dollar_volume", 200_000) or 0), 1_000_000)
    if float(candidate.get("Dollar_Volume", 0) or 0) < liquidity_floor:
        return None

    closes = [float(bar.get("close", 0) or 0) for bar in daily_bars]
    price = float(candidate.get("price", candidate.get("Preis", 0)) or 0)
    if price <= 0:
        return None

    change_pct = float(candidate.get("change_pct", candidate.get("Change_Pct", 0)) or 0)
    close_pos = float(candidate.get("Close_Position", candidate.get("close_pos", 0.5)) or 0.5)
    rvol = float(candidate.get("RVOL", candidate.get("rvol", 1)) or 1)
    base_score = float(candidate.get("base_score", candidate.get("score", 0)) or 0)
    best_match: Optional[Dict[str, Any]] = None

    for profile in ma_profiles:
        ma_type = profile["ma_type"]
        ma_period = profile["ma_period"]
        ma_series = calculate_ema_series(closes, ma_period) if ma_type == "EMA" else _calc_sma_series(closes, ma_period)
        if not ma_series or ma_series[-1] is None:
            continue

        current_ma = ma_series[-1]
        prev_ma = next((val for val in reversed(ma_series[:-1]) if val is not None), None)
        if current_ma is None or prev_ma is None or current_ma <= 0 or prev_ma <= 0:
            continue

        distance_signed = ((price - current_ma) / current_ma) * 100
        distance_abs = abs(distance_signed)
        max_distance = profile["ma_distance_max"]
        ma_slope_pct = ((current_ma - prev_ma) / prev_ma) * 100

        recent_pairs = [
            (float(close), ma_val)
            for close, ma_val in zip(closes[-6:], ma_series[-6:])
            if ma_val is not None and ma_val > 0
        ]
        if len(recent_pairs) < 4:
            continue

        approach = profile["ma_approach"]
        is_long = approach == "from_above"
        prior_distances = [abs((close - ma_val) / ma_val) * 100 for close, ma_val in recent_pairs[:-1]]
        prior_avg_distance = sum(prior_distances) / len(prior_distances) if prior_distances else max_distance + 1
        above_count = sum(1 for close, ma_val in recent_pairs[:-1] if close >= ma_val)
        below_count = sum(1 for close, ma_val in recent_pairs[:-1] if close <= ma_val)

        if is_long:
            if distance_signed < 0 or distance_signed > max_distance:
                continue
            if ma_slope_pct <= 0.05:
                continue
            if above_count < max(2, len(recent_pairs) - 2):
                continue
            if distance_abs > prior_avg_distance + 0.75:
                continue
            if change_pct < -4 or close_pos < 0.20:
                continue
        else:
            if distance_signed > 0 or abs(distance_signed) > max_distance:
                continue
            if ma_slope_pct >= -0.05:
                continue
            if below_count < max(2, len(recent_pairs) - 2):
                continue
            if distance_abs > prior_avg_distance + 0.75:
                continue
            if change_pct > 4 or close_pos > 0.80:
                continue

        ma_score = 0.0
        if distance_abs <= 0.5:
            ma_score += 30
        elif distance_abs <= 1.0:
            ma_score += 25
        elif distance_abs <= 2.0:
            ma_score += 18
        else:
            ma_score += 10

        if (is_long and ma_slope_pct >= 1.0) or ((not is_long) and ma_slope_pct <= -1.0):
            ma_score += 25
        else:
            ma_score += 18

        if (is_long and above_count >= 4) or ((not is_long) and below_count >= 4):
            ma_score += 20
        elif (is_long and above_count >= 3) or ((not is_long) and below_count >= 3):
            ma_score += 14

        if 0.8 <= rvol <= 3.5:
            ma_score += 10
        elif rvol >= 0.6:
            ma_score += 6

        if (is_long and close_pos >= 0.50) or ((not is_long) and close_pos <= 0.50):
            ma_score += 15
        else:
            ma_score += 5

        if ma_score < 60:
            continue

        final_score = round(min(100, base_score * 0.4 + ma_score * 0.6))
        match = {
            "ma_label": f"{ma_type} {ma_period}",
            "ma_value": round(float(current_ma), 2),
            "distance_abs": round(distance_abs, 2),
            "distance_signed": round(distance_signed, 2),
            "ma_slope_pct": round(ma_slope_pct, 2),
            "ma_score": round(ma_score, 1),
            "score": final_score,
        }
        if best_match is None or match["score"] > best_match["score"] or (
            match["score"] == best_match["score"] and match["ma_score"] > best_match["ma_score"]
        ):
            best_match = match

    if not best_match:
        return None

    enriched = dict(candidate)
    enriched.update({
        "ma_type": best_match["ma_label"],
        "ma_value": best_match["ma_value"],
        "MA_Distance%": best_match["distance_abs"],
        "ma_distance_signed_pct": best_match["distance_signed"],
        "ma_slope_pct": best_match["ma_slope_pct"],
        "ma_score": best_match["ma_score"],
        "score": best_match["score"],
        "grade": _strategy_score_to_grade(best_match["score"]),
    })
    return enriched


def _apply_special_strategy_post_filter(
    candidates: List[Dict[str, Any]],
    strat: Dict[str, Any],
    strategy_name: str,
) -> List[Dict[str, Any]]:
    """Upgrade strategy scans from pure snapshot filters to setup validation."""
    if not any(
        strat.get(flag)
        for flag in ("needs_history", "needs_volume_profile", "needs_harmonic", "needs_ma", "needs_cup_handle")
    ):
        return candidates

    if strat.get("needs_history") and analyze_multi_day_pattern is None:
        print(f"[Strategy Scan] {strategy_name}: analysis helper missing, returning no results")
        return []
    if strat.get("needs_harmonic") and not HAS_PATTERNS:
        print(f"[Strategy Scan] {strategy_name}: patterns helper missing, returning no results")
        return []

    history_cache: Dict[str, List[Dict[str, Any]]] = {}
    candidate_limit = 220
    if strat.get("needs_harmonic"):
        candidate_limit = 120
    elif strat.get("needs_cup_handle"):
        candidate_limit = 180
    elif strat.get("needs_ma"):
        candidate_limit = 180
    elif strat.get("needs_volume_profile"):
        candidate_limit = 160

    min_history = max(int(strat.get("history_days", 0) or 0), 20)
    if strat.get("needs_volume_profile"):
        min_history = max(min_history, 90)
    if strat.get("needs_harmonic"):
        min_history = max(min_history, 220)
    if strat.get("needs_cup_handle"):
        min_history = max(min_history, 180)
    if strat.get("needs_ma"):
        min_history = max(min_history, _get_max_ma_period(strat) + 12)

    filtered: List[Dict[str, Any]] = []
    for candidate in candidates[:candidate_limit]:
        ticker = candidate.get("ticker") or candidate.get("Ticker")
        if not ticker:
            continue

        daily_bars = _fetch_strategy_daily_history(str(ticker), min_history, history_cache)
        if len(daily_bars) < min_history:
            continue

        enriched = dict(candidate)
        enriched["_daily_bars"] = daily_bars

        if strat.get("needs_history"):
            enriched = _apply_pattern_strategy_filter(enriched, strat)
            if not enriched:
                continue
        if strat.get("needs_volume_profile"):
            enriched = _apply_void_strategy_filter(enriched, strat, strategy_name)
            if not enriched:
                continue
        if strat.get("needs_harmonic"):
            enriched = _apply_harmonic_strategy_filter(enriched, strat)
            if not enriched:
                continue
        if strat.get("needs_cup_handle"):
            enriched = _apply_cup_handle_strategy_filter(enriched, strat)
            if not enriched:
                continue
        if strat.get("needs_ma"):
            enriched = _apply_ma_strategy_filter(enriched, strat)
            if not enriched:
                continue

        enriched.pop("_daily_bars", None)
        filtered.append(enriched)

    filtered.sort(key=lambda x: (-float(x.get("score", 0) or 0), -float(x.get("Dollar_Volume", 0) or 0), -abs(float(x.get("Change_Pct", 0) or 0))))
    print(f"[Strategy Scan] {strategy_name}: {len(filtered)}/{min(len(candidates), candidate_limit)} Kandidaten nach Spezial-Check")
    return filtered


def _bi_background_scan_wrapper(direction: str) -> None:
    """Wrapper to run _bi_background_scan in background without candidates pre-load."""
    try:
        print(f"[BI {direction}] Starting scan...")
        _bi_background_scan(POLYGON_KEY, direction=direction, candidates=None)
        print(f"[BI {direction}] Scan completed")
        # Email Alert bei Grade S/A
        cache = BI_CACHE_LONG if direction == "long" else BI_CACHE_SHORT
        _check_and_alert(f"bi_{direction}", cache)
    except Exception as e:
        print(f"BI background scan error ({direction}): {e}")
        import traceback
        traceback.print_exc()


def _enrich_biotech_alert_trade_levels() -> Dict[str, int]:
    """Add structured long levels to Biotech cache rows before email gating."""
    rows, _ = load_cache_file(BIOTECH_CACHE, max_age_hours=24 * 30)
    if not rows:
        return {"rows": 0, "changed": 0, "valid_after": 0}

    changed = 0
    valid_after = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _alert_trade_levels(row).get("valid"):
            valid_after += 1
            continue

        price = _alert_float(row.get("Preis", row.get("price", row.get("Price"))))
        if not price or price <= 0:
            continue

        tech = row.get("Tech_Details") if isinstance(row.get("Tech_Details"), dict) else {}
        support = _alert_float(tech.get("support"))
        resistance = _alert_float(tech.get("resistance"))
        high_90d = _alert_float(tech.get("high_90d"))
        low_90d = _alert_float(tech.get("low_90d"))
        atr = _alert_float(tech.get("atr_14"))

        if not atr or atr <= 0:
            sr_range = (resistance - support) if support and resistance and resistance > support else 0
            range_10d_pct = _alert_float(tech.get("range_10d%"))
            atr = max(price * 0.035, sr_range * 0.25 if sr_range > 0 else 0)
            if range_10d_pct and range_10d_pct > 0:
                atr = max(atr, price * min(max(range_10d_pct / 300, 0.025), 0.08))

        setup = _build_structured_trade_setup(
            "LONG",
            price,
            atr,
            support,
            resistance,
            high_90d,
            low_90d,
            tech.get("pos_90d"),
        )
        if not setup:
            continue

        row["direction"] = "LONG"
        row["Signal_Direction"] = "LONG"
        row["Entry"] = setup["entry"]
        row["StopLoss"] = setup["stop"]
        row["TP1"] = setup["tp1"]
        row["TP2"] = setup["tp2"]
        row["trade_setup"] = setup
        row["Trade_Setup_Source"] = "biotech_daily_structure"
        changed += 1
        if _alert_trade_levels(row).get("valid"):
            valid_after += 1

    if changed:
        save_cache_file(BIOTECH_CACHE, rows)
    return {"rows": len(rows), "changed": changed, "valid_after": valid_after}


def _biotech_scan_wrapper() -> None:
    """Wrapper to run biotech background scan in background."""
    try:
        print("[Biotech] Starting scan... (this takes 5-15 minutes)")
        _biotech_background_scan(POLYGON_KEY)
        print("[Biotech] Scan completed")
        enrichment = _enrich_biotech_alert_trade_levels()
        if enrichment.get("changed"):
            print(f"[Biotech] Added alert trade levels: {enrichment}")
        # Email Alert bei Grade S/A
        _check_and_alert("biotech", BIOTECH_CACHE)
    except Exception as e:
        print(f"Biotech background scan error: {e}")
        import traceback
        traceback.print_exc()


def _strategy_scan_wrapper(strategy_name: str, send_email: bool = True) -> List[Dict[str, Any]]:
    """V2.2: Erweiterter Snapshot-Scanner für alle Strategien.
    Berechnet Gap%, Vortag%, Dollar-Volume und filtert korrekt."""
    try:
        strat = STRATEGIES.get(strategy_name)
        if not strat:
            print(f"[Strategy Scan] Strategie '{strategy_name}' nicht gefunden")
            return []

        filters = strat.get("filters", {})
        change_min, change_max = filters.get("Change %", (-999, 999))
        price_min, price_max = filters.get("Preis", (0, 999999))
        rvol_min, rvol_max = filters.get("RVOL", (0, 999))
        close_pos_min, close_pos_max = filters.get("Close Position", (0, 1))
        gap_min, gap_max = filters.get("Gap %", (-999, 999))
        vortag_min, vortag_max = filters.get("Vortag %", (-999, 999))
        # Mindest-Dollar-Volume: Strategie-spezifisch ODER global $200k
        # Ohne das rutschen illiquide Penny Stocks durch
        min_dollar_vol = strat.get("min_dollar_volume", 200_000)
        _has_gap_filter = "Gap %" in filters
        _has_vortag_filter = "Vortag %" in filters

        print(f"[Strategy Scan] {strategy_name}: Change {change_min}..{change_max}%, Preis ${price_min}..${price_max}")

        # Full Snapshot zuerst: ruhige Struktur-Setups (MA Bounce, Flags,
        # Wyckoff, Compression) entstehen oft NICHT in den Top-Gainern/Losern.
        results = []
        _all_snapshot_tickers = _fetch_strategy_snapshot_universe(strategy_name)
        common_stock_universe, common_stock_source = _load_common_stock_universe()
        session_name, _session_label = get_current_trading_session()
        _use_extended_prices = session_name in ("Pre-Market", "After-Hours")

        for t in _all_snapshot_tickers:
                try:
                    ticker = str(t.get("ticker", "")).upper().strip()
                    day = t.get("day", {}) or {}
                    prev = t.get("prevDay", {}) or {}
                    if not ticker or "." in ticker or "/" in ticker or not prev.get("c"):
                        continue
                    non_stock_reason = _stock_alert_asset_exclusion_reason(
                        ticker,
                        common_stock_universe=common_stock_universe,
                        universe_source=common_stock_source,
                        require_reference=common_stock_universe is None,
                    )
                    if non_stock_reason:
                        continue

                    prev_close_regular = float(prev.get("c", 0) or 0)
                    day_close = float(day.get("c", 0) or 0)
                    last_price = float((t.get("lastTrade", {}) or {}).get("p", 0) or 0)
                    regular_price = day_close or last_price
                    use_ext_price = _use_extended_prices and last_price > 0 and day_close > 0

                    if use_ext_price:
                        price = last_price
                        prev_close = day_close
                        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                    else:
                        price = regular_price
                        prev_close = prev_close_regular
                        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                    if not price or not prev_close:
                        continue

                    volume = day.get("v", 0)
                    prev_vol = prev.get("v", 0)
                    # RVOL: prev_vol muss realistisch sein (>1000 Shares), sonst = 1.0
                    if prev_vol > 1000:
                        rvol = round(volume / prev_vol, 2)
                        rvol = min(rvol, 50.0)  # Cap bei 50x — darüber = Datenfehler
                    else:
                        rvol = 1.0  # Kein zuverlässiger Vergleich → neutral
                    dollar_vol = volume * price

                    # Close Position: Extended-Preise in die Range einbeziehen und clampen.
                    day_high = max(float(day.get("h", price) or price), price)
                    day_low = min(float(day.get("l", price) or price), price)
                    day_open = float(day.get("o", prev_close) or prev_close)
                    day_range = day_high - day_low
                    close_pos = _clamp_float((price - day_low) / day_range if day_range > 0 else 0.5, 0.0, 1.0, 0.5)

                    # Regular: Open vs Prev Close. PM/AH: lastTrade vs regular close.
                    regular_gap_pct = ((day_open - prev_close_regular) / prev_close_regular * 100) if prev_close_regular > 0 else 0
                    gap_pct = change_pct if use_ext_price else regular_gap_pct

                    # V2.2: Vortag % = Previous Day Change (prev_close vs prev_open approximiert)
                    prev_open = prev.get("o", prev_close_regular)
                    vortag_pct = ((prev_close_regular - prev_open) / prev_open * 100) if prev_open > 0 else 0
                    prev_atr_pct = _snapshot_atr_pct(day, prev, price)

                    # V3.2: Multi-Day Runner Bypass — bei Vortag >10% wird RVOL
                    # übersprungen (Day2/3 Runner haben niedrigen RVOL weil Vortag auch hoch war)
                    # V4 FIX (2026-04-17): Label-Trennung. Vorher hat der OR-Zweig
                    # "(change_pct>30 and close_pos>0.6)" jeden Day-1-Extrem-Move als
                    # MDR gelabelt — auch wenn Vortag 0% oder negativ war (echte
                    # Beispiele aus Live-Cache: BBGI Vortag=-2%, MAAS Vortag=-4%).
                    # Day-1-Blowouts sind KEINE Multi-Day-Runner und brauchen ein
                    # eigenes Label, damit der Trader sie unterscheiden kann.
                    _is_true_mdr = vortag_pct > 10 and change_pct > 5
                    _is_day1_blowout = (change_pct > 30 and close_pos > 0.6) and not _is_true_mdr
                    # _is_mdr steuert den RVOL-Bypass — beide Klassen duerfen bypassen
                    # (Day-1-Blowouts haben in der Praxis ohnehin RVOL>=1.5, aber
                    # Konsistenz-halber gleiche Filter-Regel).
                    _is_mdr = _is_true_mdr or _is_day1_blowout

                    # Filter anwenden
                    if not (change_min <= change_pct <= change_max):
                        continue
                    if not (price_min <= price <= price_max):
                        continue
                    if "RVOL" in filters and not _is_mdr and not (rvol_min <= rvol <= rvol_max):
                        continue
                    if "Close Position" in filters and not (close_pos_min <= close_pos <= close_pos_max):
                        continue
                    if _has_gap_filter and not (gap_min <= gap_pct <= gap_max):
                        continue
                    if _has_vortag_filter and not (vortag_min <= vortag_pct <= vortag_max):
                        continue
                    if min_dollar_vol > 0 and dollar_vol < min_dollar_vol:
                        continue

                    # Scoring: ATR-/Wick-aware statt "je groesser der Move desto besser".
                    _strat_score, _score_meta = _score_strategy_candidate(
                        strategy_name=strategy_name,
                        filters=filters,
                        change_pct=change_pct,
                        rvol=rvol,
                        close_pos=close_pos,
                        dollar_vol=dollar_vol,
                        gap_pct=gap_pct,
                        vortag_pct=vortag_pct,
                        price=price,
                        day_open=day_open,
                        day_high=day_high,
                        day_low=day_low,
                        prev_atr_pct=prev_atr_pct,
                    )

                    # Grade (verschärft — konsistent mit Krypto-Scanner)
                    _strat_grade = _strategy_score_to_grade(_strat_score)

                    # V3.2 / V4: MDR-Tag und Day-1-Blowout-Tag getrennt
                    _mdr_label = None
                    if _is_true_mdr:
                        # V3.3: Distribution-Check — sinkende RVOL + Fading = Crash-Risiko
                        _mdr_fading = rvol < 0.8 and close_pos < 0.5  # RVOL sinkt + Preis faded
                        _mdr_exhaustion = rvol < 0.5  # Volume kollabiert = Käufer weg

                        if _mdr_fading or _mdr_exhaustion:
                            _mdr_label = "MDR CRASH-RISIKO"
                            _strat_score -= 10  # Malus statt Bonus
                        elif vortag_pct > 30 and change_pct > 15:
                            _mdr_label = "MDR ELITE"
                            _strat_score += 15
                        elif vortag_pct > 15 and change_pct > 8:
                            _mdr_label = "MDR STARK"
                            _strat_score += 10
                        else:
                            _mdr_label = "MDR"
                            _strat_score += 5
                        # Re-grade nach MDR Bonus (mit Cap)
                        _strat_score = max(0, min(100, _strat_score))
                        _strat_grade = _strategy_score_to_grade(_strat_score)
                    elif _is_day1_blowout:
                        # V4 FIX: Day-1-Extrem-Move — explosiver Single-Day-Move, kein MDR.
                        # Kein pauschaler Bonus (Vortag 0% oder negativ => kein Trend-Support),
                        # aber auch kein Malus wenn Volumen/Preis stark sind.
                        _blowout_fading = rvol < 1.0 and close_pos < 0.5
                        if _blowout_fading:
                            _mdr_label = "BLOWOUT FADING"
                            _strat_score -= 8  # Kracher der kippt ist das riskanteste
                        else:
                            _mdr_label = "BLOWOUT"
                            # Kein Bonus — Base-Score (Change/RVOL/ClosePos) reicht aus
                        # Re-grade (falls Malus gezogen)
                        _strat_score = max(0, min(100, _strat_score))
                        _strat_grade = _strategy_score_to_grade(_strat_score)

                    strategy_row = {
                        "Ticker": ticker,
                        "ticker": ticker,
                        "Preis": round(price, 2),
                        "price": round(price, 2),
                        "Change_Pct": round(change_pct, 2),
                        "change_pct": round(change_pct, 2),
                        "Volume": volume,
                        "volume": volume,
                        "RVOL": rvol,
                        "rvol": rvol,
                        "Dollar_Volume": round(dollar_vol),
                        "Prev_Close": round(prev_close, 2),
                        "Day_Open": round(day_open, 2),
                        "Day_High": round(day_high, 2),
                        "Day_Low": round(day_low, 2),
                        "Close_Position": round(close_pos, 2),
                        "close_pos": round(close_pos, 2),
                        "open_to_current_pct": round(((price - day_open) / day_open * 100), 2) if day_open > 0 else None,
                        "Gap_Pct": round(gap_pct, 2),
                        "gap_pct": round(gap_pct, 2),
                        "Vortag_Pct": round(vortag_pct, 2),
                        "ATR_Pct": _score_meta.get("atr_pct"),
                        "Extension_ATR": _score_meta.get("extension_atr"),
                        "Setup_Score": _score_meta.get("setup_score"),
                        "Upper_Wick_Pct": _score_meta.get("upper_wick_pct"),
                        "Lower_Wick_Pct": _score_meta.get("lower_wick_pct"),
                        "Signal_Direction": _score_meta.get("direction"),
                        "Extended_Hours": use_ext_price,
                        "Regular_Gap_Pct": round(regular_gap_pct, 2),
                        "base_score": _strat_score,
                        "base_grade": _strat_grade,
                        "score": _strat_score,
                        "grade": _strat_grade,
                        "mdr_tag": _mdr_label,
                    }
                    _setup_direction = str(_score_meta.get("direction") or "").upper()
                    if _setup_direction in ("LONG", "SHORT"):
                        _trade_setup = _build_structured_trade_setup(
                            _setup_direction,
                            price,
                            price * (prev_atr_pct / 100.0),
                            day_low,
                            day_high,
                            day_high,
                            day_low,
                            close_pos * 100.0,
                        )
                        if _trade_setup:
                            strategy_row.update({
                                "Entry": _trade_setup["entry"],
                                "StopLoss": _trade_setup["stop"],
                                "TP1": _trade_setup["tp1"],
                                "TP2": _trade_setup["tp2"],
                                "entry": _trade_setup["entry"],
                                "stop_loss": _trade_setup["stop"],
                                "tp1": _trade_setup["tp1"],
                                "tp2": _trade_setup["tp2"],
                                "trade_setup": _trade_setup,
                                "Trade_Setup_Source": "stock_strategy_structure",
                            })
                    if _score_meta.get("direction") == "long" and _strat_grade in _ALERT_TOP_GRADES:
                        strategy_row.update(_fetch_long_latest_intraday_state(ticker))
                        strategy_row["long_entry_quality"] = _long_entry_quality(strategy_row)
                        strategy_row["alertable_long"] = not _long_entry_rule_reasons(strategy_row)
                    results.append(strategy_row)
                except Exception as item_err:
                    print(f"[Strategy Scan] {strategy_name}: skip {t.get('ticker', '?')} ({item_err})")
                    continue

        # Sortieren nach SCORE absteigend (nicht Change% — Score ist die Gesamtbewertung)
        results.sort(key=lambda x: (-x.get("score", 0), -abs(x.get("Change_Pct", 0))))
        results = _apply_special_strategy_post_filter(results, strat, strategy_name)
        results = results[:50]

        # V2.2: Separate Cache-Datei pro Strategie + Fallback auf generischen Cache
        _strat_cache = _strategy_cache_path(strategy_name)
        save_cache_file(_strat_cache, results)
        save_cache_file(STRATEGY_SCAN_CACHE, results)  # Fallback für alte Clients
        print(f"[Strategy Scan] {strategy_name}: {len(results)} Treffer -> {_strat_cache}")
        if send_email:
            _send_strategy_scan_alerts(strategy_name, results, "stocks")
        return results

    except Exception as e:
        print(f"[Strategy Scan] Fehler: {e}")
        import traceback
        traceback.print_exc()
        return []


def _stock_strategy_alert_sweep_wrapper() -> None:
    """Run core stock strategy alerts automatically.

    BI/Bear/Biotech/ORB already run on their own schedules. This sweep covers
    the generic stock-strategy mails that otherwise only happen after a manual
    strategy scan.
    """
    try:
        all_rows: List[Dict[str, Any]] = []
        summary = []
        for strategy_name in _AUTO_STOCK_ALERT_STRATEGIES:
            rows = _strategy_scan_wrapper(strategy_name, send_email=False) or []
            summary.append({"strategy": strategy_name, "rows": len(rows)})
            for row in rows[:25]:
                if not isinstance(row, dict):
                    continue
                enriched = dict(row)
                enriched.setdefault("Strategy", strategy_name)
                enriched.setdefault("strategy", strategy_name)
                all_rows.append(enriched)
            time.sleep(1)

        all_rows.sort(
            key=lambda x: (
                -float(x.get("score", x.get("Score", 0)) or 0),
                -abs(float(x.get("Change_Pct", x.get("change_pct", 0)) or 0)),
            )
        )
        save_cache_file(STRATEGY_SCAN_CACHE, all_rows[:100])
        print(f"[Strategy Sweep] {len(all_rows)} Kandidaten aus {len(summary)} Strategien: {summary}")
        _send_strategy_scan_alerts("Aktien Auto-Sweep", all_rows[:75], "stocks")
    except Exception as e:
        print(f"[Strategy Sweep] Fehler: {e}")
        import traceback
        traceback.print_exc()


def _crypto_strategy_scan_wrapper(strategy_name: str) -> None:
    """CoinGecko-basierter Scanner fuer generische Crypto-Strategien."""
    try:
        strat = CRYPTO_STRATEGIES.get(strategy_name)
        if not strat:
            print(f"[Crypto Strategy] Strategie '{strategy_name}' nicht gefunden")
            return

        filters = strat.get("filters", {})
        change_min, change_max = filters.get("Change %", (-999, 999))
        price_min, price_max = filters.get("Preis", (0, 999999999))
        rvol_min, rvol_max = filters.get("RVOL", (0, 999))
        close_pos_min, close_pos_max = filters.get("Close Position", (0, 1))
        mcap_min, mcap_max = filters.get("MarketCap", (0, 10**15))
        trend_min, trend_max = filters.get("Vortag %", (-999, 999))

        coins = _fetch_coingecko_markets(pages=8)
        cg_status = dict(_CG_MARKETS_STATUS)
        cg_partial = bool(cg_status.get("partial"))
        cg_source = cg_status.get("source") or "unknown"
        cg_warning = cg_status.get("warning")
        results = []
        btc_7d = 0.0
        for coin in coins:
            if coin.get("id") == "bitcoin":
                btc_7d = coin.get("price_change_percentage_7d_in_currency") or coin.get("price_change_percentage_7d") or 0
                break

        for coin in coins:
            try:
                cid = str(coin.get("id", "") or "")
                symbol = str(coin.get("symbol", "") or "").upper()
                name = str(coin.get("name", "") or "")
                price = float(coin.get("current_price") or 0)
                if not symbol or price <= 0:
                    continue
                if _is_excluded_crypto_asset(symbol, cid, name):
                    continue

                mcap = float(coin.get("market_cap") or 0)
                vol_24h = float(coin.get("total_volume") or 0)
                change_24h = float(coin.get("price_change_percentage_24h") or 0)
                change_7d = float(coin.get("price_change_percentage_7d_in_currency") or coin.get("price_change_percentage_7d") or 0)
                high_24h = float(coin.get("high_24h") or price)
                low_24h = float(coin.get("low_24h") or price)
                range_24h = high_24h - low_24h
                close_pos = _clamp_float((price - low_24h) / range_24h if range_24h > 0 else 0.5, 0.0, 1.0, 0.5)

                vol_mcap_ratio = (vol_24h / mcap * 100) if mcap > 0 else 0.0
                turnover_intensity = vol_mcap_ratio / 10.0  # 15% Vol/MCap ~= 1.5 turnover intensity
                trend_daily = change_7d / 7.0

                if not (change_min <= change_24h <= change_max):
                    continue
                if not (price_min <= price <= price_max):
                    continue
                if "MarketCap" in filters and not (mcap_min <= mcap <= mcap_max):
                    continue
                if "RVOL" in filters and not (rvol_min <= turnover_intensity <= rvol_max):
                    continue
                if "Close Position" in filters and not (close_pos_min <= close_pos <= close_pos_max):
                    continue
                if "Vortag %" in filters and not (trend_min <= trend_daily <= trend_max):
                    continue

                score = 35
                # Fresh but not chased beats raw FOMO.
                if 0 < change_24h <= 8:
                    score += 16
                elif 8 < change_24h <= 15:
                    score += 8
                elif change_24h > 20:
                    score -= 12
                elif change_24h < -10:
                    score -= 8

                if 15 <= vol_mcap_ratio <= 80:
                    score += 18
                elif 5 <= vol_mcap_ratio < 15:
                    score += 8
                elif vol_mcap_ratio > 150:
                    score -= 10

                if close_pos >= 0.75:
                    score += 12
                elif close_pos >= 0.55:
                    score += 6
                elif close_pos <= 0.25 and change_24h > 0:
                    score -= 8

                btc_alpha_7d = change_7d - btc_7d if btc_7d else change_7d
                if btc_alpha_7d > 10:
                    score += 10
                elif btc_alpha_7d > 3:
                    score += 5
                elif btc_alpha_7d < -15:
                    score -= 12

                # Strategy-specific quality nudges.
                key = _normalize_strategy_key(strategy_name)
                if "low_cap" in key or "rocket" in key:
                    if 5_000_000 <= mcap <= 500_000_000 and vol_24h >= 500_000:
                        score += 12
                    if mcap < 5_000_000:
                        score -= 18
                if "accumulation" in key:
                    if abs(change_24h) <= 2 and 12 <= vol_mcap_ratio <= 30:
                        score += 15
                    if abs(change_24h) > 5:
                        score -= 12
                if "bear" in key or "dip" in key or "reversal" in key:
                    if change_24h < 0:
                        score += 8

                score = max(0, min(100, int(round(score))))
                grade = _strategy_score_to_grade(score)
                risk_flags = ["coingecko_snapshot_only", "no_intraday_execution_trigger"]
                if cg_partial:
                    risk_flags.append("partial_crypto_data")
                results.append({
                    "Ticker": symbol,
                    "ticker": symbol,
                    "Name": name,
                    "ID": cid,
                    "Preis": round(price, 6),
                    "price": round(price, 6),
                    "Change_Pct": round(change_24h, 2),
                    "change_pct": round(change_24h, 2),
                    "Change7d": round(change_7d, 2),
                    "Volume": vol_24h,
                    "volume": vol_24h,
                    "MarketCap": round(mcap),
                    "VolMCapRatio": round(vol_mcap_ratio, 2),
                    "TurnoverIntensity": round(turnover_intensity, 2),
                    "RVOL": round(turnover_intensity, 2),
                    "rvol": round(turnover_intensity, 2),
                    "Close_Position": round(close_pos, 2),
                    "BtcRelative7d": round(btc_alpha_7d, 2),
                    "score": score,
                    "grade": grade,
                    "isCrypto": True,
                    "signal_quality": "observe",
                    "entry_status": "BEOBACHTEN",
                    "trade_action": "BEOBACHTEN",
                    "trade_signal": "BEOBACHTEN",
                    "signal_label": "Achtung beobachten: kein Entry ohne frischen Exchange-Trigger",
                    "execution_trigger_ok": False,
                    "alertable_crypto": False,
                    "risk_flags": risk_flags,
                    "data_status": "partial" if cg_partial else "ok",
                    "partial_data": cg_partial,
                    "data_warning": cg_warning,
                    "data_source": f"CoinGecko markets ({cg_source})",
                    "scanner_note": "Crypto-Strategie-Score ist Beobachtung, kein Entry. JETZT_TRADEN braucht einen frischen Micro-/Execution-Trigger.",
                    "volume_model": "TurnoverIntensity = Vol/MCap/10",
                })
            except Exception as item_err:
                print(f"[Crypto Strategy] skip {coin.get('symbol', '?')} ({item_err})")

        results.sort(key=lambda x: (-x.get("score", 0), -abs(x.get("change_pct", 0))))
        results = results[:80]
        _strat_cache = _strategy_cache_path(strategy_name, "crypto")
        save_cache_file(_strat_cache, results)
        print(f"[Crypto Strategy] {strategy_name}: {len(results)} Treffer -> {_strat_cache}")
        _send_strategy_scan_alerts(strategy_name, results, "crypto")
    except Exception as e:
        print(f"[Crypto Strategy] Fehler: {e}")
        import traceback
        traceback.print_exc()


TURTLE_CACHE = "/tmp/turtle_scan_cache.json"

def _turtle_scan_wrapper() -> None:
    """Turtle Trading Live-Scanner: Findet Aktien die ihr 20-Tage-Hoch durchbrechen.
    Original System 1 (Richard Dennis, 1983) — Donchian Channel Breakout."""
    try:
        print("[Turtle] Starte Turtle Breakout Scanner...")
        results = []

        # Full snapshot first: Turtle breakouts can emerge from quiet bases, not only top gainers.
        _all_tickers = []
        try:
            url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY}, timeout=30)
            if resp.status_code == 200:
                _all_tickers.extend(resp.json().get("tickers", []))
        except Exception:
            pass

        if len(_all_tickers) < 250:
            for endpoint in ["gainers", "losers"]:
                try:
                    url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/{endpoint}"
                    resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 250})
                    if resp.status_code == 200:
                        _all_tickers.extend(resp.json().get("tickers", []))
                except Exception:
                    pass

        # Deduplizieren
        _seen = set()
        _unique = []
        for t in _all_tickers:
            sym = t.get("ticker", "")
            if sym not in _seen:
                _seen.add(sym)
                _unique.append(t)
        _all_tickers = _unique

        print(f"[Turtle] {len(_all_tickers)} Aktien im Snapshot")

        # ── 2. Vorfilter: Preis $5+, Change > 0%, kein OTC ──
        _common_stock_universe, _common_stock_source = _load_common_stock_universe()
        candidates = []
        for t in _all_tickers:
            ticker = t.get("ticker", "")
            if not ticker or "." in ticker or len(ticker) > 5:
                continue  # OTC / Warrants raus
            if _stock_alert_asset_exclusion_reason(
                ticker,
                common_stock_universe=_common_stock_universe,
                universe_source=_common_stock_source,
                require_reference=_common_stock_universe is None,
            ):
                continue
            day = t.get("day", {})
            prev = t.get("prevDay", {})
            price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
            prev_close = prev.get("c", 0)
            if price < 5 or prev_close <= 0:
                continue
            change_pct = (price - prev_close) / prev_close * 100
            if change_pct < -0.35:
                continue
            volume = day.get("v", 0)
            dollar_volume = volume * price
            if dollar_volume < 1_000_000:
                continue
            day_high = day.get("h", price) or price
            day_low = day.get("l", price) or price
            day_open = day.get("o", prev_close) or prev_close
            close_pos = (price - day_low) / max(day_high - day_low, 1e-9)
            if price < day_open * 0.995 and close_pos < 0.55:
                continue
            priority = (change_pct * 1.4) + (close_pos * 2.0) + min(2.0, dollar_volume / 50_000_000)
            candidates.append((ticker, t, price, prev_close, change_pct, volume, priority))

        print(f"[Turtle] {len(candidates)} Kandidaten nach Vorfilter")

        # Broad candidate pool by breakout quality, not raw FOMO.
        candidates.sort(key=lambda x: -x[6])
        candidates = candidates[:250]

        from datetime import timedelta
        _today = datetime.now()
        _from = (_today - timedelta(days=45)).strftime("%Y-%m-%d")
        _to = _today.strftime("%Y-%m-%d")

        for ticker, snap_data, price, prev_close, change_pct, volume, priority in candidates:
            try:
                # 30 Tage Daily Bars holen (brauchen 21+ für Donchian 20)
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{_from}/{_to}"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 35, "sort": "asc"})
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if len(bars) < 22:
                    continue  # Brauchen mindestens 22 Bars (20 History + aktueller + 1 für ATR)

                highs = [b.get("h", 0) for b in bars]
                lows = [b.get("l", 0) for b in bars]
                closes = [b.get("c", 0) for b in bars]
                volumes = [b.get("v", 0) for b in bars]

                # Letzter Bar = heute (oder letzter Handelstag)
                i = len(bars) - 1

                # ── Donchian Channel High (20 Tage OHNE aktuellen Tag) ──
                dc_high_20 = max(highs[max(0, i - 20):i])
                dc_low_10 = min(lows[max(0, i - 10):i])

                # ── ATR(20) als EMA ──
                atr = 0.0
                for k in range(1, len(bars)):
                    tr = max(
                        highs[k] - lows[k],
                        abs(highs[k] - closes[k - 1]),
                        abs(lows[k] - closes[k - 1]),
                    )
                    if k == 1:
                        atr = tr
                    elif k <= 20:
                        atr = ((k - 1) * atr + tr) / k  # Seed mit laufendem Avg
                    else:
                        atr = (19.0 * atr + tr) / 20.0  # EMA

                # ── BREAKOUT CHECK: Close > 20-Day High ──
                current_close = closes[i]
                if current_close <= dc_high_20 or atr <= 0:
                    continue  # Kein Breakout

                # ── Turtle Levels berechnen ──
                entry_price = dc_high_20  # Theoretischer Entry am Breakout-Level
                stop_loss = entry_price - 2.0 * atr
                exit_level = dc_low_10  # 10-Tage-Tief = Exit
                risk_per_share = entry_price - stop_loss
                reward_to_exit = current_close - entry_price

                # Breakout-Stärke: Wie weit über dem 20-Day-High?
                breakout_pct = (current_close - dc_high_20) / dc_high_20 * 100

                # ── Scoring (0-100) ──
                score = 0

                # Breakout-Stärke (0-25): Frischer Breakout besser als überschossener
                if breakout_pct < 1.0:
                    score += 25  # Ideal: Gerade erst durchgebrochen
                elif breakout_pct < 3.0:
                    score += 20
                elif breakout_pct < 5.0:
                    score += 12
                else:
                    score += 5  # Zu weit weg — späte Entry

                # Volume Confirmation (0-25)
                avg_vol_20 = sum(volumes[max(0, i - 20):i]) / min(20, max(1, i))
                rvol = volumes[i] / avg_vol_20 if avg_vol_20 > 0 else 1.0
                if rvol >= 2.5:
                    score += 25
                elif rvol >= 1.5:
                    score += 18
                elif rvol >= 1.0:
                    score += 10
                else:
                    score += 3

                # Trend-Stärke (0-20): Aufwärtstrend über 20 Tage
                if len(closes) >= 21:
                    trend_pct = (closes[i] - closes[i - 20]) / closes[i - 20] * 100
                    if trend_pct > 15:
                        score += 20
                    elif trend_pct > 8:
                        score += 15
                    elif trend_pct > 3:
                        score += 10
                    elif trend_pct > 0:
                        score += 5

                # ATR-Qualität (0-15): Nicht zu volatil, nicht zu eng
                atr_pct = atr / current_close * 100
                if 1.0 <= atr_pct <= 4.0:
                    score += 15  # Sweet Spot
                elif 0.5 <= atr_pct <= 6.0:
                    score += 10
                else:
                    score += 3

                # Entry-Qualität (0-15): Frischer Breakout = bester Entry
                # Je NÄHER am Breakout-Level, desto besser das R/R-Potenzial
                if risk_per_share > 0:
                    # Wie weit über Entry sind wir schon? (0% = perfekt, >10% = zu spät)
                    overshoot = (current_close - entry_price) / entry_price * 100
                    if overshoot < 1.0:
                        score += 15  # Ideal: Gerade erst durchgebrochen
                    elif overshoot < 3.0:
                        score += 12  # Noch gut
                    elif overshoot < 5.0:
                        score += 7   # Moderat überschossen
                    else:
                        score += 2   # Zu weit — Entry riskant

                raw_score = min(100, score)
                score, turtle_quality_flags = _turtle_score_cap(raw_score, change_pct, rvol, breakout_pct)
                grade = _strategy_score_to_grade(score)

                results.append({
                    "Ticker": ticker,
                    "Preis": round(current_close, 2),
                    "Change_Pct": round(change_pct, 2),
                    "DC_High_20": round(dc_high_20, 2),
                    "DC_Low_10": round(dc_low_10, 2),
                    "Breakout_Pct": round(breakout_pct, 2),
                    "ATR": round(atr, 2),
                    "ATR_Pct": round(atr / current_close * 100, 2),
                    "Entry": round(entry_price, 2),
                    "Stop": round(stop_loss, 2),
                    "Exit_Level": round(exit_level, 2),
                    "Risk": round(risk_per_share, 2),
                    "RVOL": round(rvol, 2),
                    "Volume": volume,
                    "Dollar_Volume": round(volume * current_close),
                    "raw_score": round(raw_score, 2),
                    "score": score,
                    "grade": grade,
                    "turtle_quality_flags": turtle_quality_flags,
                    "score_details": f"Donchian {breakout_pct:.2f}% over 20D high | RVOL {rvol:.2f}x | ATR {atr_pct:.2f}%",
                    "signal": f"Breakout +{breakout_pct:.1f}% über 20T-Hoch | Stop ${stop_loss:.2f} (2×ATR) | Exit ${exit_level:.2f} (10T-Tief)",
                })

            except Exception as e:
                continue

        # Sortieren: Score absteigend
        results.sort(key=lambda x: -x["score"])
        results = results[:50]

        print(f"[Turtle] {len(results)} Breakout-Signale gefunden")
        save_cache_file(TURTLE_CACHE, results)

    except Exception as e:
        print(f"[Turtle] Scanner Fehler: {e}")
        import traceback
        traceback.print_exc()


def _bear_scan_wrapper() -> None:
    """Run bear scanner in background — finds inverse ETF opportunities and breakdown stocks."""
    try:
        result = {
            "inverse_etfs": [],
            "short_candidates": [],
            "breakdown_stocks": [],
            "diagnostics": {
                "scanner": "bear",
                "raw_candidates": 0,
                "processed_common_stocks": 0,
                "excluded_non_common": 0,
                "price_or_prev_close_filtered": 0,
                "dollar_volume_filtered": 0,
                "drop_filtered": 0,
                "history_missing": 0,
                "history_fetch_errors": 0,
                "extended_hours": False,
            },
        }

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

                # Determine leverage from name
                leverage = 1.0
                if "3x" in desc.upper():
                    leverage = 3.0
                elif "2x" in desc.upper():
                    leverage = 2.0
                elif "1.5x" in desc.upper():
                    leverage = 1.5
                elif "1x" not in desc.upper():  # Default to 1x if no leverage specified
                    # Check if it's inverse (has decay risk)
                    if "short" in desc.lower() or "inverse" in desc.lower():
                        leverage = 1.0

                # Build item dict
                item = {
                    "ticker": ticker, "name": desc, "underlying": underlying,
                    "price": round(close, 2), "change_1d": round(chg_1d, 2),
                    "change_5d": round(chg_5d, 2), "change_20d": round(chg_20d, 2),
                    "volume": vol, "rvol": rvol, "signal": signal,
                }

                # Add decay warning for leveraged/inverse ETFs
                if leverage > 1.0 or "inverse" in desc.lower():
                    item["decay_warning"] = True
                    item["decay_note"] = f"{leverage:.1f}x Hebel — Langfristig Wertverlust durch tägliches Rebalancing"
                else:
                    item["decay_warning"] = False

                result["inverse_etfs"].append(item)
            except Exception as e:
                print(f"[Warning] Error processing inverse ETF {ticker}: {e}")
                continue

        result["inverse_etfs"].sort(key=lambda x: x.get("change_5d", 0), reverse=True)

        # --- Section 2: Breakdown stocks V2.2 — mit Score/Grade System ---
        # V3.4 FIX: AH/PM-fähig — Full Snapshot nutzen wenn Losers-Endpoint leer (AH/PM)
        try:
            snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/losers"
            snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY, "limit": 250})

            losers = []
            _raw_tickers = []
            _is_extended_hours = False
            _diagnostics = result.setdefault("diagnostics", {})
            _diagnostics["losers_http_status"] = snap_resp.status_code
            if snap_resp.status_code == 200:
                _raw_tickers = snap_resp.json().get("tickers", [])
                _diagnostics["losers_endpoint_count"] = len(_raw_tickers)

            # V3.4: Wenn Losers-Endpoint wenig/keine Ergebnisse → Extended Hours
            # Full Snapshot holen und lastTrade vs day.close vergleichen
            if len(_raw_tickers) < 10:
                print(f"[Bear] Losers endpoint nur {len(_raw_tickers)} Ticker — Extended Hours Modus")
                _is_extended_hours = True
                _diagnostics["extended_hours"] = True
                try:
                    _full_snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
                    _full_resp = rate_limited_get(_full_snap_url, params={"apiKey": POLYGON_KEY}, timeout=30)
                    _diagnostics["full_snapshot_http_status"] = _full_resp.status_code
                    if _full_resp.status_code == 200:
                        _all = _full_resp.json().get("tickers", [])
                        # Finde AH/PM Losers: lastTrade.p vs day.c (Regular Close)
                        _ah_losers = []
                        for _t in _all:
                            try:
                                _lt = _t.get("lastTrade", {}).get("p", 0)
                                _dc = _t.get("day", {}).get("c", 0)
                                if not _lt or not _dc or _dc <= 0:
                                    continue
                                _ah_chg = ((_lt - _dc) / _dc) * 100
                                # V3.4: Min $3 Preis + Min $50k Dollar-Volume AH
                                _ah_vol = _t.get("day", {}).get("v", 0)
                                _ah_dv = _lt * _ah_vol if _ah_vol else 0
                                if _ah_chg < -3 and _lt >= 3 and _ah_dv >= 50_000:
                                    _t["_ah_change_pct"] = _ah_chg
                                    _t["_ah_price"] = _lt
                                    _ah_losers.append(_t)
                            except Exception:
                                continue
                        _ah_losers.sort(key=lambda x: x.get("_ah_change_pct", 0))
                        _raw_tickers = _ah_losers[:250]
                        print(f"[Bear] Extended Hours: {len(_raw_tickers)} AH/PM Losers gefunden")
                except Exception as _ext_err:
                    print(f"[Bear] Extended Hours fetch failed: {_ext_err}")
                    _diagnostics["extended_hours_error"] = str(_ext_err)

            _diagnostics["raw_candidates"] = len(_raw_tickers)

            if _raw_tickers:
                tickers = _raw_tickers
                print(f"[Bear] Processing {len(tickers)} tickers (extended={_is_extended_hours})")
                _common_stock_universe, _common_stock_source = _load_common_stock_universe()
                _excluded_non_stock = 0
                _diagnostics["common_stock_source"] = _common_stock_source
                for t in tickers:
                    try:
                        day = t.get("day", {})
                        prev = t.get("prevDay", {})
                        # V3.4: Bei Extended Hours → AH-Preis und AH-Change nutzen
                        if _is_extended_hours and t.get("_ah_price"):
                            price = t["_ah_price"]
                            prev_close = day.get("c", 0) or prev.get("c", 0)  # Vergleich vs Regular Close
                            chg_pct = t.get("_ah_change_pct", 0)
                        else:
                            price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
                            prev_close = prev.get("c", 0)
                            chg_pct = ((price - prev_close) / prev_close) * 100 if prev_close else 0
                        if not price or not prev_close or price < 3:
                            _diagnostics["price_or_prev_close_filtered"] = int(_diagnostics.get("price_or_prev_close_filtered", 0) or 0) + 1
                            continue
                        vol = day.get("v", 0)
                        dollar_vol = price * vol
                        if dollar_vol < 300_000 and not _is_extended_hours:
                            _diagnostics["dollar_volume_filtered"] = int(_diagnostics.get("dollar_volume_filtered", 0) or 0) + 1
                            continue
                        if chg_pct > -3:
                            _diagnostics["drop_filtered"] = int(_diagnostics.get("drop_filtered", 0) or 0) + 1
                            continue
                        day_open = day.get("o", 0) or prev_close
                        day_high = day.get("h", 0) or max(price, day_open)
                        day_low = day.get("l", 0) or min(price, day_open)
                        open_to_current_pct = ((price - day_open) / day_open * 100) if day_open else None
                        close_pos = ((price - day_low) / (day_high - day_low)) if day_high > day_low else 0.5

                        ticker_sym = t.get("ticker", "")
                        # V2.6b: ETF/ETP/Leveraged Filter — keine ETFs in Breakdown-Stocks
                        _tk_up = ticker_sym.upper()
                        non_stock_reason = _stock_alert_asset_exclusion_reason(
                            _tk_up,
                            common_stock_universe=_common_stock_universe,
                            universe_source=_common_stock_source,
                            require_reference=_common_stock_universe is None,
                        )
                        if non_stock_reason:
                            _excluded_non_stock += 1
                            _diagnostics["excluded_non_common"] = _excluded_non_stock
                            continue
                        rvol = 0
                        ma20 = 0
                        ma50 = 0
                        ma20_dist = 0
                        ma50_dist = 0
                        low_20d = None
                        low_60d = None
                        has_history = False

                        try:
                            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker_sym}/range/1/day/2024-01-01/2099-12-31"
                            resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 60, "sort": "desc"})
                            if resp.status_code == 200:
                                bars = resp.json().get("results", [])
                                if len(bars) >= 21:
                                    has_history = True
                                    ma20 = sum(b.get("c", 0) for b in bars[1:21]) / 20
                                    ma20_dist = round((price - ma20) / ma20 * 100, 2) if ma20 > 0 else 0
                                    if len(bars) >= 51:
                                        ma50 = sum(b.get("c", 0) for b in bars[1:51]) / 50
                                        ma50_dist = round((price - ma50) / ma50 * 100, 2) if ma50 > 0 else 0
                                    else:
                                        ma50 = None  # Nicht genug Daten — NICHT mit ma20 gleichsetzen
                                        ma50_dist = 0

                                    avg_vol = sum(b.get("v", 0) for b in bars[1:21]) / min(20, len(bars) - 1)
                                    rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0
                                    lows_20 = [b.get("l", b.get("c", 0)) for b in bars[1:21] if b.get("l", b.get("c", 0)) > 0]
                                    lows_60 = [b.get("l", b.get("c", 0)) for b in bars[1:60] if b.get("l", b.get("c", 0)) > 0]
                                    low_20d = min(lows_20) if lows_20 else None
                                    low_60d = min(lows_60) if lows_60 else None
                        except Exception as e:
                            print(f"[Bear] History failed for {ticker_sym}: {e}")
                            _diagnostics["history_fetch_errors"] = int(_diagnostics.get("history_fetch_errors", 0) or 0) + 1

                        # V2.2: Ohne History-Daten → überspringen (kein Blindflug)
                        if not has_history:
                            _diagnostics["history_missing"] = int(_diagnostics.get("history_missing", 0) or 0) + 1
                            continue

                        # ── Scoring System (0-100) ──
                        score = 0
                        score_details = []

                        # 1. Change Magnitude (0-25): Je stärker der Drop, desto besser
                        abs_chg = abs(chg_pct)
                        if abs_chg >= 15:
                            score += 25; score_details.append(f"Drop {chg_pct:.1f}% (extrem)")
                        elif abs_chg >= 10:
                            score += 20; score_details.append(f"Drop {chg_pct:.1f}% (stark)")
                        elif abs_chg >= 6:
                            score += 15; score_details.append(f"Drop {chg_pct:.1f}%")
                        elif abs_chg >= 4:
                            score += 10; score_details.append(f"Drop {chg_pct:.1f}% (moderat)")
                        else:
                            score += 5

                        # 2. RVOL (0-20): Hohes Vol bestätigt den Move
                        if rvol >= 3.0:
                            score += 20; score_details.append(f"RVOL {rvol:.1f}x (extrem)")
                        elif rvol >= 2.0:
                            score += 15; score_details.append(f"RVOL {rvol:.1f}x (stark)")
                        elif rvol >= 1.5:
                            score += 10; score_details.append(f"RVOL {rvol:.1f}x")
                        elif rvol >= 1.0:
                            score += 5; score_details.append(f"RVOL {rvol:.1f}x (normal)")
                        else:
                            score_details.append(f"RVOL {rvol:.1f}x (schwach)")

                        # 3. MA20 Trend (0-20): Unter MA20 = bestätigter Downtrend
                        # V3.4 FIX: Kein Abzug mehr wenn über MA20 — bei frischen Breakdowns
                        # ist der Preis oft noch über MA20 (gerade erst gedroppt)
                        if ma20_dist < -10:
                            score += 20; score_details.append(f"MA20 {ma20_dist:.1f}% (weit darunter)")
                        elif ma20_dist < -5:
                            score += 15; score_details.append(f"MA20 {ma20_dist:.1f}%")
                        elif ma20_dist < -2:
                            score += 10; score_details.append(f"MA20 {ma20_dist:.1f}%")
                        elif ma20_dist < 0:
                            score += 5
                        elif ma20_dist < 5:
                            score += 0; score_details.append(f"Knapp über MA20 ({ma20_dist:+.1f}%)")
                        else:
                            score_details.append(f"Weit über MA20 ({ma20_dist:+.1f}%) — Vorsicht")

                        # 4. MA50 Trend (0-15) — nur wenn genug Daten (>=51 Bars)
                        if ma50 is not None:
                            if ma50_dist < -10:
                                score += 15
                            elif ma50_dist < -5:
                                score += 10
                            elif ma50_dist < 0:
                                score += 5
                        # Kein Score wenn ma50 nicht berechenbar (zu wenig History)

                        # 5. Dollar Volume Quality (0-10)
                        if dollar_vol >= 10_000_000:
                            score += 10; score_details.append("$Vol >10M")
                        elif dollar_vol >= 5_000_000:
                            score += 7
                        elif dollar_vol >= 1_000_000:
                            score += 4
                        else:
                            score += 1

                        # 6. Price Quality (0-10): $10-$200 = ideal für Shorts
                        if 10 <= price <= 200:
                            score += 10
                        elif 5 <= price < 10:
                            score += 5
                        elif price > 200:
                            score += 7  # Teuer aber shortbar

                        # ── Grade (V3.4: Recalibriert — vorher war S/A fast unerreichbar) ──
                        if score >= 70:
                            grade = "S"
                        elif score >= 55:
                            grade = "A"
                        elif score >= 40:
                            grade = "B"
                        elif score >= 25:
                            grade = "C"
                        else:
                            grade = "D"

                        bear_row = {
                            "ticker": ticker_sym,
                            "price": round(price, 2),
                            "change_pct": round(chg_pct, 2),
                            "volume": vol,
                            "dollar_volume": round(dollar_vol, 0),
                            "rvol": rvol,
                            "ma20_dist": ma20_dist,
                            "ma50_dist": ma50_dist,
                            "score": score,
                            "grade": grade,
                            "direction": "SHORT",
                            "open_to_current_pct": round(open_to_current_pct, 2) if open_to_current_pct is not None else None,
                            "close_pos": round(close_pos, 3),
                            "DayHigh": round(day_high, 4) if day_high else None,
                            "DayLow": round(day_low, 4) if day_low else None,
                            "score_details": " | ".join(score_details),
                            "asset_check": "common_stock",
                        }
                        trade_setup = _build_bear_structure_trade_setup(
                            entry=price,
                            day_high=day_high,
                            day_low=day_low,
                            day_open=day_open,
                            ma20=ma20,
                            ma50=ma50,
                            low_20d=low_20d,
                            low_60d=low_60d,
                            change_pct=chg_pct,
                        )
                        if trade_setup:
                            bear_row.update(trade_setup)
                        if score >= 55:
                            bear_row.update(_fetch_bear_latest_intraday_state(ticker_sym))
                        bear_row["short_block_reasons"] = _bear_short_rule_reasons(bear_row)
                        bear_row["entry_quality"] = _bear_entry_quality(bear_row)
                        bear_row["alertable_short"] = not bear_row["short_block_reasons"]
                        bear_row["crash_alert_ok"] = _bear_crash_alert_ok(bear_row)
                        losers.append(bear_row)
                        _diagnostics["processed_common_stocks"] = int(_diagnostics.get("processed_common_stocks", 0) or 0) + 1
                    except Exception as e:
                        print(f"[Warning] Error processing breakdown stock: {e}")
                        continue
                losers.sort(key=lambda x: x.get("score", 0), reverse=True)
                result["breakdown_stocks"] = losers[:30]
                _diagnostics["breakdown_count"] = len(result["breakdown_stocks"])
                result["asset_filter"] = {
                    "source": _common_stock_source,
                    "excluded_non_common": _excluded_non_stock,
                }
                print(f"[Bear] Final breakdown_stocks: {len(losers[:30])} (excluded_non_common={_excluded_non_stock}, source={_common_stock_source})")
        except Exception as e:
            print(f"Breakdown stocks error: {e}")
            result.setdefault("diagnostics", {})["breakdown_error"] = str(e)

        # Save the latest scan snapshot, including diagnostics when no stock passes filters.
        result.setdefault("diagnostics", {})["breakdown_count"] = len(result.get("breakdown_stocks", []))
        result.setdefault("diagnostics", {})["inverse_etf_count"] = len(result.get("inverse_etfs", []))
        if not result.get("breakdown_stocks"):
            result["diagnostics"]["no_stock_reason"] = _bear_empty_warning_from_results([result])
        has_stock_data = len(result.get("breakdown_stocks", [])) > 0 or len(result.get("inverse_etfs", [])) > 0
        if has_stock_data:
            save_cache_file(BEAR_CACHE, [result])
            print(f"[Bear] Saved {len(result.get('inverse_etfs',[]))} ETFs, {len(result.get('breakdown_stocks',[]))} breakdowns")
            # V2.2: Bear Alert — vollständige Infos pro Signal
            _bd_rows = []
            for bd in result.get("breakdown_stocks", []):
                if isinstance(bd, dict) and bd.get("grade") in ("S", "A") and bd.get("score", 0) >= 55:
                    _gr = bd.get("grade", "?")
                    _gc = {"S": "#7c3aed", "A": "#16a34a", "B": "#2563eb", "C": "#ca8a04"}.get(_gr, "#666")
                    _bd_rows.append(
                        f"<tr><td style='padding:4px 8px;font-weight:bold;color:{_gc}'>{_gr}</td>"
                        f"<td style='padding:4px 8px;font-weight:bold'>{bd.get('ticker','?')}</td>"
                        f"<td style='padding:4px 8px;text-align:right'>${bd.get('price',0):.2f}</td>"
                        f"<td style='padding:4px 8px;text-align:right;color:#dc2626'>{bd.get('change_pct',0):.1f}%</td>"
                        f"<td style='padding:4px 8px;text-align:right'>{bd.get('rvol',0):.1f}x</td>"
                        f"<td style='padding:4px 8px;text-align:right'>{bd.get('ma20_dist',0):.1f}%</td>"
                        f"<td style='padding:4px 8px;text-align:right;font-weight:bold'>{bd.get('score',0)}</td></tr>"
                    )
            # V2.6b: Crash-Flash — EINE Sammel-Mail pro Tag, nur Grade S/A, keine ETFs/ETPs
            _ETF_KEYWORDS = {"etf", "etp", "leveraged", "inverse", "ultra", "proshares", "direxion", "amplify", "graniteshares"}
            _crash_stocks = []
            _crash_level_tickers = set()
            for bd in result.get("breakdown_stocks", []):
                if not isinstance(bd, dict):
                    continue
                _cs_ticker = bd.get("ticker", "")
                _cs_chg = bd.get("change_pct", 0)
                _crash_state = _classify_crash_alert_candidate(bd)
                # Nur echte, aktuelle Crash-Setups: zentraler Alert-Gate inklusive Asset, Plan und Trade-Health.
                if _cs_chg > -10 or not _crash_state.get("alertable_now"):
                    continue
                # ETF/ETP Filter — Ticker-Heuristik (3+ gleiche Buchstaben am Ende = oft ETF)
                _cs_tk_up = _cs_ticker.upper()
                if len(_cs_tk_up) >= 4 and _cs_tk_up[-1] in ("X", "Q", "S") and _cs_tk_up[-2] in ("X", "Q", "S"):
                    continue  # SOXS, SQQQ, SPXS, UVXY etc.
                _crash_level_tickers.add(_cs_tk_up)
                _crash_stocks.append(bd)

            if _crash_stocks:
                _crash_date = datetime.now().strftime('%Y%m%d')
                _fresh_crash_stocks = []
                _crash_dedupe_keys = []
                for _cs in _crash_stocks:
                    _ticker = str(_cs.get("ticker", "?")).upper()
                    _dedupe_key = f"crash_stock_{_crash_date}_{_ticker}"
                    if not _email_dedupe_active(_dedupe_key, _CRASH_ALERT_DEDUPE_SEC):
                        _fresh_crash_stocks.append(_cs)
                        _crash_dedupe_keys.append(_dedupe_key)
                    else:
                        print(f"[Bear] CRASH Alert skipped by persistent dedupe: {_ticker}")
                _crash_stocks = _fresh_crash_stocks
            else:
                _crash_dedupe_keys = []

            if _crash_stocks:
                _crash_ck = f"crash_summary_{datetime.now().strftime('%Y%m%d')}"
                if _crash_ck not in _EMAIL_COOLDOWN:
                    _crash_rows = ""
                    for _cs in _crash_stocks[:5]:  # Max 5 in einer Mail
                        _gc = {"S": "#7c3aed", "A": "#16a34a"}.get(_cs.get("grade", ""), "#666")
                        _crash_rows += (
                            f"<tr><td style='padding:6px 8px;font-weight:bold;color:{_gc}'>{_cs.get('grade','?')}</td>"
                            f"<td style='padding:6px 8px;font-weight:bold'>{_cs.get('ticker','?')}</td>"
                            f"<td style='padding:6px 8px;text-align:right'>${_cs.get('price',0):.2f}</td>"
                            f"<td style='padding:6px 8px;text-align:right;color:#dc2626;font-weight:bold'>{_cs.get('change_pct',0):.1f}%</td>"
                            f"<td style='padding:6px 8px;text-align:right'>{_cs.get('rvol',0):.1f}x</td>"
                            f"<td style='padding:6px 8px;text-align:left'>{_format_alert_plan_html(_cs)}</td>"
                            f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{_cs.get('score',0)}</td></tr>"
                        )
                    _crash_body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
                    <h2 style="color:#dc2626">⚠️ Crash Alert — {len(_crash_stocks)} Aktien</h2>
                    <p style="color:#666;font-size:13px">{datetime.now().strftime('%d.%m.%Y %H:%M')} UTC</p>
                    <table style="border-collapse:collapse;width:100%;font-size:13px">
                    <tr style="background:#fef2f2"><th style="padding:6px 8px;text-align:left">Grd</th>
                    <th style="padding:6px 8px;text-align:left">Ticker</th>
                    <th style="padding:6px 8px;text-align:right">Preis</th>
                    <th style="padding:6px 8px;text-align:right">Drop</th>
                    <th style="padding:6px 8px;text-align:right">RVOL</th>
                    <th style="padding:6px 8px;text-align:left">Entry / Stop / TP</th>
                    <th style="padding:6px 8px;text-align:right">Score</th></tr>
                    {_crash_rows}</table>
                    </body></html>'''
                    _send_email_alert(f"⚠️ CRASH: {len(_crash_stocks)} Aktien ({_crash_stocks[0].get('ticker','?')} {_crash_stocks[0].get('change_pct',0):.0f}%)", _crash_body)
                    if _EMAIL_SEND_LOG and _EMAIL_SEND_LOG[-1].get("status") == "sent":
                        _EMAIL_COOLDOWN[_crash_ck] = time.time()
                        for _dedupe_key in _crash_dedupe_keys:
                            _email_dedupe_mark(_dedupe_key)
                        for _cs in _crash_stocks:
                            _mark_bearish_stock_alert(_cs.get("ticker", ""), now=time.time())
                        print(f"[Bear] CRASH SUMMARY sent: {[c.get('ticker') for c in _crash_stocks]}")
            elif result.get("breakdown_stocks"):
                _record_email_event(
                    "Crash Alert",
                    "skipped",
                    "no_active_crash_stock:"
                    + _crash_alert_suppression_summary_for_rows(result.get("breakdown_stocks", [])),
                )

            # V2.6b: Bear Summary Email — 1x pro Tag, nur wenn Grade S/A Signale dabei
            _bd_rows = []
            _bear_summary_tickers = []
            for bd in result.get("breakdown_stocks", []):
                if not isinstance(bd, dict):
                    continue
                _bear_state = _classify_alert_candidate("bear", bd)
                if not _bear_state.get("alertable_now"):
                    continue
                _ticker_up = str(bd.get("ticker", "")).upper()
                if _ticker_up in _crash_level_tickers or _bearish_stock_alert_remaining(_ticker_up) > 0:
                    continue
                _gr = bd.get("grade", "?")
                _gc = {"S": "#7c3aed", "A": "#16a34a", "B": "#2563eb", "C": "#ca8a04"}.get(_gr, "#666")
                _bear_summary_tickers.append(_ticker_up)
                _bd_rows.append(
                    f"<tr><td style='padding:4px 8px;font-weight:bold;color:{_gc}'>{_gr}</td>"
                    f"<td style='padding:4px 8px;font-weight:bold'>{bd.get('ticker','?')}</td>"
                    f"<td style='padding:4px 8px;text-align:right'>${bd.get('price',0):.2f}</td>"
                    f"<td style='padding:4px 8px;text-align:right;color:#dc2626'>{bd.get('change_pct',0):.1f}%</td>"
                    f"<td style='padding:4px 8px;text-align:right'>{bd.get('rvol',0):.1f}x</td>"
                    f"<td style='padding:4px 8px;text-align:right'>{bd.get('ma20_dist',0):.1f}%</td>"
                    f"<td style='padding:4px 8px;text-align:left'>{_format_alert_plan_html(bd)}</td>"
                    f"<td style='padding:4px 8px;text-align:right'>{bd.get('entry_quality','?')}</td>"
                    f"<td style='padding:4px 8px;text-align:right;font-weight:bold'>{bd.get('score',0)}</td></tr>"
                )
            _total_signals = len(_bd_rows)
            if _total_signals > 0:
                _bear_ck = f"bear_summary_{datetime.now().strftime('%Y%m%d')}"
                if _bear_ck not in _EMAIL_COOLDOWN:
                    _ts = f"<p style='color:#666;font-size:13px'>{datetime.now().strftime('%d.%m.%Y %H:%M')} UTC | {_total_signals} Aktien-Shorts</p>"
                    _bd_html = ""
                    if _bd_rows:
                        _bd_html = (
                            "<h3 style='color:#dc2626;margin-top:16px'>Short-Kandidaten (Grade S/A)</h3>"
                            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
                            "<tr style='background:#fef2f2;border-bottom:1px solid #ddd'>"
                            "<th style='padding:6px 8px;text-align:left'>Grd</th>"
                            "<th style='padding:6px 8px;text-align:left'>Ticker</th>"
                            "<th style='padding:6px 8px;text-align:right'>Preis</th>"
                            "<th style='padding:6px 8px;text-align:right'>Chg%</th>"
                            "<th style='padding:6px 8px;text-align:right'>RVOL</th>"
                            "<th style='padding:6px 8px;text-align:right'>MA20</th>"
                            "<th style='padding:6px 8px;text-align:left'>Entry / Stop / TP</th>"
                            "<th style='padding:6px 8px;text-align:right'>Timing</th>"
                            "<th style='padding:6px 8px;text-align:right'>Score</th></tr>"
                            + "".join(_bd_rows) + "</table>"
                        )
                    _bear_body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
                    <h2 style="color:#dc2626">Bear Scanner Alert</h2>
                    {_ts}{_bd_html}
                    <p style="color:#999;font-size:11px;margin-top:12px">Nur handelbare Breakdown-Shorts: Drop -3% bis -12%, keine grüne Reclaim-Kerze, Close nahe Tagestief. Überdehnte Crashs bleiben Watch/Crash-Monitor, kein FOMO-Short.</p>
                    </body></html>'''
                    _send_email_alert(f"Bear Alert: {_total_signals} Aktien-Shorts", _bear_body)
                    if _EMAIL_SEND_LOG and _EMAIL_SEND_LOG[-1].get("status") == "sent":
                        _EMAIL_COOLDOWN[_bear_ck] = time.time()
                        for _ticker in _bear_summary_tickers:
                            _mark_bearish_stock_alert(_ticker, now=time.time())
            elif result.get("breakdown_stocks"):
                _record_email_event(
                    "Bear Scanner Alert",
                    "skipped",
                    "no_alertable_short_setup:"
                    + _alert_suppression_summary_for_rows("bear", result.get("breakdown_stocks", [])),
                )
        else:
            print(f"[Bear] No data (market closed/weekend?) — keeping previous cache")
    except Exception as e:
        print(f"Bear scanner error: {e}")
        import traceback
        traceback.print_exc()


# ── Background Scheduler ──
# Runs all scans automatically at defined intervals (like old Streamlit version)
_scheduler_running = False
_scan_status = {
    "bi_long": {"running": False, "last_run": None, "next_run": None, "interval_min": 180},
    "bi_short": {"running": False, "last_run": None, "next_run": None, "interval_min": 180},
    "bear": {"running": False, "last_run": None, "next_run": None, "interval_min": 15},
    "biotech": {"running": False, "last_run": None, "next_run": None, "interval_min": 240},
    "early_movers": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "crash_monitor": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "market_context": {"running": False, "last_run": None, "next_run": None, "interval_min": 15},
    "btc_divergenz": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "money_flow": {"running": False, "last_run": None, "next_run": None, "interval_min": 60},
    "new_listing": {"running": False, "last_run": None, "next_run": None, "interval_min": 15},
    "volume_spikes": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "orb": {"running": False, "last_run": None, "next_run": None, "interval_min": 5},
    "turtle": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "strategy_scan": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
}
SCAN_CACHE_MAP = {
    "bi_long": "/tmp/bi_cache_long.json",
    "bi_short": "/tmp/bi_cache_short.json",
    "bear": "/tmp/bear_scanner_cache.json",
    "biotech": "/tmp/alpha_biotech_cache.json",
    "early_movers": "/tmp/early_movers_cache.json",
    "crash_monitor": "/tmp/crash_monitor_cache.json",
    "market_context": "/tmp/market_context_cache.json",
    "btc_divergenz": "/tmp/btc_divergenz_cache.json",
    "money_flow": "/tmp/money_flow_cache.json",
    "new_listing": "/tmp/new_listing_scanner.json",
    "volume_spikes": "/tmp/volume_spikes_cache.json",
    "orb": "/tmp/orb_scan_results.json",
    "turtle": "/tmp/turtle_scan_cache.json",
    "strategy_scan": "/tmp/strategy_scan_cache.json",
}
_scan_lock = threading.Lock()
_cache_lock = threading.Lock()


def _scan_cache_health(scan_name: str, scan_state: Dict[str, Any]) -> Dict[str, Any]:
    """Expose whether a scanner cache is fresh enough for the UI."""
    cache_path = SCAN_CACHE_MAP.get(scan_name)
    if not cache_path:
        return {
            "cache_file": None,
            "cache_exists": False,
            "cache_age_seconds": None,
            "cache_stale": None,
            "cache_health": "not_tracked",
        }

    cache_file = os.path.basename(cache_path)
    if not os.path.exists(cache_path):
        return {
            "cache_file": cache_file,
            "cache_exists": False,
            "cache_age_seconds": None,
            "cache_stale": True,
            "cache_health": "missing",
        }

    try:
        age_seconds = int(max(0, time.time() - os.path.getmtime(cache_path)))
        interval_seconds = max(60, int(scan_state.get("interval_min", 0) or 0) * 60)
        stale_after = max(interval_seconds * 2, interval_seconds + 15 * 60)
        stale = age_seconds > stale_after
        return {
            "cache_file": cache_file,
            "cache_exists": True,
            "cache_age_seconds": age_seconds,
            "cache_stale": stale,
            "cache_health": "stale" if stale else "ok",
        }
    except Exception as exc:
        return {
            "cache_file": cache_file,
            "cache_exists": True,
            "cache_age_seconds": None,
            "cache_stale": True,
            "cache_health": "error",
            "cache_error": str(exc),
        }

# Initialize last_run from cache files on startup (survives restarts)
def _init_scan_status_from_cache():
    """Read cache file timestamps to populate last_run on startup."""
    import os
    for scan_name, cache_path in SCAN_CACHE_MAP.items():
        if scan_name in _scan_status and os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cache_data = json.load(f)
                cached_at = None
                # Neues Format: ISO string
                if isinstance(cache_data, dict) and cache_data.get("cached_at"):
                    cached_at = cache_data["cached_at"]
                # Altes Scanner-Format: Unix timestamp
                elif isinstance(cache_data, dict) and "timestamp" in cache_data:
                    ts = cache_data["timestamp"]
                    if isinstance(ts, (int, float)) and ts > 1000000000:
                        cached_at = datetime.fromtimestamp(ts).isoformat()
                # Fallback: File modification time
                if not cached_at:
                    cached_at = datetime.fromtimestamp(os.path.getmtime(cache_path)).isoformat()
                _scan_status[scan_name]["last_run"] = cached_at
                print(f"[Init] {scan_name} last_run restored: {cached_at}")
            except Exception as e:
                print(f"[Init] Could not read cache for {scan_name}: {e}")

_init_scan_status_from_cache()

_SCAN_TIMEOUTS = {"bi_long": 45, "bi_short": 45, "biotech": 45, "bear": 20, "early_movers": 25}

def _run_scan_safe(name, func, timeout_min=None):
    """Run a scan function safely in a background thread (non-blocking).
    Uses a watchdog pattern: if a scan is still 'running' after timeout_min,
    the next check will force-reset it so the scheduler isn't stuck."""
    if timeout_min is None:
        timeout_min = _SCAN_TIMEOUTS.get(name, 10)
    with _scan_lock:
        # Watchdog: if already marked running but started too long ago, force-reset
        if _scan_status[name]["running"]:
            started = _scan_status[name].get("_started_at")
            if started and (time.time() - started) > timeout_min * 60:
                print(f"[Scheduler] {name} WATCHDOG: still running after {timeout_min}min — force-resetting")
                _scan_status[name]["running"] = False
            else:
                print(f"[Scheduler] {name} already running — skipping")
                return
        _scan_status[name]["running"] = True
        _scan_status[name]["_started_at"] = time.time()

    def _worker():
        start_t = time.time()
        try:
            func()
            elapsed = round(time.time() - start_t, 1)
            print(f"[Scheduler] {name} DONE in {elapsed}s")
        except Exception as e:
            elapsed = round(time.time() - start_t, 1)
            print(f"[Scheduler] {name} ERROR after {elapsed}s: {e}")
            import traceback
            traceback.print_exc()
        finally:
            with _scan_lock:
                _scan_status[name]["running"] = False
                _scan_status[name]["_started_at"] = None
                # last_run IMMER aktualisieren (auch bei Fehler) — sonst zeigt UI ewig alten Wert
                _scan_status[name]["last_run"] = datetime.now().isoformat()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    # NON-BLOCKING: thread runs in background, watchdog handles timeouts

def _scheduler_loop():
    """Background loop that triggers all scans at their defined intervals."""
    global _scheduler_running
    print("[Scheduler] Starting automatic background scans...")

    # Initial delay to let server fully start
    time.sleep(5)

    # Run all scans once immediately on startup
    # Reihenfolge: Schnelle Crypto-Scans zuerst, dann Stock-Scans gestaffelt
    # BI Long + BI Short NICHT gleichzeitig (beide nutzen Polygon heavy)
    # V2.2: Scans in LEICHT (Snapshot, 1-2 API Calls) und SCHWER (tausende Calls) aufteilen
    # Leichte Scans starten parallel, schwere NACHEINANDER (teilen sich 200 calls/min)
    light_scans = [
        ("early_movers", _early_movers_wrapper),
        ("crash_monitor", _crash_monitor_wrapper),
        ("market_context", _market_context_wrapper),
        ("btc_divergenz", _btc_divergenz_wrapper),
        ("volume_spikes", _volume_spikes_wrapper),
        ("money_flow", _money_flow_wrapper),
        ("orb", _orb_scanner_wrapper),
        ("bear", _bear_scan_wrapper),  # V2.5: Bear ist light (~30 API-Calls), nicht heavy
        ("strategy_scan", _stock_strategy_alert_sweep_wrapper),
        ("turtle", _turtle_scan_wrapper),  # ~80 API-Calls (Snapshot + Bars)
    ]
    heavy_scans = [
        ("bi_long", lambda: _bi_background_scan_wrapper("long")),
        ("bi_short", lambda: _bi_background_scan_wrapper("short")),
        ("biotech", _biotech_scan_wrapper),
    ]
    scan_tasks = light_scans + heavy_scans

    # Only add new_listing scan if module is available
    if HAS_NEW_LISTING_SCANNER:
        scan_tasks.append(("new_listing", _new_listing_wrapper))
    _heavy_names = {name for name, _ in heavy_scans}
    _isolated_names = {"early_movers"}

    def _wait_for_scan_completion(name: str, label: str) -> None:
        """Some scanners need quiet network time; do not start noisy peers while they build signals."""
        timeout_sec = max(60, int(_SCAN_TIMEOUTS.get(name, 10)) * 60)
        print(f"[Scheduler] Warte auf {name} ({label})...")
        _wait_start = time.time()
        while _scheduler_running:
            with _scan_lock:
                still_running = bool(_scan_status.get(name, {}).get("running"))
            if not still_running:
                break
            time.sleep(5 if name in _isolated_names else 10)
            if time.time() - _wait_start > timeout_sec:
                print(f"[Scheduler] {name} Timeout nach {int(timeout_sec / 60)}min - weiter")
                break
        print(f"[Scheduler] {name} fertig nach {int(time.time() - _wait_start)}s - naechster Scan")

    # ── Smart Startup: Nur Scans starten die keinen frischen Cache haben ──
    last_run_times = {}
    for name, func in scan_tasks:
        if not _scheduler_running:
            break
        interval_sec = _scan_status[name]["interval_min"] * 60
        cache_file = SCAN_CACHE_MAP.get(name)
        cache_age = None
        if cache_file and os.path.exists(cache_file):
            cache_age = time.time() - os.path.getmtime(cache_file)

        if cache_age is not None and cache_age < interval_sec:
            # Cache ist frisch genug → NICHT neu scannen
            last_run_times[name] = time.time() - cache_age
            next_run = time.time() + (interval_sec - cache_age)
            with _scan_lock:
                _scan_status[name]["last_run"] = datetime.fromtimestamp(time.time() - cache_age).isoformat()
                _scan_status[name]["next_run"] = datetime.fromtimestamp(next_run).isoformat()
            print(f"[Scheduler] {name}: Cache frisch ({int(cache_age)}s alt, Intervall {interval_sec}s) — übersprungen, nächster in {int(interval_sec - cache_age)}s")
        else:
            # Kein Cache oder zu alt → scannen
            age_str = f"{int(cache_age)}s alt" if cache_age else "kein Cache"
            print(f"[Scheduler] Initial scan: {name} ({age_str})")
            if name == "market_context" and _scan_status.get("crash_monitor", {}).get("running"):
                print("[Scheduler] market_context wartet kurz auf crash_monitor...")
                _wait_start = time.time()
                while _scan_status.get("crash_monitor", {}).get("running") and _scheduler_running:
                    if time.time() - _wait_start > 120:
                        print("[Scheduler] market_context: crash_monitor wartet zu lange, nutze letzten Cache")
                        break
                    time.sleep(3)
            _run_scan_safe(name, func)
            last_run_times[name] = time.time()
            with _scan_lock:
                _scan_status[name]["next_run"] = datetime.fromtimestamp(
                    time.time() + interval_sec
                ).isoformat()
            # V2.2: Schwere Scans (bi_long, bi_short, biotech) WARTEN bis fertig
            # bevor der nächste startet — sonst teilen sich alle 200 calls/min
            if name in _isolated_names:
                _wait_for_scan_completion(name, "isolierter Crypto-Trigger-Scan")
            elif name in _heavy_names:
                print(f"[Scheduler] Warte auf {name} (schwerer Scan)...")
                _wait_start = time.time()
                while _scan_status[name]["running"] and _scheduler_running:
                    time.sleep(10)
                    _wait_sec = int(time.time() - _wait_start)
                    if _wait_sec > 3600:  # Max 1h warten
                        print(f"[Scheduler] {name} Timeout nach 1h — weiter")
                        break
                print(f"[Scheduler] {name} fertig nach {int(time.time() - _wait_start)}s — nächster Scan")
            else:
                time.sleep(3)  # Leichte Scans: nur kurzer Stagger

    # Fill any missing last_run_times
    for name in _scan_status:
        if name not in last_run_times:
            last_run_times[name] = 0  # Will run on next check

    while _scheduler_running:
        now = time.time()
        for name, func in scan_tasks:
            if not _scheduler_running:
                break
            with _scan_lock:
                interval_sec = _scan_status[name]["interval_min"] * 60
                elapsed = now - last_run_times.get(name, 0)
                is_running = _scan_status[name]["running"]
                started_at = _scan_status[name].get("_started_at")

            # Watchdog: Force-Reset wenn Scan zu lange hängt
            if is_running and started_at:
                timeout_min = _SCAN_TIMEOUTS.get(name, 10)
                stuck_sec = now - started_at
                if stuck_sec > timeout_min * 60:
                    print(f"[Scheduler] WATCHDOG: {name} hängt seit {int(stuck_sec)}s (timeout={timeout_min}min) — Force-Reset!")
                    with _scan_lock:
                        _scan_status[name]["running"] = False
                        _scan_status[name]["_started_at"] = None
                    is_running = False  # Erlaubt sofortigen Neustart

            if elapsed >= interval_sec and not is_running:
                if name == "market_context" and _scan_status.get("crash_monitor", {}).get("running"):
                    print("[Scheduler] market_context skip: crash_monitor laeuft gerade")
                    continue
                # V2.2: Schwere Scans nicht starten wenn ein anderer schwerer läuft
                if name in _heavy_names:
                    _other_heavy_running = False
                    with _scan_lock:
                        for _hn in _heavy_names:
                            if _hn != name and _scan_status.get(_hn, {}).get("running", False):
                                _other_heavy_running = True
                                break
                    if _other_heavy_running:
                        continue  # Nächstes Mal probieren
                print(f"[Scheduler] Running: {name} (interval: {_scan_status[name]['interval_min']}min)")
                _run_scan_safe(name, func)  # Non-blocking
                last_run_times[name] = time.time()
                with _scan_lock:
                    _scan_status[name]["next_run"] = datetime.fromtimestamp(
                        last_run_times[name] + interval_sec
                    ).isoformat()
                if name in _isolated_names:
                    _wait_for_scan_completion(name, "isolierter Crypto-Trigger-Scan")
                else:
                    time.sleep(2)  # Small stagger between scan launches
        time.sleep(30)  # Check every 30 seconds


@asynccontextmanager
async def lifespan(app):
    """Start background scheduler on startup, stop on shutdown."""
    global _scheduler_running
    _scheduler_running = True
    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    scheduler_thread.start()
    _start_trade_reminder_loop()
    print("[Scheduler] Background scan scheduler started")
    yield
    _scheduler_running = False
    _stop_trade_reminder_loop()
    print("[Scheduler] Background scan scheduler stopped")


# ── FastAPI App ──
app = FastAPI(
    title="TradingBot Scanner API",
    description="REST API for trading scanner modules",
    version=API_VERSION,
    lifespan=lifespan,
)

_cors_origins = {
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://178.104.69.209:3000",
    "http://178.104.69.209",
}
if PUBLIC_APP_URL:
    _cors_origins.add(PUBLIC_APP_URL)
for _origin in str(os.environ.get("CORS_ORIGINS", "") or _SECRETS.get("CORS_ORIGINS", "")).split(","):
    _origin = _origin.strip().rstrip("/")
    if _origin:
        _cors_origins.add(_origin)

# CORS middleware for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
_VENDOR_DIR = os.path.join(_FRONTEND_DIR, "vendor")
if os.path.isdir(_VENDOR_DIR):
    app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")


_PUBLIC_API_PATHS = {
    "/api/health",
    "/api/system-health",
    "/api/commercial-readiness",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/plans",
    "/api/stripe/webhook",
}
_FEATURE_GATES = [
    ("/api/ai-analysis", "has_ai_analysis"),
    ("/api/orb", "has_orb_scanner"),
    ("/api/run-backtest", "has_backtest"),
    ("/api/backtest", "has_backtest"),
]
_TAB_GATES = [
    ("/api/autotrader", "autotrader"),
    ("/api/biotech", "biotech"),
    ("/api/btc-divergenz", "btc-divergenz"),
    ("/api/early-movers", "early-movers"),
    ("/api/new-listing", "new-listing"),
    ("/api/volume-spikes", "volume-spikes"),
    ("/api/money-flow", "money-flow"),
    ("/api/crash-monitor", "crash-monitor"),
    ("/api/bear", "short-scanner"),
    ("/api/bi-", "bi-scanner"),
    ("/api/kalender", "kalender"),
    ("/api/chart-data", "chart-analyse"),
    ("/api/ticker-detail", "chart-analyse"),
    ("/api/ticker-search", "chart-analyse"),
    ("/api/strategies", "strategie-guide"),
    ("/api/scan", "scanner"),
]


def _token_from_authorization(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    authorization = str(authorization).strip()
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return authorization


def _commerce_gate_denial(path: str, token: str, payload: Dict[str, Any]) -> Optional[JSONResponse]:
    email = str(payload.get("email", "")).lower()
    if email in ADMIN_EMAILS:
        return None
    for prefix, feature in _FEATURE_GATES:
        if path.startswith(prefix) and not check_feature(token, feature):
            return JSONResponse(
                status_code=403,
                content={"detail": "Plan upgrade required", "feature": feature, "upgrade_required": True},
            )
    for prefix, tab_id in _TAB_GATES:
        if path.startswith(prefix) and not check_tab_access(token, tab_id):
            return JSONResponse(
                status_code=403,
                content={"detail": "Plan upgrade required", "tab": tab_id, "upgrade_required": True},
            )
    return None


@app.middleware("http")
async def commerce_auth_gate(request: Request, call_next):
    """Optional production gate: set COMMERCE_ENFORCE_AUTH=1 to protect API data server-side."""
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if not COMMERCE_ENFORCE_AUTH or not path.startswith("/api/") or path in _PUBLIC_API_PATHS:
        return await call_next(request)
    if path.startswith("/api/auth/") and path not in {
        "/api/auth/me",
        "/api/auth/checkout",
        "/api/auth/billing-portal",
        "/api/auth/alert-settings",
    }:
        return await call_next(request)
    if not HAS_AUTH:
        return JSONResponse(status_code=503, content={"detail": "Auth system not available"})
    token = _token_from_authorization(request.headers.get("authorization"))
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Login required"})
    payload = verify_token(token)
    if not payload:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    denial = _commerce_gate_denial(path, token, payload)
    if denial:
        return denial
    return await call_next(request)


# ── Serve Frontend (index.html) ──
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the React frontend."""
    frontend_path = os.path.join(_FRONTEND_DIR, "index.html")
    if os.path.exists(frontend_path):
        with open(frontend_path, "r", encoding="utf-8") as f:
            response = HTMLResponse(content=f.read())
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return HTMLResponse(content="<h1>Alpha Station</h1><p>Frontend not found</p>", status_code=404)


# ══════════════════════════════════════════════════════════════
# V3.4: AUTH & SUBSCRIPTION ENDPOINTS
# ══════════════════════════════════════════════════════════════

def _get_token_from_header(authorization: str = Header(None)) -> Optional[str]:
    """Extract JWT token from Authorization header."""
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class CheckoutRequest(BaseModel):
    plan: str  # basic, pro, elite


class BillingPortalRequest(BaseModel):
    return_url: str = ""


class AlertSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    alert_email: Optional[str] = None
    narrative_email_frequency: Optional[str] = None


# ── Admin Models ──
class PlanUpdateRequest(BaseModel):
    plan: str


class CouponCreateRequest(BaseModel):
    code: str
    plan: str
    duration_days: int
    max_uses: int
    description: str = ""


class CouponToggleRequest(BaseModel):
    pass


class RedeemCouponRequest(BaseModel):
    code: str


class TicketCreateRequest(BaseModel):
    subject: str
    message: str


class TicketReplyRequest(BaseModel):
    message: str


class TicketStatusRequest(BaseModel):
    status: str


@app.post("/api/auth/register")
async def api_register(req_body: RegisterRequest):
    """Register a new user account."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    result = register_user(req_body.email, req_body.password, req_body.name)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/api/auth/login")
async def api_login(req_body: LoginRequest):
    """Login with email + password. Returns JWT token."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    result = login_user(req_body.email, req_body.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["message"])
    return result


@app.get("/api/auth/me")
async def api_get_me(authorization: str = Header(None)):
    """Get current user info + plan limits from JWT token."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    token = _get_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    limits = get_user_limits(token)
    # Load full user data from DB
    email = payload.get("email", "")
    from modules.auth import _load_users
    db = _load_users()
    db_user = db.get("users", {}).get(email, {})
    return {
        "user": {
            "id": payload.get("sub"),
            "email": email,
            "name": db_user.get("name", ""),
            "plan": limits.get("plan", "free"),
            "stripe_customer_id": db_user.get("stripe_customer_id"),
            "trial_ends_at": db_user.get("trial_ends_at"),
            "is_admin": limits.get("is_admin", False),
        },
        "limits": limits,
    }


@app.get("/api/auth/alert-settings")
async def api_get_alert_settings(authorization: str = Header(None)):
    """Get per-user email alert settings."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    token = _get_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return get_user_alert_settings(token)


@app.put("/api/auth/alert-settings")
async def api_update_alert_settings(req_body: AlertSettingsRequest, authorization: str = Header(None)):
    """Update per-user email alert settings."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    token = _get_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    result = update_user_alert_settings(
        token,
        enabled=req_body.enabled,
        alert_email=req_body.alert_email,
        narrative_email_frequency=req_body.narrative_email_frequency,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Could not update alert settings"))
    return result


@app.get("/api/commercial-readiness")
async def api_commercial_readiness():
    """Operational checklist for paid-beta/commercial readiness."""
    auth_status = auth_security_status() if HAS_AUTH else {"critical": ["Auth system not available"], "warnings": []}
    critical = list(auth_status.get("critical", []))
    warnings = list(auth_status.get("warnings", []))
    if not PUBLIC_APP_URL.startswith("https://"):
        warnings.append("PUBLIC_APP_URL is not HTTPS")
    if not COMMERCE_ENFORCE_AUTH:
        critical.append("COMMERCE_ENFORCE_AUTH is disabled; API data is not server-side paywalled")
    if not ALERT_SEND_TO_SUBSCRIBERS:
        warnings.append("ALERT_SEND_TO_SUBSCRIBERS disabled; paid users will not receive platform alerts")
    return {
        "status": "blocked" if critical else ("warning" if warnings else "ready"),
        "commercial_ready": not critical,
        "public_app_url": PUBLIC_APP_URL,
        "commerce_enforce_auth": COMMERCE_ENFORCE_AUTH,
        "alert_send_to_subscribers": ALERT_SEND_TO_SUBSCRIBERS,
        "auth": auth_status,
        "critical": critical,
        "warnings": warnings,
        "note": "Commercial readiness here covers technical gating/security only; legal, tax and data-license review still need human/legal approval.",
    }


@app.get("/api/auth/plans")
async def api_get_plans():
    """Get available plans and pricing."""
    return {
        "plans": [
            {"id": "basic", "name": "Basic", "price": 29, "interval": "month",
             "features": ["4 Scanner Tabs", "Scan alle 30min", "30 Ticker-Details/Stunde"]},
            {"id": "pro", "name": "Pro", "price": 79, "interval": "month",
             "features": ["Alle Scanner", "Echtzeit Scans", "Volle Sidebar-Analyse", "Email Alerts", "Trade Setups"],
             "popular": True},
            {"id": "elite", "name": "Elite", "price": 149, "interval": "month",
             "features": ["Alles aus Pro", "ORB Scanner", "Backtesting", "API Access", "Priority Support"]},
        ]
    }


@app.post("/api/auth/checkout")
async def api_create_checkout(req_body: CheckoutRequest, authorization: str = Header(None)):
    """Create Stripe checkout session for subscription."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    token = _get_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    email = payload.get("email")
    base_url = PUBLIC_APP_URL
    checkout_url = create_checkout_session(
        email=email,
        plan=req_body.plan,
        success_url=f"{base_url}?checkout=success&plan={req_body.plan}",
        cancel_url=f"{base_url}?checkout=cancel",
    )
    if not checkout_url:
        raise HTTPException(status_code=500, detail="Could not create checkout session. Check Stripe configuration.")
    return {"url": checkout_url}


@app.post("/api/auth/billing-portal")
async def api_billing_portal(req_body: BillingPortalRequest, authorization: str = Header(None)):
    """Create Stripe billing portal session for subscription management."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    token = _get_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    email = payload.get("email")
    return_url = req_body.return_url or PUBLIC_APP_URL
    portal_url = create_billing_portal(email, return_url)
    if not portal_url:
        raise HTTPException(status_code=500, detail="Could not create billing portal")
    return {"url": portal_url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (subscription changes, payments)."""
    if not HAS_AUTH:
        raise HTTPException(status_code=503, detail="Auth system not available")
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    result = handle_stripe_webhook(payload, sig_header)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Webhook error"))
    return {"status": "ok"}


# ── Endpoints ──

@app.get("/api/health", response_model=HealthResponse)
def get_health():
    """Check API health and configuration status."""
    return HealthResponse(
        status="healthy",
        version=API_VERSION,
        timestamp=datetime.now().isoformat(),
        api_keys_configured={
            "market_data": bool(POLYGON_KEY),
            "catalyst_data": bool(BPIQ_API_KEY),
            "ai_assistant": bool(ANTHROPIC_API_KEY),
        },
    )


def _build_system_health() -> Dict[str, Any]:
    """Build a trader-facing health summary without triggering expensive scans."""
    with _scan_lock:
        scan_health = {}
        health_counts = {"ok": 0, "stale": 0, "missing": 0, "error": 0, "not_tracked": 0}
        running_scans = []
        stale_or_missing = []

        for name, status in _scan_status.items():
            cache = _scan_cache_health(name, status)
            health_key = cache.get("cache_health", "not_tracked")
            health_counts[health_key] = health_counts.get(health_key, 0) + 1
            if status.get("running"):
                running_scans.append(name)
            if health_key in ("stale", "missing", "error"):
                stale_or_missing.append(name)
            scan_health[name] = {
                "running": status.get("running", False),
                "last_run": status.get("last_run"),
                "next_run": status.get("next_run"),
                "interval_min": status.get("interval_min"),
                **cache,
            }

    api_keys = {
        "market_data": bool(POLYGON_KEY),
        "catalyst_data": bool(BPIQ_API_KEY),
        "ai_assistant": bool(ANTHROPIC_API_KEY),
    }
    email_alerts = _email_alert_status()

    warnings = []
    critical = []
    if not api_keys["market_data"]:
        critical.append("Market-Data-Zugang fehlt - Aktien-/ORB-/Marktdaten koennen nicht sauber laufen")
    if not email_alerts["configured"]:
        warnings.append("Email-Alerts sind nicht konfiguriert - GMAIL_USER/GMAIL_APP_PASSWORD fehlen")
    if not _scheduler_running:
        warnings.append("Background-Scheduler ist nicht aktiv")
    if stale_or_missing:
        warnings.append(f"{len(stale_or_missing)} Scanner-Caches sind alt, fehlen oder haben Fehler")

    overall = "critical" if critical else ("warning" if warnings else "healthy")
    return {
        "status": overall,
        "version": API_VERSION,
        "timestamp": datetime.now().isoformat(),
        "api_keys_configured": api_keys,
        "email_alerts": email_alerts,
        "scheduler": {
            "running": _scheduler_running,
            "total_scans": len(_scan_status),
            "running_scans": running_scans,
            "stale_or_missing_scans": stale_or_missing,
            "health_counts": health_counts,
        },
        "scans": scan_health,
        "calendar": {
            "official_sources": ["Federal Reserve", "BLS", "BEA", "Census", "ISM", "NYSE/Nasdaq", "LSE", "Deutsche Boerse", "JPX", "HKEX"],
            "official_event_families": ["FOMC/FED", "CPI", "NFP", "PPI", "GDP/PCE", "Retail Sales", "Advance Economic Indicators", "ISM PMI"],
            "exchange_calendars": ["NYSE/Nasdaq", "LSE", "Xetra/Frankfurt", "Tokyo", "Hong Kong"],
            "estimated_event_families": ["Earnings Season", "Initial Jobless Claims"],
            "quality": "official_core_macro_plus_2026_exchange_hours_marked_estimates_remaining",
        },
        "risk_policy": RISK_POLICY,
        "market_context": _get_market_context_snapshot().get("summary"),
        "warnings": warnings,
        "critical": critical,
    }


@app.get("/api/system-health")
def get_system_health():
    """Detailed system health for UI/admin checks."""
    return _build_system_health()


@app.get("/api/email-alert-status")
def get_email_alert_status():
    """Safe email-alert diagnostics without exposing credentials."""
    return {
        "status": "ok",
        "email_alerts": _email_alert_status(),
        "common_stock_guard": _common_stock_guard_status(),
        "recent_email_events": list(_EMAIL_SEND_LOG[-20:]),
        "common_reasons_for_no_mail": [
            "Keine neuen S/A/A+ Setups in den aktuellen Scanner-Caches.",
            "Startup-Cooldown nach Restart ist noch aktiv.",
            "Ticker ist im 8h Alert-Cooldown oder Crash-Dedupe 36h aktiv.",
            "Short-Ticker wurde bereits bearish gemeldet; Crash hat Vorrang vor Bear/BI-Short.",
            "Mail wurde wegen ETF/ETP-Inhalt geblockt.",
            "Pump-&-Dump: kein aktives SHORT-now Signal mit Safety OK, unverpassten Targets und R:R >= 1.5.",
            "Gmail SMTP/Test-Mail ist fehlgeschlagen.",
        ],
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/email-alert-audit")
def get_email_alert_audit():
    """Show which scanner results are currently email-alert eligible."""
    cache_targets = {
        "bi_long": BI_CACHE_LONG,
        "bi_short": BI_CACHE_SHORT,
        "biotech": BIOTECH_CACHE,
        "bear": BEAR_CACHE,
        "orb": ORB_CACHE,
        "new_listing": NEW_LISTING_CACHE,
        "early_movers": EARLY_MOVERS_CACHE,
        "volume_spikes": VOLUME_SPIKES_CACHE,
        "strategy_scan": STRATEGY_SCAN_CACHE,
    }
    scanners = {}
    for name, path in cache_targets.items():
        try:
            scanners[name] = _build_alert_audit_for_cache(name, path)
        except Exception as exc:
            scanners[name] = {"scanner": name, "error": str(exc), "cache_file": os.path.basename(path)}

    return {
        "status": "ok",
        "summary": _summarize_email_alert_audit(scanners),
        "email_alerts": _email_alert_status(),
        "common_stock_guard": _common_stock_guard_status(),
        "policy": {
            "top_grades": sorted(_ALERT_TOP_GRADES),
            "cooldown_seconds": _EMAIL_COOLDOWN_SEC,
            "startup_delay_seconds": _EMAIL_STARTUP_DELAY,
            "rvol_guard_scanners": sorted(_ALERT_RVOL_GUARD_SCANNERS),
            "min_rvol": _ALERT_MIN_RVOL,
            "new_listing_min_rr": _NEW_LISTING_MIN_ALERT_RR,
            "early_mover_min_rr": _EARLY_MOVER_MIN_ALERT_RR,
            "early_mover_retest_max_distance_r": _EARLY_MOVER_RETEST_MAX_DISTANCE_R,
            "bearish_stock_dedupe_seconds": _BEARISH_STOCK_ALERT_DEDUPE_SEC,
            "note": "Alerts are defensive: S/A/A+ only; watch/wait/context rows are suppressed from scanner signal lists and do not email. Crash-level bearish stocks suppress duplicate Bear/BI-Short mails. Pump-&-Dump mails require a real New-Listing source, valid listing-age window, active SHORT-now timing, Safety OK, unmissed targets, minimum R:R and a fresh micro-crack trigger. Early-Mover crypto mails are long-only and require confirmed closed 5m execution, BTC tailwind, fresh data, TP1 not missed, live R:R and a weak-link trade score. Explosion-Armed/watch mails are hard-disabled.",
        },
        "coverage": {
            "automatic_api_scheduler": ["bi_long", "bi_short", "biotech", "bear", "orb", "new_listing", "early_movers", "strategy_scan"],
            "manual_scan_alerts": ["stock_strategy"],
            "watch_only_crypto_no_trade_email": ["crypto_strategy", "btc_divergenz"],
            "informational_no_trade_email": ["btc_divergenz", "money_flow", "crash_monitor"],
        },
        "scanners": scanners,
        "recent_email_events": list(_EMAIL_SEND_LOG[-20:]),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/trade-reminders")
def get_trade_reminders(status: Optional[str] = Query(None, description="Filter: active, triggered, expired, cancelled")):
    """List trade reminders for browser polling and UI status."""
    with _TRADE_REMINDER_LOCK:
        reminders = _load_trade_reminders()
    if status:
        wanted = status.lower()
        reminders = [r for r in reminders if str(r.get("status", "")).lower() == wanted]
    now = _reminder_now()
    public = []
    for r in reminders:
        expires_at = float(r.get("expires_at", 0) or 0)
        last_check = dict(r.get("last_check") or {}) if isinstance(r.get("last_check"), dict) else r.get("last_check")
        trigger_result = dict(r.get("trigger_result") or {}) if isinstance(r.get("trigger_result"), dict) else r.get("trigger_result")
        if isinstance(last_check, dict):
            last_check.pop("row", None)
        if isinstance(trigger_result, dict):
            trigger_result.pop("row", None)
        public.append({
            "id": r.get("id"),
            "ticker": r.get("ticker"),
            "asset_type": r.get("asset_type"),
            "scanner": r.get("scanner"),
            "condition": r.get("condition"),
            "channel": r.get("channel"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "expires_at": r.get("expires_at_iso"),
            "triggered_at": r.get("triggered_at"),
            "last_check": last_check,
            "trigger_result": trigger_result,
            "remaining_seconds": max(0, int(expires_at - now)) if expires_at else None,
        })
    return {
        "status": "ok",
        "count": len(public),
        "reminders": public,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/trade-reminders")
def create_trade_reminder(request: TradeReminderRequest):
    """Create a temporary monitor for a ticker/coin trigger or retest."""
    ticker = str(request.ticker or "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker fehlt")
    duration_hours = max(0.25, min(float(request.duration_hours or 6), _TRADE_REMINDER_MAX_HOURS))
    channel = str(request.channel or "email_browser").lower()
    if channel not in ("email", "browser", "email_browser"):
        channel = "email_browser"
    now = _reminder_now()
    row = request.row if isinstance(request.row, dict) else {}
    reminder = {
        "id": uuid.uuid4().hex[:12],
        "ticker": ticker.replace("USDT", "") if request.asset_type.lower() == "crypto" else ticker,
        "asset_type": str(request.asset_type or "crypto").lower(),
        "scanner": str(request.scanner or "early_movers"),
        "condition": str(request.condition or "trigger_or_retest"),
        "channel": channel,
        "status": "active",
        "row": row,
        "created_at": _reminder_iso(now),
        "updated_at": _reminder_iso(now),
        "expires_at": now + duration_hours * 3600,
        "expires_at_iso": _reminder_iso(now + duration_hours * 3600),
        "last_checked_at": 0,
        "last_check": None,
    }
    with _TRADE_REMINDER_LOCK:
        reminders = _load_trade_reminders()
        # Replace older active reminder for same ticker/condition to avoid duplicate pings.
        for old in reminders:
            if (
                old.get("status") == "active"
                and str(old.get("ticker", "")).upper() == reminder["ticker"]
                and str(old.get("condition", "")) == reminder["condition"]
            ):
                old["status"] = "cancelled"
                old["updated_at"] = _reminder_iso(now)
        reminders.append(reminder)
        _save_trade_reminders(reminders)
    return {
        "status": "ok",
        "message": f"Reminder fuer {reminder['ticker']} aktiv",
        "reminder": {k: v for k, v in reminder.items() if k != "row"},
    }


@app.delete("/api/trade-reminders/{reminder_id}")
def cancel_trade_reminder(reminder_id: str):
    """Cancel a reminder."""
    changed = False
    with _TRADE_REMINDER_LOCK:
        reminders = _load_trade_reminders()
        for r in reminders:
            if r.get("id") == reminder_id and r.get("status") == "active":
                r["status"] = "cancelled"
                r["updated_at"] = _reminder_iso()
                changed = True
        if changed:
            _save_trade_reminders(reminders)
    if not changed:
        raise HTTPException(status_code=404, detail="Aktiver Reminder nicht gefunden")
    return {"status": "ok", "message": "Reminder deaktiviert"}


@app.get("/api/risk-policy")
def get_risk_policy():
    """Central risk guardrails used by scanner quality explanations."""
    return {"status": "success", "risk_policy": RISK_POLICY, "timestamp": datetime.now().isoformat()}


@app.get("/api/catalyst-data-status")
def get_catalyst_data_status():
    """Admin check whether the configured premium catalyst feed is reachable."""
    key = os.environ.get("BPIQ_API_KEY") or _SECRETS.get("BPIQ_API_KEY", "")
    payload = {
        "status": "ok",
        "key_configured": bool(key),
        "working": False,
        "http_status": None,
        "sample_results": 0,
        "error": None,
        "source_note": "Checks the premium catalyst feed without exposing the key.",
        "timestamp": datetime.now().isoformat(),
    }
    if not key:
        payload["status"] = "warning"
        payload["error"] = "Catalyst data key missing"
        return payload
    try:
        resp = req.get(
            "https://api.bpiq.com/api/v1/drugs/?has_catalyst=true&limit=1&offset=0",
            headers={"Authorization": f"Token {key}"},
            timeout=15,
        )
        payload["http_status"] = resp.status_code
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", []) if isinstance(data, dict) else []
            payload["sample_results"] = len(results)
            payload["working"] = True
            return payload
        payload["status"] = "warning"
        payload["error"] = f"Catalyst data feed returned HTTP {resp.status_code}"
        return payload
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = str(exc)
        return payload


@app.get("/api/biotech-catalyst-watchlist")
def get_biotech_catalyst_watchlist(
    limit: int = Query(85, ge=1, le=200),
    window_days: Optional[int] = Query(None, ge=1, le=365),
):
    """Supplemental premium catalyst watchlist for the Biotech scanner UI."""
    watchlist = get_bpiq_catalyst_watchlist(limit=limit, window_days=window_days)
    return watchlist


@app.get("/api/debug-keys")
def debug_keys():
    """Disabled: never expose provider/key diagnostics in a sellable build."""
    return {"status": "disabled", "message": "Key diagnostics are disabled in this build."}


@app.post("/api/test-email")
def test_email_alert():
    """Test-Endpoint: Sendet eine Test-Mail um Email-Alerts zu verifizieren."""
    status = _email_alert_status()
    if not status["configured"]:
        raise HTTPException(status_code=500, detail={
            "message": "Email Alerts nicht konfiguriert",
            "status": status,
        })
    success = _send_email_alert(
        "✅ TradingBot Test — Email Alerts funktionieren!",
        f'''<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
        <h2 style="color:#059669">✅ Email Alert System aktiv</h2>
        <p>Dieser Test wurde am <b>{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC</b> gesendet.</p>
        <p>Du wirst ab jetzt automatisch benachrichtigt bei: <b>Grade S/A/A+</b> (BI + Biotech), <b>Bear/Crash</b>, <b>ORB Breakouts</b>, <b>Pump-&-Dump SHORT</b> und manuellen Aktien-/Crypto-Strategie-Scans mit Top-Grade.</p>
        <p style="color:#999;font-size:12px">TradingBot Alert System v{API_VERSION}</p>
        </body></html>''',
        bypass_startup_cooldown=True,
    )
    if success:
        return {"status": "ok", "message": "Test-Email gesendet!"}
    raise HTTPException(status_code=500, detail="Email konnte nicht gesendet werden — prüfe GMAIL_APP_PASSWORD")


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
    """Get status of all background scans (running, last_run, next_run) + progress."""
    with _scan_lock:
        scans_copy = {}
        health_counts = {"ok": 0, "stale": 0, "missing": 0, "error": 0, "not_tracked": 0}
        for name, status in _scan_status.items():
            cache_health = _scan_cache_health(name, status)
            health_counts[cache_health.get("cache_health", "not_tracked")] = (
                health_counts.get(cache_health.get("cache_health", "not_tracked"), 0) + 1
            )
            scans_copy[name] = {
                "running": status["running"],
                "last_run": status["last_run"],
                "next_run": status["next_run"],
                "interval_min": status["interval_min"],
                **cache_health,
            }
            # Add runtime info for running scans
            if status["running"] and status.get("_started_at"):
                scans_copy[name]["running_since_sec"] = int(time.time() - status["_started_at"])

    # Progress-Daten aus /tmp/ Files anhängen (BI + Biotech)
    for scan_key, reader in [("bi_long", lambda: _bi_progress_read("long")),
                              ("bi_short", lambda: _bi_progress_read("short")),
                              ("biotech", _biotech_progress_read)]:
        try:
            prog = reader()
            if prog and isinstance(prog, dict):
                scans_copy[scan_key]["progress"] = {
                    "checked": prog.get("checked", 0),
                    "total": prog.get("total", 0),
                    "hits": prog.get("hits", 0),
                    "detail": prog.get("detail", ""),
                    "status": prog.get("status", ""),
                }
        except Exception:
            pass

    return {
        "scheduler_running": _scheduler_running,
        "scans": scans_copy,
        "health_counts": health_counts,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/ticker-search")
def search_tickers(q: str = Query(..., description="Search query (ticker or company name)")):
    """Search tickers by symbol or company name via Polygon.io."""
    try:
        q = q.strip().upper()
        if not q:
            return {"results": []}
        # Polygon Ticker Search API
        url = "https://api.polygon.io/v3/reference/tickers"
        resp = rate_limited_get(url, params={
            "apiKey": POLYGON_KEY,
            "search": q,
            "active": "true",
            "limit": 15,
            "order": "asc",
            "sort": "ticker",
        })
        if resp.status_code != 200:
            return {"results": []}
        data = resp.json().get("results", [])
        results = []
        for t in data:
            ticker = t.get("ticker", "")
            results.append({
                "ticker": ticker,
                "name": t.get("name", ""),
                "market": t.get("market", ""),
                "type": t.get("type", ""),
                "locale": t.get("locale", ""),
            })
        return {"results": results}
    except Exception as e:
        return {"results": []}


@app.get("/api/ticker-detail")
def get_ticker_detail(ticker: str = Query(..., description="Ticker symbol (e.g. NVDA, AAPL, X:BTCUSD)")):
    """Get detailed price data for a single ticker (30 days, key metrics)."""
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 60, "sort": "desc", "adjusted": "true"})
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

        # V2.7: FIX — MA nur berechnen wenn genug Bars vorhanden (sonst None statt falscher Wert)
        ma20 = round(sum(closes[:20]) / 20, 2) if len(closes) >= 20 else None
        ma50 = round(sum(closes[:50]) / 50, 2) if len(closes) >= 50 else None
        avg_vol = sum(volumes[1:21]) / min(len(volumes) - 1, 20) if len(volumes) > 1 else 1
        rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0

        # RSI (14-period) — Wilder's Smoothing (industry standard)
        rsi = None
        if len(closes) >= 15:
            # closes[0]=newest, need chronological for Wilder's
            chron_closes = list(reversed(closes[:30]))  # Last 30 bars chronological
            if len(chron_closes) >= 15:
                changes = [chron_closes[i] - chron_closes[i-1] for i in range(1, len(chron_closes))]
                gains_init = [max(c, 0) for c in changes[:14]]
                losses_init = [abs(min(c, 0)) for c in changes[:14]]
                avg_gain = sum(gains_init) / 14
                avg_loss = sum(losses_init) / 14
                # Wilder's smoothing for remaining bars
                for c in changes[14:]:
                    avg_gain = (avg_gain * 13 + max(c, 0)) / 14
                    avg_loss = (avg_loss * 13 + abs(min(c, 0))) / 14
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    rsi = round(100 - (100 / (1 + rs)), 1)
                else:
                    rsi = 100.0

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
            """Calculate EMA — data is DESCENDING (newest first), returns EMA of newest bar."""
            if len(data) < period:
                return None
            # Reverse to chronological (oldest first)
            chron = list(reversed(data[:max(len(data), period + 20)]))
            k = 2 / (period + 1)
            ema = sum(chron[:period]) / period  # Seed with SMA
            for i in range(period, len(chron)):
                ema = chron[i] * k + ema * (1 - k)
            return round(ema, 2)

        ema9 = calculate_ema(closes, 9)
        ema20 = calculate_ema(closes, 20)
        ema50 = calculate_ema(closes, 50)
        ema100 = calculate_ema(closes, 100)
        ema200 = calculate_ema(closes, 200)

        # 2. VWAP (V3.4 FIX: nur heutiger Tag, nicht kumulativ über alle Bars)
        # TradingView resettet VWAP täglich um Market Open
        vwap = None
        if len(bars) >= 2:
            # bars[0] = heute (newest), nur heutigen Bar für Intraday-VWAP
            # Bei Daily-Bars: VWAP = Typical Price des heutigen Tages
            today_bar = bars[0]
            tp_today = (today_bar["h"] + today_bar["l"] + today_bar["c"]) / 3
            vol_today = today_bar.get("v", 0)
            if vol_today > 0:
                vwap = round(tp_today, 2)
            else:
                vwap = round(tp_today, 2)

        # 3. MACD (optimized: calculate EMA series once, then derive MACD line)
        ema12 = calculate_ema(closes, 12)
        ema26 = calculate_ema(closes, 26)
        macd = None
        macd_signal = None
        macd_histogram = None
        if ema12 is not None and ema26 is not None:
            macd = round(ema12 - ema26, 2)
            # Signal line is EMA9 of MACD line
            if len(closes) >= 34:
                # Calculate EMA12 and EMA26 series for all bars (single pass each)
                ema12_series = calculate_ema_series(closes, 12)
                ema26_series = calculate_ema_series(closes, 26)
                # Derive MACD line from the series (only where both values exist)
                macd_line_vals = []
                min_len = min(len(ema12_series), len(ema26_series))
                for i in range(min_len):
                    if ema12_series[i] is not None and ema26_series[i] is not None:
                        macd_line_vals.append(ema12_series[i] - ema26_series[i])
                if len(macd_line_vals) >= 9:
                    # Reverse to chronological for EMA calculation of signal line
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

        # 5. Fibonacci levels: daily detail uses the same directional model as chart overlays.
        fib_levels = {}
        fib_meta = {}
        if len(bars) >= 20:
            fib_payload = _calculate_directional_fib_levels(
                list(reversed(highs[:60])),
                list(reversed(lows[:60])),
                list(reversed(closes[:60])),
                timeframe="1D",
                direction=None,
                lookback=min(60, len(bars)),
            )
            fib_levels = fib_payload.get("levels", {}) if fib_payload else {}
            fib_meta = fib_payload.get("meta", {}) if fib_payload else {}

        # 6. ATR (Average True Range, 14-period, Wilder's Smoothing)
        # V3.4 FIX: Vorher simpler Durchschnitt, jetzt korrekt Wilder's wie TradingView
        atr = None
        if len(bars) >= 15:
            # bars sind DESCENDING → reversed = chronologisch
            chron_highs = list(reversed(highs[:60]))
            chron_lows = list(reversed(lows[:60]))
            chron_closes = list(reversed(closes[:60]))
            tr_values = []
            for i in range(1, len(chron_highs)):
                h = chron_highs[i]
                l = chron_lows[i]
                pc = chron_closes[i - 1]
                tr = max(h - l, abs(h - pc), abs(l - pc))
                tr_values.append(tr)
            if len(tr_values) >= 14:
                # Wilder's: Seed mit SMA der ersten 14 TRs, dann smoothen
                atr_val = sum(tr_values[:14]) / 14
                for tr in tr_values[14:]:
                    atr_val = (atr_val * 13 + tr) / 14
                atr = round(atr_val, 2)

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

        # 5. Bollinger Position (CONTEXT-AWARE)
        if bb_upper is not None and bb_lower is not None:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_pos = (close - bb_lower) / bb_range
                is_uptrend = close > ma20 and (ma50 is None or close > ma50)
                if bb_pos > 0.8:
                    if is_uptrend:
                        signals.append({"name": "Bollinger", "status": "neutral", "detail": "Upper band (Trend)", "points": 1})
                        score += 1
                    else:
                        signals.append({"name": "Bollinger", "status": "bearish", "detail": "Near upper band", "points": 0})
                elif bb_pos < 0.2:
                    signals.append({"name": "Bollinger", "status": "bullish", "detail": "Near lower band", "points": 2})
                    score += 2
                else:
                    signals.append({"name": "Bollinger", "status": "neutral", "detail": "Within bands", "points": 1})
                    score += 1

        # 6. ATR (volatility) — Low ATR = tight stops possible
        if atr is not None:
            avg_price = (high + low) / 2
            atr_pct = (atr / avg_price) * 100 if avg_price > 0 else 0
            if atr_pct < 1:
                signals.append({"name": "Volatility", "status": "neutral", "detail": f"Low ATR ({atr_pct:.1f}%) - tight stops", "points": 1})
                score += 1
            elif atr_pct > 5:
                signals.append({"name": "Volatility", "status": "bearish", "detail": f"Very high ATR ({atr_pct:.1f}%)", "points": 0})
            elif atr_pct > 3:
                signals.append({"name": "Volatility", "status": "bullish", "detail": f"Good ATR ({atr_pct:.1f}%)", "points": 2})
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

        # 9. Trade Setup — ATR-based, uses confluence direction
        # V3.0: Auch für Grade B anzeigen (war vorher nur S/A)
        trade_setup = None
        if signal_grade in ['S', 'A', 'B'] and confluence_direction != "NEUTRAL":
            if confluence_direction == "LONG":
                trade_setup = _build_structured_trade_setup(
                    "LONG", close, atr, support_1, resist_1, high_20d, low_20d, range_pos
                )
            else:  # SHORT
                trade_setup = _build_structured_trade_setup(
                    "SHORT", close, atr, support_1, resist_1, high_20d, low_20d, range_pos
                )

        # 10. Candlestick data for chart (last 60 bars, reversed to chronological, with EMA overlays)
        candles = []
        bars_for_chart = list(reversed(bars[:60]))

        # Calculate EMA20 and EMA50 for each candle
        # Use full lookback (all available data) for proper EMA calculation
        ema20_series = calculate_ema_series(closes, 20) if len(closes) >= 20 else []
        ema50_series = calculate_ema_series(closes, 50) if len(closes) >= 50 else []

        # Align with chart bars (last 60 bars in chronological order)
        ema20_for_chart = []
        ema50_for_chart = []
        start_idx = len(closes) - 60
        for i, ema_val in enumerate(ema20_series):
            if start_idx + i >= 0:
                ema20_for_chart.append(ema_val)
        for i, ema_val in enumerate(ema50_series):
            if start_idx + i >= 0:
                ema50_for_chart.append(ema_val)

        # Reverse to match bars_for_chart order (chronological)
        ema20_for_chart = list(reversed(ema20_for_chart[-60:]))
        ema50_for_chart = list(reversed(ema50_for_chart[-60:]))

        for idx, b in enumerate(bars_for_chart):
            candle = {
                "t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0),
                "ema20": ema20_for_chart[idx] if idx < len(ema20_for_chart) else None,
                "ema50": ema50_for_chart[idx] if idx < len(ema50_for_chart) else None
            }
            candles.append(candle)

        # ── BI Scanner Cache Lookup: übernimm Grade/Score/Direction wenn vorhanden ──
        bi_scanner = None
        for cache_path, bi_dir in [(BI_CACHE_LONG, "LONG"), (BI_CACHE_SHORT, "SHORT")]:
            try:
                cached_results, _ = load_cache_file(cache_path)
                if cached_results:
                    normed = _normalize_keys(cached_results, _BI_KEY_MAP)
                    for item in normed:
                        if isinstance(item, dict) and item.get("ticker", "").upper() == ticker.upper():
                            bi_scanner = {
                                "grade": item.get("grade"),
                                "score": item.get("score"),
                                "max_score": item.get("max_score"),
                                "grade_label": item.get("grade_label"),
                                "confidence": item.get("confidence"),
                                "direction": bi_dir,
                                "source": "BI Scanner",
                                "entry": item.get("entry"),
                                "stop_loss": item.get("stop_loss"),
                                "tp1": item.get("tp1"),
                                "tp2": item.get("tp2"),
                                "risk_reward": item.get("risk_reward"),
                                "rvol": item.get("rvol"),
                            }
                            break
                if bi_scanner:
                    break
            except Exception as e:
                print(f"[Warning] Error loading BI Scanner cache for {ticker}: {e}")

        # Wenn BI Scanner Daten vorhanden → überschreibe signal_grade/score/confluence
        if bi_scanner:
            _bi_grade = bi_scanner["grade"] or signal_grade
            # RVOL Guard: Auch hier anwenden (nicht nur im List-Endpoint)
            _bi_rvol = bi_scanner.get("rvol") if bi_scanner.get("rvol") is not None else (rvol if rvol is not None else 0)
            if _bi_rvol < 0.7 and _bi_grade in ("S", "A", "A+"):
                _bi_grade = "B"
                bi_scanner["grade"] = "B"
                bi_scanner["grade_label"] = "B — SOLIDE (RVOL zu niedrig)"
            elif _bi_rvol < 0.5 and _bi_grade == "B":
                _bi_grade = "C"
                bi_scanner["grade"] = "C"
                bi_scanner["grade_label"] = "C — WATCH (RVOL zu niedrig)"
            signal_grade = _bi_grade
            score = bi_scanner["score"] if bi_scanner["score"] is not None else score
            confluence_direction = bi_scanner["direction"]
            confluence = {**confluence, "direction": confluence_direction}
            # Trade Setup vom BI Scanner übernehmen wenn vorhanden
            if bi_scanner.get("entry") is not None and bi_scanner.get("stop_loss") is not None:
                trade_setup = {
                    "entry": bi_scanner["entry"],
                    "stop": bi_scanner["stop_loss"],
                    "tp1": bi_scanner.get("tp1"),
                    "tp2": bi_scanner.get("tp2"),
                    "rr": bi_scanner.get("risk_reward"),
                    "direction": bi_scanner["direction"],
                }
            # V3.1: Trade Setup generieren falls BI Scanner keins hat aber Grade S/A/B
            elif not trade_setup and signal_grade in ['S', 'A', 'B'] and confluence_direction != "NEUTRAL":
                if confluence_direction == "LONG":
                    trade_setup = _build_structured_trade_setup(
                        "LONG", close, atr, support_1, resist_1, high_20d, low_20d, range_pos
                    )
                else:  # SHORT
                    trade_setup = _build_structured_trade_setup(
                        "SHORT", close, atr, support_1, resist_1, high_20d, low_20d, range_pos
                    )

        # V3.2: Extension-Score — wie weit ist Preis von MA20/VWAP entfernt
        ext_ma20 = round((close - ma20) / ma20 * 100, 1) if (ma20 and ma20 > 0) else None
        ext_vwap = round((close - vwap) / vwap * 100, 1) if (vwap and vwap > 0) else None
        _ext_max = max(abs(ext_ma20 or 0), abs(ext_vwap or 0))
        if _ext_max >= 50:
            ext_warning = "EXTREM überextended — kein Entry"
            ext_level = "extreme"
        elif _ext_max >= 30:
            ext_warning = "Stark überextended — hohes Risiko"
            ext_level = "high"
        elif _ext_max >= 15:
            ext_warning = "Moderat extended — Pullback möglich"
            ext_level = "moderate"
        else:
            ext_warning = None
            ext_level = "normal"

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
            "ext_ma20": ext_ma20, "ext_vwap": ext_vwap, "ext_warning": ext_warning, "ext_level": ext_level,
            "macd": macd, "macd_signal": macd_signal, "macd_histogram": macd_histogram,
            "bb_upper": bb_upper, "bb_lower": bb_lower,
            "fib_levels": fib_levels,
            "fib_meta": fib_meta,
            "atr": atr,
            "signals": signals, "signal_score": score, "signal_grade": signal_grade,
            "confluence": confluence,
            "trade_setup": trade_setup,
            "candles": candles,
            "bi_scanner": bi_scanner,  # None wenn nicht im Cache
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Chart Cache (In-Memory, TTL-basiert) ──
# Vermeidet wiederholte API-Calls beim schnellen Wechseln zwischen Tickers
_CHART_CACHE = {}  # key: "ticker:timeframe" → {"data": result, "ts": time.time()}
_CHART_CACHE_TTL = {"5m": 30, "15m": 60, "1H": 120, "4H": 300, "1D": 90, "1W": 300}
_CHART_CACHE_MAX = 100  # Max Einträge (LRU-artiges Cleanup)

@app.get("/api/chart-data")
def get_chart_data(
    ticker: str = Query(..., description="Ticker symbol"),
    timeframe: str = Query("1D", description="5m, 15m, 1H, 4H, 1D, 1W"),
    overlays: str = Query("ema,vwap,sr,fib", description="Comma-separated: ema,vwap,sr,fib,patterns"),
    direction: Optional[str] = Query(None, description="Optional setup direction for directional overlays")
):
    """Get OHLCV data with chart overlays for TradingView Lightweight Charts."""
    try:
        # ── Chart Cache Check ──
        fib_direction = _normalize_chart_direction(direction)
        _cache_key = f"{ticker}:{timeframe}:{overlays}:{fib_direction or 'auto'}"
        _ttl = _CHART_CACHE_TTL.get(timeframe, 120)
        if _cache_key in _CHART_CACHE:
            _cached = _CHART_CACHE[_cache_key]
            if time.time() - _cached["ts"] < _ttl:
                return _cached["data"]

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
        last_candle_ts = ohlcv[-1].get("time") if ohlcv else None
        if last_candle_ts:
            candle_age_seconds = max(0, int(time.time() - int(last_candle_ts)))
            is_chart_stale = candle_age_seconds > 7 * 24 * 3600
            result["chart_freshness"] = {
                "last_candle_time": datetime.fromtimestamp(int(last_candle_ts), tz=timezone.utc).isoformat(),
                "age_seconds": candle_age_seconds,
                "stale": is_chart_stale,
            }
            if is_chart_stale:
                result["chart_warning"] = "Chart-Daten sind stale; Preis/Trade-Setup nutzen aktuelle Scanner-/Quote-Daten."

        closes = [bar["close"] for bar in ohlcv]
        highs = [bar["high"] for bar in ohlcv]
        lows = [bar["low"] for bar in ohlcv]
        volumes = [bar.get("volume", 0) for bar in ohlcv]
        times = [bar["time"] for bar in ohlcv]

        # EMA Overlays (as time-series for line drawing)
        if "ema" in overlay_list:
            ema_overlays = {}
            ema_meta = {}
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
                            key = f"ema{period}"
                            ema_overlays[key] = ema_data
                            ema_meta[key] = {
                                "period": period,
                                "timeframe": timeframe,
                                "bars_available": len(closes),
                                "warmup_ok": len(closes) >= period * 2,
                                "model": f"EMA({period}) on selected {timeframe} candles",
                            }
                    except Exception as e:
                        print(f"[Warning] Error calculating EMA{period}: {e}")
            result["ema"] = ema_overlays
            result["ema_meta"] = ema_meta

        # VWAP V4.0: Intraday (5m/15m/1H) = Daily Reset, Higher TF (4H/1D/1W) = Session VWAP
        if "vwap" in overlay_list and len(ohlcv) >= 10:
            try:
                vwap_data = []
                cum_tp_vol = 0
                cum_vol = 0
                # Daily Reset nur bei echten Intraday-Timeframes
                use_daily_reset = timeframe in ("5m", "15m", "1H")
                prev_date = None

                for bar in ohlcv:
                    if use_daily_reset:
                        from datetime import datetime as _dt
                        bar_date = _dt.utcfromtimestamp(bar["time"]).strftime("%Y-%m-%d")
                        if prev_date is not None and bar_date != prev_date:
                            cum_tp_vol = 0
                            cum_vol = 0
                        prev_date = bar_date

                    tp = (bar["high"] + bar["low"] + bar["close"]) / 3
                    vol = bar.get("volume", 0)
                    cum_tp_vol += tp * vol
                    cum_vol += vol
                    if cum_vol > 0:
                        vwap_data.append({"time": bar["time"], "value": round(cum_tp_vol / cum_vol, 2)})
                result["vwap"] = vwap_data
            except Exception as e:
                print(f"[Warning] Error calculating VWAP: {e}")

        # Support/Resistance levels — REAL calculation from swing points + volume clusters
        if "sr" in overlay_list:
            try:
                current_price = closes[-1] if closes else 0
                if HAS_REAL_SR and len(ohlcv) >= 20:
                    # Convert to format expected by calculate_sr_from_historical
                    # It expects: [(date, open, high, low, close, volume), ...]
                    ohlc_tuples = [(b["time"], b["open"], b["high"], b["low"], b["close"], b.get("volume", 0)) for b in ohlcv]
                    sr_result = calculate_sr_from_historical(ohlc_tuples, current_price)
                    # sr_result returns: ((supports_prices, resistances_prices), fib_info)
                    # fib_info has: supports_detail [{price, type, strength}], resistances_detail [{price, type, strength}]
                    if sr_result and isinstance(sr_result, tuple) and len(sr_result) == 2:
                        (sr_prices, fib_info) = sr_result
                        sup_detail = fib_info.get("supports_detail", []) if isinstance(fib_info, dict) else []
                        res_detail = fib_info.get("resistances_detail", []) if isinstance(fib_info, dict) else []
                        # If no detail, build from price lists
                        if not sup_detail and isinstance(sr_prices, tuple) and len(sr_prices) >= 1:
                            sup_detail = [{"price": p, "strength": 3, "type": "Swing"} for p in (sr_prices[0] if isinstance(sr_prices[0], list) else [])]
                        if not res_detail and isinstance(sr_prices, tuple) and len(sr_prices) >= 2:
                            res_detail = [{"price": p, "strength": 3, "type": "Swing"} for p in (sr_prices[1] if isinstance(sr_prices[1], list) else [])]
                        result["sr"] = {
                            "support_levels": sup_detail,
                            "resistance_levels": res_detail,
                            "period_high": fib_info.get("period_high") if isinstance(fib_info, dict) else None,
                            "period_low": fib_info.get("period_low") if isinstance(fib_info, dict) else None,
                            "pdh": fib_info.get("prev_day_high") if isinstance(fib_info, dict) else None,
                            "pdl": fib_info.get("prev_day_low") if isinstance(fib_info, dict) else None,
                        }
                    else:
                        result["sr"] = {"support_levels": [], "resistance_levels": []}
                else:
                    # Simple fallback
                    last = ohlcv[-1]
                    h, l, c = last["high"], last["low"], last["close"]
                    pivot = round((h + l + c) / 3, 2)
                    result["sr"] = {
                        "support_levels": [
                            {"price": round(2 * pivot - h, 2), "strength": 3, "type": "Pivot S1"},
                            {"price": round(pivot - (h - l), 2), "strength": 2, "type": "Pivot S2"},
                        ],
                        "resistance_levels": [
                            {"price": round(2 * pivot - l, 2), "strength": 3, "type": "Pivot R1"},
                            {"price": round(pivot + (h - l), 2), "strength": 2, "type": "Pivot R2"},
                        ],
                        "pivot": pivot,
                    }
            except Exception as e:
                print(f"S/R error: {e}")

        # Diagonal Trendlines V2 — Minimum 3 Touches, extend bis zum letzten Bar
        if "sr" in overlay_list and len(ohlcv) >= 40:
            try:
                _n = len(ohlcv)
                _highs = [d["high"] for d in ohlcv]
                _lows = [d["low"] for d in ohlcv]
                _times = [d["time"] for d in ohlcv]

                # ATR für Toleranz
                _tr = [max(_highs[i] - _lows[i], abs(_highs[i] - ohlcv[i-1]["close"]), abs(_lows[i] - ohlcv[i-1]["close"])) for i in range(1, _n)]
                _atr = sum(_tr[-14:]) / min(14, len(_tr)) if _tr else 1

                # Swing-Erkennung (window proportional zur Datenmenge)
                _sw = max(4, _n // 25)
                _swing_highs = []
                _swing_lows = []
                for i in range(_sw, _n - _sw - 1):
                    if _highs[i] >= max(_highs[max(0,i-_sw):i]) and _highs[i] >= max(_highs[i+1:min(_n, i+_sw+1)]):
                        if not _swing_highs or i - _swing_highs[-1][0] >= _sw:
                            _swing_highs.append((i, _highs[i]))
                    if _lows[i] <= min(_lows[max(0,i-_sw):i]) and _lows[i] <= min(_lows[i+1:min(_n, i+_sw+1)]):
                        if not _swing_lows or i - _swing_lows[-1][0] >= _sw:
                            _swing_lows.append((i, _lows[i]))

                trendlines = []
                _tol = _atr * 0.4  # Toleranz: 40% vom ATR

                def find_best_trendline(swings, check_above=False):
                    """Findet die Linie mit den meisten Touches durch Swing-Punkte.
                    check_above=True: Resistance (kein Preis darf signifikant ÜBER die Linie)
                    check_above=False: Support (kein Preis darf signifikant UNTER die Linie)
                    """
                    best = None
                    best_score = 0
                    for a in range(len(swings)):
                        for b in range(a + 1, len(swings)):
                            i1, p1 = swings[a]
                            i2, p2 = swings[b]
                            if i2 <= i1 or i2 - i1 < max(10, _n // 8):
                                continue
                            slope = (p2 - p1) / (i2 - i1)
                            # Zähle Touches (Swings die die Linie berühren)
                            touches = 0
                            touch_indices = []
                            violated = False
                            for idx, price in swings:
                                expected = p1 + slope * (idx - i1)
                                diff = price - expected
                                if abs(diff) <= _tol:
                                    touches += 1
                                    touch_indices.append(idx)
                                elif check_above and diff > _tol * 2:
                                    # Preis weit ÜBER Resistance → ungültig
                                    violated = True
                                    break
                                elif not check_above and diff < -_tol * 2:
                                    # Preis weit UNTER Support → ungültig
                                    violated = True
                                    break
                            if violated or touches < 3:
                                continue
                            # Score = touches × Spannweite
                            span = max(touch_indices) - min(touch_indices)
                            score = touches * span
                            if score > best_score:
                                best_score = score
                                # Linie vom ersten Touch bis zum letzten Bar verlängern
                                first_i = min(touch_indices)
                                last_i = _n - 1
                                best = {
                                    "points": [
                                        {"time": _times[first_i], "price": round(p1 + slope * (first_i - i1), 2)},
                                        {"time": _times[last_i], "price": round(p1 + slope * (last_i - i1), 2)},
                                    ],
                                    "touches": touches,
                                }
                    return best

                sup = find_best_trendline(_swing_lows, check_above=False)
                if sup:
                    sup["type"] = "support"
                    trendlines.append(sup)

                res = find_best_trendline(_swing_highs, check_above=True)
                if res:
                    res["type"] = "resistance"
                    trendlines.append(res)

                if trendlines:
                    result["trendlines"] = trendlines
            except Exception as e:
                print(f"Trendline error: {e}")
                import traceback; traceback.print_exc()

        # Volume Profile (VRVP)
        if "vrvp" in overlay_list and len(ohlcv) >= 10:
            try:
                vp = calculate_volume_profile(ohlcv, num_bins=24)
                if vp:
                    # Add POC, VAH, VAL as price lines
                    # Add bins for histogram rendering
                    result["vrvp"] = {
                        "poc": round(vp["poc"], 2),
                        "vah": round(vp["vah"], 2),
                        "val": round(vp["val"], 2),
                        "bins": [{"low": round(b["low"], 2), "high": round(b["high"], 2), "mid": round(b["mid"], 2), "volume": int(b["volume"])} for b in vp["bins"]],
                        "hvns": [{"mid": round(h["mid"], 2), "volume": int(h["volume"])} for h in (vp.get("hvns") or [])],
                        "lvns": [{"mid": round(l["mid"], 2), "volume": int(l["volume"])} for l in (vp.get("lvns") or [])],
                    }
                    # Also find volume voids
                    voids = find_volume_voids(closes[-1], vp)
                    if voids:
                        result["vrvp"]["voids"] = voids
            except Exception as e:
                print(f"VRVP error: {e}")

        # Fibonacci levels — V3.0: Richtungsabhängig (SHORT=abwärts, LONG=aufwärts)
        if "fib" in overlay_list and len(ohlcv) >= 20:
            try:
                fib_payload = _calculate_directional_fib_levels(
                    highs,
                    lows,
                    closes,
                    timeframe=timeframe,
                    direction=fib_direction,
                )
                if fib_payload:
                    result["fib"] = fib_payload["levels"]
                    result["fib_direction"] = fib_payload["meta"]["direction"]
                    result["fib_meta"] = fib_payload["meta"]

            except Exception as e:
                print(f"[Warning] Error calculating Fibonacci levels: {e}")

        # Pattern detection V4.0 — Timeframe-abhängig, kein Pivot-Noise
        if "patterns" in overlay_list and HAS_PATTERNS:
            try:
                patterns_result = {}

                # Lookback + Min-Bars je Timeframe (höherer TF = mehr Bars nötig)
                tf_config = {
                    "5m": {"lookback": 50, "min_pattern_bars": 8, "harmonic_min_score": 25},
                    "15m": {"lookback": 60, "min_pattern_bars": 10, "harmonic_min_score": 30},
                    "1H": {"lookback": 80, "min_pattern_bars": 12, "harmonic_min_score": 35},
                    "4H": {"lookback": 150, "min_pattern_bars": 20, "harmonic_min_score": 40},
                    "1D": {"lookback": 200, "min_pattern_bars": 25, "harmonic_min_score": 45},
                    "1W": {"lookback": 100, "min_pattern_bars": 15, "harmonic_min_score": 50},
                }
                _tfc = tf_config.get(timeframe, tf_config["4H"])

                # Harmonic patterns
                try:
                    harmonics = find_harmonic_for_chart(ohlcv)
                    if harmonics:
                        harmonics = [h for h in harmonics if (h.get("score") or 0) >= _tfc["harmonic_min_score"]]
                        if harmonics:
                            patterns_result["harmonic"] = harmonics[:2]
                except Exception as e:
                    print(f"Harmonic error: {e}")

                # Chart patterns (Double Top/Bottom, H&S, Triangles, Wedges)
                try:
                    _lookback = min(_tfc["lookback"], len(ohlcv))
                    chart_pats = detect_chart_patterns(ohlcv, lookback=_lookback)
                    if chart_pats:
                        # CRITICAL: detect_chart_patterns Indizes sind relativ zu ohlcv[-lookback:]
                        # Wir brauchen den Offset zum vollen ohlcv-Array
                        _idx_offset = len(ohlcv) - _lookback

                        # Filtere Patterns mit zu wenig Bars-Abstand
                        filtered = []
                        for cp in chart_pats:
                            dp = cp.get("draw_points", [])
                            if len(dp) >= 2:
                                indices = [p.get("index", 0) for p in dp if p.get("index") is not None]
                                if indices:
                                    span = max(indices) - min(indices)
                                    if span >= _tfc["min_pattern_bars"]:
                                        filtered.append(cp)
                            else:
                                filtered.append(cp)

                        # Nur die besten 3 Patterns (nach Confidence sortieren)
                        conf_order = {"High": 3, "Medium": 2, "Low": 1}
                        filtered.sort(key=lambda x: conf_order.get(x.get("confidence", "Low"), 0), reverse=True)
                        chart_pats = filtered[:3]

                        for cp in chart_pats:
                            # detect_index ist relativ zum Slice → Offset addieren
                            idx = cp.get("detect_index")
                            if idx is not None:
                                actual_idx = idx + _idx_offset
                                if 0 <= actual_idx < len(ohlcv):
                                    cp["time"] = ohlcv[actual_idx]["time"]
                                else:
                                    cp["time"] = ohlcv[-1]["time"]
                            elif ohlcv:
                                cp["time"] = ohlcv[-1]["time"]
                            # draw_points: Slice-Index + Offset → ohlcv-Index → Time
                            if cp.get("draw_points"):
                                for dp in cp["draw_points"]:
                                    di = dp.get("index")
                                    if di is not None:
                                        actual_di = di + _idx_offset
                                        if 0 <= actual_di < len(ohlcv):
                                            dp["time"] = ohlcv[actual_di]["time"]
                        patterns_result["chart_patterns"] = chart_pats
                except Exception as e:
                    print(f"Chart patterns error: {e}")
                    import traceback; traceback.print_exc()

                # Pivots: NICHT im Chart anzeigen (nur Noise)
                # Werden intern von Harmonics genutzt, aber nicht als Marker gerendert

                # Order blocks + Liquidity: Entfernt — zu viel Noise im Chart

                # V2.5: Kohärenz-Filter — widersprüchliche bullish+bearish Patterns bereinigen
                # Bestimme dominante Richtung aus Preis-Trend
                try:
                    _cp = closes[-1]
                    _sma20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 5 else _cp
                    _sma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 10 else _sma20
                    _trend_bullish = _cp > _sma20 and _sma20 > _sma50
                    _trend_bearish = _cp < _sma20 and _sma20 < _sma50
                    # Trend neutral wenn weder klar bullish noch bearish

                    if _trend_bullish or _trend_bearish:
                        _dominant = "bullish" if _trend_bullish else "bearish"
                        _opposing = "bearish" if _trend_bullish else "bullish"

                        # Chart Patterns: Gegensätzliche entfernen (außer "High" Confidence)
                        if "chart_patterns" in patterns_result:
                            patterns_result["chart_patterns"] = [
                                p for p in patterns_result["chart_patterns"]
                                if p.get("type") != _opposing or p.get("confidence") == "High"
                            ]

                        # Order Blocks: Nur die zur Trend-Richtung passenden behalten
                        if "order_blocks" in patterns_result:
                            obs = patterns_result["order_blocks"]
                            if _dominant == "bullish":
                                # Bei Uptrend: Bearish OBs entfernen wenn Bullish OB vorhanden
                                if obs.get("nearest_bull_ob") and obs.get("nearest_bear_ob"):
                                    obs.pop("nearest_bear_ob", None)
                            else:
                                if obs.get("nearest_bear_ob") and obs.get("nearest_bull_ob"):
                                    obs.pop("nearest_bull_ob", None)
                except Exception as _coh_err:
                    print(f"[Warning] Coherence filter error: {_coh_err}")

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
            except Exception as e:
                print(f"[Warning] Error calculating Bollinger Bands: {e}")

        # Volume data
        vol_data = [{"time": bar["time"], "value": bar.get("volume", 0), "color": "rgba(16,185,129,0.3)" if bar["close"] >= bar["open"] else "rgba(220,38,38,0.3)"} for bar in ohlcv]
        result["volume"] = vol_data

        # ── Cache speichern ──
        _CHART_CACHE[_cache_key] = {"data": result, "ts": time.time()}
        # Cleanup wenn zu viele Einträge
        if len(_CHART_CACHE) > _CHART_CACHE_MAX:
            oldest = sorted(_CHART_CACHE.items(), key=lambda x: x[1]["ts"])[:20]
            for k, _ in oldest:
                _CHART_CACHE.pop(k, None)

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Crypto Chart Cache ──
_CRYPTO_CHART_CACHE = {}
_CRYPTO_CHART_TTL = 120  # 2 Minuten (CoinGecko Rate Limits)

@app.get("/api/crypto-chart")
def get_crypto_chart(
    coin_id: str = Query(..., description="CoinGecko coin ID (e.g. bitcoin, based-one)"),
    days: int = Query(30, description="Number of days")
):
    """Get OHLCV chart data for crypto coins via CoinGecko."""
    try:
        # ── Cache Check ──
        _cc_key = f"{coin_id}:{days}"
        if _cc_key in _CRYPTO_CHART_CACHE:
            _cached = _CRYPTO_CHART_CACHE[_cc_key]
            if time.time() - _cached["ts"] < _CRYPTO_CHART_TTL:
                return _cached["data"]

        bars = fetch_daily_candles_crypto(coin_id, days=min(days, 90))
        if not bars or len(bars) < 2:
            # Leere Antwort statt 404 → Frontend kann "Retry" anbieten
            return {
                "ticker": coin_id,
                "timeframe": "1D",
                "candles": [],
                "volume": [],
                "ema": {},
                "error": "rate_limited",
            }

        # Convert to TradingView Lightweight Charts format
        candles = []
        vol_data = []
        for bar in bars:
            ts = int(bar.get("t", 0) / 1000) if bar.get("t", 0) > 1e10 else int(bar.get("t", 0))
            candle = {
                "time": ts,
                "open": round(bar["o"], 6),
                "high": round(bar["h"], 6),
                "low": round(bar["l"], 6),
                "close": round(bar["c"], 6),
            }
            candles.append(candle)
            vol_data.append({
                "time": ts,
                "value": int(bar.get("v", 0)),
                "color": "rgba(16,185,129,0.3)" if bar["c"] >= bar["o"] else "rgba(220,38,38,0.3)"
            })

        # Simple EMA overlays
        closes = [c["close"] for c in candles]
        times = [c["time"] for c in candles]
        ema_overlays = {}
        for period in [9, 20, 50]:
            if len(closes) >= period:
                try:
                    ema_vals = calculate_ema_series(closes, period)
                    ema_data = []
                    start = len(times) - len(ema_vals)
                    for i, val in enumerate(ema_vals):
                        if val is not None:
                            ema_data.append({"time": times[start + i], "value": round(val, 6)})
                    if ema_data:
                        ema_overlays[f"ema{period}"] = ema_data
                except Exception:
                    pass

        _result = {
            "ticker": coin_id,
            "timeframe": "1D",
            "candles": candles,
            "volume": vol_data,
            "ema": ema_overlays,
        }
        # ── Cache speichern ──
        _CRYPTO_CHART_CACHE[_cc_key] = {"data": _result, "ts": time.time()}
        if len(_CRYPTO_CHART_CACHE) > 80:
            oldest = sorted(_CRYPTO_CHART_CACHE.items(), key=lambda x: x[1]["ts"])[:20]
            for k, _ in oldest:
                _CRYPTO_CHART_CACHE.pop(k, None)
        return _result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Exchange-spezifischer Chart-Endpoint (NLS Coins) ──────────────────────────
@app.get("/api/exchange-chart")
def get_exchange_chart(
    symbol: str = Query(..., description="Contract symbol (e.g. BTCUSDT, SXTUSDT)"),
    exchange: str = Query(..., description="Exchange name (binance, mexc, bitget, crypto_com)"),
    timeframe: str = Query("1h", description="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)"),
    count: int = Query(100, description="Number of candles"),
):
    """Holt Chart-Daten direkt von einer Exchange (für NLS Coins die nicht auf CoinGecko sind)."""
    if not HAS_NEW_LISTING_SCANNER:
        raise HTTPException(status_code=501, detail="New listing scanner module not available")

    try:
        # ── Cache Check ──
        _ex_key = f"ex:{symbol}:{exchange}:{timeframe}"
        _ex_ttl = 60 if timeframe in ("5m", "15m") else 120
        if _ex_key in _CHART_CACHE:
            _cached = _CHART_CACHE[_ex_key]
            if time.time() - _cached["ts"] < _ex_ttl:
                return _cached["data"]

        # Timeframe-Mapping von Frontend-Format
        tf_map = {"5m": "5m", "15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d", "1W": "1d"}
        tf = tf_map.get(timeframe, timeframe.lower())
        limit = min(count, 500)
        if timeframe == "1W":
            limit = min(count * 7, 500)

        bars = fetch_candles_for(symbol, exchange, timeframe=tf, count=limit)
        if not bars or len(bars) < 2:
            raise HTTPException(status_code=404, detail=f"No chart data for '{symbol}' on {exchange}")

        # Weekly aggregation wenn 1W angefragt
        if timeframe == "1W":
            from datetime import datetime as _dt
            weekly = {}
            for b in bars:
                dt = _dt.utcfromtimestamp(b["timestamp"])
                week_start = dt - timedelta(days=dt.weekday())
                wk = int(week_start.replace(hour=0, minute=0, second=0).timestamp())
                if wk not in weekly:
                    weekly[wk] = {"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": 0, "timestamp": wk}
                else:
                    weekly[wk]["high"] = max(weekly[wk]["high"], b["high"])
                    weekly[wk]["low"] = min(weekly[wk]["low"], b["low"])
                    weekly[wk]["close"] = b["close"]
                weekly[wk]["volume"] += b.get("volume", 0)
            bars = sorted(weekly.values(), key=lambda x: x["timestamp"])

        # Format für TradingView Lightweight Charts
        candles = []
        vol_data = []
        for bar in bars:
            ts = bar["timestamp"]
            candle = {
                "time": ts,
                "open": round(bar["open"], 8),
                "high": round(bar["high"], 8),
                "low": round(bar["low"], 8),
                "close": round(bar["close"], 8),
            }
            candles.append(candle)
            vol_data.append({
                "time": ts,
                "value": int(bar.get("volume", 0)),
                "color": "rgba(16,185,129,0.3)" if bar["close"] >= bar["open"] else "rgba(220,38,38,0.3)"
            })

        # EMA Overlays berechnen
        closes = [c["close"] for c in candles]
        times = [c["time"] for c in candles]
        ema_overlays = {}
        for period in [9, 20, 50]:
            if len(closes) >= period:
                try:
                    ema_vals = calculate_ema_series(closes, period)
                    ema_data = []
                    start = len(times) - len(ema_vals)
                    for i, val in enumerate(ema_vals):
                        if val is not None:
                            ema_data.append({"time": times[start + i], "value": round(val, 8)})
                    if ema_data:
                        ema_overlays[f"ema{period}"] = ema_data
                except Exception:
                    pass

        _result = {
            "ticker": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
            "candles": candles,
            "volume": vol_data,
            "ema": ema_overlays,
        }

        # ── Cache Save ──
        _CHART_CACHE[_ex_key] = {"data": _result, "ts": time.time()}
        if len(_CHART_CACHE) > 100:
            _sorted = sorted(_CHART_CACHE.items(), key=lambda x: x[1]["ts"])
            for _dk, _ in _sorted[:20]:
                _CHART_CACHE.pop(_dk, None)

        return _result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── AI-Analyse Cache (30 Min TTL, spart 80-90% der API-Calls) ──
_AI_CACHE = {}  # {ticker: {"analysis": ..., "timestamp": ..., "expires": ...}}
_AI_CACHE_TTL = 1800  # 30 Minuten
_AI_USER_CALLS = {}  # {email: {"date": "2026-04-05", "count": 0}}


@app.get("/api/ai-analysis")
def get_ai_analysis(
    ticker: str = Query(..., description="Ticker symbol"),
    authorization: str = Header(None),
):
    """Generate AI analysis for a ticker using the configured AI provider.
    Premium Feature: Nur Pro ($79) und Elite ($149) Pläne.
    Cached für 30 Min pro Ticker (alle User teilen den Cache).
    """
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not configured")

    # ── Plan-Check: AI nur für berechtigte User ──
    user_email = None
    user_plan = "expired"
    ai_limit = 0
    try:
        from modules.auth import verify_token, get_plan_features
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        elif authorization:
            token = authorization

        if token:
            payload = verify_token(token)
            if payload:
                user_email = payload.get("email", "")
                user_plan = payload.get("plan", "expired")
                features = get_plan_features(user_plan)
                if not features.get("has_ai_analysis", False):
                    return {
                        "ticker": ticker,
                        "analysis": None,
                        "error": "ai_not_available",
                        "message": f"AI-Analyse ist ab dem Pro-Plan ($79/Mo) verfügbar. Dein Plan: {user_plan}",
                        "upgrade_required": True,
                    }
                ai_limit = features.get("ai_calls_per_day", 0)
    except Exception:
        pass  # Kein Auth-Modul = kein Plan-Check (Admin/Dev)

    # ── Rate-Limit pro User ──
    if user_email and ai_limit < 999:
        today = datetime.now().strftime("%Y-%m-%d")
        user_key = f"{user_email}"
        if user_key not in _AI_USER_CALLS or _AI_USER_CALLS[user_key].get("date") != today:
            _AI_USER_CALLS[user_key] = {"date": today, "count": 0}
        if _AI_USER_CALLS[user_key]["count"] >= ai_limit:
            return {
                "ticker": ticker,
                "analysis": None,
                "error": "ai_limit_reached",
                "message": f"Tageslimit erreicht ({ai_limit} AI-Analysen/Tag). Upgrade auf Elite für unlimitiert.",
                "upgrade_required": True,
            }

    # ── Cache prüfen (30 Min TTL) ──
    ticker_upper = ticker.upper()
    now = time.time()
    if ticker_upper in _AI_CACHE:
        cached = _AI_CACHE[ticker_upper]
        if cached.get("expires", 0) > now:
            # Cache-Hit: User-Call zählen, aber keinen API-Call machen
            if user_email and ai_limit < 999:
                _AI_USER_CALLS[f"{user_email}"]["count"] += 1
            return {
                "ticker": ticker,
                "analysis": cached["analysis"],
                "model": cached.get("model", AI_PROVIDER_MODEL),
                "timestamp": cached["timestamp"],
                "cached": True,
            }

    # ── Preisdaten holen ──
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

    # ── AI provider API call ──
    try:
        ai_resp = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": AI_PROVIDER_MODEL,
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
        if ai_resp.status_code == 200:
            content = ai_resp.json().get("content", [{}])[0].get("text", "Analyse nicht verfuegbar")
            ts = datetime.now().isoformat()

            # In Cache speichern
            _AI_CACHE[ticker_upper] = {
                "analysis": content,
                "model": AI_PROVIDER_MODEL,
                "timestamp": ts,
                "expires": now + _AI_CACHE_TTL,
            }

            # Alte Cache-Einträge aufräumen (max 200)
            if len(_AI_CACHE) > 200:
                expired = [k for k, v in _AI_CACHE.items() if v.get("expires", 0) < now]
                for k in expired:
                    del _AI_CACHE[k]

            # User-Call zählen
            if user_email and ai_limit < 999:
                _AI_USER_CALLS[f"{user_email}"]["count"] += 1

            return {"ticker": ticker, "analysis": content, "model": AI_PROVIDER_MODEL, "timestamp": ts, "cached": False}
        else:
            return {"ticker": ticker, "analysis": f"API Fehler: {ai_resp.status_code}", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"ticker": ticker, "analysis": f"Fehler: {str(e)}", "timestamp": datetime.now().isoformat()}


@app.get("/api/strategies", response_model=StrategiesResponse)
def list_strategies(market_type: str = Query("stocks", description="Market type: stocks, crypto, futures, forex")):
    """List all strategies for a given market type. Strips internal fields for public API."""
    strategies = get_public_strategies_for_market(market_type)

    # Strip internal calculation details — users should not see filters, logic, thresholds
    # NO description — contains internal details; frontend has its own guide texts
    _safe_keys = {"stocks_only", "needs_history", "needs_harmonic",
                  "needs_volume_profile", "needs_ma", "needs_cup_handle", "ma_type", "ma_period",
                  "best_time", "best_pairs", "harmonic_direction",
                  "display_group", "merged_from", "canonical_name"}
    safe_strategies = {}
    for name, config in strategies.items():
        safe_strategies[name] = {k: v for k, v in config.items() if k in _safe_keys}

    return StrategiesResponse(
        market_type=market_type,
        strategies=safe_strategies,
        count=len(safe_strategies),
    )


@app.post("/api/scan")
def run_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    """
    Run main scanner with specified strategy and market type.
    Routes to correct scanner based on strategy parameter.
    """
    if request.market_type != "crypto" and not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    # Validate strategy
    resolved_strategy = resolve_strategy_name(request.strategy, request.market_type)
    strategies = get_strategies_for_market(request.market_type)
    if resolved_strategy not in strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{request.strategy}' for market '{request.market_type}'"
        )

    # Route to correct scanner based on strategy
    strategy_lower = resolved_strategy.lower()

    if request.market_type == "crypto":
        _strat_name = resolved_strategy
        _safe_key = f"crypto_strat_{_strat_name.lower().replace(' ', '_')}"
        if _safe_key not in _scan_status:
            with _scan_lock:
                _scan_status[_safe_key] = {"running": False, "last_run": None, "next_run": None, "interval_min": 5}
        _run_scan_safe(_safe_key, lambda: _crypto_strategy_scan_wrapper(_strat_name))
        return {
            "status": "started",
            "message": f"Crypto-Strategie-Scan gestartet: {resolved_strategy}",
            "strategy": resolved_strategy,
            "requested_strategy": request.strategy,
            "market_type": request.market_type,
        }

    if "bi_long" in strategy_lower:
        _run_scan_safe("bi_long", lambda: _bi_background_scan_wrapper("long"))
        return {
            "status": "started",
            "message": "BI Scanner (Long) started",
            "strategy": resolved_strategy,
        }
    elif "bi_short" in strategy_lower:
        _run_scan_safe("bi_short", lambda: _bi_background_scan_wrapper("short"))
        return {
            "status": "started",
            "message": "BI Scanner (Short) started",
            "strategy": resolved_strategy,
        }
    elif "biotech" in strategy_lower:
        _run_scan_safe("biotech", _biotech_scan_wrapper)
        return {
            "status": "started",
            "message": "Biotech Scanner started",
            "strategy": resolved_strategy,
        }
    elif "early" in strategy_lower or "movers" in strategy_lower:
        _run_scan_safe("early_movers", _early_movers_wrapper)
        return {
            "status": "started",
            "message": "Early Movers Scanner started",
            "strategy": resolved_strategy,
        }
    elif "volume" in strategy_lower or "spike" in strategy_lower:
        _run_scan_safe("volume_spikes", _volume_spikes_wrapper)
        return {
            "status": "started",
            "message": "Volume Spikes Scanner started",
            "strategy": resolved_strategy,
        }
    elif strategy_lower in ("bear", "bear scanner", "bear scan"):
        # V2.6b: Nur explizit "bear" — nicht mehr jedes "short" abfangen
        # "Breakout Short", "Breakdown Short" etc. sind generische Strategien
        _run_scan_safe("bear", _bear_scan_wrapper)
        return {
            "status": "started",
            "message": "Bear Scanner started",
            "strategy": resolved_strategy,
        }
    elif "crash" in strategy_lower:
        _run_scan_safe("crash_monitor", _crash_monitor_wrapper)
        return {
            "status": "started",
            "message": "Crash Monitor started",
            "strategy": resolved_strategy,
        }
    elif "btc" in strategy_lower or "divergenz" in strategy_lower:
        _run_scan_safe("btc_divergenz", _btc_divergenz_wrapper)
        return {
            "status": "started",
            "message": "BTC Divergenz Scanner started",
            "strategy": resolved_strategy,
        }
    elif "money" in strategy_lower or "flow" in strategy_lower:
        _run_scan_safe("money_flow", _money_flow_wrapper)
        return {
            "status": "started",
            "message": "Money Flow Scanner started",
            "strategy": resolved_strategy,
        }
    elif "turtle" in strategy_lower:
        _run_scan_safe("turtle", _turtle_scan_wrapper)
        return {
            "status": "started",
            "message": "Turtle Breakout Scanner started",
            "strategy": resolved_strategy,
        }
    elif "listing" in strategy_lower:
        if HAS_NEW_LISTING_SCANNER:
            _run_scan_safe("new_listing", _new_listing_wrapper)
            return {
                "status": "started",
                "message": "New Listing Scanner started",
                "strategy": resolved_strategy,
            }
        else:
            raise HTTPException(status_code=400, detail="New Listing Scanner not available")
    else:
        # Generische Strategie (PM Losers, PM Gainers, AH, Whale Watch, etc.)
        # Nutzt Polygon Snapshot + Filter aus strategies.py
        _strat_name = resolved_strategy
        # V2.2: Separate scan-locks pro Strategie statt ein einziger "strategy_scan"
        _safe_key = f"strat_{_strat_name.lower().replace(' ', '_')}"
        if _safe_key not in _scan_status:
            with _scan_lock:
                _scan_status[_safe_key] = {"running": False, "last_run": None, "next_run": None, "interval_min": 5}
        _run_scan_safe(_safe_key, lambda: _strategy_scan_wrapper(_strat_name))
        return {
            "status": "started",
            "message": f"Strategie-Scan gestartet: {resolved_strategy}",
            "strategy": resolved_strategy,
            "requested_strategy": request.strategy,
        }


@app.get("/api/scan-results", response_model=ScanResultsResponse)
def get_scan_results(
    strategy: str = Query(None, description="Strategy name (e.g., bi_long, biotech, bear)"),
    direction: str = Query(None, description="Backward compat: long or short (only for BI scanner)"),
    market_type: str = Query("stocks", description="Market type for generic strategy caches")
):
    """Get cached scan results for specified strategy.

    Supports both:
    - ?strategy=bi_long (new way)
    - ?direction=long (old way, for backward compatibility)
    """
    # Determine cache file based on strategy parameter
    cache_file = None
    normalize_map = None

    if strategy:
        resolved_strategy = resolve_strategy_name(strategy, market_type)
        strategy_lower = resolved_strategy.lower()
        if market_type == "crypto":
            cache_file = _strategy_cache_path(resolved_strategy, "crypto")
        elif "bi_long" in strategy_lower:
            cache_file = BI_CACHE_LONG
            normalize_map = _BI_KEY_MAP
        elif "bi_short" in strategy_lower:
            cache_file = BI_CACHE_SHORT
            normalize_map = _BI_KEY_MAP
        elif "biotech" in strategy_lower:
            cache_file = BIOTECH_CACHE
        elif strategy_lower in ("bear", "bear scanner", "bear scan"):
            cache_file = BEAR_CACHE
        elif "early" in strategy_lower or "movers" in strategy_lower:
            cache_file = EARLY_MOVERS_CACHE
        elif "volume" in strategy_lower or "spike" in strategy_lower:
            cache_file = VOLUME_SPIKES_CACHE
        elif "crash" in strategy_lower:
            cache_file = CRASH_MONITOR_CACHE
        elif "btc" in strategy_lower or "divergenz" in strategy_lower:
            cache_file = BTC_DIVERGENZ_CACHE
        elif "money" in strategy_lower or "flow" in strategy_lower:
            cache_file = MONEY_FLOW_CACHE
        elif "listing" in strategy_lower:
            cache_file = NEW_LISTING_CACHE
        elif "turtle" in strategy_lower:
            cache_file = TURTLE_CACHE
        else:
            # V2.2: Generische Strategie — versuche zuerst strategie-spezifischen Cache
            _strat_cache = _strategy_cache_path(resolved_strategy, market_type)
            if os.path.exists(_strat_cache):
                cache_file = _strat_cache
            elif market_type != "stocks":
                cache_file = _strat_cache
            else:
                cache_file = STRATEGY_SCAN_CACHE  # Fallback
    elif direction:
        # Backward compatibility: direction parameter (old way)
        if direction not in ["long", "short"]:
            raise HTTPException(status_code=400, detail="Direction must be 'long' or 'short'")
        cache_file = BI_CACHE_LONG if direction == "long" else BI_CACHE_SHORT
        normalize_map = _BI_KEY_MAP
    else:
        # Default to BI long if neither is specified
        cache_file = BI_CACHE_LONG
        normalize_map = _BI_KEY_MAP

    results, cached_at = load_cache_file(cache_file)
    if normalize_map:
        results = _normalize_keys(results, normalize_map)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")

    scanner_name = "strategy_scan"
    if direction:
        scanner_name = f"bi_{direction}"
    elif strategy:
        sl = str(strategy).lower()
        if "short" in sl and "bi" in sl:
            scanner_name = "bi_short"
        elif "long" in sl and "bi" in sl:
            scanner_name = "bi_long"
        elif "biotech" in sl:
            scanner_name = "biotech"
        elif "bear" in sl:
            scanner_name = "bear"
        elif "orb" in sl:
            scanner_name = "orb"
        elif "turtle" in sl:
            scanner_name = "turtle"
        elif "crypto" in market_type:
            scanner_name = "early_movers"
    results = _decorate_scan_results(results, scanner_name, cache_age)
    results = _apply_signal_only_policy(scanner_name, results)
    quality = _scan_quality_payload(scanner_name, cache_age, results)

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
        data_source=quality["data_source"],
        data_quality=quality,
        warnings=quality["warnings"],
        exclusion_policy=quality["exclusion_policy"],
    )


@app.post("/api/bi-scan")
def trigger_bi_scan(request: BIScanRequest):
    """Trigger BI background scan (long or short direction)."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    if request.direction not in ["long", "short"]:
        raise HTTPException(status_code=400, detail="Direction must be 'long' or 'short'")

    # Thread statt BackgroundTasks — überlebt Browser-Reload
    _run_scan_safe(f"bi_{request.direction}", lambda: _bi_background_scan_wrapper(request.direction))

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
    results = _normalize_keys(results, _BI_KEY_MAP)

    # RVOL Guard: Korrigiere Grades bei Auslieferung (Sicherheitsnetz)
    for r in results:
        if isinstance(r, dict):
            _rv_raw = r.get("rvol", r.get("RVOL", None))
            _rv = _rv_raw if _rv_raw is not None else 0
            _gr = r.get("grade", r.get("BI_Grade", ""))
            if _rv < 0.7 and _gr in ("S", "A", "A+"):
                r["grade"] = "B"
                r["grade_label"] = "B — SOLIDE (RVOL zu niedrig)"
            elif _rv < 0.5 and _gr == "B":
                r["grade"] = "C"
                r["grade_label"] = "C — WATCH (RVOL zu niedrig)"

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")

    scanner_name = f"bi_{direction}"
    results = _decorate_scan_results(results, scanner_name, cache_age)
    results = _apply_signal_only_policy(scanner_name, results)
    quality = _scan_quality_payload(scanner_name, cache_age, results)
    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
        data_source=quality["data_source"],
        data_quality=quality,
        warnings=quality["warnings"],
        exclusion_policy=quality["exclusion_policy"],
    )


@app.post("/api/bear-scan")
def trigger_bear_scan():
    """Trigger bear scanner (short opportunities)."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    _run_scan_safe("bear", _bear_scan_wrapper)

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
        except Exception as e:
            print(f"[Warning] {e}")

    results = _decorate_scan_results(results, "bear", cache_age)
    results = _apply_signal_only_policy("bear", results)
    quality = _scan_quality_payload("bear", cache_age, results)
    result_count = _effective_scan_result_count("bear", results)
    return ScanResultsResponse(
        status="success",
        count=result_count,
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
        data_source=quality["data_source"],
        data_quality=quality,
        warnings=quality["warnings"],
        exclusion_policy=quality["exclusion_policy"],
    )


@app.post("/api/biotech-scan")
def trigger_biotech_scan():
    """Trigger biotech background scan (FDA catalysts, clinical trials)."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    _run_scan_safe("biotech", _biotech_scan_wrapper)

    return {
        "status": "started",
        "message": "Biotech scan started",
    }


@app.get("/api/biotech-results", response_model=ScanResultsResponse)
def get_biotech_results():
    """Get cached biotech scan results."""
    _enrich_biotech_alert_trade_levels()
    results, cached_at = load_cache_file(BIOTECH_CACHE)
    results = _normalize_keys(results, _BIOTECH_KEY_MAP)
    results = _sanitize_biotech_public_results(results)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")

    results = _decorate_scan_results(results, "biotech", cache_age)
    results = _apply_signal_only_policy("biotech", results)
    quality = _scan_quality_payload("biotech", cache_age, results)
    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
        data_source=quality["data_source"],
        data_quality=quality,
        warnings=quality["warnings"],
        exclusion_policy=quality["exclusion_policy"],
    )


# ── Early Movers (Crypto Scanner) ──
EARLY_MOVERS_CACHE = "/tmp/early_movers_cache.json"
_CG_MARKETS_STATUS = {"source": "unknown", "partial": False, "warning": None}

CRYPTO_NARRATIVES = {
    # AI / ML
    "fetch-ai": "AI", "singularitynet": "AI", "ocean-protocol": "AI",
    "render-token": "AI", "akash-network": "AI", "bittensor": "AI",
    "artificial-superintelligence-alliance": "AI", "near": "AI",
    "worldcoin-wld": "AI", "arkham": "AI", "numeraire": "AI",
    "phala-network": "AI", "nosana": "AI", "virtuals-protocol": "AI",
    "ai16z": "AI", "griffain": "AI", "goatseus-maximus": "AI",
    "io-net": "AI", "grass": "AI",
    # Meme
    "dogecoin": "Meme", "shiba-inu": "Meme", "pepe": "Meme",
    "dogwifcoin": "Meme", "bonk": "Meme", "floki": "Meme",
    "brett-based": "Meme", "mog-coin": "Meme", "popcat": "Meme",
    "cat-in-a-dogs-world": "Meme", "neiro-on-eth": "Meme",
    "fartcoin": "Meme", "trump": "Meme", "melania-meme": "Meme",
    "peanut-the-squirrel": "Meme", "act-i-the-ai-prophecy": "Meme",
    # RWA (Real World Assets)
    "ondo-finance": "RWA", "mantra": "RWA", "polymesh": "RWA",
    "centrifuge": "RWA", "goldfinch": "RWA", "maple": "RWA",
    "clearpool": "RWA", "pendle": "RWA",
    # DePIN
    "helium": "DePIN", "theta-token": "DePIN", "filecoin": "DePIN",
    "arweave": "DePIN", "hivemapper": "DePIN",
    "iotex": "DePIN", "dimo-network": "DePIN",
    # L1 / L2
    "solana": "L1", "avalanche-2": "L1", "sui": "L1",
    "aptos": "L1", "sei-network": "L1", "injective-protocol": "L1",
    "celestia": "L1", "monad": "L1", "berachain": "L1",
    "arbitrum": "L2", "optimism": "L2", "polygon-ecosystem-token": "L2",
    "starknet": "L2", "zksync": "L2", "base-protocol": "L2",
    # DeFi
    "uniswap": "DeFi", "aave": "DeFi", "lido-dao": "DeFi",
    "maker": "DeFi", "curve-dao-token": "DeFi", "compound-governance-token": "DeFi",
    "jupiter-exchange-solana": "DeFi", "raydium": "DeFi",
    "hyperliquid": "DeFi", "ethena": "DeFi",
    # Gaming
    "immutable-x": "Gaming", "the-sandbox": "Gaming", "axie-infinity": "Gaming",
    "gala": "Gaming", "illuvium": "Gaming", "beam-2": "Gaming",
    "ronin": "Gaming", "pixels": "Gaming",
}

EXCLUDED_CRYPTO_SYMBOLS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USDS", "USDD",
    "USDP", "PYUSD", "FRAX", "LUSD", "GUSD", "DOLA", "SUSD", "EUSD", "USDL",
    "USDY", "USDX", "EURC", "EUROC", "WBTC", "CBTC", "TBTC", "LBTC", "WETH",
    "WBNB", "STETH", "WSTETH", "RETH", "CBETH", "WBETH", "WEETH", "EZETH",
    "METH", "RSETH", "SFRXETH", "FRXETH", "PAXG", "XAUT",
}
EXCLUDED_CRYPTO_TEXT_TERMS = (
    "stablecoin", "stable coin", "wrapped ", "bridged ", "liquid staked",
    "liquid-staked", "staked ether", "staked eth", "staked bitcoin",
    "staked btc", "staking ether", "staking eth", "tether", "usd coin",
    "paypal usd", "binance usd", "frax", "ethena usde", "pax gold",
    "tether gold", "tokenized gold", "gold-backed", "gold backed",
)
PERP_OI_HISTORY_CACHE = "/tmp/early_movers_perp_oi_history.json"


def _crypto_asset_exclusion_reason(symbol: str, coin_id: str = "", name: str = "") -> Optional[str]:
    """Return why a crypto asset should not be treated as a directional mover."""
    sym = (symbol or "").upper().strip()
    cid = (coin_id or "").lower().strip()
    lower_name = (name or "").lower().strip()
    text = f"{cid} {lower_name}"

    if sym in EXCLUDED_CRYPTO_SYMBOLS:
        return f"excluded stable/wrapped symbol {sym}"
    if any(term in text for term in EXCLUDED_CRYPTO_TEXT_TERMS):
        return "excluded stable/wrapped/liquid-staking asset"
    if sym.startswith("USD") and len(sym) <= 5:
        return f"excluded USD-pegged symbol {sym}"
    return None


def _is_excluded_crypto_asset(symbol: str, coin_id: str = "", name: str = "") -> bool:
    return _crypto_asset_exclusion_reason(symbol, coin_id, name) is not None


def _round_crypto_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if number >= 100:
        return round(number, 2)
    if number >= 1:
        return round(number, 4)
    if number >= 0.01:
        return round(number, 6)
    return round(number, 8)


def _enrich_perp_oi_history(perp_data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Attach OI change since the previous scan so positioning is not just a snapshot."""
    if not isinstance(perp_data, dict) or not perp_data:
        return perp_data or {}

    now = time.time()
    previous = {}
    try:
        if os.path.exists(PERP_OI_HISTORY_CACHE):
            with open(PERP_OI_HISTORY_CACHE, "r") as _f:
                previous = json.load(_f)
    except Exception:
        previous = {}

    next_history = {}
    for sym, info in perp_data.items():
        if not isinstance(info, dict):
            continue
        oi_now = float(info.get("oi_usdt") or 0)
        prev = previous.get(sym, {}) if isinstance(previous, dict) else {}
        oi_prev = float(prev.get("oi_usdt") or 0)
        prev_ts = float(prev.get("timestamp") or 0)
        if oi_now > 0 and oi_prev > 0:
            info["oi_change_pct"] = round(((oi_now - oi_prev) / oi_prev) * 100, 2)
            info["oi_history_age_seconds"] = int(max(0, now - prev_ts)) if prev_ts else None
        else:
            info.setdefault("oi_change_pct", None)
            info.setdefault("oi_history_age_seconds", None)
        next_history[sym] = {
            "oi_usdt": oi_now,
            "volume24_usdt": float(info.get("volume24_usdt") or 0),
            "timestamp": now,
        }

    try:
        with open(PERP_OI_HISTORY_CACHE, "w") as _f:
            json.dump(next_history, _f, default=_serialize_json)
    except Exception:
        pass

    return perp_data


def _build_early_mover_long_setup(
    entry: Dict[str, Any],
    phase: int,
    score: int,
    btc_24h: float,
    btc_7d: float,
) -> Dict[str, Any]:
    """Build a conditional long plan from daily crypto market data.

    Early Movers gives tactical levels plus the confirmation that must happen
    before the app can mark the setup as JETZT_TRADEN.
    """
    price = float(entry.get("Price") or 0)
    if price <= 0:
        return {"direction": "LONG", "trade_action": "NO_TRADE", "warnings": ["missing price"]}

    high_24h = float(entry.get("High24h") or price)
    low_24h = float(entry.get("Low24h") or price)
    if high_24h <= low_24h:
        high_24h = price * 1.04
        low_24h = price * 0.96

    range_24h = max(high_24h - low_24h, price * 0.025)
    range_pct = range_24h / price
    price_pos = max(0.0, min(1.0, (price - low_24h) / range_24h))
    mcap = float(entry.get("MCap") or 0)
    vol_mcap = float(entry.get("VolMCapRatio") or 0)
    c24 = float(entry.get("Change24h") or 0)
    c7d = float(entry.get("Change7d") or 0)
    alpha_24h = round(c24 - (btc_24h or 0), 2)
    alpha_7d = round(c7d - (btc_7d or 0), 2)
    extreme_turnover = vol_mcap >= _EARLY_MOVER_TURNOVER_CHURN_BLOCK_PCT
    turnover_without_alpha = bool(
        extreme_turnover
        and alpha_24h <= 0
        and c24 < 2.0
    )

    risk_pct = max(0.035, min(0.12, range_pct * 0.42))
    if mcap and mcap < 20_000_000:
        risk_pct = max(risk_pct, 0.055)
    if vol_mcap > 80:
        risk_pct = max(risk_pct, 0.065)

    liquidity = _early_mover_static_liquidity(entry)
    warnings = []
    notes = []
    trigger_conditions = [
        "5m Execution-Trigger abwarten",
        "kein Market-Buy in eine lange gruene Kerze",
        "BTC darf im Moment des Entries nicht hart abverkaufen",
    ]
    btc_block = bool(btc_24h <= -3.0 or btc_7d <= -7.0)
    btc_warn = bool((btc_24h < -1.0 or btc_7d < -4.0) and not btc_block)
    if btc_block:
        warnings.append(f"BTC Gegenwind: 24h {btc_24h:+.1f}%, 7d {btc_7d:+.1f}%")
    elif btc_warn:
        warnings.append(f"BTC ist nicht sauber risk-on: 24h {btc_24h:+.1f}%, 7d {btc_7d:+.1f}%")
    elif btc_24h >= 0.5 and alpha_24h >= 1:
        notes.append(f"BTC Tailwind + Coin-Alpha {alpha_24h:+.1f}%")

    if phase == 1:
        if price_pos <= 0.72 and 0 <= c24 <= 8:
            setup_entry = price
            trade_action = "LONG_TRIGGER"
            entry_status = "CONDITIONAL_LONG"
            entry_quality = "GOOD"
            notes.append("Phase 1: Volumen kommt rein, Preis noch nicht ueberhitzt")
        else:
            setup_entry = min(price, max(low_24h + range_24h * 0.55, price * (1 - min(0.07, risk_pct))))
            trade_action = "WAIT_FOR_RETEST"
            entry_status = "WAIT_FOR_RETEST"
            entry_quality = "EXTENDED"
            warnings.append("Preis ist nah am 24h-Hoch - Retest statt Chase")
    elif phase == 2:
        if price_pos <= 0.82 and c24 <= 10 and score >= 60:
            setup_entry = price
            trade_action = "LONG_TRIGGER"
            entry_status = "CONDITIONAL_LONG"
            entry_quality = "EXTENDED"
            notes.append("Phase 2: Breakout laeuft, nur mit frischem Intraday-Trigger")
        else:
            setup_entry = min(price, max(low_24h + range_24h * 0.62, price * (1 - min(0.08, risk_pct * 0.9))))
            trade_action = "WAIT_FOR_RETEST"
            entry_status = "WAIT_FOR_RETEST"
            entry_quality = "LATE"
            warnings.append("Breakout ist erweitert - besser Pullback/Flag handeln")
    else:
        pullback_pct = min(0.16, max(0.08, risk_pct * 1.35))
        setup_entry = min(price, max(low_24h + range_24h * 0.50, price * (1 - pullback_pct)))
        trade_action = "NO_LONG_CHASE"
        entry_status = "WAIT_FOR_DEEP_RETEST"
        entry_quality = "CHASE"
        warnings.append("Phase 3 ueberhitzt - kein Long ohne tiefen Retest")

    if btc_block and trade_action == "LONG_TRIGGER":
        trade_action = "WAIT_FOR_BTC_CONFIRMATION"
        entry_status = "WAIT_FOR_BTC_CONFIRMATION"
        entry_quality = "EXTENDED"

    if turnover_without_alpha and trade_action in ("LONG_TRIGGER", "WAIT_FOR_RETEST"):
        trade_action = "WAIT_FOR_RETEST"
        entry_status = "WAIT_FOR_RETEST"
        entry_quality = "CHURN"
        warnings.append("Vol/MCap extrem hoch ohne BTC-Alpha - erst 5m Retest/Reclaim abwarten")

    if liquidity.get("hard_block"):
        trade_action = "WAIT_FOR_LIQUIDITY"
        entry_status = "WAIT_FOR_LIQUIDITY"
        entry_quality = "THIN"
        warnings.extend(liquidity.get("reasons") or [])

    stop_buffer = max(range_24h * 0.035, setup_entry * 0.006)
    min_stop_breathing_room = max(setup_entry * 0.012, range_24h * 0.08)
    if extreme_turnover:
        min_stop_breathing_room = max(min_stop_breathing_room, setup_entry * 0.025)
    elif vol_mcap >= _EARLY_MOVER_TURNOVER_WARN_PCT:
        min_stop_breathing_room = max(min_stop_breathing_room, setup_entry * 0.018)
    max_structure_risk = setup_entry * (0.16 if mcap and mcap < 20_000_000 else 0.14)
    structure_supports = [
        (low_24h + range_24h * 0.618, "fib_61_8_retest"),
        (low_24h + range_24h * 0.500, "range_mid_retest"),
        (low_24h + range_24h * 0.382, "fib_38_2_retest"),
        (low_24h, "24h_swing_low"),
    ]
    stop = None
    stop_source = "volatility_fallback"
    for level, label in sorted(structure_supports, key=lambda item: setup_entry - item[0]):
        if not (0 < level < setup_entry):
            continue
        proposed_stop = max(0.00000001, level - stop_buffer)
        risk_candidate = setup_entry - proposed_stop
        if risk_candidate >= min_stop_breathing_room and risk_candidate <= max_structure_risk:
            stop = proposed_stop
            stop_source = f"{label}_invalidation"
            break

    if stop is None:
        risk_distance = max(setup_entry * risk_pct, range_24h * 0.25)
        stop = max(0.00000001, setup_entry - min(risk_distance, max_structure_risk))
        warnings.append("Kein sauberes Struktur-Stop-Level - nur mit bestaetigtem Retest handeln")

    if stop <= 0 or stop >= setup_entry:
        stop = setup_entry * (1 - risk_pct)
        stop_source = "volatility_fallback"

    risk = max(setup_entry - stop, setup_entry * 0.01)

    target_candidates = []
    if high_24h > setup_entry:
        target_candidates.append((high_24h, "24h_high_liquidity"))
    target_candidates.extend([
        (low_24h + range_24h * 1.272, "127_2_range_extension"),
        (low_24h + range_24h * 1.618, "161_8_range_extension"),
        (low_24h + range_24h * 2.000, "200_0_range_extension"),
        (low_24h + range_24h * 2.618, "261_8_range_extension"),
        (high_24h + range_24h * 0.272, "breakout_measured_move_127"),
        (high_24h + range_24h * 0.618, "breakout_measured_move_161"),
        (high_24h + range_24h * 1.000, "breakout_measured_move_200"),
        (high_24h + range_24h * 1.618, "breakout_measured_move_261"),
    ])

    unique_targets = sorted({round(p, 10): l for p, l in target_candidates if p > setup_entry}.items())

    def _pick_structural_crypto_long_target(min_rr: float, min_pct: float, min_above: float) -> tuple[float, str, bool]:
        min_price = max(setup_entry + risk * min_rr, setup_entry * (1 + min_pct))
        candidates = [(level, label) for level, label in unique_targets if level > min_above]
        for level, label in candidates:
            if level >= min_price:
                return level, label, True
        if candidates:
            level, label = candidates[-1]
            return level, f"{label}_too_close", False
        return setup_entry, "no_structural_target", False

    min_tp1_pct = 0.055 if phase == 1 else 0.045
    min_tp2_pct = 0.095 if phase == 1 else 0.075
    if mcap and mcap < 20_000_000:
        min_tp1_pct = max(min_tp1_pct, 0.065)
        min_tp2_pct = max(min_tp2_pct, 0.11)
    if vol_mcap >= _EARLY_MOVER_TURNOVER_WARN_PCT:
        min_tp1_pct = max(min_tp1_pct, 0.06)
        min_tp2_pct = max(min_tp2_pct, 0.10)

    tp1, tp1_source, tp1_structural_ok = _pick_structural_crypto_long_target(1.35, min_tp1_pct, setup_entry)
    tp2_floor_rr = 2.6 if phase == 1 else 2.25
    tp2, tp2_source, tp2_structural_ok = _pick_structural_crypto_long_target(tp2_floor_rr, min_tp2_pct, tp1 + risk * 0.25)
    min_tp2_gap = max(setup_entry * 0.018, risk * 0.45)
    if tp2 <= tp1 + min_tp2_gap:
        projected_extension = max(
            tp1 + max(risk * 1.2, setup_entry * 0.035),
            setup_entry * (1 + min_tp2_pct),
            high_24h + range_24h * 0.618,
        )
        tp2 = projected_extension
        tp2_source = f"{tp2_source}_projected_extension_no_clean_structure"
        tp2_structural_ok = False

    target_quality = "STRUCTURAL" if tp1_structural_ok and tp2_structural_ok else "WEAK_STRUCTURAL_TARGETS"
    if target_quality != "STRUCTURAL":
        warnings.append("Strukturziele zu eng/fehlend - kein sauberer Early-Mover-Tradeplan")
        if trade_action == "LONG_TRIGGER":
            trade_action = "WAIT_FOR_RETEST"
            entry_status = "WAIT_FOR_RETEST"
            entry_quality = "TARGETS_TIGHT"

    rr_tp1 = round((tp1 - setup_entry) / risk, 2) if risk > 0 else 0
    rr_tp2 = round((tp2 - setup_entry) / risk, 2) if risk > 0 else 0
    live_entry = max(price, setup_entry)
    live_risk = max(live_entry - stop, risk)
    live_reward = ((tp1 - live_entry) + (tp2 - live_entry)) / 2
    live_rr = round(max(0.0, live_reward) / live_risk, 2) if live_risk > 0 else 0
    distance_to_entry_r = round((price - setup_entry) / risk, 2) if risk > 0 else 0
    late_to_tp1 = price >= tp1

    if late_to_tp1:
        trade_action = "WAIT_FOR_CONTINUATION"
        entry_status = "TP1_ALREADY_REACHED"
        entry_quality = "CHASE"
        warnings.append("TP1 waere live bereits erreicht - nicht hinterherlaufen")
    elif live_rr < 1.2 and trade_action == "LONG_TRIGGER":
        trade_action = "WAIT_FOR_RETEST"
        entry_status = "WAIT_FOR_RETEST"
        entry_quality = "LATE"
        warnings.append(f"Live R:R nur {live_rr:.2f} - besserer Entry noetig")

    risk_flags = []
    if phase == 3:
        risk_flags.append("overheated_phase3")
    if btc_block or (btc_warn and alpha_24h < 1.0):
        risk_flags.append("btc_headwind")
    elif btc_warn:
        risk_flags.append("btc_caution")
    if distance_to_entry_r >= 0.75:
        risk_flags.append("chased_from_entry")
    if extreme_turnover:
        risk_flags.append("extreme_volume_turnover")
    elif vol_mcap >= _EARLY_MOVER_TURNOVER_WARN_PCT:
        risk_flags.append("high_volume_turnover")
    if turnover_without_alpha:
        risk_flags.extend(["turnover_without_alpha", "extreme_turnover_churn"])
    if vol_mcap > 100:
        risk_flags.append("very_high_volume_turnover")
    if target_quality != "STRUCTURAL":
        risk_flags.append("weak_structural_targets")
    if entry.get("data_warning"):
        risk_flags.append("data_warning")
    risk_flags.extend(liquidity.get("flags") or [])

    action_label = {
        "LONG_TRIGGER": "Long nur mit Trigger",
        "WAIT_FOR_RETEST": "Auf Retest warten",
        "WAIT_FOR_BTC_CONFIRMATION": "BTC-Bestaetigung abwarten",
        "WAIT_FOR_LIQUIDITY": "Liquiditaet abwarten",
        "WAIT_FOR_CONTINUATION": "Nur neue Continuation-Flag",
        "NO_LONG_CHASE": "Kein Long-Chase",
        "NO_TRADE": "No Trade",
    }.get(trade_action, trade_action)

    return {
        "direction": "LONG",
        "entry": _round_crypto_price(setup_entry),
        "stop": _round_crypto_price(stop),
        "stop_loss": _round_crypto_price(stop),
        "tp1": _round_crypto_price(tp1),
        "tp2": _round_crypto_price(tp2),
        "risk": _round_crypto_price(risk),
        "rr": round((rr_tp1 + rr_tp2) / 2, 2),
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "live_rr": live_rr,
        "model": "structure-first conditional long: stop=invalidations, TP=targets, RR=filter",
        "level_model": "crypto_structure_first_v2",
        "stop_source": stop_source,
        "tp1_source": tp1_source,
        "tp2_source": tp2_source,
        "target_quality": target_quality,
        "target_min_pct_required": {
            "tp1": round(min_tp1_pct * 100, 2),
            "tp2": round(min_tp2_pct * 100, 2),
        },
        "trade_action": trade_action,
        "action_label": action_label,
        "entry_status": entry_status,
        "entry_quality": entry_quality,
        "distance_to_entry_r": distance_to_entry_r,
        "late_to_tp1": late_to_tp1,
        "warnings": list(dict.fromkeys(warnings))[:6],
        "notes": list(dict.fromkeys(notes))[:6],
        "trigger_conditions": trigger_conditions,
        "risk_flags": risk_flags,
        "execution_liquidity": liquidity,
        "btc_context": {
            "btc_24h": round(btc_24h or 0, 2),
            "btc_7d": round(btc_7d or 0, 2),
            "alpha_24h": alpha_24h,
            "alpha_7d": alpha_7d,
            "tailwind": not btc_block and not btc_warn,
        },
    }


def _fetch_coingecko_markets(pages=4):
    """Fetch CoinGecko markets API with 2 min file cache to reduce rate limiting."""
    _CG_CACHE = "/tmp/coingecko_markets_cache.json"

    def _load_cached(max_age_seconds: Optional[int] = None):
        try:
            if not os.path.exists(_CG_CACHE):
                return []
            age = time.time() - os.path.getmtime(_CG_CACHE)
            if max_age_seconds is not None and age > max_age_seconds:
                return []
            with open(_CG_CACHE, "r") as _f:
                _cached = json.load(_f)
            _coins = _cached.get("coins", [])
            return _coins if isinstance(_coins, list) else []
        except Exception:
            return []

    try:
        _coins = _load_cached(max_age_seconds=120)
        if _coins:
            _CG_MARKETS_STATUS.update({"source": "fresh_cache", "partial": False, "warning": None})
            return _coins
    except Exception:
        pass

    all_coins = []
    incomplete_reason = None
    for page_num in range(1, pages + 1):
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": page_num,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d,14d,30d"
        }
        resp = None
        for _retry in range(4):
            try:
                resp = req.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    if _retry < 3:
                        time.sleep(15 * (_retry + 1))
                        continue
                    break
                elif resp.status_code == 200:
                    break
                else:
                    if _retry < 3:
                        time.sleep(5)
                        continue
                    break
            except Exception:
                if _retry < 3:
                    time.sleep(5)
                    continue
                break
        if resp and resp.status_code == 429:
            incomplete_reason = "CoinGecko rate limit"
            break
        if not resp or resp.status_code != 200:
            incomplete_reason = f"CoinGecko HTTP {getattr(resp, 'status_code', 'error')}"
            break
        try:
            page_coins = resp.json()
        except Exception:
            page_coins = []
        if not isinstance(page_coins, list) or not page_coins:
            incomplete_reason = "CoinGecko returned empty page"
            break
        all_coins.extend(page_coins)
        if page_num < pages:
            time.sleep(3.0)

    expected_min = pages * 250
    is_complete = not incomplete_reason and len(all_coins) >= expected_min

    # Save only complete scans. A partial page must not poison the cache.
    try:
        if is_complete and all_coins:
            with open(_CG_CACHE, "w") as _f:
                json.dump({"coins": all_coins, "cached_at": datetime.now().isoformat(), "pages": pages}, _f)
    except Exception:
        pass

    if not is_complete:
        stale = _load_cached(max_age_seconds=3600)
        if stale:
            _CG_MARKETS_STATUS.update({
                "source": "stale_cache_after_error",
                "partial": False,
                "warning": f"{incomplete_reason or 'CoinGecko incomplete'} — nutze letzten vollständigen Cache",
            })
            return stale
        _CG_MARKETS_STATUS.update({
            "source": "partial_live",
            "partial": True,
            "warning": f"{incomplete_reason or 'CoinGecko incomplete'} — Live-Daten sind unvollständig",
        })
        return all_coins

    _CG_MARKETS_STATUS.update({"source": "fresh_live", "partial": False, "warning": None})
    return all_coins


def fetch_mexc_funding_oi():
    """MEXC Perpetual Futures: Funding Rate + Open Interest for all contracts."""
    try:
        time.sleep(0.1)
        resp = req.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            return {}

        result = {}
        for t in data.get("data", []):
            symbol = t.get("symbol", "")
            if not symbol.endswith("_USDT"):
                continue
            base = symbol.replace("_USDT", "")
            hold_vol = float(t.get("holdVol") or 0)
            volume24 = float(t.get("volume24") or 0)
            fr = float(t.get("fundingRate") or 0)
            last_price = float(t.get("lastPrice") or t.get("last") or 0)
            oi_usdt = hold_vol * last_price if last_price > 0 else hold_vol
            vol_usdt = volume24 * last_price if last_price > 0 else volume24
            oi_ratio = (oi_usdt / vol_usdt) if vol_usdt > 0 else 0
            result[base] = {
                "contract_symbol": symbol,
                "chart_exchange": "mexc",
                "funding_rate": fr,
                "hold_vol": hold_vol,
                "volume24": vol_usdt,
                "oi_usdt": oi_usdt,
                "oi_ratio": round(oi_ratio, 2),
            }
        return result
    except Exception:
        return {}


def fetch_bitget_funding_oi():
    """Bitget Perpetual Futures: Funding Rate + Open Interest for all USDT contracts."""
    try:
        time.sleep(0.1)
        resp = req.get("https://api.bitget.com/api/v2/mix/market/tickers",
                          params={"productType": "USDT-FUTURES"}, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if data.get("code") != "00000" or not data.get("data"):
            return {}

        result = {}
        for t in data.get("data", []):
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            base = symbol.replace("USDT", "")
            fr = float(t.get("fundingRate") or 0)
            hold_amount = float(t.get("holdingAmount") or 0)
            vol_usdt = float(t.get("usdtVolume") or 0)
            last_price = float(t.get("lastPr") or 0)
            change_24h = float(t.get("change24h") or 0)

            oi_usdt = hold_amount * last_price if last_price > 0 else 0
            oi_ratio = (oi_usdt / vol_usdt) if vol_usdt > 0 else 0

            result[base] = {
                "contract_symbol": symbol,
                "chart_exchange": "bitget",
                "funding_rate": fr,
                "hold_amount": hold_amount,
                "oi_usdt": oi_usdt,
                "volume24_usdt": vol_usdt,
                "oi_ratio": round(oi_ratio, 2),
                "change24h": change_24h,
                "last_price": last_price,
            }
        return result
    except Exception:
        return {}


def fetch_binance_funding_oi():
    """Binance USDT-M Futures: bulk 24h volume + funding for execution routing."""
    try:
        time.sleep(0.1)
        ticker_resp = req.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=15)
        if ticker_resp.status_code != 200:
            return {}
        tickers = ticker_resp.json()
        if not isinstance(tickers, list):
            return {}

        funding_by_symbol: Dict[str, float] = {}
        try:
            premium_resp = req.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=15)
            if premium_resp.status_code == 200:
                premiums = premium_resp.json()
                if isinstance(premiums, list):
                    for item in premiums:
                        symbol = str(item.get("symbol") or "")
                        if symbol.endswith("USDT"):
                            funding_by_symbol[symbol] = float(item.get("lastFundingRate") or 0)
        except Exception:
            funding_by_symbol = {}

        result = {}
        for t in tickers:
            symbol = str(t.get("symbol") or "")
            if not symbol.endswith("USDT"):
                continue
            # Avoid coin-margined/quarterly variants and obvious stable/depeg pairs.
            base = symbol[:-4]
            vol_usdt = float(t.get("quoteVolume") or 0)
            last_price = float(t.get("lastPrice") or 0)
            change_24h = float(t.get("priceChangePercent") or 0)
            if vol_usdt <= 0 or last_price <= 0:
                continue
            result[base] = {
                "contract_symbol": symbol,
                "chart_exchange": "binance",
                "funding_rate": funding_by_symbol.get(symbol, 0),
                "oi_usdt": 0,
                "volume24_usdt": vol_usdt,
                "oi_ratio": 0,
                "change24h": change_24h,
                "last_price": last_price,
            }
        return result
    except Exception:
        return {}


def fetch_multi_exchange_perps():
    """Multi-Exchange Perpetual Data: Binance + Bitget + MEXC combined."""
    mexc = fetch_mexc_funding_oi()
    bitget = fetch_bitget_funding_oi()
    binance = fetch_binance_funding_oi()

    all_symbols = set(mexc.keys()) | set(bitget.keys()) | set(binance.keys())
    result = {}

    for sym in all_symbols:
        m = mexc.get(sym, {})
        b = bitget.get(sym, {})
        bn = binance.get(sym, {})

        exchanges = []
        if bn:
            exchanges.append("Binance")
        if m:
            exchanges.append("MEXC")
        if b:
            exchanges.append("Bitget")

        mexc_vol = m.get("volume24", 0) if m else 0
        bitget_vol = b.get("volume24_usdt", 0) if b else 0
        binance_vol = bn.get("volume24_usdt", 0) if bn else 0
        candidates = [
            ("Binance", bn, binance_vol, f"{sym}USDT", "binance"),
            ("Bitget", b, bitget_vol, f"{sym}USDT", "bitget"),
            ("MEXC", m, mexc_vol, f"{sym}_USDT", "mexc"),
        ]
        candidates = [item for item in candidates if item[1]]
        if not candidates:
            continue
        best, best_data, best_vol, fallback_contract, fallback_exchange = max(candidates, key=lambda item: item[2] or 0)
        best_contract = best_data.get("contract_symbol") or fallback_contract
        best_chart_exchange = best_data.get("chart_exchange") or fallback_exchange
        best_fr = best_data.get("funding_rate", 0)
        # Binance is often best for execution volume, but bulk OI is not available
        # cheaply. Keep the strongest OI snapshot from Bitget/MEXC for positioning.
        best_oi_usdt = best_data.get("oi_usdt", 0) or max(m.get("oi_usdt", 0) if m else 0, b.get("oi_usdt", 0) if b else 0)
        best_oi_ratio = best_data.get("oi_ratio", 0)
        if not best_oi_ratio and best_oi_usdt and best_vol:
            best_oi_ratio = round(best_oi_usdt / best_vol, 2)

        result[sym] = {
            "exchanges": exchanges,
            "best_exchange": best,
            "best_contract_symbol": best_contract,
            "best_chart_exchange": best_chart_exchange,
            "funding_rate": best_fr,
            "oi_ratio": best_oi_ratio,
            "oi_usdt": best_oi_usdt,
            "volume24_usdt": max(mexc_vol, bitget_vol, binance_vol),
            "binance": bn,
            "mexc": m,
            "bitget": b,
        }

    return _enrich_perp_oi_history(result)


def _classify_phase(change_24h, change_7d, vol_mcap_pct, btc_24h=0):
    """Klassifiziert einen Coin in Phase 1 (Accumulation), 2 (Breakout) oder 3 (Überhitzt).

    MOVE-INTENSITY-ANSATZ (Volume/Price Divergence):
    intensity = abs(c24) / normalized_volume
    Misst wie stark der Preis sich RELATIV zum Volume bewegt hat.

    - Niedriges intensity (<3): Volume hoch aber Preis kaum bewegt → Smart Money akkumuliert leise
    - Mittleres intensity (3-6): Preis und Volume im Gleichgewicht → bestätigter Breakout
    - Hohes intensity (>6): Preis rast dem Volume davon → Überhitzt, Retail-FOMO

    Zusätzlich: BTC-Alpha und absolute Schwellwerte als Sicherheitsnetz.
    """
    c24 = change_24h or 0
    c7d = change_7d or 0
    vm = vol_mcap_pct or 0
    alpha = c24 - (btc_24h or 0)  # BTC-relative Alpha

    # Move-Intensity: Preis-Bewegung normalisiert auf Volume-Anomalie
    # vm/30 normalisiert (Scanner filtert auf vm>30, also norm_vol >= 1)
    norm_vol = max(1, vm / 30)
    intensity = abs(c24) / norm_vol

    # ═══ Phase 3: Überhitzt — NICHT kaufen, Korrektur kommt ═══
    # Absolut: ≥12% in 24h = klar gepumpt, egal was Volume sagt
    if c24 >= 12:
        return 3, "Überhitzt", "#ef4444"
    # Intensity-basiert: Preis rast dem Volume davon (>6) + positiver Move
    if intensity > 6 and c24 > 5:
        return 3, "Überhitzt", "#ef4444"
    # Alpha-basiert: ≥8% besser als BTC + positiver abs. Move
    if alpha >= 8 and c24 > 3:
        return 3, "Überhitzt", "#ef4444"
    # 7d extrem: ≥30% Wochenperformance + heute positiv = Pump zu lang
    if c7d >= 30 and c24 > 0:
        return 3, "Überhitzt", "#ef4444"

    # ═══ Phase 2: Breakout — bestätigter Move ═══
    # Intensity 3-6: Preis und Volume bewegen sich zusammen = gesunder Move
    if intensity >= 3 and c24 > 4:
        return 2, "Breakout", "#f59e0b"
    # Absolut: ≥8% in 24h = starker Move
    if c24 >= 8:
        return 2, "Breakout", "#f59e0b"
    # Alpha ≥5% + positiver Move = klare Überperformance
    if alpha >= 5 and c24 > 3:
        return 2, "Breakout", "#f59e0b"

    # ═══ Phase 1: Accumulation — Smart Money kauft leise ═══
    # Volume auffällig aber Preis kaum bewegt → bester Einstieg
    return 1, "Accumulation", "#10b981"


def _calculate_risk(change_24h, change_7d, vol_mcap_pct, funding_rate, phase, perp_volume_24h=None, has_perp=True):
    """Berechnet Risiko-Level basierend auf Marktdaten."""
    c24 = abs(change_24h or 0)
    c7d_raw = change_7d or 0
    vm = vol_mcap_pct or 0
    fr = abs((funding_rate or 0) * 100)
    perp_volume = _alert_float(perp_volume_24h, 0) or 0
    reasons = []
    hard_risk = False

    # Verschärfte Schwellen — Trader brauchen ehrliche Warnungen
    if has_perp is False:
        reasons.append("kein Perp/Trigger-Markt - Ausfuehrung nicht verifizierbar")
        hard_risk = True
    elif perp_volume and perp_volume < _EARLY_MOVER_MIN_PERP_VOLUME_USD:
        reasons.append(f"Perp-Volumen duenn: {_compact_usd(perp_volume)}/24h - Market-Impact-Risiko")
        hard_risk = True
    elif perp_volume and perp_volume < _EARLY_MOVER_WARN_PERP_VOLUME_USD:
        reasons.append(f"Perp-Volumen nur mittel: {_compact_usd(perp_volume)}/24h")

    if c24 > 15:
        reasons.append(f"24h Change stark: {change_24h:+.1f}% — Einstieg riskant")
    if c24 > 10:
        reasons.append(f"24h Change erhöht: {change_24h:+.1f}%")
    if vm > 60:
        reasons.append(f"Vol/MCap hoch: {vm:.0f}% — mögliche Euphorie")
    if fr > 0.05:
        reasons.append(f"Funding Rate erhöht: {funding_rate*100:+.3f}% — Longs crowded")
    if abs(c7d_raw) > 30:
        reasons.append(f"7d Change extrem: {c7d_raw:+.1f}%")

    if hard_risk:
        return "HIGH", "#ef4444", reasons
    if phase == 3:
        if not reasons:
            reasons.append("Überhitzt — Korrektur wahrscheinlich, NICHT kaufen")
        return "HIGH", "#ef4444", reasons
    if len(reasons) >= 2:
        return "HIGH", "#ef4444", reasons
    elif len(reasons) >= 1 or phase == 2:
        return "MEDIUM", "#f59e0b", reasons
    return "LOW", "#10b981", reasons


def fetch_early_movers(_prefetched_perps=None):
    """Early Movers Scanner V4.0 — Phase-Klassifikation + Unified List

    4 strategies to find next 10x coins early:
    1. Volume Spike Detector: Vol/MCap anomalously high, price not yet exploded
    2. Micro-Cap Momentum: $1M-$50M MCap, early movement
    3. Whale Accumulation: OI + FR + Preisstabilität = stille Akkumulation
    4. Narrative Tracker: Sektor-Performance, Leaders & Laggards

    Alle Scores nutzen BTC-relative Performance für Alpha-Erkennung.
    Symbol-Matching mit 1000x-Prefix für Börsen-Perps (1000PEPE etc.)

    Returns: dict with volume_spikes, micro_caps, whale_acc, narratives, stats
    """
    all_coins = _fetch_coingecko_markets(pages=_EARLY_MOVER_MARKET_PAGES)
    if not all_coins:
        return {"coins": [], "stats": {"error": "No data"}}

    perp_data = _prefetched_perps if _prefetched_perps is not None else fetch_multi_exchange_perps()

    btc_7d = 0
    btc_24h = 0
    for c in all_coins:
        if c.get("id") == "bitcoin":
            btc_7d = c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d") or 0
            btc_24h = c.get("price_change_percentage_24h") or 0
            break

    # Fetch trending coins
    trending_ids = set()
    try:
        _tr_resp = req.get("https://api.coingecko.com/api/v3/search/trending", timeout=15)
        if _tr_resp.status_code == 200:
            _tr_coins = _tr_resp.json().get("coins", [])
            for _tc in _tr_coins:
                _item = _tc.get("item", {})
                _tid = _item.get("id", "")
                if _tid:
                    trending_ids.add(_tid)
    except Exception:
        pass

    volume_spikes = []
    micro_caps = []
    whale_accumulations = []
    market_sweep = []
    excluded_assets = 0

    for coin in all_coins:
        try:
            price = coin.get("current_price") or 0
            if price <= 0:
                continue

            cid = coin.get("id", "")
            symbol = coin.get("symbol", "").upper()
            name = coin.get("name", "")
            mcap = coin.get("market_cap") or 0
            vol_24h = coin.get("total_volume") or 0
            change_1h = coin.get("price_change_percentage_1h_in_currency") or 0
            change_24h = coin.get("price_change_percentage_24h") or 0
            change_7d = coin.get("price_change_percentage_7d_in_currency") or coin.get("price_change_percentage_7d") or 0
            change_14d = coin.get("price_change_percentage_14d_in_currency") or 0
            change_30d = coin.get("price_change_percentage_30d_in_currency") or 0
            high_24h = coin.get("high_24h") or price
            low_24h = coin.get("low_24h") or price

            # Perp-Match: Direkt oder mit 1000-Prefix (Börsen listen z.B. 1000PEPE, 1000SHIB)
            perp_match_symbol = symbol
            perp_info = perp_data.get(symbol, {})
            if not perp_info:
                perp_match_symbol = f"1000{symbol}"
                perp_info = perp_data.get(perp_match_symbol, {})
            if not perp_info:
                perp_match_symbol = f"10000{symbol}"
                perp_info = perp_data.get(perp_match_symbol, {})
            if not perp_info:
                perp_match_symbol = symbol
            has_perp = bool(perp_info)
            funding_rate = perp_info.get("funding_rate", 0)
            oi_ratio = perp_info.get("oi_ratio", 0)
            best_exchange = perp_info.get("best_exchange", "")
            exchanges = perp_info.get("exchanges", [])
            rank = coin.get("market_cap_rank")

            # Skip stablecoins, wrapped assets and liquid-staking derivatives.
            if _is_excluded_crypto_asset(symbol, cid, name):
                excluded_assets += 1
                continue

            vol_mcap_ratio = (vol_24h / mcap * 100) if mcap > 0 else 0
            initial_phase, _, _ = _classify_phase(change_24h, change_7d, vol_mcap_ratio, btc_24h)
            narrative = CRYPTO_NARRATIVES.get(cid, "")
            is_trending = cid in trending_ids
            # BTC-relative Performance (zeigt Alpha vs. Markt)
            btc_relative_7d = round(change_7d - btc_7d, 2) if btc_7d else round(change_7d, 2)

            base_entry = {
                "Symbol": symbol, "Name": name, "ID": cid,
                "Rank": rank,
                "Price": price, "MCap": mcap, "Vol24h": vol_24h,
                "Change1h": round(change_1h, 2), "Change24h": round(change_24h, 2),
                "Change7d": round(change_7d, 2), "Change14d": round(change_14d, 2),
                "Change30d": round(change_30d, 2),
                "VolMCapRatio": round(vol_mcap_ratio, 2),
                "HasPerp": has_perp, "FundingRate": funding_rate,
                "OI_Ratio": oi_ratio,
                "PerpVolume24h": perp_info.get("volume24_usdt", 0) if perp_info else 0,
                "PerpOI": perp_info.get("oi_usdt", 0) if perp_info else 0,
                "BestExchange": best_exchange,
                "PerpMatchSymbol": perp_match_symbol if has_perp else None,
                "PerpChartSymbol": perp_info.get("best_contract_symbol") if perp_info else None,
                "PerpChartExchange": perp_info.get("best_chart_exchange") if perp_info else None,
                "Exchanges": exchanges,
                "Narrative": narrative,
                "High24h": high_24h, "Low24h": low_24h,
                "IsTrending": is_trending,
                "BtcRelative7d": btc_relative_7d,
                "Btc24h": round(btc_24h, 2),
                "Btc7d": round(btc_7d, 2),
                "BtcRelative24h": round(change_24h - btc_24h, 2),
                "current_price": price,
                "direction": "LONG",
                "dollar_volume": vol_24h,
                "relative_volume": round(max(0, vol_mcap_ratio / 30), 2),
                "close_pos": round((price - low_24h) / (high_24h - low_24h), 2) if high_24h > low_24h else 0.5,
                "OI_ChangePct": perp_info.get("oi_change_pct") if perp_info else None,
                "OI_HistoryAgeSeconds": perp_info.get("oi_history_age_seconds") if perp_info else None,
            }

            # Market Sweep: every chartable Top-1000 coin reaches the 5m
            # execution check. Specialised scanners can still override the
            # score/source, but they are no longer the admission ticket.
            chart_exchange = base_entry.get("PerpChartExchange") or base_entry.get("BestExchange")
            chart_contract = base_entry.get("PerpChartSymbol") or base_entry.get("PerpMatchSymbol")
            if has_perp and chart_exchange and chart_contract:
                alpha_24h = change_24h - btc_24h
                sweep_score = 25
                sweep_score += 12
                if vol_24h >= 25_000_000:
                    sweep_score += 12
                elif vol_24h >= 10_000_000:
                    sweep_score += 10
                elif vol_24h >= 2_000_000:
                    sweep_score += 7
                elif vol_24h >= 500_000:
                    sweep_score += 4
                if 1 <= vol_mcap_ratio <= 60:
                    sweep_score += 8
                elif 60 < vol_mcap_ratio <= 120:
                    sweep_score += 3
                elif vol_mcap_ratio > 120:
                    sweep_score -= 10
                if -2 <= change_24h <= 8:
                    sweep_score += 8
                elif 8 < change_24h <= 14:
                    sweep_score += 3
                elif change_24h > 20:
                    sweep_score -= 12
                if alpha_24h >= 2:
                    sweep_score += 7
                elif alpha_24h < -3:
                    sweep_score -= 7
                if 0 <= change_7d <= 25:
                    sweep_score += 5
                elif change_7d > 80:
                    sweep_score -= 8
                if is_trending:
                    sweep_score += 3
                if initial_phase == 3:
                    sweep_score -= 12

                entry = dict(base_entry)
                entry["MarketSweepScore"] = max(5, min(82, int(sweep_score)))
                entry["Signal"] = "Market Sweep - 5m execution scan"
                market_sweep.append(entry)

            # 1. VOLUME SPIKE DETECTOR (MCap >$10M, Vol >$500k — kleinere sind manipulierbar)
            if mcap > 10_000_000 and vol_24h > 500_000:
                if vol_mcap_ratio > 30 and change_7d < 60:
                    if change_24h < -8:
                        pass
                    else:
                        range_24h = high_24h - low_24h
                        if range_24h > 0:
                            price_position = (price - low_24h) / range_24h
                        else:
                            price_position = 0.5

                        # Volume Score: logarithmisch — extreme Vol/MCap bringt nicht mehr endlos Punkte
                        # 30%→10, 50%→15, 100%→22, 200%→25 (max 25)
                        vol_score = min(25, int(10 * math.log2(max(1, vol_mcap_ratio / 15))))

                        momentum_score = 0
                        if 10 < change_7d < 50:
                            momentum_score = 18
                        elif change_7d > 50:
                            momentum_score = 8   # Zu spät — überkauft
                        elif 0 < change_7d <= 10:
                            momentum_score = 12
                        elif change_7d <= 0:
                            # 7d negativ aber 24h/1h pumpt = Reversal-Signal, moderater Score
                            if change_24h > 5 and change_1h > 1:
                                momentum_score = 10
                            elif change_24h > 3:
                                momentum_score = 5
                            else:
                                momentum_score = 0

                        # Freshness: Leicht positive 24h = gut. Aber STARKE 24h = zu spät!
                        freshness_score = 0
                        if 0 < change_24h <= 8 and change_1h > 0:
                            freshness_score = 12  # Ideal: leicht positiv, gerade erst los
                        elif 0 < change_24h <= 8:
                            freshness_score = 7
                        elif change_24h > 8:
                            freshness_score = 3   # Schon stark gepumpt — weniger frisch

                        position_score = 0
                        if price_position >= 0.7:
                            position_score = 8
                        elif price_position >= 0.5:
                            position_score = 4

                        perp_score = 7 if has_perp else 0
                        if len(exchanges) >= 2:
                            perp_score += 3

                        recency_score = 0
                        if change_1h > 5 and vol_mcap_ratio > 40:
                            recency_score = 12
                        elif change_1h > 2 and vol_mcap_ratio > 30:
                            recency_score = 8
                        elif change_1h > 0 and change_24h > 3:
                            recency_score = 4

                        trending_score = 7 if is_trending else 0

                        # BTC-relative Alpha
                        btc_alpha_score = 0
                        if btc_relative_7d > 20:
                            btc_alpha_score = 8
                        elif btc_relative_7d > 10:
                            btc_alpha_score = 5
                        elif btc_relative_7d > 5:
                            btc_alpha_score = 2

                        # ── PENALTY-SYSTEM ──
                        trend_penalty = 0

                        # 1) Downtrend-Penalty
                        if change_30d < -30:
                            trend_penalty = 30
                        elif change_30d < -15:
                            trend_penalty = 20
                        elif change_30d < -5:
                            trend_penalty = 12
                        if change_14d < -15:
                            trend_penalty = max(trend_penalty, 20)
                        elif change_14d < -5:
                            trend_penalty = max(trend_penalty, 10)

                        # 2) PUMP-Penalty: Schon stark gepumpt = Einstieg zu spät
                        # Das ist der KERN-FIX: Ein Coin der +20% gemacht hat, ist kein "Early" Mover mehr
                        if change_24h >= 15:
                            trend_penalty += 20  # Stark überhitzt (>= statt > — exakt 15% ist auch Pump)
                        elif change_24h >= 10:
                            trend_penalty += 10  # Schon gut gelaufen

                        # 3) Low-Price Orderbuch-Penalty
                        if price < 1.0 and mcap < 100_000_000:
                            trend_penalty += 8

                        total_score = int(vol_score + momentum_score + freshness_score + position_score + perp_score + recency_score + trending_score + btc_alpha_score - trend_penalty)
                        # Theorie-Max: 25+18+12+8+10+12+7+8 = 100 — Penalties ziehen stark ab

                        # V3.2: Phase 3 (Überhitzt) NICHT empfehlen + Threshold von 30 auf 40
                        # Phase 3 sagt "NICHT kaufen" aber Score war hoch genug → widersprüchlich
                        if total_score >= 40 and initial_phase != 3:
                            entry = dict(base_entry)
                            entry["EarlyScore"] = min(100, total_score)
                            entry["PricePosition"] = round(price_position, 2)
                            entry["RecencyScore"] = recency_score
                            entry["TrendingBonus"] = trending_score
                            if price_position >= 0.7 and change_1h > 3:
                                entry["Signal"] = "Starker Kaufdruck + Live-Pump!"
                            elif price_position >= 0.7:
                                entry["Signal"] = "Akkumulation (Preis nahe Hoch)"
                            elif change_24h > 5:
                                entry["Signal"] = "Volume + positive 24h"
                            else:
                                entry["Signal"] = "Volume-Spike — beobachten"
                            volume_spikes.append(entry)

            # 2. MICRO-CAP MOMENTUM (MCap $5M-$50M, Vol >$1M — unter $5M MCap manipulierbar)
            _micro_vol_min = 1_500_000 if change_7d >= 20 else 1_000_000
            if 5_000_000 <= mcap <= 50_000_000 and vol_24h > _micro_vol_min:
                if change_7d > 5 and change_24h > -5:  # War -10, zu locker für "Momentum"
                    degen_score = 0

                    # 7d-Momentum (max 30)
                    if 5 < change_7d < 35:
                        degen_score += 24
                    elif change_7d < 75:
                        degen_score += 18
                    elif change_7d < 120:
                        degen_score += 10
                    else:
                        degen_score += 3

                    # Vol/MCap (max 25)
                    if vol_mcap_ratio > 50:
                        degen_score += 25
                    elif vol_mcap_ratio > 20:
                        degen_score += 15
                    else:
                        degen_score += 5

                    # MCap-Bonus: Kleiner = mehr Upside, aber KEIN Widerspruch zur Low-Price-Penalty
                    # Low-Price-Penalty gilt nur für Coins <$1 (dünnes Orderbuch-Proxy)
                    # MCap-Bonus gilt immer (Upside-Potenzial)
                    if mcap < 10_000_000:
                        degen_score += 15  # War 25 — reduziert: kleinste MCap ≠ automatisch bester Score
                    elif mcap < 20_000_000:
                        degen_score += 12
                    else:
                        degen_score += 8

                    if has_perp:
                        degen_score += 10
                    if len(exchanges) >= 2:
                        degen_score += 5

                    # Frische Bestätigung: 24h UND 1h müssen positiv sein
                    if change_1h > 2 and change_24h > 3:
                        degen_score += 8
                    elif change_1h > 0 and change_24h > 0:
                        degen_score += 3

                    if is_trending:
                        degen_score += 12  # War 15 — Trending allein ist kein starkes Signal

                    # Extreme Pumps (>200% 7d) = wahrscheinlich zu spät, Abzug
                    if change_24h >= 18:
                        degen_score -= 25
                    elif change_24h >= 12:
                        degen_score -= 15
                    if change_7d > 200:
                        degen_score -= 30
                    elif change_7d > 150:
                        degen_score -= 22
                    elif change_7d > 100:
                        degen_score -= 12
                    if initial_phase == 3:
                        degen_score -= 25

                    # BTC-Alpha Bonus
                    if btc_relative_7d > 15:
                        degen_score += 5

                    # Downtrend-Penalty: MicroCap im Abwärtstrend = Bagholding
                    # max() statt Stacking — konsistent mit Volume Spike Scanner
                    dt_penalty = 0
                    if change_30d < -30:
                        dt_penalty = 30
                    elif change_30d < -15:
                        dt_penalty = 20
                    elif change_30d < -5:
                        dt_penalty = 10
                    if change_14d < -15:
                        dt_penalty = max(dt_penalty, 15)
                    elif change_14d < -5:
                        dt_penalty = max(dt_penalty, 8)
                    degen_score -= dt_penalty
                    # Low-Price = dünnes Orderbuch (unabhängig vom MCap-Bonus)
                    if price < 1.0:
                        degen_score -= 5

                    # Nur Coins mit Score >= 20 aufnehmen (über-gestrafte rausfiltern)
                    if degen_score < 20:
                        continue
                    entry = dict(base_entry)
                    entry["DegenScore"] = min(100, degen_score)
                    entry["Signal"] = f"MicroCap +{change_7d:.0f}% (7T)"
                    if is_trending:
                        entry["Signal"] += " 🔥 TRENDING"
                    micro_caps.append(entry)

            # 3. WHALE ACCUMULATION
            perp_vol_usdt = perp_info.get("volume24_usdt", 0) if perp_info else 0
            perp_oi_usdt = perp_info.get("oi_usdt", 0) if perp_info else 0
            if has_perp and mcap > 10_000_000 and perp_vol_usdt > 100_000:
                whale_score = 0
                signals = []
                oi_change_pct = perp_info.get("oi_change_pct")

                # Snapshot OI alone is not accumulation. We need OI expansion when
                # history is available; otherwise this stays a lower-confidence read.
                if oi_change_pct is None:
                    whale_score -= 5
                    signals.append("OI history fehlt - nur Perp-Snapshot, keine bestaetigte Akkumulation")
                elif oi_change_pct >= 25:
                    whale_score += 20
                    signals.append(f"OI +{oi_change_pct:.1f}% seit letztem Scan (starker Aufbau)")
                elif oi_change_pct >= 10:
                    whale_score += 14
                    signals.append(f"OI +{oi_change_pct:.1f}% seit letztem Scan")
                elif oi_change_pct >= 3:
                    whale_score += 6
                    signals.append(f"OI +{oi_change_pct:.1f}% leicht steigend")
                elif oi_change_pct <= -10:
                    whale_score -= 12
                    signals.append(f"OI {oi_change_pct:.1f}% - Positionen werden abgebaut")

                # OI/Vol Ratio — NUR mit absolutem OI-Gate (sonst = Illiquidität)
                # Mindestens $200k OI nötig damit der Ratio überhaupt Bedeutung hat
                if perp_oi_usdt >= 200_000:
                    if oi_ratio >= 3.0:
                        whale_score += 25
                        signals.append(f"OI/Vol {oi_ratio:.1f}x (stark gehebelt)")
                    elif oi_ratio >= 1.5:
                        whale_score += 18
                        signals.append(f"OI/Vol {oi_ratio:.1f}x (Positioning hoch)")
                    elif oi_ratio >= 0.8:
                        whale_score += 10
                        signals.append(f"OI/Vol {oi_ratio:.1f}x (moderat)")
                else:
                    # Niedriges OI — Ratio ignorieren, nur OI-Existenz minimal werten
                    whale_score += 3
                    signals.append(f"OI nur ${perp_oi_usdt/1e3:.0f}k — zu dünn für Whale-Signal")

                # Bonus für absolut hohe OI (echte Whale-Größe)
                if perp_oi_usdt > 10_000_000:
                    whale_score += 12
                    signals.append(f"OI ${perp_oi_usdt/1e6:.1f}M (Whale-Größe)")
                elif perp_oi_usdt > 3_000_000:
                    whale_score += 8
                    signals.append(f"OI ${perp_oi_usdt/1e6:.1f}M (solide)")
                elif perp_oi_usdt > 1_000_000:
                    whale_score += 4

                fr_pct = funding_rate * 100
                # Whale-Akkumulation = Preis stabil/steigend TROTZ neutraler/negativer FR
                # Hohe positive FR = Longs überfüllt = Liquidations-Risiko (BEARISH)
                if fr_pct >= 0.08:
                    whale_score -= 5  # Extrem overcrowded — Warnsignal
                    signals.append(f"FR +{fr_pct:.3f}% — Longs überfüllt, Liquidations-Risiko!")
                elif fr_pct >= 0.03:
                    whale_score += 5   # Leicht bullish, aber vorsichtig
                elif 0.0 <= fr_pct < 0.03:
                    whale_score += 15  # Neutral-positiv = ideale Akkumulationszone
                    signals.append(f"FR neutral {fr_pct:+.3f}% — stille Akkumulation")
                elif fr_pct <= -0.03:
                    if change_24h > 3:
                        whale_score += 25  # Shorts zahlen + Preis steigt = Squeeze
                        signals.append(f"FR {fr_pct:.3f}% + Preis +{change_24h:.1f}% → Short-Squeeze!")
                    elif change_1h > 1:
                        whale_score += 15
                        signals.append(f"FR negativ {fr_pct:.3f}% + 1h Pump → Squeeze-Aufbau")
                    else:
                        whale_score += 5  # Negative FR allein = Shorts dominieren, abwarten
                else:
                    whale_score += 10  # Leicht negative FR = gesund

                # BUG FIX: Whale = Akkumulation, Coin darf NICHT stark fallen
                # Stabile/leicht steigende Coins = gut, fallende = schlecht
                if -5 <= change_7d <= 5:
                    whale_score += 20  # Stabil = perfekt für stille Akkumulation
                    signals.append(f"Preis stabil ({change_7d:+.1f}%) trotz OI-Aufbau")
                elif 5 < change_7d < 30:
                    whale_score += 15  # Leicht steigend = gut
                elif change_7d >= 30:
                    whale_score += 5   # Schon zu stark gepumpt
                elif -10 <= change_7d < -5:
                    whale_score += 3   # Leicht fallend, noch ok aber vorsichtig
                elif -15 <= change_7d < -10:
                    whale_score -= 5   # Deutlich fallend, skeptisch
                    signals.append(f"Preis {change_7d:+.1f}% — OI könnten Shorts sein")
                else:
                    whale_score -= 20  # Stark fallend = OI sind Shorts, keine Whales
                    signals.append(f"WARNUNG: Preis {change_7d:+.1f}% — OI sind wahrscheinlich Shorts!")

                if len(exchanges) >= 2:
                    whale_score += 10
                    signals.append(f"On {' + '.join(exchanges)}")

                # Downtrend-Penalty: Langfristiger Abwärtstrend = OI sind wahrscheinlich Shorts
                if change_30d < -30:
                    whale_score -= 25
                    signals.append(f"30d: {change_30d:+.0f}% — Langzeit-Downtrend")
                elif change_30d < -15:
                    whale_score -= 15
                    signals.append(f"30d: {change_30d:+.0f}% — Abwärtstrend")
                elif change_30d < -5:
                    whale_score -= 8
                # Low-Price = dünne Orderbücher
                if price < 1.0 and mcap < 100_000_000:
                    whale_score -= 8

                # BUG FIX: Negativen Score abfangen (kann durch Malus passieren)
                whale_score = max(0, whale_score)

                min_whale_threshold = 45 if oi_change_pct is None else 35
                if whale_score >= min_whale_threshold:
                    entry = dict(base_entry)
                    entry["WhaleScore"] = min(100, whale_score)
                    entry["Signals"] = signals
                    entry["Signal"] = "Perp Positioning + OI-Aufbau" if oi_change_pct is not None and oi_change_pct >= 3 else "Perp Positioning (Snapshot)"
                    entry["OI_ChangePct"] = oi_change_pct
                    entry["oi_snapshot_only"] = oi_change_pct is None
                    # BTC-Alpha für Whale: Coin hält sich besser als BTC = stärkeres Signal
                    if btc_relative_7d > 5:
                        entry["WhaleScore"] = min(100, entry["WhaleScore"] + 5)
                        signals.append(f"Outperformt BTC um {btc_relative_7d:+.1f}% (Alpha)")
                    whale_accumulations.append(entry)

        except Exception as _coin_err:
            print(f"[Early Movers] Error processing {coin.get('symbol','?')}: {_coin_err}")
            continue

    # Sort
    # Neu Gelistet entfernt — wird vom NLS (New Listing Scanner) abgedeckt
    volume_spikes.sort(key=lambda x: x.get("EarlyScore", 0), reverse=True)
    micro_caps.sort(key=lambda x: x.get("DegenScore", 0), reverse=True)
    whale_accumulations.sort(key=lambda x: x.get("WhaleScore", 0), reverse=True)
    market_sweep.sort(key=lambda x: x.get("MarketSweepScore", 0), reverse=True)

    # ═══════════════════════════════════════════════════════════════════
    # UNIFIED LIST: Alle 3 Strategien → eine Liste mit Phase-Klassifikation
    # ═══════════════════════════════════════════════════════════════════
    seen_symbols = {}  # Deduplizierung: Symbol → bester Eintrag

    # BTC 24h Change für Alpha-Berechnung in Phase-Klassifikation
    btc_24h = 0
    for c in all_coins:
        if c.get("id") == "bitcoin":
            btc_24h = c.get("price_change_percentage_24h") or 0
            break

    def _grade_for_score(score_value):
        return _score_grade_for_value(score_value)

    def _add_to_unified(entries, source_name, score_key):
        for entry in entries:
            sym = entry.get("Symbol", "")
            raw_score = entry.get(score_key, 0)
            vm = entry.get("VolMCapRatio", 0)
            c24 = entry.get("Change24h", 0)
            c7d = entry.get("Change7d", 0)
            fr = entry.get("FundingRate", 0)

            phase, phase_label, phase_color = _classify_phase(c24, c7d, vm, btc_24h)
            risk_level, risk_color, risk_reasons = _calculate_risk(
                c24, c7d, vm, fr, phase,
                entry.get("PerpVolume24h", 0),
                bool(entry.get("HasPerp")),
            )
            risk_reasons = list(risk_reasons or [])
            liquidity = _early_mover_static_liquidity(entry)

            # Phase-Multiplier: Phase 3 = deutliche Strafe, Phase 1 = leichter Boost
            if phase == 1:
                score = min(100, int(raw_score * 1.05))  # +5% — konservativ
            elif phase == 3:
                score = min(100, int(raw_score * 0.6))   # -40% — überhitzt = NICHT kaufen
            else:
                score = raw_score
            if liquidity.get("score_penalty"):
                score = max(0, int(score) - int(liquidity["score_penalty"]))
            if liquidity.get("reasons"):
                risk_reasons.extend(liquidity["reasons"])
                if liquidity.get("hard_block"):
                    risk_level = "HIGH"
                    risk_color = "#ef4444"
                elif risk_level == "LOW":
                    risk_level = "MEDIUM"
                    risk_color = "#f59e0b"

            # Signal-Text basierend auf Phase — ehrlich und direkt
            alpha = c24 - btc_24h
            if vm >= _EARLY_MOVER_TURNOVER_CHURN_BLOCK_PCT and alpha <= 0 and c24 < 2:
                score = max(0, int(score) - 18)
                risk_reasons.append("Vol/MCap extrem hoch ohne BTC-Alpha - Churn/Distribution moeglich")
                if risk_level == "LOW":
                    risk_level = "MEDIUM"
                    risk_color = "#f59e0b"
            elif vm >= _EARLY_MOVER_TURNOVER_WARN_PCT and alpha < 0 and c24 < 3:
                score = max(0, int(score) - 10)
                risk_reasons.append("Vol/MCap hoch, aber Coin schlaegt BTC nicht")
                if risk_level == "LOW":
                    risk_level = "MEDIUM"
                    risk_color = "#f59e0b"
            if phase == 1:
                if score >= 70:
                    signal_text = "BEOBACHTEN: Smart-Money-Akkumulation - Entry erst mit Execution-Trigger/Retest"
                elif score >= 40:
                    signal_text = "BEOBACHTEN: Volume-Anomalie - noch kein Entry-Signal"
                else:
                    signal_text = "BEOBACHTEN: leichte Aktivitaet"
            elif phase == 2:
                if c24 > 12:
                    signal_text = f"BEOBACHTEN: Breakout +{c24:.0f}% - nicht chase, Retest/Execution bestaetigen"
                elif score >= 60:
                    signal_text = "BEOBACHTEN: Momentum stark - Entry nur mit frischem Intraday-Trigger"
                else:
                    signal_text = "BEOBACHTEN: Ausbruch laeuft - Vorsicht"
            else:
                signal_text = f"ÜBERHITZT +{c24:.0f}%/24h — NICHT kaufen, Korrektur kommt"
                if c7d > 40:
                    signal_text = f"ÜBERHITZT +{c7d:.0f}%/7d — Gewinnmitnahmen wahrscheinlich"

            grade, grade_label = _grade_for_score(score)

            unified_entry = dict(entry)
            watch_flags = list(risk_reasons or [])
            watch_flags.extend(["observe_only_scanner", "no_intraday_execution_trigger"])
            watch_flags.extend(liquidity.get("flags") or [])
            if _CG_MARKETS_STATUS.get("partial"):
                watch_flags.append("partial_crypto_data")
            unified_entry.update({
                "phase": phase,
                "phase_label": phase_label,
                "phase_color": phase_color,
                "score": score,
                "raw_score": raw_score,
                "risk_level": risk_level,
                "risk_color": risk_color,
                "risk_reasons": risk_reasons,
                "grade": grade,
                "grade_label": grade_label,
                "signal_text": signal_text,
                "source": source_name,
                "signal_quality": "observe",
                "entry_status": "BEOBACHTEN",
                "trade_action": "BEOBACHTEN",
                "trade_signal": "BEOBACHTEN",
                "signal_label": "Achtung beobachten: noch kein Execution-Trigger",
                "execution_trigger_ok": False,
                "alertable_crypto": False,
                "risk_flags": watch_flags,
                "execution_liquidity": liquidity,
                "data_source": f"CoinGecko + multi-exchange perps ({_CG_MARKETS_STATUS.get('source') or 'unknown'})",
                "data_warning": _CG_MARKETS_STATUS.get("warning"),
                "scanner_note": "Early Movers liefert Beobachten- oder Jetzt-Traden-Signale. Kein Entry ohne bestaetigten adaptiven Execution-Trigger.",
            })

            # Dedup: Behalte den mit höherem Score, aber merke ALLE Quellen
            if sym not in seen_symbols:
                unified_entry["sources"] = [source_name]
                seen_symbols[sym] = unified_entry
            else:
                # Quelle hinzufügen (Multi-Signal = stärkere Konfluenz)
                if source_name not in seen_symbols[sym].get("sources", []):
                    seen_symbols[sym]["sources"].append(source_name)
                if score > seen_symbols[sym]["score"]:
                    _old_sources = seen_symbols[sym].get("sources", [])
                    unified_entry["sources"] = _old_sources
                    seen_symbols[sym] = unified_entry

    _add_to_unified(market_sweep, "Market Sweep", "MarketSweepScore")
    _add_to_unified(volume_spikes, "Volume Spike", "EarlyScore")
    _add_to_unified(micro_caps, "Micro-Cap", "DegenScore")
    _add_to_unified(whale_accumulations, "Perp Positioning", "WhaleScore")

    # Konfluenz-Bonus: Coin in 2+ Strategien = stärkeres Signal
    for sym, entry in seen_symbols.items():
        n_sources = len(entry.get("sources", []))
        if n_sources >= 3:
            entry["score"] = min(100, entry["score"] + 10)
            entry["signal_text"] += f" | KONFLUENZ: {', '.join(entry['sources'])}"
        elif n_sources == 2:
            entry["score"] = min(100, entry["score"] + 5)
            entry["signal_text"] += f" | {', '.join(entry['sources'])}"

    # Final trade-plan pass after confluence bonuses. Early Movers are long-only,
    # but the app marks JETZT_TRADEN only after a fresh exchange trigger.
    for entry in seen_symbols.values():
        final_score = int(entry.get("score") or 0)
        grade, grade_label = _grade_for_score(final_score)
        entry["grade"] = grade
        entry["grade_label"] = grade_label
        setup = _build_early_mover_long_setup(entry, entry.get("phase") or 1, final_score, btc_24h, btc_7d)
        setup_warnings = setup.get("warnings") or []
        setup_flags = list(setup.get("risk_flags") or [])
        existing_flags = [str(f) for f in (entry.get("risk_flags") or [])]
        if setup.get("trade_action") == "LONG_TRIGGER":
            setup_flags.append("requires_5m_trigger")
        else:
            setup_flags.append("no_market_entry")
        if entry.get("oi_snapshot_only"):
            setup_flags.append("oi_snapshot_only")
        entry.update({
            "direction": "LONG",
            "setup_score": final_score,
            "entry": setup.get("entry"),
            "live_entry": entry.get("Price"),
            "stop_loss": setup.get("stop_loss"),
            "stop": setup.get("stop"),
            "tp1": setup.get("tp1"),
            "tp2": setup.get("tp2"),
            "risk_reward": setup.get("rr"),
            "rr_tp1": setup.get("rr_tp1"),
            "rr_tp2": setup.get("rr_tp2"),
            "tp1_source": setup.get("tp1_source"),
            "tp2_source": setup.get("tp2_source"),
            "target_quality": setup.get("target_quality"),
            "live_rr_ratio": setup.get("live_rr"),
            "distance_to_entry_r": setup.get("distance_to_entry_r"),
            "late_to_tp1": setup.get("late_to_tp1"),
            "entry_quality": setup.get("entry_quality"),
            "entry_status": setup.get("entry_status"),
            "trade_action": setup.get("trade_action"),
            "execution_trigger_ok": False,
            "signal_quality": "observe" if setup.get("trade_action") != "NO_LONG_CHASE" else "no_chase",
            "alertable_crypto": False,
            "trade_setup": setup,
            "btc_context": setup.get("btc_context"),
            "risk_flags": list(dict.fromkeys(existing_flags + setup_flags + setup_warnings)),
            "scanner_note": "Early Movers ist long-only. JETZT_TRADEN erst mit bestaetigtem adaptiven Execution-Trigger oder sauberem Retest.",
        })
        _apply_early_mover_signal_state(entry)
        entry["signal_text"] = f"{setup.get('action_label')}: {entry.get('signal_text', '')}"

    # Sortierung: Score absteigend — Coins aus ALLEN Phasen mischen
    # (vorher: Phase 1 zuerst → bei 300+ Phase-1-Coins kamen Breakout/Überhitzt nie in Top 50)
    all_unified = sorted(
        seen_symbols.values(),
        key=lambda x: (
            0 if x.get("trade_signal") == "JETZT_TRADEN" else 1,
            -int(x.get("entry_score") or 0),
            -int(x.get("setup_score") or x.get("score") or 0),
        ),
    )

    # Proportionale Auswahl: Jede Phase bekommt mindestens ihre Top-Coins
    # damit Breakout und Überhitzt IMMER sichtbar sind
    phase_1 = [c for c in all_unified if c["phase"] == 1]
    phase_2 = [c for c in all_unified if c["phase"] == 2]
    phase_3 = [c for c in all_unified if c["phase"] == 3]

    MAX_DISPLAY = _EARLY_MOVER_MAX_DISPLAY
    # Phase 2 + 3 immer ALLE zeigen (sind selten und wichtig), Rest Phase 1
    p2_coins = phase_2  # alle Breakouts
    p3_coins = phase_3  # alle Überhitzten
    p1_slots = max(0, MAX_DISPLAY - len(p2_coins) - len(p3_coins))
    p1_coins = phase_1[:p1_slots]

    # Zusammenfügen: Phase 2+3 zuerst (wichtiger), dann Phase 1, jeweils nach Score
    unified = sorted(
        p1_coins + p2_coins + p3_coins,
        key=lambda x: (
            0 if x.get("trade_signal") == "JETZT_TRADEN" else 1,
            -int(x.get("entry_score") or 0),
            1 if x["phase"] in (2, 3) else 2,
            -int(x.get("setup_score") or x.get("score") or 0),
        ),
    )

    # Verify every chartable Top-1000 coin before cutting the display list.
    trigger_checks = 0
    trigger_eligible = 0
    trigger_no_chart = 0
    trigger_reason_counts = {}
    trigger_ok_examples = []
    trigger_pool = all_unified[:_EARLY_MOVER_TRIGGER_SCAN_LIMIT]
    for item in trigger_pool:
        contract = item.get("PerpChartSymbol") or item.get("PerpMatchSymbol")
        exchange = _normalize_crypto_exchange(item.get("PerpChartExchange") or item.get("BestExchange"))
        if not contract or not exchange:
            trigger_no_chart += 1
            _apply_early_mover_signal_state(item)
            continue
        trigger_eligible += 1
        trigger_check = _verify_early_mover_intraday_trigger(item)
        trigger_checks += 1
        reason = str(trigger_check.get("reason", "unknown") if isinstance(trigger_check, dict) else "unknown")
        trigger_reason_counts[reason] = trigger_reason_counts.get(reason, 0) + 1
        if isinstance(trigger_check, dict) and trigger_check.get("ok") and len(trigger_ok_examples) < 8:
            trigger_ok_examples.append({
                "symbol": item.get("Symbol"),
                "exchange": exchange,
                "reason": reason,
                "execution_score": trigger_check.get("execution_score"),
                "timeframe": trigger_check.get("timeframe"),
            })
        _apply_early_mover_signal_state(item, trigger_check)

    # Rebuild display selection after trigger checks so actionable rows can bubble up.
    all_unified = sorted(
        all_unified,
        key=lambda x: (
            0 if x.get("trade_signal") == "JETZT_TRADEN" else 1,
            -int(x.get("entry_score") or 0),
            1 if x["phase"] in (2, 3) else 2,
            -int(x.get("setup_score") or x.get("score") or 0),
        ),
    )
    phase_1 = [c for c in all_unified if c["phase"] == 1]
    phase_2 = [c for c in all_unified if c["phase"] == 2]
    phase_3 = [c for c in all_unified if c["phase"] == 3]
    p2_coins = phase_2
    p3_coins = phase_3
    p1_slots = max(0, MAX_DISPLAY - len(p2_coins) - len(p3_coins))
    p1_coins = phase_1[:p1_slots]
    unified = sorted(
        p1_coins + p2_coins + p3_coins,
        key=lambda x: (
            0 if x.get("trade_signal") == "JETZT_TRADEN" else 1,
            -int(x.get("entry_score") or 0),
            1 if x["phase"] in (2, 3) else 2,
            -int(x.get("setup_score") or x.get("score") or 0),
        ),
    )
    unified = sorted(
        all_unified,
        key=lambda x: (
            0 if x.get("trade_signal") == "JETZT_TRADEN" else 1,
            -int(x.get("entry_score") or 0),
            1 if x["phase"] in (2, 3) else 2,
            -int(x.get("setup_score") or x.get("score") or 0),
        ),
    )[:MAX_DISPLAY]

    unified = sorted(
        unified,
        key=lambda x: (
            0 if x.get("trade_signal") == "JETZT_TRADEN" else 1,
            -int(x.get("entry_score") or 0),
            1 if x["phase"] in (2, 3) else 2,
            -int(x.get("setup_score") or x.get("score") or 0),
        ),
    )
    p1_coins = [c for c in unified if c["phase"] == 1]
    p2_coins = [c for c in unified if c["phase"] == 2]
    p3_coins = [c for c in unified if c["phase"] == 3]

    stats = {
        "total_coins": len(all_coins),
        "unified_count": len(unified),
        "phase_1_count": len(p1_coins),
        "phase_2_count": len(p2_coins),
        "phase_3_count": len(p3_coins),
        "total_found": len(all_unified),  # Gesamtzahl vor Limit
        "trending_coins": len(trending_ids),
        "excluded_assets": excluded_assets,
        "btc_24h": btc_24h,
        "btc_7d": btc_7d,
        "perps_total": len(perp_data),
        "data_source": _CG_MARKETS_STATUS.get("source"),
        "data_warning": _CG_MARKETS_STATUS.get("warning"),
        "partial_data": _CG_MARKETS_STATUS.get("partial", False),
        "intraday_trigger_checks": trigger_checks,
        "intraday_trigger_eligible": trigger_eligible,
        "intraday_trigger_no_chart": trigger_no_chart,
        "intraday_trigger_reason_counts": trigger_reason_counts,
        "intraday_trigger_ok_examples": trigger_ok_examples,
        "intraday_trigger_scan_limit": _EARLY_MOVER_TRIGGER_SCAN_LIMIT,
        "intraday_trigger_scope": "all_chartable_top_1000",
        "market_universe_target": _EARLY_MOVER_MARKET_PAGES * 250,
        "market_sweep_candidates": len(market_sweep),
        "trade_now_count": sum(1 for c in unified if c.get("trade_signal") == "JETZT_TRADEN"),
        "explosion_armed_count": sum(
            1 for c in unified
            if c.get("pre_breakout_armed") or c.get("trade_signal") == "EXPLOSION_ARMED"
        ),
    }

    return {
        "coins": unified,
        "stats": stats,
    }


def _early_movers_wrapper() -> None:
    """Run Early Movers crypto scanner and save results."""
    try:
        print("[Early Movers] Starting crypto scanner...")

        # Fetch multi-exchange perp data once
        perp_data = fetch_multi_exchange_perps()

        # Run full analysis
        result = fetch_early_movers(_prefetched_perps=perp_data)

        # Save results
        save_cache_file(EARLY_MOVERS_CACHE, [result])
        s = result.get("stats", {})
        print(f"[Early Movers] Scan complete. {s.get('unified_count', 0)} coins — "
              f"Phase 1: {s.get('phase_1_count', 0)}, Phase 2: {s.get('phase_2_count', 0)}, "
              f"Phase 3: {s.get('phase_3_count', 0)}")
        _send_early_mover_long_alerts(result)
    except Exception as e:
        print(f"[Early Movers] Error: {e}")


@app.post("/api/early-movers-scan")
def trigger_early_movers():
    _run_scan_safe("early_movers", _early_movers_wrapper)
    return {"status": "started", "message": "Early Movers scan started"}


@app.get("/api/early-movers-results")
def get_early_movers():
    results, cached_at = load_cache_file(EARLY_MOVERS_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")
    decorated = _decorate_early_mover_results(results, cache_age)
    quality = _scan_quality_payload("early_movers", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


# ── Crash Monitor (VIX + Market Breadth) + Fear Score ──
# Note: _crash_monitor_wrapper is defined later with fear score functionality
CRASH_MONITOR_CACHE = "/tmp/crash_monitor_cache.json"


@app.post("/api/crash-monitor-scan")
def trigger_crash_monitor():
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
    _run_scan_safe("crash_monitor", _crash_monitor_wrapper)
    return {"status": "started", "message": "Crash monitor scan started"}


@app.get("/api/crash-monitor-results")
def get_crash_monitor():
    results, cached_at = load_cache_file(CRASH_MONITOR_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")
    if results and isinstance(results[0], dict) and results[0].get("status") == "error":
        quality = _scan_quality_payload("crash_monitor", cache_age, results)
        warnings = list(quality.get("warnings", []))
        warnings.append(results[0].get("message") or "Crash monitor scan failed")
        return {
            "status": "error",
            "data": results,
            "cached_at": cached_at,
            "cache_age_seconds": cache_age,
            "data_quality": quality,
            "warnings": warnings,
            "exclusion_policy": quality["exclusion_policy"],
        }
    decorated = _decorate_scan_results(results, "crash_monitor", cache_age)
    quality = _scan_quality_payload("crash_monitor", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


# ── BTC Divergenz ──
BTC_DIVERGENZ_CACHE = "/tmp/btc_divergenz_cache.json"

def _build_crypto_btc_divergence_results() -> List[Dict[str, Any]]:
    """Build crypto-only BTC divergence watch rows; no equities/ETFs belong here."""
    coins = _fetch_coingecko_markets(pages=4)
    if not coins:
        return []

    btc = next((c for c in coins if c.get("id") == "bitcoin" or str(c.get("symbol", "")).upper() == "BTC"), None)
    if not btc:
        return []

    btc_24h = float(btc.get("price_change_percentage_24h") or 0)
    btc_7d = float(btc.get("price_change_percentage_7d_in_currency") or btc.get("price_change_percentage_7d") or 0)
    btc_14d = float(btc.get("price_change_percentage_14d_in_currency") or 0)
    btc_regime = (
        "RISK_OFF" if btc_24h <= -1.5 or btc_7d <= -4
        else "STAGNANT" if abs(btc_24h) <= 1.5 and abs(btc_7d) <= 4
        else "RISK_ON"
    )
    btc_weak_or_flat = btc_regime in ("RISK_OFF", "STAGNANT")

    try:
        perp_data = fetch_multi_exchange_perps()
    except Exception:
        perp_data = {}

    rows: List[Dict[str, Any]] = []
    for coin in coins:
        try:
            cid = str(coin.get("id") or "")
            symbol = str(coin.get("symbol") or "").upper().strip()
            name = str(coin.get("name") or symbol)
            if not symbol or symbol == "BTC" or _is_excluded_crypto_asset(symbol, cid, name):
                continue

            price = float(coin.get("current_price") or 0)
            mcap = float(coin.get("market_cap") or 0)
            volume = float(coin.get("total_volume") or 0)
            if price <= 0 or mcap < 5_000_000 or volume < 250_000:
                continue

            change_1h = float(coin.get("price_change_percentage_1h_in_currency") or 0)
            change_24h = float(coin.get("price_change_percentage_24h") or 0)
            change_7d = float(coin.get("price_change_percentage_7d_in_currency") or coin.get("price_change_percentage_7d") or 0)
            change_14d = float(coin.get("price_change_percentage_14d_in_currency") or 0)
            alpha_24h = round(change_24h - btc_24h, 2)
            alpha_7d = round(change_7d - btc_7d, 2)
            alpha_14d = round(change_14d - btc_14d, 2)
            vol_mcap = round((volume / mcap * 100), 2) if mcap > 0 else 0

            perp_lookup = perp_data.get(symbol) or perp_data.get(f"1000{symbol}") or perp_data.get(f"10000{symbol}") or {}
            has_perp = bool(perp_lookup)
            contract = str(perp_lookup.get("best_contract_symbol") or (f"{symbol}USDT" if has_perp else ""))
            exchange = str(perp_lookup.get("best_chart_exchange") or perp_lookup.get("best_exchange") or "").lower()

            coin_explodes = change_24h >= 8 or change_7d >= 18
            strong_alpha = alpha_24h >= 6 or alpha_7d >= 12
            short_watch = btc_weak_or_flat and coin_explodes and strong_alpha
            long_watch = btc_regime == "RISK_ON" and alpha_24h >= 4 and alpha_7d >= 8 and change_24h > 0 and vol_mcap <= 120
            overheated = vol_mcap >= 100 or change_24h >= 25 or alpha_24h >= 18

            score = 0
            score += min(35, max(0, alpha_7d) * 1.2)
            score += min(25, max(0, alpha_24h) * 2.0)
            score += 18 if btc_regime == "RISK_OFF" else 12 if btc_regime == "STAGNANT" else 12 if long_watch else 0
            if 2 <= vol_mcap <= 80:
                score += 10
            elif vol_mcap > 150:
                score -= 12
            if has_perp:
                score += 10
            if volume < 1_000_000:
                score -= 10
            score = max(0, min(100, int(round(score))))

            if short_watch:
                signal = "SHORT-WATCH HEISS" if overheated else "SHORT-WATCH"
                trade_action = "WAIT_FOR_SHORT_TRIGGER"
                signal_label = "BTC schwach/seitwaerts, Coin outperformt stark - Short-Setup abwarten"
                bias = "SHORT"
            elif long_watch:
                signal = "LONG-WATCH"
                trade_action = "WAIT_FOR_LONG_TRIGGER"
                signal_label = "Coin zeigt relative Staerke bei BTC-Risk-On - Long-Setup abwarten"
                bias = "LONG"
            elif alpha_7d <= -10 and btc_regime != "RISK_OFF":
                signal = "WEAK VS BTC"
                trade_action = "BEOBACHTEN"
                signal_label = "Coin underperformt BTC - kein Momentum-Long"
                bias = "AVOID_LONG"
            else:
                signal = "NEUTRAL"
                trade_action = "BEOBACHTEN"
                signal_label = "Keine handelbare BTC-Divergenz"
                bias = "NEUTRAL"

            risk_flags = ["btc_divergence_watch_only", "requires_5m_trigger"]
            if not has_perp:
                risk_flags.append("no_perp_execution")
            if overheated:
                risk_flags.append("overheated_move")
            if vol_mcap > 150:
                risk_flags.append("extreme_turnover")

            rows.append({
                "ticker": symbol,
                "symbol": symbol,
                "name": name,
                "price": _round_crypto_price(price),
                "change_1h": round(change_1h, 2),
                "change_1d": round(change_24h, 2),
                "change_5d": round(change_7d, 2),
                "change_7d": round(change_7d, 2),
                "change_14d": round(change_14d, 2),
                "div_1d": alpha_24h,
                "div_5d": alpha_7d,
                "alpha_24h": alpha_24h,
                "alpha_7d": alpha_7d,
                "alpha_14d": alpha_14d,
                "btc_24h": round(btc_24h, 2),
                "btc_7d": round(btc_7d, 2),
                "btc_regime": btc_regime,
                "vol_mcap": vol_mcap,
                "market_cap": mcap,
                "volume_24h": volume,
                "has_perp": has_perp,
                "contract": contract,
                "exchange": exchange,
                "best_exchange": perp_lookup.get("best_exchange", ""),
                "funding_rate": perp_lookup.get("funding_rate", 0),
                "oi_ratio": perp_lookup.get("oi_ratio", 0),
                "score": score,
                "grade": "S" if score >= 85 else "A" if score >= 75 else "B" if score >= 60 else "C" if score >= 45 else "D",
                "signal": signal,
                "trade_bias": bias,
                "trade_action": trade_action,
                "trade_signal": "BEOBACHTEN",
                "entry_status": trade_action,
                "signal_label": signal_label,
                "signal_quality": "watch_only",
                "execution_trigger_ok": False,
                "risk_flags": risk_flags,
                "scanner_note": "BTC-Divergenz ist ein Watch-/Bias-Scanner: kein Trade ohne 5m Trigger, Retest oder Rejection.",
                "isCrypto": True,
            })
        except Exception as e:
            print(f"[BTC-Div Warning] {e}")
            continue

    return sorted(
        [r for r in rows if r.get("score", 0) >= 35 or r.get("signal") != "NEUTRAL"],
        key=lambda r: (
            0 if str(r.get("signal", "")).startswith("SHORT") else 1 if str(r.get("signal", "")).startswith("LONG") else 2,
            -float(r.get("score") or 0),
            -abs(float(r.get("alpha_7d") or 0)),
        ),
    )[:120]

def _btc_divergenz_wrapper() -> None:
    """Compare crypto coins/perps vs BTC; equities/ETFs are intentionally excluded."""
    try:
        save_cache_file(BTC_DIVERGENZ_CACHE, _build_crypto_btc_divergence_results())
        return
        assets = []
        results = []

        for sym, short, name in assets:
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/2024-01-01/2099-12-31"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if len(bars) < 2:
                    continue

                bars_by_date = {}
                for b in bars:
                    if not b.get("t") or not b.get("c"):
                        continue
                    d = datetime.utcfromtimestamp(b["t"] / 1000).strftime("%Y-%m-%d")
                    bars_by_date[d] = b
                if len(bars_by_date) < 2:
                    continue

                dates_desc = sorted(bars_by_date.keys(), reverse=True)
                close = bars_by_date[dates_desc[0]]["c"]

                entry = {
                    "ticker": sym,
                    "symbol": short,
                    "name": name,
                    "price": round(close, 2),
                    "change_1d": 0,
                    "change_5d": 0,
                    "change_20d": 0,
                    "_bars_by_date": bars_by_date,
                }
                results.append(entry)
            except Exception as e:
                print(f"[Warning] {e}")
                continue

        btc_data = next((r for r in results if r["ticker"] == "X:BTCUSD"), None)

        def _change_for_dates(series: Dict[str, Dict[str, Any]], dates_desc: List[str], lookback: int) -> float:
            if len(dates_desc) <= lookback:
                return 0.0
            now_close = series[dates_desc[0]].get("c", 0)
            old_close = series[dates_desc[lookback]].get("c", 0)
            return ((now_close - old_close) / old_close * 100) if old_close else 0.0

        def _aligned_returns(asset_series: Dict[str, Dict[str, Any]], btc_series: Dict[str, Dict[str, Any]], max_pairs: int = 20):
            common = sorted(set(asset_series.keys()) & set(btc_series.keys()), reverse=True)
            asset_returns, btc_returns = [], []
            for i in range(min(max_pairs, len(common) - 1)):
                d0, d1 = common[i], common[i + 1]
                a_prev = asset_series[d1].get("c", 0)
                b_prev = btc_series[d1].get("c", 0)
                if a_prev > 0 and b_prev > 0:
                    asset_returns.append((asset_series[d0]["c"] - a_prev) / a_prev * 100)
                    btc_returns.append((btc_series[d0]["c"] - b_prev) / b_prev * 100)
            return asset_returns, btc_returns, common

        # Calculate divergence vs BTC on matched market dates only.
        if btc_data:
            btc_series = btc_data.get("_bars_by_date", {})
            for r in results:
                series = r.get("_bars_by_date", {})
                _, _, common_dates = _aligned_returns(series, btc_series, max_pairs=20)
                if len(common_dates) >= 2:
                    r["change_1d"] = round(_change_for_dates(series, common_dates, 1), 2)
                    r["change_5d"] = round(_change_for_dates(series, common_dates, min(5, len(common_dates) - 1)), 2)
                    r["change_20d"] = round(_change_for_dates(series, common_dates, min(20, len(common_dates) - 1)), 2)

            btc_data["div_1d"] = 0
            btc_data["div_5d"] = 0
            btc_data["beta"] = 1.0
            btc_data["correlation"] = 1.0
            btc_data["z_score"] = 0
            btc_data["signal"] = "BTC"

            for r in results:
                if r["ticker"] == "X:BTCUSD":
                    continue
                asset_returns, btc_returns, common_dates = _aligned_returns(r.get("_bars_by_date", {}), btc_series, max_pairs=20)
                btc_change_1d = _change_for_dates(btc_series, common_dates, 1) if len(common_dates) >= 2 else 0
                btc_change_5d = _change_for_dates(btc_series, common_dates, min(5, len(common_dates) - 1)) if len(common_dates) >= 2 else 0
                r["div_1d"] = round(r.get("change_1d", 0) - btc_change_1d, 2)
                r["div_5d"] = round(r.get("change_5d", 0) - btc_change_5d, 2)

                beta = 1.0
                correlation = 0.0
                z_score = 0.0
                n = min(len(asset_returns), len(btc_returns))
                if n >= 10:
                    mean_a = sum(asset_returns[:n]) / n
                    mean_b = sum(btc_returns[:n]) / n
                    denom = max(n - 1, 1)
                    cov = sum((asset_returns[i] - mean_a) * (btc_returns[i] - mean_b) for i in range(n)) / denom
                    var_b = sum((btc_returns[i] - mean_b) ** 2 for i in range(n)) / denom
                    var_a = sum((asset_returns[i] - mean_a) ** 2 for i in range(n)) / denom
                    std_a = var_a ** 0.5 if var_a > 0 else 1
                    std_b = var_b ** 0.5 if var_b > 0 else 1
                    beta = cov / var_b if var_b > 0 else 1.0
                    correlation = max(-1.0, min(1.0, cov / (std_a * std_b) if (std_a * std_b) > 0 else 0.0))
                    expected_5d = btc_change_5d * beta
                    divergence = r.get("change_5d", 0) - expected_5d
                    residual_var = var_a + (beta ** 2) * var_b - 2 * beta * cov
                    residual_std = max(residual_var ** 0.5 if residual_var > 0 else std_a, 0.5)
                    z_score = divergence / residual_std

                r["beta"] = round(beta, 2)
                r["correlation"] = round(correlation, 2)
                r["z_score"] = round(z_score, 2)
                r["aligned_days"] = len(common_dates)

                if z_score > 1.5 and correlation > 0.5:
                    r["signal"] = "WATCH LONG-BIAS"
                elif z_score < -1.5 and correlation > 0.5:
                    r["signal"] = "WATCH RISIKO"
                elif abs(z_score) < 0.5:
                    r["signal"] = "NEUTRAL"
                else:
                    r["signal"] = "WATCH"
                r["signal_quality"] = "observe"
                r["entry_status"] = "BEOBACHTEN"
                r["trade_action"] = "BEOBACHTEN"
                r["trade_signal"] = "BEOBACHTEN"
                r["signal_label"] = "Achtung beobachten: BTC-Proxy-Kontext, kein Entry"
                r["execution_trigger_ok"] = False
                r["risk_flags"] = ["btc_divergence_context_only", "no_intraday_execution_trigger"]
                r["scanner_note"] = "BTC-Divergenz ist Kontext/Bias, kein Kauf- oder Short-Trigger."

        # Remove internal bars and filter out BTC reference before saving
        final_results = []
        for r in results:
            if "_bars_by_date" in r:
                del r["_bars_by_date"]
            # BTC ist nur Referenz, nicht in Ergebnisliste anzeigen
            if r["ticker"] != "X:BTCUSD":
                final_results.append(r)

        save_cache_file(BTC_DIVERGENZ_CACHE, final_results)
    except Exception as e:
        print(f"BTC divergenz error: {e}")


@app.post("/api/btc-divergenz-scan")
def trigger_btc_divergenz():
    _run_scan_safe("btc_divergenz", _btc_divergenz_wrapper)
    return {"status": "started", "message": "BTC Divergenz scan started"}


@app.get("/api/btc-divergenz-results")
def get_btc_divergenz():
    results, cached_at = load_cache_file(BTC_DIVERGENZ_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")
    decorated = _decorate_scan_results(results, "btc_divergenz", cache_age)
    decorated = _apply_signal_only_policy("btc_divergenz", decorated)
    quality = _scan_quality_payload("btc_divergenz", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


# ── Money Flow (Sector Performance) ──
MONEY_FLOW_CACHE = "/tmp/money_flow_cache.json"
NARRATIVE_PULSE_CACHE = "/tmp/narrative_pulse_cache.json"
NARRATIVE_PULSE_DEDUPE_SEC = 30 * 60 * 60
NARRATIVE_PULSE_FREQUENCIES = {
    "daily": {"label": "Taeglich", "ttl": 30 * 60 * 60},
    "twice_daily": {"label": "2x taeglich", "ttl": 13 * 60 * 60},
    "weekly": {"label": "Woechentlich", "ttl": 8 * 24 * 60 * 60},
}

SECTOR_ETFS = {
    "XLK": "Technologie", "XLF": "Finanzen", "XLV": "Gesundheit",
    "XLE": "Energie", "XLI": "Industrie", "XLY": "Konsum (zyklisch)",
    "XLP": "Konsum (defensiv)", "XLU": "Versorger", "XLRE": "Immobilien",
    "XLB": "Grundstoffe", "XLC": "Kommunikation",
}

NARRATIVE_PROXIES = {
    "SMH": {"name": "Semiconductors", "examples": ["NVDA", "AMD", "AVGO", "MU", "QCOM", "TSM", "ASML", "MX"]},
    "XBI": {"name": "Biotech", "examples": ["VKTX", "MRNA", "BIIB", "REGN", "VRTX", "CRSP"]},
    "KRE": {"name": "Regional Banks", "examples": ["WAL", "ZION", "CFG", "KEY", "CMA", "RF"]},
    "XRT": {"name": "Retail", "examples": ["WMT", "TGT", "COST", "M", "BBY", "ANF"]},
    "XHB": {"name": "Homebuilders", "examples": ["DHI", "LEN", "PHM", "TOL", "KBH", "NVR"]},
    "XOP": {"name": "Oil & Gas", "examples": ["XOM", "CVX", "OXY", "EOG", "DVN", "APA"]},
    "XME": {"name": "Metals & Mining", "examples": ["FCX", "NUE", "CLF", "AA", "STLD", "X"]},
    "GDX": {"name": "Gold Miners", "examples": ["NEM", "GOLD", "AEM", "KGC", "AU", "HMY"]},
    "TAN": {"name": "Solar", "examples": ["FSLR", "ENPH", "SEDG", "RUN", "NXT", "ARRY"]},
    "ARKK": {"name": "Speculative Growth", "examples": ["TSLA", "COIN", "ROKU", "HOOD", "PLTR", "SOFI"]},
    "IYT": {"name": "Transports", "examples": ["UPS", "FDX", "UNP", "DAL", "UAL", "LUV"]},
}


def _calculate_cmf(closes: List[float], highs: List[float], lows: List[float], volumes: List[float], period: int = 20) -> float:
    """
    Fix 3b: Chaikin Money Flow (CMF) Indicator
    CMF > 0.1 = strong buying pressure
    CMF < -0.1 = strong selling pressure
    """
    if len(closes) < period:
        return 0

    mfv = []  # Money Flow Volume
    for i in range(len(closes)):
        hl_range = highs[i] - lows[i]
        if hl_range > 0:
            mf_mult = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / hl_range
            mfv.append(mf_mult * volumes[i])
        else:
            mfv.append(0)

    if len(mfv) >= period:
        cmf = sum(mfv[-period:]) / sum(volumes[-period:]) if sum(volumes[-period:]) > 0 else 0
        return round(cmf, 4)
    return 0


def _fetch_daily_proxy_perf(ticker: str, limit: int = 30) -> Optional[Dict[str, Any]]:
    """Fetch recent daily performance for a sector/theme proxy or representative stock."""
    if not ticker or not POLYGON_KEY:
        return None
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
    resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": limit, "sort": "desc"}, timeout=15)
    if resp.status_code != 200:
        return None
    bars = resp.json().get("results", [])
    if len(bars) < 2:
        return None

    close = float(bars[0].get("c", 0) or 0)
    prev = float(bars[1].get("c", 0) or 0)
    if close <= 0 or prev <= 0:
        return None

    idx_5 = min(5, len(bars) - 1)
    idx_20 = min(20, len(bars) - 1)
    base_5 = float(bars[idx_5].get("c", close) or close)
    base_20 = float(bars[idx_20].get("c", close) or close)
    vol = float(bars[0].get("v", 0) or 0)
    vol_window = [float(b.get("v", 0) or 0) for b in bars[1:21]]
    avg_vol = sum(vol_window) / len(vol_window) if vol_window else 0

    closes = [float(b.get("c", 0) or 0) for b in reversed(bars)]
    volumes = [float(b.get("v", 0) or 0) for b in reversed(bars)]
    highs = [float(b.get("h", 0) or 0) for b in reversed(bars)]
    lows = [float(b.get("l", 0) or 0) for b in reversed(bars)]
    obv_values = calculate_obv(closes, volumes)
    obv_change = 0.0
    if len(obv_values) >= 6 and obv_values[-6] != 0:
        obv_change = (obv_values[-1] - obv_values[-6]) / abs(obv_values[-6]) * 100

    cmf = _calculate_cmf(closes, highs, lows, volumes, period=20)
    return {
        "ticker": ticker,
        "price": round(close, 2),
        "change_1d": round(((close - prev) / prev) * 100, 2),
        "change_5d": round(((close - base_5) / base_5) * 100, 2) if base_5 > 0 else 0,
        "change_20d": round(((close - base_20) / base_20) * 100, 2) if base_20 > 0 else 0,
        "volume": vol,
        "rvol": round(vol / avg_vol, 2) if avg_vol > 0 else 0,
        "obv_change": round(obv_change, 2),
        "cmf": cmf,
    }


def _narrative_score(row: Dict[str, Any]) -> float:
    score = (
        float(row.get("change_5d", 0) or 0) * 0.55
        + float(row.get("change_20d", 0) or 0) * 0.25
        + float(row.get("change_1d", 0) or 0) * 0.20
    )
    rvol = float(row.get("rvol", 0) or 0)
    cmf = float(row.get("cmf", 0) or 0)
    obv = float(row.get("obv_change", 0) or 0)
    if rvol >= 1.3:
        score += min(3.0, (rvol - 1.0) * 2.0)
    if cmf > 0.10:
        score += 1.5
    elif cmf < -0.10:
        score -= 1.5
    if obv > 8:
        score += 1.0
    elif obv < -8:
        score -= 1.0
    return round(score, 2)


def _narrative_bias(score: float) -> str:
    if score >= 4:
        return "BULLISCH"
    if score <= -4:
        return "BEARISCH"
    return "NEUTRAL"


def _narrative_representatives(tickers: List[str], direction: str, max_items: int = 3) -> List[Dict[str, Any]]:
    reps: List[Dict[str, Any]] = []
    for ticker in tickers[:8]:
        try:
            perf = _fetch_daily_proxy_perf(ticker, limit=25)
            if perf:
                reps.append({
                    "ticker": ticker,
                    "change_1d": perf.get("change_1d", 0),
                    "change_5d": perf.get("change_5d", 0),
                    "rvol": perf.get("rvol", 0),
                })
        except Exception as exc:
            print(f"[Narrative] representative skip {ticker}: {exc}")
    reverse = direction == "bull"
    reps.sort(key=lambda item: (float(item.get("change_5d", 0) or 0), float(item.get("change_1d", 0) or 0)), reverse=reverse)
    return reps[:max_items]


def _build_narrative_pulse(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    enriched: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        score = _narrative_score(item)
        item["narrative_score"] = score
        item["bias"] = _narrative_bias(score)
        enriched.append(item)

    bullish = sorted(enriched, key=lambda x: x.get("narrative_score", 0), reverse=True)[:5]
    bearish = sorted(enriched, key=lambda x: x.get("narrative_score", 0))[:5]

    for item in bullish[:3]:
        examples = item.get("examples") or []
        item["representatives"] = _narrative_representatives(examples, "bull") if examples else []
    for item in bearish[:3]:
        examples = item.get("examples") or []
        item["representatives"] = _narrative_representatives(examples, "bear") if examples else []

    return {
        "status": "success",
        "generated_at": datetime.now().isoformat(),
        "bullish": bullish,
        "bearish": bearish,
        "all": sorted(enriched, key=lambda x: x.get("narrative_score", 0), reverse=True),
    }


def _format_narrative_row(item: Dict[str, Any]) -> str:
    reps = item.get("representatives") or []
    rep_text = ", ".join(
        f"{html.escape(str(rep.get('ticker', '')))} {float(rep.get('change_5d', 0) or 0):+.1f}%"
        for rep in reps
    ) or html.escape(", ".join(item.get("examples", [])[:3]) or "-")
    color = "#059669" if float(item.get("narrative_score", 0) or 0) >= 0 else "#dc2626"
    return (
        "<tr>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'><b>{html.escape(str(item.get('sector', item.get('narrative', ''))))}</b><br>"
        f"<span style='color:#64748b'>{html.escape(str(item.get('ticker', '')))} Proxy</span></td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;color:{color};font-weight:bold'>{float(item.get('narrative_score', 0) or 0):+.1f}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{float(item.get('change_1d', 0) or 0):+.1f}%</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{float(item.get('change_5d', 0) or 0):+.1f}%</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{float(item.get('change_20d', 0) or 0):+.1f}%</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{float(item.get('rvol', 0) or 0):.1f}x</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{html.escape(rep_text)}</td>"
        "</tr>"
    )


def _narrative_pulse_bucket(frequency: str, utc_now: datetime) -> str:
    frequency = str(frequency or "daily").strip().lower()
    if frequency == "twice_daily":
        bucket = "am" if utc_now.hour < 12 else "pm"
        return f"{utc_now.strftime('%Y%m%d')}_{bucket}"
    if frequency == "weekly":
        iso = utc_now.isocalendar()
        return f"{iso.year}_w{iso.week:02d}"
    return utc_now.strftime("%Y%m%d")


def _narrative_pulse_recipients(frequency: str) -> List[str]:
    if not HAS_AUTH or not ALERT_SEND_TO_SUBSCRIBERS:
        return []
    try:
        return get_email_alert_recipients("narrative_pulse", frequency)
    except Exception as exc:
        print(f"[Narrative] Recipient filter failed: {exc}")
        return []


def _send_narrative_pulse_email(payload: Dict[str, Any], now: Optional[float] = None) -> bool:
    now = now or time.time()
    utc_now = datetime.now(timezone.utc)

    bullish = payload.get("bullish", [])[:5]
    bearish = payload.get("bearish", [])[:5]
    if not bullish and not bearish:
        _record_email_event("Narrative Pulse Daily", "skipped", "empty_payload")
        return False

    top_bull = bullish[0].get("sector", bullish[0].get("narrative", "-")) if bullish else "-"
    top_bear = bearish[0].get("sector", bearish[0].get("narrative", "-")) if bearish else "-"
    bull_rows = "".join(_format_narrative_row(item) for item in bullish)
    bear_rows = "".join(_format_narrative_row(item) for item in bearish)
    table_head = (
        "<tr style='background:#f8fafc'><th style='padding:8px;text-align:left'>Narrativ</th>"
        "<th style='padding:8px;text-align:left'>Score</th><th style='padding:8px;text-align:left'>1D</th>"
        "<th style='padding:8px;text-align:left'>5D</th><th style='padding:8px;text-align:left'>20D</th>"
        "<th style='padding:8px;text-align:left'>RVOL</th><th style='padding:8px;text-align:left'>Beispiele</th></tr>"
    )
    body = f"""<html><body style="font-family:Arial,sans-serif;max-width:820px;margin:0 auto;color:#111827">
    <h2 style="margin-bottom:4px">Alpha Station Narrative Pulse</h2>
    <p style="color:#64748b;margin-top:0">{utc_now.strftime('%d.%m.%Y %H:%M')} UTC | Markt-Rotation, keine Einzeltrade-Freigabe.</p>
    <p><b>Bullischstes Narrativ:</b> {html.escape(str(top_bull))}<br>
    <b>Bearischstes Narrativ:</b> {html.escape(str(top_bear))}</p>
    <h3 style="color:#059669">Bullischste Narrative</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">{table_head}{bull_rows}</table>
    <h3 style="color:#dc2626;margin-top:22px">Bearischste Narrative</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">{table_head}{bear_rows}</table>
    <p style="color:#64748b;font-size:12px;margin-top:18px">Score mischt 5D/20D/1D Performance, Aktivitaet, CMF und OBV. Das zeigt, wohin Kapital rotiert; Entries kommen weiterhin nur ueber die separaten Scanner mit Entry/Stop/TP.</p>
    </body></html>"""
    sent_any = False
    tried_filtered_recipients = False
    for frequency, cfg in NARRATIVE_PULSE_FREQUENCIES.items():
        recipients = _narrative_pulse_recipients(frequency)
        if not recipients:
            continue
        tried_filtered_recipients = True
        bucket = _narrative_pulse_bucket(frequency, utc_now)
        dedupe_key = f"narrative_pulse_{frequency}_{bucket}"
        if not _email_dedupe_claim(dedupe_key, int(cfg["ttl"]), now=now):
            _record_email_event(f"Narrative Pulse {frequency}", "skipped", "frequency_dedupe_active")
            continue
        subject = f"Narrative Pulse ({cfg['label']}): Bullisch {top_bull} | Bearisch {top_bear}"
        sent = _send_email_alert(subject, body, recipient_emails=recipients)
        sent_any = bool(sent) or sent_any

    if tried_filtered_recipients or (HAS_AUTH and ALERT_SEND_TO_SUBSCRIBERS):
        return sent_any

    # Fallback for single-user/no-auth deployments.
    day_key = utc_now.strftime("%Y%m%d")
    dedupe_key = f"narrative_pulse_daily_{day_key}"
    if not _email_dedupe_claim(dedupe_key, NARRATIVE_PULSE_DEDUPE_SEC, now=now):
        _record_email_event("Narrative Pulse Daily", "skipped", "daily_dedupe_active")
        return False
    return _send_email_alert(f"Narrative Pulse: Bullisch {top_bull} | Bearisch {top_bear}", body)


def _money_flow_wrapper() -> None:
    """Fetch sector ETF performance for money flow analysis."""
    try:
        sectors = []
        proxy_universe = {
            **{ticker: {"name": name, "type": "sector", "examples": []} for ticker, name in SECTOR_ETFS.items()},
            **{ticker: {"name": cfg["name"], "type": "theme", "examples": cfg.get("examples", [])} for ticker, cfg in NARRATIVE_PROXIES.items()},
        }
        for ticker, cfg in proxy_universe.items():
            name = cfg["name"]
            try:
                perf = _fetch_daily_proxy_perf(ticker, limit=30)
                if not perf:
                    continue
                close = perf["price"]
                chg_1d = perf["change_1d"]
                chg_5d = perf["change_5d"]
                chg_20d = perf["change_20d"]
                vol = perf["volume"]
                rvol = perf["rvol"]
                cmf = perf["cmf"]
                obv_change = perf["obv_change"]
                if obv_change > 10 and chg_5d < 2:
                    obv_signal = "ACCUMULATION"
                elif obv_change < -10 and chg_5d > -2:
                    obv_signal = "DISTRIBUTION"
                else:
                    obv_signal = "NEUTRAL"

                if cmf > 0.1:
                    cmf_signal = "BUYING"
                elif cmf < -0.1:
                    cmf_signal = "SELLING"
                else:
                    cmf_signal = "NEUTRAL"

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
                    "ticker": ticker, "sector": name, "narrative": name, "narrative_type": cfg.get("type", "sector"),
                    "examples": cfg.get("examples", []),
                    "price": round(close, 2),
                    "change_1d": round(chg_1d, 2), "change_5d": round(chg_5d, 2),
                    "change_20d": round(chg_20d, 2), "volume": vol, "rvol": rvol,
                    "narrative_score": _narrative_score({
                        "change_1d": chg_1d, "change_5d": chg_5d, "change_20d": chg_20d,
                        "rvol": rvol, "cmf": cmf, "obv_change": obv_change,
                    }),
                    "flow_signal": flow,
                    "trade_signal": "BEOBACHTEN",
                    "trade_action": "BEOBACHTEN",
                    "signal_label": "Achtung beobachten: Marktrotation, kein Einzeltrade-Signal",
                    "execution_trigger_ok": False,
                    "obv_signal": obv_signal,
                    "obv_change": round(obv_change, 2),
                    "cmf": cmf,
                    "cmf_signal": cmf_signal,
                })
            except Exception as e:
                print(f"[Warning] Error processing sector {name} ticker {ticker}: {e}")
                continue

        sectors.sort(key=lambda x: x.get("change_5d", 0), reverse=True)
        save_cache_file(MONEY_FLOW_CACHE, sectors)
        narrative_payload = _build_narrative_pulse(sectors)
        save_cache_file(NARRATIVE_PULSE_CACHE, narrative_payload)
        _send_narrative_pulse_email(narrative_payload)
    except Exception as e:
        print(f"Money flow error: {e}")


@app.post("/api/money-flow-scan")
def trigger_money_flow():
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
    _run_scan_safe("money_flow", _money_flow_wrapper)
    return {"status": "started", "message": "Money Flow scan started"}


@app.get("/api/money-flow-results")
def get_money_flow():
    results, cached_at = load_cache_file(MONEY_FLOW_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] Error calculating cache age: {e}")
    decorated = _decorate_scan_results(results, "money_flow", cache_age)
    quality = _scan_quality_payload("money_flow", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


@app.get("/api/narrative-pulse")
def get_narrative_pulse():
    payload, cached_at = load_cache_file(NARRATIVE_PULSE_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] Error calculating narrative pulse cache age: {e}")
    return {
        "status": "success" if payload else "empty",
        "data": payload or {},
        "cached_at": cached_at,
        "cache_age_seconds": cache_age,
        "data_source": "Sector/theme proxy rotation + representative stock performance",
        "note": "Narrative context only; entries still require scanner trade setup.",
    }


# ── New Listing Scanner ──
NEW_LISTING_CACHE = "/tmp/new_listing_scanner.json"

def _display_crypto_contract_symbol(symbol: str) -> str:
    """Clean exchange contract suffixes without eating real ticker letters."""
    display = str(symbol or "").strip().upper()
    for suffix in ("USD-PERP", "USDT-PERP", "_USDT", "-USDT", "USDT", "_PERP", "-PERP", "USD"):
        if display.endswith(suffix) and len(display) > len(suffix):
            display = display[:-len(suffix)]
            break
    return display or str(symbol or "").strip().upper()


def _listing_announcement_exchange(value: Any) -> str:
    """Normalize announcement source names to their exchange."""
    source = str(value or "").strip().lower()
    if source.endswith("_announcement"):
        source = source[: -len("_announcement")]
    return source


def _new_listing_exchange_mismatch(row: Dict[str, Any]) -> bool:
    """Block stale rows where a headline source and tradable contract disagree."""
    ann_exchange = _listing_announcement_exchange(
        row.get("announcement_exchange") or row.get("announcement_source")
    )
    trade_exchange = str(row.get("exchange") or "").strip().lower()
    if not ann_exchange or not trade_exchange:
        return False
    return ann_exchange != trade_exchange


def _flatten_new_listing_pipeline_results(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert rich module results to the flat table shape used by the FastAPI UI."""
    flat = []

    def _positive_float(*values: Any) -> float:
        for value in values:
            try:
                numeric = float(value or 0)
            except (TypeError, ValueError):
                continue
            if numeric > 0:
                return numeric
        return 0.0

    def _append_signal(entry: Dict[str, Any], bucket: str) -> None:
        sig = entry.get("signal", {}) or {}
        pump = sig.get("pump_data", {}) or {}
        raw_symbol = entry.get("symbol") or sig.get("symbol") or ""
        display_symbol = _display_crypto_contract_symbol(raw_symbol)
        timing = sig.get("timing", "")
        if bucket == "signals":
            signal_label = "SHORT" if "SHORT" in timing.upper() else timing or "SHORT"
        elif bucket == "watchlist":
            signal_label = "BEOBACHTEN"
        else:
            signal_label = sig.get("grade", "MONITOR")

        flat.append({
            "symbol": display_symbol,
            "exchange": entry.get("exchange", ""),
            "contract": raw_symbol,
            "price": _positive_float(pump.get("current_price"), pump.get("micro_current_price"), sig.get("entry")),
            "announcement_title": entry.get("announcement_title", ""),
            "announcement_url": entry.get("announcement_url", ""),
            "announcement_source": entry.get("announcement_source", ""),
            "announcement_exchange": entry.get("announcement_exchange", ""),
            "contract_confirmed": entry.get("contract_confirmed"),
            "tradable_contract_confirmed": entry.get("tradable_contract_confirmed"),
            "change_24h": entry.get("change_24h", 0),
            "volume_24h": pump.get("volume_usd_24h", 0),
            "pump_pct": pump.get("pump_pct", 0),
            "from_ath_pct": pump.get("from_ath_pct", 0),
            "exhaustion_score": sig.get("exh_score", 0),
            "exhaustion_details": sig.get("exh_details", []),
            "signal": signal_label,
            "entry": sig.get("entry", 0),
            "stop": sig.get("stop_loss", sig.get("stop", 0)),
            "tp1": sig.get("tp1", 0),
            "tp2": sig.get("tp2", 0),
            "confirmations": 0,
            "listing_date": entry.get("detected_at", ""),
            "hours_tracked": pump.get("hours_tracked", 0),
            "listing_age_hours": sig.get("listing_age_hours", pump.get("listing_age_hours", entry.get("listing_age_hours"))),
            "listing_age_source": sig.get("listing_age_source", pump.get("listing_age_source", entry.get("listing_age_source"))),
            "listing_source": sig.get("listing_source", pump.get("listing_source", entry.get("listing_source", ""))),
            "listing_trade_ok": sig.get("listing_trade_ok", entry.get("listing_trade_ok", False)),
            "trade_category": sig.get("trade_category", entry.get("trade_category", "")),
            "trade_action": "SHORT_NOW" if bucket == "signals" and sig.get("listing_trade_ok") else "BEOBACHTEN",
            "trade_signal": "JETZT_TRADEN" if bucket == "signals" and sig.get("listing_trade_ok") else "BEOBACHTEN",
            "signal_label": "Jetzt shorten" if bucket == "signals" and sig.get("listing_trade_ok") else "Achtung beobachten",
            "vol_ratio": pump.get("vol_ratio", 0),
            "funding_rate": pump.get("funding_rate", 0),
            "long_pct": pump.get("long_pct", 0),
            "red_streak": pump.get("red_streak", 0),
            "btc_divergence": pump.get("btc_divergence", 0),
            "btc_change_pct": pump.get("btc_change_pct", sig.get("btc_change_pct")),
            "coin_change_pct": pump.get("coin_change_pct", sig.get("coin_change_pct")),
            "btc_short_context": pump.get("btc_short_context", sig.get("btc_short_context", "")),
            "btc_tailwind_risk": pump.get("btc_tailwind_risk", sig.get("btc_tailwind_risk", False)),
            "btc_context_ok": sig.get("btc_context_ok", True),
            "rr1": sig.get("rr1", 0),
            "rr2": sig.get("rr2", 0),
            "rr_effective": sig.get("rr_effective", sig.get("rr1", 0)),
            "tp1_missed": sig.get("tp1_missed", False),
            "tp2_missed": sig.get("tp2_missed", False),
            "timing_quality": sig.get("timing_quality", 0),
            "grade": sig.get("grade", ""),
            "safety_ok": sig.get("safety_ok", False),
            "safety_warnings": sig.get("safety_warnings", []),
            "risk_pct": sig.get("risk_pct", 0),
            "stop_loss": sig.get("stop_loss", 0),
            "hard_stop_loss": sig.get("hard_stop_loss", 0),
            "stop_model": sig.get("stop_model", ""),
            "setup_type": sig.get("setup_type", ""),
            "micro_trigger_ok": pump.get("micro_trigger_ok", False),
            "micro_score": pump.get("micro_score", 0),
            "micro_reasons": pump.get("micro_reasons", []),
            "micro_warnings": pump.get("micro_warnings", []),
            "micro_from_high_pct": pump.get("micro_from_high_pct", 0),
            "micro_current_price": pump.get("micro_current_price", 0),
            "confirmation_ok": sig.get("confirmation_ok", False),
            "continuation_risk": sig.get("continuation_risk", False),
            "signal_quality": sig.get("signal_quality", ""),
            "risk_flags": sig.get("risk_flags", []),
            "source": bucket,
            "raw_score": pump.get("raw_score", sig.get("exh_score", 0)),
        })

    for bucket in ("signals", "watchlist"):
        for entry in payload.get(bucket, []) or []:
            if isinstance(entry, dict):
                _append_signal(entry, bucket)

    for item in payload.get("monitoring", []) or []:
        if not isinstance(item, dict):
            continue
        raw_symbol = item.get("symbol", "")
        flat.append({
            "symbol": _display_crypto_contract_symbol(raw_symbol),
            "exchange": item.get("exchange", ""),
            "contract": raw_symbol,
            "price": _positive_float(item.get("price"), item.get("micro_current_price")),
            "announcement_title": item.get("announcement_title", ""),
            "announcement_url": item.get("announcement_url", ""),
            "announcement_source": item.get("announcement_source", ""),
            "announcement_exchange": item.get("announcement_exchange", ""),
            "contract_confirmed": item.get("contract_confirmed"),
            "tradable_contract_confirmed": item.get("tradable_contract_confirmed"),
            "pump_pct": item.get("pump_pct", 0),
            "from_ath_pct": item.get("from_ath_pct", 0),
            "exhaustion_score": item.get("exh_score", 0),
            "signal": item.get("timing", "MONITOR"),
            "funding_rate": item.get("funding_rate", 0),
            "grade": item.get("grade", ""),
            "hours_tracked": item.get("hours_tracked", 0),
            "listing_age_hours": item.get("listing_age_hours"),
            "listing_age_source": item.get("listing_age_source"),
            "listing_source": item.get("source", ""),
            "listing_trade_ok": item.get("listing_trade_ok", False),
            "trade_category": item.get("trade_category", ""),
            "trade_action": "BEOBACHTEN",
            "trade_signal": "BEOBACHTEN",
            "signal_label": "Achtung beobachten",
            "vol_ratio": item.get("volume_ratio", 0),
            "safety_ok": item.get("safety_ok", False),
            "safety_warnings": item.get("safety_warnings", []),
            "rr_effective": item.get("rr_effective", 0),
            "risk_pct": item.get("risk_pct", 0),
            "stop_loss": item.get("stop_loss", 0),
            "hard_stop_loss": item.get("hard_stop_loss", 0),
            "stop_model": item.get("stop_model", ""),
            "setup_type": item.get("setup_type", ""),
            "micro_trigger_ok": item.get("micro_trigger_ok", False),
            "micro_score": item.get("micro_score", 0),
            "micro_reasons": item.get("micro_reasons", []),
            "micro_warnings": item.get("micro_warnings", []),
            "micro_from_high_pct": item.get("micro_from_high_pct", 0),
            "micro_current_price": item.get("micro_current_price", 0),
            "btc_change_pct": item.get("btc_change_pct"),
            "coin_change_pct": item.get("coin_change_pct"),
            "btc_divergence": item.get("btc_divergence"),
            "btc_short_context": item.get("btc_short_context", ""),
            "btc_tailwind_risk": item.get("btc_tailwind_risk", False),
            "confirmation_ok": item.get("confirmation_ok", False),
            "continuation_risk": item.get("continuation_risk", False),
            "signal_quality": item.get("signal_quality", ""),
            "risk_flags": item.get("risk_flags", []),
            "source": "monitoring",
        })

    for ann in payload.get("announcement_watchlist", []) or []:
        if not isinstance(ann, dict):
            continue
        base = _display_crypto_contract_symbol(ann.get("base") or "")
        if not base:
            continue
        contracts = ann.get("matched_contracts", []) if isinstance(ann.get("matched_contracts", []), list) else []
        exchange = ann.get("exchange", "")
        contract = ""
        same_exchange_contracts = [
            c for c in contracts
            if str(c.get("exchange") or "").lower() == str(exchange or "").lower()
        ]
        if same_exchange_contracts:
            contract = same_exchange_contracts[0].get("symbol") or ""
        contract_confirmed = bool(same_exchange_contracts or ann.get("contract_confirmed"))
        warnings = ["announcement_watch", "wait_for_dump_trigger"]
        if not contract_confirmed:
            warnings.append("contract_not_live_on_announcement_exchange")
        flat.append({
            "symbol": base,
            "exchange": exchange,
            "contract": contract,
            "price": 0,
            "pump_pct": 0,
            "from_ath_pct": 0,
            "exhaustion_score": 0,
            "signal": "ANNOUNCEMENT WATCH",
            "funding_rate": 0,
            "grade": "WATCH",
            "hours_tracked": ann.get("age_hours"),
            "listing_age_hours": ann.get("age_hours"),
            "listing_age_source": "announcement_time",
            "listing_source": ann.get("source", ""),
            "listing_trade_ok": False,
            "contract_confirmed": contract_confirmed,
            "tradable_contract_confirmed": contract_confirmed,
            "cross_exchange_contracts": ann.get("cross_exchange_contracts", []),
            "trade_category": "ANNOUNCEMENT_WATCH",
            "trade_action": "BEOBACHTEN",
            "trade_signal": "BEOBACHTEN",
            "signal_label": "Neues Listing beobachten",
            "rr_effective": 0,
            "risk_pct": 0,
            "safety_ok": False,
            "safety_warnings": warnings,
            "risk_flags": warnings,
            "announcement_title": ann.get("title", ""),
            "announcement_url": ann.get("url", ""),
            "announcement_source": ann.get("source", ""),
            "source": "announcement",
        })

    def _dedupe_rank(row: Dict[str, Any]) -> tuple:
        action = str(row.get("trade_action") or row.get("trade_signal") or "").upper()
        category = str(row.get("trade_category") or "").upper()
        source = str(row.get("source") or "").lower()
        priority = 5
        if action == "SHORT_NOW" or category == "NEW_LISTING_DUMP":
            priority = 0
        elif source == "watchlist":
            priority = 1
        elif source == "monitoring":
            priority = 2
        elif source == "announcement":
            priority = 4
        return (
            priority,
            0 if _positive_float(row.get("price")) > 0 else 1,
            -float(row.get("exhaustion_score") or 0),
            -float(row.get("pump_pct") or 0),
        )

    deduped: Dict[tuple, Dict[str, Any]] = {}
    for row in flat:
        key = (
            str(row.get("symbol") or "").upper(),
            str(row.get("exchange") or "").lower(),
        )
        if key not in deduped or _dedupe_rank(row) < _dedupe_rank(deduped[key]):
            deduped[key] = row
    flat = list(deduped.values())

    flat.sort(key=lambda r: (
        0 if str(r.get("signal", "")).startswith("SHORT") else 1,
        1 if _positive_float(r.get("price")) <= 0 else 0,
        -float(r.get("exhaustion_score") or 0),
        -float(r.get("pump_pct") or 0),
    ))
    return flat


def _new_listing_wrapper() -> None:
    """Run the full Pump & Dump scanner pipeline and cache flat UI results."""
    if not HAS_NEW_LISTING_SCANNER:
        print("[New Listing] Module not available")
        return

    try:
        seed_instrument_cache()
        payload = run_new_listing_scanner()
        _send_new_listing_pipeline_alerts(payload if isinstance(payload, dict) else {})
        results = _flatten_new_listing_pipeline_results(payload if isinstance(payload, dict) else {})
        save_cache_file(NEW_LISTING_CACHE, results)
        print(f"[New Listing] Full pipeline processed {len(results)} UI rows")
    except Exception as e:
        print(f"New listing wrapper error: {e}")


@app.post("/api/new-listing-scan")
def trigger_new_listing_scan():
    """Trigger new listing scanner (Crypto.com and other exchanges)."""
    if not HAS_NEW_LISTING_SCANNER:
        raise HTTPException(status_code=400, detail="New listing scanner module not available")

    _run_scan_safe("new_listing", _new_listing_wrapper)
    return {"status": "started", "message": "New Listing scan started"}


@app.get("/api/new-listing-results")
def get_new_listing_results():
    """Get cached new listing scan results."""
    results, cached_at = load_cache_file(NEW_LISTING_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")
    raw_count = len(results) if results else 0
    decorated = _decorate_scan_results(results, "new_listing", cache_age)
    decorated = _apply_signal_only_policy("new_listing", decorated)
    stats = {
        "raw_rows": raw_count,
        "new_listings": len(decorated) if decorated else 0,
        "exchanges_monitored": len(set(r.get("exchange", "") for r in decorated)) if decorated else 0,
        "active_signals": len([r for r in decorated if r.get("trade_signal") == "JETZT_TRADEN" or r.get("trade_action") == "SHORT_NOW"]) if decorated else 0,
        "suppressed_watch_rows": max(0, raw_count - len(decorated or [])),
        "signal_only": True,
    }
    quality = _scan_quality_payload("new_listing", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "stats": stats, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


# ── Volume Spikes Scanner ──
VOLUME_SPIKES_CACHE = "/tmp/volume_spikes_cache.json"

def _volume_spikes_wrapper() -> None:
    """Find US stocks with unusual volume (RVOL > 3.0, price > $2)."""
    try:
        spikes = []
        tickers = []

        try:
            snap_resp = rate_limited_get(
                "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"apiKey": POLYGON_KEY},
                timeout=30,
            )
            if snap_resp.status_code == 200:
                tickers.extend(snap_resp.json().get("tickers", []))
        except Exception:
            pass

        if len(tickers) < 250:
            for endpoint in ["gainers", "losers"]:
                snap_url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/{endpoint}"
                snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY, "limit": 250})

                if snap_resp.status_code != 200:
                    if snap_resp.status_code == 403:
                        print(f"[Warning] 403 Forbidden on {endpoint} endpoint - check API plan")
                    continue
                tickers.extend(snap_resp.json().get("tickers", []))

        common_stock_universe, common_stock_source = _load_common_stock_universe()
        seen_symbols = set()
        for t in tickers:
                try:
                    symbol = str(t.get("ticker", "") or "").upper().strip()
                    if not symbol or symbol in seen_symbols:
                        continue
                    seen_symbols.add(symbol)
                    if _stock_alert_asset_exclusion_reason(
                        symbol,
                        common_stock_universe=common_stock_universe,
                        universe_source=common_stock_source,
                        require_reference=common_stock_universe is None,
                    ):
                        continue
                    day = t.get("day", {})
                    prev = t.get("prevDay", {})

                    price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
                    if not price or price < 2:
                        continue

                    vol = day.get("v", 0)
                    prev_vol = prev.get("v", 0)

                    # Fix 2a: RVOL Baseline — use prevDay volume as quick baseline
                    # Snapshot only has day + prevDay; 20-day median would need extra API call per ticker
                    # For gainers/losers bulk scan, prevDay is acceptable (close enough to median for high-vol stocks)
                    if prev_vol > 0:
                        rvol = vol / prev_vol
                    else:
                        continue

                    if rvol > 3.0:
                        prev_close = prev.get("c", 0)
                        chg = ((price - prev_close) / prev_close * 100) if prev_close else 0

                        # Fix 2b: Dollar Volume Minimum
                        dollar_volume = price * vol
                        if dollar_volume < 1_000_000:
                            continue

                        # Fix 2c: Breakout vs Absorption
                        price_change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        if abs(price_change_pct) > 2:
                            signal_type = "BREAKOUT" if price_change_pct > 0 else "BREAKDOWN"
                        elif abs(price_change_pct) < 0.5:
                            signal_type = "ABSORPTION"  # High volume, no price move = accumulation/distribution
                        else:
                            signal_type = "NORMAL"

                        spikes.append({
                            "ticker": symbol,
                            "price": round(price, 2),
                            "change_pct": round(chg, 2),
                            "volume": vol,
                            "rvol": round(rvol, 2),
                            "dollar_volume": round(dollar_volume, 0),
                            "signal_type": signal_type,
                            "asset_class": "stock",
                            "trade_signal": "BEOBACHTEN",
                            "trade_action": "BEOBACHTEN",
                            "signal_label": "Achtung beobachten: Aktien-Volume-Spike, kein Crypto-Signal",
                            "execution_trigger_ok": False,
                        })
                except Exception as e:
                    print(f"[Warning] Error processing volume spike for ticker: {e}")
                    continue

        # Sort by RVOL descending
        spikes.sort(key=lambda x: x.get("rvol", 0), reverse=True)
        save_cache_file(VOLUME_SPIKES_CACHE, spikes[:50])  # Keep top 50
    except Exception as e:
        print(f"Volume spikes error: {e}")


@app.post("/api/volume-spikes-scan")
def trigger_volume_spikes():
    """Trigger volume spikes scanner."""
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    _run_scan_safe("volume_spikes", _volume_spikes_wrapper)
    return {"status": "started", "message": "Volume Spikes scan started"}


@app.get("/api/volume-spikes-results")
def get_volume_spikes():
    """Get cached volume spikes results."""
    results, cached_at = load_cache_file(VOLUME_SPIKES_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")
    decorated = _decorate_scan_results(results, "volume_spikes", cache_age)
    decorated = _apply_signal_only_policy("volume_spikes", decorated)
    quality = _scan_quality_payload("volume_spikes", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


# ── ORB Scanner (Opening Range Breakout) ──
ORB_CACHE = "/tmp/orb_scan_results.json"

def _orb_scanner_wrapper() -> None:
    """
    ORB Scanner V2 — Professional Grade Opening Range Breakout
    Basiert auf Mark Fisher ACD + Toby Crabel Methodik.
    Läuft nur Mo-Fr 9:45-11:00 ET (alle 5 Min).

    Verbesserungen V2:
    - Volume Confirmation auf Breakout-Candle (verhindert ~40% false positives)
    - Entry/Stop/Target Levels für jedes Setup
    - OR-Size vs ATR Check (zu weite OR = schlechtes R:R)
    - Failed Breakout Detection (Crabel Reversal Setups)
    - Scoring/Grading System (S/A/B/C)
    - Verbesserte RVOL Berechnung (U-Shape Intraday Curve)
    """
    try:
        from zoneinfo import ZoneInfo
        et_tz = ZoneInfo("US/Eastern")
        now_et = datetime.now(et_tz)
        hour, minute = now_et.hour, now_et.minute
        time_val = hour * 60 + minute
        weekday = now_et.weekday()

        # Prime ORB: 9:45-11:00 ET. Manual late review is allowed until close,
        # but late results are capped and labelled because the classic edge decays.
        if weekday >= 5 or time_val < ORB_START_MINUTE or time_val >= ORB_SCAN_END_MINUTE:
            print(f"[ORB] Außerhalb Fenster ({now_et.strftime('%H:%M')} ET, {'Mo-Fr' if weekday < 5 else 'Wochenende'}) — übersprungen")
            # Trotzdem Cache mit Phase-Info speichern, damit Frontend Feedback gibt
            _phase = "weekend" if weekday >= 5 else "pre_open" if time_val < ORB_START_MINUTE else "expired"
            save_cache_file(ORB_CACHE, [{"breakouts": [], "failed_breakouts": [], "candidates": [],
                "stats": {"scanned": 0, "candidates": 0, "breakouts": 0, "failed": 0},
                "or_phase": _phase, "market_time": now_et.strftime("%H:%M ET")}])
            return

        is_late_orb_session = time_val > ORB_PRIMARY_END_MINUTE
        orb_phase = "late_review" if is_late_orb_session else "active"
        session_quality = "late_review" if is_late_orb_session else "prime"

        print(f"[ORB] Scanner V2 gestartet ({now_et.strftime('%H:%M')} ET, {session_quality})...")

        today_str = now_et.strftime("%Y-%m-%d")

        prev_data = None
        prev_trade_date = None
        for lookback in range(1, 9):
            candidate_day = now_et - timedelta(days=lookback)
            if candidate_day.weekday() >= 5:
                continue
            candidate_str = candidate_day.strftime("%Y-%m-%d")
            prev_data = fetch_grouped_daily(POLYGON_KEY, candidate_str)
            if prev_data:
                prev_trade_date = candidate_str
                break
        if not prev_data:
            print("[ORB] Keine Vortages-Daten")
            return
        print(f"[ORB] Referenz-Tag: {prev_trade_date}")

        # V2.8: Snapshot API statt fetch_grouped_daily für heutige Daten
        # fetch_grouped_daily liefert während Handelszeit KEINE Daten (nur nach Börsenschluss)
        # Snapshot API liefert Live-Intraday-Daten
        today_data = {}
        try:
            snap_resp = rate_limited_get(
                "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"apiKey": POLYGON_KEY}, timeout=30
            )
            if snap_resp.status_code == 200:
                for t in snap_resp.json().get("tickers", []):
                    sym = str(t.get("ticker", "") or "").upper().strip()
                    day = t.get("day", {}) or {}
                    lt = t.get("lastTrade", {}) or {}
                    if day.get("o"):
                        today_data[sym] = {
                            "o": day.get("o", 0),
                            "h": day.get("h", 0),
                            "l": day.get("l", 0),
                            "c": day.get("c", 0) or lt.get("p", 0),
                            "v": day.get("v", 0),
                        }
                print(f"[ORB] Snapshot: {len(today_data)} Ticker mit Intraday-Daten")
            else:
                print(f"[ORB] Snapshot HTTP {snap_resp.status_code} — Fallback auf grouped daily")
                today_data_raw = fetch_grouped_daily(POLYGON_KEY, today_str)
                if today_data_raw:
                    today_data = today_data_raw
        except Exception as e:
            print(f"[ORB] Snapshot Fehler: {e} — Fallback auf grouped daily")
            today_data_raw = fetch_grouped_daily(POLYGON_KEY, today_str)
            if today_data_raw:
                today_data = today_data_raw

        mins_since_open = max(1, time_val - 570)  # 570 = 9:30
        total_market_mins = 390

        # ── Verbesserte RVOL: U-Shape Intraday Volume Curve ──
        # Reale Markt-Microstructure: hohes Vol am Open/Close, niedrig Mittags
        def _intraday_evf(mins_open):
            """Expected Volume Fraction — U-Shape Modell."""
            if mins_open <= 5:
                return 0.08 * (mins_open / 5)    # Erste 5 Min = 8% des Tagesvolumens
            elif mins_open <= 15:
                return 0.08 + 0.07 * ((mins_open - 5) / 10)   # 15 Min = 15%
            elif mins_open <= 30:
                return 0.15 + 0.07 * ((mins_open - 15) / 15)  # 30 Min = 22%
            elif mins_open <= 60:
                return 0.22 + 0.08 * ((mins_open - 30) / 30)  # 1h = 30%
            elif mins_open <= 120:
                return 0.30 + 0.10 * ((mins_open - 60) / 60)  # 2h = 40%
            else:
                return 0.40 + 0.60 * ((mins_open - 120) / max(1, total_market_mins - 120))

        evf = max(0.01, _intraday_evf(mins_since_open))

        # ── Kandidaten filtern ──
        candidates = []
        for ticker, prev in prev_data.items():
            ticker = str(ticker or "").upper().strip()
            if len(ticker) > 5 or "." in ticker:
                continue
            non_stock_reason = _looks_like_non_stock_etp_symbol(ticker)
            if non_stock_reason:
                continue
            prev_close = prev.get("c", 0)
            if prev_close < 5 or prev_close > 2000:
                continue
            prev_vol = prev.get("v", 0)
            if prev_vol < 500000:
                continue
            # ATR Proxy aus Vortag (High-Low / Close)
            prev_atr_pct = (prev.get("h", 0) - prev.get("l", 0)) / prev_close * 100 if prev_close > 0 else 0

            today = today_data.get(ticker, {}) if today_data else {}
            today_open = today.get("o", 0)
            today_vol = today.get("v", 0)
            today_high = today.get("h", 0)
            today_low = today.get("l", 0)
            today_close = today.get("c", 0)

            if today_open <= 0:
                continue

            gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close > 0 else 0
            rvol = today_vol / (prev_vol * evf) if prev_vol * evf > 0 else 0

            if abs(gap_pct) < 1.5 and rvol < 1.3:
                continue

            candidates.append({
                "ticker": ticker, "prev_close": round(prev_close, 2),
                "open": round(today_open, 2), "current": round(today_close or today_open, 2),
                "high": round(today_high, 2), "low": round(today_low, 2),
                "gap_pct": round(gap_pct, 2), "rvol": round(rvol, 2), "volume": today_vol,
                "prev_atr_pct": round(prev_atr_pct, 2), "prev_vol": prev_vol,
            })

        candidates.sort(key=lambda x: abs(x["gap_pct"]) * 0.5 + min(x["rvol"], 5) * 0.3 + min(abs(x["prev_atr_pct"]), 5) * 0.2, reverse=True)
        stock_candidates = []
        non_stock_excluded = []
        for cand in candidates[:120]:
            is_stock, reason = _is_orb_common_stock_candidate(cand["ticker"])
            if is_stock:
                cand["asset_check"] = reason
                stock_candidates.append(cand)
            else:
                non_stock_excluded.append({"ticker": cand["ticker"], "reason": reason})
                print(f"[ORB] Exclude non-stock {cand['ticker']}: {reason}")
            if len(stock_candidates) >= 50:
                break
        candidates = stock_candidates

        # ── 5-Min Candles für Breakout Detection ──
        market_open_ms = int(now_et.replace(hour=9, minute=30, second=0, microsecond=0).timestamp() * 1000)
        or_end_ms = int(now_et.replace(hour=9, minute=45, second=0, microsecond=0).timestamp() * 1000)
        breakouts = []
        failed_breakouts = []

        # V3.0 Debug-Counters: Wo gehen Kandidaten verloren?
        _dbg = {"api_fail": 0, "no_bars": 0, "no_rth": 0, "no_or": 0, "or_wide": 0, "or_narrow": 0, "in_range": 0, "failed": 0, "passed": 0, "non_stock": len(non_stock_excluded)}

        for cand in candidates:
            t = cand["ticker"]
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{t}/range/5/minute/{today_str}/{today_str}"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "adjusted": "true", "sort": "asc", "limit": 50000}, timeout=10)
                if resp.status_code != 200:
                    _dbg["api_fail"] += 1
                    continue
                bars = resp.json().get("results", [])
                if not bars or len(bars) < 2:
                    _dbg["no_bars"] += 1
                    continue
                bars = [b for b in bars if b.get("t", 0) >= market_open_ms]
                if len(bars) < 2:
                    _dbg["no_rth"] += 1
                    continue

                # ── Opening Range bestimmen (9:30-9:45 = erste 3 5-Min Candles) ──
                or_bars = [b for b in bars if b.get("t", 0) < or_end_ms]
                if not or_bars or len(or_bars) < 3:
                    _dbg["no_or"] += 1
                    continue
                or_high = max(b.get("h", 0) for b in or_bars)
                or_low = min(b.get("l", 999999) for b in or_bars)
                or_size = or_high - or_low
                or_size_pct = (or_size / or_low * 100) if or_low > 0 else 0
                atr_pct, atr_model = _fetch_orb_atr_pct(t, now_et, cand.get("prev_atr_pct", 0))

                # ── OR-Size vs ATR Check ──
                # OR > 2x ATR = zu volatil für ORB (schlechtes R:R, Mark Fisher Regel)
                prev_atr_dollar = cand["prev_close"] * atr_pct / 100
                if prev_atr_dollar > 0 and or_size > prev_atr_dollar * 2.0:
                    _dbg["or_wide"] += 1
                    continue

                # OR zu eng = kein echtes Setup (< 0.3% = noise)
                if or_size_pct < 0.3:
                    _dbg["or_narrow"] += 1
                    continue

                # ── VWAP Berechnung ──
                total_vwap_num = sum((b.get("h",0)+b.get("l",0)+b.get("c",0))/3 * b.get("v",0) for b in bars)
                total_vol = sum(b.get("v", 0) for b in bars)
                vwap = total_vwap_num / total_vol if total_vol > 0 else (or_high + or_low) / 2

                # ── OR Volume (für Volume-Confirmation) ──
                or_avg_vol = sum(b.get("v", 0) for b in or_bars) / len(or_bars) if or_bars else 0

                current_price = bars[-1].get("c", 0)
                post_or = [b for b in bars if b.get("t", 0) >= or_end_ms]
                latest_bar = bars[-1] if bars else {}
                latest_open = float(latest_bar.get("o", current_price) or current_price or 0)
                latest_high = float(latest_bar.get("h", current_price) or current_price or 0)
                latest_low = float(latest_bar.get("l", current_price) or current_price or 0)
                latest_close = float(latest_bar.get("c", current_price) or current_price or 0)
                latest_range = max(latest_high - latest_low, 1e-9)
                latest_close_pos = max(0.0, min(1.0, (latest_close - latest_low) / latest_range))
                latest_body_top = max(latest_open, latest_close)
                latest_body_bottom = min(latest_open, latest_close)
                latest_upper_wick_pct = max(0.0, latest_high - latest_body_top) / latest_range * 100
                latest_lower_wick_pct = max(0.0, latest_body_bottom - latest_low) / latest_range * 100

                # ══════════════════════════════════════════════════════
                # V3.0 REWRITE: Current-State Breakout Detection
                # Alte Logik suchte den ERSTEN Ausbruch → Pullback = tot.
                # Neue Logik: Wo ist der Preis JETZT relativ zu OR?
                # ══════════════════════════════════════════════════════
                breakout_dir = None
                breakout_confirmed = False
                breakout_bar_vol = 0
                or_mid = (or_high + or_low) / 2
                breakout_state = "in_range"
                volume_scope = "none"

                # Zähle Bars über/unter OR
                bars_above = sum(1 for b in post_or if b.get("c", 0) > or_high)
                bars_below = sum(1 for b in post_or if b.get("c", 0) < or_low)
                bars_inside = len(post_or) - bars_above - bars_below
                recent_post_or = post_or[-3:] if post_or else []
                recent_above = sum(1 for b in recent_post_or if b.get("c", 0) > or_high)
                recent_below = sum(1 for b in recent_post_or if b.get("c", 0) < or_low)

                # ── Schritt 1: Aktueller Preis bestimmt Richtung ──
                if current_price > or_high:
                    breakout_dir = "LONG"
                    breakout_state = "active_breakout"
                elif current_price < or_low:
                    breakout_dir = "SHORT"
                    breakout_state = "active_breakout"

                # ── Schritt 2: Volume Confirmation — gab es einen Bar mit Volume? ──
                if breakout_dir:
                    recent_relevant_bars = []
                    for b in post_or[-3:]:
                        bc = b.get("c", 0)
                        if breakout_dir == "LONG" and bc > or_high:
                            recent_relevant_bars.append(b)
                        elif breakout_dir == "SHORT" and bc < or_low:
                            recent_relevant_bars.append(b)
                    if recent_relevant_bars:
                        breakout_bar_vol = recent_relevant_bars[-1].get("v", 0)
                        volume_scope = "latest_3_post_or"
                    if or_avg_vol > 0 and breakout_bar_vol >= or_avg_vol * 0.8:
                        breakout_confirmed = True

                # ── Schritt 3: Failed Breakout — Preis DEUTLICH zurück in OR ──
                if not breakout_dir and (bars_above >= 1 or bars_below >= 1):
                    # Es GAB einen Ausbruch, aber Preis ist zurück in OR
                    if bars_above >= 1 and current_price < or_mid:
                        failed_breakouts.append({
                            **cand,
                            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                            "or_size_pct": round(or_size_pct, 2),
                            "vwap": round(vwap, 2), "direction": "FAILED_LONG→SHORT",
                            "current_price": round(current_price, 2),
                            "entry": round(or_low - or_size * 0.05, 2),
                            "stop": round(or_high + or_size * 0.15, 2),
                            "target": round(or_low - or_size * 0.8, 2),
                        })
                    elif bars_below >= 1 and current_price > or_mid:
                        failed_breakouts.append({
                            **cand,
                            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                            "or_size_pct": round(or_size_pct, 2),
                            "vwap": round(vwap, 2), "direction": "FAILED_SHORT→LONG",
                            "current_price": round(current_price, 2),
                            "entry": round(or_high + or_size * 0.05, 2),
                            "stop": round(or_low - or_size * 0.15, 2),
                            "target": round(or_high + or_size * 0.8, 2),
                        })

                if not breakout_dir:
                    if bars_above >= 1 or bars_below >= 1:
                        _dbg["failed"] += 1
                    else:
                        _dbg["in_range"] += 1
                        # V3.0: Log die Top-5 "in range" Kandidaten für Debugging
                        if _dbg["in_range"] <= 5:
                            print(f"[ORB DBG] {t} IN RANGE: price={current_price:.2f} OR=[{or_low:.2f}-{or_high:.2f}] above={bars_above} below={bars_below} post_or={len(post_or)}")
                    continue

                _dbg["passed"] += 1
                print(f"[ORB DBG] {t} BREAKOUT {breakout_dir}: price={current_price:.2f} OR=[{or_low:.2f}-{or_high:.2f}] above={bars_above} below={bars_below} vol_confirmed={breakout_confirmed}")

                # ── Entry / Stop / Target Levels ──
                # Tactical ORB stop near OR midpoint gives a realistic tradeable R:R.
                # The opposite side of the OR remains the "hard invalidation" reference.
                if breakout_dir == "LONG":
                    entry = round(or_high + or_size * 0.02, 2)   # Knapp über OR High
                    stop = round(or_mid - or_size * 0.05, 2)
                    invalidation_stop = round(or_low - or_size * 0.10, 2)
                    target1 = round(or_high + or_size * 1.0, 2)  # 1x OR Size
                    target2 = round(or_high + or_size * 1.5, 2)  # 1.5x OR Size
                    risk = entry - stop
                    reward_blended = 0.5 * (target1 - entry) + 0.5 * (target2 - entry)
                    distance_to_entry_r = (current_price - entry) / risk if risk > 0 else 0
                    late_to_tp1 = current_price >= target1
                else:
                    entry = round(or_low - or_size * 0.02, 2)
                    stop = round(or_mid + or_size * 0.05, 2)
                    invalidation_stop = round(or_high + or_size * 0.10, 2)
                    target1 = round(or_low - or_size * 1.0, 2)
                    target2 = round(or_low - or_size * 1.5, 2)
                    risk = stop - entry
                    reward_blended = 0.5 * (entry - target1) + 0.5 * (entry - target2)
                    distance_to_entry_r = (entry - current_price) / risk if risk > 0 else 0
                    late_to_tp1 = current_price <= target1
                rr_ratio = round(reward_blended / risk, 2) if risk > 0 else 0
                if breakout_dir == "LONG":
                    live_entry = max(float(current_price), float(entry))
                    live_risk = live_entry - stop
                    live_reward_blended = 0.5 * (target1 - live_entry) + 0.5 * (target2 - live_entry)
                else:
                    live_entry = min(float(current_price), float(entry))
                    live_risk = stop - live_entry
                    live_reward_blended = 0.5 * (live_entry - target1) + 0.5 * (live_entry - target2)
                live_rr_ratio = round(max(0, live_reward_blended) / live_risk, 2) if live_risk > 0 else 0
                rr_for_score = min(rr_ratio, live_rr_ratio) if live_rr_ratio > 0 else 0

                if late_to_tp1 or distance_to_entry_r >= 1.0 or live_rr_ratio < 1.0:
                    entry_quality = "CHASE"
                    entry_quality_score = 25
                elif distance_to_entry_r > 0.75 or live_rr_ratio < 1.5:
                    entry_quality = "LATE"
                    entry_quality_score = 45
                elif distance_to_entry_r > 0.35 or live_rr_ratio < 2.0:
                    entry_quality = "EXTENDED"
                    entry_quality_score = 70
                else:
                    entry_quality = "GOOD"
                    entry_quality_score = 90

                # V2.8: R:R nur Info-Spalte, kein Hard-Filter mehr

                # ── VWAP Alignment ──
                vwap_aligned = (breakout_dir == "LONG" and current_price > vwap) or \
                               (breakout_dir == "SHORT" and current_price < vwap)

                # ── Scoring System (0-100) ──
                score = 0
                score_details = []

                # 1. Volume Confirmation (0-25 Punkte)
                if breakout_confirmed:
                    vol_ratio = breakout_bar_vol / or_avg_vol if or_avg_vol > 0 else 0
                    if vol_ratio >= 2.0:
                        score += 25
                        score_details.append("Vol 2x+ ✓")
                    elif vol_ratio >= 1.5:
                        score += 20
                        score_details.append("Vol 1.5x ✓")
                    else:
                        score += 12
                        score_details.append("Vol OK")
                else:
                    score += 5
                    score_details.append("Vol ✗")

                # 2. RVOL (0-20 Punkte)
                _rvol = cand["rvol"]
                if _rvol >= 3.0:
                    score += 20
                    score_details.append(f"RVOL {_rvol:.1f}x ✓✓")
                elif _rvol >= 2.0:
                    score += 15
                    score_details.append(f"RVOL {_rvol:.1f}x ✓")
                elif _rvol >= 1.5:
                    score += 10
                    score_details.append(f"RVOL {_rvol:.1f}x")
                else:
                    score += 5

                # 3. Gap Quality (0-15 Punkte) — Richtung muss zum Breakout passen.
                _gap_raw = cand["gap_pct"]
                _gap = abs(_gap_raw)
                _gap_aligned = (breakout_dir == "LONG" and _gap_raw >= 0) or (breakout_dir == "SHORT" and _gap_raw <= 0)
                if _gap_aligned and 2.0 <= _gap <= 5.0:
                    score += 15  # Sweet Spot
                    score_details.append(f"Gap {cand['gap_pct']:+.1f}% ✓")
                elif _gap_aligned and _gap > 5.0:
                    score += 8   # Zu groß, höheres Reversal-Risiko
                    score_details.append(f"Gap {cand['gap_pct']:+.1f}% (weit)")
                elif _gap_aligned and _gap >= 1.5:
                    score += 10
                    score_details.append(f"Gap {cand['gap_pct']:+.1f}%")
                elif _gap >= 1.5:
                    score += 4
                    score_details.append(f"Counter-Gap {cand['gap_pct']:+.1f}%")
                else:
                    score += 5

                # 4. OR Size Quality (0-15 Punkte)
                # Ideale OR: 0.5-1.5% des Preises
                if 0.5 <= or_size_pct <= 1.5:
                    score += 15
                    score_details.append(f"OR {or_size_pct:.1f}% ✓")
                elif or_size_pct < 0.5:
                    score += 8   # Eng — könnte Noise-Breakout sein
                    score_details.append(f"OR {or_size_pct:.1f}% (eng)")
                else:
                    score += 8
                    score_details.append(f"OR {or_size_pct:.1f}% (weit)")

                # 5. VWAP Alignment (0-10 Punkte)
                if vwap_aligned:
                    score += 10
                    score_details.append("VWAP ✓")
                else:
                    score += 3
                    score_details.append("VWAP ✗")

                # 6. R:R Ratio (0-10 Punkte)
                if rr_for_score >= 2.5:
                    score += 10
                    score_details.append(f"Live R:R {live_rr_ratio:.1f} ✓✓")
                elif rr_for_score >= 2.0:
                    score += 8
                    score_details.append(f"Live R:R {live_rr_ratio:.1f} ✓")
                elif rr_for_score >= 1.5:
                    score += 6
                    score_details.append(f"Live R:R {live_rr_ratio:.1f}")
                else:
                    score += 3

                # 7. Holding Strength (0-5 Punkte)
                hold_bars = bars_above if breakout_dir == "LONG" else bars_below
                recent_hold_bars = recent_above if breakout_dir == "LONG" else recent_below
                total_post = len(post_or) if post_or else 1
                total_recent_post = len(recent_post_or) if recent_post_or else 1
                hold_pct = hold_bars / total_post
                recent_hold_pct = recent_hold_bars / total_recent_post
                if hold_pct >= 0.8:
                    score += 5
                    score_details.append("Hold ✓")
                elif hold_pct >= 0.6:
                    score += 3

                # No top-grade without live volume confirmation; no chase after TP1.
                if not breakout_confirmed:
                    score = min(score, 64)
                    score_details.append("Cap: Vol unconfirmed")
                if late_to_tp1:
                    score = min(score, 54)
                    score_details.append("Late: TP1 already reached")
                elif distance_to_entry_r > 0.75:
                    score = min(score, 69)
                    score_details.append(f"Late: {distance_to_entry_r:.1f}R from entry")
                if entry_quality == "CHASE":
                    score = min(score, 49)
                    score_details.append("Entry CHASE")
                elif entry_quality == "LATE":
                    score = min(score, 62)
                    score_details.append("Entry LATE")
                elif entry_quality == "EXTENDED":
                    score = min(score, 76)
                    score_details.append("Entry EXTENDED")
                else:
                    score_details.append("Entry GOOD")
                if is_late_orb_session:
                    score = min(score, 54 if time_val >= 12 * 60 else 69)
                    score_details.append("Late-session ORB cap")

                # ── Grading ──
                if score >= 85:
                    grade = "S"
                elif score >= 70:
                    grade = "A"
                elif score >= 55:
                    grade = "B"
                else:
                    grade = "C"

                breakouts.append({
                    **cand,
                    "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                    "or_size_pct": round(or_size_pct, 2),
                    "atr_pct": round(atr_pct, 2),
                    "atr_model": atr_model,
                    "vwap": round(vwap, 2), "direction": breakout_dir,
                    "breakout_state": breakout_state,
                    "late_session": is_late_orb_session,
                    "session_quality": session_quality,
                    "current_price": round(current_price, 2),
                    "close_pos": round(latest_close_pos, 3),
                    "upper_wick_pct": round(latest_upper_wick_pct, 1),
                    "lower_wick_pct": round(latest_lower_wick_pct, 1),
                    "entry": entry, "live_entry": round(live_entry, 2), "stop": stop,
                    "invalidation_stop": invalidation_stop,
                    "target1": target1, "target2": target2,
                    "rr_ratio": rr_ratio,
                    "live_rr_ratio": live_rr_ratio,
                    "rr_model": "50/50 TP1/TP2",
                    "distance_to_entry_r": round(distance_to_entry_r, 2),
                    "entry_quality": entry_quality,
                    "entry_quality_score": entry_quality_score,
                    "late_to_tp1": late_to_tp1,
                    "vol_confirmed": breakout_confirmed,
                    "hold_pct": round(hold_pct, 3),
                    "recent_hold_pct": round(recent_hold_pct, 3),
                    "volume_scope": volume_scope,
                    "vwap_aligned": vwap_aligned,
                    "score": score, "grade": grade,
                    "score_details": " | ".join(score_details),
                })
            except Exception as orb_item_err:
                print(f"[ORB] Skip {t}: {orb_item_err}")
                continue

        # Sortiere nach Score
        breakouts.sort(key=lambda x: x.get("score", 0), reverse=True)

        result = {
            "breakouts": breakouts,
            "failed_breakouts": failed_breakouts[:10],
            "candidates": candidates[:20],
            "stats": {
                "scanned": len(prev_data), "candidates": len(candidates),
                "breakouts": len(breakouts), "failed": len(failed_breakouts),
                "debug": _dbg,
                "excluded_non_stocks": non_stock_excluded[:25],
            },
            "or_phase": orb_phase,
            "session_quality": session_quality,
            "market_time": now_et.strftime("%H:%M ET"),
        }
        save_cache_file(ORB_CACHE, [result])
        print(f"[ORB] V3.0 Fertig: {len(breakouts)} Breakouts, {len(failed_breakouts)} Failed (von {len(candidates)} Kandidaten)")
        print(f"[ORB] Filter-Stats: {_dbg}")

        # ── Alert bei Grade S/A Breakouts ──
        alert_breakouts = [b for b in breakouts if b.get("grade") in ("S", "A")]
        _alert_now = time.time()
        _fresh_alert_breakouts = []
        for _b in alert_breakouts:
            _state = _classify_alert_candidate("orb", _b, _alert_now)
            if _state["alertable_now"]:
                _EMAIL_COOLDOWN[_state["cooldown_key"]] = _alert_now
                _fresh_alert_breakouts.append(_b)
        alert_breakouts = _fresh_alert_breakouts
        if alert_breakouts:
            rows = ""
            for b in alert_breakouts:
                emoji = "🏆" if b["grade"] == "S" else ("⬆️" if b["direction"] == "LONG" else "⬇️")
                vol_icon = "🔊" if b.get("vol_confirmed") else "🔇"
                rows += f'<tr><td style="padding:8px;border-bottom:1px solid #eee"><b>{b["ticker"]}</b></td>'
                rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{emoji} {b["direction"]} ({b["grade"]})</td>'
                rows += f'<td style="padding:8px;border-bottom:1px solid #eee">${b["current_price"]}</td>'
                rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{b["gap_pct"]:+.1f}%</td>'
                rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{b["rvol"]:.1f}x {vol_icon}</td>'
                rows += (
                    f'<td style="padding:8px;border-bottom:1px solid #eee">'
                    f'Entry ${b["entry"]}<br>Stop <span style="color:#dc2626">${b["stop"]}</span><br>'
                    f'TP1/TP2 <span style="color:#059669">${b["target1"]} / ${b["target2"]}</span>'
                    f'</td></tr>'
                )
            body = f'''<html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
            <h2 style="color:#1a73e8">🔔 ORB Breakouts — {now_et.strftime("%H:%M")} ET</h2>
            <p style="color:#666">{len(alert_breakouts)} Top-Setups (Grade S/A)</p>
            <table style="width:100%;border-collapse:collapse;font-size:13px">
            <tr style="background:#f5f5f5"><th style="padding:8px;text-align:left">Ticker</th>
            <th style="padding:8px;text-align:left">Setup</th><th style="padding:8px;text-align:left">Preis</th>
            <th style="padding:8px;text-align:left">Gap</th><th style="padding:8px;text-align:left">RVOL</th>
            <th style="padding:8px;text-align:left">Entry / Stop / TP</th></tr>
            {rows}</table>
            <p style="color:#999;font-size:11px;margin-top:15px">ORB V2 — Volume Confirmed | VWAP Aligned | R:R optimiert</p>
            </body></html>'''
            _send_email_alert(f"🔔 {len(alert_breakouts)} ORB Breakouts (Grade {alert_breakouts[0]['grade']}+)", body)

    except Exception as e:
        print(f"[ORB] Fehler: {e}")
        import traceback
        traceback.print_exc()


@app.post("/api/orb-scan")
def trigger_orb_scan():
    """Trigger ORB Scanner (Opening Range Breakout) — nur aktiv 9:45-11:00 ET Mo-Fr."""
    _run_scan_safe("orb", _orb_scanner_wrapper)
    return {"status": "started", "message": "ORB scan triggered"}


@app.get("/api/orb-results")
def get_orb_results():
    """Get cached ORB scan results."""
    results, cached_at = load_cache_file(ORB_CACHE)
    cache_age = None
    if cached_at:
        try:
            cache_age = int((datetime.now() - datetime.fromisoformat(cached_at)).total_seconds())
        except Exception:
            pass
    decorated = _decorate_orb_results(results, cache_age)
    quality = _scan_quality_payload("orb", cache_age, decorated)
    return {"status": "success", "data": decorated, "cached_at": cached_at, "cache_age_seconds": cache_age, "data_quality": quality, "warnings": quality["warnings"], "exclusion_policy": quality["exclusion_policy"]}


# ── Fear & Greed Score (CNN-kompatibel, 0-100) ──
# V2.5: Komplett neu — Skala wie CNN: 0 = Extreme Fear, 100 = Extreme Greed
# Verwendet 7 Faktoren analog zu CNN, basierend auf verfügbaren Daten
def _calculate_fear_score(vix_data: Dict, breadth_data: Dict, indices_data: List[Dict]) -> tuple[int, str, Dict]:
    """
    Calculate Fear & Greed score compatible with CNN scale.
    0 = Extreme Fear, 25 = Fear, 50 = Neutral, 75 = Greed, 100 = Extreme Greed
    Each factor produces a sub-score 0-100, final score is weighted average.
    """
    factors = {}
    weights = {}

    def get_index_data(ticker):
        for idx in indices_data:
            if idx.get("ticker") == ticker:
                return idx
        return None

    spy_data = get_index_data("SPY")
    qqq_data = get_index_data("QQQ")
    iwm_data = get_index_data("IWM")

    # ── Factor 1: VIX Level (Market Volatility) — Weight 20 ──
    # VIX < 12 = 100 (Extreme Greed), 12-15 = 75, 15-20 = 50, 20-25 = 25, 25-30 = 10, >30 = 0
    if vix_data and "price" in vix_data:
        vix = vix_data["price"]
        if vix <= 12:
            factors["VIX Level"] = 100
        elif vix <= 15:
            factors["VIX Level"] = 75 - (vix - 12) / 3 * 25  # 75→50
        elif vix <= 20:
            factors["VIX Level"] = 50 - (vix - 15) / 5 * 25  # 50→25
        elif vix <= 25:
            factors["VIX Level"] = 25 - (vix - 20) / 5 * 15  # 25→10
        elif vix <= 35:
            factors["VIX Level"] = 10 - (vix - 25) / 10 * 10  # 10→0
        else:
            factors["VIX Level"] = 0
        weights["VIX Level"] = 20

    # ── Factor 2: Market Momentum (S&P 500 vs 20D) — Weight 20 ──
    # SPY 20D change: > +5% = 100, +2-5% = 75, 0-2% = 55, -2-0% = 40, -5 to -2% = 20, < -5% = 0
    if spy_data and "change_20d" in spy_data:
        chg20 = spy_data["change_20d"]
        if chg20 >= 5:
            factors["Momentum"] = 100
        elif chg20 >= 2:
            factors["Momentum"] = 75 + (chg20 - 2) / 3 * 25
        elif chg20 >= 0:
            factors["Momentum"] = 50 + chg20 / 2 * 25
        elif chg20 >= -2:
            factors["Momentum"] = 30 + (chg20 + 2) / 2 * 20
        elif chg20 >= -5:
            factors["Momentum"] = 10 + (chg20 + 5) / 3 * 20
        elif chg20 >= -10:
            factors["Momentum"] = (chg20 + 10) / 5 * 10
        else:
            factors["Momentum"] = 0
        weights["Momentum"] = 20

    # ── Factor 3: Market Breadth (A/D Ratio) — Weight 15 ──
    # A/D > 2.0 = 100, 1.5 = 80, 1.0 = 50, 0.7 = 30, 0.5 = 15, < 0.3 = 0
    if breadth_data and "ad_ratio" in breadth_data:
        ad = breadth_data["ad_ratio"]
        if ad >= 2.0:
            factors["Breadth"] = 100
        elif ad >= 1.5:
            factors["Breadth"] = 80 + (ad - 1.5) / 0.5 * 20
        elif ad >= 1.0:
            factors["Breadth"] = 50 + (ad - 1.0) / 0.5 * 30
        elif ad >= 0.7:
            factors["Breadth"] = 30 + (ad - 0.7) / 0.3 * 20
        elif ad >= 0.3:
            factors["Breadth"] = (ad - 0.3) / 0.4 * 30
        else:
            factors["Breadth"] = 0
        weights["Breadth"] = 15

    # ── Factor 4: Index 5D Performance (Proxy für Strength) — Weight 15 ──
    # Durchschnitt der 5D-Changes aller Indizes
    chg5_list = [idx.get("change_5d", 0) for idx in indices_data if "change_5d" in idx]
    if chg5_list:
        avg_5d = sum(chg5_list) / len(chg5_list)
        if avg_5d >= 3:
            factors["5D Strength"] = 100
        elif avg_5d >= 1:
            factors["5D Strength"] = 70 + avg_5d / 3 * 30
        elif avg_5d >= 0:
            factors["5D Strength"] = 50 + avg_5d * 20
        elif avg_5d >= -2:
            factors["5D Strength"] = 25 + (avg_5d + 2) / 2 * 25
        elif avg_5d >= -5:
            factors["5D Strength"] = 5 + (avg_5d + 5) / 3 * 20
        else:
            factors["5D Strength"] = max(0, (avg_5d + 10) / 5 * 5)  # Linear 0 bei -10, 5 bei -5
        weights["5D Strength"] = 15

    # ── Factor 5: Nasdaq vs S&P Divergenz (Risk Appetite Proxy) — Weight 10 ──
    # Wenn Nasdaq besser als S&P = Risk On, umgekehrt = Risk Off
    if qqq_data and spy_data:
        qqq_5d = qqq_data.get("change_5d", 0)
        spy_5d = spy_data.get("change_5d", 0)
        diff = qqq_5d - spy_5d  # Positiv = Tech outperforms = Risk On
        factors["Risk Appetite"] = max(0, min(100, 50 + diff * 10))
        weights["Risk Appetite"] = 10

    # ── Factor 6: Small Cap Strength (Russell vs S&P) — Weight 10 ──
    if iwm_data and spy_data:
        iwm_5d = iwm_data.get("change_5d", 0)
        spy_5d = spy_data.get("change_5d", 0)
        diff = iwm_5d - spy_5d  # Positiv = Small Caps outperform = bullish breadth
        factors["Small Cap"] = max(0, min(100, 50 + diff * 12))
        weights["Small Cap"] = 10

    # ── Factor 7: VIX Trend (5D Change) — Weight 10 ──
    # VIX sinkend = Greed, VIX steigend = Fear
    if vix_data and "change_5d" in vix_data:
        vix_chg = vix_data["change_5d"]  # Positiv = VIX gestiegen = mehr Fear
        if vix_chg <= -15:
            factors["VIX Trend"] = 100
        elif vix_chg <= -5:
            factors["VIX Trend"] = 75 + (-5 - vix_chg) / 10 * 25
        elif vix_chg <= 0:
            factors["VIX Trend"] = 50 + (-vix_chg) / 5 * 25
        elif vix_chg <= 10:
            factors["VIX Trend"] = 50 - vix_chg / 10 * 35
        elif vix_chg <= 25:
            factors["VIX Trend"] = 15 - (vix_chg - 10) / 15 * 15
        else:
            factors["VIX Trend"] = 0
        weights["VIX Trend"] = 10

    # ── Gewichteter Durchschnitt ──
    total_weight = sum(weights.values())
    if total_weight > 0:
        score = sum(factors.get(k, 50) * weights[k] for k in weights) / total_weight
    else:
        score = 50  # Neutral wenn keine Daten

    score = max(0, min(100, round(score)))

    # Details für Frontend
    details = {}
    for k, v in factors.items():
        details[k.lower().replace(" ", "_")] = round(v, 1)

    # Fear Level — CNN-kompatibel
    if score <= 20:
        fear_level = "EXTREME ANGST"
    elif score <= 40:
        fear_level = "ANGST"
    elif score <= 60:
        fear_level = "NEUTRAL"
    elif score <= 80:
        fear_level = "GIER"
    else:
        fear_level = "EXTREME GIER"

    return score, fear_level, details


# Update crash monitor to include fear score
def _crash_monitor_wrapper() -> None:
    """Fetch VIX, major indices, and market breadth data with fear score."""
    try:
        print("[Crash Monitor] Starting scan...")
        result = {"vix": {}, "indices": [], "breadth": {}, "fear_score": 0, "fear_level": ""}

        # Try VIX index first, fallback to UVXY ETF proxy
        vix_tickers = [
            ("SPY", "S&P 500", "S&P 500 ETF"),
            ("QQQ", "Nasdaq", "Nasdaq 100 ETF"),
            ("DIA", "Dow Jones", "Dow Jones ETF"),
            ("IWM", "Russell 2000", "Russell 2000 ETF"),
        ]

        # VIX via Polygon snapshot + historical for 5d/20d change
        try:
            vix_url = "https://api.polygon.io/v3/snapshot?ticker.any_of=I:VIX&apiKey=" + POLYGON_KEY
            vix_resp = rate_limited_get(vix_url, params={})
            if vix_resp.status_code == 200:
                vix_results = vix_resp.json().get("results", [])
                if vix_results:
                    vix_session = vix_results[0].get("session", {})
                    vix_price = vix_session.get("close", 0) or vix_session.get("previous_close", 0)
                    vix_prev = vix_session.get("previous_close", vix_price)
                    if vix_price > 0:
                        chg = ((vix_price - vix_prev) / vix_prev * 100) if vix_prev else 0
                        # V3.4 FIX: Historische VIX-Daten für 5d/20d Change holen
                        vix_5d = 0
                        vix_20d = 0
                        try:
                            from datetime import timedelta
                            _end = datetime.now().strftime("%Y-%m-%d")
                            _start = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
                            _vix_hist_url = f"https://api.polygon.io/v2/aggs/ticker/I:VIX/range/1/day/{_start}/{_end}"
                            _vix_hist = rate_limited_get(_vix_hist_url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
                            if _vix_hist.status_code == 200:
                                _vix_bars = _vix_hist.json().get("results", [])
                                if len(_vix_bars) >= 6:
                                    vix_5d = round(((vix_price - _vix_bars[5]["c"]) / _vix_bars[5]["c"] * 100), 2)
                                if len(_vix_bars) >= 21:
                                    vix_20d = round(((vix_price - _vix_bars[20]["c"]) / _vix_bars[20]["c"] * 100), 2)
                        except Exception as _vhist_err:
                            print(f"[Crash Monitor] VIX history error: {_vhist_err}")
                        result["vix"] = {"ticker": "I:VIX", "name": "VIX", "price": round(vix_price, 2),
                                         "change_1d": round(chg, 2), "change_5d": vix_5d, "change_20d": vix_20d}
                        if vix_price >= 30: result["vix"]["level"] = "EXTREME"
                        elif vix_price >= 25: result["vix"]["level"] = "HIGH"
                        elif vix_price >= 20: result["vix"]["level"] = "ELEVATED"
                        else: result["vix"]["level"] = "LOW"
                        print(f"[Crash Monitor] VIX from snapshot: {vix_price}")
            if not result.get("vix", {}).get("price"):
                # Fallback: Use UVXY daily change to estimate VIX level
                # UVXY is 1.5x leveraged VIX futures - absolute price is useless due to decay
                # Instead, use daily % change to estimate current VIX regime
                uvxy_url = f"https://api.polygon.io/v2/aggs/ticker/UVXY/range/1/day/2024-01-01/2099-12-31"
                uvxy_resp = rate_limited_get(uvxy_url, params={"apiKey": POLYGON_KEY, "limit": 10, "sort": "desc"})
                if uvxy_resp.status_code == 200:
                    bars = uvxy_resp.json().get("results", [])
                    if bars and len(bars) >= 2:
                        uvxy_today = bars[0]["c"]
                        uvxy_prev = bars[1]["c"]
                        uvxy_chg_pct = ((uvxy_today - uvxy_prev) / uvxy_prev * 100) if uvxy_prev else 0
                        # Calculate avg 5-day volatility from UVXY as VIX proxy
                        # Normal VIX ~15-18, elevated ~20-25, high ~25-30, extreme >30
                        # Estimate from UVXY behavior:
                        # - UVXY 5d avg abs change <3% → VIX ~14-16 (calm)
                        # - UVXY 5d avg abs change 3-8% → VIX ~17-22 (normal)
                        # - UVXY 5d avg abs change 8-15% → VIX ~22-28 (elevated)
                        # - UVXY 5d avg abs change >15% → VIX ~28+ (fear)
                        recent_changes = []
                        for i in range(min(5, len(bars) - 1)):
                            c1 = bars[i]["c"]
                            c2 = bars[i + 1]["c"]
                            if c2 > 0:
                                recent_changes.append(abs((c1 - c2) / c2 * 100))
                        avg_abs_change = sum(recent_changes) / len(recent_changes) if recent_changes else 0
                        # Map avg absolute change to estimated VIX
                        if avg_abs_change > 15:
                            est_vix = min(45, 28 + (avg_abs_change - 15) * 0.5)
                        elif avg_abs_change > 8:
                            est_vix = 22 + (avg_abs_change - 8) * 0.86
                        elif avg_abs_change > 3:
                            est_vix = 17 + (avg_abs_change - 3) * 1.0
                        else:
                            est_vix = 13 + avg_abs_change * 1.3
                        est_vix = round(max(12, min(50, est_vix)), 1)
                        # V3.4: 5d/20d Change aus UVXY-Bars ableiten
                        _uvxy_5d = 0
                        _uvxy_20d = 0
                        if len(bars) >= 6:
                            _uvxy_5d = round(((bars[0]["c"] - bars[5]["c"]) / bars[5]["c"] * 100) / 1.5, 2)
                        result["vix"] = {"ticker": "UVXY", "name": "VIX (est.)", "price": est_vix,
                                         "change_1d": round(uvxy_chg_pct / 1.5, 2),
                                         "change_5d": _uvxy_5d, "change_20d": _uvxy_20d}
                        if est_vix >= 30: result["vix"]["level"] = "EXTREME"
                        elif est_vix >= 25: result["vix"]["level"] = "HIGH"
                        elif est_vix >= 20: result["vix"]["level"] = "ELEVATED"
                        else: result["vix"]["level"] = "LOW"
                        print(f"[Crash Monitor] VIX estimated from UVXY volatility: {est_vix} (avg_abs_chg={avg_abs_change:.1f}%)")
        except Exception as e:
            print(f"[Crash Monitor] VIX fetch error: {e}")

        for sym, name, desc in vix_tickers:
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/2024-01-01/2099-12-31"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 30, "sort": "desc"})
                if resp.status_code != 200:
                    print(f"[Crash Monitor] {sym} API response: {resp.status_code}")
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

                if False:  # VIX now handled separately above
                    pass
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
            except Exception as e:
                print(f"[Warning] Error processing market index: {e}")
                continue

        # Market breadth — V3.4 FIX: Full Snapshot statt nur Gainers/Losers (die geben nur Top 250)
        try:
            up = 0
            down = 0
            unchanged = 0
            excluded_non_common = 0
            gainers_chgs = []
            losers_chgs = []
            common_stock_universe, common_stock_source = _load_common_stock_universe()

            snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            try:
                snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY}, timeout=30)
                print(f"[Crash Monitor] Full snapshot: status={snap_resp.status_code}")
            except Exception as req_err:
                print(f"[Crash Monitor] Full snapshot FAILED: {req_err}")
                snap_resp = None

            if snap_resp and snap_resp.status_code == 200:
                all_tickers = snap_resp.json().get("tickers", [])
                print(f"[Crash Monitor] Full snapshot: {len(all_tickers)} tickers received")
                for t in all_tickers:
                    try:
                        ticker = str(t.get("ticker", "") or "").upper().strip()
                        if not ticker:
                            continue
                        if common_stock_universe is not None:
                            if ticker not in common_stock_universe:
                                excluded_non_common += 1
                                continue
                        elif _looks_like_non_stock_etp_symbol(ticker):
                            excluded_non_common += 1
                            continue

                        tod = t.get("todaysChangePerc", 0)
                        if not isinstance(tod, (int, float)):
                            continue
                        if tod > 0:
                            up += 1
                            gainers_chgs.append(tod)
                        elif tod < 0:
                            down += 1
                            losers_chgs.append(tod)
                        else:
                            unchanged += 1
                    except Exception:
                        continue

            gainers_avg_chg = round(sum(gainers_chgs) / len(gainers_chgs), 2) if gainers_chgs else 0
            losers_avg_chg = round(sum(losers_chgs) / len(losers_chgs), 2) if losers_chgs else 0
            print(f"[Crash Monitor] Breadth raw: {up} up, {down} down, {unchanged} unchanged")

            total = up + down + unchanged
            ratio = round(up / down, 2) if down > 0 else (999.0 if up > 0 else 0)
            if total == 0:
                breadth_signal = "MARKT GESCHLOSSEN"
            elif ratio > 1.5:
                breadth_signal = "BULLISH"
            elif ratio < 0.7:
                breadth_signal = "BEARISH"
            else:
                breadth_signal = "NEUTRAL"
            result["breadth"] = {
                "advancing": up, "declining": down, "unchanged": unchanged,
                "total": total, "ad_ratio": ratio,
                "advancing_pct": round(up / total * 100, 1) if total > 0 else 0,
                "declining_pct": round(down / total * 100, 1) if total > 0 else 0,
                "unchanged_pct": round(unchanged / total * 100, 1) if total > 0 else 0,
                "breadth_signal": breadth_signal,
                "market_open": total > 0,
                "gainers_avg_chg": gainers_avg_chg,
                "losers_avg_chg": losers_avg_chg,
                "common_stock_filtered": True,
                "common_stock_source": common_stock_source,
                "common_stock_universe_count": len(common_stock_universe) if common_stock_universe is not None else None,
                "excluded_non_common": excluded_non_common,
            }
            print(f"[Crash Monitor] Breadth: {up} up, {down} down, ratio={ratio}, signal={breadth_signal}, excluded={excluded_non_common}, source={common_stock_source}")
        except Exception as e:
            print(f"[Warning] Market breadth error: {e}")

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
        print(f"[Crash Monitor] Done — fear_score={fear_score}, indices={len(result.get('indices',[]))}, vix={result.get('vix',{}).get('price','?')}")
    except Exception as e:
        print(f"Crash monitor error: {e}")
        import traceback
        traceback.print_exc()
        save_cache_file(CRASH_MONITOR_CACHE, [{
            "status": "error",
            "error": str(e),
            "message": "Crash monitor scan failed; stale data must not be treated as a fresh success.",
            "timestamp": datetime.now().isoformat(),
            "vix": {},
            "indices": [],
            "breadth": {},
            "fear_score": 0,
            "fear_level": "ERROR",
        }])


# ── Kalender (Economic Calendar) ──
def _calculate_next_occurrence(month: int, day: int) -> str:
    """Calculate next occurrence of a recurring event."""
    from datetime import date, timedelta
    today = date.today()
    next_date = date(today.year, month, day)
    if next_date < today:
        next_date = date(today.year + 1, month, day)
    return next_date.isoformat()


def _event_time_fields(date_str: str, hour_et: int, minute_et: int = 0) -> Dict[str, Any]:
    """Return ET and local Zurich time labels for a known US macro release time."""
    try:
        from datetime import date as _date, datetime as _dt, time as _dt_time
        from zoneinfo import ZoneInfo
        d = _date.fromisoformat(date_str)
        dt_et = _dt.combine(d, _dt_time(hour_et, minute_et), tzinfo=ZoneInfo("America/New_York"))
        dt_local = dt_et.astimezone(ZoneInfo("Europe/Zurich"))
        return {
            "time_et": dt_et.strftime("%I:%M %p ET").lstrip("0"),
            "time_local": dt_local.strftime("%H:%M Zurich"),
            "datetime_et": dt_et.isoformat(),
            "datetime_local": dt_local.isoformat(),
        }
    except Exception:
        return {"time_et": None, "time_local": None, "datetime_et": None, "datetime_local": None}


def _add_event(events: List[Dict[str, Any]], *, date_str: str, event: str, importance: str,
               description: str, impact: str, source: str, source_url: str = "",
               estimated: bool = False, hour_et: Optional[int] = None,
               minute_et: int = 0, category: str = "macro") -> None:
    item = {
        "date": date_str,
        "event": event,
        "importance": importance,
        "description": description,
        "impact": impact,
        "source": source,
        "source_url": source_url,
        "estimated": estimated,
        "category": category,
    }
    if hour_et is not None:
        item.update(_event_time_fields(date_str, hour_et, minute_et))
    events.append(item)


EXCHANGE_CALENDARS_2026 = [
    {
        "code": "US",
        "name": "NYSE / Nasdaq",
        "country": "USA",
        "timezone": "America/New_York",
        "city_label": "New York",
        "currency": "USD",
        "segments": [("09:30", "16:00")],
        "source": "NYSE / Nasdaq",
        "source_url": "https://www.nyse.com/markets/hours-calendars",
        "holiday_source_url": "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule",
        "holidays": {
            "2026-01-01": "New Year's Day",
            "2026-01-19": "Martin Luther King Jr. Day",
            "2026-02-16": "Washington's Birthday / Presidents Day",
            "2026-04-03": "Good Friday",
            "2026-05-25": "Memorial Day",
            "2026-06-19": "Juneteenth National Independence Day",
            "2026-07-03": "Independence Day observed",
            "2026-09-07": "Labor Day",
            "2026-11-26": "Thanksgiving Day",
            "2026-12-25": "Christmas Day",
        },
        "early_closes": {
            "2026-07-02": {"name": "Early close before Independence Day", "close": "13:00"},
            "2026-11-27": {"name": "Early close after Thanksgiving", "close": "13:00"},
            "2026-12-24": {"name": "Christmas Eve early close", "close": "13:00"},
        },
    },
    {
        "code": "LSE",
        "name": "London Stock Exchange",
        "country": "UK",
        "timezone": "Europe/London",
        "city_label": "London",
        "currency": "GBP",
        "segments": [("08:00", "16:30")],
        "source": "LSE",
        "source_url": "https://www.londonstockexchange.com/",
        "holiday_source_url": "https://www.londonstockexchange.com/",
        "holidays": {
            "2026-01-01": "New Year's Day",
            "2026-04-03": "Good Friday",
            "2026-04-06": "Easter Monday",
            "2026-05-04": "Early May Bank Holiday",
            "2026-05-25": "Spring Bank Holiday",
            "2026-08-31": "Summer Bank Holiday",
            "2026-12-25": "Christmas Day",
            "2026-12-28": "Boxing Day substitute",
        },
        "early_closes": {
            "2026-12-24": {"name": "Christmas Eve early close", "close": "12:30"},
            "2026-12-31": {"name": "New Year's Eve early close", "close": "12:30"},
        },
    },
    {
        "code": "XETRA",
        "name": "Xetra / Frankfurt",
        "country": "Deutschland",
        "timezone": "Europe/Berlin",
        "city_label": "Frankfurt",
        "currency": "EUR",
        "segments": [("09:00", "17:30")],
        "source": "Deutsche Boerse",
        "source_url": "https://www.xetra.com/xetra-en/newsroom/trading-calendar",
        "holiday_source_url": "https://www.xetra.com/xetra-en/newsroom/trading-calendar",
        "holidays": {
            "2026-01-01": "Neujahr",
            "2026-04-03": "Karfreitag",
            "2026-04-06": "Ostermontag",
            "2026-05-01": "Tag der Arbeit",
            "2026-12-24": "Heiligabend",
            "2026-12-25": "1. Weihnachtstag",
            "2026-12-31": "Silvester",
        },
        "early_closes": {},
    },
    {
        "code": "TSE",
        "name": "Tokyo Stock Exchange",
        "country": "Japan",
        "timezone": "Asia/Tokyo",
        "city_label": "Tokyo",
        "currency": "JPY",
        "segments": [("09:00", "11:30"), ("12:30", "15:30")],
        "source": "JPX",
        "source_url": "https://www.jpx.co.jp/english/corporate/about-jpx/calendar/",
        "holiday_source_url": "https://www.jpx.co.jp/english/corporate/about-jpx/calendar/",
        "holidays": {
            "2026-01-01": "New Year's Day",
            "2026-01-02": "Market Holiday",
            "2026-01-03": "Market Holiday",
            "2026-01-12": "Coming of Age Day",
            "2026-02-11": "National Foundation Day",
            "2026-02-23": "Emperor's Birthday",
            "2026-03-20": "Vernal Equinox",
            "2026-04-29": "Showa Day",
            "2026-05-03": "Constitution Memorial Day",
            "2026-05-04": "Greenery Day",
            "2026-05-05": "Children's Day",
            "2026-05-06": "Constitution Memorial Day observed",
            "2026-07-20": "Marine Day",
            "2026-08-11": "Mountain Day",
            "2026-09-21": "Respect for the Aged Day",
            "2026-09-22": "National holiday",
            "2026-09-23": "Autumnal Equinox",
            "2026-10-12": "Sports Day",
            "2026-11-03": "Culture Day",
            "2026-11-23": "Labor Thanksgiving Day",
            "2026-12-31": "Market Holiday",
        },
        "early_closes": {},
    },
    {
        "code": "HKEX",
        "name": "Hong Kong Exchange",
        "country": "Hongkong",
        "timezone": "Asia/Hong_Kong",
        "city_label": "Hong Kong",
        "currency": "HKD",
        "segments": [("09:30", "12:00"), ("13:00", "16:00")],
        "source": "HKEX",
        "source_url": "https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Calendar-and-Trading-Hours?sc_lang=en",
        "holiday_source_url": "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Market-Data-Services/Infrastructure/Index-Feed-Calendar-2026-%28English-%2C-a-%2C-Chinese%29.pdf",
        "holidays": {
            "2026-01-01": "The first day of January",
            "2026-02-17": "Lunar New Year's Day",
            "2026-02-18": "The second day of Lunar New Year",
            "2026-02-19": "The third day of Lunar New Year",
            "2026-04-03": "Good Friday",
            "2026-04-06": "The day following Ching Ming Festival",
            "2026-04-07": "The day following Easter Monday",
            "2026-05-01": "Labour Day",
            "2026-05-25": "The day following the Birthday of the Buddha",
            "2026-06-19": "Tuen Ng Festival",
            "2026-07-01": "HKSAR Establishment Day",
            "2026-10-01": "National Day",
            "2026-10-19": "The day following Chung Yeung Festival",
            "2026-12-25": "Christmas Day",
        },
        "early_closes": {},
    },
]


def _exchange_minutes(hhmm: str) -> int:
    hour, minute = [int(part) for part in hhmm.split(":")]
    return hour * 60 + minute


def _exchange_dt(local_date, hhmm: str, tz):
    from datetime import datetime as _dt, time as _dt_time
    hour, minute = [int(part) for part in hhmm.split(":")]
    return _dt.combine(local_date, _dt_time(hour, minute), tzinfo=tz)


def _exchange_segments_for_day(exchange: Dict[str, Any], date_str: str) -> List[tuple[str, str]]:
    segments = list(exchange.get("segments") or [])
    early = (exchange.get("early_closes") or {}).get(date_str)
    if not early:
        return segments

    early_close = early.get("close")
    if not early_close:
        return segments

    early_minutes = _exchange_minutes(early_close)
    adjusted = []
    for start, end in segments:
        if _exchange_minutes(start) < early_minutes:
            adjusted.append((start, early_close if _exchange_minutes(end) > early_minutes else end))
    return adjusted


def _is_exchange_trading_day(exchange: Dict[str, Any], local_date) -> bool:
    return local_date.weekday() < 5 and local_date.isoformat() not in (exchange.get("holidays") or {})


def _next_exchange_session(exchange: Dict[str, Any], start_date):
    from datetime import timedelta as _timedelta
    probe = start_date
    for _ in range(370):
        if _is_exchange_trading_day(exchange, probe):
            segments = _exchange_segments_for_day(exchange, probe.isoformat())
            if segments:
                return probe, segments[0][0]
        probe += _timedelta(days=1)
    return None, None


def _format_exchange_dt(dt_obj, city_label: str) -> str:
    return f"{dt_obj.strftime('%d.%m. %H:%M')} {city_label}"


def _build_exchange_calendar_status(now_utc=None) -> List[Dict[str, Any]]:
    from datetime import datetime as _dt, timezone as _timezone, timedelta as _timedelta
    from zoneinfo import ZoneInfo

    if now_utc is None:
        now_utc = _dt.now(_timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_timezone.utc)

    zurich_tz = ZoneInfo("Europe/Zurich")
    result = []

    for exchange in EXCHANGE_CALENDARS_2026:
        tz = ZoneInfo(exchange["timezone"])
        now_local = now_utc.astimezone(tz)
        local_date = now_local.date()
        date_str = local_date.isoformat()
        holidays = exchange.get("holidays") or {}
        early = (exchange.get("early_closes") or {}).get(date_str)
        segments = _exchange_segments_for_day(exchange, date_str)
        now_minutes = now_local.hour * 60 + now_local.minute

        status = "closed"
        status_label = "Geschlossen"
        closed_reason = "Ausserhalb der Handelszeit"
        next_open_dt = None
        next_close_dt = None

        if date_str in holidays:
            status = "holiday"
            closed_reason = f"Feiertag: {holidays[date_str]}"
            next_date, next_open = _next_exchange_session(exchange, local_date + _timedelta(days=1))
            if next_date and next_open:
                next_open_dt = _exchange_dt(next_date, next_open, tz)
        elif local_date.weekday() >= 5:
            closed_reason = "Wochenende"
            next_date, next_open = _next_exchange_session(exchange, local_date + _timedelta(days=1))
            if next_date and next_open:
                next_open_dt = _exchange_dt(next_date, next_open, tz)
        else:
            for start, end in segments:
                start_min = _exchange_minutes(start)
                end_min = _exchange_minutes(end)
                if start_min <= now_minutes < end_min:
                    status = "open"
                    status_label = "Offen"
                    closed_reason = ""
                    next_close_dt = _exchange_dt(local_date, end, tz)
                    break

            if status != "open":
                future_segments = [seg for seg in segments if _exchange_minutes(seg[0]) > now_minutes]
                past_segments = [seg for seg in segments if _exchange_minutes(seg[1]) <= now_minutes]
                if future_segments:
                    next_open_dt = _exchange_dt(local_date, future_segments[0][0], tz)
                    if past_segments:
                        status = "break"
                        status_label = "Pause"
                        closed_reason = "Mittagspause"
                    else:
                        closed_reason = "Noch nicht geoeffnet"
                else:
                    next_date, next_open = _next_exchange_session(exchange, local_date + _timedelta(days=1))
                    if next_date and next_open:
                        next_open_dt = _exchange_dt(next_date, next_open, tz)
                    closed_reason = "Handelstag beendet"

        next_holiday = None
        for holiday_date, holiday_name in sorted(holidays.items()):
            if holiday_date >= date_str:
                next_holiday = {"date": holiday_date, "name": holiday_name}
                break

        def _segments_label(target_tz, label):
            labels = []
            for start, end in segments:
                start_dt = _exchange_dt(local_date, start, tz).astimezone(target_tz)
                end_dt = _exchange_dt(local_date, end, tz).astimezone(target_tz)
                labels.append(f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}")
            return f"{' / '.join(labels)} {label}" if labels else "geschlossen"

        next_open_zurich = next_open_dt.astimezone(zurich_tz) if next_open_dt else None
        next_close_zurich = next_close_dt.astimezone(zurich_tz) if next_close_dt else None

        result.append({
            "code": exchange["code"],
            "name": exchange["name"],
            "country": exchange["country"],
            "currency": exchange["currency"],
            "timezone": exchange["timezone"],
            "city_label": exchange["city_label"],
            "market_date": date_str,
            "status": status,
            "status_label": status_label,
            "is_open": status == "open",
            "closed_reason": closed_reason,
            "holiday_today": holidays.get(date_str),
            "special_hours": early.get("name") if early else None,
            "regular_hours_local": _segments_label(tz, exchange["city_label"]),
            "regular_hours_zurich": _segments_label(zurich_tz, "Zuerich"),
            "now_local": now_local.strftime("%H:%M"),
            "next_open_local": _format_exchange_dt(next_open_dt, exchange["city_label"]) if next_open_dt else None,
            "next_open_zurich": _format_exchange_dt(next_open_zurich, "Zuerich") if next_open_zurich else None,
            "next_close_local": _format_exchange_dt(next_close_dt, exchange["city_label"]) if next_close_dt else None,
            "next_close_zurich": _format_exchange_dt(next_close_zurich, "Zuerich") if next_close_zurich else None,
            "next_holiday": next_holiday,
            "source": exchange["source"],
            "source_url": exchange["source_url"],
            "holiday_source_url": exchange["holiday_source_url"],
        })

    return result


@app.get("/api/kalender")
def get_economic_calendar():
    """Get upcoming economic events and important dates."""
    try:
        from datetime import date, timedelta
        events = []

        # Official FOMC decision dates from the Federal Reserve calendar.
        # Decision/statement: 2:00 p.m. ET. Press conference: 2:30 p.m. ET.
        fomc_source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        fomc_calendar_source = "https://www.federalreserve.gov/newsevents/2026-04.htm"
        fomc_decisions = [
            ("2026-01-28", False), ("2026-03-18", True), ("2026-04-29", False),
            ("2026-06-17", True), ("2026-07-29", False), ("2026-09-16", True),
            ("2026-10-28", False), ("2026-12-09", True),
            ("2027-01-27", False), ("2027-03-17", True), ("2027-04-28", False),
            ("2027-06-09", True), ("2027-07-28", False), ("2027-09-15", True),
            ("2027-10-27", False), ("2027-12-08", True),
        ]
        for decision_date, has_sep in fomc_decisions:
            desc = "Federal Reserve Interest Rate Decision"
            if has_sep:
                desc += " + Summary of Economic Projections"
            _add_event(
                events,
                date_str=decision_date,
                event="FED Zinsentscheid USA" + (" (SEP)" if has_sep else ""),
                importance="high",
                description=desc,
                impact="Sehr Hoch",
                source="Federal Reserve",
                source_url=fomc_calendar_source if decision_date == "2026-04-29" else fomc_source,
                estimated=False,
                hour_et=14,
                minute_et=0,
                category="central_bank",
            )
            _add_event(
                events,
                date_str=decision_date,
                event="FOMC Pressekonferenz",
                importance="high",
                description="Federal Reserve Chair press conference after rate decision",
                impact="Sehr Hoch",
                source="Federal Reserve",
                source_url=fomc_calendar_source if decision_date == "2026-04-29" else fomc_source,
                estimated=False,
                hour_et=14,
                minute_et=30,
                category="central_bank",
            )

        official_macro_events = [
            # BLS CPI
            ("2026-05-12", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for April 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-06-10", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for May 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-07-14", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for June 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-08-12", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for July 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-09-11", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for August 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-10-14", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for September 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-11-10", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for October 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),
            ("2026-12-10", "CPI (Verbraucherpreisindex)", "high", "US Consumer Price Index for November 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", 8, 30, "inflation"),

            # BLS Employment Situation / NFP
            ("2026-05-08", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for April 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-06-05", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for May 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-07-02", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for June 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-08-07", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for July 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-09-04", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for August 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-10-02", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for September 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-11-06", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for October 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-12-04", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for November 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),

            # BLS PPI
            ("2026-05-13", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for April 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-06-11", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for May 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-07-15", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for June 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-08-13", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for July 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-09-10", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for August 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-10-15", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for September 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-11-13", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for October 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-12-15", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for November 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),

            # BEA GDP / PCE
            ("2026-04-30", "GDP (Advance Estimate)", "high", "US GDP Advance Estimate, Q1 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-04-30", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for March 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-05-28", "GDP (Second Estimate)", "high", "US GDP Second Estimate and Corporate Profits, Q1 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-05-28", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for April 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-06-25", "GDP (Third Estimate)", "high", "US GDP Third Estimate, Q1 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-06-25", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for May 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-07-30", "GDP (Advance Estimate)", "high", "US GDP Advance Estimate, Q2 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-07-30", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for June 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-08-26", "GDP (Second Estimate)", "high", "US GDP Second Estimate and Corporate Profits, Q2 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-08-26", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for July 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-09-30", "GDP (Third Estimate)", "high", "US GDP Third Estimate, Q2 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-09-30", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for August 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-10-29", "GDP (Advance Estimate)", "high", "US GDP Advance Estimate, Q3 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-10-29", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for September 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-11-25", "GDP (Second Estimate)", "high", "US GDP Second Estimate and Corporate Profits, Q3 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-11-25", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for October 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-12-23", "GDP (Third Estimate)", "high", "US GDP Third Estimate, Q3 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-12-23", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for November 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),

            # Census Retail Sales / Advance Economic Indicators
            ("2026-05-14", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for April 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-06-17", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for May 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-07-16", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for June 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-08-14", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for July 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-09-16", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for August 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-10-15", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for September 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-11-17", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for October 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-12-16", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for November 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-05-29", "Advance Economic Indicators", "medium", "US Advance Economic Indicators Report for April 2026", "Hoch", "Census", "https://www.census.gov/econ/indicators/release_schedule.html", 8, 30, "macro"),
            ("2026-06-26", "Advance Economic Indicators", "medium", "US Advance Economic Indicators Report for May 2026", "Hoch", "Census", "https://www.census.gov/econ/indicators/release_schedule.html", 8, 30, "macro"),
            ("2026-07-28", "Advance Economic Indicators", "medium", "US Advance Economic Indicators Report for June 2026", "Hoch", "Census", "https://www.census.gov/econ/indicators/release_schedule.html", 8, 30, "macro"),
        ]
        for date_str, event_name, importance, description, impact, source, source_url, hour, minute, category in official_macro_events:
            _add_event(
                events,
                date_str=date_str,
                event=event_name,
                importance=importance,
                description=description,
                impact=impact,
                source=source,
                source_url=source_url,
                estimated=False,
                hour_et=hour,
                minute_et=minute,
                category=category,
            )

        ism_source = "https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/"
        official_ism_events = [
            ("2026-01-05", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-02-02", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-03-02", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-04-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-05-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-06-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-07-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-08-03", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-09-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-10-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-11-02", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-12-01", "ISM Manufacturing PMI", "ISM Manufacturing PMI Report release", "business_survey"),
            ("2026-01-07", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-02-04", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-03-04", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-04-06", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-05-05", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-06-03", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-07-06", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-08-05", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-09-03", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-10-05", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-11-04", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-12-03", "ISM Services PMI", "ISM Services PMI Report release", "business_survey"),
            ("2026-05-15", "ISM Supply Chain Planning Forecast", "ISM semiannual supply chain planning forecast", "macro"),
            ("2026-12-16", "ISM Supply Chain Planning Forecast", "ISM semiannual supply chain planning forecast", "macro"),
        ]
        for date_str, event_name, description, category in official_ism_events:
            _add_event(
                events,
                date_str=date_str,
                event=event_name,
                importance="medium",
                description=description,
                impact="Hoch",
                source="ISM",
                source_url=ism_source,
                estimated=False,
                hour_et=10,
                minute_et=0,
                category=category,
            )

        earnings_months = [4, 7, 10, 1]  # Q1, Q2, Q3, Q4
        for month in earnings_months:
            try:
                next_earnings = _calculate_next_occurrence(month, 15)
                _add_event(events, date_str=next_earnings, event="Earnings Season",
                           importance="high", description="Corporate Earnings Reports (ungefährer Start)",
                           impact="Hoch", source="Estimated schedule", estimated=True,
                           category="earnings")
            except Exception as e:
                print(f"[Warning] {e}")

        # Jobless Claims (weekly, every Thursday)
        try:
            from datetime import timedelta
            today_dt = date.today()
            days_until_thursday = (3 - today_dt.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
            for i in range(4):  # Next 4 Thursdays
                next_thursday = today_dt + timedelta(days=days_until_thursday + i * 7)
                _add_event(events, date_str=next_thursday.isoformat(),
                           event="Erstanträge Arbeitslosenhilfe",
                           importance="medium", description="Initial Jobless Claims (wöchentlich, geschätzt)",
                           impact="Mittel", source="Estimated schedule", estimated=True,
                           hour_et=8, minute_et=30)
        except Exception as e:
            print(f"[Warning] {e}")

        # Official source schedules replace the old estimated placeholders for
        # these market-moving releases.
        official_event_prefixes = ("CPI", "NFP", "GDP", "PPI", "Retail Sales")
        events = [
            e for e in events
            if not (e.get("estimated") and str(e.get("event", "")).startswith(official_event_prefixes))
        ]

        # Filter: only future events within 120 days
        today_str = date.today().isoformat()
        max_date = (date.today() + timedelta(days=120)).isoformat()
        events = [e for e in events if today_str <= e["date"] <= max_date]

        # Sort by date
        events.sort(key=lambda x: x["date"])

        official_count = sum(1 for e in events if not e.get("estimated"))
        estimated_count = sum(1 for e in events if e.get("estimated"))

        return {
            "status": "success",
            "source": "official_macro_calendar_with_marked_estimates",
            "events": events,
            "exchanges": _build_exchange_calendar_status(),
            "official_count": official_count,
            "estimated_count": estimated_count,
            "official_sources": ["Federal Reserve", "BLS", "BEA", "Census", "ISM", "NYSE/Nasdaq", "LSE", "Deutsche Boerse", "JPX", "HKEX"],
            "timestamp": datetime.now().isoformat(),
            "note": "FOMC/FED, CPI, NFP, PPI, GDP/PCE, Retail Sales, Census Advance Economic Indicators and ISM PMI use official 2026 source schedules. Exchange hours/holidays cover NYSE/Nasdaq, LSE, Xetra, Tokyo and Hong Kong for 2026. Earnings and weekly claims remain marked estimates."
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }


# ── Market Context / Headline Risk ──
MARKET_CONTEXT_CACHE = "/tmp/market_context_cache.json"
MARKET_CONTEXT_MAX_AGE_SECONDS = 45 * 60


def _cache_age_seconds(cached_at: Optional[str]) -> Optional[int]:
    if not cached_at:
        return None
    try:
        return int(max(0, (datetime.now() - datetime.fromisoformat(cached_at)).total_seconds()))
    except Exception:
        return None


def _calendar_event_risk_snapshot() -> Dict[str, Any]:
    try:
        calendar = get_economic_calendar()
        if isinstance(calendar, dict) and calendar.get("status") == "success":
            return build_event_risk(calendar.get("events", []))
    except Exception as exc:
        return {"score": 0, "level": "LOW", "upcoming_events": [], "error": str(exc)}
    return {"score": 0, "level": "LOW", "upcoming_events": []}


def _load_crash_context_snapshot() -> Dict[str, Any]:
    try:
        crash_results, _ = load_cache_file(CRASH_MONITOR_CACHE)
        if crash_results and isinstance(crash_results[0], dict):
            if crash_results[0].get("status") != "error":
                return crash_results[0]
    except Exception:
        pass
    return {}


def _fetch_polygon_market_headlines(limit: int = 50) -> tuple[List[Dict[str, Any]], Optional[str]]:
    if not POLYGON_KEY:
        return [], "POLYGON_KEY fehlt"
    try:
        resp = rate_limited_get(
            "https://api.polygon.io/v2/reference/news",
            params={
                "apiKey": POLYGON_KEY,
                "limit": limit,
                "sort": "published_utc",
                "order": "desc",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return [], f"Polygon news HTTP {resp.status_code}"
        payload = resp.json()
        return payload.get("results", []) or [], None
    except Exception as exc:
        return [], str(exc)


def _market_context_wrapper() -> None:
    """Build market weather from cached market internals, scheduled events and headlines."""
    headlines, headline_error = _fetch_polygon_market_headlines()
    if headline_error:
        headline_risk = missing_headline_risk(headline_error)
    else:
        headline_risk = analyze_headlines(headlines)
    event_risk = _calendar_event_risk_snapshot()
    crash_data = _load_crash_context_snapshot()
    context = build_market_context(crash_data, headline_risk, event_risk)
    context["source"] = {
        "market_internals": "crash_monitor_cache",
        "headlines": "Polygon news",
        "events": "Alpha Station economic calendar",
    }
    context["headline_count"] = len(headlines)
    save_cache_file(MARKET_CONTEXT_CACHE, [context])
    print(f"[Market Context] {context.get('regime')} / {context.get('trade_mode')} risk={context.get('overall_risk_score')} headlines={len(headlines)}")


def _get_market_context_snapshot() -> Dict[str, Any]:
    """Cheap cache-only market context for scanner decoration."""
    try:
        cached, cached_at = load_cache_file(MARKET_CONTEXT_CACHE)
        if cached and isinstance(cached[0], dict):
            context = dict(cached[0])
            cache_age = _cache_age_seconds(cached_at)
            if cache_age is not None and cache_age <= MARKET_CONTEXT_MAX_AGE_SECONDS:
                context["cache_age_seconds"] = cache_age
                context["cache_status"] = "fresh"
                return context
            stale_msg = f"Market-Context-Cache stale ({cache_age}s alt)" if cache_age is not None else "Market-Context-Cache timestamp unbekannt"
            headline_risk = missing_headline_risk(stale_msg)
            event_risk = _calendar_event_risk_snapshot()
            fallback = build_market_context(_load_crash_context_snapshot(), headline_risk, event_risk)
            fallback["cache_age_seconds"] = cache_age
            fallback["cache_status"] = "stale"
            fallback["warnings"] = list(fallback.get("warnings", [])) + [stale_msg]
            return fallback
    except Exception:
        pass

    headline_risk = missing_headline_risk("Market-Context-Cache fehlt; Live-News nicht verfuegbar")
    event_risk = _calendar_event_risk_snapshot()
    context = build_market_context(_load_crash_context_snapshot(), headline_risk, event_risk)
    context["cache_age_seconds"] = None
    context["warnings"] = list(context.get("warnings", [])) + ["Market-Context-Cache fehlt; nutze Crash/Kalender-Fallback ohne Live-News"]
    return context


@app.post("/api/market-context-scan")
def trigger_market_context_scan():
    _run_scan_safe("market_context", _market_context_wrapper)
    return {"status": "started", "message": "Market context scan started"}


@app.get("/api/market-context")
def get_market_context():
    context = _get_market_context_snapshot()
    stale = context.get("cache_status") == "stale" or (
        context.get("cache_age_seconds") is not None and context.get("cache_age_seconds") > MARKET_CONTEXT_MAX_AGE_SECONDS
    )
    return {
        "status": "success",
        "data": context,
        "cache_age_seconds": context.get("cache_age_seconds"),
        "warnings": (context.get("warnings") or []) + (["Market context cache stale"] if stale else []),
    }


# ── Backtest Engine ──
BACKTEST_CACHE = "/tmp/backtest_cache.json"
BACKTEST_PROGRESS: Dict[str, Dict[str, Any]] = {}
_BACKTEST_PROGRESS_LOCK = threading.Lock()
_BACKTEST_PROGRESS_TTL_SECONDS = 2 * 3600


class BacktestRequest(BaseModel):
    ticker: str = "AAPL"
    strategy: str = "sma_crossover"  # sma_crossover, rsi_mean_reversion, ema_crossover
    months: int = 6
    max_tickers: int = 50
    min_price: float = 0
    min_volume: int = 0
    job_id: Optional[str] = None


ADVANCED_SCANNER_BACKTESTS = {
    "scanner_bi_long": {
        "name": "BI Scanner Long",
        "category": "Scanner Backtests",
        "direction": "long",
        "engine": "bi_v2",
        "default_max_tickers": 200,
        "default_min_price": 5.0,
        "default_min_volume": 200000,
        "note": "Backtest nutzt die BI-Retest-Engine mit 50/50 TP1/TP2-Logik.",
    },
    "scanner_bi_short": {
        "name": "BI Scanner Short",
        "category": "Scanner Backtests",
        "direction": "short",
        "engine": "bi_v2",
        "default_max_tickers": 200,
        "default_min_price": 5.0,
        "default_min_volume": 200000,
        "note": "Backtest nutzt die BI-Short-Retest-Engine mit 50/50 TP1/TP2-Logik.",
    },
    "scanner_biotech": {
        "name": "Bio Catalyst Scanner",
        "category": "Scanner Backtests",
        "direction": "long",
        "engine": "biotech",
        "default_max_tickers": 100,
        "default_min_price": 2.0,
        "default_min_volume": 100000,
        "note": "Historische Catalyst-Daten sind begrenzt; Volume-Spikes dienen als Catalyst-Proxy.",
    },
}

CRYPTO_BACKTESTS = {
    "crypto_early_mover_long": {
        "name": "Crypto Early Mover Long",
        "category": "Crypto Backtests",
        "direction": "long",
        "default_max_tickers": 40,
        "note": "Daily-OHLC Backtest; Intraday-Trigger/Retests koennen historisch nur naeherungsweise abgebildet werden.",
    },
    "crypto_pump_dump_short": {
        "name": "Crypto Pump & Dump Short",
        "category": "Crypto Backtests",
        "direction": "short",
        "default_max_tickers": 40,
        "note": "Daily-OHLC Backtest fuer parabolische Pumps mit bestaetigtem Crack; neue Coin-Microstructure ist nicht voll rekonstruierbar.",
    },
}


def _backtest_progress_key(job_id: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(job_id or "").strip())
    return safe[:80] or f"bt_{int(time.time() * 1000)}"


def _cleanup_backtest_progress(now: Optional[float] = None) -> None:
    now = now or time.time()
    with _BACKTEST_PROGRESS_LOCK:
        stale = [
            job_id for job_id, state in BACKTEST_PROGRESS.items()
            if now - float(state.get("updated_ts") or state.get("started_ts") or now) > _BACKTEST_PROGRESS_TTL_SECONDS
        ]
        for job_id in stale:
            BACKTEST_PROGRESS.pop(job_id, None)


def _backtest_progress_update(
    job_id: Optional[str],
    status: str = "running",
    pct: float = 0.0,
    message: str = "",
    **extra: Any,
) -> Optional[str]:
    if not job_id:
        return None
    safe_job = _backtest_progress_key(job_id)
    now = time.time()
    pct = max(0.0, min(1.0, _bt_float(pct, 0.0)))
    with _BACKTEST_PROGRESS_LOCK:
        current = BACKTEST_PROGRESS.get(safe_job, {})
        current.update({
            "job_id": safe_job,
            "status": status,
            "pct": round(pct, 4),
            "percent": round(pct * 100, 1),
            "message": message or current.get("message") or "",
            "started_ts": current.get("started_ts") or now,
            "updated_ts": now,
            "updated_at": datetime.now().isoformat(),
        })
        current.update(extra)
        BACKTEST_PROGRESS[safe_job] = current
    return safe_job


def _backtest_progress_read(job_id: Optional[str]) -> Dict[str, Any]:
    safe_job = _backtest_progress_key(job_id)
    _cleanup_backtest_progress()
    with _BACKTEST_PROGRESS_LOCK:
        state = dict(BACKTEST_PROGRESS.get(safe_job, {}))
    if not state:
        return {
            "job_id": safe_job,
            "status": "unknown",
            "pct": 0,
            "percent": 0,
            "message": "Kein Backtest-Fortschritt gefunden",
        }
    started = float(state.get("started_ts") or time.time())
    state["elapsed_seconds"] = round(max(0, time.time() - started), 1)
    return state


def _bt_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def _bt_round_price(value: Any, crypto: bool = False) -> Optional[float]:
    number = _bt_float(value, 0)
    if number <= 0:
        return None
    if crypto:
        return _round_crypto_price(number)
    return _round_trade_price(number)


def _bt_bar_date(bar: Dict[str, Any]) -> str:
    raw = bar.get("date") or bar.get("time")
    if raw:
        return str(raw)[:10]
    ts = bar.get("t")
    try:
        if ts:
            return datetime.utcfromtimestamp(float(ts) / 1000).strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def _bt_max_drawdown(trades: List[Dict[str, Any]]) -> float:
    equity = 100.0
    peak = equity
    max_dd = 0.0
    for trade in trades:
        equity *= 1 + (_bt_float(trade.get("pnl_pct")) / 100)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, ((peak - equity) / peak) * 100)
    return round(max_dd, 2)


def _bt_compounded_return(trades: List[Dict[str, Any]]) -> float:
    equity = 100.0
    for trade in trades:
        equity *= 1 + (_bt_float(trade.get("pnl_pct")) / 100)
    return round(equity - 100.0, 2)


def _bt_backtest_verdict(
    total_trades: int,
    win_rate: float,
    avg_pnl: float,
    profit_factor: float,
    avg_r: float,
    max_drawdown: float,
    total_return: float,
) -> Dict[str, Any]:
    """Translate raw stats into a clear trading gate for the UI."""
    if total_trades <= 0:
        return {
            "status": "no_trades",
            "label": "KEIN SIGNAL",
            "color": "gray",
            "tradable": False,
            "summary": "Backtest hat keine ausfuehrbaren Trades gefunden.",
            "reasons": ["Keine belastbare Aussage ohne Trades."],
        }

    blockers: List[str] = []
    warnings: List[str] = []
    if total_trades < 20:
        warnings.append(f"Sample klein: nur {total_trades} Trades.")
    if profit_factor <= 0:
        blockers.append("Profit Factor nicht positiv.")
    elif profit_factor < 1.0:
        blockers.append(f"Profit Factor {profit_factor:.2f} < 1.00.")
    elif profit_factor < 1.15:
        warnings.append(f"Profit Factor {profit_factor:.2f} ist nur knapp positiv.")
    if avg_pnl < 0:
        blockers.append(f"Durchschnitt pro Trade {avg_pnl:.2f}% ist negativ.")
    elif avg_pnl < 0.25:
        warnings.append(f"Durchschnitt pro Trade {avg_pnl:.2f}% ist sehr duenn.")
    if avg_r < 0:
        blockers.append(f"Avg R {avg_r:.2f} ist negativ.")
    elif total_trades >= 10 and avg_r < 0.10:
        warnings.append(f"Avg R {avg_r:.2f} zeigt kaum Edge.")
    if max_drawdown >= 35:
        blockers.append(f"Max Drawdown {max_drawdown:.1f}% ist zu hoch.")
    elif max_drawdown >= 20:
        warnings.append(f"Max Drawdown {max_drawdown:.1f}% ist erhoeht.")
    if total_return <= -10:
        blockers.append(f"Equity {total_return:.1f}% ist klar negativ.")

    if blockers:
        return {
            "status": "blocked",
            "label": "NICHT FREIGEBEN",
            "color": "red",
            "tradable": False,
            "summary": "Diese Strategie darf mit diesen Regeln nicht live gehandelt werden.",
            "reasons": blockers + warnings,
        }
    if total_trades < 20:
        return {
            "status": "sample_small",
            "label": "ZU WENIG DATEN",
            "color": "orange",
            "tradable": False,
            "summary": "Ergebnis ist noch nicht belastbar genug fuer Live-Freigabe.",
            "reasons": warnings or ["Mindestens 20 Trades fuer eine erste Aussage abwarten."],
        }
    if profit_factor >= 1.35 and avg_pnl >= 0.35 and avg_r >= 0.20 and max_drawdown <= 20 and win_rate >= 45:
        return {
            "status": "approved",
            "label": "FREIGABE MOEGLICH",
            "color": "green",
            "tradable": True,
            "summary": "Backtest zeigt eine robuste Edge, trotzdem nur mit Risk-Limits handeln.",
            "reasons": warnings,
        }
    if profit_factor >= 1.10 and avg_pnl > 0 and max_drawdown < 30:
        return {
            "status": "selective",
            "label": "NUR SELEKTIV",
            "color": "blue",
            "tradable": False,
            "summary": "Leichte Edge, aber noch nicht stark genug fuer automatische Freigabe.",
            "reasons": warnings or ["Filter/Entry-Regeln weiter verschaerfen."],
        }
    return {
        "status": "weak",
        "label": "EDGE ZU SCHWACH",
        "color": "orange",
        "tradable": False,
        "summary": "Nicht schlecht genug fuer einen harten Block, aber noch keine Live-Freigabe.",
        "reasons": warnings or ["Profit Factor, Avg PnL oder Drawdown sind nicht ueberzeugend."],
    }


def _bt_trade_sort_key(trade: Dict[str, Any]) -> Tuple[str, str]:
    date_key = str(trade.get("entry_date") or trade.get("signal_date") or trade.get("exit_date") or "")
    ticker_key = str(trade.get("ticker") or trade.get("symbol") or "")
    return (date_key, ticker_key)


def _bt_stats_by_grade(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for grade in ["S", "A", "B", "C", "D"]:
        grade_trades = [t for t in trades if str(t.get("grade") or "").upper() == grade]
        if not grade_trades:
            continue
        wins = [t for t in grade_trades if _bt_float(t.get("pnl_pct")) > 0]
        losses = [t for t in grade_trades if _bt_float(t.get("pnl_pct")) <= 0]
        gross_profit = sum(_bt_float(t.get("pnl_pct")) for t in wins)
        gross_loss = abs(sum(_bt_float(t.get("pnl_pct")) for t in losses))
        stats[grade] = {
            "total": len(grade_trades),
            "winners": len(wins),
            "losers": len(losses),
            "win_rate": round(len(wins) / len(grade_trades) * 100, 1),
            "avg_pnl": round(sum(_bt_float(t.get("pnl_pct")) for t in grade_trades) / len(grade_trades), 2),
            "avg_r": round(sum(_bt_float(t.get("r_multiple")) for t in grade_trades) / len(grade_trades), 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0),
        }
    return stats


def _normalize_backtest_trades(trades: List[Dict[str, Any]], direction: str, crypto: bool = False) -> List[Dict[str, Any]]:
    rows = []
    for trade in trades[-150:]:
        entry_price = trade.get("actual_entry", trade.get("entry_price", trade.get("entry_target")))
        exit_price = trade.get("exit_price")
        rows.append({
            "ticker": trade.get("ticker") or trade.get("symbol") or "",
            "entry_date": trade.get("entry_date") or trade.get("signal_date") or "",
            "entry_price": _bt_round_price(entry_price, crypto),
            "exit_date": trade.get("exit_date") or "",
            "exit_price": _bt_round_price(exit_price, crypto),
            "pnl_pct": round(_bt_float(trade.get("pnl_pct")), 2),
            "r_multiple": round(_bt_float(trade.get("r_multiple")), 2),
            "type": str(trade.get("direction") or direction or "LONG").upper(),
            "grade": str(trade.get("grade") or "").upper(),
            "outcome": trade.get("outcome") or trade.get("exit_reason") or "",
            "signal_date": trade.get("signal_date") or "",
            "tp1_hit": bool(trade.get("tp1_hit")),
        })
    return rows


def _build_backtest_result(
    strategy: str,
    label: str,
    direction: str,
    months: int,
    trades: List[Dict[str, Any]],
    total_signals: Optional[int] = None,
    no_fill: int = 0,
    n_tickers: int = 0,
    stats_by_grade: Optional[Dict[str, Dict[str, Any]]] = None,
    note: str = "",
    crypto: bool = False,
) -> Dict[str, Any]:
    filled = [t for t in trades if str(t.get("outcome") or "").upper() != "NO_FILL"]
    filled = sorted(filled, key=_bt_trade_sort_key)
    pcts = [_bt_float(t.get("pnl_pct")) for t in filled]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    total_trades = len(filled)
    normalized_trades = _normalize_backtest_trades(filled, direction, crypto)
    win_rate = round(len(wins) / total_trades * 100, 1) if total_trades else 0
    avg_pnl = round(sum(pcts) / total_trades, 2) if total_trades else 0
    sum_pnl = round(sum(pcts), 2) if total_trades else 0
    total_return = _bt_compounded_return(filled)
    max_drawdown = _bt_max_drawdown(filled)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0)
    avg_r = round(sum(_bt_float(t.get("r_multiple")) for t in filled) / total_trades, 2) if total_trades else 0
    return {
        "ticker": "Crypto Universe" if crypto else "Scanner Universe",
        "strategy": strategy,
        "strategy_label": label,
        "backtest_type": "crypto" if crypto else "scanner",
        "months": months,
        "n_tickers": n_tickers,
        "total_signals": int(total_signals if total_signals is not None else len(trades)),
        "total_trades": total_trades,
        "no_fill": int(no_fill),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "sum_pnl": sum_pnl,
        "total_return": total_return,
        "compounded_return": total_return,
        "max_drawdown": max_drawdown,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "best_trade": round(max(pcts), 2) if pcts else 0,
        "worst_trade": round(min(pcts), 2) if pcts else 0,
        "profit_factor": profit_factor,
        "avg_r": avg_r,
        "verdict": _bt_backtest_verdict(total_trades, win_rate, avg_pnl, profit_factor, avg_r, max_drawdown, total_return),
        "stats_by_grade": stats_by_grade or _bt_stats_by_grade(filled),
        "trades": normalized_trades,
        "note": note,
        "methodology": "Universe-Kennzahlen sind chronologisch sortierte Trade-Statistiken, keine garantierte Portfolio-Rendite.",
        "timestamp": datetime.now().isoformat(),
    }


def _normalize_scanner_backtest(raw: Dict[str, Any], strategy: str, meta: Dict[str, Any], months: int) -> Dict[str, Any]:
    summary = raw.get("summary") or {}
    all_trades = raw.get("trades") or []
    filled = [t for t in all_trades if str(t.get("outcome") or "").upper() != "NO_FILL"]
    return _build_backtest_result(
        strategy=strategy,
        label=meta.get("name", strategy),
        direction=meta.get("direction", "long"),
        months=months,
        trades=filled,
        total_signals=summary.get("total_signals", len(all_trades)),
        no_fill=summary.get("no_fill", len(all_trades) - len(filled)),
        n_tickers=summary.get("n_tickers", 0),
        stats_by_grade=raw.get("stats_by_grade") or None,
        note=meta.get("note", ""),
        crypto=False,
    )


def _run_advanced_scanner_backtest(request: BacktestRequest) -> Dict[str, Any]:
    if not HAS_ADVANCED_BACKTESTS:
        return {"error": "Advanced Backtest Engines sind nicht geladen"}
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    strategy = request.strategy
    meta = ADVANCED_SCANNER_BACKTESTS[strategy]
    max_tickers = max(5, min(int(request.max_tickers or meta["default_max_tickers"]), 500))
    min_price = max(0.0, float(request.min_price or meta["default_min_price"]))
    min_volume = max(0, int(request.min_volume or meta["default_min_volume"]))
    months = max(1, min(int(request.months or 6), 24))
    job_id = _backtest_progress_key(request.job_id)

    def _progress_callback(pct: float, text: str) -> None:
        _backtest_progress_update(
            job_id,
            "running",
            max(0.02, min(0.96, pct)),
            text or f"{meta.get('name', 'Scanner')} Backtest laeuft...",
            strategy=strategy,
        )

    if meta["engine"] == "bi_v2":
        raw = run_bi_v2_backtest(
            POLYGON_KEY,
            direction=meta["direction"],
            months=months,
            max_tickers=max_tickers,
            min_price=min_price,
            min_volume=min_volume,
            progress_callback=_progress_callback,
        )
    else:
        raw = run_biotech_backtest(
            POLYGON_KEY,
            months=months,
            max_tickers=max_tickers,
            min_price=min_price,
            min_volume=min_volume,
            progress_callback=_progress_callback,
        )
    return _normalize_scanner_backtest(raw or {}, strategy, meta, months)


def _normalize_crypto_bars(raw_bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bars = []
    for bar in raw_bars or []:
        o = _bt_float(bar.get("o"))
        h = _bt_float(bar.get("h"))
        l = _bt_float(bar.get("l"))
        c = _bt_float(bar.get("c"))
        if min(o, h, l, c) <= 0:
            continue
        bars.append({
            "date": _bt_bar_date(bar),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": _bt_float(bar.get("v")),
        })
    return bars


def _bt_atr(bars: List[Dict[str, Any]], idx: int, period: int = 14) -> float:
    if idx <= 0:
        return 0.0
    start = max(1, idx - period + 1)
    trs = []
    for i in range(start, idx + 1):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs) if trs else 0.0


def _simulate_crypto_trade(
    bars: List[Dict[str, Any]],
    entry_idx: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    max_hold: int,
    fee_pct: float = 0.25,
) -> Optional[Dict[str, Any]]:
    if entry_idx >= len(bars) or entry <= 0:
        return None
    side = direction.lower()
    if side not in ("long", "short"):
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if side == "long" and not (stop < entry < tp1 < tp2):
        return None
    if side == "short" and (tp1 <= 0 or tp2 <= 0):
        return None
    if side == "short" and not (tp2 < tp1 < entry < stop):
        return None

    current_stop = stop
    tp1_hit = False
    exit_price = None
    exit_reason = None
    exit_date = None
    end_idx = min(len(bars) - 1, entry_idx + max_hold - 1)

    for idx in range(entry_idx, end_idx + 1):
        bar = bars[idx]
        if side == "long":
            if bar["low"] <= current_stop:
                exit_price = current_stop
                exit_reason = "TRAIL_STOP" if tp1_hit else "STOP"
                exit_date = bar["date"]
                break
            if bar["high"] >= tp2:
                exit_price = (tp1 + tp2) / 2
                exit_reason = "TP2"
                tp1_hit = True
                exit_date = bar["date"]
                break
            if not tp1_hit and bar["high"] >= tp1:
                tp1_hit = True
                current_stop = max(current_stop, entry + risk * 0.25)
        else:
            if bar["high"] >= current_stop:
                exit_price = current_stop
                exit_reason = "TRAIL_STOP" if tp1_hit else "STOP"
                exit_date = bar["date"]
                break
            if bar["low"] <= tp2:
                exit_price = (tp1 + tp2) / 2
                exit_reason = "TP2"
                tp1_hit = True
                exit_date = bar["date"]
                break
            if not tp1_hit and bar["low"] <= tp1:
                tp1_hit = True
                current_stop = min(current_stop, entry - risk * 0.25)

    if exit_price is None:
        close_price = bars[end_idx]["close"]
        if tp1_hit:
            if side == "long":
                exit_price = (tp1 + max(close_price, entry)) / 2
            else:
                exit_price = (tp1 + min(close_price, entry)) / 2
            exit_reason = "TP1_PARTIAL"
        else:
            exit_price = close_price
            exit_reason = "MAX_HOLD"
        exit_date = bars[end_idx]["date"]

    if side == "long":
        pnl_pct = ((exit_price - entry) / entry) * 100 - fee_pct
    else:
        pnl_pct = ((entry - exit_price) / entry) * 100 - fee_pct
    return {
        "entry_date": bars[entry_idx]["date"],
        "actual_entry": entry,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "outcome": exit_reason,
        "tp1_hit": tp1_hit,
        "pnl_pct": round(pnl_pct, 2),
        "r_multiple": round(pnl_pct / (risk / entry * 100), 2) if risk > 0 else 0,
        "is_winner": pnl_pct > 0,
    }


def _crypto_backtest_universe(max_tickers: int) -> List[Dict[str, Any]]:
    pages = max(1, min(4, math.ceil(max_tickers / 180)))
    coins = _fetch_coingecko_markets(pages=pages)
    universe = []
    for coin in coins or []:
        symbol = str(coin.get("symbol") or "").upper()
        coin_id = str(coin.get("id") or "")
        name = str(coin.get("name") or "")
        if not symbol or not coin_id:
            continue
        if _is_excluded_crypto_asset(symbol, coin_id, name):
            continue
        if _bt_float(coin.get("total_volume")) < 500000:
            continue
        universe.append(coin)
        if len(universe) >= max_tickers:
            break
    return universe


def _crypto_signal_grade(score: float) -> str:
    score = min(100, max(0, _bt_float(score)))
    if score >= 85:
        return "S"
    if score >= 75:
        return "A"
    if score >= 62:
        return "B"
    return "C"


def _run_crypto_backtest(request: BacktestRequest) -> Dict[str, Any]:
    strategy = request.strategy
    meta = CRYPTO_BACKTESTS[strategy]
    job_id = _backtest_progress_key(request.job_id)
    months = max(1, min(int(request.months or 3), 12))
    max_tickers = max(5, min(int(request.max_tickers or meta["default_max_tickers"]), 120))
    days = min(365, max(90, months * 30 + 45))
    _backtest_progress_update(job_id, "running", 0.03, "Crypto-Universum wird geladen...", strategy=strategy)
    universe = _crypto_backtest_universe(max_tickers)
    _backtest_progress_update(
        job_id,
        "running",
        0.08,
        f"{len(universe)} Crypto-Kandidaten geladen - historische Kerzen werden geprueft...",
        strategy=strategy,
        total_items=len(universe),
        done_items=0,
    )
    trades: List[Dict[str, Any]] = []
    total_signals = 0

    total_universe = max(1, len(universe))
    for coin_idx, coin in enumerate(universe, start=1):
        coin_id = str(coin.get("id") or "")
        symbol = str(coin.get("symbol") or "").upper()
        _backtest_progress_update(
            job_id,
            "running",
            0.08 + (coin_idx - 1) / total_universe * 0.84,
            f"Crypto {coin_idx}/{len(universe)}: {symbol or coin_id} wird backgetestet...",
            strategy=strategy,
            total_items=len(universe),
            done_items=coin_idx - 1,
            current_item=symbol or coin_id,
        )
        bars = _normalize_crypto_bars(fetch_daily_candles_crypto(coin_id, days=days))
        if len(bars) < 45:
            continue
        cooldown_until = -999

        for idx in range(30, len(bars) - 1):
            if idx <= cooldown_until:
                continue
            close = bars[idx]["close"]
            prev_close = bars[idx - 1]["close"]
            if close <= 0 or prev_close <= 0:
                continue
            change_1d = ((close - prev_close) / prev_close) * 100
            change_3d = ((close - bars[idx - 3]["close"]) / bars[idx - 3]["close"]) * 100 if idx >= 3 and bars[idx - 3]["close"] > 0 else 0
            change_7d = ((close - bars[idx - 7]["close"]) / bars[idx - 7]["close"]) * 100 if idx >= 7 and bars[idx - 7]["close"] > 0 else 0
            closes20 = [b["close"] for b in bars[idx - 20:idx]]
            volumes20 = [b["volume"] for b in bars[idx - 20:idx] if b.get("volume", 0) > 0]
            sma20 = sum(closes20) / len(closes20)
            avg_vol20 = sum(volumes20) / len(volumes20) if volumes20 else 0
            volume_ratio = bars[idx]["volume"] / avg_vol20 if avg_vol20 > 0 else 1.0
            atr = _bt_atr(bars, idx, 14)
            if atr <= 0:
                continue

            if strategy == "crypto_early_mover_long":
                if not (1.5 <= change_1d <= 10.5 and 4.0 <= change_7d <= 55.0):
                    continue
                if close < sma20 * 1.005 or not (1.25 <= volume_ratio <= 7.5):
                    continue
                if change_3d > 28 or change_7d > 75:
                    continue
                entry_idx = idx + 1
                signal_bar = bars[idx]
                next_bar = bars[entry_idx]
                range20_high = max(b["high"] for b in bars[idx - 20:idx + 1])
                range20_low = min(b["low"] for b in bars[idx - 20:idx + 1])
                range20 = max(range20_high - range20_low, close * 0.02)
                close_pos20 = (close - range20_low) / range20
                signal_range = max(signal_bar["high"] - signal_bar["low"], close * 0.01)
                signal_close_pos = (close - signal_bar["low"]) / signal_range
                if signal_close_pos < 0.55:
                    continue
                if close_pos20 > 0.92 and change_3d > 18:
                    continue
                entry = next_bar["open"]
                pre_risk = max(atr * 1.45, close * 0.05)
                if entry > close + pre_risk * 0.65 or entry < close - pre_risk * 0.75:
                    continue
                if next_bar["close"] < next_bar["open"] and next_bar["close"] < close:
                    continue
                stop_distance = max(atr * 1.45, entry * 0.055)
                stop = entry - stop_distance
                tp1 = entry + stop_distance * 1.8
                tp2 = entry + stop_distance * 3.0
                score = 50 + min(16, change_7d / 3) + min(14, volume_ratio * 4) + min(10, signal_close_pos * 10)
                direction = "LONG"
                max_hold = 8
                decision_reason = "daily_proxy_trade_now: next_day_confirmation"
            else:
                recent_high = max(b["high"] for b in bars[max(0, idx - 7):idx + 1])
                pullback_from_high = ((close - recent_high) / recent_high) * 100 if recent_high > 0 else 0
                red_day = close < bars[idx]["open"]
                broke_prev_low = close < bars[idx - 1]["low"]
                signal_bar = bars[idx]
                signal_range = max(signal_bar["high"] - signal_bar["low"], close * 0.01)
                signal_close_pos = (close - signal_bar["low"]) / signal_range
                if not (change_7d >= 50 or change_3d >= 28):
                    continue
                if pullback_from_high > -10 or pullback_from_high < -38:
                    continue
                if not (red_day and broke_prev_low and signal_close_pos <= 0.35):
                    continue
                if volume_ratio < 1.3:
                    continue
                entry_idx = idx + 1
                next_bar = bars[entry_idx]
                entry = next_bar["open"]
                pre_risk = max(atr * 1.6, close * 0.07)
                if entry < close - pre_risk * 0.65 or entry > recent_high:
                    continue
                if next_bar["close"] > next_bar["open"] and next_bar["close"] > close:
                    continue
                stop_distance = max(atr * 1.6, entry * 0.07)
                stop = max(recent_high * 1.02, entry + stop_distance)
                risk = stop - entry
                if risk <= 0 or (risk / entry) > 0.28:
                    continue
                tp1 = entry - risk * 1.6
                tp2 = entry - risk * 2.8
                score = 52 + min(18, change_7d / 4) + min(14, abs(pullback_from_high)) + min(12, volume_ratio * 3)
                direction = "SHORT"
                max_hold = 6
                decision_reason = "daily_proxy_trade_now: crack_confirmed"

            sim = _simulate_crypto_trade(bars, entry_idx, direction.lower(), entry, stop, tp1, tp2, max_hold)
            if not sim:
                continue
            total_signals += 1
            cooldown_until = idx + 7
            sim.update({
                "ticker": symbol,
                "symbol": symbol,
                "coin_id": coin_id,
                "signal_date": bars[idx]["date"],
                "direction": direction,
                "grade": _crypto_signal_grade(score),
                "score": round(score, 1),
                "change_1d": round(change_1d, 2),
                "change_7d": round(change_7d, 2),
                "rvol": round(volume_ratio, 2),
                "entry_target": entry,
                "stop_target": stop,
                "tp1_target": tp1,
                "tp2_target": tp2,
                "decision": "TRADE_NOW",
                "decision_reason": decision_reason,
            })
            trades.append(sim)

        _backtest_progress_update(
            job_id,
            "running",
            0.08 + coin_idx / total_universe * 0.84,
            f"Crypto {coin_idx}/{len(universe)} erledigt - {total_signals} Signale gefunden",
            strategy=strategy,
            total_items=len(universe),
            done_items=coin_idx,
            current_item=symbol or coin_id,
            signals_found=total_signals,
        )

    _backtest_progress_update(job_id, "running", 0.96, "Crypto-Kennzahlen werden berechnet...", strategy=strategy, signals_found=total_signals)
    return _build_backtest_result(
        strategy=strategy,
        label=meta["name"],
        direction=meta["direction"],
        months=months,
        trades=trades,
        total_signals=total_signals,
        no_fill=0,
        n_tickers=len(universe),
        note=meta["note"],
        crypto=True,
    )


def _calc_ema_series(data, period):
    """Calculate EMA series from price data."""
    if len(data) < period:
        return []
    emas = [sum(data[:period]) / period]
    k = 2 / (period + 1)
    for val in data[period:]:
        emas.append(val * k + emas[-1] * (1 - k))
    return emas


def _backtest_stats(trades, ticker, strategy, months):
    """Calculate backtest statistics from a list of trades."""
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "ticker": ticker, "strategy": strategy, "months": months,
            "total_trades": 0, "win_rate": 0, "avg_pnl": 0, "total_return": 0,
            "max_drawdown": 0, "avg_win": 0, "avg_loss": 0, "best_trade": 0,
            "profit_factor": 0, "avg_r": 0,
            "verdict": _bt_backtest_verdict(0, 0, 0, 0, 0, 0, 0),
            "worst_trade": 0, "trades": [], "timestamp": datetime.now().isoformat(),
        }
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses_list = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = round(len(wins) / total_trades * 100, 1)
    avg_pnl = round(sum(t["pnl_pct"] for t in trades) / total_trades, 2)
    total_return = round(sum(t["pnl_pct"] for t in trades), 2)
    avg_win = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t["pnl_pct"] for t in losses_list) / len(losses_list), 2) if losses_list else 0
    best_trade = round(max(t["pnl_pct"] for t in trades), 2)
    worst_trade = round(min(t["pnl_pct"] for t in trades), 2)
    gross_profit = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses_list))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0)
    avg_r = round(sum(_bt_float(t.get("r_multiple")) for t in trades) / total_trades, 2) if any("r_multiple" in t for t in trades) else 0
    # Max Drawdown als % des Equity-Peaks (nicht in Prozentpunkten)
    max_dd = 0
    peak = 100  # Start-Equity = 100%
    equity = 100
    for t in trades:
        equity *= (1 + t["pnl_pct"] / 100)  # Compound
        if equity > peak:
            peak = equity
        dd_pct = ((peak - equity) / peak) * 100 if peak > 0 else 0
        if dd_pct > max_dd:
            max_dd = dd_pct
    return {
        "ticker": ticker, "strategy": strategy, "months": months,
        "total_trades": total_trades, "win_rate": win_rate, "avg_pnl": avg_pnl,
        "total_return": total_return, "max_drawdown": round(max_dd, 2),
        "avg_win": avg_win, "avg_loss": avg_loss, "best_trade": best_trade,
        "worst_trade": worst_trade, "profit_factor": profit_factor, "avg_r": avg_r,
        "verdict": _bt_backtest_verdict(total_trades, win_rate, avg_pnl, profit_factor, avg_r, round(max_dd, 2), total_return),
        "trades": trades[-50:],
        "timestamp": datetime.now().isoformat(),
    }


def _make_trade(entry_date, entry_price, exit_date, exit_price, direction="long"):
    """Create a trade record with PnL calculation inkl. Trading-Fees.
    Fees: 0.1% pro Seite (Entry + Exit) = 0.2% Roundtrip — typisch für Broker."""
    FEE_PCT = 0.1  # 0.1% pro Trade (Entry + Exit = 0.2% total)
    if direction == "short":
        pnl_raw = ((entry_price - exit_price) / entry_price) * 100
    else:
        pnl_raw = ((exit_price - entry_price) / entry_price) * 100
    pnl = pnl_raw - (2 * FEE_PCT)  # Entry + Exit Fee abziehen
    return {
        "entry_date": entry_date, "entry_price": round(entry_price, 2),
        "exit_date": exit_date, "exit_price": round(exit_price, 2),
        "pnl_pct": round(pnl, 2), "pnl_raw": round(pnl_raw, 2), "type": direction.upper(),
    }


def _run_backtest(ticker: str, strategy: str, months: int) -> Dict:
    """Run backtest — supports indicator strategies + all BACKTEST_STRATEGY_RULES."""
    try:
        # Fetch daily bars from Polygon
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2024-01-01/2099-12-31"
        resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": months * 22 + 60, "sort": "desc"})
        if resp.status_code != 200:
            return {"error": f"Keine Daten fuer {ticker}"}
        bars = resp.json().get("results", [])
        if len(bars) < 60:
            return {"error": f"Zu wenige Daten fuer {ticker} ({len(bars)} Bars)"}

        # Reverse to chronological
        bars = list(reversed(bars))
        opens = [b["o"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        closes = [b["c"] for b in bars]
        volumes = [b.get("v", 0) for b in bars]
        dates = [datetime.fromtimestamp(b["t"] / 1000).strftime("%Y-%m-%d") for b in bars]

        trades = []
        position = None

        # ══════════════════════════════════════════════════════════
        # INDICATOR-BASED STRATEGIES
        # ══════════════════════════════════════════════════════════

        if strategy == "sma_crossover":
            for i in range(51, len(closes)):
                sma20 = sum(closes[i-20:i]) / 20
                sma50 = sum(closes[i-50:i]) / 50
                prev_sma20 = sum(closes[i-21:i-1]) / 20
                prev_sma50 = sum(closes[i-51:i-1]) / 50
                if position is None:
                    if prev_sma20 <= prev_sma50 and sma20 > sma50:
                        position = {"entry_date": dates[i], "entry_price": closes[i]}
                else:
                    if prev_sma20 >= prev_sma50 and sma20 < sma50:
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i]))
                        position = None

        elif strategy == "rsi_mean_reversion":
            for i in range(15, len(closes)):
                gains, losses = [], []
                for j in range(14):
                    diff = closes[i-j] - closes[i-j-1]
                    (gains if diff > 0 else losses).append(abs(diff))
                avg_gain = sum(gains) / 14 if gains else 0.001
                avg_loss = sum(losses) / 14 if losses else 0.001
                rsi = 100 - (100 / (1 + avg_gain / avg_loss))
                if position is None:
                    if rsi < 30:
                        position = {"entry_date": dates[i], "entry_price": closes[i]}
                else:
                    if rsi > 70:
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i]))
                        position = None

        elif strategy == "ema_crossover":
            if len(closes) > 21:
                ema9_full = _calc_ema_series(closes, 9)
                ema21_full = _calc_ema_series(closes, 21)
                # Align both to same index: ema9 starts at idx 8, ema21 at idx 20
                offset = 21 - 9  # = 12
                for i in range(1, len(ema21_full)):
                    e9_idx = i + offset
                    if e9_idx >= len(ema9_full) or e9_idx < 1:
                        continue
                    bar_idx = 20 + i
                    if bar_idx >= len(dates):
                        break
                    if position is None:
                        if ema9_full[e9_idx] > ema21_full[i] and ema9_full[e9_idx - 1] <= ema21_full[i - 1]:
                            position = {"entry_date": dates[bar_idx], "entry_price": closes[bar_idx]}
                    else:
                        if ema9_full[e9_idx] < ema21_full[i] and ema9_full[e9_idx - 1] >= ema21_full[i - 1]:
                            trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[bar_idx], closes[bar_idx]))
                            position = None

        elif strategy == "macd":
            if len(closes) > 35:
                ema12 = _calc_ema_series(closes, 12)
                ema26 = _calc_ema_series(closes, 26)
                # MACD = EMA12 - EMA26, aligned to ema26 start (idx 25)
                offset = 26 - 12  # = 14
                macd_line = []
                for i in range(len(ema26)):
                    e12_idx = i + offset
                    if e12_idx < len(ema12):
                        macd_line.append(ema12[e12_idx] - ema26[i])
                if len(macd_line) > 9:
                    signal_line = _calc_ema_series(macd_line, 9)
                    sig_offset = 9
                    for i in range(1, len(signal_line)):
                        m_idx = i + sig_offset - 1
                        if m_idx >= len(macd_line) or m_idx < 1:
                            continue
                        bar_idx = 25 + m_idx + 1
                        if bar_idx >= len(dates):
                            continue
                        if position is None:
                            if macd_line[m_idx] > signal_line[i] and macd_line[m_idx - 1] <= signal_line[i - 1]:
                                position = {"entry_date": dates[bar_idx], "entry_price": closes[bar_idx]}
                        else:
                            if macd_line[m_idx] < signal_line[i] and macd_line[m_idx - 1] >= signal_line[i - 1]:
                                trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[bar_idx], closes[bar_idx]))
                                position = None

        elif strategy == "bollinger_bands":
            period = 20
            for i in range(period, len(closes)):
                window = closes[i-period:i]
                sma = sum(window) / period
                std = (sum((x - sma)**2 for x in window) / period) ** 0.5
                if std < 0.001:
                    continue  # Keine Volatilität = kein Signal
                upper = sma + 2 * std
                lower = sma - 2 * std
                if position is None:
                    # Long: Preis unter Lower Band
                    if closes[i] <= lower:
                        position = {"entry_date": dates[i], "entry_price": closes[i], "dir": "long", "bar_idx": i}
                    # Short: Preis über Upper Band
                    elif closes[i] >= upper:
                        position = {"entry_date": dates[i], "entry_price": closes[i], "dir": "short", "bar_idx": i}
                else:
                    bars_held = i - position.get("bar_idx", i)
                    if position["dir"] == "long" and closes[i] >= sma:
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i], "long"))
                        position = None
                    elif position["dir"] == "short" and closes[i] <= sma:
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i], "short"))
                        position = None
                    elif bars_held >= 20:
                        # Max-Hold Timeout: verhindert infinite holding in Trends
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i], position["dir"]))
                        position = None

        elif strategy == "mean_reversion_sma":
            # Buy when price drops >5% below SMA50, sell when back above SMA50
            for i in range(50, len(closes)):
                sma50 = sum(closes[i-50:i]) / 50
                pct_from_sma = ((closes[i] - sma50) / sma50) * 100
                if position is None:
                    if pct_from_sma < -5:
                        position = {"entry_date": dates[i], "entry_price": closes[i]}
                else:
                    if closes[i] > sma50:
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i]))
                        position = None

        # ══════════════════════════════════════════════════════════
        # TURTLE TRADING (Richard Dennis, 1983)
        # Donchian Channel Breakout + ATR-based Stop + Trail Exit
        # ══════════════════════════════════════════════════════════

        elif strategy == "turtle_breakout":
            # ── Original Turtle System 1 (Richard Dennis, 1983) ──
            # Donchian Channel Breakout + ATR(20) EMA + Previous-Breakout-Filter
            donchian_entry = 20  # Entry: break above 20-day high
            donchian_exit = 10   # Exit: break below 10-day low
            atr_period = 20
            atr_stop_mult = 2.0  # Stop-Loss = 2× N (ATR)

            # ── Pre-compute ATR(20) als EMA (Original Turtle "N") ──
            # N = ((19 × prev_N) + TR_today) / 20
            atr_arr = [0.0] * len(closes)
            for k in range(1, len(closes)):
                tr = max(
                    highs[k] - lows[k],
                    abs(highs[k] - closes[k - 1]),
                    abs(lows[k] - closes[k - 1]),
                )
                if k < atr_period:
                    # Seed: simple average for first atr_period bars
                    atr_arr[k] = tr
                elif k == atr_period:
                    seed_sum = sum(
                        max(highs[j] - lows[j],
                            abs(highs[j] - closes[j - 1]),
                            abs(lows[j] - closes[j - 1]))
                        for j in range(1, atr_period + 1)
                    )
                    atr_arr[k] = seed_sum / atr_period
                else:
                    # EMA smoothing: N = (19 × prev_N + TR) / 20
                    atr_arr[k] = (19.0 * atr_arr[k - 1] + tr) / 20.0

            # Track previous breakout outcome for System 1 filter
            last_breakout_profitable = False

            for i in range(donchian_entry + 1, len(closes)):
                # Donchian Channel High (20-Tage) — ohne aktuellen Tag
                dc_high = max(highs[i - donchian_entry:i])
                # Donchian Channel Low (10-Tage) für Exit
                dc_low_exit = min(lows[max(0, i - donchian_exit):i])

                atr = atr_arr[i]

                if position is None:
                    # ENTRY: Close durchbricht 20-Tage-Hoch
                    if closes[i] > dc_high and atr > 0:
                        # System 1 Filter: Skip wenn letzter Breakout profitabel war
                        if last_breakout_profitable:
                            last_breakout_profitable = False  # Reset — nächster gilt wieder
                            continue

                        # Entry-Preis = Donchian-Breakout-Level (dc_high), nicht Close
                        entry_price = dc_high
                        stop_price = entry_price - atr_stop_mult * atr
                        position = {
                            "entry_date": dates[i],
                            "entry_price": entry_price,
                            "stop": stop_price,
                            "entry_atr": atr,  # ATR zum Zeitpunkt des Einstiegs
                        }
                else:
                    # EXIT-Bedingungen prüfen (Stop oder Donchian-Exit)
                    stop_price = position["stop"]
                    entry_atr = position["entry_atr"]

                    # Stop-Loss getroffen (Intraday Low)
                    if lows[i] <= stop_price:
                        exit_price = stop_price  # Ausführung am Stop
                        pnl = exit_price - position["entry_price"]
                        last_breakout_profitable = (pnl > 0)
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], exit_price))
                        position = None
                    # Donchian Exit: Close unter 10-Tage-Tief
                    elif closes[i] < dc_low_exit:
                        pnl = closes[i] - position["entry_price"]
                        last_breakout_profitable = (pnl > 0)
                        trades.append(_make_trade(position["entry_date"], position["entry_price"], dates[i], closes[i]))
                        position = None
                    else:
                        # Trailing Stop mit Entry-ATR (nicht aktuellem ATR)
                        new_stop = closes[i] - atr_stop_mult * entry_atr
                        if new_stop > position["stop"]:
                            position["stop"] = new_stop

        # ══════════════════════════════════════════════════════════
        # RULE-BASED STRATEGIES (from BACKTEST_STRATEGY_RULES)
        # ══════════════════════════════════════════════════════════

        elif strategy in BACKTEST_RULES:
            rule = BACKTEST_RULES[strategy]
            sig = rule["signal"]
            direction = rule.get("direction", "long")
            stop_pct = rule.get("stop_pct", 0.05)
            tp1_rr = rule.get("tp1_rr", 1.5)
            max_hold = rule.get("max_hold_days", 5)
            entry_type = rule.get("entry", "next_open")
            min_price = rule.get("min_price", 1.0)

            # Pre-calc RVOL (20d avg volume) — init with 1.0 to avoid div-by-zero
            avg_vols = [1.0] * len(bars)
            for i in range(20, len(bars)):
                avg_vols[i] = sum(volumes[i-20:i]) / 20 if sum(volumes[i-20:i]) > 0 else 1

            for i in range(2, len(bars) - 1):
                if position is not None:
                    # ── MANAGE OPEN POSITION: Stop/TP/MaxHold ──
                    days_held = position.get("days_held", 0) + 1
                    position["days_held"] = days_held
                    entry_p = position["entry_price"]
                    risk = abs(entry_p * stop_pct)

                    if direction == "short":
                        # Short: stop above entry, TP below
                        stop_price = entry_p * (1 + stop_pct)
                        tp_price = entry_p - risk * tp1_rr
                        if highs[i] >= stop_price:
                            trades.append(_make_trade(position["entry_date"], entry_p, dates[i], stop_price, "short"))
                            position = None
                            continue
                        if lows[i] <= tp_price:
                            trades.append(_make_trade(position["entry_date"], entry_p, dates[i], tp_price, "short"))
                            position = None
                            continue
                    else:
                        # Long: stop below entry, TP above
                        stop_price = entry_p * (1 - stop_pct)
                        tp_price = entry_p + risk * tp1_rr
                        if lows[i] <= stop_price:
                            trades.append(_make_trade(position["entry_date"], entry_p, dates[i], stop_price, "long"))
                            position = None
                            continue
                        if highs[i] >= tp_price:
                            trades.append(_make_trade(position["entry_date"], entry_p, dates[i], tp_price, "long"))
                            position = None
                            continue

                    # Max hold days → exit at close
                    if days_held >= max_hold:
                        trades.append(_make_trade(position["entry_date"], entry_p, dates[i], closes[i], direction))
                        position = None
                    continue

                # ── CHECK SIGNAL CONDITIONS ──
                if closes[i] < min_price:
                    continue

                c = closes[i]
                o = opens[i]
                h = highs[i]
                lo = lows[i]
                prev_c = closes[i - 1]
                change_pct = ((c - prev_c) / prev_c) * 100 if prev_c else 0
                close_pos = (c - lo) / (h - lo) if (h - lo) > 0 else 0.5

                # Gap %
                gap_pct = ((o - prev_c) / prev_c) * 100 if prev_c else 0

                # Prev day change %
                prev_change_pct = ((prev_c - closes[i - 2]) / closes[i - 2]) * 100 if i >= 2 and closes[i - 2] else 0

                # RVOL
                rvol = volumes[i] / avg_vols[i] if avg_vols[i] > 0 else 1.0

                # Check all signal conditions
                match = True
                if "change_pct_min" in sig and change_pct < sig["change_pct_min"]:
                    match = False
                if "change_pct_max" in sig and change_pct > sig["change_pct_max"]:
                    match = False
                if "close_pos_min" in sig and close_pos < sig["close_pos_min"]:
                    match = False
                if "close_pos_max" in sig and close_pos > sig["close_pos_max"]:
                    match = False
                if "gap_pct_min" in sig and gap_pct < sig["gap_pct_min"]:
                    match = False
                if "gap_pct_max" in sig and gap_pct > sig["gap_pct_max"]:
                    match = False
                if "prev_change_pct_min" in sig and prev_change_pct < sig["prev_change_pct_min"]:
                    match = False
                if "prev_change_pct_max" in sig and prev_change_pct > sig["prev_change_pct_max"]:
                    match = False
                if "rvol_min" in sig and rvol < sig["rvol_min"]:
                    match = False
                if "rvol_max" in sig and rvol > sig["rvol_max"]:
                    match = False

                if not match:
                    continue

                # ── ENTRY ──
                if entry_type == "next_open" and i + 1 < len(bars):
                    position = {"entry_date": dates[i + 1], "entry_price": opens[i + 1], "days_held": 0}
                elif entry_type == "at_close":
                    position = {"entry_date": dates[i], "entry_price": closes[i], "days_held": 0}
                elif entry_type == "prev_high":
                    # Entry only if next day breaks prev high
                    if i + 1 < len(bars) and highs[i + 1] > highs[i]:
                        position = {"entry_date": dates[i + 1], "entry_price": highs[i], "days_held": 0}

        else:
            return {"error": f"Unbekannte Strategie: {strategy}"}

        # Close any open position at last bar
        if position is not None:
            direction = BACKTEST_RULES.get(strategy, {}).get("direction", "long")
            t = _make_trade(position["entry_date"], position["entry_price"], dates[-1], closes[-1], direction)
            t["type"] += " (offen)"
            trades.append(t)

        return _backtest_stats(trades, ticker, strategy, months)

    except Exception as e:
        return {"error": str(e), "ticker": ticker, "strategy": strategy}


@app.post("/api/run-backtest")
def run_backtest(request: BacktestRequest):
    """Run a backtest for a ticker with given strategy."""
    job_id = _backtest_progress_key(request.job_id)
    request.job_id = job_id
    _backtest_progress_update(job_id, "running", 0.01, "Backtest gestartet...", strategy=request.strategy)

    try:
        if request.strategy in ADVANCED_SCANNER_BACKTESTS:
            result = _run_advanced_scanner_backtest(request)
        elif request.strategy in CRYPTO_BACKTESTS:
            result = _run_crypto_backtest(request)
        else:
            if not POLYGON_KEY:
                raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
            if not str(request.ticker or "").strip():
                raise HTTPException(status_code=400, detail="Ticker ist fuer diese Strategie erforderlich")
            _backtest_progress_update(job_id, "running", 0.15, f"Daten fuer {request.ticker.upper()} werden geladen...", strategy=request.strategy)
            result = _run_backtest(request.ticker.upper(), request.strategy, request.months)
            _backtest_progress_update(job_id, "running", 0.9, "Einzel-Ticker Kennzahlen werden berechnet...", strategy=request.strategy)
    except Exception as exc:
        _backtest_progress_update(job_id, "error", 1.0, f"Backtest fehlgeschlagen: {exc}", strategy=request.strategy, error=str(exc))
        raise

    # Cache result
    try:
        safe_ticker = re.sub(r"[^A-Za-z0-9_-]", "_", str(request.ticker or "UNIVERSE").upper()) or "UNIVERSE"
        safe_strategy = re.sub(r"[^A-Za-z0-9_-]", "_", str(request.strategy or "unknown"))
        cache_key = f"/tmp/backtest_{safe_ticker}_{safe_strategy}.json"
        with open(cache_key, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "results": result}, f, default=_serialize_json)
    except Exception as e:
        print(f"[Warning] {e}")

    result["job_id"] = job_id
    if result.get("error"):
        _backtest_progress_update(job_id, "error", 1.0, str(result.get("error")), strategy=request.strategy, error=result.get("error"))
    else:
        _backtest_progress_update(
            job_id,
            "success",
            1.0,
            f"Fertig: {result.get('total_trades', 0)} Trades, {result.get('total_signals', result.get('total_trades', 0))} Signale",
            strategy=request.strategy,
            total_trades=result.get("total_trades", 0),
            total_signals=result.get("total_signals", result.get("total_trades", 0)),
        )
    return result


@app.get("/api/backtest-progress")
def get_backtest_progress(job_id: str = Query(...)):
    """Get progress for a running backtest job."""
    return _backtest_progress_read(job_id)


@app.get("/api/backtest-strategies")
def list_backtest_strategies():
    """List all available backtest strategies."""
    indicator_strats = [
        {"id": "sma_crossover", "name": "SMA Crossover (20/50)", "category": "Indikator", "direction": "long", "requires_ticker": True},
        {"id": "ema_crossover", "name": "EMA Crossover (9/21)", "category": "Indikator", "direction": "long", "requires_ticker": True},
        {"id": "rsi_mean_reversion", "name": "RSI Mean Reversion", "category": "Indikator", "direction": "long", "requires_ticker": True},
        {"id": "macd", "name": "MACD Crossover", "category": "Indikator", "direction": "long", "requires_ticker": True},
        {"id": "bollinger_bands", "name": "Bollinger Bands", "category": "Indikator", "direction": "long", "requires_ticker": True},
        {"id": "mean_reversion_sma", "name": "Mean Reversion (SMA50)", "category": "Indikator", "direction": "long", "requires_ticker": True},
        {"id": "turtle_breakout", "name": "Turtle Breakout (Donchian 20/10)", "category": "Indikator", "direction": "long", "requires_ticker": True},
    ]
    scanner_strats = [
        {
            "id": sid,
            "name": meta["name"],
            "category": meta["category"],
            "direction": meta["direction"],
            "requires_ticker": False,
            "default_max_tickers": meta.get("default_max_tickers"),
            "default_min_price": meta.get("default_min_price"),
            "default_min_volume": meta.get("default_min_volume"),
            "note": meta.get("note", ""),
        }
        for sid, meta in ADVANCED_SCANNER_BACKTESTS.items()
    ]
    crypto_strats = [
        {
            "id": sid,
            "name": meta["name"],
            "category": meta["category"],
            "direction": meta["direction"],
            "requires_ticker": False,
            "default_max_tickers": meta.get("default_max_tickers"),
            "note": meta.get("note", ""),
        }
        for sid, meta in CRYPTO_BACKTESTS.items()
    ]
    rule_strats = []
    for name, rule in BACKTEST_RULES.items():
        rule_strats.append({
            "id": name,
            "name": name,
            "category": "Single-Ticker Scanner-Regeln",
            "direction": rule.get("direction", "long"),
            "requires_ticker": True,
        })
    return {"strategies": scanner_strats + crypto_strats + indicator_strats + rule_strats}


@app.get("/api/backtest-results")
def get_backtest_results(ticker: str = Query("AAPL"), strategy: str = Query("sma_crossover")):
    """Get cached backtest results."""
    safe_ticker = re.sub(r"[^A-Za-z0-9_-]", "_", str(ticker or "UNIVERSE").upper()) or "UNIVERSE"
    safe_strategy = re.sub(r"[^A-Za-z0-9_-]", "_", str(strategy or "unknown"))
    cache_key = f"/tmp/backtest_{safe_ticker}_{safe_strategy}.json"
    if Path(cache_key).exists():
        try:
            with open(cache_key, "r") as f:
                data = json.load(f)
            return {"status": "success", "data": data.get("results", {}), "cached_at": data.get("cached_at")}
        except Exception as e:
            print(f"[Warning] {e}")
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


# ── Auto-Trader Endpoints ──
_autotrader_thread = None

class AutotraderConfigUpdate(BaseModel):
    config: dict

@app.get("/api/autotrader/status")
def autotrader_status():
    """Get Auto-Trader status, config, and recent log."""
    state = _autotrader_state_read()
    config = _autotrader_config_load()
    market_hours = _autotrader_is_market_hours()
    # Read log
    log_entries = []
    try:
        import json as _json
        with open("/tmp/alpha_autotrader_log.json", "r") as f:
            log_entries = _json.load(f)
    except Exception:
        pass
    return {
        "state": state,
        "config": config,
        "market_hours": market_hours,
        "thread_alive": _autotrader_thread is not None and _autotrader_thread.is_alive() if _autotrader_thread else False,
        "log": log_entries[-50:],  # Last 50 entries
    }

@app.post("/api/autotrader/config")
def autotrader_update_config(body: AutotraderConfigUpdate):
    """Update Auto-Trader configuration."""
    config = _autotrader_config_load()
    config.update(body.config)
    _autotrader_config_save(config)
    _autotrader_log(f"Config aktualisiert: {list(body.config.keys())}", "INFO")
    return {"ok": True, "config": config}

@app.post("/api/autotrader/start")
def autotrader_start():
    """Start the Auto-Trader background loop."""
    global _autotrader_thread
    if _autotrader_thread and _autotrader_thread.is_alive():
        return {"ok": False, "error": "Auto-Trader läuft bereits"}
    poly_key = os.environ.get("POLYGON_KEY", "")
    if not poly_key:
        return {"ok": False, "error": "Polygon API Key fehlt"}
    from modules.scanners import _autotrader_clear_stop
    _autotrader_clear_stop()
    _autotrader_thread = threading.Thread(target=autotrader_background_loop, args=(poly_key,), daemon=True)
    _autotrader_thread.start()
    _autotrader_log("Auto-Trader gestartet via API", "INFO")
    return {"ok": True, "message": "Auto-Trader gestartet"}

@app.post("/api/autotrader/stop")
def autotrader_stop():
    """Stop the Auto-Trader background loop."""
    _autotrader_request_stop()
    state = _autotrader_state_read()
    state["status"] = "stopped"
    _autotrader_state_write(state)
    _autotrader_log("Auto-Trader gestoppt via API", "INFO")
    return {"ok": True, "message": "Auto-Trader wird gestoppt"}

@app.post("/api/autotrader/scan-once")
def autotrader_run_single_scan():
    """Run a single Auto-Trader scan (not background loop)."""
    poly_key = os.environ.get("POLYGON_KEY", "")
    if not poly_key:
        return {"ok": False, "error": "Polygon API Key fehlt"}
    result = autotrader_scan_once(poly_key)
    return {"ok": True, "result": result}

@app.post("/api/autotrader/clear-positions")
def autotrader_clear_positions():
    """Clear all tracked positions (does NOT close actual broker positions)."""
    state = _autotrader_state_read()
    state["positions"] = []
    state["trades_today"] = 0
    state["daily_pnl"] = 0
    _autotrader_state_write(state)
    _autotrader_log("Positionen zurückgesetzt via API", "INFO")
    return {"ok": True}


# ── Admin System ──

def _require_admin(authorization: Optional[str]):
    """
    Helper: Extract token, verify it, check admin status.
    Returns (payload, email) on success, raises HTTPException(403) if not admin.
    """
    if not authorization:
        raise HTTPException(status_code=403, detail="Missing Authorization header")

    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=403, detail="Invalid Authorization header format")

    token = parts[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    email = payload.get("email", "")
    if email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")

    return payload, email


_ADMIN_DATA_DIR = Path(os.environ.get("ALPHA_DATA_DIR", Path(__file__).parent / "data_cache")) / "auth"
_ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
_COUPON_PATH = _ADMIN_DATA_DIR / "alpha_station_coupons.json"
_TICKET_PATH = _ADMIN_DATA_DIR / "alpha_station_tickets.json"


def _load_coupons() -> Dict:
    """Load coupons from JSON file."""
    coupon_path = _COUPON_PATH
    if os.path.exists(coupon_path):
        try:
            with open(coupon_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"coupons": []}
    return {"coupons": []}


def _save_coupons(data: Dict):
    """Save coupons to JSON file."""
    try:
        coupon_path = _COUPON_PATH
        with open(coupon_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Error] Coupons speichern fehlgeschlagen: {e}")


def _load_tickets() -> Dict:
    """Load support tickets from JSON file."""
    ticket_path = _TICKET_PATH
    if os.path.exists(ticket_path):
        try:
            with open(ticket_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"tickets": [], "next_id": 1}
    return {"tickets": [], "next_id": 1}


def _save_tickets(data: Dict):
    """Save support tickets to JSON file."""
    try:
        ticket_path = _TICKET_PATH
        with open(ticket_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Error] Tickets speichern fehlgeschlagen: {e}")


# ── Admin: User Management ──

@app.get("/api/admin/users")
def admin_list_users(authorization: Optional[str] = Header(None)):
    """List all users (admin only)."""
    _require_admin(authorization)

    db = _load_users()
    users = []

    for email, user_data in db.get("users", {}).items():
        users.append({
            "email": email,
            "name": user_data.get("name", ""),
            "plan": user_data.get("plan", "trial"),
            "created_at": user_data.get("created_at", ""),
            "last_login": user_data.get("last_login", ""),
            "trial_ends_at": user_data.get("trial_ends_at", ""),
            "stripe_customer_id": user_data.get("stripe_customer_id", ""),
            "is_admin": email in ADMIN_EMAILS,
        })

    return {"users": users}


@app.put("/api/admin/users/{email}/plan")
def admin_update_user_plan(email: str, req_body: PlanUpdateRequest, authorization: Optional[str] = Header(None)):
    """Update a user's plan manually (admin only)."""
    _require_admin(authorization)

    email = email.lower().strip()
    plan = req_body.plan.lower().strip()

    # Validate plan
    valid_plans = ["trial", "expired", "basic", "pro", "elite"]
    if plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Must be one of: {', '.join(valid_plans)}")

    # Load and update database
    db = _load_users()

    if email not in db.get("users", {}):
        raise HTTPException(status_code=404, detail="User not found")

    db["users"][email]["plan"] = plan

    _save_users(db)
    return {"success": True, "message": f"Plan für {email} auf {plan} aktualisiert"}


@app.delete("/api/admin/users/{email}")
def admin_delete_user(email: str, authorization: Optional[str] = Header(None)):
    """Delete a user from database (admin only)."""
    _require_admin(authorization)

    email = email.lower().strip()

    # Load database
    db = _load_users()

    if email not in db.get("users", {}):
        raise HTTPException(status_code=404, detail="User not found")

    del db["users"][email]

    _save_users(db)
    return {"success": True, "message": f"Benutzer {email} gelöscht"}


@app.get("/api/admin/stats")
def admin_get_stats(authorization: Optional[str] = Header(None)):
    """Get admin statistics (admin only)."""
    _require_admin(authorization)

    db = _load_users()
    users = db.get("users", {})

    # Basic counts
    total_users = len(users)
    users_by_plan = {}
    for plan in ["trial", "expired", "basic", "pro", "elite"]:
        users_by_plan[plan] = 0

    new_today = 0
    new_this_week = 0
    active_today = 0
    estimated_mrr = 0

    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")
    week_ago = now - timedelta(days=7)

    plan_prices = {"basic": 29, "pro": 79, "elite": 149, "trial": 0, "expired": 0}

    for email, user_data in users.items():
        plan = user_data.get("plan", "trial")
        users_by_plan[plan] = users_by_plan.get(plan, 0) + 1

        # Created today
        created_at = user_data.get("created_at", "")
        if created_at.startswith(today_str):
            new_today += 1

        # Created this week
        if created_at:
            try:
                created_date = datetime.fromisoformat(created_at.split("T")[0])
                if created_date >= week_ago:
                    new_this_week += 1
            except Exception:
                pass

        # Active today
        last_login = user_data.get("last_login", "")
        if last_login.startswith(today_str):
            active_today += 1

        # MRR (paying plans only)
        if plan in ["basic", "pro", "elite"]:
            estimated_mrr += plan_prices.get(plan, 0)

    return {
        "total_users": total_users,
        "users_by_plan": users_by_plan,
        "new_today": new_today,
        "new_this_week": new_this_week,
        "active_today": active_today,
        "estimated_mrr": estimated_mrr,
    }


@app.get("/api/admin/logs")
def admin_get_logs(authorization: Optional[str] = Header(None)):
    """Get last 200 lines from scanner log (admin only)."""
    _require_admin(authorization)

    log_path = "/tmp/alpha_station_scanner.log"
    lines = []

    if os.path.exists(log_path):
        try:
            with open(log_path, "r") as f:
                all_lines = f.readlines()
                # Get last 200 lines
                lines = [line.rstrip("\n") for line in all_lines[-200:]]
        except Exception as e:
            lines = [f"Fehler beim Lesen der Log-Datei: {str(e)}"]

    return {"logs": lines}


# ── Admin: Coupon Management ──

@app.post("/api/admin/coupons")
def admin_create_coupon(req_body: CouponCreateRequest, authorization: Optional[str] = Header(None)):
    """Create a new coupon (admin only)."""
    payload, admin_email = _require_admin(authorization)

    code = req_body.code.upper().strip()
    plan = req_body.plan.lower().strip()

    # Validate inputs
    valid_plans = ["trial", "basic", "pro", "elite"]
    if plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Must be one of: {', '.join(valid_plans)}")

    if req_body.duration_days < 1:
        raise HTTPException(status_code=400, detail="duration_days must be >= 1")

    if req_body.max_uses < 1:
        raise HTTPException(status_code=400, detail="max_uses must be >= 1")

    # Load existing coupons
    data = _load_coupons()

    # Check for duplicate
    for coupon in data.get("coupons", []):
        if coupon["code"] == code:
            raise HTTPException(status_code=400, detail="Coupon code already exists")

    # Create coupon
    coupon = {
        "code": code,
        "plan": plan,
        "duration_days": req_body.duration_days,
        "max_uses": req_body.max_uses,
        "uses": 0,
        "created_at": datetime.utcnow().isoformat(),
        "created_by": admin_email,
        "description": req_body.description,
        "active": True,
    }

    data["coupons"].append(coupon)
    _save_coupons(data)

    return {"success": True, "coupon": coupon}


@app.get("/api/admin/coupons")
def admin_list_coupons(authorization: Optional[str] = Header(None)):
    """List all coupons (admin only)."""
    _require_admin(authorization)

    data = _load_coupons()
    return {"coupons": data.get("coupons", [])}


@app.put("/api/admin/coupons/{code}/toggle")
def admin_toggle_coupon(code: str, authorization: Optional[str] = Header(None)):
    """Toggle coupon active/inactive (admin only)."""
    _require_admin(authorization)

    code = code.upper().strip()
    data = _load_coupons()

    for coupon in data.get("coupons", []):
        if coupon["code"] == code:
            coupon["active"] = not coupon["active"]
            _save_coupons(data)
            return {"success": True, "coupon": coupon}

    raise HTTPException(status_code=404, detail="Coupon not found")


@app.delete("/api/admin/coupons/{code}")
def admin_delete_coupon(code: str, authorization: Optional[str] = Header(None)):
    """Delete a coupon (admin only)."""
    _require_admin(authorization)

    code = code.upper().strip()
    data = _load_coupons()

    original_count = len(data.get("coupons", []))
    data["coupons"] = [c for c in data.get("coupons", []) if c["code"] != code]

    if len(data["coupons"]) == original_count:
        raise HTTPException(status_code=404, detail="Coupon not found")

    _save_coupons(data)
    return {"success": True, "message": f"Coupon {code} gelöscht"}


# ── Coupon Redemption (any authenticated user) ──

@app.post("/api/redeem-coupon")
def redeem_coupon(req_body: RedeemCouponRequest, authorization: Optional[str] = Header(None)):
    """Redeem a coupon code (any authenticated user)."""
    if not authorization:
        raise HTTPException(status_code=403, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=403, detail="Invalid Authorization header format")

    token = parts[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    user_email = payload.get("email", "")
    code = req_body.code.upper().strip()

    # Load coupons
    coupon_data = _load_coupons()
    coupon = None
    coupon_index = -1

    for idx, c in enumerate(coupon_data.get("coupons", [])):
        if c["code"] == code:
            coupon = c
            coupon_index = idx
            break

    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")

    if not coupon.get("active", False):
        raise HTTPException(status_code=400, detail="Coupon is not active")

    if coupon.get("uses", 0) >= coupon.get("max_uses", 0):
        raise HTTPException(status_code=400, detail="Coupon has reached max uses")

    # Load user database and update plan
    db = _load_users()

    if user_email not in db.get("users", {}):
        raise HTTPException(status_code=404, detail="User not found")

    # Update user plan
    db["users"][user_email]["plan"] = coupon["plan"]

    # Increment coupon uses
    coupon_data["coupons"][coupon_index]["uses"] += 1

    _save_users(db)
    _save_coupons(coupon_data)
    return {
        "success": True,
        "message": f"Plan auf {coupon['plan']} aktualisiert via Coupon {code}",
        "new_plan": coupon["plan"],
    }


# ── Support Tickets ──

@app.post("/api/admin/tickets")
def create_ticket(req_body: TicketCreateRequest, authorization: Optional[str] = Header(None)):
    """Create a support ticket (any authenticated user)."""
    if not authorization:
        raise HTTPException(status_code=403, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=403, detail="Invalid Authorization header format")

    token = parts[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    user_email = payload.get("email", "")

    # Load tickets
    data = _load_tickets()
    ticket_id = data.get("next_id", 1)

    # Create ticket
    ticket = {
        "id": ticket_id,
        "email": user_email,
        "subject": req_body.subject.strip(),
        "message": req_body.message.strip(),
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        "replies": [],
    }

    data["tickets"].append(ticket)
    data["next_id"] = ticket_id + 1
    _save_tickets(data)

    return {"success": True, "ticket": ticket}


@app.get("/api/admin/tickets")
def admin_list_tickets(authorization: Optional[str] = Header(None)):
    """List all support tickets (admin only)."""
    _require_admin(authorization)

    data = _load_tickets()
    return {"tickets": data.get("tickets", [])}


@app.put("/api/admin/tickets/{ticket_id}/reply")
def admin_reply_ticket(ticket_id: int, req_body: TicketReplyRequest, authorization: Optional[str] = Header(None)):
    """Reply to a support ticket (admin only)."""
    _require_admin(authorization)

    data = _load_tickets()
    ticket = None

    for t in data.get("tickets", []):
        if t["id"] == ticket_id:
            ticket = t
            break

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    reply = {
        "message": req_body.message.strip(),
        "from": "admin",
        "created_at": datetime.utcnow().isoformat(),
    }

    ticket["replies"].append(reply)
    _save_tickets(data)

    return {"success": True, "ticket": ticket}


@app.put("/api/admin/tickets/{ticket_id}/status")
def admin_update_ticket_status(ticket_id: int, req_body: TicketStatusRequest, authorization: Optional[str] = Header(None)):
    """Update ticket status (admin only)."""
    _require_admin(authorization)

    valid_statuses = ["open", "closed", "in_progress"]
    if req_body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

    data = _load_tickets()
    ticket = None

    for t in data.get("tickets", []):
        if t["id"] == ticket_id:
            ticket = t
            break

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket["status"] = req_body.status
    _save_tickets(data)

    return {"success": True, "ticket": ticket}


@app.get("/api/my-tickets")
def get_my_tickets(authorization: Optional[str] = Header(None)):
    """Get user's own support tickets (any authenticated user)."""
    if not authorization:
        raise HTTPException(status_code=403, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=403, detail="Invalid Authorization header format")

    token = parts[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    user_email = payload.get("email", "")

    # Load tickets
    data = _load_tickets()
    user_tickets = [t for t in data.get("tickets", []) if t["email"] == user_email]

    return {"tickets": user_tickets}


# ── Run with uvicorn ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
