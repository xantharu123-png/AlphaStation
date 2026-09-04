"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        ALPHA STATION V68.0 PRO                               ║
║                     Multi-Asset Scanner & Analyzer                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Version: 68.0 (Intensives Strategie-Audit — Double-Pass)                   ║
║  Date: 12. März 2026                                                         ║
║  Author: Miroslav                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  V68.0 STRATEGIE-AUDIT — Aktien + BI Scanner + BioTech Scanner:             ║
║  ✅ Relative Strength: RS-Outperformance ohne abs > 0 (Korrekturen)        ║
║  ✅ SPY-Override: Nur im Crash-Modus (<-3%), Rebounds nicht blockiert       ║
║  ✅ Konsolidierung High-Vol → Akkumulation (Bonus statt Penalty)           ║
║  ✅ Gap-Handling: Midpoint als Open-Schätzung statt Doji-Artefakt          ║
║  ✅ Timing: Dynamisch max(8%, 3.5×ATR) statt hardcoded 8%                 ║
║  ✅ BI: OBV Crypto symmetrisch, Resilience gecappt, Grading geglättet     ║
║  ✅ BI: Order Block + Liquidity Level Key-Mismatch behoben (KeyError)      ║
║  ✅ BioTech: Catalyst gewichtet, Momentum Floor, Readout-Cap bei 100       ║
║  ✅ VP: Value Area Overshoot-Guard für exaktere 70%-Grenze                 ║
║  V70.7: Full Audit Fix — 14 Findings (Divergenz + Early Movers)              ║
║  V67.3: Strategy Audit & Fixes (8 Fixes)                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
import re
import pandas as pd
import requests
import anthropic
import json
import pytz
import numpy as np
import time
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1 as components

AI_PROVIDER_MODEL = "".join(["clau", "de", "-sonnet-4-20250514"])

# Volume Profile Engine V1.1 (Audit-Fixed)
try:
    from volume_profile import (
        calculate_volume_profile as vp_calculate_profile,
        analyze_vp_signals as vp_analyze_signals,
        get_vp_lookback_for_strategy,
        get_strategy_type_for_scanner
    )
    VP_AVAILABLE = True
except ImportError:
    VP_AVAILABLE = False

# Interactive Brokers TWS Integration V1.0
try:
    import asyncio
    # Python 3.13+ entfernt auto-erstellte Event-Loops — manuell erstellen
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    import nest_asyncio
    nest_asyncio.apply()
    from ib_insync import IB, Stock, Future, Forex, Crypto, LimitOrder, StopOrder, Order
    IB_INSYNC_AVAILABLE = True
except ImportError:
    IB_INSYNC_AVAILABLE = False

# ── Module Imports (V69.6 Refactoring) ──
from modules.indicators import (
    calculate_close_position, estimate_crypto_atr, calculate_atr_from_ohlc,
    calculate_atr_14, calculate_adx, calculate_rsi_from_bars, calculate_macd,
    calculate_stochastic, calculate_sma, calculate_ema, calculate_ma_distance,
    calculate_vwap, calculate_ema_series, calculate_obv
)
from modules.scorers import (
    assess_breakout_health, calculate_confluence_score,
    calculate_alpha_score, calculate_setup_score, calculate_setup_score_crypto,
    calculate_alpha_score_crypto, is_signal_significant,
    calculate_exhaustion_score, get_exhaustion_grade, calculate_pm_quality_score
)

from modules.strategies import (
    STRATEGIES, FUTURES_STRATEGIES, FOREX_STRATEGIES, CRYPTO_STRATEGIES,
    INTERNATIONAL_STRATEGIES, BACKTEST_STRATEGY_RULES,
    get_strategies_for_market, apply_strategy, classify_pm_setup
)
from modules.patterns import (
    validate_flag_pattern, analyze_candles, detect_flag_pattern_multiday,
    analyze_breakout_imminent, find_pivots, check_fibonacci_ratio,
    HARMONIC_PATTERNS, identify_harmonic_pattern, scan_harmonic_patterns,
    scan_wyckoff_single, scan_wyckoff_batch,
    detect_volume_imbalances, detect_order_blocks, detect_liquidity_levels,
    format_smc_setup, detect_wolfe_waves, detect_chart_patterns,
    find_harmonic_for_chart
)
try:
    # scan_harmonic_batch ist in modules/patterns.py aktuell nicht vorhanden
    # (Migration "Moved to modules/patterns.py" unvollständig, siehe Audit-Report).
    # Guarded Import: Harmonic-Batch-Scan degradiert dann zu "keine Treffer",
    # statt die komplette App beim Import zu crashen.
    from modules.patterns import scan_harmonic_batch
except ImportError:
    def scan_harmonic_batch(tickers, poly_key, days=120, timeframe="day"):
        print("[WARN] modules.patterns.scan_harmonic_batch fehlt — Harmonic-Batch-Scan deaktiviert")
        return {}
from modules.data_fetchers import (
    rate_limited_get, fetch_daily_candles_crypto, fetch_daily_candles,
    fetch_multi_day_data, fetch_historical_data_crypto, fetch_historical_data_stocks,
    fetch_ohlcv_for_chart, fetch_realtime_price_alpaca, fetch_realtime_price_polygon,
    get_ticker_news, get_ticker_details, fetch_backtest_daily_data, fetch_grouped_daily,
    get_binance_tradingview_symbol, _get_bpiq_catalysts,
    _calculate_biotech_catalyst_score, _detect_catalyst,
    _fetch_historical_yahoo, _fetch_ohlcv_yahoo, _fetch_ohlcv_polygon
)
from modules.brokers import (
    _get_ib_state, ib_connect, ib_disconnect, ib_is_connected,
    ib_get_contract, ib_calc_shares, ib_submit_bracket
)
from modules.backtests import (
    run_bi_v2_backtest, run_biotech_backtest, simulate_trade,
    run_full_backtest_grouped, run_full_backtest, compute_backtest_stats
)
from modules.scanners import (
    _bi_config_load, _bi_config_save, _bi_cache_load, _bi_cache_save,
    _bi_cache_age_str, _bi_progress_write, _bi_progress_read,
    _bi_scan_is_running, _bi_background_scan,
    _bi_cache_path, _bi_progress_path, _bi_progress_clear,
    _bi_stop_file, _bi_request_stop, _bi_should_stop, _bi_clear_stop,
    _biotech_config_load, _biotech_config_save, _biotech_cache_load,
    _biotech_cache_save, _biotech_progress_write,
    _biotech_progress_read, _biotech_background_scan,
    _biotech_quick_scan,
    _biotech_technical_score, _biotech_news_momentum, _biotech_risk_score,
    _fetch_biotech_universe,
    _compute_biotech_technical_from_bars, _scan_biotech_news,
    _check_clinical_trials, _biotech_universe_cache_load,
    _biotech_universe_cache_save,
    _biotech_progress_file, _biotech_stop_file, _biotech_request_stop,
    _biotech_should_stop, _biotech_clear_stop, _biotech_cache_file,
    _biotech_universe_cache_file,
    _autotrader_config_load, _autotrader_config_save, _autotrader_state_read,
    _autotrader_state_write, _autotrader_log,
    _autotrader_is_market_hours, _autotrader_should_stop, _autotrader_clear_stop,
    _autotrader_request_stop,
    autotrader_scan_once, autotrader_background_loop
)
from modules.chart_utils import create_lightweight_chart_html
from modules.analysis import (
    calculate_sr_from_historical, analyze_multi_day_pattern,
    find_wyckoff_for_chart, calculate_accumulation_score,
    _detect_chart_patterns, calculate_rvol_at_time,
    get_timing_assessment, generate_ai_chart_analysis,
    get_accumulation_display, check_earnings_proximity,
    compute_daily_metrics, _earnings_flag
)
from modules.premarket import (
    evaluate_pm_setups, get_pm_session_bars, _save_pm_setups,
    get_spy_pm_change, _load_pm_tracker
)
from modules.volume_analysis import (
    calculate_volume_profile, find_volume_voids, find_volume_voids_for_chart
)
from modules.helpers import (
    get_current_trading_session, get_volatility_regime, is_spac,
    _load_watchlist, format_vi_for_display, cluster_nearby_levels,
    combined_score, calculate_sr_levels_simple, calculate_sr_levels,
    _crypto_breakout_ok, check_signal, _pick_top_strikes,
    _resolve_sector_etf, get_heatmap_color, get_text_color
)

# IB Broker functions — Moved to modules/brokers.py (V69.9 refactoring)



# =============================================================================
# 🤖 AUTO-TRADER ENGINE V1.0 — Automated BI Signal → IBKR Order Pipeline
# =============================================================================

_AUTOTRADER_CONFIG_FILE = "/tmp/alpha_autotrader_config.json"
_AUTOTRADER_STATE_FILE = "/tmp/alpha_autotrader_state.json"
_AUTOTRADER_STOP_FILE = "/tmp/alpha_autotrader_stop"
_AUTOTRADER_LOG_FILE = "/tmp/alpha_autotrader_log.json"

_AUTOTRADER_DEFAULT_CONFIG = {
    "mode": "semi",                  # "full" = auto-submit, "semi" = submit with transmit=False
    "max_positions": 5,              # Max gleichzeitig offene Positionen
    "position_size_type": "dollar",  # "dollar" oder "shares"
    "position_size": 2000,           # $ pro Trade (oder Shares)
    "excluded_grades": ["A"],        # Grade A raus (Backtest-bestätigt: PF 0.39)
    "min_bi_pct": 55,                # Min BI Score in % (score/max_score)
    "min_smart_money": 2,            # Min Smart Money Hits
    "scan_interval_min": 15,         # Scan-Intervall in Minuten
    "max_daily_loss_pct": 3.0,       # Stop nach -3% Tagesverlust
    "cooldown_days": 5,              # Keine Doppel-Entries in X Tagen
    "trading_hours_only": True,      # Nur 9:30-16:00 ET
    "min_rr": 2.0,                   # Min Risk:Reward Ratio
    "max_tickers_scan": 300,         # Tickers pro Scan-Durchlauf
    "min_price": 5.0,               # Mindestpreis
    "min_volume": 200000,            # Mindestvolumen
}


# _autotrader_config_load — Moved to modules/scanners.py



# _autotrader_config_save — Moved to modules/scanners.py



# _autotrader_state_read — Moved to modules/scanners.py



# _autotrader_state_write — Moved to modules/scanners.py



# _autotrader_log — Moved to modules/scanners.py



# _autotrader_request_stop — Moved to modules/scanners.py


def _autotrader_should_stop():
    """Prüft ob Stop-Signal gesetzt ist."""
    return os.path.exists(_AUTOTRADER_STOP_FILE)


def _autotrader_clear_stop():
    """Löscht Stop-Signal."""
    try:
        os.remove(_AUTOTRADER_STOP_FILE)
    except Exception:
        pass


# _autotrader_is_market_hours — Moved to modules/scanners.py



def _autotrader_check_cooldown(ticker, cooldown_dict, cooldown_days):
    """Prüft ob Ticker noch im Cooldown ist."""
    if ticker not in cooldown_dict:
        return False
    last_trade_date = cooldown_dict[ticker]
    try:
        last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d")
        return (datetime.now() - last_dt).days < cooldown_days
    except Exception:
        return False


# autotrader_scan_once — Moved to modules/scanners.py



# autotrader_background_loop — Moved to modules/scanners.py



# =============================================================================
# 0. API HELPERS (Rate Limiting + Caching + Logging)
# =============================================================================
def _debug_log(msg, error=None):
    """Optionales Debug-Logging (nur wenn debug_mode aktiv)."""
    if st.session_state.get("debug_mode", False):
        if error:
            print(f"[ALPHA DEBUG] {msg}: {error}")
        else:
            print(f"[ALPHA DEBUG] {msg}")

# rate_limited_get — Moved to modules/data_fetchers.py (V69.9 refactoring)
# Rate limiter globals dort (thread-safe mit Lock)


# SPAC SIC Codes (Blank Checks, Shell Companies)
SPAC_SIC_CODES = {"6770", "6726"}

# Cache für SPAC-Ticker (wird beim Laden der CS-Liste befüllt)
SPAC_TICKERS = set()

@st.cache_data(ttl=3600)
def load_common_stock_tickers_cached(api_key):
    """Cached Version: Lädt alle Common Stock Tickers (1h Cache). Filtert SPACs raus.
    Returns: (common_stocks: set, spac_tickers: set)
    """
    try:
        url = "https://api.polygon.io/v3/reference/tickers"
        params = {
            "type": "CS",
            "market": "stocks",
            "active": "true",
            "limit": 1000,
            "apiKey": api_key
        }

        all_tickers = set()
        _spac_set = set()
        next_url = None

        for _ in range(20):
            if next_url:
                resp = rate_limited_get(next_url, timeout=30).json()
            else:
                resp = rate_limited_get(url, params=params, timeout=30).json()

            results = resp.get("results", [])
            for r in results:
                ticker = r.get("ticker", "")
                if not ticker:
                    continue
                ticker_upper = ticker.upper()
                sic = r.get("sic_code", "")
                name = r.get("name", "")

                # SPAC-Erkennung: SIC Code 6770/6726 ODER Name enthält "Acquisition Corp" etc.
                if sic in SPAC_SIC_CODES or is_spac(name):
                    _spac_set.add(ticker_upper)
                    continue  # Nicht in Common Stock Liste aufnehmen

                all_tickers.add(ticker_upper)

            next_url = resp.get("next_url")
            if next_url:
                next_url = f"{next_url}&apiKey={api_key}"
            else:
                break

        return all_tickers, _spac_set
    except Exception as e:
        print(f"Fehler beim Laden der Aktien-Liste: {e}")
        return set()

# =============================================================================
# CRYPTO SYMBOL VALIDATOR FOR TRADINGVIEW
# =============================================================================
# get_binance_tradingview_symbol — Moved to modules/data_fetchers.py

# =============================================================================
# 1. INITIALISIERUNG
# =============================================================================
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state:
    st.session_state.scan_results = []
if "active_filters" not in st.session_state:
    st.session_state.active_filters = {}
if "additional_filters" not in st.session_state:
    st.session_state.additional_filters = {}
if "current_strategy" not in st.session_state:
    st.session_state.current_strategy = ""
if "filter_reset_counter" not in st.session_state:
    st.session_state.filter_reset_counter = 0
if "market_type" not in st.session_state:
    st.session_state.market_type = "Krypto"
if "active_trading_session" not in st.session_state:
    st.session_state.active_trading_session = "Regular"
if "debug_mode" not in st.session_state:
    st.session_state.debug_mode = False
# IBKR TWS Session State
if "ib_port" not in st.session_state:
    st.session_state.ib_port = 7497  # 7497=Paper, 7496=Live
if "ib_position_size" not in st.session_state:
    st.session_state.ib_position_size = 100
if "ib_size_type" not in st.session_state:
    st.session_state.ib_size_type = "Shares"
if "ib_orders_log" not in st.session_state:
    st.session_state.ib_orders_log = []
if "ib_show_form" not in st.session_state:
    st.session_state.ib_show_form = None  # None or ticker string
# Auto-Trader Session State
if "autotrader_thread" not in st.session_state:
    st.session_state.autotrader_thread = None
if "watchlist" not in st.session_state:
    # Lade persistierte Watchlist falls vorhanden
    try:
        with open("/tmp/alpha_station_watchlist.json", "r") as _f:
            st.session_state.watchlist = json.load(_f)
    except Exception as e:
        st.session_state.watchlist = []
if "selected_row_index" not in st.session_state:
    st.session_state.selected_row_index = 0
if "sr_levels" not in st.session_state:
    st.session_state.sr_levels = {"support": [], "resistance": []}
if "fib_info" not in st.session_state:
    st.session_state.fib_info = {}
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = False
if "show_pm_watchlist" not in st.session_state:
    st.session_state.show_pm_watchlist = False
if "pm_watchlist_data" not in st.session_state:
    st.session_state.pm_watchlist_data = None
if "pm_spy_change" not in st.session_state:
    st.session_state.pm_spy_change = 0
if "show_ai_chart" not in st.session_state:
    st.session_state.show_ai_chart = False
if "ai_chart_ticker" not in st.session_state:
    st.session_state.ai_chart_ticker = None

# VERSION für Filter-Sync - erhöhe bei Strategie-Änderungen!
FILTER_VERSION = "70.4"
if st.session_state.get("filter_version") != FILTER_VERSION:
    st.session_state.filters_synced = False
    st.session_state.filter_version = FILTER_VERSION
    # FORCE: Lösche auch active_filters damit sie neu geladen werden
    st.session_state.active_filters = {}

# =============================================================================
# 2. STRATEGIE-DEFINITIONEN
# =============================================================================
# V68: VORTAG% DEFINITION (zentrale Dokumentation)
# ─────────────────────────────────────────────────────────────────────────────
# "Vortag %" hat je nach Markt eine ANDERE Semantik:
#   STOCKS/FUTURES/FOREX:  Einzelkerze — (Close - Open) / Open * 100
#   CRYPTO:                6-Tage-Durchschnitt — avg(abs(daily_change)) über 6 Tage
# WICHTIG: Gleicher Filtername, andere Berechnung! Crypto-Thresholds sind anders kalibriert.
# ─────────────────────────────────────────────────────────────────────────────
# STRATEGIES — Moved to modules/strategies.py (V69.6 refactoring)


# =============================================================================
# FUTURES STRATEGIEN 📈
# =============================================================================
# FUTURES_STRATEGIES — Moved to modules/strategies.py (V69.6 refactoring)


# =============================================================================
# FOREX STRATEGIEN 💱
# =============================================================================
# FOREX_STRATEGIES — Moved to modules/strategies.py (V69.6 refactoring)


# =============================================================================
# KRYPTO STRATEGIEN 🌐 (angepasst - keine Gaps/Pre-Post)
# RVOL bei Krypto = Turnover Ratio normalisiert (10% Turnover = 1.0)
# Typische Werte: 0.3-0.8 normal, >1.0 erhöht, >2.0 sehr hoch
# =============================================================================
# CRYPTO_STRATEGIES — Moved to modules/strategies.py (V69.6 refactoring)


# =============================================================================
# INTERNATIONALE AKTIEN STRATEGIEN 🌍 (angepasste Schwellenwerte!)
# EU/UK/JP Aktien bewegen sich weniger als US-Aktien → niedrigere Thresholds
# RVOL wird zur Laufzeit nach Tageszeit normalisiert
# =============================================================================
# INTERNATIONAL_STRATEGIES — Moved to modules/strategies.py (V69.6 refactoring)


# Funktion um Strategien basierend auf Markt zu bekommen
# get_strategies_for_market — Moved to modules/strategies.py (V69.6 refactoring)


# =============================================================================
# 3. HELPER FUNCTIONS
# =============================================================================
# get_current_trading_session — Moved to modules/helpers.py


# apply_strategy — Moved to modules/strategies.py (V69.6 refactoring)


# calculate_close_position — Moved to modules/indicators.py (V69.6 refactoring)



# =============================================================================
# BREAKOUT HEALTH — Fakeout-Erkennung & Exhaustion Detection
# =============================================================================
# Bewertet ob ein Breakout ECHT ist oder ob ein Selloff kommt.
#
# FAKEOUT-SIGNALE (Breakout ist NICHT echt):
#   - Low Volume Breakout:  RVOL < 1.5 → kein institutionelles Interesse
#   - Wick Rejection:       Langer oberer Docht → Verkäufer drücken zurück
#   - Close Weakness:       Close weit weg vom High → Käufer verlieren Kontrolle
#   - Gap Risk:             Unfilled bearish VI/FVG direkt über dem Preis
#
# EXHAUSTION-SIGNALE (Selloff kommt BALD):
#   - Overextension:        Zu weit zu schnell (>10% in einer Session)
#   - Volume Climax:        RVOL > 5x → oft das Top (alle haben schon gekauft)
#   - Wick Growing:         Oberer Docht wird größer → zunehmender Verkaufsdruck
#   - Body Shrinking:       Kerze wird kleiner → Momentum lässt nach
#
# BESTÄTIGUNGS-SIGNALE (Breakout ist ECHT):
#   - Volume Confirmation:  RVOL > 2.0 → institutionell getrieben
#   - Clean Close:          Close nahe High (>85%) → Käufer dominieren
#   - No Upper Wick:        Wenig Docht → kein Verkaufsdruck
#   - Prior Base:           Vortag% war flach → Breakout aus Konsolidierung
# =============================================================================

# assess_breakout_health — Moved to modules/scorers.py (V69.6 refactoring)



# calculate_confluence_score — Moved to modules/scorers.py (V69.6 refactoring)



# calculate_alpha_score — Moved to modules/scorers.py (V69.6 refactoring)



# calculate_setup_score — Moved to modules/scorers.py (V69.6 refactoring)



# estimate_crypto_atr — Moved to modules/indicators.py (V69.6 refactoring)



# calculate_setup_score_crypto — Moved to modules/scorers.py (V69.6 refactoring)



# calculate_alpha_score_crypto — Moved to modules/scorers.py (V69.6 refactoring)



# calculate_rvol_at_time — Moved to modules/analysis.py


# validate_flag_pattern — Moved to modules/patterns.py (V69.6 refactoring)



# =============================================================================
# V69: UNIVERSELLE MULTI-DAY CANDLESTICK-ANALYSE
# =============================================================================
# Holt 20-30 Tageskerzen via Polygon Aggregates API und berechnet:
# - Trend (SMA5/10/20, Higher Highs/Lows)
# - Support/Resistance Levels
# - Candlestick Patterns (Doji, Hammer, Engulfing, etc.)
# - Volume Profile (Accumulation/Distribution)
# - Consolidation Detection
# - Breakout-Bereitschaft
# Wird von ALLEN Strategien genutzt (nicht nur Bull Flag)
# =============================================================================

_CANDLE_ANALYSIS_CACHE = {}
_CANDLE_CACHE_TTL = 300  # 5 Minuten


# fetch_daily_candles_crypto — Moved to modules/data_fetchers.py (V69.9 refactoring)



# fetch_daily_candles — Moved to modules/data_fetchers.py (V69.9 refactoring)



# analyze_candles — Moved to modules/patterns.py (V69.6 refactoring)



# =============================================================================
# V69: ECHTE MULTI-DAY FLAG DETECTION (Daily Candlesticks)
# =============================================================================
# Holt 20 Tageskerzen via Polygon Aggregates API und erkennt:
# - Pole (Fahnenstange): 2-7 Kerzen mit starkem Trend
# - Flag (Konsolidierung): 2-7 Kerzen mit enger Range + sinkendem Volumen
# - Retracement: Flag darf max 50% der Pole zurückgeben
# =============================================================================

# detect_flag_pattern_multiday — Moved to modules/patterns.py (V69.6 refactoring)



# calculate_atr_from_ohlc — Moved to modules/indicators.py (V69.6 refactoring)



# calculate_atr_14 — Moved to modules/indicators.py (V69.6 refactoring)


# is_signal_significant — Moved to modules/scorers.py (V69.6 refactoring)



# fetch_multi_day_data — Moved to modules/data_fetchers.py (V69.9 refactoring)



# analyze_multi_day_pattern — Moved to modules/analysis.py



# =============================================================================
# BREAKOUT IMMINENT SCANNER 🔮 — Multi-Signal Composite Prediction
# Kombiniert 12 Faktoren um bevorstehende Breakouts vorherzusagen
# =============================================================================

# calculate_adx — Moved to modules/indicators.py (V69.6 refactoring)



# calculate_rsi_from_bars — Moved to modules/indicators.py (V69.6 refactoring)



# calculate_macd — Moved to modules/indicators.py (V69.6 refactoring)



# calculate_stochastic — Moved to modules/indicators.py (V69.6 refactoring)



# analyze_breakout_imminent — Moved to modules/patterns.py (V69.6 refactoring)



# =============================================================================
# HARMONIC PATTERN SCANNER 🦋
# Erkennt Gartley, Butterfly, Bat, Crab, Shark Patterns
# =============================================================================

# find_pivots — Moved to modules/patterns.py (V69.6 refactoring)



# check_fibonacci_ratio — Moved to modules/patterns.py (V69.6 refactoring)



# Harmonic Pattern Definitionen mit Fibonacci-Verhältnissen
# HARMONIC_PATTERNS — Moved to modules/patterns.py (V69.6 refactoring)



# identify_harmonic_pattern — Moved to modules/patterns.py (V69.6 refactoring)



# scan_harmonic_patterns — Moved to modules/patterns.py (V69.6 refactoring)



# scan_harmonic_batch — Moved to modules/patterns.py


# scan_wyckoff_single — Moved to modules/patterns.py (V69.6 refactoring)



# scan_wyckoff_batch — Moved to modules/patterns.py (V69.6 refactoring)



# find_wyckoff_for_chart — Moved to modules/analysis.py



# get_volatility_regime — Moved to modules/helpers.py

def validate_liquidity(volume, price, min_dollar_volume=100000):
    """
    Prüft ob genug Liquidität für einen Trade vorhanden ist.

    Gemini's Kritik: "Pennystocks mit 100 Aktien Volumen"

    Dollar Volume = Volume * Price
    Minimum: $100,000 für Day Trading
    """
    if volume <= 0 or price <= 0:
        return False, 0

    dollar_volume = volume * price
    is_liquid = dollar_volume >= min_dollar_volume

    return is_liquid, dollar_volume

def get_time_adjusted_liquidity_threshold(threshold, session="Regular"):
    """
    Passt den Liquiditäts-Schwellwert an die aktuelle Tageszeit an.

    PROBLEM: Am Morgen (10:00 ET) sind erst ~12% des Tagesvolumens gehandelt.
    Ein $10M Dollar-Volume-Filter killt dann fast alle Aktien, weil das
    tatsächlich gehandelte Volume noch zu niedrig ist.

    LÖSUNG: Senke den Schwellwert proportional zur Tageszeit.
    Der User meint mit "$10M Minimum" das TAGES-Volume, nicht das Morgen-Volume.

    Beispiel um 10:00 ET (expected_pct = 0.12):
    - User-Filter: $10M Minimum
    - Angepasst: $10M × 0.12 = $1.2M aktuell nötig
    - Eine Aktie mit $1.5M um 10:00 → PASS (wird auf ~$12.5M Tages-Vol kommen)

    Returns: Angepasster Schwellwert
    """
    if threshold <= 0:
        return 0

    # Für Pre/Post/Extended: Keine Anpassung
    if session in ["Pre-Market", "After-Hours", "Extended"]:
        return threshold

    try:
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        current_hour = now_et.hour + now_et.minute / 60

        market_open = 9.5
        market_close = 16.0

        if current_hour < market_open:
            return threshold
        if current_hour >= market_close:
            return threshold

        # Gleiches Volume Profile wie calculate_rvol_at_time
        volume_profile = [
            (9.5, 0.0), (10.0, 0.12), (10.5, 0.22), (11.0, 0.30),
            (11.5, 0.36), (12.0, 0.42), (12.5, 0.47), (13.0, 0.52),
            (13.5, 0.57), (14.0, 0.62), (14.5, 0.68), (15.0, 0.75),
            (15.5, 0.85), (16.0, 1.0),
        ]

        expected_pct = 0.0
        for i, (hour, pct) in enumerate(volume_profile):
            if current_hour <= hour:
                if i == 0:
                    expected_pct = 0.0
                else:
                    prev_hour, prev_pct = volume_profile[i-1]
                    time_ratio = (current_hour - prev_hour) / (hour - prev_hour)
                    expected_pct = prev_pct + time_ratio * (pct - prev_pct)
                break
        else:
            expected_pct = 1.0

        expected_pct = max(0.05, expected_pct)

        # Schwellwert proportional zur Tageszeit senken
        return threshold * expected_pct

    except Exception:
        return threshold

# =============================================================================
# ETF / ETP FILTER - Filtert Leveraged ETFs, ETNs, etc.
# =============================================================================
# Bekannte ETF-Suffixe und Patterns
ETF_BLACKLIST = {
    # Leveraged ETFs (2x, 3x)
    "TQQQ", "SQQQ", "UPRO", "SPXU", "UDOW", "SDOW", "UMDD", "SMDD",
    "QLD", "QID", "SSO", "SDS", "DDM", "DXD", "MVV", "MZZ",
    "UWM", "TWM", "UYM", "SZK", "ROM", "REW", "USD", "SSG",
    "AGQ", "ZSL", "UCO", "SCO", "BOIL", "KOLD", "NUGT", "DUST",
    "JNUG", "JDST", "LABU", "LABD", "TECS", "TECL", "SOXL", "SOXS",
    "FNGU", "FNGD", "WEBL", "WEBS", "NAIL", "DRV", "ERX", "ERY",
    "FAS", "FAZ", "TNA", "TZA", "SPXL", "SPXS", "URTY", "SRTY",
    "CURE", "PILL", "RETL", "WANT", "MIDU", "MIDZ", "HIBL", "HIBS",
    "BULZ", "BERZ", "BNKU", "BNKD", "DPST", "WDRW", "DFEN", "DUSL",
    "EURL", "DRN", "SRS", "YINN", "YANG", "INDL", "EDC", "EDZ",
    "RUSL", "RUSS", "LBJ", "BZQ", "EWV", "EFO", "EFU", "EET", "EEV",
    "UGE", "SBB", "UCC", "SCC", "UPW", "SDP",
    
    # Volatility ETFs/ETNs
    "VXX", "UVXY", "SVXY", "VIXY", "VIXM", "VXZ", "TVIX", "SVOL",
    
    # Inverse ETFs (1x)
    "SH", "PSQ", "DOG", "RWM", "MYY", "SBB", "SEF", "SPDN", "HDGE",
    
    # Gold/Silver/Commodity ETFs
    "GLD", "SLV", "IAU", "PHYS", "PSLV", "SGOL", "SIVR", "BAR",
    "USO", "UNG", "DBA", "DBC", "GSG", "PDBC", "COMT",
    
    # Bond ETFs
    "TLT", "IEF", "SHY", "BND", "AGG", "LQD", "HYG", "JNK", "EMB",
    "TIP", "GOVT", "MUB", "VCSH", "VCIT", "VCLT", "BSV", "BIV", "BLV",
    
    # Major Index ETFs
    "SPY", "QQQ", "DIA", "IWM", "IWF", "IWD", "IWB", "IWV",
    "VOO", "VTI", "VTV", "VUG", "VIG", "VYM", "VEA", "VWO",
    "EFA", "EEM", "IEFA", "IEMG", "ACWI", "VT", "VXUS",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "IYR", "VNQ", "REIT",
    
    # Thematic/Sector ETFs  
    "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ", "ARKX", "PRNT", "IZRL",
    "HACK", "CIBR", "WCLD", "SKYY", "CLOU", "BLOK", "BITO", "GBTC",
    "KWEB", "MCHI", "FXI", "ASHR", "GXC", "CQQQ",
    
    # Tradr ETFs (wie NBIZ aus deinem Screenshot)
    "NBIZ", "NBIS",

    # Weitere ETFs/ETPs die durch Pattern-Filter rutschen
    "DSPY", "SPYG", "SPYV", "SPLG", "SPYD", "SPHD", "SPHQ", "SPIB", "SPSB",
    "SCHD", "SCHX", "SCHB", "SCHG", "SCHV", "SCHF", "SCHE", "SCHZ", "SCHA",
    "JEPI", "JEPQ", "DIVO", "NUSI", "QYLD", "XYLD", "RYLD",
    "RSP", "QUAL", "MTUM", "SIZE", "VLUE", "USMV", "DGRO",
    "COWZ", "CALF", "MOAT", "SMMD", "AVUV", "AVLV",
    "IHI", "IBB", "XBI", "LABU", "GNOM", "ARKG",
    "SMH", "SOXX", "PSI", "QTEC", "IGV", "CLOU",
    "TAN", "ICLN", "PBW", "QCLN", "ACES", "CNRG",
    "MSOS", "MJ", "YOLO", "POTX", "THCX",
    "BITQ", "DAPP", "WGMI", "IBIT", "FBTC", "ETHE", "ETHV",
}

# Patterns die auf ETFs hindeuten
ETF_PATTERNS = [
    "ETF", "ETN", "ETP",  # Enthält ETF/ETN/ETP
    "2X", "3X", "-2X", "-3X",  # Leveraged
    "ULTRA", "PROSHARES", "DIREXION",  # Bekannte ETF-Anbieter
    "BULL", "BEAR",  # Leveraged Bull/Bear
    "SHORT", "INVERSE",  # Inverse
]

# SPAC-Filter Patterns (Name-basiert)
SPAC_PATTERNS = [
    "ACQUISITION CORP", "ACQUISITION CO",
    "BLANK CHECK", "SHELL COMPANY",
    "MERGER CORP", "MERGER SUB",
    "CAPITAL ACQUISITION", "HOLDINGS ACQUISITION",
]

# =============================================================================
# ECHTE AKTIEN LISTE (Common Stock = CS)
# =============================================================================
# Cache für echte Aktien-Ticker (wird einmal pro Session geladen)
COMMON_STOCK_TICKERS = set()

def _load_common_stock_tickers_direct(api_key):
    """Direkte Version OHNE st.cache_data — für Background-Threads."""
    try:
        url = "https://api.polygon.io/v3/reference/tickers"
        params = {
            "type": "CS", "market": "stocks", "active": "true",
            "limit": 1000, "apiKey": api_key
        }
        all_tickers = set()
        _spac_set = set()
        next_url = None
        for _ in range(20):
            if next_url:
                resp = rate_limited_get(next_url, timeout=30).json()
            else:
                resp = rate_limited_get(url, params=params, timeout=30).json()
            results = resp.get("results", [])
            for r in results:
                ticker = r.get("ticker", "")
                if not ticker:
                    continue
                ticker_upper = ticker.upper()
                sic = r.get("sic_code", "")
                name = r.get("name", "")
                if sic in SPAC_SIC_CODES or is_spac(name):
                    _spac_set.add(ticker_upper)
                    continue
                all_tickers.add(ticker_upper)
            next_url = resp.get("next_url")
            if next_url:
                next_url = f"{next_url}&apiKey={api_key}"
            else:
                break
        return all_tickers, _spac_set
    except Exception as e:
        print(f"[CS-Loader Direct] Fehler: {e}")
        return set(), set()

def load_common_stock_tickers(api_key):
    """Lädt alle echten Aktien (type=CS). Nutzt In-Memory-Cache, dann st.cache_data, dann Direct."""
    global COMMON_STOCK_TICKERS, SPAC_TICKERS
    if COMMON_STOCK_TICKERS:
        return COMMON_STOCK_TICKERS

    # Versuche st.cache_data (funktioniert nur im Main-Thread)
    try:
        result = load_common_stock_tickers_cached(api_key)
    except Exception:
        # Background-Thread → st.cache_data nicht verfügbar → direkt laden
        result = _load_common_stock_tickers_direct(api_key)

    if isinstance(result, tuple):
        COMMON_STOCK_TICKERS, SPAC_TICKERS = result
    else:
        COMMON_STOCK_TICKERS = result
    return COMMON_STOCK_TICKERS

# is_spac — Moved to modules/helpers.py

def is_etf_or_etp(ticker):
    """
    Prüft ob ein Ticker ein ETF, ETN, Warrant, Unit oder ähnliches Produkt ist.

    Returns: True wenn KEIN normaler Aktien-Ticker, False wenn normale Aktie
    """
    if not ticker:
        return False
    
    ticker_upper = ticker.upper().strip()
    
    # Direkte Blacklist-Prüfung
    if ticker_upper in ETF_BLACKLIST:
        return True
    
    # Pattern-basierte Prüfung (für Namen, falls verfügbar)
    for pattern in ETF_PATTERNS:
        if pattern in ticker_upper:
            return True
    
    # =========================================================================
    # WARRANTS & UNITS FILTER
    # =========================================================================
    # Warrants: .WS, .WT, .W, oder W am Ende (wie TLSIW)
    if ticker_upper.endswith(".WS") or ticker_upper.endswith(".WT"):
        return True
    if ticker_upper.endswith(".W") or ticker_upper.endswith("W"):
        # Aber nicht wenn es ein normaler Ticker ist der zufällig mit W endet
        # Prüfe ob vorletzte Zeichen ein Buchstabe ist (normale Aktie) oder Zahl (Warrant)
        if len(ticker_upper) > 1:
            # Warrants haben oft Format: TICKER + W (wie TLSIW = TLSI + W)
            # Normale Aktien mit W am Ende: BMW, LOW, etc. (aber diese sind nicht in US)
            pass  # Erstmal alle mit W am Ende durchlassen, .WS und .WT sind sicher
    
    # Units: .U oder U am Ende
    if ticker_upper.endswith(".U"):
        return True
    
    # Rights: .R oder .RT
    if ticker_upper.endswith(".R") or ticker_upper.endswith(".RT"):
        return True
    
    # Z am Ende = oft Units (wie BCTXZ)
    # ABER: Viele normale Aktien enden auf Z (AMZN hat kein Z am Ende, aber andere)
    # Sicherer: Nur wenn Ticker > 4 Zeichen und auf Z endet
    if len(ticker_upper) > 4 and ticker_upper.endswith("Z"):
        return True
    
    # Preferred Stock: .PR, -P, /P
    if ".PR" in ticker_upper or "-P" in ticker_upper:
        return True
    
    return False

# =============================================================================
# SMA / EMA BERECHNUNG
# =============================================================================
# calculate_sma — Moved to modules/indicators.py (V69.6 refactoring)


# calculate_ema — Moved to modules/indicators.py (V69.6 refactoring)


@st.cache_data(ttl=300)
def fetch_historical_closes(ticker, api_key, days=200, return_ohlcv=False):
    """
    Holt historische Schlusskurse von Polygon für SMA/EMA Berechnung.
    
    NEU V1.1: return_ohlcv=True gibt zusätzlich volle OHLCV-Bars zurück
    für Volume Profile Berechnung — KEIN Extra-API-Call.
    
    Args:
        ticker: Aktien-Ticker
        api_key: Polygon API Key
        days: Anzahl HANDELSTAGE die benötigt werden (z.B. 210 für SMA200)
        return_ohlcv: Wenn True, gibt (closes, ohlcv_bars) zurück
    
    Returns:
        Wenn return_ohlcv=False: Liste von Schlusskursen (ältester zuerst) oder None
        Wenn return_ohlcv=True: Tuple (closes, ohlcv_bars) oder (None, None)
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        # BUGFIX: Kalendertage ≠ Handelstage!
        # 252 Handelstage/Jahr ÷ 365 Kalendertage = Faktor ~1.45
        # Mit 1.5x + 20 Puffer für Feiertage sind wir sicher
        # Vorher: days + 50 → bei SMA200 nur 260 Kalendertage = ~179 Handelstage < 200!
        calendar_days = int(days * 1.5) + 20
        start_date = end_date - timedelta(days=calendar_days)
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"apiKey": api_key, "limit": calendar_days, "sort": "asc"}
        
        resp = rate_limited_get(url, params=params, timeout=10)
        data = resp.json()
        
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return (None, None) if return_ohlcv else None
        
        closes = [bar["c"] for bar in data.get("results", [])]
        
        if return_ohlcv:
            ohlcv = [{
                "open": bar.get("o", 0),
                "high": bar.get("h", 0),
                "low": bar.get("l", 0),
                "close": bar.get("c", 0),
                "volume": bar.get("v", 0),
                "time": bar.get("t", 0),
            } for bar in data.get("results", [])]
            return closes, ohlcv
        
        return closes
    
    except Exception as e:
        return (None, None) if return_ohlcv else None

# calculate_ma_distance — Moved to modules/indicators.py (V69.6 refactoring)



# =============================================================================
# BREAKOUT TIMING BEWERTUNG
# =============================================================================
def calculate_breakout_timing(row_data, fib_info=None):
    """
    Bewertet ob ein Breakout-Einstieg noch gut ist oder schon überdehnt.
    
    Faktoren:
    1. Distanz vom Breakout (Change%) - wie weit ist der Move schon?
    2. RSI - überkauft/überverkauft?
    3. Fib Extension - über 127.2% / 161.8%?
    4. RVOL - Volumen-Bestätigung?
    5. ATR - ist der Move überdehnt vs. normale Volatilität?
    
    Returns:
        dict mit:
        - score: 0-6 Punkte
        - rating: "FRÜH", "OK", oder "ZU SPÄT"
        - emoji: ✅, ⚠️, oder ❌
        - factors: Liste der Einzelbewertungen
        - risk: Risiko-Einschätzung
    """
    factors = []
    score = 0
    
    # Extrahiere Daten
    change_pct = abs(row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0)
    rvol = row_data.get("RVOL", 1) or 1
    atr_pct = row_data.get("ATR%", 0) or 0
    price = row_data.get("Preis", 0) or row_data.get("Price", 0) or 0
    
    # 1. DISTANZ VOM BREAKOUT (Change%)
    if change_pct <= 3:
        factors.append({"name": "Distanz", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Früh im Move"})
        score += 1
    elif change_pct <= 7:
        factors.append({"name": "Distanz", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Noch OK"})
        score += 0.5
    else:
        factors.append({"name": "Distanz", "value": f"+{change_pct:.1f}%", "ok": False, "detail": "Schon weit gelaufen"})
    
    # 2. MOMENTUM (basierend auf heutigem Move — KEIN echtes RSI!)
    # Echtes RSI braucht 14 Tage History, hier nur Tages-Momentum verfügbar
    momentum = change_pct  # Einfach: wie weit ist der Move heute?
    if momentum <= 5:
        factors.append({"name": "Momentum", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Moderate Bewegung"})
        score += 1
    elif momentum <= 10:
        factors.append({"name": "Momentum", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Starke Bewegung"})
        score += 0.5
    else:
        factors.append({"name": "Momentum", "value": f"+{momentum:.1f}%", "ok": False, "detail": "Überdehnt (>10%)"})
    
    # 3. FIBONACCI EXTENSION
    if fib_info and price > 0:
        fib_127 = fib_info.get("fib_1272", 0) or fib_info.get("period_high", 0) * 1.05
        fib_161 = fib_info.get("fib_1618", 0) or fib_info.get("period_high", 0) * 1.15
        
        if fib_127 > 0:
            if price < fib_127:
                factors.append({"name": "Fib Extension", "value": "Unter 127.2%", "ok": True, "detail": "Raum nach oben"})
                score += 1
            elif price < fib_161:
                factors.append({"name": "Fib Extension", "value": "Bei 127.2%", "ok": True, "detail": "Erstes Ziel erreicht"})
                score += 0.5
            else:
                factors.append({"name": "Fib Extension", "value": "Über 161.8%", "ok": False, "detail": "Stark überdehnt"})
    else:
        # Fallback ohne Fib-Daten
        if change_pct < 5:
            factors.append({"name": "Fib Extension", "value": "N/A", "ok": True, "detail": "Früh im Move"})
            score += 1
    
    # 4. RVOL (Relative Volume)
    if rvol >= 2.0:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": True, "detail": "Starke Bestätigung"})
        score += 1
    elif rvol >= 1.5:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": True, "detail": "Gute Bestätigung"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
    else:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwaches Volumen"})
    
    # 5. ATR ÜBERDEHNUNG
    if atr_pct > 0:
        atr_multiple = change_pct / atr_pct if atr_pct > 0 else 0
        if atr_multiple <= 1.0:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": True, "detail": "Normal"})
            score += 1
        elif atr_multiple <= 1.5:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": True, "detail": "Leicht erhöht"})
            score += 0.5
        elif atr_multiple <= 2.0:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": False, "detail": "Überdehnt"})
        else:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": False, "detail": "Stark überdehnt!"})
    else:
        # Kein echtes ATR → Schätze konservativ basierend auf Change%
        # Durchschnitt US-Aktie: ~2-3% ATR, aber variiert stark
        if change_pct <= 3:
            factors.append({"name": "ATR (est.)", "value": "Früh", "ok": True, "detail": "Move noch klein"})
            score += 0.75
        elif change_pct <= 6:
            factors.append({"name": "ATR (est.)", "value": "Moderat", "ok": True, "detail": "Normaler Move"})
            score += 0.5
        else:
            factors.append({"name": "ATR (est.)", "value": "Weit", "ok": False, "detail": "Schon weit gelaufen"})
    
    # 6. VOLUME TREND (basierend auf RVOL Stärke)
    if rvol >= 1.5:
        factors.append({"name": "Vol. Trend", "value": "Steigend", "ok": True, "detail": "Kaufdruck"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "Vol. Trend", "value": "Flat", "ok": True, "detail": "Stabil"})
        score += 0.5
    else:
        factors.append({"name": "Vol. Trend", "value": "Fallend", "ok": False, "detail": "Nachlassend"})
    
    # GESAMTBEWERTUNG
    max_score = 6
    score = min(score, max_score)
    
    if score >= 5:
        rating = "FRÜH"
        emoji = "✅"
        risk = "Niedrig - Guter Einstieg möglich"
        color = "green"
    elif score >= 3:
        rating = "OK"
        emoji = "⚠️"
        risk = "Mittel - Vorsichtig positionieren"
        color = "orange"
    else:
        rating = "ZU SPÄT"
        emoji = "❌"
        risk = "Hoch - Besser auf Pullback warten"
        color = "red"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "color": color
    }


# =============================================================================
# GAP TIMING BEWERTUNG
# =============================================================================
def calculate_gap_timing(row_data, is_gap_up=True):
    """
    Bewertet ob ein Gap-Trade-Einstieg noch gut ist.
    
    Faktoren:
    1. Gap Size - Optimale Größe 3-8%
    2. VWAP Position - Gap Up über VWAP = bullish
    3. PM/AH Volume - Starkes Pre-Market Volume bestätigt
    4. Gap Fill Risiko - High Vol Gaps füllen seltener (45% vs 85%)
    5. Zeit seit Open - Früher ist besser
    6. ATR Context - Gap vs. normale Volatilität
    """
    factors = []
    score = 0
    
    change_pct = abs(row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0)
    rvol = row_data.get("RVOL", 1) or 1
    atr_pct = row_data.get("ATR%", 2.5) or 2.5
    price = row_data.get("Preis", 0) or row_data.get("Price", 0) or 0
    prev_close = row_data.get("Prev Close", 0) or row_data.get("PrevClose", 0) or 0
    
    # 1. GAP SIZE - Optimal 3-8%
    if 3 <= change_pct <= 8:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Optimale Größe"})
        score += 1
    elif 1 <= change_pct < 3:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Klein aber OK"})
        score += 0.5
    elif 8 < change_pct <= 15:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Groß - Vorsicht"})
        score += 0.5
    else:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": False, "detail": "Zu klein/groß"})
    
    # 2. RVOL als Proxy für PM Volume
    if rvol >= 2.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Starke Bestätigung"})
        score += 1
        gap_fill_risk = "Low"
    elif rvol >= 1.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Gute Bestätigung"})
        score += 0.75
        gap_fill_risk = "Medium"
    elif rvol >= 1.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
        gap_fill_risk = "Medium"
    else:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwach - Fill wahrscheinlich"})
        gap_fill_risk = "High"
    
    # 3. GAP FILL RISIKO
    # High Volume Gaps: nur 45% füllen in 5+ Tagen
    # Low Volume Gaps: 85% füllen in 2 Tagen
    if gap_fill_risk == "Low":
        factors.append({"name": "Fill Risiko", "value": "~45%", "ok": True, "detail": "Trend wahrscheinlich"})
        score += 1
    elif gap_fill_risk == "Medium":
        factors.append({"name": "Fill Risiko", "value": "~60%", "ok": True, "detail": "Moderat"})
        score += 0.5
    else:
        factors.append({"name": "Fill Risiko", "value": "~85%", "ok": False, "detail": "Fill sehr wahrscheinlich"})
    
    # 4. ATR CONTEXT - Gap vs. normale Volatilität
    gap_atr_ratio = change_pct / atr_pct if atr_pct > 0 else 1
    if gap_atr_ratio >= 1.5:
        factors.append({"name": "Gap/ATR", "value": f"{gap_atr_ratio:.1f}x", "ok": True, "detail": "Signifikanter Gap"})
        score += 1
    elif gap_atr_ratio >= 1.0:
        factors.append({"name": "Gap/ATR", "value": f"{gap_atr_ratio:.1f}x", "ok": True, "detail": "Normaler Gap"})
        score += 0.5
    else:
        factors.append({"name": "Gap/ATR", "value": f"{gap_atr_ratio:.1f}x", "ok": False, "detail": "Kleiner Gap"})
    
    # 5. MOMENTUM BESTÄTIGUNG (basierend auf Change-Richtung vs Gap)
    # Wenn Gap Up und Change positiv = Momentum hält
    if is_gap_up:
        if change_pct > 0:
            factors.append({"name": "Momentum", "value": "Hält", "ok": True, "detail": "Gap hält über Open"})
            score += 1
        else:
            factors.append({"name": "Momentum", "value": "Schwächt", "ok": False, "detail": "Gap füllt sich"})
    else:
        if change_pct < 0:
            factors.append({"name": "Momentum", "value": "Hält", "ok": True, "detail": "Gap hält unter Open"})
            score += 1
        else:
            factors.append({"name": "Momentum", "value": "Schwächt", "ok": False, "detail": "Gap füllt sich"})
    
    # 6. OPENING RANGE CONTEXT
    # Schätze basierend auf Change und RVOL
    if rvol >= 1.5 and change_pct >= 2:
        factors.append({"name": "OR Break", "value": "Wahrscheinlich", "ok": True, "detail": "Starker Start"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "OR Break", "value": "Möglich", "ok": True, "detail": "Abwarten"})
        score += 0.5
    else:
        factors.append({"name": "OR Break", "value": "Unsicher", "ok": False, "detail": "Schwacher Start"})
    
    # GESAMTBEWERTUNG
    max_score = 6
    score = min(score, max_score)
    
    if score >= 4.5:
        rating = "GO"
        emoji = "✅"
        risk = "Gap & Go Setup - Trend folgen"
        recommendation = "Gap hält wahrscheinlich - Trend folgen"
    elif score >= 3:
        rating = "WARTEN"
        emoji = "⚠️"
        risk = "Abwarten - Opening Range beobachten"
        recommendation = "15-30min warten, dann entscheiden"
    else:
        rating = "FADE"
        emoji = "❌"
        risk = "Gap Fill wahrscheinlich - Vorsicht"
        recommendation = "Gap könnte füllen - Gegen-Trade oder Skip"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4.5 else "orange" if score >= 3 else "red"
    }


# =============================================================================
# MA BOUNCE TIMING BEWERTUNG
# =============================================================================
def calculate_ma_bounce_timing(row_data, ma_type="EMA 21"):
    """
    Bewertet ob ein MA Bounce Einstieg gut getimed ist.
    
    Faktoren:
    1. Distanz zum MA - Näher = besser (0-2% ideal)
    2. MA Trend-Richtung - MA muss in Trade-Richtung zeigen
    3. Bounce Bestätigung - Reaktion am MA sichtbar?
    4. RSI Zone - Neutral (40-60) ist ideal für Bounce
    5. Zeit seit letztem MA-Test - Länger weg = stärkerer Bounce
    """
    factors = []
    score = 0
    
    ma_distance = abs(row_data.get("MA_Distance%", 0) or row_data.get("MA Distance", 0) or 0)
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    rvol = row_data.get("RVOL", 1) or 1
    
    # 1. DISTANZ ZUM MA - Näher = besser
    # WICHTIG: Wenn kein MA-Daten vorhanden (ma_distance=0 weil nicht befüllt),
    # vergeben wir neutralen Score statt Maximum
    has_ma_data = (row_data.get("MA_Distance%") is not None and row_data.get("MA_Distance%") != 0) or \
                  (row_data.get("MA Distance") is not None and row_data.get("MA Distance") != 0)
    
    if not has_ma_data:
        factors.append({"name": "MA Distanz", "value": "N/A", "ok": True, "detail": "Keine MA-Daten"})
        score += 0.5  # Neutral statt 1.5 Maximum
    elif ma_distance <= 0.5:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Perfekt am MA"})
        score += 1.5
    elif ma_distance <= 1.0:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Sehr nah"})
        score += 1.25
    elif ma_distance <= 2.0:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Akzeptabel"})
        score += 1
    elif ma_distance <= 3.0:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Noch OK"})
        score += 0.5
    else:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": False, "detail": "Zu weit vom MA"})
    
    # 2. BOUNCE BESTÄTIGUNG (Change-Richtung nach Touch)
    # Bei Long-Setup: Change sollte positiv sein (Bounce nach oben)
    if change_pct > 0:
        if change_pct >= 1:
            factors.append({"name": "Bounce", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Starke Reaktion"})
            score += 1
        else:
            factors.append({"name": "Bounce", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Leichte Reaktion"})
            score += 0.75
    elif change_pct > -1:
        factors.append({"name": "Bounce", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Neutral"})
        score += 0.5
    else:
        factors.append({"name": "Bounce", "value": f"{change_pct:.1f}%", "ok": False, "detail": "Kein Bounce - Durchbruch?"})
    
    # 3. MOMENTUM ZONE (basierend auf heutigem Move — KEIN RSI)
    # Ideal für Bounce: Moderate Bewegung (±3%)
    momentum = abs(change_pct)
    if momentum <= 3:
        factors.append({"name": "Momentum", "value": f"{change_pct:+.1f}%", "ok": True, "detail": "Moderat — Ideal für Bounce"})
        score += 1
    elif momentum <= 5:
        factors.append({"name": "Momentum", "value": f"{change_pct:+.1f}%", "ok": True, "detail": "Leicht extended"})
        score += 0.5
    else:
        factors.append({"name": "Momentum", "value": f"{change_pct:+.1f}%", "ok": False, "detail": "Zu stark bewegt"})
    
    # 4. VOLUME BESTÄTIGUNG
    if rvol >= 1.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Starkes Interesse"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
    else:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwaches Interesse"})
    
    # 5. MA TYP KONTEXT
    if "200" in ma_type:
        factors.append({"name": "MA Typ", "value": "SMA 200", "ok": True, "detail": "Stärkster Support"})
        score += 0.5
    elif "50" in ma_type:
        factors.append({"name": "MA Typ", "value": "SMA 50", "ok": True, "detail": "Starker Support"})
        score += 0.5
    else:
        factors.append({"name": "MA Typ", "value": "EMA 21", "ok": True, "detail": "Swing-Trading MA"})
        score += 0.5
    
    # GESAMTBEWERTUNG
    max_score = 5
    score = min(score, max_score)
    
    if score >= 4:
        rating = "PERFEKT"
        emoji = "✅"
        risk = "Idealer Bounce-Einstieg"
        recommendation = "Entry am MA mit Stop darunter"
    elif score >= 2.5:
        rating = "GUT"
        emoji = "⚠️"
        risk = "Akzeptabler Einstieg"
        recommendation = "Entry möglich, engerer Stop"
    else:
        rating = "WARTEN"
        emoji = "❌"
        risk = "Kein klarer Bounce"
        recommendation = "Auf besseren Entry warten"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4 else "orange" if score >= 2.5 else "red"
    }


# =============================================================================
# MEAN REVERSION / REVERSAL TIMING BEWERTUNG
# =============================================================================
def calculate_reversal_timing(row_data, is_long=True):
    """
    Bewertet ob ein Mean Reversion / Reversal Einstieg gut getimed ist.
    
    Faktoren:
    1. RSI Extrem - <30 für Long, >70 für Short
    2. Bollinger Band - Außerhalb = überdehnt
    3. Distanz von MA - >2 Std.Dev = überdehnt
    4. Umkehr-Signal - Change-Richtung dreht?
    5. Volume - Capitulation Volume = gut
    6. S/R Level - Bei Support/Resistance?
    """
    factors = []
    score = 0
    
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    rvol = row_data.get("RVOL", 1) or 1
    atr_pct = row_data.get("ATR%", 2.5) or 2.5
    
    # 1. SELLOFF-TIEFE (statt fake RSI)
    # Für Mean Reversion: je tiefer der Drop, desto besser für Long-Reversal
    momentum = change_pct  # Negativer Wert bei Selloff
    
    if is_long:
        if momentum <= -8:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": True, "detail": "Starker Selloff — Reversal-Zone"})
            score += 1.5
        elif momentum <= -5:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": True, "detail": "Deutlicher Selloff"})
            score += 1
        elif momentum <= -3:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": True, "detail": "Moderater Rückgang"})
            score += 0.5
        else:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": False, "detail": "Kein echter Selloff"})
    else:  # Short Reversal
        if momentum >= 8:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Starke Rally — Reversal-Zone"})
            score += 1.5
        elif momentum >= 5:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Deutliche Rally"})
            score += 1
        elif momentum >= 3:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Moderate Rally"})
            score += 0.5
        else:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": False, "detail": "Keine echte Rally"})
    
    # 2. ÜBERDEHNUNG (Change vs ATR)
    extension = abs(change_pct) / atr_pct if atr_pct > 0 else 0
    if extension >= 2.0:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": True, "detail": "Stark überdehnt"})
        score += 1
    elif extension >= 1.5:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": True, "detail": "Überdehnt"})
        score += 0.75
    elif extension >= 1.0:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": True, "detail": "Moderat"})
        score += 0.5
    else:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": False, "detail": "Nicht überdehnt"})
    
    # 3. VOLUME (Capitulation = gut für Reversal)
    if rvol >= 2.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Capitulation möglich"})
        score += 1
    elif rvol >= 1.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Erhöhtes Volumen"})
        score += 0.75
    elif rvol >= 1.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
    else:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwach"})
    
    # 4. UMKEHR-SIGNAL (Change zeigt erste Erholung?)
    if is_long:
        if change_pct > 0:
            factors.append({"name": "Umkehr", "value": "Ja", "ok": True, "detail": "Erste Erholung sichtbar"})
            score += 1
        elif change_pct > -2:
            factors.append({"name": "Umkehr", "value": "Möglich", "ok": True, "detail": "Stabilisiert sich"})
            score += 0.5
        else:
            factors.append({"name": "Umkehr", "value": "Nein", "ok": False, "detail": "Fällt noch"})
    else:
        if change_pct < 0:
            factors.append({"name": "Umkehr", "value": "Ja", "ok": True, "detail": "Erste Schwäche sichtbar"})
            score += 1
        elif change_pct < 2:
            factors.append({"name": "Umkehr", "value": "Möglich", "ok": True, "detail": "Momentum nachlassend"})
            score += 0.5
        else:
            factors.append({"name": "Umkehr", "value": "Nein", "ok": False, "detail": "Steigt noch"})
    
    # 5. RISK/REWARD basierend auf Extension
    if extension >= 1.5:
        factors.append({"name": "R:R", "value": "Gut", "ok": True, "detail": f"Mean ~{extension:.0f}x ATR entfernt"})
        score += 1
    elif extension >= 1.0:
        factors.append({"name": "R:R", "value": "OK", "ok": True, "detail": "Akzeptables R:R"})
        score += 0.5
    else:
        factors.append({"name": "R:R", "value": "Schlecht", "ok": False, "detail": "Wenig Raum zum Mean"})
    
    # GESAMTBEWERTUNG
    max_score = 6
    score = min(score, max_score)
    
    if score >= 4.5:
        rating = "EXTREM"
        emoji = "✅"
        risk = "Stark überdehnt - Reversal wahrscheinlich"
        recommendation = "Entry mit Stop unter Extrem"
    elif score >= 3:
        rating = "MÖGLICH"
        emoji = "⚠️"
        risk = "Überdehnt - Reversal möglich"
        recommendation = "Auf Bestätigung warten"
    else:
        rating = "ZU FRÜH"
        emoji = "❌"
        risk = "Nicht genug überdehnt"
        recommendation = "Warten auf stärkere Überdehnung"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4.5 else "orange" if score >= 3 else "red"
    }


# =============================================================================
# VOLUME VOID TIMING BEWERTUNG
# =============================================================================
def calculate_void_timing(row_data):
    """
    Bewertet ob ein Volume Void Trade-Einstieg gut getimed ist.
    
    Faktoren:
    1. Void Size - Größer = mehr Potenzial
    2. Distanz zum Void - Näher = besser
    3. Trend-Richtung - Void in Trend-Richtung = stärker
    4. Void Alter - Neuere Voids sind relevanter
    5. Multiple Voids - Gestaffelte Voids = stärker
    """
    factors = []
    score = 0
    
    void_size = row_data.get("VoidSize%", 0) or row_data.get("Void Size", 0) or 0
    void_dist = abs(row_data.get("VoidDist%", 0) or row_data.get("Void Distance", 0) or 0)
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    voids_above = row_data.get("VoidsAbove", 0) or 0
    voids_below = row_data.get("VoidsBelow", 0) or 0
    
    # 1. VOID SIZE
    if void_size >= 5:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": True, "detail": "Großes Void"})
        score += 1
    elif void_size >= 3:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": True, "detail": "Gutes Void"})
        score += 0.75
    elif void_size >= 1:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": True, "detail": "Kleines Void"})
        score += 0.5
    else:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": False, "detail": "Sehr klein"})
    
    # 2. DISTANZ ZUM VOID
    if void_dist <= 1:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": True, "detail": "Sehr nah"})
        score += 1
    elif void_dist <= 2:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": True, "detail": "Nah"})
        score += 0.75
    elif void_dist <= 5:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": True, "detail": "Moderat"})
        score += 0.5
    else:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": False, "detail": "Weit entfernt"})
    
    # 3. TREND-RICHTUNG vs VOID
    # Wenn Preis steigt und Void oben liegt = gut
    if change_pct > 0 and voids_above > 0:
        factors.append({"name": "Trend → Void", "value": "Aligned", "ok": True, "detail": "Void in Bewegungsrichtung"})
        score += 1
    elif change_pct < 0 and voids_below > 0:
        factors.append({"name": "Trend → Void", "value": "Aligned", "ok": True, "detail": "Void in Bewegungsrichtung"})
        score += 1
    elif voids_above > 0 or voids_below > 0:
        factors.append({"name": "Trend → Void", "value": "Neutral", "ok": True, "detail": "Void vorhanden"})
        score += 0.5
    else:
        factors.append({"name": "Trend → Void", "value": "Kein Void", "ok": False, "detail": "Kein nahes Void"})
    
    # 4. MULTIPLE VOIDS
    total_voids = voids_above + voids_below
    if total_voids >= 3:
        factors.append({"name": "Voids", "value": f"{total_voids} Voids", "ok": True, "detail": "Mehrere Voids = mehr Targets"})
        score += 1
    elif total_voids >= 1:
        factors.append({"name": "Voids", "value": f"{total_voids} Void(s)", "ok": True, "detail": "Void vorhanden"})
        score += 0.5
    else:
        factors.append({"name": "Voids", "value": "0 Voids", "ok": False, "detail": "Keine Voids sichtbar"})
    
    # 5. SETUP QUALITÄT
    if void_size >= 3 and void_dist <= 2:
        factors.append({"name": "Setup", "value": "A+", "ok": True, "detail": "Großes Void, sehr nah"})
        score += 1
    elif void_size >= 2 and void_dist <= 3:
        factors.append({"name": "Setup", "value": "B", "ok": True, "detail": "Gutes Setup"})
        score += 0.5
    else:
        factors.append({"name": "Setup", "value": "C", "ok": False, "detail": "Schwaches Setup"})
    
    # GESAMTBEWERTUNG
    max_score = 5
    score = min(score, max_score)
    
    if score >= 4:
        rating = "STARK"
        emoji = "✅"
        risk = "Klares Void-Setup"
        recommendation = "Entry Richtung Void mit Target am Void-Ende"
    elif score >= 2.5:
        rating = "OK"
        emoji = "⚠️"
        risk = "Akzeptables Setup"
        recommendation = "Entry möglich, konservatives Target"
    else:
        rating = "SCHWACH"
        emoji = "❌"
        risk = "Kein klares Void-Setup"
        recommendation = "Besseres Setup abwarten"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4 else "orange" if score >= 2.5 else "red"
    }


# =============================================================================
# INSIDER TIMING BEWERTUNG
# =============================================================================
def calculate_insider_timing(row_data):
    """
    Bewertet ob ein Insider-Buying Signal stark ist.
    
    Faktoren:
    1. Cluster Buying - Mehrere Insider = stärker
    2. Transaktionsgröße - Größer = mehr Conviction
    3. Insider-Rolle - CEO/CFO > Director > 10% Owner
    4. Timing - Nach Pullback = besser
    5. Open Market Purchase - Code "P" = echtes Geld
    """
    factors = []
    score = 0
    
    # Extrahiere Insider-Daten (falls verfügbar)
    num_insiders = row_data.get("Insiders", 1) or row_data.get("InsiderCount", 1) or 1
    transaction_value = row_data.get("InsiderValue", 0) or row_data.get("TransValue", 0) or 0
    insider_role = row_data.get("InsiderRole", "Unknown") or "Unknown"
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    change_1m = row_data.get("1M%", 0) or row_data.get("Change1M", 0) or 0
    
    # 1. CLUSTER BUYING
    if num_insiders >= 3:
        factors.append({"name": "Cluster", "value": f"{num_insiders} Insider", "ok": True, "detail": "Starkes Cluster-Signal"})
        score += 1.5
    elif num_insiders >= 2:
        factors.append({"name": "Cluster", "value": f"{num_insiders} Insider", "ok": True, "detail": "Cluster-Signal"})
        score += 1
    else:
        factors.append({"name": "Cluster", "value": "1 Insider", "ok": True, "detail": "Einzelner Kauf"})
        score += 0.5
    
    # 2. TRANSAKTIONSGRÖSSE
    if transaction_value >= 500000:
        factors.append({"name": "Größe", "value": f"${transaction_value/1000:.0f}K", "ok": True, "detail": "Sehr große Position"})
        score += 1
    elif transaction_value >= 100000:
        factors.append({"name": "Größe", "value": f"${transaction_value/1000:.0f}K", "ok": True, "detail": "Große Position"})
        score += 0.75
    elif transaction_value > 0:
        factors.append({"name": "Größe", "value": f"${transaction_value/1000:.0f}K", "ok": True, "detail": "Moderate Position"})
        score += 0.5
    else:
        factors.append({"name": "Größe", "value": "N/A", "ok": True, "detail": "Keine Daten"})
        score += 0.5
    
    # 3. INSIDER-ROLLE
    role_upper = insider_role.upper() if isinstance(insider_role, str) else ""
    if "CEO" in role_upper or "CFO" in role_upper or "CHIEF" in role_upper:
        factors.append({"name": "Rolle", "value": insider_role[:10], "ok": True, "detail": "C-Suite = höchste Conviction"})
        score += 1
    elif "DIRECTOR" in role_upper or "DIR" in role_upper:
        factors.append({"name": "Rolle", "value": "Director", "ok": True, "detail": "Board-Member"})
        score += 0.75
    elif "10%" in role_upper or "OWNER" in role_upper:
        factors.append({"name": "Rolle", "value": "10% Owner", "ok": True, "detail": "Großaktionär"})
        score += 0.5
    else:
        factors.append({"name": "Rolle", "value": "Insider", "ok": True, "detail": "Unbekannte Rolle"})
        score += 0.5
    
    # 4. TIMING - Kauf nach Pullback ist besser
    if change_1m < -10:
        factors.append({"name": "Timing", "value": f"{change_1m:.0f}% (1M)", "ok": True, "detail": "Kauf nach starkem Pullback"})
        score += 1
    elif change_1m < -5:
        factors.append({"name": "Timing", "value": f"{change_1m:.0f}% (1M)", "ok": True, "detail": "Kauf nach Pullback"})
        score += 0.75
    elif change_1m < 0:
        factors.append({"name": "Timing", "value": f"{change_1m:.0f}% (1M)", "ok": True, "detail": "Kauf bei Schwäche"})
        score += 0.5
    else:
        factors.append({"name": "Timing", "value": f"+{change_1m:.0f}% (1M)", "ok": False, "detail": "Kauf nach Run-up"})
    
    # 5. PREIS-AKTION BESTÄTIGUNG
    if change_pct > 0:
        factors.append({"name": "Preis", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Positive Reaktion"})
        score += 0.5
    elif change_pct > -2:
        factors.append({"name": "Preis", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Stabil"})
        score += 0.25
    else:
        factors.append({"name": "Preis", "value": f"{change_pct:.1f}%", "ok": False, "detail": "Weiter fallend"})
    
    # GESAMTBEWERTUNG
    max_score = 5
    score = min(score, max_score)
    
    if score >= 4:
        rating = "STARK"
        emoji = "✅"
        risk = "Starkes Insider-Signal"
        recommendation = "Entry mit Stop unter Recent Low"
    elif score >= 2.5:
        rating = "MODERAT"
        emoji = "⚠️"
        risk = "Moderates Signal"
        recommendation = "Auf weitere Bestätigung achten"
    else:
        rating = "SCHWACH"
        emoji = "❌"
        risk = "Schwaches Signal"
        recommendation = "Nicht allein auf Insider verlassen"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4 else "orange" if score >= 2.5 else "red"
    }


# =============================================================================
# MASTER TIMING BEWERTUNG - Wählt richtige Funktion basierend auf Strategie
# =============================================================================
# get_timing_assessment — Moved to modules/analysis.py


# calculate_volume_profile — Moved to modules/volume_analysis.py

# find_volume_voids — Moved to modules/volume_analysis.py

@st.cache_data(ttl=300)  # 5 Minuten Cache
def scan_volume_voids_batch(tickers, poly_key, direction="long"):
    """
    Scannt eine Liste von Tickern nach Volume Voids.
    
    Args:
        tickers: Liste von Ticker-Symbolen
        poly_key: Polygon API Key
        direction: "long" (Voids über Preis) oder "short" (Voids unter Preis)
    
    Returns:
        Liste von Tickern mit Volume Void Daten
    """
    results = []
    
    # Kein künstliches Limit — Polygon Starter: 100 Calls/Min
    
    for ticker in tickers:
        try:
            # Hole 60 Tage historische Daten (mehr = besseres Volume Profile)
            from datetime import timedelta
            end_date = datetime.now()
            start_date = end_date - timedelta(days=90)
            
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            params = {"apiKey": poly_key, "adjusted": "true", "sort": "asc"}
            
            resp = rate_limited_get(url, params=params, timeout=10)
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            bars = data.get("results", [])
            
            if len(bars) < 10:
                continue
            
            # Konvertiere zu OHLCV Format
            ohlcv = []
            for bar in bars:
                ohlcv.append({
                    'open': bar.get('o', 0),
                    'high': bar.get('h', 0),
                    'low': bar.get('l', 0),
                    'close': bar.get('c', 0),
                    'volume': bar.get('v', 0)
                })
            
            # Berechne Volume Profile (20 Bins für gute Auflösung)
            vp = calculate_volume_profile(ohlcv, num_bins=20)
            if not vp:
                continue
            
            # Aktueller Preis
            current_price = ohlcv[-1]['close']
            
            # Finde Voids
            voids = find_volume_voids(current_price, vp)
            if not voids:
                continue
            
            # Filter nach Direction
            if direction == "long":
                if not voids.get('voids_above', []) or voids.get('void_score', 0) < 15:
                    continue
                
                nearest = voids.get('nearest_void_above', {})
                distance_to_void = (nearest.get('low', 0) - current_price) / current_price * 100
                
            else:  # short
                if not voids.get('voids_below', []) or voids.get('void_score', 0) < 10:
                    continue
                
                nearest = voids.get('nearest_void_below', {})
                distance_to_void = (current_price - nearest.get('high', 0)) / current_price * 100
            
            results.append({
                'ticker': ticker,
                'price': current_price,
                'void_score': voids.get('void_score', 0),
                'nearest_void': nearest,
                'distance_to_void_pct': round(distance_to_void, 2),
                'void_size_pct': round(nearest.get('size_pct', 0), 2),
                'poc': round(voids.get('poc', 0), 2),
                'vah': round(voids.get('vah', 0), 2),
                'val': round(voids.get('val', 0), 2),
                'num_voids_above': len(voids.get('voids_above', [])),
                'num_voids_below': len(voids.get('voids_below', [])),
                'voids_above': voids.get('voids_above', []),
                'voids_below': voids.get('voids_below', [])
            })
            
        except Exception as e:
            continue
    
    # Sortiere nach Void Score
    results.sort(key=lambda x: x['void_score'], reverse=True)
    
    return results

# =============================================================================
# BI BACKGROUND SCAN — Überlebt Streamlit-Reruns via Threading + Dateien
# =============================================================================
import threading
import os

_BI_CACHE_FILE = "/tmp/bi_cache_{direction}.json"
_BI_PROGRESS_FILE = "/tmp/bi_scan_progress_{direction}.json"
_BI_CONFIG_FILE = "/tmp/alpha_bi_config.json"
_BI_CACHE_MAX_AGE = 7200  # 2 Stunden — Auto-Scan bei 15:45 und 18:30 CET
_bi_scan_lock = threading.Lock()

_BI_DEFAULT_CONFIG = {
    "direction": "long",
    "threshold": 85,
    "auto_enabled": True,
    "scan1_h": 15,
    "scan1_m": 45,
    "scan2_h": 18,
    "scan2_m": 30,
    "cache_ttl_h": 2,
}

# _bi_config_load — Moved to modules/scanners.py


# _bi_config_save — Moved to modules/scanners.py



# _bi_cache_path — Moved to modules/scanners.py


# _bi_progress_path — Moved to modules/scanners.py


# _bi_cache_load — Moved to modules/scanners.py



# _bi_cache_save — Moved to modules/scanners.py



# _bi_progress_read — Moved to modules/scanners.py



# _bi_progress_write — Moved to modules/scanners.py



# _bi_progress_clear — Moved to modules/scanners.py


# _bi_cache_age_str — Moved to modules/scanners.py



# _bi_scan_is_running — Moved to modules/scanners.py



# ── BI Scanner Stop-Mechanismus ──
# _bi_stop_file — Moved to modules/scanners.py

# _bi_request_stop — Moved to modules/scanners.py

# _bi_should_stop — Moved to modules/scanners.py

# _bi_clear_stop — Moved to modules/scanners.py


# _detect_chart_patterns — Moved to modules/analysis.py



# _bi_background_scan — Moved to modules/scanners.py



# =====================================================
# 🧬 BIOTECH SCANNER — FDA Catalysts & Pipeline Tracker
# =====================================================

# Biotech SIC Codes (Pharmaceutical & Biotech Manufacturing)
BIOTECH_SIC_CODES = {
    "2833", "2834", "2835", "2836",  # Pharma / Biotech Manufacturing
    "2831",  # Biological Products
    "3841", "3842",  # Medical Instruments & Devices
    "8731", "8734",  # R&D / Testing Labs
}

# Keywords zum Erkennen von Biotech-Aktien (wenn SIC fehlt)
BIOTECH_NAME_KEYWORDS = [
    "pharma", "therapeutics", "biosciences", "biotech", "biopharma",
    "oncology", "genomics", "immuno", "medical", "diagnostics",
    "gene therapy", "cell therapy", "biologics", "vaccine", "antibody",
    "rna", "mrna", "crispr", "peptide", "neuro", "cardio",
]

# FDA / Catalyst Keywords für News-Scanning
FDA_CATALYST_KEYWORDS = {
    # Höchste Priorität — direkte FDA Events
    "tier1": {
        "keywords": ["fda approval", "fda approved", "pdufa", "nda accepted", "bla accepted",
                     "fda clearance", "breakthrough therapy", "fast track", "priority review",
                     "accelerated approval", "orphan drug", "emergency use", "eua granted",
                     "complete response letter", "adcom", "advisory committee",
                     "fda decision", "fda action date"],
        "score": 30,
        "label": "🎯 FDA Event"
    },
    # Hohe Priorität — Clinical Trial Milestones
    "tier2": {
        "keywords": ["phase 3 results", "phase 3 data", "phase iii", "pivotal trial",
                     "primary endpoint met", "primary endpoint", "topline results", "topline data",
                     "positive results", "statistically significant", "overall survival",
                     "progression-free survival", "complete remission", "phase 2 results",
                     "phase ii data", "late-breaking", "interim analysis", "interim data"],
        "score": 22,
        "label": "📊 Trial Results"
    },
    # Mittlere Priorität — Pipeline & Partnership
    "tier3": {
        "keywords": ["licensing agreement", "partnership", "collaboration", "acquisition target",
                     "buyout", "merger", "ind filed", "ind accepted", "clinical trial initiation",
                     "patient enrollment", "first patient dosed", "dosing initiated",
                     "expanded access", "compassionate use", "label expansion"],
        "score": 15,
        "label": "🤝 Deal/Pipeline"
    },
    # Niedrige Priorität — Allgemeine Pipeline Signals
    "tier4": {
        "keywords": ["preclinical", "phase 1", "phase i", "proof of concept",
                     "patent granted", "patent filed", "ip protection", "data presentation",
                     "conference presentation", "manuscript published", "peer review"],
        "score": 8,
        "label": "🔬 Early Pipeline"
    },
}

# Negative Biotech Katalysatoren — Score-Abzug
BIOTECH_NEGATIVE_CATALYSTS = {
    "clinical hold": -25,
    "fda rejection": -30,
    "complete response": -20,
    "trial failure": -25,
    "missed endpoint": -25,
    "adverse events": -15,
    "safety concern": -15,
    "stock offering": -10,
    "dilution": -10,
    "shelf registration": -8,
    "going concern": -20,
    "delisting": -25,
    "sec investigation": -15,
}


_BIOTECH_CONFIG_FILE = "/tmp/alpha_biotech_config.json"

_BIOTECH_DEFAULT_CONFIG = {
    "auto_scan": True,
    "quick_interval_h": 2,
    "full_interval_h": 6,
    "min_score": 20,
}

# _biotech_config_load — Moved to modules/scanners.py


# _biotech_config_save — Moved to modules/scanners.py



# _biotech_progress_file — Moved to modules/scanners.py

# _biotech_stop_file — Moved to modules/scanners.py

# _biotech_request_stop — Moved to modules/scanners.py

# _biotech_should_stop — Moved to modules/scanners.py

# _biotech_clear_stop — Moved to modules/scanners.py

# _biotech_progress_write — Moved to modules/scanners.py


# _biotech_progress_read — Moved to modules/scanners.py


# _biotech_cache_file — Moved to modules/scanners.py

# _biotech_cache_save — Moved to modules/scanners.py


# _biotech_cache_load — Moved to modules/scanners.py



def _is_biotech_stock(ticker_details):
    """Prüft ob ein Ticker ein Biotech/Pharma-Unternehmen ist."""
    sic = str(ticker_details.get("sic_code", ""))
    if sic in BIOTECH_SIC_CODES:
        return True
    name = (ticker_details.get("name", "") or "").lower()
    desc = (ticker_details.get("description", "") or "").lower()
    combined = name + " " + desc
    return any(kw in combined for kw in BIOTECH_NAME_KEYWORDS)


# _fetch_biotech_universe — Moved to modules/scanners.py



# _scan_biotech_news — Moved to modules/scanners.py



# =============================================================================
# BPIQ CATALYST API — Kuratierte Biotech-Katalysator-Daten
# =============================================================================
# BPIQ liefert manuell kuratierte Catalyst-Dates (PDUFA, Phase 3 Readouts etc.)
# die DEUTLICH zuverlässiger sind als ClinicalTrials.gov Primary Completion Dates.
# ClinicalTrials.gov bleibt als Fallback für Pipeline-Daten (Studienanzahl, Phasen).
#
# API: https://api.bpiq.com/api/v1/drugs/
# Auth: Token-Header
# Rate: ~1.3s pro Call, Batch-Fetch aller Catalyst-Drugs in 4 Calls möglich
# =============================================================================

_BPIQ_CATALYST_CACHE = {}
_BPIQ_CACHE_TIMESTAMP = 0

def _load_bpiq_catalyst_cache():
    """
    DEPRECATED (Audit 10.06.2026) — tote Dublette, der Live-Pfad ist
    modules/data_fetchers.py::_load_bpiq_catalyst_cache (von Scannern und
    Tests genutzt). Diese Kopie hat repo-weit 0 Aufrufer; sie bleibt nur
    vorsorglich fuer die Streamlit-UI erhalten und wird NICHT weiter gepflegt.
    Aenderungen an der BPIQ-Logik gehoeren ausschliesslich nach
    modules/data_fetchers.py.

    Lädt ALLE Drugs mit Catalyst-Dates von BPIQ in einen In-Memory-Cache.
    648 Drugs, 332 Ticker — 4 API-Calls (limit=200 pro Call).
    Cache-TTL: 4 Stunden (Daten werden täglich aktualisiert).
    """
    global _BPIQ_CATALYST_CACHE, _BPIQ_CACHE_TIMESTAMP
    import time as _time

    # Cache noch gültig? (4h = 14400s)
    if _BPIQ_CATALYST_CACHE and (_time.time() - _BPIQ_CACHE_TIMESTAMP) < 14400:
        return _BPIQ_CATALYST_CACHE

    try:
        bpiq_key = st.secrets.get("BPIQ_API_KEY", "")
        if not bpiq_key:
            return {}

        all_drugs = []
        for offset in range(0, 800, 200):
            resp = rate_limited_get(
                f"https://api.bpiq.com/api/v1/drugs/?has_catalyst=true&limit=200&offset={offset}",
                headers={"Authorization": f"Token {bpiq_key}"},
                timeout=15
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            all_drugs.extend(results)

        # Gruppiere nach Ticker
        cache = {}
        _now = datetime.now()
        for drug in all_drugs:
            ticker = drug.get("ticker", "").upper()
            if not ticker:
                continue

            cat_date = drug.get("catalyst_date")
            cat_text = drug.get("catalyst_date_text", "TBA")
            stage = drug.get("stage_event", {})
            stage_label = stage.get("stage_label", "")
            event_label = stage.get("event_label", "")
            full_label = stage.get("label", "")
            bpiq_score = stage.get("score", 0)

            # Tage bis Catalyst berechnen
            days_until = None
            if cat_date:
                try:
                    _cd = datetime.strptime(cat_date[:10], "%Y-%m-%d")
                    days_until = (_cd - _now).days
                except Exception:
                    pass

            # Kategorie bestimmen
            category = ""
            if days_until is not None:
                if days_until < 0:
                    if abs(days_until) <= 90:
                        category = "OVERDUE"
                    # >90d overdue bei BPIQ = wahrscheinlich veralteter Eintrag
                elif days_until <= 30:
                    category = "IMMINENT"
                elif days_until <= 90:
                    category = "UPCOMING"
                elif days_until <= 365:
                    category = "LATER"

            # Phase-Multiplikator
            phase_mult = 1.0
            if "Phase 3" in stage_label or "PDUFA" in stage_label:
                phase_mult = 3.0
            elif "Phase 2" in stage_label:
                phase_mult = 2.0
            elif "Phase 1" in stage_label:
                phase_mult = 1.0
            else:
                phase_mult = 0.5

            entry = {
                "drug_name": drug.get("drug_name", "")[:60],
                "stage_label": stage_label,
                "event_label": event_label,
                "full_label": full_label,
                "catalyst_date": cat_date,
                "catalyst_date_text": cat_text,
                "days_until": days_until,
                "category": category,
                "phase_mult": phase_mult,
                "bpiq_score": bpiq_score,
                "indications": drug.get("indications_text", ""),
                "note": (drug.get("note", "") or "")[:200],
                "source": drug.get("catalyst_source", ""),
            }

            if ticker not in cache:
                cache[ticker] = []
            cache[ticker].append(entry)

        # Sortiere pro Ticker: PDUFA zuerst, dann IMMINENT, dann nach Datum
        cat_order = {"OVERDUE": 0, "IMMINENT": 1, "UPCOMING": 2, "LATER": 3, "": 9}
        for ticker in cache:
            cache[ticker].sort(key=lambda x: (
                cat_order.get(x["category"], 9),
                x["days_until"] if x["days_until"] is not None else 9999
            ))

        _BPIQ_CATALYST_CACHE = cache
        _BPIQ_CACHE_TIMESTAMP = _time.time()
        return cache

    except Exception:
        return {}


# _get_bpiq_catalysts — Moved to modules/data_fetchers.py


# _check_clinical_trials — Moved to modules/scanners.py



# _biotech_technical_score — Moved to modules/scanners.py



# _biotech_risk_score — Moved to modules/scanners.py



# _calculate_biotech_catalyst_score — Moved to modules/data_fetchers.py


# _biotech_news_momentum — Moved to modules/scanners.py



# _biotech_background_scan — Moved to modules/scanners.py



# _biotech_universe_cache_file — Moved to modules/scanners.py

# _biotech_universe_cache_save — Moved to modules/scanners.py


# _biotech_universe_cache_load — Moved to modules/scanners.py



# _biotech_quick_scan — Moved to modules/scanners.py



def _watchlist_file():
    """Pfad zur Watchlist-Datei."""
    return "/tmp/alpha_station_watchlist.json"

def _save_watchlist():
    """Speichert Watchlist als JSON (überlebt App-Reruns)."""
    try:
        import json
        # Entferne nicht-serialisierbare Daten
        clean = []
        for w in st.session_state.watchlist:
            clean.append({
                "ticker": w["ticker"],
                "market": w.get("market", ""),
                "price": w.get("price", 0),
                "added": w.get("added", ""),
            })
        with open(_watchlist_file(), "w") as f:
            json.dump(clean, f)
    except Exception as e:
        pass

# _load_watchlist — Moved to modules/helpers.py

def add_to_watchlist(ticker, data):
    """Fügt Ticker zur Watchlist hinzu (mit Persistenz)."""
    entry = {
        "ticker": ticker,
        "market": st.session_state.market_type,
        "price": data.get("Preis", 0),
        "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data": data
    }
    existing = [w["ticker"] for w in st.session_state.watchlist]
    if ticker not in existing:
        st.session_state.watchlist.append(entry)
        _save_watchlist()
        return True
    return False

def remove_from_watchlist(ticker):
    """Entfernt Ticker von Watchlist (mit Persistenz)."""
    st.session_state.watchlist = [w for w in st.session_state.watchlist if w["ticker"] != ticker]
    _save_watchlist()

@st.cache_data(ttl=86400)
def _resolve_coingecko_id(symbol):
    """
    V67.5: Mappt Krypto-Symbol (BTC, ETH) auf CoinGecko coin_id (bitcoin, ethereum).
    CoinGecko API braucht den vollen ID, nicht das Boersen-Symbol.
    Cached fuer 24h da sich IDs nicht aendern.
    """
    # Bekannte Top-Coins (spart API-Call)
    known_ids = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
        "SHIB": "shiba-inu", "LTC": "litecoin", "ATOM": "cosmos",
        "XLM": "stellar", "NEAR": "near", "FIL": "filecoin",
        "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
        "SUI": "sui", "SEI": "sei-network", "TIA": "celestia",
        "INJ": "injective-protocol", "FET": "fetch-ai", "RENDER": "render-token",
        "PEPE": "pepe", "WIF": "dogwifcoin", "BONK": "bonk",
        "FLOKI": "floki", "TRX": "tron", "TON": "the-open-network",
        "ICP": "internet-computer", "HBAR": "hedera-hashgraph",
        "VET": "vechain", "ALGO": "algorand", "FTM": "fantom",
        "SAND": "the-sandbox", "MANA": "decentraland", "AXS": "axie-infinity",
        "AAVE": "aave", "MKR": "maker", "CRV": "curve-dao-token",
        "LDO": "lido-dao", "RPL": "rocket-pool", "SNX": "havven",
        "COMP": "compound-governance-token", "SUSHI": "sushi",
        "1INCH": "1inch", "ENS": "ethereum-name-service",
        "IMX": "immutable-x", "GMT": "stepn", "APE": "apecoin",
    }
    sym = symbol.upper().strip()
    if sym in known_ids:
        return known_ids[sym]

    # Fallback: CoinGecko Search API
    try:
        search_url = f"https://api.coingecko.com/api/v3/search?query={sym.lower()}"
        resp = rate_limited_get(search_url, timeout=10)
        if resp.status_code == 200:
            coins = resp.json().get("coins", [])
            for c in coins:
                if c.get("symbol", "").upper() == sym:
                    return c.get("id", sym.lower())
            if coins:
                return coins[0].get("id", sym.lower())
    except Exception:
        pass
    return sym.lower()


# fetch_historical_data_crypto — Moved to modules/data_fetchers.py (V69.9 refactoring)


# fetch_historical_data_stocks — Moved to modules/data_fetchers.py (V69.9 refactoring)



# _fetch_historical_yahoo — Moved to modules/data_fetchers.py


# =============================================================================
# AI CHART ANALYZER - PATTERN RECOGNITION & TECHNICAL ANALYSIS
# =============================================================================

# fetch_ohlcv_for_chart — Moved to modules/data_fetchers.py (V69.9 refactoring)



# _fetch_ohlcv_yahoo — Moved to modules/data_fetchers.py


# _fetch_ohlcv_polygon — Moved to modules/data_fetchers.py


# =============================================================================
# VOLUME IMBALANCE (ICT) — Body-to-Body Gaps & Fair Value Gaps
# =============================================================================
# ICT Konzept: Volume Imbalances sind Lücken zwischen den BODIES zweier
# aufeinanderfolgender Kerzen. Diese Zonen wirken als Preismagneten —
# der Markt kehrt oft zurück um sie zu füllen (Mitigation).
#
# Typen:
#   VI (Volume Imbalance):  Body-Gap zwischen 2 Kerzen, Wicks können überlappen
#   FVG (Fair Value Gap):   3-Kerzen-Pattern, Wick von Kerze 1 berührt nicht Wick von Kerze 3
#   OG (Opening Gap):       Wicks überlappen sich GAR NICHT (stärkstes Signal)
#
# Trading:
#   - Unfilled bullish VI unter dem Preis = potentieller Support / Long Entry
#   - Unfilled bearish VI über dem Preis = potentielle Resistance / Short Entry
#   - Preis kehrt in ~70-80% der Fälle zurück um die Zone zu füllen
#   - Mitigation = Zone wurde vom Preis berührt (gefüllt)
#   - CE (Consequent Encroachment) = 50% der Zone gefüllt (wichtig für Entries)
# =============================================================================

# detect_volume_imbalances — Moved to modules/patterns.py (V69.6 refactoring)



# format_vi_for_display — Moved to modules/helpers.py


# =============================================================================
# ICT ORDER BLOCKS (OB) — Institutionelle Entry-Zonen
# =============================================================================
# Bullish OB: Letzte bärische Kerze VOR starkem Aufwärts-Impuls
# Bearish OB: Letzte bullische Kerze VOR starkem Abwärts-Impuls
# Zone = Body der Gegenkerze. Preis kehrt oft zurück um OB zu "mitigieren".
# =============================================================================

# detect_order_blocks — Moved to modules/patterns.py (V69.6 refactoring)



# =============================================================================
# ICT LIQUIDITY LEVELS — Buyside / Sellside
# =============================================================================
# Buyside: Über Equal Highs / Swing Highs (Buy Stops der Shorts)
# Sellside: Unter Equal Lows / Swing Lows (Sell Stops der Longs)
# =============================================================================

# detect_liquidity_levels — Moved to modules/patterns.py (V69.6 refactoring)



# format_smc_setup — Moved to modules/patterns.py (V69.6 refactoring)



# detect_wolfe_waves — Moved to modules/patterns.py (V69.6 refactoring)



# detect_chart_patterns — Moved to modules/patterns.py (V69.6 refactoring)



# calculate_vwap — Moved to modules/indicators.py (V69.6 refactoring)



# find_volume_voids_for_chart — Moved to modules/volume_analysis.py


# find_harmonic_for_chart — Moved to modules/patterns.py


# generate_ai_chart_analysis — Moved to modules/analysis.py


# calculate_ema_series — Moved to modules/indicators.py (V69.6 refactoring)



# create_lightweight_chart_html — Moved to modules/chart_utils.py



def display_ai_chart_analyzer(ticker, poly_key, timeframe="1H"):
    """
    Hauptfunktion: Zeigt AI Chart mit ALLEN Analysen.
    
    Features:
    - Candlestick Chart mit Volume
    - EMAs (20/50/100/200)
    - Support/Resistance Levels
    - Fibonacci Retracements
    - VWAP + Standard Deviation Bands
    - Volume Voids (orange markiert)
    - Pattern Recognition
    - Trade Zones (Entry/Stop/Target)
    """
    st.subheader(f"🤖 AI Chart Analyzer - {ticker}")
    
    # Timeframe Selector - V67.2: +Weekly für Langzeit-Ansicht
    tf_cols = st.columns(7)
    
    timeframes = ["5m", "15m", "1H", "4H", "1D", "1W"]
    tf_labels = ["5 Min", "15 Min", "1H", "4H", "Daily", "Weekly"]
    tf_icons = ["⚡", "⏱️", "🕐", "📊", "📅", "📆"]
    
    for i, (tf, label, icon) in enumerate(zip(timeframes, tf_labels, tf_icons)):
        with tf_cols[i]:
            current_tf = st.session_state.get(f"chart_tf_{ticker}", timeframe)
            if st.button(f"{icon} {label}", key=f"tf_{tf}_{ticker}", 
                        type="primary" if tf == current_tf else "secondary",
                        use_container_width=True):
                st.session_state[f"chart_tf_{ticker}"] = tf
                st.rerun()
    
    # Info-Spalte: zeigt wie viel History geladen wird
    with tf_cols[6]:
        tf_info = {
            "5m": "7 Tage", "15m": "3 Wochen", "1H": "3 Monate",
            "4H": "6 Monate", "1D": "2 Jahre", "1W": "5 Jahre"
        }
        current_tf = st.session_state.get(f"chart_tf_{ticker}", timeframe)
        st.caption(f"📏 {tf_info.get(current_tf, '')}")
    
    # Get current timeframe from session
    current_tf = st.session_state.get(f"chart_tf_{ticker}", timeframe)
    
    # Fetch Data
    with st.spinner(f"📥 Lade {ticker} Chart Daten ({current_tf})..."):
        ohlcv = fetch_ohlcv_for_chart(ticker, poly_key, current_tf)
    
    if not ohlcv:
        st.error(f"❌ Keine Chart-Daten für {ticker} im Timeframe {current_tf} verfügbar")
        st.caption("Versuche einen anderen Timeframe oder prüfe ob der Ticker korrekt ist.")
        return
    
    st.caption(f"📊 {len(ohlcv)} Bars geladen")
    
    current_price = ohlcv[-1]["close"]
    
    # Calculate ALL Technical Analysis
    with st.spinner("🔍 Berechne alle Indikatoren..."):
        
        # ======== ECHTE S/R ANALYSE ========
        highs = [d["high"] for d in ohlcv]
        lows = [d["low"] for d in ohlcv]
        closes = [d["close"] for d in ohlcv]
        volumes = [d.get("volume", 0) for d in ohlcv]
        
        period_high = max(highs)
        period_low = min(lows)
        price_range = period_high - period_low
        
        key_levels = []
        
        # ============================================
        # 1. MAJOR SWING POINTS (Wichtigste Wendepunkte)
        # ============================================
        # ADAPTIVER min_swing_pct basierend auf ATR
        # Low-Vol Stocks brauchen niedrigere Schwelle
        
        # Max Proximity: Levels weiter als 35% vom Preis weg sind nutzlos
        max_distance_pct = 0.35
        atr_values = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr_values.append(tr)
        atr_14 = sum(atr_values[-14:]) / min(14, len(atr_values)) if atr_values else current_price * 0.02
        atr_pct = atr_14 / current_price if current_price > 0 else 0.02
        
        # Min Swing = 3× ATR% (z.B. ATR=2% → braucht 6% Move, ATR=5% → 15%)
        # Floor bei 4%, Cap bei 15%
        min_swing_pct = max(0.04, min(0.15, atr_pct * 3))
        
        # Swing Highs
        total_candles = len(highs)
        for i in range(5, len(highs) - 1):
            # Ist dies ein lokales Hoch?
            if highs[i] >= max(highs[max(0,i-5):i]) and highs[i] >= max(highs[i+1:min(len(highs),i+3)]):
                # Proximity-Check: Level >35% vom Preis weg → überspringen
                distance_pct = abs(highs[i] - current_price) / current_price if current_price > 0 else 1
                if distance_pct > max_distance_pct:
                    continue
                
                # Wie weit ist der Preis danach gefallen?
                future_low = min(lows[i+1:min(len(lows), i+20)]) if i+1 < len(lows) else lows[-1]
                drop_pct = (highs[i] - future_low) / highs[i]
                
                if drop_pct >= min_swing_pct:
                    recency_bonus = int((i / total_candles) * 15)
                    proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                    key_levels.append({
                        "price": highs[i],
                        "type": "Swing High",
                        "strength": min(95, 50 + int(drop_pct * 80) + recency_bonus + proximity_bonus),
                        "is_support": highs[i] < current_price
                    })
        
        # Swing Lows
        for i in range(5, len(lows) - 1):
            if lows[i] <= min(lows[max(0,i-5):i]) and lows[i] <= min(lows[i+1:min(len(lows),i+3)]):
                distance_pct = abs(lows[i] - current_price) / current_price if current_price > 0 else 1
                if distance_pct > max_distance_pct:
                    continue
                
                future_high = max(highs[i+1:min(len(highs), i+20)]) if i+1 < len(highs) else highs[-1]
                rally_pct = (future_high - lows[i]) / lows[i] if lows[i] > 0 else 0
                
                if rally_pct >= min_swing_pct:
                    recency_bonus = int((i / total_candles) * 15)
                    proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                    key_levels.append({
                        "price": lows[i],
                        "type": "Swing Low",
                        "strength": min(95, 50 + int(rally_pct * 80) + recency_bonus + proximity_bonus),
                        "is_support": lows[i] < current_price
                    })
        
        # ============================================
        # 2. VOLUME CLUSTER LEVELS (Wo wurde viel gehandelt?)
        # ============================================
        if sum(volumes) > 0:
            # Teile Price Range in Bins
            num_bins = 30
            bin_size = price_range / num_bins if price_range > 0 else 1
            volume_bins = {}
            
            for i, (h, l, v) in enumerate(zip(highs, lows, volumes)):
                # Verteile Volume auf alle Bins die die Kerze berührt
                low_bin = int((l - period_low) / bin_size) if bin_size > 0 else 0
                high_bin = int((h - period_low) / bin_size) if bin_size > 0 else 0
                
                for b in range(max(0, low_bin), min(num_bins, high_bin + 1)):
                    bin_price = period_low + (b + 0.5) * bin_size
                    if bin_price not in volume_bins:
                        volume_bins[bin_price] = 0
                    volume_bins[bin_price] += v / max(1, high_bin - low_bin + 1)
            
            # Finde die Top Volume Levels (POC-artig)
            if volume_bins:
                sorted_bins = sorted(volume_bins.items(), key=lambda x: x[1], reverse=True)
                avg_vol = sum(v for _, v in sorted_bins) / len(sorted_bins)
                
                # Nur Levels mit überdurchschnittlichem Volume UND in Proximity-Zone
                for price, vol in sorted_bins[:5]:
                    distance_pct = abs(price - current_price) / current_price if current_price > 0 else 1
                    if vol > avg_vol * 1.5 and distance_pct <= max_distance_pct:
                        key_levels.append({
                            "price": price,
                            "type": "High Volume",
                            "strength": 75,
                            "is_support": price < current_price
                        })
        
        # ============================================
        # 3. FIBONACCI LEVELS (Vom letzten signifikanten Swing)
        # ============================================
        # Finde den letzten großen Swing für Fibonacci
        # Nicht immer Period High→Low (das ist falsch bei Seitwärtsmärkten)
        
        fib_high = period_high
        fib_low = period_low
        
        # Suche den letzten signifikanten Swing High und Swing Low
        # in den letzten 60% der Daten
        recent_start = max(0, len(highs) - int(len(highs) * 0.6))
        
        # Letzter signifikanter Swing High
        for i in range(len(highs) - 2, recent_start, -1):
            if i >= 5 and highs[i] >= max(highs[max(0,i-5):i]) and highs[i] >= max(highs[i+1:min(len(highs),i+3)]):
                future_low = min(lows[i+1:min(len(lows), i+15)]) if i+1 < len(lows) else lows[-1]
                if (highs[i] - future_low) / highs[i] > atr_pct * 2:
                    fib_high = highs[i]
                    break
        
        # Letzter signifikanter Swing Low
        for i in range(len(lows) - 2, recent_start, -1):
            if i >= 5 and lows[i] <= min(lows[max(0,i-5):i]) and lows[i] <= min(lows[i+1:min(len(lows),i+3)]):
                future_high = max(highs[i+1:min(len(highs), i+15)]) if i+1 < len(highs) else highs[-1]
                if (future_high - lows[i]) / lows[i] > atr_pct * 2 if lows[i] > 0 else False:
                    fib_low = lows[i]
                    break
        
        fib_range = fib_high - fib_low
        fib_ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
        
        # Nur Fib Levels wenn die Range groß genug ist (min 3× ATR)
        if fib_range > atr_14 * 3:
            for ratio in fib_ratios:
                fib_price = fib_low + fib_range * ratio
                distance_pct = abs(fib_price - current_price) / current_price if current_price > 0 else 1
                # Nur wenn nicht zu nah UND nicht zu weit
                if 0.02 < distance_pct <= max_distance_pct:
                    key_levels.append({
                        "price": fib_price,
                        "type": f"Fib {ratio*100:.1f}%",
                        "strength": 70 if ratio in [0.5, 0.618] else 60,
                        "is_support": fib_price < current_price
                    })
        
        # ============================================
        # 4. PERIOD HIGH/LOW (nur wenn nah genug am Preis!)
        # ============================================
        
        ph_dist = abs(period_high - current_price) / current_price if current_price > 0 else 1
        pl_dist = abs(period_low - current_price) / current_price if current_price > 0 else 1
        
        if ph_dist <= max_distance_pct:
            key_levels.append({
                "price": period_high,
                "type": "Period High",
                "strength": 90,
                "is_support": False
            })
        if pl_dist <= max_distance_pct:
            key_levels.append({
                "price": period_low,
                "type": "Period Low", 
                "strength": 90,
                "is_support": True
            })
        
        # ============================================
        # 5. ROUND NUMBERS (Psychologische Levels)
        # ============================================
        if current_price >= 100:
            round_step = 10
        elif current_price >= 10:
            round_step = 1
        elif current_price >= 1:
            round_step = 0.5
        else:
            round_step = 0.05
        
        round_price = round(current_price / round_step) * round_step
        for offset in [-3, -2, -1, 1, 2, 3]:
            rp = round_price + offset * round_step
            distance_pct = abs(rp - current_price) / current_price if current_price > 0 else 1
            if distance_pct > max_distance_pct or distance_pct < 0.02:
                continue
            key_levels.append({
                "price": rp,
                "type": f"Round ${rp:.2f}",
                "strength": 55 + int(max(0, (1 - distance_pct / max_distance_pct)) * 15),
                "is_support": rp < current_price
            })
        
        # ============================================
        # 6. GAP LEVELS (Offene Gaps, nur in Proximity-Zone)
        # ============================================
        for i in range(1, len(ohlcv)):
            prev_close = ohlcv[i-1]["close"]
            curr_open = ohlcv[i]["open"]
            gap_pct = abs(curr_open - prev_close) / prev_close if prev_close > 0 else 0
            
            gap_price = (prev_close + curr_open) / 2
            distance_pct = abs(gap_price - current_price) / current_price if current_price > 0 else 1
            
            # Nur signifikante Gaps (>2%) innerhalb Proximity-Zone
            if gap_pct > 0.02 and distance_pct <= max_distance_pct:
                key_levels.append({
                    "price": gap_price,
                    "type": "Gap",
                    "strength": 65,
                    "is_support": gap_price < current_price
                })
        
        # ============================================
        # CLUSTERING: Merge nahe Levels
        # ============================================
# cluster_nearby_levels — Moved to modules/helpers.py
        
        # Cluster alle Levels
        all_clustered = cluster_nearby_levels(key_levels, tolerance_pct=0.03)
        
        # Trenne in Support und Resistance
        supports = [l for l in all_clustered if l["price"] < current_price * 0.98]
        resistances = [l for l in all_clustered if l["price"] > current_price * 1.02]
        
        # Filter: Entferne Levels die zu weit weg sind (>35%)
        supports = [l for l in supports if abs(l["price"] - current_price) / current_price <= max_distance_pct]
        resistances = [l for l in resistances if abs(l["price"] - current_price) / current_price <= max_distance_pct]
        
        # Sortiere nach COMBINED Score: Stärke + Proximity
# combined_score — Moved to modules/helpers.py
        
        supports = sorted(supports, key=lambda l: combined_score(l, current_price), reverse=True)[:3]
        resistances = sorted(resistances, key=lambda l: combined_score(l, current_price), reverse=True)[:3]
        
        # Sortiere nach Nähe zum Preis für Anzeige
        supports = sorted(supports, key=lambda x: x["price"], reverse=True)
        resistances = sorted(resistances, key=lambda x: x["price"])
        
        sr_levels = {
            "support_levels": supports,
            "resistance_levels": resistances,
            "current_price": current_price
        }
        
        # PDH/PDL/PDC — Echte Previous Day Berechnung
        # Gruppiere Bars nach Datum, finde den vorletzten TAG
        from collections import defaultdict
        day_bars = defaultdict(list)
        for d in ohlcv:
            ts = d.get("time", 0)
            if ts > 1e10:  # ms → s
                ts = ts / 1000
            try:
                day_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts > 0 else "unknown"
            except Exception:
                day_str = "unknown"
            day_bars[day_str].append(d)
        
        sorted_days = sorted([k for k in day_bars.keys() if k != "unknown"])
        
        if len(sorted_days) >= 2:
            prev_day_key = sorted_days[-2]  # Vorgestriger Tag
            prev_bars = day_bars[prev_day_key]
            pdh = max(b["high"] for b in prev_bars)
            pdl = min(b["low"] for b in prev_bars)
            pdc = prev_bars[-1]["close"]
        else:
            pdh = period_high
            pdl = period_low
            pdc = closes[-1]
        
        # Fib Levels für separate Anzeige (nutze die verbesserten Swing Punkte)
        fib_levels = {
            "swing_high": fib_high,
            "swing_low": fib_low,
            "levels": {
                "0.0": fib_low,
                "0.236": fib_low + fib_range * 0.236,
                "0.382": fib_low + fib_range * 0.382,
                "0.5": fib_low + fib_range * 0.5,
                "0.618": fib_low + fib_range * 0.618,
                "0.786": fib_low + fib_range * 0.786,
                "1.0": fib_high,
            }
        }
        
        # Patterns
        patterns = detect_chart_patterns(ohlcv, lookback=80)
        
        # VWAP
        vwap_data = calculate_vwap(ohlcv)
        
        # Volume Voids
        volume_voids = find_volume_voids_for_chart(ohlcv, num_bins=20)
        
        # Harmonic Patterns (auf dem aktuellen Timeframe!)
        harmonic_data = find_harmonic_for_chart(ohlcv)
        
        # Wyckoff Patterns (auf dem aktuellen Timeframe!)
        wyckoff_data = find_wyckoff_for_chart(ohlcv)
        
        # Volume Profile für POC/VAH/VAL
        vp = None
        if len(ohlcv) >= 20:
            ohlcv_for_vp = [{"high": d["high"], "low": d["low"], "volume": d["volume"]} for d in ohlcv]
            vp = calculate_volume_profile(ohlcv_for_vp, num_bins=15)
        
        # Generate AI Analysis
        ai_analysis = generate_ai_chart_analysis(ticker, ohlcv, patterns, sr_levels, fib_levels, vp)
        
        # Trade Zones from AI Analysis
        trade_zones = None
        if ai_analysis and ai_analysis.get("trade_idea"):
            trade = ai_analysis.get("trade_idea", {})
            trade_zones = {
                "entry": trade.get("entry"),
                "stop": trade.get("stop"),
                "target": trade.get("target")
            }
    
    # Display Options - ÜBERSICHTLICHER: Weniger default an
    st.markdown("**⚙️ Chart Optionen:**")
    col_opt1, col_opt2, col_opt3, col_opt4, col_opt5, col_opt6, col_opt7, col_opt8 = st.columns(8)
    with col_opt1:
        show_ema = st.checkbox("📈 EMAs", value=True, key=f"ema_{ticker}_{current_tf}")
    with col_opt2:
        show_sr = st.checkbox("📏 S/R", value=True, key=f"sr_{ticker}_{current_tf}")
    with col_opt3:
        show_vwap = st.checkbox("📊 VWAP", value=False, key=f"vwap_{ticker}_{current_tf}")
    with col_opt4:
        show_fib = st.checkbox("🎯 Fib", value=False, key=f"fib_{ticker}_{current_tf}")
    with col_opt5:
        show_voids = st.checkbox("🕳️ Voids", value=False, key=f"voids_{ticker}_{current_tf}")
    with col_opt6:
        show_harmonic = st.checkbox("🦋 Harmonic", value=False, key=f"harmonic_{ticker}_{current_tf}")
    with col_opt7:
        show_wyckoff = st.checkbox("🏦 Wyckoff", value=False, key=f"wyckoff_{ticker}_{current_tf}")
    with col_opt8:
        show_zones = st.checkbox("🎯 Zones", value=False, key=f"zones_{ticker}_{current_tf}")
    
    # PDH/PDL/PDC Anzeige
    if pdh > 0 and pdl > 0:
        col_pdh, col_pdl, col_pdc = st.columns(3)
        with col_pdh:
            st.caption(f"📈 PDH: ${pdh:.2f}")
        with col_pdl:
            st.caption(f"📉 PDL: ${pdl:.2f}")
        with col_pdc:
            st.caption(f"📊 PDC: ${pdc:.2f}")
    
    # Generate Chart HTML
    chart_html = create_lightweight_chart_html(
        ohlcv_data=ohlcv,
        ticker=ticker,
        sr_levels=sr_levels if show_sr else None,
        patterns=patterns,
        fib_levels=fib_levels if show_fib else None,
        ema_periods=[20, 50, 200] if show_ema else [],
        height=500,
        show_volume=True,
        vwap_data=vwap_data if show_vwap else None,
        volume_voids=volume_voids if show_voids else None,
        trade_zones=trade_zones if show_zones else None,
        harmonic_data=harmonic_data if show_harmonic else None,
        wyckoff_data=wyckoff_data if show_wyckoff else None
    )
    
    # Display Chart
    components.html(chart_html, height=520)
    
    # Analysis Section
    st.divider()
    
    col_patterns, col_levels, col_trade = st.columns([1, 1, 1])
    
    # === PATTERNS ===
    with col_patterns:
        st.subheader("🔍 Patterns")
        
        # Harmonic Patterns (wenn erkannt)
        if harmonic_data:
            for hp in harmonic_data[:2]:
                direction_emoji = "🟢" if hp.get("direction", "LONG") == "LONG" else "🔴"
                st.success(f"{hp.get('emoji', '')} **{hp.get('pattern', 'Unknown')}** {direction_emoji} {hp['direction']}") if hp.get("direction", "LONG") == "LONG" else st.error(f"{hp.get('emoji', '')} **{hp.get('pattern', 'Unknown')}** {direction_emoji} {hp['direction']}")
                st.caption(f"Score: {hp.get('score', 0)} | Matches: {hp['matches']} | Erfolg: {hp.get('success_rate', '?')}%")
                if hp.get("trade"):
                    t = hp["trade"]
                    st.caption(f"Entry: ${t.get('entry', 0):.2f} | SL: ${t.get('stop_loss', 0):.2f} | TP1: ${t.get('tp1', 0):.2f}")
        
        # Wyckoff Patterns (wenn erkannt)
        if wyckoff_data:
            for wp in wyckoff_data[:2]:
                direction_emoji = "🟢" if wp.get("direction", "LONG") == "LONG" else "🔴"
                if wp.get("direction", "LONG") == "LONG":
                    st.success(f"🏦 **Wyckoff {wp.get('type', 'Unknown')}** {direction_emoji} {wp.get('phase', 'Unknown')}")
                else:
                    st.error(f"🏦 **Wyckoff {wp.get('type', 'Unknown')}** {direction_emoji} {wp.get('phase', 'Unknown')}")
                st.caption(f"Score: {wp.get('score', 0)} | Range: ${wp.get('range_low', 0):.2f}—${wp.get('range_high', 0):.2f}")
                events_str = " → ".join([e.get('name', '') for e in wp.get('events', [])])
                if events_str:
                    st.caption(f"Events: {events_str}")
                if wp.get("trade"):
                    t = wp.get("trade", {})
                    st.caption(f"Entry: ${t.get('entry', 0):.2f} | SL: ${t.get('stop', 0):.2f} | TP1: ${t.get('tp1', 0):.2f}")
        
        if patterns:
            for p in patterns[:5]:
                emoji = p.get("emoji", "📊")
                pattern_name = p.get("pattern", "Unknown")
                pattern_type = p.get("type", "neutral")
                confidence = p.get("confidence", "Medium")
                
                if pattern_type == "bullish":
                    st.success(f"{emoji} **{pattern_name}**")
                elif pattern_type == "bearish":
                    st.error(f"{emoji} **{pattern_name}**")
                else:
                    st.info(f"{emoji} **{pattern_name}**")
                
                st.caption(f"{confidence} Confidence")
        else:
            st.info("👀 Keine klaren Patterns")
    
    # === LEVELS ===
    with col_levels:
        st.subheader("📏 Key Levels")
        
        current_price = ohlcv[-1]["close"]
        st.metric("Aktuell", f"${current_price:.2f}")
        
        # S/R
        if sr_levels:
            supports = sr_levels.get("support_levels", [])
            resistances = sr_levels.get("resistance_levels", [])
            
            if resistances:
                st.caption(f"🔴 R1: ${resistances[0]['price']:.2f}")
            if supports:
                st.caption(f"🟢 S1: ${supports[0]['price']:.2f}")
        
        # VWAP
        if vwap_data:
            vwap = vwap_data.get("vwap", 0)
            st.caption(f"📊 VWAP: ${vwap:.2f}")
            if current_price > vwap:
                st.caption("↑ Über VWAP (Bullish)")
            else:
                st.caption("↓ Unter VWAP (Bearish)")
        
        # Volume Profile
        if vp:
            poc = vp.get("poc", current_price)
            st.caption(f"📈 POC: ${poc:.2f}")
            
        # Volume Voids
        if volume_voids:
            st.caption(f"🕳️ {len(volume_voids)} Volume Voids gefunden")
        
        # Harmonic Patterns count
        if harmonic_data:
            hp = harmonic_data[0]
            st.caption(f"🦋 {hp.get('pattern', 'Unknown')} ({hp['direction']}) Score={hp.get('score', 0)}")
        
        # Wyckoff Patterns
        if wyckoff_data:
            wp = wyckoff_data[0]
            st.caption(f"🏦 Wyckoff {wp.get('type', 'Unknown')} ({wp.get('phase', 'Unknown')}) Score={wp.get('score', 0)}")
    
    # === TRADE SETUP ===
    with col_trade:
        st.subheader("💡 Trade Setup")
        
        if ai_analysis and ai_analysis.get("trade_idea"):
            trade = ai_analysis.get("trade_idea", {})
            bias = ai_analysis.get("bias", "Neutral")
            
            bias_emoji = "🟢" if trade.get("direction", "LONG") == "LONG" else "🔴"
            st.markdown(f"### {bias_emoji} {trade.get('direction', 'LONG')}")
            
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                st.metric("🎯 Entry", f"${trade.get('entry', 0):.2f}")
                st.metric("🛑 Stop", f"${trade.get('stop', 0):.2f}")
            with col_t2:
                st.metric("✅ Target", f"${trade.get('target', 0):.2f}")
                rr = trade.get("risk_reward", 0)
                rr_color = "green" if rr >= 2 else "orange" if rr >= 1 else "red"
                st.markdown(f"**R:R:** <span style='color:{rr_color};font-size:18px;'>{rr:.1f}:1</span>", unsafe_allow_html=True)
            
            # Rating
            if rr >= 2:
                st.success("✅ Gutes Setup!")
            elif rr >= 1:
                st.warning("⚠️ OK Setup")
            else:
                st.error("❌ Schlechtes R:R")
        else:
            st.info("🔍 Warte auf klares Setup...")
            st.caption("Kein eindeutiger Bias erkannt.")
    
    # Pattern Details Expander
    if patterns:
        with st.expander("📋 Pattern Details"):
            for p in patterns:
                st.markdown(f"**{p.get('emoji', '')} {p.get('pattern', '')}**")
                
                # Wyckoff-spezifische Anzeige
                if "Wyckoff" in p.get("pattern", ""):
                    phase_emoji = p.get("phase_emoji", "")
                    st.markdown(f"{phase_emoji} **Phase:** {p.get('phase', '')}")
                    st.caption(f"Range: ${p.get('range_low', 0):.2f} — ${p.get('range_high', 0):.2f} | Score: {p.get('score', 0)}/100 | Target: ${p.get('target', 0):.2f}")
                    
                    events = p.get("events", [])
                    if events:
                        st.markdown("**Erkannte Events:**")
                        for event in events:
                            st.caption(f"  ✓ {event}")
                else:
                    st.caption(p.get("description", ""))
                
                st.divider()


# fetch_realtime_price_alpaca — Moved to modules/data_fetchers.py (V69.9 refactoring)



# fetch_realtime_price_polygon — Moved to modules/data_fetchers.py (V69.9 refactoring)



# calculate_sr_from_historical — Moved to modules/analysis.py



# calculate_sr_levels_simple — Moved to modules/helpers.py


# calculate_sr_levels — Moved to modules/helpers.py

# =============================================================================
# 3b. AKKUMULATIONS-ANALYSE (Wyckoff-Style)
# =============================================================================

# calculate_obv — Moved to modules/indicators.py (V69.6 refactoring)


# calculate_accumulation_score — Moved to modules/analysis.py



# get_accumulation_display — Moved to modules/analysis.py

# =============================================================================
# EARNINGS CALENDAR — Finnhub Earnings Warning System
# =============================================================================

def fetch_earnings_calendar(finnhub_key, days_ahead=7):
    """
    Holt Earnings Calendar von Finnhub für die nächsten X Tage.
    EIN API-Call für ALLE Earnings — sehr effizient.
    
    Returns: Dict {ticker: {"date": "2026-02-26", "hour": "amc", "epsEstimate": 1.5, ...}}
    hour: "bmo" = Before Market Open, "amc" = After Market Close, "dmh" = During Market Hours
    """
    try:
        from datetime import datetime, timedelta
        today = datetime.now()
        
        # Cache-Check (30 Min gültig, vermeidet doppelte API-Calls)
        _cache = st.session_state.get("_earnings_cache")
        _cache_time = st.session_state.get("_earnings_cache_time")
        if _cache is not None and _cache_time and (today - _cache_time).total_seconds() < 1800:
            return _cache
        
        from_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")  # Gestern (für AMC von gestern)
        to_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        
        url = "https://finnhub.io/api/v1/calendar/earnings"
        params = {"from": from_date, "to": to_date, "token": finnhub_key}
        resp = rate_limited_get(url, params=params, timeout=10)
        data = resp.json()
        
        calendar = {}
        earnings_list = data.get("earningsCalendar", [])
        
        for entry in earnings_list:
            symbol = entry.get("symbol", "")
            if not symbol:
                continue
            
            ear_date = entry.get("date", "")
            hour = entry.get("hour", "")  # bmo, amc, dmh
            
            # Nur zukünftige oder heutige Earnings (und gestern AMC)
            calendar[symbol] = {
                "date": ear_date,
                "hour": hour,
                "epsEstimate": entry.get("epsEstimate"),
                "revenueEstimate": entry.get("revenueEstimate"),
                "quarter": entry.get("quarter"),
                "year": entry.get("year"),
            }
        
        # Cache speichern
        st.session_state["_earnings_cache"] = calendar
        st.session_state["_earnings_cache_time"] = today
        
        return calendar
    except Exception:
        return {}


# check_earnings_proximity — Moved to modules/analysis.py


# =============================================================================
# ECONOMIC CALENDAR — Makro-Events (FOMC, CPI, NFP, etc.)
# =============================================================================

@st.cache_data(ttl=1800)
def fetch_economic_calendar(_finnhub_key=None, days_ahead=7):
    """
    Wirtschaftskalender: Holt makroökonomische Events.

    Primär: Finnhub /calendar/economic (falls Key vorhanden)
    Fallback: Kuratierte Liste der wichtigsten US-Makro-Events

    Returns: list[dict] sortiert nach Datum/Uhrzeit
        [{date, time, event, impact, country, actual, estimate, prior, unit}]
    """
    from datetime import datetime, timedelta
    import pytz

    events = []
    today = datetime.now()
    from_date = today.strftime("%Y-%m-%d")
    to_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # ── Primär: Finnhub Economic Calendar (nur wenn Premium-Plan) ──
    if _finnhub_key:
        try:
            url = "https://finnhub.io/api/v1/calendar/economic"
            params = {"from": from_date, "to": to_date, "token": _finnhub_key}
            resp = rate_limited_get(url, params=params, timeout=5)
            # 403 = Free Plan hat keinen Zugang → sofort Fallback
            if resp.status_code == 403 or resp.status_code == 401:
                _finnhub_key = None  # Merke: kein Premium → Fallback
                raise ValueError("Finnhub Free Plan — kein Economic Calendar Zugang")
            data = resp.json()

            for ev in data.get("economicCalendar", []):
                event_name = ev.get("event", "")
                country = ev.get("country", "")

                # Impact-Level ableiten aus Event-Name
                impact = _classify_economic_impact(event_name)

                events.append({
                    "date": ev.get("date", ""),
                    "time": ev.get("time", ""),
                    "event": event_name,
                    "impact": impact,
                    "country": country,
                    "actual": ev.get("actual"),
                    "estimate": ev.get("estimate"),
                    "prior": ev.get("prev"),
                    "unit": ev.get("unit", ""),
                })
        except Exception:
            pass  # Fallback wird genutzt

    # ── Fallback: Kuratierte recurring Events ──
    if not events:
        events = _generate_known_economic_events(from_date, to_date)

    # Sortieren: zuerst nach Datum, dann Impact (High first)
    _impact_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    events.sort(key=lambda e: (e.get("date", ""), _impact_order.get(e.get("impact", "LOW"), 2), e.get("time", "")))

    return events


def _classify_economic_impact(event_name: str) -> str:
    """Klassifiziert ein Economic Event nach Impact-Level."""
    name_upper = event_name.upper()

    # HIGH Impact Events
    high_keywords = [
        "FOMC", "FED INTEREST", "FEDERAL FUNDS RATE", "NON-FARM", "NONFARM",
        "NFP", "CPI", "CONSUMER PRICE INDEX", "GDP", "GROSS DOMESTIC",
        "UNEMPLOYMENT RATE", "PCE", "PERSONAL CONSUMPTION", "RETAIL SALES",
        "ISM MANUFACTURING PMI", "ISM SERVICES PMI", "PPI", "PRODUCER PRICE",
        "FED CHAIR", "POWELL", "CORE CPI", "CORE PCE", "INITIAL JOBLESS",
        "JOLTS", "MICHIGAN CONSUMER", "DURABLE GOODS", "HOUSING STARTS",
        "EXISTING HOME SALES", "NEW HOME SALES", "TRADE BALANCE",
        "EMPIRE STATE", "PHILLY FED",
    ]
    for kw in high_keywords:
        if kw in name_upper:
            return "HIGH"

    # MEDIUM Impact Events
    medium_keywords = [
        "BUILDING PERMITS", "INDUSTRIAL PRODUCTION", "CAPACITY UTILIZATION",
        "CONSUMER CONFIDENCE", "BUSINESS INVENTORIES", "FACTORY ORDERS",
        "WHOLESALE INVENTORIES", "IMPORT PRICE", "EXPORT PRICE",
        "BEIGE BOOK", "TREASURY", "AUCTION", "PMI", "ADP ",
        "CONTINUING CLAIMS", "PERSONAL INCOME", "PERSONAL SPENDING",
        "CHICAGO PMI", "RICHMOND FED", "DALLAS FED", "KANSAS CITY",
    ]
    for kw in medium_keywords:
        if kw in name_upper:
            return "MEDIUM"

    return "LOW"


def _generate_known_economic_events(from_date: str, to_date: str) -> list:
    """
    Generiert eine kuratierte Liste der wichtigsten US-Makro-Events.
    Basiert auf dem typischen monatlichen Release-Kalender.
    Wird nur als Fallback genutzt wenn kein API-Key vorhanden.
    """
    from datetime import datetime, timedelta

    events = []
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.strptime(to_date, "%Y-%m-%d")

    # Typischer monatlicher Kalender (Tag des Monats ist approximativ)
    _monthly_events = [
        (1,  "08:30", "ISM Manufacturing PMI", "HIGH"),
        (3,  "08:30", "ISM Services PMI", "HIGH"),
        (3,  "10:00", "JOLTS Job Openings", "HIGH"),
        (5,  "08:30", "Non-Farm Payrolls (NFP)", "HIGH"),
        (5,  "08:30", "Unemployment Rate", "HIGH"),
        (10, "08:30", "CPI (Consumer Price Index)", "HIGH"),
        (10, "08:30", "Core CPI m/m", "HIGH"),
        (12, "08:30", "PPI (Producer Price Index)", "HIGH"),
        (13, "08:30", "Initial Jobless Claims", "MEDIUM"),
        (15, "08:30", "Retail Sales m/m", "HIGH"),
        (15, "08:30", "Empire State Manufacturing", "MEDIUM"),
        (16, "09:15", "Industrial Production m/m", "MEDIUM"),
        (17, "10:00", "Michigan Consumer Sentiment (Prel.)", "HIGH"),
        (20, "08:30", "Housing Starts", "MEDIUM"),
        (20, "08:30", "Building Permits", "MEDIUM"),
        (22, "10:00", "Existing Home Sales", "MEDIUM"),
        (24, "10:00", "New Home Sales", "MEDIUM"),
        (25, "08:30", "Durable Goods Orders m/m", "HIGH"),
        (27, "08:30", "GDP (Quarterly, 2nd/3rd est.)", "HIGH"),
        (27, "08:30", "Initial Jobless Claims", "MEDIUM"),
        (28, "08:30", "PCE Price Index", "HIGH"),
        (28, "08:30", "Core PCE m/m", "HIGH"),
        (28, "10:00", "Michigan Consumer Sentiment (Final)", "MEDIUM"),
    ]

    # FOMC-Meetings (bekannte Termine + Approximation für zukünftige Jahre)
    _fomc_dates = {
        2025: ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
               "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17"],
        2026: ["2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
               "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"],
        2027: ["2027-01-27", "2027-03-17", "2027-05-05", "2027-06-16",
               "2027-07-28", "2027-09-22", "2027-11-03", "2027-12-15"],
    }
    _fomc_dates_list = []
    for _yr in range(start.year, end.year + 1):
        _fomc_dates_list.extend(_fomc_dates.get(_yr, []))

    # Events für jeden Monat im Zeitraum generieren
    current = start.replace(day=1)
    while current <= end + timedelta(days=31):
        for day, time_str, name, impact in _monthly_events:
            try:
                import calendar as _cal_mod
                _max_day = _cal_mod.monthrange(current.year, current.month)[1]
                ev_date = current.replace(day=min(day, _max_day))
            except ValueError:
                continue

            if start <= ev_date <= end:
                events.append({
                    "date": ev_date.strftime("%Y-%m-%d"),
                    "time": time_str,
                    "event": name,
                    "impact": impact,
                    "country": "US",
                    "actual": None,
                    "estimate": None,
                    "prior": None,
                    "unit": "",
                })

        # Nächster Monat
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    # FOMC-Termine einfügen
    for fomc_date_str in _fomc_dates_list:
        fomc_date = datetime.strptime(fomc_date_str, "%Y-%m-%d")
        if start <= fomc_date <= end:
            events.append({
                "date": fomc_date_str,
                "time": "14:00",
                "event": "FOMC Interest Rate Decision",
                "impact": "HIGH",
                "country": "US",
                "actual": None,
                "estimate": None,
                "prior": None,
                "unit": "%",
            })
            # Pressekonferenz 30 Min später
            events.append({
                "date": fomc_date_str,
                "time": "14:30",
                "event": "FOMC Press Conference (Powell)",
                "impact": "HIGH",
                "country": "US",
                "actual": None,
                "estimate": None,
                "prior": None,
                "unit": "",
            })

    return events


# =============================================================================
# 4. DATA FETCHING FUNCTIONS
# =============================================================================

def fetch_insider_transactions(finnhub_key, transaction_type="BUY"):
    """Holt Insider-Transaktionen von Finnhub"""
    results = []
    
    try:
        from datetime import timedelta
        
        # Letzte 30 Tage
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        # Finnhub Insider Transactions API
        url = f"https://finnhub.io/api/v1/stock/insider-transactions"
        params = {
            "symbol": "",  # Leer = alle
            "from": start_date,
            "to": end_date,
            "token": finnhub_key
        }
        
        # Wir holen die Top-Aktien mit Insider-Aktivität
        # Da Finnhub kein "alle" unterstützt, holen wir beliebte Ticker
        popular_tickers = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "AMD", "INTC",
            "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "PYPL",
            "JNJ", "PFE", "UNH", "MRK", "ABBV", "LLY", "BMY",
            "XOM", "CVX", "COP", "SLB", "OXY",
            "DIS", "NFLX", "CMCSA", "T", "VZ",
            "WMT", "COST", "TGT", "HD", "LOW",
            "BA", "CAT", "GE", "MMM", "HON",
            "KO", "PEP", "MCD", "SBUX", "NKE",
            "CRM", "ORCL", "IBM", "CSCO", "ADBE", "NOW", "SNOW", "PLTR",
            "SQ", "SHOP", "COIN", "HOOD", "SOFI",
            "RIVN", "LCID", "NIO", "F", "GM",
            "MRNA", "BNTX", "REGN", "VRTX", "BIIB"
        ]
        
        insider_data = {}  # ticker -> list of transactions
        
        # Batch-Abfrage (max 60/min bei Finnhub)
        for ticker in popular_tickers[:50]:  # Limitieren auf 50 für Speed
            try:
                url = f"https://finnhub.io/api/v1/stock/insider-transactions"
                params = {"symbol": ticker, "token": finnhub_key}
                resp = rate_limited_get(url, params=params, timeout=5)
                
                if resp.status_code == 200:
                    data = resp.json()
                    transactions = data.get("data", [])
                    
                    if transactions:
                        insider_data[ticker] = transactions
                        
            except Exception as e:
                continue
        
        # Filtern nach BUY oder SELL
        for ticker, transactions in insider_data.items():
            buy_value = 0
            sell_value = 0
            buy_count = 0
            sell_count = 0
            recent_transactions = []
            
            for t in transactions[:20]:  # Letzte 20 Transaktionen
                trans_type = t.get("transactionType", "")
                shares = abs(t.get("share", 0) or 0)
                price = t.get("transactionPrice", 0) or 0
                value = shares * price
                name = t.get("name", "Unknown")
                date = t.get("transactionDate", "")
                
                # P-Purchase, S-Sale, A-Grant/Award
                if "P" in trans_type or "Buy" in trans_type.lower():
                    buy_value += value
                    buy_count += 1
                    recent_transactions.append({
                        "type": "BUY",
                        "name": name,
                        "shares": shares,
                        "value": value,
                        "date": date
                    })
                elif "S" in trans_type or "Sale" in trans_type.lower():
                    sell_value += value
                    sell_count += 1
                    recent_transactions.append({
                        "type": "SELL",
                        "name": name,
                        "shares": shares,
                        "value": value,
                        "date": date
                    })
            
            # Filter nach gewünschtem Typ
            if transaction_type == "BUY" and buy_count > 0 and buy_value > 10000:
                results.append({
                    "Ticker": ticker,
                    "Name": "",
                    "InsiderType": "BUY",
                    "BuyCount": buy_count,
                    "BuyValue": buy_value,
                    "SellCount": sell_count,
                    "SellValue": sell_value,
                    "NetValue": buy_value - sell_value,
                    "Transactions": recent_transactions[:5],
                    "Alpha": int(buy_value / 10000)  # Alpha basiert auf Kaufvolumen
                })
            elif transaction_type == "SELL" and sell_count > 0 and sell_value > 50000:
                results.append({
                    "Ticker": ticker,
                    "Name": "",
                    "InsiderType": "SELL",
                    "BuyCount": buy_count,
                    "BuyValue": buy_value,
                    "SellCount": sell_count,
                    "SellValue": sell_value,
                    "NetValue": buy_value - sell_value,
                    "Transactions": recent_transactions[:5],
                    "Alpha": int(sell_value / 10000)
                })
        
        # Sortieren nach Value
        if transaction_type == "BUY":
            results = sorted(results, key=lambda x: x["BuyValue"], reverse=True)
        else:
            results = sorted(results, key=lambda x: x["SellValue"], reverse=True)
        
        return results[:30], 0, 0
        
    except Exception as e:
        st.error(f"Finnhub Fehler: {e}")
        return [], 0, 0


def _cg_file_cache_usable(cached, age_seconds, max_age=120):
    """H-14 Audit-Fix (pure, testbar): Frische-Check für den CoinGecko-Datei-Cache.

    Partial-Caches (429-Teilabrufe, markiert mit "partial": true) gelten IMMER
    als stale → Re-Fetch statt dem Rumpf-Universum 2 Min blind zu vertrauen.
    """
    if not isinstance(cached, dict):
        return False
    if cached.get("partial"):
        return False
    coins = cached.get("coins") or []
    if not coins:
        return False
    return age_seconds is not None and age_seconds < max_age


@st.cache_data(ttl=120)
def _fetch_coingecko_markets(pages=4):
    """Cached CoinGecko markets API — 2 Min TTL, reduziert Rate Limit Probleme."""
    # Thread-Fallback: Wenn Datei-Cache frisch genug ist (< 2 Min), nutze den
    # H-14: partial-Caches (Teilabrufe) werden NICHT als frisch akzeptiert
    _CG_CACHE = "/tmp/coingecko_markets_cache.json"
    _partial_fallback = []
    try:
        if os.path.exists(_CG_CACHE):
            _cg_age = time.time() - os.path.getmtime(_CG_CACHE)
            with open(_CG_CACHE, "r") as _f:
                _cached = json.load(_f)
            if _cg_file_cache_usable(_cached, _cg_age):
                return _cached.get("coins", [])
            if _cached.get("partial") and (_cached.get("coins") or []):
                # Nur Notnagel falls der Live-Re-Fetch komplett leer ausgeht
                _partial_fallback = _cached.get("coins", [])
    except Exception:
        pass

    all_coins = []
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
        for _retry in range(4):  # 4 Retries mit längerem Backoff
            try:
                resp = rate_limited_get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    if _retry < 3:
                        time.sleep(15 * (_retry + 1))  # 15s, 30s, 45s Backoff
                        continue
                    break
                elif resp.status_code == 200:
                    break
                else:
                    # Anderer Fehler — Retry
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
            # Rate limited — gib zurück was wir haben (besser als nichts)
            break
        try:
            page_coins = resp.json() if resp and resp.status_code == 200 else []
        except Exception:
            page_coins = []
        if not isinstance(page_coins, list) or not page_coins:
            break
        all_coins.extend(page_coins)
        if page_num < pages:
            time.sleep(3.0)  # Mehr Pause zwischen Pages (war 2.5s)
    if not all_coins and _partial_fallback:
        # H-14: Live-Re-Fetch komplett gescheitert → alter Partial-Cache als Notnagel
        return _partial_fallback
    return all_coins


def fetch_crypto_data():
    """Holt Krypto-Daten von CoinGecko mit korrektem Vortag — 500 Coins (2 Seiten)"""
    results = []
    skipped_filter = 0
    
    try:
        # Nutze gecachte Marktdaten (2 Min TTL → weniger Rate Limits)
        coins = _fetch_coingecko_markets(pages=4)
        if not coins:
            return [], 0, 0

        # ── BTC Benchmark extrahieren (für Relative Stärke) ──
        btc_change_24h = 0
        btc_change_7d = 0
        for _c in coins:
            if _c.get("symbol", "").lower() == "btc" or _c.get("id", "") == "bitcoin":
                btc_change_24h = _c.get("price_change_percentage_24h") or 0
                btc_change_7d = (
                    _c.get("price_change_percentage_7d_in_currency") or
                    _c.get("price_change_percentage_7d") or 0
                )
                break

        f = st.session_state.active_filters
        af = st.session_state.additional_filters

        for coin in coins:
            try:
                price = coin.get("current_price") or 0
                if price <= 0:
                    continue
                
                # HEUTE: 24h Change
                change_24h = coin.get("price_change_percentage_24h") or 0
                
                # VORTAG BERECHNUNG (V67.5 FIX — MULTIPLIKATIV):
                # CoinGecko liefert kein einzelnes "gestern" - wir approximieren:
                # Prozentuale Aenderungen sind MULTIPLIKATIV, nicht additiv!
                # 7d_multiplier = (1 + 7d%/100), 24h_multiplier = (1 + 24h%/100)
                # 6d_multiplier = 7d_mul / 24h_mul
                # Vortag ≈ Durchschnittliche taegliche Aenderung der 6 Tage VOR heute
                change_7d = (
                    coin.get("price_change_percentage_7d_in_currency") or
                    coin.get("price_change_percentage_7d") or
                    0
                )

                if change_7d != 0 and change_24h != -100:
                    # Multiplikative Berechnung (korrekt fuer Prozente)
                    mul_7d = 1 + change_7d / 100
                    mul_24h = 1 + change_24h / 100
                    if mul_24h > 0:
                        mul_6d = mul_7d / mul_24h  # 6-Tage Performance ohne heute
                        # Durchschnittliche taegliche Aenderung ueber 6 Tage
                        # Geometrisches Mittel: mul_6d^(1/6) - 1
                        if mul_6d > 0:
                            avg_daily_mul = mul_6d ** (1/6)
                            vortag_chg = round((avg_daily_mul - 1) * 100, 2)
                        else:
                            vortag_chg = round((mul_6d - 1) * 100 / 6, 2)
                    else:
                        vortag_chg = 0
                else:
                    vortag_chg = 0
                
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price
                vol_24h = coin.get("total_volume") or 0
                market_cap = coin.get("market_cap") or 1

                # ── LIQUIDITÄTSFILTER (V70.7) ──
                # Volume = Liquidität. MCap niedrig damit Early Movers durchkommen.
                if vol_24h < 5_000_000:
                    continue  # <$5M Volume = deine Order bewegt den Preis
                if market_cap < 10_000_000:
                    continue  # <$10M MCap Minimum

                # OHLC für Wick-Berechnung
                # Approximation: Open = Price / (1 + change/100)
                open_price = price / (1 + change_24h / 100) if change_24h != -100 else price
                # V69 FIX: Clamp open_price in High/Low Range (sonst negative Wicks möglich)
                open_price = max(low_24h, min(high_24h, open_price))
                
                # Wick-Berechnungen (mit min_range_pct Check für Konsistenz)
                candle_range = high_24h - low_24h if high_24h > low_24h else 0
                range_pct = (candle_range / low_24h * 100) if low_24h > 0 else 0
                
                # Nur Wick berechnen wenn genug Range
                # Krypto: 0.2% Minimum (niedriger als Aktien, da 24h-Kerzen)
                if range_pct >= 0.2 and candle_range > 0:
                    body_top = max(open_price, price)
                    body_bottom = min(open_price, price)
                    upper_wick_pct = ((high_24h - body_top) / candle_range) * 100
                    lower_wick_pct = ((body_bottom - low_24h) / candle_range) * 100
                else:
                    upper_wick_pct = 0
                    lower_wick_pct = 0

                # GAP % - KRYPTO HAT KEINE ECHTEN GAPS (24/7 Markt)
                # Wir setzen es auf None damit der Filter weiß dass es nicht anwendbar ist
                gap_pct = None  # Explizit None für "nicht verfügbar"
                
                # RVOL Berechnung (Krypto-spezifisch) — V67.5 REVIDIERT
                # WICHTIG: CoinGecko liefert kein historisches Durchschnittsvolumen!
                # Wir verwenden "Turnover Ratio" = Vol24h / MarketCap als Proxy.
                #
                # Baselines basierend auf typischen Krypto-Turnover-Daten:
                #   - BTC/ETH (>$100B): ~2-4% Turnover normal
                #   - Large Cap L1s (>$10B): ~5-8% Turnover normal
                #   - Mid Cap ($1B-$10B): ~8-15% Turnover normal
                #   - Small Cap ($100M-$1B): ~15-30% Turnover normal
                #   - Micro Cap (<$100M): ~20-50%+ Turnover normal
                #
                # RVOL 1.0 = durchschnittliches Tagesvolumen fuer diese Kategorie
                if market_cap > 0 and vol_24h > 0:
                    turnover_pct = (vol_24h / market_cap) * 100
                    # Baseline = typischer Tages-Turnover (%) fuer diese Kategorie
                    # RVOL 1.0 bedeutet "normales Volumen fuer diese Groesse"
                    # V69 AUDIT FIX: Baselines an echte Mediane angepasst
                    # Quelle: CoinGecko Top-500 empirische Turnover-Daten
                    #   Mega >$100B: 2-4% normal, Large >$10B: 5-8%, Mid >$1B: 8-15%
                    #   Small >$100M: 15-30%, Micro <$100M: 20-50%+
                    if market_cap > 100_000_000_000:
                        baseline = 3.0    # Mega Cap (BTC, ETH) — Median ~3%
                    elif market_cap > 10_000_000_000:
                        baseline = 6.0    # Large Cap (SOL, BNB) — Median ~6%
                    elif market_cap > 1_000_000_000:
                        baseline = 10.0   # Mid Cap — Median ~10%
                    elif market_cap > 100_000_000:
                        baseline = 20.0   # Small Cap — Median ~20%
                    else:
                        baseline = 30.0   # Micro Cap — Median ~30%
                    rvol = round(turnover_pct / baseline, 2)
                    rvol = max(0.1, min(rvol, 50.0))  # Cap bei 50x
                else:
                    rvol = 1.0
                
                # Krypto: Niedrigerer min_range_pct (0.3%) da viele Coins kleinere Ranges haben
                close_pos = calculate_close_position(high_24h, low_24h, price, min_range_pct=0.3)

                # =====================================================
                # FILTER-LOGIK (KRYPTO-SPEZIFISCH)
                # =====================================================
                match = True
                
                # RVOL Filter
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if not (rvol_min <= rvol <= rvol_max): match = False
                
                # Change % (heute)
                if "Change %" in f and not (f["Change %"][0] <= change_24h <= f["Change %"][1]): 
                    match = False
                
                # Vortag % (approximiert aus 7d-Daten)
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): 
                    match = False
                
                # Preis
                if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): 
                    match = False
                
                # MarketCap Filter (z.B. Low Cap Rockets)
                if "MarketCap" in f:
                    mc_min, mc_max = f["MarketCap"]
                    if not (mc_min <= market_cap <= mc_max): match = False
                
                # Close Position - mit None Check (falls Range zu klein)
                if "Close Position" in f:
                    if close_pos is not None:
                        if not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): 
                            match = False
                    # Wenn close_pos None ist, ignoriere den Filter
                
                # Wick Filter (funktioniert bei Krypto)
                if "Upper Wick %" in f and not (f["Upper Wick %"][0] <= upper_wick_pct <= f["Upper Wick %"][1]): 
                    match = False
                if "Lower Wick %" in f and not (f["Lower Wick %"][0] <= lower_wick_pct <= f["Lower Wick %"][1]): 
                    match = False
                
                # GAP Filter - NICHT ANWENDBAR BEI KRYPTO!
                # Wenn jemand Gap-Strategie bei Krypto wählt, findet er nichts
                if "Gap %" in f:
                    # Krypto hat keine Gaps - dieser Filter matched nie
                    match = False
                
                # Zusatzfilter
                if af.get("preis_min", 0) > 0 and price < af["preis_min"]: match = False
                if af.get("preis_max", 100000) < 100000 and price > af["preis_max"]: match = False
                if af.get("nur_gewinner") and change_24h <= 0: match = False
                if af.get("nur_verlierer") and change_24h >= 0: match = False
                
                if not match:
                    skipped_filter += 1
                    continue
                
                ticker = coin.get("symbol", "").upper()
                alpha = calculate_alpha_score_crypto(rvol, vortag_chg, change_24h, market_cap,
                                                      high_24h=high_24h, low_24h=low_24h, price=price)
                
                # Flag-Pattern Validierung für Krypto
                flag_score = 0
                flag_details = []
                current_strategy = st.session_state.get("current_strategy", "")
                
                # Für Krypto: prev_close = open_price (24h Referenz)
                prev_close_approx = open_price
                
                if current_strategy == "Bull Flag":
                    is_valid, flag_score, flag_details = validate_flag_pattern(
                        vortag_chg, change_24h, rvol, price, prev_close_approx, high_24h, low_24h, "bull",
                        prev_high=high_24h, prev_low=low_24h,  # V69 FIX: 24h Range als Flagpole
                        market_type="Krypto"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score

                elif current_strategy == "Bear Flag":
                    is_valid, flag_score, flag_details = validate_flag_pattern(
                        vortag_chg, change_24h, rvol, price, prev_close_approx, high_24h, low_24h, "bear",
                        prev_high=high_24h, prev_low=low_24h,  # V69 FIX: 24h Range als Flagpole
                        market_type="Krypto"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score
                
                # Breakout Health für Crypto — ALLE Strategien
                breakout_health = None
                SHORT_KEYWORDS = ["Short", "Bear", "Breakdown", "Losers", "Down", "Distribution", "⬇️", "Selling"]
                setup_direction = "short" if any(kw in current_strategy for kw in SHORT_KEYWORDS) else "long"

                if (setup_direction == "long" and change_24h > 0) or (setup_direction == "short" and change_24h < 0):
                    breakout_health = assess_breakout_health(
                        change_pct=abs(change_24h), rvol=rvol, close_pos=close_pos,
                        high=high_24h, low=low_24h, close=price,
                        open_price=open_price, prev_close=prev_close_approx,
                        vortag_pct=vortag_chg, vi_result=None,
                        market_type="Krypto", market_cap=market_cap
                    )

                    # V68: 7d-Trend Korrektur
                    if breakout_health and change_7d < -5:
                        bh = breakout_health
                        penalty = 20 if change_7d < -15 else 15 if change_7d < -10 else 10
                        bh["health_score"] = max(10, bh.get("health_score", 0) - penalty)
                        bh.setdefault("warnings", []).append(
                            f"🔴 7d-Trend: {change_7d:+.1f}% — Bounce im Abwärtstrend, kein echter Breakout"
                        )
                        h = bh.get("health_score", 0)
                        if h >= 75: bh["verdict"], bh["verdict_emoji"] = "STRONG", "💪🟢"
                        elif h >= 55: bh["verdict"], bh["verdict_emoji"] = "HEALTHY", "✅🟢"
                        elif h >= 40: bh["verdict"], bh["verdict_emoji"] = "CAUTION", "⚠️🟡"
                        elif h >= 25: bh["verdict"], bh["verdict_emoji"] = "WEAK", "⚠️🟠"
                        else: bh["verdict"], bh["verdict_emoji"] = "FAKEOUT", "🚫🔴"
                        if h < 40:
                            bh["action"] = "KEIN ENTRY — 7d-Trend negativ, wahrscheinlich nur Bounce."

                # Setup Score für Krypto — CRYPTO-SPEZIFISCH
                setup_score = calculate_setup_score_crypto(
                    change_pct=change_24h, rvol=rvol, close_pos=close_pos,
                    upper_wick_pct=upper_wick_pct, lower_wick_pct=lower_wick_pct,
                    vortag_pct=vortag_chg, vol_24h=vol_24h, price=price,
                    market_cap=market_cap, direction=setup_direction,
                    high_24h=high_24h, low_24h=low_24h
                )

                # Health-Penalty auf SetupScore anwenden
                if breakout_health and isinstance(breakout_health, dict):
                    bh_score = breakout_health.get("health_score", 100)
                    bh_selloff = breakout_health.get("selloff_risk", "LOW")
                    if bh_score < 40 or bh_selloff in ("IMMINENT", "CRITICAL"):
                        setup_score = max(0, setup_score - 25)  # Schwere Penalty
                    elif bh_score < 55 or bh_selloff == "HIGH":
                        setup_score = max(0, setup_score - 15)
                    elif bh_score < 70 or bh_selloff == "MEDIUM":
                        setup_score = max(0, setup_score - 5)
                
                # BTC Relative Stärke
                rel_strength_24h = round(change_24h - btc_change_24h, 2)
                # Korrelations-Label
                if abs(change_24h - btc_change_24h) <= 2.0:
                    btc_corr_label = "🔗 BTC-korreliert"
                elif change_24h > btc_change_24h + 5.0:
                    btc_corr_label = "💪 Outperformer"
                elif change_24h < btc_change_24h - 5.0:
                    btc_corr_label = "⚠️ Underperformer"
                elif change_24h > btc_change_24h + 2.0:
                    btc_corr_label = "📈 Rel. stark"
                elif change_24h < btc_change_24h - 2.0:
                    btc_corr_label = "📉 Rel. schwach"
                else:
                    btc_corr_label = "↔️ Neutral"

                results.append({
                    "Ticker": ticker,
                    "Name": coin.get("name", "")[:15],
                    "Preis": round(price, 6),
                    "Chg%": round(change_24h, 2),
                    "RVOL": rvol,
                    "Vortag%": round(vortag_chg, 2),
                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5,
                    "Alpha": alpha,
                    "UpperWick%": round(upper_wick_pct, 1),
                    "LowerWick%": round(lower_wick_pct, 1),
                    "Gap%": 0,
                    "FlagScore": flag_score,
                    "FlagDetails": flag_details,
                    "High": high_24h,
                    "Low": low_24h,
                    "PrevClose": prev_close_approx,
                    "BreakoutHealth": breakout_health,
                    "SetupScore": setup_score,
                    "BTC_Chg%": round(btc_change_24h, 2),
                    "RelStrength": rel_strength_24h,
                    "BTC_Label": btc_corr_label,
                    "CandleAnalysis": None,  # V69: Wird post-scan für Top-Ergebnisse befüllt
                    "CoinId": coin.get("id", ""),  # Für CoinGecko History-Lookup
                })
            except Exception as e:
                continue
        
        return results, 0, skipped_filter
    except Exception as e:
        st.error(f"CoinGecko Fehler: {e}")
        return [], 0, 0


# =============================================================================
# BTC-DIVERGENZ SHORT SCANNER V1.0
# Findet Altcoins die gegen BTC-Schwäche pumpen → Short-Kandidaten bei Exhaustion
# =============================================================================

# calculate_exhaustion_score — Moved to modules/scorers.py (V69.6 refactoring)



# get_exhaustion_grade — Moved to modules/scorers.py (V69.6 refactoring)



# ═══════════════════════════════════════════════════════════════════════════════
# 🔥 EARLY MOVERS SCANNER V1.0 — Findet die nächsten 10x-Coins früh
# ═══════════════════════════════════════════════════════════════════════════════

# Narrative/Sektor-Mapping für bekannte Coins
CRYPTO_NARRATIVES = {
    # AI / ML
    "fetch-ai": "🤖 AI", "singularitynet": "🤖 AI", "ocean-protocol": "🤖 AI",
    "render-token": "🤖 AI", "akash-network": "🤖 AI", "bittensor": "🤖 AI",
    "artificial-superintelligence-alliance": "🤖 AI", "near": "🤖 AI",
    "worldcoin-wld": "🤖 AI", "arkham": "🤖 AI", "numeraire": "🤖 AI",
    "phala-network": "🤖 AI", "nosana": "🤖 AI", "virtuals-protocol": "🤖 AI",
    "ai16z": "🤖 AI", "griffain": "🤖 AI", "goatseus-maximus": "🤖 AI",
    "io-net": "🤖 AI", "grass": "🤖 AI",
    # Meme
    "dogecoin": "🐶 Meme", "shiba-inu": "🐶 Meme", "pepe": "🐶 Meme",
    "dogwifcoin": "🐶 Meme", "bonk": "🐶 Meme", "floki": "🐶 Meme",
    "brett-based": "🐶 Meme", "mog-coin": "🐶 Meme", "popcat": "🐶 Meme",
    "cat-in-a-dogs-world": "🐶 Meme", "neiro-on-eth": "🐶 Meme",
    "fartcoin": "🐶 Meme", "trump": "🐶 Meme", "melania-meme": "🐶 Meme",
    "peanut-the-squirrel": "🐶 Meme", "act-i-the-ai-prophecy": "🐶 Meme",
    # RWA (Real World Assets)
    "ondo-finance": "🏦 RWA", "mantra": "🏦 RWA", "polymesh": "🏦 RWA",
    "centrifuge": "🏦 RWA", "goldfinch": "🏦 RWA", "maple": "🏦 RWA",
    "clearpool": "🏦 RWA", "pendle": "🏦 RWA",
    # DePIN
    "helium": "📡 DePIN", "theta-token": "📡 DePIN", "filecoin": "📡 DePIN",
    "arweave": "📡 DePIN", "hivemapper": "📡 DePIN",
    "iotex": "📡 DePIN", "dimo-network": "📡 DePIN",
    # L1 / L2
    "solana": "⛓️ L1", "avalanche-2": "⛓️ L1", "sui": "⛓️ L1",
    "aptos": "⛓️ L1", "sei-network": "⛓️ L1", "injective-protocol": "⛓️ L1",
    "celestia": "⛓️ L1", "monad": "⛓️ L1", "berachain": "⛓️ L1",
    "arbitrum": "🔗 L2", "optimism": "🔗 L2", "polygon-ecosystem-token": "🔗 L2",
    "starknet": "🔗 L2", "zksync": "🔗 L2", "base-protocol": "🔗 L2",
    # DeFi
    "uniswap": "💰 DeFi", "aave": "💰 DeFi", "lido-dao": "💰 DeFi",
    "maker": "💰 DeFi", "curve-dao-token": "💰 DeFi", "compound-governance-token": "💰 DeFi",
    "jupiter-exchange-solana": "💰 DeFi", "raydium": "💰 DeFi",
    "hyperliquid": "💰 DeFi", "ethena": "💰 DeFi",
    # Gaming
    "immutable-x": "🎮 Gaming", "the-sandbox": "🎮 Gaming", "axie-infinity": "🎮 Gaming",
    "gala": "🎮 Gaming", "illuvium": "🎮 Gaming", "beam-2": "🎮 Gaming",
    "ronin": "🎮 Gaming", "pixels": "🎮 Gaming",
}


# ── Early Movers Background-Thread Helpers ──
_EARLY_PROGRESS_FILE = "/tmp/alpha_early_progress.json"
_EARLY_STOP_FILE = "/tmp/alpha_early_stop.flag"

def _early_progress_write(status, detail="", pct=0):
    """Schreibt Early Movers Scan-Progress."""
    try:
        import json as _j
        with open(_EARLY_PROGRESS_FILE, "w") as f:
            _j.dump({"status": status, "detail": detail, "pct": pct, "timestamp": time.time()}, f)
    except Exception:
        pass

def _early_progress_read():
    try:
        import json as _j
        with open(_EARLY_PROGRESS_FILE, "r") as f:
            return _j.load(f)
    except Exception:
        return None

def _early_progress_clear():
    try: os.remove(_EARLY_PROGRESS_FILE)
    except Exception: pass

def _early_request_stop():
    try:
        with open(_EARLY_STOP_FILE, "w") as f: f.write("stop")
    except Exception: pass

def _early_should_stop():
    return os.path.exists(_EARLY_STOP_FILE)

def _early_clear_stop():
    try: os.remove(_EARLY_STOP_FILE)
    except Exception: pass

def _early_background_scan():
    """Background-Thread für Early Movers Scan."""
    try:
        _early_clear_stop()
        _early_progress_write("running", "📡 Lade CoinGecko Marktdaten (Seite 1/4)...", 5)

        # 1. CoinGecko Markets laden — mit Progress zwischen den Pages
        all_coins = []
        for page_num in range(1, 5):
            if _early_should_stop():
                _early_progress_write("stopped", f"⏹️ Gestoppt bei Seite {page_num}/4")
                return
            _early_progress_write("running", f"📡 Lade CoinGecko Seite {page_num}/4...", page_num * 15)
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": 250, "page": page_num, "sparkline": False,
                "price_change_percentage": "1h,24h,7d,14d,30d"
            }
            resp = None
            for _retry in range(4):
                try:
                    resp = rate_limited_get(url, params=params, timeout=30)
                    if resp.status_code == 429:
                        if _retry < 3:
                            _early_progress_write("running", f"⏳ Rate Limit — warte {15*(_retry+1)}s...", page_num * 15)
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
                break
            try:
                page_coins = resp.json() if resp and resp.status_code == 200 else []
            except Exception:
                page_coins = []
            if not isinstance(page_coins, list) or not page_coins:
                break
            all_coins.extend(page_coins)
            if page_num < 4:
                time.sleep(3.0)

        if _early_should_stop():
            _early_progress_write("stopped", "⏹️ Gestoppt")
            return

        if not all_coins:
            _early_progress_write("error", "Keine Daten von CoinGecko")
            return

        # 2. Perp-Daten laden (einmal, dann an fetch_early_movers weitergeben)
        _early_progress_write("running", f"📡 Lade Perp-Daten (Bitget + MEXC)...", 70)
        perp_data = fetch_multi_exchange_perps()

        if _early_should_stop():
            _early_progress_write("stopped", "⏹️ Gestoppt")
            return

        # 3. Analyse — nutze die originale Funktion mit vorgeladenen Daten
        _early_progress_write("running", f"🔍 Analysiere {len(all_coins)} Coins...", 85)
        result = fetch_early_movers(_prefetched_perps=perp_data)

        if _early_should_stop():
            _early_progress_write("stopped", "⏹️ Gestoppt")
            return

        # Ergebnis in Cache-File speichern
        import json as _j
        try:
            with open("/tmp/alpha_early_results.json", "w") as f:
                _j.dump(result, f, default=str)
        except Exception:
            pass

        _early_progress_write("done", f"✅ Fertig — {result.get('stats', {}).get('volume_spikes', 0)} Volume Spikes", 100)
    except Exception as e:
        _early_progress_write("error", f"Fehler: {e}")

def _early_results_load():
    """Lädt Early Movers Ergebnisse aus dem Cache-File."""
    try:
        import json as _j
        with open("/tmp/alpha_early_results.json", "r") as f:
            return _j.load(f)
    except Exception:
        return None


@st.cache_data(ttl=120)
def fetch_early_movers(_prefetched_perps=None):
    """
    🔥 Early Movers Scanner V2.0 — Multi-Exchange (Bitget + MEXC)

    5 Strategien um die nächsten 10x-Coins früh zu finden:
    1. Volume Spike Detector: Vol/MCap anomal hoch, Preis noch nicht explodiert
    2. Micro-Cap Momentum: $1M-$50M MCap, erste Bewegung
    3. Whale Accumulation: OI steigt stark aber Preis noch stabil = große Trader positionieren sich
    4. Funding Rate Flip: FR war negativ (alle shorten) → wird positiv = Squeeze kommt
    5. Narrative Tracker: Sektor-Performance & Nachzügler

    Args:
        _prefetched_perps: Optionale vorgeladene Perp-Daten (vermeidet Doppel-Fetch)

    Returns: dict mit Listen für jede Kategorie
    """
    all_coins = _fetch_coingecko_markets(pages=4)
    if not all_coins:
        return {"volume_spikes": [], "micro_caps": [], "whale_acc": [], "narratives": {}, "recently_listed": [], "stats": {"error": "Keine Daten"}}

    # Multi-Exchange Perp-Daten (Bitget + MEXC) — nutze prefetched wenn vorhanden
    perp_data = _prefetched_perps if _prefetched_perps is not None else fetch_multi_exchange_perps()

    # BTC Benchmark
    btc_7d = 0
    for c in all_coins:
        if c.get("id") == "bitcoin":
            btc_7d = c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d") or 0
            break

    # ── FIX 8: Social Sentiment via CoinGecko Trending (free) ──
    trending_ids = set()
    try:
        import requests as _req
        _tr_resp = _req.get("https://api.coingecko.com/api/v3/search/trending", timeout=15)
        if _tr_resp.status_code == 200:
            _tr_coins = _tr_resp.json().get("coins", [])
            for _tc in _tr_coins:
                _item = _tc.get("item", {})
                _tid = _item.get("id", "")
                if _tid:
                    trending_ids.add(_tid)
    except Exception:
        pass

    # ── FIX 9: Neu Gelistet — Coins mit MCap aber ohne 14d/30d Daten ──
    # (Kein extra API-Call nötig — erkennen wir direkt aus den markets-Daten)
    newly_listed_coins = []

    volume_spikes = []
    micro_caps = []
    whale_accumulations = []
    narrative_coins = {}  # {narrative: [coins]}

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

            # M-7 Audit-Fix: Stablecoins/Wrapped/LSD (vollständige Liste) +
            # Leveraged-Token VOR dem Perp-Lookup überspringen
            if symbol in EXCLUDED_CRYPTO_SYMBOLS_LOCAL or _is_leveraged_token_symbol(symbol):
                continue

            # Multi-Exchange Perp info (Bitget + MEXC)
            # M-5 Audit-Fix: 1000{SYM}-Mapping + Preis-Plausibilität gegen Kollisionen
            perp_info = _lookup_perp_info(perp_data, symbol, price)
            has_perp = bool(perp_info)
            funding_rate = perp_info.get("funding_rate", 0)
            oi_ratio = perp_info.get("oi_ratio", 0)
            oi_is_estimate = bool(perp_info.get("oi_usd_estimate", False))  # H-1
            best_exchange = perp_info.get("best_exchange", "")
            exchanges = perp_info.get("exchanges", [])

            # ── RVOL berechnen (Vol/MCap Ratio als Proxy) ──
            vol_mcap_ratio = (vol_24h / mcap * 100) if mcap > 0 else 0

            # ── Narrative ──
            narrative = CRYPTO_NARRATIVES.get(cid, "")

            # ── FIX 8: Trending Flag ──
            is_trending = cid in trending_ids

            # ── FIX 9: Neu gelistet? (kein 14d/30d Daten = wahrscheinlich neu) ──
            is_newly_listed = (mcap > 0 and vol_24h > 100_000
                               and (change_14d == 0 or change_14d is None) and (change_30d == 0 or change_30d is None)
                               and change_7d != 0)  # Hat 7d aber keine 14d/30d

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
                "IsNewlyListed": is_newly_listed,
            }

            # ── FIX 9: Neu gelistete Coins sammeln ──
            if is_newly_listed and change_7d > 0:
                nl_entry = dict(base_entry)
                nl_entry["Signal"] = "🆕 Neu gelistet"
                nl_entry["NewScore"] = min(100, int(change_7d * 2 + vol_mcap_ratio))
                newly_listed_coins.append(nl_entry)

            # ══════════════════════════════════════════════
            # 1. VOLUME SPIKE DETECTOR (nur BULLISH Volume!)
            # Bedingung: Hohes Volume relativ zu MCap + Preis stabil/steigend
            # Preis nahe 24h-High = Käufer dominant (Akkumulation)
            # Preis nahe 24h-Low + hohes Vol = Abverkauf → SKIP
            # ══════════════════════════════════════════════
            if mcap > 5_000_000 and vol_24h > 200_000:
                # Volume/MCap > 30% ist ungewöhnlich hoch (normal: 5-15%)
                if vol_mcap_ratio > 30 and change_7d < 100:
                    # ── BULLISH-FILTER: Abverkäufe rausfiltern ──
                    # Wenn 24h stark negativ = Distribution, nicht Akkumulation
                    if change_24h < -8:
                        pass  # Skip: Das ist ein Dump, kein Accumulation
                    else:
                        # Price Position im 24h-Range (0 = am Low, 1 = am High)
                        range_24h = high_24h - low_24h
                        if range_24h > 0:
                            price_position = (price - low_24h) / range_24h
                        else:
                            price_position = 0.5
                        # Score berechnen
                        vol_score = min(35, vol_mcap_ratio / 2)  # Max 35 Punkte für Volume

                        momentum_score = 0
                        if 10 < change_7d < 50:
                            momentum_score = 20  # Sweet Spot: Steigt, aber nicht zu schnell
                        elif change_7d > 50:
                            momentum_score = 10  # Schon etwas überhitzt
                        elif 0 < change_7d <= 10:
                            momentum_score = 15  # Ganz am Anfang
                        elif change_7d <= 0:
                            # Fix #8: 7d negativ aber HEUTE pumpt = frischer Reversal
                            # Das sind die BESTEN Early Movers — Boden gefunden, Trend dreht
                            if change_24h > 5 and change_1h > 1:
                                momentum_score = 18  # Starker frischer Pump nach Boden
                            elif change_24h > 3:
                                momentum_score = 10  # Möglicher Reversal
                            else:
                                momentum_score = 0   # Noch kein Reversal-Signal

                        freshness_score = 0
                        if change_24h > 0 and change_1h > 0:
                            freshness_score = 15  # Aktive Buying-Pressure
                        elif change_24h > 0:
                            freshness_score = 10

                        # NEU: Price-Position Score — nahe am High = Käufer dominant
                        position_score = 0
                        if price_position >= 0.7:
                            position_score = 10  # Preis im oberen 30% des Tagesrange = bullish
                        elif price_position >= 0.5:
                            position_score = 5   # Obere Hälfte = leicht bullish
                        # Preis im unteren 30% → 0 Punkte (bearish volume)

                        perp_score = 10 if has_perp else 0
                        if len(exchanges) >= 2:
                            perp_score += 5  # Multi-Exchange Bonus

                        # ── FIX 6: Volume Recency Score ──
                        # 1h stark positiv + Vol hoch = Volume kommt JETZT rein (nicht gestern)
                        recency_score = 0
                        if change_1h > 5 and vol_mcap_ratio > 40:
                            recency_score = 15  # Massiver Live-Zufluss
                        elif change_1h > 2 and vol_mcap_ratio > 30:
                            recency_score = 10  # Aktiver Zufluss
                        elif change_1h > 0 and change_24h > 3:
                            recency_score = 5   # Noch aktiv

                        # ── FIX 8: Trending Bonus ──
                        trending_score = 10 if is_trending else 0

                        total_score = int(vol_score + momentum_score + freshness_score + position_score + perp_score + recency_score + trending_score)

                        # Fix #14: Minimum-Score 30 — darunter ist das Signal zu schwach
                        if total_score >= 30:
                            entry = dict(base_entry)
                            entry["EarlyScore"] = total_score
                            entry["PricePosition"] = round(price_position, 2)
                            entry["RecencyScore"] = recency_score
                            entry["TrendingBonus"] = trending_score
                            if price_position >= 0.7 and change_1h > 3:
                                entry["Signal"] = "🚨 Starker Kaufdruck + Live Pump!"
                            elif price_position >= 0.7:
                                entry["Signal"] = "📊 Akkumulation (Preis nahe High)"
                            elif change_24h > 5:
                                entry["Signal"] = "📈 Volume + positive 24h"
                            else:
                                entry["Signal"] = "📊 Volume Spike (beobachten)"
                            volume_spikes.append(entry)

            # ══════════════════════════════════════════════
            # 2. MICRO-CAP MOMENTUM
            # $1M - $50M MCap, 7d: +5% (FIX 7: gesenkt von +20%), mindestens etwas Volume
            # Fix #9: Bullish-Filter — 24h darf nicht stark negativ sein (Crash ≠ Momentum)
            # Fix #13: Min Volume angepasst (FIX 7: $750K für <+20%, $500K für >+20%)
            # ══════════════════════════════════════════════
            _micro_vol_min = 750_000 if change_7d >= 20 else 500_000  # FIX 7: hohes Momentum braucht MEHR Vol-Validierung
            if 1_000_000 <= mcap <= 50_000_000 and vol_24h > _micro_vol_min:
                if change_7d > 5 and change_24h > -10:
                    # Degen Score
                    degen_score = 0
                    # 7d momentum
                    if change_7d >= 100:
                        degen_score += 30
                    elif change_7d >= 50:
                        degen_score += 25
                    else:
                        degen_score += 15

                    # Volume power
                    if vol_mcap_ratio > 50:
                        degen_score += 25
                    elif vol_mcap_ratio > 20:
                        degen_score += 15
                    else:
                        degen_score += 5

                    # MCap upside (smaller = more potential)
                    if mcap < 5_000_000:
                        degen_score += 25  # Tiny cap = huge upside
                    elif mcap < 15_000_000:
                        degen_score += 20
                    else:
                        degen_score += 10

                    # Perp = institutional attention (Bitget oder MEXC)
                    if has_perp:
                        degen_score += 10
                    if len(exchanges) >= 2:
                        degen_score += 5  # Auf beiden Exchanges = mehr Aufmerksamkeit

                    # Frische Bewegung
                    if change_1h > 2:
                        degen_score += 5

                    # ── FIX 8: Trending Bonus für MicroCaps ──
                    if is_trending:
                        degen_score += 15  # Trending MicroCap = hoch relevant

                    # ── FIX 9: Neu gelistet Bonus ──
                    if is_newly_listed:
                        degen_score += 10  # Frisch gelistet = Early Mover Potential

                    entry = dict(base_entry)
                    entry["DegenScore"] = min(100, degen_score)
                    entry["Signal"] = f"🔥 MicroCap +{change_7d:.0f}% 7d"
                    if is_trending:
                        entry["Signal"] += " 🔥 TRENDING"
                    if is_newly_listed:
                        entry["Signal"] += " 🆕 NEU"
                    micro_caps.append(entry)

            # ══════════════════════════════════════════════
            # 3. WHALE ACCUMULATION (OI steigt, Preis noch stabil)
            # Wenn OI/Vol Ratio hoch ist = Trader halten große Positionen
            # Kombiniert mit positivem Funding = Longs dominieren = Überzeugung
            # ══════════════════════════════════════════════
            if has_perp and mcap > 10_000_000:
                whale_score = 0
                signals = []

                # OI/Volume Ratio hoch = Positionen werden aufgebaut
                # H-1 Audit-Fix: MEXC-OI ohne contractSize ist nur eine Schätzung
                # (holdVol=Kontrakte ≠ Coins) → keine vollen OI-Punkte vergeben
                if oi_is_estimate:
                    if oi_ratio >= 0.8:
                        whale_score += 10  # konservativ: max. Basis-Punkte
                        signals.append(f"📈 OI/Vol ~{oi_ratio:.1f}x (Schätzung, MEXC ohne contractSize)")
                elif oi_ratio >= 3.0:
                    whale_score += 30
                    signals.append(f"🐋 OI/Vol {oi_ratio:.1f}x (stark gehebelt)")
                elif oi_ratio >= 1.5:
                    whale_score += 20
                    signals.append(f"📈 OI/Vol {oi_ratio:.1f}x (Positionen aufgebaut)")
                elif oi_ratio >= 0.8:
                    whale_score += 10

                # Funding Rate positiv = Longs zahlen = bullish Überzeugung
                fr_pct = funding_rate * 100
                if fr_pct >= 0.05:
                    whale_score += 20
                    signals.append(f"💰 FR +{fr_pct:.3f}% (Longs dominant)")
                elif fr_pct >= 0.01:
                    whale_score += 10
                elif fr_pct <= -0.03:
                    # Fix #11: Negatives FR + Preis steigt = MÖGLICHER Squeeze
                    # Aber ohne historische FR können wir keinen echten "Flip" bestätigen
                    if change_24h > 3:
                        whale_score += 20
                        signals.append(f"⚡ FR negativ {fr_pct:.3f}% aber Preis +{change_24h:.1f}% → Squeeze-Potenzial")
                    elif change_1h > 1:
                        whale_score += 12
                        signals.append(f"⚡ FR negativ {fr_pct:.3f}% + 1h-Pump → beobachten")

                # Preis noch in früher Phase (nicht schon 3x)
                if 5 < change_7d < 40:
                    whale_score += 15  # Sweet spot: Bewegung gestartet, nicht überhitzt
                elif change_7d <= 5:
                    whale_score += 20  # Noch am Anfang = bestes Timing!

                # Multi-Exchange Coverage
                if len(exchanges) >= 2:
                    whale_score += 10
                    signals.append(f"🏦 Auf {' + '.join(exchanges)}")

                if whale_score >= 35:
                    entry = dict(base_entry)
                    entry["WhaleScore"] = min(100, whale_score)
                    entry["Signals"] = signals
                    whale_accumulations.append(entry)

            # ══════════════════════════════════════════════
            # 4. NARRATIVE TRACKER
            # Sammle alle Coins nach Sektor
            # ══════════════════════════════════════════════
            if narrative and mcap > 10_000_000:
                if narrative not in narrative_coins:
                    narrative_coins[narrative] = []
                narrative_coins[narrative].append(base_entry)

        except Exception:
            continue

    # ── FIX 9: Newly Listed sortieren ──
    newly_listed_coins.sort(key=lambda x: x.get("NewScore", 0), reverse=True)

    # Sortieren
    volume_spikes.sort(key=lambda x: x.get("EarlyScore", 0), reverse=True)
    micro_caps.sort(key=lambda x: x.get("DegenScore", 0), reverse=True)
    whale_accumulations.sort(key=lambda x: x.get("WhaleScore", 0), reverse=True)

    # ── Narrative Aggregation ──
    narrative_summary = {}
    for narr, coins_list in narrative_coins.items():
        if len(coins_list) < 2:
            continue
        avg_7d = sum(c.get("Change7d", 0) for c in coins_list) / len(coins_list)
        avg_24h = sum(c.get("Change24h", 0) for c in coins_list) / len(coins_list)
        total_vol = sum(c["Vol24h"] for c in coins_list)
        total_mcap = sum(c.get("MCap", 0) for c in coins_list)

        # Fix #12: Nachzügler nur bei bullishem Sektor (avg_7d > 0)
        # Bei negativem Sektor gibt's keine "Aufholkandidaten" — alles fällt
        if avg_7d > 2:
            laggards = [c for c in coins_list if c.get("Change7d", 0) < avg_7d * 0.5 and c.get("Change7d", 0) > -10]
        else:
            laggards = []  # Kein Aufholpotential wenn Sektor negativ/seitwärts
        # Top-Performer
        leaders = sorted(coins_list, key=lambda x: x["Change7d"], reverse=True)[:3]

        narrative_summary[narr] = {
            "avg_7d": round(avg_7d, 2),
            "avg_24h": round(avg_24h, 2),
            "total_vol": total_vol,
            "total_mcap": total_mcap,
            "count": len(coins_list),
            "leaders": leaders,
            "laggards": laggards[:5],
            "coins": coins_list,
        }

    # Sort narratives by 7d performance
    narrative_summary = dict(sorted(narrative_summary.items(), key=lambda x: x[1]["avg_7d"], reverse=True))

    stats = {
        "total_coins": len(all_coins),
        "volume_spikes": len(volume_spikes),
        "micro_caps": len(micro_caps),
        "whale_acc": len(whale_accumulations),
        "narratives": len(narrative_summary),
        "recently_listed": len(newly_listed_coins),
        "trending_coins": len(trending_ids),
        "btc_7d": btc_7d,
        "perps_mexc": len(fetch_mexc_funding_oi()),
        "perps_bitget": len(fetch_bitget_funding_oi()),
    }

    return {
        "volume_spikes": volume_spikes[:30],
        "micro_caps": micro_caps[:30],
        "whale_acc": whale_accumulations[:25],
        "recently_listed": newly_listed_coins[:20],
        "narratives": narrative_summary,
        "stats": stats,
    }


# ── M-7 Audit-Fix: Stablecoins / Wrapped / LSD / Gold-Token — keine direktionalen Mover ──
# SYNC mit api.py EXCLUDED_CRYPTO_SYMBOLS (dort gepflegt, hier gespiegelt) + lokale
# Ergänzung CBBTC (cbBTC). Identische Kopie in bg_service.py — Änderungen in beiden nachziehen.
EXCLUDED_CRYPTO_SYMBOLS_LOCAL = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USDS", "USDD",
    "USDP", "PYUSD", "FRAX", "LUSD", "GUSD", "DOLA", "SUSD", "EUSD", "USDL",
    "USDY", "USDX", "EURC", "EUROC", "WBTC", "CBTC", "TBTC", "LBTC", "WETH",
    "WBNB", "STETH", "WSTETH", "RETH", "CBETH", "WBETH", "WEETH", "EZETH",
    "METH", "RSETH", "SFRXETH", "FRXETH", "PAXG", "XAUT",
    "CBBTC",  # cbBTC (Coinbase Wrapped BTC) — Ergänzung zur api-Liste
}


def _is_leveraged_token_symbol(symbol):
    """M-7 Audit-Fix: Leveraged-Token erkennen (kein Spot-Mover, gehört nicht in Scans).

    Erkennt: 3L/3S/4L/4S/5L/5S-Suffixe, UP/DOWN-Endungen (Binance Leveraged Tokens)
    und BULL/BEAR-Token. Konservative Mindestlängen, damit echte Ticker wie
    'JUP' (endet auf UP) oder ein Coin namens 'BULL' nicht gefiltert werden.
    SYNC: identische Kopie in bg_service.py.
    """
    sym = (symbol or "").upper().strip()
    if len(sym) >= 4 and sym[-2:] in ("3L", "3S", "4L", "4S", "5L", "5S"):
        return True
    if sym.endswith("UP") and len(sym) >= 5:
        return True
    if sym.endswith("DOWN") and len(sym) >= 6:
        return True
    if (sym.endswith("BULL") or sym.endswith("BEAR")) and len(sym) >= 6:
        return True
    return False


def _btc_div_signal_status(exh_score, close_pos, change_1h, change_24h, btc_weak):
    """H-7 Audit-Fix: Einheitliche, pure Timing-/Gate-Logik für BTC-Divergenz-Shorts.

    SYNC: Identische Implementierung in scanner.py UND bg_service.py — Änderungen
    immer in beiden Dateien nachziehen. (Shared-Home wäre modules/scorers, gehört
    aber einem anderen Team; Cross-Import hat Seiteneffekte: Logging/PID/Streamlit.)

    Regeln (konsolidiert mit der api.py-Variante):
    - "JETZT"-Signale erst ab ExhScore >= 65 (vorher bg_service: 55/50/45) UND nur
      bei BTC-Schwäche (btc_weak=True). BTC stark → bestenfalls "BEOBACHTEN".
    - Dieser Pfad liefert KEINEN Entry/Stop/TP → auch "JETZT SHORTEN" ist nur ein
      Beobachtungssignal und trägt den expliziten Hinweis "kein definierter Stop".

    Returns:
        (timing: str, timing_quality: int, btc_gate: bool)
        timing_quality: 5=JETZT SHORTEN, 4=JETZT, 3=BEREIT, 2=WATCH/BEOBACHTEN,
                        0=ZU FRÜH, -1=ZU SPÄT. btc_gate=True nur bei BTC-Schwäche.
    """
    no_stop_note = " · kein Einstiegssignal, kein definierter Stop"
    cp = close_pos if close_pos is not None else 0.5
    price_near_high = cp >= 0.70
    price_mid_range = 0.40 <= cp < 0.70
    price_near_low = cp < 0.40
    btc_gate = bool(btc_weak)

    if price_near_low and change_24h < -3:
        return ("⚫ ZU SPÄT — Preis schon {:.0f}% vom High, Move gelaufen".format((1 - cp) * 100), -1, btc_gate)

    if not btc_gate:
        # H-7: BTC auf den Makro-Zeitfenstern stark → KEIN Short-Timing vergeben,
        # egal wie hoch Divergenz/ExhScore sind. Nur beobachten.
        if exh_score >= 50:
            return ("👁️ BEOBACHTEN (BTC stark — kein Short-Timing)", 2, False)
        return ("⚪ ZU FRÜH", 0, False)

    if exh_score >= 65 and price_near_high and change_1h < -1.5:
        return ("🔴 SHORT-KONTEXT — Nahe High, 1h kippt ({:+.1f}%){}".format(change_1h, no_stop_note), 5, True)
    if exh_score >= 65 and price_near_high and change_1h < -0.5:
        return ("🟠 SHORT-KONTEXT — Nahe High, erste Schwäche (1h {:+.1f}%){}".format(change_1h, no_stop_note), 4, True)
    if exh_score >= 65 and price_near_high:
        return ("🟡 BEREIT — Nahe High, warte auf rote 1h-Kerze", 3, True)
    if exh_score >= 65 and price_mid_range:
        return ("🟡 BEREIT — Warte auf Bounce Richtung High für besseren Entry", 3, True)
    if exh_score >= 50 and price_near_high and change_1h < -2.0:
        # Vorher "JETZT" schon ab Score 50/55 — konsolidiert: unter 65 kein JETZT mehr.
        # WICHTIG: Wort "JETZT" hier vermeiden — UI matcht per Substring!
        return ("🟡 BEREIT — Starker 1h-Dump ({:+.1f}%), ExhScore unter Schwelle 65".format(change_1h), 3, True)
    if exh_score >= 50 and price_mid_range and change_1h < 0:
        return ("🟠 WATCHLIST — Mittlerer Bereich, könnte noch bounzen", 2, True)
    if exh_score >= 65 and price_near_low:
        return ("⚫ ZU SPÄT — Preis schon {:.0f}% vom High gefallen".format((1 - cp) * 100), -1, True)
    if exh_score >= 50:
        return ("🟠 WATCHLIST — Noch nicht reif", 2, True)
    return ("⚪ ZU FRÜH", 0, True)


def _lookup_perp_info(perp_data, symbol, cg_price=None):
    """M-5 Audit-Fix: Perp-Lookup mit 1000{SYM}-Mapping + Preis-Plausibilität.

    Memecoins werden auf Perp-Börsen oft als 1000PEPE/10000SATS gelistet
    (Referenz: api.py-Variante). Zusätzlich Plausi-Check: Weicht der Perp-Preis
    (nach Multiplier) mehr als Faktor 3 vom CoinGecko-Preis ab, ist es eine
    Symbol-Kollision (anderes Asset, gleicher Ticker) → kein Match, FR/OI leer.
    """
    if not perp_data or not symbol:
        return {}
    try:
        cg_price = float(cg_price) if cg_price else 0.0
    except (TypeError, ValueError):
        cg_price = 0.0
    for key, mult in ((symbol, 1.0), (f"1000{symbol}", 1000.0), (f"10000{symbol}", 10000.0)):
        info = perp_data.get(key)
        if not info:
            continue
        try:
            perp_price = float(info.get("last_price") or 0)
        except (TypeError, ValueError):
            perp_price = 0.0
        if cg_price > 0 and perp_price > 0:
            expected = cg_price * mult
            ratio = perp_price / expected if expected > 0 else 0.0
            if ratio > 3.0 or ratio < (1.0 / 3.0):
                continue  # Faktor > 3 daneben → Kollision, kein Match
        return info
    return {}


@st.cache_data(ttl=300)
def fetch_mexc_funding_oi():
    """
    MEXC Perpetual Futures: Funding Rate + Open Interest für alle Contracts.
    Ein einziger API-Call liefert ~847 Contracts.
    Returns: dict { 'BTC': {'funding_rate': 0.0001, 'hold_vol': 628M, 'volume24': 148M, 'oi_ratio': 4.2}, ... }
    """
    try:
        resp = requests.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            return {}
        return _parse_mexc_perp_tickers(data.get("data", []))
    except Exception:
        return {}


def _parse_mexc_perp_tickers(items):
    """H-1 Audit-Fix (pure, testbar): MEXC /contract/ticker Items → {BASE: {...}}.

    Einheiten-Korrektur:
    - Volumen: `amount24` ist bereits 24h-Turnover in USDT (wie bei
      detect_active_pumps/_fetch_mexc_perp_rows). `volume24` sind KONTRAKTE —
      volume24*lastPrice war falsch (Kontrakt ≠ 1 Coin).
    - OI: `holdVol` sind Kontrakte. Mit `contractSize` (falls im Response) →
      echtes OI in USD. Ohne contractSize nur Schätzung → oi_usd_estimate=True,
      nachgelagerte Schwellen behandeln das konservativ.
    """
    result = {}
    for t in items or []:
        symbol = t.get("symbol", "")
        if not symbol.endswith("_USDT"):
            continue
        base = symbol.replace("_USDT", "")
        try:
            hold_vol = float(t.get("holdVol") or 0)
            volume24 = float(t.get("volume24") or 0)
            amount24 = float(t.get("amount24") or 0)
            contract_size = float(t.get("contractSize") or 0)
            fr = float(t.get("fundingRate") or 0)
            last_price = float(t.get("lastPrice") or t.get("last") or 0)
        except (TypeError, ValueError):
            continue
        # H-1: amount24 = 24h-Volumen in USDT (direkt vom Exchange, korrekt)
        if amount24 > 0:
            vol_usdt = amount24
            vol_estimate = False
        else:
            vol_usdt = volume24 * last_price if last_price > 0 else volume24
            vol_estimate = True
        # H-1: holdVol = Kontrakte → USD nur mit contractSize exakt
        if contract_size > 0 and last_price > 0:
            oi_usdt = hold_vol * contract_size * last_price
            oi_estimate = False
        else:
            oi_usdt = hold_vol * last_price if last_price > 0 else hold_vol
            oi_estimate = True
        oi_ratio = (oi_usdt / vol_usdt) if vol_usdt > 0 else 0
        result[base] = {
            "funding_rate": fr,
            "hold_vol": hold_vol,
            "volume24": vol_usdt,  # USDT-basiert (amount24)
            "oi_usdt": oi_usdt,
            "oi_ratio": round(oi_ratio, 2),
            "oi_usd_estimate": oi_estimate,
            "vol_usd_estimate": vol_estimate,
            "last_price": last_price,
        }
    return result


@st.cache_data(ttl=300)
def fetch_bitget_funding_oi():
    """
    Bitget Perpetual Futures: Funding Rate + Open Interest für alle USDT-Contracts.
    Ein einziger API-Call liefert ~540 Contracts.
    Returns: dict { 'BTC': {'funding_rate': -0.000022, 'hold_amount': 27452, 'volume24_usdt': 2.2B, 'oi_ratio': 1.5, 'change24h': 0.018}, ... }
    """
    try:
        resp = requests.get("https://api.bitget.com/api/v2/mix/market/tickers",
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
            hold_amount = float(t.get("holdingAmount") or 0)  # OI in base currency
            vol_usdt = float(t.get("usdtVolume") or 0)
            last_price = float(t.get("lastPr") or 0)
            change_24h = float(t.get("change24h") or 0)

            # OI in USDT
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


@st.cache_data(ttl=300)
def fetch_multi_exchange_perps():
    """
    Multi-Exchange Perpetual Data: MEXC + Bitget kombiniert.
    Gibt für jeden Coin das beste Exchange-Match zurück.
    Returns: dict { 'BTC': {
        'exchanges': ['MEXC', 'Bitget'],
        'best_exchange': 'Bitget',  # höchstes Volume
        'funding_rate': -0.000022,  # vom besten Exchange
        'oi_ratio': 1.5,
        'oi_usdt': ...,
        'volume24_usdt': ...,
        'mexc': {...}, 'bitget': {...}
    }, ... }
    """
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

        # Bestimme bestes Exchange (höchstes Volume)
        mexc_vol = m.get("volume24", 0) if m else 0
        bitget_vol = b.get("volume24_usdt", 0) if b else 0

        if bitget_vol >= mexc_vol and b:
            best = "Bitget"
            best_fr = b.get("funding_rate", 0)
            best_oi_ratio = b.get("oi_ratio", 0)
            best_oi_usdt = b.get("oi_usdt", 0)
            best_vol = bitget_vol
            best_oi_estimate = False  # Bitget holdingAmount = Base-Coins → echtes OI
            best_last_price = b.get("last_price", 0)
        elif m:
            best = "MEXC"
            best_fr = m.get("funding_rate", 0)
            best_oi_ratio = m.get("oi_ratio", 0)
            best_oi_usdt = m.get("oi_usdt", m.get("hold_vol", 0))
            best_vol = mexc_vol
            best_oi_estimate = bool(m.get("oi_usd_estimate", True))  # H-1
            best_last_price = m.get("last_price", 0)
        else:
            continue

        result[sym] = {
            "exchanges": exchanges,
            "best_exchange": best,
            "funding_rate": best_fr,
            "oi_ratio": best_oi_ratio,
            "oi_usdt": best_oi_usdt,
            "oi_usd_estimate": best_oi_estimate,  # H-1: True = OI nur geschätzt
            "last_price": best_last_price,  # M-5: für Preis-Plausi-Check
            "volume24_usdt": max(mexc_vol, bitget_vol),
            "mexc": m,
            "bitget": b,
        }

    return result


# =============================================================================
# ORB — OPENING RANGE BREAKOUT SCANNER
# =============================================================================

@st.cache_data(ttl=60)
def fetch_orb_scanner(poly_key):
    """
    🔔 Opening Range Breakout (ORB) Scanner

    Automatisch aktiv um 9:45 ET (15 Min nach Market Open).

    Flow:
    1. Vorfilter: Grouped Daily von gestern → Gap Up >2% oder RVOL >1.5
    2. 5-Min-Candles der ersten 15 Min holen → Opening Range (High/Low)
    3. Aktuelle 5-Min-Candles prüfen → Breakout über/unter OR?
    4. Scoring: Gap-Size, RVOL, OR-Width, Breakout-Stärke, VWAP-Position

    Returns:
        dict mit {candidates, breakouts, stats, or_phase}
    """
    import pytz
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)

    result = {
        "candidates": [],
        "breakouts": [],
        "stats": {"scanned": 0, "candidates": 0, "breakouts": 0},
        "or_phase": "closed",  # "pre_open", "building", "active", "closed"
        "or_end_time": None,
        "market_time": now_et.strftime("%H:%M ET"),
    }

    # ── Phase-Detection ──
    hour, minute = now_et.hour, now_et.minute
    time_val = hour * 60 + minute  # Minuten seit Mitternacht

    market_open = 9 * 60 + 30   # 9:30
    or_end = 9 * 60 + 45        # 9:45
    or_active_end = 11 * 60     # 11:00 — ORB-Breakouts nach 11 Uhr verlieren Edge
    market_close = 16 * 60      # 16:00

    # Minuten seit Market Open (für RVOL-at-Time Normalisierung)
    mins_since_open = max(1, time_val - market_open)
    total_market_mins = 390  # 6.5h Handelstag

    weekday = now_et.weekday()
    if weekday >= 5:  # Wochenende
        result["or_phase"] = "weekend"
        return result

    # Fix #8: US-Feiertage erkennen (NYSE geschlossen)
    _us_holidays = {
        (1, 1),    # New Year's Day
        (1, 20),   # MLK Day (approx – 3. Montag Januar)
        (2, 17),   # Presidents' Day (approx – 3. Montag Februar)
        (4, 18),   # Good Friday (variiert – hier 2025 Näherung)
        (5, 26),   # Memorial Day (approx – letzter Montag Mai)
        (6, 19),   # Juneteenth
        (7, 4),    # Independence Day
        (9, 1),    # Labor Day (approx – 1. Montag September)
        (11, 27),  # Thanksgiving (approx – 4. Donnerstag November)
        (12, 25),  # Christmas
    }
    _today_md = (now_et.month, now_et.day)
    if _today_md in _us_holidays:
        result["or_phase"] = "holiday"
        return result

    # Fix #1: Korrekte Phase-Reihenfolge (closed > expired > active > building > pre_open)
    if time_val >= market_close:
        result["or_phase"] = "closed"
        return result
    elif time_val >= or_active_end:
        result["or_phase"] = "expired"
        return result
    elif time_val < market_open:
        result["or_phase"] = "pre_open"
        return result
    elif time_val < or_end:
        result["or_phase"] = "building"
        mins_left = or_end - time_val
        result["or_end_time"] = f"{mins_left} Min bis OR fertig"
        return result

    # ── Phase "active" — OR ist fertig, suche Breakouts ──
    result["or_phase"] = "active"
    today_str = now_et.strftime("%Y-%m-%d")
    yesterday = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
    # Freitag → Donnerstag Skip
    if weekday == 0:
        yesterday = (now_et - timedelta(days=3)).strftime("%Y-%m-%d")

    # ── Schritt 1: Vorfilter — Gapper + Volume Stocks finden ──
    # Grouped Daily von gestern für Prev-Close
    prev_data = fetch_grouped_daily(poly_key, yesterday)
    if not prev_data:
        # Versuche Tag davor
        day_before = (now_et - timedelta(days=2)).strftime("%Y-%m-%d")
        if weekday == 0:
            day_before = (now_et - timedelta(days=4)).strftime("%Y-%m-%d")
        prev_data = fetch_grouped_daily(poly_key, day_before)

    if not prev_data:
        return result

    # Grouped Daily von HEUTE für aktuelle Daten
    today_data = fetch_grouped_daily(poly_key, today_str)

    # ── Common Stock Whitelist für ORB (nur echte Aktien) ──
    _orb_cs = COMMON_STOCK_TICKERS
    if not _orb_cs:
        # Datei-Cache (24h)
        _CS_FILE_ORB = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(_CS_FILE_ORB) and (time.time() - os.path.getmtime(_CS_FILE_ORB)) < 86400:
                with open(_CS_FILE_ORB, "r") as _cf:
                    _orb_cs = set(json.load(_cf))
        except Exception:
            pass
    if not _orb_cs:
        try:
            _orb_cs, _ = _load_common_stock_tickers_direct(poly_key)
            if _orb_cs:
                with open("/tmp/cs_tickers_cache.json", "w") as _cf:
                    json.dump(list(_orb_cs), _cf)
        except Exception:
            _orb_cs = set()

    # Kandidaten: Gap > 2% ODER Today Volume > 1.5x Average
    candidates = []
    for ticker, prev in prev_data.items():
        # Filter: nur echte Aktien (Common Stock Whitelist)
        if len(ticker) > 5 or "." in ticker:
            continue
        if _orb_cs and ticker.upper() not in _orb_cs:
            continue
        elif not _orb_cs and is_etf_or_etp(ticker):
            continue  # Fallback wenn CS-Liste nicht geladen
        prev_close = prev.get("c", 0)
        if prev_close < 10 or prev_close > 2000:  # Min $10 (Spread-Qualität), Mega-Caps OK
            continue
        prev_vol = prev.get("v", 0)
        if prev_vol < 500000:  # Min Liquidität
            continue

        # Heutige Daten
        today = today_data.get(ticker, {}) if today_data else {}
        today_open = today.get("o", 0)
        today_vol = today.get("v", 0)
        today_high = today.get("h", 0)
        today_low = today.get("l", 0)
        today_close = today.get("c", 0)

        if today_open <= 0:
            continue

        # Gap berechnen
        gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close > 0 else 0

        # Fix #2: RVOL-at-Time — normalisiert auf Tageszeit
        # Um 10:00 ET (30 Min nach Open) hast du ~15% des Tagesvolumens
        # Ohne Normalisierung wäre RVOL immer zu niedrig am Morgen
        time_fraction = mins_since_open / total_market_mins
        # Volumen-Verteilung ist U-förmig (viel am Open/Close, wenig Mittags)
        # Approximation: erste 30 Min = ~20% des Tagesvolumens
        if mins_since_open <= 30:
            expected_vol_fraction = 0.20 * (mins_since_open / 30)
        elif mins_since_open <= 60:
            expected_vol_fraction = 0.20 + 0.10 * ((mins_since_open - 30) / 30)
        else:
            expected_vol_fraction = 0.30 + 0.70 * ((mins_since_open - 60) / (total_market_mins - 60))
        expected_vol_fraction = max(0.01, expected_vol_fraction)
        expected_vol = prev_vol * expected_vol_fraction
        rvol = today_vol / expected_vol if expected_vol > 0 else 0

        # Vorfilter: Gap > 2% ODER RVOL > 1.5
        # Auch Gap Down > -2% (Short-ORB)
        if abs(gap_pct) < 2 and rvol < 1.5:
            continue

        candidates.append({
            "ticker": ticker,
            "prev_close": round(prev_close, 2),
            "open": round(today_open, 2),
            "current": round(today_close, 2) if today_close > 0 else round(today_open, 2),
            "high": round(today_high, 2),
            "low": round(today_low, 2),
            "gap_pct": round(gap_pct, 2),
            "rvol": round(rvol, 2),
            "volume": today_vol,
        })

    # Top 40 nach abs(gap) + RVOL sortieren
    candidates.sort(key=lambda x: abs(x["gap_pct"]) * 0.6 + min(x["rvol"], 5) * 0.4, reverse=True)
    candidates = candidates[:40]
    result.get("stats", {})["scanned"] = len(prev_data)
    result.get("stats", {})["candidates"] = len(candidates)

    # ── Schritt 2: 5-Min-Candles holen → Opening Range berechnen ──
    breakouts = []

    for cand in candidates:
        ticker = cand.get("ticker", "")
        try:
            # 5-Min Candles für heute
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{today_str}/{today_str}"
            resp = rate_limited_get(url, params={
                "apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 50000
            }, timeout=10)

            if resp.status_code != 200:
                continue

            bars = resp.json().get("results", [])
            if not bars or len(bars) < 2:  # Min 2 Candles (OR braucht nur 9:30+9:35)
                continue

            # Fix #3: Pre-Market Bars rausfiltern — nur Regular Hours (9:30+ ET)
            # Polygon Timestamp ist in Millisekunden UTC
            market_open_ms = int(now_et.replace(hour=9, minute=30, second=0, microsecond=0).timestamp() * 1000)
            or_end_ms = int(now_et.replace(hour=9, minute=45, second=0, microsecond=0).timestamp() * 1000)
            bars = [b for b in bars if b.get("t", 0) >= market_open_ms]
            if len(bars) < 2:
                continue

            # Opening Range = Bars zwischen 9:30 und 9:45 ET
            or_bars = [b for b in bars if b.get("t", 0) < or_end_ms]
            if not or_bars:
                or_bars = bars[:3]  # Fallback
            if not or_bars:
                continue  # Keine Bars verfügbar → Skip

            or_high = max(b["h"] for b in or_bars)
            or_low = min(b["l"] for b in or_bars)
            or_range = or_high - or_low
            # FIX: OR-Range als % vom Opening-Preis (nicht vom Low)
            _or_open = or_bars[0].get("o", or_low)
            or_range_pct = (or_range / _or_open * 100) if _or_open > 0 else 0

            # FIX: Mindest-Volumen während OR — illiquide ORs filtern
            or_vol = sum(b.get("v", 0) for b in or_bars)
            if or_vol < 30000:  # Min 30K Shares in OR-Phase
                continue

            # VWAP berechnen (volumengewichteter Durchschnittspreis)
            total_vwap_num = 0
            total_vol = 0
            for b in bars:
                typical_price = (b["h"] + b["l"] + b["c"]) / 3
                total_vwap_num += typical_price * b.get("v", 0)
                total_vol += b.get("v", 0)
            vwap = total_vwap_num / total_vol if total_vol > 0 else (or_high + or_low) / 2

            # Aktuelle Candle (letzte verfügbare)
            latest = bars[-1]
            current_price = latest["c"]
            current_high = latest["h"]

            # ── Breakout Detection ──
            # Nutze post-OR Bars UND aktuellen Preis (auch wenn post-OR Bars noch nicht da)
            breakout_dir = None
            breakout_pct = 0

            post_or = [b for b in bars if b.get("t", 0) >= or_end_ms]
            bars_above_or = sum(1 for b in post_or if b["c"] > or_high)
            bars_below_or = sum(1 for b in post_or if b["c"] < or_low)

            # Auch den aktuellen Preis aus Grouped Daily nutzen (robuster als nur 5-Min Bars)
            _daily_price = cand.get("current", 0) or current_price

            if current_price > or_high and bars_above_or >= 2:
                breakout_dir = "LONG"
                breakout_pct = (current_price - or_high) / or_high * 100
            elif current_price < or_low and bars_below_or >= 2:
                breakout_dir = "SHORT"
                breakout_pct = (or_low - current_price) / or_low * 100
            elif current_price > or_high and bars_above_or >= 1:
                breakout_dir = "LONG"
                breakout_pct = (current_price - or_high) / or_high * 100
            elif current_price < or_low and bars_below_or >= 1:
                breakout_dir = "SHORT"
                breakout_pct = (or_low - current_price) / or_low * 100
            # Fallback: Kein post-OR Bar aber aktueller Preis bricht aus
            # (wichtig direkt nach 9:45 wenn post-OR Bars noch nicht verfügbar)
            elif _daily_price > or_high * 1.002:  # 0.2% über OR High = Breakout
                breakout_dir = "LONG"
                breakout_pct = (_daily_price - or_high) / or_high * 100
            elif _daily_price < or_low * 0.998:  # 0.2% unter OR Low = Breakdown
                breakout_dir = "SHORT"
                breakout_pct = (or_low - _daily_price) / or_low * 100

            # Breakout-Bestätigung Flag
            _bo_confirmed = (breakout_dir == "LONG" and bars_above_or >= 2) or \
                            (breakout_dir == "SHORT" and bars_below_or >= 2)

            if not breakout_dir:
                # Kein Breakout — als Kandidat speichern
                cand["or_high"] = round(or_high, 2)
                cand["or_low"] = round(or_low, 2)
                cand["or_range_pct"] = round(or_range_pct, 2)
                cand["status"] = "IN RANGE"
                continue

            # ── ORB Scoring (0-100) ──
            score = 0
            factors = []

            # FIX: RVOL direkt vom Kandidaten lesen (vorher Variable aus Pre-Filter Loop)
            rvol = cand.get("rvol", 0)

            # 1. Gap-Richtung aligned mit Breakout (max 20)
            if (cand.get("gap_pct", 0) > 0 and breakout_dir == "LONG") or \
               (cand.get("gap_pct", 0) < 0 and breakout_dir == "SHORT"):
                gap_score = min(20, abs(cand.get("gap_pct", 0)) * 3)
                score += gap_score
                factors.append(f"Gap aligned ({cand.get('gap_pct', 0):+.1f}%)")
            elif abs(cand.get("gap_pct", 0)) < 1:
                # Fix #6: Kein/kleiner Gap — Stock ist hier wegen RVOL
                # Flat Open + hohes Volume = institutionelles Interesse
                if rvol >= 2:
                    score += 10
                    factors.append(f"Flat Open + High Vol ({rvol:.1f}x)")
                else:
                    score += 5
                    factors.append(f"Kein Gap ({cand.get('gap_pct', 0):+.1f}%)")
            elif abs(cand.get("gap_pct", 0)) > 2:
                # Gegen-Breakout = Fade, schwächer
                score += 5
                factors.append(f"Counter-Gap ({cand.get('gap_pct', 0):+.1f}%)")

            # 2. RVOL (max 20)
            if rvol >= 3:
                score += 20
                factors.append(f"RVOL {rvol:.1f}x 🔥")
            elif rvol >= 2:
                score += 15
                factors.append(f"RVOL {rvol:.1f}x")
            elif rvol >= 1.5:
                score += 10
                factors.append(f"RVOL {rvol:.1f}x")
            else:
                score += 5
                factors.append(f"RVOL {rvol:.1f}x (schwach)")

            # 3. OR-Width: Enge OR = besserer Breakout (max 15)
            # Ideal: 1-3% Range
            if 0.5 <= or_range_pct <= 2:
                score += 15
                factors.append(f"Enge OR ({or_range_pct:.1f}%)")
            elif 2 < or_range_pct <= 4:
                score += 10
                factors.append(f"Moderate OR ({or_range_pct:.1f}%)")
            elif or_range_pct < 0.5:
                score += 5
                factors.append(f"Sehr enge OR ({or_range_pct:.1f}%)")
            else:
                score += 3
                factors.append(f"Weite OR ({or_range_pct:.1f}%)")

            # 4. Breakout-Extension über OR (max 15)
            # Zu weit = schon überdehnt, zu nah = noch nicht bestätigt
            if 0.3 <= breakout_pct <= 1.5:
                score += 15
                factors.append(f"Clean Breakout (+{breakout_pct:.1f}%)")
            elif breakout_pct < 0.3:
                score += 8
                factors.append(f"Knapper BO (+{breakout_pct:.1f}%)")
            elif breakout_pct <= 3:
                score += 10
                factors.append(f"Extended (+{breakout_pct:.1f}%)")
            else:
                score += 3
                factors.append(f"Überdehnt (+{breakout_pct:.1f}%)")

            # 5. VWAP-Position (max 15)
            if breakout_dir == "LONG" and current_price > vwap:
                score += 15
                factors.append("Über VWAP ✅")
            elif breakout_dir == "SHORT" and current_price < vwap:
                score += 15
                factors.append("Unter VWAP ✅")
            else:
                score += 3
                factors.append("VWAP gegen Breakout ⚠️")

            # 6. Volumen-Bestätigung im Breakout-Bar (max 15)
            # Fix #10: Nutze die erste Candle die über OR bricht, nicht max() aller Bars
            or_avg_vol = sum(b.get("v", 0) for b in or_bars) / len(or_bars) if or_bars else 1
            post_or_bars = [b for b in bars if b.get("t", 0) >= or_end_ms]
            breakout_bar = None
            if post_or_bars:
                # Finde die ERSTE Candle die über/unter OR schließt
                for b in post_or_bars:
                    if breakout_dir == "LONG" and b["c"] > or_high:
                        breakout_bar = b
                        break
                    elif breakout_dir == "SHORT" and b["c"] < or_low:
                        breakout_bar = b
                        break
                if breakout_bar:
                    breakout_vol = breakout_bar.get("v", 0)
                else:
                    breakout_vol = post_or_bars[-1].get("v", 0)
                vol_ratio = breakout_vol / or_avg_vol if or_avg_vol > 0 else 0
                if vol_ratio >= 1.5:
                    score += 15
                    factors.append(f"Vol-Surge {vol_ratio:.1f}x")
                elif vol_ratio >= 1.0:
                    score += 10
                    factors.append(f"Vol OK {vol_ratio:.1f}x")
                else:
                    score += 3
                    factors.append(f"Vol schwach {vol_ratio:.1f}x")

            # Fix #4: Breakout-Bestätigung Bonus/Malus
            if _bo_confirmed:
                score += 5
                factors.append("Bestätigt (2+ Bars) ✅")
            else:
                score -= 5
                factors.append("Unbestätigt (1 Bar) ⚠️")

            # ── FIX: Fakeout-Detection (Crabel/Fisher Best Practice) ──
            # Wenn Preis nach Breakout zurück in OR fällt → Fakeout
            _is_fakeout = False
            if post_or_bars and breakout_bar:
                _bars_after_bo = [b for b in post_or_bars
                                  if b.get("t", 0) > breakout_bar.get("t", 0)]
                if breakout_dir == "LONG":
                    # Fakeout: eine spätere Candle schließt UNTER OR-High
                    _fakeout_bars = sum(1 for b in _bars_after_bo if b["c"] < or_high)
                    if _fakeout_bars >= 2:
                        _is_fakeout = True
                elif breakout_dir == "SHORT":
                    _fakeout_bars = sum(1 for b in _bars_after_bo if b["c"] > or_low)
                    if _fakeout_bars >= 2:
                        _is_fakeout = True

            if _is_fakeout:
                score -= 20
                factors.append("⛔ Fakeout — Preis zurück in OR")

            # ── FIX: Time-Decay — späte Breakouts verlieren Edge ──
            # Ideal: 9:45-10:15 (0-30 Min nach OR). Nach 10:30 sinkt Wahrscheinlichkeit
            _mins_after_or = time_val - or_end  # Minuten nach OR-Ende
            if _mins_after_or <= 30:
                pass  # Optimal — kein Abzug
            elif _mins_after_or <= 60:
                score -= 5
                factors.append(f"Spät ({_mins_after_or}min nach OR)")
            elif _mins_after_or <= 90:
                score -= 10
                factors.append(f"Spät ({_mins_after_or}min nach OR) ⚠️")
            else:
                score -= 15
                factors.append(f"Sehr spät ({_mins_after_or}min nach OR) ⚠️")

            # ── FIX: Überdehnt-Filter — >5% Extension = gefährlich ──
            if breakout_pct > 5:
                score -= 15
                factors.append(f"⚠️ Stark überdehnt ({breakout_pct:.1f}%)")
            elif breakout_pct > 3:
                score -= 5

            # Score Clamp
            score = max(0, min(100, score))

            # Rating
            if score >= 75:
                rating = "A+"
                emoji = "🟢"
            elif score >= 60:
                rating = "A"
                emoji = "🟢"
            elif score >= 45:
                rating = "B"
                emoji = "🟡"
            elif score >= 30:
                rating = "C"
                emoji = "🟠"
            else:
                rating = "D"
                emoji = "🔴"

            # ── FIX: Entry/Stop/Target — nutze aktuellen Preis als Entry ──
            # Alter Code: Entry = OR-High → R:R war immer fix 1.5:1
            # Neuer Code: Entry = current_price, da Trader JETZT einsteigen würde
            if breakout_dir == "LONG":
                stop = round(or_low, 2)  # Stop unter OR-Low
                entry = round(current_price, 2)
                _actual_risk = entry - stop
                if _actual_risk > 0:
                    target = round(entry + _actual_risk * 2.0, 2)  # 2:1 R:R Ziel
                else:
                    target = round(or_high + or_range * 1.5, 2)  # Fallback
            else:
                stop = round(or_high, 2)  # Stop über OR-High
                entry = round(current_price, 2)
                _actual_risk = stop - entry
                if _actual_risk > 0:
                    target = round(entry - _actual_risk * 2.0, 2)  # 2:1 R:R Ziel
                else:
                    target = round(or_low - or_range * 1.5, 2)  # Fallback

            risk = abs(entry - stop)
            reward = abs(target - entry)
            rr_ratio = round(reward / risk, 1) if risk > 0 else 0

            breakouts.append({
                "ticker": ticker,
                "direction": breakout_dir,
                "score": score,
                "rating": rating,
                "emoji": emoji,
                "current": round(current_price, 2),
                "or_high": round(or_high, 2),
                "or_low": round(or_low, 2),
                "or_range_pct": round(or_range_pct, 2),
                "breakout_pct": round(breakout_pct, 2),
                "gap_pct": cand.get("gap_pct", 0),
                "rvol": cand.get("rvol", 0),
                "volume": cand.get("volume", 0),
                "vwap": round(vwap, 2),
                "target": target,
                "stop": stop,
                "entry": entry,
                "rr": rr_ratio,
                "fakeout": _is_fakeout,
                "confirmed": _bo_confirmed,
                "mins_after_or": _mins_after_or,
                "factors": factors,
                "num_bars": len(bars),
            })

            time.sleep(0.15)  # Rate limiting

        except Exception as e:
            _debug_log(f"ORB error {ticker}", e)
            continue

    # Sortiere nach Score
    breakouts.sort(key=lambda x: x["score"], reverse=True)
    result["breakouts"] = breakouts
    result["candidates"] = [c for c in candidates if c.get("status") == "IN RANGE"]
    result.get("stats", {})["breakouts"] = len(breakouts)

    return result


@st.cache_data(ttl=300)
def fetch_btc_divergence_shorts():
    """
    BTC-Divergenz Short Scanner V1.0

    Findet Coins die GEGEN BTC-Schwäche pumpen und Exhaustion-Zeichen zeigen.
    Kriterien:
    1. BTC 7d-Change ≤ 0% (BTC ist schwach/seitwärts)
    2. Altcoin 7d-Change > +10% (Coin pumpt trotzdem)
    3. Exhaustion Score bewertet Short-Timing

    Returns: (results, btc_data, stats)
    """
    try:
        # Nutze gecachte Marktdaten (2 Min TTL → weniger Rate Limits)
        all_coins = _fetch_coingecko_markets(pages=4)
        if not all_coins:
            return [], None, {"error": "Keine Daten"}

        # ── BTC Benchmark ──
        btc_data = None
        for c in all_coins:
            if c.get("symbol", "").lower() == "btc" or c.get("id") == "bitcoin":
                btc_data = {
                    "price": c.get("current_price", 0),
                    "change_1h": c.get("price_change_percentage_1h_in_currency") or 0,
                    "change_24h": c.get("price_change_percentage_24h") or 0,
                    "change_7d": c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d") or 0,
                    "change_14d": c.get("price_change_percentage_14d_in_currency") or 0,
                    "change_30d": c.get("price_change_percentage_30d_in_currency") or 0,
                    "market_cap": c.get("market_cap", 0),
                }
                break

        if not btc_data:
            return [], None, {"error": "BTC nicht gefunden"}

        btc_7d = btc_data.get("change_7d", 0)
        btc_14d = btc_data.get("change_14d", 0)
        btc_30d = btc_data.get("change_30d", 0)

        # ── BTC-Stärke-Check (Multi-Timeframe) ──
        # Echte Divergenz-Trader shorten nur wenn BTC auf MINDESTENS 2 Zeitfenstern schwach ist.
        # Ein einzelnes schwaches Zeitfenster (z.B. 14d flat aber 7d +5%) ist keine Schwäche.
        btc_weak_7d = btc_7d <= 0.0          # 7d negativ/flat
        btc_weak_14d = btc_14d <= 3.0        # 14d kaum Bewegung
        btc_weak_30d = btc_30d <= 3.0        # 30d kaum Bewegung
        _btc_weak_count = sum([btc_weak_7d, btc_weak_14d, btc_weak_30d])
        btc_has_weakness = _btc_weak_count >= 2  # Mindestens 2 von 3 Zeitfenstern schwach
        btc_bullish = not btc_has_weakness

        # ── Multi-Exchange Perp-Daten (Bitget + MEXC, ~1400 Contracts) ──
        try:
            perp_data = fetch_multi_exchange_perps()
        except Exception:
            perp_data = {}

        results = []
        scanned = 0
        skipped = 0
        _div_total = len(all_coins)

        for _coin_idx, coin in enumerate(all_coins):
            # Progress-Update alle 50 Coins
            if _coin_idx % 50 == 0:
                try:
                    with open("/tmp/div_scan_progress.json", "w") as _pf:
                        json.dump({
                            "status": "running",
                            "checked": _coin_idx,
                            "total": _div_total,
                            "hits": len(results),
                            "detail": f"📊 {_coin_idx}/{_div_total} Coins geprüft — {len(results)} Divergenzen",
                            "timestamp": time.time()
                        }, _pf)
                except Exception:
                    pass
            try:
                price = coin.get("current_price") or 0
                if price <= 0:
                    continue
                symbol = coin.get("symbol", "").upper()
                # M-7 Audit-Fix: BTC selbst + vollständige Stable-/Wrapped-/LSD-Liste
                # + Leveraged-Token (3L/3S/…, UP/DOWN, BULL/BEAR) überspringen
                if symbol == "BTC" or symbol in EXCLUDED_CRYPTO_SYMBOLS_LOCAL or _is_leveraged_token_symbol(symbol):
                    continue

                change_1h = coin.get("price_change_percentage_1h_in_currency") or 0
                change_24h = coin.get("price_change_percentage_24h") or 0
                change_7d = (coin.get("price_change_percentage_7d_in_currency") or
                             coin.get("price_change_percentage_7d") or 0)
                change_14d = coin.get("price_change_percentage_14d_in_currency") or 0
                change_30d = coin.get("price_change_percentage_30d_in_currency") or 0
                market_cap = coin.get("market_cap") or 0
                vol_24h = coin.get("total_volume") or 0
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price

                # ── LIQUIDITÄTSFILTER (V70.7) ──
                # Volume = Liquidität. MCap niedrig damit Early Movers durchkommen.
                if vol_24h < 5_000_000:
                    continue  # <$5M Volume = deine Order bewegt den Preis
                if market_cap < 10_000_000:
                    continue  # <$10M MCap Minimum

                scanned += 1

                # ── Multi-Timeframe Divergenz-Filter ──
                # Coin qualifiziert sich wenn er auf MINDESTENS einem Zeitfenster
                # deutlich gegen BTC outperformt
                div_7d = change_7d - btc_7d
                div_14d = (change_14d - btc_14d) if change_14d else 0
                div_30d = (change_30d - btc_30d) if change_30d else 0

                # Beste Divergenz = höchstes Zeitfenster mit genug Signal
                best_div = max(div_7d, div_14d, div_30d)
                best_tf = "7d"
                if best_div == div_30d and div_30d >= 10:
                    best_tf = "30d"
                elif best_div == div_14d and div_14d >= 10:
                    best_tf = "14d"

                # Filter: Mindestens 10% Outperformance auf irgendeinem Zeitfenster
                if best_div < 10:
                    skipped += 1
                    continue
                # Coin muss absolut auch gestiegen sein (auf dem besten Zeitfenster)
                best_change = {"7d": change_7d, "14d": change_14d, "30d": change_30d}[best_tf]
                if best_change < 8:
                    skipped += 1
                    continue

                # ── OHLC-Daten für Wick-Berechnung ──
                # Fix #3: Open ist approximiert aus rolling-24h-change (nicht echte Tageskerze).
                # Wick-% sind daher Schätzungen. Wir clampen den Open auf [low, high].
                open_price = price / (1 + change_24h / 100) if change_24h != -100 else price
                open_price = max(low_24h, min(high_24h, open_price))
                candle_range = high_24h - low_24h if high_24h > low_24h else 0
                range_pct = (candle_range / low_24h * 100) if low_24h > 0 else 0

                if range_pct >= 0.5 and candle_range > 0:
                    # Min Range von 0.2% auf 0.5% erhöht — unter 0.5% ist die
                    # Wick-Berechnung zu rauschig und liefert falsche Shooting Stars
                    body_top = max(open_price, price)
                    body_bottom = min(open_price, price)
                    upper_wick_pct = ((high_24h - body_top) / candle_range) * 100
                    lower_wick_pct = ((body_bottom - low_24h) / candle_range) * 100
                else:
                    upper_wick_pct = 0
                    lower_wick_pct = 0

                close_pos = calculate_close_position(high_24h, low_24h, price, min_range_pct=0.3)

                # ── Multi-Exchange Funding Rate + OI für diesen Coin ──
                # M-5 Audit-Fix: 1000{SYM}-Mapping + Preis-Plausibilität gegen Kollisionen
                perp_info = _lookup_perp_info(perp_data, symbol, price)
                coin_funding_rate = perp_info.get("funding_rate") if perp_info else None
                # H-1: geschätztes OI (MEXC ohne contractSize) nicht in den Score füttern
                if perp_info and not perp_info.get("oi_usd_estimate"):
                    coin_oi_ratio = perp_info.get("oi_ratio")
                else:
                    coin_oi_ratio = None
                coin_exchanges = perp_info.get("exchanges", []) if perp_info else []
                coin_best_exchange = perp_info.get("best_exchange", "") if perp_info else ""

                # ── Exhaustion Score (Multi-Timeframe + Derivatives) ──
                exh_score, exh_details = calculate_exhaustion_score(
                    change_24h=change_24h,
                    change_7d=change_7d,
                    btc_change_7d=btc_7d,
                    rvol=None,
                    close_pos=close_pos,
                    upper_wick_pct=upper_wick_pct,
                    lower_wick_pct=lower_wick_pct,
                    market_cap=market_cap,
                    high_24h=high_24h,
                    low_24h=low_24h,
                    price=price,
                    vol_24h=vol_24h,
                    change_1h=change_1h,
                    change_14d=change_14d,
                    change_30d=change_30d,
                    btc_change_14d=btc_14d,
                    btc_change_30d=btc_30d,
                    funding_rate=coin_funding_rate,
                    oi_volume_ratio=coin_oi_ratio,
                )

                grade, grade_emoji, grade_label = get_exhaustion_grade(exh_score)

                # ── Short-Timing V3 (mit 1h + Preis-Position) ──
                # KERNPRINZIP: Preis muss NAHE AM HIGH sein für einen guten Short-Entry!
                # Wenn der Preis schon weit vom High gefallen ist → Move gelaufen, zu spät.
                #
                # close_pos: 1.0 = am High, 0.0 = am Low des 24h-Range
                # Für Short: close_pos > 0.7 = ideal (nahe High), < 0.4 = zu spät
                #
                # PURR-Fix: Coin +28% 7d, 24h negativ, aber Preis schon 15% vom High
                # → Das ist Konsolidierung, nicht frisches Reversal → "ZU SPÄT" nicht "JETZT"

                # ── H-7 Audit-Fix: Timing über gemeinsame Gate-Helper-Logik ──
                # Vorher: "JETZT SHORTEN" allein nach Divergenz/ExhScore — auch bei
                # BTC +20%/7d (btc_has_weakness wurde berechnet, aber ignoriert).
                # Jetzt: BTC-Schwäche-Gate + expliziter "kein Stop"-Hinweis,
                # da dieser Pfad keinen Entry/Stop/TP liefert (Beobachtungssignal).
                cp = close_pos if close_pos is not None else 0.5
                timing, _timing_quality, _btc_gate = _btc_div_signal_status(
                    exh_score, close_pos, change_1h, change_24h, btc_has_weakness)

                # Coins ohne Perp: Downgrade "JETZT" → "WATCHLIST" (nicht shortbar!)
                if not perp_info:
                    if "JETZT" in timing:
                        timing = "🟠 WATCHLIST — Kein Perp-Contract, nicht direkt shortbar ⚠️"
                        _timing_quality = 2
                    else:
                        timing = timing + " ⚠️ KEIN PERP"

                # RVOL für Anzeige
                if market_cap > 0 and vol_24h > 0:
                    turnover = (vol_24h / market_cap) * 100
                    mc = market_cap
                    if mc > 100_000_000_000:   bl = 3.0
                    elif mc > 10_000_000_000:  bl = 6.0
                    elif mc > 1_000_000_000:   bl = 10.0
                    elif mc > 100_000_000:     bl = 20.0
                    else:                       bl = 30.0
                    rvol = round(turnover / bl, 2)
                else:
                    rvol = 1.0

                # H-7: _timing_quality kommt direkt aus _btc_div_signal_status
                # (kein fragiles String-Matching mehr)

                results.append({
                    "Ticker": symbol,
                    "Name": coin.get("name", symbol),
                    "Preis": price,
                    "1h%": round(change_1h, 2),
                    "24h%": round(change_24h, 2),
                    "7d%": round(change_7d, 2),
                    "14d%": round(change_14d, 2),
                    "30d%": round(change_30d, 2),
                    "BTC_7d%": round(btc_7d, 2),
                    "BTC_14d%": round(btc_14d, 2),
                    "BTC_30d%": round(btc_30d, 2),
                    "Divergenz%": round(best_div, 1),
                    "BestTF": best_tf,
                    "Div7d%": round(div_7d, 1),
                    "Div14d%": round(div_14d, 1),
                    "Div30d%": round(div_30d, 1),
                    "ExhScore": exh_score,
                    "ExhGrade": grade,
                    "GradeEmoji": grade_emoji,
                    "GradeLabel": grade_label,
                    "Timing": timing,
                    "TimingQuality": _timing_quality,
                    "btc_gate": _btc_gate,  # H-7: False = BTC stark, kein Short-Timing
                    "RVOL": rvol,
                    "UpperWick%": round(upper_wick_pct, 1),
                    "ClosePos": close_pos,
                    "MarketCap": market_cap,
                    "Vol24h": vol_24h,
                    "ExhDetails": exh_details,
                    "CoinId": coin.get("id", ""),
                    "FundingRate": coin_funding_rate,
                    "OI_Ratio": coin_oi_ratio,
                    "HasPerp": bool(perp_info),
                    "Exchanges": coin_exchanges,
                    "BestExchange": coin_best_exchange,
                })

            except Exception:
                continue

        # ── SELL-OFF PROBABILITY SCORE ──
        # Composite-Score: Wie wahrscheinlich ist ein Sell-Off JETZT?
        # Kombiniert: Exhaustion (40%) + Timing (25%) + Preis-Position (20%) + Tradebarkeit (15%)
        for r in results:
            sell_prob = 0

            # 1. Exhaustion Score (0-40 Punkte) — Kernindikator
            exh = r["ExhScore"]
            sell_prob += min(40, exh * 0.4)

            # 2. Timing-Qualität (0-25 Punkte) — Ist der Entry JETZT gut?
            # Nutzt strukturierte TimingQuality statt fragiles String-Matching
            tq = r.get("TimingQuality", 0)
            if tq >= 5:
                sell_prob += 25  # 🔴 JETZT SHORTEN — Perfekter Einstieg
            elif tq >= 4:
                sell_prob += 20  # 🟢 JETZT
            elif tq >= 3:
                sell_prob += 12  # 🟡 BEREIT
            elif tq >= 2:
                sell_prob += 5   # 🟠 WATCHLIST
            elif tq < 0:
                sell_prob -= 10  # ZU SPÄT → ABZUG
            # ZU FRÜH (tq=0) = 0 Punkte

            # 3. Preis-Position (0-20 Punkte) — Nahe am High = besser shortbar
            cp = r.get("ClosePos") or 0.5
            if cp >= 0.80:
                sell_prob += 20  # Am High = perfekter Short-Entry
            elif cp >= 0.65:
                sell_prob += 15
            elif cp >= 0.50:
                sell_prob += 8
            elif cp < 0.35:
                sell_prob -= 5  # Am Low = Move gelaufen

            # 4. Tradebarkeit (0-15 Punkte)
            if r.get("HasPerp"):
                sell_prob += 10
                # Crowded Short Check (negatives FR = alle shorten schon)
                fr = r.get("FundingRate")
                if fr is not None and fr < -0.0003:
                    sell_prob -= 5  # Crowded short → weniger wahrscheinlich
                elif fr is not None and fr > 0.0005:
                    sell_prob += 5  # Longs zahlen = noch nicht geshorted
            # Volume-Bonus
            if r.get("Vol24h", 0) > 20_000_000:
                sell_prob += 5  # Genug Liquidität zum Traden

            r["SellProb"] = max(0, min(100, int(sell_prob)))

        # Sortiere nach Sell-Off Wahrscheinlichkeit (höchste zuerst)
        results.sort(key=lambda x: x["SellProb"], reverse=True)

        stats = {
            "scanned": scanned,
            "candidates": len(results),
            "skipped": skipped,
            "btc_7d": btc_7d,
            "btc_bullish": btc_bullish,
            "btc_has_weakness": btc_has_weakness,  # H-7: Gate-Status für UI
        }
        return results, btc_data, stats

    except Exception as e:
        return [], None, {"error": str(e)}


def fetch_stock_data(poly_key, session="Regular", skip_filters=False):
    """
    Holt Aktien-Daten von Polygon.io Snapshot API.

    WICHTIG: Die Snapshot API liefert immer die aktuellsten Daten inkl. Pre/Post Market
    im 'lastTrade' und 'min' Feld. Die 'day' Daten sind die Regular Session.

    Session Parameter steuert wie wir die Daten interpretieren:
    - Regular: Nutze 'day' Daten (Regular Hours OHLCV)
    - Pre-Market/After-Hours/Extended: Nutze 'lastTrade' für aktuellen Preis

    skip_filters: Wenn True, werden keine Filter angewendet (für MA Bounce etc.)
    """
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    
    # DEBUG: Detaillierte Filter-Statistiken
    debug_stats = {
        "total_tickers": 0,
        "skipped_change": 0,
        "skipped_rvol": 0,
        "skipped_closepos": 0,
        "skipped_vortag": 0,
        "skipped_preis": 0,
        "skipped_etf": 0,
        "skipped_other": 0,
        "closepos_samples": [],  # Sammle ein paar Close Position Werte
    }
    
    try:
        # Polygon Snapshot API
        url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=30).json()
        tickers = resp.get("tickers", [])
        
        if len(tickers) == 0:
            return [], 0, 0, debug_stats
        
        # Bei skip_filters: Keine Filter anwenden, nur Basis-Daten
        # WICHTIG: exclude_etfs=False weil load_common_stock_tickers 20+ API-Calls macht
        # und den Background-Thread ewig blockiert. Caller filtert selbst mit is_etf_or_etp().
        if skip_filters:
            f = {}  # Keine Filter
            af = {"exclude_etfs": False, "preis_min": 5.0, "preis_max": 100000, "min_liquidity": 0}  # Basis-Filter (min $5)
        else:
            f = st.session_state.active_filters
            af = st.session_state.additional_filters
        
        # ETF-Filter Flag
        exclude_etfs = af.get("exclude_etfs", True)
        
        # Lade Liste der echten Aktien (Common Stock) wenn Filter aktiv
        common_stocks = set()
        if exclude_etfs:
            common_stocks = load_common_stock_tickers(poly_key)
        
        for t in tickers:
            try:
                # =====================================================
                # AKTIEN FILTER - Nur echte Aktien (Common Stock)
                # =====================================================
                ticker_symbol = t.get("ticker", "")
                
                if exclude_etfs:
                    # Methode 1: Prüfe ob in Common Stock Liste
                    if common_stocks and ticker_symbol.upper() not in common_stocks:
                        debug_stats["skipped_etf"] += 1
                        continue
                    
                    # Methode 2: Fallback - alte Pattern-Prüfung falls Liste leer
                    if not common_stocks and is_etf_or_etp(ticker_symbol):
                        debug_stats["skipped_etf"] += 1
                        continue
                
                # SPAC-Filter: "Acquisition Corp" etc. rausfiltern
                _ticker_name = t.get("name", "") or ""
                if is_spac(_ticker_name):
                    debug_stats["skipped_etf"] += 1  # Zählt zu den gefilterten
                    continue

                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                last = t.get("lastTrade", {}) or {}
                minute_data = t.get("min", {}) or {}

                # =====================================================
                # PREIS JE NACH SESSION
                # =====================================================
                # Die Snapshot API hat KEINE separaten preMarket/afterHours Objekte!
                # Stattdessen: lastTrade enthält den letzten Trade (egal welche Session)
                
                if session in ["Pre-Market", "After-Hours", "Extended"]:
                    # Nutze lastTrade für aktuellsten Preis (inkl. Extended Hours)
                    price = last.get("p") or minute_data.get("c") or day.get("c") or prev.get("c") or 0
                    
                    if price <= 0:
                        skipped_no_price += 1
                        continue
                    
                    # Für Extended: OHLC aus day, aber Preis aus lastTrade
                    open_price = day.get("o") or price
                    high = day.get("h") or price
                    low = day.get("l") or price
                    close = price  # Aktueller Preis aus lastTrade
                    vol = day.get("v") or minute_data.get("v") or 0
                    
                    # Change vs. Previous Close
                    prev_close = prev.get("c") or 0
                    change = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                    
                else:  # Regular Hours (default)
                    # Preis: day.c zuerst, dann lastTrade als Fallback
                    price = day.get("c") or last.get("p") or minute_data.get("c") or prev.get("c") or 0
                    if price <= 0:
                        skipped_no_price += 1
                        continue
                    
                    open_price = day.get("o") or price
                    high = day.get("h") or price
                    low = day.get("l") or price
                    close = day.get("c") or price
                    vol = day.get("v") or minute_data.get("v") or 0
                    
                    # Change berechnen
                    change = t.get("todaysChangePerc")
                    if change is None:
                        prev_close = prev.get("c") or 0
                        change = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                    change = change or 0
                
                # =====================================================
                # GEMEINSAME BERECHNUNGEN
                # =====================================================
                
                # Previous Day Daten für Gap-Berechnung
                prev_high = prev.get("h") or 0
                prev_low = prev.get("l") or 0
                prev_close = prev.get("c") or 0
                
                # GAP-Berechnung - ZWEI VARIANTEN:
                # 
                # 1. Gap vs Close (Standard): Wie viel hat es seit gestern Schlusskurs bewegt?
                #    - Positiv: Open > Previous Close
                #    - Wird für "normale" Gap-Strategien verwendet
                #
                # 2. True Gap (über/unter Range): Open außerhalb der gestrigen Range
                #    - Gap Up: Open > Previous HIGH (komplett über der Range)
                #    - Gap Down: Open < Previous LOW (komplett unter der Range)
                #    - Stärkeres Signal als normaler Gap
                
                day_open = day.get("o") or open_price
                
                # Standard Gap vs Close
                gap_vs_close = ((day_open - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                
                # True Gap (Open komplett außerhalb der gestrigen Range)
                true_gap_pct = 0
                if prev_high > 0 and prev_low > 0:
                    if day_open > prev_high:
                        true_gap_pct = ((day_open - prev_high) / prev_high) * 100
                    elif day_open < prev_low:
                        true_gap_pct = ((day_open - prev_low) / prev_low) * 100
                
                # STANDARD: Gap % = Gap vs Previous Close (was Trader erwarten)
                gap_pct = gap_vs_close
                
                # WICK-Berechnungen
                # WICHTIG: Morgens ist die Range oft zu klein für zuverlässige Wick-Analyse
                candle_range = high - low if high > low else 0
                range_pct = (candle_range / low * 100) if low > 0 else 0
                
                # Nur Wick berechnen wenn genug Range (min 0.5% = sinnvolle Kerze)
                if range_pct >= 0.5 and candle_range > 0:
                    body_top = max(open_price, close)
                    body_bottom = min(open_price, close)
                    upper_wick_pct = ((high - body_top) / candle_range) * 100
                    lower_wick_pct = ((body_bottom - low) / candle_range) * 100
                else:
                    upper_wick_pct = 0
                    lower_wick_pct = 0
                
                # RVOL Berechnung - VERBESSERT mit Time-Normalisierung
                prev_vol = prev.get("v") or 0
                rvol = calculate_rvol_at_time(vol, prev_vol, session)
                rvol = min(rvol, 999.0)
                
                # Vortag Change - WICHTIG: Das ist die INTRADAY-Bewegung von GESTERN
                # (prev_close - prev_open) / prev_open
                # NICHT die Bewegung von vorgestern zu gestern!
                # 
                # Für Bull/Bear Flag wäre eigentlich (prev_close - prev_prev_close) nötig,
                # aber Polygon Snapshot hat nur 1 Tag History.
                # 
                # Interpretation:
                # Vortag% = KERZEN-BODY der gestrigen Session (Close vs Open)
                # NICHT die Tagesperformance (Close vs PrevDayClose)!
                # Positiv = bullische Kerze (Close > Open), Negativ = bärische Kerze
                prev_open = prev.get("o") or 0
                vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2) if prev_open > 0 else 0
                
                # Close Position Berechnung
                # WICHTIG: Bei Extended Hours ist Close Position NICHT sinnvoll!
                # High/Low kommen aus Regular Session, aber Close ist Extended Preis
                # Beispiel: Regular High=$100, Extended Preis=$115 → Close Pos = 1.5 (unmöglich!)
                if session in ["Pre-Market", "After-Hours", "Extended"]:
                    close_pos = None  # Nicht berechenbar für Extended Hours
                else:
                    close_pos = calculate_close_position(high, low, close)
                
                # ATR Berechnung (Volatilitäts-Kontext)
                atr_pct = calculate_atr_from_ohlc(high, low, close, prev_close)
                volatility_regime, vola_adj = get_volatility_regime(atr_pct)
                
                # Liquiditäts-Check
                # WICHTIG: Pre-Market/After-Hours haben typisch nur 10-20% des Regular-Volumes
                # Standard $100k Dollar-Vol ist für Extended Sessions zu strikt
                if session in ["Pre-Market", "After-Hours", "Extended"]:
                    base_min_dollar_vol = 20000  # $20k für Extended (vs $100k Regular)
                else:
                    base_min_dollar_vol = 100000

                is_liquid, dollar_volume = validate_liquidity(vol, price, min_dollar_volume=base_min_dollar_vol)
                
                # FILTER-LOGIK
                match = True
                
                # Liquiditäts-Filter für Gap-Strategien UND PM/AH Strategien (Gemini's Kritik)
                # Pre-Market ist dünn: ohne Dollar-Volume Filter zeigt Scanner illiquide Pennystocks
                current_strat = st.session_state.get("current_strategy", "")
                
                # Hole min_dollar_volume aus Strategie-Definition falls vorhanden
                strategy_def = STRATEGIES.get(current_strat, {})
                strat_min_dollar_vol = strategy_def.get("min_dollar_volume", 0)
                
                # Wenn Strategie min_dollar_volume definiert, nutze das
                if strat_min_dollar_vol > 0:
                    if dollar_volume < strat_min_dollar_vol:
                        skipped_filter += 1
                        debug_stats["skipped_other"] += 1
                        continue  # Skip wegen Strategie-spezifischem Dollar Volume
                
                liquidity_strategies = [
                    "Gap Up", "Gap Down", "Gap Up (High Vol)", "Gap Down (High Vol)",
                    "PM Gainers 🌅", "PM Losers 🌅", "PM Gap & Go 🌅", "PM Penny Movers 🌅",
                    "AH Gainers 🌙", "AH Losers 🌙", "AH Earnings Gainers 🌙📈", "AH Earnings Losers 🌙📉",
                    "Consolidation Breakout 🚀", "Reversal Setup 🪤"
                ]
                # HINWEIS: Breakout Long, Breakdown Short, Volume Surge, Whale Watch etc.
                # haben bereits RVOL-Filter (2.0+) eingebaut → brauchen keinen extra Liquiditäts-Filter
                
                # PM/AH: Sehr niedrig ($10k) weil dünn gehandelt
                # Regular: Moderater Threshold ($25k) für Basis-Liquidität
                if current_strat in liquidity_strategies:
                    if session in ["Pre-Market", "After-Hours"]:
                        min_dollar_vol = 10000    # $10k für PM/AH
                    else:
                        min_dollar_vol = 25000    # $25k für Regular
                    
                    is_liquid, dollar_volume = validate_liquidity(vol, price, min_dollar_vol)
                    if not is_liquid:
                        skipped_filter += 1
                        continue  # Skip illiquide Trades
                
                # GLOBALER LIQUIDITÄTS-FILTER (aus Zusatzfiltern)
                # WICHTIG: Schwellwert wird an Tageszeit angepasst!
                # Am Morgen (10:00 ET) sind erst ~12% des Tagesvolumens gehandelt.
                # Ohne Anpassung würde $10M-Filter fast alle Aktien rauswerfen.
                user_min_liquidity = af.get("min_liquidity", 0)
                if user_min_liquidity > 0:
                    adjusted_min_liq = get_time_adjusted_liquidity_threshold(user_min_liquidity, session)
                    if dollar_volume < adjusted_min_liq:
                        skipped_filter += 1
                        debug_stats["skipped_other"] += 1
                        continue  # Skip wegen User-definiertem Liquiditäts-Minimum
                
                # DEBUG: Zähle total tickers
                debug_stats["total_tickers"] += 1
                
                # Sammle Close Position Samples (erste 20)
                if close_pos is not None and len(debug_stats.get("closepos_samples", 0)) < 20:
                    debug_stats.get("closepos_samples", 0).append(round(close_pos, 2))
                
                # FILTER-CHECKS mit detailliertem Tracking
                filter_failed = None
                
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if not (rvol_min <= rvol <= rvol_max): 
                        filter_failed = "rvol"
                        debug_stats["skipped_rvol"] += 1
                
                if filter_failed is None and "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): 
                    filter_failed = "change"
                    debug_stats["skipped_change"] += 1
                    
                if filter_failed is None and "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): 
                    filter_failed = "vortag"
                    debug_stats["skipped_vortag"] += 1
                    
                if filter_failed is None and "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): 
                    filter_failed = "preis"
                    debug_stats["skipped_preis"] += 1
                
                # Close Position Filter - Skip wenn None (Extended Hours)
                if filter_failed is None and "Close Position" in f:
                    if close_pos is not None:
                        if not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): 
                            filter_failed = "closepos"
                            debug_stats["skipped_closepos"] += 1
                    # Wenn close_pos None ist (Extended Hours), ignoriere diesen Filter
                
                # Neue Filter: Gap & Wicks
                if filter_failed is None and "Gap %" in f and not (f["Gap %"][0] <= gap_pct <= f["Gap %"][1]): 
                    filter_failed = "gap"
                    debug_stats["skipped_other"] += 1
                
                # Wick-Filter mit Mindest-Range-Check
                # Morgens ist die Range sehr klein → Wick% unzuverlässig
                range_pct = ((high - low) / low * 100) if low > 0 else 0
                strat_min_range = strategy_def.get("min_range_pct", 0)
                
                if filter_failed is None and "Upper Wick %" in f:
                    # Prüfe ob genug Range für zuverlässige Wick-Berechnung
                    if strat_min_range > 0 and range_pct < strat_min_range:
                        filter_failed = "range_too_small"
                        debug_stats["skipped_other"] += 1
                    elif not (f["Upper Wick %"][0] <= upper_wick_pct <= f["Upper Wick %"][1]): 
                        filter_failed = "upper_wick"
                        debug_stats["skipped_other"] += 1
                        
                if filter_failed is None and "Lower Wick %" in f:
                    if strat_min_range > 0 and range_pct < strat_min_range:
                        filter_failed = "range_too_small"
                        debug_stats["skipped_other"] += 1
                    elif not (f["Lower Wick %"][0] <= lower_wick_pct <= f["Lower Wick %"][1]): 
                        filter_failed = "lower_wick"
                        debug_stats["skipped_other"] += 1
                
                if filter_failed is None and af.get("preis_min", 0) > 0 and price < af["preis_min"]: 
                    filter_failed = "preis_min"
                    debug_stats["skipped_other"] += 1
                if filter_failed is None and af.get("preis_max", 100000) < 100000 and price > af["preis_max"]: 
                    filter_failed = "preis_max"
                    debug_stats["skipped_other"] += 1
                if filter_failed is None and af.get("nur_gewinner") and change <= 0: 
                    filter_failed = "nur_gewinner"
                    debug_stats["skipped_other"] += 1
                if filter_failed is None and af.get("nur_verlierer") and change >= 0: 
                    filter_failed = "nur_verlierer"
                    debug_stats["skipped_other"] += 1
                
                if filter_failed is not None:
                    skipped_filter += 1
                    continue
                
                ticker_raw = t.get("ticker", "")
                alpha = calculate_alpha_score(rvol, vortag_chg, change)
                
                # Flag-Pattern Validierung (für Bull Flag / Bear Flag Strategien)
                flag_score = 0
                flag_details = []
                current_strategy = st.session_state.get("current_strategy", "")
                
                if current_strategy == "Bull Flag":
                    # V69: Echte Multi-Day Flag Analyse (20 Tageskerzen)
                    is_valid, flag_score, flag_details, _flag_data = detect_flag_pattern_multiday(
                        poly_key, ticker_raw, pattern_type="bull"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score

                elif current_strategy == "Bear Flag":
                    # V69: Echte Multi-Day Flag Analyse (20 Tageskerzen)
                    is_valid, flag_score, flag_details, _flag_data = detect_flag_pattern_multiday(
                        poly_key, ticker_raw, pattern_type="bear"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score
                
                # V69: Multi-Day Candlestick-Analyse für ALLE Strategien
                # Nur für Strategien die davon profitieren (nicht PM/AH Sessions)
                candle_analysis = None
                _session_strategy = any(kw in current_strategy for kw in ["PM ", "AH ", "🌅", "🌙"])
                if not _session_strategy and poly_key:
                    _candles = fetch_daily_candles(poly_key, ticker_raw, days=25)
                    if _candles and len(_candles) >= 5:
                        candle_analysis = analyze_candles(_candles)

                # Breakout Health Assessment — für ALLE Strategien mit positiver Change
                breakout_health = None
                SHORT_KEYWORDS = ["Short", "Bear", "Breakdown", "Losers", "Down", "Distribution", "⬇️", "Selling"]
                setup_direction = "short" if any(kw in current_strategy for kw in SHORT_KEYWORDS) else "long"

                if (setup_direction == "long" and change > 0) or (setup_direction == "short" and change < 0):
                    breakout_health = assess_breakout_health(
                        change_pct=abs(change), rvol=rvol, close_pos=close_pos,
                        high=high, low=low, close=price,
                        open_price=None, prev_close=prev_close,
                        prev_high=prev_high, prev_low=prev_low,
                        vortag_pct=vortag_chg, vi_result=None,
                        atr_pct=atr_pct
                    )

                # Setup Score
                setup_score = calculate_setup_score(
                    change_pct=change, rvol=rvol, close_pos=close_pos,
                    upper_wick_pct=upper_wick_pct, lower_wick_pct=lower_wick_pct,
                    vortag_pct=vortag_chg, atr_pct=atr_pct,
                    dollar_volume=dollar_volume, price=price,
                    direction=setup_direction
                )

                # Health-Penalty auf SetupScore anwenden
                if breakout_health and isinstance(breakout_health, dict):
                    bh_score = breakout_health.get("health_score", 100)
                    bh_selloff = breakout_health.get("selloff_risk", "LOW")
                    if bh_score < 40 or bh_selloff in ("IMMINENT", "CRITICAL"):
                        setup_score = max(0, setup_score - 25)  # Schwere Penalty
                    elif bh_score < 55 or bh_selloff == "HIGH":
                        setup_score = max(0, setup_score - 15)  # Mittlere Penalty
                    elif bh_score < 70 or bh_selloff == "MEDIUM":
                        setup_score = max(0, setup_score - 5)   # Leichte Penalty

                # V69: Candlestick-Bonus/Penalty auf SetupScore
                if candle_analysis:
                    _ca_trend = candle_analysis.get("trend", "sideways")
                    _ca_patterns = candle_analysis.get("patterns", [])
                    _bullish_patterns = [p for p in _ca_patterns if p.get("type") == "bullish"]
                    _bearish_patterns = [p for p in _ca_patterns if p.get("type") == "bearish"]

                    if setup_direction == "long":
                        # Long-Setup profitiert von Uptrend + bullischen Patterns
                        if _ca_trend == "up":
                            setup_score = min(100, setup_score + 8)
                        elif _ca_trend == "down":
                            setup_score = max(0, setup_score - 10)
                        if _bullish_patterns:
                            setup_score = min(100, setup_score + 5)
                        if _bearish_patterns:
                            setup_score = max(0, setup_score - 5)
                        if candle_analysis.get("volume_trend") == "accumulation":
                            setup_score = min(100, setup_score + 5)
                        elif candle_analysis.get("volume_trend") == "distribution":
                            setup_score = max(0, setup_score - 8)
                    else:
                        # Short-Setup profitiert von Downtrend + bearischen Patterns
                        if _ca_trend == "down":
                            setup_score = min(100, setup_score + 8)
                        elif _ca_trend == "up":
                            setup_score = max(0, setup_score - 10)
                        if _bearish_patterns:
                            setup_score = min(100, setup_score + 5)
                        if _bullish_patterns:
                            setup_score = max(0, setup_score - 5)
                        if candle_analysis.get("volume_trend") == "distribution":
                            setup_score = min(100, setup_score + 5)
                        elif candle_analysis.get("volume_trend") == "accumulation":
                            setup_score = max(0, setup_score - 8)

                results.append({
                    "Ticker": ticker_raw, "Name": "",
                    "Preis": round(price, 4), "Chg%": round(change, 2),
                    "RVOL": rvol, "Vortag%": vortag_chg,
                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5, "Alpha": alpha,
                    "Gap%": round(gap_pct, 2),
                    "TrueGap%": round(true_gap_pct, 2),
                    "UpperWick%": round(upper_wick_pct, 1),
                    "LowerWick%": round(lower_wick_pct, 1),
                    "FlagScore": flag_score,
                    "FlagDetails": flag_details,
                    "High": high,
                    "Low": low,
                    "PrevClose": prev_close,
                    "ATR%": atr_pct,
                    "VolRegime": volatility_regime,
                    "DollarVol": dollar_volume,
                    "IsLiquid": is_liquid,
                    "BreakoutHealth": breakout_health,
                    "SetupScore": setup_score,
                    "CandleAnalysis": candle_analysis,
                })
            except Exception as e:
                continue
        
        return results, skipped_no_price, skipped_filter, debug_stats
    except Exception as e:
        try:
            st.error(f"Polygon Fehler: {e}")
        except Exception:
            print(f"[fetch_stock_data] Polygon Fehler: {e}")
        return [], 0, 0, {}


# =============================================================================
# PRE-MARKET WATCHLIST - ERWEITERTE PM ANALYSE V2
# =============================================================================

# Katalysator-Keywords nach Kategorie + Sentiment-Klassifikation
# sentiment: "bullish", "bearish", "neutral" (bestimmt Score-Effekt)
CATALYST_KEYWORDS = {
    "📊 EARNINGS": {"keywords": ["earnings", "revenue", "profit", "EPS", "guidance", "quarterly", "fiscal", "beat", "miss", "outlook"], "sentiment": "neutral"},
    "💊 FDA/BIO": {"keywords": ["FDA", "approval", "trial", "phase", "drug", "clinical", "PDUFA", "NDA", "breakthrough", "therapy", "patent"], "sentiment": "neutral"},
    "🚨 OFFERING": {"keywords": ["offering", "dilution", "shelf", "secondary", "ATM", "warrant", "convertible", "raise", "registered direct", "public offering"], "sentiment": "bearish"},
    "🤝 M&A": {"keywords": ["acquisition", "merger", "takeover", "buyout", "deal", "purchase agreement"], "sentiment": "bullish"},
    "📋 CONTRACT": {"keywords": ["contract", "awarded", "partnership", "agreement", "collaboration", "deal with"], "sentiment": "bullish"},
    "⚖️ LEGAL": {"keywords": ["lawsuit", "SEC", "investigation", "settlement", "subpoena", "fraud", "class action", "indictment"], "sentiment": "bearish"},
    "📈 UPGRADE": {"keywords": ["upgrade", "price target", "buy rating", "overweight", "outperform"], "sentiment": "bullish"},
    "📉 DOWNGRADE": {"keywords": ["downgrade", "sell rating", "underweight", "underperform", "cut"], "sentiment": "bearish"},
    "🚨 REVERSE SPLIT": {"keywords": ["reverse split", "reverse stock split", "r/s"], "sentiment": "bearish"},
    "🔀 STOCK SPLIT": {"keywords": ["stock split", "forward split"], "sentiment": "bullish"},
    "💵 DIVIDEND": {"keywords": ["dividend", "payout", "distribution"], "sentiment": "bullish"},
    "👤 INSIDER": {"keywords": ["insider", "CEO buy", "director purchase", "10b5"], "sentiment": "bullish"},
    "🚀 PRODUCT": {"keywords": ["launch", "release", "new product", "unveil", "announce"], "sentiment": "bullish"},
    "🔻 BANKRUPTCY": {"keywords": ["bankruptcy", "chapter 11", "chapter 7", "delisting", "going concern"], "sentiment": "bearish"},
}

# Bearish catalysts → Score-Penalty statt Bonus!
BEARISH_CATALYSTS = {"🚨 OFFERING", "⚖️ LEGAL", "📉 DOWNGRADE", "🔻 BANKRUPTCY", "🚨 REVERSE SPLIT"}
BULLISH_CATALYSTS = {"🤝 M&A", "📋 CONTRACT", "📈 UPGRADE", "💵 DIVIDEND", "👤 INSIDER", "🚀 PRODUCT", "🔀 STOCK SPLIT"}

# _detect_catalyst — Moved to modules/data_fetchers.py

# get_ticker_news — Moved to modules/data_fetchers.py (V69.9 refactoring)



# get_ticker_details — Moved to modules/data_fetchers.py (V69.9 refactoring)



# get_pm_session_bars — Moved to modules/premarket.py



# get_spy_pm_change — Moved to modules/premarket.py



# calculate_pm_quality_score — Moved to modules/scorers.py (V69.6 refactoring)



# classify_pm_setup — Moved to modules/strategies.py (V69.6 refactoring)



def fetch_premarket_watchlist(poly_key, min_change=2.0, min_volume=50000, min_price=1.0, max_price=500.0):
    """
    ERWEITERTE Pre-Market Watchlist mit allen Metriken.
    
    Features:
    - Echtes PM Session High/Low (via Aggregates API)
    - Previous Day High/Low (PDH/PDL)
    - Gap Size & Direction
    - Relative Strength vs SPY
    - Setup Kategorisierung
    - Risk Management Levels
    - PM VWAP
    """
    results = []
    
    try:
        # 1. SPY PM Change für RS Berechnung
        spy_pm_change = get_spy_pm_change(poly_key)
        
        # 2. Snapshot API für schnelle Filterung
        url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=30).json()
        tickers = resp.get("tickers", [])
        
        if len(tickers) == 0:
            return [], spy_pm_change
        
        # Lade Common Stocks für Filter
        common_stocks = load_common_stock_tickers(poly_key)
        
        # Heute's Datum für Aggregates API
        from datetime import datetime
        import pytz
        et_tz = pytz.timezone('America/New_York')
        now_et = datetime.now(et_tz)
        today_str = now_et.strftime("%Y-%m-%d")
        
        # Erste Filterung: Nur Mover behalten
        candidates = []
        
        for t in tickers:
            try:
                ticker = t.get("ticker", "")
                
                # Nur echte Aktien (Common Stock)
                if common_stocks and ticker not in common_stocks:
                    continue
                
                # Skip Warrants, Units, Rights
                if any(ticker.endswith(suffix) for suffix in [".WS", ".WT", ".W", ".U", ".R", ".RT"]):
                    continue
                if ticker.endswith("W") and len(ticker) > 3:
                    continue
                
                prev_day = t.get("prevDay", {})
                last_trade = t.get("lastTrade", {})
                min_data = t.get("min", {})
                
                prev_close = prev_day.get("c", 0)
                if prev_close <= 0:
                    continue
                
                # Aktueller Preis
                current_price = last_trade.get("p", 0) or min_data.get("c", 0)
                if current_price <= 0:
                    continue
                
                # Preisfilter
                if current_price < min_price or current_price > max_price:
                    continue
                
                # PM Change (vs. Previous Close)
                pm_change = ((current_price - prev_close) / prev_close) * 100
                
                # Min Change Filter
                if abs(pm_change) < min_change:
                    continue
                
                # Volume Check (grob)
                pm_vol_estimate = min_data.get("v", 0)
                if pm_vol_estimate < min_volume / 10:  # Lockerer Filter, wird später genauer
                    continue
                
                # Previous Day Data
                pdh = prev_day.get("h", prev_close)
                pdl = prev_day.get("l", prev_close)
                
                # Gap berechnen (erstmal Schätzung, wird mit echtem PM Open korrigiert)
                gap_pct = ((current_price - prev_close) / prev_close) * 100
                
                # Previous Day Volume für Volume Ratio (Fix 4)
                prev_day_vol = prev_day.get("v", 0)
                
                candidates.append({
                    "ticker": ticker,
                    "current_price": current_price,
                    "prev_close": prev_close,
                    "pm_change": pm_change,
                    "pdh": pdh,
                    "pdl": pdl,
                    "gap_pct": gap_pct,
                    "prev_day_vol": prev_day_vol,
                    "snapshot_data": t
                })
                
            except Exception as e:
                continue
        
        # Sortiere nach Change und nimm Top 40 für Detail-Analyse
        candidates.sort(key=lambda x: abs(x["pm_change"]), reverse=True)
        top_candidates = candidates[:30]  # Reduziert von 40 für Rate Limiting
        
        # 3. Detaillierte PM Daten für Top-Kandidaten
        for idx_pm, cand in enumerate(top_candidates):
            try:
                ticker = cand.get("ticker", "")
                
                # Rate Limiting: Pause nach je 10 Calls
                if idx_pm > 0 and idx_pm % 10 == 0:
                    time.sleep(0.5)
                
                # Hole echte PM Session Daten
                pm_data = get_pm_session_bars(poly_key, ticker, today_str)
                
                if pm_data and pm_data["pm_volume"] >= min_volume:
                    current_price = cand.get("current_price", 0)
                    prev_close = cand.get("prev_close", 0)
                    
                    # Echte PM High/Low
                    pm_high = pm_data["pm_high"]
                    pm_low = pm_data["pm_low"]
                    pm_volume = pm_data["pm_volume"]
                    pm_vwap = pm_data["pm_vwap"]
                    pm_open = pm_data["pm_open"]
                    
                    # FIX 3: ECHTE Gap-Berechnung (PM Open vs PrevClose)
                    # Gap = Wie viel hat sich der Preis ÜBER NACHT bewegt (vor jeglichem PM Trading)
                    real_gap_pct = ((pm_open - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                    
                    # FIX 4: Volume Ratio (PM Vol vs erwartetes PM-Volumen)
                    prev_day_vol = cand.get("prev_day_vol", 0)
                    # PRE-MARKET Volume ist typisch nur 5-10% des Regular-Day-Volumens.
                    # Vergleich PM_Vol vs ganzen Tag waere unfair (0.05-0.1x = immer "DEAD").
                    # Stattdessen: Vergleiche PM_Vol mit erwartetem PM-Anteil (8% des Tages).
                    # VolR 1.0 = normales PM-Volume fuer diese Aktie.
                    expected_pm_vol = prev_day_vol * 0.08 if prev_day_vol > 0 else 0
                    vol_ratio = round(pm_volume / expected_pm_vol, 1) if expected_pm_vol > 0 else 1.0
                    
                    # PM Range und Position
                    pm_range = pm_high - pm_low if pm_high > pm_low else 0.01
                    pm_position = ((current_price - pm_low) / pm_range) * 100 if pm_range > 0 else 50
                    
                    # Distance to PM High/Low
                    dist_to_high = ((pm_high - current_price) / pm_high) * 100 if pm_high > 0 else 0
                    dist_to_low = ((current_price - pm_low) / pm_low) * 100 if pm_low > 0 else 0
                    
                    # Relative Strength vs SPY
                    rs_vs_spy = cand.get("pm_change", 0) - spy_pm_change
                    
                    # ATR% Schätzung (PM Range / Preis)
                    atr_pct = (pm_range / current_price) * 100 if current_price > 0 else 5
                    
                    # Setup Klassifizierung (mit echtem Gap + Volume Ratio!)
                    setup_type, setup_emoji, setup_desc = classify_pm_setup(
                        cand.get("pm_change", 0), real_gap_pct, pm_position, rs_vs_spy, atr_pct,
                        vol_ratio=vol_ratio
                    )
                    
                    # ============================================================
                    # TRADE SETUPS — Basierend auf ECHTEN Marktstruktur-Leveln
                    # ============================================================
                    # Stop: Unter echtem Support (PM Low, VWAP, PDH/PDL)
                    # TP: Nächste Resistenz oder Measured Move
                    # Kein künstliches 2% Minimum — echte Level bestimmen Risk
                    # ============================================================
                    
                    pdh = cand.get("pdh", 0)
                    pdl = cand.get("pdl", 0)
                    
                    # Fibonacci der PM Range
                    fib_382 = pm_low + pm_range * 0.382
                    fib_500 = pm_low + pm_range * 0.500
                    fib_618 = pm_low + pm_range * 0.618
                    
                    setups = []
                    
                    if cand.get("pm_change", 0) > 0:  # === LONG ===
                        
                        # --- Setup 1: BREAKOUT — Entry über PM High ---
                        brk_entry = pm_high
                        # Stop: Unter VWAP (echtes Niveau wo Käufer aktiv waren)
                        brk_stop = round(pm_vwap - pm_range * 0.05, 2)  # Knapp unter VWAP
                        setups.append({
                            "name": "Breakout",
                            "emoji": "🚀",
                            "desc": f"Entry bei Break über PM High ${pm_high:.2f}",
                            "entry": brk_entry,
                            "stop": brk_stop,
                        })
                        
                        # --- Setup 2: VWAP PULLBACK — Entry am VWAP ---
                        vwap_entry = pm_vwap
                        # Stop: Unter PM Low (echte Struktur-Unterstützung)
                        vwap_stop = round(pm_low - pm_range * 0.05, 2)  # Knapp unter PM Low
                        setups.append({
                            "name": "VWAP Pullback",
                            "emoji": "🔄",
                            "desc": f"Entry bei Pullback zum VWAP ${pm_vwap:.2f}",
                            "entry": vwap_entry,
                            "stop": vwap_stop,
                        })
                        
                        # --- Setup 3: SUPPORT RETEST — Entry an PDH oder Fib ---
                        if pdh > 0 and pdh < pm_high and pdh > pm_low:
                            retest_entry = pdh
                            retest_label = f"PDH ${pdh:.2f}"
                            # Stop: Unter PrevClose oder PDL
                            retest_stop = round(min(prev_close, pdl) - pm_range * 0.05, 2) if pdl > 0 else round(prev_close - pm_range * 0.10, 2)
                        else:
                            retest_entry = fib_500
                            retest_label = f"Fib 50% ${fib_500:.2f}"
                            # Stop: Unter PM Low
                            retest_stop = round(pm_low - pm_range * 0.10, 2)
                        
                        setups.append({
                            "name": "Support Retest",
                            "emoji": "📐",
                            "desc": f"Entry bei Retest von {retest_label}",
                            "entry": retest_entry,
                            "stop": retest_stop,
                        })
                        
                        # Primary/Alt Auswahl basierend auf Position
                        if pm_position >= 75:
                            primary_idx, alt_idx = 0, 1  # Breakout, VWAP
                            entry_signal = "🎯 OR BREAK"
                        elif pm_position >= 40:
                            primary_idx, alt_idx = 1, 0  # VWAP, Breakout
                            entry_signal = "🔄 PULLBACK"
                        else:
                            primary_idx, alt_idx = 2, 1  # Retest, VWAP
                            entry_signal = "📐 RETEST"
                        
                        entry_detail = setups[primary_idx]["desc"]
                        
                        # === TARGETS für LONG ===
                        for s in setups:
                            s["risk"] = max(s["entry"] - s["stop"], s["entry"] * 0.005)  # Min 0.5% risk
                            
                            # TP1: Measured Move (PM Range von Entry) oder nächste Resistenz
                            tp1_measured = s["entry"] + pm_range
                            # TP2: Extended Move (2x PM Range) oder Fib Extension
                            tp2_extended = s["entry"] + pm_range * 2.0
                            
                            # Für Breakout: Mindestens PM Range nach oben
                            # Für Pullback: PM High ist TP1, darüber TP2
                            if s["name"] == "Breakout":
                                s["tp1"] = round(tp1_measured, 2)
                                s["tp2"] = round(tp2_extended, 2)
                            elif s["name"] == "VWAP Pullback":
                                s["tp1"] = round(pm_high, 2)  # PM High als erstes Ziel
                                s["tp2"] = round(pm_high + pm_range * 0.5, 2)  # Über PM High hinaus
                            else:  # Support Retest
                                s["tp1"] = round(pm_high, 2)  # PM High
                                s["tp2"] = round(pm_high + pm_range * 0.5, 2)
                            
                            s["risk_pct"] = s["risk"] / s["entry"] * 100 if s["entry"] > 0 else 0
                    
                    else:  # === SHORT ===
                        
                        # --- Setup 1: BREAKDOWN — Entry unter PM Low ---
                        brk_entry = pm_low
                        # Stop: Über VWAP
                        brk_stop = round(pm_vwap + pm_range * 0.05, 2)
                        setups.append({
                            "name": "Breakdown",
                            "emoji": "💥",
                            "desc": f"Entry bei Break unter PM Low ${pm_low:.2f}",
                            "entry": brk_entry,
                            "stop": brk_stop,
                        })
                        
                        # --- Setup 2: VWAP REJECTION — Short am VWAP ---
                        vwap_entry = pm_vwap
                        # Stop: Über PM High
                        vwap_stop = round(pm_high + pm_range * 0.05, 2)
                        setups.append({
                            "name": "VWAP Rejection",
                            "emoji": "🔄",
                            "desc": f"Short bei Rejection am VWAP ${pm_vwap:.2f}",
                            "entry": vwap_entry,
                            "stop": vwap_stop,
                        })
                        
                        # --- Setup 3: RESISTANCE RETEST ---
                        if pdl > 0 and pdl > pm_low and pdl < pm_high:
                            retest_entry = pdl
                            retest_label = f"PDL ${pdl:.2f}"
                            retest_stop = round(max(prev_close, pdh) + pm_range * 0.05, 2) if pdh > 0 else round(prev_close + pm_range * 0.10, 2)
                        else:
                            retest_entry = fib_500
                            retest_label = f"Fib 50% ${fib_500:.2f}"
                            retest_stop = round(pm_high + pm_range * 0.10, 2)
                        
                        setups.append({
                            "name": "Resistance Retest",
                            "emoji": "📐",
                            "desc": f"Short bei Retest von {retest_label}",
                            "entry": retest_entry,
                            "stop": retest_stop,
                        })
                        
                        if pm_position <= 25:
                            primary_idx, alt_idx = 0, 1
                            entry_signal = "🎯 OR BREAK"
                        elif pm_position <= 60:
                            primary_idx, alt_idx = 1, 0
                            entry_signal = "🔄 REJECTION"
                        else:
                            primary_idx, alt_idx = 2, 1
                            entry_signal = "📐 RETEST"
                        
                        entry_detail = setups[primary_idx]["desc"]
                        
                        # === TARGETS für SHORT ===
                        for s in setups:
                            s["risk"] = max(s["stop"] - s["entry"], s["entry"] * 0.005)
                            
                            tp1_measured = s["entry"] - pm_range
                            tp2_extended = s["entry"] - pm_range * 2.0
                            
                            if s["name"] == "Breakdown":
                                s["tp1"] = round(tp1_measured, 2)
                                s["tp2"] = round(tp2_extended, 2)
                            elif s["name"] == "VWAP Rejection":
                                s["tp1"] = round(pm_low, 2)
                                s["tp2"] = round(pm_low - pm_range * 0.5, 2)
                            else:
                                s["tp1"] = round(pm_low, 2)
                                s["tp2"] = round(pm_low - pm_range * 0.5, 2)
                            
                            s["risk_pct"] = s["risk"] / s["entry"] * 100 if s["entry"] > 0 else 0
                    
                    # Primary Setup für die Hauptanzeige
                    primary = setups[primary_idx]
                    alt = setups[alt_idx]
                    entry_price = primary["entry"]
                    stop_price = primary["stop"]
                    risk = primary["risk"]
                    target1 = primary["tp1"]
                    target2 = primary["tp2"]
                    
                    # Dollar Volume
                    pm_dollar_vol = current_price * pm_volume
                    
                    results.append({
                        "Ticker": ticker,
                        "PM_Preis": round(current_price, 2),
                        "PM_Chg%": round(cand.get("pm_change", 0), 2),
                        "PM_Vol": pm_volume,
                        "PM_DollarVol": pm_dollar_vol,
                        "PM_High": round(pm_high, 2),
                        "PM_Low": round(pm_low, 2),
                        "PM_VWAP": round(pm_vwap, 2),
                        "PM_Open": round(pm_open, 2),
                        "PM_Position": round(pm_position, 1),
                        "Dist_High%": round(dist_to_high, 2),
                        "Dist_Low%": round(dist_to_low, 2),
                        "PrevClose": round(prev_close, 2),
                        "PDH": round(cand.get("pdh", 0), 2),
                        "PDL": round(cand.get("pdl", 0), 2),
                        "Gap%": round(real_gap_pct, 2),  # FIX 3: Echte Gap (PM Open vs PrevClose)
                        "RS_vs_SPY": round(rs_vs_spy, 2),
                        "ATR%": round(atr_pct, 2),
                        "Vol_Ratio": vol_ratio,  # FIX 4: PM Vol vs Avg Daily Vol
                        "Entry_Signal": entry_signal,
                        "Entry_Detail": entry_detail,
                        "Setup_Type": setup_type,
                        "Setup_Emoji": setup_emoji,
                        "Setup_Desc": setup_desc,
                        "Entry_Price": round(entry_price, 2),
                        "Stop_Price": round(stop_price, 2),
                        "Target1": round(target1, 2),
                        "Target2": round(target2, 2),
                        "Risk_R": round(risk, 2),
                        "Setups": setups,
                        "Primary_Idx": primary_idx,
                        "Alt_Idx": alt_idx,
                        "Direction": "🟢 LONG" if cand.get("pm_change", 0) > 0 else "🔴 SHORT",
                        "Move_Time": pm_data.get("first_move_time", "N/A"),
                        # Placeholder für News und Details (werden später gefüllt)
                        "News": [],
                        "Catalysts": [],  # FIX 2: Katalysator-Erkennung
                        "Shares_M": 0,
                        "Float_Cat": "UNKNOWN",
                        "Float_Emoji": "❓",
                        "Market_Cap_M": 0,
                        "Company_Name": "",
                    })
                    
            except Exception as e:
                continue
        
        # Sortiere nach absolutem PM Change
        results.sort(key=lambda x: abs(x["PM_Chg%"]), reverse=True)
        
        # 4. Hole News und Details für Top 20 (API Call Limit beachten)
        final_results = results[:30]
        for i, item in enumerate(final_results[:20]):  # Nur Top 20 für Details
            try:
                ticker = item.get("Ticker", "N/A")
                
                # Ticker Details (Shares, Market Cap)
                details = get_ticker_details(poly_key, ticker)
                item["Shares_M"] = details["shares_millions"]
                item["Float_Cat"] = details["float_category"]
                item["Float_Emoji"] = details["float_emoji"]
                item["Market_Cap_M"] = details["market_cap_millions"]
                item["Company_Name"] = details["name"]
                
                # Re-Classify mit Float-Info (SQUEEZE bei Low Float!)
                setup_type, setup_emoji, setup_desc = classify_pm_setup(
                    item.get("PM_Chg%", 0), item.get("Gap%", 0), item.get("PM_Position", ""), item.get("RS_vs_SPY", 0),
                    item.get("ATR%", 5.0), vol_ratio=item.get("Vol_Ratio", 1.0),
                    float_cat=item.get("Float_Cat", "")
                )
                item["Setup_Type"] = setup_type
                item["Setup_Emoji"] = setup_emoji
                item["Setup_Desc"] = setup_desc
                
                # News + Katalysator-Erkennung (nur Top 10 - API intensive)
                if i < 10:
                    news = get_ticker_news(poly_key, ticker, limit=2)
                    item["News"] = news
                    # Extrahiere Katalysatoren aus News
                    catalysts = []
                    for n in news:
                        if n.get("catalyst") and n["catalyst"] not in catalysts:
                            catalysts.append(n["catalyst"])
                        for c in n.get("all_catalysts", []):
                            if c not in catalysts:
                                catalysts.append(c)
                    item["Catalysts"] = catalysts
                    
            except Exception as e:
                continue
        
        # 5. QUALITY SCORE für alle Results (braucht Float + Catalysts)
        for item in final_results:
            _item_catalysts = item.get("Catalysts", [])
            has_catalyst = len(_item_catalysts) > 0
            pm_score, pm_breakdown, pm_confidence = calculate_pm_quality_score(
                pm_change=item.get("PM_Chg%", 0),
                gap_pct=item.get("Gap%", 0),
                pm_position=item.get("PM_Position", ""),
                rs_vs_spy=item.get("RS_vs_SPY", 0),
                vol_ratio=item.get("Vol_Ratio", 1.0),
                shares_m=item.get("Shares_M", 0),
                float_cat=item.get("Float_Cat", "UNKNOWN"),
                has_catalyst=has_catalyst,
                pm_price=item.get("PM_Preis", 0),
                pm_vwap=item.get("PM_VWAP", 0),
                catalysts=_item_catalysts
            )
            item["PM_Score"] = pm_score
            item["PM_Breakdown"] = pm_breakdown
            item["PM_Confidence"] = pm_confidence
        
        # 6. EARNINGS CHECK — Finnhub Calendar (cached 30min)
        try:
            finnhub_key = st.secrets.get("FINNHUB_KEY", "")
            if finnhub_key:
                earnings_cal = fetch_earnings_calendar(finnhub_key, days_ahead=7)
                if earnings_cal:
                    for item in final_results:
                        ear_info = check_earnings_proximity(item.get("Ticker", "N/A"), earnings_cal)
                        if ear_info:
                            item["EarningsWarning"] = ear_info
                            # Score Penalty
                            penalty = ear_info.get("score_penalty", 0)
                            item["PM_Score"] = max(0, item["PM_Score"] + penalty)
                            # Update Confidence nach Penalty
                            s = item.get("PM_Score", 0)
                            if s >= 75:
                                item["PM_Confidence"] = "🟢 HIGH"
                            elif s >= 55:
                                item["PM_Confidence"] = "🟡 MEDIUM"
                            elif s >= 35:
                                item["PM_Confidence"] = "🟠 LOW"
                            else:
                                item["PM_Confidence"] = "🔴 AVOID"
        except Exception:
            pass  # Earnings sind optional, kein Absturz
        
        # 7. Sortiere nach PM_Score (statt nur Change%)
        final_results.sort(key=lambda x: x.get("PM_Score", 0), reverse=True)
        
        return final_results, spy_pm_change
        
    except Exception as e:
        st.error(f"PM Watchlist Fehler: {e}")
        return [], 0


def _render_pm_item(item, direction="long"):
    """
    Renders a single PM watchlist item with Quality Score, Earnings Warning, and all metrics.
    Used by both LONG and SHORT tabs to avoid code duplication.
    """
    is_long = direction == "long"
    
    with st.container():
        # ── EARNINGS WARNING — Top of card, BEFORE everything else ──
        ear_info = item.get("EarningsWarning")
        if ear_info:
            level = ear_info.get("level", "")
            if level in ("TODAY_AMC", "TODAY_BMO", "TODAY"):
                st.error(f"⛔ {ear_info['warning']} — {ear_info.get('details', '')}")
            elif level == "YESTERDAY_AMC":
                st.warning(f"🚨 {ear_info['warning']} — {ear_info.get('details', '')}")
            elif level == "TOMORROW":
                st.warning(f"⚠️ {ear_info['warning']} — {ear_info.get('details', '')}")
            elif level == "THIS_WEEK":
                st.info(f"📅 Earnings diese Woche: {ear_info.get('date', '')} — {ear_info.get('details', '')}")
        
        # ── Header Row ──
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            # Ticker + Score Badge
            pm_score = item.get("PM_Score", 0)
            confidence = item.get("PM_Confidence", "🟡 MEDIUM")
            
            # Score Color
            if pm_score >= 75:
                score_color = "#22c55e"  # green
            elif pm_score >= 55:
                score_color = "#eab308"  # yellow
            elif pm_score >= 35:
                score_color = "#f97316"  # orange
            else:
                score_color = "#ef4444"  # red
            
            st.markdown(
                f"## {item.get('Ticker', 'N/A')} "
                f"<span style='background:{score_color};color:white;padding:2px 8px;border-radius:12px;"
                f"font-size:16px;font-weight:bold;vertical-align:middle;'>Setup {pm_score}/100</span>",
                unsafe_allow_html=True
            )
            
            change_color = "green" if item.get('PM_Chg%', 0) > 0 else "red"
            sign = "+" if item.get('PM_Chg%', 0) > 0 else ""
            st.markdown(f"**<span style='color:{change_color};font-size:24px;'>{sign}{item.get('PM_Chg%', 0):.1f}%</span>**", unsafe_allow_html=True)
            st.caption(f"{item.get('Setup_Emoji', '')} {item.get('Setup_Type', '')}")
            # Float Info
            if item.get('Shares_M', 0) > 0:
                st.caption(f"{item.get('Float_Emoji', '❓')} {item.get('Shares_M', 0):.1f}M shares")
        
        with col2:
            # Preis & Levels
            vol_ratio = item.get('Vol_Ratio', 0)
            vol_ratio_str = ""
            if vol_ratio > 0:
                if vol_ratio < 0.3:
                    vol_ratio_str = f" | VolR: **⚠️ {vol_ratio:.1f}x** (DÜNN!)"
                elif vol_ratio < 0.5:
                    vol_ratio_str = f" | VolR: **{vol_ratio:.1f}x** (niedrig)"
                elif vol_ratio >= 2.0:
                    vol_ratio_str = f" | VolR: **🔥 {vol_ratio:.1f}x**"
                else:
                    vol_ratio_str = f" | VolR: **{vol_ratio:.1f}x**"
            
            st.markdown(f"**💰 ${item.get('PM_Preis', 0):.2f}** | Vol: {item.get('PM_Vol', 0):,.0f}{vol_ratio_str}")
            
            if is_long:
                st.caption(f"📊 PM High: **${item.get('PM_High', 0):.2f}** | Low: ${item.get('PM_Low', 0):.2f} | VWAP: ${item.get('PM_VWAP', 0):.2f}")
            else:
                st.caption(f"📊 PM High: ${item.get('PM_High', 0):.2f} | Low: **${item.get('PM_Low', 0):.2f}** | VWAP: ${item.get('PM_VWAP', 0):.2f}")
            
            # Gap + PM Momentum (NEU V2)
            pm_mom = item.get("PM_Breakdown", {}).get("pm_momentum", 0)
            if pm_mom != 0:
                mom_sign = "+" if pm_mom > 0 else ""
                mom_color = "green" if ((is_long and pm_mom > 0) or (not is_long and pm_mom < 0)) else "red"
                mom_emoji = "🟢" if mom_color == "green" else "🔴"
                st.caption(f"📈 Gap: {item.get('Gap%', 0):+.1f}% | PM Momentum: **{mom_emoji} {mom_sign}{pm_mom:.1f}%** | RS: {item.get('RS_vs_SPY', 0):+.1f}%")
            else:
                st.caption(f"📈 Gap: {item.get('Gap%', 0):+.1f}% | RS vs SPY: {item.get('RS_vs_SPY', 0):+.1f}%")
            st.caption(f"📉 PDH: ${item.get('PDH', 0):.2f} | PDL: ${item.get('PDL', 0):.2f}")
            
            # Market Cap
            if item.get('Market_Cap_M', 0) > 0:
                mcap = item.get('Market_Cap_M', 0)
                if mcap >= 1000:
                    st.caption(f"💵 MCap: ${mcap/1000:.1f}B")
                else:
                    st.caption(f"💵 MCap: ${mcap:.0f}M")
        
        with col3:
            # Entry Signal
            signal = item.get('Entry_Signal', '')
            if "OR BREAK" in signal:
                if is_long:
                    st.success(signal)
                else:
                    st.error(signal)
            elif "WATCH" in signal:
                st.info(signal)
            else:
                st.warning(signal)
            
            # Confidence Badge
            st.caption(f"**{confidence}**")
            
            # Position Meter
            pos = item.get('PM_Position', '')
            if is_long:
                pos_bar = "🟩" * int(pos/10) + "⬜" * (10 - int(pos/10))
            else:
                filled = 10 - int(pos/10)
                pos_bar = "⬜" * int(pos/10) + "🟥" * filled
            st.caption(f"Range-Pos: {pos_bar} {pos:.0f}%")
            st.caption("↑ Preis in PM-Range (High/Low)", help="Zeigt wo der aktuelle Preis innerhalb der PreMarket-Spanne liegt. 100% = am PM-High. Nicht mit dem Setup-Score verwechseln!")
        
        # ── WARNINGS — Sofort sichtbare Probleme ──
        warnings_list = item.get("PM_Breakdown", {}).get("warnings", [])
        if warnings_list:
            warn_str = " • ".join(warnings_list)
            st.markdown(f"<div style='background:#fef2f2;border-left:4px solid #ef4444;padding:4px 10px;margin:4px 0;border-radius:4px;font-size:13px;'>"
                       f"⚠️ {warn_str}</div>", unsafe_allow_html=True)
        
        # ── Katalysator-Zeile (mit Bearish-Warnung) ──
        catalysts = item.get('Catalysts', [])
        if catalysts:
            _bear_cats = [c for c in catalysts if c in BEARISH_CATALYSTS]
            _bull_cats = [c for c in catalysts if c not in BEARISH_CATALYSTS]
            if _bear_cats:
                bear_str = " | ".join(_bear_cats)
                st.markdown(f"<div style='background:#fef2f2;border-left:4px solid #ef4444;padding:4px 10px;margin:4px 0;border-radius:4px;font-size:13px;'>"
                           f"🚨 <b>BEARISH Katalysator:</b> {bear_str} — Vorsicht bei Long!</div>", unsafe_allow_html=True)
            if _bull_cats:
                bull_str = " | ".join(_bull_cats)
                st.markdown(f"🎯 **Katalysator:** {bull_str}")
        
        # ── News Row ──
        news_list = item.get('News', [])
        if news_list:
            news_text = ""
            for n in news_list[:2]:
                sentiment_emoji = "🟢" if n.get('sentiment') == 'positive' else "🔴" if n.get('sentiment') == 'negative' else "⚪"
                cat_tag = f" [{n.get('catalyst', '')}]" if n.get('catalyst') else ""
                news_text += f"{sentiment_emoji} {n.get('title', '')[:60]}...{cat_tag} ({n.get('published', '')})\n"
            if news_text:
                st.caption(f"📰 **News:** {news_text}")
        
        # ── Trade Setups + Score Breakdown Expander ──
        with st.expander(f"📐 Trade Setups — {item.get('Setup_Desc', '')}"):
            # Score Breakdown V2
            breakdown = item.get("PM_Breakdown", {})
            if breakdown:
                bc1, bc2, bc3, bc4, bc5 = st.columns(5)
                with bc1:
                    m_score = breakdown.get('move', 0)
                    pm_mom = breakdown.get('pm_momentum', 0)
                    m_label = f"Move"
                    if abs(pm_mom) >= 0.5:
                        m_label += f" ({pm_mom:+.1f}%)"
                    st.metric(m_label, f"{m_score:.0f}/25")
                with bc2:
                    st.metric("Pos+VWAP", f"{breakdown.get('position', 0):.0f}/20")
                with bc3:
                    v_score = breakdown.get('volume', 0)
                    v_label = "Volume" if v_score >= 8 else "⚠️ Volume"
                    st.metric(v_label, f"{v_score:.0f}/25")
                with bc4:
                    st.metric("RS", f"{breakdown.get('rs', 0):.0f}/15")
                with bc5:
                    st.metric("Cat/Float", f"{breakdown.get('catalyst_float', 0):.0f}/15")
                
                # Penalty anzeigen (wenn vorhanden)
                total_penalty = breakdown.get("penalty", 0)
                if total_penalty < 0:
                    st.caption(f"🔻 Penalty: **{total_penalty}** Punkte (Fading/Stale/Contradiction)")
                
                # Earnings Penalty anzeigen
                if ear_info:
                    penalty = ear_info.get("score_penalty", 0)
                    st.caption(f"⚠️ Earnings Penalty: {penalty} Punkte")
                
                st.markdown("---")
            
            # Trade Setups
            all_setups = item.get("Setups", [])
            primary_idx = item.get("Primary_Idx", 0)
            alt_idx = item.get("Alt_Idx", 1)
            
            for si, setup in enumerate(all_setups):
                if si == primary_idx:
                    label = "⭐ PRIMARY"
                elif si == alt_idx:
                    label = "🔹 ALTERNATIVE"
                else:
                    label = "⚪ OPTION"
                
                s_risk_pct = setup.get("risk_pct", 0)
                
                st.markdown(f"**{label}: {setup.get('emoji', '')} {setup.get('name', '')}** — {setup.get('desc', '')}")
                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1:
                    st.metric("Entry", f"${setup.get('entry', 0):.2f}")
                with sc2:
                    st.metric("Stop", f"${setup.get('stop', 0):.2f}")
                with sc3:
                    if is_long:
                        _tp_r = (setup.get('tp1', 0) - setup.get('entry', 0)) / setup.get('risk', 0) if setup.get('risk', 0) > 0 and setup.get('entry', 0) != setup.get('tp1', 0) else 0
                    else:
                        _tp_r = (setup.get('entry', 0) - setup.get('tp1', 0)) / setup.get('risk', 0) if setup.get('risk', 0) > 0 and setup.get('entry', 0) != setup.get('tp1', 0) else 0
                    st.metric(f"TP1 ({abs(_tp_r):.1f}R)", f"${setup.get('tp1', 0):.2f}")
                with sc4:
                    if is_long:
                        _tp2_r = (setup.get('tp2', 0) - setup.get('entry', 0)) / setup.get('risk', 0) if setup.get('risk', 0) > 0 and setup.get('entry', 0) != setup.get('tp2', 0) else 0
                    else:
                        _tp2_r = (setup.get('entry', 0) - setup.get('tp2', 0)) / setup.get('risk', 0) if setup.get('risk', 0) > 0 and setup.get('entry', 0) != setup.get('tp2', 0) else 0
                    st.metric(f"TP2 ({abs(_tp2_r):.1f}R)", f"${setup.get('tp2', 0):.2f}")
                st.caption(f"Risk: ${setup.get('risk', 0):.2f} ({s_risk_pct:.1f}%)")
                
                if si < len(all_setups) - 1:
                    st.markdown("---")
            
            st.caption(f"Move Start: {item.get('Move_Time', 'N/A')}")
            if item.get('Company_Name'):
                st.caption(f"🏢 {item.get('Company_Name', '')}")
        
        st.divider()


def display_premarket_watchlist(pm_data, spy_change=0):
    """
    ERWEITERTE Pre-Market Watchlist Anzeige mit allen Metriken.
    """
    
    if not pm_data:
        st.warning("⏳ Keine Pre-Market Mover gefunden. PM Session: 4:00-9:30 AM ET")
        return
    
    # Header mit SPY Info + Score Distribution
    col_header1, col_header2, col_header3 = st.columns([2, 1, 2])
    with col_header1:
        st.success(f"📋 **{len(pm_data)} Pre-Market Setups** gefunden")
    with col_header2:
        spy_color = "🟢" if spy_change >= 0 else "🔴"
        st.info(f"SPY PM: {spy_color} {spy_change:+.2f}%")
    with col_header3:
        n_high = sum(1 for x in pm_data if x.get("PM_Score", 0) >= 75)
        n_med = sum(1 for x in pm_data if 55 <= x.get("PM_Score", 0) < 75)
        n_low = sum(1 for x in pm_data if 35 <= x.get("PM_Score", 0) < 55)
        n_avoid = sum(1 for x in pm_data if x.get("PM_Score", 0) < 35)
        n_earn = sum(1 for x in pm_data if x.get("EarningsWarning"))
        score_str = f"🟢{n_high} 🟡{n_med} 🟠{n_low} 🔴{n_avoid}"
        if n_earn > 0:
            score_str += f" | ⚠️ER:{n_earn}"
        st.caption(f"**Scores:** {score_str}")
    
    # Auto-Save Setups für Tracker
    if not st.session_state.get("_pm_setups_saved_today"):
        saved = _save_pm_setups(pm_data)
        if saved:
            st.session_state._pm_setups_saved_today = True
    
    # Tabs für verschiedene Ansichten
    tab_long, tab_short, tab_all, tab_tracker, tab_export = st.tabs(["🟢 LONG", "🔴 SHORT", "📊 Alle", "📈 Tracker", "📋 Export"])
    
    # Aufteilen in Long und Short
    long_candidates = [x for x in pm_data if x["PM_Chg%"] > 0]
    short_candidates = [x for x in pm_data if x["PM_Chg%"] < 0]
    
    # === LONG TAB ===
    with tab_long:
        if long_candidates:
            for item in long_candidates[:12]:
                _render_pm_item(item, direction="long")
        else:
            st.info("Keine Long Kandidaten im PM")
    
    # === SHORT TAB ===
    with tab_short:
        if short_candidates:
            for item in short_candidates[:12]:
                _render_pm_item(item, direction="short")
        else:
            st.info("Keine Short Kandidaten im PM")
    
    # === ALL TAB (Table View) ===
    with tab_all:
        import pandas as pd
        df = pd.DataFrame(pm_data)
        
        # Spalten für Anzeige (erweitert mit Score + Confidence)
        display_cols = ["Ticker", "PM_Score", "PM_Confidence", "PM_Chg%", "PM_Preis", "PM_High", "PM_Low", "Gap%", "Vol_Ratio", "RS_vs_SPY", "Shares_M", "Float_Cat", "Setup_Type", "Entry_Signal"]
        available_cols = [col for col in display_cols if col in df.columns]
        if available_cols:
            st.dataframe(
                df[available_cols],
                column_config={
                    "Ticker": st.column_config.TextColumn("Ticker"),
                    "PM_Score": st.column_config.ProgressColumn("Setup Score", format="%d", min_value=0, max_value=100),
                    "PM_Confidence": st.column_config.TextColumn("Conf.", help="🟢HIGH 🟡MED 🟠LOW 🔴AVOID"),
                    "PM_Chg%": st.column_config.NumberColumn("PM Chg%", format="%.1f%%"),
                    "PM_Preis": st.column_config.NumberColumn("Preis", format="$%.2f"),
                    "PM_High": st.column_config.NumberColumn("PM High", format="$%.2f"),
                    "PM_Low": st.column_config.NumberColumn("PM Low", format="$%.2f"),
                    "Gap%": st.column_config.NumberColumn("Gap%", format="%.1f%%"),
                    "Vol_Ratio": st.column_config.NumberColumn("VolR", format="%.1fx", help="PM Volume vs Avg Daily Volume"),
                    "RS_vs_SPY": st.column_config.NumberColumn("RS SPY", format="%.1f%%"),
                    "Shares_M": st.column_config.NumberColumn("Shares(M)", format="%.1f"),
                    "Float_Cat": st.column_config.TextColumn("Float"),
                    "Setup_Type": st.column_config.TextColumn("Setup"),
                    "Entry_Signal": st.column_config.TextColumn("Signal"),
                },
                use_container_width=True,
                hide_index=True,
            )
    
    # === TRACKER TAB ===
    with tab_tracker:
        st.subheader("📈 Setup Tracker — Hat es funktioniert?")
        st.caption("Verfolge ob die PM Setups im echten Markt funktioniert hätten")
        
        try:
            tracker_poly_key = st.secrets.get("POLYGON_KEY", "")
        except Exception:
            tracker_poly_key = ""
        
        if tracker_poly_key:
            display_pm_tracker(tracker_poly_key)
        else:
            st.warning("⚠️ Polygon API Key benötigt für Intraday-Auswertung")
    
    # === EXPORT TAB ===
    with tab_export:
        st.subheader("📋 Copy/Paste Watchlist")
        
        # OR BREAK Kandidaten
        or_break = [x for x in pm_data if "OR BREAK" in x["Entry_Signal"]]
        
        export_text = f"═══ PRE-MARKET WATCHLIST {datetime.now().strftime('%Y-%m-%d %H:%M')} ET ═══\n"
        export_text += f"SPY PM: {spy_change:+.2f}%\n\n"
        
        export_text += "🎯 OR BREAK SETUPS (Entry bei Open):\n"
        export_text += "─" * 60 + "\n"
        if or_break:
            for item in or_break[:10]:
                direction = "LONG" if item.get("PM_Chg%", 0) > 0 else "SHORT"
                float_info = f" | {item.get('Float_Emoji', '')} {item.get('Shares_M', 0):.0f}M" if item.get('Shares_M', 0) > 0 else ""
                vol_r = f" | VolR:{item.get('Vol_Ratio', 0):.1f}x" if item.get('Vol_Ratio', 0) > 0 else ""
                cat_info = f" | {' '.join(item.get('Catalysts', []))}" if item.get('Catalysts') else ""
                export_text += f"{item.get('Ticker', 'N/A'):6} | {direction:5} | {item.get('PM_Chg%', 0):+6.1f}% | Gap:{item.get('Gap%', 0):+.1f}% | E: ${item.get('Entry_Price', 0):.2f} | S: ${item.get('Stop_Price', 0):.2f}{float_info}{vol_r}{cat_info}\n"
        else:
            export_text += "Keine OR Break Kandidaten\n"
        
        export_text += "\n👀 WATCH (Warte auf Entwicklung):\n"
        export_text += "─" * 50 + "\n"
        watch = [x for x in pm_data if "WATCH" in x["Entry_Signal"]][:5]
        for item in watch:
            direction = "LONG" if item.get("PM_Chg%", 0) > 0 else "SHORT"
            export_text += f"{item.get('Ticker', 'N/A'):6} | {direction:5} | {item.get('PM_Chg%', 0):+6.1f}% | {item.get('Setup_Type', '')}\n"
        
        st.code(export_text)
        
        # Session State für Export in andere Teile der App
        if st.button("💾 Zur Watchlist hinzufügen", key="add_pm_to_watchlist"):
            for item in or_break[:5]:
                if item.get('Ticker', 'N/A') not in st.session_state.watchlist:
                    st.session_state.watchlist.append(item.get('Ticker', 'N/A'))
            st.success(f"✅ {min(len(or_break), 5)} Ticker zur Watchlist hinzugefügt!")
        
        # Trading Tipps
        with st.expander("💡 PM Watchlist Trading Anleitung"):
            st.markdown("""
            ### Setup-Typen erklärt:
            
            | Setup | Bedeutung | Trading Ansatz |
            |-------|-----------|----------------|
            | 🚀 GAP & GO | Gap Up + hält High | Entry bei OR Break über PM High |
            | 📉 GAP & FADE | Gap Down + schwach | Short bei OR Break unter PM Low |
            | 💥 SQUEEZE | Extreme Bewegung | Vorsicht! Kann in beide Richtungen gehen |
            | 📈 CONTINUATION | Stetiger Trend | Warte auf Pullback zum VWAP |
            | ⚠️ REVERSAL WATCH | Verliert Momentum | Nicht gegen den Fade traden |
            | ↔️ RANGE | Choppy | Warte auf klare Richtung |
            
            ### Entry Strategie:
            
            1. **Pre-Market (6:00-9:30 ET):**
               - Identifiziere 🎯 OR BREAK Kandidaten
               - Notiere Entry/Stop/Target Levels
               - Setze Alarme in TradingView
            
            2. **Opening Range (9:30-9:45 ET):**
               - Beobachte erste 15 Minuten
               - Opening Range = Hoch/Tief der ersten 15min
               - Entry NICHT sofort bei Open!
            
            3. **Entry Trigger:**
               - **LONG:** Preis > OR High UND > PM High
               - **SHORT:** Preis < OR Low UND < PM Low
               - Volume muss dabei sein!
            
            4. **Risk Management:**
               - Stop: Andere Seite der OR
               - Target 1: 1.5x Risk (nimm 50% Profit)
               - Target 2: 2.5x Risk (Trail Stop)
            """)


# =============================================================================
# PM SETUP TRACKER — Verfolge ob Setups im echten Markt funktioniert hätten
# =============================================================================

PM_TRACKER_FILE = "/tmp/alpha_station_pm_tracker.json"

# _save_pm_setups — Moved to modules/premarket.py



# _load_pm_tracker — Moved to modules/premarket.py



# evaluate_pm_setups — Moved to modules/premarket.py



def display_pm_tracker(poly_key):
    """Zeigt den PM Setup Tracker Tab an"""
    
    tracker_data = _load_pm_tracker()
    
    if not tracker_data:
        st.info("📊 Noch keine PM Setups gespeichert. Lade zuerst die PM Watchlist, dann werden Setups automatisch für Tracking gespeichert.")
        return
    
    available_dates = sorted(tracker_data.keys(), reverse=True)
    
    # Datum auswählen
    selected_date = st.selectbox(
        "📅 Datum auswählen", 
        available_dates,
        format_func=lambda d: f"{d} ({tracker_data[d].get('count', 0)} Setups)"
    )
    
    if not selected_date:
        return
    
    day_data = tracker_data[selected_date]
    
    st.caption(f"Gespeichert: {day_data.get('saved_at', 'N/A')} | {day_data.get('count', 0)} Ticker")
    
    # Check ob bereits ausgewertet
    tickers = day_data.get("tickers", [])
    has_results = any(t.get("results") for t in tickers)
    
    # Auswertung starten
    col_eval1, col_eval2 = st.columns([1, 2])
    with col_eval1:
        if st.button("🔍 Setups auswerten", key=f"eval_{selected_date}", 
                     help="Holt Intraday-Daten und prüft ob Entry/Stop/TP getroffen wurden"):
            with st.spinner(f"⏳ Werte {len(tickers)} Ticker aus..."):
                eval_results = evaluate_pm_setups(poly_key, selected_date, day_data)
                
                if eval_results:
                    # Speichere Ergebnisse zurück
                    for result in eval_results:
                        for ticker_data_item in tickers:
                            if ticker_data_item["ticker"] == result.get("ticker", ""):
                                ticker_data_item["results"] = result
                    
                    tracker_data[selected_date] = day_data
                    try:
                        with open(PM_TRACKER_FILE, "w") as f:
                            json.dump(tracker_data, f, indent=2)
                    except Exception as e:
                        _debug_log("Tracker result save failed", error=e)
                    
                    st.success(f"✅ {len(eval_results)} Ticker ausgewertet!")
                    st.rerun()
                else:
                    st.warning("Keine Intraday-Daten verfügbar. Nur für vergangene Handelstage möglich.")
    
    with col_eval2:
        if has_results:
            st.caption("✅ Bereits ausgewertet — Ergebnisse unten")
    
    # Ergebnisse anzeigen
    if not has_results:
        # Zeige gespeicherte Setups ohne Auswertung
        st.markdown("### 📋 Gespeicherte Setups")
        for t in tickers[:10]:
            direction_emoji = "🟢" if t.get("direction", "") == "LONG" else "🔴"
            st.markdown(f"**{direction_emoji} {t.get('ticker', '')}** — {t.get('pm_change', 0):+.1f}% | {t.get('setup_type', '')} | Signal: {t.get('entry_signal', '')}")
            for si, s in enumerate(t.get("setups", [])):
                is_primary = "⭐" if si == t.get("primary_idx", 0) else "  "
                st.caption(f"{is_primary} {s.get('emoji', '')} {s.get('name', '')}: Entry ${s.get('entry', 0):.2f} | Stop ${s.get('stop', 0):.2f} | TP1 ${s.get('tp1', 0):.2f} | TP2 ${s.get('tp2', 0):.2f}")
        return
    
    # === AUSWERTUNG ANZEIGEN ===
    evaluated = [t for t in tickers if t.get("results")]
    
    if not evaluated:
        return
    
    # Gesamtstatistik
    st.markdown("### 📊 Ergebnis-Übersicht")
    
    # Sammle Stats pro Setup-Typ
    stats_by_type = {}  # {setup_name: {wins, losses, total_r, count, ...}}
    all_trades = []
    
    for t in evaluated:
        result = t["results"]
        for sr in result.get("setup_results", []):
            sname = sr.get("setup_name", "Unknown")
            if sname not in stats_by_type:
                stats_by_type[sname] = {
                    "count": 0, "entries": 0, "wins": 0, "losses": 0,
                    "tp1_hits": 0, "tp2_hits": 0, "stops": 0,
                    "total_r": 0, "total_pnl_pct": 0,
                    "primary_wins": 0, "primary_count": 0,
                }
            
            stats = stats_by_type[sname]
            stats["count"] = stats.get("count", 0) + 1

            if sr.get("entry_hit", False):
                stats["entries"] = stats.get("entries", 0) + 1
                stats["total_r"] += sr.get("r_multiple", 0)
                stats["total_pnl_pct"] += sr.get("pnl_pct", 0)

                if sr.get("pnl_pct", 0) > 0:
                    stats["wins"] = stats.get("wins", 0) + 1
                else:
                    stats["losses"] = stats.get("losses", 0) + 1

                if sr.get("tp1_hit", False):
                    stats["tp1_hits"] += 1
                if sr.get("tp2_hit", False):
                    stats["tp2_hits"] += 1
                if sr.get("stop_hit", False):
                    stats["stops"] += 1
                
                if sr.get("is_primary"):
                    stats["primary_count"] += 1
                    if sr.get("pnl_pct", 0) > 0:
                        stats["primary_wins"] += 1
                
                all_trades.append({
                    "ticker": t.get("ticker", ""),
                    "direction": t.get("direction", ""),
                    "setup": sname,
                    "is_primary": sr.get("is_primary", False),
                    "r_multiple": sr.get("r_multiple", 0),
                    "pnl_pct": sr.get("pnl_pct", 0),
                    "exit_reason": sr.get("exit_reason", ""),
                })
    
    # Display Stats Table
    if stats_by_type:
        stat_cols = st.columns(len(stats_by_type))
        for ci, (sname, stats) in enumerate(stats_by_type.items()):
            with stat_cols[ci % len(stat_cols)]:
                entries = stats.get("entries", 0)
                win_rate = (stats.get("wins", 0) / entries * 100) if entries > 0 else 0
                avg_r = stats.get("total_r", 0) / entries if entries > 0 else 0
                
                emoji = "🚀" if "Breakout" in sname or "Breakdown" in sname else "🔄" if "VWAP" in sname or "Rejection" in sname else "📐"
                
                st.markdown(f"**{emoji} {sname}**")
                
                # Win Rate mit Farbe
                wr_color = "green" if win_rate >= 50 else "orange" if win_rate >= 40 else "red"
                st.markdown(f"Win Rate: <span style='color:{wr_color};font-weight:bold;'>{win_rate:.0f}%</span> ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)", unsafe_allow_html=True)
                
                st.caption(f"Entries: {entries}/{stats.get('count', 0)} | Avg R: {avg_r:+.1f}R")
                st.caption(f"TP1: {stats.get('tp1_hits', 0)}× | TP2: {stats.get('tp2_hits', 0)}× | Stops: {stats.get('stops', 0)}×")
                
                if stats["primary_count"] > 0:
                    prim_wr = stats["primary_wins"] / stats["primary_count"] * 100
                    st.caption(f"⭐ Als Primary: {prim_wr:.0f}% WR ({stats.get('primary_count', 0)}×)")
    
    # Einzelne Trades
    st.markdown("### 📝 Einzelne Trades")
    
    for t in evaluated:
        result = t["results"]
        direction_emoji = "🟢" if t.get("direction", "") == "LONG" else "🔴"
        day_chg = result.get("day_change_pct", 0)
        day_color = "green" if day_chg > 0 else "red"
        
        with st.expander(f"{direction_emoji} **{t.get('ticker', '')}** — PM: {t.get('pm_change', 0):+.1f}% | Day: {day_chg:+.1f}%"):
            st.caption(f"Day Range: ${result['day_low']:.2f} — ${result['day_high']:.2f} | Open: ${result['day_open']:.2f} → Close: ${result['day_close']:.2f}")
            
            for sr in result.get("setup_results", []):
                primary_tag = "⭐" if sr.get("is_primary") else "  "
                
                if sr.get("exit_reason", "") == "NO ENTRY":
                    color = "gray"
                    result_text = "Entry nicht getriggert"
                elif sr.get("exit_reason", "") == "STOP":
                    color = "red"
                    result_text = f"STOP → ${sr.get('exit_price', 0):.2f} ({sr.get('pnl_pct', 0):+.1f}% | {sr.get('r_multiple', 0):+.1f}R)"
                elif sr.get("exit_reason", "") == "TP2":
                    color = "green"
                    result_text = f"TP2 ✅ → ${sr.get('exit_price', 0):.2f} ({sr.get('pnl_pct', 0):+.1f}% | {sr.get('r_multiple', 0):+.1f}R)"
                elif sr.get("exit_reason", "") == "TP1+EOD":
                    color = "green"
                    result_text = f"TP1 ✅ + EOD → ${sr.get('exit_price', 0):.2f} ({sr.get('pnl_pct', 0):+.1f}% | {sr.get('r_multiple', 0):+.1f}R)"
                else:
                    color = "orange" if sr.get("pnl_pct", 0) >= 0 else "red"
                    result_text = f"EOD Close → ${sr.get('exit_price', 0):.2f} ({sr.get('pnl_pct', 0):+.1f}% | {sr.get('r_multiple', 0):+.1f}R)"
                
                st.markdown(
                    f"{primary_tag} **{sr.get('setup_name', '')}**: "
                    f"Entry ${sr.get('entry', 0):.2f} | Stop ${sr.get('stop', 0):.2f} → "
                    f"<span style='color:{color};'>{result_text}</span>",
                    unsafe_allow_html=True
                )
    
    # Best/Worst Setup Summary
    if all_trades:
        st.markdown("### 🏆 Best & Worst")
        
        best = max(all_trades, key=lambda x: x["r_multiple"])
        worst = min(all_trades, key=lambda x: x["r_multiple"])
        
        col_best, col_worst = st.columns(2)
        with col_best:
            st.success(f"🏆 Best: **{best.get('ticker', '')}** {best.get('setup', '')} → {best.get('r_multiple', 0):+.1f}R ({best.get('pnl_pct', 0):+.1f}%)")
        with col_worst:
            st.error(f"💀 Worst: **{worst.get('ticker', '')}** {worst.get('setup', '')} → {worst.get('r_multiple', 0):+.1f}R ({worst.get('pnl_pct', 0):+.1f}%)")
        
        # Avg R for primaries vs alternatives
        primary_trades = [t for t in all_trades if t["is_primary"]]
        alt_trades = [t for t in all_trades if not t["is_primary"]]
        
        if primary_trades and alt_trades:
            avg_r_primary = sum(t["r_multiple"] for t in primary_trades) / len(primary_trades)
            avg_r_alt = sum(t["r_multiple"] for t in alt_trades) / len(alt_trades)
            
            st.markdown(f"⭐ **Primary Setups**: Avg {avg_r_primary:+.2f}R ({len(primary_trades)} Trades)")
            st.markdown(f"🔹 **Alternative Setups**: Avg {avg_r_alt:+.2f}R ({len(alt_trades)} Trades)")
            
            if avg_r_primary > avg_r_alt:
                st.success("✅ Primary Selection schlägt Alternativen!")
            else:
                st.warning("⚠️ Alternative Setups wären besser gewesen — Selektion überprüfen!")


# =============================================================================
# 🧪 BACKTEST LAB — 6 Monate Strategietest mit echten Daten
# =============================================================================

BACKTEST_CACHE_FILE = "/tmp/alpha_station_backtest_cache.json"
BACKTEST_RESULTS_FILE = "/tmp/alpha_station_backtest_results.json"

# Stock-Universum: Liquide Aktien quer durch alle Sektoren
BACKTEST_UNIVERSE = [
    # === Tech Large Cap (20) ===
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "GOOG", "AMD", "INTC", "CRM",
    "AVGO", "ORCL", "ADBE", "CSCO", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    # === Tech Mid/Growth (20) ===
    "PLTR", "SOFI", "SQ", "SNAP", "ROKU", "NET", "SHOP", "COIN", "CRWD", "DDOG",
    "ZS", "SNOW", "ABNB", "UBER", "LYFT", "DASH", "PINS", "U", "RBLX", "HOOD",
    # === Finance (15) ===
    "JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "BLK", "AXP", "V",
    "MA", "PYPL", "FIS", "ICE", "CME",
    # === Healthcare (15) ===
    "MRNA", "PFE", "ABBV", "JNJ", "UNH", "LLY", "TMO", "ABT", "BMY", "GILD",
    "AMGN", "REGN", "VRTX", "ISRG", "BIIB",
    # === Energy (10) ===
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL",
    # === Consumer (15) ===
    "WMT", "NKE", "COST", "TGT", "HD", "LOW", "SBUX", "MCD", "CMG", "DPZ",
    "LULU", "DECK", "ROST", "TJX", "DG",
    # === Industrial (10) ===
    "BA", "CAT", "DE", "GE", "HON", "UPS", "FDX", "LMT", "RTX", "NOC",
    # === Volatile/Small Cap (25) ===
    "GME", "AMC", "MARA", "RIOT", "SMCI", "ARM", "IONQ", "RGTI", "RIVN", "LCID",
    "PLUG", "FCEL", "SPCE", "OPEN", "WISH", "CLOV", "BB", "NOK", "TLRY", "SNDL",
    "MSTR", "UPST", "AFRM", "PATH", "AI",
    # === Biotech/Pharma (15) ===
    "NVAX", "BNTX", "DNA", "CRSP", "BEAM", "EDIT", "NTLA", "FATE", "SGEN", "ARKG",
    "EXAS", "HIMS", "DOCS", "ACHR", "JOBY",
    # === Semiconductor (10) ===
    "MRVL", "ON", "SWKS", "QRVO", "WOLF", "SMTC", "CRUS", "ALGM", "POWI", "DIOD",
    # === Real Estate / REITs (10) ===
    "O", "AMT", "PLD", "SPG", "VICI", "MPW", "IRM", "DLR", "CCI", "EQIX",
    # === ETFs (8) ===
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "ARKK"
]

# Strategie-Definitionen mit klaren Trade-Regeln
# V68: Gelockert — alte RVOL-Schwellen (2.0+) waren unrealistisch für Backtest
# BACKTEST_STRATEGY_RULES — Moved to modules/strategies.py (V69.6 refactoring)



# fetch_backtest_daily_data — Moved to modules/data_fetchers.py (V69.9 refactoring)



# fetch_grouped_daily — Moved to modules/data_fetchers.py (V69.9 refactoring)



# run_full_backtest_grouped — Moved to modules/backtests.py



# run_bi_v2_backtest — Moved to modules/backtests.py



# =============================================================================
# 🔮 CRYPTO BACKTEST — Breakout Imminent für Krypto (CoinGecko OHLC)
# =============================================================================

DEFAULT_CRYPTO_COINS = [
    # Mega Cap (Top 5 by Market Cap)
    "BTC", "ETH", "BNB", "SOL", "XRP",
    # Large Cap (Top 6-15)
    "ADA", "DOGE", "TRX", "TON", "LINK",
    "AVAX", "SHIB", "DOT", "LTC", "SUI",
    # Mid Cap (Top 16-30)
    "ICP", "UNI", "NEAR", "APT", "ATOM",
    "FIL", "HBAR", "ARB", "OP", "INJ",
    "VET", "FTM", "ALGO", "SEI", "TIA",
    # Small Cap / DeFi / Memes
    "FET", "RENDER", "IMX", "PEPE", "BONK",
    "FLOKI", "WIF", "ENS", "1INCH", "SUSHI",
    "AAVE", "MKR", "CRV", "LDO", "SNX",
    "COMP", "RPL", "AXS", "SAND", "MANA",
    "APE", "GMT", "XLM", "MATIC",
]


# _crypto_breakout_ok — Moved to modules/helpers.py


def run_crypto_backtest(direction="long", days=90, coins=None, progress_callback=None):
    """
    🔮 Crypto Breakout Imminent Backtest — Rolling-Window Analyse mit CoinGecko OHLC.

    Analog zu run_bi_v2_backtest() aber für Krypto angepasst:
    - Keine Volume-Confirmation (CoinGecko hat kein hist. Volume)
    - Spread-basierte Breakout-Bestätigung
    - Schnellere Timeouts (24/7 Markt)
    - Höhere ATR-Stops (mehr Volatilität)

    Args:
        direction: "long" oder "short"
        days: Backtest-Zeitraum in Tagen (default 90)
        coins: Liste von Coin-Symbolen (default: DEFAULT_CRYPTO_COINS)
        progress_callback: (pct, text) Callback für UI

    Returns:
        dict: {trades, stats_by_grade, summary}
    """
    if coins is None:
        coins = DEFAULT_CRYPTO_COINS

    # === PARAMETER (Crypto-angepasst) ===
    # V69.1 AUDIT FIX: stop_atr_mult 1.5→1.2 (24/7-Markt ohne Overnight-Gaps
    # braucht weniger Puffer), tp1_mult 0.8→1.0 (realistischeres erstes Ziel).
    # Alt: R:R = (0.8×range)/(1.5×ATR) → brauchte range >= 2.8×ATR für min_rr.
    # Neu: R:R = (1.0×range)/(1.2×ATR) → range >= 1.8×ATR reicht. Viel realistischer.
    window_size = 20        # CoinGecko max ~85 daily Bars (hourly→daily, ≤90d)
    max_hold = 15           # Kürzere Trends als Aktien
    slippage = 0.0015       # 0.15% (Crypto Spreads)
    stop_atr_mult = 1.2     # 24/7-Markt ohne Gaps → engerer Stop OK
    tp1_mult = 1.0          # 1x Range als erstes Ziel (realistischer)
    tp2_mult = 1.8          # Zweites Ziel etwas höher (vorher 1.5)
    trail_pct = 0.50        # 50% Trail (vs 66% bei Aktien)
    min_rr = 1.5            # R:R Gate bleibt konservativ
    breakout_timeout = 5    # 5 Tage Breakout-Fenster
    entry_timeout = 8       # 8 Tage Entry-Fenster

    all_trades = []
    signals_found = 0
    coins_processed = 0

    # === PHASE 1: Daten holen ===
    coin_histories = {}
    total_coins = len(coins)

    for i, symbol in enumerate(coins):
        if progress_callback:
            progress_callback(i / total_coins * 0.3, f"📥 Lade {symbol} ({i+1}/{total_coins})...")

        coin_id = _resolve_coingecko_id(symbol)
        if not coin_id:
            continue

        # CoinGecko hourly→daily: max 90 Tage (danach nur noch 1 Punkt/Tag = kein H/L)
        fetch_days = min(days + window_size + 10, 90)
        ohlc_data = fetch_historical_data_crypto(coin_id, fetch_days)
        if not ohlc_data or len(ohlc_data) < window_size + 5:
            continue

        # Normalisiere zu Standard-Bar-Format
        bars = []
        for candle in ohlc_data:
            if len(candle) >= 5:
                ts = candle[0]
                # CoinGecko timestamp = Millisekunden
                from datetime import datetime
                dt = datetime.utcfromtimestamp(ts / 1000)
                bars.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "open": candle[1],
                    "high": candle[2],
                    "low": candle[3],
                    "close": candle[4],
                    "volume": 0,  # V69.1: Kein Volume bei CoinGecko OHLC (0 statt 1, damit OBV keine falschen Signale produziert falls crypto_mode versehentlich aus)
                })

        if len(bars) >= window_size + 5:
            coin_histories[symbol] = bars
            coins_processed += 1

    if not coin_histories:
        if progress_callback:
            progress_callback(1.0, "❌ Keine Crypto-Daten geladen")
        return {"trades": [], "stats_by_grade": {}, "summary": {
            "total_signals": 0, "total_filled": 0, "win_rate": 0,
            "total_pnl": 0, "n_coins": 0, "direction": direction, "days": days
        }}

    # === PHASE 2: Rolling Window Analyse + Trade Simulation ===
    cooldown = {}
    total_windows = sum(max(0, len(b) - window_size) for b in coin_histories.values())
    windows_done = 0

    for symbol, bars in coin_histories.items():
        for idx in range(window_size, len(bars)):
            windows_done += 1
            if progress_callback and windows_done % 20 == 0:
                pct = 0.3 + (windows_done / max(1, total_windows)) * 0.5
                progress_callback(min(pct, 0.8), f"🔍 Analysiere {symbol} ({windows_done}/{total_windows})...")

            # Cooldown: Min 7 Bars zwischen Signalen pro Coin
            if symbol in cooldown:
                if idx - cooldown[symbol] < 7:
                    continue

            window = bars[idx - window_size:idx]

            # BI V2 Analyse (crypto_mode=True: Spread-Proxies statt Volume-Signale)
            result = analyze_breakout_imminent(window, direction=direction, crypto_mode=True)
            if len(result) == 8:
                is_valid, bi_score, bi_max, details, confidence, grade, sm_fires, sm_hits = result
            else:
                is_valid, bi_score, bi_max, details, confidence, grade = result
                sm_fires, sm_hits = 0, 0

            if not is_valid or grade == "D":
                continue
            # KEIN sm_hits Filter hier — Grade-System reicht
            # (Crypto produziert weniger SM-Signale als Aktien)

            signals_found += 1
            cooldown[symbol] = idx

            # === Range & Levels berechnen ===
            range_high = max(b["high"] for b in window[-15:])
            range_low = min(b["low"] for b in window[-15:])
            range_size = range_high - range_low
            range_pct = (range_size / range_low * 100) if range_low > 0 else 0

            if range_pct < 1.0:  # Min 1% Range (Crypto: niedriger als Aktien 2%)
                continue

            atr_5 = sum((b["high"] - b["low"]) for b in window[-5:]) / 5
            if atr_5 <= 0:
                continue

            breakout_threshold = atr_5 * 0.2

            # Levels
            if direction == "long":
                breakout_level = range_high
                retest_zone_upper = range_high + atr_5 * 0.1
                retest_zone_lower = range_high - atr_5 * 0.25
                stop_price = range_high - atr_5 * stop_atr_mult
                tp1_price = range_high + range_size * tp1_mult
                tp2_price = range_high + range_size * tp2_mult
            else:
                breakout_level = range_low
                retest_zone_upper = range_low + atr_5 * 0.25
                retest_zone_lower = range_low - atr_5 * 0.1
                stop_price = range_low + atr_5 * stop_atr_mult
                tp1_price = range_low - range_size * tp1_mult
                tp2_price = range_low - range_size * tp2_mult

            # R:R Check
            est_entry = (retest_zone_upper + retest_zone_lower) / 2
            risk = abs(est_entry - stop_price)
            reward = abs(tp1_price - est_entry)
            rr = round(reward / risk, 2) if risk > 0 else 0
            if rr < min_rr:
                continue

            # === Trade-Basis ===
            trade_result = {
                "ticker": symbol,
                "signal_date": bars[idx]["date"],
                "grade": grade,
                "score": bi_score,
                "max_score": bi_max,
                "confidence": confidence,
                "smart_money_fires": sm_fires,
                "smart_money_hits": sm_hits,
                "direction": direction.upper(),
                "entry_target": round(est_entry, 6),
                "stop_target": round(stop_price, 6),
                "tp1_target": round(tp1_price, 6),
                "tp2_target": round(tp2_price, 6),
                "rr_planned": rr,
                "range_pct": round(range_pct, 1),
            }

            # === 3-Phase Simulation ===
            breakout_confirmed = False
            entry_filled = False
            actual_entry = None
            entry_date = None
            exit_price = None
            exit_date = None
            exit_reason = None
            bars_held = 0
            current_stop = stop_price
            tp1_hit = False
            breakout_high = 0

            for day_offset in range(1, max_hold + breakout_timeout + entry_timeout):
                future_idx = idx + day_offset
                if future_idx >= len(bars):
                    break

                future_bar = bars[future_idx]

                # Phase 1: Breakout Confirmation (OHNE Volume — Spread-basiert)
                if not breakout_confirmed:
                    recent = bars[max(0, future_idx - 5):future_idx]
                    bo_ok = _crypto_breakout_ok(
                        future_bar, range_high, range_low, atr_5, direction, recent
                    )
                    if bo_ok:
                        breakout_confirmed = True
                        breakout_high = future_bar.get("high", 0) if direction == "long" else future_bar.get("low", 0)
                    elif day_offset >= breakout_timeout:
                        break
                    else:
                        continue

                # Phase 2: Pullback Retest Entry
                if not entry_filled:
                    if direction == "long":
                        breakout_high = max(breakout_high, future_bar.get("high", 0))
                        price_pulled_back = future_bar.get("low", 0) <= retest_zone_upper
                        price_above_stop = future_bar.get("low", 0) > stop_price
                        had_upward_move = breakout_high > retest_zone_upper

                        if price_pulled_back and price_above_stop and had_upward_move:
                            actual_entry = max(future_bar.get("close", 0), retest_zone_lower) * (1 + slippage)
                            entry_filled = True
                            entry_date = future_bar.get("date", "")
                            bars_held = 0
                            risk = abs(actual_entry - stop_price)
                    else:
                        breakout_high = min(breakout_high, future_bar.get("low", 0))
                        price_pulled_back = future_bar.get("high", 0) >= retest_zone_lower
                        price_below_stop = future_bar.get("high", 0) < stop_price
                        had_downward_move = breakout_high < retest_zone_lower

                        if price_pulled_back and price_below_stop and had_downward_move:
                            actual_entry = min(future_bar.get("close", 0), retest_zone_upper) * (1 - slippage)
                            entry_filled = True
                            entry_date = future_bar.get("date", "")
                            bars_held = 0
                            risk = abs(actual_entry - stop_price)

                    if day_offset >= breakout_timeout + entry_timeout and not entry_filled:
                        break
                    if not entry_filled:
                        continue

                # Phase 3: Trade Management
                bars_held += 1
                if bars_held > max_hold:
                    exit_price = future_bar.get("close", 0)
                    exit_reason = "MAX_HOLD"
                    exit_date = future_bar.get("date", "")
                    break

                if direction == "long":
                    stop_hit = future_bar.get("low", 0) <= current_stop
                    tp1_possible = future_bar.get("high", 0) >= tp1_price
                    tp2_possible = future_bar.get("high", 0) >= tp2_price

                    if stop_hit:
                        exit_price = current_stop * (1 - slippage)
                        exit_reason = "BE_STOP" if tp1_hit else "STOP"
                        exit_date = future_bar.get("date", "")
                        break

                    if tp1_possible and not tp1_hit:
                        tp1_hit = True
                        trail_level = actual_entry + (tp1_price - actual_entry) * trail_pct
                        current_stop = trail_level

                    if tp2_possible:
                        exit_price = tp2_price * (1 - slippage)
                        exit_reason = "TP2"
                        exit_date = future_bar.get("date", "")
                        break
                else:  # short
                    stop_hit = future_bar.get("high", 0) >= current_stop
                    tp1_possible = future_bar.get("low", 0) <= tp1_price
                    tp2_possible = future_bar.get("low", 0) <= tp2_price

                    if stop_hit:
                        exit_price = current_stop * (1 + slippage)
                        exit_reason = "BE_STOP" if tp1_hit else "STOP"
                        exit_date = future_bar.get("date", "")
                        break

                    if tp1_possible and not tp1_hit:
                        tp1_hit = True
                        trail_level = actual_entry - (actual_entry - tp1_price) * trail_pct
                        current_stop = trail_level

                    if tp2_possible:
                        exit_price = tp2_price * (1 + slippage)
                        exit_reason = "TP2"
                        exit_date = future_bar.get("date", "")
                        break

            # Partial Exit wenn TP1 hit aber TP2 nicht
            if entry_filled and exit_price is None:
                if tp1_hit:
                    last_idx = min(idx + max_hold + breakout_timeout, len(bars) - 1)
                    tp1_exit = tp1_price * (1 - slippage if direction == "long" else 1 + slippage)
                    close_exit = bars[last_idx]["close"]
                    if direction == "long":
                        exit_price = (tp1_exit + max(close_exit, actual_entry)) / 2
                    else:
                        exit_price = (tp1_exit + min(close_exit, actual_entry)) / 2
                    exit_reason = "TP1_PARTIAL"
                    exit_date = bars[last_idx]["date"]
                else:
                    last_idx = min(idx + max_hold + breakout_timeout, len(bars) - 1)
                    exit_price = bars[last_idx]["close"]
                    exit_reason = "MAX_HOLD"
                    exit_date = bars[last_idx]["date"]

            # P&L berechnen
            if not entry_filled or actual_entry is None or exit_price is None:
                trade_result["outcome"] = "NO_FILL"
                trade_result["pnl_pct"] = 0
                trade_result["r_multiple"] = 0
                trade_result["is_winner"] = False
            else:
                if direction == "long":
                    pnl_pct = ((exit_price - actual_entry) / actual_entry) * 100
                else:
                    pnl_pct = ((actual_entry - exit_price) / actual_entry) * 100

                r_multiple = round(pnl_pct / (risk / actual_entry * 100), 2) if risk > 0 and actual_entry > 0 else 0

                trade_result["actual_entry"] = round(actual_entry, 6)
                trade_result["exit_price"] = round(exit_price, 6)
                trade_result["entry_date"] = entry_date
                trade_result["exit_date"] = exit_date
                trade_result["exit_reason"] = exit_reason
                trade_result["bars_held"] = bars_held
                trade_result["tp1_hit"] = tp1_hit
                trade_result["pnl_pct"] = round(pnl_pct, 2)
                trade_result["r_multiple"] = r_multiple
                trade_result["is_winner"] = pnl_pct > 0
                trade_result["outcome"] = exit_reason

            all_trades.append(trade_result)

    # === PHASE 3: Stats berechnen ===
    filled_trades = [t for t in all_trades if t.get("outcome") != "NO_FILL"]

    stats_by_grade = {}
    for g in ["S", "A", "B", "C", "D"]:
        grade_trades = [t for t in filled_trades if t.get("grade", 0) == g]
        if not grade_trades:
            continue

        winners = [t for t in grade_trades if t["is_winner"]]
        losers = [t for t in grade_trades if not t["is_winner"]]
        total_pnl = sum(t["pnl_pct"] for t in grade_trades)
        gross_profit = sum(t["pnl_pct"] for t in winners)
        gross_loss = abs(sum(t["pnl_pct"] for t in losers))

        stats_by_grade[g] = {
            "total": len(grade_trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(len(winners) / len(grade_trades) * 100, 1),
            "avg_pnl": round(total_pnl / len(grade_trades), 2),
            "avg_winner": round(gross_profit / len(winners), 2) if winners else 0,
            "avg_loser": round(-gross_loss / len(losers), 2) if losers else 0,
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0,
            "avg_r": round(sum(t["r_multiple"] for t in grade_trades) / len(grade_trades), 2),
            "tp1_rate": round(sum(1 for t in grade_trades if t.get("tp1_hit")) / len(grade_trades) * 100, 1),
            "tp2_rate": round(sum(1 for t in grade_trades if t.get("outcome") == "TP2") / len(grade_trades) * 100, 1),
        }

    summary = {
        "total_signals": signals_found,
        "total_filled": len(filled_trades),
        "no_fill": len(all_trades) - len(filled_trades),
        "win_rate": round(sum(1 for t in filled_trades if t["is_winner"]) / len(filled_trades) * 100, 1) if filled_trades else 0,
        "avg_pnl": round(sum(t["pnl_pct"] for t in filled_trades) / len(filled_trades), 2) if filled_trades else 0,
        "total_pnl": round(sum(t["pnl_pct"] for t in filled_trades), 2) if filled_trades else 0,
        "n_coins": coins_processed,
        "n_coins_total": total_coins,
        "direction": direction,
        "days": days,
    }

    if progress_callback:
        progress_callback(1.0, f"✅ Crypto Backtest fertig! {signals_found} Signale, {len(filled_trades)} Trades")

    return {"trades": all_trades, "stats_by_grade": stats_by_grade, "summary": summary}


# =============================================================================
# 🧬 BIOTECH CATALYST BACKTEST — Technical Setup + Volume Confirmation
# =============================================================================

# Top-traded Biotech/Pharma Tickers für den Backtest
# Curated: Mid/Small Cap mit regelmäßigen FDA-Katalysatoren
BIOTECH_BACKTEST_UNIVERSE = [
    # Large Cap Biotech (>$20B) — kleinere Moves aber liquide
    "AMGN", "GILD", "REGN", "VRTX", "BIIB", "MRNA", "ALNY", "BMRN",
    # Mid Cap Biotech ($2-20B) — Sweet Spot für Catalyst-Trading
    "SRPT", "EXEL", "PCVX", "IONS", "NBIX", "HALO", "INSM", "CRNX",
    "RARE", "MYGN", "FOLD", "ARWR", "IOVA", "KRYS", "ITCI", "CORT",
    "DAWN", "RVMD", "SWTX", "VKTX", "CYTK", "TGTX", "AXSM", "ADMA",
    # Small Cap Biotech ($200M-2B) — hohes Catalyst-Upside
    "AGEN", "ALVR", "ARQT", "AVXL", "BCRX", "CARA", "CLDX", "CPRX",
    "DVAX", "ENTA", "GERN", "HRTX", "IMVT", "KALA", "LQDA", "MNKD",
    "NUVB", "OCUL", "PLRX", "PRAX", "RCKT", "SAGE", "SAVA", "SMMT",
    "TVTX", "VCEL", "VRNA", "XNCR", "ZYME", "APLS", "ACLX", "CDTX",
    # Recent FDA-Active (regelmäßig PDUFA/AdCom Dates)
    "ACAD", "AKBA", "ANIK", "BLUE", "BLTE", "CMPS", "CRSP", "EDIT",
    "NTLA", "BEAM", "MDGL", "KROS", "LNTH", "LGND", "NKTR", "PCRX",
    "PTCT", "RLAY", "ROIV", "RYTM", "SNDX", "TARS", "TECH", "TXRX",
]


# _compute_biotech_technical_from_bars — Moved to modules/scanners.py



# run_biotech_backtest — Moved to modules/backtests.py



# compute_daily_metrics — Moved to modules/analysis.py


# check_signal — Moved to modules/helpers.py


# simulate_trade — Moved to modules/backtests.py



# run_full_backtest — Moved to modules/backtests.py



# compute_backtest_stats — Moved to modules/backtests.py



def display_backtest_lab(poly_key):
    """UI für den Backtest Lab Tab."""
    import streamlit as st
    import json
    
    st.header("🧪 Backtest Lab")
    st.caption("Teste Strategien über historische Daten mit echten Polygon-Daten")

    # === MODUS: Standard vs BI V2 vs Crypto vs BioTech ===
    bt_mode = st.radio("Backtest-Modus", ["📊 Standard Strategien", "🔮 Breakout Imminent V2", "🌐 Crypto BI", "🧬 BioTech Catalyst"],
                        horizontal=True, key="bt_mode_radio")

    # =================================================================
    # 🔮 BREAKOUT IMMINENT V2 BACKTEST
    # =================================================================
    if bt_mode == "🔮 Breakout Imminent V2":
        st.subheader("🔮 Breakout Imminent V2 — Historischer Backtest")
        st.caption("Rollt ein 30-Tage-Fenster über den gesamten Zeitraum und sucht nach BI V2 Signalen")

        col_b1, col_b2, col_b3 = st.columns(3)
        with col_b1:
            bi_months = st.selectbox("📅 Zeitraum", [3, 6, 12], index=1, format_func=lambda x: f"{x} Monate", key="bi_months")
        with col_b2:
            bi_direction = st.selectbox("📈 Richtung", ["long", "short"], key="bi_dir")
        with col_b3:
            bi_max_tickers = st.selectbox("🎯 Aktien (Mid-Cap Prio)", [100, 200, 500, 1000], index=1, key="bi_max")

        st.caption(f"⏱️ Geschätzte Dauer: ~{bi_months * 22 // 5 + bi_max_tickers // 20} Min (Grouped Daily + Analyse)")

        if st.button("🚀 BI V2 Backtest starten", type="primary", use_container_width=True, key="bi_bt_start"):
            progress_bar = st.progress(0, text="Starte BI V2 Backtest...")

            def update_progress(pct, text):
                progress_bar.progress(min(pct, 1.0), text=text)

            with st.spinner(f"Analysiere {bi_max_tickers} Aktien über {bi_months} Monate..."):
                bi_results = run_bi_v2_backtest(
                    poly_key,
                    direction=bi_direction,
                    months=bi_months,
                    max_tickers=bi_max_tickers,
                    min_price=5.0,
                    min_volume=200000,
                    progress_callback=update_progress
                )

            st.session_state["bi_backtest_results"] = bi_results

        # Ergebnisse anzeigen
        bi_results = st.session_state.get("bi_backtest_results")
        if bi_results:
            summary = bi_results["summary"]
            stats = bi_results["stats_by_grade"]
            trades = bi_results["trades"]

            st.divider()
            st.subheader("📊 Ergebnisse")

            # Summary Metrics
            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
            with col_s1:
                st.metric("Signale gefunden", summary["total_signals"])
            with col_s2:
                st.metric("Trades ausgefuehrt", summary["total_filled"])
            with col_s3:
                st.metric("Win Rate", f"{summary.get('win_rate', 0)}%")
            with col_s4:
                _total_pnl = summary["total_pnl"]
                st.metric("Total P&L", f"{_total_pnl:+.1f}%", delta_color="normal" if _total_pnl >= 0 else "inverse")

            st.caption(f"📈 Richtung: {summary.get('direction', 'LONG').upper()} | ⏱️ {summary.get('months', 0)} Monate | 🎯 {summary.get('n_tickers', 0)} Aktien analysiert (von {summary.get('n_tickers_total', 0)} gefiltert)")

            # Grade Breakdown
            if stats:
                st.divider()
                st.subheader("🏆 Performance nach Grade")

                grade_emojis = {"S": "🏆", "A": "🔥", "B": "✅", "C": "⚠️", "D": "❌"}

                for g in ["S", "A", "B", "C", "D"]:
                    if g not in stats:
                        continue
                    s = stats[g]
                    emoji = grade_emojis.get(g, "")

                    with st.expander(f"{emoji} Grade {g} — {s['total']} Trades | Win Rate: {s['win_rate']}% | Avg P&L: {s['avg_pnl']:+.2f}% | PF: {s['profit_factor']}", expanded=(g in ("S", "A"))):
                        col_g1, col_g2, col_g3, col_g4, col_g5 = st.columns(5)
                        with col_g1:
                            st.metric("Trades", s["total"])
                            st.metric("Winners", s["winners"])
                        with col_g2:
                            st.metric("Win Rate", f"{s['win_rate']}%")
                            st.metric("Losers", s["losers"])
                        with col_g3:
                            st.metric("Avg Winner", f"+{s['avg_winner']:.2f}%")
                            st.metric("Avg Loser", f"{s['avg_loser']:.2f}%")
                        with col_g4:
                            st.metric("Profit Factor", f"{s['profit_factor']}")
                            st.metric("Avg R", f"{s['avg_r']:.2f}R")
                        with col_g5:
                            st.metric("TP1 Rate", f"{s['tp1_rate']}%")
                            st.metric("TP2 Rate", f"{s['tp2_rate']}%")

                        st.metric("Total P&L", f"{s['total_pnl']:+.2f}%")

            # Trade-Liste
            if trades:
                st.divider()
                filled = [t for t in trades if t.get("outcome") != "NO_FILL"]
                if filled:
                    st.subheader(f"📋 Trade-Liste ({len(filled)} Trades)")
                    _trade_df_data = []
                    for t in sorted(filled, key=lambda x: x.get("pnl_pct", 0), reverse=True):
                        _trade_df_data.append({
                            "Ticker": t.get("ticker", ""),
                            "Datum": t.get("signal_date", ""),
                            "Grade": t.get("grade", 0),
                            "Score": f"{t.get('score', 0)}/{t.get('max_score', 1)}",
                            "Entry": f"${t.get('actual_entry', 0):.2f}",
                            "Exit": f"${t.get('exit_price', 0):.2f}",
                            "P&L": f"{t.get('pnl_pct', 0):+.2f}%",
                            "R": f"{t.get('r_multiple', 0):.1f}R",
                            "Exit Grund": t.get("outcome", ""),
                            "Tage": t.get("bars_held", 0),
                        })
                    st.dataframe(_trade_df_data, use_container_width=True)
        else:
            st.info("🔄 Klicke 'BI V2 Backtest starten' um die Strategie zu testen.")

        return  # BI V2 Modus → nicht weitermachen mit Standard

    # =================================================================
    # 🌐 CRYPTO BREAKOUT IMMINENT BACKTEST
    # =================================================================
    elif bt_mode == "🌐 Crypto BI":
        st.subheader("🌐 Crypto Breakout Imminent — Historischer Backtest")
        st.caption("Testet BI-Signale auf 13 Krypto-Coins via CoinGecko OHLC (kein Volume → Spread-Confirmation)")

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            crypto_days = st.selectbox("📅 Tage", [30, 60, 90], index=2,
                                       format_func=lambda x: f"{x} Tage", key="crypto_bt_days")
        with col_c2:
            crypto_direction = st.selectbox("📈 Richtung", ["long", "short"], key="crypto_bt_dir")

        st.caption(f"🪙 {len(DEFAULT_CRYPTO_COINS)} Coins: Top 30 by Market Cap + DeFi + Memes")
        st.caption(f"⏱️ ~6-8 Minuten ({len(DEFAULT_CRYPTO_COINS)} Coins × CoinGecko Rate Limit ~10 Calls/Min)")

        if st.button("🚀 Crypto Backtest starten", type="primary", use_container_width=True, key="crypto_bt_start"):
            progress_bar = st.progress(0, text="Starte Crypto Backtest...")

            def update_crypto_progress(pct, text):
                progress_bar.progress(min(pct, 1.0), text=text)

            with st.spinner(f"Analysiere {len(DEFAULT_CRYPTO_COINS)} Coins über {crypto_days} Tage..."):
                crypto_results = run_crypto_backtest(
                    direction=crypto_direction,
                    days=crypto_days,
                    progress_callback=update_crypto_progress
                )

            st.session_state["crypto_backtest_results"] = crypto_results

        # Ergebnisse anzeigen
        crypto_results = st.session_state.get("crypto_backtest_results")
        if crypto_results:
            summary = crypto_results["summary"]
            trades = crypto_results.get("trades", [])
            stats = crypto_results.get("stats_by_grade", {})

            # Summary Metrics
            st.divider()
            st.subheader("📊 Zusammenfassung")
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            mc1.metric("Signale", summary["total_signals"])
            mc2.metric("Trades", summary["total_filled"])
            mc3.metric("Win Rate", f"{summary.get('win_rate', 0):.1f}%")
            mc4.metric("Total P&L", f"{summary.get('total_pnl', 0):+.2f}%")
            mc5.metric("Coins", summary["n_coins"])

            # Grade Breakdown
            if stats:
                st.divider()
                st.subheader("📊 Grade Breakdown")
                for grade in ["S", "A", "B", "C"]:
                    if grade in stats:
                        s = stats[grade]
                        with st.expander(f"Grade {grade}: {s['total']} Trades, {s['win_rate']:.1f}% WR, {s['total_pnl']:+.2f}% P&L"):
                            gc1, gc2, gc3, gc4, gc5 = st.columns(5)
                            gc1.metric("Winners", s["winners"])
                            gc2.metric("Losers", s["losers"])
                            gc3.metric("Profit Factor", f"{s['profit_factor']:.2f}")
                            gc4.metric("Avg R", f"{s['avg_r']:.2f}")
                            gc5.metric("Avg P&L", f"{s['avg_pnl']:+.2f}%")

                            gc6, gc7 = st.columns(2)
                            gc6.metric("TP1 Rate", f"{s.get('tp1_rate', 0):.1f}%")
                            gc7.metric("TP2 Rate", f"{s.get('tp2_rate', 0):.1f}%")

            # Trade-Liste
            if trades:
                st.divider()
                filled = [t for t in trades if t.get("outcome") != "NO_FILL"]
                if filled:
                    st.subheader(f"📋 Trade-Liste ({len(filled)} Trades)")
                    _crypto_df = []
                    for t in sorted(filled, key=lambda x: x.get("pnl_pct", 0), reverse=True):
                        _crypto_df.append({
                            "Coin": t.get("ticker", ""),
                            "Datum": t.get("signal_date", ""),
                            "Grade": t.get("grade", 0),
                            "Score": f"{t.get('score', 0)}/{t.get('max_score', 1)}",
                            "Entry": f"${t.get('actual_entry', 0):.4f}",
                            "Exit": f"${t.get('exit_price', 0):.4f}",
                            "P&L": f"{t.get('pnl_pct', 0):+.2f}%",
                            "R": f"{t.get('r_multiple', 0):.1f}R",
                            "Exit Grund": t.get("outcome", ""),
                            "Tage": t.get("bars_held", 0),
                        })
                    st.dataframe(_crypto_df, use_container_width=True)
        else:
            st.info("🔄 Klicke 'Crypto Backtest starten' um die Strategie zu testen.")

        return  # Crypto BI Modus → nicht weitermachen mit Standard

    # =================================================================
    # 🧬 BIOTECH CATALYST BACKTEST
    # =================================================================
    elif bt_mode == "🧬 BioTech Catalyst":
        st.subheader("🧬 BioTech Catalyst — Historischer Backtest")
        st.caption("Testet Catalyst-Proximate Entries auf Biotech-Aktien: Volume Spike + Technical Setup → Momentum Entry")

        col_bio1, col_bio2, col_bio3 = st.columns(3)
        with col_bio1:
            bio_months = st.selectbox("📅 Zeitraum", [3, 6, 12], index=1,
                                       format_func=lambda x: f"{x} Monate", key="bio_months")
        with col_bio2:
            bio_max_tickers = st.selectbox("🎯 Max Tickers", [50, 100, 150], index=1, key="bio_max")
        with col_bio3:
            bio_min_price = st.selectbox("💰 Min Preis", [1.0, 2.0, 5.0], index=1,
                                          format_func=lambda x: f"${x:.0f}", key="bio_min_price")

        st.caption(f"🧬 {len(BIOTECH_BACKTEST_UNIVERSE)} kuratierte Biotech/Pharma Tickers | "
                   f"⏱️ ~{bio_months * 22 // 5 + bio_max_tickers // 15} Min")

        with st.expander("📖 Strategie-Regeln"):
            st.markdown("""
**Entry-Signal:** RVOL ≥ 2.0 (Unusual Volume = Smart Money vor Catalyst) + Technical Score ≥ 10/20 + Aufwärtstrend (SMA20 > SMA50)

**Entry:** Next Day Open nach Signal (Momentum-Einstieg)

**Stop:** 1.5 × ATR₁₀ unter Entry (breiter wegen Biotech-Volatilität)

**Targets (Grade S/A):** TP1 = 2.0R, TP2 = 4.0R | **Grade B/C:** TP1 = 1.5R, TP2 = 3.0R

**Grading:** Technical Score (0-20) + RVOL Bonus (0-10) → S/A/B/C

**Max Hold:** 15 Tage | **Trail-Stop:** Nach TP1 → 66% des Weges Entry→TP1
            """)

        if st.button("🚀 BioTech Backtest starten", type="primary", use_container_width=True, key="bio_bt_start"):
            progress_bar = st.progress(0, text="Starte BioTech Backtest...")

            def update_bio_progress(pct, text):
                progress_bar.progress(min(pct, 1.0), text=text)

            with st.spinner(f"Analysiere bis zu {bio_max_tickers} Biotech-Aktien über {bio_months} Monate..."):
                bio_results = run_biotech_backtest(
                    poly_key,
                    months=bio_months,
                    max_tickers=bio_max_tickers,
                    min_price=bio_min_price,
                    min_volume=100000,
                    progress_callback=update_bio_progress
                )

            st.session_state["bio_backtest_results"] = bio_results

        # Ergebnisse anzeigen
        bio_results = st.session_state.get("bio_backtest_results")
        if bio_results:
            summary = bio_results["summary"]
            stats = bio_results["stats_by_grade"]
            trades = bio_results["trades"]

            st.divider()
            st.subheader("📊 Ergebnisse")

            # Summary Metrics
            col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
            with col_s1:
                st.metric("Signale", summary["total_signals"])
            with col_s2:
                st.metric("Trades", summary["total_filled"])
            with col_s3:
                st.metric("Win Rate", f"{summary.get('win_rate', 0)}%")
            with col_s4:
                _tp = summary["total_pnl"]
                st.metric("Total P&L", f"{_tp:+.1f}%", delta_color="normal" if _tp >= 0 else "inverse")
            with col_s5:
                st.metric("Biotech Tickers", f"{summary.get('n_tickers', 0)}/{summary.get('n_biotech_universe', 0)}")

            st.caption(f"⏱️ {summary.get('months', 0)} Monate | 🧬 {summary.get('n_tickers', 0)} Biotech-Aktien analysiert "
                       f"(von {summary.get('n_tickers_total', 0)} im Markt gefunden)")

            # Grade Breakdown
            if stats:
                st.divider()
                st.subheader("🏆 Performance nach Grade")
                grade_emojis = {"S": "🏆", "A": "🔥", "B": "✅", "C": "⚠️"}

                for g in ["S", "A", "B", "C"]:
                    if g not in stats:
                        continue
                    s = stats[g]
                    emoji = grade_emojis.get(g, "")

                    with st.expander(
                        f"{emoji} Grade {g} — {s['total']} Trades | WR: {s['win_rate']}% | "
                        f"Avg P&L: {s['avg_pnl']:+.2f}% | PF: {s['profit_factor']}",
                        expanded=(g in ("S", "A"))
                    ):
                        gc1, gc2, gc3, gc4, gc5 = st.columns(5)
                        with gc1:
                            st.metric("Trades", s["total"])
                            st.metric("Winners", s["winners"])
                        with gc2:
                            st.metric("Win Rate", f"{s['win_rate']}%")
                            st.metric("Losers", s["losers"])
                        with gc3:
                            st.metric("Avg Winner", f"+{s['avg_winner']:.2f}%")
                            st.metric("Avg Loser", f"{s['avg_loser']:.2f}%")
                        with gc4:
                            st.metric("Profit Factor", f"{s['profit_factor']}")
                            st.metric("Avg R", f"{s['avg_r']:.2f}R")
                        with gc5:
                            st.metric("TP1 Rate", f"{s['tp1_rate']}%")
                            st.metric("TP2 Rate", f"{s['tp2_rate']}%")

                        st.metric("Total P&L", f"{s['total_pnl']:+.2f}%")

            # Trade-Liste
            if trades:
                st.divider()
                filled = [t for t in trades if t.get("outcome") != "NO_FILL"]
                if filled:
                    st.subheader(f"📋 Trade-Liste ({len(filled)} Trades)")
                    _bio_df = []
                    for t in sorted(filled, key=lambda x: x.get("pnl_pct", 0), reverse=True):
                        _bio_df.append({
                            "Ticker": t.get("ticker", ""),
                            "Datum": t.get("signal_date", ""),
                            "Grade": t.get("grade", 0),
                            "Tech Score": f"{t.get('score', 0)}/{t.get('max_score', 1)}",
                            "RVOL": f"{t.get('rvol', 0):.1f}x",
                            "Entry": f"${t.get('actual_entry', 0):.2f}",
                            "Exit": f"${t.get('exit_price', 0):.2f}",
                            "P&L": f"{t.get('pnl_pct', 0):+.2f}%",
                            "R": f"{t.get('r_multiple', 0):.1f}R",
                            "Exit Grund": t.get("outcome", ""),
                            "Tage": t.get("bars_held", 0),
                        })
                    st.dataframe(_bio_df, use_container_width=True)
        else:
            st.info("🔄 Klicke 'BioTech Backtest starten' um die Strategie zu testen.")

        return  # BioTech Modus → nicht weitermachen mit Standard

    # =================================================================
    # 📊 STANDARD STRATEGIEN BACKTEST (original code below)
    # =================================================================
    elif bt_mode == "📊 Standard Strategien":
        pass  # Weiter unten — aber expliziter Guard verhindert Rendering bei falschem Tab
    else:
        st.warning("Unbekannter Backtest-Modus")
        return

    # Einstellungen
    col_set1, col_set2, col_set3 = st.columns(3)

    with col_set1:
        months = st.selectbox("📅 Zeitraum", [1, 3, 6, 9, 12], index=2,
                              format_func=lambda x: f"{x} Monate", key="std_bt_months")

    with col_set2:
        strat_options = list(BACKTEST_STRATEGY_RULES.keys())
        selected_strats = st.multiselect(
            "📋 Strategien",
            strat_options,
            default=strat_options,
            help="Wähle welche Strategien getestet werden sollen",
            key="std_bt_strats"
        )

    with col_set3:
        universe_size = st.selectbox(
            "🌍 Universum",
            ["Klein (30)", "Mittel (75)", "Groß (175)", "🔥 ALLE US-Aktien"],
            index=1,
            help="ALLE US-Aktien nutzt Grouped Daily API (1 Call/Tag → tausende Aktien)",
            key="std_bt_universe"
        )
        if "Klein" in universe_size:
            tickers = BACKTEST_UNIVERSE[:30]
            use_grouped = False
        elif "Mittel" in universe_size:
            tickers = BACKTEST_UNIVERSE[:75]
            use_grouped = False
        elif "Groß" in universe_size:
            tickers = BACKTEST_UNIVERSE
            use_grouped = False
        else:
            tickers = None  # Grouped mode
            use_grouped = True
    
    if use_grouped:
        st.caption("🔥 **ALLE US-Aktien** — Grouped Daily API scannt tausende Aktien pro Tag")
        st.caption(f"⏱️ Ca. {months * 22} API-Calls ({months * 22 // 5} Min bei Free Tier)")
        st.caption("📊 Filter: Preis >$5, Volumen >200k/Tag, keine Leveraged/Inverse ETFs")
    else:
        st.caption(f"Tickers: {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")
    
    # Strategie-Details anzeigen
    with st.expander("📖 Strategie-Regeln"):
        for name, rules in BACKTEST_STRATEGY_RULES.items():
            if name in selected_strats:
                direction = "🟢 LONG" if rules["direction"] == "long" else "🔴 SHORT"
                st.markdown(f"**{direction} {name}**: {rules['description']}")
                entry = rules['entry'].replace('next_open', 'Nächster Tag Open').replace('at_close', 'Signal-Tag Close').replace('prev_high', 'Vortags-High Breakout')
                st.caption(f"Entry: {entry} | Stop: {rules['stop_pct']*100:.0f}% | TP1: {rules['tp1_rr']}R | TP2: {rules['tp2_rr']}R | Max Hold: {rules['max_hold_days']}d")
    
    # Run Button
    if st.button("🚀 Backtest starten", type="primary", use_container_width=True, key="std_bt_start"):
        if not selected_strats:
            st.error("Bitte mindestens eine Strategie auswählen!")
            return
        
        progress_bar = st.progress(0, text="Starte Backtest...")
        status_text = st.empty()
        
        def update_progress(pct, text):
            progress_bar.progress(min(pct, 1.0), text=text)
        
        with st.spinner(f"{'Scanne ALLE US-Aktien' if use_grouped else f'Lade Daten für {len(tickers)} Ticker'} über {months} Monate..."):
            if use_grouped:
                results, n_tickers = run_full_backtest_grouped(
                    poly_key,
                    strategies=selected_strats,
                    months=months,
                    min_price=5.0,
                    min_volume=200000,  # 200k/Tag min → inkl. Mid/Small Caps
                    progress_callback=update_progress
                )
                st.session_state["backtest_n_tickers"] = n_tickers
            else:
                results = run_full_backtest(
                    poly_key,
                    strategies=selected_strats,
                    tickers=tickers,
                    months=months,
                    progress_callback=update_progress
                )
                st.session_state["backtest_n_tickers"] = len(tickers)
                
                # Debug-Info anzeigen
                _total_trades = sum(len(t) for t in results.values())
                if _total_trades == 0 and tickers:
                    _dbg_start = (datetime.now() - timedelta(days=months * 30 + 30)).strftime("%Y-%m-%d")
                    _dbg_end = datetime.now().strftime("%Y-%m-%d")
                    _dbg_bars = fetch_backtest_daily_data(poly_key, tickers[0], _dbg_start, _dbg_end)
                    with st.expander("🔍 Debug: 0 Trades — was ist passiert?", expanded=True):
                        st.write(f"**Test-Ticker:** {tickers[0]} | Zeitraum: {_dbg_start} → {_dbg_end}")
                        st.write(f"**Bars geladen:** {len(_dbg_bars)}")
                        if _dbg_bars:
                            st.write(f"Erster: {_dbg_bars[0]['date']} | Letzter: {_dbg_bars[-1]['date']}")
                            _test_start_str = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
                            _sigs = 0
                            for _i in range(21, len(_dbg_bars)):
                                if _dbg_bars[_i]["date"] < _test_start_str:
                                    continue
                                _m = compute_daily_metrics(_dbg_bars, _i)
                                if _m:
                                    for _sn in selected_strats:
                                        if check_signal(_m, BACKTEST_STRATEGY_RULES[_sn]["signal"]):
                                            _sigs += 1
                                            if _sigs <= 3:
                                                st.write(f"  Signal: {_sn} am {_dbg_bars[_i]['date']} | Chg={_m['change_pct']:.1f}% RVOL={_m['rvol']:.1f} ClosePos={_m['close_pos']:.2f}")
                            st.write(f"**Signale für {tickers[0]}:** {_sigs}")
                        else:
                            st.error(f"⚠️ Polygon liefert KEINE Daten für {tickers[0]}! API-Key prüfen.")
                            # Zeige gespeicherte API-Fehler
                            if hasattr(fetch_backtest_daily_data, '_errors') and fetch_backtest_daily_data._errors:
                                st.write("**API Fehler-Log:**")
                                for _e in fetch_backtest_daily_data._errors:
                                    st.code(_e)
        
        # Ergebnisse in Session State speichern
        st.session_state["backtest_results"] = results
        st.session_state["backtest_months"] = months
        st.session_state["backtest_tickers"] = tickers or []
        
        # Auch auf Disk speichern
        try:
            with open(BACKTEST_RESULTS_FILE, "w") as f:
                json.dump({"results": results, "months": months, "tickers": tickers or []}, f)
        except Exception:
            pass
    
    # Ergebnisse laden (aus Session State oder Disk)
    results = st.session_state.get("backtest_results")
    if results is None:
        try:
            with open(BACKTEST_RESULTS_FILE, "r") as f:
                saved = json.load(f)
                results = saved.get("results")
                st.session_state["backtest_results"] = results
                st.session_state["backtest_months"] = saved.get("months", 6)
                st.session_state["backtest_tickers"] = saved.get("tickers", [])
        except Exception:
            pass
    
    if not results:
        st.info("🔄 Klicke 'Backtest starten' um die Strategien zu testen.")
        return
    
    # === ERGEBNIS-ANZEIGE ===
    st.divider()
    st.subheader("📊 Ergebnisse")
    
    total_trades = sum(len(trades) for trades in results.values())
    total_winners = sum(sum(1 for t in trades if t["is_winner"]) for trades in results.values())
    total_r = sum(sum(t["r_multiple"] for t in trades) for trades in results.values())
    n_tickers = st.session_state.get("backtest_n_tickers", "?")
    
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Trades", total_trades)
    col_m2.metric("Winners", f"{total_winners} ({total_winners/total_trades*100:.0f}%)" if total_trades > 0 else "0")
    col_m3.metric("Total R", f"{total_r:+.1f}R")
    col_m4.metric("Aktien", n_tickers)
    
    # === STRATEGIE-VERGLEICH (RANGLISTE) ===
    st.subheader("🏆 Strategie-Ranking")
    
    strat_stats = {}
    for strat_name, trades in results.items():
        if trades:
            strat_stats[strat_name] = compute_backtest_stats(trades)
    
    if strat_stats:
        # Sortiere nach Profit Factor (bestes Risiko/Ertrag-Verhältnis)
        sorted_strats = sorted(strat_stats.items(), key=lambda x: x[1]["total_r"], reverse=True)
        
        for rank, (strat_name, stats) in enumerate(sorted_strats, 1):
            direction = BACKTEST_STRATEGY_RULES.get(strat_name, {}).get("direction", "long")
            dir_emoji = "🟢" if direction == "long" else "🔴"
            
            # Farbe basierend auf Performance
            if stats.get("total_r", 0) > 5:
                medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "✅"
            elif stats.get("total_r", 0) > 0:
                medal = "🔶"
            else:
                medal = "❌"
            
            with st.expander(f"{medal} #{rank} {dir_emoji} **{strat_name}** — Win Rate: {stats.get('win_rate', 0)}% | Total R: {stats.get('total_r', 0)}R | Trades: {stats['total_trades']}"):
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Win Rate", f"{stats.get('win_rate', 0)}%")
                    st.caption(f"{stats['winners']}W / {stats['losers']}L")
                with col2:
                    st.metric("Avg R", f"{stats.get('avg_r', 0)}R")
                    st.caption(f"Best: {stats['best_r']}R | Worst: {stats['worst_r']}R")
                with col3:
                    st.metric("Profit Factor", f"{stats.get('profit_factor', 0)}")
                    st.caption(f"Avg Win: +{stats['avg_win']}% | Avg Loss: -{stats['avg_loss']}%")
                with col4:
                    st.metric("Total R", f"{stats.get('total_r', 0)}R")
                    st.caption(f"Avg Hold: {stats['avg_hold']} Tage")
                
                # Exit-Verteilung
                st.markdown("**Exit-Verteilung:**")
                exit_cols = st.columns(4)
                with exit_cols[0]:
                    st.caption(f"🎯 TP2: {stats.get('tp2_rate', 0)}%")
                with exit_cols[1]:
                    st.caption(f"✅ TP1+EOD: {stats.get('tp1_rate', 0) - stats.get('tp2_rate', 0):.1f}%")
                with exit_cols[2]:
                    st.caption(f"🔴 Stop: {stats['stop_rate']}%")
                with exit_cols[3]:
                    st.caption(f"⏰ EOD: {stats['eod_rate']}%")
                
                # Einzelne Trades (letzte 10)
                trades = results[strat_name]
                if trades:
                    st.markdown("**Letzte Trades:**")
                    for trade in sorted(trades, key=lambda x: x["signal_date"], reverse=True)[:10]:
                        color = "🟢" if trade["is_winner"] else "🔴"
                        st.caption(
                            f"{color} {trade['ticker']} | {trade['signal_date']} | "
                            f"Entry ${trade['entry_price']} → Exit ${trade['exit_price']} | "
                            f"{trade['exit_reason']} | {trade['pnl_pct']:+.1f}% ({trade['r_multiple']:+.1f}R) | "
                            f"{trade['bars_held']}d"
                        )
    
    # === TOP TICKER ANALYSE ===
    st.subheader("📈 Top Ticker Performance")
    
    all_trades = []
    for strat_name, trades in results.items():
        all_trades.extend(trades)
    
    if all_trades:
        ticker_stats = {}
        for trade in all_trades:
            ticker = trade.get("ticker", "")
            if ticker not in ticker_stats:
                ticker_stats[ticker] = {"trades": 0, "wins": 0, "total_r": 0}
            ticker_stats[ticker]["trades"] += 1
            ticker_stats[ticker]["total_r"] += trade.get("r_multiple", 0)
            if trade["is_winner"]:
                ticker_stats[ticker]["wins"] += 1
        
        sorted_tickers = sorted(ticker_stats.items(), key=lambda x: x[1]["total_r"], reverse=True)
        
        col_best, col_worst = st.columns(2)
        
        with col_best:
            st.markdown("**🏆 Beste Ticker:**")
            for ticker, stats in sorted_tickers[:5]:
                wr = stats.get("wins", 0) / stats["trades"] * 100 if stats["trades"] > 0 else 0
                st.caption(f"✅ **{ticker}**: {stats.get('total_r', 0):+.1f}R | {stats.get('trades', 0)} Trades | {wr:.0f}% WR")
        
        with col_worst:
            st.markdown("**💀 Schlechteste Ticker:**")
            for ticker, stats in sorted_tickers[-5:]:
                wr = stats.get("wins", 0) / stats["trades"] * 100 if stats["trades"] > 0 else 0
                st.caption(f"❌ **{ticker}**: {stats.get('total_r', 0):+.1f}R | {stats.get('trades', 0)} Trades | {wr:.0f}% WR")
    
    # === FAZIT ===
    st.subheader("📋 Fazit")
    
    if strat_stats:
        profitable = [(n, s) for n, s in sorted_strats if s["total_r"] > 0]
        unprofitable = [(n, s) for n, s in sorted_strats if s["total_r"] <= 0]
        
        if profitable:
            best_name, best_stats = profitable[0]
            st.success(f"🏆 **Beste Strategie: {best_name}** — {best_stats.get('win_rate', 0)}% Win Rate, {best_stats.get('total_r', 0)}R Total, PF {best_stats.get('profit_factor', 0)}")
        
        if len(profitable) > 1:
            st.info(f"✅ **{len(profitable)} profitable Strategien**: {', '.join(n for n, _ in profitable)}")
        
        if unprofitable:
            st.warning(f"❌ **{len(unprofitable)} unprofitable**: {', '.join(n for n, _ in unprofitable)} — diese Strategien überdenken!")
        
        # Empfehlung
        st.markdown("---")
        st.markdown("**💡 Empfehlung:**")
        if profitable:
            best_pf = max(profitable, key=lambda x: x[1].get("profit_factor", 0))
            best_wr = max(profitable, key=lambda x: x[1]["win_rate"])
            st.caption(f"🎯 Bestes Risiko/Ertrag: **{best_pf[0]}** (PF {best_pf[1]['profit_factor']})")
            st.caption(f"🎯 Höchste Win Rate: **{best_wr[0]}** ({best_wr[1]['win_rate']}%)")
            st.caption(f"💰 Fokus auf die Top-3 Strategien und vermeide unprofitable Setups")


# =============================================================================
# INTERNATIONALE BÖRSEN - NUR Aktien OHNE US-Listing (kein ADR auf NYSE/NASDAQ)
# =============================================================================
INTERNATIONAL_STOCKS = {
    "DE": {  # Deutschland XETRA — DAX 40 + MDAX + SDAX (ohne US-ADRs)
        "suffix": ".DE",
        "name": "Deutschland (XETRA)",
        "stocks": [
            # DAX 40 (ohne SAP, SIE, ALV, DTE, BAS, BAYN, BMW, VOW3, MBG, ADS, IFX, DB1, MUV2, HEN3, RWE, EON, FRE, FME, HEI, BEI, DBK)
            "MRK", "DHL", "CON", "LIN", "VNA", "SRT3", "1COV", "MTX", "SY1", "PUM",
            "ZAL", "ENR", "HFG", "LEG", "AIR", "EVK", "DHER", "RHM", "SHL", "QIA",
            # MDAX
            "TKA", "WCH", "BNR", "GXI", "SZG", "AFX", "NDA", "KGX", "G1A",
            "FPE", "AG1", "HOT", "HAB", "NDX1", "BOSS", "TEG", "VBK",
            "DUE", "KBX", "BC8", "WAF", "EVD", "GFT", "AT1", "AIXA", "NEM",
            "JEN", "PSM", "KWS", "ECV", "UTDI", "O2D", "SOW", "PNE", "FNTN", "SAX",
            "LXS", "CBK", "CE2", "TTK", "GIL", "NCA", "KRN", "KCO",
            "TLX", "PAH3", "NOEJ", "SANT", "FAA",
            # SDAX Top
            "S92", "BYW6", "WAC", "VAR1", "AOX", "G24", "DEQ", "HBH", "CLR",
            "FEV", "MDG", "SBS", "HHFA", "DRW", "AAD", "MOR", "VOS", "ADD", "BSL",
            # Small Caps / günstige Aktien (unter ~50€)
            "TUI1", "SDF", "HDD", "BIO3", "WUW", "INH", "DBAN", "TMV", "DTG", "SFQ",
            "GYC", "DLX", "SZU", "BDT", "SMHN", "RHK", "ACX", "ADV", "CEC",
            "HAW", "JUN3", "SYZ", "NB2"
        ]
    },
    "UK": {  # London Stock Exchange — FTSE 100 + 250 (ohne US-ADRs)
        "suffix": ".L",
        "name": "UK (London)",
        "stocks": [
            # FTSE 100 (ohne SHEL, AZN, HSBA, ULVR, BP, GSK, RIO, DGE, BATS, REL, VOD, PRU, BHP, GLEN, CRH, LSEG, AAL, BA, NXT, STAN, IHG, WPP, FERG, RR)
            "NG", "LLOY", "BARC", "RKT", "IMB", "SSE", "AHT", "ABF", "NWG", "EXPN",
            "SMT", "III", "ANTO", "LAND", "SGE", "PSON", "INF", "JD", "TSCO", "SBRY",
            "RMV", "MNDI", "FRES", "KGF", "WEIR", "PSN", "SMIN", "BNZL", "AV",
            "CPG", "BKG", "SDR", "HLMA", "ADM", "ITRK", "MRO", "CRDA", "RTO", "TW",
            "LGEN", "DCC", "BRBY", "IAG", "AUTO", "SGRO", "SN", "WTB", "SPX",
            "SVT", "EVR", "HMSO", "AVV", "HSX", "DPLM", "FCIT",
            "ENT", "JMAT", "GBG", "SJP", "CNA", "PHNX",
            # FTSE 250 Top
            "HIK", "CCH", "FOUR", "OCDO", "PAGE", "SMWH", "BME", "RSHW",
            "POL", "RSW", "ASC", "ATST", "TPK", "BOO", "AJB",
            "INCH", "TRIG", "VOF", "JEO", "NAS", "AML",
            "WIZZ", "DOCS", "TUI", "STVG", "HGT", "BGFD",
            "IPO", "DIGS", "MGGT", "AGR", "BNKR", "HBR", "IGG",
            "VCT", "CINE", "DTY", "RSE", "SCT", "FUTR", "VEIL", "BVIC",
            # FTSE Small Cap / günstige Aktien
            "CARD", "MOON", "SHOE", "STEM", "GFRD", "CMCX", "RWS", "PETS", "SHED",
            "PHP", "BBOX", "FDEV", "CURY", "MTRO", "OXIG", "FSTA", "TRN", "SNWS",
            "AVON", "MTO", "LUCE", "ITV", "EZJ", "FAN", "DNLM", "MARS", "GENL",
            "OXB", "TBCG", "OSB", "MONY", "ASHM", "WINE", "TATE", "SSPG", "EDV",
            "RWA", "SUPR", "PZC", "SRP", "ALFA", "WIX", "BREE", "COST", "RNWH",
            "WOSG", "ABDN", "AO"
        ]
    },
    "CH": {  # Schweiz SIX — SMI + SPI Mid + Small (ohne US-ADRs, Fokus günstig)
        "suffix": ".SW",
        "name": "Schweiz (SIX)",
        "stocks": [
            # SMI ohne US-ADRs (NESN, ROG, NOVN, UBSG, ZURN, ABBN, SREN, GIVN, LONN, SIKA, CFR, ALC, LOGN, GEBN, HOLN)
            "SCMN", "PGHN", "SLHN", "BALN", "SGSN",
            # SPI Mid
            "SOON", "TEMN", "VACN", "BARN", "STMN", "SCHP", "LISN", "SIGN",
            "MBTN", "EMMN", "DKSH", "BUCN", "SFZN", "BCVN", "BEKN", "CERN", "TIBN",
            "COTN", "BELL", "SQN", "MOBN", "HUBN", "VIFN", "AUTN",
            "ASWN", "ZEHN", "GBMN", "HIAG", "ORON", "BOSN", "SENS", "CLTN",
            "EFGN", "ARBN", "BANB", "CPHN", "ACCN", "PEHN", "APTS",
            # SPI Small / günstige Aktien (unter ~200 CHF)
            "SANN", "LAND", "MEDX", "PEAN", "IMPN", "BSLN", "OERL", "MIKN", "FTON",
            "KOMN", "ARYN", "SKAN", "VETN", "SWON", "CALN", "CMBN", "LUKN",
            "GURN", "MCHN", "BAER", "UBXN", "OFN", "SFPN", "SRAIL", "STGN", "WKBN"
        ]
    },
    "EU": {  # Euronext — CAC 40 + AEX 25 + BEL20 (ohne US-ADRs)
        "suffix": ".PA",
        "name": "Europa (Euronext)",
        "stocks": [
            # CAC 40 (ohne MC, OR, TTE, SAN, AIR, SU, BNP, DG, SAF, KER, STM, PUB, VIV, RNO)
            "AI", "CS", "BN", "CA", "CAP", "EN", "GLE", "SGO",
            "ML", "LR", "FP", "HO", "EL", "VIE", "URW",
            "ERF", "ALO", "TKO", "BOL", "ACA", "ALD", "GFC",
            "ORA", "ENX", "SOI", "RI",
            # AEX 25 Amsterdam (ohne ASML, INGA, PHIA, UNA, ADYEN, PRX)
            "AD.AS", "HEIA.AS", "WKL.AS", "RAND.AS", "AKZA.AS",
            "ABN.AS", "AGN.AS", "BESI.AS", "ASM.AS", "IMCD.AS", "KPN.AS",
            "AALB.AS", "SBMO.AS", "LIGHT.AS", "FLOW.AS",
            # BEL20 Top (Brussels)
            "UCB.BR", "SOLB.BR", "ABI.BR", "KBC.BR", "GBLB.BR", "AGS.BR", "ACKB.BR", "COFB.BR",
            # Extra Paris
            "NEX", "IPS", "AM", "AF", "VK", "RAL",
            # Paris Small/Mid Cap (günstig)
            "ATO", "COFA", "MERY", "SMCP", "CBOT", "ELIS", "FNAC", "ICAD", "JCQ",
            "TFI", "BVI", "DBV", "GET", "JBOG", "LNA", "MAU", "NRG", "OVH", "QDT",
            "UBI", "WLN", "ELIOR", "NANO", "THEP", "RCO",
            # Amsterdam Small/Mid Cap
            "ARCAD.AS", "CRBN.AS", "HEIJM.AS", "JDEP.AS", "OCI.AS", "PHARM.AS",
            "TWEKA.AS", "WHA.AS", "ALFEN.AS", "BFIT.AS", "CTPNV.AS", "NSI.AS",
            "TOM2.AS", "VPK.AS"
        ]
    },
    "JP": {  # Tokyo Stock Exchange — Nikkei 225 (ohne US-ADRs)
        "suffix": ".T",
        "name": "Japan (Tokyo)",
        "stocks": [
            # Nikkei 225 Top (ohne 7203/Toyota, 6758/Sony, 9984/SoftBank, 8306/MUFG, 6902/Denso, 6861/Keyence,
            # 7741/HOYA, 6501/Hitachi, 8316/SMFG, 6098/Recruit, 4063/Shin-Etsu, 6367/Daikin, 9432/NTT,
            # 4502/Takeda, 7974/Nintendo, 8058/Mitsubishi, 8035/Tokyo Electron, 6954/Fanuc, 4661/Oriental Land,
            # 6981/Murata, 7267/Honda, 8766/Tokio Marine, 6594/Nidec)
            "9433", "7751", "3382", "8801", "4503", "7201", "9434", "7270", "6752", "8411", "7733",
            "5108", "8031", "4519", "6301", "9020", "4568", "2914", "8802",
            "4452", "6273", "2801", "9983", "6326", "4543", "3407", "7269",
            "6971", "8591", "2502", "8725", "4901", "6762", "4507", "9022", "4704", "7832",
            "3289", "4911", "8601", "6103", "9064", "6472", "5401", "8830", "7211", "4578",
            "9613", "3086", "6503", "2413", "6504", "7731", "4612", "6645", "5802", "5713",
            "8309", "4755", "8015", "6753", "7011", "7013", "5020", "8053", "4042", "7912",
            "4188", "8002", "6988", "8267", "6471", "1925", "5201", "2768", "8354", "6479"
        ]
    },
    "HK": {  # Hong Kong — Hang Seng (ohne US-ADRs)
        "suffix": ".HK",
        "name": "Hong Kong",
        "stocks": [
            # Hang Seng (ohne 0700/Tencent, 9988/Alibaba, 0941/ChinaMobile, 1299/AIA, 2318/PingAn,
            # 3690/Meituan, 9618/JD, 9888/Baidu, 0005/HSBC, 1810/Xiaomi, 9999/NetEase, 0883/CNOOC,
            # 2020/ANTA, 0388/HKEX, 1024/KEHoldings, 2269/WuXiBio, 9961/Trip, 9626/Bilibili)
            "0939", "1398", "2628", "1211", "0027", "1038", "2382", "0011", "0016", "0001",
            "0066", "3988", "0267", "0669", "1928", "0175", "0002", "0012", "0003", "0688",
            "0386", "1113", "0823", "0006", "1997", "0019", "0960", "1109", "0762", "0017",
            "0288", "2331", "2388", "6098", "2007", "1177", "3968", "2313",
            "1088", "2899", "0857", "1876", "6862", "2688", "1658", "0981",
            "0868", "1071", "0316", "0992", "2319", "0551", "0241", "0101", "0014", "0836",
            "1357", "2328", "6060", "1833", "2018", "6186", "0291", "3328", "0778",
            "1099", "2196", "9633", "0135", "2057", "1093", "0144", "3692", "0293", "0151",
            "6969", "1972", "0489", "2600", "9698", "0522", "0853", "9926", "6618", "1476"
        ]
    }
}

# =============================================================================
# FUTURES - Kontrakt-Listen nach Kategorie
# =============================================================================
FUTURES_CONTRACTS = {
    "INDEX": {
        "name": "Index Futures",
        "contracts": [
            ("ES=F", "S&P 500 E-mini"),
            ("NQ=F", "Nasdaq 100 E-mini"),
            ("YM=F", "Dow Jones E-mini"),
            ("RTY=F", "Russell 2000 E-mini"),
            ("NKD=F", "Nikkei 225"),
            ("FCHI=F", "CAC 40"),
            ("GDAXI=F", "DAX"),
            ("FTSE=F", "FTSE 100"),
            ("HSI=F", "Hang Seng"),
            ("VIX=F", "VIX Volatility"),
        ]
    },
    "ENERGY": {
        "name": "Energie Futures",
        "contracts": [
            ("CL=F", "Crude Oil WTI"),
            ("BZ=F", "Brent Crude"),
            ("NG=F", "Natural Gas"),
            ("HO=F", "Heating Oil"),
            ("RB=F", "RBOB Gasoline"),
        ]
    },
    "METALS": {
        "name": "Metall Futures",
        "contracts": [
            ("GC=F", "Gold"),
            ("SI=F", "Silber"),
            ("PL=F", "Platin"),
            ("PA=F", "Palladium"),
            ("HG=F", "Kupfer"),
        ]
    },
    "AGRI": {
        "name": "Agrar Futures",
        "contracts": [
            ("ZC=F", "Mais (Corn)"),
            ("ZW=F", "Weizen (Wheat)"),
            ("ZS=F", "Sojabohnen"),
            ("KC=F", "Kaffee"),
            ("CT=F", "Baumwolle"),
            ("SB=F", "Zucker"),
            ("CC=F", "Kakao"),
            ("OJ=F", "Orangensaft"),
            ("LBS=F", "Holz (Lumber)"),
            ("LE=F", "Live Cattle"),
        ]
    },
    "RATES": {
        "name": "Zins Futures",
        "contracts": [
            ("ZB=F", "30-Year T-Bond"),
            ("ZN=F", "10-Year T-Note"),
            ("ZF=F", "5-Year T-Note"),
            ("ZT=F", "2-Year T-Note"),
            ("GE=F", "Eurodollar"),
        ]
    }
}

# =============================================================================
# FOREX - Währungspaare nach Kategorie
# =============================================================================
FOREX_PAIRS = {
    "MAJORS": {
        "name": "Major Pairs",
        "pairs": [
            ("EURUSD=X", "EUR/USD"),
            ("GBPUSD=X", "GBP/USD"),
            ("USDJPY=X", "USD/JPY"),
            ("USDCHF=X", "USD/CHF"),
            ("AUDUSD=X", "AUD/USD"),
            ("USDCAD=X", "USD/CAD"),
            ("NZDUSD=X", "NZD/USD"),
        ]
    },
    "MINORS": {
        "name": "Minor Pairs",
        "pairs": [
            ("EURGBP=X", "EUR/GBP"),
            ("EURJPY=X", "EUR/JPY"),
            ("GBPJPY=X", "GBP/JPY"),
            ("EURCHF=X", "EUR/CHF"),
            ("EURAUD=X", "EUR/AUD"),
            ("EURCAD=X", "EUR/CAD"),
            ("GBPCHF=X", "GBP/CHF"),
            ("GBPAUD=X", "GBP/AUD"),
            ("AUDJPY=X", "AUD/JPY"),
            ("CADJPY=X", "CAD/JPY"),
            ("CHFJPY=X", "CHF/JPY"),
            ("AUDNZD=X", "AUD/NZD"),
            ("AUDCAD=X", "AUD/CAD"),
            ("NZDJPY=X", "NZD/JPY"),
        ]
    },
    "EXOTICS": {
        "name": "Exotic Pairs",
        "pairs": [
            ("USDTRY=X", "USD/TRY"),
            ("USDZAR=X", "USD/ZAR"),
            ("USDMXN=X", "USD/MXN"),
            ("USDBRL=X", "USD/BRL"),
            ("USDPLN=X", "USD/PLN"),
            ("USDSEK=X", "USD/SEK"),
            ("USDNOK=X", "USD/NOK"),
            ("USDDKK=X", "USD/DKK"),
            ("USDSGD=X", "USD/SGD"),
            ("USDHKD=X", "USD/HKD"),
            ("USDCNH=X", "USD/CNH"),
            ("USDINR=X", "USD/INR"),
            ("EURTRY=X", "EUR/TRY"),
            ("EURZAR=X", "EUR/ZAR"),
        ]
    }
}

#  KEIN CACHE - Filter kommen aus session_state
def fetch_futures_data(category):
    """
    Holt Futures-Daten via Yahoo Finance API.
    
    Args:
        category: "INDEX", "ENERGY", "METALS", "AGRI", "RATES"
    
    Returns:
        Liste von Futures-Daten
    """
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    
    if category not in FUTURES_CONTRACTS:
        return [], 0, 0
    
    contracts = FUTURES_CONTRACTS[category]["contracts"]
    
    f = st.session_state.active_filters
    af = st.session_state.additional_filters
    
    try:
        # Get current strategy name for VIX filtering
        current_strategy = st.session_state.get("current_strategy", "")

        for ticker, name in contracts:
            try:
                # VIX Spike nur fuer VIX-Futures
                if "VIX" in current_strategy and "VIX" not in ticker.upper() and "VX" not in ticker.upper():
                    skipped_filter += 1
                    continue

                # Yahoo Finance API Query
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                params = {"interval": "1d", "range": "5d"}
                headers = {"User-Agent": "Mozilla/5.0"}

                resp = rate_limited_get(url, params=params, headers=headers, timeout=10)
                if resp.status_code != 200:
                    skipped_no_price += 1
                    continue

                data = resp.json()
                chart = data.get("chart", {}).get("result", [])
                if not chart:
                    skipped_no_price += 1
                    continue

                quote = chart[0]
                meta = quote.get("meta", {})
                indicators = quote.get("indicators", {}).get("quote", [{}])[0]

                price = meta.get("regularMarketPrice", 0)
                prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)

                if price <= 0:
                    skipped_no_price += 1
                    continue

                # OHLCV
                closes = [c for c in indicators.get("close", []) if c is not None]
                highs = [h for h in indicators.get("high", []) if h is not None]
                lows = [l for l in indicators.get("low", []) if l is not None]
                volumes = [v for v in indicators.get("volume", []) if v is not None]

                if len(closes) < 2:
                    skipped_no_price += 1
                    continue

                yesterday_close = closes[-2] if len(closes) >= 2 else prev_close
                today_high = highs[-1] if highs else price
                today_low = lows[-1] if lows else price
                today_vol = volumes[-1] if volumes else 0
                yesterday_vol = volumes[-2] if len(volumes) >= 2 else today_vol

                change = ((price - yesterday_close) / yesterday_close * 100) if yesterday_close > 0 else 0

                if len(closes) >= 3:
                    vortag_chg = ((yesterday_close - closes[-3]) / closes[-3] * 100) if closes[-3] > 0 else 0
                else:
                    vortag_chg = 0

                rvol = round(today_vol / yesterday_vol, 2) if yesterday_vol > 0 else 1.0
                rvol = min(rvol, 999.0)

                close_pos = calculate_close_position(today_high, today_low, price)

                # Filter-Logik (V67.3: +Vortag% Filter - fehlte komplett!)
                match = True
                if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): match = False
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): match = False
                if af.get("nur_gewinner") and change <= 0: match = False
                if af.get("nur_verlierer") and change >= 0: match = False
                
                if not match:
                    skipped_filter += 1
                    continue
                
                alpha = calculate_alpha_score(rvol, vortag_chg, change)
                
                results.append({
                    "Ticker": ticker.replace("=F", ""),
                    "Name": name,
                    "Preis": round(price, 2),
                    "Chg%": round(change, 2),
                    "RVOL": rvol,
                    "Vortag%": round(vortag_chg, 2),
                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5,
                    "Alpha": alpha,
                    "Category": FUTURES_CONTRACTS[category]["name"],
                    "FullTicker": ticker,
                })
                
            except Exception as e:
                continue
        
        return results, skipped_no_price, skipped_filter
        
    except Exception as e:
        st.error(f"Futures Fehler: {e}")
        return [], 0, 0

#  KEIN CACHE - Filter kommen aus session_state
def fetch_forex_data(category):
    """
    Holt Forex-Daten via Yahoo Finance API.
    
    Args:
        category: "MAJORS", "MINORS", "EXOTICS"
    
    Returns:
        Liste von Forex-Daten
    """
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    _debug_log = []  # DEBUG: Per-ticker log
    
    if category not in FOREX_PAIRS:
        _debug_log.append(f"❌ category '{category}' not in FOREX_PAIRS")
        return [], 0, 0, _debug_log
    
    pairs = FOREX_PAIRS[category]["pairs"]
    
    f = st.session_state.active_filters
    af = st.session_state.additional_filters
    _debug_log.append(f"📋 {len(pairs)} pairs | f={f} | af_gew={af.get('nur_gewinner')} af_verl={af.get('nur_verlierer')}")
    
    try:
        for ticker, name in pairs:
            try:
                # Yahoo Finance API Query
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                params = {"interval": "1d", "range": "5d"}
                headers = {"User-Agent": "Mozilla/5.0"}
                
                resp = rate_limited_get(url, params=params, headers=headers, timeout=10)
                if resp.status_code != 200:
                    skipped_no_price += 1
                    continue
                
                data = resp.json()
                chart = data.get("chart", {}).get("result", [])
                if not chart:
                    skipped_no_price += 1
                    continue
                
                quote = chart[0]
                meta = quote.get("meta", {})
                indicators = quote.get("indicators", {}).get("quote", [{}])[0]
                
                price = meta.get("regularMarketPrice", 0)
                prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
                
                if price <= 0:
                    skipped_no_price += 1
                    continue
                
                # OHLCV
                closes = [c for c in indicators.get("close", []) if c is not None]
                highs = [h for h in indicators.get("high", []) if h is not None]
                lows = [l for l in indicators.get("low", []) if l is not None]
                
                if len(closes) < 2:
                    skipped_no_price += 1
                    continue
                
                yesterday_close = closes[-2] if len(closes) >= 2 else prev_close
                today_high = highs[-1] if highs else price
                today_low = lows[-1] if lows else price
                
                change = ((price - yesterday_close) / yesterday_close * 100) if yesterday_close > 0 else 0
                
                if len(closes) >= 3:
                    vortag_chg = ((yesterday_close - closes[-3]) / closes[-3] * 100) if closes[-3] > 0 else 0
                else:
                    vortag_chg = 0
                
                close_pos = calculate_close_position(today_high, today_low, price)
                
                # Pip-Berechnung (für Forex relevant)
                # Für JPY-Paare: 1 Pip = 0.01, sonst 0.0001
                if "JPY" in ticker:
                    pip_value = 0.01
                    pip_change = (price - yesterday_close) / pip_value
                else:
                    pip_value = 0.0001
                    pip_change = (price - yesterday_close) / pip_value
                
                # Filter-Logik (V67.3: +Vortag%, +best_pairs Filter)
                match = True
                _fail_reason = ""
                
                # Strategie-spezifische best_pairs Filterung
                current_strat = st.session_state.get("current_strategy", "")
                strat_def = FOREX_STRATEGIES.get(current_strat, {})
                best_pairs = strat_def.get("best_pairs", [])
                
                if best_pairs:
                    # best_pairs Format: "USDJPY", ticker Format: "USDJPY=X"
                    ticker_clean = ticker.replace("=X", "")
                    if ticker_clean not in best_pairs:
                        match = False
                        _fail_reason = f"best_pairs filter"
                
                if match and "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): 
                    match = False
                    _fail_reason = f"Change% {change:.4f} not in {f['Change %']}"
                if match and "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): 
                    match = False
                    _fail_reason = f"Vortag% {vortag_chg:.4f} not in {f['Vortag %']}"
                if match and af.get("nur_gewinner") and change <= 0: 
                    match = False
                    _fail_reason = "nur_gewinner"
                if match and af.get("nur_verlierer") and change >= 0: 
                    match = False
                    _fail_reason = "nur_verlierer"
                
                _debug_log.append(f"{'✅' if match else '❌'} {name}: chg={change:+.4f}% vtg={vortag_chg:+.4f}% {'| ' + _fail_reason if _fail_reason else ''}")
                
                if not match:
                    skipped_filter += 1
                    continue
                
                alpha = abs(change) * 20  # Forex hat kleinere Moves, daher *20
                
                results.append({
                    "Ticker": name,
                    "Name": "",
                    "Preis": round(price, 5),  # 5 Dezimalstellen für Forex
                    "Chg%": round(change, 3),
                    "Pips": round(pip_change, 1),
                    "Vortag%": round(vortag_chg, 3),
                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5,
                    "Alpha": round(alpha, 0),
                    "Category": FOREX_PAIRS[category]["name"],
                    "FullTicker": ticker,
                })
                
            except Exception as e:
                _debug_log.append(f"💥 {ticker}: {e}")
                continue
        
        return results, skipped_no_price, skipped_filter, _debug_log
        
    except Exception as e:
        _debug_log.append(f"💥 OUTER: {e}")
        st.error(f"Forex Fehler: {e}")
        return [], 0, 0, _debug_log

#  KEIN CACHE - Filter kommen aus session_state
def fetch_international_stock_data(exchange_code):
    """
    Holt Aktien-Daten von internationalen Börsen via Yahoo Finance API.
    
    Args:
        exchange_code: "DE", "UK", "CH", "EU", "JP", "HK"
    
    Returns:
        Liste von Aktien-Daten
    """
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    _debug_log = []
    
    if exchange_code not in INTERNATIONAL_STOCKS:
        _debug_log.append(f"❌ exchange_code '{exchange_code}' not in INTERNATIONAL_STOCKS")
        return [], 0, 0, _debug_log
    
    exchange = INTERNATIONAL_STOCKS[exchange_code]
    suffix = exchange["suffix"]
    stocks = exchange["stocks"]
    _debug_log.append(f"📋 {len(stocks)} stocks, suffix={suffix}")
    
    f = st.session_state.active_filters
    af = st.session_state.additional_filters
    _debug_log.append(f"🔍 f={f}")
    _debug_log.append(f"🔍 af={af}")
    af = st.session_state.additional_filters
    
    try:
        for ticker_base in stocks:
            try:
                # Ticker mit Suffix (außer wenn schon vorhanden)
                if "." in ticker_base:
                    ticker = ticker_base
                else:
                    ticker = f"{ticker_base}{suffix}"
                
                # Yahoo Finance API Query
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                params = {
                    "interval": "1d",
                    "range": "5d"
                }
                headers = {"User-Agent": "Mozilla/5.0"}
                
                resp = rate_limited_get(url, params=params, headers=headers, timeout=10)
                if resp.status_code != 200:
                    skipped_no_price += 1
                    continue
                
                data = resp.json()
                
                # Parse Yahoo Finance Response
                chart = data.get("chart", {}).get("result", [])
                if not chart:
                    skipped_no_price += 1
                    continue
                
                quote = chart[0]
                meta = quote.get("meta", {})
                indicators = quote.get("indicators", {}).get("quote", [{}])[0]
                
                # Aktuelle Daten
                price = meta.get("regularMarketPrice", 0)
                prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
                
                if price <= 0:
                    skipped_no_price += 1
                    continue
                
                # OHLCV aus den letzten Kerzen
                closes = indicators.get("close", [])
                opens = indicators.get("open", [])
                highs = indicators.get("high", [])
                lows = indicators.get("low", [])
                volumes = indicators.get("volume", [])
                
                # Filtere None-Werte
                closes = [c for c in closes if c is not None]
                opens = [o for o in opens if o is not None]
                highs = [h for h in highs if h is not None]
                lows = [l for l in lows if l is not None]
                volumes = [v for v in volumes if v is not None]
                
                if len(closes) < 2:
                    skipped_no_price += 1
                    continue
                
                # Berechnungen
                today_close = closes[-1] if closes else price
                today_open = opens[-1] if opens else price
                today_high = highs[-1] if highs else price
                today_low = lows[-1] if lows else price
                today_vol = volumes[-1] if volumes else 0
                
                yesterday_close = closes[-2] if len(closes) >= 2 else prev_close
                yesterday_vol = volumes[-2] if len(volumes) >= 2 else today_vol
                
                # Change %
                change = ((price - yesterday_close) / yesterday_close * 100) if yesterday_close > 0 else 0
                
                # Vortag Change (Tag davor)
                if len(closes) >= 3:
                    day_before = closes[-3]
                    vortag_chg = ((yesterday_close - day_before) / day_before * 100) if day_before > 0 else 0
                else:
                    vortag_chg = 0
                
                # RVOL — Normalisiert nach Tageszeit (Markt evtl. noch offen!)
                # Problem: Wenn DE-Markt um 11:00 Uhr gescannt wird, ist today_vol
                # nur ~25% des Tages-Volumens → RVOL sieht aus wie 0.25 statt 1.0
                from datetime import datetime
                import pytz
                
                # Handelszeiten pro Exchange (Stunden)
                _exchange_hours = {
                    "DE": {"tz": "Europe/Berlin", "open_h": 9, "open_m": 0, "close_h": 17, "close_m": 30},
                    "UK": {"tz": "Europe/London", "open_h": 8, "open_m": 0, "close_h": 16, "close_m": 30},
                    "CH": {"tz": "Europe/Zurich", "open_h": 9, "open_m": 0, "close_h": 17, "close_m": 30},
                    "EU": {"tz": "Europe/Paris", "open_h": 9, "open_m": 0, "close_h": 17, "close_m": 30},
                    "JP": {"tz": "Asia/Tokyo", "open_h": 9, "open_m": 0, "close_h": 15, "close_m": 0},
                    "HK": {"tz": "Asia/Hong_Kong", "open_h": 9, "open_m": 30, "close_h": 16, "close_m": 0},
                }
                
                raw_rvol = today_vol / yesterday_vol if yesterday_vol > 0 else 1.0
                
                # Normalisierung: Wie viel % des Handelstages ist vorbei?
                day_pct = 1.0  # Default: ganzer Tag (Markt geschlossen)
                _ex_info = _exchange_hours.get(exchange_code)
                if _ex_info:
                    try:
                        _tz = pytz.timezone(_ex_info["tz"])
                        _now = datetime.now(_tz)
                        _now_min = _now.hour * 60 + _now.minute
                        _open_min = _ex_info["open_h"] * 60 + _ex_info["open_m"]
                        _close_min = _ex_info["close_h"] * 60 + _ex_info["close_m"]
                        _total_min = _close_min - _open_min
                        
                        if _now_min < _open_min:
                            day_pct = 0.05  # Vor Eröffnung
                        elif _now_min >= _close_min:
                            day_pct = 1.0   # Nach Schluss
                        else:
                            _elapsed = _now_min - _open_min
                            day_pct = max(0.10, _elapsed / _total_min)
                    except Exception:
                        day_pct = 1.0
                
                # Normalisiertes RVOL: Wenn 25% des Tages vorbei und 25% Vol → RVOL ~1.0
                rvol = round(raw_rvol / day_pct, 2) if day_pct > 0 else raw_rvol
                rvol = min(rvol, 999.0)
                
                # Close Position
                close_pos = calculate_close_position(today_high, today_low, price)
                
                # Wick Berechnungen (mit min_range_pct Check für Konsistenz)
                candle_range = today_high - today_low if today_high > today_low else 0
                range_pct = (candle_range / today_low * 100) if today_low > 0 else 0
                
                if range_pct >= 0.5 and candle_range > 0:
                    body_top = max(today_open, today_close)
                    body_bottom = min(today_open, today_close)
                    upper_wick_pct = ((today_high - body_top) / candle_range) * 100
                    lower_wick_pct = ((body_bottom - today_low) / candle_range) * 100
                else:
                    upper_wick_pct = 0
                    lower_wick_pct = 0
                
                # ATR
                atr_pct = calculate_atr_from_ohlc(today_high, today_low, today_close, yesterday_close)
                volatility_regime, _ = get_volatility_regime(atr_pct)
                
                # Dollar Volume
                dollar_volume = today_vol * price
                is_liquid = dollar_volume >= 50000
                
                # FILTER-LOGIK
                match = True
                _fail_reason = ""
                
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if not (rvol_min <= rvol <= rvol_max): 
                        match = False
                        _fail_reason = f"RVOL {rvol:.2f} not in ({rvol_min},{rvol_max})"
                
                if match and "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): 
                    match = False
                    _fail_reason = f"Change% {change:.2f} not in {f['Change %']}"
                if match and "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): 
                    match = False
                    _fail_reason = f"Vortag% {vortag_chg:.2f} not in {f['Vortag %']}"
                if match and "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): 
                    match = False
                    _fail_reason = f"Preis {price:.2f} not in {f['Preis']}"
                
                # Close Position - mit None Check
                if match and "Close Position" in f:
                    if close_pos is not None:
                        if not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): 
                            match = False
                            _fail_reason = f"ClosePos {close_pos:.2f} not in {f['Close Position']}"
                
                if match and "Upper Wick %" in f and not (f["Upper Wick %"][0] <= upper_wick_pct <= f["Upper Wick %"][1]): 
                    match = False
                    _fail_reason = f"UpperWick"
                if match and "Lower Wick %" in f and not (f["Lower Wick %"][0] <= lower_wick_pct <= f["Lower Wick %"][1]): 
                    match = False
                    _fail_reason = f"LowerWick"
                
                if match and af.get("preis_min", 0) > 0 and price < af["preis_min"]: 
                    match = False
                    _fail_reason = f"af.preis_min {af['preis_min']}"
                if match and af.get("preis_max", 100000) < 100000 and price > af["preis_max"]: 
                    match = False
                    _fail_reason = f"af.preis_max {af['preis_max']}"
                if match and af.get("nur_gewinner") and change <= 0: 
                    match = False
                    _fail_reason = "af.nur_gewinner"
                if match and af.get("nur_verlierer") and change >= 0: 
                    match = False
                    _fail_reason = "af.nur_verlierer"
                
                _debug_log.append(f"{'✅' if match else '❌'} {ticker}: €{price:.2f} chg={change:+.2f}% rvol={rvol:.2f} {_fail_reason}")
                
                if not match:
                    skipped_filter += 1
                    continue
                
                # Clean Ticker für Anzeige
                display_ticker = ticker_base if ticker_base else ticker.replace(suffix, "")
                
                alpha = calculate_alpha_score(rvol, vortag_chg, change)
                
                results.append({
                    "Ticker": display_ticker,
                    "Name": meta.get("shortName", "")[:15] if meta.get("shortName") else "",
                    "Preis": round(price, 2),
                    "Chg%": round(change, 2),
                    "RVOL": rvol,
                    "Vortag%": round(vortag_chg, 2),
                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5,
                    "Alpha": alpha,
                    "Gap%": 0,
                    "UpperWick%": round(upper_wick_pct, 1),
                    "LowerWick%": round(lower_wick_pct, 1),
                    "ATR%": atr_pct,
                    "VolRegime": volatility_regime,
                    "DollarVol": dollar_volume,
                    "IsLiquid": is_liquid,
                    "Currency": meta.get("currency", ""),
                    "Exchange": exchange["name"],
                    "FullTicker": ticker,
                })
                
            except Exception as e:
                _debug_log.append(f"💥 {ticker}: {e}")
                continue
        
        return results, skipped_no_price, skipped_filter, _debug_log
        
    except Exception as e:
        _debug_log.append(f"💥 OUTER: {e}")
        st.error(f"Yahoo Finance Fehler: {e}")
        return [], 0, 0, _debug_log

# =============================================================================
# 5. STREAMLIT UI
# =============================================================================
st.set_page_config(page_title="Alpha V70.7 Pro", layout="wide")

# AUTO-REFRESH (wenn aktiviert)
if st.session_state.auto_refresh_enabled:
    refresh_interval = st.session_state.get("refresh_interval", 5) * 60 * 1000  # in ms
    st_autorefresh(interval=refresh_interval, key="auto_refresh")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V70.7 Pro")
    st.caption("Pre/Post Market | Insider | Gaps | AI")
    
    st.divider()
    
    # Markt-Auswahl (4 Kategorien)
    m_type = st.radio("📊 Markt:", ["Krypto", "Aktien", "Futures", "Forex"], horizontal=True)
    st.session_state.market_type = m_type
    
    if m_type == "Krypto":
        st.caption("📡 CoinGecko (Top 500) - 24/7")
    
    elif m_type == "Futures":
        # FUTURES KATEGORIE-AUSWAHL
        futures_categories = {
            "📈 Index Futures": "INDEX",
            "🛢️ Energie (Öl, Gas)": "ENERGY",
            "🥇 Metalle (Gold, Silber)": "METALS",
            "🌾 Agrar (Weizen, Mais)": "AGRI",
            "💵 Zinsen (Bonds)": "RATES"
        }
        
        selected_futures = st.selectbox(
            "📊 Futures-Kategorie:",
            list(futures_categories.keys()),
            index=0,
            key="futures_selector"
        )
        st.session_state.selected_futures_cat = futures_categories[selected_futures]
        st.caption("📡 Yahoo Finance - 15min verzögert")
        st.caption("🕐 Handelszeiten: Fast 24/7 (So-Fr)")
    
    elif m_type == "Forex":
        # FOREX KATEGORIE-AUSWAHL
        forex_categories = {
            "💵 Majors (EUR, GBP, JPY)": "MAJORS",
            "🌍 Minors (AUD, NZD, CAD)": "MINORS",
            "🌏 Exotics (TRY, ZAR, MXN)": "EXOTICS"
        }
        
        selected_forex = st.selectbox(
            "💱 Forex-Kategorie:",
            list(forex_categories.keys()),
            index=0,
            key="forex_selector"
        )
        st.session_state.selected_forex_cat = forex_categories[selected_forex]
        st.caption("📡 Yahoo Finance - 15min verzögert")
        st.caption("🕐 Handelszeiten: 24/5 (So 22:00 - Fr 22:00 UTC)")
    
    else:  # Aktien
        # BÖRSEN-AUSWAHL
        exchange_options = {
            "🇺🇸 USA (NYSE/NASDAQ)": "US",
            "🇩🇪 Deutschland (XETRA)": "DE",
            "🇬🇧 UK (London)": "UK",
            "🇨🇭 Schweiz (SIX)": "CH",
            "🇪🇺 Europa (Euronext)": "EU",
            "🇯🇵 Japan (Tokyo)": "JP",
            "🇭🇰 Hong Kong": "HK"
        }
        
        selected_exchange = st.selectbox(
            "🌍 Börse:",
            list(exchange_options.keys()),
            index=0,
            key="exchange_selector"
        )
        st.session_state.selected_exchange = exchange_options[selected_exchange]
        
        # API Info je nach Börse
        if st.session_state.selected_exchange == "US":
            st.caption("📡 Polygon.io Realtime (Starter/Paid)")
        else:
            st.caption("📡 Yahoo Finance - 15min verzögert")
        
        # TRADING SESSION - Nur für US relevant
        if st.session_state.selected_exchange == "US":
            # Automatische Session ermitteln
            auto_session, session_status = get_current_trading_session()
            
            # Override Option
            manual_override = st.checkbox("⚙️ Session manuell wählen", key="session_override")
            
            if manual_override:
                # Manueller Modus
                trading_session = st.radio(
                    "⏰ Session:",
                    ["Regular", "Pre-Market", "After-Hours", "Extended"],
                    horizontal=True,
                    key="manual_session",
                    help="Regular: 9:30-16:00 ET | Pre: 4:00-9:30 ET | After: 16:00-20:00 ET"
                )
                st.caption(f"📌 Manuell: {trading_session}")
            else:
                # Automatischer Modus
                trading_session = auto_session
                st.success(session_status)
            
            # Session in session_state speichern (für Scan)
            st.session_state.active_trading_session = trading_session
        else:
            # Internationale Börsen: Keine Session-Auswahl
            st.session_state.active_trading_session = "Regular"
            
            # Handelszeiten Info
            trading_hours = {
                "DE": "09:00-17:30 CET",
                "UK": "08:00-16:30 GMT", 
                "CH": "09:00-17:30 CET",
                "EU": "09:00-17:30 CET",
                "JP": "09:00-15:00 JST",
                "HK": "09:30-16:00 HKT"
            }
            hours = trading_hours.get(st.session_state.selected_exchange, "")
            if hours:
                st.caption(f"🕐 Handelszeiten: {hours}")
    
    st.divider()
    
    # AUTO-REFRESH CONTROLS
    st.subheader("🔄 Auto-Refresh")
    col_ar1, col_ar2 = st.columns(2)
    with col_ar1:
        auto_refresh = st.checkbox("Aktiviert", value=st.session_state.auto_refresh_enabled, key="ar_toggle")
        st.session_state.auto_refresh_enabled = auto_refresh
    with col_ar2:
        refresh_mins = st.selectbox("Intervall", [1, 2, 5, 10, 15], index=2, key="ar_interval")
        st.session_state.refresh_interval = refresh_mins
    
    if auto_refresh:
        st.success(f"⏱️ Refresh alle {refresh_mins} Min")
    
    # DEBUG MODE
    st.session_state.debug_mode = st.checkbox("🐛 Debug Mode", value=st.session_state.get("debug_mode", False), key="debug_toggle")

    # =================================================================
    # INTERACTIVE BROKERS TWS
    # =================================================================
    st.divider()
    st.subheader("🤖 IBKR TWS")

    if not IB_INSYNC_AVAILABLE:
        st.warning("ib_insync nicht installiert")
    else:
        # Connection status + button
        col_ib_s, col_ib_b = st.columns([2, 1])
        with col_ib_s:
            if ib_is_connected():
                state = _get_ib_state()
                uptime = ""
                if state.get("connect_time"):
                    delta = datetime.now() - state["connect_time"]
                    uptime = f" ({delta.seconds // 60}m)"
                st.success(f"🟢 Connected{uptime}")
            else:
                st.error("🔴 Offline")
                err = _get_ib_state().get("error")
                if err:
                    st.caption(f"⚠️ {err}")
        with col_ib_b:
            if ib_is_connected():
                if st.button("🔌 Trennen", use_container_width=True, key="ib_disconnect"):
                    ib_disconnect()
                    st.rerun()
            else:
                if st.button("🔌 Connect", use_container_width=True, key="ib_connect"):
                    success = ib_connect(port=st.session_state.ib_port)
                    if success:
                        st.rerun()

        with st.expander("⚙️ TWS Settings"):
            ib_port = st.selectbox("Port", [7497, 7496], index=0 if st.session_state.ib_port == 7497 else 1, key="ib_port_sel")
            st.session_state.ib_port = ib_port
            if ib_port == 7496:
                st.warning("⚠️ LIVE Trading Port!")

            st.divider()
            ib_size_type = st.radio("Position Size", ["Shares", "Dollar"], horizontal=True, key="ib_size_type_sel")
            st.session_state.ib_size_type = ib_size_type

            if ib_size_type == "Shares":
                ib_size = st.number_input("Anzahl Shares", min_value=1, max_value=10000, value=st.session_state.ib_position_size, step=10, key="ib_size_val")
            else:
                ib_size = st.number_input("Dollar Betrag ($)", min_value=100, max_value=500000, value=st.session_state.ib_position_size, step=100, key="ib_size_val")
            st.session_state.ib_position_size = ib_size

        # Last orders
        if st.session_state.ib_orders_log:
            with st.expander(f"📋 Orders ({len(st.session_state.ib_orders_log)})"):
                for o in st.session_state.ib_orders_log[-5:][::-1]:
                    icon = "🟢" if o["direction"] == "LONG" else "🔴"
                    st.caption(f"{icon} {o['ticker']} {o['direction']} @ ${o['entry']:.2f} — {o['time']}")

    # =================================================================
    # 🤖 AUTO-TRADER
    # =================================================================
    if IB_INSYNC_AVAILABLE:
        st.divider()
        st.subheader("🤖 Auto-Trader")

        at_config = _autotrader_config_load()
        at_state = _autotrader_state_read()
        at_running = at_state.get("status") == "running"

        # Status
        if at_running:
            _last = at_state.get("last_scan", "—")
            _pos_count = len(at_state.get("positions", []))
            _trades = at_state.get("trades_today", 0)
            mode_icon = "🟢" if at_config.get("mode") == "full" else "🟡"
            st.success(f"{mode_icon} Aktiv | {_pos_count} Pos | {_trades} Trades heute | Letzter Scan: {_last}")
        else:
            st.info("⏸️ Gestoppt")

        # Start/Stop Buttons
        col_at1, col_at2 = st.columns(2)
        with col_at1:
            if not at_running:
                if st.button("▶️ Starten", use_container_width=True, type="primary", key="at_start"):
                    if ib_is_connected():
                        import threading
                        try:
                            poly_key_at = st.secrets["POLYGON_KEY"]
                        except Exception:
                            poly_key_at = None
                        if poly_key_at:
                            _autotrader_clear_stop()
                            t = threading.Thread(
                                target=autotrader_background_loop,
                                args=(poly_key_at,),
                                daemon=True
                            )
                            t.start()
                            st.session_state.autotrader_thread = t
                            st.rerun()
                        else:
                            st.error("Polygon Key fehlt!")
                    else:
                        st.error("IBKR nicht verbunden!")
            else:
                if st.button("⏹️ Stoppen", use_container_width=True, key="at_stop"):
                    _autotrader_request_stop()
                    st.rerun()
        with col_at2:
            if st.button("🔄 1x Scan", use_container_width=True, key="at_once",
                         disabled=not ib_is_connected()):
                try:
                    poly_key_at = st.secrets["POLYGON_KEY"]
                    with st.spinner("Scanne..."):
                        scan_res = autotrader_scan_once(poly_key_at)
                    st.success(f"✅ {scan_res.get('signals_found', 0)} Signale, {scan_res.get('orders_placed', 0)} Orders")
                    if scan_res["errors"]:
                        for e in scan_res["errors"]:
                            st.caption(f"⚠️ {e}")
                except Exception as e:
                    st.error(f"Fehler: {str(e)[:80]}")

        # Settings
        with st.expander("⚙️ Auto-Trader Settings"):
            # Mode Toggle
            at_mode = st.radio(
                "Modus",
                ["🟢 Voll-Auto (transmit=True)", "🟡 Semi-Auto (bestätigen in TWS)"],
                index=0 if at_config.get("mode") == "full" else 1,
                key="at_mode_sel"
            )
            new_mode = "full" if "Voll" in at_mode else "semi"

            if new_mode == "full":
                st.warning("⚠️ Orders werden SOFORT ausgeführt!")

            # Position Size — übernimmt aus TWS Settings oben
            st.caption(f"📐 Position Size: aus TWS Settings oben")

            # Risk Settings
            at_max_pos = st.slider("Max Positionen", 1, 15, at_config.get("max_positions", 5), key="at_max_pos")
            at_max_loss = st.slider("Max Tagesverlust %", 1.0, 10.0, at_config.get("max_daily_loss_pct", 3.0),
                                     step=0.5, key="at_max_loss")
            at_interval = st.selectbox("Scan-Intervall", [5, 10, 15, 30, 60],
                                        index=[5,10,15,30,60].index(at_config.get("scan_interval_min", 15)),
                                        format_func=lambda x: f"{x} Min", key="at_interval")

            # Signal Filter
            st.caption("**Signal-Filter:**")
            at_min_score = st.slider("Min BI Score %", 40, 80, at_config.get("min_bi_pct", 55), key="at_min_score")
            at_min_rr = st.slider("Min R:R", 1.5, 4.0, at_config.get("min_rr", 2.0), step=0.5, key="at_min_rr")

            at_exclude_a = st.checkbox("Grade A ausschließen (empfohlen!)", value="A" in at_config.get("excluded_grades", ["A"]),
                                        key="at_excl_a", help="Backtest zeigt: Grade A PF=0.39 über 12 Monate")

            at_market_hours = st.checkbox("Nur Handelszeiten (9:30-16:00 ET)", value=at_config.get("trading_hours_only", True),
                                           key="at_market_hours", help="Deaktivieren zum Testen außerhalb der Börsenzeiten")

            # Save Button
            if st.button("💾 Speichern", use_container_width=True, key="at_save"):
                _ib_sz_type = st.session_state.get("ib_size_type_sel", "Shares")
                _ib_sz_val = st.session_state.get("ib_position_size", 100)
                new_config = {
                    "mode": new_mode,
                    "max_positions": at_max_pos,
                    "position_size_type": "dollar" if _ib_sz_type == "Dollar" else "shares",
                    "position_size": _ib_sz_val,
                    "excluded_grades": ["A"] if at_exclude_a else [],
                    "min_bi_pct": at_min_score,
                    "min_smart_money": 2,
                    "scan_interval_min": at_interval,
                    "max_daily_loss_pct": at_max_loss,
                    "cooldown_days": 5,
                    "trading_hours_only": at_market_hours,
                    "min_rr": at_min_rr,
                    "max_tickers_scan": 300,
                    "min_price": 5.0,
                    "min_volume": 200000,
                }
                _autotrader_config_save(new_config)
                st.success("✅ Gespeichert!")
                st.rerun()

        # Aktive Positionen
        at_positions = at_state.get("positions", [])
        if at_positions:
            with st.expander(f"📊 Positionen ({len(at_positions)})"):
                for p in at_positions:
                    mode_lbl = "🟢" if p.get("mode") == "AUTO" else "🟡"
                    st.caption(
                        f"{mode_lbl} **{p['ticker']}** Grade {p['grade']} | "
                        f"Entry ${p['entry']} | SL ${p['stop']} | "
                        f"TP1 ${p['tp1']} | {p['shares']} Shares | "
                        f"{p.get('date', '')} {p.get('time', '')}"
                    )

        # Log
        try:
            with open(_AUTOTRADER_LOG_FILE, "r") as _lf:
                at_log = json.load(_lf)
            if at_log:
                with st.expander(f"📋 Log ({len(at_log)} Einträge)"):
                    for entry in at_log[-10:][::-1]:
                        _lvl_icon = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "TRADE": "💰"}.get(entry.get("level"), "•")
                        st.caption(f"{_lvl_icon} {entry.get('time', '')} — {entry.get('msg', '')}")
        except Exception:
            pass

    # PRE-MARKET WATCHLIST BUTTON (nur für US Aktien)
    if m_type == "Aktien" and st.session_state.get("selected_exchange", "US") == "US":
        session_info = get_current_trading_session()
        if session_info[0] == "Pre-Market":
            st.divider()
            st.subheader("🌅 Pre-Market Watchlist")
            st.caption("Finde PM Movers VOR Market Open")
            
            if st.button("📋 PM Watchlist laden", key="pm_watchlist_btn", use_container_width=True):
                st.session_state.show_pm_watchlist = True
                st.session_state.pm_watchlist_data = None  # Reset für neue Daten
            
            if st.session_state.get("show_pm_watchlist", False):
                st.success("✅ PM Watchlist aktiv")
                if st.button("❌ Schließen", key="close_pm_watchlist"):
                    st.session_state.show_pm_watchlist = False
                    st.rerun()
        else:
            # Außerhalb PM Session - zeige Hinweis
            st.divider()
            with st.expander("🌅 Pre-Market Watchlist"):
                st.info(f"⏰ PM Session: 4:00-9:30 AM ET\n\nAktuell: {session_info[1]}")
                st.caption("PM Watchlist verfügbar während Pre-Market Session")
    
    st.divider()
    
    # Strategie-Auswahl - DYNAMISCH basierend auf Markt!
    st.subheader("🎯 Strategie")
    
    # Hole passende Strategien für aktuellen Markt
    _current_exchange = st.session_state.get("selected_exchange", "US")
    current_strategies = get_strategies_for_market(m_type, exchange=_current_exchange)
    strategy_list = list(current_strategies.keys())
    
    # Info welcher Markt
    market_emoji = {"Krypto": "🌐", "Aktien": "📊", "Futures": "📈", "Forex": "💱"}.get(m_type, "📊")
    st.caption(f"{market_emoji} {len(strategy_list)} Strategien für **{m_type}**")
    
    # Prüfe ob aktuelle Strategie zum Markt passt, sonst Reset
    current_saved_strategy = st.session_state.get("current_strategy", "")
    if current_saved_strategy and current_saved_strategy not in strategy_list:
        # Strategie passt nicht zum neuen Markt - Reset auf erste Strategie des neuen Markts
        first_strategy = strategy_list[0]
        apply_strategy(first_strategy, current_strategies)
        current_saved_strategy = first_strategy
    
    # Finde Index der aktuellen Strategie (falls vorhanden)
    default_index = 0
    if current_saved_strategy in strategy_list:
        default_index = strategy_list.index(current_saved_strategy)
    
    # WICHTIG: Key muss zum Markt UND Exchange passen, sonst verwirrt Streamlit sich!
    strat = st.selectbox("Wähle Strategie:", strategy_list, index=default_index, key=f"strategy_select_{m_type}_{_current_exchange}")
    
    # Strategie laden wenn sich Auswahl ändert
    if strat != st.session_state.get("current_strategy", ""):
        apply_strategy(strat, current_strategies)
        st.session_state.filters_synced = True  # Flag dass Filter aktuell sind
        st.rerun()
    
    # AUTO-FIX: Wenn Filter nicht mit Strategie-Definition übereinstimmen
    # (passiert wenn Code aktualisiert wurde aber alter Browser-Cache existiert)
    # Nur EINMAL pro Session ausführen um manuelle Änderungen nicht zu überschreiben
    if not st.session_state.get("filters_synced", False):
        expected_filters = current_strategies[strat]["filters"]
        current_filters = st.session_state.active_filters
        if current_filters != expected_filters:
            st.toast("🔄 Filter aktualisiert (neue Version)")
            apply_strategy(strat, current_strategies)
            st.session_state.filters_synced = True
            st.rerun()
        else:
            st.session_state.filters_synced = True  # Filter stimmen bereits
    
    with st.expander("ℹ️ Info"):
        st.write(current_strategies[strat]["description"])
        st.caption(current_strategies[strat]['logic'])
        
        # Multi-Day Pattern Analyse Hinweis
        strategy_data = current_strategies[strat]
        if strategy_data.get("needs_history"):
            pattern_type = strategy_data.get("pattern_type", "unknown")
            history_days = strategy_data.get("history_days", 5)
            st.info(f"📊 **Multi-Day Analyse:** {history_days} Tage Pattern ({pattern_type})")
            st.caption("🔬 Pattern-Validierung mit historischen Daten für bessere Trefferquote")
        
        # Session-Zeit Check für Futures/Forex Strategien
        if "best_time" in strategy_data:
            best_time = strategy_data["best_time"]
            st.info(f"⏰ **Beste Zeit:** {best_time}")
            
            # Aktive Session-Prüfung
            try:
                from datetime import datetime
                import pytz
                utc_now = datetime.now(pytz.UTC)
                current_utc_hour = utc_now.hour
                
                # Parse best_time (Format: "HH:00-HH:00 UTC")
                if "UTC" in best_time:
                    time_range = best_time.replace(" UTC", "").split("-")
                    if len(time_range) == 2:
                        start_hour = int(time_range[0].split(":")[0])
                        end_hour = int(time_range[1].split(":")[0])
                        
                        # Prüfe ob aktuelle Zeit im Fenster liegt
                        if start_hour <= end_hour:
                            in_window = start_hour <= current_utc_hour < end_hour
                        else:  # Overnight (z.B. 18:00-08:00)
                            in_window = current_utc_hour >= start_hour or current_utc_hour < end_hour
                        
                        if in_window:
                            st.success(f"✅ Aktuell im optimalen Zeitfenster ({current_utc_hour}:00 UTC)")
                        else:
                            st.warning(f"⚠️ **Außerhalb des optimalen Zeitfensters** ({current_utc_hour}:00 UTC) — "
                                       f"Signalqualität ist reduziert. Ergebnisse mit Vorsicht nutzen!")
            except Exception as e:
                pass  # Fehler ignorieren
        
        # Best Pairs für Forex
        if "best_pairs" in strategy_data:
            pairs = ", ".join(strategy_data["best_pairs"])
            st.caption(f"💱 Beste Paare: {pairs}")
        
        # Warnungen für marktspezifische Strategien (nur bei Aktien relevant)
        if m_type == "Aktien":
            if strat in ["Gap Up", "Gap Down", "Gap Up (High Vol)", "Gap Down (High Vol)"]:
                st.info("ℹ️ Gap-Strategien funktionieren nur bei US-Börse mit Polygon.io")
            if strat in ["Insider Buying", "Insider Selling"]:
                st.warning("⚠️ Insider-Strategien benötigen Finnhub API Key!")
    
    st.divider()
    
    # Aktive Filter
    if st.session_state.active_filters:
        st.subheader("⚙️ Filter")
        
        # Dynamischer Key: Reset bei Strategiewechsel damit Slider neue Werte übernehmen
        _frc = st.session_state.get("filter_reset_counter", 0)
        
        # WICHTIG: Wenn gerade Strategie gewechselt wurde, Slider NICHT rendern
        # weil Streamlit sonst alte Widget-Werte zurückgibt und die Filter überschreibt.
        # Stattdessen nur die aktuellen Filter-Werte anzeigen.
        _just_switched = st.session_state.get("_strategy_just_applied", False)
        if _just_switched:
            st.session_state._strategy_just_applied = False
            # Zeige Filter read-only (kein Slider = kein Override)
            for filter_name, values in st.session_state.active_filters.items():
                if isinstance(values, (tuple, list)) and len(values) == 2:
                    st.caption(f"**{filter_name}:** {values[0]} bis {values[1]}")
            st.caption("🔄 *Filter aktualisiert — nächster Scan nutzt neue Werte*")
        else:
            # Kopie der Filter für Anzeige
            current_filters = st.session_state.active_filters.copy()
            updated_filters = {}
            
            for filter_name, values in current_filters.items():
                # Überspringe Insider-Filter (kein Slider)
                if filter_name == "Insider":
                    updated_filters[filter_name] = values
                    continue
                
                # Prüfe ob values ein Tuple mit 2 Elementen ist
                if not isinstance(values, (tuple, list)) or len(values) != 2:
                    updated_filters[filter_name] = values
                    continue
                    
                if filter_name == "Close Position":
                    new_val = st.slider(
                        f"{filter_name}", 0.0, 1.0, (float(values[0]), float(values[1])), 
                        step=0.05, key=f"slider_{filter_name}_{_frc}"
                    )
                    updated_filters[filter_name] = new_val
                elif filter_name == "Preis":
                    new_val = st.slider(
                        f"{filter_name} ($)", 0.0, 10000.0, (float(values[0]), float(values[1])), 
                        key=f"slider_{filter_name}_{_frc}"
                    )
                    updated_filters[filter_name] = new_val
                else:
                    min_v = -100.0 if "%" in filter_name else 0.0
                    max_v = 100.0 if "%" in filter_name else 100.0
                    new_val = st.slider(
                        filter_name, min_v, max_v, (float(values[0]), float(values[1])), 
                        key=f"slider_{filter_name}_{_frc}"
                    )
                    updated_filters[filter_name] = new_val
            
            # Aktualisiere die Filter nach dem Rendern
            st.session_state.active_filters = updated_filters
        
        # Zusatzfilter kompakt
        with st.expander("🔧 Zusatzfilter"):
            c1, c2 = st.columns(2)
            with c1:
                preis_min = st.number_input("Min $", 0.0, 100000.0, 0.0, key="af_min")
            with c2:
                preis_max = st.number_input("Max $", 0.0, 100000.0, 100000.0, key="af_max")
            
            c3, c4 = st.columns(2)
            with c3:
                nur_gewinner = st.checkbox("✅ Gewinner", key="af_win")
            with c4:
                nur_verlierer = st.checkbox("🔻 Verlierer", key="af_lose")
            
            # ETF Filter (nur bei Aktien relevant)
            if m_type == "Aktien":
                st.divider()
                exclude_etfs = st.checkbox("🚫 ETFs ausblenden", value=True, key="af_exclude_etf",
                                          help="Filtert ETFs, Leveraged ETFs (TQQQ, SQQQ, etc.), ETNs, Warrants und Units")
                
                # Liquiditäts-Filter
                liq_options = {
                    "Kein Minimum": 0,
                    "$1M Minimum": 1_000_000,
                    "$5M Minimum": 5_000_000,
                    "$10M Minimum": 10_000_000,
                    "$50M Minimum": 50_000_000,
                }
                liq_choice = st.selectbox(
                    "💧 Min. Liquidität", 
                    list(liq_options.keys()), 
                    index=3,  # Default: $10M
                    key="af_min_liq",
                    help="Projiziertes Tages-Dollar-Volume (zeitnormalisiert). Am Morgen wird das aktuelle Volumen auf den vollen Tag hochgerechnet."
                )
                min_liquidity = liq_options[liq_choice]
            else:
                exclude_etfs = False
                min_liquidity = 0
            
            st.session_state.additional_filters = {
                "preis_min": preis_min, "preis_max": preis_max,
                "nur_gewinner": nur_gewinner, "nur_verlierer": nur_verlierer,
                    "exclude_etfs": exclude_etfs,
                "min_liquidity": min_liquidity,
            }
    
    st.divider()

    # BI Strategie → Hinweis auf eigenen Tab
    _bi_current_strat = st.session_state.get("current_strategy", "")
    if "Breakout Imminent" in _bi_current_strat:
        st.info("🔮 **Breakout Imminent** hat einen eigenen Tab → Wechsle zum **🔮 BI Scanner** Tab!")

    # SCAN Button (für ALLE anderen Strategien — BI hat eigenen Tab)
    if "Breakout Imminent" in _bi_current_strat:
        pass  # BI hat eigenen Tab
    elif st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        # Reset Navigation Index für neue Ergebnisse
        st.session_state.selected_row_index = 0
        if "ticker_select_df" in st.session_state:
            del st.session_state["ticker_select_df"]
        
        # === TEMP DEBUG - Zeige immer den Scan-Pfad ===
        _dbg_m = m_type
        _dbg_ex = st.session_state.get("selected_exchange", "US")
        _dbg_strat = st.session_state.get("current_strategy", "KEINE")
        _dbg_af = st.session_state.active_filters
        st.info(f"🔍 Scan-Pfad: m_type={_dbg_m} | exchange={_dbg_ex} | strategy={_dbg_strat} | filters={_dbg_af}")
        
        # DEBUG: Zeige aktuelle Konfiguration
        if st.session_state.get("debug_mode", False):
            st.caption(f"🔍 Debug: Markt={m_type}, Strategie={st.session_state.get('current_strategy', 'KEINE')}, Filter={st.session_state.active_filters}")
        
        # Prüfe ob Insider-Strategie gewählt
        current_strat = st.session_state.get("current_strategy", "")
        is_insider_strategy = current_strat in ["Insider Buying", "Insider Selling"]
        is_gap_strategy = current_strat in ["Gap Up", "Gap Down"]
        is_volume_void_strategy = current_strat in ["Volume Void Long 🕳️⬆️", "Volume Void Short 🕳️⬇️"]
        is_ma_bounce_strategy = "Bounce" in current_strat and ("SMA" in current_strat or "EMA" in current_strat)
        is_breakout_imminent = "Breakout Imminent" in current_strat
        
        # Warnung: Gap-Strategie bei Krypto
        if is_gap_strategy and m_type == "Krypto":
            st.error("❌ Gap-Strategien funktionieren nicht bei Krypto! Krypto handelt 24/7 und hat keine echten Gaps. Bitte wechsle zu **Aktien**.")
        
        # Volume Void Strategie bei Krypto nicht verfügbar
        elif is_volume_void_strategy and m_type == "Krypto":
            st.error("❌ Volume Void Scanner funktioniert nur für **Aktien**! Krypto hat andere Volumen-Dynamik.")
        
        # VOLUME VOID SCAN - Spezielle Logik
        elif is_volume_void_strategy:
            with st.status("🕳️ Scanne Volume Voids...") as status:
                try:
                    poly_key = st.secrets["POLYGON_KEY"]
                    
                    # Bestimme Richtung
                    direction = "long" if "Long" in current_strat else "short"
                    
                    status.update(label=f"Hole Top-Aktien für Volume Profile Analyse...")
                    
                    # Alle Aktien ungefiltert holen
                    candidates, _, _, _ = fetch_stock_data(poly_key, session="Regular", skip_filters=True)

                    # Filter: Liquidität + Preis
                    if direction == "long":
                        filtered = [c for c in candidates if -5 <= c.get("Chg%", 0) <= 10
                                    and c.get("Preis", 0) >= 5 and c.get("DollarVol", 0) >= 500_000]
                    else:
                        filtered = [c for c in candidates if -10 <= c.get("Chg%", 0) <= 5
                                    and c.get("Preis", 0) >= 5 and c.get("DollarVol", 0) >= 500_000]

                    # Sortiere nach DollarVol — KEIN Cap
                    filtered = sorted(filtered, key=lambda x: x.get("DollarVol", 0), reverse=True)
                    tickers = [c["Ticker"] for c in filtered]
                    
                    status.update(label=f"Analysiere Volume Profile für {len(tickers)} Aktien...")
                    
                    # Volume Void Scan
                    void_results = scan_volume_voids_batch(tickers, poly_key, direction=direction)
                    
                    # Konvertiere zu Standard-Format
                    results = []
                    for vr in void_results:
                        # Finde Original-Daten
                        orig = next((c for c in candidates if c["Ticker"] == vr["ticker"]), {})
                        
                        results.append({
                            "Ticker": vr["ticker"],
                            "Name": "",
                            "Preis": round(vr["price"], 2),
                            "Chg%": orig.get("Chg%", 0),
                            "RVOL": orig.get("RVOL", 1.0),
                            "Vortag%": orig.get("Vortag%", 0),
                            "ClosePos": orig.get("ClosePos", 0.5),
                            "Alpha": vr["void_score"],  # Void Score als Alpha
                            "Gap%": 0,
                            "VoidScore": vr["void_score"],
                            "VoidDist%": vr["distance_to_void_pct"],
                            "VoidSize%": vr["void_size_pct"],
                            "POC": vr["poc"],
                            "VAH": vr["vah"],
                            "VAL": vr["val"],
                            "VoidsAbove": vr["num_voids_above"],
                            "VoidsBelow": vr["num_voids_below"],
                            "NearestVoid": vr["nearest_void"],
                            "AllVoidsAbove": vr.get("voids_above", []),
                            "AllVoidsBelow": vr.get("voids_below", []),
                        })
                    
                    st.session_state.scan_results = results
                    st.session_state.market_type = "Aktien"
                    
                    direction_emoji = "⬆️" if direction == "long" else "⬇️"
                    status.update(label=f"✅ {len(results)} Volume Voids {direction_emoji} gefunden", state="complete")
                    
                except KeyError:
                    st.error("❌ POLYGON_KEY fehlt in Secrets!")
                except Exception as e:
                    st.error(f"Fehler: {e}")
        
        # HARMONIC PATTERN SCAN - Spezielle Logik 🦋
        elif current_strat in ["Harmonic Bullish 🦋⬆️", "Harmonic Bearish 🦋⬇️", "Harmonic All Patterns 🦋"]:
            if m_type != "Aktien":
                st.error("❌ Harmonic Pattern Scanner funktioniert nur für **Aktien**!")
            else:
                # Timeframe Selector für Harmonic Scan
                htf_col1, htf_col2 = st.columns([1, 3])
                with htf_col1:
                    harmonic_tf = st.selectbox(
                        "🕐 Timeframe",
                        options=["4H", "Daily", "1H"],
                        index=0,  # 4H = Default (bester TF für Harmonics)
                        key="harmonic_tf_select",
                        help="4H = Bester TF für Harmonics (saubere Swings, 1-3 Wochen Pattern)"
                    )
                with htf_col2:
                    tf_info = {"4H": "~1080 Bars (6 Monate) — Ideal für Swing-Patterns", 
                               "Daily": "~120 Bars (6 Monate) — Langfristige Patterns",
                               "1H": "~1560 Bars (3 Monate) — Kurzfristige Patterns, mehr Noise"}
                    st.info(f"📊 {tf_info.get(harmonic_tf, '')}")
                
                # Map TF to API parameters
                tf_map = {"4H": ("hour", 180), "Daily": ("day", 180), "1H": ("hour", 90)}
                api_tf, api_days = tf_map[harmonic_tf]
                # For 1H: multiplier=1, for 4H: multiplier=4 (handled in scan_harmonic_patterns)
                if harmonic_tf == "1H":
                    api_tf = "1hour"  # Signal to use 1H instead of 4H
                
                with st.status("🦋 Scanne Harmonic Patterns...") as status:
                    try:
                        poly_key = st.secrets["POLYGON_KEY"]
                        
                        # Bestimme Richtung
                        if "Bullish" in current_strat:
                            direction = "LONG"
                        elif "Bearish" in current_strat:
                            direction = "SHORT"
                        else:
                            direction = "ALL"
                        
                        status.update(label="Hole Top-Aktien für Pattern-Analyse...")
                        
                        # Alle Aktien holen (ungefiltert) für maximale Abdeckung
                        candidates, _, _, _ = fetch_stock_data(poly_key, session="Regular", skip_filters=True)

                        # Filter: Nur liquide Aktien (Preis >= $5, DollarVol >= $500k)
                        filtered = [c for c in candidates
                                    if c.get("Preis", 0) >= 5
                                    and c.get("DollarVol", 0) >= 500_000]

                        # Sortiere nach Setup Score (Fallback Alpha) — KEIN künstliches Cap
                        filtered = sorted(filtered, key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), reverse=True)
                        tickers = [c["Ticker"] for c in filtered]
                        
                        status.update(label=f"Analysiere {len(tickers)} Aktien auf Harmonic Patterns ({harmonic_tf}, {api_days}d)...")
                        
                        # Harmonic Scan
                        harmonic_results = scan_harmonic_batch(tickers, poly_key, days=api_days, timeframe=api_tf)
                        
                        # Filter nach Richtung
                        if direction != "ALL":
                            harmonic_results = [r for r in harmonic_results if r["Direction"] == direction]
                        
                        # Konvertiere zu Standard-Format für Anzeige
                        results = []
                        for hr in harmonic_results:
                            results.append({
                                "Ticker": hr["Ticker"],
                                "Name": hr["Pattern"],
                                "Preis": hr["Price"],
                                "Chg%": 0,  # Nicht relevant für Pattern
                                "RVOL": 0,
                                "Vortag%": 0,
                                "ClosePos": 0,
                                "Alpha": hr["Score"],
                                "Gap%": 0,
                                # Harmonic-spezifische Felder
                                "Pattern": hr["Pattern"],
                                "Direction": hr["Direction"],
                                "Matches": hr["Matches"],
                                "SuccessRate": hr["SuccessRate"],
                                "Entry": hr["Entry"],
                                "StopLoss": hr["StopLoss"],
                                "TP1": hr["TP1"],
                                "TP2": hr["TP2"],
                                "RiskReward": hr["RiskReward"],
                                "PatternData": hr["PatternData"]
                            })
                        
                        st.session_state.scan_results = results
                        st.session_state.market_type = "Aktien"
                        
                        direction_emoji = "⬆️" if direction == "LONG" else ("⬇️" if direction == "SHORT" else "🔄")
                        status.update(label=f"✅ {len(results)} Harmonic Patterns {direction_emoji} gefunden", state="complete")
                        
                    except KeyError:
                        st.error("❌ POLYGON_KEY fehlt in Secrets!")
                    except Exception as e:
                        st.error(f"Fehler: {e}")
        
        # =================================================================
        # WYCKOFF STRATEGIE - Akkumulation/Distribution Scanner
        # =================================================================
        elif current_strat in ["Wyckoff Accumulation 🏦⬆️", "Wyckoff Distribution 🏦⬇️"]:
            if m_type != "Aktien":
                st.error("❌ Wyckoff Scanner funktioniert nur für **Aktien**!")
            else:
                # Timeframe Selector
                wtf_col1, wtf_col2 = st.columns([1, 3])
                with wtf_col1:
                    wyckoff_tf = st.selectbox(
                        "🕐 Timeframe",
                        options=["4H", "Daily", "1H"],
                        index=0,
                        key="wyckoff_tf_select",
                        help="4H = Ideal für Wyckoff (Akkumulation braucht Wochen von Daten)"
                    )
                with wtf_col2:
                    tf_info = {"4H": "~1080 Bars / 6 Monate — Ideal für Wyckoff Phasen",
                               "Daily": "~120 Bars / 6 Monate — Langfristige Schemen",
                               "1H": "~1560 Bars / 3 Monate — Kurzfristige Mini-Akkumulation"}
                    st.info(f"📊 {tf_info.get(wyckoff_tf, '')}")
                
                tf_map = {"4H": ("hour", 180), "Daily": ("day", 180), "1H": ("1hour", 90)}
                api_tf, api_days = tf_map[wyckoff_tf]
                
                direction = "LONG" if "Accumulation" in current_strat else "SHORT"
                
                with st.status("🏦 Scanne Wyckoff Patterns...") as status:
                    try:
                        poly_key = st.secrets["POLYGON_KEY"]
                        
                        status.update(label="Hole alle Aktien für Wyckoff-Analyse...")
                        candidates, _, _, _ = fetch_stock_data(poly_key, session="Regular", skip_filters=True)
                        # Wyckoff braucht Liquidität + ordentliche Preise
                        filtered = [c for c in candidates
                                    if c.get("Preis", 0) >= 5
                                    and c.get("DollarVol", 0) >= 500_000]
                        # Sortiere nach DollarVol (liquideste zuerst)
                        filtered = sorted(filtered, key=lambda x: x.get("DollarVol", 0), reverse=True)
                        tickers = [c["Ticker"] for c in filtered]

                        est_min = max(1, len(tickers) // 80)  # ~80 Calls/Min mit Rate Limiting
                        status.update(label=f"Analysiere {len(tickers)} Aktien auf Wyckoff ({wyckoff_tf}, {api_days}d) ~{est_min} Min...")
                        
                        wyckoff_results = scan_wyckoff_batch(tickers, poly_key, days=api_days, timeframe=api_tf, direction=direction)
                        
                        results = []
                        for wr in wyckoff_results:
                            orig = next((c for c in candidates if c["Ticker"] == wr["ticker"]), {})
                            phase_label = wr["phase_short"] if "phase_short" in wr else wr["phase"]
                            
                            results.append({
                                "Ticker": wr["ticker"],
                                "Name": f"🏦 {wr['type']}",
                                "Preis": wr["current_price"],
                                "Chg%": orig.get("Chg%", 0),
                                "RVOL": orig.get("RVOL", 1.0),
                                "Vortag%": orig.get("Vortag%", 0),
                                "ClosePos": orig.get("ClosePos", 0.5),
                                "Alpha": wr["score"],
                                "Gap%": 0,
                                "WyckoffType": wr["type"],
                                "WyckoffPhase": wr["phase"],
                                "WyckoffScore": wr["score"],
                                "WyckoffEvents": " → ".join(wr["events"]),
                                "RangeHigh": wr["range_high"],
                                "RangeLow": wr["range_low"],
                                "Entry": wr["entry"],
                                "StopLoss": wr["stop"],
                                "TP1": wr["tp1"],
                                "RiskReward": wr["rr"],
                            })
                        
                        st.session_state.scan_results = results
                        st.session_state.market_type = "Aktien"
                        
                        dir_emoji = "⬆️" if direction == "LONG" else "⬇️"
                        status.update(label=f"✅ {len(results)} Wyckoff {wr['type'] if results else ''} {dir_emoji} Patterns gefunden", state="complete")
                        
                    except KeyError:
                        st.error("❌ POLYGON_KEY fehlt in Secrets!")
                    except Exception as e:
                        st.error(f"Fehler: {e}")
        
        elif is_insider_strategy:
            # Insider-Scan mit Finnhub
            with st.status("Scanne Insider-Transaktionen...") as status:
                try:
                    finnhub_key = st.secrets["FINNHUB_KEY"]
                    trans_type = "BUY" if current_strat == "Insider Buying" else "SELL"
                    status.update(label=f"Hole {trans_type} Transaktionen von Finnhub...")
                    results, snp, sf = fetch_insider_transactions(finnhub_key, trans_type)
                    st.session_state.scan_results = results
                    st.session_state.market_type = "Aktien"  # Insider nur für Aktien
                    status.update(label=f"✅ {len(results)} Insider-Signale gefunden", state="complete")
                except KeyError:
                    st.error("❌ FINNHUB_KEY fehlt in Secrets! Füge ihn hinzu unter Settings → Secrets")
                except Exception as e:
                    st.error(f"Fehler: {e}")
        
        # =================================================================
        # MA BOUNCE STRATEGIE - SMA/EMA Support/Resistance Scanner
        # =================================================================
        elif is_ma_bounce_strategy:
            with st.status("📈 Scanne MA Bounce Setups...") as status:
                try:
                    poly_key = st.secrets["POLYGON_KEY"]
                    
                    # Hole Strategie-Parameter
                    strategy_data = STRATEGIES.get(current_strat, {})
                    ma_type = strategy_data.get("ma_type", "SMA")
                    ma_period = strategy_data.get("ma_period", 50)
                    ma_approach = strategy_data.get("ma_approach", "from_above")
                    ma_distance_max = strategy_data.get("ma_distance_max", 3.0)
                    
                    status.update(label=f"Schritt 1/3: Hole alle Aktien (ungefiltert)...")
                    
                    # WICHTIG: skip_filters=True um ALLE Aktien zu bekommen!
                    # Der normale Scan würde sonst Aktien mit kleinem Change% rausfiltern
                    candidates, _, _, _ = fetch_stock_data(poly_key, session="Regular", skip_filters=True)
                    
                    status.update(label=f"Schritt 1/3: {len(candidates)} Aktien geladen, filtere...")
                    
                    # Basis-Filter: Preis $5-$1000 UND Liquidität >= $1M
                    # HINWEIS: $10M war zu restriktiv — Pullback-Aktien haben weniger Volumen als gewöhnlich
                    MIN_LIQUIDITY = 1_000_000  # $1 Million Dollar Volume
                    candidates = [c for c in candidates 
                                  if 5 <= c.get("Preis", 0) <= 1000 
                                  and c.get("DollarVol", 0) >= MIN_LIQUIDITY]
                    
                    status.update(label=f"Schritt 1/3: {len(candidates)} Aktien nach Preis/Liquiditäts-Filter...")
                    
                    # Filter nach Strategie-Richtung (für Bounce brauchen wir Pullbacks!)
                    if ma_approach == "from_above":
                        # Long Setup: Aktien die FALLEN oder flat sind (Pullback zum MA)
                        filtered = [c for c in candidates if -15 <= c.get("Chg%", 0) <= 3]
                    else:
                        # Short Setup: Aktien die STEIGEN oder flat sind (Rally zum MA)
                        filtered = [c for c in candidates if -3 <= c.get("Chg%", 0) <= 15]
                    
                    status.update(label=f"Schritt 1/3: {len(filtered)} Kandidaten nach Change%-Filter")
                    
                    # Sortiere nach Change% (kleinste Bewegung zuerst = näher am Pullback) — KEIN künstliches Cap
                    filtered = sorted(filtered, key=lambda x: abs(x.get("Chg%", 0)))
                    
                    status.update(label=f"Schritt 2/3: Berechne {ma_type} {ma_period} für {len(filtered)} Aktien...")
                    
                    # MA Berechnung für jeden Kandidaten
                    results = []
                    ma_checked = 0
                    ma_no_data = 0
                    ma_too_far = 0
                    ma_debug_log = []  # Debug: Sammle Infos über jeden Ticker
                    
                    for candidate in filtered:
                        ticker = candidate["Ticker"]
                        price = candidate["Preis"]
                        
                        # VP Lookback: max(MA-Bedarf, VP-Bedarf) — kein Extra-API-Call
                        vp_lookback = max(ma_period + 10, get_vp_lookback_for_strategy(current_strat)) if VP_AVAILABLE else ma_period + 10
                        
                        # Hole historische Daten MIT OHLCV für Volume Profile
                        if VP_AVAILABLE:
                            closes, ohlcv_bars = fetch_historical_closes(ticker, poly_key, days=vp_lookback, return_ohlcv=True)
                        else:
                            closes = fetch_historical_closes(ticker, poly_key, days=ma_period + 10)
                            ohlcv_bars = None
                        
                        if ma_checked % 10 == 9:
                            time.sleep(0.5)  # Rate Limiting: Pause nach je 10 Calls
                        
                        if not closes or len(closes) < ma_period:
                            ma_no_data += 1
                            # DEBUG: Logge warum kein History
                            ma_debug_log.append(f"❌ {ticker}: {len(closes) if closes else 0} Bars (brauche {ma_period})")
                            continue
                        
                        # Berechne MA
                        if ma_type == "SMA":
                            ma_value = calculate_sma(closes, ma_period)
                        else:
                            ma_value = calculate_ema(closes, ma_period)
                        
                        if not ma_value:
                            ma_no_data += 1
                            continue
                        
                        ma_checked += 1
                        
                        # Berechne Distanz zum MA
                        ma_distance = calculate_ma_distance(price, ma_value)
                        
                        if ma_distance is None:
                            continue

                        # Trend-Validierung: MA muss in richtige Richtung zeigen
                        ma_trend_valid = True
                        if len(closes) >= ma_period + 5:
                            if ma_type == "SMA":
                                ma_5ago = calculate_sma(closes[:-5], ma_period)
                            else:
                                ma_5ago = calculate_ema(closes[:-5], ma_period)

                            if ma_5ago and ma_value:
                                ma_slope = ((ma_value - ma_5ago) / ma_5ago) * 100
                                if ma_approach == "from_above" and ma_slope < -0.5:
                                    # Long bounce but MA is falling > 0.5% = invalid
                                    ma_trend_valid = False
                                    ma_debug_log.append(f"⛔ {ticker}: MA faellt ({ma_slope:+.2f}%) - kein Long Bounce")
                                elif ma_approach == "from_below" and ma_slope > 0.5:
                                    # Short bounce but MA is rising > 0.5% = invalid
                                    ma_trend_valid = False
                                    ma_debug_log.append(f"⛔ {ticker}: MA steigt ({ma_slope:+.2f}%) - kein Short Bounce")

                        if not ma_trend_valid:
                            ma_too_far += 1
                            continue

                        # Prüfe ob Setup gültig ist
                        is_valid = False
                        
                        if ma_approach == "from_above":
                            # Long: Preis nahe am MA (-1% bis +ma_distance_max%)
                            # Gelockert: Auch leicht unter MA ist OK (Bounce-Zone)
                            is_valid = -1.0 <= ma_distance <= ma_distance_max
                        else:
                            # Short: Preis nahe am MA (-ma_distance_max% bis +1%)
                            is_valid = -ma_distance_max <= ma_distance <= 1.0
                        
                        # DEBUG: Logge JEDEN geprüften Ticker
                        status_icon = "✅" if is_valid else "⛔"
                        ma_debug_log.append(
                            f"{status_icon} {ticker}: Preis=${price:.2f} | {ma_type}{ma_period}=${ma_value:.2f} | "
                            f"Dist={ma_distance:+.2f}% | Bars={len(closes)} | "
                            f"Band=[{-1.0 if ma_approach == 'from_above' else -ma_distance_max:.1f}% bis "
                            f"+{ma_distance_max if ma_approach == 'from_above' else 1.0:.1f}%]"
                        )
                        
                        if is_valid:
                            # Füge MA-Daten zum Ergebnis hinzu
                            candidate["MA_Value"] = round(ma_value, 2)
                            candidate["MA_Distance%"] = round(ma_distance, 2)
                            candidate["MA_Type"] = f"{ma_type}{ma_period}"
                            candidate["Alpha"] = round(100 - abs(ma_distance) * 20, 1)  # Näher am MA = höherer Score
                            
                            # ── Volume Profile Integration (V1.1) ──
                            vp_data = None
                            vp_signals = None
                            vp_summary = "N/A"
                            if VP_AVAILABLE and ohlcv_bars and len(ohlcv_bars) >= 40:
                                vp_data = vp_calculate_profile(
                                    ohlcv_bars, 
                                    lookback_days=get_vp_lookback_for_strategy(current_strat),
                                    atr_value=candidate.get("ATR%", None) and price * candidate["ATR%"] / 100
                                )
                                if vp_data:
                                    setup_direction = "short" if ma_approach != "from_above" else "long"
                                    strat_type = get_strategy_type_for_scanner(current_strat)
                                    vp_signals = vp_analyze_signals(
                                        vp_data, price, 
                                        atr=price * candidate.get("ATR%", 2.0) / 100 if candidate.get("ATR%") else None,
                                        direction=setup_direction,
                                        strategy_type=strat_type
                                    )
                                    vp_summary = f"VP: {vp_signals.get('summary', 'N/A')}"
                                    
                                    # VP Score-Adjustment auf SetupScore anwenden
                                    if vp_signals and "SetupScore" in candidate:
                                        vp_adj = vp_signals.get("score_adjustment", 0)
                                        candidate["SetupScore"] = min(100, max(0, candidate["SetupScore"] + vp_adj))
                            
                            candidate["VP"] = vp_data
                            candidate["VP_Signals"] = vp_signals
                            candidate["VP_Summary"] = vp_summary
                            
                            results.append(candidate)
                        else:
                            ma_too_far += 1
                        
                        # Progress Update
                        if ma_checked % 20 == 0:
                            status.update(label=f"Schritt 2/3: {ma_checked}/{len(filtered)} geprüft, {len(results)} Treffer...")
                    
                    # Sortiere nach SetupScore (VP-enhanced) falls vorhanden, sonst MA-Distanz
                    if results and "SetupScore" in results[0]:
                        results = sorted(results, key=lambda x: x.get("SetupScore", 0), reverse=True)[:50]
                    else:
                        results = sorted(results, key=lambda x: abs(x.get("MA_Distance%", 999)))[:50]
                    
                    st.session_state.scan_results = results
                    st.session_state.market_type = "Aktien"
                    
                    # Earnings Warning für MA Bounce Ergebnisse
                    try:
                        finnhub_key = st.secrets.get("FINNHUB_KEY", "")
                        if finnhub_key and results:
                            earnings_cal = fetch_earnings_calendar(finnhub_key, days_ahead=7)
                            if earnings_cal:
                                for r in results:
                                    ear_info = check_earnings_proximity(r.get("Ticker", ""), earnings_cal)
                                    if ear_info:
                                        r["EarningsWarning"] = ear_info
                                        penalty = ear_info.get("score_penalty", 0)
                                        if penalty and "SetupScore" in r:
                                            r["SetupScore"] = min(100, max(0, r["SetupScore"] + penalty))
                    except Exception:
                        pass
                    
                    direction_text = "Support (Long)" if ma_approach == "from_above" else "Resistance (Short)"
                    status.update(label=f"✅ {len(results)} {ma_type}{ma_period} {direction_text} Setups gefunden", state="complete")
                    
                    # DEBUG INFO — immer anzeigen bei 0 Ergebnissen
                    debug_msg = f"🔍 Pipeline: {len(candidates)} liquide Aktien → {len(filtered)} Kandidaten → {ma_checked} MA berechnet ({ma_no_data} kein History) → {ma_too_far} zu weit → {len(results)} im Band"
                    if len(results) == 0 or st.session_state.get("debug_mode", False):
                        st.caption(debug_msg)
                    
                    # DEBUG: Zeige Detail-Log für jeden geprüften Ticker
                    if ma_debug_log:
                        with st.expander(f"🔍 MA Debug Log ({len(ma_debug_log)} Ticker)", expanded=(len(results) == 0)):
                            # Zuerst die Treffer (✅), dann die Abgelehnten (⛔), dann fehlende Daten (❌)
                            hits = [l for l in ma_debug_log if l.startswith("✅")]
                            misses = [l for l in ma_debug_log if l.startswith("⛔")]
                            no_data = [l for l in ma_debug_log if l.startswith("❌")]
                            
                            if hits:
                                st.markdown(f"**✅ Treffer ({len(hits)}):**")
                                for line in hits:
                                    st.text(line)
                            
                            if misses:
                                st.markdown(f"**⛔ Zu weit vom MA ({len(misses)}):**")
                                for line in misses[:20]:  # Max 20 zeigen
                                    st.text(line)
                                if len(misses) > 20:
                                    st.text(f"... und {len(misses) - 20} weitere")
                            
                            if no_data:
                                st.markdown(f"**❌ Kein History ({len(no_data)}):**")
                                for line in no_data[:10]:
                                    st.text(line)
                                if len(no_data) > 10:
                                    st.text(f"... und {len(no_data) - 10} weitere")
                    
                    if len(results) == 0:
                        st.info(f"ℹ️ Keine Aktien im {ma_type}{ma_period} Band (−1% bis +{ma_distance_max}%). "
                               f"{'⚠️ Kein History für alle Aktien — Polygon API Problem?' if ma_no_data > 0 and ma_checked == 0 else 'Versuche später erneut.'}")
                    
                except KeyError:
                    st.error("❌ POLYGON_KEY fehlt in Secrets!")
                except Exception as e:
                    st.error(f"Fehler beim MA Bounce Scan: {e}")
                    import traceback
                    st.code(traceback.format_exc())

        # =================================================================
        # BREAKOUT IMMINENT 🔮 — wird OBEN in der Sidebar gehandelt (Background-Scan)
        # Dieser Block ist nur noch ein Fallback falls _bi_handled nicht griff
        # =================================================================
        elif is_breakout_imminent:
            pass  # Komplett in Sidebar-UI vor SCAN STARTEN verlagert

        elif not st.session_state.active_filters and not st.session_state.get("current_strategy"):
            st.warning("Erst Strategie laden!")
        else:
            # Trading Session für Aktien (automatisch oder manuell)
            session = st.session_state.get("active_trading_session", "Regular")
            exchange = st.session_state.get("selected_exchange", "US")
            
            with st.status(f"Scanne {m_type}...") as status:
                if m_type == "Krypto":
                    status.update(label="Scanne Krypto (24/7)...")
                    results, snp, sf = fetch_crypto_data()
                    
                    # Info wenn keine Ergebnisse
                    if len(results) == 0:
                        if "Gap %" in st.session_state.active_filters:
                            st.warning("⚠️ Keine Ergebnisse - Gap-Filter bei Krypto findet nichts (keine Gaps bei 24/7 Handel)")
                        else:
                            st.warning(f"⚠️ Keine Krypto gefunden mit aktuellen Filtern. {sf} von 250 Coins gefiltert.")
                            st.caption(f"Aktive Filter: {st.session_state.active_filters}")
                
                elif m_type == "Futures":
                    # FUTURES SCAN
                    futures_cat = st.session_state.get("selected_futures_cat", "INDEX")
                    cat_names = {
                        "INDEX": "📈 Index Futures",
                        "ENERGY": "🛢️ Energie Futures",
                        "METALS": "🥇 Metall Futures",
                        "AGRI": "🌾 Agrar Futures",
                        "RATES": "💵 Zins Futures"
                    }
                    status.update(label=f"Scanne {cat_names.get(futures_cat, futures_cat)}...")
                    results, snp, sf = fetch_futures_data(futures_cat)
                    
                    if len(results) == 0:
                        st.warning(f"⚠️ Keine Ergebnisse für {cat_names.get(futures_cat)} mit aktuellen Filtern")
                
                elif m_type == "Forex":
                    # FOREX SCAN
                    forex_cat = st.session_state.get("selected_forex_cat", "MAJORS")
                    cat_names = {
                        "MAJORS": "💵 Major Pairs",
                        "MINORS": "🌍 Minor Pairs",
                        "EXOTICS": "🌏 Exotic Pairs"
                    }
                    status.update(label=f"Scanne {cat_names.get(forex_cat, forex_cat)}...")
                    
                    # DEBUG
                    with st.expander("🔍 DEBUG: Forex Scan-State", expanded=True):
                        st.write(f"**Kategorie:** {forex_cat}")
                        st.write(f"**Strategie:** {st.session_state.get('current_strategy', 'KEINE')}")
                        st.write(f"**active_filters:** {st.session_state.active_filters}")
                        st.write(f"**additional_filters:** {st.session_state.additional_filters}")
                    
                    results, snp, sf, _forex_debug = fetch_forex_data(forex_cat)
                    
                    # DEBUG
                    with st.expander("🔍 DEBUG: Forex Ergebnis", expanded=True):
                        st.write(f"**results:** {len(results)}, **skipped_price:** {snp}, **skipped_filter:** {sf}")
                        if results:
                            st.write(f"**Erster:** {results[0]}")
                        for _dl in _forex_debug:
                            st.text(_dl)
                    
                    if len(results) == 0:
                        st.warning(f"⚠️ Keine Ergebnisse für {cat_names.get(forex_cat)} mit aktuellen Filtern")
                
                elif exchange == "US":
                    # US-Aktien mit Polygon.io
                    session_labels = {
                        "Regular": "Regular Hours (9:30-16:00)",
                        "Pre-Market": "Pre-Market (4:00-9:30)",
                        "After-Hours": "After-Hours (16:00-20:00)",
                        "Extended": "Extended Hours (Pre+Regular+After)"
                    }
                    status.update(label=f"🇺🇸 Scanne USA {session_labels.get(session, session)}...")
                    
                    # RVOL-Warnung für Pre/Post Market
                    if session in ["Pre-Market", "After-Hours"] and "RVOL" in st.session_state.active_filters:
                        st.warning("⚠️ **RVOL im Pre/Post-Market ungenau!** RVOL vergleicht mit Tagesvolumen, aber der Tag hat gerade erst begonnen. Nutze besser PM/AH-Strategien ohne RVOL.")
                    
                    poly_key = st.secrets["POLYGON_KEY"]
                    results, snp, sf, debug_stats = fetch_stock_data(poly_key, session=session)
                    
                    # Zeige Filter-Statistiken wenn 0 Ergebnisse (hilft bei Troubleshooting)
                    if len(results) == 0 and debug_stats:
                        with st.expander("❓ Warum 0 Ergebnisse?", expanded=True):
                            st.write(f"**Gesamt geprüfte Aktien:** {debug_stats.get('total_tickers', 0):,}")
                            st.write(f"**Session:** {session}")
                            st.write(f"**Strategie:** {st.session_state.get('current_strategy', 'Keine')}")
                            
                            st.write("**Gefiltert wegen:**")
                            cols = st.columns(5)
                            with cols[0]:
                                st.metric("Change%", debug_stats.get('skipped_change', 0))
                            with cols[1]:
                                st.metric("RVOL", debug_stats.get('skipped_rvol', 0))
                            with cols[2]:
                                st.metric("Close Pos", debug_stats.get('skipped_closepos', 0))
                            with cols[3]:
                                st.metric("Vortag%", debug_stats.get('skipped_vortag', 0))
                            with cols[4]:
                                st.metric("Andere", debug_stats.get('skipped_other', 0))
                            
                            # Zeige aktive Filter
                            st.write(f"**Aktive Filter:** {st.session_state.active_filters}")
                            
                            # Zeige Close Position Samples
                            samples = debug_stats.get('closepos_samples', [])
                            if samples:
                                st.write(f"**Close Position Werte (Sample):** {samples}")
                                avg_cp = sum(samples) / len(samples)
                                st.write(f"**Durchschnitt:** {avg_cp:.2f}")
                                
                                # Dynamisch den Filter-Wert auslesen
                                cp_filter = st.session_state.active_filters.get("Close Position", (0, 1))
                                st.info(f"💡 Dein Filter: {cp_filter[0]}-{cp_filter[1]} | Durchschnitt: {avg_cp:.2f}")
                            else:
                                st.warning("⚠️ Keine Close Position Samples - Range zu klein oder Session Problem")
                    
                    # Info wenn wenig Ergebnisse
                    if len(results) < 5 and session in ["Pre-Market", "After-Hours"]:
                        st.info(f"ℹ️ Wenige {session} Ergebnisse ({len(results)}) - probiere PM/AH-Strategien ohne RVOL!")
                
                else:
                    # INTERNATIONALE BÖRSEN mit Yahoo Finance
                    exchange_names = {
                        "DE": "🇩🇪 Deutschland (XETRA)",
                        "UK": "🇬🇧 UK (London)",
                        "CH": "🇨🇭 Schweiz (SIX)",
                        "EU": "🇪🇺 Europa (Euronext)",
                        "JP": "🇯🇵 Japan (Tokyo)",
                        "HK": "🇭🇰 Hong Kong"
                    }
                    exchange_name = exchange_names.get(exchange, exchange)
                    status.update(label=f"Scanne {exchange_name}...")
                    
                    # DEBUG: Zeige State vor Scan
                    with st.expander("🔍 DEBUG: Scan-State", expanded=True):
                        st.write(f"**Exchange:** {exchange}")
                        st.write(f"**Strategie:** {st.session_state.get('current_strategy', 'KEINE')}")
                        st.write(f"**active_filters:** {st.session_state.active_filters}")
                        st.write(f"**additional_filters:** {st.session_state.additional_filters}")
                        st.write(f"**m_type:** {m_type}")
                        st.write(f"**filter_reset_counter:** {st.session_state.get('filter_reset_counter', 0)}")
                    
                    results, snp, sf, _intl_debug = fetch_international_stock_data(exchange)
                    
                    # DEBUG: Zeige Ergebnis
                    with st.expander("🔍 DEBUG: Scan-Ergebnis", expanded=True):
                        st.write(f"**results:** {len(results)}")
                        st.write(f"**skipped_no_price:** {snp}")
                        st.write(f"**skipped_filter:** {sf}")
                        if results:
                            st.write(f"**Erster Treffer:** {results[0]}")
                        for _dl in _intl_debug:
                            st.text(_dl)
                    
                    if len(results) == 0:
                        st.warning(f"⚠️ Keine Ergebnisse für {exchange_name} mit aktuellen Filtern")
                        st.caption(f"💡 Tipp: Wähle **🌍 Alle zeigen** um alle Aktien zu sehen, oder **🌍 Gewinner/Verlierer** für weniger strenge Filter")
                    elif sf > 0:
                        st.caption(f"📊 {len(results)} Treffer | {sf} ausgefiltert | RVOL nach Tageszeit normalisiert")
                
                st.session_state.scan_results = sorted(results, key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), reverse=True)
                
                # =============================================================
                # K1: MULTI-DAY PATTERN VALIDATION (wenn needs_history=True)
                # =============================================================
                current_strategies = get_strategies_for_market(m_type, exchange=exchange)
                strategy_data = current_strategies.get(st.session_state.get("current_strategy", ""), {})
                
                # SKIP K1 fuer Breakout Imminent — wird in eigenem elif-Block verarbeitet
                pattern_type_k1 = strategy_data.get("pattern_type", "")
                if strategy_data.get("needs_history") and m_type == "Aktien" and exchange == "US" and not pattern_type_k1.startswith("breakout_imminent"):
                    try:
                        poly_key = st.secrets["POLYGON_KEY"]
                        pattern_type = pattern_type_k1 or "consolidation"
                        history_days = strategy_data.get("history_days", 5)

                        status.update(label=f"📊 Validiere Multi-Day Pattern ({pattern_type})...")
                        
                        validated_results = []
                        checked = 0
                        for r in st.session_state.scan_results[:30]:  # Max 30 API-Calls
                            ticker = r.get("Ticker", "")
                            if not ticker:
                                continue
                            
                            bars = fetch_multi_day_data(ticker, poly_key, days=history_days)
                            checked += 1
                            
                            if bars and len(bars) >= 3:
                                is_valid, pat_score, details = analyze_multi_day_pattern(bars, pattern_type)
                                if is_valid:
                                    r["PatternScore"] = pat_score
                                    r["PatternDetails"] = details
                                    validated_results.append(r)
                            else:
                                # Keine History = trotzdem behalten aber mit Score 0
                                r["PatternScore"] = 0
                                r["PatternDetails"] = ["⚠️ Keine Multi-Day Daten"]
                                validated_results.append(r)
                            
                            if checked % 10 == 0:
                                time.sleep(0.5)  # Rate Limiting
                        
                        if validated_results:
                            st.session_state.scan_results = sorted(validated_results, key=lambda x: x.get("PatternScore", 0), reverse=True)
                            status.update(label=f"✅ {len(validated_results)} validierte Patterns (von {len(results)})")
                        else:
                            status.update(label=f"⚠️ Keine Ergebnisse nach Multi-Day Validierung")
                    except Exception as e:
                        if st.session_state.get("debug_mode"):
                            st.warning(f"Multi-Day Validierung Fehler: {e}")
                
                # K2: SIGNAL SIGNIFICANCE CHECK
                if m_type == "Aktien" and st.session_state.scan_results:
                    sig_results = []
                    for r in st.session_state.scan_results:
                        atr = r.get("ATR%", 0)
                        chg = r.get("Chg%", 0)
                        if atr > 0 and not is_signal_significant(abs(chg), atr, multiplier=1.0):
                            r["SignalWeak"] = True  # Markiere schwache Signale
                        sig_results.append(r)
                    st.session_state.scan_results = sig_results
                
                # =============================================================
                # K3: VOLUME PROFILE ENRICHMENT (Standard-Pipeline)
                # VP für Top-Ergebnisse berechnen und SetupScore anpassen
                # =============================================================
                VP_ENRICHMENT_STRATEGIES = {
                    # Breakout-Typ
                    "Breakout Long", "Breakdown Short", "Early Momentum",
                    "Whale Watch", "Whale Watch Short 🐻", "Volume Surge",
                    "Gap Up", "Gap Down", "Gap Up (High Vol)", "Gap Down (High Vol)",
                    "PM Gap & Go 🌅", "Penny Rockets",
                    # Bounce-Typ  
                    "Dip Buy", "Reversal Hunter",
                    # Flag (bekommen auch VP nach Multi-Day)
                    "Bull Flag", "Bear Flag",
                    # Breite Strategien
                    "Breakout Long (Ultra)", "Gap Up Momentum (Ultra)",
                    "PM Gainers 🌅",
                }
                
                current_strat_vp = st.session_state.get("current_strategy", "")
                vp_should_run = (
                    VP_AVAILABLE 
                    and m_type == "Aktien" 
                    and exchange == "US"
                    and current_strat_vp in VP_ENRICHMENT_STRATEGIES
                    and st.session_state.scan_results
                )
                
                if vp_should_run:
                    try:
                        poly_key = st.secrets["POLYGON_KEY"]
                        strat_type = get_strategy_type_for_scanner(current_strat_vp)
                        vp_lookback = get_vp_lookback_for_strategy(current_strat_vp)
                        
                        # Richtung aus Strategie
                        SHORT_KW = ["Short", "Bear", "Breakdown", "Losers", "Down"]
                        vp_direction = "short" if any(kw in current_strat_vp for kw in SHORT_KW) else "long"
                        
                        top_results = st.session_state.scan_results[:30]
                        status.update(label=f"📊 Volume Profile für Top {len(top_results)} Aktien...")
                        
                        vp_enriched = 0
                        for idx, r in enumerate(top_results):
                            try:
                                ticker = r.get("Ticker", "")
                                price = r.get("Preis", 0)
                                if not ticker or price <= 0:
                                    continue
                                
                                # OHLCV holen (gleicher Endpoint wie fetch_historical_closes)
                                result = fetch_historical_closes(ticker, poly_key, days=vp_lookback, return_ohlcv=True)
                                if result is None or not isinstance(result, tuple):
                                    continue
                                closes, ohlcv_bars = result
                                
                                if not ohlcv_bars or len(ohlcv_bars) < 40:
                                    continue
                                
                                # VP berechnen
                                atr_val = price * r.get("ATR%", 2.0) / 100 if r.get("ATR%") else None
                                vp_data = vp_calculate_profile(
                                    ohlcv_bars, 
                                    lookback_days=vp_lookback,
                                    atr_value=atr_val
                                )
                                
                                if vp_data:
                                    vp_signals = vp_analyze_signals(
                                        vp_data, price,
                                        atr=atr_val,
                                        direction=vp_direction,
                                        strategy_type=strat_type
                                    )
                                    vp_summary = f"VP: {vp_signals.get('summary', 'N/A')}"
                                    
                                    # SetupScore anpassen
                                    if vp_signals and "SetupScore" in r:
                                        vp_adj = vp_signals.get("score_adjustment", 0)
                                        r["SetupScore"] = min(100, max(0, r["SetupScore"] + vp_adj))
                                    
                                    r["VP"] = vp_data
                                    r["VP_Signals"] = vp_signals
                                    r["VP_Summary"] = vp_summary
                                    vp_enriched += 1
                                
                                # Rate Limiting
                                if (idx + 1) % 10 == 0:
                                    time.sleep(0.3)
                                    status.update(label=f"📊 VP: {idx+1}/{len(top_results)} analysiert...")
                                    
                            except Exception:
                                continue
                        
                        # Re-sort nach VP-adjustiertem SetupScore
                        if vp_enriched > 0:
                            st.session_state.scan_results = sorted(
                                st.session_state.scan_results,
                                key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), 
                                reverse=True
                            )
                            status.update(label=f"✅ VP für {vp_enriched}/{len(top_results)} Aktien berechnet")
                        
                    except Exception as e:
                        if st.session_state.get("debug_mode"):
                            st.warning(f"VP Enrichment Fehler: {e}")
                
                # =============================================================
                # K4: EARNINGS WARNING ENRICHMENT
                # Prüft ob Scan-Ergebnisse bald Earnings haben
                # =============================================================
                if m_type == "Aktien" and st.session_state.scan_results:
                    try:
                        finnhub_key = st.secrets.get("FINNHUB_KEY", "")
                        if finnhub_key:
                            status.update(label="📅 Prüfe Earnings Calendar...")
                            earnings_cal = fetch_earnings_calendar(finnhub_key, days_ahead=7)
                            
                            if earnings_cal:
                                earnings_found = 0
                                for r in st.session_state.scan_results:
                                    ticker = r.get("Ticker", "")
                                    ear_info = check_earnings_proximity(ticker, earnings_cal)
                                    if ear_info:
                                        r["EarningsWarning"] = ear_info
                                        earnings_found += 1
                                        
                                        # SetupScore-Penalty
                                        penalty = ear_info.get("score_penalty", 0)
                                        if penalty and "SetupScore" in r:
                                            r["SetupScore"] = min(100, max(0, r["SetupScore"] + penalty))
                                
                                if earnings_found > 0:
                                    # Re-sort nach Penalty
                                    st.session_state.scan_results = sorted(
                                        st.session_state.scan_results,
                                        key=lambda x: x.get("SetupScore", x.get("Alpha", 0)),
                                        reverse=True
                                    )
                    except Exception:
                        pass  # Kein Finnhub Key = kein Earnings Check
                
                # Session-Info in Status
                if m_type == "Futures":
                    status.update(label=f"✅ {len(st.session_state.scan_results)} Futures Signale", state="complete")
                elif m_type == "Forex":
                    status.update(label=f"✅ {len(st.session_state.scan_results)} Forex Signale", state="complete")
                elif m_type == "Aktien" and exchange != "US":
                    exchange_flag = {"DE": "🇩🇪", "UK": "🇬🇧", "CH": "🇨🇭", "EU": "🇪🇺", "JP": "🇯🇵", "HK": "🇭🇰"}.get(exchange, "🌍")
                    status.update(label=f"✅ {len(st.session_state.scan_results)} {exchange_flag} Signale", state="complete")
                elif m_type == "Aktien" and session != "Regular":
                    status.update(label=f"✅ {len(st.session_state.scan_results)} {session} Signale", state="complete")
                else:
                    status.update(label=f"✅ {len(st.session_state.scan_results)} Signale", state="complete")

# -----------------------------------------------------------------------------
# OPTIONS UNUSUAL ACTIVITY DETECTION (Free Polygon Plan)
# -----------------------------------------------------------------------------

@st.cache_data(ttl=900)  # 15 Min Cache — Options-Daten ändern sich nicht so schnell
def _detect_unusual_options(ticker, current_price, poly_key):
    """
    Erkennt ungewöhnliche Optionsaktivität für einen Ticker.

    Strategie (Free Plan optimiert — minimale API Calls):
    1. Reference API → ATM-Kontrakte identifizieren (1 Call)
    2. Aggregates für 6 wichtigste ATM-Strikes (6 Calls)
    3. RVOL-Vergleich: aktuelles Volume vs 20-Tage-Avg

    Args:
        ticker: Aktien-Ticker (z.B. "MRNA")
        current_price: Aktueller Aktienpreis
        poly_key: Polygon API Key

    Returns:
        dict: {has_unusual, signals, put_call_ratio, total_call_vol, total_put_vol, label}
    """
    try:
        from datetime import timedelta as _td
        _now = datetime.now()
        _empty = {"has_unusual": False, "signals": [], "put_call_ratio": 0,
                  "total_call_vol": 0, "total_put_vol": 0, "label": ""}

        if not ticker or not current_price or current_price <= 0 or not poly_key:
            return _empty

        # ── Schritt 1: ATM-Zone bestimmen ──
        # ATM = ±15% vom aktuellen Preis, gerundet auf gängige Strikes
        _atm_low = current_price * 0.85
        _atm_high = current_price * 1.15

        # Nächste Expiry: 7-30 Tage in der Zukunft
        _exp_min = (_now + _td(days=3)).strftime("%Y-%m-%d")
        _exp_max = (_now + _td(days=45)).strftime("%Y-%m-%d")

        # ── Schritt 2: Reference API → ATM Kontrakte (1 Call für Calls, 1 für Puts) ──
        _ref_url = "https://api.polygon.io/v3/reference/options/contracts"
        _atm_contracts = {"calls": [], "puts": []}

        for _ctype in ["call", "put"]:
            _params = {
                "underlying_ticker": ticker,
                "contract_type": _ctype,
                "expired": "false",
                "strike_price.gte": round(_atm_low, 0),
                "strike_price.lte": round(_atm_high, 0),
                "expiration_date.gte": _exp_min,
                "expiration_date.lte": _exp_max,
                "limit": 100,
                "apiKey": poly_key,
                "order": "asc",
                "sort": "strike_price",
            }
            try:
                _resp = rate_limited_get(_ref_url, params=_params, timeout=10)
                if _resp.status_code == 200:
                    _contracts = _resp.json().get("results", [])
                    _atm_contracts["calls" if _ctype == "call" else "puts"] = _contracts
            except Exception:
                pass

        if not _atm_contracts["calls"] and not _atm_contracts["puts"]:
            return _empty

        # ── Schritt 3: Wähle die 3 nächsten ATM-Strikes pro Typ ──
        # Sortiere nach Nähe zum aktuellen Preis
# _pick_top_strikes — Moved to modules/helpers.py

        _selected_calls = _pick_top_strikes(_atm_contracts["calls"], 3, current_price)
        _selected_puts = _pick_top_strikes(_atm_contracts["puts"], 3, current_price)
        _selected = [(c, "CALL") for c in _selected_calls] + [(c, "PUT") for c in _selected_puts]

        if not _selected:
            return _empty

        # ── Schritt 4: Aggregate Volume für jeden ausgewählten Strike (6 Calls) ──
        _end = _now
        _start = _now - _td(days=30)
        _contract_data = []

        for _contract, _ctype in _selected:
            _oticker = _contract.get("ticker", "")
            if not _oticker:
                continue

            try:
                _agg_url = f"https://api.polygon.io/v2/aggs/ticker/{_oticker}/range/1/day/{_start.strftime('%Y-%m-%d')}/{_end.strftime('%Y-%m-%d')}"
                _agg_params = {"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 50}
                _agg_resp = rate_limited_get(_agg_url, params=_agg_params, timeout=10)

                if _agg_resp.status_code == 200:
                    _bars = _agg_resp.json().get("results", [])
                    if _bars:
                        _volumes = [b.get("v", 0) for b in _bars]
                        _avg_vol = sum(_volumes[:-1]) / max(len(_volumes) - 1, 1) if len(_volumes) > 1 else _volumes[0]
                        _last_vol = _volumes[-1]
                        _rvol = _last_vol / _avg_vol if _avg_vol > 0 else 0

                        _contract_data.append({
                            "ticker": _oticker,
                            "type": _ctype,
                            "strike": _contract.get("strike_price", 0),
                            "expiry": _contract.get("expiration_date", ""),
                            "last_vol": _last_vol,
                            "avg_vol": round(_avg_vol, 1),
                            "rvol": round(_rvol, 1),
                            "days_data": len(_bars),
                        })
            except Exception:
                pass

        if not _contract_data:
            return _empty

        # ── Schritt 5: Anomalie-Detection ──
        _signals = []
        _total_call_vol = sum(c.get("last_vol", 0) for c in _contract_data if c.get("type", 0) == "CALL")
        _total_put_vol = sum(c.get("last_vol", 0) for c in _contract_data if c.get("type", 0) == "PUT")
        _pc_ratio = _total_put_vol / _total_call_vol if _total_call_vol > 0 else 0

        for c in _contract_data:
            if c.get("rvol", 0) >= 3.0:
                _signals.append({
                    "severity": "HIGH",
                    "type": c.get("type", 0),
                    "strike": c.get("strike", 0),
                    "rvol": c.get("rvol", 0),
                    "vol": c.get("last_vol", 0),
                    "avg": c.get("avg_vol", 0),
                    "label": f"🔴 {c['type']} ${c['strike']:.0f}: {c['rvol']:.1f}x Volumen ({c['last_vol']} vs avg {c['avg_vol']:.0f})"
                })
            elif c.get("rvol", 0) >= 2.0:
                _signals.append({
                    "severity": "MEDIUM",
                    "type": c.get("type", 0),
                    "strike": c.get("strike", 0),
                    "rvol": c.get("rvol", 0),
                    "vol": c.get("last_vol", 0),
                    "avg": c.get("avg_vol", 0),
                    "label": f"🟡 {c['type']} ${c['strike']:.0f}: {c['rvol']:.1f}x Volumen ({c['last_vol']} vs avg {c['avg_vol']:.0f})"
                })

        # Put/Call Ratio Anomalie
        if _pc_ratio >= 2.0:
            _signals.append({
                "severity": "HIGH", "type": "P/C_RATIO", "strike": 0,
                "rvol": _pc_ratio, "vol": _total_put_vol, "avg": _total_call_vol,
                "label": f"🔴 Put/Call Ratio: {_pc_ratio:.1f} — starke Put-Aktivität (bearish Signal)"
            })
        elif _pc_ratio <= 0.3 and _total_call_vol > 50:
            _signals.append({
                "severity": "MEDIUM", "type": "P/C_RATIO", "strike": 0,
                "rvol": _pc_ratio, "vol": _total_call_vol, "avg": _total_put_vol,
                "label": f"🟡 Put/Call Ratio: {_pc_ratio:.2f} — starke Call-Aktivität (bullish Signal)"
            })

        # Gesamt-Label
        _label = ""
        _has_unusual = len(_signals) > 0
        if _has_unusual:
            _top = _signals[0]
            _label = _top["label"]
        else:
            _label = f"✅ Normal (C:{_total_call_vol} P:{_total_put_vol} P/C:{_pc_ratio:.2f})"

        return {
            "has_unusual": _has_unusual,
            "signals": _signals,
            "put_call_ratio": round(_pc_ratio, 2),
            "total_call_vol": _total_call_vol,
            "total_put_vol": _total_put_vol,
            "label": _label,
            "contracts_checked": len(_contract_data),
        }

    except Exception:
        return {"has_unusual": False, "signals": [], "put_call_ratio": 0,
                "total_call_vol": 0, "total_put_vol": 0, "label": ""}


def _render_options_activity_banner(ticker, current_price, poly_key):
    """Rendert ein Options-Activity Banner im Biotech Scanner Detail View."""
    if not ticker or not current_price or not poly_key:
        return

    try:
        _data = _detect_unusual_options(ticker, current_price, poly_key)
        if not _data or not _data.get("contracts_checked"):
            return

        if _data["has_unusual"]:
            _signals = _data["signals"]
            _high = [s for s in _signals if s["severity"] == "HIGH"]
            _medium = [s for s in _signals if s["severity"] == "MEDIUM"]

            if _high:
                st.error(f"🎰 **UNUSUAL OPTIONS ACTIVITY** — {_signals[0]['label']}")
                for s in _signals[1:3]:
                    st.warning(s["label"])
            elif _medium:
                st.warning(f"🎰 **Options-Signal** — {_signals[0]['label']}")
        else:
            # Nur im Detail View anzeigen, nicht als Banner
            _pc = _data["put_call_ratio"]
            _cv = _data["total_call_vol"]
            _pv = _data["total_put_vol"]
            if _cv + _pv > 0:
                st.caption(f"🎰 Options: Call Vol {_cv} | Put Vol {_pv} | P/C Ratio {_pc:.2f}")
    except Exception:
        pass


# -----------------------------------------------------------------------------
# SEKTOR-TREND CONTEXT BANNER (Helper)
# -----------------------------------------------------------------------------
# SIC Code → SPDR Sektor ETF Mapping
_SIC_TO_SECTOR = {
    # Technology (XLK) — NUR spezifische 4-Digit Codes, keine 2-Digit Prefixe
    # (35xx=Industrial Machinery, 36xx=Electronic, 37xx=Transportation → zu breit für XLK)
    "7371": "XLK", "7372": "XLK", "7373": "XLK", "7374": "XLK", "7375": "XLK", "7376": "XLK", "7377": "XLK", "7378": "XLK", "7379": "XLK",
    "3559": "XLK", "3669": "XLK", "3672": "XLK", "3674": "XLK", "3679": "XLK",
    "3577": "XLK", "3661": "XLK", "3663": "XLK", "3678": "XLK",
    # Healthcare (XLV)
    "28": "XLV", "80": "XLV",
    "2830": "XLV", "2833": "XLV", "2834": "XLV", "2835": "XLV", "2836": "XLV",
    "3841": "XLV", "3842": "XLV", "3843": "XLV", "3844": "XLV", "3845": "XLV", "3851": "XLV",
    "5912": "XLV", "8000": "XLV", "8011": "XLV", "8049": "XLV", "8050": "XLV", "8060": "XLV", "8071": "XLV", "8082": "XLV", "8090": "XLV",
    # Financials (XLF)
    "60": "XLF", "61": "XLF", "62": "XLF", "63": "XLF", "64": "XLF", "67": "XLF",
    "6020": "XLF", "6021": "XLF", "6022": "XLF", "6035": "XLF", "6036": "XLF",
    "6141": "XLF", "6153": "XLF", "6159": "XLF", "6162": "XLF", "6163": "XLF",
    "6199": "XLF", "6200": "XLF", "6211": "XLF", "6282": "XLF", "6311": "XLF", "6321": "XLF", "6324": "XLF", "6331": "XLF", "6399": "XLF",
    # Energy (XLE)
    "13": "XLE", "29": "XLE",
    "1311": "XLE", "1381": "XLE", "1382": "XLE", "1389": "XLE",
    "2911": "XLE", "2990": "XLE",
    # Consumer Discretionary (XLY)
    "25": "XLY", "53": "XLY", "54": "XLY", "55": "XLY", "56": "XLY", "57": "XLY", "58": "XLY", "59": "XLY", "70": "XLY", "72": "XLY", "78": "XLY", "79": "XLY",
    "5311": "XLY", "5411": "XLY", "5812": "XLY", "5944": "XLY", "5945": "XLY", "5961": "XLY", "7011": "XLY",
    # Consumer Staples (XLP)
    "20": "XLP", "21": "XLP",
    "2000": "XLP", "2011": "XLP", "2013": "XLP", "2020": "XLP", "2030": "XLP", "2040": "XLP", "2050": "XLP", "2060": "XLP", "2080": "XLP", "2086": "XLP", "2090": "XLP",
    "2100": "XLP", "2111": "XLP",
    # Industrials (XLI)
    "15": "XLI", "16": "XLI", "17": "XLI", "34": "XLI", "40": "XLI", "42": "XLI", "44": "XLI", "45": "XLI",
    "3714": "XLI", "3720": "XLI", "3721": "XLI", "3724": "XLI", "3728": "XLI", "3743": "XLI",
    "4011": "XLI", "4013": "XLI", "4210": "XLI", "4213": "XLI", "4412": "XLI", "4512": "XLI", "4522": "XLI", "4581": "XLI",
    # Materials (XLB)
    "10": "XLB", "12": "XLB", "14": "XLB", "24": "XLB", "26": "XLB", "30": "XLB", "32": "XLB", "33": "XLB",
    # Utilities (XLU)
    "49": "XLU",
    "4911": "XLU", "4922": "XLU", "4923": "XLU", "4924": "XLU", "4931": "XLU", "4932": "XLU", "4941": "XLU",
    # Real Estate (XLRE)
    "65": "XLRE", "6500": "XLRE", "6510": "XLRE", "6512": "XLRE", "6552": "XLRE", "6798": "XLRE",
    # Communication Services (XLC)
    "27": "XLC", "48": "XLC",
    "4812": "XLC", "4813": "XLC", "4822": "XLC", "4833": "XLC", "4841": "XLC", "4899": "XLC",
    "7311": "XLC", "7812": "XLC", "7819": "XLC", "7822": "XLC",
}

_SECTOR_ETF_META = {
    "XLK": {"name": "Technology", "emoji": "💻"},
    "XLF": {"name": "Financials", "emoji": "🏦"},
    "XLE": {"name": "Energy", "emoji": "⚡"},
    "XLV": {"name": "Healthcare", "emoji": "🏥"},
    "XLI": {"name": "Industrials", "emoji": "🏭"},
    "XLY": {"name": "Consumer Disc.", "emoji": "🛒"},
    "XLP": {"name": "Consumer Staples", "emoji": "🥫"},
    "XLU": {"name": "Utilities", "emoji": "💡"},
    "XLB": {"name": "Materials", "emoji": "🧱"},
    "XLRE": {"name": "Real Estate", "emoji": "🏠"},
    "XLC": {"name": "Communication", "emoji": "📱"},
}

# Bekannte Ticker → Sektor Overrides (für Mega-Caps die jeder kennt)
_TICKER_SECTOR_OVERRIDE = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "GOOGL": "XLC", "GOOG": "XLC",
    "META": "XLC", "AMZN": "XLY", "TSLA": "XLY", "NFLX": "XLC", "DIS": "XLC",
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF", "WFC": "XLF",
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "OXY": "XLE",
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV", "ABBV": "XLV", "MRK": "XLV", "LLY": "XLV",
    "AVGO": "XLK", "AMD": "XLK", "INTC": "XLK", "MU": "XLK", "QCOM": "XLK", "TXN": "XLK",
    "CRM": "XLK", "ORCL": "XLK", "ADBE": "XLK", "NOW": "XLK", "INTU": "XLK", "PLTR": "XLK",
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP",
    "CAT": "XLI", "DE": "XLI", "BA": "XLI", "UPS": "XLI", "HON": "XLI", "GE": "XLI",
    "LIN": "XLB", "APD": "XLB", "NEM": "XLB", "FCX": "XLB",
    "NEE": "XLU", "DUK": "XLU", "SO": "XLU", "D": "XLU",
    "AMT": "XLRE", "PLD": "XLRE", "SPG": "XLRE", "O": "XLRE",
    "T": "XLC", "VZ": "XLC", "CMCSA": "XLC", "TMUS": "XLC",
    "V": "XLK", "MA": "XLK", "PYPL": "XLK", "SQ": "XLK",
    "COIN": "XLF", "HOOD": "XLF", "SOFI": "XLF", "AFRM": "XLK",
    "MRNA": "XLV", "BNTX": "XLV", "CRSP": "XLV", "REGN": "XLV", "GILD": "XLV", "BIIB": "XLV", "VRTX": "XLV",
}


# _resolve_sector_etf — Moved to modules/helpers.py


@st.cache_data(ttl=600)  # 10 Min Cache (konsistent mit Money Flow Tab)
def _fetch_sector_etf_performance(poly_key):
    """Holt aktuelle Tagesperformance für alle 11 SPDR Sektor-ETFs."""
    results = {}
    from datetime import timedelta
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5)  # Letzte 5 Tage um Wochenende abzudecken
    for etf in _SECTOR_ETF_META:
        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/{etf}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 10}, timeout=8)
            if resp.status_code == 200:
                bars = resp.json().get("results", [])
                if bars and len(bars) >= 2:
                    prev_close = bars[-2]["c"]
                    last_close = bars[-1]["c"]
                    chg = ((last_close - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    results[etf] = round(chg, 2)
        except Exception:
            pass
    return results


def _render_sector_trend_banner(ticker, sic_code="", poly_key=None, all_tickers=None):
    """
    Zeigt Sektor-Trend Banner für einen oder mehrere Ticker.
    - Einzelner Ticker: zeigt dessen Sektor + Performance
    - all_tickers: Liste von (ticker, sic_code) → zeigt Top-Sektoren der Ergebnisse
    """
    if not poly_key:
        return
    try:
        perf = _fetch_sector_etf_performance(poly_key)
        if not perf:
            return

        if all_tickers and len(all_tickers) > 0:
            # Sammle welche Sektoren in den Ergebnissen vertreten sind
            sector_counts = {}
            for t, sic in all_tickers:
                etf = _resolve_sector_etf(t, sic)
                if etf and etf in perf:
                    sector_counts[etf] = sector_counts.get(etf, 0) + 1

            if not sector_counts:
                return

            # Sortiere nach Häufigkeit, zeige Top 5
            top_sectors = sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            parts = []
            for etf, count in top_sectors:
                meta = _SECTOR_ETF_META.get(etf, {})
                chg = perf.get(etf, 0)
                emoji = meta.get("emoji", "")
                name = meta.get("name", etf)
                color = "🟢" if chg > 0.3 else ("🔴" if chg < -0.3 else "⚪")
                parts.append(f"{emoji} {name} **{chg:+.1f}%** {color} ({count})")

            st.markdown(f"📊 **Sektor-Trend:** {' · '.join(parts)}")

        elif ticker:
            etf = _resolve_sector_etf(ticker, sic_code)
            if etf and etf in perf:
                meta = _SECTOR_ETF_META.get(etf, {})
                chg = perf.get(etf, 0)
                emoji = meta.get("emoji", "")
                name = meta.get("name", etf)
                if chg > 1.5:
                    st.success(f"📊 **Sektor-Trend:** {emoji} {name} ({etf}) **{chg:+.1f}%** — Starker Rückenwind")
                elif chg > 0.3:
                    st.info(f"📊 **Sektor-Trend:** {emoji} {name} ({etf}) **{chg:+.1f}%** — Leichter Rückenwind")
                elif chg < -1.5:
                    st.error(f"📊 **Sektor-Trend:** {emoji} {name} ({etf}) **{chg:+.1f}%** — Starker Gegenwind")
                elif chg < -0.3:
                    st.warning(f"📊 **Sektor-Trend:** {emoji} {name} ({etf}) **{chg:+.1f}%** — Leichter Gegenwind")
                else:
                    st.caption(f"📊 Sektor-Trend: {emoji} {name} ({etf}) {chg:+.1f}% — Neutral")
    except Exception:
        pass


# =============================================================================
# 🔴 CRASH MONITOR — SPY/VIX/Breadth/Sektoren
# =============================================================================

# Sektor-ETFs für Rotation-Analyse
SECTOR_ETFS = {
    "XLK": ("Tech", "💻"), "XLF": ("Financials", "🏦"), "XLV": ("Healthcare", "🏥"),
    "XLP": ("Consumer Staples", "🛒"), "XLU": ("Utilities", "⚡"), "XLE": ("Energy", "🛢️"),
    "XLI": ("Industrials", "🏭"), "XLB": ("Materials", "⛏️"), "XLRE": ("Real Estate", "🏠"),
    "XLC": ("Communication", "📡"), "XLY": ("Consumer Disc.", "🛍️"),
}

# Defensive vs Risk-On Sektoren
DEFENSIVE_SECTORS = {"XLP", "XLU", "XLV", "XLRE"}
RISK_ON_SECTORS = {"XLK", "XLY", "XLC", "XLE"}

# Inverse ETFs für Bear Scanner
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


def _detect_wyckoff_distribution(closes, highs, lows, volumes):
    """
    Wyckoff Distribution Schematic Detection für SPY.
    Erkennt die 5 Phasen: A (PSY/BC/AR), B (Range), C (UTAD), D (LPSY), E (Markdown)
    """
    if len(closes) < 60:
        return {"phase": None, "confidence": 0, "detail": "Zu wenig Daten"}

    n = min(120, len(closes))
    c = closes[-n:]
    h = highs[-n:]
    l = lows[-n:]
    v = volumes[-n:]
    current = c[-1]

    bc_idx = h.index(max(h))
    bc_price = max(h)

    post_bc_lows = l[bc_idx:]
    if len(post_bc_lows) < 5:
        return {"phase": "Kein Pattern", "confidence": 0, "detail": "BC zu kürzlich"}
    ar_idx_rel = post_bc_lows.index(min(post_bc_lows[:min(30, len(post_bc_lows))]))
    ar_idx = bc_idx + ar_idx_rel
    ar_price = l[ar_idx]

    range_high = bc_price
    range_low = ar_price
    range_size = range_high - range_low
    if range_size <= 0:
        return {"phase": "Kein Pattern", "confidence": 0, "detail": "Keine Trading Range"}
    range_pct = (range_size / range_high) * 100

    signals = []
    confidence = 0

    # PSY
    pre_bc = c[max(0, bc_idx-20):bc_idx]
    if len(pre_bc) >= 5:
        if min(pre_bc) / max(pre_bc) - 1 < -0.02:
            signals.append("PSY: Preliminary Supply erkannt")
            confidence += 10

    # BC Volume
    if bc_idx < len(v):
        if v[bc_idx] > sum(v)/len(v) * 1.3:
            signals.append(f"BC: Buying Climax bei ${bc_price:.0f} (hohes Vol)")
            confidence += 15
        else:
            signals.append(f"BC: Hoch bei ${bc_price:.0f}")
            confidence += 8

    # AR
    bc_to_ar_bars = ar_idx - bc_idx
    bc_to_ar_pct = ((ar_price - bc_price) / bc_price) * 100
    if bc_to_ar_bars <= 15 and bc_to_ar_pct < -3:
        signals.append(f"AR: Automatic Reaction {bc_to_ar_pct:.1f}% in {bc_to_ar_bars} Tagen")
        confidence += 12

    # ST
    post_ar = list(range(ar_idx + 1, len(c)))
    st_count = sum(1 for i in post_ar if h[i] >= bc_price * 0.98 and v[i] < v[bc_idx] * 0.8)
    if st_count > 0:
        signals.append(f"ST: {st_count}x Secondary Test(s) des BC-Levels")
        confidence += 10

    # UTAD
    utad_detected = False
    for i in post_ar:
        if h[i] > bc_price * 1.005 and i + 5 < len(c):
            if min(c[i+1:i+6]) < bc_price * 0.98:
                signals.append(f"UTAD: Fake-Breakout über ${bc_price:.0f}, dann Reversal")
                confidence += 20
                utad_detected = True
                break

    # SOW
    sow_detected = False
    for i in post_ar:
        if c[i] < ar_price:
            signals.append(f"SOW: Sign of Weakness — Preis unter AR-Support ${ar_price:.0f}")
            confidence += 15
            sow_detected = True
            break

    # LPSY
    lpsy_count = 0
    if len(c) >= 30:
        recent = c[-30:]
        for i in range(2, len(recent)-2):
            if recent[i] > recent[i-1] and recent[i] > recent[i-2] and recent[i] > recent[i+1]:
                if recent[i] < bc_price * 0.97:
                    lpsy_count += 1
        if lpsy_count >= 2:
            signals.append(f"LPSY: {lpsy_count} schwache Rallyes unter BC-Level")
            confidence += 12

    # Volume declining
    if len(v) >= 40:
        if sum(v[-40:-20])/20 > sum(v[-20:])/20 * 1.15:
            signals.append("Volumen nimmt ab — typisch für Distribution")
            confidence += 8

    # Phase bestimmen
    price_in_range = (current - range_low) / max(0.01, range_size)
    phase = "Unbestimmt"
    detail = ""

    if confidence < 20:
        phase = "Kein Wyckoff"
        detail = "Zu wenige Wyckoff-Signale"
    elif not sow_detected and not utad_detected and price_in_range > 0.5:
        phase = "Phase B"
        detail = f"Trading Range ${range_low:.0f}-${range_high:.0f} — Distribution läuft"
    elif utad_detected and not sow_detected:
        phase = "Phase C"
        detail = "UTAD erkannt — Fake-Breakout, Smart Money verkauft an Retail"
    elif sow_detected and current > range_low:
        phase = "Phase D"
        detail = f"LPSY-Zone — Schwache Rallyes, Preis nahe Support ${range_low:.0f}"
    elif sow_detected and current <= range_low:
        phase = "Phase E"
        detail = f"MARKDOWN — Preis unter Support ${range_low:.0f}, Abverkauf läuft!"
    elif price_in_range > 0.7:
        phase = "Phase A/B"
        detail = "Mögliche Distribution-Range bildet sich"

    return {
        "phase": phase, "confidence": min(100, confidence), "detail": detail,
        "signals": signals, "bc_price": round(bc_price, 2), "ar_price": round(ar_price, 2),
        "range_pct": round(range_pct, 1), "price_position": round(price_in_range * 100, 0),
        "utad": utad_detected, "sow": sow_detected, "lpsy_count": lpsy_count,
    }


@st.cache_data(ttl=300)
def fetch_crash_monitor_data(poly_key):
    """
    🔴 Crash Monitor V2 — Professionelles Frühwarnsystem für Markt-Crashs.

    Fear Score 0-100 basierend auf 12+ Indikatoren:
    1. SPY Drawdown vom 52W-Hoch (0-25 pts)
    2. SPY vs SMA50 (0-12 pts)
    3. SPY vs SMA200 (0-15 pts)
    4. RSI (0-12 pts)
    5. MACD Momentum (0-8 pts)
    6. VIX Level (0-15 pts) — UVXY als Proxy
    7. Konsekutive Verlusttage (0-8 pts)
    8. 5d/20d Momentum (0-10 pts)
    9. Sell-Volume Trend (0-8 pts)
    10. Death/Golden Cross (0-15 pts)
    11. Sektor-Rotation (0-10 pts)
    12. Markt-Breadth A/D Ratio (0-12 pts)
    """
    result = {"spy": {}, "vix": {}, "sectors": [], "breadth": {}, "signals": [], "fear_score": 0}

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=400)

        # ══════════════════════════════════════════
        # 1. SPY DAILY BARS (200+ Tage)
        # ══════════════════════════════════════════
        url = f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=15)
        signals = []
        fear = 0

        if resp.status_code == 200:
            bars = resp.json().get("results", [])
            if bars and len(bars) >= 50:
                closes = [b["c"] for b in bars]
                volumes = [b["v"] for b in bars]
                highs = [b["h"] for b in bars]
                lows = [b["l"] for b in bars]
                current = closes[-1]
                prev_close = closes[-2] if len(closes) >= 2 else current

                # ── SMAs ──
                sma20 = sum(closes[-20:]) / 20
                sma50 = sum(closes[-50:]) / 50
                sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None  # None = nicht genug Daten

                # ── RSI (14 Tage, Wilder's Smoothing — korrekte iterative Methode) ──
                # Phase 1: Initiale avg_gain/avg_loss mit SMA über erste 14 Perioden
                rsi_period = 14
                rsi_data = closes[-min(len(closes), 100):]  # Letzte 100 Bars für Einlaufphase
                if len(rsi_data) >= rsi_period + 1:
                    initial_gains = []
                    initial_losses = []
                    for i in range(1, rsi_period + 1):
                        diff = rsi_data[i] - rsi_data[i - 1]
                        initial_gains.append(max(0, diff))
                        initial_losses.append(max(0, -diff))
                    avg_gain = sum(initial_gains) / rsi_period
                    avg_loss = sum(initial_losses) / rsi_period
                    # Phase 2: Wilder's iteratives Smoothing über restliche Bars
                    for i in range(rsi_period + 1, len(rsi_data)):
                        diff = rsi_data[i] - rsi_data[i - 1]
                        avg_gain = (avg_gain * (rsi_period - 1) + max(0, diff)) / rsi_period
                        avg_loss = (avg_loss * (rsi_period - 1) + max(0, -diff)) / rsi_period
                    rs = avg_gain / max(0.001, avg_loss)
                    rsi = 100 - (100 / (1 + rs))
                else:
                    rsi = 50  # Fallback: nicht genug Daten

                # ── MACD (12/26 EMA Differenz) ──
                def _ema(data, period):
                    if len(data) < period:
                        return sum(data) / len(data)
                    mult = 2 / (period + 1)
                    ema_val = sum(data[:period]) / period
                    for p in data[period:]:
                        ema_val = (p - ema_val) * mult + ema_val
                    return ema_val
                # MACD: Berechne MACD-Linie für die letzten 35 Bars
                # um eine saubere 9-EMA (Signal-Linie) daraus zu bilden
                macd_series = []
                for j in range(35, 0, -1):
                    # Für jeden Bar: EMA12 und EMA26 bis zu diesem Punkt
                    slice_end = len(closes) - j + 1
                    if slice_end < 26:
                        continue
                    e12 = _ema(closes[:slice_end], 12)
                    e26 = _ema(closes[:slice_end], 26)
                    macd_series.append(e12 - e26)
                macd = macd_series[-1] if macd_series else 0
                macd_signal = _ema(macd_series, 9) if len(macd_series) >= 9 else macd
                macd_hist = macd - macd_signal

                # ── Drawdown vom 52W-Hoch ──
                high_252 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
                drawdown = ((current - high_252) / high_252) * 100

                # ── Death Cross / Golden Cross ──
                cross_signal = ""
                if len(closes) >= 201:
                    sma50_prev = sum(closes[-51:-1]) / 50
                    sma200_prev = sum(closes[-201:-1]) / 200
                    if sma50_prev > sma200_prev and sma50 < sma200:
                        cross_signal = "💀 DEATH CROSS (frisch!)"
                    elif sma50_prev < sma200_prev and sma50 > sma200:
                        cross_signal = "✨ GOLDEN CROSS (frisch!)"
                    elif sma50 < sma200:
                        cross_signal = "💀 Death Cross aktiv"
                    else:
                        cross_signal = "📈 Über Golden Cross"
                    # SMA50 nähert sich SMA200? Warnsignal
                    sma_gap_pct = ((sma50 - sma200) / sma200) * 100
                else:
                    sma_gap_pct = 5  # default

                # ── Volumen-Analyse ──
                vol_avg20 = sum(volumes[-20:]) / 20
                vol_today = volumes[-1]
                vol_ratio = vol_today / max(1, vol_avg20)

                # Sell-Volume Trend: Down-Days Volumen vs Up-Days Volumen (letzte 20 Tage)
                up_vol, down_vol = 0, 0
                for i in range(-20, 0):
                    if closes[i] < closes[i - 1]:
                        down_vol += volumes[i]
                    else:
                        up_vol += volumes[i]
                sell_pressure = down_vol / max(1, up_vol + down_vol)  # >0.5 = mehr Sell-Volume

                # ── Konsekutive Verlusttage ──
                consec_down = 0
                for i in range(len(closes) - 1, 0, -1):
                    if closes[i] < closes[i - 1]:
                        consec_down += 1
                    else:
                        break

                # ── Momentum (5d / 20d / 50d Performance) ──
                chg_5d = ((closes[-1] - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0
                chg_20d = ((closes[-1] - closes[-21]) / closes[-21]) * 100 if len(closes) >= 21 else 0
                chg_50d = ((closes[-1] - closes[-51]) / closes[-51]) * 100 if len(closes) >= 51 else 0

                # ── Preis-Position im 20-Tage-Range ──
                high_20 = max(highs[-20:])
                low_20 = min(lows[-20:])
                range_pos = (current - low_20) / max(0.01, high_20 - low_20)  # 0 = am Tief, 1 = am Hoch

                result["spy"] = {
                    "price": round(current, 2), "prev_close": round(prev_close, 2),
                    "change_pct": round((current - prev_close) / prev_close * 100, 2),
                    "sma20": round(sma20, 2), "sma50": round(sma50, 2), "sma200": round(sma200, 2) if sma200 is not None else None,
                    "rsi": round(rsi, 1), "macd": round(macd, 2), "macd_signal": round(macd_signal, 2),
                    "macd_hist": round(macd_hist, 2),
                    "drawdown": round(drawdown, 1), "high_52w": round(high_252, 2),
                    "cross_signal": cross_signal, "vol_ratio": round(vol_ratio, 2),
                    "sell_pressure": round(sell_pressure * 100, 1),
                    "consec_down": consec_down,
                    "chg_5d": round(chg_5d, 2), "chg_20d": round(chg_20d, 2), "chg_50d": round(chg_50d, 2),
                    "range_pos": round(range_pos, 2), "sma_gap_pct": round(sma_gap_pct, 2) if len(closes) >= 201 else None,
                }

                # ── Wyckoff Distribution Detection ──
                result.get("spy", {})["wyckoff"] = _detect_wyckoff_distribution(closes, highs, lows, volumes)

                # ══════════════════════════════════════════
                # FEAR SCORE BERECHNUNG (12 Indikatoren)
                # ══════════════════════════════════════════

                # 1. DRAWDOWN vom 52W-Hoch (max 25 pts)
                if drawdown <= -20:
                    signals.append(("🔴", "BÄRENMARKT", f"SPY {drawdown:.1f}% vom Hoch — offizieller Bärenmarkt"))
                    fear += 25
                elif drawdown <= -10:
                    signals.append(("🔴", "KORREKTUR", f"SPY {drawdown:.1f}% vom Hoch — Korrektur-Territorium"))
                    fear += 18
                elif drawdown <= -5:
                    signals.append(("🟠", "PULLBACK", f"SPY {drawdown:.1f}% vom Hoch — Pullback-Zone"))
                    fear += 12
                elif drawdown <= -3:
                    signals.append(("🟡", "SCHWÄCHE", f"SPY {drawdown:.1f}% vom 52W-Hoch"))
                    fear += 7
                elif drawdown <= -1:
                    fear += 3

                # 2. SPY vs SMA50 (max 12 pts)
                if current < sma50:
                    gap_50 = ((current - sma50) / sma50) * 100
                    if gap_50 <= -5:
                        signals.append(("🔴", "WEIT UNTER SMA50", f"SPY ${current:.0f} | SMA50 ${sma50:.0f} ({gap_50:.1f}%)"))
                        fear += 12
                    elif gap_50 <= -2:
                        signals.append(("🟠", "UNTER SMA50", f"SPY ${current:.0f} < SMA50 ${sma50:.0f} ({gap_50:.1f}%)"))
                        fear += 8
                    else:
                        signals.append(("🟡", "KNAPP UNTER SMA50", f"SPY ${current:.0f} ≈ SMA50 ${sma50:.0f}"))
                        fear += 5

                # 3. SPY vs SMA200 (max 15 pts) — nur wenn genug Daten vorhanden
                if sma200 is not None and current < sma200:
                    gap_200 = ((current - sma200) / sma200) * 100
                    if gap_200 <= -10:
                        signals.append(("🔴", "TIEF UNTER SMA200", f"SPY ${current:.0f} | SMA200 ${sma200:.0f} ({gap_200:.1f}%)"))
                        fear += 15
                    elif gap_200 <= -3:
                        signals.append(("🔴", "UNTER SMA200", f"SPY ${current:.0f} < SMA200 ${sma200:.0f}"))
                        fear += 12
                    else:
                        signals.append(("🟠", "KNAPP UNTER SMA200", f"SPY nahe SMA200 ${sma200:.0f}"))
                        fear += 8
                elif sma200 is not None:
                    # Über SMA200 aber knapp?
                    pct_above = ((current - sma200) / sma200) * 100
                    if pct_above < 2:
                        signals.append(("🟡", "SMA200 NAHE", f"SPY nur {pct_above:.1f}% über SMA200 — Unterstützung wackelt"))
                        fear += 3

                # 4. RSI (max 12 pts)
                if rsi <= 20:
                    signals.append(("🔴", "RSI PANIK", f"RSI {rsi:.0f} — extremes Panik-Level"))
                    fear += 12
                elif rsi <= 30:
                    signals.append(("🔴", "RSI ÜBERVERKAUFT", f"RSI {rsi:.0f} — starker Verkaufsdruck"))
                    fear += 10
                elif rsi <= 40:
                    signals.append(("🟠", "RSI SCHWACH", f"RSI {rsi:.0f} — bärisches Momentum"))
                    fear += 6
                elif rsi <= 45:
                    signals.append(("🟡", "RSI NEUTRAL-SCHWACH", f"RSI {rsi:.0f}"))
                    fear += 3

                # 5. MACD (max 8 pts)
                if macd < 0:
                    fear += 3
                    if macd_hist < 0:
                        signals.append(("🟠", "MACD BÄRISCH", f"MACD {macd:.2f} unter Signal-Linie — Abwärtstrend"))
                        fear += 5
                    else:
                        fear += 2

                # 6. Konsekutive Verlusttage (max 8 pts)
                if consec_down >= 5:
                    signals.append(("🔴", f"{consec_down} VERLUSTTAGE", f"{consec_down} Tage in Folge Verlust — starker Abwärtstrend"))
                    fear += 8
                elif consec_down >= 3:
                    signals.append(("🟠", f"{consec_down} VERLUSTTAGE", f"{consec_down} aufeinanderfolgende Verlusttage"))
                    fear += 5
                elif consec_down >= 2:
                    fear += 2

                # 7. Momentum 5d/20d (max 10 pts)
                if chg_5d <= -5:
                    signals.append(("🔴", "5D CRASH", f"SPY {chg_5d:+.1f}% in 5 Tagen — Sell-Off"))
                    fear += 6
                elif chg_5d <= -2:
                    signals.append(("🟠", "5D SCHWACH", f"SPY {chg_5d:+.1f}% in 5 Tagen"))
                    fear += 3
                elif chg_5d < 0:
                    fear += 1

                if chg_20d <= -5:
                    signals.append(("🔴", "20D ABWÄRTSTREND", f"SPY {chg_20d:+.1f}% in 20 Tagen"))
                    fear += 4
                elif chg_20d <= -2:
                    fear += 2
                elif chg_20d < 0:
                    fear += 1

                # 8. Sell-Volume Druck (max 8 pts)
                if sell_pressure > 65:
                    signals.append(("🔴", "SELL-DRUCK", f"{sell_pressure:.0f}% des Volumens an Down-Tagen (20d) — Distribution"))
                    fear += 8
                elif sell_pressure > 55:
                    signals.append(("🟠", "ERHÖHTER SELL-DRUCK", f"{sell_pressure:.0f}% Sell-Volume (20d)"))
                    fear += 4
                elif sell_pressure > 50:
                    fear += 2

                # 9. Death Cross / SMA Konvergenz (max 15 pts)
                if "DEATH CROSS (frisch" in cross_signal:
                    signals.append(("🔴", "DEATH CROSS", "SMA50 kreuzt SMA200 nach unten — historisch starkes Bärensignal"))
                    fear += 15
                elif "Death Cross aktiv" in cross_signal:
                    signals.append(("🔴", "DEATH CROSS AKTIV", "SMA50 unter SMA200 — Bärenmarkt-Regime"))
                    fear += 12
                elif sma_gap_pct is not None and 0 < sma_gap_pct < 1.5:
                    signals.append(("🟡", "SMA KONVERGENZ", f"SMA50 nur {sma_gap_pct:.1f}% über SMA200 — Death Cross droht"))
                    fear += 5

                # 10. Hohes Volumen bei Sell-Off (max 5 pts)
                if vol_ratio > 2.0 and (current - prev_close) < 0:
                    signals.append(("🔴", "PANIK-VOLUMEN", f"RVOL {vol_ratio:.1f}x bei Verlust — institutioneller Sell-Off"))
                    fear += 5
                elif vol_ratio > 1.5 and (current - prev_close) < 0:
                    fear += 2

                # 11. Range Position (Preis am 20d-Tief = mehr Angst)
                if range_pos < 0.15:
                    signals.append(("🔴", "AM 20D-TIEF", f"SPY nahe dem 20-Tage-Tief — keine Käufer"))
                    fear += 5
                elif range_pos < 0.3:
                    fear += 2

        # ══════════════════════════════════════════
        # 2. VIX via UVXY (2x VIX Futures ETF)
        # ══════════════════════════════════════════
        # UVXY reagiert stärker als VIXY — besserer Angstmesser
        # Zusätzlich holen wir VIXY für Vergleich
        for vix_etf in ["UVXY", "VIXY"]:
            vix_url = f"https://api.polygon.io/v2/aggs/ticker/{vix_etf}/range/1/day/{(end_date - timedelta(days=60)).strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            vix_resp = rate_limited_get(vix_url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)
            if vix_resp.status_code == 200:
                vix_bars = vix_resp.json().get("results", [])
                if vix_bars and len(vix_bars) >= 10:
                    vix_current = vix_bars[-1]["c"]
                    vix_prev = vix_bars[-2]["c"]
                    vix_avg20 = sum(b["c"] for b in vix_bars[-20:]) / min(20, len(vix_bars[-20:]))
                    vix_avg5 = sum(b["c"] for b in vix_bars[-5:]) / min(5, len(vix_bars[-5:]))
                    vix_change = ((vix_current - vix_prev) / max(0.01, vix_prev)) * 100
                    vix_spike = vix_current / max(0.01, vix_avg20)
                    # 5d Trend: steigendes VIX = zunehmende Angst
                    vix_5d_chg = ((vix_avg5 - vix_avg20) / max(0.01, vix_avg20)) * 100

                    if vix_etf == "UVXY":
                        result["vix"] = {
                            "ticker": vix_etf,
                            "price": round(vix_current, 2),
                            "change_pct": round(vix_change, 1),
                            "avg20": round(vix_avg20, 2),
                            "spike_ratio": round(vix_spike, 2),
                            "trend_5d": round(vix_5d_chg, 1),
                        }

                        # VIX Fear Scoring (max 15 pts)
                        if vix_spike > 2.0:
                            signals.append(("🔴", "VIX EXTREM", f"{vix_etf} {vix_spike:.1f}x über 20d-Schnitt — Panik-Modus"))
                            fear += 15
                        elif vix_spike > 1.5:
                            signals.append(("🔴", "VIX SPIKE", f"{vix_etf} {vix_spike:.1f}x über Durchschnitt — starke Angst"))
                            fear += 12
                        elif vix_spike > 1.2:
                            signals.append(("🟠", "VIX ERHÖHT", f"{vix_etf} {vix_spike:.1f}x über Durchschnitt — erhöhte Nervosität"))
                            fear += 7
                        elif vix_spike > 1.05:
                            fear += 3

                        # Trend: VIX steigt über mehrere Tage = zunehmende Angst
                        if vix_5d_chg > 20:
                            signals.append(("🔴", "VIX TREND ↑", f"{vix_etf} steigt stark: 5d-Schnitt {vix_5d_chg:+.0f}% über 20d"))
                            fear += 5
                        elif vix_5d_chg > 10:
                            fear += 3
                    break  # Nur den ersten verfügbaren ETF nutzen

        # ══════════════════════════════════════════
        # 3. SEKTOR + SAFE-HAVEN + CREDIT — PARALLEL FETCH
        # ══════════════════════════════════════════
        # Alle ETFs in einem Batch parallel laden statt sequentiell (16 Calls → parallel)
        ALL_MONITOR_ETFS = {}
        for _etf, (_name, _emoji) in SECTOR_ETFS.items():
            ALL_MONITOR_ETFS[_etf] = {"name": _name, "emoji": _emoji, "category": "sector"}
        for _etf, (_name, _emoji) in [("TLT", ("US Treasury 20Y+", "🏦")), ("GLD", ("Gold", "🥇")), ("UUP", ("US Dollar", "💵"))]:
            ALL_MONITOR_ETFS[_etf] = {"name": _name, "emoji": _emoji, "category": "safe_haven"}
        ALL_MONITOR_ETFS["HYG"] = {"name": "High Yield Bonds", "emoji": "💳", "category": "credit"}
        ALL_MONITOR_ETFS["LQD"] = {"name": "Inv. Grade Bonds", "emoji": "💳", "category": "credit"}

        _etf_start = (end_date - timedelta(days=40)).strftime('%Y-%m-%d')
        _etf_end = end_date.strftime('%Y-%m-%d')

        def _fetch_etf_bars(etf_ticker):
            """Holt Tages-Bars für einen ETF."""
            try:
                _url = f"https://api.polygon.io/v2/aggs/ticker/{etf_ticker}/range/1/day/{_etf_start}/{_etf_end}"
                _resp = rate_limited_get(_url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=8)
                if _resp.status_code == 200:
                    return etf_ticker, _resp.json().get("results", [])
            except Exception:
                pass
            return etf_ticker, []

        # Parallel fetch mit ThreadPoolExecutor (5 parallel statt 1 sequentiell)
        from concurrent.futures import ThreadPoolExecutor
        _all_bars = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_etf_bars, etf): etf for etf in ALL_MONITOR_ETFS}
            for future in futures:
                try:
                    etf_ticker, bars = future.result(timeout=15)
                    _all_bars[etf_ticker] = bars
                except Exception:
                    continue

        # ── Sektoren aus Batch-Ergebnissen extrahieren ──
        sector_data = []
        for etf, meta in ALL_MONITOR_ETFS.items():
            if meta["category"] != "sector":
                continue
            bars = _all_bars.get(etf, [])
            if bars and len(bars) >= 5:
                s_current = bars[-1]["c"]
                s_5d = bars[-6]["c"] if len(bars) >= 6 else bars[0]["c"]
                s_20d = bars[-21]["c"] if len(bars) >= 21 else bars[0]["c"]
                chg_1d = ((s_current - bars[-2]["c"]) / bars[-2]["c"]) * 100
                chg_5d = ((s_current - s_5d) / s_5d) * 100
                chg_20d = ((s_current - s_20d) / s_20d) * 100
                sector_data.append({
                    "etf": etf, "name": meta["name"], "emoji": meta["emoji"],
                    "price": round(s_current, 2),
                    "chg_1d": round(chg_1d, 2), "chg_5d": round(chg_5d, 2),
                    "chg_20d": round(chg_20d, 2),
                    "is_defensive": etf in DEFENSIVE_SECTORS,
                    "is_risk_on": etf in RISK_ON_SECTORS,
                })
        result["sectors"] = sorted(sector_data, key=lambda x: x["chg_5d"])

        # Rotation-Signal (max 10 pts)
        if sector_data:
            def_avg = sum(s["chg_5d"] for s in sector_data if s["is_defensive"]) / max(1, sum(1 for s in sector_data if s["is_defensive"]))
            risk_avg = sum(s["chg_5d"] for s in sector_data if s["is_risk_on"]) / max(1, sum(1 for s in sector_data if s["is_risk_on"]))
            rotation_gap = def_avg - risk_avg
            # Wie viele Sektoren sind negativ auf 5d?
            neg_sectors = sum(1 for s in sector_data if s["chg_5d"] < 0)
            neg_pct = neg_sectors / max(1, len(sector_data)) * 100

            result.get("breadth", {})["rotation_gap"] = round(rotation_gap, 2)
            result.get("breadth", {})["defensive_avg"] = round(def_avg, 2)
            result.get("breadth", {})["risk_on_avg"] = round(risk_avg, 2)
            result.get("breadth", {})["neg_sectors"] = neg_sectors
            result.get("breadth", {})["total_sectors"] = len(sector_data)

            if rotation_gap > 3:
                signals.append(("🔴", "RISK-OFF ROTATION", f"Defensive {def_avg:+.1f}% vs Risk-On {risk_avg:+.1f}% (5d) — Flight to Safety"))
                fear += 10
            elif rotation_gap > 1.5:
                signals.append(("🟡", "LEICHTE ROTATION", f"Defensive outperformen Risk-On um {rotation_gap:.1f}%"))
                fear += 4

            if neg_pct >= 80:
                signals.append(("🔴", "BREITE SEKTORSCHWÄCHE", f"{neg_sectors}/{len(sector_data)} Sektoren negativ (5d)"))
                fear += 5
            elif neg_pct >= 60:
                fear += 2

        # ══════════════════════════════════════════
        # 4. MARKT-BREADTH (Snapshot)
        # ══════════════════════════════════════════
        try:
            snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            snap_resp = rate_limited_get(snap_url, params={"apiKey": poly_key}, timeout=30)
            if snap_resp.status_code == 200:
                snap_tickers = snap_resp.json().get("tickers", [])
                total = 0
                advancing = 0
                declining = 0
                heavy_losers = 0  # >5% Verlust
                extreme_losers = 0  # >10% Verlust
                for t in snap_tickers:
                    td = t.get("todaysChangePerc", 0)
                    if td is None:
                        continue
                    total += 1
                    if td > 0:
                        advancing += 1
                    elif td < 0:
                        declining += 1
                    if td <= -5:
                        heavy_losers += 1
                    if td <= -10:
                        extreme_losers += 1

                ad_ratio = advancing / max(1, declining)
                pct_declining = (declining / max(1, total)) * 100

                result.get("breadth", {})["total"] = total
                result.get("breadth", {})["advancing"] = advancing
                result.get("breadth", {})["declining"] = declining
                result.get("breadth", {})["ad_ratio"] = round(ad_ratio, 2)
                result.get("breadth", {})["pct_declining"] = round(pct_declining, 1)
                result.get("breadth", {})["heavy_losers"] = heavy_losers
                result.get("breadth", {})["extreme_losers"] = extreme_losers

                # Breadth Scoring (max 12 pts) — nur bei genug Daten (>100 Tickers)
                if total < 100:
                    pass  # Zu wenig Daten für aussagekräftige Breadth → kein Fear-Score Beitrag
                elif ad_ratio < 0.4:
                    signals.append(("🔴", "BREADTH KOLLAPS", f"A/D Ratio {ad_ratio:.2f} — Massiver Ausverkauf"))
                    fear += 12
                elif ad_ratio < 0.6:
                    signals.append(("🔴", "BREADTH SCHWACH", f"A/D Ratio {ad_ratio:.2f} — deutlich mehr Verlierer"))
                    fear += 8
                elif ad_ratio < 0.8:
                    signals.append(("🟠", "BREADTH NEGATIV", f"A/D Ratio {ad_ratio:.2f}"))
                    fear += 4
                elif ad_ratio < 1.0:
                    fear += 2

                if extreme_losers > 50:
                    signals.append(("🔴", "CRASH-SELLING", f"{extreme_losers} Aktien mit >10% Verlust heute"))
                    fear += 5
        except Exception:
            pass

        # ══════════════════════════════════════════
        # 5. SAFE-HAVEN TRACKER (TLT, GLD, UUP) — aus Batch-Daten
        # ══════════════════════════════════════════
        safe_havens = {}
        for sh_etf in ["TLT", "GLD", "UUP"]:
            meta = ALL_MONITOR_ETFS.get(sh_etf, {})
            bars = _all_bars.get(sh_etf, [])
            if bars and len(bars) >= 5:
                sh_cur = bars[-1]["c"]
                sh_prev = bars[-2]["c"]
                sh_5d = bars[-6]["c"] if len(bars) >= 6 else bars[0]["c"]
                sh_20d = bars[-21]["c"] if len(bars) >= 21 else bars[0]["c"]
                safe_havens[sh_etf] = {
                    "name": meta.get("name", sh_etf), "emoji": meta.get("emoji", ""),
                    "price": round(sh_cur, 2),
                    "chg_1d": round(((sh_cur - sh_prev) / sh_prev) * 100, 2),
                    "chg_5d": round(((sh_cur - sh_5d) / sh_5d) * 100, 2),
                    "chg_20d": round(((sh_cur - sh_20d) / sh_20d) * 100, 2),
                }
        result["safe_havens"] = safe_havens

        # Flight-to-Safety Signal: SPY fällt + Safe Havens steigen (max 10 pts)
        spy_5d = result.get("spy", {}).get("chg_5d", 0)
        fts_count = 0
        for _sh_etf, _sh_data in safe_havens.items():
            if _sh_data["chg_5d"] > 0 and spy_5d < -1:
                fts_count += 1
        if fts_count >= 3 and spy_5d < -2:
            signals.append(("🔴", "FLIGHT TO SAFETY", f"TLT+GLD+UUP steigen bei SPY {spy_5d:+.1f}% — Institutionelle flüchten"))
            fear += 10
        elif fts_count >= 2 and spy_5d < -1:
            signals.append(("🟠", "SAFE-HAVEN FLOWS", f"{fts_count}/3 Safe Havens steigen bei SPY-Schwäche"))
            fear += 5
        elif fts_count >= 1 and spy_5d < -2:
            fear += 2

        # Korrelationsanomalie: ALLES fällt (SPY + TLT + GLD) = Liquiditätskrise
        if safe_havens:
            all_falling = all(d["chg_5d"] < -1 for d in safe_havens.values()) and spy_5d < -2
            if all_falling:
                signals.append(("🔴", "LIQUIDITÄTSKRISE", "SPY + Bonds + Gold + Dollar ALLE negativ — Cash is King"))
                fear += 12

        # ══════════════════════════════════════════
        # 6. CREDIT STRESS (HYG vs LQD Spread) — aus Batch-Daten
        # ══════════════════════════════════════════
        credit_data = {}
        for cr_etf in ["HYG", "LQD"]:
            bars = _all_bars.get(cr_etf, [])
            if bars and len(bars) >= 5:
                cr_cur = bars[-1]["c"]
                cr_5d = bars[-6]["c"] if len(bars) >= 6 else bars[0]["c"]
                credit_data[cr_etf] = {
                    "price": round(cr_cur, 2),
                    "chg_5d": round(((cr_cur - cr_5d) / cr_5d) * 100, 2),
                }

        if "HYG" in credit_data and "LQD" in credit_data:
            # Credit Spread Widening: HYG fällt stärker als LQD = Stress
            hyg_5d = credit_data["HYG"]["chg_5d"]
            lqd_5d = credit_data["LQD"]["chg_5d"]
            credit_spread_chg = lqd_5d - hyg_5d  # Positiv = Spread weitet sich (Stress)
            result["credit"] = {
                "hyg_5d": hyg_5d, "lqd_5d": lqd_5d,
                "spread_change": round(credit_spread_chg, 2),
            }
            if credit_spread_chg > 2.0:
                signals.append(("🔴", "CREDIT STRESS", f"HYG {hyg_5d:+.1f}% vs LQD {lqd_5d:+.1f}% — Credit Spreads weiten sich stark"))
                fear += 8
            elif credit_spread_chg > 1.0:
                signals.append(("🟠", "CREDIT WARNUNG", f"HYG underperformt LQD um {credit_spread_chg:.1f}% (5d)"))
                fear += 4
            elif credit_spread_chg > 0.5:
                fear += 2

        # ══════════════════════════════════════════
        # 7. SPY SUPPORT/RESISTANCE LEVELS
        # ══════════════════════════════════════════
        spy_data = result.get("spy", {})
        if spy_data:
            _s_price = spy_data.get("price", 0)
            _s_sma50 = spy_data.get("sma50", 0)
            _s_sma200 = spy_data.get("sma200")
            _s_high52 = spy_data.get("high_52w", 0)

            # Pivot-basierte Support/Resistance + SMA-Levels
            levels = []
            levels.append({"level": _s_high52, "type": "resistance", "label": "52W Hoch"})
            levels.append({"level": _s_sma50, "type": "support" if _s_price > _s_sma50 else "resistance", "label": "SMA 50"})
            if _s_sma200:
                levels.append({"level": _s_sma200, "type": "support" if _s_price > _s_sma200 else "resistance", "label": "SMA 200"})

            # Runde psychologische Levels (z.B. $650, $660, $670...)
            base = int(_s_price / 10) * 10
            for lvl in [base - 20, base - 10, base, base + 10, base + 20]:
                if lvl > 0:
                    levels.append({
                        "level": lvl,
                        "type": "support" if lvl < _s_price else "resistance",
                        "label": f"${lvl} (psycho.)"
                    })

            # Sortiere nach Entfernung zum aktuellen Preis
            levels = sorted(levels, key=lambda x: abs(x["level"] - _s_price))

            # Nächster Support und Resistance
            supports = [l for l in levels if l["type"] == "support" and l["level"] < _s_price]
            resistances = [l for l in levels if l["type"] == "resistance" and l["level"] > _s_price]

            result.get("spy", {})["next_support"] = supports[0] if supports else None
            result.get("spy", {})["next_resistance"] = resistances[0] if resistances else None
            result.get("spy", {})["key_levels"] = levels[:8]  # Top 8 nächste Levels

        # ══════════════════════════════════════════
        # FEAR SCORE TREND (Vergleich zum letzten Check)
        # ══════════════════════════════════════════
        import json as _json_fear
        _fear_history_file = "/tmp/crash_fear_history.json"
        try:
            with open(_fear_history_file, "r") as _fhf:
                _fear_history = _json_fear.load(_fhf)
        except Exception:
            _fear_history = []

        # Aktuellen Score speichern
        _now_ts = end_date.strftime("%Y-%m-%d %H:%M")
        _fear_history.append({"ts": _now_ts, "score": min(100, fear)})
        _fear_history = _fear_history[-50:]  # Letzte 50 Checks behalten
        try:
            with open(_fear_history_file, "w") as _fhf:
                _json_fear.dump(_fear_history, _fhf)
        except Exception:
            pass

        # Trend berechnen
        _prev_fear = _fear_history[-2]["score"] if len(_fear_history) >= 2 else None
        _fear_trend = None
        if _prev_fear is not None:
            _fear_delta = min(100, fear) - _prev_fear
            if _fear_delta > 10:
                _fear_trend = f"↑ +{_fear_delta} (stark steigend)"
            elif _fear_delta > 3:
                _fear_trend = f"↑ +{_fear_delta} (steigend)"
            elif _fear_delta < -10:
                _fear_trend = f"↓ {_fear_delta} (stark fallend)"
            elif _fear_delta < -3:
                _fear_trend = f"↓ {_fear_delta} (fallend)"
            else:
                _fear_trend = f"→ {_fear_delta:+d} (stabil)"
        result["fear_trend"] = _fear_trend
        result["fear_history"] = _fear_history[-10:]  # Letzte 10 für UI-Chart

        result["signals"] = signals
        result["fear_score"] = min(100, fear)

    except Exception as e:
        result["error"] = str(e)

    return result


@st.cache_data(ttl=300)
def fetch_bear_scanner_data(poly_key):
    """
    🐻 Bear Scanner — Findet Short-Opportunitäten und Inverse-ETF-Chancen.
    """
    result = {"inverse_etfs": [], "short_candidates": [], "breakdown_stocks": []}

    try:
        end_date = datetime.now()

        # ── 1. Inverse ETFs Performance ──
        for etf, (desc, underlying) in INVERSE_ETFS.items():
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{etf}/range/1/day/{(end_date - timedelta(days=40)).strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
                resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=8)
                if resp.status_code == 200:
                    bars = resp.json().get("results", [])
                    if bars and len(bars) >= 5:
                        current = bars[-1]["c"]
                        prev = bars[-2]["c"]
                        p5d = bars[-6]["c"] if len(bars) >= 6 else bars[0]["c"]
                        p20d = bars[-21]["c"] if len(bars) >= 21 else bars[0]["c"]
                        vol = bars[-1].get("v", 0)
                        vol_avg = sum(b.get("v", 0) for b in bars[-20:]) / 20

                        chg_1d = ((current - prev) / prev) * 100
                        chg_5d = ((current - p5d) / p5d) * 100
                        chg_20d = ((current - p20d) / p20d) * 100

                        # Momentum-Signal: Inverse ETF steigt = Markt fällt
                        momentum = ""
                        if chg_5d > 10:
                            momentum = "🔥 STARK"
                        elif chg_5d > 5:
                            momentum = "📈 Steigend"
                        elif chg_5d > 0:
                            momentum = "↗️ Leicht"
                        else:
                            momentum = "↘️ Fallend"

                        result["inverse_etfs"].append({
                            "Ticker": etf, "Name": desc, "Underlying": underlying,
                            "Preis": round(current, 2),
                            "1d%": round(chg_1d, 2), "5d%": round(chg_5d, 2),
                            "20d%": round(chg_20d, 2),
                            "Vol": vol, "RVOL": round(vol / max(1, vol_avg), 2),
                            "Momentum": momentum,
                        })
            except Exception:
                continue
        result["inverse_etfs"] = sorted(result["inverse_etfs"], key=lambda x: x["5d%"], reverse=True)

        # ── 2. Short-Kandidaten: Überkaufte Aktien mit Erschöpfungssignalen ──
        try:
            snap_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            snap_resp = rate_limited_get(snap_url, params={"apiKey": poly_key}, timeout=30)
            if snap_resp.status_code == 200:
                snap_tickers = snap_resp.json().get("tickers", [])

                # CS-Whitelist laden (gleicher Datei-Cache wie BI/ORB Scanner)
                _bear_cs = COMMON_STOCK_TICKERS
                if not _bear_cs:
                    _CS_FILE_BEAR = "/tmp/cs_tickers_cache.json"
                    try:
                        if os.path.exists(_CS_FILE_BEAR) and (time.time() - os.path.getmtime(_CS_FILE_BEAR)) < 86400:
                            with open(_CS_FILE_BEAR, "r") as _cf:
                                _bear_cs = set(json.load(_cf))
                    except Exception:
                        pass
                if not _bear_cs:
                    try:
                        _bear_cs, _ = _load_common_stock_tickers_direct(poly_key)
                        if _bear_cs:
                            with open("/tmp/cs_tickers_cache.json", "w") as _cf:
                                json.dump(list(_bear_cs), _cf)
                    except Exception:
                        _bear_cs = set()

                # Breakdown-Stocks: Stark fallende Large-Caps (>$5, >$500k DolVol)
                breakdowns = []
                for t in snap_tickers:
                    try:
                        ticker = t.get("ticker", "")
                        day = t.get("day", {}) or {}
                        prev = t.get("prevDay", {}) or {}
                        last = t.get("lastTrade", {}) or {}

                        price = last.get("p", 0) or day.get("c", 0) or 0
                        prev_close = prev.get("c", 0) or 0
                        if price < 5 or prev_close <= 0:
                            continue

                        change_pct = ((price - prev_close) / prev_close) * 100
                        volume = day.get("v", 0) or 0
                        dollar_vol = price * volume

                        if dollar_vol < 500_000:
                            continue
                        # CS-Whitelist: Nur echte Aktien (keine ETFs, Fonds, etc.)
                        if _bear_cs and ticker.upper() not in _bear_cs:
                            continue
                        elif not _bear_cs and is_etf_or_etp(ticker):
                            continue

                        # Breakdown: Starker Tagesverlust
                        if change_pct <= -4:
                            breakdowns.append({
                                "Ticker": ticker,
                                "Preis": round(price, 2),
                                "Change%": round(change_pct, 2),
                                "Volume": volume,
                                "DollarVol": round(dollar_vol / 1_000_000, 1),
                            })
                    except Exception:
                        continue

                # Top 30 stärkste Verlierer
                breakdowns = sorted(breakdowns, key=lambda x: x["Change%"])[:30]
                result["breakdown_stocks"] = breakdowns

        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)

    return result


# -----------------------------------------------------------------------------
# HAUPTBEREICH - TABS
# -----------------------------------------------------------------------------
tab_scanner, tab_bi, tab_biotech, tab_divergence, tab_early, tab_newlisting, tab_search, tab_watchlist, tab_moneyflow, tab_calendar, tab_crash, tab_bear, tab_backtest, tab_guide = st.tabs(["📊 Scanner", "🔮 BI Scanner", "🧬 Biotech", "📉 BTC-Divergenz", "🔥 Early Movers", "🆕 New Listing", "🔍 Suche", "⭐ Watchlist", "💰 Money Flow", "📅 Kalender", "🔴 Crash Monitor", "🐻 Bear Scanner", "🧪 Backtest", "📖 Strategie Guide"])

with tab_scanner:
    # PRE-MARKET WATCHLIST ANZEIGE (wenn aktiv)
    if st.session_state.get("show_pm_watchlist", False):
        st.header("🌅 Pre-Market Watchlist V2")
        st.caption("Echte PM Session High/Low | Technical Levels | Risk Management")
        
        # PM Daten laden wenn noch nicht vorhanden
        if st.session_state.get("pm_watchlist_data") is None:
            try:
                poly_key = st.secrets["POLYGON_KEY"]
                with st.spinner("🔍 Lade Pre-Market Movers (echte PM Session Daten)..."):
                    pm_data, spy_change = fetch_premarket_watchlist(
                        poly_key, 
                        min_change=2.0,  # Min 2% Bewegung
                        min_volume=50000,  # Min 50K Vol
                        min_price=1.0,  # Min $1
                        max_price=500.0  # Max $500
                    )
                    st.session_state.pm_watchlist_data = pm_data
                    st.session_state.pm_spy_change = spy_change
            except KeyError:
                st.error("❌ POLYGON_KEY fehlt in Secrets!")
                pm_data = []
                spy_change = 0
            except Exception as e:
                st.error(f"Fehler: {e}")
                import traceback
                st.code(traceback.format_exc())
                pm_data = []
                spy_change = 0
        else:
            pm_data = st.session_state.pm_watchlist_data
            spy_change = st.session_state.get("pm_spy_change", 0)
        
        # Refresh Button
        col_ref1, col_ref2 = st.columns([3, 1])
        with col_ref2:
            if st.button("🔄 Refresh", key="pm_refresh"):
                st.session_state.pm_watchlist_data = None
                st.rerun()
        
        # PM Watchlist anzeigen
        if pm_data:
            display_premarket_watchlist(pm_data, spy_change)
        
        st.divider()
        st.caption("👇 Normaler Scanner weiterhin verfügbar")
    
    # AI CHART ANALYZER ANZEIGE (wenn aktiv)
    if st.session_state.get("show_ai_chart", False) and st.session_state.get("ai_chart_ticker"):
        ticker = st.session_state.ai_chart_ticker
        
        col_chart_header, col_chart_close = st.columns([4, 1])
        with col_chart_header:
            st.header(f"🤖 AI Chart Analyzer")
        with col_chart_close:
            if st.button("❌ Schließen", key="close_ai_chart"):
                st.session_state.show_ai_chart = False
                st.session_state.ai_chart_ticker = None
                st.rerun()
        
        try:
            poly_key = st.secrets["POLYGON_KEY"]
            display_ai_chart_analyzer(ticker, poly_key, timeframe="1H")
        except KeyError:
            st.error("❌ POLYGON_KEY fehlt in Secrets!")
        except Exception as e:
            st.error(f"Chart Fehler: {e}")
            import traceback
            st.code(traceback.format_exc())
        
        st.divider()
        st.caption("👇 Normaler Scanner weiterhin verfügbar")
    
    col_chart, col_journal = st.columns([3, 1])
    
    # Prüfe ob Insider-Strategie aktiv
    is_insider = st.session_state.current_strategy in ["Insider Buying", "Insider Selling"]
    is_volume_void = st.session_state.current_strategy in ["Volume Void Long 🕳️⬆️", "Volume Void Short 🕳️⬇️"]
    is_harmonic = st.session_state.current_strategy in ["Harmonic Bullish 🦋⬆️", "Harmonic Bearish 🦋⬇️", "Harmonic All Patterns 🦋"]
    is_wyckoff = st.session_state.current_strategy in ["Wyckoff Accumulation 🏦⬆️", "Wyckoff Distribution 🏦⬇️"]
    is_bi = "Breakout Imminent" in st.session_state.current_strategy
    
    with col_journal:
        st.caption(f"📋 **Ergebnisse** — {st.session_state.current_strategy or ''} | {st.session_state.get('active_trading_session', '') if st.session_state.market_type == 'Aktien' else '24/7'}")
        
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            
            # ── Universal Earnings Check (für Pipelines ohne K4) ──
            if "EarningsWarning" not in df.columns and st.session_state.market_type == "Aktien":
                try:
                    finnhub_key = st.secrets.get("FINNHUB_KEY", "")
                    if finnhub_key:
                        earnings_cal = fetch_earnings_calendar(finnhub_key, days_ahead=7)
                        if earnings_cal:
                            for idx_e in range(len(st.session_state.scan_results)):
                                r = st.session_state.scan_results[idx_e]
                                ear_info = check_earnings_proximity(r.get("Ticker", ""), earnings_cal)
                                if ear_info:
                                    r["EarningsWarning"] = ear_info
                                    penalty = ear_info.get("score_penalty", 0)
                                    if penalty and "SetupScore" in r:
                                        r["SetupScore"] = min(100, max(0, r["SetupScore"] + penalty))
                            # DataFrame neu erstellen mit Earnings-Daten
                            df = pd.DataFrame(st.session_state.scan_results)
                except Exception:
                    pass
            
            # ── FINALE SORTIERUNG: Immer nach Score absteigend ──
            # Nach Earnings-Penalties & VP-Adjustments können Scores ungeordnet sein
            sort_key = None
            if "BI_Score" in df.columns:
                sort_key = "BI_Score"
            elif "SetupScore" in df.columns:
                sort_key = "SetupScore"
            elif "WyckoffScore" in df.columns:
                sort_key = "WyckoffScore"
            elif "VoidScore" in df.columns:
                sort_key = "VoidScore"
            elif "Alpha" in df.columns:
                sort_key = "Alpha"

            if sort_key:
                df = df.sort_values(by=sort_key, ascending=False).reset_index(drop=True)
                # scan_results auch aktualisieren (für Navigation)
                st.session_state.scan_results = df.to_dict("records")

            # Earnings-Marker als separate Spalte (Ticker nicht verändern!)
            if "EarningsWarning" in df.columns:
# _earnings_flag — Moved to modules/analysis.py
                df["ER"] = df["EarningsWarning"].apply(_earnings_flag)
            
            # =====================================================================
            # KOMPAKTE TICKER-LISTE
            # =====================================================================
            num_results = len(df)
            current_idx = st.session_state.selected_row_index
            
            # Begrenze Index
            if current_idx >= num_results:
                current_idx = max(0, num_results - 1)
                st.session_state.selected_row_index = current_idx
            
            # Keyboard Navigation (W/E)
            from streamlit.components.v1 import html
            keyboard_html = f"""
            <div style="height:0;overflow:hidden;">
                <script>
                    (function() {{
                        var currentIdx = {current_idx}, maxIdx = {num_results - 1};
                        function clickBtn(text) {{
                            var btns = window.parent.document.querySelectorAll('button');
                            for (var i = 0; i < btns.length; i++) {{
                                if ((btns[i].textContent||'').includes(text)) {{ btns[i].click(); return; }}
                            }}
                        }}
                        function scrollDataframeToRow(rowIdx) {{
                            try {{
                                var doc = window.parent.document;
                                // Methode 1: Streamlit Glide Data Grid (neuere Versionen)
                                var glideCanvases = doc.querySelectorAll('[data-testid="stDataFrame"] .dvn-scroller');
                                if (glideCanvases.length > 0) {{
                                    var scroller = glideCanvases[0];
                                    var rowHeight = 35;
                                    var targetScroll = Math.max(0, (rowIdx * rowHeight) - (scroller.clientHeight / 2));
                                    scroller.scrollTop = targetScroll;
                                    return;
                                }}
                                // Methode 2: role=grid (aeltere Versionen)
                                var grids = doc.querySelectorAll('[role="grid"], [class*="dataframe"]');
                                for (var dc = 0; dc < grids.length; dc++) {{
                                    var rows = grids[dc].querySelectorAll('[role="row"]');
                                    if (rows.length > rowIdx + 1) {{
                                        rows[rowIdx + 1].scrollIntoView({{ behavior: "smooth", block: "nearest" }});
                                        return;
                                    }}
                                }}
                                // Methode 3: Generischer Scroll-Container
                                var containers = doc.querySelectorAll('[data-testid="stDataFrame"]');
                                if (containers.length > 0) {{
                                    var c = containers[0];
                                    var scrollEl = c.querySelector('[style*="overflow"]') || c;
                                    var rowH = 35;
                                    scrollEl.scrollTop = Math.max(0, (rowIdx * rowH) - (scrollEl.clientHeight / 2));
                                }}
                            }} catch(e) {{ }}
                        }}
                        function onKey(e) {{
                            var ae = window.parent.document.activeElement || e.target || {{}};
                            var tag = (ae.tagName||'').toLowerCase();
                            if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
                            if (ae.isContentEditable || ae.getAttribute('role') === 'textbox') return;
                            if ((e.target.tagName||'').toLowerCase() === 'input') return;
                            var k = e.key.toLowerCase();
                            if ((k==='w'||k==='arrowup') && currentIdx > 0) {{ e.preventDefault(); clickBtn('⬆'); }}
                            if ((k==='e'||k==='arrowdown') && currentIdx < maxIdx) {{ e.preventDefault(); clickBtn('⬇'); }}
                        }}
                        try {{ window.parent.document.removeEventListener('keydown', window.parent._alphaNav);
                               window.parent._alphaNav = onKey;
                               window.parent.document.addEventListener('keydown', onKey);
                        }} catch(err) {{ document.addEventListener('keydown', onKey); }}

                        // Auto-scroll to current row on load (mit Retry fuer langsames Rendering)
                        setTimeout(function() {{ scrollDataframeToRow({current_idx}); }}, 150);
                        setTimeout(function() {{ scrollDataframeToRow({current_idx}); }}, 500);
                    }})();
                </script>
            </div>
            """
            html(keyboard_html, height=0)
            
            # ⬆ #1/46 ⬇
            nc1, nc2, nc3 = st.columns([1, 2, 1])
            with nc1:
                if st.button("⬆️", key="nav_prev_btn", disabled=current_idx <= 0, use_container_width=True):
                    st.session_state.selected_row_index = max(0, current_idx - 1)
                    # Sync radio state
                    if "ticker_select_df" in st.session_state:
                        del st.session_state["ticker_select_df"]
                    st.rerun()
            with nc2:
                st.markdown(f"<div style='text-align:center;font-weight:bold;'>#{current_idx + 1}/{num_results}</div>", unsafe_allow_html=True)
            with nc3:
                if st.button("⬇️", key="next_nav_btn", disabled=current_idx >= num_results - 1, use_container_width=True):
                    st.session_state.selected_row_index = min(num_results - 1, current_idx + 1)
                    if "ticker_select_df" in st.session_state:
                        del st.session_state["ticker_select_df"]
                    st.rerun()
            
            # ── Kompakte Ticker-Tabelle ──
            # Earnings flags
            er_col = [""] * num_results
            if "EarningsWarning" in df.columns:
                for i_e in range(num_results):
                    ear = df.iloc[i_e].get("EarningsWarning")
                    if ear and isinstance(ear, dict):
                        level = ear.get("level", "")
                        if level in ("TODAY_AMC", "TODAY_BMO", "TODAY", "YESTERDAY_AMC"):
                            er_col[i_e] = "⛔"
                        elif level == "TOMORROW":
                            er_col[i_e] = "⚠️"
                        elif level == "THIS_WEEK":
                            er_col[i_e] = "📅"
            
            compact_data = {
                "Ticker": df["Ticker"].tolist(),
            }
            has_er = any(e != "" for e in er_col)
            if has_er:
                compact_data["ER"] = er_col
            
            if "Chg%" in df.columns:
                compact_data["%"] = [f"{v:+.1f}%" if isinstance(v, (int, float)) else str(v) for v in df["Chg%"].tolist()]
            
            if "SetupScore" in df.columns:
                compact_data["S"] = df["SetupScore"].tolist()
            
            df_compact = pd.DataFrame(compact_data)
            
            compact_config = {
                "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                "%": st.column_config.TextColumn("%", width="small"),
            }
            if has_er:
                compact_config["ER"] = st.column_config.TextColumn("ER", width="small")
            if "S" in compact_data:
                compact_config["S"] = st.column_config.ProgressColumn("S", min_value=0, max_value=100, format="%d", width="small")
            
            sel = st.dataframe(
                df_compact,
                on_select="rerun",
                selection_mode="single-row",
                hide_index=True,
                use_container_width=True,
                height=300,
                column_config=compact_config
            )
            
            selected_row_idx = current_idx
            if sel.selection and sel.selection.rows:
                clicked_idx = sel.selection.rows[0]
                if clicked_idx != current_idx:
                    st.session_state.selected_row_index = clicked_idx
                    selected_row_idx = clicked_idx
                    st.rerun()
            
            # Zeile verarbeiten
            if selected_row_idx is not None and 0 <= selected_row_idx < len(df):
                row = df.iloc[selected_row_idx]
                st.session_state.selected_symbol = str(row.get("Ticker", ""))
                st.session_state.current_data = row.to_dict()
                
                # =====================================================
                # REALTIME PREIS CHECK (Polygon Paid oder Alpaca)
                # =====================================================
                if st.session_state.market_type == "Aktien":
                    try:
                        ticker = str(row.get("Ticker", ""))
                        scanner_price = row.get("Preis", 0)
                        realtime = None
                        
                        # 1. Versuche Polygon (wenn Key vorhanden = Paid User)
                        try:
                            poly_key = st.secrets["POLYGON_KEY"]
                            realtime = fetch_realtime_price_polygon(ticker, poly_key)
                        except Exception as e:
                            pass
                        
                        # 2. Fallback: Alpaca
                        if not realtime:
                            try:
                                alpaca_key = st.secrets["ALPACA_KEY"]
                                alpaca_secret = st.secrets["ALPACA_SECRET"]
                                if alpaca_key and alpaca_secret:
                                    realtime = fetch_realtime_price_alpaca(ticker, alpaca_key, alpaca_secret)
                            except Exception as e:
                                pass
                        
                        if realtime and realtime.get("price", 0) > 0:
                            rt_price = realtime["price"]
                            price_diff = rt_price - scanner_price
                            price_diff_pct = (price_diff / scanner_price * 100) if scanner_price > 0 else 0
                            
                            source = realtime.get("source", "")
                            time_str = realtime.get("time", "")
                            time_info = f" @ {time_str}" if time_str else ""
                            
                            # Warnung wenn Preis stark abweicht (>3%)
                            if abs(price_diff_pct) > 3:
                                if price_diff_pct > 0:
                                    st.error(f"🚨 **LIVE: ${rt_price:.2f}** (+{price_diff_pct:.1f}% über Scanner!){time_info}")
                                else:
                                    st.success(f"📉 **LIVE: ${rt_price:.2f}** ({price_diff_pct:.1f}% unter Scanner){time_info}")
                            elif abs(price_diff_pct) > 1:
                                st.info(f"📡 **LIVE: ${rt_price:.2f}** ({price_diff_pct:+.1f}%){time_info}")
                            else:
                                st.caption(f"📡 Live: ${rt_price:.2f} ✓{time_info}")
                    except Exception as e:
                        pass  # Fehler ignorieren

                # ═══════════════════════════════════════════════════════
                # 📊 SEKTOR-TREND BANNER
                # ═══════════════════════════════════════════════════════
                if st.session_state.market_type == "Aktien":
                    try:
                        _st_poly = st.secrets.get("POLYGON_KEY", "")
                        if _st_poly:
                            _st_ticker = str(row.get("Ticker", ""))
                            _st_sic = str(row.get("sic_code", "") or "")
                            _render_sector_trend_banner(_st_ticker, sic_code=_st_sic, poly_key=_st_poly)
                    except Exception:
                        pass

                # ═══════════════════════════════════════════════════════
                # ⚠️ EARNINGS WARNING — GANZ OBEN, VOR ALLEM ANDEREN!
                # ═══════════════════════════════════════════════════════
                if "EarningsWarning" in df.columns:
                    try:
                        ear_warn = row.get("EarningsWarning")
                        if ear_warn and isinstance(ear_warn, dict):
                            level = ear_warn.get("level", "")
                            warning_text = ear_warn.get("warning", "")
                            details = ear_warn.get("details", "")
                            penalty = ear_warn.get("score_penalty", 0)
                            
                            # Prominente Warnung je nach Level
                            if level in ("TODAY_AMC", "TODAY_BMO", "TODAY", "YESTERDAY_AMC"):
                                st.error(f"⛔ **{warning_text}**")
                                if details:
                                    st.error(f"📊 {details}")
                                if level == "TODAY_AMC":
                                    st.error("🚫 **NICHT KAUFEN!** Earnings heute nach Börsenschluss — "
                                            "massiver Gap-Risk morgen. Position VOR Close schliessen oder absichern!")
                                elif level == "TODAY_BMO":
                                    st.error("🚫 **VORSICHT!** Earnings heute vor Eröffnung — "
                                            "Preis kann sich durch Earnings massiv verändert haben!")
                                elif level == "YESTERDAY_AMC":
                                    st.error("🚫 **ACHTUNG!** Earnings gestern AMC — "
                                            "heutiger Preis enthält Earnings-Reaktion!")
                                if penalty:
                                    st.caption(f"SetupScore: {penalty:+d} Punkte wegen Earnings-Risiko")
                            
                            elif level == "TOMORROW":
                                st.warning(f"⚠️ **{warning_text}**")
                                if details:
                                    st.caption(f"📊 {details}")
                                st.warning("⚠️ Position-Sizing reduzieren oder vor Earnings schliessen!")
                                if penalty:
                                    st.caption(f"SetupScore: {penalty:+d} Punkte")
                            
                            elif level == "THIS_WEEK":
                                st.info(f"📅 **{warning_text}**")
                                if details:
                                    st.caption(f"📊 {details}")
                                st.caption("💡 Earnings diese Woche — Haltezeit berücksichtigen!")
                            
                            elif level == "NEXT_WEEK":
                                st.caption(f"📋 {warning_text}")
                    except Exception:
                        pass
                
                # Insider Details anzeigen
                if is_insider and "Transactions" in df.columns:
                    try:
                        transactions = row["Transactions"]
                        if transactions and isinstance(transactions, list):
                            st.divider()
                            st.caption("📊 Letzte Transaktionen:")
                            for t in transactions[:3]:
                                emoji = "🟢" if t["type"] == "BUY" else "🔴"
                                st.caption(f"{emoji} {t['name'][:20]}: {t['shares']:,.0f} Aktien (${t['value']:,.0f})")
                    except Exception as e:
                        pass
                
                # Flag Pattern Details anzeigen
                is_flag_strategy = st.session_state.current_strategy in ["Bull Flag", "Bear Flag"]
                if is_flag_strategy and "FlagDetails" in df.columns:
                    try:
                        flag_details = row.get("FlagDetails", "")
                        flag_score = row.get("FlagScore", 0) if "FlagScore" in df.columns else 0
                        
                        if flag_details and isinstance(flag_details, list) and len(flag_details) > 0:
                            st.divider()
                            if flag_score >= 80:
                                st.success(f"🎯 Flag Score: **{flag_score}/100** (EXCELLENT)")
                            elif flag_score >= 60:
                                st.info(f"✅ Flag Score: **{flag_score}/100** (GOOD)")
                            else:
                                st.warning(f"⚠️ Flag Score: **{flag_score}/100** (WEAK)")
                            
                            st.caption("**Pattern-Analyse:**")
                            for detail in flag_details:
                                st.caption(detail)
                    except Exception as e:
                        pass
                
                # =====================================================================
                # V69: CANDLESTICK-ANALYSE — Trend, Patterns, Support/Resistance
                # =====================================================================
                if "CandleAnalysis" in df.columns:
                    try:
                        _ca = row.get("CandleAnalysis")
                        if _ca and isinstance(_ca, dict) and _ca.get("candle_count", 0) >= 5:
                            st.divider()
                            _ca_trend = _ca.get("trend", "unknown")
                            _ca_strength = _ca.get("trend_strength", 0)
                            _ca_patterns = _ca.get("patterns", [])
                            _ca_vol = _ca.get("volume_trend", "neutral")

                            # Trend + Volume auf einer Zeile
                            _trend_icon = {"up": "📈", "down": "📉", "sideways": "➡️"}.get(_ca_trend, "❓")
                            _trend_label = {"up": "Aufwärtstrend", "down": "Abwärtstrend", "sideways": "Seitwärts"}.get(_ca_trend, "Unbekannt")
                            _vol_icon = {"accumulation": "🟢 Akkumulation", "distribution": "🔴 Distribution", "neutral": "⚪ Neutral"}.get(_ca_vol, "")

                            _col_t, _col_v = st.columns(2)
                            with _col_t:
                                st.caption(f"{_trend_icon} **Trend:** {_trend_label} ({_ca_strength}%)")
                            with _col_v:
                                st.caption(f"📊 **Volumen:** {_vol_icon}")

                            # Support/Resistance
                            _supp = _ca.get("support", 0)
                            _resi = _ca.get("resistance", 0)
                            if _supp > 0 and _resi > 0:
                                st.caption(f"🟢 Support: ${_supp:,.2f} | 🔴 Resistance: ${_resi:,.2f}")

                            # Consolidation + Breakout Ready
                            if _ca.get("consolidation"):
                                _cd = _ca.get("consol_days", 0)
                                _cr = _ca.get("consol_range_pct", 0)
                                if _ca.get("breakout_ready"):
                                    st.success(f"🎯 **Breakout Ready!** {_cd} Tage Konsolidierung ({_cr:.1f}% Range) + steigendes Volumen")
                                else:
                                    st.info(f"📦 Konsolidierung seit {_cd} Tagen ({_cr:.1f}% Range)")

                            # Candlestick Patterns
                            if _ca_patterns:
                                for _cp in _ca_patterns[:4]:  # Max 4 Patterns anzeigen
                                    _cp_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(_cp.get("type", ""), "")
                                    st.caption(f"{_cp_icon} **{_cp['name']}** ({_cp.get('pos', '')}) — {_cp.get('signal', '')}")
                    except Exception:
                        pass

                # =====================================================================
                # BREAKOUT HEALTH — Fakeout-Erkennung & Selloff-Risiko
                # =====================================================================
                if "BreakoutHealth" in df.columns:
                    try:
                        bh = row.get("BreakoutHealth")
                        if bh and isinstance(bh, dict) and bh.get("health_score") is not None:
                            st.divider()
                            
                            # Header mit Score und Verdict
                            health = bh.get("health_score", 0)
                            verdict = bh.get("verdict", "")
                            verdict_emoji = bh.get("verdict_emoji", "")
                            selloff = bh["selloff_risk"]
                            selloff_emoji = bh["selloff_risk_emoji"]
                            
                            col_health, col_selloff = st.columns(2)
                            with col_health:
                                if health >= 60:
                                    st.success(f"{verdict_emoji} Breakout Health: **{health}/100** ({verdict})")
                                elif health >= 40:
                                    st.warning(f"{verdict_emoji} Breakout Health: **{health}/100** ({verdict})")
                                else:
                                    st.error(f"{verdict_emoji} Breakout Health: **{health}/100** ({verdict})")
                            
                            with col_selloff:
                                if selloff == "LOW":
                                    st.success(f"{selloff_emoji} Selloff-Risiko: **{selloff}**")
                                elif selloff == "MEDIUM":
                                    st.info(f"{selloff_emoji} Selloff-Risiko: **{selloff}**")
                                elif selloff == "HIGH":
                                    st.warning(f"{selloff_emoji} Selloff-Risiko: **{selloff}**")
                                else:
                                    st.error(f"{selloff_emoji} Selloff-Risiko: **{selloff}**")
                            
                            # Action
                            st.info(f"💡 **{bh['action']}**")
                            
                            # Signals und Warnings
                            if bh.get("signals"):
                                for sig in bh["signals"]:
                                    st.caption(sig)
                            if bh.get("warnings"):
                                for warn in bh.get("warnings", []):
                                    st.caption(warn)
                    except Exception:
                        pass
                
                # Volatilitäts-Regime und Liquiditäts-Info anzeigen
                if "ATR%" in df.columns:
                    try:
                        atr_val = row["ATR%"] if pd.notna(row.get("ATR%")) else 0
                        vol_regime = row["VolRegime"] if "VolRegime" in df.columns and pd.notna(row.get("VolRegime")) else "NORMAL"
                        dollar_vol = row["DollarVol"] if "DollarVol" in df.columns and pd.notna(row.get("DollarVol")) else 0
                        
                        if atr_val > 0:
                            st.divider()
                            col_atr, col_liq = st.columns(2)
                            with col_atr:
                                regime_emoji = {"LOW": "😴", "NORMAL": "📊", "HIGH": "⚡", "EXTREME": "🔥"}.get(vol_regime, "📊")
                                regime_color = {"LOW": "blue", "NORMAL": "gray", "HIGH": "orange", "EXTREME": "red"}.get(vol_regime, "gray")
                                st.metric(
                                    f"{regime_emoji} Volatilität", 
                                    f"{atr_val:.1f}% ATR",
                                    delta=vol_regime,
                                    delta_color="off"
                                )
                            with col_liq:
                                if dollar_vol >= 1000000:
                                    liq_str = f"${dollar_vol/1000000:.1f}M"
                                    st.metric("💧 Liquidität", liq_str, delta="HIGH", delta_color="normal")
                                elif dollar_vol >= 100000:
                                    liq_str = f"${dollar_vol/1000:.0f}K"
                                    st.metric("💧 Liquidität", liq_str, delta="OK", delta_color="off")
                                else:
                                    liq_str = f"${dollar_vol/1000:.0f}K"
                                    st.metric("💧 Liquidität", liq_str, delta="LOW ⚠️", delta_color="inverse")
                    except Exception as e:
                        pass
                
                # =====================================================================
                # STRATEGIE-TIMING BEWERTUNG (für alle unterstützten Strategien)
                # =====================================================================
                current_strat = st.session_state.get("current_strategy", "")
                
                # Strategien mit Timing-Bewertung
                timing_strategies = {
                    # Breakout Strategien
                    "Breakout Long": "breakout", "Breakout Short": "breakout", 
                    "Breakout Long (Ultra)": "breakout", "Breakout Short (Ultra)": "breakout",
                    "Breakdown Short": "breakout",
                    "Early Momentum": "breakout", "Penny Rockets": "breakout",
                    "Whale Watch": "breakout", "Whale Watch Short 🐻": "breakout",
                    "Consolidation Breakout": "breakout",
                    "Bull Flag": "breakout", "Bear Flag": "breakout",
                    "Volume Surge": "breakout", "High Volume Churn": "breakout",
                    # Gap Strategien
                    "Gap Up": "gap", "Gap Down": "gap", 
                    "Gap Up (High Vol)": "gap", "Gap Down (High Vol)": "gap",
                    "PM Gainers 🌅": "gap", "PM Gap & Go 🌅": "gap", "AH Gainers 🌙": "gap",
                    # MA Bounce Strategien
                    "EMA 21 Bounce (Long)": "ma_bounce", "EMA 21 Bounce (Short)": "ma_bounce",
                    "SMA 50 Bounce (Long)": "ma_bounce", "SMA 50 Bounce (Short)": "ma_bounce",
                    "SMA 200 Bounce (Long)": "ma_bounce", "SMA 200 Bounce (Short)": "ma_bounce",
                    # Mean Reversion Strategien
                    "Mean Reversion Long": "reversal", "Mean Reversion Short": "reversal",
                    "Oversold Bounce": "reversal", "Overbought Short": "reversal",
                    "RSI Oversold": "reversal", "RSI Overbought": "reversal",
                    "Reversal Hunter": "reversal",
                    "Reversal Setup 🪤": "reversal",
                    # Dip Buy = MA Bounce Charakter (Pullback im Trend)
                    "Dip Buy": "ma_bounce",
                    # Volume Void Strategien
                    "Volume Void Long": "void", "Volume Void Short": "void",
                    # Insider Strategien
                    "Insider Buying": "insider", "Insider Cluster": "insider",
                }
                
                # Prüfe ob aktuelle Strategie eine Timing-Bewertung hat
                strat_type = timing_strategies.get(current_strat)
                
                # Fallback: Prüfe Strategie-Name auf Keywords
                if not strat_type:
                    strat_upper = current_strat.upper() if current_strat else ""
                    if any(x in strat_upper for x in ["BREAKOUT", "AUSBRUCH", "ULTRA"]):
                        strat_type = "breakout"
                    elif any(x in strat_upper for x in ["GAP", "PM", "AH", "PREMARKET"]):
                        strat_type = "gap"
                    elif any(x in strat_upper for x in ["BOUNCE", "EMA", "SMA", "MA "]):
                        strat_type = "ma_bounce"
                    elif any(x in strat_upper for x in ["REVERSAL", "REVERSION", "OVERSOLD", "OVERBOUGHT", "RSI"]):
                        strat_type = "reversal"
                    elif any(x in strat_upper for x in ["VOID", "FVG", "LIQUIDITY"]):
                        strat_type = "void"
                    elif any(x in strat_upper for x in ["INSIDER", "FORM 4"]):
                        strat_type = "insider"
                
                if strat_type:
                    try:
                        # Hole Fib-Info falls verfügbar
                        fib_info = st.session_state.get("fib_info", {})
                        
                        # TREND-ERKENNUNG: Reversal nur wenn Stock tatsächlich im Downtrend
                        # Prüfe ob Preis in der oberen/unteren Hälfte des Period-Range liegt
                        if strat_type == "reversal" and fib_info:
                            period_high = fib_info.get("period_high", 0)
                            period_low = fib_info.get("period_low", 0)
                            if period_high > period_low > 0:
                                current_price_val = row.get("Preis", 0) or row.get("Close", 0) or row.get("price", 0) or 0
                                if current_price_val > 0:
                                    range_position = (current_price_val - period_low) / (period_high - period_low)
                                    if range_position > 0.60:
                                        # Stock nahe Period High = Uptrend → Das ist KEIN Reversal!
                                        # Reklassifiziere als Breakout/Continuation
                                        strat_type = "breakout"
                        
                        # Berechne Strategie-spezifisches Timing
                        timing = get_timing_assessment(row.to_dict(), current_strat, fib_info)
                        
                        st.divider()
                        
                        # Titel basierend auf Strategie-Typ
                        timing_titles = {
                            "breakout": "🎯 Breakout-Timing",
                            "gap": "🌅 Gap-Timing",
                            "ma_bounce": "📈 MA-Bounce Timing",
                            "reversal": "🔄 Reversal-Timing",
                            "void": "📊 Volume-Void Timing",
                            "insider": "👔 Insider-Signal Stärke"
                        }
                        title = timing_titles.get(strat_type, "🎯 Timing-Bewertung")
                        
                        st.subheader(f"{title}: {timing.get('emoji', '')} {timing.get('rating', '')}")
                        st.caption(f"Score: **{timing.get('score', 0)}/{timing.get('max_score', 1)}** | {timing.get('risk', '')}")
                        
                        # Faktoren anzeigen
                        col_tech, col_conf = st.columns(2)
                        
                        # Faktor-Überschriften je nach Strategie
                        factor_titles = {
                            "breakout": ("📊 Technische Faktoren:", "📈 Bestätigungs-Faktoren:"),
                            "gap": ("📊 Gap-Faktoren:", "📈 Bestätigung:"),
                            "ma_bounce": ("📊 MA-Faktoren:", "📈 Bestätigung:"),
                            "reversal": ("📊 Überdehnungs-Faktoren:", "📈 Umkehr-Signale:"),
                            "void": ("📊 Void-Faktoren:", "📈 Setup-Qualität:"),
                            "insider": ("📊 Signal-Stärke:", "📈 Timing-Faktoren:")
                        }
                        title1, title2 = factor_titles.get(strat_type, ("📊 Faktoren:", "📈 Bestätigung:"))
                        
                        with col_tech:
                            st.markdown(f"**{title1}**")
                            for f in timing.get('factors', [])[:3]:
                                icon = "✅" if f['ok'] else "❌"
                                st.caption(f"{icon} {f['name']}: {f['value']} ({f['detail']})")
                        
                        with col_conf:
                            st.markdown(f"**{title2}**")
                            for f in timing.get('factors', [])[3:]:
                                icon = "✅" if f['ok'] else "❌"
                                st.caption(f"{icon} {f['name']}: {f['value']} ({f['detail']})")
                        
                        # Strategie-spezifische Empfehlung
                        recommendation = timing.get('recommendation', timing.get('risk', ''))
                        
                        if timing.get('rating', '') in ["FRÜH", "GO", "PERFEKT", "EXTREM", "STARK"]:
                            st.success(f"💡 **Empfehlung:** {recommendation}")
                        elif timing.get('rating', '') in ["OK", "WARTEN", "GUT", "MÖGLICH", "MODERAT"]:
                            st.warning(f"💡 **Empfehlung:** {recommendation}")
                        else:
                            st.error(f"💡 **Empfehlung:** {recommendation}")
                    except Exception as e:
                        pass
                
                # MA Bounce Details anzeigen
                if "MA_Value" in df.columns and pd.notna(row.get("MA_Value")):
                    try:
                        ma_value = row.get("MA_Value", 0)
                        ma_distance = row["MA_Distance%"] if pd.notna(row.get("MA_Distance%")) else 0
                        ma_type = row.get("MA_Type", "") if "MA_Type" in df.columns else "MA"
                        
                        st.divider()
                        st.subheader(f"📈 {ma_type} Support/Resistance")
                        
                        col_ma1, col_ma2 = st.columns(2)
                        with col_ma1:
                            st.metric(f"{ma_type} Wert", f"${ma_value:.2f}")
                        with col_ma2:
                            if ma_distance >= 0:
                                st.metric("Abstand", f"+{ma_distance:.1f}%", delta="ÜBER MA", delta_color="normal")
                            else:
                                st.metric("Abstand", f"{ma_distance:.1f}%", delta="UNTER MA", delta_color="inverse")
                        
                        # Setup Qualität
                        abs_dist = abs(ma_distance)
                        if abs_dist <= 1.0:
                            st.success(f"🎯 **PERFEKT** - Nur {abs_dist:.1f}% vom {ma_type} entfernt!")
                        elif abs_dist <= 2.0:
                            st.info(f"✅ **GUT** - {abs_dist:.1f}% vom {ma_type} entfernt")
                        else:
                            st.warning(f"⚠️ **OK** - {abs_dist:.1f}% vom {ma_type} entfernt")
                        
                    except Exception as e:
                        pass
                
                # ── Volume Profile Details anzeigen (V1.1) ──
                if VP_AVAILABLE and "VP_Summary" in df.columns:
                    try:
                        vp_summary = row.get("VP_Summary", "N/A")
                        if vp_summary and vp_summary != "N/A":
                            st.divider()
                            st.subheader("📊 Volume Profile")
                            st.caption(vp_summary)
                            
                            # VP Signals als Detail-Liste
                            vp_signals = row.get("VP_Signals") if "VP_Signals" in df.columns else None
                            if isinstance(vp_signals, dict) and vp_signals.get("signals"):
                                for sig in vp_signals["signals"]:
                                    st.text(f"  {sig}")
                                
                                adj = vp_signals.get("score_adjustment", 0)
                                if adj > 0:
                                    st.success(f"VP Score: +{adj} Punkte")
                                elif adj < 0:
                                    st.error(f"VP Score: {adj} Punkte")
                    except Exception as e:
                        pass
                
                # Volume Void Details anzeigen
                if is_volume_void and "VoidScore" in df.columns:
                    try:
                        void_score = row["VoidScore"] if pd.notna(row.get("VoidScore")) else 0
                        void_dist = row["VoidDist%"] if pd.notna(row.get("VoidDist%")) else 0
                        void_size = row["VoidSize%"] if pd.notna(row.get("VoidSize%")) else 0
                        poc = row["POC"] if pd.notna(row.get("POC")) else 0
                        vah = row["VAH"] if pd.notna(row.get("VAH")) else 0
                        val = row["VAL"] if pd.notna(row.get("VAL")) else 0
                        voids_above = row["VoidsAbove"] if pd.notna(row.get("VoidsAbove")) else 0
                        voids_below = row["VoidsBelow"] if pd.notna(row.get("VoidsBelow")) else 0
                        nearest_void = row.get("NearestVoid", 0) if "NearestVoid" in df.columns else None
                        
                        st.divider()
                        
                        # Score Anzeige
                        if void_score >= 70:
                            st.success(f"🕳️ **Volume Void Score: {void_score}/100** (EXCELLENT)")
                        elif void_score >= 50:
                            st.info(f"🕳️ **Volume Void Score: {void_score}/100** (GOOD)")
                        else:
                            st.warning(f"🕳️ **Volume Void Score: {void_score}/100** (MODERATE)")
                        
                        # Void Details
                        col_v1, col_v2 = st.columns(2)
                        with col_v1:
                            direction = "⬆️" if "Long" in st.session_state.current_strategy else "⬇️"
                            st.metric(f"Entfernung zum Void {direction}", f"{void_dist:.1f}%")
                            st.metric("Void Größe", f"{void_size:.1f}%")
                        with col_v2:
                            st.metric("Voids darüber", f"{voids_above}")
                            st.metric("Voids darunter", f"{voids_below}")
                        
                        # Volume Profile Levels
                        st.caption("**📊 Volume Profile Levels:**")
                        current_price = row["Preis"]
                        
                        # VAH, POC, VAL als Levels
                        if vah > 0:
                            vah_dist = ((vah - current_price) / current_price * 100)
                            st.caption(f"📈 VAH (Value Area High): ${vah:.2f} ({vah_dist:+.1f}%)")
                        if poc > 0:
                            poc_dist = ((poc - current_price) / current_price * 100)
                            st.caption(f"🎯 POC (Point of Control): ${poc:.2f} ({poc_dist:+.1f}%)")
                        if val > 0:
                            val_dist = ((val - current_price) / current_price * 100)
                            st.caption(f"📉 VAL (Value Area Low): ${val:.2f} ({val_dist:+.1f}%)")
                        
                        # Nearest Void Details
                        if nearest_void and isinstance(nearest_void, dict):
                            st.caption("**🕳️ Nächstes Volume Void:**")
                            st.caption(f"   Range: ${nearest_void.get('low', 0):.2f} - ${nearest_void.get('high', 0):.2f}")
                            st.caption(f"   Volumen: {nearest_void.get('volume_pct', 0):.0f}% des Durchschnitts")
                        
                    except Exception as e:
                        pass
                
                # Harmonic Pattern Details anzeigen 🦋
                if "PatternData" in df.columns and pd.notna(row.get("PatternData")):
                    try:
                        pattern_data = row["PatternData"]
                        if isinstance(pattern_data, dict):
                            st.divider()
                            
                            # Pattern Header
                            emoji = pattern_data.get("emoji", "🦋")
                            pattern_name = pattern_data.get("pattern", "Unknown")
                            direction = pattern_data.get("direction", "")
                            score = pattern_data.get("score", 0)
                            success_rate = pattern_data.get("success_rate", 0)
                            
                            dir_emoji = "⬆️ LONG" if direction == "LONG" else "⬇️ SHORT"
                            
                            # Distanz-Warnung wenn Entry weit vom Preis
                            entry_dist = pattern_data.get("entry_distance_pct", 0)
                            dist_warning = ""
                            if entry_dist > 20:
                                dist_warning = " | 🚫 ABGELAUFEN"
                            elif entry_dist > 10:
                                dist_warning = " | ⚠️ VERALTET"
                            elif entry_dist > 5:
                                dist_warning = f" | 🟡 Entry {entry_dist:.0f}% entfernt"

                            if score >= 80 and entry_dist <= 10:
                                st.success(f"{emoji} **{pattern_name}** | {dir_emoji} | Score: {score}/100{dist_warning}")
                            elif score >= 60 and entry_dist <= 20:
                                st.info(f"{emoji} **{pattern_name}** | {dir_emoji} | Score: {score}/100{dist_warning}")
                            else:
                                st.warning(f"{emoji} **{pattern_name}** | {dir_emoji} | Score: {score}/100{dist_warning}")

                            st.caption(f"📊 Historische Erfolgsrate: **{success_rate}%**")
                            
                            # XABCD Punkte
                            points = pattern_data.get("points", {})
                            if points:
                                st.caption("**📍 XABCD Punkte:**")
                                point_str = " → ".join([f"{k}=${v}" for k, v in points.items()])
                                st.caption(f"   {point_str}")
                            
                            # Fibonacci Ratios
                            ratios = pattern_data.get("ratios", {})
                            if ratios:
                                st.caption("**📐 Fibonacci Verhältnisse:**")
                                for ratio_name, ratio_val in ratios.items():
                                    st.caption(f"   {ratio_name}: {ratio_val}")
                            
                            # Trade Setup
                            trade = pattern_data.get("trade", {})
                            if trade:
                                st.divider()
                                st.caption("**🎯 Trade Setup:**")
                                
                                col_t1, col_t2 = st.columns(2)
                                with col_t1:
                                    st.metric("Entry", f"${trade.get('entry', 0):.2f}")
                                    st.metric("Stop Loss", f"${trade.get('stop_loss', 0):.2f}", 
                                             delta=f"{((trade.get('stop_loss', 0) - trade.get('entry', 1)) / trade.get('entry', 1) * 100):.1f}%",
                                             delta_color="inverse")
                                with col_t2:
                                    st.metric("TP1", f"${trade.get('tp1', 0):.2f}")
                                    st.metric("TP2", f"${trade.get('tp2', 0):.2f}")
                                
                                rr = trade.get('risk_reward', 0)
                                if rr >= 2:
                                    st.success(f"✅ Risk/Reward: **{rr:.1f}:1** (Excellent)")
                                elif rr >= 1.5:
                                    st.info(f"📊 Risk/Reward: **{rr:.1f}:1** (Good)")
                                else:
                                    st.warning(f"⚠️ Risk/Reward: **{rr:.1f}:1** (Consider)")
                            
                            # Pattern Details (Matches)
                            details = pattern_data.get("details", [])
                            if details:
                                with st.expander("📋 Pattern Details"):
                                    for detail in details:
                                        st.caption(detail)
                    except Exception as e:
                        pass
                
                # Wyckoff Pattern Details anzeigen 🏦
                # =====================================================================
                # 🔮 BREAKOUT IMMINENT — Detail-Anzeige
                # =====================================================================
                if is_bi and "BI_Score" in df.columns:
                    try:
                        bi_score = row.get("BI_Score", 0)
                        bi_max = row.get("BI_MaxScore", 200)
                        bi_conf = row.get("BI_Confidence", 0)
                        bi_details = row.get("BI_Details", [])
                        bi_dir = row.get("BI_Direction", "LONG")
                        bi_grade = row.get("BI_GradeLabel", "")

                        st.divider()
                        dir_label = "⬆️ LONG" if bi_dir == "LONG" else "⬇️ SHORT"

                        # Grade-basierte Farbe
                        bi_grade_letter = row.get("BI_Grade", "D")
                        if bi_grade_letter == "S":
                            st.success(f"🔮 **Breakout Imminent V2** {dir_label} | {bi_grade} | Score: **{bi_score}/{bi_max}** ({bi_score*100//bi_max}%) | Konfidenz: {bi_conf}%")
                        elif bi_grade_letter == "A":
                            st.success(f"🔮 **Breakout Imminent V2** {dir_label} | {bi_grade} | Score: **{bi_score}/{bi_max}** ({bi_score*100//bi_max}%) | Konfidenz: {bi_conf}%")
                        elif bi_grade_letter == "B":
                            st.info(f"🔮 **Breakout Imminent V2** {dir_label} | {bi_grade} | Score: **{bi_score}/{bi_max}** ({bi_score*100//bi_max}%) | Konfidenz: {bi_conf}%")
                        else:
                            st.warning(f"🔮 **Breakout Imminent V2** {dir_label} | {bi_grade} | Score: **{bi_score}/{bi_max}** ({bi_score*100//bi_max}%) | Konfidenz: {bi_conf}%")

                        # Signal-Details nach Gruppen
                        if isinstance(bi_details, list):
                            fire_signals = [d for d in bi_details if "🔥" in str(d)]
                            ok_signals = [d for d in bi_details if "✅" in str(d)]
                            weak_signals = [d for d in bi_details if "⚠️" in str(d) or "❌" in str(d)]

                            if fire_signals:
                                st.caption(f"**🔥 Starke Signale ({len(fire_signals)}/20):**")
                                for s in fire_signals:
                                    st.caption(f"  {s}")
                            if ok_signals:
                                st.caption(f"**✅ Positive Signale ({len(ok_signals)}/20):**")
                                for s in ok_signals:
                                    st.caption(f"  {s}")
                            if weak_signals:
                                with st.expander(f"⚠️ Schwache/Fehlende Signale ({len(weak_signals)}/20)"):
                                    for s in weak_signals:
                                        st.caption(f"  {s}")

                        # Range + Entry/SL/TP
                        st.caption(f"📏 **Range:** ${row.get('RangeLow', 0):.2f} — ${row.get('RangeHigh', 0):.2f}")

                        col_t1, col_t2 = st.columns(2)
                        with col_t1:
                            st.metric("Entry", f"${row.get('Entry', 0):.2f}")
                            st.metric("Stop Loss", f"${row.get('StopLoss', 0):.2f}")
                        with col_t2:
                            st.metric("TP1 (1x Range)", f"${row.get('TP1', 0):.2f}")
                            st.metric("TP2 (1.618x)", f"${row.get('TP2', 0):.2f}")

                        rr = row.get('RiskReward', 0)
                        if rr >= 2:
                            st.success(f"✅ R:R **{rr:.1f}:1**")
                        elif rr >= 1.5:
                            st.info(f"📊 R:R **{rr:.1f}:1**")
                        else:
                            st.warning(f"⚠️ R:R **{rr:.1f}:1**")
                    except Exception:
                        pass

                if "WyckoffPhase" in df.columns and pd.notna(row.get("WyckoffPhase")):
                    try:
                        st.divider()
                        w_type = row.get("WyckoffType", "")
                        w_phase = row.get("WyckoffPhase", "")
                        w_score = row.get("WyckoffScore", 0)
                        w_events = row.get("WyckoffEvents", "")
                        
                        dir_emoji = "⬆️ LONG" if "Accumulation" in w_type else "⬇️ SHORT"
                        
                        if w_score >= 70:
                            st.success(f"🏦 **Wyckoff {w_type}** | {dir_emoji} | Score: {w_score}")
                        elif w_score >= 50:
                            st.info(f"🏦 **Wyckoff {w_type}** | {dir_emoji} | Score: {w_score}")
                        else:
                            st.warning(f"🏦 **Wyckoff {w_type}** | {dir_emoji} | Score: {w_score}")
                        
                        st.caption(f"📍 **Phase:** {w_phase}")
                        st.caption(f"📏 **Range:** ${row.get('RangeLow', 0):.2f} — ${row.get('RangeHigh', 0):.2f}")
                        
                        if w_events:
                            st.caption(f"📋 **Events:** {w_events}")
                        
                        col_t1, col_t2 = st.columns(2)
                        with col_t1:
                            st.metric("Entry", f"${row.get('Entry', 0):.2f}")
                            st.metric("Stop Loss", f"${row.get('StopLoss', 0):.2f}")
                        with col_t2:
                            st.metric("TP1", f"${row.get('TP1', 0):.2f}")
                            rr = row.get('RiskReward', 0)
                            if rr >= 2:
                                st.success(f"✅ R:R **{rr:.1f}:1**")
                            elif rr >= 1.5:
                                st.info(f"📊 R:R **{rr:.1f}:1**")
                            else:
                                st.warning(f"⚠️ R:R **{rr:.1f}:1**")
                    except Exception:
                        pass
                
                # =====================================================================
                # 🔥 CONFLUENCE CHECK — Automatisch bei Klick (alle Strategien)
                # Berechnet 10 unabhängige Kategorien für den gewählten Ticker
                # =====================================================================
                if st.session_state.market_type == "Aktien" and "ConfluenceScore" not in df.columns:
                    try:
                        ticker_for_conf = str(row.get("Ticker", ""))
                        price_for_conf = row.get("Preis", 0)
                        change_for_conf = row.get("Chg%", 0)
                        
                        # Nur für Aktien mit gültigem Preis
                        if price_for_conf > 0:
                            # Cache Key: Ticker + Preis (ändert sich bei neuem Scan)
                            cache_key = f"conf_{ticker_for_conf}_{price_for_conf:.2f}"
                            
                            # Prüfe ob bereits gecacht
                            if cache_key not in st.session_state:
                                try:
                                    poly_key = st.secrets["POLYGON_KEY"]
                                    
                                    # SPY-Daten cachen (1x pro Session)
                                    if "spy_confluence_data" not in st.session_state:
                                        try:
                                            spy_ohlcv = fetch_ohlcv_for_chart("SPY", poly_key, timeframe="1D", bars=60)
                                            if spy_ohlcv and len(spy_ohlcv) >= 2:
                                                spy_chg = (spy_ohlcv[-1]["close"] - spy_ohlcv[-2]["close"]) / spy_ohlcv[-2]["close"] * 100
                                                spy_closes = [d["close"] for d in spy_ohlcv]
                                                spy_ema50 = sum(spy_closes[-50:]) / 50 if len(spy_closes) >= 50 else None
                                                spy_bull = spy_ohlcv[-1]["close"] > spy_ema50 if spy_ema50 else None
                                                st.session_state.spy_confluence_data = {
                                                    "change": spy_chg, "trend_bullish": spy_bull
                                                }
                                            else:
                                                st.session_state.spy_confluence_data = {"change": None, "trend_bullish": None}
                                        except Exception:
                                            st.session_state.spy_confluence_data = {"change": None, "trend_bullish": None}
                                    
                                    spy_data = st.session_state.spy_confluence_data
                                    
                                    # OHLCV für Ticker laden (1D, 220 Bars für EMA200)
                                    ohlcv_conf = fetch_ohlcv_for_chart(ticker_for_conf, poly_key, timeframe="1D", bars=220)
                                    
                                    # Richtung aus Change bestimmen
                                    conf_direction = "long" if change_for_conf >= 0 else "short"
                                    
                                    # Confluence berechnen
                                    conf_result = calculate_confluence_score(
                                        ticker=ticker_for_conf,
                                        price=price_for_conf,
                                        change_pct=change_for_conf,
                                        rvol=row.get("RVOL", None),
                                        close_pos=row.get("ClosePos", 0.5),
                                        high=row.get("High", 0),
                                        low=row.get("Low", 0),
                                        prev_close=row.get("PrevClose", 0),
                                        vortag_pct=row.get("Vortag%", 0),
                                        atr_pct=row.get("ATR%", None),
                                        dollar_volume=row.get("DollarVol", None),
                                        ohlcv_data=ohlcv_conf,
                                        spy_change=spy_data.get("change"),
                                        spy_trend_bullish=spy_data.get("trend_bullish"),
                                        direction=conf_direction
                                    )
                                    st.session_state[cache_key] = conf_result
                                except KeyError:
                                    st.session_state[cache_key] = None
                                except Exception:
                                    st.session_state[cache_key] = None
                            
                            # Anzeigen
                            conf_result = st.session_state.get(cache_key)
                            if conf_result and isinstance(conf_result, dict):
                                st.divider()
                                conf_pass = conf_result["total_pass"]
                                conf_signal = conf_result["signal"]
                                conf_dir = conf_result.get("direction", "")
                                dir_emoji = "🟢 LONG" if conf_dir == "long" else "🔴 SHORT"
                                
                                # Kompakte Header-Zeile
                                if conf_pass >= 9:
                                    st.success(f"🔥 Confluence: **{conf_pass}/10** {dir_emoji} — {conf_result.get('action', '')}")
                                elif conf_pass >= 8:
                                    st.success(f"🔥 Confluence: **{conf_pass}/10** {dir_emoji} — {conf_result.get('action', '')}")
                                elif conf_pass >= 7:
                                    st.info(f"🔥 Confluence: **{conf_pass}/10** {dir_emoji} — {conf_result.get('action', '')}")
                                elif conf_pass >= 6:
                                    st.warning(f"⚠️ Confluence: **{conf_pass}/10** {dir_emoji} — {conf_result.get('action', '')}")
                                else:
                                    st.error(f"🚫 Confluence: **{conf_pass}/10** {dir_emoji} — {conf_result.get('action', '')}")
                                
                                # 10 Kategorien kompakt als 2x5 Grid
                                conf_cats = conf_result.get("categories", {})
                                if conf_cats:
                                    cat_list = list(conf_cats.values())
                                    col_a, col_b = st.columns(2)
                                    for i, cat in enumerate(cat_list):
                                        target_col = col_a if i < 5 else col_b
                                        with target_col:
                                            icon = "✅" if cat["pass"] else "❌"
                                            st.caption(f"{icon} {cat.get('emoji', '')} **{cat.get('name', '')}**: {cat.get('value', '')}")
                    except Exception:
                        pass

                # =====================================================================
                # ₿ BTC KONTEXT — Relative Stärke vs Bitcoin (nur Krypto)
                # =====================================================================
                if st.session_state.market_type == "Krypto":
                    btc_chg = row.get("BTC_Chg%", None)
                    rel_str = row.get("RelStrength", None)
                    btc_label = row.get("BTC_Label", "")
                    coin_chg = row.get("Chg%", 0)

                    if btc_chg is not None:
                        st.divider()
                        btc_c1, btc_c2, btc_c3 = st.columns(3)
                        with btc_c1:
                            btc_color = "normal" if abs(btc_chg) < 2 else ("off" if btc_chg < 0 else "normal")
                            st.metric("₿ Bitcoin 24h", f"{btc_chg:+.1f}%")
                        with btc_c2:
                            st.metric("Rel. Stärke vs BTC", f"{rel_str:+.1f}%" if rel_str is not None else "N/A")
                        with btc_c3:
                            st.metric("Signal", btc_label)

                        # Interpretation
                        if abs(coin_chg - btc_chg) <= 2.0:
                            st.warning(f"🔗 **Nur BTC-Korrelation!** Coin bewegt sich ≈ gleich wie BTC ({btc_chg:+.1f}%). Kein eigenständiger Breakout — warte auf Entkopplung.")
                        elif rel_str and rel_str > 5.0:
                            st.success(f"💪 **Starke relative Stärke!** Coin outperformt BTC um {rel_str:+.1f}% — eigenständiger Move, echtes Signal.")
                        elif rel_str and rel_str < -5.0:
                            st.error(f"⚠️ **Relative Schwäche!** Coin underperformt BTC um {rel_str:+.1f}% — trotz BTC-Stärke schwach.")
                        elif rel_str and rel_str > 2.0:
                            st.info(f"📈 **Leicht relativ stark** vs BTC (+{rel_str:.1f}%) — gutes Zeichen, aber noch keine klare Entkopplung.")

                # ACTION BUTTONS ROW
                _btn_cols = st.columns(3)
                with _btn_cols[0]:
                    if st.button(f"⭐ Watchlist", use_container_width=True, key=f"wl_{selected_row_idx}"):
                        if add_to_watchlist(row.get("Ticker", ""), row.to_dict()):
                            st.success(f"✅ {row.get('Ticker', '')} hinzugefügt!")
                        else:
                            st.info("Bereits in Watchlist")
                with _btn_cols[1]:
                    if st.session_state.market_type == "Aktien":
                        if st.button(f"🤖 AI Chart", use_container_width=True, type="primary", key=f"ai_{selected_row_idx}"):
                            st.session_state.show_ai_chart = True
                            st.session_state.ai_chart_ticker = row.get('FullTicker', row.get('Ticker', ''))
                            st.rerun()
                with _btn_cols[2]:
                    _ib_live = IB_INSYNC_AVAILABLE and ib_is_connected()
                    if st.button(f"📤 IBKR", use_container_width=True, key=f"ib_{selected_row_idx}", disabled=not _ib_live,
                                 help=None if _ib_live else ("Verbinde TWS in der Sidebar" if IB_INSYNC_AVAILABLE else "ib_insync nicht installiert")):
                        if _ib_live:
                            st.session_state.ib_show_form = row.get('Ticker', '')

                # IBKR ORDER FORM (appears when button clicked)
                if st.session_state.get("ib_show_form") == row.get('Ticker', ''):
                    with st.container(border=True):
                        st.caption(f"📤 **Order für {row.get('Ticker', '')}** an TWS senden")

                        # Check for pre-calculated levels
                        has_entry = row.get("Entry") and float(row.get("Entry", 0)) > 0
                        has_sl = row.get("StopLoss") and float(row.get("StopLoss", 0)) > 0
                        has_tp = (row.get("TP1") and float(row.get("TP1", 0)) > 0) or (row.get("TP2") and float(row.get("TP2", 0)) > 0)
                        has_auto = has_entry and has_sl and has_tp

                        current_price = float(row.get("Preis", 0))

                        # Direction
                        _dir_default = 0  # LONG
                        if "Short" in str(st.session_state.get("current_strategy", "")) or "Breakdown" in str(st.session_state.get("current_strategy", "")) or "Bear" in str(st.session_state.get("current_strategy", "")):
                            _dir_default = 1
                        if row.get("BI_Direction") == "SHORT":
                            _dir_default = 1

                        _fc1, _fc2 = st.columns(2)
                        with _fc1:
                            _direction = st.radio("Richtung", ["LONG", "SHORT"], index=_dir_default, horizontal=True, key=f"ibdir_{row.get('Ticker', '')}")
                        with _fc2:
                            _size_label = f"Shares" if st.session_state.ib_size_type == "Shares" else f"$ Betrag"
                            _shares_input = st.number_input(_size_label, value=st.session_state.ib_position_size, min_value=1, key=f"ibsz_{row.get('Ticker', '')}")

                        # Entry / SL / TP fields
                        _fc3, _fc4, _fc5, _fc6 = st.columns(4)
                        with _fc3:
                            _entry = st.number_input("Entry $", value=float(row.get("Entry", current_price)), format="%.2f", min_value=0.01, key=f"ibe_{row.get('Ticker', '')}")
                        with _fc4:
                            _sl_default = float(row.get("StopLoss", current_price * (0.97 if _direction == "LONG" else 1.03)))
                            _sl = st.number_input("Stop-Loss $", value=_sl_default, format="%.2f", min_value=0.01, key=f"ibs_{row.get('Ticker', '')}")
                        with _fc5:
                            _tp1_default = float(row.get("TP1", current_price * (1.05 if _direction == "LONG" else 0.95)))
                            _tp1 = st.number_input("TP1 $", value=_tp1_default, format="%.2f", min_value=0.01, key=f"ibt1_{row.get('Ticker', '')}")
                        with _fc6:
                            _tp2_val = float(row.get("TP2", 0))
                            _tp2 = st.number_input("TP2 $ (opt)", value=_tp2_val, format="%.2f", min_value=0.0, key=f"ibt2_{row.get('Ticker', '')}")

                        # R:R display
                        _risk = abs(_entry - _sl) if abs(_entry - _sl) > 0 else 0.01
                        _reward = abs(_tp1 - _entry)
                        _rr = _reward / _risk if _risk > 0 else 0
                        _rr_color = "🟢" if _rr >= 2 else ("🟡" if _rr >= 1 else "🔴")
                        _final_shares = ib_calc_shares(_entry, _shares_input, st.session_state.ib_size_type)

                        st.caption(f"{_rr_color} R:R = **{_rr:.1f}:1** | {_final_shares} Shares | Risk: ${_risk * _final_shares:.0f}")

                        # Send / Cancel buttons
                        _sc1, _sc2 = st.columns(2)
                        with _sc1:
                            if st.button("✅ An TWS senden", use_container_width=True, type="primary", key=f"ibsend_{row.get('Ticker', '')}"):
                                _tp_list = [_tp1]
                                if _tp2 > 0:
                                    _tp_list.append(_tp2)
                                result = ib_submit_bracket(
                                    ticker=row.get("FullTicker", row.get("Ticker", "")),
                                    entry=_entry, sl=_sl, tp_list=_tp_list,
                                    shares=_final_shares, direction=_direction,
                                    market_type=st.session_state.market_type,
                                    exchange=st.session_state.get("selected_exchange", "US")
                                )
                                if result["success"]:
                                    st.success(f"✅ {result['message']}")
                                    st.session_state.ib_orders_log.append({
                                        "ticker": row.get("Ticker", ""), "direction": _direction,
                                        "entry": _entry, "sl": _sl, "tp1": _tp1,
                                        "shares": _final_shares,
                                        "ids": result["order_ids"],
                                        "time": datetime.now().strftime("%H:%M:%S")
                                    })
                                    st.session_state.ib_show_form = None
                                else:
                                    st.error(f"❌ {result['message']}")
                        with _sc2:
                            if st.button("❌ Abbrechen", use_container_width=True, key=f"ibcancel_{row.get('Ticker', '')}"):
                                st.session_state.ib_show_form = None
                                st.rerun()
        else:
            st.info("Klicke 'SCAN STARTEN'")
    
    with col_chart:
        st.subheader(f"📊 {st.session_state.selected_symbol}")
        
        # TIMEFRAME SELECTOR
        col_tf, col_empty = st.columns([1, 2])
        with col_tf:
            selected_tf = st.selectbox(
                "⏱️ Timeframe",
                ["1H", "4H", "1D", "1W", "1M"],
                index=1,  # Default: 4H
                key="tf_selector",
                help="S/R-Levels werden basierend auf diesem Timeframe berechnet"
            )
        
        # Timeframe zu TradingView Interval mappen
        tf_to_tv = {
            "1H": "60",
            "4H": "240", 
            "1D": "D",
            "1W": "W",
            "1M": "M"
        }
        tv_interval = tf_to_tv.get(selected_tf, "240")
        
        # S/R Levels NEU berechnen wenn Timeframe sich ändert
        if "current_data" in st.session_state:
            current_price = st.session_state.current_data.get("Preis", 0)
            ticker = st.session_state.selected_symbol
            m_type = st.session_state.market_type
            
            # Polygon Key für Aktien
            poly_key = None
            if m_type == "Aktien":
                try:
                    poly_key = st.secrets["POLYGON_KEY"]
                except Exception as e:
                    pass
            
            # S/R mit historischen Daten berechnen
            # Für internationale Aktien: FullTicker verwenden (z.B. VNA.DE statt VNA)
            sr_ticker = ticker
            if "current_data" in st.session_state:
                sr_ticker = st.session_state.current_data.get("FullTicker", ticker)
            (supports, resistances), fib_info = calculate_sr_levels(
                price=current_price,
                ticker=sr_ticker,
                market_type=m_type,
                timeframe=selected_tf,
                poly_key=poly_key
            )
            st.session_state.sr_levels = {"support": supports, "resistance": resistances}
            st.session_state.fib_info = fib_info
        
        # S/R LEVELS ANZEIGE
        if st.session_state.sr_levels["support"] or st.session_state.sr_levels["resistance"]:
            st.caption(f"🎯 **Support & Resistance** ({selected_tf})")
            
            # Hole Detail-Infos falls vorhanden
            fib_info = st.session_state.get("fib_info", {})
            supports_detail = fib_info.get("supports_detail", [])
            resistances_detail = fib_info.get("resistances_detail", [])
            
            col_s, col_r = st.columns(2)
            with col_s:
                st.markdown("**🟢 Support**")
                if supports_detail:
                    for i, s in enumerate(supports_detail, 1):
                        # Stärke-Emoji basierend auf Score
                        strength = s.get("strength", 50)
                        if strength >= 90:
                            emoji = "🔥"  # Sehr stark
                        elif strength >= 70:
                            emoji = "💪"  # Stark
                        elif strength >= 50:
                            emoji = "✓"   # OK
                        else:
                            emoji = "○"   # Schwach
                        
                        price = s.get("price", 0)
                        level_type = s.get("type", "")
                        st.caption(f"S{i}: ${price:,.4f} {emoji}")
                        st.caption(f"   ↳ {level_type}")
                else:
                    for i, s in enumerate(st.session_state.sr_levels["support"], 1):
                        st.caption(f"S{i}: ${s:,.4f}")
                        
            with col_r:
                st.markdown("**🔴 Resistance**")
                if resistances_detail:
                    for i, r in enumerate(resistances_detail, 1):
                        strength = r.get("strength", 50)
                        if strength >= 90:
                            emoji = "🔥"
                        elif strength >= 70:
                            emoji = "💪"
                        elif strength >= 50:
                            emoji = "✓"
                        else:
                            emoji = "○"
                        
                        price = r.get("price", 0)
                        level_type = r.get("type", "")
                        st.caption(f"R{i}: ${price:,.4f} {emoji}")
                        st.caption(f"   ↳ {level_type}")
                else:
                    for i, r in enumerate(st.session_state.sr_levels["resistance"], 1):
                        st.caption(f"R{i}: ${r:,.4f}")
            
            # Legende
            st.caption("🔥=Sehr stark (PDH/PDL) | 💪=Stark (Multi-Touch) | ✓=OK")
            
            # Previous Day Levels separat anzeigen
            if fib_info.get("prev_day_high"):
                st.divider()
                st.caption("**📅 Previous Day Levels**")
                col_pd1, col_pd2, col_pd3 = st.columns(3)
                with col_pd1:
                    st.metric("PDH", f"${fib_info['prev_day_high']:,.4f}")
                with col_pd2:
                    st.metric("PDL", f"${fib_info['prev_day_low']:,.4f}")
                with col_pd3:
                    if fib_info.get("prev_day_close"):
                        st.metric("PDC", f"${fib_info['prev_day_close']:,.4f}")
            
            # Konsolidierungszonen anzeigen
            if fib_info.get("consolidation_zones"):
                st.markdown("**🟣 Konsolidierungszonen** (High Activity)")
                for i, zone in enumerate(fib_info["consolidation_zones"], 1):
                    st.caption(f"Zone {i}: ${zone['low']:,.4f} - ${zone['high']:,.4f} ({zone['days']} Kerzen, {zone['pct_time']}%)")
            
            # Fibonacci Zusatz-Info anzeigen
            if fib_info:
                with st.expander("📊 Fibonacci Details"):
                    fi = fib_info
                    if fi.get("period_high"):
                        st.caption(f"Periode High: ${fi['period_high']:,.4f}")
                        st.caption(f"Periode Low: ${fi['period_low']:,.4f}")
                        st.caption(f"---")
                        st.caption(f"Fib 23.6%: ${fi.get('fib_236', 0):,.4f}")
                        st.caption(f"Fib 38.2%: ${fi.get('fib_382', 0):,.4f}")
                        st.caption(f"Fib 50.0%: ${fi.get('fib_500', 0):,.4f}")
                        st.caption(f"Fib 61.8%: ${fi.get('fib_618', 0):,.4f}")
                        st.caption(f"Fib 78.6%: ${fi.get('fib_786', 0):,.4f}")
            
            # =========================================
            # AKKUMULATIONS-ANALYSE (Wyckoff)
            # =========================================
            with st.expander("📦 Akkumulations-Analyse (Wyckoff)", expanded=False):
                try:
                    ticker = st.session_state.selected_symbol
                    # FullTicker für internationale Aktien
                    if "current_data" in st.session_state:
                        ticker = st.session_state.current_data.get("FullTicker", ticker)
                    m_type = st.session_state.market_type
                    
                    # Polygon Key für Aktien
                    poly_key = None
                    if m_type == "Aktien":
                        try:
                            poly_key = st.secrets["POLYGON_KEY"]
                        except Exception as e:
                            pass
                    
                    display, analysis = get_accumulation_display(ticker, m_type, poly_key)
                    
                    if display:
                        # Score Header
                        st.markdown(f"### {display.get('score_color', '')} Akkumulations-Score: **{display.get('score', 0)}/100** ({display.get('score_label', '')})")
                        
                        st.divider()
                        
                        # Metriken in Spalten
                        col_a1, col_a2, col_a3 = st.columns(3)
                        
                        with col_a1:
                            range_icon = "✅" if display['range_pct'] < 15 else "⚠️" if display['range_pct'] < 25 else "❌"
                            st.metric(
                                f"{range_icon} Range (20T)",
                                f"{display['range_pct']:.1f}%",
                                help="Je enger die Range, desto besser für Akkumulation"
                            )
                        
                        with col_a2:
                            st.metric(
                                f"{display.get('obv_icon', '')} OBV-Trend",
                                f"{display.get('obv_trend', ''):+.1f}%",
                                help="Steigendes OBV bei flachem Preis = Smart Money akkumuliert"
                            )
                        
                        with col_a3:
                            vol_icon = "✅" if display['volume_trend'] < -10 else "⚪"
                            st.metric(
                                f"{vol_icon} Vol-Trend",
                                f"{display['volume_trend']:+.1f}%",
                                help="Abnehmendes Volumen = Konsolidierung (gut)"
                            )
                        
                        col_b1, col_b2, col_b3 = st.columns(3)
                        
                        with col_b1:
                            pos_icon = "🟢" if display['position'] < 0.4 else "🟡" if display['position'] < 0.6 else "🔴"
                            st.metric(
                                f"{pos_icon} Pos. in Range",
                                f"{display['position']:.0%}",
                                help="Nahe Support (0%) = besserer Entry"
                            )
                        
                        with col_b2:
                            days_icon = "✅" if display.get('days_in_range', 0) >= 10 else "⚪"
                            st.metric(
                                f"{days_icon} Tage in Range",
                                f"{display.get('days_in_range', 0)}",
                                help="Längere Akkumulation = mehr Smart Money"
                            )
                        
                        with col_b3:
                            st.metric(
                                "📊 Wyckoff Phase",
                                display['wyckoff_phase'].split('(')[0].strip(),
                                help="Phase C = Idealer Entry, Phase B = Warten"
                            )
                        
                        st.divider()
                        
                        # Interpretation
                        st.markdown(f"**Interpretation:** {display.get('interpretation', '')}")
                        
                        # OBV Details
                        st.caption(f"OBV Status: {display.get('obv_text', '')}")
                        
                        # Empfehlung basierend auf Score
                        if display.get('score', 0) >= 75:
                            st.success("🎯 **BREAKOUT WATCH!** Dieses Asset zeigt starke Akkumulations-Signale. Beobachte für Ausbruch mit Volumen!")
                        elif display.get('score', 0) >= 55:
                            st.info("👀 **BEOBACHTEN:** Gute Akkumulations-Tendenzen. Warte auf besseren Entry oder Volumen-Signal.")
                        elif display.get('score', 0) >= 35:
                            st.warning("⏳ **NEUTRAL:** Noch keine klare Akkumulation. Weiter beobachten.")
                        else:
                            st.error("⚠️ **VORSICHT:** Schwache Akkumulations-Signale. Möglicherweise Distribution!")
                        
                    else:
                        st.warning(f"Nicht genug Daten für Akkumulations-Analyse")
                        st.caption(analysis.get("interpretation", ""))
                        
                except Exception as e:
                    st.error(f"Akkumulations-Analyse Fehler: {e}")
            
            # TradingView Tipp (nur wenn VP Engine NICHT aktiv oder keine VP-Daten)
            _full_t = st.session_state.current_data.get("FullTicker", "") if "current_data" in st.session_state else ""
            _has_vp = False
            try:
                if VP_AVAILABLE and "VP_Summary" in df.columns:
                    _has_vp = row.get("VP_Summary") not in (None, "N/A", "")
            except Exception:
                pass
            if not _has_vp and not any(_full_t.upper().endswith(s) for s in (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")):
                st.info("💡 **Tipp:** Aktiviere im TradingView Chart den 'Volume Profile' Indikator für echte Volume-Daten")
        
        # =====================================================
        # CHART PATTERN WARNUNG — Umkehr-Patterns erkennen (Daily, 90 Tage)
        # =====================================================
        _pattern_ticker = st.session_state.get("selected_symbol", "")
        _pattern_direction = "long"  # Default
        _cur_strat = st.session_state.get("current_strategy", "")
        if any(kw in _cur_strat.lower() for kw in ["short", "distribution", "⬇️", "selling"]):
            _pattern_direction = "short"

        if st.session_state.market_type == "Aktien" and _pattern_ticker:
            try:
                _poly_key = st.secrets.get("POLYGON_KEY", "")
                if _poly_key:
                    # Cache Pattern-Ergebnis in session_state um wiederholte API-Calls zu vermeiden
                    _pat_cache_key = f"_pattern_cache_{_pattern_ticker}"
                    _pat_cached = st.session_state.get(_pat_cache_key)
                    if _pat_cached and _pat_cached.get("ticker") == _pattern_ticker:
                        _pat_warnings = _pat_cached.get("warnings", [])
                    else:
                        _pat_end = datetime.now()
                        _pat_start = _pat_end - timedelta(days=130)
                        _pat_url = f"https://api.polygon.io/v2/aggs/ticker/{_pattern_ticker}/range/1/day/{_pat_start.strftime('%Y-%m-%d')}/{_pat_end.strftime('%Y-%m-%d')}"
                        _pat_resp = requests.get(_pat_url, params={"adjusted": "true", "sort": "asc", "apiKey": _poly_key}, timeout=10)
                        _pat_warnings = []
                        if _pat_resp.status_code == 200:
                            _pat_raw = _pat_resp.json().get("results", [])
                            if _pat_raw and len(_pat_raw) >= 30:
                                _pat_bars = [{"date": datetime.fromtimestamp(b["t"]/1000).strftime("%Y-%m-%d"),
                                              "open": b["o"], "high": b["h"], "low": b["l"],
                                              "close": b["c"], "volume": b["v"]} for b in _pat_raw]
                                _pat_warnings = _detect_chart_patterns(_pat_bars, direction=_pattern_direction)
                        st.session_state[_pat_cache_key] = {"ticker": _pattern_ticker, "warnings": _pat_warnings}

                    # Anzeige
                    if _pat_warnings:
                        for _pw in _pat_warnings:
                            _sev = _pw.get("severity", "info")
                            _pat_text = f"**{_pw['pattern']}** — {_pw['description']}"
                            if _sev == "high":
                                st.error(_pat_text)
                            elif _sev == "medium":
                                st.warning(_pat_text)
                            else:
                                st.info(_pat_text)
            except Exception:
                pass  # Pattern-Check ist optional — nie die Haupt-UI blockieren

        # =====================================================
        # CHART RENDERING
        # =====================================================
        # Internationale Aktien: Eigener Lightweight-Chart (Yahoo Finance)
        # TradingView Widget unterstützt kein Intraday für EU-Börsen
        _full_ticker = st.session_state.current_data.get("FullTicker", st.session_state.selected_symbol) if "current_data" in st.session_state else st.session_state.selected_symbol
        _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
        _is_international = any(_full_ticker.upper().endswith(s) for s in _intl_suffixes)
        
        if _is_international:
            # Eigener Chart via Yahoo Finance — alle Timeframes funktionieren
            with st.spinner(f"📥 Lade {_full_ticker} Chart ({selected_tf})..."):
                _chart_ohlcv = _fetch_ohlcv_yahoo(_full_ticker, selected_tf)
            
            if _chart_ohlcv and len(_chart_ohlcv) > 10:
                # S/R Levels für Chart-Overlay
                _sr_for_chart = None
                if st.session_state.sr_levels["support"] or st.session_state.sr_levels["resistance"]:
                    _sr_for_chart = st.session_state.sr_levels
                
                _chart_html = create_lightweight_chart_html(
                    ohlcv_data=_chart_ohlcv,
                    ticker=_full_ticker,
                    sr_levels=_sr_for_chart,
                    patterns=None,
                    fib_levels=None,
                    ema_periods=[20, 50, 200],
                    height=400,
                    show_volume=True
                )
                import streamlit.components.v1 as components
                components.html(_chart_html, height=420)
                st.caption(f"📊 {len(_chart_ohlcv)} Bars | Yahoo Finance | {selected_tf}")
            else:
                st.warning(f"⚠️ Keine Chart-Daten für {_full_ticker} ({selected_tf})")
        else:
            # US-Aktien, Krypto, Forex, Futures: TradingView Widget
            if st.session_state.market_type == "Krypto":
                tv_symbol = f"BINANCE:{get_binance_tradingview_symbol(st.session_state.selected_symbol)}"
            elif st.session_state.market_type == "Forex":
                tv_symbol = f"FX:{st.session_state.selected_symbol.replace('/', '')}"
            elif st.session_state.market_type == "Futures":
                futures_tv_map = {
                    "ES": "CME_MINI:ES1!", "NQ": "CME_MINI:NQ1!", "YM": "CBOT_MINI:YM1!",
                    "RTY": "CME_MINI:RTY1!", "CL": "NYMEX:CL1!", "GC": "COMEX:GC1!",
                    "SI": "COMEX:SI1!", "NG": "NYMEX:NG1!", "ZB": "CBOT:ZB1!",
                    "ZN": "CBOT:ZN1!", "ZC": "CBOT:ZC1!", "ZS": "CBOT:ZS1!",
                    "ZW": "CBOT:ZW1!", "HG": "COMEX:HG1!", "PL": "NYMEX:PL1!",
                    "KC": "ICEUSA:KC1!", "CT": "ICEUSA:CT1!", "SB": "ICEUSA:SB1!",
                }
                tv_symbol = futures_tv_map.get(st.session_state.selected_symbol, st.session_state.selected_symbol)
            else:
                tv_symbol = st.session_state.selected_symbol
            
            tv_html = f'''
            <div style="height:420px; border-radius: 8px; overflow: hidden;">
                <div id="tv_chart" style="height:100%"></div>
                <script src="https://s3.tradingview.com/tv.js"></script>
                <script>
                    new TradingView.widget({{
                        "autosize": true,
                        "symbol": "{tv_symbol}",
                        "interval": "{tv_interval}",
                        "timezone": "Europe/Berlin",
                        "theme": "dark",
                        "style": "1",
                        "locale": "de_DE",
                        "enable_publishing": false,
                        "hide_side_toolbar": false,
                        "allow_symbol_change": true,
                        "studies": ["Volume@tv-basicstudies"],
                        "container_id": "tv_chart"
                    }});
                </script>
            </div>
            '''
            st.components.v1.html(tv_html, height=420)

# =============================================================================
# ORB SCANNER — Opening Range Breakout (im Scanner Tab, nach Hauptbereich)
# =============================================================================
    st.divider()
    st.header("🔔 Opening Range Breakout (ORB)")
    st.caption("Automatischer Scan der 15-Min Opening Range | Breakout-Detection mit Scoring")

    try:
        _orb_poly_key = st.secrets["POLYGON_KEY"]
    except KeyError:
        _orb_poly_key = None

    if _orb_poly_key:
        # Auto-Scan Button + Status
        _orb_col1, _orb_col2 = st.columns([3, 1])
        with _orb_col2:
            _orb_refresh = st.button("🔄 ORB Refresh", key="orb_refresh_btn")
        with _orb_col1:
            pass

        if _orb_refresh:
            # Fix #9: Nur ORB-Cache leeren, nicht alle Caches
            fetch_orb_scanner.clear()

        with st.spinner("🔍 Scanne Opening Range Breakouts..."):
            orb_data = fetch_orb_scanner(_orb_poly_key)

        phase = orb_data.get("or_phase", "closed")

        # ── Phase-basierte Anzeige ──
        if phase == "weekend":
            st.info("📅 Wochenende — ORB Scanner ist Mo-Fr 9:45-11:00 ET aktiv")
        elif phase == "holiday":
            st.info("🏛️ US-Feiertag — NYSE geschlossen, kein ORB-Scan heute")
        elif phase == "pre_open":
            st.info(f"⏳ **Pre-Market** ({orb_data.get('market_time', '')}) — ORB Scanner startet automatisch um 9:45 ET (15:45 CET)")
        elif phase == "building":
            st.warning(f"🔨 **Opening Range baut sich auf** — {orb_data.get('or_end_time', '')} ({orb_data.get('market_time', '')})")
            # Fix #11: Progress-Bar Parsing absichern
            try:
                _or_end_str = orb_data.get('or_end_time', '15')
                _mins_left = int(str(_or_end_str).split()[0]) if _or_end_str else 15
                st.progress(min(1.0, max(0.0, (15 - _mins_left) / 15)))
            except (ValueError, TypeError, IndexError):
                st.progress(0.0)
        elif phase == "expired":
            st.info(f"⏰ **ORB-Fenster abgelaufen** ({orb_data.get('market_time', '')}) — ORB-Breakouts nach 11:00 ET verlieren Edge")
        elif phase == "active":
            # ── Statistik ──
            stats = orb_data["stats"]
            _s1, _s2, _s3, _s4 = st.columns(4)
            _s1.metric("Gescannt", f"{stats['scanned']:,}")
            _s2.metric("Kandidaten", stats["candidates"])
            _s3.metric("Breakouts", stats["breakouts"])
            _s4.metric("Zeit", orb_data["market_time"])

            breakouts = orb_data.get("breakouts", [])
            candidates_in_range = orb_data.get("candidates", [])

            if breakouts:
                st.subheader(f"🚀 {len(breakouts)} Aktive Breakouts")

                for bo in breakouts:
                    direction_emoji = "🟢 LONG" if bo.get("direction", "") == "LONG" else "🔴 SHORT"
                    with st.expander(
                        f"{bo.get('emoji', '')} **{bo['ticker']}** — {direction_emoji} | "
                        f"Score: {bo['score']}/100 ({bo['rating']}) | "
                        f"${bo.get('current', 0)} ({bo.get('breakout_pct', 0):+.1f}% über OR)",
                        expanded=(bo["score"] >= 60)
                    ):
                        # Hauptinfos
                        c1, c2, c3, c4, c5 = st.columns(5)
                        c1.metric("Kurs", f"${bo.get('current', 0)}")
                        c2.metric("Gap", f"{bo['gap_pct']:+.1f}%")
                        c3.metric("RVOL", f"{bo['rvol']:.1f}x")
                        c4.metric("OR Range", f"{bo['or_range_pct']:.1f}%")
                        c5.metric("R:R", f"{bo['rr']}:1")

                        # OR Levels
                        st.markdown(f"""
**Opening Range:** ${bo['or_low']} — ${bo['or_high']} | **VWAP:** ${bo['vwap']}

| Level | Preis |
|-------|-------|
| 🎯 Target (Measured Move) | **${bo['target']}** |
| ➡️ Entry (OR Break) | ${bo.get('entry', 0)} |
| 🛑 Stop (OR {'Low' if bo.get('direction', 'LONG') == 'LONG' else 'High'}) | ${bo['stop']} |
""")

                        # Fakeout/Bestätigung Warnung
                        if bo.get("fakeout"):
                            st.error("⛔ **FAKEOUT** — Preis ist nach Breakout zurück in OR gefallen!")
                        elif not bo.get("confirmed", True):
                            st.warning("⚠️ Unbestätigt — nur 1 Bar über OR, auf Bestätigung warten")

                        # Scoring Faktoren
                        st.markdown("**Faktoren:** " + " | ".join(bo["factors"]))

                        # TradingView Link
                        st.markdown(f"[📈 {bo['ticker']} auf TradingView](https://www.tradingview.com/chart/?symbol={bo['ticker']}&interval=5)")

            elif candidates_in_range:
                st.info(f"⏳ {len(candidates_in_range)} Stocks in der Opening Range — noch kein Breakout")
                # Kompakte Tabelle der Kandidaten
                import pandas as pd
                df_cand = pd.DataFrame(candidates_in_range)
                if not df_cand.empty and "ticker" in df_cand.columns:
                    display_cols = ["ticker", "gap_pct", "rvol", "or_high", "or_low", "or_range_pct"]
                    display_cols = [c for c in display_cols if c in df_cand.columns]
                    st.dataframe(
                        df_cand[display_cols].rename(columns={
                            "ticker": "Ticker", "gap_pct": "Gap%", "rvol": "RVOL",
                            "or_high": "OR High", "or_low": "OR Low", "or_range_pct": "OR Range%"
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
            else:
                st.warning("Keine ORB-Kandidaten gefunden (zu wenig Gapper/Volume heute)")
        else:
            st.info(f"📊 ORB Scanner — Markt geschlossen ({orb_data.get('market_time', '')})")
    else:
        st.warning("⚠️ POLYGON_KEY fehlt — ORB Scanner braucht Polygon API")

# =============================================================================
# BI SCANNER TAB — Breakout Imminent mit Auto-Scan + Einstellungen
# =============================================================================
with tab_bi:
    st.header("🔮 Breakout Imminent Scanner")
    st.caption("Automatischer Hintergrund-Scan für Breakout-Imminent Setups | 20-Signal Composite Scoring")

    # ── Config laden ──
    _bi_cfg = _bi_config_load()

    # ── Einstellungen (Expander) ──
    with st.expander("⚙️ Einstellungen", expanded=False):
        bi_set_col1, bi_set_col2 = st.columns(2)
        with bi_set_col1:
            # BI Scanner = immer Long (Short → Bear Scanner Tab)
            st.session_state["bi_tab_direction"] = "long"
            st.info("📈 **Long Only** — Für Short-Setups → 🐻 Bear Scanner Tab")
        with bi_set_col2:
            bi_tab_threshold = st.number_input(
                "Score Threshold",
                min_value=50, max_value=150, value=_bi_cfg.get("threshold", 85),
                step=5, help="Minimum BI Score für Treffer (Standard: 85 Long, 80 Short)",
                key="bi_tab_threshold_input"
            )
            st.session_state["bi_tab_threshold"] = bi_tab_threshold

        st.divider()
        st.subheader("⏰ Auto-Scan Zeiten (CET)")
        bi_time_col1, bi_time_col2, bi_time_col3 = st.columns(3)
        with bi_time_col1:
            bi_scan1_h = st.number_input("Scan 1 — Stunde", min_value=9, max_value=22, value=_bi_cfg.get("scan1_h", 15), key="bi_s1h")
            bi_scan1_m = st.number_input("Scan 1 — Minute", min_value=0, max_value=59, value=_bi_cfg.get("scan1_m", 45), key="bi_s1m")
            st.session_state["bi_scan1_h"] = bi_scan1_h
            st.session_state["bi_scan1_m"] = bi_scan1_m
        with bi_time_col2:
            bi_scan2_h = st.number_input("Scan 2 — Stunde", min_value=9, max_value=22, value=_bi_cfg.get("scan2_h", 18), key="bi_s2h")
            bi_scan2_m = st.number_input("Scan 2 — Minute", min_value=0, max_value=59, value=_bi_cfg.get("scan2_m", 30), key="bi_s2m")
            st.session_state["bi_scan2_h"] = bi_scan2_h
            st.session_state["bi_scan2_m"] = bi_scan2_m
        with bi_time_col3:
            bi_auto_enabled = st.toggle("Auto-Scan aktiv", value=_bi_cfg.get("auto_enabled", False), key="bi_auto_toggle")
            st.session_state["bi_auto_enabled"] = bi_auto_enabled
            bi_cache_ttl_h = st.number_input("Cache TTL (Std)", min_value=1, max_value=6, value=_bi_cfg.get("cache_ttl_h", 2), key="bi_ttl")
            st.session_state["bi_cache_ttl_h"] = bi_cache_ttl_h
            st.divider()
            bi_crash_mode = st.toggle("🔴 **Crash Mode**", value=st.session_state.get("bi_crash_mode", False), key="bi_crash_toggle",
                                       help="Aktiviert: Scannt nach überkauften, volatilen Aktien die bei einem Crash am stärksten fallen. "
                                            "Filter: Change >3%, RVOL >1.5 (statt ruhige Aktien)")
            st.session_state["bi_crash_mode"] = bi_crash_mode
            if bi_crash_mode:
                st.warning("🔴 **Crash Mode aktiv** — Sucht Aktien die bei Sell-Off am verwundbarsten sind: "
                           "hoher Recent-Move, hohes Volumen, nahe Highs → maximaler Downside bei Panik")

        st.caption(f"📋 Scan 1: **{bi_scan1_h:02d}:{bi_scan1_m:02d}** CET | Scan 2: **{bi_scan2_h:02d}:{bi_scan2_m:02d}** CET | TTL: {bi_cache_ttl_h}h | Auto: {'✅' if bi_auto_enabled else '❌'}")

        # ── Config speichern wenn geändert ──
        _bi_new_cfg = {
            "direction": "long",
            "threshold": bi_tab_threshold,
            "auto_enabled": bi_auto_enabled,
            "scan1_h": bi_scan1_h,
            "scan1_m": bi_scan1_m,
            "scan2_h": bi_scan2_h,
            "scan2_m": bi_scan2_m,
            "cache_ttl_h": bi_cache_ttl_h,
        }
        if _bi_new_cfg != _bi_cfg:
            _bi_config_save(_bi_new_cfg)

    # ── Status + Ergebnisse ──
    st.divider()

    bi_dir = st.session_state.get("bi_tab_direction", "long")
    bi_dir_emoji = "⬆️" if bi_dir == "long" else "⬇️"
    bi_dir_label = "Long" if bi_dir == "long" else "Short"
    cache_ttl_min = max(720, st.session_state.get("bi_cache_ttl_h", 12) * 60)  # Min 12h (bg_service scannt 3x/Tag)

    # Cache + Status laden
    bi_cached_results, bi_cached_ts, bi_cache_age = _bi_cache_load(bi_dir)
    bi_cache_ok = bi_cached_results is not None and bi_cache_age is not None and bi_cache_age < cache_ttl_min
    bi_running = _bi_scan_is_running(bi_dir)
    bi_progress = _bi_progress_read(bi_dir)

    # ── Auto-Scan Logik ──
    from zoneinfo import ZoneInfo
    bi_now_cet = datetime.now(ZoneInfo("Europe/Berlin"))
    bi_now_hm = bi_now_cet.hour * 60 + bi_now_cet.minute
    # Extended Hours: Pre-Market ab 10:00 CET (4:00 ET) bis After-Hours 23:00 CET (17:00 ET)
    # BI Scanner analysiert Daily-Kerzen → funktioniert auch außerhalb Regular Hours
    bi_market_open = 10 * 60       # 10:00 CET (Pre-Market US)
    bi_market_close = 23 * 60      # 23:00 CET (After-Hours US)
    bi_is_market = bi_market_open <= bi_now_hm <= bi_market_close and bi_now_cet.weekday() < 5
    bi_auto_on = st.session_state.get("bi_auto_enabled", False)

    bi_should_auto = False
    bi_auto_reason = ""
    # Cooldown: Nach Fehler/leeren Ergebnissen 5 Minuten warten bevor Auto-Restart
    _bi_error_cooldown = (bi_progress and bi_progress.get("status") in ("error", "no_candidates")
                          and time.time() - bi_progress.get("timestamp", 0) < 300)
    # Auto-Scan NUR zu den programmierten Zeiten — NICHT bei jedem Seitenaufruf
    if bi_auto_on and bi_is_market and not bi_cache_ok and not bi_running and not _bi_error_cooldown:
        s1 = st.session_state.get("bi_scan1_h", 15) * 60 + st.session_state.get("bi_scan1_m", 45)
        s2 = st.session_state.get("bi_scan2_h", 18) * 60 + st.session_state.get("bi_scan2_m", 30)
        for wmin, wname in [(s1, "Scan 1"), (s2, "Scan 2")]:
            if abs(bi_now_hm - wmin) <= 15:
                bi_should_auto = True
                bi_auto_reason = f"Auto-{wname} ({wmin // 60:02d}:{wmin % 60:02d} CET)"
                break

    # ── FALL 1: Scan läuft ──
    if bi_running and bi_progress:
        p_c = bi_progress.get("checked", 0)
        p_t = bi_progress.get("total", 0)
        p_h = bi_progress.get("hits", 0)
        p_top = bi_progress.get("top_score", 0)
        p_detail = bi_progress.get("detail", "")

        # Status-Text: Phase 1 (Snapshot laden) vs Phase 2 (Analyse)
        if p_t == 0:
            st.info(f"🔮 **BI Scan {bi_dir_label} {bi_dir_emoji}** — {p_detail or '📡 Lade Aktien-Snapshot...'}")
        else:
            pct = round(p_c / max(1, p_t) * 100)
            est = max(1, (p_t - p_c) // 75)
            st.info(f"🔮 **BI Scan {bi_dir_label} {bi_dir_emoji} läuft** — {p_c}/{p_t} ({pct}%) | {p_h} Treffer | Top: {p_top} | ~{est} Min")

        # Progress-Bar + Stop-Button in einer Zeile
        _bi_prog_col1, _bi_prog_col2 = st.columns([5, 1])
        with _bi_prog_col1:
            _bi_pct_val = min(1.0, p_c / max(1, p_t)) if p_t > 0 else 0.0
            st.progress(_bi_pct_val)
        with _bi_prog_col2:
            if st.button("⏹️ Stop", key=f"bi_stop_btn_{bi_dir}", use_container_width=True, type="secondary"):
                _bi_request_stop(bi_dir)
                # Auch direkt Progress auf "stopped" setzen für sofortiges Feedback
                _bi_progress_write(bi_dir, "stopped", checked=p_c, total=p_t, hits=p_h,
                                   detail=f"⏹️ Manuell gestoppt bei {p_c}/{p_t}")
                st.toast("⏹️ BI Scan wird gestoppt...")
                time.sleep(1)
                st.rerun()

        st.caption("💡 Andere Tabs normal benutzen — Scan läuft im Hintergrund!")

        # Progress-Update: nutze globalen Sidebar Auto-Refresh statt eigenem
        # (eigener st_autorefresh sabotiert andere Tabs)

        # Alten Cache laden während Scan läuft
        if bi_cache_ok:
            try:
                cache_t = datetime.fromtimestamp(bi_cached_ts).strftime("%H:%M") if bi_cached_ts else "?"
            except (ValueError, TypeError, OSError):
                cache_t = "?"
            if st.button(f"⚡ Vorherige Ergebnisse laden ({len(bi_cached_results)} Treffer von {cache_t})", use_container_width=True, key="bi_tab_load_while_running"):
                st.session_state.bi_tab_results = bi_cached_results

    # ── FALL 2: Scan fertig ──
    elif bi_progress and bi_progress.get("status") == "done":
        fresh, _, _ = _bi_cache_load(bi_dir)
        if fresh is not None:
            st.session_state.bi_tab_results = fresh
            st.success(f"✅ **BI Scan {bi_dir_label} fertig!** {len(fresh)} Treffer — automatisch geladen")
            st.caption(f"🔍 {bi_progress.get('detail', '')}")
        else:
            st.warning("⚠️ Scan fertig — keine Treffer")
        _bi_progress_clear(bi_dir)

    # ── FALL 2b: Scan manuell gestoppt ──
    elif bi_progress and bi_progress.get("status") == "stopped":
        _bp_hits = bi_progress.get("hits", 0)
        _bp_checked = bi_progress.get("checked", 0)
        _bp_total = bi_progress.get("total", 0)
        st.warning(f"⏹️ BI Scan gestoppt bei {_bp_checked}/{_bp_total} — {_bp_hits} Treffer gespeichert")
        # Ergebnisse aus Cache laden falls vorhanden
        if _bp_hits > 0:
            fresh, _, _ = _bi_cache_load(bi_dir)
            if fresh is not None:
                st.session_state.bi_tab_results = fresh
            else:
                # Thread hat Cache evtl. noch nicht geschrieben — kurz warten + retry
                _stop_age = time.time() - bi_progress.get("timestamp", 0)
                if _stop_age < 5:
                    time.sleep(2)
                    st.rerun()
        # Stop-Signal + Progress aufräumen nach 15 Sek
        _stop_age = time.time() - bi_progress.get("timestamp", 0)
        if _stop_age > 15:
            _bi_clear_stop(bi_dir)
            try:
                os.remove(_bi_progress_path(bi_dir))
            except Exception:
                pass

    # ── FALL 3a: Keine Kandidaten (kein Error, aber auch keine Ergebnisse) ──
    elif bi_progress and bi_progress.get("status") == "no_candidates":
        _nc_age = time.time() - bi_progress.get("timestamp", 0)
        st.info(f"📭 {bi_progress.get('detail', 'Keine Kandidaten')} — Nächster Auto-Scan in {max(0, 5 - int(_nc_age / 60))} Min")
        if st.button(f"🔄 Erneut scannen {bi_dir_emoji}", use_container_width=True, key="bi_tab_retry_nc"):
            _bi_progress_write(bi_dir, status="idle")
            st.rerun()

    # ── FALL 3b: Fehler ──
    elif bi_progress and bi_progress.get("status") == "error":
        _err_age = time.time() - bi_progress.get("timestamp", 0)
        st.error(f"❌ BI Scan Fehler: {bi_progress.get('detail', 'Unbekannt')} — Retry in {max(0, 5 - int(_err_age / 60))} Min")
        # Button trotzdem anzeigen damit User manuell neu starten kann
        if st.button(f"🔄 Erneut scannen {bi_dir_emoji}", use_container_width=True, type="primary", key="bi_tab_retry_scan"):
            _bi_progress_write(bi_dir, status="idle")  # Reset Error-Status
            st.rerun()

    # ── FALL 4: Kein Scan aktiv ──
    else:
        if bi_cache_ok:
            try:
                cache_t = datetime.fromtimestamp(bi_cached_ts).strftime("%H:%M") if bi_cached_ts else "?"
            except (ValueError, TypeError, OSError):
                cache_t = "?"
            st.success(f"⚡ **Cache {bi_dir_label}:** {len(bi_cached_results)} Treffer von {cache_t} ({_bi_cache_age_str(bi_cache_age)})")
            # Auto-Load
            if not st.session_state.get("bi_tab_results"):
                st.session_state.bi_tab_results = bi_cached_results

        if bi_should_auto:
            st.info(f"🤖 **{bi_auto_reason}** — Scan startet automatisch...")

    # ── Scan Button — IMMER sichtbar wenn kein Scan läuft ──
    if not bi_running:
        bi_btn_col1, bi_btn_col2 = st.columns(2)
        with bi_btn_col1:
            bi_manual_scan = st.button(f"🚀 Scan starten {bi_dir_emoji}", use_container_width=True, type="primary", key="bi_tab_manual_scan")
        with bi_btn_col2:
            if bi_cache_ok:
                if st.button(f"⚡ Cache laden ({len(bi_cached_results)})", use_container_width=True, key="bi_tab_load_cache"):
                    st.session_state.bi_tab_results = bi_cached_results
                    st.rerun()

        # Zeitinfo
        if bi_auto_on and bi_is_market:
            s1 = st.session_state.get("bi_scan1_h", 15) * 60 + st.session_state.get("bi_scan1_m", 45)
            s2 = st.session_state.get("bi_scan2_h", 18) * 60 + st.session_state.get("bi_scan2_m", 30)
            st.caption(f"⏰ Auto-Scan: {s1//60:02d}:{s1%60:02d} + {s2//60:02d}:{s2%60:02d} CET | Nächster wenn Cache >{cache_ttl_min//60}h alt")
        elif not bi_is_market:
            st.caption("⏰ Markt geschlossen — Auto-Scan pausiert (Mo-Fr 10:00-23:00 CET inkl. Pre/After)")

    # --- Scan starten (Auto oder Manuell) ---
    if not bi_running and (bi_should_auto or bi_manual_scan):
            try:
                poly_key = st.secrets["POLYGON_KEY"]
                reason = bi_auto_reason if bi_should_auto else "Manuell gestartet"

                # Fix: fetch_stock_data im Background-Thread starten statt Main-Thread zu blockieren
                # Vorher blockierte st.spinner() den GESAMTEN Script-Run → alle Tabs danach leer
                # WICHTIG: crash_mode VOR Thread-Start lesen (session_state nicht thread-safe!)
                _bi_crash_mode_val = st.session_state.get("bi_crash_mode", False)

                def _bi_fetch_and_scan(pk, direction, is_crash=False):
                    try:
                        _bi_progress_write(direction, status="running", checked=0, total=0, hits=0,
                                           detail="📡 Lade Aktien-Snapshot von Polygon...")

                        # Direkt Polygon API aufrufen (fetch_stock_data hat st.error/st.session_state
                        # die im Background-Thread crashen/hängen können)
                        import requests as _req
                        try:
                            _snap_resp = _req.get(
                                "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                                params={"apiKey": pk}, timeout=30)
                        except _req.exceptions.Timeout:
                            _bi_progress_write(direction, status="error",
                                detail="Polygon API Timeout (30s) — nicht erreichbar")
                            return
                        except Exception as _net_err:
                            _bi_progress_write(direction, status="error",
                                detail=f"Netzwerk-Fehler: {_net_err}")
                            return

                        if _snap_resp.status_code != 200:
                            _bi_progress_write(direction, status="error",
                                detail=f"Polygon HTTP {_snap_resp.status_code}: {_snap_resp.text[:150]}")
                            return

                        _tickers = _snap_resp.json().get("tickers", [])
                        if not _tickers:
                            _bi_progress_write(direction, status="error",
                                detail="Polygon Snapshot leer — 0 Tickers zurückgegeben")
                            return

                        _bi_progress_write(direction, status="running", checked=0, total=0, hits=0,
                                           detail=f"📊 {len(_tickers)} Aktien geladen, filtere...")

                        # Basis-Daten extrahieren — nutze ALLE verfügbaren Polygon-Felder
                        # Preis-Kaskade: lastTrade.p > day.c > day.vw > prevDay.c > prevDay.vw > min.c
                        raw = []
                        _skip_no_price = 0
                        _skip_negative = 0
                        for t in _tickers:
                            try:
                                _lt = t.get("lastTrade", {}) or {}
                                _day = t.get("day", {}) or {}
                                _prev = t.get("prevDay", {}) or {}
                                _min = t.get("min", {}) or {}
                                _lq = t.get("lastQuote", {}) or {}

                                # Preis-Kaskade: alle möglichen Quellen durchprobieren
                                price = (
                                    _lt.get("p") or
                                    _day.get("c") or
                                    _day.get("vw") or
                                    _min.get("c") or
                                    _min.get("vw") or
                                    _prev.get("c") or
                                    _prev.get("vw") or
                                    # lastQuote: Midpoint aus Bid/Ask
                                    ((_lq.get("P", 0) + _lq.get("p", 0)) / 2 if _lq.get("P") and _lq.get("p") else 0) or
                                    0
                                )
                                if not price or price <= 0:
                                    _skip_no_price += 1
                                    continue

                                # Change%: Polygon-Feld, Fallback manuell berechnen
                                change_pct = t.get("todaysChangePerc") or 0
                                if not change_pct and _prev.get("c") and _prev["c"] > 0:
                                    change_pct = (price - _prev["c"]) / _prev["c"] * 100

                                # Volume + RVOL
                                vol = _day.get("v") or _min.get("av") or 0
                                prev_vol = _prev.get("v") or 0
                                rvol = vol / prev_vol if prev_vol and prev_vol > 0 else 0
                                dollar_vol = price * vol if vol else 0

                                raw.append({
                                    "Ticker": t.get("ticker", ""),
                                    "Name": t.get("name", "") or "",
                                    "Preis": round(price, 2),
                                    "Change%": round(change_pct, 2),
                                    "RVOL": round(rvol, 2),
                                    "Volume": vol,
                                    "DollarVol": dollar_vol,
                                })
                            except Exception:
                                continue

                        if not raw:
                            # Debug: Zeige warum alle rausgefallen sind + Beispiel-Ticker
                            _sample = ""
                            if _tickers:
                                _s = _tickers[0]
                                _sample = (f" | Beispiel: {_s.get('ticker','?')}: "
                                           f"lastTrade={_s.get('lastTrade',{})}, "
                                           f"day.c={(_s.get('day') or {}).get('c')}, "
                                           f"prevDay.c={(_s.get('prevDay') or {}).get('c')}")
                            _bi_progress_write(direction, status="error",
                                detail=f"Keine Aktien nach Basis-Filter ({len(_tickers)} geprüft, "
                                       f"{_skip_no_price} ohne Preis){_sample}")
                            return

                        # ── Common Stock Whitelist laden (statt ETF-Blacklist) ──
                        # Nur echte Aktien (type=CS) → filtert ALLE ETFs, Fonds, Warrants etc.
                        # 3-Stufen Cache: In-Memory → Datei → API (mit Datei-Persist)
                        _bi_progress_write(direction, status="running", checked=0, total=0, hits=0,
                                           detail=f"📋 Lade Common Stock Liste...")
                        _CS_FILE = "/tmp/cs_tickers_cache.json"
                        _cs_set = COMMON_STOCK_TICKERS  # 1) In-Memory

                        if not _cs_set:
                            # 2) Datei-Cache (max 24h alt)
                            try:
                                if os.path.exists(_CS_FILE):
                                    _cs_age = time.time() - os.path.getmtime(_CS_FILE)
                                    if _cs_age < 86400:  # 24h
                                        with open(_CS_FILE, "r") as _cf:
                                            _cs_set = set(json.load(_cf))
                                        print(f"[BI] CS-Liste aus Datei-Cache: {len(_cs_set)} Ticker")
                            except Exception:
                                pass

                        if not _cs_set:
                            # 3) API laden + in Datei speichern
                            try:
                                _cs_set, _ = _load_common_stock_tickers_direct(pk)
                                if _cs_set:
                                    with open(_CS_FILE, "w") as _cf:
                                        json.dump(list(_cs_set), _cf)
                                    print(f"[BI] CS-Liste von API geladen + gecacht: {len(_cs_set)} Ticker")
                            except Exception as _cs_err:
                                print(f"[BI] CS-Liste Fehler: {_cs_err}")
                                _cs_set = set()

                        # Filter: Nur Common Stocks + Basis-Liquidität
                        # SPAC-Ticker Muster: Enden oft auf AC, ACU, ACQU + Warrants/Units
                        _SPAC_TICKER_SUFFIXES = ("ACU", "ACW", "ACQU")
                        def _is_likely_spac(name, ticker):
                            if is_spac(name or ""):
                                return True
                            # Preis genau $10.xx + Ticker endet auf AC = sehr wahrscheinlich SPAC
                            # (Polygon Name kann leer sein)
                            return False

                        if _cs_set:
                            filtered = [s for s in raw if isinstance(s, dict)
                                        and s.get("Ticker", "").upper() in _cs_set
                                        and s.get("Preis", 0) >= 5
                                        and s.get("DollarVol", 0) >= 200_000
                                        and not _is_likely_spac(s.get("Name", ""), s.get("Ticker", ""))]
                            _bi_progress_write(direction, status="running", checked=0, total=0, hits=0,
                                               detail=f"📋 {len(filtered)} Common Stocks (von {len(raw)} gesamt)")
                        else:
                            # Fallback: is_etf_or_etp Blacklist (CS-Liste nicht ladbar)
                            filtered = [s for s in raw if isinstance(s, dict)
                                        and s.get("Preis", 0) >= 5
                                        and s.get("DollarVol", 0) >= 200_000
                                        and not is_etf_or_etp(s.get("Ticker", ""))
                                        and not is_spac(s.get("Name", ""))]
                            _bi_progress_write(direction, status="running", checked=0, total=0, hits=0,
                                               detail=f"⚠️ CS-Liste nicht verfügbar, Blacklist-Filter: {len(filtered)} Kandidaten")
                        if not filtered:
                            _bi_progress_write(direction, status="no_candidates", detail=f"Keine Kandidaten nach Filter ({len(raw)} Aktien geprüft)")
                            return
                        _bi_progress_write(direction, status="running", checked=0, total=len(filtered), hits=0,
                                           detail=f"{len(filtered)} Kandidaten, starte Analyse...")
                        _bi_background_scan(pk, direction, filtered)
                    except Exception as e:
                        _bi_progress_write(direction, status="error", detail=f"Fehler: {e}")

                thread = threading.Thread(target=_bi_fetch_and_scan, args=(poly_key, bi_dir, _bi_crash_mode_val), daemon=True)
                thread.start()
                st.info(f"🚀 **{reason}** — Scan startet im Hintergrund...")
                st.caption("💡 Andere Tabs normal benutzen — Scan läuft im Hintergrund!")
                time.sleep(2)
                st.rerun()
            except KeyError:
                st.error("❌ POLYGON_KEY fehlt in secrets!")
            except Exception as e:
                st.error(f"❌ BI Scanner Fehler: {e}")

    # ── Ergebnis-Tabelle ──
    st.divider()
    bi_tab_data = st.session_state.get("bi_tab_results", None)
    if bi_tab_data and len(bi_tab_data) > 0:
        import pandas as pd
        bi_df = pd.DataFrame(bi_tab_data)
        if "BI_Score" in bi_df.columns:
            bi_df = bi_df.sort_values(by="BI_Score", ascending=False).reset_index(drop=True)

        st.subheader(f"🔮 {len(bi_df)} Treffer — {bi_dir_label} {bi_dir_emoji}")

        # ── Sektor-Trend Übersicht für alle BI-Ergebnisse ──
        try:
            _bi_poly = st.secrets.get("POLYGON_KEY", "")
            if _bi_poly and len(bi_df) > 0:
                _bi_all_tickers = [(str(r.get("Ticker", "")), str(r.get("sic_code", "") or "")) for r in bi_tab_data]
                _render_sector_trend_banner(None, poly_key=_bi_poly, all_tickers=_bi_all_tickers)
        except Exception:
            pass

        # Rename Felder für Display (Code-Namen → lesbare Namen)
        rename_map = {}
        if "BI_Confidence" in bi_df.columns:
            rename_map["BI_Confidence"] = "Konfidenz%"
        if "RiskReward" in bi_df.columns:
            rename_map["RiskReward"] = "R:R"
        if "RangeHigh" in bi_df.columns and "RangeLow" in bi_df.columns:
            bi_df["Breakout_Zone"] = bi_df.apply(
                lambda r: f"${r.get('RangeLow', 0):.2f}-${r.get('RangeHigh', 0):.2f}" if r.get('RangeHigh', 0) < 100
                else f"${r.get('RangeLow', 0):.0f}-${r.get('RangeHigh', 0):.0f}", axis=1)
        if "BI_GradeLabel" in bi_df.columns:
            rename_map["BI_GradeLabel"] = "Grade"
        elif "BI_Grade" in bi_df.columns:
            rename_map["BI_Grade"] = "Grade"
        bi_df = bi_df.rename(columns=rename_map)

        # Display-Spalten (mit korrekten Namen nach Rename)
        display_cols = [c for c in [
            "Ticker", "Preis", "Change%", "BI_Score", "Grade", "Konfidenz%",
            "PatternLabel", "Breakout_Zone", "R:R", "Entry", "StopLoss", "TP1",
            "Range%", "RVOL", "DollarVol"
        ] if c in bi_df.columns]

        # ── Klickbare Tabelle: Zeile anklicken → Detail unten ──
        if "Ticker" in bi_df.columns:
            # Aktuelle Auswahl
            bi_sel_idx = st.session_state.get("bi_sel_idx", 0)
            bi_sel_idx = min(bi_sel_idx, len(bi_df) - 1)

            # Dataframe mit Selektion (on_select)
            if display_cols:
                event = st.dataframe(
                    bi_df[display_cols],
                    use_container_width=True,
                    height=min(800, 40 + len(bi_df) * 35),
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="bi_tab_df_select"
                )

                # Auswahl aus Dataframe-Klick übernehmen
                if event and event.selection and event.selection.rows:
                    bi_sel_idx = event.selection.rows[0]
                    st.session_state["bi_sel_idx"] = bi_sel_idx

            # ── Keyboard Navigation: E (zurück) / W (vor) ──
            nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
            with nav_col1:
                if st.button("◀ Zurück (E)", key="bi_nav_prev", use_container_width=True, disabled=bi_sel_idx <= 0):
                    st.session_state["bi_sel_idx"] = max(0, bi_sel_idx - 1)
                    st.rerun()
            with nav_col2:
                st.caption(f"📌 **{bi_sel_idx + 1} / {len(bi_df)}** — Klicke Zeile in Tabelle oder nutze ◀ ▶ Buttons (Tastatur: **E** / **W**)")
            with nav_col3:
                if st.button("Vor ▶ (W)", key="bi_nav_next", use_container_width=True, disabled=bi_sel_idx >= len(bi_df) - 1):
                    st.session_state["bi_sel_idx"] = min(len(bi_df) - 1, bi_sel_idx + 1)
                    st.rerun()

            # Keyboard shortcuts via JS
            st.components.v1.html(f"""
            <script>
            document.addEventListener('keydown', function(e) {{
                var ae = parent.document.activeElement || e.target || {{}};
                var tag = (ae.tagName||'').toLowerCase();
                if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
                if (ae.isContentEditable || ae.getAttribute && ae.getAttribute('role') === 'textbox') return;
                if ((e.target.tagName||'').toLowerCase() === 'input') return;
                if (e.key === 'e' || e.key === 'E') {{
                    const prevBtn = parent.document.querySelectorAll('button');
                    prevBtn.forEach(b => {{ if (b.textContent.includes('Zurück')) b.click(); }});
                }}
                if (e.key === 'w' || e.key === 'W') {{
                    const prevBtn = parent.document.querySelectorAll('button');
                    prevBtn.forEach(b => {{ if (b.textContent.includes('Vor')) b.click(); }});
                }}
            }});
            </script>
            """, height=0)

            # ── Detail-Ansicht für ausgewählten Ticker ──
            bi_row = bi_df.iloc[bi_sel_idx]
            ticker = bi_row.get("Ticker", "?")
            st.divider()
            st.subheader(f"📌 {ticker}")

            # Sektor-Trend für ausgewählten Ticker
            try:
                _bi_pk = st.secrets.get("POLYGON_KEY", "")
                if _bi_pk:
                    _render_sector_trend_banner(ticker, sic_code=str(bi_row.get("sic_code", "") or ""), poly_key=_bi_pk)
            except Exception:
                pass

            # Options Unusual Activity für BI Scanner
            try:
                _bi_pk2 = st.secrets.get("POLYGON_KEY", "")
                _bi_price = bi_row.get("Preis", bi_row.get("close", 0))
                if _bi_pk2 and _bi_price > 0:
                    _render_options_activity_banner(ticker, _bi_price, _bi_pk2)
            except Exception:
                pass

            d1, d2, d3, d4 = st.columns(4)
            with d1:
                score = bi_row.get("BI_Score", 0)
                max_s = bi_row.get("BI_MaxScore", 200)
                pct_s = round(score / max(1, max_s) * 100)
                st.metric("BI Score", f"{score}/{max_s} ({pct_s}%)")
                st.metric("Grade", bi_row.get("Grade", bi_row.get("BI_Grade", "N/A")))
            with d2:
                _bi_conf = bi_row.get('Konfidenz%', bi_row.get('BI_Confidence', 0))
                st.metric("Konfidenz", f"{float(_bi_conf) if _bi_conf is not None else 0:.0f}%")
                _bi_rr = bi_row.get('R:R', bi_row.get('RiskReward', 0))
                st.metric("R:R", f"{float(_bi_rr) if _bi_rr is not None else 0:.1f}")
            with d3:
                st.metric("Entry", f"${float(bi_row.get('Entry', 0) or 0):.2f}")
                st.metric("Stop Loss", f"${float(bi_row.get('StopLoss', 0) or 0):.2f}")
            with d4:
                st.metric("TP1", f"${float(bi_row.get('TP1', 0) or 0):.2f}")
                st.metric("TP2", f"${float(bi_row.get('TP2', 0) or 0):.2f}")

            # Breakout Zone
            zone_text = bi_row.get("Breakout_Zone", "")
            if not zone_text:
                rh = bi_row.get("RangeHigh", 0)
                rl = bi_row.get("RangeLow", 0)
                zone_text = f"${rl:.2f} — ${rh:.2f}" if rh > 0 else "N/A"
            st.caption(f"🎯 Breakout Zone: **{zone_text}** | Preis: ${bi_row.get('Preis', 0):.2f} | Change: {bi_row.get('Change%', 0):.1f}% | RVOL: {bi_row.get('RVOL', 0):.2f}")

            # ── Chart-Pattern-Warnungen ──
            pattern_warns = bi_row.get("PatternWarnings", [])
            if isinstance(pattern_warns, str):
                try:
                    import json as _json
                    pattern_warns = _json.loads(pattern_warns) if pattern_warns else []
                except Exception:
                    pattern_warns = []
            if pattern_warns and len(pattern_warns) > 0:
                for pw in pattern_warns:
                    sev = pw.get("severity", "info")
                    pat = pw.get("pattern", "")
                    desc = pw.get("description", "")
                    if sev == "high":
                        st.error(f"**{pat}** — {desc}")
                    elif sev == "medium":
                        st.warning(f"**{pat}** — {desc}")
                    else:
                        st.info(f"**{pat}** — {desc}")
            else:
                st.success("✅ **Kein Umkehr-Pattern erkannt** — Chart ist clean")

            # Signal-Details
            details = bi_row.get("BI_Details", "")
            if details:
                with st.expander("🔬 Signal-Details", expanded=False):
                    st.text(details)

            # ── TradingView Chart ──
            st.divider()
            bi_tv_symbol = ticker
            bi_tv_html = f'''
            <div style="height:500px; border-radius: 8px; overflow: hidden;">
                <div id="bi_tv_chart" style="height:100%"></div>
                <script src="https://s3.tradingview.com/tv.js"></script>
                <script>
                    new TradingView.widget({{
                        "autosize": true,
                        "symbol": "{bi_tv_symbol}",
                        "interval": "D",
                        "timezone": "Europe/Berlin",
                        "theme": "dark",
                        "style": "1",
                        "locale": "de_DE",
                        "enable_publishing": false,
                        "hide_side_toolbar": false,
                        "allow_symbol_change": true,
                        "studies": ["Volume@tv-basicstudies", "BB@tv-basicstudies", "RSI@tv-basicstudies"],
                        "container_id": "bi_tv_chart",
                        "range": "3M"
                    }});
                </script>
            </div>
            '''
            st.components.v1.html(bi_tv_html, height=500)
    else:
        st.info("Noch keine BI Ergebnisse. Scan starten oder auf Auto-Scan warten.")


# -----------------------------------------------------------------------------
# 🧬 BIOTECH TAB — FDA Catalyst Scanner
# -----------------------------------------------------------------------------
with tab_biotech:
    st.subheader("🧬 Biotech Scanner — FDA Catalysts & Pipeline Tracker")
    st.caption("Scannt Biotech/Pharma-Aktien nach FDA-Events, Clinical Trial Ergebnissen und Pipeline-Katalysatoren")

    # ── Settings aus Config laden ──
    _bio_cfg = _biotech_config_load()

    # ── Settings Expander ──
    with st.expander("⚙️ Einstellungen", expanded=False):
        _bio_col1, _bio_col2, _bio_col3 = st.columns(3)
        with _bio_col1:
            _bio_min_score = st.slider("Min. Score", min_value=0, max_value=50,
                                        value=_bio_cfg.get("min_score", 20), key="bio_min_score")
            _bio_auto_scan = st.toggle("🔄 Auto-Scan",
                                       value=_bio_cfg.get("auto_scan", False), key="bio_auto_scan",
                                       help="Automatischer Scan im Intervall (nur wenn aktiviert)")
        with _bio_col2:
            _quick_options = [1, 2, 3, 4]
            _quick_default = _bio_cfg.get("quick_interval_h", 2)
            _quick_idx = _quick_options.index(_quick_default) if _quick_default in _quick_options else 1
            _bio_quick_interval = st.selectbox("⚡ Quick Scan Intervall",
                                                options=_quick_options,
                                                index=_quick_idx,
                                                format_func=lambda x: f"Alle {x}h",
                                                key="bio_quick_interval",
                                                help="Quick Scan = nur News updaten (schnell, ~2-3 Min)")
            _full_options = [4, 6, 8, 12]
            _full_default = _bio_cfg.get("full_interval_h", 6)
            _full_idx = _full_options.index(_full_default) if _full_default in _full_options else 1
            _bio_full_interval = st.selectbox("🔬 Full Scan Intervall",
                                               options=_full_options,
                                               index=_full_idx,
                                               format_func=lambda x: f"Alle {x}h",
                                               key="bio_full_interval",
                                               help="Full Scan = Universum + News + Pipeline + Technik (langsam, ~15-20 Min)")
        with _bio_col3:
            st.markdown("""
            **⚡ Quick Scan** (alle 1-2h)
            Nur News-Update für bekannte Tickers. Schnell (~2 Min).

            **🔬 Full Scan** (alle 4-6h)
            Universum + News + BPIQ + Technik. Gründlich (~15 Min).

            FDA-News kommen **jederzeit** — Pre-Market, Regular, After-Hours.
            """)

        # ── Einstellungen speichern wenn geändert ──
        _bio_new_cfg = {
            "auto_scan": _bio_auto_scan,
            "quick_interval_h": _bio_quick_interval,
            "full_interval_h": _bio_full_interval,
            "min_score": _bio_min_score,
        }
        if _bio_new_cfg != _bio_cfg:
            _biotech_config_save(_bio_new_cfg)

    # ── Auto-Scan Logik (Intervall-basiert) ──
    _bio_auto_triggered = False
    _bio_auto_type = None  # "quick" oder "full"

    # Anti-Loop Guard: Track wann letzter Auto-Scan gestartet wurde
    if "bio_auto_scan_started_at" not in st.session_state:
        st.session_state.bio_auto_scan_started_at = 0

    if _bio_auto_scan:
        try:
            _bio_prog_check = _biotech_progress_read()
            _bio_is_running = _bio_prog_check and _bio_prog_check.get("status") == "running"
            # Auch "done" als Blocker — Scan ist gerade erst fertig, kein neuer nötig
            _bio_recently_done = (_bio_prog_check and _bio_prog_check.get("status") == "done"
                                  and time.time() - _bio_prog_check.get("timestamp", 0) < 300)  # 5 Min Cooldown
            # Anti-Loop: Mindestens 60 Sek seit letztem Auto-Start warten
            _bio_recently_started = (time.time() - st.session_state.bio_auto_scan_started_at) < 60

            if not _bio_is_running and not _bio_recently_done and not _bio_recently_started:
                # Prüfe wann letzter Full Scan war
                _bio_full_cache = _biotech_cache_load(max_age_hours=_bio_full_interval)
                _bio_quick_cache = _biotech_cache_load(max_age_hours=_bio_quick_interval)

                if not _bio_full_cache:
                    # Kein frischer Full-Scan Cache → Full Scan starten
                    _bio_auto_triggered = True
                    _bio_auto_type = "full"
                elif not _bio_quick_cache:
                    # Full Scan ist noch frisch, aber Quick Intervall abgelaufen → Quick Scan
                    _bio_auto_triggered = True
                    _bio_auto_type = "quick"
        except Exception:
            pass

    # ── Scan Controls ──
    _bio_col_a, _bio_col_b, _bio_col_c, _bio_col_d = st.columns([1, 1, 1, 1])

    with _bio_col_a:
        _bio_full_btn = st.button("🔬 Full Scan", use_container_width=True, type="primary",
                                   help="Kompletter Scan: Universum + News + Pipeline + Technik")

    with _bio_col_b:
        _bio_quick_btn = st.button("⚡ Quick Scan", use_container_width=True,
                                    help="Schnell: Nur News-Update für bekannte Tickers")

    with _bio_col_c:
        _bio_cached = _biotech_cache_load(max_age_hours=24)
        if _bio_cached:
            try:
                _bio_cache_age = (time.time() - os.path.getmtime(_biotech_cache_file())) / 60
                _bio_age_str = f"{_bio_cache_age:.0f} Min" if _bio_cache_age < 120 else f"{_bio_cache_age/60:.1f}h"
                st.caption(f"📦 {len(_bio_cached)} Ergebnisse ({_bio_age_str} alt)")
            except Exception:
                st.caption(f"📦 {len(_bio_cached)} Ergebnisse")

    with _bio_col_d:
        if _bio_auto_scan:
            st.caption(f"🔄 Quick: {_bio_quick_interval}h | Full: {_bio_full_interval}h")
        if _bio_auto_triggered:
            _auto_label = "🔬 Full" if _bio_auto_type == "full" else "⚡ Quick"
            st.info(f"🔄 Auto {_auto_label} Scan startet...")

    # ── Start Scan (manuell ODER auto-triggered) ──
    _bio_start_full = _bio_full_btn or (_bio_auto_triggered and _bio_auto_type == "full")
    _bio_start_quick = _bio_quick_btn or (_bio_auto_triggered and _bio_auto_type == "quick")

    if _bio_start_full or _bio_start_quick:
        try:
            _bio_poly_key = st.secrets.get("POLYGON_KEY", "")
            if not _bio_poly_key:
                st.error("❌ POLYGON_KEY fehlt in Secrets!")
            else:
                if _bio_start_full:
                    _bio_thread = threading.Thread(
                        target=_biotech_background_scan,
                        args=(_bio_poly_key,),
                        daemon=True
                    )
                    _scan_label = "Full Scan"
                else:
                    _bio_thread = threading.Thread(
                        target=_biotech_quick_scan,
                        args=(_bio_poly_key,),
                        daemon=True
                    )
                    _scan_label = "Quick Scan"

                _bio_thread.start()
                if _bio_auto_triggered:
                    st.session_state.bio_auto_scan_started_at = time.time()
                _trigger = "Auto" if _bio_auto_triggered else "Manuell"
                st.toast(f"🧬 {_scan_label} gestartet ({_trigger})...")
                time.sleep(2)
                st.rerun()
        except Exception as e:
            st.error(f"Fehler: {e}")

    # ── Progress Anzeige ──
    _bio_prog = _biotech_progress_read()
    if _bio_prog and _bio_prog.get("status") == "running":
        # Timeout: Scan älter als 60 Min = abgebrochen
        _bio_age = time.time() - _bio_prog.get("timestamp", 0)
        if _bio_age > 7200:
            st.error(f"⏰ Biotech Scan Timeout (>120 Min, letzte Aktivität vor {_bio_age/60:.0f} Min). Bitte neu starten.")
            try:
                os.remove(_biotech_progress_file())
            except Exception:
                pass
        else:
            _bp_checked = _bio_prog.get("checked", 0)
            _bp_total = _bio_prog.get("total", 0)
            _bp_hits = _bio_prog.get("hits", 0)
            _bp_detail = _bio_prog.get("detail", "")
            _bp_pct = _bp_checked / max(1, _bp_total)

            _bio_prog_col1, _bio_prog_col2 = st.columns([5, 1])
            with _bio_prog_col1:
                st.progress(_bp_pct, text=f"🧬 {_bp_checked}/{_bp_total} | {_bp_hits} Treffer | {_bp_detail}")
            with _bio_prog_col2:
                if st.button("⏹️ Stop", key="bio_stop_btn", use_container_width=True, type="secondary"):
                    _biotech_request_stop()
                    st.toast("⏹️ Biotech Scan wird gestoppt...")
                    time.sleep(1)
                    st.rerun()

            # Progress-Update: nutze globalen Sidebar Auto-Refresh statt eigenem

    elif _bio_prog and _bio_prog.get("status") == "stopped":
        _bp_hits = _bio_prog.get("hits", 0)
        _bp_checked = _bio_prog.get("checked", 0)
        _bp_total = _bio_prog.get("total", 0)
        st.warning(f"⏹️ Scan gestoppt bei {_bp_checked}/{_bp_total} — {_bp_hits} Treffer gespeichert")
        # Nach 30 Sek aufräumen
        if time.time() - _bio_prog.get("timestamp", 0) > 30:
            try:
                os.remove(_biotech_progress_file())
            except Exception:
                pass

    elif _bio_prog and _bio_prog.get("status") == "error":
        st.error(f"❌ Scan Fehler: {_bio_prog.get('detail', 'Unbekannt')}")

    elif _bio_prog and _bio_prog.get("status") == "done":
        _bp_hits = _bio_prog.get("hits", 0)
        _bp_total = _bio_prog.get("total", 0)
        _bp_top = _bio_prog.get("top_score", 0)
        _bp_done_age = time.time() - _bio_prog.get("timestamp", 0)
        # Nur kurz "fertig" anzeigen, dann Progress-Datei aufräumen
        if _bp_done_age < 60:
            st.success(f"✅ Scan fertig: {_bp_total} Biotech-Aktien → **{_bp_hits} mit Katalysator** (Top Score: {_bp_top})")
        else:
            # Progress-Datei entfernen damit kein dauerhaftes "done" hängen bleibt
            try:
                os.remove(_biotech_progress_file())
            except Exception:
                pass

    # ── Ergebnisse anzeigen ──
    _bio_results = _bio_cached if _bio_cached else _biotech_cache_load(max_age_hours=24)

    # Leere Liste ist kein gültiger Cache — auf None setzen
    if _bio_results is not None and len(_bio_results) == 0:
        _bio_results = None

    if _bio_results:
        # Filter nach Min Score
        _bio_filtered = [r for r in _bio_results if isinstance(r, dict) and r.get("Score", 0) >= _bio_min_score]

        if not _bio_filtered:
            st.info("Keine Ergebnisse über dem Mindest-Score. Reduziere den Min. Score in den Einstellungen.")
        else:
            # ── Summary Metrics ──
            _bio_m1, _bio_m2, _bio_m3, _bio_m4, _bio_m5 = st.columns(5)
            _bio_grade_a = sum(1 for r in _bio_filtered if r.get("Grade") == "A")
            _bio_grade_b = sum(1 for r in _bio_filtered if r.get("Grade") == "B")
            _bio_fda_count = sum(1 for r in _bio_filtered if "FDA" in r.get("Catalyst", "") or "PDUFA" in r.get("Readout_Label", ""))
            _bio_bpiq_cat = sum(1 for r in _bio_filtered if r.get("BPIQ_Available") and r.get("Readout_Score", 0) > 0)

            _bio_m1.metric("🧬 Treffer", len(_bio_filtered))
            _bio_m2.metric("🅰️ Grade A", _bio_grade_a)
            _bio_m3.metric("🅱️ Grade B", _bio_grade_b)
            _bio_m4.metric("🎯 FDA/PDUFA", _bio_fda_count)
            _bio_m5.metric("📊 BPIQ Catalysts", _bio_bpiq_cat)

            # ── Sektor-Trend: Healthcare / Biotech ──
            try:
                _bio_poly = st.secrets.get("POLYGON_KEY", "")
                if _bio_poly:
                    _bio_perf = _fetch_sector_etf_performance(_bio_poly)
                    if _bio_perf and "XLV" in _bio_perf:
                        _xlv_chg = _bio_perf["XLV"]
                        if _xlv_chg > 1.5:
                            st.success(f"📊 **Sektor-Trend:** 🏥 Healthcare (XLV) **{_xlv_chg:+.1f}%** — Starker Rückenwind für Biotech")
                        elif _xlv_chg > 0.3:
                            st.info(f"📊 **Sektor-Trend:** 🏥 Healthcare (XLV) **{_xlv_chg:+.1f}%** — Leichter Rückenwind")
                        elif _xlv_chg < -1.5:
                            st.error(f"📊 **Sektor-Trend:** 🏥 Healthcare (XLV) **{_xlv_chg:+.1f}%** — Starker Gegenwind für Biotech")
                        elif _xlv_chg < -0.3:
                            st.warning(f"📊 **Sektor-Trend:** 🏥 Healthcare (XLV) **{_xlv_chg:+.1f}%** — Leichter Gegenwind")
                        else:
                            st.caption(f"📊 Sektor-Trend: 🏥 Healthcare (XLV) {_xlv_chg:+.1f}% — Neutral")
            except Exception:
                pass

            st.divider()

            # ── Dataframe ──
            import pandas as pd
            _bio_df = pd.DataFrame(_bio_filtered)
            _bio_display_cols = ["Ticker", "Risk_Flag", "Name", "Score", "Grade", "Chart", "Catalyst", "Event_Result", "Readout_Label", "Preis", "MCap_M", "RVOL",
                                 "Phase3", "Phase2", "Active_Trials", "Sentiment", "Float_Cat"]
            _bio_avail_cols = [c for c in _bio_display_cols if c in _bio_df.columns]

            _bio_sel = st.dataframe(
                _bio_df[_bio_avail_cols],
                column_config={
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Risk_Flag": st.column_config.TextColumn("⚠️", width="small"),
                    "Name": st.column_config.TextColumn("Name", width="medium"),
                    "Score": st.column_config.ProgressColumn("Catalyst Score", format="%d", min_value=0, max_value=100),
                    "Grade": st.column_config.TextColumn("Grade", width="small"),
                    "Chart": st.column_config.TextColumn("Chart", width="small"),
                    "Catalyst": st.column_config.TextColumn("Katalysator", width="medium"),
                    "Event_Result": st.column_config.TextColumn("📋 Ergebnis", width="small"),
                    "Readout_Label": st.column_config.TextColumn("⏰ Readout", width="large"),
                    "Preis": st.column_config.NumberColumn("Preis", format="$%.2f"),
                    "MCap_M": st.column_config.NumberColumn("MCap (M$)", format="%.0f"),
                    "RVOL": st.column_config.NumberColumn("RVOL", format="%.1f"),
                    "Phase3": st.column_config.NumberColumn("Ph3", width="small"),
                    "Phase2": st.column_config.NumberColumn("Ph2", width="small"),
                    "Active_Trials": st.column_config.NumberColumn("Trials", width="small"),
                    "Sentiment": st.column_config.TextColumn("Sentiment"),
                    "Float_Cat": st.column_config.TextColumn("Float", width="small"),
                },
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="biotech_table"
            )

            # ── Detail View bei Auswahl ──
            _bio_selected_idx = None
            if _bio_sel and _bio_sel.selection and _bio_sel.selection.rows:
                _bio_selected_idx = _bio_sel.selection.rows[0]

            if _bio_selected_idx is not None and 0 <= _bio_selected_idx < len(_bio_filtered):
                _bio_item = _bio_filtered[_bio_selected_idx]
                st.divider()

                # Header
                _bio_score = _bio_item.get("Score", 0)
                _bio_score_color = "#22c55e" if _bio_score >= 75 else "#eab308" if _bio_score >= 55 else "#f97316" if _bio_score >= 35 else "#ef4444"
                st.markdown(
                    f"## 🧬 {_bio_item.get('Ticker', 'N/A')} — {_bio_item.get('Name', '')} "
                    f"<span style='background:{_bio_score_color};color:white;padding:4px 12px;border-radius:12px;"
                    f"font-size:18px;font-weight:bold;'>{_bio_item.get('Grade', '?')} ({_bio_score}/100)</span>",
                    unsafe_allow_html=True
                )

                # ── Event Result Sentiment ──
                _bio_ev_result = _bio_item.get("Event_Result", "")
                if _bio_ev_result and _bio_ev_result not in ("—", ""):
                    if "Positiv" in _bio_ev_result:
                        st.success(f"📋 **Trial/Event Ergebnis:** {_bio_ev_result}")
                    elif "Negativ" in _bio_ev_result:
                        st.error(f"📋 **Trial/Event Ergebnis:** {_bio_ev_result}")
                    elif "Gemischt" in _bio_ev_result:
                        st.warning(f"📋 **Trial/Event Ergebnis:** {_bio_ev_result}")
                    elif "Ausstehend" in _bio_ev_result:
                        st.info(f"📋 **Trial/Event Ergebnis:** {_bio_ev_result}")

                # ── Penny Stock / Micro Cap Warnung ──
                _bio_risk_flag = _bio_item.get("Risk_Flag", "")
                if "PENNY" in _bio_risk_flag:
                    st.error("🚨 **PENNY STOCK WARNUNG** — MCap unter $50M und/oder Preis unter $1. "
                             "Extrem hohes Risiko: geringe Liquidität, große Spreads, anfällig für Manipulation. "
                             "Nur mit Spielgeld und striktem Stop-Loss traden!")
                elif "MICRO" in _bio_risk_flag:
                    st.warning("⚠️ **MICRO CAP** — MCap unter $100M. Erhöhte Volatilität und Liquiditätsrisiko. "
                               "Position-Sizing reduzieren!")

                # Chart Health Warnung (wenn schwach/kritisch)
                _bio_ch = _bio_item.get("Chart_Health", 10)
                _bio_dd = _bio_item.get("Drawdown", 0)
                _bio_sr = _bio_item.get("Selloff_Reason", "")
                if _bio_ch <= 5:
                    _ch_parts = [_bio_item.get("Chart", "")]
                    if _bio_dd > 0:
                        _ch_parts.append(f"−{_bio_dd:.0f}% vom High")
                    _td = _bio_item.get("Tech_Details", {})
                    if _td.get("trend", "").startswith("📉"):
                        _ch_parts.append(_td["trend"])
                    if _td.get("recent_action"):
                        _ch_parts.append(_td["recent_action"])
                    if _bio_sr:
                        if "❓" in _bio_sr:
                            st.warning(f"📉 **Chart schwach** ({' | '.join(_ch_parts)}) — Aber: **Kein negativer Catalyst gefunden** → mögliche Dip-Opportunity?")
                        else:
                            st.error(f"📉 **Chart schwach** ({' | '.join(_ch_parts)}) — Grund: **{_bio_sr}**")
                    else:
                        st.warning(f"📉 **Chart Achtung:** {' | '.join(_ch_parts)}")

                # ── Catalyst Kalender (BPIQ oder CT.gov Fallback) ──
                _bio_has_bpiq = _bio_item.get("BPIQ_Available", False)
                _bio_bpiq_cats = _bio_item.get("BPIQ_Catalysts", [])
                _bio_readouts = _bio_item.get("Readout_Details", [])
                _bio_readout_lbl = _bio_item.get("Readout_Label", "")

                if _bio_has_bpiq and _bio_bpiq_cats:
                    # ── BPIQ Kuratierte Catalyst-Daten ──
                    for _bcat in _bio_bpiq_cats[:3]:
                        _bc_cat = _bcat.get("category", "")
                        _bc_days = _bcat.get("days_until")
                        _bc_label = _bcat.get("full_label", "?")
                        _bc_stage = _bcat.get("stage_label", "")
                        _bc_event = _bcat.get("event_label", "")
                        _bc_drug = _bcat.get("drug_name", "")[:40]
                        _bc_date_txt = _bcat.get("catalyst_date_text", "")
                        _bc_date = _bcat.get("catalyst_date", "")
                        _bc_note = (_bcat.get("note", "") or "")[:200]
                        _bc_ind = _bcat.get("indications", "")

                        # Status: Ergebnis war oder wird erwartet
                        _bc_status = ""
                        if _bc_cat == "OVERDUE":
                            _bc_status = "⏰ Ergebnis ausstehend"
                        elif _bc_cat in ("IMMINENT", "UPCOMING", "LATER"):
                            _bc_status = "📅 Erwartet"
                        elif not _bc_date and not _bc_cat:
                            _bc_status = "📋 TBA"

                        # Event-Typ Beschreibung
                        _bc_event_desc = f"{_bc_stage}" + (f" — {_bc_event}" if _bc_event else "")

                        if "PDUFA" in _bc_label:
                            if _bc_cat == "IMMINENT":
                                st.error(f"🔴 **PDUFA** 📅 {_bc_date_txt} ({_bc_days}d) — {_bc_drug}"
                                         f"{' | ' + _bc_ind if _bc_ind else ''}")
                            elif _bc_cat == "UPCOMING":
                                st.warning(f"🟡 **PDUFA** 📅 {_bc_date_txt} (in {_bc_days}d) — {_bc_drug}"
                                           f"{' | ' + _bc_ind if _bc_ind else ''}")
                            elif _bc_cat == "OVERDUE":
                                st.error(f"🔴 **PDUFA ÜBERFÄLLIG** ({abs(_bc_days)}d seit {_bc_date_txt}) — {_bc_drug}")
                            else:
                                st.info(f"🟢 **PDUFA** ({_bc_date_txt or 'TBA'}) — {_bc_drug}")
                        else:
                            _date_info = f"📅 {_bc_date_txt}" if _bc_date_txt and _bc_date_txt != "TBA" else "📅 TBA"
                            if _bc_cat == "OVERDUE":
                                st.error(f"🔴 **{_bc_event_desc}** — ÜBERFÄLLIG ({abs(_bc_days)}d) — {_bc_drug}"
                                         f"\n{_date_info}{' | ' + _bc_ind if _bc_ind else ''}")
                            elif _bc_cat == "IMMINENT":
                                st.warning(f"🟡 **{_bc_event_desc}** — in {_bc_days}d ({_bc_date_txt}) — {_bc_drug}"
                                           f"{' | ' + _bc_ind if _bc_ind else ''}")
                            elif _bc_cat == "UPCOMING":
                                st.info(f"🟢 **{_bc_event_desc}** — in {_bc_days}d ({_bc_date_txt}) — {_bc_drug}"
                                        f"{' | ' + _bc_ind if _bc_ind else ''}")
                            else:
                                # LATER oder TBA
                                st.info(f"⚪ **{_bc_event_desc}** — {_bc_drug} ({_date_info})"
                                        f"{' | ' + _bc_ind if _bc_ind else ''}")
                        if _bc_note:
                            st.caption(f"📝 {_bc_note}")

                elif _bio_readouts:
                    # ── CT.gov Fallback (wenn kein BPIQ) ──
                    _ro_top = _bio_readouts[0]
                    _ro_cat = _ro_top.get("readout_category", "") if isinstance(_ro_top, dict) and "readout_category" in _ro_top else _ro_top.get("category", "")
                    if _ro_cat == "OVERDUE":
                        _phase = _ro_top.get("phase", _ro_top.get("full_label", "?"))
                        _pc = _ro_top.get("primary_completion", _ro_top.get("catalyst_date_text", "?"))
                        _days = abs(_ro_top.get("days_until_readout", _ro_top.get("days_until", 0)))
                        st.error(f"🔴 **TRIAL READOUT ÜBERFÄLLIG** — {_phase}: {_days}d überfällig. "
                                 f"_{_ro_top.get('title', _ro_top.get('drug_name', ''))}_")
                    elif _ro_cat == "OVERDUE_STALE":
                        st.caption(f"⚪ Trial Readout veraltet — wahrscheinlich abgeschlossen, Status nicht aktualisiert")
                    elif _ro_cat == "IMMINENT":
                        _days = _ro_top.get("days_until_readout", _ro_top.get("days_until", "?"))
                        st.warning(f"🟡 **TRIAL READOUT IN {_days} TAGEN** — "
                                   f"_{_ro_top.get('title', _ro_top.get('drug_name', ''))}_")
                    elif _ro_cat == "UPCOMING":
                        _days = _ro_top.get("days_until_readout", _ro_top.get("days_until", "?"))
                        st.info(f"🟢 **Trial Readout in {_days} Tagen** — "
                                f"_{_ro_top.get('title', _ro_top.get('drug_name', ''))}_")

                # ── Options Unusual Activity (on-demand, nur in Detail View) ──
                try:
                    _bio_price = _bio_item.get("Preis", 0)
                    _bio_mcap = _bio_item.get("MCap_M", 0)
                    _opt_poly = st.secrets.get("POLYGON_KEY", "")
                    # Nur für Aktien mit MCap > $200M (darunter keine Options-Liquidität)
                    if _opt_poly and _bio_price > 0 and _bio_mcap >= 200:
                        _render_options_activity_banner(_bio_item.get("Ticker", "N/A"), _bio_price, _opt_poly)
                except Exception:
                    pass

                # Score Breakdown
                _bio_bc1, _bio_bc2, _bio_bc3, _bio_bc4, _bio_bc5, _bio_bc6, _bio_bc7 = st.columns(7)
                _bio_bc1.metric("🎯 Catalyst", f"{_bio_item.get('Catalyst_Score', 0)}/30", help="FDA/PDUFA Events, Approvals, Breakthrough")
                _bio_bc2.metric("🔬 Pipeline", f"{_bio_item.get('Pipeline_Score', 0)}/20", help="Phase 3/2/1 Clinical Trials")
                _bio_bc3.metric("⏰ Readout", f"{_bio_item.get('Readout_Score', 0)}/15", help="Überfällige/bevorstehende Trial-Readouts. OVERDUE=🔴, IMMINENT=🟡, UPCOMING=🟢")
                _bio_bc4.metric("📈 Technical", f"{_bio_item.get('Technical_Score', 0)}/20", help="Volume, Trend, Akkumulation")
                _bio_bc5.metric("🎰 Opportunity", f"{_bio_item.get('Risk_Score', 0)}/15", help="Sweet Spot: $0.5-10B MCap, Low Float, keine Red Flags")
                _bio_bc6.metric("📰 Momentum", f"{_bio_item.get('Momentum_Score', 0)}/15", help="News Sentiment & Frequency")
                _bio_bc7.metric("📊 Chart", f"{_bio_ch}/10", delta=f"−{_bio_dd:.0f}% vom High" if _bio_dd >= 10 else None, delta_color="normal", help="Chart Health: 10=perfekt, 0=Crash.")

                st.divider()

                # ── Catalyst Details ──
                _bio_dc1, _bio_dc2 = st.columns(2)

                with _bio_dc1:
                    st.markdown("### 🎯 Katalysator")
                    _bio_cats = _bio_item.get("Catalysts_All", [])
                    if _bio_cats:
                        for cat in _bio_cats[:5]:
                            _tier_color = {"tier1": "🔴", "tier2": "🟠", "tier3": "🟡", "tier4": "🟢"}.get(cat.get("tier"), "⚪")
                            st.markdown(f"{_tier_color} **{cat.get('label', '')}**: {cat.get('keyword', '')} ({cat.get('date', '')})")
                            if cat.get("headline"):
                                st.caption(f"📰 {cat['headline']}")
                    else:
                        st.info("Kein direkter FDA-Catalyst gefunden — Signal basiert auf Pipeline/Momentum")

                    # Headline
                    if _bio_item.get("Headline"):
                        st.markdown(f"**💡 Top Headline:** {_bio_item.get('Headline', '')}")

                    # Negative Flags
                    _neg_flags = _bio_item.get("Negative_Flags", [])
                    if _neg_flags:
                        st.markdown("#### ⚠️ Warnungen")
                        for nf in _neg_flags:
                            st.error(f"**{nf['flag'].upper()}** ({nf['date']}) — Penalty: {nf['penalty']} Punkte")

                with _bio_dc2:
                    # ── BPIQ Drug Pipeline (kuratiert) ──
                    if _bio_item.get("BPIQ_Available") and _bio_item.get("BPIQ_Catalysts"):
                        st.markdown("### 💊 Drug Pipeline (BPIQ)")
                        _all_bpiq = _bio_item.get("BPIQ_Catalysts", [])
                        for _bd in _all_bpiq[:5]:
                            _bd_stage = _bd.get("stage_label", "")
                            _bd_emoji = "🔴" if "3" in _bd_stage or "PDUFA" in _bd_stage else "🟠" if "2" in _bd_stage else "🟢"
                            _bd_cat = _bd.get("category", "")
                            _bd_days = _bd.get("days_until")
                            _bd_badge = ""
                            if _bd_cat == "IMMINENT":
                                _bd_badge = f" ⏰🟡 **{_bd_days}d**"
                            elif _bd_cat == "UPCOMING":
                                _bd_badge = f" ⏰🟢 {_bd_days}d"
                            elif _bd_cat == "OVERDUE":
                                _bd_badge = f" ⏰🔴 **{abs(_bd_days)}d**"
                            st.markdown(f"{_bd_emoji} **{_bd.get('full_label', '?')}** — {_bd.get('drug_name', '')}{_bd_badge}")
                            _bd_ind = _bd.get("indications", "")
                            if _bd_ind:
                                st.caption(f"Indikation: {_bd_ind}")
                        st.caption("_Quelle: BPIQ (kuratiert, täglich aktualisiert)_")
                        st.divider()

                    st.markdown("### 🔬 Catalyst Pipeline (BPIQ)")
                    _bio_readouts = _bio_item.get("Readout_Details", [])
                    if _bio_readouts:
                        for _rd in _bio_readouts[:5]:
                            _rd_label = _rd.get("full_label", _rd.get("stage_label", "?"))
                            _rd_drug = _rd.get("drug_name", "")
                            _rd_days = _rd.get("days_until")
                            _rd_cat = _rd.get("category", "")
                            _rd_date = _rd.get("catalyst_date_text", "TBA")
                            _rd_emoji = "🔴" if _rd_cat == "OVERDUE" else "🟡" if _rd_cat == "IMMINENT" else "🟢" if _rd_cat in ("UPCOMING", "LATER") else "⚪"
                            _rd_timing = ""
                            if _rd_days is not None and _rd_cat == "OVERDUE":
                                _rd_timing = f" — **ÜBERFÄLLIG** ({abs(_rd_days)}d)"
                            elif _rd_days is not None and _rd_days > 0:
                                _rd_timing = f" — in {_rd_days}d ({_rd_date})"
                            elif _rd_date and _rd_date != "TBA":
                                _rd_timing = f" — {_rd_date}"
                            st.markdown(f"{_rd_emoji} **{_rd_label}** — {_rd_drug}{_rd_timing}")
                            _rd_note = _rd.get("note", "")
                            if _rd_note:
                                st.caption(f"📝 {_rd_note[:150]}")
                    else:
                        st.info("Keine BPIQ Catalyst-Daten für diesen Ticker")

                # ── Technical Details & News ──
                _bio_tc1, _bio_tc2 = st.columns(2)

                with _bio_tc1:
                    st.markdown("### 📈 Technische Analyse")
                    _tech = _bio_item.get("Tech_Details", {})
                    if _tech:
                        _tc_cols = st.columns(3)
                        _tc_cols[0].metric("💰 Preis", f"${_tech.get('price', 0):.2f}")
                        _tc_cols[1].metric("📊 RVOL", f"{_tech.get('RVOL', 0):.1f}x")
                        _tc_cols[2].metric("📉 90D Pos", f"{_tech.get('pos_90d', 0):.0f}%")

                        if _tech.get("vol_signal"):
                            st.caption(f"Volume: {_tech['vol_signal']}")
                        if _tech.get("trend"):
                            st.caption(f"Trend: {_tech['trend']}")
                        if _tech.get("consolidation"):
                            st.caption(f"Range: {_tech['consolidation']}")

                        # V69: Candlestick-Patterns
                        _bio_patterns = _tech.get("candle_patterns", [])
                        _bio_ca_trend = _tech.get("candle_trend", "")
                        _bio_vol_trend = _tech.get("candle_volume_trend", "")
                        _bio_support = _tech.get("support", 0)
                        _bio_resi = _tech.get("resistance", 0)

                        if _bio_support > 0 and _bio_resi > 0:
                            st.caption(f"🟢 Support: ${_bio_support:,.2f} | 🔴 Resistance: ${_bio_resi:,.2f}")

                        if _tech.get("breakout_ready"):
                            st.success("🎯 **Breakout Ready** — enge Range + steigendes Volumen!")

                        if _bio_vol_trend == "accumulation":
                            st.caption("📊 Volumen: 🟢 Akkumulation (Käufer aktiv)")
                        elif _bio_vol_trend == "distribution":
                            st.caption("📊 Volumen: 🔴 Distribution (Verkäufer aktiv)")

                        if _bio_patterns:
                            st.markdown("**🕯️ Candlestick-Patterns:**")
                            for _bp in _bio_patterns[:4]:
                                _bp_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(_bp.get("type", ""), "")
                                st.caption(f"{_bp_icon} **{_bp['name']}** ({_bp.get('pos', '')}) — {_bp.get('signal', '')}")

                    # Risk Details
                    _risk_details = _bio_item.get("Risk_Details", [])
                    if _risk_details:
                        st.markdown("**🛡️ Risiko-Profil:**")
                        for rd in _risk_details:
                            st.caption(rd)

                with _bio_tc2:
                    # ── FDA/Trial Catalysts Detail ──
                    _bio_all_cats = _bio_item.get("Catalysts_All", [])
                    if _bio_all_cats:
                        st.markdown("### 🎯 Erkannte Katalysatoren")
                        for _ac in _bio_all_cats[:5]:
                            _ac_tier = _ac.get("tier", "")
                            _ac_kw = _ac.get("keyword", "").title()
                            _ac_date = _ac.get("date", "")
                            _ac_head = _ac.get("headline", "")
                            _tier_emoji = "🔴" if _ac_tier == "tier1" else "🟡" if _ac_tier == "tier2" else "🟢" if _ac_tier == "tier3" else "⚪"
                            _tier_badge = {"tier1": "FDA", "tier2": "Trial", "tier3": "Deal", "tier4": "Pipeline"}.get(_ac_tier, "")
                            st.markdown(f"{_tier_emoji} **[{_tier_badge}]** {_ac_kw} — {_ac_date}")
                            if _ac_head:
                                st.caption(f"📰 {_ac_head}")
                        st.divider()

                    st.markdown("### 📰 Aktuelle News")
                    _bio_news = _bio_item.get("News", [])
                    if _bio_news:
                        for n in _bio_news[:5]:
                            sent_emoji = "🟢" if n.get("sentiment") == "positive" else "🔴" if n.get("sentiment") == "negative" else "⚪"
                            cat_badge = f" **{n['catalyst']}**" if n.get("catalyst") else ""
                            st.markdown(f"{sent_emoji}{cat_badge} {n.get('title', '')} ({n.get('published', '')})")
                    else:
                        st.info("Keine aktuellen News verfügbar")

                # ── TradingView Chart ──
                st.divider()
                _bio_ticker_tv = _bio_item.get("Ticker", "N/A")
                _bio_tv_html = f'''
                <div id="biotech-tv-widget" style="height:400px;">
                <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
                <script type="text/javascript">
                new TradingView.widget({{
                    "autosize": true,
                    "symbol": "{_bio_ticker_tv}",
                    "interval": "D",
                    "timezone": "Europe/Berlin",
                    "theme": "dark",
                    "style": "1",
                    "locale": "de_DE",
                    "toolbar_bg": "#1e1e1e",
                    "enable_publishing": false,
                    "hide_side_toolbar": false,
                    "allow_symbol_change": true,
                    "studies": ["Volume@tv-basicstudies", "MAExp@tv-basicstudies"],
                    "container_id": "biotech-tv-widget",
                    "height": "400",
                    "width": "100%"
                }});
                </script>
                </div>
                '''
                st.components.v1.html(_bio_tv_html, height=420)

    else:
        # Kein Cache — Willkommens-Seite
        st.markdown("---")
        st.markdown("""
        ### 🧬 Willkommen beim Biotech Scanner

        Dieser Scanner analysiert **Biotech- und Pharma-Aktien** auf:

        **🎯 FDA-Katalysatoren (30 Punkte)**
        PDUFA Dates, FDA Approvals, Breakthrough Therapy, Fast Track, Priority Review, AdCom Meetings

        **🔬 Catalyst Pipeline (20 Punkte)**
        BPIQ kuratierte Catalyst-Dates — PDUFA, Phase 3 Readouts, AdCom

        **📈 Technische Analyse (20 Punkte)**
        Unusual Volume, Volume-Trend, Akkumulation, Price Position, Trend-Richtung

        **📰 News Momentum (15 Punkte)**
        Sentiment-Analyse, News-Frequenz, Katalysator-Dichte

        **🛡️ Risiko-Bewertung (15 Punkte)**
        Market Cap, Float, Penny Stock Check, negative Signale (Clinical Hold, Offerings, etc.)

        ---
        **Drücke "🔬 Full Scan" um zu beginnen!**
        """)


# =============================================================================
# TAB: BTC-DIVERGENZ SHORT SCANNER 📉
# =============================================================================
with tab_divergence:
    st.header("📉 BTC-Divergenz Short Scanner V2")
    st.caption("Findet Altcoins die gegen BTC-Schwäche pumpen — mit 1h-Echtzeit-Timing für präzise Short-Entries")

    col_info, col_scan, col_refresh = st.columns([2, 1, 1])
    with col_info:
        st.markdown("""
        **Strategie:** Wenn BTC seitwärts/bearisch ist aber ein Altcoin hochschießt,
        fehlt dem Pump der Rückenwind. Diese Coins korrigieren fast immer stark.

        **Kriterien:** BTC schwach auf mind. einem TF (7d/14d/30d) · Altcoin outperformt > +10% · Exhaustion-Score
        **Multi-TF:** Scannt 7d, 14d & 30d Divergenz — längere Divergenz = zuverlässigerer Short
        **1h-Timing:** Erkennt den exakten Kipp-Moment für den Entry
        """)
    with col_scan:
        scan_divergence = st.button("🔍 Divergenz-Scan starten (1000 Coins)", type="primary", key="btn_div_scan")
    with col_refresh:
        div_auto_refresh = st.checkbox("🔄 Auto-Refresh (5 Min)", key="div_auto_refresh",
                                        help="Scannt alle 5 Minuten automatisch — warnt bei Timing-Änderungen")
        if div_auto_refresh:
            st_autorefresh(interval=300_000, key="div_autorefresh_timer")  # 5 Minuten
            # Bei Auto-Refresh automatisch scannen wenn noch keine Daten da
            if "div_results" not in st.session_state:
                scan_divergence = True

    # ── Background-Thread Scan (damit BI Scanner Auto-Refresh den Scan nicht killt) ──
    _DIV_PROGRESS = "/tmp/div_scan_progress.json"
    _DIV_RESULTS = "/tmp/div_scan_results.json"

    # Fallback: Wenn keine Progress-Datei aber Results vorhanden, direkt laden (bg_service Cache)
    if "div_results" not in st.session_state and not os.path.exists(_DIV_PROGRESS) and os.path.exists(_DIV_RESULTS):
        try:
            _res_age = time.time() - os.path.getmtime(_DIV_RESULTS)
            if _res_age < 7800:  # Max 130 Min alt
                with open(_DIV_RESULTS, "r") as _f:
                    _div_data = json.load(_f)
                new_results = _div_data.get("results", [])
                _div_defaults = {
                    "Timing": "⚪ Früh", "SellProb": 0, "ExhScore": 0, "ExhGrade": "—",
                    "GradeEmoji": "⚪", "1h%": 0, "24h%": 0, "14d%": 0, "30d%": 0,
                    "7d%": 0, "BestTF": "—", "FundingRate": None, "OI_Ratio": None,
                    "Divergenz%": 0, "Div7d%": 0, "Div14d%": 0, "Div30d%": 0,
                    "RVOL": 0, "UpperWick%": 0, "MarketCap": 0, "Vol24h": 0,
                    "HasPerp": False, "Exchanges": [], "ExhDetails": [],
                    "CoinId": "", "BestExchange": "", "Ticker": "?", "Name": "?", "Preis": 0,
                    "btc_gate": None,  # H-7: None = Altdaten ohne Gate-Info
                }
                for _r in new_results:
                    for _dk, _dv in _div_defaults.items():
                        if _dk not in _r:
                            _r[_dk] = _dv
                st.session_state["div_results"] = new_results
                st.session_state["div_btc"] = _div_data.get("btc")
                st.session_state["div_stats"] = _div_data.get("stats")
                st.session_state["div_last_update"] = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(_DIV_RESULTS))) if os.path.exists(_DIV_RESULTS) else time.strftime("%H:%M:%S")
                st.caption(f"⚡ BTC-Divergenz Daten aus Background-Service (vor {_res_age/60:.0f} Min)")
        except Exception:
            pass

    def _div_bg_scan(clear_cache=False):
        """Background-Thread für BTC-Divergenz Scan.
        WICHTIG: st.cache_data funktioniert NICHT in Threads!
        Daher: CoinGecko direkt per requests holen, dann fetch_btc_divergence_shorts
        mit den vorgeladenen Daten füttern via Datei-Cache.
        """
        try:
            import requests as _rq

            with open(_DIV_PROGRESS, "w") as _f:
                json.dump({"status": "running", "checked": 0, "total": 0, "hits": 0,
                           "detail": "📡 Lade CoinGecko Marktdaten (4 Seiten × 250)...",
                           "timestamp": time.time()}, _f)

            # CoinGecko Daten DIREKT laden (kein st.cache_data!)
            # H-14: Vollständigkeit mitzählen — Teilabrufe als partial markieren
            _cg_coins = []
            _cg_pages_ok = 0
            for _pg in range(1, 5):
                try:
                    _cg_resp = _rq.get("https://api.coingecko.com/api/v3/coins/markets",
                        params={"vs_currency": "usd", "order": "market_cap_desc",
                                "per_page": 250, "page": _pg, "sparkline": False,
                                "price_change_percentage": "1h,24h,7d,14d,30d"},
                        timeout=30)
                    if _cg_resp.status_code == 200:
                        _pg_data = _cg_resp.json()
                        if isinstance(_pg_data, list):
                            _cg_coins.extend(_pg_data)
                            _cg_pages_ok += 1
                        with open(_DIV_PROGRESS, "w") as _f:
                            json.dump({"status": "running", "checked": 0, "total": 0, "hits": 0,
                                       "detail": f"📡 CoinGecko Seite {_pg}/4 geladen ({len(_cg_coins)} Coins)...",
                                       "timestamp": time.time()}, _f)
                    elif _cg_resp.status_code == 429:
                        # Rate Limit — nutze was wir haben
                        with open(_DIV_PROGRESS, "w") as _f:
                            json.dump({"status": "running", "checked": 0, "total": 0, "hits": 0,
                                       "detail": f"⚠️ CoinGecko Rate Limit bei Seite {_pg} — nutze {len(_cg_coins)} Coins",
                                       "timestamp": time.time()}, _f)
                        break
                except Exception as _cg_err:
                    print(f"[DIV] CoinGecko Seite {_pg} Fehler: {_cg_err}")
                    if _pg > 1:
                        break  # Mindestens 1 Seite haben wir
                if _pg < 4:
                    time.sleep(3)  # Rate Limit Pause

            if not _cg_coins:
                with open(_DIV_PROGRESS, "w") as _f:
                    json.dump({"status": "error", "detail": "CoinGecko liefert keine Daten",
                               "timestamp": time.time()}, _f)
                return

            # Speichere CoinGecko Daten in Datei damit fetch_btc_divergence_shorts sie nutzen kann
            # H-14 Audit-Fix: Teilabrufe (429) explizit als partial markieren —
            # Konsumenten (_fetch_coingecko_markets, api) behandeln partial als stale
            _CG_CACHE = "/tmp/coingecko_markets_cache.json"
            _cg_partial = (_cg_pages_ok < 4) or (len(_cg_coins) < 1000)
            with open(_CG_CACHE, "w") as _f:
                json.dump({"coins": _cg_coins, "ts": time.time(),
                           "partial": _cg_partial, "pages_fetched": _cg_pages_ok}, _f)

            with open(_DIV_PROGRESS, "w") as _f:
                json.dump({"status": "running", "checked": 0, "total": len(_cg_coins), "hits": 0,
                           "detail": f"📊 {len(_cg_coins)} Coins geladen, starte Divergenz-Analyse...",
                           "timestamp": time.time()}, _f)

            # Jetzt fetch_btc_divergence_shorts aufrufen — die Funktion hat @st.cache_data
            # aber da wir im Thread sind, wird der Dekorator ignoriert/fehlschlagen.
            # Wir rufen die Logik direkt auf mit __wrapped__ wenn verfügbar.
            try:
                # Versuche die unwrapped Version (ohne st.cache_data)
                _fn = getattr(fetch_btc_divergence_shorts, '__wrapped__', fetch_btc_divergence_shorts)
                _dr, _bi, _ds = _fn()
            except Exception:
                # Fallback: direkte Aufruf (kann funktionieren wenn cache_data gracefully faillt)
                _dr, _bi, _ds = fetch_btc_divergence_shorts()

            with open(_DIV_RESULTS, "w") as _f:
                json.dump({"results": _dr, "btc": _bi, "stats": _ds, "ts": time.time()}, _f)
            with open(_DIV_PROGRESS, "w") as _f:
                json.dump({"status": "done", "detail": f"✅ {len(_dr)} Divergenzen gefunden",
                           "timestamp": time.time()}, _f)
        except Exception as _e:
            import traceback
            print(f"[DIV] Background Scan Fehler: {_e}\n{traceback.format_exc()}")
            with open(_DIV_PROGRESS, "w") as _f:
                json.dump({"status": "error", "detail": str(_e), "timestamp": time.time()}, _f)

    _div_should_scan = scan_divergence or (div_auto_refresh and st.session_state.get("div_results") is not None)
    _div_prog = None
    try:
        if os.path.exists(_DIV_PROGRESS):
            with open(_DIV_PROGRESS, "r") as _f:
                _div_prog = json.load(_f)
    except Exception:
        pass
    _div_running = _div_prog and _div_prog.get("status") == "running" and (time.time() - _div_prog.get("timestamp", 0)) < 300

    if _div_should_scan and not _div_running:
        threading.Thread(target=_div_bg_scan, args=(scan_divergence,), daemon=True).start()
        st.toast("📡 BTC-Divergenz Scan gestartet...")
        time.sleep(2)
        st.rerun()

    # ── Progress anzeigen (wie BI Scanner) ──
    if _div_running:
        _dv_checked = _div_prog.get("checked", 0)
        _dv_total = _div_prog.get("total", 0)
        _dv_hits = _div_prog.get("hits", 0)
        _dv_pct = int(_dv_checked / _dv_total * 100) if _dv_total > 0 else 0
        _dv_detail = _div_prog.get("detail", "Scanne...")

        st.info(f"📡 BTC-Divergenz Scan läuft — {_dv_checked}/{_dv_total} ({_dv_pct}%) | {_dv_hits} Treffer | {_dv_detail}")
        if _dv_total > 0:
            st.progress(_dv_pct / 100, text=f"{_dv_checked}/{_dv_total} Coins")

    # ── Ergebnisse laden wenn fertig ──
    if _div_prog and _div_prog.get("status") == "done":
        try:
            with open(_DIV_RESULTS, "r") as _f:
                _div_data = json.load(_f)
            # Alert-Check: Hat sich Timing geändert?
            old_results = st.session_state.get("div_results", [])
            new_results = _div_data.get("results", [])
            if old_results and new_results and div_auto_refresh:
                old_timing = {r.get("Ticker", ""): r.get("Timing", "") for r in old_results}
                for coin in new_results:
                    old_t = old_timing.get(coin.get("Ticker", ""), "")
                    new_t = coin.get("Timing", "")
                    if "JETZT" in new_t and "JETZT" not in old_t and old_t:
                        st.toast(f"🚨 {coin.get('Ticker', '?')} → {new_t}", icon="🔴")
            # Fehlende Felder mit Defaults auffüllen (bg_service hat vereinfachte Analyse)
            _div_defaults = {
                "Timing": "⚪ Früh", "SellProb": 0, "ExhScore": 0, "ExhGrade": "—",
                "GradeEmoji": "⚪", "1h%": 0, "24h%": 0, "14d%": 0, "30d%": 0,
                "7d%": 0, "BestTF": "—", "FundingRate": None, "OI_Ratio": None,
                "Divergenz%": 0, "Div7d%": 0, "Div14d%": 0, "Div30d%": 0,
                "RVOL": 0, "UpperWick%": 0, "MarketCap": 0, "Vol24h": 0,
                "HasPerp": False, "Exchanges": [], "ExhDetails": [],
                "CoinId": "", "BestExchange": "", "Ticker": "?", "Name": "?",
                "Preis": 0,
                "btc_gate": None,  # H-7: None = Altdaten ohne Gate-Info
            }
            for _r in new_results:
                for _dk, _dv in _div_defaults.items():
                    if _dk not in _r:
                        _r[_dk] = _dv
            st.session_state["div_results"] = new_results
            st.session_state["div_btc"] = _div_data.get("btc")
            st.session_state["div_stats"] = _div_data.get("stats")
            st.session_state["div_last_update"] = time.strftime("%H:%M:%S")
            os.remove(_DIV_PROGRESS)
        except Exception:
            pass

    if _div_prog and _div_prog.get("status") == "error":
        st.error(f"❌ Divergenz-Scan Fehler: {_div_prog.get('detail', 'Unbekannt')}")
        if st.button("🔄 Erneut scannen", key="div_retry_btn"):
            try:
                os.remove(_DIV_PROGRESS)
            except Exception:
                pass
            st.rerun()
        try:
            # Auto-Cleanup nach 5 Min
            if time.time() - _div_prog.get("timestamp", 0) > 300:
                os.remove(_DIV_PROGRESS)
        except Exception:
            pass

    # Ergebnisse anzeigen
    div_results = st.session_state.get("div_results")
    btc_info = st.session_state.get("div_btc")
    div_stats = st.session_state.get("div_stats")

    if div_stats and "error" in div_stats:
        if "Rate Limit" in str(div_stats["error"]):
            st.warning("⏳ CoinGecko Rate Limit — Daten werden aus Cache geladen. Einfach nochmal klicken.")
        elif div_stats["error"] == "Keine Daten":
            st.error("🚫 Keine Daten von CoinGecko erhalten — API evtl. nicht erreichbar.")
        else:
            st.warning(f"⚠️ {div_stats['error']}")

    # BTC-Bullish-Warnung (Scan läuft trotzdem, aber Qualität der Setups ist geringer)
    if div_stats and div_stats.get("btc_bullish"):
        st.warning(
            f"⚠️ **BTC ist auf allen Zeitfenstern bullisch** "
            f"(7d: {div_stats.get('btc_7d', 0):+.1f}%) — "
            f"Divergenz-Setups sind weniger zuverlässig wenn BTC stark ist. "
            f"Die besten Shorts kommen wenn BTC auf mind. einem Zeitfenster schwach ist. "
            f"Ergebnisse unten als **Watch Only** markiert."
        )

    if btc_info:
        st.markdown("---")
        # Zeile 1: BTC Preis + kurzfristige Daten
        bc1, bc2, bc3, bc4 = st.columns(4)
        bc1.metric("₿ BTC Preis", f"${btc_info.get('price', 0):,.0f}")
        bc2.metric("BTC 1h", f"{btc_info.get('change_1h', 0):+.1f}%",
                    delta=f"{btc_info.get('change_1h', 0):+.1f}%",
                    delta_color="inverse")
        _btc_24h = btc_info.get('change_24h', 0) or 0
        bc3.metric("BTC 24h", f"{_btc_24h:+.1f}%",
                    delta=f"{_btc_24h:+.1f}%",
                    delta_color="inverse")
        if div_stats and "candidates" in div_stats:
            bc4.metric("Kandidaten", f"{div_stats['candidates']}", f"von {div_stats.get('scanned', 0)} gescannt")

        # Zeile 2: BTC Multi-Timeframe Trend
        bt1, bt2, bt3 = st.columns(3)
        btc_7d_val = btc_info.get('change_7d', 0) or 0
        btc_14d_val = btc_info.get('change_14d', 0)
        btc_30d_val = btc_info.get('change_30d', 0)
        bt1.metric("BTC 7d", f"{btc_7d_val:+.1f}%",
                    delta=f"{btc_7d_val:+.1f}%", delta_color="inverse")
        bt2.metric("BTC 14d", f"{btc_14d_val:+.1f}%",
                    delta=f"{btc_14d_val:+.1f}%", delta_color="inverse")
        bt3.metric("BTC 30d", f"{btc_30d_val:+.1f}%",
                    delta=f"{btc_30d_val:+.1f}%", delta_color="inverse")

        # Letztes Update anzeigen
        last_update = st.session_state.get("div_last_update")
        if last_update:
            st.caption(f"🕐 Letztes Update: {last_update}" + (" · Auto-Refresh aktiv" if st.session_state.get("div_auto_refresh") else ""))

    if div_results:
        st.markdown("---")

        # ── Echtzeit-Alerts: Coins die JETZT kippen ──
        # H-7 Audit-Fix: Im Watch-Only-Modus (BTC bullisch) KEINE rote Alarmbox —
        # ohne BTC-Schwäche gibt es kein Short-Timing, nur Beobachtung.
        is_watch_only = bool(div_stats and div_stats.get("btc_bullish", False))
        jetzt_coins = [c for c in div_results
                       if "JETZT" in c.get("Timing", "") and c.get("btc_gate", True) is not False]
        if is_watch_only:
            st.info("👁️ **BTC ist stark — Watch-Only-Modus.** Keine aktiven Short-Signale; "
                    "überdehnte Coins unten nur beobachten (kein Short-Timing ohne BTC-Schwäche).")
            st.markdown("---")
        elif jetzt_coins:
            st.error(f"🚨 **{len(jetzt_coins)} AKTIVE SHORT-SIGNALE** — Diese Coins kippen gerade! "
                     f"(kein definierter Stop — Beobachtungssignale, Entry/Stop selbst setzen)")
            for jc in jetzt_coins:
                st.markdown(
                    f"  **{jc.get('Ticker', '?')}** — 1h: **{jc.get('1h%', 0):+.1f}%** | "
                    f"7d: {jc.get('7d%', 0):+.1f}% | Exhaustion: {jc.get('ExhScore', 0)}/100 | "
                    f"{jc.get('Timing', '')}"
                )
            st.markdown("---")
        if is_watch_only:
            st.subheader(f"👁️ {len(div_results)} Coins auf Watchlist (BTC bullisch — Watch Only)")
        else:
            st.subheader(f"🎯 {len(div_results)} Short-Kandidaten gefunden")

        # ── Klickbare Tabelle (wie BI Scanner) ──
        import pandas as pd
        div_df = pd.DataFrame(div_results)
        div_num = len(div_df)

        # Display-Spalten für Tabelle — sortiert nach SellProb (höchste zuerst)
        div_display_data = {
            "Ticker": div_df["Ticker"].tolist(),
            "Sell%": div_df["SellProb"].tolist(),
            "Exh": div_df["ExhScore"].tolist(),
            "1h%": [f"{v:+.1f}%" for v in div_df["1h%"].tolist()],
            "7d%": [f"{v:+.1f}%" for v in div_df["7d%"].tolist()],
            "14d%": [f"{v:+.1f}%" for v in div_df["14d%"].tolist()],
            "Div%": [f"+{v:.0f}%" for v in div_df["Divergenz%"].tolist()],
            "BestTF": div_df["BestTF"].tolist(),
            "FR": [f"{v*100:+.3f}%" if v is not None else "—" for v in div_df["FundingRate"].tolist()],
            "OI/Vol": [f"{v:.1f}x" if v is not None else "—" for v in div_df["OI_Ratio"].tolist()],
            "RSI": [f"{v:.0f}" if v is not None and v == v else "—" for v in div_df["RSI14"].tolist()] if "RSI14" in div_df.columns else [],
            "OI Δ%": [f"{v:+.1f}%" if v is not None and v == v else "—" for v in div_df["OI_Delta%"].tolist()] if "OI_Delta%" in div_df.columns else [],
            "BTC Dom": [f"{v:.1f}%" if v is not None and v == v else "—" for v in div_df["BTCDominance"].tolist()] if "BTCDominance" in div_df.columns else [],
        }
        # Entferne leere Spalten (wenn Daten nicht vorhanden)
        div_display_data = {k: v for k, v in div_display_data.items() if v}
        # Timing kürzen für Tabelle
        timing_short = []
        for t in div_df["Timing"].tolist():
            if "SHORTEN" in t: timing_short.append("🔴 JETZT")
            elif "JETZT" in t: timing_short.append("🟢 JETZT")
            elif "BEREIT" in t: timing_short.append("🟡 BEREIT")
            elif "WATCHLIST" in t: timing_short.append("🟠 WATCH")
            else: timing_short.append("⚪ FRÜH")
        div_display_data["Timing"] = timing_short

        div_table_df = pd.DataFrame(div_display_data)

        # Aktuelle Auswahl
        div_sel_idx = st.session_state.get("div_sel_idx", 0)
        div_sel_idx = min(div_sel_idx, div_num - 1)

        # Dataframe mit Selektion
        div_event = st.dataframe(
            div_table_df,
            use_container_width=True,
            height=min(600, 40 + div_num * 35),
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="div_tab_df_select"
        )

        # Auswahl aus Dataframe-Klick übernehmen
        if div_event and div_event.selection and div_event.selection.rows:
            div_sel_idx = div_event.selection.rows[0]
            st.session_state["div_sel_idx"] = div_sel_idx

        # ── Keyboard Navigation: W (zurück) / E (vor) ──
        div_nav1, div_nav2, div_nav3 = st.columns([1, 3, 1])
        with div_nav1:
            if st.button("◀ Zurück (W)", key="div_nav_prev", use_container_width=True, disabled=div_sel_idx <= 0):
                st.session_state["div_sel_idx"] = max(0, div_sel_idx - 1)
                st.rerun()
        with div_nav2:
            st.caption(f"📌 **{div_sel_idx + 1} / {div_num}** — Klicke Zeile in Tabelle oder nutze ◀ ▶ Buttons (Tastatur: **W** = zurück / **E** = vor)")
        with div_nav3:
            if st.button("Vor ▶ (E)", key="div_nav_next", use_container_width=True, disabled=div_sel_idx >= div_num - 1):
                st.session_state["div_sel_idx"] = min(div_num - 1, div_sel_idx + 1)
                st.rerun()

        # Keyboard shortcuts via JS — robust über parent.document mit Fallback
        st.components.v1.html(f"""
        <script>
        (function() {{
            function findAndClickBtn(text) {{
                // Suche in parent (Streamlit Main Frame)
                try {{
                    var doc = window.parent.document;
                    var btns = doc.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {{
                        if (btns[i].textContent.indexOf(text) !== -1) {{
                            btns[i].click();
                            return true;
                        }}
                    }}
                }} catch(e) {{}}
                // Fallback: eigenes document
                var btns2 = document.querySelectorAll('button');
                for (var j = 0; j < btns2.length; j++) {{
                    if (btns2[j].textContent.indexOf(text) !== -1) {{
                        btns2[j].click();
                        return true;
                    }}
                }}
                return false;
            }}
            function onKeyHandler(e) {{
                var ae = window.parent.document.activeElement || e.target || {{}};
                var tag = (ae.tagName || '').toLowerCase();
                if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
                if (ae.isContentEditable || (ae.getAttribute && ae.getAttribute('role') === 'textbox')) return;
                if ((e.target.tagName||'').toLowerCase() === 'input') return;
                if (e.key === 'w' || e.key === 'W') {{
                    e.preventDefault();
                    findAndClickBtn('Zur\\u00fcck');
                }}
                if (e.key === 'e' || e.key === 'E') {{
                    e.preventDefault();
                    findAndClickBtn('Vor');
                }}
            }}
            // Registriere auf parent document (Streamlit Main Frame)
            try {{
                var pdoc = window.parent.document;
                if (pdoc._divNavHandler) pdoc.removeEventListener('keydown', pdoc._divNavHandler);
                pdoc._divNavHandler = onKeyHandler;
                pdoc.addEventListener('keydown', onKeyHandler);
            }} catch(e) {{
                document.addEventListener('keydown', onKeyHandler);
            }}
        }})();
        </script>
        """, height=0)

        # ── Detail-Ansicht für ausgewählten Coin ──
        coin = div_results[div_sel_idx]
        grade = coin.get("ExhGrade", "—")
        emoji = coin.get("GradeEmoji", "⚪")
        timing = coin.get("Timing", "⚪ Früh")

        st.divider()
        # Sell-Off Probability Badge
        _sp = coin.get("SellProb", 0)
        if _sp >= 70:
            _sp_color = "🔴"
            _sp_label = "SEHR WAHRSCHEINLICH"
        elif _sp >= 50:
            _sp_color = "🟠"
            _sp_label = "WAHRSCHEINLICH"
        elif _sp >= 35:
            _sp_color = "🟡"
            _sp_label = "MÖGLICH"
        else:
            _sp_color = "⚪"
            _sp_label = "UNWAHRSCHEINLICH"
        _c_ticker = coin.get('Ticker', '?')
        _c_name = coin.get('Name', '?')
        _c_preis = coin.get('Preis', 0) or 0
        _c_1h = coin.get('1h%', 0) or 0
        _c_24h = coin.get('24h%', 0) or 0
        _c_7d = coin.get('7d%', 0) or 0
        _c_14d = coin.get('14d%', 0) or 0
        _c_30d = coin.get('30d%', 0) or 0
        _c_exh = coin.get('ExhScore', 0) or 0
        _c_div = coin.get('Divergenz%', 0) or 0
        _c_rvol = coin.get('RVOL', 0) or 0
        _c_uw = coin.get('UpperWick%', 0) or 0
        _c_mcap = coin.get('MarketCap', 0) or 0
        _c_vol = coin.get('Vol24h', 0) or 0

        st.subheader(f"{emoji} {_c_ticker} — {_c_name}")
        st.markdown(f"**Sell-Off:** {_sp_color} **{_sp}/100** ({_sp_label}) · **Timing:** {timing}")

        # Zeile 1: Preis, 1h, 24h, Exhaustion, SellProb
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Preis", f"${_c_preis:.4f}" if _c_preis < 1 else f"${_c_preis:.2f}")
        mc2.metric("1h", f"{_c_1h:+.1f}%",
                   delta=f"{_c_1h:+.1f}%",
                   delta_color="inverse")
        mc3.metric("24h", f"{_c_24h:+.1f}%",
                   delta=f"{_c_24h:+.1f}%",
                   delta_color="inverse")
        mc4.metric("Exhaustion", f"{_c_exh}/100 ({grade})")
        mc5.metric("Sell-Off %", f"{_sp}/100", help="Composite: Exhaustion + Timing + Preis-Position + Tradebarkeit")

        # Zeile 2: Multi-Timeframe Performance + Divergenz
        tf1, tf2, tf3, tf4 = st.columns(4)
        tf1.metric("7d", f"{_c_7d:+.1f}%", delta=f"Div: +{coin.get('Div7d%', 0):.0f}%")
        tf2.metric("14d", f"{_c_14d:+.1f}%", delta=f"Div: +{coin.get('Div14d%', 0):.0f}%")
        tf3.metric("30d", f"{_c_30d:+.1f}%", delta=f"Div: +{coin.get('Div30d%', 0):.0f}%")
        tf4.metric("Beste Divergenz", f"+{_c_div:.0f}%", delta=f"auf {coin.get('BestTF', '7d')}")

        # Zeile 3: Funding Rate + Open Interest + Kontext
        fr3, fr4, fr5, fr6 = st.columns(4)
        _fr_val = coin.get("FundingRate")
        _oi_val = coin.get("OI_Ratio")
        _has_perp = coin.get("HasPerp", False)
        if _has_perp and _fr_val is not None:
            _fr_color = "inverse" if _fr_val > 0 else "normal"  # Positive FR = schlecht für Longs
            fr3.metric("Funding Rate", f"{_fr_val*100:+.4f}%",
                       delta="Longs zahlen" if _fr_val > 0.0001 else ("Shorts zahlen" if _fr_val < -0.0001 else "Neutral"),
                       delta_color=_fr_color)
        else:
            fr3.metric("Funding Rate", "—", delta="Kein Perp auf MEXC")
        if _has_perp and _oi_val is not None:
            fr4.metric("OI/Volume", f"{_oi_val:.1f}x",
                       delta="Überhebelt!" if _oi_val >= 3.0 else ("Erhöht" if _oi_val >= 1.5 else "Normal"))
        else:
            fr4.metric("OI/Volume", "—")
        fr5.metric("RVOL", f"{_c_rvol:.1f}x")
        fr6.metric("Upper Wick", f"{_c_uw:.0f}%")

        # Zeile 3b: Neue V3/V4 Metriken
        _rsi_val = coin.get("RSI14")
        _oi_delta = coin.get("OI_Delta%")
        _btc_dom = coin.get("BTCDominance")
        _liq_long = coin.get("LiqLong", 0)
        _liq_short = coin.get("LiqShort", 0)
        _dom_f = coin.get("DomFactor", 1.0)
        _liq_f = coin.get("LiqFactor", 1.0)
        _oi_f = coin.get("OI_Factor", 1.0)

        v4a, v4b, v4c, v4d = st.columns(4)
        _rsi_ok = _rsi_val is not None and _rsi_val == _rsi_val  # NaN-safe
        _oi_ok = _oi_delta is not None and _oi_delta == _oi_delta
        v4a.metric("RSI (14)", f"{_rsi_val:.0f}" if _rsi_ok else "—",
                   delta="Überkauft!" if _rsi_ok and _rsi_val >= 70 else ("Neutral" if _rsi_ok and _rsi_val >= 40 else "Überverkauft" if _rsi_ok else ""))
        v4b.metric("OI Δ 24h", f"{_oi_delta:+.1f}%" if _oi_ok else "—",
                   delta="Positionen aufgebaut" if _oi_ok and _oi_delta > 10 else ("Stabil" if _oi_ok and abs(_oi_delta) <= 10 else ""))
        v4c.metric("BTC Dominance", f"{_btc_dom:.1f}%" if _btc_dom else "—",
                   delta=f"Faktor: {_dom_f:.2f}" if _btc_dom else "")
        _liq_info_str = "—"
        if _liq_long > 0 or _liq_short > 0:
            _liq_info_str = f"L:${_liq_long/1e6:.1f}M / S:${_liq_short/1e6:.1f}M"
        v4d.metric("Liquidations", _liq_info_str,
                   delta=f"Faktor: {_liq_f:.2f}" if _liq_f != 1.0 else "")

        # Kontext-Info
        st.caption(
            f"MarketCap: ${_c_mcap/1e6:.0f}M · "
            f"Vol24h: ${_c_vol/1e6:.0f}M · "
            f"{'🟢 Perp: ' + ' + '.join(coin.get('Exchanges', [])) if _has_perp else '⚪ Kein Perp'}"
        )

        # Exhaustion-Analyse Details
        with st.expander("🔬 Exhaustion-Analyse (8 Dimensionen)", expanded=False):
            for detail in coin.get("ExhDetails", []):
                st.markdown(f"  {detail}")

        # ── TradingView Chart (eingebettet) ──
        st.divider()
        # TradingView Symbol: Coin + USDT.P (Perpetual) auf MEXC oder Bybit,
        # Fallback auf Binance Spot. TradingView sucht automatisch die beste Exchange.
        _div_ticker = _c_ticker.upper()
        _div_coinid = coin.get('CoinId', '').lower()

        # Spezial-Mappings für bekannte Abweichungen
        _tv_symbol_overrides = {
            "MIOTA": "IOTA", "IOT": "IOTA", "XDG": "DOGE",
        }
        _div_ticker_tv = _tv_symbol_overrides.get(_div_ticker, _div_ticker)

        # Versuche mehrere Exchanges in Prioritätsreihenfolge
        # Bestes Exchange zuerst (wo höchstes Volume), dann Fallbacks
        _best_exch = coin.get("BestExchange", "")
        _primary_exch = "BITGET" if _best_exch == "Bitget" else "MEXC"
        _secondary_exch = "MEXC" if _primary_exch == "BITGET" else "BITGET"
        _div_tv_symbols = [
            f"{_primary_exch}:{_div_ticker_tv}USDT.P",    # Bestes Exchange Perpetual
            f"{_secondary_exch}:{_div_ticker_tv}USDT.P",  # Zweites Exchange Perpetual
            f"BYBIT:{_div_ticker_tv}USDT.P",               # Bybit Perpetual
            f"BINANCE:{_div_ticker_tv}USDT",                # Binance Spot
            f"COINBASE:{_div_ticker_tv}USD",                 # Coinbase
        ]
        # Primäres Symbol (TradingView zeigt Error und erlaubt Symbol-Wechsel wenn nicht gefunden)
        div_tv_full = _div_tv_symbols[0]

        div_tv_html = f'''
        <div style="height:500px; border-radius: 8px; overflow: hidden;">
            <div id="div_tv_chart_{div_sel_idx}" style="height:100%"></div>
            <script src="https://s3.tradingview.com/tv.js"></script>
            <script>
                // Versuche mehrere Exchanges — TradingView zeigt den ersten gültigen
                var symbols = {json.dumps(_div_tv_symbols)};
                var currentTry = 0;
                function createChart(sym) {{
                    try {{
                        new TradingView.widget({{
                            "autosize": true,
                            "symbol": sym,
                            "interval": "60",
                            "timezone": "Europe/Berlin",
                            "theme": "dark",
                            "style": "1",
                            "locale": "de_DE",
                            "enable_publishing": false,
                            "hide_side_toolbar": false,
                            "allow_symbol_change": true,
                            "studies": ["Volume@tv-basicstudies", "RSI@tv-basicstudies"],
                            "container_id": "div_tv_chart_{div_sel_idx}",
                            "range": "1M"
                        }});
                    }} catch(e) {{
                        currentTry++;
                        if (currentTry < symbols.length) createChart(symbols[currentTry]);
                    }}
                }}
                createChart(symbols[0]);
            </script>
        </div>
        '''
        st.components.v1.html(div_tv_html, height=500)
        st.caption(f"📊 Chart: {div_tv_full} | Falls Symbol nicht gefunden: oben im Chart nach **{_div_ticker_tv}USDT** suchen")

    elif div_results is not None and len(div_results) == 0 and btc_info:
        st.info("Keine Coins mit genug Divergenz gefunden. Kriterien: Altcoin 7d > +10% UND Divergenz vs BTC > 10%.")

    # ── 🔴 CRYPTO RISK-OFF TRACKER (zusätzliche Sektion, ändert bestehende Logik nicht) ──
    st.divider()
    with st.expander("🔴 **Crypto Risk-Off Tracker** — Welche Coins fallen am stärksten wenn SPY kippt?", expanded=False):
        st.caption("Zeigt Coins die bei Markt-Stress am verwundbarsten sind: hohe MCap-Verluste, negatives Momentum, Funding negativ")

        _roff_data = st.session_state.get("div_results")
        _roff_btc = st.session_state.get("div_btc")
        if _roff_btc and _roff_data:
            # BTC als Benchmark
            _btc_7d = _roff_btc.get("change_7d", 0)
            _btc_30d = _roff_btc.get("change_30d", 0)

            st.markdown(f"**BTC Benchmark:** 7d `{_btc_7d:+.1f}%` · 30d `{_btc_30d:+.1f}%`")

            if _btc_7d < -3 or _btc_30d < -5:
                st.error("🔴 **BTC ist schwach** — Risk-Off für Crypto aktiv. Altcoins fallen typisch 2-3x stärker.")
            elif _btc_7d < 0:
                st.warning("🟡 **BTC leicht negativ** — Vorsicht bei Altcoin-Longs")

            # Risk-Off Ranking: Coins die am stärksten gegen BTC gefallen sind
            _roff_candidates = []
            for _r in _roff_data:
                _r_7d = _r.get("Change7d", _r.get("7d%", 0))
                _r_30d = _r.get("Change30d", _r.get("30d%", 0))
                _roff_candidates.append({
                    "Ticker": _r.get("Ticker", ""),
                    "7d%": round(_r_7d, 1),
                    "30d%": round(_r_30d, 1),
                    "ExhScore": _r.get("ExhScore", 0),
                    "Timing": _r.get("Timing", ""),
                })

            if _roff_candidates:
                st.markdown("**🐻 Top Short-Targets bei Risk-Off (aus Divergenz-Daten):**")
                import pandas as pd
                _roff_df = pd.DataFrame(_roff_candidates)
                _roff_df = _roff_df.sort_values("ExhScore", ascending=False).head(15)
                st.dataframe(_roff_df, use_container_width=True, hide_index=True)

                st.markdown("""
                **💡 Risk-Off Crypto Strategie:**
                - **Funding negativ + BTC schwach** = Shorts bereits überfüllt → Squeeze-Gefahr!
                - **ExhScore > 60 + BTC schwach** = Bestes Short-Setup (Altcoin hat gepumpt ohne Rückenwind)
                - **BTC -5% oder mehr** = Altcoins fallen typisch 10-30%, besonders Small/Mid-Caps
                - Für Short-Execution: **Bitget/MEXC** Perps nutzen (siehe BTC-Divergenz Ergebnisse oben)
                """)
        else:
            st.info("💡 Erst einen **Divergenz-Scan** oben starten — die Daten werden dann hier für den Risk-Off Tracker genutzt.")


# -----------------------------------------------------------------------------
# 🔥 EARLY MOVERS TAB - Volume Spikes, Micro-Caps, Narrative Tracker
# -----------------------------------------------------------------------------
with tab_early:
    st.subheader("🔥 Early Movers Scanner")
    st.caption("Finde die nächsten 10x-Coins bevor sie explodieren — Volume Spikes, Micro-Cap Momentum, Sektor-Rotation")

    # ── Progress/Status lesen ──
    _early_prog = _early_progress_read()
    _early_is_running = _early_prog and _early_prog.get("status") == "running" and not _early_should_stop()

    # ── Scan läuft → Progress + Stop-Button ──
    if _early_is_running:
        _ep_detail = _early_prog.get("detail", "Scanne...")
        _ep_pct = _early_prog.get("pct", 0)
        st.info(f"🔥 **Early Movers Scan läuft** — {_ep_detail}")
        _early_col1, _early_col2 = st.columns([5, 1])
        with _early_col1:
            st.progress(min(1.0, _ep_pct / 100))
        with _early_col2:
            if st.button("⏹️ Stop", key="early_stop_btn", use_container_width=True, type="secondary"):
                _early_request_stop()
                _early_progress_write("stopped", "⏹️ Manuell gestoppt")
                st.toast("⏹️ Early Movers Scan wird gestoppt...")
                time.sleep(1)
                st.rerun()
        # Progress-Update: nutze globalen Sidebar Auto-Refresh statt eigenem

    # ── Scan fertig → Ergebnisse laden ──
    elif _early_prog and _early_prog.get("status") == "done":
        _cached = _early_results_load()
        if _cached:
            st.session_state["early_data"] = _cached
        _early_progress_clear()

    # ── Scan gestoppt ──
    elif _early_prog and _early_prog.get("status") == "stopped":
        st.warning(f"⏹️ {_early_prog.get('detail', 'Gestoppt')}")
        _early_clear_stop()
        _early_progress_clear()

    # ── Fehler ──
    elif _early_prog and _early_prog.get("status") == "error":
        st.error(f"❌ {_early_prog.get('detail', 'Fehler')}")
        _early_progress_clear()

    # ── Scan-Button (nur wenn kein Scan läuft) ──
    if not _early_is_running:
        if st.button("🔥 EARLY MOVERS SCANNEN", type="primary", use_container_width=True, key="early_scan_btn"):
            st.session_state["early_data"] = None
            _fetch_coingecko_markets.clear()
            fetch_early_movers.clear()
            _early_clear_stop()
            import threading
            _early_thread = threading.Thread(target=_early_background_scan, daemon=True)
            _early_thread.start()
            st.toast("🔥 Early Movers Scan gestartet...")
            time.sleep(2)
            st.rerun()

    # ── Auto-Load wenn noch keine Daten ──
    if "early_data" not in st.session_state or st.session_state.get("early_data") is None:
        if not _early_is_running:
            # Versuche aus Cache-File zu laden
            _cached = _early_results_load()
            if _cached:
                st.session_state["early_data"] = _cached
            else:
                st.info("💡 Klicke auf **EARLY MOVERS SCANNEN** um den Scan zu starten.")

    early_data = st.session_state.get("early_data") or {}
    stats = early_data.get("stats", {}) if early_data else {}

    if "error" in stats:
        st.error(f"❌ {stats['error']}")
        st.info("💡 **Tipp:** CoinGecko Free API hat Rate Limits (~30 Req/Min). "
                "Klicke nochmals auf 'EARLY MOVERS SCANNEN' — beim zweiten Versuch klappt es meistens.")
    else:
        # Stats Header
        st.markdown(f"""
        **BTC 7d:** `{stats.get('btc_7d', 0):+.1f}%` ·
        **Volume Spikes:** `{stats.get('volume_spikes', 0)}` ·
        **Whale Signals:** `{stats.get('whale_acc', 0)}` ·
        **Micro-Caps:** `{stats.get('micro_caps', 0)}` ·
        **Neu gelistet:** `{stats.get('recently_listed', 0)}` ·
        **Trending:** `{stats.get('trending_coins', 0)}` ·
        **Sektoren:** `{stats.get('narratives', 0)}` ·
        **Perps:** `{stats.get('perps_bitget', 0)} Bitget + {stats.get('perps_mexc', 0)} MEXC`
        """)

        early_tab1, early_tab2, early_tab3, early_tab4, early_tab5 = st.tabs(["📊 Volume Spikes", "🐋 Whale Accumulation", "🔥 Micro-Cap Degen", "🆕 Neu Gelistet", "🏷️ Narrative Tracker"])

        # ── TAB 1: Volume Spikes (nur bullish!) ──
        with early_tab1:
            st.markdown("### 📊 Volume Akkumulation (Bullish)")
            st.caption("Coins mit hohem Kaufvolumen — Abverkäufe werden gefiltert. Preis nahe 24h-High = Käufer dominant.")

            vol_spikes = early_data.get("volume_spikes", [])
            if not vol_spikes:
                st.info("Keine Volume Spikes gefunden. Markt ist ruhig.")
            else:
                for i, coin in enumerate(vol_spikes[:15]):
                    symbol = coin["Symbol"]
                    score = coin.get("EarlyScore", 0)
                    signal = coin.get("Signal", "")
                    exch_list = coin.get("Exchanges", [])
                    best_exch = coin.get("BestExchange", "")
                    if coin["HasPerp"]:
                        perp_tag = " + ".join(exch_list) if exch_list else "Perp"
                    else:
                        perp_tag = "⚠️ kein Perp"

                    # Color based on score
                    if score >= 70:
                        score_color = "🟢"
                    elif score >= 50:
                        score_color = "🟡"
                    else:
                        score_color = "🟠"

                    with st.expander(f"{score_color} **{symbol}** — Score {score}/100 · {signal} · `{perp_tag}`", expanded=(i < 3)):
                        c1, c2, c3, c4 = st.columns(4)
                        _cp = coin.get('Price', 0) or 0
                        c1.metric("Preis", f"${_cp:.4f}" if _cp < 1 else f"${_cp:.2f}")
                        c2.metric("MCap", f"${(coin.get('MCap', 0) or 0)/1e6:.1f}M")
                        c3.metric("Vol/MCap", f"{coin.get('VolMCapRatio', 0) or 0:.0f}%", help="Normal: 5-15%. >30% = ungewöhnlich")
                        c4.metric("24h Vol", f"${(coin.get('Vol24h', 0) or 0)/1e6:.1f}M")

                        c5, c6, c7, c8, c9 = st.columns(5)
                        c5.metric("1h", f"{coin.get('Change1h', 0) or 0:+.1f}%")
                        c6.metric("24h", f"{coin.get('Change24h', 0) or 0:+.1f}%")
                        c7.metric("7d", f"{coin.get('Change7d', 0) or 0:+.1f}%")
                        c8.metric("30d", f"{coin.get('Change30d', 0) or 0:+.1f}%")
                        # Price Position: Wo steht der Preis im 24h-Range
                        pp = coin.get("PricePosition", 0.5)
                        pp_label = "🟢 Käufer" if pp >= 0.7 else "🟡 Neutral" if pp >= 0.4 else "🔴 Verkäufer"
                        c9.metric("24h Position", f"{pp*100:.0f}%", help=f"{pp_label} — 100% = am Tageshoch, 0% = am Tagestief")

                        if coin.get("HasPerp") and best_exch:
                            tv_prefix = "BITGET" if best_exch == "Bitget" else "MEXC"
                            fr_val = (coin.get('FundingRate', 0) or 0) * 100
                            st.markdown(f"📈 **TradingView:** `{tv_prefix}:{symbol}USDT.P` · **Funding:** {fr_val:.4f}% · **Exchange:** {' + '.join(exch_list)}")
                        if coin.get("Narrative"):
                            st.markdown(f"**Narrativ:** {coin.get('Narrative', '')}")

        # ── TAB 2: Whale Accumulation ──
        with early_tab2:
            st.markdown("### 🐋 Whale Accumulation Detector")
            st.caption("Hohes OI/Vol Ratio + positive Funding = Überzeugung im Markt. FR-Flip = Short Squeeze Potential.")
            st.info("ℹ️ **Hinweis:** OI/Vol Ratio zeigt das aktuelle Verhältnis, nicht die Veränderung. "
                    "Hohes Ratio kann Akkumulation ODER festsitzende Positionen bedeuten. "
                    "Kombiniere mit 24h/7d-Trend für bessere Einschätzung.", icon="ℹ️")

            whale_acc = early_data.get("whale_acc", [])
            if not whale_acc:
                st.info("Keine Whale-Accumulation-Signale gefunden. Die großen Fische sind noch ruhig.")
            else:
                for i, coin in enumerate(whale_acc[:15]):
                    symbol = coin["Symbol"]
                    w_score = coin.get("WhaleScore", 0)
                    signals = coin.get("Signals", [])
                    exch_list = coin.get("Exchanges", [])
                    best_exch = coin.get("BestExchange", "")
                    exch_tag = " + ".join(exch_list) if exch_list else "?"

                    if w_score >= 70:
                        emoji = "🐋"
                    elif w_score >= 50:
                        emoji = "🦈"
                    else:
                        emoji = "🐟"

                    with st.expander(f"{emoji} **{symbol}** — Whale Score {w_score}/100 · `{exch_tag}` · OI/Vol {coin.get('OI_Ratio', 0):.1f}x", expanded=(i < 3)):
                        c1, c2, c3, c4 = st.columns(4)
                        _wp = coin.get('Price', 0) or 0
                        c1.metric("Preis", f"${_wp:.4f}" if _wp < 1 else f"${_wp:.2f}")
                        c2.metric("MCap", f"${(coin.get('MCap', 0) or 0)/1e6:.1f}M")
                        c3.metric("OI/Vol Ratio", f"{coin.get('OI_Ratio', 0):.2f}x", help="Wie stark gehebelt wird. >1.5 = Positionen werden aufgebaut")
                        fr_val = coin.get("FundingRate", 0) * 100
                        c4.metric("Funding Rate", f"{fr_val:+.4f}%", help="Positiv = Longs dominant, Negativ = Shorts dominant")

                        c5, c6, c7, c8 = st.columns(4)
                        c5.metric("1h", f"{coin.get('Change1h', 0) or 0:+.1f}%")
                        c6.metric("24h", f"{coin.get('Change24h', 0) or 0:+.1f}%")
                        c7.metric("7d", f"{coin.get('Change7d', 0) or 0:+.1f}%")
                        c8.metric("24h Vol", f"${(coin.get('Vol24h', 0) or 0)/1e6:.1f}M")

                        # Signals anzeigen
                        if signals:
                            st.markdown("**Signale:**")
                            for sig in signals:
                                st.markdown(f"- {sig}")

                        # TradingView Link mit bestem Exchange
                        if best_exch:
                            tv_prefix = "BITGET" if best_exch == "Bitget" else "MEXC"
                            st.markdown(f"📈 **TradingView:** `{tv_prefix}:{symbol}USDT.P` · **Exchange:** {exch_tag}")

                        if coin.get("Narrative"):
                            st.markdown(f"**Narrativ:** {coin.get('Narrative', '')}")

        # ── TAB 3: Micro-Cap Degen ──
        with early_tab3:
            st.markdown("### 🔥 Micro-Cap Momentum")
            st.caption("Kleine Coins ($1M-$50M MCap) die gerade anfangen zu laufen — hohes Risiko, hohes Potential")
            st.warning("⚠️ DEGEN ZONE: Micro-Caps sind extrem volatil und oft illiquid. Nur mit Spielgeld!")

            micro_caps = early_data.get("micro_caps", [])
            if not micro_caps:
                st.info("Keine Micro-Cap Movers gefunden.")
            else:
                for i, coin in enumerate(micro_caps[:15]):
                    symbol = coin.get("Symbol", "?")
                    score = coin.get("DegenScore", 0)
                    exch_list = coin.get("Exchanges", [])
                    best_exch = coin.get("BestExchange", "")
                    if coin.get("HasPerp"):
                        perp_tag = "✅ " + (" + ".join(exch_list) if exch_list else "Perp")
                    else:
                        perp_tag = "⚠️ kein Perp"

                    if score >= 70:
                        emoji = "🚀"
                    elif score >= 50:
                        emoji = "🔥"
                    else:
                        emoji = "💫"

                    _dm = coin.get('MCap', 0) or 0
                    _dp = coin.get('Price', 0) or 0
                    with st.expander(f"{emoji} **{symbol}** — Degen Score {score}/100 · MCap ${_dm/1e6:.1f}M · `{perp_tag}`", expanded=(i < 3)):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Preis", f"${_dp:.6f}" if _dp < 0.01 else f"${_dp:.4f}" if _dp < 1 else f"${_dp:.2f}")
                        c2.metric("MCap", f"${_dm/1e6:.2f}M")
                        c3.metric("7d Change", f"{coin.get('Change7d', 0) or 0:+.1f}%")
                        c4.metric("Vol/MCap", f"{coin.get('VolMCapRatio', 0) or 0:.0f}%")

                        c5, c6, c7, c8 = st.columns(4)
                        c5.metric("1h", f"{coin.get('Change1h', 0) or 0:+.1f}%")
                        c6.metric("24h", f"{coin.get('Change24h', 0) or 0:+.1f}%")
                        c7.metric("14d", f"{coin.get('Change14d', 0) or 0:+.1f}%")
                        c8.metric("24h Vol", f"${(coin.get('Vol24h', 0) or 0)/1e6:.1f}M")

                        # Potential-Berechnung
                        if _dm > 0:
                            to_100m = (100_000_000 / _dm - 1) * 100
                            to_500m = (500_000_000 / _dm - 1) * 100
                            st.markdown(f"**Potential:** Zu $100M MCap = **+{to_100m:.0f}%** · Zu $500M MCap = **+{to_500m:.0f}%**")

                        if coin.get("HasPerp") and best_exch:
                            tv_prefix = "BITGET" if best_exch == "Bitget" else "MEXC"
                            st.markdown(f"📈 `{tv_prefix}:{symbol}USDT.P` · {' + '.join(exch_list)}")

        # ── TAB 4: Neu Gelistet (FIX 9) ──
        with early_tab4:
            st.markdown("### 🆕 Neu Gelistete Coins")
            st.caption("Coins die erst kürzlich auf CoinGecko gelistet wurden (< 14 Tage). Frische Listings = hohes Momentum-Potential.")

            _newly = early_data.get("recently_listed", [])
            if not _newly:
                st.info("Keine neuen Listings mit positivem Momentum gefunden.")
            else:
                import pandas as _pd_nl
                _nl_df = _pd_nl.DataFrame(_newly)
                _nl_cols = ["Symbol", "Name", "Price", "Change1h", "Change24h", "Change7d",
                            "MCap", "Vol24h", "VolMCapRatio", "NewScore", "Signal"]
                _nl_show = [c for c in _nl_cols if c in _nl_df.columns]
                st.dataframe(_nl_df[_nl_show].head(20), use_container_width=True, hide_index=True)

        # ── TAB 5: Narrative Tracker ──
        with early_tab5:
            st.markdown("### 🏷️ Sektor-Rotation Tracker")
            st.caption("Welcher Narrativ zieht gerade Geld an? Finde die Nachzügler bevor sie aufholen.")

            narrative_data = early_data.get("narratives", {})
            if not narrative_data:
                st.info("Keine Narrative-Daten verfügbar.")
            else:
                # Sektor-Übersicht als Columns
                cols_per_row = 3
                narr_items = list(narrative_data.items())
                for row_start in range(0, len(narr_items), cols_per_row):
                    cols = st.columns(cols_per_row)
                    for col_idx, (narr, data) in enumerate(narr_items[row_start:row_start + cols_per_row]):
                        with cols[col_idx]:
                            avg_7d = data.get("avg_7d", 0)
                            if avg_7d > 10:
                                bg = "#1a472a"
                            elif avg_7d > 0:
                                bg = "#2d4a2d"
                            elif avg_7d > -10:
                                bg = "#4a2d2d"
                            else:
                                bg = "#472a1a"

                            st.markdown(f"""
                            <div style="background:{bg};padding:12px;border-radius:8px;margin-bottom:8px;">
                                <b>{narr}</b><br>
                                <span style="font-size:1.3em;">{avg_7d:+.1f}%</span> <small>7d avg</small><br>
                                <small>{data['count']} Coins · Vol ${data['total_vol']/1e9:.1f}B</small>
                            </div>
                            """, unsafe_allow_html=True)

                st.divider()

                # Detail pro Narrativ
                for narr, data in narr_items:
                    with st.expander(f"{narr} — **{data['avg_7d']:+.1f}%** 7d · {data['count']} Coins"):
                        # Leaders
                        if data.get("leaders"):
                            st.markdown("**🏆 Top Performer:**")
                            for c in data["leaders"]:
                                exch = " + ".join(c.get("Exchanges", [])) if c.get("HasPerp") else "❌"
                                perp = f"✅ {exch}" if c.get("HasPerp", False) else "❌ kein Perp"
                                st.markdown(f"- **{c.get('Symbol', '')}** {c.get('Change7d', 0):+.1f}% 7d · ${c.get('MCap', 0)/1e6:.0f}M MCap · {perp}")

                        # Laggards (potentielle Nachzügler)
                        if data.get("laggards"):
                            st.markdown("**🐌 Nachzügler (Aufholpotential):**")
                            for c in data["laggards"]:
                                delta_to_avg = data.get("avg_7d", 0) - c.get("Change7d", 0)
                                exch = " + ".join(c.get("Exchanges", [])) if c.get("HasPerp") else "❌"
                                perp = f"✅ {exch}" if c.get("HasPerp", False) else "❌ kein Perp"
                                st.markdown(f"- **{c.get('Symbol', '')}** {c.get('Change7d', 0):+.1f}% 7d *(Sektor-Avg: {data['avg_7d']:+.1f}%, Gap: {delta_to_avg:.1f}%)* · {perp}")


# =============================================================================
# TAB: 🆕 NEW LISTING DUMP SCANNER
# =============================================================================
with tab_newlisting:
    st.header("🆕 New Listing Dump Scanner")
    st.caption("Short-Signale für neue PERP-Listings — Crypto.com + MEXC (755 Perps!) + Bitget = 1.500+ Instrumente")

    # ── Scan Button ──
    _nls_col_btn, _nls_col_info = st.columns([1, 3])
    with _nls_col_btn:
        _nls_scan_btn = st.button("🔍 New Listing Scan", type="primary", use_container_width=True, key="nls_scan_btn")
    with _nls_col_info:
        st.markdown("**Scannt Crypto.com** nach neuen PERP-Listings und berechnet Pump-Exhaustion Scores")

    if _nls_scan_btn:
        with st.spinner("🔍 Scanne Crypto.com für neue Listings..."):
            try:
                import sys as _sys
                _sys.path.insert(0, os.path.dirname(__file__))
                from modules.new_listing_scanner import run_new_listing_scanner, seed_instrument_cache
                seed_instrument_cache()
                _nls_live_results = run_new_listing_scanner()
                st.success(f"✅ Scan abgeschlossen: {len(_nls_live_results.get('signals', []))} Signale, "
                           f"{len(_nls_live_results.get('watchlist', []))} Watchlist, "
                           f"{len(_nls_live_results.get('monitoring', []))} monitoring "
                           f"({_nls_live_results.get('duration_sec', '?')}s)")
            except Exception as _nls_e:
                st.error(f"❌ Scan Fehler: {_nls_e}")

    # ── Background Service Cache laden ──
    _nls_cache_file = os.path.join(os.path.dirname(__file__), "data_cache", "new_listing_scanner.json")
    _nls_data = None
    if os.path.exists(_nls_cache_file):
        try:
            with open(_nls_cache_file, "r") as f:
                _nls_data = json.load(f)
        except Exception:
            pass

    if _nls_data:
        _nls_ts = _nls_data.get("timestamp", "?")
        _nls_signals = _nls_data.get("signals", [])
        _nls_watchlist = _nls_data.get("watchlist", [])
        _nls_monitoring = _nls_data.get("monitoring", [])

        st.info(f"Letzte Aktualisierung: {_nls_ts[:19].replace('T', ' ')} UTC | "
                f"{len(_nls_monitoring)} Coins überwacht | "
                f"Dauer: {_nls_data.get('duration_sec', '?')}s")

        # ── Signale (Short Entry) ──
        if _nls_signals:
            st.subheader(f"🔴 {len(_nls_signals)} Short-Signal{'e' if len(_nls_signals) != 1 else ''}")
            for sig_entry in _nls_signals:
                sig = sig_entry.get("signal", {})
                pd_ = sig.get("pump_data", {})
                with st.expander(f"**{sig_entry['symbol']}** — {sig.get('grade_label', '?')} | "
                                 f"Pump: +{pd_.get('pump_pct', 0):.0f}% | "
                                 f"ExhScore: {sig.get('exh_score', 0)}"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Entry", f"${sig.get('entry', 0):.6f}")
                        st.metric("Stop Loss", f"${sig.get('stop_loss', 0):.6f}",
                                  delta=f"+{sig.get('risk_pct', 0):.1f}%", delta_color="inverse")
                    with col2:
                        st.metric("TP1 (-20% ATH)", f"${sig.get('tp1', 0):.6f}")
                        st.metric("TP2 (-40% ATH)", f"${sig.get('tp2', 0):.6f}")
                    with col3:
                        st.metric("RR (TP1)", f"{sig.get('rr1', 0):.1f}x")
                        st.metric("RR (TP2)", f"{sig.get('rr2', 0):.1f}x")

                    st.markdown(f"**Timing:** {sig.get('timing', '?')}")
                    st.markdown(f"**Max Leverage:** {sig.get('max_leverage', '?')}x | "
                                f"**Max Haltedauer:** {sig.get('max_position_hours', '?')}h")

                    # Safety
                    sw = sig.get("safety_warnings", [])
                    if sw:
                        st.markdown("**Safety:** " + " | ".join(sw))

                    # Exhaustion Details
                    ed = sig.get("exh_details", [])
                    if ed:
                        st.markdown("**Exhaustion Details:**")
                        for d in ed:
                            st.markdown(f"  {d}")
        else:
            st.success("Keine aktiven Short-Signale — kein überhitztes Listing erkannt")

        # ── Watchlist ──
        if _nls_watchlist:
            st.subheader(f"🟡 {len(_nls_watchlist)} auf der Watchlist")
            for w_entry in _nls_watchlist:
                sig = w_entry.get("signal", {})
                pd_ = sig.get("pump_data", {})
                st.markdown(f"**{w_entry['symbol']}** — {sig.get('timing', '?')} | "
                            f"Pump: +{pd_.get('pump_pct', 0):.0f}% | "
                            f"ExhScore: {sig.get('exh_score', 0)} | "
                            f"Grade: {sig.get('grade_label', '?')}")

        # ── Monitoring Übersicht ──
        if _nls_monitoring:
            st.subheader(f"📡 {len(_nls_monitoring)} Listings in Überwachung")
            import pandas as pd
            _mon_df = pd.DataFrame(_nls_monitoring)
            _mon_cols = {
                "symbol": "Symbol",
                "exh_score": "ExhScore",
                "pump_pct": "Pump %",
                "from_ath_pct": "Vom ATH %",
                "volume_ratio": "Vol Ratio",
                "safety_ok": "Safety",
                "grade": "Grade",
                "timing": "Timing",
                # AUDIT-Kleinkram: hours_tracked = Anzahl 1h-Kerzen, NICHT das
                # Listing-Alter → ehrliches Label + echtes Alter separat anzeigen
                "listing_age_hours": "Alter (h)",
                "hours_tracked": "Kerzen (1h)",
            }
            _mon_display = _mon_df.rename(columns=_mon_cols)
            for col in _mon_cols.values():
                if col not in _mon_display.columns:
                    _mon_display[col] = "-"
            st.dataframe(_mon_display[[c for c in _mon_cols.values() if c in _mon_display.columns]],
                         use_container_width=True, hide_index=True)
        else:
            st.info("Keine Listings in Überwachung. Neue PERP-Listings werden automatisch erkannt.")

        # ── Neue Listings erkannt ──
        _new_detected = _nls_data.get("new_listings_detected", [])
        if _new_detected:
            st.subheader(f"🆕 {len(_new_detected)} neue Listings erkannt")
            st.markdown(", ".join(f"**{s}**" for s in _new_detected))

        # ── Strategie-Info ──
        with st.expander("📖 So funktioniert der Scanner"):
            st.markdown("""
**Strategie: New Listing Dump**

Basierend auf Marktdaten 2024-2026:
- 54% der neuen Listings pumpen am ersten Tag
- 89% dumpen danach, 70% unter Peak innerhalb 2 Wochen
- Das Pump-Fenster dauert typisch 2-6 Stunden

**Der Scanner:**
1. Erkennt neue PERP-Listings auf Crypto.com (alle 15 Min)
2. Trackt den Pump via 1h-Candles (bis zu 72h)
3. Berechnet Pump-Exhaustion (7 Komponenten, 0-100)
4. Prüft Liquidität (Spread, Orderbook, Volume)
5. Generiert Short-Signal mit Entry/Stop/TP

**Risk Management:**
- Stop: ATH + 5% (absolutes Maximum)
- TP1: -20% vom ATH
- TP2: -40% vom ATH
- Max Haltedauer: 48h
- Max Leverage: 10x empfohlen
""")
    else:
        st.warning("⏳ New Listing Scanner läuft noch nicht. Starte den Background Service.")
        st.code("sudo systemctl start tradingbot-bg.service", language="bash")


# -----------------------------------------------------------------------------
# SUCHE TAB - Manuelle Ticker-Suche
# -----------------------------------------------------------------------------
with tab_search:
    st.subheader("🔍 Manuelle Suche")
    st.caption("Suche nach einer bestimmten Aktie oder Kryptowährung")
    
    col_search1, col_search2, col_search3 = st.columns([2, 1, 1])
    
    with col_search1:
        search_input = st.text_input(
            "Ticker eingeben",
            placeholder="z.B. TSLA, AAPL, BTC, ETH, XRP...",
            key="manual_search_input"
        ).upper().strip()
    
    with col_search2:
        search_market = st.radio("Markt", ["Aktien", "Krypto"], horizontal=True, key="search_market")
    
    with col_search3:
        st.write("")  # Spacer
        search_clicked = st.button("🔍 Suchen", type="primary", key="search_btn")
    
    if search_clicked and search_input:
        with st.spinner(f"Suche {search_input}..."):
            search_result = None
            
            if search_market == "Krypto":
                # CoinGecko Suche - Verbessert mit Search API
                try:
                    # Methode 1: Direkte Suche via Search API
                    search_url = f"https://api.coingecko.com/api/v3/search?query={search_input.lower()}"
                    search_resp = rate_limited_get(search_url, timeout=15)
                    
                    coin_id = None
                    if search_resp.status_code == 200:
                        search_data = search_resp.json()
                        coins_found = search_data.get("coins", [])
                        
                        # Finde den besten Match
                        for c in coins_found:
                            if c.get("symbol", "").upper() == search_input:
                                coin_id = c.get("id")
                                break
                        
                        # Fallback: Erster Treffer
                        if not coin_id and coins_found:
                            coin_id = coins_found[0].get("id")
                    
                    # Methode 2: Falls Search nicht klappt, in Markets suchen
                    if not coin_id:
                        markets_url = "https://api.coingecko.com/api/v3/coins/markets"
                        params = {
                            "vs_currency": "usd",
                            "order": "market_cap_desc",
                            "per_page": 250,
                            "page": 1
                        }
                        markets_resp = rate_limited_get(markets_url, params=params, timeout=30)
                        if markets_resp.status_code == 200:
                            for coin in markets_resp.json():
                                if coin.get("symbol", "").upper() == search_input:
                                    coin_id = coin.get("id")
                                    break
                    
                    # Jetzt Coin-Daten holen
                    if coin_id:
                        detail_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                        params = {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"}
                        detail_resp = rate_limited_get(detail_url, params=params, timeout=15)
                        
                        if detail_resp.status_code == 200:
                            coin = detail_resp.json()
                            market_data = coin.get("market_data", {})
                            
                            price = market_data.get("current_price", {}).get("usd", 0)
                            change = market_data.get("price_change_percentage_24h", 0) or 0
                            vol = market_data.get("total_volume", {}).get("usd", 0)
                            mcap = market_data.get("market_cap", {}).get("usd", 1)
                            high = market_data.get("high_24h", {}).get("usd", price)
                            low = market_data.get("low_24h", {}).get("usd", price)
                            
                            rvol = round((vol / mcap) * 500, 2) if mcap > 0 else 1.0
                            rvol = max(0.1, min(rvol, 100))
                            close_pos = calculate_close_position(high, low, price)
                            alpha = calculate_alpha_score(rvol, change, change)
                            
                            search_result = {
                                "Ticker": coin.get("symbol", "").upper(),
                                "Name": coin.get("name", ""),
                                "Preis": round(price, 6),
                                "Chg%": round(change, 2),
                                "RVOL": rvol,
                                "Vortag%": round(change, 2),
                                "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5,
                                "Alpha": alpha,
                                "High24h": high,
                                "Low24h": low,
                                "Volume": vol,
                                "MarketCap": mcap
                            }
                except Exception as e:
                    st.error(f"Fehler bei Krypto-Suche: {e}")
            
            else:
                # Polygon Aktien-Suche
                try:
                    poly_key = st.secrets["POLYGON_KEY"]
                    url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{search_input}"
                    params = {"apiKey": poly_key}
                    resp = rate_limited_get(url, params=params, timeout=15)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        ticker_data = data.get("ticker", {})
                        
                        if ticker_data:
                            day = ticker_data.get("day", {}) or {}
                            prev = ticker_data.get("prevDay", {}) or {}
                            last = ticker_data.get("lastTrade", {}) or {}
                            
                            price = day.get("c") or last.get("p") or prev.get("c") or 0
                            
                            if price > 0:
                                high = day.get("h", price)
                                low = day.get("l", price)
                                
                                change = ticker_data.get("todaysChangePerc", 0) or 0
                                
                                vol = day.get("v", 0)
                                prev_vol = prev.get("v", 1)
                                rvol = round(vol / prev_vol, 2) if prev_vol > 0 else 1.0
                                
                                prev_open = prev.get("o", 0)
                                prev_close = prev.get("c", 0)
                                vortag = round(((prev_close - prev_open) / prev_open) * 100, 2) if prev_open > 0 else 0
                                
                                close_pos = calculate_close_position(high, low, price)
                                alpha = calculate_alpha_score(rvol, vortag, change)
                                
                                search_result = {
                                    "Ticker": search_input,
                                    "Name": search_input,
                                    "Preis": round(price, 4),
                                    "Chg%": round(change, 2),
                                    "RVOL": rvol,
                                    "Vortag%": vortag,
                                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5,
                                    "Alpha": alpha,
                                    "High24h": high,
                                    "Low24h": low,
                                    "Volume": vol
                                }
                except Exception as e:
                    st.error(f"Fehler bei Aktien-Suche: {e}")
            
            # Ergebnis anzeigen
            if search_result:
                st.success(f"✅ {search_result.get('Ticker', '')} gefunden!")
                
                # In Session State speichern
                st.session_state.selected_symbol = search_result["Ticker"]
                st.session_state.current_data = search_result
                st.session_state.market_type = search_market
                
                # Daten anzeigen
                st.divider()
                
                col_d1, col_d2, col_d3, col_d4 = st.columns(4)
                with col_d1:
                    st.metric("Preis", f"${search_result.get('Preis', 0):,.4f}")
                with col_d2:
                    st.metric("24h", f"{search_result.get('Chg%', 0):.2f}%", 
                             delta=f"{search_result.get('Chg%', 0):.2f}%",
                             delta_color="normal" if search_result.get('Chg%', 0) >= 0 else "inverse")
                with col_d3:
                    st.metric("RVOL", f"{search_result.get('RVOL', 0):.1f}x")
                with col_d4:
                    st.metric("Alpha", f"{search_result.get('Alpha', 0):.0f}")
                
                st.divider()
                
                # Details
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.caption(f"📈 24h High: ${search_result.get('High24h', 0):,.4f}")
                    st.caption(f"📉 24h Low: ${search_result.get('Low24h', 0):,.4f}")
                with col_info2:
                    st.caption(f"📊 Volume: {search_result.get('Volume', 0):,.0f}")
                    if 'MarketCap' in search_result:
                        st.caption(f"💰 Market Cap: ${search_result.get('MarketCap', 0):,.0f}")
                
                # Aktionen
                st.divider()
                col_act1, col_act2 = st.columns(2)
                with col_act1:
                    if st.button(f"⭐ {search_result.get('Ticker', '')} zur Watchlist", key="search_watchlist", use_container_width=True):
                        if add_to_watchlist(search_result["Ticker"], search_result):
                            st.success("Hinzugefügt!")
                        else:
                            st.info("Bereits in Watchlist")
                with col_act2:
                    if st.button("🤖 AI-Analyse starten", key="search_ai_btn", type="primary", use_container_width=True):
                        st.session_state.run_search_analysis = True
                
                # Chart direkt anzeigen
                st.divider()
                st.subheader(f"📊 Chart: {search_result.get('Ticker', '')}")

                if search_market == "Krypto":
                    tv_symbol = f"BINANCE:{get_binance_tradingview_symbol(search_result.get('Ticker', ''))}"
                else:
                    # Internationale Aktien: Exchange-Prefix für TradingView
                    _sr_sym = search_result.get('Ticker', '')
                    _sr_full = search_result.get('FullTicker', _sr_sym)
                    _tv_exchange_map = {
                        ".DE": "XETR:", ".L": "LSE:", ".SW": "SIX:", ".PA": "EURONEXT:",
                        ".AS": "EURONEXT:", ".BR": "EURONEXT:", ".T": "TSE:", ".HK": "HKEX:"
                    }
                    tv_symbol = _sr_sym
                    for suffix, prefix in _tv_exchange_map.items():
                        if _sr_full.upper().endswith(suffix):
                            tv_symbol = f"{prefix}{_sr_sym}"
                            break
                
                tv_html = f'''
                <div style="height:400px; border-radius: 8px; overflow: hidden;">
                    <div id="tv_search_chart" style="height:100%"></div>
                    <script src="https://s3.tradingview.com/tv.js"></script>
                    <script>
                        new TradingView.widget({{
                            "autosize": true,
                            "symbol": "{tv_symbol}",
                            "interval": "240",
                            "timezone": "Europe/Berlin",
                            "theme": "dark",
                            "style": "1",
                            "locale": "de_DE",
                            "enable_publishing": false,
                            "hide_side_toolbar": false,
                            "allow_symbol_change": true,
                            "studies": ["Volume@tv-basicstudies"],
                            "container_id": "tv_search_chart"
                        }});
                    </script>
                </div>
                '''
                st.components.v1.html(tv_html, height=400)
                
                # AI-Analyse wenn Button geklickt wurde
                if st.session_state.get("run_search_analysis", False):
                    st.divider()
                    st.subheader("🤖 AI-Analyse")
                    with st.spinner("KI analysiert..."):
                        try:
                            client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                            
                            prompt = f"""SCHNELL-ANALYSE für {search_result.get('Ticker', '')}

DATEN:
- Preis: ${search_result.get('Preis', 0)}
- 24h Change: {search_result.get('Chg%', 0)}%
- RVOL: {search_result.get('RVOL', 0)}x
- Alpha-Score: {search_result.get('Alpha', 0)}
- Markt: {search_market}

AUFGABEN:
1. Kurze technische Einschätzung (2-3 Sätze)
2. Key Support & Resistance Levels
3. Empfehlung: LONG / SHORT / ABWARTEN
4. Rating: X/100

Keine Disclaimers. Direkt und knapp."""

                            message = client.messages.create(
                                model=AI_PROVIDER_MODEL,
                                max_tokens=800,
                                system="Du bist ein präzises Trading-Terminal. Kurz und knackig.",
                                messages=[{"role": "user", "content": prompt}]
                            )
                            
                            st.write(message.content[0].text)
                            st.session_state.run_search_analysis = False
                            
                        except Exception as e:
                            st.error(f"Fehler: {e}")
                
            else:
                st.warning(f"❌ '{search_input}' nicht gefunden. Prüfe die Schreibweise.")
                st.caption("Beispiele: TSLA, AAPL, NVDA, BTC, ETH, SOL")

# -----------------------------------------------------------------------------
# WATCHLIST TAB
# -----------------------------------------------------------------------------
with tab_watchlist:
    st.subheader("⭐ Meine Watchlist")
    
    if st.session_state.watchlist:
        st.caption(f"{len(st.session_state.watchlist)} Ticker gespeichert")
        
        for i, item in enumerate(st.session_state.watchlist):
            with st.container():
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                with c1:
                    st.markdown(f"**{item['ticker']}**")
                    st.caption(item['market'])
                with c2:
                    st.metric("Preis (beim Hinzufügen)", f"${item['price']:.4f}")
                with c3:
                    st.caption(f"Hinzugefügt: {item['added']}")
                with c4:
                    if st.button("🗑️", key=f"del_{i}"):
                        remove_from_watchlist(item['ticker'])
                        st.rerun()
                st.divider()
        
        # Watchlist Export
        if st.button("📋 Watchlist kopieren"):
            tickers = ", ".join([w['ticker'] for w in st.session_state.watchlist])
            st.code(tickers)
        
        if st.button("🗑️ Alle löschen", type="secondary"):
            st.session_state.watchlist = []
            _save_watchlist()
            st.rerun()
    else:
        st.info("Noch keine Ticker in der Watchlist. Wähle einen Ticker im Scanner und klicke '⭐ zur Watchlist'")

# -----------------------------------------------------------------------------
# AI ANALYSE
# -----------------------------------------------------------------------------
st.divider()

col_ai1, col_ai2 = st.columns([3, 1])
with col_ai1:
    st.subheader("🤖 KI Analyse")
with col_ai2:
    analyze_btn = st.button("Analyse starten", type="primary", use_container_width=True)

if analyze_btn:
    if "current_data" not in st.session_state:
        st.warning("Wähle zuerst einen Ticker!")
    else:
        with st.spinner("KI analysiert..."):
            try:
                d = st.session_state.current_data
                m_type = st.session_state.market_type
                sr = st.session_state.sr_levels
                fib = st.session_state.get("fib_info", {})
                
                news_txt = "Keine News."
                if m_type == "Aktien":
                    try:
                        poly_key = st.secrets["POLYGON_KEY"]
                        news_resp = rate_limited_get(
                            "https://api.polygon.io/v2/reference/news",
                            params={"ticker": st.session_state.selected_symbol, "limit": 3, "apiKey": poly_key},
                            timeout=10
                        ).json()
                        news_items = news_resp.get("results", [])
                        if news_items:
                            news_txt = "\n".join([f"- {n.get('title', 'N/A')}" for n in news_items])
                    except Exception as e:
                        pass
                
                client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                
                # Fibonacci-erweiterte S/R Info
                sr_text = f"""
SUPPORT & RESISTANCE (aus Swing Highs/Lows + Fibonacci):
Support-Zonen: {', '.join([f'${s}' for s in sr['support']])}
Resistance-Zonen: {', '.join([f'${r}' for r in sr['resistance']])}
"""
                
                # Fibonacci Details hinzufügen wenn vorhanden
                if fib:
                    sr_text += f"""
FIBONACCI LEVELS (basierend auf Periode High/Low):
• Periode High: ${fib.get('period_high', 'N/A')}
• Periode Low: ${fib.get('period_low', 'N/A')}
• Fib 23.6%: ${fib.get('fib_236', 'N/A')}
• Fib 38.2%: ${fib.get('fib_382', 'N/A')}
• Fib 50.0%: ${fib.get('fib_500', 'N/A')}
• Fib 61.8% (Golden Ratio): ${fib.get('fib_618', 'N/A')}
• Fib 78.6%: ${fib.get('fib_786', 'N/A')}
• Fib Extension 127.2%: ${fib.get('fib_1272', 'N/A')}
• Fib Extension 161.8%: ${fib.get('fib_1618', 'N/A')}
"""
                    
                    # Konsolidierungszonen hinzufügen
                    if fib.get('consolidation_zones'):
                        sr_text += f"""
KONSOLIDIERUNGSZONEN (High Activity - wo viel gehandelt wurde):
"""
                        for i, zone in enumerate(fib['consolidation_zones'], 1):
                            sr_text += f"• Zone {i}: ${zone['low']} - ${zone['high']} ({zone['days']} Kerzen = {zone['pct_time']}% der Zeit)\n"
                        sr_text += """
Diese Zonen sind wichtig weil:
- Viele Orders/Positionen wurden hier eröffnet
- Oft fungieren sie als Support/Resistance
- Preis tendiert dazu, in diese Zonen zurückzukehren
"""
                
                # Erweiterter Profi-Prompt
                asset_name = d.get('Name', d['Ticker'])
                current_date = datetime.now().strftime("%d.%m.%Y")
                
                # MARKTSPEZIFISCHE KATALYSATOREN
                if m_type == "Krypto":
                    katalysatoren_text = """6. KOMMENDE KATALYSATOREN (KRYPTO-SPEZIFISCH)
   - Token Unlocks / Vesting Schedules (wann werden Tokens freigeschaltet?)
   - Protokoll-Upgrades / Hard Forks / Soft Forks
   - Mainnet Launches / Testnet Updates
   - Halvings (bei PoW Coins)
   - Token Burns / Buybacks
   - Neue Exchange Listings
   - Partnership Announcements
   - Staking/Yield Änderungen
   - Regulatorische Entwicklungen (ETF-Entscheidungen, Gesetzgebung)
   - Makro: Fed-Entscheidungen, Risk-On/Risk-Off Sentiment
   - Wann ist das nächste wichtige Datum für diesen Coin?"""
                    
                    system_extra = """
KRYPTO-EXPERTISE:
- Du kennst typische Krypto-Katalysatoren: Halvings, Upgrades, Token Burns, Unlocks, Forks
- Du weisst dass Krypto 24/7 handelt und volatiler ist
- Du berücksichtigst On-Chain Metriken wenn relevant
- Du kennst die wichtigsten Protokolle und deren Upgrade-Zyklen"""

                else:  # Aktien
                    katalysatoren_text = """6. KOMMENDE KATALYSATOREN (AKTIEN-SPEZIFISCH)
   
   EARNINGS & FINANCIALS:
   - Nächster Earnings Report (Datum, Erwartungen)
   - Guidance Updates
   - Dividenden-Termine (Ex-Date, Payment Date)
   - Aktienrückkauf-Programme
   
   SEKTOR-SPEZIFISCH:
   
   Biotech/Pharma:
   - FDA-Entscheidungen (PDUFA Dates)
   - Klinische Studien (Phase 1/2/3 Readouts)
   - AdCom Meetings
   - Patent-Abläufe
   
   Tech:
   - Produkt-Launches
   - Developer Conferences
   - Nutzerzahlen / MAU Reports
   
   Retail:
   - Same-Store-Sales Reports
   - Holiday Season Performance
   
   Energie:
   - OPEC Meetings
   - Inventory Reports
   
   ALLGEMEIN:
   - Insider-Käufe/Verkäufe
   - Institutionelle Bewegungen (13F Filings)
   - Analysten-Rating Änderungen
   - Index-Aufnahmen/Entfernungen (S&P 500, etc.)
   - Stock Splits
   - Spin-Offs / M&A Gerüchte
   
   MAKRO:
   - Fed Meetings / Zinsentscheidungen
   - CPI / Inflationsdaten
   - Arbeitsmarktdaten
   
   - Wann ist das nächste wichtige Datum für diese Aktie?"""
                    
                    system_extra = """
AKTIEN-EXPERTISE:
- Du kennst Earnings-Zyklen und typische Reaktionen
- Bei Biotech/Pharma kennst du FDA-Prozesse und klinische Studien-Phasen
- Du weisst dass Pre-Market und After-Hours wichtig sind
- Du berücksichtigst Sektor-Rotation und Marktbreite
- Du kennst die Bedeutung von Insider-Transaktionen und institutionellem Ownership"""
                
                prompt = f"""ALPHA STATION PRO - VOLLSTÄNDIGER TRADING REPORT

═══════════════════════════════════════════════════
ASSET: {d['Ticker']} ({asset_name})
MARKT: {m_type}
DATUM: {current_date}
═══════════════════════════════════════════════════

LIVE-DATEN:
• Aktueller Preis: ${d['Preis']}
• 24h Änderung: {d['Chg%']}%
• RVOL (Volumen-Ratio): {d['RVOL']}x
• Close Position: {d.get('ClosePos', 0.5)} (0=Tagestief, 1=Tageshoch)
• Alpha-Score: {d['Alpha']}
• Vortag Performance: {d.get('Vortag%', 'N/A')}%
• Gap%: {d.get('Gap%', 'N/A')}%
• ATR%: {d.get('ATR%', 'N/A')}% (Volatilitäts-Regime: {d.get('VolRegime', 'N/A')})
• Dollar Volume: ${d.get('DollarVol', 0):,.0f}
• MA Distanz: {d.get('MA_Distance%', 'N/A')}% ({d.get('MA_Type', '')})

AKTIVE STRATEGIE: {st.session_state.get('current_strategy', 'Keine')}

{sr_text}

AKTUELLE NEWS:
{news_txt}

═══════════════════════════════════════════════════
DEINE AUFGABEN (VOLLSTÄNDIGER REPORT):
═══════════════════════════════════════════════════

1. STRATEGIE-ANALYSE
   - Bewerte das Setup für die Strategie "{st.session_state.current_strategy}"
   - Passt das Asset zur gewählten Strategie? Warum/warum nicht?

2. FIBONACCI-ANALYSE
   - Analysiere die gegebenen Fibonacci-Levels
   - Wo steht der Preis im Verhältnis zu den Fib-Levels?
   - Welches Fib-Level ist das wichtigste für diesen Trade?
   - Bei welchem Fib-Level erwarten wir Reaktion?
   - Gib konkrete Preise an: "Fib 61.8% bei $XX ist Key-Level"

3. KONSOLIDIERUNGSZONEN-ANALYSE
   - Analysiere die High-Activity Zonen wo viel gehandelt wurde
   - Liegt der aktuelle Preis in/nahe einer Konsolidierungszone?
   - Welche Zone ist am wichtigsten als S/R?
   - Erkläre warum diese Zonen als Support/Resistance fungieren können
   - Beispiel: "Zone $1.78-$1.92 war 40% der Zeit aktiv = starke Support-Zone"

4. ELLIOTT WAVE ANALYSE
   - In welcher Elliott Wave befinden wir uns wahrscheinlich?
   - Welle 1, 2, 3, 4 oder 5 (Impuls) oder A, B, C (Korrektur)?
   - Begründe deine Einschätzung basierend auf der Preisbewegung
   - Was ist das wahrscheinliche Kursziel basierend auf Elliott Wave?
   - Beispiel: "Wir sind in Welle 3, typisches Ziel ist 161.8% Extension bei $XX"

5. ENTRY-STRATEGIE
   - Exakter Einstiegspunkt (Preis)
   - Entry-Typ: Market Order / Limit Order / Stop-Entry?
   - Optimaler Einstiegszeitpunkt (sofort, bei Pullback, bei Breakout?)
   - Nutze Fibonacci-Level oder Konsolidierungszone für Entry

6. STOP-LOSS & TAKE-PROFIT (MIT FIBONACCI + ZONEN)
   - Stop-Loss: Unter welchem Fib-Level oder welcher Zone? Konkreter Preis
   - Take-Profit 1: Welches Fib-Level oder Zone? Konkreter Preis
   - Take-Profit 2: Welches Fib-Extension Level? Konkreter Preis
   - Risk/Reward Ratio

7. NEWS & SENTIMENT
   - Analyse der aktuellen News (falls vorhanden)
   - Sentiment-Einschätzung: Bullish / Bearish / Neutral

{katalysatoren_text}

9. RISIKO-FAKTOREN
   - Was könnte schiefgehen?
   - Welche Warnsignale gibt es?
   - Sektor-spezifische Risiken

10. FINAL VERDICT
   - Rating: X/100
   - Empfehlung: STRONG LONG / LONG / ABWARTEN / SHORT / STRONG SHORT
   - Konfidenz: Hoch / Mittel / Niedrig
   - Positionsgröße-Empfehlung: Klein (1-2%) / Normal (2-5%) / Aggressiv (5-10%)
   - Zeithorizont: Intraday / Swing (Tage) / Position (Wochen)

═══════════════════════════════════════════════════
ZUSAMMENFASSUNG ZUM EINZEICHNEN:
Am Ende liste diese Levels klar auf, damit der User sie im Chart einzeichnen kann:
- Entry: $XX
- Stop-Loss: $XX
- TP1: $XX (Fib XX%)
- TP2: $XX (Fib XX%)
- Key Fib Levels: $XX (23.6%), $XX (38.2%), $XX (50%), $XX (61.8%), $XX (78.6%)
═══════════════════════════════════════════════════

REGELN: Keine Disclaimers, keine Ausreden, keine Höflichkeitsfloskeln.
Du bist ein Trading-Terminal. Die Daten sind Fakten. Liefere konkrete Zahlen.
═══════════════════════════════════════════════════"""

                system_prompt = f"""Du bist ALPHA TERMINAL - ein präzises, professionelles Trading-Analyse-System mit Expertise in Fibonacci und Elliott Wave.

DEINE EIGENSCHAFTEN:
- Du lieferst messerscharfe, konkrete Analysen
- Du nennst IMMER exakte Preise und Zahlen
- Du bist Experte für Fibonacci Retracements und Extensions
- Du kannst Elliott Waves identifizieren und Kursziele ableiten
- Du bist direkt und ohne Umschweife
- Du gibst klare Handlungsempfehlungen
- Du recherchierst aus deinem Wissen bekannte Termine und Events
{system_extra}

FIBONACCI EXPERTISE:
- Du kennst alle wichtigen Fib-Levels: 23.6%, 38.2%, 50%, 61.8%, 78.6%
- Du kennst Fib-Extensions: 127.2%, 161.8%, 200%, 261.8%
- Du weisst dass 61.8% das "Golden Ratio" ist und oft starke Reaktionen zeigt
- Du nutzt Fib-Levels für Entry, Stop-Loss und Take-Profit

ELLIOTT WAVE EXPERTISE:
- Du kennst die 5-Wellen Impuls-Struktur (1-2-3-4-5)
- Du kennst die 3-Wellen Korrektur-Struktur (A-B-C)
- Welle 3 ist typischerweise die längste und stärkste
- Welle 4 retraced typischerweise zum 38.2% Fib der Welle 3
- Du gibst eine Einschätzung welche Welle gerade läuft

FORMATIERUNG:
- Nutze klare Überschriften
- Nutze Bullet Points für Übersichtlichkeit
- Hebe wichtige Zahlen hervor
- Liste am Ende alle wichtigen Preise zum Einzeichnen auf

VERBOTEN:
- Keine Disclaimers über "keine Anlageberatung"
- Keine Ausreden über fehlende Daten
- Keine vagen Aussagen - immer konkret"""

                message = client.messages.create(
                    model=AI_PROVIDER_MODEL,
                    max_tokens=2500,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}]
                )
                
                st.markdown(f"### 🎯 ALPHA REPORT: {d['Ticker']}")
                
                # Info-Box mit Key-Metriken
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                with col_m1:
                    st.metric("Preis", f"${d['Preis']:.4f}")
                with col_m2:
                    delta_color = "normal" if d['Chg%'] >= 0 else "inverse"
                    st.metric("24h", f"{d['Chg%']:.2f}%", delta=f"{d['Chg%']:.2f}%", delta_color=delta_color)
                with col_m3:
                    st.metric("RVOL", f"{d['RVOL']:.1f}x")
                with col_m4:
                    st.metric("Alpha", f"{d['Alpha']:.0f}")
                
                st.divider()
                st.write(message.content[0].text)
                
            except Exception as e:
                st.error(f"Fehler: {e}")

# -----------------------------------------------------------------------------
# MONEY FLOW TAB - Sektor Rotation & Smart Money Tracking
# -----------------------------------------------------------------------------
with tab_moneyflow:
    st.subheader("💰 Money Flow Heatmap")
    st.caption("Wohin fließt das Smart Money? Sektor-Rotation auf einen Blick")
    
    # Zeitraum Auswahl
    col_period, col_refresh, col_empty = st.columns([2, 1, 2])
    with col_period:
        period = st.selectbox(
            "📅 Zeitraum",
            ["1 Tag", "1 Woche", "1 Monat", "3 Monate"],
            index=1,  # Default: 1 Woche
            key="moneyflow_period"
        )
    with col_refresh:
        if st.button("🔄", key="refresh_moneyflow", help="Daten aktualisieren"):
            st.cache_data.clear()
            st.rerun()
    
    # Period zu Tagen
    period_days = {"1 Tag": 1, "1 Woche": 7, "1 Monat": 30, "3 Monate": 90}
    days = period_days.get(period, 7)
    
    @st.cache_data(ttl=600)  # 10 Minuten Cache
    def fetch_sector_performance(poly_key, days):
        """Holt historische Performance für Sektoren"""
        from datetime import timedelta
        
        sectors = {
            # US Sektor ETFs (SPDR)
            "XLK": {"name": "Technology", "emoji": "💻", "category": "Sektoren"},
            "XLF": {"name": "Financials", "emoji": "🏦", "category": "Sektoren"},
            "XLE": {"name": "Energy", "emoji": "⚡", "category": "Sektoren"},
            "XLV": {"name": "Healthcare", "emoji": "🏥", "category": "Sektoren"},
            "XLI": {"name": "Industrials", "emoji": "🏭", "category": "Sektoren"},
            "XLY": {"name": "Consumer Disc.", "emoji": "🛒", "category": "Sektoren"},
            "XLP": {"name": "Consumer Staples", "emoji": "🥫", "category": "Sektoren"},
            "XLU": {"name": "Utilities", "emoji": "💡", "category": "Sektoren"},
            "XLB": {"name": "Materials", "emoji": "🧱", "category": "Sektoren"},
            "XLRE": {"name": "Real Estate", "emoji": "🏠", "category": "Sektoren"},
            "XLC": {"name": "Communication", "emoji": "📱", "category": "Sektoren"},
            # Thematische ETFs
            "SMH": {"name": "Semiconductors", "emoji": "🔌", "category": "Themen"},
            "ARKK": {"name": "Innovation", "emoji": "🚀", "category": "Themen"},
            "HACK": {"name": "Cybersecurity", "emoji": "🔒", "category": "Themen"},
            "TAN": {"name": "Solar", "emoji": "☀️", "category": "Themen"},
            "BOTZ": {"name": "AI & Robotics", "emoji": "🤖", "category": "Themen"},
            # Asset Klassen
            "GLD": {"name": "Gold", "emoji": "🥇", "category": "Assets"},
            "SLV": {"name": "Silver", "emoji": "🥈", "category": "Assets"},
            "USO": {"name": "Oil", "emoji": "🛢️", "category": "Assets"},
            "TLT": {"name": "Bonds 20Y", "emoji": "📜", "category": "Assets"},
            "UUP": {"name": "US Dollar", "emoji": "💵", "category": "Assets"},
            # Indices
            "SPY": {"name": "S&P 500", "emoji": "📊", "category": "Indices"},
            "QQQ": {"name": "Nasdaq 100", "emoji": "📈", "category": "Indices"},
            "IWM": {"name": "Russell 2000", "emoji": "📉", "category": "Indices"},
            "DIA": {"name": "Dow Jones", "emoji": "🏛️", "category": "Indices"},
        }
        
        results = []
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 5)  # Extra Tage für Wochenenden
        
        for ticker, info in sectors.items():
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
                params = {"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 100}
                resp = rate_limited_get(url, params=params, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    bars = data.get("results", [])
                    
                    if bars and len(bars) >= 2:
                        # Start und End Preis
                        start_price = bars[0]["c"]
                        end_price = bars[-1]["c"]
                        
                        # Performance berechnen
                        change_pct = ((end_price - start_price) / start_price * 100) if start_price > 0 else 0
                        
                        results.append({
                            "ticker": ticker,
                            "name": info["name"],
                            "emoji": info["emoji"],
                            "category": info["category"],
                            "change": round(change_pct, 2),
                            "price": round(end_price, 2)
                        })
            except Exception as e:
                continue
        
        return results
    
# get_heatmap_color — Moved to modules/helpers.py
    
# get_text_color — Moved to modules/helpers.py
    
    # Daten laden
    try:
        poly_key = st.secrets["POLYGON_KEY"]
        
        with st.spinner(f"Lade {period} Performance..."):
            sector_data = fetch_sector_performance(poly_key, days)
        
        if sector_data:
            # Gruppiere nach Kategorie
            categories = {}
            for item in sector_data:
                cat = item["category"]
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(item)
            
            # Sortiere jede Kategorie nach Performance
            for cat in categories:
                categories[cat] = sorted(categories[cat], key=lambda x: x["change"], reverse=True)
            
            # HEATMAP ANZEIGE
            st.markdown(f"### 🗺️ Sektor Heatmap ({period})")
            
            # Reihenfolge der Kategorien
            cat_order = ["Indices", "Sektoren", "Themen", "Assets"]
            
            for cat_name in cat_order:
                if cat_name not in categories:
                    continue
                    
                items = categories[cat_name]
                
                st.markdown(f"**{cat_name}**")
                
                # Grid Layout (4 Spalten)
                cols = st.columns(4)
                
                for i, item in enumerate(items):
                    col_idx = i % 4
                    
                    with cols[col_idx]:
                        change = item["change"]
                        bg_color = get_heatmap_color(change)
                        text_color = get_text_color(change)
                        
                        # Heatmap Kachel mit HTML
                        st.markdown(f"""
                        <div style="
                            background-color: {bg_color};
                            color: {text_color};
                            padding: 12px;
                            border-radius: 8px;
                            text-align: center;
                            margin: 4px 0;
                        ">
                            <div style="font-size: 20px;">{item['emoji']}</div>
                            <div style="font-weight: bold; font-size: 14px;">{item['name']}</div>
                            <div style="font-size: 18px; font-weight: bold;">{change:+.1f}%</div>
                            <div style="font-size: 11px; opacity: 0.8;">{item['ticker']}</div>
                        </div>
                        """, unsafe_allow_html=True)
                
                st.markdown("")  # Spacer
            
            st.divider()
            
            # TOP & BOTTOM MOVERS
            all_sorted = sorted(sector_data, key=lambda x: x["change"], reverse=True)
            
            col_top, col_bottom = st.columns(2)
            
            with col_top:
                st.markdown("### 🚀 Top Performer")
                for item in all_sorted[:5]:
                    st.markdown(f"🟢 **{item['emoji']} {item['name']}**: {item['change']:+.1f}%")
            
            with col_bottom:
                st.markdown("### 📉 Schwächste")
                for item in all_sorted[-5:]:
                    st.markdown(f"🔴 **{item['emoji']} {item['name']}**: {item['change']:+.1f}%")
            
            st.divider()
            
            # MONEY FLOW INTERPRETATION
            st.markdown("### 🧠 Money Flow Analyse")
            
            # Risk-On vs Risk-Off
            risk_on_tickers = ["XLK", "QQQ", "IWM", "ARKK", "SMH", "XLY"]
            risk_off_tickers = ["TLT", "GLD", "XLU", "XLP", "UUP"]
            
            risk_on = [x for x in sector_data if x["ticker"] in risk_on_tickers]
            risk_off = [x for x in sector_data if x["ticker"] in risk_off_tickers]
            
            risk_on_avg = sum(x["change"] for x in risk_on) / len(risk_on) if risk_on else 0
            risk_off_avg = sum(x["change"] for x in risk_off) / len(risk_off) if risk_off else 0
            
            col_ro1, col_ro2 = st.columns(2)
            
            with col_ro1:
                if risk_on_avg > risk_off_avg + 1:
                    st.success(f"🟢 **RISK-ON** Modus")
                    st.caption(f"Wachstum: {risk_on_avg:+.1f}% vs Sicherheit: {risk_off_avg:+.1f}%")
                    st.caption("→ Geld fließt in Tech, Small Caps, Growth")
                elif risk_off_avg > risk_on_avg + 1:
                    st.warning(f"🟡 **RISK-OFF** Modus")
                    st.caption(f"Sicherheit: {risk_off_avg:+.1f}% vs Wachstum: {risk_on_avg:+.1f}%")
                    st.caption("→ Geld fließt in Bonds, Gold, Defensive")
                else:
                    st.info(f"⚖️ **NEUTRAL** - Kein klarer Trend")
                    st.caption(f"Risk-On: {risk_on_avg:+.1f}% | Risk-Off: {risk_off_avg:+.1f}%")
            
            with col_ro2:
                # Stärkster vs Schwächster Sektor
                sektoren = [x for x in sector_data if x["category"] == "Sektoren"]
                if sektoren:
                    best = max(sektoren, key=lambda x: x["change"])
                    worst = min(sektoren, key=lambda x: x["change"])
                    spread = best["change"] - worst["change"]
                    
                    st.markdown("**Sektor Rotation:**")
                    st.caption(f"🚀 Leader: **{best['name']}** ({best['change']:+.1f}%)")
                    st.caption(f"📉 Laggard: **{worst['name']}** ({worst['change']:+.1f}%)")
                    
                    if spread > 10:
                        st.caption(f"⚠️ Hohe Dispersion ({spread:.0f}%) - Stockpicking wichtig!")
                    else:
                        st.caption(f"✅ Moderate Dispersion ({spread:.0f}%)")
            
            st.divider()
            st.caption(f"💡 Daten von Polygon.io | Letzte {days} Handelstage | Cache: 10 Min")
        
        else:
            st.warning("Keine Daten verfügbar. Markt evtl. geschlossen?")
        
    except KeyError:
        st.error("❌ POLYGON_KEY fehlt! Füge ihn in Settings → Secrets hinzu.")
    except Exception as e:
        st.error(f"Fehler beim Laden: {e}")

# =============================================================================
# TAB: WIRTSCHAFTSKALENDER
# =============================================================================
with tab_calendar:
    st.subheader("📅 Wirtschaftskalender — Makro-Events")
    st.caption("FOMC, CPI, NFP, GDP, PCE und weitere marktbewegende Events")

    try:
        import pytz as _cal_pytz
        _cal_et = _cal_pytz.timezone("US/Eastern")
        _cal_now = datetime.now(_cal_et)

        _cal_finnhub_key = st.secrets.get("FINNHUB_KEY", "")

        # ── Controls ──
        _cal_col1, _cal_col2, _cal_col3 = st.columns([2, 1, 1])
        with _cal_col1:
            _cal_days = st.selectbox(
                "Zeitraum",
                options=[7, 14, 30],
                format_func=lambda d: f"Nächste {d} Tage",
                index=0,
                key="cal_days_select",
            )
        with _cal_col2:
            _cal_impact_filter = st.multiselect(
                "Impact-Filter",
                options=["HIGH", "MEDIUM", "LOW"],
                default=["HIGH", "MEDIUM"],
                key="cal_impact_filter",
            )
        with _cal_col3:
            _cal_country_filter = st.selectbox(
                "Land",
                options=["Alle", "US", "EU", "CN", "JP", "GB"],
                index=1,
                key="cal_country_filter",
            )

        # ── Daten laden ──
        _cal_events = fetch_economic_calendar(
            _finnhub_key=_cal_finnhub_key if _cal_finnhub_key else None,
            days_ahead=_cal_days,
        )

        # ── Filtern ──
        _cal_filtered = [
            ev for ev in _cal_events
            if ev.get("impact", "LOW") in _cal_impact_filter
            and (_cal_country_filter == "Alle" or ev.get("country", "US") == _cal_country_filter)
        ]

        if not _cal_filtered:
            st.info("Keine Events im gewählten Zeitraum/Filter gefunden.")
        else:
            # ── Nächstes HIGH-Impact Event Countdown ──
            _today_str = _cal_now.strftime("%Y-%m-%d")
            _now_time_str = _cal_now.strftime("%H:%M")
            _next_high = None
            for ev in _cal_filtered:
                if ev.get("impact") == "HIGH":
                    ev_date = ev.get("date", "")
                    ev_time = ev.get("time", "23:59")
                    if ev_date > _today_str or (ev_date == _today_str and ev_time > _now_time_str):
                        _next_high = ev
                        break

            if _next_high:
                try:
                    _nh_dt = datetime.strptime(
                        f"{_next_high['date']} {_next_high.get('time', '08:30')}",
                        "%Y-%m-%d %H:%M"
                    )
                    _nh_dt = _cal_et.localize(_nh_dt)
                    _delta = _nh_dt - _cal_now
                    _hours_left = int(_delta.total_seconds() // 3600)
                    _mins_left = int((_delta.total_seconds() % 3600) // 60)

                    if _delta.total_seconds() > 0:
                        st.markdown(f"""
                        <div style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                                    border: 1px solid #e94560; border-radius: 12px; padding: 16px;
                                    margin-bottom: 16px; text-align: center;">
                            <div style="font-size: 12px; color: #e94560; text-transform: uppercase; letter-spacing: 2px;">
                                ⏰ Nächstes HIGH-Impact Event
                            </div>
                            <div style="font-size: 20px; font-weight: bold; color: #fff; margin: 8px 0;">
                                {_next_high['event']}
                            </div>
                            <div style="font-size: 28px; font-weight: bold; color: #e94560;">
                                {_hours_left}h {_mins_left}m
                            </div>
                            <div style="font-size: 13px; color: #aaa;">
                                {_next_high['date']} um {_next_high.get('time', 'TBD')} ET
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                except Exception:
                    pass

            # ── Events nach Tagen gruppiert ──
            from collections import defaultdict
            _cal_by_date = defaultdict(list)
            for ev in _cal_filtered:
                _cal_by_date[ev.get("date", "unknown")].append(ev)

            for _cal_date in sorted(_cal_by_date.keys()):
                _day_events = _cal_by_date[_cal_date]

                # Datum-Header formatieren
                try:
                    _dt_obj = datetime.strptime(_cal_date, "%Y-%m-%d")
                    _weekday_names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
                    _day_label = f"{_weekday_names[_dt_obj.weekday()]}, {_dt_obj.strftime('%d.%m.%Y')}"
                    if _cal_date == _today_str:
                        _day_label = f"🔴 HEUTE — {_day_label}"
                    elif _cal_date == (_cal_now + timedelta(days=1)).strftime("%Y-%m-%d"):
                        _day_label = f"🟡 MORGEN — {_day_label}"
                except Exception:
                    _day_label = _cal_date

                _high_count = sum(1 for e in _day_events if e.get("impact") == "HIGH")
                _header_suffix = f" — ⚡ {_high_count} HIGH" if _high_count > 0 else ""

                with st.expander(f"📆 {_day_label}{_header_suffix}", expanded=(_cal_date == _today_str)):
                    for ev in _day_events:
                        _imp = ev.get("impact", "LOW")
                        if _imp == "HIGH":
                            _imp_color = "#e94560"
                            _imp_icon = "🔴"
                        elif _imp == "MEDIUM":
                            _imp_color = "#f5a623"
                            _imp_icon = "🟡"
                        else:
                            _imp_color = "#4ecdc4"
                            _imp_icon = "🟢"

                        _ev_time = ev.get("time", "—")
                        _ev_name = ev.get("event", "Unknown")
                        _ev_country = ev.get("country", "")
                        _ev_actual = ev.get("actual")
                        _ev_estimate = ev.get("estimate")
                        _ev_prior = ev.get("prior")
                        _ev_unit = ev.get("unit", "")

                        # Werte formatieren
                        _val_parts = []
                        if _ev_actual is not None:
                            _val_parts.append(f"**Actual: {_ev_actual}{_ev_unit}**")
                        if _ev_estimate is not None:
                            _val_parts.append(f"Est: {_ev_estimate}{_ev_unit}")
                        if _ev_prior is not None:
                            _val_parts.append(f"Prior: {_ev_prior}{_ev_unit}")
                        _val_str = " | ".join(_val_parts) if _val_parts else ""

                        # Beat/Miss Indikator
                        _beat_miss = ""
                        if _ev_actual is not None and _ev_estimate is not None:
                            try:
                                if float(_ev_actual) > float(_ev_estimate):
                                    _beat_miss = " ✅ BEAT"
                                elif float(_ev_actual) < float(_ev_estimate):
                                    _beat_miss = " ❌ MISS"
                                else:
                                    _beat_miss = " ➡️ INLINE"
                            except (ValueError, TypeError):
                                pass

                        st.markdown(
                            f"{_imp_icon} **{_ev_time} ET** — "
                            f"<span style='color:{_imp_color};font-weight:bold;'>[{_imp}]</span> "
                            f"{_ev_name} ({_ev_country}){_beat_miss}"
                            + (f"  \n&nbsp;&nbsp;&nbsp;&nbsp;{_val_str}" if _val_str else ""),
                            unsafe_allow_html=True,
                        )

            # ── Zusammenfassung ──
            st.divider()
            _total_high = sum(1 for e in _cal_filtered if e.get("impact") == "HIGH")
            _total_med = sum(1 for e in _cal_filtered if e.get("impact") == "MEDIUM")
            _total_low = sum(1 for e in _cal_filtered if e.get("impact") == "LOW")
            _sum_c1, _sum_c2, _sum_c3, _sum_c4 = st.columns(4)
            _sum_c1.metric("Total Events", len(_cal_filtered))
            _sum_c2.metric("🔴 HIGH", _total_high)
            _sum_c3.metric("🟡 MEDIUM", _total_med)
            _sum_c4.metric("🟢 LOW", _total_low)

            if not _cal_finnhub_key:
                st.info("💡 Für Live-Daten: Füge FINNHUB_KEY in Settings → Secrets hinzu. Aktuell wird der kuratierte Kalender angezeigt.")

    except Exception as e:
        st.error(f"Fehler im Wirtschaftskalender: {e}")

# =============================================================================
# TAB: 🔴 CRASH MONITOR
# =============================================================================
with tab_crash:
    st.header("🔴 S&P 500 Crash Monitor")
    st.caption("Echtzeit-Frühwarnsystem für Markt-Crashs — SPY · VIX · Breadth · Sektor-Rotation")

    _crash_col_btn, _crash_col_info = st.columns([1, 3])
    with _crash_col_btn:
        _crash_scan = st.button("🔴 CRASH CHECK", type="primary", use_container_width=True, key="crash_scan_btn")
    with _crash_col_info:
        st.markdown("**Analysiert:** SPY Technicals (SMA50/200, RSI, MACD) · VIX · Safe Havens (TLT/GLD/UUP) · Credit Stress (HYG/LQD) · Breadth · Sektor-Rotation · Support/Resistance · Fear Score Trend")

    # ── Background Service Cache laden (wenn verfügbar) ──
    _bg_cache_file = os.path.join(os.path.dirname(__file__), "data_cache", "crash_monitor.json")
    _bg_cache_loaded = False
    if "crash_data" not in st.session_state and not _crash_scan:
        try:
            if os.path.exists(_bg_cache_file):
                import json as _cj
                with open(_bg_cache_file, "r") as _cf:
                    _bg_meta = _cj.load(_cf)
                _bg_age = time.time() - _bg_meta.get("updated_ts", 0)
                if _bg_age < 43200:  # Max 12h alt (bg_service scannt 2x/Tag)
                    st.session_state["crash_data"] = _bg_meta.get("data", {})
                    _bg_cache_loaded = True
                    st.caption(f"⚡ Daten aus Background-Service (vor {_bg_age:.0f}s)")
        except Exception:
            pass

    if _crash_scan:
        fetch_crash_monitor_data.clear()
        try:
            with st.spinner("📡 Lade Crash-Monitor Daten (SPY + VIX + Safe Havens + Credit + Sektoren + Breadth)..."):
                _crash_pk = st.secrets["POLYGON_KEY"]
                st.session_state["crash_data"] = fetch_crash_monitor_data(_crash_pk)
        except KeyError:
            st.error("❌ POLYGON_KEY fehlt in secrets!")
        except Exception as _ce:
            st.error(f"❌ Fehler: {_ce}")

    if "crash_data" not in st.session_state:
        st.info("💡 Klicke auf **CRASH CHECK** um den Monitor zu starten.\n\n🔄 **Tipp:** Starte `python bg_service.py start` für automatische Hintergrund-Updates — dann sind die Daten sofort da!")

    _cd = st.session_state.get("crash_data", {})
    if _cd:
        _spy = _cd.get("spy", {})
        _vix = _cd.get("vix", {})
        _breadth = _cd.get("breadth", {})
        _sectors = _cd.get("sectors", [])
        _signals = _cd.get("signals", [])
        _fear = _cd.get("fear_score", 0)

        # ── Fear Score Banner ──
        if _fear >= 60:
            _fear_color = "🔴"
            _fear_label = "EXTREME ANGST"
            st.error(f"🔴 **FEAR SCORE: {_fear}/100 — {_fear_label}** — Crash-Risiko HOCH")
        elif _fear >= 40:
            _fear_color = "🟠"
            _fear_label = "ERHÖHTE ANGST"
            st.warning(f"🟠 **FEAR SCORE: {_fear}/100 — {_fear_label}** — Vorsicht geboten")
        elif _fear >= 20:
            _fear_color = "🟡"
            _fear_label = "LEICHTE ANSPANNUNG"
            st.info(f"🟡 **FEAR SCORE: {_fear}/100 — {_fear_label}**")
        else:
            _fear_color = "🟢"
            _fear_label = "RUHIG"
            st.success(f"🟢 **FEAR SCORE: {_fear}/100 — {_fear_label}** — Markt stabil")

        st.progress(min(1.0, _fear / 100))

        # ── SPY Metrics ──
        if _spy:
            st.markdown("### 📊 S&P 500 (SPY)")
            _sc1, _sc2, _sc3, _sc4, _sc5 = st.columns(5)
            _spy_price = _spy.get('price', 0) or 0
            _spy_chg = _spy.get('change_pct', 0) or 0
            _spy_rsi = _spy.get('rsi', 50) or 50
            _spy_dd = _spy.get('drawdown', 0) or 0
            _spy_h52 = _spy.get('high_52w', 0) or 0
            _spy_vr = _spy.get('vol_ratio', 1) or 1
            _spy_sma20 = _spy.get('sma20', 0) or 0
            _spy_sma50 = _spy.get('sma50', 0) or 0
            _spy_macd = _spy.get('macd', 0) or 0
            _sc1.metric("SPY Preis", f"${_spy_price}", f"{_spy_chg:+.2f}%",
                        delta_color="normal")
            _sc2.metric("RSI (14)", f"{_spy_rsi:.0f}",
                        "Überverkauft" if _spy_rsi < 30 else ("Überkauft" if _spy_rsi > 70 else "Normal"))
            _sc3.metric("Drawdown", f"{_spy_dd:.1f}%",
                        f"vom Hoch ${_spy_h52}", delta_color="off")
            _sc4.metric("RVOL", f"{_spy_vr:.1f}x",
                        "Hoch!" if _spy_vr > 2 else "Normal")
            _sc5.metric("Signal", _spy.get('cross_signal', ''))

            _sm1, _sm2, _sm3, _sm4 = st.columns(4)
            _sm1.metric("SMA 20", f"${_spy_sma20}")
            _sm2.metric("SMA 50", f"${_spy_sma50}",
                        "↑ Darüber" if _spy_price > _spy_sma50 else "↓ Darunter",
                        delta_color="normal" if _spy_price > _spy_sma50 else "inverse")
            _spy_sma200 = _spy.get('sma200')
            if _spy_sma200 is not None:
                _sm3.metric("SMA 200", f"${_spy_sma200}",
                            "↑ Darüber" if _spy_price > _spy_sma200 else "↓ Darunter",
                            delta_color="normal" if _spy_price > _spy_sma200 else "inverse")
            else:
                _sm3.metric("SMA 200", "N/A", "Zu wenig Daten")
            _sm4.metric("MACD", f"{_spy_macd:.2f}",
                        "Bullisch" if _spy_macd > 0 else "Bärisch",
                        delta_color="normal" if _spy_macd > 0 else "inverse")

        # ── Erweiterte SPY Metriken ──
        if _spy:
            st.markdown("### 📉 Momentum & Druck")
            _mm1, _mm2, _mm3, _mm4, _mm5, _mm6 = st.columns(6)
            _mm1.metric("5d Perf.", f"{_spy.get('chg_5d', 0):+.2f}%",
                        delta_color="normal" if _spy.get('chg_5d', 0) >= 0 else "inverse")
            _mm2.metric("20d Perf.", f"{_spy.get('chg_20d', 0):+.2f}%",
                        delta_color="normal" if _spy.get('chg_20d', 0) >= 0 else "inverse")
            _mm3.metric("50d Perf.", f"{_spy.get('chg_50d', 0):+.2f}%",
                        delta_color="normal" if _spy.get('chg_50d', 0) >= 0 else "inverse")
            _mm4.metric("Sell-Druck", f"{_spy.get('sell_pressure', 50):.0f}%",
                        "🔴 Distribution" if _spy.get('sell_pressure', 50) > 60 else "Normal")
            _mm5.metric("Verlust-Tage", f"{_spy.get('consec_down', 0)} in Folge",
                        "🔴" if _spy.get('consec_down', 0) >= 3 else "")
            _mm6.metric("20d Range-Pos.", f"{_spy.get('range_pos', 0.5):.0%}",
                        "Am Tief" if _spy.get('range_pos', 0.5) < 0.2 else ("Am Hoch" if _spy.get('range_pos', 0.5) > 0.8 else "Mitte"))

            if _spy.get('sma_gap_pct') is not None:
                _gap = _spy.get('sma_gap_pct', 0) or 0
                if 0 < _gap < 2:
                    st.warning(f"⚠️ SMA50/SMA200 Gap nur **{_gap:.1f}%** — Death Cross droht!")

        # ── Wyckoff Distribution Analysis ──
        _wyckoff = _spy.get("wyckoff", {}) if _spy else {}
        if _wyckoff and _wyckoff.get("phase") and _wyckoff.get("phase") != "Kein Wyckoff":
            _wk_phase = _wyckoff["phase"]
            _wk_conf = _wyckoff.get("confidence", 0)
            _wk_detail = _wyckoff.get("detail", "")

            st.markdown("### 📐 Wyckoff Distribution Analyse")

            # Phase-Banner
            if "Phase E" in _wk_phase:
                st.error(f"🔴 **{_wk_phase}** (Confidence: {_wk_conf}%) — {_wk_detail}")
            elif "Phase D" in _wk_phase:
                st.error(f"🟠 **{_wk_phase}** (Confidence: {_wk_conf}%) — {_wk_detail}")
            elif "Phase C" in _wk_phase:
                st.warning(f"🟡 **{_wk_phase}** (Confidence: {_wk_conf}%) — {_wk_detail}")
            elif "Phase B" in _wk_phase:
                st.warning(f"🟡 **{_wk_phase}** (Confidence: {_wk_conf}%) — {_wk_detail}")
            else:
                st.info(f"📊 **{_wk_phase}** (Confidence: {_wk_conf}%) — {_wk_detail}")

            # Key Levels
            _wk1, _wk2, _wk3, _wk4 = st.columns(4)
            _wk1.metric("BC (Buying Climax)", f"${_wyckoff.get('bc_price', 0):.0f}")
            _wk2.metric("AR (Auto Reaction)", f"${_wyckoff.get('ar_price', 0):.0f}")
            _wk3.metric("Range", f"{_wyckoff.get('range_pct', 0):.1f}%")
            _wk4.metric("Position in Range", f"{_wyckoff.get('price_position', 50):.0f}%",
                         "Am Tief" if _wyckoff.get("price_position", 50) < 25 else
                         ("Am Hoch" if _wyckoff.get("price_position", 50) > 75 else "Mitte"))

            # Wyckoff Events
            _wk_signals = _wyckoff.get("signals", [])
            if _wk_signals:
                with st.expander(f"📋 Wyckoff Events ({len(_wk_signals)} erkannt)", expanded=True):
                    for _ws in _wk_signals:
                        if "UTAD" in _ws or "SOW" in _ws or "MARKDOWN" in _ws:
                            st.error(f"🔴 {_ws}")
                        elif "LPSY" in _ws or "Secondary" in _ws:
                            st.warning(f"🟠 {_ws}")
                        else:
                            st.info(f"📊 {_ws}")

            # Interpretation
            if _wyckoff.get("utad") and _wyckoff.get("sow"):
                st.error("⚠️ **UTAD + SOW bestätigt** — Wyckoff Distribution fast abgeschlossen. Phase E (Markdown) droht!")
            elif _wyckoff.get("utad"):
                st.warning("⚠️ **UTAD erkannt** — Fake-Breakout. Smart Money hat an Retail verkauft. Vorsicht!")
            elif _wyckoff.get("sow"):
                st.warning("⚠️ **SOW erkannt** — Preis hat Support gebrochen. Distribution bestätigt sich.")

        # ── VIX ──
        if _vix:
            _vix_ticker = _vix.get('ticker', 'UVXY')
            st.markdown(f"### 😱 VIX ({_vix_ticker} Proxy)")
            _vc1, _vc2, _vc3, _vc4 = st.columns(4)
            _vc1.metric(_vix_ticker, f"${_vix.get('price', 0)}", f"{_vix.get('change_pct', 0):+.1f}%")
            _vc2.metric("20d Durchschnitt", f"${_vix.get('avg20', 0)}")
            _vc3.metric("Spike-Ratio", f"{_vix.get('spike_ratio', 1):.2f}x",
                        "🔴 SPIKE!" if _vix.get('spike_ratio', 1) > 1.5 else ("🟠 Erhöht" if _vix.get('spike_ratio', 1) > 1.2 else "Normal"))
            _vc4.metric("5d Trend", f"{_vix.get('trend_5d', 0):+.1f}%",
                        "↑ Steigend" if _vix.get('trend_5d', 0) > 5 else ("↓ Fallend" if _vix.get('trend_5d', 0) < -5 else "Stabil"))

        # ── Fear Score Trend ──
        _fear_trend = _cd.get("fear_trend")
        if _fear_trend:
            st.caption(f"📈 Trend seit letztem Check: **{_fear_trend}**")

        # ── Fear History Mini-Chart ──
        _fear_hist = _cd.get("fear_history", [])
        if len(_fear_hist) >= 2:
            import pandas as pd
            _fh_df = pd.DataFrame(_fear_hist)
            _fh_df.columns = ["Zeitpunkt", "Fear Score"]
            st.area_chart(_fh_df.set_index("Zeitpunkt"), height=120, color="#ff4444")

        # ── Safe-Haven Tracker ──
        _safe_havens = _cd.get("safe_havens", {})
        if _safe_havens:
            st.markdown("### 🛡️ Safe-Haven Tracker (Flight to Safety)")
            _sh_cols = st.columns(len(_safe_havens))
            for _i, (_sh_etf, _sh_data) in enumerate(_safe_havens.items()):
                _sh_chg5 = _sh_data.get("chg_5d", 0)
                _sh_cols[_i].metric(
                    f"{_sh_data.get('emoji', '')} {_sh_data.get('name', 'N/A')} ({_sh_etf})",
                    f"${_sh_data.get('price', 0)}",
                    f"5d: {_sh_chg5:+.1f}%",
                    delta_color="normal" if _sh_chg5 > 0 else "inverse"
                )
            # Interpretation
            _spy_5d = _cd.get("spy", {}).get("chg_5d", 0)
            _sh_up = sum(1 for d in _safe_havens.values() if d.get("chg_5d", 0) > 0)
            if _sh_up >= 2 and _spy_5d < -1:
                st.warning(f"⚠️ **{_sh_up}/3 Safe Havens steigen** bei SPY {_spy_5d:+.1f}% — Geld fließt in Sicherheit")
            elif all(d.get("chg_5d", 0) < -1 for d in _safe_havens.values()) and _spy_5d < -2:
                st.error("🔴 **ALLES fällt** (SPY + Bonds + Gold + Dollar) — Liquiditätskrise möglich!")

        # ── Credit Stress ──
        _credit = _cd.get("credit", {})
        if _credit:
            st.markdown("### 💳 Credit Stress (HYG vs LQD)")
            _cr1, _cr2, _cr3 = st.columns(3)
            _cr1.metric("HYG (High Yield)", f"{_credit['hyg_5d']:+.1f}% (5d)")
            _cr2.metric("LQD (Inv. Grade)", f"{_credit['lqd_5d']:+.1f}% (5d)")
            _spread_chg = _credit.get("spread_change", 0)
            _cr3.metric("Spread-Änderung", f"{_spread_chg:+.1f}%",
                        "🔴 Weitet sich" if _spread_chg > 1 else ("Normal" if _spread_chg < 0.5 else "↑ Leicht"))

        # ── SPY Support/Resistance ──
        if _spy:
            _next_sup = _spy.get("next_support")
            _next_res = _spy.get("next_resistance")
            if _next_sup or _next_res:
                st.markdown("### 📐 SPY Key Levels")
                _lv1, _lv2 = st.columns(2)
                if _next_sup:
                    _sup_dist = ((_spy.get("price", 1) - _next_sup.get("level", 0)) / _spy.get("price", 1)) * 100
                    _lv1.metric(f"🟢 Nächster Support", f"${_next_sup.get('level', 0):.0f}",
                                f"{_next_sup.get('label', 'N/A')} ({_sup_dist:.1f}% entfernt)")
                if _next_res:
                    _res_dist = ((_next_res.get("level", 0) - _spy.get("price", 1)) / _spy.get("price", 1)) * 100
                    _lv2.metric(f"🔴 Nächster Widerstand", f"${_next_res.get('level', 0):.0f}",
                                f"{_next_res.get('label', 'N/A')} ({_res_dist:.1f}% entfernt)")

                # Alle Key Levels anzeigen
                _all_levels = _spy.get("key_levels", [])
                if _all_levels:
                    with st.expander("📊 Alle Key Levels", expanded=False):
                        import pandas as pd
                        _lvl_df = pd.DataFrame(_all_levels)
                        _sp_p = _spy.get('price', 1) or 1
                        _lvl_df["Entfernung"] = _lvl_df["level"].apply(lambda l, p=_sp_p: f"{((l - p) / p) * 100:+.1f}%")
                        _lvl_df["Level"] = _lvl_df["level"].apply(lambda l: f"${l:.0f}")
                        _lvl_df["Typ"] = _lvl_df["type"].apply(lambda t: "🟢 Support" if t == "support" else "🔴 Resistance")
                        _lvl_df["Label"] = _lvl_df["label"]
                        st.dataframe(_lvl_df[["Level", "Typ", "Label", "Entfernung"]], use_container_width=True, hide_index=True)

        # ── Signale ──
        if _signals:
            st.markdown("### ⚠️ Aktive Signale")
            for _s_icon, _s_name, _s_detail in _signals:
                if _s_icon == "🔴":
                    st.error(f"{_s_icon} **{_s_name}** — {_s_detail}")
                elif _s_icon == "🟠":
                    st.warning(f"{_s_icon} **{_s_name}** — {_s_detail}")
                else:
                    st.info(f"{_s_icon} **{_s_name}** — {_s_detail}")
        else:
            st.info("✅ Keine Warnsignale aktiv — Markt sieht stabil aus")

        # ── Breadth ──
        if _breadth:
            st.markdown("### 📊 Markt-Breadth")
            _bc1, _bc2, _bc3, _bc4 = st.columns(4)
            _bc1.metric("Gewinner", f"{_breadth.get('advancing', 0):,}")
            _bc2.metric("Verlierer", f"{_breadth.get('declining', 0):,}",
                        f"{_breadth.get('pct_declining', 0):.0f}% des Marktes" if _breadth.get('pct_declining') else "")
            _bc3.metric("A/D Ratio", f"{_breadth.get('ad_ratio', 0):.2f}",
                        "🔴 Schwach" if _breadth.get('ad_ratio', 1) < 0.6 else ("🟠 Negativ" if _breadth.get('ad_ratio', 1) < 0.8 else "OK"))
            _bc4.metric("Starke Verluste (>5%)", f"{_breadth.get('heavy_losers', 0):,}",
                        f"davon {_breadth.get('extreme_losers', 0):,} > 10%" if _breadth.get('extreme_losers', 0) > 0 else "")

            if _breadth.get("rotation_gap"):
                _rot = _breadth["rotation_gap"]
                if _rot > 1.5:
                    st.warning(f"🔄 **Sektor-Rotation:** Defensive Sektoren outperformen Risk-On um **{_rot:.1f}%** (5d) — Flight to Safety")
                elif _rot < -1.5:
                    st.success(f"🚀 **Risk-On Modus:** Risk-On Sektoren outperformen Defensive um **{abs(_rot):.1f}%** (5d)")

        # ── Sektor-Performance Tabelle ──
        if _sectors:
            st.markdown("### 🏢 Sektor-Performance (Rotation-Check)")
            import pandas as pd
            _sec_df = pd.DataFrame(_sectors)
            _sec_df["Sektor"] = _sec_df.apply(lambda r: f"{r['emoji']} {r['name']}", axis=1)
            _sec_df["Typ"] = _sec_df.apply(lambda r: "🛡️ Defensiv" if r["is_defensive"] else ("🎯 Risk-On" if r["is_risk_on"] else "Neutral"), axis=1)
            _sec_display = _sec_df[["Sektor", "etf", "Typ", "chg_1d", "chg_5d", "chg_20d"]].rename(columns={
                "etf": "ETF", "chg_1d": "1d%", "chg_5d": "5d%", "chg_20d": "20d%"
            })
            st.dataframe(_sec_display, use_container_width=True, hide_index=True)

        # ── Strategie-Empfehlung ──
        st.markdown("### 💡 Strategie-Hinweise")
        if _fear >= 60:
            st.markdown("""
            **Bei Fear Score > 60:**
            - 🐻 **Bear Scanner** Tab für Short/Inverse-ETF Opportunitäten nutzen
            - 🛡️ Defensiv: XLP, XLU, XLV outperformen typisch in Crashs
            - 💵 Cash-Quote erhöhen, Stop-Losses enger setzen
            - ⏳ Warte auf Kapitulations-Signal (VIX Spike + Reversal) für Dip-Buys
            """)
        elif _fear >= 30:
            st.markdown("""
            **Bei Fear Score 30-60:**
            - 🔍 Selektiv Long: Nur beste Setups, reduzierte Positionsgrößen
            - 🐻 Bear Scanner für Absicherungs-Ideen checken
            - 📊 Breadth beobachten: Verschlechtert sich A/D Ratio weiter?
            """)
        else:
            st.markdown("""
            **Bei Fear Score < 30:**
            - 📈 Normaler Scan-Modus: BI Scanner + Biotech für Long-Setups
            - 🔥 Risk-On Sektoren (Tech, Consumer Disc.) performen typisch gut
            - 👀 Trotzdem Crash Monitor regelmäßig checken!
            """)


# =============================================================================
# TAB: 🐻 BEAR SCANNER V2 — Short Scanner mit BI-Score + Confluence
# =============================================================================
with tab_bear:
    st.header("🐻 Short Scanner V2")
    st.caption("20-Signal Composite Scoring für Short-Setups | Gleiche Analyse-Tiefe wie BI Scanner")

    # Fear Score Info
    _crash_fear = st.session_state.get("crash_data", {}).get("fear_score", 0) if isinstance(st.session_state.get("crash_data"), dict) else 0
    if _crash_fear >= 40:
        st.error(f"🔴 Fear Score: **{_crash_fear}/100** — Bear-Setups sollten gut funktionieren!")
    elif _crash_fear >= 20:
        st.warning(f"🟡 Fear Score: **{_crash_fear}/100** — Selektive Short-Chancen möglich")
    else:
        st.info(f"🟢 Fear Score: **{_crash_fear}/100** — Markt ruhig, Shorts riskanter")

    # ── Trading-Modus Toggle ──
    bear_mode_col1, bear_mode_col2 = st.columns([1, 3])
    with bear_mode_col1:
        bear_trade_mode = st.radio(
            "Trading-Modus",
            ["🔄 Swing", "⚡ Intraday"],
            horizontal=True,
            key="bear_trade_mode_radio",
            help="Swing: Mehrtägig, weiter Stop/Target. Intraday: Rein bei Open, raus vor Close."
        )
        st.session_state["bear_trade_mode"] = "intraday" if "Intraday" in bear_trade_mode else "swing"
    with bear_mode_col2:
        _btm = st.session_state.get("bear_trade_mode", "swing")
        if _btm == "intraday":
            st.info("⚡ **Intraday** — Entry bei Open, Exit vor Close | Stop: 0.5% | TP1: 1.0R, TP2: 1.5R | Min DollarVol: $1M")
        else:
            st.caption("🔄 **Swing** — Mehrtägige Trades | Stop: 1.5% | TP1: 2.0R, TP2: 3.5R | Min DollarVol: $500k")

    st.divider()

    # Status laden (nutzt gleiche BI-Infrastruktur mit direction="short")
    bear_cached_results, bear_cached_ts, bear_cache_age = _bi_cache_load("short")
    bear_cache_ok = bear_cached_results is not None and bear_cache_age is not None and bear_cache_age < 43200  # 12h (bg_service scannt 3x/Tag)
    bear_running = _bi_scan_is_running("short")
    bear_progress = _bi_progress_read("short")

    # ── FALL 1: Scan läuft ──
    if bear_running and bear_progress:
        p_c = bear_progress.get("checked", 0)
        p_t = bear_progress.get("total", 0)
        p_h = bear_progress.get("hits", 0)
        p_top = bear_progress.get("top_score", 0)
        p_detail = bear_progress.get("detail", "")
        if p_t == 0:
            st.info(f"🐻 **Short Scan ⬇️** — {p_detail or '📡 Lade Aktien-Snapshot...'}")
        else:
            pct = round(p_c / max(1, p_t) * 100)
            est = max(1, (p_t - p_c) // 75)
            st.info(f"🐻 **Short Scan ⬇️ läuft** — {p_c}/{p_t} ({pct}%) | {p_h} Treffer | Top: {p_top} | ~{est} Min")
        _bear_prog_col1, _bear_prog_col2 = st.columns([5, 1])
        with _bear_prog_col1:
            st.progress(min(1.0, p_c / max(1, p_t)) if p_t > 0 else 0.0)
        with _bear_prog_col2:
            if st.button("⏹️ Stop", key="bear_stop_btn", use_container_width=True):
                _bi_request_stop("short")
                _bi_progress_write("short", "stopped", checked=p_c, total=p_t, hits=p_h,
                                   detail=f"⏹️ Manuell gestoppt bei {p_c}/{p_t}")
                time.sleep(1)
                st.rerun()
        st.caption("💡 Andere Tabs normal benutzen — Scan läuft im Hintergrund!")

    # ── FALL 2: Scan fertig ──
    elif bear_progress and bear_progress.get("status") == "done":
        fresh, _, _ = _bi_cache_load("short")
        if fresh is not None:
            st.session_state.bear_tab_results = fresh
            st.success(f"✅ **Short Scan fertig!** {len(fresh)} Treffer — automatisch geladen")
            st.caption(f"🔍 {bear_progress.get('detail', '')}")
        else:
            st.warning("⚠️ Scan fertig — keine Treffer")
        _bi_progress_clear("short")

    # ── FALL 2b: Gestoppt ──
    elif bear_progress and bear_progress.get("status") == "stopped":
        _bp_hits = bear_progress.get("hits", 0)
        _bp_checked = bear_progress.get("checked", 0)
        _bp_total = bear_progress.get("total", 0)
        st.warning(f"⏹️ Short Scan gestoppt bei {_bp_checked}/{_bp_total} — {_bp_hits} Treffer gespeichert")
        if _bp_hits > 0:
            fresh, _, _ = _bi_cache_load("short")
            if fresh is not None:
                st.session_state.bear_tab_results = fresh
        _stop_age = time.time() - bear_progress.get("timestamp", 0)
        if _stop_age > 15:
            _bi_clear_stop("short")
            try:
                os.remove(_bi_progress_path("short"))
            except Exception:
                pass

    # ── FALL 3a: Keine Kandidaten ──
    elif bear_progress and bear_progress.get("status") == "no_candidates":
        st.info(f"📭 {bear_progress.get('detail', 'Keine Kandidaten')}")
        if st.button("🔄 Erneut scannen ⬇️", use_container_width=True, key="bear_tab_retry_nc"):
            _bi_progress_write("short", status="idle")
            st.rerun()

    # ── FALL 3b: Fehler ──
    elif bear_progress and bear_progress.get("status") == "error":
        st.error(f"❌ Short Scan Fehler: {bear_progress.get('detail', 'Unbekannt')}")
        if st.button("🔄 Erneut scannen ⬇️", use_container_width=True, type="primary", key="bear_tab_retry_scan"):
            _bi_progress_write("short", status="idle")
            st.rerun()

    # ── FALL 4: Kein Scan aktiv — Buttons anzeigen ──
    else:
        if bear_cache_ok:
            try:
                cache_t = datetime.fromtimestamp(bear_cached_ts).strftime("%H:%M") if bear_cached_ts else "?"
            except (ValueError, TypeError, OSError):
                cache_t = "?"
            st.success(f"⚡ **Cache Short:** {len(bear_cached_results)} Treffer von {cache_t} ({_bi_cache_age_str(bear_cache_age)})")
            if not st.session_state.get("bear_tab_results"):
                st.session_state.bear_tab_results = bear_cached_results

        bear_btn_col1, bear_btn_col2 = st.columns(2)
        with bear_btn_col1:
            bear_manual_scan = st.button("🚀 Short Scan starten ⬇️", use_container_width=True, type="primary", key="bear_tab_manual_scan")
        with bear_btn_col2:
            if bear_cache_ok:
                if st.button(f"⚡ Cache laden ({len(bear_cached_results)})", use_container_width=True, key="bear_tab_load_cache"):
                    st.session_state.bear_tab_results = bear_cached_results
                    st.rerun()

        # --- Scan starten ---
        if bear_manual_scan and not bear_running:
            try:
                poly_key = st.secrets["POLYGON_KEY"]

                def _bear_fetch_and_scan(pk):
                    try:
                        _bi_progress_write("short", status="running", checked=0, total=0, hits=0,
                                           detail="📡 Lade Aktien-Snapshot von Polygon...")
                        import requests as _req
                        try:
                            _snap_resp = _req.get(
                                "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                                params={"apiKey": pk}, timeout=30)
                        except _req.exceptions.Timeout:
                            _bi_progress_write("short", status="error", detail="Polygon API Timeout")
                            return
                        except Exception as _net_err:
                            _bi_progress_write("short", status="error", detail=f"Netzwerk-Fehler: {_net_err}")
                            return
                        if _snap_resp.status_code != 200:
                            _bi_progress_write("short", status="error", detail=f"Polygon HTTP {_snap_resp.status_code}")
                            return
                        _tickers = _snap_resp.json().get("tickers", [])
                        if not _tickers:
                            _bi_progress_write("short", status="error", detail="Polygon Snapshot leer")
                            return

                        _bi_progress_write("short", status="running", checked=0, total=0, hits=0,
                                           detail=f"📊 {len(_tickers)} Aktien geladen, filtere Short-Kandidaten...")

                        # Basis-Daten extrahieren
                        raw = []
                        for t in _tickers:
                            try:
                                _lt = t.get("lastTrade", {}) or {}
                                _day = t.get("day", {}) or {}
                                _prev = t.get("prevDay", {}) or {}
                                _min = t.get("min", {}) or {}
                                _lq = t.get("lastQuote", {}) or {}
                                price = (_lt.get("p") or _day.get("c") or _day.get("vw") or
                                         _min.get("c") or _prev.get("c") or _prev.get("vw") or 0)
                                if not price or price <= 0:
                                    continue
                                change_pct = t.get("todaysChangePerc") or 0
                                if not change_pct and _prev.get("c") and _prev["c"] > 0:
                                    change_pct = (price - _prev["c"]) / _prev["c"] * 100
                                vol = _day.get("v") or _min.get("av") or 0
                                prev_vol = _prev.get("v") or 0
                                rvol = vol / prev_vol if prev_vol and prev_vol > 0 else 0
                                dollar_vol = price * vol if vol else 0
                                raw.append({
                                    "Ticker": t.get("ticker", ""), "Name": t.get("name", "") or "",
                                    "Preis": round(price, 2), "Change%": round(change_pct, 2),
                                    "RVOL": round(rvol, 2), "Volume": vol, "DollarVol": dollar_vol,
                                })
                            except Exception:
                                continue

                        if not raw:
                            _bi_progress_write("short", status="error", detail=f"Keine Aktien ({len(_tickers)} geprüft)")
                            return

                        # CS-Whitelist
                        _bi_progress_write("short", status="running", checked=0, total=0, hits=0,
                                           detail="📋 Lade Common Stock Liste...")
                        _CS_FILE = "/tmp/cs_tickers_cache.json"
                        _cs_set = COMMON_STOCK_TICKERS
                        if not _cs_set:
                            try:
                                if os.path.exists(_CS_FILE) and (time.time() - os.path.getmtime(_CS_FILE)) < 86400:
                                    with open(_CS_FILE, "r") as _cf:
                                        _cs_set = set(json.load(_cf))
                            except Exception:
                                pass
                        if not _cs_set:
                            try:
                                _cs_set, _ = _load_common_stock_tickers_direct(pk)
                                if _cs_set:
                                    with open(_CS_FILE, "w") as _cf:
                                        json.dump(list(_cs_set), _cf)
                            except Exception:
                                _cs_set = set()

                        # Short Pre-Filter: Verschärft — echte Schwäche filtern
                        # Change <= -2% ODER (RVOL >= 1.8 UND Change <= -1%)
                        if _cs_set:
                            filtered = [s for s in raw if isinstance(s, dict)
                                        and s.get("Ticker", "").upper() in _cs_set
                                        and s.get("Preis", 0) >= 5
                                        and s.get("DollarVol", 0) >= 500_000
                                        and (s.get("Change%", 0) <= -2.0
                                             or (s.get("RVOL", 0) >= 1.8 and s.get("Change%", 0) <= -1.0))]
                        else:
                            filtered = [s for s in raw if isinstance(s, dict)
                                        and s.get("Preis", 0) >= 5
                                        and s.get("DollarVol", 0) >= 500_000
                                        and not is_etf_or_etp(s.get("Ticker", ""))
                                        and not is_spac(s.get("Name", ""))
                                        and (s.get("Change%", 0) <= -2.0
                                             or (s.get("RVOL", 0) >= 1.8 and s.get("Change%", 0) <= -1.0))]

                        if not filtered:
                            _bi_progress_write("short", status="no_candidates",
                                               detail=f"Keine Short-Kandidaten ({len(raw)} Aktien geprüft)")
                            return
                        _bi_progress_write("short", status="running", checked=0, total=len(filtered), hits=0,
                                           detail=f"{len(filtered)} Short-Kandidaten, starte Analyse...")
                        _bi_background_scan(pk, "short", filtered)
                    except Exception as e:
                        import traceback
                        print(f"[BEAR] Fehler: {e}\n{traceback.format_exc()}")
                        _bi_progress_write("short", status="error", detail=f"Fehler: {e}")

                thread = threading.Thread(target=_bear_fetch_and_scan, args=(poly_key,), daemon=True)
                thread.start()
                st.info("🚀 **Short Scan gestartet** — Hintergrund-Analyse läuft...")
                st.caption("💡 Andere Tabs normal benutzen — Scan läuft im Hintergrund!")
                time.sleep(2)
                st.rerun()
            except KeyError:
                st.error("❌ POLYGON_KEY fehlt in secrets!")
            except Exception as e:
                st.error(f"❌ Short Scanner Fehler: {e}")

    # ── Ergebnis-Tabelle (gleich wie BI Scanner) ──
    st.divider()
    bear_tab_data = st.session_state.get("bear_tab_results", None)
    if bear_tab_data and len(bear_tab_data) > 0:
        import pandas as pd
        bear_df = pd.DataFrame(bear_tab_data)

        # Intraday-Modus: Entry/Stop/TP umrechnen
        _bear_mode = st.session_state.get("bear_trade_mode", "swing")
        if _bear_mode == "intraday" and "Entry" in bear_df.columns:
            # Engerer Stop + schnellere Targets für Intraday
            for idx_row in bear_df.index:
                _entry = bear_df.at[idx_row, "Entry"] if "Entry" in bear_df.columns else 0
                if _entry and _entry > 0:
                    bear_df.at[idx_row, "StopLoss"] = round(_entry * 1.005, 2)   # 0.5% Stop
                    _risk_id = bear_df.at[idx_row, "StopLoss"] - _entry
                    bear_df.at[idx_row, "TP1"] = round(_entry - _risk_id * 1.0, 2)  # 1.0R
                    bear_df.at[idx_row, "RiskReward"] = 1.0
            # Filter: Nur DollarVol >= $1M für Intraday
            if "DollarVol" in bear_df.columns:
                bear_df = bear_df[bear_df["DollarVol"] >= 1_000_000].reset_index(drop=True)

        if "BI_Score" in bear_df.columns:
            bear_df = bear_df.sort_values(by="BI_Score", ascending=False).reset_index(drop=True)

        _mode_label = "⚡ Intraday" if _bear_mode == "intraday" else "🔄 Swing"
        st.subheader(f"🐻 {len(bear_df)} Treffer — Short ⬇️ ({_mode_label})")

        # Display-Spalten
        _bear_display_cols = [c for c in ["Ticker", "Preis", "Change%", "BI_Score", "ShortBonusScore",
                                           "BI_GradeLabel", "PatternLabel", "RiskReward", "Entry",
                                           "StopLoss", "TP1", "RVOL", "DollarVol"] if c in bear_df.columns]

        bear_sel = st.dataframe(
            bear_df[_bear_display_cols] if _bear_display_cols else bear_df,
            column_config={
                "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                "Preis": st.column_config.NumberColumn("Preis", format="$%.2f"),
                "Change%": st.column_config.NumberColumn("Change%", format="%.2f%%"),
                "BI_Score": st.column_config.ProgressColumn("BI Score", format="%d", min_value=0, max_value=250),
                "ShortBonusScore": st.column_config.NumberColumn("🐻 Bonus", format="%+d", help="5 Short-Bonus-Signale: Earnings, SMA200, Gaps, MarketCap, News"),
                "BI_GradeLabel": st.column_config.TextColumn("Grade", width="medium"),
                "PatternLabel": st.column_config.TextColumn("Pattern", width="large"),
                "RiskReward": st.column_config.NumberColumn("R:R", format="%.1f"),
                "Entry": st.column_config.NumberColumn("Entry", format="$%.2f"),
                "StopLoss": st.column_config.NumberColumn("Stop", format="$%.2f"),
                "TP1": st.column_config.NumberColumn("TP1", format="$%.2f"),
                "RVOL": st.column_config.NumberColumn("RVOL", format="%.2f"),
                "DollarVol": st.column_config.NumberColumn("$Vol", format="$%.0f"),
            },
            use_container_width=True,
            height=min(800, 40 + len(bear_df) * 35),
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="bear_df_sel"
        )

        # ── Detail-Ansicht bei Zeilen-Klick ──
        _bear_sel_idx = None
        if bear_sel and bear_sel.selection and bear_sel.selection.rows:
            _bear_sel_idx = bear_sel.selection.rows[0]

        if _bear_sel_idx is not None and 0 <= _bear_sel_idx < len(bear_df):
            _bear_item = bear_df.iloc[_bear_sel_idx]
            st.divider()

            _bear_score = _bear_item.get("BI_Score", 0)
            _bear_grade = _bear_item.get("BI_GradeLabel", _bear_item.get("BI_Grade", "?"))
            st.markdown(f"## 🐻 {_bear_item.get('Ticker', 'N/A')} — Short Setup | {_bear_grade} ({_bear_score}/200)")

            # Metriken
            _bm1, _bm2, _bm3, _bm4 = st.columns(4)
            _bm1.metric("BI Score", f"{_bear_score}/200")
            _bm1.metric("Grade", _bear_grade)
            _bm2.metric("Konfidenz", f"{_bear_item.get('BI_Confidence', 0):.0f}%")
            _bm2.metric("R:R", f"{_bear_item.get('RiskReward', 0):.1f}")
            _bm3.metric("Entry (Short)", f"${_bear_item.get('Entry', 0):.2f}")
            _bm3.metric("Stop Loss", f"${_bear_item.get('StopLoss', 0):.2f}")
            _bm4.metric("TP1", f"${_bear_item.get('TP1', 0):.2f}")
            _bm4.metric("TP2", f"${_bear_item.get('TP2', 0):.2f}")

            st.caption(f"🎯 Range: ${_bear_item.get('RangeLow', 0):.2f}−${_bear_item.get('RangeHigh', 0):.2f} | "
                       f"Preis: ${_bear_item.get('Preis', 0):.2f} | Change: {_bear_item.get('Change%', 0):.1f}% | "
                       f"RVOL: {_bear_item.get('RVOL', 0):.2f}")

            # 🐻 Short Bonus Signals Detail
            _short_bonus = _bear_item.get("ShortBonusScore", 0)
            _short_details = _bear_item.get("ShortBonusDetails", [])
            if isinstance(_short_details, str):
                try:
                    _short_details = json.loads(_short_details) if _short_details else []
                except Exception:
                    _short_details = []
            if _short_details:
                with st.expander(f"🐻 Short Bonus Signals ({_short_bonus:+d} Punkte)", expanded=True):
                    for _sd in _short_details:
                        if isinstance(_sd, str):
                            if _sd.startswith("🔥"):
                                st.success(_sd)
                            elif _sd.startswith("✅"):
                                st.info(_sd)
                            elif _sd.startswith("⚠️"):
                                st.warning(_sd)
                            else:
                                st.caption(_sd)

            # Pattern-Warnungen
            _bear_warns = _bear_item.get("PatternWarnings", [])
            if isinstance(_bear_warns, str):
                try:
                    _bear_warns = json.loads(_bear_warns) if _bear_warns else []
                except Exception:
                    _bear_warns = []
            if _bear_warns:
                for pw in _bear_warns:
                    sev = pw.get("severity", "info")
                    if sev == "high":
                        st.error(f"**{pw.get('pattern', '')}** — {pw.get('description', '')}")
                    elif sev == "medium":
                        st.warning(f"**{pw.get('pattern', '')}** — {pw.get('description', '')}")
                    else:
                        st.info(f"**{pw.get('pattern', '')}** — {pw.get('description', '')}")

            # Confluence
            _bear_conf = _bear_item.get("Confluence", {})
            if isinstance(_bear_conf, dict) and _bear_conf.get("categories"):
                _bc_cats = _bear_conf["categories"]
                _bc_pass = sum(1 for c in _bc_cats.values() if c.get("pass"))
                _bc_total = len(_bc_cats)
                _bc_sig = _bear_conf.get("signal", "")
                _bc_act = _bear_conf.get("action", "")
                st.markdown(f"**🔥 Confluence: {_bc_pass}/{_bc_total}** {_bear_conf.get('signal_emoji', '')} **{_bc_sig}** — {_bc_act}")
                for _ck, _cv in _bc_cats.items():
                    _c_icon = "✅" if _cv.get("pass") else "❌"
                    st.markdown(f"{_c_icon} {_cv.get('emoji', '')} **{_cv.get('name', _ck)}:** {_cv.get('value', '')} ({_cv.get('detail', '')})")

            # Signal Details
            _bear_details = _bear_item.get("BI_Details", "")
            if _bear_details:
                with st.expander("🔬 Signal-Details (20 Faktoren)", expanded=False):
                    if isinstance(_bear_details, list):
                        for d in _bear_details:
                            st.markdown(f"• {d}")
                    else:
                        st.text(str(_bear_details))
    else:
        st.info("Noch keine Short-Ergebnisse. **Scan starten** um Short-Kandidaten zu finden.")


# =============================================================================
# TAB: BACKTEST LAB
# =============================================================================
with tab_backtest:
    try:
        bt_poly_key = st.secrets["POLYGON_KEY"]
        display_backtest_lab(bt_poly_key)
    except KeyError:
        st.error("❌ POLYGON_KEY fehlt! Füge ihn in Settings → Secrets hinzu.")
    except Exception as e:
        st.error(f"Fehler: {e}")

# =============================================================================
# TAB: STRATEGIE GUIDE 📖
# =============================================================================
with tab_guide:
    st.header("📖 Strategie Guide — Alle Strategien erklärt")
    st.caption("Detaillierte Erklärung jeder Strategie: Wie sie funktioniert, was sie erkennt und wann du sie einsetzen solltest.")

    # =========================================================================
    # US AKTIEN STRATEGIEN
    # =========================================================================
    with st.expander("🇺🇸 US Aktien Strategien (Haupt-Scanner)", expanded=True):

        st.subheader("📊 Momentum & Volumen")

        st.markdown("""
**Volume Surge**
Findet Aktien mit mindestens doppelt so viel Volumen wie normal (RVOL > 2.0) UND einer Aufwärtsbewegung von mindestens 2%.
Die Kombination aus hohem Volumen und Preisbewegung zeigt echtes institutionelles Interesse — nicht nur zufällige Schwankungen.
*Empfohlen für:* Day Trading, Momentum Scalps. Ideal in den ersten 1-2 Stunden nach Börsenöffnung.

**Early Momentum**
Sucht Aktien mit >3% Tagesanstieg, RVOL >1.5 und einem Close nahe dem Tageshoch (>60% der Tagesrange).
Der Close nahe High bestätigt, dass das Momentum intakt ist — kein Fake-Spike der sofort abverkauft wurde.
*Empfohlen für:* Day Trading, Intraday-Swings. Preis $5-500 filtert Penny Stocks und illiquide Werte raus.

**Whale Watch / Whale Watch Short 🐻**
Erkennt extremes Volumen (RVOL >3.0) MIT klarer Richtung — Long wenn Change >2% + Close nahe High, Short wenn Change <-2% + Close nahe Low.
RVOL >3x bedeutet 3-mal mehr gehandelt als normal — das sind keine Retail-Trader, sondern Big Player (Institutionen, Fonds).
Der Close-Position-Filter eliminiert „Churn" (hohes Volumen ohne Richtung = Distribution).
*Empfohlen für:* Swing Trading (1-5 Tage), auch Intraday. Whale Buying kann Trends über mehrere Tage antreiben.

**Penny Rockets**
Niedrigpreisige Aktien unter $5 mit RVOL >3.0 und >3% Anstieg. Zusätzlich: Mindestens $100k Dollar-Volumen für Liquidität.
*Empfohlen für:* Spekulative Day Trades. HOHES Risiko! Nur mit striktem Stop-Loss und kleiner Position.
""")

        st.subheader("🚀 Breakout & Breakdown")

        st.markdown("""
**Breakout Long**
Momentum-Ausbruch: >3% Anstieg + RVOL >1.5 + Close in den oberen 35% der Tagesrange.
Volumen bestätigt den Ausbruch — ohne Volumen sind Breakouts oft „False Breakouts" die scheitern.
*Empfohlen für:* Day Trading und Swing Trading. Entry am Breakout-Punkt, Stop-Loss unter dem letzten Konsolidierungs-Low.

**Breakdown Short**
Das Gegenstück: >3% Abverkauf + RVOL >1.5 + Close nahe dem Tagestief.
*Empfohlen für:* Short Day Trades. Achtung: Shorts haben unbegrenztes Risiko — strenger Stop-Loss pflicht!
""")

        st.subheader("📈🐻 Flag Patterns (Multi-Day)")

        st.markdown("""
**Bull Flag**
Klassisches Fortsetzungsmuster: Starke bullische Vortagskerze (+4% bis +25%), dann heute enge Konsolidierung (±2%) bei sinkendem Volumen.
Die Analyse nutzt 5 Tage History und prüft das Muster über mehrere Tage.
⚠️ **Wichtig:** „Vortag %" zeigt die Kerzenstärke (Close vs Open), NICHT die Tagesperformance!
*Empfohlen für:* Swing Trading (2-5 Tage). Entry über dem Flag-High, Target = Pole-Höhe ab Breakout.

**Bear Flag**
Das Short-Gegenstück: Starke bärische Vortagskerze (-4% bis -25%), dann Konsolidierung.
*Empfohlen für:* Short Swing Trades. Entry unter dem Flag-Low, Target = Pole-Tiefe ab Breakdown.
""")

        st.subheader("💰 Dip Buy & Reversals")

        st.markdown("""
**Dip Buy**
Qualitäts-Aktien (>$10) mit moderatem Rücksetzer (-2% bis -8%) bei NORMALEM Volumen (RVOL 0.6-1.5).
Der RVOL-Filter ist entscheidend: Normales Volumen = gesunder Rücksetzer. Hohes Volumen = möglicherweise Panik-Verkauf!
Zusätzlich: Mindestens $500k Dollar-Volumen für Liquidität.
*Empfohlen für:* Swing Trading, Position Trading. Warte auf Stabilisierung/Bounce bevor du einsteigst.

**Reversal Hunter**
Bounce nach roter Kerze: Bärische Vortagskerze (>-3%), heute Käufer (+2%+) mit erhöhtem Volumen.
⚠️ **Wichtig:** Bei Aktien im Uptrend ist das kein Reversal sondern ein „Continuation Dip Buy". Echte Reversals sind nur bei Downtrend-Aktien relevant.
*Empfohlen für:* Swing Trading. Aggressive Entry bei Bestätigung, konservativ erst am nächsten Tag.
""")

        st.subheader("🌅 Pre-Market Strategien")

        st.markdown("""
**PM Gainers / PM Losers / PM Gap & Go / PM Penny Movers**
Speziell für die Pre-Market Session (4:00-9:30 AM ET). Kein RVOL-Filter (PM-Volumen ist zu dünn).
- *PM Gainers:* >5% Anstieg vs. Previous Close → Gap-Up Momentum
- *PM Losers:* >5% Abverkauf → Gap-Down Kandidaten
- *PM Gap & Go:* Solide Aktien (>$5) mit >3% Gap → bester Momentum-Trade bei Market Open
- *PM Penny Movers:* Aktien unter $5 mit >10% Move → extrem spekulativ

*Empfohlen für:* Planung der Opening-Trades. Gap & Go ist die zuverlässigste PM-Strategie.
""")

        st.subheader("🌙 After-Hours Strategien")

        st.markdown("""
**AH Gainers / AH Losers / AH Earnings Gainers / AH Earnings Losers**
Für die After-Hours Session (16:00-20:00 ET). Erkennt Earnings-Reaktionen und News.
- *AH Earnings Gainers:* >8% Anstieg bei Aktien >$10 → positive Earnings-Überraschung
- *AH Earnings Losers:* >8% Abverkauf → negative Earnings oder schwache Guidance

*Empfohlen für:* Nächster-Tag-Planung. AH-Moves zeigen oft die Richtung für den nächsten Handelstag.
""")

        st.subheader("📈📉 Gap Strategien")

        st.markdown("""
**Gap Up / Gap Down / Gap Up (High Vol) / Gap Down (High Vol)**
Erkennt Gaps zwischen dem Schlusskurs und dem heutigen Open. Nur für Aktien (Krypto hat keine Gaps).
- *Gap Up/Down:* Mind. 2% Gap + RVOL >1.0 → reales Interesse bestätigt
- *High Vol Varianten:* 3%+ Gap + RVOL >2.0 + Preis $5-500 → stärkere Momentum-Plays

*Empfohlen für:* Day Trading bei Market Open. Gap-Fill-Trades (Erwartung dass der Gap geschlossen wird) oder Gap & Go (Momentum in Gap-Richtung).
""")

        st.subheader("🕯️ Wick Strategien")

        st.markdown("""
**Long Wick Up**
Obere Wick >35% der Gesamtrange + Change <3%. Eine lange obere Wick bedeutet: Preis stieg hoch, wurde aber wieder runterverkauft.
Das ist ein **Short-Signal** — Verkaufsdruck dominiert.
*Empfohlen für:* Short Trades (konträr). Funktioniert am besten an bekannten Widerständen.

**Long Wick Down**
Untere Wick >35% der Gesamtrange + Change >-5%. Preis fiel tief, Käufer sprangen ein → „Hammer"-Pattern.
Das ist ein **Long-Signal** — Kaufdruck auf niedrigem Niveau.
*Empfohlen für:* Long Trades (konträr). Besonders stark an bekannten Support-Zonen.
""")

        st.subheader("🔍 Insider Strategien")

        st.markdown("""
**Insider Buying / Insider Selling**
Erkennt offizielle SEC-Filings wenn Firmen-Insider (CEO, CFO, Directors, 10%+ Aktionäre) kaufen oder verkaufen.
- *Insider Buying:* Wenn jemand der die Firma am besten kennt mit eigenem Geld kauft → bullishes Signal
- *Insider Selling:* Große Verkäufe können Warnsignal sein (aber oft auch nur Steuer-/Diversifikations-Gründe)

*Empfohlen für:* Swing/Position Trading. Insider Buying ist eines der stärksten fundamentalen Signale. Am besten kombiniert mit technischer Bestätigung.
""")

        st.subheader("📦 Konsolidierungs-Strategien")

        st.markdown("""
**Consolidation 📦**
Multi-Day Seitwärtsphase: Heute UND gestern enge Range (±2%) + sinkendes Volumen (RVOL 0.2-1.2).
Nutzt 5 Tage History um echte mehrtägige Konsolidierungen zu finden.
*Empfohlen für:* Breakout-Vorbereitung. Setze Alerts für den Ausbruch aus der Range.

**Consolidation Breakout 🚀**
Ausbruch aus mehrtägiger Range: Vortag war ruhig (±3%), heute >1.5% Anstieg + RVOL >1.5.
Nutzt 15 Tage History für bessere Pattern-Erkennung.
*Empfohlen für:* Swing Trading. Der Volumen-bestätigte Breakout aus einer Konsolidierung ist eines der zuverlässigsten Setups.

**Reversal Setup 🪤**
Mehrtägiger Abverkauf + heute bullische Umkehr: Vortag -2% bis -8%, heute +2% bis +15% mit erhöhtem Volumen.
*Empfohlen für:* Aggressive Swing Trades. Höheres Risiko — Downtrends können weitergehen.

**Tight Range 📐**
Extrem enge Tagesrange (±1%) + sehr niedriges Volumen (RVOL 0.2-0.8). „Ruhe vor dem Sturm" — Richtung unklar!
*Empfohlen für:* Straddle/Strangle-Optionsstrategien oder Breakout-Alerts in beide Richtungen.

**High Volume Churn 📤**
Hohes Volumen (RVOL >1.8) OHNE Preisfortschritt (±2%). Das ist Smart Money in Aktion!
Akkumulation (bei Support) oder Distribution (bei Widerstand).
*Empfohlen für:* Analyse-Tool — zeigt wo sich Big Player positionieren. Warte auf den Breakout in die Richtung.
""")

        st.subheader("🕳️ Volume Void Strategien")

        st.markdown("""
**Volume Void Long 🕳️⬆️ / Volume Void Short 🕳️⬇️**
Analysiert das Volume Profile (wo wurde wie viel gehandelt) und findet „Löcher" — Preiszonen mit wenig historischem Volumen.
- *Long:* Preis liegt UNTER einem Volume Void → wenig Widerstand, Preis kann schnell hochschießen
- *Short:* Preis liegt ÜBER einem Volume Void → wenig Support, Preis kann schnell fallen

*Empfohlen für:* Day/Swing Trading mit klaren Zielzonen. Volume Voids werden oft schnell durchlaufen.
""")

        st.subheader("🦋 Harmonic Patterns")

        st.markdown("""
**Harmonic Bullish 🦋⬆️ / Harmonic Bearish 🦋⬇️ / Harmonic All 🦋**
Erkennt XABCD-Patterns basierend auf Fibonacci-Verhältnissen: Gartley, Bat, Butterfly, Crab.
- Der Entry erfolgt am Punkt D (Completion Zone)
- Stop-Loss knapp unter/über Punkt D
- Take Profit bei den Fibonacci-Retracement-Levels von AD

*Empfohlen für:* Erfahrene Swing Trader. Harmonic Patterns haben hohe Trefferquoten wenn korrekt identifiziert. Preis $5-500.
""")

        st.subheader("🔮 Breakout Imminent V2 (20-Signal Composite)")

        st.markdown("""
**Breakout Imminent Long 🔮⬆️ / Breakout Imminent Short 🔮⬇️**
Das fortschrittlichste Setup im Scanner. Kombiniert **20 unabhängige Signale** aus 5 Kategorien zu einem Composite Score (max 200 Punkte):

🔋 **ENERGIE (Compression-Signale):**
1. **ATR Squeeze** — Volatilität schrumpft → Energie baut sich auf
2. **Volume Dry-Up** — Volumen sinkt → Desinteresse vor Explosion
3. **StdDev Compression** — Standardabweichung sinkt → statistische Breakout-Wahrscheinlichkeit steigt
4. **Candle Body Compression** — Kerzenkörper werden kleiner (Doji-Cluster) → Gleichgewicht vor Ausbruch

📊 **MOMENTUM (Frühindikatoren):**
5. **RSI Drift** — RSI driftet Richtung 55+ (Long) / unter 45 (Short) → subtiler Bias
6. **MACD Histogram Divergenz** — Histogram dreht bei flachem Preis → unsichtbares Momentum
7. **Stochastic Momentum** — %K kreuzt %D in Extremzonen → Timing-Signal
8. **ADX Turning** — ADX < 20 + steigend → neuer Trend formiert sich JETZT

🏦 **SMART MONEY (Institutionelle Aktivität):**
9. **OBV Divergenz** — OBV vs. Preis divergiert → Smart Money positioniert sich
10. **Institutional Accumulation Days** — Hohes Volumen + kleine Kerzen → Fonds laden auf
11. **Order Block Confluence** — Breakout-Level nahe institutioneller Kauf/Verkaufszone
12. **Liquidity Pool Proximity** — Stop-Cluster über/unter Range → explosiver Stop-Hunt Effekt

📐 **STRUKTUR (Pattern-Signale):**
13. **Range Duration** — Konsolidierung >10 Tage → stärkerer Ausbruch
14. **Boundary Tests** — 4+ Tests an Resistance/Support → Wand wird schwächer
15. **Higher Lows / Lower Highs** — Strukturelle Verengung → Kontrolle wird übernommen
16. **Fibonacci Confluence** — Preis nahe Key-Fib-Level (38.2%, 50%, 61.8%) → starker Wendepunkt

🎯 **TARGETS (Breakout-Ziele):**
17. **Volume Void Above/Below** — Low-Volume-Zone über/unter Preis = Vakuum-Effekt
18. **FVG/Volume Imbalance** — Unfilled Fair Value Gaps = Preismagneten
19. **Relative Stärke** — Resilience nach Dips (Long) / Schwäche nach Bounces (Short)
20. **Close Position Clustering** — Closes nahe Highs (Long) / Lows (Short) → Bias bestätigt

**Grade-System:**
- 🏆 **S-Tier** (≥140): ELITE SETUP — höchste Wahrscheinlichkeit
- 🔥 **A-Tier** (≥120): STARK — sehr guter Kandidat
- ✅ **B-Tier** (≥100): SOLIDE — tradeworthy
- ⚠️ **C-Tier** (≥80): WATCHLIST — beobachten

**Schwellenwerte:** Long ≥100 Punkte, Short ≥90 Punkte. Nutzt 30 Tage History.
Entry/SL/TP automatisch berechnet (ATR-basierter Stop, Measured Move Targets).

*Empfohlen für:* Swing Trading (2-10 Tage). DAS beste Setup für frühzeitige Breakout-Erkennung.
""")

        st.subheader("🏦 Wyckoff Strategien")

        st.markdown("""
**Wyckoff Accumulation 🏦⬆️ / Wyckoff Distribution 🏦⬇️**
Basiert auf Richard Wyckoff's Theorie der Akkumulations- und Distributions-Phasen:
- *Accumulation:* Smart Money kauft leise in einer Trading Range. Erkennt: Enge Range + abnehmendes Volumen + steigende OBV-Divergenz.
- *Distribution:* Smart Money verkauft leise. Erkennt: Enge Range + abnehmendes Volumen + fallende OBV-Divergenz.

Nutzt 30 Tage History für die Analyse.
⚠️ **Hinweis:** Echte Wyckoff-Analyse erfordert Wochen/Monate. Diese Strategien sind vereinfachte Versionen.

*Empfohlen für:* Swing/Position Trading (Wochen). Einer der stärksten Ansätze für „Smart Money Following".
""")

        st.subheader("📈 MA Bounce Strategien")

        st.markdown("""
**SMA 50 Bounce Long / Short**
Findet Aktien die sich dem 50-Tage Simple Moving Average nähern (max 3% Abstand).
- *Long:* Preis kommt von OBEN → SMA50 als Support + SMA50 muss steigen
- *Short:* Preis kommt von UNTEN → SMA50 als Resistance + SMA50 muss fallen

**SMA 200 Bounce Long / Short 🏛️**
Wie SMA 50, aber mit dem 200-Tage MA — dem wichtigsten MA überhaupt!
Paul Tudor Jones: „Nichts ist so zuverlässig wie der SMA 200 als Support/Resistance."
*Empfohlen für:* Position Trading. SMA200-Bounces sind langfristig sehr zuverlässig.

**EMA 21 Bounce (Swing) 🎯**
Linda Raschke's „Holy Grail" Setup: Pullback zur EMA 21 im Uptrend.
EMA 21 ist DER Swing-Trading Moving Average. Max 2% Abstand.
*Empfohlen für:* Swing Trading (3-10 Tage). Sehr zuverlässig in klaren Trends.
""")

    # =========================================================================
    # FUTURES STRATEGIEN
    # =========================================================================
    with st.expander("📈 Futures Strategien", expanded=False):

        st.markdown("""
**📈 Alle zeigen**
Zeigt alle verfügbaren Futures ohne Filter. Nützlich für einen schnellen Marktüberblick.

---

**Futures Momentum 📈**
Futures mit >1% Tagesbewegung nach oben. Für Futures ist 1% bereits signifikant (gehebelt!).
*Empfohlen für:* Intraday Trend-Following. Funktioniert besonders gut bei ES, NQ, CL.

**Futures Breakdown 📉**
Futures mit >1% Abverkauf. Short-Opportunity bei klarem Verkaufsdruck.
*Empfohlen für:* Short Day Trades mit engen Stops.

**Futures Reversal 🔄**
Vorherige Session gefallen (>-2%), aktuelle Session steigend (+0.5%+). Mögliche Trendumkehr.
*Empfohlen für:* Contrarian Day Trades. Bestätigung abwarten bevor du einsteigst!

---

**Globex Gap 🌙**
Overnight Gap zwischen US Close und Asia/Europe Session. Mind. +0.3%.
*Beste Zeit:* 18:00-08:00 UTC (Globex Overnight). Gap-Fill-Trades sind hier profitabel.

**London Open Momentum 🇬🇧**
Neue Richtung bei Eröffnung der London Session (+0.2%+).
*Beste Zeit:* 07:00-10:00 UTC. Die Europa-Session setzt oft den Ton für den ganzen Tag.

**NY Open Breakout 🗽**
Breakout bei US-Börsenöffnung (+0.3%+). Höchste Liquidität des Tages.
*Beste Zeit:* 13:30-16:00 UTC. Die „Power Hour" der Futures.

---

**High Volatility ⚡**
Überdurchschnittliche Tagesbewegung (>2%). Für Futures bereits extrem — hohes Risiko, hohe Chance.
*Empfohlen für:* Erfahrene Scalper mit striktem Risikomanagement.

**Low Volatility Squeeze 🎯**
Extrem enge Tagesrange (±0.3%). Breakout-Setup — die Feder ist gespannt!
*Empfohlen für:* Breakout-Trades in beide Richtungen. Stop-Loss eng, Target weit.

**VIX Spike Alert 🔥**
Nur für VIX/VX Kontrakte! >5% Anstieg = Angst im Markt steigt massiv.
*Empfohlen für:* Absicherung und Marktanalyse. VIX >25 = erhöhte Vorsicht bei Long-Positionen.
""")

    # =========================================================================
    # FOREX STRATEGIEN
    # =========================================================================
    with st.expander("💱 Forex Strategien", expanded=False):

        st.markdown("""
**💱 Alle zeigen**
Zeigt alle Forex-Paare ohne Filter.

---

**Forex Momentum 💹**
Starke Pip-Bewegung >0.3%. Für Forex ist das bereits signifikant!
*Empfohlen für:* Intraday Trend-Following. Am besten während der London/NY Overlap.

**Forex Reversal 🔄**
Letzte 24h gefallen (-0.5% bis -3%), jetzt steigend (+0.1%+). Mögliche Umkehr.
*Empfohlen für:* Swing Trades. Bestätigung bei Support-Zonen abwarten.

**Pip Hunter 🎯**
Die größten Pip-Bewegungen des Tages (>0.5%). Top Movers nach absoluter Bewegung.
*Empfohlen für:* Momentum Scalps. Folge der Richtung der größten Moves.

---

**Tokyo Session 🇯🇵** (00:00-09:00 UTC)
Asiatische Session — oft ruhiger, aber JPY-Paare (USDJPY, EURJPY, GBPJPY, AUDJPY) sind aktiv.
*Empfohlen für:* JPY-Pair Trading, ruhigere Märkte. Enge Ranges → Range-Trading.

**London Session 🇬🇧** (08:00-17:00 UTC)
Höchste Liquidität! EUR/GBP-Paare besonders aktiv. >0.2% Move = Momentum.
*Empfohlen für:* Die produktivste Forex-Session. Trend-Trades + Breakouts.

**NY Session 🗽** (13:00-22:00 UTC)
USD-Paare am aktivsten: EURUSD, GBPUSD, USDJPY, USDCHF.
*Empfohlen für:* USD-basierte Trades. News-Trades bei US-Wirtschaftsdaten.

**London/NY Overlap 🔥** (13:00-17:00 UTC)
Maximale Liquidität und Volatilität! >0.3% Move. BESTE Trading-Zeit im Forex.
*Empfohlen für:* Alle Forex-Strategien. Hier passiert das meiste Volumen.

---

**Safe Haven Flow 🛡️**
USD/CHF oder USD/JPY fallen >0.4% = Investoren flüchten in sichere Währungen (CHF, JPY).
Risk-Off Signal für den gesamten Markt!
*Empfohlen für:* Marktanalyse und Absicherung. Bei starkem Risk-Off → vorsichtig mit Aktien-Longs.

**Risk-On Rally 🚀**
AUD/USD, NZD/USD steigen >0.3% = Investoren gehen ins Risiko.
Risk-On Signal — Aktienmärkte oft ebenfalls stark.
*Empfohlen für:* Sentiment-Analyse. Risk-On → aggressive Trades möglich.

**Exotic Movers 🌍**
Große Bewegungen (>0.5%) in Emerging Market Währungen. Hohe Volatilität!
*Empfohlen für:* Erfahrene Forex-Trader. Exotics haben wider Spreads → größere Positionsgrößen nötig.

**Range Bound 📊**
Minimale Bewegung (±0.15%). Seitwärtsmarkt = Range Trading möglich.
*Empfohlen für:* Mean-Reversion Trades innerhalb der Range. Buy Low / Sell High.
""")

    # =========================================================================
    # KRYPTO STRATEGIEN
    # =========================================================================
    with st.expander("🌐 Krypto Strategien", expanded=False):

        st.markdown("""
**🌐 Alle zeigen**
Zeigt alle Krypto-Assets ohne Filter.

⚠️ **Krypto-Besonderheiten:** RVOL bei Krypto = Turnover Ratio normalisiert (10% Turnover = RVOL 1.0).
Typische Werte: 0.3-0.8 normal, >1.0 erhöht, >2.0 sehr hoch. Keine Gaps/Pre-Post Sessions.

---

**Volume Surge**
RVOL >1.5 + Change >3%. Deutlich über Baseline = echtes Interesse, nicht nur Bot-Trading.
*Empfohlen für:* Crypto Day Trading. Besonders nach News, Listings oder Whale-Transaktionen.

**Bull Flag / Bear Flag**
Basiert auf 6-Tage Durchschnitt (nicht Vortag!). Bull Flag: Avg >+0.5%/Tag + heute flach + sinkendes Volumen.
*Empfohlen für:* Swing Trading (2-7 Tage). Krypto-Flags brechen oft explosiver aus als bei Aktien.

**Breakout Long / Breakdown Short**
>4% Bewegung + Close nahe High (Long) bzw. nahe Low (Short).
*Empfohlen für:* Momentum Trades. Bei Krypto sind 4% normal — das ist der angepasste Threshold.

**Low Cap Rockets 🚀**
Market Cap <$500M + RVOL >1.2 + Change >5%. Die explosivsten Altcoins!
*Empfohlen für:* Hochspekulative Trades. Extrem hohes Risiko! Nur mit Kapital das du verlieren kannst.

**Dip Buy**
Rücksetzer -3% bis -15% bei normalem Volumen (RVOL 0.3-1.5). Kein Panik-Dump.
*Empfohlen für:* Nachkaufen bei etablierten Coins (BTC, ETH, SOL). Nicht bei Random-Altcoins!

**Reversal Hunter**
6-Tage Trend negativ (<-1%/Tag), heute Käufer (+2%+). Mögliche Trendwende.
*Empfohlen für:* Contrarian Swing Trades. Am besten bei Coins mit starken Fundamentals.

**Early Momentum**
>3% Anstieg + RVOL >1.0. Positive Bewegung mit Volumen-Bestätigung.
*Empfohlen für:* Trend-Following. Im 24/7 Krypto-Markt = aktuelles Momentum.

**Whale Watch 🐋**
RVOL >2.5 + Change >5%. Extrem hohes Volumen mit klarer Richtung.
*Empfohlen für:* Folge den Walen! Whale-Buys bei Krypto sind oft über On-Chain Daten verifizierbar.

**Accumulation 📦**
Seitwärts (±2%) + leicht erhöhtes Volumen (RVOL 1.2-3.0). Jemand sammelt leise ein.
*Empfohlen für:* Position Trading (Wochen/Monate). Akkumulation vor großen Moves erkennen.
""")

    # =========================================================================
    # INTERNATIONALE STRATEGIEN
    # =========================================================================
    with st.expander("🌍 Internationale Aktien Strategien", expanded=False):

        st.markdown("""
⚠️ **Besonderheiten:** EU/UK/JP Aktien bewegen sich WENIGER als US-Aktien → niedrigere Thresholds!
RVOL wird nach Tageszeit normalisiert (am Morgen niedrigeres Volumen = normal).

---

**🌍 Alle zeigen** — Alle Aktien der Börse ohne Filter.

**🌍 Gewinner / 🌍 Verlierer**
Einfach: Aktien im Plus (>0.3%) bzw. Minus (<-0.3%) heute. Für EU reicht das als Filter.
*Empfohlen für:* Schnellen Marktüberblick.

**🌍 Momentum**
Change >1%. Für europäische Blue-Chips ist 1% bereits echtes Momentum!
*Empfohlen für:* Trend-Following bei DAX, FTSE, CAC Aktien.

**🌍 Breakout / 🌍 Breakdown**
>1.5% Bewegung + Close nahe High (Breakout) bzw. Low (Breakdown).
*Empfohlen für:* Day/Swing Trading an europäischen Börsen.

**🌍 Dip Buy**
Moderate Schwäche (-0.5% bis -5%). Kaufchance bei soliden Aktien.
*Empfohlen für:* Value-orientierte Trades bei europäischen Dividenden-Aktien.

**🌍 Volume Spike**
RVOL >0.4 (normalisiert!) + positive Bewegung. Bei EU-Aktien selten >1.0 untertags.
*Empfohlen für:* Erkennung von News-Events und institutioneller Aktivität.

**🌍 Reversal**
Vortag -1.5%+, heute +0.5%+. Bounce nach Schwäche.
*Empfohlen für:* Contrarian Trades. Europäische Aktien kehren schneller zum Mittelwert zurück.

**🌍 Bull Flag / 🌍 Bear Flag**
Starker Vortag (±1.5%+), heute enge Range (±1%). Angepasste Thresholds für EU!
*Empfohlen für:* Swing Trading (2-5 Tage).

**🌍 Big Movers**
Change >2%. Das ist VIEL für europäische Verhältnisse — meistens News-getrieben.
*Empfohlen für:* Event-basiertes Trading. Earnings, M&A, Profit Warnings.

**🌍 Whale Watch**
RVOL >0.5 (normalisiert nach Tageszeit). Deutlich über Durchschnitt = Big Player aktiv.
*Empfohlen für:* Smart Money Detection. Große Fonds bewegen europäische Aktien stärker als US.
""")

    # =========================================================================
    # ALLGEMEINE TIPPS
    # =========================================================================
    with st.expander("💡 Allgemeine Trading-Tipps", expanded=False):

        st.markdown("""
### Strategie-Kombination

Die besten Trades entstehen wenn **mehrere Strategien gleichzeitig** auf dieselbe Aktie zeigen:
- Volume Surge + Breakout Long = bestätigter Ausbruch
- Whale Watch + Insider Buying = Smart Money + Insider Agreement
- Consolidation Breakout + Volume Void Long = Breakout durch dünn gehandelte Zone
- Breakout Imminent + MA Bounce = statistische + technische Bestätigung

### Risikomanagement

1. **Positionsgröße:** Nie mehr als 1-2% deines Kapitals pro Trade riskieren
2. **Stop-Loss:** IMMER setzen. Kein Trade ohne Exit-Plan!
3. **Risk/Reward:** Mindestens 1:2 Verhältnis (riskiere 1 um 2 zu gewinnen)
4. **Korrelation:** Nicht 5 Tech-Aktien gleichzeitig long — das ist EIN Trade, nicht fünf!

### Zeitfilter

- **09:30-10:30 ET:** Höchste Volatilität — ideal für Momentum/Breakout Strategien
- **11:30-14:00 ET:** „Dead Zone" — weniger Volumen, mehr False Breakouts
- **14:00-16:00 ET:** „Power Hour" — Institutionelle Anpassungen, wieder mehr Volumen
- **Pre/After Market:** Weniger Liquidität, größere Spreads — Vorsicht!
""")

    st.divider()
    st.caption("📖 Alpha Station V70.7 PRO — Strategy Guide | Alle Strategien werden kontinuierlich optimiert.")

# -----------------------------------------------------------------------------
# FOOTER
# -----------------------------------------------------------------------------
st.divider()
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("Alpha Station V70.7 Pro")
with c2:
    st.caption(f"Watchlist: {len(st.session_state.watchlist)} Ticker")
with c3:
    if st.session_state.auto_refresh_enabled:
        st.caption(f"🔄 Auto-Refresh: {st.session_state.refresh_interval} Min")
    else:
        st.caption("🔄 Auto-Refresh: Aus")
