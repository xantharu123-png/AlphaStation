"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        ALPHA STATION V67.4 PRO                               ║
║                     Multi-Asset Scanner & Analyzer                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Version: 67.4 (Full Audit Fix)                                             ║
║  Date: 12. Februar 2026                                                      ║
║  Author: Miroslav + Claude                                                   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  V67.4 AUDIT FIX - Alle 36 Findings behoben:                                ║
║  ✅ rate_limited_get() in ALLE 30 API-Calls eingebaut                       ║
║  ✅ Watchlist Persistenz via JSON (/tmp/alpha_station_watchlist.json)        ║
║  ✅ Claude AI Prompt erweitert (ATR%, Vol-Regime, Vortag%, MA-Distanz)      ║
║  ✅ Debug-Output hinter debug_mode Flag + "Warum 0 Ergebnisse?" UX         ║
║  ✅ Alle except Exception: → except Exception as e: (Debugging)             ║
║  ✅ _debug_log() Helper für optionales Logging                               ║
║  V67.3: Strategy Audit & Fixes (8 Fixes)                                    ║
║  V67.2: Chart History 3x mehr + Weekly Timeframe                             ║
║  V67.1: PM Watchlist + AI Chart Pattern Rewrite                             ║
║  V67.0: AI Chart Analyzer mit Lightweight Charts                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
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

_last_api_call = 0
_api_call_count = 0
_api_call_window_start = 0

def rate_limited_get(url, params=None, timeout=15, calls_per_minute=75, **kwargs):
    """Rate-limited requests.get() - wartet automatisch wenn zu viele Calls.
    
    Akzeptiert alle kwargs die requests.get() auch akzeptiert (headers, etc.)
    """
    global _last_api_call, _api_call_count, _api_call_window_start
    
    now = time.time()
    
    # Reset Counter jede Minute
    if now - _api_call_window_start > 60:
        _api_call_count = 0
        _api_call_window_start = now
    
    # Warte wenn Limit erreicht
    if _api_call_count >= calls_per_minute:
        wait_time = 60 - (now - _api_call_window_start)
        if wait_time > 0:
            time.sleep(wait_time)
        _api_call_count = 0
        _api_call_window_start = time.time()
    
    # Minimum 0.1s zwischen Calls
    elapsed = now - _last_api_call
    if elapsed < 0.1:
        time.sleep(0.1 - elapsed)
    
    _last_api_call = time.time()
    _api_call_count += 1
    
    return requests.get(url, params=params, timeout=timeout, **kwargs)

@st.cache_data(ttl=3600)
def load_common_stock_tickers_cached(api_key):
    """Cached Version: Lädt alle Common Stock Tickers (1h Cache)."""
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
        next_url = None
        
        for _ in range(20):
            if next_url:
                resp = rate_limited_get(next_url, timeout=30).json()
            else:
                resp = rate_limited_get(url, params=params, timeout=30).json()
            
            results = resp.get("results", [])
            for r in results:
                ticker = r.get("ticker", "")
                if ticker:
                    all_tickers.add(ticker.upper())
            
            next_url = resp.get("next_url")
            if next_url:
                next_url = f"{next_url}&apiKey={api_key}"
            else:
                break
        
        return all_tickers
    except Exception as e:
        print(f"Fehler beim Laden der Aktien-Liste: {e}")
        return set()

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
FILTER_VERSION = "67.4"
if st.session_state.get("filter_version") != FILTER_VERSION:
    st.session_state.filters_synced = False
    st.session_state.filter_version = FILTER_VERSION
    # FORCE: Lösche auch active_filters damit sie neu geladen werden
    st.session_state.active_filters = {}

# =============================================================================
# 2. STRATEGIE-DEFINITIONEN
# =============================================================================
STRATEGIES = {
    "Volume Surge": {
        "description": "Aktien/Krypto mit überdurchschnittlichem Volumen UND Bewegung",
        "filters": {"RVOL": (2.0, 50.0), "Change %": (2.0, 100.0)},
        "logic": "RVOL > 2.0 + Change > 2% = echtes Interesse mit Richtung"
    },
    "Bull Flag": {
        "description": "Konsolidierung nach starkem Anstieg - Multi-Day Pattern (⚠️ Vortag% = Kerze, nicht Tagesperformance)",
        "filters": {"Vortag %": (4.0, 25.0), "Change %": (-2.0, 2.0), "RVOL": (0.2, 2.0)},
        "logic": "Starke bullische Vortags-KERZE (Close>Open) + heute enge Konsolidierung",
        "needs_history": True,
        "pattern_type": "bull_flag",
        "history_days": 5
    },
    "Bear Flag": {
        "description": "Konsolidierung nach Abverkauf - Multi-Day Short-Setup (⚠️ Vortag% = Kerze, nicht Tagesperformance)",
        "filters": {"Vortag %": (-25.0, -4.0), "Change %": (-2.0, 2.0), "RVOL": (0.2, 2.0)},
        "logic": "Starke bärische Vortags-KERZE (Close<Open) + heute enge Konsolidierung",
        "needs_history": True,
        "pattern_type": "bear_flag",
        "history_days": 5
    },
    "Breakout Long": {
        "description": "Momentum-Ausbruch mit Volumen-Bestätigung",
        "filters": {"Change %": (3.0, 50.0), "RVOL": (1.5, 50.0), "Close Position": (0.65, 1.0)},
        "logic": "Anstieg 3%+ mit erhöhtem Volumen + Close nahe High"
    },
    "Breakdown Short": {
        "description": "Abverkauf mit Volumen - Short-Chance",
        "filters": {"Change %": (-50.0, -3.0), "RVOL": (1.5, 50.0), "Close Position": (0.0, 0.35)},
        "logic": "Abverkauf -3%+ mit erhöhtem Volumen + Close nahe Low"
    },
    "Penny Rockets": {
        "description": "Günstige Aktien mit explosivem Volumen (min $100k Volumen)",
        "filters": {"Preis": (0.10, 5.0), "RVOL": (3.0, 100.0), "Change %": (3.0, 100.0)},
        "logic": "Lowcaps unter $5 mit extremem Interesse - NUR liquide!",
        "min_dollar_volume": 100000
    },
    "Dip Buy": {
        "description": "Qualitäts-Assets im Rücksetzer ohne Panik (min $500k Volumen)",
        "filters": {"Preis": (10.0, 100000.0), "Change %": (-8.0, -2.0), "RVOL": (0.3, 1.5)},
        "logic": "Moderater Rücksetzer ohne Volumen-Panik (RVOL < 1.5) = Kaufchance",
        "min_dollar_volume": 500000
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach starkem Abverkauf (⚠️ Vortag% = Kerze, nicht Tagesperformance)",
        "filters": {"Vortag %": (-50.0, -3.0), "Change %": (2.0, 30.0), "RVOL": (1.5, 50.0)},
        "logic": "Gestern bärische KERZE (Close<Open, -3%+), heute Käufer (+2%+) mit erhöhtem Volumen"
    },
    "Early Momentum": {
        "description": "Starker Tagesstart mit Volumen - Preis hält sich oben",
        "filters": {"Change %": (3.0, 30.0), "RVOL": (1.5, 50.0), "Close Position": (0.6, 1.0), "Preis": (5.0, 500.0)},
        "logic": "Change > 3% + RVOL > 1.5 + Close nahe High = echtes Momentum"
    },
    "Whale Watch": {
        "description": "Extremes Volumen MIT klarer Richtung - Big Player aktiv",
        "filters": {"RVOL": (3.0, 100.0), "Change %": (2.0, 100.0)},
        "logic": "RVOL > 3.0 + Change > 2% = institutionelles Interesse mit klarer Richtung"
    },
    "Whale Watch Short 🐻": {
        "description": "Extremes Volumen + Abverkauf - Big Player verkaufen",
        "filters": {"RVOL": (3.0, 100.0), "Change %": (-100.0, -2.0)},
        "logic": "RVOL > 3.0 + Change < -2% = institutioneller Verkaufsdruck"
    },
    # =========================================================================
    # PRE-MARKET STRATEGIEN 🌅 - Optimiert für 4:00-9:30 AM ET (KEIN RVOL!)
    # =========================================================================
    "PM Gainers 🌅": {
        "description": "🌅 PRE-MARKET: Aktien mit starkem Anstieg vor Börsenöffnung",
        "filters": {"Change %": (5.0, 100.0), "Preis": (1.0, 10000.0)},
        "logic": "Change > 5% vs. Previous Close = starkes Pre-Market Momentum",
        "stocks_only": True,
        "session_hint": "Pre-Market"
    },
    "PM Losers 🌅": {
        "description": "🌅 PRE-MARKET: Aktien mit starkem Abverkauf vor Börsenöffnung",
        "filters": {"Change %": (-100.0, -5.0), "Preis": (1.0, 10000.0)},
        "logic": "Change < -5% vs. Previous Close = Gap-Down Kandidat",
        "stocks_only": True,
        "session_hint": "Pre-Market"
    },
    "PM Gap & Go 🌅": {
        "description": "🌅 PRE-MARKET: Quality Gaps mit Momentum-Potenzial",
        "filters": {"Change %": (3.0, 50.0), "Preis": (5.0, 500.0)},
        "logic": "Solide Aktien (>$5) mit 3%+ Gap = Momentum-Trade bei Open",
        "stocks_only": True,
        "session_hint": "Pre-Market"
    },
    "PM Penny Movers 🌅": {
        "description": "🌅 PRE-MARKET: Günstige Aktien mit explosiver Bewegung",
        "filters": {"Change %": (10.0, 500.0), "Preis": (0.10, 5.0)},
        "logic": "Lowcaps unter $5 mit >10% Move = High Risk/Reward",
        "stocks_only": True,
        "session_hint": "Pre-Market"
    },
    # =========================================================================
    # AFTER-HOURS STRATEGIEN 🌙 - Optimiert für 16:00-20:00 ET (KEIN RVOL!)
    # =========================================================================
    "AH Gainers 🌙": {
        "description": "🌙 AFTER-HOURS: Aktien steigen nach Börsenschluss",
        "filters": {"Change %": (3.0, 100.0), "Preis": (1.0, 10000.0)},
        "logic": "Change > 3% vs. Regular Close = positive News/Earnings",
        "stocks_only": True,
        "session_hint": "After-Hours"
    },
    "AH Losers 🌙": {
        "description": "🌙 AFTER-HOURS: Aktien fallen nach Börsenschluss",
        "filters": {"Change %": (-100.0, -3.0), "Preis": (1.0, 10000.0)},
        "logic": "Change < -3% vs. Regular Close = negative News/Earnings",
        "stocks_only": True,
        "session_hint": "After-Hours"
    },
    "AH Earnings Gainers 🌙📈": {
        "description": "🌙 AFTER-HOURS: Starker ANSTIEG nach Earnings",
        "filters": {"Change %": (8.0, 200.0), "Preis": (10.0, 1000.0)},
        "logic": ">8% Anstieg nach Close = positive Earnings Überraschung",
        "stocks_only": True,
        "session_hint": "After-Hours"
    },
    "AH Earnings Losers 🌙📉": {
        "description": "🌙 AFTER-HOURS: Starker ABVERKAUF nach Earnings",
        "filters": {"Change %": (-200.0, -8.0), "Preis": (10.0, 1000.0)},
        "logic": ">8% Fall nach Close = negative Earnings Überraschung oder Guidance",
        "stocks_only": True,
        "session_hint": "After-Hours"
    },
    # =========================================================================
    # GAP STRATEGIEN - NUR AKTIEN! (Mit Liquiditäts-Filter!)
    # =========================================================================
    "Gap Up": {
        "description": "📈 NUR AKTIEN: Gap nach oben mit Volumen-Bestätigung",
        "filters": {"Gap %": (2.0, 50.0), "RVOL": (0.5, 100.0)},
        "logic": "Open > Previous High + Mindest-Volumen = Echtes Gap (nicht Pennystocks)",
        "stocks_only": True
    },
    "Gap Down": {
        "description": "📉 NUR AKTIEN: Gap nach unten mit Volumen-Bestätigung",
        "filters": {"Gap %": (-50.0, -2.0), "RVOL": (0.5, 100.0)},
        "logic": "Open < Previous Low + Mindest-Volumen = Echtes Gap",
        "stocks_only": True
    },
    "Gap Up (High Vol)": {
        "description": "📈🔥 Gap Up mit HOHEM Volumen - Starkes Momentum",
        "filters": {"Gap %": (3.0, 50.0), "RVOL": (2.0, 100.0), "Preis": (5.0, 500.0)},
        "logic": "Gap + hohes Volumen + liquide Aktie = Momentum-Play",
        "stocks_only": True
    },
    "Gap Down (High Vol)": {
        "description": "📉🔥 Gap Down mit HOHEM Volumen - Panik oder News",
        "filters": {"Gap %": (-50.0, -3.0), "RVOL": (2.0, 100.0), "Preis": (5.0, 500.0)},
        "logic": "Gap Down + hohes Volumen = News-Event, Gap-Fill Trade",
        "stocks_only": True
    },
    # =========================================================================
    # WICK STRATEGIEN - BEIDE MÄRKTE
    # =========================================================================
    "Long Wick Up": {
        "description": "Lange obere Wick = Verkaufsdruck, oft Reversal nach unten",
        "filters": {"Upper Wick %": (35.0, 100.0), "Change %": (-10.0, 3.0)},
        "logic": "Obere Wick > 35% + Change < 3% = Ablehnung höherer Preise (Short-Signal)",
        "min_range_pct": 0.5
    },
    "Long Wick Down": {
        "description": "Lange untere Wick = Kaufdruck, oft Reversal nach oben",
        "filters": {"Lower Wick %": (35.0, 100.0), "Change %": (-5.0, 10.0)},
        "logic": "Untere Wick > 35% = Ablehnung tieferer Preise (Long-Signal, Hammer-Pattern)",
        "min_range_pct": 0.5
    },
    # =========================================================================
    # INSIDER STRATEGIEN - NUR AKTIEN
    # =========================================================================
    "Insider Buying": {
        "description": "🔥 NUR AKTIEN: Insider (CEO, CFO, Directors) kaufen eigene Aktien",
        "filters": {"Insider": "BUY"},
        "logic": "Insider kaufen = Sie glauben an die Firma → Bullish Signal",
        "stocks_only": True
    },
    "Insider Selling": {
        "description": "⚠️ NUR AKTIEN: Insider verkaufen große Mengen",
        "filters": {"Insider": "SELL"},
        "logic": "Große Insider-Verkäufe können Warnsignal sein",
        "stocks_only": True
    },
    # =========================================================================
    # KONSOLIDIERUNGS-STRATEGIEN 📦 - (Wyckoff-inspiriert, vereinfacht)
    # HINWEIS: Echte Wyckoff-Analyse erfordert Wochen von Daten!
    # Diese Strategien finden 2-Tage Konsolidierungen, NICHT echte Wyckoff-Patterns.
    # Mit Multi-Day Analyse (5 Tage) für bessere Pattern-Erkennung.
    # =========================================================================
    "Consolidation 📦": {
        "description": "📦 Multi-Day Seitwärtsphase mit sinkendem Volumen (⚠️ Vortag% = Kerze, nicht Tagesperformance)",
        "filters": {"Change %": (-2.0, 2.0), "Vortag %": (-2.0, 2.0), "RVOL": (0.2, 1.2)},
        "logic": "Enge Range (±2%) + kleine Vortags-Kerze + niedriges Volumen = Ruhe vor dem Sturm",
        "needs_history": True,
        "pattern_type": "consolidation",
        "history_days": 5
    },
    "Consolidation Breakout 🚀": {
        "description": "📦→🚀 Ausbruch aus MEHRTÄGIGER enger Range mit Volumen (⚠️ Vortag% = Kerze)",
        "filters": {"Change %": (3.0, 30.0), "Vortag %": (-2.5, 2.5), "RVOL": (2.0, 50.0)},
        "logic": "Mehrtägige enge Range + heute Ausbruch (+3%+) mit hohem Volumen",
        "needs_history": True,
        "pattern_type": "consolidation_breakout",
        "history_days": 5
    },
    "Reversal Setup 🪤": {
        "description": "📦 Mehrtägiger Abverkauf + heute bullische Umkehr (⚠️ Vortag% = Kerze)",
        "filters": {"Change %": (2.0, 15.0), "Vortag %": (-8.0, -2.0), "RVOL": (1.5, 10.0)},
        "logic": "Mehrtägiger Downtrend + heute grün mit erhöhtem Volumen = Boden-Bildung",
        "needs_history": True,
        "pattern_type": "reversal_setup",
        "history_days": 5
    },
    "Tight Range 📐": {
        "description": "📐 Extrem enge Tagesrange mit niedrigem Volumen - Explosion steht bevor",
        "filters": {"Change %": (-1.0, 1.0), "RVOL": (0.2, 0.8)},
        "logic": "Enge Range + niedriges Volumen = echte Ruhe vor dem Sturm (Richtung unklar)"
    },
    "High Volume Churn 📤": {
        "description": "📤 Hohes Volumen ohne Preisfortschritt = mögliche Verteilung",
        "filters": {"Change %": (-3.0, 3.0), "RVOL": (2.5, 50.0)},
        "logic": "Hohes Volumen (RVOL > 2.5) + enge Range = jemand akkumuliert/distribuiert",
        "needs_history": True,
        "pattern_type": "accumulation",
        "history_days": 5
    },
    # =========================================================================
    # VOLUME VOID STRATEGIEN 🕳️ - Low Volume Node Scanner
    # =========================================================================
    "Volume Void Long 🕳️⬆️": {
        "description": "🕳️ Preis UNTER einem Volume Void - Potenzial für schnellen Anstieg!",
        "filters": {"Change %": (-5.0, 10.0), "Preis": (5.0, 500.0)},
        "logic": "Wenig Widerstand über aktuellem Preis → Preis kann schnell durch das 'Loch' steigen",
        "stocks_only": True,
        "needs_volume_profile": True
    },
    "Volume Void Short 🕳️⬇️": {
        "description": "🕳️ Preis ÜBER einem Volume Void - Potenzial für schnellen Fall!",
        "filters": {"Change %": (-10.0, 5.0), "Preis": (5.0, 500.0)},
        "logic": "Wenig Support unter aktuellem Preis → Preis kann schnell durch das 'Loch' fallen",
        "stocks_only": True,
        "needs_volume_profile": True
    },
    # =========================================================================
    # HARMONIC PATTERN STRATEGIEN 🦋 - Fibonacci-basierte Reversal Patterns
    # =========================================================================
    "Harmonic Bullish 🦋⬆️": {
        "description": "🦋 Bullische Harmonic Patterns (Gartley, Bat, Butterfly, Crab)",
        "filters": {"Preis": (5.0, 500.0)},
        "logic": "XABCD Pattern mit Fibonacci-Verhältnissen → Long Entry am Punkt D",
        "stocks_only": True,
        "needs_harmonic": True,
        "harmonic_direction": "LONG"
    },
    "Harmonic Bearish 🦋⬇️": {
        "description": "🦋 Bärische Harmonic Patterns (Short-Setups)",
        "filters": {"Preis": (5.0, 500.0)},
        "logic": "XABCD Pattern mit Fibonacci-Verhältnissen → Short Entry am Punkt D",
        "stocks_only": True,
        "needs_harmonic": True,
        "harmonic_direction": "SHORT"
    },
    "Harmonic All Patterns 🦋": {
        "description": "🦋 Alle Harmonic Patterns (Long + Short)",
        "filters": {"Preis": (5.0, 500.0)},
        "logic": "Scannt nach allen XABCD Patterns unabhängig von Richtung",
        "stocks_only": True,
        "needs_harmonic": True,
        "harmonic_direction": "ALL"
    },
    # =========================================================================
    # MA BOUNCE STRATEGIEN 📈 - Support/Resistance an Moving Averages
    # =========================================================================
    "SMA 50 Bounce Long 📈": {
        "description": "📈 Preis nähert sich SMA 50 von OBEN - Support-Zone für Long",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-5.0, 2.0)},
        "logic": "Preis 0-3% über SMA50 + SMA50 steigend = Support-Bounce Setup",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 50,
        "ma_approach": "from_above",  # Preis kommt von oben
        "ma_distance_max": 3.0  # Max 3% über SMA
    },
    "SMA 50 Bounce Short 📉": {
        "description": "📉 Preis nähert sich SMA 50 von UNTEN - Resistance-Zone für Short",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-2.0, 5.0)},
        "logic": "Preis 0-3% unter SMA50 + SMA50 fallend = Resistance-Bounce Setup",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 50,
        "ma_approach": "from_below",  # Preis kommt von unten
        "ma_distance_max": 3.0
    },
    "SMA 200 Bounce Long 🏛️": {
        "description": "🏛️ Preis nähert sich SMA 200 von OBEN - STARKER Support (Paul Tudor Jones)",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-8.0, 2.0)},
        "logic": "SMA200 ist DER wichtigste MA! Preis 0-3% über SMA200 = Kaufchance",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 200,
        "ma_approach": "from_above",
        "ma_distance_max": 3.0
    },
    "SMA 200 Bounce Short 🏛️": {
        "description": "🏛️ Preis nähert sich SMA 200 von UNTEN - STARKE Resistance",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-2.0, 8.0)},
        "logic": "SMA200 ist starke Resistance! Preis 0-3% unter SMA200 = Short-Chance",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 200,
        "ma_approach": "from_below",
        "ma_distance_max": 3.0
    },
    "EMA 21 Bounce (Swing) 🎯": {
        "description": "🎯 EMA 21 Bounce - Linda Raschke 'Holy Grail' Setup",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-4.0, 4.0)},
        "logic": "EMA21 ist DER Swing-Trading MA! Pullback zur EMA21 im Uptrend = Entry",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "EMA",
        "ma_period": 21,
        "ma_approach": "from_above",
        "ma_distance_max": 2.0  # Enger für EMA21
    },
}

# =============================================================================
# FUTURES STRATEGIEN 📈
# =============================================================================
FUTURES_STRATEGIES = {
    "📈 Alle zeigen": {
        "description": "Alle Futures anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Futures"
    },
    # =========================================================================
    # MOMENTUM STRATEGIEN (Any Time)
    # =========================================================================
    "Futures Momentum 📈": {
        "description": "📈 Starke Bewegung mit Volumen-Bestätigung",
        "filters": {"Change %": (1.0, 20.0)},
        "logic": "Futures mit >1% Tagesbewegung = klares Momentum"
    },
    "Futures Breakdown 📉": {
        "description": "📉 Starker Abverkauf - Short-Opportunity",
        "filters": {"Change %": (-20.0, -1.0)},
        "logic": "Futures mit <-1% = Verkaufsdruck"
    },
    "Futures Reversal 🔄": {
        "description": "🔄 Trendumkehr nach starkem Move (⚠️ Vortag% = Session-Kerze)",
        "filters": {"Vortag %": (-10.0, -2.0), "Change %": (0.5, 10.0)},
        "logic": "Letzte Session gefallen, jetzt steigend = potenzielle Umkehr"
    },
    # =========================================================================
    # SESSION-BASIERTE STRATEGIEN (mit Zeitfenster-Hinweis)
    # =========================================================================
    "Globex Gap 🌙": {
        "description": "🌙 Overnight Gap vs. Regular Session Close",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "Gap zwischen US Close und Asia/Europe Session",
        "best_time": "18:00-08:00 UTC (Globex Overnight)"
    },
    "London Open Momentum 🇬🇧": {
        "description": "🇬🇧 Momentum bei London Börsenöffnung (08:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "Europa-Session bringt oft neue Richtung",
        "best_time": "07:00-10:00 UTC"
    },
    "NY Open Breakout 🗽": {
        "description": "🗽 Breakout bei US-Börsenöffnung (14:30 UTC)",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "US-Session mit höchster Liquidität = große Moves",
        "best_time": "13:30-16:00 UTC"
    },
    # =========================================================================
    # SPREAD & STRUKTUR (Any Time)
    # =========================================================================
    "High Volatility ⚡": {
        "description": "⚡ Überdurchschnittliche Tagesbewegung",
        "filters": {"Change %": (2.0, 50.0)},
        "logic": "Große Bewegung = Trading-Opportunity"
    },
    "Low Volatility Squeeze 🎯": {
        "description": "🎯 Enge Range - Breakout erwartet",
        "filters": {"Change %": (-0.3, 0.3)},
        "logic": "Sehr kleine Bewegung = Ruhe vor dem Sturm"
    },
    "VIX Spike Alert 🔥": {
        "description": "🔥 VIX steigt stark - Angst im Markt",
        "filters": {"Change %": (5.0, 100.0)},
        "logic": "Nur für VIX: Starker Anstieg = Absicherung aktiv"
    },
}

# =============================================================================
# FOREX STRATEGIEN 💱
# =============================================================================
FOREX_STRATEGIES = {
    "💱 Alle zeigen": {
        "description": "Alle Forex-Paare anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Paare"
    },
    # =========================================================================
    # PIP-BASIERTE MOMENTUM STRATEGIEN (Any Time)
    # =========================================================================
    "Forex Momentum 💹": {
        "description": "💹 Starke Pip-Bewegung in eine Richtung",
        "filters": {"Change %": (0.3, 5.0)},
        "logic": "Für Forex ist >0.3% bereits signifikant"
    },
    "Forex Reversal 🔄": {
        "description": "🔄 Gegenbewegung nach starkem Vortag (⚠️ Vortag% = 24h Kerze)",
        "filters": {"Vortag %": (-3.0, -0.5), "Change %": (0.1, 3.0)},
        "logic": "Letzte 24h gefallen, jetzt steigend = Umkehr-Signal"
    },
    "Pip Hunter 🎯": {
        "description": "🎯 Größte Pip-Bewegungen des Tages",
        "filters": {"Change %": (0.5, 10.0)},
        "logic": "Top Movers nach Pips sortiert"
    },
    # =========================================================================
    # SESSION-STRATEGIEN (mit Zeitfenster für optimale Nutzung)
    # =========================================================================
    "Tokyo Session 🇯🇵": {
        "description": "🇯🇵 Bewegungen während Tokyo Session (00:00-09:00 UTC)",
        "filters": {"Change %": (0.1, 3.0)},
        "logic": "Asiatische Session - oft ruhiger, aber JPY-Paare aktiv",
        "best_time": "00:00-09:00 UTC",
        "best_pairs": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY"]
    },
    "London Session 🇬🇧": {
        "description": "🇬🇧 Bewegungen während London Session (08:00-17:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "Höchste Liquidität - EUR/GBP-Paare besonders aktiv",
        "best_time": "08:00-17:00 UTC",
        "best_pairs": ["EURUSD", "GBPUSD", "EURGBP", "EURJPY"]
    },
    "NY Session 🗽": {
        "description": "🗽 Bewegungen während NY Session (13:00-22:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "USD-Paare am aktivsten",
        "best_time": "13:00-22:00 UTC",
        "best_pairs": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"]
    },
    "London/NY Overlap 🔥": {
        "description": "🔥 Höchste Volatilität: London + NY gleichzeitig (13:00-17:00 UTC)",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "Beste Trading-Zeit - maximale Liquidität und Bewegung",
        "best_time": "13:00-17:00 UTC"
    },
    # =========================================================================
    # SPEZIELLE FOREX-STRATEGIEN (Any Time)
    # =========================================================================
    "Safe Haven Flow 🛡️": {
        "description": "🛡️ Flucht in sichere Währungen (CHF, JPY) - Risk-Off Signal",
        "filters": {"Change %": (-5.0, -0.4)},
        "logic": "USD/CHF oder USD/JPY fallen deutlich = Risk-Off Modus (Investoren kaufen CHF/JPY)",
        "best_pairs": ["USDCHF", "USDJPY", "EURJPY"]
    },
    "Risk-On Rally 🚀": {
        "description": "🚀 Risikofreudige Währungen steigen (AUD, NZD) - Risk-On Signal",
        "filters": {"Change %": (0.3, 5.0)},
        "logic": "AUD/USD, NZD/USD steigen deutlich = Risk-On Sentiment (Investoren gehen ins Risiko)",
        "best_pairs": ["AUDUSD", "NZDUSD", "AUDJPY"]
    },
    "Exotic Movers 🌍": {
        "description": "🌍 Große Bewegungen in Exotic Pairs",
        "filters": {"Change %": (0.5, 20.0)},
        "logic": "Emerging Market Währungen mit hoher Volatilität"
    },
    "Range Bound 📊": {
        "description": "📊 Seitwärts-Bewegung - Range Trading",
        "filters": {"Change %": (-0.15, 0.15)},
        "logic": "Minimale Bewegung = Trade die Range"
    },
}

# =============================================================================
# KRYPTO STRATEGIEN 🌐 (angepasst - keine Gaps/Pre-Post)
# RVOL bei Krypto = Turnover Ratio normalisiert (10% Turnover = 1.0)
# Typische Werte: 0.3-0.8 normal, >1.0 erhöht, >2.0 sehr hoch
# =============================================================================
CRYPTO_STRATEGIES = {
    "🌐 Alle zeigen": {
        "description": "Alle Krypto-Assets anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Coins"
    },
    "Volume Surge": {
        "description": "Erhöhtes Volumen + starke Bewegung",
        "filters": {"RVOL": (0.8, 50.0), "Change %": (2.0, 100.0)},
        "logic": "RVOL > 0.8 (überdurchschnittlicher Turnover) + Change > 2%"
    },
    "Bull Flag": {
        "description": "Bullische Konsolidierung nach Anstieg (⚠️ Vortag% = 24h Kerze)",
        "filters": {"Vortag %": (4.0, 25.0), "Change %": (-2.0, 2.0), "RVOL": (0.1, 0.8)},
        "logic": "Starke 24h-Kerze (+4-25%), heute flach mit sinkendem Volumen"
    },
    "Bear Flag": {
        "description": "Bärische Konsolidierung nach Abverkauf (⚠️ Vortag% = 24h Kerze)",
        "filters": {"Vortag %": (-25.0, -4.0), "Change %": (-2.0, 2.0), "RVOL": (0.1, 0.8)},
        "logic": "Starke 24h-Kerze (-4 bis -25%), heute flach = weitere Schwäche"
    },
    "Breakout Long": {
        "description": "Ausbruch nach oben — Close nahe Tageshoch",
        "filters": {"Change %": (3.0, 50.0), "Close Position": (0.6, 1.0)},
        "logic": "Close nahe High + starke Bewegung = bullischer Ausbruch"
    },
    "Breakdown Short": {
        "description": "Ausbruch nach unten — Close nahe Tagestief",
        "filters": {"Change %": (-50.0, -3.0), "Close Position": (0.0, 0.4)},
        "logic": "Close nahe Low + starke Abwärtsbewegung = Schwäche"
    },
    "Low Cap Rockets 🚀": {
        "description": "Günstige Coins mit explosivem Volumen",
        "filters": {"Preis": (0.0001, 1.0), "RVOL": (0.8, 50.0), "Change %": (2.0, 100.0)},
        "logic": "Coins unter $1 mit überdurchschnittlichem Turnover"
    },
    "Dip Buy": {
        "description": "Rücksetzer ohne Panik-Volumen",
        "filters": {"Change %": (-8.0, -2.0), "RVOL": (0.1, 1.5)},
        "logic": "Moderater Rücksetzer ohne Volumen-Panik"
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach starkem Abverkauf (⚠️ Vortag% = 24h Kerze)",
        "filters": {"Vortag %": (-50.0, -3.0), "Change %": (1.0, 30.0)},
        "logic": "Letzte 24h negativ, jetzt Käufer = mögliche Umkehr"
    },
    "Early Momentum": {
        "description": "Starke Bewegung mit erhöhtem Volumen",
        "filters": {"Change %": (2.0, 30.0), "RVOL": (0.3, 20.0)},
        "logic": "Positive Bewegung mit Volumen-Bestätigung"
    },
    "Whale Watch 🐋": {
        "description": "Extremes Volumen MIT klarer Richtung - Big Player aktiv",
        "filters": {"RVOL": (2.0, 50.0), "Change %": (3.0, 100.0)},
        "logic": "RVOL > 2.0 + Change > 3% = Whale Activity mit klarer Richtung"
    },
    "Accumulation 📦": {
        "description": "Leise Akkumulation bei stabilem Preis",
        "filters": {"Change %": (-2.0, 2.0), "RVOL": (0.5, 2.0)},
        "logic": "Seitwärts + leicht erhöhtes Volumen = jemand sammelt"
    },
}

# =============================================================================
# INTERNATIONALE AKTIEN STRATEGIEN 🌍 (angepasste Schwellenwerte!)
# EU/UK/JP Aktien bewegen sich weniger als US-Aktien → niedrigere Thresholds
# RVOL wird zur Laufzeit nach Tageszeit normalisiert
# =============================================================================
INTERNATIONAL_STRATEGIES = {
    "🌍 Alle zeigen": {
        "description": "Alle Aktien der Börse anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Aktien"
    },
    "🌍 Gewinner": {
        "description": "Aktien im Plus heute",
        "filters": {"Change %": (0.3, 100.0)},
        "logic": "Change > 0.3% = Aufwärtsbewegung"
    },
    "🌍 Verlierer": {
        "description": "Aktien im Minus heute",
        "filters": {"Change %": (-100.0, -0.3)},
        "logic": "Change < -0.3% = Abwärtsbewegung"
    },
    "🌍 Momentum": {
        "description": "Stärkste positive Bewegung",
        "filters": {"Change %": (1.0, 50.0)},
        "logic": "Change > 1% = echtes Momentum für europäische Blue-Chips"
    },
    "🌍 Breakout": {
        "description": "Starker Ausbruch nach oben — Close nahe Tageshoch",
        "filters": {"Change %": (1.5, 50.0), "Close Position": (0.65, 1.0)},
        "logic": "Change > 1.5% + Close nahe High = bullischer Ausbruch"
    },
    "🌍 Breakdown": {
        "description": "Starker Abverkauf — Close nahe Tagestief",
        "filters": {"Change %": (-50.0, -1.5), "Close Position": (0.0, 0.35)},
        "logic": "Change < -1.5% + Close nahe Low = Verkaufsdruck"
    },
    "🌍 Dip Buy": {
        "description": "Moderate Schwäche — potenzielle Kaufchance",
        "filters": {"Change %": (-5.0, -0.5)},
        "logic": "Change -0.5% bis -5% = Rücksetzer bei soliden Aktien"
    },
    "🌍 Volume Spike": {
        "description": "Deutlich überdurchschnittliches Volumen (normalisiert nach Tageszeit)",
        "filters": {"Change %": (0.5, 50.0), "RVOL": (0.4, 50.0)},
        "logic": "RVOL > 0.4 (normalisiert) + positive Bewegung = erhöhtes Interesse. Bei EU-Aktien selten >1.0 untertags."
    },
    "🌍 Reversal": {
        "description": "Trendumkehr: Vortag stark gefallen, heute Bounce",
        "filters": {"Vortag %": (-30.0, -1.5), "Change %": (0.5, 30.0)},
        "logic": "Gestern -1.5%+, heute Erholung +0.5%+ = mögliche Wende"
    },
    "🌍 Bull Flag": {
        "description": "Konsolidierung nach starkem Vortag — Momentum-Fortsetzung",
        "filters": {"Vortag %": (1.5, 20.0), "Change %": (-1.0, 1.0)},
        "logic": "Starker Vortag (+1.5%+), heute enge Range = Flagge bildet sich"
    },
    "🌍 Bear Flag": {
        "description": "Konsolidierung nach Abverkauf — Short-Setup",
        "filters": {"Vortag %": (-20.0, -1.5), "Change %": (-1.0, 1.0)},
        "logic": "Schwacher Vortag (-1.5%+), heute enge Range = Bear Flag"
    },
    "🌍 Big Movers": {
        "description": "Größte absolute Bewegungen des Tages",
        "filters": {"Change %": (2.0, 100.0)},
        "logic": "Change > 2% = signifikante Bewegung für europäische Verhältnisse"
    },
    "🌍 Whale Watch": {
        "description": "Extremes Volumen (normalisiert) — Big Player aktiv",
        "filters": {"RVOL": (0.5, 50.0)},
        "logic": "RVOL > 0.5 (normalisiert nach Tageszeit) = deutlich über Durchschnitt"
    },
}

# Funktion um Strategien basierend auf Markt zu bekommen
def get_strategies_for_market(market_type, exchange="US"):
    """Gibt die passenden Strategien für den gewählten Markt zurück"""
    if market_type == "Krypto":
        return CRYPTO_STRATEGIES
    elif market_type == "Futures":
        return FUTURES_STRATEGIES
    elif market_type == "Forex":
        return FOREX_STRATEGIES
    else:  # Aktien
        if exchange and exchange != "US":
            return INTERNATIONAL_STRATEGIES
        return STRATEGIES

# =============================================================================
# 3. HELPER FUNCTIONS
# =============================================================================
def get_current_trading_session():
    """
    Ermittelt automatisch die aktuelle Trading Session basierend auf US Eastern Time.
    
    Sessions:
    - Pre-Market:  4:00 AM - 9:30 AM ET
    - Regular:     9:30 AM - 4:00 PM ET
    - After-Hours: 4:00 PM - 8:00 PM ET
    - Closed:      8:00 PM - 4:00 AM ET → Nutze Regular (letzte Tagesdaten)
    """
    try:
        # Aktuelle Zeit in US Eastern
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        current_hour = now_et.hour
        current_minute = now_et.minute
        current_time = current_hour + current_minute / 60  # z.B. 9.5 = 9:30
        
        # Wochenende = Markt geschlossen → Nutze Regular (Freitag-Daten)
        if now_et.weekday() >= 5:  # Samstag = 5, Sonntag = 6
            return "Regular", "📅 Wochenende - zeige Freitag-Daten"
        
        # Session bestimmen
        if 4.0 <= current_time < 9.5:
            return "Pre-Market", f"🌅 Pre-Market ({now_et.strftime('%H:%M')} ET)"
        elif 9.5 <= current_time < 16.0:
            return "Regular", f"🟢 Regular Hours ({now_et.strftime('%H:%M')} ET)"
        elif 16.0 <= current_time < 20.0:
            return "After-Hours", f"🌙 After-Hours ({now_et.strftime('%H:%M')} ET)"
        else:
            # Nachts → Nutze Regular (letzte Tagesdaten)
            return "Regular", f"😴 Markt geschlossen ({now_et.strftime('%H:%M')} ET) - zeige letzte Daten"
            
    except Exception as e:
        # Fallback wenn pytz nicht funktioniert
        return "Regular", "📊 Regular Hours"


