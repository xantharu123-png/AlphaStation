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
from copy import deepcopy
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import FastAPI, BackgroundTasks, Query, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# V3.4: Auth & Subscription System
try:
    from modules.auth import (
        register_user, login_user, verify_token, get_user_plan,
        get_user_limits, check_tab_access, check_feature,
        create_checkout_session, create_billing_portal,
        handle_stripe_webhook, PLANS, SCANNER_TABS_BY_PLAN,
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
)
from modules.indicators import calculate_ema_series, calculate_vwap, calculate_rsi_from_bars, calculate_macd, calculate_obv
from modules.volume_analysis import calculate_volume_profile, find_volume_voids

try:
    from modules.scorers import calculate_setup_score as calculate_stock_setup_score
except ImportError:
    calculate_stock_setup_score = None

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
    "Trend Reversal",
    "MA Bounce Long",
    "MA Bounce Short",
    "Wyckoff Accumulation",
    "Wyckoff Distribution",
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
    "IREX", "MSTX", "MSTU", "MSTZ", "TSLL", "TSLQ", "NVDL", "NVDQ", "NVDU", "NVDD",
    "CONL", "GGLL", "GGLS", "AAPU", "AAPD", "AMZU", "AMZD", "METU", "METD",
    "SOXL", "SOXS", "TQQQ", "SQQQ", "UPRO", "SPXU", "SPXL", "SPXS", "LABU", "LABD",
    "TECL", "TECS", "FNGU", "FNGD", "BOIL", "KOLD", "GUSH", "DRIP", "NUGT", "DUST",
    "JNUG", "JDST", "YINN", "YANG", "UVXY", "VIXY", "VXX", "BITO", "BITI",
}

NON_STOCK_ETP_KEYWORDS = {
    " ETF", "ETN", "ETP", " FUND", "2X", "3X", "LEVERAGED", "INVERSE",
    "ULTRA", "ULTRAPRO", "BULL", "BEAR", "DAILY TARGET", "TRADR", "T-REX",
    "DIREXION", "PROSHARES", "GRANITESHARES", "YIELDMAX", "ROUNDHILL", "DEFIANCE",
    "REX SHARES", "MICROSECTORS", "VOLATILITY SHARES",
}

ORB_ALLOWED_POLYGON_TYPES = {"CS", "ADRC", "ADRP"}
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
    """Cheap symbol-level guard for products that should never be in stock ORB."""
    tk = str(ticker or "").upper().strip()
    if not tk:
        return "empty ticker"
    if tk in NON_STOCK_ETP_TICKERS or tk in INVERSE_ETFS:
        return "known ETF/ETP ticker"
    if len(tk) >= 4 and tk[-1] in ("X", "Q") and tk[-2] in ("X", "Q", "S"):
        return "leveraged ETF ticker pattern"
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
        return True, "no polygon key for reference check"

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

        if asset_type and asset_type not in ORB_ALLOWED_POLYGON_TYPES:
            result = (False, f"type={asset_type}")
        elif any(keyword in f" {name}" for keyword in NON_STOCK_ETP_KEYWORDS):
            result = (False, "ETF/ETP keyword")
        elif market and market != "stocks":
            result = (False, f"market={market}")
        else:
            result = (True, asset_type or "reference ok")
    except Exception as e:
        result = (False, f"reference error: {e}")

    _ORB_REFERENCE_CACHE[tk] = result
    return result


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
def _load_secrets():
    """Liest .streamlit/secrets.toml"""
    secrets = {}
    paths = [
        Path(__file__).parent / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]
    for sp in paths:
        if sp.exists():
            with open(sp, "r") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, val = line.split("=", 1)
                        secrets[key.strip()] = val.strip().strip('"')
            if secrets:
                break
    return secrets

_SECRETS = _load_secrets()

# Fix: POLYGON_KEY aus secrets.toml laden falls env var leer
if not POLYGON_KEY:
    POLYGON_KEY = _SECRETS.get("POLYGON_KEY", "")

_EMAIL_COOLDOWN = {}
_EMAIL_COOLDOWN_SEC = 3600 * 8  # V2.6: 8h pro Ticker
_EMAIL_STARTUP_TIME = time.time()  # V2.6b: Startup-Zeitpunkt für Cooldown nach Restart
_EMAIL_STARTUP_DELAY = 300  # 5 Min nach Restart keine Mails (Cache-Daten = alt)

print(f"[Init] POLYGON_KEY: {'gesetzt' if POLYGON_KEY else 'FEHLT!'}")
print(f"[Init] Email alerts: {'AKTIV' if _SECRETS.get('GMAIL_USER') and _SECRETS.get('GMAIL_APP_PASSWORD') else 'INAKTIV (secrets.toml fehlt)'}")