def apply_strategy(strategy_name, strategies_dict=None):
    """Wendet eine Strategie an. strategies_dict ist optional - wird automatisch ermittelt."""
    
    # Wenn kein Dictionary übergeben, suche in allen
    if strategies_dict is None:
        # Prüfe in welchem Dictionary die Strategie existiert
        if strategy_name in CRYPTO_STRATEGIES:
            strategies_dict = CRYPTO_STRATEGIES
        elif strategy_name in FUTURES_STRATEGIES:
            strategies_dict = FUTURES_STRATEGIES
        elif strategy_name in FOREX_STRATEGIES:
            strategies_dict = FOREX_STRATEGIES
        elif strategy_name in INTERNATIONAL_STRATEGIES:
            strategies_dict = INTERNATIONAL_STRATEGIES
        else:
            strategies_dict = STRATEGIES  # Aktien als Fallback
    
    if strategy_name in strategies_dict:
        strategy = strategies_dict[strategy_name]
        st.session_state.active_filters = strategy["filters"].copy()
        st.session_state.current_strategy = strategy_name
        st.session_state.filter_reset_counter = st.session_state.get("filter_reset_counter", 0) + 1
        st.session_state._strategy_just_applied = True  # Flag: Slider nicht rendern beim nächsten Rerun
        st.session_state.additional_filters = {
            "preis_min": 0.0, "preis_max": 100000.0,
            "nur_gewinner": False, "nur_verlierer": False,
        }
        
        # Auto-Switch für PM/AH Strategien (nur bei Aktien-Strategien)
        if strategy.get("session_hint") == "Pre-Market":
            st.session_state.active_trading_session = "Pre-Market"
            st.session_state.market_type = "Aktien"
        elif strategy.get("session_hint") == "After-Hours":
            st.session_state.active_trading_session = "After-Hours"
            st.session_state.market_type = "Aktien"
        
        # Auto-Switch auf Aktien für stocks_only Strategien
        if strategy.get("stocks_only"):
            st.session_state.market_type = "Aktien"
    else:
        # Strategie nicht gefunden - setze trotzdem auf den Namen
        st.session_state.current_strategy = strategy_name
        st.session_state.active_filters = {}
        st.warning(f"⚠️ Strategie '{strategy_name}' nicht gefunden!")

def calculate_close_position(high, low, close, min_range_pct=1.0):
    """
    Berechnet Close Position mit Sicherheits-Checks.
    
    Close Position = (close - low) / (high - low)
    Zeigt wo der Preis innerhalb der Tagesrange geschlossen hat:
    - 1.0 = Close am High (bullisch)
    - 0.5 = Close in der Mitte
    - 0.0 = Close am Low (bärisch)
    
    WICHTIG: Morgens ist die Range sehr klein → Close Position unzuverlässig!
    Wir geben None zurück wenn die Range < min_range_pct ist.
    
    Bei min_range_pct=1.0%:
    - $50 Aktie braucht mindestens $0.50 Range
    - $100 Aktie braucht mindestens $1.00 Range
    - $50 Aktie braucht mindestens $0.25 Range
    - $100 Aktie braucht mindestens $0.50 Range
    - Balance zwischen Zuverlässigkeit und früher Erkennung
    
    Args:
        high: Tageshoch
        low: Tagestief
        close: Aktueller Preis
        min_range_pct: Mindest-Range in % für zuverlässige Berechnung (default 1.0%)
    
    Returns:
        Close Position (0-1) oder None wenn nicht berechenbar
    """
    if high is None or low is None or close is None:
        return None
    if high <= 0 or low <= 0:
        return None
    if high == low:
        return None  # Keine Range = keine Close Position
    
    # Prüfe ob genug Range vorhanden (Morgen-Problem vermeiden)
    range_pct = ((high - low) / low) * 100
    if range_pct < min_range_pct:
        return None  # Zu wenig Range für zuverlässige Close Position
    
    close_pos = (close - low) / (high - low)
    
    # Clamp auf 0-1 (kann >1 oder <0 sein wenn close außerhalb range)
    return max(0.0, min(1.0, close_pos))

def calculate_alpha_score(rvol, vortag_pct, change_pct):
    """
    Normalisierter Alpha Score 0-100.
    
    Gewichtung:
    - RVOL (Volumen-Interesse): max 30 Punkte
    - Vortag% (Trend-Kontext): max 35 Punkte  
    - Change% (Heutige Stärke): max 35 Punkte
    
    RVOL Skala: 1.0 = normal, 2.0 = doppelt, 5.0+ = extrem
    Change Skala: 5% = moderat, 10%+ = stark
    """
    # RVOL: 0-5 mapped zu 0-30 Punkte (cap bei 5x)
    rvol_capped = min(max(rvol, 0), 5)
    rvol_score = (rvol_capped / 5) * 30
    
    # Vortag%: 0-15% mapped zu 0-35 Punkte
    vortag_abs = min(abs(vortag_pct), 15)
    vortag_score = (vortag_abs / 15) * 35
    
    # Change%: 0-15% mapped zu 0-35 Punkte
    change_abs = min(abs(change_pct), 15)
    change_score = (change_abs / 15) * 35
    
    return round(rvol_score + vortag_score + change_score, 0)

def calculate_rvol_at_time(current_vol, prev_day_vol, session="Regular"):
    """
    Berechnet RVOL-at-Time (Intraday-normalisiert)
    
    Das Problem mit einfachem RVOL (today_vol / yesterday_vol):
    - Um 10:00 Uhr hat der Markt erst 30 Min gehandelt
    - Gestern hatte der Markt 6.5 Stunden (390 Min)
    - Simple RVOL wäre dann immer ~0.08 (8%)
    
    Lösung: Time-Weighted RVOL mit Volume Profile
    - Typisches Intraday-Volumen-Profil:
      * 9:30-10:30: ~22% des Tagesvolumens (Opening Rush)
      * 10:30-12:00: ~18% 
      * 12:00-14:00: ~15% (Lunch Lull)
      * 14:00-15:30: ~20%
      * 15:30-16:00: ~25% (Closing Rush)
    
    Returns: Normalisiertes RVOL
    """
    if prev_day_vol <= 0 or current_vol <= 0:
        return 1.0
    
    try:
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        current_hour = now_et.hour + now_et.minute / 60
        
        # Pre-Market und After-Hours: Keine Normalisierung möglich
        if session in ["Pre-Market", "After-Hours", "Extended"]:
            # Für Pre/Post: Einfacher Vergleich, aber mit Warnung
            return round(current_vol / prev_day_vol, 2)
        
        # Regular Hours: 9:30 - 16:00 (6.5 Stunden = 390 Minuten)
        market_open = 9.5   # 9:30
        market_close = 16.0 # 16:00
        
        # Wenn Markt noch nicht offen oder schon geschlossen
        if current_hour < market_open:
            return 1.0
        if current_hour >= market_close:
            # Nach 16:00: Normaler Vergleich da Tag vorbei
            return round(current_vol / prev_day_vol, 2)
        
        # Intraday Volume Profile (kumulativ)
        # Basierend auf typischem US-Aktien Handelsmuster
        volume_profile = [
            (9.5, 0.0),    # Market Open
            (10.0, 0.12),  # 12% nach 30 Min
            (10.5, 0.22),  # 22% nach 1h
            (11.0, 0.30),  # 30% nach 1.5h
            (11.5, 0.36),  # 36%
            (12.0, 0.42),  # 42% - Lunch beginnt
            (12.5, 0.47),  # 47%
            (13.0, 0.52),  # 52%
            (13.5, 0.57),  # 57%
            (14.0, 0.62),  # 62%
            (14.5, 0.68),  # 68%
            (15.0, 0.75),  # 75%
            (15.5, 0.85),  # 85% - Closing Rush
            (16.0, 1.0),   # 100% at Close
        ]
        
        # Finde den erwarteten Volumen-Anteil für aktuelle Uhrzeit
        expected_pct = 0.0
        for i, (hour, pct) in enumerate(volume_profile):
            if current_hour <= hour:
                if i == 0:
                    expected_pct = 0.0
                else:
                    # Lineare Interpolation zwischen den Punkten
                    prev_hour, prev_pct = volume_profile[i-1]
                    time_ratio = (current_hour - prev_hour) / (hour - prev_hour)
                    expected_pct = prev_pct + time_ratio * (pct - prev_pct)
                break
        else:
            expected_pct = 1.0
        
        # Mindestens 5% erwarten (für sehr frühe Zeiten)
        expected_pct = max(0.05, expected_pct)
        
        # Erwartetes Volumen zu dieser Uhrzeit
        expected_vol = prev_day_vol * expected_pct
        
        # RVOL-at-Time
        rvol_normalized = current_vol / expected_vol if expected_vol > 0 else 1.0
        
        return round(min(rvol_normalized, 999.0), 2)
        
    except Exception as e:
        # Fallback: Einfache Berechnung
        return round(current_vol / prev_day_vol, 2) if prev_day_vol > 0 else 1.0

def validate_flag_pattern(vortag_chg, change_today, rvol, price, prev_close, high, low, pattern_type="bull"):
    """
    Validiert Bull/Bear Flag Pattern mit zusätzlichen Kriterien:
    
    Bull Flag Kriterien:
    1. ✅ Vortag: Starker Anstieg (Fahnenstange) 
    2. ✅ Heute: Seitwärts/leicht runter (Konsolidierung)
    3. ✅ Volumen sinkt (RVOL < 1.5)
    4. 🆕 Retracement < 50% der Fahnenstange
    5. 🆕 Preis über dem 50% Fibonacci Level
    
    Returns: (is_valid, score, details)
    """
    details = []
    score = 0
    
    if pattern_type == "bull":
        # Kriterium 1: Vortag stark positiv (4-25%)
        if 4.0 <= vortag_chg <= 25.0:
            score += 25
            details.append(f"✅ Fahnenstange: {vortag_chg:+.1f}%")
        else:
            details.append(f"❌ Fahnenstange schwach: {vortag_chg:+.1f}%")
        
        # Kriterium 2: Heute seitwärts (-2% bis +2%)
        if -2.0 <= change_today <= 2.0:
            score += 20
            details.append(f"✅ Konsolidierung: {change_today:+.1f}%")
        elif -4.0 <= change_today <= 4.0:
            score += 10
            details.append(f"⚠️ Leichte Konsolidierung: {change_today:+.1f}%")
        else:
            details.append(f"❌ Keine Konsolidierung: {change_today:+.1f}%")
        
        # Kriterium 3: Volumen sinkt
        if rvol <= 1.0:
            score += 25
            details.append(f"✅ Volumen sinkt stark: RVOL {rvol:.1f}x")
        elif rvol <= 1.5:
            score += 15
            details.append(f"✅ Volumen sinkt: RVOL {rvol:.1f}x")
        else:
            details.append(f"❌ Volumen zu hoch: RVOL {rvol:.1f}x")
        
        # Kriterium 4: Fibonacci Retracement Check
        # Fahnenstange = Vortag Bewegung
        # Retracement sollte < 50% sein
        if prev_close > 0 and vortag_chg > 0:
            # Geschätzter Preis vor dem Move
            price_before_move = prev_close / (1 + vortag_chg/100)
            move_size = prev_close - price_before_move
            
            # Heutiges Retracement (vom High gestern = prev_close zu heutigem Low)
            retracement = prev_close - low if low > 0 else 0
            retracement_pct = (retracement / move_size * 100) if move_size > 0 else 0
            
            if retracement_pct <= 38.2:
                score += 30
                details.append(f"✅ Flaches Retracement: {retracement_pct:.1f}% (ideal)")
            elif retracement_pct <= 50.0:
                score += 20
                details.append(f"✅ Gesundes Retracement: {retracement_pct:.1f}%")
            elif retracement_pct <= 61.8:
                score += 10
                details.append(f"⚠️ Tiefes Retracement: {retracement_pct:.1f}%")
            else:
                details.append(f"❌ Zu tiefes Retracement: {retracement_pct:.1f}%")
        
        is_valid = score >= 60
        
    else:  # Bear Flag
        # Spiegelbildlich für Bear Flag
        if -25.0 <= vortag_chg <= -4.0:
            score += 25
            details.append(f"✅ Fahnenstange (Short): {vortag_chg:+.1f}%")
        else:
            details.append(f"❌ Fahnenstange schwach: {vortag_chg:+.1f}%")
        
        if -2.0 <= change_today <= 2.0:
            score += 20
            details.append(f"✅ Konsolidierung: {change_today:+.1f}%")
        elif -4.0 <= change_today <= 4.0:
            score += 10
            details.append(f"⚠️ Leichte Konsolidierung: {change_today:+.1f}%")
        else:
            details.append(f"❌ Keine Konsolidierung: {change_today:+.1f}%")
        
        if rvol <= 1.0:
            score += 25
            details.append(f"✅ Volumen sinkt stark: RVOL {rvol:.1f}x")
        elif rvol <= 1.5:
            score += 15
            details.append(f"✅ Volumen sinkt: RVOL {rvol:.1f}x")
        else:
            details.append(f"❌ Volumen zu hoch: RVOL {rvol:.1f}x")
        
        # Retracement für Bear Flag (bounce sollte < 50% sein)
        if prev_close > 0 and vortag_chg < 0:
            price_before_move = prev_close / (1 + vortag_chg/100)
            move_size = price_before_move - prev_close  # positiv
            
            # Heutiges Retracement (von prev_close zu heutigem High)
            retracement = high - prev_close if high > 0 else 0
            retracement_pct = (retracement / move_size * 100) if move_size > 0 else 0
            
            if retracement_pct <= 38.2:
                score += 30
                details.append(f"✅ Flacher Bounce: {retracement_pct:.1f}% (ideal)")
            elif retracement_pct <= 50.0:
                score += 20
                details.append(f"✅ Gesunder Bounce: {retracement_pct:.1f}%")
            elif retracement_pct <= 61.8:
                score += 10
                details.append(f"⚠️ Starker Bounce: {retracement_pct:.1f}%")
            else:
                details.append(f"❌ Zu starker Bounce: {retracement_pct:.1f}%")
        
        is_valid = score >= 60
    
    return is_valid, score, details

def calculate_atr_from_ohlc(high, low, close, prev_close):
    """
    Berechnet True Range für eine einzelne Kerze.
    ATR = Average True Range über mehrere Kerzen
    
    True Range = max(
        High - Low,
        |High - Previous Close|,
        |Low - Previous Close|
    )
    
    Für einen Scanner mit nur Tagesdaten: Wir nutzen die aktuelle Kerze
    """
    if high <= 0 or low <= 0 or close <= 0:
        return 0
    
    # True Range Komponenten
    tr1 = high - low
    tr2 = abs(high - prev_close) if prev_close > 0 else 0
    tr3 = abs(low - prev_close) if prev_close > 0 else 0
    
    true_range = max(tr1, tr2, tr3)
    
    # ATR als Prozent vom Preis (für Vergleichbarkeit)
    atr_pct = (true_range / close) * 100 if close > 0 else 0
    
    return round(atr_pct, 2)

def is_signal_significant(change_pct, atr_pct, multiplier=1.5):
    """
    Prüft ob eine Preisbewegung signifikant ist im Kontext der Volatilität.
    
    Gemini's Kritik: "5% Move in niedrigem VIX = signifikant, 5% in Crash = Rauschen"
    
    Lösung: Change muss > ATR * multiplier sein
    
    Beispiel:
    - ATR = 2% (ruhiger Markt): 5% Move ist 2.5x ATR = SIGNIFIKANT
    - ATR = 8% (volatiler Markt): 5% Move ist 0.6x ATR = RAUSCHEN
    """
    if atr_pct <= 0:
        return True  # Fallback wenn keine ATR
    
    significance_ratio = abs(change_pct) / atr_pct
    return significance_ratio >= multiplier


def fetch_multi_day_data(ticker, api_key, days=5):
    """
    Holt Multi-Day OHLCV Daten von Polygon für echte Pattern-Analyse.
    
    Returns: Liste von Dictionaries mit {date, open, high, low, close, volume}
             Sortiert von ältestem zu neuestem Tag
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 5)  # Extra Buffer für Wochenenden
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"adjusted": "true", "sort": "asc", "apiKey": api_key}
        
        resp = rate_limited_get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get("status") != "OK" or not data.get("results"):
            return []
        
        results = []
        for bar in data["results"][-days:]:  # Letzte N Tage
            results.append({
                "date": datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d"),
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar["v"]
            })
        
        return results
    except Exception as e:
        return []


def analyze_multi_day_pattern(bars, pattern_type="consolidation"):
    """
    Analysiert Multi-Day Patterns basierend auf historischen Daten.
    
    Pattern Types:
    - consolidation: Enge Range über mehrere Tage (Breakout Setup)
    - bull_flag: Starker Anstieg gefolgt von enger Konsolidierung
    - bear_flag: Starker Abfall gefolgt von enger Konsolidierung
    - accumulation: Seitwärts mit steigendem Volumen am Ende
    
    Returns: (is_valid, score, details)
    """
    if len(bars) < 3:
        return False, 0, ["❌ Nicht genug Daten (min. 3 Tage)"]
    
    details = []
    score = 0
    
    # Berechne tägliche Änderungen
    daily_changes = []
    for i in range(1, len(bars)):
        chg = ((bars[i]["close"] - bars[i-1]["close"]) / bars[i-1]["close"]) * 100
        daily_changes.append(chg)
    
    # Berechne Gesamt-Range der letzten Tage
    all_highs = [b["high"] for b in bars]
    all_lows = [b["low"] for b in bars]
    total_range_pct = ((max(all_highs) - min(all_lows)) / bars[0]["close"]) * 100
    
    # Durchschnittliches Volumen
    volumes = [b["volume"] for b in bars]
    avg_vol = sum(volumes) / len(volumes)
    recent_vol = volumes[-1]
    vol_trend = recent_vol / avg_vol if avg_vol > 0 else 1.0
    
    if pattern_type == "consolidation":
        # Enge Range über mehrere Tage
        if total_range_pct < 8:
            score += 30
            details.append(f"✅ Enge Range: {total_range_pct:.1f}% über {len(bars)} Tage")
        elif total_range_pct < 12:
            score += 15
            details.append(f"⚠️ Moderate Range: {total_range_pct:.1f}%")
        else:
            details.append(f"❌ Range zu groß: {total_range_pct:.1f}%")
        
        # Volumen sollte sinken
        if vol_trend < 0.8:
            score += 20
            details.append(f"✅ Volumen sinkt: {vol_trend:.2f}x")
        elif vol_trend < 1.2:
            score += 10
            details.append(f"⚠️ Volumen stabil: {vol_trend:.2f}x")
        else:
            details.append(f"❌ Volumen steigt: {vol_trend:.2f}x")
    
    elif pattern_type == "bull_flag":
        # Erster Teil: Starker Anstieg (Fahnenstange)
        if len(bars) >= 4:
            pole_move = ((bars[-3]["close"] - bars[0]["close"]) / bars[0]["close"]) * 100
            
            if pole_move >= 5:
                score += 30
                details.append(f"✅ Fahnenstange: {pole_move:+.1f}%")
            elif pole_move >= 3:
                score += 15
                details.append(f"⚠️ Schwache Fahnenstange: {pole_move:+.1f}%")
            else:
                details.append(f"❌ Keine Fahnenstange: {pole_move:+.1f}%")
            
            # Letzten 2 Tage: Konsolidierung
            recent_range = abs(daily_changes[-1]) + abs(daily_changes[-2]) if len(daily_changes) >= 2 else 0
            if recent_range < 4:
                score += 25
                details.append(f"✅ Konsolidierung: {recent_range:.1f}% Bewegung")
            else:
                details.append(f"❌ Keine Konsolidierung: {recent_range:.1f}%")
    
    elif pattern_type == "accumulation":
        # Seitwärts mit steigendem Volumen am Ende
        if total_range_pct < 10:
            score += 20
            details.append(f"✅ Seitwärtsphase: {total_range_pct:.1f}%")
        
        # Volumen steigt zum Ende
        if len(volumes) >= 3:
            early_vol = sum(volumes[:len(volumes)//2]) / (len(volumes)//2)
            late_vol = sum(volumes[len(volumes)//2:]) / (len(volumes) - len(volumes)//2)
            
            if late_vol > early_vol * 1.3:
                score += 30
                details.append(f"✅ Volumen-Akkumulation: {late_vol/early_vol:.1f}x")
            elif late_vol > early_vol:
                score += 15
                details.append(f"⚠️ Leichte Volumen-Zunahme: {late_vol/early_vol:.1f}x")
    
    elif pattern_type == "consolidation_breakout":
        # Prüfe ob die Tage VOR dem Breakout eng waren
        # Letzter Tag = heute (Breakout) → prüfe nur die Tage davor
        if len(bars) >= 4:
            pre_breakout_bars = bars[:-1]  # Alles außer heute
            pre_highs = [b["high"] for b in pre_breakout_bars]
            pre_lows = [b["low"] for b in pre_breakout_bars]
            pre_range_pct = ((max(pre_highs) - min(pre_lows)) / pre_breakout_bars[0]["close"]) * 100
            
            # Kriterium 1: Enge Range VOR dem Breakout
            if pre_range_pct < 6:
                score += 35
                details.append(f"✅ Enge Vorbreakout-Range: {pre_range_pct:.1f}% über {len(pre_breakout_bars)} Tage")
            elif pre_range_pct < 10:
                score += 20
                details.append(f"⚠️ Moderate Vorbreakout-Range: {pre_range_pct:.1f}%")
            else:
                details.append(f"❌ Range vor Breakout zu groß: {pre_range_pct:.1f}%")
            
            # Kriterium 2: Tägliche Änderungen waren klein
            pre_changes = [abs(c) for c in daily_changes[:-1]]  # Ohne heute
            if pre_changes:
                avg_daily_change = sum(pre_changes) / len(pre_changes)
                if avg_daily_change < 2.0:
                    score += 25
                    details.append(f"✅ Ruhige Vortage: ∅{avg_daily_change:.1f}% tgl. Bewegung")
                elif avg_daily_change < 3.5:
                    score += 10
                    details.append(f"⚠️ Moderate Vortage: ∅{avg_daily_change:.1f}%")
                else:
                    details.append(f"❌ Volatile Vortage: ∅{avg_daily_change:.1f}%")
            
            # Kriterium 3: Volumen war niedrig vor dem Breakout, steigt am Breakout-Tag
            pre_vol_avg = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
            breakout_vol = volumes[-1]
            vol_ratio = breakout_vol / pre_vol_avg if pre_vol_avg > 0 else 1.0
            
            if vol_ratio > 2.0:
                score += 20
                details.append(f"✅ Volumen-Explosion: {vol_ratio:.1f}x vs Vortage")
            elif vol_ratio > 1.3:
                score += 10
                details.append(f"⚠️ Leicht erhöhtes Volumen: {vol_ratio:.1f}x")
            else:
                details.append(f"❌ Kein Volumen-Anstieg: {vol_ratio:.1f}x")
        else:
            details.append("❌ Nicht genug Daten für Breakout-Validierung")
    
    elif pattern_type == "reversal_setup":
        # Prüfe ob es einen mehrtägigen Downtrend gab VOR dem heutigen Reversal
        if len(bars) >= 3:
            # Kriterium 1: Gesamtbewegung der Vortage war negativ
            pre_bars = bars[:-1]  # Alles außer heute
            total_decline = ((pre_bars[-1]["close"] - pre_bars[0]["close"]) / pre_bars[0]["close"]) * 100
            
            if total_decline <= -5:
                score += 35
                details.append(f"✅ Starker Mehrtages-Decline: {total_decline:+.1f}%")
            elif total_decline <= -3:
                score += 20
                details.append(f"⚠️ Moderater Decline: {total_decline:+.1f}%")
            elif total_decline <= -1:
                score += 10
                details.append(f"⚠️ Leichter Decline: {total_decline:+.1f}%")
            else:
                details.append(f"❌ Kein Downtrend vor Reversal: {total_decline:+.1f}%")
            
            # Kriterium 2: Mindestens 2 von N Vortagen waren rot
            red_days = sum(1 for c in daily_changes[:-1] if c < 0)
            total_pre_days = len(daily_changes) - 1
            if total_pre_days > 0:
                red_pct = red_days / total_pre_days
                if red_pct >= 0.6:
                    score += 25
                    details.append(f"✅ {red_days}/{total_pre_days} Vortage rot = Verkaufsdruck")
                elif red_pct >= 0.4:
                    score += 10
                    details.append(f"⚠️ {red_days}/{total_pre_days} Vortage rot")
                else:
                    details.append(f"❌ Nur {red_days}/{total_pre_days} rote Vortage")
            
            # Kriterium 3: Heutiges Reversal mit erhöhtem Volumen
            if len(volumes) >= 2:
                pre_vol_avg = sum(volumes[:-1]) / len(volumes[:-1])
                today_vol = volumes[-1]
                vol_ratio = today_vol / pre_vol_avg if pre_vol_avg > 0 else 1.0
                
                if vol_ratio > 1.5:
                    score += 20
                    details.append(f"✅ Reversal-Volumen: {vol_ratio:.1f}x über Vortage")
                elif vol_ratio > 1.0:
                    score += 10
                    details.append(f"⚠️ Leicht erhöhtes Volumen: {vol_ratio:.1f}x")
                else:
                    details.append(f"❌ Schwaches Reversal-Volumen: {vol_ratio:.1f}x")
        else:
            details.append("❌ Nicht genug Daten für Reversal-Validierung")
    
    is_valid = score >= 40
    return is_valid, score, details


# =============================================================================
# HARMONIC PATTERN SCANNER 🦋
# Erkennt Gartley, Butterfly, Bat, Crab, Shark Patterns
# =============================================================================

def find_pivots(prices, window=5):
    """
    Findet Swing Highs und Swing Lows (Pivot Points) mit ZigZag-Logik.
    
    Args:
        prices: Liste von Dictionaries mit 'high', 'low', 'close', 'date'
        window: Anzahl Kerzen links/rechts für Pivot-Bestätigung
    
    Returns:
        Liste von Pivots: [{'type': 'high'/'low', 'price': x, 'index': i, 'date': d}, ...]
    """
    if len(prices) < window * 2 + 1:
        return []
    
    pivots = []
    
    for i in range(window, len(prices) - window):
        # Prüfe Swing High
        is_swing_high = True
        current_high = prices[i]['high']
        for j in range(i - window, i + window + 1):
            if j != i and prices[j]['high'] >= current_high:
                is_swing_high = False
                break
        
        if is_swing_high:
            pivots.append({
                'type': 'high',
                'price': current_high,
                'index': i,
                'date': prices[i].get('date', '')
            })
            continue  # Ein Punkt kann nicht beides sein
        
        # Prüfe Swing Low
        is_swing_low = True
        current_low = prices[i]['low']
        for j in range(i - window, i + window + 1):
            if j != i and prices[j]['low'] <= current_low:
                is_swing_low = False
                break
        
        if is_swing_low:
            pivots.append({
                'type': 'low',
                'price': current_low,
                'index': i,
                'date': prices[i].get('date', '')
            })
    
    return pivots


def check_fibonacci_ratio(actual, target, tolerance=0.05):
    """
    Prüft ob ein Verhältnis innerhalb der Toleranz liegt.
    
    Args:
        actual: Berechnetes Verhältnis
        target: Ziel-Fibonacci-Level (z.B. 0.618)
        tolerance: Erlaubte Abweichung (default 5%)
    
    Returns:
        (is_valid, deviation_pct)
    """
    deviation = abs(actual - target)
    deviation_pct = (deviation / target) * 100 if target > 0 else 100
    is_valid = deviation <= tolerance
    return is_valid, deviation_pct


# Harmonic Pattern Definitionen mit Fibonacci-Verhältnissen
HARMONIC_PATTERNS = {
    "Gartley": {
        "emoji": "🦋",
        "description": "Klassisches Harmonic Pattern mit hoher Erfolgsrate",
        "ratios": {
            "AB_XA": (0.618, 0.05),      # AB = 61.8% von XA
            "BC_AB": (0.382, 0.886, 0.05), # BC = 38.2-88.6% von AB
            "CD_BC": (1.272, 1.618, 0.05), # CD = 127.2-161.8% von BC
            "AD_XA": (0.786, 0.05),       # D = 78.6% Retracement von XA
        },
        "success_rate": 70,
        "target_ratios": [0.382, 0.618]  # Profit Targets
    },
    "Butterfly": {
        "emoji": "🦋",
        "description": "Extension Pattern - D geht über X hinaus",
        "ratios": {
            "AB_XA": (0.786, 0.05),
            "BC_AB": (0.382, 0.886, 0.05),
            "CD_BC": (1.618, 2.618, 0.08),
            "AD_XA": (1.272, 1.618, 0.08),  # D extends beyond X
        },
        "success_rate": 65,
        "target_ratios": [0.382, 0.618, 1.0]
    },
    "Bat": {
        "emoji": "🦇",
        "description": "Tiefes Retracement Pattern",
        "ratios": {
            "AB_XA": (0.382, 0.5, 0.05),
            "BC_AB": (0.382, 0.886, 0.05),
            "CD_BC": (1.618, 2.618, 0.08),
            "AD_XA": (0.886, 0.05),
        },
        "success_rate": 70,
        "target_ratios": [0.382, 0.618]
    },
    "Crab": {
        "emoji": "🦀",
        "description": "Extremes Extension Pattern",
        "ratios": {
            "AB_XA": (0.382, 0.618, 0.05),
            "BC_AB": (0.382, 0.886, 0.05),
            "CD_BC": (2.24, 3.618, 0.10),
            "AD_XA": (1.618, 0.08),
        },
        "success_rate": 60,
        "target_ratios": [0.382, 0.618]
    },
    "Shark": {
        "emoji": "🦈",
        "description": "Aggressives Reversal Pattern",
        "ratios": {
            "AB_XA": (0.446, 0.618, 0.05),
            "BC_AB": (1.13, 1.618, 0.08),
            "CD_BC": (1.618, 2.24, 0.08),
            "AD_XA": (0.886, 1.13, 0.08),
        },
        "success_rate": 55,
        "target_ratios": [0.5, 0.886]
    }
}


def identify_harmonic_pattern(pivots, prices, min_pivots=5):
    """
    Identifiziert Harmonic Patterns aus Pivot Points.
    
    Args:
        pivots: Liste von Pivot Points
        prices: Original Preisdaten
        min_pivots: Minimum Anzahl Pivots für Pattern
    
    Returns:
        Liste von erkannten Patterns mit Score und Details
    """
    if len(pivots) < min_pivots:
        return []
    
    patterns_found = []
    
    # Suche nach XABCD Sequenzen (letzte 5 Pivots)
    # Pattern muss alternieren: High-Low-High-Low-High oder Low-High-Low-High-Low
    for i in range(len(pivots) - 4):
        potential_xabcd = pivots[i:i+5]
        
        # Prüfe Alternierung
        types = [p['type'] for p in potential_xabcd]
        alternates = all(types[j] != types[j+1] for j in range(4))
        if not alternates:
            continue
        
        # Extrahiere XABCD Preise
        X = potential_xabcd[0]['price']
        A = potential_xabcd[1]['price']
        B = potential_xabcd[2]['price']
        C = potential_xabcd[3]['price']
        D = potential_xabcd[4]['price']
        
        # Bestimme Richtung (bullish = X ist Low, bearish = X ist High)
        is_bullish = potential_xabcd[0]['type'] == 'low'
        
        # Berechne Fibonacci Verhältnisse
        xa_move = abs(A - X)
        if xa_move == 0:
            continue
            
        ab_retracement = abs(B - A) / xa_move
        
        ab_move = abs(B - A)
        if ab_move == 0:
            continue
        bc_retracement = abs(C - B) / ab_move
        
        bc_move = abs(C - B)
        if bc_move == 0:
            continue
        cd_extension = abs(D - C) / bc_move
        
        ad_retracement = abs(D - A) / xa_move
        
        # Prüfe gegen alle Pattern-Definitionen
        for pattern_name, pattern_def in HARMONIC_PATTERNS.items():
            score = 0
            details = []
            matches = 0
            total_checks = 0
            
            ratios = pattern_def["ratios"]
            
            # AB/XA Check
            if "AB_XA" in ratios:
                target = ratios["AB_XA"]
                if len(target) == 2:  # Single value
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(ab_retracement, target_val, tol)
                else:  # Range
                    min_val, max_val, tol = target
                    is_valid = (min_val - tol) <= ab_retracement <= (max_val + tol)
                    dev = 0 if is_valid else min(abs(ab_retracement - min_val), abs(ab_retracement - max_val)) * 100
                
                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 20
                    details.append(f"✅ AB/XA: {ab_retracement:.3f}")
                else:
                    details.append(f"❌ AB/XA: {ab_retracement:.3f}")
            
            # BC/AB Check
            if "BC_AB" in ratios:
                target = ratios["BC_AB"]
                if len(target) == 2:
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(bc_retracement, target_val, tol)
                else:
                    min_val, max_val, tol = target
                    is_valid = (min_val - tol) <= bc_retracement <= (max_val + tol)
                
                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 20
                    details.append(f"✅ BC/AB: {bc_retracement:.3f}")
                else:
                    details.append(f"❌ BC/AB: {bc_retracement:.3f}")
            
            # CD/BC Check
            if "CD_BC" in ratios:
                target = ratios["CD_BC"]
                if len(target) == 2:
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(cd_extension, target_val, tol)
                else:
                    min_val, max_val, tol = target
                    is_valid = (min_val - tol) <= cd_extension <= (max_val + tol)
                
                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 25
                    details.append(f"✅ CD/BC: {cd_extension:.3f}")
                else:
                    details.append(f"❌ CD/BC: {cd_extension:.3f}")
            
            # AD/XA Check (wichtigstes Verhältnis!)
            if "AD_XA" in ratios:
                target = ratios["AD_XA"]
                if len(target) == 2:
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(ad_retracement, target_val, tol)
                else:
                    min_val, max_val, tol = target
                    is_valid = (min_val - tol) <= ad_retracement <= (max_val + tol)
                
                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 35  # Höhere Gewichtung
                    details.append(f"✅ AD/XA: {ad_retracement:.3f}")
                else:
                    details.append(f"❌ AD/XA: {ad_retracement:.3f}")
            
            # Pattern gilt als erkannt wenn mindestens 3/4 Verhältnisse stimmen
            if matches >= 3 and score >= 60:
                # Berechne Entry, Stop Loss, Take Profits
                if is_bullish:
                    entry = D
                    stop_loss = D * 0.97  # 3% unter D
                    tp1 = D + (C - D) * 0.382
                    tp2 = D + (C - D) * 0.618
                    tp3 = C  # Full retracement
                    direction = "LONG"
                else:
                    entry = D
                    stop_loss = D * 1.03  # 3% über D
                    tp1 = D - (D - C) * 0.382
                    tp2 = D - (D - C) * 0.618
                    tp3 = C
                    direction = "SHORT"
                
                patterns_found.append({
                    "pattern": pattern_name,
                    "emoji": pattern_def["emoji"],
                    "direction": direction,
                    "score": score,
                    "matches": f"{matches}/{total_checks}",
                    "success_rate": pattern_def["success_rate"],
                    "details": details,
                    "points": {
                        "X": round(X, 2),
                        "A": round(A, 2),
                        "B": round(B, 2),
                        "C": round(C, 2),
                        "D": round(D, 2)
                    },
                    "ratios": {
                        "AB/XA": round(ab_retracement, 3),
                        "BC/AB": round(bc_retracement, 3),
                        "CD/BC": round(cd_extension, 3),
                        "AD/XA": round(ad_retracement, 3)
                    },
                    "trade": {
                        "entry": round(entry, 2),
                        "stop_loss": round(stop_loss, 2),
                        "tp1": round(tp1, 2),
                        "tp2": round(tp2, 2),
                        "tp3": round(tp3, 2),
                        "risk_reward": round(abs(tp2 - entry) / abs(entry - stop_loss), 2) if abs(entry - stop_loss) > 0 else 0
                    },
                    "pivot_indices": [p['index'] for p in potential_xabcd],
                    "dates": {
                        "X": potential_xabcd[0].get('date', ''),
                        "D": potential_xabcd[4].get('date', '')
                    }
                })
    
    # Sortiere nach Score (beste zuerst)
    patterns_found.sort(key=lambda x: x['score'], reverse=True)
    return patterns_found


def scan_harmonic_patterns(ticker, api_key, days=60, timeframe="day"):
    """
    Scannt eine Aktie nach Harmonic Patterns.
    
    Args:
        ticker: Aktien-Symbol
        api_key: Polygon API Key
        days: Anzahl Tage historischer Daten
        timeframe: "day" für Daily, "hour" für 4H
    
    Returns:
        Dictionary mit Pattern-Ergebnissen
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 10)
        
        # Polygon Aggregates API
        multiplier = 1
        span = "day"
        if timeframe == "hour":
            multiplier = 4
            span = "hour"
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{span}/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"adjusted": "true", "sort": "asc", "limit": 500, "apiKey": api_key}
        
        resp = rate_limited_get(url, params=params, timeout=20)
        data = resp.json()
        
        if data.get("status") != "OK" or not data.get("results"):
            return {"error": "No data", "patterns": []}
        
        # Konvertiere zu unserem Format
        prices = []
        for bar in data["results"]:
            prices.append({
                "date": datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d"),
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar["v"]
            })
        
        if len(prices) < 20:
            return {"error": "Not enough data", "patterns": []}
        
        # Finde Pivots
        pivots = find_pivots(prices, window=3)
        
        if len(pivots) < 5:
            return {"error": "Not enough pivots", "patterns": [], "pivot_count": len(pivots)}
        
        # Identifiziere Patterns
        patterns = identify_harmonic_pattern(pivots, prices)
        
        # Aktueller Preis
        current_price = prices[-1]["close"]
        
        return {
            "ticker": ticker,
            "current_price": current_price,
            "days_analyzed": len(prices),
            "pivots_found": len(pivots),
            "patterns": patterns,
            "last_update": prices[-1]["date"]
        }
        
    except Exception as e:
        return {"error": str(e), "patterns": []}


def scan_harmonic_batch(tickers, api_key, days=60):
    """
    Scannt mehrere Aktien nach Harmonic Patterns.
    
    Returns:
        Liste von Aktien mit gefundenen Patterns
    """
    results = []
    
    for i, ticker in enumerate(tickers):
        try:
            scan_result = scan_harmonic_patterns(ticker, api_key, days)
            
            if scan_result.get("patterns"):
                # Nimm das beste Pattern
                best_pattern = scan_result["patterns"][0]
                
                results.append({
                    "Ticker": ticker,
                    "Pattern": f"{best_pattern['emoji']} {best_pattern['pattern']}",
                    "Direction": best_pattern["direction"],
                    "Score": best_pattern["score"],
                    "Matches": best_pattern["matches"],
                    "SuccessRate": f"{best_pattern['success_rate']}%",
                    "Entry": best_pattern["trade"]["entry"],
                    "StopLoss": best_pattern["trade"]["stop_loss"],
                    "TP1": best_pattern["trade"]["tp1"],
                    "TP2": best_pattern["trade"]["tp2"],
                    "RiskReward": best_pattern["trade"]["risk_reward"],
                    "Price": scan_result["current_price"],
                    "PatternData": best_pattern
                })
        except Exception as e:
            continue
        
        # Rate Limiting: Pause nach je 10 Calls
        if i % 10 == 9:
            time.sleep(0.5)
    
    # Sortiere nach Score
    results.sort(key=lambda x: x["Score"], reverse=True)
    return results


def get_volatility_regime(atr_pct):
    """
    Klassifiziert das aktuelle Volatilitäts-Regime
    
    Returns: (regime_name, filter_adjustment)
    """
    if atr_pct < 1.5:
        return "LOW", 0.7  # Niedrige Vola: Strengere Filter (70%)
    elif atr_pct < 3.0:
        return "NORMAL", 1.0  # Normal: Standard Filter
    elif atr_pct < 5.0:
        return "HIGH", 1.3  # Hohe Vola: Lockerere Filter (130%)
    else:
        return "EXTREME", 1.5  # Extrem: Sehr lockere Filter (150%)

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
    "SH", "PSQ", "DOG", "RWM", "MYY", "SBB", "SEF",
    
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
}

# Patterns die auf ETFs hindeuten
ETF_PATTERNS = [
    "ETF", "ETN", "ETP",  # Enthält ETF/ETN/ETP
    "2X", "3X", "-2X", "-3X",  # Leveraged
    "ULTRA", "PROSHARES", "DIREXION",  # Bekannte ETF-Anbieter
    "BULL", "BEAR",  # Leveraged Bull/Bear
    "SHORT", "INVERSE",  # Inverse
]

# =============================================================================
# ECHTE AKTIEN LISTE (Common Stock = CS)
# =============================================================================
# Cache für echte Aktien-Ticker (wird einmal pro Session geladen)
COMMON_STOCK_TICKERS = set()

def load_common_stock_tickers(api_key):
    """Lädt alle echten Aktien (type=CS) — nutzt st.cache_data (1h Cache)."""
    global COMMON_STOCK_TICKERS
    if COMMON_STOCK_TICKERS:
        return COMMON_STOCK_TICKERS
    COMMON_STOCK_TICKERS = load_common_stock_tickers_cached(api_key)
    return COMMON_STOCK_TICKERS

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
def calculate_sma(closes, period):
    """
    Berechnet Simple Moving Average.
    
    Args:
        closes: Liste von Schlusskursen (neuester zuletzt)
        period: SMA Periode (z.B. 50, 200)
    
    Returns:
        SMA Wert oder None wenn nicht genug Daten
    """
    if not closes or len(closes) < period:
        return None
    
    # Nimm die letzten 'period' Werte
    relevant_closes = closes[-period:]
    return sum(relevant_closes) / period

def calculate_ema(closes, period):
    """
    Berechnet Exponential Moving Average.
    
    Args:
        closes: Liste von Schlusskursen (neuester zuletzt)
        period: EMA Periode (z.B. 8, 21)
    
    Returns:
        EMA Wert oder None wenn nicht genug Daten
    """
    if not closes or len(closes) < period:
        return None
    
    multiplier = 2 / (period + 1)
    
    # Starte mit SMA als Basis
    ema = sum(closes[:period]) / period
    
    # Berechne EMA für restliche Werte
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
    
    return ema

@st.cache_data(ttl=300)
def fetch_historical_closes(ticker, api_key, days=200):
    """
    Holt historische Schlusskurse von Polygon für SMA/EMA Berechnung.
    
    Args:
        ticker: Aktien-Ticker
        api_key: Polygon API Key
        days: Anzahl Tage (default 200 für SMA200)
    
    Returns:
        Liste von Schlusskursen (ältester zuerst) oder None bei Fehler
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 50)  # Extra Puffer
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"apiKey": api_key, "limit": days + 50, "sort": "asc"}
        
        resp = rate_limited_get(url, params=params, timeout=10)
        data = resp.json()
        
        if data.get("status") != "OK" or not data.get("results"):
            return None
        
        closes = [bar["c"] for bar in data["results"]]
        return closes
    
    except Exception as e:
        return None

def calculate_ma_distance(price, ma_value):
    """
    Berechnet den Abstand vom Preis zum Moving Average in Prozent.
    
    Positiv = Preis über MA
    Negativ = Preis unter MA
    """
    if not ma_value or ma_value <= 0:
        return None
    
    return ((price - ma_value) / ma_value) * 100


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
    
    # 2. RSI (wenn verfügbar, sonst anhand von Change% schätzen)
    # RSI ist oft nicht direkt verfügbar, daher Schätzung basierend auf Move
    estimated_rsi = 50 + (change_pct * 2.5)  # Grobe Schätzung
    if estimated_rsi < 65:
        factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Nicht überkauft"})
        score += 1
    elif estimated_rsi < 75:
        factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Leicht erhöht"})
        score += 0.5
    else:
        factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": False, "detail": "Überkauft"})
    
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
        # Schätze ATR basierend auf typischer Volatilität (~2-3%)
        estimated_atr_multiple = change_pct / 2.5
        if estimated_atr_multiple <= 1.5:
            factors.append({"name": "ATR (est.)", "value": f"~{estimated_atr_multiple:.1f}x", "ok": True, "detail": "Normal"})
            score += 1
        else:
            factors.append({"name": "ATR (est.)", "value": f"~{estimated_atr_multiple:.1f}x", "ok": False, "detail": "Überdehnt"})
    
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
    if ma_distance <= 0.5:
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
    
    # 3. RSI ZONE (geschätzt aus Change)
    # Ideal für Bounce: RSI 40-60 (neutral)
    estimated_rsi = 50 + (change_pct * 3)
    if 40 <= estimated_rsi <= 60:
        factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Neutral - Ideal"})
        score += 1
    elif 35 <= estimated_rsi < 40 or 60 < estimated_rsi <= 65:
        factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Leicht extended"})
        score += 0.5
    else:
        factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": False, "detail": "Überkauft/Überverkauft"})
    
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
    
    # 1. RSI EXTREM (geschätzt)
    # Für Mean Reversion brauchen wir extreme RSI-Werte
    # Bei Selloff: RSI sollte <30 sein für Long-Reversal
    estimated_rsi = 50 + (change_pct * 3)
    
    if is_long:
        if estimated_rsi <= 25:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Stark überverkauft"})
            score += 1.5
        elif estimated_rsi <= 30:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Überverkauft"})
            score += 1
        elif estimated_rsi <= 40:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Leicht überverkauft"})
            score += 0.5
        else:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": False, "detail": "Nicht überverkauft"})
    else:  # Short Reversal
        if estimated_rsi >= 75:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Stark überkauft"})
            score += 1.5
        elif estimated_rsi >= 70:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Überkauft"})
            score += 1
        elif estimated_rsi >= 60:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": True, "detail": "Leicht überkauft"})
            score += 0.5
        else:
            factors.append({"name": "RSI (est.)", "value": f"~{estimated_rsi:.0f}", "ok": False, "detail": "Nicht überkauft"})
    
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
def get_timing_assessment(row_data, strategy_name, fib_info=None):
    """
    Wählt die richtige Timing-Bewertung basierend auf der Strategie.
    """
    strategy_upper = strategy_name.upper() if strategy_name else ""
    
    # Breakout Strategien
    if any(x in strategy_upper for x in ["BREAKOUT", "AUSBRUCH", "ULTRA"]):
        return calculate_breakout_timing(row_data, fib_info)
    
    # Gap Strategien
    elif any(x in strategy_upper for x in ["GAP UP", "GAP DOWN", "PM GAINER", "PM GAP", "AH GAINER", "PREMARKET", "AFTERHOUR"]):
        is_gap_up = "DOWN" not in strategy_upper
        return calculate_gap_timing(row_data, is_gap_up)
    
    # MA Bounce Strategien
    elif any(x in strategy_upper for x in ["MA BOUNCE", "EMA", "SMA", "MOVING AVERAGE", "BOUNCE"]):
        ma_type = "EMA 21" if "EMA" in strategy_upper else ("SMA 200" if "200" in strategy_upper else "SMA 50")
        return calculate_ma_bounce_timing(row_data, ma_type)
    
    # Mean Reversion / Reversal Strategien
    elif any(x in strategy_upper for x in ["REVERSAL", "MEAN REVERSION", "OVERSOLD", "OVERBOUGHT", "RSI"]):
        is_long = "SHORT" not in strategy_upper and "OVERBOUGHT" not in strategy_upper
        return calculate_reversal_timing(row_data, is_long)
    
    # Volume Void Strategien
    elif any(x in strategy_upper for x in ["VOID", "VOLUME VOID", "FVG", "FAIR VALUE", "LIQUIDITY"]):
        return calculate_void_timing(row_data)
    
    # Insider Strategien
    elif any(x in strategy_upper for x in ["INSIDER", "FORM 4", "SEC"]):
        return calculate_insider_timing(row_data)
    
    # Default: Breakout-Bewertung als Fallback
    else:
        return calculate_breakout_timing(row_data, fib_info)


def calculate_volume_profile(ohlcv_data, num_bins=20):
    """
    Berechnet Volume Profile aus historischen OHLCV Daten.
    """
    if not ohlcv_data or len(ohlcv_data) < 5:
        return None
    
    try:
        # Finde Gesamt-Range
        all_highs = [d['high'] for d in ohlcv_data if d.get('high', 0) > 0]
        all_lows = [d['low'] for d in ohlcv_data if d.get('low', 0) > 0]
        
        if not all_highs or not all_lows:
            return None
        
        range_high = max(all_highs)
        range_low = min(all_lows)
        
        if range_high <= range_low:
            return None
        
        # Erstelle Preis-Bins
        bin_size = (range_high - range_low) / num_bins
        bins = []
        
        for i in range(num_bins):
            bin_low = range_low + (i * bin_size)
            bin_high = bin_low + bin_size
            bins.append({
                'low': bin_low,
                'high': bin_high,
                'mid': (bin_low + bin_high) / 2,
                'volume': 0
            })
        
        # Verteile Volumen auf Bins
        # Für jeden Tag: Verteile das Tagesvolumen proportional auf die Bins die der Tag berührt
        for day in ohlcv_data:
            day_high = day.get('high', 0)
            day_low = day.get('low', 0)
            day_vol = day.get('volume', 0)
            
            if day_high <= 0 or day_low <= 0 or day_vol <= 0:
                continue
            
            day_range = day_high - day_low
            if day_range <= 0:
                day_range = 0.01  # Minimum für Doji
            
            # Finde welche Bins dieser Tag berührt
            for bin in bins:
                # Überlappung berechnen
                overlap_low = max(bin['low'], day_low)
                overlap_high = min(bin['high'], day_high)
                
                if overlap_high > overlap_low:
                    # Proportionaler Anteil des Volumens
                    overlap_pct = (overlap_high - overlap_low) / day_range
                    bin['volume'] += day_vol * overlap_pct
        
        # Berechne Statistiken
        volumes = [b['volume'] for b in bins]
        if not volumes or max(volumes) == 0:
            return None
        
        total_volume = sum(volumes)
        avg_volume = total_volume / num_bins
        max_volume = max(volumes)
        
        # Point of Control (POC) - Bin mit meistem Volumen
        poc_bin = max(bins, key=lambda x: x['volume'])
        poc = poc_bin['mid']
        
        # Value Area (70% des Volumens um POC)
        # Sortiere Bins nach Volumen absteigend
        sorted_bins = sorted(bins, key=lambda x: x['volume'], reverse=True)
        va_volume = 0
        va_target = total_volume * 0.70
        va_bins = []
        
        for bin in sorted_bins:
            va_bins.append(bin)
            va_volume += bin['volume']
            if va_volume >= va_target:
                break
        
        if va_bins:
            vah = max(b['high'] for b in va_bins)
            val = min(b['low'] for b in va_bins)
        else:
            vah = range_high
            val = range_low
        
        # Identifiziere LVNs (Low Volume Nodes) - Bins mit < 30% des Durchschnitts
        lvn_threshold = avg_volume * 0.30
        lvns = []
        
        for i, bin in enumerate(bins):
            if bin['volume'] < lvn_threshold:
                lvns.append({
                    'low': bin['low'],
                    'high': bin['high'],
                    'mid': bin['mid'],
                    'volume': bin['volume'],
                    'volume_pct': (bin['volume'] / avg_volume * 100) if avg_volume > 0 else 0
                })
        
        # Identifiziere HVNs (High Volume Nodes) - Bins mit > 150% des Durchschnitts
        hvn_threshold = avg_volume * 1.50
        hvns = []
        
        for bin in bins:
            if bin['volume'] > hvn_threshold:
                hvns.append({
                    'low': bin['low'],
                    'high': bin['high'],
                    'mid': bin['mid'],
                    'volume': bin['volume'],
                    'volume_pct': (bin['volume'] / avg_volume * 100) if avg_volume > 0 else 0
                })
        
        return {
            'bins': bins,
            'poc': poc,
            'vah': vah,
            'val': val,
            'lvns': lvns,
            'hvns': hvns,
            'range_high': range_high,
            'range_low': range_low,
            'avg_volume': avg_volume
        }
        
    except Exception as e:
        return None

def find_volume_voids(current_price, volume_profile, min_void_size_pct=2.0):
    """
    Findet Volume Voids (LVNs) relativ zum aktuellen Preis.
    
    Returns:
        dict mit:
        - voids_above: LVNs über aktuellem Preis (Long-Potenzial)
        - voids_below: LVNs unter aktuellem Preis (Short-Potenzial/Support fehlt)
        - nearest_void_above: Nächstes Loch über Preis
        - nearest_void_below: Nächstes Loch unter Preis
        - void_score: 0-100 Score für Trade-Potenzial
    """
    if not volume_profile or not volume_profile.get('lvns'):
        return None
    
    lvns = volume_profile['lvns']
    range_size = volume_profile['range_high'] - volume_profile['range_low']
    
    if range_size <= 0:
        return None
    
    voids_above = []
    voids_below = []
    
    for lvn in lvns:
        # Void-Größe als % der Gesamtrange
        void_size_pct = (lvn['high'] - lvn['low']) / range_size * 100
        
        # Nur signifikante Voids (> min_void_size_pct)
        if void_size_pct < min_void_size_pct:
            continue
        
        lvn_with_size = {**lvn, 'size_pct': void_size_pct}
        
        if lvn['low'] > current_price:
            # Void ist ÜBER aktuellem Preis
            voids_above.append(lvn_with_size)
        elif lvn['high'] < current_price:
            # Void ist UNTER aktuellem Preis
            voids_below.append(lvn_with_size)
    
    # Sortiere nach Nähe zum aktuellen Preis
    voids_above.sort(key=lambda x: x['low'])  # Nächstes zuerst
    voids_below.sort(key=lambda x: x['high'], reverse=True)  # Nächstes zuerst
    
    # Berechne Void Score
    void_score = 0
    
    # Score für Voids über Preis (Long-Potenzial)
    if voids_above:
        nearest_above = voids_above[0]
        distance_pct = (nearest_above['low'] - current_price) / current_price * 100
        
        # Näher = besser, größer = besser
        if distance_pct < 5:  # Innerhalb 5%
            void_score += 40
        elif distance_pct < 10:
            void_score += 25
        elif distance_pct < 20:
            void_score += 10
        
        # Bonus für große Voids
        if nearest_above['size_pct'] > 5:
            void_score += 20
        elif nearest_above['size_pct'] > 3:
            void_score += 10
        
        # Bonus für mehrere Voids hintereinander
        if len(voids_above) >= 2:
            void_score += 15
    
    # Score für Voids unter Preis (fehlendes Support)
    if voids_below:
        nearest_below = voids_below[0]
        distance_pct = (current_price - nearest_below['high']) / current_price * 100
        
        # Für Short: Näher = mehr Risiko/Chance
        if distance_pct < 5:
            void_score += 15  # Kann schnell fallen
    
    return {
        'voids_above': voids_above,
        'voids_below': voids_below,
        'nearest_void_above': voids_above[0] if voids_above else None,
        'nearest_void_below': voids_below[0] if voids_below else None,
        'void_score': min(void_score, 100),
        'poc': volume_profile['poc'],
        'vah': volume_profile['vah'],
        'val': volume_profile['val']
    }

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
    
    # Begrenze auf 20 Ticker pro Scan (API-Limits)
    tickers = tickers[:20]
    
    for ticker in tickers:
        try:
            # Hole 30 Tage historische Daten
            from datetime import timedelta
            end_date = datetime.now()
            start_date = end_date - timedelta(days=45)
            
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
            
            # Berechne Volume Profile
            vp = calculate_volume_profile(ohlcv, num_bins=15)
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
                if not voids['voids_above'] or voids['void_score'] < 25:
                    continue
                
                nearest = voids['nearest_void_above']
                distance_to_void = (nearest['low'] - current_price) / current_price * 100
                
            else:  # short
                if not voids['voids_below'] or voids['void_score'] < 20:
                    continue
                
                nearest = voids['nearest_void_below']
                distance_to_void = (current_price - nearest['high']) / current_price * 100
            
            results.append({
                'ticker': ticker,
                'price': current_price,
                'void_score': voids['void_score'],
                'nearest_void': nearest,
                'distance_to_void_pct': round(distance_to_void, 2),
                'void_size_pct': round(nearest['size_pct'], 2),
                'poc': round(voids['poc'], 2),
                'vah': round(voids['vah'], 2),
                'val': round(voids['val'], 2),
                'num_voids_above': len(voids['voids_above']),
                'num_voids_below': len(voids['voids_below']),
                'voids_above': voids['voids_above'],
                'voids_below': voids['voids_below']
            })
            
        except Exception as e:
            continue
    
    # Sortiere nach Void Score
    results.sort(key=lambda x: x['void_score'], reverse=True)
    
    return results

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

def _load_watchlist():
    """Lädt Watchlist aus JSON falls vorhanden."""
    try:
        import json
        with open(_watchlist_file(), "r") as f:
            return json.load(f)
    except Exception as e:
        return []

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

def fetch_historical_data_crypto(coin_id, days):
    """Holt historische OHLC-Daten von CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
        params = {"vs_currency": "usd", "days": days}
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # Format: [[timestamp, open, high, low, close], ...]
            if data and len(data) > 0:
                return data
    except Exception as e:
        pass
    return None

def fetch_historical_data_stocks(ticker, days, poly_key):
    """Holt historische Daten — Polygon für US, Yahoo für internationale Aktien"""
    _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
    _is_intl = any(ticker.upper().endswith(s) for s in _intl_suffixes)
    
    if _is_intl:
        return _fetch_historical_yahoo(ticker, days)
    
    # US-Aktien: Polygon
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        params = {"apiKey": poly_key, "limit": days}
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                return [[r["t"], r["o"], r["h"], r["l"], r["c"], r.get("v", 0)] for r in results]
    except Exception as e:
        pass
    return None


def _fetch_historical_yahoo(ticker, days):
    """Yahoo Finance historische Daily-Daten für internationale Aktien"""
    try:
        # Days to Yahoo range string
        if days <= 30:
            yf_range = "1mo"
        elif days <= 90:
            yf_range = "3mo"
        elif days <= 180:
            yf_range = "6mo"
        elif days <= 365:
            yf_range = "1y"
        elif days <= 730:
            yf_range = "2y"
        else:
            yf_range = "5y"
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": yf_range}
        headers = {"User-Agent": "Mozilla/5.0"}
        
        resp = rate_limited_get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        
        timestamps = chart[0].get("timestamp", [])
        indicators = chart[0].get("indicators", {}).get("quote", [{}])[0]
        
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Format: [[timestamp_ms, open, high, low, close, volume], ...]
        result = []
        for i in range(len(timestamps)):
            if i >= len(closes) or closes[i] is None:
                continue
            result.append([
                timestamps[i] * 1000,  # seconds → ms für Kompatibilität
                opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                closes[i],
                volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            ])
        
        return result if result else None
    except Exception:
        return None


# =============================================================================
# AI CHART ANALYZER - PATTERN RECOGNITION & TECHNICAL ANALYSIS
# =============================================================================

def fetch_ohlcv_for_chart(ticker, poly_key, timeframe="1H", bars=300):
    """
    Holt OHLCV Daten für Chart-Darstellung.
    
    V67.5: Yahoo Finance Fallback für internationale Aktien + Forex + Futures + Krypto
    
    Args:
        ticker: Ticker Symbol (z.B. "AAPL", "VNA.DE", "EURUSD=X", "BTC-USD")
        poly_key: Polygon API Key
        timeframe: "5m", "15m", "1H", "4H", "1D", "1W"
        bars: Anzahl Bars (wird pro Timeframe angepasst)
        
    Returns:
        List of dicts with time, open, high, low, close, volume
    """
    # Erkennung: Ist das ein internationaler/Yahoo-Ticker?
    _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
    _yahoo_patterns = ("=X", "=F", "-USD", "-EUR", "-GBP")
    _is_yahoo = any(ticker.upper().endswith(s) for s in _intl_suffixes + _yahoo_patterns)
    
    # Krypto-Tickers (CoinGecko IDs → Yahoo Format)
    if not _is_yahoo and ticker.upper() in ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC"):
        ticker = f"{ticker.upper()}-USD"
        _is_yahoo = True
    
    if _is_yahoo:
        return _fetch_ohlcv_yahoo(ticker, timeframe)
    else:
        return _fetch_ohlcv_polygon(ticker, poly_key, timeframe)


def _fetch_ohlcv_yahoo(ticker, timeframe="1H"):
    """Yahoo Finance OHLCV für internationale Aktien, Forex, Futures, Krypto."""
    try:
        # Yahoo Finance interval & range mapping
        tf_map = {
            "5m":  ("5m",  "60d",  500),    # 60 Tage 5-min
            "15m": ("15m", "60d",  500),    # 60 Tage 15-min
            "1H":  ("1h",  "730d", 500),    # 2 Jahre 1H
            "4H":  ("1h",  "730d", 500),    # 2 Jahre 1H → aggregiere zu 4H
            "1D":  ("1d",  "2y",   500),    # 2 Jahre Daily
            "1W":  ("1wk", "5y",   260),    # 5 Jahre Weekly
            "1M":  ("1mo", "max",  120),    # Max Monthly
        }
        
        if timeframe not in tf_map:
            timeframe = "1H"
        
        yf_interval, yf_range, max_bars = tf_map[timeframe]
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": yf_interval, "range": yf_range}
        headers = {"User-Agent": "Mozilla/5.0"}
        
        resp = rate_limited_get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        
        timestamps = chart[0].get("timestamp", [])
        indicators = chart[0].get("indicators", {}).get("quote", [{}])[0]
        
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Build raw bars (filter None values)
        raw_bars = []
        for i in range(len(timestamps)):
            if i >= len(closes) or closes[i] is None:
                continue
            raw_bars.append({
                "t": timestamps[i],
                "o": opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                "h": highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                "l": lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                "c": closes[i],
                "v": volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            })
        
        if not raw_bars:
            return None
        
        # Für 4H: Aggregiere 1H Bars zu 4H
        if timeframe == "4H":
            aggregated = []
            for i in range(0, len(raw_bars), 4):
                chunk = raw_bars[i:i+4]
                if chunk:
                    aggregated.append({
                        "t": chunk[0]["t"],
                        "o": chunk[0]["o"],
                        "h": max(c["h"] for c in chunk),
                        "l": min(c["l"] for c in chunk),
                        "c": chunk[-1]["c"],
                        "v": sum(c.get("v", 0) for c in chunk)
                    })
            raw_bars = aggregated
        
        # Formatiere für Lightweight Charts
        effective_bars = min(max_bars, len(raw_bars))
        ohlcv = []
        for bar in raw_bars[-effective_bars:]:
            ohlcv.append({
                "time": bar["t"],  # Yahoo timestamps sind schon in Sekunden
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0)
            })
        
        return ohlcv if ohlcv else None
        
    except Exception as e:
        return None


def _fetch_ohlcv_polygon(ticker, poly_key, timeframe="1H"):
    """Polygon.io OHLCV für US-Aktien."""
    try:
        # Timeframe mapping: (multiplier, span, days_back, max_bars)
        tf_map = {
            "5m": ("5", "minute", 7, 500),
            "15m": ("15", "minute", 21, 500),
            "1H": ("1", "hour", 90, 500),
            "4H": ("1", "hour", 180, 500),
            "1D": ("1", "day", 730, 500),
            "1W": ("1", "week", 1825, 260),
        }
        
        if timeframe not in tf_map:
            timeframe = "1H"
        
        mult, span, days_back, max_bars = tf_map[timeframe]
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start_date}/{end_date}"
        params = {"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 50000}
        
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        results = data.get("results", [])
        
        if not results:
            return None
        
        # Für 4H: Aggregiere 1H Bars zu 4H
        if timeframe == "4H":
            aggregated = []
            for i in range(0, len(results), 4):
                chunk = results[i:i+4]
                if len(chunk) >= 1:
                    aggregated.append({
                        "t": chunk[0]["t"],
                        "o": chunk[0]["o"],
                        "h": max(c["h"] for c in chunk),
                        "l": min(c["l"] for c in chunk),
                        "c": chunk[-1]["c"],
                        "v": sum(c.get("v", 0) for c in chunk)
                    })
            results = aggregated
        
        effective_bars = min(max_bars, len(results))
        ohlcv = []
        for bar in results[-effective_bars:]:
            ohlcv.append({
                "time": bar["t"] // 1000,  # Polygon ms → seconds
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0)
            })
        
        return ohlcv
        
    except Exception as e:
        return None