def _send_email_alert(subject, body_html):
    """Sendet E-Mail Alert via Gmail SMTP."""
    # V2.6b: Nach Restart 5 Min warten (alte Cache-Daten erzeugen Phantom-Alerts)
    if time.time() - _EMAIL_STARTUP_TIME < _EMAIL_STARTUP_DELAY:
        print(f"[Alert] SKIP (Startup-Cooldown): {subject}")
        return False
    gmail_user = _SECRETS.get("GMAIL_USER", "")
    gmail_pass = _SECRETS.get("GMAIL_APP_PASSWORD", "")
    alert_to = _SECRETS.get("ALERT_EMAIL", gmail_user)
    if not gmail_user or not gmail_pass:
        return False
    for attempt in range(3):
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"TradingBot Alert <{gmail_user}>"
            msg["To"] = alert_to
            msg["Subject"] = subject
            plain = re.sub(r"<[^>]+>", "", body_html.replace("<br>", "\n").replace("</tr>", "\n"))
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))
            # Try port 587 (STARTTLS) first, fallback to 465 (SSL)
            try:
                server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, alert_to.split(","), msg.as_string())
                server.quit()
            except Exception:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
                    server.login(gmail_user, gmail_pass)
                    server.sendmail(gmail_user, alert_to.split(","), msg.as_string())
            print(f"[Alert] Email gesendet: {subject}")
            return True
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"[Alert] Email FEHLER nach 3 Versuchen: {e}")
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
        _alert_grades = {"S", "A", "A+"}
        alerts = []
        for r in results:
            if not isinstance(r, dict):
                continue
            ticker = r.get("ticker", r.get("Ticker", r.get("symbol", "")))
            grade = r.get("BI_Grade", r.get("Grade", r.get("grade", r.get("rating", ""))))
            score = r.get("BI_Score", r.get("Score", r.get("score", r.get("Alpha", 0))))
            _rvol_raw = r.get("RVOL", r.get("rvol", None))
            _rvol_check = _rvol_raw if _rvol_raw is not None else 0
            # RVOL Guard: Grade S/A braucht min RVOL 0.7 — Sicherheitsnetz
            if grade in ("S", "A", "A+") and _rvol_check < 0.7:
                grade = "B"  # Downgrade — kein Alert
            if grade not in _alert_grades:
                continue
            ck = f"{scanner_name}_{ticker}"
            if ck in _EMAIL_COOLDOWN and now - _EMAIL_COOLDOWN[ck] < _EMAIL_COOLDOWN_SEC:
                continue
            _EMAIL_COOLDOWN[ck] = now
            alerts.append({"ticker": ticker, "grade": grade, "score": score,
                           "price": r.get("Preis", r.get("price", r.get("current", 0))),
                           "direction": r.get("BI_Direction", r.get("direction", "")),
                           "rvol": r.get("RVOL", r.get("rvol", 0))})
        if not alerts:
            # Log warum keine Alerts
            all_grades = [r.get("BI_Grade", r.get("Grade", "?")) for r in results if isinstance(r, dict)]
            print(f"[Alert] {scanner_name}: Keine alertbaren Grades. Vorhandene Grades: {dict((g, all_grades.count(g)) for g in set(all_grades))}")
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
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">${a["price"]}</td>'
            rows += f'<td style="padding:8px;border-bottom:1px solid #eee">{a["rvol"]}x</td></tr>'
        body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
        <h2 style="color:#1a73e8">🚨 TradingBot Alert — {label}</h2>
        <p style="color:#666">{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC | {n} starke Setups</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr style="background:#f5f5f5"><th style="padding:8px;text-align:left">Ticker</th>
        <th style="padding:8px;text-align:left">Grade</th><th style="padding:8px;text-align:left">Score</th>
        <th style="padding:8px;text-align:left">Preis</th><th style="padding:8px;text-align:left">RVOL</th></tr>
        {rows}</table>
        <p style="color:#999;font-size:12px;margin-top:20px">Automatischer Alert — S = ELITE | A = STARK | B = SOLIDE</p>
        </body></html>'''
        print(f"[Alert] {scanner_name}: Sende Alert für {n} Treffer: {[a['ticker'] for a in alerts]}")
        _send_email_alert(subject, body)
    except Exception as e:
        import traceback
        print(f"[Alert] Check-Fehler {scanner_name}: {e}\n{traceback.format_exc()}")


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
    "BPIQ_Available": "bpiq_available", "BPIQ_Catalysts": "bpiq_catalysts",
    "Selloff_Reason": "selloff_reason", "Negative_Flags": "negative_flags",
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
        for flag in ("needs_history", "needs_volume_profile", "needs_harmonic", "needs_ma")
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
    elif strat.get("needs_ma"):
        candidate_limit = 180
    elif strat.get("needs_volume_profile"):
        candidate_limit = 160

    min_history = max(int(strat.get("history_days", 0) or 0), 20)
    if strat.get("needs_volume_profile"):
        min_history = max(min_history, 90)
    if strat.get("needs_harmonic"):
        min_history = max(min_history, 220)
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


def _biotech_scan_wrapper() -> None:
    """Wrapper to run biotech background scan in background."""
    try:
        print("[Biotech] Starting scan... (this takes 5-15 minutes)")
        _biotech_background_scan(POLYGON_KEY)
        print("[Biotech] Scan completed")
        # Email Alert bei Grade S/A
        _check_and_alert("biotech", BIOTECH_CACHE)
    except Exception as e:
        print(f"Biotech background scan error: {e}")
        import traceback
        traceback.print_exc()


def _strategy_scan_wrapper(strategy_name: str) -> None:
    """V2.2: Erweiterter Snapshot-Scanner für alle Strategien.
    Berechnet Gap%, Vortag%, Dollar-Volume und filtert korrekt."""
    try:
        strat = STRATEGIES.get(strategy_name)
        if not strat:
            print(f"[Strategy Scan] Strategie '{strategy_name}' nicht gefunden")
            return

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
        session_name, _session_label = get_current_trading_session()
        _use_extended_prices = session_name in ("Pre-Market", "After-Hours")

        for t in _all_snapshot_tickers:
                try:
                    ticker = str(t.get("ticker", "")).upper().strip()
                    day = t.get("day", {}) or {}
                    prev = t.get("prevDay", {}) or {}
                    if not ticker or "." in ticker or "/" in ticker or not prev.get("c"):
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

                    results.append({
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
                    })
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
        print(f"[Strategy Scan] {strategy_name}: {len(results)} Treffer → {_strat_cache}")

    except Exception as e:
        print(f"[Strategy Scan] Fehler: {e}")
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
                if symbol in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "WBTC", "WETH", "STETH", "RETH"):
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
                crypto_rvol = vol_mcap_ratio / 10.0  # 15% Vol/MCap ~= 1.5 crypto volume intensity
                trend_daily = change_7d / 7.0

                if not (change_min <= change_24h <= change_max):
                    continue
                if not (price_min <= price <= price_max):
                    continue
                if "MarketCap" in filters and not (mcap_min <= mcap <= mcap_max):
                    continue
                if "RVOL" in filters and not (rvol_min <= crypto_rvol <= rvol_max):
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
                    "RVOL": round(crypto_rvol, 2),
                    "rvol": round(crypto_rvol, 2),
                    "Close_Position": round(close_pos, 2),
                    "BtcRelative7d": round(btc_alpha_7d, 2),
                    "score": score,
                    "grade": grade,
                    "isCrypto": True,
                    "data_source": "CoinGecko markets",
                    "volume_model": "RVOL = Vol/MCap/10",
                })
            except Exception as item_err:
                print(f"[Crypto Strategy] skip {coin.get('symbol', '?')} ({item_err})")

        results.sort(key=lambda x: (-x.get("score", 0), -abs(x.get("change_pct", 0))))
        results = results[:80]
        _strat_cache = _strategy_cache_path(strategy_name, "crypto")
        save_cache_file(_strat_cache, results)
        print(f"[Crypto Strategy] {strategy_name}: {len(results)} Treffer -> {_strat_cache}")
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

        # ── 1. Polygon Snapshot: Alle Aktien mit positivem Move holen ──
        _all_tickers = []
        for endpoint in ["gainers"]:
            try:
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/{endpoint}"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY, "limit": 250})
                if resp.status_code == 200:
                    _all_tickers.extend(resp.json().get("tickers", []))
            except Exception:
                pass

        # Auch Full Snapshot falls wenig Gainers (AH/PM)
        if len(_all_tickers) < 30:
            try:
                url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
                resp = rate_limited_get(url, params={"apiKey": POLYGON_KEY})
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
        candidates = []
        for t in _all_tickers:
            ticker = t.get("ticker", "")
            if not ticker or "." in ticker or len(ticker) > 5:
                continue  # OTC / Warrants raus
            day = t.get("day", {})
            prev = t.get("prevDay", {})
            price = day.get("c", 0) or t.get("lastTrade", {}).get("p", 0)
            prev_close = prev.get("c", 0)
            if price < 5 or prev_close <= 0:
                continue
            change_pct = (price - prev_close) / prev_close * 100
            if change_pct < 0.5:
                continue  # Nur Aufwärtsbewegungen — Turtle ist Long-only
            volume = day.get("v", 0)
            if volume * price < 500000:
                continue  # Mindest-Dollar-Volume $500k
            candidates.append((ticker, t, price, prev_close, change_pct, volume))

        print(f"[Turtle] {len(candidates)} Kandidaten nach Vorfilter")

        # ── 3. Top 80 nach Change% sortieren, dann History holen ──
        candidates.sort(key=lambda x: -x[4])
        candidates = candidates[:80]

        from datetime import timedelta
        _today = datetime.now()
        _from = (_today - timedelta(days=45)).strftime("%Y-%m-%d")
        _to = _today.strftime("%Y-%m-%d")

        for ticker, snap_data, price, prev_close, change_pct, volume in candidates:
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

                score = min(100, score)

                # Grade
                if score >= 75:
                    grade = "S"
                elif score >= 60:
                    grade = "A"
                elif score >= 45:
                    grade = "B"
                elif score >= 30:
                    grade = "C"
                else:
                    grade = "D"

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
                    "score": score,
                    "grade": grade,
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
            if snap_resp.status_code == 200:
                _raw_tickers = snap_resp.json().get("tickers", [])

            # V3.4: Wenn Losers-Endpoint wenig/keine Ergebnisse → Extended Hours
            # Full Snapshot holen und lastTrade vs day.close vergleichen
            if len(_raw_tickers) < 10:
                print(f"[Bear] Losers endpoint nur {len(_raw_tickers)} Ticker — Extended Hours Modus")
                _is_extended_hours = True
                try:
                    _full_snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
                    _full_resp = rate_limited_get(_full_snap_url, params={"apiKey": POLYGON_KEY}, timeout=30)
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

            if _raw_tickers:
                tickers = _raw_tickers
                print(f"[Bear] Processing {len(tickers)} tickers (extended={_is_extended_hours})")
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
                            continue
                        vol = day.get("v", 0)
                        dollar_vol = price * vol
                        if dollar_vol < 300_000 and not _is_extended_hours:
                            continue
                        if chg_pct > -3:
                            continue

                        ticker_sym = t.get("ticker", "")
                        # V2.6b: ETF/ETP/Leveraged Filter — keine ETFs in Breakdown-Stocks
                        _tk_up = ticker_sym.upper()
                        # Bekannte ETF-Suffixe und Muster filtern
                        _etf_tickers = {"SOXS","SQQQ","SPXU","SPXS","UVXY","VIXY","QID","SRTY","TZA","SDOW","LABD",
                                       "SDS","SH","PSQ","DOG","RWM","SOXL","TQQQ","UPRO","SPXL","UDOW","FNGU",
                                       "AMPL","KOLD","BOIL","DRIP","GUSH","JDST","JNUG","NUGT","DUST","YANG","YINN",
                                       "SVXY","VXX","TVIX","BITI","BITO"}
                        if _tk_up in _etf_tickers:
                            continue
                        # Heuristik: 4+ Zeichen, endet auf X/Q/S doppelt = wahrscheinlich ETF
                        if len(_tk_up) >= 4 and _tk_up[-1] in ("X","Q") and _tk_up[-2] in ("X","Q","S"):
                            continue
                        rvol = 0
                        ma20 = 0
                        ma50 = 0
                        ma20_dist = 0
                        ma50_dist = 0
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
                        except Exception as e:
                            print(f"[Bear] History failed for {ticker_sym}: {e}")

                        # V2.2: Ohne History-Daten → überspringen (kein Blindflug)
                        if not has_history:
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

                        losers.append({
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
                            "score_details": " | ".join(score_details),
                        })
                    except Exception as e:
                        print(f"[Warning] Error processing breakdown stock: {e}")
                        continue
                losers.sort(key=lambda x: x.get("score", 0), reverse=True)
                result["breakdown_stocks"] = losers[:30]
                print(f"[Bear] Final breakdown_stocks: {len(losers[:30])}")
        except Exception as e:
            print(f"Breakdown stocks error: {e}")

        # Only save if we got actual stock data — don't overwrite Friday's results on weekends
        has_stock_data = len(result.get("breakdown_stocks", [])) > 0 or len(result.get("inverse_etfs", [])) > 0
        if has_stock_data:
            save_cache_file(BEAR_CACHE, [result])
            print(f"[Bear] Saved {len(result.get('inverse_etfs',[]))} ETFs, {len(result.get('breakdown_stocks',[]))} breakdowns")
            # V2.2: Bear Alert — vollständige Infos pro Signal
            _etf_rows = []
            _bd_rows = []
            for etf in result.get("inverse_etfs", []):
                if isinstance(etf, dict) and etf.get("signal") == "STARK":
                    _name = etf.get("name", etf.get("underlying", ""))[:30]
                    _chg5 = etf.get("change_5d", 0)
                    _chg1 = etf.get("change_1d", 0)
                    _rvol = etf.get("rvol", 0)
                    _etf_rows.append(
                        f"<tr><td style='padding:4px 8px;font-weight:bold'>{etf.get('ticker','?')}</td>"
                        f"<td style='padding:4px 8px'>{_name}</td>"
                        f"<td style='padding:4px 8px;text-align:right;color:#dc2626'>{_chg1:+.1f}%</td>"
                        f"<td style='padding:4px 8px;text-align:right;font-weight:bold;color:#dc2626'>{_chg5:+.1f}%</td>"
                        f"<td style='padding:4px 8px;text-align:right'>{_rvol:.1f}x</td></tr>"
                    )
            for bd in result.get("breakdown_stocks", []):
                if isinstance(bd, dict) and bd.get("score", 0) >= 50:
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
            for bd in result.get("breakdown_stocks", []):
                if not isinstance(bd, dict):
                    continue
                _cs_ticker = bd.get("ticker", "")
                _cs_grade = bd.get("grade", "")
                _cs_chg = bd.get("change_pct", 0)
                _cs_score = bd.get("score", 0)
                # V2.8: Nur Grade S/A + Drop >= -10% + Score >= 60 (vereinheitlicht mit bg_service)
                if _cs_grade not in ("S", "A") or _cs_chg > -10 or _cs_score < 60:
                    continue
                # ETF/ETP Filter — Ticker-Heuristik (3+ gleiche Buchstaben am Ende = oft ETF)
                _cs_tk_up = _cs_ticker.upper()
                if len(_cs_tk_up) >= 4 and _cs_tk_up[-1] in ("X", "Q", "S") and _cs_tk_up[-2] in ("X", "Q", "S"):
                    continue  # SOXS, SQQQ, SPXS, UVXY etc.
                _crash_stocks.append(bd)

            if _crash_stocks:
                _crash_ck = f"crash_summary_{datetime.now().strftime('%Y%m%d')}"
                if _crash_ck not in _EMAIL_COOLDOWN:
                    _EMAIL_COOLDOWN[_crash_ck] = time.time()
                    _crash_rows = ""
                    for _cs in _crash_stocks[:5]:  # Max 5 in einer Mail
                        _gc = {"S": "#7c3aed", "A": "#16a34a"}.get(_cs.get("grade", ""), "#666")
                        _crash_rows += (
                            f"<tr><td style='padding:6px 8px;font-weight:bold;color:{_gc}'>{_cs.get('grade','?')}</td>"
                            f"<td style='padding:6px 8px;font-weight:bold'>{_cs.get('ticker','?')}</td>"
                            f"<td style='padding:6px 8px;text-align:right'>${_cs.get('price',0):.2f}</td>"
                            f"<td style='padding:6px 8px;text-align:right;color:#dc2626;font-weight:bold'>{_cs.get('change_pct',0):.1f}%</td>"
                            f"<td style='padding:6px 8px;text-align:right'>{_cs.get('rvol',0):.1f}x</td>"
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
                    <th style="padding:6px 8px;text-align:right">Score</th></tr>
                    {_crash_rows}</table>
                    </body></html>'''
                    _send_email_alert(f"⚠️ CRASH: {len(_crash_stocks)} Aktien ({_crash_stocks[0].get('ticker','?')} {_crash_stocks[0].get('change_pct',0):.0f}%)", _crash_body)
                    print(f"[Bear] CRASH SUMMARY sent: {[c.get('ticker') for c in _crash_stocks]}")

            # V2.6b: Bear Summary Email — 1x pro Tag, nur wenn Grade S/A Signale dabei
            _bd_strong = [r for r in _bd_rows if True]  # already filtered above
            _total_signals = len(_etf_rows) + len(_bd_rows)
            _has_strong_signal = len(_etf_rows) > 0 or _total_signals >= 3
            if _total_signals > 0 and _has_strong_signal:
                _bear_ck = f"bear_summary_{datetime.now().strftime('%Y%m%d')}"
                if _bear_ck not in _EMAIL_COOLDOWN:
                    _EMAIL_COOLDOWN[_bear_ck] = time.time()
                    _ts = f"<p style='color:#666;font-size:13px'>{datetime.now().strftime('%d.%m.%Y %H:%M')} UTC | {_total_signals} Signale</p>"
                    _etf_html = ""
                    if _etf_rows:
                        _etf_html = (
                            "<h3 style='color:#dc2626;margin-top:16px'>Inverse ETFs (Signal STARK)</h3>"
                            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
                            "<tr style='background:#fef2f2;border-bottom:1px solid #ddd'>"
                            "<th style='padding:6px 8px;text-align:left'>Ticker</th>"
                            "<th style='padding:6px 8px;text-align:left'>Name</th>"
                            "<th style='padding:6px 8px;text-align:right'>1T%</th>"
                            "<th style='padding:6px 8px;text-align:right'>5T%</th>"
                            "<th style='padding:6px 8px;text-align:right'>RVOL</th></tr>"
                            + "".join(_etf_rows) + "</table>"
                        )
                    _bd_html = ""
                    if _bd_rows:
                        _bd_html = (
                            "<h3 style='color:#dc2626;margin-top:16px'>Short-Kandidaten (Score 50+)</h3>"
                            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
                            "<tr style='background:#fef2f2;border-bottom:1px solid #ddd'>"
                            "<th style='padding:6px 8px;text-align:left'>Grd</th>"
                            "<th style='padding:6px 8px;text-align:left'>Ticker</th>"
                            "<th style='padding:6px 8px;text-align:right'>Preis</th>"
                            "<th style='padding:6px 8px;text-align:right'>Chg%</th>"
                            "<th style='padding:6px 8px;text-align:right'>RVOL</th>"
                            "<th style='padding:6px 8px;text-align:right'>MA20</th>"
                            "<th style='padding:6px 8px;text-align:right'>Score</th></tr>"
                            + "".join(_bd_rows) + "</table>"
                        )
                    _bear_body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
                    <h2 style="color:#dc2626">Bear Scanner Alert</h2>
                    {_ts}{_etf_html}{_bd_html}
                    </body></html>'''
                    _send_email_alert(f"Bear Alert: {_total_signals} Signale", _bear_body)
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
    "btc_divergenz": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "money_flow": {"running": False, "last_run": None, "next_run": None, "interval_min": 60},
    "new_listing": {"running": False, "last_run": None, "next_run": None, "interval_min": 120},
    "volume_spikes": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "orb": {"running": False, "last_run": None, "next_run": None, "interval_min": 5},
    "turtle": {"running": False, "last_run": None, "next_run": None, "interval_min": 30},
    "strategy_scan": {"running": False, "last_run": None, "next_run": None, "interval_min": 5},
}
SCAN_CACHE_MAP = {
    "bi_long": "/tmp/bi_cache_long.json",
    "bi_short": "/tmp/bi_cache_short.json",
    "bear": "/tmp/bear_scanner_cache.json",
    "biotech": "/tmp/alpha_biotech_cache.json",
    "early_movers": "/tmp/early_movers_cache.json",
    "crash_monitor": "/tmp/crash_monitor_cache.json",
    "btc_divergenz": "/tmp/btc_divergenz_cache.json",
    "money_flow": "/tmp/money_flow_cache.json",
    "new_listing": "/tmp/new_listing_scanner.json",
    "volume_spikes": "/tmp/volume_spikes_cache.json",
    "orb": "/tmp/orb_scan_results.json",
    "turtle": "/tmp/turtle_scan_cache.json",
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

_SCAN_TIMEOUTS = {"bi_long": 45, "bi_short": 45, "biotech": 45, "bear": 20}

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
        ("btc_divergenz", _btc_divergenz_wrapper),
        ("volume_spikes", _volume_spikes_wrapper),
        ("money_flow", _money_flow_wrapper),
        ("orb", _orb_scanner_wrapper),
        ("bear", _bear_scan_wrapper),  # V2.5: Bear ist light (~30 API-Calls), nicht heavy
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
            _run_scan_safe(name, func)
            last_run_times[name] = time.time()
            with _scan_lock:
                _scan_status[name]["next_run"] = datetime.fromtimestamp(
                    time.time() + interval_sec
                ).isoformat()
            # V2.2: Schwere Scans (bi_long, bi_short, biotech) WARTEN bis fertig
            # bevor der nächste startet — sonst teilen sich alle 200 calls/min
            if name in _heavy_names:
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
                time.sleep(2)  # Small stagger between scan launches
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

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
_VENDOR_DIR = os.path.join(_FRONTEND_DIR, "vendor")
if os.path.isdir(_VENDOR_DIR):
    app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")


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
    base_url = "http://178.104.69.209:3000"
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
    return_url = req_body.return_url or "http://178.104.69.209:3000"
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
            "POLYGON_KEY": bool(POLYGON_KEY),
            "BPIQ_API_KEY": bool(BPIQ_API_KEY),
            "ANTHROPIC_API_KEY": bool(ANTHROPIC_API_KEY),
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
        "POLYGON_KEY": bool(POLYGON_KEY),
        "BPIQ_API_KEY": bool(BPIQ_API_KEY),
        "ANTHROPIC_API_KEY": bool(ANTHROPIC_API_KEY),
    }

    warnings = []
    critical = []
    if not api_keys["POLYGON_KEY"]:
        critical.append("POLYGON_KEY fehlt - Aktien-/ORB-/Marktdaten koennen nicht sauber laufen")
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
        "scheduler": {
            "running": _scheduler_running,
            "total_scans": len(_scan_status),
            "running_scans": running_scans,
            "stale_or_missing_scans": stale_or_missing,
            "health_counts": health_counts,
        },
        "scans": scan_health,
        "calendar": {
            "official_sources": ["Federal Reserve", "BLS", "BEA", "Census"],
            "official_event_families": ["FOMC/FED", "CPI", "NFP", "PPI", "GDP/PCE", "Retail Sales", "Advance Economic Indicators"],
            "estimated_event_families": ["Earnings Season", "ISM Manufacturing PMI", "Initial Jobless Claims"],
            "quality": "official_core_macro_marked_estimates_remaining",
        },
        "warnings": warnings,
        "critical": critical,
    }


@app.get("/api/system-health")
def get_system_health():
    """Detailed system health for UI/admin checks."""
    return _build_system_health()


@app.get("/api/debug-keys")
def debug_keys():
    """Temp debug: zeigt ob secrets.toml geladen wird."""
    import pathlib
    p1 = Path(__file__).parent / ".streamlit" / "secrets.toml"
    p2 = Path.home() / ".streamlit" / "secrets.toml"
    return {
        "polygon_key_len": len(POLYGON_KEY),
        "polygon_key_first4": POLYGON_KEY[:4] if POLYGON_KEY else "LEER",
        "secrets_loaded_keys": list(_SECRETS.keys()),
        "path1_exists": p1.exists(),
        "path1": str(p1),
        "path2_exists": p2.exists(),
        "path2": str(p2),
    }


@app.post("/api/test-email")
def test_email_alert():
    """Test-Endpoint: Sendet eine Test-Mail um Email-Alerts zu verifizieren."""
    if not _SECRETS.get("GMAIL_USER"):
        raise HTTPException(status_code=500, detail="secrets.toml nicht gefunden oder GMAIL_USER fehlt")
    success = _send_email_alert(
        "✅ TradingBot Test — Email Alerts funktionieren!",
        f'''<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
        <h2 style="color:#059669">✅ Email Alert System aktiv</h2>
        <p>Dieser Test wurde am <b>{datetime.now().strftime("%d.%m.%Y %H:%M")} UTC</b> gesendet.</p>
        <p>Du wirst ab jetzt automatisch benachrichtigt bei: <b>Grade S/A</b> (BI + Biotech), <b>Bear (2x/Tag bei starken Signalen)</b>, <b>Crash Flash (≥-15%)</b>, <b>ORB Breakouts</b> (Grade S/A).</p>
        <p style="color:#999;font-size:12px">TradingBot Alert System v{API_VERSION}</p>
        </body></html>'''
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
                entry = round(close, 2)
                atr_stop = atr * 2 if (atr and atr > 0) else (close * 0.03)
                _sup = support_1 if (support_1 and support_1 > 0) else (close * 0.97)
                stop = round(max(_sup, close - atr_stop), 2)
                risk = entry - stop
                if risk > 0:
                    tp1 = round(entry + risk, 2)
                    tp2 = round(entry + risk * 1.618, 2)
                    trade_setup = {
                        "entry": entry, "stop": stop,
                        "tp1": tp1, "tp2": tp2,
                        "rr": round((tp1 - entry) / risk, 2),
                        "direction": "LONG"
                    }
            else:  # SHORT
                entry = round(close, 2)
                atr_stop = atr * 2 if (atr and atr > 0) else (close * 0.03)
                _res = resist_1 if (resist_1 and resist_1 > 0) else (close * 1.03)
                stop = round(min(_res, close + atr_stop), 2)
                risk = stop - entry
                if risk > 0:
                    tp1 = round(entry - risk, 2)
                    tp2 = round(entry - risk * 1.618, 2)
                    trade_setup = {
                        "entry": entry, "stop": stop,
                        "tp1": tp1, "tp2": tp2,
                        "rr": round((entry - tp1) / risk, 2),
                        "direction": "SHORT"
                    }

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
                    _entry = round(close, 2)
                    _atr_stop = atr * 2 if (atr and atr > 0) else (close * 0.03)
                    _sup = support_1 if (support_1 and support_1 > 0) else (close * 0.97)
                    _stop = round(max(_sup, close - _atr_stop), 2)
                    _risk = _entry - _stop
                    if _risk > 0:
                        _tp1 = round(_entry + _risk, 2)
                        _tp2 = round(_entry + _risk * 1.618, 2)
                        trade_setup = {
                            "entry": _entry, "stop": _stop,
                            "tp1": _tp1, "tp2": _tp2,
                            "rr": round((_tp1 - _entry) / _risk, 2),
                            "direction": "LONG"
                        }
                else:  # SHORT
                    _entry = round(close, 2)
                    _atr_stop = atr * 2 if (atr and atr > 0) else (close * 0.03)
                    _res = resist_1 if (resist_1 and resist_1 > 0) else (close * 1.03)
                    _stop = round(min(_res, close + _atr_stop), 2)
                    _risk = _stop - _entry
                    if _risk > 0:
                        _tp1 = round(_entry - _risk, 2)
                        _tp2 = round(_entry - _risk * 1.618, 2)
                        trade_setup = {
                            "entry": _entry, "stop": _stop,
                            "tp1": _tp1, "tp2": _tp2,
                            "rr": round((_entry - _tp1) / _risk, 2),
                            "direction": "SHORT"
                        }

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
    overlays: str = Query("ema,vwap,sr,fib", description="Comma-separated: ema,vwap,sr,fib,patterns")
):
    """Get OHLCV data with chart overlays for TradingView Lightweight Charts."""
    try:
        # ── Chart Cache Check ──
        _cache_key = f"{ticker}:{timeframe}"
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
                    except Exception as e:
                        print(f"[Warning] Error calculating EMA{period}: {e}")
            result["ema"] = ema_overlays

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
                h20 = max(highs[-20:])
                l20 = min(lows[-20:])
                rng = h20 - l20
                cur_price = closes[-1]
                fib = {}

                if rng > 0:
                    # Bestimme Richtung: Preis näher am High = SHORT (Abverkauf erwartet)
                    # Preis näher am Low = LONG (Erholung erwartet)
                    mid = l20 + rng * 0.5
                    is_short_bias = cur_price > mid  # Preis in oberer Hälfte = eher SHORT

                    if is_short_bias:
                        # SHORT: Fib von HIGH nach LOW (High=100%, Low=0%)
                        # Retracement = wie weit ist Preis vom High zurückgekommen
                        for ratio in [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]:
                            fib[f"{int(ratio*100)}%"] = round(h20 - rng * ratio, 2)
                        # Extensions nach UNTEN (Short-Targets)
                        fib["127%"] = round(h20 - rng * 1.272, 2)
                        fib["161%"] = round(h20 - rng * 1.618, 2)
                        fib["200%"] = round(h20 - rng * 2.0, 2)
                    else:
                        # LONG: Fib von LOW nach HIGH (Low=0%, High=100%)
                        # Retracement = wie weit ist Preis vom Low gestiegen
                        for ratio in [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]:
                            fib[f"{int(ratio*100)}%"] = round(l20 + rng * ratio, 2)
                        # Extensions nach OBEN (Long-Targets)
                        fib["127%"] = round(l20 + rng * 1.272, 2)
                        fib["161%"] = round(l20 + rng * 1.618, 2)
                        fib["200%"] = round(l20 + rng * 2.0, 2)

                    result["fib"] = fib
                    result["fib_direction"] = "short" if is_short_bias else "long"
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
    """Generate AI analysis for a ticker using Claude.
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
                "model": cached.get("model", "claude-sonnet-4-20250514"),
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

    # ── Claude API Call ──
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
            ts = datetime.now().isoformat()

            # In Cache speichern
            _AI_CACHE[ticker_upper] = {
                "analysis": content,
                "model": "claude-sonnet-4-20250514",
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

            return {"ticker": ticker, "analysis": content, "model": "claude-sonnet-4-20250514", "timestamp": ts, "cached": False}
        else:
            return {"ticker": ticker, "analysis": f"API Fehler: {claude_resp.status_code}", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"ticker": ticker, "analysis": f"Fehler: {str(e)}", "timestamp": datetime.now().isoformat()}


@app.get("/api/strategies", response_model=StrategiesResponse)
def list_strategies(market_type: str = Query("stocks", description="Market type: stocks, crypto, futures, forex")):
    """List all strategies for a given market type. Strips internal fields for public API."""
    strategies = get_public_strategies_for_market(market_type)

    # Strip internal calculation details — users should not see filters, logic, thresholds
    # NO description — contains internal details; frontend has its own guide texts
    _safe_keys = {"stocks_only", "needs_history", "needs_harmonic",
                  "needs_volume_profile", "needs_ma", "ma_type", "ma_period",
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

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
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

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
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

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
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
    results, cached_at = load_cache_file(BIOTECH_CACHE)
    results = _normalize_keys(results, _BIOTECH_KEY_MAP)

    cache_age = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age = int((datetime.now() - cached_dt).total_seconds())
        except Exception as e:
            print(f"[Warning] {e}")

    return ScanResultsResponse(
        status="success",
        count=len(results),
        data=results,
        cached_at=cached_at,
        cache_age_seconds=cache_age,
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


def fetch_multi_exchange_perps():
    """Multi-Exchange Perpetual Data: MEXC + Bitget combined."""
    mexc = fetch_mexc_funding_oi()
    bitget = fetch_bitget_funding_oi()

    all_symbols = set(mexc.keys()) | set(bitget.keys())
    result = {}

    for sym in all_symbols:
        m = mexc.get(sym, {})
        b = bitget.get(sym, {})

        exchanges = []
        if m:
            exchanges.append("MEXC")
        if b:
            exchanges.append("Bitget")

        mexc_vol = m.get("volume24", 0) if m else 0
        bitget_vol = b.get("volume24_usdt", 0) if b else 0

        if bitget_vol >= mexc_vol and b:
            best = "Bitget"
            best_fr = b.get("funding_rate", 0)
            best_oi_ratio = b.get("oi_ratio", 0)
            best_oi_usdt = b.get("oi_usdt", 0)
            best_vol = bitget_vol
        elif m:
            best = "MEXC"
            best_fr = m.get("funding_rate", 0)
            best_oi_ratio = m.get("oi_ratio", 0)
            best_oi_usdt = m.get("oi_usdt", 0)  # FIX: war hold_vol (Kontraktanzahl statt USDT)
            best_vol = mexc_vol
        else:
            continue

        result[sym] = {
            "exchanges": exchanges,
            "best_exchange": best,
            "funding_rate": best_fr,
            "oi_ratio": best_oi_ratio,
            "oi_usdt": best_oi_usdt,
            "volume24_usdt": max(mexc_vol, bitget_vol),
            "mexc": m,
            "bitget": b,
        }

    return result


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


def _calculate_risk(change_24h, change_7d, vol_mcap_pct, funding_rate, phase):
    """Berechnet Risiko-Level basierend auf Marktdaten."""
    c24 = abs(change_24h or 0)
    c7d_raw = change_7d or 0
    vm = vol_mcap_pct or 0
    fr = abs((funding_rate or 0) * 100)
    reasons = []

    # Verschärfte Schwellen — Trader brauchen ehrliche Warnungen
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
    all_coins = _fetch_coingecko_markets(pages=8)
    if not all_coins:
        return {"coins": [], "stats": {"error": "No data"}}

    perp_data = _prefetched_perps if _prefetched_perps is not None else fetch_multi_exchange_perps()

    btc_7d = 0
    for c in all_coins:
        if c.get("id") == "bitcoin":
            btc_7d = c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d") or 0
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
            perp_info = perp_data.get(symbol, {})
            if not perp_info:
                perp_info = perp_data.get(f"1000{symbol}", {})
            if not perp_info:
                perp_info = perp_data.get(f"10000{symbol}", {})
            has_perp = bool(perp_info)
            funding_rate = perp_info.get("funding_rate", 0)
            oi_ratio = perp_info.get("oi_ratio", 0)
            best_exchange = perp_info.get("best_exchange", "")
            exchanges = perp_info.get("exchanges", [])

            # Skip stablecoins + wrapped
            if symbol in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "WBTC", "WETH", "STETH", "RETH"):
                continue

            vol_mcap_ratio = (vol_24h / mcap * 100) if mcap > 0 else 0
            narrative = CRYPTO_NARRATIVES.get(cid, "")
            is_trending = cid in trending_ids
            # BTC-relative Performance (zeigt Alpha vs. Markt)
            btc_relative_7d = round(change_7d - btc_7d, 2) if btc_7d else round(change_7d, 2)

            base_entry = {
                "Symbol": symbol, "Name": name, "ID": cid,
                "Price": price, "MCap": mcap, "Vol24h": vol_24h,
                "Change1h": round(change_1h, 2), "Change24h": round(change_24h, 2),
                "Change7d": round(change_7d, 2), "Change14d": round(change_14d, 2),
                "Change30d": round(change_30d, 2),
                "VolMCapRatio": round(vol_mcap_ratio, 2),
                "HasPerp": has_perp, "FundingRate": funding_rate,
                "OI_Ratio": oi_ratio,
                "BestExchange": best_exchange,
                "Exchanges": exchanges,
                "Narrative": narrative,
                "High24h": high_24h, "Low24h": low_24h,
                "IsTrending": is_trending,
                "BtcRelative7d": btc_relative_7d,
            }

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
                        if total_score >= 40 and phase != 3:
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
                    if change_7d >= 100:
                        degen_score += 30
                    elif change_7d >= 50:
                        degen_score += 25
                    else:
                        degen_score += 15

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
                    if change_7d > 200:
                        degen_score -= 15
                    elif change_7d > 150:
                        degen_score -= 10

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

                # OI/Vol Ratio — NUR mit absolutem OI-Gate (sonst = Illiquidität)
                # Mindestens $200k OI nötig damit der Ratio überhaupt Bedeutung hat
                if perp_oi_usdt >= 200_000:
                    if oi_ratio >= 3.0:
                        whale_score += 25
                        signals.append(f"OI/Vol {oi_ratio:.1f}x (stark gehebelt)")
                    elif oi_ratio >= 1.5:
                        whale_score += 18
                        signals.append(f"OI/Vol {oi_ratio:.1f}x (Positionen im Aufbau)")
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

                if whale_score >= 35:
                    entry = dict(base_entry)
                    entry["WhaleScore"] = min(100, whale_score)
                    entry["Signals"] = signals
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

    def _add_to_unified(entries, source_name, score_key):
        for entry in entries:
            sym = entry.get("Symbol", "")
            raw_score = entry.get(score_key, 0)
            vm = entry.get("VolMCapRatio", 0)
            c24 = entry.get("Change24h", 0)
            c7d = entry.get("Change7d", 0)
            fr = entry.get("FundingRate", 0)

            phase, phase_label, phase_color = _classify_phase(c24, c7d, vm, btc_24h)
            risk_level, risk_color, risk_reasons = _calculate_risk(c24, c7d, vm, fr, phase)

            # Phase-Multiplier: Phase 3 = deutliche Strafe, Phase 1 = leichter Boost
            if phase == 1:
                score = min(100, int(raw_score * 1.05))  # +5% — konservativ
            elif phase == 3:
                score = min(100, int(raw_score * 0.6))   # -40% — überhitzt = NICHT kaufen
            else:
                score = raw_score

            # Signal-Text basierend auf Phase — ehrlich und direkt
            alpha = c24 - btc_24h
            if phase == 1:
                if score >= 70:
                    signal_text = "Smart Money Accumulation — guter Einstieg"
                elif score >= 40:
                    signal_text = "Volume-Anomalie — beobachten"
                else:
                    signal_text = "Leichte Aktivität"
            elif phase == 2:
                if c24 > 12:
                    signal_text = f"Starker Breakout +{c24:.0f}% — Einstieg riskant"
                elif score >= 60:
                    signal_text = "Breakout bestätigt — Momentum, enger Stop"
                else:
                    signal_text = "Ausbruch läuft — Vorsicht"
            else:
                signal_text = f"ÜBERHITZT +{c24:.0f}%/24h — NICHT kaufen, Korrektur kommt"
                if c7d > 40:
                    signal_text = f"ÜBERHITZT +{c7d:.0f}%/7d — Gewinnmitnahmen wahrscheinlich"

            # Grade berechnen
            if score >= 80:
                grade, grade_label = "S", "Excellent"
            elif score >= 60:
                grade, grade_label = "A", "Stark"
            elif score >= 40:
                grade, grade_label = "B", "Solide"
            elif score >= 25:
                grade, grade_label = "C", "Schwach"
            else:
                grade, grade_label = "D", "Uninteressant"

            unified_entry = dict(entry)
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

    _add_to_unified(volume_spikes, "Volume Spike", "EarlyScore")
    _add_to_unified(micro_caps, "Micro-Cap", "DegenScore")
    _add_to_unified(whale_accumulations, "Whale", "WhaleScore")

    # Konfluenz-Bonus: Coin in 2+ Strategien = stärkeres Signal
    for sym, entry in seen_symbols.items():
        n_sources = len(entry.get("sources", []))
        if n_sources >= 3:
            entry["score"] = min(100, entry["score"] + 10)
            entry["signal_text"] += f" | KONFLUENZ: {', '.join(entry['sources'])}"
        elif n_sources == 2:
            entry["score"] = min(100, entry["score"] + 5)
            entry["signal_text"] += f" | {', '.join(entry['sources'])}"

    # Sortierung: Score absteigend — Coins aus ALLEN Phasen mischen
    # (vorher: Phase 1 zuerst → bei 300+ Phase-1-Coins kamen Breakout/Überhitzt nie in Top 50)
    all_unified = sorted(seen_symbols.values(), key=lambda x: -x["score"])

    # Proportionale Auswahl: Jede Phase bekommt mindestens ihre Top-Coins
    # damit Breakout und Überhitzt IMMER sichtbar sind
    phase_1 = [c for c in all_unified if c["phase"] == 1]
    phase_2 = [c for c in all_unified if c["phase"] == 2]
    phase_3 = [c for c in all_unified if c["phase"] == 3]

    MAX_DISPLAY = 120  # Mehr Coins analysiert (2000 statt 1000) → mehr Ergebnisse zeigen
    # Phase 2 + 3 immer ALLE zeigen (sind selten und wichtig), Rest Phase 1
    p2_coins = phase_2  # alle Breakouts
    p3_coins = phase_3  # alle Überhitzten
    p1_slots = max(0, MAX_DISPLAY - len(p2_coins) - len(p3_coins))
    p1_coins = phase_1[:p1_slots]

    # Zusammenfügen: Phase 2+3 zuerst (wichtiger), dann Phase 1, jeweils nach Score
    unified = sorted(p1_coins + p2_coins + p3_coins, key=lambda x: (1 if x["phase"] in (2, 3) else 2, -x["score"]))

    stats = {
        "total_coins": len(all_coins),
        "unified_count": len(unified),
        "phase_1_count": len(p1_coins),
        "phase_2_count": len(p2_coins),
        "phase_3_count": len(p3_coins),
        "total_found": len(all_unified),  # Gesamtzahl vor Limit
        "trending_coins": len(trending_ids),
        "btc_7d": btc_7d,
        "perps_total": len(perp_data),
        "data_source": _CG_MARKETS_STATUS.get("source"),
        "data_warning": _CG_MARKETS_STATUS.get("warning"),
        "partial_data": _CG_MARKETS_STATUS.get("partial", False),
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
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


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
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── BTC Divergenz ──
BTC_DIVERGENZ_CACHE = "/tmp/btc_divergenz_cache.json"

def _btc_divergenz_wrapper() -> None:
    """Compare BTC vs correlated assets for divergence signals."""
    try:
        assets = [
            # BTC nur als Referenz (wird NICHT in Ergebnisliste angezeigt)
            ("X:BTCUSD", "BTC", "Bitcoin"),
            # BTC-korrelierte Aktien — das ist was Trader interessiert
            ("MSTR", "MSTR", "MicroStrategy"),
            ("COIN", "COIN", "Coinbase"),
            ("MARA", "MARA", "Marathon Digital"),
            ("RIOT", "RIOT", "Riot Platforms"),
            ("CLSK", "CLSK", "CleanSpark"),
            ("BITF", "BITF", "Bitfarms"),
            ("HUT", "HUT", "Hut 8 Mining"),
            ("CIFR", "CIFR", "Cipher Mining"),
            ("IREN", "IREN", "Iris Energy"),
            ("BTDR", "BTDR", "Bitdeer Technologies"),
            ("GBTC", "GBTC", "Grayscale BTC Trust"),
            ("IBIT", "IBIT", "iShares Bitcoin Trust"),
            ("ETHE", "ETHE", "Grayscale ETH Trust"),
            ("BITO", "BITO", "ProShares BTC Strategy"),
        ]
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
                    r["signal"] = "KAUFEN"
                elif z_score < -1.5 and correlation > 0.5:
                    r["signal"] = "MEIDEN"
                elif abs(z_score) < 0.5:
                    r["signal"] = "ABWARTEN"
                else:
                    r["signal"] = "BEOBACHTEN"

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
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")
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
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


# ── Money Flow (Sector Performance) ──
MONEY_FLOW_CACHE = "/tmp/money_flow_cache.json"

SECTOR_ETFS = {
    "XLK": "Technologie", "XLF": "Finanzen", "XLV": "Gesundheit",
    "XLE": "Energie", "XLI": "Industrie", "XLY": "Konsum (zyklisch)",
    "XLP": "Konsum (defensiv)", "XLU": "Versorger", "XLRE": "Immobilien",
    "XLB": "Grundstoffe", "XLC": "Kommunikation",
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
                avg_vol = sum(b.get("v", 0) for b in bars[1:21]) / min(len(bars)-1, 20) if len(bars) > 1 else 1
                rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0

                # Fix 3a: On-Balance Volume (OBV) Trend
                closes = [b["c"] for b in reversed(bars)]
                volumes = [b.get("v", 0) for b in reversed(bars)]
                obv_values = calculate_obv(closes, volumes)
                if len(obv_values) >= 6:
                    obv_change = (obv_values[-1] - obv_values[-6]) / abs(obv_values[-6]) * 100 if obv_values[-6] != 0 else 0
                    price_change = (closes[-1] - closes[-6]) / closes[-6] * 100 if closes[-6] > 0 else 0
                    if obv_change > 10 and price_change < 2:
                        obv_signal = "ACCUMULATION"
                    elif obv_change < -10 and price_change > -2:
                        obv_signal = "DISTRIBUTION"
                    else:
                        obv_signal = "NEUTRAL"
                else:
                    obv_change = 0
                    obv_signal = "NEUTRAL"

                # Fix 3b: Chaikin Money Flow (CMF)
                highs = [b.get("h", 0) for b in reversed(bars)]
                lows = [b.get("l", 0) for b in reversed(bars)]
                cmf = _calculate_cmf(closes, highs, lows, volumes, period=20)
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
                    "ticker": ticker, "sector": name, "price": round(close, 2),
                    "change_1d": round(chg_1d, 2), "change_5d": round(chg_5d, 2),
                    "change_20d": round(chg_20d, 2), "volume": vol, "rvol": rvol,
                    "flow_signal": flow,
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
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


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


def _flatten_new_listing_pipeline_results(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert rich module results to the flat table shape used by the FastAPI UI."""
    flat = []

    def _append_signal(entry: Dict[str, Any], bucket: str) -> None:
        sig = entry.get("signal", {}) or {}
        pump = sig.get("pump_data", {}) or {}
        raw_symbol = entry.get("symbol") or sig.get("symbol") or ""
        display_symbol = _display_crypto_contract_symbol(raw_symbol)
        timing = sig.get("timing", "")
        if bucket == "signals":
            signal_label = "SHORT" if "SHORT" in timing.upper() else timing or "SHORT"
        elif bucket == "watchlist":
            signal_label = "WATCH"
        else:
            signal_label = sig.get("grade", "MONITOR")

        flat.append({
            "symbol": display_symbol,
            "exchange": entry.get("exchange", ""),
            "contract": raw_symbol,
            "price": pump.get("current_price", sig.get("entry", 0)),
            "change_24h": entry.get("change_24h", 0),
            "volume_24h": pump.get("volume_usd_24h", 0),
            "pump_pct": pump.get("pump_pct", 0),
            "from_ath_pct": pump.get("from_ath_pct", 0),
            "exhaustion_score": sig.get("exh_score", 0),
            "exhaustion_details": sig.get("exh_details", []),
            "signal": signal_label,
            "confirmations": 0,
            "listing_date": entry.get("detected_at", ""),
            "hours_tracked": pump.get("hours_tracked", 0),
            "listing_age_hours": pump.get("listing_age_hours"),
            "vol_ratio": pump.get("vol_ratio", 0),
            "funding_rate": pump.get("funding_rate", 0),
            "long_pct": pump.get("long_pct", 0),
            "red_streak": pump.get("red_streak", 0),
            "btc_divergence": pump.get("btc_divergence", 0),
            "rr1": sig.get("rr1", 0),
            "rr2": sig.get("rr2", 0),
            "rr_effective": sig.get("rr_effective", sig.get("rr1", 0)),
            "tp1_missed": sig.get("tp1_missed", False),
            "tp2_missed": sig.get("tp2_missed", False),
            "grade": sig.get("grade", ""),
            "safety_ok": sig.get("safety_ok", False),
            "safety_warnings": sig.get("safety_warnings", []),
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
            "price": item.get("price", 0),
            "pump_pct": item.get("pump_pct", 0),
            "from_ath_pct": item.get("from_ath_pct", 0),
            "exhaustion_score": item.get("exh_score", 0),
            "signal": item.get("timing", "MONITOR"),
            "funding_rate": item.get("funding_rate", 0),
            "grade": item.get("grade", ""),
            "hours_tracked": item.get("hours_tracked", 0),
            "vol_ratio": item.get("volume_ratio", 0),
            "safety_ok": item.get("safety_ok", False),
            "source": "monitoring",
        })

    flat.sort(key=lambda r: (
        0 if str(r.get("signal", "")).startswith("SHORT") else 1,
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
    stats = {
        "new_listings": len(results) if results else 0,
        "exchanges_monitored": len(set(r.get("exchange", "") for r in results)) if results else 0,
        "active_signals": len([r for r in results if r.get("signal", "").startswith("SHORT")]) if results else 0,
    }
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age, "stats": stats}


# ── Volume Spikes Scanner ──
VOLUME_SPIKES_CACHE = "/tmp/volume_spikes_cache.json"

def _volume_spikes_wrapper() -> None:
    """Find stocks with unusual volume (RVOL > 3.0, price > $2)."""
    try:
        # Fetch market snapshot using /gainers endpoint (Starter plan compatible) for higher volume stocks
        # Combine with /losers endpoint to get comprehensive coverage
        spikes = []

        for endpoint in ["gainers", "losers"]:
            snap_url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/{endpoint}"
            snap_resp = rate_limited_get(snap_url, params={"apiKey": POLYGON_KEY, "limit": 250})

            if snap_resp.status_code != 200:
                if snap_resp.status_code == 403:
                    print(f"[Warning] 403 Forbidden on {endpoint} endpoint - check API plan")
                continue

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
                            "ticker": t.get("ticker", ""),
                            "price": round(price, 2),
                            "change_pct": round(chg, 2),
                            "volume": vol,
                            "rvol": round(rvol, 2),
                            "dollar_volume": round(dollar_volume, 0),
                            "signal_type": signal_type,
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
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


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
                total_post = len(post_or) if post_or else 1
                hold_pct = hold_bars / total_post
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
                    "current_price": round(current_price, 2),
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
                rows += f'<td style="padding:8px;border-bottom:1px solid #eee">E: ${b["entry"]} S: ${b["stop"]} T: ${b["target1"]}</td></tr>'
            body = f'''<html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
            <h2 style="color:#1a73e8">🔔 ORB Breakouts — {now_et.strftime("%H:%M")} ET</h2>
            <p style="color:#666">{len(alert_breakouts)} Top-Setups (Grade S/A)</p>
            <table style="width:100%;border-collapse:collapse;font-size:13px">
            <tr style="background:#f5f5f5"><th style="padding:8px;text-align:left">Ticker</th>
            <th style="padding:8px;text-align:left">Setup</th><th style="padding:8px;text-align:left">Preis</th>
            <th style="padding:8px;text-align:left">Gap</th><th style="padding:8px;text-align:left">RVOL</th>
            <th style="padding:8px;text-align:left">E/S/T</th></tr>
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
    return {"status": "success", "data": results, "cached_at": cached_at, "cache_age_seconds": cache_age}


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
            gainers_chgs = []
            losers_chgs = []

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
            }
            print(f"[Crash Monitor] Breadth: {up} up, {down} down, ratio={ratio}, signal={breadth_signal}")
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

            # BLS Employment Situation / NFP
            ("2026-05-08", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for April 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-06-05", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for May 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-07-02", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for June 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),
            ("2026-08-07", "NFP (Non-Farm Payroll)", "high", "US Employment Situation for July 2026", "Sehr Hoch", "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", 8, 30, "labor"),

            # BLS PPI
            ("2026-05-13", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for April 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-06-11", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for May 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-07-15", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for June 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),
            ("2026-08-13", "PPI (Erzeugerpreisindex)", "medium", "US Producer Price Index for July 2026", "Hoch", "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", 8, 30, "inflation"),

            # BEA GDP / PCE
            ("2026-04-30", "GDP (Advance Estimate)", "high", "US GDP Advance Estimate, Q1 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-04-30", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for March 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-05-28", "GDP (Second Estimate)", "high", "US GDP Second Estimate and Corporate Profits, Q1 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-05-28", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for April 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-06-25", "GDP (Third Estimate)", "high", "US GDP Third Estimate, Q1 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-06-25", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for May 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),
            ("2026-07-30", "GDP (Advance Estimate)", "high", "US GDP Advance Estimate, Q2 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "growth"),
            ("2026-07-30", "PCE / Personal Income and Outlays", "high", "US Personal Income and Outlays for June 2026", "Sehr Hoch", "BEA", "https://www.bea.gov/news/schedule", 8, 30, "inflation"),

            # Census Retail Sales / Advance Economic Indicators
            ("2026-05-14", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for April 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-06-17", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for May 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-07-16", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for June 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
            ("2026-08-14", "Retail Sales (Einzelhandelsumsaetze)", "medium", "US Advance Monthly Retail Trade Report for July 2026", "Hoch", "Census", "https://www.census.gov/retail/release_schedule.html", 8, 30, "consumer"),
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

        # CPI (released ~12th of each month)
        for month in range(1, 13):
            try:
                next_cpi = _calculate_next_occurrence(month, 12)
                _add_event(events, date_str=next_cpi, event="CPI (Verbraucherpreisindex)",
                           importance="high", description="US Consumer Price Index YoY (geschätzt)",
                           impact="Sehr Hoch", source="Estimated schedule", estimated=True,
                           hour_et=8, minute_et=30)
            except Exception as e:
                print(f"[Warning] {e}")

        # NFP (1st Friday of each month, approx. 3rd-7th)
        for month in range(1, 13):
            try:
                next_nfp = _calculate_next_occurrence(month, 5)
                _add_event(events, date_str=next_nfp, event="NFP (Non-Farm Payroll)",
                           importance="high", description="US Employment Report (geschätzt)",
                           impact="Sehr Hoch", source="Estimated schedule", estimated=True,
                           hour_et=8, minute_et=30)
            except Exception as e:
                print(f"[Warning] {e}")

        # GDP (end of each quarter)
        for month in [3, 6, 9, 12]:
            try:
                next_gdp = _calculate_next_occurrence(month, 28)
                _add_event(events, date_str=next_gdp, event="GDP",
                           importance="high", description="Gross Domestic Product Report (geschätzt)",
                           impact="Sehr Hoch", source="Estimated schedule", estimated=True,
                           hour_et=8, minute_et=30)
            except Exception as e:
                print(f"[Warning] {e}")

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

        # PPI (Producer Price Index - ~15th of each month)
        for month in range(1, 13):
            try:
                next_ppi = _calculate_next_occurrence(month, 15)
                _add_event(events, date_str=next_ppi, event="PPI (Erzeugerpreisindex)",
                           importance="medium", description="US Producer Price Index MoM (geschätzt)",
                           impact="Hoch", source="Estimated schedule", estimated=True,
                           hour_et=8, minute_et=30)
            except Exception as e:
                print(f"[Warning] {e}")

        # Retail Sales (~15th of each month)
        for month in range(1, 13):
            try:
                next_retail = _calculate_next_occurrence(month, 16)
                _add_event(events, date_str=next_retail, event="Retail Sales (Einzelhandelsumsätze)",
                           importance="medium", description="US Monthly Retail Sales Report (geschätzt)",
                           impact="Hoch", source="Estimated schedule", estimated=True,
                           hour_et=8, minute_et=30)
            except Exception as e:
                print(f"[Warning] {e}")

        # ISM Manufacturing PMI (1st business day of each month)
        for month in range(1, 13):
            try:
                next_ism = _calculate_next_occurrence(month, 1)
                _add_event(events, date_str=next_ism, event="ISM Manufacturing PMI",
                           importance="medium", description="Institute for Supply Management Manufacturing Index (geschätzt)",
                           impact="Hoch", source="Estimated schedule", estimated=True,
                           hour_et=10, minute_et=0)
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
            "official_count": official_count,
            "estimated_count": estimated_count,
            "official_sources": ["Federal Reserve", "BLS", "BEA", "Census"],
            "timestamp": datetime.now().isoformat(),
            "note": "FOMC/FED, CPI, NFP, PPI, GDP/PCE, Retail Sales and Census Advance Economic Indicators use official 2026 source schedules. Earnings, ISM and weekly claims remain marked estimates."
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
        "worst_trade": worst_trade, "trades": trades[-50:],
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
    if not POLYGON_KEY:
        raise HTTPException(status_code=400, detail="POLYGON_KEY not configured")

    result = _run_backtest(request.ticker, request.strategy, request.months)

    # Cache result
    try:
        cache_key = f"/tmp/backtest_{request.ticker}_{request.strategy}.json"
        with open(cache_key, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "results": result}, f, default=_serialize_json)
    except Exception as e:
        print(f"[Warning] {e}")

    return result


@app.get("/api/backtest-strategies")
def list_backtest_strategies():
    """List all available backtest strategies."""
    indicator_strats = [
        {"id": "sma_crossover", "name": "SMA Crossover (20/50)", "category": "Indikator", "direction": "long"},
        {"id": "ema_crossover", "name": "EMA Crossover (9/21)", "category": "Indikator", "direction": "long"},
        {"id": "rsi_mean_reversion", "name": "RSI Mean Reversion", "category": "Indikator", "direction": "long"},
        {"id": "macd", "name": "MACD Crossover", "category": "Indikator", "direction": "long"},
        {"id": "bollinger_bands", "name": "Bollinger Bands", "category": "Indikator", "direction": "long"},
        {"id": "mean_reversion_sma", "name": "Mean Reversion (SMA50)", "category": "Indikator", "direction": "long"},
        {"id": "turtle_breakout", "name": "Turtle Breakout (Donchian 20/10)", "category": "Indikator", "direction": "long"},
    ]
    rule_strats = []
    for name, rule in BACKTEST_RULES.items():
        rule_strats.append({
            "id": name,
            "name": name,
            "category": "Scanner",
            "direction": rule.get("direction", "long"),
        })
    return {"strategies": indicator_strats + rule_strats}


@app.get("/api/backtest-results")
def get_backtest_results(ticker: str = Query("AAPL"), strategy: str = Query("sma_crossover")):
    """Get cached backtest results."""
    cache_key = f"/tmp/backtest_{ticker}_{strategy}.json"
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


def _load_coupons() -> Dict:
    """Load coupons from JSON file."""
    coupon_path = "/tmp/alpha_station_coupons.json"
    if os.path.exists(coupon_path):
        try:
            with open(coupon_path, "r") as f:
                return json.load(f)
        except Exception:
            return {"coupons": []}
    return {"coupons": []}


def _save_coupons(data: Dict):
    """Save coupons to JSON file."""
    try:
        coupon_path = "/tmp/alpha_station_coupons.json"
        with open(coupon_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Error] Coupons speichern fehlgeschlagen: {e}")


def _load_tickets() -> Dict:
    """Load support tickets from JSON file."""
    ticket_path = "/tmp/alpha_station_tickets.json"
    if os.path.exists(ticket_path):
        try:
            with open(ticket_path, "r") as f:
                return json.load(f)
        except Exception:
            return {"tickets": [], "next_id": 1}
    return {"tickets": [], "next_id": 1}


def _save_tickets(data: Dict):
    """Save support tickets to JSON file."""
    try:
        ticket_path = "/tmp/alpha_station_tickets.json"
        with open(ticket_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Error] Tickets speichern fehlgeschlagen: {e}")


# ── Admin: User Management ──

@app.get("/api/admin/users")
def admin_list_users(authorization: Optional[str] = Header(None)):
    """List all users (admin only)."""
    _require_admin(authorization)

    db = json.loads(open(AUTH_DB_PATH).read()) if os.path.exists(AUTH_DB_PATH) else {"users": {}}
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
    db = json.loads(open(AUTH_DB_PATH).read()) if os.path.exists(AUTH_DB_PATH) else {"users": {}}

    if email not in db.get("users", {}):
        raise HTTPException(status_code=404, detail="User not found")

    db["users"][email]["plan"] = plan

    # Save database
    try:
        with open(AUTH_DB_PATH, "w") as f:
            json.dump(db, f, indent=2)
        return {"success": True, "message": f"Plan für {email} auf {plan} aktualisiert"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern: {str(e)}")


@app.delete("/api/admin/users/{email}")
def admin_delete_user(email: str, authorization: Optional[str] = Header(None)):
    """Delete a user from database (admin only)."""
    _require_admin(authorization)

    email = email.lower().strip()

    # Load database
    db = json.loads(open(AUTH_DB_PATH).read()) if os.path.exists(AUTH_DB_PATH) else {"users": {}}

    if email not in db.get("users", {}):
        raise HTTPException(status_code=404, detail="User not found")

    del db["users"][email]

    # Save database
    try:
        with open(AUTH_DB_PATH, "w") as f:
            json.dump(db, f, indent=2)
        return {"success": True, "message": f"Benutzer {email} gelöscht"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern: {str(e)}")


@app.get("/api/admin/stats")
def admin_get_stats(authorization: Optional[str] = Header(None)):
    """Get admin statistics (admin only)."""
    _require_admin(authorization)

    db = json.loads(open(AUTH_DB_PATH).read()) if os.path.exists(AUTH_DB_PATH) else {"users": {}}
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
    db = json.loads(open(AUTH_DB_PATH).read()) if os.path.exists(AUTH_DB_PATH) else {"users": {}}

    if user_email not in db.get("users", {}):
        raise HTTPException(status_code=404, detail="User not found")

    # Update user plan
    db["users"][user_email]["plan"] = coupon["plan"]

    # Increment coupon uses
    coupon_data["coupons"][coupon_index]["uses"] += 1

    # Save both
    try:
        with open(AUTH_DB_PATH, "w") as f:
            json.dump(db, f, indent=2)
        _save_coupons(coupon_data)

        return {
            "success": True,
            "message": f"Plan auf {coupon['plan']} aktualisiert via Coupon {code}",
            "new_plan": coupon["plan"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern: {str(e)}")


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