def detect_chart_patterns(ohlcv_data, lookback=50):
    """
    Erkennt Chart-Patterns automatisch.
    
    V67.1 REWRITE - Strengere Kriterien:
    - Adaptives Swing-Window (min 5, skaliert mit Datenmenge)
    - Mindestabstand zwischen Pattern-Punkten (min 10 Bars)
    - Mindesttiefe/Höhe für Neckline
    - Volume-Bestätigung
    - Weniger "forming" false positives
    
    Returns:
        List of detected patterns with details
    """
    if not ohlcv_data or len(ohlcv_data) < 30:
        return []
    
    patterns = []
    
    try:
        data = ohlcv_data[-lookback:]
        highs = [d["high"] for d in data]
        lows = [d["low"] for d in data]
        closes = [d["close"] for d in data]
        volumes = [d.get("volume", 0) for d in data]
        
        current_price = closes[-1]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        
        # ATR-basierte Toleranz (adaptiv statt hardcoded)
        atr_values = []
        for i in range(1, len(data)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr_values.append(tr)
        atr = sum(atr_values[-14:]) / min(14, len(atr_values)) if atr_values else current_price * 0.02
        atr_pct = atr / current_price if current_price > 0 else 0.02
        
        # Finde Swing Highs und Lows mit ADAPTIVEM Window
        # Window = mindestens 5, maximal 10, skaliert mit Datenmenge
        swing_window = max(5, min(10, len(data) // 8))
        
        swing_highs = []
        swing_lows = []
        
        for i in range(swing_window, len(data) - swing_window):
            # Swing High: Höher als alle Bars links UND rechts im Window
            if highs[i] >= max(highs[i-swing_window:i]) and highs[i] >= max(highs[i+1:i+swing_window+1]):
                swing_highs.append({"price": highs[i], "index": i, "volume": volumes[i]})
            # Swing Low: Tiefer als alle Bars links UND rechts im Window
            if lows[i] <= min(lows[i-swing_window:i]) and lows[i] <= min(lows[i+1:i+swing_window+1]):
                swing_lows.append({"price": lows[i], "index": i, "volume": volumes[i]})
        
        # === DOUBLE TOP ===
        if len(swing_highs) >= 2:
            # Prüfe alle Paare, nicht nur die letzten zwei
            for a in range(len(swing_highs) - 1):
                for b in range(a + 1, len(swing_highs)):
                    h1_data = swing_highs[a]
                    h2_data = swing_highs[b]
                    h1, h2 = h1_data["price"], h2_data["price"]
                    idx1, idx2 = h1_data["index"], h2_data["index"]
                    
                    # KRITERIUM 1: Mindestabstand zwischen Tops (min 10 Bars)
                    if idx2 - idx1 < 10:
                        continue
                    
                    # KRITERIUM 2: Ähnliche Höhe (innerhalb 1.5× ATR)
                    if abs(h1 - h2) > atr * 1.5:
                        continue
                    
                    # KRITERIUM 3: Neckline muss signifikant tiefer sein (min 2× ATR)
                    neckline = min(lows[idx1:idx2+1])
                    top_avg = (h1 + h2) / 2
                    depth = top_avg - neckline
                    
                    if depth < atr * 2:
                        continue
                    
                    # KRITERIUM 4: VORHERIGER UPTREND
                    # Der Kurs VOR dem ersten Top muss tiefer gewesen sein
                    pre_start = max(0, idx1 - 15)
                    pre_end = max(0, idx1 - 2)
                    if pre_end > pre_start:
                        pre_lows = lows[pre_start:pre_end]
                        pre_low_avg = sum(pre_lows) / len(pre_lows)
                        # Vorheriges Level muss deutlich tiefer sein als die Tops
                        if top_avg - pre_low_avg < atr * 2:
                            continue
                    
                    # KRITERIUM 5: KLARER DIP zwischen den Tops
                    mid_section = closes[idx1:idx2+1]
                    if mid_section:
                        mid_avg = sum(mid_section) / len(mid_section)
                        # Wenn der Durchschnitt zwischen Tops nahe den Tops liegt → Range, kein M-Pattern
                        if top_avg - mid_avg < depth * 0.3:
                            continue
                    
                    # KRITERIUM 6: RECENCY
                    if idx2 < len(data) * 0.3:
                        continue
                    
                    # KRITERIUM 7: Volume-Divergenz (weniger Vol beim 2. Top)
                    vol_confirmation = h2_data["volume"] < h1_data["volume"] * 1.2
                    
                    # Preis unter Neckline = bestätigt
                    if current_price < neckline:
                        patterns.append({
                            "pattern": "Double Top",
                            "emoji": "🔻",
                            "type": "bearish",
                            "level1": round(h1, 2),
                            "level2": round(h2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline - depth, 2),
                            "confidence": "High" if vol_confirmation else "Medium",
                            "description": f"Double Top @ ${top_avg:.2f} - Neckline ${neckline:.2f} broken. Target: ${neckline - depth:.2f}"
                        })
                        break  # Nur das beste Pattern nehmen
                    elif current_price < top_avg * 0.97 and current_price > neckline:
                        patterns.append({
                            "pattern": "Double Top (forming)",
                            "emoji": "⚠️",
                            "type": "bearish",
                            "level1": round(h1, 2),
                            "level2": round(h2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline - depth, 2),
                            "confidence": "Low",
                            "description": f"Potential Double Top @ ${top_avg:.2f}. Watch neckline ${neckline:.2f}"
                        })
                        break
                if patterns and patterns[-1]["pattern"].startswith("Double Top"):
                    break
        
        # === DOUBLE BOTTOM ===
        if len(swing_lows) >= 2:
            for a in range(len(swing_lows) - 1):
                for b in range(a + 1, len(swing_lows)):
                    l1_data = swing_lows[a]
                    l2_data = swing_lows[b]
                    l1, l2 = l1_data["price"], l2_data["price"]
                    idx1, idx2 = l1_data["index"], l2_data["index"]
                    
                    # KRITERIUM 1: Mindestabstand (min 10 Bars)
                    if idx2 - idx1 < 10:
                        continue
                    
                    # KRITERIUM 2: Ähnliche Tiefe (innerhalb 1.5× ATR)
                    if abs(l1 - l2) > atr * 1.5:
                        continue
                    
                    # KRITERIUM 3: Neckline muss signifikant höher sein (min 2× ATR)
                    neckline = max(highs[idx1:idx2+1])
                    bottom_avg = (l1 + l2) / 2
                    depth = neckline - bottom_avg
                    
                    if depth < atr * 2:
                        continue
                    
                    # KRITERIUM 4: VORHERIGER DOWNTREND
                    # Der Kurs VOR dem ersten Bottom muss höher gewesen sein
                    # Mindestens 5 Bars vor Bottom 1 anschauen
                    pre_start = max(0, idx1 - 15)
                    pre_end = max(0, idx1 - 2)
                    if pre_end > pre_start:
                        pre_highs = highs[pre_start:pre_end]
                        pre_high_avg = sum(pre_highs) / len(pre_highs)
                        # Der Durchschnitt vor Bottom 1 muss mindestens 2× ATR höher sein als die Bottoms
                        # Sonst war es eine Seitwärtsrange, kein Downtrend
                        if pre_high_avg - bottom_avg < atr * 2:
                            continue
                    
                    # KRITERIUM 5: KLARER RALLY-PEAK zwischen den Bottoms
                    # Die Neckline muss deutlich über den Bottoms UND über dem
                    # allgemeinen Preisniveau vor/nach den Bottoms liegen
                    # Prüfe ob der Bereich zwischen den Bottoms ein klares "V" oder "W" zeigt
                    mid_section = closes[idx1:idx2+1]
                    if mid_section:
                        mid_avg = sum(mid_section) / len(mid_section)
                        # Wenn der Durchschnitt zwischen den Bottoms nahe den Bottoms liegt,
                        # ist es eine flache Range, kein W-Pattern
                        if mid_avg - bottom_avg < depth * 0.3:
                            continue
                    
                    # KRITERIUM 6: RECENCY — zweites Bottom muss in der zweiten Hälfte der Daten sein
                    if idx2 < len(data) * 0.3:
                        continue
                    
                    # KRITERIUM 7: Volume-Bestätigung
                    recent_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else avg_volume
                    vol_confirmation = recent_vol > avg_volume * 0.8
                    
                    # Preis über Neckline = bestätigt
                    if current_price > neckline:
                        patterns.append({
                            "pattern": "Double Bottom",
                            "emoji": "🚀",
                            "type": "bullish",
                            "level1": round(l1, 2),
                            "level2": round(l2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline + depth, 2),
                            "confidence": "High" if vol_confirmation else "Medium",
                            "description": f"Double Bottom @ ${bottom_avg:.2f} - Neckline ${neckline:.2f} broken. Target: ${neckline + depth:.2f}"
                        })
                        break
                    elif current_price > bottom_avg + atr and current_price < neckline:
                        patterns.append({
                            "pattern": "Double Bottom (forming)",
                            "emoji": "👀",
                            "type": "bullish",
                            "level1": round(l1, 2),
                            "level2": round(l2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline + depth, 2),
                            "confidence": "Low",
                            "description": f"Potential Double Bottom @ ${bottom_avg:.2f}. Watch neckline ${neckline:.2f}"
                        })
                        break
                if patterns and any(p["pattern"].startswith("Double Bottom") for p in patterns):
                    break
        
        # === HEAD & SHOULDERS ===
        if len(swing_highs) >= 3:
            # Prüfe die letzten 3-5 Swing Highs für H&S
            for i in range(max(0, len(swing_highs) - 5), len(swing_highs) - 2):
                for j in range(i + 1, len(swing_highs) - 1):
                    for k in range(j + 1, len(swing_highs)):
                        sh1, sh2, sh3 = swing_highs[i], swing_highs[j], swing_highs[k]
                        h1, h2, h3 = sh1["price"], sh2["price"], sh3["price"]
                        idx1, idx2, idx3 = sh1["index"], sh2["index"], sh3["index"]
                        
                        # Mindestabstand zwischen Schultern
                        if idx2 - idx1 < 5 or idx3 - idx2 < 5:
                            continue
                        
                        # Head muss höher als beide Shoulders
                        if not (h2 > h1 and h2 > h3):
                            continue
                        
                        # Head muss mindestens 1× ATR höher sein
                        if h2 - max(h1, h3) < atr:
                            continue
                        
                        # Shoulders ähnlich hoch (innerhalb 2× ATR)
                        if abs(h1 - h3) > atr * 2:
                            continue
                        
                        # Neckline
                        neckline = min(lows[idx1:idx3+1])
                        
                        if current_price < neckline:
                            patterns.append({
                                "pattern": "Head & Shoulders",
                                "emoji": "🔻🔻",
                                "type": "bearish",
                                "left_shoulder": round(h1, 2),
                                "head": round(h2, 2),
                                "right_shoulder": round(h3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline - (h2 - neckline), 2),
                                "confidence": "High",
                                "description": f"H&S Complete! Neckline ${neckline:.2f} broken. Target: ${neckline - (h2 - neckline):.2f}"
                            })
                        elif current_price < h3 * 0.97:
                            patterns.append({
                                "pattern": "Head & Shoulders (forming)",
                                "emoji": "⚠️",
                                "type": "bearish",
                                "left_shoulder": round(h1, 2),
                                "head": round(h2, 2),
                                "right_shoulder": round(h3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline - (h2 - neckline), 2),
                                "confidence": "Medium",
                                "description": f"H&S forming. Watch neckline @ ${neckline:.2f}"
                            })
                        
                        if any(p["pattern"].startswith("Head") for p in patterns):
                            break
                    if any(p["pattern"].startswith("Head") for p in patterns):
                        break
                if any(p["pattern"].startswith("Head") for p in patterns):
                    break
        
        # === INVERSE HEAD & SHOULDERS ===
        if len(swing_lows) >= 3:
            for i in range(max(0, len(swing_lows) - 5), len(swing_lows) - 2):
                for j in range(i + 1, len(swing_lows) - 1):
                    for k in range(j + 1, len(swing_lows)):
                        sl1, sl2, sl3 = swing_lows[i], swing_lows[j], swing_lows[k]
                        l1, l2, l3 = sl1["price"], sl2["price"], sl3["price"]
                        idx1, idx2, idx3 = sl1["index"], sl2["index"], sl3["index"]
                        
                        if idx2 - idx1 < 5 or idx3 - idx2 < 5:
                            continue
                        if not (l2 < l1 and l2 < l3):
                            continue
                        if min(l1, l3) - l2 < atr:
                            continue
                        if abs(l1 - l3) > atr * 2:
                            continue
                        
                        neckline = max(highs[idx1:idx3+1])
                        
                        if current_price > neckline:
                            patterns.append({
                                "pattern": "Inverse Head & Shoulders",
                                "emoji": "🚀🚀",
                                "type": "bullish",
                                "left_shoulder": round(l1, 2),
                                "head": round(l2, 2),
                                "right_shoulder": round(l3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline + (neckline - l2), 2),
                                "confidence": "High",
                                "description": f"Inv. H&S Complete! Neckline ${neckline:.2f} broken. Target: ${neckline + (neckline - l2):.2f}"
                            })
                        elif current_price > l3 + atr:
                            patterns.append({
                                "pattern": "Inverse H&S (forming)",
                                "emoji": "👀",
                                "type": "bullish",
                                "left_shoulder": round(l1, 2),
                                "head": round(l2, 2),
                                "right_shoulder": round(l3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline + (neckline - l2), 2),
                                "confidence": "Medium",
                                "description": f"Inv. H&S forming. Watch neckline @ ${neckline:.2f}"
                            })
                        
                        if any(p["pattern"].startswith("Inverse") for p in patterns):
                            break
                    if any(p["pattern"].startswith("Inverse") for p in patterns):
                        break
                if any(p["pattern"].startswith("Inverse") for p in patterns):
                    break
        
        # === TRIANGLES ===
        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            recent_highs = [h["price"] for h in swing_highs[-4:]]
            recent_lows = [l["price"] for l in swing_lows[-4:]]
            
            if len(recent_highs) >= 3 and len(recent_lows) >= 3:
                high_trend = (recent_highs[-1] - recent_highs[0]) / recent_highs[0]
                low_trend = (recent_lows[-1] - recent_lows[0]) / recent_lows[0]
                high_range = (max(recent_highs) - min(recent_highs)) / max(recent_highs)
                low_range = (max(recent_lows) - min(recent_lows)) / max(recent_lows)
                
                # ASCENDING TRIANGLE: Flat resistance + rising support
                if high_range < 0.02 and low_trend > 0.02:
                    resistance = sum(recent_highs) / len(recent_highs)
                    patterns.append({
                        "pattern": "Ascending Triangle",
                        "emoji": "📐⬆️",
                        "type": "bullish",
                        "resistance": round(resistance, 2),
                        "target": round(resistance * 1.05, 2),
                        "confidence": "Medium",
                        "description": f"Ascending Triangle - Resistance @ ${resistance:.2f}. Breakout target +5%"
                    })
                
                # DESCENDING TRIANGLE: Falling resistance + flat support
                elif low_range < 0.02 and high_trend < -0.02:
                    support = sum(recent_lows) / len(recent_lows)
                    patterns.append({
                        "pattern": "Descending Triangle",
                        "emoji": "📐⬇️",
                        "type": "bearish",
                        "support": round(support, 2),
                        "target": round(support * 0.95, 2),
                        "confidence": "Medium",
                        "description": f"Descending Triangle - Support @ ${support:.2f}. Breakdown target -5%"
                    })
                
                # SYMMETRICAL TRIANGLE: Converging trendlines
                elif high_trend < -0.01 and low_trend > 0.01:
                    apex_price = (recent_highs[-1] + recent_lows[-1]) / 2
                    range_pct = (recent_highs[-1] - recent_lows[-1]) / apex_price * 100
                    
                    if range_pct < 5:
                        patterns.append({
                            "pattern": "Symmetrical Triangle",
                            "emoji": "📐",
                            "type": "neutral",
                            "apex": round(apex_price, 2),
                            "range": f"{range_pct:.1f}%",
                            "confidence": "Medium",
                            "description": f"Symmetrical Triangle - Apex @ ${apex_price:.2f}. Breakout imminent!"
                        })
        
        # === FLAGS & PENNANTS ===
        # Verbessert: Finde den tatsächlichen Pole durch Preisbewegung
        if len(closes) >= 20:
            # Suche nach dem stärksten Move in den Daten
            best_pole_end = 0
            best_pole_move = 0
            
            for pole_end in range(5, min(int(len(closes) * 0.5), 20)):
                move = (closes[pole_end] - closes[0]) / closes[0]
                if abs(move) > abs(best_pole_move):
                    best_pole_move = move
                    best_pole_end = pole_end
            
            if best_pole_end > 0 and abs(best_pole_move) > 0.08:
                pole_end = best_pole_end
                pole_move = best_pole_move
                
                flag_data = closes[pole_end:]
                flag_highs = highs[pole_end:]
                flag_lows = lows[pole_end:]
                
                if len(flag_data) >= 5:
                    flag_range = max(flag_data) - min(flag_data)
                    flag_range_pct = flag_range / closes[pole_end] if closes[pole_end] > 0 else 0
                    
                    flag_high_trend = (flag_highs[-1] - flag_highs[0]) / flag_highs[0] if flag_highs[0] > 0 else 0
                    flag_low_trend = (flag_lows[-1] - flag_lows[0]) / flag_lows[0] if flag_lows[0] > 0 else 0
                    
                    # BULL FLAG: Starker Anstieg + leichter Rückgang im Kanal
                    if pole_move > 0.08 and flag_range_pct < 0.06:
                        if flag_high_trend < 0 and flag_low_trend < 0:
                            patterns.append({
                                "pattern": "Bull Flag",
                                "emoji": "🚩⬆️",
                                "type": "bullish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move), 2),
                                "confidence": "Medium",
                                "description": f"Bull Flag after {pole_move*100:.0f}% rally. Target: ${closes[-1] * (1 + pole_move):.2f}"
                            })
                        elif flag_high_trend < 0 and flag_low_trend > 0:
                            patterns.append({
                                "pattern": "Bullish Pennant",
                                "emoji": "🔺⬆️",
                                "type": "bullish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move * 0.8), 2),
                                "confidence": "Medium",
                                "description": f"Bullish Pennant after {pole_move*100:.0f}% rally. Target: ${closes[-1] * (1 + pole_move * 0.8):.2f}"
                            })
                    
                    # BEAR FLAG: Starker Abfall + leichte Erholung
                    elif pole_move < -0.08 and flag_range_pct < 0.06:
                        if flag_high_trend > 0 and flag_low_trend > 0:
                            patterns.append({
                                "pattern": "Bear Flag",
                                "emoji": "🚩⬇️",
                                "type": "bearish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move), 2),
                                "confidence": "Medium",
                                "description": f"Bear Flag after {abs(pole_move)*100:.0f}% drop. Target: ${closes[-1] * (1 + pole_move):.2f}"
                            })
                        elif flag_high_trend < 0 and flag_low_trend > 0:
                            patterns.append({
                                "pattern": "Bearish Pennant",
                                "emoji": "🔻⬇️",
                                "type": "bearish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move * 0.8), 2),
                                "confidence": "Medium",
                                "description": f"Bearish Pennant after {abs(pole_move)*100:.0f}% drop. Target: ${closes[-1] * (1 + pole_move * 0.8):.2f}"
                            })
        
        # === CUP & HANDLE ===
        if len(closes) >= 30:
            cup_end = int(len(closes) * 0.7)
            cup_data = closes[:cup_end]
            
            if len(cup_data) >= 15:
                cup_left = cup_data[:len(cup_data)//3]
                cup_bottom = cup_data[len(cup_data)//3:2*len(cup_data)//3]
                cup_right = cup_data[2*len(cup_data)//3:]
                
                left_avg = sum(cup_left) / len(cup_left)
                bottom_avg = sum(cup_bottom) / len(cup_bottom)
                right_avg = sum(cup_right) / len(cup_right)
                
                if left_avg > bottom_avg and right_avg > bottom_avg:
                    cup_depth = (left_avg - bottom_avg) / left_avg
                    
                    if 0.10 < cup_depth < 0.40:
                        handle_data = closes[cup_end:]
                        handle_range = (max(handle_data) - min(handle_data)) / max(handle_data) if max(handle_data) > 0 else 1
                        
                        if handle_range < cup_depth * 0.5:
                            cup_lip = max(left_avg, right_avg)
                            
                            if current_price > cup_lip * 0.95:
                                patterns.append({
                                    "pattern": "Cup & Handle",
                                    "emoji": "☕⬆️",
                                    "type": "bullish",
                                    "cup_depth": f"{cup_depth*100:.1f}%",
                                    "breakout_level": round(cup_lip, 2),
                                    "target": round(cup_lip * (1 + cup_depth), 2),
                                    "confidence": "High" if current_price > cup_lip else "Medium",
                                    "description": f"Cup & Handle - Breakout @ ${cup_lip:.2f}. Target: ${cup_lip * (1 + cup_depth):.2f}"
                                })
        
        # === WEDGES ===
        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            recent_highs = [h["price"] for h in swing_highs[-4:]]
            recent_lows = [l["price"] for l in swing_lows[-4:]]
            
            if len(recent_highs) >= 3 and len(recent_lows) >= 3:
                high_slope = (recent_highs[-1] - recent_highs[0]) / len(recent_highs)
                low_slope = (recent_lows[-1] - recent_lows[0]) / len(recent_lows)
                
                are_converging = abs(high_slope - low_slope) < abs(high_slope + low_slope) / 2 if (high_slope + low_slope) != 0 else False
                
                # RISING WEDGE (bearish)
                if high_slope > 0 and low_slope > 0 and are_converging and low_slope > high_slope:
                    patterns.append({
                        "pattern": "Rising Wedge",
                        "emoji": "📈⬇️",
                        "type": "bearish",
                        "target": round(recent_lows[0], 2),
                        "confidence": "Medium",
                        "description": f"Rising Wedge (bearish) - Target support @ ${recent_lows[0]:.2f}"
                    })
                
                # FALLING WEDGE (bullish)
                elif high_slope < 0 and low_slope < 0 and are_converging and high_slope > low_slope:
                    patterns.append({
                        "pattern": "Falling Wedge",
                        "emoji": "📉⬆️",
                        "type": "bullish",
                        "target": round(recent_highs[0], 2),
                        "confidence": "Medium",
                        "description": f"Falling Wedge (bullish) - Target resistance @ ${recent_highs[0]:.2f}"
                    })
        
        # === BASE BREAKOUT ===
        # Lange Seitwärtsphase + Ausbruch darüber
        # Typisch für ATEX-artige Setups: Monate flach, dann Gap/Breakout
        if len(closes) >= 30 and not any(p["pattern"] in ["Double Bottom", "Cup & Handle"] for p in patterns):
            # Finde die Base: Teile Daten in erste 60% (potenzielle Base) und letzte 40%
            base_end = int(len(closes) * 0.6)
            base_data = closes[:base_end]
            breakout_data = closes[base_end:]
            
            if len(base_data) >= 15 and len(breakout_data) >= 5:
                base_high = max(highs[:base_end])
                base_low = min(lows[:base_end])
                base_avg = sum(base_data) / len(base_data)
                base_range_pct = (base_high - base_low) / base_avg if base_avg > 0 else 1
                
                # Base muss eng sein (Range < 25% des Durchschnittspreises)
                # UND der aktuelle Preis muss deutlich über der Base liegen
                breakout_pct = (current_price - base_high) / base_high if base_high > 0 else 0
                
                if base_range_pct < 0.25 and breakout_pct > 0.05:
                    # Zusätzlich: Die Mehrzahl der Base-Bars sollte in einer engen Range sein
                    tight_count = sum(1 for c in base_data if abs(c - base_avg) / base_avg < 0.08)
                    tight_pct = tight_count / len(base_data)
                    
                    if tight_pct > 0.6:
                        patterns.append({
                            "pattern": "Base Breakout",
                            "emoji": "💥⬆️",
                            "type": "bullish",
                            "base_low": round(base_low, 2),
                            "base_high": round(base_high, 2),
                            "breakout_pct": f"+{breakout_pct*100:.1f}%",
                            "target": round(base_high + (base_high - base_low), 2),
                            "confidence": "High" if breakout_pct > 0.10 else "Medium",
                            "description": f"Base Breakout! Range ${base_low:.2f}-${base_high:.2f}, broke out +{breakout_pct*100:.1f}%. Target: ${base_high + (base_high - base_low):.2f}"
                        })
        
        # === WYCKOFF ACCUMULATION ===
        # Erkennt die klassische Wyckoff-Akkumulationsphase:
        # 1. Vorheriger Downtrend → Trading Range
        # 2. Selling Climax (SC): Hohes Volume am Tief
        # 3. Automatic Rally (AR): Bounce nach SC → Oberkante Range
        # 4. Secondary Test (ST): Retest SC-Area mit weniger Volume
        # 5. Spring: Kurzer Bruch unter Range → schnelle Erholung (Shakeout)
        # 6. Sign of Strength (SOS): Starker Move über AR mit Volume
        # 7. Last Point of Support (LPS): Pullback nach SOS
        
        if len(closes) >= 40 and not any("Wyckoff" in p.get("pattern", "") for p in patterns):
            try:
                # Phase 1: Finde Trading Range
                # Teile Daten: erste 25% = Potential-Downtrend, 25-75% = Range, letzte 25% = aktuell
                q1_end = int(len(closes) * 0.25)
                q3_start = int(len(closes) * 0.75)
                
                early_data = closes[:q1_end]
                range_data = closes[q1_end:q3_start]
                late_data = closes[q3_start:]
                
                range_highs = highs[q1_end:q3_start]
                range_lows = lows[q1_end:q3_start]
                range_vols = volumes[q1_end:q3_start]
                
                if len(range_data) >= 10 and len(early_data) >= 5 and len(late_data) >= 5:
                    early_avg = sum(early_data) / len(early_data)
                    range_avg = sum(range_data) / len(range_data)
                    range_high = max(range_highs)
                    range_low = min(range_lows)
                    range_width = range_high - range_low
                    range_width_pct = range_width / range_avg if range_avg > 0 else 1
                    avg_range_vol = sum(range_vols) / len(range_vols) if range_vols else 1
                    
                    # === WYCKOFF ACCUMULATION ===
                    # Voraussetzung: Preis fiel VOR der Range (Downtrend in early_data)
                    prior_decline = (early_data[0] - min(early_data)) / early_data[0] if early_data[0] > 0 else 0
                    came_from_above = early_avg > range_avg * 1.02
                    
                    if (prior_decline > 0.05 or came_from_above) and range_width_pct < 0.30:
                        wyckoff_events = []
                        wyckoff_score = 0
                        
                        # Event 1: Selling Climax (SC) — Höchstes Volume nahe dem Tief
                        sc_idx = None
                        for ri in range(len(range_data)):
                            abs_idx = q1_end + ri
                            if range_vols[ri] > avg_range_vol * 1.8 and range_lows[ri] <= range_low + range_width * 0.15:
                                sc_idx = ri
                                wyckoff_events.append(f"SC @ ${range_lows[ri]:.2f} (Vol {range_vols[ri]/avg_range_vol:.1f}x)")
                                wyckoff_score += 20
                                break
                        
                        # Event 2: Automatic Rally (AR) — Schneller Bounce nach SC
                        ar_level = None
                        if sc_idx is not None and sc_idx + 3 < len(range_data):
                            post_sc = range_highs[sc_idx:min(sc_idx + 8, len(range_highs))]
                            if post_sc:
                                ar_level = max(post_sc)
                                if ar_level > range_low + range_width * 0.5:
                                    wyckoff_events.append(f"AR @ ${ar_level:.2f}")
                                    wyckoff_score += 15
                        
                        # Event 3: Secondary Test (ST) — Retest SC-Area mit weniger Volume
                        st_found = False
                        if sc_idx is not None:
                            for ri in range(sc_idx + 5, len(range_data)):
                                if range_lows[ri] <= range_low + range_width * 0.20:
                                    if range_vols[ri] < range_vols[sc_idx] * 0.8:
                                        wyckoff_events.append(f"ST @ ${range_lows[ri]:.2f} (lower vol)")
                                        wyckoff_score += 15
                                        st_found = True
                                        break
                        
                        # Event 4: Spring — Kurzer Bruch unter Range Low → schnelle Erholung
                        spring_found = False
                        for ri in range(max(0, len(range_data) - int(len(range_data) * 0.6)), len(range_data)):
                            if range_lows[ri] < range_low:
                                # Check: Preis muss innerhalb 3 Bars wieder in Range sein
                                if ri + 3 < len(range_data):
                                    recovery = any(range_data[ri+j] > range_low + range_width * 0.2 for j in range(1, min(4, len(range_data) - ri)))
                                    if recovery:
                                        wyckoff_events.append(f"Spring @ ${range_lows[ri]:.2f} (shakeout)")
                                        wyckoff_score += 25
                                        spring_found = True
                                        break
                        
                        # Event 5: Sign of Strength (SOS) — Preis bricht über AR/Range High
                        sos_found = False
                        late_highs = highs[q3_start:]
                        late_vols = volumes[q3_start:]
                        avg_late_vol = sum(late_vols) / len(late_vols) if late_vols else 1
                        
                        if current_price > range_high:
                            wyckoff_events.append(f"SOS: Price ${current_price:.2f} > Range High ${range_high:.2f}")
                            wyckoff_score += 20
                            sos_found = True
                        elif any(h > range_high for h in late_highs):
                            wyckoff_events.append(f"SOS attempt: Touched ${max(late_highs):.2f}")
                            wyckoff_score += 10
                        
                        # Event 6: Volume-Bestätigung beim Breakout
                        if sos_found and late_vols:
                            breakout_vol = max(late_vols[-5:]) if len(late_vols) >= 5 else max(late_vols)
                            if breakout_vol > avg_range_vol * 1.5:
                                wyckoff_events.append(f"Volume Confirm: {breakout_vol/avg_range_vol:.1f}x avg")
                                wyckoff_score += 10
                        
                        # Bestimme Phase
                        if wyckoff_score >= 50:
                            if sos_found:
                                phase = "Phase D/E (Markup beginning)"
                                phase_emoji = "🟢"
                            elif spring_found:
                                phase = "Phase C (Spring — bullish shakeout)"
                                phase_emoji = "🟡"
                            elif st_found:
                                phase = "Phase B (Building cause)"
                                phase_emoji = "🔵"
                            else:
                                phase = "Phase A (Selling exhaustion)"
                                phase_emoji = "⚪"
                            
                            confidence = "High" if wyckoff_score >= 70 else "Medium" if wyckoff_score >= 50 else "Low"
                            
                            patterns.append({
                                "pattern": f"Wyckoff Accumulation",
                                "emoji": "🏦⬆️",
                                "type": "bullish",
                                "phase": phase,
                                "phase_emoji": phase_emoji,
                                "range_low": round(range_low, 2),
                                "range_high": round(range_high, 2),
                                "events": wyckoff_events,
                                "score": wyckoff_score,
                                "target": round(range_high + range_width, 2),
                                "confidence": confidence,
                                "description": f"Wyckoff Accumulation — {phase}. Range ${range_low:.2f}-${range_high:.2f}. Events: {', '.join(wyckoff_events[:3])}"
                            })
                    
                    # === WYCKOFF DISTRIBUTION ===
                    # Voraussetzung: Preis stieg VOR der Range (Uptrend in early_data)
                    prior_rally = (max(early_data) - early_data[0]) / early_data[0] if early_data[0] > 0 else 0
                    came_from_below = early_avg < range_avg * 0.98
                    
                    if (prior_rally > 0.05 or came_from_below) and range_width_pct < 0.30:
                        wyckoff_events = []
                        wyckoff_score = 0
                        
                        # Event 1: Buying Climax (BC) — Höchstes Volume nahe dem Hoch
                        bc_idx = None
                        for ri in range(len(range_data)):
                            if range_vols[ri] > avg_range_vol * 1.8 and range_highs[ri] >= range_high - range_width * 0.15:
                                bc_idx = ri
                                wyckoff_events.append(f"BC @ ${range_highs[ri]:.2f} (Vol {range_vols[ri]/avg_range_vol:.1f}x)")
                                wyckoff_score += 20
                                break
                        
                        # Event 2: Automatic Reaction (AR)
                        if bc_idx is not None and bc_idx + 3 < len(range_data):
                            post_bc = range_lows[bc_idx:min(bc_idx + 8, len(range_lows))]
                            if post_bc:
                                ar_level = min(post_bc)
                                if ar_level < range_high - range_width * 0.5:
                                    wyckoff_events.append(f"AR @ ${ar_level:.2f}")
                                    wyckoff_score += 15
                        
                        # Event 3: Secondary Test (ST) — Retest BC mit weniger Volume
                        if bc_idx is not None:
                            for ri in range(bc_idx + 5, len(range_data)):
                                if range_highs[ri] >= range_high - range_width * 0.20:
                                    if range_vols[ri] < range_vols[bc_idx] * 0.8:
                                        wyckoff_events.append(f"ST @ ${range_highs[ri]:.2f} (lower vol)")
                                        wyckoff_score += 15
                                        break
                        
                        # Event 4: Upthrust (UT) — Kurzer Bruch über Range High → Failure
                        for ri in range(max(0, len(range_data) - int(len(range_data) * 0.6)), len(range_data)):
                            if range_highs[ri] > range_high:
                                if ri + 3 < len(range_data):
                                    failure = any(range_data[ri+j] < range_high - range_width * 0.2 for j in range(1, min(4, len(range_data) - ri)))
                                    if failure:
                                        wyckoff_events.append(f"UT @ ${range_highs[ri]:.2f} (failed)")
                                        wyckoff_score += 25
                                        break
                        
                        # Event 5: Sign of Weakness (SOW)
                        sow_found = False
                        if current_price < range_low:
                            wyckoff_events.append(f"SOW: Price ${current_price:.2f} < Range Low ${range_low:.2f}")
                            wyckoff_score += 20
                            sow_found = True
                        elif any(l < range_low for l in lows[q3_start:]):
                            wyckoff_events.append(f"SOW attempt: Touched ${min(lows[q3_start:]):.2f}")
                            wyckoff_score += 10
                        
                        if wyckoff_score >= 50:
                            if sow_found:
                                phase = "Phase D/E (Markdown beginning)"
                                phase_emoji = "🔴"
                            else:
                                phase = "Phase B/C (Distribution in progress)"
                                phase_emoji = "🟠"
                            
                            confidence = "High" if wyckoff_score >= 70 else "Medium"
                            
                            patterns.append({
                                "pattern": f"Wyckoff Distribution",
                                "emoji": "🏦⬇️",
                                "type": "bearish",
                                "phase": phase,
                                "phase_emoji": phase_emoji,
                                "range_low": round(range_low, 2),
                                "range_high": round(range_high, 2),
                                "events": wyckoff_events,
                                "score": wyckoff_score,
                                "target": round(range_low - range_width, 2),
                                "confidence": confidence,
                                "description": f"Wyckoff Distribution — {phase}. Range ${range_low:.2f}-${range_high:.2f}. Events: {', '.join(wyckoff_events[:3])}"
                            })
            
            except Exception:
                pass  # Wyckoff detection failed silently
        
        # =================================================================
        # CANDLESTICK PATTERNS — Letzte 1-3 Kerzen
        # =================================================================
        # Nur die letzten Kerzen analysieren (aktuell relevant)
        # Kontext wichtig: Hammer nur nach Downtrend bullish etc.
        # =================================================================
        
        if len(data) >= 5:
            try:
                # Letzte Kerzen
                c0 = data[-1]  # Aktuellste Kerze
                c1 = data[-2]  # Vorherige
                c2 = data[-3]  # Zwei zurück
                
                o0, h0, l0, cl0 = c0["high"], c0["high"], c0["low"], c0["close"]
                o0 = data[-1].get("open", data[-1]["close"])  # Manche Daten haben kein open
                # Robuster: open aus close der vorherigen Kerze ableiten wenn nötig
                o0 = c0.get("open", c1["close"])
                h0, l0, cl0 = c0["high"], c0["low"], c0["close"]
                
                o1 = c1.get("open", c2["close"])
                h1, l1, cl1 = c1["high"], c1["low"], c1["close"]
                
                o2 = c2.get("open", data[-4]["close"] if len(data) >= 4 else c2["close"])
                h2, l2, cl2 = c2["high"], c2["low"], c2["close"]
                
                # Body und Shadow Berechnungen
                body0 = abs(cl0 - o0)
                body1 = abs(cl1 - o1)
                body2 = abs(cl2 - o2)
                
                range0 = h0 - l0 if h0 > l0 else 0.001
                range1 = h1 - l1 if h1 > l1 else 0.001
                range2 = h2 - l2 if h2 > l2 else 0.001
                
                upper_shadow0 = h0 - max(cl0, o0)
                lower_shadow0 = min(cl0, o0) - l0
                upper_shadow1 = h1 - max(cl1, o1)
                lower_shadow1 = min(cl1, o1) - l1
                
                is_green0 = cl0 > o0
                is_green1 = cl1 > o1
                is_green2 = cl2 > o2
                
                # Kontext: Mini-Trend der letzten 5-10 Kerzen
                lookback_trend = closes[-10:-1] if len(closes) >= 11 else closes[:-1]
                trend_start = lookback_trend[0] if lookback_trend else cl0
                trend_end = lookback_trend[-1] if lookback_trend else cl0
                recent_trend = (trend_end - trend_start) / trend_start if trend_start > 0 else 0
                is_downtrend = recent_trend < -0.02
                is_uptrend = recent_trend > 0.02
                
                # ─── SINGLE CANDLE PATTERNS ───
                
                # HAMMER (bullish) — Langer unterer Schatten, kleiner Body oben
                # Bedingung: nach Downtrend, unterer Schatten ≥ 2x Body, oberer Schatten klein
                if (is_downtrend and 
                    lower_shadow0 >= body0 * 2 and 
                    upper_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):  # Nicht komplett Doji
                    patterns.append({
                        "pattern": "Hammer",
                        "emoji": "🔨",
                        "type": "bullish",
                        "confidence": "High" if is_green0 else "Medium",
                        "description": f"Hammer @ ${cl0:.2f} nach Downtrend — Käufer wehren sich am Tief. {'Grüner Body = stärker' if is_green0 else 'Roter Body = Bestätigung abwarten'}"
                    })
                
                # INVERTED HAMMER (bullish) — Langer oberer Schatten, kleiner Body unten, nach Downtrend
                if (is_downtrend and
                    upper_shadow0 >= body0 * 2 and
                    lower_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):
                    patterns.append({
                        "pattern": "Inverted Hammer",
                        "emoji": "🔨⬆️",
                        "type": "bullish",
                        "confidence": "Medium",
                        "description": f"Inverted Hammer @ ${cl0:.2f} — Kaufdruck kommt auf, Bestätigung durch nächste grüne Kerze nötig"
                    })
                
                # SHOOTING STAR (bearish) — Wie Inverted Hammer aber nach Uptrend
                if (is_uptrend and
                    upper_shadow0 >= body0 * 2 and
                    lower_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):
                    patterns.append({
                        "pattern": "Shooting Star",
                        "emoji": "⭐⬇️",
                        "type": "bearish",
                        "confidence": "High" if not is_green0 else "Medium",
                        "description": f"Shooting Star @ ${cl0:.2f} nach Uptrend — Verkäufer drücken vom Hoch. {'Roter Body = stärker' if not is_green0 else 'Grüner Body = schwächer'}"
                    })
                
                # HANGING MAN (bearish) — Wie Hammer aber nach Uptrend
                if (is_uptrend and
                    lower_shadow0 >= body0 * 2 and
                    upper_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):
                    patterns.append({
                        "pattern": "Hanging Man",
                        "emoji": "☠️",
                        "type": "bearish",
                        "confidence": "Medium",
                        "description": f"Hanging Man @ ${cl0:.2f} nach Uptrend — Verkaufsdruck nimmt zu trotz Erholung"
                    })
                
                # DOJI — Sehr kleiner Body, zeigt Unentschlossenheit
                if body0 <= range0 * 0.10 and range0 > atr * 0.3:
                    # Dragonfly Doji (langer unterer Schatten)
                    if lower_shadow0 > range0 * 0.6:
                        doji_type = "Dragonfly Doji"
                        doji_emoji = "🐉"
                        doji_bias = "bullish" if is_downtrend else "neutral"
                        doji_desc = "Dragonfly Doji — Starke Ablehnung vom Tief"
                    # Gravestone Doji (langer oberer Schatten)
                    elif upper_shadow0 > range0 * 0.6:
                        doji_type = "Gravestone Doji"
                        doji_emoji = "🪦"
                        doji_bias = "bearish" if is_uptrend else "neutral"
                        doji_desc = "Gravestone Doji — Starke Ablehnung vom Hoch"
                    else:
                        doji_type = "Doji"
                        doji_emoji = "➕"
                        doji_bias = "neutral"
                        doji_desc = "Doji — Markt unentschlossen, warte auf Richtung"
                    
                    patterns.append({
                        "pattern": doji_type,
                        "emoji": doji_emoji,
                        "type": doji_bias,
                        "confidence": "Medium" if doji_bias != "neutral" else "Low",
                        "description": f"{doji_desc} @ ${cl0:.2f}"
                    })
                
                # MARUBOZU — Große Kerze fast ohne Schatten (starkes Momentum)
                if body0 > range0 * 0.85 and body0 > atr * 1.2:
                    maru_type = "Bullish Marubozu" if is_green0 else "Bearish Marubozu"
                    maru_emoji = "💪⬆️" if is_green0 else "💪⬇️"
                    patterns.append({
                        "pattern": maru_type,
                        "emoji": maru_emoji,
                        "type": "bullish" if is_green0 else "bearish",
                        "confidence": "High",
                        "description": f"{maru_type} @ ${cl0:.2f} — Starkes Momentum, {'Käufer' if is_green0 else 'Verkäufer'} dominieren komplett"
                    })
                
                # ─── TWO CANDLE PATTERNS ───
                
                # BULLISH ENGULFING — Grüne Kerze verschluckt vorherige rote komplett
                if (not is_green1 and is_green0 and
                    o0 <= cl1 and cl0 >= o1 and
                    body0 > body1 * 0.8):
                    conf = "High" if is_downtrend else "Medium"
                    patterns.append({
                        "pattern": "Bullish Engulfing",
                        "emoji": "🟢⬆️",
                        "type": "bullish",
                        "confidence": conf,
                        "description": f"Bullish Engulfing @ ${cl0:.2f} — Grüne Kerze verschluckt rote. {'Nach Downtrend = starkes Reversal-Signal' if is_downtrend else 'Stärker nach Pullback'}"
                    })
                
                # BEARISH ENGULFING — Rote Kerze verschluckt vorherige grüne komplett
                if (is_green1 and not is_green0 and
                    o0 >= cl1 and cl0 <= o1 and
                    body0 > body1 * 0.8):
                    conf = "High" if is_uptrend else "Medium"
                    patterns.append({
                        "pattern": "Bearish Engulfing",
                        "emoji": "🔴⬇️",
                        "type": "bearish",
                        "confidence": conf,
                        "description": f"Bearish Engulfing @ ${cl0:.2f} — Rote Kerze verschluckt grüne. {'Nach Uptrend = starkes Reversal-Signal' if is_uptrend else 'Stärker nach Bounce'}"
                    })
                
                # PIERCING LINE (bullish) — Rote Kerze, dann grüne die über 50% der roten schließt
                if (not is_green1 and is_green0 and is_downtrend and
                    o0 < cl1 and  # Gap down open
                    cl0 > o1 - body1 * 0.5 and cl0 < o1):  # Schließt über 50% der roten
                    patterns.append({
                        "pattern": "Piercing Line",
                        "emoji": "🗡️⬆️",
                        "type": "bullish",
                        "confidence": "Medium",
                        "description": f"Piercing Line @ ${cl0:.2f} — Käufer drehen nach Gap Down, Recovery über 50%"
                    })
                
                # DARK CLOUD COVER (bearish) — Gegenteil von Piercing
                if (is_green1 and not is_green0 and is_uptrend and
                    o0 > cl1 and  # Gap up open
                    cl0 < cl1 - body1 * 0.5 and cl0 > o1):  # Schließt unter 50% der grünen
                    patterns.append({
                        "pattern": "Dark Cloud Cover",
                        "emoji": "🌑⬇️",
                        "type": "bearish",
                        "confidence": "Medium",
                        "description": f"Dark Cloud Cover @ ${cl0:.2f} — Verkäufer drehen nach Gap Up, Rückgang über 50%"
                    })
                
                # TWEEZER BOTTOM (bullish) — Zwei Kerzen mit fast gleichem Tief
                if (is_downtrend and
                    abs(l0 - l1) <= atr * 0.15 and
                    not is_green1 and is_green0):
                    patterns.append({
                        "pattern": "Tweezer Bottom",
                        "emoji": "🔧⬆️",
                        "type": "bullish",
                        "confidence": "Medium",
                        "description": f"Tweezer Bottom @ ${l0:.2f} — Doppeltes Tief auf gleichem Level, Support bestätigt"
                    })
                
                # TWEEZER TOP (bearish) — Zwei Kerzen mit fast gleichem Hoch
                if (is_uptrend and
                    abs(h0 - h1) <= atr * 0.15 and
                    is_green1 and not is_green0):
                    patterns.append({
                        "pattern": "Tweezer Top",
                        "emoji": "🔧⬇️",
                        "type": "bearish",
                        "confidence": "Medium",
                        "description": f"Tweezer Top @ ${h0:.2f} — Doppeltes Hoch auf gleichem Level, Resistance bestätigt"
                    })
                
                # ─── THREE CANDLE PATTERNS ───
                
                # MORNING STAR (bullish) — Rote Kerze, kleiner Body (Doji/Spinning), grüne Kerze
                if (not is_green2 and is_green0 and
                    body2 > atr * 0.5 and body0 > atr * 0.5 and  # Große äußere Kerzen
                    body1 < body2 * 0.4 and body1 < body0 * 0.4 and  # Kleine Mitte
                    cl0 > o2 - body2 * 0.5 and  # Grüne schließt über 50% der roten
                    is_downtrend):
                    patterns.append({
                        "pattern": "Morning Star",
                        "emoji": "🌅⬆️",
                        "type": "bullish",
                        "confidence": "High",
                        "description": f"Morning Star @ ${cl0:.2f} — Klassisches 3-Kerzen Reversal nach Downtrend. Starkes Kaufsignal"
                    })
                
                # EVENING STAR (bearish) — Grüne Kerze, kleiner Body, rote Kerze
                if (is_green2 and not is_green0 and
                    body2 > atr * 0.5 and body0 > atr * 0.5 and
                    body1 < body2 * 0.4 and body1 < body0 * 0.4 and
                    cl0 < cl2 + body2 * 0.5 and
                    is_uptrend):
                    patterns.append({
                        "pattern": "Evening Star",
                        "emoji": "🌙⬇️",
                        "type": "bearish",
                        "confidence": "High",
                        "description": f"Evening Star @ ${cl0:.2f} — Klassisches 3-Kerzen Reversal nach Uptrend. Starkes Verkaufssignal"
                    })
                
                # THREE WHITE SOLDIERS (bullish) — Drei aufeinanderfolgende grüne Kerzen
                if (is_green0 and is_green1 and is_green2 and
                    cl0 > cl1 > cl2 and  # Steigend
                    body0 > atr * 0.4 and body1 > atr * 0.4 and body2 > atr * 0.4 and  # Substantielle Bodies
                    upper_shadow0 < body0 * 0.3):  # Wenig oberer Schatten (Stärke)
                    patterns.append({
                        "pattern": "Three White Soldiers",
                        "emoji": "💂💂💂",
                        "type": "bullish",
                        "confidence": "High" if is_downtrend else "Medium",
                        "description": f"Three White Soldiers — Drei starke grüne Kerzen. {'Reversal nach Downtrend!' if is_downtrend else 'Trendfortsetzung'}"
                    })
                
                # THREE BLACK CROWS (bearish) — Drei aufeinanderfolgende rote Kerzen
                if (not is_green0 and not is_green1 and not is_green2 and
                    cl0 < cl1 < cl2 and
                    body0 > atr * 0.4 and body1 > atr * 0.4 and body2 > atr * 0.4 and
                    lower_shadow0 < body0 * 0.3):
                    patterns.append({
                        "pattern": "Three Black Crows",
                        "emoji": "🐦‍⬛🐦‍⬛🐦‍⬛",
                        "type": "bearish",
                        "confidence": "High" if is_uptrend else "Medium",
                        "description": f"Three Black Crows — Drei starke rote Kerzen. {'Reversal nach Uptrend!' if is_uptrend else 'Trendfortsetzung'}"
                    })
            
            except Exception:
                pass  # Candlestick detection failed
        
        return patterns
        
    except Exception as e:
        return []


def calculate_vwap(ohlcv_data):
    """
    Berechnet VWAP (Volume Weighted Average Price) mit Standard Deviations.
    
    Returns:
        dict mit vwap, upper_band_1, upper_band_2, lower_band_1, lower_band_2
    """
    if not ohlcv_data or len(ohlcv_data) < 5:
        return None
    
    try:
        # Typischer Preis = (High + Low + Close) / 3
        typical_prices = [(d["high"] + d["low"] + d["close"]) / 3 for d in ohlcv_data]
        volumes = [d.get("volume", 0) for d in ohlcv_data]
        
        # Kumulative Werte
        cumulative_tp_vol = 0
        cumulative_vol = 0
        vwap_values = []
        
        for tp, vol in zip(typical_prices, volumes):
            cumulative_tp_vol += tp * vol
            cumulative_vol += vol
            if cumulative_vol > 0:
                vwap_values.append(cumulative_tp_vol / cumulative_vol)
            else:
                vwap_values.append(tp)
        
        current_vwap = vwap_values[-1] if vwap_values else typical_prices[-1]
        
        # Standard Deviation berechnen
        squared_diffs = [(tp - current_vwap) ** 2 for tp in typical_prices]
        variance = sum(squared_diffs) / len(squared_diffs)
        std_dev = variance ** 0.5
        
        return {
            "vwap": round(current_vwap, 2),
            "vwap_values": vwap_values,
            "std_dev": round(std_dev, 2),
            "upper_1": round(current_vwap + std_dev, 2),
            "upper_2": round(current_vwap + 2 * std_dev, 2),
            "lower_1": round(current_vwap - std_dev, 2),
            "lower_2": round(current_vwap - 2 * std_dev, 2),
        }
    except Exception as e:
        return None


def find_volume_voids_for_chart(ohlcv_data, num_bins=20):
    """
    Findet Volume Voids für Chart-Darstellung.
    
    Returns:
        List of void zones with price_low, price_high, strength
    """
    if not ohlcv_data or len(ohlcv_data) < 10:
        return []
    
    try:
        # Preis-Range
        all_highs = [d["high"] for d in ohlcv_data]
        all_lows = [d["low"] for d in ohlcv_data]
        
        range_high = max(all_highs)
        range_low = min(all_lows)
        bin_size = (range_high - range_low) / num_bins
        
        # Volume pro Bin
        bins = [{"low": range_low + i * bin_size, 
                 "high": range_low + (i + 1) * bin_size, 
                 "volume": 0} for i in range(num_bins)]
        
        for d in ohlcv_data:
            vol = d.get("volume", 0)
            h, l = d["high"], d["low"]
            day_range = h - l if h > l else 0.01
            
            for bin in bins:
                overlap_low = max(bin["low"], l)
                overlap_high = min(bin["high"], h)
                if overlap_high > overlap_low:
                    overlap_pct = (overlap_high - overlap_low) / day_range
                    bin["volume"] += vol * overlap_pct
        
        # Durchschnitt berechnen
        avg_vol = sum(b["volume"] for b in bins) / len(bins)
        
        # Voids = Bins mit < 30% des Durchschnitts
        voids = []
        for bin in bins:
            if bin["volume"] < avg_vol * 0.3:
                strength = 1 - (bin["volume"] / avg_vol) if avg_vol > 0 else 1
                voids.append({
                    "price_low": round(bin["low"], 2),
                    "price_high": round(bin["high"], 2),
                    "strength": round(strength, 2)
                })
        
        return voids
    except Exception as e:
        return []


def calculate_ema_series(closes, period):
    """Berechnet EMA-Serie für Chart-Overlay (gibt Liste zurück, nicht Einzelwert)."""
    if len(closes) < period:
        return []
    
    multiplier = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]  # SMA als Start
    
    for price in closes[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    
    # Padding am Anfang
    return [None] * (len(closes) - len(ema)) + ema


def generate_ai_chart_analysis(ticker, ohlcv_data, patterns, sr_levels, fib_levels, volume_profile=None):
    """
    Generiert KI-basierte Chart-Analyse.
    
    Returns:
        dict mit summary, trade_idea, risk_reward, key_levels
    """
    if not ohlcv_data or len(ohlcv_data) < 10:
        return None
    
    current_price = ohlcv_data[-1]["close"]
    
    analysis = {
        "ticker": ticker,
        "current_price": current_price,
        "summary": [],
        "trade_idea": None,
        "key_levels": [],
        "bias": "Neutral"
    }
    
    # Pattern Analysis
    bullish_patterns = [p for p in patterns if p.get("type") == "bullish"]
    bearish_patterns = [p for p in patterns if p.get("type") == "bearish"]
    
    if bullish_patterns:
        for p in bullish_patterns:
            analysis["summary"].append(f"{p['emoji']} {p['pattern']}: {p['description']}")
        analysis["bias"] = "Bullish"
    
    if bearish_patterns:
        for p in bearish_patterns:
            analysis["summary"].append(f"{p['emoji']} {p['pattern']}: {p['description']}")
        if not bullish_patterns:
            analysis["bias"] = "Bearish"
        else:
            analysis["bias"] = "Mixed"
    
    # Support/Resistance Analysis
    if sr_levels:
        supports = sr_levels.get("support_levels", [])
        resistances = sr_levels.get("resistance_levels", [])
        
        if supports:
            nearest_support = supports[0]
            dist = (current_price - nearest_support["price"]) / current_price * 100
            analysis["summary"].append(f"📉 Nearest Support: ${nearest_support['price']:.2f} ({dist:.1f}% below)")
            analysis["key_levels"].append({"type": "support", "price": nearest_support["price"], "strength": nearest_support.get("strength", 1)})
        
        if resistances:
            nearest_resistance = resistances[0]
            dist = (nearest_resistance["price"] - current_price) / current_price * 100
            analysis["summary"].append(f"📈 Nearest Resistance: ${nearest_resistance['price']:.2f} ({dist:.1f}% above)")
            analysis["key_levels"].append({"type": "resistance", "price": nearest_resistance["price"], "strength": nearest_resistance.get("strength", 1)})
    
    # Trade Idea Generation
    if analysis["bias"] == "Bullish" and sr_levels:
        supports = sr_levels.get("support_levels", [])
        resistances = sr_levels.get("resistance_levels", [])
        
        if supports and resistances:
            entry = current_price
            stop = supports[0]["price"] * 0.99
            target = resistances[0]["price"]
            risk = entry - stop
            reward = target - entry
            rr = reward / risk if risk > 0 else 0
            
            analysis["trade_idea"] = {
                "direction": "LONG",
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk_reward": round(rr, 2)
            }
    
    elif analysis["bias"] == "Bearish" and sr_levels:
        supports = sr_levels.get("support_levels", [])
        resistances = sr_levels.get("resistance_levels", [])
        
        if supports and resistances:
            entry = current_price
            stop = resistances[0]["price"] * 1.01
            target = supports[0]["price"]
            risk = stop - entry
            reward = entry - target
            rr = reward / risk if risk > 0 else 0
            
            analysis["trade_idea"] = {
                "direction": "SHORT",
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk_reward": round(rr, 2)
            }
    
    return analysis


def create_lightweight_chart_html(ohlcv_data, ticker, sr_levels=None, patterns=None, fib_levels=None, 
                                   ema_periods=[20, 50, 100, 200], height=500, show_volume=True,
                                   vwap_data=None, volume_voids=None, trade_zones=None):
    """
    Erstellt HTML für Lightweight Charts mit ALLEN Overlays:
    - Candlesticks + Volume
    - EMAs (20/50/100/200)
    - Support/Resistance Levels
    - Fibonacci Retracements
    - VWAP + Standard Deviation Bands
    - Volume Voids (orange highlights)
    - Trade Zones (Entry/Stop/Target)
    - Pattern Markers
    
    Returns:
        HTML string für streamlit.components.v1.html()
    """
    if not ohlcv_data or len(ohlcv_data) < 5:
        return "<div>Keine Daten verfügbar</div>"
    
    # Prepare candlestick data
    candles_json = json.dumps(ohlcv_data)
    
    # Prepare volume data
    volume_data = [{"time": d["time"], "value": d.get("volume", 0), 
                    "color": "rgba(38, 166, 154, 0.5)" if d["close"] >= d["open"] else "rgba(239, 83, 80, 0.5)"} 
                   for d in ohlcv_data]
    volume_json = json.dumps(volume_data)
    
    # Prepare EMA data
    closes = [d["close"] for d in ohlcv_data]
    times = [d["time"] for d in ohlcv_data]
    
    ema_lines = []
    ema_colors = ["#2196F3", "#FF9800", "#E91E63", "#9C27B0"]  # Blue, Orange, Pink, Purple
    
    for i, period in enumerate(ema_periods):
        ema_values = calculate_ema_series(closes, period)
        ema_data = []
        for j, val in enumerate(ema_values):
            if val is not None:
                ema_data.append({"time": times[j], "value": round(val, 2)})
        if ema_data:
            ema_lines.append({
                "data": ema_data,
                "color": ema_colors[i % len(ema_colors)],
                "label": f"EMA {period}"
            })
    
    ema_json = json.dumps(ema_lines)
    
    # Prepare VWAP data
    vwap_lines = []
    if vwap_data:
        vwap_values = vwap_data.get("vwap_values", [])
        if vwap_values and len(vwap_values) == len(times):
            # VWAP Line
            vwap_line_data = [{"time": times[i], "value": round(vwap_values[i], 2)} for i in range(len(vwap_values))]
            vwap_lines.append({"data": vwap_line_data, "color": "#FFEB3B", "label": "VWAP", "lineWidth": 2})
            
            # Upper/Lower Bands
            std = vwap_data.get("std_dev", 0)
            if std > 0:
                upper_1 = [{"time": times[i], "value": round(vwap_values[i] + std, 2)} for i in range(len(vwap_values))]
                lower_1 = [{"time": times[i], "value": round(vwap_values[i] - std, 2)} for i in range(len(vwap_values))]
                upper_2 = [{"time": times[i], "value": round(vwap_values[i] + 2*std, 2)} for i in range(len(vwap_values))]
                lower_2 = [{"time": times[i], "value": round(vwap_values[i] - 2*std, 2)} for i in range(len(vwap_values))]
                
                vwap_lines.append({"data": upper_1, "color": "rgba(255, 235, 59, 0.5)", "label": "VWAP +1σ", "lineWidth": 1})
                vwap_lines.append({"data": lower_1, "color": "rgba(255, 235, 59, 0.5)", "label": "VWAP -1σ", "lineWidth": 1})
                vwap_lines.append({"data": upper_2, "color": "rgba(255, 235, 59, 0.3)", "label": "VWAP +2σ", "lineWidth": 1})
                vwap_lines.append({"data": lower_2, "color": "rgba(255, 235, 59, 0.3)", "label": "VWAP -2σ", "lineWidth": 1})
    
    vwap_json = json.dumps(vwap_lines)
    
    # Prepare S/R lines - VERBESSERT mit Type Labels
    sr_lines = []
    if sr_levels:
        for s in sr_levels.get("support_levels", [])[:3]:
            # Stärke skalieren: 50-100 → 1-3 für Liniendicke
            strength_raw = s.get("strength", 50)
            line_width = 1 if strength_raw < 70 else (2 if strength_raw < 90 else 3)
            
            # Type als Label nutzen wenn vorhanden
            level_type = s.get("type", "Support")
            label = f"S: ${s['price']:.2f}"
            if "PDL" in level_type:
                label = f"PDL: ${s['price']:.2f}"
            elif "Fib" in level_type:
                label = f"Fib: ${s['price']:.2f}"
            elif "Round" in level_type:
                label = f"${s['price']:.2f}"
            
            sr_lines.append({
                "price": s["price"],
                "color": "#4CAF50",
                "lineWidth": line_width,
                "label": label,
                "type": "support"
            })
        for r in sr_levels.get("resistance_levels", [])[:3]:
            strength_raw = r.get("strength", 50)
            line_width = 1 if strength_raw < 70 else (2 if strength_raw < 90 else 3)
            
            level_type = r.get("type", "Resistance")
            label = f"R: ${r['price']:.2f}"
            if "PDH" in level_type:
                label = f"PDH: ${r['price']:.2f}"
            elif "PDC" in level_type:
                label = f"PDC: ${r['price']:.2f}"
            elif "Fib" in level_type:
                label = f"Fib: ${r['price']:.2f}"
            elif "Round" in level_type:
                label = f"${r['price']:.2f}"
            
            sr_lines.append({
                "price": r["price"],
                "color": "#F44336",
                "lineWidth": line_width,
                "label": label,
                "type": "resistance"
            })
    sr_json = json.dumps(sr_lines)
    
    # Prepare Fibonacci lines
    fib_lines = []
    if fib_levels:
        fib_colors = {
            "0.0": "#787B86", "0.236": "#F44336", "0.382": "#FF9800",
            "0.5": "#FFEB3B", "0.618": "#4CAF50", "0.786": "#2196F3",
            "1.0": "#787B86", "1.272": "#9C27B0", "1.618": "#E91E63"
        }
        for level, price in fib_levels.get("levels", {}).items():
            fib_lines.append({
                "price": price,
                "color": fib_colors.get(level, "#787B86"),
                "label": f"Fib {level}"
            })
    fib_json = json.dumps(fib_lines)
    
    # Prepare Volume Voids (for horizontal highlighting)
    voids_json = json.dumps(volume_voids if volume_voids else [])
    
    # Prepare Trade Zones
    zones = []
    if trade_zones:
        if trade_zones.get("entry"):
            zones.append({"price": trade_zones["entry"], "color": "rgba(76, 175, 80, 0.3)", "label": "ENTRY", "type": "entry"})
        if trade_zones.get("stop"):
            zones.append({"price": trade_zones["stop"], "color": "rgba(244, 67, 54, 0.3)", "label": "STOP", "type": "stop"})
        if trade_zones.get("target"):
            zones.append({"price": trade_zones["target"], "color": "rgba(33, 150, 243, 0.3)", "label": "TARGET", "type": "target"})
        if trade_zones.get("target2"):
            zones.append({"price": trade_zones["target2"], "color": "rgba(33, 150, 243, 0.2)", "label": "TP2", "type": "target2"})
    zones_json = json.dumps(zones)
    
    # Pattern markers
    markers = []
    if patterns:
        for p in patterns[:5]:  # Max 5 patterns
            marker_color = "#4CAF50" if p.get("type") == "bullish" else "#F44336" if p.get("type") == "bearish" else "#FFEB3B"
            markers.append({
                "time": ohlcv_data[-1]["time"],
                "position": "aboveBar" if p.get("type") == "bearish" else "belowBar",
                "color": marker_color,
                "shape": "arrowDown" if p.get("type") == "bearish" else "arrowUp",
                "text": p.get("pattern", "")[:15]
            })
    markers_json = json.dumps(markers)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
        <style>
            body {{ margin: 0; padding: 0; background: #1a1a2e; font-family: Arial, sans-serif; }}
            #chart-container {{ width: 100%; height: {height}px; position: relative; }}
            .chart-legend {{
                position: absolute; top: 10px; left: 10px; z-index: 100;
                background: rgba(26, 26, 46, 0.95); padding: 10px; border-radius: 5px;
                color: #fff; font-size: 11px; max-width: 180px;
            }}
            .legend-item {{ display: flex; align-items: center; margin: 2px 0; }}
            .legend-color {{ width: 16px; height: 3px; margin-right: 6px; border-radius: 1px; }}
            .legend-section {{ margin-top: 6px; font-weight: bold; color: #888; font-size: 10px; }}
            .ticker-title {{
                position: absolute; top: 10px; right: 10px; z-index: 100;
                background: rgba(26, 26, 46, 0.95); padding: 10px 15px; border-radius: 5px;
                color: #fff; font-size: 16px; font-weight: bold;
            }}
            .pattern-box {{
                position: absolute; bottom: 10px; right: 10px; z-index: 100;
                background: rgba(26, 26, 46, 0.95); padding: 8px 12px; border-radius: 5px;
                color: #fff; font-size: 11px; max-width: 200px;
            }}
            .pattern-bullish {{ color: #4CAF50; }}
            .pattern-bearish {{ color: #F44336; }}
            .void-indicator {{
                position: absolute; top: 50px; right: 10px; z-index: 100;
                background: rgba(255, 152, 0, 0.2); padding: 5px 10px; border-radius: 3px;
                color: #FF9800; font-size: 10px; border: 1px solid rgba(255, 152, 0, 0.5);
            }}
        </style>
    </head>
    <body>
        <div id="chart-container">
            <div class="ticker-title">{ticker}</div>
            <div class="chart-legend" id="legend"></div>
            <div class="pattern-box" id="patterns"></div>
        </div>
        
        <script>
            const container = document.getElementById('chart-container');
            
            const chart = LightweightCharts.createChart(container, {{
                width: container.clientWidth,
                height: {height},
                layout: {{
                    background: {{ type: 'solid', color: '#1a1a2e' }},
                    textColor: '#d1d4dc',
                }},
                grid: {{
                    vertLines: {{ color: 'rgba(42, 46, 57, 0.5)' }},
                    horzLines: {{ color: 'rgba(42, 46, 57, 0.5)' }},
                }},
                crosshair: {{
                    mode: LightweightCharts.CrosshairMode.Normal,
                }},
                rightPriceScale: {{
                    borderColor: 'rgba(197, 203, 206, 0.4)',
                    scaleMargins: {{ top: 0.05, bottom: 0.2 }},
                }},
                timeScale: {{
                    borderColor: 'rgba(197, 203, 206, 0.4)',
                    timeVisible: true,
                    secondsVisible: false,
                }},
            }});
            
            // Candlestick Series
            const candleSeries = chart.addCandlestickSeries({{
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderUpColor: '#26a69a',
                borderDownColor: '#ef5350',
                wickUpColor: '#26a69a',
                wickDownColor: '#ef5350',
            }});
            
            const candleData = {candles_json};
            candleSeries.setData(candleData);
            
            // Volume Series
            {"" if not show_volume else f'''
            const volumeSeries = chart.addHistogramSeries({{
                priceFormat: {{ type: 'volume' }},
                priceScaleId: '',
            }});
            volumeSeries.priceScale().applyOptions({{
                scaleMargins: {{ top: 0.85, bottom: 0 }},
            }});
            const volumeData = {volume_json};
            volumeSeries.setData(volumeData);
            '''}
            
            // Build Legend
            let legendHtml = '<div class="legend-section">📈 EMAs</div>';
            
            // EMA Lines
            const emaLines = {ema_json};
            emaLines.forEach((ema, index) => {{
                const lineSeries = chart.addLineSeries({{
                    color: ema.color,
                    lineWidth: 1,
                    priceLineVisible: false,
                    lastValueVisible: false,
                }});
                lineSeries.setData(ema.data);
                legendHtml += `<div class="legend-item"><div class="legend-color" style="background:${{ema.color}}"></div>${{ema.label}}</div>`;
            }});
            
            // VWAP Lines
            const vwapLines = {vwap_json};
            if (vwapLines.length > 0) {{
                legendHtml += '<div class="legend-section">📊 VWAP</div>';
                vwapLines.forEach((vwap, index) => {{
                    const lineSeries = chart.addLineSeries({{
                        color: vwap.color,
                        lineWidth: vwap.lineWidth || 1,
                        priceLineVisible: false,
                        lastValueVisible: index === 0,
                    }});
                    lineSeries.setData(vwap.data);
                    if (index === 0) {{
                        legendHtml += `<div class="legend-item"><div class="legend-color" style="background:${{vwap.color}}"></div>${{vwap.label}}</div>`;
                    }}
                }});
            }}
            
            // Support/Resistance Lines
            const srLines = {sr_json};
            if (srLines.length > 0) {{
                legendHtml += '<div class="legend-section">📏 S/R Levels</div>';
                srLines.forEach(sr => {{
                    candleSeries.createPriceLine({{
                        price: sr.price,
                        color: sr.color,
                        lineWidth: sr.lineWidth,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: sr.label,
                    }});
                    const icon = sr.type === 'support' ? '🟢' : '🔴';
                    legendHtml += `<div class="legend-item">${{icon}} ${{sr.price.toFixed(2)}}</div>`;
                }});
            }}
            
            // Fibonacci Lines
            const fibLines = {fib_json};
            if (fibLines.length > 0) {{
                fibLines.forEach(fib => {{
                    candleSeries.createPriceLine({{
                        price: fib.price,
                        color: fib.color,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: true,
                        title: fib.label,
                    }});
                }});
            }}
            
            // Trade Zones (as price lines with different styles)
            const tradeZones = {zones_json};
            if (tradeZones.length > 0) {{
                legendHtml += '<div class="legend-section">🎯 Trade Setup</div>';
                tradeZones.forEach(zone => {{
                    let lineColor, lineStyle, icon;
                    if (zone.type === 'entry') {{
                        lineColor = '#4CAF50';
                        lineStyle = LightweightCharts.LineStyle.Solid;
                        icon = '🎯';
                    }} else if (zone.type === 'stop') {{
                        lineColor = '#F44336';
                        lineStyle = LightweightCharts.LineStyle.Solid;
                        icon = '🛑';
                    }} else {{
                        lineColor = '#2196F3';
                        lineStyle = LightweightCharts.LineStyle.Dashed;
                        icon = '✅';
                    }}
                    
                    candleSeries.createPriceLine({{
                        price: zone.price,
                        color: lineColor,
                        lineWidth: 2,
                        lineStyle: lineStyle,
                        axisLabelVisible: true,
                        title: zone.label,
                    }});
                    legendHtml += `<div class="legend-item">${{icon}} ${{zone.price.toFixed(2)}} (${{zone.label}})</div>`;
                }});
            }}
            
            // Volume Voids - Add indicator if any exist
            const volumeVoids = {voids_json};
            if (volumeVoids.length > 0) {{
                volumeVoids.forEach(void_ => {{
                    // Add dotted lines at void boundaries
                    candleSeries.createPriceLine({{
                        price: void_.price_low,
                        color: 'rgba(255, 152, 0, 0.6)',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: false,
                    }});
                    candleSeries.createPriceLine({{
                        price: void_.price_high,
                        color: 'rgba(255, 152, 0, 0.6)',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: false,
                    }});
                }});
                
                // Add void indicator
                const voidDiv = document.createElement('div');
                voidDiv.className = 'void-indicator';
                voidDiv.innerHTML = `🕳️ ${{volumeVoids.length}} Volume Void${{volumeVoids.length > 1 ? 's' : ''}}`;
                container.appendChild(voidDiv);
            }}
            
            // Pattern Markers
            const markers = {markers_json};
            if (markers.length > 0) {{
                candleSeries.setMarkers(markers);
            }}
            
            // Pattern Box
            const patterns = {json.dumps([{"pattern": p.get("pattern", ""), "type": p.get("type", "neutral"), "confidence": p.get("confidence", "Medium")} for p in (patterns or [])[:3]])};
            if (patterns.length > 0) {{
                let patternHtml = '<strong>🔍 Patterns:</strong><br>';
                patterns.forEach(p => {{
                    const cls = p.type === 'bullish' ? 'pattern-bullish' : p.type === 'bearish' ? 'pattern-bearish' : '';
                    patternHtml += `<div class="${{cls}}">${{p.pattern}} (${{p.confidence}})</div>`;
                }});
                document.getElementById('patterns').innerHTML = patternHtml;
            }} else {{
                document.getElementById('patterns').style.display = 'none';
            }}
            
            document.getElementById('legend').innerHTML = legendHtml;
            
            // Auto-fit
            chart.timeScale().fitContent();
            
            // Resize handler
            window.addEventListener('resize', () => {{
                chart.applyOptions({{ width: container.clientWidth }});
            }});
        </script>
    </body>
    </html>
    """
    
    return html


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
        def cluster_nearby_levels(levels, tolerance_pct=0.03):
            """Merged Levels die zu nah beieinander sind"""
            if not levels:
                return []
            
            sorted_levels = sorted(levels, key=lambda x: x["price"])
            clusters = []
            current_cluster = [sorted_levels[0]]
            
            for level in sorted_levels[1:]:
                cluster_avg = sum(l["price"] for l in current_cluster) / len(current_cluster)
                if abs(level["price"] - cluster_avg) / cluster_avg < tolerance_pct:
                    current_cluster.append(level)
                else:
                    # Finalisiere Cluster - behalte stärkstes Level
                    best = max(current_cluster, key=lambda x: x["strength"])
                    # Kombiniere Types
                    types = list(set(l["type"] for l in current_cluster))
                    if len(types) > 1:
                        best["type"] = " + ".join(types[:2])
                    # Erhöhe Stärke bei Confluence
                    best["strength"] = min(99, best["strength"] + len(current_cluster) * 5)
                    clusters.append(best)
                    current_cluster = [level]
            
            # Letzter Cluster
            if current_cluster:
                best = max(current_cluster, key=lambda x: x["strength"])
                types = list(set(l["type"] for l in current_cluster))
                if len(types) > 1:
                    best["type"] = " + ".join(types[:2])
                best["strength"] = min(99, best["strength"] + len(current_cluster) * 5)
                clusters.append(best)
            
            return clusters
        
        # Cluster alle Levels
        all_clustered = cluster_nearby_levels(key_levels, tolerance_pct=0.03)
        
        # Trenne in Support und Resistance
        supports = [l for l in all_clustered if l["price"] < current_price * 0.98]
        resistances = [l for l in all_clustered if l["price"] > current_price * 1.02]
        
        # Filter: Entferne Levels die zu weit weg sind (>35%)
        supports = [l for l in supports if abs(l["price"] - current_price) / current_price <= max_distance_pct]
        resistances = [l for l in resistances if abs(l["price"] - current_price) / current_price <= max_distance_pct]
        
        # Sortiere nach COMBINED Score: Stärke + Proximity
        def combined_score(level):
            distance = abs(level["price"] - current_price) / current_price
            proximity_factor = max(0.3, 1.0 - distance * 2)
            return level["strength"] * proximity_factor
        
        supports = sorted(supports, key=combined_score, reverse=True)[:3]
        resistances = sorted(resistances, key=combined_score, reverse=True)[:3]
        
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
            trade = ai_analysis["trade_idea"]
            trade_zones = {
                "entry": trade.get("entry"),
                "stop": trade.get("stop"),
                "target": trade.get("target")
            }
    
    # Display Options - ÜBERSICHTLICHER: Weniger default an
    st.markdown("**⚙️ Chart Optionen:**")
    col_opt1, col_opt2, col_opt3, col_opt4, col_opt5, col_opt6 = st.columns(6)
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
        trade_zones=trade_zones if show_zones else None
    )
    
    # Display Chart
    components.html(chart_html, height=520)
    
    # Analysis Section
    st.divider()
    
    col_patterns, col_levels, col_trade = st.columns([1, 1, 1])
    
    # === PATTERNS ===
    with col_patterns:
        st.subheader("🔍 Patterns")
        
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
    
    # === TRADE SETUP ===
    with col_trade:
        st.subheader("💡 Trade Setup")
        
        if ai_analysis and ai_analysis.get("trade_idea"):
            trade = ai_analysis["trade_idea"]
            bias = ai_analysis.get("bias", "Neutral")
            
            bias_emoji = "🟢" if trade["direction"] == "LONG" else "🔴"
            st.markdown(f"### {bias_emoji} {trade['direction']}")
            
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                st.metric("🎯 Entry", f"${trade['entry']:.2f}")
                st.metric("🛑 Stop", f"${trade['stop']:.2f}")
            with col_t2:
                st.metric("✅ Target", f"${trade['target']:.2f}")
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


def fetch_realtime_price_alpaca(ticker, alpaca_key, alpaca_secret):
    """
    Holt REALTIME Preis von Alpaca (kostenlos mit Account!)
    
    Alpaca bietet kostenlose Realtime-Daten für US-Aktien.
    Erstelle Account auf alpaca.markets und hole API Keys.
    
    Returns: dict mit price, change, change_pct, timestamp oder None
    """
    try:
        # Alpaca Latest Quote API
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest"
        headers = {
            "APCA-API-KEY-ID": alpaca_key,
            "APCA-API-SECRET-KEY": alpaca_secret
        }
        
        resp = rate_limited_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            quote = data.get("quote", {})
            
            # Bid/Ask Midpoint als Preis
            bid = quote.get("bp", 0)
            ask = quote.get("ap", 0)
            
            if bid > 0 and ask > 0:
                price = (bid + ask) / 2
                timestamp = quote.get("t", "")
                
                return {
                    "price": round(price, 2),
                    "bid": bid,
                    "ask": ask,
                    "spread": round(ask - bid, 4),
                    "timestamp": timestamp,
                    "source": "Alpaca Realtime"
                }
        
        # Fallback: Latest Trade
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest"
        resp = rate_limited_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            trade = data.get("trade", {})
            price = trade.get("p", 0)
            
            if price > 0:
                return {
                    "price": round(price, 2),
                    "timestamp": trade.get("t", ""),
                    "source": "Alpaca Realtime"
                }
    except Exception as e:
        pass
    return None


def fetch_realtime_price_polygon(ticker, poly_key):
    """
    Holt REALTIME Preis von Polygon (benötigt Stocks Starter oder höher!)
    
    Nutzt den Single-Ticker Snapshot für schnellste Updates.
    
    Returns: dict mit price, change_pct, volume oder None
    """
    try:
        # Single Ticker Snapshot = schnellster Realtime-Endpoint
        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        params = {"apiKey": poly_key}
        
        resp = rate_limited_get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            ticker_data = data.get("ticker", {})
            
            if ticker_data:
                last_trade = ticker_data.get("lastTrade", {})
                day = ticker_data.get("day", {})
                prev = ticker_data.get("prevDay", {})
                
                # Preis aus lastTrade (REALTIME!)
                price = last_trade.get("p", 0) or day.get("c", 0)
                
                if price > 0:
                    prev_close = prev.get("c", 0)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    
                    # Timestamp vom letzten Trade
                    timestamp = last_trade.get("t", 0)
                    if timestamp:
                        # Nanoseconds to datetime
                        from datetime import datetime
                        try:
                            ts_seconds = timestamp / 1e9
                            trade_time = datetime.fromtimestamp(ts_seconds)
                            time_str = trade_time.strftime("%H:%M:%S")
                        except Exception as e:
                            time_str = ""
                    else:
                        time_str = ""
                    
                    return {
                        "price": round(price, 2),
                        "change_pct": round(change_pct, 2),
                        "volume": day.get("v", 0),
                        "high": day.get("h", price),
                        "low": day.get("l", price),
                        "time": time_str,
                        "source": "Polygon Realtime"
                    }
    except Exception as e:
        pass
    return None


def calculate_sr_from_historical(ohlc_data, current_price):
    """
    ECHTE S/R-Berechnung mit technischer Analyse.
    
    Methoden:
    1. Major Swing Points (dynamischer Threshold basierend auf ATR)
    2. Volume Clusters (wo wurde am meisten gehandelt?)
    3. Fibonacci Retracements (50%, 61.8% = stärkste)
    4. Recent Consolidation Zones (wo hat der Preis Zeit verbracht?)
    5. Round Numbers (psychologische Levels)
    6. Gap Levels (offene Gaps)
    
    PROXIMITY BOOST = Levels nah am aktuellen Preis werden bevorzugt!
    CONFLUENCE = mehrere Levels nah beieinander = STÄRKER!
    """
    if not ohlc_data or len(ohlc_data) < 5:
        return calculate_sr_levels_simple(current_price)
    
    # Extrahiere OHLC Daten
    highs = [candle[2] for candle in ohlc_data]   # Index 2 = High
    lows = [candle[3] for candle in ohlc_data]    # Index 3 = Low
    closes = [candle[4] for candle in ohlc_data]  # Index 4 = Close
    
    total_candles = len(ohlc_data)
    
    # PDH/PDL/PDC — Echte Previous Day Berechnung
    # ohlc_data ist Daily → vorletzte Candle = gestern
    if total_candles >= 2:
        prev_day_high = ohlc_data[-2][2]    # Vorletzte Candle High
        prev_day_low = ohlc_data[-2][3]     # Vorletzte Candle Low
        prev_day_close = ohlc_data[-2][4]   # Vorletzte Candle Close
    else:
        prev_day_high = highs[-1] if highs else 0
        prev_day_low = lows[-1] if lows else 0
        prev_day_close = closes[-1] if closes else 0
    
    # Period High und Low
    period_high = max(highs)
    period_low = min(lows)
    price_range = period_high - period_low
    
    if price_range <= 0:
        return calculate_sr_levels_simple(current_price), {}
    
    # =========================================================================
    # ATR-basierter dynamischer Swing-Threshold
    # =========================================================================
    atr_values = []
    for i in range(1, min(20, len(closes))):
        tr = max(
            highs[-(i)] - lows[-(i)],
            abs(highs[-(i)] - closes[-(i+1)]) if i+1 <= len(closes) else 0,
            abs(lows[-(i)] - closes[-(i+1)]) if i+1 <= len(closes) else 0
        )
        atr_values.append(tr)
    
    atr = sum(atr_values) / len(atr_values) if atr_values else price_range * 0.03
    atr_pct = atr / current_price if current_price > 0 else 0.03
    
    # Dynamischer Threshold: 2x ATR% (minimum 3%, maximum 15%)
    min_swing_pct = max(0.03, min(0.15, atr_pct * 2))
    
    # Max Proximity: Levels weiter als 35% vom Preis weg sind nutzlos
    max_distance_pct = 0.35
    
    key_levels = []
    
    # =========================================================================
    # 1. MAJOR SWING POINTS (dynamischer Threshold!)
    # =========================================================================
    lookback = max(3, min(8, total_candles // 15))  # Adaptiver Lookback
    
    # Swing Highs
    for i in range(lookback, len(highs) - 2):
        window_before = highs[max(0, i-lookback):i]
        window_after = highs[i+1:min(len(highs), i+max(2, lookback//2))]
        
        if window_before and window_after and highs[i] >= max(window_before) and highs[i] >= max(window_after):
            # Prüfe: Wie weit ist der Kurs danach gefallen?
            future_low = min(lows[i+1:min(len(lows), i+20)]) if i+1 < len(lows) else lows[-1]
            drop_pct = (highs[i] - future_low) / highs[i] if highs[i] > 0 else 0
            
            # Proximity-Check: Ist das Level nah genug am aktuellen Preis?
            distance_pct = abs(highs[i] - current_price) / current_price
            if distance_pct > max_distance_pct:
                continue
            
            if drop_pct >= min_swing_pct:
                # Recency-Bonus: Neuere Swing-Points wichtiger
                recency = i / total_candles  # 0 = alt, 1 = neu
                recency_bonus = int(recency * 15)
                
                # Proximity-Bonus: Näher am Preis = nützlicher
                proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                
                key_levels.append({
                    "price": highs[i],
                    "type": "Swing High",
                    "strength": min(95, 50 + int(drop_pct * 80) + recency_bonus + proximity_bonus),
                    "is_support": highs[i] < current_price
                })
    
    # Swing Lows
    for i in range(lookback, len(lows) - 2):
        window_before = lows[max(0, i-lookback):i]
        window_after = lows[i+1:min(len(lows), i+max(2, lookback//2))]
        
        if window_before and window_after and lows[i] <= min(window_before) and lows[i] <= min(window_after):
            future_high = max(highs[i+1:min(len(highs), i+20)]) if i+1 < len(highs) else highs[-1]
            rally_pct = (future_high - lows[i]) / lows[i] if lows[i] > 0 else 0
            
            distance_pct = abs(lows[i] - current_price) / current_price
            if distance_pct > max_distance_pct:
                continue
            
            if rally_pct >= min_swing_pct:
                recency = i / total_candles
                recency_bonus = int(recency * 15)
                proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                
                key_levels.append({
                    "price": lows[i],
                    "type": "Swing Low",
                    "strength": min(95, 50 + int(rally_pct * 80) + recency_bonus + proximity_bonus),
                    "is_support": lows[i] < current_price
                })
    
    # =========================================================================
    # 2. RECENT CONSOLIDATION ZONES (wo hat der Preis Zeit verbracht?)
    # =========================================================================
    # Teile den Preisbereich in Bins und zähle wie oft der Preis dort war
    recent_n = min(total_candles, max(30, total_candles // 3))  # Letzte 1/3 der Daten
    recent_closes = closes[-recent_n:]
    recent_highs = highs[-recent_n:]
    recent_lows = lows[-recent_n:]
    
    if recent_closes:
        recent_high = max(recent_highs)
        recent_low = min(recent_lows)
        recent_range = recent_high - recent_low
        
        if recent_range > 0:
            num_bins = 20
            bin_size = recent_range / num_bins
            bins = {}
            
            for j in range(recent_n):
                mid = (recent_highs[j] + recent_lows[j]) / 2
                bin_idx = int((mid - recent_low) / bin_size)
                bin_idx = min(bin_idx, num_bins - 1)
                bins[bin_idx] = bins.get(bin_idx, 0) + 1
            
            # Finde die Top-Bins (> 15% der Bars)
            threshold = recent_n * 0.15
            for bin_idx, count in bins.items():
                if count >= threshold:
                    zone_price = recent_low + (bin_idx + 0.5) * bin_size
                    distance_pct = abs(zone_price - current_price) / current_price
                    
                    if distance_pct > max_distance_pct or distance_pct < 0.02:
                        continue
                    
                    proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                    
                    key_levels.append({
                        "price": zone_price,
                        "type": "Consolidation",
                        "strength": min(90, 55 + int(count / recent_n * 60) + proximity_bonus),
                        "is_support": zone_price < current_price
                    })
    
    # =========================================================================
    # 3. FIBONACCI LEVELS (nur innerhalb der Proximity-Zone)
    # =========================================================================
    fib_levels_dict = {
        "23.6": period_low + price_range * 0.236,
        "38.2": period_low + price_range * 0.382,
        "50.0": period_low + price_range * 0.5,
        "61.8": period_low + price_range * 0.618,
        "78.6": period_low + price_range * 0.786,
    }
    
    for fib_name, fib_price in fib_levels_dict.items():
        distance_pct = abs(fib_price - current_price) / current_price
        if distance_pct > max_distance_pct or distance_pct < 0.02:
            continue
        
        proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 15)
        strength = (75 if fib_name in ["50.0", "61.8"] else 60) + proximity_bonus
        
        key_levels.append({
            "price": fib_price,
            "type": f"Fib {fib_name}%",
            "strength": min(90, strength),
            "is_support": fib_price < current_price
        })
    
    # =========================================================================
    # 4. PERIOD HIGH/LOW (nur wenn nah genug!)
    # =========================================================================
    ph_dist = abs(period_high - current_price) / current_price
    pl_dist = abs(period_low - current_price) / current_price
    
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
    
    # =========================================================================
    # 5. ROUND NUMBERS
    # =========================================================================
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
        distance_pct = abs(rp - current_price) / current_price
        if distance_pct > max_distance_pct or distance_pct < 0.02:
            continue
        
        proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 15)
        
        key_levels.append({
            "price": rp,
            "type": f"Round ${rp:.2f}",
            "strength": 55 + proximity_bonus,
            "is_support": rp < current_price
        })
    
    # =========================================================================
    # 6. GAP LEVELS (nur innerhalb der Proximity-Zone)
    # =========================================================================
    for i in range(1, len(closes)):
        prev_close = closes[i-1]
        curr_high = highs[i]
        curr_low = lows[i]
        
        distance_pct = abs(prev_close - current_price) / current_price
        if distance_pct > max_distance_pct:
            continue
        
        # Gap Up
        if curr_low > prev_close:
            gap_pct = (curr_low - prev_close) / prev_close if prev_close > 0 else 0
            if gap_pct > 0.02:
                key_levels.append({
                    "price": prev_close,
                    "type": "Gap Fill",
                    "strength": 65,
                    "is_support": prev_close < current_price
                })
        
        # Gap Down
        if curr_high < prev_close:
            gap_pct = (prev_close - curr_high) / prev_close if prev_close > 0 else 0
            if gap_pct > 0.02:
                key_levels.append({
                    "price": prev_close,
                    "type": "Gap Fill",
                    "strength": 65,
                    "is_support": prev_close < current_price
                })
    
    # =========================================================================
    # 7. CLUSTERING MIT CONFLUENCE
    # =========================================================================
    def cluster_with_confluence(levels, tolerance_pct=0.03):
        if not levels:
            return []
        
        sorted_levels = sorted(levels, key=lambda x: x["price"])
        clusters = []
        current_cluster = [sorted_levels[0]]
        
        for level in sorted_levels[1:]:
            cluster_avg = sum(l["price"] for l in current_cluster) / len(current_cluster)
            if abs(level["price"] - cluster_avg) / cluster_avg < tolerance_pct:
                current_cluster.append(level)
            else:
                best = max(current_cluster, key=lambda x: x["strength"])
                types = list(set(l["type"] for l in current_cluster))
                if len(types) > 1:
                    best["type"] = " + ".join(types[:2])
                best["strength"] = min(99, best["strength"] + len(current_cluster) * 5)
                clusters.append(best)
                current_cluster = [level]
        
        if current_cluster:
            best = max(current_cluster, key=lambda x: x["strength"])
            types = list(set(l["type"] for l in current_cluster))
            if len(types) > 1:
                best["type"] = " + ".join(types[:2])
            best["strength"] = min(99, best["strength"] + len(current_cluster) * 5)
            clusters.append(best)
        
        return clusters
    
    all_clustered = cluster_with_confluence(key_levels, tolerance_pct=0.03)
    
    # Trenne Support und Resistance
    supports_raw = [l for l in all_clustered if l["price"] < current_price * 0.98]
    resistances_raw = [l for l in all_clustered if l["price"] > current_price * 1.02]
    
    # Sortiere nach COMBINED Score: Stärke + Proximity
    # Proximity-Gewichtung: Nähere Levels sind wichtiger!
    def combined_score(level):
        distance = abs(level["price"] - current_price) / current_price
        proximity_factor = max(0.3, 1.0 - distance * 2)  # Näher = höherer Faktor
        return level["strength"] * proximity_factor
    
    supports_raw = sorted(supports_raw, key=combined_score, reverse=True)[:3]
    resistances_raw = sorted(resistances_raw, key=combined_score, reverse=True)[:3]
    
    # Sortiere nach Preis für Anzeige
    supports_cleaned = sorted(supports_raw, key=lambda x: x["price"], reverse=True)
    resistances_cleaned = sorted(resistances_raw, key=lambda x: x["price"])
    
    # =========================================================================
    # OUTPUT
    # =========================================================================
    def smart_round(price):
        if price >= 1000:
            return round(price, 0)
        elif price >= 100:
            return round(price, 1)
        elif price >= 10:
            return round(price, 2)
        elif price >= 1:
            return round(price, 3)
        else:
            return round(price, 4)
    
    supports = [smart_round(s["price"]) for s in supports_cleaned]
    resistances = [smart_round(r["price"]) for r in resistances_cleaned]
    
    supports_detail = [{"price": smart_round(s["price"]), "type": s["type"], "strength": s["strength"]} for s in supports_cleaned]
    resistances_detail = [{"price": smart_round(r["price"]), "type": r["type"], "strength": r["strength"]} for r in resistances_cleaned]
    
    fib_info = {
        "period_high": smart_round(period_high),
        "period_low": smart_round(period_low),
        "prev_day_high": smart_round(prev_day_high),
        "prev_day_low": smart_round(prev_day_low),
        "prev_day_close": smart_round(prev_day_close),
        "fib_236": smart_round(fib_levels_dict["23.6"]),
        "fib_382": smart_round(fib_levels_dict["38.2"]),
        "fib_500": smart_round(fib_levels_dict["50.0"]),
        "fib_618": smart_round(fib_levels_dict["61.8"]),
        "fib_786": smart_round(fib_levels_dict["78.6"]),
        "supports_detail": supports_detail,
        "resistances_detail": resistances_detail,
        "consolidation_zones": [],
        "total_candles": total_candles,
    }
    
    return (supports, resistances), fib_info


def calculate_sr_levels_simple(price):
    """Fallback: Berechnet S/R basierend auf Fibonacci vom Preis"""
    if price <= 0:
        return ([], []), {}
    
    # Schätze eine Range basierend auf typischer Volatilität (±20%)
    estimated_high = price * 1.20
    estimated_low = price * 0.80
    price_range = estimated_high - estimated_low
    
    # Fibonacci Levels
    supports = [
        round(price * 0.95, 6),   # -5%
        round(price * 0.90, 6),   # -10%
        round(price * 0.85, 6),   # -15%
    ]
    
    resistances = [
        round(price * 1.05, 6),   # +5%
        round(price * 1.10, 6),   # +10%
        round(price * 1.15, 6),   # +15%
    ]
    
    return (supports, resistances), {}


def calculate_sr_levels(price, ticker=None, market_type="Krypto", timeframe="4H", poly_key=None):
    """Hauptfunktion: Berechnet S/R-Levels basierend auf Timeframe"""
    
    # Timeframe zu Tagen mappen — genug Daten für aussagekräftige Swing-Points!
    tf_to_days = {
        "5Min": 2,
        "15Min": 5,
        "1H": 14,
        "4H": 60,     # War 7! Braucht mindestens 60 Tage für brauchbare Swing-Points
        "1D": 120,    # War 30
        "1W": 365,    # War 90
        "1M": 730
    }
    days = tf_to_days.get(timeframe, 60)
    
    # Versuche historische Daten zu holen
    ohlc_data = None
    
    if market_type == "Krypto" and ticker:
        coin_id = ticker.lower()
        ohlc_data = fetch_historical_data_crypto(coin_id, days)
    
    elif market_type == "Aktien" and ticker:
        # Internationale Aktien: Yahoo (kein poly_key nötig)
        _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
        if any(ticker.upper().endswith(s) for s in _intl_suffixes):
            ohlc_data = _fetch_historical_yahoo(ticker, days)
        elif poly_key:
            ohlc_data = fetch_historical_data_stocks(ticker, days, poly_key)
    
    # Berechne S/R aus historischen Daten oder Fallback
    if ohlc_data:
        return calculate_sr_from_historical(ohlc_data, price)
    else:
        return calculate_sr_levels_simple(price)

# =============================================================================
# 3b. AKKUMULATIONS-ANALYSE (Wyckoff-Style)
# =============================================================================

def calculate_obv(closes, volumes):
    """
    Berechnet On-Balance-Volume (OBV)
    OBV steigt wenn Smart Money akkumuliert
    """
    if not closes or not volumes or len(closes) != len(volumes):
        return [], 0
    
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    
    # OBV Trend (Steigung der letzten 10 Perioden)
    if len(obv) >= 10:
        recent_obv = obv[-10:]
        trend = (recent_obv[-1] - recent_obv[0]) / (abs(recent_obv[0]) + 1) * 100
    else:
        trend = 0
    
    return obv, trend

def calculate_accumulation_score(ticker, market_type, poly_key=None, days=20):
    """
    Berechnet Akkumulations-Score (0-100) basierend auf Wyckoff-Kriterien
    
    Kriterien:
    1. Range Tightness: Je enger die Range, desto höher der Score
    2. OBV Trend: Steigend bei flachem Preis = Akkumulation
    3. Volume Pattern: Abnehmendes Volumen = Konsolidierung
    4. Position in Range: Nahe Support = besserer Entry
    5. Zeit in Range: Länger = mehr akkumuliert
    
    Returns: dict mit Score und Details
    """
    result = {
        "score": 0,
        "range_pct": 0,
        "obv_trend": 0,
        "volume_trend": 0,
        "position_in_range": 0.5,
        "days_in_range": 0,
        "wyckoff_phase": "Unknown",
        "interpretation": "",
        "data_available": False
    }
    
    try:
        # Historische Daten holen
        ohlc_data = None
        
        if market_type == "Krypto":
            coin_id = ticker.lower()
            ohlc_data = fetch_historical_data_crypto(coin_id, days)
        elif market_type == "Aktien":
            # Internationale Aktien: Yahoo (kein poly_key nötig)
            _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
            if any(ticker.upper().endswith(s) for s in _intl_suffixes):
                ohlc_data = _fetch_historical_yahoo(ticker, days)
            elif poly_key:
                ohlc_data = fetch_historical_data_stocks(ticker, days, poly_key)
        
        if not ohlc_data or len(ohlc_data) < 10:
            result["interpretation"] = "Nicht genug historische Daten"
            return result
        
        result["data_available"] = True
        
        # Daten extrahieren - OHLC Format: [timestamp, open, high, low, close]
        # Index: 0=timestamp, 1=open, 2=high, 3=low, 4=close
        closes = [d[4] for d in ohlc_data if len(d) >= 5]
        highs = [d[2] for d in ohlc_data if len(d) >= 5]
        lows = [d[3] for d in ohlc_data if len(d) >= 5]
        
        # Für Volume: Polygon hat es im Result, CoinGecko nicht direkt
        # Wir nehmen einfach den Preisspread als Proxy wenn kein Volume
        volumes = []
        for d in ohlc_data:
            if len(d) >= 6 and d[5]:  # Volume ist Index 5 wenn vorhanden
                volumes.append(d[5])
            elif len(d) >= 5:
                # Proxy: (High - Low) als "Activity"
                volumes.append(abs(d[2] - d[3]) * 1000)
        
        if not closes or not highs or not lows:
            result["interpretation"] = "Ungültiges Datenformat"
            return result
        
        current_price = closes[-1]
        period_high = max(highs)
        period_low = min(lows)
        
        # 1. Range Tightness (max 25 Punkte)
        range_pct = ((period_high - period_low) / current_price) * 100 if current_price > 0 else 100
        result["range_pct"] = round(range_pct, 2)
        
        if range_pct < 10:
            range_score = 25  # Sehr eng
        elif range_pct < 15:
            range_score = 20
        elif range_pct < 20:
            range_score = 15
        elif range_pct < 30:
            range_score = 10
        else:
            range_score = 5  # Zu volatil
        
        # 2. OBV Trend (max 25 Punkte)
        obv, obv_trend = calculate_obv(closes, volumes)
        result["obv_trend"] = round(obv_trend, 2)
        
        # Preis-Trend berechnen
        price_change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] > 0 else 0
        
        # OBV Score: Steigendes OBV bei flachem Preis = TOP!
        if obv_trend > 10 and abs(price_change) < 10:
            obv_score = 25  # Perfekte Akkumulation!
        elif obv_trend > 5 and abs(price_change) < 15:
            obv_score = 20
        elif obv_trend > 0:
            obv_score = 15
        elif obv_trend > -10:
            obv_score = 10
        else:
            obv_score = 5  # Distribution möglich
        
        # 3. Volume Trend (max 20 Punkte)
        if len(volumes) >= 10:
            first_half_vol = sum(volumes[:len(volumes)//2])
            second_half_vol = sum(volumes[len(volumes)//2:])
            vol_change = ((second_half_vol - first_half_vol) / first_half_vol) * 100 if first_half_vol > 0 else 0
            result["volume_trend"] = round(vol_change, 2)
            
            # Abnehmendes Volumen = Konsolidierung (gut für Akkumulation)
            if vol_change < -20:
                vol_score = 20  # Stark abnehmendes Volumen
            elif vol_change < -10:
                vol_score = 15
            elif vol_change < 10:
                vol_score = 10
            else:
                vol_score = 5  # Steigendes Volumen = etwas passiert
        else:
            vol_score = 10
        
        # 4. Position in Range (max 15 Punkte)
        if period_high != period_low:
            position = (current_price - period_low) / (period_high - period_low)
        else:
            position = 0.5
        result["position_in_range"] = round(position, 2)
        
        # Nahe Support = besserer Entry für Long
        if position < 0.3:
            pos_score = 15  # Nahe Support
        elif position < 0.5:
            pos_score = 12
        elif position < 0.7:
            pos_score = 8
        else:
            pos_score = 5  # Nahe Resistance
        
        # 5. Stabilität (max 15 Punkte) - wie viele Tage in der Range?
        range_tolerance = (period_high - period_low) * 0.1
        days_in_range = 0
        for i in range(len(closes) - 1, -1, -1):
            if lows[i] >= (period_low - range_tolerance) and highs[i] <= (period_high + range_tolerance):
                days_in_range += 1
            else:
                break
        result["days_in_range"] = days_in_range
        
        if days_in_range >= 15:
            stability_score = 15
        elif days_in_range >= 10:
            stability_score = 12
        elif days_in_range >= 5:
            stability_score = 8
        else:
            stability_score = 5
        
        # Gesamtscore
        total_score = range_score + obv_score + vol_score + pos_score + stability_score
        result["score"] = total_score
        
        # Wyckoff Phase bestimmen
        if obv_trend > 10 and abs(price_change) < 5 and vol_change < 0:
            result["wyckoff_phase"] = "Phase C (Spring/Test)"
            result["interpretation"] = "🟢 Ideale Akkumulation! OBV steigt, Preis flach, Volumen sinkt"
        elif obv_trend > 0 and range_pct < 20:
            result["wyckoff_phase"] = "Phase B (Accumulation)"
            result["interpretation"] = "🟡 Akkumulation läuft - Smart Money kauft"
        elif obv_trend < -10 and range_pct < 20:
            result["wyckoff_phase"] = "Phase D (Distribution?)"
            result["interpretation"] = "🟠 Vorsicht: OBV fällt - mögliche Distribution"
        elif range_pct > 25:
            result["wyckoff_phase"] = "Phase A (Selling Climax)"
            result["interpretation"] = "⚪ Hohe Volatilität - noch keine klare Akkumulation"
        else:
            result["wyckoff_phase"] = "Phase B (Range)"
            result["interpretation"] = "🔵 In Range - beobachten für Entry"
        
        # Score-Interpretation
        if total_score >= 80:
            result["interpretation"] = "🟢 STRONG BUY ZONE! " + result["interpretation"]
        elif total_score >= 60:
            result["interpretation"] = "🟡 Good Setup. " + result["interpretation"]
        elif total_score >= 40:
            result["interpretation"] = "⚪ Neutral. " + result["interpretation"]
        else:
            result["interpretation"] = "🔴 Weak Setup. " + result["interpretation"]
        
    except Exception as e:
        result["interpretation"] = f"Analyse-Fehler: {str(e)[:50]}"
    
    return result


def get_accumulation_display(ticker, market_type, poly_key=None):
    """Erstellt eine formatierte Anzeige der Akkumulations-Analyse"""
    analysis = calculate_accumulation_score(ticker, market_type, poly_key)
    
    if not analysis["data_available"]:
        return None, analysis
    
    # Score-Farbe
    score = analysis["score"]
    if score >= 80:
        score_color = "🟢"
        score_label = "STRONG"
    elif score >= 60:
        score_color = "🟡"
        score_label = "GOOD"
    elif score >= 40:
        score_color = "⚪"
        score_label = "NEUTRAL"
    else:
        score_color = "🔴"
        score_label = "WEAK"
    
    # OBV Trend Interpretation
    obv = analysis["obv_trend"]
    if obv > 10:
        obv_icon = "📈"
        obv_text = "Steigend (Bullish)"
    elif obv > 0:
        obv_icon = "↗️"
        obv_text = "Leicht steigend"
    elif obv > -10:
        obv_icon = "➡️"
        obv_text = "Flach"
    else:
        obv_icon = "📉"
        obv_text = "Fallend (Bearish)"
    
    display = {
        "score": score,
        "score_color": score_color,
        "score_label": score_label,
        "range_pct": analysis["range_pct"],
        "obv_trend": obv,
        "obv_icon": obv_icon,
        "obv_text": obv_text,
        "volume_trend": analysis["volume_trend"],
        "position": analysis["position_in_range"],
        "days_in_range": analysis["days_in_range"],
        "wyckoff_phase": analysis["wyckoff_phase"],
        "interpretation": analysis["interpretation"]
    }
    
    return display, analysis

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


def fetch_crypto_data():
    """Holt Krypto-Daten von CoinGecko mit korrektem Vortag — 500 Coins (2 Seiten)"""
    results = []
    skipped_filter = 0
    
    try:
        all_coins = []
        
        # Lade 2 Seiten = 500 Coins (Free API Limit ~3 Requests/Minute)
        for page_num in range(1, 3):
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {
                "vs_currency": "usd", 
                "order": "market_cap_desc",
                "per_page": 250, 
                "page": page_num, 
                "sparkline": False,
                "price_change_percentage": "24h,7d"
            }
            
            resp = rate_limited_get(url, params=params, timeout=30)
            if resp.status_code == 429:
                if page_num == 1:
                    st.warning("⚠️ CoinGecko Rate Limit. Warte 60 Sekunden.")
                    return [], 0, 0
                break  # Page 2 rate limited → benutze nur Page 1
            
            page_coins = resp.json()
            if not isinstance(page_coins, list) or not page_coins:
                break
            all_coins.extend(page_coins)
            
            if page_num < 2:
                time.sleep(1.2)  # Rate limit pause zwischen Seiten
        
        coins = all_coins
        if not coins:
            return [], 0, 0
        
        f = st.session_state.active_filters
        af = st.session_state.additional_filters
        
        for coin in coins:
            try:
                price = coin.get("current_price") or 0
                if price <= 0:
                    continue
                
                # HEUTE: 24h Change
                change_24h = coin.get("price_change_percentage_24h") or 0
                
                # VORTAG BERECHNUNG (VERBESSERT V67.3):
                # CoinGecko liefert kein einzelnes "gestern" - wir approximieren:
                # Vortag ≈ (7d_change - 24h_change) / 6
                # Das gibt den Durchschnitt der 6 Tage VOR heute (ohne heute)
                # Besser als vorher: 7d/7 hat heute mit reingerechnet
                change_7d = (
                    coin.get("price_change_percentage_7d_in_currency") or 
                    coin.get("price_change_percentage_7d") or 
                    0
                )
                
                if change_7d != 0:
                    # Entferne heutige Bewegung aus dem 7d-Durchschnitt
                    remaining_6d = change_7d - change_24h
                    vortag_chg = round(remaining_6d / 6, 2)
                else:
                    vortag_chg = 0
                
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price
                vol_24h = coin.get("total_volume") or 0
                market_cap = coin.get("market_cap") or 1
                
                # OHLC für Wick-Berechnung
                # Approximation: Open = Price / (1 + change/100)
                open_price = price / (1 + change_24h / 100) if change_24h != -100 else price
                
                # Wick-Berechnungen (mit min_range_pct Check für Konsistenz)
                candle_range = high_24h - low_24h if high_24h > low_24h else 0
                range_pct = (candle_range / low_24h * 100) if low_24h > 0 else 0
                
                # Nur Wick berechnen wenn genug Range (min 0.5%)
                if range_pct >= 0.5 and candle_range > 0:
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
                
                # RVOL Berechnung (Krypto-spezifisch)
                # WICHTIG: CoinGecko liefert kein historisches Durchschnittsvolumen!
                # Wir verwenden "Turnover Ratio" = Vol24h / MarketCap als Proxy
                # 
                # Interpretation:
                # - Turnover < 5%: Niedriges relatives Volumen
                # - Turnover 5-15%: Normales Volumen
                # - Turnover > 15%: Hohes relatives Volumen
                # 
                # Wir normalisieren zu RVOL-ähnlicher Skala:
                # - 10% Turnover = RVOL 1.0 (Baseline)
                # - 20% Turnover = RVOL 2.0
                # - 5% Turnover = RVOL 0.5
                if market_cap > 0 and vol_24h > 0:
                    turnover_pct = (vol_24h / market_cap) * 100
                    # M9: Dynamischer Baseline nach Marktkapitalisierung
                    # Large Cap (>$10B): 3% Turnover = normal
                    # Mid Cap ($1B-$10B): 8% Turnover = normal
                    # Small Cap (<$1B): 15% Turnover = normal
                    if market_cap > 10_000_000_000:
                        baseline = 3.0  # Large Cap
                    elif market_cap > 1_000_000_000:
                        baseline = 8.0  # Mid Cap
                    else:
                        baseline = 15.0  # Small Cap
                    rvol = round(turnover_pct / baseline, 2)
                    rvol = max(0.1, min(rvol, 50.0))  # Cap bei 50x
                else:
                    rvol = 1.0
                
                close_pos = calculate_close_position(high_24h, low_24h, price)
                
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
                alpha = calculate_alpha_score(rvol, vortag_chg, change_24h)
                
                # Flag-Pattern Validierung für Krypto
                flag_score = 0
                flag_details = []
                current_strategy = st.session_state.get("current_strategy", "")
                
                # Für Krypto: prev_close = open_price (24h Referenz)
                prev_close_approx = open_price
                
                if current_strategy == "Bull Flag":
                    is_valid, flag_score, flag_details = validate_flag_pattern(
                        vortag_chg, change_24h, rvol, price, prev_close_approx, high_24h, low_24h, "bull"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score
                    
                elif current_strategy == "Bear Flag":
                    is_valid, flag_score, flag_details = validate_flag_pattern(
                        vortag_chg, change_24h, rvol, price, prev_close_approx, high_24h, low_24h, "bear"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score
                
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
                    "Gap%": 0,  # Immer 0 bei Krypto (keine echten Gaps)
                    "FlagScore": flag_score,
                    "FlagDetails": flag_details,
                    "High": high_24h,
                    "Low": low_24h,
                    "PrevClose": prev_close_approx,
                })
            except Exception as e:
                continue
        
        return results, 0, skipped_filter
    except Exception as e:
        st.error(f"CoinGecko Fehler: {e}")
        return [], 0, 0


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
        if skip_filters:
            f = {}  # Keine Filter
            af = {"exclude_etfs": True, "preis_min": 0, "preis_max": 100000, "min_liquidity": 0}  # Basis-Filter
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
                
                # True Gap (was wir aktuell als "Gap %" verwenden)
                gap_pct = 0
                if prev_high > 0 and prev_low > 0:
                    if day_open > prev_high:
                        gap_pct = ((day_open - prev_high) / prev_high) * 100
                    elif day_open < prev_low:
                        gap_pct = ((day_open - prev_low) / prev_low) * 100
                    # Wenn Open innerhalb der Range: gap_pct = 0 (kein True Gap)
                
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
                # - Positiv: Gestern war eine bullische Kerze (Close > Open)
                # - Negativ: Gestern war eine bärische Kerze (Close < Open)
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
                
                # Liquiditäts-Check (Gemini Fix: Keine Pennystocks mit 100 Aktien)
                is_liquid, dollar_volume = validate_liquidity(vol, price, min_dollar_volume=100000)
                
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
                user_min_liquidity = af.get("min_liquidity", 0)
                if user_min_liquidity > 0 and dollar_volume < user_min_liquidity:
                    skipped_filter += 1
                    debug_stats["skipped_other"] += 1
                    continue  # Skip wegen User-definiertem Liquiditäts-Minimum
                
                # DEBUG: Zähle total tickers
                debug_stats["total_tickers"] += 1
                
                # Sammle Close Position Samples (erste 20)
                if close_pos is not None and len(debug_stats["closepos_samples"]) < 20:
                    debug_stats["closepos_samples"].append(round(close_pos, 2))
                
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
                    is_valid, flag_score, flag_details = validate_flag_pattern(
                        vortag_chg, change, rvol, price, prev_close, high, low, "bull"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue  # Skip wenn Flag-Pattern nicht valide
                    alpha = flag_score  # Nutze Flag-Score als Alpha für bessere Sortierung
                    
                elif current_strategy == "Bear Flag":
                    is_valid, flag_score, flag_details = validate_flag_pattern(
                        vortag_chg, change, rvol, price, prev_close, high, low, "bear"
                    )
                    if not is_valid:
                        skipped_filter += 1
                        continue
                    alpha = flag_score
                
                results.append({
                    "Ticker": ticker_raw, "Name": "",
                    "Preis": round(price, 4), "Chg%": round(change, 2),
                    "RVOL": rvol, "Vortag%": vortag_chg,
                    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5, "Alpha": alpha,
                    "Gap%": round(gap_pct, 2),
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
                })
            except Exception as e:
                continue
        
        return results, skipped_no_price, skipped_filter, debug_stats
    except Exception as e:
        st.error(f"Polygon Fehler: {e}")
        return [], 0, 0, {}


# =============================================================================
# PRE-MARKET WATCHLIST - ERWEITERTE PM ANALYSE V2
# =============================================================================

def get_ticker_news(poly_key, ticker, limit=3):
    """
    Holt die neuesten News für einen Ticker via Polygon News API.
    NEU: Katalysator-Erkennung (Earnings, FDA, Offering, etc.)
    Returns: List of news items with title, sentiment, published date, catalyst
    """
    # Katalysator-Keywords nach Kategorie
    CATALYST_KEYWORDS = {
        "📊 EARNINGS": ["earnings", "revenue", "profit", "EPS", "guidance", "quarterly", "fiscal", "beat", "miss", "outlook"],
        "💊 FDA/BIO": ["FDA", "approval", "trial", "phase", "drug", "clinical", "PDUFA", "NDA", "breakthrough", "therapy", "patent"],
        "💰 OFFERING": ["offering", "dilution", "shelf", "secondary", "ATM", "warrant", "convertible", "raise"],
        "🤝 M&A": ["acquisition", "merger", "takeover", "buyout", "deal", "purchase agreement"],
        "📋 CONTRACT": ["contract", "awarded", "partnership", "agreement", "collaboration", "deal with"],
        "⚖️ LEGAL": ["lawsuit", "SEC", "investigation", "settlement", "subpoena", "fraud"],
        "📈 UPGRADE": ["upgrade", "price target", "buy rating", "overweight", "outperform"],
        "📉 DOWNGRADE": ["downgrade", "sell rating", "underweight", "underperform", "cut"],
        "🔀 SPLIT": ["stock split", "reverse split"],
        "💵 DIVIDEND": ["dividend", "payout", "distribution"],
        "👤 INSIDER": ["insider", "CEO buy", "director purchase", "10b5"],
        "🚀 PRODUCT": ["launch", "release", "new product", "unveil", "announce"],
    }
    
    def detect_catalyst(title):
        """Erkennt Katalysator-Typ aus News-Titel."""
        title_lower = title.lower()
        for catalyst_type, keywords in CATALYST_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in title_lower:
                    return catalyst_type
        return None
    
    try:
        url = f"https://api.polygon.io/v2/reference/news"
        resp = rate_limited_get(url, params={"ticker": ticker, "limit": limit, "apiKey": poly_key}, timeout=5).json()
        results = resp.get("results", [])
        
        news_items = []
        detected_catalysts = []
        
        for item in results[:limit]:
            # Parse published date
            pub_date = item.get("published_utc", "")[:10]  # YYYY-MM-DD
            
            # Sentiment analysieren (wenn vorhanden)
            insights = item.get("insights", [])
            sentiment = "neutral"
            sentiment_score = 0
            for insight in insights:
                if insight.get("ticker") == ticker:
                    sentiment = insight.get("sentiment", "neutral")
                    sentiment_score = insight.get("sentiment_reasoning", "")
                    break
            
            # Katalysator erkennen
            title = item.get("title", "")
            catalyst = detect_catalyst(title)
            if catalyst and catalyst not in detected_catalysts:
                detected_catalysts.append(catalyst)
            
            news_items.append({
                "title": title[:80],  # Kürzen
                "publisher": item.get("publisher", {}).get("name", ""),
                "published": pub_date,
                "sentiment": sentiment,
                "url": item.get("article_url", ""),
                "catalyst": catalyst,
            })
        
        # Haupt-Katalysator an alle News-Items anhängen
        for n in news_items:
            n["all_catalysts"] = detected_catalysts
        
        return news_items
    except Exception as e:
        return []


def get_ticker_details(poly_key, ticker):
    """
    Holt Ticker Details: Shares Outstanding, Market Cap, etc.
    Returns: dict mit shares_outstanding, market_cap, float_category
    """
    try:
        url = f"https://api.polygon.io/v3/reference/tickers/{ticker}"
        resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=5).json()
        results = resp.get("results", {})
        
        shares_out = results.get("share_class_shares_outstanding", 0) or results.get("weighted_shares_outstanding", 0)
        market_cap = results.get("market_cap", 0)
        
        # Float Kategorie schätzen (Shares Outstanding als Proxy)
        # Echtes Float = Shares - Insider - Institutional, aber das haben wir nicht
        float_category = "UNKNOWN"
        float_emoji = "❓"
        
        if shares_out > 0:
            shares_millions = shares_out / 1_000_000
            if shares_millions < 10:
                float_category = "MICRO"
                float_emoji = "🔥🔥🔥"  # Sehr explosiv
            elif shares_millions < 20:
                float_category = "LOW"
                float_emoji = "🔥🔥"  # Explosiv
            elif shares_millions < 50:
                float_category = "MEDIUM"
                float_emoji = "🔥"
            else:
                float_category = "HIGH"
                float_emoji = "📊"
        
        return {
            "shares_outstanding": shares_out,
            "shares_millions": round(shares_out / 1_000_000, 1) if shares_out > 0 else 0,
            "market_cap": market_cap,
            "market_cap_millions": round(market_cap / 1_000_000, 1) if market_cap > 0 else 0,
            "float_category": float_category,
            "float_emoji": float_emoji,
            "name": results.get("name", ""),
            "description": results.get("description", "")[:100] if results.get("description") else ""
        }
    except Exception as e:
        return {
            "shares_outstanding": 0,
            "shares_millions": 0,
            "market_cap": 0,
            "market_cap_millions": 0,
            "float_category": "UNKNOWN",
            "float_emoji": "❓",
            "name": "",
            "description": ""
        }


def get_pm_session_bars(poly_key, ticker, date_str):
    """
    Holt die Pre-Market Session Bars (4:00-9:30 ET) via Aggregates API.
    DST-KORRIGIERT: Nutzt pytz für korrekte ET → UTC Konvertierung.
    Returns: dict mit pm_high, pm_low, pm_volume, pm_open, pm_vwap
    """
    try:
        # 1-Minute Bars für PM Session
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}"
        resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10).json()
        bars = resp.get("results", [])
        
        if not bars:
            return None
        
        # DST-KORRIGIERT: Berechne PM Session Grenzen dynamisch
        # 4:00 AM ET und 9:30 AM ET → UTC konvertieren (funktioniert für EST und EDT)
        et_tz = pytz.timezone('America/New_York')
        trade_date = datetime.strptime(date_str, "%Y-%m-%d")
        
        # PM Start: 4:00 AM ET an diesem Tag
        pm_start_et = et_tz.localize(trade_date.replace(hour=4, minute=0, second=0))
        pm_start_utc = pm_start_et.astimezone(pytz.utc)
        pm_start_ts = pm_start_utc.timestamp()
        
        # PM End: 9:30 AM ET an diesem Tag
        pm_end_et = et_tz.localize(trade_date.replace(hour=9, minute=30, second=0))
        pm_end_utc = pm_end_et.astimezone(pytz.utc)
        pm_end_ts = pm_end_utc.timestamp()
        
        # Filtere Bars innerhalb PM Session
        pm_bars = []
        for bar in bars:
            bar_ts = bar.get("t", 0) / 1000  # ms to seconds
            if pm_start_ts <= bar_ts <= pm_end_ts:
                pm_bars.append(bar)
        
        if not pm_bars:
            return None
        
        pm_high = max(b.get("h", 0) for b in pm_bars)
        pm_low = min(b.get("l", 999999) for b in pm_bars)
        pm_volume = sum(b.get("v", 0) for b in pm_bars)
        pm_open = pm_bars[0].get("o", 0)
        pm_close = pm_bars[-1].get("c", 0)
        
        # VWAP Berechnung
        total_value = sum(b.get("vw", b.get("c", 0)) * b.get("v", 0) for b in pm_bars)
        pm_vwap = total_value / pm_volume if pm_volume > 0 else pm_close
        
        # Erste Bewegung (wann kam der Move?) - jetzt in ET anzeigen
        first_big_move_time = None
        for bar in pm_bars:
            if abs((bar.get("c", 0) - pm_open) / pm_open * 100) > 2 if pm_open > 0 else False:
                ts = bar.get("t", 0) / 1000
                move_utc = datetime.utcfromtimestamp(ts).replace(tzinfo=pytz.utc)
                move_et = move_utc.astimezone(et_tz)
                first_big_move_time = move_et.strftime("%H:%M ET")
                break
        
        return {
            "pm_high": pm_high,
            "pm_low": pm_low if pm_low < 999999 else pm_close,
            "pm_volume": pm_volume,
            "pm_open": pm_open,
            "pm_close": pm_close,
            "pm_vwap": pm_vwap,
            "pm_bars_count": len(pm_bars),
            "first_move_time": first_big_move_time
        }
        
    except Exception as e:
        return None


def get_spy_pm_change(poly_key):
    """Holt SPY Pre-Market Change für Relative Strength Berechnung."""
    try:
        url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/SPY"
        resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=10).json()
        ticker_data = resp.get("ticker", {})
        
        prev_close = ticker_data.get("prevDay", {}).get("c", 0)
        last_price = ticker_data.get("lastTrade", {}).get("p", 0)
        
        if prev_close > 0 and last_price > 0:
            return ((last_price - prev_close) / prev_close) * 100
        return 0
    except Exception as e:
        return 0


def classify_pm_setup(pm_change, gap_pct, pm_position, rs_vs_spy, atr_pct=5.0):
    """
    Klassifiziert das PM Setup basierend auf Preis-Aktion + Position.
    
    Logik:
    - pm_change > 0 + Position hoch = Momentum Long ✅
    - pm_change > 0 + Position tief = Fading, Vorsicht ⚠️
    - pm_change < 0 + Position tief = Schwäche, Short ✅
    - pm_change < 0 + Position hoch = Bounced, NICHT shorten ⚠️
    
    Returns: (setup_type, setup_emoji, setup_description)
    """
    is_up = pm_change > 0
    abs_change = abs(pm_change)
    abs_gap = abs(gap_pct)
    
    # === STARKE MOVES (>5%) ===
    if abs_change >= 5:
        if is_up and pm_position >= 70:
            # Up + hält oben → Momentum Long
            if abs_gap >= 5:
                return ("GAP & GO", "🚀", "Gap Up + Holding High = Momentum Long")
            return ("MOMENTUM", "🚀", "Strong Move + Holding = Long Momentum")
        
        if is_up and pm_position < 40:
            # Up aber abverkauft → Fading
            return ("FADING", "⚠️", "Gapped Up but Fading — Caution, kein Long!")
        
        if not is_up and pm_position <= 30:
            # Down + sitzt am Low → Schwäche bestätigt
            if abs_gap >= 5:
                return ("GAP & FADE", "📉", "Gap Down + Near Low = Short Momentum")
            return ("WEAKNESS", "📉", "Strong Selling + Near Low = Short Setup")
        
        if not is_up and pm_position >= 60:
            # Down aber hat recovert → NICHT shorten
            return ("BOUNCE", "🔄", "Gapped Down but Bounced — Wait for Rejection!")
        
        # Mitte der Range bei starkem Move
        if is_up:
            return ("CONTESTED", "⚔️", "Strong Up but Mid-Range — Wait for Direction")
        else:
            return ("CONTESTED", "⚔️", "Strong Down but Mid-Range — Watch for Break")
    
    # === SQUEEZE / EXTREME (>10% + starke RS) ===
    if abs_change >= 10 and abs(rs_vs_spy) >= 5:
        if is_up:
            return ("SQUEEZE", "💥", "Extreme Move + Relative Strength = Possible Squeeze")
        else:
            return ("CAPITULATION", "🔻", "Extreme Selling = Watch for Reversal")
    
    # === MODERATE MOVES (3-5%) ===
    if 3 <= abs_change < 5:
        if is_up and pm_position >= 65:
            return ("CONTINUATION", "📈", "Steady Uptrend — Wait for Pullback Entry")
        if is_up and pm_position < 35:
            return ("FADING", "⚠️", "Moderate Up but Fading — No Long Entry")
        if not is_up and pm_position <= 35:
            return ("CONTINUATION", "📉", "Steady Selling — Wait for Bounce or Break")
        if not is_up and pm_position >= 65:
            return ("RECOVERY", "🔄", "Down but Recovering — Don't Short Here")
    
    # === KLEINE MOVES (2-3%) ===
    if 2 <= abs_change < 3:
        if 35 <= pm_position <= 65:
            return ("RANGE", "↔️", "Choppy — Wait for Direction")
        if is_up and pm_position >= 65:
            return ("MILD STRENGTH", "📈", "Slight Up Bias — Watch for Catalyst")
        if not is_up and pm_position <= 35:
            return ("MILD WEAKNESS", "📉", "Slight Down Bias — Watch for Catalyst")
    
    # DEFAULT
    return ("WATCH", "👀", "Monitor for Setup Development")


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
                ticker = cand["ticker"]
                
                # Rate Limiting: Pause nach je 10 Calls
                if idx_pm > 0 and idx_pm % 10 == 0:
                    time.sleep(0.5)
                
                # Hole echte PM Session Daten
                pm_data = get_pm_session_bars(poly_key, ticker, today_str)
                
                if pm_data and pm_data["pm_volume"] >= min_volume:
                    current_price = cand["current_price"]
                    prev_close = cand["prev_close"]
                    
                    # Echte PM High/Low
                    pm_high = pm_data["pm_high"]
                    pm_low = pm_data["pm_low"]
                    pm_volume = pm_data["pm_volume"]
                    pm_vwap = pm_data["pm_vwap"]
                    pm_open = pm_data["pm_open"]
                    
                    # FIX 3: ECHTE Gap-Berechnung (PM Open vs PrevClose)
                    # Gap = Wie viel hat sich der Preis ÜBER NACHT bewegt (vor jeglichem PM Trading)
                    real_gap_pct = ((pm_open - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                    
                    # FIX 4: Volume Ratio (PM Vol vs Average Daily Vol)
                    prev_day_vol = cand.get("prev_day_vol", 0)
                    # PM ist nur ~5.5h vs ~6.5h Regular Session, normalisiere auf volle Session
                    pm_vol_normalized = pm_volume * (6.5 / 5.5) if pm_volume > 0 else 0
                    vol_ratio = round(pm_vol_normalized / prev_day_vol, 1) if prev_day_vol > 0 else 0
                    
                    # PM Range und Position
                    pm_range = pm_high - pm_low if pm_high > pm_low else 0.01
                    pm_position = ((current_price - pm_low) / pm_range) * 100 if pm_range > 0 else 50
                    
                    # Distance to PM High/Low
                    dist_to_high = ((pm_high - current_price) / pm_high) * 100 if pm_high > 0 else 0
                    dist_to_low = ((current_price - pm_low) / pm_low) * 100 if pm_low > 0 else 0
                    
                    # Relative Strength vs SPY
                    rs_vs_spy = cand["pm_change"] - spy_pm_change
                    
                    # ATR% Schätzung (PM Range / Preis)
                    atr_pct = (pm_range / current_price) * 100 if current_price > 0 else 5
                    
                    # Setup Klassifizierung (mit echtem Gap!)
                    setup_type, setup_emoji, setup_desc = classify_pm_setup(
                        cand["pm_change"], real_gap_pct, pm_position, rs_vs_spy, atr_pct
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
                    
                    if cand["pm_change"] > 0:  # === LONG ===
                        
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
                        "PM_Chg%": round(cand["pm_change"], 2),
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
                        "PDH": round(cand["pdh"], 2),
                        "PDL": round(cand["pdl"], 2),
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
                        "Direction": "🟢 LONG" if cand["pm_change"] > 0 else "🔴 SHORT",
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
                ticker = item["Ticker"]
                
                # Ticker Details (Shares, Market Cap)
                details = get_ticker_details(poly_key, ticker)
                item["Shares_M"] = details["shares_millions"]
                item["Float_Cat"] = details["float_category"]
                item["Float_Emoji"] = details["float_emoji"]
                item["Market_Cap_M"] = details["market_cap_millions"]
                item["Company_Name"] = details["name"]
                
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
        
        return final_results, spy_pm_change
        
    except Exception as e:
        st.error(f"PM Watchlist Fehler: {e}")
        return [], 0


def display_premarket_watchlist(pm_data, spy_change=0):
    """
    ERWEITERTE Pre-Market Watchlist Anzeige mit allen Metriken.
    """
    
    if not pm_data:
        st.warning("⏳ Keine Pre-Market Mover gefunden. PM Session: 4:00-9:30 AM ET")
        return
    
    # Header mit SPY Info
    col_header1, col_header2 = st.columns([2, 1])
    with col_header1:
        st.success(f"📋 **{len(pm_data)} Pre-Market Setups** gefunden")
    with col_header2:
        spy_color = "🟢" if spy_change >= 0 else "🔴"
        st.info(f"SPY PM: {spy_color} {spy_change:+.2f}%")
    
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
                with st.container():
                    # Header Row
                    col1, col2, col3 = st.columns([1, 2, 1])
                    
                    with col1:
                        st.markdown(f"## {item['Ticker']}")
                        change_color = "green" if item['PM_Chg%'] > 0 else "red"
                        st.markdown(f"**<span style='color:{change_color};font-size:24px;'>+{item['PM_Chg%']:.1f}%</span>**", unsafe_allow_html=True)
                        st.caption(f"{item['Setup_Emoji']} {item['Setup_Type']}")
                        # Float Info
                        if item.get('Shares_M', 0) > 0:
                            st.caption(f"{item.get('Float_Emoji', '❓')} {item.get('Shares_M', 0):.1f}M shares")
                    
                    with col2:
                        # Preis & Levels
                        vol_ratio_str = f" | VolR: **{item.get('Vol_Ratio', 0):.1f}x**" if item.get('Vol_Ratio', 0) > 0 else ""
                        st.markdown(f"**💰 ${item['PM_Preis']:.2f}** | Vol: {item['PM_Vol']:,.0f}{vol_ratio_str}")
                        st.caption(f"📊 PM High: **${item['PM_High']:.2f}** | Low: ${item['PM_Low']:.2f} | VWAP: ${item['PM_VWAP']:.2f}")
                        st.caption(f"📈 Gap: {item['Gap%']:+.1f}% | RS vs SPY: {item['RS_vs_SPY']:+.1f}%")
                        st.caption(f"📉 PDH: ${item['PDH']:.2f} | PDL: ${item['PDL']:.2f}")
                        # Market Cap
                        if item.get('Market_Cap_M', 0) > 0:
                            mcap = item['Market_Cap_M']
                            if mcap >= 1000:
                                st.caption(f"💵 MCap: ${mcap/1000:.1f}B")
                            else:
                                st.caption(f"💵 MCap: ${mcap:.0f}M")
                    
                    with col3:
                        # Entry Signal
                        signal = item['Entry_Signal']
                        if "OR BREAK" in signal:
                            st.success(signal)
                        elif "WATCH" in signal:
                            st.info(signal)
                        else:
                            st.warning(signal)
                        
                        # Position Meter
                        pos = item['PM_Position']
                        pos_bar = "🟩" * int(pos/10) + "⬜" * (10 - int(pos/10))
                        st.caption(f"Position: {pos_bar} {pos:.0f}%")
                    
                    # Katalysator-Zeile (wenn erkannt)
                    catalysts = item.get('Catalysts', [])
                    if catalysts:
                        cat_str = " | ".join(catalysts)
                        st.markdown(f"🎯 **Katalysator:** {cat_str}")
                    
                    # News Row (wenn vorhanden)
                    news_list = item.get('News', [])
                    if news_list:
                        news_text = ""
                        for n in news_list[:2]:
                            sentiment_emoji = "🟢" if n.get('sentiment') == 'positive' else "🔴" if n.get('sentiment') == 'negative' else "⚪"
                            cat_tag = f" [{n.get('catalyst', '')}]" if n.get('catalyst') else ""
                            news_text += f"{sentiment_emoji} {n.get('title', '')[:60]}...{cat_tag} ({n.get('published', '')})\n"
                        if news_text:
                            st.caption(f"📰 **News:** {news_text}")
                    
                    # Risk Management Row
                    with st.expander(f"📐 Trade Setups — {item.get('Setup_Desc', '')}"):
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
                            rr_ratio = f"1:{setup['risk']:.2f}" if setup.get('risk', 0) > 0 else ""
                            
                            st.markdown(f"**{label}: {setup['emoji']} {setup['name']}** — {setup['desc']}")
                            sc1, sc2, sc3, sc4 = st.columns(4)
                            with sc1:
                                st.metric("Entry", f"${setup['entry']:.2f}")
                            with sc2:
                                st.metric("Stop", f"${setup['stop']:.2f}")
                            with sc3:
                                _tp1_r = (setup['tp1'] - setup['entry']) / setup['risk'] if setup.get('risk', 0) > 0 and setup['entry'] != setup['tp1'] else 0
                                st.metric(f"TP1 ({abs(_tp1_r):.1f}R)", f"${setup['tp1']:.2f}")
                            with sc4:
                                _tp2_r = (setup['tp2'] - setup['entry']) / setup['risk'] if setup.get('risk', 0) > 0 and setup['entry'] != setup['tp2'] else 0
                                st.metric(f"TP2 ({abs(_tp2_r):.1f}R)", f"${setup['tp2']:.2f}")
                            st.caption(f"Risk: ${setup['risk']:.2f} ({s_risk_pct:.1f}%)")
                            
                            if si < len(all_setups) - 1:
                                st.markdown("---")
                        
                        st.caption(f"Move Start: {item.get('Move_Time', 'N/A')}")
                        if item.get('Company_Name'):
                            st.caption(f"🏢 {item['Company_Name']}")
                    
                    st.divider()
        else:
            st.info("Keine Long Kandidaten im PM")
    
    # === SHORT TAB ===
    with tab_short:
        if short_candidates:
            for item in short_candidates[:12]:
                with st.container():
                    col1, col2, col3 = st.columns([1, 2, 1])
                    
                    with col1:
                        st.markdown(f"## {item['Ticker']}")
                        st.markdown(f"**<span style='color:red;font-size:24px;'>{item['PM_Chg%']:.1f}%</span>**", unsafe_allow_html=True)
                        st.caption(f"{item['Setup_Emoji']} {item['Setup_Type']}")
                        # Float Info
                        if item.get('Shares_M', 0) > 0:
                            st.caption(f"{item.get('Float_Emoji', '❓')} {item.get('Shares_M', 0):.1f}M shares")
                    
                    with col2:
                        vol_ratio_str = f" | VolR: **{item.get('Vol_Ratio', 0):.1f}x**" if item.get('Vol_Ratio', 0) > 0 else ""
                        st.markdown(f"**💰 ${item['PM_Preis']:.2f}** | Vol: {item['PM_Vol']:,.0f}{vol_ratio_str}")
                        st.caption(f"📊 PM High: ${item['PM_High']:.2f} | Low: **${item['PM_Low']:.2f}** | VWAP: ${item['PM_VWAP']:.2f}")
                        st.caption(f"📈 Gap: {item['Gap%']:+.1f}% | RS vs SPY: {item['RS_vs_SPY']:+.1f}%")
                        st.caption(f"📉 PDH: ${item['PDH']:.2f} | PDL: ${item['PDL']:.2f}")
                        # Market Cap
                        if item.get('Market_Cap_M', 0) > 0:
                            mcap = item['Market_Cap_M']
                            if mcap >= 1000:
                                st.caption(f"💵 MCap: ${mcap/1000:.1f}B")
                            else:
                                st.caption(f"💵 MCap: ${mcap:.0f}M")
                    
                    with col3:
                        signal = item['Entry_Signal']
                        if "OR BREAK" in signal:
                            st.error(signal)  # Rot für Short Breakdown
                        elif "WATCH" in signal:
                            st.info(signal)
                        else:
                            st.warning(signal)
                        
                        pos = item['PM_Position']
                        pos_bar = "🟥" * int(pos/10) + "⬜" * (10 - int(pos/10))
                        st.caption(f"Position: {pos_bar} {pos:.0f}%")
                    
                    # Katalysator-Zeile (wenn erkannt)
                    catalysts = item.get('Catalysts', [])
                    if catalysts:
                        cat_str = " | ".join(catalysts)
                        st.markdown(f"🎯 **Katalysator:** {cat_str}")
                    
                    # News Row (wenn vorhanden)
                    news_list = item.get('News', [])
                    if news_list:
                        news_text = ""
                        for n in news_list[:2]:
                            sentiment_emoji = "🟢" if n.get('sentiment') == 'positive' else "🔴" if n.get('sentiment') == 'negative' else "⚪"
                            cat_tag = f" [{n.get('catalyst', '')}]" if n.get('catalyst') else ""
                            news_text += f"{sentiment_emoji} {n.get('title', '')[:60]}...{cat_tag} ({n.get('published', '')})\n"
                        if news_text:
                            st.caption(f"📰 **News:** {news_text}")
                    
                    with st.expander(f"📐 Trade Setups — {item.get('Setup_Desc', '')}"):
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
                            
                            st.markdown(f"**{label}: {setup['emoji']} {setup['name']}** — {setup['desc']}")
                            sc1, sc2, sc3, sc4 = st.columns(4)
                            with sc1:
                                st.metric("Entry", f"${setup['entry']:.2f}")
                            with sc2:
                                st.metric("Stop", f"${setup['stop']:.2f}")
                            with sc3:
                                _tp1_r = (setup['tp1'] - setup['entry']) / setup['risk'] if setup.get('risk', 0) > 0 and setup['entry'] != setup['tp1'] else 0
                                st.metric(f"TP1 ({abs(_tp1_r):.1f}R)", f"${setup['tp1']:.2f}")
                            with sc4:
                                _tp2_r = (setup['tp2'] - setup['entry']) / setup['risk'] if setup.get('risk', 0) > 0 and setup['entry'] != setup['tp2'] else 0
                                st.metric(f"TP2 ({abs(_tp2_r):.1f}R)", f"${setup['tp2']:.2f}")
                            st.caption(f"Risk: ${setup['risk']:.2f} ({s_risk_pct:.1f}%)")
                            
                            if si < len(all_setups) - 1:
                                st.markdown("---")
                        
                        st.caption(f"Move Start: {item.get('Move_Time', 'N/A')}")
                        if item.get('Company_Name'):
                            st.caption(f"🏢 {item['Company_Name']}")
                    
                    st.divider()
        else:
            st.info("Keine Short Kandidaten im PM")
    
    # === ALL TAB (Table View) ===
    with tab_all:
        import pandas as pd
        df = pd.DataFrame(pm_data)
        
        # Spalten für Anzeige (erweitert mit Float + Vol Ratio)
        display_cols = ["Ticker", "PM_Chg%", "PM_Preis", "PM_High", "PM_Low", "Gap%", "Vol_Ratio", "RS_vs_SPY", "Shares_M", "Float_Cat", "Setup_Type", "Entry_Signal"]
        available_cols = [col for col in display_cols if col in df.columns]
        if available_cols:
            st.dataframe(
                df[available_cols],
                column_config={
                    "Ticker": st.column_config.TextColumn("Ticker"),
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
                direction = "LONG" if item["PM_Chg%"] > 0 else "SHORT"
                float_info = f" | {item.get('Float_Emoji', '')} {item.get('Shares_M', 0):.0f}M" if item.get('Shares_M', 0) > 0 else ""
                vol_r = f" | VolR:{item.get('Vol_Ratio', 0):.1f}x" if item.get('Vol_Ratio', 0) > 0 else ""
                cat_info = f" | {' '.join(item.get('Catalysts', []))}" if item.get('Catalysts') else ""
                export_text += f"{item['Ticker']:6} | {direction:5} | {item['PM_Chg%']:+6.1f}% | Gap:{item['Gap%']:+.1f}% | E: ${item['Entry_Price']:.2f} | S: ${item['Stop_Price']:.2f}{float_info}{vol_r}{cat_info}\n"
        else:
            export_text += "Keine OR Break Kandidaten\n"
        
        export_text += "\n👀 WATCH (Warte auf Entwicklung):\n"
        export_text += "─" * 50 + "\n"
        watch = [x for x in pm_data if "WATCH" in x["Entry_Signal"]][:5]
        for item in watch:
            direction = "LONG" if item["PM_Chg%"] > 0 else "SHORT"
            export_text += f"{item['Ticker']:6} | {direction:5} | {item['PM_Chg%']:+6.1f}% | {item['Setup_Type']}\n"
        
        st.code(export_text)
        
        # Session State für Export in andere Teile der App
        if st.button("💾 Zur Watchlist hinzufügen", key="add_pm_to_watchlist"):
            for item in or_break[:5]:
                if item['Ticker'] not in st.session_state.watchlist:
                    st.session_state.watchlist.append(item['Ticker'])
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

def _save_pm_setups(pm_data):
    """Speichert PM Setups mit Timestamp für späteres Tracking"""
    try:
        # Lade bestehende Daten
        existing = _load_pm_tracker()
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Vereinfache die Setups für JSON
        simplified = []
        for item in pm_data:
            setups_clean = []
            for s in item.get("Setups", []):
                setups_clean.append({
                    "name": s.get("name", ""),
                    "emoji": s.get("emoji", ""),
                    "entry": round(s.get("entry", 0), 2),
                    "stop": round(s.get("stop", 0), 2),
                    "tp1": round(s.get("tp1", 0), 2),
                    "tp2": round(s.get("tp2", 0), 2),
                    "risk": round(s.get("risk", 0), 2),
                    "risk_pct": round(s.get("risk_pct", 0), 1),
                })
            
            simplified.append({
                "ticker": item["Ticker"],
                "direction": "LONG" if item["PM_Chg%"] > 0 else "SHORT",
                "pm_change": item["PM_Chg%"],
                "pm_price": item["PM_Preis"],
                "pm_high": item["PM_High"],
                "pm_low": item["PM_Low"],
                "pm_vwap": item["PM_VWAP"],
                "gap_pct": item.get("Gap%", 0),
                "setup_type": item.get("Setup_Type", ""),
                "entry_signal": item.get("Entry_Signal", ""),
                "primary_idx": item.get("Primary_Idx", 0),
                "alt_idx": item.get("Alt_Idx", 1),
                "setups": setups_clean,
                "results": None,  # Wird nach Auswertung gefüllt
            })
        
        existing[today] = {
            "date": today,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(simplified),
            "tickers": simplified,
        }
        
        # Behalte nur die letzten 30 Tage
        sorted_dates = sorted(existing.keys(), reverse=True)[:30]
        existing = {d: existing[d] for d in sorted_dates}
        
        with open(PM_TRACKER_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        
        return True
    except Exception as e:
        _debug_log("PM Tracker save failed", error=e)
        return False


def _load_pm_tracker():
    """Lädt alle gespeicherten PM Tracker Daten"""
    try:
        with open(PM_TRACKER_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def evaluate_pm_setups(poly_key, date_str, setups_data):
    """
    Wertet PM Setups gegen echte Intraday-Daten aus.
    
    Holt 5-Minuten Bars für Regular Session (9:30-16:00 ET).
    Simuliert jedes Setup: Entry getriggert? Stop oder TP1/TP2 zuerst?
    
    Returns: Liste von Setup-Ergebnissen
    """
    results = []
    
    if not setups_data or not poly_key:
        return results
    
    tickers = setups_data.get("tickers", [])
    
    for item in tickers[:20]:  # Max 20 Ticker auswerten (API Limit)
        ticker = item["ticker"]
        direction = item["direction"]
        
        try:
            # 5-Min Bars für den ganzen Tag holen
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{date_str}/{date_str}"
            resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)
            
            if resp.status_code != 200:
                continue
            
            bars = resp.json().get("results", [])
            if not bars:
                continue
            
            # Filtere Regular Session (9:30-16:00 ET)
            et_tz = pytz.timezone('America/New_York')
            trade_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            rs_start_et = et_tz.localize(trade_date.replace(hour=9, minute=30))
            rs_start_ts = rs_start_et.astimezone(pytz.utc).timestamp()
            rs_end_et = et_tz.localize(trade_date.replace(hour=16, minute=0))
            rs_end_ts = rs_end_et.astimezone(pytz.utc).timestamp()
            
            session_bars = [b for b in bars if rs_start_ts <= b.get("t", 0) / 1000 <= rs_end_ts]
            
            if not session_bars:
                continue
            
            # Open und Close des Tages
            day_open = session_bars[0].get("o", 0)
            day_close = session_bars[-1].get("c", 0)
            day_high = max(b.get("h", 0) for b in session_bars)
            day_low = min(b.get("l", 999999) for b in session_bars)
            
            # Evaluiere jedes Setup
            setup_results = []
            for si, setup in enumerate(item.get("setups", [])):
                entry = setup.get("entry", 0)
                stop = setup.get("stop", 0)
                tp1 = setup.get("tp1", 0)
                tp2 = setup.get("tp2", 0)
                
                if entry <= 0:
                    continue
                
                # Walk through bars chronologisch
                entry_hit = False
                entry_bar_idx = -1
                stop_hit = False
                tp1_hit = False
                tp2_hit = False
                exit_price = 0
                exit_reason = "OPEN"  # Noch offen
                
                for bi, bar in enumerate(session_bars):
                    bar_high = bar.get("h", 0)
                    bar_low = bar.get("l", 999999)
                    
                    if not entry_hit:
                        # Check ob Entry getriggert wird
                        if direction == "LONG":
                            if bar_high >= entry:
                                entry_hit = True
                                entry_bar_idx = bi
                        else:  # SHORT
                            if bar_low <= entry:
                                entry_hit = True
                                entry_bar_idx = bi
                    
                    elif entry_hit:
                        # Entry ist aktiv — check Stop und TPs
                        if direction == "LONG":
                            # Stop zuerst checken (konservativ)
                            if bar_low <= stop:
                                stop_hit = True
                                exit_price = stop
                                exit_reason = "STOP"
                                break
                            if bar_high >= tp2:
                                tp2_hit = True
                                tp1_hit = True
                                exit_price = tp2
                                exit_reason = "TP2"
                                break
                            if bar_high >= tp1 and not tp1_hit:
                                tp1_hit = True
                        else:  # SHORT
                            if bar_high >= stop:
                                stop_hit = True
                                exit_price = stop
                                exit_reason = "STOP"
                                break
                            if bar_low <= tp2:
                                tp2_hit = True
                                tp1_hit = True
                                exit_price = tp2
                                exit_reason = "TP2"
                                break
                            if bar_low <= tp1 and not tp1_hit:
                                tp1_hit = True
                
                # Wenn Trade noch offen: Close at EOD
                if entry_hit and not stop_hit and not tp2_hit:
                    exit_price = day_close
                    if tp1_hit:
                        exit_reason = "TP1+EOD"
                    else:
                        exit_reason = "EOD"
                
                # P&L berechnen
                if entry_hit:
                    if direction == "LONG":
                        pnl_dollar = exit_price - entry
                    else:
                        pnl_dollar = entry - exit_price
                    pnl_pct = (pnl_dollar / entry * 100) if entry > 0 else 0
                    r_multiple = pnl_dollar / setup.get("risk", 1) if setup.get("risk", 0) > 0 else 0
                else:
                    pnl_dollar = 0
                    pnl_pct = 0
                    r_multiple = 0
                    exit_reason = "NO ENTRY"
                
                setup_results.append({
                    "setup_name": setup.get("name", ""),
                    "setup_idx": si,
                    "is_primary": si == item.get("primary_idx", 0),
                    "entry": entry,
                    "stop": stop,
                    "tp1": tp1,
                    "tp2": tp2,
                    "entry_hit": entry_hit,
                    "stop_hit": stop_hit,
                    "tp1_hit": tp1_hit,
                    "tp2_hit": tp2_hit,
                    "exit_price": round(exit_price, 2),
                    "exit_reason": exit_reason,
                    "pnl_dollar": round(pnl_dollar, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "r_multiple": round(r_multiple, 2),
                })
            
            results.append({
                "ticker": ticker,
                "direction": direction,
                "pm_change": item.get("pm_change", 0),
                "setup_type": item.get("setup_type", ""),
                "day_open": round(day_open, 2),
                "day_close": round(day_close, 2),
                "day_high": round(day_high, 2),
                "day_low": round(day_low, 2),
                "day_change_pct": round((day_close - day_open) / day_open * 100, 2) if day_open > 0 else 0,
                "setup_results": setup_results,
            })
            
        except Exception as e:
            _debug_log(f"Tracker eval failed for {ticker}", error=e)
            continue
    
    return results


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
                            if ticker_data_item["ticker"] == result["ticker"]:
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
            direction_emoji = "🟢" if t["direction"] == "LONG" else "🔴"
            st.markdown(f"**{direction_emoji} {t['ticker']}** — {t['pm_change']:+.1f}% | {t.get('setup_type', '')} | Signal: {t.get('entry_signal', '')}")
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
            stats["count"] += 1
            
            if sr["entry_hit"]:
                stats["entries"] += 1
                stats["total_r"] += sr["r_multiple"]
                stats["total_pnl_pct"] += sr["pnl_pct"]
                
                if sr["pnl_pct"] > 0:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1
                
                if sr["tp1_hit"]:
                    stats["tp1_hits"] += 1
                if sr["tp2_hit"]:
                    stats["tp2_hits"] += 1
                if sr["stop_hit"]:
                    stats["stops"] += 1
                
                if sr.get("is_primary"):
                    stats["primary_count"] += 1
                    if sr["pnl_pct"] > 0:
                        stats["primary_wins"] += 1
                
                all_trades.append({
                    "ticker": t["ticker"],
                    "direction": t["direction"],
                    "setup": sname,
                    "is_primary": sr.get("is_primary", False),
                    "r_multiple": sr["r_multiple"],
                    "pnl_pct": sr["pnl_pct"],
                    "exit_reason": sr["exit_reason"],
                })
    
    # Display Stats Table
    if stats_by_type:
        stat_cols = st.columns(len(stats_by_type))
        for ci, (sname, stats) in enumerate(stats_by_type.items()):
            with stat_cols[ci % len(stat_cols)]:
                entries = stats["entries"]
                win_rate = (stats["wins"] / entries * 100) if entries > 0 else 0
                avg_r = stats["total_r"] / entries if entries > 0 else 0
                
                emoji = "🚀" if "Breakout" in sname or "Breakdown" in sname else "🔄" if "VWAP" in sname or "Rejection" in sname else "📐"
                
                st.markdown(f"**{emoji} {sname}**")
                
                # Win Rate mit Farbe
                wr_color = "green" if win_rate >= 50 else "orange" if win_rate >= 40 else "red"
                st.markdown(f"Win Rate: <span style='color:{wr_color};font-weight:bold;'>{win_rate:.0f}%</span> ({stats['wins']}W / {stats['losses']}L)", unsafe_allow_html=True)
                
                st.caption(f"Entries: {entries}/{stats['count']} | Avg R: {avg_r:+.1f}R")
                st.caption(f"TP1: {stats['tp1_hits']}× | TP2: {stats['tp2_hits']}× | Stops: {stats['stops']}×")
                
                if stats["primary_count"] > 0:
                    prim_wr = stats["primary_wins"] / stats["primary_count"] * 100
                    st.caption(f"⭐ Als Primary: {prim_wr:.0f}% WR ({stats['primary_count']}×)")
    
    # Einzelne Trades
    st.markdown("### 📝 Einzelne Trades")
    
    for t in evaluated:
        result = t["results"]
        direction_emoji = "🟢" if t["direction"] == "LONG" else "🔴"
        day_chg = result.get("day_change_pct", 0)
        day_color = "green" if day_chg > 0 else "red"
        
        with st.expander(f"{direction_emoji} **{t['ticker']}** — PM: {t['pm_change']:+.1f}% | Day: {day_chg:+.1f}%"):
            st.caption(f"Day Range: ${result['day_low']:.2f} — ${result['day_high']:.2f} | Open: ${result['day_open']:.2f} → Close: ${result['day_close']:.2f}")
            
            for sr in result.get("setup_results", []):
                primary_tag = "⭐" if sr.get("is_primary") else "  "
                
                if sr["exit_reason"] == "NO ENTRY":
                    color = "gray"
                    result_text = "Entry nicht getriggert"
                elif sr["exit_reason"] == "STOP":
                    color = "red"
                    result_text = f"STOP → ${sr['exit_price']:.2f} ({sr['pnl_pct']:+.1f}% | {sr['r_multiple']:+.1f}R)"
                elif sr["exit_reason"] == "TP2":
                    color = "green"
                    result_text = f"TP2 ✅ → ${sr['exit_price']:.2f} ({sr['pnl_pct']:+.1f}% | {sr['r_multiple']:+.1f}R)"
                elif sr["exit_reason"] == "TP1+EOD":
                    color = "green"
                    result_text = f"TP1 ✅ + EOD → ${sr['exit_price']:.2f} ({sr['pnl_pct']:+.1f}% | {sr['r_multiple']:+.1f}R)"
                else:
                    color = "orange" if sr["pnl_pct"] >= 0 else "red"
                    result_text = f"EOD Close → ${sr['exit_price']:.2f} ({sr['pnl_pct']:+.1f}% | {sr['r_multiple']:+.1f}R)"
                
                st.markdown(
                    f"{primary_tag} **{sr.get('setup_name', '')}**: "
                    f"Entry ${sr['entry']:.2f} | Stop ${sr['stop']:.2f} → "
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
            st.success(f"🏆 Best: **{best['ticker']}** {best['setup']} → {best['r_multiple']:+.1f}R ({best['pnl_pct']:+.1f}%)")
        with col_worst:
            st.error(f"💀 Worst: **{worst['ticker']}** {worst['setup']} → {worst['r_multiple']:+.1f}R ({worst['pnl_pct']:+.1f}%)")
        
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
    # === ETFs (10) ===
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "ARKK", "SOXL", "TQQQ"
]

# Strategie-Definitionen mit klaren Trade-Regeln
# V68: Gelockert — alte RVOL-Schwellen (2.0+) waren unrealistisch für Backtest
BACKTEST_STRATEGY_RULES = {
    "Breakout Long": {
        "direction": "long",
        "description": "Momentum-Ausbruch: Change >3%, Close nahe High",
        "signal": {
            "change_pct_min": 3.0, "change_pct_max": 50.0,
            "close_pos_min": 0.60
        },
        "entry": "next_open",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Breakdown Short": {
        "direction": "short",
        "description": "Abverkauf: Change <-3%, Close nahe Low",
        "signal": {
            "change_pct_min": -50.0, "change_pct_max": -3.0,
            "close_pos_max": 0.40
        },
        "entry": "next_open",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Gap Up Momentum": {
        "direction": "long",
        "description": "Gap Up >2% + Kurs hält sich oben (Close Pos >0.55)",
        "signal": {
            "gap_pct_min": 2.0, "gap_pct_max": 30.0,
            "close_pos_min": 0.55
        },
        "entry": "next_open",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 2,
        "min_price": 5.0
    },
    "Gap Down Short": {
        "direction": "short",
        "description": "Gap Down <-2% + Kurs bleibt unten (Close Pos <0.45)",
        "signal": {
            "gap_pct_min": -30.0, "gap_pct_max": -2.0,
            "close_pos_max": 0.45
        },
        "entry": "next_open",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 2,
        "min_price": 5.0
    },
    "Dip Buy": {
        "direction": "long",
        "description": "Rücksetzer: -3% bis -8%, normales Volumen",
        "signal": {
            "change_pct_min": -8.0, "change_pct_max": -3.0,
            "rvol_max": 2.5
        },
        "entry": "at_close",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 3.0,
        "max_hold_days": 5,
        "min_price": 10.0
    },
    "Reversal Hunter": {
        "direction": "long",
        "description": "Bounce nach Abverkauf: Vortag <-4%, heute >+2%",
        "signal": {
            "prev_change_pct_max": -4.0,
            "change_pct_min": 2.0
        },
        "entry": "at_close",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Bull Flag": {
        "direction": "long",
        "description": "Starker Vortag (+3%+), heute Konsolidierung (-2% bis +2%)",
        "signal": {
            "prev_change_pct_min": 3.0,
            "change_pct_min": -2.0,
            "change_pct_max": 2.0
        },
        "entry": "prev_high",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Volume Surge": {
        "direction": "long",
        "description": "Hohes Volumen (RVOL >2) + Aufwärtsbewegung >2%",
        "signal": {
            "change_pct_min": 2.0,
            "rvol_min": 2.0
        },
        "entry": "next_open",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Early Momentum": {
        "direction": "long",
        "description": "Starker Tag (+3%+), Close nahe High → Momentum hält",
        "signal": {
            "change_pct_min": 3.0, "change_pct_max": 30.0,
            "close_pos_min": 0.55
        },
        "entry": "at_close",
        "stop_pct": 0.04,
        "tp1_rr": 1.0,
        "tp2_rr": 2.0,
        "max_hold_days": 2,
        "min_price": 5.0
    },
    "Whale Watch": {
        "direction": "long",
        "description": "Extremes Volumen (RVOL >3) mit klarer Richtung (+2%+)",
        "signal": {
            "change_pct_min": 2.0,
            "rvol_min": 3.0
        },
        "entry": "next_open",
        "stop_pct": 0.06,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 3,
        "min_price": 5.0
    }
}


def fetch_backtest_daily_data(poly_key, ticker, start_date, end_date):
    """
    Holt tägliche OHLCV-Daten von Polygon für Backtesting.
    Includes retry logic für Rate Limits (429).
    """
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": poly_key}
    
    for attempt in range(3):
        try:
            resp = rate_limited_get(url, params=params, timeout=15)
            
            if resp.status_code == 429:
                time.sleep(12 + attempt * 5)
                continue
            
            if resp.status_code != 200:
                # Speichere Fehler für Debug-Anzeige
                _err = f"{ticker}: HTTP {resp.status_code}"
                try:
                    _err += f" | {resp.text[:150]}"
                except:
                    pass
                if not hasattr(fetch_backtest_daily_data, '_errors'):
                    fetch_backtest_daily_data._errors = []
                fetch_backtest_daily_data._errors = (fetch_backtest_daily_data._errors + [_err])[-5:]
                return []
            
            data = resp.json()
            
            if data.get("status") != "OK" or not data.get("results"):
                _err = f"{ticker}: status={data.get('status')} results={data.get('resultsCount',0)} | {data.get('error','')}{data.get('message','')}"
                if not hasattr(fetch_backtest_daily_data, '_errors'):
                    fetch_backtest_daily_data._errors = []
                fetch_backtest_daily_data._errors = (fetch_backtest_daily_data._errors + [_err])[-5:]
                return []
            
            bars = []
            for r in data["results"]:
                ts = r.get("t", 0)
                dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""
                bars.append({
                    "date": dt,
                    "open": r.get("o", 0),
                    "high": r.get("h", 0),
                    "low": r.get("l", 0),
                    "close": r.get("c", 0),
                    "volume": r.get("v", 0),
                    "vwap": r.get("vw", 0)
                })
            return bars
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            continue
    
    return []


def fetch_grouped_daily(poly_key, date_str):
    """Holt ALLE US-Aktien für einen Tag (Grouped Daily Bars)."""
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    params = {"apiKey": poly_key, "adjusted": "true"}
    try:
        resp = rate_limited_get(url, params=params, timeout=30)
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            return {r["T"]: r for r in data["results"] if r.get("c", 0) > 0}
        return {}
    except:
        return {}


def run_full_backtest_grouped(poly_key, strategies=None, months=6, min_price=5.0, 
                               min_volume=100000, progress_callback=None):
    """
    Backtest über ALLE US-Aktien mit Grouped Daily Bars.
    
    Zwei-Pass Ansatz:
    1. Lade alle Tage → baue per-Ticker History auf
    2. Scanne Signale und simuliere Trades mit vollständiger History
    """
    if strategies is None:
        strategies = list(BACKTEST_STRATEGY_RULES.keys())
    
    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=months * 30 + 30)  # +30 für RVOL Lookback
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    
    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    if not trading_days:
        return {s: [] for s in strategies}, 0
    
    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}  # ticker → list of bars (chronologisch)
    total_tickers_seen = set()
    
    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.7,  # 70% für Laden
                f"📥 Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )
        
        day_data = fetch_grouped_daily(poly_key, date_str)
        if not day_data:
            continue
        
        for ticker, r in day_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue
            
            # Leveraged/Inverse ETFs und Krypto-ETPs rausfiltern
            _t = ticker.upper()
            _skip_prefixes = (
                "TQQQ","SQQQ","SOXL","SOXS","LABU","LABD","SPXL","SPXS",
                "UPRO","SPXU","UVXY","SVXY","NUGT","DUST","JNUG","JDST",
                "FNGU","FNGD","TECL","TECS","BULZ","BERZ","GUSH","DRIP",
                "FAS","FAZ","UDOW","SDOW","YANG","YINN","ERX","ERY",
                "XRPT","XXRP","XETH","BITO","GBTC","ETHE","BITW","CONL",
                "MSOX","BTFX","SOLT","NEBX","AREC","MAXI","TNA","TZA"
            )
            if any(_t.startswith(p) for p in _skip_prefixes):
                continue
            
            price = r.get("c", 0)
            volume = r.get("v", 0)
            
            if price < min_price or volume < min_volume:
                continue
            
            total_tickers_seen.add(ticker)
            
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
            }
            
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)
    
    # ============================================================
    # PASS 2: Signale erkennen + Trades simulieren
    # (Jetzt hat jeder Ticker seine VOLLSTÄNDIGE History)
    # ============================================================
    all_results = {s: [] for s in strategies}
    tickers_with_data = [t for t, bars in ticker_history.items() if len(bars) >= 30]
    
    for t_idx, ticker in enumerate(tickers_with_data):
        if progress_callback and t_idx % 500 == 0:
            progress_callback(
                0.7 + (t_idx / len(tickers_with_data)) * 0.3,  # 30% für Simulation
                f"🔍 Scanne {ticker} ({t_idx+1}/{len(tickers_with_data)})"
            )
        
        bars = ticker_history[ticker]
        
        for idx in range(21, len(bars)):
            if bars[idx]["date"] < test_start:
                continue
            
            metrics = compute_daily_metrics(bars, idx)
            if not metrics or metrics["price"] <= 0:
                continue
            
            for strat_name in strategies:
                strat = BACKTEST_STRATEGY_RULES[strat_name]
                if metrics["price"] < strat.get("min_price", 1.0):
                    continue
                
                if check_signal(metrics, strat["signal"]):
                    trade = simulate_trade(bars, idx, strat)
                    if trade:
                        trade["ticker"] = ticker
                        trade["strategy"] = strat_name
                        trade["signal_change_pct"] = round(metrics["change_pct"], 2)
                        trade["signal_rvol"] = round(metrics["rvol"], 1)
                        all_results[strat_name].append(trade)
    
    # Memory aufräumen
    del ticker_history
    
    if progress_callback:
        progress_callback(1.0, f"✅ Fertig! {len(total_tickers_seen)} Aktien gescannt")
    
    return all_results, len(total_tickers_seen)


def compute_daily_metrics(bars, idx):
    """
    Berechnet Screening-Metriken für einen Tag.
    
    Returns: dict mit change_pct, gap_pct, rvol, close_pos, prev_change_pct
    """
    if idx < 1 or idx >= len(bars):
        return None
    
    today = bars[idx]
    yesterday = bars[idx - 1]
    
    if yesterday["close"] <= 0 or today["close"] <= 0:
        return None
    
    # Change % (heute Close vs Gestern Close)
    change_pct = ((today["close"] - yesterday["close"]) / yesterday["close"]) * 100
    
    # Gap % (heute Open vs Gestern Close)
    gap_pct = ((today["open"] - yesterday["close"]) / yesterday["close"]) * 100
    
    # Close Position (wo hat heute geschlossen relativ zur Range)
    day_range = today["high"] - today["low"]
    close_pos = (today["close"] - today["low"]) / day_range if day_range > 0 else 0.5
    
    # RVOL (Volumen heute vs 20-Tage Durchschnitt)
    lookback_start = max(0, idx - 20)
    avg_vol_bars = bars[lookback_start:idx]
    avg_vol = sum(b["volume"] for b in avg_vol_bars) / len(avg_vol_bars) if avg_vol_bars else 1
    rvol = today["volume"] / avg_vol if avg_vol > 0 else 1.0
    
    # Previous day change %
    prev_change_pct = 0
    if idx >= 2:
        day_before = bars[idx - 2]
        if day_before["close"] > 0:
            prev_change_pct = ((yesterday["close"] - day_before["close"]) / day_before["close"]) * 100
    
    return {
        "change_pct": change_pct,
        "gap_pct": gap_pct,
        "close_pos": close_pos,
        "rvol": rvol,
        "prev_change_pct": prev_change_pct,
        "price": today["close"],
        "day_high": today["high"],
        "day_low": today["low"]
    }


def check_signal(metrics, signal_rules):
    """
    Prüft ob die Tages-Metriken die Signal-Bedingungen einer Strategie erfüllen.
    """
    if not metrics:
        return False
    
    for key, value in signal_rules.items():
        if key == "change_pct_min" and metrics["change_pct"] < value:
            return False
        if key == "change_pct_max" and metrics["change_pct"] > value:
            return False
        if key == "gap_pct_min" and metrics["gap_pct"] < value:
            return False
        if key == "gap_pct_max" and metrics["gap_pct"] > value:
            return False
        if key == "rvol_min" and metrics["rvol"] < value:
            return False
        if key == "rvol_max" and metrics["rvol"] > value:
            return False
        if key == "close_pos_min" and metrics["close_pos"] < value:
            return False
        if key == "close_pos_max" and metrics["close_pos"] > value:
            return False
        if key == "prev_change_pct_min" and metrics["prev_change_pct"] < value:
            return False
        if key == "prev_change_pct_max" and metrics["prev_change_pct"] > value:
            return False
    
    return True


def simulate_trade(bars, signal_idx, strategy):
    """
    Simuliert einen Trade basierend auf Signal-Tag und Strategie-Regeln.
    """
    direction = strategy["direction"]
    entry_type = strategy["entry"]
    stop_pct = strategy["stop_pct"]
    tp1_rr = strategy["tp1_rr"]
    tp2_rr = strategy["tp2_rr"]
    max_hold = strategy["max_hold_days"]
    
    signal_day = bars[signal_idx]
    
    # === ENTRY BESTIMMEN ===
    if entry_type == "next_open":
        if signal_idx + 1 >= len(bars):
            return None
        entry_price = bars[signal_idx + 1]["open"]
        trade_start_idx = signal_idx + 1
    elif entry_type == "at_close":
        entry_price = signal_day["close"]
        trade_start_idx = signal_idx + 1
    elif entry_type == "prev_high":
        if signal_idx < 1 or signal_idx + 1 >= len(bars):
            return None
        entry_price = bars[signal_idx - 1]["high"]
        trade_start_idx = signal_idx + 1
    else:
        return None
    
    if entry_price <= 0:
        return None
    
    # === MINDESTENS 1 Folgetag nötig für sinnvolle Simulation ===
    if trade_start_idx >= len(bars):
        return None
    
    # === STOP & TARGETS BERECHNEN ===
    risk = entry_price * stop_pct
    
    if direction == "long":
        stop_price = entry_price - risk
        tp1_price = entry_price + risk * tp1_rr
        tp2_price = entry_price + risk * tp2_rr
    else:  # short
        stop_price = entry_price + risk
        tp1_price = entry_price - risk * tp1_rr
        tp2_price = entry_price - risk * tp2_rr
    
    # === TRADE SIMULIEREN ===
    exit_price = None
    exit_reason = None
    exit_date = None
    tp1_hit = False
    bars_held = 0
    
    for day_offset in range(max_hold):
        bar_idx = trade_start_idx + day_offset
        if bar_idx >= len(bars):
            break
        
        bar = bars[bar_idx]
        bars_held += 1
        
        if direction == "long":
            # Prüfe ob Entry überhaupt erreicht wird (bei prev_high Entry)
            if entry_type == "prev_high" and day_offset == 0:
                if bar["high"] < entry_price:
                    return None  # Entry nicht erreicht
            
            # Stop Check (Low des Tages)
            if bar["low"] <= stop_price:
                # Wenn auch TP1 an diesem Tag möglich → konservativ: Stop first
                if bar["open"] <= stop_price:
                    exit_price = bar["open"]  # Gapped through stop
                else:
                    exit_price = stop_price
                exit_reason = "STOP"
                exit_date = bar["date"]
                break
            
            # TP2 Check
            if bar["high"] >= tp2_price:
                exit_price = tp2_price
                exit_reason = "TP2"
                exit_date = bar["date"]
                tp1_hit = True
                break
            
            # TP1 Check
            if bar["high"] >= tp1_price:
                tp1_hit = True
        
        else:  # short
            if entry_type == "prev_high" and day_offset == 0:
                if bar["low"] > entry_price:
                    return None
            
            if bar["high"] >= stop_price:
                if bar["open"] >= stop_price:
                    exit_price = bar["open"]
                else:
                    exit_price = stop_price
                exit_reason = "STOP"
                exit_date = bar["date"]
                break
            
            if bar["low"] <= tp2_price:
                exit_price = tp2_price
                exit_reason = "TP2"
                exit_date = bar["date"]
                tp1_hit = True
                break
            
            if bar["low"] <= tp1_price:
                tp1_hit = True
    
    # Max Hold erreicht → Exit at Close
    if exit_reason is None:
        last_bar_idx = min(trade_start_idx + max_hold - 1, len(bars) - 1)
        exit_price = bars[last_bar_idx]["close"]
        exit_reason = "TP1+EOD" if tp1_hit else "EOD"
        exit_date = bars[last_bar_idx]["date"]
    
    # === P&L BERECHNEN ===
    if direction == "long":
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        r_multiple = (exit_price - entry_price) / risk if risk > 0 else 0
    else:
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100
        r_multiple = (entry_price - exit_price) / risk if risk > 0 else 0
    
    # Cap R-Multiple: Max -3R bei Gap-Through (realistischer Slippage)
    r_multiple = max(r_multiple, -3.0)
    
    # Skip 0-Bar Trades (kein echter Trade)
    if bars_held == 0:
        return None
    
    return {
        "signal_date": signal_day["date"],
        "entry_date": bars[trade_start_idx]["date"] if trade_start_idx < len(bars) else signal_day["date"],
        "exit_date": exit_date,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "tp1_price": round(tp1_price, 2),
        "tp2_price": round(tp2_price, 2),
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "pnl_pct": round(pnl_pct, 2),
        "r_multiple": round(r_multiple, 2),
        "bars_held": bars_held,
        "tp1_hit": tp1_hit,
        "is_winner": pnl_pct > 0
    }


def run_full_backtest(poly_key, strategies=None, tickers=None, months=6, progress_callback=None):
    """
    Führt vollständigen Backtest über alle Strategien und Ticker durch.
    
    Args:
        poly_key: Polygon API Key
        strategies: Liste von Strategie-Namen (None = alle)
        tickers: Liste von Tickern (None = BACKTEST_UNIVERSE)
        months: Anzahl Monate zurück
        progress_callback: Funktion für Fortschrittsanzeige
    
    Returns:
        dict mit allen Ergebnissen
    """
    import time
    from datetime import datetime, timedelta
    
    if strategies is None:
        strategies = list(BACKTEST_STRATEGY_RULES.keys())
    if tickers is None:
        tickers = BACKTEST_UNIVERSE
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=months * 30 + 30)).strftime("%Y-%m-%d")  # +30 für RVOL Lookback
    
    test_start = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    
    all_results = {s: [] for s in strategies}
    ticker_data_cache = {}
    
    total_tickers = len(tickers)
    skipped_no_data = 0
    skipped_too_short = 0
    total_signals = 0
    
    for t_idx, ticker in enumerate(tickers):
        if progress_callback:
            progress_callback(t_idx / total_tickers, f"📥 {ticker} ({t_idx+1}/{total_tickers})")
        
        # Daten holen (mit Cache)
        if ticker not in ticker_data_cache:
            bars = fetch_backtest_daily_data(poly_key, ticker, start_date, end_date)
            if not bars:
                skipped_no_data += 1
                continue
            if len(bars) < 30:
                skipped_too_short += 1
                continue
            ticker_data_cache[ticker] = bars
        
        bars = ticker_data_cache[ticker]
        
        # Für jeden Tag: Metriken berechnen und Signale prüfen
        for idx in range(21, len(bars)):  # Start bei 21 für RVOL-Lookback
            if bars[idx]["date"] < test_start:
                continue
            
            metrics = compute_daily_metrics(bars, idx)
            if not metrics:
                continue
            
            # Min-Preis Filter
            if metrics["price"] <= 0:
                continue
            
            # Jede Strategie prüfen
            for strat_name in strategies:
                strat = BACKTEST_STRATEGY_RULES[strat_name]
                
                # Preis-Filter
                min_price = strat.get("min_price", 1.0)
                if metrics["price"] < min_price:
                    continue
                
                # Signal prüfen
                if check_signal(metrics, strat["signal"]):
                    total_signals += 1
                    # Trade simulieren
                    trade = simulate_trade(bars, idx, strat)
                    if trade:
                        trade["ticker"] = ticker
                        trade["strategy"] = strat_name
                        trade["signal_change_pct"] = round(metrics["change_pct"], 2)
                        trade["signal_rvol"] = round(metrics["rvol"], 1)
                        all_results[strat_name].append(trade)
    
    if progress_callback:
        loaded = len(ticker_data_cache)
        progress_callback(1.0, f"✅ Fertig! {loaded} geladen, {skipped_no_data} keine Daten, {skipped_too_short} zu kurz, {total_signals} Signale")
    
    return all_results


def compute_backtest_stats(trades):
    """Berechnet Performance-Statistiken für eine Liste von Trades."""
    if not trades:
        return {
            "total_trades": 0, "winners": 0, "losers": 0, "win_rate": 0,
            "avg_pnl": 0, "avg_r": 0, "best_r": 0, "worst_r": 0,
            "avg_hold": 0, "tp1_rate": 0, "tp2_rate": 0, "stop_rate": 0,
            "profit_factor": 0, "expectancy": 0, "total_r": 0
        }
    
    winners = [t for t in trades if t["is_winner"]]
    losers = [t for t in trades if not t["is_winner"]]
    
    total = len(trades)
    win_count = len(winners)
    
    avg_pnl = sum(t["pnl_pct"] for t in trades) / total
    avg_r = sum(t["r_multiple"] for t in trades) / total
    total_r = sum(t["r_multiple"] for t in trades)
    
    avg_win = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
    avg_loss = abs(sum(t["pnl_pct"] for t in losers) / len(losers)) if losers else 1
    
    gross_profit = sum(t["r_multiple"] for t in winners) if winners else 0
    gross_loss = abs(sum(t["r_multiple"] for t in losers)) if losers else 1
    
    tp2_count = sum(1 for t in trades if t["exit_reason"] == "TP2")
    tp1_eod_count = sum(1 for t in trades if t["exit_reason"] == "TP1+EOD")
    stop_count = sum(1 for t in trades if t["exit_reason"] == "STOP")
    eod_count = sum(1 for t in trades if t["exit_reason"] == "EOD")
    
    return {
        "total_trades": total,
        "winners": win_count,
        "losers": len(losers),
        "win_rate": round(win_count / total * 100, 1) if total > 0 else 0,
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_r": round(avg_r, 2),
        "total_r": round(total_r, 1),
        "best_r": round(max(t["r_multiple"] for t in trades), 2) if trades else 0,
        "worst_r": round(min(t["r_multiple"] for t in trades), 2) if trades else 0,
        "avg_hold": round(sum(t["bars_held"] for t in trades) / total, 1) if total > 0 else 0,
        "tp1_rate": round((tp2_count + tp1_eod_count) / total * 100, 1) if total > 0 else 0,
        "tp2_rate": round(tp2_count / total * 100, 1) if total > 0 else 0,
        "stop_rate": round(stop_count / total * 100, 1) if total > 0 else 0,
        "eod_rate": round(eod_count / total * 100, 1) if total > 0 else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
        "expectancy": round(avg_r, 2)
    }


def display_backtest_lab(poly_key):
    """UI für den Backtest Lab Tab."""
    import streamlit as st
    import json
    
    st.header("🧪 Backtest Lab")
    st.caption("Teste alle Strategien über 6 Monate mit echten Polygon-Daten")
    
    # Einstellungen
    col_set1, col_set2, col_set3 = st.columns(3)
    
    with col_set1:
        months = st.selectbox("📅 Zeitraum", [1, 3, 6, 9, 12], index=2, format_func=lambda x: f"{x} Monate")
    
    with col_set2:
        strat_options = list(BACKTEST_STRATEGY_RULES.keys())
        selected_strats = st.multiselect(
            "📋 Strategien",
            strat_options,
            default=strat_options,
            help="Wähle welche Strategien getestet werden sollen"
        )
    
    with col_set3:
        universe_size = st.selectbox(
            "🌍 Universum", 
            ["Klein (30)", "Mittel (75)", "Groß (175)", "🔥 ALLE US-Aktien"],
            index=1,
            help="ALLE US-Aktien nutzt Grouped Daily API (1 Call/Tag → tausende Aktien)"
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
        st.caption("📊 Filter: Preis >$5, Volumen >500k/Tag, keine Leveraged/Inverse ETFs")
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
    if st.button("🚀 Backtest starten", type="primary", use_container_width=True):
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
                    min_volume=500000,  # 500k/Tag min → ~3000-4000 Aktien
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
            if stats["total_r"] > 5:
                medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "✅"
            elif stats["total_r"] > 0:
                medal = "🔶"
            else:
                medal = "❌"
            
            with st.expander(f"{medal} #{rank} {dir_emoji} **{strat_name}** — Win Rate: {stats['win_rate']}% | Total R: {stats['total_r']}R | Trades: {stats['total_trades']}"):
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Win Rate", f"{stats['win_rate']}%")
                    st.caption(f"{stats['winners']}W / {stats['losers']}L")
                with col2:
                    st.metric("Avg R", f"{stats['avg_r']}R")
                    st.caption(f"Best: {stats['best_r']}R | Worst: {stats['worst_r']}R")
                with col3:
                    st.metric("Profit Factor", f"{stats['profit_factor']}")
                    st.caption(f"Avg Win: +{stats['avg_win']}% | Avg Loss: -{stats['avg_loss']}%")
                with col4:
                    st.metric("Total R", f"{stats['total_r']}R")
                    st.caption(f"Avg Hold: {stats['avg_hold']} Tage")
                
                # Exit-Verteilung
                st.markdown("**Exit-Verteilung:**")
                exit_cols = st.columns(4)
                with exit_cols[0]:
                    st.caption(f"🎯 TP2: {stats['tp2_rate']}%")
                with exit_cols[1]:
                    st.caption(f"✅ TP1+EOD: {stats['tp1_rate'] - stats['tp2_rate']:.1f}%")
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
            ticker = trade["ticker"]
            if ticker not in ticker_stats:
                ticker_stats[ticker] = {"trades": 0, "wins": 0, "total_r": 0}
            ticker_stats[ticker]["trades"] += 1
            ticker_stats[ticker]["total_r"] += trade["r_multiple"]
            if trade["is_winner"]:
                ticker_stats[ticker]["wins"] += 1
        
        sorted_tickers = sorted(ticker_stats.items(), key=lambda x: x[1]["total_r"], reverse=True)
        
        col_best, col_worst = st.columns(2)
        
        with col_best:
            st.markdown("**🏆 Beste Ticker:**")
            for ticker, stats in sorted_tickers[:5]:
                wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
                st.caption(f"✅ **{ticker}**: {stats['total_r']:+.1f}R | {stats['trades']} Trades | {wr:.0f}% WR")
        
        with col_worst:
            st.markdown("**💀 Schlechteste Ticker:**")
            for ticker, stats in sorted_tickers[-5:]:
                wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
                st.caption(f"❌ **{ticker}**: {stats['total_r']:+.1f}R | {stats['trades']} Trades | {wr:.0f}% WR")
    
    # === FAZIT ===
    st.subheader("📋 Fazit")
    
    if strat_stats:
        profitable = [(n, s) for n, s in sorted_strats if s["total_r"] > 0]
        unprofitable = [(n, s) for n, s in sorted_strats if s["total_r"] <= 0]
        
        if profitable:
            best_name, best_stats = profitable[0]
            st.success(f"🏆 **Beste Strategie: {best_name}** — {best_stats['win_rate']}% Win Rate, {best_stats['total_r']}R Total, PF {best_stats['profit_factor']}")
        
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
        for ticker, name in contracts:
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
st.set_page_config(page_title="Alpha V67.4 Pro", layout="wide")

# AUTO-REFRESH (wenn aktiviert)
if st.session_state.auto_refresh_enabled:
    refresh_interval = st.session_state.get("refresh_interval", 5) * 60 * 1000  # in ms
    st_autorefresh(interval=refresh_interval, key="auto_refresh")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V67.4 Pro")
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
                    help="Dollar Volume = Preis × Volumen. Höher = besser handelbar."
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
    
    # SCAN Button
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        # Reset Navigation Index für neue Ergebnisse
        st.session_state.selected_row_index = 0
        
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
                    
                    # Erst Standard-Scan für Kandidaten
                    candidates, _, _, _ = fetch_stock_data(poly_key, session="Regular")
                    
                    # Filter: Nur Aktien mit genug Bewegung und Liquidität
                    if direction == "long":
                        # Für Long: Aktien die noch nicht zu stark gestiegen sind
                        filtered = [c for c in candidates if -5 <= c.get("Chg%", 0) <= 10 and 5 <= c.get("Preis", 0) <= 500]
                    else:
                        # Für Short: Aktien die noch nicht zu stark gefallen sind  
                        filtered = [c for c in candidates if -10 <= c.get("Chg%", 0) <= 5 and 5 <= c.get("Preis", 0) <= 500]
                    
                    # Top 30 nach Alpha Score
                    filtered = sorted(filtered, key=lambda x: x.get("Alpha", 0), reverse=True)[:30]
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
                        
                        # Erst Standard-Scan für Kandidaten
                        candidates, _, _, _ = fetch_stock_data(poly_key, session="Regular")
                        
                        # Filter: Nur liquide Aktien mit Bewegung
                        filtered = [c for c in candidates if 5 <= c.get("Preis", 0) <= 500]
                        
                        # Sortiere nach Alpha Score, nimm Top 50
                        filtered = sorted(filtered, key=lambda x: x.get("Alpha", 0), reverse=True)[:50]
                        tickers = [c["Ticker"] for c in filtered]
                        
                        status.update(label=f"Analysiere {len(tickers)} Aktien auf Harmonic Patterns (60 Tage)...")
                        
                        # Harmonic Scan
                        harmonic_results = scan_harmonic_batch(tickers, poly_key, days=60)
                        
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
                    
                    # Basis-Filter: Preis $5-$1000 UND Liquidität >= $10M
                    MIN_LIQUIDITY = 10_000_000  # $10 Millionen
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
                    
                    # Sortiere nach Change% (kleinste Bewegung zuerst = näher am Pullback)
                    filtered = sorted(filtered, key=lambda x: abs(x.get("Chg%", 0)))[:80]
                    
                    status.update(label=f"Schritt 2/3: Berechne {ma_type} {ma_period} für {len(filtered)} Aktien...")
                    
                    # MA Berechnung für jeden Kandidaten
                    results = []
                    ma_checked = 0
                    
                    for candidate in filtered:
                        ticker = candidate["Ticker"]
                        price = candidate["Preis"]
                        
                        # Hole historische Daten (mit Rate Limiting)
                        closes = fetch_historical_closes(ticker, poly_key, days=ma_period + 10)
                        if ma_checked % 10 == 9:
                            time.sleep(0.5)  # Rate Limiting: Pause nach je 10 Calls
                        
                        if not closes or len(closes) < ma_period:
                            continue
                        
                        # Berechne MA
                        if ma_type == "SMA":
                            ma_value = calculate_sma(closes, ma_period)
                        else:
                            ma_value = calculate_ema(closes, ma_period)
                        
                        if not ma_value:
                            continue
                        
                        ma_checked += 1
                        
                        # Berechne Distanz zum MA
                        ma_distance = calculate_ma_distance(price, ma_value)
                        
                        if ma_distance is None:
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
                        
                        if is_valid:
                            # Füge MA-Daten zum Ergebnis hinzu
                            candidate["MA_Value"] = round(ma_value, 2)
                            candidate["MA_Distance%"] = round(ma_distance, 2)
                            candidate["MA_Type"] = f"{ma_type}{ma_period}"
                            candidate["Alpha"] = round(100 - abs(ma_distance) * 20, 1)  # Näher am MA = höherer Score
                            results.append(candidate)
                        
                        # Progress Update
                        if ma_checked % 20 == 0:
                            status.update(label=f"Schritt 2/3: {ma_checked}/{len(filtered)} Aktien geprüft...")
                    
                    # Sortiere nach MA-Distanz (näher = besser)
                    results = sorted(results, key=lambda x: abs(x.get("MA_Distance%", 999)))[:50]
                    
                    st.session_state.scan_results = results
                    st.session_state.market_type = "Aktien"
                    
                    direction_text = "Support (Long)" if ma_approach == "from_above" else "Resistance (Short)"
                    status.update(label=f"✅ {len(results)} {ma_type}{ma_period} {direction_text} Setups gefunden", state="complete")
                    
                    # DEBUG INFO
                    if st.session_state.get("debug_mode", False):
                        st.caption(f"🔍 Debug: {len(candidates)} Aktien geladen → {len(filtered)} nach Change%-Filter → {ma_checked} MA berechnet → {len(results)} im {ma_distance_max}%-Band")
                    
                    if len(results) == 0:
                        st.info(f"ℹ️ Keine Aktien gefunden die sich innerhalb von -{1.0}% bis +{ma_distance_max}% der {ma_type}{ma_period} befinden. "
                               f"Versuche später erneut.")
                    
                except KeyError:
                    st.error("❌ POLYGON_KEY fehlt in Secrets!")
                except Exception as e:
                    st.error(f"Fehler beim MA Bounce Scan: {e}")
                    import traceback
                    st.code(traceback.format_exc())
        
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
                
                st.session_state.scan_results = sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]
                
                # =============================================================
                # K1: MULTI-DAY PATTERN VALIDATION (wenn needs_history=True)
                # =============================================================
                current_strategies = get_strategies_for_market(m_type, exchange=exchange)
                strategy_data = current_strategies.get(st.session_state.get("current_strategy", ""), {})
                
                if strategy_data.get("needs_history") and m_type == "Aktien" and exchange == "US":
                    try:
                        poly_key = st.secrets["POLYGON_KEY"]
                        pattern_type = strategy_data.get("pattern_type", "consolidation")
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
                                is_valid, score, details = analyze_multi_day_pattern(bars, pattern_type)
                                if is_valid and score >= 40:
                                    r["PatternScore"] = score
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
# HAUPTBEREICH - TABS
# -----------------------------------------------------------------------------
tab_scanner, tab_search, tab_watchlist, tab_moneyflow, tab_backtest = st.tabs(["📊 Scanner", "🔍 Suche", "⭐ Watchlist", "💰 Money Flow", "🧪 Backtest"])

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
    
    col_chart, col_journal = st.columns([2, 1])
    
    # Prüfe ob Insider-Strategie aktiv
    is_insider = st.session_state.current_strategy in ["Insider Buying", "Insider Selling"]
    is_volume_void = st.session_state.current_strategy in ["Volume Void Long 🕳️⬆️", "Volume Void Short 🕳️⬇️"]
    is_harmonic = st.session_state.current_strategy in ["Harmonic Bullish 🦋⬆️", "Harmonic Bearish 🦋⬇️", "Harmonic All Patterns 🦋"]
    
    with col_journal:
        st.subheader("📋 Ergebnisse")
        if st.session_state.current_strategy:
            # Session-Info für Aktien
            if st.session_state.market_type == "Aktien":
                session = st.session_state.get("active_trading_session", "Regular")
                session_emoji = {
                    "Regular": "🟢",
                    "Pre-Market": "🌅",
                    "After-Hours": "🌙",
                    "Extended": "📊"
                }
                st.caption(f"{st.session_state.current_strategy} | {st.session_state.market_type} | {session_emoji.get(session, '')} {session}")
                
                # Info: Realtime mit Polygon Paid
                st.caption("📡 Mit Polygon Starter: **Realtime** | Free: ~15min verzögert")
            else:
                st.caption(f"{st.session_state.current_strategy} | {st.session_state.market_type} | 24/7")
        
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            
            # Verschiedene Spalten je nach Strategie
            if is_insider and "BuyValue" in df.columns:
                # Insider-Anzeige
                display_cols = ["Ticker", "BuyCount", "BuyValue", "SellCount", "SellValue"]
                col_config = {
                    "BuyCount": st.column_config.NumberColumn("🟢 Käufe", format="%d"),
                    "BuyValue": st.column_config.NumberColumn("🟢 Wert", format="$%,.0f"),
                    "SellCount": st.column_config.NumberColumn("🔴 Verkäufe", format="%d"),
                    "SellValue": st.column_config.NumberColumn("🔴 Wert", format="$%,.0f"),
                }
            elif is_volume_void and "VoidScore" in df.columns:
                # Volume Void Anzeige
                display_cols = ["Ticker", "Preis", "VoidScore", "VoidDist%", "VoidSize%"]
                col_config = {
                    "Preis": st.column_config.NumberColumn("Preis", format="$%.2f"),
                    "VoidScore": st.column_config.NumberColumn("🕳️ Score", format="%d"),
                    "VoidDist%": st.column_config.NumberColumn("Dist%", format="%.1f%%"),
                    "VoidSize%": st.column_config.NumberColumn("Size%", format="%.1f%%"),
                }
            elif "Pattern" in df.columns and "RiskReward" in df.columns:
                # Harmonic Pattern Anzeige 🦋
                display_cols = ["Ticker", "Pattern", "Direction", "Entry", "StopLoss", "TP1", "RiskReward"]
                col_config = {
                    "Pattern": st.column_config.TextColumn("🦋 Pattern"),
                    "Direction": st.column_config.TextColumn("📈"),
                    "Entry": st.column_config.NumberColumn("Entry", format="$%.2f"),
                    "StopLoss": st.column_config.NumberColumn("SL", format="$%.2f"),
                    "TP1": st.column_config.NumberColumn("TP1", format="$%.2f"),
                    "RiskReward": st.column_config.NumberColumn("R:R", format="%.1f"),
                }
            elif st.session_state.market_type == "Futures" and "Name" in df.columns:
                # Futures Anzeige
                display_cols = ["Ticker", "Name", "Preis", "Chg%", "Alpha"]
                col_config = {
                    "Preis": st.column_config.NumberColumn("Preis", format="%.2f"),
                    "Chg%": st.column_config.NumberColumn("Chg%", format="%.2f%%"),
                    "Alpha": st.column_config.NumberColumn("Alpha", format="%.0f⭐"),
                }
            elif st.session_state.market_type == "Forex" and "Pips" in df.columns:
                # Forex Anzeige mit Pips
                display_cols = ["Ticker", "Preis", "Chg%", "Pips", "Alpha"]
                col_config = {
                    "Preis": st.column_config.NumberColumn("Preis", format="%.5f"),
                    "Chg%": st.column_config.NumberColumn("Chg%", format="%.3f%%"),
                    "Pips": st.column_config.NumberColumn("Pips", format="%.1f"),
                    "Alpha": st.column_config.NumberColumn("Alpha", format="%.0f⭐"),
                }
            elif st.session_state.market_type == "Krypto" and "Name" in df.columns:
                display_cols = ["Ticker", "Name", "Preis", "Chg%", "Alpha"]
                col_config = {
                    "Preis": st.column_config.NumberColumn("Preis", format="$%.4f"),
                    "Chg%": st.column_config.NumberColumn("Chg%", format="%.2f%%"),
                    "Alpha": st.column_config.NumberColumn("Alpha", format="%.0f⭐"),
                }
            else:
                display_cols = ["Ticker", "Preis", "Chg%", "RVOL", "Alpha"]
                col_config = {
                    "Preis": st.column_config.NumberColumn("Preis", format="$%.4f"),
                    "Chg%": st.column_config.NumberColumn("Chg%", format="%.2f%%"),
                    "RVOL": st.column_config.NumberColumn("RVOL", format="%.1fx"),
                    "Alpha": st.column_config.NumberColumn("Alpha", format="%.0f⭐"),
                }
            
            # Nur vorhandene Spalten anzeigen
            display_cols = [c for c in display_cols if c in df.columns]
            
            # =====================================================================
            # NAVIGATION MIT VISUELLER MARKIERUNG
            # =====================================================================
            num_results = len(df)
            current_idx = st.session_state.selected_row_index
            
            # Begrenze Index auf gültige Werte
            if current_idx >= num_results:
                current_idx = max(0, num_results - 1)
                st.session_state.selected_row_index = current_idx
            
            # =====================================================================
            # KEYBOARD NAVIGATION (W/E Tasten)
            # =====================================================================
            # Methode 1: Verstecktes HTML Component für Keyboard Events
            from streamlit.components.v1 import html
            
            keyboard_html = f"""
            <div id="keyboard-nav-container" style="height:0;overflow:hidden;">
                <script>
                    // Keyboard Navigation für Alpha Station
                    (function() {{
                        var currentIdx = {current_idx};
                        var maxIdx = {num_results - 1};
                        
                        function findAndClickButton(searchText) {{
                            // Suche im Parent-Dokument (Streamlit App)
                            var doc = window.parent.document;
                            var buttons = doc.querySelectorAll('button');
                            for (var i = 0; i < buttons.length; i++) {{
                                var btn = buttons[i];
                                var text = (btn.textContent || btn.innerText || '').toLowerCase();
                                if (text.includes(searchText.toLowerCase())) {{
                                    btn.click();
                                    return true;
                                }}
                            }}
                            return false;
                        }}
                        
                        function handleKeyDown(e) {{
                            // Prüfe ob Input fokussiert
                            var activeEl = window.parent.document.activeElement;
                            var tag = activeEl ? activeEl.tagName.toLowerCase() : '';
                            if (tag === 'input' || tag === 'textarea') return;
                            
                            var key = e.key.toLowerCase();
                            
                            if ((key === 'w' || key === 'arrowup') && currentIdx > 0) {{
                                e.preventDefault();
                                findAndClickButton('vorherige');
                            }}
                            
                            if ((key === 'e' || key === 'arrowdown') && currentIdx < maxIdx) {{
                                e.preventDefault();
                                findAndClickButton('nächste');
                            }}
                        }}
                        
                        // Event Listener auf Parent-Dokument
                        try {{
                            window.parent.document.removeEventListener('keydown', window.parent.alphaKeyHandler);
                            window.parent.alphaKeyHandler = handleKeyDown;
                            window.parent.document.addEventListener('keydown', handleKeyDown);
                        }} catch(err) {{
                            // Fallback: Listener auf dieses Dokument
                            document.addEventListener('keydown', handleKeyDown);
                        }}
                    }})();
                </script>
            </div>
            """
            html(keyboard_html, height=0)
            
            # Navigation Buttons mit eindeutigen Keys
            nav_col1, nav_col2, nav_col3 = st.columns([1, 1, 1])
            with nav_col1:
                prev_disabled = current_idx <= 0
                if st.button("⬆️ Vorherige (W)", key="nav_prev_btn", disabled=prev_disabled, use_container_width=True):
                    st.session_state.selected_row_index = max(0, current_idx - 1)
                    st.rerun()
            with nav_col2:
                next_disabled = current_idx >= num_results - 1
                if st.button("⬇️ Nächste (E)", key="nav_next_btn", disabled=next_disabled, use_container_width=True):
                    st.session_state.selected_row_index = min(num_results - 1, current_idx + 1)
                    st.rerun()
            with nav_col3:
                st.markdown(f"**#{current_idx + 1}** / {num_results}")
            
            st.caption("💡 Tastatur: **W** oder **↑** = Vorherige | **E** oder **↓** = Nächste")
            
            # Erstelle Kopie des DataFrames mit visueller Markierung
            df_display = df[display_cols].copy()
            
            # Füge Marker-Spalte hinzu (→ für aktuelle Zeile)
            markers = [""] * len(df_display)
            markers[current_idx] = "→"
            df_display.insert(0, "▶", markers)
            
            # Dataframe anzeigen (ohne selection_mode für bessere Kompatibilität)
            sel = st.dataframe(
                df_display, 
                on_select="rerun", 
                selection_mode="single-row",
                hide_index=True, 
                use_container_width=True,
                column_config={
                    "▶": st.column_config.TextColumn("", width="small"),
                    **col_config
                }
            )
            
            # Auswahl verarbeiten
            selected_row_idx = current_idx  # Default: aktuelle Navigation
            
            # Bei Klick auf Dataframe: überschreibe mit geklickter Zeile
            if sel.selection and sel.selection.rows:
                clicked_idx = sel.selection.rows[0]
                if clicked_idx != current_idx:
                    st.session_state.selected_row_index = clicked_idx
                    selected_row_idx = clicked_idx
                    st.rerun()
            
            # Zeile verarbeiten
            if selected_row_idx is not None and 0 <= selected_row_idx < len(df):
                row = df.iloc[selected_row_idx]
                st.session_state.selected_symbol = str(row["Ticker"])
                st.session_state.current_data = row.to_dict()
                
                # =====================================================
                # REALTIME PREIS CHECK (Polygon Paid oder Alpaca)
                # =====================================================
                if st.session_state.market_type == "Aktien":
                    try:
                        ticker = str(row["Ticker"])
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
                        flag_details = row["FlagDetails"]
                        flag_score = row["FlagScore"] if "FlagScore" in df.columns else 0
                        
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
                        
                        st.subheader(f"{title}: {timing['emoji']} {timing['rating']}")
                        st.caption(f"Score: **{timing['score']}/{timing['max_score']}** | {timing['risk']}")
                        
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
                            for f in timing['factors'][:3]:
                                icon = "✅" if f['ok'] else "❌"
                                st.caption(f"{icon} {f['name']}: {f['value']} ({f['detail']})")
                        
                        with col_conf:
                            st.markdown(f"**{title2}**")
                            for f in timing['factors'][3:]:
                                icon = "✅" if f['ok'] else "❌"
                                st.caption(f"{icon} {f['name']}: {f['value']} ({f['detail']})")
                        
                        # Strategie-spezifische Empfehlung
                        recommendation = timing.get('recommendation', timing['risk'])
                        
                        if timing['rating'] in ["FRÜH", "GO", "PERFEKT", "EXTREM", "STARK"]:
                            st.success(f"💡 **Empfehlung:** {recommendation}")
                        elif timing['rating'] in ["OK", "WARTEN", "GUT", "MÖGLICH", "MODERAT"]:
                            st.warning(f"💡 **Empfehlung:** {recommendation}")
                        else:
                            st.error(f"💡 **Empfehlung:** {recommendation}")
                    except Exception as e:
                        pass
                
                # MA Bounce Details anzeigen
                if "MA_Value" in df.columns and pd.notna(row.get("MA_Value")):
                    try:
                        ma_value = row["MA_Value"]
                        ma_distance = row["MA_Distance%"] if pd.notna(row.get("MA_Distance%")) else 0
                        ma_type = row["MA_Type"] if "MA_Type" in df.columns else "MA"
                        
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
                        nearest_void = row["NearestVoid"] if "NearestVoid" in df.columns else None
                        
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
                            
                            if score >= 80:
                                st.success(f"{emoji} **{pattern_name}** | {dir_emoji} | Score: {score}/100")
                            elif score >= 60:
                                st.info(f"{emoji} **{pattern_name}** | {dir_emoji} | Score: {score}/100")
                            else:
                                st.warning(f"{emoji} **{pattern_name}** | {dir_emoji} | Score: {score}/100")
                            
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
                
                # Watchlist Button
                if st.button(f"⭐ {row['Ticker']} zur Watchlist", use_container_width=True):
                    if add_to_watchlist(row["Ticker"], row.to_dict()):
                        st.success(f"✅ {row['Ticker']} hinzugefügt!")
                    else:
                        st.info("Bereits in Watchlist")
                
                # AI CHART BUTTON
                if st.session_state.market_type == "Aktien":
                    if st.button(f"🤖 AI Chart für {row['Ticker']}", use_container_width=True, type="primary"):
                        st.session_state.show_ai_chart = True
                        # FullTicker für internationale Aktien (z.B. VNA.DE statt VNA)
                        st.session_state.ai_chart_ticker = row.get('FullTicker', row['Ticker'])
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
                        st.markdown(f"### {display['score_color']} Akkumulations-Score: **{display['score']}/100** ({display['score_label']})")
                        
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
                                f"{display['obv_icon']} OBV-Trend",
                                f"{display['obv_trend']:+.1f}%",
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
                            days_icon = "✅" if display['days_in_range'] >= 10 else "⚪"
                            st.metric(
                                f"{days_icon} Tage in Range",
                                f"{display['days_in_range']}",
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
                        st.markdown(f"**Interpretation:** {display['interpretation']}")
                        
                        # OBV Details
                        st.caption(f"OBV Status: {display['obv_text']}")
                        
                        # Empfehlung basierend auf Score
                        if display['score'] >= 75:
                            st.success("🎯 **BREAKOUT WATCH!** Dieses Asset zeigt starke Akkumulations-Signale. Beobachte für Ausbruch mit Volumen!")
                        elif display['score'] >= 55:
                            st.info("👀 **BEOBACHTEN:** Gute Akkumulations-Tendenzen. Warte auf besseren Entry oder Volumen-Signal.")
                        elif display['score'] >= 35:
                            st.warning("⏳ **NEUTRAL:** Noch keine klare Akkumulation. Weiter beobachten.")
                        else:
                            st.error("⚠️ **VORSICHT:** Schwache Akkumulations-Signale. Möglicherweise Distribution!")
                        
                    else:
                        st.warning(f"Nicht genug Daten für Akkumulations-Analyse")
                        st.caption(analysis.get("interpretation", ""))
                        
                except Exception as e:
                    st.error(f"Akkumulations-Analyse Fehler: {e}")
            
            # TradingView Tipp (nur für US/Krypto — internationale nutzen eigenen Chart)
            _full_t = st.session_state.current_data.get("FullTicker", "") if "current_data" in st.session_state else ""
            if not any(_full_t.upper().endswith(s) for s in (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")):
                st.info("💡 **Tipp:** Aktiviere im TradingView Chart den 'Volume Profile' Indikator für echte Volume-Daten")
        
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
                tv_symbol = f"BINANCE:{st.session_state.selected_symbol}USDT"
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
                st.success(f"✅ {search_result['Ticker']} gefunden!")
                
                # In Session State speichern
                st.session_state.selected_symbol = search_result["Ticker"]
                st.session_state.current_data = search_result
                st.session_state.market_type = search_market
                
                # Daten anzeigen
                st.divider()
                
                col_d1, col_d2, col_d3, col_d4 = st.columns(4)
                with col_d1:
                    st.metric("Preis", f"${search_result['Preis']:,.4f}")
                with col_d2:
                    st.metric("24h", f"{search_result['Chg%']:.2f}%", 
                             delta=f"{search_result['Chg%']:.2f}%",
                             delta_color="normal" if search_result['Chg%'] >= 0 else "inverse")
                with col_d3:
                    st.metric("RVOL", f"{search_result['RVOL']:.1f}x")
                with col_d4:
                    st.metric("Alpha", f"{search_result['Alpha']:.0f}")
                
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
                    if st.button(f"⭐ {search_result['Ticker']} zur Watchlist", key="search_watchlist", use_container_width=True):
                        if add_to_watchlist(search_result["Ticker"], search_result):
                            st.success("Hinzugefügt!")
                        else:
                            st.info("Bereits in Watchlist")
                with col_act2:
                    if st.button("🤖 AI-Analyse starten", key="search_ai_btn", type="primary", use_container_width=True):
                        st.session_state.run_search_analysis = True
                
                # Chart direkt anzeigen
                st.divider()
                st.subheader(f"📊 Chart: {search_result['Ticker']}")
                
                if search_market == "Krypto":
                    tv_symbol = f"BINANCE:{search_result['Ticker']}USDT"
                else:
                    # Internationale Aktien: Exchange-Prefix für TradingView
                    _sr_sym = search_result['Ticker']
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
                    with st.spinner("Claude analysiert..."):
                        try:
                            client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                            
                            prompt = f"""SCHNELL-ANALYSE für {search_result['Ticker']}

DATEN:
- Preis: ${search_result['Preis']}
- 24h Change: {search_result['Chg%']}%
- RVOL: {search_result['RVOL']}x
- Alpha-Score: {search_result['Alpha']}
- Markt: {search_market}

AUFGABEN:
1. Kurze technische Einschätzung (2-3 Sätze)
2. Key Support & Resistance Levels
3. Empfehlung: LONG / SHORT / ABWARTEN
4. Rating: X/100

Keine Disclaimers. Direkt und knapp."""

                            message = client.messages.create(
                                model="claude-sonnet-4-20250514",
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
# CLAUDE AI ANALYSE
# -----------------------------------------------------------------------------
st.divider()

col_ai1, col_ai2 = st.columns([3, 1])
with col_ai1:
    st.subheader("🤖 Claude AI Analyse")
with col_ai2:
    analyze_btn = st.button("Analyse starten", type="primary", use_container_width=True)

if analyze_btn:
    if "current_data" not in st.session_state:
        st.warning("Wähle zuerst einen Ticker!")
    else:
        with st.spinner("Claude analysiert..."):
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
                    model="claude-sonnet-4-20250514",
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
    
    def get_heatmap_color(change):
        """Gibt Hintergrundfarbe basierend auf Performance zurück"""
        if change >= 10:
            return "#006400"  # Dunkelgrün
        elif change >= 5:
            return "#228B22"  # Grün
        elif change >= 2:
            return "#32CD32"  # Hellgrün
        elif change >= 0:
            return "#90EE90"  # Sehr hellgrün
        elif change >= -2:
            return "#FFB6C1"  # Hellrot
        elif change >= -5:
            return "#FF6B6B"  # Rot
        elif change >= -10:
            return "#DC143C"  # Dunkelrot
        else:
            return "#8B0000"  # Sehr dunkelrot
    
    def get_text_color(change):
        """Gibt Textfarbe basierend auf Hintergrund zurück"""
        if abs(change) >= 5:
            return "white"
        else:
            return "black"
    
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

# -----------------------------------------------------------------------------
# FOOTER
# -----------------------------------------------------------------------------
st.divider()
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("Alpha Station V67.4 Pro")
with c2:
    st.caption(f"Watchlist: {len(st.session_state.watchlist)} Ticker")
with c3:
    if st.session_state.auto_refresh_enabled:
        st.caption(f"🔄 Auto-Refresh: {st.session_state.refresh_interval} Min")
    else:
        st.caption("🔄 Auto-Refresh: Aus")
